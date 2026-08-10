"""Stage 5: the criterion-level eligibility judgement.

Item 20c, pass 2c: "13- LangGraph Agent.py" lines 2319-3809.

The largest single stage, and the only one that spends money. It carries three
budgets that must not be confused with each other, and File 03's reconciliation
is the argument for keeping them separate:

    MAX_LLM_CLASSIFIER_RETRIES      the response came back and was unusable
    MAX_TRUNCATION_SPLITS  the response was fine but hit the output ceiling
    OPENAI_SDK_MAX_RETRIES the request never produced a usable HTTP response

``MatchingModelMismatchError`` exists because the model that ANSWERS is what gets
priced and logged, not the model that was asked for -- an alias can resolve to a
dated snapshot, and a row attributed to a model that never served it makes the
cost column mean nothing.

WHAT CHANGED:

  * ``openai_client`` -> ``deps.get_openai_client()``. This is THE seam that
    costs money if it fails: Files 45 and 46 replace the client with a recording
    or replaying proxy, and rebinding a caller global reaches a module function
    not at all.
  * ``MATCHING_REQUEST_TIMEOUT`` -> ``config.get_matching_request_timeout()``,
    called at call time. The structured timeout is lazy in the package because
    building it constructs a throwaway OpenAI client to read the SDK's own
    default connect phase, so importing it would need credentials at import.

``with_options(max_retries=...)`` IS STILL FORBIDDEN HERE, and the reason is now
doubled: it returns a NEW client object, so the proxy's __getattr__ forwarding
hands back an UNWRAPPED client -- capture would issue a real call and record
nothing, replay would go to the network instead of serving its recording.
"""

import json
import time
from collections import Counter
from typing import Dict, List, Tuple

from oncotriage import config
from oncotriage.agent import deps
from oncotriage.agent.patient import _create_patient_summary
from oncotriage.agent.prompts import (
    PROMPT_VERSION,
    prompt_sha256,
    render_system_prompt,
)
from oncotriage.agent.response_schema import (
    EVALUATIONS_KEY,
    build_response_format,
)
from oncotriage.agent.state import (
    TRIAL_VERDICT_ELIGIBLE,
    TRIAL_VERDICT_NOT_ELIGIBLE,
    TRIAL_VERDICT_NOT_EVALUABLE,
    VERDICT_SOURCE_CANONICAL,
    VERDICT_SOURCE_UNRECOGNIZED,
    TrialMatchState,
    normalize_trial_verdict,
)
from oncotriage.config import (
    CHARS_PER_TOKEN,
    MATCHING_MAX_TOKENS,
    MATCHING_MODEL,
    MATCHING_OUTPUT_SPLIT_FRACTION,
    MATCHING_OUTPUT_TOKENS_PER_TRIAL,
    MATCHING_REASONING_EFFORT,
    MATCHING_SEED,
    MAX_LLM_CLASSIFIER_RETRIES,
    MAX_TRUNCATION_SPLITS,
    RETRY_BASE_DELAY,
)
from oncotriage.observability import console, get_logger


log = get_logger(__name__)


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Stage 5 model-call seam
# ---------------------------------------------------------------------------


class MatchingModelMismatchError(RuntimeError):
    """The API answered as a different model than MATCHING_MODEL requested.

    WHY THIS STOPS THE RUN INSTEAD OF WARNING.

    Model aliases resolve. As of 2026-08-04 gpt-5.6-terra publishes exactly one
    snapshot and it is the alias string itself, so the requested and returned
    strings are identical and this never fires. That will not stay true: the
    moment OpenAI publishes a dated snapshot the alias resolves to it, every
    verdict in the pipeline shifts, and nothing in the request changes.

    Recording the returned model in inferences.matching_model (which File 13
    already does) makes the change auditable AFTER the fact. It does not stop a
    campaign from being run half on one judge and half on another, which is
    exactly the confound this project exists to remove -- an ablation study or
    a drift baseline built across that boundary is measuring the model, not the
    thing it claims to measure. A printed warning is worse than nothing here,
    because the runs that matter are multi-day batch runs whose console nobody
    reads.

    So: raise, at the first response that disagrees, before any verdict from it
    reaches a result dict.

    A None return does NOT raise. That means the response object carried no
    model field at all -- a stub (File 37) or a pre-migration recording (File
    46) -- which is a different condition with its own handling: model_answered
    stays None and File 14 logs NULL.

    Recovery is a human decision, not an automatic one: review what changed,
    then set MATCHING_MODEL to the returned string and re-baseline. The message
    carries both strings so that decision can be made from the traceback alone.

    A RuntimeError subclass rather than a ValueError, for the same reason
    UnknownModelPricingError is: a stray `except ValueError` around a parsing
    step must not be able to eat it.
    """

    def __init__(self, requested: str, returned: str):
        self.requested = requested
        self.returned = returned
        super().__init__(
            f"Stage 5 requested model {requested!r} but the API answered as "
            f"{returned!r}. The configured model resolved to something other "
            f"than what was configured -- almost certainly an alias that now "
            f"points at a dated snapshot. Every verdict from this point on "
            f"would come from a different judge than the rows already in "
            f"inferences.db, so the run is stopped rather than continued and "
            f"logged. After reviewing what changed, set MATCHING_MODEL in "
            f"'oncotriage/config.py' to {returned!r}, add it to PRICING_CONFIG if it "
            f"is not there, and re-baseline; do not accept it silently."
        )
# MATCHING_MAX_TOKENS and MATCHING_SEED are in oncotriage/config.py, together with
# the truncation thresholds calibrated against the first of them.


def call_matching_model(system_prompt: str, user_prompt: str):
    """Issue the Stage 5 evaluation request and return the raw API response.

    Lifted out of node_llm_classifier_evaluation unchanged. It is the single point where
    the pipeline talks to the matching model, which is what lets a recording
    harness capture the request and response verbatim and a replay harness
    serve them back without a network call (45-/46- Fixture Capture/Replay).

    The caller owns error handling: this raises whatever the client raises, and
    node_llm_classifier_evaluation's except block turns that into a retry.

    REQUEST SHAPE, AND WHY EACH PARAMETER IS OR IS NOT HERE. Every one of these
    was probed live against gpt-5.6-terra on 2026-08-04 rather than inferred
    from documentation; the model page does not enumerate its own parameter
    restrictions.

      max_completion_tokens   REQUIRED. `max_tokens` is rejected outright:
                              400 unsupported_parameter, "Use
                              'max_completion_tokens' instead." Note this caps
                              reasoning AND visible output together.
      reasoning_effort        Accepted values for THIS model are none / low /
                              medium / high / xhigh. Set from File 03.
      seed                    Accepted (no error). Best-effort only; the model
                              returns no system_fingerprint.
      timeout                 CLIENT-SIDE, not a request parameter: it bounds
                              how long this process waits, and never reaches
                              the model. Set from File 03, replacing the SDK's
                              600s default. Because it cannot change the
                              response, File 45 does not record it and File 46
                              does not replay it -- unlike everything else in
                              this call, a change to it is invisible in a
                              fixture diff, and that is correct.
      max_retries             NOT SET HERE, and that is not an oversight: it
                              is set on the client itself in File 03
                              (OPENAI_SDK_MAX_RETRIES) because it cannot be
                              scoped to one call without breaking the fixture
                              harness. See the note on the return statement.
                              It is the other half of the bound -- a timeout
                              with the SDK's default of 2 retries is still
                              three attempts -- and it is the TRANSPORT budget,
                              deliberately distinct from MAX_LLM_CLASSIFIER_RETRIES,
                              which covers a response that arrived and would
                              not parse. File 03 states both, plus the
                              truncation-split budget, and the worst-case wall
                              time the three produce together.
      temperature             NOT SENT. Rejected for every value but the
                              provider default of 1, so there is nothing to
                              send. MATCHING_TEMPERATURE is None.
      response_format         SENT, as of the Structured Outputs pass, and it
                              is a strict `json_schema` built by
                              oncotriage/agent/response_schema.py. The note
                              here used to say it was held back so the model
                              migration could be measured on its own; that
                              migration is done and this is the isolated change
                              that follows it.

                              PROBED LIVE before it was wired in, on
                              2026-08-09, one call, $0.002400: gpt-5.6-terra
                              accepts strict mode with this exact schema, the
                              response parses, every enum lands in vocabulary
                              and message.refusal is None. What the probe ALSO
                              found is that the model emits object keys
                              ALPHABETICALLY rather than in the schema's
                              `properties` order -- see the ordering block in
                              response_schema.py, because that defeats the
                              prompt's "explanation before eligible" device and
                              is the one behaviour change here that nobody
                              asked for.

    Anything added to this call must also be added to the request block File 45
    records and File 46 replays, or a fixture stops being able to see it. That
    is why `response_format` is in both, and why it is built by a call HERE
    rather than passed in: the recorder reads it out of kwargs, so the fixture
    sees the schema that was actually sent rather than one re-derived at
    diff time.
    """
    # NOTE THE ASYMMETRY: timeout is set HERE, the retry budget is not.
    #
    # OPENAI_SDK_MAX_RETRIES is applied once, on the client constructor in File
    # 03, and there is no way to scope it to this call that does not break
    # something else:
    #
    #   - create() has no max_retries parameter and no **kwargs, so passing it
    #     as a kwarg raises TypeError on every call. (timeout IS in create()'s
    #     signature, alongside extra_headers/extra_query/extra_body, which is
    #     the whole reason the two look like a pair but are not.)
    #   - openai_client.with_options(max_retries=...) is the SDK's supported
    #     way, and it is the trap. It returns a NEW client object, and File
    #     45's OpenAIProxy -- the shim that records Stage 5 exchanges into a
    #     fixture -- forwards unknown attributes straight to the real inner
    #     client via __getattr__. So with_options() here would hand back an
    #     UNWRAPPED client: capture would issue a real call and record nothing,
    #     and File 46's replay would go to the network instead of serving the
    #     recording. A per-call retry override costs the entire fixture
    #     harness.
    #
    # So the retry budget is client-wide by necessity, and File 03 says so.
    return deps.get_openai_client().chat.completions.create(
        model=MATCHING_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_completion_tokens=MATCHING_MAX_TOKENS,
        reasoning_effort=MATCHING_REASONING_EFFORT,
        seed=MATCHING_SEED,
        # BUILT PER CALL, not read from a module constant. The schema is a
        # nested dict handed to an SDK that may hold or mutate it; a shared
        # module-level copy is the MATCH_TIERS hazard, one layer down. Building
        # it is a handful of dict literals.
        response_format=build_response_format(),
        # The STRUCTURED Timeout, not the bare number. A per-request timeout
        # replaces the client's Timeout object outright rather than merging with
        # it, so passing the float here would have re-flattened the connect
        # phase to 300s on this call no matter what the client carries. See
        # _structured_timeout() in File 03.
        # CALLED, not imported: the structured timeout is lazy in the package
        # because building it constructs a throwaway OpenAI client to read the
        # SDK's default connect phase.
        timeout=config.get_matching_request_timeout(),
    )


# ---------------------------------------------------------------------------
# Stage 5 output-size estimation and batch splitting
# ---------------------------------------------------------------------------

# Why a trial can end up with no evaluation. Recorded per trial so a missing
# verdict is never inferred from an absence — every trial that entered Stage 5
# leaves it either evaluated or carrying one of these.
NOT_EVALUABLE_TRUNCATION_FLOOR = "truncation_floor"
# ^ this trial was sent alone and the response still hit the token ceiling.
#   There is nothing left to split; the model cannot answer it within the
#   ceiling. Distinct from a parse failure, which is a malformed answer.
NOT_EVALUABLE_SPLIT_BUDGET = "truncation_split_budget_exhausted"
# ^ the batch containing it truncated at MAX_TRUNCATION_SPLITS levels of
#   halving and could not be split further under the budget.
NOT_EVALUABLE_MODEL_OMITTED = "omitted_from_model_response"
# ^ the call succeeded and parsed, but the model returned no entry for this
#   trial. Not a truncation and not a parse failure; the reconciliation below
#   is the only thing that would ever have noticed.

_NOT_EVALUABLE_REASONS = (
    NOT_EVALUABLE_TRUNCATION_FLOOR,
    NOT_EVALUABLE_SPLIT_BUDGET,
    NOT_EVALUABLE_MODEL_OMITTED,
)

# Finish reason the API returns when it stopped because it hit max_tokens.
FINISH_REASON_LENGTH = "length"


# ---------------------------------------------------------------------------
# Malformed response entries
# ---------------------------------------------------------------------------

# A top-level entry in the model's JSON array that is not an object at all.
#
# The response is validated as a LIST and nothing below validated its MEMBERS,
# so a list holding a bare NCT id string, a number or a null reached the
# metadata-enrichment loop and raised AttributeError on ``.get`` -- uncaught,
# out through node_llm_classifier_evaluation and out through graph.invoke, which wraps
# nothing. Confirmed by running: 'str' object has no attribute 'get', at the
# first statement of that loop, for a string, an int, a list and a null alike.
#
# A crash is the safe direction and it is not the right one: it costs the whole
# patient's run -- every trial in it, including the well-formed entries in the
# same response -- for one malformed element, and it is a recovery the file
# already performs one level down, where _normalize_arm drops a criterion that
# is not an object. So the entry is DROPPED rather than repaired, and dropping
# is the whole of it: a non-object carries no nct_id, so there is nothing to
# attribute a verdict to and no verdict of any kind may be manufactured from
# it. The trial it was meant to answer for, if it was meant to answer for one,
# is then absent from the response and the reconciliation block at the end of
# the node records it as not evaluable by nct_id -- which is a fact about a
# trial rather than a guess about a fragment.
#
# Keyed by the JSON type name, capped at a handful of keys by construction, so
# a run answers "how often, and of what shape". Module-level, following
# AGE_PARSE_FAILURES in oncotriage/agent/filtering.py, and deliberately NOT a
# key in the returned dict: the twelve characterization fixtures diff Stage 5
# field by field and a new field means recapturing all twelve.
MALFORMED_EVALUATION_ENTRIES = Counter()

# Longest fragment of a dropped entry kept for the log line. Long enough to
# recognise a bare NCT id or a stray sentence, short enough that a pathological
# element cannot put the model's clinical prose into a durable record.
_MALFORMED_ENTRY_PREVIEW_LEN = 60


# ---------------------------------------------------------------------------
# Model refusals
# ---------------------------------------------------------------------------
#
# A REFUSAL IS THE MODEL DECLINING. IT IS NOT A PARSE FAILURE, AND BEFORE THIS
# THE TWO WERE THE SAME ROW.
#
# When a model refuses, the Chat Completions message carries `refusal` (a
# string) and `content` is None. Stage 5 read only content, coerced the None to
# "", and handed "" to json.loads -- which raises JSONDecodeError, so a refusal
# was recorded as `GPT-4o JSON parse error ... Expecting value: line 1 column 1`
# and RETRIED, up to MAX_LLM_CLASSIFIER_RETRIES times, at full price, against a
# model that had already declined. Three billed calls and a record that names
# the wrong fault.
#
# The two are different facts and a reader has to be able to tell them apart:
# a parse failure says the judge tried and produced garbage (re-send it), a
# refusal says the judge would not answer (re-sending is spending money to be
# told no again). So the refusal gets its own error string, its own log event
# and its own terminal route.
#
# THE PREFIX IS A CONSTANT BECAUSE THE ROUTER READS IT. Not by string test --
# route_after_llm_classifier branches on the state key below -- but every
# consumer that greps inferences.error for refusals needs one spelling, and a
# literal in two files is a literal that drifts.
REFUSAL_ERROR_PREFIX = "Stage 5 refusal"

# Longest fragment of the model's refusal text kept in the error string. The
# refusal is model prose of unbounded length; the error column is durable and
# indexed, and the diagnostic value is in the first sentence.
_REFUSAL_PREVIEW_LEN = 300

# How often the model declined, by nothing finer than a count -- the refusal
# TEXT is not a key, because it is unbounded model output about a specific
# patient and this is a process-lifetime dict. Module-level, following
# MALFORMED_EVALUATION_ENTRIES above and AGE_PARSE_FAILURES in
# oncotriage/agent/filtering.py, and deliberately NOT a key in the returned
# dict: the characterization fixtures diff Stage 5 field by field.
REFUSALS_OBSERVED = Counter()


def _refusal_text(message) -> str:
    """The refusal string on a response message, or "" if there is none.

    Read through getattr for the same reason finish_reason and
    completion_tokens_details are: a stub response object (File 37,
    tests/test_agent_trial_verdict_normalization.py) does not define the
    attribute at all, and a recording made before the field existed does not
    carry it. Absent is "not refused", which is the behaviour this node had
    before refusals were detected -- so a client that does not report the field
    degrades to the old path rather than to a new one.

    A non-string truthy value is stringified rather than trusted: the only
    thing done with it is naming the fault.
    """
    refusal = getattr(message, "refusal", None)
    if not refusal:
        return ""
    return refusal if isinstance(refusal, str) else str(refusal)


def _unwrap_evaluations(parsed):
    """The list of trial verdicts out of a parsed Stage 5 response, or None.

    TWO SHAPES ARE ACCEPTED AND THAT IS FORCED, not generous.

    Structured Outputs requires the ROOT of a strict json_schema to be an
    object, so the schema wraps the array as ``{"evaluations": [...]}`` and
    that is what the model now emits. The system prompt still asks for a bare
    array, and a bare array is also what every response before this pass looked
    like and what an old fixture recording holds. Both parse.

    Returns None when neither shape is present, which the caller turns into the
    existing "non-list JSON" error -- unchanged, including its message, so a
    genuinely malformed response is reported exactly as it was.

    A dict carrying the key but not a list under it is NOT unwrapped: that is a
    response that agreed about the envelope and not about the contents, and
    coercing it would manufacture an empty verdict set out of a malformed
    answer.
    """
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        inner = parsed.get(EVALUATIONS_KEY)
        if isinstance(inner, list):
            return inner
    return None


def _partition_response_entries(parsed: List) -> Tuple[List[Dict], List]:
    """Split one parsed chunk into usable objects and unusable entries.

    Returns ``(objects, dropped)``. Nothing is mutated and nothing is coerced;
    the caller records the drops.
    """
    objects = []
    dropped = []
    for entry in parsed:
        if isinstance(entry, dict):
            objects.append(entry)
        else:
            dropped.append(entry)
    return objects, dropped


# Why a trial-level verdict was recorded as not evaluable. Free text, matching
# the two reasons already written into `unevaluable_trials` below, but named
# because the test that proves this path asserts on it and a literal in two
# files is a literal that drifts.
#
# NOT one of the NOT_EVALUABLE_* constants above: those are values of the
# `not_evaluable_reason` FIELD and _unevaluable_entry() indexes an explanation
# table with them, so a fourth member there would be a KeyError waiting for the
# first caller who passed it.
UNEVALUABLE_UNRECOGNIZED_VERDICT = "trial-level verdict label not recognised"


def estimate_output_tokens(trials: List[Dict]) -> int:
    """Estimate the evaluation response size for a batch, before sending it.

    HOW THIS WAS CALIBRATED, so it can be re-derived when the model changes:

        SELECT candidates_evaluated, llm_classifier_output_tokens, llm_classifier_calls,
               llm_classifier_reasoning_tokens, matching_model
        FROM inferences
        WHERE candidates_evaluated > 0 AND llm_classifier_output_tokens > 0
          AND llm_classifier_calls = 1            -- see below
        GROUP BY matching_model

    RESTRICT TO llm_classifier_calls = 1 AND GROUP BY matching_model. Both matter now.
    A split run sums its tokens across chunks and, when the split was reactive,
    includes the wasted truncated call, so output/trials over those rows
    over-states the per-trial cost. And inferences.db holds rows from two
    judges since 2026-08-04; pooling them calibrates against neither.

    llm_classifier_output_tokens ALREADY INCLUDES reasoning tokens (they are a subset of
    usage.completion_tokens, not an addition to it), so no term is added for
    them. llm_classifier_reasoning_tokens is selected above only to see how much of the
    figure is invisible: at the configured effort of 'none' it is 0.

    2026-08-04, gpt-5.6-terra at reasoning_effort='none', over the 27
    single-call runs of the step 7 measurement: mean 959, median 974, p90
    1,048, p95 1,073, max 1,138, sd 92.

    HISTORICAL, gpt-4o-2024-08-06 over 1,094 rows in inferences.db: output per
    trial mean 714, median 744, p95 1,029, max 1,165; restricted to the 555
    rows at the 15-trial cap, median 712, p90 784, p95 861, p99 1,028, max
    1,062. Kept because those rows are still in the table and still priced.

    WHAT DID NOT PREDICT ANYTHING. Fitting output against both trial count and
    criteria length — the criteria text measured with File 11's characters/4
    proxy, taken from the llm_classifier_prompt column — gives

        output ~= 708 * trials + (-0.0107) * criteria_tokens

    with a residual standard deviation of 1,935 tokens, identical to the
    trial-count-only model. The criteria-length term is negative, negligible,
    and carries no signal. That is not what one would assume: the response is
    one verdict block per trial with a bounded number of criteria in it, so a
    trial with 4,000 characters of criteria costs about the same to answer as
    one with 800. The estimate is therefore linear in trial count alone, and
    the CHARS_PER_TOKEN proxy is applied to the criteria only as a tie-breaker
    for pathological inputs, not as a driver.

    Returns the estimated output tokens for this batch.
    """
    if not trials:
        return 0

    base = MATCHING_OUTPUT_TOKENS_PER_TRIAL * len(trials)

    # The measured relationship is flat in criteria length, so this contributes
    # nothing on ordinary input. It exists for the case the calibration set
    # contains none of: a trial whose criteria text is so long that the model
    # must quote more of it. Bounded to a quarter of the per-trial allowance so
    # it can never dominate a figure the data says is driven by count.
    criteria_chars = 0
    for trial_obj in trials:
        eligibility = trial_obj.get("trial", {}).get("eligibility", {})
        criteria_chars += len(eligibility.get("inclusion_criteria") or "")
        criteria_chars += len(eligibility.get("exclusion_criteria") or "")
    criteria_tokens = criteria_chars / CHARS_PER_TOKEN
    criteria_component = min(
        criteria_tokens * 0.05,
        0.25 * MATCHING_OUTPUT_TOKENS_PER_TRIAL * len(trials),
    )

    return int(base + criteria_component)


def _split_in_half(trials: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """Halve a batch, keeping order. The larger half goes first on odd counts."""
    midpoint = (len(trials) + 1) // 2
    return trials[:midpoint], trials[midpoint:]


def _build_trials_text(trials: List[Dict]) -> str:
    """Render one batch of trials for the user prompt.

    Numbering restarts at 1 within each chunk. The model is told to key its
    output on nct_id, and every merge downstream matches on nct_id, so the
    ordinal is presentation only — but it is worth saying out loud, because a
    chunked run makes "Trial 1" appear more than once in a single inference.
    """
    trials_text = ""
    for idx, trial_obj in enumerate(trials):
        trial = trial_obj["trial"]
        trials_text += f"""Trial {idx + 1} ({trial['nct_id']}, {trial['phase']}):
{trial['eligibility']['inclusion_criteria']}
{trial['eligibility']['exclusion_criteria']}

---
"""
    return trials_text


def _unevaluable_entry(trial_obj: Dict, reason: str) -> Dict:
    """A verdict-shaped record for a trial that could not be evaluated.

    Carries the same keys node_finalize and File 14 read off a real evaluation,
    so it flows through the rest of the pipeline without special-casing, and it
    states its reason rather than being an absence someone has to explain.
    """
    trial = trial_obj["trial"]
    return {
        "nct_id": trial["nct_id"],
        "title": trial.get("title", "No title"),
        "phase": trial.get("phase", "N/A"),
        "eligible": "not_evaluable",
        "match_score": 0.0,
        "score_confirmed": 0,
        "score_denominator": 0,
        "criteria_not_applicable": 0,
        "criteria": [],
        "not_evaluable_reason": reason,
        "assessment": {
            NOT_EVALUABLE_TRUNCATION_FLOOR:
                "The model's response exceeded its output ceiling with this "
                "trial sent on its own, so there was no smaller batch to fall "
                "back to. Not assessed.",
            NOT_EVALUABLE_SPLIT_BUDGET:
                "The batch containing this trial kept exceeding the model's "
                "output ceiling and reached the split limit. Not assessed.",
            NOT_EVALUABLE_MODEL_OMITTED:
                "The model returned a well-formed response that contained no "
                "entry for this trial. Not assessed.",
        }[reason],
    }


def node_llm_classifier_evaluation(state: TrialMatchState) -> dict:
    """
    Stage 5: LLM classifier, criterion-level evaluation.

    Sends ALL filtered trials to the classifier in a SINGLE call. Which model
    that is comes from ``config.MATCHING_MODEL`` and is NOT named here: this
    node was called ``node_gpt4o_evaluation`` while the judge was gpt-4o, the
    judge became gpt-5.6-terra on 2026-08-04, and the name went stale in place.
    The classifier evaluates every inclusion/exclusion criterion for each trial
    and returns structured JSON with match scores and explanations.

    On JSON parse failure or API error, sets error flag so the retry
    router (conditional edge) can loop back for another attempt.
    Up to MAX_LLM_CLASSIFIER_RETRIES attempts with exponential backoff.

    Temperature = 0 for deterministic, reproducible medical decisions.
    """
    
    start = time.time()

    patient_data = state["patient_data"]
    trials = state["filtered_trials"]
    retry_count = state.get("llm_classifier_retries", 0)
    
    # Accumulate timing across retries (previous attempts' time is already in stage_timings)
    prior_llm_classifier_time = state.get("stage_timings", {}).get("llm_classifier_evaluation", 0.0)

    # Exponential backoff on retries (skip delay on first attempt)
    if retry_count > 0:
        delay = RETRY_BASE_DELAY * (2 ** (retry_count - 1))
        log.info("backing off before a Stage 5 retry", stage=5,
                 retry=retry_count, max_retries=MAX_LLM_CLASSIFIER_RETRIES,
                 delay_s=delay)
        time.sleep(delay)

    # Build patient summary
    patient_summary = _create_patient_summary(patient_data)

    # Build trials text for prompt
    # Only eligibility criteria sent to GPT-4o. Title, conditions, brief
    # summary, interventions stripped to prevent GPT-4o from performing
    # its own disease relevance check. Disease relevance enforced upstream by
    # hybrid retrieval, cross-encoder reranking and — when it ran — the MeSH
    # site filter. Whether it ran is what Section 2 below is conditional on.
    trials_text = ""
    for idx, trial_obj in enumerate(trials):
        trial = trial_obj["trial"]

        trials_text += f"""Trial {idx + 1} ({trial['nct_id']}, {trial['phase']}):
{trial['eligibility']['inclusion_criteria']}
{trial['eligibility']['exclusion_criteria']}

---
"""


    # ------------------------------------------------------------------
    # Section 2 of the system prompt is an assertion about THIS run
    # ------------------------------------------------------------------
    # It told the model that disease relevance "has already been confirmed"
    # and then forbade it from assessing relevance at all. That pair of
    # sentences is only sound when Stage 4's cancer site filter ran, and it is
    # conditional on three things (the MeSH data files being loaded, the
    # patient resolving to specific C04 trees, and the ablation flag being
    # off). When any of them fails the model was handed a false premise
    # together with a rule preventing it from noticing.
    #
    # What limits the damage, and is recorded here rather than assumed:
    #   - The indexed corpus is oncology-only, so "every trial is a cancer
    #     trial" holds regardless. The claim that fails is the narrower one,
    #     that the trial matches THIS patient's cancer site.
    #   - Only eligibility criteria text is sent (see the trials_text build
    #     above), and RULE 3's categorically-different-diseases branch already
    #     turns an off-site trial into a criterion-level "not_met" whenever the
    #     criteria name the disease, which most oncology criteria do.
    # The residual exposure is a trial whose criteria never state the disease,
    # evaluated for a patient whose site was never checked. The unconfirmed
    # variant below lifts the prohibition for exactly that case.
    #
    # False when Stage 4 did not record the flag at all (the state key is
    # absent only if Stage 4 never ran), which is the conservative direction:
    # never assert a check that cannot be shown to have happened.
    _mesh_filter_applied = bool(state.get("mesh_filter_applied", False))
    _mesh_filter_reason = state.get("mesh_filter_skip_reason") or "unrecorded"

    # THE TEMPLATE MOVED; THE CONDITIONALITY DID NOT. Section 2's two variants,
    # the RULE 4 anchor and the trial count all live in
    # oncotriage/agent/prompts.py now. What stays here is reading THIS RUN's
    # state, which is what selects the variant -- the comment block above is
    # about that read and is deliberately not moved with the text.
    # render_system_prompt() returns the same bytes this function built inline;
    # that was proved per variant by rendering both from git HEAD and from the
    # module and comparing sha256, not by inspection.
    #
    # ONE HASH PER INFERENCE IS CORRECT EVEN WHEN THE BATCH SPLITS. The system
    # prompt is rendered ONCE, here, above the split loop, and every chunk is
    # sent with this identical string -- only the user message differs per
    # chunk (see call_matching_model(system_prompt, _user_prompt_for(chunk))
    # below). A hash per call would therefore record the same value N times and
    # say nothing a single column does not.
    system_prompt = render_system_prompt(
        mesh_filter_applied=_mesh_filter_applied,
        mesh_filter_skip_reason=_mesh_filter_reason,
        trial_count=len(trials),
    )
    # The mechanical record of what was actually sent, beside PROMPT_VERSION's
    # record of what was intended. Computed here rather than at logging time so
    # it is the hash of the string this node handed the model, not a re-render
    # from state that could disagree with it.
    system_prompt_sha256 = prompt_sha256(system_prompt)


# ================================================================
# USER MESSAGE
# ================================================================

    def _user_prompt_for(chunk: List[Dict]) -> str:
        return f"""
PATIENT RECORD:
{patient_summary}

CLINICAL TRIALS:
{_build_trials_text(chunk)}
"""

    # ── Store full prompt for DB logging (system + user combined) ──────────
    # The WHOLE batch, not the chunk that happened to be sent last. When a run
    # splits, the stored prompt is the one the run would have sent unsplit,
    # which is the thing that is comparable across runs; the split itself is
    # recorded in llm_classifier_truncation_splits, not by mutating this.
    prompt = f"[SYSTEM]\n{system_prompt}\n\n[USER]\n{_user_prompt_for(trials)}"

    # ------------------------------------------------------------------
    # Proactive: split before sending if the batch is expected to overflow
    # ------------------------------------------------------------------
    estimated_output = estimate_output_tokens(trials)
    split_threshold = int(MATCHING_MAX_TOKENS * MATCHING_OUTPUT_SPLIT_FRACTION)

    pending = []          # LIFO of (chunk, split_depth), so a split is depth-first
    proactive_splits = 0
    initial_chunks = [trials]
    if estimated_output > split_threshold:
        depth = 0
        while depth < MAX_TRUNCATION_SPLITS and any(
            estimate_output_tokens(c) > split_threshold and len(c) > 1
            for c in initial_chunks
        ):
            halved = []
            for chunk in initial_chunks:
                if estimate_output_tokens(chunk) > split_threshold and len(chunk) > 1:
                    left, right = _split_in_half(chunk)
                    halved.extend([left, right])
                    proactive_splits += 1
                else:
                    halved.append(chunk)
            initial_chunks = halved
            depth += 1
        log.info("pre-splitting the Stage 5 request: the output estimate is "
                 "over the split threshold", stage=5, event="pre_split",
                 tokens_estimated=estimated_output, threshold=split_threshold,
                 chunks=len(initial_chunks), depth=depth)
        pending = [(c, depth) for c in reversed(initial_chunks)]
    else:
        pending = [(trials, 0)]

    # ------------------------------------------------------------------
    # Evaluate, splitting reactively on finish_reason == "length"
    # ------------------------------------------------------------------
    evaluations = []
    unevaluable = []              # trials accounted for without a verdict
    truncation_splits = proactive_splits
    truncations_observed = 0
    input_tokens = 0
    output_tokens = 0
    # Reasoning tokens are a SUBSET of output_tokens, never an addition to it.
    # Verified live 2026-08-04: usage.completion_tokens minus
    # usage.completion_tokens_details.reasoning_tokens tracks the visible
    # content length across effort levels (52/0, 133/72, 147/98, 123/68 for
    # none/low/medium/high on the same prompt). OpenAI's reasoning guide states
    # the same. So this is recorded as a BREAKDOWN of output_tokens and must
    # never be added to it for costing -- doing so would bill every reasoning
    # token twice.
    reasoning_tokens = 0
    reasoning_tokens_reported = False   # any response carried the breakdown
    # The model string the API ANSWERED with, as opposed to MATCHING_MODEL,
    # which is what was asked for. They differ whenever an alias resolves to a
    # dated snapshot (gpt-4o-2024-08-06 is one). Last writer wins across a split
    # batch, which is correct: every chunk of one Stage 5 goes to the same
    # model, and if a provider ever routed them differently the last value is
    # still a model that genuinely served this run rather than a config string
    # that may have served none of it.
    model_answered = None
    response_text = ""
    calls_made = 0
    _finish_reason_warned = False
    # Once per run, not once per chunk. See the guard below the parse.
    _reasoning_order_warned = False

    while pending:
        chunk, depth = pending.pop()

        try:
            response = call_matching_model(system_prompt, _user_prompt_for(chunk))
            choice = response.choices[0]
            chunk_text = (choice.message.content or "").strip()
        except Exception as e:
            # API-level failure (timeout, rate limit, network error). This is
            # the parse/API budget, not the split budget.
            elapsed = time.time() - start
            error_msg = f"GPT-4o API error (attempt {retry_count + 1}): {str(e)}"
            log.error("Stage 5 API call failed", stage=5, status="error",
                      retry=retry_count + 1, error_type=type(e).__name__,
                      error_message=str(e))
            return {
                "evaluations": [],
                "llm_classifier_retries": retry_count + 1,
                "llm_classifier_truncation_splits": truncation_splits,
                "llm_classifier_output_tokens_estimated": estimated_output,
                "llm_classifier_raw_response": "",
                "error": error_msg,
                # The prompt WAS rendered before this return -- every one of Stage
                # 5's early returns sits below the render call -- so the hash is a
                # fact about this run and is carried. A failed run is exactly the
                # run worth knowing the prompt identity of; a version that reached
                # the database only on success would be missing for the rows most
                # worth investigating. _pipeline_provenance() reads both off state.
                "llm_classifier_prompt_version": PROMPT_VERSION,
                "llm_classifier_prompt_sha256": system_prompt_sha256,
                "stage_timings": {**state.get("stage_timings", {}), "llm_classifier_evaluation": round(prior_llm_classifier_time + elapsed, 3)}
            }

        calls_made += 1
        input_tokens += response.usage.prompt_tokens
        output_tokens += response.usage.completion_tokens
        response_text = chunk_text

        # Read defensively for the same reason finish_reason is below: File 37
        # drives this node with a stub response and File 46 serves recordings
        # made before the field existed. Absent is reported as NULL, never as
        # 0 -- "this response carried no reasoning breakdown" is not "this
        # response spent no reasoning tokens", and a non-reasoning model
        # legitimately reports 0.
        _usage_details = getattr(response.usage, "completion_tokens_details", None)
        _reasoning = getattr(_usage_details, "reasoning_tokens", None)
        if _reasoning is not None:
            reasoning_tokens += _reasoning
            reasoning_tokens_reported = True

        # The model that ANSWERED, checked against the one requested BEFORE its
        # verdicts are parsed or accumulated. Placed here rather than at logging
        # time so it fires on the first call of the first patient: a mismatch
        # discovered at log time has already spent a whole batch on the wrong
        # judge. See MatchingModelMismatchError for why this raises.
        #
        # None means the response carried no model field (a stub, or a
        # pre-migration recording). That is a different condition and falls
        # through to the existing NULL handling untouched.
        _model_returned = getattr(response, "model", None)
        if _model_returned is not None and _model_returned != MATCHING_MODEL:
            raise MatchingModelMismatchError(MATCHING_MODEL, _model_returned)

        model_answered = _model_returned or model_answered

        # ── The model declined ─────────────────────────────────────────────
        #
        # Checked HERE: after the call is counted and the answering model is
        # known, so the record is not anonymous, and BEFORE the truncation
        # branch, the fence strip and json.loads, none of which has anything to
        # do with a refusal. See REFUSAL_ERROR_PREFIX above for why this is not
        # the parse-error path.
        #
        # IT DOES NOT SPEND A PARSE RETRY. `llm_classifier_retries` is carried
        # through as `retry_count`, unincremented, and the run is routed
        # straight to the error handler by `llm_classifier_refusal` -- see
        # route_after_llm_classifier. Incrementing instead would have been the
        # smaller edit and it would be a lie in the column: no retry was spent,
        # and re-sending an identical request to a model that has refused it
        # buys another refusal at full price.
        #
        # A refusal ends the whole node, not just this chunk. Every chunk of a
        # split batch carries the same system prompt and the same patient; a
        # model that declined one has declined the premise, and issuing the
        # remaining chunks would spend money to collect the same answer N more
        # times.
        _refusal = _refusal_text(choice.message)
        if _refusal:
            elapsed = time.time() - start
            REFUSALS_OBSERVED[MATCHING_MODEL] += 1
            error_msg = (f"{REFUSAL_ERROR_PREFIX}: the model declined to "
                         f"answer (attempt {retry_count + 1}): "
                         f"{_refusal[:_REFUSAL_PREVIEW_LEN]}")
            # A DISTINCT EVENT, which is the whole point of the path: a query
            # counting event="refusal" must not also be counting malformed
            # JSON, and before this both arrived as status=error with
            # error_type=JSONDecodeError. The refusal TEXT is not a field --
            # it is model prose about this patient and the structured record is
            # durable -- so only its length travels, on the same rule as the
            # parse-failure preview below.
            log.error("Stage 5: the model refused to answer", stage=5,
                      status="error", event="refusal", retry=retry_count,
                      response_chars=len(_refusal))
            console.out(f"  [Stage 5] refusal: {_refusal[:_REFUSAL_PREVIEW_LEN]}")
            return {
                "evaluations": [],
                # UNINCREMENTED, deliberately. See above.
                "llm_classifier_retries": retry_count,
                # The flag the router terminates on. Written only here.
                "llm_classifier_refusal": _refusal[:_REFUSAL_PREVIEW_LEN],
                "llm_classifier_truncation_splits": truncation_splits,
                "llm_classifier_output_tokens_estimated": estimated_output,
                # The refusal text is the whole of what the model returned, so
                # it IS the raw response for this run. Capped by the same rule
                # as the error string.
                "llm_classifier_raw_response": _refusal[:_REFUSAL_PREVIEW_LEN],
                # A model DID answer -- by declining -- so the run is not
                # anonymous, on the same argument as the parse-error path.
                "matching_model": model_answered,
                "error": error_msg,
                "llm_classifier_prompt_version": PROMPT_VERSION,
                "llm_classifier_prompt_sha256": system_prompt_sha256,
                "stage_timings": {**state.get("stage_timings", {}), "llm_classifier_evaluation": round(prior_llm_classifier_time + elapsed, 3)}
            }

        # finish_reason is read defensively because not every client object
        # that reaches here carries one: the retrieval-observability test
        # (File 37) drives this node with a stub response, and a stub is
        # exactly the case where silently assuming "truncated" would be wrong.
        #
        # Absent is treated as "not truncated", which is the behaviour this
        # node had before truncation was detected at all — so a client that
        # does not report it degrades to the old path rather than to a new
        # one. It is announced rather than assumed: without the field there is
        # no truncation detection on this run, and that is worth a line.
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason is None and not _finish_reason_warned:
            log.warning("the response object carries no finish_reason; "
                        "truncation cannot be detected on this run, falling "
                        "back to JSON-parse failure as the only signal",
                        stage=5, event="finish_reason_absent", degraded=True)
            _finish_reason_warned = True

        # ── Reactive: the response was cut off, not malformed ──────────────
        #
        # Read from finish_reason rather than inferred from a token count. The
        # previous guard compared output_tokens against 12000, printed a cost
        # warning, and let the truncated text fall through to json.loads, which
        # failed and retried an IDENTICAL request that truncated again — three
        # times, then an error result. finish_reason is the API stating the
        # fact directly.
        if finish_reason == FINISH_REASON_LENGTH:
            truncations_observed += 1

            if len(chunk) == 1:
                # THE FLOOR. One trial, still over the ceiling; there is
                # nothing left to halve. Recorded, not retried and not dropped.
                log.warning("truncation floor: a single trial exceeds the "
                            "output ceiling; recording it as not evaluable",
                            stage=5, event="truncation_floor",
                            nct_id=chunk[0]["trial"]["nct_id"], depth=depth)
                unevaluable.append(
                    _unevaluable_entry(chunk[0], NOT_EVALUABLE_TRUNCATION_FLOOR)
                )
                continue

            if depth >= MAX_TRUNCATION_SPLITS:
                log.warning("truncation split budget exhausted; recording the "
                            "chunk as not evaluable", stage=5,
                            event="split_budget_exhausted", depth=depth,
                            count=len(chunk), max_retries=MAX_TRUNCATION_SPLITS)
                unevaluable.extend(
                    _unevaluable_entry(t, NOT_EVALUABLE_SPLIT_BUDGET)
                    for t in chunk
                )
                continue

            left, right = _split_in_half(chunk)
            truncation_splits += 1
            log.info("response truncated; splitting the chunk and retrying as "
                     "two calls", stage=5, event="truncation_split",
                     depth=depth, count=len(chunk), chunks=truncation_splits)
            # Pushed right-then-left so the LIFO pops left first and the
            # evaluation order stays the batch's original order.
            pending.append((right, depth + 1))
            pending.append((left, depth + 1))
            continue

        # Clean markdown fences if present
        if chunk_text.startswith("```"):
            chunk_text = chunk_text.split("```")[1]
            if chunk_text.startswith("json"):
                chunk_text = chunk_text[4:]
            chunk_text = chunk_text.strip()

        # Parse JSON response
        try:
            parsed = json.loads(chunk_text)
        except json.JSONDecodeError as e:
            # JSON parse failure: set error for the retry router. Separate
            # budget from the splits above -- a malformed answer is not a long
            # one, and re-sending is the right response to it.
            elapsed = time.time() - start
            error_msg = f"GPT-4o JSON parse error (attempt {retry_count + 1}): {str(e)}"
            log.error("Stage 5 response was not valid JSON", stage=5,
                      status="error", retry=retry_count + 1,
                      error_type=type(e).__name__, error_message=str(e),
                      response_chars=len(chunk_text))
            # THE PREVIEW GOES TO THE CONSOLE, NOT THE RECORD. It is the one
            # thing that diagnoses a malformed answer and it is also the
            # model's criterion-level reasoning about this patient, so it is
            # not a field: `response_preview` is absent from LOGGABLE_FIELDS on
            # purpose and would be dropped if it were passed. Console output is
            # transient and unindexed; the structured record is neither.
            console.out(f"  [Stage 5] response preview: {chunk_text[:300]}")
            return {
                "evaluations": [],
                "llm_classifier_retries": retry_count + 1,
                "llm_classifier_truncation_splits": truncation_splits,
                "llm_classifier_output_tokens_estimated": estimated_output,
                "llm_classifier_raw_response": chunk_text,
                # A model DID answer here -- badly, but it answered -- so the
                # run is not anonymous. Carried so that a patient whose retries
                # all end in malformed JSON still logs which model produced
                # them instead of a NULL that reads as "Stage 5 never ran".
                # The token counters are deliberately not carried: they are not
                # accumulated on this path at all (a pre-existing gap), and
                # reporting a reasoning subtotal against a zero output total
                # would be arithmetically incoherent.
                "matching_model": model_answered,
                "error": error_msg,
                # The prompt WAS rendered before this return -- every one of Stage
                # 5's early returns sits below the render call -- so the hash is a
                # fact about this run and is carried. A failed run is exactly the
                # run worth knowing the prompt identity of; a version that reached
                # the database only on success would be missing for the rows most
                # worth investigating. _pipeline_provenance() reads both off state.
                "llm_classifier_prompt_version": PROMPT_VERSION,
                "llm_classifier_prompt_sha256": system_prompt_sha256,
                "stage_timings": {**state.get("stage_timings", {}), "llm_classifier_evaluation": round(prior_llm_classifier_time + elapsed, 3)}
            }

        # THE ARRAY, out of whichever envelope carried it. Structured Outputs
        # forces an object root, so the model now sends {"evaluations": [...]};
        # a bare array -- the pre-pass shape, an old recording, any run where
        # the response format did not take -- is accepted unchanged. See
        # _unwrap_evaluations. None means neither shape, which falls into the
        # branch below with its message untouched.
        #
        # THE ORIGINAL `parsed` IS NOT OVERWRITTEN UNTIL THE GUARD HAS PASSED,
        # and that is not tidiness: the error message below reports
        # type(parsed).__name__, so assigning the unwrap result first would have
        # made every failure report `type=NoneType` -- the type of the failure
        # itself -- instead of the dict or string the model actually sent. The
        # message is the only diagnosis this path produces.
        _unwrapped = _unwrap_evaluations(parsed)
        if not isinstance(_unwrapped, list):
            elapsed = time.time() - start
            error_msg = f"GPT-4o returned non-list JSON (type={type(parsed).__name__})"
            log.error("Stage 5 returned JSON that is not a list", stage=5,
                      status="error", retry=retry_count + 1,
                      error_message=error_msg,
                      response_chars=len(chunk_text))
            # Same reasoning as the parse-error branch above.
            console.out(f"  [Stage 5] response preview: {chunk_text[:300]}")
            return {
                "evaluations": [],
                "llm_classifier_retries": retry_count + 1,
                "llm_classifier_truncation_splits": truncation_splits,
                "llm_classifier_output_tokens_estimated": estimated_output,
                "llm_classifier_raw_response": chunk_text,
                # A model DID answer here -- badly, but it answered -- so the
                # run is not anonymous. Carried so that a patient whose retries
                # all end in malformed JSON still logs which model produced
                # them instead of a NULL that reads as "Stage 5 never ran".
                # The token counters are deliberately not carried: they are not
                # accumulated on this path at all (a pre-existing gap), and
                # reporting a reasoning subtotal against a zero output total
                # would be arithmetically incoherent.
                "matching_model": model_answered,
                "error": error_msg,
                # The prompt WAS rendered before this return -- every one of Stage
                # 5's early returns sits below the render call -- so the hash is a
                # fact about this run and is carried. A failed run is exactly the
                # run worth knowing the prompt identity of; a version that reached
                # the database only on success would be missing for the rows most
                # worth investigating. _pipeline_provenance() reads both off state.
                "llm_classifier_prompt_version": PROMPT_VERSION,
                "llm_classifier_prompt_sha256": system_prompt_sha256,
                "stage_timings": {**state.get("stage_timings", {}), "llm_classifier_evaluation": round(prior_llm_classifier_time + elapsed, 3)}
            }

        parsed = _unwrapped

        # ── The reasoning-first design, checked on the bytes ────────────────
        #
        # ALPHABETICAL KEY EMISSION IS OBSERVED BEHAVIOUR OF THE CURRENT MODEL,
        # NOT A DOCUMENTED API GUARANTEE. The field is named "assessment" so
        # that it sorts before "eligible" and the model writes its reasoning
        # before its verdict; nothing in the Structured Outputs contract
        # promises that ordering, so a provider change could silently put the
        # verdict first again and every symptom would be a slightly worse
        # classifier with no signal anywhere. This turns that into a visible
        # event.
        #
        # On the TEXT, not on the parsed dict: json.loads preserves insertion
        # order, but the whole question is what the model emitted, and re-asking
        # the parsed object is one indirection further from the bytes. Two
        # str.find calls on a string already in memory.
        #
        # Both needles are quoted, which is what makes them KEY positions:
        # '"eligible"' does not match inside '"not_eligible"' (the preceding
        # character is an underscore, not a quote), and a key precedes its own
        # value, so the first hit of each is the first trial object's key.
        # Either needle absent means the shape is not what this guard describes
        # and it says nothing -- the schema's `required` is what enforces
        # presence, and a guard that also reported absence would fire twice for
        # one fault.
        #
        # WARN ONLY, ONCE PER RUN. It never fails the run: the response is
        # valid, the verdicts are usable, and refusing them would trade a
        # quality signal for an outage. Once, because a split batch would
        # otherwise report the identical finding per chunk.
        if not _reasoning_order_warned:
            _first_assessment = chunk_text.find('"assessment"')
            _first_eligible = chunk_text.find('"eligible"')
            if (_first_assessment >= 0 and _first_eligible >= 0
                    and _first_assessment > _first_eligible):
                _reasoning_order_warned = True
                log.warning("the model emitted its verdict before its "
                            "assessment; the reasoning-first design of the "
                            "Stage 5 prompt is no longer in force and the "
                            "field name may need to change again", stage=5,
                            event="reasoning_order_regression",
                            response_chars=len(chunk_text))

        # A well-formed response is a list OF OBJECTS, and only the list half
        # was checked. See MALFORMED_EVALUATION_ENTRIES for why a non-object is
        # dropped and counted here rather than left to raise AttributeError in
        # the enrichment loop below.
        _objects, _dropped = _partition_response_entries(parsed)
        if _dropped:
            for _entry in _dropped:
                MALFORMED_EVALUATION_ENTRIES[type(_entry).__name__] += 1
            log.warning("the model returned entries that are not objects; "
                        "dropping them -- a non-object carries no nct_id and "
                        "cannot become a verdict. Any trial they were meant to "
                        "answer for is recorded by the reconciliation below",
                        stage=5, event="malformed_entry",
                        count=len(_dropped),
                        # The TYPE is the diagnosis; the fragment is capped and
                        # goes to the console only, on the same rule as the
                        # parse-failure preview above -- it is model output
                        # about this patient and the structured record is
                        # durable and indexed.
                        error_type=",".join(sorted(
                            {type(e).__name__ for e in _dropped})))
            console.out("  [Stage 5] dropped non-object entries: " + "; ".join(
                repr(e)[:_MALFORMED_ENTRY_PREVIEW_LEN] for e in _dropped[:5]))
        evaluations.extend(_objects)

    if truncations_observed:
        log.info("responses hit the output ceiling", stage=5,
                 event="truncation_summary", count=truncations_observed,
                 threshold=MATCHING_MAX_TOKENS, chunks=truncation_splits,
                 calls=calls_made)

    # ── Estimate against actual, so the calibration can be tightened ───────
    # Logged every run, not only when it matters. The constants in File 03 were
    # derived from this column pair on 1,094 historical rows; recording the
    # estimate beside the outcome is what lets the next derivation be better.
    # The reasoning share is printed beside the total rather than added to it,
    # so the line reads the way the billing does: one output figure, with the
    # invisible part of it named.
    # The reasoning share is carried BESIDE the total rather than added to it,
    # so the record reads the way the billing does: one output figure, with the
    # invisible part of it named. `None` when the API did not report it -- never
    # 0, which would assert a share nothing measured.
    log.info("Stage 5 output token accounting", stage=5,
             event="output_tokens", tokens_estimated=estimated_output,
             tokens_actual=output_tokens,
             tokens_reasoning=reasoning_tokens if reasoning_tokens_reported
                              else None,
             calls=calls_made,
             estimate_ratio=(round(output_tokens / estimated_output, 3)
                             if estimated_output else None))

    # SUCCESS: enrich evaluations with trial metadata (title, phase)
    for eval_result in evaluations:
        nct_id = eval_result.get("nct_id", "")
        for trial_obj in trials:
            if trial_obj["trial"]["nct_id"] == nct_id:
                eval_result["title"] = trial_obj["trial"].get("title", "No title")
                eval_result["phase"] = trial_obj["trial"].get("phase", "N/A")
                break
    
    # ── Inline parsing: normalize labels, consistency check, recompute score ──
    #
    # ORDER IS LOAD-BEARING. Criterion-label normalization runs FIRST, on every
    # trial, on every trial-level branch. Only then is the disqualification
    # check applied.
    #
    # The disqualification check scans one vocabulary per arm: "not_met" on
    # inclusions, "violated" on exclusions. A cross-vocabulary label -- e.g.
    # "violated" written on an INCLUSION criterion -- is matched by neither
    # scan. Running the check first therefore let such a trial pass as
    # "eligible" while the criterion was stored with a disqualifying label:
    # a record that is internally contradictory and a clinical false positive.
    # Normalizing first removes that state entirely.

    # Per-arm vocabularies (Section 1 of the system prompt). Disjoint by
    # construction, so a status from the wrong list is not a disguised
    # disqualifier -- it is uninterpretable output.
    _INCLUSION_STATUSES = frozenset({"met", "not_met", "not_evaluable"})
    _EXCLUSION_STATUSES = frozenset({"not_violated", "violated", "not_evaluable"})

    # The trial-level vocabulary lives in oncotriage/agent/state.py and is
    # reached ONLY through normalize_trial_verdict, which node_finalize calls
    # too. The three-member tuple that used to be here was consulted by exactly
    # one `not in` test; leaving it as a local nothing reads is the declared-
    # and-never-read shape the project's own scan exists to report.

    label_remaps = []       # audit log: criterion labels outside their vocabulary
    unevaluable_trials = []  # audit log: trials that could not be evaluated
    verdict_normalizations = []  # audit log: trial-level labels not written canonically

    # ── Not-applicable criteria are scored by neither party ──────────────────
    #
    # Section 3 of the system prompt maps a criterion whose subject matter is
    # biologically impossible for this patient onto "not_violated" (exclusion)
    # or "met" (inclusion). Those are the same labels a genuinely confirmed
    # criterion carries, so a naive confirmed/total ratio counts them as
    # evidence of fit.
    #
    # In oncology, inapplicable exclusions are near-universal and sex-linked:
    # pregnancy, lactation, and contraception criteria appear on most trials
    # and resolve to "not_violated" for every male patient. Counting them
    # inflates match_score for male patients over female patients on the SAME
    # trial, from criteria neither patient was actually evaluated against.
    #
    # A criterion that cannot apply is evidence of nothing. It is removed from
    # the numerator AND the denominator, so the score reports the fraction of
    # applicable criteria that were confirmed.
    _NOT_APPLICABLE_PREFIX = "not applicable"

    def _is_not_applicable_patient_value(pv) -> bool:
        """True if patient_value marks the criterion as inapplicable to this patient."""
        if not isinstance(pv, str):
            return False
        return pv.strip().lower().startswith(_NOT_APPLICABLE_PREFIX)

    not_applicable_excluded = []  # audit log: criteria dropped from scoring

    def _compute_match_score(inc, exc, nct_id):
        """
        Recompute match_score over APPLICABLE criteria only.

        Returns (score, confirmed, denominator, n_not_applicable). A trial whose
        every criterion is inapplicable has denominator 0 and scores 0.0: no
        applicable criterion was confirmed, so nothing supports a positive score.
        Every excluded criterion is appended to `not_applicable_excluded`.
        """
        confirmed = 0
        denominator = 0
        n_na = 0

        for arm, criteria, confirming_status in (
            ("inclusion", inc, "met"),
            ("exclusion", exc, "not_violated"),
        ):
            for c in criteria:
                pv = c.get("patient_value", "")
                if _is_not_applicable_patient_value(pv):
                    n_na += 1
                    not_applicable_excluded.append({
                        "nct_id": nct_id,
                        "arm": arm,
                        "criterion": str(c.get("criterion", ""))[:200],
                        "patient_value": pv,
                        "status": c.get("status", ""),
                    })
                    continue
                denominator += 1
                if c.get("status") == confirming_status:
                    confirmed += 1

        score = round(confirmed / denominator, 2) if denominator > 0 else 0.0
        return score, confirmed, denominator, n_na

    def _record_score(eval_result, inc, exc, nct_id):
        """Write match_score and its provenance fields onto one evaluation."""
        score, confirmed, denominator, n_na = _compute_match_score(inc, exc, nct_id)
        eval_result["match_score"] = score
        eval_result["score_confirmed"] = confirmed
        eval_result["score_denominator"] = denominator
        eval_result["criteria_not_applicable"] = n_na
        if denominator == 0:
            log.info("every criterion was inapplicable to this patient; "
                     "match_score 0.0 over an empty denominator", stage=5,
                     event="empty_denominator", nct_id=nct_id or None,
                     criteria_not_applicable=n_na)
        return score

    def _record_zero_score(eval_result, inc, exc):
        """
        Score fields for a trial whose score is 0.0 by verdict, not by ratio
        (rejected, or not evaluated). Denominator/confirmed are still recorded
        so a zero score is distinguishable from an unscored one downstream.
        """
        eval_result["match_score"] = 0.0
        eval_result["score_confirmed"] = 0
        eval_result["score_denominator"] = 0
        eval_result["criteria_not_applicable"] = sum(
            1 for c in list(inc) + list(exc)
            if isinstance(c, dict)
            and _is_not_applicable_patient_value(c.get("patient_value", ""))
        )

    def _normalize_arm(criteria, allowed, arm, nct_id):
        """
        Coerce every criterion in one arm into that arm's vocabulary.

        A status outside `allowed` resolves to "not_evaluable" rather than to
        the nearest same-meaning label in the correct vocabulary. Guessing the
        model's intent would let an unparseable label disqualify a patient with
        no quotable evidence behind it, which constraint C5 forbids.

        Non-dict entries are dropped: nothing downstream can read them.

        Returns the cleaned list. Every change is appended to `label_remaps`.
        """
        cleaned = []
        for c in criteria:
            if not isinstance(c, dict):
                label_remaps.append({
                    "nct_id": nct_id,
                    "arm": arm,
                    "criterion": str(c)[:200],
                    "original_status": None,
                    "corrected_status": None,
                    "reason": "criterion entry is not an object -- dropped",
                })
                continue

            status = c.get("status", "")
            if status not in allowed:
                label_remaps.append({
                    "nct_id": nct_id,
                    "arm": arm,
                    "criterion": str(c.get("criterion", ""))[:200],
                    "original_status": status,
                    "corrected_status": "not_evaluable",
                    "reason": f"status not in {arm} vocabulary",
                })
                c["status"] = "not_evaluable"

            cleaned.append(c)
        return cleaned

    for eval_result in evaluations:
        nct_id = eval_result.get("nct_id", "")

        # ── Step 0: the trial-level verdict ─────────────────────────────────
        #
        # THIS LINE USED TO READ `eligible = "not_eligible"` FOR ANYTHING
        # OUTSIDE THE VOCABULARY, and that is a rejection: a statement that
        # this trial assessed the patient and turned them down. The model never
        # said it. It is the one place in this file that resolved an
        # uninterpretable answer INTO a verdict; every other place resolves one
        # into "not evaluated" and says why -- Step 2 for a trial the model
        # returned no criteria for, Step 3's remap branch for a rejection whose
        # every disqualifier was out of vocabulary, and _normalize_arm one
        # level down for a criterion status.
        #
        # It was worse than a mislabel. The clobber ran BEFORE node_finalize,
        # whose own synonym map has always resolved boolean True, "Eligible"
        # and "yes" correctly -- so this line was destroying exactly the values
        # the pipeline's own downstream normalizer existed to rescue, and the
        # map could never be reached to disagree with it. Measured on the
        # shipped code: `True` -> not_eligible -> near_misses. See
        # normalize_trial_verdict, which both stages now call.
        #
        # The verdict is set to "not_evaluable" here and NOT recorded yet. The
        # recording waits for the branch chain below, because Step 3 can still
        # reach a supported verdict for this entry out of the criteria -- and
        # unevaluable_trials feeds a log line that says "these are not
        # rejections", so an entry that ends as one must not be in it.
        raw_verdict = eval_result.get("eligible")
        verdict, verdict_source = normalize_trial_verdict(raw_verdict)
        verdict_unrecognized = verdict_source == VERDICT_SOURCE_UNRECOGNIZED
        if verdict_source != VERDICT_SOURCE_CANONICAL:
            verdict_normalizations.append({
                "nct_id": nct_id,
                # repr, capped: the label is model output of unknown type and
                # unknown length, and "" and None must not read alike.
                "original_label": repr(raw_verdict)[:_MALFORMED_ENTRY_PREVIEW_LEN],
                # The TYPE is what the log line carries, because it diagnoses
                # the defect (a bool where a string was asked for, a null, a
                # nested object) and carries no clinical content. The label
                # TEXT stays in this list and out of the record: it is model
                # output of unbounded content and `original_label` is not on
                # LOGGABLE_FIELDS, so passing it would be dropped anyway.
                "original_type": type(raw_verdict).__name__,
                "resolved_to": verdict or TRIAL_VERDICT_NOT_EVALUABLE,
                "source": verdict_source,
            })
        eval_result["eligible"] = (
            verdict if verdict is not None else TRIAL_VERDICT_NOT_EVALUABLE
        )

        inc = eval_result.get("inclusion_criteria", [])
        exc = eval_result.get("exclusion_criteria", [])
        inc = inc if isinstance(inc, list) else []
        exc = exc if isinstance(exc, list) else []

        # ── Step 1: label normalization ─────────────────────────────────────
        # Unconditional. Runs before the verdict logic and on all three
        # trial-level branches, so no branch can store an out-of-vocabulary
        # criterion status.
        remaps_before = len(label_remaps)
        inc = _normalize_arm(inc, _INCLUSION_STATUSES, "inclusion", nct_id)
        exc = _normalize_arm(exc, _EXCLUSION_STATUSES, "exclusion", nct_id)
        eval_result["inclusion_criteria"] = inc
        eval_result["exclusion_criteria"] = exc
        remapped_here = len(label_remaps) > remaps_before

        total = len(inc) + len(exc)

        # ── Step 2: no criteria returned ────────────────────────────────────
        # A trial the model returned with no criteria at all was not evaluated.
        # That is NOT a rejection: recording it as "not_eligible" reports a
        # verdict the model never reached. It gets its own trial-level outcome
        # so non-evaluation is counted instead of masquerading as a rejection.
        if total == 0:
            # An unrecognised verdict is recorded under its OWN reason and with
            # the label the model actually wrote. Step 0 has already set the
            # value to not_evaluable, so the `!=` guard below would see nothing
            # to report and this trial's real defect would go unnamed.
            if verdict_unrecognized:
                unevaluable_trials.append({
                    "nct_id": nct_id,
                    "original_label": repr(raw_verdict)[:_MALFORMED_ENTRY_PREVIEW_LEN],
                    "reason": UNEVALUABLE_UNRECOGNIZED_VERDICT,
                })
            elif eval_result["eligible"] != TRIAL_VERDICT_NOT_EVALUABLE:
                unevaluable_trials.append({
                    "nct_id": nct_id,
                    "original_label": eval_result["eligible"],
                    "reason": "model returned no criteria",
                })
            eval_result["eligible"] = TRIAL_VERDICT_NOT_EVALUABLE
            _record_zero_score(eval_result, inc, exc)
            continue

        # ── Step 3: disqualification check, on normalized labels ────────────
        has_not_met = any(c.get("status") == "not_met" for c in inc)
        has_violated = any(c.get("status") == "violated" for c in exc)

        if has_not_met or has_violated:
            # UNCHANGED, AND IT OUTRANKS AN UNRECOGNISED LABEL ON PURPOSE. This
            # check reads the model's own criteria, which are the evidence; the
            # trial-level label is its summary of them. A summary that cannot be
            # read does not delete a criterion the model marked "not_met", and
            # recording such a trial as "not evaluated" would hide a stated
            # failure from a clinician and hand them a candidate the model had
            # already disqualified -- the same fabrication as before, pointing
            # the other way. So the rejection stands, on the criterion that
            # justifies it, and the unreadable label is reported in
            # verdict_normalizations rather than in unevaluable_trials, which
            # is a list of trials that are NOT rejections.
            eval_result["eligible"] = TRIAL_VERDICT_NOT_ELIGIBLE
            _record_zero_score(eval_result, inc, exc)

        elif eval_result["eligible"] == TRIAL_VERDICT_ELIGIBLE:
            # Legitimate eligible: recompute match_score over applicable criteria
            _record_score(eval_result, inc, exc, nct_id)

        elif eval_result["eligible"] == TRIAL_VERDICT_NOT_ELIGIBLE and remapped_here:
            # The model rejected this trial, but every disqualifying label it
            # wrote was out of vocabulary and Step 1 resolved them all away.
            # Keeping "not_eligible" would store a rejection with nothing left
            # to justify it; promoting to "eligible" would assert a match the
            # model never made. Neither verdict is supported, so the trial is
            # recorded as not evaluated.
            unevaluable_trials.append({
                "nct_id": nct_id,
                "original_label": TRIAL_VERDICT_NOT_ELIGIBLE,
                "reason": "sole disqualifier was an out-of-vocabulary label",
            })
            eval_result["eligible"] = TRIAL_VERDICT_NOT_EVALUABLE
            _record_zero_score(eval_result, inc, exc)

        else:
            # Model-declared "not_eligible" with no surviving disqualifier and
            # no remap, or model-declared "not_evaluable" with criteria present,
            # or an UNRECOGNISED label whose criteria disqualify nobody.
            #
            # The third is the fabricated rejection this branch used to receive
            # as a settled "not_eligible" from Step 0 and pass through untouched
            # under a comment reading "verdict left as the model wrote it" --
            # which was false of exactly that case, and only that case. It is
            # the one arm here that changed anything, and it is recorded, with
            # the label the model actually wrote.
            if verdict_unrecognized:
                unevaluable_trials.append({
                    "nct_id": nct_id,
                    "original_label": repr(raw_verdict)[:_MALFORMED_ENTRY_PREVIEW_LEN],
                    "reason": UNEVALUABLE_UNRECOGNIZED_VERDICT,
                })
            _record_zero_score(eval_result, inc, exc)

    if label_remaps:
        log.info("remapped out-of-vocabulary criterion labels to "
                 "not_evaluable", stage=5, event="label_remap",
                 count=len(label_remaps),
                 total=len({r["nct_id"] for r in label_remaps}))
    if unevaluable_trials:
        log.info("trials recorded as not_evaluable (these are not rejections)",
                 stage=5, event="not_evaluable",
                 not_evaluable=len(unevaluable_trials),
                 reason=sorted({t["reason"] for t in unevaluable_trials}))
    if verdict_normalizations:
        # Separate from the two above, and from unevaluable_trials in
        # particular: this says the LABEL was not written canonically, which is
        # true whatever verdict the entry finally reached. An entry here may
        # have ended eligible (a recovered "Eligible"), not_eligible (an
        # unreadable label over a criterion the model marked not_met) or
        # not_evaluable, and only the last is in that list.
        log.info("trial-level verdict labels were not written canonically",
                 stage=5, event="verdict_normalization",
                 count=len(verdict_normalizations),
                 total=len({v["nct_id"] for v in verdict_normalizations}),
                 reason=sorted({v["source"] for v in verdict_normalizations}),
                 error_type=",".join(sorted(
                     {v["original_type"] for v in verdict_normalizations})))


    # ── Absent-data validator: catch GPT-4o absent-data disqualifications ──
    #
    # GPT-4o sometimes classifies criteria as "not_met" or "violated" when
    # the patient record contains no relevant data (absent-data error).
    # This deterministic post-processor detects and corrects these errors
    # by checking the patient_value field of every disqualifying criterion.
    #
    # A criterion with patient_value indicating absent data and a
    # disqualifying status is a provable contradiction: you cannot
    # contradict a criterion without possessing the relevant data.
    #
    # After correction, the trial-level verdict is re-evaluated.
    # All corrections are logged for auditability.
 
    # Canonical phrases GPT-4o uses when patient data is absent.
    # Matched case-insensitively after stripping whitespace.
    _ABSENT_VALUE_EXACT = frozenset({
        "not in patient record",
        "not in the patient record",
        "not available in patient record",
        "not available in the patient record",
        "not documented",
        "not documented in patient record",
        "not documented in the patient record",
        "no data available",
        "no data",
        "unknown",
        "not available",
        "none documented",
        "none recorded",
        "no record",
        "no record available",
        "not reported",
        "not reported in patient record",
        "absent",
        "n/a",
    })
 
    # Prefix patterns: patient_value starts with these (case-insensitive).
    _ABSENT_VALUE_PREFIXES = (
        "not in patient",
        "not in the patient",
        "not documented",
        "not available",
        "no documented evidence",
        "no evidence",
        "no record of",
        "no data",
        "none on record",
    )
 
    def _is_absent_patient_value(pv: str) -> bool:
        """Return True if patient_value indicates absent/missing data."""
        normalized = pv.strip().lower()
        if not normalized:
            return True  # empty string = no data
        if normalized in _ABSENT_VALUE_EXACT:
            return True
        for prefix in _ABSENT_VALUE_PREFIXES:
            if normalized.startswith(prefix):
                return True
        return False
 
    absent_data_corrections = []  # audit log
 
    for eval_result in evaluations:
        if eval_result.get("eligible") != "not_eligible":
            continue
 
        inc = eval_result.get("inclusion_criteria", [])
        exc = eval_result.get("exclusion_criteria", [])
 
        # Skip trials with no criteria (should not happen with early
        # termination removed, but defensive).
        if not inc and not exc:
            continue
 
        corrected_any = False
 
        # Scan inclusion criteria
        for criterion in inc:
            status = criterion.get("status", "")
            pv = criterion.get("patient_value", "")
            if status == "not_met" and _is_absent_patient_value(pv):
                absent_data_corrections.append({
                    "nct_id": eval_result.get("nct_id", ""),
                    "criterion": criterion.get("criterion", "")[:200],
                    "original_status": status,
                    "patient_value": pv,
                    "corrected_status": "not_evaluable",
                    "reason": "patient_value indicates absent data",
                })
                criterion["status"] = "not_evaluable"
                corrected_any = True
 
        # Scan exclusion criteria
        for criterion in exc:
            status = criterion.get("status", "")
            pv = criterion.get("patient_value", "")
            if status == "violated" and _is_absent_patient_value(pv):
                absent_data_corrections.append({
                    "nct_id": eval_result.get("nct_id", ""),
                    "criterion": criterion.get("criterion", "")[:200],
                    "original_status": status,
                    "patient_value": pv,
                    "corrected_status": "not_evaluable",
                    "reason": "patient_value indicates absent data",
                })
                criterion["status"] = "not_evaluable"
                corrected_any = True
 
        # Re-evaluate trial-level verdict after corrections
        if corrected_any:
            remaining_not_met = any(
                c.get("status") == "not_met" for c in inc
            )
            remaining_violated = any(
                c.get("status") == "violated" for c in exc
            )
 
            if not remaining_not_met and not remaining_violated:
                # No remaining disqualifiers: flip to eligible
                eval_result["eligible"] = "eligible"
 
                # Recompute match_score over applicable criteria only, by the
                # same rule as the inline validator above.
                _record_score(eval_result, inc, exc, eval_result.get("nct_id", ""))
 
                # Update explanation prefix
                original_assessment = eval_result.get("assessment", "")
                if original_assessment.startswith("Known disqualifier:"):
                    eval_result["assessment"] = (
                        "No known disqualifiers. [Validator corrected absent-data disqualification.] "
                        + original_assessment
                    )
            # else: legitimate disqualifiers remain, trial stays not_eligible
 
    if absent_data_corrections:
        flipped_trials = sum(
            1 for e in evaluations
            if any(
                c["nct_id"] == e.get("nct_id") and c["corrected_status"] == "not_evaluable"
                for c in absent_data_corrections
            ) and e.get("eligible") == "eligible"
        )
        log.info("corrected absent-data criterion disqualifications", stage=5,
                 event="absent_data_correction",
                 count=len(absent_data_corrections),
                 total=len({c["nct_id"] for c in absent_data_corrections}),
                 eligible=flipped_trials)

    # ── Report not-applicable exclusions ────────────────────────────────────
    #
    # Counted off the evaluations themselves rather than off
    # `not_applicable_excluded`, because a trial rescored by the absent-data
    # validator passes through _record_score twice and would be double-counted
    # in that append-only audit list.
    _na_total = sum(e.get("criteria_not_applicable", 0) for e in evaluations)
    if _na_total:
        _na_trials = sum(1 for e in evaluations if e.get("criteria_not_applicable", 0))
        _na_empty = sum(
            1 for e in evaluations
            if e.get("criteria_not_applicable", 0) and e.get("score_denominator", 0) == 0
        )
        log.info("excluded not-applicable criteria from match_score", stage=5,
                 event="not_applicable_excluded",
                 criteria_not_applicable=_na_total, total=_na_trials,
                 empty_denominator_trials=_na_empty)

    # ── Reconciliation: every trial that entered Stage 5 must be accounted for ──
    #
    # Three ways a trial can leave this stage without a verdict, and until now
    # none of them was visible: a truncation floor, an exhausted split budget,
    # or the model simply not mentioning it in an otherwise valid response.
    # The first two are collected above; the third is only detectable here, by
    # comparing what came back against what was sent.
    #
    # The invariant this enforces is countable, not vague: after this block,
    # every nct_id in filtered_trials appears exactly once in evaluations.
    evaluations.extend(unevaluable)

    _evaluated_ids = {e.get("nct_id") for e in evaluations}
    _omitted = [t for t in trials if t["trial"]["nct_id"] not in _evaluated_ids]
    if _omitted:
        log.warning("trials sent to the model came back with no entry; "
                    "recording them as not evaluable", stage=5,
                    event="reconciliation", count=len(_omitted),
                    nct_ids=[t["trial"]["nct_id"] for t in _omitted])
        evaluations.extend(
            _unevaluable_entry(t, NOT_EVALUABLE_MODEL_OMITTED) for t in _omitted
        )

    not_evaluable_truncated = sum(
        1 for e in evaluations
        if e.get("not_evaluable_reason") in (NOT_EVALUABLE_TRUNCATION_FLOOR,
                                             NOT_EVALUABLE_SPLIT_BUDGET)
    )

    # Sort by match score descending
    evaluations.sort(
         key=lambda x: (x.get("match_score", 0), x.get("nct_id", "")),
         reverse=True
     )

    elapsed = time.time() - start
    log.info("Stage 5 evaluation complete", stage=5,
             duration_s=round(elapsed, 3), evaluated=len(evaluations),
             # The scope limitation, as a field rather than a sentence: Stage
             # 5's system prompt only asserts that disease relevance was
             # confirmed when the MeSH filter actually ran, and a run where it
             # did not is a different claim about the same output.
             mesh_filter_applied=_mesh_filter_applied,
             skip_reason=_mesh_filter_reason)

    return {
        "evaluations": evaluations,
        "llm_classifier_retries": retry_count,
        # Two budgets, two counters. llm_classifier_retries counts whole-node retries
        # for malformed or failed responses; this counts levels of halving
        # spent because a response was cut off. Sharing one would have failed a
        # patient that hit a single parse error and then needed two splits.
        "llm_classifier_truncation_splits": truncation_splits,
        "llm_classifier_output_tokens_estimated": estimated_output,
        "not_evaluable_truncated": not_evaluable_truncated,
        "llm_classifier_calls": calls_made,
        "llm_classifier_raw_response": response_text,
        "llm_classifier_prompt": prompt,
        "llm_classifier_input_tokens": input_tokens,
        "llm_classifier_output_tokens": output_tokens,
        # The reasoning share of llm_classifier_output_tokens, NOT an extra charge on top
        # of it. None when no response carried the breakdown -- see the
        # accumulator above.
        "llm_classifier_reasoning_tokens": (reasoning_tokens if reasoning_tokens_reported
                                   else None),
        # The model that actually answered. File 14 logs this into
        # inferences.matching_model and prices against it, so the stored cost is
        # computed from the model that produced the tokens rather than from
        # whatever MATCHING_MODEL happens to say at read time.
        "matching_model": model_answered,
        "cross_vocab_remaps": len(label_remaps),
        "error": "",  # Clear error on success
        # What was intended, and what was sent. PROMPT_VERSION is a module
        # constant rather than a state read because the render happened in
        # this process, in this call; the hash is of that exact render.
        # Both reach inferences via _pipeline_provenance() (terminal.py),
        # which is what puts them on the no-candidate and error paths too.
        "llm_classifier_prompt_version": PROMPT_VERSION,
        "llm_classifier_prompt_sha256": system_prompt_sha256,
        "stage_timings": {**state.get("stage_timings", {}), "llm_classifier_evaluation": round(prior_llm_classifier_time + elapsed, 3)}
    }


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""
