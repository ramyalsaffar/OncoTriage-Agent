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
from oncotriage.observability import console


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


console.out(f"[Paths] Settings module loaded from {path_settings.__file__} "
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
        console.out("🐳 Running in Docker container")

        # Provenance of main_path, recorded in both branches so a reader of a
        # log can tell a container run from a local one without inferring it.
        _RESOLVED["_main_path_source"] = "Docker image layout (fixed)"
        _RESOLVED["main_path"] = "/app/"
    else:
        # Local development paths (macOS)
        console.out("💻 Running on local machine")

        main, source = path_settings.resolve_main_path()
        _RESOLVED["main_path"] = main
        _RESOLVED["_main_path_source"] = source
        console.out(f"[Paths] Project root: {main} (from {source})")

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
    # The MLflow file-backed tracking store (the tracking pass). It sits under
    # the RESULTS tree rather than as a new top-level sibling, because it is an
    # output of runs, and inventing a fourteenth root-level glob would mean a
    # fourteenth way for `_glob_one` to fail on a machine that has everything
    # else.
    #
    # IN THE CONTAINER THIS WRITES INSIDE THE CONTAINER'S OWN VOLUME, and that
    # is stated rather than discovered: `docker-compose.yml` is unchanged by
    # this pass, so a containerised run's tracking store lives in the
    # `app-results` volume and is not the developer's. The campaign runs on the
    # host.
    "result_tracking_path":     "/app/results/mlflow_tracking/",
    "keys_path":                "/app/",
    "airflow_path":             "/app/airflow_home/",
    # `requirements_path` STOOD HERE AND IS DELETED (pass 20f-3). It resolved
    # `{root}/*Requirements/` locally and /app/requirements/ in the container,
    # and NO CODE HAS EVER READ IT -- measured at pass 20f-2, and again here:
    # the only hits for the name in the tree were its own two table entries and
    # prose. Pass 20f-2 deleted `requirements/requirements.txt` (pyproject.toml
    # is the one dependency list) and left the DIRECTORY standing precisely
    # because this variable named it, recording the removal as a follow-up with
    # the whole edit written out. This is that edit: the variable goes, the
    # directory goes with it, and the container's bring-up report was thirteen
    # paths instead of fourteen. (It is fourteen again since the tracking pass
    # added `result_tracking_path` above — a different name, and one that code
    # reads.)
    #
    # The stale sibling `{root}/07- Requirements/` is outside the repository and
    # is not touched by this or any commit. Nothing resolves to it any more.
    "data_MeSH_path":           "/app/data/mesh/",
    "checkpoint_path":          "/app/checkpoint/",

    # ---- the Testing tree (the portability pass) --------------------------
    #
    # These three were NOT in this table and NOT in the local one. Both roots
    # were resolved by a private `sorted(glob.glob(main_path + "/*Testing"))`
    # -- one copy in `oncotriage/fixtures/capture.py:fixture_root()` and a
    # second in `oncotriage/evaluation/run_harness.py:evaluation_root()` --
    # and BOTH fell back to `os.path.join(main_path, "09- Testing")` when the
    # glob matched nothing. That fallback is the defect: it INVENTS a path
    # rather than raising, so a wrong or unset root sent twelve captured
    # fixtures, or a paid evaluation campaign's manifest and per-patient
    # records, into a directory nobody was looking at, and every one of those
    # runs reported success. Neither had an ambiguity guard either, so two
    # `*Testing` siblings resolved by filesystem order -- the exact
    # nondeterminism pass 20f-1 removed from every other path in this module.
    #
    # `testing_path` exists because both leaves hang off it. Without it each
    # leaf would carry its own copy of the `*Testing/` pattern, which is two
    # copies of one fact and the shape this module removes everywhere else.
    # It is read by the two resolvers below and by nothing outside this file,
    # which is the same standing `results_path` had before the ablation and
    # tracking trees were added under it.
    "testing_path":             "/app/testing/",
    "testing_fixture_path":     "/app/testing/characterization_fixtures/",
    "testing_evaluation_path":  "/app/testing/evaluation_runs/",

    # ---- the model cache (the portability pass) ---------------------------
    #
    # WHERE THE TWO LOCAL MODEL CACHES GO, and until this pass neither was in
    # the project tree at all: the MedCPT cross-encoder (836 MB on disk,
    # MEASURED -- see the block above pin_model_cache) landed in
    # huggingface_hub's default under the user's HOME, and the FastEmbed BM25
    # model landed in `tempfile.gettempdir()/fastembed_cache`, which on macOS
    # is under /var/folders and is PURGEABLE -- so a long campaign could lose
    # its BM25 encoder mid-run and silently re-download it.
    #
    # THE CONTAINER VALUE IS NOT /app/... AND THAT IS DELIBERATE. The
    # Dockerfile already sets HF_HOME=/opt/models/huggingface and
    # FASTEMBED_CACHE_PATH=/opt/models/fastembed and docker-compose.yml already
    # mounts the `model_cache` named volume at /opt/models. This entry
    # DESCRIBES that arrangement rather than moving it: the cache has a
    # different lifecycle from the application data (it is regenerable, it is
    # large, and it must survive `down -v` of the data volumes independently),
    # which is why it has its own volume and its own root there.
    #
    # The name has no `data_` prefix for the same reason: the local branch puts
    # it under the data tree and the container does not, so a prefix naming one
    # of the two would be false in the other.
    "model_cache_path":         "/opt/models/",
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
    "result_tracking_path":
        lambda: _glob_one(_resolve("results_path") + "/*MLflow Tracking/",
                          "MLflow tracking"),
    "keys_path":
        lambda: _glob_one(_root() + "/*Keys/", "keys"),
    "airflow_path":
        lambda: _glob_one(_root() + "/*Airflow/", "Airflow"),
    "checkpoint_path":
        lambda: _glob_one(_root() + "/*Checkpoint/", "checkpoint"),
    # See the Docker table above for what these four replace and why. The
    # patterns are suffix globs like every other entry here, so the Testing
    # tree and its two subdirectories can be renumbered but not renamed past
    # their suffix -- and a missing one now RAISES naming the pattern, which
    # is what the two private resolvers refused to do.
    "testing_path":
        lambda: _glob_one(_root() + "/*Testing/", "testing"),
    "testing_fixture_path":
        lambda: _glob_one(_resolve("testing_path") + "/*Characterization Fixtures/",
                          "characterization fixtures"),
    "testing_evaluation_path":
        lambda: _glob_one(_resolve("testing_path") + "/*Evaluation Runs/",
                          "evaluation runs"),
    # UNDER THE DATA TREE RATHER THAN AS A NEW ROOT-LEVEL SIBLING, on this
    # module's own argument for `result_tracking_path`: a root-level glob is
    # one more way `_glob_one` can fail on a machine that has everything else.
    # The data tree is already where every downloaded third-party artefact
    # lives -- the trial corpus, the MeSH release, the Synthea bundles -- and a
    # model checkpoint is one more of those.
    "model_cache_path":
        lambda: _glob_one(_resolve("data_path") + "/*Model Cache/",
                          "model cache"),
}


_RESOLVERS = {"main_path": _root, "_main_path_source": _root_source}
_RESOLVERS.update(
    {name: (lambda value=value: value) for name, value in _DOCKER_PATHS.items()}
    if IS_DOCKER else _LOCAL_PATHS
)

# Both branches must expose the SAME eighteen names — that is the defect item
# 20a found in code_path, restated as a check now that the two branches are two
# dicts rather than two halves of an if. Checked at import because it costs one
# set comparison and catches a name added to one table and not the other.
#
# THE TABLES ARE A TRIPLE, NOT A PAIR, and this check only sees two of them.
# `.github/scripts/provision_ci_paths.py:_skeleton()` is the third: it creates
# the directories a CI checkout does not have, and a name present here and
# absent there resolves on a developer machine and raises in CI. That file
# cross-checks itself against `PATH_NAMES` at the end of its own main(), so the
# third table fails loudly rather than silently — but the check lives THERE,
# because this module must not import a CI script.
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
# The local model caches
# ---------------------------------------------------------------------------
#
# TWO MODELS ARE DOWNLOADED AT RUN TIME AND NEITHER USED TO LAND IN THE PROJECT
# TREE. `oncotriage/agent/deps.py` loads `ncbi/MedCPT-Cross-Encoder` through
# transformers, which caches under huggingface_hub's default -- the user's
# HOME. MEASURED ON 2026-08-22 AT 836 MB on disk, not the ~110 MB this project
# has been repeating since item 21: the repository serves the weights in two
# formats and huggingface_hub fetches what transformers asks for. The old
# figure is left as written wherever it appears in a past-tense account; it is
# wrong, and the number that matters here is that this is the largest single
# thing the pipeline puts anywhere. `oncotriage/embedding.py` builds the FastEmbed `Qdrant/bm25`
# encoder, and fastembed's default is
# `os.path.join(tempfile.gettempdir(), "fastembed_cache")` -- read out of
# fastembed/common/utils.py:define_cache_dir, not assumed. On macOS that is
# under /var/folders, which the operating system may PURGE at any time; a purge
# during a campaign is a silent re-download inside a run that is otherwise
# spending money per patient.
#
# THE ENVIRONMENT VARIABLE IS NOT THE MECHANISM FOR HUGGINGFACE, AND THAT WAS
# MEASURED RATHER THAN ASSUMED. huggingface_hub reads HF_HOME ONCE, at its own
# import, into module constants, and in THIS project huggingface_hub is
# already imported before any pipeline code runs: `qdrant_client` imports
# `fastembed` at module scope and `fastembed` imports `huggingface_hub` at
# module scope, so `import oncotriage.agent.deps` alone puts both in
# sys.modules. Measured on 2026-08-22, in that order:
#
#     hf_hub HF_HUB_CACHE at its import : ~/.cache/huggingface/hub
#     os.environ["HF_HOME"] = "/tmp/..."
#     transformers TRANSFORMERS_CACHE   : ~/.cache/huggingface/hub   <- UNMOVED
#
# So a pass that only exported HF_HOME would have reported a pinned cache,
# changed nothing, and gone on downloading into the user's home -- the silent
# false report this project treats as worse than a failure. The mechanism is
# therefore the `cache_dir=` ARGUMENT, which transformers resolves at CALL time
# and which outranks every variable. `huggingface_cache_dir()` and
# `fastembed_cache_dir()` below are what the three load sites pass.
#
# THE VARIABLE IS STILL SET, and it is not decoration: it is what a SUBPROCESS
# inherits, it is what a library reached by a path this module does not know
# about will read, and it keeps the host arrangement identical in shape to the
# container's (HF_HOME=/opt/models/huggingface,
# FASTEMBED_CACHE_PATH=/opt/models/fastembed, set in the Dockerfile). The value
# it is set to and the value handed to `cache_dir=` agree by construction:
# huggingface_hub's own rule is HF_HUB_CACHE = HF_HOME + "/hub", and that is
# exactly what `huggingface_cache_dir()` returns.
#
# THE ENVIRONMENT WINS, ALWAYS. An operator who has exported HF_HOME -- or
# HF_HUB_CACHE, which is honoured and never written -- has said where their
# model cache is, very often a shared cache holding several projects'
# checkpoints, and moving it out from under them would re-download gigabytes
# and orphan what is there. Only the DEFAULT moves. When the environment
# answered, `*_cache_dir()` returns None and the library's own resolution is
# left completely alone rather than being second-guessed with a path this
# module computed. That is also what keeps the Docker branch untouched (its
# variables are set in the image) and it is the escape hatch for a machine with
# no `*Model Cache/` directory: set the variable and nothing here resolves a
# path at all.
#
# WHAT AN EMPTY VALUE MEANS. `HF_HOME=""` is treated as UNSET rather than as an
# explicit choice: an empty cache root is not a location, and honouring it would
# hand huggingface_hub a path that resolves relative to the working directory.
# Same treatment `oncotriage/settings.py` gives every ONCOTRIAGE_* variable.
#
# WHY THE ANSWER IS CACHED. `pin_model_cache()` writes into os.environ, so a
# second call would read its OWN write back and report the source as
# "environment" -- a true statement about os.environ and a false one about who
# decided. The first answer per variable is recorded and returned forever,
# which is also what makes the call idempotent for the three load sites.

MODEL_CACHE_ENV_HF = "HF_HOME"
"""huggingface_hub's cache HOME. Read at ITS import; see the block above for
why that makes it a mirror rather than the mechanism."""

MODEL_CACHE_ENV_HF_HUB = "HF_HUB_CACHE"
"""huggingface_hub's cache directory proper. HONOURED AND NEVER WRITTEN: it
outranks HF_HOME in huggingface_hub's own resolution, so an operator who set it
has decided, and this module must not compute a `cache_dir=` that overrides
them."""

MODEL_CACHE_ENV_FASTEMBED = "FASTEMBED_CACHE_PATH"
"""fastembed's cache root, read in define_cache_dir() on every construction."""

MODEL_CACHE_SUBDIRS = {
    MODEL_CACHE_ENV_HF: "huggingface",
    MODEL_CACHE_ENV_FASTEMBED: "fastembed",
}
"""Variable -> subdirectory of ``model_cache_path``. The two names are the
Dockerfile's, character for character, so a cache written on the host and one
written in the container have the same shape."""

MODEL_CACHE_ALSO_HONOURED = {
    MODEL_CACHE_ENV_HF: (MODEL_CACHE_ENV_HF_HUB,),
    MODEL_CACHE_ENV_FASTEMBED: (),
}
"""Other variables that mean "the operator has decided" for a given cache. The
value is checked, never written."""

MODEL_CACHE_ENV_VARS = tuple(MODEL_CACHE_SUBDIRS)
"""The closed set this module knows how to pin. An unknown name raises."""

MODEL_CACHE_HUB_SUBDIR = "hub"
"""huggingface_hub's own rule: the hub cache is HF_HOME + "/hub". Named because
`huggingface_cache_dir()` has to reproduce it exactly -- a `cache_dir=` that
disagreed with the exported HF_HOME would put one process's download in a
different place from the next one's."""

MODEL_CACHE_SOURCE_ENVIRONMENT = "environment"
MODEL_CACHE_SOURCE_PROJECT = "project"
MODEL_CACHE_SOURCES = (MODEL_CACHE_SOURCE_ENVIRONMENT, MODEL_CACHE_SOURCE_PROJECT)
"""Closed, so a caller may branch on it exhaustively."""

_MODEL_CACHE_PINS = {}


def pin_model_cache(variable):
    """Decide where `variable`'s model cache is. Returns (name, value, source).

    `name` is the environment variable that ANSWERED -- `variable` itself when
    this module decided, and possibly one of ``MODEL_CACHE_ALSO_HONOURED`` when
    the operator did. Three members rather than two because the honoured
    siblings do not all mean the same thing: HF_HOME is a home and HF_HUB_CACHE
    is the cache directory itself, so a report that named only the value would
    be ambiguous about which it was showing.

    `source` is drawn from ``MODEL_CACHE_SOURCES``. Idempotent: the first answer
    for a variable is recorded and returned unchanged thereafter, so the source
    stays a statement about who decided rather than about what os.environ
    currently holds -- this function writes into os.environ.

    Raises:
        KeyError: `variable` is not one of ``MODEL_CACHE_ENV_VARS``.
        RuntimeError: the environment named nothing and ``model_cache_path``
            does not resolve -- the ordinary `_glob_one` diagnosis, naming the
            pattern.
        OSError: the cache subdirectory could not be created.
    """
    if variable not in MODEL_CACHE_SUBDIRS:
        raise KeyError(
            f"{variable!r} is not a model-cache variable this module knows how "
            f"to pin; the closed set is {MODEL_CACHE_ENV_VARS}"
        )

    # Same lock the path cache uses, and re-entrant for the same reason: the
    # resolve below re-enters _resolve(). Double-checked outside it because a
    # recorded pin is a dict read.
    if variable in _MODEL_CACHE_PINS:
        return _MODEL_CACHE_PINS[variable]

    with _RESOLVE_LOCK:
        if variable in _MODEL_CACHE_PINS:
            return _MODEL_CACHE_PINS[variable]

        answer = None
        for name in (variable,) + MODEL_CACHE_ALSO_HONOURED[variable]:
            existing = (os.environ.get(name) or "").strip()
            if existing:
                answer = (name, existing, MODEL_CACHE_SOURCE_ENVIRONMENT)
                break

        if answer is None:
            target = os.path.join(_resolve("model_cache_path"),
                                  MODEL_CACHE_SUBDIRS[variable])
            # Both libraries create their own cache tree, so this is not
            # required for them to work. It is here so that the directory a
            # report names is a directory that exists, and so that an
            # unwritable cache root fails HERE -- naming the variable and the
            # path -- rather than thirty frames inside a download.
            os.makedirs(target, exist_ok=True)
            os.environ[variable] = target
            answer = (variable, target, MODEL_CACHE_SOURCE_PROJECT)

        _MODEL_CACHE_PINS[variable] = answer
        return answer


def huggingface_cache_dir():
    """The ``cache_dir=`` to hand ``from_pretrained``, or None.

    None means "the environment decided, leave huggingface_hub's own resolution
    alone". A string is this project's hub cache, which is HF_HOME + "/hub" by
    huggingface_hub's own rule so that the argument and the exported variable
    name one location rather than two.

    THIS IS THE MECHANISM, not the exported variable: see the block above for
    the measurement showing that HF_HOME set after huggingface_hub's import
    moves nothing, and that in this project huggingface_hub is always already
    imported (qdrant_client -> fastembed -> huggingface_hub).
    """
    _name, value, source = pin_model_cache(MODEL_CACHE_ENV_HF)
    if source == MODEL_CACHE_SOURCE_ENVIRONMENT:
        return None
    return os.path.join(value, MODEL_CACHE_HUB_SUBDIR)


def fastembed_cache_dir():
    """The ``cache_dir=`` to hand ``SparseTextEmbedding``, or None.

    None means the environment decided. fastembed's `cache_dir` argument and
    its FASTEMBED_CACHE_PATH variable name the same thing -- `define_cache_dir`
    uses the argument when it is not None and the variable otherwise -- so
    unlike the HuggingFace side there is no "/hub" to append.

    The argument is passed anyway, rather than relying on the variable that
    would work here, because the two caches are pinned the same way on purpose:
    a rule that holds for one library and not the other is a rule the next
    person applies to the wrong one.
    """
    _name, value, source = pin_model_cache(MODEL_CACHE_ENV_FASTEMBED)
    if source == MODEL_CACHE_SOURCE_ENVIRONMENT:
        return None
    return value


def model_cache_pins():
    """A copy of every pin decided so far. Diagnostic; decides nothing.

    A copy rather than the dict, on ``deps.cached_keys()``'s footing: a reader
    that could mutate the record could make a later call report a decision that
    was never taken.
    """
    with _RESOLVE_LOCK:
        return dict(_MODEL_CACHE_PINS)


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
