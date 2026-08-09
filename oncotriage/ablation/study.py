"""The ablation study: one stage disabled at a time, over one patient sample.

Moved out of ``26- Ablation Study.py`` by item 20c, pass 3d.
``26- Ablation Study.py`` survives as a THIN ENTRY POINT. It keeps no re-export
shim: all 28 of its top-level names were grepped against every ``.py``, ``.md``,
``.toml`` and ``.yml`` in the tree, and the only hits outside the file are
``ABLATION_DB`` (File 27's own definition of the same name, over the same
directory), a prose reference to ``ABLATION_CONFIGS`` in File 27's comment, two
prose mentions of ``log_ablation_result`` in CLAUDE.md and the exception audit,
and the exec-bootstrap locals every numbered file shares.

WHAT CHANGED, and why each one had to
-------------------------------------
1. ``ABLATION_DB`` / ``ABLATION_SUMMARY_JSON`` were module-level
   ``Path(result_ablation_path)`` expressions and are now ``ablation_db()`` /
   ``ablation_summary_json()``. ``result_ablation_path`` is lazy, so reading it
   at module scope globbed the sibling data tree at IMPORT and raised on any
   machine without one.

2. ``_ablation_checkpoint_path()`` read a bare ``checkpoint_path``; it reads
   ``paths.checkpoint_path`` inside the function body now. Same value, resolved
   on the call rather than out of a namespace this module does not have. (Pass
   20f-3 gave it a ``db_path`` argument as well -- with ``None`` it is still
   that same value.)

3. ``_CANCER_REGISTRY`` was read out of the shared exec namespace, where File
   13's shim binds it as ``deps.get_cancer_registry()``. Both sites call that
   accessor directly, which is the SAME OBJECT -- see the shim's line 232 --
   so a study run classifies patients with the registry the pipeline it is
   measuring used. Deliberately NOT ``load_registry()``: this is a measurement
   of the agent, and it must see whatever the agent sees.

4. Every free name that used to arrive from ``01- Imports.py`` or the File 03
   re-export is an explicit import: ``MAX_WORKERS``, ``MATCHING_MODEL`` and
   ``Project_Name`` from ``oncotriage.config``; ``CaffeinateSession``,
   ``get_model_cost`` and ``resolve_qdrant_collection`` from ``oncotriage.utils``;
   the pipeline entry points from ``oncotriage.agent.*``; ``load_all_patients``
   from ``oncotriage.fhir.parser``.

Nothing else moved. Every config dict, every SQL statement, the migration and
its three backfills, the two locks, the checkpoint, the thread pool and the
summary are the line slice of File 26 between its bootstrap and its ``__main__``
guard, unmodified.

TWO THINGS PASS 20c-3d DELIBERATELY DID NOT FIX, AND PASS 20f-1 FIXED BOTH
--------------------------------------------------------------------------
Both were recorded here rather than silently carried, and both are behaviour
changes, which is why a conversion pass whose acceptance criterion was that
nothing changed was the wrong place for either.

  * ``ablation_db()`` WAS A DEFAULT WITH NO OVERRIDE -- the last implicit-path
    database writer in the repository. Every other writer takes its path as an
    argument (``log_inference(db_path=)``, ``log_drift_metrics(db_path=)``,
    ``empty_database(db_path, flag)``, ``select_samples(source_db, output_db)``)
    and this one did not, so a study run could not be pointed at a scratch file
    and no isolation test could be written for it.

    It takes ``db_path`` now, threaded through every function that opens the
    database -- ``init_ablation_db``, ``_create_run``, ``_finalize_run``,
    ``log_ablation_result``, ``generate_summary`` -- and ``main()`` gets it from
    a new ``--db`` flag. ``None`` still means the production
    ``ablation_results.db``, exactly as before, so every existing command is
    unchanged.

    AN EXPLICIT ARGUMENT IS NOT CACHED, and that is the same rule
    ``resolve_inference_db_path`` follows: the cache below answers "where does
    this machine keep the study database", which is a fact about the machine,
    while an argument is a fact about one call. Caching the argument would make
    the first call in a process decide the answer for every later one.

    ``ablation_summary_json(db_path)`` FOLLOWS THE DATABASE, landing beside it
    rather than in the production results directory. A run told to write
    somewhere else must not leave a production artifact behind describing a
    scratch database; with ``None`` it resolves exactly where it always did.

    ``tests/test_ablation_db_isolation.py`` is the demonstration.

    TWO THINGS PASS 20f-1 LEFT AND PASS 20f-3 CLOSED, and both were named there:

      * THE CHECKPOINT DID NOT FOLLOW ``--db``. An isolated run read the
        PRODUCTION resume file, skipped every pair a production run had already
        done, wrote nothing for them into the scratch database, and reported
        COMPLETE. ``_ablation_checkpoint_path(db_path)`` puts the checkpoint
        beside the database now; the decision it needed -- what "resume" means
        across two databases -- is argued at that function.
      * AN ABSENT PARENT DIRECTORY gave a bare
        ``sqlite3.OperationalError: unable to open database file``, naming
        neither the path nor the flag. ``_require_writable_parent()`` refuses it
        by name, which is what ``settings.resolve_inferences_db()`` already did
        for the other redirectable database.

    ``oncotriage/ablation/analysis.py`` reads the production database through
    its own accessor and is a READER, so it is outside this item.

  * ``save_ablation_checkpoint()``'s inner ``except OSError: pass`` (the unlink
    of the temp file after a failed write) RECORDED NOTHING. It was the one
    exception in this file caught without a counter or a message; the exception
    audit lists it as SILENT and item 11a's sweep missed it because the audit's
    line number was eleven lines off.

    Both handlers now count into ``CHECKPOINT_WRITE_FAILURES`` below, on item
    11a's shape -- a module-level ``Counter``, keyed by what failed and by the
    exception type. THE RECOVERY IS UNCHANGED: the write failure still prints
    and continues, the unlink failure is still swallowed, and no exception
    reaches a caller that did not get one before. The outer handler is counted
    too, and not only the silent one, because a ``tmp_unlink`` failure can only
    happen after a ``write`` failure -- a count of the second with no count of
    the first is a number nobody can interpret.

WHAT IMPORTING THIS MODULE DOES
-------------------------------
Nothing. No path is resolved, no database is opened, no client is built, no
model is loaded, no graph is compiled. ``tqdm`` and ``pandas`` come in at import
because the module body needs neither -- they are used inside functions -- but
they are cheap and were already in ``01- Imports.py``; the expensive things
(OpenAI, Qdrant, MedCPT, FastEmbed) all sit behind ``oncotriage.agent.deps``
accessors that build on first call.
"""

import argparse
import json
import os
import random
import sqlite3
import sys
import threading
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from oncotriage import paths
from oncotriage.ablation.common import (
    ABLATION_DB_FILENAME,
    ABLATION_SUMMARY_FILENAME,
    _require_writable_parent,
)
from oncotriage.agent import deps
from oncotriage.agent.evaluation import MatchingModelMismatchError
from oncotriage.agent.graph import build_matching_graph
from oncotriage.agent.patient import compute_patient_hash
from oncotriage.agent.retrieval import build_bm25_index_from_qdrant
from oncotriage.agent.state import CHANNEL_ABLATED, CHANNEL_OK
from oncotriage.config import MATCHING_MODEL, MAX_WORKERS, Project_Name
from oncotriage.fhir.parser import load_all_patients
from oncotriage.utils import (
    CaffeinateSession,
    get_model_cost,
    resolve_qdrant_collection,
)
from oncotriage.observability import console, correlation_scope, get_logger


log = get_logger(__name__)


#------------------------------------------------------------------------------


# ===========================================================================
# LAZY PATHS
# ===========================================================================
#
# File 26 built both of these at module level over result_ablation_path, which
# oncotriage/paths.py resolves by glob on first READ. Importing this module
# would therefore have globbed the sibling data tree -- the one thing no module
# in this package may do at import.
#
# Locked, matching oncotriage/fhir/clean.py and oncotriage/agent/deps.py. This
# module genuinely IS multi-threaded (MAX_WORKERS workers per config), and while
# nothing in a worker calls these two today, `if k not in d: d[k] = build()` is
# a non-atomic sequence and this is not the file to leave that pattern in.

_RESOLVED = {}
_RESOLVE_LOCK = threading.RLock()


# BOTH FILENAMES AND THE PARENT-DIRECTORY GUARD MOVED TO
# oncotriage/ablation/common.py (pass 20f-4), because the ANALYSIS side needs
# them: `analysis.ablation_db()` took no argument and hardcoded the string
# "ablation_results.db", so a study written with --db could not be analysed and
# the filename existed as a constant here AND as a literal there. One
# definition, two consumers. They are imported by NAME rather than through the
# module so that `study.ABLATION_DB_FILENAME` still resolves -- it is read by
# tests/test_ablation_db_isolation.py.
#
# `_require_writable_parent`'s message is byte-identical to the one pass 20f-3
# shipped: the example command it names is that function's default argument.




def ablation_db(db_path=None) -> Path:
    """The study's SQLite database.

    Separate from the production inferences.db on purpose: an ablation run is
    not a production inference and must not reach drift detection or the
    Reproducibility dashboard.

    Args:
        db_path: Where to write. ``None`` -- the default and what every
            documented command produces -- means the production
            ``ablation_results.db`` under ``result_ablation_path``, resolved on
            first call and cached.

            AN EXPLICIT ARGUMENT IS RETURNED AS GIVEN AND IS NEVER CACHED.
            The cache answers "where does this machine keep the study
            database", which is a fact about the machine; an argument is a fact
            about one call, and caching it would let the first caller in a
            process decide for every later one. Same rule as
            ``resolve_inference_db_path`` in oncotriage/storage/database_logger.py,
            where the explicit argument also outranks everything and is not
            remembered.

    Returns:
        pathlib.Path. Added by pass 20f-1; see the module docstring for why
        this was the last database writer in the project with no override.

    Raises:
        RuntimeError: an explicit ``db_path`` whose PARENT DIRECTORY does not
            exist (pass 20f-3). See ``_require_writable_parent`` below.
    """
    if db_path is not None:
        return _require_writable_parent(Path(db_path))

    with _RESOLVE_LOCK:
        if "ablation_db" not in _RESOLVED:
            _RESOLVED["ablation_db"] = Path(paths.result_ablation_path) / ABLATION_DB_FILENAME
        return _RESOLVED["ablation_db"]


def ablation_summary_json(db_path=None) -> Path:
    """Where generate_summary() exports the machine-readable table.

    THE SUMMARY FOLLOWS THE DATABASE. With an explicit ``db_path`` it lands in
    that database's directory, because a run told to write somewhere else must
    not leave a production artifact behind that describes a scratch database.
    With ``None`` it resolves exactly where it always did.
    """
    if db_path is not None:
        return _require_writable_parent(Path(db_path)).parent / ABLATION_SUMMARY_FILENAME

    with _RESOLVE_LOCK:
        if "ablation_summary_json" not in _RESOLVED:
            _RESOLVED["ablation_summary_json"] = (
                Path(paths.result_ablation_path) / ABLATION_SUMMARY_FILENAME)
        return _RESOLVED["ablation_summary_json"]


# ===========================================================================
# THREAD SAFETY
# ===========================================================================

_ablation_db_lock = threading.Lock()
_ablation_checkpoint_lock = threading.Lock()


# ===========================================================================
# CONSTANTS
# ===========================================================================

SAMPLE_SIZE_DEFAULT = 75
ABLATION_SEED = 42
# File 26 built its two output Paths here, over the lazy
# result_ablation_path. They are the two accessors above.
ABLATION_CHECKPOINT_FILENAME = "ablation_checkpoint.json"


# ===========================================================================
# CHECKPOINT HELPERS (crash-safe resume)
# ===========================================================================
# Tracks completed (config_name, patient_id) pairs. If the run crashes at
# config 5 of 7, resume skips configs 1-4 entirely and picks up mid-config-5.
# Uses atomic temp+replace writes (same pattern as File 25 Batch Runner).

# Checkpoint write degradations, keyed by WHICH step failed and by the
# exception type (pass 20f-1, item 11a's shape).
#
# Module-level, following PARTIAL_DATE_DEGRADATIONS in oncotriage/utils.py,
# AGE_PARSE_FAILURES in oncotriage/agent/filtering.py and
# INDEX_AGE_PARSE_FAILURES in oncotriage/retrieval/indexer.py -- the same shape
# item 11a established rather than a new one, and deliberately NOT a new key in
# any result dict.
#
# TWO KEY PREFIXES, and both are needed:
#
#   write:{ExceptionType}       the atomic write or the os.replace failed. This
#                               one already printed; the counter makes it
#                               countable at the end of a run rather than
#                               something to find by reading scrollback.
#   tmp_unlink:{ExceptionType}  the cleanup of the temp file after that failure
#                               ALSO failed. THIS IS THE ONE THE EXCEPTION
#                               AUDIT LISTS AS SILENT: it was `except OSError:
#                               pass`, with no counter and no message, so a
#                               .tmp file left behind in the checkpoint
#                               directory was the only trace it had happened.
#
# The type is in the key because the fixes differ: ENOSPC is a full disk,
# EACCES is a permissions problem on the checkpoint directory, and
# IsADirectoryError means something has taken the temp file's name.
#
# THE RECOVERY IS UNCHANGED. Both handlers still return normally, the run still
# continues, and a checkpoint that could not be written still costs only the
# resume, exactly as before. Recording is all that was added.
CHECKPOINT_WRITE_FAILURES = Counter()


def _ablation_checkpoint_path(db_path=None) -> Path:
    """Where the resume state for `db_path` lives.

    THE CHECKPOINT FOLLOWS THE DATABASE (pass 20f-3), on exactly the argument
    ``ablation_summary_json()`` already made: an artifact that describes one
    database must not be shared with another.

    WHAT WENT WRONG WITHOUT IT. The checkpoint is a set of
    ``(config_name, patient_id)`` pairs, and the ONLY thing it can mean is
    "already written". Pass 20f-1 gave the database a ``--db`` override and left
    this function taking no argument, so an isolated run read the PRODUCTION
    resume file: every pair a production run had completed was skipped, nothing
    was written for it into the scratch database, and the run printed
    ``Status: COMPLETE``. A scratch database silently missing up to 525 rows,
    reported as a finished study -- and the ablation ANALYSIS averages per
    config, so a config that lost rows comes back with a plausible number
    computed over a different sample than its neighbours.

    Pass 20f-1 recorded this as a follow-up and named the decision it needed:
    "the checkpoint is keyed by (config, patient) and carries no path, so
    redirecting it is a separate decision about what RESUME means". THE ANSWER
    IS THAT RESUME IS PER DATABASE, because "already written" is a statement
    about a database and about nothing else. Two consequences, both intended:

      * ``--db scratch.db`` twice in a row resumes the second run from the
        first -- the property that makes an interrupted study cheap, now
        available to an isolated one;
      * a production run and a scratch run do not see each other at all, in
        either direction. The production checkpoint is not read by a ``--db``
        run and is not written by one, so an isolated run can no longer
        CLEAR it either (``clear_ablation_checkpoint()`` runs on a clean
        finish, and before this pass an isolated run's finish deleted the
        production resume state).

    Args:
        db_path: ``None`` -- the default and what every documented command
            produces -- means ``{checkpoint_path}/ablation_checkpoint.json``,
            exactly where it has always been. An explicit path puts the
            checkpoint BESIDE that database, named after it, so two scratch
            databases in one directory do not share resume state either.
    """
    if db_path is not None:
        db = _require_writable_parent(Path(db_path))
        return db.parent / (db.stem + "_checkpoint.json")
    return Path(paths.checkpoint_path) / ABLATION_CHECKPOINT_FILENAME


def load_ablation_checkpoint(db_path=None) -> set:
    """Load set of completed (config_name, patient_id) tuples for `db_path`."""
    cp = _ablation_checkpoint_path(db_path)
    if not cp.exists():
        return set()
    try:
        with open(cp, "r") as f:
            data = json.load(f)
        completed = set(tuple(pair) for pair in data.get("completed", []))
        console.out(f"[Checkpoint] Resuming: {len(completed)} patient-config pairs already completed.")
        return completed
    except (json.JSONDecodeError, KeyError) as e:
        console.out(f"[Checkpoint] WARNING: Could not read checkpoint ({e}). Starting fresh.")
        return set()


def save_ablation_checkpoint(completed: set, db_path=None) -> None:
    """Atomically persist completed set to `db_path`'s checkpoint file."""
    with _ablation_checkpoint_lock:
        cp = _ablation_checkpoint_path(db_path)
        tmp_path = cp.with_suffix(".tmp")
        try:
            with open(tmp_path, "w") as f:
                json.dump(
                    {
                        "completed": list(completed),
                        "last_updated": datetime.now().isoformat(),
                        "count": len(completed),
                    },
                    f,
                    indent=2,
                )
            os.replace(tmp_path, cp)
        except OSError as e:
            CHECKPOINT_WRITE_FAILURES[f"write:{type(e).__name__}"] += 1
            console.out(f"[Checkpoint] WARNING: Could not write checkpoint ({e}). Continuing.")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError as unlink_error:
                    # Was `pass`, with nothing recorded at all -- the one
                    # exception in this file caught without a counter or a
                    # message (pass 20f-1, item 11a). CONTINUING IS STILL
                    # RIGHT: this is the cleanup of a temp file whose own write
                    # already failed and already printed, so raising here would
                    # replace a reported degradation with an unreported crash.
                    # What was wrong is that a leftover .tmp file in the
                    # checkpoint directory was the only evidence it happened.
                    CHECKPOINT_WRITE_FAILURES[
                        f"tmp_unlink:{type(unlink_error).__name__}"] += 1
                    console.out(f"[Checkpoint] WARNING: could not remove the "
                          f"temporary checkpoint file {tmp_path} "
                          f"({unlink_error}). Continuing; it will be "
                          f"overwritten by the next successful write.")
                

def clear_ablation_checkpoint(db_path=None) -> None:
    """Delete `db_path`'s checkpoint file to start a fresh run."""
    cp = _ablation_checkpoint_path(db_path)
    if cp.exists():
        cp.unlink()
        console.out("[Checkpoint] Cleared.")


# ===========================================================================
# ABLATION CONFIGURATIONS
# ===========================================================================
# Each dict is passed into the LangGraph initial state as 'ablation_flags'.
# File 13 nodes read flags via state.get("ablation_flags") or {}.
# Default {} = all stages active (production behavior).

ABLATION_CONFIGS = [
    {
        "name": "full_pipeline",
        "description": "All stages active (baseline)",
        "flags": {
            "skip_mesh_filter": False,
            "skip_stage_filter": False,
            "skip_histology_filter": False,
            "skip_cross_encoder": False,
            "retrieval_mode": "hybrid",
        },
    },
    {
        "name": "no_mesh_filter",
        # skip_mesh_filter removes BOTH MeSH uses: the Stage 3 relevance boost
        # and the Stage 4 hard drop. Disabling only the drop left this row
        # confounded, because the boost still reordered the pool.
        "description": "MeSH cancer site filter disabled (Stage 3 boost + Stage 4 drop)",
        "flags": {
            "skip_mesh_filter": True,
            "skip_stage_filter": False,
            "skip_histology_filter": False,
            "skip_cross_encoder": False,
            "retrieval_mode": "hybrid",
        },
    },
    {
        "name": "no_stage_filter",
        "description": "Cancer stage filter disabled",
        "flags": {
            "skip_mesh_filter": False,
            "skip_stage_filter": True,
            "skip_histology_filter": False,
            "skip_cross_encoder": False,
            "retrieval_mode": "hybrid",
        },
    },
    {
        "name": "no_histology_filter",
        "description": "Histology mismatch filter disabled",
        "flags": {
            "skip_mesh_filter": False,
            "skip_stage_filter": False,
            "skip_histology_filter": True,
            "skip_cross_encoder": False,
            "retrieval_mode": "hybrid",
        },
    },
    {
        "name": "no_cross_encoder",
        # Removes ONLY the reranking. Stage 3 still resolves the patient's
        # MeSH trees before this flag's early return, so Stage 4's cancer site
        # filter stays active — otherwise this row measured cross-encoder
        # removal and MeSH-filter removal together.
        "description": "Cross-encoder reranking disabled (fusion score passthrough)",
        "flags": {
            "skip_mesh_filter": False,
            "skip_stage_filter": False,
            "skip_histology_filter": False,
            "skip_cross_encoder": True,
            "retrieval_mode": "hybrid",
        },
    },
    {
        "name": "bm25_only",
        "description": "BM25 retrieval only (vector search disabled)",
        "flags": {
            "skip_mesh_filter": False,
            "skip_stage_filter": False,
            "skip_histology_filter": False,
            "skip_cross_encoder": False,
            "retrieval_mode": "bm25_only",
        },
    },
    {
        "name": "vector_only",
        "description": "Vector retrieval only (BM25 disabled)",
        "flags": {
            "skip_mesh_filter": False,
            "skip_stage_filter": False,
            "skip_histology_filter": False,
            "skip_cross_encoder": False,
            "retrieval_mode": "vector_only",
        },
    },
]

# Lookup for --configs filter validation
_VALID_CONFIG_NAMES = {c["name"] for c in ABLATION_CONFIGS}


# ===========================================================================
# STRATIFIED SAMPLING
# ===========================================================================

def _cancer_group_key(display: str) -> str:
    """Map a cancer display name to a broad anatomical group for sampling."""
    display_lower = display.lower()

    groups = [
        ("lung",        ["lung", "pulmonary", "bronch", "nsclc", "sclc"]),
        ("breast",      ["breast"]),
        ("colorectal",  ["colon", "rectal", "rectum", "colorectal"]),
        ("prostate",    ["prostate"]),
        ("pancreatic",  ["pancrea"]),
        ("ovarian",     ["ovary", "ovarian"]),
        ("uterine",     ["uterus", "uterine", "cervix", "cervical"]),
        ("hematologic", ["leukemia", "leukaemia", "lymphoma", "myeloma"]),
        ("melanoma",    ["melanoma"]),
        ("liver",       ["liver", "hepato", "hepatic"]),
        ("kidney",      ["kidney", "renal"]),
        ("bladder",     ["bladder"]),
        ("thyroid",     ["thyroid"]),
        ("brain",       ["brain", "glioma", "glioblastoma"]),
        ("head_neck",   ["oropharyn", "oral cavity", "head and neck"]),
    ]

    for group_name, keywords in groups:
        if any(kw in display_lower for kw in keywords):
            return group_name

    return "other"


def _get_patient_group(patient, registry):
    """Get the cancer group key for a single patient."""
    conditions = patient.get("conditions", [])
    cancer_conditions = [c for c in conditions if registry.is_primary_cancer(c)]
    if cancer_conditions:
        primary = sorted(cancer_conditions, key=registry.sort_key)[0]
        return _cancer_group_key(primary.get("display", "Unknown"))
    return "unknown"


def stratified_sample(patients, sample_size, seed):
    """
    Select a stratified sample covering diverse cancer types.

    Groups patients by primary cancer (via CancerCodeRegistry 3-layer
    detection + tiebreaker sort), then samples proportionally. At least
    1 patient per group. Sorted by patient_id for deterministic ordering.

    Args:
        patients:    Parsed FHIR patient dicts from load_all_patients()
        sample_size: Target count
        seed:        Random seed

    Returns:
        List of patient dicts, length = min(sample_size, len(patients))
    """
    if len(patients) <= sample_size:
        console.out(f"  Population ({len(patients)}) <= sample ({sample_size}). Using all.")
        return sorted(patients, key=lambda p: p["patient_id"])

    # Local Random instance rather than random.seed(): seeding the
    # process-wide state would shift the draw of every other consumer of
    # `random` in the same session.
    rng = random.Random(seed)
    registry = deps.get_cancer_registry()

    # Group by cancer type
    cancer_groups = defaultdict(list)
    for patient in patients:
        cancer_groups[_get_patient_group(patient, registry)].append(patient)

    # Proportional sampling, minimum 1 per group
    total = len(patients)
    sampled = []

    for group_name in sorted(cancer_groups):
        group = cancer_groups[group_name]
        share = max(1, round(len(group) / total * sample_size))
        share = min(share, len(group))
        sampled.extend(rng.sample(group, share))

    # Trim if rounding + min-1 caused oversampling. Fresh Random(seed) here,
    # not the rng above: the original code re-seeded at this point, so the
    # shuffle must start from the seed state to reproduce the same trim.
    if len(sampled) > sample_size:
        trim_rng = random.Random(seed)
        trim_rng.shuffle(sampled)
        sampled = sampled[:sample_size]

    # Deterministic processing order
    sampled.sort(key=lambda p: p["patient_id"])

    # Report
    console.out(f"\nStratified sample: {len(sampled)} patients, "
          f"{len(cancer_groups)} cancer groups")
    
    sampled_ids = {p["patient_id"] for p in sampled}
    for gname in sorted(cancer_groups):
        n_sample = sum(1 for p in cancer_groups[gname] if p["patient_id"] in sampled_ids)
        n_pop = len(cancer_groups[gname])
        console.out(f"  {gname:15s}: {n_sample:3d} sampled / {n_pop:4d} total")

    return sampled


# ===========================================================================
# DATABASE
# ===========================================================================

def init_ablation_db(db_path=None):
    """Create ablation database tables (idempotent).

    Args:
        db_path: Database to create the tables in. ``None`` means the
            production ``ablation_results.db`` -- see ``ablation_db()``.
    """
    conn = sqlite3.connect(str(ablation_db(db_path)))
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS ablation_runs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp       TEXT NOT NULL,
            config_name         TEXT NOT NULL,
            config_description  TEXT,
            sample_size         INTEGER,
            total_time_seconds  REAL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS ablation_results (
            id                              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id                          INTEGER NOT NULL,
            config_name                     TEXT NOT NULL,
            patient_id                      TEXT NOT NULL,
            cancer_group                    TEXT,
            primary_condition               TEXT,
            bm25_retrieved                  INTEGER DEFAULT 0,
            vector_retrieved                INTEGER DEFAULT 0,
            candidates_retrieved            INTEGER DEFAULT 0,
            candidates_reranked             INTEGER DEFAULT 0,
            candidates_after_rule_filter    INTEGER DEFAULT 0,
            candidates_after_quality_filter INTEGER DEFAULT 0,
            candidates_evaluated            INTEGER DEFAULT 0,
            eligible_count                  INTEGER DEFAULT 0,
            not_eligible_count              INTEGER DEFAULT 0,
            not_evaluable_count             INTEGER DEFAULT 0,
            avg_match_score                 REAL,
            avg_match_score_all             REAL DEFAULT 0,
            has_match                       INTEGER DEFAULT 0,
            criteria_not_applicable         INTEGER DEFAULT 0,
            eligible_nct_ids                TEXT DEFAULT '',
            near_miss_nct_ids               TEXT DEFAULT '',
            mesh_dropped                    INTEGER DEFAULT 0,
            stage_dropped                   INTEGER DEFAULT 0,
            histology_dropped               INTEGER DEFAULT 0,
            query_expansion_time            REAL DEFAULT 0,
            hybrid_retrieval_time           REAL DEFAULT 0,
            cross_encoder_time              REAL DEFAULT 0,
            rule_filter_time                REAL DEFAULT 0,
            llm_classifier_evaluation_time           REAL DEFAULT 0,
            total_time                      REAL DEFAULT 0,
            llm_classifier_input_tokens              INTEGER DEFAULT 0,
            llm_classifier_output_tokens             INTEGER DEFAULT 0,
            estimated_cost_usd              REAL DEFAULT 0,
            error                           TEXT DEFAULT '',
            FOREIGN KEY (run_id) REFERENCES ablation_runs(id)
        )
    """)

    # Columns added after the table was first created. CREATE TABLE IF NOT
    # EXISTS is a no-op on an existing ablation_results.db, so the INSERT below
    # would fail against a database built before the column was introduced.
    _existing = {row[1] for row in c.execute("PRAGMA table_info(ablation_results)")}
    for _column, _sql_type in {
        "not_evaluable_count":     "INTEGER DEFAULT 0",
        "avg_match_score_all":     "REAL DEFAULT 0",
        "has_match":               "INTEGER DEFAULT 0",
        "criteria_not_applicable": "INTEGER DEFAULT 0",
        # Degradation record (item 11b), same fields File 14 writes for
        # production inferences. No DEFAULT: a NULL means the stage did not
        # report, and an ablation comparison must be able to tell a run that
        # lost a retrieval channel from one that was configured without it.
        # This matters more here than in production — a config's numbers are
        # only interpretable if the run behind them was not degraded.
        "retrieval_channels":       "TEXT",
        "retrieval_degraded":       "INTEGER",
        "retrieval_trials_lost":    "INTEGER",
        "query_expansion_path":     "TEXT",
        # skip_mesh_filter now changes the Stage 5 prompt as well as the
        # filter, because the prompt's relevance assertion is conditional on
        # the filter having run (File 13, Section 2). Recorded per row so the
        # config's effect is not inferred from the config name.
        "mesh_filter_applied":      "INTEGER",
        "mesh_filter_skip_reason":  "TEXT",
        # The model that ANSWERED this row's Stage 5 call, and the key its
        # estimated_cost_usd was priced against. No DEFAULT, and no backfill:
        # rows written before this column existed were all priced against
        # whatever MATCHING_MODEL was at the time, which is not recoverable
        # from the row, so NULL is the only honest value for them. Writing
        # today's MATCHING_MODEL into them would relabel history as having run
        # on a model that did not exist when they were produced.
        #
        # NULL therefore means two different things depending on when the row
        # was written -- pre-migration, or a no-candidates run that never
        # called a model -- and the two are separable by run_id against
        # ablation_runs.run_timestamp. That ambiguity is the price of not
        # rebuilding the database, which is the right trade: the alternative
        # discards every historical ablation comparison.
        "matching_model":           "TEXT",
    }.items():
        if _column not in _existing:
            c.execute(f"ALTER TABLE ablation_results ADD COLUMN {_column} {_sql_type}")
            console.out(f"Schema migration: added ablation_results.{_column}")

            # ADD COLUMN fills existing rows with the DEFAULT, which for these
            # two is the wrong value on any row that DID have matches. Backfill
            # them from the columns the historical rows already carry, using the
            # same convention log_ablation_result() applies to new rows.
            if _column == "avg_match_score_all":
                c.execute(
                    "UPDATE ablation_results "
                    "SET avg_match_score_all = COALESCE(avg_match_score, 0.0)"
                )
                console.out(f"  Backfilled avg_match_score_all for {c.rowcount} row(s) "
                      f"(null match score -> 0.0)")
            elif _column == "has_match":
                c.execute(
                    "UPDATE ablation_results "
                    "SET has_match = CASE WHEN eligible_count > 0 THEN 1 ELSE 0 END"
                )
                console.out(f"  Backfilled has_match for {c.rowcount} row(s)")
            elif _column == "criteria_not_applicable":
                # No historical source: pre-migration runs never recorded it.
                # Left at 0 and called out so a zero is not read as "none were
                # excluded" when it means "not measured".
                console.out("  criteria_not_applicable left at 0 for pre-migration "
                      "rows (not measured, not zero)")

    conn.commit()
    conn.close()
    console.out(f"Ablation database: {ablation_db(db_path)}")


def _create_run(config_name, config_description, sample_size, db_path=None):
    """Insert a new ablation_runs row, return run_id."""
    with _ablation_db_lock:
        conn = sqlite3.connect(str(ablation_db(db_path)))
        c = conn.cursor()
        c.execute(
            "INSERT INTO ablation_runs "
            "(run_timestamp, config_name, config_description, sample_size) "
            "VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(), config_name, config_description, sample_size),
        )
        run_id = c.lastrowid
        conn.commit()
        conn.close()
        return run_id
    

def _finalize_run(run_id, elapsed_seconds, db_path=None):
    """Update run with total elapsed time."""
    with _ablation_db_lock:
        conn = sqlite3.connect(str(ablation_db(db_path)))
        conn.execute(
            "UPDATE ablation_runs SET total_time_seconds = ? WHERE id = ?",
            (round(elapsed_seconds, 2), run_id),
        )
        conn.commit()
        conn.close()
        

def log_ablation_result(run_id, config_name, patient_data, result,
                        ablation_flags, db_path=None):
    """
    Log one patient's ablation result.

    ``db_path`` is where the row goes; ``None`` means the production
    ``ablation_results.db``. It is keyword-with-a-default and LAST, so every
    existing positional call site is unchanged -- the same shape
    ``log_inference(result, patient_data, db_path=None)`` has.

    Uses get_model_cost() from File 02/03 for cost consistency with
    File 14's production logging. bm25_retrieved/vector_retrieved are the
    observed counts Stage 2 reports, not values derived from the config.
    Non-critical: errors are printed but do not crash the study.

    UnknownModelPricingError is the exception to that: cost is computed before
    the try block so an unpriced model raises out of here instead of being
    printed as "Failed to log result". A cost-per-config comparison built from
    rows that could not be priced is not a comparison, and the study should
    stop rather than produce one. Same reasoning as File 14's log_inference().
    """

    # Cost via same pricing function as File 14 — outside the try, see docstring.
    input_tok = result.get("llm_classifier_input_tokens", 0)
    output_tok = result.get("llm_classifier_output_tokens", 0)

    # The model that ACTUALLY answered, read off response.model by Stage 5 and
    # carried to all three terminal nodes by _pipeline_provenance() (File 13).
    # Not MATCHING_MODEL: that is what was asked for, it is read at log time so
    # a config edit mid-study would relabel earlier rows, and an alias can
    # resolve to a dated snapshot that never matches it. An ablation study is
    # the place where that matters most — its whole claim is that only the
    # named stage varied between configurations, and a judge that changed
    # underneath the campaign invalidates every comparison in it.
    #
    # None when no Stage 5 response was obtained: node_no_candidates, or a
    # failure before the first call returned.
    matching_model_used = result.get("matching_model")

    # MATCHING_MODEL is the pricing key ONLY in that None case, where there are
    # no Stage 5 tokens to price and the arithmetic is 0 x rate = 0 whichever
    # priced model is named. This is not a recovery path around
    # get_model_cost(): the lookup still happens, still raises
    # UnknownModelPricingError for an unpriced model, and still sits outside
    # the try block so an unpriced model stops the study instead of being
    # printed as "Failed to log result". What it is not allowed to do is raise
    # on a no-candidates run purely because that run has no model name to look
    # up. Mirrors File 14's log_inference() exactly.
    #
    # WHICH PATH WAS TAKEN IS RECORDED: matching_model below is written NULL on
    # exactly the rows where the fallback key was used, so "priced against the
    # model that answered" and "priced against the configured model because
    # nothing answered" stay separable without a second column.
    #
    # Reasoning tokens are NOT added to output_tok. They are already inside
    # usage.completion_tokens and therefore inside llm_classifier_output_tokens; adding
    # them would bill every one of them twice.
    cost = get_model_cost(matching_model_used or MATCHING_MODEL,
                          input_tok, output_tok)

    conn = None

    with _ablation_db_lock:
        
        try:
            # Counts. Tracked separately so the three buckets still sum to
            # candidates_evaluated: a trial that could not be evaluated is
            # neither a match nor a rejection.
            matches = result.get("matches", [])
            near_misses = result.get("near_misses", [])
            not_evaluable = result.get("not_evaluable", [])

            # ── Match score: two metrics, not one ────────────────────────────
            #
            # avg_match_score is CONDITIONAL on the patient having at least one
            # eligible trial: it is None when there are no matches, and every
            # consumer (SQL AVG, pandas mean) skips nulls. That makes it a mean
            # over a subpopulation whose membership is chosen by the very
            # configuration under test — an ablation that destroys recall keeps
            # only its most confident matches and scores HIGHER on it.
            #
            # avg_match_score_all is unconditional: a patient with no eligible
            # trial received no match quality, which is 0.0, not missing data.
            # It is defined for every sampled patient, so a mean over it is a
            # mean over the same population in every configuration.
            #
            # has_match makes the split itself a first-class metric, so the
            # recall a configuration gives up is reported next to the quality
            # it appears to gain.
            avg_score = None
            if matches:
                avg_score = round(
                    sum(m.get("match_score", 0) for m in matches) / len(matches), 4
                )
            avg_score_all = avg_score if avg_score is not None else 0.0
            has_match = 1 if matches else 0

            # Timings
            timings = result.get("stage_timings", {})
    
            # Cancer group
            cancer_group = _get_patient_group(patient_data, deps.get_cancer_registry())
            
            # BM25 / vector retrieval counts, as OBSERVED by Stage 2 and carried
            # out through the terminal result (File 13, _pipeline_provenance).
            #
            # These were previously derived from retrieval_mode: the disabled
            # channel got 0 and the active one got its configured request size.
            # That is a restatement of the config — every hybrid row read
            # 75/100 — so a retrieval chart built from it plots the settings,
            # not what the ablation did to recall. The disabled channel still
            # reads 0 because the channel genuinely returned nothing, and the
            # active one now varies with the query.
            bm25_retrieved = result.get("bm25_retrieved", 0)
            vector_retrieved = result.get("vector_retrieved", 0)

            # The mode still has to agree with the counts. A disabled channel
            # returning trials means the flag did not reach Stage 2, which
            # would silently invalidate the configuration under test.
            _mode = ablation_flags.get("retrieval_mode", "hybrid")
            if _mode == "vector_only" and bm25_retrieved:
                console.out(f"  WARNING: vector_only run returned {bm25_retrieved} "
                      f"BM25 trials — retrieval_mode did not reach Stage 2")
            if _mode == "bm25_only" and vector_retrieved:
                console.out(f"  WARNING: bm25_only run returned {vector_retrieved} "
                      f"vector trials — retrieval_mode did not reach Stage 2")

            # A channel that dropped out mid-run contaminates the configuration
            # it is attributed to: this patient's numbers describe less
            # retrieval than the config specifies. Said out loud at log time,
            # and stored, so a config mean can be recomputed without these rows.
            if result.get("retrieval_degraded"):
                _lost = [
                    f"{name}={c['status']}"
                    for name, c in (result.get("retrieval_channels") or {}).items()
                    if c["status"] not in (CHANNEL_OK, CHANNEL_ABLATED)
                ]
                console.out(f"  WARNING: degraded retrieval for "
                      f"{patient_data['patient_id']} — {', '.join(_lost)}. "
                      f"This row does not describe the '{config_name}' config.")
    
            # Eligible / near-miss NCT IDs for trial-level overlap analysis
            eligible_nct_ids = ",".join(
                m.get("nct_id", "") for m in matches if m.get("nct_id")
            )
            near_miss_nct_ids = ",".join(
                m.get("nct_id", "") for m in near_misses if m.get("nct_id")
            )
    
            conn = sqlite3.connect(str(ablation_db(db_path)))
            conn.execute("""
                INSERT INTO ablation_results (
                    run_id, config_name, patient_id, cancer_group, primary_condition,
                    bm25_retrieved, vector_retrieved,
                    candidates_retrieved, candidates_reranked,
                    candidates_after_rule_filter, candidates_after_quality_filter,
                    candidates_evaluated,
                    eligible_count, not_eligible_count, not_evaluable_count, avg_match_score,
                    avg_match_score_all, has_match, criteria_not_applicable,
                    eligible_nct_ids, near_miss_nct_ids,
                    mesh_dropped, stage_dropped, histology_dropped,
                    query_expansion_time, hybrid_retrieval_time, cross_encoder_time,
                    rule_filter_time, llm_classifier_evaluation_time, total_time,
                    llm_classifier_input_tokens, llm_classifier_output_tokens,
                    estimated_cost_usd, error,
                    retrieval_channels, retrieval_degraded, retrieval_trials_lost,
                    query_expansion_path, mesh_filter_applied, mesh_filter_skip_reason,
                    matching_model
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?
                )
            """, (
                run_id,
                config_name,
                patient_data["patient_id"],
                cancer_group,
                result.get("primary_condition", ""),
                bm25_retrieved,
                vector_retrieved,
                result.get("candidates_retrieved", 0),
                result.get("candidates_reranked", 0),
                result.get("candidates_after_rule_filter", 0),
                result.get("candidates_after_quality_filter", 0),
                result.get("candidates_evaluated", 0),
                len(matches),
                len(near_misses),
                len(not_evaluable),
                avg_score,
                avg_score_all,
                has_match,
                result.get("criteria_not_applicable", 0),
                eligible_nct_ids,
                near_miss_nct_ids,
                result.get("mesh_dropped", 0),
                result.get("stage_dropped", 0),
                result.get("histology_dropped", 0),
                round(timings.get("query_expansion", 0), 3),
                round(timings.get("hybrid_retrieval", 0), 3),
                round(timings.get("cross_encoder", 0), 3),
                round(timings.get("rule_filter", 0), 3),
                round(timings.get("llm_classifier_evaluation", 0), 3),
                round(sum(timings.values()), 3),
                input_tok,
                output_tok,
                round(cost, 6),
                result.get("error", ""),
                # Degradation record, carried out of the pipeline by
                # _pipeline_provenance() (File 13). NULL where the stage did
                # not report — never a substituted clean value.
                (json.dumps(result["retrieval_channels"])
                 if result.get("retrieval_channels") else None),
                result.get("retrieval_degraded"),
                result.get("retrieval_trials_lost"),
                result.get("query_expansion_path"),
                (None if result.get("mesh_filter_applied") is None
                 else int(bool(result["mesh_filter_applied"]))),
                result.get("mesh_filter_skip_reason"),
                # Resolved above, outside the tuple, because it is the same
                # value get_model_cost() was called with. Reading the result
                # dict twice could price a row against one model and label it
                # with another.
                matching_model_used,
            ))
            conn.commit()
    
        except Exception as e:
            console.out(f"  WARNING: Failed to log result: {e}")
    
        finally:
            if conn is not None:
                conn.close()


# ===========================================================================
# PIPELINE INVOCATION WITH ABLATION FLAGS
# ===========================================================================

def match_patient_ablation(patient_data, bm25_index, nct_ids, graph, ablation_flags):
    """
    Run the matching pipeline with ablation flags in the LangGraph state.

    Identical to match_patient_to_trials() except:
      - Injects 'ablation_flags' into initial state
      - Does NOT call log_inference() (no writes to production inferences.db)

    Args:
        patient_data:   Parsed FHIR patient dict
        bm25_index:     Pre-built BM25Okapi index
        nct_ids:        NCT IDs aligned with BM25 index
        graph:          Compiled LangGraph StateGraph
        ablation_flags: Dict with keys: skip_mesh_filter, skip_stage_filter,
                        skip_histology_filter, skip_cross_encoder (all bool),
                        retrieval_mode ("hybrid"|"bm25_only"|"vector_only")

    Returns:
        Result dict with: patient_id, matches, near_misses, stage_timings,
        candidates_*, llm_classifier_*_tokens, mesh/stage/histology_dropped, error
    """
    initial_state = {
        "patient_data":                     patient_data,
        "bm25_index":                       bm25_index,
        "nct_ids":                          nct_ids,
        "expanded_query":                   "",
        "hybrid_results":                   [],
        "bm25_retrieved":                   0,
        "vector_retrieved":                 0,
        "reranked_trials":                  [],
        "filtered_trials":                  [],
        "candidates_after_rule_filter":     0,
        "candidates_after_quality_filter":  0,
        "evaluations":                      [],
        "llm_classifier_retries":                    0,
        "llm_classifier_raw_response":               "",
        "result":                           {},
        "error":                            "",
        "stage_timings":                    {},
        "patient_trees":                    set(),
        "patient_histology":                set(),
        "mesh_resolution":                  "",
        "ablation_flags":                   ablation_flags,
    }

    # THE CORRELATION SCOPE IS NOT OPENED HERE. It is opened by _process_one()
    # in run_ablation_study(), one level up, and the reason is that the config
    # NAME is not in this function -- `ablation_flags` carries the flags, never
    # the name. A first draft scoped here and logged
    # ablation_flags.get("_config_name"), which is not a key of that dict: the
    # field would have been None on every line of every study, which reads as
    # "the config was not recorded" rather than as "this code asked the wrong
    # object". _process_one is also the narrowest scope that contains the
    # DATABASE WRITE as well as the pipeline run, so the two share an ID.
    final_state = graph.invoke(initial_state)
    result = final_state["result"]
    result["qdrant_collection"] = resolve_qdrant_collection()
    result["patient_data_hash"] = compute_patient_hash(patient_data)
    return result


# ===========================================================================
# SUMMARY REPORTING
# ===========================================================================

def generate_summary(db_path=None):
    """
    Query ablation database and produce summary table + deltas + JSON export.

    Uses the most recent run per config_name, so re-running a single config
    updates its row without affecting others. Returns DataFrame or None.

    ``db_path`` is the database to read and the directory the JSON export lands
    in; ``None`` means the production pair. See ``ablation_summary_json()`` for
    why the export follows the database rather than staying put.
    """
    if not ablation_db(db_path).exists():
        console.out("No ablation database found.")
        return None

    conn = sqlite3.connect(str(ablation_db(db_path)))
    try:
        df = pd.read_sql_query("""
            SELECT
                r.config_name,
                COUNT(*)                                            AS n,
                ROUND(AVG(r.candidates_retrieved), 1)               AS avg_retrieved,
                ROUND(AVG(r.candidates_reranked), 1)                AS avg_reranked,
                ROUND(AVG(r.candidates_after_rule_filter), 1)       AS avg_after_rules,
                ROUND(AVG(r.candidates_evaluated), 1)               AS avg_evaluated,
                ROUND(AVG(r.eligible_count), 2)                     AS avg_eligible,
                ROUND(AVG(r.not_eligible_count), 2)                 AS avg_not_eligible,
                -- Unconditional: every sampled patient contributes, zero-match
                -- patients at 0.0. This is the headline quality metric.
                ROUND(AVG(r.avg_match_score_all), 3)                AS avg_score_all,
                -- Proportion of sampled patients with >= 1 eligible trial. The
                -- recall a configuration gives up must be read alongside any
                -- apparent quality gain, not separately from it.
                ROUND(AVG(CASE WHEN r.eligible_count > 0
                               THEN 1.0 ELSE 0.0 END), 3)           AS match_rate,
                -- Conditional on having >= 1 match. Reported with its own n
                -- because that n differs by configuration.
                ROUND(AVG(r.avg_match_score), 3)                    AS avg_score_cond,
                SUM(CASE WHEN r.eligible_count > 0 THEN 1 ELSE 0 END) AS n_scored,
                ROUND(AVG(r.mesh_dropped), 1)                       AS avg_mesh_drop,
                ROUND(AVG(r.stage_dropped), 1)                      AS avg_stage_drop,
                ROUND(AVG(r.histology_dropped), 1)                  AS avg_hist_drop,
                ROUND(AVG(r.total_time), 2)                         AS avg_time_s,
                ROUND(AVG(r.estimated_cost_usd), 4)                 AS avg_cost,
                ROUND(SUM(r.estimated_cost_usd), 4)                 AS total_cost,
                -- Pooled, not a per-patient mean: total spend over total
                -- matches found. A zero-match patient's cost stays in the
                -- numerator, so a configuration cannot look cheap per match by
                -- dropping the patients it failed. NULL when nothing matched.
                ROUND(SUM(r.estimated_cost_usd)
                      / NULLIF(SUM(r.eligible_count), 0), 5)        AS cost_per_eligible,
                SUM(CASE WHEN r.error != '' THEN 1 ELSE 0 END)      AS errors
            FROM ablation_results r
            INNER JOIN (
                SELECT config_name, id AS max_run_id
                FROM ablation_runs
                WHERE (config_name, run_timestamp) IN (
                    SELECT config_name, MAX(run_timestamp)
                    FROM ablation_runs
                    GROUP BY config_name
                )
            ) latest ON r.config_name = latest.config_name
                     AND r.run_id    = latest.max_run_id
            GROUP BY r.config_name
        """, conn)
    finally:
        conn.close()

    if df.empty:
        console.out("No ablation results found.")
        return None

    # Reorder to match ABLATION_CONFIGS
    order = {c["name"]: i for i, c in enumerate(ABLATION_CONFIGS)}
    df["_sort"] = df["config_name"].map(order).fillna(999)
    df = df.sort_values("_sort").drop(columns=["_sort"]).reset_index(drop=True)

    # --- Print compact table ---
    console.out("\n" + "=" * 130)
    console.out("  ABLATION STUDY RESULTS")
    console.out("=" * 130 + "\n")
    console.out(df.to_string(index=False))
    console.out(
        "\n  avg_score_all  = mean match score over ALL n sampled patients "
        "(no eligible trial counts as 0.0)."
        "\n  match_rate     = proportion of sampled patients with >= 1 eligible trial."
        "\n  avg_score_cond = CONDITIONAL mean over the n_scored patients that had "
        "a match; n_scored varies by config,"
        "\n                   so this column is NOT comparable across configs on "
        "its own."
    )

    # --- Deltas vs baseline ---
    bl_rows = df[df["config_name"] == "full_pipeline"]
    if not bl_rows.empty:
        bl = bl_rows.iloc[0]

        console.out("\n" + "-" * 130)
        console.out("  DELTAS vs FULL PIPELINE (baseline)")
        console.out("-" * 130)
        console.out(f"  {'Config':25s} | {'Δevaluated':>11s} | {'Δeligible':>10s} | "
              f"{'Δscore_all':>10s} | {'Δmatch_rate':>12s} | {'Δcost/pt':>10s} | "
              f"{'Δtime/pt':>9s} | {'Δmesh_drop':>11s} | {'Δstage_drop':>12s}")
        console.out("  " + "-" * 122)

        for _, row in df.iterrows():
            if row["config_name"] == "full_pipeline":
                continue
            console.out(
                f"  {row['config_name']:25s} | "
                f"{row['avg_evaluated']  - bl['avg_evaluated']:+11.1f} | "
                f"{row['avg_eligible']   - bl['avg_eligible']:+10.2f} | "
                f"{row['avg_score_all']  - bl['avg_score_all']:+10.3f} | "
                f"{row['match_rate']     - bl['match_rate']:+12.3f} | "
                f"${row['avg_cost']      - bl['avg_cost']:+9.4f} | "
                f"{row['avg_time_s']     - bl['avg_time_s']:+9.2f} | "
                f"{row['avg_mesh_drop']  - bl['avg_mesh_drop']:+11.1f} | "
                f"{row['avg_stage_drop'] - bl['avg_stage_drop']:+12.1f}"
            )

        # The conditional mean is shown only against its own n, never as a
        # bare delta: the two configs being differenced averaged over different
        # patient sets, so the difference is not attributable to the ablation.
        console.out("\n  CONDITIONAL SCORE (mean over matched patients only -- read with n)")
        console.out(f"  {'Config':25s} | {'score_cond':>10s} | {'n_scored':>8s} | {'n':>5s}")
        console.out("  " + "-" * 56)
        for _, row in df.iterrows():
            _cond = row["avg_score_cond"]
            _cond_s = "N/A" if pd.isna(_cond) else f"{_cond:.3f}"
            console.out(
                f"  {row['config_name']:25s} | {_cond_s:>10s} | "
                f"{int(row['n_scored']):>8d} | {int(row['n']):>5d}"
            )

    # --- JSON export ---
    summary_records = df.to_dict(orient="records")
    with open(ablation_summary_json(db_path), "w") as f:
        json.dump(summary_records, f, indent=2)
    console.out(f"\n  Summary exported: {ablation_summary_json(db_path)}")

    return df


# ===========================================================================
# MAIN
# ===========================================================================

def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="OncoMatch Ablation Study")
    parser.add_argument(
        "--sample-size", type=int, default=SAMPLE_SIZE_DEFAULT,
        help=f"Number of patients to sample (default: {SAMPLE_SIZE_DEFAULT})"
    )
    parser.add_argument(
        "--summary-only", action="store_true",
        help="Skip pipeline runs; print summary from existing database"
    )
    parser.add_argument(
        "--configs", nargs="+", default=None,
        help="Run only specific configs (e.g., --configs full_pipeline no_mesh_filter)"
    )
    # Pass 20f-1. Default None, which is the production database and every
    # documented command's behaviour. The summary JSON follows the database
    # into the same directory, which the help says out loud because a run that
    # quietly stopped updating ablation_summary.json would be a surprise.
    parser.add_argument(
        "--db", default=None, metavar="PATH",
        help="Write the study to this SQLite database instead of the "
             "production ablation_results.db. ablation_summary.json is "
             "written beside it. Default: the production database."
    )
    return parser.parse_args()


def main():
    """Run the ablation study."""

    args = parse_args()

    # One local, read by every writer call below. None is the production
    # database; --db is the only thing that changes it, and it is threaded
    # explicitly rather than stashed in module state, so nothing this function
    # calls can be redirected by anything except its own argument.
    db_path = args.db

    console.out()
    console.out("=" * 70)
    console.out(f"{Project_Name}: ABLATION STUDY")
    console.out("=" * 70)
    console.out()
    if db_path is not None:
        console.out(f"  --db in effect: {ablation_db(db_path)}")
        console.out(f"  Summary will go beside it: {ablation_summary_json(db_path)}")
        # Named as loudly as the database, because pass 20f-3 changed WHICH
        # file this is and an operator resuming an isolated study needs to see
        # that it is not the production one.
        console.out(f"  Checkpoint (resume state): {_ablation_checkpoint_path(db_path)}")
        console.out()

    # --- Summary-only mode ---
    # init_ablation_db() runs first even though nothing is written: the summary
    # query selects avg_match_score_all, which a database built before that
    # column existed does not have. init is idempotent and performs the
    # migration, so --summary-only works against an old database.
    if args.summary_only:
        init_ablation_db(db_path=db_path)
        generate_summary(db_path=db_path)
        return

    # --- Validate --configs if provided ---
    if args.configs:
        invalid = set(args.configs) - _VALID_CONFIG_NAMES
        if invalid:
            console.out(f"ERROR: Unknown config(s): {invalid}")
            console.out(f"Valid configs: {sorted(_VALID_CONFIG_NAMES)}")
            sys.exit(1)
        configs = [c for c in ABLATION_CONFIGS if c["name"] in args.configs]
    else:
        configs = ABLATION_CONFIGS

    with CaffeinateSession("Ablation Study"):

        # --- Step 1: Initialize ---
        init_ablation_db(db_path=db_path)

        console.out("\n[Step 1] Building BM25 index...")
        bm25_index, nct_ids = build_bm25_index_from_qdrant()
        console.out(f"  {len(nct_ids)} trials indexed")

        if not nct_ids:
            console.out("ERROR: No trials in Qdrant. Run File 11 first.")
            sys.exit(1)

        console.out("[Step 1] Compiling LangGraph pipeline...")
        graph = build_matching_graph()

        # --- Step 2: Load and sample patients ---
        console.out(f"\n[Step 2] Loading patients from {paths.data_fhir_path}...")
        all_patients = load_all_patients(paths.data_fhir_path)
        console.out(f"  {len(all_patients)} patients loaded")

        if not all_patients:
            console.out("ERROR: No patients found. Run Files 04-07 first.")
            sys.exit(1)

        sample = stratified_sample(all_patients, args.sample_size, ABLATION_SEED)

        # --- Step 3: Resume support ---
        completed = load_ablation_checkpoint(db_path=db_path)

        # --- Step 4: Run each config ---
        total_configs = len(configs)
        total_runs = total_configs * len(sample)
        already_done = len(completed)
        remaining = total_runs - already_done
        study_start = time.time()

        console.out(f"\n  Total runs:     {total_runs} ({total_configs} configs × {len(sample)} patients)")
        console.out(f"  Already done:   {already_done}")
        console.out(f"  Remaining:      {remaining}")
        console.out()

        # --- tqdm progress bar ---
        console.out("*" * 70)
        progress = tqdm(
            total=total_runs,
            initial=already_done,
            desc="🔬 ABLATION PROGRESS",
            unit="run",
            bar_format="{desc}: {percentage:3.0f}%|{bar:40}| {n_fmt}/{total_fmt} "
                       "[Elapsed: {elapsed} | ETA: {remaining} | {rate_fmt}] {postfix}",
            ncols=120,
            smoothing=0.1,
        )

        run_success = 0
        run_error = 0
        interrupted = False

        # THE builtins.print MONKEY-PATCH USED TO BE HERE, AND IT IS DELETED.
        # See oncotriage/batch/runner.py for what it did to every print(end=),
        # print(sep=), print(file=) and print(flush=) in the process while it
        # was live. Registering the bar with the console channel serves the one
        # real purpose it had -- keeping output off the bar's redraw -- and
        # covers the structured log handler too, which the patch could not.
        _bar_token = console.attach_bar()

        def _process_one(patient_data, config_name, ablation_flags, run_id):
            """Run pipeline + log for one patient-config pair.

            A pipeline failure is caught and turned into an error result, so
            one bad patient does not stop the study. There are TWO exceptions
            to that, and both are conditions of the study rather than of the
            patient:

              UnknownModelPricingError  out of log_ablation_result(). Not a
                per-patient failure but a missing entry in PRICING_CONFIG.

              MatchingModelMismatchError  out of Stage 5 (File 13). The judge
                resolved to a different model mid-campaign. Catching it would
                turn "every configuration was compared against the same judge"
                -- the entire premise of an ablation study -- into a claim the
                results cannot support, one silently-failed patient at a time.
                An ablation study is the single worst place in this project to
                absorb this error, which is why it is named explicitly rather
                than left to the blanket handler below.

            Both propagate through future.result() and stop the run. The
            checkpoint means resuming after fixing File 03 costs nothing.
            """
            pid = patient_data["patient_id"]
            # ONE (patient, config) PAIR IS ONE CORRELATION ID, and this is the
            # narrowest scope that contains the whole pair -- the pipeline run
            # AND the ablation_results.db write below. The study drives
            # MAX_WORKERS of these at once, so without a scope every line it
            # emits would carry the "-" sentinel and a study's logs would be one
            # undifferentiated stream.
            #
            # Two configs of the same patient are two runs and get two IDs, on
            # purpose; `config_name` and `patient_id` are the fields that join
            # them back together.
            with correlation_scope():
                log.info("ablation run started",
                         event="ablation_run_started", patient_id=pid,
                         config_name=config_name, run_id=run_id)
                return _process_one_scoped(patient_data, config_name,
                                           ablation_flags, run_id, pid)

        def _process_one_scoped(patient_data, config_name, ablation_flags,
                                run_id, pid):
            """The body of _process_one, inside its correlation scope.

            Split out rather than indenting the original eighty-line body: a
            re-indentation of that size is a diff nobody can review in a pass
            whose promise is that only output routing changed, and this way the
            body below is byte-identical to what it was.
            """
            try:
                result = match_patient_ablation(
                    patient_data, bm25_index, nct_ids, graph, ablation_flags
                )
            except MatchingModelMismatchError:
                # Re-raised before the blanket handler can see it. Deliberately
                # not wrapped or re-worded: File 13's message already carries
                # both model strings and what to do about them.
                raise
            except Exception as e:
                traceback.print_exc()
                result = {
                    "error": str(e),
                    "matches": [],
                    "near_misses": [],
                    "not_evaluable": [],
                    "stage_timings": {},
                    "primary_condition": "",
                    "candidates_retrieved": 0,
                    "candidates_reranked": 0,
                    "candidates_after_rule_filter": 0,
                    "candidates_after_quality_filter": 0,
                    "candidates_evaluated": 0,
                    "mesh_dropped": 0,
                    "stage_dropped": 0,
                    "histology_dropped": 0,
                    "llm_classifier_input_tokens": 0,
                    "llm_classifier_output_tokens": 0,
                }

            log_ablation_result(run_id, config_name, patient_data, result,
                                ablation_flags, db_path=db_path)
            return pid, result

        try:
            for config_idx, config in enumerate(configs, 1):
                config_name = config["name"]
                ablation_flags = config["flags"]

                # Skip entirely completed configs
                config_pairs = {(config_name, p["patient_id"]) for p in sample}
                
                if config_pairs.issubset(completed):
                    console.out(f"\n  [SKIP] Config '{config_name}' already completed.")
                    progress.update(len(config_pairs))
                    continue

                console.out(f"\n{'#' * 70}")
                console.out(f"# CONFIG {config_idx}/{total_configs}: {config_name}")
                console.out(f"# {config['description']}")
                console.out(f"# Flags: {ablation_flags}")
                console.out(f"{'#' * 70}")

                run_id = _create_run(config_name, config["description"],
                                     len(sample), db_path=db_path)
                config_start = time.time()

                # Filter to pending patients for this config
                pending_patients = [
                    p for p in sample
                    if (config_name, p["patient_id"]) not in completed
                ]
                # Update progress for already-completed patients in this config
                already_done_in_config = len(sample) - len(pending_patients)
                if already_done_in_config > 0:
                    progress.update(already_done_in_config)

                def _on_done(future, _config_name=config_name):
                    nonlocal run_success, run_error
                    try:
                        pid, result = future.result()
                    except Exception as e:
                        run_error += 1
                        progress.set_postfix(ok=run_success, err=run_error)
                        progress.update(1)
                        console.out(f"  [CALLBACK ERROR] {_config_name}: {type(e).__name__}: {e}")
                        return

                    if result.get("error"):
                        run_error += 1
                    else:
                        run_success += 1

                    completed.add((_config_name, pid))
                    save_ablation_checkpoint(completed, db_path=db_path)

                    progress.set_postfix(ok=run_success, err=run_error)
                    progress.update(1)

                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    futures = []
                    for patient_data in pending_patients:
                        future = executor.submit(
                            _process_one,
                            patient_data=patient_data,
                            config_name=config_name,
                            ablation_flags=ablation_flags,
                            run_id=run_id,
                        )
                        future.add_done_callback(_on_done)
                        futures.append(future)

                    # Wait for all to complete (callbacks handle progress)
                    for future in futures:
                        future.result()

                config_elapsed = time.time() - config_start
                _finalize_run(run_id, config_elapsed, db_path=db_path)
                console.out(f"\n  Config '{config_name}' done: {config_elapsed / 60:.1f} min")

        except KeyboardInterrupt:
            interrupted = True
            console.out("\n[INTERRUPTED] Waiting for active threads to finish...")
            # ThreadPoolExecutor's with-block handles shutdown

        finally:
            progress.close()
            console.detach_bar(_bar_token)

        # --- Step 5: Summary ---
        study_elapsed = time.time() - study_start

        console.out()
        console.out("=" * 70)
        console.out(f"{Project_Name}: ABLATION STUDY SUMMARY")
        console.out("=" * 70)
        console.out(f"  Wall time:       {study_elapsed / 60:.1f} min")
        console.out(f"  Completed:       {run_success + run_error}")
        console.out(f"  Success:         {run_success}")
        console.out(f"  Errors:          {run_error}")
        console.out(f"  Database:        {ablation_db(db_path)}")

        # Checkpoint degradations, reported here rather than left in the
        # scrollback (pass 20f-1, item 11a's shape). Printed only when there
        # were any, matching INDEX_AGE_PARSE_FAILURES in
        # oncotriage/retrieval/indexer.py -- a zero line every run trains a
        # reader to skip it.
        if CHECKPOINT_WRITE_FAILURES:
            console.out(f"  Checkpoint:      "
                  f"{sum(CHECKPOINT_WRITE_FAILURES.values())} write "
                  f"degradation(s) {dict(CHECKPOINT_WRITE_FAILURES)} -- resume "
                  f"state may be behind the rows already in the database")

        if interrupted:
            console.out(f"  Status:          INTERRUPTED (resume with same command)")
        else:
            generate_summary(db_path=db_path)
            clear_ablation_checkpoint(db_path=db_path)
            console.out(f"  Summary:         {ablation_summary_json(db_path)}")
            console.out(f"  Status:          COMPLETE")

        console.out("=" * 70)
        console.out()
#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
