"""Histology tag extraction — the second half of File 10.

ITEM 20c, PASS 2a: this is "10- Structured Eligibility Extractor.py" from line
699 onward, moved. Logic byte-for-byte unchanged.

Same shape as the stage half: INDEX TIME File 11 calls enrich_histology_tags(),
QUERY TIME File 13 calls extract_patient_histology() then
is_histology_mismatch(). The tags are mutually exclusive pairs (nsclc/sclc,
squamous/adenocarcinoma), and a conflict is what filters a trial out.

Its ONE dependency on the stage half was _is_negated, which now lives in
oncotriage.extraction.negation. _is_histology_negated() below is a different
rule — it looks for a negation SUFFIX after the match — and stays here.

This module reads nothing from the project except _is_negated, and no file.
"""

import re
from typing import Dict, List, Optional, Set, Tuple

from oncotriage.extraction.negation import _is_negated



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
#
# IGNORECASE, like every other pattern in this file. Without it "nsclc" in
# lower case produced NO histology tag, and an untagged trial is filtered by
# nobody: is_histology_mismatch() only fires when the trial carries a tag, so a
# lower-case small-cell trial reached a non-small-cell patient — the exact
# confusion this module exists to prevent.
_NSCLC_ABBREV_RE = re.compile(r"\bNSCLC\b", re.IGNORECASE)

# Detect "small cell" that is NOT preceded by "non-"
# Uses negative lookbehind for "non" + separator
_SMALL_CELL_RE = re.compile(
    r"(?<!\bnon[\s\-\u2010\u2011\u2012\u2013])"  # not preceded by "non-"
    r"\bsmall[\s\-]?cell\b",
    re.IGNORECASE,
)

# Detect "SCLC" abbreviation (but NOT "NSCLC")
#
# IGNORECASE for the reason above. It changes what the negative lookbehind
# excludes — case-folded, `(?<!\bN)` now also excludes a preceding lower-case
# "n" — and that widening costs nothing, because THE LOOKBEHIND IS UNREACHABLE
# IN BOTH CASES. `\bSCLC\b` requires a word boundary before the S, and in
# "NSCLC"/"nsclc" the preceding N is itself a word character, so there is no
# boundary there and the alternative branch never gets as far as the lookbehind.
# It is kept as a belt-and-braces guard, not deleted, but it is not what stops
# SCLC firing inside NSCLC. Proved by running both cases and both spellings.
_SCLC_ABBREV_RE = re.compile(r"(?<!\bN)\bSCLC\b", re.IGNORECASE)

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

# Which rule suppressed a mention, plus how many trials came out permissive
# on the histology axis. Never silent: every skip lands in one of these
# counters and is readable via get_histology_extraction_stats() after a build.
_HISTOLOGY_EXTRACTION_COUNTS: Dict[str, int] = {
    "clause_prefix":           0,   # rule 1 — _is_negated look-back
    "morphological":           0,   # rule 2 — "non-<term>"
    "clause_suffix":           0,   # rule 3 — "<term> … is excluded"
    "exclusive_pair_kept":     0,   # a trial carried both members of an
                                    # exclusive pair ("adenocarcinoma OR
                                    # squamous") — both tags are indexed, the
                                    # trial permits either. Counted, not dropped.
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
# A patient and a trial CONFLICT only when the two tag sets DO NOT OVERLAP
# and a mutually exclusive pair spans them. Overlap wins: if the patient's
# own histology is among the trial's tags, the trial names that patient's
# disease and is kept, whatever else it also names.
#
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
#
# sclc ↔ neuroendocrine is deliberately ABSENT: SCLC *is* a neuroendocrine
# carcinoma of the lung, so a neuroendocrine trial is a trial for the SCLC
# patient's own disease. Listing it would drop exactly the trials that fit.
# tracheal ↔ squamous is likewise absent — tracheal squamous cell carcinoma
# is the commonest tracheal histology, not a contradiction.

# Define mutually exclusive pairs as frozensets for O(1) lookup.
# This set must match the table above, pair for pair.
_EXCLUSIVE_PAIRS = {
    frozenset({"nsclc", "sclc"}),
    frozenset({"nsclc", "neuroendocrine"}),
    frozenset({"nsclc", "tracheal"}),
    frozenset({"sclc", "tracheal"}),
    frozenset({"squamous", "adenocarcinoma"}),
}


def _find_exclusive_pair(tags: Set[str]) -> Optional[Tuple[str, str]]:
    """
    Return the first mutually exclusive pair present in `tags`, or None.

    Same _EXCLUSIVE_PAIRS table used for the cross-side conflict check, so
    the "permits either" count and the patient/trial conflict test can never
    disagree about what the pairs are.
    """
    tag_set = set(tags)
    for pair in _EXCLUSIVE_PAIRS:
        if pair.issubset(tag_set):
            return tuple(sorted(pair))
    return None


def _has_conflict(patient_tags: Set[str], trial_tags: Set[str]) -> bool:
    """
    True when the trial must be DROPPED for this patient's histology.

    The rule is INTERSECTION FIRST:
      1. Either set empty                     → False (unknown → keep)
      2. Sets intersect                       → False (the trial names the
         patient's own histology; it is a trial for this disease, whatever
         else it also names)
      3. No intersection, but a mutually exclusive pair spans the two sets
                                              → True  (drop)
      4. Otherwise                            → False

    Rule 2 is what makes "adenocarcinoma OR squamous cell carcinoma of the
    esophagus" work: an adenocarcinoma patient intersects that trial and is
    kept, instead of being dropped by the adeno↔squamous pair the trial also
    carries. It is also correct for a patient with two primaries, whose tag
    set can itself contain both members of a pair — such a patient matches
    trials naming either type.
    """
    if not patient_tags or not trial_tags:
        return False

    # The patient's own histology is named by the trial → keep, full stop.
    if patient_tags & trial_tags:
        return False

    for p_tag in patient_tags:
        for t_tag in trial_tags:
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

    A trial carrying BOTH members of a mutually exclusive pair is not
    self-contradictory — it PERMITS either histology ("adenocarcinoma or
    squamous cell carcinoma of the esophagus", common in esophageal, cervical
    and urothelial disease). Both tags are kept: _has_conflict() intersects
    before it looks for an exclusive pair, so each population still matches.
    The occurrence is counted (exclusive_pair_kept) so index builds report how
    much of the corpus is permissive on this axis.

    Args:
        trial: Trial dict from parse_trial_metadata(). Modified in-place.

    Returns:
        The same trial dict with "histology_tags" key added (sorted list).
    """
    title = trial.get("title") or ""
    inclusion = trial.get("eligibility", {}).get("inclusion_criteria") or ""

    # Union tags from title + inclusion (both may contain useful signals)
    tags = _extract_histology_tags(title)
    tags |= _extract_histology_tags(inclusion)

    if _find_exclusive_pair(tags):
        _HISTOLOGY_EXTRACTION_COUNTS["exclusive_pair_kept"] += 1

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
    primaries — squamous cervical + lung adenocarcinoma). Both are kept:
    under the intersection-first rule in _has_conflict() such a patient
    matches trials naming EITHER type, which is what two primaries means.
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
      - the two sets intersect              → False (trial names the
                                                     patient's histology → keep)
      - no overlap, exclusive pair spans    → True  (mismatch → drop)
      - no overlap, no exclusive pair       → False (compatible → keep)

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
Created on Sun Mar  1 2026

@author: ramyalsaffar
"""
