"""Cancer stage requirement extraction — the first half of File 10.

ITEM 20c, PASS 2a: this is "10- Structured Eligibility Extractor.py" up to
line 698, moved, minus the four negation names that went to
oncotriage.extraction.negation. Logic byte-for-byte unchanged.

Deterministic, rule-based, zero-LLM. INDEX TIME: File 11 calls
enrich_structured_eligibility(trial) per trial. QUERY TIME: File 13 calls
extract_patient_stage() then is_stage_mismatch(), which is an integer
comparison. Unknown -> None -> the trial passes through unfiltered.

This module reads TWO names from the project — _is_negated, and
LOINC_AJCC_CLINICAL_M out of oncotriage.constants, which is the leaf of the
import graph and imports nothing itself. It touches no config constant, no
path, and no file. Importing it compiles regexes and nothing else.

(That sentence read "NOTHING except _is_negated" until the M-category item. The
LOINC has to be shared rather than inline because oncotriage/fhir/parser.py
ROUTES the Observation by it and this module SELECTS it back; two spellings
that drift make the rule silently never fire. Argued at the constant.)
"""

import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

from oncotriage.constants import LOINC_AJCC_CLINICAL_M
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
#
# THE TWO non_oncology_* KEYS ARE SEPARATE ON PURPOSE, and folding them would
# destroy both. `non_oncology_stage_skipped` counts TRIAL text and is read by
# `oncotriage/retrieval/indexer.py` through get_stage_extraction_stats() after
# an index build, to describe the corpus it just wrote. The patient-side guard
# fires at QUERY time, once per matching condition display, on every patient of
# every run -- so sharing the key would add an unbounded query-time count to an
# index-time statistic and make that number mean nothing at all. Nothing pins
# this dict's key set (checked: tests/test_registries_cancer_codes_and_stage_
# extraction.py reads individual keys and `any(values())`, and no test compares
# `.keys()`), so adding one is safe; what is NOT safe is reusing one.
#
# A plain dict rather than a Counter, and that is load-bearing: `d[k] += 1` on
# a key that is not declared here raises KeyError instead of silently creating
# a counter nobody reads. A typo at an increment site fails loudly.
_STAGE_EXTRACTION_COUNTS: Dict[str, int] = {
    "negated_skipped":            0,  # mention sat in a negative context
    "non_oncology_stage_skipped": 0,  # TRIAL text: "stage 4" of CKD/GVHD/etc.
    "non_oncology_patient_stage_skipped": 0,  # PATIENT condition display, ditto
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


def _is_non_oncology_stage(text: str, match_start: int, match_end: int,
                           counter_key: str = "non_oncology_stage_skipped") -> bool:
    """
    True if a "stage N" mention is qualified by a non-cancer staging system
    within _NON_ONCOLOGY_CONTEXT_WINDOW chars either side.

    Skipping such a mention is the conservative direction on both blocks: in
    the inclusion block it would invent a requirement, and in the exclusion
    block it would cap the trial below the stages it actually accepts, which
    hides it from patients it wants.

    `counter_key` exists so the PATIENT side can share this implementation
    without sharing its statistic -- see _STAGE_EXTRACTION_COUNTS. The default
    is the trial-side key, so every existing call site is unchanged. A key not
    declared in that dict raises KeyError rather than being created silently.
    """
    lo = max(0, match_start - _NON_ONCOLOGY_CONTEXT_WINDOW)
    hi = match_end + _NON_ONCOLOGY_CONTEXT_WINDOW
    if _NON_ONCOLOGY_STAGE_CONTEXT_RE.search(text[lo:hi]):
        _STAGE_EXTRACTION_COUNTS[counter_key] += 1
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


# ═══════════════════════════════════════════════════════════════════════════════
# THE AJCC CLINICAL M CATEGORY (LOINC 21907-1)
# ═══════════════════════════════════════════════════════════════════════════════
#
# M1 means distant metastasis. In every AJCC solid-tumour staging system that is
# stage IV by definition, whatever T and N say — so an M category is the one
# TNM axis a stage GROUP can be derived from on its own.
#
# THIS AXIS IS READ IN ONE DIRECTION ONLY, and getting that wrong is the way to
# do far more damage than the rule repairs:
#
#   cM1  ->  4        distant metastasis. Determinate.
#   cM0  ->  NOTHING  a POSITIVE statement that there is no distant metastasis.
#                     It is not evidence of an early stage: a patient can be
#                     cM0 and stage IIIC, and reading it as stage 0 or I would
#                     hand the Stage 4 filter a floor low enough to drop every
#                     advanced-disease trial they actually qualify for.
#   cMX  ->  NOTHING  "cannot be assessed" — the absence of a determination.
#
# Measured over all 1,000 corpus bundles on 2026-08-07 rather than taken from
# the note in oncotriage/fhir/parser.py that claimed it: 295 observations on
# 295 patients, one each, no patient carrying two — 290 cM0 (SNOMED 1229901006)
# and 5 cM1 (SNOMED 1229903009). So the mapping above is not a symmetry
# question. A rule that also read cM0 would reach 58 patients wrongly for every
# one it reached rightly.
#
# WHY THE WHOLE LIST IS SCANNED RATHER THAN THE MOST RECENT OBSERVATION WINNING,
# which is what the stage-GROUP tier above does. An AJCC stage group is
# RESTATED on re-staging and the newest assignment supersedes the older one, so
# "most recent wins" is right there. The M axis is not restated that way: a
# later cM0 after treatment records a RESPONSE, and AJCC does not de-stage a
# patient who has had distant metastasis — the stage group stays IV. So any cM1
# anywhere in the record answers the question. It is also the conservative
# direction for pre-screening, where a stage read too low drops trials
# permanently and invisibly while one read too high is still read by Stage 5.
# No corpus patient carries two of these observations, so this is a statement
# about what the rule MEANS rather than a behaviour difference today.
#
# The value text is what is matched, because that is what survives parsing:
# _parse_observation() keeps valueCodeableConcept's display and drops the code
# beside it. Changing that dict is not available — it feeds compute_patient_hash
# and the Stage 5 prompt, so widening it would invalidate all twelve
# characterization fixtures to read a code the display already carries.
#
# The optional prefix covers the AJCC notation for how the category was
# determined (c linical, p athological, y post-therapy, r recurrence) so a real
# EHR writing "ypM1a" is read; the subcategory letter covers M1a/M1b/M1c/M1d,
# every one of which is distant metastasis and therefore stage IV. The
# surrounding guards are lookarounds rather than \b because \b would not admit
# the "c" in "cM1" — the character before the M is a word character, so
# r"\bM1\b" matches nothing in the string this corpus actually stores.
_AJCC_M_CATEGORY_RE = re.compile(
    r"(?<![A-Za-z0-9])"                 # start of a token, not mid-word
    r"(?:yc|yp|rc|rp|[cpry])?"          # optional AJCC determination prefix
    r"m(?P<category>[01x])"             # the axis value itself
    r"(?P<subcategory>[a-d])?"          # M1a / M1b / M1c / M1d
    r"(?![A-Za-z0-9])",                 # end of a token
    re.IGNORECASE,
)

# 21907-1 values on which _AJCC_M_CATEGORY_RE found NO category at all, keyed by
# the text that failed. Module-level, following AGE_PARSE_FAILURES in
# oncotriage/agent/filtering.py, and NOT a new key in any returned dict for the
# reason argued there: the twelve characterization fixtures diff Stage 4's
# output field by field.
#
# ONLY the unreadable case is counted. cM0 and cMX are READ, and they mean "this
# axis contributes no stage" — a determinate answer, not a degradation. Counting
# them would put 290 entries per corpus pass into a counter whose whole purpose
# is to make the rare failure visible. This is third-party data, so it counts
# rather than raises, on exactly the footing AGE_PARSE_FAILURES argues.
M_CATEGORY_UNREADABLE: Dict[str, int] = Counter()

# Longest raw value kept in a counter key, matching _AGE_KEY_MAX_LEN's reasoning:
# long enough to see the shape of a real value, short enough that a pathological
# field cannot grow the key without bound.
_M_KEY_MAX_LEN: int = 60


def _stage_from_m_category(
    cancer_metastasis_observations: Optional[List[Dict]],
) -> Optional[int]:
    """
    Stage IV if any AJCC clinical M observation reports M1, else None.

    Reads LOINC 21907-1 ONLY, selected by code. The other three codes in
    parser.py's _METASTASIS_LOINCS travel in the same list and must not be read
    here: 44667-4 shares the M axis but carries metastasis SITE names (all 290
    corpus values are "None (qualifier value)"), and 85343-2 / 85344-0 are nodal
    COUNTS on the N axis. Keying on the ``metastasis_category == "M"`` field
    instead of the code would pull 44667-4 in.

    Returns 4 or None. None means "this axis says nothing", never "stage 0".
    """
    for obs in cancer_metastasis_observations or []:
        if (obs.get("code") or "").strip() != LOINC_AJCC_CLINICAL_M:
            continue

        raw = obs.get("value")
        text = "" if raw is None else str(raw)

        match = _AJCC_M_CATEGORY_RE.search(text)
        if match is None:
            M_CATEGORY_UNREADABLE[_m_key_text(text)] += 1
            continue

        if match.group("category") == "1":
            return _STAGE_MAX_ORDINAL

    return None


def _m_key_text(raw) -> str:
    """The raw value, capped, for use in a counter key. Never raises."""
    text = str(raw)
    return text if len(text) <= _M_KEY_MAX_LEN else text[:_M_KEY_MAX_LEN] + "..."


# ═══════════════════════════════════════════════════════════════════════════════
# WHICH TIER ANSWERED
# ═══════════════════════════════════════════════════════════════════════════════
# A closed vocabulary, one member per tier of extract_patient_stage(), in tier
# order. It exists because the Stage 5 prompt states the stage's PROVENANCE
# beside the stage: "Stage IV" resolved from a clinician-assigned stage group
# and "Stage IV" inferred from the word "metastatic" in a diagnosis name are
# the same ordinal and very different evidence, and a model asked to resolve a
# stage-gated criterion conservatively needs to know which it was handed.
#
# MACHINE TOKENS, NOT PROMPT WORDING. The wording a reader sees belongs to the
# thing doing the rendering; these name the tier. oncotriage/agent/patient.py
# holds the phrases and guards its map against STAGE_SOURCES at import, so a
# tier added here cannot reach the prompt as a KeyError or as silence.
#
# These are values, not tunables: each names a tier that exists in the function
# below, and adding one without adding a tier would be a declaration about
# machinery that is not there.
STAGE_SOURCE_STAGE_GROUP: str = "stage_group_observation"
STAGE_SOURCE_M_CATEGORY: str = "m_category_observation"
STAGE_SOURCE_CONDITION_DISPLAY: str = "condition_display"
STAGE_SOURCE_METASTATIC_KEYWORD: str = "metastatic_keyword"

STAGE_SOURCES: Tuple[str, ...] = (
    STAGE_SOURCE_STAGE_GROUP,
    STAGE_SOURCE_M_CATEGORY,
    STAGE_SOURCE_CONDITION_DISPLAY,
    STAGE_SOURCE_METASTATIC_KEYWORD,
)

# The clinical numeral for each ordinal, which is what trial criteria text is
# written in ("Stage IV or recurrent disease", "Stage IB-IIIA"). A fact about
# AJCC stage groups, not a tunable, and it lives here rather than at the render
# site because it is the inverse of _STAGE_ORDINAL above -- the one place the
# numeral/ordinal correspondence is stated. Stage 0 is in-situ disease and is a
# stage: it is spelled "Stage 0" because AJCC does, not because it is a
# placeholder for absence.
STAGE_NUMERALS: Dict[int, str] = {
    0: "Stage 0",
    1: "Stage I",
    2: "Stage II",
    3: "Stage III",
    4: "Stage IV",
}

# The two maps have to cover the same scale, and nothing else would notice if
# they stopped: _STAGE_ORDINAL is what produces an ordinal and STAGE_NUMERALS
# is what renders one, so a value producible but not renderable is a KeyError
# in the middle of building a prompt.
if set(STAGE_NUMERALS) != set(_STAGE_ORDINAL.values()):
    raise RuntimeError(
        "STAGE_NUMERALS does not cover the ordinal scale _STAGE_ORDINAL "
        f"produces: numerals {sorted(STAGE_NUMERALS)} vs ordinals "
        f"{sorted(set(_STAGE_ORDINAL.values()))}"
    )


def extract_patient_stage(
    conditions: List[Dict],
    cancer_stage_observations: Optional[List[Dict]] = None,
    cancer_metastasis_observations: Optional[List[Dict]] = None,
) -> Optional[int]:
    """Extract patient's cancer stage ordinal (0-4). See
    ``extract_patient_stage_with_source`` for the tiers and the reasoning.

    THIS IS A THIN DELEGATE AND THAT IS DELIBERATE. The tier logic lives in
    ``extract_patient_stage_with_source`` and is not duplicated here: two
    implementations of "what stage is this patient" is exactly the disagreement
    between the Stage 4 filter and the Stage 5 prompt that adding the prompt's
    stage section exists to remove. Callers that do not need the provenance
    keep this signature -- the pipeline's Stage 4 and the fixture cohort scan
    are both pinned to it by name.

    Returns ordinal 0-4 or None.  None -> stage filter keeps all trials.
    """
    ordinal, _source = extract_patient_stage_with_source(
        conditions,
        cancer_stage_observations,
        cancer_metastasis_observations,
    )
    return ordinal


def extract_patient_stage_with_source(
    conditions: List[Dict],
    cancer_stage_observations: Optional[List[Dict]] = None,
    cancer_metastasis_observations: Optional[List[Dict]] = None,
) -> Tuple[Optional[int], Optional[str]]:
    """
    Extract patient's cancer stage ordinal (0–4) from FHIR data.

    Tiers (first match wins):
      0. mCODE TNM stage group Observations (LOINC 21908-9/21902-2/21914-7)
         — structured, most reliable, used by mCODE-compliant EHRs (Epic etc.)
         — most recent observation wins when multiple exist.
      1. AJCC clinical M category Observation (LOINC 21907-1): M1 → 4.
         cM0 and cMX contribute nothing. See _stage_from_m_category().
      2. Condition display text regex — catches "TNM stage 1", "Stage III",
         "Carcinoma of breast, Stage 3" etc. across ALL cancer types.
         Works with Synthea, real EHRs, and any SNOMED display text.
         GUARDED by _is_non_oncology_stage: "Chronic kidney disease stage 3"
         is a staging system, not a cancer stage, and on this corpus it was
         supplying 245 of the 260 stages this tier produced.
      3. "metastatic" keyword in Condition display (→ 4)

    WHY THE M TIER SITS WHERE IT DOES. Below the stage group, because a stage
    group is the stage the clinician ASSIGNED — it already accounts for T, N and
    M together — and deriving one from a single axis is weaker evidence than
    being handed the answer. Above both condition-display tiers, because those
    read a diagnosis NAME: a coded Observation on a standard LOINC beats prose
    that happens to contain the word "stage", and it beats the "metastatic"
    keyword outright, since that keyword is the same clinical fact this tier
    reads from a structured field instead of a free-text one.

    CARRYING BOTH IS NOT ITSELF A CONTRADICTION, and the corpus is the reason
    to say so precisely. All five cM1 patients in the 1,000-bundle corpus also
    carry a stage GROUP, and all five groups read "Stage 4" — they AGREE, so
    this tier never fires for them and the change measured zero. A record is
    contradicting itself only when the group is BELOW IV beside a cM1, of which
    there are ZERO. Should one appear, this function does not reconcile: it
    takes the stage group, which is what the tier order means, and the
    disagreement is a data finding for whoever owns the record.

    Returns ``(ordinal, source)``. ``ordinal`` is 0–4 or None; None → stage
    filter keeps all trials. ``source`` is the STAGE_SOURCES member naming the
    tier that answered, and is None exactly when the ordinal is None — no tier
    answered, so there is no provenance to state.

    THE SOURCE IS REPORTED, NOT DECIDED HERE. Nothing in this function branches
    on it and no tier's logic or order changed when it was added; each tier
    already knew which one it was, and that fact was simply being dropped on
    the way out. What consumes it is the Stage 5 prompt, which states the
    evidence class beside the stage.
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
                    return ordinal, STAGE_SOURCE_STAGE_GROUP
            # Fallback: "metastatic" in display
            if 'metastatic' in display.lower() and 'non-metastatic' not in display.lower():
                return 4, STAGE_SOURCE_STAGE_GROUP

    # Tier 1: AJCC clinical M category Observation — structured, and the one
    # TNM axis that determines a stage group on its own.
    m_category_stage = _stage_from_m_category(cancer_metastasis_observations)
    if m_category_stage is not None:
        return m_category_stage, STAGE_SOURCE_M_CATEGORY

    # Tier 2: Condition display text regex (covers all cancer types, all SNOMED displays)
    #
    # GUARDED BY _is_non_oncology_stage, WHICH THIS TIER DID NOT CONSULT UNTIL
    # THE CKD ITEM. The regex sees the word "stage" and a numeral; it cannot
    # tell AJCC staging from any other staging system, and a patient's
    # condition list is FULL of other staging systems. Measured over all 1,000
    # corpus bundles on 2026-08-08: of the 260 patients whose stage came from
    # this tier, 245 got it from "Chronic kidney disease stage N (disorder)"
    # and 15 from a real cancer TNM display. Corpus-wide the regex matched CKD
    # displays 1,025 times against 16 cancer ones.
    #
    # It damages in BOTH directions, which is why "conservative" cannot mean
    # keeping it: a CKD stage 1 sets a floor that drops the advanced-disease
    # trials the patient qualifies for, and a CKD stage 4 sets a ceiling that
    # drops the early ones.
    #
    # finditer + continue, not search + give up, so that a display carrying two
    # stage mentions can still yield the cancer one when the first is
    # suppressed. That mirrors _collect_stage_ordinals() on the trial side
    # exactly -- this is the same guard wired the same way, not a second one.
    for cond in conditions:
        display = cond.get("display") or ""
        for m in _SNOMED_DISPLAY_STAGE_RE.finditer(display):
            if _is_non_oncology_stage(
                    display, m.start(), m.end(),
                    counter_key="non_oncology_patient_stage_skipped"):
                continue
            ordinal = _STAGE_ORDINAL.get(m.group(1).lower())
            if ordinal is not None:
                return ordinal, STAGE_SOURCE_CONDITION_DISPLAY

    # Tier 3: Metastatic keyword in Condition display
    #
    # DELIBERATELY NOT GUARDED by _is_non_oncology_stage, and the reason is that
    # the guard is the wrong instrument here rather than that this tier is
    # safe. That function answers "is this STAGE NUMERAL qualified by a
    # non-cancer STAGING SYSTEM": it needs a match span to window around, and
    # its vocabulary is CKD / GVHD / NYHA / Child-Pugh / COPD. This tier has no
    # numeral and no staging system -- it reads one word. Calling it here would
    # mean inventing a window around a keyword and asking a question about
    # staging systems that no display containing "metastatic" will answer.
    #
    # The real false-positive class for this tier is a DIFFERENT vocabulary:
    # "metastatic calcification" (calcium deposition in normal tissue, classically
    # secondary to chronic kidney disease), "metastatic abscess" and "metastatic
    # infection" (septic emboli) are non-cancer uses of the word. The guard
    # above would not catch any of them -- none is in its regex -- so widening
    # this tier means a new vocabulary, which is its own item with its own
    # measurement. Measured over all 1,000 corpus bundles on 2026-08-08:
    # exactly ONE condition display in the whole corpus contains "metastatic"
    # ("Metastatic malignant neoplasm to prostate (disorder)", 1 occurrence),
    # it is genuine cancer, and this tier is the answering tier for ZERO
    # patients. So the exposure today is nil and inventing a guard for it would
    # be untested code guarding nothing.
    for cond in conditions:
        display = (cond.get("display") or "").lower()
        if "metastatic" in display and "non-metastatic" not in display:
            return 4, STAGE_SOURCE_METASTATIC_KEYWORD

    return None, None


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
