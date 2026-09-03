"""OncoTriage settings — the one place a path environment variable is named.

Item 20a (pass 1 of 6) created this as ``oncotriage_settings.py`` at the code
directory, loaded BY FILE LOCATION, because ``01- Imports.py`` was ``exec()``'d
as often as it was run and could not rely on ``sys.path``. Item 20c moved the
content here, into a real package module, and left ``oncotriage_settings.py``
behind as a re-exporting shim for those by-location callers.

BOTH CALLERS ARE GONE AND SO IS THE SHIM (pass 20e). ``28- Select Evaluation Sample.py``
stopped at pass 20c-3d, when its body moved to
``oncotriage/evaluation/sampling.py``; a by-location load there had also been
registering a SECOND copy of this module in ``sys.modules`` under the name
``oncotriage_settings``, two ``_RESOLVED`` caches answering one question.
``01- Imports.py``'s own by-location search stopped earlier still, at pass
20c-2b, when ``oncotriage/paths.py:_load_path_settings()`` became a plain
``import`` — File 01 imported the RESULT, not the file. So by the time pass 20e
measured it, ``oncotriage_settings.py`` had zero consumers of any kind: the only
hits for its filename anywhere in the tree were two comments in
``pyproject.toml`` explaining why it was not packaged. Deleted.

THE RULE THAT MADE IT NECESSARY IS WORTH KEEPING: loading a module by location
does not consult ``sys.path``, so a file loaded that way has to exist at a fixed
place under a fixed name, and it gets its own entry in ``sys.modules`` separate
from any package copy. Nothing in this repository loads anything by location any
more; ``tests/test_package_invariants.py`` section 1c scans for it.

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
``tests/test_package_invariants.py`` enforces it. (Third-party imports inside function
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
# 13- LangGraph Agent.py and fixture_replay.py already use.

ENV_MAIN_PATH = "ONCOTRIAGE_MAIN_PATH"
"""Project root: the directory holding '02- Data', '03- Code', '05- Keys', ...

Read by oncotriage.paths' local (non-Docker) branch. Every other local path is
globbed off it; setting this one variable relocates the whole tree.
"""

ENV_CODE_PATH = "ONCOTRIAGE_CODE_PATH"
"""Directory holding the numbered scripts.

Only 23- Airflow DAG.py's generated DAG needs this as a separate variable. Every
numbered entry point locates its own directory from its own __file__ and must
not read an environment variable for it — a stale value would point a file at a
different copy of itself. (Before pass 20e the same sentence said "the 29 files
that carry an exec_chain bootstrap"; there is no exec chain now, and the rule is
unchanged.)
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

ENV_QDRANT_URL = "ONCOTRIAGE_QDRANT_URL"
"""Endpoint of the Qdrant server, overriding the ``QDRANT_URL`` line in the .env.

NOT A PATH, which is why it is resolved by its own function below rather than by
``_from_env``. That helper runs every value through ``with_trailing_sep``, which
appends ``os.sep`` -- on macOS and Linux that turns ``http://qdrant:6333`` into
``http://qdrant:6333/``, which qdrant-client happens to tolerate, and on Windows
into ``http://qdrant:6333\\``, which it does not. A helper whose correctness
depends on which operating system the container host runs is not a helper. Same
reasoning as ENV_AIRFLOW_PASSWORD, ENV_INFERENCES_DB and
ENV_ALLOW_DEGRADED_REGISTRIES; fourth victim.

WHY THIS EXISTS, AND WHY IT IS NOT SIMPLY ``QDRANT_URL``. ``paths.load_env_keys()``
POPS ``OPENAI_API_KEY``, ``QDRANT_URL`` and ``QDRANT_API_KEY`` out of
``os.environ`` and rewrites all three from the .env. That pop is deliberate and
is KEPT: it exists so a stale exported credential cannot shadow the credentials
file, which is the direction that silently sends a production key to the wrong
endpoint or a dead key to the right one.

(The mechanism under that sentence changed after the provider flip and the
guarantee did not. It used to be ``load_dotenv(..., override=True)``, which
loaded EVERY name in the file; it is now an ALLOWLIST -- the file is parsed with
``dotenv_values`` and only ``paths.ALLOWLISTED_ENV_KEYS`` is written into
``os.environ``. ``QDRANT_URL`` is in that allowlist, so everything this block
argues is unchanged. What changed is that a name NOT in it -- whatever an
operator adds to the credentials file next -- no longer reaches the process
environment as a side effect of resolving these three.) The
consequence, measured inside the running container on 2026-08-06, was that
``QDRANT_URL: http://qdrant:6333`` in docker-compose.yml set the variable, was
popped, and the client still opened Qdrant Cloud -- so the compose `qdrant`
service was declared, started, healthy, and used by nothing.

So there are two different intents wearing one variable name, and they are split
rather than reconciled:

  * ``QDRANT_URL`` in the environment is an ACCIDENT -- a leftover export, a
    shell profile, a CI runner's inherited variable. It must not win, and it
    still does not: the pop is untouched.
  * ``ONCOTRIAGE_QDRANT_URL`` is a DECISION. Nothing exports it by accident;
    the prefix is this project's and it appears in no other tool's namespace.
    It wins, and ``oncotriage.config`` prints which source answered.

That asymmetry is the whole design. It is the same shape as ENV_INFERENCES_DB,
which redirects a database that otherwise resolves from ``paths`` -- a named,
project-prefixed variable beating a default, with the accidental route left
closed.

WHAT IT DOES NOT VALIDATE: that anything is listening. A URL that resolves to
nothing fails at the first client call, loudly, naming the endpoint; a
connectivity probe here would open a socket at settings-resolution time, which
is the one thing every module in this package promises not to do at import.
What IS checked is that the value looks like a URL at all -- see
``resolve_qdrant_url``.

THE API KEY DOES NOT FOLLOW THE URL. See ENV_QDRANT_API_KEY.
"""

ENV_QDRANT_API_KEY = "ONCOTRIAGE_QDRANT_API_KEY"
"""API key for the Qdrant server, overriding the ``QDRANT_API_KEY`` .env line.

NOT A PATH, and a credential besides, so ``_from_env``'s trailing separator
would corrupt it exactly the way it would corrupt ENV_AIRFLOW_PASSWORD.

READ ONLY WHEN ENV_QDRANT_URL IS ALSO SET, and that coupling is the point rather
than an omission. A key is a credential issued BY one endpoint FOR one endpoint.
If ``ONCOTRIAGE_QDRANT_URL`` redirects the client to a host the operator named
in an environment variable, sending the .env's Qdrant Cloud key along to it
would forward a live production credential to an arbitrary address on the
strength of one exported string -- credential exfiltration by configuration, and
the kind that leaves no trace because the request succeeds.

So the rule is:

    URL not overridden  -> .env url,      .env key
    URL overridden, key overridden -> override url, override key
    URL overridden, key NOT overridden -> override url, NO KEY AT ALL

The third row is the ordinary case and it is what the compose stack uses: a
local Qdrant with no ``QDRANT__SERVICE__API_KEY`` configured ignores the header
entirely. Redirecting to a SECOND authenticated cluster without naming its key
gets a 401/403 from that cluster, which names the host and is one variable away
from fixed -- loud, immediate, and strictly better than the silent forward.

``oncotriage.config`` prints which of the three rows applied on every process
that opens a Qdrant client, so a run never has to be guessed at afterwards.
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
variable says, and ``tests/test_degraded_dependencies.py`` demonstrates the refusal
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
    lets "tests/test_degraded_dependencies.py" demonstrate both arms in one process
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


def resolve_qdrant_url():
    """Resolve the Qdrant endpoint override from the environment.

    DELIBERATELY NOT ``_from_env``, for the reason written at ENV_QDRANT_URL:
    that helper appends ``os.sep``, which is right for a directory and wrong for
    a URL on every platform and catastrophic on one of them.

    Returns:
        (url, source) where source is ENV_QDRANT_URL when the variable was set
        to a non-empty value, or (None, None) when it was not. Like the password
        and inferences-database resolvers, this invents no fallback: "not set"
        means the caller should use its own default, and the caller
        (``oncotriage.config``) is the only thing that knows the default is the
        .env.

    Whitespace is stripped, for the same reason ``resolve_inferences_db`` strips
    it: ``export ONCOTRIAGE_QDRANT_URL=$(cat somefile)`` carries a trailing
    newline, and a URL ending in "\\n" fails to connect with an error about the
    host rather than about the variable.

    Raises:
        RuntimeError: the value does not begin with ``http://`` or ``https://``.
            This is the one check worth making eagerly, and it is a check about
            SHAPE, not about reachability. ``QdrantClient(url=...)`` accepts a
            bare host and then behaves in ways that depend on the value: it may
            treat it as a path, or default a port, and the failure surfaces
            later as a connection error naming something the operator never
            typed. Naming the variable here costs one comparison and turns a
            confusing runtime failure into a configuration message. Reachability
            is deliberately NOT probed -- opening a socket while resolving a
            setting is the one thing every module in this package promises not
            to do outside a call the caller asked for.
    """
    raw = os.environ.get(ENV_QDRANT_URL)
    if raw is None or raw.strip() == "":
        return None, None

    url = raw.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise RuntimeError(
            f"{ENV_QDRANT_URL} is set to {raw!r}, which is not a URL.\n"
            f"  It must begin with 'http://' or 'https://', e.g.\n"
            f"      export {ENV_QDRANT_URL}='http://localhost:6333'\n"
            f"  A bare host is accepted by QdrantClient and then interpreted in "
            f"a way that depends on the value, so the connection failure that "
            f"follows names a host nobody typed."
        )
    return url, ENV_QDRANT_URL


def resolve_qdrant_api_key():
    """Resolve the Qdrant API key override from the environment.

    DELIBERATELY NOT ``_from_env``: a credential with a separator appended is a
    credential that authenticates nowhere, and the server's answer to it (401)
    says nothing about a trailing slash. Same reasoning as
    ``resolve_airflow_password``.

    Returns:
        (key, source) where source is ENV_QDRANT_API_KEY when the variable was
        set to a non-empty value, or (None, None) when it was not.

    ONLY ``oncotriage.config`` CALLS THIS, and only when
    ``resolve_qdrant_url()`` has already answered. An unset key beside an
    overridden URL means "send no key", NOT "fall back to the .env key" -- see
    ENV_QDRANT_API_KEY for why forwarding a cloud credential to an
    environment-named host is the failure this coupling exists to prevent.
    Nothing is validated about the value: unlike a URL there is no shape a key
    is required to have, and the only authority on whether it is right is the
    server.
    """
    raw = os.environ.get(ENV_QDRANT_API_KEY)
    if raw is None or raw.strip() == "":
        return None, None
    return raw.strip(), ENV_QDRANT_API_KEY


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


ENV_LOG_LEVEL = "ONCOTRIAGE_LOG_LEVEL"
"""Severity floor for the structured JSON logger in ``oncotriage.observability``.

NOT A PATH, so it is resolved by its own function below rather than by
``_from_env``: that helper appends a trailing separator, and ``"DEBUG/"`` is not
a level name. Fourth victim of the same helper after ENV_AIRFLOW_PASSWORD,
ENV_INFERENCES_DB and ENV_ALLOW_DEGRADED_REGISTRIES, and the reason it is worth
naming again is that this one fails QUIETLY in the useful direction: an
unrecognised level would fall back to the default, so an operator who set DEBUG
to diagnose a failing run would get INFO and conclude the lines do not exist.

Accepted: the five standard names, case-insensitive. Unset means INFO.
"""

_LOG_LEVELS = {
    "CRITICAL": 50, "ERROR": 40, "WARNING": 30, "INFO": 20, "DEBUG": 10,
}

DEFAULT_LOG_LEVEL = "INFO"


def resolve_log_level():
    """The configured severity floor as an int. Raises on an unrecognised name.

    Returns:
        A ``logging`` level integer. Unset or empty gives ``INFO``.

    Raises:
        RuntimeError: the variable names something that is not one of the five
            standard levels. It RAISES rather than falling back, on the same
            argument as ``resolve_allow_degraded_registries``: a typo in a
            switch that decides what gets recorded must not be read as "the
            default", because the operator would then be looking for lines that
            were never emitted and would have no way to tell the variable from
            the code.
    """
    raw = os.environ.get(ENV_LOG_LEVEL)
    if raw is None or raw.strip() == "":
        return _LOG_LEVELS[DEFAULT_LOG_LEVEL]

    name = raw.strip().upper()
    if name in _LOG_LEVELS:
        return _LOG_LEVELS[name]

    raise RuntimeError(
        f"{ENV_LOG_LEVEL} is set to {raw!r}, which is not a logging level.\n"
        f"  Accepted (case-insensitive): "
        f"{', '.join(sorted(_LOG_LEVELS, key=_LOG_LEVELS.get, reverse=True))}.\n"
        f"  Unset or empty means {DEFAULT_LOG_LEVEL}.")


#------------------------------------------------------------------------------


ENV_BEDROCK_API_KEY = "ONCOTRIAGE_BEDROCK_API_KEY"
"""Amazon Bedrock API key for the Stage 5 judge when MATCHING_PROVIDER is
"bedrock".

NOT A PATH, and a credential besides, so it is resolved by its own function
below rather than by ``_from_env``: that helper runs every value through
``with_trailing_sep``, and a bearer token with a slash appended authenticates
nowhere while the server's 401 says nothing about a trailing separator. Fifth
victim of that helper after ENV_AIRFLOW_PASSWORD, ENV_INFERENCES_DB,
ENV_ALLOW_DEGRADED_REGISTRIES and ENV_LOG_LEVEL.

WHY IT IS NOT A FOURTH LINE IN ``05- Keys/.env``. ``paths.load_env_keys()``
reads exactly three names and validates that all three are present; a fourth
would make every process that has no Bedrock key fail to start, including every
process running the default OpenAI provider. The credential is therefore an
environment variable, which is also what AWS's own documentation assumes -- see
ENV_AWS_BEARER_TOKEN_BEDROCK below.

A SHORT-TERM BEDROCK API KEY EXPIRES IN AT MOST 12 HOURS (AWS, "API keys",
docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html, read 2026-08-21),
which is shorter than a full-corpus batch run. Nothing here refreshes it: the
resolver is called once per process, on first client construction, exactly as
the OpenAI key is. A run longer than the key's life fails mid-run with a 401
naming the endpoint. The documented fix is either a long-term key or the
``aws-bedrock-token-generator`` refresh loop, and wiring that in is a change to
the client's auth path rather than to this resolver -- recorded in
``oncotriage/agent/bedrock_adapter.py``'s VERIFY-AT-GO-LIVE list.
"""

ENV_AWS_BEARER_TOKEN_BEDROCK = "AWS_BEARER_TOKEN_BEDROCK"
"""AWS's OWN documented variable for a Bedrock API key, consulted second.

This is the one name in this module that is not project-prefixed, and that is
deliberate rather than an oversight. Every other ``ONCOTRIAGE_*`` name exists
because an unprefixed variable could be set by ACCIDENT -- ``QDRANT_URL`` is the
recorded case, and ``paths.load_env_keys()`` pops it for that reason. This one
is different in kind: ``AWS_BEARER_TOKEN_BEDROCK`` is set on purpose, by an
operator following AWS's own getting-started page, and it names exactly the
credential this project wants. Refusing to read it would mean an operator whose
`aws` tooling already works has to copy the same secret into a second variable.

IT LOSES TO THE PROJECT-PREFIXED NAME, on the ENV_QDRANT_URL precedent: a
project-prefixed variable is a DECISION and beats a shared one. The resolver
reports WHICH of the two answered, and no caller ever prints the value.
"""


def resolve_bedrock_api_key():
    """Resolve the Bedrock API key from the environment. Two tiers, in order.

    DELIBERATELY NOT ``_from_env`` -- see ENV_BEDROCK_API_KEY.

    Returns:
        ``(key, source)`` where source is ENV_BEDROCK_API_KEY or
        ENV_AWS_BEARER_TOKEN_BEDROCK, or ``(None, None)`` when neither is set
        to a non-empty value. The caller decides what to do with "not set":
        unlike a path there is no fallback that could be right, so this
        function does not invent one, and ``oncotriage.config`` raises naming
        both variables.

    Whitespace is stripped and a whitespace-only value counts as unset, for the
    reason ``resolve_airflow_password`` records: ``export VAR=$(cat file)``
    carries the file's trailing newline, and sending that newline to the auth
    endpoint gets a 401 that names nothing.

    NEVER RETURNS THE VALUE IN AN EXCEPTION MESSAGE OR A LOG LINE. Callers
    print the SOURCE. AWS's own note is that API keys are passed as
    authorization headers and are not logged by CloudTrail; this project must
    not be the thing that logs them.
    """
    for var in (ENV_BEDROCK_API_KEY, ENV_AWS_BEARER_TOKEN_BEDROCK):
        raw = os.environ.get(var)
        if raw is not None and raw.strip() != "":
            return raw.strip(), var
    return None, None


#------------------------------------------------------------------------------


ENV_S3_STAGING_REGION = "ONCOTRIAGE_S3_STAGING_REGION"
"""The AWS Region ``oncotriage/staging/`` stages to, overriding the config
default.

WHY THIS ONE IS DEPLOYMENT-VARYING AND THEREFORE OVERRIDABLE. A bucket's Region
is fixed for its lifetime, so the staging preflight compares the resolved boto3
session Region against the configured one and REFUSES a mismatch rather than
creating a bucket on the wrong continent. That refusal is right and its only
remedy was a SOURCE EDIT -- an operator whose account, data-residency rule or
existing bucket lives outside ``us-east-1`` had to change a tracked file to run
the tool at all, and a tracked file changed for one machine is a file that gets
committed for every machine. This variable is that remedy.

NOT A PATH, so it is resolved by its own function below rather than by
``_from_env``: that helper runs every value through ``with_trailing_sep``, and
``"us-east-1/"`` is not a Region. It is interpolated straight into a client
configuration, so the separator would arrive as part of the Region name and the
resulting failure would name a slash nowhere. SIXTH victim of that helper after
ENV_AIRFLOW_PASSWORD, ENV_INFERENCES_DB, ENV_ALLOW_DEGRADED_REGISTRIES,
ENV_LOG_LEVEL and ENV_BEDROCK_API_KEY.

IT DOES NOT VERIFY THAT THE REGION EXISTS, and that is a stated limit rather
than an omission. Nothing here can know AWS's Region list without a network
call, and this module makes none. A Region that is well-formed and wrong is
caught where it already was: the preflight's session comparison, which names
both values and now names where each came from.
"""

ENV_BEDROCK_REGION = "ONCOTRIAGE_BEDROCK_REGION"
"""The AWS Region in the Bedrock base URL, overriding the config default.

Same argument as ENV_S3_STAGING_REGION and a different consequence: this one is
interpolated into ``BEDROCK_BASE_URL_TEMPLATES``, so a wrong value produces a
hostname that resolves nowhere and a config default forces every operator whose
Bedrock quota is granted outside ``us-east-1`` to edit a tracked file before the
Stage 5 judge can answer at all.

NOT A PATH, and resolved by its own function for exactly the reason above: a
trailing separator would land INSIDE a hostname
(``bedrock-runtime.us-east-1/.amazonaws.com``), which is not a diagnosis
anybody would read as a separator problem. Seventh victim of ``_from_env``.

IT IS STILL NOT GATED BY THE RESUME FINGERPRINT, and making it settable does
not change that -- it makes it easier to reach, which is the argument for
saying so twice. Two runs against ``us.openai.gpt-5.6-terra`` in different
Regions remain indistinguishable to ``oncotriage/run_fingerprint.py``. See the
note at ``config.matching_wire_model()``; the follow-up it records is now one
export away from being triggered by accident rather than by a source edit, and
that raises its rank rather than changing its shape.
"""


def _resolve_region(var_name, fallback):
    """Shared body for the two Region resolvers. Returns ``(region, source)``.

    ONE BODY, TWO PUBLIC FUNCTIONS, and that is deliberate in both directions.
    The two Regions are genuinely independent facts -- an operator can stage to
    one Region and call Bedrock in another, and a single shared
    ``ONCOTRIAGE_AWS_REGION`` would make that impossible to express -- so each
    has its own variable, its own docstring and its own named resolver that a
    caller and a grep can find. What they do NOT need is two copies of four
    lines of stripping logic, which is the shape this project keeps removing.

    ``source`` is the variable name when the environment decided the answer and
    ``None`` when the caller's fallback applied. Callers RENDER that, so a
    refusal about a Region says whether the expected value came from an export
    or from ``oncotriage/config.py`` -- which are different edits.

    EMPTY AND WHITESPACE-ONLY MEAN UNSET, on ``_from_env``'s own recorded
    argument: ``export ONCOTRIAGE_BEDROCK_REGION=`` is a common way to clear a
    variable, and honouring it literally would produce an empty Region and a
    refusal about a value nobody typed.

    IT NEVER RAISES, and that is a decision rather than an oversight. Both
    callers resolve at MODULE SCOPE in ``oncotriage/config.py``, so a raise here
    would make ``import oncotriage.config`` fail -- for every process in the
    project, including every one that never touches S3 or Bedrock -- on a typo
    in a variable that concerns two of them. ``resolve_qdrant_url`` raises on a
    malformed value and can afford to because it is called lazily from
    ``get_qdrant_client()``. Validation of the VALUE therefore stays where it
    already is and where it is already lazy and provider-gated:
    ``config.validate_matching_provider_config()`` for Bedrock, and the
    preflight's session comparison for S3.
    """
    raw = os.environ.get(var_name)
    if raw is not None and raw.strip() != "":
        return raw.strip(), var_name
    return fallback, None


def resolve_s3_staging_region(fallback):
    """Resolve the S3 staging Region. Returns ``(region, source)``.

    ``fallback`` is supplied by the caller rather than stored here, on
    ``resolve_code_path``'s precedent and for a second reason of its own:
    CLAUDE.md tells an operator that every tunable lives in
    ``oncotriage/config.py``, and the DEFAULT Region is a tunable. Storing it
    here would put one of the two halves of that value in the module an
    operator is told not to look in.

    DELIBERATELY NOT ``_from_env`` -- see ENV_S3_STAGING_REGION.
    """
    return _resolve_region(ENV_S3_STAGING_REGION, fallback)


def resolve_bedrock_region(fallback):
    """Resolve the Bedrock Region. Returns ``(region, source)``.

    DELIBERATELY NOT ``_from_env`` -- see ENV_BEDROCK_REGION.
    """
    return _resolve_region(ENV_BEDROCK_REGION, fallback)


#------------------------------------------------------------------------------


ENV_IMAGE_DIGEST = "ONCOTRIAGE_IMAGE_DIGEST"
"""The immutable content digest of the image this process is running in.

WHY IT IS AN ENVIRONMENT VARIABLE AND NOT A PROBE. A container cannot read its
own image digest: the digest is a fact the DAEMON holds, and nothing inside the
namespace exposes it -- `/etc/hostname` is the container id, `/proc/self/cgroup`
is the container id, and neither identifies the image those bytes came from.
Every published answer to "what image am I" is the same answer: whoever starts
the container tells it. So this is the channel, and a run that was not told
records that it was not told rather than a plausible substitute.

SET IT AT `docker run` / compose TIME, from `docker image inspect --format
'{{index .RepoDigests 0}}'`, which is the only value that survives a tag being
moved onto different bytes.

DELIBERATELY NOT ``_from_env``: that helper appends ``os.sep``, and a digest
with a trailing slash is a digest that matches nothing and looks like one.
Seventh victim, after ENV_AIRFLOW_PASSWORD, ENV_INFERENCES_DB,
ENV_ALLOW_DEGRADED_REGISTRIES, ENV_LOG_LEVEL, ENV_BEDROCK_API_KEY and the two
Regions.
"""

ENV_IMAGE_TAG = "ONCOTRIAGE_IMAGE_TAG"
"""The build tag of the image this process is running in, when no digest was given.

STRICTLY WEAKER THAN THE DIGEST AND RECORDED AS SUCH. A tag is a NAME, and a
name can be moved onto different bytes without changing -- which is exactly the
failure `Dockerfile`'s pinned base-image digests exist to prevent one layer
down. It is accepted because it is what an operator has when they built the
image locally and never pushed it, and because a weak identity that says it is
weak is worth more than none. `environment.image_identity()` reports WHICH of
the two answered, and no reader may treat them as interchangeable.

DELIBERATELY NOT ``_from_env``, for ENV_IMAGE_DIGEST's reason.
"""


def _resolve_image_field(name):
    """One image-identity variable. Returns the stripped value, or ``None``.

    NEVER RAISES. This runs at run-record time, after a run has been started
    and before nothing -- there is no operator action gated on it and no cost
    to continuing without it. An unset or blank value means "nobody told this
    process what image it is", which `environment.image_identity()` records as
    a state of its own rather than as a failure.
    """
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def resolve_image_digest():
    """The image digest an operator supplied, or ``None``."""
    return _resolve_image_field(ENV_IMAGE_DIGEST)


def resolve_image_tag():
    """The image build tag an operator supplied, or ``None``."""
    return _resolve_image_field(ENV_IMAGE_TAG)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug  4 2026
"""
