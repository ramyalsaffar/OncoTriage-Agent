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
import contextlib
import json
import os
import random
import sqlite3
import sys
import threading
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import CancelledError, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd
from tqdm import tqdm

# `control` HOLDS THE RUN LOCK AND THE STOP SWITCH, and importing it at MODULE
# SCOPE is what preserves a guarantee this file used to state through its own
# `import fcntl`. That module is POSIX-only and its absence is a REFUSAL rather
# than a degradation: running a paid study UNLOCKED because the locking
# primitive was missing would be precisely the failure the lock exists to
# prevent, and it would be silent. At module scope the failure is at import --
# and because this import is at module scope, that holds transitively without a
# second `import fcntl` here to keep in step.
from oncotriage import control
from oncotriage import degradation
from oncotriage import paths
from oncotriage.ablation.common import (
    ABLATION_DB_FILENAME,
    ABLATION_SUMMARY_FILENAME,
    _require_writable_parent,
    open_ablation_db_readonly,
)
# ── THE WRITE MACHINERY, BORROWED RATHER THAN REBUILT ──────────────────────
#
# This module writes its own database with the same shape of concurrency
# `inferences.db` has -- a thread pool, a done-callback inserting a row per
# completed patient -- and it had none of the hardening that one grew: a bare
# `sqlite3.connect` on sqlite3's 5-second default timeout, the rollback journal,
# and no retry at all. The three names below are the storage layer's policy,
# reused so there is ONE definition of "how long do we wait", "which journal
# mode", and "which errors are transient". A second copy of that policy here is
# how the two halves of a rule drift apart while both look maintained.
#
# THE DIRECTION OF THE IMPORT IS FINE: `ablation` is a top-level consumer and
# `storage` is below it -- `oncotriage/batch/runner.py` imports the same module
# for the same reason. `storage` imports nothing from `ablation`.
from oncotriage.storage.database_logger import (
    apply_journal_mode,
    open_connection,
    run_with_write_retry,
)
from oncotriage.agent import deps
from oncotriage.agent.evaluation import (
    MatchingModelMismatchError,
    clear_stage5_shutdown,
    request_stage5_shutdown,
)
from oncotriage.agent.graph import build_matching_graph
from oncotriage.agent.patient import compute_patient_hash
from oncotriage.agent.retrieval import build_bm25_index_from_qdrant
from oncotriage.agent.state import CHANNEL_ABLATED, CHANNEL_OK
from oncotriage.config import (ABLATION_SAMPLE_SIZE_DEFAULT, ABLATION_SEED,
                              MATCHING_MODEL, MAX_WORKERS, Project_Name)
from oncotriage.fhir.parser import load_all_patients
from oncotriage.utils import (
    CaffeinateSession,
    get_model_cost,
    preserve_corrupt_file,
    resolve_qdrant_collection,
)
from oncotriage import run_fingerprint
from oncotriage import spend
from oncotriage import tracking
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

# SAMPLE_SIZE_DEFAULT and ABLATION_SEED were literals here. Both are
# oncotriage/config.py's now, imported above and unchanged in value, and the
# first was renamed ABLATION_SAMPLE_SIZE_DEFAULT on the way: config is one flat
# namespace and a bare `SAMPLE_SIZE_DEFAULT` there reads as "the" sample size
# for a project that has three unrelated samplers in it.
#
# ONLY ONE OF THE TWO IS TRACKED, and the asymmetry is the point.
# oncotriage/tracking.py logs ABLATION_SEED, because nothing overrides it, and
# does NOT log the default sample size, because --sample-size does and a
# default the run did not use is a false record. See CONFIGURATION_PARAM_NAMES.
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


CORRUPT_CHECKPOINT_SUFFIX = ".corrupt"
"""Suffix a checkpoint that could not be read is COPIED to.

Copied, never renamed, and ``oncotriage/utils.py:preserve_corrupt_file`` argues
why: a renamed checkpoint is gone from its own path, so the refusal below would
be loud once and silent afterwards -- the next invocation would find nothing,
start fresh, and re-run every ``(config, patient)`` pair at a live Stage 5 call
each. Copying leaves the refusal STICKY until an operator passes
``--fresh-start``.
"""

CHECKPOINT_FAULTS = Counter()
"""Ablation checkpoint faults, keyed ``{phase}:{detail}`` -- the same phases and
the same shape as ``oncotriage/batch/runner.py:CHECKPOINT_FAULTS``, deliberately
a SEPARATE counter because the two describe different files and a shared name
would report a batch fault and a study fault as one number.

    ``load:``      the file existed and could not be read back
    ``shape:``     it parsed and was not a checkpoint
    ``preserve:``  the unreadable file could not even be copied aside
    ``refused:``   a readable checkpoint was REFUSED, keyed by the
                   ``FP_OUTCOMES`` member that refused it
"""


def report_checkpoint_faults(out=None) -> bool:
    """CHECKPOINT_FAULTS' end-of-study reader. True when it had something to say.

    THE READ SIDE OF THE FILE ``CHECKPOINT_WRITE_FAILURES`` REPORTS THE WRITE
    SIDE OF, and it had no reader anywhere until the counter-reader audit. Its
    home is this module rather than ``oncotriage/degradation.py`` for the
    reason that module's docstring gives for its neighbour: importing
    ``ablation.study`` into the registry would drag the whole study -- graph,
    fixtures, thread pool -- into ``25- Batch Runner.py``. The batch runner's
    identically-named counter DOES go there, through ``register()``, and the
    name already being taken is a second, independent reason this one could not
    join even if the import graph allowed it.

    REPORTING AFTER THE FACT IS NOT REDUNDANT. A ``refused:`` fault already
    printed a loud refusal with a remediation command at the TOP of the run,
    thousands of lines of scrollback ago, and a ``load:`` / ``shape:`` /
    ``preserve:`` fault printed a warning and carried on. This puts it beside
    the numbers it qualifies, because "Status: COMPLETE" over a study that
    silently began from scratch is the reading this counter exists to prevent.

    ``out`` is injectable and this is a function rather than four lines inside
    ``main()`` on ``degradation.print_report``'s argument: ``main()`` cannot be
    driven without a live graph and a paid Stage 5 call per patient, and a
    reader nothing can exercise is how a reader comes to be wrong.
    """
    if not CHECKPOINT_FAULTS:
        return False
    emit = out or console.out
    emit(f"  Ckpt faults:     {sum(CHECKPOINT_FAULTS.values())} read/refusal "
         f"fault(s) {dict(sorted(CHECKPOINT_FAULTS.items()))} -- a 'refused:' "
         f"key means the resume was DECLINED and this run covers ONLY what it "
         f"executed itself")
    return True


def _checkpoint_remediation(db_path) -> tuple:
    """The command that clears THIS database's refused checkpoint.

    PER DATABASE, because the checkpoint is (pass 20f-3). A remediation naming
    the production checkpoint while the operator is running ``--db scratch.db``
    would send them to delete the wrong file -- and the production one is the
    expensive one.

    This entry point HAS argparse, so unlike the batch runner the remediation
    is a flag on the documented command rather than a ``python -c``.
    """
    flag = "--fresh-start" + (f" --db {db_path}" if db_path is not None else "")
    return (
        f"The checkpoint this refers to is {_ablation_checkpoint_path(db_path)}",
        "To start fresh (this DISCARDS the resume state and re-runs every "
        "(config, patient) pair, at cost):",
        f'    python "26- Ablation Study.py" {flag}',
        "NOTHING HAS BEEN DELETED. The checkpoint is exactly as it was.",
    )


def load_ablation_checkpoint(db_path=None, fingerprint=None) -> set:
    """Load set of completed (config_name, patient_id) tuples for `db_path`.

    Args:
        db_path: which database's resume state to read. Unchanged, and pass
            20f-3's argument for it is unchanged with it.
        fingerprint: the configuration to compare the stored stamp against.
            ``None`` takes ``run_fingerprint.current()``, which asks Qdrant --
            so a caller with no endpoint passes its own rather than being
            refused for a reason that has nothing to do with its checkpoint.
            ``main()`` passes the stamp it resolved before its pool existed.

    Raises:
        run_fingerprint.ResumeRefusal: the checkpoint exists and this run may
            not continue it -- unreadable, or produced by a different
            configuration, or by an unknown one.

    WHAT IT USED TO DO SILENTLY, TWICE OVER. An unreadable checkpoint warned
    and returned an empty set, which on a resume is a silent decision to re-run
    every pair an earlier study completed -- up to 525 live Stage 5 calls. And
    a checkpoint recorded WHAT was done and never what it was done UNDER, so a
    study resumed after a prompt edit skipped the pairs the old prompt had
    completed and ran the rest under the new one, into one
    ``ablation_results.db`` -- and ``generate_summary`` averages per config, so
    a config split across two prompts comes back with a plausible number
    computed over two different pipelines.

    THE PER-DATABASE ISOLATION PASS 20f-3 ESTABLISHED IS UNCHANGED AND IS WHAT
    THE STAMP RIDES IN. The fingerprint goes inside ``db_path``'s own
    checkpoint file, so a scratch study and a production study still do not see
    each other in either direction -- and a scratch study run under a different
    configuration now cannot silently continue a production one even if
    somebody points ``--db`` at the same directory.
    """
    cp = _ablation_checkpoint_path(db_path)
    if not cp.exists():
        return set()

    try:
        with open(cp, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        _unreadable_checkpoint(cp, db_path, f"{type(e).__name__}: {e}",
                               f"load:{type(e).__name__}")

    if not isinstance(data, dict):
        _unreadable_checkpoint(
            cp, db_path, f"expected a JSON object, found {type(data).__name__}",
            f"shape:{type(data).__name__}")
    pairs = data.get("completed")
    if not isinstance(pairs, list):
        # NOT the same as an empty checkpoint. `data.get("completed", [])` used
        # to turn this into "nothing completed", which is a silent full re-run
        # wearing the clothes of a successful read.
        _unreadable_checkpoint(
            cp, db_path, f"'completed' is {type(pairs).__name__}, not a list",
            f"shape:completed={type(pairs).__name__}")

    outcome, detail = run_fingerprint.compare(
        data.get("fingerprint"),
        fingerprint if fingerprint is not None else run_fingerprint.current())
    if outcome != run_fingerprint.FP_MATCH:
        CHECKPOINT_FAULTS[f"refused:{outcome}"] += 1
        log.error("ablation checkpoint refused", event="checkpoint_refused",
                  status="error", error_type=outcome)
        raise run_fingerprint.ResumeRefusal("\n".join(
            run_fingerprint.refusal_lines(
                outcome, detail, f"the ablation checkpoint at {cp}",
                _checkpoint_remediation(db_path),
                # The stored stamp, so a checkpoint written by a NEWER build is
                # refused WITHOUT `--fresh-start` being presented as the fix.
                recorded=data.get("fingerprint"))),
            outcome=outcome)

    try:
        completed = set(tuple(pair) for pair in pairs)
    except TypeError as e:
        # A "completed" list whose members are not iterable -- a list of ints,
        # say. `tuple(3)` raises, and the pre-pass code did not catch it, so
        # the study died with a TypeError instead of a diagnosis.
        _unreadable_checkpoint(
            cp, db_path, f"'completed' holds a member that is not a pair: {e}",
            "shape:completed_member")

    console.out(f"[Checkpoint] Resuming: {len(completed)} patient-config pairs already completed.")
    console.out(f"[Checkpoint] Configuration matches: {detail}")
    return completed


def _unreadable_checkpoint(cp, db_path, error: str, counter_key: str) -> None:
    """Count, COPY aside, log, and raise. Always raises; never returns."""
    CHECKPOINT_FAULTS[counter_key] += 1
    preserved, preserve_error, preserve_key = preserve_corrupt_file(
        cp, CORRUPT_CHECKPOINT_SUFFIX, keep_original=True)
    if preserved:
        where = f"A copy has been preserved as {preserved}."
    else:
        CHECKPOINT_FAULTS[f"preserve:{preserve_key}"] += 1
        where = (f"It could NOT be copied aside ({preserve_error}), so the only "
                 f"copy is the one still at {cp}.")

    log.error("ablation checkpoint unreadable", event="checkpoint_unreadable",
              status="error", error_message=error)
    raise run_fingerprint.ResumeRefusal("\n".join(
        [f"REFUSED (unreadable): the ablation checkpoint at {cp}",
         f"    {error}",
         f"    {where}",
         "    Continuing would silently re-run every (config, patient) pair an "
         "earlier study completed, at a live Stage 5 call each. THE CHECKPOINT "
         "IS INTACT: nothing was deleted and no pair was re-run."]
        + [f"    {line}" for line in _checkpoint_remediation(db_path)]),
        outcome=run_fingerprint.FP_ABSENT)


def save_ablation_checkpoint(completed: set, db_path=None,
                             fingerprint: dict = None) -> None:
    """Atomically persist completed set to `db_path`'s checkpoint file.

    ``fingerprint`` defaults to ``run_fingerprint.current()`` -- what every
    call site passes -- and is an argument at all so a test can write a
    checkpoint stamped with a configuration it chooses rather than having
    to reach into the resolver's cache to fabricate one.
    """
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
                        # WHAT PRODUCED THIS SET. `run_fingerprint.current()`
                        # is resolved once per process and cached, so this is
                        # free after the first call -- and the caching is a
                        # correctness argument rather than a saving: a study is
                        # ONE configuration, and a per-write stamp straddling
                        # the weekly alias swap would put two collections into
                        # one checkpoint and the file would then refuse itself.
                        "fingerprint": (fingerprint if fingerprint is not None
                                        else run_fingerprint.current()),
                        "collection_identity":
                            run_fingerprint.COLLECTION_IDENTITY,
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
# THE STUDY RUN LOCK
# ===========================================================================
#
# TWO STUDIES AGAINST ONE STATE DIRECTORY IS THE BATCH RUNNER'S SILENT DOUBLE
# BILL, WITH A LARGER MULTIPLIER. oncotriage/batch/runner.py's THE RUN LOCK
# section measured it for a batch pass: both invocations read the same
# checkpoint at start, both process the same units at one live Stage 5 call
# each, and the loser's completions vanish from the checkpoint when the winner
# next writes it -- so a third run re-bills those too. Every word of that
# transfers here, and one thing is worse: a study's unit is a (config, patient)
# PAIR, so the same patient is paid for once per configuration, and two studies
# overlapping across seven configurations duplicate up to 7 x sample_size calls
# rather than sample_size.
#
# IT ALSO CORRUPTS THE COMPARISON, WHICH THE BATCH CASE CANNOT. generate_summary
# reports the LATEST ablation_runs row per config, joined to its results by
# run_id. Two studies interleaved produce two rows per config with the sample
# SPLIT arbitrarily between them, so the row that wins is an average over
# whichever subset that process happened to run -- and the configs are then
# compared against each other over different patient sets, which is the one
# thing an ablation study may not do.
#
# THE KEY IS THE CHECKPOINT FILE, NOT ITS DIRECTORY, AND THAT DIVERGES FROM THE
# BATCH RUNNER DELIBERATELY. That file keys on the checkpoint DIRECTORY because
# it has exactly one checkpoint. This one's checkpoint follows --db (pass
# 20f-3): two scratch databases in one directory have two checkpoints, on the
# argument that "already written" is a statement about A DATABASE. The lock
# protects that same state, so it has to be as fine-grained as the state is --
# keyed on the directory, two independent --db studies in /tmp would refuse
# each other for nothing.
#
# AND THE FILENAME PREFIX IS DIFFERENT FROM THE BATCH RUNNER'S, WHICH IS NOT
# COSMETIC. With no --db the study's state directory IS paths.checkpoint_path,
# the same directory the batch runner locks. A shared lock file would make a
# batch run and an ablation study block each other -- two harnesses that write
# different databases, read different checkpoints and have nothing to say to
# one another. The digest is of the same string; the prefix is what keeps the
# two namespaces apart.
#
# THE MECHANISM IS UNCHANGED AND IS NOT RE-ARGUED HERE: flock(LOCK_EX |
# LOCK_NB) on a file in the system temp directory, held for the process's life,
# released BY THE KERNEL however it exits. Read THE RUN LOCK in
# oncotriage/batch/runner.py for why it is not a pid file and why the lock file
# is never unlinked.

EXIT_LOCK_UNAVAILABLE = 1
"""Exit code when the lock could not be ATTEMPTED at all.

DELIBERATELY NOT ``control.EXIT_LOCKED``. 3 means another study is already
running, which a supervisor may reasonably wait out; this means the lock file
could not be opened, which waiting does not fix. 1 is what every other refusal
in this entry point returns and carries the same standing: nothing run, nothing
billed.

IT STAYS HERE RATHER THAN MOVING TO ``oncotriage/control.py`` WITH ITS SIBLING.
``EXIT_LOCKED`` is 3 in all three programs and they agree on why, so it is a
fact about the mechanism. This one's VALUE is read off THIS entry point's own
vocabulary -- 1 is what its other refusals return -- and the serial runner,
whose 1 already means "a test failed", uses 4. One shared constant cannot carry
that.

THE RESIDUAL AMBIGUITY IS STATED RATHER THAN GLOSSED: 1 is also what a refused
checkpoint and a stale sentinel return, so the exit code alone says "refused
before running" and not WHICH refusal. The console line is unambiguous, and
giving this one a fourth code while its two siblings keep 1 would make the
vocabulary less legible rather than more.
"""


LOCK_FILE_PREFIX = "oncotriage-ablation-run-"
"""The lock file's name prefix, and it is load-bearing rather than cosmetic.

All three of this project's run locks live in ONE per-user directory (see
``control.lock_directory`` for why one directory is the right shape), so the
prefix is the only thing that keeps them apart. With no ``--db`` this study's
state directory IS the batch runner's checkpoint directory -- so if the two
prefixes ever collided, a study and a batch run would refuse each other while
guarding entirely different things.
"""


def ablation_run_lock_path(db_path=None) -> str:
    """Where the run lock for this study's state lives.

    THE KEY IS THIS PROGRAM'S AND STAYS HERE -- the study's checkpoint file, so
    that a ``--db`` study and a production study lock independently exactly as
    they checkpoint independently. The derivation from a key to a lock file --
    the temp directory, the ``realpath``, the sha256, the truncation -- is
    ``control.lock_file_path``, which is where the argument for each of those
    now lives, including why ``abspath`` was two locks for one checkpoint.
    """
    return control.lock_file_path(LOCK_FILE_PREFIX,
                                  _ablation_checkpoint_path(db_path))


class AlreadyRunning(control.AlreadyRunning):
    """Another process holds the STUDY lock. Carries its record.

    A subclass of the shared class rather than the shared class itself, and the
    argument moved when ``oncotriage/control.py`` was written. What this
    docstring used to say -- that a shared class would put the whole batch
    module, its checkpoint, its ledger and its stop switch into the import graph
    of every study -- is no longer true of anything: ``control`` imports nothing
    from the project. What survives is that the refusals are raised by different
    programs, name different consequences and are remediated with different
    commands, and neither entry point catches the other's. See
    ``control.AlreadyRunning`` for the full argument.
    """


class LockUnavailable(control.LockUnavailable):
    """The STUDY lock could not be ATTEMPTED. Carries the path and the errno.

    A DIFFERENT FINDING FROM ``AlreadyRunning`` AND NOT A SUBCLASS OF IT -- they
    are siblings under ``control``'s two base classes, so ``except
    LockUnavailable`` cannot catch a held lock and vice versa. That one means
    another study holds the lock, which is benign and self-clearing; this means
    the lock file could not be opened at all, and waiting does not fix it.

    Both bases are ``RuntimeError`` and deliberately NOT ``OSError``; the whole
    argument is at ``control.LockUnavailable``, including why the conversion
    happens at the acquisition site rather than in the entry point's guard.
    """


def lock_unavailable_lines(exc) -> list:
    """The diagnosis, as the lines the entry point prints. One text, one caller.

    A FUNCTION RATHER THAN A BLOCK IN THE GUARD, on ``run_lock_refusal_lines``'
    footing: drivable by a test without arranging an unopenable path in a
    subprocess.

    THE MECHANICAL HALF IS ``control.lock_unavailable_lines`` -- the symbolic
    errno (``13`` is a number an operator looks up, ``EACCES`` is the thing they
    already know), the ``at:`` line when the failing filename differs, the
    causes list and the nothing-was-billed line. What is passed in is the half
    that is not: what running WITHOUT the guarantee would cost a study.
    """
    return control.lock_unavailable_lines(
        exc,
        header="[Ablation] REFUSING TO RUN: the study lock could not be taken.",
        consequence=[
            "        This is NOT 'another study holds the lock' -- that is a "
            "different",
            "        refusal with a different exit code. The lock file could "
            "not be",
            "        opened at all, so this study cannot establish that it is "
            "the only",
            "        one, and running without that guarantee is how two "
            "studies split",
            "        one configuration's sample between two ablation_runs "
            "rows.",
        ])


@contextlib.contextmanager
def exclusive_run_lock(path=None, db_path=None):
    """Hold an exclusive, non-blocking flock for the duration of the block.

    Yields the lock file's path. The mechanism -- the 0700 directory, the
    ``O_NOFOLLOW`` open, the non-blocking flock, the UTC record written only
    after the lock is held, the kernel release -- is
    ``control.hold_exclusive_lock``; what is decided here is this study's key,
    its two exception classes and the field its record names.

    THE DECORATOR IS NOT DECORATION. Without it ``with exclusive_run_lock():``
    raises ``AttributeError`` on a generator at the top of a paid study, and
    ``tests/test_package_invariants.py``'s decorator inventory is what makes
    that loss visible in bucket A rather than only when somebody runs one.

    WHAT WAS ACTUALLY LOCKED IS WHAT THE RECORD NAMES, resolved ONCE here. The
    batch runner's first version read its directory a second time when writing
    the record, so a caller passing an explicit path got a holder record naming
    a directory it had nothing to do with -- worse than no record, because an
    operator acts on it.
    """
    derived = path is None
    if derived:
        path = ablation_run_lock_path(db_path)
    try:
        # ``realpath``, matching the KEY. The lock is keyed on the resolved
        # path, so a record naming the unresolved one could show an operator a
        # different string from the one the refused study derived its digest
        # from -- two names for the one thing the refusal is about.
        state = os.path.realpath(str(_ablation_checkpoint_path(db_path)))
    except Exception as exc:                                    # noqa: BLE001
        state = f"<unresolved: {type(exc).__name__}: {exc}>"
    with control.hold_exclusive_lock(
            path,
            already_running=AlreadyRunning,
            lock_unavailable=LockUnavailable,
            record_key="checkpoint",
            record_value=state,
            # ONLY WHEN WE DERIVED THE PATH. A caller who named the lock file
            # directly owns its directory; creating one under a path this
            # function was handed would be a side effect nobody asked for.
            ensure_directory=derived) as held:
        yield held


def run_lock_refusal_lines(exc) -> list:
    """The refusal, as the lines the entry point prints. One text, one caller.

    A FUNCTION RATHER THAN A BLOCK IN THE GUARD so it can be driven by a test
    without starting two processes, and so the entry point's ``__main__`` block
    stays what this project's rule says it is.
    """
    return control.already_running_lines(
        exc,
        header="[Ablation] REFUSING TO RUN: another ablation study holds the "
               "lock.",
        record_keys=("pid", "host", "user", "started", "checkpoint", "record"),
        key_width=12,
        body=[
            "",
            "        Two studies against one checkpoint both read the same "
            "resume",
            "        state at start, so both run the SAME (config, patient) "
            "pairs at",
            "        one live Stage 5 call each -- and the loser's completions "
            "are",
            "        then dropped from the checkpoint by the winner's next "
            "write, so",
            "        a third run re-bills those too.",
            "",
            "        Worse, generate_summary() reports the LATEST run per "
            "config: two",
            "        interleaved studies split each config's sample between "
            "two rows,",
            "        so the configs end up compared over different patient "
            "sets.",
            "",
            "        Wait for the other study, or stop it cleanly:",
            f"            touch {describe_ablation_stop_switch_path(None)}",
            "",
            "        NOTHING HAS BEEN RUN AND NOTHING HAS BEEN BILLED.",
        ])


# ===========================================================================
# THE OPERATOR STOP SWITCH
# ===========================================================================
#
# WHY A STUDY NEEDS ONE AT ALL. 7 configs x 75 patients is 525 live Stage 5
# calls and 3-5 hours; "stop cleanly, I will resume" is an ordinary operational
# request and the two ways to make it were both wrong for the reasons
# oncotriage/batch/runner.py's own STOP SWITCH section records -- Ctrl-C needs a
# terminal the process is attached to, and SIGTERM is deliberately abrupt.
#
# IT IS HONOURED AT TWO GRANULARITIES, AND BETWEEN-CONFIGS ALONE WOULD BE A
# USELESS SWITCH. A configuration at the default sample size is 75 live calls
# and roughly half an hour; at the documented full size it is one seventh of a
# 3-5 hour study. An operator who stops a study to rebuild the index, or because
# the model is being repointed, is asking for the run to stop -- not to keep
# spending for another half hour and then stop. So the switch is polled BETWEEN
# PATIENTS, at the same cadence the checkpoint is written, exactly as the batch
# runner polls between patients; the between-configs check above the loop is
# what turns a stop noticed during config 3 into "configs 4-7 are not started"
# rather than seven more banners.
#
# THE SENTINEL IS PER DATABASE, DERIVED FROM THE CHECKPOINT PATH, and both
# halves matter:
#
#   * PER DATABASE, because the checkpoint is (pass 20f-3). A --db study and a
#     production study share a directory by default but not a resume state, and
#     a stop asked of one must not stop the other -- nor leave a stale sentinel
#     that refuses it.
#   * DERIVED, so the sentinel and the state it stops cannot drift apart. There
#     is one owner of where a study's state lives and this reads it.
#
# AND ITS NAME IS NOT `STOP`. With no --db this directory IS the batch runner's
# checkpoint directory, whose sentinel is `STOP`. Sharing the name would make
# `touch STOP` stop both harnesses -- which sounds like a feature and is two
# defects: the batch runner's stale-sentinel refusal would fire for a request
# made of a different program and name `25- Batch Runner.py --clear-stop` as the
# fix, and an operator resuming a batch run with --clear-stop would silently
# withdraw an ablation stop nobody had withdrawn. One fact, two owners, no error
# when they disagree.

ABLATION_STOP_SUFFIX = "_STOP"
"""Appended to the checkpoint's stem to name the sentinel.

Upper case so a directory listing says which file is state and which is a
control, and no extension for the same reason. With no --db this is
``ablation_checkpoint_STOP``; with ``--db /tmp/x/foo.db`` it is
``/tmp/x/foo_checkpoint_STOP``.
"""


def ablation_stop_switch_path(db_path=None) -> Path:
    """THE ONE OWNER of where this study's stop sentinel lives.

    ``_ablation_checkpoint_path``'s shape and derived from it, for a sharper
    version of that function's reason: an operator creates this file BY HAND,
    and every message that tells them where to put it -- the run banner, the
    lock refusal, the stale-sentinel refusal, the stop announcement, --help --
    has to name the same path. Two expressions of it is an operator writing a
    file the study never reads, which looks exactly like a switch that does not
    work.
    """
    cp = _ablation_checkpoint_path(db_path)
    return cp.with_name(cp.stem + ABLATION_STOP_SUFFIX)


def describe_ablation_stop_switch_path(db_path=None) -> str:
    """The sentinel's path as a string, or a description when it cannot resolve.

    A RENDERER OVER ``ablation_stop_switch_path()``, NOT A SECOND EXPRESSION OF
    IT -- the path still has one owner; this only decides what to PRINT when
    asking the owner would raise.

    IT EXISTS BECAUSE TWO OF ITS CALLERS ARE ON SHUTDOWN PATHS. With no --db the
    path resolves ``paths.checkpoint_path``, which globs the sibling data tree
    on first read and RAISES on a machine that does not have it -- so an
    interrupt arriving before anything had resolved a path would have had this
    message raise INSIDE the handler that was trying to explain the interrupt.
    An exception in an exception handler on a shutdown path is the one place a
    helpful message must not be able to fire from.
    """
    try:
        return str(ablation_stop_switch_path(db_path))
    except Exception as exc:                                    # noqa: BLE001
        return (f"<the {ABLATION_STOP_SUFFIX.lstrip('_')} file beside this "
                f"study's checkpoint; its path could not be resolved here: "
                f"{type(exc).__name__}>")


STOP_SWITCH_FAULTS = Counter()
"""Stop-switch faults, keyed ``{phase}:{ExceptionType}``.

Module-level, following ``CHECKPOINT_WRITE_FAILURES`` and ``CHECKPOINT_FAULTS``
above rather than becoming a column: this is a property of the STUDY's control
files, and an ablation_results row is the wrong place for it.

A SEPARATE COUNTER from the batch runner's of the same name, for
``CHECKPOINT_FAULTS``' stated reason: the two describe different files, and one
number covering both would report a batch fault and a study fault as one
finding.

Phases:
    ``poll:``      the existence check itself raised. The switch is NOT tripped
                   by one of these -- see ``_AblationStopSwitch.poll``.
    ``message:``   the file existed and its text could not be read. The switch
                   IS tripped; only the note is lost.
    ``preflight:`` the start-of-study check could not be made.
    ``clear:``     ``--clear-stop`` could not remove it.
"""



class StaleAblationStopSwitch(RuntimeError):
    """The stop sentinel was already present before the study began.

    A ``RuntimeError`` subclass and deliberately not a ``ValueError`` or an
    ``OSError``, on ``UnknownModelPricingError``'s and
    ``IndexVerificationError``'s precedent: a stray ``except OSError`` around a
    path check must not be able to eat a refusal.
    """




class _AblationStopSwitch(control.StopSwitch):
    """The ablation study's stop switch. ONE THING IS DECIDED HERE: the path.

    Everything else -- the latch, the lock, the "a poll that raises does not
    trip the switch" direction, the fault phases, the announcement written
    outside the lock -- is ``control.StopSwitch``, which is where each of those
    arguments now lives.

    THE PATH IS BOUND AT ``arm()`` RATHER THAN RESOLVED PER POLL, which is the
    opposite of what the batch runner does and is right for both. This
    sentinel's location depends on ``--db`` and the poll runs on MAX_WORKERS
    done-callbacks; ``main()`` has already resolved it for the banner, so
    binding it there means the path an operator was TOLD to write and the path
    the study watches are one reading rather than two. The batch runner's is a
    fixed name in a fixed directory, so resolving it per poll costs one call to
    an owner that is already the single source of truth.

    Binding is therefore the INHERITED ``_resolve_path``: ``control.StopSwitch``
    holds ``_armed_path`` and returns it, and an unarmed switch never trips --
    which is not a silent skip, because ``main()`` arms it before the first
    billed call and the entry point's preflight has already asked the same
    question, so an unarmed switch here means a caller that is not ``main()``.
    """

    def __init__(self):
        super().__init__(
            STOP_SWITCH_FAULTS,
            unit="(config, patient) pair",
            subject="this study",
            # "Noticed between configurations." -- this study names a MOMENT,
            # so it takes no article. The batch runner names a PASS ("the run",
            # "the resample pass") and passes "during the ".
            noticed_prefix="",
            banner_width=70,
            default_where="study")

    # `console.out` and `log.warning` are looked up HERE, at call time, rather
    # than captured in the constructor -- see `control.StopSwitch._emit` for the
    # measurement that made that the shipped shape.
    def _emit(self, line=""):
        if line:
            console.out(line)
        else:
            console.out()

    def _warn(self, message, **fields):
        log.warning(message, **fields)


def _read_stop_message(path) -> str:
    """The operator's note, capped, or None. NEVER RAISES.

    A THIN BINDING OF ``control.read_stop_message`` TO THIS STUDY'S COUNTER, and
    the binding is the whole of what is decided here: the bounded read, the tail
    probe and the truncation guard are one implementation in ``control`` because
    they were written twice and hardened twice, while ``STOP_SWITCH_FAULTS``
    stays per program because a batch fault and a study fault are different
    findings and one number covering both would report them as one.
    """
    return control.read_stop_message(path, STOP_SWITCH_FAULTS)


STOP_SWITCH = _AblationStopSwitch()
"""The one instance. See ``control.StopSwitch`` for why it is module-level and
reset, and ``_AblationStopSwitch`` above for the one thing this study decides."""


def clear_ablation_stop_switch(db_path=None) -> str:
    """Delete the sentinel. Returns a ``control.STOP_CLEAR_*`` member. Used by
    ``--clear-stop``.

    A SEPARATE GESTURE FROM ``--fresh-start`` AND NOT FOLDED INTO IT, because
    the two clear opposite things: ``--fresh-start`` discards the RESUME STATE
    and re-bills every (config, patient) pair, and this discards a CONTROL FILE
    and costs nothing. An operator resuming a stopped study wants exactly this
    and must not be within one flag of the other.

    THE MECHANISM IS ``control.clear_stop_switch`` -- it never raises, catches
    ``Exception`` rather than ``OSError`` (``ablation_stop_switch_path`` itself
    raises a plain ``RuntimeError`` when the sibling data tree cannot be
    globbed), counts under ``clear:`` and re-describes the path rather than
    referencing a name that may never have been bound. What is supplied here is
    this study's four facts: where its sentinel is for THIS ``--db``, how to
    describe it, whose counter to charge, and what a permission error usually
    means for a study's state directory.
    """
    return control.clear_stop_switch(
        lambda: ablation_stop_switch_path(db_path),
        lambda: describe_ablation_stop_switch_path(db_path),
        STOP_SWITCH_FAULTS,
        unit="pair", out=console.out,
        remediation="[STOP]   A permission error here usually means the state "
                    "directory is read-only or owned by another user; `ls -ld` "
                    "it.")


def report_stop_switch_faults(out=None) -> bool:
    """STOP_SWITCH_FAULTS' end-of-study reader. True when it had something to say.

    `report_checkpoint_faults`' shape and its reason: this module's counters are
    excluded from `oncotriage/degradation.py`'s registry by name, so the
    registry's rule -- every counter has a production reader -- is met here, at
    the end of the study's own `main()`.

    THE PHASES ARE REPORTED SEPARATELY BECAUSE THEY POINT OPPOSITE WAYS. A
    `poll:` key means the study MAY HAVE KEPT GOING through a stop request; a
    `clear:` key means an operator asked to RESUME and the sentinel is still
    there. Summing them into one number would give an operator a count with no
    direction.
    """
    emit = console.out if out is None else out
    if not STOP_SWITCH_FAULTS:
        return False
    emit(f"  Stop switch:     {sum(STOP_SWITCH_FAULTS.values())} fault(s) "
         f"{dict(STOP_SWITCH_FAULTS)}")
    if any(k.startswith("poll:") or k.startswith("preflight:")
           for k in STOP_SWITCH_FAULTS):
        emit("                   a poll:/preflight: key means the sentinel "
             "could not be READ, so this")
        emit("                   study may have run through a stop somebody "
             "asked for")
    if any(k.startswith("clear:") for k in STOP_SWITCH_FAULTS):
        emit("                   a clear: key means --clear-stop could not "
             "REMOVE it, so the next")
        emit("                   study will refuse to start until it is "
             "deleted by hand")
    if any(k.startswith("message:") for k in STOP_SWITCH_FAULTS):
        emit("                   a message: key means only the operator's "
             "note was lost; the stop")
        emit("                   itself was honoured")
    return True


def assert_no_stale_ablation_stop_switch(db_path=None) -> None:
    """Refuse to start while the sentinel is already there.

    Raises:
        StaleAblationStopSwitch: it is present.

    WHY THIS IS A REFUSAL AND NOT A NO-OP. Without it the switch is a trap that
    fires once and then silently every time: the study that honoured it leaves
    the file behind (deliberately -- see ``clear_ablation_stop_switch``), so the
    NEXT invocation would trip on its first completed pair, cancel the rest, and
    report a study that stopped for a reason nobody asked for that day. On a
    cron entry or a restart loop that is a comparison that never completes while
    every run reports success.

    Stopping BEFORE the first pair rather than after one is what makes the
    message actionable: nothing has been billed, nothing has been written, and
    the fix is one ``rm``.

    A CHECK THAT RAISES IS NOT COUNTED AS A STOP -- ``_AblationStopSwitch.poll``'s
    direction, for its reason -- with the failure counted under ``preflight:``.
    """
    try:
        path = ablation_stop_switch_path(db_path)
        present = path.exists()
    except Exception as exc:                                    # noqa: BLE001
        STOP_SWITCH_FAULTS[f"preflight:{type(exc).__name__}"] += 1
        console.out(f"[STOP] WARNING: the stop switch could not be checked "
                    f"({type(exc).__name__}: {exc}). Continuing.")
        return
    if not present:
        return

    note = _read_stop_message(path)
    _db_arg = "" if db_path is None else f" --db {db_path}"
    raise StaleAblationStopSwitch("\n".join(
        [f"REFUSED (stop switch present): {path}",
         "    A stop sentinel is already beside this study's checkpoint, so "
         "this run would stop again at its first completed (config, patient) "
         "pair -- for a request that was made before it started.",
         f"    Note in the file: {note}" if note else
         "    The file is empty, which is the ordinary `touch` form.",
         "    NOTHING HAS BEEN RUN AND NOTHING HAS BEEN BILLED.",
         "    To run: delete it and start again.",
         f"        rm {path}",
         f"        python \"26- Ablation Study.py\"{_db_arg}",
         "    or, in one command:",
         f"        python \"26- Ablation Study.py\" --clear-stop{_db_arg}"]))


class _PairCancelled(RuntimeError):
    """A (config, patient) pair was never started because the switch tripped.

    ``oncotriage/batch/runner.py`` raises ``concurrent.futures.CancelledError``
    at the equivalent point; THIS FILE MAY NOT, and the difference is forced by
    ``_on_done``. That callback catches ``Exception`` and counts what it catches
    as a run ERROR -- and ``CancelledError`` is a ``BaseException`` subclass in
    Python 3.8+, so it would travel straight PAST the callback's handler, out of
    ``future.result()`` in the drain loop, and end the study by exception with
    the parent tracking run recorded FAILED. A pair nobody ran is not a study
    that failed.

    So it is an ``Exception``, and ``_on_done`` is taught to recognise it and
    count it as CANCELLED rather than as an error -- which is the same
    distinction the batch runner's ``_on_done`` already draws, arrived at from
    the other side.
    """


def _stop_reason_now():
    """Why a configuration was cut short, or None. A ``RUN_STOP_REASONS`` member.

    ONE DERIVATION, READ WHEREVER THE ANSWER IS NEEDED, so the reason stored in
    ``ablation_runs.stop_reason`` and the reason an operator is shown cannot
    disagree. It reads the two latches rather than taking an argument, for the
    same reason ``oncotriage/batch/runner.py`` derives its own once and hands
    the value to both readers: a caller that passed the wrong one would produce
    a row that is internally consistent and false.

    THE OPERATOR OUTRANKS THE BUDGET, AND THE ORDER IS A DECISION. Both latches
    can be set -- a study that reached its cap and whose operator then wrote the
    sentinel, or the reverse -- and only one word fits in the column. An
    operator stop is a request a person made and can point to; a spend stop is
    a threshold the run crossed. Reporting the second when a person had already
    asked for the first sends that person to ``config.SPEND_CAP_USD`` to explain
    a stop they themselves caused. ``oncotriage/batch/runner.py`` resolves the
    identical ambiguity the identical way.
    """
    if STOP_SWITCH.requested:
        return RUN_STOP_REASON_OPERATOR
    if not spend.SPEND_STOP.requested:
        return None
    # THE LATCH'S OWN `limit` IS READ RATHER THAN RE-DERIVED. It records which
    # of the two spend limits fired at the moment it fired; asking the ledger
    # again here would answer about NOW, and a run that tripped the call
    # ceiling and then also crossed the cap would be recorded as a budget
    # event -- which sends an operator to raise a cap over a pipeline defect.
    if spend.SPEND_STOP.limit == spend.SPEND_LIMIT_CALL_CEILING:
        return RUN_STOP_REASON_CALL_CEILING
    return RUN_STOP_REASON_SPEND_CAP


def _run_pair_unless_stopped(_process, **kwargs):
    """The submitted callable. Refuses to begin work once the switch has tripped.

    WHY THIS EXISTS WHEN CANCELLATION ALREADY DOES THE JOB. Cancellation is a
    sweep and a sweep has an edge: the switch latches inside a done-callback on
    a WORKER thread while the submit loop on the MAIN thread polls once per
    pair, so between the latch and the loop's next poll exactly ONE more future
    can be submitted -- after the sweep that would have cancelled it. A worker
    picking it up in that window starts a pair, and one live billed Stage 5
    call, after the operator asked the study to stop.

    ONE PAIR IS A SMALL BOUND AND IT IS NOT THE POINT. "No further pair is
    started" is the contract this switch is documented by, and a contract with
    an unstated edge is the class of defect this project exists to remove.

    ``STOP_SWITCH.requested`` AND NOT ``poll()``: a plain attribute read, so
    this adds no filesystem call to the hot path, and it cannot miss a stop that
    matters -- the value it reads is set by the sweep already cancelling this
    future's siblings.
    """
    if STOP_SWITCH.requested:
        raise _PairCancelled(
            "the operator stop switch tripped before this pair started")
    # THE SPEND LATCH IS READ HERE FOR THE IDENTICAL REASON AND WITH THE
    # IDENTICAL SHAPE. `spend.SPEND_STOP.requested` is a plain attribute read,
    # not `poll()`, so this adds one boolean to the hot path -- and it closes
    # the same one-pair edge: the spend latch is set inside `_on_done` on a
    # worker thread, and a pair submitted between that and the submit loop's
    # next poll would otherwise start and issue a live billed Stage 5 call
    # after the budget was gone.
    #
    # IT IS A SEPARATE MESSAGE, NOT A SHARED ONE. `_PairCancelled` is counted
    # into `run_cancelled` either way, but the string reaches the operator's
    # console and "the operator stop switch tripped" is false of a study
    # nobody touched.
    if spend.SPEND_STOP.requested:
        raise _PairCancelled(
            "a spend limit was reached before this pair started")
    return _process(**kwargs)


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

# ===========================================================================
# THE PER-CONFIGURATION RUN STATUS
# ===========================================================================
#
# `ablation_runs` HAD NO STATUS COLUMN AND NO STATUS CONVENTION AT ALL, and
# that was measured before this was written rather than assumed: the table is
# (id, run_timestamp, config_name, config_description, sample_size,
# total_time_seconds) and `_finalize_run` set the last of those and nothing
# else. So the brief's "per the ablation database's own status conventions"
# named something that did not exist, and this is it -- built on the one this
# project already has, `oncotriage/storage/database_logger.py`'s
# RUN_RECORD_TERMINAL_STATUSES.
#
# WHY A STATUS IS LOAD-BEARING HERE AND NOT MERELY TIDY. generate_summary()
# reports the LATEST ablation_runs row per config_name and joins its results by
# run_id. A configuration cut short -- by a stop, by Ctrl-C, by SIGTERM --
# leaves a row that IS the latest for that config and whose results are a
# PREFIX of the sample. Every average computed over it is an average over
# however many patients happened to run before the operator stopped, presented
# beside the other configurations' full-sample averages as though the two were
# comparable. That is precisely what STOPPED means in
# `oncotriage/batch/runner.py`: "this covers a PREFIX of the cohort, so no rate
# computed over it is a rate about the cohort". Without the column the database
# cannot say which rows those are.
#
# THE VOCABULARY IS CLOSED AND A CALLER MAY BRANCH ON IT EXHAUSTIVELY.

RUN_STATUS_RUNNING = "RUNNING"
RUN_STATUS_COMPLETE = "COMPLETE"
RUN_STATUS_STOPPED = "STOPPED"
RUN_STATUS_KILLED = "KILLED"

RUN_STATUSES = (RUN_STATUS_RUNNING, RUN_STATUS_COMPLETE, RUN_STATUS_STOPPED,
                RUN_STATUS_KILLED)
"""Every value `ablation_runs.status` can hold. NULL is not a member.

    RUNNING   `_create_run` wrote it and nothing has finalized it. A row left
              this way is a configuration whose process did not get to run a
              handler -- SIGKILL, a power loss, a `docker kill`. It is the
              same shape, and the same admission, as a `runs` row left RUNNING
              with a NULL finished_at in `oncotriage/storage/database_logger.py`.
    COMPLETE  every pending pair of this configuration ran.
    STOPPED   the operator stop sentinel cut it short. The results under this
              run_id are a PREFIX of the sample.
    KILLED    Ctrl-C or SIGTERM cut it short. Same prefix warning, different
              gesture -- and, unlike STOPPED, the pairs in flight were failed
              rather than finished, because both abrupt paths ask Stage 5 to
              stop issuing requests.

NULL MEANS THE ROW PREDATES THIS COLUMN, and nothing is backfilled. Writing
COMPLETE into historical rows would assert that every one of them finished,
which is false of any study that was ever interrupted -- and those are exactly
the rows a reader most needs to distrust. `_summary_status_warning` reports NULL
as "not recorded", separately from the three it can read.

`STOPPED` AND `KILLED` ARE BOTH PREFIXES AND ARE STILL SEPARATE MEMBERS,
because the operator's next command differs: a stop is resumed by deleting the
sentinel, an interrupt by running the same command again. That is the argument
`oncotriage/batch/runner.py` makes for keeping the two apart in `runs.status`,
and the argument `readiness.probe_index` makes for `empty` vs `absent`.
"""

RUN_STATUSES_PARTIAL = (RUN_STATUS_RUNNING, RUN_STATUS_STOPPED,
                        RUN_STATUS_KILLED)
"""The members whose results are NOT a whole sample. Derived from the tuple
above rather than retyped, one line down, so a member added to one and not the
other fails `tests/test_ablation_stop_and_lock.py` rather than silently being
treated as complete.
"""

RUN_STOP_REASON_OPERATOR = "operator"
RUN_STOP_REASON_SPEND_CAP = "spend_cap"
RUN_STOP_REASON_CALL_CEILING = "call_ceiling"

RUN_STOP_REASONS = (RUN_STOP_REASON_OPERATOR, RUN_STOP_REASON_SPEND_CAP,
                    RUN_STOP_REASON_CALL_CEILING)
"""Why a configuration was cut short, stored in `ablation_runs.stop_reason`.
CLOSED, and NULL is the fourth reading rather than a fourth member.

**A COLUMN AND NOT TWO MORE STATUSES**, which is the ruling
`oncotriage/storage/database_logger.py:RUN_STOP_REASONS` already made for the
`runs` table, adopted here rather than re-argued: `status` answers HOW a
configuration ended, and a spend stop's answer is byte-identical to an
operator stop's -- every pair it started finished and was written, pairs remain
it never began, the checkpoint is intact, a resume continues. Two more members
would be two more things `RUN_STATUSES_PARTIAL`, `_summary_status_warning` and
`tests/test_ablation_stop_and_lock.py` must learn, all of which would answer
identically for all three.

NULL MEANS ONE OF TWO THINGS AND BOTH ARE HONEST: the row predates this column,
or the configuration was not cut short at all. A reader separates them with
`status`, which is what it is for -- a COMPLETE row with a NULL stop_reason ran
its whole sample, and a STOPPED row with a NULL stop_reason was written before
the column existed. There is deliberately no `not_stopped` member: it would be
a value asserting the absence of an event, on every COMPLETE row ever written,
to save a reader one column.
"""


def init_ablation_db(db_path=None):
    """Create ablation database tables (idempotent).

    Args:
        db_path: Database to create the tables in. ``None`` means the
            production ``ablation_results.db`` -- see ``ablation_db()``.
    """
    conn = open_connection(str(ablation_db(db_path)))

    # THE JOURNAL MODE IS SET HERE AND NOWHERE ELSE, exactly as
    # oncotriage/storage/database_logger.py sets it inside its own
    # initialize_database, and for the same reason: it is a property of the
    # FILE, so one successful application converts the database permanently and
    # every later connection inherits it. This function is the only one in this
    # module that runs before the pool exists, which is what makes it the right
    # place.
    #
    # IT COUNTS INTO THE SAME JOURNAL_MODE_DEGRADATIONS the inference writer
    # uses; the counter's meaning is "a database this process writes is not in
    # the mode that was asked for", and which database is in the key.
    apply_journal_mode(conn, str(ablation_db(db_path)))

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

    # `status` IS AN ADDITIVE COLUMN AND IS DELIBERATELY NOT IN THE CREATE
    # TABLE ABOVE, on the precedent `oncotriage/storage/database_logger.py`
    # sets for `runs.matching_call_mode`: a column named in both places is
    # added twice to a fresh database and reports `duplicate column name` at
    # the first INSERT of every new study. Named once, in the migration, so a
    # fresh database and a migrated one end up with the identical physical
    # column order -- which is what makes the two indistinguishable to every
    # reader.
    #
    # NO DEFAULT, AND NOT `NOT NULL`. A DEFAULT would fill every historical row
    # with a status nobody measured; NOT NULL cannot be added to a populated
    # table by ALTER at all. NULL is the honest value for a row written before
    # the column existed, and `_summary_status_warning` reads it as exactly
    # that.
    _run_columns = {row[1] for row in c.execute("PRAGMA table_info(ablation_runs)")}
    if "status" not in _run_columns:
        c.execute("ALTER TABLE ablation_runs ADD COLUMN status TEXT")
        console.out("Schema migration: added ablation_runs.status")
        console.out("  Existing rows left NULL (not measured, not COMPLETE): "
                    "a study that was interrupted before this column existed "
                    "is exactly the row a reader must not be told finished")

    # `stop_reason` IS ADDITIVE FOR `status`'s REASONS, WORD FOR WORD, and it
    # is a SECOND migration rather than a second name in the same `if`: a
    # database migrated by the pass that added `status` has that column and not
    # this one, so testing for either would leave one of them un-added.
    if "stop_reason" not in _run_columns:
        c.execute("ALTER TABLE ablation_runs ADD COLUMN stop_reason TEXT")
        console.out("Schema migration: added ablation_runs.stop_reason")

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

    # ── THE TWO ACCESS PATHS `ablation_results` IS ACTUALLY READ BY ─────────
    #
    # AFTER the column migrations, for the reason the inference indexes are:
    # CREATE INDEX on a column that does not exist yet is an error, not a no-op.
    # Both are IF NOT EXISTS, so re-opening an existing study database adds only
    # what is missing.
    #
    #   run_id -- `oncotriage/ablation/analysis.py:load_ablation_data` JOINs
    #     every result row to `ablation_runs` on it, and `generate_summary`'s
    #     table joins the same way through _LATEST_RUN_PER_CONFIG_SQL. It is the
    #     column the whole analysis side reads through.
    #   (config_name, patient_id) -- COMPOSITE, and in that order, because the
    #     question this table is asked is "did this configuration already do
    #     this patient": the checkpoint's membership is exactly that pair, and
    #     `generate_summary` groups by config_name alone, which the same index
    #     serves as a leftmost prefix. The reverse order would serve neither.
    #
    # NO INDEX ON `patient_id` ALONE, and that is the same ruling the inference
    # table's absent nct_id index records: every insert maintains every index,
    # and a patient_id-only lookup is not a question anything here asks -- a
    # patient always appears with a configuration. The composite's leftmost
    # prefix is `config_name`, so a patient_id-only scan is NOT served by it;
    # that is stated rather than glossed, and it is the cost of choosing this
    # order.
    #
    # NOT MEASURED AT SCALE, and said plainly rather than borrowed from the
    # inference measurement. A study is ABLATION_SAMPLE_SIZE patients times
    # seven configurations -- hundreds of rows, not tens of thousands -- so
    # neither index will be observable in a timing today. They are here because
    # the JOIN and the pair lookup are the only two ways this table is read,
    # because the write cost of an index on a table written once per patient
    # per configuration is not measurable either, and because an index added
    # when the table is small is an index that is already there when it is not.
    c.execute("CREATE INDEX IF NOT EXISTS idx_ablation_results_run_id "
              "ON ablation_results(run_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ablation_results_config_patient "
              "ON ablation_results(config_name, patient_id)")

    conn.commit()
    conn.close()
    console.out(f"Ablation database: {ablation_db(db_path)}")


RUN_RECORD_FAILURES = Counter()
"""`ablation_runs` bookkeeping writes that did not land, keyed
``{phase}:{ExceptionType}`` -- ``finalize:`` only, today.

WHY `_finalize_run` STOPPED RAISING, WHICH IS A BEHAVIOUR CHANGE STATED AS ONE.
It ran the study's LAST database write before the summary, so a raise out of it
propagated to `main()`'s `except BaseException`, recorded the parent tracking
run FAILED and re-raised -- destroying the summary, the JSON export and the
checkpoint clear, AFTER every live Stage 5 call of the configuration had already
been paid for. The column it writes is read by nothing that decides anything
(`total_time_seconds` appears in no query; `status` is a warning line), so
crashing a finished study over it buys nothing and costs the artifacts the study
exists to produce.

IT IS ALSO A CORRECTNESS REQUIREMENT ON THE SHUTDOWN PATHS. The stop and the
interrupt both finalize the configuration they cut short, and both run inside a
handler whose job is to leave a record. A raise there would replace the record
with a traceback -- an exception inside an exception handler on a shutdown path,
which `describe_ablation_stop_switch_path` exists one function up to prevent.

`oncotriage/storage/database_logger.py:finalize_run_record` is the precedent,
including "never raises" meaning `except Exception` -- so `KeyboardInterrupt`
and a second SIGTERM still escape, exactly as they escape `_write_inference_row`.
A finalizer that swallowed a Ctrl-C would leave an operator holding the key down
against a process that will not stop.

THERE IS NO `create:` KEY AND ITS ABSENCE IS NOT AN OMISSION. `_create_run`
raises, and the argument for that is `_create_run`'s own -- read it there. It is
NOT `start_run_record`'s "this runs before any spend", which is true of the
batch runner (one row, opened once, before the first patient) and FALSE HERE:
`_create_run` is called once per CONFIGURATION, inside the loop, so by
configuration 3 of 7 two whole configurations of live Stage 5 calls have already
been billed. What makes raising affordable is the CHECKPOINT, not the position.
"""


def report_run_record_failures(out=None) -> bool:
    """RUN_RECORD_FAILURES' end-of-study reader. True when it had something to say.

    The study's counters are excluded from `oncotriage/degradation.py`'s
    registry by name -- importing this module there would drag the graph, the
    fixtures and the thread pool into every batch run -- so the registry's
    contract ("every counter has a reader") is met HERE, at the end of the
    study's own `main()`, which is this entry point's equivalent of that report.
    `report_checkpoint_faults` is the pattern and this is the third of its kind.

    `out` IS INJECTABLE for that function's reason: `main()` cannot be driven
    without a live Qdrant and a paid Stage 5 call per pair, so a reader nothing
    can exercise is how a reader comes to be wrong.
    """
    emit = console.out if out is None else out
    if not RUN_RECORD_FAILURES:
        return False
    emit(f"  Run records:     {sum(RUN_RECORD_FAILURES.values())} "
         f"bookkeeping write(s) did not land {dict(RUN_RECORD_FAILURES)} -- "
         f"a configuration's row may read RUNNING or carry no elapsed time "
         f"even though it finished; the RESULTS rows are unaffected")
    return True


def _create_run(config_name, config_description, sample_size, db_path=None):
    """Insert a new ablation_runs row, return run_id. RAISES on failure.

    The row opens `RUNNING`. It is finalized by `_finalize_run`, and a row still
    reading RUNNING when a study is over is a configuration whose process had no
    chance to run a handler -- see `RUN_STATUSES`.

    WHY THIS RAISES WHERE `_finalize_run` DOES NOT, AND WHY THE OBVIOUS REASON
    IS THE WRONG ONE. `oncotriage/storage/database_logger.py:start_run_record`
    raises on the argument that it runs BEFORE ANY SPEND: the batch runner opens
    exactly one row, once, ahead of its first patient, so a failure there costs
    a run that had not started. THAT ARGUMENT DOES NOT TRANSFER TO THIS
    FUNCTION and was, until this was corrected, restated here as though it did.
    This is called ONCE PER CONFIGURATION, from inside `main()`'s loop, so on
    configuration 3 of 7 it runs with two whole configurations of live Stage 5
    calls already billed. "A failure here costs nothing" is false of every call
    but the first.

    THE CASE IT IS ACTUALLY IN IS PER-CONFIGURATION AND RESUME-COVERED, and
    that is what makes raising the right choice anyway:

    * WHAT A RAISE COSTS IS THE REST OF THE STUDY, NOT THE MONEY ALREADY SPENT.
      Every (config, patient) pair that completed is in the checkpoint, written
      by `_on_done` as it completed. The raise reaches `main()`'s outer
      `except BaseException`, which does NOT clear the checkpoint and does not
      regenerate the summary -- Step 5 is outside the `try` for exactly that
      reason -- so `--summary-only` still reads what ran and a resume re-runs
      only what did not. Nothing is re-billed. That is the same protection
      `main()` already relies on for a Ctrl-C.
      `open_run_id` is None at this point (it is assigned from this function's
      RETURN, and cleared when the previous configuration was finalized), so
      that handler finalizes nothing and leaves no configuration reading
      RUNNING that never opened.
    * WHAT SWALLOWING WOULD COST IS UNBOUNDED AND UNRECOVERABLE. The
      counterfactual is this function returning None on failure the way
      `_finalize_run` returns False: the configuration's whole results set
      would then be written with `run_id` NULL or 0, pointing at no run.
      `ablation_results` DECLARES `FOREIGN KEY (run_id) REFERENCES
      ablation_runs(id)` and nothing in this module issues
      `PRAGMA foreign_keys = ON` -- SQLite leaves it off per connection -- so
      the declaration refuses nothing and every row lands. `generate_summary`'s
      INNER JOIN against `_LATEST_RUN_PER_CONFIG_SQL` then silently omits all
      of them: a configuration that ran, cost money and produced rows would be
      ABSENT from the table with nothing saying so, which is strictly worse
      than a study that stopped and said why.

    So the line is drawn in the same PLACE as `start_run_record`'s, for a
    different reason, and the difference is written down because a reader who
    borrows this function's disposition without its argument gets the wrong
    answer for a caller that is not in a loop.
    """
    def _insert():
        # THE WHOLE WRITE, CONNECT INCLUDED, because run_with_write_retry calls
        # this again from scratch on a retry: a connection whose transaction was
        # rolled back by a contention error is not a connection to reuse.
        conn = open_connection(str(ablation_db(db_path)))
        try:
            c = conn.cursor()
            c.execute(
                "INSERT INTO ablation_runs "
                "(run_timestamp, config_name, config_description, sample_size, "
                " status) "
                "VALUES (?, ?, ?, ?, ?)",
                (datetime.now().isoformat(), config_name, config_description,
                 sample_size, RUN_STATUS_RUNNING),
            )
            run_id = c.lastrowid
            conn.commit()
            return run_id
        finally:
            # IN A `finally`, WHICH IT WAS NOT. The old form closed the
            # connection on the success path only, so a raise anywhere in the
            # INSERT leaked it -- and this function RAISES by design (see the
            # docstring), so the leak was on the path that is taken.
            conn.close()

    with _ablation_db_lock:
        return run_with_write_retry(_insert, "the ablation run row")


def ablation_spend_before(db_path=None):
    """What prior studies against THIS database already spent. Never raises.

    Returns a ``spend.LedgerSeed``.

    **THIS IS THE STUDY'S CAMPAIGN, AND THE DATABASE IS WHAT DEFINES IT.** The
    batch runner walks the ``runs`` chain backwards over identical fingerprint
    columns because several PROCESSES contribute to one campaign there. A study
    has no such chain and needs none: pass 20f-3 made the checkpoint follow
    ``--db``, so "the work this database already holds" and "the work this
    resume will skip" are the same set by construction -- which is exactly the
    quantity a resumed study's budget must not be charged for again, and
    exactly the quantity it must not be handed for free.

    IT SUMS EVERY ROW RATHER THAN THE CHECKPOINTED ONES. A row exists because a
    pair was run and BILLED; the checkpoint is a record of what was written,
    and the two can differ by a pair whose write failed. Summing the rows counts
    money that was spent, which is the question; summing the checkpoint would
    count money that was spent AND recorded, which is a smaller number and the
    under-enforcing direction.

    A NULL COST MAKES THE ANSWER A FLOOR and ``LedgerSeed.is_floor`` says so --
    ``estimated_cost_usd`` is ``REAL DEFAULT 0``, so a row written before
    pricing existed reads 0 and is indistinguishable from a pair that genuinely
    cost nothing. That ambiguity is inherited from the column's own DEFAULT and
    is reported rather than papered over: the NULL count below is exact, and the
    zeros are not separable.

    NEVER RAISES. It runs before the first billed call of the study, where a
    fresh database, an absent table and an unreadable file are all ordinary --
    and a study refusing to start because its own resume history could not be
    read would be a brake stopping a run it has nothing to say about. An
    unreadable history yields a FRESH seed, which is the OVER-spending
    direction, and that is stated rather than hidden: it is the same direction
    `LedgerSeed`'s floor already fails in, and the alternative -- refusing --
    turns a missing file into a stopped campaign.
    """
    try:
        conn = open_connection(str(ablation_db(db_path)))
    except Exception:                                           # noqa: BLE001
        return spend.LedgerSeed()
    try:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "ablation_results" not in names:
            return spend.LedgerSeed()
        row = conn.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0.0), COUNT(*), "
            "       SUM(CASE WHEN estimated_cost_usd IS NULL THEN 1 ELSE 0 END)"
            "  FROM ablation_results").fetchone()
        runs = conn.execute(
            "SELECT COUNT(*) FROM ablation_runs").fetchone()[0] \
            if "ablation_runs" in names else 0
    except Exception:                                           # noqa: BLE001
        return spend.LedgerSeed()
    finally:
        conn.close()
    if not row or not row[1]:
        return spend.LedgerSeed()
    return spend.LedgerSeed(usd=float(row[0] or 0.0), rows=int(row[1]),
                            unpriced=int(row[2] or 0), runs=int(runs or 0),
                            source=spend.SEED_SOURCE_CAMPAIGN)


def _finalize_run(run_id, elapsed_seconds, status, db_path=None,
                  stop_reason=None) -> bool:
    """Record how long the configuration took and how it ended. Never raises.

    Args:
        status: a `RUN_STATUSES` member. REQUIRED, WITH NO DEFAULT, on
            `empty_database(db_path, flag)`'s precedent: every plausible default
            is a claim. `COMPLETE` would let a caller that forgot record a
            stopped configuration as a finished one, which is the single thing
            this column exists to make impossible.

    Returns:
        True when the row was updated. False when it was not, which is counted.

    THE ROW COUNT IS READ. `UPDATE ... WHERE id = ?` against an id that is not
    there SUCCEEDS and updates nothing, and SQLite reports no error -- so a
    finalizer that did not check `rowcount` would report success for a run row
    that was never written. That is the "reported success, wrote nothing" shape
    the write-durability pass removed from `log_inference`, and this is the same
    check for the same reason.

    Args (continued):
        stop_reason: a `RUN_STOP_REASONS` member, or None. **An unrecognised
            reason is REFUSED AND COUNTED, and the row is finalized with a NULL
            reason rather than with the unrecognised one** -- the column exists
            to be grouped on, and a value outside the closed vocabulary is a
            bucket no `GROUP BY` consumer knows about. The STATUS is still
            written, because how a configuration ended is the more important of
            the two facts and must not be lost to a defect in the second.

    AN UNRECOGNISED STATUS IS REFUSED RATHER THAN STORED. A typo would put a
    value outside the closed vocabulary into a column readers branch on, and
    silently -- so it is counted and the write is skipped, leaving the row
    RUNNING, which is at least a member and at least true of a study that did
    not record how it ended. `tracking.end_run`'s substitution rule, with the
    conservative direction chosen.
    """
    if status not in RUN_STATUSES:
        RUN_RECORD_FAILURES[f"finalize:UnknownStatus({status!r})"] += 1
        console.out(f"  [Run record] refusing to store an unrecognised status "
                    f"{status!r} for run {run_id}; the row stays "
                    f"{RUN_STATUS_RUNNING}")
        return False
    if stop_reason is not None and stop_reason not in RUN_STOP_REASONS:
        RUN_RECORD_FAILURES[f"finalize:UnknownStopReason({stop_reason!r})"] += 1
        console.out(f"  [Run record] refusing to store an unrecognised stop "
                    f"reason {stop_reason!r} for run {run_id}; the row keeps a "
                    f"NULL reason and the status is still written")
        stop_reason = None
    try:
        def _update():
            conn = open_connection(str(ablation_db(db_path)))
            try:
                cur = conn.execute(
                    "UPDATE ablation_runs "
                    "SET total_time_seconds = ?, status = ?, stop_reason = ? "
                    "WHERE id = ?",
                    (round(elapsed_seconds, 2), status, stop_reason, run_id),
                )
                rowcount = cur.rowcount
                conn.commit()
                return rowcount
            finally:
                conn.close()

        with _ablation_db_lock:
            # THE RETRY IS INSIDE THIS FUNCTION'S OWN `except Exception`, which
            # is what keeps the "never raises" contract: run_with_write_retry
            # re-raises what the last attempt raised, and that handler counts it
            # into RUN_RECORD_FAILURES exactly as it counted a first-attempt
            # failure before.
            updated = run_with_write_retry(_update,
                                           "the ablation run finalization")
    except Exception as exc:                                    # noqa: BLE001
        RUN_RECORD_FAILURES[f"finalize:{type(exc).__name__}"] += 1
        console.out(f"  [Run record] could not finalize run {run_id} as "
                    f"{status}: {type(exc).__name__}: {exc}")
        return False
    if updated != 1:
        RUN_RECORD_FAILURES[f"finalize:NoSuchRun({updated})"] += 1
        console.out(f"  [Run record] finalizing run {run_id} as {status} "
                    f"matched {updated} rows, not 1")
        return False
    return True


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

    # THE `conn = None` / `finally: if conn is not None: conn.close()` PAIR IS
    # GONE, and it is not a dropped guarantee. The connection is opened and
    # closed inside `_insert` now, in its own `finally`, which is where it has
    # to be for a retry to get a fresh one -- and that is strictly tighter than
    # what it replaces: the old `finally` closed the connection only if the
    # `conn = sqlite3.connect(...)` line had been REACHED, so any raise in the
    # ~80 lines of value derivation above it left nothing to close and any raise
    # after it left the connection open for exactly as long as the handler took.
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
    
            # ── THE ROW IS BUILT FIRST, THEN WRITTEN ────────────────────────
            #
            # The SQL and the value tuple are bound to locals so the write
            # itself is a callable `run_with_write_retry` can invoke AGAIN --
            # which is the whole requirement it places on its argument. Nothing
            # in either literal moved: the SQL below is the text that was
            # inside conn.execute(), and the tuple is the one that followed it.
            #
            # BUILDING THE TUPLE OUTSIDE THE RETRY IS DELIBERATE. Every element
            # of it is a read of `result`, `timings` or a local computed above,
            # and re-evaluating those on a retry would be re-deriving the row
            # rather than re-writing it. One row, one derivation, up to
            # SQLITE_WRITE_MAX_ATTEMPTS attempts at storing it.
            _insert_sql = """
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
            """
            _row = (
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
            )

            def _insert():
                # THE CONNECT IS INSIDE, because a retry needs a fresh
                # connection: one whose transaction was rolled back by a
                # contention error is not one to reuse.
                conn = open_connection(str(ablation_db(db_path)))
                try:
                    conn.execute(_insert_sql, _row)
                    conn.commit()
                finally:
                    conn.close()

            run_with_write_retry(_insert, "an ablation result row")
    
        except Exception as e:
            console.out(f"  WARNING: Failed to log result: {e}")


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

_LATEST_RUN_PER_CONFIG_SQL = """
            SELECT config_name, MAX(id) AS run_id
            FROM ablation_runs
            GROUP BY config_name
"""
"""WHICH ``ablation_runs`` ROW IS THE LATEST FOR ITS CONFIGURATION. ONE OWNER.

TWO READERS INTERPOLATE THIS AND NEITHER RESTATES IT: ``generate_summary``,
which joins its results and averages them, and ``_summary_status_warning``,
which reads its ``status`` and qualifies those averages. They must select the
SAME row or the qualification is about a run whose numbers are not on the
table -- a warning naming a stopped configuration whose printed means came from
a different, complete run, or worse, silence over a prefix. Before this they
were two hand-written copies of one SELECT, which is the shape this project has
removed for the alias family, the cross-encoder checkpoint and the BM25 model:
nothing raises when two copies disagree.

``MAX(id)``, NEVER ``MAX(run_timestamp)``, AND THAT IS THE CORRECTION RATHER
THAN THE CONSOLIDATION. The previous form was

    WHERE (config_name, run_timestamp) IN (
        SELECT config_name, MAX(run_timestamp) FROM ablation_runs
        GROUP BY config_name)

and ``run_timestamp`` is ``datetime.now().isoformat()`` -- a NAIVE LOCAL time
written by ``_create_run``. It fails in two ways, both silent:

* EXACT TIES SELECT MORE THAN ONE ROW. ``IN`` matches every row carrying the
  maximum, so two runs of one configuration sharing a timestamp both qualify.
  ``_summary_status_warning`` then prints that configuration TWICE, once per
  status, and ``generate_summary``'s INNER JOIN admits BOTH runs' results and
  averages them together -- a mean over two runs presented as the latest run's.
  isoformat() carries microseconds, so a tie needs two inserts inside one
  microsecond; ``_create_run`` holds ``_ablation_db_lock`` across the insert,
  which serialises them but does not make the clock advance, and a coarse clock
  or a restored/copied row makes it ordinary rather than exotic.
* LOCAL TIME IS NOT MONOTONE. At the DST fall-back the wall clock repeats an
  hour, so a run started at 01:30 EDT and a later one started at 01:30 EST
  write timestamps an hour APART IN THE WRONG DIRECTION: the earlier run wins
  ``MAX``, and the summary reports a superseded run as the latest one. A study
  that spans the boundary is not unusual -- seven configurations at one live
  Stage 5 call per pair is hours.

``id`` HAS NEITHER FAULT. It is ``INTEGER PRIMARY KEY AUTOINCREMENT``, so it is
unique by construction (no tie is possible, and ``MAX`` therefore selects
exactly one row per configuration) and monotone in INSERT order within one
database, which is precisely the ordering "latest run" means. It is the same
argument ``oncotriage/storage/queries.py:campaign_summary`` records for reading
run order off ``runs.id`` rather than ``started_at``.

``run_timestamp`` IS NOT DELETED AND IS STILL WHAT AN OPERATOR READS. It is the
human-facing fact -- when did this run happen -- and it stays in the table and
in the report. What changes is that it no longer DECIDES anything.
"""


def _summary_status_warning(conn) -> list:
    """Lines naming any configuration whose LATEST run was not COMPLETE.

    Returns [] when every latest run is COMPLETE, which is the ordinary case and
    prints nothing at all -- a clean line every study trains a reader to skip it,
    which is the argument `report_checkpoint_faults` already makes.

    WHY THIS QUALIFIES THE TABLE RATHER THAN CHANGING IT. `generate_summary`
    selects the latest run per config and averages its results. A configuration
    cut short leaves the latest row covering a PREFIX of the sample, so its
    averages are over however many patients ran before the stop -- printed
    beside the other configurations' full-sample averages as if comparable.

    THE FIX IS TO NAME IT, NOT TO FILTER IT, and that is a decision with two
    halves. Filtering would change WHICH rows every historical comparison rests
    on -- silently, for every reader of this table since the study existed --
    and it would answer a partial configuration with nothing at all, which is
    strictly less information than a partial number that says it is partial.
    `oncotriage/storage/queries.py:print_cost_by_model` reached the same answer
    for the same reason and marks its total `<- A FLOOR, NOT A TOTAL`; this is
    that, for a mean.

    NULL IS ITS OWN BUCKET AND IS NOT READ AS A FAILURE. A row written before
    `ablation_runs.status` existed records nothing about how it ended -- which
    is not the same as ending badly, and not the same as COMPLETE either. It is
    reported as "not recorded" so a reader knows the question was not asked
    rather than answered.

    A PRE-EXISTING DEFECT THIS DOES NOT FIX, AND NAMES: a RESUMED configuration
    also reports on a subset. The resume creates a NEW ablation_runs row and
    logs only the pairs it actually ran, so `n` is the size of the REMAINDER
    rather than of the sample. That is a fact about `generate_summary`'s
    run_id join and it predates every control in this pass; closing it means
    changing which rows the table is built from, which is the redesign this
    function's own docstring declines. `n` is in the printed table, so a reader
    comparing it against `sample_size` can see it.
    """
    try:
        rows = conn.execute(f"""
            SELECT r.config_name, r.status
            FROM ablation_runs r
            INNER JOIN ({_LATEST_RUN_PER_CONFIG_SQL}) latest
                    ON r.id = latest.run_id
            ORDER BY r.config_name
        """).fetchall()
    except sqlite3.Error as exc:                                # noqa: BLE001
        # A DATABASE THAT PREDATES THE COLUMN CANNOT ANSWER, and that is not a
        # study failure: `no such column: status` is exactly what an
        # un-migrated file says. Reported as itself rather than swallowed --
        # the standing rule -- and never as "every configuration is complete".
        return [f"  [Status] could not be read from ablation_runs "
                f"({type(exc).__name__}: {exc}); this table says nothing about "
                f"whether any configuration was cut short"]

    partial = [(name, status) for name, status in rows
               if status in RUN_STATUSES_PARTIAL]
    unrecorded = [name for name, status in rows if status is None]
    if not partial and not unrecorded:
        return []

    lines = ["", "  " + "!" * 76]
    if partial:
        lines.append("  [Status] THESE CONFIGURATIONS' NUMBERS ARE OVER A "
                     "PREFIX OF THE SAMPLE, NOT THE SAMPLE:")
        for name, status in partial:
            lines.append(f"           {name:25s} latest run: {status}")
        lines.append("           A run that was stopped, interrupted or killed "
                     "logged results for")
        lines.append("           only the pairs it reached. Every mean above "
                     "for these configurations")
        lines.append("           is a mean over that prefix, and the deltas "
                     "against the baseline")
        lines.append("           difference two different patient sets. Resume "
                     "the study and re-run")
        lines.append("           this summary before reading them as a "
                     "comparison.")
    if unrecorded:
        lines.append("  [Status] These configurations' latest run predates "
                     "ablation_runs.status, so")
        lines.append("           whether it finished is NOT RECORDED -- which "
                     "is not the same as")
        lines.append("           COMPLETE: " + ", ".join(unrecorded))
    lines.append("  " + "!" * 76)
    return lines


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

    # READ-ONLY: this is a reader and `ablation_db`'s own docstring has said so
    # since it was written. The existence check three lines above is what an
    # operator sees; this is what makes the open unable to CREATE the file if
    # that check is ever passed or bypassed.
    conn = open_ablation_db_readonly(db_path)
    try:
        df = pd.read_sql_query(f"""
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
            INNER JOIN ({_LATEST_RUN_PER_CONFIG_SQL}) latest
                    ON r.config_name = latest.config_name
                   AND r.run_id      = latest.run_id
            GROUP BY r.config_name
        """, conn)
    finally:
        conn.close()

    if df.empty:
        console.out("No ablation results found.")
        return None

    # A SECOND, SHORT-LIVED CONNECTION rather than widening the one above.
    # That one is scoped to the single `read_sql_query` and closed in a
    # `finally` on purpose; holding one open across the whole printed report --
    # pandas formatting, a dozen console writes -- would keep a reader on the
    # study's own database for no reason while workers may still be writing it.
    # SO THE LINES ARE COMPUTED HERE AND PRINTED LATER: what crosses the report
    # is a list of strings, not a connection.
    _status_conn = open_ablation_db_readonly(db_path)
    try:
        _status_lines = _summary_status_warning(_status_conn)
    finally:
        _status_conn.close()

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

    # THE QUALIFICATION IS PRINTED BETWEEN THE TABLE AND THE DELTAS, and the
    # position is chosen rather than convenient: a reader who stops at the table
    # has already seen it, and a reader who goes on to the deltas -- which
    # difference two configurations directly and are the number most likely to
    # be quoted -- reads it immediately above them.
    for _line in _status_lines:
        console.out(_line)

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

STUDY_STATUS_COMPLETE = "COMPLETE"
STUDY_STATUS_STOPPED = "STOPPED"
STUDY_STATUS_INTERRUPTED = "INTERRUPTED"
STUDY_STATUS_CRASHED = "CRASHED"

STUDY_STATUSES = (STUDY_STATUS_COMPLETE, STUDY_STATUS_STOPPED,
                  STUDY_STATUS_INTERRUPTED, STUDY_STATUS_CRASHED)
"""How the STUDY ended, which is not the same question as how a CONFIGURATION
ended (`RUN_STATUSES`, stored in ablation_runs.status).

They are separate vocabularies because the two facts can differ: a study whose
last configuration was STOPPED is itself STOPPED, but a study that ran every
configuration to COMPLETE is COMPLETE and its rows say nothing about it. This
one is printed and never stored -- a study is not a row in this schema -- so
the two cannot be confused by a reader of the database.

`INTERRUPTED` RATHER THAN `KILLED`, and the wording is deliberate: this is the
line an operator reads on their own terminal immediately after pressing Ctrl-C,
and "KILLED" reads as something that happened TO the study. The stored
per-configuration status IS `KILLED`, because there it sits beside `runs.status`
values written by the batch runner and has to agree with them.

`CRASHED` IS SEPARATE FROM `INTERRUPTED` AND THAT IS THE POINT OF HAVING IT.
Both reach the closing block through the same `except BaseException`, and both
leave the same per-configuration `KILLED` row -- but one is a shutdown the
operator asked for and the other is a defect. Printing "INTERRUPTED" over a
`MatchingModelMismatchError` would tell an operator their own Ctrl-C stopped a
study that in fact fell over, and printing "CRASHED" over a `docker stop` would
send them hunting a bug that is not there.
"""


def print_study_close(status, study_elapsed, run_success, run_error,
                      run_cancelled, db_path=None, out=None,
                      degradation_snapshot=None, census_snapshot=None) -> None:
    """The study's closing block. ONE TEXT, TWO CALLERS.

    Called from the normal path and from the Ctrl-C handler, which re-raises
    and therefore never reaches the normal one. `oncotriage/batch/runner.py`
    accepts the same duplication and argues it -- "the wording is the summary
    line's, deliberately, so an operator reading a scrolled-back log sees the
    same numbers in the same shape whether the pass ended or was interrupted" --
    and a function is that argument with the duplication removed.

    IT IS WHERE THE STUDY'S THREE COUNTERS ARE READ, and that is a contract
    rather than a convenience. `oncotriage/degradation.py` excludes this
    module's counters from its registry by name (importing the study there would
    drag the graph, the fixtures and the thread pool into every batch run), so
    the registry's rule -- every counter has a production reader -- is met at
    the end of this file's own `main()`. Before this function existed the
    Ctrl-C path skipped that block entirely, so an interrupted study reported
    none of its degradations.

    AND IT IS WHERE THE REGISTRY'S TWO BLOCKS ARE PRINTED, WHICH THIS STUDY
    OWED AND DID NOT PAY. Every counter in `oncotriage/degradation.py`'s
    registry and census is moved by a study exactly as it is moved by a batch
    run -- the study drives the same six-stage graph, the same Stage 5, the same
    writer -- and until this function printed them, an ablation study reported
    NONE of them. A study that dropped a retrieval channel on every patient, or
    kept every sex-specific trial because a sex would not parse, ended with a
    summary table and nothing saying so; `oncotriage/batch/runner.py` has
    printed both blocks at the end of every run since the counter-reader pass,
    and the two programs owe a reader the same account.

    THE THREE READERS BELOW ARE STILL THIS FILE'S OWN and are NOT duplicated by
    the registry block. `degradation.py` excludes this module's counters by name
    -- importing the study there would drag the graph, the fixtures and the
    thread pool into every batch run's import graph -- so `CHECKPOINT_FAULTS`,
    `STOP_SWITCH_FAULTS` and this module's `RUN_RECORD_FAILURES` are read here
    and only here. The registry's `RUN_RECORD_FAILURES` is
    `oncotriage/storage/database_logger.py`'s, a different object with a
    different subject; the two are the pair `tests/test_degradation_counter_
    readers.py` calls dual-owned, and printing both is the point rather than a
    collision.

    ONE SNAPSHOT PER BLOCK, TAKEN HERE. The batch runner takes its two in
    `main()` because it has THREE consumers of the degradation one -- the
    structured event, the `run_metrics` flush and the printed block -- and they
    must describe one instant. This study has exactly one consumer of each, so
    the snapshot is taken at that consumer: same guarantee, no parameter for a
    caller to forget. `degradation_snapshot` / `census_snapshot` stay
    INJECTABLE for the same reason `out` is -- a test needs to drive the block
    against known counts, and a future flush here would hand in the snapshot it
    persisted rather than taking a second one.

    BOTH ARE TAKEN BEFORE ANYTHING IS PRINTED, and that ordering is not
    cosmetic: emitting a line can itself move `EMIT_FAILURES`, so a snapshot
    taken part-way down would describe a report that was already being written.

    `out` IS INJECTABLE on `report_checkpoint_faults`' precedent: `main()`
    cannot be driven without a live Qdrant and a live billed call per pair, so a
    reader nothing can exercise is how a reader comes to be wrong.
    """
    emit = console.out if out is None else out
    # TAKEN BEFORE THE FIRST `emit`, for the reason in the docstring: a line
    # this block writes can itself move EMIT_FAILURES.
    if degradation_snapshot is None:
        degradation_snapshot = degradation.snapshot()
    if census_snapshot is None:
        census_snapshot = degradation.census_snapshot()
    # AN UNRECOGNISED STATUS IS NAMED RATHER THAN FALLING THROUGH THE CHAIN
    # BELOW INTO SILENCE. Without this a status outside the closed vocabulary
    # -- a typo, a member added without a branch -- prints the whole block with
    # NO `Status:` line at all, which reads as a study that ended in a way
    # nobody thought to describe. `_finalize_run` applies the same rule to
    # `RUN_STATUSES` one table over, and this is what gives `STUDY_STATUSES` a
    # reader rather than leaving it the dead declaration check 2h of
    # tests/test_package_invariants.py exists to report.
    if status not in STUDY_STATUSES:
        status = STUDY_STATUS_CRASHED
        emit(f"  [Study] an unrecognised study status was passed to the "
             f"closing block; reporting it as {STUDY_STATUS_CRASHED}")
    emit()
    emit("=" * 70)
    emit(f"{Project_Name}: ABLATION STUDY SUMMARY")
    emit("=" * 70)
    emit(f"  Wall time:       {study_elapsed / 60:.1f} min")
    emit(f"  Completed:       {run_success + run_error}")
    emit(f"  Success:         {run_success}")
    emit(f"  Errors:          {run_error}")
    # NAMED ONLY WHEN NON-ZERO, so a clean study's block is byte-identical to
    # what it has always printed and a stopped one cannot report pairs nobody
    # ran as pairs that failed.
    if run_cancelled:
        emit(f"  Cancelled:       {run_cancelled} (never started, never billed)")
    emit(f"  Database:        {ablation_db(db_path)}")

    # Checkpoint degradations, reported here rather than left in the
    # scrollback (pass 20f-1, item 11a's shape). Printed only when there
    # were any, matching INDEX_AGE_PARSE_FAILURES in
    # oncotriage/retrieval/indexer.py -- a zero line every run trains a
    # reader to skip it.
    if CHECKPOINT_WRITE_FAILURES:
        emit(f"  Checkpoint:      "
             f"{sum(CHECKPOINT_WRITE_FAILURES.values())} write "
             f"degradation(s) {dict(CHECKPOINT_WRITE_FAILURES)} -- resume "
             f"state may be behind the rows already in the database")

    # SEVERITY ASCENDING, VERDICT LAST -- `oncotriage/batch/runner.py`'s
    # ordering, adopted rather than invented. The census is observations about
    # what this study rendered and flagged; the registry block is the faults;
    # this module's own three readers are faults too and sit beside them; the
    # `Status:` line below is the verdict. A reader scanning UP from the bottom
    # of a long log meets the conclusion, then the reasoning, then the
    # background.
    degradation.print_census_report(census_snapshot, out=emit)
    degradation.print_report(degradation_snapshot, out=emit)

    report_checkpoint_faults(out=emit)
    report_stop_switch_faults(out=emit)
    report_run_record_failures(out=emit)

    # THE SPEND BLOCK, ON EVERY PATH INCLUDING THE CRASH ONE. It is what an
    # operator asks first about a study that stopped, and `spend.report_lines`
    # always emits -- a study that spent nothing still prints, because silence
    # would be indistinguishable from a ledger that was never wired up.
    emit()
    for _line in spend.report_lines():
        emit(f"  {_line}")
    emit()

    if status == STUDY_STATUS_STOPPED and spend.SPEND_STOP.requested \
            and not STOP_SWITCH.requested:
        # A BUDGET STOP AND AN OPERATOR STOP END A STUDY THE SAME WAY AND ARE
        # REMEDIATED DIFFERENTLY, so they get different blocks. Telling an
        # operator who wrote no sentinel to `rm` one -- and printing a path
        # that does not exist -- is the wrong-remediation defect
        # `describe_checkpoint_state` exists to remove, met here through the
        # other switch.
        emit("  Status:          STOPPED (a spend limit was reached)")
        emit(f"                   limit:    {spend.SPEND_STOP.limit}")
        emit(f"                   noticed:  {spend.SPEND_STOP.detected_in}")
        emit("                   NO SENTINEL WAS WRITTEN, so there is nothing "
             "to delete and")
        emit("                   --clear-stop has nothing to clear.")
        if spend.SPEND_STOP.limit == spend.SPEND_LIMIT_CALL_CEILING:
            emit("                   THIS IS A DEFECT REPORT, NOT A BUDGET "
                 "EVENT: one Stage 5")
            emit("                   invocation asked for more billed calls "
                 "than its")
            emit("                   configuration can produce. Raising the "
                 "cap will not help.")
        else:
            emit("                   To continue: raise config.SPEND_CAP_USD "
                 "and run again --")
            emit("                   this database's rows are counted, so the "
                 "resume starts")
            emit("                   from what was already spent rather than "
                 "from zero.")
        emit("                   NO SUMMARY WAS GENERATED and the checkpoint "
             "was KEPT: this study")
        emit("                   covers a PREFIX of its configurations, so no "
             "mean over it is a")
        emit("                   mean over the sample.")
    elif status == STUDY_STATUS_STOPPED:
        emit("  Status:          STOPPED (an operator asked for it)")
        emit(f"                   sentinel: "
             f"{describe_ablation_stop_switch_path(db_path)}")
        emit("                   The sentinel is NOT deleted by the study that "
             "honoured it, and")
        emit("                   the next study refuses to start while it is "
             "there. To resume:")
        emit(f"                       rm "
             f"{describe_ablation_stop_switch_path(db_path)}")
        emit("                   or, in one command: python "
             "\"26- Ablation Study.py\" --clear-stop")
        emit("                   NO SUMMARY WAS GENERATED and the checkpoint "
             "was KEPT: this study")
        emit("                   covers a PREFIX of its configurations, so no "
             "mean over it is a")
        emit("                   mean over the sample.")
    elif status == STUDY_STATUS_INTERRUPTED:
        emit("  Status:          INTERRUPTED (resume with same command)")
        emit("                   NO SUMMARY WAS GENERATED and the checkpoint "
             "was KEPT.")
    elif status == STUDY_STATUS_CRASHED:
        emit("  Status:          CRASHED (see the traceback below)")
        emit("                   NO SUMMARY WAS GENERATED and the checkpoint "
             "was KEPT, so a")
        emit("                   resume after the fix costs nothing for what "
             "already ran.")
    # COMPLETE prints its own two lines at the call site, because they name the
    # summary file the caller has just written and this function does not write.


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="OncoMatch Ablation Study")
    parser.add_argument(
        "--sample-size", type=int, default=ABLATION_SAMPLE_SIZE_DEFAULT,
        help=f"Number of patients to sample (default: {ABLATION_SAMPLE_SIZE_DEFAULT})"
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
    # The configuration-fingerprint pass. load_ablation_checkpoint() REFUSES a
    # checkpoint it cannot vouch for and deletes nothing, so this is the "yes,
    # discard it" it names. It clears THIS --db's checkpoint and no other,
    # because the checkpoint is per database (pass 20f-3) and a flag that
    # cleared the production resume state while the operator was running an
    # isolated study would be the very defect that pass removed.
    parser.add_argument(
        "--fresh-start", action="store_true",
        help="Delete this database's checkpoint before running, discarding all "
             "resume state so every (config, patient) pair runs again. The "
             "remediation for a refused checkpoint -- and it re-bills every "
             "pair, which is why it is a flag rather than a fallback."
    )
    # --clear-stop IS THE RESUME GESTURE AFTER A STOP, AND IT IS A SEPARATE
    # FLAG FROM --fresh-start ON PURPOSE. They are opposites: --fresh-start
    # DISCARDS the resume state and re-bills every (config, patient) pair, this
    # discards a CONTROL FILE and costs nothing. An operator resuming a stopped
    # study wants exactly this and must not be one keystroke from the other,
    # which is also why the two have no combined form.
    #
    # It is a flag rather than the study deleting the sentinel itself: see
    # assert_no_stale_ablation_stop_switch for why a self-clearing switch would
    # let a restart loop honour a stop nobody asked for and report success each
    # time.
    #
    # THE PATH IS NOT INTERPOLATED INTO THE HELP. ablation_stop_switch_path()
    # reads paths.checkpoint_path, which resolves the sibling data tree by glob
    # on first read and RAISES on a machine that does not have it -- so a
    # resolved path here would make `--help` fail on exactly the checkout where
    # somebody is reading it to find out what the flag does. The run banner
    # prints the resolved path, where resolving it is already unavoidable.
    parser.add_argument(
        "--clear-stop", action="store_true",
        help="Delete this study's operator stop sentinel (the _STOP file "
             "beside its checkpoint -- the run banner prints its absolute "
             "path) before running. This is how a study that was STOPPED is "
             "resumed: the sentinel is left in place by the run that honoured "
             "it, and a study refuses to start while it is there. It discards "
             "no results and re-bills nothing."
    )
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

    # ══ STEP 0: THE CONTROL FILES, ABOVE EVERY DESTRUCTIVE FLAG ════════════
    #
    # ORDERING IS THE WHOLE OF THIS BLOCK, and it is the defect the
    # pre-migration pass had to fix in oncotriage/batch/runner.py. That file's
    # preflight lived INSIDE main(), below the flag handling -- so an operator
    # who typed the destructive flag while a sentinel was still present got the
    # resume state DELETED and then a refusal whose own last line reads
    # "NOTHING HAS BEEN RUN AND NOTHING HAS BEEN BILLED": true of the billing
    # and false of the resume state, which was gone, and the next invocation
    # re-ran everything. Here the flag is --fresh-start and the state is up to
    # 525 live Stage 5 calls.
    #
    # --clear-stop SATISFIES THE PREFLIGHT RATHER THAN BEING BLOCKED BY IT, and
    # that is the rule's other half rather than an exception to it. The
    # refusal's own remediation names that flag, so a preflight that refused the
    # command it tells the operator to run would be a loop with no exit. The
    # asymmetry is exactly the destructive/non-destructive line: --clear-stop
    # deletes a CONTROL FILE and re-bills nothing, --fresh-start deletes the
    # RESUME STATE and re-bills every pair.
    if args.clear_stop:
        # ALL THREE OUTCOMES ARE BRANCHED ON, and the third is why the return
        # is not a bool. This SKIPS the preflight below, so a clear that FAILED
        # and was reported as "nothing to clear" would start the study with the
        # sentinel still there -- and it would stop again at the first completed
        # pair, after billing that pair, for a request just withdrawn.
        _cleared = clear_ablation_stop_switch(db_path=db_path)
        if _cleared == control.STOP_CLEAR_ABSENT:
            console.out(f"[--clear-stop] No stop sentinel at "
                        f"{describe_ablation_stop_switch_path(db_path)}; "
                        f"nothing to clear.")
        elif _cleared == control.STOP_CLEAR_FAILED:
            console.out("[--clear-stop] REFUSING TO RUN: the sentinel is still "
                        "there. NOTHING HAS BEEN RUN AND NOTHING HAS BEEN "
                        "BILLED.")
            sys.exit(1)
    elif not (args.summary_only and not args.fresh_start):
        # ── THE PREFLIGHT, AND THE ONE INVOCATION IT DOES NOT APPLY TO ─────
        #
        # A PURELY READ-ONLY INVOCATION IS EXEMPT, and the exemption is as
        # narrow as it can be: `--summary-only` WITHOUT `--fresh-start`. That
        # mode reads the database, writes ablation_summary.json, runs nothing
        # and bills nothing -- so the refusal's own premise ("this run would
        # stop again at its first completed pair") is false of it, and its own
        # remediation ("delete the sentinel") would tell an operator to
        # withdraw a stop they had not withdrawn just to LOOK at what the
        # stopped study produced. Refusing there would make the natural next
        # command after a stop the one command that un-stops the next study.
        #
        # `--fresh-start` PUTS IT BACK, even combined with --summary-only,
        # because that flag DELETES THE RESUME STATE whatever else the
        # invocation does -- which is exactly the destructive act the preflight
        # is ordered above.
        #
        # KEPT HERE AS WELL AS IN THE ENTRY POINT'S GUARD, AND IT IS NOT
        # REDUNDANT: main() is directly callable by an embedder that never sees
        # that guard, and the check is one stat call. On the entry-point path
        # the guard has NOT already run this -- it owns the lock and the signal
        # disposition and nothing else -- so this is the only place it fires.
        try:
            assert_no_stale_ablation_stop_switch(db_path=db_path)
        except StaleAblationStopSwitch as _stale:
            console.out()
            console.out(str(_stale))
            sys.exit(1)

    # --- --fresh-start, before anything reads the checkpoint ---
    # Above --summary-only deliberately: --summary-only reads the database and
    # never the checkpoint, so combining the two would silently do nothing
    # while looking like it had cleared something. Announced before it happens,
    # because it is destructive and expensive and the operator should see the
    # file named while there is still time to interrupt.
    if args.fresh_start:
        console.out(f"[--fresh-start] Discarding {_ablation_checkpoint_path(db_path)}. "
                    f"Every (config, patient) pair will run again, at one live "
                    f"Stage 5 call each.")
        clear_ablation_checkpoint(db_path=db_path)
        if args.summary_only:
            console.out("[--fresh-start] NOTE: --summary-only was also given, "
                        "so nothing will be re-run in this invocation.")

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

    # ONE CONFIGURATION PER STUDY, RESOLVED ONCE. Dropped here so a second
    # main() in one process resolves again rather than stamping its checkpoint
    # with the first study's collection.
    run_fingerprint.clear_cache()

    # ── THE OTHER TWO PIECES OF PER-RUN MODULE STATE ───────────────────────
    #
    # Both for the reason the line above records and `clear_write_ledger` /
    # `STOP_SWITCH.reset` record in oncotriage/batch/runner.py's main(): module
    # state that survives into the next study describes the WRONG study. Here
    # each has a specific cost. A stop inherited from a previous main() in this
    # process would cancel every pair of this one without an operator having
    # asked; a Stage 5 shutdown flag inherited from one would make every pair
    # FAIL without a request being sent, and the study would report a whole
    # cohort of errors for a Ctrl-C somebody pressed in an earlier run.
    STOP_SWITCH.reset()
    clear_stage5_shutdown()
    # THE FOURTH AND FIFTH PIECES, for the reason the three above are cleared.
    # A ledger inherited from an earlier main() in this process would charge
    # this study for money another one spent, and once the two together crossed
    # the cap this study would refuse to start over spend it did not make; the
    # latch goes with it, or a stop from the earlier study would cancel every
    # pair of this one before a request was issued.
    spend.SPEND_LEDGER.reset()
    spend.SPEND_STOP.reset()


    with CaffeinateSession("Ablation Study"):

        # --- Step 1: Initialize ---
        init_ablation_db(db_path=db_path)

        # ── ARM THE STOP SWITCH, AND SAY WHERE IT IS ───────────────────────
        #
        # ARMED HERE RATHER THAN RESOLVED PER POLL, which is where this
        # diverges from oncotriage/batch/runner.py's switch: that one has a
        # single sentinel and resolves it inside poll(); this one's location
        # depends on --db, and the poll runs on MAX_WORKERS done-callbacks.
        # Binding it once, on this thread, means the path an operator is TOLD
        # to write and the path the study watches are ONE reading rather than
        # two that could disagree.
        #
        # THE BANNER IS UNCONDITIONAL, unlike the --db lines above it. An
        # operator can only use a switch whose path they have been given, and
        # asking them to derive it from a checkpoint filename is how a stop
        # gets written to a file nothing reads -- which looks exactly like a
        # switch that does not work. This is the same reason
        # oncotriage/batch/runner.py prints its sentinel path on every run.
        STOP_SWITCH.arm(ablation_stop_switch_path(db_path))
        console.out(f"  Stop switch:     touch "
                    f"{describe_ablation_stop_switch_path(db_path)}")
        console.out( "                   (stops cleanly between pairs; the "
                     "checkpoint stays current and a resume skips what ran)")

        # ── THE SPEND GATE ────────────────────────────────────────────────
        #
        # SEEDED FROM THIS DATABASE'S OWN HISTORY, which is what makes the cap
        # a budget for the STUDY rather than for one invocation of it. Without
        # it a study stopped on its cap and resumed would get a fresh $300
        # every time, which is the per-invocation failure `LedgerSeed` exists
        # to remove one program over.
        #
        # BOTH LINES PRINT UNCONDITIONALLY, including on a fresh study and an
        # uncapped one, on `describe_cap()`'s argument: the dangerous state
        # must not be the quiet one.
        console.out(spend.describe_cap())
        spend.SPEND_LEDGER.seed(ablation_spend_before(db_path))
        console.out(spend.describe_seed(spend.SPEND_LEDGER.seeded))

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
        #
        # THE CONFIGURATION STAMP IS TAKEN ON THIS THREAD, BEFORE THE POOL.
        # save_ablation_checkpoint() is called from _on_done, a done-CALLBACK
        # running on a WORKER thread, so without this MAX_WORKERS threads would
        # reach an unwarmed resolver at once. It is also the value the gate one
        # line below compares against, so what refuses a resume and what stamps
        # the writes are one reading rather than two that can straddle an alias
        # swap.
        _fingerprint = run_fingerprint.current()
        # run_fingerprint owns this sentence; see the same call in
        # oncotriage/batch/runner.py:main for why it is not written out here.
        console.out(f"  Configuration: {run_fingerprint.summary(_fingerprint)}")

        # A REFUSAL HERE IS ABOVE tracking.start_run AND ABOVE THE FIRST BILLED
        # CALL, so a checkpoint this study may not continue stops it having
        # spent nothing, opened no tracking run and -- above all -- deleted
        # nothing.
        try:
            completed = load_ablation_checkpoint(db_path=db_path,
                                                 fingerprint=_fingerprint)
        except run_fingerprint.ResumeRefusal as exc:
            console.out()
            console.out(str(exc))
            sys.exit(1)

        # --- Step 3b: Open the parent tracking run (the tracking pass) ---
        # ONE PARENT PER STUDY, one nested child per configuration, and the
        # children are opened AFTER the run loop rather than around it -- see
        # the block below generate_summary(). The parent opens here because
        # this is the last line before the first billed call and the first
        # point at which the sample size, the seed, the config selection and
        # the resume state are all known.
        #
        # A RESUMED STUDY IS A NEW PARENT, TAGGED `resumed=true`, exactly as in
        # oncotriage/batch/runner.py and for the same reason recorded there: no
        # run-continuation machinery is invented, and the tag is what joins the
        # two parents a resumed study produces.
        #
        # MAIN THREAD ONLY. Every tracking call in this file is outside the
        # ThreadPoolExecutor -- this one before it is created, the rest after
        # the last future has been waited on.
        tracking.start_run(
            kind="ablation",
            # `seed` IS NOT PASSED HERE ANY MORE, and its absence is the
            # promotion working rather than an omission. ABLATION_SEED became
            # a config constant, so tracking.CONFIGURATION_PARAM_NAMES logs it
            # by name on every run; passing it here as well would put one
            # number into the store twice under two keys, which is exactly what
            # that tuple's RRF_K note argues against. The collision check in
            # start_run would NOT have caught it -- it compares keys, and
            # "seed" and "ABLATION_SEED" are different keys.
            #
            # If a --seed flag is ever added to this file, this line comes back
            # AND ABLATION_SEED leaves the enumeration, on the same day: from
            # that day the constant is a default the run may not have used.
            params={
                "sample_size": args.sample_size,
                "configs": ",".join(c["name"] for c in configs),
                "db_path": str(ablation_db(db_path)),
            },
            tags={"resumed": "true" if completed else "false"},
        )

        # THE PARENT RUN IS CLOSED ON EVERY EXIT PATH. See the identical guard
        # in oncotriage/batch/runner.py:main() for the measurement behind it --
        # MLflow's own atexit hook records a crashed run as FINISHED. The
        # KeyboardInterrupt this file already handles is caught further in and
        # ends the parent as KILLED; what this catches is everything else -- a
        # raise from generate_summary(), from the checkpoint clear, or from the
        # pool.
        # ── THE COUNTERS AND THE OPEN RUN ID LIVE ABOVE THE OUTER `try` ────
        #
        # The `except BaseException` guard at the bottom now FINALIZES the open
        # configuration and PRINTS the closing block, so every name it reads has
        # to be bound before the `try` is entered -- otherwise an exception
        # raised in the first few statements (tqdm's constructor is one) would
        # meet an unbound local and replace the study's diagnosis with a
        # NameError about a counter, on the one path whose job is to leave a
        # record.
        run_success = 0
        run_error = 0
        run_cancelled = 0
        # THE CONFIGURATION CURRENTLY OPEN, so the shutdown paths can finalize
        # it. A plain local rather than module state, for the reason
        # oncotriage/batch/runner.py gives for threading its run_id through:
        # there is nothing to forget, so a second main() in one process cannot
        # inherit the first study's open configuration.
        #
        # THERE IS NO `interrupted` FLAG. It existed to carry "Ctrl-C happened"
        # past the handler down to Step 5; the handler re-raises now, so Step 5
        # is unreachable from that path and a flag read there would be dead.
        # What replaced it is `STOP_SWITCH.requested`, which is a fact about the
        # run rather than a variable somebody has to remember to set.
        open_run_id = None
        study_start = time.time()
        # ── "WAS EVERYTHING COVERED", NOT "WAS A SENTINEL SEEN" ────────────
        #
        # THE PRE-MIGRATION PASS HAD TO FIX EXACTLY THIS IN THE BATCH RUNNER
        # AND THE NAIVE PORT REPRODUCES IT. `main()` there read
        # `STOP_SWITCH.requested` at four sites, which is a question about
        # whether a sentinel was SEEN and not about whether the work was DONE
        # -- so a stop written while the last pass was already finishing
        # recorded a run STOPPED, "whose entire meaning is 'this campaign
        # covers a PREFIX'", over a cohort that had been covered in full.
        #
        # A STUDY MEETS THE SAME CASE IN TWO PLACES. A stop can arrive while
        # every pair of the current configuration is ALREADY IN FLIGHT -- they
        # all finish, nothing is cancelled, nothing goes unsubmitted, and that
        # configuration's results are the WHOLE sample rather than a prefix.
        # And if that configuration is the last one, the STUDY covered its work
        # too.
        #
        # SO THE ANSWER IS THE ONE THE BATCH RUNNER ARRIVED AT: the only two
        # ways a unit can be left unattempted are "never submitted" and
        # "cancelled before it started", and this flag is set False when either
        # happens -- or when a whole configuration is never started at all.
        # `STOP_SWITCH.requested` still decides what is ANNOUNCED; this decides
        # what is RECORDED.
        #
        # IT MATTERS BEYOND A LABEL. A configuration wrongly recorded STOPPED
        # is the LATEST row for its config_name, and a resume SKIPS a
        # configuration whose pairs are all checkpointed -- so it never gets a
        # COMPLETE row, and `_summary_status_warning` warns about a prefix that
        # is not one, permanently.
        study_covered = True

        try:
            # --- Step 4: Run each config ---
            total_configs = len(configs)
            total_runs = total_configs * len(sample)
            already_done = len(completed)
            remaining = total_runs - already_done

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

                    # ── THE SWITCH, BETWEEN CONFIGURATIONS ─────────────────
                    #
                    # THIS IS THE COARSE HALF AND IT IS NOT THE SWITCH. On its
                    # own it would be a useless granularity: one configuration
                    # is `sample_size` live Stage 5 calls -- 75 at the default,
                    # roughly half an hour, one seventh of a 3-5 hour study --
                    # so an operator who asked a study to stop would keep paying
                    # for all of it. The switch that matters is polled BETWEEN
                    # PAIRS, in the submit loop and in _on_done below, at the
                    # checkpoint's own cadence.
                    #
                    # WHAT THIS ONE BUYS is that a stop noticed during
                    # configuration 3 of 7 leaves configurations 4-7 UNSTARTED
                    # rather than opening four more ablation_runs rows, printing
                    # four more banners and creating four more empty
                    # configurations for generate_summary to average over. A
                    # `_create_run` row with no results is the shape
                    # `_summary_status_warning` then has to explain.
                    if (STOP_SWITCH.poll(where="between configurations")
                        | spend.SPEND_STOP.poll(where="between configurations")):
                        _unstarted = total_configs - config_idx + 1
                        study_covered = False
                        console.out(f"[STOP] {_unstarted} "
                                    f"configuration(s) were never started.")
                        # THE BAR IS RESIZED TO WHAT WAS ACTUALLY ACCOUNTED
                        # FOR, and `progress.n` is exact HERE and only here:
                        # the previous configuration's executor was shut down
                        # with wait=True, which joins every worker, and a
                        # done-callback runs on the worker that completed the
                        # item (or, for a cancelled one, inside shutdown on this
                        # thread) -- so nothing can still be counting.
                        #
                        # ASSIGNED RATHER THAN DECREMENTED BY
                        # `unstarted * len(sample)`, which is the
                        # arithmetic that looks right and is wrong: the bar was
                        # created with `initial=already_done`, a figure that
                        # already includes pairs completed in the configurations
                        # this stop never reached, so subtracting their whole
                        # sample would double-count those.
                        progress.total = progress.n
                        progress.refresh()
                        break

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
                    open_run_id = run_id
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

                    futures = []
                    # PER CONFIGURATION, and separate from the study-wide
                    # `run_cancelled` on purpose: the status written to THIS
                    # configuration's row is a statement about THIS
                    # configuration's sample, and a study-wide total would
                    # mark a fully-covered configuration as a prefix because a
                    # LATER one was cut short.
                    config_cancelled = [0]

                    def _on_done(future, _config_name=config_name,
                                 _futures=futures, _cxl=config_cancelled):
                        nonlocal run_success, run_error, run_cancelled
                        try:
                            pid, result = future.result()
                        except CancelledError:
                            # A FUTURE THE SWEEP BELOW CANCELLED. Never started,
                            # never billed, and NOT an error: counting it as one
                            # would report work nobody ran as work that failed,
                            # and a stopped study's closing block would read like
                            # a study that broke.
                            run_cancelled += 1
                            _cxl[0] += 1
                            progress.set_postfix(ok=run_success, err=run_error,
                                                 cxl=run_cancelled)
                            progress.update(1)
                            return
                        except _PairCancelled:
                            # THE SWEEP'S EDGE, closed by _run_pair_unless_stopped:
                            # a pair submitted between the switch latching and the
                            # submit loop's next poll is cancelled by nothing, so
                            # the callable itself refuses. Same accounting as a
                            # cancelled future, because it is the same event.
                            run_cancelled += 1
                            _cxl[0] += 1
                            progress.set_postfix(ok=run_success, err=run_error,
                                                 cxl=run_cancelled)
                            progress.update(1)
                            return
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

                        # ── THE SWITCH, BETWEEN PAIRS ─────────────────────
                        #
                        # POLLED AFTER THE CHECKPOINT IS WRITTEN, so a stop
                        # noticed here is a stop taken against state that is
                        # already current: every pair this study has finished is
                        # in the file, and a resume skips exactly those.
                        #
                        # THE SWEEP CANCELS WHAT HAS NOT STARTED. Future.cancel()
                        # returns False for a running future and leaves it alone,
                        # which is the contract wanted: pairs in flight are
                        # already paid for and their rows are worth having.
                        if (STOP_SWITCH.poll(where="during a configuration")
                            | spend.SPEND_STOP.poll(where="during a configuration")):
                            _n = control.cancel_queued(_futures)
                            if _n:
                                console.out(f"[STOP] {_n} queued (config, "
                                            f"patient) pair(s) cancelled before "
                                            f"they could be started.")

                        progress.set_postfix(ok=run_success, err=run_error)
                        progress.update(1)

                    # ── THE EXECUTOR IS NOT A CONTEXT MANAGER ──────────────
                    #
                    # `with ThreadPoolExecutor(...) as executor:` calls
                    # shutdown(wait=True) -- WITHOUT cancel_futures -- from
                    # __exit__, which runs BEFORE any `except` clause below it.
                    # Every future is submitted up front by the loop below, so
                    # __exit__ DRAINS THE WHOLE REMAINING CONFIGURATION at one
                    # live billed Stage 5 call each, and only then is the
                    # handler entered. This is the identical defect the
                    # pre-migration pass removed from
                    # oncotriage/batch/runner.py:run_batch, measured there at
                    # 20 of 20 queued tasks completing under the `with` form
                    # against 2 of 20 under this one.
                    #
                    # THE MULTIPLIER HERE IS LARGER. A batch run's queue is the
                    # rest of the corpus once; this file's `with` block is
                    # INSIDE the configuration loop, so an interrupt drained
                    # the rest of THIS configuration and then -- because the
                    # KeyboardInterrupt was caught and not re-raised -- the loop
                    # carried on to the next configuration and did it again.
                    #
                    # THE `finally` SHUTDOWN IS WHAT MAKES IT TOTAL: it covers
                    # the SystemExit the entry point's SIGTERM handler raises
                    # and any ordinary exception, not only KeyboardInterrupt. On
                    # the NORMAL path every future has completed by the time it
                    # runs, so cancel_futures=True cancels nothing and the
                    # behaviour is byte-identical to the `with` form.
                    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
                    pairs_unsubmitted = 0
                    try:
                        for _index, patient_data in enumerate(pending_patients):
                            # THE SUBMIT LOOP HONOURS THE SWITCH TOO, and this
                            # is not redundant with the sweep in _on_done.
                            # Submitting `sample_size` futures takes
                            # milliseconds, so in production the loop is long
                            # finished before any pair completes and this never
                            # fires. It fires on a SMALL sample, a slow
                            # filesystem or a loaded machine -- exactly the
                            # conditions a test runs under -- and without it a
                            # pair submitted after the sweep would be neither
                            # cancelled nor accounted for, and would run and
                            # bill after the stop was announced.
                            if (STOP_SWITCH.poll(where="while submitting")
                                | spend.SPEND_STOP.poll(where="the submit loop")):
                                pairs_unsubmitted = (len(pending_patients)
                                                     - _index)
                                break
                            future = executor.submit(
                                _run_pair_unless_stopped,
                                _process_one,
                                patient_data=patient_data,
                                config_name=config_name,
                                ablation_flags=ablation_flags,
                                run_id=run_id,
                            )
                            future.add_done_callback(_on_done)
                            futures.append(future)

                        if pairs_unsubmitted:
                            # THE BAR IS RESIZED TO WHAT WILL BE ACCOUNTED FOR.
                            # _on_done advances it once per future, cancelled
                            # ones included, and a pair that was never submitted
                            # has no future and no callback -- so a bar still
                            # sized to the whole sample stops short and reads as
                            # a study that hung at the moment it was shutting
                            # down cleanly.
                            progress.total -= pairs_unsubmitted
                            progress.refresh()
                            console.out(f"[STOP] {pairs_unsubmitted} (config, "
                                        f"patient) pair(s) in '{config_name}' "
                                        f"were never submitted.")

                        # Wait for all to complete (callbacks handle progress)
                        for future in futures:
                            try:
                                future.result()
                            except (CancelledError, _PairCancelled):
                                # NOT AN ERROR AND IT MUST NOT ESCAPE HERE.
                                # Reachable only on the stop path: the switch
                                # cancels while this loop is still draining, and
                                # an escape would leave the config loop by
                                # exception -- into main()'s
                                # `except BaseException`, which records the
                                # parent tracking run FAILED. A clean operator
                                # stop would then be indistinguishable from a
                                # crash, which is the one thing the new status
                                # exists to distinguish. _on_done has already
                                # counted it.
                                continue
                    finally:
                        # SHUTDOWN FIRST, before anything below reads the
                        # counters, so no worker is still writing when they are
                        # read. Idempotent: on the interrupt path the handler
                        # below has already shut it down and this is a no-op.
                        executor.shutdown(wait=True, cancel_futures=True)

                    config_elapsed = time.time() - config_start
                    # THE STATUS IS A STATEMENT ABOUT COVERAGE, NOT ABOUT THE
                    # SWITCH. A configuration whose pairs were all already in
                    # flight when the stop arrived finishes every one of them:
                    # nothing was cancelled, nothing went unsubmitted, and its
                    # results are the WHOLE sample. Recording that STOPPED
                    # would assert a prefix that does not exist -- and, because
                    # a resume SKIPS a configuration whose pairs are all
                    # checkpointed, that row would stay the latest for its
                    # config_name forever. See `study_covered`.
                    _config_covered = (pairs_unsubmitted == 0
                                       and config_cancelled[0] == 0)
                    if not _config_covered:
                        study_covered = False
                    _config_status = (RUN_STATUS_COMPLETE if _config_covered
                                      else RUN_STATUS_STOPPED)
                    # ONE DERIVATION, READ BY THE ROW AND BY NOTHING ELSE THAT
                    # RE-DERIVES IT. `_stop_reason_now()` reads the two latches
                    # rather than being told, which is what keeps the reason
                    # stored and the reason announced one reading -- the
                    # duplicated-derivation pass's rule for `runs.status`,
                    # applied to the column beside it.
                    #
                    # A COVERED CONFIGURATION STORES NO REASON EVEN IF A LATCH
                    # IS SET. A stop that arrived while every pair was already
                    # in flight cut NOTHING short: the configuration ran its
                    # whole sample, `_config_covered` says so, and a reason
                    # beside a COMPLETE status would assert a prefix that does
                    # not exist. Same argument the status itself is made on.
                    _finalize_run(run_id, config_elapsed, _config_status,
                                  db_path=db_path,
                                  stop_reason=(None if _config_covered
                                               else _stop_reason_now()))
                    open_run_id = None
                    console.out(f"\n  Config '{config_name}' "
                                f"{'done' if _config_covered else 'stopped'}"
                                f": {config_elapsed / 60:.1f} min")
                    # THERE IS DELIBERATELY NO `break` HERE. Falling through to
                    # the top-of-loop poll is what COUNTS the configurations
                    # that were never started -- the poll sets
                    # `_unstarted` and prints it, and a break here would
                    # leave that at 0 and tell a stopped operator nothing about
                    # how much of the study remains. The poll is above
                    # `_create_run`, so nothing is opened on the way out; the
                    # first draft of this block carried the break as "belt and
                    # braces" and it was strictly worse.

            except KeyboardInterrupt:
                # ── THE FIRST STATEMENT, BEFORE ANYTHING IS PRINTED ────────
                #
                # THE RAISE ALONE DOES NOT REACH STAGE 5. CPython delivers a
                # signal to the MAIN thread; the pipeline runs on WORKER threads
                # of the pool above. So the KeyboardInterrupt lands here, the
                # executor's `finally` cancels QUEUED pairs, and every pair
                # already in flight then finishes its WHOLE Stage 5 exchange --
                # in the grouped arm every remaining chunk of the packer's plan,
                # in the per-trial arm ceil(MAX_TRIALS_FOR_EVALUATION /
                # MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS) rounds -- each bounded
                # only by MATCHING_REQUEST_TIMEOUT_SECONDS and the SDK's own
                # retries, while shutdown(wait=True) blocks. Every one of those
                # is a live billed request issued AFTER the operator pressed
                # Ctrl-C.
                #
                # Setting the flag bounds the wait at ONE in-flight request per
                # worker: what is already in the air cannot be interrupted, and
                # every queued or subsequent call returns immediately without
                # being sent. The pairs affected FAIL rather than completing
                # partially, so none of them is checkpointed and a resume
                # re-runs them whole -- which is the c33 argument, and the
                # reason a shutdown is not isolated to a chunk.
                request_stage5_shutdown("Ctrl-C during the ablation study")
                console.out("\n[INTERRUPTED] Waiting for active threads to finish...")
                console.out("[INTERRUPTED] Checkpoint saved: every completed "
                            "(config, patient) pair is in it and a resume will "
                            "skip them.")
                # ── THE CONFIGURATION IN FLIGHT IS RECORDED AS KILLED ──────
                #
                # WITHOUT THIS ITS ROW STAYS `RUNNING` FOREVER, which is the
                # shape reserved for a process that had no chance to run a
                # handler -- and this one did. The results under it are a PREFIX
                # of the sample, and `_summary_status_warning` is what tells a
                # later reader so; a row that never says how it ended cannot be
                # distinguished from one whose study is still going.
                #
                # `_finalize_run` NEVER RAISES, which is what makes this safe to
                # put in a handler. See RUN_RECORD_FAILURES.
                # THE FINALIZE AND THE CLOSING BLOCK ARE THE OUTER GUARD'S,
                # NOT THIS HANDLER'S, and moving them there is what closed a
                # real gap: SIGTERM raises SystemExit, which this `except
                # KeyboardInterrupt` does NOT catch, so a `docker stop` left
                # the open configuration reading RUNNING forever and printed
                # no closing block at all. Measured, before the move: exit
                # 143, and the ablation_runs row still RUNNING. One handler
                # for all three abrupt paths is the fix; this one keeps only
                # what is specific to Ctrl-C.
                # THE INTERRUPT IS RE-RAISED, AND IT USED TO BE SWALLOWED. The
                # old handler set a flag and RETURNED NORMALLY -- so the study
                # carried on to the NEXT CONFIGURATION, and, worse, the `with
                # ThreadPoolExecutor` form above had already drained the rest of
                # THIS one at a live billed call each on its way out. Both costs
                # are exactly the batch runner's, which the stop-switch pass
                # measured and re-raised for.
                #
                # WHAT IT REACHES: `except BaseException` below closes the
                # parent tracking run FAILED and re-raises, which is the same
                # thing oncotriage/batch/runner.py:main() does with a Ctrl-C --
                # MLflow's three-member vocabulary has no STOPPED and no
                # INTERRUPTED, and FAILED is the closest true statement it can
                # carry for a study that did not finish.
                #
                # THE CHECKPOINT IS INTACT AND NOTHING HERE DELETES ANYTHING.
                # Every completed pair was checkpointed by _on_done as it
                # finished, and a cancelled one was never added.
                raise

            finally:
                progress.close()
                console.detach_bar(_bar_token)

            # --- Step 5: Summary ---
            #
            # REACHED BY THE NORMAL PATH AND BY A STOP, AND NOT BY Ctrl-C, which
            # re-raises above after printing the same block itself.
            study_elapsed = time.time() - study_start
            # NOT `STOP_SWITCH.requested`. A stop that arrives while the LAST
            # configuration's pairs are all in flight leaves nothing
            # unattempted -- every configuration ran its whole sample -- and
            # such a study has covered its work. Recording it STOPPED would
            # withhold the summary and keep a checkpoint over a study with
            # nothing left to resume, which is the batch runner's scenario C
            # correction applied here. The stop is still ANNOUNCED either way,
            # by the switch's own console block; what this decides is which of
            # the two things it is reported as having cut short.
            stopped = not study_covered

            print_study_close(
                STUDY_STATUS_STOPPED if stopped else STUDY_STATUS_COMPLETE,
                study_elapsed, run_success, run_error, run_cancelled,
                db_path=db_path)

            if stopped:
                # NEITHER THE SUMMARY NOR THE CHECKPOINT CLEAR RUNS, and the
                # second is the one that would cost money. clear_ablation_
                # checkpoint() deletes the resume state, so on a stopped study
                # it would throw away every completed (config, patient) pair and
                # the next run would re-bill all of them -- at one live Stage 5
                # call each. The summary is withheld for the reason
                # print_study_close states: a stopped study covers a PREFIX of
                # its configurations, and generate_summary() would overwrite
                # ablation_summary.json with means computed over it.
                #
                # `--summary-only` REMAINS AVAILABLE and is the deliberate way
                # to look at a prefix on purpose: it prints the table with
                # _summary_status_warning's qualification above the deltas.
                pass
            else:
                summary_df = generate_summary(db_path=db_path)
                clear_ablation_checkpoint(db_path=db_path)
                console.out(f"  Summary:         {ablation_summary_json(db_path)}")
                # THE CONSTANT, NOT THE LITERAL. This line and the
                # `print_study_close(... STUDY_STATUS_COMPLETE)` call fifteen
                # lines above report the SAME verdict about the SAME study, and
                # a literal here is a second copy of it that no test and no
                # reader can see disagree. (The f-prefix went with it: it had no
                # placeholder, which is the pyflakes F541 that says exactly
                # this -- a formatted string that formats nothing is a string
                # somebody meant to interpolate into.)
                console.out(f"  Status:          {STUDY_STATUS_COMPLETE}")

            # --- Step 6: Close the tracking run (the tracking pass) ---
            # THE CHILDREN ARE OPENED HERE, FROM THE SUMMARY, and not around each
            # config's loop. Two reasons, both about honesty rather than
            # convenience:
            #
            #   * the numbers a child carries are generate_summary()'s, which are
            #     computed by ONE SQL query over the whole database after the last
            #     config finishes. A child opened around the loop would have to
            #     recompute them per config, which is a second computation of a
            #     figure the study already produces -- the shape this project has
            #     removed twice (cost_by_model, ablation_db).
            #   * generate_summary() reports the LATEST run per config, so it
            #     covers configs this invocation resumed rather than ran. A child
            #     per loop iteration would index only what this process executed,
            #     and a resumed study's index would be missing its earlier configs.
            #
            # AN INTERRUPTED STUDY GETS NO CHILDREN AND A `KILLED` PARENT. That is
            # the honest record: generate_summary() was not called, so there are no
            # per-config numbers to index, and inventing them from a partial
            # database would be the metric invention this pass forbids.
            # A STOPPED STUDY GETS NO CHILDREN AND A `KILLED` PARENT. That is
            # the honest record: generate_summary() was not called, so there are
            # no per-configuration numbers to index, and inventing them from a
            # partial database would be the metric invention this pass forbids.
            #
            # `KILLED` AND NOT `STOPPED`, and the substitution is deliberate
            # rather than a shortcut. tracking.RUN_STATUSES is MLflow's
            # three-member vocabulary (FINISHED / FAILED / KILLED) and has no
            # STOPPED; passing one would be silently replaced by FAILED, which
            # reads as a study that broke. KILLED is MLflow's own "run killed by
            # user" and is the closest true statement available.
            # `oncotriage/batch/runner.py` makes exactly this substitution for
            # exactly this reason, and records STOPPED in its own table where
            # the vocabulary is this project's -- which here is
            # ablation_runs.status.
            #
            # THE Ctrl-C PATH DOES NOT REACH THIS LINE: it re-raises above and
            # the parent is closed FAILED by the `except BaseException` guard.
            if stopped:
                tracking.end_run(status="KILLED")
            else:
                _summary_records = ([] if summary_df is None
                                    else summary_df.to_dict(orient="records"))
                for _record in _summary_records:
                    # `configs` is the only legal caller param here: the child's
                    # subject IS one configuration, and every other parameter it
                    # would carry is the parent's, logged once there.
                    tracking.start_run(
                        kind="ablation",
                        params={"configs": _record["config_name"],
                                "sample_size": args.sample_size},
                        run_name=_record["config_name"],
                        nested=True,
                    )
                    # EVERY NUMERIC COLUMN generate_summary() produced, under the
                    # column's own name. Nothing is computed here and nothing is
                    # renamed: a metric whose name differs from the summary table's
                    # column is a number a reviewer has to translate.
                    # log_run_metrics drops a non-numeric value by KEY and counts
                    # it, which is what happens to a NULL cost_per_eligible -- it is
                    # visible in the summary table and it is not a metric.
                    tracking.log_run_metrics(
                        {_k: _v for _k, _v in _record.items() if _k != "config_name"})
                    tracking.end_run(status="FINISHED")

                tracking.end_run(
                    status="FINISHED" if run_error == 0 else "FAILED",
                    artifacts=[ablation_summary_json(db_path)])

            console.out("=" * 70)
            console.out()
        except BaseException as _exc:
            # ══ THE ONE HANDLER FOR ALL THREE ABRUPT PATHS ═════════════════
            #
            # Ctrl-C (KeyboardInterrupt, re-raised by the handler above),
            # SIGTERM (SystemExit, raised by the entry point's handler) and an
            # ordinary crash all arrive here, and all three owe the same two
            # things: a per-configuration row that says how it ended, and the
            # closing block with the study's three degradation counters in it.
            #
            # IT USED TO OWE NEITHER TO SIGTERM. `except KeyboardInterrupt`
            # does not catch SystemExit, so a `docker stop` exited 143 with the
            # open configuration still reading RUNNING and no closing block --
            # measured, which is what moved both here.
            #
            # STEP 5 IS NOT IN A `finally`, so nothing below the `try` runs on
            # any of these paths. That is deliberate: Step 5 GENERATES THE
            # SUMMARY and CLEARS THE CHECKPOINT, and doing either after a crash
            # would overwrite ablation_summary.json with means over a prefix
            # and then delete the resume state that makes the retry free.
            #
            # NOTHING HERE RAISES. `_finalize_run` never does (see
            # RUN_RECORD_FAILURES), `print_study_close` only formats, and
            # `tracking.end_run` swallows and counts -- so the exception that
            # brought us here reaches the operator rather than being replaced
            # by a failure in the code that was trying to explain it.
            if open_run_id is not None:
                # NO `stop_reason` ON THE CRASH PATH, AND THAT IS THE POINT
                # OF THE COLUMN. KILLED means the process did not get to the
                # end; a spend latch that happened to be set when a SIGTERM
                # arrived did not cause this, and storing it would attribute a
                # crash to a budget. The two facts are separable and the row
                # keeps them separate.
                _finalize_run(open_run_id, time.time() - study_start,
                              RUN_STATUS_KILLED, db_path=db_path)
                open_run_id = None
            print_study_close(
                STUDY_STATUS_INTERRUPTED
                if isinstance(_exc, (KeyboardInterrupt, SystemExit))
                else STUDY_STATUS_CRASHED,
                time.time() - study_start,
                run_success, run_error, run_cancelled, db_path=db_path)
            # `FAILED` IS THE CLOSEST TRUE STATEMENT MLflow'S THREE-MEMBER
            # VOCABULARY CAN CARRY for a study that did not finish, and it is
            # what oncotriage/batch/runner.py:main() records for a Ctrl-C.
            tracking.end_run(status="FAILED")
            raise

#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
