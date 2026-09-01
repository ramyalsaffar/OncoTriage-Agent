"""SQLite schema and the inference logger.

Moved out of ``14- Database Logger.py`` by item 20c, pass 2b.
``14- Database Logger.py`` survived as an explicit re-export shim over this
module because Files 17, 25, 26, 32, 36, 37, 38, 40 and 45 exec-chained it, and
IS DELETED AS OF PASS 20e: all nine were measured and none is a chainer any more
(17, 25 and 26 became thin entry points; 32, 36, 37, 38 and 40 became modules
under ``tests/`` in pass 20d-1; 45 became ``oncotriage/fixtures/capture.py``).

THE SHIM'S ``log_inference`` WRAPPER WENT WITH IT, and the argument for it is
kept here because the argument is about THIS function. The wrapper was
``log_inference(result, patient_data, db_path=None)`` with
``db_path = globals().get("inferences_path")`` — defined inside the exec'd text,
so its ``__globals__`` WAS the shared namespace and the lookup stayed live. That
mattered because five files rebound ``inferences_path`` at a temporary database
and only then loaded File 14; without the wrapper all five would have written
real rows into the real ``inferences.db`` while printing the name of the
temporary file each thought it was using. Silent in both directions.

By pass 20d-1 all five ALSO passed ``db_path=`` explicitly and asserted on the
path this function returns, which is why the wrapper's removal changes nothing:
File 14's own docstring recorded "no remaining consumer in the repository" a
pass before it was deleted. THE RULE THAT OUTLIVES IT: this function must keep
taking the database as an argument and must keep RETURNING the path it wrote to,
because those two together are what let an isolation test assert where it wrote
instead of trusting that it wrote somewhere else.

TWO DELIBERATE CHANGES, and they are the reason this pass was not a straight move
--------------------------------------------------------------------------------

1. ``log_inference`` TAKES ``db_path``.

   It used to read a bare ``inferences_path`` out of the shared namespace. Five
   files rebind that name at a temporary database and only then load File 14 —
   36, 37, 38, 40 and 45 — and that redirect is the only thing standing between
   a test run and the production inferences.db. A module function cannot see a
   caller's globals, so the redirect would have gone quiet the moment this file
   became a module: five tests writing real rows into the real database, each
   still printing the name of the temporary file it thought it was using. The
   failure mode is silent in both directions, which is why the fix is a
   parameter and not a global.

   ``None`` means ``oncotriage.paths.inferences_path``, or
   ``ONCOTRIAGE_INFERENCES_DB`` when that is set — see
   ``resolve_inference_db_path`` for the three-tier order and for why the
   argument deliberately outranks the variable. The five test files pass the
   path explicitly, which is the only mechanism now that the shim's late-binding
   wrapper is gone (pass 20e).

2. ``_resolve_primary_cancer`` LEFT ALTOGETHER (pass 20c-2c).

   Pass 2b changed it from reading ``_CANCER_REGISTRY`` — which
   "13- LangGraph Agent.py" assigned at its own line 64, a layering violation
   that left the function raising NameError in any chain loading 14 without 13 —
   to calling ``load_registry()``. Pass 2c finished the job: it is a domain
   question about SNOMED and ICD-10 codes and it opens no database, so it now
   lives in ``oncotriage/registries/primary_cancer.py`` and is IMPORTED here.

   That direction is the point. The agent's three terminal nodes call it too, and
   while it lived here the agent depended on the storage layer for a registry
   lookup. Both callers now import it from the registries package and neither
   imports the other. This module re-exports it, which is what
   ``tests/test_fhir_birth_date_and_demographics.py`` section 9b reaches — the
   only place in the repository that touches the storage layer without the
   agent.

WHAT IMPORTING THIS MODULE DOES
-------------------------------
Nothing observable. Item 20b turned schema creation into a function precisely so
that loading this file would stop opening the production database, and that
holds here: no connection, no CREATE, no path resolution, no registry
construction. ``load_registry()`` — reached through ``primary_cancer`` — builds
on first CALL and imports the ICD-10-CM release inside its own body.

COST ACCOUNTING FAILS LOUDLY, and the ordering that makes it do so is
load-bearing: ``get_model_cost()`` is called BEFORE ``log_inference``'s try
block, so an unpriced model raises ``UnknownModelPricingError`` out to the caller
instead of being swallowed by the broad except that exists to keep a database
fault from killing the pipeline. Never move it inside, and never wrap it in a
recovery path.
"""

import json
import os
import sqlite3
import threading
import time
import urllib.parse
from collections import Counter
from datetime import datetime
from typing import Dict, NamedTuple

from oncotriage import paths
from oncotriage import settings
from oncotriage import config as _config
from oncotriage.config import (
    CROSS_ENCODER_MODEL,
    MATCHING_MODEL,
    PRICING_CONFIG,
    SQLITE_BUSY_TIMEOUT_SECONDS,
    SQLITE_JOURNAL_MODE,
    SQLITE_PAGE_SIZE,
    SQLITE_WRITE_MAX_ATTEMPTS,
    SQLITE_WRITE_RETRY_BASE_DELAY,
)
from oncotriage.constants import UNKNOWN_DATE
from oncotriage.registries.primary_cancer import _resolve_primary_cancer
from oncotriage.utils import deduplicate_by_display, get_model_cost
from oncotriage.observability import console, get_logger

log = get_logger(__name__)


#------------------------------------------------------------------------------


def resolve_inference_db_path(db_path=None):
    """The database ``log_inference`` will write to for this call.

    Three tiers, first match wins:

        1. ``db_path`` -- an explicit argument, returned unmodified;
        2. ``ONCOTRIAGE_INFERENCES_DB`` (pass 20c-3i);
        3. ``oncotriage.paths.inferences_path``, the configured production
           database, resolved on this call -- see that module for why
           resolution is lazy.

    Returns:
        The path string.

    WHY TIER 2 EXISTS. "17- FastAPI Server.py" calls ``log_inference(result,
    patient_data)`` with no path, and it cannot sensibly do otherwise -- it is a
    server handling requests, not a test that knows where its output belongs. So
    every run of "18- FastAPI Server Test.py" or "19- FastAPI Server Batch
    Test.py" against a live server wrote real rows into the real production
    database. That is not hypothetical: six such rows dated 2026-08-05 are in
    it, and they changed which query "16- Database Query.py" dies at.

    The server is a separate process, so the redirect has to be settable from
    OUTSIDE the process that decides to log. An environment variable is the only
    channel that reaches it:

        ONCOTRIAGE_INFERENCES_DB=/tmp/t.db python "17- FastAPI Server.py"

    ``oncotriage/monitoring/drift.py:resolve_drift_db_path`` honours the same
    variable, deliberately without importing this function -- see its docstring.

    THIS FUNCTION DOES NOT CONSULT THE EXEC NAMESPACE, and that asymmetry is on
    purpose. The shim's ``log_inference`` wrapper is what reads
    ``globals().get("inferences_path")``; this one always answers "what does a
    caller that passed nothing get", which is exactly the question the five
    isolation tests need answered in order to show that passing the scratch path
    is doing any work. If this resolved through the namespace too, those tests
    would be comparing a value against itself.

    THE ARGUMENT STILL WINS OVER THE VARIABLE, and that ordering is what keeps
    those five tests meaningful. They pass an explicit scratch path and assert
    on the path returned; if the variable outranked the argument, a stray export
    in the operator's shell would silently redirect a test that had asked for
    somewhere specific, and the assertion would report the redirect as the
    answer it wanted.

    It resolves and returns; it opens nothing. Calling it is safe on a machine
    with a database it must not touch. The one thing it can RAISE is a
    RuntimeError from ``resolve_inferences_db`` when the variable names a path
    whose parent directory is absent -- deliberately, because both callers
    resolve outside their try block so a configuration defect reaches the
    operator rather than being swallowed as a logging fault.
    """
    if db_path is not None:
        return db_path
    override, _source = settings.resolve_inferences_db()
    if override is not None:
        return override
    return paths.inferences_path

#------------------------------------------------------------------------------


# Item 20b: schema creation is a function, not a module body.
#
# Loading this file used to open the production database and run every CREATE
# TABLE and every additive migration as a side effect of the exec chain. Nine
# other files load 14 or are loaded beside it; each of them was touching
# inferences.db just by being read. A file must be loadable without writing to
# anything.
#
# What moved: only the executable statements. The two COLUMN_ADDITIONS dicts
# stay at module level, byte for byte, because they are pure data and because
# tests/test_storage_ecog_logging.py reads INFERENCE_COLUMN_ADDITIONS directly. The
# migration loops are unchanged; they are what adds a column without destroying
# rows, and items 29b and 20a both depend on that.
#
# The SQL is still written flush against column 0 inside its triple-quoted
# strings even though it now sits inside a function. Indenting those lines
# would change the CREATE text SQLite stores in sqlite_master.sql, so the
# schema would no longer be identical to the one this file produced before.


#------------------------------------------------------------------------------


# THE SCHEMA ERA THIS CODE CREATES, STAMPED INTO EVERY DATABASE IT OPENS.
#
# SQLite carries a caller-owned 32-bit integer in its file header, readable with
# `PRAGMA user_version` and costing no table, no row and no migration. It was 0
# on every database this project has ever written -- the default -- so no tool
# could ask a FILE which schema era it held; the only way to find out was to
# read `PRAGMA table_info` on three tables and compare the result against a
# reading of this module. That is a derivation a person does, and it is why the
# gpt4o rename could leave sixteen queries pointing at columns nobody noticed
# were gone.
#
# THE RULE, AND IT IS THE WHOLE VALUE OF THE NUMBER: BUMP THIS IN THE SAME
# COMMIT THAT CHANGES THE SCHEMA. An entry added to INFERENCE_COLUMN_ADDITIONS,
# TRIAL_MATCH_COLUMN_ADDITIONS or RUN_COLUMN_ADDITIONS, a new table, a new
# index, a rename -- each is a new era. A stamp that lags the schema is worse
# than no stamp, because a reader acts on it.
#
# IT STARTS AT 1 AND NOT AT 0. Zero is what SQLite writes into a file nobody
# has stamped, so `user_version = 0` has to keep meaning "unstamped, era
# unknown, ask table_info" -- and it does: that is what every database written
# before this constant existed reports, including the production file. Numbering
# eras from 1 leaves that reading unambiguous.
#
# WHAT IT IS NOT. It is not a migration ledger and nothing branches on it: the
# migrations here are idempotent and presence-driven (`IF NOT EXISTS`, a
# `PRAGMA table_info` check before each ALTER), so they do not need to know
# where they started. It answers one question -- which era is this file -- for
# a human, a support script, or a future tool that must refuse a database it
# does not understand.
# ERA 7: `runs.stop_reason`, added with RUN_COLUMN_ADDITIONS and its migration
#        loop. A run stopped by the spend gate and a run stopped by an operator
#        are both STOPPED -- the campaign covers a prefix of the cohort, the
#        checkpoint is intact, a resume continues -- and only this column says
#        which. Additive TEXT, NULL on every existing row and on every run that
#        was not stopped, never backfilled.
# ERA 6: `inferences.llm_classifier_input_tokens_estimated` and
#        `inferences.llm_classifier_input_budget`, added together with
#        INFERENCE_COLUMN_ADDITIONS -- one era, two columns, on era 5's
#        precedent: the number counts schema changes, not columns, and a
#        measurement without its recorded denominator is uninterpretable, so
#        the pair is one change. They give the INPUT guard the per-row scalar
#        the OUTPUT guard has had since its own era, and they close the two
#        populations llm_classifier_packing cannot answer for: every Stage 5
#        failure return (that report is published on the success return only)
#        and every row of the shipped per-trial call mode (which bypasses the
#        packer by design). Both additive INTEGER, both NULL on every existing
#        row, neither backfilled.
# ERA 5: TWO COLUMNS IN ONE COMMIT, which is what an era is -- the number
#        counts schema changes, not columns. `trial_matches.criteria_split`
#        carries the indexer's own split method through onto every trial that
#        reached Stage 5, so a campaign's exposure to the trials whose whole
#        criteria block was sent as INCLUSION text becomes a query instead of
#        an unanswerable question; the field existed only inside a Qdrant
#        payload before. `runs.note` carries the operator's stop note, which
#        was read, printed and then discarded, so a STOPPED row said nothing
#        about why. Both are additive TEXT, both are NULL on every existing row,
#        and neither is backfilled.
# ERA 4: `runs.matching_call_mode`, added with RUN_COLUMN_ADDITIONS and its
#        migration loop. It is the RUN-level twin of era 3's per-row column and
#        it is not redundant with it: era 3 records what each patient row was
#        produced under, and this records what the run was STAMPED with, which
#        is the value `run_fingerprint` gates a resume on and the value
#        `campaign_summary` stitches on. A disagreement between the two is a run
#        whose flag moved mid-process, which nothing could state before.
# ERA 3: `inferences.matching_call_mode`, added with INFERENCE_COLUMN_ADDITIONS
#        and its migration loop. It records whether Stage 5 sent one trial per
#        request or several, which llm_classifier_packing cannot state on its
#        own once per-trial mode can bypass the packer.
# ERA 2: `runs.resumed`, added with RUN_COLUMN_ADDITIONS and its migration loop.
# ERA 1: the constant's own introduction -- the schema as it stood then.
SCHEMA_USER_VERSION = 7


#------------------------------------------------------------------------------


# THE FILE'S IDENTITY, STAMPED BESIDE ITS ERA.
#
# SQLite carries a SECOND caller-owned 32-bit integer in the same header,
# `PRAGMA application_id`, and it answers a question `user_version` cannot: not
# "which era is this file" but "is this file OURS AT ALL". The two are read
# together and neither is sufficient alone, because `user_version` is a bare
# integer with no owner -- a database written by any other tool that uses the
# field carries a number in it that means something else entirely, and reading
# that as an oncotriage era is exactly the misreading the refusal below exists
# to prevent.
#
# THE VALUE IS THE ASCII OF "ONC1" READ AS A BIG-ENDIAN 32-BIT INTEGER --
# 0x4F4E4331 -- which is the convention SQLite's own magic.txt registry uses.
# It fits in a signed 32-bit integer (1330529073 < 2147483647), which matters:
# the field IS signed, and a value with the top bit set reads back negative.
#
# ZERO IS "UNSTAMPED", NOT "FOREIGN". That is SQLite's default, so it is what
# every database this project wrote before this constant existed reports --
# including the production file -- and it is also what a brand-new empty file
# reports for the moments before the stamp lands. Both are ours; a foreign
# application_id is a NON-ZERO value that is not this one.
ONCOTRIAGE_APPLICATION_ID = 0x4F4E4331

APPLICATION_ID_UNSTAMPED = 0
"""What ``PRAGMA application_id`` reports on a file nobody has stamped.

A NAMED CONSTANT rather than a bare ``0`` at the two comparisons that read it,
because the number means "unstamped" and not "the application whose id is
zero", and those two readings lead to opposite decisions."""


class IncompatibleDatabaseError(RuntimeError):
    """This code refuses to open the database at that path, and says why.

    A ``RuntimeError`` subclass and deliberately NOT a ``sqlite3.Error`` or a
    ``ValueError``, on ``UnknownModelPricingError``'s and ``MissingTableError``'s
    precedent: every writer in this project wraps its database work in a broad
    ``except sqlite3.Error`` whose whole purpose is that a logging fault does not
    kill the pipeline, and a refusal that those handlers could swallow would be
    reported as a non-critical logging failure and then IGNORED -- which is the
    one outcome this refusal must not have.

    THE TWO CASES IT CARRIES, and they have different remediations:

      * A NEWER ERA. ``PRAGMA user_version`` is HIGHER than
        ``SCHEMA_USER_VERSION``, i.e. this file was last migrated by a version of
        this module that knows more than this one does. See the refusal for why
        that stopped being tolerated.
      * A FOREIGN FILE. ``PRAGMA application_id`` is non-zero and is not
        ``ONCOTRIAGE_APPLICATION_ID``. Some other application created this file
        and this code was about to CREATE FIVE TABLES IN IT.
    """


def assert_database_is_compatible(conn, db_path):
    """Refuse ``conn``'s database if this code must not write to it.

    Returns ``(application_id, user_version)`` -- both read once, so the caller
    that goes on to stamp does not re-read them.

    RUNS BEFORE ANYTHING MUTATES THE FILE, which is the whole design and not an
    ordering preference. A refusal has to leave the file exactly as it found it,
    and both of the things ``initialize_database`` does before its first CREATE
    -- setting the page size and converting the journal to WAL -- write to the
    header. So this is the first statement after the connect.

    THE IDENTITY CHECK COMES FIRST, and that ordering is load-bearing: on a
    foreign file the ``user_version`` integer belongs to somebody else's
    numbering, so comparing it against ``SCHEMA_USER_VERSION`` produces a true
    number and a false meaning. "This is not our database" has to be settled
    before "which era of ours is it" can be asked at all.

    WHY THE ERA CHECK REFUSES UPWARD AND NOT DOWNWARD.

      DOWNWARD (an OLDER file, this code newer) is the ordinary case and is
      permissive by design: the migrations here are additive and presence-driven
      (``IF NOT EXISTS``, a ``PRAGMA table_info`` check before every ALTER), so
      newer code opening an older file adds what is missing, stamps forward, and
      every row already there is untouched. That is what the additive mechanism
      is FOR, and refusing it would make every schema addition a manual
      migration.

      UPWARD (a NEWER file, this code older) is refused, and this REPLACES a
      permissive branch that left the stamp alone and carried on. The argument
      that branch made -- "this schema is strictly additive, so a file stamped 7
      still HAS everything era 7 gave it" -- is a true statement about the eras
      that EXIST and a promise about eras that do not exist yet, made by the code
      that cannot see them. What it cannot survive: an era that adds a NOT NULL
      column with a default, that renames one (this project has renamed columns
      before -- the gpt4o rename moved nine of them), or that changes what a
      value in an existing column MEANS. Older code meeting any of those writes
      rows that are well-formed, that raise nothing, and that the newer readers
      cannot interpret -- silent corruption of the one artifact every published
      number is computed from, which is the exact class of defect this project
      exists to remove. The cost of refusing is one loud message and one deliberate
      operator action; the cost of tolerating is discovered later or never.

    IT DOES NOT DECIDE ANYTHING ABOUT A FILE THAT IS NOT THERE. ``sqlite3``
    creates an empty database on connect, and an empty database reports 0 and 0,
    so a fresh file passes both checks and is stamped by the caller.
    """
    application_id = conn.execute("PRAGMA application_id").fetchone()[0]
    user_version = conn.execute("PRAGMA user_version").fetchone()[0]

    if (application_id != APPLICATION_ID_UNSTAMPED
            and application_id != ONCOTRIAGE_APPLICATION_ID):
        raise IncompatibleDatabaseError(
            f"{db_path} is not an oncotriage database and this code was about "
            f"to create tables in it.\n"
            f"    PRAGMA application_id: {application_id} "
            f"(0x{application_id & 0xFFFFFFFF:08X}); this project stamps "
            f"{ONCOTRIAGE_APPLICATION_ID} "
            f"(0x{ONCOTRIAGE_APPLICATION_ID:08X}).\n"
            f"    Nothing has been written to it. The usual cause is a mistyped "
            f"path or a stale ONCOTRIAGE_INFERENCES_DB pointing at some other "
            f"tool's SQLite file; check the path and try again.")

    if user_version > SCHEMA_USER_VERSION:
        raise IncompatibleDatabaseError(
            f"{db_path} was written by a NEWER version of oncotriage and this "
            f"one refuses to write to it.\n"
            f"    The file reports schema era {user_version}; this code is era "
            f"{SCHEMA_USER_VERSION}.\n"
            f"    Nothing has been written to it. Writing anyway would put rows "
            f"in it that this code cannot describe -- a column era "
            f"{SCHEMA_USER_VERSION} does not know about is left NULL by every "
            f"row it writes, and a column whose MEANING moved is worse than "
            f"that.\n"
            f"    Either run the newer code against this file, or archive it "
            f"and let this one build a fresh database:\n"
            f"        mv {db_path!r} {db_path!r}.era{user_version}-archive")

    return application_id, user_version


# ---------------------------------------------------------------------------
# THE SERVING-DATABASE READINESS PROBE
# ---------------------------------------------------------------------------

SERVING_DATABASE_CHECK = "inference_database"
"""The name this probe's check carries in a readiness report. Written once
because ``oncotriage/api/server.py`` folds the check into
``serving_readiness()`` and a test asserts on the name; two spellings of one
check name is a test that passes against a report that names something else."""


def probe_serving_database(db_path=None):
    """Can this process WRITE the inference database? A check dict, never a raise.

    Returns ``{"name": SERVING_DATABASE_CHECK, "ok": bool, "detail": str}`` --
    the shape ``oncotriage/agent/readiness.py`` builds its report from.

    ===================================================================
    WHY THIS EXISTS: A REFUSED DATABASE IS INVISIBLE AT EVERY OTHER SURFACE
    ===================================================================

    ``assert_database_is_compatible`` refuses a file written by a NEWER schema
    era, and that refusal is correct. What happens next is not.
    ``_write_inference_row``'s handler catches ``Exception`` -- deliberately, so
    a logging fault cannot kill a paid pipeline result -- and
    ``IncompatibleDatabaseError`` is a ``RuntimeError``, so it is caught there
    like any other. The write returns ``ok=False``.

    MEASURED, not reasoned about: with a newer-era file in place,
    ``POST /match`` runs the whole pipeline, makes its billed Stage 5 calls,
    returns **HTTP 200 with a complete, correct body**, and stores nothing. The
    client cannot tell. ``GET /health`` probed the MeSH lookups and the trial
    index and knew nothing about the database, so it stayed **green**. The only
    trace anywhere is one ERROR line per request in the server log.

    So the failure mode is not "the server is down" -- which an operator would
    notice -- but "the server bills for every request and keeps no record",
    which nothing on any surface reports. That is what this probe is for, and it
    is why the answer belongs at ``/health``: a `curl -f` healthcheck is the one
    thing watching a container that nobody is reading the logs of.

    ===================================================================
    IT LIVES IN THE STORAGE LAYER AND IS COMPOSED BY THE CALLER
    ===================================================================

    The obvious home is inside ``serving_readiness()``. It cannot go there:
    ``oncotriage/agent/retrieval.py`` imports ``agent.readiness``, so a
    storage import inside that module puts the whole storage layer -- and every
    module it pulls in -- into the AGENT's import graph. Pass 20c-2c moved
    ``_resolve_primary_cancer`` OUT of this file precisely to remove that
    coupling in the other direction; adding it back pointing the other way is
    the same edge. ``oncotriage/api/server.py`` already imports both and is
    where the two are joined.

    ===================================================================
    IT MUST NOT CREATE THE FILE IT IS ASKED ABOUT
    ===================================================================

    ``sqlite3.connect`` CREATES a missing database. A probe that answered "the
    file is fine" by bringing it into existence would be File 41's
    guard-that-creates-its-own-evidence defect, and on a container whose data
    volume failed to mount it would silently establish an empty database at the
    mount point. So an absent file is answered from ``os.path.exists`` and never
    opened, and an existing one is opened through a ``mode=ro`` URI.

    AN ABSENT FILE IS ``ok=True`` and that is deliberate: a fresh deployment has
    no inference database until its first write, and the first write creates a
    correct one. Reporting it NOT ready would make every clean bring-up
    unhealthy until somebody sent a request.

    A FILE THAT CANNOT BE READ IS ``ok=False``. Unlike the index probe's
    ``unverifiable`` state -- where "cannot tell" is a network question -- a
    local database this process cannot open read-only is one it certainly cannot
    write, so there is no third state to be careful about.
    """
    try:
        resolved = resolve_inference_db_path(db_path)
    except Exception as exc:
        # resolve_inferences_db() RAISES on a configured path whose parent
        # directory is absent -- a configuration defect, and exactly the kind
        # this endpoint exists to name. It must arrive as a check, not as a 500
        # from the health endpoint.
        return {
            "name": SERVING_DATABASE_CHECK,
            "ok": False,
            "detail": (f"the inference database path could not be resolved: "
                       f"{type(exc).__name__}: {exc}"),
        }

    if not os.path.exists(resolved):
        # AN ABSENT FILE IS ONLY ``ok`` IF ITS PARENT CAN HOLD IT. This branch
        # said "the first write will create it" unconditionally in its first
        # draft, which is the claim it cannot make from the file alone:
        # sqlite3 creates a missing FILE and refuses a missing DIRECTORY, so an
        # absent parent -- a container whose data volume failed to mount, a
        # mistyped ONCOTRIAGE_INFERENCES_DB -- reported ready and then lost
        # every row, which is the exact failure this probe exists to name.
        _parent = os.path.dirname(resolved) or "."
        if not os.path.isdir(_parent):
            return {
                "name": SERVING_DATABASE_CHECK,
                "ok": False,
                "detail": (f"{resolved} does not exist and its directory "
                           f"{_parent} is not there either, so the first write "
                           f"cannot create it. The usual causes are a data "
                           f"volume that failed to mount and a mistyped "
                           f"ONCOTRIAGE_INFERENCES_DB."),
            }
        if not os.access(_parent, os.W_OK):
            return {
                "name": SERVING_DATABASE_CHECK,
                "ok": False,
                "detail": (f"{resolved} does not exist and its directory "
                           f"{_parent} is not writable by this process, so the "
                           f"first write cannot create it."),
            }
        return {
            "name": SERVING_DATABASE_CHECK,
            "ok": True,
            "detail": (f"{resolved} does not exist yet; the first write will "
                       f"create it in {_parent} at schema era "
                       f"{SCHEMA_USER_VERSION}."),
        }

    conn = None
    try:
        # read_only=True is a mode=ro URI, so nothing is created and nothing
        # is written -- and it goes through _open_connection so this module's
        # "every connection carries the busy timeout" invariant holds. A
        # read-only connection can still answer both PRAGMAs.
        conn = _open_connection(resolved, read_only=True)
        assert_database_is_compatible(conn, resolved)
    except IncompatibleDatabaseError as exc:
        # THE MESSAGE IS CARRIED VERBATIM rather than summarised, on the
        # precedent readiness.py already set for DegradedDependencyError: it
        # already names the file, both eras and the archive command, and a
        # second wording here is a second thing to keep in step.
        return {"name": SERVING_DATABASE_CHECK, "ok": False, "detail": str(exc)}
    except Exception as exc:
        return {
            "name": SERVING_DATABASE_CHECK,
            "ok": False,
            "detail": (f"{resolved} could not be opened read-only: "
                       f"{type(exc).__name__}: {exc}. A database this process "
                       f"cannot read is one it cannot write."),
        }
    else:
        return {
            "name": SERVING_DATABASE_CHECK,
            "ok": True,
            "detail": f"{resolved} is writable at schema era {SCHEMA_USER_VERSION}.",
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                # A close that fails leaks one descriptor and says nothing
                # about readiness. Reporting NOT ready over it would make a
                # healthy server unhealthy for a reason the operator cannot act
                # on.
                pass


# ---------------------------------------------------------------------------
# STARTING A CAMPAIGN ON A FRESH DATABASE -- THE PROCEDURE, AND WHY
# ---------------------------------------------------------------------------
#
# The migrations in this file are additive: a column added in era N appears on
# an existing database as an ALTER, and every row written before it keeps NULL.
# That is right for a database being carried forward, and it is the WRONG shape
# for the file a campaign's published numbers are computed from, for three
# reasons this schema cannot fix in place:
#
#   1. NULL IS AMBIGUOUS ACROSS AN ERA BOUNDARY. In a carried-forward file,
#      `matching_call_mode IS NULL` means "written before era 3" for some rows
#      and nothing at all for others, and no query can separate a campaign's
#      rows from the ones that preceded it except by run attribution -- which
#      itself is NULL on every row written before the run-identity pass.
#   2. THE PAGE SIZE CANNOT BE CHANGED IN PLACE. SQLITE_PAGE_SIZE reaches a
#      database only at creation; an existing file keeps 4096 until it is
#      VACUUMed, and VACUUM rewrites the whole file.
#   3. THE JOURNAL MODE CONVERTS ON FIRST OPEN, so a carried-forward file spends
#      its history in whatever mode it was created in and only the tail of it in
#      WAL.
#
# THE PROCEDURE, and it is two commands:
#
#     mv "02- Data/03- Inferences Storage/inferences.db" \
#        "02- Data/03- Inferences Storage/inferences-2026-08-archive.db"
#     # then start the campaign normally; the first write builds the new file
#
# The first write is `start_run_record`, which calls `initialize_database`, which
# creates a file with ALL columns present from the first row, all five tables,
# WAL from the first write, page_size SQLITE_PAGE_SIZE, both header stamps, and
# every index. Nothing else has to be done and no migration is run.
#
# THE ARCHIVE IS MOVED, NOT DELETED, and the distinction is the whole point: it
# is the only copy of every historical row, it is what a reader compares the new
# campaign against, and it is still readable by every query in
# `oncotriage/storage/queries.py` through `--db`.


#------------------------------------------------------------------------------


# Schema migration for the inferences table.
#
# CREATE TABLE IF NOT EXISTS is a no-op once the table exists, so columns added
# after the first run must be applied explicitly. Rows written before a column
# existed keep NULL, which is the honest value: the counter was not recorded,
# as opposed to having been recorded as zero.
INFERENCE_COLUMN_ADDITIONS = {
    "not_evaluable_trials": "INTEGER",   # trials the model could not assess at all
    "cross_vocab_remaps":   "INTEGER",   # criterion labels resolved to not_evaluable
    # Which layer resolved the patient's MeSH C04 identity ("snomed",
    # "icd10+fuzzy_synonym", ...), or why none did ("pan_cancer_only",
    # "unmapped", "no_cancer_condition", "no_valid_condition",
    # "no_mesh_filter"). mesh_dropped = 0 is ambiguous on its own: it means
    # both "the filter found nothing to drop" and "the patient was never
    # resolved, so the filter never ran". This column separates the two.
    "mesh_resolution":      "TEXT",
    # Count of entries the model returned an evaluation for that were never in
    # the candidate set sent to it. THE DETECTOR EXISTS NOW: it runs per chunk
    # in node_llm_classifier_evaluation, drops every such entry before it can
    # be enriched or scored, and writes the total into
    # result["hallucinated_trials"] via _pipeline_provenance.
    #
    # FABRICATED ONLY, WHICH IS WHAT THE SENTENCE ABOVE HAS ALWAYS SAID. The
    # detector drops two kinds of entry and this column counts one of them: an
    # id that is in no candidate set of the run. The other kind -- an id in the
    # node's sent set but not in the chunk that answered, which is the model
    # answering the whole batch to every call of a SPLIT request -- is dropped
    # by the same code and counted only in the `out_of_set_entry` log event,
    # under cross_chunk_count / cross_chunk_nct_ids. It costs the patient
    # nothing (that id's own chunk answers it, or the reconciliation records it
    # as omitted) and folding it in here would make a split run's number
    # incomparable with an unsplit run's.
    #
    # 0 IS A MEASUREMENT AND NULL IS NOT. A normal run stores 0, which asserts
    # that every returned entry was compared against the candidate set and
    # every one belonged to it. NULL means no such comparison was completed --
    # a row written before the detector existed, or a run that ended at an API
    # failure, a refusal or an unparseable response, where Stage 5's success
    # return was never reached. Never fold the two together, and never default
    # this to 0 in a reader.
    "hallucinated_trials":  "INTEGER",
    # --- Retrieval and expansion degradation (item 11b) ---------------------
    # Stage 2 runs four retrieval channels behind one try/except each. Before
    # these columns existed, a channel that raised was printed and dropped, and
    # fusion continued on the survivors: a dense-search outage produced the
    # same stored row as a clean run. bm25_retrieved / vector_retrieved cannot
    # substitute — 0 means both "returned nothing" and "never returned".
    #
    # retrieval_channels holds the per-channel record as JSON:
    #   {"title": {"status": "ok", "count": 75, "error": ""},
    #    "dense": {"status": "failed", "count": 0, "error": "..."}}
    # status is one of File 13's CHANNEL_* constants: ok | failed | ablated |
    # empty_query. The scalars beside it are the same fact in queryable form,
    # with ablated channels excluded from "expected" so a bm25_only ablation is
    # not reported as a degraded run.
    #
    # NULL on every one of them means Stage 2 did not report, which is not the
    # same as a clean run — see _pipeline_provenance() in File 13.
    "retrieval_channels":           "TEXT",
    "retrieval_channels_expected":  "INTEGER",
    "retrieval_channels_ok":        "INTEGER",
    "retrieval_degraded":           "INTEGER",  # 1 = an expected channel did not return
    # Trials ranked into the fusion pool whose payload could not be recovered,
    # so they never reached Stage 3. The batch-scroll fallback that loses them
    # used to print a line and keep going.
    "retrieval_trials_lost":        "INTEGER",
    # Which query Stage 1 searched with: "mesh_expanded" or
    # "base_query_fallback". The fallback printed a WARNING and nothing else,
    # so the rate at which the pipeline ran without any MeSH expansion was not
    # recoverable from the database. Distinct from mesh_resolution, which says
    # why resolution failed rather than what the run then did.
    "query_expansion_path":         "TEXT",
    # Whether Stage 4's cancer site filter actually ran (1/0), and why not.
    # Stage 5's system prompt asserts to the model that disease relevance was
    # confirmed; that assertion is now conditional on this flag, so the flag
    # belongs in the record of the inference it shaped.
    "mesh_filter_applied":          "INTEGER",
    "mesh_filter_skip_reason":      "TEXT",
    # The same pair for the four Stage 4 filters that had a drop counter and no
    # marker. mesh_dropped got mesh_filter_applied because Stage 5's prompt
    # depends on it; stage_dropped, histology_dropped, age_dropped and
    # sex_dropped got nothing, so a 0 in any of those four columns meant both
    # "checked, nothing to drop" and "never checked" -- exactly the ambiguity
    # mesh_resolution was added to remove one column over.
    #
    # Each *_applied is 1/0/NULL on the same convention as mesh_filter_applied:
    # NULL is "Stage 4 did not report", never "did not run". Each
    # *_skip_reason is 'applied' when it ran, or one of that filter's own
    # constants in oncotriage/agent/state.py:
    #
    #   stage      'ablation_skipped' | 'no_patient_stage'
    #   histology  'ablation_skipped' | 'no_patient_histology'
    #   age        'no_patient_age'            (never ablated)
    #   sex        'sex_not_comparable'        (never ablated)
    #
    # THE AGE MARKER IS PATIENT-LEVEL AND DOES NOT COVER PER-TRIAL SKIPS. A
    # trial whose own min_age/max_age text will not parse is kept and skipped
    # individually; that is recorded in agent/filtering.py's AGE_PARSE_FAILURES
    # and in the Stage 4 log line's `age_unparsed`, not here. So
    # `age_filter_applied = 1 AND age_dropped = 0` still admits "every trial's
    # bounds were unreadable", and the counter is where that is answered.
    "stage_filter_applied":         "INTEGER",
    "stage_filter_skip_reason":     "TEXT",
    "histology_filter_applied":     "INTEGER",
    "histology_filter_skip_reason": "TEXT",
    "age_filter_applied":           "INTEGER",
    "age_filter_skip_reason":       "TEXT",
    "sex_filter_applied":           "INTEGER",
    "sex_filter_skip_reason":       "TEXT",
    # --- The one-glance degradation marker ---------------------------------
    #
    # 1 = at least one degradation signal fired on this run; 0 = none did;
    # NULL = this row did not come from a pipeline terminal node, or predates
    # the column. Derived in oncotriage/agent/terminal.py:_derive_degraded_run,
    # which is where the exact predicate and every term left out of it are
    # argued -- read that before querying this.
    #
    # IT IS A SUMMARY, NOT A MEASUREMENT. Every term is a column that is
    # already here (error, retrieval_degraded, mesh_filter_skip_reason,
    # llm_classifier_retries, not_evaluable_truncated), and this column exists
    # only so "was anything wrong with this run" is one predicate rather than
    # five with three different NULL conventions between them.
    #
    # 0 DOES NOT ASSERT THAT EVERY CHECK RAN. A run that ended at
    # node_no_candidates before Stage 4 stores 0 with mesh_filter_applied NULL
    # beside it. The stronger question -- "clean AND fully observed" -- is
    # `degraded_run = 0 AND retrieval_degraded IS NOT NULL AND
    # mesh_filter_applied IS NOT NULL`. This is deliberate and the reason is at
    # the derivation.
    #
    # WHY THIS IS A COLUMN WHILE INFERENCE_WRITE_FAILURES IS NOT. This is a
    # per-PATIENT observation and it belongs on the patient's row. That counter
    # is a per-RUN property, and a column recording that a row could not be
    # written is circular -- the argument is at the counter, and this pass did
    # not weaken it.
    "degraded_run":                 "INTEGER",
    # --- Age provenance (item 12) -------------------------------------------
    # The date this run computed patient ages against (DATA_SNAPSHOT_DATE,
    # File 03), and how much of the patient's birthDate the record carried.
    #
    # age was previously derived from datetime.now(), so the stored age — and
    # the Stage 5 prompt built from it — moved with the clock while
    # patient_data_hash, which keys on birth_date, stayed identical. Rows
    # written before this column existed keep NULL, which is honest: their
    # reference date was whatever day they happened to run and is not
    # recoverable from the row.
    #
    # birth_date_precision is "day" for an exact age; "month"/"year" mean the
    # age was imputed from a mid-range anchor (File 02) because the record was
    # partial, which HIPAA Safe Harbor de-identification produces by design;
    # "missing"/"unparseable"/"after_reference" mean age is NULL and say why.
    # NULL here means the parser did not report — not that the date was exact.
    "age_reference_date":           "TEXT",
    "birth_date_precision":         "TEXT",
    # --- ECOG performance status (File 07 parses it, File 13 carries it) -----
    # The score that reached the Stage 5 prompt, and how it was arrived at.
    # ECOG 0-1 or 0-2 gates nearly every interventional oncology trial, so these
    # move the verdict directly; without them a corpus whose observations all
    # postdate DATA_SNAPSHOT_DATE would match systematically worse with nothing
    # in the row explaining it.
    #
    # READ THE CONVENTION BEFORE QUERYING THESE. ecog_value is NULL in three
    # different situations and cannot separate them on its own:
    #
    #   ecog_selection IS NULL          the row predates this migration, or the
    #                                   caller logged a result that never came
    #                                   from a pipeline terminal node. Nothing
    #                                   is known about this patient's ECOG.
    #   ecog_selection = 'none_recorded'  the patient genuinely carried no ECOG
    #                                   observation. ecog_observations_found = 0.
    #   ecog_selection = 'all_after_reference_date',
    #                   'undated_ambiguous'
    #                   or 'all_before_primary_diagnosis'
    #                                   observations exist but none was usable.
    #                                   ecog_observations_found >= 1 says how many.
    #                                   The third of those is the one that is NOT
    #                                   a date-handling problem: the observation
    #                                   is well-formed and inside the snapshot,
    #                                   and it was refused because it predates
    #                                   the patient's primary cancer diagnosis --
    #                                   a performance status for a person who did
    #                                   not yet have the disease. A query that
    #                                   lumps it with the other two will read a
    #                                   correctness refusal as a snapshot-date
    #                                   fault.
    #
    # THE VOCABULARY IS oncotriage.constants.ECOG_SELECTION_VALUES AND THIS
    # COLUMN HAS NO CHECK CONSTRAINT, on `matching_provider`'s and
    # `criteria_split`'s footing. `storage` may not import `fhir`, and the
    # producer is `oncotriage/fhir/parser.py`; a constraint written out here
    # would be a second copy of that vocabulary with nothing failing when the
    # two disagree. The names moved to `oncotriage.constants` for exactly that
    # reason -- it imports nothing, so the producer, this reader, the dashboard
    # and the drift metric can share one spelling -- and this comment is prose
    # about them rather than a second declaration of them.
    #
    # So: absence is `ecog_selection = 'none_recorded'`, NEVER
    # `ecog_value IS NULL`. And a score of 0 is a real, fully-active patient --
    # the most eligible there is -- so ecog_value = 0 must never be treated as
    # missing either. Both confusions are the ones this column set exists to
    # prevent, which is why the selection path is stored beside the value rather
    # than being derivable from it.
    "ecog_value":                   "INTEGER",
    "ecog_selection":               "TEXT",
    "ecog_observations_found":      "INTEGER",
    #
    # ecog_date is HOW OLD the score is: the effective date of the observation
    # that was actually used. Nothing else in the row carries it, and it is not
    # a detail -- measured over this cohort the median selected observation is
    # roughly 17.7 years old, so a performance status that gates nearly every
    # trial is routinely being read off a reading older than the disease.
    #
    # IT IS A DATE OR IT IS NULL, and it follows ecog_value's convention rather
    # than inventing a second one: NULL is ambiguous on its own and
    # ecog_selection is what resolves it.
    #
    #   ecog_selection IS NULL          the row predates this migration, or the
    #                                   result never came from a terminal node.
    #   ecog_selection = 'none_recorded'
    #                   or 'all_after_reference_date'
    #                   or 'undated_ambiguous'
    #                   or 'all_before_primary_diagnosis'
    #                                   no observation was used, so there is no
    #                                   date to report. ecog_value is NULL too.
    #                                   The last of the four is the one where an
    #                                   observation existed, was well-formed AND
    #                                   was dated: its date is deliberately not
    #                                   reported, because this column means "how
    #                                   old is the score being used" and there is
    #                                   no score being used. The date it would
    #                                   have carried is on the patient record as
    #                                   ecog_performance_status['date'] until the
    #                                   refusal drops it, and the anchor it lost
    #                                   to is primary_diagnosis_date beside it --
    #                                   neither is a column, because neither is a
    #                                   fact about a score this row published.
    #   ecog_selection = 'undated_single'
    #                                   AN OBSERVATION WAS USED AND HAD NO DATE.
    #                                   ecog_value is a real score; ecog_date is
    #                                   NULL because the source carried nothing
    #                                   datelike. This is the one row shape
    #                                   where a NULL date sits beside a
    #                                   non-NULL score, and the pair is exactly
    #                                   what identifies it.
    #   ecog_selection = 'most_recent_on_or_before_reference_date'
    #                                   a dated observation was used and this is
    #                                   its date.
    #
    # WHY THE PARSER'S UNKNOWN_DATE IS NOT STORED. The parser writes that
    # literal into an Observation with neither effectiveDateTime nor
    # effectivePeriod.start, and the undated_single path can select such an
    # observation, so the value genuinely reaches this writer. Storing it would
    # put a non-date in a date column, and not harmlessly: SQLite compares TEXT
    # lexically, "unknown" sorts after every ISO digit, so `ORDER BY ecog_date
    # DESC` and every `ecog_date > '2020'` would rank the one reading with NO
    # date as the NEWEST of all -- the exact opposite of the truth, in the one
    # column whose entire purpose is measuring staleness. date(ecog_date) and
    # julianday(ecog_date) would meanwhile return NULL for it without comment.
    # So it is mapped to NULL here and the fact that an observation WAS used
    # survives in ecog_selection, which is where this column set has always kept
    # that distinction.
    #
    # THE STRING IS STORED AS THE SOURCE WROTE IT and is not reformatted: FHIR
    # dateTime is legally YYYY, YYYY-MM, YYYY-MM-DD or a full offset-bearing
    # instant, and this corpus carries the last of those. Normalising to a day
    # would impute precision the record does not have -- the same reason
    # birth_date_precision exists a few columns up. A reader wanting a day
    # should use SQLite's date(), which reads the leading YYYY-MM-DD and
    # returns NULL for a year-only or month-only value rather than guessing.
    #
    # THE ONE VALUE THIS COLUMN CAN HOLD THAT IS NEITHER A DATE NOR NULL, stated
    # rather than papered over. UNKNOWN_DATE is the only non-date the PARSER can
    # produce for this field; anything else arriving here came from a source
    # Observation whose effectiveDateTime violates the FHIR dateTime type
    # ("N/A", a free-text note). Such a value is unparseable, so
    # _select_ecog_performance_status treats the observation as undated and can
    # still select it down the undated_single path -- and it is then stored
    # VERBATIM rather than nulled, deliberately: this column's contract is "the
    # date as the source wrote it", and silently discarding malformed source
    # data would hide a data-quality fault instead of recording one, which is
    # the defect this project exists to remove. It is not silent in the row
    # either -- ecog_selection reads 'undated_single', which is what tells a
    # reader the value cannot be a real date. Giving it a proper degradation
    # counter is a recorded follow-up rather than a silent fix, and this corpus
    # produces zero of them: Synthea writes ISO instants.
    "ecog_date":                    "TEXT",

    # --- Stage 5 truncation control (item 19c) -----------------------------
    #
    # Two counters because there are two budgets. llm_classifier_retries counts whole-
    # node retries for a malformed or failed response; llm_classifier_truncation_splits
    # counts levels of halving spent because a response was CUT OFF at the
    # model's output ceiling. Before this, a truncated response fell through to
    # the JSON parser, failed there, and was retried as an identical request
    # that truncated again -- so a truncation was logged as three parse
    # retries, and the two causes were indistinguishable in the record.
    #
    # llm_classifier_output_tokens_estimated is the pre-call estimate, stored beside the
    # actual in llm_classifier_output_tokens. That column pair is what the constants in
    # 03- Config.py were derived from over 1,094 historical rows, and storing
    # the estimate is what lets the next derivation be measured rather than
    # guessed. NULL when Stage 5 never ran: "estimated nothing" is not "0".
    #
    # not_evaluable_truncated counts trials that entered Stage 5 and left with
    # no verdict because of truncation. It is a SUBSET of not_evaluable_trials
    # in the sense that both end up not evaluable, but the cause is different
    # and only this column separates "the model assessed it and could not
    # conclude" from "the model never got to answer".
    #
    # llm_classifier_calls is how many requests the stage actually issued. Without it a
    # split run and an unsplit one are indistinguishable in the token columns,
    # because the tokens are summed across chunks.
    "llm_classifier_truncation_splits":      "INTEGER",
    "llm_classifier_output_tokens_estimated": "INTEGER",
    "not_evaluable_truncated":      "INTEGER",
    "llm_classifier_calls":                  "INTEGER",

    # --- Reasoning-model accounting (item 29a, gpt-5.6-terra migration) ------
    #
    # The reasoning share OF llm_classifier_output_tokens. NOT an additional charge.
    # OpenAI's reasoning guide and a live probe on 2026-08-04 both put
    # usage.completion_tokens_details.reasoning_tokens INSIDE
    # usage.completion_tokens, billed at the output rate. So:
    #
    #     estimated_cost_usd already includes these tokens.
    #     llm_classifier_output_tokens already includes these tokens.
    #
    # Anyone adding this column into a cost calculation is double-billing.
    # It is stored because it is the only way to see what fraction of the
    # output spend bought reasoning rather than verdicts, and because it is
    # what MATCHING_OUTPUT_TOKENS_PER_TRIAL (File 03) must be calibrated
    # against now that reasoning tokens consume the same ceiling.
    #
    # NULL means the response carried no breakdown -- every row written while
    # GPT-4o was the judge, a replayed pre-migration fixture, or a run that
    # never reached Stage 5. That is NOT 0. A non-reasoning model that
    # genuinely reports reasoning_tokens=0 stores 0, and the two must stay
    # distinguishable: a query averaging this column has to exclude NULL, not
    # coalesce it.
    "llm_classifier_reasoning_tokens":       "INTEGER",

    # --- Which Stage 5 system prompt produced this row ----------------------
    #
    # READ THIS BEFORE WRITING A QUERY AGAINST EITHER COLUMN.
    #
    # llm_classifier_prompt_sha256 IS NOT sha256(llm_classifier_prompt). The
    # prompt column holds the SYSTEM message and the USER message concatenated
    # ("[SYSTEM]\n...\n\n[USER]\n..."), and the user half carries this
    # patient's record, so its hash identifies the PATIENT. This column hashes
    # the SYSTEM message alone, which is what identifies the TEMPLATE and is
    # therefore the thing that can be grouped on across patients. The two
    # cannot be reconciled by re-hashing the stored text and must not be
    # compared with each other.
    #
    # llm_classifier_prompt_version is hand-maintained in
    # oncotriage/agent/prompts.py and says what a human intended; the hash is
    # computed per call and says what was actually sent. They can disagree --
    # an edit made without bumping the version leaves two runs sharing a
    # version and differing in hash -- and that disagreement is exactly what
    # the pair exists to make visible. Trust the hash for identity; read the
    # version for intent.
    #
    # NULL AND NOT-NULL MEAN DIFFERENT THINGS ON THE TWO COLUMNS:
    #
    #   version NULL   the row predates this migration, or was logged by a
    #                  caller that did not come from a pipeline terminal node.
    #   version SET    this build's template version. Set on EVERY terminal
    #                  path, including the ones where Stage 5 never ran, because
    #                  it is a property of the code rather than of the run.
    #   hash NULL      no system prompt was ever rendered for this row --
    #                  node_no_candidates, or a failure upstream of Stage 5.
    #                  It is NOT "the hash was not recorded".
    #   hash SET       these are the exact bytes the model was sent. One value
    #                  per inference even when the batch split into chunks: the
    #                  system message is rendered once and reused for every
    #                  chunk, and only the user message differs.
    #
    # So "did Stage 5 run" is `llm_classifier_prompt_sha256 IS NOT NULL`, never
    # a test on the version.
    "llm_classifier_prompt_version":         "TEXT",
    "llm_classifier_prompt_sha256":          "TEXT",

    # --- How much of that prompt was the patient ----------------------------
    #
    # Estimated tokens of the PATIENT RECORD block's body inside the Stage 5
    # system message, measured by the pipeline's own estimator
    # (oncotriage/agent/evaluation.py:estimate_prompt_tokens, the
    # characters/CHARS_PER_TOKEN proxy) over the NEUTRALIZED text that was
    # actually interpolated. It is a MEASUREMENT, not a billed figure: the
    # provider's own count for the whole request is
    # llm_classifier_input_tokens, and these two must never be compared as if
    # they measured the same thing.
    #
    # WHAT IT IS FOR. The system message is constant except for this block, so
    # a run's fixed prefix is template + record. With this column a reader can
    # say how much of a patient's spend was their own record and how much was
    # instruction; without it the two are one number and the split is
    # unrecoverable from any stored row.
    #
    # THE TEMPLATE'S SHARE IS DELIBERATELY NOT A COLUMN. It is the fixed
    # prefix minus this value minus the user wrapper, and storing a derived
    # quantity beside its inputs is how two copies of one fact start
    # disagreeing. Derive it.
    #
    # ONE VALUE PER INFERENCE EVEN WHEN THE BATCH SPLIT, exactly as with
    # llm_classifier_prompt_sha256: the record is rendered once, above the
    # split loop, and every chunk of that patient carries the same bytes.
    #
    # NULL MEANS NO PROMPT WAS EVER RENDERED -- node_no_candidates, a failure
    # upstream of Stage 5, a row written before this column existed, or a
    # result dict that did not come from a pipeline terminal node. It is NOT
    # "the record was empty": Stage 5 writes this on every one of its returns,
    # including the failing ones, because the render precedes the first call.
    # So it is NULL exactly when llm_classifier_prompt_sha256 is NULL, and 0
    # would be a genuine reading of an empty record rather than an absence.
    "llm_classifier_patient_record_tokens":  "INTEGER",

    # --- What the INPUT packer did, and what the provider served from cache -
    #
    # THESE FOUR WERE MEASURED AND THEN THROWN AWAY AT THIS WRITE. Stage 5
    # computed all four, TrialMatchState declared them and
    # _pipeline_provenance() carried them onto every result dict; this table
    # declared no column, so File 14 wrote the columns it knew about and
    # dropped the rest in silence. The comment beside them in
    # oncotriage/agent/terminal.py recorded that as a deferred schema decision.
    # This is that decision.
    #
    # NULL IS "NOT MEASURED" ON ALL FOUR, AND 0 IS A MEASUREMENT. The
    # populations differ per column and are spelled out below, because they are
    # not the same population and a reader who assumes they are will read a
    # packing report of an unpacked run.
    #
    # NONE OF THE FOUR IS A COSTING TERM, and one of them looks like one.
    # llm_classifier_cached_input_tokens is a SUBSET of
    # llm_classifier_input_tokens -- the provider's report of how much of this
    # request's prefix it served from cache -- exactly as
    # llm_classifier_reasoning_tokens is a subset of the output figure. Cached
    # input bills at a lower rate (PRICING_CONFIG's gpt-5.6-terra note records
    # $0.20/1M against $2.00/1M) and that discount is deliberately NOT modelled
    # by get_model_cost(), so that estimated_cost_usd stays comparable with
    # every historical row in the same column. Subtracting this from the input
    # figure, or pricing it separately, silently re-bases the whole cost series.
    #
    #   cached  NULL = no response of this run reported prompt_tokens_details
    #                  .cached_tokens: a stub, a recording made before the
    #                  field existed, a run that never reached Stage 5, or a
    #                  run that ended at a failure return (the totals are not
    #                  carried out of those -- the per-call ledger is, and it
    #                  is where a failed run's cache reading lives).
    #           0    = a response DID report the field and reported zero: the
    #                  provider cached nothing. That is the reading this column
    #                  exists to distinguish from the absence above, and it is
    #                  the reading that says a prefix is not being reused.
    #
    #   THE PER-TRIAL CACHE WARMUP IS NOT IN THIS FIGURE, and that exclusion is
    #   what keeps the two readings above apart on that arm. The warmup is the
    #   request that WRITES the shared prefix, so it reports 0 on a completely
    #   healthy run; folding it in turned every per-trial row into a 0 and made
    #   "the wave said nothing about caching" indistinguishable from "the wave
    #   reported and nothing was cached" -- the exact pair of readings this
    #   column is for. So on the per-trial arm this is the WAVE's total: NULL
    #   means no trial call reported the field, 0 means trial calls reported it
    #   and the provider served nothing from cache. It is the same convention
    #   the ledger's `trials` and `entries_emitted` already apply to that row.
    #   The warmup's own figure is untouched in llm_classifier_call_details,
    #   on the row carrying `warmup`, which is the only place it can answer
    #   whether the warmup wrote the prefix or found it already warm.
    #
    #   CONSEQUENCE, STATED: on the per-trial arm this is a subset of the
    #   WAVE's input tokens rather than of llm_classifier_input_tokens, which
    #   still carries the warmup and is therefore still an upper bound -- so
    #   "cached <= input" holds and a cached/input ratio under-reads by the
    #   warmup's own prompt. Nothing is re-based: this was never a cost term.
    #
    # llm_classifier_call_details IS THE ONLY COLUMN THAT CAN ANSWER WHETHER
    # THE CACHE WARMS. The summed figure above cannot: 5,000 cached tokens
    # across three calls is equally consistent with a cache that warms after
    # the first request and one that never warms at all, and those have
    # opposite implications for what packing costs. This is the per-call
    # ledger, JSON, one object per request ISSUED, each carrying call_index
    # (1-based), depth, trials, prompt_tokens, completion_tokens,
    # cached_tokens, reasoning_tokens, finish_reason and entries_emitted.
    # trial_matches.call_index joins to it by equality.
    #
    #   WHAT `call_index` ORDERS BY IS NOT THE SAME QUESTION IN BOTH ARMS, and
    #   this used to say "in the order they were issued" flatly, which is true
    #   of one of them. GROUPED mode issues one request, waits for it, counts
    #   it and issues the next, so ISSUE order and ACCOUNTING order are one
    #   sequence. PER-TRIAL mode submits every trial call to a thread pool up
    #   front and then consumes the responses in the order the node ASKS for
    #   them -- the pending LIFO's pop order, which is packing order -- so
    #   call_index follows the ACCOUNTING order and the order the requests
    #   actually reached the provider is the pool's and is not recorded
    #   anywhere. It is deterministic in both arms, which is what the join to
    #   trial_matches.call_index needs; what it is NOT, on the per-trial arm,
    #   is a wire-order timeline. Reading two adjacent per-trial rows as "this
    #   one went out before that one" is the misreading this paragraph exists
    #   to prevent -- and it is the reading that matters, because whether the
    #   cache warms is a question about what went out first.
    #
    #   details NULL = node_llm_classifier_evaluation was never entered. Stage 5
    #                  writes this key on EVERY one of its returns, including
    #                  the failing ones, so its absence is stronger than the
    #                  other three: it means no attempt was made at all.
    #           '[]' = the node WAS entered and no call produced a usage
    #                  object -- the first request raised before any response
    #                  arrived. AN EMPTY LIST IS NOT NULL HERE and the INSERT
    #                  tests `is not None` rather than truthiness for exactly
    #                  this reason; see the value expression.
    #
    #   packed_chunks  NULL = the packer's record does not describe this run.
    #                  TWO POPULATIONS, and they share the NULL because they
    #                  are the same statement:
    #                    (a) the run did not reach the success return. Stage 5
    #                        writes this on its SUCCESS return only
    #                        (hallucinated_trials' convention, not the
    #                        truncation counters'): the chunk list is a plan,
    #                        and a run that died at its first call would
    #                        otherwise publish the whole plan as though every
    #                        request in it had been sent.
    #                    (b) SOMETHING BYPASSED THE PACKER. Per-trial call mode
    #                        partitions the batch itself and the packer never
    #                        runs, so there is no chunk count to report -- the
    #                        requests that went out came from the mode, not
    #                        from a budget. The bypass is NOT invisible: it is
    #                        named in llm_classifier_packing.bypassed_by, which
    #                        is present on that branch and on no other, and
    #                        stage5_input_packing_pressure counts those rows in
    #                        `bypassed_inferences` rather than folding them into
    #                        `unpacked_inferences`.
    #
    #                  0 chunks is reachable and is a measurement, and it has
    #                  TWO producers, separated by packing.enabled rather than
    #                  by this scalar -- stated here because a reader who takes
    #                  0 to mean only the first will misread the second:
    #                    enabled = true  -- the packer RAN and produced no
    #                        chunk, i.e. an empty candidate set.
    #                    enabled = false -- MATCHING_INPUT_PACKING_ENABLED was
    #                        off, so the batch went out as one unpacked
    #                        request. This is the pre-packer node's behaviour
    #                        preserved deliberately; it is NOT the bypass, and
    #                        it is NOT rewritten to NULL, because
    #                        `enabled = false` with no bypassed_by already says
    #                        it and section 6e of
    #                        tests/test_agent_stage5_input_packing.py is the
    #                        standing contract that it does.
    #                  What 0 is NOT is a bypassed run: a per-trial row storing
    #                  0 here would be a six-request patient reading identically
    #                  to a patient with no candidates at all, which is why (b)
    #                  above is a NULL. Use llm_classifier_calls for "how many
    #                  requests did this patient send"; this column counts the
    #                  PACKER's chunks.
    #
    #   packing        NULL on (a) for the same reason, and NEVER on (b): the
    #                  bypass record IS the blob, carrying enabled = false, no
    #                  budget, no cap and bypassed_by. So the pair is not
    #                  redundant -- packing NOT NULL with packed_chunks NULL is
    #                  exactly the bypass, and both NULL is the failure return.
    #
    # packing is the report BEHIND the count and the count is the scalar a
    # query groups by; both are stored because a JSON blob cannot be grouped on
    # and a scalar cannot be audited. The report carries the estimator named,
    # the configured and effective budgets, the cap, the two degradation flags
    # (cap_relaxed_budget, over_budget_chunk), one entry per chunk, and
    # prefix_sha256 -- the same value as llm_classifier_prompt_sha256, repeated
    # there because the record's whole claim is "these N requests shared one
    # prefix".
    "llm_classifier_cached_input_tokens":    "INTEGER",
    "llm_classifier_call_details":           "TEXT",
    "llm_classifier_packed_chunks":          "INTEGER",
    "llm_classifier_packing":                "TEXT",

    # --- How close this run came to the two OUTPUT guards -------------------
    #
    # READ THIS BEFORE COMPUTING A RATIO OFF EITHER COLUMN.
    #
    # Stage 5 has three guards and this pair supplies the denominators for two
    # of them. The third -- the INPUT packing budget -- needs no column here:
    # its estimate, its configured budget, its effective budget and its two
    # degradation flags are all inside llm_classifier_packing above, and a
    # value derivable from an existing column is not stored a second time.
    #
    #   llm_classifier_output_split_threshold
    #       The PROACTIVE splitter's threshold, in tokens, as it stood when
    #       this run was sent: int(MATCHING_MAX_TOKENS x
    #       MATCHING_OUTPUT_SPLIT_FRACTION). The batch is halved before the
    #       first request when llm_classifier_output_tokens_estimated exceeds
    #       it. It is stored BESIDE that estimate because the estimate alone
    #       cannot be read: a ratio against a threshold nobody recorded is
    #       uninterpretable the moment either constant behind it moves, and
    #       both have moved once already (the GPT-4o ceiling was 16,000).
    #
    #   llm_classifier_output_ceiling
    #       MATCHING_MAX_TOKENS in force -- the per-request output ceiling sent
    #       as max_completion_tokens, and the REACTIVE guard's trigger: a
    #       response that reaches it comes back with finish_reason 'length' and
    #       the batch is halved. The per-call figure it is the denominator FOR
    #       is llm_classifier_call_details[].completion_tokens, not
    #       llm_classifier_output_tokens, which is summed across chunks and so
    #       cannot be compared with a per-request ceiling.
    #
    # NEITHER IS DERIVABLE FROM THE OTHER, which is why both are here and this
    # is not one number stored twice. The threshold is a TRUNCATED product of
    # the ceiling and a fraction that is not stored, so the ceiling cannot be
    # recovered from it; and the fraction is unknown in the other direction
    # too. A run at (32000, 0.90) and a run at (28800, 1.00) record the same
    # threshold and have completely different reactive headroom -- with only
    # one column that difference is invisible, which is the exact failure this
    # pair exists to prevent.
    #
    # WHY THE FRACTION ITSELF IS NOT A COLUMN: it is derivable to within the
    # int() truncation from the two that are, it is not what any comparison is
    # made against, and a third column carrying a quantity a reader can compute
    # is a third thing that can go stale on its own.
    #
    #   NULL on both = Stage 5 never ran for this row (node_no_candidates, or a
    #                  failure upstream of the node), or the row predates these
    #                  columns. Both are computed unconditionally ABOVE the
    #                  send loop, so every one of the node's five returns
    #                  carries them -- the four failure returns included, on
    #                  llm_classifier_output_tokens_estimated's own convention:
    #                  they are facts measured BEFORE the first call, so they
    #                  are true of a run whether or not it answered.
    #   0            = not reachable from a configured pipeline and is NOT
    #                  defended against here. MATCHING_MAX_TOKENS is a positive
    #                  ceiling; a 0 in either column means somebody configured
    #                  one, and reading it as "unmeasured" would hide that.
    #
    # NOT COSTING TERMS AND NOT MEASUREMENTS OF THIS RUN'S OUTPUT. They are the
    # CONFIGURATION the run was judged against, recorded per row because that
    # is the only place a later reader can find what was in force at the time.
    # Averaging them across a campaign that spans a config change is
    # meaningless; grouping by them is what such a campaign supports.
    "llm_classifier_output_split_threshold": "INTEGER",
    "llm_classifier_output_ceiling":         "INTEGER",

    # --- The INPUT guard's estimate and its denominator (era 6) -------------
    #
    # THE INPUT SIDE HAD NO SCALAR AT ALL, and the two columns above are the
    # design it is mirroring. Input pressure lived only inside
    # llm_classifier_packing, and that report cannot answer for two whole
    # populations:
    #
    #   * EVERY STAGE 5 FAILURE RETURN. The chunk list is a PLAN, and Stage 5
    #     publishes it on the success return only -- deliberately, so a run
    #     that died at its first call does not publish the whole plan as
    #     though it had been sent. So a failed row's input size was NULL, and
    #     a run that failed BECAUSE its input was enormous is exactly the row
    #     worth asking the question of.
    #   * EVERY ROW OF THE SHIPPED CALL MODE. Per-trial mode BYPASSES the
    #     packer, so its report carries `enabled: False`, a `bypassed_by` note,
    #     `budget_tokens: None` and an empty chunk list. That is honest -- the
    #     packer really did not run -- and it leaves the mode whose entire cost
    #     argument is per-request input size with no per-request input figure.
    #
    #   llm_classifier_input_tokens_estimated
    #       The LARGEST SINGLE-REQUEST input estimate among the requests this
    #       patient's Stage 5 dispatch was partitioned into: the shared prefix
    #       (system message plus the user wrapper) charged in full, plus that
    #       request's own rendered trial blocks, by the same
    #       characters/CHARS_PER_TOKEN proxy the packer prices with.
    #
    #       A MAXIMUM, NOT A SUM, AND THAT IS THE COLUMN'S DEFINITION rather
    #       than an implementation detail. MATCHING_INPUT_TOKEN_BUDGET is a
    #       budget on ONE REQUEST, so the number that approaches it is the
    #       biggest request; a sum across chunks would rise with the chunk
    #       count, which is the packer working rather than pressure. In
    #       per-trial mode it is the largest prefix-plus-one-trial call. THE
    #       PER-CALL FIGURES ARE NOT DUPLICATED HERE: llm_classifier_call_
    #       details already carries one row per request issued.
    #
    #       MEASURED AT PLAN TIME, above the send loop, which is what makes a
    #       failed row comparable with a successful one. A figure defined over
    #       requests actually ISSUED would report a smaller number for a
    #       patient whose first call raised than for the same patient whose
    #       calls succeeded. Reactive splits only ever HALVE a chunk that was
    #       already sent at full size, so on a run that completes this is also
    #       the maximum over what was issued.
    #
    #   llm_classifier_input_budget
    #       MATCHING_INPUT_TOKEN_BUDGET as it stood for this run -- the
    #       CONFIGURED per-request budget. NOT the packer's effective one: a
    #       relaxation is itself pressure, and measuring against the relaxed
    #       figure would report a relaxed run as comfortably inside its budget.
    #       A ratio above 1.0 against this column is the honest reading of such
    #       a run, and llm_classifier_packing keeps the effective budget for
    #       the runs where a packer selected one.
    #
    #       IT IS A COLUMN RATHER THAN A DERIVATION, and that was checked
    #       before it was added. The only stored copy of this number is
    #       llm_classifier_packing's `budget_tokens_configured`, in a column
    #       that is NULL on exactly the two populations above -- so deriving
    #       from it would leave the new estimate without a denominator on every
    #       row that needed one.
    #
    # NULL ON BOTH MEANS STAGE 5 WAS NEVER ENTERED (no candidates, an upstream
    # failure) or the row predates era 6. It never means "this run had no
    # input": every one of Stage 5's returns sits below the render and carries
    # both, the four failure returns and the per-trial floor included.
    "llm_classifier_input_tokens_estimated": "INTEGER",
    "llm_classifier_input_budget":           "INTEGER",
    # --- Which provider served Stage 5 -------------------------------------
    #
    # "openai" or "bedrock", exactly -- config.MATCHING_PROVIDER's value, read
    # here at INSERT time.
    #
    # A PLAIN STRING, DELIBERATELY NOT A LOOKUP-TABLE KEY, and the schema's own
    # precedent is two columns away: matching_model and ecog_selection are both
    # free TEXT carrying a closed vocabulary, and this project has never
    # normalised one into a side table. SQLite has no enum, a CHECK constraint
    # cannot be added by the ALTER-only migration mechanism this dict IS, and a
    # lookup table would put a join in front of every cost query for a column
    # with two values. AT THE POSTGRES MIGRATION it becomes TEXT with a CHECK
    # constraint naming the two values -- which is the point at which the
    # vocabulary can be enforced by the database rather than by the writer.
    #
    # READ FROM CONFIG, NOT FROM THE RESULT DICT, and that is what makes it
    # unconditional. CROSS_ENCODER_MODEL two lines below is written the same
    # way and for the same reason: the value is a fact about the PROCESS, not
    # about how far the pipeline got, so it lands on every row this writer
    # produces -- a no-candidates row, an error-handler row and a Stage 5
    # failure return alike. Routing it through the result dict would have made
    # it NULL on exactly the rows a migration investigation cares about most.
    # Note the asymmetry with matching_model, which is read off the RESPONSE:
    # which provider was ASKED and which model ANSWERED are different facts,
    # and only the second can surprise you.
    #
    # NULL MEANS THE ROW PREDATES THIS COLUMN, and such a row is provably
    # OpenAI: the Bedrock path did not exist when it was written. NOTHING IS
    # BACKFILLED. A backfilled value is indistinguishable from a measured one,
    # and the whole reason this column exists is that a stored row should not
    # have to be dated to be interpreted.
    "matching_provider":                     "TEXT",
    # --- HOW STAGE 5 PARTITIONED ITS WORK ----------------------------------
    #
    # "grouped" or "per_trial", exactly -- config.matching_call_mode()'s value,
    # read here at INSERT time.
    #
    # WHAT IT SEPARATES THAT NOTHING ELSE CAN. llm_classifier_packing.enabled
    # reads False on a run where the packing switch was off AND on a run where
    # per-trial mode bypassed the packer, and those two ran different request
    # shapes against the same patient. The measured reason per-trial mode
    # exists -- reasoning leaking between trials that share one prompt -- makes
    # them capable of producing different VERDICTS, so a campaign that mixed
    # them and could not tell them apart afterwards would be uninterpretable.
    #
    # A PLAIN STRING ON matching_provider's PRECEDENT two lines up, for the
    # identical reasons: SQLite has no enum, a CHECK constraint cannot be added
    # by the ALTER-only mechanism this dict IS, and a lookup table would put a
    # join in front of every query for a column with two values. AT THE
    # POSTGRES MIGRATION it becomes TEXT with a CHECK naming both members of
    # config.MATCHING_CALL_MODES.
    #
    # READ FROM CONFIG, NOT FROM THE RESULT DICT, which is what makes it
    # unconditional: it lands on the no-candidates rows, the error-handler rows
    # and every Stage 5 failure return, which are exactly the rows a mode
    # comparison must be able to attribute. A run that died before Stage 5 was
    # still CONFIGURED in a mode, and that is what this column claims.
    #
    # THROUGH ONE OWNER, config.matching_call_mode(), rather than by reading
    # the flag here. oncotriage/agent/evaluation.py decides how to partition
    # from the same function, so the row cannot name a mode the node did not
    # run -- the divergence a second reader of the same constant would make
    # possible. matching_wire_model() is the same shape for the same reason.
    #
    # NULL MEANS THE ROW PREDATES THIS COLUMN, and such a row is provably
    # grouped: per-trial mode did not exist when it was written. NOTHING IS
    # BACKFILLED, on matching_provider's argument -- a backfilled value is
    # indistinguishable from a measured one.
    "matching_call_mode":                    "TEXT",
    # --- What Stage 5's normalizer corrected, per run ----------------------
    #
    # THREE ARTIFACTS ARE PRODUCED BY THAT NORMALIZER AND UNTIL THIS PASS TWO OF
    # THEM REACHED NO STORED BYTE. `not_evaluable_reason` was stamped on the
    # entry and dropped at the trial_matches INSERT; `verdict_normalizations`
    # was a local list read by one log line; `label_remaps` survived only as its
    # own LENGTH, in `cross_vocab_remaps` beside these two.
    #
    # WHAT EACH OF THESE ANSWERS THAT cross_vocab_remaps CANNOT.
    #
    #   verdict_normalizations  How many trial-level verdicts the model wrote in
    #                           a spelling outside TRIAL_VERDICTS -- boolean
    #                           True, "Eligible", a null, a nested object. A
    #                           DIFFERENT ARTIFACT from a criterion remap, not a
    #                           subset of one: an entry counted here may have
    #                           ended eligible, not_eligible or not_evaluable.
    #                           The per-row breakdown is
    #                           trial_matches.verdict_source /
    #                           verdict_original_type, which is what the
    #                           "from what original types" question groups by.
    #
    #   remapped_trials         How many TRIALS carried at least one criterion
    #                           remap. cross_vocab_remaps counts remap EVENTS,
    #                           so four remaps on one trial and one remap on
    #                           each of four trials are the same number there
    #                           and different findings here.
    #
    # NULL = STAGE 5's NORMALIZER DID NOT REPORT, 0 = IT REPORTED NONE, and the
    # distinction is the whole reason these are stored rather than derived. Both
    # are recoverable by joining trial_matches -- but a COUNT over a child table
    # returns 0 for "measured none", for "these rows predate the columns" and
    # for "no Stage 5 ran" alike. That is exactly the argument
    # hallucinated_trials makes beside trial_matches.hallucinated, and this pass
    # adopts it rather than inventing it.
    #
    # A PRE-EXISTING DEFECT IN THE NEIGHBOUR IS RECORDED AND NOT FIXED HERE.
    # `cross_vocab_remaps` is written by _pipeline_provenance as
    # `state.get(..., 0)` and as a literal 0 by node_no_candidates, so it reads
    # 0 on runs whose normalizer never ran and its NULL population is only rows
    # that predate it. Narrowing it now would change what an existing column
    # means for every reader; these two are honest from their first row and the
    # asymmetry is stated at both ends.
    #
    # PER-REASON AND PER-TYPE BREAKDOWNS ARE DELIBERATELY NOT STORED HERE. They
    # are GROUP BYs over trial_matches, which is plain SQL over scalar columns;
    # storing them would mean either a JSON blob (which no query can group on)
    # or one column per constant (a schema that migrates whenever a reason is
    # added).
    "verdict_normalizations":                "INTEGER",
    "remapped_trials":                       "INTEGER",

    # --- WHICH RECORDED RUN PRODUCED THIS ROW (the run-identity pass) -------
    #
    # `runs.id`, or NULL. Additive like every column above it, so every row
    # already in a database keeps NULL, which is the honest value: those rows
    # were written before there was a run to attach them to.
    #
    # NULL IS A FIRST-CLASS VALUE HERE AND NOT ONLY A LEGACY ONE, which makes
    # it unlike `hallucinated_trials` and unlike the two normalizer counters
    # directly above. Three live callers write NULL on purpose:
    #
    #   oncotriage/api/server.py   a request is not a campaign. It has no
    #                              start, no end and no configuration stamp of
    #                              its own, so there is nothing for it to point
    #                              at and inventing a run per request would put
    #                              one row in `runs` for every POST.
    #   a direct log_inference     a test, a notebook, an embedder.
    #   the batch runner, IF the run row could not be created -- which cannot
    #                              happen silently: start_run_record RAISES, so
    #                              a batch run either has its id or has stopped.
    #
    # So `run_id IS NULL` means "not part of a recorded batch run", NEVER "the
    # run is unknown". THE QUERY FOR ONE CAMPAIGN IS THEREFORE A JOIN, not a
    # timestamp window:
    #
    #     SELECT i.* FROM inferences i JOIN runs r ON r.id = i.run_id
    #     WHERE r.id = ?
    #
    # which is the whole point of the pass: the timestamp-gap heuristic it
    # replaces cannot tell a resumed campaign from two campaigns, cannot tell a
    # batch row from an API row written in the same minute, and has no way at
    # all to attribute a row to the configuration that produced it.
    #
    # THE REFERENCE IS DECLARED AND UNENFORCED. See the `runs` CREATE TABLE for
    # the decision and the four reasons; the short version is that
    # `PRAGMA foreign_keys` is per CONNECTION and this module opens only some of
    # the connections that touch this file.
    #
    # THE `REFERENCES` CLAUSE IS DOCUMENTATION AND IT IS WORTH THE CHARACTERS.
    # It puts the relationship in `sqlite_master`, where every tool that reads a
    # schema -- a diagram generator, a migration assistant, `.schema` at the
    # sqlite3 prompt, the next person -- can see it, and it is what makes
    # `PRAGMA foreign_keys = ON` a one-line experiment rather than a schema
    # change. It changes NOTHING at run time while the pragma is off.
    #
    # IT IS LEGAL ON AN ALTER. SQLite permits ADD COLUMN with a REFERENCES
    # clause provided the column's default is NULL, which this one's is; a NOT
    # NULL default would be the case it refuses.
    #
    # IT REACHES ONLY DATABASES THAT DO NOT YET HAVE THE COLUMN. A file where
    # `run_id` already exists keeps the declaration it was created with, because
    # this migration is skipped for it and SQLite has no way to add a constraint
    # to an existing column short of rebuilding the table. The production file
    # has no `run_id` yet, so it gets the declared form; a database built since
    # the run-identity pass keeps the undeclared one, and the two behave
    # identically because neither is enforced.
    "run_id":                                "INTEGER REFERENCES runs(id)",
}


#------------------------------------------------------------------------------


# Schema migration for the runs table.
#
# THE FIRST ENTRY CREATED THIS DICT, which is what the schema comment at the
# `runs` CREATE TABLE instructed: the table was new when it was written, so an
# empty dict would have been a loop iterating nothing and passing for free --
# the shape pass 20f-3 deleted `_REEXPORT_EXEMPTIONS` for rather than emptying.
# It has something to do now.
#
# `resumed` -- WAS THIS CAMPAIGN A RESUME. 1 when the checkpoint handed main()
# a non-empty completed set, 0 when it did not, NULL on every row written before
# this column existed. Three values and all three are readings:
#
#   NULL  this run predates the column. NOT "we did not check" and not 0 --
#         a run that was not a resume is a MEASURED 0, and collapsing the two
#         would make every historical row assert something nobody recorded.
#         Same rule as `hallucinated` and `collection_points` next door.
#   0     the checkpoint was empty or absent: this campaign started from
#         nothing.
#   1     the checkpoint named at least one completed patient, so some of the
#         rows this run is attributed to were written by an EARLIER process.
#
# WHY IT IS WORTH A COLUMN. A resumed run's `runs` row already lies about two
# things by construction, and only this column says so: its `started_at` is when
# the LAST process started, not when the campaign did, and its patient count
# through `inferences.run_id` covers only the patients THIS process wrote --
# every patient the earlier process completed carries the EARLIER run's id.
# So `patients` on a resumed row is a fragment of the campaign, and a reader
# with no way to tell a resumed run from a fresh one reads that fragment as
# the whole.
#
# IT IS WRITTEN FROM THE SAME FACT THE MLflow TAG READS -- the truthiness of
# the checkpoint-derived completed set, at the same point in main(), passed as
# one argument. Two records of one fact that are computed twice are two records
# that can disagree; oncotriage/batch/runner.py takes the boolean once and hands
# it to both.
#
# `matching_call_mode` -- WHICH STAGE 5 ARM THIS RUN WAS STAMPED WITH. Exactly
# `config.matching_call_mode()`'s two-member vocabulary, "grouped" or
# "per_trial", written through RUN_FINGERPRINT_COLUMNS like every other stamp
# field rather than by a reader of the flag. NULL on every row written before
# this column existed, which is NOT "grouped": a run that ran in grouped mode is
# a MEASURED grouped, and collapsing the two would make every historical row
# assert an arm nobody recorded. `resumed`'s rule next door, and
# `collection_points`' one column further.
#
# IT IS IN THIS DICT *AND* IN RUN_FINGERPRINT_COLUMNS, WHICH IS NOT A
# DUPLICATION BUT TWO ORTHOGONAL FACTS ABOUT ONE COLUMN. This dict says WHEN the
# column arrived, which is what migrates an existing database and what
# oncotriage/storage/queries.py:ADDITIVE_COLUMNS reads so a query naming it can
# declare `requires_columns` and be SKIPPED rather than killing report() on a
# database that predates it. RUN_FINGERPRINT_COLUMNS says WHAT the column means,
# which is what fills it at the INSERT and what generates the campaign stitch
# predicate. Neither implies the other, and RUN_COLUMNS -- which is derived from
# both -- is what has to know they can name the same column; see its own note.
#
# DELIBERATELY *NOT* ADDED TO THE `runs` CREATE TABLE. Leaving it out means a
# FRESH database gets it from the same ALTER an existing one does, so both end
# up with the identical physical column order. Putting it in the CREATE TABLE
# would give a fresh database the column in the fingerprint block and a migrated
# one the column at the end -- two real column orders for one declared schema,
# which is the kind of difference that surfaces only in whichever tool reads a
# row positionally.
RUN_COLUMN_ADDITIONS = {
    "resumed": "INTEGER",
    "matching_call_mode": "TEXT",
    # THE OPERATOR'S OWN WORDS ABOUT WHY THIS RUN ENDED THE WAY IT DID.
    #
    # The stop sentinel may carry a note -- `touch` is the documented gesture
    # and an empty file is the common case, but an operator who writes one into
    # it is answering the question a reviewer asks first. That note was READ
    # (control.read_stop_message), LOGGED and PRINTED in the run's closing
    # block, and then died with the process: `runs.status` said STOPPED and
    # nothing anywhere said why. A campaign covering a prefix of the cohort is
    # exactly the row whose reason a reviewer needs, and the reason existed and
    # was thrown away.
    #
    # NULL ON EVERY HISTORICAL ROW AND ON MOST NEW ONES, and that is a value
    # rather than an absence to be explained: no note was left. It is written
    # only by `finalize_run_record`, only when its caller passes one, and the
    # only caller that does is the batch runner's stop path.
    #
    # CAPPED AT THE WRITE, by `RUN_NOTE_MAX_CHARS`, even though the one shipped
    # source is already capped upstream at control.STOP_MESSAGE_MAX_CHARS. A
    # writer that trusts its caller to have bounded a free-text field is a
    # writer that puts an arbitrarily large blob in a durable table the first
    # time a second caller appears; the two bounds are independent for that
    # reason and neither is derived from the other.
    #
    # IT IS FREE TEXT AN OPERATOR TYPED, so it is deliberately NOT a loggable
    # field and nothing branches on it. It is stored, and it is read by a human.
    "note": "TEXT",
    # WHICH STOP THIS WAS, AS A CLOSED MACHINE-READABLE VOCABULARY.
    # See RUN_STOP_REASONS below for the members and for why this is a column
    # beside `status` rather than two more members OF `status`.
    "stop_reason": "TEXT",
}


# The bound on `runs.note`. NOT in oncotriage/config.py, on
# control.STOP_MESSAGE_MAX_CHARS's argument: that file's promise is that every
# constant in it is a tunable an operator can change to change what a run does,
# and this changes nothing about a run -- it bounds one column of one table so a
# caller cannot put an unbounded blob in it. Larger than
# control.STOP_MESSAGE_MAX_CHARS on purpose, so the shipped path is never
# truncated twice and a second truncation marker in the column means a SECOND
# caller with a larger note, which is a fact worth being able to see.
RUN_NOTE_MAX_CHARS = 2000


#------------------------------------------------------------------------------


# WHAT THE gpt4o -> llm_classifier RENAME LEFT ON DISK.
#
# The naming pass renamed nine columns of `inferences` in place in the CREATE
# TABLE and in every reader. A database written before it carries the OLD name
# and nothing added the new one -- `ALTER TABLE ... RENAME COLUMN` is not
# expressible through INFERENCE_COLUMN_ADDITIONS, which can only ADD, and the
# pass recorded that decision rather than writing a migration: the production
# database is disposable and every published number comes from a fresh run.
#
# SO A RENAMED COLUMN IS ADDITIVE-SHAPED FROM THE READER'S SIDE and is not in
# the additions dict. That gap is what this constant closes. A query naming
# `llm_classifier_evaluation_time` against a pre-rename database raises
# `no such column` exactly as one naming a genuinely new column does, and
# oncotriage/storage/queries.py must be able to derive both classes from one
# set. Measured, not asserted: `report()` against the production database died
# at its SECOND query on that error, having printed eight lines.
#
# ALL NINE ARE HERE, INCLUDING THE FOUR THAT ARE ALSO IN
# INFERENCE_COLUMN_ADDITIONS. Those four were added after the rename under the
# new name, so a pre-rename database lacks them for two independent reasons and
# either declaration would do. Listing only the five that are not in that dict
# would make this a set defined by what another dict happens to contain, which
# is the shape that goes stale silently; listing all nine makes it a complete
# record of the rename, and the union the guard takes is the same either way.
#
# THE VALUES ARE NOT READ BY ANY MIGRATION and there is deliberately no code
# that renames anything. They are here because the guard's staleness check in
# tests/test_storage_schema_guards.py asserts that no old name is a column of a
# freshly created database -- which is what says the rename actually happened
# and that this record is not describing a state that no longer exists.
RENAMED_INFERENCE_COLUMNS = {
    # current name                              pre-rename name
    "llm_classifier_evaluation_time":           "gpt4o_evaluation_time",
    "llm_classifier_prompt":                    "gpt4o_prompt",
    "llm_classifier_input_tokens":              "gpt4o_input_tokens",
    "llm_classifier_output_tokens":             "gpt4o_output_tokens",
    "llm_classifier_retries":                   "gpt4o_retries",
    "llm_classifier_truncation_splits":         "gpt4o_truncation_splits",
    "llm_classifier_output_tokens_estimated":   "gpt4o_output_tokens_estimated",
    "llm_classifier_calls":                     "gpt4o_calls",
    "llm_classifier_reasoning_tokens":          "gpt4o_reasoning_tokens",
}


#------------------------------------------------------------------------------


# Schema migration for the trial_matches table (same reasoning as above).
#
# rerank_score stays the BOOSTED ranking score, so historical rows keep their
# meaning. The unboosted score and the MeSH boost are recorded separately so
# the boost's effect on ranking can be measured rather than inferred.
#
# match_score is confirmed/denominator over APPLICABLE criteria only (File 13
# excludes criteria the model marked "Not applicable -- ..." from both). Storing
# the three inputs makes the ratio auditable: a 0.0 score on a denominator of 8
# (nothing confirmable) is a different finding from 0.0 on a denominator of 0
# (no criterion applied to this patient), and neither is visible from the
# rounded score alone.
TRIAL_MATCH_COLUMN_ADDITIONS = {
    "rerank_score_raw": "REAL",   # fused rerank score before the MeSH boost
    "mesh_boost":       "REAL",   # additive boost, 0.0 when no tier matched
    "mesh_boost_tier":  "TEXT",   # "direct" | "pan_cancer" | "none"
    "score_confirmed":         "INTEGER",  # match_score numerator
    "score_denominator":       "INTEGER",  # match_score denominator (applicable only)
    "criteria_not_applicable": "INTEGER",  # criteria excluded from both
    # Per-trial marker for the same detection as inferences.hallucinated_trials.
    # Written from match["hallucinated"], which Stage 5 stamps onto every
    # surviving evaluation on its success path.
    #
    # TWO VALUES ARE REACHABLE AND THE THIRD IS NOT, BY CONSTRUCTION.
    #   0    = this row was checked and its NCT ID was in the candidate set.
    #   NULL = no check ran for this row: a run that ended before Stage 5
    #          completed, a result dict built outside the pipeline, or a row
    #          written before the detector existed.
    #   1    NEVER APPEARS. An entry outside the candidate set is dropped in
    #          node_llm_classifier_evaluation before enrichment, so it becomes
    #          no evaluation and therefore no row. The count of what was
    #          dropped lives in inferences.hallucinated_trials, which is the
    #          only place it can live -- there is no trial to hang it on.
    # The value is kept as a marker rather than removed because 0 against NULL
    # is what separates a checked row from an unchecked one, which is the whole
    # question this column answers.
    "hallucinated":            "INTEGER",
    # HOW THE INDEXER SPLIT THIS TRIAL'S ELIGIBILITY TEXT, copied through from
    # the trial's own `full_trial_json.criteria_split` -- one of
    # oncotriage/retrieval/indexer.py's CRITERIA_SPLIT_* constants: "both",
    # "inclusion_only", "exclusion_only", "unsplit" or "empty_criteria".
    #
    # WHAT IT IS FOR, AND IT IS A CAMPAIGN QUESTION RATHER THAN A ROW ONE. A
    # trial recorded "unsplit" had its whole criteria block handed to Stage 5
    # as INCLUSION text with the exclusion side EMPTY, so every exclusion
    # criterion in it was presented to the model as something the patient must
    # MEET. The admission pass cut that population from 746 trials to 213 and
    # deliberately left the remainder, and nothing downstream could say how
    # many of them a given campaign actually evaluated -- the field existed
    # only inside a Qdrant payload, which no query and no stored row could
    # reach. THIS COLUMN IS THAT MEASUREMENT and nothing more: it does not
    # change the split, it does not gate anything, and no code branches on it.
    #
    # WRITTEN ON EVERY TRIAL THAT REACHED STAGE 5, model-answered or
    # pipeline-constructed alike, which is where its NULL convention departs
    # from `emission_index`'s two rows below. Those are facts about WHERE THE
    # MODEL PUT an entry, so a constructed entry has none and NULL says so.
    # This is a fact about the TRIAL, which is equally true whether the model
    # answered for it or not -- so `_unevaluable_entry` stamps it too, and NULL
    # here means only "the trial dict carried no such field": a row written
    # before this column existed, a trial indexed before the admission pass
    # added the field, or a result dict built outside the pipeline.
    #
    # PLAIN TEXT WITH NO CHECK CONSTRAINT, on `matching_provider`'s and
    # `ecog_selection`'s footing: the vocabulary is owned by a module this one
    # may not import (`retrieval` sits above `storage` in the import graph and
    # importing it here would put a scraper in every batch run's import graph),
    # so a constraint would be a second copy of that vocabulary with nothing
    # failing when the two disagree. tests/test_storage_criteria_split_column.py
    # is what pins them to each other instead.
    "criteria_split":          "TEXT",
    # WHERE IN THE MODEL'S ANSWER THIS VERDICT STOOD. Both are stamped by
    # oncotriage/agent/evaluation.py on the parsed response, before any entry is
    # dropped and before the node's match_score sort, and they are the only
    # record of the order the model WROTE its answers in -- trial_number beside
    # them is the pipeline's own retrieval rank, which is a different fact and
    # is what this column pair was mistaken for while it did not exist.
    #
    #   emission_index  0-based position in the array THAT CALL returned.
    #                   GAPPY BY DESIGN: an entry the node dropped (a non-object,
    #                   an out-of-set id, a duplicate) keeps its position out of
    #                   the survivors' numbering rather than closing it up, so a
    #                   missing index is evidence rather than an inconsistency.
    #                   The count those positions are out of is NOT in this
    #                   table -- it is llm_classifier_call_details.entries_emitted,
    #                   which is per CALL and has no per-trial row to live on.
    #   call_index      1-based ordinal of the billed call that returned it,
    #                   joining that ledger by equality. 1-based because the
    #                   ledger is, and two fields of one run named call_index
    #                   disagreeing about their base is a silent off-by-one.
    #
    # NULL ON BOTH means one of three things and is never backfilled: the row
    # predates these columns; the entry was CONSTRUCTED by the pipeline rather
    # than returned by the model (a truncation floor, an exhausted split budget,
    # conflicting duplicates, or a trial the model never mentioned -- see
    # _unevaluable_entry, which sets both to None on purpose); or the result dict
    # was built outside the pipeline. The first is separable from the other two
    # by the run's date; the second and third are not separable here.
    #
    # THE SENTENCE THAT USED TO END THIS PARAGRAPH WAS FALSE AND WAS DELETED. It
    # said the second and third were told apart by "a not_evaluable_reason in
    # criterion_details". `not_evaluable_reason` was a key of the in-memory
    # evaluation dict and was NOT a column of this table and is still NOT a
    # member of criterion_details, which json.dumps exactly two keys,
    # "inclusion" and "exclusion" -- see criterion_json at the INSERT below.
    # HALF OF THAT IS NOW OUT OF DATE AND IS CORRECTED RATHER THAN LEFT: it IS a
    # column of this table as of the provenance pass, declared below, and it is
    # still not in criterion_details. What distinguishes a pipeline-constructed
    # non-evaluation from a model-returned one is these two columns themselves,
    # in the opposite direction to the paragraph above: the constructed ones are
    # the NULL population, and `verdict_source` below selects exactly the same
    # population for the same reason, which is what lets a reader cross-check
    # all three. A model-returned entry corrected to not_evaluable by Stage 5
    # (see UNEVALUABLE_REJECTION_UNSUPPORTED) carries real integers here.
    #
    # 0 IS A REAL POSITION AND MUST NOT BE READ AS ABSENT. The first entry of
    # the first call carries emission_index 0, so every reader has to test
    # IS NULL rather than falsiness -- the same rule ECOG and the cached-token
    # columns already carry.
    "emission_index":          "INTEGER",
    "call_index":              "INTEGER",
    # =======================================================================
    # WHAT STAGE 5's NORMALIZER DID TO THIS TRIAL
    # =======================================================================
    #
    # Five columns, one question each, all of them per patient-trial pair. Every
    # value is stamped by oncotriage/agent/evaluation.py at the line where the
    # correction is DECIDED, so the record and the behaviour cannot disagree,
    # and none of the five can be forged by the model: the Stage 5 response
    # schema is strict with `additionalProperties: false` and a complete
    # `required` list, and no name below is in TRIAL_FIELDS or CRITERION_FIELDS.
    #
    # --- not_evaluable_reason ----------------------------------------------
    #
    # WHY THIS TRIAL WAS RECORDED AS NOT EVALUATED. It has existed on the
    # in-memory entry since the reconciliation pass and was DROPPED HERE: the
    # INSERT below named nineteen columns and none of them was it, and
    # `criterion_details` json.dumps exactly "inclusion" and "exclusion". The
    # field was present on the dict at the line that wrote the row. The
    # paragraph above `emission_index` records that this column did not exist;
    # it does now, and that paragraph's warning about what `criterion_details`
    # does NOT contain still holds -- this is a column, not a JSON key.
    #
    # ELEVEN VALUES ARE REACHABLE, in THREE families a reader must not conflate.
    # The vocabulary is closed and its owner is
    # `oncotriage/agent/evaluation.py:NOT_EVALUABLE_REASONS`, which enumerates
    # all eleven and partitions them by WHO decided:
    #   CONSTRUCTED by the pipeline, the trial never having been answered --
    #     'truncation_floor', 'truncation_split_budget_exhausted',
    #     'omitted_from_model_response', 'conflicting_duplicate_answers',
    #     'per_trial_call_failed'
    #   CORRECTED -- the model answered and the answer could not be used --
    #     'trial-level verdict label not recognised'
    #     'model returned no criteria'
    #     'model rejection unsupported by its own criteria arrays'
    #     'no disqualifying row survived label normalisation'
    #     'trial-level verdict label unresolvable at finalization'  (Stage 6)
    #   DECLARED by the model, nothing corrected --
    #     'model declared this trial not evaluable'
    #   The CONSTRUCTED family is separable without reading the strings, by
    #   verdict_source below: such an entry never carried a model-written label,
    #   so its verdict_source is NULL. The other two are not separable that way
    #   -- both answered -- which is why each has its own reason rather than
    #   sharing one.
    #
    # NULL NOW MEANS EXACTLY ONE THING: the row predates this column. That is
    # the whole of the change; it used to mean three, and one of the three was
    # invisible. NEVER '' -- an empty string would be a reason of zero
    # characters, which is not a reading of anything.
    #
    # WHAT THE PARAGRAPH HERE USED TO SAY, AND WHY IT IS GONE RATHER THAN
    # EDITED. It recorded a DELIBERATE GAP: Stage 5's Step 2 -- a trial the
    # model returned with no criteria -- put its reason in an audit list and did
    # not stamp the entry, left open because `not_evaluable_reason` is one of
    # the per-verdict keys the twelve characterization fixtures compare and
    # writing it where it had not been written costs a re-capture. It also
    # offered a four-term predicate that made the population "IDENTIFIABLE
    # without reading prose":
    #     eligible = 'not_evaluable' AND verdict_source IS NOT NULL
    #     AND not_evaluable_reason IS NULL
    #     AND criterion_details = '{"inclusion": [], "exclusion": []}'
    # THAT PREDICATE DID NOT SEPARATE WHAT IT SAID IT SEPARATED. Its last term
    # was justified by the model-DECLARED population having non-empty arrays;
    # the prompt's Section 1 requires a not_evaluable trial's arrays to be
    # EMPTY, so a model-declared non-evaluation satisfies all four terms and was
    # reported as the Step 2 defect. Two populations, one bucket, no column able
    # to tell them apart. Both are stamped now and the predicate is unnecessary.
    #
    # THE FIXTURE COST WAS MEASURED RATHER THAN ESTIMATED BEFORE IT WAS PAID:
    # across all twelve recorded fixtures there is exactly ONE not_evaluable
    # verdict and it already carried a reason from a branch this change does not
    # touch, so no fixture-compared value moves.
    "not_evaluable_reason":    "TEXT",
    # --- verdict_source / verdict_original_label / verdict_original_type ---
    #
    # HOW THE MODEL'S TRIAL-LEVEL LABEL WAS READ. `normalize_trial_verdict`
    # resolves a written label into the three-member vocabulary and reports HOW;
    # before this pass that report reached one log line and nothing else, so a
    # stored 'eligible' could not be told from a stored 'eligible' recovered
    # from boolean `True`.
    #
    # verdict_source IS WRITTEN ON EVERY MODEL-RETURNED ROW, INCLUDING THE
    # ORDINARY ONE, and that is the design rather than an oversight:
    #   'canonical'    the label was read and needed no recovery. A MEASUREMENT,
    #                  on exactly the footing `hallucinated = 0` is one.
    #   'normalized'   case, whitespace or one of the four synonyms recovered it.
    #   'unrecognized' it could not be resolved at all; the branch chain then
    #                  decided what to record, which is why an 'unrecognized'
    #                  row can still carry any of the three verdicts.
    #   NULL           NO NORMALIZER RAN FOR THIS ROW. True of every entry the
    #                  pipeline CONSTRUCTED (a truncation floor, an exhausted
    #                  split budget, a model omission, conflicting duplicates --
    #                  they are appended after the normalizer loop and never had
    #                  a model-written label), of a result dict built outside the
    #                  pipeline, and of a row written before this column.
    #                  NULL here and NULL on emission_index/call_index select the
    #                  same population for the same reason.
    #
    # THE OTHER TWO ARE NULL WHENEVER THE LABEL WAS CANONICAL, because for a
    # canonical label the "original" IS `eligible`, stored two columns away, and
    # a repr of it would be a second copy of a value that cannot disagree with
    # itself. They are also NULL when verdict_source is.
    #
    #   verdict_original_label  repr() of what the model wrote, capped. repr and
    #                           not the value: it is output of unknown type and
    #                           unknown length, and '' and None must not read
    #                           alike. Kept off the structured LOG deliberately
    #                           (it is not on LOGGABLE_FIELDS) and stored HERE
    #                           deliberately -- this table already holds every
    #                           criterion string in criterion_details and the
    #                           whole response in
    #                           inferences.llm_classifier_raw_response, so a
    #                           capped verdict label is strictly inside what it
    #                           carries, and it is the only thing that makes the
    #                           normalisation auditable per row.
    #   verdict_original_type   type(raw).__name__ -- 'bool', 'NoneType',
    #                           'dict', 'str'. This is what the campaign's
    #                           "from what original types" question groups by,
    #                           and it carries no content at all.
    "verdict_source":          "TEXT",
    "verdict_original_label":  "TEXT",
    "verdict_original_type":   "TEXT",
    # --- criterion_remaps ---------------------------------------------------
    #
    # HOW MANY OF THIS TRIAL'S CRITERION LABELS WERE OUTSIDE THEIR ARM'S
    # VOCABULARY. `inferences.cross_vocab_remaps` has always carried the RUN's
    # event total; which trial each event belonged to was lost, so "how many
    # trials were affected" -- a different number, and the one that matters for
    # a per-trial finding -- was unanswerable.
    #
    #   0     the normalizer read this trial's arrays and rewrote nothing.
    #         MEASURED, exactly like `hallucinated = 0`.
    #   n>0   n remap EVENTS on this trial.
    #   NULL  no normalizer ran for this row -- the same population as
    #         verdict_source IS NULL.
    #
    # THE SUM OF THIS COLUMN OVER ONE INFERENCE'S ROWS EQUALS THAT INFERENCE'S
    # cross_vocab_remaps, and that invariant is checkable in one query. It holds
    # because both come from the same list, counted at the same line.
    #
    # EVENTS, NOT STORED ROWS. A criterion entry that was not an object at all
    # is DROPPED rather than relabelled, so it counts here and leaves nothing in
    # criterion_details -- meaning this number can exceed the number of rows in
    # that JSON carrying `remapped_from_status`, and the difference IS the
    # number dropped.
    #
    # IT DOES NOT COUNT THE ABSENT-DATA VALIDATOR'S REWRITES, which also produce
    # 'not_evaluable' statuses and are a different finding with their own audit
    # list and their own log event. Folding them in would make one column mean
    # two things and would break the sum above.
    "criterion_remaps":        "INTEGER",
}


#------------------------------------------------------------------------------


# ===========================================================================
# RUN IDENTITY (the run-identity pass)
# ===========================================================================
#
# WHAT WAS MISSING. `inferences` and `trial_matches` are per-PATIENT records.
# Neither carries anything about the CAMPAIGN that produced them, so "which
# rows belong to one batch run" was recovered by looking for gaps between
# consecutive `timestamp` values -- a heuristic that is wrong in four separate
# ways and silent in all of them:
#
#   * a RESUMED run reads as two campaigns, because the gap is the interruption;
#   * two campaigns started back to back read as one, because there is no gap;
#   * an API row written by "17- FastAPI Server.py" during a batch run is
#     indistinguishable from a batch row, because both land in the same file;
#   * and no gap between timestamps says anything about the CONFIGURATION,
#     which is what a run-level number actually needs to be attributed to.
#
# `runs` is a real thing to attach to. The batch runner creates one row before
# its first patient and finalizes it at the end; every write that run makes
# carries its id.
#
# WHY IT IS HERE AND NOT IN oncotriage/tracking.py. That module indexes runs
# for a HUMAN comparing campaigns, in a store that is not this database, and it
# is optional -- `TRACKING_DEGRADATIONS` exists precisely because an index
# failure must not take the run with it. This is a JOIN KEY inside the database
# the rows are in, and a query that needs it cannot reach across to an MLflow
# file store. They answer different questions and both are kept.


RUN_RECORD_STATUS_RUNNING = "RUNNING"
"""The status a run row carries between creation and finalization.

DELIBERATELY NOT IN ``oncotriage/tracking.py``'s ``RUN_STATUSES``, which is
``("FINISHED", "FAILED", "KILLED")`` and excludes ``RUNNING`` on the argument
written there: passing ``RUNNING`` to ``end_run`` would leave a finished run
looking live forever. That argument is about the END of a run. This table
records the START of one as well, so it needs the live value that tuple omits.
"""

RUN_RECORD_STATUS_STOPPED = "STOPPED"
"""A run that stopped cleanly between patients rather than covering its cohort.

NOT ``KILLED`` AND NOT ``FINISHED``, and it is a third value rather than a flag
on either because it answers a question neither can. ``KILLED`` means the
process did not get to the end -- an unhandled exception, a Ctrl-C, a SIGTERM --
so its patients were abandoned mid-flight and the record may be short of what
was billed. ``FINISHED`` means the campaign covered its cohort. A stopped run is
neither: every patient it started completed and was written, and patients remain
that it never began. An operator reading `runs` needs to tell "I asked for this"
from "something went wrong", because only the second is worth investigating, and
a reviewer needs to tell "this campaign covers the cohort" from "this campaign
covers a prefix of it", because only the first supports a rate.

THREE MECHANISMS PRODUCE IT AND ``runs.stop_reason`` IS WHAT SAYS WHICH.
This paragraph named one -- ``oncotriage/batch/runner.py:STOP_SWITCH``, the
sentinel file polled between patients at the checkpoint's own cadence -- and it
was true of every STOPPED row written before era 7. The other two are
``oncotriage/spend.py``'s cap and its per-invocation billed-call ceiling. All
three end a run in exactly the shape this status describes, which is why they
share it; see ``RUN_STOP_REASONS`` for why the distinction is a column rather
than three statuses.

MLflow HAS NO SUCH STATUS, which is why this tuple stops being value-identical
to ``tracking.RUN_STATUSES`` -- see ``RUN_RECORD_STATUSES_BEYOND_TRACKING``.
"""

RUN_RECORD_STATUSES_BEYOND_TRACKING = (RUN_RECORD_STATUS_STOPPED,)
"""The terminal statuses this table has and ``oncotriage/tracking.py`` does not.

A NAMED DIVERGENCE RATHER THAN AN UNDECLARED ONE. Before the stop switch these
two vocabularies were value-identical and a test asserted exactly that, which is
the right check for two restated copies of one fact. They are no longer one
fact: MLflow's terminal vocabulary is FINISHED / FAILED / KILLED and this
project does not get to widen it (``oncotriage/tracking.py:RUN_STATUSES`` says
so, and ``oncotriage/batch/runner.py``'s crash handler already records the same
kind of divergence for KILLED). So the relation is now

    RUN_RECORD_TERMINAL_STATUSES == tracking.RUN_STATUSES
                                    + RUN_RECORD_STATUSES_BEYOND_TRACKING

and ``tests/test_storage_run_identity.py`` asserts THAT, in order. The
difference matters: an equality check would have to be deleted to add a status,
and a deleted check protects nothing, whereas this one still fails when a
status is added to one side and named in neither.

WHAT A CALLER DOES WITH IT: ``oncotriage/batch/runner.py`` maps STOPPED to
MLflow's KILLED at ``tracking.end_run`` -- "run killed by user", which is
literally what a stop switch is -- and records the mapping at the call site.
"""

RUN_RECORD_STATUS_FINISHED = "FINISHED"
"""The campaign ran to the end and covered its cohort.

NAMED, LIKE ITS TWO NEIGHBOURS, BECAUSE IT HAS CALLERS. ``RUNNING`` and
``STOPPED`` were named from the start and these three were not, so
``oncotriage/batch/runner.py`` wrote them out as bare literals -- eight of them,
in three places, deriving the SAME verdict twice under a comment arguing that
the two derivations must not disagree. A value with a name can be imported; a
literal can only be retyped.
"""

RUN_RECORD_STATUS_FAILED = "FAILED"
"""The campaign ran to the end and some of its patients errored.

NOT ``KILLED``: every patient was attempted. See ``RUN_RECORD_STATUS_KILLED``.
"""

RUN_RECORD_STATUS_KILLED = "KILLED"
"""The process did not get to the end -- an unhandled exception, a Ctrl-C, a SIGTERM.

Distinct from ``FAILED`` because only this one has patients that were never
attempted, and distinct from ``STOPPED`` because nobody asked for it.
"""

RUN_RECORD_TERMINAL_STATUSES = ((RUN_RECORD_STATUS_FINISHED,
                                 RUN_RECORD_STATUS_FAILED,
                                 RUN_RECORD_STATUS_KILLED)
                                + RUN_RECORD_STATUSES_BEYOND_TRACKING)
"""How a run ENDED.

RESTATED RATHER THAN IMPORTED, and the reason is layering, not taste:
``oncotriage.tracking`` imports ``oncotriage.agent.prompts``, so a
``from oncotriage.tracking import RUN_STATUSES`` here would make the STORAGE
layer depend on the AGENT layer -- the edge pass 20c-2c moved
``_resolve_primary_cancer`` out of this module to remove.

A RESTATED CONSTANT IS A CONSTANT THAT CAN DRIFT, so the alignment is checked
rather than promised: ``tests/test_storage_run_identity.py`` imports both and
requires this tuple to equal ``tracking.RUN_STATUSES`` plus
``RUN_RECORD_STATUSES_BEYOND_TRACKING``, in that order. A test may import both
because a test is not in the import graph either module ships.

THE FIRST THREE ARE THIS MODULE'S OWN LITERALS AND ARE NOT READ OUT OF
``oncotriage.tracking``, deliberately: they are the restated copy, and deriving
them FROM THE MODULE THEY ARE CHECKED AGAINST would make the round-trip check
agree with itself by construction.

THEY ARE NOW REACHED THROUGH THREE NAMES RATHER THAN TYPED INTO THIS TUPLE, and
that does NOT weaken the check, which is worth stating because an earlier
version of this paragraph forbade "deriving them from anything in this module"
and would have read as forbidding it. ``RUN_RECORD_STATUS_FINISHED`` and its two
neighbours are literals declared HERE; the comparison is still this module's
independent text against ``tracking.RUN_STATUSES``'s independent text, and it
still fails if either moves. What changed is only that a CALLER can now name a
status instead of retyping it -- which is what ``oncotriage/batch/runner.py``
needed and did not have.
"""

RUN_RECORD_STATUSES = (RUN_RECORD_STATUS_RUNNING,) + RUN_RECORD_TERMINAL_STATUSES
"""Every value ``runs.status`` may hold. CLOSED.

Closed for ``deps.OVERRIDE_KEYS``' reason: a caller may branch on it
exhaustively, and a status outside it is a run that no ``WHERE status = ...``
will ever return.
"""


CAMPAIGN_RESUMABLE_STATUSES = ("KILLED", "FAILED", "STOPPED")
"""The statuses a resumed run may attach to.

``RUN_RECORD_TERMINAL_STATUSES`` minus FINISHED, and a strict subset of it --
``oncotriage/storage/queries.py`` carries the guard that says so, beside the
SQL it protects.

WRITTEN OUT RATHER THAN DERIVED as ``[s for s in RUN_RECORD_TERMINAL_STATUSES
if s != "FINISHED"]``, which was the obvious form and is the wrong one: a
terminal status added tomorrow would silently become resumable without anybody
deciding that it should. Listing them makes that guard a real check and makes a
new status a deliberate edit here.

TWO CONSUMERS, ONE OWNER. ``queries.campaign_summary`` stitches every campaign
in the table with a recursive CTE; ``campaign_spend_before`` below walks ONE
chain backwards to seed a resumed run's spend gate. They must agree about what
a campaign IS or a budget would be computed over a different set of runs than
the report that presents it -- so they read one tuple rather than two.

IT LIVED IN ``queries.py`` UNTIL THE SPEND-GATE PASS and moved here rather than
being copied, because the second consumer is one layer DOWN: ``queries`` imports
this module, so this module cannot import it back.

STOPPED IS RESUMABLE FOR THE SAME REASON KILLED IS, and more strongly. A stop is
an operator -- or, since era 7, the spend gate -- saying "pause this campaign";
the checkpoint is intact by construction, and the whole point is that the next
invocation picks up where it left off. A stopped fragment that did NOT stitch
would report the resumed half as a separate campaign covering a fraction of the
cohort.
"""


RUN_STOP_REASON_OPERATOR = "operator"
"""A human wrote the ``STOP`` sentinel. ``oncotriage/control.py``'s switch."""

RUN_STOP_REASON_SPEND_CAP = "spend_cap"
"""The campaign reached ``config.SPEND_CAP_USD``. See ``oncotriage/spend.py``."""

RUN_STOP_REASON_CALL_CEILING = "call_ceiling"
"""One Stage 5 invocation hit its derived billed-call ceiling.

NOT a budget event and it must not be read as one: the campaign may be nowhere
near its cap. It means a single invocation asked for more billed calls than the
configuration can legitimately produce, which is a defect in this pipeline.
"""

RUN_STOP_REASONS = (RUN_STOP_REASON_OPERATOR,
                    RUN_STOP_REASON_SPEND_CAP,
                    RUN_STOP_REASON_CALL_CEILING)
"""Every value ``runs.stop_reason`` may hold. CLOSED.

WHY THIS IS A COLUMN AND NOT THREE MORE MEMBERS OF ``RUN_RECORD_STATUSES``, and
the alternative was built far enough to be measured before it was rejected.

``runs.status`` answers HOW A RUN ENDED, and its four members partition that
question. A spend stop's answer to it is BYTE-IDENTICAL to an operator stop's:
every patient it started completed and was written, patients remain that it
never began, the checkpoint is intact, and a resume continues. That is
``RUN_RECORD_STATUS_STOPPED``'s definition, unamended.

So a fifth and sixth status would be two more members that every exhaustive
consumer -- ``TRACKING_STATUS_FOR``, ``CAMPAIGN_RESUMABLE_STATUSES``,
``campaign_summary``'s generated predicate, the Run Health tab's
``health_record`` CASE, ``tests/test_storage_run_identity.py``'s composition pin
-- would have to learn, and every one of them would answer IDENTICALLY for all
three. A distinction on which no consumer branches differently is a distinction
that belongs in a different column; that is ``verdict_source``'s argument one
table over, where ``canonical`` is a value of its own column rather than a
fourth member of ``eligible``.

WHAT THE COLUMN BUYS THAT ``note`` CANNOT. ``runs.note`` already carries the
operator's own words and is deliberately free text -- unbranchable, ungroupable,
and NULL on every stop where nobody typed anything (``touch`` is the documented
gesture). "Which mechanism stopped this campaign" is a ``GROUP BY``, and a
``GROUP BY`` over free text is the query item 38 had to remove from
``pipeline_consistency``.

NULL MEANS "NOT A STOP, OR A ROW WRITTEN BEFORE ERA 7" and those two are not
separable in this column alone -- ``status`` separates them, which is why they
can share a NULL. A FINISHED or KILLED row carries NULL here by construction.

**ONE CORRECTION THIS FORCES, MADE IN THE SAME COMMIT.**
``RUN_RECORD_STATUS_STOPPED``'s docstring said "a run AN OPERATOR ASKED TO
STOP" and named the sentinel as "THE SWITCH THAT PRODUCES IT". That was true of
every STOPPED row ever written and stopped being true here; the docstring now
names all three producers and points at this tuple.
"""

if len(set(RUN_STOP_REASONS)) != len(RUN_STOP_REASONS):
    # A RuntimeError and not an `assert`: `python -O` deletes assert statements,
    # and a duplicated member would make a GROUP BY over this column report two
    # mechanisms as one -- which is the whole thing the column exists to
    # prevent.
    raise RuntimeError(
        f"RUN_STOP_REASONS must have no duplicates; it is {RUN_STOP_REASONS!r}")



RUN_FINGERPRINT_COLUMNS = (
    "fingerprint_version",
    "llm_classifier_prompt_version",
    "llm_classifier_renderer_digest",
    "matching_model_configured",
    "matching_call_mode",
    "qdrant_collection",
    "collection_points",
    "data_snapshot_date",
)
"""The configuration stamp, as columns, in ``run_fingerprint``'s own order.

INDIVIDUAL COLUMNS AND NOT A JSON BLOB. Plain-SQL queryability is the standing
precedent in this schema -- `mesh_resolution`, `query_expansion_path` and the
four `*_filter_applied` pairs are all scalars for the same reason -- and the
question these exist to answer is "every run whose renderer digest was X", or
"...whose collection had fewer than N points", which `json_extract` over a blob
answers only for a reader who knows the blob's shape.

THE NAMES ARE ``("fingerprint_version",) + run_fingerprint.FINGERPRINT_FIELDS``
AND THEY ARE RESTATED HERE FOR THE LAYERING REASON ABOVE, one level worse:
``oncotriage.run_fingerprint`` imports ``oncotriage.agent.prompts`` AND
``oncotriage.agent.readiness``, and readiness builds a Qdrant client. A storage
module that imported it would put the agent, and a network probe's import
graph, behind ``import oncotriage.storage.database_logger``.

So the round trip is CLOSED BY A TEST, not by an import:
``tests/test_storage_run_identity.py`` requires this tuple to equal
``("fingerprint_version",) + FINGERPRINT_FIELDS`` exactly, in order. A field
added to the stamp and not to this tuple fails there with a name, rather than
being recorded in the stamp and silently absent from every run row.
"""

RUN_FINGERPRINT_INTEGER_COLUMNS = frozenset({
    "fingerprint_version",
    "collection_points",
})
"""Which stamp fields are stored as numbers, and therefore NULLed when unknown.

WHY THIS IS NOT COSMETIC. ``run_fingerprint`` degrades an unresolvable field to
the STRING ``"unknown"`` -- `UNKNOWN` in that module -- and the five TEXT
columns store that verbatim, which is exactly right for them. Storing it in an
INTEGER-affinity column is the ``ecog_date`` trap, one column type over: SQLite
keeps a non-numeric string as TEXT whatever the declared affinity, and it orders
every TEXT value ABOVE every INTEGER, so

    WHERE collection_points > 1000

would return the rows where the count could not be established, and
``ORDER BY collection_points DESC`` would rank them as the largest collections
there are. That is the opposite of the truth in the one column whose purpose is
saying how much was indexed.

So a non-int reaches these columns as NULL, and the fact is not lost: the two
questions are answered by two different predicates.

    fingerprint_version IS NULL                        no stamp was recorded
    fingerprint_version IS NOT NULL
      AND collection_points IS NULL                    a stamp was recorded and
                                                       the count was not
                                                       established -- read
                                                       qdrant_collection, which
                                                       is TEXT and says
                                                       'unknown' when even the
                                                       NAME did not resolve

A STAMP CARRYING NO ``fingerprint_version`` IS TREATED AS NO STAMP, which is
``run_fingerprint``'s own FP_ABSENT rule ("nothing recorded, or a stamp with no
version") applied at the write. ``bool`` is excluded from the int test because
``isinstance(True, int)`` is True and ``collection_points = 1`` would be a
plausible-looking lie.
"""

def _last_wins(*groups) -> tuple:
    """The concatenation of ``groups`` with duplicates removed, LAST WINS.

    One column can legitimately be named by two of the sources ``RUN_COLUMNS``
    is built from -- ``matching_call_mode`` is a stamp field AND an additive
    column, for the two orthogonal reasons argued at ``RUN_COLUMN_ADDITIONS`` --
    and naming it twice in an INSERT's column list is an
    ``OperationalError: duplicate column name``, at the write, on every run.

    LAST WINS RATHER THAN FIRST, AND THAT IS WHAT KEEPS THE ORDER TRUE. A column
    in the additions dict physically lands where ``ALTER TABLE`` appends it, at
    the end -- so keeping the FIRST occurrence would put it at its stamp
    position and make this tuple describe a column order no database has.
    Keeping the last puts every additive column after every base one, which is
    exactly the physical order of both a fresh database and a migrated one (see
    ``RUN_COLUMN_ADDITIONS`` for why those two agree).

    Deliberately not ``dict.fromkeys``, which keeps the first.
    """
    ordered = []
    for name in (name for group in groups for name in group):
        if name in ordered:
            ordered.remove(name)
        ordered.append(name)
    return tuple(ordered)


RUN_COLUMNS = _last_wins(("started_at", "finished_at", "status",
                          "invocation_source"), RUN_FINGERPRINT_COLUMNS,
                         tuple(RUN_COLUMN_ADDITIONS))
"""Every column ``start_run_record`` writes, in the CREATE TABLE's order.

ONE DECLARATION. The INSERT's column list and its placeholder count are both
built from this tuple, so they cannot disagree with each other the way a
hand-written positional VALUES tuple can -- which is a live risk in this module:
the ``inferences`` INSERT names 85 columns positionally and its comment says in
as many words that a loop there would put the column order in two places. That
argument is about a tuple whose values are hand-picked per column. This one's
values are looked up BY NAME out of a dict, so deriving the list removes a
failure mode instead of hiding one.

RUN_COLUMN_ADDITIONS IS APPENDED RATHER THAN LISTED, so an entry added to that
dict is written by this INSERT without a second edit here. The order matches the
migration's: base columns in CREATE TABLE order, then additions in dict order,
which is the order ALTER TABLE appends them in -- so the tuple describes the
real column order of a migrated table rather than a plausible one.

THAT ORDER IS WHY THE DE-DUPLICATION KEEPS THE LAST OCCURRENCE. A column named
by BOTH ``RUN_FINGERPRINT_COLUMNS`` and ``RUN_COLUMN_ADDITIONS`` -- which
``matching_call_mode`` is -- must appear once, and it must appear where the
ALTER actually put it. See ``_last_wins`` directly above. Without the
de-duplication the INSERT names that column twice and raises
``duplicate column name`` on the first run of every campaign.

A COLUMN IN THAT DICT MUST THEREFORE HAVE A KEY IN start_run_record's `values`,
and a KeyError at the INSERT is what says it does not. That is the intended
failure: silent is the alternative, and a column added to the schema and never
written is the shape this project treats as a defect.
"""


RUN_RECORD_FAILURES = Counter()
"""Run rows that could not be created or finalized, keyed by what went wrong.

Module-level, following ``INFERENCE_WRITE_FAILURES`` immediately below rather
than becoming a column -- for that counter's reason, sharpened: a column
recording that the run row could not be written would have to live on the run
row that does not exist.

Keys are ``finalize:{ExceptionType}``, ``finalize:no_run_id``,
``finalize:row_not_found`` and ``finalize:unknown_status:{status}``. THERE IS NO
``start:`` KEY AND THAT IS NOT AN OMISSION: ``start_run_record`` raises, so a
creation failure stops the run rather than being counted and continued past.
"""


#------------------------------------------------------------------------------


# ===========================================================================
# RUN METRICS: THE HEALTH RECORD, WRITTEN WHILE THE RUN IS STILL ALIVE
# ===========================================================================
#
# THE GAP. ``oncotriage/degradation.py`` reads twenty-odd module-level counters
# at the END of a run and prints them. That block is the only reader, so the
# whole health record of a campaign lives in one process's memory until the
# moment it exits -- and a campaign that CRASHES prints nothing, which means a
# 22,000-patient run that died at patient 19,000 leaves no record of what
# degraded on the way. The `runs` row it leaves behind says RUNNING with a NULL
# `finished_at` and nothing else. Nor can anything watch a live run: the numbers
# exist, and there is no way to ask for them from outside the process.
#
# `run_metrics` is that record, on disk, refreshed as the run proceeds.
#
# NARROW, ON `drift_metrics`' PRECEDENT. (run_id, category, name, value) plus
# when it was written. The alternative -- one column per counter -- would mean a
# schema migration every time a counter joins the registry, which is exactly the
# trade `drift_metrics` already declined for metric names that grow the same way.
#
# WHAT MAY GO IN IT, AND THE LINE IS NOT A CONVENTION -- IT IS ENFORCED BELOW.
# Counter NAMES and TOTALS only, which is ``degradation.totals()``'s output and
# deliberately NOT ``degradation.snapshot()``'s. That module's own docstring is
# the authority: counter KEYS carry third-party and clinical text --
# SEX_UNKNOWN_KEPT is keyed by the patient's recorded sex, M_CATEGORY_UNREADABLE
# by a capped copy of an observation's display text -- and this table is a
# DURABLE, run-keyed record, which is precisely what LOGGABLE_FIELDS exists to
# keep that text out of. The detail keeps going to the console, which is
# transient and unindexed.
#
# `_run_metric_rows` refuses a mapping that is not name->int, and it identifies
# a name by ``str.isidentifier()`` -- counter names are Python module-level
# variable names by construction, and no key this project produces is one
# ("unconverted:Sodium mmol/L" is not). That is the mechanical guarantee, rather
# than an instruction in a docstring that a future caller reads or does not.


RUN_METRIC_CATEGORY_DEGRADATION = "degradation"
"""``run_metrics.category`` for a row that IS one of the registry's counters."""

RUN_METRIC_CATEGORY_META = "meta"
"""``run_metrics.category`` for a row ABOUT the flush rather than about the run.

SEPARATE FROM THE COUNTERS, so ``WHERE category = 'degradation'`` is the set of
things that actually degraded and nothing else. A meta row under the same
category would be summed by every aggregate over that column.
"""

RUN_METRIC_CATEGORIES = (RUN_METRIC_CATEGORY_DEGRADATION,
                         RUN_METRIC_CATEGORY_META)
"""Every value ``run_metrics.category`` may hold. CLOSED, on
``RUN_RECORD_STATUSES``' footing: a reader may branch on it exhaustively, and a
category outside it is a row no ``WHERE category = ...`` will return.
"""


RUN_METRIC_META_COUNTERS_REGISTERED = "counters_registered"
"""How many counters were CONSULTED, whatever they read.

THE ROW THAT MAKES SILENCE READABLE, and it is the reason a clean run writes
anything at all. ``totals()`` drops every zero counter, so a run that degraded
in no way contributes no `degradation` rows -- and "no rows" is exactly what a
run whose flushing was never wired up looks like, and what a run that crashed
before its first flush looks like. With this row:

    no run_metrics rows for a run_id        nothing ever flushed for it
    counters_registered = N, no others      N counters were read and all N were
                                            zero -- a MEASUREMENT of health
    counters_registered = N, plus rows      those are what moved

Same argument, one layer down, as ``degradation.report_lines`` printing "all N
counters are zero" rather than printing nothing.
"""

RUN_METRIC_META_COUNTERS_NONZERO = "counters_nonzero"
"""How many of the consulted counters were non-zero at this flush.

Derivable by counting the `degradation` rows, and stored anyway: it is the one
value that lets ``WHERE category='meta'`` alone answer "was this run clean",
without a second query whose empty result has two possible meanings.
"""

RUN_METRIC_META_NAMES = (RUN_METRIC_META_COUNTERS_REGISTERED,
                         RUN_METRIC_META_COUNTERS_NONZERO)
"""Every ``name`` written under ``RUN_METRIC_CATEGORY_META``. CLOSED."""


RUN_METRICS_FLUSH_FAILURES = Counter()
"""Health flushes that did not land, keyed by what went wrong.

MODULE-LEVEL, AND THE REQUIREMENT IS SHARPER HERE THAN FOR ITS NEIGHBOURS: this
counter records the failure of the mechanism that persists counters, so a row in
`run_metrics` recording it could only be written by the thing that just failed.
It is registered in ``oncotriage/degradation.py``'s ``_REGISTRY_SPEC`` and read
by the run-end block, which needs no database at all.

Keys are ``flush:no_run_id``, ``flush:not_a_mapping:{type}``,
``flush:bad_registered_count:{type}``, ``flush:non_integer_value``,
``flush:nested_value`` (the ``snapshot()``-instead-of-``totals()`` mistake, which
is the one that would carry clinical text), ``flush:non_identifier_name`` and
``flush:{ExceptionType}``.

THE TWO SHAPE KEYS DELIBERATELY CARRY NO OFFENDING VALUE. Every other counter in
this project keys by the thing that went wrong; this one may not, because the
thing that went wrong here IS the text this table exists to exclude. The value
goes to the console -- once per process per reason, `_apply_journal_mode`'s
"loud, once" shape -- where it is transient.

IT IS ALWAYS ONE FLUSH BEHIND ITSELF, stated rather than discovered: a failure
counted during flush N is written to the table by flush N+1, and a failure in
the LAST flush of a run never reaches the table at all. That is inherent -- see
the first paragraph -- and it is why the run-end degradation block, not this
table, is the authority on it.
"""

_RUN_METRIC_SHAPE_ANNOUNCED = set()
"""Reasons a malformed flush has already been announced on the console.

The flush runs once per patient, so a caller passing the wrong mapping would
otherwise print the same line 22,000 times. The COUNTER still increments every
time, so the total is honest; only the console line is deduplicated.
"""

_ANNOUNCE_LOCK = threading.Lock()
"""Guards ``_RUN_METRIC_SHAPE_ANNOUNCED``'s check-then-act.

A LOCK OF ITS OWN RATHER THAN ``_WRITE_LOCK``, deliberately. That lock's
invariant -- and the thing ``tests/test_package_invariants.py`` section 5e's
control counts by stripping -- is "every DATABASE STATEMENT in this file is
issued under ``_WRITE_LOCK``". This guards a set, not a statement, and
borrowing the write lock for it would make that count mean two things at once.

A plain ``Lock``, not an ``RLock``: nothing taken under it re-enters.
"""


#------------------------------------------------------------------------------


# Who calls initialize_database(), and when.
#
# Both, deliberately:
#
#   - Any caller may call it explicitly to build or migrate a database at a
#     path of its choosing. That is what makes it testable without
#     monkey-patching a global, which is why it takes db_path as an argument.
#
#   - log_inference() ensures the schema itself, once per resolved path,
#     immediately before its first write.
#
# The second is not redundancy, it is the answer to "what stops a caller that
# never called it from writing to a database with no tables". Relying on entry
# points alone would fail silently here: log_inference deliberately swallows
# sqlite3.Error so a logging fault cannot kill the pipeline, so a missing table
# would surface as one "Database logging failed" line per patient and a run
# that records nothing. Worse, the tests that repoint inferences_path at a
# temporary file (36, 37, 38, 40, 45) would each need a new explicit call, and
# any future caller that forgot one would get the same silent hole.
#
# Ensuring on first use makes the never-initialized state unreachable rather
# than merely detectable. The cost is one connection per distinct path per
# process; _INITIALIZED_DATABASES keys on the resolved absolute path so a test
# that repoints inferences_path is initialized again, and a batch run of 22k
# patients pays for it once.
#
# The path is recorded only after the work succeeds, so a failed attempt is
# retried on the next call instead of being remembered as done.
_INITIALIZED_DATABASES = set()


#------------------------------------------------------------------------------


# ===========================================================================
# WRITE DURABILITY (the write-durability pass)
# ===========================================================================
#
# THE DEFECT. ``_write_inference_row`` catches ``sqlite3.Error``, rolls back,
# prints "Database logging failed (non-critical)" and continues -- and
# ``log_inference`` then returns ``db_path`` exactly as it does on success. The
# caller cannot tell the row was lost, so the patient is recorded as successful
# and the run reports complete. Every number in the paper comes from one final
# run; if that run loses rows and reports complete, the result looks whole and
# is not.
#
# NOT RE-RAISING IS STILL RIGHT. The existing comment is correct that a logging
# fault must not destroy a ~70-second pipeline result that cost a live Stage 5
# call. What was wrong was that it also did not TELL anyone. Three things close
# that, in the order they take effect:
#
#   1. WAL and an explicit busy timeout, so contention mostly does not happen;
#   2. a bounded retry, so transient contention that does happen is survived;
#   3. an outcome the caller can read, and a counter, so a write that is lost
#      anyway is visible from the return value, from the log, and from the
#      batch summary's reconciliation.
#
# WHAT THIS DELIBERATELY DOES NOT TOUCH: ``_WRITE_LOCK``, the schema, and the
# broad ``except Exception`` below ``except sqlite3.Error``. The lock closes the
# IN-PROCESS race and is measured doing so by
# ``tests/test_package_invariants.py`` section 5e; everything here is about the
# processes it cannot reach.


INFERENCE_WRITE_FAILURES = Counter()
"""Inference writes that were given up on, keyed ``{ExceptionType}:{retryable}``.

Module-level, following ``AGE_PARSE_FAILURES`` and ``CHECKPOINT_WRITE_FAILURES``
rather than becoming a new column: this is a property of the RUN, and a new
column would mean a schema migration to record that a row could not be written,
which is circular.

The ``retryable`` half is the diagnosis. ``sqlite3.OperationalError:retryable``
means contention outlived ``SQLITE_WRITE_MAX_ATTEMPTS`` and the fix is more
attempts, a longer timeout or fewer writers. ``sqlite3.IntegrityError:terminal``
means the write was never going to succeed and retrying it would only have made
the run slower.
"""

INFERENCE_WRITE_RETRIES = Counter()
"""Retries actually made, keyed by exception type. Attempts, not calls.

Separate from the failure counter because the two answer different questions: a
run with 400 retries and 0 failures is one where this pass did its job, and a run
with 0 of each is one where there was no contention to survive. Folding them
together would make those two indistinguishable.
"""

WRITE_RETRY_OUTCOMES = Counter()
"""What ``run_with_write_retry`` did, keyed ``{outcome}:{ExceptionType}``.

THE GENERIC HELPER'S COUNTER, AND IT IS NOT ``INFERENCE_WRITE_RETRIES``. That one
belongs to ``_write_inference_row_with_retry``, which owns its own loop, its own
outcome dict and its own failure counter. ``run_with_write_retry`` is the helper
every OTHER write in the project retries through -- today the ablation study's
three -- and until this counter existed it incremented nothing at all: it printed
a console line, emitted a log record, and left no total. A study that retried
four hundred times to lose nothing was indistinguishable, at the end of the run,
from one that met no contention, and the two have opposite implications for what
the next increment of load costs.

THREE OUTCOMES, A CLOSED VOCABULARY, AND THE FIRST ONE ALONE IS NOT ENOUGH:

``retried:{Type}``    one per retry SCHEDULED -- per sleep, not per call. The
                      direct analogue of ``INFERENCE_WRITE_RETRIES``.
``recovered:{Type}``  one per CALL that retried at least once and then returned.
                      This is the key that makes "retried and lost nothing"
                      sayable; ``retried:`` on its own is equally consistent with
                      a call that retried and then gave up.
``exhausted:{Type}``  one per CALL that ran out of ``SQLITE_WRITE_MAX_ATTEMPTS``
                      while the error was still retryable, and re-raised.

THERE IS DELIBERATELY NO ``terminal:`` KEY. An error ``_is_retryable`` refuses is
not a retry outcome -- nothing was retried -- and the caller's own ``except`` is
what counts it, with the caller's own semantics. A key here would be a second,
differently-scoped tally of the same event in a report that already carries the
caller's.

WHAT IT DOES NOT DOUBLE-COUNT: ``exhausted:`` is not a lost row. This helper
RAISES rather than swallowing (see its docstring), so whether the write is lost
is the caller's finding and the caller's counter. ``exhausted:`` says the retry
mechanism gave up; what that cost is recorded where the decision was made.

THE KEYS ARE AN OUTCOME WORD AND AN EXCEPTION CLASS NAME -- both code
identifiers, never the ``subject`` string and never an exception MESSAGE, which
can carry a path.
"""

JOURNAL_MODE_DEGRADATIONS = Counter()
"""Databases whose journal mode is not what ``SQLITE_JOURNAL_MODE`` asked for.

Keyed ``requested->actual``. WAL is a property of the FILE, not of the
connection, and it can fail to take -- a network filesystem cannot provide the
shared memory the wal-index needs, and a read-only directory cannot hold the
``-wal`` file. Both leave the pragma returning the OLD mode with nothing raised,
which is the silent-degradation shape this project exists to remove.
"""


ANALYZE_FAILURES = Counter()
"""Databases whose planner statistics could not be refreshed, by exception type.

WHAT IT COSTS WHEN IT IS NON-ZERO, and it is deliberately small: `sqlite_stat1`
is stale or absent, so SQLite plans the campaign queries from its built-in
guesses instead of from measured selectivity. Every query still returns the
right answer; some of them choose a worse index. That is why `analyze_database`
counts rather than raising -- see its docstring for the position argument.

IT IS ON THE RUN-END REPORT through oncotriage/degradation.py, which is the only
reader. There is no key for "it was never called": ANALYZE runs once per batch
run, at a point main() reaches on the success path only, and a run that died
before it has a KILLED `runs` row saying so.
"""


class InferenceWriteResult(str):
    """The database path this call wrote to, plus whether the row landed.

    A ``str`` SUBCLASS, and the choice is forced rather than clever. Before this
    pass ``log_inference`` returned ``db_path``, and that return value is a
    pinned contract in five places:

        tests/test_storage_ecog_logging.py:328
        tests/test_storage_inference_logging_contract.py:811, :910
        tests/test_agent_retrieval_observability.py:994, :1027, :1061
        tests/test_fhir_birth_date_and_demographics.py:896

    each of which compares it with ``==`` against its own scratch path. That
    comparison is what makes those five isolation tests checkable at all, so it
    may not break. A subclass of ``str`` compares, hashes, formats,
    ``os.path``-joins and JSON-serialises exactly as the path string did, while
    carrying the four facts a caller now needs.

    WHAT CONSTRAINS THE SHAPE, read rather than assumed. The two production
    callers -- ``oncotriage/batch/runner.py`` line 370 and
    ``oncotriage/api/server.py`` line 280 -- both DISCARD the return value
    today. So a return value alone reaches neither, which is why the counters
    above and ``runner``'s ledger exist as well; the batch runner is changed to
    read ``.ok``, and the API server deliberately is not (see the note there).

    Attributes:
        ok:           True only if the row and its children are committed.
        error:        ``"{Type}: {message}"`` when not, else None.
        attempts:     Attempts made, 1 when it worked first time.
        inference_id: The ``inferences.id`` assigned, or None if nothing landed.
                      This is what makes reconciliation exact rather than
                      statistical -- see ``runner.reconcile_writes``.
    """

    # __slots__ so an instance cannot silently grow an attribute that a reader
    # then trusts; str subclasses get no __dict__ from this alone, which is the
    # point.
    __slots__ = ("ok", "error", "attempts", "inference_id")

    def __new__(cls, db_path, ok=True, error=None, attempts=1,
                inference_id=None):
        obj = super().__new__(cls, db_path)
        obj.ok = bool(ok)
        obj.error = error
        obj.attempts = int(attempts)
        obj.inference_id = inference_id
        return obj

    def __repr__(self):
        # NOT str.__repr__. A caller who prints this in a diagnosis must see the
        # outcome, not just a path that looks like a success. Equality, which is
        # what the five pinned tests use, is untouched: it comes from str.
        return (f"InferenceWriteResult({str.__repr__(self)}, ok={self.ok}, "
                f"attempts={self.attempts}, inference_id={self.inference_id!r})")


# THE RETRYABLE CLASS IS NARROW, AND THAT IS A DECISION WITH A CONTROL BEHIND IT.
#
# "Retry the write" is only correct for failures that are transient. SQLite
# reports contention as an OperationalError whose message names a lock or a busy
# database; those clear on their own and a retry is exactly right.
#
# WHAT IS DELIBERATELY NOT RETRIED, and this is the important half:
#
#   "duplicate column name: X" is ALSO a sqlite3.OperationalError, and retrying
#   it would in fact succeed -- the second thread's ALTER already added the
#   column, so a second attempt finds the schema complete and the INSERT lands.
#   It is excluded anyway. That error is the signature of the migration race
#   ``_WRITE_LOCK`` exists to close, and
#   tests/test_package_invariants.py section 5e proves the lock necessary by
#   STRIPPING it and requiring rows to be lost. A retry broad enough to repair
#   that race would repair the negative control too, and the check whose whole
#   job is to show the lock is load-bearing would start passing for free.
#   Silently deleting the evidence for a lock is worse than not retrying an
#   error that the lock already prevents.
#
#   IntegrityError, ProgrammingError, DatabaseError-on-corruption and a full
#   disk are not transient at all. Retrying them spends SQLITE_WRITE_MAX_ATTEMPTS
#   x SQLITE_BUSY_TIMEOUT_SECONDS of a batch run to arrive at the same failure.
#
# Matched on the MESSAGE because sqlite3 does not expose a distinct exception
# type for contention; `sqlite3.OperationalError` covers both cases above. The
# strings are SQLite's own and stable ("database is locked", "database table is
# locked", "database is busy"), and the match is substring-and-lowercase so a
# wrapped or prefixed message still resolves.
_RETRYABLE_MESSAGE_MARKERS = ("database is locked", "database table is locked",
                              "database is busy", "database schema is locked")


def _is_retryable(exc):
    """True if exc is transient contention worth another attempt.

    Returns False for every other sqlite3.Error, including the migration race --
    see the block above for why that exclusion is deliberate.
    """
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return any(marker in message for marker in _RETRYABLE_MESSAGE_MARKERS)


def _open_connection(db_path, read_only=False):
    """``sqlite3.connect`` with this project's busy timeout applied.

    THE TIMEOUT IS PER CONNECTION, so it has to be set on every one of them --
    it is not a property of the file the way the journal mode is. Passed to
    ``connect()`` rather than issued as a PRAGMA afterwards because the
    connection attempt itself can meet a locked database, and a PRAGMA on the
    next line would be too late to help it.

    ``sqlite3.connect`` takes SECONDS as a float; ``PRAGMA busy_timeout`` takes
    MILLISECONDS as an integer. Mixing those up gives a 30-millisecond timeout
    that looks like a 30-second one, so the config constant is in seconds and
    the conversion happens in exactly one place, below.

    ``read_only=True`` OPENS THROUGH A ``mode=ro`` URI, and it exists so that
    ``probe_serving_database`` can ask a question about a database without
    being the reason it exists. A plain ``sqlite3.connect`` CREATES a missing
    file, so a probe that used one would answer "this database is fine" by
    bringing it into existence -- File 41's guard-that-creates-its-own-evidence
    defect, and on a container whose data volume failed to mount it would
    establish an empty database at the mount point.

    IT IS A PARAMETER HERE RATHER THAN A SECOND ``sqlite3.connect`` AT THE
    PROBE, and that is not tidiness: this module's invariant is that EVERY
    connection it opens carries the busy timeout, and
    ``tests/test_storage_write_durability.py`` section 3c enforces it by
    requiring every ``sqlite3.connect`` in this file to be inside this
    function. The first draft of the probe opened its own and 3c FAILED --
    which is the check working. A read-only connection can still meet a locked
    database, so it wants the timeout for the same reason every other one does.

    THE URI FORM ESCAPES THE PATH. A path containing ``?`` or ``#`` would
    otherwise be read as carrying URI query or fragment syntax, and this
    project's own project root contains characters (``C.V..V``) that make that
    worth doing properly rather than by f-string.
    """
    if read_only:
        return sqlite3.connect(
            "file:" + urllib.parse.quote(db_path) + "?mode=ro",
            uri=True, timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
    return sqlite3.connect(db_path, timeout=SQLITE_BUSY_TIMEOUT_SECONDS)


def _apply_journal_mode(conn, db_path):
    """Set ``SQLITE_JOURNAL_MODE`` on db_path and VERIFY it took.

    Returns the mode the database is actually in, lowercased.

    WHY VERIFY. ``PRAGMA journal_mode=WAL`` does not raise when it cannot be
    honoured; it returns the mode still in force. On a network filesystem (no
    shared memory for the wal-index) or in a read-only directory, that is the
    old mode, and a caller that assumed it took would go on believing readers
    and the writer no longer block each other. The pragma statement RETURNS the
    resulting mode, so the check costs nothing extra -- but it has to be read,
    and reading it is the entire mechanism.

    LOUD, ONCE PER DATABASE PER PROCESS. This runs inside initialize_database,
    which runs once per resolved path per process, so a mismatch is one WARNING
    and one counter increment rather than one per row.
    """
    requested = str(SQLITE_JOURNAL_MODE).strip().lower()

    row = conn.execute(f"PRAGMA journal_mode = {requested}").fetchone()
    actual = str(row[0]).lower() if row else "unknown"

    if actual != requested:
        JOURNAL_MODE_DEGRADATIONS[f"{requested}->{actual}"] += 1
        console.out(
            f"⚠ SQLite journal mode: asked for {requested.upper()}, the "
            f"database is in {actual.upper()}. Concurrent readers and the "
            f"writer will block each other, so a second writing process can "
            f"still lose rows under contention.\n"
            f"    Database: {db_path}\n"
            f"    Usual causes: the file is on a network filesystem (WAL needs "
            f"shared memory the mount cannot provide), or its directory is not "
            f"writable.\n"
            f"    Set SQLITE_JOURNAL_MODE in oncotriage/config.py to "
            f"'{actual}' to accept this deliberately and stop this warning.")
        log.warning("sqlite journal mode not applied",
                    event="journal_mode_degraded",
                    journal_mode_requested=requested, journal_mode=actual,
                    db_path=str(db_path))
    else:
        log.info("sqlite journal mode applied", event="journal_mode",
                 journal_mode=actual, db_path=str(db_path))

    return actual


# ---------------------------------------------------------------------------
# THE THREE PIECES OTHER WRITERS IN THIS PROJECT REUSE
# ---------------------------------------------------------------------------
#
# `oncotriage/ablation/study.py` writes its own database -- `ablation_results.db`
# -- with the same shape of concurrency this module was hardened for: a thread
# pool, a done-callback that inserts a row per completed patient, and a second
# process that the ablation run lock refuses but cannot prevent from EXISTING
# (an operator with two checkouts, a `--db` pointed at the same file). It had
# none of the hardening: a bare `sqlite3.connect` with sqlite3's 5-second
# default timeout, the rollback journal, and no retry.
#
# THE ALTERNATIVE WAS A SECOND POLICY AND THAT IS THE THING WORTH AVOIDING. Two
# retry loops with two backoffs and two definitions of "transient" is how the
# two halves of a rule drift apart while both look maintained -- the shape this
# project removed for the BM25 sparse model, the cross-encoder checkpoint and
# the latest-run-per-config SQL. So the policy has one owner: these three
# functions, `_is_retryable` below them, and the four SQLITE_* constants in
# oncotriage/config.py.
#
# WHAT IS DELIBERATELY NOT SHARED: `_write_inference_row_with_retry`. It is NOT
# reimplemented on `run_with_write_retry` even though the loop is the same
# shape, and the reason is that its contract is different in a way that matters
# -- it RETURNS an outcome dict rather than raising, so that a lost row cannot
# kill a pipeline that has already paid for the patient, and it logs
# `patient_id` on every attempt. Rewriting it onto a generic helper would be a
# refactor of the one write path whose behaviour under contention has been
# measured, inside a pass that changes nothing about it. The duplication is one
# loop and it is recorded here rather than left to be discovered.


def open_connection(db_path):
    """A connection carrying this project's busy timeout. See _open_connection.

    THE PUBLIC NAME, for writers outside this module. It delegates rather than
    calling ``sqlite3.connect`` itself because this module holds exactly one
    connect site by design -- ``tests/test_storage_write_durability.py`` section
    3c asserts by AST that every ``sqlite3.connect`` in this file is inside
    ``_open_connection``, so that no connection anywhere can be opened without
    the timeout.
    """
    return _open_connection(db_path)


def apply_journal_mode(conn, db_path):
    """Set and VERIFY the journal mode on db_path. See _apply_journal_mode.

    The public name, for a writer that owns a different database and wants the
    same guarantee. Counts into the same ``JOURNAL_MODE_DEGRADATIONS``, which is
    correct: the counter's meaning is "a database this process writes is not in
    the mode that was asked for", and which database is in the key.
    """
    return _apply_journal_mode(conn, db_path)


def run_with_write_retry(operation, subject):
    """Call ``operation()``, retrying only transient contention. Returns its value.

    RAISES what the last attempt raised. That is the difference between this and
    ``_write_inference_row_with_retry``, and it is the right contract HERE: every
    caller already sits inside its own ``except Exception`` that counts the
    failure into its own counter and decides what a lost row means for it, so a
    helper that swallowed the exception would take that decision away from the
    module that owns it.

    Args:
        operation: a zero-argument callable that does the whole write --
            connect, statements, commit, close. It is called again from scratch
            on a retry, which is why it must own its connection: a connection
            whose transaction was rolled back by a contention error is not a
            connection to reuse.
        subject: what is being written, for the console and log lines. FREE
            TEXT AND STILL NOT A COUNTER KEY, and that is unchanged by
            ``WRITE_RETRY_OUTCOMES`` below: the counter keys on the exception
            CLASS, which is a code identifier, because a caller is free to
            interpolate a path or an id into this string and a run-end report is
            not the place to discover that it did.

    ONLY THE TRANSIENT CLASS IS RETRIED, through ``_is_retryable``, which is the
    same classifier the inference write uses and excludes `duplicate column
    name` for the reason argued above it: a retry broad enough to repair the
    migration race would silently repair the negative control that proves the
    write lock necessary.

    EVERY OUTCOME IS COUNTED into ``WRITE_RETRY_OUTCOMES``; see that counter for
    the three keys and for why there is no fourth. Counting is the ONLY thing
    this function does that it did not do before -- the control line, the log
    record, the classifier, the delay schedule and what is re-raised are all
    unchanged, so no caller's behaviour moves.
    """
    max_attempts = max(1, int(SQLITE_WRITE_MAX_ATTEMPTS))
    attempts = 0
    last_retryable_type = None
    while True:
        attempts += 1
        try:
            value = operation()
        except Exception as exc:                               # noqa: BLE001
            if not _is_retryable(exc) or attempts >= max_attempts:
                # EXHAUSTION IS COUNTED AND A TERMINAL ERROR IS NOT. The two
                # arms of this `if` are different findings: the first means the
                # budget ran out while the error was still transient (more
                # attempts, a longer timeout or fewer writers would have helped)
                # and the second means retrying was never going to work. Only
                # the first is a retry outcome; the second is the caller's
                # failure to count, in its own counter, with its own meaning.
                if _is_retryable(exc):
                    WRITE_RETRY_OUTCOMES[f"exhausted:{type(exc).__name__}"] += 1
                raise
            delay = SQLITE_WRITE_RETRY_BASE_DELAY * (2 ** (attempts - 1))
            console.out(f"  ↻ Retrying {subject} in {delay:.2f}s "
                        f"(attempt {attempts + 1}/{max_attempts}): "
                        f"{type(exc).__name__}: {exc}")
            log.warning("a database write contended, retrying",
                        event="write_retry", subject=str(subject),
                        attempts=attempts, max_retries=max_attempts,
                        delay_s=round(delay, 3),
                        error_type=type(exc).__name__,
                        error_message=str(exc))
            WRITE_RETRY_OUTCOMES[f"retried:{type(exc).__name__}"] += 1
            last_retryable_type = type(exc).__name__
            time.sleep(delay)
        else:
            # RECOVERY IS COUNTED ONLY WHEN SOMETHING WAS SURVIVED. `attempts
            # == 1` is the ordinary uncontended write, and a `recovered:` key
            # for it would make the counter a call census rather than a record
            # of contention -- every write in the project would move it, and the
            # run-end report exists to name what did NOT go to plan.
            if attempts > 1 and last_retryable_type is not None:
                WRITE_RETRY_OUTCOMES[
                    f"recovered:{last_retryable_type}"] += 1
            return value


# ---------------------------------------------------------------------------
# THE WRITE LOCK (pass 20c-3b)
# ---------------------------------------------------------------------------
#
# WHERE IT USED TO LIVE, AND WHY THAT WAS WRONG.
#
# "25- Batch Runner.py" lines 65-73 did this, at module level, after chaining
# File 14:
#
#     _db_lock = threading.Lock()
#     _original_log_inference = log_inference
#     def _thread_safe_log_inference(*args, **kwargs):
#         with _db_lock:
#             return _original_log_inference(*args, **kwargs)
#     log_inference = _thread_safe_log_inference
#
# It worked -- for File 25. It is a MONKEYPATCH IN ONE CALLER, so every other
# concurrent caller of log_inference had no lock at all, and there is one:
#
#     "17- FastAPI Server.py" line 191 calls log_inference from
#     loop.run_in_executor(None, _run_matching_pipeline, ...), i.e. from the
#     default ThreadPoolExecutor, on as many threads as there are in-flight
#     requests. Two overlapping POST /match requests were writing to the same
#     SQLite file through two connections with no serialization whatever.
#
# WHAT THAT ACTUALLY RISKS, stated rather than gestured at. The write is not one
# statement: it is _ensure_database (DDL), an INSERT into inferences, a read of
# cursor.lastrowid, N INSERTs into trial_matches keyed on that id, and a commit.
# sqlite3's own locking makes each STATEMENT safe; it does not make that
# SEQUENCE atomic. Two unserialized writers on one file give you, in rising
# order of nastiness:
#
#   1. "database is locked" OperationalError under contention, which
#      log_inference CATCHES and reports as non-critical -- so the row is simply
#      lost and the run reports success. Silent data loss, which is the one
#      failure mode this project exists to remove.
#   2. a rolled-back inference INSERT whose trial_matches rows were already
#      committed by the other connection's commit, leaving trial_matches rows
#      pointing at an inference_id that is not there.
#
# So the lock moves HERE, beside the writes it protects, where every caller gets
# it and no caller has to know it exists. File 25's monkeypatch is deleted.
#
# THIS IS A DELIBERATE BEHAVIOUR CHANGE FOR FILE 17, and it is the point of the
# move: the API's concurrent writers are serialized now and were not before.
#
# ONE GLOBAL LOCK, NOT ONE PER PATH. A dict of per-path locks needs its own lock
# to populate safely, and it would buy nothing measurable: a process writes one
# database, the critical section is a handful of milliseconds of SQLite work,
# and it sits inside a per-patient pipeline whose measured median is ~68 seconds
# of Stage 5 alone. Twelve threads queueing microseconds behind each other at
# the end of a minute of work is not a bottleneck.
#
# AN RLock, NOT A Lock. log_inference takes it and then calls _ensure_database,
# which calls initialize_database, which takes it again. A plain Lock would
# deadlock the first time a batch run met an uninitialized database.
#
# WHAT IT DOES NOT COVER: get_model_cost(). That is called BEFORE the lock and
# before the try, for the reason written at log_inference -- it touches no
# database, and an unpriced model must reach the caller rather than be held up
# behind, or swallowed by, database machinery.
_WRITE_LOCK = threading.RLock()


def _ensure_index(cursor, index_name, table, columns):
    """``CREATE INDEX IF NOT EXISTS`` -- but only if the columns are there.

    WHY THE GUARD, and it is the same shape the ALTER loops in this function
    already have. ``CREATE INDEX ... ON t(c)`` on a column ``t`` does not have
    RAISES ``no such column``, and this function is on the WRITE PATH:
    ``_ensure_database`` calls it before the first inference row of a run. A
    raise there converts a database in an unexpected shape into a dead
    pipeline, which contradicts this function's own contract -- "adds only what
    is missing and destroys nothing".

    IT IS NOT REACHABLE BY A DATABASE THIS PROJECT WROTE. Every column these
    five indexes name is either in a base ``CREATE TABLE`` here or is added by
    the migration loop that runs above them. What it is reachable by is a
    hand-built one -- a test fabricating an older era, a stub, a file assembled
    by a support script -- and the honest answer for those is the one the ALTER
    loops give: do what can be done, and SAY what could not.

    THE SKIP IS ANNOUNCED. A silent one is indistinguishable from an index that
    was created, which is the difference between a fast query and a full scan
    nobody is told about.
    """
    present = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
    missing = [c for c in columns if c not in present]
    if missing:
        console.out(
            f"Schema: index {index_name} NOT created -- {table} has no "
            f"{', '.join(missing)}. Queries that would have used it will scan.")
        return False
    cursor.execute(f"CREATE INDEX IF NOT EXISTS {index_name} "
                   f"ON {table}({', '.join(columns)})")
    return True


def initialize_database(db_path):
    """Create the five tables at db_path and apply the additive migrations.

    THE COUNT IN THAT SENTENCE IS THE ONE THING IN THIS DOCSTRING THAT CAN ROT,
    and it had already: it read "three" through the run-identity pass, which
    added `runs`. It is five now -- runs, inferences, trial_matches,
    drift_metrics, run_metrics -- and
    ``tests/test_storage_run_metrics_flush.py`` pins the set by name so the
    next addition fails a check rather than leaving a stale number here.

    Idempotent: every CREATE is IF NOT EXISTS and every ALTER is guarded by a
    PRAGMA table_info check, so calling this on an existing database adds only
    what is missing and destroys nothing.

    Returns the resolved absolute path, so a caller can log where it wrote.

    HOLDS THE WRITE LOCK (pass 20c-3b). This runs DDL and mutates
    _INITIALIZED_DATABASES; two threads meeting an uninitialized database would
    otherwise both run the migration loop and both mutate the set. The body is a
    separate function purely so the SQL below keeps its exact indentation --
    those CREATE statements are flush at column 0 inside their triple-quoted
    strings on purpose, because SQLite stores the CREATE text verbatim in
    sqlite_master.sql and re-indenting them would change the recorded schema.
    """
    with _WRITE_LOCK:
        return _initialize_database_locked(db_path)


def _initialize_database_locked(db_path):
    """initialize_database's body. Callers hold _WRITE_LOCK."""
    # Connect
    # It will create it if deos not exist, and it won't override if it does.
    #
    # Through _open_connection so this connection carries the same busy timeout
    # every other one does. It matters MORE here than on the insert path: this
    # is where the ALTER TABLE migrations run, and DDL takes an exclusive lock.
    conn = _open_connection(db_path)

    # ── REFUSE BEFORE MUTATING ──────────────────────────────────────────────
    #
    # FIRST, ahead of the page size and ahead of the journal mode, because both
    # of those WRITE THE FILE HEADER and a refusal has to leave the database
    # exactly as it found it. The connect above creates an empty file if the
    # path does not exist, which is unchanged behaviour and is what makes a
    # fresh database possible at all; an empty file reports 0 and 0 and passes.
    #
    # It RAISES. This is the one thing in this function that is allowed to, and
    # it is deliberate: every caller reaches here before its first billed call
    # (start_run_record opens the run row; log_inference's _ensure_database runs
    # on the first write), the fault is a configuration fault an operator fixes
    # with one command, and continuing means writing rows nobody can read back.
    _found_application_id, _found_version = assert_database_is_compatible(
        conn, db_path)

    # ── THE PAGE SIZE, AND IT MUST BE ISSUED HERE ───────────────────────────
    #
    # ABOVE _apply_journal_mode, NOT BELOW IT. Measured on sqlite 3.45.3 rather
    # than read off the documentation: `PRAGMA page_size = 16384` issued AFTER
    # `journal_mode = WAL` on a fresh database is SILENTLY IGNORED -- no error,
    # no warning, the pragma reports success and the file keeps 4096. Issued
    # before, it takes. That ordering is the entire reason this block is not
    # three lines further down.
    #
    # ON AN EXISTING DATABASE IT IS INERT, also measured: a file that already
    # has pages keeps its page size and reports no error. That is the designed
    # outcome and NOT a degradation, which is why the mismatch below is
    # reported and NOT counted -- an existing file keeping its page size is the
    # normal, permanent state of every database this project has ever written,
    # and a counter that is non-zero on every run of every campaign says
    # nothing. Changing it needs a VACUUM (a full rewrite) or a fresh file; see
    # the campaign-start procedure at ONCOTRIAGE_APPLICATION_ID.
    #
    # THE VALUE IS INTERPOLATED for the reason the user_version stamp is: a
    # pragma takes no bound parameter. int() is what keeps that safe.
    conn.execute(f"PRAGMA page_size = {int(SQLITE_PAGE_SIZE)}")

    # THE JOURNAL MODE IS SET HERE AND NOWHERE ELSE, because it is a property of
    # the FILE: one successful application converts the database permanently and
    # every later connection inherits it. Doing it on the insert path instead
    # would issue a pragma per row to change nothing. It is applied BEFORE the
    # CREATE statements so the schema work itself runs under the mode the
    # database will keep.
    _apply_journal_mode(conn, db_path)

    # READ BACK, because a pragma that is ignored reports success. Reported at
    # INFO with both numbers so a reader of the log can tell a fresh file (which
    # got what was asked for) from a carried-forward one (which did not, and
    # cannot).
    _page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    if _page_size != int(SQLITE_PAGE_SIZE):
        log.info("sqlite page size left as the database had it",
                 event="page_size_existing",
                 page_size=_page_size,
                 page_size_requested=int(SQLITE_PAGE_SIZE),
                 db_path=str(db_path))
    else:
        log.info("sqlite page size applied", event="page_size",
                 page_size=_page_size, db_path=str(db_path))

    # ── STAMP THE IDENTITY ──────────────────────────────────────────────────
    #
    # Only when it is UNSTAMPED, so an already-stamped file's header is not
    # rewritten on every open -- the checked value can only be ours or zero by
    # this line, because assert_database_is_compatible refused everything else.
    #
    # IT IS STAMPED HERE AND THE ERA IS STAMPED AT THE BOTTOM, and the asymmetry
    # is deliberate. The era says "this file HAS these columns" and so must be
    # written only after they exist; the identity says "this file is ours" and is
    # true the moment this code takes responsibility for it -- which is now,
    # because the CREATE statements below are what make it ours.
    if _found_application_id == APPLICATION_ID_UNSTAMPED:
        conn.execute(
            f"PRAGMA application_id = {int(ONCOTRIAGE_APPLICATION_ID)}")
        log.info("sqlite application id stamped", event="application_id",
                 db_path=str(db_path))

    # Create cursor
    cursor = conn.cursor()

    # Runs table (the run-identity pass)
    #
    # CREATED FIRST, BEFORE `inferences`, because `inferences.run_id` points at
    # it. Nothing enforces that ordering today -- see the foreign-key decision
    # below -- but a schema whose creation order contradicts its own references
    # is a schema that cannot have the constraint turned on later without being
    # reordered first.
    #
    # ------------------------------------------------------------------------
    # THE FOREIGN-KEY DECISION, AND IT IS A DECISION RATHER THAN AN OVERSIGHT.
    # ------------------------------------------------------------------------
    # `run_id` IS AN UNENFORCED REFERENCE, exactly like `trial_matches`'
    # FOREIGN KEY on `inferences(id)` directly below, and `PRAGMA foreign_keys`
    # is NOT turned on. Four reasons, in the order they decided it:
    #
    #   1. ENFORCEMENT IS PER CONNECTION AND THIS MODULE OPENS ONLY SOME OF
    #      THEM. SQLite defaults `foreign_keys` OFF and the pragma has to be
    #      issued on every connection. `_open_connection` is this module's one
    #      connection site -- but `oncotriage/storage/queries.py`,
    #      `oncotriage/storage/maintenance.py`, `oncotriage/monitoring/drift.py`,
    #      `oncotriage/dashboard/data.py`, `oncotriage/evaluation/sampling.py`
    #      and every test open their own. A constraint honoured by one writer
    #      and ignored by six other openers of the same file is not an
    #      invariant; it is a property of which module happened to open it,
    #      which is worse than no constraint because it reads like one.
    #
    #   2. IT WOULD BREAK `empty_database` (oncotriage/storage/maintenance.py),
    #      which issues `DELETE FROM` over every table `sqlite_master` reports,
    #      in that catalogue's order -- i.e. creation order, parents first. With
    #      enforcement on, deleting `runs` while `inferences` rows still point
    #      at it raises a constraint violation and the wipe fails, having
    #      deleted nothing (the raise lands before the commit). That is a real
    #      regression in a shipped tool, traded for a constraint nothing needs.
    #
    #   3. IT WOULD CONVERT A RECOVERABLE STATE INTO A LOST ROW. A violation
    #      arrives as `sqlite3.IntegrityError`, which `_is_retryable` classes as
    #      TERMINAL -- so a row that today lands with a dangling id would
    #      instead be given up on, counted in INFERENCE_WRITE_FAILURES, and
    #      gone. This module's whole write-durability design is about not losing
    #      rows to database bookkeeping.
    #
    #   4. THE VALUE IS SMALL. NULL is legitimate and expected here (the API
    #      server, every direct call), and the only non-NULL values are written
    #      by the same process that created the run row moments earlier, into
    #      the same file, under the same lock. There is no path that produces a
    #      dangling id by accident -- the one that produces it ON PURPOSE is
    #      `oncotriage/evaluation/sampling.py`, which copies a SUBSET of
    #      `inferences` into a second database, and which this pass teaches to
    #      copy the referenced `runs` rows with them.
    #
    # TURNING IT ON IS A ONE-PLACE DECISION THAT MUST BE TAKEN IN SEVEN: every
    # connection site above, together, plus a wipe that deletes children before
    # parents. Recorded here so that whoever wants it has the list.
    cursor.execute('''
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    invocation_source TEXT NOT NULL,
    fingerprint_version INTEGER,
    llm_classifier_prompt_version TEXT,
    llm_classifier_renderer_digest TEXT,
    matching_model_configured TEXT,
    qdrant_collection TEXT,
    collection_points INTEGER,
    data_snapshot_date TEXT
)
''')

    # THE DICT AND THE LOOP EXIST NOW, on the instruction the comment they
    # replace gave: "a column added to this table later gets the dict and the
    # loop, copied from the two below, in the same commit that adds the column
    # -- at which point the loop has something to do." `resumed` is that column
    # and this is that commit. The shape below is copied from the two migrations
    # further down, not re-derived.
    _existing_run_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(runs)")
    }
    for _column, _sql_type in RUN_COLUMN_ADDITIONS.items():
        if _column not in _existing_run_columns:
            cursor.execute(f"ALTER TABLE runs ADD COLUMN {_column} {_sql_type}")
            console.out(f"Schema migration: added runs.{_column}")

    # Inferences table
    # candidates_filtered INTEGER is for trials sent to GPT-4o (after quality threshold + cost cap)
    cursor.execute('''
CREATE TABLE IF NOT EXISTS inferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    age INTEGER,
    sex TEXT,
    race TEXT, 
    ethnicity TEXT,
    primary_condition TEXT,
    condition_count INTEGER,
    medication_count INTEGER,
    allergy_count INTEGER,
    expanded_query TEXT,
    candidates_retrieved INTEGER,
    candidates_reranked INTEGER,
    bm25_retrieved INTEGER,
    vector_retrieved INTEGER,
    candidates_after_rule_filter INTEGER,
    candidates_after_quality_filter INTEGER,
    candidates_filtered INTEGER,
    mesh_dropped INTEGER,
    mesh_resolution TEXT,
    stage_dropped INTEGER,
    histology_dropped INTEGER,
    candidates_evaluated INTEGER,
    eligible_matches INTEGER,
    near_misses INTEGER,
    not_evaluable_trials INTEGER,
    cross_vocab_remaps INTEGER,
    query_expansion_time REAL,
    hybrid_retrieval_time REAL,
    cross_encoder_time REAL,
    rule_filter_time REAL,
    llm_classifier_evaluation_time REAL,
    total_time REAL,
    llm_classifier_prompt TEXT,
    llm_classifier_input_tokens INTEGER,
    llm_classifier_output_tokens INTEGER,
    matching_model TEXT,
    cross_encoder_model TEXT,
    pricing_version TEXT,
    estimated_cost_usd REAL,
    qdrant_collection TEXT,
    error TEXT,
    patient_data_hash TEXT,
    expansion_prompt TEXT,
    llm_classifier_retries INTEGER,
    ablation_flags TEXT,
    hallucinated_trials INTEGER,
    ecog_value INTEGER,
    ecog_selection TEXT,
    ecog_observations_found INTEGER
)
''')


    _existing_inference_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(inferences)")
    }
    for _column, _sql_type in INFERENCE_COLUMN_ADDITIONS.items():
        if _column not in _existing_inference_columns:
            cursor.execute(f"ALTER TABLE inferences ADD COLUMN {_column} {_sql_type}")
            console.out(f"Schema migration: added inferences.{_column}")

    # ── THE THREE ACCESS PATHS `inferences` IS ACTUALLY READ BY ─────────────
    #
    # MEASURED, not assumed, on a fabricated database at the scale the campaign
    # will reach -- 22,000 inference rows and 330,000 trial_matches children,
    # sqlite 3.45.3, mean over 200/20/100 repetitions. Milliseconds per query,
    # before and after all three:
    #
    #     lookup by patient_id      1.615  ->  0.012      (134x)
    #     a one-month timestamp range 1.649 -> 1.061      (1.6x)
    #     count by run_id           2.028  ->  0.015      (135x)
    #     group by run_id + join    3.556  ->  0.778      (4.6x)
    #     one patient's write       0.089  ->  0.086      (no measurable cost)
    #
    # THE WRITE COST IS THE HALF THAT DECIDES IT, and it is why the trial_matches
    # nct_id index is NOT here while these three are: every insert maintains
    # every index on the table, so an index that no read plan chooses is a pure
    # tax. At one patient per ~68 seconds of pipeline, three more B-tree
    # insertions per row is below the noise floor of the measurement above.
    #
    # WHY EACH ONE, by its reader rather than by its shape:
    #
    #   patient_id -- the dashboard's per-patient drill-down, the resample
    #     pass's lookup, `pipeline_consistency`'s ordering and every "what
    #     happened to this patient" question an operator asks. It is the only
    #     column in this table a human types.
    #   timestamp -- `oncotriage/monitoring/drift.py` selects a baseline window
    #     and a comparison window by timestamp on every drift run, and the
    #     dashboard's sidebar filters by date. The gain is the smallest of the
    #     three (1.6x) because a one-month range over an evenly spread year
    #     still touches a twelfth of the table; it is kept because the drift
    #     windows are narrower than that in production and because the write
    #     cost is zero either way.
    #   run_id -- every campaign query joins on it (`run_summary`,
    #     `run_attribution_coverage`, `dangling_run_references`,
    #     `campaign_summary`, `call_mode_comparison`,
    #     `stage5_cache_effectiveness`), and it is the column the run tables
    #     were added to make askable.
    #
    # ALL THREE ARE `IF NOT EXISTS` and sit AFTER the column migrations, so a
    # database arriving without `run_id` gets the column and then its index in
    # one open. The order matters for exactly that one: CREATE INDEX on a
    # column that does not exist yet is an error, not a no-op.
    _ensure_index(cursor, "idx_inferences_patient_id", "inferences",
                  ("patient_id",))
    _ensure_index(cursor, "idx_inferences_timestamp", "inferences",
                  ("timestamp",))
    _ensure_index(cursor, "idx_inferences_run_id", "inferences", ("run_id",))


    # Trial matches table
    cursor.execute('''
CREATE TABLE IF NOT EXISTS trial_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inference_id INTEGER NOT NULL,
    nct_id TEXT NOT NULL,
    trial_title TEXT,
    trial_phase TEXT,
    trial_number INTEGER,
    rerank_score REAL,
    rerank_score_raw REAL,
    mesh_boost REAL,
    mesh_boost_tier TEXT,
    match_score REAL,
    eligible TEXT,
    assessment TEXT,
    criterion_details TEXT,
    hallucinated INTEGER,
    FOREIGN KEY (inference_id) REFERENCES inferences(id)
)
''')


    _existing_trial_match_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(trial_matches)")
    }
    for _column, _sql_type in TRIAL_MATCH_COLUMN_ADDITIONS.items():
        if _column not in _existing_trial_match_columns:
            cursor.execute(f"ALTER TABLE trial_matches ADD COLUMN {_column} {_sql_type}")
            console.out(f"Schema migration: added trial_matches.{_column}")

    # THE CHILD LOOKUP IS THE ONLY ACCESS PATH THIS TABLE HAS, and until this
    # line it had no index at all -- so every one of them was a full scan of
    # every trial row the database has ever held, across every run.
    #
    # MEASURED, at 22,000-patient scale (~330,000 child rows): fetching one
    # inference's trials took 169 ms without the index and 0.02 ms with it.
    # `trial_matches` is written 15-ish rows per patient and read by
    # `run_normalizer_provenance`, the four provenance queries, the dashboard's
    # per-patient drill-down and every JOIN in this registry, all of them on
    # `inference_id`.
    #
    # AND THE INDEX THAT IS NOT HERE IS A RULING, NOT AN OVERSIGHT. An index on
    # `nct_id` was measured HARMFUL -- 32% slower -- because the queries that
    # group by it read the whole table anyway, so the planner gains nothing and
    # every insert pays to maintain a second B-tree. Do not add one. If a future
    # access path seems to want it, re-measure first and record the number.
    #
    # IT IS `IF NOT EXISTS` for the same idempotence reason every CREATE in this
    # function is, and it is placed AFTER the column migrations so a database
    # arriving with neither gets its columns and its index in one open.
    _ensure_index(cursor, "idx_trial_matches_inference_id", "trial_matches",
                  ("inference_id",))


    # Drift metrics table
    cursor.execute('''
CREATE TABLE IF NOT EXISTS drift_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    metric_category TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL,
    baseline_mean REAL,
    baseline_std REAL,
    p_value REAL,
    z_score REAL,
    threshold REAL,
    alert INTEGER,
    baseline_window_days INTEGER,
    comparison_window_days INTEGER,
    notes TEXT
)
''')


    # Run metrics table (the health-persistence pass)
    #
    # CREATED LAST AND THAT IS DELIBERATE, on the same rule the `runs` comment
    # states: a table's creation must not precede the table it references. It
    # only has to come after `runs`, and appending it leaves the four existing
    # tables' positions in `sqlite_master` exactly where they were -- which is
    # the order `empty_database` deletes in.
    #
    # `run_id` IS DECLARED AND UNENFORCED, for the four reasons written out at
    # `runs`. Note the fourth applies here more strongly rather than less: every
    # row this table holds is written by `flush_run_metrics`, which is handed the
    # id the same process obtained from `start_run_record` minutes earlier, into
    # the same file, under the same lock.
    #
    # THE `REFERENCES` CLAUSE REACHES ONLY A DATABASE THIS CREATE ACTUALLY RUNS
    # ON -- i.e. one that does not have the table yet. `CREATE TABLE IF NOT
    # EXISTS` is a no-op against an existing `run_metrics`, and SQLite cannot add
    # a constraint to an existing column, so a database built between the
    # health-persistence pass and this one keeps the undeclared form forever. The
    # two behave identically at run time (nothing enforces either) and differ
    # only in what `sqlite_master` tells a reader. `orphan_run_metrics` in
    # oncotriage/storage/queries.py is the audit that finds the violations
    # either way.
    #
    # `value` IS INTEGER, NOT REAL. `drift_metrics.metric_value` is REAL because
    # a KS statistic is; every value here is a `Counter` total or a count of
    # counters, and both are whole. Declaring REAL would render every total as
    # `412.0` to a reader and invite an average over a column of event counts.
    #
    # NO UNIQUE CONSTRAINT, AND THE FLUSH IS DELETE-AND-INSERT RATHER THAN AN
    # UPSERT. Three reasons, in the order they decided it:
    #
    #   1. THE TWO ARE NOT EQUIVALENT AND DELETE-AND-INSERT IS THE CORRECT ONE.
    #      An upsert keyed on (run_id, name) replaces the counters the new flush
    #      CARRIES and leaves behind any row whose counter is no longer in the
    #      set. That never happens during a run -- the counters are cumulative
    #      and `totals()` drops zeros, so the name set only grows -- but it
    #      happens the moment anything clears a counter (`degradation.clear_all`
    #      exists and a harness uses it), and the residue would be a stale
    #      non-zero total presented as current. Replacing the run's whole
    #      picture cannot leave residue by construction.
    #   2. IT IS ONE TRANSACTION, so a concurrent reader on another connection
    #      sees the previous flush or this one, never a half-replaced mixture of
    #      the two. That is what makes "a dashboard can read health live" a safe
    #      thing to offer.
    #   3. THERE IS NO UNIQUE CONSTRAINT ANYWHERE IN THIS SCHEMA and adding the
    #      first one to enable `ON CONFLICT` would make an IntegrityError
    #      reachable on the write path -- which `_is_retryable` classes as
    #      TERMINAL, so a row that today lands would instead be given up on.
    #
    # THE INDEX IS NOT DECORATION. That DELETE runs once per completed patient,
    # against a table that accumulates across every run the database has ever
    # held; without it each flush of a 22,000-patient run scans the whole
    # history. It is IF NOT EXISTS for the same idempotence reason every CREATE
    # here is.
    cursor.execute('''
CREATE TABLE IF NOT EXISTS run_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    value INTEGER,
    written_at TEXT NOT NULL
)
''')

    _ensure_index(cursor, "idx_run_metrics_run_id", "run_metrics",
                  ("run_id",))


    # ------------------------------------------------------------------
    # STAMP THE SCHEMA ERA -- LAST, AND THE POSITION IS THE POINT
    # ------------------------------------------------------------------
    # Every CREATE and every ALTER above has run by this line, so the stamp is
    # written only over a file that HAS the era it claims. Stamping first would
    # label a database that a raise in the middle then left half-migrated --
    # and a wrong era is worse than no era, because a reader acts on it while
    # `0` sends them to `PRAGMA table_info`.
    #
    # It is inside the same transaction as the migrations, so a failure takes
    # the stamp with the columns rather than leaving one without the other.
    #
    # THE VALUE IS INTERPOLATED, NOT BOUND. `PRAGMA user_version = ?` is a
    # syntax error in SQLite -- pragmas take no parameters -- so the f-string is
    # forced. It is safe because SCHEMA_USER_VERSION is a module constant this
    # file owns; the int() is what keeps that true if someone ever makes it a
    # string.
    #
    # IT NEVER LOWERS AN EXISTING STAMP, and that is a correctness rule rather
    # than caution. This schema is strictly additive -- nothing here drops a
    # column, a table or an index -- so a file stamped 7 that this era-1 code
    # then opens still HAS everything era 7 gave it, plus whatever era 1 just
    # ensured. Writing 1 over the 7 would erase a true statement and replace it
    # with a false one. The reverse case (a newer writer meeting an older file)
    # needs no rule: it migrates the file forward and then stamps forward.
    #
    # The refusal is ANNOUNCED on the same channel every other thing this
    # function says goes to. A silent no-op here would be indistinguishable
    # from a stamp that worked.
    # RE-READ RATHER THAN REUSING THE VALUE THE REFUSAL ABOVE ALREADY HAS, and
    # the difference is one window wide: this lock is per PROCESS, so a second
    # process running newer code can have migrated and re-stamped the file
    # between that read and this one. Reusing the earlier reading would then
    # LOWER a stamp that had legitimately moved up. This branch is what remains
    # of the old permissive one -- it is unreachable through the ordinary path,
    # because assert_database_is_compatible refuses a higher era before any of
    # the work above runs, and it is kept because that concurrent window is real
    # and the DDL above has already been applied by the time it is reached.
    _stamp_now = cursor.execute("PRAGMA user_version").fetchone()[0]
    if _stamp_now > SCHEMA_USER_VERSION:
        console.out(
            f"Schema stamp: LEFT AT {_stamp_now}, not lowered to "
            f"{SCHEMA_USER_VERSION}. It read {_found_version} when this open "
            f"began, so another process migrated this database while these "
            f"tables were being ensured. The additive work above is done and "
            f"nothing is wrong with the rows that are there -- but this process "
            f"should not go on writing to it; run the newer code."
        )
    else:
        cursor.execute(f"PRAGMA user_version = {int(SCHEMA_USER_VERSION)}")
        if _stamp_now != SCHEMA_USER_VERSION:
            console.out(
                f"Schema stamp: user_version {_stamp_now} -> "
                f"{SCHEMA_USER_VERSION}"
            )

    conn.commit()
    conn.close()
    console.out(f"Database initialized at: {db_path}")

    _INITIALIZED_DATABASES.add(os.path.abspath(db_path))
    return os.path.abspath(db_path)


def _ensure_database(db_path):
    """Initialize db_path unless this process already did.

    Called by log_inference before its first write. Kept separate from
    initialize_database so an explicit caller always gets the real work done
    (a caller who deleted the file and wants it rebuilt calls that one), while
    the hot path pays the cost once.
    """
    with _WRITE_LOCK:
        resolved = os.path.abspath(db_path)
        if resolved in _INITIALIZED_DATABASES:
            return resolved
        return initialize_database(db_path)


#------------------------------------------------------------------------------


# ===========================================================================
# THE RUN ROW: CREATE IT BEFORE THE FIRST PATIENT, FINALIZE IT AFTER THE LAST
# ===========================================================================


def _run_fingerprint_value(column, fingerprint):
    """One stamp field, coerced for the column it is going into.

    Returns the value to bind. ``None`` for anything the column cannot honestly
    hold -- see ``RUN_FINGERPRINT_INTEGER_COLUMNS`` for why an unresolved count
    may not be stored as the string ``"unknown"`` in a numeric column.

    A ``fingerprint`` of ``None`` -- a caller that had no stamp to give --
    leaves every one of these columns NULL, which is exactly the "no stamp was
    recorded" state ``fingerprint_version IS NULL`` selects.
    """
    if not isinstance(fingerprint, dict):
        return None
    raw = fingerprint.get(column)
    if raw is None:
        return None
    if column in RUN_FINGERPRINT_INTEGER_COLUMNS:
        # `bool` first: isinstance(True, int) is True, and a `collection_points`
        # of 1 that was really a True is a number nobody measured.
        if isinstance(raw, bool) or not isinstance(raw, int):
            return None
        return raw
    return str(raw)


def start_run_record(invocation_source, db_path=None, fingerprint=None,
                     resumed=None):
    """Open a run row at db_path and return its ``runs.id``.

    Args:
        invocation_source: which entry point is running. REQUIRED, with no
            default, on ``empty_database(db_path, flag)``'s precedent: a default
            of "unknown" would be a row that names no caller, and this column
            exists to name one. A non-string or an empty one raises.

            THE VOCABULARY IS OPEN, and that is the one place this module
            declines the closed-set convention it follows for
            ``RUN_RECORD_STATUSES``. A status is a fact this module produces and
            can therefore enumerate; an invocation source is a fact about the
            caller, and the set of callers grows outside this file. What is
            enforced instead is that one was GIVEN.
        db_path: where to write. ``None`` means the configured production
            database -- ``resolve_inference_db_path``'s three tiers, the same
            ones ``log_inference`` uses, so a caller that lets both resolve gets
            one file by construction rather than by coincidence.
        fingerprint: this run's configuration stamp, normally
            ``oncotriage.run_fingerprint.current()``. ``None`` writes NULL to
            every stamp column.

            TAKEN AS AN ARGUMENT AND NOT RESOLVED HERE. This module may not
            import ``run_fingerprint`` (see ``RUN_FINGERPRINT_COLUMNS`` for the
            layering), and it should not want to: the caller has already
            resolved the stamp once, on its main thread, and that ONE reading is
            what gates its resume and stamps its checkpoint. A second resolution
            here would be a second Qdrant round trip that can disagree with the
            first across an alias swap -- the defect
            ``oncotriage/tracking.py:configuration_params`` records finding by
            running.

    Returns:
        The integer ``runs.id``. Never ``None``.

    RAISES, AND THAT IS THE DESIGN. Every other write in this module refuses to
    kill the pipeline, because those writes happen AFTER a live Stage 5 call has
    been paid for. This one happens before the first patient, where a failure
    costs nothing, and where continuing would produce a whole campaign of rows
    that cannot be attributed to anything -- the exact condition the run row
    exists to remove. ``oncotriage/tracking.py:start_run`` raises for the same
    reason, at the same point in the same ``main()``, and is the precedent.

    ``started_at`` IS ``datetime.now().isoformat()``, NAIVE AND LOCAL, matching
    ``inferences.timestamp`` exactly. Both are read off the same clock and a
    reader will compare them ("which patients ran inside this run's window");
    making one UTC and the other local would put a silent offset between two
    columns that are meant to be compared. If this project ever moves to UTC it
    moves both, together.
    """
    if not isinstance(invocation_source, str) or not invocation_source.strip():
        raise ValueError(
            f"start_run_record: invocation_source must be a non-empty string "
            f"naming the entry point that is running; got "
            f"{invocation_source!r}. It has no default on purpose -- see this "
            f"function's docstring.")

    db_path = resolve_inference_db_path(db_path)

    values = {
        "started_at":        datetime.now().isoformat(),
        # NULL until finalize_run_record fills it. A row whose finished_at is
        # still NULL is the honest record of a process that did not get to
        # finalize -- see finalize_run_record for the shape and the query.
        "finished_at":       None,
        "status":            RUN_RECORD_STATUS_RUNNING,
        "invocation_source": invocation_source.strip(),
    }
    for column in RUN_FINGERPRINT_COLUMNS:
        values[column] = _run_fingerprint_value(column, fingerprint)

    # WAS THIS A RESUME. Three values, and the coercion is what keeps the
    # column readable:
    #
    #   None -> NULL, meaning NOT RECORDED. Two callers reach it -- a row
    #           written before this column existed, and a caller with no
    #           checkpoint concept to report. Both are "nobody measured this",
    #           which is a different fact from a measured 0, exactly as
    #           `hallucinated`'s NULL is different from its 0.
    #   else -> `int(bool(...))`, so the column holds 0 or 1 and NOTHING ELSE.
    #           Not the caller's object, and not `True`/`False` left to
    #           sqlite3's own adaptation: `collection_points` next door records
    #           what a non-integer in an INTEGER-affinity column costs -- SQLite
    #           keeps a TEXT value as TEXT whatever the declared affinity and
    #           orders every TEXT above every INTEGER, so `WHERE resumed = 1`
    #           would silently miss it.
    values["resumed"] = None if resumed is None else int(bool(resumed))

    # NULL AT OPEN, AND ONLY finalize_run_record EVER FILLS IT. The value is a
    # statement about how the run ENDED, which nothing here can know. It is set
    # explicitly rather than left out because RUN_COLUMNS is derived from
    # RUN_COLUMN_ADDITIONS and the guard immediately below requires every
    # declared column to have a value -- see its own note for why that guard
    # exists at all.
    values["note"] = None

    # NULL AT OPEN FOR ``note``'s REASON, and one more of its own. Which
    # mechanism stopped a run is a statement about how it ENDED, so only
    # ``finalize_run_record`` can fill it -- and NULL here is also what makes
    # ``stop_reason IS NULL`` mean "this run was not stopped" on every row of
    # every era rather than only on the ones written before era 7.
    values["stop_reason"] = None

    # EVERY DECLARED COLUMN HAS A VALUE, CHECKED BEFORE THE INSERT.
    #
    # `RUN_COLUMNS` is derived from `RUN_COLUMN_ADDITIONS`, so adding an entry
    # to that dict adds a column to this INSERT -- and if nothing here sets it,
    # the bind below raises a bare `KeyError: 'the_name'` from inside a
    # generator expression, thirty frames from the dict that caused it. The
    # failure is correct (a column added to the schema and never written is the
    # shape this project treats as a defect) and the diagnosis is not.
    #
    # It is a RuntimeError rather than an assert: `python -O` deletes asserts,
    # and this is a schema-consistency guard rather than a debugging aid.
    _unset = [c for c in RUN_COLUMNS if c not in values]
    if _unset:
        raise RuntimeError(
            f"start_run_record has no value for {', '.join(_unset)}, which "
            f"RUN_COLUMNS declares. A column added to RUN_COLUMN_ADDITIONS is "
            f"written by this INSERT and must be given a value here, in the "
            f"same commit -- see RUN_COLUMNS."
        )

    columns = ", ".join(RUN_COLUMNS)
    placeholders = ", ".join("?" * len(RUN_COLUMNS))

    # UNDER THE WRITE LOCK, like every other statement this module issues.
    # The two shipped call sites are main-thread-only -- this one runs before
    # the pool is created and finalize_run_record after it has been joined --
    # so the lock is uncontended in practice. It is taken anyway because the
    # module's invariant is "every database statement in this file is issued
    # under _WRITE_LOCK", and an invariant with a documented exception is a
    # convention. _ensure_database takes it again; that is why it is an RLock.
    with _WRITE_LOCK:
        _ensure_database(db_path)
        conn = _open_connection(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"INSERT INTO runs ({columns}) VALUES ({placeholders})",
                tuple(values[c] for c in RUN_COLUMNS))
            run_id = cursor.lastrowid
            conn.commit()
        finally:
            conn.close()

    console.out(f"[Run] Opened run {run_id} ({invocation_source}) in {db_path}")
    log.info("run record opened", event="run_record_opened",
             inference_run_id=run_id, mode=invocation_source.strip(),
             status=RUN_RECORD_STATUS_RUNNING, db_path=str(db_path))
    return run_id


def _coerce_run_note(note, run_id):
    """The text to store in ``runs.note``, or ``None`` to leave the column alone.

    NEVER RAISES and never coerces. A note that is not a string is REFUSED --
    counted under ``finalize:bad_note:{type}`` and dropped -- rather than passed
    through ``str()``: an exception object, a dict or a ``None`` rendered as
    text all produce a plausible sentence in a column whose only reader is a
    human who will believe it. Refusing loses a note that was never usable;
    coercing invents one.

    ``bool`` IS NOT A STRING and needs no special case here, unlike every
    integer column in this module -- ``isinstance(True, str)`` is False, so the
    ordinary type test already rejects it.

    An empty or whitespace-only note is ``None``: a column holding ``""`` says
    nothing that NULL does not, and distinguishing them would give a reader a
    third state to interpret for no gain. The note is stripped for the same
    reason ``control.read_stop_message`` strips its own -- a file written with
    `echo` carries a trailing newline that is not content.

    THE CAP NAMES ITSELF IN THE STORED TEXT. A note cut at
    ``RUN_NOTE_MAX_CHARS`` with no marker is a note whose ending the reader
    invents; the marker is the same shape ``control.read_stop_message`` uses
    and it is deliberately INSIDE the cap-plus-marker string rather than
    replacing content beyond it, so the stored value is always a prefix of what
    the caller meant plus a statement that it is one.
    """
    if note is None:
        return None
    if not isinstance(note, str):
        RUN_RECORD_FAILURES[f"finalize:bad_note:{type(note).__name__}"] += 1
        log.warning("a run note that was not a string was refused rather than "
                    "coerced; runs.note is left as it was",
                    event="run_record_note_refused",
                    inference_run_id=run_id,
                    error_type=type(note).__name__)
        return None
    text = note.strip()
    if not text:
        return None
    if len(text) > RUN_NOTE_MAX_CHARS:
        return (text[:RUN_NOTE_MAX_CHARS]
                + f"... [truncated at {RUN_NOTE_MAX_CHARS} characters]")
    return text


class CampaignSpend(NamedTuple):
    """What the runs this one is RESUMING already spent. See
    ``campaign_spend_before``."""

    usd: float = 0.0
    rows: int = 0
    unpriced: int = 0
    run_ids: tuple = ()

    @property
    def runs(self) -> int:
        return len(self.run_ids)


def campaign_spend_before(run_id, db_path=None) -> CampaignSpend:
    """The billed spend of the runs this run is resuming. NEVER RAISES.

    WHY A RESUME MUST ASK. ``config.SPEND_CAP_USD`` is a CAMPAIGN budget. A cap
    that reset with each invocation is not a brake at all: a run that tripped
    the cap and was restarted -- by a systemd ``Restart=``, a cron entry, an
    operator who thought it had hung -- would get a fresh $300 every time, and
    the run lock (which forbids CONCURRENT runs) does nothing about SEQUENTIAL
    ones. This is what closes that.

    THE ROWS ARE THE SOURCE OF TRUTH, and there is no second one. The checkpoint
    could have carried a running total, and that was rejected: it is a control
    file an operator may delete (``--fresh`` does), it is written by the very
    process whose spend it would be claiming, and it would be a SECOND ledger to
    keep in step with the one the database already holds. ``inferences`` rows are
    what the money actually bought.

    WHICH RUNS COUNT -- ``campaign_summary``'s STITCH, WALKED FORWARD.
    A run with ``resumed = 1`` continues the campaign of the nearest PRECEDING
    run whose status is in ``CAMPAIGN_RESUMABLE_STATUSES`` and whose fingerprint
    columns are all identical; chains stitch transitively. That rule is
    ``oncotriage/storage/queries.py``'s and it is not re-decided here -- what is
    different is only the direction: that query stitches every campaign in the
    table at once with a recursive CTE, and this walks ONE chain backwards from
    a known run in a few round trips.

    THE TWO ARE PINNED AGAINST EACH OTHER by ``tests/test_spend_gate.py``, on
    ``RUN_RECORD_TERMINAL_STATUSES``' precedent: a restated rule is a rule that
    can drift, so it is checked rather than promised. A test may import both.

    WHY NOT SIMPLY REUSE ``campaign_summary``. It is a REPORTING query over the
    whole table, keyed on a campaign's FIRST run; this needs the answer for a
    run that has just been created and has no rows of its own yet, at the top of
    ``main()``, before the first billed call. Running the whole recursive stitch
    to read one chain would also make every batch run's startup cost a full-table
    scan of ``runs`` joined to ``inferences``.

    WHY THE FINGERPRINT MUST MATCH, and it is the same argument
    ``campaign_summary`` makes: a prompt bump, a renderer edit, a re-index or a
    model change between the crash and the resume breaks the chain, because
    "which configuration produced this number" is the question a campaign total
    is asked. A budget follows the campaign, and a re-configured run is a new
    campaign.

    NULLS: SQLite's ``IS`` is null-safe equality, so a field that degraded to
    NULL on both sides compares equal -- correct -- and two runs with NO STAMP
    AT ALL would also compare equal, which is not. Both sides are therefore
    additionally required to carry a ``fingerprint_version``, which is
    ``run_fingerprint``'s own key for "unknown configuration" and is
    ``campaign_summary``'s guard restated.

    A ROW WITH A NULL COST IS COUNTED IN ``unpriced`` AND CONTRIBUTES NOTHING TO
    ``usd``, which makes the sum a FLOOR. That is stated by every consumer --
    ``spend.describe_seed`` prints "<- A FLOOR, NOT A TOTAL" -- rather than
    hidden, because the direction of the error matters: a floor UNDER-counts,
    so the gate lets the campaign spend more than it should. The alternative,
    refusing to resume, would make one unpriceable historical row block a
    campaign; ``print_cost_by_model`` faced the identical choice and made the
    identical call.

    Args:
        run_id: the row this run just opened. ``None`` returns an empty result
            rather than raising -- a caller with no run row has no campaign.
        db_path: the database the run row is in.

    Returns:
        ``CampaignSpend``. Empty on any failure, which is the direction argued
        below.

    IT NEVER RAISES, AND THE DIRECTION IS THE UNCOMFORTABLE HALF. A failure here
    returns an EMPTY seed, so a resumed run starts its budget at zero and may
    spend a second full cap. The alternative -- refusing to run -- turns a
    read-only bookkeeping query into something that can stop a campaign, and
    this is called at the top of ``main()`` where the honest failure is "this
    database could not be read", which every write below would hit anyway. The
    failure is COUNTED into ``RUN_RECORD_FAILURES`` under ``campaign_spend:``
    and printed, so it is never silent.
    """
    try:
        if run_id is None:
            return CampaignSpend()

        db_path = resolve_inference_db_path(db_path)
        conn = _open_connection(db_path)
        try:
            cursor = conn.cursor()
            _fp = ", ".join(RUN_FINGERPRINT_COLUMNS)
            cursor.execute(
                f"SELECT id, resumed, {_fp} FROM runs WHERE id = ?", (run_id,))
            row = cursor.fetchone()
            if row is None:
                RUN_RECORD_FAILURES["campaign_spend:row_not_found"] += 1
                return CampaignSpend()

            _stamp = list(row[2:])
            # NO STAMP, NO CHAIN. `fingerprint_version` is RUN_FINGERPRINT_COLUMNS'
            # first member and is `run_fingerprint`'s own key for "this
            # configuration was never recorded". Stitching on an all-NULL stamp
            # would make every unstamped run in the table one campaign.
            if _stamp[0] is None:
                return CampaignSpend()

            _match = " AND ".join(f"{c} IS ?" for c in RUN_FINGERPRINT_COLUMNS)
            _statuses = ", ".join("?" for _ in CAMPAIGN_RESUMABLE_STATUSES)

            chain = []
            cur_id, cur_resumed = row[0], row[1]
            # THE WALK IS BOUNDED BY `id <` AND CANNOT LOOP: each step selects a
            # STRICTLY smaller id, so the sequence is decreasing in a finite set.
            # A `seen` guard would be defending against a database in which a
            # row's id is not its own.
            while cur_resumed == 1:
                cursor.execute(
                    f"SELECT id, resumed FROM runs "
                    f"WHERE id < ? AND status IN ({_statuses}) "
                    f"  AND fingerprint_version IS NOT NULL AND {_match} "
                    f"ORDER BY id DESC LIMIT 1",
                    (cur_id, *CAMPAIGN_RESUMABLE_STATUSES, *_stamp))
                prev = cursor.fetchone()
                if prev is None:
                    break
                chain.append(prev[0])
                cur_id, cur_resumed = prev[0], prev[1]

            if not chain:
                return CampaignSpend()

            _ids = ", ".join("?" for _ in chain)
            cursor.execute(
                f"SELECT COALESCE(SUM(estimated_cost_usd), 0.0), "
                f"       COUNT(*), "
                f"       SUM(CASE WHEN estimated_cost_usd IS NULL THEN 1 "
                f"                ELSE 0 END) "
                f"FROM inferences WHERE run_id IN ({_ids})",
                tuple(chain))
            total, rows, unpriced = cursor.fetchone()
        finally:
            conn.close()

        return CampaignSpend(usd=float(total or 0.0), rows=int(rows or 0),
                             unpriced=int(unpriced or 0),
                             run_ids=tuple(reversed(chain)))

    except Exception as exc:                                   # noqa: BLE001
        RUN_RECORD_FAILURES[f"campaign_spend:{type(exc).__name__}"] += 1
        console.out(f"⚠ The campaign's prior spend could not be read "
                    f"(non-critical): {type(exc).__name__}: {exc}")
        log.error("the resumed campaign's prior spend could not be read, so "
                  "this run's spend gate starts from zero",
                  event="campaign_spend_unreadable",
                  inference_run_id=run_id,
                  error_type=type(exc).__name__, error_message=str(exc))
        return CampaignSpend()



def finalize_run_record(run_id, status, db_path=None, note=None,
                        stop_reason=None):
    """Stamp ``finished_at`` and ``status`` on a run row. NEVER RAISES.

    Args:
        run_id:  what ``start_run_record`` returned. ``None`` is tolerated and
                 counted -- a caller whose run row was never opened has nothing
                 to finalize, and making that an exception would turn a missing
                 index entry into a crash at the end of a paid campaign.
        status:  one of ``RUN_RECORD_TERMINAL_STATUSES``. An unrecognised value
                 is replaced by ``FAILED`` and counted, never by ``FINISHED`` --
                 ``oncotriage/tracking.py:end_run``'s rule, adopted verbatim,
                 for its reason: a run whose ending could not be described is
                 not a run that ended well. ``RUNNING`` is unrecognised HERE
                 even though it is a member of ``RUN_RECORD_STATUSES``, because
                 finalizing a run to "still going" is the one thing the end of a
                 run must not do.
        db_path: the database the run row is in. Must resolve to the same file
                 ``start_run_record`` wrote to; ``None`` means the configured
                 production database.
        stop_reason: one of ``RUN_STOP_REASONS``, or ``None``.

                 ``None`` LEAVES THE COLUMN ALONE, exactly as ``note`` does and
                 for its reason: this function is public, nothing stops a caller
                 finalizing twice, and an unconditional ``stop_reason = ?``
                 would let a second call erase the first's.

                 AN UNRECOGNISED VALUE IS REFUSED AND COUNTED, never stored.
                 The column exists to be grouped on, and a value outside the
                 closed vocabulary is a bucket no ``GROUP BY`` consumer knows
                 about -- which is the failure a closed vocabulary with an open
                 writer produces. It is refused rather than mapped to a default,
                 because every default available here is a claim about a
                 mechanism that may not have run.

        note:    free text explaining how the run ended, or ``None``.

                 ``None`` LEAVES THE COLUMN ALONE rather than writing NULL over
                 it, and the difference is not academic: this function is public
                 and nothing stops a caller finalizing twice, so an
                 unconditional ``note = ?`` would let a second call with no note
                 erase the first call's. The SET list is assembled from what was
                 actually supplied.

                 Anything that is not a string is REFUSED and counted rather
                 than coerced -- ``str(exc)`` on an exception object, or
                 ``str(None)`` giving the four characters "None", would put a
                 plausible-looking sentence in a column a human reads and
                 believes. An empty or whitespace-only note is treated as no
                 note, because a column holding "" says nothing that NULL does
                 not already say.

                 Capped at ``RUN_NOTE_MAX_CHARS`` with the truncation NAMED in
                 the stored text, on ``control.read_stop_message``'s footing: a
                 silently cut note is a note whose ending a reader invents.

    Returns:
        True if exactly one row was updated. False on every failure, so a caller
        that wants to know can ask -- and both shipped callers do not, because
        the counter and the log line are what an operator reads.

    IT RUNS AFTER THE MONEY IS SPENT, which is the whole reason it may not
    raise: by this line the campaign has made one live Stage 5 call per patient
    and written its rows, and an index failure must not take those with it. That
    is ``log_run_metrics``'s argument and ``log_inference``'s, and the failure is
    reported the way both report theirs -- a console line, a structured record
    and ``RUN_RECORD_FAILURES``.

    "NEVER RAISES" MEANS WHAT IT MEANS EVERYWHERE ELSE IN THIS MODULE:
    ``except Exception``, so the three exceptions that are NOT ``Exception``
    subclasses -- ``KeyboardInterrupt``, ``SystemExit`` and ``GeneratorExit`` --
    still escape, exactly as they escape ``_write_inference_row``. A finalizer
    that swallowed a Ctrl-C would leave an operator holding a key down against a
    process that will not stop.

    THIS SENTENCE NAMED ``MemoryError`` AND WAS WRONG.
    ``issubclass(MemoryError, Exception)`` is True, so the handler below CATCHES
    it: a finalize that runs out of memory is counted under
    ``finalize:MemoryError`` and returns False like any other failure, and it
    does NOT propagate. ``flush_run_metrics`` measured that and recorded it as a
    finding against this docstring rather than editing a function it did not
    otherwise touch; this is that finding closed. The correction matters because
    a reader deciding whether a caller must handle an escaping MemoryError from
    this line would have written a handler that can never run.

    THE ROW COUNT IS CHECKED. ``UPDATE ... WHERE id = ?`` against an id that is
    not there succeeds and updates nothing; SQLite reports no error for it. A
    finalizer that did not read ``rowcount`` would report success for a run row
    that was never written, which is the "reported success, wrote nothing"
    shape the write-durability pass removed one function down.

    PATH RESOLUTION IS INSIDE THE TRY HERE, AND THAT IS A KNOWING DEVIATION.
    Everywhere else in this module -- ``log_inference``, and ``start_run_record``
    above -- it happens outside, so a configuration defect (``resolve_inferences_db``
    raises when the variable names a path whose parent is absent) reaches the
    operator instead of being swallowed as a logging fault. The deviation is
    forced by the contract: this function may not raise, full stop, because the
    money is already spent. It costs nothing in practice -- every write of the
    run this is finalizing resolved the same way and would have failed first --
    and the defect is not hidden, it is counted under its exception type and
    logged at ERROR.
    """
    try:
        if run_id is None:
            RUN_RECORD_FAILURES["finalize:no_run_id"] += 1
            log.warning("finalize_run_record was called with no run id; "
                        "nothing was finalized",
                        event="run_record_finalize_skipped", status=str(status))
            return False

        if status not in RUN_RECORD_TERMINAL_STATUSES:
            RUN_RECORD_FAILURES[f"finalize:unknown_status:{status}"] += 1
            log.warning("an unrecognised run status was replaced by FAILED",
                        event="run_record_status_unknown",
                        inference_run_id=run_id, status=str(status))
            status = "FAILED"

        db_path = resolve_inference_db_path(db_path)

        # THE SET LIST IS ASSEMBLED, NOT BRANCHED ON. Two hand-written UPDATE
        # strings would be two statements to keep in step; this is one, and the
        # column list and the bind tuple are built side by side so they cannot
        # disagree about their length the way a positional VALUES tuple can.
        _assignments = ["finished_at = ?", "status = ?"]
        _bind = [datetime.now().isoformat(), status]
        _stored_note = _coerce_run_note(note, run_id)
        if _stored_note is not None:
            _assignments.append("note = ?")
            _bind.append(_stored_note)
        # THE REASON IS VALIDATED HERE AND NOT COERCED. See the `stop_reason`
        # argument: a value outside RUN_STOP_REASONS is refused and counted, so
        # a typo leaves the column NULL -- which reads as "not a stop" and is
        # wrong in the honest direction -- rather than creating a bucket that
        # every GROUP BY over this column silently ignores.
        if stop_reason is not None:
            if stop_reason in RUN_STOP_REASONS:
                _assignments.append("stop_reason = ?")
                _bind.append(stop_reason)
            else:
                RUN_RECORD_FAILURES[
                    f"finalize:unknown_stop_reason:{stop_reason}"] += 1
                log.warning("an unrecognised run stop reason was refused "
                            "rather than stored; runs.stop_reason is left as "
                            "it was",
                            event="run_record_stop_reason_refused",
                            inference_run_id=run_id, reason=str(stop_reason))
        _bind.append(run_id)

        with _WRITE_LOCK:
            conn = _open_connection(db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    f"UPDATE runs SET {', '.join(_assignments)} WHERE id = ?",
                    tuple(_bind))
                updated = cursor.rowcount
                conn.commit()
            finally:
                conn.close()

        if updated != 1:
            RUN_RECORD_FAILURES["finalize:row_not_found"] += 1
            console.out(f"⚠ Run {run_id} could not be finalized: "
                        f"{updated} rows matched in {db_path}")
            log.error("the run row to finalize was not found",
                      event="run_record_finalize_missing",
                      inference_run_id=run_id, status=status,
                      count=updated, db_path=str(db_path))
            return False

        console.out(f"[Run] Closed run {run_id}: {status}")
        # THE NOTE'S PRESENCE IS LOGGED AND ITS TEXT IS NOT. It is free text an
        # operator typed at a terminal, so it belongs in the durable table a
        # human reads and not in a correlation-keyed structured record -- the
        # same line oncotriage/observability.py's field allowlist draws, drawn
        # here at the call site because `count` is on that allowlist and a
        # `note` field would not be.
        log.info("run record closed", event="run_record_closed",
                 inference_run_id=run_id, status=status, db_path=str(db_path),
                 count=len(_stored_note) if _stored_note else 0)
        return True

    except Exception as exc:                                   # noqa: BLE001
        RUN_RECORD_FAILURES[f"finalize:{type(exc).__name__}"] += 1
        console.out(f"⚠ Run {run_id} could not be finalized (non-critical): "
                    f"{type(exc).__name__}: {exc}")
        log.error("the run record could not be finalized; it will read as "
                  "RUNNING with a NULL finished_at",
                  event="run_record_finalize_failed",
                  inference_run_id=run_id, status=str(status),
                  error_type=type(exc).__name__, error_message=str(exc))
        return False


#------------------------------------------------------------------------------


# ===========================================================================
# THE HEALTH FLUSH
# ===========================================================================


def _note_run_metric_shape(reason, detail):
    """Count a malformed-flush reason; announce it on the console ONCE.

    ``reason`` is a fixed identifier and goes into the counter. ``detail`` is
    whatever the caller handed over that should not have been -- and it is a
    NAME rather than a value at three of the five call sites, which is why the
    line below says "name or value" rather than the "value" it said when the
    only caller was the mapping check. The distinction matters when reading the
    line: `non_identifier_name`, `non_integer_value` and `value_out_of_range`
    all report the counter's NAME, deliberately, because the value is the half
    that could be unbounded -- possibly the
    third-party or clinical text this table exists to exclude -- so it reaches
    the CONSOLE, which is transient and unindexed, and nothing else. Not the
    counter, whose totals are written to the very table in question; not the
    structured log, which is durable and correlation-keyed.

    ONCE PER REASON PER PROCESS, on ``_apply_journal_mode``'s footing: the flush
    runs once per completed patient, and a caller passing the wrong mapping
    would otherwise print an identical line for every patient of the run.

    RETURNS NOTHING. It was written returning False, and every caller then read
    ``return _note_run_metric_shape(...) and None`` -- which is ``False``, not
    ``None``, so the refusal sentinel the caller tests for was never produced
    and the malformed rows reached the insert loop as a bool. Caught by running.
    """
    RUN_METRICS_FLUSH_FAILURES[f"flush:{reason}"] += 1
    with _ANNOUNCE_LOCK:
        first = reason not in _RUN_METRIC_SHAPE_ANNOUNCED
        _RUN_METRIC_SHAPE_ANNOUNCED.add(reason)
    if first:
        console.out(
            f"⚠ run_metrics: a health flush was refused -- {reason}. "
            f"Offending name or value (console only, deliberately not "
            f"stored): {detail!r}\n"
            f"    flush_run_metrics() takes degradation.totals(), which is "
            f"{{counter name: int}}. degradation.snapshot() is the nested form "
            f"and its KEYS carry clinical and third-party text; it must not "
            f"reach a durable table. This will not be printed again this "
            f"process; RUN_METRICS_FLUSH_FAILURES keeps counting.")


# The range SQLite's INTEGER column can hold: signed 64-bit, exactly. Named
# rather than written as two literals at the comparison, because the pair is one
# fact about the storage engine and a reader has to be able to see that the
# bound is not this project's choice.
_SQLITE_INT64_MIN = -(2 ** 63)
_SQLITE_INT64_MAX = 2 ** 63 - 1


def _run_metric_rows(totals, counters_registered):
    """``(category, name, value)`` triples for one flush, or ``None`` to refuse.

    THE WHOLE FLUSH IS REFUSED ON ANY BAD MEMBER rather than the bad member
    being skipped, and that is the deliberate choice. A name that is not an
    identifier or a value that is not an int means this mapping did not come
    from ``degradation.totals()`` -- so every OTHER member of it is suspect too,
    and writing them would produce a partial record that reads as a complete
    one. Refusing is loud (a counter on the run-end report, and one console
    line) where a partial write is silent.

    ``str.isidentifier()`` IS THE TEST FOR A COUNTER NAME. Registry names are
    module-level Python variable names by construction, so every legitimate one
    passes; every key any counter in this project produces fails it, because
    they are built from exception types, units, statuses and observation text
    with separators in them. It is a mechanical guarantee rather than a promise.

    ``bool`` IS NOT AN INT HERE -- ``isinstance(True, int)`` is True, and a
    total of 1 that was really a True is a number nobody counted. Same trap, and
    the same exclusion, as ``RUN_FINGERPRINT_INTEGER_COLUMNS``.
    """
    if isinstance(counters_registered, bool) or not isinstance(counters_registered, int):
        _note_run_metric_shape(
            f"bad_registered_count:{type(counters_registered).__name__}",
            counters_registered)
        return None
    if counters_registered < 0:
        _note_run_metric_shape("bad_registered_count:negative",
                               counters_registered)
        return None
    if not isinstance(totals, dict):
        _note_run_metric_shape(f"not_a_mapping:{type(totals).__name__}",
                               type(totals).__name__)
        return None

    rows = []
    for name, value in totals.items():
        if not isinstance(name, str) or not name.isidentifier():
            _note_run_metric_shape("non_identifier_name", name)
            return None
        # NESTED FIRST, because it is the ONE malformed shape that would carry
        # clinical text -- a caller who passed snapshot() instead of totals().
        # Reported under its own key so that mistake is diagnosable rather than
        # arriving as a generic "not an integer".
        if isinstance(value, (dict, list, tuple, set)):
            _note_run_metric_shape("nested_value", name)
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            _note_run_metric_shape("non_integer_value", name)
            return None
        # AND IT HAS TO FIT IN THE COLUMN. A Python int is unbounded and
        # SQLite's INTEGER is signed 64-bit, so a total outside that range is a
        # value this column cannot hold. Left to the insert it raises
        # OverflowError, which the flush's broad handler catches and counts as
        # `flush:OverflowError` -- a key that names the exception and not the
        # counter, so the one fact an operator needs (WHICH counter overflowed)
        # is the one thing the record does not carry. Refused here instead, with
        # the name, before the DELETE opens a transaction.
        #
        # UNREACHABLE BY ANY COUNTER THIS PROJECT INCREMENTS BY ONE -- 2**63 is
        # more events than a campaign could produce in the age of the universe.
        # It is here because `totals()` is a mapping this function's contract
        # says it does not trust, and every other member of that contract is
        # checked; a bound that is checked everywhere except at the top of the
        # range is a bound an in-process caller can walk past with a synthesized
        # value.
        if not (_SQLITE_INT64_MIN <= value <= _SQLITE_INT64_MAX):
            _note_run_metric_shape("value_out_of_range", name)
            return None
        rows.append((RUN_METRIC_CATEGORY_DEGRADATION, name, value))

    # THE META ROWS GO LAST so the insert order matches the read order a human
    # would want, and they are built from the SAME two facts the caller just
    # supplied -- `counters_nonzero` is len(rows) rather than a second count of
    # the same mapping, so the two cannot disagree.
    rows.append((RUN_METRIC_CATEGORY_META,
                 RUN_METRIC_META_COUNTERS_REGISTERED, counters_registered))
    rows.append((RUN_METRIC_CATEGORY_META,
                 RUN_METRIC_META_COUNTERS_NONZERO, len(rows) - 1))
    return rows


def flush_run_metrics(run_id, totals, counters_registered, db_path=None):
    """Replace ``run_id``'s health rows with ``totals``. NEVER RAISES.

    Args:
        run_id: what ``start_run_record`` returned. ``None`` is tolerated and
            counted, exactly as ``finalize_run_record`` tolerates it: a caller
            with no run row has nothing to attach metrics to, and making that an
            exception would turn a missing index entry into a crash inside a
            worker thread's done-callback.
        totals: ``{counter name: int}`` -- ``oncotriage/degradation.py``'s
            ``totals()``, and NOT its ``snapshot()``. See ``_run_metric_rows``
            for what is checked and why the check is mechanical.

            TAKEN AS AN ARGUMENT AND NOT READ HERE, for two reasons and either
            would be sufficient. The layering one: ``oncotriage.degradation``
            imports THIS module, so this module cannot import it back. The
            correctness one, which is the reason that would matter even without
            it: the run-end flush must describe the SAME instant as the printed
            report and the logged summary, and the only way to guarantee that is
            for one snapshot to be taken once and handed to all three.
        counters_registered: how many counters were consulted to produce
            ``totals``. REQUIRED, with no default, on
            ``empty_database(db_path, flag)``'s precedent -- a default would
            write a number nobody measured into the one row whose job is to say
            how much was measured.
        db_path: which database. ``None`` means the configured production one,
            through the same resolver ``log_inference`` uses.

    Returns:
        True if the rows landed. False on every failure, so a caller that wants
        to know can ask; the shipped callers do not, because
        ``RUN_METRICS_FLUSH_FAILURES`` and the log line are what an operator
        reads.

    IT MAY NOT RAISE, AND THE REASONING IS ``finalize_run_record``'s VERBATIM
    rather than by analogy. It is called from ``_on_done``, a done-callback on a
    worker thread, once per completed patient -- so by the time it runs, that
    patient has cost a live Stage 5 call, and an exception there would be
    swallowed by ``concurrent.futures`` and logged to somebody else's logger
    where nothing in this project reads it. A health record that can destroy the
    run it describes is worse than no health record.

    "NEVER RAISES" MEANS ``except Exception``, so what escapes is what is not an
    ``Exception`` subclass -- ``KeyboardInterrupt``, ``SystemExit`` and
    ``GeneratorExit`` -- exactly as it escapes ``_write_inference_row`` and
    ``finalize_run_record``. A flush that swallowed a Ctrl-C would leave an
    operator holding a key down against a run that will not stop.

    NOTE THE CORRECTION. This sentence was copied from
    ``finalize_run_record``'s docstring, which named ``MemoryError`` as a thing
    that escapes. It does not: ``issubclass(MemoryError, Exception)`` is True,
    so this handler catches it. Measured rather than repeated, and reported here
    as a finding against four neighbouring docstrings rather than fixed, because
    correcting a claim in functions this pass did not otherwise touch was a
    separate edit.

    THAT SEPARATE EDIT HAS SINCE HAPPENED and all four now name the three
    ``BaseException``-only classes. This paragraph is kept as the record of
    where the correction was first measured -- the finding was real, and the
    sentence it corrected had been copied four times before anyone ran
    ``issubclass``.

    PATH RESOLUTION IS INSIDE THE TRY, which is ``finalize_run_record``'s
    knowing deviation repeated here for its reason. Everywhere else in this
    module it happens outside, so a configuration defect --
    ``resolve_inferences_db`` raises when the variable names a path whose parent
    is absent -- reaches the operator instead of being swallowed as a logging
    fault. The deviation is forced by the contract: this may not raise, full
    stop. It costs nothing in practice, because ``start_run_record`` resolved
    the same way before the run began and would have failed first, and the
    defect is not hidden -- it is counted under its exception type and logged at
    ERROR.

    THREAD SAFETY IS ``_WRITE_LOCK``, the same one every other statement in this
    module is issued under -- and what it buys is stated as MEASURED rather than
    as argued, because the two are not the same here.

    MEASURED: a revert harness stripped this lock and drove MAX_WORKERS threads
    through the flush behind a barrier, and NOTHING WAS LOST OR DUPLICATED. That
    is the same honest finding ``tests/test_package_invariants.py`` section 5e
    records for the steady-state INSERT path, and it has the same cause: with
    ``sqlite3``'s default isolation level the DELETE opens a transaction that
    the executemany and the commit finish, and SQLite's own file locking already
    refuses a second write transaction while one is open.

    SO WHY IT IS TAKEN, in the order the reasons decided it. First, the module's
    invariant is "every database statement in this file is issued under
    ``_WRITE_LOCK``", and an invariant with a documented exception is a
    convention -- ``start_run_record`` takes it for the same reason on a path
    that is main-thread-only. Second, it converts contention from MAX_WORKERS
    threads busy-waiting on SQLite's ``busy_timeout`` into an uncontended
    in-process queue. Third, and this is the one that would bite: the atomicity
    above is a property of the DEFAULT isolation level. Set
    ``isolation_level=None`` on ``_open_connection`` -- one keyword, and
    autocommit is a plausible future edit -- and the DELETE and the INSERTs
    become separate transactions, at which point two flushes really can
    interleave and the loser's rows really are wiped after they were written.
    The lock is what makes that edit safe instead of silently destructive.

    The single transaction is what makes it atomic with respect to READERS on
    other connections, which is the half a lock cannot provide at all.

    THE DELETE IS SCOPED TO ONE ``run_id``. Rows belonging to other runs -- and
    every historical run in the same file -- are untouched.
    """
    try:
        if run_id is None:
            RUN_METRICS_FLUSH_FAILURES["flush:no_run_id"] += 1
            log.warning("a health flush was asked for with no run id; nothing "
                        "was written",
                        event="run_metrics_flush_skipped",
                        count=len(totals) if isinstance(totals, dict) else 0)
            return False

        rows = _run_metric_rows(totals, counters_registered)
        if rows is None:
            # Already counted and announced by _note_run_metric_shape. Logged
            # here with the COUNT only -- never a name, never a value.
            log.error("a health flush was refused because its totals were not "
                      "the {name: int} form; nothing was written",
                      event="run_metrics_flush_refused",
                      inference_run_id=run_id,
                      count=len(totals) if isinstance(totals, dict) else 0)
            return False

        db_path = resolve_inference_db_path(db_path)
        written_at = datetime.now().isoformat()

        with _WRITE_LOCK:
            _ensure_database(db_path)
            conn = _open_connection(db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM run_metrics WHERE run_id = ?",
                               (run_id,))
                cursor.executemany(
                    "INSERT INTO run_metrics "
                    "(run_id, category, name, value, written_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [(run_id, category, name, value, written_at)
                     for category, name, value in rows])
                conn.commit()
            finally:
                conn.close()

        # DEBUG, NOT INFO. This fires once per completed patient; at INFO a
        # 22,000-patient run would put 22,000 identical records into the
        # structured stream for a fact whose current value is already in the
        # table. The failure paths above stay at WARNING and ERROR.
        log.debug("run health flushed", event="run_metrics_flushed",
                  inference_run_id=run_id, count=len(rows),
                  db_path=str(db_path))
        return True

    except Exception as exc:                                   # noqa: BLE001
        RUN_METRICS_FLUSH_FAILURES[f"flush:{type(exc).__name__}"] += 1
        log.error("a health flush failed; this run's persisted degradation "
                  "record is stale by at least one flush",
                  event="run_metrics_flush_failed",
                  inference_run_id=run_id,
                  error_type=type(exc).__name__, error_message=str(exc))
        return False


def analyze_database(db_path=None):
    """Refresh SQLite's planner statistics for db_path. NEVER RAISES.

    Returns True if ANALYZE ran and committed, False on any failure.

    WHAT IT ACTUALLY BUYS, stated as measured rather than as a general good.
    Without `sqlite_stat1` SQLite plans from a built-in guess -- roughly ten
    rows per distinct index value, for every index, whatever the data. On a
    22,000-row `inferences` table the real numbers are nothing like that:

        idx_inferences_patient_id   1 row per value
        idx_inferences_timestamp    262 rows per value
        idx_inferences_run_id       1100 rows per value

    A query whose WHERE names both `run_id` and `timestamp` therefore has a
    genuine choice, and without statistics the planner has no basis for it --
    it will happily search the 1100-row index and filter, where the 262-row one
    would have read a quarter as much. WITH statistics it chooses on the
    measured numbers.

    AND WHAT IT DOES NOT BUY, because the honest half is worth writing down: on
    the four single-predicate queries the three new indexes were added for, it
    changed nothing measurable (0.012 -> 0.011 ms, 1.061 -> 1.167, 0.015 ->
    0.015, 0.778 -> 0.779). There is only one index that can serve those, so
    there is no choice for statistics to inform. It earns its keep on plans with
    a choice, and it costs 9 ms at 22,000 inferences and 330,000 trial_matches
    -- measured, once per run, against a campaign measured in hours.

    IT MAY NOT RAISE, on `flush_run_metrics`' and `finalize_run_record`'s
    footing rather than by analogy: it runs at the END of a run, after every
    patient has cost a live Stage 5 call, and the worst thing a stale statistics
    table can do is make a later query choose a worse index. A campaign
    destroyed at its last statement by an optimisation is the wrong trade in
    every direction.

    "NEVER RAISES" MEANS ``except Exception``, so what escapes is what is not an
    ``Exception`` subclass -- ``KeyboardInterrupt``, ``SystemExit`` and
    ``GeneratorExit`` -- exactly as it escapes ``_write_inference_row``,
    ``finalize_run_record`` and ``flush_run_metrics``. An ANALYZE that swallowed
    a Ctrl-C would leave an operator holding a key down against a run that has
    already finished everything that mattered.

    IT TAKES ``_WRITE_LOCK``. ANALYZE writes `sqlite_stat1`, so it is a write
    statement in this module and the module's invariant is that every one of
    them is issued under that lock. It does NOT go through
    ``run_with_write_retry``: the retry exists so a row is not lost, and there
    is no row here -- a contended ANALYZE is worth exactly one attempt and a
    counter, because the next run's will do the same job.
    """
    try:
        db_path = resolve_inference_db_path(db_path)
        with _WRITE_LOCK:
            conn = _open_connection(db_path)
            try:
                conn.execute("ANALYZE")
                conn.commit()
            finally:
                conn.close()
        log.info("sqlite planner statistics refreshed", event="analyze",
                 db_path=str(db_path))
        return True
    except Exception as exc:                                   # noqa: BLE001
        ANALYZE_FAILURES[type(exc).__name__] += 1
        log.warning("sqlite planner statistics could not be refreshed; queries "
                    "will be planned from SQLite's default guesses",
                    event="analyze_failed",
                    error_type=type(exc).__name__, error_message=str(exc))
        return False


#------------------------------------------------------------------------------


# _resolve_primary_cancer MOVED OUT in pass 20c-2c.
#
# It lives in oncotriage/registries/primary_cancer.py now and is imported at the
# top of this module. It is a domain question about SNOMED and ICD-10 codes, it
# opens no database, and it sat here only because this is where the answer was
# first needed. The consequence was an import edge pointing the wrong way:
# File 13's three terminal nodes called it, so the AGENT depended on the STORAGE
# layer for a registry lookup.
#
# Both callers -- oncotriage/agent/terminal.py and log_inference below -- now
# import it from the registries package, and neither imports the other. The
# function itself is byte-identical to the one pass 2b left here, which
# tests/test_package_invariants.py re-derives with ast.unparse against git HEAD.
#
# It is still re-exported by "14- Database Logger.py", because Files 17, 25, 26,
# 32, 36, 37, 38, 40 and 45 read the name out of the shared exec namespace.


#------------------------------------------------------------------------------


# Logging function
def log_inference(result: Dict, patient_data: Dict, db_path=None,
                  run_id=None):
    """
    Log inference result to SQLite database.

    Non-critical operation: Errors are logged but not raised to avoid
    breaking the main pipeline if database logging fails.

    The one exception is UnknownModelPricingError. Cost is computed BEFORE the
    try block below precisely so it cannot be caught by it: an unpriced model
    is a configuration defect, not a database failure, and swallowing it would
    either drop the row entirely (with a message blaming logging) or, before
    get_model_cost() learned to raise, write a row asserting the run was free.
    Either way the operator is not told that the cost column has stopped
    meaning anything. It propagates to the caller instead.

    Args:
        result:       The pipeline result dict from a terminal node.
        patient_data: The parsed patient dict, used for the fallbacks.
        db_path:      Database to write to. None means the configured
                      production database -- see resolve_inference_db_path.
                      Files 36, 37, 38, 40 and 45 pass a temporary path; before
                      pass 20c-2b they rebound a global instead, which a module
                      function cannot see.
        run_id:       The ``runs.id`` this write belongs to, or None.

                      DEFAULTS TO None, WHICH IS A VALUE AND NOT A FALLBACK.
                      NULL in the column means "not part of a recorded batch
                      run", and it is what "17- FastAPI Server.py" and every
                      direct caller write on purpose -- a request is not a
                      campaign. See the column's note in
                      INFERENCE_COLUMN_ADDITIONS.

                      IT IS PASSED, NEVER LOOKED UP. There is deliberately no
                      module-level "current run" that this function could read:
                      such a state survives into the next run in the same
                      process and would attribute the second campaign's rows to
                      the first one's run row. That is the argument
                      ``oncotriage/batch/runner.py:clear_write_ledger`` and
                      ``run_fingerprint.clear_cache()`` are both written from,
                      and threading the id as an argument is the version of it
                      that cannot be forgotten -- there is nothing to clear.

    Returns:
        The database path this call actually used, so a caller can ASSERT where
        it wrote rather than assuming. That return value is what makes the five
        isolation tests checkable: each of them compares it against its own
        temporary file. It is returned even when the write fails, because the
        path is resolved before the try block and "which database did you aim
        at" is answerable whether or not the shot landed.

    THREAD SAFETY (pass 20c-3b). Everything that touches the database runs
    under ``_WRITE_LOCK``. That lock used to be a monkeypatch inside
    "25- Batch Runner.py", so the batch runner was serialized and
    "17- FastAPI Server.py" -- which calls this from the event loop's thread
    pool, once per in-flight request -- was not. See the block above
    ``initialize_database`` for what two unserialized writers on one SQLite file
    actually cost, which is a lost row reported as a success.

    The path resolution and ``get_model_cost()`` deliberately stay OUTSIDE the
    lock: neither touches the database, and holding a write lock while doing
    configuration lookups would serialize work that has no reason to be
    serialized.
    """

    # Resolved BEFORE the try, alongside get_model_cost() and for the same
    # reason: a path that cannot be resolved is a configuration defect, not a
    # database failure, and the broad except below exists only for the latter.
    # A caller that passes db_path resolves nothing at all.
    db_path = resolve_inference_db_path(db_path)

    # The model that ACTUALLY answered, read off response.model by Stage 5 and
    # carried to all three terminal nodes by _pipeline_provenance() (File 13).
    # Not MATCHING_MODEL: that is what was asked for, and an alias can resolve
    # to a dated snapshot, so pricing and logging against it would attribute a
    # row to a model that may never have served it. It is also read at log time,
    # which means a config edit between the run and the log would relabel the
    # row -- exactly the class of drift this project treats as a defect.
    #
    # None when no Stage 5 response was obtained: node_no_candidates, or a
    # failure before the first call returned. The column then stores NULL,
    # which says "no model produced this row" rather than naming one that did
    # not run.
    matching_model_used = result.get("matching_model")

    # Calculate cost using pricing config. Outside the try — see the docstring.
    #
    # MATCHING_MODEL is the pricing key ONLY in the None case above, where
    # there are no Stage 5 tokens to price and the arithmetic is 0 x rate = 0
    # whichever priced model is named. This is not a recovery path around
    # get_model_cost(): the lookup still happens, still raises
    # UnknownModelPricingError for an unpriced model, and still sits outside
    # the try block so an unpriced model aborts the whole log rather than
    # writing a row that claims the run was free. What it is not allowed to do
    # is raise on a no-candidates run purely because that run has no model name
    # to look up.
    #
    # WHICH PATH WAS TAKEN IS RECORDED, as this project requires of any
    # fallback: matching_model is written NULL on exactly the rows where the
    # fallback key was used, so "priced against the model that answered" and
    # "priced against the configured model because nothing answered" are
    # separable in the table without a second column. A NULL matching_model row
    # carrying non-zero llm_classifier tokens would be the one case where they are not,
    # and File 16's Query 10 and File 21's cost tab both call that out.
    #
    # Reasoning tokens are NOT added to the output figure here. They are
    # already inside llm_classifier_output_tokens (see the schema note on
    # llm_classifier_reasoning_tokens); adding them would bill every one of them twice.
    total_cost = get_model_cost(
        matching_model_used or MATCHING_MODEL,
        result.get("llm_classifier_input_tokens", 0),
        result.get("llm_classifier_output_tokens", 0)
    )

    # EVERYTHING BELOW THIS LINE TOUCHES THE DATABASE, so it is serialized.
    # The body is a separate function rather than an indented `with` block for
    # one reason: the INSERT statements inside it are triple-quoted strings
    # whose indentation is part of nothing, but re-indenting 250 lines to add a
    # `with` would bury the actual change of this pass in a whitespace diff
    # nobody can review. The guarantee is identical.
    #
    # FIVE `with _WRITE_LOCK:` SITES IN THIS MODULE AS OF THE RUN-IDENTITY
    # PASS -- initialize_database, _ensure_database, here, start_run_record and
    # finalize_run_record -- and the retry loop is INSIDE this one rather than
    # around it. Two reasons for the nesting, both load-bearing: a retry that
    # released and re-took the lock would let a second thread interleave between
    # attempts, which is the interleaving the lock exists to forbid; and section
    # 5e of tests/test_package_invariants.py asserts on the number of sites its
    # control STRIPPED, so a site added without updating that number fails a
    # check that is measuring the lock rather than measuring this pass. The
    # count moved 3 -> 5 there, deliberately and in the same commit: the
    # assertion's job is non-degeneracy (the control really did remove
    # something), and the two new sites are two more places the control must
    # reach.
    with _WRITE_LOCK:
        outcome = _write_inference_row_with_retry(
            result, patient_data, db_path, matching_model_used, total_cost,
            run_id)

    # AFTER the finally inside _write_inference_row, not inside it. A return
    # inside a finally block SWALLOWS any exception propagating out of the try
    # -- and one exception is meant to propagate from this function:
    # UnknownModelPricingError is raised above, so it never reaches here, but a
    # KeyboardInterrupt or a SystemExit raised inside the write would be
    # discarded by a `return` in the finally and the caller would be told the
    # write succeeded. It escapes the `with` above instead, releasing the lock
    # on the way, and this line is never reached.
    #
    # THIS COMMENT SAID "KeyboardInterrupt or a MemoryError" AND THE SECOND WAS
    # WRONG. `issubclass(MemoryError, Exception)` is True, so the handlers
    # inside _write_inference_row catch it and it never propagates to be
    # discarded here. The three that are not Exception subclasses --
    # KeyboardInterrupt, SystemExit, GeneratorExit -- are the ones this
    # placement protects, and SystemExit is the one that actually reaches this
    # module in production: it is what the entry point's SIGTERM handler raises.
    #
    # THE RETURN IS AN InferenceWriteResult, which IS db_path -- see that class
    # for why a str subclass rather than a tuple. `== db_path` and every other
    # string operation are unchanged; `.ok` is the new fact.
    return InferenceWriteResult(
        db_path,
        ok=outcome["ok"],
        error=outcome["error"],
        attempts=outcome["attempts"],
        inference_id=outcome["inference_id"],
    )


def _write_inference_row_with_retry(result: Dict, patient_data: Dict, db_path,
                                    matching_model_used, total_cost,
                                    run_id=None):
    """Attempt the write up to ``SQLITE_WRITE_MAX_ATTEMPTS`` times.

    CALLERS HOLD ``_WRITE_LOCK``; see log_inference for why the loop is inside
    it rather than around it.

    Returns a dict: ok, error, attempts, inference_id. RAISES NOTHING that
    ``_write_inference_row`` did not already raise, which is nothing except the
    three that are not ``Exception`` subclasses and must escape
    (``KeyboardInterrupt``, ``SystemExit``, ``GeneratorExit``) -- so the contract
    "a database fault does not kill the pipeline" is unchanged.

    IT SAID "the two ... (KeyboardInterrupt, MemoryError)" AND MemoryError IS
    NOT ONE OF THEM: ``issubclass(MemoryError, Exception)`` is True, so
    ``_write_inference_row``'s handlers catch it, record it as a terminal (not
    retryable) failure, and this function returns ``ok=False`` for it like any
    other. A caller written against the old wording would have expected a
    MemoryError to reach it and would never see one.

    Only the transient class is retried. ``_is_retryable`` is where that is
    decided and the block above it is why the migration race is excluded.
    """
    # max(1, ...) so a misconfigured 0 -- or a negative -- still makes ONE
    # attempt rather than skipping the loop entirely, which would leave
    # `outcome` None and turn a config typo into an AttributeError inside the
    # writer. A logging config defect must not become a pipeline crash; that is
    # the same reasoning as the broad handler below it.
    max_attempts = max(1, int(SQLITE_WRITE_MAX_ATTEMPTS))

    attempts = 0
    outcome = None

    while attempts < max_attempts:
        attempts += 1
        outcome = _write_inference_row(result, patient_data, db_path,
                                       matching_model_used, total_cost,
                                       run_id)
        outcome["attempts"] = attempts

        if outcome["ok"]:
            if attempts > 1:
                # Recovered. Recorded at INFO rather than silently, because a
                # run that needed 400 retries to lose nothing is a run whose
                # next increment of load loses rows.
                log.info("inference write succeeded after retrying",
                         event="inference_write_retried",
                         patient_id=str(result.get("patient_id", "")),
                         attempts=attempts, db_path=str(db_path))
            return outcome

        exc = outcome["exception"]
        if not _is_retryable(exc) or attempts >= max_attempts:
            break

        INFERENCE_WRITE_RETRIES[type(exc).__name__] += 1
        delay = SQLITE_WRITE_RETRY_BASE_DELAY * (2 ** (attempts - 1))
        console.out(f"  ↻ Retrying inference write in {delay:.2f}s "
                    f"(attempt {attempts + 1}/{max_attempts}): "
                    f"{type(exc).__name__}: {exc}")
        log.warning("inference write contended, retrying",
                    event="inference_write_retry",
                    patient_id=str(result.get("patient_id", "")),
                    attempts=attempts, max_retries=max_attempts,
                    delay_s=round(delay, 3),
                    error_type=type(exc).__name__, error_message=str(exc),
                    db_path=str(db_path))
        time.sleep(delay)

    # Given up. The pipeline result is NOT destroyed -- that is still the
    # contract -- but the loss is now recorded in three places a reader can
    # reach: this counter, this log record, and the returned object's `.ok`.
    exc = outcome["exception"]
    retryable = "retryable" if _is_retryable(exc) else "terminal"
    INFERENCE_WRITE_FAILURES[f"{type(exc).__name__}:{retryable}"] += 1
    log.error("inference write LOST after exhausting attempts",
              event="inference_write_lost",
              patient_id=str(result.get("patient_id", "")),
              attempts=attempts, max_retries=max_attempts,
              status=retryable,
              error_type=type(exc).__name__, error_message=str(exc),
              db_path=str(db_path))
    return outcome


def _write_inference_row(result: Dict, patient_data: Dict, db_path,
                         matching_model_used, total_cost, run_id=None):
    """The database half of log_inference. CALLERS HOLD ``_WRITE_LOCK``.

    Split out of log_inference in pass 20c-3b so the lock could be taken with a
    `with` statement without re-indenting the whole body. Everything here is
    byte-for-byte what log_inference did; nothing was reordered.

    RETURNS AN OUTCOME DICT as of the write-durability pass -- ``ok``,
    ``error``, ``exception``, ``attempts``, ``inference_id``. It used to return
    nothing at all, which is precisely the defect: the two handlers below print
    "non-critical" and the caller was told the same thing on both paths.

    Raising is still confined to what raised before -- nothing but the three
    that are not ``Exception`` subclasses and are meant to escape:
    ``KeyboardInterrupt``, ``SystemExit`` and ``GeneratorExit`` -- so the "a
    logging fault does not kill the pipeline" contract is unchanged.

    IT NAMED ``MemoryError`` AS ONE OF THEM AND THAT WAS WRONG.
    ``issubclass(MemoryError, Exception)`` is True, so the two handlers below
    catch it: an out-of-memory write is recorded as a terminal failure and the
    outcome dict says so. What escapes is the BaseException-only set, and
    ``SystemExit`` is the member that actually arrives here -- the batch
    runner's SIGTERM handler raises it. The single caller,
    ``_write_inference_row_with_retry``, decides what to do with a failure.

    ONE CALL IS ONE TRANSACTION, which is what makes a retry safe. sqlite3's
    default isolation opens an implicit transaction at the first INSERT and
    ``conn.rollback()`` in both handlers below discards the inference row AND
    its trial_matches children together, so a retried attempt cannot duplicate a
    partially-written row. The only statement after ``conn.commit()`` is a
    console line; if THAT raised, the generic handler records a terminal (not
    retryable) failure, so a committed row is never written twice.
    """
    conn = None
    inference_id = None
    try:
        # Item 20b: the schema is no longer created when this file is loaded,
        # so it is ensured here, once per resolved path, before the first
        # write. Inside the try on purpose: a table that cannot be created is
        # a database failure, and this function's contract is that database
        # failures are reported and do not kill the pipeline. That is the
        # opposite of get_model_cost() above, which is outside the try because
        # an unpriced model is a configuration defect, not a database one.
        _ensure_database(db_path)

        # Through _open_connection: the busy timeout is per connection and this
        # is the one that meets the other process's writes.
        conn = _open_connection(db_path)
        cursor = conn.cursor()

        demographics = patient_data.get("demographics", {})
        conditions = patient_data.get("conditions", [])
        timings = result.get("stage_timings", {})

        # ECOG performance status. Preferred source is the result dict, where
        # _pipeline_provenance() (File 13) puts it on all three terminal paths;
        # the patient dict is the fallback for a caller logging a result that
        # did not come from the graph.
        #
        # The source is chosen ONCE for all three columns rather than per field.
        # Per-field fallback could take the value from one patient and the
        # selection path from another, producing a row that describes no patient
        # at all -- and the three columns are only interpretable together.
        #
        # ecog_selection is the marker for "did this report", the same role it
        # plays in the schema comment above: a terminal node sets it to a string
        # whenever the parsed field was present and leaves it None when it was
        # not. It is used instead of ecog_value because ecog_value is
        # legitimately None for a patient with no observation, and legitimately
        # 0 -- falsy, and the most eligible score there is -- for a fully active
        # one. Neither can mark presence.
        _patient_ecog = patient_data.get("ecog_performance_status") or {}
        if result.get("ecog_selection") is not None:
            ecog_value              = result.get("ecog_value")
            ecog_selection          = result.get("ecog_selection")
            ecog_observations_found = result.get("ecog_observations_found")
            ecog_date               = result.get("ecog_date")
        else:
            ecog_value              = _patient_ecog.get("value")
            ecog_selection          = _patient_ecog.get("selection")
            ecog_observations_found = _patient_ecog.get("observations_found")
            ecog_date               = _patient_ecog.get("date")

        # The parser's stand-in for an Observation carrying no date at all. It
        # reaches this writer for real -- the undated_single selection path uses
        # such an observation -- and it must not be stored: see the ecog_date
        # entry in INFERENCE_COLUMN_ADDITIONS for why a non-date in this column
        # would sort as the NEWEST reading rather than as no reading.
        #
        # Applied AFTER the source is chosen, not inside either branch, so both
        # routes get the identical treatment. Compared against the constant
        # rather than re-parsed with parse_partial_date(): that function
        # increments PARTIAL_DATE_DEGRADATIONS on an out-of-range component, and
        # the selected observation has already been through it once at parse
        # time, so re-parsing here would double-count a real data-quality signal
        # in the one process that runs both.
        if ecog_date == UNKNOWN_DATE:
            ecog_date = None

        # Sum of stage durations only — excludes LangGraph routing overhead (~50-200ms)
        total_time = sum(timings.values())

        cursor.execute('''
            INSERT INTO inferences (
                patient_id, timestamp, age, sex, race, ethnicity, primary_condition,
                condition_count, medication_count, allergy_count, expanded_query,
                candidates_retrieved, candidates_reranked, 
                bm25_retrieved, vector_retrieved, 
                candidates_after_rule_filter,
                candidates_after_quality_filter,
                candidates_filtered, mesh_dropped, mesh_resolution,
                stage_dropped, histology_dropped,
                candidates_evaluated,
                eligible_matches, near_misses,
                not_evaluable_trials, cross_vocab_remaps,
                query_expansion_time, hybrid_retrieval_time, cross_encoder_time,
                rule_filter_time, llm_classifier_evaluation_time, total_time,
                llm_classifier_prompt, llm_classifier_input_tokens, llm_classifier_output_tokens,
                matching_model, cross_encoder_model,
                pricing_version, estimated_cost_usd, qdrant_collection, error,
                patient_data_hash, expansion_prompt,
                llm_classifier_retries, ablation_flags, hallucinated_trials,
                retrieval_channels, retrieval_channels_expected,
                retrieval_channels_ok, retrieval_degraded,
                retrieval_trials_lost, query_expansion_path,
                mesh_filter_applied, mesh_filter_skip_reason,
                stage_filter_applied, stage_filter_skip_reason,
                histology_filter_applied, histology_filter_skip_reason,
                age_filter_applied, age_filter_skip_reason,
                sex_filter_applied, sex_filter_skip_reason,
                degraded_run,
                age_reference_date, birth_date_precision,
                ecog_value, ecog_selection, ecog_observations_found, ecog_date,
                llm_classifier_truncation_splits, llm_classifier_output_tokens_estimated,
                not_evaluable_truncated, llm_classifier_calls,
                llm_classifier_reasoning_tokens,
                llm_classifier_prompt_version, llm_classifier_prompt_sha256,
                llm_classifier_patient_record_tokens,
                llm_classifier_cached_input_tokens, llm_classifier_call_details,
                llm_classifier_packed_chunks, llm_classifier_packing,
                llm_classifier_output_split_threshold,
                llm_classifier_output_ceiling,
                llm_classifier_input_tokens_estimated,
                llm_classifier_input_budget,
                matching_provider, matching_call_mode,
                verdict_normalizations, remapped_trials,
                run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            result["patient_id"],
            result["timestamp"],
            demographics.get("age"),
            demographics.get("sex"),
            demographics.get("race"),
            demographics.get("ethnicity"),
            result.get("primary_condition") or _resolve_primary_cancer(conditions),
            result.get("condition_count", len(deduplicate_by_display(patient_data.get("conditions", [])))),
            result.get("medication_count", len(deduplicate_by_display(patient_data.get("medications", [])))),
            result.get("allergy_count", len(patient_data.get("allergies", []))),
            result.get("expanded_query", ""),
            result.get("candidates_retrieved", 0),
            result.get("candidates_reranked", 0),
            # Observed per-channel counts from Stage 2, not the configured
            # request sizes. Inserting BM25_RETRIEVAL_SIZE / VECTOR_RETRIEVAL_SIZE
            # here made both columns constant across every row, so any ratio
            # built on them (File 16's fusion_efficiency) described the config
            # rather than the run, and a single-channel ablation still logged
            # both channels as full. NULL when the key is absent, which means a
            # result dict that did not come from a pipeline terminal node.
            result.get("bm25_retrieved"),
            result.get("vector_retrieved"),
            result.get("candidates_after_rule_filter", 0),
            result.get("candidates_after_quality_filter", 0),
            result.get("candidates_filtered", 0),
            result.get("mesh_dropped", 0),
            result.get("mesh_resolution", ""),
            result.get("stage_dropped", 0),
            result.get("histology_dropped", 0),
            result.get("candidates_evaluated", 0),
            len(result.get("matches", [])),
            len(result.get("near_misses", [])),
            # Non-evaluations are counted here, never folded into near_misses:
            # a trial that could not be assessed is not a rejection.
            result.get("not_evaluable_trials", len(result.get("not_evaluable", []))),
            result.get("cross_vocab_remaps", 0),
            timings.get("query_expansion", 0),
            timings.get("hybrid_retrieval", 0),
            timings.get("cross_encoder", 0),
            timings.get("rule_filter", 0),
            timings.get("llm_classifier_evaluation", 0),
            total_time,
            result.get("llm_classifier_prompt", ""),
            result.get("llm_classifier_input_tokens", 0),
            result.get("llm_classifier_output_tokens", 0),
            # Resolved above, outside the tuple, because the same value is what
            # get_model_cost() was called with. Reading it twice could price a
            # row against one model and label it with another.
            matching_model_used,
            # WAS A LITERAL "ncbi/MedCPT-Cross-Encoder" (pass 20f-2). It is the
            # same fact as the checkpoint oncotriage/agent/deps.py loads, and a
            # row that names one model while Stage 3 ran another is a row that
            # cannot be reasoned about later. Note the asymmetry with
            # matching_model_used directly above, which is read off the Stage 5
            # RESPONSE rather than from config: the API can answer with a dated
            # snapshot of the model it was asked for, so there the request and
            # the answer are two different facts. The cross-encoder runs in this
            # process, so what was asked for IS what ran.
            CROSS_ENCODER_MODEL,
            PRICING_CONFIG["last_updated"],
            total_cost,
            result.get("qdrant_collection", ""),
            result.get("error", ""),
            result.get("patient_data_hash", ""),
            result.get("expansion_prompt", ""),
            # Written by all three terminal nodes via _pipeline_provenance()
            # (File 13). Reading "gpt4o_retries_exhausted" here logged 0 for
            # every run that did not end in node_error_handler, because that
            # node was the only writer of the old key.
            result.get("llm_classifier_retries", 0),                  # llm_classifier_retries
            json.dumps(result.get("ablation_flags") or {}),  # ablation_flags
            # No default, and 0 is now a real value rather than an unreached
            # one: Stage 5's detector writes the key on its success return, so
            # NULL here means the check did not complete. See the migration
            # note above.
            result.get("hallucinated_trials"),               # hallucinated_trials
            # Degradation record. Every one of these is .get() with no default,
            # so a result dict that never reached the stage in question writes
            # NULL rather than a value that would read as "checked, all clean".
            # retrieval_channels is serialized only when present: json.dumps(None)
            # would store the string 'null', which is not the same as SQL NULL.
            (json.dumps(result["retrieval_channels"])
             if result.get("retrieval_channels") else None),
            result.get("retrieval_channels_expected"),
            result.get("retrieval_channels_ok"),
            result.get("retrieval_degraded"),
            result.get("retrieval_trials_lost"),
            result.get("query_expansion_path"),
            # bool -> 0/1 for SQLite, but None stays None: "the filter did not
            # report" is a third state and must not collapse into "did not run".
            (None if result.get("mesh_filter_applied") is None
             else int(bool(result["mesh_filter_applied"]))),
            result.get("mesh_filter_skip_reason"),
            # The four other Stage 4 filters, same three-state treatment. The
            # bool -> 0/1 conversion is written out per column rather than
            # looped, because the VALUES tuple is positional and a loop here
            # would put the column order in two places.
            (None if result.get("stage_filter_applied") is None
             else int(bool(result["stage_filter_applied"]))),
            result.get("stage_filter_skip_reason"),
            (None if result.get("histology_filter_applied") is None
             else int(bool(result["histology_filter_applied"]))),
            result.get("histology_filter_skip_reason"),
            (None if result.get("age_filter_applied") is None
             else int(bool(result["age_filter_applied"]))),
            result.get("age_filter_skip_reason"),
            (None if result.get("sex_filter_applied") is None
             else int(bool(result["sex_filter_applied"]))),
            result.get("sex_filter_skip_reason"),
            # The one-glance marker. No default: a result dict that did not
            # come from a terminal node reports NULL, which is the honest
            # value -- nothing is known about whether that run was degraded.
            # int(bool(...)) rather than the raw value so a caller handing back
            # True/False stores 1/0 like every other flag column.
            (None if result.get("degraded_run") is None
             else int(bool(result["degraded_run"]))),
            # Age provenance. The reference date comes from the result, written
            # by _pipeline_provenance() (File 13) on all three terminal paths;
            # it falls back to the patient dict only for a caller that logs a
            # result it did not get from the graph. Both stay NULL when neither
            # reported: the age in this row is then not reproducible, and that
            # must not read as "computed against today".
            (result.get("age_reference_date")
             or demographics.get("age_reference_date")),
            (result.get("birth_date_precision")
             or demographics.get("birth_date_precision")),
            # ECOG. Resolved above, outside the tuple, because the value needs an
            # `is None` test rather than the `or` chain used for the age columns:
            # `or` would treat a legitimate ECOG 0 -- fully active, the most
            # eligible a patient can be -- as absent.
            ecog_value,
            ecog_selection,
            ecog_observations_found,
            ecog_date,
            # Stage 5 truncation record. The three counts default to 0 because
            # a run that ended before Stage 5 genuinely performed zero splits
            # and lost zero trials to truncation; the ESTIMATE has no default,
            # because a run that never estimated anything did not estimate 0.
            result.get("llm_classifier_truncation_splits", 0),
            result.get("llm_classifier_output_tokens_estimated"),
            result.get("not_evaluable_truncated", 0),
            result.get("llm_classifier_calls", 0),
            # No default. A response that carried no reasoning breakdown, and a
            # response that spent zero reasoning tokens, are different facts;
            # .get() with no default stores NULL for the first and 0 for the
            # second. Defaulting to 0 here would make every GPT-4o-era row and
            # every stubbed run look like a reasoning run that did no thinking.
            result.get("llm_classifier_reasoning_tokens"),
            # Which Stage 5 system prompt produced this row. Neither is
            # defaulted: a result dict that did not come from a pipeline
            # terminal node reports NULL for both, which is honest -- nothing
            # is known about which template it used. Note that the two NULLs
            # are read differently once a terminal node HAS written them; see
            # the migration comment above.
            result.get("llm_classifier_prompt_version"),
            result.get("llm_classifier_prompt_sha256"),
            # No default, on the hash's argument and with the hash's meaning: a
            # run that rendered no system message measured no record, and 0
            # would be indistinguishable from a genuinely empty record.
            result.get("llm_classifier_patient_record_tokens"),
            # --- The Stage 5 packing and cache record ----------------------
            #
            # NO DEFAULT ON ANY OF THE FOUR. Each is a MEASUREMENT that this
            # run either made or did not, and the migration comment above says
            # what NULL means per column. A default of 0 would assert one
            # request that cached nothing on a run that made no request, and a
            # default of {} would assert a packing report that describes no
            # run.
            #
            # THE CACHED FIGURE IS NOT SUBTRACTED FROM THE INPUT FIGURE AND IS
            # NOT PRICED. It is a subset of llm_classifier_input_tokens, which
            # get_model_cost() above already charged in full at the uncached
            # rate, deliberately -- see the migration comment. Anyone folding
            # this into a cost expression is re-basing the whole series.
            result.get("llm_classifier_cached_input_tokens"),
            # `is not None`, NOT truthiness, and the difference is the whole
            # point of the column. retrieval_channels above tests truthiness
            # because {} and None mean the same thing there -- Stage 2 either
            # reported per-channel status or it did not. Here they do not: []
            # is "the node ran and no call produced a usage object", which is
            # a fact about a run that was attempted, and None is "the node was
            # never entered". json.dumps(None) is the string 'null', which is
            # neither, and is the specific trap this expression avoids.
            (json.dumps(result["llm_classifier_call_details"])
             if result.get("llm_classifier_call_details") is not None else None),
            result.get("llm_classifier_packed_chunks"),
            # Same rule. A packing report is a dict and an empty one would be
            # a packer that reported nothing rather than an absent packer, so
            # the test is on presence and never on emptiness.
            (json.dumps(result["llm_classifier_packing"])
             if result.get("llm_classifier_packing") is not None else None),
            # ── The two OUTPUT guard denominators ──────────────────────
            #
            # PLAIN `.get()` WITH NO DEFAULT, which is
            # llm_classifier_output_tokens_estimated's rule immediately above
            # and not the truncation counters'. Both are CONFIGURATION the run
            # was judged against rather than work that either happened or did
            # not, so a run that never entered Stage 5 has no value for them and
            # a 0 supplied here would assert a ceiling of zero. Stage 5 writes
            # both on every one of its five returns, so a NULL pair means the
            # node was never entered -- or the row predates these columns.
            result.get("llm_classifier_output_split_threshold"),
            result.get("llm_classifier_output_ceiling"),
            # ── The two INPUT guard figures ────────────────────────────
            #
            # THE SAME RULE, one guard over: plain `.get()` with no default,
            # because both are measurements/configuration a run either recorded
            # or did not. A 0 supplied here would assert a request carrying
            # nothing and a configured budget of zero, and every pressure ratio
            # built on the pair would be a division by zero. Stage 5 writes
            # both on every one of its six returns, so a NULL pair means the
            # node was never entered -- or the row predates era 6.
            result.get("llm_classifier_input_tokens_estimated"),
            result.get("llm_classifier_input_budget"),
            # FROM CONFIG, NOT FROM `result` -- see the column's note in
            # INFERENCE_COLUMN_ADDITIONS. Reading it here rather than off the
            # result dict is what makes it unconditional: every row this writer
            # produces carries it, including the no-candidates rows and the
            # Stage 5 failure returns, which are exactly the rows a provider
            # migration needs to be able to attribute.
            # READ LIVE OFF THE MODULE, NOT AS A BOUND NAME. A
            # `from oncotriage.config import MATCHING_PROVIDER` at the top of
            # this file BINDS the value into this module's namespace at import,
            # so a caller that later sets `config.MATCHING_PROVIDER` -- the go-
            # live probe, a test, an operator flipping it in a REPL -- would
            # reach nothing and every row would record the value the process
            # started with. That is the patch-point defect
            # tests/test_agent_rrf_config_ownership.py exists for, and it
            # applies to any constant that can move WITHIN a process. The other
            # config names imported above cannot: CROSS_ENCODER_MODEL and
            # MATCHING_MODEL identify models that are fixed for a run.
            _config.MATCHING_PROVIDER,
            # SAME SEAM, ONE STEP FURTHER: a FUNCTION on the config module
            # rather than a constant read off it. matching_call_mode() is the
            # single owner of "which mode is this", and
            # oncotriage/agent/evaluation.py partitions the batch from that
            # same function -- so this column and the node cannot disagree
            # about the run they are both describing. Reading
            # MATCHING_PER_TRIAL_CALLS_ENABLED here instead would be a second
            # interpretation of one constant, free to drift from the first the
            # moment the vocabulary gains a member.
            _config.matching_call_mode(),
            # --- What Stage 5's normalizer corrected, per run ---------------
            #
            # NO DEFAULT ON EITHER, which is hallucinated_trials' rule two lines
            # of this tuple away and not the truncation counters'. Both describe
            # a CHECK, so 0 is a claim only a completed normalizer is entitled
            # to make; a run that ended at node_no_candidates, at an API
            # failure, at a refusal or at an unparseable answer carries no key
            # and must reach the column as NULL. A `, 0` here would report a
            # clean audit that was never performed.
            #
            # FROM `result`, NOT FROM CONFIG, and the asymmetry with
            # matching_provider immediately above is the point: that is a fact
            # about the PROCESS and lands on every row, these are facts about
            # HOW FAR THIS RUN GOT and must not.
            result.get("verdict_normalizations"),
            result.get("remapped_trials"),
            # --- WHICH RECORDED RUN THIS ROW BELONGS TO --------------------
            #
            # FROM THE ARGUMENT, NOT FROM `result`, and the asymmetry with the
            # two lines above is the same one `matching_provider` already
            # carries a few lines up: those are facts about how far THIS
            # PATIENT'S run got and are read off the result dict; this is a
            # fact about the PROCESS that is writing, and the pipeline result
            # knows nothing about it. Reading it off `result` would also let a
            # model response, a fixture or a hand-built dict set it.
            run_id,
        ))
        
        inference_id = cursor.lastrowid
        
        # not_evaluable trials are written too, with eligible = "not_evaluable",
        # so the criterion-level record exists for anything that reads back the
        # non-evaluations rather than only their count.
        all_trials = (
            result.get("matches", [])
            + result.get("near_misses", [])
            + result.get("not_evaluable", [])
        )

        for match in all_trials:
            # Build criterion details JSON from inclusion/exclusion arrays
            inclusion = match.get("inclusion_criteria", [])
            exclusion = match.get("exclusion_criteria", [])
            inclusion = inclusion if isinstance(inclusion, list) else []
            exclusion = exclusion if isinstance(exclusion, list) else []
            # STILL EXACTLY TWO KEYS. What changed at this pass is inside the
            # arrays, not here: a criterion whose status Stage 5 rewrote now
            # carries `remapped_from_status` beside its corrected `status`,
            # written by _normalize_arm at the line that rewrites it. The model
            # cannot supply that key -- CRITERION_FIELDS is (criterion,
            # patient_value, status) with additionalProperties: false -- so a
            # row carrying it was rewritten here, and a row without it was not.
            # Absent rather than empty on every other row, which is
            # TEMPORAL_CONFLICT_FIELD's convention in the same arrays.
            criterion_json = json.dumps({
                "inclusion":       inclusion,
                "exclusion":       exclusion,
            })
            
            cursor.execute('''
                INSERT INTO trial_matches (
                    inference_id, nct_id, trial_title, trial_phase,
                    trial_number, rerank_score, rerank_score_raw, mesh_boost, mesh_boost_tier,
                    match_score, eligible, assessment, criterion_details,
                    score_confirmed, score_denominator, criteria_not_applicable,
                    hallucinated, criteria_split, emission_index, call_index,
                    not_evaluable_reason, verdict_source,
                    verdict_original_label, verdict_original_type,
                    criterion_remaps
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                inference_id,
                match.get("nct_id", ""),
                match.get("title", ""),
                match.get("phase", ""),
                match.get("trial_number"),
                match.get("rerank_score"),
                match.get("rerank_score_raw"),
                match.get("mesh_boost"),
                match.get("mesh_boost_tier"),
                match.get("match_score", 0.0),
                match.get("eligible", "not_eligible"),
                # WHAT THIS COLUMN MEANS CHANGED AT PROMPT_VERSION 1.5.0, and
                # nothing about the write did. For an "eligible" or a
                # "not_eligible" trial this is no longer the model's free-written
                # draft: it is a rendering COMPOSED by
                # oncotriage/agent/evaluation.py:compose_assessment out of the
                # criterion / patient_value / status rows stored beside it in
                # criterion_details, so the two can no longer contradict each
                # other. For a "not_evaluable" trial it is still the model's own
                # text, because a trial the model declared not evaluable has
                # empty arrays by contract and there is nothing to compose from.
                #
                # ONE not_evaluable POPULATION IS COMPOSED, and a reader of this
                # column has to know it exists: a rejection the model wrote with
                # no disqualifying row to support it is corrected to
                # "not_evaluable" by Stage 5 (see
                # UNEVALUABLE_REJECTION_UNSUPPORTED) with both arrays kept as
                # evidence, and its assessment is
                # ASSESSMENT_UNSUPPORTED_REJECTION_TEXT -- a fixed sentence
                # opening "Not evaluable:" that says the model rejected the
                # trial while citing no disqualifying criterion. It USED TO BE
                # the one value in this column that identifies the correction,
                # because `not_evaluable_reason` was not a column here; as of
                # the provenance pass that column exists and carries the reason
                # directly, so a reader identifies the correction by
                # `not_evaluable_reason` and no longer by matching prose. Until
                # composition existed the row stored the model's rejection
                # draft, opening "Known disqualifier:" beside a verdict that is
                # not a rejection and criteria carrying none: a row that
                # contradicted itself in the column a clinician reads. Rows
                # written before it still do. The draft is NOT stored here
                # and gets no column of its own; it survives in
                # inferences.llm_classifier_raw_response only for a run that
                # made ONE call, because that column is assigned per chunk
                # rather than appended (see the block above compose_assessment
                # in oncotriage/agent/evaluation.py).
                # A row written before 1.5.0 carries the draft.
                match.get("assessment", ""),
                criterion_json,
                match.get("score_confirmed"),
                match.get("score_denominator"),
                match.get("criteria_not_applicable"),
                # 0 when Stage 5's out-of-set detector checked this row, NULL
                # when it never ran. 1 is unreachable: see the migration note.
                match.get("hallucinated"),
                # NO DEFAULT, for the reason its column note gives: the value
                # is copied through from the trial's own full_trial_json, so a
                # missing key means the indexed trial carried no such field.
                # A `, "unsplit"` or a `, ""` here would assert a split method
                # for a trial nobody measured one for, which is exactly the
                # reading this column exists to make possible.
                match.get("criteria_split"),
                # NO DEFAULT ON EITHER, which is the whole point. A pipeline-
                # constructed entry carries an explicit None and an entry from a
                # result dict built outside the pipeline carries no key at all;
                # both must reach the column as NULL. A `, 0` here would assert
                # that every such trial stood first in the first call's answer.
                match.get("emission_index"),
                match.get("call_index"),
                # --- What Stage 5's normalizer did to THIS trial ------------
                #
                # NO DEFAULT ON ANY OF THE FIVE, for emission_index's reason
                # immediately above. An entry the pipeline CONSTRUCTED carries
                # none of them and an entry from a result dict built outside the
                # pipeline carries none either; both must reach their columns as
                # NULL. A `, 0` on criterion_remaps would assert that the
                # normalizer read a trial it never saw and found it clean, and a
                # `, ''` on the two label columns would store a reason and an
                # original of zero characters, neither of which is a reading.
                #
                # verdict_source IS PRESENT ON EVERY MODEL-RETURNED ROW,
                # 'canonical' included, so NULL here selects exactly the
                # constructed / out-of-pipeline / pre-migration population --
                # the same one emission_index and call_index select, which is
                # what lets a reader cross-check the three.
                match.get("not_evaluable_reason"),
                match.get("verdict_source"),
                match.get("verdict_original_label"),
                match.get("verdict_original_type"),
                match.get("criterion_remaps"),
            ))
        
        conn.commit()
        console.out(f"✓ Logged inference for patient {result['patient_id']} (ID: {inference_id})")
        outcome = {"ok": True, "error": None, "exception": None,
                   "attempts": 1, "inference_id": inference_id}

    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        console.out(f"⚠ Database logging failed (non-critical): {e}")
        # DO NOT re-raise - logging failure should not break pipeline
        #
        # BUT DO REPORT IT. Before the write-durability pass this handler ended
        # here and log_inference returned db_path exactly as on success, so
        # "the row is stored" and "the row is gone" were the same answer to the
        # caller. The outcome below is what makes them different; the retry
        # decision on top of it is _write_inference_row_with_retry's.
        outcome = {"ok": False, "error": f"{type(e).__name__}: {e}",
                   "exception": e, "attempts": 1, "inference_id": None}

    except Exception as e:
        if conn:
            conn.rollback()
        console.out(f"⚠ Logging error (non-critical): {e}")
        # DO NOT re-raise - logging failure should not break pipeline
        outcome = {"ok": False, "error": f"{type(e).__name__}: {e}",
                   "exception": e, "attempts": 1, "inference_id": None}

    finally:
        if conn:
            conn.close()

    # RETURNED HERE, AFTER the finally and never inside it. A `return` inside a
    # finally block swallows any exception propagating out of the try -- and
    # three are meant to propagate: KeyboardInterrupt, SystemExit and
    # GeneratorExit, the only exceptions that are not Exception subclasses and
    # so the only ones the handlers above do not catch. Returning here leaves
    # them escaping exactly as they did before this pass.
    #
    # THE OLD WORDING SAID "two ... (KeyboardInterrupt, MemoryError)" AND WAS
    # WRONG ABOUT THE SECOND. issubclass(MemoryError, Exception) is True, so an
    # out-of-memory write is caught above and recorded as terminal. The
    # placement is unchanged and is still correct; only the list of what it
    # protects was.
    return outcome


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 12 13:26:56 2026
@author: ramyalsaffar
"""
