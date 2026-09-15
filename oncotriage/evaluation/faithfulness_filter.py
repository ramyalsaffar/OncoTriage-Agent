# Faithfulness Placeholder Filter
#################################

"""Which recorded assessments Ragas faithfulness must not score.

DECIDED FROM THE VERDICT'S OWN REASON AND PROVENANCE FIELDS, NEVER FROM ITS TEXT.
A text match would be satisfied by a model that happened to write "Not
assessed." and would miss a placeholder the day its wording changes. The reason
is stamped by the pipeline, never by the model
(``oncotriage/agent/evaluation.py:_strip_forged_provenance``), so it is the
field that says who wrote the text.

WHAT FAITHFULNESS MEASURES, AND WHY THREE KINDS OF TEXT ARE NOT THE SAME.
Faithfulness asks whether each statement in the response is supported by the
retrieved contexts (the patient summary and the trial text). That is a
measurement of the pipeline only when the response is text the pipeline
PRODUCED ABOUT THIS PATIENT AND THIS TRIAL. Read from the code that writes the
stored ``assessment``:

  * CONSTRUCTED PLACEHOLDERS -- the five reasons in ``REASONS_CONSTRUCTED``.
    ``_unevaluable_entry`` stores a fixed sentence per reason ("The request
    carrying this trial did not produce a response, so the model never
    answered for it. Not assessed."). The model never answered. The sentence
    is identical for every patient and every trial, and it makes claims about
    the pipeline's transport, not about either context. Its
    ``assessment_draft`` is the same sentence (``setdefault`` in the node), so
    it is a placeholder under BOTH response fields.
  * FIXED CORRECTION SENTENCES -- the two reasons in
    ``REASONS_FIXED_CORRECTION_TEXT``. ``compose_assessment`` returns a
    constant for these (``ASSESSMENT_UNSUPPORTED_REJECTION_TEXT`` /
    ``ASSESSMENT_REMAP_NO_SURVIVOR_TEXT``), and that constant's own comment
    says it "contains no clinical claim" and that every word is "a statement
    about what the NODE did". So under ``assessment`` it is excluded. Under
    ``assessment_draft`` the same trial carries the MODEL'S own rejection prose
    (measured on the stored evaluation runs: 14 of 14 such drafts differ from
    the assessment), which is a real clinical explanation and is KEPT.
  * MODEL TEXT KEPT BY THE PIPELINE -- ``REASONS_KEPT_MODEL_TEXT``. Step 2's
    and Step 3's unrecognised and no-criteria branches, the model-declared
    non-evaluation, and Stage 6's unresolved label all keep the model's own
    draft (``ASSESSMENT_KEPT_NOT_EVALUABLE``). Kept under both fields.

A composed ``eligible`` / ``not_eligible`` assessment is not ``not_evaluable``
and is never excluded here.

PROVENANCE CORROBORATES A CONSTRUCTED REASON. A constructed entry never stood
in a model response, so ``emission_index``, ``call_index`` and
``verdict_source`` are None on it (``_unevaluable_entry``; the
``verdict_source`` column note in ``oncotriage/storage/database_logger.py``).
A verdict that names a constructed reason AND carries a non-None value in one
of those fields contradicts itself. It is NOT excluded -- the filter will not
silently drop a sample on evidence that disagrees with itself -- and the
conflict is returned as a note for the caller to record. An ABSENT key is not
a conflict: records written before those fields existed lack them.

A ``not_evaluable`` verdict with NO reason, or with a reason outside the three
classes, is kept and noted. That is the conservative direction for a filter:
scoring a sample it could not classify costs one pair, while dropping one
removes a measurement nobody can see was removed.

THE REASONS ARE RESTATED, NOT IMPORTED. The owner is
``oncotriage/agent/evaluation.py`` (and ``oncotriage/agent/state.py`` for the
Stage 6 member). Importing it would put the whole Stage 5 module behind
``import oncotriage.evaluation.ragas_harness``, which the rater already declines
for the same reason. ``tests/test_campaign_export.py`` imports both sides and
requires the restatement to equal the owner, which is what stops the copy
drifting. This module imports nothing from the project.
"""

import hashlib
import json


TRIAL_VERDICT_NOT_EVALUABLE = "not_evaluable"

RESPONSE_FIELD_ASSESSMENT = "assessment"
RESPONSE_FIELD_DRAFT = "assessment_draft"
RESPONSE_FIELDS = (RESPONSE_FIELD_ASSESSMENT, RESPONSE_FIELD_DRAFT)

REASONS_CONSTRUCTED = (
    "truncation_floor",
    "truncation_split_budget_exhausted",
    "omitted_from_model_response",
    "conflicting_duplicate_answers",
    "per_trial_call_failed",
)
"""The model never answered; ``_unevaluable_entry`` wrote a fixed sentence."""

REASONS_FIXED_CORRECTION_TEXT = (
    "model rejection unsupported by its own criteria arrays",
    "no disqualifying row survived label normalisation",
)
"""A corrected rejection; ``compose_assessment`` stores a fixed sentence."""

REASONS_KEPT_MODEL_TEXT = (
    "trial-level verdict label not recognised",
    "model returned no criteria",
    "model declared this trial not evaluable",
    "trial-level verdict label unresolvable at finalization",
)
"""The stored assessment is the model's own draft."""

EXCLUDE_CONSTRUCTED = "constructed_placeholder"
EXCLUDE_FIXED_CORRECTION = "fixed_correction_text"

PROVENANCE_FIELDS_NONE_ON_CONSTRUCTED = ("emission_index", "call_index",
                                         "verdict_source")

_FILTER_RULE = (
    "faithfulness_placeholder_filter/v1: not_evaluable verdicts are excluded "
    "from faithfulness when not_evaluable_reason is one of the five "
    "pipeline-constructed reasons (corroborated by None emission_index, "
    "call_index and verdict_source) for every response field, or one of the two "
    "fixed correction reasons when the scored field is 'assessment'")

_ALL_CLASSES = (REASONS_CONSTRUCTED, REASONS_FIXED_CORRECTION_TEXT,
                REASONS_KEPT_MODEL_TEXT)
_FLAT = [r for cls in _ALL_CLASSES for r in cls]
if len(set(_FLAT)) != len(_FLAT):
    # A RuntimeError rather than an `assert`, which `python -O` deletes. A
    # reason in two classes would have two answers to "is this a placeholder".
    raise RuntimeError(
        f"faithfulness_filter reason classes overlap: "
        f"{sorted({r for r in _FLAT if _FLAT.count(r) > 1})}")


FILTER_IDENTITY = _FILTER_RULE + " | classes sha256:" + hashlib.sha256(
    json.dumps([REASONS_CONSTRUCTED, REASONS_FIXED_CORRECTION_TEXT,
                REASONS_KEPT_MODEL_TEXT, PROVENANCE_FIELDS_NONE_ON_CONSTRUCTED,
                RESPONSE_FIELDS]).encode("utf-8")).hexdigest()[:16]
"""What a Ragas resume must agree on. DERIVED from the rule's data, so a reason
moved between classes changes it without anybody remembering to bump a version;
the version word covers what the data cannot, which is the code of ``classify``.
A partial file scored before this filter existed records no such value, so
``ragas_harness.identity_disagreement`` reports it by name and the resume
refuses."""


def classify(verdict, response_field):
    """``(exclusion, note)`` for one verdict and the field being scored.

    ``exclusion`` is None (score it) or ``"<kind>:<reason>"``. ``note`` is None
    or a sentence the caller records as a problem. PURE: it reads one dict.
    """
    if response_field not in RESPONSE_FIELDS:
        raise ValueError(f"unknown response field {response_field!r}; "
                         f"expected one of {RESPONSE_FIELDS}")
    if not isinstance(verdict, dict):
        return None, None
    if verdict.get("eligible") != TRIAL_VERDICT_NOT_EVALUABLE:
        return None, None
    reason = verdict.get("not_evaluable_reason")
    if not reason:
        return None, ("not_evaluable verdict carries no not_evaluable_reason; "
                      "kept for faithfulness because it cannot be classified")
    if reason in REASONS_CONSTRUCTED:
        conflicting = [f for f in PROVENANCE_FIELDS_NONE_ON_CONSTRUCTED
                       if verdict.get(f) is not None]
        if conflicting:
            return None, (f"not_evaluable_reason {reason!r} names a "
                          f"pipeline-constructed entry but {conflicting} are "
                          f"set, which a constructed entry never carries; "
                          f"kept for faithfulness, the record contradicts "
                          f"itself")
        return f"{EXCLUDE_CONSTRUCTED}:{reason}", None
    if reason in REASONS_FIXED_CORRECTION_TEXT:
        if response_field == RESPONSE_FIELD_ASSESSMENT:
            return f"{EXCLUDE_FIXED_CORRECTION}:{reason}", None
        return None, None
    if reason in REASONS_KEPT_MODEL_TEXT:
        return None, None
    return None, (f"not_evaluable_reason {reason!r} is not a value this "
                  f"filter classifies; kept for faithfulness")


def exclusion_counts(samples):
    """``{exclusion: count}`` over samples carrying ``faithfulness_exclusion``.

    Derived from the samples rather than accumulated while loading, so a
    ``--limit`` applied after loading is reflected in what is reported.
    """
    counts = {}
    for sample in samples:
        key = getattr(sample, "faithfulness_exclusion", None)
        if key is not None:
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep 14 2026

@author: ramyalsaffar
"""
