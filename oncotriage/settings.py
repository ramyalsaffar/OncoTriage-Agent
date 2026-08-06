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

ENV_INFERENCES_DB = "ONCOTRIAGE_INFERENCES_DB"
"""Full path to the SQLite inference log, overriding ``paths.inferences_path``.

NOT A DIRECTORY, which is why it is resolved by its own function below rather
than by ``_from_env``. That helper runs every value through
``with_trailing_sep``, so ``/tmp/scratch.db`` would come back as
``/tmp/scratch.db/`` -- and the consequence is not a visible error. Both
consumers pass the result to ``sqlite3.connect``, which on a path ending in a
separator raises ``OperationalError: unable to open database file``;
``log_inference`` CATCHES ``sqlite3.Error`` by design, so a run redirected with
a trailing separator would print one "Database logging failed (non-critical)"
line per patient and record nothing, while reporting success. Silent data loss
is the exact failure this project exists to remove, and a helper that appends a
separator is the exact way to cause it here. Same reasoning as
ENV_AIRFLOW_PASSWORD above, different victim.

ADDED BY PASS 20c-3i, and the defect it answers was measured rather than
supposed. "17- FastAPI Server.py" calls ``log_inference(result, patient_data)``
with no path, so it resolves to the production database. "18- FastAPI Server
Test.py" and "19- FastAPI Server Batch Test.py" POST real bundles to that live
server, so every run of either wrote real rows into the real ``inferences.db``.
Six such rows were found on 2026-08-05 (patients repeated across three runs of
two), and they changed which query "16- Database Query.py" dies at -- which is
how this surfaced at all. Nothing else reported it.

The server is a SEPARATE PROCESS started by the operator, so neither test file
can redirect it from inside itself; the variable has to be set on the server:

    ONCOTRIAGE_INFERENCES_DB=/tmp/oncotriage-test.db python "17- FastAPI Server.py"

Both test files detect the case where it was NOT set: they read the production
row count before and after their run and fail, naming this variable, if it
moved.

RESOLUTION IS NOT VALIDATED FOR EXISTENCE OF THE FILE -- a database that does
not exist yet is the normal state, ``sqlite3.connect`` creates it, and
``initialize_database`` builds the schema. The PARENT DIRECTORY is validated,
because that is the case sqlite cannot recover from and the one that would
otherwise be swallowed by ``log_inference``'s broad except.
"""

ENV_ALLOW_DEGRADED_REGISTRIES = "ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES"
"""Permit the pipeline to run with a detection layer MISSING rather than raise.

NOT A PATH, which is why it is resolved by its own function below rather than
by ``_from_env``: that helper runs every value through ``with_trailing_sep``,
so ``ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES=1`` would come back as ``"1/"`` and
never compare equal to ``"1"`` again. The switch would then be permanently off
however it was set, which is the quiet direction -- a run that was meant to be
allowed to degrade would instead raise, and the operator would have no way to
tell the variable from a typo. Same reasoning as ENV_AIRFLOW_PASSWORD and
ENV_INFERENCES_DB, third victim.

ADDED BY ITEM 11a. Two layers of this pipeline could DISAPPEAR without anything
failing, and both were measured rather than supposed:

  * ``registries/mesh.py:load_mesh_filter()`` printed a warning and returned
    None when ``mesh_c04_lookup.json`` / ``mesh_tree_to_name.json`` were
    absent. Stage 4's cancer site filter then keeps every trial, and
    ``oncotriage/agent/deps.py`` documented that None as legitimate.
  * ``registries/cancer_code_registry.py:_build_icd10_cancer_sets()`` caught
    ImportError on ``icd10``, called ``logger.error`` and returned three EMPTY
    sets. The registry then logged "CancerCodeRegistry ready" and went on
    classifying with SNOMED and the display-term fallback only -- 1,600+
    ICD-10-CM primary codes silently gone, on a corpus whose real-EHR path is
    exactly the ICD-10 one.

Both now RAISE ``DegradedDependencyError`` by default, naming the missing file
or package and the command that supplies it. This variable is the documented
way to run anyway: the run continues, a WARNING names exactly which layer is
absent, and the degradation is recorded (``mesh.MESH_FILTER_DEGRADATIONS`` /
``cancer_code_registry.REGISTRY_DEGRADATIONS``, and per inference in the
existing ``mesh_filter_skip_reason`` column for the MeSH case).

    export ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES=1

IT DOES NOT REACH THE DELETION PATH, and that exemption is the point rather
than an oversight. ``oncotriage/fhir/clean.py`` unlinks patient bundles from
the corpus on the strength of ``CancerCodeRegistry.is_primary_cancer()``. A
degraded registry there means a missing pip package deletes the dataset --
the exact failure this variable would otherwise re-create --  so
``filter_cancer_patients_inplace()`` refuses a degraded registry whatever this
variable says, and ``48- Degraded Dependency Test.py`` demonstrates the refusal
with the variable SET.

RECOGNISED VALUES: 1/true/yes/on and 0/false/no/off, case-insensitive,
whitespace stripped; empty or unset means off. Anything else RAISES rather than
being read as off. A variable whose whole job is to permit a degraded run must
not itself degrade silently: ``=True`` is fine, but ``=maybe`` or a shell that
exported the literal string ``$FLAG`` would otherwise leave the operator
believing degradation was permitted while every run kept raising -- or, worse
under any other spelling of the default, believing it was forbidden while every
run kept degrading.
"""

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
# Degraded-dependency failure
# ---------------------------------------------------------------------------
#
# WHY THE EXCEPTION LIVES IN settings AND NOT BESIDE EITHER RAISE SITE.
#
# Two modules raise it -- oncotriage.registries.mesh and
# oncotriage.registries.cancer_code_registry -- and neither may import the
# other: mesh needs paths, the cancer registry deliberately needs nothing but
# constants, and a shared parent under registries/ would be a third module
# whose only content is one class. This module already imports NOTHING from the
# project, which is what makes it importable from anywhere without a cycle, and
# it is where ENV_ALLOW_DEGRADED_REGISTRIES is named. Keeping the class beside
# the variable is what stops the message and the variable drifting apart: every
# raise below is constructed through `degraded_dependency_error()`, so the
# opt-out instruction is written once.
#
# A RuntimeError SUBCLASS, and deliberately NOT an ImportError or an OSError,
# for the same reason UnknownModelPricingError is deliberately not a KeyError:
# the ICD-10 raise replaces an `except ImportError` and the MeSH raise replaces
# a missing-file check, so both sit inside code that callers already wrap in
# handlers for exactly those types. An ImportError subclass here would be
# swallowed by the very handler this item exists to remove -- including
# `_build_icd10_cancer_sets`'s own, one frame up.

class DegradedDependencyError(RuntimeError):
    """A detection layer's data file or package is missing.

    Raised instead of continuing with the layer silently absent. Carries
    ``layer`` (the machine-readable name recorded in the degradation counters)
    so a caller can branch on which one failed without parsing the message.
    """

    def __init__(self, message, layer=None):
        super().__init__(message)
        self.layer = layer


def degraded_dependency_error(layer, what_is_missing, how_to_fix):
    """Build the DegradedDependencyError for `layer`, opt-out instruction included.

    Args:
        layer:           machine-readable layer name, e.g. "icd10_cancer_codes".
                         Also the key used in the module-level degradation
                         counters, so the exception and the counter cannot name
                         the same failure two different ways.
        what_is_missing: one line naming the file or package, in full.
        how_to_fix:      the command that supplies it.

    Every raise in the package goes through here, so the sentence telling the
    operator how to run anyway is written once and cannot go stale in one of
    the two raise sites while staying correct in the other.
    """
    return DegradedDependencyError(
        f"{what_is_missing}\n"
        f"  Fix it:   {how_to_fix}\n"
        f"  Or run degraded, accepting that the {layer!r} layer is ABSENT:\n"
        f"      export {ENV_ALLOW_DEGRADED_REGISTRIES}=1\n"
        f"  Degraded runs log a WARNING naming the layer and record it. They "
        f"are NOT permitted for the in-place cohort deletion in "
        f"oncotriage/fhir/clean.py, which refuses a degraded registry however "
        f"this variable is set.",
        layer=layer,
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

# Accepted spellings of the degraded-mode switch. Both directions are listed
# explicitly and anything outside the two sets raises: see the argument at
# ENV_ALLOW_DEGRADED_REGISTRIES for why an unrecognised value must not be read
# as "off".
_ALLOW_DEGRADED_TRUE = frozenset({"1", "true", "yes", "on"})
_ALLOW_DEGRADED_FALSE = frozenset({"0", "false", "no", "off"})


def resolve_allow_degraded_registries():
    """Whether a missing detection layer may be tolerated. Returns (bool, source).

    DELIBERATELY NOT ``_from_env`` -- that helper appends a trailing separator,
    which is right for a directory and turns this flag into a string that can
    never match. See ENV_ALLOW_DEGRADED_REGISTRIES.

    Returns:
        (allowed, source) where source is ENV_ALLOW_DEGRADED_REGISTRIES when
        the variable decided the answer, and None when it was unset or empty
        and the default (do not tolerate) applied. Callers print the SOURCE, so
        a degraded run always says why it was permitted.

    Read at CALL time, not at import, and that is the opposite choice from
    ONCOTRIAGE_DEFER_LOCAL_MODELS. That variable has to be decided before
    "13- LangGraph Agent.py" is exec'd, because it selects between two ways of
    building the process. This one is consulted at the moment a file turns out
    to be missing, which is already lazy -- and reading it at call time is what
    lets "48- Degraded Dependency Test.py" demonstrate both arms in one process
    instead of paying for a subprocess per arm.

    Raises:
        RuntimeError: the variable is set to something that is neither a
            recognised true value nor a recognised false one.
    """
    raw = os.environ.get(ENV_ALLOW_DEGRADED_REGISTRIES)
    if raw is None or raw.strip() == "":
        return False, None

    value = raw.strip().lower()
    if value in _ALLOW_DEGRADED_TRUE:
        return True, ENV_ALLOW_DEGRADED_REGISTRIES
    if value in _ALLOW_DEGRADED_FALSE:
        return False, ENV_ALLOW_DEGRADED_REGISTRIES

    raise RuntimeError(
        f"{ENV_ALLOW_DEGRADED_REGISTRIES} is set to {raw!r}, which is neither "
        f"on nor off.\n"
        f"  Accepted (case-insensitive): "
        f"{', '.join(sorted(_ALLOW_DEGRADED_TRUE))} to permit a degraded run, "
        f"{', '.join(sorted(_ALLOW_DEGRADED_FALSE))} to forbid one.\n"
        f"  Unset or empty means forbid. It is not read as 'off', because a "
        f"switch that decides whether a missing detection layer may be "
        f"tolerated must not itself be tolerant of a value nobody meant."
    )


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


def resolve_inferences_db():
    """Resolve the inference database override from the environment.

    DELIBERATELY NOT ``_from_env``, for the reason written at
    ENV_INFERENCES_DB: that helper appends a trailing separator, which is
    correct for every directory in the table above and turns a database file
    path into something ``sqlite3.connect`` refuses -- refuses in the one way
    ``log_inference`` swallows.

    Returns:
        (path, source) where source is ENV_INFERENCES_DB when the variable was
        set to a non-empty value, or (None, None) when it was not. Like the
        password resolver, this invents no fallback: "not set" means the caller
        should use its own default, and only the caller knows what that is
        (``paths.inferences_path`` for both of today's two callers).

    Whitespace is stripped and ``~`` is expanded. Both matter and neither is
    cosmetic. ``export ONCOTRIAGE_INFERENCES_DB=$(cat somefile)`` carries a
    trailing newline, and a path ending in "\\n" fails to open; ``~/scratch.db``
    is a plausible thing to type and, unexpanded, resolves against the working
    directory into a file inside a directory literally named "~" that does not
    exist -- again an unopenable path, again swallowed.

    Raises:
        RuntimeError: the parent directory does not exist. This is the one
            check worth making eagerly. A database FILE that does not exist is
            the normal case -- sqlite creates it -- but a missing parent
            directory makes ``sqlite3.connect`` raise ``OperationalError``, and
            both consumers resolve the path OUTSIDE their try block precisely so
            a configuration defect reaches the operator instead of being
            reported as one non-critical logging failure per patient.
    """
    raw = os.environ.get(ENV_INFERENCES_DB)
    if raw is None or raw.strip() == "":
        return None, None

    path = os.path.expanduser(raw.strip())
    parent = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(parent):
        raise RuntimeError(
            f"{ENV_INFERENCES_DB} points into a directory that does not "
            f"exist: {parent!r} (from {path!r})\n"
            f"Create the directory, or set {ENV_INFERENCES_DB} to a path "
            f"whose parent exists, e.g.\n"
            f"    export {ENV_INFERENCES_DB}='/tmp/oncotriage-test.db'"
        )
    return path, ENV_INFERENCES_DB


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
