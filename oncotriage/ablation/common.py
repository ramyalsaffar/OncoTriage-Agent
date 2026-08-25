"""What the ablation writer and the ablation reader both need (pass 20f-4).

THIS MODULE EXISTS TO BREAK A CYCLE, and the cycle is real rather than stylistic.
``oncotriage/ablation/analysis.py`` calls the nine figure functions from
``main()``, so it must import ``oncotriage/ablation/figures.py`` at MODULE scope
-- ``tests/test_package_invariants.py`` check 1b forbids a package import inside
a function body, because a deferred import is a dependency no scan of an import
block can see. The figures in turn need ``CONFIG_ORDER``, ``CONFIG_LABELS``,
``BASELINE`` and ``output_dir()``, all of which used to live in ``analysis``.
Importing them back from ``analysis`` would be an import cycle. They live here,
and both modules import DOWN into this one; nothing here imports either.

IT ALSO ENDS A DUPLICATED FILENAME AND A DUPLICATED GUARD (item 6 of the pass).
``analysis.ablation_db()`` took no argument and HARDCODED the string
``"ablation_results.db"`` while ``study.ablation_db(db_path)`` took a path and
read ``ABLATION_DB_FILENAME``. Two consequences, both of which this closes:

  * a study written with ``--db`` could not be ANALYSED -- there was no way to
    point File 27 at it, so the isolation ``--db`` bought for the writer stopped
    at the writer;
  * the filename existed as a constant AND as a literal, which is the shape
    pass 20f-2 removed for the MedCPT checkpoint and pass 20c-3a removed for the
    BM25 sparse model. Nothing fails when the two disagree; the reader simply
    reports on a database the writer is not filling.

``_require_writable_parent`` moved here for the same reason rather than being
reimplemented: pass 20f-3 built it, argued it, and gave it a message that names
the directory. A second copy in the reader is a second copy to drift. Its
``example_command`` argument is the ONLY thing added -- File 26's message is
byte-identical to what pass 20f-3 shipped, because that is its default, and File
27's names File 27.

WHAT DELIBERATELY DID NOT MOVE. ``study.ablation_db()`` and
``study.ablation_summary_json()`` keep their own bodies and their own
``_RESOLVED`` cache. ``tests/test_ablation_db_isolation.py`` installs a decoy
into ``study._RESOLVED`` by name -- that is the mechanism its whole
"the default was not touched" argument rests on -- and a study function
delegating to this module's cache would leave that decoy unread and the test
passing for the wrong reason.
"""

import os
import sqlite3
import threading
from pathlib import Path

from oncotriage import paths


#------------------------------------------------------------------------------


# ===========================================================================
# FILENAMES
# ===========================================================================
#
# ONE definition each. study.py imports both and analysis.py imports the first;
# neither writes the string again.

ABLATION_DB_FILENAME = "ablation_results.db"
ABLATION_SUMMARY_FILENAME = "ablation_summary.json"


# ===========================================================================
# CONFIG VOCABULARY
# ===========================================================================
#
# The display order and labels every table, figure and report shares. Moved out
# of analysis.py unchanged, including the order, which is load-bearing: every
# figure reindexes on CONFIG_ORDER and a reordering silently re-labels bars.

# Config display order (matches File 26 ABLATION_CONFIGS)
CONFIG_ORDER = [
    "full_pipeline",
    "no_mesh_filter",
    "no_stage_filter",
    "no_histology_filter",
    "no_cross_encoder",
    "bm25_only",
    "vector_only",
]

CONFIG_LABELS = {
    "full_pipeline":       "Full Pipeline (baseline)",
    "no_mesh_filter":      "− MeSH Filter",
    "no_stage_filter":     "− Stage Filter",
    "no_histology_filter": "− Histology Filter",
    "no_cross_encoder":    "− Cross-Encoder",
    "bm25_only":           "BM25 Only",
    "vector_only":         "Vector Only",
}

BASELINE = "full_pipeline"


# ===========================================================================
# LAZY PATHS
# ===========================================================================
#
# File 27 wrote these two as module-level expressions over result_ablation_path.
# Importing this module would then have resolved a sibling directory, which is
# the one thing every module in this package is forbidden to do at import: a
# wheel install, a CI checkout of "03- Code" alone or a container built before
# its data volume is mounted has no such tree, and the failure is an ImportError
# from a module that was only meant to be read.
#
# Locked for the same reason oncotriage/fhir/clean.py locks its three: `if k not
# in d: d[k] = build()` is two atomic operations and one non-atomic sequence.
# Nothing here runs multi-threaded today; the lock is about the pattern being
# copied when a third accessor is added.

_RESOLVED = {}
_RESOLVE_LOCK = threading.RLock()


def _require_writable_parent(path: Path,
                             example_command='python "26- Ablation Study.py" '
                                             '--db /tmp/ablation/results.db') -> Path:
    """Refuse an explicit database path whose parent directory is missing.

    TWO DATABASE WRITERS, ONE BEHAVIOUR (pass 20f-3). ``--db /nowhere/x.db``
    used to reach ``sqlite3.connect`` and come back as

        sqlite3.OperationalError: unable to open database file

    which names nothing: not the path, not the flag, not the directory. It
    surfaces at the first ``init_ablation_db()`` call, after the argument
    parsing and the banner, so the operator sees a study that started and then
    died on a message about "database file".

    ``settings.resolve_inferences_db()`` had already settled the shape for the
    OTHER redirectable database, and its argument transfers unchanged: a
    database FILE that does not exist is the normal case -- sqlite creates it --
    but a missing PARENT is a configuration defect, and the check is worth
    making eagerly and loudly. There the risk was a swallowed
    ``OperationalError`` reported as one non-critical logging failure per
    patient; here nothing swallows it, and the cost is a bare exception instead
    of an instruction. Both are the same defect and both now name the directory.

    NOT APPLIED TO THE DEFAULT, deliberately. ``db_path=None`` resolves
    ``paths.result_ablation_path``, which ``_glob_one`` has already proved
    exists -- it raises naming the pattern when nothing matches. Checking it
    again here would only be able to fail on a directory deleted between the
    glob and the connect.

    ``~`` is expanded, for the reason ``resolve_inferences_db`` expands it:
    ``--db ~/scratch.db`` is a plausible thing to type, and a shell that does
    not expand it (it will not, after ``=``, in some shells and in most
    programmatic invocations) leaves a path inside a directory literally named
    "~".

    PASS 20f-4 ADDED ``example_command`` AND NOTHING ELSE. File 27 reuses this
    guard rather than carrying a second copy of it, and an operator who typed
    ``--db`` at the ANALYSIS is not helped by an example that names the study.
    The default is byte-identical to the message pass 20f-3 shipped, so File
    26's diagnostic did not move.
    """
    expanded = Path(os.path.expanduser(str(path)))
    parent = expanded.expanduser().resolve().parent
    if not parent.is_dir():
        raise RuntimeError(
            f"--db points into a directory that does not exist: {str(parent)!r} "
            f"(from {str(path)!r})\n"
            f"Create the directory, or give --db a path whose parent exists, "
            f"e.g.\n"
            f"    {example_command}\n"
            f"Without this check sqlite3 raises 'unable to open database file', "
            f"which names neither the path nor the flag."
        )
    return expanded


# The example command in the parent-directory refusal. `output_dir` and
# `ablation_db` below are the ANALYSIS side of the pair -- study.py keeps its
# own two accessors -- so both name File 27. study.py reaches
# _require_writable_parent directly and gets the File 26 default.
_ANALYSIS_DB_EXAMPLE = ('python "27- Ablation Analysis.py" '
                        '--db /tmp/ablation/results.db')


def output_dir(db_path=None) -> Path:
    """Where every table, figure and report is written. Creates nothing.

    File 27 did not create this directory either -- it wrote into whatever
    ``result_ablation_path`` globbed to, and a missing directory surfaced as an
    OSError from the first ``to_csv``. Adding a mkdir here would be a behaviour
    change dressed as a fix, and ``oncotriage/fhir/explore.py`` already carries
    the argument for keeping resolution and creation separate.

    THE OUTPUTS FOLLOW THE DATABASE (pass 20f-4), on exactly the argument
    ``study.ablation_summary_json()`` already made: an artifact that describes
    one database must not be written beside another. With ``--db`` the tables,
    figures and reports land in that database's directory; with ``None`` they
    land exactly where they always did.
    """
    if db_path is not None:
        return _require_writable_parent(
            Path(db_path), example_command=_ANALYSIS_DB_EXAMPLE).parent

    with _RESOLVE_LOCK:
        if "output_dir" not in _RESOLVED:
            _RESOLVED["output_dir"] = Path(paths.result_ablation_path)
        return _RESOLVED["output_dir"]


def ablation_db(db_path=None) -> Path:
    """The database File 26 wrote. READ ONLY from the analysis side.

    Args:
        db_path: ``None`` -- the default and what every documented command
            produces -- means the production ``ablation_results.db`` under
            ``result_ablation_path``, resolved on first call and cached.

            AN EXPLICIT ARGUMENT IS RETURNED AS GIVEN AND IS NEVER CACHED, the
            same rule ``study.ablation_db`` and
            ``storage.database_logger.resolve_inference_db_path`` follow: the
            cache answers "where does this machine keep the study database",
            which is a fact about the machine, while an argument is a fact about
            one call.

    Raises:
        RuntimeError: an explicit ``db_path`` whose PARENT DIRECTORY does not
            exist. See ``_require_writable_parent``.
    """
    if db_path is not None:
        return _require_writable_parent(Path(db_path),
                                        example_command=_ANALYSIS_DB_EXAMPLE)

    with _RESOLVE_LOCK:
        if "ablation_db" not in _RESOLVED:
            _RESOLVED["ablation_db"] = Path(paths.result_ablation_path) / ABLATION_DB_FILENAME
        return _RESOLVED["ablation_db"]


class MissingAblationDatabaseError(RuntimeError):
    """There is no ablation database at that path, and no reader will make one.

    A ``RuntimeError`` subclass and deliberately NOT a ``sqlite3.Error``, on
    ``oncotriage.storage.queries.MissingDatabaseError``'s footing and for the
    same reason: both readers here sit under callers with broad handlers, and a
    refusal those could swallow would be reported as an empty study.
    """


def open_ablation_db_readonly(db_path=None):
    """A READ-ONLY connection to the ablation database. The caller closes it.

    THE ONE OWNER of "open the study database to read it", for both readers --
    ``oncotriage/ablation/analysis.py``'s two loaders and
    ``oncotriage/ablation/study.py``'s two summary readers. It is in this module
    rather than in either of them for the reason this module exists: they both
    need it, and ``analysis`` importing ``study`` would drag the thread pool,
    the graph and the agent into a process that only wants to read a table.

    WHY READ-ONLY. ``sqlite3.connect(path)`` CREATES an empty database when the
    path does not exist, so `--db` pointed one directory wrong did not fail: it
    brought a database into existence and the analysis then reported a study
    with no rows, which is indistinguishable from a study that produced none.
    `load_ablation_data` guarded against that with an ``exists()`` check and
    ``load_error_data`` -- called immediately after it -- did not, so the guard
    covered one of the two paths through the same file.

    IT ALSO MAKES THE MODE MATCH ``ablation_db``'s OWN DOCSTRING, which has said
    "READ ONLY from the analysis side" since it was written and had nothing
    enforcing it.

    The ``?`` escaping and the WAL limit are
    ``oncotriage.storage.queries.connect``'s, documented there rather than
    re-argued: a URI's query string starts at the first literal question mark,
    and a WAL database whose ``-shm`` is missing cannot be opened read-only
    inside a directory that is not writable.
    """
    resolved = ablation_db(db_path)
    if not os.path.isfile(resolved):
        raise MissingAblationDatabaseError(
            f"No ablation database at {str(resolved)!r}.\n"
            f"    Readers do not create one -- an empty database would report a "
            f"study with no results, which reads exactly like a study that "
            f"produced none.\n"
            f"    Run the study first (python '26- Ablation Study.py'), or "
            f"point --db at the file you meant.")
    uri = ("file:" + os.path.abspath(str(resolved)).replace("?", "%3f")
           + "?mode=ro")
    return sqlite3.connect(uri, uri=True)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
