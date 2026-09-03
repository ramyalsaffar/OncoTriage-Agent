######################################################################
# Stage 5 on Amazon Bedrock: the Responses API translation
######################################################################

"""Translate the Stage 5 request onto Bedrock's OpenAI-compatible Responses API.

THE FLAG IS OFF AND NOTHING HERE RUNS. ``config.MATCHING_PROVIDER`` is
``"openai"``; ``oncotriage/agent/evaluation.py:call_matching_model`` dispatches
on it and every statement in this module is unreachable under the default. No
Bedrock client is constructed, no credential is resolved, and the request the
OpenAI client is handed is byte-for-byte the one it was handed before this file
existed. The twelve characterization fixtures replay clean without recapture,
which is the behavioural half of that claim;
``tests/test_agent_bedrock_adapter.py`` section 1 is the structural half.

WHY THE RESPONSES API IS THE PRIMARY FORM, AND NOT CHAT COMPLETIONS
-------------------------------------------------------------------
Stage 5 speaks Chat Completions today, and Bedrock serves Chat Completions on
both endpoints, so a literal translation would have been no translation at all.
It is still the wrong target, for two measured reasons:

  1. **Prompt caching is Responses-only for this model.** The GPT-5.6 Terra
     model card lists, under features supported on ``bedrock-runtime``,
     "Prompt caching (Responses API only)", and the prompt-caching page's
     OpenAI section opens "OpenAI models on Amazon Bedrock support prompt
     caching through the Responses API". Stage 5's whole packing design rests
     on N requests sharing one prefix -- ``llm_classifier_packing`` records
     ``prefix_sha256`` for exactly that claim -- so the API that cannot cache
     that prefix is the API that throws away a 90% input discount.
  2. **It is the shape both endpoints agree on.** Which of the two endpoints
     this project's quota lands on is not yet known, so the endpoint is
     configuration (``config.BEDROCK_ENDPOINT``) and the request form has to be
     one that survives either choice. Responses is served on both.

THE MAPPING, FIELD BY FIELD, WITH ITS CITATION
-----------------------------------------------
Left is what ``evaluation.call_matching_model`` sends today. Right is what this
module sends. Sources read 2026-08-21 and named by page.

  model=MATCHING_MODEL
      -> model=config.BEDROCK_MATCHING_MODEL   (config.matching_wire_model())
      The wire id is NOT the priced/configured name. On ``bedrock-runtime`` the
      GPT-5.6 models are reachable only through a cross-Region inference
      profile: "Name a cross-Region inference profile as the model, not a
      foundation model ID" (bedrock-mantle.html, "Model IDs"), and the Terra
      model card's Programmatic Access table reads "Not supported" in that
      endpoint's In-Region column. On ``bedrock-mantle`` it is the bare
      ``openai.gpt-5.6-terra``. config.validate_matching_provider_config()
      refuses the wrong combination locally, naming the constant to edit.

  messages=[{"role":"system",...},{"role":"user",...}]
      -> input=[{"type":"message","role":BEDROCK_SYSTEM_ROLE,
                 "content":[{"type":"input_text","text":system_prompt}]},
                {"type":"message","role":"user",
                 "content":[{"type":"input_text","text":user_prompt}]}]
      THE SYSTEM MESSAGE BECOMES AN INPUT ITEM, NOT ``instructions=``. Both
      express "a system message inserted into the model's context", and
      ``instructions`` is the more obvious translation -- it is rejected
      because it is OUTSIDE ``input``, and every cache breakpoint AWS documents
      hangs off an ``input_text`` content block ("prompt_cache_breakpoint on
      input_text, input_image and input_file blocks (Responses API)",
      prompt-caching.html's supported-models table). Putting the one large
      stable prefix somewhere a breakpoint cannot be attached would forfeit the
      reason for choosing this API. The role is ``developer`` rather than
      ``system`` because that is what AWS's own GPT-5.6 examples use; it is a
      config knob (BEDROCK_SYSTEM_ROLE) precisely because this is the one
      mapping decision with no citation proving the two are equivalent.

  max_completion_tokens=MATCHING_MAX_TOKENS
      -> max_output_tokens=MATCHING_MAX_TOKENS
      Same meaning, same caveat: on a reasoning model it caps reasoning AND
      visible output together, which is what config's note on
      MATCHING_MAX_TOKENS already records. The value is NOT recomputed.

  reasoning_effort=MATCHING_REASONING_EFFORT
      -> reasoning={"effort": MATCHING_REASONING_EFFORT}
      The Responses API nests it. NOTE A TYPING DIVERGENCE THAT IS NOT A
      RUNTIME ONE: the installed SDK (openai 1.99.9, measured) types
      ``ReasoningEffort`` as ``'minimal'|'low'|'medium'|'high'`` and this
      project's configured value is ``'none'`` -- which the chat path has been
      sending successfully since 2026-08-04, because these are TypedDict type
      hints and nothing validates them at runtime. So ``'none'`` goes on the
      wire unchanged. Whether BEDROCK accepts it is the single highest-value
      probe check; see VERIFY-AT-GO-LIVE (1).

  seed=MATCHING_SEED
      -> DROPPED, RECORDED, NOT SILENT.
      There is no ``seed`` parameter on the Responses API: it is absent from
      the installed SDK's ``responses.create`` signature and present on
      ``chat.completions.create`` (measured, not inferred). The drop is counted
      in BEDROCK_ADAPTER_DEGRADATIONS["seed_not_expressible"], which reaches
      the run-end degradation report through oncotriage/degradation.py, and
      logged once per process at WARNING.
      WHAT IT COSTS, honestly: config's own note records that the seed is
      "best-effort only" and that the model returns no ``system_fingerprint``,
      so Stage 5 was already only best-effort reproducible. Losing it widens
      that, and it is the reason k=3 rater agreement across a provider flip
      cannot be read as a pure provider difference.
      ``config.BEDROCK_SEND_SEED_IN_EXTRA_BODY`` exists so that go-live can
      turn it back on in one edit if the probe shows Bedrock tolerates an
      unknown field. It defaults to False because a 400 on an unknown field
      fails EVERY Stage 5 call of the run.

  response_format={"type":"json_schema",
                   "json_schema":{"name":..., "strict":True, "schema":...}}
      -> text={"format":{"type":"json_schema", "name":..., "strict":True,
                         "schema":...}}
      THE NESTING IS DIFFERENT AND THAT IS THE WHOLE TRANSLATION: Chat
      Completions wraps the three fields in a ``json_schema`` object; the
      Responses API flattens them into the format object itself. Confirmed
      against the installed SDK's ``ResponseFormatTextJSONSchemaConfigParam``,
      whose keys are exactly ``type``/``name``/``schema``/``strict``
      (+``description``). The schema itself is ``build_response_format()``'s,
      unwrapped rather than rebuilt, so the fixture recorder and this path
      cannot disagree about what was sent.
      READ VERIFY-AT-GO-LIVE (3) BEFORE TRUSTING THIS ONE.

  temperature
      -> NOT EXPRESSIBLE ON THIS MODEL. DROPPED, COUNTED, LOGGED.
      THE REQUEST IS UNCHANGED and this entry is not: ``config.MATCHING_TEMPERATURE``
      is 0.0 now, as the pipeline's determinism rule, and gpt-5.6-terra rejects
      every value but its default -- probed live 2026-08-04, quoted at that
      constant: "'temperature' does not support 0 with this model. Only the
      default (1) value is supported." So the sentence that used to stand here
      ("MATCHING_TEMPERATURE is None") described a project-wide policy that no
      longer exists; the restriction is the MODEL's and is declared as such in
      ``config.MATCHING_TEMPERATURE_MODEL_ACCEPTS``.
      THE DECISION IS DECLARED, NEVER DISCOVERED BY SENDING, which is why this
      branch does not try the parameter and fall back on a 400: a 400 here
      fails a Stage 5 call that has already been signed, and it cannot tell a
      model restriction from a throttled account. The drop is counted once per
      process under ``temperature_not_expressible``, beside the seed's.
      WHAT IT COSTS: Stage 5 on this arm samples at the provider default of 1
      and no configuration can change that, so its verdicts are less
      reproducible than the shipped Converse arm's by construction. That is a
      property of the judge, and the only remedy is a different one.

  timeout=config.get_matching_request_timeout()
      -> the same object, passed the same way. Client-side, never on the wire,
      never recorded in a fixture.

  (new) store=config.BEDROCK_STORE
      SENT AS False, AND THAT OVERRIDES THE VENDOR DEFAULT OF True. AWS: "When
      ``store`` is ``true`` (the default), Amazon Bedrock retains the response,
      including the input and output, for 30 days." The Stage 5 input is a
      rendered patient record. See config.BEDROCK_STORE.

  (new) service_tier
      OMITTED by default, which IS Standard. Terra supports Standard only;
      "priority" and "flex" are refused by config's validator rather than sent.

  (new) prompt_cache_key / prompt_cache_options
      OMITTED by default -- implicit caching, the vendor default. Explicit mode
      DISABLES the automatic breakpoint, so a half-configured explicit setup
      caches nothing at all while looking configured. ``prompt_cache_options``
      travels in ``extra_body`` because the installed SDK has no such
      parameter; AWS's own example does the same.

THE RESPONSE COMES BACK AS A ChatCompletion
--------------------------------------------
``translate_response`` returns a real ``openai.types.chat.ChatCompletion``,
constructed by validation rather than faked with a namespace, so every
attribute Stage 5 reads exists with the type it expects and a shape error
surfaces here rather than thirty frames downstream. Stage 5's post-call code is
untouched:

  response.choices[0].message.content   <- the concatenated ``output_text``
                                           parts of the ``message`` items in
                                           ``response.output``. ``reasoning``
                                           items are skipped -- they are not
                                           visible output and Stage 5 parses
                                           this string as JSON.
  response.choices[0].message.refusal   <- a ``refusal`` content part, if any.
  response.choices[0].finish_reason     <- derived; see below.
  response.usage.prompt_tokens          <- usage.input_tokens
  response.usage.completion_tokens      <- usage.output_tokens
  response.usage.prompt_tokens_details
          .cached_tokens                <- usage.input_tokens_details
                                             .cached_tokens
  response.usage.completion_tokens_details
          .reasoning_tokens             <- usage.output_tokens_details
                                             .reasoning_tokens
  response.model                        <- the echo, PASSED THROUGH UNCHANGED.

  ``cache_write_tokens`` is carried through as an extra field on
  prompt_tokens_details (the SDK's models tolerate extras; measured). Nothing
  in this project reads it yet and nothing prices it -- it is recorded so that
  a future pass can, rather than being discarded at the boundary.

THE MODEL ECHO IS NOT REWRITTEN, AND THAT IS DELIBERATE. It would have been one
line to present ``MATCHING_MODEL`` here and keep ``MatchingModelMismatchError``
quiet, and it would have made every stored row claim a model that did not serve
it. Instead ``evaluation.py`` compares the echo against
``config.matching_wire_model()`` -- identical to MATCHING_MODEL with the flag
off -- and ``inferences.matching_model`` stores what answered. PRICING_CONFIG
carries rows for the three Bedrock wire ids for exactly this reason; an id not
in it raises UnknownModelPricingError before a row is written, which is the
loud failure this project requires.

finish_reason, DERIVED FROM status + incomplete_details.reason:

  status "completed"                                  -> "stop"
  status "incomplete", reason "max_output_tokens"     -> "length"
  status "incomplete", reason "content_filter"        -> "content_filter"
  status "incomplete", reason anything else / absent  -> "length", counted
  status "failed" / an ``error`` object               -> raises
  status "in_progress" / "queued"                     -> raises

  "length" IS THE LOAD-BEARING ONE: it is what drives Stage 5's truncation-split
  path (FINISH_REASON_LENGTH). Mapping a truncated answer to "stop" would hand
  the parser a half-written JSON array and spend the parse-retry budget on a
  problem the split budget exists to solve.
  AN UNRECOGNISED ``incomplete`` REASON MAPS TO "length" RATHER THAN RAISING,
  and that is argued rather than convenient: ``incomplete`` already means the
  model did not finish, "length" is the only unfinished finish_reason the
  downstream understands, and the alternative ("stop") asserts a complete
  answer that is known not to be one. It is counted, so the choice is visible.
  A ``failed`` or still-running response RAISES, because it carries no verdicts
  at all: Stage 5's except turns that into its API-failure return, which
  records the tokens billed so far and does not invent an evaluation.

ERROR TAXONOMY
--------------
The OpenAI SDK raises the SAME exception classes against a Bedrock base URL as
against OpenAI's, because they are selected from the HTTP status code, not from
the host. Stage 5 catches bare ``Exception`` and turns any of them into its
API-failure return, so control flow is identical by construction. What this
module adds is a NAME for each, logged, so that a Bedrock-specific failure is
diagnosable from the record instead of from a stack trace:

  429 RateLimitError        throttling. Retried in-SDK by the client's
                            max_retries (OPENAI_SDK_MAX_RETRIES), exactly as an
                            OpenAI 429 is.
  401 AuthenticationError   bad, absent or EXPIRED key. A short-term Bedrock
                            key lasts at most 12 hours, which is shorter than a
                            full-corpus run -- see VERIFY-AT-GO-LIVE (6).
  403 PermissionDeniedError model not allow-listed, or the missing
                            ``bedrock:InvokeModel`` on the account's default
                            project (``arn:aws:bedrock:{region}:{account}:
                            project/default``) that ``bedrock-runtime``
                            additionally authorizes.
  404 NotFoundError         wrong base-URL path, or a model id this endpoint
                            does not serve. The mantle ``/openai/v1`` vs
                            ``/v1`` split is the likely cause; see
                            config.BEDROCK_BASE_URL_TEMPLATES.
  400 BadRequestError       an unsupported parameter. THE EXPECTED SHAPE OF
                            EVERY VERIFY-AT-GO-LIVE FAILURE BELOW.
  408 APITimeoutError       the structured timeout fired.
      APIConnectionError    the endpoint was unreachable.
  5xx InternalServerError   retried in-SDK.

WHERE THE DOCUMENTATION DOES NOT STATE AN ERROR SHAPE, THIS SAYS SO. AWS does
not document the error body of the OpenAI-compatible surface -- whether a
Bedrock ``ThrottlingException`` arrives as a 429 with an OpenAI-shaped error
object, or as something the SDK maps to APIStatusError, is unverified.
``classify_error`` therefore falls back to the SUPERCLASS (``APIStatusError``,
then ``OpenAIError``, then ``Exception``) and reports the status code it saw
rather than guessing a category.

VERIFY-AT-GO-LIVE
-----------------
Every line here is a fact taken from documentation that only a live call can
confirm. ``bedrock_probe.py`` is the first command of day one and each item
names the check that settles it and the edit if it differs.

 (1) reasoning effort "none" is accepted.
     PROBE: issues the real request at config.MATCHING_REASONING_EFFORT and
     prints ``response.reasoning``. AWS documents no effort vocabulary for the
     OpenAI-compatible surface and the installed SDK types only
     minimal/low/medium/high.
     IF IT 400s: the pipeline's calibration is at 'none' (config records the
     69.1% agreement measurement behind that choice), so the honest options are
     'minimal' -- the nearest documented level, and A DIFFERENT JUDGE, needing
     re-baselining -- or the mantle endpoint if the two differ. Do not silently
     substitute a level.

 (2) the reasoning parameter shape is ``{"effort": ...}``.
     PROBE: same call. IF IT 400s naming ``reasoning``, the shape moved; edit
     ``_reasoning_param``.

 (3) structured output survives as ``text.format`` with ``strict: true``.
     THIS IS THE ONE THE MODEL CARD CASTS DOUBT ON. Terra's card lists
     "Structured outputs" under NOT SUPPORTED on ``bedrock-runtime``. That link
     points at ``structured-output.html``, which is the Bedrock-NATIVE feature
     -- ``outputConfig.textFormat`` on Converse and ``output_config.format`` /
     ``response_format`` on InvokeModel -- and that page's supported-API table
     names Converse, InvokeModel, cross-Region and batch inference and does not
     mention the OpenAI-compatible Responses API at all. Terra's card ALSO
     lists Invoke as unsupported, which is consistent with the card's row being
     about the native feature. So the card does not settle the question either
     way, and no AWS page states whether ``text.format`` is honoured.
     PROBE: sends the real Stage 5 schema and (a) checks the call is accepted,
     (b) parses the output against the schema, (c) checks
     ``response.text.format`` echoes back.
     IF IT 400s: strict mode is unavailable, the schema becomes a prompt-level
     instruction again, and Stage 5's parse-retry budget becomes load-bearing
     in a way it has not been since the Structured Outputs pass. That is a
     CHANGE IN JUDGE BEHAVIOUR, not a config detail: report it before running a
     campaign.
     IF IT IS ACCEPTED AND SILENTLY IGNORED -- accepted, no error, output not
     constrained -- that is the dangerous outcome and (c) is what catches it.

 (4) ``seed`` really is unavailable, and whether extra_body is tolerated.
     PROBE: a second call with the seed smuggled through extra_body, run only
     under ``--probe-seed``. IF IT SUCCEEDS: set
     config.BEDROCK_SEND_SEED_IN_EXTRA_BODY = True.

 (5) the cached-token field is ``usage.input_tokens_details.cached_tokens``.
     Documented (prompt-caching.html shows exactly that response shape) and
     therefore only weakly uncertain. PROBE: two identical calls; the second
     must report a non-zero value, and the probe prints the whole usage block
     so a renamed field is visible rather than read as a zero. IF IT MOVED:
     edit ``_usage_block``; a wrong name here does not raise, it stores NULL,
     and NULL means "no response reported it" -- a silent loss of exactly the
     measurement that says whether packing pays for itself.

 (6) whether the granted quota is on ``bedrock-runtime`` or ``bedrock-mantle``,
     and which Region.
     PROBE: prints the base URL it used and the request id. IF THE QUOTA IS ON
     MANTLE: set BEDROCK_ENDPOINT = "bedrock-mantle" AND
     BEDROCK_MATCHING_MODEL = "openai.gpt-5.6-terra" (the bare id) -- the
     validator will refuse the profile-prefixed id there.

 (7) the model echo matches the configured id.
     PROBE: prints ``response.model``. Stage 5 RAISES on a mismatch. IF the
     echo is the bare ``openai.gpt-5.6-terra`` while a ``us.`` profile was
     requested, the comparison in evaluation.py needs to compare against the
     echoed family rather than the profile -- report it, do not loosen the
     check to a substring.

 (8) PRICING. PRICING_CONFIG's three Bedrock rows are the model card's Standard
     tier, SHORT context (272K) band. PROBE: prints its own cost from those
     rows. IF the console's billing disagrees, the band or the routing option
     is wrong; both are in PRICING_CONFIG's comment.

 (9) key lifetime. A short-term key expires in <= 12 hours; a full-corpus batch
     run is longer. Nothing here refreshes it. IF a long run 401s mid-way, the
     fix is a long-term key or wiring ``aws-bedrock-token-generator`` into
     ``config.get_bedrock_client``.

(10) ``store=false`` is accepted. PROBE: the call is made with it; a 400 naming
     ``store`` means retention cannot be declined per request and the data
     -retention account setting must be used instead (AWS documents a
     ``none`` retention mode that rejects an explicit ``store=true``).

NOTHING IN THIS MODULE RUNS AT IMPORT. No client, no credential, no socket, no
file. ``tests/test_package_invariants.py`` section 2 proves it for every module
in the package and this one is in that sweep.
"""

from collections import Counter
from typing import Dict, List, Optional, Tuple

import openai
from openai.types.chat import ChatCompletion

from oncotriage import config
from oncotriage.agent import deps
from oncotriage.agent.response_schema import build_response_format
from oncotriage.observability import get_logger


log = get_logger(__name__)


# ---------------------------------------------------------------------------
# The degradation record
# ---------------------------------------------------------------------------
#
# A MODULE-LEVEL Counter, on AGE_PARSE_FAILURES' footing (item 11a), NOT a key
# in any result dict: the twelve characterization fixtures diff Stage 4's and
# Stage 5's dicts field by field, and a new field there means recapturing all
# twelve at Terra prices for something no stage reads.
#
# WHAT EACH KEY MEANS -- every one of them is "the request or the response was
# not what this adapter was built against", which is precisely the class of
# thing that must never be silent.
BEDROCK_ADAPTER_DEGRADATIONS = Counter()

DEGRADATION_SEED_DROPPED = "seed_not_expressible"
DEGRADATION_TEMPERATURE_DROPPED = "temperature_not_expressible"
DEGRADATION_UNKNOWN_INCOMPLETE = "incomplete_reason_unrecognised"
DEGRADATION_NO_USAGE = "response_carried_no_usage"
DEGRADATION_NO_MESSAGE_ITEM = "response_carried_no_message_item"
DEGRADATION_UNKNOWN_ERROR = "error_class_unrecognised"

DEGRADATION_KEYS = (
    DEGRADATION_SEED_DROPPED,
    # THE TEMPERATURE THIS PIPELINE ASKS FOR AND THIS MODEL WILL NOT TAKE. It
    # moves whenever `config.MATCHING_TEMPERATURE` is set and this arm is live,
    # which at the shipped constant is EVERY run of it -- so a non-zero value
    # here is the configuration working, exactly as the seed's is, and zero
    # means the operator set the constant to None.
    DEGRADATION_TEMPERATURE_DROPPED,
    DEGRADATION_UNKNOWN_INCOMPLETE,
    DEGRADATION_NO_USAGE,
    DEGRADATION_NO_MESSAGE_ITEM,
    DEGRADATION_UNKNOWN_ERROR,
)
"""The closed vocabulary of this counter's keys. Declared so a reader can
branch on it exhaustively and so a typo in a bump site is catchable by test
rather than by producing a counter nobody reads -- the same argument
``deps.RESOLUTION_STATES`` carries."""

# REGISTERED IN ``oncotriage/degradation.py``'s ``_REGISTRY_SPEC``, NOT BY A
# ``register()`` CALL HERE, and the reason is a cycle rather than a preference:
# that module imports ``oncotriage.agent.evaluation``, which imports this
# module, so an import of ``degradation`` here would close the loop. The second
# route (``register()`` at the owning module's own scope) exists for exactly
# the modules that CANNOT be imported by the registry; this one can be, so it
# takes the declarative route and the registry stays the one list.


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class BedrockResponseTranslationError(RuntimeError):
    """A Responses reply carried no answer this pipeline can use.

    A ``failed`` response, one still ``in_progress``, or one carrying an
    ``error`` object. It is a RuntimeError subclass rather than a ValueError on
    the ``UnknownModelPricingError`` / ``MatchingModelMismatchError``
    precedent: a stray ``except ValueError`` around a JSON parse must not eat
    it.

    IT IS RAISED RATHER THAN TURNED INTO AN EMPTY ANSWER. Stage 5's own except
    catches it and takes the API-failure return, which records the tokens
    already billed, names the error, and evaluates nothing. Manufacturing a
    ``finish_reason`` for a response that failed would put a fabricated verdict
    path into the one stage that spends money.
    """


# ---------------------------------------------------------------------------
# Request translation
# ---------------------------------------------------------------------------

def _message_item(role: str, text: str) -> Dict:
    """One Responses ``input`` item carrying one text part.

    The ``{"type": "message", ...}`` long form rather than the
    ``{"role": ..., "content": "..."}`` shorthand, because a cache breakpoint
    attaches to an ``input_text`` BLOCK and the shorthand has no block to
    attach it to. Costs nothing today and is the shape
    BEDROCK_PROMPT_CACHE_MODE will need.
    """
    return {
        "type": "message",
        "role": role,
        "content": [{"type": "input_text", "text": text}],
    }


def _reasoning_param() -> Optional[Dict]:
    """``reasoning`` for the configured effort, or None to omit the field.

    ``MATCHING_REASONING_EFFORT`` is a plain string in config and goes on the
    wire unchanged -- including ``'none'``, which the installed SDK's type hint
    does not list and which the chat path has been sending since 2026-08-04.
    See VERIFY-AT-GO-LIVE (1) and (2).
    """
    if config.MATCHING_REASONING_EFFORT is None:
        return None
    return {"effort": config.MATCHING_REASONING_EFFORT}


def _text_format_param() -> Dict:
    """``text`` built from ``build_response_format()``, re-nested not rebuilt.

    Chat Completions:  {"type":"json_schema","json_schema":{name,strict,schema}}
    Responses:         {"format":{"type":"json_schema",name,strict,schema}}

    THE SCHEMA IS UNWRAPPED FROM THE CHAT FORM RATHER THAN BUILT A SECOND TIME.
    Two builders for one schema is the shape pass 20f-2 removed for the MedCPT
    checkpoint and pass 20c-3a for the BM25 sparse model: they agree until the
    day they do not, and here the disagreement would be a judge constrained by
    a schema that is not the one the fixture recorded.

    Raises:
        BedrockResponseTranslationError: if the chat-form builder ever stops
            producing the shape this unwraps. Better here, once, than as a
            silently unconstrained decode.
    """
    chat_form = build_response_format()
    inner = chat_form.get("json_schema")
    if not isinstance(inner, dict) or "schema" not in inner or "name" not in inner:
        raise BedrockResponseTranslationError(
            "build_response_format() no longer returns the Chat Completions "
            f"json_schema shape this adapter unwraps; got keys "
            f"{sorted(chat_form)}. Update _text_format_param() in "
            "oncotriage/agent/bedrock_adapter.py.")

    fmt = {
        "type": "json_schema",
        "name": inner["name"],
        "schema": inner["schema"],
    }
    if "strict" in inner:
        fmt["strict"] = inner["strict"]
    return {"format": fmt}


def build_bedrock_request(system_prompt: str, user_prompt: str) -> Dict:
    """The complete kwargs for ``client.responses.create``. PURE.

    Pure and separate from the call on purpose: it is what
    ``tests/test_agent_bedrock_adapter.py`` compares field by field against a
    pinned expectation, and a translation proved only through a live call is a
    translation that cannot be tested without spending money.

    Validates the provider configuration FIRST, so a misconfiguration names the
    constant to edit rather than arriving as a 400 from a signed request.
    """
    config.validate_matching_provider_config()

    kwargs: Dict = {
        "model": config.matching_wire_model(),
        "input": [
            _message_item(config.BEDROCK_SYSTEM_ROLE, system_prompt),
            _message_item("user", user_prompt),
        ],
        "max_output_tokens": config.MATCHING_MAX_TOKENS,
        "text": _text_format_param(),
        # See config.BEDROCK_STORE. Sent explicitly BECAUSE the vendor default
        # is the opposite; an omitted field here would retain the patient
        # record server-side for 30 days by default.
        "store": config.BEDROCK_STORE,
        "timeout": config.get_matching_request_timeout(),
    }

    reasoning = _reasoning_param()
    if reasoning is not None:
        kwargs["reasoning"] = reasoning

    # OMITTED, NOT SENT AS None. `service_tier=None` is a value the SDK would
    # serialise; omission is what "Standard" means.
    if config.BEDROCK_SERVICE_TIER is not None:
        kwargs["service_tier"] = config.BEDROCK_SERVICE_TIER

    if config.BEDROCK_PROMPT_CACHE_KEY is not None:
        kwargs["prompt_cache_key"] = config.BEDROCK_PROMPT_CACHE_KEY

    extra_body: Dict = {}
    if config.BEDROCK_PROMPT_CACHE_MODE is not None:
        # extra_body because the installed SDK has no such parameter; AWS's own
        # documented example uses extra_body for exactly this field.
        extra_body["prompt_cache_options"] = {
            "mode": config.BEDROCK_PROMPT_CACHE_MODE}

    if config.BEDROCK_SEND_SEED_IN_EXTRA_BODY:
        extra_body["seed"] = config.MATCHING_SEED
    # THE DROP IS RECORDED BY `_warn_seed_once`, NOT HERE, and the split is
    # deliberate on two counts. This function is documented PURE and is driven
    # directly by the tests; a pure function that mutates a module-level
    # counter is neither. And the drop is a fact about the CONFIGURATION, not
    # about each request: counting it per call would put a five-figure number
    # in the run-end degradation report on every Bedrock run, which makes that
    # report's "all counters are zero" signal mean nothing. It is counted once
    # per process, beside the one warning that says the same thing.

    if extra_body:
        kwargs["extra_body"] = extra_body

    return kwargs


# ---------------------------------------------------------------------------
# Response translation
# ---------------------------------------------------------------------------

_STATUS_COMPLETED = "completed"
_STATUS_INCOMPLETE = "incomplete"

FINISH_STOP = "stop"
FINISH_LENGTH = "length"
FINISH_CONTENT_FILTER = "content_filter"

_INCOMPLETE_REASON_TO_FINISH = {
    "max_output_tokens": FINISH_LENGTH,
    "content_filter": FINISH_CONTENT_FILTER,
}
"""The two ``incomplete_details.reason`` values the OpenAI Responses API
documents. Anything else is counted and mapped to ``length`` -- see the module
docstring for why that direction and not ``stop``."""


def _as_dict(obj) -> Dict:
    """A plain dict for an SDK model, a dict, or None.

    Accepts BOTH so that a test may hand this module literal dicts and the SDK
    hands it pydantic models, WITHOUT the module having to import a response
    type it would then be pinned to. ``model_dump`` is preferred over attribute
    walking because it carries the extra fields Bedrock adds
    (``cache_write_tokens``) that the SDK's own model does not declare.
    """
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dump()
    return {k: v for k, v in vars(obj).items() if not k.startswith("_")}


def _finish_reason(status, incomplete_details) -> str:
    """Map Responses ``status`` + ``incomplete_details`` onto a finish_reason.

    Raises:
        BedrockResponseTranslationError: for a status that carries no answer.
    """
    if status == _STATUS_COMPLETED:
        return FINISH_STOP

    if status == _STATUS_INCOMPLETE:
        reason = _as_dict(incomplete_details).get("reason")
        mapped = _INCOMPLETE_REASON_TO_FINISH.get(reason)
        if mapped is not None:
            return mapped
        BEDROCK_ADAPTER_DEGRADATIONS[DEGRADATION_UNKNOWN_INCOMPLETE] += 1
        log.warning(
            "Bedrock returned an incomplete response with an unrecognised "
            "reason; treating it as a length truncation, which is the only "
            "unfinished finish_reason Stage 5 acts on",
            stage=5, event="bedrock_incomplete_reason_unrecognised",
            reason=str(reason), degraded=True, provider=config.MATCHING_PROVIDER)
        return FINISH_LENGTH

    raise BedrockResponseTranslationError(
        f"Bedrock returned a response with status {status!r}, which carries no "
        f"answer to evaluate. Stage 5 records this as an API failure with the "
        f"tokens billed so far rather than manufacturing a verdict.")


def _content_and_refusal(output) -> Tuple[str, Optional[str]]:
    """The visible text and the refusal, walked out of ``response.output``.

    ``reasoning`` items are SKIPPED rather than concatenated: they are not
    visible output, Stage 5 parses this string as JSON, and folding a reasoning
    summary into it would corrupt every parse.
    """
    texts: List[str] = []
    refusals: List[str] = []

    for item in (output or []):
        item_d = _as_dict(item)
        if item_d.get("type") not in (None, "message"):
            continue
        for part in (item_d.get("content") or []):
            part_d = _as_dict(part)
            kind = part_d.get("type")
            if kind == "output_text":
                texts.append(part_d.get("text") or "")
            elif kind == "refusal":
                refusals.append(part_d.get("refusal") or "")

    if not texts and not refusals:
        BEDROCK_ADAPTER_DEGRADATIONS[DEGRADATION_NO_MESSAGE_ITEM] += 1

    return "".join(texts), ("".join(refusals) if refusals else None)


def _usage_block(usage) -> Dict:
    """Responses usage -> Chat Completions usage, key by key.

    NOTHING IS DEFAULTED TO ZERO THAT WAS NOT REPORTED. Stage 5 distinguishes
    "the response carried no cached-token reading" (NULL) from "the provider
    cached nothing" (0), and the whole point of ``llm_classifier_cached_input_
    tokens`` is that distinction. An absent details object therefore produces
    an absent details object, not one full of zeros.

    ``prompt_tokens`` / ``completion_tokens`` DO default to 0, because Stage 5
    adds them unconditionally and a missing count there is a broken response
    rather than an unreported measurement -- and it is counted.
    """
    u = _as_dict(usage)
    if not u:
        BEDROCK_ADAPTER_DEGRADATIONS[DEGRADATION_NO_USAGE] += 1

    prompt_tokens = u.get("input_tokens") or 0
    completion_tokens = u.get("output_tokens") or 0

    block: Dict = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": u.get("total_tokens",
                              prompt_tokens + completion_tokens),
    }

    in_details = u.get("input_tokens_details")
    if in_details is not None:
        d = dict(_as_dict(in_details))
        # Carried verbatim, including Bedrock's `cache_write_tokens`, which the
        # SDK's own model does not declare and tolerates as an extra field.
        block["prompt_tokens_details"] = d

    out_details = u.get("output_tokens_details")
    if out_details is not None:
        block["completion_tokens_details"] = dict(_as_dict(out_details))

    return block


def translate_response(response):
    """A Responses reply as the ``ChatCompletion`` Stage 5 already consumes.

    Returns a REAL ``openai.types.chat.ChatCompletion``, validated rather than
    faked, so every attribute the post-call code reads exists with the type it
    expects and a shape error surfaces HERE rather than thirty frames into the
    parse. ``model_validate`` rather than ``model_construct`` for the same
    reason: construction that skips validation would let a malformed
    translation through silently.

    Raises:
        BedrockResponseTranslationError: a failed or unfinished response.
    """
    r = _as_dict(response)

    error = r.get("error")
    if error:
        raise BedrockResponseTranslationError(
            f"Bedrock returned a response carrying an error object: {error!r}")

    content, refusal = _content_and_refusal(r.get("output"))
    finish = _finish_reason(r.get("status"), r.get("incomplete_details"))

    # A RESPONSE WITH NO MODEL ECHO IS REFUSED BY NAME. Stage 5 has a
    # documented path for a None model -- a stub, or a recording made before
    # the field existed -- and it is deliberately NOT reachable from here: on
    # this provider an absent echo means the one check that says WHICH judge
    # answered cannot be made, and `MatchingModelMismatchError` exists because
    # a run half-served by another model is the confound this project removes.
    # Without this, the ChatCompletion below fails validation on a required
    # field, which is the same refusal with a message nobody can act on.
    model_echo = r.get("model")
    if not isinstance(model_echo, str) or not model_echo:
        raise BedrockResponseTranslationError(
            f"Bedrock returned a response whose `model` field is "
            f"{model_echo!r}. Stage 5 compares that echo against "
            f"config.matching_wire_model() to prove which judge answered, and "
            f"a response that names none cannot be attributed to one.")

    return ChatCompletion.model_validate({
        # The Responses id, kept as-is. Nothing in this project reads it; it is
        # here because a ChatCompletion requires one and inventing a value that
        # cannot be traced back to a request would be worse than carrying the
        # real one.
        "id": r.get("id") or "",
        "object": "chat.completion",
        "created": r.get("created_at") or 0,
        # PASSED THROUGH UNCHANGED. See the module docstring: evaluation.py
        # compares this against config.matching_wire_model() and the database
        # stores it.
        "model": model_echo,
        "choices": [{
            "index": 0,
            "finish_reason": finish,
            "logprobs": None,
            "message": {
                "role": "assistant",
                "content": content,
                "refusal": refusal,
            },
        }],
        "usage": _usage_block(r.get("usage")),
    })


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------

ERROR_THROTTLED = "throttled"
ERROR_AUTH = "auth"
ERROR_FORBIDDEN = "forbidden"
ERROR_NOT_FOUND = "not_found"
ERROR_BAD_REQUEST = "bad_request"
ERROR_TIMEOUT = "timeout"
ERROR_CONNECTION = "connection"
ERROR_SERVER = "server"
ERROR_TRANSLATION = "translation"
ERROR_UNCLASSIFIED = "unclassified"

ERROR_CATEGORIES = (
    ERROR_THROTTLED, ERROR_AUTH, ERROR_FORBIDDEN, ERROR_NOT_FOUND,
    ERROR_BAD_REQUEST, ERROR_TIMEOUT, ERROR_CONNECTION, ERROR_SERVER,
    ERROR_TRANSLATION, ERROR_UNCLASSIFIED,
)
"""Closed. ``ERROR_UNCLASSIFIED`` is a member rather than a fallback nobody
named: an error this taxonomy does not recognise is a finding, and a category
it can be counted under is how it becomes one."""


def classify_error(exc: BaseException) -> str:
    """Name what went wrong. Never changes control flow.

    NOTHING ABOUT STAGE 5's BEHAVIOUR DEPENDS ON THIS. Its except clause
    catches bare ``Exception`` and takes the same return whatever this says, so
    an error the taxonomy misreads costs a log field and nothing else. That is
    deliberate: a classifier that gated recovery would be a second retry policy
    disagreeing with the SDK's.

    Classification is by the SDK's EXCEPTION CLASS, which the SDK selects from
    the HTTP status code and not from the host -- which is why the same
    taxonomy is correct for a Bedrock base URL. Where AWS documents no error
    shape for the OpenAI-compatible surface, this falls back through the
    superclasses rather than inventing a category.
    """
    if isinstance(exc, BedrockResponseTranslationError):
        return ERROR_TRANSLATION

    for cls_name, category in (
            ("APITimeoutError", ERROR_TIMEOUT),
            ("RateLimitError", ERROR_THROTTLED),
            ("AuthenticationError", ERROR_AUTH),
            ("PermissionDeniedError", ERROR_FORBIDDEN),
            ("NotFoundError", ERROR_NOT_FOUND),
            ("BadRequestError", ERROR_BAD_REQUEST),
            ("InternalServerError", ERROR_SERVER),
            # APIConnectionError is LAST of the connection family because
            # APITimeoutError subclasses it; checked first above.
            ("APIConnectionError", ERROR_CONNECTION),
    ):
        cls = getattr(openai, cls_name, None)
        if cls is not None and isinstance(exc, cls):
            return category

    # Superclass fallback: an APIStatusError this taxonomy does not name is
    # still an HTTP answer, and its status code is the diagnosis.
    status_cls = getattr(openai, "APIStatusError", None)
    if status_cls is not None and isinstance(exc, status_cls):
        BEDROCK_ADAPTER_DEGRADATIONS[DEGRADATION_UNKNOWN_ERROR] += 1
        return ERROR_UNCLASSIFIED

    BEDROCK_ADAPTER_DEGRADATIONS[DEGRADATION_UNKNOWN_ERROR] += 1
    return ERROR_UNCLASSIFIED


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------

_SEED_WARNED = False
_TEMPERATURE_WARNED = False


def _warn_temperature_once() -> None:
    """One WARNING and one counter bump per process for the dropped temperature.

    A SECOND FUNCTION BESIDE ``_warn_seed_once`` RATHER THAN A LINE INSIDE IT,
    because the two are not the same event and are not gated by the same
    condition: the seed's drop is unconditional on this arm, and this one fires
    only while ``MATCHING_TEMPERATURE`` is set -- an operator who chose the
    documented opt-out asked for nothing and has degraded nothing. Folding them
    would make the seed's warning conditional on a temperature or the
    temperature's unconditional; both are wrong.

    Once rather than per call, and the counter bumped HERE rather than in
    ``build_bedrock_request``, on ``_warn_seed_once``'s arguments verbatim: the
    builder is documented PURE and driven directly by the tests, and the drop
    is a property of the CONFIGURATION rather than of each request, so 1 says
    everything 45,000 would.

    THE CAPABILITY IS READ FROM ITS OWNER rather than re-derived from
    ``MATCHING_PROVIDER``. ``config.matching_temperature_capability()`` is the
    one function that decides what the live arm sends, and a second copy of
    that decision here is how a request and its own record come to disagree.
    """
    global _TEMPERATURE_WARNED
    if _TEMPERATURE_WARNED:
        return
    if config.MATCHING_TEMPERATURE is None:
        return                      # the declared opt-out; nothing was dropped
    if (config.matching_temperature_capability()
            == config.MATCHING_TEMPERATURE_SUPPORTED):
        # UNREACHABLE ON THIS ARM AT THE SHIPPED DECLARATION, and asked anyway,
        # on the argument in the docstring: the owner is what decides whether a
        # drop happened, and a branch keyed on "this is a Terra arm, therefore
        # it was dropped" would be a second copy of the capability table.
        return
    _TEMPERATURE_WARNED = True
    BEDROCK_ADAPTER_DEGRADATIONS[DEGRADATION_TEMPERATURE_DROPPED] += 1
    log.warning(
        "MATCHING_TEMPERATURE is set and gpt-5.6-terra rejects every value but "
        "its default, so it is being dropped; Stage 5 samples at the provider "
        "default on this arm and no configuration can change that. Set "
        "MATCHING_TEMPERATURE to None to declare the opt-out, or run the "
        "Converse arm, whose model accepts the parameter.",
        stage=5, event="bedrock_temperature_dropped", degraded=True,
        provider=config.MATCHING_PROVIDER,
        reason=(f"configured={config.MATCHING_TEMPERATURE!r} "
                f"capability={config.matching_temperature_capability()!r}"))


def _warn_seed_once() -> None:
    """One WARNING and one counter bump per process for the dropped seed.

    Once rather than per call for the reason ``_finish_reason_warned`` exists
    in evaluation.py: a per-call line on a 22,000-patient run is noise that
    buries the lines that matter. THE COUNTER IS BUMPED HERE RATHER THAN IN
    ``build_bedrock_request`` for the reason recorded there -- the drop is a
    property of the configuration, so 1 says everything 45,000 would, and a
    counter that is guaranteed non-zero on every run of a configuration makes
    the run-end report's CLEAN line worthless.
    """
    global _SEED_WARNED
    if _SEED_WARNED:
        return
    _SEED_WARNED = True
    BEDROCK_ADAPTER_DEGRADATIONS[DEGRADATION_SEED_DROPPED] += 1
    log.warning(
        "MATCHING_SEED is not expressible on the Responses API and is being "
        "dropped; Stage 5 is less reproducible on this provider than on "
        "OpenAI. Set config.BEDROCK_SEND_SEED_IN_EXTRA_BODY only after the "
        "go-live probe shows the field is tolerated.",
        stage=5, event="bedrock_seed_dropped", degraded=True,
        provider=config.MATCHING_PROVIDER)


def call_matching_model_bedrock(system_prompt: str, user_prompt: str):
    """Issue the Stage 5 request against Bedrock; return a ChatCompletion.

    THE CALLER OWNS ERROR HANDLING, exactly as it does for the OpenAI path:
    this raises whatever the client raises and ``node_llm_classifier_
    evaluation``'s except turns it into a retry. The only thing added on the
    failure path is a log line naming the taxonomy category, emitted before the
    re-raise so the record exists whatever the caller then does.

    THE CLIENT COMES FROM ``deps``, never from ``config`` directly, so a test
    harness installs ``deps.set_override(deps.BEDROCK_CLIENT, stub)`` and this
    call site sees it -- the same grip ``OPENAI_CLIENT`` gives the two fixture
    harnesses today.

    ``with_options`` IS FORBIDDEN HERE for the reason evaluation.py records: it
    returns a NEW client object, so a recording or replaying proxy's
    ``__getattr__`` forwarding hands back an unwrapped client.
    """
    # BUILT FIRST, because building validates the provider configuration --
    # and a run that is about to be refused for naming an unsupported service
    # tier should not first be told about the seed.
    kwargs = build_bedrock_request(system_prompt, user_prompt)

    if not config.BEDROCK_SEND_SEED_IN_EXTRA_BODY:
        _warn_seed_once()
    # AFTER THE BUILD, on the seed's argument one line up: building validates
    # the provider configuration, and a run about to be refused for naming an
    # unsupported service tier should not first be told about a temperature.
    _warn_temperature_once()

    try:
        raw = deps.get_bedrock_client().responses.create(**kwargs)
    except Exception as exc:
        log.error("Stage 5 Bedrock request failed",
                  stage=5, status="error",
                  event="bedrock_request_failed",
                  reason=classify_error(exc),
                  error_type=type(exc).__name__,
                  error_message=str(exc),
                  provider=config.MATCHING_PROVIDER,
                  endpoint=config.get_bedrock_base_url())
        raise

    return translate_response(raw)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug 21 2026

@author: ramyalsaffar
"""
