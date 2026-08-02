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

# Ends of the ordinal scale. AJCC stage groups run 0 (in situ) to IV
# (distant), so these are facts about the staging system, not tunables.
_STAGE_MIN_ORDINAL: int = 0
_STAGE_MAX_ORDINAL: int = 4

# A collected span is treated as UNRESOLVED, not as a permissive range,
# when its lower bound is at or below this and its upper bound reaches
# _STAGE_MAX_ORDINAL.
#
# Why: _extract_stage_from_text collects every non-negated stage mention in
# a block and returns the global min/max. One stray "Stage I" in a
# prior-therapy sentence alongside a genuine "Stage IV" widens the span to
# I-IV, which admits every stage a staged patient can carry — the filter is
# off, but the payload claims a resolved requirement. Recording None instead
# says the same thing honestly: the block did not yield a usable bound. The
# only behavioural difference at query time is that stage-0 patients are now
# kept rather than dropped, which is the conservative direction.
_STAGE_FULL_RANGE_MIN_CEILING: int = 1

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


# How far back to look for a negation cue governing a match.
_NEGATION_LOOKBACK = 80

# A clause boundary between the negation cue and the match means the cue
# governs some earlier clause, not this match.
_CLAUSE_BOUNDARIES = re.compile(
    r"[.;]|\.\s|\bin\s+(?:patients?|participants?|subjects?)\b",
    re.IGNORECASE,
)


def _is_negated(text: str, match_start: int) -> bool:
    """
    Check if a regex match position is preceded by a negation phrase
    within the SAME clause/sentence.

    Uses a two-layer approach:
      1. Look back max _NEGATION_LOOKBACK chars for a negation keyword and
         take the NEAREST one — the last match in the window, not the first.
      2. Verify there is no sentence/clause boundary (period, semicolon,
         "in Participants") between that keyword and the match — prevents
         false positives from distant unrelated clauses like "With or
         Without MK-2870 in Participants With Resectable Stage II..."

    Why nearest rather than first: the cue that governs a phrase is the one
    closest to it. re.search() returns the LEFTMOST negation in the window,
    which is the one most likely to have a clause boundary after it — so
    layer 2 discards it and the function reports "not negated" even when a
    nearer cue sits in the same clause as the match. Picking the furthest
    candidate systematically under-detects negation, which is the wrong
    direction: a missed negation writes a requirement the trial never
    stated. Scanning to the last match fixes the selection.
    """
    window_start = max(0, match_start - _NEGATION_LOOKBACK)
    prefix = text[window_start:match_start]

    # Nearest preceding negation cue = last match in the look-back window.
    neg_match = None
    for m in _NEGATION_PREFIXES.finditer(prefix):
        neg_match = m
    if neg_match is None:
        return False

    # Text between the negation keyword and the match
    between = prefix[neg_match.end():]

    # If there's a clause boundary between negation and match, not negated
    if _CLAUSE_BOUNDARIES.search(between):
        return False

    return True


# Which stage-extraction path fired. Nothing here recovers silently: a
# skipped mention, a discarded span and a tightened bound each land in a
# counter, readable after an index build via get_stage_extraction_stats().
_STAGE_EXTRACTION_COUNTS: Dict[str, int] = {
    "negated_skipped":            0,  # mention sat in a negative context
    "non_oncology_stage_skipped": 0,  # "stage 4" of CKD/GVHD/Child-Pugh etc.
    "full_range_unresolved":      0,  # collected span covered the whole scale
    "exclusion_upper_bound":      0,  # exclusion text lowered max_stage
    "exclusion_not_suffix":       0,  # exclusion stages did not reach stage IV
    "exclusion_scale_swept":      0,  # exclusion run covered stage I upward
    "exclusion_contradictory":    0,  # exclusion bound fell below the min bound
}


def get_stage_extraction_stats() -> Dict[str, int]:
    """Copy of the stage negation / span / exclusion-bound counters."""
    return dict(_STAGE_EXTRACTION_COUNTS)


def reset_stage_extraction_stats() -> None:
    """Zero the stage counters (per-run reporting, tests)."""
    for key in _STAGE_EXTRACTION_COUNTS:
        _STAGE_EXTRACTION_COUNTS[key] = 0


# Staging systems that are NOT cancer staging. "Stage 4" in criteria text
# just as often means chronic kidney disease, graft-versus-host disease or
# Child-Pugh class as it means AJCC stage IV, and _SINGLE_RE cannot tell the
# difference — it only sees the word "stage" and a numeral.
#
# Each entry is a disease-specific phrase, never a bare organ word: "renal"
# or "kidney" alone would suppress "Stage IV renal cell carcinoma", which is
# a cancer stage. External clinical-terminology facts, so they live here as
# a named constant rather than in config.
_NON_ONCOLOGY_STAGE_CONTEXT_RE = re.compile(
    r"\bckd\b|chronic\s+kidney\s+disease|kidney\s+disease|"
    r"renal\s+(?:insufficiency|failure|impairment|disease|dysfunction)|"
    r"nephropathy|end[\s-]stage\s+renal|"
    r"\bgvhd\b|graft[\s-]versus[\s-]host|"
    r"\bnyha\b|heart\s+failure|"
    r"child[\s-]pugh|cirrhosis|(?:hepatic|liver)\s+fibrosis|encephalopathy|"
    r"\bcopd\b|american\s+society\s+of\s+anesthesiologist|"
    r"pressure\s+(?:ulcer|injury)|decubitus|"
    r"retinopathy|sleep\s+apn(?:o)?ea|hypertension|"
    r"endometriosis|sarcoidosis|fibrosis",
    re.IGNORECASE,
)

# How far either side of a stage mention to look for such a qualifier.
# Tight on purpose: these qualifiers sit adjacent to the numeral
# ("CKD >=Stage 4", "Stage 4 skin GVHD", "stage IV-V chronic kidney
# disease"), and a wide window would start suppressing genuine cancer
# stages that merely share a criteria bullet with a comorbidity.
_NON_ONCOLOGY_CONTEXT_WINDOW = 30


def _stage_negated(text: str, match_start: int) -> bool:
    """_is_negated with the stage-side counter attached."""
    if _is_negated(text, match_start):
        _STAGE_EXTRACTION_COUNTS["negated_skipped"] += 1
        return True
    return False


def _is_non_oncology_stage(text: str, match_start: int, match_end: int) -> bool:
    """
    True if a "stage N" mention is qualified by a non-cancer staging system
    within _NON_ONCOLOGY_CONTEXT_WINDOW chars either side.

    Skipping such a mention is the conservative direction on both blocks: in
    the inclusion block it would invent a requirement, and in the exclusion
    block it would cap the trial below the stages it actually accepts, which
    hides it from patients it wants.
    """
    lo = max(0, match_start - _NON_ONCOLOGY_CONTEXT_WINDOW)
    hi = match_end + _NON_ONCOLOGY_CONTEXT_WINDOW
    if _NON_ONCOLOGY_STAGE_CONTEXT_RE.search(text[lo:hi]):
        _STAGE_EXTRACTION_COUNTS["non_oncology_stage_skipped"] += 1
        return True
    return False


def _is_full_range_span(lo: int, hi: int) -> bool:
    """
    True if (lo, hi) covers the whole discriminable stage scale and
    therefore constrains nothing. See _STAGE_FULL_RANGE_MIN_CEILING.
    """
    return lo <= _STAGE_FULL_RANGE_MIN_CEILING and hi >= _STAGE_MAX_ORDINAL


def _collect_stage_ordinals(text: str) -> List[int]:
    """
    Every non-negated stage ordinal mentioned in `text`, in match order.

    Shared by the inclusion-side extractor and the exclusion-side upper
    bound so both read stage mentions — and negation — identically.
    """
    ordinals: List[int] = []

    for m in _RANGE_RE.finditer(text):
        if _stage_negated(text, m.start()):
            continue
        if _is_non_oncology_stage(text, m.start(), m.end()):
            continue
        lo = _STAGE_ORDINAL.get(m.group(1).lower())
        hi = _STAGE_ORDINAL.get(m.group(2).lower())
        if lo is not None:
            ordinals.append(lo)
        if hi is not None:
            ordinals.append(hi)

    # Also check single-stage mentions that may follow the range pattern
    # (e.g., the "IV" in "stage II, III, or IV" that the range regex missed)
    for m in _SINGLE_RE.finditer(text):
        if _stage_negated(text, m.start()):
            continue
        if _is_non_oncology_stage(text, m.start(), m.end()):
            continue
        val = _STAGE_ORDINAL.get(m.group(1).lower())
        if val is not None:
            ordinals.append(val)

    return ordinals


def _extract_stage_upper_bound_from_exclusion(exclusion: str) -> Optional[int]:
    """
    Read the EXCLUSION block for an upper stage bound.

    "Stage IV disease will be excluded" is a hard cap on the population that
    the inclusion block never states, and until now nothing read it — the
    trial ended up either unresolved or, worse, carrying an inclusion-derived
    max that admitted the very stage the trial rejects.

    Semantics are inverted relative to the inclusion block: an affirmative
    (non-negated) stage mention here names who is kept OUT. Negated mentions
    are skipped exactly as elsewhere, so "except stage I" — "except for" is
    already a _NEGATION_PREFIXES cue — contributes nothing.

    Only a bound is derivable, and only from a CONTIGUOUS SUFFIX of the
    scale: the excluded stages must reach _STAGE_MAX_ORDINAL, and the bound
    is one below the lowest stage in that unbroken run down from the top.
      "Stage IV excluded"        → {4}       → max_stage 3
      "Stage III or IV excluded" → {3, 4}    → max_stage 2
      "prior stage I malignancy" → {1}       → no bound (does not reach IV)
      "stage I ... stage IV"     → {1, 4}    → max_stage 3 (the stray 1 is
                                               not part of the run)
    An excluded set that does not reach the top ("stage II excluded") is not
    expressible as an upper bound at all; it is counted and ignored rather
    than approximated, because approximating it would drop patients the
    trial accepts. A run that reaches all the way down to stage I is
    likewise refused — see the _STAGE_MIN_ORDINAL check below.

    Non-cancer staging systems ("stage 4 chronic kidney disease") are
    skipped by _is_non_oncology_stage() before any of this.

    Returns:
        The upper bound ordinal, or None when no bound is derivable.
    """
    if not exclusion or not exclusion.strip():
        return None

    excluded = set(_collect_stage_ordinals(exclusion))
    if not excluded:
        return None

    if _STAGE_MAX_ORDINAL not in excluded:
        _STAGE_EXTRACTION_COUNTS["exclusion_not_suffix"] += 1
        return None

    # Walk down from the top while the run is unbroken.
    lowest_excluded = _STAGE_MAX_ORDINAL
    while (lowest_excluded - 1) in excluded:
        lowest_excluded -= 1

    bound = lowest_excluded - 1
    if bound <= _STAGE_MIN_ORDINAL:
        # The run swallowed the whole scale: stage I upward all "excluded",
        # leaving only stage 0. No interventional trial enrols in-situ
        # disease exclusively, so this is never an eligibility statement —
        # it is a sentence that merely enumerates stages, e.g. "For Murphy
        # stage III/IV patients, or stage I/II patients with steroid
        # pretreatment, the following applies". Emitting max_stage=0 there
        # would hide the trial from every staged patient it wants. Leave
        # the axis unfiltered instead.
        _STAGE_EXTRACTION_COUNTS["exclusion_scale_swept"] += 1
        return None

    return bound


def _extract_stage_from_text(text: str) -> Optional[Tuple[int, int]]:
    """
    Extract (min_stage, max_stage) from a block of text.
    Returns None if nothing found.  Skips negated mentions.

    A collected span that covers the whole scale is returned as None rather
    than as a permissive range — see _STAGE_FULL_RANGE_MIN_CEILING.
    """
    if not text or not text.strip():
        return None

    # --- Pass 1: Stage range / list ---
    # Collect ALL non-negated stage mentions, then take min/max
    all_ordinals = _collect_stage_ordinals(text)

    if all_ordinals:
        lo, hi = min(all_ordinals), max(all_ordinals)
        if _is_full_range_span(lo, hi):
            # The block mentioned stages, but the span they imply admits
            # everything. Report unresolved instead of a fake requirement;
            # the exclusion block may still supply a real upper bound.
            _STAGE_EXTRACTION_COUNTS["full_range_unresolved"] += 1
            return None
        return (lo, hi)

    # --- Pass 2: Metastatic keyword (not non-metastatic) ---
    if _NON_METASTATIC_RE.search(text):
        # "Non-metastatic" → don't set stage=4 even if "metastatic" also matches
        pass
    else:
        for m in _METASTATIC_RE.finditer(text):
            if _stage_negated(text, m.start()):
                continue
            return (_STAGE_MAX_ORDINAL, _STAGE_MAX_ORDINAL)

    # --- Pass 3: Locally advanced ---
    for m in _LOCALLY_ADVANCED_RE.finditer(text):
        if _stage_negated(text, m.start()):
            continue
        return (3, _STAGE_MAX_ORDINAL)

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

    The EXCLUSION block is then read separately, for an upper bound only —
    "Stage IV will be excluded" is a cap the inclusion block never states.
    It can only tighten max_stage, never loosen it or move min_stage. See
    _extract_stage_upper_bound_from_exclusion().

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

    # --- Upper bound from the exclusion block ---
    # Applies whether or not the inclusion side resolved: an unresolved
    # trial gains a cap it never had, and a resolved one is tightened.
    exclusion_upper = _extract_stage_upper_bound_from_exclusion(exclusion)
    if exclusion_upper is not None:
        if max_stage is None or exclusion_upper < max_stage:
            _STAGE_EXTRACTION_COUNTS["exclusion_upper_bound"] += 1
            max_stage = exclusion_upper

        if min_stage is not None and min_stage > max_stage:
            # Inclusion floor sits above the exclusion cap — the two blocks
            # disagree and the trial would accept nobody. Drop back to
            # unresolved rather than index an unsatisfiable range.
            _STAGE_EXTRACTION_COUNTS["exclusion_contradictory"] += 1
            min_stage = None
            max_stage = None

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
# HISTOLOGY NEGATION HANDLING
# ══════════════════════════════════════════════════════════════════════════
#
# Trial text negates a histology mention in three structurally different
# ways, and each one, if read literally, writes a tag the trial does not
# actually require:
#
#   1. CLAUSE PREFIX  "patients without squamous histology"
#                     → _is_negated(), the same look-back used by stage
#                       extraction, over the phrases in _NEGATION_PREFIXES.
#   2. MORPHOLOGICAL  "non-squamous", "non‑adenocarcinoma"
#                     → _NON_MORPH_PREFIX_RE. Not a phrase, so the
#                       look-back cannot see it; "non-small cell" already
#                       has its own dedicated pattern pair.
#   3. CLAUSE SUFFIX  "adenocarcinoma is excluded"
#                     → _NEGATION_SUFFIXES. The cue FOLLOWS the term, so
#                       a look-back is structurally blind to it.
#
# Direction of error matters. A missed negation invents a required
# histology, which then conflicts with a patient who does qualify and
# hides the trial from them — the defect this whole module exists to
# avoid. An over-eager negation merely drops a tag, leaving the trial
# unfiltered, which is the conservative default of the module. So every
# rule here is allowed to over-fire.
#
# Known over-fire: "…without prior therapy for non-small cell lung cancer"
# negates the nsclc tag, because _is_negated cannot tell that the negation
# scopes over "prior therapy" rather than the histology. The trial then
# carries no histology tag and is filtered by nobody. Counted under
# clause_prefix in get_histology_extraction_stats().

# "non-" / "non " immediately before the term (unicode hyphens included).
# Anchored at end-of-string because it is matched against the look-back window.
_NON_MORPH_PREFIX_RE = re.compile(
    r"\bnon[\s\-‐‑‒–]?$",
    re.IGNORECASE,
)

# Exclusion cue appearing AFTER the histology term, within the same clause.
# "ruled out" appears in _NEGATION_PREFIXES too, but in real criteria text it
# almost always trails its term ("adenocarcinoma must be ruled out"), where a
# look-back cannot reach it.
_NEGATION_SUFFIXES = re.compile(
    r"\b(?:excluded|excluded\s+from|ineligible|disallowed|ruled\s+out|"
    r"not\s+(?:eligible|allowed|permitted|included|enrolled))\b",
    re.IGNORECASE,
)

# How far past the term to look for a trailing exclusion cue, before the
# first clause boundary. Same boundary set ([.;]) as _is_negated.
_HISTOLOGY_SUFFIX_WINDOW = 60

# Chars of look-back needed to see a "non-" prefix ("non-" / "non ").
_NON_MORPH_LOOKBACK = 6

# Which rule suppressed a mention / rejected a tag set. Never silently
# recovered: every skip lands in one of these counters and is readable via
# get_histology_extraction_stats() after an index build.
_HISTOLOGY_EXTRACTION_COUNTS: Dict[str, int] = {
    "clause_prefix":           0,   # rule 1 — _is_negated look-back
    "morphological":           0,   # rule 2 — "non-<term>"
    "clause_suffix":           0,   # rule 3 — "<term> … is excluded"
    "contradiction_rejected":  0,   # enrich_histology_tags refused a tag set
    "contradiction_softened":  0,   # a contradictory pair was dropped
                                    # (patient conditions, or a refused trial
                                    #  recovered via soften_histology_conflict)
}


def get_histology_extraction_stats() -> Dict[str, int]:
    """Copy of the histology negation / contradiction counters."""
    return dict(_HISTOLOGY_EXTRACTION_COUNTS)


def reset_histology_extraction_stats() -> None:
    """Zero the histology counters (per-run reporting, tests)."""
    for key in _HISTOLOGY_EXTRACTION_COUNTS:
        _HISTOLOGY_EXTRACTION_COUNTS[key] = 0


def _is_histology_negated(text: str, match_start: int, match_end: int) -> bool:
    """
    True if a histology term match sits in a negative context.

    Applies the three rules above in order and records which one fired.
    """
    if _is_negated(text, match_start):
        _HISTOLOGY_EXTRACTION_COUNTS["clause_prefix"] += 1
        return True

    lookback = text[max(0, match_start - _NON_MORPH_LOOKBACK):match_start]
    if _NON_MORPH_PREFIX_RE.search(lookback):
        _HISTOLOGY_EXTRACTION_COUNTS["morphological"] += 1
        return True

    tail = text[match_end:match_end + _HISTOLOGY_SUFFIX_WINDOW]
    tail = re.split(r"[.;]", tail, maxsplit=1)[0]
    if _NEGATION_SUFFIXES.search(tail):
        _HISTOLOGY_EXTRACTION_COUNTS["clause_suffix"] += 1
        return True

    return False


def _has_affirmative_match(pattern, text: str) -> bool:
    """
    True if `pattern` matches `text` at least once in a NON-negated context.

    Replaces the bare pattern.search() the tag extractor used to do — a
    search only proves the words are present, not that the trial wants them.
    """
    for m in pattern.finditer(text):
        if _is_histology_negated(text, m.start(), m.end()):
            continue
        return True
    return False


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

    NEGATION-AWARE: a mention in a negative context ("non-squamous",
    "without adenocarcinoma", "adenocarcinoma is excluded") describes who
    is kept OUT, not what the trial requires, and produces no tag. See
    _is_histology_negated().
    """
    if not text or not text.strip():
        return set()

    tags = set()

    # Lung context is a topic gate, not a claim about the patient — a
    # negated lung mention still means the text is about lung. Left as a
    # plain search deliberately.
    has_lung = bool(_LUNG_CONTEXT_RE.search(text))

    # --- Non-small cell vs small cell (lung-specific) ---
    has_nsc = (_has_affirmative_match(_NON_SMALL_CELL_RE, text)
               or _has_affirmative_match(_NSCLC_ABBREV_RE, text))
    has_sc = (_has_affirmative_match(_SMALL_CELL_RE, text)
              or _has_affirmative_match(_SCLC_ABBREV_RE, text))

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
    if _has_affirmative_match(_NEUROENDOCRINE_RE, text):
        tags.add("neuroendocrine")

    # --- Squamous vs Adenocarcinoma ---
    if _has_affirmative_match(_SQUAMOUS_RE, text):
        tags.add("squamous")

    if _has_affirmative_match(_ADENOCARCINOMA_RE, text):
        tags.add("adenocarcinoma")

    # --- Tracheal ---
    if _has_affirmative_match(_TRACHEAL_RE, text):
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


class HistologyTagConflictError(ValueError):
    """
    Raised when ONE side (a single trial, or a single text) yields a tag set
    that contains a mutually exclusive pair.

    A trial cannot REQUIRE both squamous and adenocarcinoma histology, so
    such a set is never a fact about the trial. Written to the payload as-is
    it would conflict with a squamous patient AND with an adenocarcinoma
    patient, hiding the trial from both — so extraction refuses to produce
    it rather than quietly poisoning the index.

    The refusal is deliberately loud but recoverable: the error carries the
    trial that produced it, so a caller that knows the pair means "permits
    either" can call soften_histology_conflict() and still index the trial.

    Attributes:
        trial: the trial dict that produced the set (None if not trial-side).
        tags:  the full extracted tag set, contradiction included.
        pair:  the mutually exclusive pair found, as a sorted tuple.
    """

    def __init__(self, message: str, trial: Optional[Dict] = None,
                 tags: Optional[Set[str]] = None,
                 pair: Optional[Tuple[str, str]] = None):
        super().__init__(message)
        self.trial = trial
        self.tags = set(tags or ())
        self.pair = pair


def _find_exclusive_pair(tags: Set[str]) -> Optional[Tuple[str, str]]:
    """
    Return the first mutually exclusive pair present in `tags`, or None.

    Same _EXCLUSIVE_PAIRS table used for the cross-side conflict check, so
    self-contradiction and patient/trial conflict can never disagree.
    """
    tag_set = set(tags)
    for pair in _EXCLUSIVE_PAIRS:
        if pair.issubset(tag_set):
            return tuple(sorted(pair))
    return None


def _drop_contradictory_tags(tags: Set[str], context_label: str, log=print) -> Set[str]:
    """
    Remove every mutually exclusive pair from `tags` and log each removal.

    Both sides use this, for the same reason from opposite directions:
      - PATIENT: a contradictory set can be legitimate (two primaries —
        squamous cervical + lung adenocarcinoma). Keeping both would
        conflict with, and drop, trials for either one.
      - TRIAL: a contradictory set means the trial PERMITS either histology
        ("adenocarcinoma or squamous cell carcinoma of the esophagus"), and
        the pair is dropped by soften_histology_conflict() after the raise.

    Either way the pair goes, that axis is left unfiltered, and the trial
    stays reachable. Conservative: unknown → keep.

    Args:
        tags:          extracted tag set.
        context_label: what produced the set, for the log line.
        log:           sink for the per-drop message (pass tqdm.write to
                       keep a progress bar intact).
    """
    tags = set(tags)
    pair = _find_exclusive_pair(tags)
    while pair:
        _HISTOLOGY_EXTRACTION_COUNTS["contradiction_softened"] += 1
        log(f"  [Histology] {context_label}: mutually exclusive tags "
            f"'{pair[0]}' + '{pair[1]}' both extracted — dropping both, "
            f"that axis is left unfiltered")
        tags -= set(pair)
        pair = _find_exclusive_pair(tags)
    return tags


def soften_histology_conflict(error: HistologyTagConflictError, log=print) -> Dict:
    """
    Recover an indexable trial from a HistologyTagConflictError.

    A trial tagged {squamous, adenocarcinoma} in practice PERMITS either
    histology rather than requiring both — "adenocarcinoma or squamous cell
    carcinoma of the esophagus" is ordinary eligibility language. Refusing
    such a trial removes it from the index for EVERY patient, including
    patients with no histology tag at all, which is the same false-ineligible
    direction the histology filter exists to prevent. Dropping the pair keeps
    the trial indexed and unfiltered on the histology axis.

    Call this from the index-time handler (File 11); enrich_histology_tags
    still raises, so the refusal is never silent and always counted.

    Args:
        error: the HistologyTagConflictError raised by enrich_histology_tags.
        log:   sink for the per-drop message (pass tqdm.write under a bar).

    Returns:
        The trial dict, with "histology_tags" set to the softened list.

    Raises:
        HistologyTagConflictError: re-raised unchanged when the error carries
            no trial — there is nothing to recover and the caller must skip it.
    """
    if error.trial is None:
        raise error

    label = f"trial {error.trial.get('nct_id') or '<unknown>'}"
    error.trial["histology_tags"] = sorted(
        _drop_contradictory_tags(error.tags, label, log=log)
    )
    return error.trial


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

    Raises:
        HistologyTagConflictError: if the resulting set is self-contradictory
            (contains a mutually exclusive pair). The caller must skip the
            trial rather than index a payload no patient can ever satisfy.
    """
    title = trial.get("title") or ""
    inclusion = trial.get("eligibility", {}).get("inclusion_criteria") or ""

    # Union tags from title + inclusion (both may contain useful signals)
    tags = _extract_histology_tags(title)
    tags |= _extract_histology_tags(inclusion)

    # A single trial cannot require two mutually exclusive histologies.
    # Refuse to emit a set that would conflict with both patient populations
    # at once. The error carries the trial, so an index-time caller can call
    # soften_histology_conflict() and index it unfiltered on this axis.
    pair = _find_exclusive_pair(tags)
    if pair:
        _HISTOLOGY_EXTRACTION_COUNTS["contradiction_rejected"] += 1
        raise HistologyTagConflictError(
            f"Trial {trial.get('nct_id') or '<unknown>'}: self-contradictory "
            f"histology tags {sorted(tags)} — '{pair[0]}' and '{pair[1]}' are "
            f"mutually exclusive, so no patient can satisfy both.",
            trial=trial,
            tags=tags,
            pair=pair,
        )

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

    A patient CAN legitimately carry two mutually exclusive tags (two
    primaries), unlike a trial. Rather than raise at query time, the
    contradictory pair is dropped and logged — see _drop_contradictory_tags.
    """
    tags = set()
    for cond in conditions:
        display = cond.get("display") or ""
        tags |= _extract_histology_tags(display)

    return _drop_contradictory_tags(tags, "patient conditions")


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

