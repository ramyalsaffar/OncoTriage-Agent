"""MeSH crosswalk builders — the offline half of File 09.

ITEM 20c, PASS 2a: this is "09- MeSH Cancer Site Relevance Filter.py" lines
57-696, moved. Logic byte-for-byte unchanged.

THESE FIVE FUNCTIONS ARE CALLED FROM NOWHERE IN THE PIPELINE. Verified by grep
across every file in the repository: the only call sites are File 09's own
__main__ block, which is still the documented way to run them. They parse
desc2026.xml and MRCONSO_2025AB.RRF and write the JSON lookups that
oncotriage.registries.mesh reads back.

They live in their own module so that importing the filter carries no
MRCONSO-reading code. Nothing imports this module at runtime; File 09's shim
re-exports it so `python "09- MeSH Cancer Site Relevance Filter.py"` still
rebuilds the lookups.

Each builder takes its output directory as an ARGUMENT, so this module needs
nothing from oncotriage at all — not even data_MeSH_path. File 09's __main__
block supplies it, the way it always did.
"""

import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path



#------------------------------------------------------------------------------


# ===========================================================================
# DATA BUILDER: Parse raw files → compact JSON lookups (run once)
# ===========================================================================


def build_mesh_lookup(mesh_xml_path: str, output_dir: str) -> dict:
    """
    Parse desc2026.xml and extract the C04 (Neoplasms) subtree.

    Produces two JSON files:
      mesh_c04_lookup.json   : {descriptor_name_lower: [tree_numbers]}
      mesh_tree_to_name.json : {tree_number: descriptor_name}

    Also builds an in-memory name-to-tree mapping for all MeSH terms
    (including non-C04) that mention cancer-related tree numbers.

    Uses iterparse for memory efficiency — desc2026.xml is ~300MB.
    Only DescriptorRecord elements with at least one C04 tree number
    are retained.

    Args:
        mesh_xml_path: Path to desc2026.xml
        output_dir:    Directory to write JSON files

    Returns:
        dict with keys 'name_to_trees' and 'tree_to_name'
    """
    

    print("Parsing MeSH descriptor XML (C04 Neoplasms subtree)...")
    print(f"  Source: {mesh_xml_path}")

    name_to_trees = {}   # {descriptor_name_lower: [tree_numbers]}
    tree_to_name  = {}   # {tree_number: descriptor_name}
    uid_to_trees  = {}   # {descriptor_ui: [tree_numbers]} — for MRCONSO crosswalk

    # --- Iterparse: memory-efficient streaming of 300MB XML ---
    # We only care about DescriptorRecord elements.
    # For each, extract DescriptorUI, DescriptorName/String, and
    # TreeNumberList/TreeNumber.
    # Keep only records with at least one C04 tree number.
    context = ET.iterparse(mesh_xml_path, events=("end",))

    descriptor_count = 0
    c04_count = 0

    for event, elem in context:
        if elem.tag != "DescriptorRecord":
            continue

        descriptor_count += 1

        # Extract descriptor UI (e.g., "D001943")
        ui_elem = elem.find("DescriptorUI")
        descriptor_ui = ui_elem.text.strip() if ui_elem is not None and ui_elem.text else None

        # Extract descriptor name
        name_elem = elem.find("DescriptorName/String")
        if name_elem is None or not name_elem.text:
            elem.clear()
            continue

        descriptor_name = name_elem.text.strip()

        # Extract all tree numbers
        tree_numbers = []
        tree_list = elem.find("TreeNumberList")
        if tree_list is not None:
            for tn in tree_list.findall("TreeNumber"):
                if tn.text:
                    tree_numbers.append(tn.text.strip())

        # Filter: keep only C04 tree numbers (Neoplasms branch)
        c04_trees = [t for t in tree_numbers if t.startswith("C04")]

        if c04_trees:
            c04_count += 1
            name_lower = descriptor_name.lower()
            name_to_trees[name_lower] = c04_trees

            if descriptor_ui:
                uid_to_trees[descriptor_ui] = c04_trees

            for tree_num in c04_trees:
                tree_to_name[tree_num] = descriptor_name

        # Free memory — critical for 300MB file
        elem.clear()

    print(f"  Parsed {descriptor_count:,} descriptors total")
    print(f"  Retained {c04_count:,} C04 (Neoplasms) descriptors")
    print(f"  Tree numbers indexed: {len(tree_to_name):,}")
    print(f"  Descriptor UIDs indexed: {len(uid_to_trees):,}")

    # --- Save to JSON ---
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    lookup_path = output_path / "mesh_c04_lookup.json"
    tree_path   = output_path / "mesh_tree_to_name.json"
    uid_path    = output_path / "mesh_uid_to_trees.json"

    with open(lookup_path, "w") as f:
        json.dump(name_to_trees, f, indent=2)
    print(f"  Saved: {lookup_path} ({len(name_to_trees):,} entries)")

    with open(tree_path, "w") as f:
        json.dump(tree_to_name, f, indent=2)
    print(f"  Saved: {tree_path} ({len(tree_to_name):,} entries)")

    with open(uid_path, "w") as f:
        json.dump(uid_to_trees, f, indent=2)
    print(f"  Saved: {uid_path} ({len(uid_to_trees):,} entries)")

    return {"name_to_trees": name_to_trees, "tree_to_name": tree_to_name,
            "uid_to_trees": uid_to_trees}


def build_snomed_to_mesh_crosswalk(mrconso_path: str, output_dir: str,
                                    mesh_uid_to_trees: dict) -> dict:
    """
    Parse UMLS MRCONSO_2025AB.RRF and build SNOMED → MeSH crosswalk for C04 terms.

    The CUI (Concept Unique Identifier) is the bridge:
      Row with SAB=SNOMEDCT_US gives us SNOMED_CODE → CUI
      Row with SAB=MSH gives us CUI → MeSH_DESCRIPTOR_UID

    We use the MeSH CODE field (= descriptor UID like "D001943") to match
    against our C04 lookup, NOT the STR field. This is critical because
    MRCONSO has multiple string variants per MeSH descriptor (MH=Main
    Heading, PM=Permuted Term, EN=Entry Term), and STR might be a synonym
    like "Breast Cancer" that doesn't match our preferred name
    "Breast Neoplasms".

    MRCONSO_2025AB.RRF format (pipe-delimited, 18 columns):
      CUI|LAT|TS|LUI|STT|SUI|ISPREF|AUI|SAUI|SCUI|SDUI|SAB|TTY|CODE|STR|SRL|SUPPRESS|CVF

    Key columns (0-indexed):
      0  = CUI  (Concept Unique Identifier)
      11 = SAB  (Source vocabulary: SNOMEDCT_US, MSH, etc.)
      13 = CODE (Code in that vocabulary — for MSH this is the descriptor UID)
      14 = STR  (String name of the concept)

    Args:
        mrconso_path:      Path to MRCONSO_2025AB.RRF
        output_dir:        Directory to write JSON file
        mesh_uid_to_trees: {descriptor_ui: [tree_numbers]} from build_mesh_lookup()

    Returns:
        dict: {snomed_code: [mesh_descriptor_names_lower]}
    """
    print("\nBuilding SNOMED → MeSH crosswalk from MRCONSO_2025AB.RRF...")
    print(f"  Source: {mrconso_path}")

    # --- Pass 1: Build CUI → MeSH C04 tree numbers mapping ---
    # Match on CODE (descriptor UID), NOT on STR (which varies by TTY).
    cui_to_trees = defaultdict(set)   # {CUI: {tree_number, ...}}

    print("  Pass 1: Extracting CUI → MeSH C04 mappings (via descriptor UID)...")
    mesh_rows = 0
    matched_rows = 0

    with open(mrconso_path, "r", encoding="utf-8") as f:
        for line in f:
            fields = line.rstrip("\n").split("|")
            if len(fields) < 15:
                continue

            sab = fields[11]
            if sab != "MSH":
                continue

            mesh_rows += 1
            cui = fields[0]
            mesh_code = fields[13]  # Descriptor UID (e.g., "D001943")

            # Match against our C04 UID lookup
            if mesh_code in mesh_uid_to_trees:
                matched_rows += 1
                cui_to_trees[cui].update(mesh_uid_to_trees[mesh_code])

    print(f"    MSH rows scanned: {mesh_rows:,}")
    print(f"    MSH rows matched to C04: {matched_rows:,}")
    print(f"    CUIs with C04 trees: {len(cui_to_trees):,}")

    # --- Pass 2: Build SNOMED code → CUI, then resolve to tree numbers ---
    print("  Pass 2: Extracting SNOMED → CUI → C04 tree mappings...")
    snomed_to_trees = defaultdict(set)  # {snomed_code: {tree_number, ...}}
    snomed_rows = 0

    with open(mrconso_path, "r", encoding="utf-8") as f:
        for line in f:
            fields = line.rstrip("\n").split("|")
            if len(fields) < 15:
                continue

            sab = fields[11]
            if sab != "SNOMEDCT_US":
                continue

            snomed_rows += 1
            cui = fields[0]
            snomed_code = fields[13]

            # Resolve: does this CUI have any C04 tree numbers?
            if cui in cui_to_trees:
                snomed_to_trees[snomed_code].update(cui_to_trees[cui])

    print(f"    SNOMEDCT_US rows scanned: {snomed_rows:,}")
    print(f"    SNOMED codes with C04 tree mapping: {len(snomed_to_trees):,}")

    # Convert sets to sorted lists for JSON serialization
    snomed_to_trees_serializable = {
        code: sorted(trees)
        for code, trees in snomed_to_trees.items()
    }

    # --- Save to JSON ---
    output_path = Path(output_dir)
    crosswalk_path = output_path / "snomed_to_mesh_trees.json"

    with open(crosswalk_path, "w") as f:
        json.dump(snomed_to_trees_serializable, f, indent=2)
    print(f"  Saved: {crosswalk_path} ({len(snomed_to_trees_serializable):,} entries)")

    return snomed_to_trees_serializable


def build_icd10_to_mesh_crosswalk(mrconso_path: str, output_dir: str,
                                   mesh_uid_to_trees: dict) -> dict:
    """
    Parse UMLS MRCONSO_2025AB.RRF and build ICD-10-CM -> MeSH crosswalk for C04 terms.

    Mirrors build_snomed_to_mesh_crosswalk but targets SAB=ICD10CM rows.
    The CUI bridge is identical:
      Row with SAB=MSH     gives us CUI -> MeSH_DESCRIPTOR_UID -> C04 trees
      Row with SAB=ICD10CM gives us ICD10_CODE -> CUI

    Real EHRs primarily use ICD-10-CM for condition coding. Without this
    crosswalk, ICD-10-coded patients fall to fuzzy string matching (Layer 3)
    for MeSH tree resolution, which is lossy.

    ICD-10-CM codes are stored in BOTH dot-formatted (C34.10) and dot-free
    (C3410) forms so downstream lookup works regardless of which form the
    FHIR parser extracts. MRCONSO stores ICD-10-CM codes with dots.

    MRCONSO_2025AB.RRF format (pipe-delimited, 18 columns):
      CUI|LAT|TS|LUI|STT|SUI|ISPREF|AUI|SAUI|SCUI|SDUI|SAB|TTY|CODE|STR|SRL|SUPPRESS|CVF

    Key columns (0-indexed):
      0  = CUI  (Concept Unique Identifier)
      11 = SAB  (Source vocabulary: ICD10CM, MSH, etc.)
      13 = CODE (Code in that vocabulary)

    Args:
        mrconso_path:      Path to MRCONSO_2025AB.RRF
        output_dir:        Directory to write JSON file
        mesh_uid_to_trees: {descriptor_ui: [tree_numbers]} from build_mesh_lookup()

    Returns:
        dict: {icd10_code: [tree_numbers]} with both dotted and dot-free keys
    """
    print("\nBuilding ICD-10-CM -> MeSH crosswalk from MRCONSO_2025AB.RRF...")
    print(f"  Source: {mrconso_path}")

    # --- Pass 1: Build CUI -> MeSH C04 tree numbers mapping ---
    # Identical to build_snomed_to_mesh_crosswalk Pass 1.
    # Could be shared, but kept separate to avoid restructuring tested code.
    cui_to_trees = defaultdict(set)

    print("  Pass 1: Extracting CUI -> MeSH C04 mappings (via descriptor UID)...")
    mesh_rows = 0
    matched_rows = 0

    with open(mrconso_path, "r", encoding="utf-8") as f:
        for line in f:
            fields = line.rstrip("\n").split("|")
            if len(fields) < 15:
                continue

            sab = fields[11]
            if sab != "MSH":
                continue

            mesh_rows += 1
            cui = fields[0]
            mesh_code = fields[13]

            if mesh_code in mesh_uid_to_trees:
                matched_rows += 1
                cui_to_trees[cui].update(mesh_uid_to_trees[mesh_code])

    print(f"    MSH rows scanned: {mesh_rows:,}")
    print(f"    MSH rows matched to C04: {matched_rows:,}")
    print(f"    CUIs with C04 trees: {len(cui_to_trees):,}")

    # --- Pass 2: Build ICD-10-CM code -> CUI, then resolve to tree numbers ---
    print("  Pass 2: Extracting ICD-10-CM -> CUI -> C04 tree mappings...")
    icd10_to_trees = defaultdict(set)
    icd10_rows = 0

    with open(mrconso_path, "r", encoding="utf-8") as f:
        for line in f:
            fields = line.rstrip("\n").split("|")
            if len(fields) < 15:
                continue

            sab = fields[11]
            if sab != "ICD10CM":
                continue

            icd10_rows += 1
            cui = fields[0]
            icd10_code = fields[13]

            if cui in cui_to_trees:
                icd10_to_trees[icd10_code].update(cui_to_trees[cui])

    print(f"    ICD10CM rows scanned: {icd10_rows:,}")
    print(f"    ICD-10-CM codes with C04 tree mapping: {len(icd10_to_trees):,}")

    # --- Expand to dot-free variants ---
    # MRCONSO stores ICD-10-CM codes with dots (e.g., "C34.10").
    # Real EHRs may send codes with or without dots. CancerCodeRegistry
    # normalizes by stripping dots, so the crosswalk should support both.
    # Add dot-free keys for any code that contains a dot.
    expanded = {}
    for code, trees in icd10_to_trees.items():
        expanded[code] = trees
        dot_free = code.replace(".", "")
        if dot_free != code:
            expanded[dot_free] = trees

    print(f"    After dot-free expansion: {len(expanded):,} entries")

    # Convert sets to sorted lists for JSON serialization
    icd10_serializable = {
        code: sorted(trees)
        for code, trees in expanded.items()
    }

    # --- Save to JSON ---
    output_path = Path(output_dir)
    crosswalk_path = output_path / "icd10_to_mesh_trees.json"

    with open(crosswalk_path, "w") as f:
        json.dump(icd10_serializable, f, indent=2)
    print(f"  Saved: {crosswalk_path} ({len(icd10_serializable):,} entries)")

    return icd10_serializable


def build_umls_synonym_crosswalk(mrconso_path: str, output_dir: str,
                                  mesh_uid_to_trees: dict,
                                  name_to_trees: dict) -> dict:
    """
    Parse UMLS MRCONSO and build a comprehensive synonym-to-MeSH-C04-trees crosswalk.

    Purpose: Map common clinical cancer names ("prostate cancer", "lung adenocarcinoma",
    "NSCLC", "colon cancer") to their correct MeSH C04 tree numbers. This is the
    production-grade fix for the fuzzy matching failure where "prostate cancer"
    resolves to "Hereditary Breast and Ovarian Cancer Syndrome" instead of
    "Prostatic Neoplasms".

    How it works:
      Pass 1 -- Build CUI-to-C04-trees mapping from SAB=MSH rows.
                Identical logic to build_snomed_to_mesh_crosswalk Pass 1.
                CUI bridge: MSH CODE (descriptor UID) -> mesh_uid_to_trees.
      Pass 2 -- For every CUI that has C04 trees, collect ALL English-language
                STR values from ALL source vocabularies (NCI, SNOMED, ICD-10,
                ICD-O-3, OMIM, HPO, common names, etc.).
                This captures every synonym UMLS knows for each cancer concept.

    Post-processing:
      - Lowercase all synonyms
      - Strip parenthetical suffixes: "(disorder)", "(finding)", "(morphologic
        abnormality)", "(situation)", "(body structure)" -- SNOMED artifacts that
        would never appear in EHR display text or TREC topics
      - Skip synonyms shorter than 4 characters (too ambiguous: "ca", "met")
      - Skip synonyms that are pure digits or single words under 5 chars
      - Skip synonyms already present as keys in name_to_trees (MeSH descriptor
        names are already handled by direct lookup, no need to duplicate)
      - Merge tree numbers when multiple CUIs map to the same synonym string

    MRCONSO_2025AB.RRF format (pipe-delimited, 18 columns):
      CUI|LAT|TS|LUI|STT|SUI|ISPREF|AUI|SAUI|SCUI|SDUI|SAB|TTY|CODE|STR|SRL|SUPPRESS|CVF

    Key columns (0-indexed):
      0  = CUI  (Concept Unique Identifier)
      1  = LAT  (Language: ENG, SPA, etc.)
      11 = SAB  (Source vocabulary)
      13 = CODE (Code in that vocabulary)
      14 = STR  (String name -- the synonym text)
      16 = SUPPRESS (O=obsolete, E=suppressible, Y=suppressed, N=active)

    Args:
        mrconso_path:      Path to MRCONSO_2025AB.RRF
        output_dir:        Directory to write JSON file
        mesh_uid_to_trees: {descriptor_ui: [tree_numbers]} from build_mesh_lookup()
        name_to_trees:     {mesh_name_lower: [tree_numbers]} from build_mesh_lookup()
                           Used to skip synonyms that duplicate existing MeSH names.

    Returns:
        dict: {synonym_lower: [tree_numbers_sorted]}

    Output file:
        umls_synonym_to_mesh_trees.json in output_dir
    """
    print("\nBuilding UMLS synonym-to-MeSH crosswalk from MRCONSO_2025AB.RRF...")
    print(f"  Source: {mrconso_path}")

    # --- Pass 1: Build CUI -> MeSH C04 tree numbers mapping ---
    # Same CUI bridge as SNOMED and ICD-10 crosswalks.
    cui_to_trees = defaultdict(set)

    print("  Pass 1: Extracting CUI -> MeSH C04 mappings (via descriptor UID)...")
    mesh_rows = 0
    matched_rows = 0

    with open(mrconso_path, "r", encoding="utf-8") as f:
        for line in f:
            fields = line.rstrip("\n").split("|")
            if len(fields) < 15:
                continue

            sab = fields[11]
            if sab != "MSH":
                continue

            mesh_rows += 1
            cui = fields[0]
            mesh_code = fields[13]

            if mesh_code in mesh_uid_to_trees:
                matched_rows += 1
                cui_to_trees[cui].update(mesh_uid_to_trees[mesh_code])

    print(f"    MSH rows scanned: {mesh_rows:,}")
    print(f"    MSH rows matched to C04: {matched_rows:,}")
    print(f"    CUIs with C04 trees: {len(cui_to_trees):,}")

    # --- Pass 2: Collect ALL English synonyms for cancer CUIs ---
    # For every row in MRCONSO where the CUI has C04 trees and the
    # language is English, capture the STR field as a synonym.
    # This spans ALL source vocabularies (NCI, SNOMED, ICD-10, ICD-O,
    # OMIM, common names, etc.).
    print("  Pass 2: Collecting English synonyms for cancer CUIs (all vocabularies)...")
    synonym_to_trees = defaultdict(set)
    total_rows_scanned = 0
    synonym_hits = 0

    # Parenthetical suffixes to strip (SNOMED/NCI artifacts)
    _PAREN_STRIP = re.compile(
        r"\s*\("
        r"(?:disorder|finding|morphologic abnormality|situation|body structure|"
        r"clinical finding|observable entity|qualifier value|cell structure|"
        r"procedure|substance|event|context-dependent category|"
        r"navigational concept|assessment scale)"
        r"\)\s*$",
        re.IGNORECASE,
    )

    with open(mrconso_path, "r", encoding="utf-8") as f:
        for line in f:
            fields = line.rstrip("\n").split("|")
            if len(fields) < 17:
                continue

            total_rows_scanned += 1

            # English only
            lat = fields[1]
            if lat != "ENG":
                continue

            cui = fields[0]
            if cui not in cui_to_trees:
                continue

            # Skip suppressed/obsolete entries
            suppress = fields[16]
            if suppress in ("O", "Y"):
                continue

            raw_str = fields[14].strip()
            if not raw_str:
                continue

            # Clean: strip parenthetical SNOMED suffixes
            cleaned = _PAREN_STRIP.sub("", raw_str).strip()
            if not cleaned:
                continue

            synonym_lower = cleaned.lower()

            # Skip too-short synonyms (ambiguous: "ca", "met", "aml")
            if len(synonym_lower) < 4:
                continue

            # Skip single words under 5 chars (too ambiguous)
            words = synonym_lower.split()
            if len(words) == 1 and len(synonym_lower) < 5:
                continue

            # Skip pure digits
            if synonym_lower.replace(" ", "").isdigit():
                continue

            synonym_to_trees[synonym_lower].update(cui_to_trees[cui])
            synonym_hits += 1

    print(f"    Total MRCONSO rows scanned: {total_rows_scanned:,}")
    print(f"    Synonym-tree associations found: {synonym_hits:,}")
    print(f"    Unique synonyms (before filtering): {len(synonym_to_trees):,}")

    # --- Post-processing: remove synonyms already in name_to_trees ---
    # These are MeSH descriptor names already handled by direct lookup.
    # Keeping them would be harmless but wastes memory and clutters the file.
    name_to_trees_keys = set(name_to_trees.keys())
    removed_duplicates = 0
    for key in list(synonym_to_trees.keys()):
        if key in name_to_trees_keys:
            del synonym_to_trees[key]
            removed_duplicates += 1

    print(f"    Removed {removed_duplicates:,} synonyms already in MeSH name_to_trees")
    print(f"    Final unique synonyms: {len(synonym_to_trees):,}")

    # --- Spot-check: verify critical cancer names resolved correctly ---
    _SPOT_CHECKS = {
        "prostate cancer":       "C04.588.945",     # prefix of Prostatic Neoplasms tree
        "lung cancer":           "C04.588.894.797",  # prefix of Lung Neoplasms tree
        "breast cancer":         "C04.588.274",      # prefix of Breast Neoplasms tree
        "colon cancer":          "C04.588.274.476",  # prefix of Colonic Neoplasms tree
        "gastric cancer":        "C04.588.274.476",  # Stomach is under Digestive -- will vary
        "melanoma":              "C04.557",           # prefix of Neoplasms by Histological Type
        "cervical cancer":       "C04.588.945",      # prefix of Urogenital Neoplasms tree
        "ovarian cancer":        "C04.588.322",       # prefix of Ovarian Neoplasms tree
        "bladder cancer":        "C04.588.945",      # prefix of Urinary Bladder Neoplasms
        "non-small cell lung cancer": "C04.588.894", # prefix of Lung Neoplasms tree
        "cholangiocarcinoma":    "C04.588",           # prefix for bile duct/liver
    }

    print("\n  --- Spot Check (critical cancer synonyms) ---")
    spot_pass = 0
    spot_fail = 0
    for term, expected_prefix in _SPOT_CHECKS.items():
        trees = synonym_to_trees.get(term, set())
        if trees:
            has_match = any(t.startswith(expected_prefix) for t in trees)
            status = "OK" if has_match else "TREE MISMATCH"
            if has_match:
                spot_pass += 1
            else:
                spot_fail += 1
            sample_tree = sorted(trees)[0]
            print(f"    '{term}' -> {len(trees)} trees, sample: {sample_tree} [{status}]")
        else:
            spot_fail += 1
            print(f"    '{term}' -> NOT FOUND [MISSING]")

    print(f"  Spot check: {spot_pass} passed, {spot_fail} failed out of {len(_SPOT_CHECKS)}")

    # Convert sets to sorted lists for JSON serialization
    synonym_serializable = {
        synonym: sorted(trees)
        for synonym, trees in synonym_to_trees.items()
    }

    # --- Save to JSON ---
    output_path = Path(output_dir)
    crosswalk_path = output_path / "umls_synonym_to_mesh_trees.json"

    with open(crosswalk_path, "w") as f:
        json.dump(synonym_serializable, f, indent=2)
    print(f"\n  Saved: {crosswalk_path} ({len(synonym_serializable):,} entries)")

    return synonym_serializable


def build_all_lookups(mesh_xml_path: str, mrconso_path: str, output_dir: str):
    """
    One-shot builder: parse MeSH XML + MRCONSO_2025AB.RRF -> JSON lookup files.

    Produces:
      - mesh_c04_lookup.json       (MeSH C04 descriptor names -> tree numbers)
      - mesh_tree_to_name.json     (tree number -> descriptor name)
      - mesh_uid_to_trees.json     (descriptor UID -> tree numbers)
      - snomed_to_mesh_trees.json  (SNOMED code -> C04 tree numbers)
      - icd10_to_mesh_trees.json   (ICD-10-CM code -> C04 tree numbers)

    Run once after downloading data. JSON files are loaded at startup
    by MeSHCancerFilter via load_mesh_filter().

    Args:
        mesh_xml_path: Path to desc2026.xml
        mrconso_path:  Path to MRCONSO_2025AB.RRF
        output_dir:    Directory to write JSON files (data_MeSH_path)
    """
    print("=" * 60)
    print("  MeSH Cancer Filter: Building Lookup Files")
    print("=" * 60)

    # Step 1: MeSH hierarchy
    mesh_data = build_mesh_lookup(mesh_xml_path, output_dir)

    # Step 2: SNOMED crosswalk (needs uid_to_trees from step 1)
    build_snomed_to_mesh_crosswalk(
        mrconso_path, output_dir,
        mesh_uid_to_trees=mesh_data["uid_to_trees"]
    )

    # Step 3: ICD-10-CM crosswalk (needs uid_to_trees from step 1)
    build_icd10_to_mesh_crosswalk(
        mrconso_path, output_dir,
        mesh_uid_to_trees=mesh_data["uid_to_trees"]
    )

    # Step 4: UMLS synonym crosswalk (needs uid_to_trees + name_to_trees from step 1)
    build_umls_synonym_crosswalk(
        mrconso_path, output_dir,
        mesh_uid_to_trees=mesh_data["uid_to_trees"],
        name_to_trees=mesh_data["name_to_trees"],
    )

    print(f"\n{'=' * 60}")
    print("  All lookup files built successfully!")
    print(f"{'=' * 60}\n")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 20 2026

@author: ramyalsaffar
"""
