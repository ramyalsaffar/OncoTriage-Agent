"""
WHICH STAGE 5 ARM PRODUCED THESE ROWS, AND HOW TO SAY SO ON A PANEL.

THE DEFECT THIS EXISTS TO REMOVE. ``MATCHING_PER_TRIAL_CALLS_ENABLED`` ships
``True``, so Stage 5 sends ONE billed request per patient-trial pair behind a
dedicated warmup; grouped mode — the retained comparison arm — sends between one
and ``MATCHING_MAX_INPUT_PACKED_CHUNKS`` requests for the whole patient. Those
two arms put wildly different numbers in the SAME columns:

    llm_classifier_input_tokens   per-trial carries the shared prefix once per
                                  trial call PLUS the warmup, so a 15-trial
                                  patient pays ~16 prompt renderings; grouped
                                  pays between 1 and 5.
    llm_classifier_calls          1..5 grouped, 1 + trials per-trial.
    estimated_cost_usd            follows the input figure.

So ``df['llm_classifier_input_tokens'].mean()`` over a table holding both arms
is a mean over two populations that are not the same measurement, and every
figure derived from it — an average, a median, a 1000-patient projection, a
per-trial ratio — is a number about nothing. Nothing raises, nothing is NULL,
and the panel looks exactly as it did before the default flipped.

THIS MODULE DOES NOT DECIDE WHAT A PANEL DOES ABOUT IT. It answers "which arms
are in this frame, in what proportion, and is that safe to average", and it
renders that answer as a label. Each tab applies its own policy at its own call
site, which is the shape ``oncotriage/agent/readiness.py``'s four-state index
vocabulary already has: one owner for the reading, the policy written where the
consequence is.

THREE STATES, NOT TWO, AND THE THIRD IS NOT A DEFECT.

  * the COLUMN IS ABSENT — the database predates era 3 entirely. It gains the
    column on the next write; nothing is wrong with the rows already there.
  * the column is present and the VALUE IS NULL — that row was written before
    era 3 by a database that has since been migrated forward. Also not a defect.
  * the value is one of ``config.MATCHING_CALL_MODES``.

The first two are folded into ONE bucket for grouping — an operator's question
is "can I average this", and the answer is no in both cases — but they are
DISTINGUISHED in the caption, because the remedies differ and because a reader
told "the column is not in this database" goes looking for a different thing
than one told "these rows predate it".

THE NOT-RECORDED LABEL IS IMPORTED, NOT RETYPED. ``queries.MODE_NOT_RECORDED_LABEL``
is what ``call_mode_comparison`` and ``stage5_cache_effectiveness`` COALESCE to,
and a dashboard bucket spelled differently from the query layer's is two names
for one fact — the shape this project removes wherever it finds it. A reader
putting File 16's arm table beside this dashboard must see the same bucket name.

MIXED MEANS MORE THAN ONE BUCKET, INCLUDING THE NOT-RECORDED ONE. A frame that
is half per-trial and half unrecorded is exactly as unsafe to average as one
that is half per-trial and half grouped: in both cases the mean is taken over
two populations and only one of them is described. Treating the unrecorded
bucket as "probably the same arm" would be inventing the fact this module
exists to report.
"""

import json

import pandas as pd

from oncotriage import config
from oncotriage.storage.queries import MODE_NOT_RECORDED_LABEL


# The column the reading comes from. Written once because three tabs test for
# its presence and a retyped string is a tab that silently reports
# "not recorded" for a database that records it perfectly well.
MODE_COLUMN = "matching_call_mode"


# HOW EACH ARM IS SPELLED ON SCREEN. The stored values are `grouped` and
# `per_trial` -- config.MATCHING_CALL_MODES, and the keys here are derived from
# that tuple at import rather than typed, so a third arm cannot be added to the
# pipeline and silently render as its raw storage value.
#
# The VALUES are display strings and may differ from the storage spelling
# (`per_trial` reads badly in a chart title); nothing joins on them.
_DISPLAY_OVERRIDES = {"per_trial": "per-trial"}
MODE_DISPLAY = {m: _DISPLAY_OVERRIDES.get(m, m) for m in config.MATCHING_CALL_MODES}

# The order buckets are reported in: the declared arms in config's order, then
# the not-recorded bucket last. Deterministic, so two renders of one frame
# cannot disagree about column order -- determinism is a stated property of this
# pipeline and a dashboard is not exempt from it.
BUCKET_ORDER = tuple(MODE_DISPLAY[m] for m in config.MATCHING_CALL_MODES) + (
    MODE_NOT_RECORDED_LABEL,)


# The closed key set `describe()` returns. Declared so a consumer can branch
# over it exhaustively rather than testing for keys, and so a key added here
# without a reader is visible to tests/test_package_invariants.py check 2h.
MIX_FIELDS = ("column_present", "counts", "buckets", "is_mixed", "rows",
              "unrecorded_rows", "sole_bucket")


def bucket_of(value):
    """One stored value -> its display bucket. Never raises.

    PUBLIC because a single-row consumer needs it. ``describe`` answers about a
    FRAME; ``oncotriage/dashboard/tabs/patient_explorer.py`` renders exactly one
    row and needs the same mapping for it, and a second copy of the NULL /
    unrecognised-value rules at that call site is a second place for them to
    disagree with the buckets every other panel groups by.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return MODE_NOT_RECORDED_LABEL
    try:
        text = str(value)
    except Exception:
        return MODE_NOT_RECORDED_LABEL
    # An UNRECOGNISED value is reported AS ITSELF and never folded into the
    # not-recorded bucket. `matching_call_mode` is written from
    # config.matching_call_mode(), whose vocabulary is closed and validated at
    # import -- so a value outside it means something wrote this table that this
    # code does not know about, and hiding it under "(not recorded)" would
    # report a real finding as an absence. It also makes the frame MIXED, which
    # is the honest consequence.
    return MODE_DISPLAY.get(text, text)


def describe(df):
    """What arms are in ``df``? Returns a dict with exactly ``MIX_FIELDS``.

    Raises nothing and reads nothing but the frame. A frame with no rows is
    reported as ``rows == 0`` with an empty ``counts``; callers render that as
    an empty state rather than as a mode finding.
    """
    rows = int(len(df))
    present = MODE_COLUMN in getattr(df, "columns", [])

    if not present or rows == 0:
        counts = ({MODE_NOT_RECORDED_LABEL: rows} if rows else {})
    else:
        raw = df[MODE_COLUMN].map(bucket_of).value_counts()
        # BUCKET_ORDER first, then anything unrecognised in sorted order, so
        # the ordering is total and deterministic even for a value this code
        # has never seen.
        known = [b for b in BUCKET_ORDER if b in raw.index]
        extra = sorted(str(b) for b in raw.index if b not in BUCKET_ORDER)
        counts = {b: int(raw[b]) for b in known + extra}

    buckets = [b for b, n in counts.items() if n]
    return {
        "column_present": present,
        "counts": counts,
        "buckets": buckets,
        "is_mixed": len(buckets) > 1,
        "rows": rows,
        "unrecorded_rows": int(counts.get(MODE_NOT_RECORDED_LABEL, 0)),
        "sole_bucket": buckets[0] if len(buckets) == 1 else None,
    }


def label_suffix(mix):
    """A short clause to append to a chart title or a metric label.

    Returns ``""`` for an empty frame -- there is nothing to qualify -- so a
    caller can concatenate unconditionally.
    """
    if not mix["rows"]:
        return ""
    if mix["sole_bucket"] is not None:
        return f" ({mix['sole_bucket']})"
    parts = ", ".join(f"{b} {mix['counts'][b]:,}" for b in mix["buckets"])
    return f" (MIXED CALL MODES: {parts})"


def caption(mix, what="figure"):
    """The sentence that goes under a panel whose numbers the mix qualifies.

    ``what`` names the thing being qualified, so one owner serves "these
    averages", "this histogram" and "this projection" without three copies of
    the paragraph.
    """
    if not mix["rows"]:
        return "No rows in this selection."

    if mix["sole_bucket"] == MODE_NOT_RECORDED_LABEL:
        if not mix["column_present"]:
            return (
                f"**Stage 5 call mode is not recorded in this database.** The "
                f"`{MODE_COLUMN}` column was added in schema era 3 and this file "
                f"predates it; it will be added by the next run that opens it. "
                f"Every {what} here is over rows whose arm is unknown, so it "
                f"cannot be compared with a per-trial or grouped figure."
            )
        return (
            f"**Stage 5 call mode is not recorded on any of these "
            f"{mix['rows']:,} rows.** They were written before schema era 3. "
            f"Every {what} here is over rows whose arm is unknown."
        )

    if not mix["is_mixed"]:
        return (
            f"All {mix['rows']:,} rows in this selection ran Stage 5 in "
            f"**{mix['sole_bucket']}** mode, so every {what} below is a "
            f"statement about that arm."
        )

    parts = ", ".join(f"**{b}** {mix['counts'][b]:,}" for b in mix["buckets"])
    return (
        f"**This selection MIXES Stage 5 call modes** ({parts} of "
        f"{mix['rows']:,} rows). The two arms put different quantities in the "
        f"same token, call-count and cost columns — per-trial sends one billed "
        f"request per patient-trial pair plus a cache warmup, grouped sends one "
        f"per packed chunk — so any {what} blended across them is a mean over "
        f"two populations. Split by mode, or filter to one, before comparing."
    )


def split(df):
    """``df`` -> ``[(bucket, sub_frame), ...]`` in ``BUCKET_ORDER``.

    An empty frame yields ``[]``. A frame with no mode column yields a single
    not-recorded pair holding every row, so a caller that always splits renders
    the same panel it would have rendered anyway rather than an empty one.
    """
    if not len(df):
        return []
    if MODE_COLUMN not in df.columns:
        return [(MODE_NOT_RECORDED_LABEL, df)]

    keyed = df[MODE_COLUMN].map(bucket_of)
    mix = describe(df)
    return [(b, df[keyed == b]) for b in mix["buckets"]]


def annotate(df):
    """Return a COPY of ``df`` carrying a ``call_mode_label`` display column.

    A copy, never an in-place assignment: the frame handed to a tab is the
    sidebar-filtered selection that every other tab on the page also holds, and
    ``@st.cache_data`` returns the SAME object across reruns — mutating it would
    leak a column into panels that never asked for one and would accumulate
    across reruns. This is the ``MATCH_TIERS`` hazard that
    ``tests/test_package_invariants.py`` section 6a exists to catch, one frame
    over.
    """
    out = df.copy()
    if MODE_COLUMN in out.columns:
        out["call_mode_label"] = out[MODE_COLUMN].map(bucket_of)
    else:
        out["call_mode_label"] = MODE_NOT_RECORDED_LABEL
    return out


# ===========================================================================
# THE PER-CALL LEDGER
# ===========================================================================
# `inferences.llm_classifier_call_details` is the ONLY column that can answer a
# per-call question, and the storage layer says so at its own declaration: a
# summed figure of 5,000 cached tokens across three calls is equally consistent
# with a cache that warms after the first request and one that never warms.
#
# WHY A PER-CALL FIGURE MUST NOT BE DIVIDED OUT OF A PATIENT TOTAL. The obvious
# arithmetic -- `llm_classifier_input_tokens / llm_classifier_calls` -- is wrong
# for a per-trial row in a way that has no symptom: one of those calls is the
# WARMUP, which carries the whole shared prefix and a one-token output ceiling,
# so it drags the "average call" down while being infrastructure rather than an
# evaluation. It is wrong for a grouped row too, where the chunks are not equal
# sized. The ledger records what each request actually reported, so nothing has
# to be inferred.

# The warmup row is marked `warmup: True` by Stage 5 and NO OTHER ROW CARRIES
# THE KEY AT ALL (the absent-rather-than-empty convention `unconsumed` follows),
# so this reads presence and never truthiness of a value that may not be there.
WARMUP_KIND = "warmup (infrastructure)"
EVALUATION_KIND = "evaluation"

LEDGER_ABSENT = "absent"
"""Stage 5 was never entered -- the column is NULL. Not the same as `[]`."""

LEDGER_EMPTY = "empty"
"""Stage 5 ran and no call produced a usage object: the first request raised."""

LEDGER_UNREADABLE = "unreadable"
"""The column holds something that is not a JSON list of objects."""

LEDGER_ROWS = "rows"
"""The ledger decoded and holds at least one request.

NAMED LIKE ITS THREE SIBLINGS RATHER THAN LEFT A LITERAL. A closed vocabulary
whose members are three constants and one bare string is one a consumer cannot
branch over consistently, and the literal is the member that gets retyped at a
call site and then quietly stops matching."""

LEDGER_STATES = (LEDGER_ABSENT, LEDGER_EMPTY, LEDGER_UNREADABLE, LEDGER_ROWS)
"""Closed, so a caller may branch over it exhaustively."""


def ledger(raw):
    """Decode one row's ``llm_classifier_call_details``. Raises nothing.

    Returns ``{"state": <one of LEDGER_STATES>, "rows": [...], "calls": int,
    "warmup_calls": int, "evaluation_calls": int}``.

    NULL AND ``[]`` ARE DIFFERENT ANSWERS and are kept different, which is the
    storage layer's own rule for this column: NULL means the node was never
    entered, ``[]`` means it ran and counted no usage. Rendering both as "0
    calls" would report a stage that never ran as a stage that made no request.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return {"state": LEDGER_ABSENT, "rows": [], "calls": 0,
                "warmup_calls": 0, "evaluation_calls": 0}

    if isinstance(raw, (list, tuple)):
        decoded = list(raw)
    else:
        try:
            decoded = json.loads(raw)
        except Exception:
            return {"state": LEDGER_UNREADABLE, "rows": [], "calls": 0,
                    "warmup_calls": 0, "evaluation_calls": 0}

    if not isinstance(decoded, list):
        return {"state": LEDGER_UNREADABLE, "rows": [], "calls": 0,
                "warmup_calls": 0, "evaluation_calls": 0}
    if not decoded:
        return {"state": LEDGER_EMPTY, "rows": [], "calls": 0,
                "warmup_calls": 0, "evaluation_calls": 0}

    rows = []
    for entry in decoded:
        if not isinstance(entry, dict):
            # A member that is not an object is reported as unreadable rather
            # than skipped: a ledger this code cannot read whole is not a
            # ledger it should render part of.
            return {"state": LEDGER_UNREADABLE, "rows": [], "calls": 0,
                    "warmup_calls": 0, "evaluation_calls": 0}
        rows.append(dict(entry, _kind=(WARMUP_KIND if "warmup" in entry
                                       else EVALUATION_KIND)))

    warmups = sum(1 for r in rows if r["_kind"] == WARMUP_KIND)
    return {"state": LEDGER_ROWS, "rows": rows, "calls": len(rows),
            "warmup_calls": warmups, "evaluation_calls": len(rows) - warmups}


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Aug 25 2026

@author: ramyalsaffar
"""
