"""Reference-free Ragas metrics over a recorded evaluation run.

WHAT THIS MEASURES, AND WHAT IT DOES NOT. Three reference-free Ragas metrics are
computed over what the pipeline already recorded: without-reference context
precision on the retrieval side, and faithfulness plus response relevancy on the
generation side. Every one is a JUDGE MODEL'S OPINION about text, scored on a
scale the metric defines. None of them is a correctness measurement, none is
validated against a clinician, and no number here says a match was right.

WHY REFERENCE-FREE ONLY. Context recall needs labelled reference contexts -- a
human statement of which trials SHOULD have been retrieved for each patient --
and this project has none. A recall figure computed against a reference the
pipeline itself produced would measure the pipeline's agreement with itself. It
is out of scope until labels exist, and ``ragas_manifest.json`` records that as
a field rather than leaving it to be inferred from an absence.

THE JUDGE IS A DIFFERENT FAMILY FROM THE PIPELINE, deliberately, on the same
argument ``oncotriage/evaluation/rater.py`` makes: Stage 5 runs
``config.MATCHING_MODEL`` (OpenAI) and the judge here is Anthropic's
``claude-sonnet-4-6``. A judge from the vendor that produced the text measures
family agreement rather than text quality.

THE ONE OPENAI CALL, NAMED. ``ResponseRelevancy`` reverse-engineers questions
from the response and scores their COSINE SIMILARITY to the real question, so it
needs an embedding model; that is an OpenAI call
(``config.EMBEDDING_MODEL``). It is an embedder, not a judge -- it renders no
verdict and reads no criterion -- so family separation is intact. Nothing else
in this module calls OpenAI, and nothing at all re-runs the pipeline, opens a
database, or touches a characterization fixture.

THIS SPENDS MONEY AT STANDARD (NON-BATCH) RATES. Ragas drives the judge
synchronously, one request at a time inside each metric, so the Message Batches
API the rater uses is not available here and its 50% discount does not apply.
``--dry-run`` builds both datasets, counts the calls, prices them and submits
nothing.

Entry point: ``ragas_run.py`` at the code root.
"""

import hashlib
import io
import json
import math
import os
import statistics
import sys
import time

from oncotriage import config, paths
from oncotriage.observability import console, get_logger

log = get_logger(__name__)


#------------------------------------------------------------------------------
# Refusals
#------------------------------------------------------------------------------


class RagasRefusal(RuntimeError):
    """Raised before anything is submitted, when a precondition fails.

    A RuntimeError subclass and deliberately NOT a ValueError, on the precedent
    of ``RaterRefusal`` and ``UnknownModelPricingError``: a stray
    ``except ValueError`` around argument handling must not be able to swallow
    the one thing standing between a misconfigured run and a live bill.
    """

    def __init__(self, message, code="refused"):
        super().__init__(message)
        self.code = code


#------------------------------------------------------------------------------
# What this harness is pinned to
#------------------------------------------------------------------------------


DEFAULT_JUDGE_MODEL = "claude-sonnet-4-6"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 4096
DEFAULT_MAX_WORKERS = 4
DEFAULT_CLIENT_RETRIES = 5

# ResponseRelevancy generates this many questions per sample and averages their
# cosine similarity to the real user_input. It is Ragas' own default; it is
# named here because it multiplies the judge-call count by exactly this factor
# and the dry run has to be able to say so.
RELEVANCY_STRICTNESS = 3

METRIC_CONTEXT_PRECISION = "context_precision_without_reference"
METRIC_FAITHFULNESS = "faithfulness"
METRIC_RESPONSE_RELEVANCY = "response_relevancy"

DATASET_RETRIEVAL = "retrieval"
DATASET_GENERATION = "generation"

# Which metric runs over which dataset. Closed, and read by the runner, the dry
# run and the summary alike so the three cannot disagree about what was run.
DATASET_METRICS = {
    DATASET_RETRIEVAL: (METRIC_CONTEXT_PRECISION,),
    DATASET_GENERATION: (METRIC_FAITHFULNESS, METRIC_RESPONSE_RELEVANCY),
}

ALL_METRICS = (METRIC_CONTEXT_PRECISION, METRIC_FAITHFULNESS,
               METRIC_RESPONSE_RELEVANCY)

# ONLY response relevancy needs an embedder. Naming that here rather than
# testing for it at each site is what lets a context-precision-only run make no
# OpenAI call at all -- not merely no OpenAI *request*, but no client built and
# no OPENAI_API_KEY read. A run that says it called one vendor must not quietly
# construct another's client.
METRICS_NEEDING_EMBEDDINGS = (METRIC_RESPONSE_RELEVANCY,)


def _selected(active):
    """The flat set of metric names in an active mapping."""
    return {m for metrics in active.values() for m in metrics}


def active_dataset_metrics(selected):
    """dataset -> the selected metrics for it, with empty datasets dropped.

    One derivation, read by the plan, the runner, the summary and the
    post-checks, so a partial run cannot have four opinions about what it was
    supposed to do. A dataset nobody selected a metric for is absent, which is
    what makes ``--metrics context_precision_without_reference`` skip building
    and scoring the 126-sample generation set entirely.
    """
    chosen = tuple(m for m in ALL_METRICS if m in set(selected))
    if not chosen:
        raise RagasRefusal("no metrics selected; there would be nothing to "
                           "score.", code="no_metrics_selected")
    active = {}
    for dataset, metrics in DATASET_METRICS.items():
        here = tuple(m for m in metrics if m in chosen)
        if here:
            active[dataset] = here
    return active


# Recorded into the manifest so a reader of the output does not have to know
# this file to know what was left out and why.
CONTEXT_RECALL_SCOPE_NOTE = (
    "Context recall is OUT OF SCOPE for this run and was not computed. It "
    "requires labelled reference contexts -- a human statement of which trials "
    "should have been retrieved for each patient -- and none exist for this "
    "cohort. Computing it against contexts the pipeline itself selected would "
    "measure the pipeline's agreement with itself, not its recall.")

RELEVANCY_RANGE_NOTE = (
    "response_relevancy is the mean cosine similarity between the real "
    "question and questions reverse-engineered from the response, so it is "
    "bounded by [-1, 1] and NOT by [0, 1]. A negative value is not merely "
    "arithmetically possible: feeding this harness an unrelated paragraph "
    "about the Eiffel Tower in place of an assessment scored -0.0025, "
    "measured. Ragas also zeroes the score when every generated question is "
    "judged noncommittal, so an exact 0.0 means 'the judge read this response "
    "as evasive' rather than 'orthogonal' -- 19 of 126 samples in the "
    "reference run scored exactly 0.0 for that reason, not because their "
    "cosine similarity was zero.")

CIRCULARITY_RULE_NOTE = (
    "CIRCULARITY RULE. A retrieved context must not be judged useful merely "
    "because its ID is echoed back in the response; a rejected trial has to "
    "earn its relevance verdict on CONTENT. The retrieval response therefore "
    "carries only the trials the pipeline verdicted ELIGIBLE, each as its "
    "nct_id plus that trial's assessment text, and rejected trials are absent "
    "from it entirely. RESIDUAL, STATED: a matched trial's own ID is still "
    "echoed, so a matched context can still be credited on the ID cue alone. "
    "That is deliberate -- a matched trial genuinely is the answer -- and it "
    "means the bias is removed for the rejected trials the saturated metric "
    "could not discriminate, not for every context.")

SUPERSEDES_NOTE = (
    "This run SUPERSEDES the context_precision_without_reference figures in "
    "the sibling ragas/ directory, which are retained unchanged as the record "
    "of the biased design. There, the retrieval response was a mechanical "
    "listing of EVERY verdicted trial as '<nct_id>: <label>', so every "
    "retrieved context's ID appeared in the answer verbatim -- including the "
    "IDs of trials the pipeline had rejected -- and a judge could mark any "
    "context useful on a string match without reading the trial text. It "
    "saturated as that predicts: mean 0.9456 over 10 patients, five of them "
    "at the metric's ceiling (1.0000 to four decimals; Ragas divides by "
    "sum(verdicts)+1e-10, so an exact 1.0 is unreachable and NO value in "
    "either run is exactly 1.0). A LOWER score here is the fix working, not a "
    "regression: rejected-but-retrieved trials are now judged on merit. The "
    "two runs are NOT comparable as a before/after of retrieval quality -- "
    "nothing about the pipeline changed, only what the judge was asked.")

REPRODUCIBILITY_NOTE = (
    "THESE SCORES ARE NOT REPRODUCIBLE RUN TO RUN, AT TEMPERATURE 0. "
    "faithfulness is supported-statements / total-statements, and the "
    "statement decomposition is itself a generative call whose output VARIES: "
    "one identical sample (05de31b9.../NCT01803542, a 150-character "
    "assessment) was observed at 1.0000 on five separate runs and 0.4000 on a "
    "sixth, because a one-statement reading scoring 1/1 and a five-statement "
    "reading scoring 2/5 are both plausible decompositions of the same text. "
    "max_tokens was excluded as the cause by measurement (1.0000 at both 2048 "
    "and 4096, with output tokens ~600, far under either cap). Treat a "
    "single sample's score as one draw, not a measurement, and prefer the "
    "distribution over any individual value.")

# The fixed generation-side question. Deliberately WITHOUT the patient summary:
# response relevancy embeds questions reverse-engineered from the ASSESSMENT and
# compares them to this string, so a summary-laden user_input would be
# dissimilar to every generated question by construction and would floor the
# metric for reasons that have nothing to do with the pipeline.
GENERATION_QUESTION_TEMPLATE = (
    "Is this patient eligible for trial {nct_id}? Assess the eligibility "
    "criteria against the patient record.")


#------------------------------------------------------------------------------
# WHICH RECORDED TEXT IS SCORED
#------------------------------------------------------------------------------
# WHY THIS IS AN OPTION RATHER THAN A CONSTANT. At PROMPT_VERSION 1.5.0 the
# stored ``assessment`` of an eligible or not_eligible trial stopped being the
# model's free-written reasoning: ``oncotriage/agent/evaluation.py:
# compose_assessment`` builds it from that trial's own criterion /
# patient_value / status rows, quoting the criterion text (which restates the
# trial) and the patient value (which comes from the patient record) VERBATIM.
# Both of a generation sample's two contexts are therefore the sources the
# composed text was assembled from, so FAITHFULNESS OVER THE COMPOSED FIELD IS
# EXPECTED NEAR ITS CEILING BY CONSTRUCTION -- it measures the renderer, and a
# metric that cannot fail is not a measurement.
#
# The model's own prose survives beside it: ``collect_verdicts`` copies each
# verdict verbatim, so a >=1.5.0 run artifact carries ``assessment_draft``,
# which is held in memory only and has no database column. Scoring THAT is what
# keeps faithfulness a statement about the MODEL. Both readings are wanted --
# one prices the renderer, one prices the model -- so this is a selection, not
# a replacement, and the manifest records which was scored.
#
# THE DEFAULT DOES NOT MOVE. Every figure published from this harness so far was
# read over ``assessment``; changing what a bare invocation scores would make
# two runs with identical command lines mean different things.

RESPONSE_FIELD_ASSESSMENT = "assessment"
RESPONSE_FIELD_DRAFT = "assessment_draft"

RESPONSE_FIELDS = (RESPONSE_FIELD_ASSESSMENT, RESPONSE_FIELD_DRAFT)
"""The closed set of recorded verdict fields this harness will score.

Closed rather than "any key name", on ``deps.OVERRIDE_KEYS``' footing: an
unrecognised field would be read as absent on every verdict and refuse, but the
message would blame the artifact for a typo in the command line.
"""

DEFAULT_RESPONSE_FIELD = RESPONSE_FIELD_ASSESSMENT

RESPONSE_FIELD_NOTES = {
    RESPONSE_FIELD_ASSESSMENT: (
        "response = the STORED assessment. At PROMPT_VERSION >= 1.5.0 this is "
        "COMPOSED text, not the model's prose: it is built from the trial's "
        "own criterion / patient_value / status rows, quoting the criterion "
        "(which restates the trial text) and the patient value (which comes "
        "from the patient record) verbatim. Both of this sample's contexts are "
        "the sources it was assembled from, so faithfulness over this field is "
        "EXPECTED NEAR ITS CEILING and measures the RENDERER rather than the "
        "model. A high number here is the composition working, not a quality "
        "finding."),
    RESPONSE_FIELD_DRAFT: (
        "response = assessment_draft, the model's OWN prose for this trial, "
        "kept beside the composed assessment in memory and in the run artifact "
        "with no database column. Faithfulness over this field measures the "
        "MODEL: the draft was written before composition and is not quoted "
        "from the contexts, so the metric can fail. Figures over this field "
        "are NOT comparable with figures over `assessment` -- they are two "
        "different subjects, not a before/after."),
}

RESPONSE_FIELD_MISSING_NOTE = (
    "A verdict that does not CARRY the selected field is a refusal, never a "
    "fallback to another field. Falling back would score a mixture -- some "
    "samples the model's prose, some the renderer's -- under one metric name, "
    "and the mean over that mixture is a number about nothing. An artifact "
    "written before PROMPT_VERSION 1.5.0 has no `assessment_draft` key at all, "
    "which is exactly the case that must refuse rather than quietly reporting "
    "the composed figure under the draft's name.")


#------------------------------------------------------------------------------
# The environment stamp
#------------------------------------------------------------------------------
# WHY IT EXISTS. Ragas is deliberately NOT a pipeline dependency and is not in
# pyproject.toml -- installing it there would drag ``openai`` from 1.x to 2.x
# and bump ``langgraph``, both of which the pipeline depends on -- so NOTHING IN
# THIS REPOSITORY PINS THE ENVIRONMENT THIS HARNESS RUNS IN. A later run under a
# different ragas, whose metric prompts, statement decomposition or defaults
# have moved, would produce different scores, and that drift would be
# indistinguishable from pipeline drift. REPRODUCIBILITY_NOTE already records
# that faithfulness is not reproducible sample to sample at temperature 0; the
# environment must not add a second, unrecorded source of variation on top of a
# known one.
#
# THERE IS NO LOCKFILE IN THIS REPOSITORY, AND THAT IS WHY THIS EXISTS RATHER
# THAN BEING REDUNDANT WITH ONE. The environment that produced the two
# reference runs under ``09- Testing/Evaluation Runs/`` could not be found when
# it was looked for: no ``pyvenv.cfg`` anywhere under the project root or the
# home directory holds ragas, no conda environment has it, nothing in the pip
# or uv caches names it, and the two manifests it wrote record a ragas version
# and nothing else about it. So it could not be frozen, and freezing a fresh
# environment instead would be a lockfile claiming a provenance it does not
# have. A stamp taken by the run itself has the opposite property: it cannot
# claim anything about a run it did not make. It is also what a truthful
# lockfile needs first -- the next run records what it used, and THAT is a
# thing that can honestly be pinned.
#
# A LOCKFILE CAN BE IGNORED; A RECORDED VERSION CANNOT BE ABSENT. When one is
# added, the two stay independent on purpose: an operator who installs by hand,
# or who edits the environment after creating it, still gets a truthful
# manifest rather than the file's aspiration.
#
# READ THROUGH importlib.metadata RATHER THAN ``__version__``, AND NEVER AT
# IMPORT. Three reasons, none of them "``__version__`` is missing" -- measured
# on this machine, anthropic 0.72.0, openai 1.99.9 and langchain-core 1.5.3 all
# expose one:
#
#   1. ASKING WOULD MEAN IMPORTING. ``__version__`` is an attribute of a loaded
#      module, so reading it for ragas imports ragas -- which breaks this
#      module's lazy-import discipline and is precisely what lets ``--help``
#      and ``--dry-run`` run in an environment that has no ragas at all. There
#      is no ``__version__`` to read for a distribution that is not installed,
#      and "not installed" is a thing this stamp must be able to say.
#   2. THE DISTRIBUTION NAME IS NOT THE MODULE NAME. ``langchain-core`` imports
#      as ``langchain_core``. A ``__version__`` route needs a name-mapping
#      table beside ENVIRONMENT_PACKAGES, which is a second declaration that
#      can drift from the first; importlib.metadata takes the distribution name
#      directly, which is also the name ``pip install`` and ``pip freeze`` use.
#   3. IT IS THE THING A LOCKFILE REPRODUCES. A lockfile pins distributions,
#      not module attributes, so the metadata reading is the one that answers
#      "would reinstalling this give me the same code".
#
# importlib.metadata READS THE FILESYSTEM, which is why every call site here is
# inside a function body: tests/test_package_invariants.py section 2 imports
# every package module with ``builtins.open`` and ``io.open`` trapped to raise,
# and a module-scope lookup would fire them.

ENVIRONMENT_PACKAGES = ("ragas", "anthropic", "openai", "langchain-core")
"""The distributions whose version decides what a score means.

``ragas`` owns the metric prompts and the decomposition; ``anthropic`` and
``openai`` are the two SDKs that carry the judge and the embedder;
``langchain-core`` is ragas' own wrapper layer, whose message and callback
shapes ragas builds its prompts on top of. Recorded whether or not it is
installed -- "``langchain-core`` if present" is a statement about the
environment, and ``absent`` states it.
"""

PACKAGE_ABSENT = "absent"
"""Recorded for a distribution that is not installed.

A FACT ABOUT THE ENVIRONMENT, NOT A FAILURE. The dry-run path returns before
ragas is imported, so it legitimately runs in the project environment, where
``absent`` is the correct record of the interpreter that produced the plan.
"""

PACKAGE_UNREADABLE_PREFIX = "unreadable: "
"""Recorded when the metadata read failed for any reason OTHER than absence.

The distinction is the point, and collapsing it is the defect. A bare
``except Exception: return PACKAGE_ABSENT`` would record a corrupt
``dist-info``, an unreadable site-packages or a broken importlib as "not
installed" -- a false statement about the environment, in the one field that
exists to be trusted, with nothing anywhere saying otherwise. It would also
break this project's standing rule that no exception is caught without being
re-raised or recorded: here the record IS the returned value, which is why
nothing is counted beside it.
"""


def package_version(name, version_fn=None):
    """The installed version of ``name``, or a truthful record of why not.

    ``version_fn`` is the seam. The default is ``importlib.metadata.version``;
    a test installs a stand-in so both the absent path and the unreadable path
    can be driven without uninstalling anything or corrupting a real
    ``dist-info``.
    """
    import importlib.metadata

    lookup = version_fn if version_fn is not None else importlib.metadata.version
    try:
        return lookup(name)
    except importlib.metadata.PackageNotFoundError:
        return PACKAGE_ABSENT
    except Exception as exc:                            # noqa: BLE001
        # Recorded in the value rather than swallowed. The caller writing this
        # into a manifest is the record.
        return f"{PACKAGE_UNREADABLE_PREFIX}{type(exc).__name__}: {exc}"


def environment_stamp(version_fn=None):
    """What this process is actually running, recorded rather than assumed.

    ``python_executable`` is here because of how the lockfile item started: the
    environment that produced the two reference runs under
    ``09- Testing/Evaluation Runs/`` could not be found afterwards -- no
    ``pyvenv.cfg`` anywhere under the project root or the home directory holds
    ragas -- so it could not be frozen, and the manifests it wrote recorded a
    ragas version and nothing about where it lived. The interpreter path is one
    string that makes the next environment findable.
    """
    return {
        "python_version": sys.version,
        "python_executable": sys.executable,
        "packages": {name: package_version(name, version_fn)
                     for name in ENVIRONMENT_PACKAGES},
    }


def print_environment(environment):
    """Print the stamp. ONE renderer, so both paths describe it identically.

    A separate formatter for the dry run could disagree with the one the real
    run prints, and an operator comparing a plan against a run would be
    comparing two descriptions rather than two environments.
    """
    console.out("environment:")
    # 15 is one past the longest name in ENVIRONMENT_PACKAGES
    # ("langchain-core"), so the column does not tear when the longest entry is
    # printed. Derived rather than typed, so adding a longer name widens it.
    width = max(len(n) for n in (("python", "executable")
                                 + tuple(environment["packages"]))) + 1
    first_line = (environment["python_version"].splitlines() or [""])[0]
    console.out(f"    {'python':<{width}} {first_line}")
    console.out(f"    {'executable':<{width}} "
                f"{environment['python_executable']}")
    for name, version in environment["packages"].items():
        console.out(f"    {name:<{width}} {version}")


#------------------------------------------------------------------------------
# Reading an evaluation run
#------------------------------------------------------------------------------


def default_run_dir():
    """The evaluation-run directory this harness reads by default.

    Resolved lazily and relative to the project root, never hardcoded absolute:
    ``oncotriage/paths.py`` owns the root and ``09- Testing`` is a sibling of
    the code directory. Nothing is created and nothing is read here.
    """
    return os.path.join(paths.main_path, "09- Testing", "Evaluation Runs",
                        "eval_run_20260811_093337")


class RetrievalSample(object):
    """One patient: the summary, the ranked contexts, the verdict listing."""

    __slots__ = ("patient_id", "user_input", "retrieved_contexts", "response",
                 "response_field", "matched_count")

    def __init__(self, patient_id, user_input, retrieved_contexts, response,
                 response_field=DEFAULT_RESPONSE_FIELD, matched_count=None):
        self.patient_id = patient_id
        self.user_input = user_input
        self.retrieved_contexts = retrieved_contexts
        self.response = response
        # COUNTED WHERE THE RESPONSE IS BUILT, never re-derived from the text.
        # The dry run used to recover it as ``len(response.splitlines())``,
        # which is only true while no scored field contains a newline. That
        # held for the composed ``assessment`` -- measured, 0 of 126 -- and
        # ``assessment_draft`` is free model prose, so making the field
        # selectable made a display that over-counts matches and under-counts
        # "must earn it on content" reachable on real data.
        self.matched_count = matched_count
        # Carried on the SAMPLE, not only in args, so the post-check that every
        # scored response really came from the declared field has something to
        # read. A manifest field is a claim; this is what makes it checkable.
        self.response_field = response_field

    @property
    def key(self):
        return (self.patient_id,)

    def as_join(self):
        return {"patient_id": self.patient_id}


class GenerationSample(object):
    """One verdicted trial: the fixed question, two contexts, the assessment."""

    __slots__ = ("patient_id", "nct_id", "user_input", "retrieved_contexts",
                 "response", "eligible", "verdict_group", "response_field")

    def __init__(self, patient_id, nct_id, user_input, retrieved_contexts,
                 response, eligible, verdict_group,
                 response_field=DEFAULT_RESPONSE_FIELD):
        self.patient_id = patient_id
        self.nct_id = nct_id
        self.user_input = user_input
        self.retrieved_contexts = retrieved_contexts
        self.response = response
        self.eligible = eligible
        self.verdict_group = verdict_group
        self.response_field = response_field

    @property
    def key(self):
        return (self.patient_id, self.nct_id)

    def as_join(self):
        return {"patient_id": self.patient_id, "nct_id": self.nct_id}


class RunInput(object):
    """Everything read out of an evaluation run directory."""

    def __init__(self, run_dir, manifest, retrieval, generation, problems,
                 response_field=DEFAULT_RESPONSE_FIELD, field_census=None):
        self.run_dir = run_dir
        self.manifest = manifest
        self.retrieval = retrieval
        self.generation = generation
        self.problems = problems
        self.response_field = response_field
        # Measured while reading, so the report's "the draft population is the
        # differing one" is a harness measurement rather than a side script's.
        self.field_census = field_census or {}


ELIGIBLE_LABEL = "eligible"

NO_MATCH_RESPONSE = ("No matching trial was found for this patient. None of "
                     "the retrieved trials was assessed as eligible.")


def _match_response(verdicts, problems=None, patient_id=None,
                    response_field=DEFAULT_RESPONSE_FIELD):
    """The retrieval-side ``response``: the clinical answer, not an ID roster.

    ``response_field`` SELECTS THE SAME TEXT THE GENERATION DATASET SCORES, and
    it applies here rather than being pinned to ``assessment`` so that a run's
    single recorded ``response_field`` is true of everything in it. Pinning this
    side would let a manifest stamped ``assessment_draft`` describe a retrieval
    response built from the composed assessment -- one field name over two
    different texts, which is the mixture this option exists to prevent.

    THE CIRCULARITY RULE THIS ENFORCES. A retrieved context must not be judged
    useful merely because its ID is echoed back in the response; a rejected
    trial has to earn its relevance verdict on CONTENT. Context precision asks
    the judge "was this context useful in arriving at this answer", so whatever
    goes in ``response`` defines what "useful" can mean.

    THE SUPERSEDED DESIGN, AND WHY IT WAS BIASED. The first version of this
    function listed EVERY verdicted trial as ``<nct_id>: <label>``. Every
    retrieved context's ID therefore appeared in the answer verbatim, including
    the IDs of trials the pipeline had REJECTED -- so a judge could mark any
    context useful on a string match alone, without reading a word of the trial
    text. The metric saturated exactly as that predicts: mean 0.9456 over 10
    patients with five of them at the metric's ceiling: 1.0000 to four
    decimals, which is really 0.99999999999... because Ragas divides by
    ``sum(verdicts) + 1e-10``, so an exact 1.0 is unreachable and no value in
    either run is exactly 1.0.

    WHAT THIS VERSION DOES. Only trials the pipeline verdicted eligible -- the
    matches -- contribute, each as its nct_id followed by that trial's
    assessment text. Rejected trials are absent from the response entirely, so
    the 63 of 126 retrieved trials that were rejected now have to be judged on
    whether their CONTENT helped reach this answer.

    THE RESIDUAL, STATED RATHER THAN GLOSSED. A matched trial's ID is still
    echoed, so a matched context can still be credited on the ID cue alone.
    That is a deliberate limit of this design and not an oversight: a matched
    trial genuinely IS the answer, and stripping its ID would leave assessments
    that name no trial. The bias is removed for rejected trials, which are the
    ones the saturated metric could not discriminate; it is unchanged for
    matched ones.

    Nothing is summarised or rephrased -- the assessment text is verbatim, so
    the metric scores the run rather than this harness's prose.

    Returns ``(text, matched_count)``. The count is returned rather than left
    to be recovered from the text, because a scored field containing a newline
    would make ``len(text.splitlines())`` over-count the matches.
    """
    lines = []
    for verdict in verdicts:
        nct_id = verdict.get("nct_id")
        if not nct_id:
            continue
        if verdict.get("eligible") != ELIGIBLE_LABEL:
            continue
        # `eligible` and `verdict_group` are two encodings of one decision --
        # node_finalize splits eligible/not_eligible into matches/near_misses
        # -- and they agree on all 126 verdicts of the reference run. Keyed on
        # `eligible` and cross-checked rather than assumed, so a future
        # divergence is a recorded problem instead of a silent mis-selection.
        group = verdict.get("verdict_group")
        if group is not None and group != "matches" and problems is not None:
            problems.append(f"{patient_id}/{nct_id}: eligible="
                            f"{verdict.get('eligible')!r} but verdict_group="
                            f"{group!r}; treated as a match on `eligible`")
        assessment = (verdict.get(response_field) or "").strip()
        lines.append(f"{nct_id}: {assessment}" if assessment else f"{nct_id}")
    if not lines:
        return NO_MATCH_RESPONSE, 0
    return "\n".join(lines), len(lines)


def load_run(run_dir, response_field=DEFAULT_RESPONSE_FIELD):
    """Read a run directory into both datasets, in a deterministic order.

    Every path comes from the manifest's own ``runs`` table rather than from a
    directory glob, so a stray JSON file beside the records cannot be read as a
    patient and a record the manifest names but which is missing is a recorded
    problem rather than a silently shorter dataset. Same shape as
    ``rater.load_run``, for the same reasons.

    ``response_field`` NAMES THE RECORDED TEXT BOTH DATASETS SCORE, and a
    verdict that does not CARRY that key is a refusal -- see
    RESPONSE_FIELD_MISSING_NOTE. The distinction enforced here is between a key
    that is ABSENT (the artifact predates the field: structural, refuse before
    anything is priced or spent) and a key that is present but EMPTY (this one
    trial has no text: recorded as a problem, no sample built, which is what
    the harness already did for an empty assessment). Collapsing them would let
    a whole pre-1.5.0 artifact read as "126 empty assessments" and score
    nothing while exiting 0.
    """
    if response_field not in RESPONSE_FIELDS:
        raise RagasRefusal(
            f"unknown response field {response_field!r}; this harness scores "
            f"one of {', '.join(RESPONSE_FIELDS)}. Every verdict would read as "
            f"missing and the refusal would blame the artifact.",
            code="response_field_unknown")

    manifest_path = os.path.join(run_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        raise RagasRefusal(
            f"no manifest.json under {run_dir!r}. --run-dir must name an "
            f"evaluation run directory.", code="run_dir_invalid")
    with io.open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    runs = manifest.get("runs")
    if not isinstance(runs, dict) or not runs:
        raise RagasRefusal(f"{manifest_path!r} carries no 'runs' table.",
                           code="run_dir_invalid")

    retrieval = []
    generation = []
    problems = []
    # The missing-key scan accumulates ACROSS patients and is raised once, at
    # the end, naming the count and a bounded sample. Raising on the first hit
    # would tell an operator that one trial lacks the field when the truth is
    # that the whole artifact does, and they would fix it one verdict at a time.
    missing_field = []
    census = {"verdicts_seen": 0, "both_fields_present": 0,
              "identical": 0, "differing": 0}

    for patient_id in sorted(runs.keys()):
        entry = runs[patient_id]
        filename = entry.get("file")
        if not filename:
            problems.append(f"{patient_id}: manifest entry names no file")
            continue
        record_path = os.path.join(run_dir, filename)
        if not os.path.isfile(record_path):
            problems.append(f"{patient_id}: {filename} named by the manifest "
                            f"is not on disk")
            continue
        with io.open(record_path, "r", encoding="utf-8") as fh:
            record = json.load(fh)

        summary = (record.get("patient_summary") or {}).get("text")
        if not summary or not summary.strip():
            problems.append(f"{patient_id}: empty patient_summary.text -- "
                            f"neither dataset can be built for this patient")
            continue

        contexts = []
        for context in record.get("contexts") or []:
            text = context.get("trial_text")
            nct_id = context.get("nct_id")
            if not text or not text.strip():
                problems.append(f"{patient_id}/{nct_id}: empty trial_text; "
                                f"dropped from retrieved_contexts")
                continue
            contexts.append((context.get("rank"), nct_id, text))
        # Ranked order is load-bearing: context precision is an AVERAGE
        # PRECISION, which is a function of where each useful context sits in
        # the list. A rank of None sorts last rather than raising, and is
        # recorded as a problem so it cannot pass unnoticed.
        if any(rank is None for rank, _, _ in contexts):
            problems.append(f"{patient_id}: at least one context carries no "
                            f"rank; those sort last")
        contexts.sort(key=lambda item: (item[0] is None, item[0]))

        verdicts = record.get("verdicts") or []
        if contexts and verdicts:
            match_text, matched_count = _match_response(
                verdicts, problems, patient_id, response_field)
            retrieval.append(RetrievalSample(
                patient_id=patient_id,
                user_input=summary,
                retrieved_contexts=[text for _, _, text in contexts],
                response=match_text,
                response_field=response_field,
                matched_count=matched_count))
        else:
            problems.append(f"{patient_id}: no retrieval sample built "
                            f"({len(contexts)} contexts, {len(verdicts)} "
                            f"verdicts)")

        by_nct = {nct_id: text for _, nct_id, text in contexts}
        for verdict in verdicts:
            nct_id = verdict.get("nct_id")
            if not nct_id:
                problems.append(f"{patient_id}: a verdict carries no nct_id")
                continue
            # A CENSUS OF THE TWO FIELDS, TAKEN WHILE THE RECORD IS OPEN. Read
            # for every verdict whatever field is being scored, because "how
            # many drafts are byte-identical to their composed assessment" is
            # the population statement any draft-side figure has to be read
            # over, and a figure whose denominator comes from a side script is
            # a figure with two provenances.
            census["verdicts_seen"] += 1
            if (RESPONSE_FIELD_ASSESSMENT in verdict
                    and RESPONSE_FIELD_DRAFT in verdict):
                census["both_fields_present"] += 1
                if (verdict.get(RESPONSE_FIELD_ASSESSMENT)
                        == verdict.get(RESPONSE_FIELD_DRAFT)):
                    census["identical"] += 1
                else:
                    census["differing"] += 1

            if response_field not in verdict:
                # ABSENT KEY: structural, and collected rather than raised
                # here. Never falls back to the other field.
                missing_field.append(f"{patient_id}/{nct_id}")
                continue
            assessment = (verdict.get(response_field) or "").strip()
            if not assessment:
                # Stated scope: one sample per verdicted trial WITH a non-empty
                # response field. An empty one is recorded, never scored --
                # both metrics take the response as their subject and there is
                # nothing here to be faithful or relevant.
                problems.append(f"{patient_id}/{nct_id}: empty "
                                f"{response_field}; no generation sample built")
                continue
            trial_text = by_nct.get(nct_id)
            if not trial_text:
                problems.append(f"{patient_id}/{nct_id}: verdicted trial has "
                                f"no context text in this record; no "
                                f"generation sample built")
                continue
            generation.append(GenerationSample(
                patient_id=patient_id,
                nct_id=nct_id,
                user_input=GENERATION_QUESTION_TEMPLATE.format(nct_id=nct_id),
                # The patient summary MUST be in contexts. Faithfulness checks
                # every statement in the assessment against the contexts, and
                # every assessment references patient facts -- without the
                # summary each of those is unsupported by construction and the
                # metric would measure the harness's context choice rather than
                # the pipeline.
                retrieved_contexts=[summary, trial_text],
                # WHAT THIS RESPONSE IS CHANGED AT PROMPT_VERSION 1.5.0, AND
                # THE METRIC'S MEANING CHANGED WITH IT. For an eligible or
                # not_eligible trial, `assessment` is no longer the model's
                # free-written reasoning: it is composed from that trial's own
                # criteria rows (oncotriage/agent/evaluation.py:
                # compose_assessment), quoting each `criterion` (which restates
                # the trial text) and each `patient_value` (which comes from the
                # patient record) VERBATIM. Both of this sample's contexts are
                # therefore the sources the response was built from, so
                # faithfulness over a 1.5.0 run measures the RENDERER and is
                # expected near its ceiling -- a metric that cannot fail is not
                # a measurement. Runs either side of 1.5.0 are not comparable
                # on this metric.
                #
                # The model's own reasoning is still available: `collect_verdicts`
                # copies each verdict verbatim, so a 1.5.0 run artifact carries
                # `assessment_draft`. Scoring THAT is what keeps this metric
                # about the pipeline. It is deliberately not switched here --
                # pre-1.5.0 artifacts have no such key, so the switch needs a
                # stated fallback and a note on every published figure, which is
                # a measurement decision rather than an edit.
                response=assessment,
                eligible=verdict.get("eligible"),
                verdict_group=verdict.get("verdict_group"),
                response_field=response_field))

        declared = entry.get("verdicts")
        n_here = sum(1 for s in generation if s.patient_id == patient_id)
        if isinstance(declared, int) and declared != n_here:
            problems.append(f"{patient_id}: manifest declares {declared} "
                            f"verdicts, {n_here} generation samples were built")

    # RAISED BEFORE the empty-datasets check, and before pricing, so an
    # artifact that predates the requested field refuses with the reason it
    # actually has rather than with "no samples read". Never a fallback: see
    # RESPONSE_FIELD_MISSING_NOTE.
    if missing_field:
        shown = ", ".join(missing_field[:5])
        more = ("" if len(missing_field) <= 5
                else f" (and {len(missing_field) - 5} more)")
        raise RagasRefusal(
            f"{len(missing_field)} of {census['verdicts_seen']} verdict(s) in "
            f"{run_dir!r} carry no {response_field!r} key: {shown}{more}. "
            f"{RESPONSE_FIELD_MISSING_NOTE}",
            code="response_field_missing")

    if not retrieval and not generation:
        raise RagasRefusal(
            f"no samples read from {run_dir!r}. Problems: "
            + ("; ".join(problems) if problems else "none reported"),
            code="empty_datasets")

    retrieval.sort(key=lambda s: s.key)
    generation.sort(key=lambda s: s.key)
    return RunInput(run_dir, manifest, retrieval, generation, problems,
                    response_field, census)


def apply_limit(run, limit):
    """Keep the first N samples of EACH dataset, for a smoke run.

    Per dataset rather than overall, because the two datasets are two orders of
    magnitude apart in size and a single budget would make the smoke exercise
    only one of them.
    """
    if not limit or limit <= 0:
        return run
    return RunInput(run.run_dir, run.manifest, run.retrieval[:limit],
                    run.generation[:limit], run.problems,
                    run.response_field, run.field_census)


#------------------------------------------------------------------------------
# Pricing
#------------------------------------------------------------------------------


def judge_pricing(model):
    """Per-token USD rates for the judge at STANDARD prices, or raise.

    Reuses ``config.RATER_PRICING`` -- one pricing table for the Anthropic
    vendor, not a second one to drift -- and deliberately does NOT apply its
    ``batch_discount``: Ragas drives the judge synchronously and the Message
    Batches API is not on this path, so a discounted figure here would
    under-report every run by half.

    Never returns a zero rate for an unpriced model, on the argument
    ``get_model_cost`` makes in ``oncotriage/utils.py``: a zero-cost row is
    indistinguishable from a genuinely free run and every aggregate over it
    under-reports silently.
    """
    entry = config.RATER_PRICING.get("models", {}).get(model)
    if entry is None:
        raise RagasRefusal(
            f"no pricing recorded for judge model {model!r}. Add it to "
            f"config.RATER_PRICING before spending anything; a run priced at "
            f"zero would under-report by exactly its own cost.",
            code="model_unpriced")
    return {"input": entry["input_per_mtok"] / 1e6,
            "output": entry["output_per_mtok"] / 1e6,
            "pricing_version": config.RATER_PRICING["last_updated"]}


def embedding_pricing(model):
    """Per-token USD rate for the embedding model, or raise. Same rule."""
    entry = config.PRICING_CONFIG.get("models", {}).get(model)
    if entry is None:
        raise RagasRefusal(
            f"no pricing recorded for embedding model {model!r}. Add it to "
            f"config.PRICING_CONFIG before spending anything.",
            code="model_unpriced")
    return {"input": entry["input"] / 1e6,
            "pricing_version": config.PRICING_CONFIG["last_updated"]}


#------------------------------------------------------------------------------
# The usage tally -- the ONE place spend is measured
#------------------------------------------------------------------------------


class UsageTally(object):
    """Token counts accumulated from the vendors' own usage objects.

    WHY THIS EXISTS AT ALL. Ragas' ``InstructorLLM.agenerate`` returns the
    parsed Pydantic model and throws the raw response away, so there is no
    usage anywhere in the value this harness gets back. Estimating spend from
    characters afterwards would be inventing a measurement. The instance-level
    patch in ``build_judge`` / ``build_embeddings`` is what makes the reported
    cost MEASURED rather than modelled.

    Not thread-locked, and that is argued rather than overlooked: every
    increment happens inside the single asyncio event loop this harness runs,
    so the read-modify-write is never preempted mid-sequence. A future thread
    pool over these calls would need a lock here.
    """

    def __init__(self):
        self.judge_calls = 0
        self.judge_input_tokens = 0
        self.judge_output_tokens = 0
        self.embedding_calls = 0
        self.embedding_tokens = 0

    def record_judge(self, usage):
        self.judge_calls += 1
        if usage is None:
            return
        self.judge_input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.judge_output_tokens += getattr(usage, "output_tokens", 0) or 0

    def record_embedding(self, usage):
        self.embedding_calls += 1
        if usage is None:
            return
        self.embedding_tokens += getattr(usage, "total_tokens", 0) or 0

    def cost(self, judge_model, embedding_model):
        judge = judge_pricing(judge_model)
        embed = embedding_pricing(embedding_model)
        judge_usd = (self.judge_input_tokens * judge["input"]
                     + self.judge_output_tokens * judge["output"])
        embed_usd = self.embedding_tokens * embed["input"]
        return {
            "judge_calls": self.judge_calls,
            "judge_input_tokens": self.judge_input_tokens,
            "judge_output_tokens": self.judge_output_tokens,
            "judge_usd": round(judge_usd, 6),
            "embedding_calls": self.embedding_calls,
            "embedding_tokens": self.embedding_tokens,
            "embedding_usd": round(embed_usd, 6),
            "total_usd": round(judge_usd + embed_usd, 6),
            "judge_pricing_version": judge["pricing_version"],
            "embedding_pricing_version": embed["pricing_version"],
            "rate_basis": "standard (non-batch)",
            "measured": True,
        }


#------------------------------------------------------------------------------
# The judge and the embedder
#------------------------------------------------------------------------------


RAGAS_DO_NOT_TRACK = "RAGAS_DO_NOT_TRACK"


def disable_third_party_telemetry():
    """Turn Ragas' usage analytics off, unless the operator turned it on.

    RAGAS POSTS A USAGE EVENT TO A THIRD-PARTY ENDPOINT AFTER EVERY SINGLE
    JUDGE CALL, and it does it with a SYNCHRONOUS ``requests.post`` from inside
    ``InstructorLLM.agenerate`` (``ragas/_analytics.py:track`` ->
    ``https://t.explodinggradients.com``). Two consequences, the second of
    which is what makes this non-optional:

    1. Telemetry leaves the machine on a run over clinical records. The payload
       measured here carries provider, model, llm_type, num_requests and
       is_async -- no patient text -- but a clinical-data harness should not be
       calling an unrelated third party at all without saying so.
    2. It BLOCKS THE EVENT LOOP. ``requests`` is synchronous, so the post does
       not merely delay its own call, it stalls every other coroutine
       scheduled on the loop, and concurrency stops buying anything. Measured
       on this run, same inputs, same 3 workers: 439.7s with telemetry on,
       44.2s with it off -- 9.9x -- against an API whose own measured latency
       for prompts of this size is ~2s per call, four of them in parallel in
       2.2s total, with rate-limit headers reporting the account barely
       touched. The scores were unchanged (context precision 1.0000 and
       faithfulness 1.0000 both runs; relevancy 0.4707 vs 0.4713, which is the
       judge's own question-generation variance).

    Set only when unset, and the choice is reported, on the precedent of
    ``oncotriage/tracking.py`` setting MLflow's file-store opt-out: an operator
    who exports the variable themselves keeps whatever they chose.
    """
    if os.environ.get(RAGAS_DO_NOT_TRACK):
        return os.environ[RAGAS_DO_NOT_TRACK], "operator"
    os.environ[RAGAS_DO_NOT_TRACK] = "true"
    return "true", "harness default"


def resolve_api_key(name):
    """One credential, from the environment or the project's .env, or raise."""
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    # load_env_keys() populates os.environ from 05- Keys/.env and is the one
    # place this project reads credentials from disk.
    paths.load_env_keys()
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    raise RagasRefusal(
        f"{name} is not set and was not found in the project's .env. This "
        f"harness cannot run without it.", code="missing_credentials")


def build_judge(model, temperature, max_tokens, tally, max_retries):
    """The Ragas judge LLM, wired to Anthropic, with usage recorded.

    THE ``top_p`` REMOVAL IS REQUIRED, NOT A PREFERENCE, AND IT IS ASSERTED.
    Ragas builds every InstructorLLM from ``InstructorModelArgs``, whose
    defaults are ``temperature=0.01`` AND ``top_p=0.1``, and it sends both on
    every request. Claude 4-family models reject that pair outright:

        400 invalid_request_error -- `temperature` and `top_p` cannot both be
        specified for this model. Please use only one.

    Measured against the live API on 2026-08-11, not inferred: with both, every
    single request fails and the harness scores nothing; with ``top_p`` popped
    and temperature 0.0, the identical call succeeds. So this is the one place
    this module reaches into a Ragas object's state, and the assertion below is
    what turns a future Ragas change into a named failure here instead of a run
    that 400s on every sample.
    """
    from anthropic import AsyncAnthropic
    from ragas.llms import llm_factory

    client = AsyncAnthropic(api_key=resolve_api_key("ANTHROPIC_API_KEY"),
                            max_retries=max_retries)

    # The usage seam. ``client.messages`` is a cached_property, so this patches
    # the one object every later call goes through, and instructor's
    # ``from_anthropic`` reaches it at request time. Verified by identity
    # immediately below rather than assumed.
    real_create = client.messages.create

    async def recording_create(*args, **kwargs):
        response = await real_create(*args, **kwargs)
        tally.record_judge(getattr(response, "usage", None))
        return response

    client.messages.create = recording_create
    if client.messages.create is not recording_create:
        raise RagasRefusal(
            "could not install the usage recorder on the Anthropic client, so "
            "every cost this run reported would be zero while real money was "
            "spent. Refusing to run.", code="usage_seam_failed")

    llm = llm_factory(model, provider="anthropic", client=client,
                      temperature=temperature, max_tokens=max_tokens)
    llm.model_args.pop("top_p", None)
    if "top_p" in llm.model_args:
        raise RagasRefusal(
            "ragas kept top_p in the judge's model_args alongside temperature; "
            "this model rejects that pair and every request would 400.",
            code="top_p_not_removed")
    if llm.model_args.get("temperature") != temperature:
        raise RagasRefusal(
            f"judge temperature is {llm.model_args.get('temperature')!r}, not "
            f"the requested {temperature!r}.", code="temperature_not_applied")
    return llm


def build_embeddings(model, tally):
    """The Ragas embedder, wired to OpenAI, with usage recorded.

    THE ONE OPENAI DEPENDENCY IN THIS MODULE, and it is an embedder rather than
    a judge: ``ResponseRelevancy`` scores cosine similarity between the real
    question and questions the JUDGE reverse-engineered, so something has to
    turn both into vectors. It renders no verdict and reads no criterion, so
    the different-family separation this harness exists to preserve is intact.
    """
    from openai import AsyncOpenAI
    from ragas.embeddings.base import embedding_factory

    client = AsyncOpenAI(api_key=resolve_api_key("OPENAI_API_KEY"))

    real_create = client.embeddings.create

    async def recording_create(*args, **kwargs):
        response = await real_create(*args, **kwargs)
        tally.record_embedding(getattr(response, "usage", None))
        return response

    client.embeddings.create = recording_create
    if client.embeddings.create is not recording_create:
        raise RagasRefusal(
            "could not install the usage recorder on the OpenAI client.",
            code="usage_seam_failed")

    return embedding_factory("openai", model=model, client=client,
                             interface="modern")


def build_metrics(llm, embeddings, selected=ALL_METRICS):
    """The selected metric objects, keyed by the names this harness reports.

    Only what was asked for is constructed. ``embeddings`` may be None when no
    selected metric needs one, and passing None while response relevancy IS
    selected is a refusal rather than a late AttributeError inside the first
    scored sample.
    """
    from ragas.metrics.collections import (AnswerRelevancy,
                                           ContextPrecisionWithoutReference,
                                           Faithfulness)
    chosen = set(selected)
    if METRIC_RESPONSE_RELEVANCY in chosen and embeddings is None:
        raise RagasRefusal(
            "response relevancy was selected but no embedder was built; it "
            "cannot be scored without one.", code="embeddings_missing")
    built = {}
    if METRIC_CONTEXT_PRECISION in chosen:
        built[METRIC_CONTEXT_PRECISION] = ContextPrecisionWithoutReference(
            llm=llm)
    if METRIC_FAITHFULNESS in chosen:
        built[METRIC_FAITHFULNESS] = Faithfulness(llm=llm)
    if METRIC_RESPONSE_RELEVANCY in chosen:
        built[METRIC_RESPONSE_RELEVANCY] = AnswerRelevancy(
            llm=llm, embeddings=embeddings, strictness=RELEVANCY_STRICTNESS)
    return built


#------------------------------------------------------------------------------
# The dry run: call counts and a priced range
#------------------------------------------------------------------------------


# Only used by --dry-run, and only for the token figures it labels as
# estimates. Nothing that is actually spent is computed from these.
CHARS_PER_TOKEN = 3.6

# CALIBRATED AGAINST TWO MEASURED RUNS OVER eval_run_20260817_195423 (1.9.0),
# 2026-08-17. Before this, the dry run was wrong in the same direction on both
# metrics and by enough to make a spend gate useless: context precision came in
# at 1.30x its midpoint and faithfulness at 1.83x, over even the high bound of
# its own range.
#
# THE INPUT MISS IS ONE CAUSE AND IT IS SHARED. The old estimate counted only
# the text this harness supplies, and named what it was leaving out ("exclude
# Ragas' prompt-template overhead and few-shot examples") without pricing it.
# Measured, that omission is nearly the same size per call on two structurally
# different metrics -- 2,062 tokens/call on context precision and 1,797 on
# faithfulness -- because it is dominated by a fixed instruction block and
# few-shot examples rather than by anything about the sample. So it is ONE
# constant applied per judge call, not a per-metric fudge factor.
PROMPT_TEMPLATE_TOKENS_PER_CALL = 1900
"""Ragas' own instruction block and few-shot examples, per judge call.

Measured at 2,062 (context precision) and 1,797 (faithfulness); 1900 sits
between them. It is deliberately NOT tuned to reproduce either exactly -- one
number that is slightly wrong for both is honest about being a constant, where
two numbers fitted per metric would be a curve drawn through two points and read
as precision this has not got.
"""

# THE OUTPUT MISS HAS TWO DIFFERENT CAUSES, so it has two different fixes.
#
# Context precision emits one {reason, verdict} per context and the reason is
# short and roughly fixed, so a flat per-call constant is the RIGHT SHAPE and
# only the number was wrong: 120 modelled against 201 measured.
#
# Faithfulness is the one that was structurally wrong. It emits the extracted
# STATEMENTS on the first call and one {statement, reason, verdict} on the
# second -- both proportional to how much text there is to decompose -- so a
# flat per-call constant cannot track the response at all. That is not academic
# here: the composed `assessment` and the model's `assessment_draft` over the
# same run differ by 3.6x in length (142,358 vs 39,511 characters), and a flat
# constant prices those two passes identically. See ESTIMATED_OUTPUT_PER_RESPONSE_TOKEN.
ESTIMATED_OUTPUT_TOKENS = {
    METRIC_CONTEXT_PRECISION: 201,   # a short reason plus a 0/1 verdict; MEASURED
    METRIC_RESPONSE_RELEVANCY: 60,   # one question plus a flag; NOT calibrated --
                                     # this metric has not been re-measured since
                                     # the constants were first guessed, and it is
                                     # named here rather than left to look measured
}

ESTIMATED_OUTPUT_PER_RESPONSE_TOKEN = {
    METRIC_FAITHFULNESS: 5.221,
}
"""Output tokens per token of RESPONSE, for metrics that decompose the response.

Calibrated from one run: 206,463 output tokens (measured, scaled from 237
billed calls to the 252 planned) over 39,544 response tokens. The ratio exceeds
1 because each extracted statement comes back a second time inside an NLI
verdict object carrying a prose reason.

ONE CALIBRATION POINT IS NOT A CURVE, and the estimate range around it
(ESTIMATE_LOW/ESTIMATE_HIGH) is what carries that uncertainty. What the model
buys over the flat constant is not precision, it is the right SHAPE: it moves
when the scored text moves, which is the only reason the two faithfulness
passes over one run get different prices.
"""
# The dry run reports a RANGE rather than a number, because the input estimate
# is characters-per-token and the output estimate is a constant. Both bounds
# are printed so nobody reads the midpoint as a measurement.
ESTIMATE_LOW = 0.7
ESTIMATE_HIGH = 1.6


def plan_calls(run, active):
    """How many judge and embedding calls each dataset will make, at minimum.

    These counts are DERIVED from the metric implementations rather than
    estimated: context precision issues one judge call per retrieved context,
    faithfulness two per sample (statement extraction, then NLI), and response
    relevancy ``strictness`` judge calls plus two embedding calls per sample.

    THEY ARE A LOWER BOUND, NOT AN EXACT FIGURE, and the difference is
    instructor's: a call whose structured output fails Pydantic validation is
    re-issued underneath ragas, and the retry is a second billed request this
    arithmetic cannot see. Measured on the full 136-sample run: 756 planned,
    758 actually billed -- 0.26% over. The manifest records both numbers side
    by side (``planned_calls`` and ``cost.judge_calls``) precisely so the gap
    is visible rather than reconciled away.
    """
    contexts = sum(len(s.retrieved_contexts) for s in run.retrieval)
    n_gen = len(run.generation)
    selected = {m for metrics in active.values() for m in metrics}
    whole = {
        METRIC_CONTEXT_PRECISION: {
            "samples": len(run.retrieval),
            "judge_calls": contexts,
            "embedding_calls": 0,
            "basis": "one judge call per retrieved context",
        },
        METRIC_FAITHFULNESS: {
            "samples": n_gen,
            "judge_calls": n_gen * 2,
            "embedding_calls": 0,
            "basis": "two judge calls per sample (statements, then NLI)",
        },
        METRIC_RESPONSE_RELEVANCY: {
            "samples": n_gen,
            "judge_calls": n_gen * RELEVANCY_STRICTNESS,
            "embedding_calls": n_gen * 2,
            "basis": f"{RELEVANCY_STRICTNESS} judge calls (strictness) plus "
                     f"2 embedding calls per sample",
        },
    }
    return {name: entry for name, entry in whole.items() if name in selected}


def estimate_tokens(run, active):
    """A characters-based input estimate per metric. Labelled, never measured.

    The character counts are exact -- they come from the text that will really
    be sent -- and the tokens-per-character ratio and the output constants are
    not. Prompt template overhead is deliberately excluded and named as such
    rather than guessed at.
    """
    per_metric = {}

    chars = sum(len(s.user_input) + len(s.response)
                + sum(len(c) for c in s.retrieved_contexts)
                for s in run.retrieval)
    # Each context is judged in its own call, and the question and the response
    # ride along every time, so they are counted once per context.
    repeated = sum((len(s.user_input) + len(s.response))
                   * max(0, len(s.retrieved_contexts) - 1)
                   for s in run.retrieval)
    per_metric[METRIC_CONTEXT_PRECISION] = chars + repeated

    # Statement extraction sees question + response; NLI sees the joined
    # contexts plus the statements it just produced (excluded, unknown here).
    per_metric[METRIC_FAITHFULNESS] = sum(
        (len(s.user_input) + len(s.response))
        + sum(len(c) for c in s.retrieved_contexts)
        for s in run.generation)

    # Relevancy's prompt carries the response only; contexts are not sent.
    per_metric[METRIC_RESPONSE_RELEVANCY] = sum(
        len(s.response) * RELEVANCY_STRICTNESS for s in run.generation)

    # THE PROMPT TEMPLATE IS PART OF THE INPUT AND USED TO BE LEFT OUT. It is
    # priced per JUDGE CALL rather than per sample, because that is the unit it
    # is sent in -- context precision issues one call per context and
    # faithfulness two per sample, so a per-sample overhead would be wrong by
    # the fan-out factor on both.
    plan = plan_calls(run, active)
    selected = {m for metrics in active.values() for m in metrics}
    return {name: int(value / CHARS_PER_TOKEN
                      + plan[name]["judge_calls"]
                      * PROMPT_TEMPLATE_TOKENS_PER_CALL)
            for name, value in per_metric.items() if name in selected}


def estimate_output_tokens(metric, judge_calls, run):
    """Estimated output tokens for one metric, by the shape that metric has.

    Two shapes, and which one applies is a fact about the metric rather than a
    tuning choice: a metric that emits a fixed-size object per call is priced
    per call, and one that DECOMPOSES the response is priced against the
    response. Faithfulness is the second kind, which is why the composed
    assessment and the model's draft over the same run are not the same price.
    """
    per_response = ESTIMATED_OUTPUT_PER_RESPONSE_TOKEN.get(metric)
    if per_response is not None:
        response_chars = sum(len(s.response) for s in run.generation)
        return int(per_response * response_chars / CHARS_PER_TOKEN)
    return judge_calls * ESTIMATED_OUTPUT_TOKENS[metric]


def price_plan(run, judge_model, embedding_model, active):
    """The dry run's whole answer: counts, estimated tokens, a priced range."""
    judge = judge_pricing(judge_model)
    embed = embedding_pricing(embedding_model)
    plan = plan_calls(run, active)
    input_tokens = estimate_tokens(run, active)

    rows = []
    midpoint = 0.0
    for name in ALL_METRICS:
        entry = plan.get(name)
        if entry is None:
            continue
        tokens_in = input_tokens[name]
        tokens_out = estimate_output_tokens(name, entry["judge_calls"], run)
        usd = tokens_in * judge["input"] + tokens_out * judge["output"]
        # Relevancy embeds one question and `strictness` generated ones; the
        # generated text is not known before the run, so the embedded volume is
        # approximated by the response length.
        if entry["embedding_calls"]:
            embed_chars = sum(len(s.user_input) + len(s.response)
                              for s in run.generation)
            usd += (embed_chars / CHARS_PER_TOKEN) * embed["input"]
        midpoint += usd
        rows.append({"metric": name, "samples": entry["samples"],
                     "judge_calls": entry["judge_calls"],
                     "embedding_calls": entry["embedding_calls"],
                     "basis": entry["basis"],
                     "estimated_input_tokens": tokens_in,
                     "estimated_output_tokens": tokens_out,
                     "estimated_usd": round(usd, 4)})

    return {
        "metrics": rows,
        "judge_calls_total": sum(r["judge_calls"] for r in rows),
        "embedding_calls_total": sum(r["embedding_calls"] for r in rows),
        "estimated_usd_low": round(midpoint * ESTIMATE_LOW, 4),
        "estimated_usd_high": round(midpoint * ESTIMATE_HIGH, 4),
        "estimated_usd_midpoint": round(midpoint, 4),
        "rate_basis": "standard (non-batch)",
        "judge_pricing_version": judge["pricing_version"],
        "measured": False,
        "estimate_caveat": (
            "Call counts are DERIVED from the metric implementations and are "
            "a LOWER BOUND: instructor re-issues a call whose structured "
            "output fails validation, and that retry is billed but not "
            "counted here (measured: 756 planned, 758 billed). Token figures "
            "are estimated at "
            f"{CHARS_PER_TOKEN} chars/token over the exact text to be sent, "
            f"plus {PROMPT_TEMPLATE_TOKENS_PER_CALL} tokens per judge call for "
            "Ragas' own instruction block and few-shot examples, which were "
            "previously excluded and are the single largest term the estimate "
            "used to miss. Output is priced per call for metrics that emit a "
            "fixed-size object, and against the RESPONSE LENGTH for metrics "
            "that decompose the response (faithfulness), so scoring a short "
            "field and a long one over the same run are not priced the same. "
            "All three constants are CALIBRATED FROM ONE RUN "
            "(eval_run_20260817_195423, 2026-08-17); response relevancy's "
            "output constant is NOT calibrated and is still the original "
            "guess. Treat the range as an order of magnitude, and a completed "
            "pass's MEASURED cost as the number to project from."),
    }


#------------------------------------------------------------------------------
# Scoring
#------------------------------------------------------------------------------


class Score(object):
    """One (sample, metric) outcome. Scored or unscored -- never dropped."""

    __slots__ = ("dataset", "metric", "join", "value", "status", "reason",
                 "seconds")

    def __init__(self, dataset, metric, join, value, status, reason, seconds):
        self.dataset = dataset
        self.metric = metric
        self.join = join
        self.value = value
        self.status = status
        self.reason = reason
        self.seconds = seconds

    def as_row(self):
        row = dict(self.join)
        row.update({"dataset": self.dataset, "metric": self.metric,
                    "value": self.value, "status": self.status,
                    "reason": self.reason,
                    "seconds": round(self.seconds, 3)})
        return row


#------------------------------------------------------------------------------
# Incremental persistence -- the partial score file that makes --resume possible
#------------------------------------------------------------------------------

PARTIAL_BASENAME = "ragas_partial"
PARTIAL_SCHEMA_VERSION = 1
PARTIAL_KIND = "ragas_partial_scores"

# The facts whose change makes two scores incomparable. Compared as a whole
# dict; every key is named here so a reader can see the closed list rather than
# infer it from a comparison.
#
# WHY THESE AND NOT OTHERS. Each one changes what a judge RETURNS for identical
# input text: the four package versions own the metric prompts, the statement
# decomposition and both SDKs (see ENVIRONMENT_PACKAGES); the judge model,
# temperature and max_tokens are the judge; the embedding model is what response
# relevancy's cosine similarity is computed in; the response field decides which
# recorded text is the response at all; and the run directory is the identity of
# the evaluation run being scored.
#
# ``--limit`` and ``--metrics`` are deliberately NOT here. Neither changes what
# a score MEANS -- they change which pairs are in the plan, and pair membership
# is already decided per pair, by identity and fingerprint. Refusing on them
# would forbid the two most useful resumes there are: finishing a run that was
# started with a limit, and adding a metric to a set already scored for the
# others.
#
# THE RUN DIRECTORY IS IN HERE AND IS STILL NOT SUFFICIENT, which is why
# ``pair_fingerprint`` exists: a path is not the text at that path.
RESUME_IDENTITY_KEYS = ("packages", "judge_model", "judge_temperature",
                        "judge_max_tokens", "embedding_model",
                        "response_field", "run_dir")


def partial_path(out_dir, response_field=DEFAULT_RESPONSE_FIELD):
    """Where this pass's partial scores live.

    FIELD-AWARE, exactly as ``output_paths`` is and for the same reason: two
    passes over one run with different ``--response-field`` values are two
    different measurements, and a shared partial file would have the second
    resume from the first's scores under one metric name.
    """
    suffix = ("" if response_field == DEFAULT_RESPONSE_FIELD
              else f"__{response_field}")
    return os.path.join(out_dir, f"{PARTIAL_BASENAME}{suffix}.json")


def resume_identity(run, args, environment):
    """The environment two runs must share before their scores may be merged."""
    return {
        "packages": dict(environment["packages"]),
        "judge_model": args.judge_model,
        "judge_temperature": args.temperature,
        "judge_max_tokens": args.max_tokens,
        "embedding_model": args.embedding_model,
        "response_field": run.response_field,
        "run_dir": os.path.realpath(run.run_dir),
    }


def identity_disagreement(recorded, current):
    """What changed between a partial run's environment and this one's.

    Returns a list of human-readable strings; empty means they agree. A missing
    key in the recorded identity is a disagreement, never a pass: a partial file
    that does not state what it ran under cannot be shown to have run under
    this.
    """
    changed = []
    for key in RESUME_IDENTITY_KEYS:
        was = recorded.get(key, "<not recorded>") if isinstance(recorded, dict) \
            else "<not recorded>"
        now = current.get(key)
        if was != now:
            changed.append(f"{key}: {was!r} -> {now!r}")
    return changed


def journal_row(score, fingerprint):
    """One journal row. The join is its own object rather than spread.

    ``Score.as_row()`` spreads the join keys into the top level, which is right
    for a results file a human reads and wrong here: reconstructing a Score from
    it means GUESSING which top-level keys were join keys, and a guess that is
    wrong by one key silently re-scores or mis-attributes.

    A MODULE-LEVEL FUNCTION rather than a ``@staticmethod`` on ``ScoreJournal``.
    It reads no instance state, every other transformation in this module is a
    module-level function, and a decorated definition would enter
    ``tests/test_package_invariants.py``'s pinned decorator inventory -- a pin
    worth paying for a decorator that does something, and not for one that only
    says "this method ignores self".
    """
    return {
        "pair_key": pair_key(score.dataset, score.metric, score.join),
        "dataset": score.dataset,
        "metric": score.metric,
        "join": dict(score.join),
        "value": score.value,
        "status": score.status,
        "reason": score.reason,
        "seconds": round(score.seconds, 3),
        "inputs_sha256": fingerprint,
    }


class ScoreJournal(object):
    """Scores written to disk as they complete, so a crash loses the in-flight.

    ATOMIC ON EVERY WRITE, on ``oncotriage/batch/runner.py:save_checkpoint``'s
    precedent: the whole file is serialised to ``<path>.partial-tmp`` and moved
    over the target with ``os.replace``. A reader therefore sees the file before
    a pair or after it, never during -- which matters because the file that
    protects against a crash is the one file a crash must not be able to
    corrupt.

    THE TEMPORARY SUFFIX IS NOT ``.tmp``, and that is not cosmetic.
    ``post_checks`` fails the run on any ``.tmp`` left in the output directory,
    a check that exists to catch a torn ``write_json``. A journal temp file
    racing that check would fail a run for a file the journal was about to
    replace anyway, so it is named out of that namespace and cleaned up by the
    same ``os.replace``.

    THE COST IS A FULL REWRITE PER PAIR AND IT IS PAID DELIBERATELY. An append
    log would be cheaper and is not atomic against a partial line; the reference
    run is 738 pairs of a few hundred bytes, so the whole file is well under a
    megabyte and the rewrite is milliseconds against judge calls measured in
    seconds. Determinism and a file that can never be half-written beat the
    micro-optimisation, and the batch runner made the same trade for the same
    reason.
    """

    def __init__(self, path, identity, started_at_utc=None):
        self.path = path
        self.identity = identity
        self.started_at_utc = started_at_utc or _utc_now()
        self.rows = []
        self.writes = 0
        self.write_failures = {}

    def record(self, score, fingerprint):
        self.rows.append(journal_row(score, fingerprint))
        self.flush()

    def flush(self):
        tmp = self.path + ".partial-tmp"
        payload = {
            "schema_version": PARTIAL_SCHEMA_VERSION,
            "kind": PARTIAL_KIND,
            "started_at_utc": self.started_at_utc,
            "updated_at_utc": _utc_now(),
            "identity": self.identity,
            "note": ("Crash-recovery state for `ragas_run.py --resume`. It is "
                     "NOT a result: it holds only the pairs completed so far, "
                     "carries no summary and no post-checks, and is deleted "
                     "when the run it belongs to completes. A pair is reused "
                     "only if this file's `identity` matches the resuming run "
                     "AND that pair's `inputs_sha256` still matches the text "
                     "the metric would be handed today."),
            "scores": self.rows,
        }
        try:
            with io.open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=1, ensure_ascii=False)
            os.replace(tmp, self.path)
            self.writes += 1
        except OSError as exc:
            # COUNTED, NEVER SWALLOWED, AND NEVER FATAL. The run is mid-flight
            # and every pair already scored has been PAID FOR; killing it over a
            # failure to write the recovery file would destroy exactly what the
            # file exists to protect. The count is reported at the end, so an
            # operator learns that --resume will not have everything before they
            # rely on it.
            key = f"{type(exc).__name__}"
            self.write_failures[key] = self.write_failures.get(key, 0) + 1
            if len(self.write_failures) == 1 and self.write_failures[key] == 1:
                console.out(f"  WARNING: the partial score file could not be "
                            f"written ({type(exc).__name__}: {exc}). Scoring "
                            f"continues; --resume will not see these pairs.")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                # The temp file could not be removed either. Same argument, one
                # level down; it is inside the output directory, is not named
                # ``.tmp`` so it cannot fail post_checks, and the next flush
                # replaces it.
                self.write_failures["tmp_unlink"] = (
                    self.write_failures.get("tmp_unlink", 0) + 1)

    def discard(self):
        """Remove the partial file. Called only after the run has COMPLETED.

        A partial file left beside a finished result would let the next
        ``--resume`` claim work it had not verified, and would sit in the output
        directory looking like a deliverable. Removal failures are reported and
        do not fail the run: the results and manifest are already written.
        """
        for target in (self.path, self.path + ".partial-tmp"):
            try:
                if os.path.exists(target):
                    os.remove(target)
            except OSError as exc:
                console.out(f"  WARNING: could not remove {target} "
                            f"({type(exc).__name__}: {exc}); a later --resume "
                            f"would read a partial file for a completed run.")


def load_partial(path):
    """The partial file at ``path``, or ``(None, reason)`` if it is unusable.

    Returns ``(payload, None)`` on success. Every failure is a reason string and
    nothing raises: the caller decides whether an unusable partial is a refusal
    (``--resume`` was asked for) or a note (it was not).
    """
    if not os.path.exists(path):
        return None, "no partial score file"
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception as exc:                                # noqa: BLE001
        return None, f"unreadable ({type(exc).__name__}: {exc})"
    if not isinstance(payload, dict):
        return None, f"malformed (top level is {type(payload).__name__})"
    if payload.get("kind") != PARTIAL_KIND:
        return None, f"not a partial score file (kind={payload.get('kind')!r})"
    if payload.get("schema_version") != PARTIAL_SCHEMA_VERSION:
        return None, (f"written at schema_version "
                      f"{payload.get('schema_version')!r}; this code reads "
                      f"{PARTIAL_SCHEMA_VERSION}")
    if not isinstance(payload.get("scores"), list):
        return None, "malformed (no scores list)"
    return payload, None


def reusable_scores(payload, run, active, identity):
    """Which paid pairs in a partial file this run may keep.

    Returns ``(by_pair_key, report)``. ``report`` counts every row and says what
    happened to it, because a resume that quietly drops paid work is the same
    defect as one that quietly keeps invalid work.

    THREE WAYS A ROW IS NOT REUSED, and each is counted separately:

      - ``not_in_plan``: the pair is not in this run's plan at all. That is the
        ordinary consequence of resuming with a different ``--metrics`` or a
        smaller ``--limit``, and it is not an error -- but it is money already
        spent that this run's results will not contain, so it is reported.
      - ``stale``: the pair IS in the plan and the text it was scored against
        has changed. Re-scored.
      - ``duplicate``: two rows for one pair key. The LAST wins, matching the
        journal's append order, and the earlier one is counted.

    An UNSCORED row is reused exactly like a scored one. It is a recorded
    outcome -- ``_score_one`` never drops and never coerces to 0.0 -- and
    re-running it would spend money to re-derive a result the harness already
    holds. A user who believes an unscored pair was a transient fault deletes
    the partial file, or the row, and re-runs.
    """
    samples_for = {DATASET_RETRIEVAL: run.retrieval,
                   DATASET_GENERATION: run.generation}
    planned = {}
    plan_duplicates = 0
    for dataset, metric_names in active.items():
        for metric_name in metric_names:
            for sample in samples_for[dataset]:
                key = pair_key(dataset, metric_name, sample.as_join())
                # TWO SAMPLES CAN SHARE A PAIR KEY, and ``load_run`` does not
                # dedupe: a run artifact carrying two verdicts for one
                # (patient, trial) builds two GenerationSamples with one join.
                # That is already indistinguishable in ``ragas_results.json``,
                # which keys its rows the same way -- but resume must not make
                # it WORSE by handing both occurrences one paid score. Counted
                # here, and consumed once in ``score_all``, so the second
                # occurrence is re-scored rather than cloned.
                if key in planned:
                    plan_duplicates += 1
                planned[key] = pair_fingerprint(metric_name, sample)

    keep = {}
    report = {"rows": 0, "reused": 0, "not_in_plan": 0, "stale": 0,
              "duplicate": 0, "malformed": 0,
              "plan_duplicate_keys": plan_duplicates,
              "identity": dict(identity)}
    for row in payload.get("scores") or []:
        report["rows"] += 1
        if not isinstance(row, dict) or not row.get("pair_key"):
            report["malformed"] += 1
            continue
        key = row["pair_key"]
        if key not in planned:
            report["not_in_plan"] += 1
            continue
        if row.get("inputs_sha256") != planned[key]:
            report["stale"] += 1
            continue
        if key in keep:
            report["duplicate"] += 1
        keep[key] = row
    report["reused"] = len(keep)
    return keep, report


def score_from_row(row):
    """Rebuild a ``Score`` from a journal row, so the merged set is one type.

    Merging dicts into a list of Scores would make every consumer --
    ``summarize``, ``build_results``, ``post_checks`` -- test which it had. One
    type in means those three are byte-for-byte the code a single-pass run runs,
    which is what "indistinguishable in shape from a single-pass run" has to
    mean to be checkable.
    """
    return Score(row["dataset"], row["metric"], dict(row.get("join") or {}),
                 row.get("value"), row.get("status"), row.get("reason"),
                 float(row.get("seconds") or 0.0))


#------------------------------------------------------------------------------


def metric_kwargs(metric_name, sample):
    """Exactly what this metric is handed for this sample.

    ONE CONSTRUCTION SITE, and that is the whole reason it is a function.
    ``_score_one`` calls it to score, and ``pair_fingerprint`` calls it to
    decide whether an already-paid score is still about this sample. Two copies
    would be two answers to "what does this metric read", and they would differ
    in exactly the direction that costs money: a fingerprint over MORE than the
    metric reads re-scores pairs that were still valid, and one over LESS
    carries forward a score about text that has changed. Same argument as the
    single BM25 construction site in ``oncotriage/embedding.py``.
    """
    kwargs = {"user_input": sample.user_input, "response": sample.response}
    if metric_name != METRIC_RESPONSE_RELEVANCY:
        # Response relevancy's ascore takes no contexts -- it compares the real
        # question to questions generated from the response alone.
        kwargs["retrieved_contexts"] = list(sample.retrieved_contexts)
    return kwargs


def pair_key(dataset, metric_name, join):
    """The identity of one (sample, metric) pair, as one string.

    Built from the JOIN KEYS rather than from a list index, because ``--limit``
    and a re-read of the run directory both change positions while leaving
    identities alone -- and a resume that matched on position would carry one
    patient's score onto another patient.
    """
    return "|".join([dataset, metric_name]
                    + [f"{k}={join[k]}" for k in sorted(join)])


def pair_fingerprint(metric_name, sample):
    """sha256 over exactly the text this metric will judge for this sample.

    THIS IS WHAT MAKES RESUME SAFE ACROSS A CHANGED RUN DIRECTORY, and it is
    strictly stronger than comparing the run directory's path. Two runs can
    name the same directory and hold different text -- an evaluation run
    re-generated in place, a ``--response-field`` whose verdicts were rewritten
    -- and a score is about TEXT, not about a path. A path check would pass and
    the mean would be over two different populations.

    It is also what lets ``--limit`` and ``--metrics`` change between the
    interrupted run and the resumed one without refusing: validity is decided
    per pair, so a pair that is still in the plan and still about the same text
    is reused whatever else moved around it.
    """
    payload = json.dumps(metric_kwargs(metric_name, sample),
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _score_one(metric_name, metric, sample, dataset, semaphore):
    """Score one sample with one metric. Never raises; records instead.

    A per-sample failure is recorded as UNSCORED with the exception type and
    message, and a NaN -- which Ragas returns when a metric produced no
    statements or no questions to work with -- is recorded as unscored with its
    own reason. Neither is silently dropped and neither is coerced to 0.0: a
    zero is a real score this scale can produce, and writing one for a sample
    that was never judged would move every aggregate over it.
    """
    kwargs = metric_kwargs(metric_name, sample)

    started = time.monotonic()
    async with semaphore:
        try:
            result = await metric.ascore(**kwargs)
            value = float(result.value)
        except Exception as exc:                       # noqa: BLE001
            return Score(dataset, metric_name, sample.as_join(), None,
                         "unscored", f"{type(exc).__name__}: {exc}"[:500],
                         time.monotonic() - started)
    if math.isnan(value):
        return Score(dataset, metric_name, sample.as_join(), None, "unscored",
                     "metric returned NaN (no statements or questions to "
                     "score)", time.monotonic() - started)
    return Score(dataset, metric_name, sample.as_join(), value, "scored", None,
                 time.monotonic() - started)


async def score_all(run, metrics, max_workers, active, progress=True,
                    journal=None, reuse=None):
    """Every (sample, metric) pair, concurrently, in a deterministic order.

    Progress is reported as pairs COMPLETE rather than as they are dispatched.
    A run of this shape is 700+ sequential judge calls behind a handful of
    tasks and takes tens of minutes; without a completion counter an operator
    cannot tell a slow run from a wedged one, and the only available response
    to either is to kill it and lose the spend.

    ``reuse`` is a ``{pair_key: journal row}`` mapping of pairs already paid for
    -- see ``reusable_scores``. Those pairs are NOT dispatched; they are rebuilt
    as ``Score`` objects and returned alongside the newly scored ones, so what
    comes back is one list of one type and every consumer downstream is the code
    a single-pass run runs.

    ``journal`` is a ``ScoreJournal``. Each completed pair is recorded and
    flushed BEFORE the next one is reported, so a kill loses only the pairs
    actually in flight. The flush is synchronous inside the coroutine and
    therefore blocks the event loop for the length of one small file write --
    milliseconds, against judge calls measured in seconds -- which is the price
    of the guarantee that the file is never observed half-written.

    REUSED PAIRS ARE RE-RECORDED INTO THE JOURNAL. The journal a resumed run
    writes is therefore the whole set so far, not the increment, so a run
    interrupted twice resumes from one file rather than needing every earlier
    one.
    """
    import asyncio

    semaphore = asyncio.Semaphore(max_workers)
    reuse = reuse or {}
    remaining = dict(reuse)
    pending = []
    carried = []
    samples_for = {DATASET_RETRIEVAL: run.retrieval,
                   DATASET_GENERATION: run.generation}
    for dataset, metric_names in active.items():
        for metric_name in metric_names:
            for sample in samples_for[dataset]:
                key = pair_key(dataset, metric_name, sample.as_join())
                if key in remaining:
                    # POPPED, not read. Two samples sharing a pair key would
                    # otherwise both carry the SAME paid score; the first takes
                    # it and the second is scored for real, which costs at most
                    # one pair and cannot attribute one sample's judgement to
                    # another.
                    row = remaining.pop(key)
                    carried.append(score_from_row(row))
                    if journal is not None:
                        journal.rows.append(row)
                    continue
                pending.append((metric_name, sample, dataset))

    if journal is not None and carried:
        # One flush for the whole carried set rather than one per pair: none of
        # it is new work, and the file must hold it before the first NEW pair is
        # dispatched so that a crash on that pair does not lose the carry.
        journal.flush()

    total = len(pending)
    started = time.monotonic()
    done = {"n": 0}

    async def run_one(metric_name, sample, dataset):
        score = await _score_one(metric_name, metrics[metric_name], sample,
                                 dataset, semaphore)
        if journal is not None:
            journal.record(score, pair_fingerprint(metric_name, sample))
        done["n"] += 1
        if progress:
            elapsed = time.monotonic() - started
            rate = done["n"] / elapsed if elapsed > 0 else 0.0
            remaining = (total - done["n"]) / rate if rate > 0 else float("nan")
            console.out(f"  [{done['n']:>4}/{total}] {score.metric} "
                        f"{'/'.join(str(v) for v in score.join.values())} "
                        f"-> {score.status}"
                        f"{'' if score.value is None else f' {score.value:.4f}'}"
                        f"  ({score.seconds:.1f}s, ~{remaining / 60:.1f}m left)")
        return score

    if progress:
        if carried:
            console.out(f"  resuming: {len(carried)} pair(s) already scored and "
                        f"carried forward, {total} left to score")
        console.out(f"  scoring {total} (sample, metric) pairs at "
                    f"{max_workers} concurrent...")
    scores = await asyncio.gather(
        *(run_one(name, sample, dataset) for name, sample, dataset in pending))
    return carried + list(scores)


#------------------------------------------------------------------------------
# Summarising
#------------------------------------------------------------------------------


def summarize(scores, run, active):
    """Per-metric distribution over the SCORED values only, plus the rest.

    Unscored samples are counted and named, never folded into the statistics:
    an aggregate that quietly averaged over fewer samples than it claims is the
    defect this whole project is written against.
    """
    selected = [m for m in ALL_METRICS
                if m in {x for ms in active.values() for x in ms}]
    by_metric = {}
    for metric_name in selected:
        rows = [s for s in scores if s.metric == metric_name]
        values = [s.value for s in rows if s.status == "scored"]
        unscored = [s for s in rows if s.status != "scored"]
        entry = {
            "metric": metric_name,
            "samples": len(rows),
            "scored": len(values),
            "unscored": len(unscored),
            "unscored_reasons": sorted({s.reason for s in unscored if s.reason}),
        }
        if values:
            entry.update({
                "mean": round(statistics.fmean(values), 6),
                "median": round(statistics.median(values), 6),
                "min": round(min(values), 6),
                "max": round(max(values), 6),
                "stdev": (round(statistics.stdev(values), 6)
                          if len(values) > 1 else None),
            })
        else:
            entry.update({"mean": None, "median": None, "min": None,
                          "max": None, "stdev": None})
        lowest = sorted((s for s in rows if s.status == "scored"),
                        key=lambda s: (s.value, sorted(s.join.items())))[:10]
        entry["lowest"] = [{"value": round(s.value, 6), **s.join}
                           for s in lowest]
        entry["unscored_samples"] = [{**s.join, "reason": s.reason}
                                     for s in unscored]
        by_metric[metric_name] = entry

    samples_for = {DATASET_RETRIEVAL: len(run.retrieval),
                   DATASET_GENERATION: len(run.generation)}
    return {
        "datasets": {dataset: {"samples": samples_for[dataset],
                               "metrics": list(metric_names)}
                     for dataset, metric_names in active.items()},
        "metrics": by_metric,
        "total_pairs": len(scores),
        "total_scored": sum(1 for s in scores if s.status == "scored"),
        "total_unscored": sum(1 for s in scores if s.status != "scored"),
    }


def _fmt(value):
    return "  --  " if value is None else f"{value:6.4f}"


def print_summary(summary, cost, judge_model, embedding_model, wall_seconds,
                  temperature, active, response_field=None):
    console.banner("RAGAS EVALUATION -- REFERENCE-FREE METRICS")
    console.out(f"judge:      {judge_model} (temperature "
                f"{temperature}, non-batch rates)")
    if response_field is not None:
        console.out(f"response:   the recorded {response_field!r} field")
    selected = {m for ms in active.values() for m in ms}
    if METRIC_RESPONSE_RELEVANCY in selected:
        console.out(f"embeddings: {embedding_model} (response relevancy only)")
    else:
        console.out("embeddings: none -- no metric in this run needs one")
    console.out(f"wall time:  {wall_seconds:.1f}s")
    console.out("")

    for dataset, entry in summary["datasets"].items():
        console.out(f"{dataset:<11} {entry['samples']:>5} samples  "
                    f"-> {', '.join(entry['metrics'])}")
    console.out("")

    header = (f"{'metric':<34}{'n':>5}{'unsc':>6}{'mean':>9}{'median':>9}"
              f"{'min':>9}{'max':>9}")
    console.out(header)
    console.out("-" * len(header))
    for name, entry in summary["metrics"].items():
        console.out(f"{name:<34}{entry['scored']:>5}{entry['unscored']:>6}"
                    f"{_fmt(entry['mean']):>9}{_fmt(entry['median']):>9}"
                    f"{_fmt(entry['min']):>9}{_fmt(entry['max']):>9}")
    console.out("")

    for name, entry in summary["metrics"].items():
        if not entry["lowest"]:
            continue
        console.out(f"10 lowest-scoring samples -- {name}")
        for row in entry["lowest"]:
            keys = "  ".join(f"{k}={v}" for k, v in row.items()
                             if k != "value")
            console.out(f"    {row['value']:6.4f}   {keys}")
        console.out("")

    if summary["total_unscored"]:
        console.out(f"UNSCORED: {summary['total_unscored']} of "
                    f"{summary['total_pairs']} pairs. Each is listed in "
                    f"ragas_results.json with its reason; none was dropped "
                    f"and none was counted as 0.0.")
        for name, entry in summary["metrics"].items():
            for row in entry["unscored_samples"][:10]:
                keys = "  ".join(f"{k}={v}" for k, v in row.items()
                                 if k != "reason")
                console.out(f"    {name}: {keys}  -- {row['reason']}")
        console.out("")

    if METRIC_RESPONSE_RELEVANCY in selected:
        console.out(RELEVANCY_RANGE_NOTE)
        console.out("")
    if METRIC_CONTEXT_PRECISION in selected:
        console.out(CIRCULARITY_RULE_NOTE)
        console.out("")
    if response_field is not None:
        console.out(RESPONSE_FIELD_NOTES[response_field])
        console.out("")
    console.out(REPRODUCIBILITY_NOTE)
    console.out("")
    console.out(CONTEXT_RECALL_SCOPE_NOTE)
    console.out("")
    if cost:
        console.out(f"MEASURED spend: ${cost['total_usd']:.4f}  "
                    f"(judge ${cost['judge_usd']:.4f} over "
                    f"{cost['judge_calls']} calls, "
                    f"{cost['judge_input_tokens']:,} in / "
                    f"{cost['judge_output_tokens']:,} out; embeddings "
                    f"${cost['embedding_usd']:.4f} over "
                    f"{cost['embedding_calls']} calls)")
    console.out("")


def print_plan(plan, run, out_dir, active, environment):
    console.banner("RAGAS DRY RUN -- NOTHING WAS SUBMITTED")
    console.out(f"run dir:    {run.run_dir}")
    console.out(f"output dir: {out_dir}")
    results_path, manifest_path = output_paths(out_dir, run.response_field)
    console.out(f"would write {os.path.basename(results_path)} and "
                f"{os.path.basename(manifest_path)}")
    console.out(f"response:   the recorded {run.response_field!r} field")
    census = run.field_census or {}
    if census.get("both_fields_present"):
        console.out(f"            of {census['verdicts_seen']} verdict(s), "
                    f"{census['both_fields_present']} carry both "
                    f"{RESPONSE_FIELD_ASSESSMENT} and "
                    f"{RESPONSE_FIELD_DRAFT}: {census['identical']} "
                    f"byte-identical, {census['differing']} differing")
    console.out("")
    # THE SAME FIELDS THE MANIFEST STAMPS, THROUGH THE SAME RENDERER, AND THEY
    # DESCRIBE THIS INTERPRETER TRUTHFULLY WHATEVER IT IS. The dry run returns
    # before ``import ragas``, so it can legitimately be run from the project
    # environment -- which does not have ragas, by design -- and ``ragas
    # absent`` is then the CORRECT record of what produced this plan, not a
    # defect and not a reason to refuse. A plan is arithmetic over recorded
    # text; it needs no judge, no SDK and no metric implementation. What the
    # line is for is the opposite mistake: reading a dry run's numbers as if
    # they came from the environment the scoring run will use.
    print_environment(environment)
    console.out("")
    if DATASET_RETRIEVAL in active:
        console.out(f"{DATASET_RETRIEVAL:<11} {len(run.retrieval):>5} samples "
                    f"(one per patient)")
    if DATASET_GENERATION in active:
        console.out(f"{DATASET_GENERATION:<11} {len(run.generation):>5} "
                    f"samples (one per verdicted trial with a non-empty "
                    f"assessment)")
    console.out("")
    if DATASET_RETRIEVAL in active:
        print_retrieval_response_shape(run)
    header = (f"{'metric':<34}{'samples':>9}{'judge':>8}{'embed':>8}"
              f"{'est.$':>9}")
    console.out(header)
    console.out("-" * len(header))
    for row in plan["metrics"]:
        console.out(f"{row['metric']:<34}{row['samples']:>9}"
                    f"{row['judge_calls']:>8}{row['embedding_calls']:>8}"
                    f"{row['estimated_usd']:>9.4f}")
    console.out("-" * len(header))
    console.out(f"{'TOTAL':<34}{'':>9}{plan['judge_calls_total']:>8}"
                f"{plan['embedding_calls_total']:>8}"
                f"{plan['estimated_usd_midpoint']:>9.4f}")
    console.out("")
    for row in plan["metrics"]:
        console.out(f"  {row['metric']}: {row['basis']}")
    console.out("")
    console.out(f"PROJECTED COST RANGE: "
                f"${plan['estimated_usd_low']:.2f} to "
                f"${plan['estimated_usd_high']:.2f} at standard "
                f"(non-batch) rates.")
    console.out("")
    console.out(plan["estimate_caveat"])
    console.out("")
    console.out(RESPONSE_FIELD_NOTES[run.response_field])
    console.out("")
    console.out(CONTEXT_RECALL_SCOPE_NOTE)
    console.out("")
    if run.problems:
        console.out(f"{len(run.problems)} problem(s) reading the run:")
        for problem in run.problems[:20]:
            console.out(f"    {problem}")
        console.out("")


#------------------------------------------------------------------------------
# Output
#------------------------------------------------------------------------------


def print_resume_preview(run, args, active, out_dir, environment):
    """What ``--resume`` would carry and what it would still have to judge.

    Free, and callable with nothing built: it reads one JSON file and compares
    hashes. Prints nothing at all when there is no partial file AND --resume was
    not asked for, so an ordinary dry run is unchanged.
    """
    partial_file = partial_path(out_dir, run.response_field)
    payload, why_not = load_partial(partial_file)
    if payload is None and not args.resume:
        return
    console.out("")
    console.out(f"resume: {partial_file}")
    if payload is None:
        console.out(f"    {why_not} -- --resume would score the whole plan")
        return
    identity = resume_identity(run, args, environment)
    changed = identity_disagreement(payload.get("identity"), identity)
    if changed:
        console.out("    WOULD REFUSE: the partial file's environment differs")
        for line in changed:
            console.out(f"        {line}")
        return
    _, report = reusable_scores(payload, run, active, identity)
    total = sum(len(metric_names)
                * (len(run.retrieval) if dataset == DATASET_RETRIEVAL
                   else len(run.generation))
                for dataset, metric_names in active.items())
    console.out(f"    environment matches; {report['reused']} of "
                f"{report['rows']} recorded pair(s) are current")
    console.out(f"    --resume would judge {total - report['reused']} of "
                f"{total} pair(s) in this plan")
    for label, count in (("not in this plan", report["not_in_plan"]),
                         ("scored against text that has since changed",
                          report["stale"]),
                         ("duplicate pair keys", report["duplicate"]),
                         ("malformed rows", report["malformed"])):
        if count:
            console.out(f"        {count} {label}")


def print_retrieval_response_shape(run):
    """What the retrieval response now IS, per patient, before any spend.

    The whole point of the redesign is what goes in this field, so the dry run
    shows it rather than describing it: how many matched trials contribute,
    how many retrieved trials are consequently absent from the answer and must
    earn their verdict on content, and the first line of the text itself.
    """
    console.out("retrieval response shape (the redesigned field):")
    header = (f"    {'patient':<12}{'ctx':>5}{'matched':>9}{'rejected':>10}"
              f"{'chars':>8}  first line")
    console.out(header)
    console.out("    " + "-" * (len(header) - 4))
    no_match = 0
    uncounted = 0
    for sample in run.retrieval:
        text = sample.response
        matched = sample.matched_count
        if matched is None:
            # NEVER RENDERED AS 0. A sample built without a count -- which only
            # a caller constructing one by hand can produce -- is unknown, and
            # printing an unknown as zero would report every trial as having to
            # earn its verdict on content.
            uncounted += 1
            shown_matched, shown_rejected = "?", "?"
        else:
            if not matched:
                no_match += 1
            shown_matched = str(matched)
            shown_rejected = str(len(sample.retrieved_contexts) - matched)
        first = text.splitlines()[0] if text else ""
        console.out(f"    {sample.patient_id[:10]:<12}"
                    f"{len(sample.retrieved_contexts):>5}{shown_matched:>9}"
                    f"{shown_rejected:>10}"
                    f"{len(text):>8}  {first[:58]}")
    total_ctx = sum(len(s.retrieved_contexts) for s in run.retrieval)
    total_matched = sum(s.matched_count for s in run.retrieval
                        if s.matched_count is not None)
    console.out("")
    console.out(f"    {total_matched} of {total_ctx} retrieved trials appear "
                f"in a response; the other {total_ctx - total_matched} must "
                f"earn their relevance verdict on content.")
    console.out(f"    patients with no matching trial: {no_match}")
    if uncounted:
        console.out(f"    {uncounted} sample(s) carry no match count and are "
                    f"excluded from both totals above.")
    console.out("")


RESULTS_BASENAME = "ragas_results"
MANIFEST_BASENAME = "ragas_manifest"


def output_paths(out_dir, response_field=DEFAULT_RESPONSE_FIELD):
    """(results, manifest) paths, NAMED AFTER THE FIELD THEY SCORE.

    ``write_json`` replaces its target and the default output location is one
    directory per run, so two passes over the same run with different response
    fields would have written the second over the first -- same filenames, same
    directory, no error, and the surviving file's manifest would be the only
    thing distinguishing $3 of measurement from the $3 of measurement it had
    just destroyed. The field is in the NAME, so they cannot collide even when
    an operator points both at one directory.

    THE DEFAULT FIELD KEEPS THE HISTORICAL NAMES, deliberately. Two schema-1
    manifests already sit under ``09- Testing/Evaluation Runs/`` as
    ``ragas_results.json``; ``superseded_record`` finds the earlier biased
    precision output by that exact path, and renaming it would silently break
    the provenance link rather than fail. So only a NON-default field takes a
    suffix, and one function derives both names so they cannot disagree about
    which pass they belong to.
    """
    suffix = ("" if response_field == DEFAULT_RESPONSE_FIELD
              else f"__{response_field}")
    return (os.path.join(out_dir, f"{RESULTS_BASENAME}{suffix}.json"),
            os.path.join(out_dir, f"{MANIFEST_BASENAME}{suffix}.json"))


def existing_outputs(out_dir, response_field=DEFAULT_RESPONSE_FIELD):
    """Which of this pass's two target files are already on disk.

    Field-aware naming stops two DIFFERENT passes colliding; it does nothing
    about re-running the SAME pass, which still replaces a scored result with
    another one. That is a real loss -- these files cost dollars and are not
    reproducible sample to sample (REPRODUCIBILITY_NOTE), so the replaced run
    cannot be recovered by re-running it. Reported to ``main``, which refuses
    before spending unless ``--overwrite`` says the replacement is intended.
    """
    return [p for p in output_paths(out_dir, response_field)
            if os.path.exists(p)]


def write_json(path, payload):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False, ensure_ascii=False)
    os.replace(tmp, path)


def _utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def build_results(scores, run, active):
    """Per-sample scores, joined by patient_id and nct_id, deterministic."""
    rows = [s.as_row() for s in scores]
    rows.sort(key=lambda r: (r["dataset"], r["metric"],
                             r.get("patient_id") or "",
                             r.get("nct_id") or ""))
    return {
        "schema_version": 1,
        "generated_at_utc": _utc_now(),
        "run_dir": run.run_dir,
        "response_field": run.response_field,
        "notes": {
            "response_field": RESPONSE_FIELD_NOTES[run.response_field],
            "context_recall_scope": CONTEXT_RECALL_SCOPE_NOTE,
            **({"circularity_rule": CIRCULARITY_RULE_NOTE}
               if METRIC_CONTEXT_PRECISION in _selected(active) else {}),
            **({"relevancy_range": RELEVANCY_RANGE_NOTE}
               if METRIC_RESPONSE_RELEVANCY in _selected(active) else {}),
            "reproducibility": REPRODUCIBILITY_NOTE,
            "unscored": ("A row with status 'unscored' carries value null and "
                         "a reason. It was never dropped and was never "
                         "counted as 0.0."),
        },
        "scores": rows,
    }


def superseded_record(run_dir, out_dir, active):
    """Name the earlier biased precision outputs, if they are really there.

    Conditional on BOTH facts rather than asserted unconditionally: a manifest
    that claims to supersede a file which does not exist is a false provenance
    claim, and so is one that claims to supersede a metric this run did not
    compute.
    """
    selected = {m for metrics in active.values() for m in metrics}
    if METRIC_CONTEXT_PRECISION not in selected:
        return None
    prior_dir = os.path.join(run_dir, "ragas")
    prior = os.path.join(prior_dir, "ragas_results.json")
    if os.path.realpath(prior_dir) == os.path.realpath(out_dir):
        return None
    if not os.path.isfile(prior):
        return None
    return {"path": prior,
            "metric": METRIC_CONTEXT_PRECISION,
            "retained": "unchanged, as the record of the biased design",
            "reason": SUPERSEDES_NOTE}


def build_manifest(run, summary, cost, args, wall_seconds, ragas_version,
                   plan, active, supersedes=None, environment=None,
                   resumed_from=None, pairs_scored_here=None):
    """The record of what ran, under what, at what cost.

    ``environment`` defaults to a stamp taken here rather than to ``None``, so a
    caller that forgets it writes a truthful record instead of a null field.
    ``main()`` passes the same object it printed, so the plan an operator read
    and the manifest they keep cannot describe different environments.
    """
    return {
        # SCHEMA 2 ADDS ``environment`` AND NOTHING ELSE. The two manifests
        # already on disk under 09- Testing/Evaluation Runs/ are schema 1 and
        # carry no environment block; they record a ragas version and nothing
        # about the interpreter, the SDKs or where any of it lived. Bumping is
        # what lets a reader tell "this run predates the stamp" from "this run
        # was stamped and the block is missing". Nothing in this repository
        # reads the field, so the bump costs nothing today; leaving two
        # different field sets both claiming schema 1 would cost later.
        "schema_version": 2,
        "generated_at_utc": _utc_now(),
        "run_dir": run.run_dir,
        "ragas_version": ragas_version,
        "environment": (environment if environment is not None
                        else environment_stamp()),
        # WHICH RECORDED TEXT WAS SCORED. Read from the RunInput rather than
        # from args, so this field reports what the samples were actually built
        # from; an args value could be stamped onto a run whose loader had been
        # handed something else.
        "response_field": run.response_field,
        "response_field_note": RESPONSE_FIELD_NOTES[run.response_field],
        "response_field_missing_policy": RESPONSE_FIELD_MISSING_NOTE,
        # The population any draft-side figure has to be read over: how many
        # verdicts carry BOTH fields, and of those how many are byte-identical
        # (a class whose composed text is the model's own text kept unchanged,
        # for which the two figures cannot differ). Counted over the whole run
        # before --limit is applied.
        "response_field_census": run.field_census,
        "judge_model": args.judge_model,
        "judge_provider": "anthropic",
        "judge_temperature": args.temperature,
        "judge_max_tokens": args.max_tokens,
        "embeddings_model": args.embedding_model,
        "embeddings_provider": "openai",
        "relevancy_strictness": RELEVANCY_STRICTNESS,
        "max_workers": args.max_workers,
        "limit": args.limit or None,
        "ragas_telemetry_disabled": os.environ.get(RAGAS_DO_NOT_TRACK),
        "metrics": {dataset: list(metric_names)
                    for dataset, metric_names in active.items()},
        "metrics_not_run": [m for m in ALL_METRICS
                            if m not in {x for ms in active.values()
                                         for x in ms}],
        "sample_counts": {
            DATASET_RETRIEVAL: (len(run.retrieval)
                                if DATASET_RETRIEVAL in active else 0),
            DATASET_GENERATION: (len(run.generation)
                                 if DATASET_GENERATION in active else 0),
            "total_pairs": summary["total_pairs"],
            "scored": summary["total_scored"],
            "unscored": summary["total_unscored"],
        },
        "planned_calls": {
            "judge": plan["judge_calls_total"],
            "embedding": plan["embedding_calls_total"],
        },
        "wall_seconds": round(wall_seconds, 3),
        "cost": cost,
        # WHAT ``cost`` AND ``wall_seconds`` ARE ABOUT, stated as a field rather
        # than left for a reader to assume. Both are measured by THIS process --
        # ``UsageTally`` counts the calls this invocation made and the clock
        # starts when this invocation starts -- so on a resumed run they are the
        # cost and the time of FINISHING the set, not of the set. The
        # interrupted run's spend is real, was billed, and is not recoverable
        # from here: its own manifest was never written, which is exactly why it
        # was interrupted. Same shape as ``fixture_capture.py``'s end-of-run
        # summary pricing only what that run wrote.
        "cost_scope": ("this invocation only; pairs carried forward by --resume "
                       "were paid for by the interrupted run and are not in this "
                       "figure"
                       if resumed_from else "the whole run"),
        # ON THE ABLATION RUNNER'S ``resumed=true`` PRECEDENT
        # (oncotriage/ablation/study.py), and carrying more than a boolean: a
        # reader asking "is this mean over 738 pairs one environment produced"
        # needs to know how many were carried, how many were dropped and why.
        "resumed": bool(resumed_from),
        "resumed_from": resumed_from,
        "pairs_scored_this_invocation": pairs_scored_here,
        "supersedes": supersedes,
        "circularity_rule_note": CIRCULARITY_RULE_NOTE,
        "context_recall_scope_note": CONTEXT_RECALL_SCOPE_NOTE,
        "relevancy_range_note": RELEVANCY_RANGE_NOTE,
        "reproducibility_note": REPRODUCIBILITY_NOTE,
        "dataset_construction": {
            DATASET_RETRIEVAL: (
                "one sample per patient. user_input = the patient summary; "
                "retrieved_contexts = that patient's fenced trial texts in "
                "recorded rank order; response = THE CLINICAL ANSWER -- for "
                "each trial the pipeline verdicted eligible, its nct_id "
                f"followed by that trial's recorded {run.response_field!r} "
                "text verbatim, one per "
                "line, or a plain statement that no matching trial was found. "
                "Trials the pipeline rejected are absent from the response. "
                "No prose is written by this harness."),
            DATASET_GENERATION: (
                "one sample per verdicted trial with a non-empty assessment. "
                "user_input = a short fixed eligibility question naming the "
                "nct_id and deliberately EXCLUDING the patient summary, "
                "because response relevancy embeds questions reverse-"
                "engineered from the response and a summary-laden user_input "
                "would floor the score by construction; retrieved_contexts = "
                "[patient summary, that trial's fenced text], because "
                "faithfulness checks every statement against the contexts and "
                "an assessment citing patient facts would otherwise be "
                f"unsupported by construction; response = the recorded "
                f"{run.response_field!r} verbatim."),
        },
        "problems": run.problems,
    }


#------------------------------------------------------------------------------
# Post-checks -- free, and run on every scoring run
#------------------------------------------------------------------------------


def snapshot_tree(root, exclude_dir=None):
    """sha256 of every file under ``root``, keyed by relative path.

    Taken before scoring and compared after writing, this is what turns "this
    run left the earlier outputs alone" from a claim into a check. It covers
    the WHOLE run directory rather than just the one sibling this run happens
    to supersede, so it also proves the harness did not touch the patient
    records it read -- a read-only harness that quietly rewrote its own input
    would otherwise be indistinguishable from one that did not.
    """

    snapshot = {}
    exclude = os.path.realpath(exclude_dir) if exclude_dir else None
    for dirpath, dirnames, filenames in os.walk(root):
        if exclude and os.path.realpath(dirpath) == exclude:
            dirnames[:] = []
            continue
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            try:
                with io.open(full, "rb") as fh:
                    snapshot[os.path.relpath(full, root)] = hashlib.sha256(
                        fh.read()).hexdigest()
            except OSError as exc:                      # noqa: PERF203
                snapshot[os.path.relpath(full, root)] = f"unreadable: {exc}"
    return snapshot


def ragas_version_disagreement(environment, imported_version):
    """A failure string when the two readings of the ragas version disagree.

    TWO READINGS OF ONE FACT, SO THEY ARE COMPARED RATHER THAN LEFT TO DRIFT.
    ``ragas_version`` in the manifest is ``ragas.__version__`` -- the module
    that actually scored the run -- and ``environment.packages['ragas']`` is
    the version of the DISTRIBUTION on this path, which is the thing a
    reinstall reproduces. They agree in any ordinary install and disagree when
    a source checkout, a stale ``.pth`` or a second site-packages shadows the
    installed distribution: exactly the state in which "recreate the
    environment and re-run" would silently not reproduce these scores.
    Recording both without comparing them would be two fields free to diverge
    with nothing failing when they did.

    A SEPARATE FUNCTION SO IT CAN BE EXERCISED WITHOUT A BILLED RUN. Every
    other check in ``main()``'s failure block needs a scored run behind it,
    which costs about $9 on the reference corpus; folding this one in beside
    them would have made it the one assertion here that nothing ever runs.

    Returns ``None`` when the two agree.
    """
    # ``.get``, not ``[...]``. The caller runs this AFTER scoring and after
    # both output files are written, so a KeyError -- which is all it would
    # take to drop "ragas" from ENVIRONMENT_PACKAGES -- would traceback out of
    # a run that had already spent its money and produced correct output, and
    # would take the whole failure summary with it.
    stamped = environment["packages"].get("ragas")
    if stamped is None:
        return ("environment: the stamp carries no ragas entry, so the "
                "version that scored this run could not be cross-checked "
                "against the distribution on this path. ENVIRONMENT_PACKAGES "
                "no longer names it.")
    if stamped != imported_version:
        return (f"environment: distribution metadata reports ragas "
                f"{stamped!r} while the module that scored this run reports "
                f"{imported_version!r}. Something on this path is shadowing "
                f"the installed distribution, so reinstalling the recorded "
                f"version would not reproduce these scores.")
    return None


def post_checks(run, scores, results_path, manifest_path, out_dir, active,
                tree_before=None, tree_root=None):
    """Every sample accounted for, results round-trip, nothing written outside.

    Returns a list of failure strings; empty means all three held.
    """
    failures = []

    samples_for = {DATASET_RETRIEVAL: len(run.retrieval),
                   DATASET_GENERATION: len(run.generation)}
    expected = sum(len(metric_names) * samples_for[dataset]
                   for dataset, metric_names in active.items())
    if len(scores) != expected:
        failures.append(f"accounting: expected {expected} (sample, metric) "
                        f"pairs, got {len(scores)}")
    accounted = sum(1 for s in scores if s.status in ("scored", "unscored"))
    if accounted != len(scores):
        failures.append(f"accounting: {len(scores) - accounted} pair(s) are "
                        f"neither scored nor unscored")

    # PROVENANCE. The manifest states one response_field for the whole run;
    # this is what makes that a checked fact rather than a claim. A silent
    # fallback -- the defect --response-field exists to prevent -- would show
    # up here as a mixture, and a mean over a mixture is a number about
    # nothing. The declared field is read off the RunInput, which is what the
    # manifest also stamps, and the samples are read individually.
    mismatched = [s for s in (run.retrieval + run.generation)
                  if s.response_field != run.response_field]
    if mismatched:
        failures.append(
            f"provenance: {len(mismatched)} sample(s) were built from a "
            f"response field other than the declared {run.response_field!r}: "
            f"{sorted({s.response_field for s in mismatched})}")

    try:
        with io.open(results_path, "r", encoding="utf-8") as fh:
            reloaded = json.load(fh)
        if len(reloaded.get("scores", [])) != len(scores):
            failures.append(
                f"round-trip: results.json holds "
                f"{len(reloaded.get('scores', []))} rows, {len(scores)} were "
                f"scored")
        with io.open(manifest_path, "r", encoding="utf-8") as fh:
            json.load(fh)
    except Exception as exc:                            # noqa: BLE001
        failures.append(f"round-trip: {type(exc).__name__}: {exc}")

    out_dir_real = os.path.realpath(out_dir)
    for path in (results_path, manifest_path):
        if not os.path.realpath(path).startswith(out_dir_real + os.sep):
            failures.append(f"containment: {path} is outside {out_dir}")
    stray = [n for n in os.listdir(out_dir) if n.endswith(".tmp")]
    if stray:
        failures.append(f"containment: temporary files left behind: {stray}")

    if tree_before is not None and tree_root is not None:
        after = snapshot_tree(tree_root, exclude_dir=out_dir)
        changed = sorted(k for k in set(tree_before) | set(after)
                         if tree_before.get(k) != after.get(k))
        if changed:
            failures.append(
                f"integrity: {len(changed)} file(s) under {tree_root} changed "
                f"outside the output directory: {changed[:10]}")
        elif not tree_before:
            failures.append(
                "integrity: the pre-run snapshot was empty, so the "
                "unchanged-tree check could not have failed -- it proves "
                "nothing about this run")

    return failures


#------------------------------------------------------------------------------
# CLI
#------------------------------------------------------------------------------


def _parse_args(argv=None):
    import argparse
    p = argparse.ArgumentParser(
        prog="ragas_run.py",
        description="Reference-free Ragas metrics over a recorded evaluation "
                    "run. SPENDS MONEY on the Anthropic API at standard "
                    "(non-batch) rates unless --dry-run is given.")
    p.add_argument("--run-dir", default=None,
                   help="the evaluation run to score (default: the "
                        "10-patient run under 09- Testing/Evaluation Runs/)")
    p.add_argument("--output-dir", default=None,
                   help="where to write results/manifest "
                        "(default: <run-dir>/ragas/)")
    p.add_argument("--dry-run", action="store_true",
                   help="build both datasets, print counts, per-metric judge-"
                        "call estimates and a projected cost range; call "
                        "nothing")
    p.add_argument("--limit", type=int, default=0, metavar="N",
                   help="score only the first N samples of EACH dataset "
                        "(a cheap smoke run)")
    p.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    p.add_argument("--embedding-model", default=None,
                   help=f"default: config.EMBEDDING_MODEL")
    p.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS,
                   help=f"concurrent (sample, metric) scorings "
                        f"(default: {DEFAULT_MAX_WORKERS})")
    p.add_argument("--max-retries", type=int, default=DEFAULT_CLIENT_RETRIES,
                   help="Anthropic SDK retries for 429/5xx")
    p.add_argument("--metrics", nargs="+", choices=ALL_METRICS,
                   default=list(ALL_METRICS), metavar="METRIC",
                   help="score only these metrics (default: all three). "
                        "Selecting none of the generation metrics skips that "
                        "dataset entirely; selecting no metric that needs an "
                        "embedder means no OpenAI client is built and "
                        "OPENAI_API_KEY is never read. Choices: "
                        + ", ".join(ALL_METRICS))
    p.add_argument("--response-field", choices=RESPONSE_FIELDS,
                   default=DEFAULT_RESPONSE_FIELD, metavar="FIELD",
                   help=f"which recorded verdict text to score as the response "
                        f"(default: {DEFAULT_RESPONSE_FIELD}). "
                        f"{RESPONSE_FIELD_ASSESSMENT} is COMPOSED text at "
                        f"PROMPT_VERSION >= 1.5.0 and faithfulness over it "
                        f"measures the renderer near its ceiling; "
                        f"{RESPONSE_FIELD_DRAFT} is the model's own prose and "
                        f"measures the model. A verdict lacking the selected "
                        f"field is a refusal, never a fallback. Outputs are "
                        f"named after the field, so passes cannot overwrite "
                        f"each other. Choices: " + ", ".join(RESPONSE_FIELDS))
    p.add_argument("--resume", action="store_true",
                   help="continue an interrupted run from its partial score "
                        "file in the output directory. Pairs already scored "
                        "are carried forward -- not re-judged and not re-paid "
                        "-- provided the partial file's environment (ragas, "
                        "anthropic, openai and langchain-core versions, judge "
                        "model, temperature, max tokens, embedding model, "
                        "response field and run directory) matches this run's, "
                        "and provided the text each pair was scored against is "
                        "unchanged. Any environment difference is a REFUSAL "
                        "naming what moved: scores from two environments are a "
                        "mean about nothing. The partial file is written after "
                        "every pair whether or not --resume was given, and is "
                        "deleted when a run completes.")
    p.add_argument("--overwrite", action="store_true",
                   help="replace this pass's output files if they already "
                        "exist. Without it an existing result is a refusal "
                        "BEFORE anything is spent: these files cost money and "
                        "are not reproducible sample to sample, so a replaced "
                        "one cannot be recovered by re-running.")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    if args.embedding_model is None:
        args.embedding_model = config.EMBEDDING_MODEL

    # Before ragas is imported anywhere: its do_not_track() is lru_cached on
    # first read, so a later assignment would reach nothing.
    telemetry, telemetry_source = disable_third_party_telemetry()

    try:
        run_dir = args.run_dir or default_run_dir()
        run_dir = os.path.abspath(os.path.expanduser(run_dir))
        out_dir = args.output_dir or os.path.join(run_dir, "ragas")
        out_dir = os.path.abspath(os.path.expanduser(out_dir))

        active = active_dataset_metrics(args.metrics)
        needs_embeddings = any(m in METRICS_NEEDING_EMBEDDINGS
                               for ms in active.values() for m in ms)
        run = apply_limit(load_run(run_dir, args.response_field), args.limit)
        # Priced before anything is built, so an unpriced model refuses before
        # a client is constructed rather than after the first billed call.
        plan = price_plan(run, args.judge_model, args.embedding_model, active)
    except RagasRefusal as exc:
        console.out(f"REFUSED ({exc.code}): {exc}")
        log.error("ragas_refused", extra={"reason": exc.code})
        return 1

    # Taken ONCE, before either path diverges, and handed to whichever runs. A
    # second call would be a second reading of the same fact, and two readings
    # of a filesystem taken minutes apart can disagree.
    environment = environment_stamp()

    if args.dry_run:
        print_plan(plan, run, out_dir, active, environment)
        # THE RESUME PREVIEW IS ON THE FREE PATH ON PURPOSE. The plan above
        # prices the WHOLE set; an operator about to resume is going to pay for
        # the remainder, and the difference between those two numbers is the
        # decision they are making. Everything it needs -- the partial file, the
        # identity comparison, the per-pair fingerprints -- is filesystem and
        # arithmetic, so the preview costs nothing and calls nothing. It is also
        # the only way to exercise the resume gate without a live judge.
        print_resume_preview(run, args, active, out_dir, environment)
        return 0

    parent = os.path.dirname(out_dir.rstrip(os.sep))
    if not os.path.isdir(parent):
        console.out(f"REFUSED (output_parent_missing): {parent!r} does not "
                    f"exist, so --output-dir cannot be created there.")
        return 1

    # BEFORE A CENT IS SPENT, and before the output directory is created: an
    # existing result for THIS field is a refusal unless the replacement was
    # asked for. Field-aware naming already stops two different passes
    # colliding; this is the same-pass case, where the file being replaced cost
    # real money and cannot be recovered by re-running (REPRODUCIBILITY_NOTE).
    if not args.overwrite:
        clash = existing_outputs(out_dir, args.response_field)
        if clash:
            console.out(f"REFUSED (output_exists): {len(clash)} output "
                        f"file(s) for --response-field "
                        f"{args.response_field} already exist and would be "
                        f"replaced: {', '.join(clash)}. These cost money and "
                        f"are not reproducible sample to sample. Pass "
                        f"--overwrite to replace them, or --output-dir to "
                        f"write elsewhere.")
            log.error("ragas_refused", extra={"reason": "output_exists"})
            return 1
    os.makedirs(out_dir, exist_ok=True)
    # Derived ONCE, from the same function the guard above consulted, so what
    # is announced before the spend and what is written after it cannot name
    # different files.
    results_path, manifest_path = output_paths(out_dir, run.response_field)

    # --- Resume ------------------------------------------------------------
    #
    # DECIDED BEFORE A CLIENT IS BUILT AND BEFORE A CENT IS SPENT, on the same
    # footing as the pricing refusal above: a partial file from another
    # environment must stop the run while stopping is still free, not after the
    # first billed pair has been merged into it.
    identity = resume_identity(run, args, environment)
    partial_file = partial_path(out_dir, run.response_field)
    reuse, reuse_report = {}, None
    resumed_from = None

    payload, why_not = load_partial(partial_file)
    if args.resume:
        if payload is None:
            # A --resume with nothing to resume from is not an error and is not
            # silent. Scoring the whole plan is the correct behaviour and it is
            # also what an operator who mistyped --output-dir is about to pay
            # for, so the reason is named before the spend rather than inferred
            # from a cost afterwards.
            console.out(f"--resume: {os.path.basename(partial_file)} "
                        f"{why_not}; scoring the whole plan.")
        else:
            changed = identity_disagreement(payload.get("identity"), identity)
            if changed:
                console.out(
                    f"REFUSED (resume_environment_changed): "
                    f"{os.path.basename(partial_file)} was written under a "
                    f"different environment and its scores must not be merged "
                    f"with this run's -- a mean over two environments is a mean "
                    f"about nothing. What changed:")
                for line in changed:
                    console.out(f"    {line}")
                console.out(
                    "  Re-run without --resume to score the whole plan (which "
                    "replaces the partial file), or point --output-dir "
                    "elsewhere to keep it.")
                log.error("ragas_refused",
                          extra={"reason": "resume_environment_changed"})
                return 1
            reuse, reuse_report = reusable_scores(payload, run, active, identity)
            resumed_from = {
                "partial_path": partial_file,
                "partial_started_at_utc": payload.get("started_at_utc"),
                "partial_updated_at_utc": payload.get("updated_at_utc"),
                "rows_in_partial": reuse_report["rows"],
                "reused_pairs": reuse_report["reused"],
                "dropped_not_in_plan": reuse_report["not_in_plan"],
                "dropped_stale_inputs": reuse_report["stale"],
                "dropped_duplicate": reuse_report["duplicate"],
                "dropped_malformed": reuse_report["malformed"],
            }
            console.out(
                f"--resume: {reuse_report['reused']} of "
                f"{reuse_report['rows']} pair(s) in "
                f"{os.path.basename(partial_file)} are current and will not be "
                f"re-judged.")
            for label, count in (("not in this run's plan",
                                  reuse_report["not_in_plan"]),
                                 ("scored against text that has since changed",
                                  reuse_report["stale"]),
                                 ("duplicate pair keys (last wins)",
                                  reuse_report["duplicate"]),
                                 ("malformed rows", reuse_report["malformed"])):
                if count:
                    console.out(f"    {count} {label}")
    elif payload is not None:
        # NOT AN AUTOMATIC RESUME. Reusing a paid score is a decision about
        # money and about whether two runs are comparable, and it is the
        # operator's, not a default triggered by a file being present.
        console.out(
            f"NOTE: {os.path.basename(partial_file)} holds "
            f"{len(payload.get('scores') or [])} already-scored pair(s) from an "
            f"earlier run. Without --resume they will be re-judged and re-paid, "
            f"and this run replaces that file.")

    journal = ScoreJournal(partial_file, identity)

    try:
        import asyncio

        import ragas

        tally = UsageTally()
        llm = build_judge(args.judge_model, args.temperature, args.max_tokens,
                          tally, args.max_retries)
        # No embedder is CONSTRUCTED unless a selected metric needs one, so a
        # context-precision-only run reads no OPENAI_API_KEY and builds no
        # OpenAI client. Asserted after scoring by the embedding-call check
        # below, which turns "no OpenAI call" from a claim into a measurement.
        embeddings = (build_embeddings(args.embedding_model, tally)
                      if needs_embeddings else None)
        metrics = build_metrics(llm, embeddings, args.metrics)
    except RagasRefusal as exc:
        console.out(f"REFUSED ({exc.code}): {exc}")
        log.error("ragas_refused", extra={"reason": exc.code})
        return 1

    console.out(f"judge {args.judge_model} | embeddings "
                f"{args.embedding_model} | {plan['judge_calls_total']} judge "
                f"calls planned | estimated "
                f"${plan['estimated_usd_low']:.2f}-"
                f"${plan['estimated_usd_high']:.2f}")
    console.out(f"{RAGAS_DO_NOT_TRACK}={telemetry} ({telemetry_source})")
    console.out("metrics: " + ", ".join(
        m for ms in active.values() for m in ms)
        + (" | embedder: none built (no selected metric needs one)"
           if not needs_embeddings else f" | embedder: {args.embedding_model}"))
    console.out(f"response field: {run.response_field} -> "
                f"{os.path.basename(results_path)}")
    # Printed here, before a cent is spent, and not only written into the
    # manifest at the end: the manifest lands after the whole run -- 414 seconds
    # and $9.29 on the reference run -- and an operator who is about to spend
    # that wants to see which ragas is about to score it while stopping is still
    # free.
    print_environment(environment)

    # Snapshotted before a cent is spent and compared after writing, so
    # "the earlier outputs are untouched" is checked rather than asserted.
    tree_before = snapshot_tree(run_dir, exclude_dir=out_dir)

    started = time.monotonic()
    scores = asyncio.run(score_all(run, metrics, args.max_workers, active,
                                   journal=journal, reuse=reuse))
    wall_seconds = time.monotonic() - started

    summary = summarize(scores, run, active)
    cost = tally.cost(args.judge_model, args.embedding_model)

    write_json(results_path, build_results(scores, run, active))
    write_json(manifest_path,
               build_manifest(run, summary, cost, args, wall_seconds,
                              ragas.__version__, plan, active,
                              superseded_record(run_dir, out_dir, active),
                              environment,
                              resumed_from=resumed_from,
                              # COUNTED, not `len(scores) - len(reuse)`. That
                              # subtraction is off by one for every pair key the
                              # plan holds twice, because only the first
                              # occurrence consumes its carried row.
                              pairs_scored_here=sum(
                                  1 for s in scores
                                  if pair_key(s.dataset, s.metric, s.join)
                                  not in reuse)))

    # THE PARTIAL FILE GOES ONLY NOW, once both outputs are on disk. Removing it
    # earlier would leave a window in which a crash had destroyed the recovery
    # state without having produced the thing that replaces it. Removing it
    # never would leave a crash-recovery file beside a finished result, which
    # the next --resume would read as work it may keep without having checked
    # the result it belongs to.
    if journal.write_failures:
        # Reported at the end as well as at the first occurrence: the warning
        # during a 400-second run scrolls away, and this is the line that says
        # the recovery file was incomplete for anyone who resumes from it.
        console.out(f"  NOTE: the partial score file failed to write "
                    f"{sum(journal.write_failures.values())} time(s) "
                    f"({journal.write_failures}); a --resume during this run "
                    f"would have seen fewer pairs than were scored.")

    print_summary(summary, cost, args.judge_model, args.embedding_model,
                  wall_seconds, args.temperature, active, run.response_field)
    console.out(f"wrote {results_path}")
    console.out(f"wrote {manifest_path}")

    failures = post_checks(run, scores, results_path, manifest_path, out_dir,
                           active, tree_before, run_dir)
    if not needs_embeddings and tally.embedding_calls:
        failures.append(
            f"vendor isolation: {tally.embedding_calls} embedding call(s) "
            f"were made although no selected metric needs an embedder")
    # TWO READINGS OF ONE FACT, SO THEY ARE COMPARED RATHER THAN LEFT TO DRIFT.
    # ``ragas_version`` is ``ragas.__version__`` -- the module that actually
    # scored this run -- and ``environment.packages['ragas']`` is the version
    # of the DISTRIBUTION on this path, which is the thing a lockfile installs
    # and reproduces. They agree in any ordinary install and disagree when a
    # source checkout, a stale ``.pth`` or a second site-packages shadows the
    # installed distribution -- which is exactly the state in which
    # "recreate the environment from the lockfile" would not reproduce these
    # scores. Recording both without comparing them would be two fields that
    # can silently diverge; this makes the divergence a named post-check
    # failure. It is exit 3, not a refusal: the outputs are already written and
    # nothing about them is wrong -- what is in doubt is their reproducibility.
    disagreement = ragas_version_disagreement(environment, ragas.__version__)
    if disagreement:
        failures.append(disagreement)
    # THE PARTIAL FILE GOES ONLY NOW, and only if the post-checks held.
    #
    # Removing it earlier would leave a window in which a crash had destroyed
    # the recovery state without having produced the thing that replaces it.
    # Removing it never would leave a crash-recovery file beside a finished
    # result, which the next --resume would read as work it may keep without
    # having checked the result it belongs to.
    #
    # KEEPING IT WHEN A POST-CHECK FAILED is the third case and it is worth the
    # extra branch: the pairs are scored and paid for, the failure is about a
    # property of the OUTPUT (a torn round trip, a tree that moved, a ragas
    # version disagreement), and an operator fixing that and re-running should
    # not be re-buying 738 judge calls to do it. `--resume --overwrite` then
    # rewrites the outputs for nothing.
    if not failures:
        journal.discard()
    if failures:
        console.out("")
        console.out("POST-CHECKS FAILED:")
        for failure in failures:
            console.out(f"    {failure}")
        console.out(f"    (the partial score file was KEPT at "
                    f"{os.path.basename(partial_file)}: every pair in it is "
                    f"paid for, and `--resume --overwrite` will rewrite the "
                    f"outputs without re-judging anything)")
        return 3
    console.out(f"post-checks: accounting, round-trip, containment and "
                f"tree integrity all held "
                f"({len(tree_before)} file(s) under the run directory "
                f"hashed before and after, unchanged)")

    return 3 if summary["total_unscored"] else 0


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug 11 2026

@author: ramyalsaffar
"""
