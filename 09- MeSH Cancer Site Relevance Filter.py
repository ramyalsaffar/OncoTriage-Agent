# MeSH Cancer Site Relevance Filter
###################################


#------------------------------------------------------------------------------


"""
MeSH Cancer Site Relevance Filter
==================================
Production-grade cancer site filter using the MeSH (Medical Subject Headings)
neoplasm hierarchy to prevent irrelevant trials from reaching GPT-4o.

Problem: After age/sex filtering, a breast cancer patient can still have
prostate cancer trials sent to GPT-4o. This wastes tokens and dilutes
the match pool.

Solution: Use the official MeSH tree hierarchy (C04 = Neoplasms) to
determine whether a trial's target cancer is anatomically related to
the patient's cancer diagnosis.

Architecture — two-layer patient mapping (Option C from design):
  Layer 1 — SNOMED-to-MeSH crosswalk via UMLS MRCONSO_2025AB.RRF (gold standard)
            Uses CUI bridge: SNOMED code → CUI → MeSH descriptor name
  Layer 2 — Fuzzy string matching against MeSH descriptor names (fallback)
            Fires only when SNOMED code is absent or not in UMLS

Trial side: Direct lookup — ClinicalTrials.gov conditions ARE MeSH terms.

Filter logic:
  - Extract patient MeSH tree numbers (via Layer 1 or 2)
  - Extract trial MeSH tree numbers (direct lookup)
  - If ANY shared ancestry (tree number prefix match) → KEEP
  - If trial is pan-cancer (tree number = C04 with depth ≤ 2) → KEEP
  - If either side unmappable → KEEP (conservative)
  - Otherwise → FILTER OUT

Data files (generated once by build_all_lookups()):
  - mesh_c04_lookup.json      : {descriptor_name_lower: [tree_numbers]}
  - mesh_tree_to_name.json    : {tree_number: descriptor_name}
  - mesh_uid_to_trees.json    : {descriptor_ui: [tree_numbers]}
  - snomed_to_mesh_trees.json : {snomed_code: [tree_numbers]}

Standards: MeSH 2026, UMLS Metathesaurus, SNOMED CT US Edition
Works with: Synthea FHIR bundles + real EHR data
"""


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
                 synonym_to_trees: dict = None):
        """
        Args:
            name_to_trees:    {mesh_name_lower: [tree_numbers]}
            tree_to_name:     {tree_number: mesh_name}
            snomed_to_trees:  {snomed_code: [tree_numbers]}
            icd10_to_trees:   {icd10_code: [tree_numbers]} (optional)
            synonym_to_trees: {synonym_lower: [tree_numbers]} (optional, UMLS crosswalk)
        """
        self.name_to_trees    = name_to_trees    # lowercase keys
        self.tree_to_name     = tree_to_name
        self.snomed_to_trees  = snomed_to_trees
        self.icd10_to_trees   = icd10_to_trees or {}
        self.synonym_to_trees = synonym_to_trees or {}

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
        print(f"MeSHCancerFilter loaded: {len(self.name_to_trees):,} C04 descriptors, "
              f"{len(self.snomed_to_trees):,} SNOMED crosswalk entries, "
              f"{icd10_status}, {synonym_status}")

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


def load_mesh_filter() -> MeSHCancerFilter:
    """
    Load pre-built MeSH lookup files and return a MeSHCancerFilter instance.

    Required (from desc2026.xml -- enables fuzzy string matching):
      - mesh_c04_lookup.json
      - mesh_tree_to_name.json

    Optional (from MRCONSO_2025AB.RRF -- enables code-based crosswalks):
      - snomed_to_mesh_trees.json  (SNOMED crosswalk, Layer 1)
      - icd10_to_mesh_trees.json   (ICD-10-CM crosswalk, Layer 2, built by Item 2.1)

    If required files are missing -> returns None (filter disabled).
    If crosswalk files are missing -> loads without them (fuzzy matching only).
    """
    mesh_dir = Path(data_MeSH_path)

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
        print("WARNING: MeSH Cancer Filter core files not found:")
        for m in missing:
            print(f"  - {m}")
        print("\nRun build_mesh_lookup() first (requires desc2026.xml).")
        print("The cancer site filter will be DISABLED (all trials pass).\n")
        return None

    print("Loading MeSH Cancer Filter...")

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
        print("  NOTE: snomed_to_mesh_trees.json not found -- SNOMED crosswalk disabled.")

    # Optional: ICD-10-CM crosswalk (Layer 2)
    icd10_to_trees = {}
    if icd10_xwalk_path.exists():
        with open(icd10_xwalk_path, "r") as f:
            icd10_to_trees = json.load(f)
    else:
        print("  NOTE: icd10_to_mesh_trees.json not found -- ICD-10 crosswalk disabled.")
        print("  To enable: run build_icd10_to_mesh_crosswalk() (Item 2.1).")

    # Optional: UMLS synonym crosswalk (Strategy 0 in fuzzy matching)
    synonym_xwalk_path = mesh_dir / "umls_synonym_to_mesh_trees.json"
    synonym_to_trees = {}
    if synonym_xwalk_path.exists():
        with open(synonym_xwalk_path, "r") as f:
            synonym_to_trees = json.load(f)
    else:
        print("  NOTE: umls_synonym_to_mesh_trees.json not found -- UMLS synonym crosswalk disabled.")
        print("  To enable: run build_umls_synonym_crosswalk() (Item 1).")

    if not snomed_to_trees and not icd10_to_trees and not synonym_to_trees:
        print("  Filter will use fuzzy string matching only.")

    return MeSHCancerFilter(name_to_trees, tree_to_name, snomed_to_trees,
                            icd10_to_trees, synonym_to_trees)


# ===========================================================================
# MAIN: Build lookup files from raw data
# ===========================================================================


if __name__ == "__main__":

    # Paths — uses project path convention from 01- Imports.py
    _code_dir = "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code/"

    for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
        with open(_code_dir + _bootstrap) as _fh:
            exec(_fh.read(), globals())

    exec_chain(
        ["03- Config.py"],
        caller_file=_code_dir + "09- MeSH Cancer Site Relevance Filter.py",
        caller_globals=globals(),
        chain_label="01 → 02 → 03",
    )

    # --- Locate raw data files ---
    mesh_xml = str(Path(data_MeSH_path) / "desc2026.xml")
    mrconso  = str(Path(data_MeSH_path) / "MRCONSO_2025AB.RRF")

    if not Path(mesh_xml).exists():
        print(f"ERROR: MeSH XML not found at {mesh_xml}")
        print("Download from: https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/desc2026.gz")
        sys.exit(1)

    # --- Step 1: Always build MeSH hierarchy (only needs desc2026.xml) ---
    mesh_data = build_mesh_lookup(mesh_xml, output_dir=data_MeSH_path)

    # --- Step 2: Build crosswalks only if MRCONSO available ---
    if Path(mrconso).exists():
        
        build_snomed_to_mesh_crosswalk(
            mrconso, data_MeSH_path,
            mesh_uid_to_trees=mesh_data["uid_to_trees"]
        )
        
        build_icd10_to_mesh_crosswalk(
            mrconso, data_MeSH_path,
            mesh_uid_to_trees=mesh_data["uid_to_trees"]
        )
        
        build_umls_synonym_crosswalk(
            mrconso, data_MeSH_path,
            mesh_uid_to_trees=mesh_data["uid_to_trees"],
            name_to_trees=mesh_data["name_to_trees"],
        )
        
    else:
        print(f"\nNOTE: MRCONSO_2025AB.RRF not found at {mrconso}")
        print("SNOMED and ICD-10 crosswalks will not be built.")
        print("Filter will use fuzzy string matching only.")
        print("To enable crosswalks: download MRCONSO_2025AB.RRF from UMLS and re-run.\n")

    # --- Quick validation ---
    mesh_filter = load_mesh_filter()
    if mesh_filter:
        print("\n--- Quick Validation ---")

        # Test: "Breast Neoplasms" should have C04.588.274
        test_name = "breast neoplasms"
        trees = mesh_filter.name_to_trees.get(test_name, [])
        print(f"  '{test_name}' → trees: {trees}")

        # Test: "Colonic Neoplasms" should have C04.588.274.476
        test_name2 = "colonic neoplasms"
        trees2 = mesh_filter.name_to_trees.get(test_name2, [])
        print(f"  '{test_name2}' → trees: {trees2}")

        # Test ancestry: breast and colonic should NOT be related
        if trees and trees2:
            related = any(
                t1.startswith(t2) or t2.startswith(t1)
                for t1 in trees for t2 in trees2
            )
            print(f"  Breast ↔ Colonic related? {related} (expected: False)")

        print("\n✓ MeSH Cancer Filter ready for use.")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 20 2026

@author: ramyalsaffar
"""