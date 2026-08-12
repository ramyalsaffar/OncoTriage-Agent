# Experiment Tracking
####################

"""The run-to-configuration index: which configuration, prompt, model and code
produced which number.

WHAT THIS IS AND WHAT IT IS NOT
-------------------------------
It is an INDEX OVER RUNS. It sits above the databases and replaces none of
them: ``inferences.db`` keeps the per-patient record, ``ablation_results.db``
keeps the per-patient-per-config record, and both are still the authority on
what happened to a patient. What neither of them answers is the review-time
question -- *which* configuration, prompt version, model and commit produced the
number in the paper -- because both store per-patient rows and neither stores
the run's inputs.

So this module logs, once per run: the configuration constants that govern the
run, the prompt version and the prompt template's digests, the model, the
resolved Qdrant collection, the git commit, and the summary numbers the run's
own summary already computed. Nothing per-patient, and nothing computed here.

EVERY CALLER GOES THROUGH THESE THREE FUNCTIONS AND NOTHING IMPORTS ``mlflow``
------------------------------------------------------------------------------
``start_run`` / ``log_run_metrics`` / ``end_run``. That is not a style rule: the
artifact store moves to S3 at the migration (see ``tracking_uri()``), and one
wrapper means that move is one file. A caller reaching ``mlflow`` directly would
be a second place to change and a second place to forget -- the same argument
``oncotriage/agent/deps.py`` makes about clients and
``oncotriage/embedding.py`` about the BM25 encoder.

``tests/test_tracking_mlflow_index.py`` asserts by AST that no other package
module imports ``mlflow``, with a planted control.

WHY ``import mlflow`` IS INSIDE THE FUNCTION BODIES
---------------------------------------------------
The project's standing rule is that importing a package module opens no client,
loads no model, reads no file and spawns no process, and
``tests/test_package_invariants.py`` section 2 proves it by arming twelve traps
before importing every module in the package. ``mlflow-skinny`` pulls in
GitPython, opentelemetry, databricks-sdk, protobuf and cryptography -- a 33 MB
tree whose import cost is real and whose git handling is exactly the kind of
thing that spawns a process. Hoisting it would make importing THIS module pay
for all of it, and would put the whole tree in front of every test that imports
the package.

It is therefore a deferred THIRD-PARTY import, which is the exemption
``_build_icd10_cancer_sets()``'s ``import icd10`` and
``stage2_retrieval_tests()``'s ``import torch`` already carry, and which check
1b explicitly allows. Check 1b's prohibition is on deferring an ``oncotriage``
import; every project import in this module is at module scope.

The measured cost, on the development machine: ``import mlflow`` is 0.28 s, paid
once, at the first ``start_run()``.

THE PACKAGE IS ``mlflow-skinny``, AND THE FILE STORE NEEDS AN OPT-OUT
---------------------------------------------------------------------
``mlflow-skinny`` is the client-only distribution: no flask, no alembic, no
sqlalchemy, no server, no UI. Measured rather than assumed -- installing it into
the development environment adds exactly TWO packages, ``mlflow-skinny`` and
``databricks-sdk``, and moves no pin (``pip install --dry-run``, 2026-08-10),
and ``pip-audit`` over the resulting tree reports ZERO findings.

WHAT THAT COSTS, stated because it is the one surprise in this pass: **MLflow
3.15 put the filesystem tracking backend into maintenance mode and it now RAISES
unless ``MLFLOW_ALLOW_FILE_STORE`` is set.** The message names
``sqlite:///mlflow.db`` as the replacement, and that route is NOT available
here: a SQLAlchemy backend needs sqlalchemy and alembic, which is the whole
tree ``mlflow-skinny`` exists to avoid, and under skinny the sqlite URI fails
outright (measured: ``UnsupportedModelRegistryStoreURIException``, because the
model-registry store has no sqlite implementation in this distribution).

So this module sets the vendor's own documented opt-out, and only when it is
UNSET, so an operator who has made a deliberate choice keeps it, and says so in the log
line. It is set at CALL time rather than at import, and that is safe rather than
lucky: MLflow reads the variable when the store is CONSTRUCTED, not when it is
imported -- proved both ways, by setting it after ``import mlflow`` (works) and
by removing it and pointing at a fresh directory (raises).

WHAT A TRACKING FAILURE DOES, AND THE LINE IS ITEM 11a's
---------------------------------------------------------
    * ``start_run`` RAISES. It runs at the top of a run, before a cent is spent,
      and everything that can fail there is CONFIGURATION -- the package is not
      installed, the store directory does not exist, the experiment cannot be
      created. One command fixes each, and every run afterwards is correct.
      A missing package in particular refuses by name, with the install command;
      it must never no-op silently, because a campaign whose index quietly did
      not happen is worse than one that refused to start.
    * ``log_run_metrics`` and ``end_run`` DO NOT RAISE. They run at the END of a
      run that has already cost money and already written its rows. Losing that
      run's results because an index write failed would be the tracking layer
      damaging the thing it exists to describe. They record the failure in
      ``TRACKING_DEGRADATIONS``, log it at WARNING, and return False.

That asymmetry is the same line ``oncotriage/paths.py:_glob_one`` draws between
a configuration defect (raise) and third-party data (count), and the counter is
registered in ``oncotriage/degradation.py`` so a degraded index shows up in the
run's own degradation block rather than only in the scrollback.

WHAT IS DELIBERATELY NOT TRACKED
---------------------------------
The API and the MCP server. A request is not a run: it has no configuration
sweep, no summary and no artifact, and one tracking run per HTTP request would
turn the index into a log. Both already write a row per request to
``inferences.db``, which is the per-request record.

Run from terminal:
    (nothing -- this module has no entry point. It is called by
     oncotriage/batch/runner.py and oncotriage/ablation/study.py.)
"""

import math
import numbers
import os
import subprocess
from collections import Counter

import oncotriage
from oncotriage import config
from oncotriage import paths
from oncotriage import utils
from oncotriage.agent.prompts import (
    PROMPT_VERSION,
    prompt_sha256,
    render_system_prompt,
)
from oncotriage.observability import console, get_logger


log = get_logger(__name__)


#------------------------------------------------------------------------------


# ===========================================================================
# DEGRADATION
# ===========================================================================

TRACKING_DEGRADATIONS = Counter()
"""Non-zero means the run happened and its INDEX is incomplete.

Keyed by what degraded, so a reader can tell the three apart without reading
the log:

    ``git_commit:{Reason}``          the commit could not be read; the run is
                                     tagged ``unknown`` and the code that
                                     produced its numbers is not identified
    ``qdrant_collection:{Reason}``   the backing collection could not be
                                     resolved; same treatment
    ``metrics:{ExceptionType}``      a metric write failed; those numbers are
                                     absent from the index
    ``end_run:{ExceptionType}``      the run was not closed cleanly; it may
                                     read as RUNNING forever
    ``artifact:{ExceptionType}``     an artifact did not reach the store
    ``metric_not_numeric:{key}``     a caller passed something MLflow cannot
                                     store as a metric. The KEY, never the
                                     value -- LOGGABLE_FIELDS' rule, applied to
                                     a counter that lands in a durable record

Registered in ``oncotriage/degradation.py`` beside the other seventeen, so the
run-end report prints it. It is NOT registered from here: this module has no
reason to import ``degradation``, and ``register()`` exists for the one module
(``oncotriage/batch/runner.py``) that ``degradation`` cannot import back.
"""


class TrackingUnavailableError(RuntimeError):
    """``mlflow`` is not installed, or the tracking store cannot be opened.

    A ``RuntimeError`` subclass and deliberately NOT an ``ImportError``: a stray
    ``except ImportError`` around an optional feature is exactly how a missing
    tracking package would become a silent no-op, which is the outcome this
    class exists to prevent. Same reasoning as
    ``UnknownModelPricingError`` and ``DegradedDependencyError``.
    """


INSTALL_COMMAND = "pip install -e .   (or: pip install mlflow-skinny)"
"""What to type. Named once, so the refusal message and the test assert the
same string.

IT CARRIES NO VERSION, deliberately. The pin lives in ``pyproject.toml`` and
nowhere else: a version written here as well would be one fact in two places,
which is what this project removed for the MedCPT checkpoint (pass 20f-2), the
BM25 model name (pass 20c-3a) and the ablation database filename (pass 20f-4),
and in each case the two copies had already drifted. ``pip install -e .`` is
also the CORRECT instruction rather than a convenient one -- tracking is a
declared dependency of this package, not an optional add-on, so installing the
package is what installs it at the pinned version."""


#------------------------------------------------------------------------------


# ===========================================================================
# THE STORE
# ===========================================================================

EXPERIMENT_NAME = "oncotriage"
"""One experiment for the project; ``kind`` separates batch runs from ablation
studies as a TAG rather than as a second experiment.

Two experiments would mean two ids to search and a cross-experiment comparison
(``the batch run that produced the paper's headline number`` against
``the ablation arm it is being compared with``) would need a join the file store
does not do. One experiment plus a tag is one search."""


RUN_KINDS = ("batch", "ablation")
"""The closed vocabulary ``start_run``'s ``kind`` may take.

CLOSED, and an unknown value RAISES, on ``deps.OVERRIDE_KEYS``' argument: a
typo'd kind is a run that no ``tags.kind = 'batch'`` search will ever return,
and a silently-accepted one is invisible until somebody notices a number
missing from a comparison."""


RUN_STATUSES = ("FINISHED", "FAILED", "KILLED")
"""MLflow's terminal statuses, restated as a closed set this module accepts.

MLflow itself accepts a wider set including ``RUNNING`` and ``SCHEDULED``;
passing either to ``end_run`` would leave a finished run looking live forever,
which is the one thing the end of a run must not do."""


_ALLOW_FILE_STORE_ENV = "MLFLOW_ALLOW_FILE_STORE"


def tracking_uri():
    """The tracking URI, as a ``file:`` URI over ``paths.result_tracking_path``.

    THE ARTIFACT STORE MOVES TO S3 AT THE MIGRATION. When it does, this function
    returns the S3 URI (or a remote tracking server's http URI with an S3
    artifact root) and NOTHING ELSE IN THE PROJECT CHANGES -- that is the whole
    reason the three functions below exist rather than callers using ``mlflow``
    directly. It is recorded here as a comment rather than as a half-built
    switch, because a code path with no store behind it is a code path nobody
    has run.

    The path is read INSIDE this function, never as a module-scope
    ``from oncotriage.paths import result_tracking_path``: a ``from X import
    name`` is an attribute read, and on this module it would fire the lazy
    resolver at import and glob the whole sibling tree. That is the hole pass
    20c-2c found in ``registries/mesh.py``.
    """
    return "file:" + paths.result_tracking_path


def _configure_store(mlflow):
    """Point MLflow at the store and make the file backend usable.

    Separate from ``start_run`` so the reason for the environment write sits
    with the write. See the module docstring for why the opt-out is needed at
    all and why setting it here rather than at import is safe.
    """
    if _ALLOW_FILE_STORE_ENV not in os.environ:
        os.environ[_ALLOW_FILE_STORE_ENV] = "true"
        _source = "set by oncotriage.tracking"
    else:
        _source = f"already set to {os.environ[_ALLOW_FILE_STORE_ENV]!r}"

    uri = tracking_uri()
    mlflow.set_tracking_uri(uri)
    console.out(f"[Tracking] store: {uri}  ({_ALLOW_FILE_STORE_ENV} {_source})")
    return uri


def _import_mlflow():
    """Return the ``mlflow`` module, or refuse by name.

    The refusal is the one thing this pass promises never to soften: an absent
    tracking package must stop the run, not shrink to a no-op.
    """
    try:
        import mlflow                                          # noqa: PLC0415
    except ImportError as exc:
        raise TrackingUnavailableError(
            f"experiment tracking is required and `mlflow` is not importable: "
            f"{type(exc).__name__}: {exc}\n"
            f"  Install it with: {INSTALL_COMMAND}\n"
            f"  It is a DEFAULT dependency of this package, not an extra -- the "
            f"batch runner and the ablation study are both indexed by it, so a "
            f"run without it would produce numbers nothing can trace back to a "
            f"configuration.\n"
            f"  This is deliberately not a silent no-op: a campaign whose index "
            f"quietly did not happen is worse than one that refused to start."
        ) from exc
    return mlflow


#------------------------------------------------------------------------------


# ===========================================================================
# THE PARAMETERS -- NAMED CONSTANTS ONLY
# ===========================================================================
#
# EVERY PARAMETER THIS MODULE LOGS IS ENUMERATED BY NAME BELOW. There is no
# `config.__dict__`, no `os.environ`, no `vars()` and nothing read from the keys
# directory anywhere in this file, and that is a security property rather than a
# tidiness one: a tracking store is a durable, copied, shared index, and a
# credential that reaches one outlives every scrub of the place it leaked from.
# `tests/test_tracking_mlflow_index.py` asserts the logged key set is EXACTLY
# the enumeration, and asserts by AST that this module names none of those four
# constructs.
#
# WHAT IS IN THE LIST: the constants that govern what a run produces --
# the model and how it is called, what is retrieved, what is re-ranked, what
# survives the filters, and the date ages are computed against. A reviewer
# asking "why is this number different from that one" gets the answer by
# diffing two runs' parameters.
#
# WHAT IS NOT, and why: MAX_WORKERS, the SQLite tunables and the tqdm settings.
# They change how long a run takes and not one number it produces; a parameter
# that cannot explain a difference in a result is noise in a comparison.

CONFIGURATION_PARAM_NAMES = (
    # --- the model, and how Stage 5 is called ------------------------------
    "MATCHING_MODEL",
    "MATCHING_REASONING_EFFORT",
    "MATCHING_TEMPERATURE",
    "MATCHING_SEED",
    "MATCHING_MAX_TOKENS",
    "MAX_LLM_CLASSIFIER_RETRIES",
    "MAX_TRUNCATION_SPLITS",
    # --- retrieval (Stage 2) ------------------------------------------------
    "COLLECTION_NAME",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIM",
    "BM25_RETRIEVAL_SIZE",
    "VECTOR_RETRIEVAL_SIZE",
    "RRF_POOL_SIZE",
    # --- re-ranking (Stage 3) ----------------------------------------------
    "CROSS_ENCODER_MODEL",
    "TOP_K_CANDIDATES",
    # --- the MeSH boost and the two-knob quality gate (Stages 3-4) ---------
    "MESH_BOOST_DIRECT_FRACTION",
    "MESH_BOOST_PAN_FRACTION",
    "MESH_BOOST_DIRECT_FLOOR",
    "MESH_BOOST_PAN_FLOOR",
    "QUALITY_THRESHOLD_PERCENTILE",
    "MEDCPT_SCORE_FLOOR",
    # --- the cost cap (Stage 4) --------------------------------------------
    "MAX_TRIALS_FOR_EVALUATION",
    # --- the corpus the ages are computed against --------------------------
    "DATA_SNAPSHOT_DATE",
)
"""Attribute names read off ``oncotriage.config`` and logged verbatim.

Read through ``getattr(config, name)`` at call time rather than imported at
module scope, so a test that sets ``config.DATA_SNAPSHOT_DATE`` -- this
project's supported patch point for it -- is reflected here the way it is
reflected in ``get_age_reference_date()``."""


CALLER_PARAM_KEYS = frozenset({
    "sample_size",
    "seed",
    "configs",
    "resample_count",
    "resample_seed",
    "db_path",
    "patient_count",
})
"""The ONLY keys a caller may add to the enumeration.

CLOSED, and an unknown key RAISES -- ``deps.OVERRIDE_KEYS``' shape, and for a
sharper reason here: this is the one door through which a caller could put
something that is not a named constant into a durable store. "Params are named
constants only" is a rule that has to be enforced somewhere, and a convention
enforced by nothing is a convention that holds until the first hurried caller.

The members are the run-shape facts a constant cannot carry because they come
from the command line: the ablation study's ``--sample-size`` and seed, its
``--configs`` selection, both databases' ``--db`` redirect, and the batch
runner's corpus size and resample settings."""


_PROMPT_PROBE = {
    "mesh_filter_applied": None,          # filled per variant below
    "mesh_filter_skip_reason": "unrecorded",
    # PROMPT_VERSION 1.6.0 moved the patient record into the system message and
    # deleted trial_count (which this dict used to carry as 0). The probe value
    # is a fixed placeholder, never a real record: these digests fingerprint the
    # TEMPLATE, and a patient's data in a tracking store would outlive every
    # retention policy this project has.
    "patient_record": "<probe: no patient record>",
}
"""Fixed arguments for the prompt-template fingerprints below.

They are DECLARED rather than sampled from a run, and the two digests they
produce are fingerprints OF THE TEMPLATE, not the sha of any prompt that was
actually sent. See ``_prompt_params``."""


def _prompt_params():
    """``PROMPT_VERSION`` plus a digest per Section-2 variant.

    WHY TWO DIGESTS AND NOT ONE, AND WHY THEY ARE NOT "THE RUN'S PROMPT SHA".
    ``render_system_prompt`` takes three arguments and two of them vary PER
    PATIENT -- ``patient_record`` is that patient's whole record, and
    ``mesh_filter_skip_reason`` is whichever reason Stage 4 recorded. A run
    therefore does not have "a" prompt sha, and the per-inference one already
    exists and is already stored: ``inferences.llm_classifier_prompt_sha256``.
    Since 1.6.0 that stored hash covers the patient record too, which makes it
    more per-patient than it was and leaves this pair the only template-level
    identity there is.

    What a run DOES have is a template, and the template has exactly one
    branch: ``mesh_filter_applied``. So the coverage this logs is one digest per
    branch, rendered with the declared probe arguments above. Both move if and
    only if the template text moves -- which is the question an audit asks of a
    run ("was this produced by the prompt I think it was") and which
    ``PROMPT_VERSION`` alone cannot answer, because it is hand-maintained.

    The probe arguments are logged beside the digests, because a digest whose
    inputs are not recorded is a number nobody can reproduce.
    """
    out = {
        "prompt_version": PROMPT_VERSION,
        "prompt_digest_probe": (
            f"mesh_filter_skip_reason="
            f"{_PROMPT_PROBE['mesh_filter_skip_reason']!r}, "
            f"patient_record={_PROMPT_PROBE['patient_record']!r}"),
    }
    for applied, label in ((True, "site_confirmed"), (False, "site_unconfirmed")):
        rendered = render_system_prompt(
            mesh_filter_applied=applied,
            mesh_filter_skip_reason=_PROMPT_PROBE["mesh_filter_skip_reason"],
            patient_record=_PROMPT_PROBE["patient_record"],
        )
        out[f"prompt_template_sha256_{label}"] = prompt_sha256(rendered)
    return out


UNKNOWN = "unknown"
"""What a metadata field reads when it could not be established.

A DOCUMENTED SENTINEL, never an omitted key and never a plausible-looking
substitute. Absence of the fact, stated -- the same shape
``llm_classifier_prompt_sha256`` uses, and the opposite of the
``trials_indexed ... if qdrant_client else 0`` the Docker pass removed for
inventing a zero."""


def git_commit():
    """``(commit, dirty, warnings)`` for the checkout this package was imported
    from.

    DEGRADES, NEVER CRASHES. There is no git in the container -- ``.git`` is not
    copied into the image and the binary is not installed -- so a containerised
    run reaching here must record ``unknown`` and carry on. Crashing a batch run
    over a metadata field would be the tracking layer damaging the run it exists
    to describe, and omitting the key would make "we could not tell" look
    identical to "nobody asked".

    THE DIRTY FLAG IS PART OF THE ANSWER, not an extra. A commit identifies the
    code only if the working tree matches it; a run made from an edited tree is
    tagged ``dirty=true`` so a reviewer knows the commit does not fully identify
    what ran. This is the pass's one addition beyond the brief's "git commit",
    and it is here because the brief's stated goal -- prove which CODE produced
    which number -- is not met by a commit id alone.

    The working directory is derived from ``oncotriage.__file__``, never from
    ``os.getcwd()``: a run may be launched from anywhere, and ``git rev-parse``
    resolves against the cwd.
    """
    warnings = []
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(oncotriage.__file__)))

    def _git(*argv):
        completed = subprocess.run(
            ("git",) + argv, cwd=repo_dir, capture_output=True, text=True,
            timeout=30, check=False)
        if completed.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(argv)} exited {completed.returncode}: "
                f"{completed.stderr.strip()[:200]}")
        return completed.stdout.strip()

    try:
        commit = _git("rev-parse", "HEAD")
    except Exception as exc:                                   # noqa: BLE001
        TRACKING_DEGRADATIONS[f"git_commit:{type(exc).__name__}"] += 1
        warnings.append("git_commit_unknown")
        log.warning("git commit could not be read; the run is tagged unknown",
                    event="tracking_metadata_degraded", tracking_field="git_commit",
                    error_type=type(exc).__name__)
        return UNKNOWN, UNKNOWN, warnings

    try:
        dirty = "true" if _git("status", "--porcelain") else "false"
    except Exception as exc:                                   # noqa: BLE001
        TRACKING_DEGRADATIONS[f"git_dirty:{type(exc).__name__}"] += 1
        warnings.append("git_dirty_unknown")
        log.warning("git working-tree state could not be read",
                    event="tracking_metadata_degraded", tracking_field="git_dirty",
                    error_type=type(exc).__name__)
        dirty = UNKNOWN

    return commit, dirty, warnings


def qdrant_collection():
    """``(collection, warnings)`` -- the collection the alias actually resolves
    to.

    Goes through ``oncotriage/utils.py:resolve_qdrant_collection()``, the
    project's one resolver, rather than opening a second client or reading the
    alias here.

    WHAT IT CAN AND CANNOT TELL YOU, stated because the limit is real.
    ``resolve_qdrant_collection`` never raises: after three attempts it prints a
    warning and RETURNS ``config.COLLECTION_NAME`` as a fallback. So a returned
    value equal to the alias means either "the alias is itself a real
    collection" or "resolution failed", and this function cannot distinguish
    them -- the two are the same string at the Qdrant API. What it CAN catch is
    the case that does raise: no credentials, no client, an unreachable host,
    anything that fails while the client is being CONSTRUCTED. That is the
    container-without-Qdrant case the brief names, and it degrades to
    ``unknown`` with a warning tag.

    The residual is recorded as a finding of this pass rather than papered over
    here: making the resolver distinguish "resolved" from "fell back" is a
    change to a function eight other call sites depend on.
    """
    warnings = []
    try:
        return utils.resolve_qdrant_collection(), warnings
    except Exception as exc:                                   # noqa: BLE001
        TRACKING_DEGRADATIONS[f"qdrant_collection:{type(exc).__name__}"] += 1
        warnings.append("qdrant_collection_unknown")
        log.warning("Qdrant collection could not be resolved; the run is "
                    "tagged unknown",
                    event="tracking_metadata_degraded",
                    tracking_field="qdrant_collection",
                    error_type=type(exc).__name__)
        return UNKNOWN, warnings


def configuration_params(collection=None):
    """Every named constant this module logs, as ``{name: value}``.

    A function rather than a module constant because three of its members are
    resolved per call -- the Qdrant collection, and the two prompt digests --
    and because the config values are read at call time so a patched constant
    is reflected.

    Args:
        collection: the already-resolved backing collection, or None to resolve
            it here. ``start_run`` passes the value it resolved, and that is a
            correctness argument rather than a saving: ``resolve_qdrant_collection``
            makes a live call with three retries, so calling it once for the
            parameter and again for the warning tag would be two round trips
            that CAN DISAGREE -- an alias swap between them would put one
            collection in the parameters while the tags said the other was
            unknown. One resolution, two readers. (Found by running: the first
            version of ``start_run`` did exactly that.)
    """
    out = {name: getattr(config, name) for name in CONFIGURATION_PARAM_NAMES}
    out.update(_prompt_params())
    if collection is None:
        collection, _warnings = qdrant_collection()
    out["qdrant_collection_resolved"] = collection
    return out


#------------------------------------------------------------------------------


# ===========================================================================
# THE THREE FUNCTIONS
# ===========================================================================
#
# THREAD SAFETY, stated rather than assumed. MLflow's fluent API keeps an active
# run per context, and both callers drive twelve worker threads. Neither calls
# anything in this module from a worker: `start_run` and `end_run` bracket
# `main()` on the main thread and `log_run_metrics` runs after the pool has been
# joined. That is a property of the call sites, not of this module, and it is
# recorded at both of them.

def start_run(kind, params=None, run_name=None, nested=False, tags=None):
    """Open a tracking run and log its parameters and tags. Returns the run id.

    Args:
        kind: one of ``RUN_KINDS``. Becomes the ``kind`` tag, which is what
            separates batch runs from ablation studies in one experiment.
        params: extra parameters to log ALONGSIDE the enumeration, restricted to
            ``CALLER_PARAM_KEYS``. An unknown key raises ``KeyError``. The
            enumeration is always logged; a caller cannot suppress it, and a
            caller-supplied key that collides with an enumerated name raises
            rather than overwriting a constant with a caller's value.
        run_name: MLflow's display name. None lets MLflow generate one.
        nested: open this run as a child of the currently active one. Used by
            the ablation study, which logs one parent per study and one child
            per configuration. It is a keyword on the briefed three-function
            surface rather than a fourth function, because a nested run IS a
            run -- everything else about it is identical.
        tags: extra tags. Free-form on purpose: a tag is metadata a human
            attaches to a run (``resumed``, ``interrupted``), not a value a
            comparison is computed from. The four the brief names -- kind,
            prompt_version, model, git commit -- are set here and cannot be
            overridden by this argument.

    Raises:
        TrackingUnavailableError: ``mlflow`` is not importable, or the store
            could not be opened. See the module docstring for why this raises
            while the other two functions do not.
        ValueError: ``kind`` is not in ``RUN_KINDS``.
        KeyError: ``params`` carries a key outside ``CALLER_PARAM_KEYS``, or one
            that collides with the enumeration.
    """
    if kind not in RUN_KINDS:
        raise ValueError(
            f"tracking: kind={kind!r} is not one of {list(RUN_KINDS)}. A "
            f"mistyped kind is a run that no `tags.kind` search returns, so it "
            f"is refused rather than accepted.")

    caller_params = dict(params or {})
    unknown = sorted(set(caller_params) - CALLER_PARAM_KEYS)
    if unknown:
        raise KeyError(
            f"tracking: {unknown} is not in CALLER_PARAM_KEYS "
            f"({sorted(CALLER_PARAM_KEYS)}). Parameters are NAMED CONSTANTS "
            f"ONLY -- the enumeration in this module, plus the closed set of "
            f"run-shape facts a constant cannot carry. A tracking store is "
            f"durable and copied; anything that reaches it outlives every "
            f"scrub of where it leaked from.")

    mlflow = _import_mlflow()

    try:
        _configure_store(mlflow)
        mlflow.set_experiment(EXPERIMENT_NAME)
    except Exception as exc:                                   # noqa: BLE001
        raise TrackingUnavailableError(
            f"experiment tracking could not open its store at "
            f"{tracking_uri()}: {type(exc).__name__}: {exc}\n"
            f"  The directory is `result_tracking_path` in oncotriage/paths.py. "
            f"It must exist -- every path there is resolved by glob and "
            f"_glob_one raises when nothing matches.\n"
            f"  This raises rather than degrading because it is configuration, "
            f"fixed by one command, and it is reached before the run has spent "
            f"anything."
        ) from exc

    commit, dirty, warnings = git_commit()
    collection, collection_warnings = qdrant_collection()
    warnings.extend(collection_warnings)

    enumerated = configuration_params(collection=collection)
    collision = sorted(set(caller_params) & set(enumerated))
    if collision:
        raise KeyError(
            f"tracking: {collision} would overwrite an enumerated constant "
            f"with a caller-supplied value. The enumeration is what a reviewer "
            f"diffs two runs on; a caller able to rewrite it makes that diff "
            f"unreliable.")

    # EVERY VALIDATION IS ABOVE `mlflow.start_run`, deliberately. A raise after
    # the run is open leaves an orphan sitting at RUNNING in the store forever
    # -- nothing closes it, because the exception propagates past `end_run` --
    # and a store full of eternally-RUNNING runs is exactly the unreadable
    # index this module exists to avoid. So the kind, the caller params, the
    # param collisions and the tag collisions are all checked first, and the
    # run is opened only once nothing is left that can refuse.
    run_tags = {
        # The four the brief names.
        "kind": kind,
        "prompt_version": PROMPT_VERSION,
        "model": config.MATCHING_MODEL,
        "git_commit": commit,
        # The fifth, argued at git_commit(): a commit identifies the code only
        # if the tree is clean.
        "git_dirty": dirty,
        # The package version, so a run is traceable to a release as well as to
        # a commit. One string, `oncotriage.__version__`, which pass 20f-2 made
        # the project's single version site.
        "oncotriage_version": oncotriage.__version__,
    }
    for name in warnings:
        # A WARNING TAG PER DEGRADED FIELD, never a single boolean. "Something
        # was unknown" sends a reviewer looking; "qdrant_collection_unknown"
        # tells them where to stop.
        run_tags[name] = "true"
    for key, value in (tags or {}).items():
        if key in run_tags:
            raise KeyError(
                f"tracking: tag {key!r} is set by this module and may not be "
                f"overridden by a caller.")
        run_tags[key] = str(value)

    run = mlflow.start_run(run_name=run_name, nested=nested)
    mlflow.set_tags(run_tags)
    mlflow.log_params({key: str(value) for key, value
                       in {**enumerated, **caller_params}.items()})

    run_id = run.info.run_id
    console.out(f"[Tracking] run {run_id} ({kind}"
                f"{', nested' if nested else ''}) — "
                f"{len(enumerated) + len(caller_params)} params, "
                f"{len(run_tags)} tags")
    log.info("tracking run started", event="tracking_run_started",
             tracking_run_id=run_id, tracking_kind=kind,
             tracking_param_count=len(enumerated) + len(caller_params))
    return run_id


def log_run_metrics(metrics):
    """Log numbers onto the active run. Returns True when every one landed.

    DOES NOT RAISE. See the module docstring: this runs after the run has spent
    its money and written its rows, and a failure here must not take those with
    it.

    Args:
        metrics: ``{name: number}``. ``bool`` is accepted and stored as 1/0 --
            MLflow has no boolean metric, and a verdict like the write
            reconciliation's ``complete`` is exactly the kind of fact a review
            filters on. ``None`` and non-numeric values are DROPPED, counted
            under ``metric_not_numeric:{key}`` and logged by KEY; a silent drop
            would be indistinguishable from a caller that never passed the
            field.

            THE TEST IS ``numbers.Real``, NOT ``isinstance(value, (int, float))``,
            and that is a measured correction rather than a preference. The
            ablation study's metrics come from ``DataFrame.to_dict(orient=
            "records")``, which yields NUMPY scalars: ``numpy.float64`` is a
            subclass of ``float`` and would have passed either test, but
            ``numpy.int64`` IS NOT A SUBCLASS OF ``int`` -- so an ``(int, float)``
            test silently dropped every integer column the summary produces
            (``n``, ``n_scored``, ``errors``) while keeping every float one.
            Both numpy scalar types are registered with the ``numbers`` ABCs,
            so ``numbers.Real`` accepts them. (``numpy.bool_`` is registered
            with neither and would be dropped by key; no caller in this project
            produces one.)

            NaN AND INFINITY ARE DROPPED, not stored and not coerced to zero.
            A NULL SQL aggregate reaches pandas as NaN -- ``cost_per_eligible``
            is NULL for a configuration that matched nothing -- and storing
            that as 0 would make "no matches at any price" indistinguishable
            from "free", which is the exact defect item 38 removed from the
            cost query.
    """
    mlflow = _import_mlflow()

    if mlflow.active_run() is None:
        TRACKING_DEGRADATIONS["metrics:NoActiveRun"] += 1
        log.warning("metrics were offered with no tracking run open; they were "
                    "not stored", event="tracking_metrics_dropped",
                    tracking_metric_count=len(metrics or {}))
        return False

    numeric = {}
    for key, value in (metrics or {}).items():
        if isinstance(value, bool):
            # BEFORE the Real test, because bool is a subclass of int and would
            # otherwise be stored as 1.0/0.0 through the float path. Same value,
            # but the branch that produced it is what the reader below relies on.
            numeric[key] = int(value)
        elif isinstance(value, numbers.Real) and math.isfinite(float(value)):
            numeric[key] = float(value)
        else:
            TRACKING_DEGRADATIONS[f"metric_not_numeric:{key}"] += 1
            log.warning("a metric was not a number and was dropped",
                        event="tracking_metric_dropped", tracking_field=key)

    if not numeric:
        return False

    try:
        mlflow.log_metrics(numeric)
    except Exception as exc:                                   # noqa: BLE001
        TRACKING_DEGRADATIONS[f"metrics:{type(exc).__name__}"] += 1
        log.warning("tracking metrics could not be written; the run happened "
                    "and its index is incomplete",
                    event="tracking_metrics_failed",
                    error_type=type(exc).__name__)
        return False

    return len(numeric) == len(metrics or {})


def end_run(status="FINISHED", artifacts=None):
    """Attach artifacts and close the active run. Returns True on a clean close.

    DOES NOT RAISE, for the reason ``log_run_metrics`` does not: it is the last
    thing a paid run does.

    Args:
        status: one of ``RUN_STATUSES``. An unrecognised status is replaced by
            ``FAILED`` and counted, never by ``FINISHED`` -- a run whose ending
            could not be described is not a run that ended well.
        artifacts: a sequence whose members are EITHER a filesystem path to log
            as-is, OR a ``(filename, text)`` pair to write into the store
            directly. The two-form argument is the shape
            ``parse_fhir_bundle(bundle_or_path)`` already uses in this project,
            and it is here for the same reason: the batch runner's results file
            exists on disk, while its degradation summary is a list of lines
            that exists nowhere. Materialising the second into a temp file just
            to hand over a path would create a file, and the report the run
            already printed is not a file the run owns.

            An artifact that fails to attach is counted and the run still
            closes: an unattached artifact is a gap in the index, and refusing
            to close the run over it would turn that gap into a run that reads
            RUNNING forever.
    """
    mlflow = _import_mlflow()

    if mlflow.active_run() is None:
        TRACKING_DEGRADATIONS["end_run:NoActiveRun"] += 1
        log.warning("end_run was called with no tracking run open",
                    event="tracking_end_no_run")
        return False

    if status not in RUN_STATUSES:
        TRACKING_DEGRADATIONS[f"end_run:UnknownStatus:{status}"] += 1
        log.warning("an unrecognised run status was replaced by FAILED",
                    event="tracking_status_unknown")
        status = "FAILED"

    ok = True
    for artifact in artifacts or ():
        try:
            if isinstance(artifact, tuple):
                filename, text = artifact
                mlflow.log_text(text, filename)
            else:
                mlflow.log_artifact(str(artifact))
        except Exception as exc:                               # noqa: BLE001
            ok = False
            TRACKING_DEGRADATIONS[f"artifact:{type(exc).__name__}"] += 1
            log.warning("an artifact could not be attached to the tracking run",
                        event="tracking_artifact_failed",
                        error_type=type(exc).__name__)

    try:
        run_id = mlflow.active_run().info.run_id
        mlflow.end_run(status=status)
    except Exception as exc:                                   # noqa: BLE001
        TRACKING_DEGRADATIONS[f"end_run:{type(exc).__name__}"] += 1
        log.warning("the tracking run could not be closed; it may read as "
                    "RUNNING in the store",
                    event="tracking_end_failed", error_type=type(exc).__name__)
        return False

    console.out(f"[Tracking] run {run_id} closed: {status}")
    log.info("tracking run ended", event="tracking_run_ended",
             tracking_run_id=run_id, tracking_status=status)
    return ok


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Aug 10 2026

@author: ramyalsaffar
"""
