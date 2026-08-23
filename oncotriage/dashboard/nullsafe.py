"""
Rendering a cell that may be NULL, for every dashboard tab.

WHY THIS MODULE EXISTS
----------------------
`inferences` and `trial_matches` are full of columns that are legitimately NULL,
and there are three separate reasons a cell can be empty, none of which is an
error:

  * an ADDITIVE column (`INFERENCE_COLUMN_ADDITIONS`) that a row written before
    the column existed does not carry;
  * a stage that did not run -- a no-candidates patient records no evaluation
    counts, an error-handler row records almost nothing;
  * a measurement that was deliberately not made, which this project separates
    from a measured zero everywhere it writes one.

A dashboard tab that reaches for ``int(row['candidates_retrieved'])`` on such a
cell does not render a blank -- IT RAISES, and a raise inside ``main()`` takes
the WHOLE PAGE down, every tab, before the reader sees anything. That is the
defect this module exists to remove: one sparse row in a 22,000-patient corpus
should cost one em dash, not the dashboard.

``pd.isna`` AND NOT ``is None``, EVERYWHERE
------------------------------------------
A column holding numbers for some rows and SQL NULL for others arrives from
pandas as float64, so a NULL is ``nan`` -- which is TRUTHY, is not equal to
``None``, and raises ``ValueError`` from ``int()`` rather than the
``TypeError`` a ``None`` raises. Item 38 had to fix exactly that confusion in
the cost arithmetic (``int(x or 0)`` on a NULL SUM), and every helper here is
written against both shapes.

THE DEFAULTS ARE ARGUMENTS, NOT POLICY
--------------------------------------
Nothing here decides what an absent value MEANS -- the call site does, because
only the call site knows. ``optional_int_text`` exists precisely so that a
column whose NULL means "never measured" cannot be rendered as ``0``, which is
the measured answer; ``as_int`` exists for the columns where a default really is
a reading. Choosing between them is the whole judgement, and it is made where
the column is known.

WHAT THIS MODULE IMPORTS FROM THE PROJECT
-----------------------------------------
Nothing, on ``oncotriage/dashboard/tiers.py``'s footing. It is a leaf, so any
tab may use it without creating an edge between tabs.
"""

import pandas as pd


# The em dash every tab already used for "this cell holds nothing". Named once
# so a tab cannot render a different glyph for the same finding -- a reader
# learns one mark, not four.
ABSENT_TEXT = "—"

# What a text column renders as when it is NULL. Longer than the em dash on
# purpose: a missing NUMBER in a metric tile reads fine as a dash beside its
# label, while a missing free-text field beside other free text does not.
ABSENT_LABEL = "(not recorded)"


def is_absent(value) -> bool:
    """True when `value` is SQL NULL, ``None``, ``nan`` or ``pd.NaT``.

    THE ``isinstance(str)`` GUARD IS LOAD-BEARING, not defensive noise.
    ``pd.isna`` on a list or an ndarray returns an ARRAY, and ``bool()`` of an
    array raises ``ValueError: truth value of an array is ambiguous`` -- so a
    column holding a JSON list would turn this helper into the very crash it
    exists to prevent. A string and a container are answered directly and
    everything else goes to pandas.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return False
    if isinstance(value, (list, tuple, dict, set)):
        return False
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    if isinstance(result, bool):
        return result
    # An ndarray or a Series: not a scalar, so not an absent scalar.
    return False


def is_boolean(value) -> bool:
    """True for a Python ``bool`` AND for a numpy/pandas boolean.

    ``isinstance(value, bool)`` IS NOT ENOUGH AND THAT IS NOT PEDANTRY. A pandas
    column of dtype ``bool`` yields ``numpy.bool_`` on every element read, and
    ``numpy.bool_`` is NOT a subclass of ``bool`` -- so the three helpers below
    would have laundered ``True`` into the integer 1 for exactly the column
    shape that produces it, while correctly refusing a hand-written ``True``.
    ``pd.api.types.is_bool`` answers both and answers False for every numeric
    type, which is what those helpers actually need to ask.
    """
    return bool(pd.api.types.is_bool(value))


def as_int(value, default=0):
    """An ``int``, or `default` when the cell is absent or not a number.

    ``default`` IS A READING AND THE CALLER MAKES IT. Use this only where zero
    (or whatever default is passed) is the honest answer for an absent cell --
    a funnel stage that a run never reached really did pass zero candidates on.
    Where the absence means "nobody measured this", use ``optional_int_text``:
    rendering that as ``0`` states the measured answer for an unmeasured column,
    which is the one confusion the run tables were given a meta row to remove.

    A ``bool`` is NOT accepted as an integer here even though ``bool`` is an
    ``int`` subclass in Python. ``True`` rendered as ``1`` in a candidate count
    is a number nobody measured, and SQLite has no boolean type, so a column
    that ought to hold counts and holds a bool is a writer defect this must not
    launder.
    """
    if is_absent(value) or is_boolean(value):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        # OverflowError is `int(inf)`. A float column with an infinity in it is
        # not a count, and returning the default is the same reading as NULL.
        return default


def as_float(value, default=0.0):
    """A ``float``, or `default` when the cell is absent or not a number."""
    if is_absent(value) or is_boolean(value):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_text(value, default=ABSENT_LABEL):
    """A display string, or `default` when the cell is absent."""
    if is_absent(value):
        return default
    return str(value)


def optional_int_text(value, default=ABSENT_TEXT):
    """An integer rendered as TEXT, or `default` when the cell is absent.

    A SEPARATE HELPER FROM ``as_int`` BECAUSE THE DEFAULT MUST NOT BE A NUMBER,
    and that is the entire point of it. ``counters_registered`` and
    ``degradation_events`` are the two columns whose NULL means "never
    measured", so rendering either as ``0`` would print the measured-clean
    answer for a run that was never asked. The cell says so instead.
    """
    if is_absent(value) or is_boolean(value):
        return default
    try:
        return str(int(value))
    except (TypeError, ValueError, OverflowError):
        return default


def format_number(value, spec="", default=ABSENT_TEXT):
    """``format(value, spec)``, or `default` when the cell is absent.

    THE F-STRING IS THE CRASH SITE, NOT ONLY ``int()``. ``f"{None:.2f}"`` raises
    ``TypeError: unsupported format string passed to NoneType.__format__``, and
    ``f"{nan:.2f}"`` does NOT raise -- it renders the string ``"nan"`` into a
    metric tile, which is worse than a dash because it looks like a measurement.
    Both are absences and both come out as `default`.
    """
    if is_absent(value):
        return default
    try:
        return format(float(value), spec) if spec else str(value)
    except (TypeError, ValueError):
        return default


def format_timestamp(value, spec="%Y-%m-%d %H:%M", default=ABSENT_TEXT):
    """``value.strftime(spec)``, or `default` when the cell is absent.

    ``pd.NaT`` IS THE CASE THIS EXISTS FOR AND IT DOES NOT BEHAVE LIKE ``nan``.
    ``pd.NaT.strftime(...)`` RAISES ``ValueError: NaTType does not support
    strftime`` rather than returning a marker, so any tab formatting a timestamp
    column that a single unparseable row turned into NaT takes the page down.
    A value with no ``strftime`` at all (a plain string, which is what the
    column holds before ``pd.to_datetime`` runs) is returned as text rather than
    refused.
    """
    if is_absent(value):
        return default
    formatter = getattr(value, "strftime", None)
    if formatter is None:
        return str(value)
    try:
        return formatter(spec)
    except (ValueError, TypeError):
        return default


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug 23 2026

@author: ramyalsaffar
"""
