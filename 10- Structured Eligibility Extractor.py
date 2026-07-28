"""
Structured Eligibility Extractor
================================

Deterministic, rule-based parser that extracts structured cancer stage
requirements from clinical trial eligibility criteria at INDEX TIME.

Architecture follows CriteriaMapper (2024) and Yale CTPM (2026):
  INDEX TIME:  inclusion_criteria → sentence split → NER → structured dict
  QUERY TIME:  integer comparison in Stage 4 (zero cost)

Design principles:
  1. DETERMINISTIC — no LLM, no API calls, no randomness
  2. CONSERVATIVE — unknown → None → trial passes through unfiltered
  3. MULTI-PASS — title, inclusion criteria, exclusion criteria (negation-aware)
  4. REAL-WORLD READY — handles all natural language variations observed in
     352K+ ClinicalTrials.gov entries per Criteria2Query/CTKB corpus analysis

Integration:
  File 11 (index time): call enrich_structured_eligibility(trial) per trial
  File 13 (query time): call extract_patient_stage() + is_stage_mismatch()
"""


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE ORDINAL MAPPING
# ═══════════════════════════════════════════════════════════════════════════════
# Maps all stage text variants to integer ordinal 0–4.

_STAGE_ORDINAL: Dict[str, int] = {
    # Stage 0 (in-situ)
    "0": 0,
    # Stage I  (all substages → 1)
    "i": 1, "1": 1,
    "ia": 1, "ia1": 1, "ia2": 1, "ia3": 1,
    "ib": 1, "ib1": 1, "ib2": 1, "ic": 1,
    # Stage II (all substages → 2)
    "ii": 2, "2": 2,
    "iia": 2, "iia1": 2, "iia2": 2,
    "iib": 2, "iic": 2,
    # Stage III (all substages → 3)
    "iii": 3, "3": 3,
    "iiia": 3, "iiib": 3, "iiic": 3,
    "iiic1": 3, "iiic2": 3,
    # Stage IV (all substages → 4)
    "iv": 4, "4": 4,
    "iva": 4, "ivb": 4, "ivc": 4,
}

# Regex alternation for stage values — longest first to prevent partial match.
_STAGE_ALT = (
    r"iii[abc][12]?|ii[abc][12]?|iv[abc]?|i[abc][123]?|"
    r"iii|ii|iv|i|"
    r"[0-4]"
)

# ═══════════════════════════════════════════════════════════════════════════════
# TRIAL-SIDE EXTRACTION (index time)
# ═══════════════════════════════════════════════════════════════════════════════

# --- Pattern 1: Stage range ---
# "Stage IIA to IIIB", "Stages IB-III", "stage II, III, or IV",
# "stage II/III", "Stage IIA–IIIB"
_RANGE_RE = re.compile(
    r"\bstages?\s+"
    r"(" + _STAGE_ALT + r")"                         # lower bound (group 1)
    r"\s*(?:to|-|–|—|through|or|/|,\s*(?:or\s+)?)\s*"  # separator
    r"(?:stage\s+)?"                                  # optional repeated "stage"
    r"(" + _STAGE_ALT + r")"                          # upper bound (group 2)
    r"(?![a-z])",                                     # not followed by letter
    re.IGNORECASE,
)

# --- Pattern 2: Single stage ---
# "Stage III", "stage IV", "Stage 2"
_SINGLE_RE = re.compile(
    r"\bstages?\s+(" + _STAGE_ALT + r")(?![a-z])",
    re.IGNORECASE,
)

# --- Pattern 3: Metastatic keyword ---
# "metastatic" as standalone descriptor.
# Must NOT be preceded by "non-" or "non " (non-metastatic = opposite).
_METASTATIC_RE = re.compile(
    r"(?<!\bnon[- ])\bmetastatic\b",
    re.IGNORECASE,
)

# Detect explicit "non-metastatic"
_NON_METASTATIC_RE = re.compile(
    r"\bnon[- ]metastatic\b",
    re.IGNORECASE,
)

# --- Pattern 4: Locally advanced keyword ---
_LOCALLY_ADVANCED_RE = re.compile(
    r"\blocally[- ]+advanced\b",
    re.IGNORECASE,
)

# --- Negation context patterns ---
# Detects when a stage mention sits inside a NEGATIVE context
# (exclusion phrase or negated sentence), meaning it describes
# who is EXCLUDED, not the target population.
_NEGATION_PREFIXES = re.compile(
    r"(?:no\s+prior|no\s+history|no\s+previous|without\b|"
    r"must\s+not\s+have|should\s+not\s+have|"
    r"exclude[ds]?\b|excluding\b|ruled\s+out|"
    r"absence\s+of|free\s+of|"
    r"other\s+than|except\s+for)",
    re.IGNORECASE,
)


def _is_negated(text: str, match_start: int) -> bool:
    """
    Check if a regex match position is preceded by a negation phrase
    within the SAME clause/sentence.

    Uses a two-layer approach:
      1. Look back max 80 chars for a negation keyword.
      2. If found, verify there is no sentence/clause boundary
         (period, semicolon, comma-space, "in Participants") between
         the negation and the match — prevents false positives from
         distant unrelated clauses like "With or Without MK-2870 in
         Participants With Resectable Stage II..."
    """
    window_start = max(0, match_start - 80)
    prefix = text[window_start:match_start]

    neg_match = _NEGATION_PREFIXES.search(prefix)
    if not neg_match:
        return False

    # Text between negation keyword and the stage mention
    between = prefix[neg_match.end():]

    # If there's a clause boundary between negation and stage, not negated
    clause_boundaries = re.compile(r"[.;]|\.\s|\bin\s+(?:patients?|participants?|subjects?)\b", re.IGNORECASE)
    if clause_boundaries.search(between):
        return False

    return True


def _extract_stage_from_text(text: str) -> Optional[Tuple[int, int]]:
    """
    Extract (min_stage, max_stage) from a block of text.
    Returns None if nothing found.  Skips negated mentions.
    """
    if not text or not text.strip():
        return None

    # --- Pass 1: Stage range / list ---
    # Collect ALL non-negated stage mentions, then take min/max
    all_ordinals = []
    for m in _RANGE_RE.finditer(text):
        if _is_negated(text, m.start()):
            continue
        lo = _STAGE_ORDINAL.get(m.group(1).lower())
        hi = _STAGE_ORDINAL.get(m.group(2).lower())
        if lo is not None:
            all_ordinals.append(lo)
        if hi is not None:
            all_ordinals.append(hi)

    # Also check single-stage mentions that may follow the range pattern
    # (e.g., the "IV" in "stage II, III, or IV" that the range regex missed)
    for m in _SINGLE_RE.finditer(text):
        if _is_negated(text, m.start()):
            continue
        val = _STAGE_ORDINAL.get(m.group(1).lower())
        if val is not None:
            all_ordinals.append(val)

    if all_ordinals:
        return (min(all_ordinals), max(all_ordinals))

    # --- Pass 2: Single stage (only if no ordinals found in Pass 1) ---
    if not all_ordinals:
        for m in _SINGLE_RE.finditer(text):
            if _is_negated(text, m.start()):
                continue
            stage = _STAGE_ORDINAL.get(m.group(1).lower())
            if stage is not None:
                return (stage, stage)

    # --- Pass 3: Metastatic keyword (not non-metastatic) ---
    if _NON_METASTATIC_RE.search(text):
        # "Non-metastatic" → don't set stage=4 even if "metastatic" also matches
        pass
    else:
        for m in _METASTATIC_RE.finditer(text):
            if _is_negated(text, m.start()):
                continue
            return (4, 4)

    # --- Pass 4: Locally advanced ---
    for m in _LOCALLY_ADVANCED_RE.finditer(text):
        if _is_negated(text, m.start()):
            continue
        return (3, 4)

    return None


def _extract_accepts_metastatic(
    title: str,
    inclusion: str,
    exclusion: str,
) -> Optional[bool]:
    """
    Determine if trial accepts metastatic patients.
    Returns True / False / None (unknown).
    """
    # Check exclusion for metastatic rejection using same negation patterns
    # as stage extraction — no hardcoded phrase list.
    if exclusion:
        for m in _METASTATIC_RE.finditer(exclusion):
            if not _is_negated(exclusion, m.start()):
                return False

        # Also catch "non-metastatic" in exclusion (redundant but explicit)
        if _NON_METASTATIC_RE.search(exclusion):
            return False

    # Check title + inclusion for positive metastatic signal
    combined = f"{title} {inclusion}".lower()
    if _NON_METASTATIC_RE.search(combined):
        return False
    if _METASTATIC_RE.search(combined):
        return True

    return None


def enrich_structured_eligibility(trial: Dict) -> Dict:
    """
    Extract structured stage requirements from a trial dict and store as
    trial["structured_eligibility"].

    Called at index time (File 11) for each trial after parse_trial_metadata().

    Multi-pass extraction (ordered by confidence):
      1. Title — most curated, highest signal
      2. Inclusion criteria — detailed but noisier

    First pass that returns a result wins.  If all return None,
    structured_eligibility is set to all-None values.
    The query-time filter will keep the trial (conservative).

    Args:
        trial: Trial dict from parse_trial_metadata(). Modified in-place.

    Returns:
        The same trial dict with "structured_eligibility" key added.
    """
    title     = trial.get("title") or ""
    inclusion = trial.get("eligibility", {}).get("inclusion_criteria") or ""
    exclusion = trial.get("eligibility", {}).get("exclusion_criteria") or ""

    # --- Stage extraction: title → inclusion (first hit wins) ---
    stage_result = _extract_stage_from_text(title)
    if stage_result is None:
        stage_result = _extract_stage_from_text(inclusion)

    min_stage = stage_result[0] if stage_result else None
    max_stage = stage_result[1] if stage_result else None

    # --- Metastatic acceptance ---
    accepts_metastatic = _extract_accepts_metastatic(title, inclusion, exclusion)

    trial["structured_eligibility"] = {
        "min_stage":          min_stage,
        "max_stage":          max_stage,
        "accepts_metastatic": accepts_metastatic,
    }

    return trial


# ═══════════════════════════════════════════════════════════════════════════════
# PATIENT-SIDE EXTRACTION (query time — File 13)
# ═══════════════════════════════════════════════════════════════════════════════

# Regex to extract stage from ANY SNOMED/EHR display text.
# Matches patterns like "Carcinoma of breast, Stage 3", "Stage IV lung cancer",
# "TNM stage 1 (disorder)", etc. — works for all cancer types, not just breast.
_SNOMED_DISPLAY_STAGE_RE = re.compile(
    r"\bstage\s+(" + _STAGE_ALT + r")(?![a-z])",
    re.IGNORECASE,
)

_PATIENT_STAGE_RE = re.compile(
    r"(?:tnm\s+)?stage\s+(" + _STAGE_ALT + r")(?![a-z])",
    re.IGNORECASE,
)


def extract_patient_stage(
    conditions: List[Dict],
    cancer_stage_observations: Optional[List[Dict]] = None,
) -> Optional[int]:
    """
    Extract patient's cancer stage ordinal (0–4) from FHIR data.

    Tiers (first match wins):
      0. mCODE TNM stage group Observations (LOINC 21908-9/21902-2/21914-7)
         — structured, most reliable, used by mCODE-compliant EHRs (Epic etc.)
         — most recent observation wins when multiple exist.
      1. Condition display text regex — catches "TNM stage 1", "Stage III",
         "Carcinoma of breast, Stage 3" etc. across ALL cancer types.
         Works with Synthea, real EHRs, and any SNOMED display text.
      2. "metastatic" keyword in Condition display (→ 4)

    Returns ordinal 0–4 or None.  None → stage filter keeps all trials.
    """
    # Tier 0: mCODE TNM stage group Observations — structured, highest priority
    if cancer_stage_observations:
        # Sort most recent first; multiple staging events may exist (restaging)
        sorted_obs = sorted(
            cancer_stage_observations,
            key=lambda o: o.get('date') or '0000-00-00',
            reverse=True
        )
        for obs in sorted_obs:
            display = obs.get('stage_display') or ''
            # Try display text regex first (e.g. "Stage IIIA", "IV")
            m = _SNOMED_DISPLAY_STAGE_RE.search(display)
            if m:
                ordinal = _STAGE_ORDINAL.get(m.group(1).lower())
                if ordinal is not None:
                    return ordinal
            # Fallback: "metastatic" in display
            if 'metastatic' in display.lower() and 'non-metastatic' not in display.lower():
                return 4

    # Tier 1: Condition display text regex (covers all cancer types, all SNOMED displays)
    for cond in conditions:
        display = cond.get("display") or ""
        m = _SNOMED_DISPLAY_STAGE_RE.search(display)
        if m:
            ordinal = _STAGE_ORDINAL.get(m.group(1).lower())
            if ordinal is not None:
                return ordinal

    # Tier 2: Metastatic keyword in Condition display
    for cond in conditions:
        display = (cond.get("display") or "").lower()
        if "metastatic" in display and "non-metastatic" not in display:
            return 4

    return None


def is_stage_mismatch(patient_stage: Optional[int], trial: Dict) -> bool:
    """
    Returns True if trial should be DROPPED due to stage mismatch.

    Logic (conservative — defaults to KEEP):
      - patient_stage is None            → False (unknown patient → keep)
      - structured_eligibility absent    → False (no data → keep)
      - min_stage is None                → False (trial unspecified → keep)
      - patient_stage < min_stage        → True  (patient too early → drop)
      - patient_stage > max_stage        → True  (patient too late → drop)
      - otherwise                        → False (within range → keep)
    """
    if patient_stage is None:
        return False

    se = trial.get("structured_eligibility")
    if not se:
        return False

    min_s = se.get("min_stage")
    max_s = se.get("max_stage")

    if min_s is not None and patient_stage < min_s:
        return True

    if max_s is not None and patient_stage > max_s:
        return True

    return False


#------------------------------------------------------------------------------

# ══════════════════════════════════════════════════════════════════════════
# HISTOLOGY MISMATCH FILTER
# ══════════════════════════════════════════════════════════════════════════
# Deterministic, rule-based filter that detects when a trial's required
# cancer histology conflicts with the patient's diagnosed cancer histology.
#
# INDEX TIME (File 11): enrich_histology_tags(trial)
# QUERY TIME (File 13): extract_patient_histology() + is_histology_mismatch()
"""
Histology Mismatch Filter
=========================

Deterministic, rule-based filter that detects when a trial's required
cancer histology conflicts with the patient's diagnosed cancer histology.

Examples of mismatches this filter catches:
  - NSCLC patient ↔ Small Cell Lung Cancer trial
  - NSCLC patient ↔ Neuroendocrine Carcinoma trial
  - NSCLC patient ↔ Tracheal Squamous Cell Carcinoma trial
  - Adenocarcinoma patient ↔ Squamous Cell Carcinoma trial (same site)
  - Any cancer patient ↔ trial for a fundamentally different histology

Architecture (mirrors stage filter):
  INDEX TIME (File 11): extract trial_histology_tags from title + criteria
  QUERY TIME (File 13): extract patient_histology_tags from conditions
                         → compare → drop on conflict

Design principles:
  1. DETERMINISTIC — no LLM, no API calls
  2. CONSERVATIVE — unknown → None → trial passes through unfiltered
  3. GENERAL — works for ALL cancer types, not hardcoded to lung/breast/colon
  4. NO HARDCODED LISTS — all extraction via regex pattern matching
  5. CONFLICT-BASED — only drops when there's a POSITIVE conflict signal
     (both sides have info AND it conflicts)

Integration:
  File 11 (index time): call enrich_histology_tags(trial) per trial
  File 13 (query time): call extract_patient_histology() + is_histology_mismatch()
"""


# ══════════════════════════════════════════════════════════════════════════
# HISTOLOGY EXTRACTION PATTERNS
# ══════════════════════════════════════════════════════════════════════════
#
# Cancer histology has two independent axes:
#   1. BROAD TYPE: small cell vs non-small cell, neuroendocrine vs carcinoma
#   2. SPECIFIC SUBTYPE: squamous vs adenocarcinoma, ductal vs lobular
#
# We extract NORMALIZED TAGS from text, then check for conflicts.
# A conflict occurs when:
#   - Patient has tag X, trial REQUIRES tag Y, and X/Y are mutually exclusive
#
# Tags are hierarchical strings like:
#   "nsclc", "sclc", "squamous", "adenocarcinoma", "neuroendocrine"
#
# The key insight: we don't need to enumerate all histologies.
# We just need to detect MUTUALLY EXCLUSIVE pairs.


# --- Pattern: Non-small cell vs Small cell ---
# This is the single biggest source of histology mismatch in the data.
# "Non-small cell" and "small cell" are FUNDAMENTALLY different diseases
# with completely different treatments, prognosis, and biology.
#
# CRITICAL REGEX DESIGN:
#   - "non-small cell" → tag "nsclc" (when in lung context)
#   - "small cell" WITHOUT "non-" prefix → tag "sclc" (when in lung context)
#   - Must handle: "Non-Small Cell", "NSCLC", "non-small-cell",
#     "Non small cell", "non‐small cell" (Unicode hyphen)
#   - Must NOT match: "non-small cell" as "small cell"

# Detect "non-small cell" (all variations) — must check BEFORE "small cell"
_NON_SMALL_CELL_RE = re.compile(
    r"\bnon[\s\-\u2010\u2011\u2012\u2013]?small[\s\-\u2010\u2011\u2012\u2013]?cell\b",
    re.IGNORECASE,
)

# Detect "NSCLC" abbreviation
_NSCLC_ABBREV_RE = re.compile(r"\bNSCLC\b")

# Detect "small cell" that is NOT preceded by "non-"
# Uses negative lookbehind for "non" + separator
_SMALL_CELL_RE = re.compile(
    r"(?<!\bnon[\s\-\u2010\u2011\u2012\u2013])"  # not preceded by "non-"
    r"\bsmall[\s\-]?cell\b",
    re.IGNORECASE,
)

# Detect "SCLC" abbreviation (but NOT "NSCLC")
_SCLC_ABBREV_RE = re.compile(r"(?<!\bN)\bSCLC\b")

# --- Pattern: Neuroendocrine ---
# Neuroendocrine tumors/carcinomas are distinct from carcinomas.
# "neuroendocrine" in a trial title = different disease from NSCLC/adenocarcinoma.
_NEUROENDOCRINE_RE = re.compile(
    r"\bneuro[\s\-]?endocrine\b",
    re.IGNORECASE,
)

# --- Pattern: Squamous vs Adenocarcinoma ---
# Within a given cancer site, squamous cell carcinoma and adenocarcinoma
# are different histologies. Some trials target one specifically.
_SQUAMOUS_RE = re.compile(
    r"\bsquamous\s+cell\b|\bsquamous\b",
    re.IGNORECASE,
)

_ADENOCARCINOMA_RE = re.compile(
    r"\badenocarcinoma\b",
    re.IGNORECASE,
)

# --- Pattern: Tracheal ---
# Tracheal cancer is anatomically distinct from lung cancer.
# A "tracheal carcinoma" trial should not match a lung cancer patient.
_TRACHEAL_RE = re.compile(
    r"\btracheal\b",
    re.IGNORECASE,
)

# --- Pattern: Lung context ---
# We need to know if "small cell" / "non-small cell" is about LUNG cancer.
# In nearly all clinical contexts, "small cell" without other organ = lung.
# But to be safe, check for lung/pulmonary context.
_LUNG_CONTEXT_RE = re.compile(
    r"\blung\b|\bpulmonary\b|\bNSCLC\b|\bSCLC\b|\bbronch",
    re.IGNORECASE,
)


# ══════════════════════════════════════════════════════════════════════════
# TAG EXTRACTION
# ══════════════════════════════════════════════════════════════════════════

def _extract_histology_tags(text: str) -> Set[str]:
    """
    Extract a set of normalized histology tags from free text.

    Returns tags like: {"nsclc"}, {"sclc"}, {"neuroendocrine"},
    {"squamous"}, {"adenocarcinoma"}, {"tracheal"}, or empty set.

    Multiple tags can co-occur: "squamous non-small cell lung cancer"
    → {"nsclc", "squamous"}
    """
    if not text or not text.strip():
        return set()

    tags = set()
    has_lung = bool(_LUNG_CONTEXT_RE.search(text))

    # --- Non-small cell vs small cell (lung-specific) ---
    has_nsc = bool(_NON_SMALL_CELL_RE.search(text)) or bool(_NSCLC_ABBREV_RE.search(text))
    has_sc = bool(_SMALL_CELL_RE.search(text)) or bool(_SCLC_ABBREV_RE.search(text))

    if has_nsc:
        tags.add("nsclc")
    elif has_sc and has_lung:
        # "Small cell" in lung context → SCLC
        tags.add("sclc")
    elif has_sc and not has_lung:
        # "Small cell" without lung context — could be SCLC (most common),
        # but be conservative: only tag if lung context present or if
        # combined with other lung signals
        # Actually, "small cell lung cancer" is by far the most common
        # "small cell" cancer. But "small cell carcinoma of bladder" exists.
        # Conservative: require lung context for sclc tag.
        pass

    # --- Neuroendocrine ---
    if _NEUROENDOCRINE_RE.search(text):
        tags.add("neuroendocrine")

    # --- Squamous vs Adenocarcinoma ---
    if _SQUAMOUS_RE.search(text):
        tags.add("squamous")

    if _ADENOCARCINOMA_RE.search(text):
        tags.add("adenocarcinoma")

    # --- Tracheal ---
    if _TRACHEAL_RE.search(text):
        tags.add("tracheal")

    return tags


# ══════════════════════════════════════════════════════════════════════════
# CONFLICT DETECTION
# ══════════════════════════════════════════════════════════════════════════
#
# Two tag sets CONFLICT if they contain a MUTUALLY EXCLUSIVE pair.
# Mutual exclusivity is SYMMETRIC and defined by the biology:
#
#   nsclc ↔ sclc           (fundamentally different diseases)
#   nsclc ↔ neuroendocrine (NSCLC is epithelial, NE is neuroendocrine)
#   nsclc ↔ tracheal       (different anatomical origin)
#   sclc ↔ tracheal        (different anatomical origin)
#   squamous ↔ adenocarcinoma (different cell lineage, same site)
#
# IMPORTANT: nsclc and squamous are NOT mutually exclusive!
# NSCLC includes both squamous and adenocarcinoma subtypes.
# A "squamous NSCLC" trial is valid for an NSCLC patient.
#
# Similarly, adenocarcinoma and nsclc are NOT mutually exclusive.

# Define mutually exclusive pairs as frozensets for O(1) lookup
_EXCLUSIVE_PAIRS = {
    frozenset({"nsclc", "sclc"}),
    frozenset({"squamous", "adenocarcinoma"}),
}


def _has_conflict(patient_tags: Set[str], trial_tags: Set[str]) -> bool:
    """
    Check if patient and trial histology tags have a mutually exclusive conflict.

    Returns True if ANY pair (one from patient, one from trial) is exclusive.
    Returns False if no conflict detected.

    Conservative: if either set is empty, no conflict possible → False.
    """
    if not patient_tags or not trial_tags:
        return False

    for p_tag in patient_tags:
        for t_tag in trial_tags:
            if p_tag == t_tag:
                # Same tag = compatible, skip
                continue
            if frozenset({p_tag, t_tag}) in _EXCLUSIVE_PAIRS:
                return True

    return False


# ══════════════════════════════════════════════════════════════════════════
# TRIAL-SIDE EXTRACTION (index time — File 11)
# ══════════════════════════════════════════════════════════════════════════

def enrich_histology_tags(trial: Dict) -> Dict:
    """
    Extract histology tags from trial title + inclusion criteria and store
    as trial["histology_tags"].

    Called at index time (File 11) for each trial after parse_trial_metadata().

    Multi-pass extraction: title first (most curated), then inclusion criteria.
    Tags are UNIONED across passes (not first-hit-wins like stage) because
    histology can appear in either location.

    Conservative: if no histology extracted → empty set → no conflict possible
    → trial passes through at query time.

    Args:
        trial: Trial dict from parse_trial_metadata(). Modified in-place.

    Returns:
        The same trial dict with "histology_tags" key added (set of strings).
    """
    title = trial.get("title") or ""
    inclusion = trial.get("eligibility", {}).get("inclusion_criteria") or ""

    # Union tags from title + inclusion (both may contain useful signals)
    tags = _extract_histology_tags(title)
    tags |= _extract_histology_tags(inclusion)

    # Store as sorted list for JSON serialization (Qdrant payload)
    trial["histology_tags"] = sorted(tags)

    return trial


# ══════════════════════════════════════════════════════════════════════════
# PATIENT-SIDE EXTRACTION (query time — File 13)
# ══════════════════════════════════════════════════════════════════════════

def extract_patient_histology(conditions: List[Dict]) -> Set[str]:
    """
    Extract histology tags from patient FHIR conditions.

    Scans ALL condition display texts and unions the tags.
    Works with Synthea ("Non-small cell carcinoma of lung, TNM stage 1")
    and real EHRs ("Adenocarcinoma of lung", "Squamous cell carcinoma of cervix").

    Returns set of tags or empty set.  Empty → filter keeps all trials.
    """
    tags = set()
    for cond in conditions:
        display = cond.get("display") or ""
        tags |= _extract_histology_tags(display)
    return tags


# ══════════════════════════════════════════════════════════════════════════
# MISMATCH DETECTION (query time — File 13)
# ══════════════════════════════════════════════════════════════════════════

def is_histology_mismatch(patient_tags: Set[str], trial: Dict) -> bool:
    """
    Returns True if trial should be DROPPED due to histology mismatch.

    Logic (conservative — defaults to KEEP):
      - patient_tags is empty               → False (unknown → keep all)
      - trial has no histology_tags key     → False (backward compatible)
      - trial histology_tags is empty       → False (unknown → keep)
      - conflict detected between sets      → True  (mismatch → drop)
      - no conflict                         → False (compatible → keep)

    Args:
        patient_tags: Set from extract_patient_histology()
        trial: Trial dict with optional "histology_tags" key

    Returns:
        True if trial should be dropped, False if it should be kept.
    """
    if not patient_tags:
        return False

    trial_tags_raw = trial.get("histology_tags")
    if not trial_tags_raw:
        return False

    # Convert from list (JSON storage) back to set
    trial_tags = set(trial_tags_raw) if isinstance(trial_tags_raw, list) else trial_tags_raw

    return _has_conflict(patient_tags, trial_tags)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Feb 24 22:11:18 2026

@author: ramyalsaffar
"""

