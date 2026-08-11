# Evaluation Run Harness
########################

"""Run the pipeline over a stratified slice of the cohort and persist what two
downstream harnesses will consume: an LLM rater of criterion decisions, and
Ragas retrieval metrics.

THIS COSTS MONEY. Every selected patient is one real end-to-end run including a
live billed Stage 5 call. Ten patients is of the order of one dollar at
2026-08 prices; the entry point prints the projection and the manifest records
what was actually spent.

WHAT IT IS NOT
--------------
It is not a fixture capture. ``oncotriage/fixtures/capture.py`` records the
pipeline's internals -- every embedding, every cross-encoder score, every chat
completion -- so a refactor can be replayed against them for free. This records
the pipeline's OUTPUTS in the shape two evaluators need, and nothing here reads,
writes or validates a fixture.

It is not a batch run either: it opens no database and calls ``log_inference``
nowhere. ``oncotriage/batch/runner.py`` is the thing that writes ``inferences``
rows, and pointing an evaluation slice at the production database would put ten
rows in it that no production run produced.

WHAT IT PERSISTS, AND WHY EACH PIECE
------------------------------------
One JSON per patient plus a manifest, all under ``--output-dir``:

  run provenance      which model answered, which prompt version and hash, which
                      Qdrant collection, which terminal node, the token counts
                      and the priced cost. A rating produced against one prompt
                      version is not evidence about another.
  patient_summary     the EXACT text Stage 5 was shown, via
                      ``_create_patient_summary``. An LLM rater judging whether
                      a criterion decision follows from the patient record has
                      to see the record the decider saw, not a re-derivation of
                      it.
  contexts            one entry per trial in ``state["filtered_trials"]``, each
                      carrying that trial's fenced criteria text via
                      ``_build_trials_text([entry])`` -- ONE CALL PER TRIAL so
                      each context is separable, which is what Ragas needs, and
                      the retrieval scores that ranked it.
  verdicts            every entry of ``matches`` + ``near_misses`` +
                      ``not_evaluable``, verbatim, including the complete
                      inclusion/exclusion criteria arrays. One entry of one of
                      those arrays is one criterion decision, and that count is
                      the unit both evaluators are sized in.

BOTH RENDERERS ARE IMPORTED, NEVER REIMPLEMENTED. ``_build_trials_text`` and
``_create_patient_summary`` are the functions Stage 5 itself calls. A local copy
would agree with the pipeline on the day it was written and drift silently
afterwards, and the whole value of this artifact is that the text in it is the
text the model saw. Both are deliberately criteria-only on the trial side: the
title, the conditions and the brief summary are stripped before the model sees
them, so a rater given the trial's title would be judging on evidence the
pipeline withheld.

NOTHING AFTER ``graph.invoke`` MAY LOSE A PAID RUN. Rendering the summary,
rendering a context and stamping the collection all happen after the money is
spent, so each is wrapped and each failure is RECORDED INTO THE RECORD rather
than raised. A harness that threw away a completed billed run because a
downstream render failed would be the most expensive possible way to fail.

DETERMINISM. Selection is a pure function of the scan rows: candidates are
ordered by bundle filename and every tie breaks on that name, so the same cohort
selects the same patients on every run and a subset can be re-run by patient id.
"""

import argparse
import glob
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from oncotriage import paths
from oncotriage.agent import readiness
from oncotriage.agent.evaluation import _build_trials_text
from oncotriage.agent.graph import build_initial_state, build_matching_graph
from oncotriage.agent.patient import _create_patient_summary, compute_patient_hash
from oncotriage.agent.prompts import PROMPT_VERSION
from oncotriage.agent.state import EXPANSION_PATH_FALLBACK
from oncotriage.config import (
    COLLECTION_NAME,
    DATA_SNAPSHOT_DATE,
    MATCHING_MODEL,
    Project_Name,
)
from oncotriage.fhir.parser import parse_fhir_bundle
from oncotriage.fixtures.capture import scan_cohort
from oncotriage.observability import console, correlation_scope, get_logger
from oncotriage.utils import (
    CaffeinateSession,
    UnknownModelPricingError,
    get_age_reference_date,
    get_model_cost,
    resolve_qdrant_collection,
)


log = get_logger(__name__)


#------------------------------------------------------------------------------


# ===========================================================================
# CONSTANTS
# ===========================================================================

# Bumped when the shape of a persisted record changes. The post-check refuses a
# record whose version it does not know, for the reason load_fixture() refuses
# one: a consumer that reads a v1 field out of a v2 file gets None and reports
# it as a missing value rather than as a version mismatch.
RECORD_SCHEMA_VERSION = 1

MANIFEST_FILENAME = "manifest.json"
OUTPUT_DIR_PREFIX = "eval_run_"
DEFAULT_SELECTION_SIZE = 10

# The three groups node_finalize splits `evaluations` into, in the order a
# reader wants them. Kept as a tuple rather than written out at each use so the
# per-patient record, the criterion count and the post-check cannot disagree
# about which lists carry verdicts.
VERDICT_GROUPS = ("matches", "near_misses", "not_evaluable")

# Result keys NOT copied into the per-patient record's `result` block, each with
# a reason. Everything else is copied verbatim, which is what makes "any
# degradation, refusal or error field the result carries" true by construction
# rather than by an enumeration that rots the next time a stage adds a key.
RESULT_OMITTED_KEYS = {
    # Persisted in full under "verdicts", with the group each came from.
    "matches": "persisted under verdicts",
    "near_misses": "persisted under verdicts",
    "not_evaluable": "persisted under verdicts",
    # The whole rendered Stage 5 user message: the patient summary and every
    # trial's criteria concatenated. Both halves are persisted separately and
    # separably above, so this is the same bytes a third time and the largest
    # single field in the record. Its sha256 is kept in provenance.
    "llm_classifier_prompt": "patient_summary + contexts carry the same text, separably",
}

# What the post-check requires of every persisted record. A record missing one
# of these is unusable to a downstream harness, and the point of the check is to
# find that out for free rather than half way through a rating run.
REQUIRED_RECORD_KEYS = (
    "schema_version", "patient_id", "run", "patient_summary",
    "contexts", "verdicts", "criterion_decision_count", "result",
)
REQUIRED_RUN_KEYS = (
    "captured_at_utc", "bundle", "terminal_node", "matching_model",
    "qdrant_collection", "llm_classifier_prompt_version",
    "llm_classifier_prompt_sha256", "llm_classifier_input_tokens",
    "llm_classifier_output_tokens", "cost",
)

# Per-patient outcome vocabulary. Closed: the manifest writer asserts membership,
# so a status invented at a new call site fails here rather than reaching a
# reader who has to guess what it meant.
STATUS_OK = "ok"                        # a terminal result was produced and persisted
STATUS_NOTHING_TO_EVALUATE = "nothing_to_evaluate"   # node_no_candidates: valid, empty
STATUS_PIPELINE_ERROR = "pipeline_error"             # node_error_handler: the graph's own
STATUS_FAILED = "failed"                # an exception escaped graph.invoke
RUN_STATUSES = (STATUS_OK, STATUS_NOTHING_TO_EVALUATE,
                STATUS_PIPELINE_ERROR, STATUS_FAILED)

# Why each patient is in the selection. Closed for the same reason.
REASON_FALLBACK = "expansion_path_fallback"
REASON_UNKNOWN_STAGE = "unknown_stage"
REASON_SPREAD = "spread"
SELECTION_REASONS = (REASON_FALLBACK, REASON_UNKNOWN_STAGE, REASON_SPREAD)

# Exit codes, stated here because three callers read them.
EXIT_OK = 0
EXIT_PRECONDITION = 1     # refused before spending anything
EXIT_INCOMPLETE = 2       # the run happened; something in it did not

_RESOLVED = {}
# Locked to match oncotriage/fixtures/capture.py, oncotriage/paths.py and
# oncotriage/agent/deps.py. Nothing here is multi-threaded -- patients run one
# at a time, on purpose, because a paid run is easier to reason about serially
# -- and the lock is about the pattern the next accessor added here will copy.
_RESOLVE_LOCK = threading.RLock()


#------------------------------------------------------------------------------


# ===========================================================================
# WHERE OUTPUT GOES
# ===========================================================================

def evaluation_root() -> str:
    """Where evaluation runs live. Resolved on first call, cached. CREATES NOTHING.

    THE PROJECT ROOT ITSELF WOULD HAVE BEEN THE WRONG ANSWER, and this is the
    one place this module departs from the letter of its brief. That root is a
    numbered directory tree (``01- Project Blueprint`` ... ``15- Code Copies``)
    and dropping ``eval_run_20260811_120000`` beside them breaks the one
    convention the whole layout has. ``oncotriage/fixtures/capture.py``'s
    ``fixture_root()`` had the same choice and answered it by globbing
    ``*Testing`` -- which is what "outside the repo, like the fixture directory"
    actually points at -- so this resolves the same directory and takes a
    subdirectory of it. ``--output-dir`` overrides, and the manifest records the
    absolute path either way.

    The glob is ``sorted()[0]`` rather than ``paths._glob_one``: this is a
    DESTINATION, and a project root carrying two ``*Testing`` directories should
    put an evaluation run in the first one deterministically rather than refuse
    to run. ``_glob_one`` raises on ambiguity because its callers RESOLVE INPUTS
    -- reading the wrong corpus, or deleting bundles out of it.
    """
    with _RESOLVE_LOCK:
        if "evaluation_root" not in _RESOLVED:
            matches = sorted(glob.glob(os.path.join(paths.main_path, "*Testing")))
            testing_dir = matches[0] if matches else os.path.join(
                paths.main_path, "09- Testing")
            _RESOLVED["evaluation_root"] = os.path.join(
                testing_dir, "Evaluation Runs")
        return _RESOLVED["evaluation_root"]


def new_output_dir(stamp: str) -> str:
    """The default destination for one run. Takes the timestamp; creates nothing.

    The stamp is an ARGUMENT rather than read from the clock in here, so the
    manifest's ``created_at_utc`` and the directory name are the same instant by
    construction rather than by two clock reads that can straddle a second.
    """
    return os.path.join(evaluation_root(), OUTPUT_DIR_PREFIX + stamp)


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def record_filename(patient_id: str, taken: set) -> str:
    """A filesystem-safe, collision-free name for one patient's record.

    Synthea patient ids are UUIDs and need none of this. It is here because the
    id is third-party data reaching a path expression: a bundle carrying an id
    with a ``/`` in it would write outside the output directory, and two ids
    differing only in a character this replaces would silently overwrite each
    other -- one paid run destroying another with no error at all. ``taken`` is
    the set of names already issued THIS run; a repeat gets a numeric suffix.
    """
    base = _SAFE_NAME_RE.sub("_", str(patient_id or "")).strip("._-") or "patient"
    if base == os.path.splitext(MANIFEST_FILENAME)[0]:
        # A patient literally called "manifest" would otherwise overwrite the
        # manifest. Vanishingly unlikely and free to prevent.
        base = base + "_patient"
    candidate = base
    suffix = 1
    while candidate + ".json" in taken:
        suffix += 1
        candidate = f"{base}__{suffix}"
    taken.add(candidate + ".json")
    return candidate + ".json"


#------------------------------------------------------------------------------


# ===========================================================================
# SELECTION  (pure: a function of the scan rows and nothing else)
# ===========================================================================

def _diversity_pick(candidates: List[Dict], selected: List[Dict]) -> Dict:
    """The candidate that adds the most spread, ties broken by bundle name.

    Ranked on (how many already-selected patients share this primary diagnosis,
    how many share this stage, bundle name). Minimising the first term takes a
    new diagnosis whenever one is available, which is what "as many distinct
    primary_diagnosis values as available" asks for; the second term then
    spreads stages within a diagnosis that has to repeat. The third makes the
    whole thing a total order, so there is no tie left for dict or filesystem
    ordering to decide.
    """
    diag_counts = {}
    stage_counts = {}
    for entry in selected:
        row = entry["row"]
        diag_counts[row["primary_diagnosis"]] = \
            diag_counts.get(row["primary_diagnosis"], 0) + 1
        stage_counts[row["stage"]] = stage_counts.get(row["stage"], 0) + 1

    def _key(row):
        return (diag_counts.get(row["primary_diagnosis"], 0),
                stage_counts.get(row["stage"], 0),
                row["bundle"])

    return min(candidates, key=_key)


def _spread_note(row: Dict, selected: List[Dict]) -> str:
    """What this pick adds, stated at the moment it is made."""
    diagnoses = {e["row"]["primary_diagnosis"] for e in selected}
    stages = {e["row"]["stage"] for e in selected}
    gains = []
    if row["primary_diagnosis"] not in diagnoses:
        gains.append("new primary diagnosis")
    if row["stage"] not in stages:
        gains.append(f"new stage ({row['stage']})")
    if not gains:
        return ("no unseen diagnosis or stage remained; least-represented of "
                "both, first by bundle name")
    return " and ".join(gains)


def select_patients(rows: List[Dict], size: int) -> Tuple[List[Dict], List[str]]:
    """Choose the evaluation slice. Pure, deterministic, and it states its misses.

    Returns ``(selection, deviations)``. Each selection entry is
    ``{"row", "reason", "note"}``; ``reason`` is one of SELECTION_REASONS and
    ``note`` says what that pick bought.

    THE TWO HARD STRATA ARE TAKEN FIRST BECAUSE THEY ARE CONSTRAINTS, not
    preferences, and a diversity pass that happened to satisfy them would stop
    satisfying them the day the cohort changed:

      1. EXACTLY ONE patient whose Stage 1 took EXPANSION_PATH_FALLBACK. Exactly
         one, so every other fallback patient is removed from the pool
         afterwards -- that branch degrades the query to demographics plus the
         diagnosis display, and a slice with three of them is measuring the
         degraded path three times instead of measuring the pipeline.
      2. At least one patient with no determinable cancer stage, which is what
         makes Stage 4's stage filter skip. Already satisfied if the fallback
         patient happens to be unstaged, and checked rather than assumed.

    Everything after that is the diversity greedy. A stratum that cannot be
    filled is a recorded deviation and never a raise: the cohort is an input,
    and refusing to run because it lacks a fallback patient would make this
    harness unusable on any cohort but today's.
    """
    by_bundle = sorted(rows, key=lambda r: r["bundle"])
    selection: List[Dict] = []
    deviations: List[str] = []
    taken = set()

    def _take(row, reason, note):
        assert reason in SELECTION_REASONS, f"unknown selection reason {reason!r}"
        taken.add(row["bundle"])
        selection.append({"row": row, "reason": reason, "note": note})

    if size < 1:
        return [], ["--select must be at least 1; nothing was selected"]

    # --- 1. the one fallback patient ---------------------------------------
    fallback = [r for r in by_bundle
                if r["expansion_path"] == EXPANSION_PATH_FALLBACK]
    if fallback:
        _take(fallback[0], REASON_FALLBACK,
              "the one EXPANSION_PATH_FALLBACK patient (first by bundle name); "
              f"{len(fallback)} in the cohort")
    else:
        deviations.append(
            "no patient in this cohort takes EXPANSION_PATH_FALLBACK, so the "
            "degraded-expansion stratum is empty; the slice covers the MeSH "
            "path only")

    # Every OTHER fallback patient leaves the pool. "Exactly one" is the
    # requirement, and the diversity greedy has no reason not to pick a second.
    pool = [r for r in by_bundle
            if r["bundle"] not in taken
            and r["expansion_path"] != EXPANSION_PATH_FALLBACK]

    # --- 2. at least one unstaged patient ----------------------------------
    have_unstaged = any(e["row"]["stage"] is None for e in selection)
    if len(selection) < size and not have_unstaged:
        unstaged = [r for r in pool if r["stage"] is None]
        if unstaged:
            row = _diversity_pick(unstaged, selection)
            _take(row, REASON_UNKNOWN_STAGE,
                  "no determinable cancer stage, so Stage 4's stage filter "
                  "skips; " + _spread_note(row, selection))
        else:
            deviations.append(
                "no patient in this cohort has an undeterminable cancer stage, "
                "so the unknown-stage stratum is empty")
    elif have_unstaged:
        # Recorded rather than silent: a reader comparing the selection against
        # the brief needs to know the stratum was satisfied by the pick above
        # rather than skipped.
        deviations.append(
            "the unknown-stage stratum was satisfied by the fallback patient, "
            "who is also unstaged; no separate slot was spent on it")

    # --- 3. the rest, by spread --------------------------------------------
    while len(selection) < size:
        remaining = [r for r in pool if r["bundle"] not in taken]
        if not remaining:
            deviations.append(
                f"the cohort offers only {len(selection)} selectable patient(s) "
                f"after the strata above; {size} were requested")
            break
        row = _diversity_pick(remaining, selection)
        _take(row, REASON_SPREAD, _spread_note(row, selection))

    return selection, deviations


def selection_table(selection: List[Dict]) -> List[Dict]:
    """The selection as the manifest stores it and the console prints it.

    The bundle's absolute PATH is deliberately not in here: it is a property of
    the machine that ran the scan, and the manifest records the bundle directory
    once. The filename is what re-identifies a patient across machines.
    """
    table = []
    for index, entry in enumerate(selection, start=1):
        row = entry["row"]
        table.append({
            "index": index,
            "patient_id": row["patient_id"],
            "bundle": row["bundle"],
            "primary_diagnosis": row["primary_diagnosis"],
            "stage": row["stage"],
            "expansion_path": row["expansion_path"],
            "mesh_resolution": row["mesh_resolution"],
            "ecog": row["ecog"],
            "reason": entry["reason"],
            "note": entry["note"],
        })
    return table


def print_selection(table: List[Dict], deviations: List[str]) -> None:
    console.out(f"\n{'-' * 78}\nSELECTION ({len(table)} patient(s))\n{'-' * 78}")
    console.out(f"{'#':>2}  {'bundle':<40} {'stage':>5}  {'path':<18} "
                f"{'ecog':>4}  diagnosis")
    for entry in table:
        console.out(
            f"{entry['index']:>2}  {entry['bundle'][:40]:<40} "
            f"{str(entry['stage']):>5}  {entry['expansion_path'][:18]:<18} "
            f"{str(entry['ecog']):>4}  {entry['primary_diagnosis'][:44]}")
        console.out(f"      -> {entry['reason']}: {entry['note']}")
    if deviations:
        console.out("\n  DEVIATIONS FROM THE REQUESTED STRATIFICATION:")
        for note in deviations:
            console.out(f"    * {note}")
    else:
        console.out("\n  Every requested stratum was filled.")

    # Non-degenerate spread is worth stating as a number rather than left for a
    # reader to count off the table.
    console.out(f"\n  distinct primary diagnoses: "
                f"{len({e['primary_diagnosis'] for e in table})}")
    console.out(f"  distinct stages:            "
                f"{sorted({str(e['stage']) for e in table})}")


#------------------------------------------------------------------------------


# ===========================================================================
# WHAT ONE RUN COST
# ===========================================================================

def price_run(result: Dict) -> Dict:
    """Price this run's Stage 5 usage from what the API reported.

    Returns cost_usd, cost_complete, and a `notes` list naming every reason the
    figure is not the whole truth. It NEVER raises and NEVER silently returns a
    zero for an unpriceable model: this is a terminal report on money that is
    already spent, so refusing to print a number would fail a run that
    succeeded, and a real 0.0 for an unpriced model is the defect item 38
    removed from the cost query -- a value every aggregate absorbs without
    knowing it is missing.

    ``reasoning_tokens`` are deliberately NOT added to the output tokens: they
    are a SUBSET of them, and adding them bills every reasoning token twice.

    THE RETRY CASE IS UNDER-REPORTED AND SAYS SO. Stage 5's token counters are
    local to one invocation of ``node_llm_classifier_evaluation`` and start at
    zero each time, while a JSON-parse failure routes the graph back INTO that
    node -- so a run with retries reports the tokens of its LAST attempt only,
    and every earlier attempt was billed and is invisible here. That is a
    property of the pipeline, not of this function; what this function must not
    do is present the resulting figure as complete.
    """
    model = result.get("matching_model")
    tokens_in = result.get("llm_classifier_input_tokens") or 0
    tokens_out = result.get("llm_classifier_output_tokens") or 0
    retries = result.get("llm_classifier_retries") or 0
    notes = []

    if model is None:
        # No Stage 5 response was ever obtained: node_no_candidates emptied the
        # pool, or the run died before the first call returned. Zero here is a
        # measurement, not a fallback, and cost_complete stays True.
        if tokens_in or tokens_out:
            notes.append(
                f"no model was recorded but {tokens_in}+{tokens_out} tokens "
                f"were: the run cannot be priced")
            return {"cost_usd": None, "cost_complete": False,
                    "model": None, "tokens_in": tokens_in,
                    "tokens_out": tokens_out, "notes": notes}
        notes.append("no Stage 5 call was made")
        return {"cost_usd": 0.0, "cost_complete": True, "model": None,
                "tokens_in": 0, "tokens_out": 0, "notes": notes}

    if retries:
        notes.append(
            f"llm_classifier_retries={retries}: Stage 5's token counters report "
            f"the final attempt only, so this is a FLOOR on what was billed")

    try:
        cost = get_model_cost(model, tokens_in, tokens_out)
    except UnknownModelPricingError as exc:
        notes.append(f"{model} is not in PRICING_CONFIG: {exc}")
        return {"cost_usd": None, "cost_complete": False, "model": model,
                "tokens_in": tokens_in, "tokens_out": tokens_out,
                "notes": notes}

    return {"cost_usd": cost, "cost_complete": not retries, "model": model,
            "tokens_in": tokens_in, "tokens_out": tokens_out, "notes": notes}


def count_criterion_decisions(verdicts: List[Dict]) -> int:
    """One entry of one inclusion/exclusion array of one verdicted trial = one.

    This is the unit both downstream harnesses are sized in, so it is computed
    once here and read by the manifest, the console summary and the post-check
    rather than by three loops that can disagree.
    """
    total = 0
    for verdict in verdicts:
        for key in ("inclusion_criteria", "exclusion_criteria"):
            arm = verdict.get(key)
            if isinstance(arm, list):
                total += len(arm)
    return total


#------------------------------------------------------------------------------


# ===========================================================================
# BUILDING ONE RECORD
# ===========================================================================

def build_contexts(filtered_trials: List[Dict]) -> Tuple[List[Dict], List[str]]:
    """One retrieval context per trial Stage 5 was sent.

    ``_build_trials_text`` is called with a ONE-ELEMENT list per trial so each
    context is a separable unit -- a Ragas context precision or recall score is
    computed per retrieved context, and one string holding fifteen trials is one
    context whatever it contains. The text is byte-for-byte what that trial
    contributed to the Stage 5 message, fences included, because it is the same
    function that produced that message.

    Per-trial failures are recorded and skipped rather than raised: this runs
    after the billed call, and one malformed trial payload must not cost the
    whole run.
    """
    contexts = []
    problems = []
    for index, entry in enumerate(filtered_trials, start=1):
        trial = entry.get("trial") or {}
        nct_id = trial.get("nct_id")
        text = None
        error = None
        try:
            text = _build_trials_text([entry])
        except Exception as exc:                     # noqa: BLE001 -- recorded below
            error = f"{type(exc).__name__}: {exc}"
            problems.append(f"context {index} ({nct_id}): {error}")
        contexts.append({
            "rank": index,
            "nct_id": nct_id,
            "trial_text": text,
            "trial_text_error": error,
            "rerank_score": entry.get("rerank_score"),
            "rerank_score_raw": entry.get("rerank_score_raw"),
            "medcpt_score_max": entry.get("medcpt_score_max"),
        })
    return contexts, problems


def collect_verdicts(result: Dict) -> List[Dict]:
    """Every verdicted trial, verbatim, tagged with the group it came from.

    Verbatim rather than projected onto a field list: the brief names
    nct_id / eligible / match_score / assessment and the two criteria arrays,
    and node_finalize also merges in rerank_score, rerank_score_raw, mesh_boost,
    mesh_boost_tier and trial_number. A projection would silently drop whichever
    of those a future rater turns out to need, and the cost of keeping them is
    a few kilobytes.
    """
    verdicts = []
    for group in VERDICT_GROUPS:
        for entry in result.get(group) or []:
            record = dict(entry)
            record["verdict_group"] = group
            verdicts.append(record)
    return verdicts


def _json_default(value):
    """Make the one non-JSON type the pipeline produces serialisable, loudly.

    ``set`` appears on state (patient_trees, patient_histology) and could reach
    a result key a future stage adds. Anything else becomes its repr, which is
    lossy -- so ``write_json`` reports every coercion it made and the record
    carries the report.
    """
    if isinstance(value, (set, frozenset)):
        return sorted(str(v) for v in value)
    return repr(value)


def write_json(path: str, payload: Dict) -> List[str]:
    """Write one JSON file, reporting every type coercion it had to make.

    Written to a temporary file and renamed, so a crash part way through leaves
    the previous record intact rather than a truncated one that parses as far as
    the truncation and then does not.
    """
    coercions = []

    def _default(value):
        rendered = _json_default(value)
        if not isinstance(value, (set, frozenset)):
            coercions.append(f"{type(value).__name__} rendered as repr()")
        return rendered

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, default=_default)
        handle.write("\n")
    os.replace(tmp, path)
    return coercions


def build_record(row: Dict,
                 patient_data: Dict,
                 final_state: Dict,
                 result: Dict,
                 selection_entry: Dict,
                 elapsed_s: float,
                 stamp_problems: List[str]) -> Dict:
    """Assemble one patient's persisted record. Raises nothing it can record.

    Everything in here happens after the billed call. Each renderer is wrapped
    individually so that one failure costs its own field and not the run.
    """
    problems = list(stamp_problems)

    summary_text = None
    summary_error = None
    try:
        summary_text = _create_patient_summary(patient_data)
    except Exception as exc:                         # noqa: BLE001 -- recorded
        summary_error = f"{type(exc).__name__}: {exc}"
        problems.append(f"patient summary: {summary_error}")

    contexts, context_problems = build_contexts(
        final_state.get("filtered_trials") or [])
    problems.extend(context_problems)

    verdicts = collect_verdicts(result)
    cost = price_run(result)

    # Everything the result carries except the three verdict lists and the
    # rendered prompt. This is what makes the degradation, refusal and error
    # fields present by construction: they are whatever the terminal node wrote,
    # not a list maintained here.
    residual = {k: v for k, v in result.items() if k not in RESULT_OMITTED_KEYS}

    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "patient_id": result.get("patient_id") or row.get("patient_id"),
        "run": {
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "bundle": row["bundle"],
            "selection_reason": selection_entry["reason"],
            "selection_note": selection_entry["note"],
            "terminal_node": result.get("terminal_node"),
            "matching_model": result.get("matching_model"),
            "matching_model_configured": MATCHING_MODEL,
            "qdrant_collection": result.get("qdrant_collection"),
            "collection_alias": COLLECTION_NAME,
            "patient_data_hash": result.get("patient_data_hash"),
            "llm_classifier_prompt_version": result.get("llm_classifier_prompt_version"),
            "llm_classifier_prompt_sha256": result.get("llm_classifier_prompt_sha256"),
            "llm_classifier_prompt_chars": len(result.get("llm_classifier_prompt") or ""),
            "llm_classifier_input_tokens": result.get("llm_classifier_input_tokens"),
            "llm_classifier_output_tokens": result.get("llm_classifier_output_tokens"),
            "llm_classifier_reasoning_tokens": result.get("llm_classifier_reasoning_tokens"),
            "llm_classifier_calls": result.get("llm_classifier_calls"),
            "llm_classifier_retries": result.get("llm_classifier_retries"),
            "llm_classifier_truncation_splits": result.get("llm_classifier_truncation_splits"),
            # WHETHER THE MODEL REFUSED, AND HOW LONG THE REFUSAL WAS -- NEVER
            # THE REFUSAL TEXT.
            #
            # Read off the STATE rather than the result, because neither
            # _pipeline_provenance() nor node_error_handler() copies it: a
            # refused run otherwise reaches a consumer as a generic `error`
            # string with nothing saying the model declined, and a rater asked
            # to judge criterion decisions that do not exist needs to know
            # which of the two happened.
            #
            # THE TEXT IS DELIBERATELY NOT PERSISTED, and that is the
            # pipeline's own decision rather than this module's caution.
            # oncotriage/agent/evaluation.py argues it at the refusal return:
            # "The refusal TEXT is not a field -- it is model prose about this
            # patient and the structured record is durable -- so only its
            # length travels." A JSON file on disk is more durable than a log
            # line, not less, so the same rule binds harder here. The first
            # version of this field carried the truncated prose and was wrong.
            "llm_classifier_refused": final_state.get("llm_classifier_refusal") is not None,
            "llm_classifier_refusal_chars": (
                len(final_state["llm_classifier_refusal"])
                if final_state.get("llm_classifier_refusal") is not None else None),
            "data_snapshot_date": DATA_SNAPSHOT_DATE,
            "age_reference_date": result.get("age_reference_date"),
            "duration_s": round(elapsed_s, 3),
            "cost": cost,
            "problems": problems,
        },
        "patient_summary": {"text": summary_text, "error": summary_error},
        "contexts": contexts,
        "verdicts": verdicts,
        "criterion_decision_count": count_criterion_decisions(verdicts),
        "result": residual,
        "result_omitted_keys": dict(RESULT_OMITTED_KEYS),
    }


def run_one_patient(selection_entry: Dict, graph: object) -> Tuple[Dict, Dict]:
    """One end-to-end run. Returns ``(record, outcome)``; ``record`` is None only
    if nothing was produced at all.

    THE INVOCATION IS THE ONE ``oncotriage/fixtures/capture.py`` USES, for the
    reason that file gives: ``build_initial_state`` rather than a local copy of
    that dict, because a key seeded in one and not the other is a run starting
    from different ground; ``correlation_scope`` because this is one patient's
    run and a log line carrying the "-" sentinel cannot be read back against the
    record it produced; and the two stamps
    ``match_patient_to_trials`` applies, repeated here rather than calling it
    because the record needs ``state["filtered_trials"]``, which that function
    does not return.

    ``graph.invoke`` WRITES TO NO DATABASE, and the accurate form of that claim
    is narrower than the obvious one. ``log_inference`` IS reachable in this
    process -- ``oncotriage.fixtures.capture``, imported above for
    ``scan_cohort``, imports ``oncotriage.storage.database_logger`` -- so
    "nothing imports it" would be false. What is true is that no code path from
    here calls it: the writers are ``oncotriage/api/server.py`` and
    ``oncotriage/batch/runner.py``, and neither is on this path. The
    demonstration is behavioural rather than structural, because a structural
    one would have to be re-argued every time an import moves: a run is driven
    with ``sqlite3.connect`` patched to raise, the record comes out whole, and
    the trap is then fired to show it was armed.
    """
    row = selection_entry["row"]
    started = time.time()

    try:
        patient_data = parse_fhir_bundle(row["path"])
    except Exception as exc:                         # noqa: BLE001 -- recorded
        return None, {"status": STATUS_FAILED, "at": "parse",
                      "error": f"{type(exc).__name__}: {exc}"}

    try:
        initial_state = build_initial_state(patient_data)
        with correlation_scope():
            final_state = graph.invoke(initial_state)
        result = final_state["result"]
    except Exception as exc:                         # noqa: BLE001 -- recorded
        # An exception that escapes the graph rather than landing in
        # node_error_handler: the Stage 2 readiness gate, a transport failure
        # outside a channel's own handler, a LangGraph recursion limit. There is
        # no result to persist, so the failure is the record.
        return None, {"status": STATUS_FAILED, "at": "invoke",
                      "error": f"{type(exc).__name__}: {exc}"}

    # --- the two stamps, each survivable -----------------------------------
    # Both run after the money is spent. resolve_qdrant_collection() makes a
    # network call and compute_patient_hash() walks the patient dict; neither is
    # allowed to cost the run.
    stamp_problems = []
    try:
        result["qdrant_collection"] = resolve_qdrant_collection()
    except Exception as exc:                         # noqa: BLE001 -- recorded
        result["qdrant_collection"] = None
        stamp_problems.append(f"qdrant_collection stamp: {type(exc).__name__}: {exc}")
    try:
        result["patient_data_hash"] = compute_patient_hash(patient_data)
    except Exception as exc:                         # noqa: BLE001 -- recorded
        result["patient_data_hash"] = None
        stamp_problems.append(f"patient_data_hash stamp: {type(exc).__name__}: {exc}")

    elapsed = time.time() - started
    record = build_record(row, patient_data, final_state, result,
                          selection_entry, elapsed, stamp_problems)

    terminal = result.get("terminal_node")
    if result.get("error"):
        status = STATUS_PIPELINE_ERROR
    elif not record["verdicts"]:
        # node_no_candidates, or a finalize with an empty evaluation set. Valid,
        # and there is nothing for either downstream harness to rate.
        status = STATUS_NOTHING_TO_EVALUATE
    else:
        status = STATUS_OK

    return record, {"status": status, "terminal_node": terminal,
                    "error": result.get("error") or None}


#------------------------------------------------------------------------------


# ===========================================================================
# THE MANIFEST
# ===========================================================================

def read_manifest(output_dir: str) -> Dict:
    """The manifest already in this directory, or None. RAISES on a corrupt one.

    Raising is the point. ``--only`` re-runs a subset into an existing
    directory, and the manifest is the record of what the first, paid, run did.
    Overwriting it because it did not parse would delete that record to make
    room for a partial one.
    """
    path = os.path.join(output_dir, MANIFEST_FILENAME)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_manifest(output_dir: str, manifest: Dict) -> List[str]:
    for entry in (manifest.get("runs") or {}).values():
        assert entry.get("status") in RUN_STATUSES, \
            f"unknown run status {entry.get('status')!r}"
    manifest["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    return write_json(os.path.join(output_dir, MANIFEST_FILENAME), manifest)


def summarise(manifest: Dict) -> Dict:
    """Totals over whatever the manifest currently holds.

    Recomputed from ``runs`` on every write rather than accumulated, so a
    ``--only`` re-run that replaces one patient's entry cannot leave a total
    describing the entry it replaced.
    """
    runs = manifest.get("runs") or {}
    by_status = {}
    by_terminal = {}
    cost = 0.0
    cost_complete = True
    contexts = 0
    decisions = 0
    verdicts = 0

    for entry in runs.values():
        by_status[entry["status"]] = by_status.get(entry["status"], 0) + 1
        terminal = entry.get("terminal_node") or "none"
        by_terminal[terminal] = by_terminal.get(terminal, 0) + 1
        contexts += entry.get("contexts") or 0
        decisions += entry.get("criterion_decisions") or 0
        verdicts += entry.get("verdicts") or 0
        run_cost = (entry.get("cost") or {}).get("cost_usd")
        if run_cost is None:
            cost_complete = False
        else:
            cost += run_cost
        if not (entry.get("cost") or {}).get("cost_complete", True):
            cost_complete = False

    return {
        "patients": len(runs),
        "by_status": dict(sorted(by_status.items())),
        "by_terminal_node": dict(sorted(by_terminal.items())),
        "verdicted_trials": verdicts,
        "retrieval_contexts": contexts,
        "criterion_decisions": decisions,
        "cost_usd": round(cost, 6),
        # False means the number above is a FLOOR: an unpriced model, a run with
        # Stage 5 retries whose earlier attempts are not in the counters, or a
        # run that produced no priceable record at all.
        "cost_complete": cost_complete,
    }


#------------------------------------------------------------------------------


# ===========================================================================
# THE FREE POST-CHECK
# ===========================================================================

def post_check(output_dir: str) -> Dict:
    """Load every record written here, validate it, and total it. Free.

    It re-derives the criterion count from the persisted arrays instead of
    trusting the number in the file, because a stored count that agrees with
    itself is not evidence -- the check exists to catch a writer that persisted
    a count and then a different set of verdicts.
    """
    findings = []
    manifest_name = MANIFEST_FILENAME

    files = sorted(
        f for f in os.listdir(output_dir)
        if f.endswith(".json") and f != manifest_name
    )
    if not files:
        findings.append("no patient record was written")

    totals = {"records": 0, "verdicts": 0, "contexts": 0,
              "criterion_decisions": 0, "contexts_with_text": 0,
              "summaries_present": 0}

    for name in files:
        full = os.path.join(output_dir, name)
        try:
            with open(full, "r", encoding="utf-8") as handle:
                record = json.load(handle)
        except Exception as exc:                     # noqa: BLE001 -- recorded
            findings.append(f"{name}: unreadable ({type(exc).__name__}: {exc})")
            continue

        missing = [k for k in REQUIRED_RECORD_KEYS if k not in record]
        if missing:
            findings.append(f"{name}: missing key(s) {missing}")
            continue
        if record["schema_version"] != RECORD_SCHEMA_VERSION:
            findings.append(
                f"{name}: schema_version {record['schema_version']} is not "
                f"{RECORD_SCHEMA_VERSION}")
            continue

        missing_run = [k for k in REQUIRED_RUN_KEYS if k not in (record["run"] or {})]
        if missing_run:
            findings.append(f"{name}: run block missing {missing_run}")

        recomputed = count_criterion_decisions(record["verdicts"])
        if recomputed != record["criterion_decision_count"]:
            findings.append(
                f"{name}: criterion_decision_count says "
                f"{record['criterion_decision_count']}, the persisted arrays "
                f"hold {recomputed}")

        if record["patient_summary"].get("text"):
            totals["summaries_present"] += 1
        else:
            findings.append(f"{name}: no patient summary text was rendered")

        for context in record["contexts"]:
            if context.get("trial_text"):
                totals["contexts_with_text"] += 1
            else:
                findings.append(
                    f"{name}: context {context.get('rank')} "
                    f"({context.get('nct_id')}) has no trial text")

        totals["records"] += 1
        totals["verdicts"] += len(record["verdicts"])
        totals["contexts"] += len(record["contexts"])
        totals["criterion_decisions"] += recomputed
        # The record itself is deliberately NOT retained. This runs over
        # whatever a run produced, and at cohort scale that is a thousand
        # records of a hundred kilobytes each; a checker that has to hold the
        # thing it is checking cannot check a large one.

    return {"files": files, "totals": totals, "findings": findings}


def print_post_check(report: Dict) -> None:
    totals = report["totals"]
    console.out(f"\n{'-' * 78}\nPOST-CHECK (free; reads what was written)\n{'-' * 78}")
    console.out(f"  records loaded and validated : {totals['records']}")
    console.out(f"  patient summaries present    : {totals['summaries_present']}")
    console.out(f"  retrieval contexts           : {totals['contexts']} "
                f"({totals['contexts_with_text']} with text)")
    console.out(f"  verdicted trials             : {totals['verdicts']}")
    console.out(f"  criterion decisions          : {totals['criterion_decisions']}")
    if report["findings"]:
        console.out(f"\n  {len(report['findings'])} FINDING(S):")
        for finding in report["findings"]:
            console.out(f"    * {finding}")
    else:
        console.out("\n  No findings.")


#------------------------------------------------------------------------------


# ===========================================================================
# ENTRY POINT
# ===========================================================================

def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the pipeline over a stratified evaluation slice and "
                    "persist what the rating and Ragas harnesses consume. "
                    "COSTS MONEY: one live Stage 5 call per patient."
    )
    parser.add_argument("--scan-only", action="store_true",
                        help="Classify the cohort, print the selection, run "
                             "nothing. Free.")
    parser.add_argument("--select", type=int, default=DEFAULT_SELECTION_SIZE,
                        metavar="N",
                        help=f"How many patients to select "
                             f"(default {DEFAULT_SELECTION_SIZE}).")
    parser.add_argument("--only", nargs="*", default=None, metavar="PATIENT_ID",
                        help="Run only these patient ids out of the selection. "
                             "Pair it with --output-dir to update an existing run.")
    parser.add_argument("--output-dir", default=None,
                        help="Where to write. Default: a new timestamped "
                             "directory under the project's Testing tree.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)

    if args.select < 1:
        console.out("[FATAL] --select must be at least 1.")
        return EXIT_PRECONDITION

    started_utc = datetime.now(timezone.utc)
    stamp = started_utc.strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir or new_output_dir(stamp)

    console.out(f"\n{'=' * 78}")
    console.out(f"{Project_Name}: Evaluation Run "
                f"(record schema v{RECORD_SCHEMA_VERSION})")
    console.out(f"{'=' * 78}")
    console.out(f"  Output directory : {output_dir}")
    console.out(f"  Prompt version   : {PROMPT_VERSION}")
    console.out(f"  Model configured : {MATCHING_MODEL}")
    console.out(f"  Snapshot date    : {DATA_SNAPSHOT_DATE}  "
                f"(age reference {get_age_reference_date().isoformat()})")

    bundle_paths = sorted(glob.glob(paths.data_fhir_path + "*.json"))
    if not bundle_paths:
        console.out(f"[FATAL] No FHIR bundles found in {paths.data_fhir_path}")
        return EXIT_PRECONDITION
    console.out(f"  Cohort           : {len(bundle_paths)} bundles")

    rows = scan_cohort(bundle_paths)
    if not rows:
        console.out("[FATAL] No bundle parsed successfully.")
        return EXIT_PRECONDITION

    selection, deviations = select_patients(rows, args.select)
    table = selection_table(selection)
    print_selection(table, deviations)

    if not selection:
        console.out("\n[FATAL] Nothing was selected.")
        return EXIT_PRECONDITION

    # --- --only, resolved against the selection BEFORE anything is spent ----
    to_run = selection
    if args.only is not None:
        wanted = list(args.only)
        # `--only` WITH NO IDS IS A REFUSAL, NOT AN EMPTY RUN. argparse's
        # nargs="*" makes a bare `--only` an empty list, which selects nothing
        # and runs nothing.
        #
        # MEASURED RATHER THAN ARGUED, and the measurement moved the argument.
        # The first version of this comment said the unguarded path "exits 0",
        # the Files 18/19 defect. It does not: the post-check finds no record,
        # reports it, and main() returns EXIT_INCOMPLETE. What the unguarded
        # path actually does is create the output directory, probe the index,
        # write a manifest describing a run of nobody, append an invocation
        # naming nobody, and then fail with a generic "no patient record was
        # written" -- a diagnosis pointing at the output rather than at the
        # typo in the command. Refusing here costs nothing and names the cause.
        if not wanted:
            console.out("\n[FATAL] --only was given with no patient id. "
                        "Name at least one, or omit the flag to run the whole "
                        "selection. Nothing was run.")
            return EXIT_PRECONDITION
        by_id = {e["row"]["patient_id"]: e for e in selection}
        unknown = [w for w in wanted if w not in by_id]
        if unknown:
            console.out(f"\n[FATAL] --only names {len(unknown)} patient id(s) "
                        f"that are not in this selection: {unknown}")
            console.out("        Selected ids are:")
            for entry in selection:
                console.out(f"          {entry['row']['patient_id']}")
            return EXIT_PRECONDITION
        # Selection order, not argument order, so two invocations naming the
        # same ids run them in the same sequence.
        to_run = [e for e in selection if e["row"]["patient_id"] in set(wanted)]

    if args.scan_only:
        console.out(f"\n[Scan-only] {len(to_run)} patient(s) would run. "
                    f"Nothing was executed and nothing was written.")
        return EXIT_OK

    # --- refuse to spend against an index that cannot answer ----------------
    #
    # Every Qdrant call in Stage 2 SUCCEEDS against a collection with zero
    # points, so an unusable index produces ten well-formed no-candidates
    # records rather than an error -- at the cost of ten runs. The probe is two
    # cheap calls and it is the difference between a refusal and a wasted run.
    # `unverifiable` refuses too, which is the API-startup policy rather than
    # Stage 2's: a probe that could not run is not evidence the index is fine,
    # and this caller is about to spend money on the answer.
    probe = readiness.probe_index()
    console.out(f"\n[Index] {probe['collection']} @ {probe['endpoint']}: "
                f"{probe['state']}"
                + (f", {probe['points']} points" if probe["points"] is not None else "")
                + (f" ({probe['error']})" if probe["error"] else ""))
    if probe["state"] != readiness.INDEX_POPULATED:
        console.out(f"[FATAL] Refusing to spend against an index in state "
                    f"{probe['state']!r}. Nothing was run.")
        return EXIT_PRECONDITION

    # --- an existing manifest is read before it is replaced -----------------
    os.makedirs(output_dir, exist_ok=True)
    try:
        manifest = read_manifest(output_dir)
    except Exception as exc:                         # noqa: BLE001 -- fatal by design
        console.out(f"[FATAL] {os.path.join(output_dir, MANIFEST_FILENAME)} "
                    f"exists and could not be read ({type(exc).__name__}: {exc}). "
                    f"It is the record of a paid run; refusing to overwrite it.")
        return EXIT_PRECONDITION

    if manifest is None:
        manifest = {
            "schema_version": RECORD_SCHEMA_VERSION,
            "created_at_utc": started_utc.isoformat(),
            "runs": {},
            "invocations": [],
        }
    manifest["output_dir"] = os.path.abspath(output_dir)
    manifest["cohort"] = {
        "bundle_dir": paths.data_fhir_path,
        "bundles_found": len(bundle_paths),
        "rows_parsed": len(rows),
    }
    manifest["selection"] = {
        "requested": args.select,
        "selected": len(table),
        "deviations": deviations,
        "patients": table,
    }
    manifest["environment"] = {
        "qdrant_collection": probe["collection"],
        "collection_alias": COLLECTION_NAME,
        "collection_points": probe["points"],
        "matching_model_configured": MATCHING_MODEL,
        "llm_classifier_prompt_version": PROMPT_VERSION,
        "data_snapshot_date": DATA_SNAPSHOT_DATE,
        "age_reference_date": get_age_reference_date().isoformat(),
    }
    manifest.setdefault("runs", {})
    manifest.setdefault("invocations", [])

    # Names already issued, so a re-run keeps writing to the same file and a new
    # patient cannot collide with one an earlier invocation wrote.
    taken = {entry["file"] for entry in manifest["runs"].values()
             if entry.get("file")}

    console.out(f"\n{'=' * 78}")
    console.out(f"PAID RUN: {len(to_run)} patient(s), one live Stage 5 call each")
    console.out(f"{'=' * 78}")

    log.info("evaluation run started", event="evaluation_run_started",
             count=len(to_run), collection=probe["collection"])

    failures = []
    with CaffeinateSession("evaluation run"):
        for index, entry in enumerate(to_run, start=1):
            row = entry["row"]
            console.out(f"\n{'#' * 78}")
            console.out(f"# [{index}/{len(to_run)}] {row['patient_id']}  "
                        f"{row['primary_diagnosis'][:44]}")
            console.out(f"{'#' * 78}")

            # Read BEFORE the run, because both branches below need it: a
            # re-run writes back to the file the first attempt used, and a
            # re-run that FAILS must not erase the manifest's pointer to the
            # record the first attempt left on disk.
            existing = manifest["runs"].get(row["patient_id"]) or {}

            record, outcome = run_one_patient(entry, graph=compiled_graph())

            if record is None:
                failures.append((row["patient_id"], outcome["error"]))
                manifest["runs"][row["patient_id"]] = {
                    "status": STATUS_FAILED,
                    "bundle": row["bundle"],
                    # Carried forward, not blanked. The file is still there and
                    # is still a real paid run; what is now true of it is that
                    # a later attempt failed, which `stale_record` says.
                    "file": existing.get("file"),
                    "stale_record": bool(existing.get("file")),
                    "terminal_node": None,
                    "error": outcome["error"],
                    "failed_at": outcome["at"],
                }
                console.out(f"  FAILED at {outcome['at']}: {outcome['error']}")
                if existing.get("file"):
                    console.out(f"    (an earlier attempt's record, "
                                f"{existing['file']}, is left in place and is "
                                f"now marked stale)")
                log.error("patient run failed", event="evaluation_patient_failed",
                          patient_id=row["patient_id"], status="error",
                          error_message=outcome["error"])
                manifest["totals"] = summarise(manifest)
                write_manifest(output_dir, manifest)
                continue

            name = existing.get("file") or record_filename(row["patient_id"], taken)
            coercions = write_json(os.path.join(output_dir, name), record)

            run_entry = {
                "status": outcome["status"],
                "bundle": row["bundle"],
                "file": name,
                "terminal_node": outcome["terminal_node"],
                "error": outcome["error"],
                "verdicts": len(record["verdicts"]),
                "contexts": len(record["contexts"]),
                "criterion_decisions": record["criterion_decision_count"],
                "cost": record["run"]["cost"],
                "duration_s": record["run"]["duration_s"],
                "problems": record["run"]["problems"] + coercions,
            }
            manifest["runs"][row["patient_id"]] = run_entry
            manifest["totals"] = summarise(manifest)
            write_manifest(output_dir, manifest)

            cost_usd = run_entry["cost"]["cost_usd"]
            console.out(
                f"  {outcome['status']:<20} {outcome['terminal_node']}  "
                f"trials={run_entry['contexts']}  "
                f"verdicts={run_entry['verdicts']}  "
                f"criteria={run_entry['criterion_decisions']}  "
                f"{run_entry['duration_s']}s  "
                + (f"${cost_usd:.5f}" if cost_usd is not None else "cost UNKNOWN"))
            for note in run_entry["problems"]:
                console.out(f"    ! {note}")
            log.info("patient run finished", event="evaluation_patient_finished",
                     patient_id=row["patient_id"], status=outcome["status"],
                     node=outcome["terminal_node"],
                     evaluated=run_entry["verdicts"],
                     criteria_count=run_entry["criterion_decisions"],
                     cost_usd=cost_usd)

    manifest["totals"] = summarise(manifest)
    manifest["invocations"].append({
        "started_at_utc": started_utc.isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "select": args.select,
        "only": list(args.only) if args.only is not None else None,
        "patients_run": [e["row"]["patient_id"] for e in to_run],
    })
    write_manifest(output_dir, manifest)

    # --- report -------------------------------------------------------------
    totals = manifest["totals"]
    console.out(f"\n{'=' * 78}")
    console.out("RUN SUMMARY")
    console.out(f"{'=' * 78}")
    reached = sum(1 for e in to_run
                  if (manifest["runs"].get(e["row"]["patient_id"]) or {})
                  .get("terminal_node"))
    console.out(f"  terminal node reached : {reached}/{len(to_run)}")
    for status, count in totals["by_status"].items():
        console.out(f"    {status:<22} {count}")
    for node, count in totals["by_terminal_node"].items():
        console.out(f"    {node:<22} {count}")
    if failures:
        console.out(f"\n  {len(failures)} PATIENT(S) PRODUCED NO RECORD:")
        for patient_id, error in failures:
            console.out(f"    {patient_id}: {error}")
    console.out(f"\n  total cost            : ${totals['cost_usd']:.5f}"
                + ("" if totals["cost_complete"] else "   <- A FLOOR, NOT A TOTAL"))
    console.out(f"  criterion decisions   : {totals['criterion_decisions']}")
    console.out(f"  retrieval contexts    : {totals['retrieval_contexts']}")
    console.out(f"  output directory      : {os.path.abspath(output_dir)}")

    report = post_check(output_dir)
    print_post_check(report)

    log.info("evaluation run finished", event="evaluation_run_finished",
             count=len(to_run), total=totals["criterion_decisions"],
             cost_usd=totals["cost_usd"],
             status="ok" if not (failures or report["findings"]) else "degraded")

    if failures or report["findings"]:
        return EXIT_INCOMPLETE
    return EXIT_OK


def compiled_graph() -> object:
    """The compiled graph, built once per process and cached on this module.

    Compiled lazily rather than at the top of main() so ``--scan-only`` and
    every refusal above it stay free of it, and once rather than per patient
    because compiling is pure overhead repeated.
    """
    with _RESOLVE_LOCK:
        if "graph" not in _RESOLVED:
            _RESOLVED["graph"] = build_matching_graph()
        return _RESOLVED["graph"]


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug 11 09:30:00 2026

@author: ramyalsaffar
"""
