"""MeSH cancer site relevance filter — the runtime half of File 09.

ITEM 20c, PASS 2a: this is "09- MeSH Cancer Site Relevance Filter.py" from
PAN_CANCER_TREE_MAX_DEPTH onward, moved. Logic byte-for-byte unchanged, sliced
statement by statement so comments travelled with what they document.

WHY THE FILE WAS SPLIT IN TWO. File 09 held two unrelated halves. The five
crosswalk BUILDERS parse desc2026.xml and the 1.5 GB UMLS MRCONSO release and
write JSON; they run once, by hand, from File 09's own __main__ block, and are
called from nowhere else in the repository. The FILTER half reads those JSON
files at load_mesh_filter() time and is what File 13 imports on every run.

Keeping them in one module would mean every process that wants the filter also
carries the MRCONSO-reading code — a file that opens a 1.5 GB RRF line by line
sitting one typo away from the query path. They are now
oncotriage.registries.mesh_crosswalk_build and this module, and this module does
not import that one.

WHAT THIS MODULE NEEDS FROM THE PROJECT: data_MeSH_path, and nothing else. It
reads no config constant. Importing it reads NO JSON — load_mesh_filter() is a
function, and every file read happens inside it.

AND IT NO LONGER RESOLVES THAT PATH AT IMPORT (pass 20c-2c). Pass 2a wrote
``from oncotriage.paths import data_MeSH_path`` at module scope. Pass 2b then
made every path in ``oncotriage.paths`` lazy — but a ``from X import name`` is
an attribute READ, so it fires the resolver, and this one line kept the whole
sibling directory tree being globbed the moment anything imported this module.
That is not academic: ``oncotriage.agent.deps`` imports this module for
``load_mesh_filter``, so importing the AGENT resolved the tree and raised on any
machine without it — the exact defect pass 2b existed to remove, surviving one
module over. Pass 2b's check only covered ``oncotriage.config``, which is how it
went unnoticed.

The module object is imported instead and the attribute is read inside
``load_mesh_filter()``, where the JSON files are read anyway.
"""

import json
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional, Set

from oncotriage import paths, settings
from oncotriage.observability import console


logger = logging.getLogger(__name__)


# ===========================================================================
# DEGRADATION RECORD (item 11a)
# ===========================================================================
#
# Module-level, following PARTIAL_DATE_DEGRADATIONS in oncotriage/utils.py, and
# deliberately NOT a new key in anything the pipeline returns: the twelve
# characterization fixtures diff what the pipeline produced field by field, and
# a new field there means recapturing twelve fixtures -- twelve live GPT-4o
# runs -- to record something no stage reads.
#
# The per-inference record for the MeSH case already exists and is unchanged:
# Stage 4 writes MESH_FILTER_SKIP_NO_FILTER into `mesh_filter_skip_reason`,
# which item 11b put in the `inferences` table. This counter is the PROCESS-level
# record -- what a batch run can print once at the end -- and the layer names
# below are the same strings the DegradedDependencyError carries as `.layer`, so
# a run that raised and a run that degraded name the same thing.
MESH_FILTER_DEGRADATIONS = Counter()

# The layer names. The first is the one item 11a made raise; the other three are
# the optional crosswalks, which were already LOGGED (each prints a NOTE line)
# and are now also RECORDED. They deliberately do NOT raise: unlike the two core
# files, the filter is fully functional without them -- it falls back to fuzzy
# descriptor matching, which is the documented design -- so their absence is a
# capability reduction that announces itself, not a layer vanishing in silence.
MESH_LAYER_CORE = "mesh_c04_core"
MESH_LAYER_SNOMED_CROSSWALK = "mesh_snomed_crosswalk"
MESH_LAYER_ICD10_CROSSWALK = "mesh_icd10_crosswalk"
MESH_LAYER_UMLS_SYNONYMS = "mesh_umls_synonym_crosswalk"
# The non-oncology complement, read by the SCRAPER's admission screen rather
# than by Stage 4. Optional and degrades in the safe direction: without it
# classify_trial_oncology() can never return TRIAL_NON_ONCOLOGY, so the screen
# admits everything it cannot positively rule out. See that method.
MESH_LAYER_NON_ONCOLOGY = "mesh_non_oncology_terms"


# ===========================================================================
# TRIAL ADMISSION VERDICT (the scraper's oncology screen)
# ===========================================================================
#
# A CLOSED three-member vocabulary, on the same footing as
# oncotriage/agent/readiness.py's four index states: the caller branches on it
# exhaustively and an unknown value is a bug rather than a default.
#
# The distinction that matters is the one a keyword substring test cannot make.
# "not resolvable to a cancer tree" is TWO facts:
#
#   TRIAL_NON_ONCOLOGY  -- every registered condition is a known MeSH term and
#                          none of them is under C04. A positive determination.
#                          This is the ONLY verdict that may drop a trial.
#   TRIAL_UNRESOLVED    -- at least one condition resolved to nothing at all.
#                          The screen has no opinion, so the trial is KEPT and
#                          the fact is counted, because the size of this bucket
#                          is the size of the uncertainty being absorbed.
#
# Collapsing them is what the old frozenset keyword screen did, and it is why
# Glioblastoma, Mesothelioma, Neuroblastoma, Retinoblastoma and Hepatoblastoma
# were dropped: no substring of any of them appears in that list.
TRIAL_ONCOLOGY = "oncology"
TRIAL_NON_ONCOLOGY = "non_oncology"
TRIAL_UNRESOLVED = "unresolved"

TRIAL_ONCOLOGY_VERDICTS = (TRIAL_ONCOLOGY, TRIAL_NON_ONCOLOGY, TRIAL_UNRESOLVED)

    

# ===========================================================================
# PAN-CANCER DEPTH TEST (shared by the filter and by the pipeline stages)
# ===========================================================================


# Depth of a C04 tree number = number of dot-separated segments.
#   C04             -> 1  (Neoplasms, the root of the whole branch)
#   C04.588         -> 2  (Neoplasms by Site — every solid tumour lives under it)
#   C04.588.274     -> 3  (Breast Neoplasms — an actual site)
# A tree number at depth <= 2 names no cancer site. On the trial side that
# means "basket trial, any cancer" (see _is_pan_cancer). On the PATIENT side it
# means the opposite: the patient's site is unknown, because C04 is a prefix of
# every descriptor in the tree and therefore matches everything.
#
# This is a structural fact about the MeSH C04 hierarchy, not a tunable.
PAN_CANCER_TREE_MAX_DEPTH = 2


def specific_cancer_trees(trees) -> Set[str]:
    """Keep only tree numbers that name an actual cancer site or type.

    Drops C04 and C04.* depth-2 nodes. Used on the patient side wherever a
    tree number is about to be treated as the patient's cancer identity:
    a pan-cancer node there is an unresolved patient, not a pan-cancer
    patient, and letting it through makes every ancestry test succeed.
    """
    return {t for t in trees if len(t.split(".")) > PAN_CANCER_TREE_MAX_DEPTH}


# ===========================================================================
# FILTER CLASS: Loaded at runtime, used by node_rule_based_filter
# ===========================================================================


class MeSHCancerFilter:
    """
    Production cancer site relevance filter using MeSH neoplasm hierarchy.

    Loaded once at startup from pre-built JSON files.
    Called per-trial in node_rule_based_filter to check whether a trial's
    target cancer type is related to the patient's cancer diagnosis.

    Patient mapping (resolve_patient_trees, layers tried in order):
      snomed  : SNOMED code → MeSH via UMLS crosswalk (gold standard)
      icd10   : ICD-10-CM code → MeSH via UMLS crosswalk (real EHR path)
      fuzzy_* : Display string → MeSH via exact / synonym / substring / stem
      Every layer must clear the pan-cancer depth test; a layer resolving
      only to C04 or a depth-2 node is walked past, and a patient no layer
      resolves is reported unresolved (⇒ conservative KEEP, as before).

    Trial mapping:
      Direct: trial["conditions"] are MeSH terms from ClinicalTrials.gov

    Filter logic:
      Related (keep)    : shared ancestry in C04 tree
      Pan-cancer (keep) : trial targets broad neoplasm category
      Unmappable (keep) : conservative — don't filter what can't be classified
      Unrelated (drop)  : no shared ancestry, both sides have clear site

    Usage:
        mesh_filter = load_mesh_filter()  # called once at startup
        keep = mesh_filter.is_cancer_relevant(patient_conditions, trial)
    """

    # Pan-cancer / basket trial indicators.
    # Trials whose ONLY C04 tree numbers are at depth ≤ 2 (e.g., C04, C04.588)
    # are considered cancer-agnostic and always pass the filter.
    PAN_CANCER_MAX_DEPTH = PAN_CANCER_TREE_MAX_DEPTH

    # Resolution outcomes reported by resolve_patient_trees(). Any other value
    # is a "+"-joined list of the layers that produced the patient's trees
    # (e.g. "snomed", "icd10+fuzzy_synonym").
    RESOLUTION_NO_CANCER = "no_cancer_condition"
    RESOLUTION_UNMAPPED  = "unmapped"
    RESOLUTION_PAN_ONLY  = "pan_cancer_only"

    # Words that appear in most medical display strings and carry no
    # site signal. Removed before fuzzy matching.
    _DISPLAY_STOPWORDS = frozenset({
        "of", "the", "a", "an", "in", "and", "or", "with",
        "to", "for", "by", "on", "at", "as", "is", "not",
    })

    def __init__(self, name_to_trees: dict, tree_to_name: dict,
                 snomed_to_trees: dict, icd10_to_trees: dict = None,
                 synonym_to_trees: dict = None, non_oncology_terms: dict = None):
        """
        Args:
            name_to_trees:      {mesh_name_lower: [tree_numbers]}
            tree_to_name:       {tree_number: mesh_name}
            snomed_to_trees:    {snomed_code: [tree_numbers]}
            icd10_to_trees:     {icd10_code: [tree_numbers]} (optional)
            synonym_to_trees:   {synonym_lower: [tree_numbers]} (optional, UMLS crosswalk)
            non_oncology_terms: {term_lower: [top_categories]} (optional) — MeSH
                terms positively OUTSIDE C04. Read only by
                classify_trial_oncology(). Absent means that method can never
                return TRIAL_NON_ONCOLOGY, so the scraper's screen admits
                everything; that is the safe direction and it is counted.
        """
        self.name_to_trees    = name_to_trees    # lowercase keys
        self.tree_to_name     = tree_to_name
        self.snomed_to_trees  = snomed_to_trees
        self.icd10_to_trees   = icd10_to_trees or {}
        self.synonym_to_trees = synonym_to_trees or {}
        self.non_oncology_terms = non_oncology_terms or {}

        # Pre-compute: set of all lowercase MeSH names for fuzzy matching
        self._all_names = set(name_to_trees.keys())
        
        # Pre-compute: word-to-names index for fast fuzzy lookup (stemmed)
        self._word_index = defaultdict(set)
        for name in self._all_names:
            for word in name.split():
                if len(word) >= 3:  # skip short words like "of", "in"
                    self._word_index[self._stem(word)].add(name)
        
        icd10_count = len(self.icd10_to_trees)
        icd10_status = f"{icd10_count:,} ICD-10 crosswalk entries" if icd10_count else "ICD-10 crosswalk not loaded"
        synonym_count = len(self.synonym_to_trees)
        synonym_status = f"{synonym_count:,} UMLS synonym entries" if synonym_count else "UMLS synonym crosswalk not loaded"
        non_onc_count = len(self.non_oncology_terms)
        non_onc_status = (f"{non_onc_count:,} non-oncology terms"
                          if non_onc_count else "non-oncology lookup not loaded")
        console.out(f"MeSHCancerFilter loaded: {len(self.name_to_trees):,} C04 descriptors, "
              f"{len(self.snomed_to_trees):,} SNOMED crosswalk entries, "
              f"{icd10_status}, {synonym_status}, {non_onc_status}")

    # -----------------------------------------------------------------
    # Patient side: condition → MeSH tree numbers
    # -----------------------------------------------------------------

    def patient_mesh_trees(self, conditions: list,
                           cancer_registry) -> Set[str]:
        """
        Extract MeSH C04 tree numbers for a patient's cancer diagnoses.

        Thin wrapper over resolve_patient_trees() for callers that only need
        the trees. Use resolve_patient_trees() when the resolution layer
        matters (logging, the mesh_resolution column, diagnostics).

        Returns:
            Set of C04 tree number strings at depth > PAN_CANCER_MAX_DEPTH,
            or empty set if the patient is unresolved.
        """
        return self.resolve_patient_trees(conditions, cancer_registry)["trees"]

    def resolve_patient_trees(self, conditions: list,
                              cancer_registry) -> dict:
        """
        Resolve a patient's cancer diagnoses to specific MeSH C04 tree numbers.

        Multi-coding aware: for each cancer condition, tries code-based
        crosswalk lookups before falling back to fuzzy string matching.

        Resolution layers, per condition, in order:
          snomed          -- SNOMED code from codings -> snomed_to_trees
          icd10           -- ICD-10-CM code from codings -> icd10_to_trees
          fuzzy_exact     -- display IS a MeSH descriptor name
          fuzzy_synonym   -- display in the UMLS synonym crosswalk
          fuzzy_substring -- display contains / is contained by a descriptor
          fuzzy_stem      -- stemmed word overlap against the descriptor index

        The pan-cancer depth test is applied to EVERY layer, not just the
        first hit. A layer that resolves only to C04 or a depth-2 node
        (mCODE's SNOMED root 363346000 -> ["C04"] is the common case, and 35
        SNOMED / 6 ICD-10 / 302 UMLS-synonym keys behave the same way) has not
        identified the patient's cancer: C04 is a prefix of every descriptor
        in the tree, so accepting it would name every cancer type in the
        Stage 1 expanded query and hand every trial the Stage 3 direct-match
        boost. Such a hit is recorded and the remaining layers are tried. If
        no layer produces a tree below the pan-cancer ceiling, the patient is
        reported unresolved — which downstream means "keep everything",
        the same conservative stance an unmappable patient already gets.

        Args:
            conditions:      Patient's condition list from FHIR
            cancer_registry: CancerCodeRegistry instance (_CANCER_REGISTRY)
                             for identifying primary cancer conditions

        Returns:
            dict:
              "trees"               : set[str]  — specific C04 trees (may be empty)
              "resolution"          : str       — "+"-joined layer names that
                                      produced the trees, or one of
                                      RESOLUTION_NO_CANCER / RESOLUTION_UNMAPPED /
                                      RESOLUTION_PAN_ONLY
              "layers"              : list[str] — layers that produced trees
              "pan_only_layers"     : list[str] — layers that produced only
                                      pan-cancer nodes and were walked past,
                                      whether or not a later layer answered
              "conditions_total"    : int
              "conditions_resolved" : int
              "conditions_pan_only" : int
              "conditions_unmapped" : int
        """
        diagnostics = {
            "trees":               set(),
            "resolution":          self.RESOLUTION_NO_CANCER,
            "layers":              [],
            "pan_only_layers":     [],
            "conditions_total":    0,
            "conditions_resolved": 0,
            "conditions_pan_only": 0,
            "conditions_unmapped": 0,
        }

        # Identify cancer conditions using existing registry
        cancer_conditions = [
            c for c in conditions
            if cancer_registry.is_primary_cancer(c)
        ]

        diagnostics["conditions_total"] = len(cancer_conditions)

        if not cancer_conditions:
            return diagnostics  # empty — will trigger conservative pass

        trees            = set()
        layers           = set()
        pan_only_layers  = set()

        for condition in cancer_conditions:
            resolved_layer       = None
            condition_pan_layers = set()

            for layer_name, layer_trees in self._resolution_layers(condition):
                if not layer_trees:
                    continue

                specific = specific_cancer_trees(layer_trees)
                if specific:
                    trees.update(specific)
                    resolved_layer = layer_name
                    break

                # Pan-cancer-only hit: this layer named no site. Record it and
                # keep walking instead of accepting C04 as the patient's identity.
                condition_pan_layers.add(layer_name)

            # Every layer walked past is recorded, whether or not a later one
            # resolved the condition: the escalation is the thing worth seeing
            # in the log, and a condition that escalated to an answer is not a
            # pan-cancer condition.
            pan_only_layers.update(condition_pan_layers)

            if resolved_layer is not None:
                layers.add(resolved_layer)
                diagnostics["conditions_resolved"] += 1
            elif condition_pan_layers:
                diagnostics["conditions_pan_only"] += 1
            else:
                diagnostics["conditions_unmapped"] += 1

        diagnostics["trees"]           = trees
        diagnostics["layers"]          = sorted(layers)
        diagnostics["pan_only_layers"] = sorted(pan_only_layers)

        if trees:
            diagnostics["resolution"] = "+".join(sorted(layers))
        elif diagnostics["conditions_pan_only"]:
            diagnostics["resolution"] = self.RESOLUTION_PAN_ONLY
        else:
            diagnostics["resolution"] = self.RESOLUTION_UNMAPPED

        return diagnostics

    def _resolution_layers(self, condition: dict):
        """
        Yield (layer_name, tree_numbers) for one condition, in priority order.

        A generator rather than a chain of if-blocks so the caller can apply
        the pan-cancer depth test to each layer independently and continue
        past a layer that resolved only to C04 / a depth-2 node.

        Layers with no code / no data yield nothing at all, so a missing
        crosswalk is indistinguishable from a crosswalk miss to the caller
        (both simply advance to the next layer).
        """
        # --- Layer 1: SNOMED crosswalk (gold standard) ---
        snomed_code = self._extract_code_by_system(condition, "snomed")
        if snomed_code:
            yield "snomed", set(self.snomed_to_trees.get(snomed_code, []))

        # --- Layer 2: ICD-10-CM crosswalk (real EHR primary path) ---
        icd10_code = self._extract_code_by_system(condition, "icd10cm")
        if icd10_code:
            yield "icd10", set(self.icd10_to_trees.get(icd10_code, []))

        # --- Layer 3: Fuzzy string match, one entry per strategy ---
        display = (condition.get("display") or "").strip()
        if display:
            yield from self._fuzzy_layers(display)

    def _extract_code_by_system(self, condition: dict, target_system: str) -> Optional[str]:
        """
        Extract a specific code system's code from a parsed FHIR condition dict.

        Multi-coding aware: scans the "codings" list for the first entry whose
        system_key matches target_system. Falls back to the single "code" field
        if "codings" is absent (backward compatible with pre-1.1b parsed data).

        Args:
            condition:     Parsed condition dict from FHIR parser.
            target_system: System key to look for ("snomed", "icd10cm", etc.).
                           Must match keys from _SYSTEM_URI_TO_KEY in File 07.

        Returns:
            Code string if found, None otherwise.
        """
        # Multi-coding path: scan for specific system
        codings = condition.get("codings", [])
        if codings:
            for c in codings:
                if c.get("system_key") == target_system:
                    code = (c.get("code") or "").strip()
                    if code and code.lower() not in ("unknown", "none"):
                        return code
            return None

        # Backward compatible fallback: no codings list, use single code field
        code = (condition.get("code") or "").strip()
        if code and code.lower() not in ("unknown", "none", ""):
            return code
        return None
    

    @staticmethod
    def _stem(word: str) -> str:
        """
        Lightweight medical stemmer for MeSH ↔ FHIR word-form alignment.

        Applied to BOTH sides (MeSH index keys at build time, display words
        at query time) so that different word forms normalize to the same
        stem. False stems are harmless because both sides use the same
        function — consistency matters, not linguistic correctness.

        Rules (first match wins):
          Strip trailing 's'   (len > 2, not 'ss') : neoplasms → neoplasm
          Strip trailing 'ic'  (len > 4)            : colonic → colon
          Strip trailing 'al'  (len > 4)            : rectal → rect
          Strip trailing 'ous' (len > 5)            : villous → vill
          Strip trailing 'ary' (len > 5)            : biliary → bili

        Verified safe against:
          - 'ss' words: mass, loss (no strip)
          - Short words: cell, oral, anal, tic (length guards prevent strip)
          - Medical collisions: hepatic≠hepatitis, renal≠renin (no collisions)
          - Hyphenated: non-small (no suffix match, passes through)
        """
        if word.endswith("s") and len(word) > 2 and not word.endswith("ss"):
            return word[:-1]
        if word.endswith("ic") and len(word) > 4:
            return word[:-2]
        if word.endswith("al") and len(word) > 4:
            return word[:-2]
        if word.endswith("ous") and len(word) > 5:
            return word[:-3]
        if word.endswith("ary") and len(word) > 5:
            return word[:-3]
        
        return word


    def _fuzzy_layers(self, display: str):
        """
        Yield (strategy_name, tree_numbers) for a condition display string.

        Strategies, in priority order:
          fuzzy_exact     -- display IS a MeSH descriptor name
          fuzzy_synonym   -- UMLS synonym crosswalk, O(1) dict lookup
          fuzzy_substring -- descriptor contained in display, or vice versa
          fuzzy_stem      -- stemmed word overlap against the descriptor index

        The two heuristic strategies are skipped when the display carries no
        site or histology token at all (_SITELESS_DISPLAY_STEMS).

        Yielding instead of returning the first hit lets the caller apply the
        pan-cancer depth test per strategy: a display of "malignant neoplastic
        disease" hits fuzzy_synonym with C04 alone, and the caller can walk on
        to the substring and stem strategies rather than accept it. Consumers
        that want the old first-hit-wins behaviour take the first non-empty
        yield (see _fuzzy_match_display).
        """
        # Clean: strip parenthetical suffixes like "(disorder)", "(finding)"
        display_clean = re.sub(r"\([^)]*\)", "", display).strip()
        display_lower = display_clean.lower()

        if not display_lower:
            return

        display_words = set(display_lower.split()) - self._DISPLAY_STOPWORDS

        if not display_words:
            return

        # Does the display name a site or histology at all? Punctuation is
        # stripped first so "neoplasm," stems to "neoplasm" and is recognised
        # as generic. The two exact strategies below run regardless — they
        # either match a real descriptor or they do not. The two heuristic
        # strategies are gated on this, for the reason on
        # _SITELESS_DISPLAY_STEMS.
        _tokens = re.sub(r"[^\w\s-]", " ", display_lower).split()
        _site_stems = {
            self._stem(w) for w in _tokens
            if len(w) >= 3 and w not in self._DISPLAY_STOPWORDS
        } - self._SITELESS_DISPLAY_STEMS
        has_site_token = bool(_site_stems)

        # --- Strategy fuzzy_exact: display IS a MeSH descriptor name ---
        # Must be tried before substring matching. Without it,
        # "melanoma" matches "non-melanoma skin neoplasms" via substring,
        # and "cholangiocarcinoma" matches "carcinoma" via substring,
        # because Python set iteration order is non-deterministic.
        if display_lower in self._all_names:
            yield "fuzzy_exact", set(self.name_to_trees.get(display_lower, []))

        # --- Strategy fuzzy_synonym: UMLS crosswalk (O(1) dictionary lookup) ---
        # Resolves common clinical names ("prostate cancer", "gastric cancer",
        # "NSCLC") to correct MeSH C04 trees via UMLS Metathesaurus synonyms.
        # This fixes the critical failure where fuzzy matching mapped
        # "prostate cancer" to "Hereditary Breast and Ovarian Cancer Syndrome".
        # Runs before substring/stemmed matching because it is exact, fast,
        # and authoritative (backed by UMLS CUI-level identity).
        if self.synonym_to_trees:
            trees = self.synonym_to_trees.get(display_lower)
            if trees:
                yield "fuzzy_synonym", set(trees)

        if not has_site_token:
            return

        # --- Strategy fuzzy_substring ---
        # "malignant neoplasm of colon" matches if a MeSH name
        # is contained within it or vice versa
        matched_trees = set()
        for name in self._all_names:
            if name in display_lower or display_lower in name:
                matched_trees.update(self.name_to_trees.get(name, []))
        if matched_trees:
            yield "fuzzy_substring", matched_trees

        # --- Strategy fuzzy_stem: stemmed word overlap scoring ---
        display_stems = {self._stem(w) for w in display_words if len(w) >= 3}

        if not display_stems:
            return

        candidates = {}  # {mesh_name: overlap_count}

        for stem in display_stems:
            if stem in self._word_index:
                for name in self._word_index[stem]:
                    candidates[name] = candidates.get(name, 0) + 1

        if not candidates:
            return

        # Require at least 2 matching stems (or 1 if stem ≥ 6 chars)
        min_overlap = 1 if any(len(s) >= 6 for s in display_stems) else 2
        best_score = max(candidates.values())

        if best_score < min_overlap:
            return

        # Trees from all descriptors with the best score
        tree_numbers = set()
        for name, score in candidates.items():
            if score == best_score:
                tree_numbers.update(self.name_to_trees.get(name, []))

        if tree_numbers:
            yield "fuzzy_stem", tree_numbers

    def _fuzzy_match_display(self, display: str) -> Set[str]:
        """
        Match a condition display string against MeSH descriptor names.

        Returns the first strategy result that names an actual cancer site
        (depth > PAN_CANCER_MAX_DEPTH). Strategies resolving only to C04 or a
        depth-2 node are skipped, for the reason given in resolve_patient_trees.

        Kept as a named entry point for diagnostics and ad-hoc lookups;
        the pipeline goes through resolve_patient_trees().

        Returns:
            Set of specific C04 tree numbers, empty if nothing resolved
        """
        for _strategy, trees in self._fuzzy_layers(display):
            specific = specific_cancer_trees(trees)
            if specific:
                return specific
        return set()


    # -----------------------------------------------------------------
    # Trial side: trial["conditions"] → MeSH tree numbers
    # -----------------------------------------------------------------

    def trial_mesh_trees(self, trial: dict) -> Set[str]:
        """
        Extract MeSH C04 tree numbers from a trial's conditions.

        ClinicalTrials.gov conditions are MeSH terms, so this is a
        direct dictionary lookup — no crosswalk needed.

        Also checks trial keywords for MeSH-matchable terms.

        Args:
            trial: Trial dict with 'conditions' and 'keywords' lists

        Returns:
            Set of C04 tree number strings, or empty set if unmappable
        """
        tree_numbers = set()

        # Direct lookup: trial conditions are MeSH terms
        for condition in (trial.get("conditions") or []):
            name_lower = condition.strip().lower()
            trees = self.name_to_trees.get(name_lower, [])
            if trees:
                tree_numbers.update(trees)
            elif self.synonym_to_trees:
                # Fallback: free-text conditions (legacy XML, non-standard entries)
                # e.g., "Non Small Cell Lung Cancer" -> Carcinoma, Non-Small-Cell Lung trees
                syn_trees = self.synonym_to_trees.get(name_lower, [])
                tree_numbers.update(syn_trees)

        # Also check keywords (some trials put cancer type in keywords)
        if not tree_numbers:
            for keyword in (trial.get("keywords") or []):
                name_lower = keyword.strip().lower()
                trees = self.name_to_trees.get(name_lower, [])
                if trees:
                    tree_numbers.update(trees)
                elif self.synonym_to_trees:
                    syn_trees = self.synonym_to_trees.get(name_lower, [])
                    tree_numbers.update(syn_trees)

        return tree_numbers

    
    # Stems that appear in virtually every C04 MeSH descriptor.
    # They carry zero disease-specificity signal in a trial title
    # and would cause every title to match every patient's cancer.
    _GENERIC_ONCOLOGY_STEMS = frozenset({
        "cancer", "carcinoma", "malignant", "malignancy",
        "metastasi", "metastat", "neoplasm", "oncolog",
        "tumor", "tumour", "cell",
    })

    # Minimum stem length for title-based MeSH resolution.
    # ≥5 captures colon (5), renal (5), liver (5).
    # Lung (4) is captured via "non-small" (9) in NSCLC titles.
    _TITLE_STEM_MIN_LEN = 5

    # Stems that name no anatomical site or histology on the PATIENT side.
    # The generic oncology stems above, plus the words a coder writes when the
    # record does not say where the cancer is.
    #
    # A display built only from these ("Malignant neoplastic disease",
    # "Malignant neoplasm, unspecified", "Cancer") identifies no site, so the
    # two heuristic strategies must not answer for it: stem overlap on
    # "malignant"/"neoplast"/"disease" returns 27 unrelated descriptors
    # (Bowen's Disease, Hodgkin Disease, Carcinoid Heart Disease...), and
    # substring on "cancer" returns Hereditary Breast and Ovarian Cancer
    # Syndrome — the same class of false identity the UMLS synonym crosswalk
    # was built to stop. A false site is worse than no site: no site means
    # KEEP everything, a false site means Stage 4 drops the right trials.
    _SITELESS_DISPLAY_STEMS = _GENERIC_ONCOLOGY_STEMS | frozenset({
        "neoplast",                        # _stem("neoplastic")
        "disease", "disorder", "lesion", "mass", "growth",
        "primary", "secondary", "unspecified", "site", "nos",
        "invasive", "situ", "overlapping", "stage", "grade",
    })

    def _resolve_trees_from_title(self, title: str) -> Set[str]:
        """
        Extract specific (non-pan-cancer) MeSH C04 trees from a trial title.

        Uses the same stemmed word index as _fuzzy_match_display but skips
        generic oncology stems (neoplasm, cancer, tumor, etc.) that appear
        in every oncology trial title and carry zero specificity signal.

        Only returns trees at depth > PAN_CANCER_MAX_DEPTH.

        Returns:
            Set of specific C04 tree numbers, or empty set if no specific
            cancer type is identifiable from the title.
        """
        cleaned = re.sub(r"[^\w\s-]", "", title.lower()).strip()
        words = cleaned.split()

        _stopwords = frozenset({
            "a", "an", "the", "of", "in", "on", "for", "to", "and", "or",
            "with", "by", "at", "as", "is", "its", "not", "from", "that",
            "this", "are", "was", "were", "been", "be", "has", "have", "had",
            "do", "does", "did", "will", "would", "could", "should", "may",
            "might", "can", "shall", "must", "need",
        })
        words = [w for w in words if len(w) >= 3 and w not in _stopwords]

        if not words:
            return set()

        candidates = {}
        has_specific_stem = False

        for word in words:
            stem = self._stem(word)
            if len(stem) < 3:
                continue
            if stem in self._GENERIC_ONCOLOGY_STEMS:
                continue
            if len(stem) < self._TITLE_STEM_MIN_LEN:
                continue
            if stem in self._word_index:
                has_specific_stem = True
                for name in self._word_index[stem]:
                    candidates[name] = candidates.get(name, 0) + 1

        if not candidates or not has_specific_stem:
            return set()

        best_score = max(candidates.values())

        tree_numbers = set()
        for name, score in candidates.items():
            if score == best_score:
                tree_numbers.update(self.name_to_trees.get(name, []))

        specific_trees = {
            t for t in tree_numbers
            if len(t.split(".")) > self.PAN_CANCER_MAX_DEPTH
        }

        return specific_trees
    

    # -----------------------------------------------------------------
    # Admission decision: may this trial enter the corpus at all?
    # -----------------------------------------------------------------

    # Retained from the scraper's old frozenset screen, and retained ONLY as a
    # keep-signal. It can move a verdict to TRIAL_ONCOLOGY and can never move
    # one to TRIAL_NON_ONCOLOGY, so it cannot cause a drop and its famous gaps
    # ("blastoma", "thelioma") cost nothing: a trial it misses falls through to
    # the crosswalk, which resolves all five of them.
    _ONCOLOGY_VOCABULARY = frozenset({
        "neoplasm", "cancer", "carcinoma", "sarcoma", "lymphoma", "leukemia",
        "leukaemia", "melanoma", "glioma", "myeloma", "tumor", "tumour",
        "malignant", "malignancy", "oncology", "oncologic", "oncolyt",
        "metastatic", "metastasis", "blastoma", "thelioma", "adenoma",
        "myelodysplas", "myeloproliferat", "carcinoid", "chemotherapy",
        "radiotherapy", "dysplas", "mastectom",
    })

    # MeSH top-level categories that name a DISEASE. C is the Diseases tree;
    # F03 is Mental Disorders. Everything else -- E (Analytical, Diagnostic and
    # Therapeutic Techniques), N (Health Care), M (Named Groups), G (Phenomena),
    # B (Organisms), D (Chemicals), F01 (Behavior) -- names something that is
    # not a disease at all.
    #
    # WHY THIS GATES THE DROP, and it was found by inspecting the trials the
    # first version of this screen actually removed rather than by reading it.
    # ClinicalTrials.gov's condition field is free text and sponsors put
    # non-diseases in it. Two real examples out of 31 drops:
    #
    #   NCT06545292  conditions=["Drug Monitoring"]        (E01)
    #                title="...Drug Monitoring of Oncolytics"
    #   NCT05436561  conditions=["Disease-free Survival"]  (E01, E05, N04...)
    #                title="...Reduced Conditioning Regimen..."
    #
    # Both are oncology trials. "Drug Monitoring" IS a MeSH term and it IS
    # outside C04, so the naive rule made a confident positive non-oncology
    # determination from a string that names no disease whatsoever. A term that
    # is not a disease is not evidence that the trial is not cancer, so it now
    # reads as UNRESOLVED and the trial is kept. Measured against the captured
    # drop set: 11 of 31 drops become keeps, including both of the above and a
    # trial in Clonal Cytopenia of Undetermined Significance, which is a
    # PRE-MALIGNANT myeloid state.
    # Prefixes, matched with startswith: "C" covers the whole Diseases tree
    # (C01..C26), "F03" covers Mental Disorders without admitting F01 Behavior.
    _DISEASE_CATEGORIES = ("C", "F03")

    def _is_disease_category(self, categories) -> bool:
        """True if any category names a disease. See _DISEASE_CATEGORIES.

        Deliberately UNDECORATED. It reads no instance state and would be a
        natural @classmethod, but every decorated definition in the package is
        pinned by name in tests/test_package_invariants.py section 2i, and a
        decorator added for tidiness is a pinned-inventory edit for no
        behavioural gain. A plain method costs nothing and keeps that inventory
        meaning "something structural changed".
        """
        return any(
            any(c.startswith(prefix) for prefix in self._DISEASE_CATEGORIES)
            for c in categories
        )

    def classify_trial_oncology(self, trial: dict) -> dict:
        """Decide whether a trial may be admitted to the corpus.

        THE ASYMMETRY IS THE WHOLE DESIGN. Three independent tests can vote
        KEEP; exactly one test can vote DROP, and it requires every registered
        condition to be a MeSH term positively outside C04.

        Order, first hit wins:

          1. C04 crosswalk on conditions + keywords (trial_mesh_trees) ->
             TRIAL_ONCOLOGY. This is the layer the old keyword screen should
             have been, and it resolves Glioblastoma, Mesothelioma,
             Neuroblastoma, Retinoblastoma and Hepatoblastoma, none of which
             contains any substring in the old list.
          2. Oncology vocabulary anywhere in conditions/keywords/title ->
             TRIAL_ONCOLOGY. A pure keep-signal, see _ONCOLOGY_VOCABULARY.
          3. Every registered condition present in non_oncology_terms ->
             TRIAL_NON_ONCOLOGY. The only verdict that permits a drop.
          4. Anything else -> TRIAL_UNRESOLVED. KEEP, and counted.

        WHY ONLY `conditions` DECIDES STEP 3, when steps 1 and 2 also read
        keywords and title. conditions is the registered disease list; keywords
        are free-text author tags and a title is prose. A term missing from
        either of those is not evidence of anything, so letting them contribute
        to a DROP would manufacture positive determinations out of noise. They
        may only ever add a keep.

        A trial with NO registered conditions is TRIAL_UNRESOLVED, never
        TRIAL_NON_ONCOLOGY: "all zero of its conditions are non-cancer" is
        vacuously true and would drop every such trial on no evidence.

        Returns:
            dict:
              "verdict"       : one of TRIAL_ONCOLOGY_VERDICTS
              "evidence"      : short machine-readable reason
              "trees"         : sorted C04 trees found (may be empty)
              "categories"    : sorted non-C04 MeSH top categories, step 3 only
              "unresolved"    : condition strings that resolved to nothing
        """
        conditions = [c.strip() for c in (trial.get("conditions") or [])
                      if isinstance(c, str) and c.strip()]
        keywords = [k.strip() for k in (trial.get("keywords") or [])
                    if isinstance(k, str) and k.strip()]

        # --- 1. Positive oncology by crosswalk ---
        trees = self.trial_mesh_trees(trial)
        if trees:
            return {"verdict": TRIAL_ONCOLOGY, "evidence": "c04_crosswalk",
                    "trees": sorted(trees), "categories": [], "unresolved": []}

        # --- 2. Positive oncology by vocabulary (keep-signal only) ---
        haystack = " ".join(conditions + keywords
                            + [str(trial.get("title") or "")]).lower()
        if any(term in haystack for term in self._ONCOLOGY_VOCABULARY):
            return {"verdict": TRIAL_ONCOLOGY, "evidence": "oncology_vocabulary",
                    "trees": [], "categories": [], "unresolved": []}

        # --- 3. Positive NON-oncology: the only route to a drop ---
        if not conditions:
            return {"verdict": TRIAL_UNRESOLVED, "evidence": "no_conditions",
                    "trees": [], "categories": [], "unresolved": []}

        if not self.non_oncology_terms:
            # The layer is absent. It cannot make a positive determination, so
            # it must not pretend to: everything is unresolved and kept.
            return {"verdict": TRIAL_UNRESOLVED,
                    "evidence": "non_oncology_layer_absent",
                    "trees": [], "categories": [], "unresolved": conditions}

        categories = set()
        unresolved = []
        not_a_disease = []
        for cond in conditions:
            cats = self.non_oncology_terms.get(cond.lower())
            if cats is None:
                unresolved.append(cond)
            elif not self._is_disease_category(cats):
                # A MeSH term that names no disease. See _DISEASE_CATEGORIES:
                # this is the shape that produced real false drops.
                not_a_disease.append(cond)
            else:
                categories.update(cats)

        if unresolved or not_a_disease:
            # Either way the screen has no opinion. The two are reported apart
            # because they need different fixes: an unresolved string may be a
            # missing synonym, a non-disease string is a registration habit.
            return {"verdict": TRIAL_UNRESOLVED,
                    "evidence": ("condition_unresolved" if unresolved
                                 else "condition_not_a_disease"),
                    "trees": [], "categories": sorted(categories),
                    "unresolved": unresolved + not_a_disease}

        return {"verdict": TRIAL_NON_ONCOLOGY, "evidence": "all_conditions_non_c04",
                "trees": [], "categories": sorted(categories), "unresolved": []}

    # -----------------------------------------------------------------
    # Filter decision: is this trial relevant to this patient?
    # -----------------------------------------------------------------

    def is_cancer_relevant(self, patient_trees: Set[str],
                           trial: dict) -> bool:
        """
        Determine if a trial's cancer type is relevant to the patient.

        Decision logic:
          1. If patient has no trees → KEEP (can't classify patient)
          2. If trial has no trees  → KEEP (can't classify trial)
          3. If trial is pan-cancer → KEEP (basket trial, any cancer)
          4. If shared ancestry     → KEEP (same cancer family)
          5. Otherwise              → DROP (unrelated cancer type)

        Shared ancestry = one tree number is a prefix of another.
        Example: patient has C04.588.274 (Breast Neoplasms)
                 trial has   C04.588.274.476 (Inflammatory Breast Neoplasms)
                 → C04.588.274 is prefix of C04.588.274.476 → RELATED

        Args:
            patient_trees: Set of C04 tree numbers from patient_mesh_trees()
            trial:         Trial dict (passed to trial_mesh_trees())

        Returns:
            True if trial should be kept, False if it should be filtered out
        """
        # Rule 1: Can't classify patient → keep everything
        if not patient_trees:
            return True

        # Get trial trees
        trial_trees = self.trial_mesh_trees(trial)

        # Rule 2: Can't classify trial from conditions/keywords →
        # fallback: try to resolve cancer type from trial title.
        # If title contains a specific cancer type that does NOT share
        # ancestry with the patient, DROP. Otherwise KEEP (conservative).
        if not trial_trees:
            title = (trial.get("title") or "").strip()
            if title:
                title_trees = self._resolve_trees_from_title(title)
                if title_trees:
                    # Title resolved to specific cancer trees.
                    # Check ancestry against patient.
                    for pt in patient_trees:
                        for tt in title_trees:
                            if pt.startswith(tt) or tt.startswith(pt):
                                return True  # shared ancestry via title
                    # Specific cancer in title, no ancestry with patient → DROP
                    return False
            # No title or no trees from title → conservative KEEP
            return True

        # Rule 3: Pan-cancer trial → keep
        # A trial is pan-cancer if ALL its tree numbers are at depth ≤ 2
        # (e.g., C04 = depth 1, C04.588 = depth 2)
        if self._is_pan_cancer(trial_trees):
            return True

        # Rule 4: Shared ancestry → keep
        for pt in patient_trees:
            for tt in trial_trees:
                if pt.startswith(tt) or tt.startswith(pt):
                    return True

        # Rule 5: No relationship found → drop
        return False

    def _is_pan_cancer(self, trial_trees: Set[str]) -> bool:
        """
        Check if a trial is pan-cancer (cancer-agnostic / basket trial).

        A trial is pan-cancer if ALL its C04 tree numbers have depth ≤ 2.
        Depth = number of dot-separated segments.
          C04         → depth 1 (Neoplasms root)
          C04.588     → depth 2 (Neoplasms by Site — still very broad)
          C04.588.274 → depth 3 (Breast Neoplasms — specific site)

        Examples of pan-cancer trials:
          - "Solid Tumors" (C04.588) — any solid tumor
          - "Neoplasms" (C04) — any cancer
          - "Carcinoma" (C04.557.337) — depth 3, NOT pan-cancer

        Returns:
            True if all trial trees are at depth ≤ PAN_CANCER_MAX_DEPTH
        """
        if not trial_trees:
            return False
        
        for tree in trial_trees:
            depth = len(tree.split("."))
            if depth > self.PAN_CANCER_MAX_DEPTH:
                return False
        return True

    # -----------------------------------------------------------------
    # Diagnostics
    # -----------------------------------------------------------------

    def explain_filter(self, patient_trees: Set[str],
                       trial: dict) -> str:
        """
        Human-readable explanation of the filter decision.
        Useful for debugging and logging.
        """
        trial_trees = self.trial_mesh_trees(trial)

        if not patient_trees:
            return "KEEP: patient cancer type unmappable to MeSH"

        if not trial_trees:
            return "KEEP: trial cancer type unmappable to MeSH"

        if self._is_pan_cancer(trial_trees):
            return "KEEP: pan-cancer / basket trial"

        for pt in patient_trees:
            for tt in trial_trees:
                if pt.startswith(tt) or tt.startswith(pt):
                    pt_name = self.tree_to_name.get(pt, pt)
                    tt_name = self.tree_to_name.get(tt, tt)
                    return f"KEEP: shared ancestry — patient [{pt_name}] ↔ trial [{tt_name}]"

        # Build readable names for the drop reason
        pt_names = [self.tree_to_name.get(t, t) for t in sorted(patient_trees)]
        tt_names = [self.tree_to_name.get(t, t) for t in sorted(trial_trees)]
        return (f"DROP: no shared ancestry — "
                f"patient [{', '.join(pt_names[:3])}] vs "
                f"trial [{', '.join(tt_names[:3])}]")


# ===========================================================================
# LOADER: Called at startup alongside _CANCER_REGISTRY
# ===========================================================================


def load_mesh_filter() -> Optional[MeSHCancerFilter]:
    """
    Load pre-built MeSH lookup files and return a MeSHCancerFilter instance.

    Required (from desc2026.xml -- enables fuzzy string matching):
      - mesh_c04_lookup.json
      - mesh_tree_to_name.json

    Optional (from MRCONSO_2025AB.RRF -- enables code-based crosswalks):
      - snomed_to_mesh_trees.json  (SNOMED crosswalk, Layer 1)
      - icd10_to_mesh_trees.json   (ICD-10-CM crosswalk, Layer 2, built by Item 2.1)

    If crosswalk files are missing -> loads without them (fuzzy matching only),
    printing which one and counting it in MESH_FILTER_DEGRADATIONS.

    IF A REQUIRED FILE IS MISSING THIS RAISES (item 11a). It used to print a
    warning and return None, and the None then travelled all the way to Stage 4,
    where every trial passes the cancer site filter. That is a real production
    configuration in the sense that the pipeline keeps running and produces
    matches; it is not one anybody chooses, because the only trace of it was a
    warning on a console nobody reads two hours into a 22k-patient batch.

    None IS STILL A REACHABLE STATE, and every branch that handles it is real
    and still tested. What changed is that it can no longer be CREATED SILENTLY
    from missing files. It arrives two ways now, both deliberate:

      * a dependency override -- ``deps.set_override(deps.MESH_FILTER, None)``,
        which is what "tests/test_agent_retrieval_observability.py" installs and what
        "tests/test_agent_ablation_flag_passthrough.py" stubs. An override never calls
        this function at all;
      * ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES=1, below, which logs at WARNING,
        records the layer, and returns None exactly as before.

    Raises:
        settings.DegradedDependencyError: a required lookup file is absent and
            the degraded-mode variable is not set.
    """
    # Read HERE, not at import: oncotriage.paths resolves lazily and a
    # module-scope `from ... import data_MeSH_path` would resolve the whole
    # sibling tree for anything that imported this module. See the docstring.
    mesh_dir = Path(paths.data_MeSH_path)

    lookup_path        = mesh_dir / "mesh_c04_lookup.json"
    tree_path          = mesh_dir / "mesh_tree_to_name.json"
    crosswalk_path     = mesh_dir / "snomed_to_mesh_trees.json"
    icd10_xwalk_path   = mesh_dir / "icd10_to_mesh_trees.json"

    # Required files — without these, no filter at all
    if not lookup_path.exists() or not tree_path.exists():
        missing = []
        if not lookup_path.exists():
            missing.append(str(lookup_path))
        if not tree_path.exists():
            missing.append(str(tree_path))

        what_is_missing = (
            "MeSH Cancer Filter core lookup file(s) not found:\n"
            + "".join(f"    - {m}\n" for m in missing)
            + "  Without them the Stage 4 cancer site filter cannot run and "
              "EVERY trial passes it, for every patient."
        )
        how_to_fix = ('python "09- MeSH Cancer Site Relevance Filter.py"   '
                      "(runs build_mesh_lookup(); requires desc2026.xml)")

        allowed, source = settings.resolve_allow_degraded_registries()
        if not allowed:
            raise settings.degraded_dependency_error(
                MESH_LAYER_CORE, what_is_missing, how_to_fix)

        # Degraded, deliberately. Log at WARNING naming exactly which layer is
        # absent and which variable permitted it, record it, and return the
        # None every downstream branch already handles.
        MESH_FILTER_DEGRADATIONS[MESH_LAYER_CORE] += 1
        logger.warning(
            "DEGRADED: the %r layer is ABSENT — the Stage 4 cancer site filter "
            "is DISABLED and every trial will pass it. Permitted by %s. "
            "Missing: %s. Supply it with: %s",
            MESH_LAYER_CORE, source, ", ".join(missing), how_to_fix,
        )
        console.out(f"WARNING: MeSH Cancer Filter DISABLED — {MESH_LAYER_CORE} layer "
              f"absent, permitted by {source}. All trials will pass the cancer "
              f"site filter.\n")
        return None

    console.out("Loading MeSH Cancer Filter...")

    with open(lookup_path, "r") as f:
        name_to_trees = json.load(f)

    with open(tree_path, "r") as f:
        tree_to_name = json.load(f)

    # Optional: SNOMED crosswalk (Layer 1)
    snomed_to_trees = {}
    if crosswalk_path.exists():
        with open(crosswalk_path, "r") as f:
            snomed_to_trees = json.load(f)
    else:
        # LOGGED before item 11a, RECORDED as well now. It does not raise: the
        # filter is designed to work without it and says so in the class
        # docstring, so this is a documented capability reduction rather than a
        # layer disappearing unannounced. The count is what lets a run answer
        # "was the SNOMED layer there" after the fact instead of from scrollback.
        MESH_FILTER_DEGRADATIONS[MESH_LAYER_SNOMED_CROSSWALK] += 1
        logger.warning("MeSH: the %r layer is absent (%s) -- patient SNOMED "
                       "codes will fall through to fuzzy display matching.",
                       MESH_LAYER_SNOMED_CROSSWALK, crosswalk_path)
        console.out("  NOTE: snomed_to_mesh_trees.json not found -- SNOMED crosswalk disabled.")

    # Optional: ICD-10-CM crosswalk (Layer 2)
    icd10_to_trees = {}
    if icd10_xwalk_path.exists():
        with open(icd10_xwalk_path, "r") as f:
            icd10_to_trees = json.load(f)
    else:
        MESH_FILTER_DEGRADATIONS[MESH_LAYER_ICD10_CROSSWALK] += 1
        logger.warning("MeSH: the %r layer is absent (%s) -- patient ICD-10-CM "
                       "codes will fall through to fuzzy display matching.",
                       MESH_LAYER_ICD10_CROSSWALK, icd10_xwalk_path)
        console.out("  NOTE: icd10_to_mesh_trees.json not found -- ICD-10 crosswalk disabled.")
        console.out("  To enable: run build_icd10_to_mesh_crosswalk() (Item 2.1).")

    # Optional: UMLS synonym crosswalk (Strategy 0 in fuzzy matching)
    synonym_xwalk_path = mesh_dir / "umls_synonym_to_mesh_trees.json"
    synonym_to_trees = {}
    if synonym_xwalk_path.exists():
        with open(synonym_xwalk_path, "r") as f:
            synonym_to_trees = json.load(f)
    else:
        MESH_FILTER_DEGRADATIONS[MESH_LAYER_UMLS_SYNONYMS] += 1
        logger.warning("MeSH: the %r layer is absent (%s) -- clinical synonyms "
                       "will fall through to substring and stem matching.",
                       MESH_LAYER_UMLS_SYNONYMS, synonym_xwalk_path)
        console.out("  NOTE: umls_synonym_to_mesh_trees.json not found -- UMLS synonym crosswalk disabled.")
        console.out("  To enable: run build_umls_synonym_crosswalk() (Item 1).")

    # Optional: the non-oncology complement, read only by the scraper's
    # admission screen (classify_trial_oncology). Its absence CANNOT cause a
    # trial to be dropped -- it can only stop one from being ruled out -- so it
    # degrades in the safe direction and does not raise. It is still recorded,
    # because "the screen admitted everything" and "the screen ran" look
    # identical in a corpus count.
    non_oncology_path = mesh_dir / "mesh_non_oncology_terms.json"
    non_oncology_terms = {}
    if non_oncology_path.exists():
        with open(non_oncology_path, "r") as f:
            non_oncology_terms = json.load(f)
    else:
        MESH_FILTER_DEGRADATIONS[MESH_LAYER_NON_ONCOLOGY] += 1
        logger.warning("MeSH: the %r layer is absent (%s) -- the trial "
                       "admission screen cannot make a positive non-oncology "
                       "determination and will ADMIT every trial.",
                       MESH_LAYER_NON_ONCOLOGY, non_oncology_path)
        console.out("  NOTE: mesh_non_oncology_terms.json not found -- the scrape "
              "admission screen will admit every trial.")
        console.out('  To enable: run python "09- MeSH Cancer Site Relevance Filter.py"')

    if not snomed_to_trees and not icd10_to_trees and not synonym_to_trees:
        console.out("  Filter will use fuzzy string matching only.")

    return MeSHCancerFilter(name_to_trees, tree_to_name, snomed_to_trees,
                            icd10_to_trees, synonym_to_trees, non_oncology_terms)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 20 2026

@author: ramyalsaffar
"""
