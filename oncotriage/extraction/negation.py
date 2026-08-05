"""Negation detection shared by the stage and histology extractors.

ITEM 20c, PASS 2a: this is "10- Structured Eligibility Extractor.py" lines
124-184, moved. Logic byte-for-byte unchanged.

WHY THIS MODULE EXISTS AT ALL. File 10 was two extractors in one file — stage
requirements to line 698, histology tags from 699 — and the split was clean
except for exactly ONE name crossing the boundary: _is_histology_negated()
calls _is_negated(). Measured with ast, not grep: every top-level definition in
each half was walked for Name loads resolving to a top-level definition in the
other half, and that call is the only edge in either direction.

One shared helper is what makes the split a fact rather than a judgement call.
It lives here, with the three constants only it reads, and stage.py and
histology.py both import it. If a second shared name ever appears, it belongs
here too — or the split was wrong.

Note that histology.py has its own _is_histology_negated() with its own
_NEGATION_SUFFIXES / _NON_MORPH_LOOKBACK: suffix-side negation is a different
rule and stayed with the extractor that uses it.
"""

import re


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


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Mar  1 2026

@author: ramyalsaffar
"""
