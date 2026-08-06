"""Every filesystem location the pipeline reads or writes.

Moved out of ``01- Imports.py`` by item 20c. ``01- Imports.py`` keeps its
third-party import block verbatim — Files 04 to 46 rely on those names being in
the shared exec namespace — and re-exports everything below.

Imports ``oncotriage.settings`` and nothing else from the project. ``settings``
must never import this module in either direction — at module scope OR inside a
function body — because ``paths`` reads it while resolving the root.

``load_env_keys`` LIVES HERE as of pass 20c-2a. Pass 20c-1 put it in
``settings`` and reached ``keys_path`` through an import deferred into the
function body; that worked, but a deferred import is a dependency no static scan
of the import block can see, and the whole point of the package split is an
import graph that can be read. It is here instead, beside the ``keys_path`` it
defaults to, and the deferral is gone. ``config`` imports ``paths`` and
``settings`` and still never imports ``utils``, so the original cycle stays
broken.

RESOLUTION IS LAZY AS OF PASS 20c-2b, and that is a correction, not a polish
-----------------------------------------------------------------------------
Until this pass every path was a module-level assignment, so IMPORTING this
module resolved the whole sibling directory tree and RAISED if any part of it
was absent. ``oncotriage.config`` imports this module for ``load_env_keys``, so
``import oncotriage.config`` inherited that: on a machine without the sibling
tree — a wheel installed into a fresh environment, a CI checkout of ``03- Code``
alone, a container built before the data volume is mounted — importing the
config module to read ``MAX_WORKERS`` died with a RuntimeError about a directory
pattern that matched nothing. A configuration module that cannot be imported
without the data tree beside it is not importable in any useful sense.

Every path below is therefore resolved on FIRST ATTRIBUTE ACCESS and cached,
through the module-level ``__getattr__`` that PEP 562 added in Python 3.7.
Consumers change nothing: ``from oncotriage.paths import data_fhir_path`` and
``paths.inferences_path`` both go through ``__getattr__`` and both still get a
plain string. ``01- Imports.py`` imports all sixteen names by name, so the exec
chain resolves the tree exactly as eagerly as it always did — the laziness is
for everyone who is NOT the exec chain.

What is NOT lazy, and must not be:

  * ``IS_DOCKER`` — one ``os.path.exists`` and one environment read, no glob,
    cannot raise;
  * ``path_settings`` / ``_load_path_settings`` — an import;
  * ``_glob_one`` — a function. It reads the root through ``_resolve()`` rather
    than through a module global, because a module-level ``__getattr__`` is
    consulted for attribute access on the MODULE and not for a global name
    lookup inside a function body. ``main_path`` written bare in here would be a
    NameError, not a lazy read. Its no-match message is unchanged since item
    20c; pass 20f-1 added a SECOND message, for a pattern that matches more
    than one directory, and the block above the function argues why that raises
    rather than picking a winner;
  * ``REQUIRED_ENV_KEYS`` / ``load_env_keys`` — data and a function. Importing
    ``load_env_keys`` triggers no resolution at all; CALLING it with no argument
    resolves ``keys_path`` and nothing else.

WHAT ``__getattr__`` DOES TO ``hasattr``, and it is not the usual thing
-----------------------------------------------------------------------
``hasattr(paths, name)`` is defined as "``getattr`` did not raise
``AttributeError``". Two of the three outcomes here behave normally; the third
does not, and callers have to know which:

  * a name this module binds eagerly (``IS_DOCKER``, ``load_env_keys``) ->
    ``True``, no resolution;
  * a name it does not know at all -> ``__getattr__`` raises ``AttributeError``
    -> ``False``, no resolution;
  * a name in ``PATH_NAMES`` -> the resolver RUNS. On a healthy tree that is
    ``True``. On a broken one the resolver raises ``RuntimeError``, and Python
    does NOT convert that to ``AttributeError``, so ``hasattr`` PROPAGATES it
    instead of returning ``False``.

That last case is deliberate: a wrong project root must fail where it is read,
loudly, naming the variable to set. But it means ``hasattr`` is the wrong tool
for asking "does this module expose this name" — the question has an answer even
when the tree is missing, and ``hasattr`` cannot give it. Ask ``name in
PATH_NAMES or hasattr(...)`` instead, which is what ``tests/test_package_invariants.py``
does; it used to call ``hasattr`` on all sixteen and would have aborted rather
than reported on any checkout without the sibling directories.

IMPORT-TIME SIDE EFFECTS, stated because they are what is left. Importing this
module now:

  * imports ``oncotriage.settings`` and ``dotenv``;
  * prints one line naming the settings module.

It opens no socket, loads no model, touches no database, reads no file and
resolves no directory. ``tests/test_package_invariants.py`` section 2b imports it with
the project root pointed at a directory that does not exist and requires the
import to succeed and the first path READ to raise.
"""

import glob
import os
import threading

from dotenv import load_dotenv

from oncotriage import settings as path_settings


# Detect if running in Docker container
IS_DOCKER = os.path.exists('/.dockerenv') or os.getenv('DOCKER_CONTAINER', 'false').lower() == 'true'


def _load_path_settings():
    """Return (settings module, its directory).

    COMPATIBILITY SHAPE. Before item 20c this function searched three candidate
    directories for ``oncotriage_settings.py`` and loaded it by file location,
    because ``01- Imports.py`` is ``exec()``'d as often as it is run and could
    not rely on ``sys.path``. A package module has no such problem: the import
    above is the whole resolution.

    Kept, and kept returning the same 2-tuple, because ``01- Imports.py``
    defined the name and this pass drops no name that file defined. It has one
    caller today and that caller is the shim.
    """
    return path_settings, os.path.dirname(os.path.abspath(path_settings.__file__))


print(f"[Paths] Settings module loaded from {path_settings.__file__} "
      f"(via the oncotriage package)")


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Lazy resolution machinery
# ---------------------------------------------------------------------------
#
# _RESOLVED is the cache. A name lands in it exactly once, the first time
# anything reads the corresponding module attribute, and every later read is a
# dict hit. Nothing here is resettable: a process that resolved the tree once
# and then saw a different tree would produce two sets of paths in one run, and
# every path-derived artefact — the checkpoint, the manifest, the database —
# would be ambiguous about which one produced it.
#
# The cache stores the VALUE, so a resolver that raises is retried on the next
# read rather than remembered as failed. That matters for the one recoverable
# case: ONCOTRIAGE_MAIN_PATH set wrongly, corrected, and the read repeated in
# the same interactive session.
#
# THE LOCK, added in pass 20c-2c. Pass 2b shipped this cache unguarded on the
# argument that "01- Imports.py" imports all sixteen names at bootstrap, before
# any worker thread exists, so the first-read race is unreachable. That argument
# has an expiry date and pass 20c-3 is it: the twelve-worker batch runner and
# the FastAPI server are the next two files to convert, and the moment either
# resolves a path from a worker rather than from the exec chain, two threads can
# enter the same resolver together.
#
# What the race would actually cost is small and entirely avoidable: the globs
# are idempotent, so the worst outcome is duplicated work and a SECOND
# "[Paths] Project root" banner in the log, which reads as the process having
# resolved the tree twice. That is exactly the kind of quietly-wrong log line
# this project treats as a defect, and a lock is three lines.
#
# RLock, not Lock: _resolve() is re-entrant by construction. Reading
# `inferences_path` calls its resolver, which calls _resolve("data_path"), which
# calls _resolve("main_path"). A plain Lock would deadlock the first read of any
# derived path on a single thread.
#
# Double-checked: the fast path reads the dict WITHOUT taking the lock. A dict
# read is atomic under the GIL and a name that is present was written by a
# thread that had already finished resolving it, so a hit needs no
# synchronisation. Only a miss pays for the lock, and only once per name.

_RESOLVED = {}
_RESOLVE_LOCK = threading.RLock()


def _resolve(name):
    """Resolve one path name, caching it. Raises AttributeError for unknowns.

    Thread-safe: concurrent first reads of the same name resolve it once.
    """
    if name in _RESOLVED:
        return _RESOLVED[name]

    resolver = _RESOLVERS.get(name)
    if resolver is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        )

    with _RESOLVE_LOCK:
        # Re-checked inside the lock. Between the miss above and acquiring it,
        # another thread may have resolved this very name; without this line
        # both would run the resolver and both would print the banner, which is
        # the observable half of the race the lock exists to remove.
        if name in _RESOLVED:
            return _RESOLVED[name]

        value = resolver()
        _RESOLVED[name] = value
        return value


# glob.glob(pattern)[0] on its own raises IndexError, and an IndexError names
# neither the pattern that matched nothing nor the root it was anchored to.
# Every sibling directory in the local branch is discovered by prefix glob, so
# a wrong root produces one IndexError per run and no diagnosis.
#
# Defined outside the branch, not inside the local one, so that both branches
# leave the same set of names behind. The Docker branch does not call it; a
# name defined in one branch and not the other is the exact defect item 20a
# found in code_path.
#
# It reads the root through _resolve() rather than as a bare global: see the
# module docstring. By the time any caller reaches here the root is already
# cached, because every local resolver below globs off it.
#
# ---------------------------------------------------------------------------
# THE SECOND FAILURE MODE USED TO BE SILENT, AND PASS 20f-1 CLOSED IT
# ---------------------------------------------------------------------------
# Until this pass the body was `hits = glob.glob(pattern)` followed by
# `return hits[0]`, and the comment above admitted it: "same unsorted [0]".
# glob.glob DOES NOT SORT -- it returns os.scandir order, which on APFS is
# neither alphabetical nor stable across a rename, a restore, a copy or a
# different machine. So when two siblings matched one pattern, WHICH ONE THE
# PIPELINE USED WAS FILESYSTEM ORDER, decided silently, per machine, and liable
# to change without anything in the project changing. Determinism is a stated
# property here -- temperature 0, stable argsort, RESAMPLE_SEED -- and this was
# the one place a PATH resolved nondeterministically.
#
# MEASURED FIRST, on 2026-08-06: all FOURTEEN local call sites match exactly
# one directory on this machine (code, data, patients, FHIR bundle, trials,
# MeSH, inferences, results, FHIR exploration results, ablation results, keys,
# Airflow, requirements, checkpoint). So sorting changes no value that is being
# produced today and the raise below cannot fire today. A guard against a
# layout that does not exist yet is the only kind that can be added for free.
#
# WHY MORE THAN ONE MATCH RAISES rather than taking the sorted winner:
#
#   * It is item 11a's line, applied. A missing or ambiguous CONFIGURATION is
#     fixed by one command -- rename the stray sibling, or set the root
#     variable -- and every run afterwards is correct, so it raises. A
#     third-party DATA degradation that no operator can fix is counted instead.
#     Two sibling directories matching one prefix is configuration.
#   * The cost of guessing is not a degraded run but a confidently wrong one.
#     oncotriage/fhir/clean.py UNLINKS patient bundles out of whichever
#     "*Patients/" directory won; a wrong "*Data/" sends inferences.db, the
#     deletion manifest and the checkpoint into a tree nobody is reading, and
#     every one of those runs reports success.
#   * The alternative -- pick one and warn -- is weakest exactly where it
#     matters: one WARNING line at first read, inside a process that prints
#     hundreds of lines, on a run that then completes and produces numbers.
#   * The price is one failed run for an operator who genuinely added a second
#     matching sibling, with a message naming both directories and the two ways
#     out. That is cheap against a deleted corpus.
#
# THE MESSAGE STILL NAMES WHICH ONE WOULD HAVE WON, so the operator can see the
# ambiguity AND what the pre-20f-1 code would have handed the pipeline.
#
# WHAT sorted() BUYS, stated honestly: only that the DIAGNOSIS is deterministic.
# When exactly one directory matches, order cannot affect the answer; when more
# than one does, this function refuses rather than choosing. Sorting is what
# makes two machines meeting the same ambiguity print the same candidate list
# and name the same "would have resolved to" directory.
def _glob_one(pattern, label):
    hits = sorted(glob.glob(pattern))
    if not hits:
        raise RuntimeError(
            f"No directory matched the {label} pattern: {pattern!r}\n"
            f"Project root in use: {_resolve('main_path')!r} "
            f"(from {_resolve('_main_path_source')})\n"
            f"Set {path_settings.ENV_MAIN_PATH} if the root is wrong, or check "
            f"that the sibling directory exists and still ends in the expected suffix."
        )
    if len(hits) > 1:
        _candidates = "".join(f"  {hit}\n" for hit in hits)
        raise RuntimeError(
            f"{len(hits)} directories matched the {label} pattern: {pattern!r}\n"
            f"{_candidates}"
            f"Project root in use: {_resolve('main_path')!r} "
            f"(from {_resolve('_main_path_source')})\n"
            f"Before pass 20f-1 this would have resolved to {hits[0]!r} on this "
            f"machine, chosen by filesystem enumeration order rather than by "
            f"anything you could predict, and said nothing.\n"
            f"Rename or move the extra sibling so that exactly one directory "
            f"ends in the expected suffix, or set "
            f"{path_settings.ENV_MAIN_PATH} to a root where that is true."
        )
    return hits[0]


def _resolve_root():
    """Resolve main_path and _main_path_source together, and announce the branch.

    They are one resolver because they are one decision: the root and the
    provenance of the root. Splitting them would let a log record a root
    without saying where it came from, which is the thing item 20a added the
    source for.

    The branch banner is printed HERE rather than at import, because that is
    now where the branch is actually taken. A line saying which environment the
    paths came from, printed before any path has been resolved, would be
    describing a decision that had not been made yet.

    Idempotent, and it has to be: _resolve() caches per NAME, so a caller that
    reads _main_path_source first and main_path second would otherwise take the
    branch twice and print the banner twice. The guard is on both names
    together because this resolver writes both.
    """
    if "main_path" in _RESOLVED and "_main_path_source" in _RESOLVED:
        return _RESOLVED

    if IS_DOCKER:
        # Docker container paths (Linux environment)
        print("🐳 Running in Docker container")

        # Provenance of main_path, recorded in both branches so a reader of a
        # log can tell a container run from a local one without inferring it.
        _RESOLVED["_main_path_source"] = "Docker image layout (fixed)"
        _RESOLVED["main_path"] = "/app/"
    else:
        # Local development paths (macOS)
        print("💻 Running on local machine")

        main, source = path_settings.resolve_main_path()
        _RESOLVED["main_path"] = main
        _RESOLVED["_main_path_source"] = source
        print(f"[Paths] Project root: {main} (from {source})")

    return _RESOLVED


def _root():
    """main_path, resolving the root pair if it has not been resolved yet."""
    return _resolve_root()["main_path"]


def _root_source():
    return _resolve_root()["_main_path_source"]


# The Dockerfile does `COPY . /app/`, so the numbered scripts sit directly in
# /app. code_path was added to this branch in item 20a: the local branch has
# always defined it and this branch never did, so any file reaching for it was
# container-only broken.
#
# Fixed strings, not globs — nothing here can fail, and the branch is kept lazy
# only so that both branches expose the same names through the same mechanism.
_DOCKER_PATHS = {
    "code_path":                "/app/",
    "data_path":                "/app/data/",
    "data_patient_path":        "/app/data/patients/",
    "data_fhir_path":           "/app/data/patients/fhir/",
    "data_trial_path":          "/app/data/trials/",
    "inferences_path":          "/app/data/inferences.db",
    "results_path":             "/app/results/",
    "result_fhir_explore_path": "/app/results/fhir_exploration/",
    "result_ablation_path":     "/app/results/ablation/",
    "keys_path":                "/app/",
    "airflow_path":             "/app/airflow_home/",
    "requirements_path":        "/app/requirements/",
    "data_MeSH_path":           "/app/data/mesh/",
    "checkpoint_path":          "/app/checkpoint/",
}


# The local branch, one resolver per name, in the same order and with the same
# patterns and labels the module-level assignments used. Each one names its
# dependencies through _resolve(), so reading a single leaf path resolves that
# path's chain and nothing else: reading inferences_path globs the root, the
# data directory and the inferences directory, and never touches Airflow or
# Requirements.
_LOCAL_PATHS = {
    "code_path":
        lambda: _glob_one(_root() + "/*Code/", "code"),
    "data_path":
        lambda: _glob_one(_root() + "/*Data/", "data"),
    "data_patient_path":
        lambda: _glob_one(_resolve("data_path") + "/*Patients/", "patients"),
    "data_fhir_path":
        lambda: _glob_one(_resolve("data_patient_path"), "FHIR bundle") + "fhir/",
    "data_trial_path":
        lambda: _glob_one(_resolve("data_path") + "/*Trials/", "trials"),
    "data_MeSH_path":
        lambda: _glob_one(_resolve("data_path") + "/*MeSH/", "MeSH"),
    "inferences_path":
        lambda: _glob_one(_resolve("data_path") + "/*Inferences Storage/",
                          "inferences") + "inferences.db",
    "results_path":
        lambda: _glob_one(_root() + "/*Results/", "results"),
    "result_fhir_explore_path":
        lambda: _glob_one(_resolve("results_path") + "/*FHIR Exploration/",
                          "FHIR exploration results"),
    "result_ablation_path":
        lambda: _glob_one(_resolve("results_path") + "/*Ablation/",
                          "ablation results"),
    "keys_path":
        lambda: _glob_one(_root() + "/*Keys/", "keys"),
    "airflow_path":
        lambda: _glob_one(_root() + "/*Airflow/", "Airflow"),
    "requirements_path":
        lambda: _glob_one(_root() + "/*Requirements/", "requirements"),
    "checkpoint_path":
        lambda: _glob_one(_root() + "/*Checkpoint/", "checkpoint"),
}


_RESOLVERS = {"main_path": _root, "_main_path_source": _root_source}
_RESOLVERS.update(
    {name: (lambda value=value: value) for name, value in _DOCKER_PATHS.items()}
    if IS_DOCKER else _LOCAL_PATHS
)

# Both branches must expose the SAME fourteen names — that is the defect item
# 20a found in code_path, restated as a check now that the two branches are two
# dicts rather than two halves of an if. Checked at import because it costs one
# set comparison and catches a name added to one table and not the other.
#
# A raise rather than an assert: `python -O` strips assert statements, and an
# invariant that disappears under a common interpreter flag is not an invariant.
if set(_DOCKER_PATHS) != set(_LOCAL_PATHS):
    raise RuntimeError(
        "the Docker and local path tables define different names: "
        f"{sorted(set(_DOCKER_PATHS) ^ set(_LOCAL_PATHS))}"
    )

PATH_NAMES = tuple(sorted(_RESOLVERS))
"""Every name this module resolves lazily. Read by ``tests/test_package_invariants.py``
so that a path added to the tables without being added to the shim's import list
is caught, rather than being a name only one of the two lists knows about."""


def __getattr__(name):
    """PEP 562 hook: resolve a path on first read, then cache it.

    Only consulted for names this module does NOT bind eagerly, so IS_DOCKER,
    _glob_one, load_env_keys and everything else reach the reader without
    passing through here.
    """
    return _resolve(name)


def __dir__():
    """The eager names plus the lazy ones, so dir() and tab completion agree
    with what getattr() will actually serve."""
    return sorted(set(globals()) | set(_RESOLVERS))


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
#
# Moved here from oncotriage/settings.py by pass 20c-2a. It defaults to
# keys_path, resolved lazily above, so this is the module it belongs in and the
# module where it needs no deferred import.
#
# To create the .env file:
#   ## create .txt file first, and clean it if it has any text due to fresh
#   ## creation!
#   ## add the text you needed!
#   ## rename it to .env
#   ## use a terminal with this (get to the targeted folder first):
#   ## mv .env.txt .env
#   ## to view the .env in Finder on Mac, hit: command + shift + .

REQUIRED_ENV_KEYS = ("OPENAI_API_KEY", "QDRANT_URL", "QDRANT_API_KEY")
"""The three variables a .env must define. Named here rather than repeated in
the two loops below, which is how one of them once came to be cleared but not
validated."""


def load_env_keys(keys_dir=None):
    """Load API keys from the .env file in `keys_dir`.

    Args:
        keys_dir: Directory holding the .env. Defaults to ``keys_path``, which
            is RESOLVED HERE, on the call, not at import — see the module
            docstring. Kept as an override so a caller that already knows its
            credentials directory — a container, a test fixture — does not have
            to agree with the glob, and so that such a caller resolves no path
            at all.

    Returns:
        {'openai': ..., 'qdrant_url': ..., 'qdrant_key': ...}

    Raises:
        FileNotFoundError: no .env at that location.
        ValueError: the file loaded but did not define all three keys.
    """
    if keys_dir is None:
        keys_dir = _resolve("keys_path")

    env_path = path_settings.with_trailing_sep(keys_dir) + '.env'

    # Validate file exists
    if not os.path.exists(env_path):
        raise FileNotFoundError(f".env file not found at: {env_path}")

    # Clear previous env vars to avoid stale values
    for key in REQUIRED_ENV_KEYS:
        os.environ.pop(key, None)

    # Load from file
    load_dotenv(dotenv_path=env_path, override=True)

    # Validate all keys loaded
    keys = {
        'openai': os.getenv('OPENAI_API_KEY'),
        'qdrant_url': os.getenv('QDRANT_URL'),
        'qdrant_key': os.getenv('QDRANT_API_KEY')
    }

    missing = [k for k, v in keys.items() if v is None]
    if missing:
        raise ValueError(f"Missing keys in .env file: {missing}")

    return keys


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug  5 2026

@author: ramyalsaffar
"""
