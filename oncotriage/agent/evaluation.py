"""Stage 5: the criterion-level eligibility judgement.

Item 20c, pass 2c: "13- LangGraph Agent.py" lines 2319-3809.

The largest single stage, and the only one that spends money. It carries three
budgets that must not be confused with each other, and File 03's reconciliation
is the argument for keeping them separate:

    MAX_GPT4O_RETRIES      the response came back and was unusable
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
from typing import Dict, List, Optional, Tuple

from oncotriage import config
from oncotriage.agent import deps
from oncotriage.agent.patient import _create_patient_summary
from oncotriage.agent.state import TrialMatchState
from oncotriage.config import (
    CHARS_PER_TOKEN,
    MATCHING_MAX_TOKENS,
    MATCHING_MODEL,
    MATCHING_OUTPUT_SPLIT_FRACTION,
    MATCHING_OUTPUT_TOKENS_PER_TRIAL,
    MATCHING_REASONING_EFFORT,
    MATCHING_SEED,
    MAX_GPT4O_RETRIES,
    MAX_TRUNCATION_SPLITS,
    RETRY_BASE_DELAY,
)
from oncotriage.utils import get_age_reference_date


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
            f"'03- Config.py' to {returned!r}, add it to PRICING_CONFIG if it "
            f"is not there, and re-baseline; do not accept it silently."
        )
# MATCHING_MAX_TOKENS and MATCHING_SEED are in 03- Config.py, together with
# the truncation thresholds calibrated against the first of them.


def call_matching_model(system_prompt: str, user_prompt: str):
    """Issue the Stage 5 evaluation request and return the raw API response.

    Lifted out of node_gpt4o_evaluation unchanged. It is the single point where
    the pipeline talks to the matching model, which is what lets a recording
    harness capture the request and response verbatim and a replay harness
    serve them back without a network call (45-/46- Fixture Capture/Replay).

    The caller owns error handling: this raises whatever the client raises, and
    node_gpt4o_evaluation's except block turns that into a retry.

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
                              deliberately distinct from MAX_GPT4O_RETRIES,
                              which covers a response that arrived and would
                              not parse. File 03 states both, plus the
                              truncation-split budget, and the worst-case wall
                              time the three produce together.
      temperature             NOT SENT. Rejected for every value but the
                              provider default of 1, so there is nothing to
                              send. MATCHING_TEMPERATURE is None.
      response_format         NOT SENT, deliberately. The model does support
                              it -- a json_object probe failed only on the
                              unrelated "messages must contain the word json"
                              rule, not on the parameter -- and Stage 5 would
                              be a good candidate for Structured Outputs. It is
                              held back so the model migration can be measured
                              on its own; adding it here would change the
                              parsing contract and the verdict distribution in
                              the same commit.

    Anything added to this call must also be added to the request block File 45
    records and File 46 replays, or a fixture stops being able to see it.
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


def estimate_output_tokens(trials: List[Dict]) -> int:
    """Estimate the evaluation response size for a batch, before sending it.

    HOW THIS WAS CALIBRATED, so it can be re-derived when the model changes:

        SELECT candidates_evaluated, gpt4o_output_tokens, gpt4o_calls,
               gpt4o_reasoning_tokens, matching_model
        FROM inferences
        WHERE candidates_evaluated > 0 AND gpt4o_output_tokens > 0
          AND gpt4o_calls = 1            -- see below
        GROUP BY matching_model

    RESTRICT TO gpt4o_calls = 1 AND GROUP BY matching_model. Both matter now.
    A split run sums its tokens across chunks and, when the split was reactive,
    includes the wasted truncated call, so output/trials over those rows
    over-states the per-trial cost. And inferences.db holds rows from two
    judges since 2026-08-04; pooling them calibrates against neither.

    gpt4o_output_tokens ALREADY INCLUDES reasoning tokens (they are a subset of
    usage.completion_tokens, not an addition to it), so no term is added for
    them. gpt4o_reasoning_tokens is selected above only to see how much of the
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
    proxy, taken from the gpt4o_prompt column — gives

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
        "explanation": {
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


def node_gpt4o_evaluation(state: TrialMatchState) -> dict:
    """
    Stage 5: GPT-4o criterion-level evaluation.

    Sends ALL filtered trials to GPT-4o in a SINGLE call.
    GPT-4o evaluates every inclusion/exclusion criterion for each trial
    and returns structured JSON with match scores and explanations.

    On JSON parse failure or API error, sets error flag so the retry
    router (conditional edge) can loop back for another attempt.
    Up to MAX_GPT4O_RETRIES attempts with exponential backoff.

    Temperature = 0 for deterministic, reproducible medical decisions.
    """
    
    start = time.time()

    patient_data = state["patient_data"]
    trials = state["filtered_trials"]
    retry_count = state.get("gpt4o_retries", 0)
    
    # Accumulate timing across retries (previous attempts' time is already in stage_timings)
    prior_gpt4o_time = state.get("stage_timings", {}).get("gpt4o_evaluation", 0.0)

    # Exponential backoff on retries (skip delay on first attempt)
    if retry_count > 0:
        delay = RETRY_BASE_DELAY * (2 ** (retry_count - 1))
        print(f"  [Retry {retry_count}/{MAX_GPT4O_RETRIES}] Waiting {delay}s before retry...")
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

    if _mesh_filter_applied:
        scope_limitation = """Disease relevance has already been confirmed. An upstream filter compared this patient's cancer site against every trial below. Every trial you receive is disease-relevant.

Your ONLY job is to evaluate the eligibility criteria text (inclusion and exclusion) against the patient record. Do not assess disease relevance. Do not disqualify a trial for any reason other than a criterion-level "not_met" or "violated" classification."""
    else:
        scope_limitation = f"""Disease relevance has NOT been confirmed for this patient. The upstream cancer site filter did not run ({_mesh_filter_reason}), so the trials below were selected by text retrieval and re-ranking alone. They come from an oncology-only corpus, so each one is a cancer trial, but none has been checked against this patient's cancer site.

Your job is to evaluate the eligibility criteria text (inclusion and exclusion) against the patient record. Where a criterion names a disease categorically different from the patient's documented cancer, classify it under RULE 3 in the normal way (inclusion -> "not_met", exclusion -> "not_violated"). That is the only form in which disease relevance may enter your output: judge criteria, never the trial as a whole, and reason only from the criteria text you were given. Do not disqualify a trial for any reason other than a criterion-level "not_met" or "violated" classification."""


# The prompt engineering for the system prompt was:
#	•	A rule-based medical reasoning scaffold
#	•	With hallucination containment
#	•	With termination control to lower cost and increase speed
#	•	With temporal logic
#	•	With subtype hierarchy rules
# Closer to a deterministic symbolic overlay on GPT-4o.


# ================================================================
# SYSTEM MESSAGE
# ================================================================

    # RULE 4's "Reference date" is the data snapshot date, not date.today().
    # It is the same anchor the patient's age was computed against (File 07),
    # so the prompt's temporal reasoning and its stated age agree, and neither
    # moves between two runs of the same patient. Under date.today() every
    # washout window ("no platinum within 6 months") silently widened as the
    # clock advanced, while patient_data_hash stayed identical.
    system_prompt = f"""
You are a clinical trial pre-screening classifier.

Your job is NOT to determine full eligibility.
Your job is ONLY to detect whether a patient is CATEGORICALLY disqualified based on explicit, documented evidence in the patient record.

If a categorical disqualifier cannot be proven using explicit patient data, the trial remains "eligible".

=====================================================================
GLOBAL INVARIANT -- MISSING DATA (HIGHEST PRIORITY RULE)
=====================================================================

ABSENT PATIENT DATA IS NEVER A DISQUALIFIER.

If the patient record does NOT explicitly contain a data point addressing a clinical concept referenced in a trial criterion, the classification for that criterion MUST be:

    "not_evaluable"

This rule has ZERO exceptions.

Absence of data is NOT evidence of absence.

Do NOT assume:
- normal lab values
- absence of diseases
- absence of medications
- absence of biomarkers or molecular markers
- absence of treatments or procedures
- absence of symptoms or progression
- treatment outcomes from treatment status

If the patient record does not explicitly state the information, the information is UNKNOWN. UNKNOWN information ALWAYS produces:

    criterion status = "not_evaluable"

=====================================================================
DISQUALIFICATION PROOF REQUIREMENT
=====================================================================

Before classifying ANY criterion as "not_met" or "violated", you MUST answer:

"Can I quote a specific, explicit patient data point that directly and unambiguously contradicts this criterion?"

YES -> you may classify as "not_met" (inclusion) / "violated" (exclusion)
NO  -> the classification MUST be "not_evaluable"

This rule overrides clinical intuition and statistical likelihood. If you cannot quote the disqualifying evidence, disqualification is forbidden.

=====================================================================
SECTION 1 -- CLASSIFICATION STATUSES
=====================================================================

INCLUSION CRITERIA use exactly one status:

"met"             Explicit patient data directly satisfies the requirement.
"not_met"         Explicit patient data directly contradicts the requirement. Requires quotable evidence.
"not_evaluable"   The patient record does not contain sufficient information. Never disqualifying.

EXCLUSION CRITERIA use exactly one status:

"not_violated"    Explicit patient data confirms the patient does NOT have the excluded condition, including resolved/inactive/completed conditions.
"violated"        Explicit patient data confirms the patient HAS the excluded condition. Requires quotable evidence.
"not_evaluable"   The patient record does not contain sufficient information. Never disqualifying.

THE TWO VOCABULARIES ARE DISJOINT AND NON-INTERCHANGEABLE.

An inclusion criterion may ONLY be "met", "not_met", or "not_evaluable". It may NEVER be "violated" or "not_violated".
An exclusion criterion may ONLY be "not_violated", "violated", or "not_evaluable". It may NEVER be "met" or "not_met".

A status drawn from the wrong vocabulary is not a stronger or weaker form of the correct one. It carries no meaning and will be discarded as "not_evaluable". If you are tempted to write "violated" on an inclusion criterion, the criterion you mean is "not_met"; write that instead.

TRIAL-LEVEL CLASSIFICATION:

"eligible"        No disqualifying evidence was found.
"not_eligible"    At least one inclusion criterion is "not_met" OR at least one exclusion criterion is "violated".
"not_evaluable"   The trial's eligibility criteria text is empty, contains no parseable criteria, or is otherwise impossible to evaluate. Return empty inclusion_criteria and exclusion_criteria arrays. THIS IS NOT A REJECTION -- it records that the trial could not be assessed, which is different from assessing it and finding a disqualifier.

Empty inclusion_criteria and exclusion_criteria arrays are permitted ONLY with "not_evaluable". An "eligible" or "not_eligible" trial MUST list every criterion it evaluated. Never return empty arrays to signal a rejection.

NOT APPLICABLE CRITERIA:
A criterion is "Not applicable" ONLY when its subject matter is biologically or logically impossible for this patient — the criterion cannot ever apply regardless of any test, treatment, or future event. Examples: reproductive criteria for the opposite sex, pediatric criteria for adults, menopausal criteria for males.
- Exclusion: status = "not_violated", patient_value = "Not applicable -- [reason]"
- Inclusion: status = "met", patient_value = "Not applicable -- [reason]"
If no patient data exists to evaluate the criterion, that is "not_evaluable".
If patient data EXISTS and CONTRADICTS a criterion, that is "not_met" (inclusion) or "violated" (exclusion) with the actual patient data as patient_value — never "Not applicable".

=====================================================================
SECTION 2 -- SCOPE LIMITATION
=====================================================================

{scope_limitation}

=====================================================================
SECTION 3 -- CRITERION EVALUATION ORDER
=====================================================================

Evaluate each trial's criteria one at a time, in order received, in complete isolation from other trials. Reset reasoning completely before each new trial.

RULE 1 -- DATA AVAILABILITY (MANDATORY FIRST STEP, GATES ALL OTHER RULES)

Search the patient record for data addressing the same clinical concept as this criterion.

If the criterion contains AND-joined components (requires multiple conditions simultaneously):
    Check each component independently.
    If ANY component has no data in the patient record:
        classification = "not_evaluable" for the entire criterion.
        Stop. Do not evaluate the components that are documented.

If the criterion is a single requirement:
    If no relevant data exists in the patient record:
        classification = "not_evaluable"
        Stop. Do not proceed to any other rule.

A documented diagnosis satisfies any "histologically confirmed" or "cytologically confirmed" or "pathologically confirmed" qualifier attached to it. A diagnosis cannot exist without some form of clinical confirmation. Do not classify as "not_met" because the confirmation method is not separately documented.

This rule gates all subsequent rules. If Rule 1 produces "not_evaluable", no other rule may override it.

RULE 2 -- MEDICATION INTERPRETATION

If relevant data is a MEDICATION, check its status:

ACTIVE / ON-HOLD / no status documented:
    Treat as current therapy.

COMPLETED / STOPPED / CANCELLED:
    Treat as historical therapy. Use end date for temporal reasoning.

Completion of therapy does NOT indicate:
- treatment failure
- disease progression
- intolerance
- response

If a criterion requires a specific treatment outcome and the patient record documents only the treatment without the outcome:
    classification = "not_evaluable"

RULE 3 -- CLINICAL TERMINOLOGY MATCHING

When the patient record and criterion use different terminology:

Synonyms or child-to-parent match:
    Acceptable. Proceed.

Parent-to-child match:
    Not sufficient. classification = "not_evaluable"

Sibling conditions:
    Treat as different. classification = "not_evaluable"

Categorically different diseases:
    inclusion -> "not_met"
    exclusion -> "not_violated"

RULE 4 -- TEMPORAL REASONING

Reference date: {get_age_reference_date().isoformat()}

If the criterion contains a time window:
    If event end date is known: calculate elapsed time.
    If event end date is unknown: classification = "not_evaluable"

If the criterion uses past-tense wording ("history of", "prior", "previous"):
    Any documented occurrence (past or present) satisfies the criterion.
    Affirming ("history of X"): if documented -> "met"/"violated". If not -> "not_evaluable".
    Negating ("no prior X"): if documented -> "not_met"/"not_violated". If not -> "not_evaluable".

If the criterion requires an active/current condition:
    Resolved/inactive/in remission: inclusion -> "not_evaluable"; exclusion -> "not_violated".
    No resolution documented: inclusion -> "met"; exclusion -> "not_evaluable".
    Explicitly active/recurrence: inclusion -> "met"; exclusion -> "violated".

RULE 5 -- DIRECT CONTRADICTION CHECK

A contradiction requires ALL three conditions:
(a) Same clinical attribute, same temporal context.
(b) Clinically incompatible values (not merely different terminology or specificity).
(c) Unambiguous -- no reasonable interpretation resolves the conflict.

If all three: "not_met" (inclusion) or "violated" (exclusion).
If ANY uncertainty: classification = "not_evaluable"

RULE 6 -- OR-JOINED CRITERIA

If a criterion contains OR-connected branches:
    If ANY branch is satisfied: "met" / "violated"
    If ALL branches are explicitly contradicted: "not_met" / "not_violated"
    If ANY branch is not_evaluable: classification = "not_evaluable"

RULE 7 -- DEFAULT

If no rule produced a classification:
    classification = "not_evaluable"

=====================================================================
SECTION 4 -- BIOMARKERS AND MOLECULAR DATA
=====================================================================

Missing biomarker or molecular testing is NEVER disqualifying.

This includes but is not limited to: EGFR, PD-L1, HER2, KRAS, BRAF, ALK, ROS1, MSI-H, dMMR, BRCA, PIK3CA, DLL3, CALR, tumor mutational burden, and any other genomic or molecular assay.

If the patient record does not contain the biomarker result:
    classification = "not_evaluable"

=====================================================================
SECTION 5 -- OUTPUT FORMAT
=====================================================================

Return ONLY a valid JSON array. No markdown fences. No text outside the array.
Evaluate ALL {len(trials)} trials in one JSON array.

Fields MUST appear in this exact order:
trial_number, nct_id, match_score, inclusion_criteria, exclusion_criteria, explanation, eligible

match_score: always 0.0

inclusion_criteria and exclusion_criteria:
    For ALL trials (both "eligible" and "not_eligible"): list ALL evaluated criteria with criterion, patient_value, status.
    For "not_evaluable" trials only: both arrays are empty.
    Every status MUST come from that criterion's own vocabulary (Section 1).

patient_value: exact data point/s from patient record, OR "Not in patient record", OR "Not applicable -- [reason]". No interpretive statements.

explanation MUST be written BEFORE eligible and determines it:
    For "eligible" trials: begin with "No known disqualifiers."
    For "not_eligible" trials: begin with "Known disqualifier:" then quote the specific patient data.
    For "not_evaluable" trials: begin with "Not evaluable:" then state what was missing from the trial's criteria text.

JSON template:
[
  {{
    "trial_number": 1,
    "nct_id": "NCT12345678",
    "match_score": 0.0,
    "inclusion_criteria": [
      {{"criterion": "Age 18-75", "patient_value": "62", "status": "met"}},
      {{"criterion": "ECOG 0-1", "patient_value": "Not in patient record", "status": "not_evaluable"}}
    ],
    "exclusion_criteria": [
      {{"criterion": "Active autoimmune disease", "patient_value": "Not in patient record", "status": "not_evaluable"}}
    ],
    "explanation": "No known disqualifiers. Age confirmed. ECOG and autoimmune status not documented.",
    "eligible": "eligible"
  }},
  {{
    "trial_number": 2,
    "nct_id": "NCT87654321",
    "match_score": 0.0,
    "inclusion_criteria": [
      {{"criterion": "Adequate renal function (creatinine ≤ 1.5 x ULN)", "patient_value": "Creatinine: 3.4 mg/dL", "status": "not_met"}},
      {{"criterion": "ECOG 0-1", "patient_value": "Not in patient record", "status": "not_evaluable"}}
    ],
    "exclusion_criteria": [
      {{"criterion": "Active hepatitis B", "patient_value": "Not in patient record", "status": "not_evaluable"}}
    ],
    "explanation": "Known disqualifier: Creatinine 3.4 mg/dL contradicts inclusion criterion requiring creatinine ≤ 1.5 x ULN.",
    "eligible": "not_eligible"
  }}
]

=====================================================================
SECTION 6 -- ABSOLUTE CONSTRAINTS
=====================================================================

C1 -- NO FABRICATION: The patient record is the ONLY source of patient information.

C2 -- NO TRIAL INFERENCE: Evaluate only what is written in the trial criteria. Do not apply standard oncology requirements unless explicitly stated in the criteria.

C3 -- EXCLUSION CONSERVATISM: "violated" requires explicit positive evidence the patient HAS the excluded condition.

C4 -- TRIAL ISOLATION: Each trial evaluated independently. Never carry reasoning across trials.

C5 -- CONSERVATISM UNDER UNCERTAINTY: Uncertainty ALWAYS resolves to "not_evaluable". Never resolve uncertainty toward disqualification.

=====================================================================
FINAL REMINDER
=====================================================================

A trial can ONLY be classified "not_eligible" if you can quote explicit patient evidence that contradicts a trial criterion. If the patient record does not contain that evidence, the criterion status MUST be "not_evaluable".
"""


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
    # recorded in gpt4o_truncation_splits, not by mutating this.
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
        print(f"  [Pre-split] estimate {estimated_output} tokens > threshold "
              f"{split_threshold} — sending {len(initial_chunks)} chunk(s) "
              f"instead of 1")
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
            print(f"  ERROR: {error_msg}")
            return {
                "evaluations": [],
                "gpt4o_retries": retry_count + 1,
                "gpt4o_truncation_splits": truncation_splits,
                "gpt4o_output_tokens_estimated": estimated_output,
                "gpt4o_raw_response": "",
                "error": error_msg,
                "stage_timings": {**state.get("stage_timings", {}), "gpt4o_evaluation": round(prior_gpt4o_time + elapsed, 3)}
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
            print("  WARNING: the response object carries no finish_reason; "
                  "truncation cannot be detected on this run. Falling back to "
                  "JSON-parse failure as the only signal.")
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
                print(f"  TRUNCATION FLOOR: {chunk[0]['trial']['nct_id']} alone "
                      f"exceeds the output ceiling. Recording as not evaluable.")
                unevaluable.append(
                    _unevaluable_entry(chunk[0], NOT_EVALUABLE_TRUNCATION_FLOOR)
                )
                continue

            if depth >= MAX_TRUNCATION_SPLITS:
                print(f"  TRUNCATION: split budget exhausted at depth {depth}; "
                      f"recording {len(chunk)} trial(s) as not evaluable.")
                unevaluable.extend(
                    _unevaluable_entry(t, NOT_EVALUABLE_SPLIT_BUDGET)
                    for t in chunk
                )
                continue

            left, right = _split_in_half(chunk)
            truncation_splits += 1
            print(f"  TRUNCATION at depth {depth}: {len(chunk)} trial(s) -> "
                  f"{len(left)} + {len(right)}, retrying as two calls "
                  f"(split {truncation_splits})")
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
            print(f"  ERROR: {error_msg}")
            print(f"  Response preview: {chunk_text[:300]}")
            return {
                "evaluations": [],
                "gpt4o_retries": retry_count + 1,
                "gpt4o_truncation_splits": truncation_splits,
                "gpt4o_output_tokens_estimated": estimated_output,
                "gpt4o_raw_response": chunk_text,
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
                "stage_timings": {**state.get("stage_timings", {}), "gpt4o_evaluation": round(prior_gpt4o_time + elapsed, 3)}
            }

        if not isinstance(parsed, list):
            elapsed = time.time() - start
            error_msg = f"GPT-4o returned non-list JSON (type={type(parsed).__name__})"
            print(f"  ERROR: {error_msg}")
            print(f"  Response preview: {chunk_text[:300]}")
            return {
                "evaluations": [],
                "gpt4o_retries": retry_count + 1,
                "gpt4o_truncation_splits": truncation_splits,
                "gpt4o_output_tokens_estimated": estimated_output,
                "gpt4o_raw_response": chunk_text,
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
                "stage_timings": {**state.get("stage_timings", {}), "gpt4o_evaluation": round(prior_gpt4o_time + elapsed, 3)}
            }

        evaluations.extend(parsed)

    if truncations_observed:
        print(f"  [Truncation] {truncations_observed} response(s) hit the "
              f"{MATCHING_MAX_TOKENS}-token ceiling; {truncation_splits} split(s) "
              f"performed across {calls_made} call(s)")

    # ── Estimate against actual, so the calibration can be tightened ───────
    # Logged every run, not only when it matters. The constants in File 03 were
    # derived from this column pair on 1,094 historical rows; recording the
    # estimate beside the outcome is what lets the next derivation be better.
    # The reasoning share is printed beside the total rather than added to it,
    # so the line reads the way the billing does: one output figure, with the
    # invisible part of it named.
    _reasoning_note = (
        f", of which {reasoning_tokens} reasoning"
        if reasoning_tokens_reported else
        ", reasoning share not reported"
    )
    print(f"  [Output tokens] estimated {estimated_output}, actual "
          f"{output_tokens}{_reasoning_note} across {calls_made} call(s) "
          f"(ratio {output_tokens / estimated_output:.2f})"
          if estimated_output else
          f"  [Output tokens] actual {output_tokens}{_reasoning_note}")

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

    _TRIAL_LEVEL_LABELS = ("eligible", "not_eligible", "not_evaluable")

    label_remaps = []       # audit log: criterion labels outside their vocabulary
    unevaluable_trials = []  # audit log: trials that could not be evaluated

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
            print(
                f"  [Validator] {nct_id or '(no NCT ID)'}: all "
                f"{n_na} criterion(s) inapplicable to this patient -- "
                f"match_score 0.0 over an empty denominator."
            )
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

        # Normalize unexpected trial-level labels
        if eval_result.get("eligible") not in _TRIAL_LEVEL_LABELS:
            eval_result["eligible"] = "not_eligible"

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
            if eval_result["eligible"] != "not_evaluable":
                unevaluable_trials.append({
                    "nct_id": nct_id,
                    "original_label": eval_result["eligible"],
                    "reason": "model returned no criteria",
                })
            eval_result["eligible"] = "not_evaluable"
            _record_zero_score(eval_result, inc, exc)
            continue

        # ── Step 3: disqualification check, on normalized labels ────────────
        has_not_met = any(c.get("status") == "not_met" for c in inc)
        has_violated = any(c.get("status") == "violated" for c in exc)

        if has_not_met or has_violated:
            eval_result["eligible"] = "not_eligible"
            _record_zero_score(eval_result, inc, exc)

        elif eval_result["eligible"] == "eligible":
            # Legitimate eligible: recompute match_score over applicable criteria
            _record_score(eval_result, inc, exc, nct_id)

        elif eval_result["eligible"] == "not_eligible" and remapped_here:
            # The model rejected this trial, but every disqualifying label it
            # wrote was out of vocabulary and Step 1 resolved them all away.
            # Keeping "not_eligible" would store a rejection with nothing left
            # to justify it; promoting to "eligible" would assert a match the
            # model never made. Neither verdict is supported, so the trial is
            # recorded as not evaluated.
            unevaluable_trials.append({
                "nct_id": nct_id,
                "original_label": "not_eligible",
                "reason": "sole disqualifier was an out-of-vocabulary label",
            })
            eval_result["eligible"] = "not_evaluable"
            _record_zero_score(eval_result, inc, exc)

        else:
            # Model-declared "not_eligible" with no surviving disqualifier and
            # no remap, or model-declared "not_evaluable" with criteria present.
            # Verdict left as the model wrote it.
            _record_zero_score(eval_result, inc, exc)

    if label_remaps:
        print(
            f"  [Validator] Remapped {len(label_remaps)} out-of-vocabulary criterion "
            f"label(s) to not_evaluable across "
            f"{len(set(r['nct_id'] for r in label_remaps))} trial(s)."
        )
    if unevaluable_trials:
        print(
            f"  [Validator] {len(unevaluable_trials)} trial(s) recorded as "
            f"not_evaluable (not rejections): "
            f"{', '.join(sorted(set(t['reason'] for t in unevaluable_trials)))}."
        )


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
                original_explanation = eval_result.get("explanation", "")
                if original_explanation.startswith("Known disqualifier:"):
                    eval_result["explanation"] = (
                        "No known disqualifiers. [Validator corrected absent-data disqualification.] "
                        + original_explanation
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
        print(
            f"  [Validator] Corrected {len(absent_data_corrections)} absent-data "
            f"criterion(s) across {len(set(c['nct_id'] for c in absent_data_corrections))} "
            f"trial(s). Flipped {flipped_trials} trial(s) to eligible."
        )

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
        print(
            f"  [Validator] Excluded {_na_total} not-applicable criterion(s) from "
            f"match_score across {_na_trials} trial(s)"
            + (f"; {_na_empty} trial(s) had no applicable criterion left."
               if _na_empty else ".")
        )

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
        print(f"  [Reconciliation] {len(_omitted)} trial(s) sent to the model "
              f"came back with no entry; recording as not evaluable: "
              f"{[t['trial']['nct_id'] for t in _omitted]}")
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
    print(f"[Stage 5] GPT-4o evaluation: {elapsed:.2f}s | {len(evaluations)} trials evaluated")
    print(f"  Scope limitation: relevance "
          f"{'confirmed upstream' if _mesh_filter_applied else 'NOT confirmed'} "
          f"[{_mesh_filter_reason}]")

    return {
        "evaluations": evaluations,
        "gpt4o_retries": retry_count,
        # Two budgets, two counters. gpt4o_retries counts whole-node retries
        # for malformed or failed responses; this counts levels of halving
        # spent because a response was cut off. Sharing one would have failed a
        # patient that hit a single parse error and then needed two splits.
        "gpt4o_truncation_splits": truncation_splits,
        "gpt4o_output_tokens_estimated": estimated_output,
        "not_evaluable_truncated": not_evaluable_truncated,
        "gpt4o_calls": calls_made,
        "gpt4o_raw_response": response_text,
        "gpt4o_prompt": prompt,
        "gpt4o_input_tokens": input_tokens,
        "gpt4o_output_tokens": output_tokens,
        # The reasoning share of gpt4o_output_tokens, NOT an extra charge on top
        # of it. None when no response carried the breakdown -- see the
        # accumulator above.
        "gpt4o_reasoning_tokens": (reasoning_tokens if reasoning_tokens_reported
                                   else None),
        # The model that actually answered. File 14 logs this into
        # inferences.matching_model and prices against it, so the stored cost is
        # computed from the model that produced the tokens rather than from
        # whatever MATCHING_MODEL happens to say at read time.
        "matching_model": model_answered,
        "cross_vocab_remaps": len(label_remaps),
        "error": "",  # Clear error on success
        "stage_timings": {**state.get("stage_timings", {}), "gpt4o_evaluation": round(prior_gpt4o_time + elapsed, 3)}
    }


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""
