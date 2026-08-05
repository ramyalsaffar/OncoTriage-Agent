"""Stage 1's deterministic MeSH walk: patient conditions -> query terms.

Item 20c, pass 2c: "13- LangGraph Agent.py" lines 766-1069, verbatim.

No LLM anywhere in here. ``resolve_patient_mesh`` is the pipeline's single entry
point for resolving a patient's MeSH C04 identity, and ``expand_query_from_mesh``
turns that identity into the descriptors Stage 2 searches with. Both take the
cancer registry and the MeSH filter as ARGUMENTS rather than reaching for them,
which is why this module needs no dependency seam of its own -- its callers
(``retrieval``) pass what ``deps`` handed them.

``format_mesh_resolution`` is the one-line log form, and the two
MESH_RESOLUTION_* constants are the outcomes that never reach the filter itself,
so they are named here rather than on MeSHCancerFilter.

Imports ``oncotriage.registries.mesh`` for the filter type and
``specific_cancer_trees``; importing it reads no JSON, because
``load_mesh_filter()`` is a function and this module does not call it.
"""

from oncotriage.registries.mesh import MeSHCancerFilter, specific_cancer_trees


#------------------------------------------------------------------------------



# Resolution outcomes that never reach the MeSH filter itself, so they are
# named here rather than on MeSHCancerFilter. Everything else in the
# mesh_resolution column comes from resolve_patient_trees().
MESH_RESOLUTION_NO_FILTER = "no_mesh_filter"       # MeSH data files not loaded
MESH_RESOLUTION_NO_CONDITIONS = "no_valid_condition"  # nothing left to resolve


def _empty_mesh_resolution(reason: str) -> dict:
    """Same shape resolve_patient_trees() returns, for the pre-resolution exits."""
    return {
        "trees":               set(),
        "resolution":          reason,
        "layers":              [],
        "pan_only_layers":     [],
        "conditions_total":    0,
        "conditions_resolved": 0,
        "conditions_pan_only": 0,
        "conditions_unmapped": 0,
    }


def resolve_patient_mesh(conditions: list, cancer_registry, mesh_filter) -> dict:
    """
    The pipeline's single entry point for resolving a patient's MeSH identity.

    Stage 1 (query expansion) and Stage 3 (relevance boost, which feeds
    Stage 4's hard drop via state["patient_trees"]) both call this, so the
    patient's cancer identity — and the layer that produced it — is the same
    number in the expanded query, the boost, the filter and the log.

    Conditions marked refuted or entered-in-error are excluded. There is no
    fallback to the unfiltered list: a diagnosis the record retracted is not
    evidence of the patient's cancer, and 07- FHIR Parser.py already drops
    both statuses before the pipeline sees the bundle, so the only way this
    filter empties a non-empty list is a hand-built condition dict.

    Returns the resolve_patient_trees() dict (see 09- MeSH Cancer Site
    Relevance Filter.py), with "resolution" set to MESH_RESOLUTION_NO_FILTER
    or MESH_RESOLUTION_NO_CONDITIONS on the pre-resolution exits.
    """
    if mesh_filter is None:
        return _empty_mesh_resolution(MESH_RESOLUTION_NO_FILTER)

    valid_conditions = [
        c for c in conditions
        if (c.get("verification_status") or "unknown")
        not in cancer_registry.exclude_verification
    ]

    if not valid_conditions:
        return _empty_mesh_resolution(MESH_RESOLUTION_NO_CONDITIONS)

    return mesh_filter.resolve_patient_trees(valid_conditions, cancer_registry)


def format_mesh_resolution(diag: dict) -> str:
    """One-line summary of a resolve_patient_mesh() result, for stage logs."""
    parts = [
        f"{diag['conditions_resolved']}/{diag['conditions_total']} cancer conditions",
        f"{len(diag['trees'])} trees",
    ]
    if diag["pan_only_layers"]:
        parts.append(f"escalated past {'+'.join(diag['pan_only_layers'])}")
    if diag["conditions_pan_only"]:
        parts.append(f"{diag['conditions_pan_only']} pan-cancer-only")
    if diag["conditions_unmapped"]:
        parts.append(f"{diag['conditions_unmapped']} unmapped")
    return f"[{diag['resolution']}] " + ", ".join(parts)


def expand_query_from_mesh(conditions: list, cancer_registry, mesh_filter) -> dict:
    """
    Deterministic query expansion using MeSH C04 hierarchy.

    Replaces GPT-4o-mini query expansion with a pure lookup:
      1. Resolve patient's cancer conditions → MeSH tree numbers
      2. Walk the C04 tree: collect self + parent + sibling + child descriptors
      3. Return ordered MeSH descriptor names for downstream query building

    Tree-walking strategy (ordered by retrieval value):
      - SELF:     The patient's own MeSH descriptor(s)        — exact match
      - CHILDREN: One level deeper in the tree                — subtypes (higher specificity)
      - SIBLINGS: Same parent, different leaf                 — related cancers (lateral recall)
      - PARENT:   One level up                                — broader category (fallback recall)

    Sibling cap: max 10 siblings per tree number to prevent explosion at
    broad nodes like "Neoplasms by Site" (C04.588) which has ~15 children.

    Designed for:
      - Synthea FHIR bundles (SNOMED codes → Layer 1 UMLS crosswalk)
      - Real-world EHR data  (ICD-10 codes → Layer 2 fuzzy matching on display text)

    Args:
        conditions:      Patient's full condition list from FHIR parser.
                         May contain non-cancer conditions; those are filtered
                         internally using cancer_registry.
        cancer_registry: CancerCodeRegistry instance (_CANCER_REGISTRY).
                         Used for: is_primary_cancer(), sort_key(),
                         exclude_verification.
        mesh_filter:     MeSHCancerFilter instance (_MESH_FILTER), or None.
                         Provides: patient_mesh_trees(), tree_to_name,
                         name_to_trees.

    Returns:
        dict with keys:
          "mesh_terms"     : list[str]  — MeSH descriptor names, ordered by tree
                             proximity (self → children → siblings → parent).
                             Alphabetical within each group for determinism.
                             Empty list if resolution fails.
          "primary_mesh"   : str|None   — single best MeSH descriptor (for R1 rerank query)
          "parent_mesh"    : str|None   — parent MeSH descriptor (for R3 rerank query)
          "patient_trees"  : list[str]  — resolved C04 tree numbers, sorted for determinism.
                             Pan-cancer nodes are never among them.
          "resolution"     : str        — the layer(s) that resolved the patient
                             ("snomed", "icd10+fuzzy_synonym", ...), or one of
                             "no_mesh_filter" | "no_valid_condition" |
                             "no_cancer_condition" | "unmapped" | "pan_cancer_only".
                             Recorded in inferences.mesh_resolution.

    Edge cases handled:
      - mesh_filter is None (MeSH data files not loaded)     → no_mesh_filter
      - No cancer conditions in patient record                → no_cancer_condition
      - SNOMED code not in crosswalk (real EHR with ICD-10)  → falls to icd10, then fuzzy
      - Every layer resolves only to C04 / a depth-2 node    → pan_cancer_only,
        no terms, no query expansion (see the Stage 1 guard below)
      - Tree number resolves but not in tree_to_name          → skipped, uses what's available
      - Patient maps to multiple tree numbers                 → all trees walked
      - Self descriptor appears at multiple tree levels       → deduplicated
      - Broad parent node with 15+ siblings                  → capped at 10
      - Root-level tree (e.g., "C04" with no parent)         → cannot occur: a
        pan-cancer node is never accepted as the patient's identity
      - conditions list contains refuted/entered-in-error     → filtered out,
        with no fallback to the unfiltered list
    """
    MAX_SIBLINGS = 10

    result = {
        "mesh_terms": [],
        "primary_mesh": None,
        "parent_mesh": None,
        "patient_trees": [],
        # Overwritten by resolve_patient_mesh() on every path below; this is
        # only the value a caller would see if the filter were never consulted.
        "resolution": MESH_RESOLUTION_NO_FILTER,
    }

    # ── Resolve patient → MeSH tree numbers ───────────────────────────────
    # Delegates to resolve_patient_mesh() — the same call Stage 3 makes for
    # the relevance boost and for the trees Stage 4 filters on. This
    # guarantees the patient's cancer identity is resolved identically in
    # every stage, and that the layer that produced it is the one logged.
    #
    # It handles: mesh_filter=None, the refuted/entered-in-error filter,
    # the primary-cancer filter, the four resolution layers, and the
    # pan-cancer depth test applied to each of them.
    mesh_resolution = resolve_patient_mesh(conditions, cancer_registry, mesh_filter)
    result["resolution"] = mesh_resolution["resolution"]

    # ── Stage 1 guard: pan-cancer node is not an identity ─────────────────
    # A patient whose trees are only C04 / a depth-2 node builds
    # child_prefixes = {"C04."}, which matches every descriptor in the tree:
    # the expanded query would name every cancer type in MeSH and feed two of
    # the four fusion channels. resolve_patient_trees() already drops those,
    # so this is the second gate — it exists so a change there cannot silently
    # reopen the path, and it says so in the log when it fires.
    resolved_trees = mesh_resolution["trees"]
    patient_trees = specific_cancer_trees(resolved_trees)

    if patient_trees != resolved_trees:
        print(f"  Stage 1 MeSH guard: dropped "
              f"{len(resolved_trees) - len(patient_trees)} pan-cancer tree(s) "
              f"from the patient resolution")
        if not patient_trees:
            result["resolution"] = MeSHCancerFilter.RESOLUTION_PAN_ONLY

    if not patient_trees:
        return result

    # Sort for deterministic output (sets have arbitrary iteration order)
    patient_trees_sorted = sorted(patient_trees)

    result["patient_trees"] = patient_trees_sorted

    # ── Walk the C04 tree ─────────────────────────────────────────────────
    # Collect descriptor names at four proximity levels.

    self_names = set()       # exact descriptors for patient's tree numbers
    child_names = set()      # one level deeper (subtypes)
    sibling_names = set()    # same parent, different leaf (related cancers)
    parent_names = set()     # one level up (broader category)

    tree_to_name = mesh_filter.tree_to_name   # {tree_number: descriptor_name}

    # --- Pass 1: SELF + PARENT (direct lookups, O(1) per tree) ---
    for tree_num in patient_trees_sorted:
        # SELF
        if tree_num in tree_to_name:
            self_names.add(tree_to_name[tree_num])

        # PARENT: strip last dot-segment
        parts = tree_num.split(".")
        if len(parts) >= 2:
            parent_tree = ".".join(parts[:-1])
            if parent_tree in tree_to_name:
                parent_names.add(tree_to_name[parent_tree])

    # --- Pass 2: CHILDREN + SIBLINGS (single scan of tree_to_name) ---
    # Build prefix sets for efficient matching.
    # O(T) where T = ~2000 tree entries — single pass over the dictionary.
    child_prefixes = {tree_num + "." for tree_num in patient_trees_sorted}

    parent_prefixes = {}   # {parent_prefix_str: set_of_patient_trees_under_this_parent}
    for tree_num in patient_trees_sorted:
        parts = tree_num.split(".")
        if len(parts) >= 2:
            parent_prefix = ".".join(parts[:-1]) + "."
            if parent_prefix not in parent_prefixes:
                parent_prefixes[parent_prefix] = set()
            parent_prefixes[parent_prefix].add(tree_num)

    for candidate_tree, candidate_name in tree_to_name.items():
        # CHILDREN: candidate starts with any patient tree + "."
        for cp in child_prefixes:
            if candidate_tree.startswith(cp):
                # Only direct children (one level deeper, no grandchildren)
                remaining = candidate_tree[len(cp):]
                if "." not in remaining:
                    child_names.add(candidate_name)
                break   # matched one prefix, no need to check others

        # SIBLINGS: candidate shares a parent prefix with a patient tree,
        # but is not the patient tree itself
        for pp, pp_trees in parent_prefixes.items():
            if candidate_tree.startswith(pp):
                # Must not be one of the patient's own trees
                if candidate_tree not in patient_trees:
                    # Same depth as patient tree (sibling, not nephew/grandchild)
                    remaining = candidate_tree[len(pp):]
                    if "." not in remaining:
                        sibling_names.add(candidate_name)
                break   # matched one parent prefix, done

    # ── Deduplicate across levels ─────────────────────────────────────────
    # A descriptor can appear at multiple tree positions. Assign it to the
    # closest level only (self > child > sibling > parent).
    child_names -= self_names
    sibling_names -= self_names | child_names
    parent_names -= self_names | child_names | sibling_names

    # ── Cap siblings to prevent explosion ─────────────────────────────────
    # Broad nodes like "Digestive System Neoplasms" (C04.588.274) can have
    # 15+ sibling cancer sites. Cap to MAX_SIBLINGS most relevant.
    # Sort alphabetically for determinism, take first N.
    sibling_list = sorted(sibling_names)
    if len(sibling_list) > MAX_SIBLINGS:
        sibling_list = sibling_list[:MAX_SIBLINGS]

    # ── Build ordered term list ───────────────────────────────────────────
    # Priority: self → children → siblings → parent
    # Within each group: sorted alphabetically for determinism
    mesh_terms = (
        sorted(self_names)
        + sorted(child_names)
        + sibling_list
        + sorted(parent_names)
    )

    result["mesh_terms"] = mesh_terms
    
    if self_names:
        # Build inverse map: name -> max depth among patient's own tree numbers
        # O(P) where P = len(patient_trees_sorted), not O(T) scan of tree_to_name
        _name_to_max_depth = {}
        for t in patient_trees_sorted:
            n = tree_to_name.get(t)
            if n and n in self_names:
                depth = t.count(".") + 1
                if depth > _name_to_max_depth.get(n, 0):
                    _name_to_max_depth[n] = depth
        result["primary_mesh"] = max(
            sorted(self_names),
            key=lambda name: _name_to_max_depth.get(name, 0),
        )
    else:
        result["primary_mesh"] = None
    
    # Pick the parent with the most patient trees beneath it (most relevant)
    if parent_names:
        parent_counts = {}
        for tree_num in patient_trees_sorted:
            parts = tree_num.split(".")
            if len(parts) >= 2:
                parent_tree = ".".join(parts[:-1])
                if parent_tree in tree_to_name:
                    name = tree_to_name[parent_tree]
                    parent_counts[name] = parent_counts.get(name, 0) + 1
        result["parent_mesh"] = max(parent_counts, key=lambda k: (parent_counts[k], k))

        
    else:
        result["parent_mesh"] = None

    return result


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""
