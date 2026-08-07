"""Cancer stage requirement extraction — the first half of File 10.

ITEM 20c, PASS 2a: this is "10- Structured Eligibility Extractor.py" up to
line 698, moved, minus the four negation names that went to
oncotriage.extraction.negation. Logic byte-for-byte unchanged.

Deterministic, rule-based, zero-LLM. INDEX TIME: File 11 calls
enrich_structured_eligibility(trial) per trial. QUERY TIME: File 13 calls
extract_patient_stage() then is_stage_mismatch(), which is an integer
comparison. Unknown -> None -> the trial passes through unfiltered.

This module reads NOTHING from the project except _is_negated. It touches no
config constant, no path, and no file. Importing it compiles regexes and
nothing else.
"""

import re
from typing import Dict, List, Optional, Tuple

from oncotriage.extraction.negation import _is_negated



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

# A SECOND PATIENT-SIDE STAGE REGEX STOOD HERE AND IS DELETED (pass 20f-3).
#
# It was `(?:tnm\s+)?stage\s+(...)`, differing from the one above only by an
# optional "tnm " prefix -- which the `\b` in the survivor already admits, since
# "TNM stage 1 (disorder)" contains "stage 1" on a word boundary. Nothing read
# it: `extract_patient_stage()` uses _SNOMED_DISPLAY_STAGE_RE at both of its
# match sites, and a repository-wide grep for the name returned only its own
# assignment and the prose recording that it was dead.
#
# Pass 20e's check 2h(ii) is what surfaced it -- deleting the numbered shims
# removed the `from oncotriage.extraction.stage import *`-shaped reads that had
# been masking every dead name in this module -- and pass 20e exempted rather
# than removed it, because that pass's acceptance criterion was that no
# behaviour changed and deleting a regex from the stage extractor is a change
# to this module. It is the follow-up that pass recorded, and it is the one of
# its three findings it said "should simply go".
#
# Its exemption entry in tests/test_package_invariants.py went with it, and had
# to: that file's "every exempted constant still exists" guard fails on an
# exemption for a deleted name.


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


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Mar  1 2026

@author: ramyalsaffar
"""
