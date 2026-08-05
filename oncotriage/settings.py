"""OncoTriage settings — the one place a path environment variable is named,
and the one place credentials are read off disk.

Item 20a (pass 1 of 6) created this as ``oncotriage_settings.py`` at the code
directory, loaded by file location. Item 20c moved the content here, into a
real package module, and left ``oncotriage_settings.py`` behind as a
re-exporting shim: ``01- Imports.py`` and ``28- Select 30 Samples.py`` still
load that filename by location, and neither had to change.

Why load_env_keys() lives HERE and not in utils
-----------------------------------------------
It used to sit in ``02- Utility Functions.py``, and ``03- Config.py`` called it
at line 194. File 02 in turn read ``PRICING_CONFIG``, ``COLLECTION_NAME``,
``qdrant_client`` and ``DATA_SNAPSHOT_DATE`` out of File 03. Under ``exec()``
into a shared namespace that is legal — every name resolves at call time — but
as modules it is a hard import cycle: ``config`` imports ``utils`` imports
``config``.

``load_env_keys`` is the ONLY thing config needed from utils, and it needs
nothing from config: a directory and ``python-dotenv``. Moving it here is what
breaks the cycle. ``config`` now imports ``settings`` and never imports
``utils``, and that edge must not be added back.

Deliberate non-dependencies
---------------------------
This module imports nothing from the project except, at CALL time inside
``load_env_keys``, ``oncotriage.paths`` for the default keys directory. That
one import is deferred into the function body on purpose:

* ``paths`` imports ``settings`` at module load to resolve the project root, so
  a module-level ``from oncotriage.paths import keys_path`` here would be a
  second cycle in place of the one just removed;
* the dependency is on a VALUE that only matters when someone actually reads a
  ``.env``, not on anything needed to define this module;
* and callers who know their own keys directory can pass ``keys_dir`` and never
  trigger it at all.

The honest alternative was to put ``load_env_keys`` in ``paths`` beside
``keys_path``, where no deferral would be needed. It is here instead because
"where credentials come from" is a settings question, and because ``paths``
resolving the tree is a heavier import than a caller who only wants keys should
have to pay for at module load.

Resolution order for every path below: the environment variable if it is set to
a non-empty value, otherwise the FALLBACK_* constant. There is no third tier —
a fallback that is wrong should fail loudly at the point of use, not be patched
over by a guess.
"""

import os

from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Environment variable names
# ---------------------------------------------------------------------------
# The ONCOTRIAGE_ prefix matches ONCOTRIAGE_DEFER_LOCAL_MODELS, which
# 13- LangGraph Agent.py and 46- Fixture Replay.py already use.

ENV_MAIN_PATH = "ONCOTRIAGE_MAIN_PATH"
"""Project root: the directory holding '02- Data', '03- Code', '05- Keys', ...

Read by oncotriage.paths' local (non-Docker) branch. Every other local path is
globbed off it; setting this one variable relocates the whole tree.
"""

ENV_CODE_PATH = "ONCOTRIAGE_CODE_PATH"
"""Directory holding the numbered scripts.

Only 23- Airflow DAG.py's generated DAG needs this as a separate variable. The
29 files that carry an exec_chain bootstrap locate this directory from their
own __file__ and must not read an environment variable for it — a stale value
would point a file at a different copy of itself.
"""

ENV_KEYS_PATH = "ONCOTRIAGE_KEYS_PATH"
"""Directory holding the credential files. 23- Airflow DAG.py's DAG only."""

ENV_DATA_TRIAL_PATH = "ONCOTRIAGE_DATA_TRIAL_PATH"
"""Directory the trial scrape writes into. 23- Airflow DAG.py's DAG only."""


# ---------------------------------------------------------------------------
# Fallbacks
# ---------------------------------------------------------------------------
# This is the one absolute personal path left in the codebase, and it is here
# so that a machine with no ONCOTRIAGE_MAIN_PATH set behaves exactly as it did
# before item 20a. It is a compatibility shim for one developer's machine, not
# a default anyone else should inherit: on any other checkout, set
# ONCOTRIAGE_MAIN_PATH.
#
# It is deliberately NOT derived from this file's own location. Passes 20b-20f
# move files around, and a derived root would silently change meaning as the
# layout changes. A literal that goes stale fails visibly (see
# require_existing_directory below); a derivation that goes stale resolves to
# the wrong tree and keeps running. Item 20c moved this file from the code
# directory into the oncotriage package, which is exactly the kind of move that
# would have shifted a derived root by one directory level and gone unnoticed.

FALLBACK_MAIN_PATH = (
    "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/"
    "03- Clinical Trial Patient Match/"
)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _from_env(var_name, fallback):
    """Return os.environ[var_name] if it is set and non-empty, else fallback.

    An empty string is treated as unset. `export ONCOTRIAGE_MAIN_PATH=` is a
    common way to clear a variable, and honouring it literally would resolve
    every derived path to a relative one against the working directory.

    Returns (value, source) where source is the variable name or "fallback",
    so callers can log which path they took.
    """
    raw = os.environ.get(var_name)
    if raw is not None and raw.strip() != "":
        return with_trailing_sep(raw.strip()), var_name
    return with_trailing_sep(fallback), "fallback"


def with_trailing_sep(path):
    """Normalise to a directory string ending in exactly one separator.

    Every consumer in this codebase concatenates rather than joins
    (`main_path + "/*Data/"`), so a missing trailing separator is a silent
    corruption rather than an error. Callers may pass either shape.
    """
    return path.rstrip("/\\") + os.sep


def require_existing_directory(path, var_name, what):
    """Raise if `path` is not an existing directory, naming the variable to set.

    Without this, a wrong root reaches `glob.glob(main_path + "/*Data/")[0]`
    and fails with an IndexError that names neither the path it tried nor the
    variable that controls it. That error told a reader nothing.
    """
    if not os.path.isdir(path):
        raise RuntimeError(
            f"{what} does not exist or is not a directory: {path!r}\n"
            f"Set {var_name} to the correct location, e.g.\n"
            f"    export {var_name}='/path/to/project/'"
        )
    return path


def resolve_main_path():
    """Resolve the project root. Returns (path, source)."""
    path, source = _from_env(ENV_MAIN_PATH, FALLBACK_MAIN_PATH)
    require_existing_directory(path, ENV_MAIN_PATH, "Project root (main_path)")
    return path, source


def resolve_code_path(fallback):
    """Resolve the code directory for the generated Airflow DAG.

    `fallback` is supplied by the caller rather than stored here: 23- Airflow
    DAG.py bakes in whatever oncotriage.paths resolved at DAG-generation time,
    so the two cannot drift. Not validated here — the DAG is written on one
    machine and parsed on another, and a directory that is absent at
    generation time may be present at parse time.
    """
    return _from_env(ENV_CODE_PATH, fallback)


def resolve_keys_path(fallback):
    """Resolve the credentials directory for the generated Airflow DAG."""
    return _from_env(ENV_KEYS_PATH, fallback)


def resolve_data_trial_path(fallback):
    """Resolve the trial-data directory for the generated Airflow DAG."""
    return _from_env(ENV_DATA_TRIAL_PATH, fallback)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
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
        keys_dir: Directory holding the .env. Defaults to ``keys_path`` from
            ``oncotriage.paths``, imported here rather than at module scope —
            see this module's docstring for why that import is deferred.

    Returns:
        {'openai': ..., 'qdrant_url': ..., 'qdrant_key': ...}

    Raises:
        FileNotFoundError: no .env at that location.
        ValueError: the file loaded but did not define all three keys.
    """
    if keys_dir is None:
        from oncotriage.paths import keys_path
        keys_dir = keys_path

    env_path = with_trailing_sep(keys_dir) + '.env'

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
Created on Tue Aug  4 2026
"""
