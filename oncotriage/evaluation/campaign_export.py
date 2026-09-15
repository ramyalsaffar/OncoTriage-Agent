# Campaign Database Export
##########################

"""Turn one named campaign's stored rows into an evaluation run directory.

The output is the directory shape ``oncotriage/evaluation/rater.py:load_run``
and ``oncotriage/evaluation/ragas_harness.py:load_run`` already read, built
from the judge sample's first durably admitted main attempts. No pipeline stage runs, no model is
called, and nothing is written to the source database.

WHY THIS EXISTS. ``evaluation_run.py`` produces those directories by running the
pipeline again, which costs a live Stage 5 call per patient and measures a
second run rather than the campaign. A completed inference stores the fields
both evaluators read: the prompt Stage 5 sent (patient record and every trial's
fenced block) in ``inferences.llm_classifier_prompt``, and every verdict with
its criteria arrays in ``trial_matches``.

WHICH CAMPAIGN IS NAMED, NEVER INFERRED. ``runs.billing_campaign_id`` (schema
era 18) marks membership, and the checkpoint's campaign record is cleared on a
clean finish, so the database is the durable source. Nothing here picks "the
latest" campaign: a database can hold several and only the operator knows which
one is being evaluated.

WHICH PATIENTS: THE JUDGE SAMPLE, RECOMPUTED FROM RECORDED VALUES. The campaign
row records the cohort size and seed it drew with and the judge sample's size
and seed. The cohort is redrawn exactly as ``oncotriage/batch/runner.py`` draws
it -- ``cohort.select`` over the sorted bundle list, stratified by
``cohort_groups.group_map`` -- with every size and seed passed EXPLICITLY from
the run row. ``cohort.select`` resolves a ``None`` to today's config constants
(bound by value inside ``cohort.py`` at import), so passing ``None`` would
silently evaluate the sample today's configuration would draw rather than the
one the campaign drew. The recomputed membership digest must equal
``runs.cohort_digest``.

WHAT THAT DIGEST DOES AND DOES NOT PROVE. It proves the SAME STEMS were
selected. It says nothing about a bundle's content. Per-patient content is
checked separately: each judged bundle is parsed and
``compute_patient_hash`` must equal the selected row's
``inferences.patient_data_hash``.

WHICH ROW: THE FIRST MAIN ADMISSION in the durable campaign history sidecar.
Its completion names the exact inference ID, if any. Selection precedes outcome
filtering. Exceptions, lost writes and interrupted admissions stay in accounting;
later successes and resamples never substitute. Every campaign run must have
history coverage. Historical campaigns without that evidence refuse export.
START records admission before parsing, not proof that a model call occurred.

HOW A ROW'S STATE IS CLASSIFIED. On ``error``, prompt emptiness and
``candidates_evaluated``. NOT on ``llm_classifier_prompt_sha256``: the Stage 5
de-identification refusal returns before a prompt or hash exists, and a failed
warmup row carries a hash beside an empty prompt, so neither ``IS NULL`` nor
``IS NOT NULL`` marks Stage 5 progress.

THE PARSE RULES (R1-R3), EACH REFUSING RATHER THAN GUESSING:

  R1  ``llm_classifier_prompt`` is ``"[SYSTEM]\\n" + system + "\\n\\n[USER]\\n"
      + user``. The split is taken at the one ``"\\n\\n[USER]\\n"`` whose system
      half hashes to the stored ``llm_classifier_prompt_sha256``. Exactly one
      must qualify.
  R2  The patient summary is the text between the ``<<<PATIENT_RECORD>>>`` and
      ``<<<END_PATIENT_RECORD>>>`` lines of the system half, each occurring
      exactly once. This is the SENT, fence-neutralized text.
  R3  The user half is ``"\\nCLINICAL TRIALS:\\n" + trials + "\\n"``, and
      ``trials`` must partition, with no gap and no remainder, into fenced
      ``<<<TRIAL_DATA ...>>>`` blocks. A block's 1-based position must equal its
      ``trial_matches.trial_number``.

WHAT IS REFUSED, BEFORE ANYTHING IS WRITTEN. Every refusal is an
``ExportRefusal`` with a code from ``REFUSAL_CODES``. Missing or inconsistent
data STOPS the export for repair; it never shrinks the sample silently.

CONSISTENT READS. Campaign membership, the fingerprint columns, the inference
rows and the trial rows are read inside ONE read transaction on a ``mode=ro``
connection, so every query sees one database snapshot. The connection is closed
before anything is written.
"""

import argparse
import glob
import io
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from oncotriage.agent.patient import compute_patient_hash
from oncotriage.agent.prompts import prompt_sha256
from oncotriage.evaluation import cohort, cohort_groups, faithfulness_filter
from oncotriage.fhir.parser import parse_fhir_bundle
from oncotriage.observability import console
from oncotriage.storage.database_logger import RUN_FINGERPRINT_COLUMNS
from oncotriage.storage import attempt_history as AH


EXPORT_KIND = "campaign_database_export"
EXPORT_SCHEMA_VERSION = 1
RECORD_SCHEMA_VERSION = 1
MANIFEST_FILENAME = "manifest.json"
MIN_SCHEMA_ERA = 18

SELECTION_RULE = (
    "earliest durable MAIN admission per judged bundle across the named campaign; "
    "only its explicitly linked inference may be exported; resamples and later "
    "successes never replace a failed, unavailable or interrupted first admission")


OUTCOME_EXPORTED = "exported"
OUTCOME_FIRST_ATTEMPT_FAILED = "first_attempt_failed"
OUTCOME_NOTHING_TO_EVALUATE = "nothing_to_evaluate"
OUTCOME_NOT_IN_CAMPAIGN = "never_attempted"
OUTCOME_INTERRUPTED = "first_attempt_interrupted"
OUTCOME_UNAVAILABLE = "first_attempt_result_unavailable"
OUTCOMES = (OUTCOME_EXPORTED, OUTCOME_FIRST_ATTEMPT_FAILED,
            OUTCOME_NOTHING_TO_EVALUATE, OUTCOME_NOT_IN_CAMPAIGN,
            OUTCOME_INTERRUPTED, OUTCOME_UNAVAILABLE)

REFUSE_OUTPUT_NOT_EMPTY = "output_dir_not_empty"
REFUSE_OUTPUT_PARENT_MISSING = "output_parent_missing"
REFUSE_SOURCE_MISSING = "source_db_missing"
REFUSE_SOURCE_UNREADABLE = "source_db_unreadable"
REFUSE_SCHEMA_ERA = "schema_era_below_18"
REFUSE_CAMPAIGN_NOT_FOUND = "campaign_not_found"
REFUSE_RUNS_DISAGREE = "campaign_runs_disagree"
REFUSE_COHORT_UNRECORDED = "campaign_cohort_unrecorded"
REFUSE_CORPUS_EMPTY = "corpus_empty"
REFUSE_STEM_DUPLICATE = "judge_stem_duplicate"
REFUSE_COHORT_DIGEST = "cohort_digest_mismatch"
REFUSE_STEM_NO_BUNDLE = "judge_stem_no_bundle"
REFUSE_BUNDLE_UNREADABLE = "judge_bundle_unreadable"
REFUSE_PATIENT_ID_DUPLICATE = "judge_patient_id_duplicate"
REFUSE_HASH_MISMATCH = "patient_data_hash_mismatch"
REFUSE_INCONSISTENT = "inconsistent_stored_data"
REFUSAL_CODES = (
    REFUSE_OUTPUT_NOT_EMPTY, REFUSE_OUTPUT_PARENT_MISSING, REFUSE_SOURCE_MISSING,
    REFUSE_SOURCE_UNREADABLE,
    REFUSE_SCHEMA_ERA, REFUSE_CAMPAIGN_NOT_FOUND, REFUSE_RUNS_DISAGREE,
    REFUSE_COHORT_UNRECORDED, REFUSE_CORPUS_EMPTY, REFUSE_STEM_DUPLICATE,
    REFUSE_COHORT_DIGEST, REFUSE_STEM_NO_BUNDLE, REFUSE_BUNDLE_UNREADABLE,
    REFUSE_PATIENT_ID_DUPLICATE, REFUSE_HASH_MISMATCH, REFUSE_INCONSISTENT,
    "attempt_history_missing", "attempt_history_corrupt", "attempt_history_busy",
    "attempt_history_mismatch", "attempt_history_uncovered", "attempt_history_write_failed")

EXIT_OK = 0
EXIT_REFUSED = 1

# The recorded cohort values the redraw reads. The first four and the digest
# must agree across every run of the campaign; the stability pair is passed to
# `cohort.select` only so it never reads a config default, and it does not
# change which stems the cohort or the judge sample contain.
_COHORT_COLUMNS = ("cohort_size", "cohort_digest", "judge_sample_seed",
                   "judge_sample_size", "stability_sample_seed",
                   "stability_sample_size")
RUN_AGREEMENT_COLUMNS = tuple(RUN_FINGERPRINT_COLUMNS) + (
    "cohort_digest", "cohort_size", "judge_sample_seed", "judge_sample_size")
"""Columns every run of one campaign must agree on. ``RUN_FINGERPRINT_COLUMNS``
is the storage layer's own tuple, read rather than retyped, so a gated field
added there is checked here with no edit."""

_REQUIRED_RECORDED = ("campaign_cohort_size", "campaign_cohort_seed") + \
    _COHORT_COLUMNS

# THE ONE MEMBERSHIP PREDICATE, interpolated into every read. One spelling, so a
# read cannot scope itself to a different campaign than its neighbours.
_CAMPAIGN_PREDICATE = "r.billing_campaign_id = ?"


_TRIAL_COLUMNS = (
    "inference_id", "id", "nct_id", "trial_title", "trial_phase",
    "trial_number", "rerank_score", "rerank_score_raw", "mesh_boost",
    "mesh_boost_tier", "match_score", "eligible", "assessment",
    "criterion_details", "score_confirmed", "score_denominator",
    "criteria_not_applicable", "hallucinated", "criteria_split",
    "emission_index", "call_index", "not_evaluable_reason", "verdict_source",
    "verdict_original_label", "verdict_original_type", "criterion_remaps")

# Verdict keys copied from a trial row as they are named in a Stage 5 entry.
_VERDICT_FIELD_FROM_COLUMN = (
    ("title", "trial_title"), ("phase", "trial_phase"),
    ("trial_number", "trial_number"), ("rerank_score", "rerank_score"),
    ("rerank_score_raw", "rerank_score_raw"), ("mesh_boost", "mesh_boost"),
    ("mesh_boost_tier", "mesh_boost_tier"), ("match_score", "match_score"),
    ("eligible", "eligible"), ("assessment", "assessment"),
    ("score_confirmed", "score_confirmed"),
    ("score_denominator", "score_denominator"),
    ("criteria_not_applicable", "criteria_not_applicable"),
    ("hallucinated", "hallucinated"), ("criteria_split", "criteria_split"),
    ("emission_index", "emission_index"), ("call_index", "call_index"),
    ("not_evaluable_reason", "not_evaluable_reason"),
    ("verdict_source", "verdict_source"),
    ("verdict_original_label", "verdict_original_label"),
    ("verdict_original_type", "verdict_original_type"),
    ("criterion_remaps", "criterion_remaps"))

_SYSTEM_HEAD = "[SYSTEM]\n"
_USER_MARKER = "\n\n[USER]\n"
_PATIENT_OPEN = "<<<PATIENT_RECORD>>>"
_PATIENT_CLOSE = "<<<END_PATIENT_RECORD>>>"
_USER_HEAD = "\nCLINICAL TRIALS:\n"
_USER_TAIL = "\n"
_TRIAL_OPEN_RE = re.compile(
    r"<<<TRIAL_DATA nct_id=([^\s<>]+) phase=([^\n<>]*)>>>\n")
_TRIAL_OPEN_TOKEN = "<<<TRIAL_DATA "

# R4: node_finalize's partition of `eligible` into the three verdict groups.
_GROUP_FOR_LABEL = {"eligible": "matches", "not_evaluable": "not_evaluable"}
_GROUP_OTHER = "near_misses"

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


class ExportRefusal(RuntimeError):
    """The export stopped before writing anything. ``code`` names why."""

    def __init__(self, message, code):
        if code not in REFUSAL_CODES:
            raise ValueError(f"unknown export refusal code {code!r}")
        super().__init__(message)
        self.code = code


class _Inconsistent(Exception):
    """A stored row the parse rules cannot read. Raised to one handler."""


#------------------------------------------------------------------------------
# The read transaction
#------------------------------------------------------------------------------


def _open_readonly(db_path):
    """A ``mode=ro`` connection in autocommit mode, so BEGIN is ours to issue.

    ``mode=ro`` and never ``immutable=1``: immutable disables locking and
    change detection and ignores the ``-wal``, which is valid only on a file
    known not to change. A module-level function so a test can record every
    connection this module opens and prove each is closed before a write.
    """
    uri = Path(os.path.abspath(db_path)).as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, isolation_level=None)


def _rows(cursor):
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def read_campaign_snapshot(source_db, campaign_id, _between_reads=None):
    """Every stored fact the export needs, from ONE read transaction.

    Returns ``{"era", "runs", "first_rows", "campaign_rows", "trials"}``.
    ``first_rows`` retains the original test-seam name but now contains ALL
    campaign inferences: only explicit admission links decide the first row.
    ``_between_reads`` is a test seam called with a stage name after each read
    and before the next, while the transaction is open.

    RAISES ``ExportRefusal`` for a missing file, an era below 18, a database
    without ``runs.billing_campaign_id``, or a campaign with no runs.
    """
    if not os.path.isfile(source_db):
        raise ExportRefusal(f"source database {source_db!r} is not a file",
                            REFUSE_SOURCE_MISSING)
    try:
        conn = _open_readonly(source_db)
    except sqlite3.Error as exc:
        raise ExportRefusal(f"{source_db!r} could not be opened read-only: "
                            f"{type(exc).__name__}: {exc}",
                            REFUSE_SOURCE_UNREADABLE)
    try:
        conn.execute("BEGIN")
        era = conn.execute("PRAGMA user_version").fetchone()[0]
        run_columns = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
        if era < MIN_SCHEMA_ERA or "billing_campaign_id" not in run_columns:
            raise ExportRefusal(
                f"{source_db!r} is schema era {era}; campaign membership "
                f"(runs.billing_campaign_id) exists from era {MIN_SCHEMA_ERA}. "
                f"This database cannot say which rows belong to a campaign.",
                REFUSE_SCHEMA_ERA)

        run_cols = ["id", "status", "resumed", "started_at", "finished_at",
                    "billing_campaign_id"] + list(dict.fromkeys(
                        list(RUN_FINGERPRINT_COLUMNS) + list(_COHORT_COLUMNS)))
        runs = _rows(conn.execute(
            f"SELECT {', '.join('r.' + c for c in run_cols)} FROM runs r "
            f"WHERE {_CAMPAIGN_PREDICATE} ORDER BY r.id", (campaign_id,)))
        if not runs:
            raise ExportRefusal(
                f"no runs row in {source_db!r} carries billing_campaign_id "
                f"{campaign_id!r}", REFUSE_CAMPAIGN_NOT_FOUND)
        if _between_reads is not None:
            _between_reads("runs")

        first_rows = _rows(conn.execute(
            "SELECT i.id, i.patient_id, i.run_id, i.timestamp, "
            "COALESCE(i.error, '') AS error, "
            "COALESCE(i.llm_classifier_prompt, '') AS prompt, "
            "i.llm_classifier_prompt_sha256, i.llm_classifier_prompt_version, "
            "i.candidates_evaluated, i.patient_data_hash, "
            "i.age_reference_date, i.matching_model "
            "FROM inferences i JOIN runs r ON r.id = i.run_id "
            f"WHERE {_CAMPAIGN_PREDICATE} ORDER BY i.id", (campaign_id,)))
        if _between_reads is not None:
            _between_reads("first_rows")

        campaign_rows = [tuple(r) for r in conn.execute(
            "SELECT f.id, f.patient_id, COALESCE(f.error, '') = '' AS ok "
            "FROM inferences f JOIN runs r ON r.id = f.run_id "
            f"WHERE {_CAMPAIGN_PREDICATE} ORDER BY f.id", (campaign_id,))]
        if _between_reads is not None:
            _between_reads("campaign_rows")

        trials = {}
        for row in _rows(conn.execute(
                f"SELECT {', '.join('t.' + c for c in _TRIAL_COLUMNS)} "
                "FROM trial_matches t JOIN inferences i ON i.id = t.inference_id "
                "JOIN runs r ON r.id = i.run_id "
                f"WHERE {_CAMPAIGN_PREDICATE} "
                "ORDER BY t.inference_id, t.id", (campaign_id,))):
            trials.setdefault(row["inference_id"], []).append(row)
        if _between_reads is not None:
            _between_reads("trial_rows")

        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        # A file that is not a database, a locked or corrupt one: a named
        # refusal rather than a traceback, and nothing has been written.
        raise ExportRefusal(f"{source_db!r} could not be read: "
                            f"{type(exc).__name__}: {exc}",
                            REFUSE_SOURCE_UNREADABLE)
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        conn.close()

    return {"era": era, "runs": runs, "first_rows": first_rows,
            "campaign_rows": campaign_rows, "trials": trials}


#------------------------------------------------------------------------------
# Campaign agreement and the cohort redraw
#------------------------------------------------------------------------------


def require_runs_agree(runs, campaign_id):
    """The recorded values the export uses, once every run agrees on them."""
    disagreements = []
    for column in RUN_AGREEMENT_COLUMNS:
        values = {json.dumps(r.get(column), sort_keys=True) for r in runs}
        if len(values) > 1:
            by_run = {r["id"]: r.get(column) for r in runs}
            disagreements.append(f"{column} {by_run}")
    if disagreements:
        raise ExportRefusal(
            f"the runs of campaign {campaign_id!r} disagree on "
            f"{len(disagreements)} column(s): " + "; ".join(disagreements)
            + ". Rows produced under different configurations or cohorts are "
            "not one campaign's verdicts.", REFUSE_RUNS_DISAGREE)
    recorded = {c: runs[0].get(c) for c in _REQUIRED_RECORDED}
    missing = sorted(c for c, v in recorded.items() if v is None)
    if missing:
        raise ExportRefusal(
            f"campaign {campaign_id!r} records no value for {missing}; the "
            f"cohort and judge sample cannot be recomputed from recorded "
            f"values, and today's defaults are not a substitute",
            REFUSE_COHORT_UNRECORDED)
    return recorded


def recompute_cohort(fhir_dir, recorded, out):
    """The cohort and judge sample, redrawn from RECORDED sizes and seeds.

    Every size and seed is passed explicitly: ``cohort.select`` resolves a
    ``None`` to the config constants it bound at import.
    """
    files = sorted(glob.glob(os.path.join(fhir_dir, "*.json")))
    if not files:
        raise ExportRefusal(f"no *.json bundles under {fhir_dir!r}",
                            REFUSE_CORPUS_EMPTY)
    groups = cohort_groups.group_map(files, out=out, use_cache=False)
    try:
        selection = cohort.select(
            files,
            size=recorded["campaign_cohort_size"],
            seed=recorded["campaign_cohort_seed"],
            stability_size=recorded["stability_sample_size"],
            stability_seed=recorded["stability_sample_seed"],
            judge_size=recorded["judge_sample_size"],
            judge_seed=recorded["judge_sample_seed"],
            group_of=cohort_groups.grouper(groups))
    except ValueError as exc:
        raise ExportRefusal(f"the corpus under {fhir_dir!r} cannot be drawn: "
                            f"{exc}", REFUSE_STEM_DUPLICATE)
    if (selection.digest != recorded["cohort_digest"]
            or selection.size != recorded["cohort_size"]
            or selection.judge_size != recorded["judge_sample_size"]):
        raise ExportRefusal(
            f"the cohort redrawn from {fhir_dir!r} with the recorded size "
            f"{recorded['campaign_cohort_size']!r} and seed "
            f"{recorded['campaign_cohort_seed']!r} is {selection.size} "
            f"patients, digest {selection.digest}, judge sample "
            f"{selection.judge_size}; the campaign recorded "
            f"{recorded['cohort_size']!r} patients, digest "
            f"{recorded['cohort_digest']!r}, judge sample "
            f"{recorded['judge_sample_size']!r}. The corpus or the grouper "
            f"is not the one the campaign drew from.", REFUSE_COHORT_DIGEST)
    return selection, files


def map_judge_stems(judge_stems, files):
    """``{stem: bundle path}``, refusing a stem with no bundle or with two."""
    by_stem = {}
    for path in files:
        by_stem.setdefault(cohort.stem_of(path), []).append(path)
    missing = sorted(s for s in judge_stems if s not in by_stem)
    if missing:
        raise ExportRefusal(f"{len(missing)} judge stem(s) have no bundle in "
                            f"the corpus", REFUSE_STEM_NO_BUNDLE)
    duplicated = sorted(s for s in judge_stems if len(by_stem[s]) > 1)
    if duplicated or len(set(judge_stems)) != len(judge_stems):
        raise ExportRefusal(f"{len(duplicated) or 1} judge stem(s) are not "
                            f"unique in the corpus or the sample",
                            REFUSE_STEM_DUPLICATE)
    return {s: by_stem[s][0] for s in judge_stems}


def identify_judged(stem_to_path, parse=None, hasher=None):
    """``[{"patient_id", "computed_hash"}]`` in stem order, one per judge stem.

    The stems themselves are not returned or written: a Synthea stem carries
    the patient's name. ``parse`` and ``hasher`` default to the pipeline's own
    parser and hash, looked up at call time.
    """
    parse = parse_fhir_bundle if parse is None else parse
    hasher = compute_patient_hash if hasher is None else hasher
    judged, seen = [], {}
    for index, stem in enumerate(sorted(stem_to_path)):
        try:
            patient = parse(stem_to_path[stem])
            patient_id = patient.get("patient_id")
            computed = hasher(patient)
        except Exception as exc:                          # noqa: BLE001
            raise ExportRefusal(
                f"judge bundle #{index + 1} could not be parsed and hashed: "
                f"{type(exc).__name__}", REFUSE_BUNDLE_UNREADABLE)
        if not patient_id:
            raise ExportRefusal(f"judge bundle #{index + 1} carries no "
                                f"patient_id", REFUSE_BUNDLE_UNREADABLE)
        if patient_id in seen:
            raise ExportRefusal(
                f"patient_id {patient_id!r} is carried by two judge bundles",
                REFUSE_PATIENT_ID_DUPLICATE)
        seen[patient_id] = index
        judged.append({"patient_id": patient_id, "computed_hash": computed,
                       "bundle": AH.bundle_key(stem_to_path[stem])})
    return judged


#------------------------------------------------------------------------------
# The parse rules
#------------------------------------------------------------------------------


def split_stored_prompt(prompt, stored_sha256):
    """R1: ``(system, user)``, split where the system half hashes to the column."""
    if not prompt.startswith(_SYSTEM_HEAD):
        raise _Inconsistent("the stored prompt does not open with [SYSTEM]")
    matches = []
    at = prompt.find(_USER_MARKER, len(_SYSTEM_HEAD))
    while at >= 0:
        if stored_sha256 and prompt_sha256(
                prompt[len(_SYSTEM_HEAD):at]) == stored_sha256:
            matches.append(at)
        at = prompt.find(_USER_MARKER, at + 1)
    if len(matches) != 1:
        raise _Inconsistent(
            f"{len(matches)} [USER] split(s) hash to "
            f"llm_classifier_prompt_sha256; exactly one must")
    at = matches[0]
    return (prompt[len(_SYSTEM_HEAD):at], prompt[at + len(_USER_MARKER):])


def recover_patient_summary(system):
    """R2: the fenced patient record, exactly one fence pair, non-empty."""
    lines = system.split("\n")
    opens = [k for k, line in enumerate(lines) if line == _PATIENT_OPEN]
    closes = [k for k, line in enumerate(lines) if line == _PATIENT_CLOSE]
    if len(opens) != 1 or len(closes) != 1 or opens[0] >= closes[0]:
        raise _Inconsistent(
            f"the system half carries {len(opens)} patient-record open and "
            f"{len(closes)} close fence line(s); exactly one ordered pair must")
    text = "\n".join(lines[opens[0] + 1:closes[0]])
    if not text.strip():
        raise _Inconsistent("the fenced patient record is empty")
    return text


def partition_trial_blocks(user):
    """R3: ``[(nct_id, block_text)]``, a gapless partition of the trials text."""
    if not (user.startswith(_USER_HEAD) and user.endswith(_USER_TAIL)
            and len(user) >= len(_USER_HEAD) + len(_USER_TAIL)):
        raise _Inconsistent("the user half is not the CLINICAL TRIALS wrapper")
    trials_text = user[len(_USER_HEAD):len(user) - len(_USER_TAIL)]
    blocks, pos = [], 0
    while pos < len(trials_text):
        opened = _TRIAL_OPEN_RE.match(trials_text, pos)
        if opened is None:
            raise _Inconsistent(
                f"the trials text does not partition into fenced blocks: no "
                f"open fence at offset {pos} of {len(trials_text)}")
        nct_id = opened.group(1)
        close = f"\n<<<END_TRIAL_DATA nct_id={nct_id}>>>\n\n"
        end = trials_text.find(close, opened.end() - 1)
        if end < 0:
            raise _Inconsistent(f"trial block {nct_id} has no close fence")
        if _TRIAL_OPEN_TOKEN in trials_text[opened.end():end]:
            raise _Inconsistent(f"trial block {nct_id} contains another open "
                                f"fence before its close")
        stop = end + len(close)
        blocks.append((nct_id, trials_text[pos:stop]))
        pos = stop
    if not blocks:
        raise _Inconsistent("the stored prompt carries no trial block")
    return blocks


def _criteria_arrays(raw):
    try:
        details = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        details = None
    if (not isinstance(details, dict)
            or not isinstance(details.get("inclusion"), list)
            or not isinstance(details.get("exclusion"), list)):
        raise _Inconsistent("criterion_details is not an object carrying "
                            "inclusion and exclusion lists")
    return details["inclusion"], details["exclusion"]


def build_patient_record(first_row, trial_rows):
    """One exported record and its manifest entry. RAISES ``_Inconsistent``.

    Checks, in order: the row count equals ``candidates_evaluated``; no nct_id
    repeats; R1; R2; R3; the block set equals the trial-row set; each block's
    position equals its ``trial_number``; every criteria object parses.
    """
    candidates = first_row["candidates_evaluated"]
    if not isinstance(candidates, int) or len(trial_rows) != candidates:
        raise _Inconsistent(f"{len(trial_rows)} trial row(s) against "
                            f"candidates_evaluated {candidates!r}")
    ncts = [t["nct_id"] for t in trial_rows]
    if len(set(ncts)) != len(ncts):
        raise _Inconsistent("an nct_id repeats among this inference's trial "
                            "rows")
    system, user = split_stored_prompt(first_row["prompt"],
                                       first_row["llm_classifier_prompt_sha256"])
    summary = recover_patient_summary(system)
    blocks = partition_trial_blocks(user)
    block_ncts = [nct for nct, _ in blocks]
    if len(set(block_ncts)) != len(block_ncts):
        raise _Inconsistent("an nct_id repeats among the stored trial blocks")
    if set(block_ncts) != set(ncts):
        raise _Inconsistent(
            f"the stored trial blocks name {sorted(set(block_ncts) - set(ncts))} "
            f"without a trial row, and the trial rows name "
            f"{sorted(set(ncts) - set(block_ncts))} without a block")
    position = {nct: k for k, nct in enumerate(block_ncts, start=1)}

    contexts, verdicts = [], []
    for nct, block in blocks:
        contexts.append({"rank": position[nct], "nct_id": nct,
                         "trial_text": block, "trial_text_error": None})
    for row in sorted(trial_rows, key=lambda t: position[t["nct_id"]]):
        if row["trial_number"] != position[row["nct_id"]]:
            raise _Inconsistent(
                f"trial {row['nct_id']} has trial_number "
                f"{row['trial_number']!r} but is block "
                f"{position[row['nct_id']]} of the stored prompt")
        inclusion, exclusion = _criteria_arrays(row["criterion_details"])
        verdict = {"nct_id": row["nct_id"]}
        for key, column in _VERDICT_FIELD_FROM_COLUMN:
            verdict[key] = row[column]
        verdict["inclusion_criteria"] = inclusion
        verdict["exclusion_criteria"] = exclusion
        verdict["verdict_group"] = _GROUP_FOR_LABEL.get(row["eligible"],
                                                        _GROUP_OTHER)
        verdicts.append(verdict)

    decisions = sum(len(v["inclusion_criteria"]) + len(v["exclusion_criteria"])
                    for v in verdicts)
    not_evaluable = {}
    lost = {}
    placeholders = {}
    for v in verdicts:
        reason = v.get("not_evaluable_reason")
        if v["eligible"] == faithfulness_filter.TRIAL_VERDICT_NOT_EVALUABLE:
            label = reason or "<no reason recorded>"
            not_evaluable[label] = not_evaluable.get(label, 0) + 1
            if reason in faithfulness_filter.REASONS_CONSTRUCTED:
                lost[reason] = lost.get(reason, 0) + 1
        exclusion_key, _note = faithfulness_filter.classify(
            v, faithfulness_filter.RESPONSE_FIELD_ASSESSMENT)
        if exclusion_key is not None:
            placeholders[exclusion_key] = placeholders.get(exclusion_key, 0) + 1

    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "patient_id": first_row["patient_id"],
        "run": {
            "source": EXPORT_KIND,
            "inference_id": first_row["id"],
            "run_id": first_row["run_id"],
            "timestamp": first_row["timestamp"],
            "patient_data_hash": first_row["patient_data_hash"],
            "matching_model": first_row["matching_model"],
            "llm_classifier_prompt_version":
                first_row["llm_classifier_prompt_version"],
            "llm_classifier_prompt_sha256":
                first_row["llm_classifier_prompt_sha256"],
            "age_reference_date": first_row["age_reference_date"],
            "lost_trials": sum(lost.values()),
            "lost_trials_by_reason": dict(sorted(lost.items())),
        },
        "patient_summary": {
            "text": summary, "error": None,
            "source": ("inferences.llm_classifier_prompt, between the "
                       "PATIENT_RECORD fences: the text Stage 5 sent, after "
                       "fence neutralization")},
        "contexts": contexts,
        "verdicts": verdicts,
        "criterion_decision_count": decisions,
    }
    entry = {"file": None, "status": "ok", "inference_id": first_row["id"],
             "verdicts": len(verdicts), "contexts": len(contexts),
             "criterion_decisions": decisions,
             "lost_trials": sum(lost.values()),
             "lost_trials_by_reason": dict(sorted(lost.items())),
             "not_evaluable_by_reason": dict(sorted(not_evaluable.items())),
             "faithfulness_placeholders": dict(sorted(placeholders.items()))}
    return record, entry


#------------------------------------------------------------------------------
# The export
#------------------------------------------------------------------------------


def _record_filename(patient_id, taken):
    base = _SAFE_NAME_RE.sub("_", str(patient_id)) or "patient"
    name, k = f"{base}.json", 2
    while name in taken:
        name, k = f"{base}_{k}.json", k + 1
    taken.add(name)
    return name


def _write_json(path, payload):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)


def _require_output_dir_free(output_dir):
    parent = os.path.dirname(os.path.abspath(output_dir).rstrip(os.sep))
    if not os.path.isdir(parent):
        raise ExportRefusal(f"the parent of {output_dir!r} does not exist",
                            REFUSE_OUTPUT_PARENT_MISSING)
    if os.path.exists(output_dir) and (not os.path.isdir(output_dir)
                                       or os.listdir(output_dir)):
        raise ExportRefusal(f"{output_dir!r} exists and is not an empty "
                            f"directory", REFUSE_OUTPUT_NOT_EMPTY)


def _count(counter, key, n=1):
    counter[key] = counter.get(key, 0) + n


def first_admissions(history, snapshot, judged):
    """Validate explicit completion links, then select MAIN admissions."""
    rows = {r["id"]: r for r in snapshot["first_rows"]}
    patients = {p["bundle"]: p["patient_id"] for p in judged}
    linked, first, admitted_main = set(), {}, set()
    for a in history["attempts"]:
        c = a["completion"]
        if a["kind"] == "resample" and a["bundle"] not in admitted_main:
            raise AH.HistoryRefusal("attempt_history_uncovered", "resample has no preceding main admission")
        if a["kind"] != "resample":
            admitted_main.add(a["bundle"])
        if a["kind"] == "main":
            first.setdefault(a["bundle"], a)
        if c is None:
            continue
        pid = patients.get(a["bundle"])
        if pid is not None and c["patient_id"] is not None and c["patient_id"] != pid:
            raise AH.HistoryRefusal("attempt_history_mismatch", "bundle/patient mapping differs")
        iid = c["inference_id"]
        if iid is None:
            continue
        row = rows.get(iid)
        if (row is None or row["run_id"] != a["run_id"]
                or row["patient_id"] != c["patient_id"]
                or bool(row["error"]) != (c["outcome"] != "success")):
            raise AH.HistoryRefusal("attempt_history_mismatch", "completion references an absent or inconsistent inference")
        linked.add(iid)
    # A commit before completion persistence may leave an unlinked row, but
    # only an unfinished admission can explain it. Never assign it by guessing.
    by_patient = {p["patient_id"]: p["bundle"] for p in judged}
    for row in rows.values():
        key = by_patient.get(row["patient_id"])
        if key is not None and row["id"] not in linked:
            if not any(a["bundle"] == key and a["run_id"] == row["run_id"]
                       and a["completion"] is None for a in history["attempts"]):
                raise AH.HistoryRefusal("attempt_history_uncovered", "a judged inference has no admission/completion evidence")
    return first


def validate_row_shape(row, trials):
    """Applicable even on an error: zero-candidate failures remain valid."""
    count = row["candidates_evaluated"]
    if not row["prompt"] and (count != 0 or trials):
        raise _Inconsistent(f"no stored prompt beside candidates_evaluated {count!r} and {len(trials)} trial row(s)")
    if type(count) is not int or count < 0 or count != len(trials):
        raise _Inconsistent(f"{len(trials)} trial row(s) against candidates_evaluated {count!r}")
    if row["prompt"] and count == 0:
        raise _Inconsistent("stored prompt with zero candidates_evaluated")
    if len({t["nct_id"] for t in trials}) != len(trials):
        raise _Inconsistent("an nct_id repeats among this inference's trial rows")


def export_campaign(source_db, campaign_id, output_dir, fhir_dir, *, out=None,
                    _between_reads=None):
    """Build and write the export. Returns the manifest. RAISES ``ExportRefusal``."""
    emit = console.out if out is None else out
    for name, value in (("source_db", source_db), ("campaign_id", campaign_id),
                        ("output_dir", output_dir), ("fhir_dir", fhir_dir)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"export_campaign: {name} is required")
    _require_output_dir_free(output_dir)

    try:
        with AH.snapshot_lock(source_db, campaign_id) as history:
            snapshot = read_campaign_snapshot(source_db, campaign_id, _between_reads)
            AH.require_coverage(history, campaign_id, snapshot["runs"])
    except AH.HistoryRefusal as exc:
        if exc.code == "attempt_history_missing":
            # Preserve source/schema/campaign diagnostics on unsupported input.
            # This read is only a refusal path, never a certified export.
            read_campaign_snapshot(source_db, campaign_id)
        raise ExportRefusal(str(exc), exc.code) from exc
    recorded = require_runs_agree(snapshot["runs"], campaign_id)
    selection, files = recompute_cohort(fhir_dir, recorded, emit)
    stem_to_path = map_judge_stems(selection.judge_stems, files)
    judged = identify_judged(stem_to_path)

    try:
        AH.require_coverage(history, campaign_id, snapshot["runs"], selection.digest,
                            [AH.bundle_key(p) for p in files
                             if cohort.stem_of(p) in selection.stems])
        first_by_bundle = first_admissions(history, snapshot, judged)
    except AH.HistoryRefusal as exc:
        raise ExportRefusal(str(exc), exc.code) from exc
    rows_by_id = {r["id"]: r for r in snapshot["first_rows"]}

    outcomes, runs, records = {}, {}, []
    counters = {o: 0 for o in OUTCOMES}
    counters.update({"judge_sample": len(judged),
                     "first_attempt_failed_with_later_success": 0,
                     "trials_exported": 0, "criterion_decisions": 0,
                     "patients_with_lost_trials": 0})
    lost_by_reason, not_evaluable_by_reason, placeholders = {}, {}, {}

    for patient in judged:
        patient_id = patient["patient_id"]
        admission = first_by_bundle.get(patient["bundle"])
        if admission is None:
            outcomes[patient_id] = {"outcome": OUTCOME_NOT_IN_CAMPAIGN,
                                    "inference_id": None}
            _count(counters, OUTCOME_NOT_IN_CAMPAIGN)
            continue
        completed = admission["completion"]
        later = any(a["bundle"] == patient["bundle"]
                    and a["sequence"] > admission["sequence"]
                    and a["completion"] is not None
                    and a["completion"]["outcome"] == "success"
                    and a["completion"]["inference_id"] is not None
                    for a in history["attempts"])
        evidence = {"attempt_id": admission["id"],
                    "admission_sequence": admission["sequence"],
                    "run_id": admission["run_id"],
                    "later_success_in_campaign": later}
        row = rows_by_id.get(completed["inference_id"]) if completed else None
        if row is None:
            outcome = (OUTCOME_INTERRUPTED if completed is None else
                       OUTCOME_FIRST_ATTEMPT_FAILED if completed["outcome"] != "success"
                       else OUTCOME_UNAVAILABLE)
            outcomes[patient_id] = {"outcome": outcome, "inference_id": None, **evidence}
            _count(counters, outcome)
            if outcome == OUTCOME_FIRST_ATTEMPT_FAILED and later:
                _count(counters, "first_attempt_failed_with_later_success")
            continue
        if row["patient_data_hash"] != patient["computed_hash"]:
            raise ExportRefusal(
                f"inference {row['id']} (patient {patient_id}) stores "
                f"patient_data_hash {row['patient_data_hash']!r}; the bundle "
                f"parsed today hashes to {patient['computed_hash']!r}. The "
                f"stored content identity is absent or differs; a missing hash "
                f"does not establish that the parser or bundle changed.",
                REFUSE_HASH_MISMATCH)
        trial_rows = snapshot["trials"].get(row["id"], [])
        try:
            validate_row_shape(row, trial_rows)
            # Errors do not exempt persisted text from R1-R4 integrity. Build
            # once before classification; failed attempts are never published.
            if row["prompt"]:
                record, entry = build_patient_record(row, trial_rows)
        except _Inconsistent as exc:
            raise ExportRefusal(f"inference {row['id']}: {exc}", REFUSE_INCONSISTENT)
        if row["error"]:
            outcomes[patient_id] = {"outcome": OUTCOME_FIRST_ATTEMPT_FAILED,
                                    "inference_id": row["id"], **evidence}
            _count(counters, OUTCOME_FIRST_ATTEMPT_FAILED)
            if later:
                _count(counters, "first_attempt_failed_with_later_success")
            continue
        if not row["prompt"]:
            outcomes[patient_id] = {
                "outcome": OUTCOME_NOTHING_TO_EVALUATE,
                "inference_id": row["id"], **evidence}
            _count(counters, OUTCOME_NOTHING_TO_EVALUATE)
            continue
        records.append((patient_id, record, entry))
        outcomes[patient_id] = {"outcome": OUTCOME_EXPORTED,
                                "inference_id": row["id"], **evidence}
        _count(counters, OUTCOME_EXPORTED)
        counters["trials_exported"] += entry["verdicts"]
        counters["criterion_decisions"] += entry["criterion_decisions"]
        if entry["lost_trials"]:
            counters["patients_with_lost_trials"] += 1
        for source, target in ((entry["lost_trials_by_reason"], lost_by_reason),
                               (entry["not_evaluable_by_reason"],
                                not_evaluable_by_reason),
                               (entry["faithfulness_placeholders"],
                                placeholders)):
            for key, n in source.items():
                _count(target, key, n)

    reference_dates = sorted({r["run"]["age_reference_date"]
                              for _, r, _ in records},
                             key=lambda v: (v is None, v))
    if len(reference_dates) > 1:
        raise ExportRefusal(
            f"the exported rows carry {len(reference_dates)} different "
            f"age_reference_date values {reference_dates}; one campaign's "
            f"rules render one reference date", REFUSE_INCONSISTENT)

    taken = set()
    for patient_id, record, entry in records:
        entry["file"] = _record_filename(patient_id, taken)
        runs[patient_id] = entry

    fingerprint = {c: snapshot["runs"][0].get(c)
                   for c in RUN_FINGERPRINT_COLUMNS}
    manifest = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "kind": EXPORT_KIND,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {"database_path": os.path.abspath(source_db),
                   "schema_era": snapshot["era"],
                   "read": "one read transaction on a mode=ro connection"},
        "campaign": {"billing_campaign_id": campaign_id,
                     "run_ids": [r["id"] for r in snapshot["runs"]],
                     "run_statuses": {str(r["id"]): r["status"]
                                      for r in snapshot["runs"]},
                     # A run with no finished_at is live or was killed hard.
                     # Its rows are read consistently, but the campaign may not
                     # be complete; stated, not refused.
                     "unfinalized_run_ids": [r["id"] for r in snapshot["runs"]
                                             if r["finished_at"] is None]},
        "selection_rule": SELECTION_RULE,
        "attempt_history": {"version": history["version"],
                            "sha256": AH.document_digest(history),
                            "admissions": len(history["attempts"]),
                            "start_means": "admission, not proof of model execution"},
        "fingerprint": fingerprint,
        "cohort": {
            "corpus_dir": os.path.abspath(fhir_dir),
            "corpus_bundles": len(files),
            "draw_algorithm": cohort.STRATIFIED_DRAW_ALGORITHM,
            "recorded_cohort_size": recorded["campaign_cohort_size"],
            "recorded_cohort_seed": recorded["campaign_cohort_seed"],
            "recorded_cohort_digest": recorded["cohort_digest"],
            "recomputed_cohort_digest": selection.digest,
            "cohort_size": selection.size,
            "judge_sample_seed": recorded["judge_sample_seed"],
            "judge_sample_size": recorded["judge_sample_size"],
            "judge_sample_digest": cohort.digest(selection.judge_stems),
            "digest_proves": ("which stems were selected; per-patient content "
                              "is verified by patient_data_hash"),
        },
        "environment": {
            "age_reference_date": reference_dates[0] if reference_dates else None,
            **fingerprint,
        },
        "faithfulness_filter": faithfulness_filter.FILTER_IDENTITY,
        "counters": {
            **counters,
            "lost_trials_by_reason": dict(sorted(lost_by_reason.items())),
            "not_evaluable_trials_by_reason":
                dict(sorted(not_evaluable_by_reason.items())),
            "faithfulness_placeholders_by_exclusion":
                dict(sorted(placeholders.items())),
        },
        "outcomes": outcomes,
        "runs": runs,
        "totals": {"records": len(runs),
                   "verdicts": counters["trials_exported"],
                   "criterion_decisions": counters["criterion_decisions"]},
    }

    _require_output_dir_free(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    for patient_id, record, entry in records:
        _write_json(os.path.join(output_dir, entry["file"]), record)
    _write_json(os.path.join(output_dir, MANIFEST_FILENAME), manifest)

    emit(f"[Export] campaign {campaign_id}: judge sample "
         f"{counters['judge_sample']}, exported {counters[OUTCOME_EXPORTED]}, "
         f"first attempt failed {counters[OUTCOME_FIRST_ATTEMPT_FAILED]} "
         f"({counters['first_attempt_failed_with_later_success']} with a later "
         f"success, not substituted), nothing to evaluate "
         f"{counters[OUTCOME_NOTHING_TO_EVALUATE]}, not in campaign "
         f"{counters[OUTCOME_NOT_IN_CAMPAIGN]}, interrupted "
         f"{counters[OUTCOME_INTERRUPTED]}, result unavailable "
         f"{counters[OUTCOME_UNAVAILABLE]}")
    if manifest["campaign"]["unfinalized_run_ids"]:
        emit(f"[Export] NOTE: run(s) {manifest['campaign']['unfinalized_run_ids']} "
             f"of this campaign never finalized; the campaign may still be "
             f"running or was killed without a crash record")
    emit(f"[Export] {counters['trials_exported']} trials, "
         f"{counters['criterion_decisions']} criterion decisions, "
         f"{sum(lost_by_reason.values())} lost trial(s) across "
         f"{counters['patients_with_lost_trials']} patient(s) -> {output_dir}")
    return manifest


#------------------------------------------------------------------------------
# CLI
#------------------------------------------------------------------------------


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Export one campaign's first main admissions as an evaluation "
                    "run directory for the rater and the Ragas harness. "
                    "Reads only; calls no model.")
    parser.add_argument("--source-db", required=True,
                        help="the campaign database, opened read-only")
    parser.add_argument("--campaign-id", required=True,
                        help="runs.billing_campaign_id of the campaign")
    parser.add_argument("--output-dir", required=True,
                        help="a new or empty directory to write into")
    parser.add_argument("--fhir-dir", required=True,
                        help="the bundle directory the campaign drew its "
                             "cohort from")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    try:
        export_campaign(args.source_db, args.campaign_id, args.output_dir,
                        args.fhir_dir)
    except ExportRefusal as exc:
        console.out(f"REFUSED ({exc.code}): {exc}")
        return EXIT_REFUSED
    return EXIT_OK


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep 14 2026

@author: ramyalsaffar
"""
