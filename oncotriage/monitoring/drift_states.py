# Drift Metric States: the two axes a drift reading is reported on
###################################################################

"""What a drift metric ANSWERED and whether that answer may alert.

TWO AXES, STORED SEPARATELY, BECAUSE THEY ARE INDEPENDENT FACTS AND THE
SHIPPED DESIGN CONFLATED THEM INTO ONE INTEGER
-----------------------------------------------------------------------
``drift_metrics.alert`` was written ``0`` by every early return of every
metric -- a PSI over a column of NaNs, a KS test with one sample a side, a
z-score whose baseline had no variance, a metric whose column the database
does not have. Each of those wrote the same byte a healthy metric writes, and
the dashboard rendered every one of them as ``OK``. So the four readings

    "this metric was computed and is inside its threshold"
    "this metric could not be computed"
    "this metric has no data to compute over"
    "this metric does not alert at all, by policy"

arrived at a reader as one value, and the three that are not a verdict were
presented as the one that is. That is worse than a missing metric: a missing
metric is a known hole, and a metric that reports OK because it never ran is
coverage nobody has.

    Axis 1 -- STATUS      what the computation DID. ``METRIC_STATUSES``.
    Axis 2 -- POLICY      whether a reading of this metric may raise an alert
                          at all. ``ALERT_POLICIES``.

    alert is MEANINGFUL only when policy is ``alerting`` AND status is
    ``computed``. ``alert_is_meaningful`` is the one owner of that sentence and
    ``resolve_alert`` is the one place it decides a stored value.

WHY ``alert`` IS ``None`` AND NOT ``0`` WHEN IT IS NOT MEANINGFUL
------------------------------------------------------------------
Because ``0`` is a VERDICT -- "this was measured and it is fine" -- and the
whole defect above is that verdict being written by code that measured
nothing. NULL is the schema's own word for "this column has no value for this
row", every consumer already has to handle it, and ``alert = 1`` /
``alert = 0`` / ``alert IS NULL`` are then three questions a reader can ask in
plain SQL and get three different populations for.

The cost is stated rather than discovered: a consumer that sums ``alert``
gets the same number either way (SQLite's SUM skips NULL), and a consumer that
counts ``WHERE alert = 0`` to mean "healthy metrics" now gets a SMALLER number
than before -- correctly, because the rows it loses were never healthy
readings. Nothing in this repository does the second; the dashboard's tile
counts ``alert == 1``, which is unaffected.

WHY A REPORTING-ONLY ZERO IS NOT AN ``OK``
------------------------------------------
A reporting-only metric can legitimately compute the value ``0.0`` -- an
underfill fraction of zero is a pool that filled completely, which is the best
possible reading. It still must not render as a green tick, because the tick
is the ALERT axis's word and this metric has no alert axis. It renders as a
value under a neutral marker. ``display_state`` is the one owner of that
rendering decision, so the console and the dashboard cannot disagree about it.

DEVIATIONS FROM THE VOCABULARY THIS MODULE WAS ASKED FOR, both recorded rather
than made silently:

  * ``no_reference`` IS NOT A MEMBER. It was requested alongside
    ``reference_absent``, and the two name one state -- a metric that needs a
    reference and has none. The same brief asked for "consistent names", which
    two spellings of one state is the opposite of. The ``reference_*`` family
    is what survived, because it has three members that must be told apart
    and a consistent prefix is what makes them read as a family.
  * ``reference_unresolvable`` AND ``no_run_identity`` ARE MEMBERS AND WERE NOT
    IN THE REQUESTED LIST. Both are required elsewhere in the same brief -- an
    unresolvable reference and a database with no run identity are named there
    as DISTINCT refusals -- so a vocabulary without them would have forced two
    distinct refusals to share a member, which is the conflation this module
    exists to remove.

IT IMPORTS NOTHING FROM THE PROJECT AND NOTHING THAT COSTS ANYTHING. Every
consumer -- the metrics, the logger, the console report, the dashboard tab --
reads these names, so a dependency here would be a dependency everywhere.
"""


#------------------------------------------------------------------------------


# ===========================================================================
# AXIS 1: COMPUTATION STATUS
# ===========================================================================

STATUS_COMPUTED = "computed"
"""The metric ran over sufficient, well-formed data and produced a value.

THE ONLY STATUS WHOSE ``metric_value`` IS A MEASUREMENT. Every other member
below means the value is absent or is not a reading of what the metric names,
and a consumer that averages ``metric_value`` without filtering on this member
is averaging over a population that includes neither.
"""

STATUS_ZERO_VARIANCE = "not_computed_zero_variance"
"""Every input value was identical, so the statistic is undefined or degenerate.

DISTINCT FROM ``insufficient_data`` AND THE DISTINCTION IS THE POINT. There
was plenty of data; it did not vary. A z-score divides by a standard deviation
of zero and a PSI over a single bin is zero by construction rather than by
measurement -- and a stored ``0.0`` for the second is the "healthy zero" this
module exists to stop, because a column that has SATURATED at one value is
usually the most alarming reading a drift metric can take.
"""

STATUS_INSUFFICIENT_DATA = "insufficient_data"
"""Fewer rows survived the metric's own row rule than it needs to answer.

The count that was available and the floor it missed belong in ``notes``; this
member says only that the floor was not met.
"""

STATUS_UNVERIFIED_INPUTS = "unverified_inputs"
"""An input the metric needs was recorded and could not be trusted.

The case this exists for: a denominator read out of a run's recorded tunables
that is missing, malformed, or not a positive number. The metric could have
divided by a default and did not -- a fraction over an assumed denominator is
a number about an assumption.
"""

STATUS_MALFORMED_INPUTS = "malformed_inputs"
"""A column the metric reads is present and holds something it cannot read.

DISTINCT FROM ``unverified_inputs``: that one is about a value used to
CONFIGURE the computation (a denominator, a bound), this one is about the DATA.
A ``candidates_retrieved`` holding the string ``"many"`` lands here.
"""

STATUS_READ_FAILURE = "read_failure"
"""The metric raised while computing, and the exception is in ``notes``.

NEVER SILENTLY A ZERO. Every metric in this package wraps its body, and the
shipped code turned every one of those catches into ``alert: 0`` with the
message in a text field nothing rendered.
"""

STATUS_NO_DATA = "no_data"
"""The metric's inputs are present in the schema and no row has ever carried one.

DISTINCT FROM ``insufficient_data``, which is "some rows, not enough", and from
``refused_column_absent``, which is "the schema does not have this column at
all". This one is the never-observed column: the pipeline can write it and has
not. A rate over it would be 0/0.
"""

STATUS_REFERENCE_ABSENT = "reference_absent"
"""No reference has been designated, so a comparison metric has nothing to
compare against.

AN OPERATOR ACTION, NOT A FAULT: the remedy is to designate one. It is a
REFUSAL rather than a fallback because the fallback this replaces -- take the
oldest rows in a time window and call them the baseline -- is what made every
comparison a statement about whichever rows happened to be first.
"""

STATUS_REFERENCE_UNRESOLVABLE = "reference_unresolvable"
"""A reference IS designated and its rows could not be read back.

Rows deleted, the database replaced, a column the digest covers dropped. The
designation names what it expected; ``notes`` names what was found.
"""

STATUS_REFERENCE_MUTATED = "reference_mutated"
"""The designated row ids are all present and their CONTENT has changed.

THE STATE THE DIGEST EXISTS FOR. Counts and ids alone cannot see an UPDATE, so
a reference whose values were edited in place would go on being compared
against as though it were the thing that was designated.
"""

STATUS_NO_RUN_IDENTITY = "no_run_identity"
"""The database cannot say which run or campaign a row belongs to.

No ``runs`` table, or no ``inferences.run_id``. Every selection this module
supports is defined over a campaign, so there is nothing to select and the
honest answer is the refusal rather than "all rows".
"""

STATUS_REFUSED_INCOMPARABLE = "refused_incomparable"
"""The two populations exist and may not be compared.

Different configuration where the metric requires the same one, or no surviving
pair at all. Reported rather than computed over whatever overlapped.
"""

STATUS_REFUSED_COLUMN_ABSENT = "refused_column_absent"
"""A column this metric reads is not in the database's schema.

DISTINCT FROM ``no_data``: that column exists and is empty, this one is not
there. The two have different remedies -- run the pipeline, versus migrate the
database -- and one member for both would send an operator to the wrong one.
"""

METRIC_STATUSES = (
    STATUS_COMPUTED,
    STATUS_ZERO_VARIANCE,
    STATUS_INSUFFICIENT_DATA,
    STATUS_UNVERIFIED_INPUTS,
    STATUS_MALFORMED_INPUTS,
    STATUS_READ_FAILURE,
    STATUS_NO_DATA,
    STATUS_REFERENCE_ABSENT,
    STATUS_REFERENCE_UNRESOLVABLE,
    STATUS_REFERENCE_MUTATED,
    STATUS_NO_RUN_IDENTITY,
    STATUS_REFUSED_INCOMPARABLE,
    STATUS_REFUSED_COLUMN_ABSENT,
)
"""Every status a metric may report. CLOSED.

A consumer may branch on it exhaustively, and ``require_status`` is what makes
that safe: a member added without being declared here fails at the write rather
than arriving at a dashboard that renders it under an ``else``.
"""

STATUSES_WITH_VALUE = frozenset({STATUS_COMPUTED})
"""The statuses whose ``metric_value`` is a reading.

A FROZENSET OF ONE, and it is a set rather than an ``== STATUS_COMPUTED`` at
each call site so that "which statuses carry a value" is a question with one
answer in one place. If a future status ever carries a partial value it joins
this set and every consumer follows.
"""


#------------------------------------------------------------------------------


# ===========================================================================
# AXIS 2: ALERT POLICY
# ===========================================================================

POLICY_ALERTING = "alerting"
"""This metric's reading is compared against a calibrated threshold.

RESERVED, AND AT THE TIME OF WRITING EXACTLY ONE METRIC USES IT:
``ecog_unavailable_rate``, whose threshold is baseline-independent. Every
comparison metric ships ``reporting_only`` by ruling, because none of their
thresholds has been calibrated against this pipeline's own data and an
uncalibrated threshold produces alerts an operator learns to ignore.
"""

POLICY_REPORTING_ONLY = "reporting_only"
"""This metric is computed, stored and rendered, and NEVER raises an alert.

NOT A LESSER METRIC. A reporting-only reading is a number a human compares
across runs; what it lacks is a machine's opinion about whether the number is
bad. The shipped design had the opposite arrangement -- a threshold on every
comparison metric and no calibration behind any of them.
"""

POLICY_BASELINE_INDEPENDENT = "baseline_independent"
"""This metric alerts against an absolute threshold and consults no reference.

SEPARATE FROM ``alerting`` BECAUSE IT ANSWERS A DIFFERENT QUESTION ABOUT THE
SAME ROW. Both may alert; only this one is still meaningful when the reference
is absent, unresolvable or mutated. That is what lets a reference failure
downgrade the metrics that consume a reference and leave this one alone, which
is a property a reader has to be able to CHECK rather than take on trust.
"""

ALERT_POLICIES = (
    POLICY_ALERTING,
    POLICY_REPORTING_ONLY,
    POLICY_BASELINE_INDEPENDENT,
)
"""Every alert policy. CLOSED, for ``METRIC_STATUSES``' reason."""

ALERTING_POLICIES = frozenset({POLICY_ALERTING, POLICY_BASELINE_INDEPENDENT})
"""The policies under which an alert is a possible outcome.

DERIVED-AT-ONE-PLACE rather than written as ``policy != reporting_only``:
adding a fourth policy that does not alert would otherwise silently start
alerting.
"""


#------------------------------------------------------------------------------


# ===========================================================================
# THE GATE
# ===========================================================================

class UnknownDriftStateError(RuntimeError):
    """A status or policy outside its closed vocabulary reached a writer.

    A ``RuntimeError`` subclass and deliberately NOT a ``ValueError``, on
    ``UnknownModelPricingError``'s precedent in ``oncotriage/utils.py``: a
    broad ``except ValueError`` around a metric body must not be able to eat
    the one exception that says the vocabulary was violated.
    """


def require_status(status):
    """``status`` if it is a member of ``METRIC_STATUSES``; raise otherwise.

    THE VOCABULARY IS ONLY CLOSED IF SOMETHING CLOSES IT. Without this, a typo
    -- ``"insufficient"`` for ``"insufficient_data"`` -- reaches the database,
    every consumer's exhaustive branch falls through to its default, and the
    metric renders as whatever the default happens to be.
    """
    if status not in METRIC_STATUSES:
        raise UnknownDriftStateError(
            f"{status!r} is not a member of METRIC_STATUSES "
            f"{METRIC_STATUSES!r}. A drift metric's status is a closed "
            f"vocabulary; every consumer branches on it exhaustively.")
    return status


def require_policy(policy):
    """``policy`` if it is a member of ``ALERT_POLICIES``; raise otherwise."""
    if policy not in ALERT_POLICIES:
        raise UnknownDriftStateError(
            f"{policy!r} is not a member of ALERT_POLICIES "
            f"{ALERT_POLICIES!r}.")
    return policy


def alert_is_meaningful(status, policy):
    """Whether an alert value means anything for this (status, policy).

    THE ONE OWNER OF THE SENTENCE "alert is meaningful only when policy is
    alerting AND status is computed". Four consumers ask it -- the writer, the
    console report, the dashboard's tiles and the dashboard's table -- and four
    copies of a two-term conjunction is four chances for one of them to keep
    reading ``alert == 0`` as OK.

    Both arguments are validated rather than merely read: this function is what
    stands between a typo and a rendered verdict.
    """
    require_status(status)
    require_policy(policy)
    return policy in ALERTING_POLICIES and status in STATUSES_WITH_VALUE


ALERT_PRESENT = "present"
ALERT_MISSING = "missing"
ALERT_INVALID = "invalid"

ALERT_OUTCOMES = (ALERT_PRESENT, ALERT_MISSING, ALERT_INVALID)
"""What a candidate alert value turned out to BE. CLOSED.

THREE AND NOT TWO, AND THE THIRD IS WHY THIS VOCABULARY EXISTS. ``resolve_alert``
used to end ``return int(bool(alert))``, which has no third outcome at all: every
value that is not a verdict was MANUFACTURED into one, and which one it became
was decided by Python's truthiness table rather than by anything a metric
measured.

    MEASURED, on the shipped function, before it was changed:

        None   -> 0  -> rendered OK       a MISSING verdict became a clean one
        nan    -> 1  -> rendered ALERT    a MISSING verdict became an alarm
        'yes'  -> 1  -> rendered ALERT
        ''     -> 0  -> rendered OK
        2      -> 1  -> rendered ALERT
        -1     -> 1  -> rendered ALERT
        []     -> 0  -> rendered OK
        [0]    -> 1  -> rendered ALERT

The first two are the whole defect this module exists to remove, arriving
through the module that removes it. ``nan`` is the one that actually happens:
an INTEGER column holding NULLs reads back out of pandas as float64, so a NULL
alert round-tripping through a frame and back into a writer arrives as ``nan``
-- and ``bool(nan)`` is True.

MISSING AND INVALID ARE DIFFERENT FINDINGS AND ARE NOT FOLDED TOGETHER. Missing
is the ordinary state of a reading that has no verdict: it stays missing, end to
end, and renders as its true state. Invalid is a metric handing this module
something that is not a verdict at all, which is a DEFECT IN THE CALLER -- so
the reading is rejected as ``malformed_inputs`` rather than being stored under
whatever status the caller claimed.
"""


def classify_alert(alert):
    """Which member of ``ALERT_OUTCOMES`` this value is, and the verdict if any.

    Returns ``(outcome, value)``. ``value`` is ``0`` or ``1`` when the outcome
    is ``present`` and ``None`` otherwise.

    WHAT COUNTS AS A VERDICT IS AN ALLOWLIST, NOT A TRUTHINESS TEST. Exactly
    ``True``/``False``, the ints ``0``/``1``, and the floats ``0.0``/``1.0`` --
    the last because an INTEGER SQLite column holding NULLs reads back through
    pandas as float64, so a legitimately-stored ``1`` arrives as ``1.0`` and
    refusing it would reject the database's own round trip. ``2`` is NOT a
    verdict: this column holds two values and a third is a caller that has
    misunderstood it.

    WHAT COUNTS AS MISSING IS DETECTED WITHOUT IMPORTING pandas OR numpy, which
    this module may not do -- every consumer reads these names, so a dependency
    here is a dependency everywhere. Three shapes:

      * ``None`` -- the ordinary absence.
      * a value that is not equal to itself -- IEEE NaN, in float or numpy form.
      * a value whose ``!=`` does not answer a plain ``bool`` -- which is
        ``pandas.NA``: its comparisons return ``NA``, and that is the library
        saying "unknown". A missing value whose own equality is unknown is
        still a missing value, and reading it as INVALID would report a
        DEFECT where there is only an absence.
    """
    if alert is None:
        return ALERT_MISSING, None

    # NaN and pandas.NA, without importing either library. `x != x` is True for
    # IEEE NaN; for pandas.NA it returns NA, whose bool() raises -- so an
    # ambiguous answer is read as missing rather than as a defect.
    try:
        if bool(alert != alert):
            return ALERT_MISSING, None
    except Exception:                                      # noqa: BLE001
        return ALERT_MISSING, None

    # bool FIRST. `isinstance(True, int)` is True in Python, so an int test
    # placed above this one would answer for True and False as though they were
    # the integers 1 and 0 -- which gives the same result here and is the trap
    # this project has had to exclude explicitly in five other places.
    if isinstance(alert, bool):
        return ALERT_PRESENT, int(alert)

    # `int(alert) == alert` rather than `isinstance(alert, int)`: numpy's
    # integer and floating scalars are neither `int` nor `float` subclasses on
    # every platform, and this is what a frame hands back.
    try:
        as_int = int(alert)
        exact = (as_int == alert)
    except Exception:                                      # noqa: BLE001
        return ALERT_INVALID, None
    if not exact or as_int not in (0, 1):
        return ALERT_INVALID, None
    return ALERT_PRESENT, as_int


def resolve_alert(status, policy, alert):
    """What to STORE in ``drift_metrics.alert``, and whether the input was one.

    Returns ``(value, outcome)``:

        (0 or 1, "present")   a real verdict this reading has earned
        (None,   "missing")   no verdict -- and it STAYS missing, end to end
        (None,   "invalid")   not a verdict at all; the CALLER must reject the
                              reading as ``malformed_inputs``

    A TUPLE AND NOT A BARE VALUE, AND THAT IS THE FIX RATHER THAN A STYLE
    CHOICE. The previous signature returned one value, so "I could not read
    this" had nowhere to go and became ``int(bool(...))`` -- a manufactured
    verdict. A function that can fail to answer must make its caller handle the
    failure, and a two-member return is the smallest thing that does.

    ``None`` whenever ``alert_is_meaningful`` is False, WHATEVER the metric
    passed. That is deliberate belt-and-braces: a metric that computes an alert
    and then reports a status of ``insufficient_data`` has contradicted itself,
    and the safe reading of a contradiction is the one that does not assert a
    verdict. The outcome is still the INPUT's, so a caller can tell a
    suppressed-but-valid alert from a suppressed invalid one.
    """
    outcome, value = classify_alert(alert)
    if not alert_is_meaningful(status, policy):
        return None, outcome
    if outcome != ALERT_PRESENT:
        return None, outcome
    return value, outcome


#------------------------------------------------------------------------------


# ===========================================================================
# RENDERING
# ===========================================================================

DISPLAY_ALERT = "ALERT"
DISPLAY_OK = "OK"
DISPLAY_REPORTING = "REPORTING"
DISPLAY_NOT_COMPUTED = "NOT COMPUTED"
DISPLAY_REFUSED = "REFUSED"

DISPLAY_STATES = (DISPLAY_ALERT, DISPLAY_OK, DISPLAY_REPORTING,
                  DISPLAY_NOT_COMPUTED, DISPLAY_REFUSED)
"""How a reading presents to a human. CLOSED.

FIVE AND NOT TWO. The shipped renderer had ``ALERT`` and ``OK`` and mapped
everything that was not an alert onto ``OK``.

``REPORTING`` is the member that carries the ruling: a reporting-only metric
with a perfectly good value of ``0.0`` is a VALUE, and rendering it ``OK``
would be this project asserting a verdict it has explicitly deferred.

``REFUSED`` is separated from ``NOT COMPUTED`` because the two have different
audiences: a refusal names something an operator can act on (designate a
reference, migrate the database, resolve the mutation), and a
not-computed names a property of the data that will change on its own when
more of it arrives.
"""

_REFUSAL_STATUSES = frozenset({
    STATUS_REFERENCE_ABSENT,
    STATUS_REFERENCE_UNRESOLVABLE,
    STATUS_REFERENCE_MUTATED,
    STATUS_NO_RUN_IDENTITY,
    STATUS_REFUSED_INCOMPARABLE,
    STATUS_REFUSED_COLUMN_ABSENT,
})
"""The statuses that render ``REFUSED`` -- something a person can act on.

A SUBSET OF ``METRIC_STATUSES`` AND CHECKED TO BE ONE at import, below: a
member here that is not a status is a rendering rule for a state that cannot
occur, and it would be invisible because the branch would simply never fire.
"""

_UNEXPECTED = tuple(s for s in _REFUSAL_STATUSES if s not in METRIC_STATUSES)
if _UNEXPECTED:                                        # pragma: no cover
    raise UnknownDriftStateError(
        f"_REFUSAL_STATUSES names {_UNEXPECTED!r}, which are not in "
        f"METRIC_STATUSES. A rendering rule for a state that cannot occur "
        f"never fires and nothing would report it.")
del _UNEXPECTED


def display_state(status, policy, alert):
    """The member of ``DISPLAY_STATES`` this reading presents as.

    ONE OWNER FOR THE CONSOLE AND THE DASHBOARD. They rendered independently
    before this existed, which is how the dashboard came to print a green tick
    beside a metric the console had already reported as insufficient.

    The order of the branches is the design:

      1. NOT MEANINGFUL FIRST. Whatever ``alert`` holds, a reading whose axes
         say it cannot alert is never rendered as a verdict. This is the branch
         that stops the healthy zero, and it is first so that no later branch
         can reach a reading it should not have.
      2. Then the verdict, for the readings that have earned one.
    """
    require_status(status)
    require_policy(policy)

    if not alert_is_meaningful(status, policy):
        if status in _REFUSAL_STATUSES:
            return DISPLAY_REFUSED
        if status in STATUSES_WITH_VALUE:
            # Computed, and its policy does not alert. THE RULING'S OWN CASE:
            # a value, never an OK.
            return DISPLAY_REPORTING
        return DISPLAY_NOT_COMPUTED

    # THE AXES SAY AN ALERT IS POSSIBLE AND THE VALUE IS ABSENT, WHICH IS A ROW
    # CONTRADICTING ITSELF -- and the safe reading of a contradiction is the one
    # that does not assert a verdict, which is `resolve_alert`'s rule applied on
    # the way out instead of on the way in.
    #
    # `resolve_alert` cannot produce this pair, so nothing this project WRITES
    # reaches here. What does: a hand-edited row, a partial write, a future
    # writer, and -- the one that actually happens -- a pandas frame, where an
    # INTEGER column holding NULLs reads back as float64 and a NULL arrives as
    # `nan`. Without this branch `DISPLAY_ALERT if alert else DISPLAY_OK` takes
    # the falsy path and renders a MISSING verdict as a green tick, which is
    # the defect this module exists to remove, arriving through the database
    # instead of through the code.
    #
    # MEASURED, NOT PREDICTED: the first version of this function returned OK
    # for ('computed', 'baseline_independent', None) and for the same triple
    # with `nan`, and the tab's own `is_absent` normalisation mapped the second
    # onto the first rather than saving it.
    if alert is None or alert != alert:                    # `x != x` is NaN
        return DISPLAY_NOT_COMPUTED

    return DISPLAY_ALERT if alert else DISPLAY_OK


DISPLAY_MARKERS = {
    DISPLAY_ALERT: "\U0001F6A8",       # 🚨
    DISPLAY_OK: "✅",              # ✅
    DISPLAY_REPORTING: "\U0001F4CA",   # 📊  a chart, not a tick: a value
    DISPLAY_NOT_COMPUTED: "—",    # —   an em dash: nothing to report
    DISPLAY_REFUSED: "⛔",         # ⛔  refused, and actionable
}
"""One marker per display state, TOTAL over ``DISPLAY_STATES``.

TOTAL AND CHECKED, below. A missing key would render a reading with no marker
at all, which reads as a blank cell and therefore as nothing wrong.
"""

_MISSING = tuple(s for s in DISPLAY_STATES if s not in DISPLAY_MARKERS)
if _MISSING:                                           # pragma: no cover
    raise UnknownDriftStateError(
        f"DISPLAY_MARKERS is missing {_MISSING!r}; it must be total over "
        f"DISPLAY_STATES.")
del _MISSING


def display_label(status, policy, alert):
    """``"<marker> <STATE>"`` -- what a table cell or a console line shows."""
    state = display_state(status, policy, alert)
    return f"{DISPLAY_MARKERS[state]} {state}"


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Sep 10 2026

@author: ramyalsaffar
"""
