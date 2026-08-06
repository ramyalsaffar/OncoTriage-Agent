"""OncoTriage settings — the one place a path environment variable is named.

Item 20a (pass 1 of 6) created this as ``oncotriage_settings.py`` at the code
directory, loaded by file location. Item 20c moved the content here, into a
real package module, and left ``oncotriage_settings.py`` behind as a
re-exporting shim: ``01- Imports.py`` and ``28- Select 30 Samples.py`` still
load that filename by location, and neither had to change.

Why load_env_keys() is NOT here
-------------------------------
Pass 20c-1 put it here, because it was the ONLY thing ``config`` needed out of
``utils`` and moving it broke the ``config`` <-> ``utils`` cycle. But its
default keys directory is ``keys_path``, which lives in ``oncotriage.paths``,
and ``paths`` imports this module — so the import had to be DEFERRED into the
function body to avoid a second cycle in place of the one just removed.

Pass 20c-2a moved it to ``oncotriage.paths`` instead, beside the ``keys_path``
it reads. Same cycle break — ``config`` imports ``paths`` and ``settings``, and
still never imports ``utils`` — with no deferred import anywhere in the package.
A deferred import is a dependency that does not appear in the module's import
block, so no static scan of the import graph can see it; the package now has a
rule that every ``oncotriage``-to-``oncotriage`` import is at module scope, and
``47- Package Split Test.py`` enforces it. (Third-party imports inside function
bodies are untouched by that rule — File 08's ``import icd10`` is deliberate and
stays.)

Deliberate non-dependencies
---------------------------
This module imports nothing from the project at all, at module scope or inside
a function. ``paths`` reads it while resolving the tree, so any dependency in
the other direction would be a cycle.

Resolution order for every path below: the environment variable if it is set to
a non-empty value, otherwise the FALLBACK_* constant. There is no third tier —
a fallback that is wrong should fail loudly at the point of use, not be patched
over by a guess.
"""

import os


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

ENV_AIRFLOW_PASSWORD = "ONCOTRIAGE_AIRFLOW_PASSWORD"
"""Admin password for the Airflow REST API v2.

NOT A PATH, which is why it is resolved by its own function below rather than
by ``_from_env``: that helper runs every value through ``with_trailing_sep``,
and a password ending in a slash-or-separator is not a directory.

ADDED BY PASS 20c-3c-2, and it replaces a route that stopped working when
"24- Airflow Manager.py" became a thin entry point. That file used to hold
``AIRFLOW_PASSWORD = None`` at module level, mutated through ``global`` by its
own ``_get_password()``, and it PRINTED "SET AIRFLOW_PASSWORD in this file" as
the instruction to the operator. Once the functions moved into
``oncotriage.orchestration.airflow_manager``, "this file" became the wrong
file: a name bound in the entry point's namespace is not the package module's
global, so following that printed instruction would have set a variable nothing
reads, and the module would have gone on reading the password file as if the
operator had set nothing. It would not have raised. It would have worked, right
up to the first time the operator wanted a password OTHER than the generated
one -- and then it would have used the generated one and reported success.

The route is explicit now, in four tiers, and ``airflow_manager`` prints which
one it took:

    1. the ``password=`` argument on check_dag_status / trigger_dag / _get_token
    2. ``airflow_manager.set_airflow_password(...)``   (the in-process setter)
    3. this environment variable
    4. {airflow_path}/simple_auth_manager_passwords.json.generated

Tiers 1-3 are new; tier 4 is what File 24 always did and is still the default
for the ordinary case, where Airflow generated the password and nobody chose it.
"""


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


def resolve_airflow_password():
    """Resolve the Airflow admin password from the environment.

    DELIBERATELY NOT ``_from_env``. That helper appends a trailing separator,
    which is correct for every directory above and silently corrupts a
    credential. This is the whole reason it is a separate function rather than
    a fifth line in the table.

    Returns:
        (password, source) where source is ENV_AIRFLOW_PASSWORD when the
        variable was set to a non-empty value, or None when it was not. The
        caller decides what to do with "not set" -- unlike a path, there is no
        fallback that could be right, so this function does not invent one.

    Whitespace is stripped, and a value that is empty or whitespace-only counts
    as unset. That matches every other variable here and it is the common case
    worth handling: `export ONCOTRIAGE_AIRFLOW_PASSWORD=$(cat file)` carries the
    file's trailing newline, and sending that newline to the auth endpoint fails
    with a 401 that names nothing. A password whose meaning depends on its own
    leading or trailing whitespace cannot be set this way; set it with
    ``airflow_manager.set_airflow_password()``, which strips nothing.

    Never returns the value in an exception message or a log line: the callers
    print the SOURCE, not the secret.
    """
    raw = os.environ.get(ENV_AIRFLOW_PASSWORD)
    if raw is not None and raw.strip() != "":
        return raw.strip(), ENV_AIRFLOW_PASSWORD
    return None, None


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug  4 2026
"""
