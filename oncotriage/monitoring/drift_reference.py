# Drift Reference: which rows a comparison is measured against
###############################################################

"""A DESIGNATED reference population, and the row membership every metric reads.

WHAT THIS REPLACES, AND WHY A TIME WINDOW WAS NEVER A BASELINE
---------------------------------------------------------------
``get_baseline_and_current_data`` took the EARLIEST rows in ``inferences`` as
the baseline and the LATEST as the comparison. Three things are wrong with
that and the third is fatal:

  1. WHICH ROWS THE PIPELINE IS MEASURED AGAINST WAS DECIDED BY INSERTION
     ORDER. Nobody chose it, nothing recorded it, and it moved every time the
     table grew.
  2. THE TWO WINDOWS COULD STRADDLE A CONFIGURATION CHANGE. A prompt bump, a
     re-index, a model flip -- any of them between the two windows makes every
     difference a difference of configuration, reported as drift.
  3. A BASELINE CAPTURED AFTER THE THING WENT WRONG READS AS NO DRIFT AT ALL.
     That is the failure ``ecog_unavailable_rate`` already refuses to be
     exposed to, in as many words, by being a threshold rather than a
     comparison. This module is that argument generalised: a comparison is
     worth something only if a person chose what it compares against, and the
     choice is recorded well enough that it can be checked later.

WHAT A DESIGNATION RECORDS, AND WHY EACH PART
----------------------------------------------
    anchor_run_id          the run an operator named
    campaign_run_ids       EVERY run of that campaign, resumes included --
                           because a campaign that crashed twice is three
                           `runs` rows and a reference that took one of them
                           would be a third of a cohort
    fingerprint            the full stamp, so a later comparison can ask
                           whether the two populations were produced by the
                           same configuration rather than assume it
    tunables               `runs.tunables`, because the reporting-only
                           denominators (RRF_POOL_SIZE, TOP_K_CANDIDATES) are
                           read from the RUN that produced the row, not from
                           whatever `oncotriage/config.py` says today
    row_ids                the IMMUTABLE membership. Re-deriving it would make
                           the reference "whatever that query returns now",
                           which is the thing being replaced
    row_count/patient_count  redundant by construction, stored anyway: a JSON
                           blob that fails to parse still leaves a refusal able
                           to quote the numbers
    content_digest         over the columns the metrics READ, so an UPDATE is
                           detectable. Ids and counts cannot see one.

FOUR DISTINCT REFUSALS, NEVER A FALLBACK
-----------------------------------------
``REFERENCE_ABSENT`` (nobody designated one), ``REFERENCE_UNRESOLVABLE`` (rows
or columns gone), ``REFERENCE_MUTATED`` (ids intact, content changed) and
``REFERENCE_NO_RUN_IDENTITY`` (the database cannot say which run a row belongs
to). Each names a different remedy, which is the whole reason they are four
members and not one ``None``. ``REFERENCE_READ_FAILURE`` is the fifth and is
about the file rather than the designation.

ROW MEMBERSHIP IS LAYERED, AND THE ORDER IS THE DESIGN
--------------------------------------------------------
    LAYER 1  DEDUP -- the FIRST ATTEMPT per patient in the campaign, MIN(id).
             APPLIED BEFORE ANY SUCCESS FILTERING, which is the half that is
             easy to get backwards: dedup-after-filtering silently promotes a
             patient's SECOND attempt to "first" whenever the first one
             errored, so the population that reports failures would be exactly
             the population with no failures in it.
    LAYER 2  PER-METRIC ELIGIBILITY -- each metric declares its own row rule.
             Availability, the error-rate family and the underfill reporters
             run over the deduplicated rows INCLUDING FAILURES, because a
             failed patient must be visible to the metrics that exist to see
             failures. Stage-5 comparison metrics additionally require a clean
             error and a non-zero `candidates_evaluated`, because a patient
             whose Stage 5 never ran has no Stage 5 number to compare.

IT IMPORTS `oncotriage.storage.database_logger`, WHICH `oncotriage.monitoring`
HAS NOT DONE BEFORE, and the existing rule is not being broken. That rule --
stated at ``drift.resolve_drift_db_path`` -- is that monitoring must not depend
on storage FOR A PATH STRING, because drift detection reads a database rather
than using the logger. This imports the schema owner's VOCABULARY: the campaign
stitch rule and the fingerprint column list. The alternative is a second copy of
both, which is the drift this project spends most of its checks removing. No
path is resolved here: every entry point takes ``db_path`` as a REQUIRED
argument, on ``empty_database(db_path, flag)``'s precedent -- a designation
store that could default to the production database is a designation command
that can designate production by accident.
"""

import json
import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, NamedTuple, Optional, Sequence, Tuple

from oncotriage.registries.primary_cancer import (
    CANCER_GROUP_UNRESOLVED,
    cancer_group_key,
)
from oncotriage.storage.database_logger import (
    RUN_FINGERPRINT_COLUMNS,
    campaign_run_ids,
)


#------------------------------------------------------------------------------


# ===========================================================================
# THE DIGEST
# ===========================================================================

DIGEST_ALGORITHM = "sha256/json-rows/v1"
"""How ``content_digest`` is computed, STORED IN EVERY DESIGNATION ROW.

A NAME AND NOT A COMMENT, on ``evaluation/cohort.py:DRAW_ALGORITHM``'s
precedent: the algorithm is part of what the designation asserts, so a change
to it must be visible in the row rather than silently altering what "mutated"
means for every reference already stored.

    sha256 over the UTF-8 of one compact JSON array per selected row, in
    ASCENDING ROW-ID ORDER, each array being ``[id] + [value per column of
    digest_columns, in that order]``, newline-joined.

WHAT IT IS DETERMINISTIC ACROSS AND WHAT IT IS NOT, stated rather than assumed.
Across machines and processes: yes -- the row order is the id order, the column
order is stored, and ``json.dumps`` with fixed separators has no dict iteration
in it. Across Python MAJOR versions: floats are serialised by ``repr``, which
has been shortest-round-tripping since 3.1, so two CPython 3.x builds agree; a
future change to float repr would move every digest at once, which is loud
rather than silent because every reference would report MUTATED on the same day.
BLOBs are hex-encoded rather than raising, because a column this project writes
as TEXT can legitimately arrive as ``bytes`` from a file some other tool wrote,
and a digest that raised there would report ``read_failure`` for data it could
perfectly well have hashed.
"""

DIGEST_COLUMNS = (
    # --- identity and membership ------------------------------------------
    "patient_id",
    "run_id",
    # --- what the availability metric reads --------------------------------
    "ecog_selection",
    "ecog_value",
    # --- what the population comparison reads ------------------------------
    "age",
    "condition_count",
    "medication_count",
    # --- what the retrieval comparison reads -------------------------------
    "candidates_retrieved",
    "candidates_reranked",
    "candidates_filtered",
    "candidates_evaluated",
    # --- what the performance comparison reads -----------------------------
    "eligible_matches",
    "total_time",
    "error",
    "llm_classifier_retries",
    # --- what the reporting-only underfill metrics read --------------------
    "retrieval_trials_lost",
    # --- what the stratification reads -------------------------------------
    "primary_condition",
    # --- what the pair rule reads ------------------------------------------
    "patient_data_hash",
)
"""The INPUT columns every metric reads, and therefore what the digest covers.

IT IS THE INPUTS AND NOT THE WHOLE ROW, and the difference is the point. A
digest over ``SELECT *`` would report MUTATED for a column no metric reads --
a cost field re-priced, a provenance column backfilled by a later pass -- and
an operator would be sent to re-designate over a change that cannot move a
single number. A digest over too FEW columns is the opposite and worse: a
changed input that no digest covers is a comparison quietly taken against
something other than what was designated.

SO THE LIST IS DERIVED FROM THE METRICS AND CHECKED AGAINST THEM. Each entry
names which metric family reads it. The check that this is complete lives in
the test file, against the metric declarations in ``drift.py`` -- a list a
person maintains is a list that goes stale, and the failure mode of THIS one
is invisible.

``timestamp`` IS DELIBERATELY ABSENT. It is not an input to any metric; it is
when the row was written. Including it would make a re-import of the same
data -- which changes no measurement -- report as a mutation.
"""


def _digest_value(value):
    """One column value, in a form ``json.dumps`` can serialise deterministically."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__bytes__": bytes(value).hex()}
    return value


def content_digest(rows: Sequence[Sequence], columns: Sequence[str]) -> str:
    """The digest of ``rows`` over ``columns``. A pure function of its inputs.

    Args:
        rows: an iterable of sequences, each ``(id, *values)`` with ``values``
            in ``columns`` order. NOT re-sorted here -- the caller's SELECT
            carries ``ORDER BY id``, and re-sorting would hide a caller whose
            order was wrong.
        columns: the column names, recorded alongside the digest so a later
            resolution reads the SAME columns in the SAME order.

    Returns:
        A 64-character lowercase hex sha256.
    """
    hasher = hashlib.sha256()
    for row in rows:
        line = json.dumps([_digest_value(v) for v in row],
                          separators=(",", ":"), ensure_ascii=True)
        hasher.update(line.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


#------------------------------------------------------------------------------


# ===========================================================================
# RESOLUTION OUTCOMES
# ===========================================================================

REFERENCE_OK = "ok"
REFERENCE_ABSENT = "reference_absent"
REFERENCE_UNRESOLVABLE = "reference_unresolvable"
REFERENCE_MUTATED = "reference_mutated"
REFERENCE_NO_RUN_IDENTITY = "no_run_identity"
REFERENCE_READ_FAILURE = "read_failure"

REFERENCE_OUTCOMES = (REFERENCE_OK, REFERENCE_ABSENT, REFERENCE_UNRESOLVABLE,
                      REFERENCE_MUTATED, REFERENCE_NO_RUN_IDENTITY,
                      REFERENCE_READ_FAILURE)
"""Every answer ``resolve_reference`` can give. CLOSED.

FIVE OF THE SIX ARE REFUSALS AND EACH NAMES A DIFFERENT REMEDY -- designate
one; find out what happened to the rows; re-designate over the changed data;
migrate the database; fix the file. One member for all of them would send every
operator to the same page, which is what a silent fallback does with extra
steps.

THE NAMES MATCH ``drift_states``' STATUS MEMBERS ONE FOR ONE where they
overlap, and ``STATUS_FOR_OUTCOME`` below is the mapping rather than an
identity, so the two vocabularies can be read as one by a person and stay
separable by a program.
"""


class ReferenceResolution(NamedTuple):
    """The designated reference, resolved against the database as it is now."""

    outcome: str = REFERENCE_ABSENT
    reference_id: Optional[int] = None
    row_ids: Tuple[int, ...] = ()
    run_ids: Tuple[int, ...] = ()
    fingerprint: Dict = {}
    tunables: Dict = {}
    label: Optional[str] = None
    designated_at: Optional[str] = None
    expected_digest: Optional[str] = None
    actual_digest: Optional[str] = None
    detail: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.outcome == REFERENCE_OK

    def describe(self) -> str:
        """One line naming the reference, for a caption or a console block."""
        if self.reference_id is None:
            return f"reference: {self.outcome}"
        name = self.label or f"#{self.reference_id}"
        return (f"reference {name} (id {self.reference_id}, "
                f"{len(self.row_ids)} rows, runs "
                f"{','.join(str(r) for r in self.run_ids) or '-'}): "
                f"{self.outcome}")


#------------------------------------------------------------------------------


# ===========================================================================
# READING THE DATABASE
# ===========================================================================

def _connect(db_path):
    """A read-only connection, or a raise naming the absent file.

    ``mode=ro`` and NOT a plain ``sqlite3.connect``: that CREATES the file, so
    a reader asking "does this database have a designation" would answer by
    making a database that has nothing at all -- File 41's own recorded defect,
    and ``dashboard/data.py:_readonly_connection``'s stated reason.
    """
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"no database at {db_path}")
    uri = "file:" + os.path.abspath(db_path).replace("?", "%3f") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def table_columns(conn, table) -> Tuple[str, ...]:
    """The column names of ``table``, or ``()`` when the table is absent."""
    return tuple(row[1] for row in
                 conn.execute(f"PRAGMA table_info({table})"))


def has_run_identity(conn) -> Tuple[bool, str]:
    """Whether this database can say which run a row belongs to.

    Returns ``(ok, detail)``. Both halves are needed: the refusal has to name
    WHICH half is missing, because "no runs table" and "no run_id column" are
    different migrations.
    """
    missing = []
    if not table_columns(conn, "runs"):
        missing.append("table runs")
    if "run_id" not in table_columns(conn, "inferences"):
        missing.append("column inferences.run_id")
    if missing:
        return False, "absent: " + ", ".join(missing)
    return True, "runs and inferences.run_id are present"


def _json_or_default(text, default):
    """``json.loads(text)`` that answers ``default`` rather than raising.

    A designation's blobs are written by this module and read by this module,
    so a parse failure means the row was edited or truncated. The caller turns
    that into ``REFERENCE_UNRESOLVABLE`` with the raw text quoted; it must not
    become a traceback out of a reader whose contract is that it reports.
    """
    if text is None:
        return default
    try:
        return json.loads(text)
    except Exception:                                      # noqa: BLE001
        return default


#------------------------------------------------------------------------------


# ===========================================================================
# ROW MEMBERSHIP -- LAYER 1: DEDUP
# ===========================================================================

class CampaignRows(NamedTuple):
    """The deduplicated rows of one campaign, and what deduplication saw."""

    row_ids: Tuple[int, ...] = ()
    patient_ids: Tuple[str, ...] = ()
    rows_before_dedup: int = 0
    duplicate_rows: int = 0
    order_agrees: bool = True
    order_disagreements: Tuple[str, ...] = ()

    @property
    def patients(self) -> int:
        return len(self.patient_ids)


def campaign_rows(conn, run_ids: Sequence[int]) -> CampaignRows:
    """LAYER 1. The FIRST ATTEMPT per patient across ``run_ids``.

    ``MIN(id)`` per ``patient_id`` over every row of the campaign, and the
    ordering is by ``id`` rather than by ``timestamp`` for the reason
    ``queries.campaign_summary`` gives for reading run order off ``runs.id``:
    ``id`` is AUTOINCREMENT and therefore monotone in insert order within one
    database, while ``timestamp`` is TEXT that two rows can share.

    APPLIED BEFORE ANY SUCCESS FILTERING. Doing it the other way round --
    filter to successes, then take the first of what is left -- silently
    promotes a patient's SECOND attempt to "first" whenever the first errored,
    so the very population that exists to report failures would be the
    population with none in it. That is not a subtle bias: the resample pass
    re-runs `CAMPAIGN_STABILITY_SAMPLE_SIZE` completed patients at full price,
    so a campaign's duplicate rows are its SUCCESSES by construction.

    THE ORDER AGREEMENT IS CHECKED AND REPORTED, NEVER ACTED ON. If ordering by
    ``id`` and ordering by ``timestamp`` disagree about which row is a
    patient's first, the ``id`` answer is used -- it is the one with a
    guarantee behind it -- and the disagreement is carried out so the caller
    can put it on the report. A clock that ran backwards, a restored row, a
    hand-edited timestamp: all three are things an operator should be told
    about and none is a reason to refuse a drift reading.
    """
    if not run_ids:
        return CampaignRows()

    placeholders = ",".join("?" for _ in run_ids)
    rows = conn.execute(
        f"SELECT id, patient_id, timestamp FROM inferences "
        f"WHERE run_id IN ({placeholders}) ORDER BY id ASC",
        tuple(run_ids)).fetchall()

    first_by_id: Dict[str, Tuple[int, str]] = {}
    first_by_timestamp: Dict[str, Tuple[str, int]] = {}
    for row_id, patient_id, timestamp in rows:
        key = patient_id
        if key not in first_by_id:
            first_by_id[key] = (row_id, timestamp)
        # A NULL timestamp sorts BELOW every string here, which would make it
        # the "earliest" -- so it is excluded from the timestamp opinion
        # entirely rather than being allowed to win it. The id answer is
        # unaffected, which is the point of the id answer.
        if timestamp is not None:
            candidate = (str(timestamp), row_id)
            if key not in first_by_timestamp or candidate < first_by_timestamp[key]:
                first_by_timestamp[key] = candidate

    disagreements = []
    for key, (row_id, _ts) in sorted(first_by_id.items()):
        by_ts = first_by_timestamp.get(key)
        if by_ts is not None and by_ts[1] != row_id:
            disagreements.append(
                f"{key}: id order gives row {row_id}, timestamp order gives "
                f"row {by_ts[1]}")

    selected = tuple(sorted(row_id for row_id, _ts in first_by_id.values()))
    return CampaignRows(
        row_ids=selected,
        patient_ids=tuple(sorted(first_by_id)),
        rows_before_dedup=len(rows),
        duplicate_rows=len(rows) - len(selected),
        order_agrees=not disagreements,
        order_disagreements=tuple(disagreements),
    )


#------------------------------------------------------------------------------


# ===========================================================================
# ROW MEMBERSHIP -- LAYER 2: PER-METRIC ELIGIBILITY
# ===========================================================================

ROW_RULE_ALL = "deduplicated_including_failures"
"""Every deduplicated row, FAILURES INCLUDED.

The rule for availability, for the error-rate family and for the underfill
reporters. A FAILED PATIENT MUST BE VISIBLE TO THE METRICS THAT EXIST TO SEE
FAILURES -- a run that errored on half its cohort and reported an error rate
computed over the half that succeeded would report zero.
"""

ROW_RULE_STAGE5 = "deduplicated_stage5_complete"
"""Deduplicated rows that additionally have a clean error and a Stage 5 result.

``COALESCE(error,'') = '' AND candidates_evaluated > 0``. The rule for every
metric whose number is a property of a completed Stage 5: a patient whose
pipeline errored has no eligible-match count to compare, and one whose
``candidates_evaluated`` is zero has a match-quality denominator of zero.

BOTH TERMS ARE NEEDED AND NEITHER IMPLIES THE OTHER. A run can end cleanly with
no candidates (the no-candidates terminal node writes ``candidates_evaluated =
0`` and no error), and a run can error AFTER Stage 5 answered.
"""

ROW_RULES = (ROW_RULE_ALL, ROW_RULE_STAGE5)
"""Every row rule a metric may declare. CLOSED."""


def eligible_row_ids(conn, row_ids: Sequence[int], rule: str) -> Tuple[int, ...]:
    """LAYER 2. The subset of ``row_ids`` this rule admits.

    ``row_ids`` must ALREADY be deduplicated -- this is the second layer and it
    never re-derives the first. Passing raw campaign rows here is the mistake
    the two-layer split exists to make impossible to write by accident, which
    is why the rule names say "deduplicated".
    """
    if rule not in ROW_RULES:
        raise ValueError(f"{rule!r} is not a member of ROW_RULES {ROW_RULES!r}")
    if not row_ids:
        return ()
    if rule == ROW_RULE_ALL:
        return tuple(row_ids)

    placeholders = ",".join("?" for _ in row_ids)
    rows = conn.execute(
        f"SELECT id FROM inferences WHERE id IN ({placeholders}) "
        f"  AND COALESCE(error, '') = '' "
        f"  AND candidates_evaluated IS NOT NULL "
        f"  AND candidates_evaluated > 0 "
        f"ORDER BY id ASC",
        tuple(row_ids)).fetchall()
    return tuple(r[0] for r in rows)


#------------------------------------------------------------------------------


# ===========================================================================
# PAIRING
# ===========================================================================

PAIR_EVIDENCE_INFERENCE = (
    # WHAT THE PIPELINE WAS GIVEN. The corpus is regenerable and a bundle can
    # change between campaigns; a paired difference over two different INPUTS
    # is not a difference in the pipeline. `compute_patient_hash` is exactly
    # that fact and this column is where the pipeline already records it.
    "patient_data_hash",
    # WHAT ACTUALLY ANSWERED, as opposed to what the run was configured for.
    # These three are written per ROW and the run stamp carries either a
    # different fact or none at all:
    #   `matching_model`    is the model that ANSWERED; the stamp holds
    #                       `matching_model_configured`, and Stage 5 refuses a
    #                       mismatch precisely because the two can differ.
    #   `matching_provider` is in no run stamp field at all.
    #   `matching_call_mode` IS a stamp field AND is written per row, and both
    #                       matter: the stamp says what the campaign was
    #                       configured for and the row says what that patient
    #                       got.
    "matching_model",
    "matching_provider",
    "matching_call_mode",
    # WHICH PROMPT THE JUDGE WAS GIVEN and WHICH BYTES RANKED THE TRIALS. Both
    # are stamp fields as well; both are recorded per row because a row is what
    # a pair is made of, and a row whose prompt version differs from its
    # campaign's is a row the stamp cannot describe.
    "llm_classifier_prompt_version",
    "cross_encoder_model",
    # WHETHER A STAGE WAS ABLATED FOR THIS PATIENT. In NO run stamp -- ablation
    # flags ride in the request, not in the configuration -- so without this a
    # paired comparison could put an ablated row against a production one and
    # report the missing stage as drift.
    "ablation_flags",
)
"""Per-ROW recorded configuration a pair must agree on. Declared, closed.

EVERY ENTRY IS A FACT THE RUN STAMP CANNOT SUPPLY OR CANNOT SUPPLY ALONE, which
is the rule that keeps this list from becoming an arbitrary set of columns: the
stamp is checked separately and as ONE item (see ``PAIR_EVIDENCE_RUN``), so a
column that only restates a stamp field would be a second copy of a rule this
module deliberately has one of.

WHAT IS NOT HERE, AND WHY. `pricing_version` and `estimated_cost_usd` are about
money rather than about what the pipeline did; `qdrant_collection` IS a stamp
field and the stamp check covers it; `age_reference_date` is a pure function of
`data_snapshot_date`, which the stamp gates, so checking it would refuse the
same pairs twice and make one difference look like two.
"""

PAIR_EVIDENCE_RUN = "run_fingerprint"
"""The per-RUN configuration item, checked as ONE composite fact.

IT IS ``RUN_FINGERPRINT_COLUMNS`` AND NOT A LIST OF THIS MODULE'S OWN. That
tuple is the project's existing answer to "may these two populations be treated
as one" -- it is what every resume gate compares -- and restating any part of it
here would be a second copy that can drift from the first. ONE item rather than
fourteen because a reader asking "why is this pair incomparable" wants "the
configuration differs, in these fields" rather than fourteen independent
findings that are all the same finding.

A RUN WITH NO STAMP IS UNRESOLVABLE EVIDENCE, not a match: SQLite's ``IS`` is
null-safe equality, so two all-NULL stamps compare equal and every unstamped
run in the table would be "the same configuration". ``fingerprint_version`` is
``run_fingerprint``'s own key for "this configuration was never recorded", and
it is what the resolvability test reads -- the same guard
``campaign_spend_before`` and ``campaign_summary`` both make.
"""

PAIR_VERIFIED = "verified"
PAIR_UNVERIFIED = "unverified"
PAIR_INCOMPARABLE = "incomparable"
PAIR_OUTCOMES = (PAIR_VERIFIED, PAIR_UNVERIFIED, PAIR_INCOMPARABLE)
"""What a candidate pair resolved to. CLOSED."""


class PairResult(NamedTuple):
    """The result of matching two campaigns' rows on ``patient_id``.

    ``incomparable_on`` and ``unverified_on`` are ``{evidence: count}`` --
    WHICH recorded fact disagreed, and which could not be read. Without them a
    refusal says "no verified pair survives" and an operator has no way to tell
    a model change from a re-indexed corpus from a database that never recorded
    a patient hash.
    """

    verified: Tuple[Tuple[int, int], ...] = ()
    unverified: Tuple[Tuple[int, int], ...] = ()
    incomparable: Tuple[Tuple[int, int], ...] = ()
    reference_only: Tuple[str, ...] = ()
    comparison_only: Tuple[str, ...] = ()
    incomparable_on: Dict[str, int] = {}
    unverified_on: Dict[str, int] = {}

    def by_outcome(self) -> Dict[str, Tuple]:
        """``{outcome: pairs}`` keyed by ``PAIR_OUTCOMES``, TOTAL over it.

        THE READER THAT MAKES THE VOCABULARY LOAD-BEARING. Without it the three
        constants would be a closed set nothing branches on -- a declaration,
        which this project treats as a defect. A consumer that wants "every
        pair, labelled" gets it here rather than by re-deriving the labels from
        three attribute names.
        """
        return {
            PAIR_VERIFIED: self.verified,
            PAIR_UNVERIFIED: self.unverified,
            PAIR_INCOMPARABLE: self.incomparable,
        }

    @property
    def counts(self) -> Dict:
        """The census every result is reported with.

        DERIVED FROM ``by_outcome`` rather than from the attributes, so a
        fourth outcome added to the vocabulary appears here without a second
        edit -- and cannot be added to the vocabulary and silently left out of
        the census.
        """
        counts = {f"{outcome}_pairs": len(pairs)
                  for outcome, pairs in self.by_outcome().items()}
        counts["reference_only_patients"] = len(self.reference_only)
        counts["comparison_only_patients"] = len(self.comparison_only)
        return counts


def pair_rows(conn, reference_row_ids: Sequence[int],
              comparison_row_ids: Sequence[int]) -> PairResult:
    """Match two row sets on ``patient_id`` and classify every candidate pair.

    THE PAIR RULE IS THE INPUT **AND** THE RECORDED CONFIGURATION, and the
    second half is what a paired comparison is worth nothing without. Two rows
    for one ``patient_id`` are comparable only if they were produced over the
    same patient input AND by the same pipeline: a paired z-score of
    ``total_time`` across a model change measures the model change, and a
    paired verdict comparison across an ablated stage measures the ablation.
    Neither is drift, and both would be reported as drift.

    THE EVIDENCE IS DECLARED, NOT INVENTED. ``PAIR_EVIDENCE_INFERENCE`` is the
    per-ROW half -- every member a fact the run stamp cannot supply or cannot
    supply alone -- and ``PAIR_EVIDENCE_RUN`` is the per-RUN half, which is
    ``RUN_FINGERPRINT_COLUMNS`` checked as ONE composite item because that
    tuple is already this project's answer to "may these two populations be
    treated as one".

    THREE OUTCOMES, AND THE MIDDLE ONE IS THE POINT:

      VERIFIED       every item resolvable on both sides and equal. Countable.
      UNVERIFIED     at least one item could not be READ -- a NULL or empty
                     value, a row with no ``run_id``, a run with no stamp -- and
                     none disagreed. Its own count, excluded from verified
                     comparisons, reported with every result. It is NOT folded
                     into either of the other two: calling it verified asserts
                     a check that did not happen, and calling it incomparable
                     asserts a difference nobody observed.
      INCOMPARABLE   at least one item was resolvable on both sides and
                     DIFFERENT.

    INCOMPARABLE OUTRANKS UNVERIFIED, and the precedence is evidential rather
    than arbitrary: an OBSERVED difference is stronger evidence than an
    absence, so a pair with one item differing and another unreadable is
    incomparable. Reading it the other way would let a single unreadable column
    downgrade a real incompatibility into "we could not tell".

    REPORTING-ONLY DOES NOT WAIVE THIS. The metrics do not alert, which means
    no machine acts on the number -- and a human reading "age_ks_test = 0.5"
    beside two campaigns judged by different models is being handed a
    measurement of the model change under the word "drift". The deferral is of
    the ALERT, not of the attribution.

    THE FINGERPRINT DIFFERENCE IS STILL REPORTED AT THE COMPARISON LEVEL and
    that is not in tension with refusing here: the report says the two campaigns
    were configured differently and this says which PAIRS that makes
    uncountable. When the two campaigns share a configuration -- the ordinary
    case -- the report is empty and every pair passes, so the check costs
    nothing it does not have to.

    ``reference_only`` / ``comparison_only`` are the unpaired leftovers, by
    patient. A cohort change shows up there and nowhere else, so a comparison
    that reported only its pairs would be silent about the cohort having moved.

    THE PATIENT IS THE KEY AND THE ROW IS THE VALUE, and each side is already
    at most one row per patient because both come through
    ``campaign_rows`` -> ``eligible_row_ids``. That is asserted rather than
    assumed: a duplicate on either side raises, because a silent
    last-one-wins here would pair a patient's resample against another
    patient's first attempt.
    """
    ref_map = _patient_map(conn, reference_row_ids)
    cmp_map = _patient_map(conn, comparison_row_ids)

    verified, unverified, incomparable = [], [], []
    incomparable_on: Dict[str, int] = {}
    unverified_on: Dict[str, int] = {}

    for patient_id in sorted(set(ref_map) & set(cmp_map)):
        ref_row, ref_evidence = ref_map[patient_id]
        cmp_row, cmp_evidence = cmp_map[patient_id]
        pair = (ref_row, cmp_row)

        differed, unreadable = [], []
        for item in _pair_evidence_items(conn):
            left = ref_evidence.get(item)
            right = cmp_evidence.get(item)
            if not _evidence_resolvable(left) or not _evidence_resolvable(right):
                unreadable.append(item)
            elif left != right:
                differed.append(item)

        if differed:
            incomparable.append(pair)
            for item in differed:
                incomparable_on[item] = incomparable_on.get(item, 0) + 1
        elif unreadable:
            unverified.append(pair)
            for item in unreadable:
                unverified_on[item] = unverified_on.get(item, 0) + 1
        else:
            verified.append(pair)

    return PairResult(
        verified=tuple(verified),
        unverified=tuple(unverified),
        incomparable=tuple(incomparable),
        reference_only=tuple(sorted(set(ref_map) - set(cmp_map))),
        comparison_only=tuple(sorted(set(cmp_map) - set(ref_map))),
        incomparable_on=incomparable_on,
        unverified_on=unverified_on,
    )


def _evidence_resolvable(value) -> bool:
    """Whether one recorded evidence value can be compared at all.

    ``None`` and the empty string are UNRESOLVABLE -- the pipeline writes a
    real value or it writes nothing, so an empty string in one of these columns
    is a row that recorded no answer. Everything else is compared as it stands;
    this function deliberately does NOT normalise, because two spellings of one
    configuration are a finding rather than a formatting problem.
    """
    return value is not None and value != ""


def _pair_evidence_items(conn) -> Tuple[str, ...]:
    """The evidence items this DATABASE can actually supply.

    A column the schema does not have is dropped from the check ENTIRELY rather
    than being treated as unresolvable on both sides -- which would make every
    pair in an older database unverified on a column that could never have been
    there, and bury the pairs that are unverified for a reason somebody can act
    on. What the database CAN answer is checked; what it cannot is not a
    finding about the pairs.

    The run half is included only when the database has run identity, which
    ``has_run_identity`` has already established for every caller inside the
    drift pipeline. Checked here as well because this function is public.
    """
    have = set(table_columns(conn, "inferences"))
    items = [c for c in PAIR_EVIDENCE_INFERENCE if c in have]
    if "run_id" in have and table_columns(conn, "runs"):
        items.append(PAIR_EVIDENCE_RUN)
    return tuple(items)


def _patient_map(conn, row_ids: Sequence[int]) -> Dict[str, Tuple[int, Dict]]:
    """``{patient_id: (row_id, {evidence: value})}`` over ``row_ids``.

    ONE QUERY FOR THE WHOLE EVIDENCE SET, and the run fingerprint arrives as a
    single joined string rather than as fourteen columns: the pair check treats
    it as one composite item, so splitting it here would only let a caller
    compare a part of it. The join uses a separator no fingerprint value can
    contain, so two different stamps cannot collide into one string.
    """
    if not row_ids:
        return {}

    items = _pair_evidence_items(conn)
    inference_cols = [c for c in items if c != PAIR_EVIDENCE_RUN]
    wants_run = PAIR_EVIDENCE_RUN in items

    # `fingerprint_version` IS TESTED SEPARATELY inside `_fingerprint_key`. It
    # is `run_fingerprint`'s own key for "this configuration was never
    # recorded", and without it two all-NULL stamps would join to one identical
    # string and compare EQUAL -- which is SQLite's null-safe `IS` trap,
    # arrived at through Python instead.
    stamp_cols = []
    join = ""
    if wants_run:
        have_runs = set(table_columns(conn, "runs"))
        stamp_cols = [c for c in RUN_FINGERPRINT_COLUMNS if c in have_runs]
        join = " LEFT JOIN runs r ON r.id = i.run_id"

    # WRITTEN AS ONE LIST RATHER THAN AS AN AUGMENTED ASSIGNMENT WITH A
    # TRAILING CONDITIONAL. The first version was
    # `projection += ", " + ",".join(...) if stamp_cols else ", NULL"`, which
    # binds as `projection += (A if cond else B)` -- the intended meaning, and
    # only by accident of precedence. A reader should not have to work that out
    # to know which columns a query selects.
    projection = ", ".join(
        ["i.id", "i.patient_id"]
        + [f"i.{c}" for c in inference_cols]
        + [f"r.{c}" for c in stamp_cols])

    placeholders = ",".join("?" for _ in row_ids)
    rows = conn.execute(
        f"SELECT {projection} FROM inferences i{join} "
        f"WHERE i.id IN ({placeholders}) ORDER BY i.id ASC",
        tuple(row_ids)).fetchall()

    out: Dict[str, Tuple[int, Dict]] = {}
    for row in rows:
        row_id, patient_id = row[0], row[1]
        if patient_id in out:
            raise ValueError(
                f"patient {patient_id!r} appears in more than one row of a set "
                f"that must be deduplicated (rows {out[patient_id][0]} and "
                f"{row_id}). campaign_rows() is what guarantees this and it was "
                f"not applied.")
        evidence = dict(zip(inference_cols, row[2:2 + len(inference_cols)]))
        if wants_run:
            stamp = list(row[2 + len(inference_cols):])
            evidence[PAIR_EVIDENCE_RUN] = _fingerprint_key(stamp_cols, stamp)
        out[patient_id] = (row_id, evidence)
    return out


FINGERPRINT_KEY_SEPARATOR = "\x1f"
"""ASCII unit separator, joining a run stamp into one comparable string.

CHOSEN BECAUSE NO STAMP VALUE CAN CONTAIN IT. Every field is a version string,
a model id, a collection name, a date or an integer; a printable separator --
``|``, ``,`` -- could appear inside one and let two different stamps join to one
identical string, which is a silent match on a real difference.
"""


def _fingerprint_key(columns, values):
    """One comparable string for a run stamp, or ``None`` when unrecorded.

    ``None`` -- which ``_evidence_resolvable`` reads as UNRESOLVABLE -- whenever
    the stamp has no ``fingerprint_version``. That covers a row with no
    ``run_id`` (the LEFT JOIN gives NULLs), a run written before fingerprinting,
    and a caller that stamped nothing; in all three the configuration was never
    recorded, and comparing two unrecorded configurations as equal is the
    null-safe-equality trap ``campaign_spend_before`` already guards against.
    """
    if not columns:
        return None
    stamp = dict(zip(columns, values))
    if stamp.get("fingerprint_version") is None:
        return None
    return FINGERPRINT_KEY_SEPARATOR.join(
        "\x00" if stamp.get(c) is None else str(stamp.get(c))
        for c in columns)


#------------------------------------------------------------------------------


# ===========================================================================
# STRATIFICATION
# ===========================================================================

STRATUM_UNKNOWN = CANCER_GROUP_UNRESOLVED
"""The explicit stratum for a row whose cancer group could not be established.

IT IS THE PROJECT'S OWN ``CANCER_GROUP_UNRESOLVED`` AND NOT A NEW NAME, and
that matters because ``cancer_group_key`` answers ``other`` -- not ``unknown``
-- for a NULL display. That is right for its own contract (a missing display is
a cancer that matched no keyword) and wrong here: a row whose
``primary_condition`` was never recorded has not been classified as ``other``,
it has not been classified at all, and folding it into ``other`` would put an
un-stratified population inside a named stratum.
"""


def stratum_for(primary_condition) -> str:
    """The cancer-group stratum of one row, from its RECORDED field.

    ``STRATUM_UNKNOWN`` when ``primary_condition`` is NULL or blank; otherwise
    ``cancer_group_key`` -- the one owner of the display-to-group mapping,
    reused rather than restated.
    """
    if primary_condition is None:
        return STRATUM_UNKNOWN
    text = str(primary_condition).strip()
    if not text:
        return STRATUM_UNKNOWN
    return cancer_group_key(text)


def strata_for_rows(conn, row_ids: Sequence[int]) -> Dict[int, str]:
    """``{row_id: stratum}`` over ``row_ids``.

    A row whose ``primary_condition`` column is absent from the schema entirely
    lands in ``STRATUM_UNKNOWN`` as well -- the same answer a NULL gets, and
    for the same reason: nothing was recorded from which to classify it.
    """
    if not row_ids:
        return {}
    if "primary_condition" not in table_columns(conn, "inferences"):
        return {int(r): STRATUM_UNKNOWN for r in row_ids}
    placeholders = ",".join("?" for _ in row_ids)
    rows = conn.execute(
        f"SELECT id, primary_condition FROM inferences "
        f"WHERE id IN ({placeholders})", tuple(row_ids)).fetchall()
    return {int(row_id): stratum_for(display) for row_id, display in rows}


#------------------------------------------------------------------------------


# ===========================================================================
# DESIGNATION
# ===========================================================================

class DesignationError(RuntimeError):
    """A reference could not be designated, and the message says why.

    A ``RuntimeError`` subclass and deliberately not a ``ValueError``, on
    ``UnknownModelPricingError``'s precedent: designation is an explicit
    operator command whose failure must reach the operator, and a broad
    ``except ValueError`` anywhere above it must not be able to eat it.
    """


def _read_digest_rows(conn, row_ids: Sequence[int], columns: Sequence[str]):
    """``(id, *columns)`` for ``row_ids``, in ascending id order."""
    if not row_ids:
        return []
    projection = ", ".join(["id"] + list(columns))
    placeholders = ",".join("?" for _ in row_ids)
    return conn.execute(
        f"SELECT {projection} FROM inferences WHERE id IN ({placeholders}) "
        f"ORDER BY id ASC", tuple(row_ids)).fetchall()


def digest_columns_present(conn) -> Tuple[str, ...]:
    """The members of ``DIGEST_COLUMNS`` this database actually has.

    A DESIGNATION RECORDS WHAT IT COVERED rather than assuming the full list.
    A database missing one of them can still hold a reference; what it cannot
    do is pretend the digest covered a column that is not there, which is why
    the covered list is stored per designation and resolution reads THAT list
    rather than this constant.
    """
    have = set(table_columns(conn, "inferences"))
    return tuple(c for c in DIGEST_COLUMNS if c in have)


def designate_reference(db_path, run_id, label=None, note=None,
                        designated_by=None, now=None) -> Dict:
    """Designate the campaign containing ``run_id`` as THE drift reference.

    THE ONLY WRITER OF ``drift_reference``, and an explicit operator command:
    nothing in the pipeline calls it, and no default resolves ``db_path``.

    Args:
        db_path: the database. REQUIRED, no default -- see this module's
            docstring for why a designation store must not be able to point at
            production by omission.
        run_id: any run of the campaign to designate. The whole campaign is
            resolved from it, resumes included.
        label: a short operator-chosen name, carried into every caption and
            console line. Optional; the id is the identity.
        note: free text, for the operator's own reasons.
        designated_by: who or what designated it. Defaults to the OS user,
            which is provenance rather than authentication.
        now: the timestamp to record. Injectable so a test can pin it; a
            default of ``None`` means UTC now.

    Returns:
        A dict describing the row written, including its ``reference_id`` and
        the ``row_ids`` it designated -- the second so a caller can assert on
        the membership without re-reading the table it just wrote, which is
        what makes an isolation or mutation test checkable rather than hopeful.

    Raises:
        DesignationError: the database has no run identity, the campaign could
            not be enumerated, or it holds no rows. Every one of those means
            the designation would name a population that does not exist, and a
            reference nobody can resolve is worse than no reference: it turns
            every later run's refusal into a puzzle about a row that IS there.
    """
    if not os.path.isfile(db_path):
        raise DesignationError(f"no database at {db_path}")

    membership = campaign_run_ids(run_id, db_path=db_path)
    if not membership.resolved:
        raise DesignationError(
            f"run {run_id} does not name a campaign in {db_path}: "
            f"{membership.reason}")

    # A READ-ONLY connection for everything that is measured, and a separate
    # writable one for the INSERT. The measurement must not be able to alter
    # what it is measuring, and separating them is cheaper than remembering.
    conn = _connect(db_path)
    try:
        ok, detail = has_run_identity(conn)
        if not ok:
            raise DesignationError(
                f"{db_path} cannot say which run a row belongs to ({detail}). "
                f"Every drift selection is defined over a campaign, so there "
                f"is nothing to designate.")

        # THE STORE ITSELF, CHECKED BEFORE ANYTHING IS MEASURED. Without this
        # the failure is an `OperationalError: no such table` out of the INSERT
        # -- after the campaign has been walked, the rows deduplicated and the
        # digest computed -- and it names a table rather than a migration. This
        # module does NOT call initialize_database: creating a schema is the
        # schema owner's act, and a read-mostly designation command that
        # silently migrated a database would be doing something its name does
        # not say.
        if not table_columns(conn, "drift_reference"):
            raise DesignationError(
                f"{db_path} has no drift_reference table: it predates schema "
                f"era 16. The next writer to open it through "
                f"oncotriage.storage.database_logger.initialize_database() "
                f"adds one -- a batch run, or any command that writes an "
                f"inference row.")

        rows = campaign_rows(conn, membership.run_ids)
        if not rows.row_ids:
            raise DesignationError(
                f"campaign {membership.run_ids} holds no inference rows in "
                f"{db_path}; there is nothing to designate.")

        columns = digest_columns_present(conn)
        digest = content_digest(
            _read_digest_rows(conn, rows.row_ids, columns), columns)

        fingerprint = _run_fingerprint(conn, membership.head_id)
        tunables = _run_tunables(conn, membership.head_id)
    finally:
        conn.close()

    stamp = now or datetime.now(timezone.utc).isoformat()
    by = designated_by if designated_by is not None else _os_user()

    write = sqlite3.connect(db_path)
    try:
        cursor = write.cursor()
        # ONE TRANSACTION, WHICH IS WHAT MAKES "ONE ACTIVE" TRUE. A reader on
        # another connection sees the previous designation or this one, never
        # both and never neither.
        cursor.execute("UPDATE drift_reference SET is_active = 0 "
                       "WHERE is_active = 1")
        cursor.execute(
            "INSERT INTO drift_reference ("
            "  designated_at, designated_by, label, note, is_active,"
            "  anchor_run_id, campaign_head_run_id, campaign_run_ids,"
            "  row_ids, row_count, patient_count, fingerprint, tunables,"
            "  digest_algorithm, digest_columns, content_digest"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (stamp, by, label, note, 1,
             run_id, membership.head_id,
             json.dumps(list(membership.run_ids)),
             json.dumps(list(rows.row_ids)),
             len(rows.row_ids), rows.patients,
             json.dumps(fingerprint, sort_keys=True),
             json.dumps(tunables, sort_keys=True) if tunables is not None else None,
             DIGEST_ALGORITHM, json.dumps(list(columns)), digest))
        reference_id = cursor.lastrowid
        write.commit()
    finally:
        write.close()

    return {
        "reference_id": reference_id,
        "designated_at": stamp,
        "designated_by": by,
        "label": label,
        "note": note,
        "anchor_run_id": run_id,
        "campaign_run_ids": membership.run_ids,
        "campaign_head_run_id": membership.head_id,
        "row_ids": rows.row_ids,
        "row_count": len(rows.row_ids),
        "patient_count": rows.patients,
        "rows_before_dedup": rows.rows_before_dedup,
        "duplicate_rows": rows.duplicate_rows,
        "order_agrees": rows.order_agrees,
        "order_disagreements": rows.order_disagreements,
        "digest_algorithm": DIGEST_ALGORITHM,
        "digest_columns": columns,
        "content_digest": digest,
        "fingerprint": fingerprint,
        "campaign_membership_reason": membership.reason,
    }


def clear_reference(db_path) -> int:
    """Retire the active designation. Returns how many rows were retired.

    IT DEACTIVATES AND DOES NOT DELETE. Drift rows already written name the
    designation in ``reference_id``, and deleting it would leave them pointing
    at nothing -- so the record of what a past campaign was measured against
    would be destroyed by the act of choosing a new one.
    """
    if not os.path.isfile(db_path):
        raise DesignationError(f"no database at {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE drift_reference SET is_active = 0 "
                       "WHERE is_active = 1")
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def _os_user() -> str:
    """Who designated this, best effort. Provenance, never authentication."""
    try:
        return os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
    except Exception:                                      # noqa: BLE001
        return "unknown"


def _run_fingerprint(conn, run_id) -> Dict:
    """``{column: value}`` over ``RUN_FINGERPRINT_COLUMNS`` for one run.

    An absent column answers ``None`` rather than raising: a designation over
    an older database records what that database HAS, and the comparison side
    is what decides whether a missing field makes two populations incomparable.
    """
    have = set(table_columns(conn, "runs"))
    wanted = [c for c in RUN_FINGERPRINT_COLUMNS if c in have]
    out = {c: None for c in RUN_FINGERPRINT_COLUMNS}
    if not wanted or run_id is None:
        return out
    projection = ", ".join(wanted)
    row = conn.execute(f"SELECT {projection} FROM runs WHERE id = ?",
                       (run_id,)).fetchone()
    if row is None:
        return out
    for name, value in zip(wanted, row):
        out[name] = value
    return out


def _run_tunables(conn, run_id) -> Optional[Dict]:
    """``runs.tunables`` for one run, decoded. ``None`` when unavailable.

    THREE WAYS TO GET ``None`` AND THEY ARE NOT THE SAME, which is why the
    caller that needs a denominator reports ``unverified_inputs`` rather than
    substituting today's config: the column may be absent (a pre-era-15
    database), the value may be NULL (a run whose writer recorded none), or the
    JSON may not parse. In all three the honest answer is that this run's
    tunables are not known, and a fraction computed over an assumed denominator
    is a number about the assumption.
    """
    if "tunables" not in table_columns(conn, "runs") or run_id is None:
        return None
    row = conn.execute("SELECT tunables FROM runs WHERE id = ?",
                       (run_id,)).fetchone()
    if row is None or row[0] is None:
        return None
    decoded = _json_or_default(row[0], None)
    return decoded if isinstance(decoded, dict) else None


#------------------------------------------------------------------------------


# ===========================================================================
# RESOLUTION
# ===========================================================================

def active_reference_row(conn) -> Optional[sqlite3.Row]:
    """The active designation, or ``None``. Newest first if several are active.

    ``ORDER BY id DESC LIMIT 1`` even though the writer keeps exactly one
    active row in one transaction: a file somebody edited by hand can hold two,
    and "whichever SQLite returns first" is not an answer a drift report should
    be built on.
    """
    if not table_columns(conn, "drift_reference"):
        return None
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT * FROM drift_reference WHERE is_active = 1 "
        "ORDER BY id DESC LIMIT 1").fetchone()


def resolve_reference(db_path) -> ReferenceResolution:
    """Resolve the active designation against the database as it is NOW.

    RE-CHECKS THE IDS AND THE DIGEST, EVERY TIME. A designation is a claim about
    a population, and this is where the claim is tested rather than trusted:
    the same row ids must still be there, and hashing the columns the
    designation says it covered must still give the digest it recorded.

    Returns a ``ReferenceResolution`` whose ``outcome`` is a member of
    ``REFERENCE_OUTCOMES``. IT NEVER RAISES -- every failure is one of the
    outcomes, because this is called from the drift pipeline's own step 2 and a
    reference problem must downgrade the metrics that consume a reference
    rather than take the run down.
    """
    try:
        if not os.path.isfile(db_path):
            return ReferenceResolution(
                outcome=REFERENCE_READ_FAILURE,
                detail=f"no database at {db_path}")

        conn = _connect(db_path)
        try:
            ok, detail = has_run_identity(conn)
            if not ok:
                return ReferenceResolution(
                    outcome=REFERENCE_NO_RUN_IDENTITY, detail=detail)

            row = active_reference_row(conn)
            if row is None:
                # THE TABLE'S ABSENCE AND AN EMPTY TABLE ARE BOTH
                # `reference_absent` -- the remedy is the same, designate one --
                # AND THE DETAIL SAYS WHICH. A database written before schema
                # era 16 has no `drift_reference` at all, and a message reading
                # "no active row" over a table that is not there sends an
                # operator to look for rows in nothing.
                have_table = bool(table_columns(conn, "drift_reference"))
                return ReferenceResolution(
                    outcome=REFERENCE_ABSENT,
                    detail=("no active row in drift_reference. " if have_table
                            else "this database has no drift_reference table "
                                 "-- it predates schema era 16, and the next "
                                 "writer to open it through "
                                 "initialize_database() adds one. ")
                           + "Designate a reference with: python "
                             "\"20- Drift Detection.py\" "
                             "--designate-reference <run_id>")

            reference_id = int(row["id"])
            declared_rows = _json_or_default(row["row_ids"], None)
            declared_runs = _json_or_default(row["campaign_run_ids"], None)
            columns = _json_or_default(row["digest_columns"], None)
            base = {
                "reference_id": reference_id,
                "label": row["label"],
                "designated_at": row["designated_at"],
                "expected_digest": row["content_digest"],
                "run_ids": tuple(declared_runs or ()),
                "fingerprint": _json_or_default(row["fingerprint"], {}) or {},
                "tunables": _json_or_default(row["tunables"], {}) or {},
            }

            if not isinstance(declared_rows, list) or not declared_rows:
                return ReferenceResolution(
                    outcome=REFERENCE_UNRESOLVABLE, **base,
                    detail=f"designation {reference_id} carries no readable "
                           f"row_ids (row_count says {row['row_count']})")
            if not isinstance(columns, list) or not columns:
                return ReferenceResolution(
                    outcome=REFERENCE_UNRESOLVABLE, **base,
                    detail=f"designation {reference_id} carries no readable "
                           f"digest_columns")

            declared_rows = [int(v) for v in declared_rows]
            base["row_ids"] = tuple(declared_rows)

            missing_columns = [c for c in columns
                               if c not in table_columns(conn, "inferences")]
            if missing_columns:
                return ReferenceResolution(
                    outcome=REFERENCE_UNRESOLVABLE, **base,
                    detail=f"designation {reference_id} was digested over "
                           f"{missing_columns}, which this database no longer "
                           f"has")

            if row["digest_algorithm"] != DIGEST_ALGORITHM:
                return ReferenceResolution(
                    outcome=REFERENCE_UNRESOLVABLE, **base,
                    detail=f"designation {reference_id} was digested with "
                           f"{row['digest_algorithm']!r}; this build computes "
                           f"{DIGEST_ALGORITHM!r} and the two cannot be "
                           f"compared. Re-designate.")

            found = _read_digest_rows(conn, declared_rows, columns)
            found_ids = [int(r[0]) for r in found]
            if found_ids != sorted(declared_rows):
                absent = sorted(set(declared_rows) - set(found_ids))
                return ReferenceResolution(
                    outcome=REFERENCE_UNRESOLVABLE, **base,
                    detail=f"designation {reference_id} names "
                           f"{len(declared_rows)} rows and "
                           f"{len(found_ids)} were found; absent: "
                           f"{absent[:10]}"
                           f"{'...' if len(absent) > 10 else ''}")

            actual = content_digest(found, columns)
            if actual != row["content_digest"]:
                return ReferenceResolution(
                    outcome=REFERENCE_MUTATED, **base, actual_digest=actual,
                    detail=f"designation {reference_id} names "
                           f"{len(declared_rows)} rows that are ALL present "
                           f"and whose content has changed. The ids and the "
                           f"count agree; the values do not. Re-designate over "
                           f"the data as it is now, or find out what edited it.")

            return ReferenceResolution(outcome=REFERENCE_OK, **base,
                                       actual_digest=actual,
                                       detail="ids and content digest agree")
        finally:
            conn.close()

    except Exception as exc:                               # noqa: BLE001
        return ReferenceResolution(
            outcome=REFERENCE_READ_FAILURE,
            detail=f"{type(exc).__name__}: {exc}")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Sep 10 2026

@author: ramyalsaffar
"""
