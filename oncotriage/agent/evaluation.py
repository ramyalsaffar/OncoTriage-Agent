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

import contextvars
import html
import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, FrozenSet, List, Optional, Tuple

from oncotriage import config
from oncotriage.agent import bedrock_adapter
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
    MATCHING_CALL_MODE_PER_TRIAL,
    MATCHING_INPUT_PACKING_ENABLED,
    MATCHING_INPUT_TOKEN_BUDGET,
    MATCHING_MAX_INPUT_PACKED_CHUNKS,
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


class _SilentLog:
    """A logger-shaped sink that emits nothing, for a render nobody sends.

    WHY A SINK AND NOT SIX ``if log_events`` GUARDS. ``_build_trials_text``
    emits five distinct events from six call sites, and the requirement on the
    measurement path is that they are suppressed UNIFORMLY -- a reader who saw
    a fence warning but no decode event from the same phantom render would be
    told that third-party text was rewritten on its way to a judge that was
    never asked. Six independent guards make that a property somebody has to
    remember at each site and at every site added later; rebinding the name the
    function logs through makes it a property of the function. There is no
    ``log`` reference left inside the render for a new call site to reach, and
    ``tests/test_agent_render_event_suppression.py`` asserts that by AST, so a
    site added tomorrow is suppressed by construction or fails the scan.

    IT IS NOT A LEVEL AND NOT A HANDLER. ``ONCOTRIAGE_LOG_LEVEL`` cannot reach
    this and neither can a logging configuration: the question "is this render
    going to be sent" is a fact about the CALLER, not about how verbose the
    operator wants to be, and a suppression an operator could switch on would
    reintroduce exactly the misattribution it exists to remove.

    EVERY PUBLIC METHOD OF ``StructuredLogger`` IS IMPLEMENTED, including the
    two the render does not currently call. A sink that is substitutable only
    for the methods in use today fails with ``AttributeError`` the first time
    somebody logs at a new level -- inside a render, where the exception would
    surface as a Stage 5 failure rather than as a missing log line. The parity
    is asserted rather than asserted-in-prose; see the same test.
    """

    __slots__ = ()

    def debug(self, message, **fields):
        """Discard."""

    def info(self, message, **fields):
        """Discard."""

    def warning(self, message, **fields):
        """Discard."""

    def error(self, message, **fields):
        """Discard."""

    def exception(self, message, **fields):
        """Discard."""

    # ``StructuredLogger.std`` is a property returning its ``logging.Logger``.
    # This is a plain class attribute rather than a property returning None,
    # which is indistinguishable at every call site (``sink.std`` is None
    # either way) and adds no DECORATED definition to the package. That last
    # part is not a style preference: ``tests/test_package_invariants.py``
    # section 2i pins the decorator inventory of the whole package by qualified
    # name, and a ``@property`` here would have made a logging change edit that
    # pin. Measured rather than guessed -- the property version failed 2i.
    std = None


_SILENT_LOG = _SilentLog()


# A MAXIMAL RUN of three or more angle brackets, which is what the user
# message's TRIAL_DATA fences are built from. Third-party text is rewritten
# through this before it is interpolated into a block; see
# _neutralize_fence_markers, which argues why the subject is the RUN and not
# the three-character substring. It is compiled once here rather than inside
# the function because _build_trials_text runs once per trial per render.
_FENCE_MARKER_RUN_RE = re.compile(r"<{3,}|>{3,}")


# ONE ESCAPE CHAIN AS THE REGISTRY ACTUALLY WRITES ONE, matched whole: an
# optional markdown backslash, then any number of "&amp;" wrappers, then one
# SEMICOLON-TERMINATED named or numeric character reference. Compiled here
# beside _FENCE_MARKER_RUN_RE and for the same reason -- _build_trials_text
# runs once per trial per render.
#
# Measured over every trial in the 2026-08-10 corpus, and the measurement is
# what fixes each choice below (09- Testing/Evaluation Runs/
# criteria_quality_census_20260814/): 579 occurrences across 197 trials in the
# two fields this stage renders, 140 of those trials putting one where a
# numeric COMPARATOR belongs. Stage 5 was shown "INR \&lt; 1.2 and platelet
# counts \&gt; 80,000/mm3" (NCT06923098) where the sponsor wrote "INR < 1.2 and
# platelet counts > 80,000/mm3" -- a threshold whose direction is spelled as an
# escaped entity. Nothing between the scrape and the prompt decoded it.
#
# THE SEMICOLON IS REQUIRED, AND THAT IS THE SAFETY PROPERTY RATHER THAN
# PEDANTRY. html.unescape implements the HTML5 rule that a named reference need
# not be terminated, so applied to a whole criteria string it rewrites
# "tumor &lt 2cm" to "tumor < 2cm", "grade &notin 3" to "grade ¬in 3" and
# "a &para b" to "a ¶ b" -- all three measured against the installed
# interpreter. Criteria text is prose and carries bare ampersands, so the
# whole-string form of this fix corrupts legitimate text. No string outside a
# match of this pattern is ever handed to html.unescape.
#
# THE BACKSLASH IS OPTIONAL AND THE CORPUS DOES NOT NEED IT TO BE. All 579
# measured occurrences carry exactly one backslash -- 579 of 579, and there is
# not one bare entity in either rendered field -- so making it optional is a
# provable no-op here rather than a guess: re-rendering all 14,324 trials with
# the backslash required and with it optional gives byte-identical output. It
# is optional so that the fix survives a future scrape that stops
# markdown-escaping, which is the only thing standing between this corpus and
# a bare "&lt;".
_ESCAPED_ENTITY_CHAIN_RE = re.compile(
    r"\\?&(?:amp;)*"
    r"(?:[A-Za-z][A-Za-z0-9]{1,31}|#[0-9]{1,7}|#[Xx][0-9A-Fa-f]{1,6});")

# Decode passes allowed WITHIN ONE MATCHED CHAIN. Measured passes to a fixed
# point, over the same 579 occurrences: 1 (468), 2 (73), 3 (32), 4 (4) and
# 11 (2) -- NCT02945579 stores
# "\&amp;amp;amp;amp;amp;amp;amp;amp;amp;amp;lt;" for a single "<". A cap of
# three, which the measured depth histogram was not yet available to rule out,
# would leave residue on six of them.
#
# Termination does not rest on this number: every pass that changes anything
# strictly shortens the string, since "&amp;" is five characters and "&" is
# one, so a chain of length n reaches a fixed point in at most n/4 passes and
# the loop cannot spin. The cap is a bound on pathological input, and 16 clears
# the measured maximum by five.
_ENTITY_DECODE_MAX_PASSES: int = 16

# A chain this function REFUSED to substitute, keyed by reason and raw chain.
# The text is then left EXACTLY as scraped: a partially decoded or damaged
# chain is a byte string no sponsor wrote, and it reads to everything
# downstream as though the decode had succeeded. Two reasons, both measured at
# zero on this corpus and both reachable by a future one:
#
#   pass_cap          -- still not at a fixed point at _ENTITY_DECODE_MAX_PASSES.
#   replacement_char  -- the reference names no character that belongs in
#                        criteria prose. html.unescape follows the HTML5 rule
#                        for an out-of-range, surrogate or zero code point and
#                        returns U+FFFD, so "\&#0;", "\&#x0;", "\&#55296;" and
#                        "\&#9999999;" all decode to the replacement character
#                        -- all four measured against the installed
#                        interpreter. Substituting would have this fix INJECT
#                        the census's own replacement_char defect into text
#                        that did not have one. A C0 control reference is
#                        refused by the same rule: "\&#8;" decodes to the empty
#                        string, and silently deleting a span of scraped text
#                        is the worse half of the same failure.
#
# ONLY the refusals are counted, on M_CATEGORY_UNREADABLE's footing. A
# successful decode is not a degradation -- it is this function working -- and
# it is already reported per render by trial_escaped_entity_decoded, so
# counting it here as well would put one entry per affected trial per render
# into a counter whose whole purpose is to make the rare failure visible.
ESCAPED_ENTITY_DECODE_UNRESOLVED: Dict[str, int] = Counter()

# The two reasons above as named constants, because they are counter keys a
# reader will filter on and a literal typed twice is a literal that drifts.
ENTITY_REFUSED_PASS_CAP: str = "pass_cap"
ENTITY_REFUSED_REPLACEMENT_CHAR: str = "replacement_char"

# Matching _M_KEY_MAX_LEN's reasoning: long enough to see the shape of a real
# chain (the deepest measured is 45 characters), short enough that a
# pathological field cannot grow the key without bound.
_ENTITY_KEY_MAX_LEN: int = 80


# ---------------------------------------------------------------------------
# The registry's markdown escaping
# ---------------------------------------------------------------------------
#
# THE SECOND HALF OF THE SAME FINDING, and _decode_escaped_entities' docstring
# scoped it out by name. ClinicalTrials.gov markdown-escapes the punctuation in
# criteria text, so the judge reads "INR \> 1.2" where the sponsor wrote
# "INR > 1.2" -- a threshold whose DIRECTION is spelled by the escaped
# character. Measured over every trial in the 2026-08-14 corpus
# (09- Testing/Evaluation Runs/markdown_escape_census_20260814/): 69,397
# punctuation escapes across 10,108 trials (70.57%), of which 41,657 in 9,044
# trials (63.14%) are a comparator.
#
# THE SET IS THE REGISTRY'S RULE, NOT THIS WEEK'S SAMPLE. Fifteen distinct
# successors occur -- "# & ) * + - . < > [ ] ^ _ | ~" -- and every one of them
# is inside CommonMark's escapable set while ZERO successors fall outside it.
# CommonMark 0.31 section 2.4 is exactly "a backslash before ASCII punctuation
# escapes it, a backslash before anything else is a literal backslash", which
# is the property that makes removal provable rather than probable: the proof
# is the rule, not the sample. Taking the whole set rather than the observed
# fifteen is measured to be a NO-OP -- re-rendering all 28,399 render-path
# fields both ways gives byte-identical output on every one -- and it is what
# lets a sponsor who writes "!" at a line start be read correctly next week.
# Same argument _ESCAPED_ENTITY_CHAIN_RE makes for its optional backslash.
#
# THREE CHARACTERS ARE THEN REMOVED FROM IT, and each removal is a refusal with
# a name rather than a gap:
#
#   "\\"      class (c) below. The backslash is CONTENT, not markup.
#   ";" "#"   the two characters a CHARACTER REFERENCE is built from. This
#             decoder runs BEFORE _decode_escaped_entities, so a decoder able
#             to supply a ";" could manufacture a chain for it out of text the
#             sponsor wrote literally: "\\&gt\\;" is a sponsor who typed
#             "&gt;", and stripping both escapes turns it into a reference that
#             then decodes to ">". Measured against the installed interpreter.
#             Cost: one occurrence of "\\#" in the whole corpus and zero of
#             "\\;". The "&" it CAN still supply is safe, and that is a
#             property rather than luck -- the chain regex's backslash is
#             optional, so anything matching after "\\&" becomes "&" was
#             already matching before.
_MARKDOWN_ASCII_PUNCTUATION: str = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
_MARKDOWN_REFERENCE_SYNTAX_CHARS: str = "#;"
_MARKDOWN_ESCAPE_DECODE_SET: FrozenSet[str] = frozenset(
    _MARKDOWN_ASCII_PUNCTUATION) - frozenset(
        _MARKDOWN_REFERENCE_SYNTAX_CHARS) - {"\\"}

# A chain this decoder REFUSED to touch, keyed by reason and raw pair, on
# ESCAPED_ENTITY_DECODE_UNRESOLVED's footing and for the same reason: the text
# went to the judge still carrying the artefact, which is the defect rather
# than the fix, and a reader has to be able to see it. Two reasons, both
# reachable and both measured non-zero on this corpus:
#
#   escaped_backslash  -- "\\\\", CommonMark's escape for a LITERAL backslash,
#                         and the only class in the render path where a
#                         backslash is content. 14 occurrences in 11 trials:
#                         the sponsor wrote "CLL\\SLL", "CRi/CRh\\^1",
#                         "\\[200 IU/mL]". It is emitted VERBATIM rather than
#                         collapsed to one backslash, and idempotence is why --
#                         see _decode_markdown_escapes.
#   reference_syntax   -- "\\#" or "\\;", refused so this decoder cannot build
#                         a character reference for the one that runs after it.
#                         1 occurrence ("\\# CLN1114", NCT06940518).
#
# A backslash before a character OUTSIDE the decode set and outside both
# reasons -- a letter, a digit, a space, end of field -- is NOT counted. Under
# CommonMark that is a literal backslash, so leaving it is a determinate answer
# rather than a degradation, on the footing M_CATEGORY_UNREADABLE argues for
# cM0 and _decode_escaped_entities argues for a match that is not a reference.
# Measured zero in the render path, in any case: class (d) of the census is
# empty, so every backslash in either field is an escape or the escaped member
# of a pair.
MARKDOWN_ESCAPE_DECODE_UNRESOLVED: Dict[str, int] = Counter()

MARKDOWN_REFUSED_ESCAPED_BACKSLASH: str = "escaped_backslash"
MARKDOWN_REFUSED_REFERENCE_SYNTAX: str = "reference_syntax"

# The refusal key carries the pair and a little of what follows it, which is
# enough to recognise the shape without letting a pathological field grow the
# key. Shorter than _ENTITY_KEY_MAX_LEN because the subject is two characters
# rather than a chain of up to 45.
_MARKDOWN_KEY_MAX_LEN: int = 24


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
        # WHICH CONSTANT TO EDIT DEPENDS ON THE PROVIDER, and naming the wrong
        # one is a wrong instruction in the message that stops a run. Under
        # Bedrock the string that was SENT comes from BEDROCK_MATCHING_MODEL,
        # and editing MATCHING_MODEL there would change the priced identity
        # while leaving the wire id untouched -- so the run would raise again,
        # identically, having also broken the pricing key.
        self.provider = config.MATCHING_PROVIDER
        constant = ("BEDROCK_MATCHING_MODEL"
                    if self.provider == config.MATCHING_PROVIDER_BEDROCK
                    else "MATCHING_MODEL")
        super().__init__(
            f"Stage 5 requested model {requested!r} but the API answered as "
            f"{returned!r} (provider: {self.provider}). The configured model "
            f"resolved to something other "
            f"than what was configured -- almost certainly an alias that now "
            f"points at a dated snapshot. Every verdict from this point on "
            f"would come from a different judge than the rows already in "
            f"inferences.db, so the run is stopped rather than continued and "
            f"logged. After reviewing what changed, set {constant} in "
            f"'oncotriage/config.py' to {returned!r}, add it to PRICING_CONFIG if it "
            f"is not there, and re-baseline; do not accept it silently."
        )
# MATCHING_MAX_TOKENS and MATCHING_SEED are in oncotriage/config.py, together with
# the truncation thresholds calibrated against the first of them.


def call_matching_model(system_prompt: str, user_prompt: str, *,
                        prompt_cache_key: Optional[str] = None):
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
    #
    # ── THE PROVIDER DISPATCH, AND WHY IT IS HERE AND NOT LOWER ───────────
    #
    # This function is the single point where the pipeline talks to the
    # matching model -- which is what lets the fixture harnesses capture and
    # replay one exchange -- so it is also the only place a second provider can
    # be introduced without giving those harnesses a second seam to know about.
    #
    # WITH THE FLAG AT ITS DEFAULT THIS COSTS TWO STRING COMPARISONS AND
    # CHANGES NOTHING. The `return` below is byte-identical to the one that
    # stood here before the adapter existed: same client, same kwargs, same
    # order, same object. That is what the twelve characterization fixtures
    # replaying clean WITHOUT recapture measures, and what
    # tests/test_agent_bedrock_adapter.py section 1 asserts structurally.
    #
    # AN UNRECOGNISED PROVIDER RAISES rather than falling through to OpenAI.
    # "not bedrock, therefore openai" would let a typo in MATCHING_PROVIDER
    # keep billing the incumbent while an operator believed they had migrated,
    # which is the silent-wrong-provider failure the closed vocabulary in
    # config exists to prevent.
    # ── THE CACHE-ROUTING HINT, AND WHY IT IS AN EXPANSION ────────────────
    #
    # `prompt_cache_key` does not enable caching -- automatic prefix caching is
    # on by default -- it asks the provider to route requests carrying the same
    # key to the same machine. That is load-bearing for per-trial mode and for
    # nothing else: N requests of one patient go out AT ONCE behind the warmup,
    # and without a routing hint they can land on N machines of which only one
    # holds the warm prefix.
    #
    # PASSED THROUGH `**_extra_kwargs` RATHER THAN AS A NAMED KEYWORD, and the
    # reason is the OFF arm. `openai.NOT_GIVEN` would be equivalent ON THE
    # WIRE, but oncotriage/fixtures/capture.py records this call's kwargs dict
    # verbatim and oncotriage/fixtures/replay.py looks a recording up by a
    # digest OF THAT DICT -- so a key that is always present, whatever its
    # value, changes the digest of every grouped-mode request and costs a
    # re-capture of all twelve characterization fixtures at live model prices.
    # An empty expansion adds nothing to the dict the SDK is handed, so grouped
    # mode's request is byte-identical to the one that shipped.
    #
    # ON BEDROCK THE ADAPTER OWNS IT and this argument is deliberately not
    # forwarded: `build_bedrock_request` already sends
    # config.BEDROCK_PROMPT_CACHE_KEY, that endpoint's caching is governed by
    # BEDROCK_PROMPT_CACHE_MODE as well, and wiring a second source of the same
    # field from here would give one request two owners. Per-trial mode against
    # Bedrock is an unbuilt branch either way -- see the warmup below.
    _extra_kwargs = {}
    if prompt_cache_key is not None:
        _extra_kwargs["prompt_cache_key"] = prompt_cache_key

    _provider = config.MATCHING_PROVIDER
    if _provider == config.MATCHING_PROVIDER_BEDROCK:
        return bedrock_adapter.call_matching_model_bedrock(
            system_prompt, user_prompt)
    if _provider != config.MATCHING_PROVIDER_OPENAI:
        config.validate_matching_provider_config()   # raises, naming the constant

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
        **_extra_kwargs,
    )


# ---------------------------------------------------------------------------
# The per-trial cache warmup
# ---------------------------------------------------------------------------
#
# CACHE-OR-NOTHING. Per-trial mode issues one request per trial and is only
# affordable because all of them share one byte-identical system message --
# instructions plus the whole patient record -- which the provider bills at the
# cached rate once it has SEEN it. The mode originally made the first REAL
# trial call the cache writer and held the rest behind it; that entangled one
# trial's failure semantics with the scheduling of every other trial, and left
# a cost leak nothing recorded: a first call that exhausted its transport
# retries sent the remaining N-1 against a cache nothing had written, at full
# input price, and the run reported a perfectly ordinary patient.
#
# So the cache is written by a request that is NOT a trial: a warmup carrying
# the identical system message and the smallest user message and output budget
# the provider permits. No trial call is issued until it has returned. If it
# cannot be established the patient fails cleanly through the existing zero-
# success floor, so the batch checkpoint resumes it rather than recording a
# cohort of trials nobody judged.


class PerTrialProviderUnsupportedError(RuntimeError):
    """Per-trial mode is on and this provider has no cache warmup.

    A ``RuntimeError`` subclass on this file's standing footing: a stray
    ``except ValueError`` around a Stage 5 call must not be able to eat it.

    IT NAMES BOTH CONSTANTS, because either one is a legitimate fix and the
    operator is the only one who can say which they meant.
    """

    def __init__(self, provider):
        super().__init__(
            "per-trial mode's cache warmup is not built for "
            f"MATCHING_PROVIDER={provider!r}. Set "
            "MATCHING_PER_TRIAL_CALLS_ENABLED = False to run this provider in "
            "grouped mode, which is fully supported, or set MATCHING_PROVIDER "
            f"to {config.MATCHING_PROVIDER_OPENAI!r}.")
        self.provider = provider


def assert_per_trial_provider_supported() -> None:
    """Refuse per-trial mode on a provider whose warmup is not built.

    ONE OWNER, TWO CALL SITES, and the second is not redundant.
    ``node_llm_classifier_evaluation`` calls this BEFORE anything is rendered
    or spent, because a refusal there costs nothing and names the constant --
    the footing ``PerTrialParallelismError`` already stands on. Without it the
    warmup's own refusal would be caught by the dispatch's ``except``,
    classified as a transport failure and retried
    ``MAX_LLM_CLASSIFIER_RETRIES`` times, so a configuration defect would
    arrive as three identical failed patients rather than as one named error.
    ``call_matching_model_warmup`` calls it too, because it is a public
    function and a direct caller must not reach the OpenAI client under a
    provider it did not select.
    """
    _provider = config.MATCHING_PROVIDER
    if _provider != config.MATCHING_PROVIDER_OPENAI:
        raise PerTrialProviderUnsupportedError(_provider)


class _WarmupUserMessageError(RuntimeError):
    """The warmup user message is unusable. Should be unreachable.

    ``oncotriage/config.py`` refuses an empty
    ``MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE`` at import, so this fires only
    when something rebound the constant WITHIN a process -- a test, a REPL, a
    probe. It raises rather than substituting a default because a warmup that
    quietly sent something other than the configured message would write a
    cache under a request nobody can reproduce.

    A ``RuntimeError`` subclass on this file's standing footing: a stray
    ``except ValueError`` around a Stage 5 call must not be able to eat it.
    """


# ── Which failures mean "this request SHAPE is refused" ───────────────────
#
# A closed two-member vocabulary. Both mean the warmup can never succeed under
# this configuration however many times it is retried, so retrying is spending
# money to collect the same 400; and both are recoverable, because the shipped
# one-then-rest schedule needs neither the minimal output budget nor the
# routing hint. Everything else -- a timeout, a 429, a 500, an auth failure --
# is a TRANSPORT failure that a retry may well fix, and is deliberately NOT in
# here: those fail the patient, which is what makes the resume correct.
WARMUP_REJECTED_MINIMAL_OUTPUT = "minimal_output_rejected"
WARMUP_REJECTED_CACHE_KEY = "prompt_cache_key_rejected"
WARMUP_REJECTIONS = (WARMUP_REJECTED_MINIMAL_OUTPUT, WARMUP_REJECTED_CACHE_KEY)

# The parameter names a 400 must NAME for this to be read as a refusal of the
# request shape rather than as a failure of the request. Both spellings of the
# output ceiling are here because the Bedrock adapter's Responses request calls
# it `max_output_tokens` while the Chat Completions request calls it
# `max_completion_tokens`, and the bare `max_tokens` because a provider
# rejecting the value often names the legacy field in its message.
_WARMUP_OUTPUT_PARAM_NAMES = ("max_completion_tokens", "max_output_tokens",
                              "max_tokens")
_WARMUP_CACHE_PARAM_NAME = "prompt_cache_key"


def _http_status_of(exc: BaseException) -> Optional[int]:
    """The HTTP status behind an SDK exception, or None.

    READ FROM TWO PLACES because the OpenAI SDK carries it on the exception
    (``APIStatusError.status_code``) while several other clients -- and the
    stand-ins this project's tests install -- carry it on a ``response``
    object. Neither is asserted to exist: this runs on a failure path and must
    not raise a second, unrelated exception while classifying the first.
    """
    for _candidate in (getattr(exc, "status_code", None),
                       getattr(getattr(exc, "response", None), "status_code",
                               None)):
        if isinstance(_candidate, int) and not isinstance(_candidate, bool):
            return _candidate
    return None


def classify_warmup_rejection(exc: BaseException) -> Optional[str]:
    """Is this the provider REFUSING the warmup's request shape?

    Returns one of ``WARMUP_REJECTIONS``, or None for everything else.

    DETECTED, NOT ASSUMED, which is the whole reason this function exists.
    ``MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS`` is 1 because that is the
    smallest answer a provider can be asked for, and a reasoning model that
    bills its reasoning against the same ceiling may refuse a value that small
    outright. Nobody here can read that provider's validation rules, and a
    design that assumed either answer would be wrong on half the providers.

    TWO CONDITIONS, BOTH REQUIRED, and the conjunction is the point. A 400
    alone is not enough: a context-length overflow and an invalid schema are
    also 400s, they are statements about the CONTENT of this patient's request
    rather than about the warmup's shape, and they will fail every trial call
    too -- so falling back for them would replace a clean patient failure with
    fifteen identical ones. The message must NAME the parameter. And the
    parameter name alone is not enough either: a 500 whose body happens to
    quote the request is not a refusal.

    IT NEVER RAISES. It runs while another exception is being handled, and an
    exception raised here would replace a named transport failure with an
    unrelated AttributeError.
    """
    if _http_status_of(exc) != 400:
        return None
    _message = str(exc).lower()
    if _WARMUP_CACHE_PARAM_NAME in _message:
        # ASKED FIRST, because a request carrying both an unrecognised routing
        # hint and a minimal ceiling is refused for whichever the provider
        # validates first -- and only this branch tells the caller to stop
        # sending the hint. Reading it as a ceiling refusal would leave the key
        # attached to the fallback's calls, which would then be refused too,
        # and the patient would fail for a reason one flag would have fixed.
        return WARMUP_REJECTED_CACHE_KEY
    if any(_name in _message for _name in _WARMUP_OUTPUT_PARAM_NAMES):
        return WARMUP_REJECTED_MINIMAL_OUTPUT
    return None


def call_matching_model_warmup(system_prompt: str, *,
                               prompt_cache_key: Optional[str] = None):
    """Write the shared prefix into the provider's cache. Evaluates nothing.

    The response is NEVER PARSED. Two things are read off it and nothing else:
    the usage block, so the warmup's own tokens are billed honestly rather than
    hidden, and the answering-model echo, so a mismatched judge fails the
    patient for the price of one one-token request instead of after fifteen
    real ones.

    WHAT IS IDENTICAL TO A TRIAL CALL, AND IT IS THE PART THAT MATTERS. The
    system message, byte for byte -- that IS the shared prefix, and prefix
    caching matches on the leading token sequence of the messages. Everything
    that differs sits AFTER it: the user message, and the output ceiling.

    ``response_format`` IS DELIBERATELY NOT SENT, and it is the one asymmetry
    worth arguing. Nothing parses this answer, so a strict schema buys nothing;
    a json_schema demand against a one-token ceiling is a combination a
    provider may well refuse, and every constraint dropped here is one fewer
    reason for the warmup to be rejected and the whole schedule to degrade. It
    costs nothing on the axis that matters because a response schema adds no
    tokens to the prompt -- the cached prefix is the MESSAGE prefix.
      VERIFY AT GO-LIVE: if this provider's cache key turns out to include the
      response format, the warmup would warm a prefix the trial calls do not
      share and every wave call would report cached_tokens = 0. That figure is
      recorded per call in ``llm_classifier_call_details``, so the measurement
      that settles it is already in the record; send ``response_format`` here
      too if it says so.

    ``reasoning_effort`` IS SENT, at whatever ``MATCHING_REASONING_EFFORT``
    holds, because a warmup that asked for a different effort than the wave is
    a different request to the provider's router. THE CONSEQUENCE IS STATED:
    reasoning tokens bill against the same ceiling this call sets to 1, so
    raising that constant may require raising
    ``MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS`` with it. If it does not,
    the provider's 400 is classified by ``classify_warmup_rejection`` and the
    patient degrades to the one-then-rest schedule with a named counter rather
    than failing.

    THE CONSTANTS ARE READ THROUGH ``config``, NOT THROUGH BOUND NAMES, on
    ``matching_call_mode()``'s footing: a constant that can move WITHIN a
    process must not be reached through a from-import, or a probe that sets it
    on the module moves nothing.

    Raises:
        Whatever the client raises. The caller owns error handling, exactly as
        it does for ``call_matching_model``.
    """
    _user = config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE
    if not _user:
        raise _WarmupUserMessageError(
            "MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE must be a non-empty "
            f"string; it is {_user!r}")

    # ── THE ADAPTER SEAM, LEFT EXPLICIT AND UNBUILT ───────────────────────
    #
    # NOT a silent fall-through to the OpenAI client, which would send this
    # patient's record to the incumbent provider while the operator believed
    # they had migrated -- the failure `call_matching_model`'s own
    # unrecognised-provider branch exists to refuse.
    #
    # The Responses API's warmup is not this request with two fields renamed.
    # Amazon Bedrock serves OpenAI models on that surface and owns its own
    # caching controls (BEDROCK_PROMPT_CACHE_MODE, BEDROCK_PROMPT_CACHE_KEY),
    # and a future Anthropic branch warms its cache the other documented way
    # entirely: a placeholder user message with an explicit `cache_control`
    # breakpoint on the system block, because that provider's caching is
    # explicit rather than automatic. Building either from documentation alone
    # is how a mode that has never run acquires a second untested path; both
    # belong to the pass that runs the go-live probe against them.
    assert_per_trial_provider_supported()

    _extra_kwargs = {}
    if prompt_cache_key is not None:
        _extra_kwargs["prompt_cache_key"] = prompt_cache_key

    return deps.get_openai_client().chat.completions.create(
        model=MATCHING_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _user},
        ],
        max_completion_tokens=config.MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS,
        reasoning_effort=MATCHING_REASONING_EFFORT,
        seed=MATCHING_SEED,
        timeout=config.get_matching_request_timeout(),
        **_extra_kwargs,
    )


def per_trial_prompt_cache_key(system_prompt_sha256: str) -> Optional[str]:
    """The routing hint for one patient's warmup and wave, or None.

    DERIVED FROM THE PREFIX ITSELF rather than from a patient id or a run id,
    and that is what makes it correct by construction: the key's whole job is
    to route requests that SHARE A PREFIX to one machine, so two requests get
    the same key exactly when they have the same prefix to share. A patient id
    would collide across a prompt-version bump -- the same patient before and
    after a PROMPT_VERSION change would ask to be routed together while sharing
    nothing -- and a random per-run key would separate a resumed campaign's
    requests from the ones it is resuming.

    NAMESPACED, because this key is sent to a provider account this project
    does not own exclusively. An unprefixed hex digest is a value another
    workload could plausibly send too, and two unrelated workloads asking to be
    routed together is the one way a routing hint can make cache behaviour
    WORSE than sending nothing.

    None WHEN THE HINT IS SWITCHED OFF, so the caller passes it straight
    through and ``call_matching_model``'s expansion stays empty.
    """
    if not config.MATCHING_PER_TRIAL_PROMPT_CACHE_KEY_ENABLED:
        return None
    return f"oncotriage-stage5-{system_prompt_sha256}"


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
NOT_EVALUABLE_CONFLICTING_DUPLICATES = "conflicting_duplicate_answers"
# ^ the model returned SEVERAL entries for this trial and they did not agree on
#   the verdict. Distinct from every reason above: the model answered, more than
#   once, and contradicted itself. See _collapse_duplicate_entries for why that
#   is a non-evaluation rather than a choice between the answers.
NOT_EVALUABLE_CALL_FAILED = "per_trial_call_failed"
# ^ PER-TRIAL MODE ONLY. The request for this trial raised -- a timeout, a
#   connection reset, a 5xx that survived the SDK's own retry -- so no response
#   for it was ever obtained.
#
#   IT IS NOT NOT_EVALUABLE_MODEL_OMITTED AND THE DIFFERENCE IS THE POINT. That
#   reason means a call SUCCEEDED, parsed, and simply carried no entry for this
#   trial, which is a statement about the model's answer. This one means there
#   was no answer to read. Reusing the omission reason would attribute a
#   transport failure to the judge, and the two have different owners and
#   different fixes.
#
#   IT EXISTS ONLY BECAUSE ONE TRIAL PER CALL MAKES ISOLATION POSSIBLE. In
#   grouped mode a raised call is the whole batch, there is nothing to isolate
#   it from, and the node returns the API-error result so the retry budget sees
#   it -- unchanged by this pass. See the except branch in the send loop.
#
#   THERE IS NO SIBLING CONSTANT FOR "EVERY CALL FAILED", and its absence is a
#   decision. When per-trial mode loses ALL of its calls the node does NOT
#   record fifteen not-evaluable trials and report success -- that would turn a
#   total outage into a patient with no matches and no error. It returns the
#   API-error result instead, so MAX_LLM_CLASSIFIER_RETRIES covers the outage
#   exactly as it does in grouped mode, and no trial is stamped at all.

_NOT_EVALUABLE_REASONS = (
    NOT_EVALUABLE_TRUNCATION_FLOOR,
    NOT_EVALUABLE_SPLIT_BUDGET,
    NOT_EVALUABLE_MODEL_OMITTED,
    NOT_EVALUABLE_CONFLICTING_DUPLICATES,
    NOT_EVALUABLE_CALL_FAILED,
)


# ---------------------------------------------------------------------------
# Per-trial call failures
# ---------------------------------------------------------------------------

PER_TRIAL_CALL_FAILURES = Counter()
"""Per-trial requests that raised, keyed by exception type name.

MODULE-LEVEL, NOT A KEY IN THE STAGE 5 RESULT, on AGE_PARSE_FAILURES' footing:
the twelve characterization fixtures diff the Stage 5 result field by field, so
a new key there costs a re-capture at live model prices for a number no stage
reads. Registered in ``oncotriage/degradation.py`` so it reaches the run-end
report, which is what makes a partial loss visible without a query.

INCREMENTED ON THE NODE THREAD ONLY. The requests are issued concurrently, but
``Counter[k] += 1`` is a load-add-store that the interpreter may switch threads
inside, so two workers incrementing the same key can lose one of them --
``oncotriage/degradation.py`` records the same hazard from the reading side.
Every increment here happens in the send loop, after the merge, on the thread
that owns the node.

STAYS AT ZERO WHILE MATCHING_PER_TRIAL_CALLS_ENABLED IS False, because nothing
in that configuration can reach the branch that increments it. Same shape as
BEDROCK_ADAPTER_DEGRADATIONS under MATCHING_PROVIDER "openai".
"""


PER_TRIAL_WARMUP_DEGRADATIONS = Counter()
"""The per-trial cache warmup did not do its job, keyed by what happened.

TWO KEY FAMILIES AND THEY MEAN OPPOSITE THINGS FOR THE PATIENT:

  * ``minimal_output_rejected`` / ``prompt_cache_key_rejected`` -- the provider
    refused the warmup's request SHAPE. The patient COMPLETED, on the retired
    one-then-rest schedule, which needs neither the minimal ceiling nor the
    routing hint. The remedy is a constant:
    MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS or
    MATCHING_PER_TRIAL_PROMPT_CACHE_KEY_ENABLED.
  * ``failed:{ExceptionType}`` -- the warmup could not be established at all
    and NO trial call was issued. The patient FAILED, deliberately, so that
    MAX_LLM_CLASSIFIER_RETRIES sees it and the batch checkpoint resumes it.

MODULE-LEVEL, NOT A KEY IN THE STAGE 5 RESULT, on PER_TRIAL_CALL_FAILURES'
footing: the twelve characterization fixtures diff that dict field by field, so
a new key there costs a re-capture at live model prices for a number no stage
reads. Registered in ``oncotriage/degradation.py`` so it reaches the run-end
report.

INCREMENTED ON THE NODE THREAD ONLY, and here that is true by construction
rather than by discipline: the warmup is awaited before any executor exists.

STAYS AT ZERO WHILE MATCHING_PER_TRIAL_CALLS_ENABLED IS False, because nothing
in that configuration reaches the branch that increments it.
"""

# The prefix under which a warmup TRANSPORT failure is counted, as opposed to a
# refusal of the request shape. Written once here rather than at the increment
# so the reader of the counter and the writer of it cannot drift.
WARMUP_FAILURE_KEY_PREFIX = "failed:"


class PerTrialParallelismError(RuntimeError):
    """``MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS`` is not a usable bound.

    A ``RuntimeError`` SUBCLASS AND DELIBERATELY NOT A ``ValueError``, on the
    footing ``UnknownModelPricingError``, ``PackingBlockMismatchError`` and
    ``CrossEncoderLimitMismatchError`` already argue in this project: a stray
    ``except ValueError`` around a Stage 5 call must not be able to eat it.

    IT RAISES RATHER THAN CLAMPING. 0 and -1 are each equally readable as "no
    parallelism, run sequentially" and as "the operator meant something else
    and typed it wrong", and clamping to 1 would silently run a whole campaign
    at a concurrency nobody chose. 1 IS legal and IS the sequential setting, so
    there is a spelling for that intent and this is not it. It fires before the
    first request of the patient, where it costs nothing.
    """

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
# Entries for a trial that was never sent
# ---------------------------------------------------------------------------
#
# THE RECONCILIATION BLOCK AT THE END OF THIS NODE ANSWERS ONE DIRECTION OF THIS
# QUESTION AND THIS ANSWERS THE OTHER. That block asks "was every trial we SENT
# accounted for"; nothing asked "is every trial that came BACK one we sent". The
# two are one problem, because the usual shape of the fault is a substitution:
# the model answers about NCT99999999, which displaces a real candidate, and the
# real candidate is then missing -- so the reconciliation records the omission
# and the fabricated verdict flows on beside it, enriched, scored, normalized,
# ranked, returned to the caller and written to trial_matches as an evaluation
# of a trial this patient was never a candidate for.
#
# A well-formed entry is indistinguishable from a real one by inspection: it
# carries an NCT-shaped id, criteria, a verdict and an explanation. The ONLY
# thing that separates it from a genuine verdict is the candidate set, which is
# known here and nowhere downstream.
#
# So the entry is DROPPED -- before enrichment, before normalization, before
# scoring -- and never becomes a verdict of any kind. Nothing is repaired: the
# id names no trial in this run, so there is nothing to attribute it to, and the
# trial it displaced is picked up BY NCT ID by the reconciliation below. That
# handoff is deliberately not duplicated here.
#
# THE COMPARISON IS AGAINST THE CHUNK, NOT THE NODE. Each call answers exactly
# the trials in its own chunk; an id belonging to a different chunk of the same
# split batch is an answer to a question that call was not asked, and treating
# the union as the sent set would accept it.
#
# BUT THE TWO CAUSES OF A DROP ARE DIFFERENT DISEASES AND ARE COUNTED APART.
#
#   CROSS-CHUNK -- the id is in the node's full sent set, just not this chunk's.
#       The model answered about the whole batch on every call. Nothing was
#       invented and nothing is lost: the id's OWN chunk answers it, or the
#       reconciliation records it as omitted. It costs the patient nothing and
#       it is a fact about how the provider handled a split request.
#   FABRICATED -- the id is in no sent set at all (or is not a string). The
#       model produced a verdict about a trial that does not exist in this run.
#       This is the clinical fault, and it is the ONLY one written to
#       inferences.hallucinated_trials, whose own definition is "trials never
#       in the candidate set sent to it".
#
# One number for both would have made an unsplit run's fabrication rate
# incomparable with a split run's, and would have put a provider quirk into a
# column a reader treats as a hallucination rate.

# Longest fragment of a returned nct_id kept for the log line. An NCT id is 11
# characters; this leaves room for a recognisable variant of one and refuses a
# sentence written into the field. Separate from _MALFORMED_ENTRY_PREVIEW_LEN
# above because that caps a whole entry's repr and this caps one field.
_OUT_OF_SET_ID_PREVIEW_LEN = 24

# The value stamped onto every evaluation that survived the check, and written
# to trial_matches.hallucinated. 1 is unreachable BY CONSTRUCTION -- an
# out-of-set entry never becomes a row -- and that is the point: the column
# separates "checked, and this row was in the candidate set" from NULL, which
# means no check ran for this row at all.
HALLUCINATION_CHECKED_CLEAN = 0


def _out_of_set_label(raw_id) -> str:
    """A loggable name for an entry that was not asked about.

    THE ID IS THE DIAGNOSIS AND THE REST OF THE ENTRY IS NOT. A fabricated
    entry carries the model's criterion-level prose about this patient, so
    nothing but the id travels, and even the id is capped: the field is model
    output and is only an identifier by convention.

    A non-string id is reported by TYPE alone. Its content is not an identifier
    by any reading, so printing it would be printing model output for no
    diagnostic gain.
    """
    if not isinstance(raw_id, str):
        return f"<{type(raw_id).__name__}>"
    return raw_id[:_OUT_OF_SET_ID_PREVIEW_LEN] or "<empty>"


def _partition_out_of_set(
    objects: List[Dict], chunk_ids, batch_ids,
) -> Tuple[List[Dict], List[str], List[str]]:
    """Split one chunk's entries three ways: asked about, cross-chunk, invented.

    Returns ``(in_set, cross_chunk_labels, fabricated_labels)``. Nothing is
    mutated and nothing is coerced; the caller records the drops.

    ``chunk_ids`` is what THIS call asked about and decides what is kept.
    ``batch_ids`` is the whole node's candidate set and decides only how a drop
    is CLASSIFIED -- never whether it is dropped. See the block above for why
    the two causes are counted apart.

    ``isinstance(raw_id, str)`` is tested BEFORE either membership test and that
    is not defensiveness about a schema-constrained field: ``[] in {"a"}``
    raises TypeError on an unhashable value, so an nct_id the model emitted as a
    list or a dict would take the whole patient's run down inside the detector
    added to stop exactly that class of loss. A non-string id is also,
    unambiguously, not one of the ids that were sent, and it is counted as
    FABRICATED -- it names no trial anywhere in this run, which is what that
    bucket means.

    A missing nct_id is out of set for the same reason, and this is where such
    an entry now stops: before, it reached enrichment with ``""``, matched no
    trial, kept no title, and left the stage as a verdict about nothing.
    """
    in_set = []
    cross_chunk = []
    fabricated = []
    for entry in objects:
        raw_id = entry.get("nct_id")
        if not isinstance(raw_id, str):
            fabricated.append(_out_of_set_label(raw_id))
        elif raw_id in chunk_ids:
            in_set.append(entry)
        elif raw_id in batch_ids:
            cross_chunk.append(_out_of_set_label(raw_id))
        else:
            fabricated.append(_out_of_set_label(raw_id))
    return in_set, cross_chunk, fabricated


# ---------------------------------------------------------------------------
# The same trial answered more than once
# ---------------------------------------------------------------------------
#
# A model that returns two entries for one sent trial used to have BOTH kept:
# two verdicts for one trial, two trial_matches rows under one inference, and a
# candidates_evaluated that counted a trial twice. The reconciliation could not
# see it -- it asks whether each sent trial appears AT LEAST once -- and the
# out-of-set detector cannot either, because the id was genuinely sent.
#
# THE POLICY IS THE PROMPT'S OWN CONSERVATISM RULE (C5), APPLIED TO THE MODEL'S
# SELF-CONTRADICTION:
#
#   identical verdicts   -> keep the FIRST, drop the rest. The model said one
#       thing twice. Choosing the first is arbitrary only in the sense that any
#       of two equal answers is; it is deterministic, which is the property
#       this pipeline is built on.
#   conflicting verdicts -> ALL of them are replaced by one not_evaluable entry.
#       A judge that says "eligible" and "not_eligible" about the same trial in
#       one response has not evaluated it, and picking either answer would be
#       choosing a verdict the model itself contradicted. Not a rejection: that
#       would be the fabricated-rejection defect the verdict-normalization pass
#       removed, arriving by a different road.
#
# COMPARED ON THE NORMALIZED VERDICT, NOT THE RAW LABEL, and that decides one
# case deliberately. "Eligible" beside "eligible" is one answer typed twice, not
# a contradiction. Two DIFFERENT unreadable labels both normalize to None and
# are therefore treated as one unreadable answer -- which preserves the
# disqualification rule that already governs them: the first entry is kept and
# its criteria decide, so a stated "not_met" still produces a rejection rather
# than being deleted by a conflict verdict.
#
# CROSS-CHUNK DUPLICATES NEED NO HANDLING AND THAT IS A FACT ABOUT THE SPLIT,
# NOT AN OVERSIGHT. _split_in_half partitions the batch, so every sent id lives
# in exactly one chunk; an entry for that id arriving in any OTHER chunk's
# response is out of set for the call that produced it and has already been
# dropped above. Grouping therefore only ever has to be within a chunk.

DUPLICATE_CASE_IDENTICAL = "identical"
DUPLICATE_CASE_CONFLICTING = "conflicting"

# Closed, and a caller may branch on it exhaustively.
DUPLICATE_CASES = (DUPLICATE_CASE_IDENTICAL, DUPLICATE_CASE_CONFLICTING)


def _collapse_duplicate_entries(
    objects: List[Dict],
) -> Tuple[List[Dict], List[Dict]]:
    """Reduce each nct_id to at most one entry.

    Returns ``(kept, collapsed)``. ``kept`` holds the surviving entries in
    their original order, with at most one per nct_id and NONE at all for an id
    whose answers conflicted -- the caller replaces those with an unevaluable
    entry, on the _unevaluable_entry precedent, because building one here would
    mean this helper reaching for the trial metadata it has no business knowing
    about.

    ``collapsed`` is one record per DUPLICATED id: ``{"nct_id", "case",
    "count"}``, where count is how many entries that id arrived with.

    Every entry is assumed to carry a string nct_id: _partition_out_of_set runs
    first and drops everything else, which is what makes the grouping safe to
    key on.
    """
    order: List[str] = []
    grouped: Dict[str, List[Dict]] = {}
    for entry in objects:
        nct_id = entry.get("nct_id")
        if nct_id not in grouped:
            grouped[nct_id] = []
            order.append(nct_id)
        grouped[nct_id].append(entry)

    kept = []
    collapsed = []
    for nct_id in order:
        entries = grouped[nct_id]
        if len(entries) == 1:
            kept.append(entries[0])
            continue
        verdicts = {normalize_trial_verdict(e.get("eligible"))[0]
                    for e in entries}
        if len(verdicts) == 1:
            collapsed.append({"nct_id": nct_id,
                              "case": DUPLICATE_CASE_IDENTICAL,
                              "count": len(entries)})
            kept.append(entries[0])
        else:
            collapsed.append({"nct_id": nct_id,
                              "case": DUPLICATE_CASE_CONFLICTING,
                              "count": len(entries)})
    return kept, collapsed


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

# A REJECTION THAT ITS OWN CRITERIA ARRAYS DO NOT SUPPORT. The model wrote a
# readable "not_eligible" and then wrote no row carrying a disqualifying status
# -- no inclusion "not_met", no exclusion "violated" -- and no label remap
# removed one, so there was never a disqualifier in the answer at all.
#
# THE SAME ARGUMENT AS THE OUT-OF-VOCABULARY BRANCH BESIDE IT, with the remap
# taken out. There, the disqualifiers existed and Step 1 resolved them away;
# here the model simply never wrote one. Either way the stored rejection would
# rest on nothing a reader could quote, and a rejection is the most dangerous
# output this pipeline produces: a false "eligible" is checked by a clinician
# reading the criteria, while a false "not_eligible" silently removes a trial
# from a patient's list and nobody ever looks at it again. Measured on real
# evaluation runs, 6 of 54 rejections had this shape -- one of them citing a
# 1963 tubal ligation as its support for a hypothyroidism diagnosis.
#
# Not "eligible": promoting it would assert a match the model never made. Not
# left as "not_eligible": that is the fabrication. Not evaluated is what the
# answer actually supports, and the arrays are kept untouched beside it so the
# non-evidence is still there to read.
UNEVALUABLE_REJECTION_UNSUPPORTED = (
    "model rejection unsupported by its own criteria arrays")

# THE SIBLING BRANCH'S REASON, REWORDED, AND THE REWORDING IS THE FIX. It read
# "sole disqualifier was an out-of-vocabulary label" and was written on the
# strength of `remapped_here`, which is true when ANY row on the trial was
# remapped -- including a row that was never disqualifying. For that trial the
# sentence asserted a disqualifier that had never existed.
#
# DISCRIMINATION WAS CONSIDERED AND IS UNSOUND, which is why this is a
# rewording rather than a second branch. To keep the old sentence for the
# trials it is true of, something would have to decide whether a remapped row
# COULD have carried the disqualification, and the only evidence available is
# `original_status` -- a string that failed an exact-match vocabulary test.
# "not_met" and "violated" are recognisable; "Not Met", "NOT MET", "fails",
# "excluded" and every other free-written disqualifier are not, and
# `_normalize_arm` refuses to guess the model's intent for exactly that reason.
# A discriminator would therefore classify a real disqualifier as "no
# disqualifying remap" whenever the model varied its case -- and, once
# UNEVALUABLE_REJECTION_UNSUPPORTED carries a composed assessment, that
# misclassification would STATE that the model cited no disqualifying
# criterion when it had. A wrong reason is bad; a wrong reason that becomes
# stored clinical prose is the defect this whole mechanism exists to remove.
#
# So the reason asserts only what is known without guessing: labels were
# normalised, and afterwards no disqualifying row is left. It does not say the
# remapped row was the disqualifier, and it does not say there never was one.
#
# THIS BRANCH NO LONGER KEEPS THE MODEL'S DRAFT EITHER, and the paragraph that
# stood here said it did. The reason it gave was sound and its conclusion was
# not: keeping the draft avoided over-claiming, but the draft is a REJECTION --
# "Known disqualifier: ..." -- stored in the column a clinician reads beside a
# verdict of not_evaluable, which is the same self-contradicting row the case
# above exists to remove, reached by the other route. The trade-off was between
# over-claiming and contradicting; a text that does neither settles it, so this
# branch is marked with the reason below and composes
# ASSESSMENT_REMAP_NO_SURVIVOR_TEXT.
#
# WHAT THAT TEXT MAY NOT SAY is exactly what the paragraphs above establish, so
# it is written to the same limit as this constant: it must not claim the model
# cited no disqualifier (a row it wrote may have been one, spelled wrong), and
# it must not claim the remapped rows WERE the disqualifiers (`remapped_here`
# is true for a remap on any row -- and for a non-dict entry that was dropped
# rather than relabelled, so it may not even claim a label was rewritten). It
# says only that the model rejected the trial, that after label normalisation
# no disqualifying row is left to support the rejection, and that the verdict
# was therefore corrected. The two markers stay DISTINCT for the same reason
# the two reasons do: composing the stronger sentence over this population is
# the misclassification the paragraph above rules out.
UNEVALUABLE_REMAP_NO_SURVIVOR = (
    "no disqualifying row survived label normalisation")


# ---------------------------------------------------------------------------
# The provenance markers this node stamps onto what it corrected
# ---------------------------------------------------------------------------
#
# THREE CORRECTIONS ARE MADE IN THIS FILE AND UNTIL THIS PASS TWO OF THEM LEFT
# NO MACHINE-READABLE TRACE ANYWHERE A QUERY COULD REACH.
#
#   `not_evaluable_reason`  was already stamped on the ENTRY -- and dropped at
#                           the write, because the trial_matches INSERT names
#                           nineteen columns and none of them was it, and
#                           `criterion_details` json.dumps exactly "inclusion"
#                           and "exclusion". The field existed on the dict at
#                           the line that wrote the row.
#   `verdict_normalizations` was a local list read by ONE log line and then
#                           discarded. Not on the entry, not in the return, not
#                           a state channel.
#   `label_remaps`          was a local list whose LENGTH survives as
#                           `cross_vocab_remaps` -- a count of remap EVENTS for
#                           the whole run. Which trial each belonged to, how
#                           many TRIALS carried one, and what each row's status
#                           was BEFORE the rewrite were all lost; `_normalize_arm`
#                           rewrites `c["status"]` in place, so `criterion_details`
#                           records the corrected value and nothing else.
#
# The keys below close that. Each is stamped where the correction is DECIDED,
# never re-derived later, so the record and the behaviour cannot disagree.
#
# THE MODEL CANNOT FORGE ANY OF THEM. `oncotriage/agent/response_schema.py` sets
# `additionalProperties: False` with a complete `required` list at every level:
# TRIAL_FIELDS is (assessment, eligible, exclusion_criteria, inclusion_criteria,
# match_score, nct_id) and CRITERION_FIELDS is (criterion, patient_value,
# status). None of these names is in either, so an entry or a row carrying one
# was written here. tests/test_storage_provenance_persistence.py asserts that
# against the REAL schema rather than assuming it, the way the two existing
# markers (`not_evaluable_reason`, TEMPORAL_CONFLICT_FIELD) already are.

VERDICT_SOURCE_FIELD = "verdict_source"
"""The entry key carrying how Step 0 read this trial's model-written label.

One of ``VERDICT_SOURCES`` -- canonical | normalized | unrecognized -- on every
entry the MODEL returned, including the ones whose label was already canonical.

PRESENT-ON-EVERY-CHECKED-ENTRY, NOT PRESENT-ONLY-WHEN-IT-MOVED, and that is the
one place this differs from the sibling markers. ``canonical`` is a MEASUREMENT:
it says the normalizer read this label and found nothing to recover. Absence
says no normalizer ran for this entry, which is true of the four
``_unevaluable_entry`` CONSTRUCTS -- they are appended after this loop and never
carried a model-written label at all. That is exactly the argument
``trial_matches.hallucinated`` already makes for 0 against NULL, and it is why
this key is not simply omitted when the label was fine.
"""

VERDICT_ORIGINAL_LABEL_FIELD = "verdict_original_label"
"""``repr`` of the label the model wrote, capped -- absent when it was canonical.

``repr`` and not the value: the label is model output of unknown type and
unknown length, and ``""`` and ``None`` must not read alike. Capped at
``_MALFORMED_ENTRY_PREVIEW_LEN``, the same cap the audit list uses.

IT IS STORED IN THE DATABASE AND STILL KEPT OFF THE LOG, and the two are not in
conflict. ``verdict_normalizations``' own comment says the label TEXT stays out
of the RECORD because ``original_label`` is not on ``LOGGABLE_FIELDS`` -- that
is the structured LOG, which is shipped, indexed and retained. The database
already holds the entire model response verbatim in
``inferences.llm_classifier_raw_response`` and every criterion string in
``trial_matches.criterion_details``; a capped repr of one verdict label is
strictly inside what that table already carries, and it is the only thing that
makes the normalisation auditable per row.
"""

VERDICT_ORIGINAL_TYPE_FIELD = "verdict_original_type"
"""``type(raw).__name__`` of the label the model wrote -- absent when canonical.

The campaign question is "from what original types", so this is the column it
groups by. It is also the half that diagnoses the defect -- a bool where a
string was asked for, a null, a nested object -- and it carries no content.
"""

CRITERION_REMAPS_FIELD = "criterion_remaps"
"""How many remap EVENTS this trial's criteria produced. 0 is a measurement.

A COUNT AND NOT A FLAG. "at least one" is ``> 0``, so the flag is derivable from
the count and the count is not derivable from the flag; and the counts of a
run's rows sum to ``inferences.cross_vocab_remaps``, which is an invariant a
query can check. It costs nothing: it is ``len(label_remaps) - remaps_before``
at the line that already computes ``remapped_here`` from those two numbers.

EVENTS, NOT ROWS, and the difference is stated because it is observable. A
non-object criterion entry is DROPPED by ``_normalize_arm`` rather than
relabelled, so it contributes to this count and leaves no row in
``criterion_details`` -- meaning this number can exceed the number of stored
rows carrying ``LABEL_REMAP_FIELD``, and the difference IS the number dropped.

IT DOES NOT COUNT THE ABSENT-DATA VALIDATOR'S REWRITES. That pass also turns a
criterion status into ``not_evaluable``, and it is a different finding with its
own audit list (``absent_data_corrections``) and its own log event
(``absent_data_correction``). Folding the two together would make one column
mean two things and would break the sum against ``cross_vocab_remaps``.
"""

LABEL_REMAP_FIELD = "remapped_from_status"
"""The key added to a RELABELLED criterion row, carrying the status the model wrote.

ABSENT rather than empty on every other row -- ``TEMPORAL_CONFLICT_FIELD``'s
convention, and for its reason: a key that is always present would claim the
detector ran on this row, and nothing can promise that for a row written before
it existed. Consumers must read it with ``.get``.

ON THE ROW, NOT IN A SIDE LIST. ``_normalize_arm`` rewrites ``c["status"]`` in
place, so before this pass the stored row said ``not_evaluable`` and what it had
said was gone. The criterion row is the owner of everything about that
criterion; a parallel column carrying criterion text plus its old status would
be a SECOND store of the same clinical string, able to disagree with the first.

NOT WRITTEN ON A DROPPED ENTRY, because a dropped entry has no row to write it
on. See ``CRITERION_REMAPS_FIELD`` for how many that is.

THE VALUE IS THE ONE ``label_remaps`` RECORDS AS ``original_status``, taken from
the same expression at the same line, so the row and the audit list cannot
disagree. That means an empty string when the model wrote no ``status`` key at
all -- which the strict schema forbids and a non-structured response does not --
and it is stored as the empty string rather than as ``None`` for exactly that
reason: absence of the KEY means "not remapped", so absence could not also be
made to mean "remapped from nothing".
"""


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


def _decode_markdown_escapes(text: str) -> Tuple[str, int, int]:
    """Remove the backslashes ClinicalTrials.gov added to escape punctuation.

    THE SPONSOR'S WORDING IS RESTORED, NEVER REWRITTEN. Like its sibling
    _decode_escaped_entities this undoes an encoding the registry applied; it
    does not paraphrase, reflow, normalise or repair. The stored corpus, the
    Qdrant payload and split_inclusion_exclusion are all untouched -- the
    decode happens at RENDER, which matters more here than it did for the
    entities, because oncotriage/retrieval/indexer.py's _HEADING_LEAD_CHARS
    DEPENDS on these backslashes: the corpus holds "\\<Exclusion Criteria\\>",
    and a splitter reading unescaped text would stop finding those headings.
    Nothing in this module is on the index-time path.

    THE DECODE SET IS CLOSED AND IS A CHARACTER SET, not a pattern; see
    _MARKDOWN_ESCAPE_DECODE_SET for how it was derived and for the three
    characters removed from it. A backslash before anything outside it is left
    exactly as scraped.

    LEFT TO RIGHT, AND THE OUTPUT IS NEVER RE-EXAMINED. That is one rule with
    two consequences, and both are correctness properties rather than
    performance ones:

      * A DOUBLE BACKSLASH RESOLVES BEFORE ITS SUCCESSOR. "\\\\\\>" is an
        escaped backslash followed by an escaped ">", not an escaped backslash
        followed by a bare ">", and only a left-to-right walk that consumes
        "\\X" as ONE unit can tell those apart. A scan that keyed on every
        backslash index would read the second member of the pair as an escape.
      * A CHARACTER THIS FUNCTION EMITS CANNOT BE READ AS MARKUP by this
        function. The sink is append-only and the cursor never moves backwards,
        so nothing produced here is re-scanned -- the same rule
        _decode_escaped_entities applies inside one match, applied here to the
        whole pass. This is what makes the function idempotent: measured over
        all 28,399 render-path fields, decoding the decoded output is the
        identity on every one.

    "\\\\" IS REFUSED RATHER THAN COLLAPSED TO "\\", AND IDEMPOTENCE IS THE
    ARGUMENT. Collapsing is the "correct" markdown reading, and it is the one
    thing this function could do that would EMIT a backslash -- whose successor
    may itself be in the decode set, at which point the output is
    indistinguishable from unprocessed input. Measured: "x\\\\\\[y" collapses
    to "x\\[y" and a second pass gives "x[y", deleting a backslash the sponsor
    wrote. Two real trials have that shape (NCT06773208, NCT07230639) and both
    are non-idempotent under collapse and idempotent under refusal. So the pair
    is emitted verbatim and recorded under MARKDOWN_REFUSED_ESCAPED_BACKSLASH.
    Cost, stated: 14 occurrences in 11 trials reach the judge as "\\\\" where
    the sponsor wrote "\\".

    AN ENTITY CHAIN IS SKIPPED WHOLE, because it belongs to the other decoder.
    _decode_escaped_entities owns exactly one escape per chain -- its own
    docstring's invariant -- and a markdown decoder that stripped the backslash
    off "\\&lt;" first would leave that function decoding a bare chain and
    removing zero escapes, silently making its documented count mean something
    else. Skipping the span keeps the two subjects DISJOINT, so neither can
    double-handle the other's escape in either order; measured, 579 chains
    decode under both orders. Only backslash-headed matches are collected: a
    bare "&lt;" starts at "&", which this walk never visits as a unit start.

    Args:
        text: the third-party criteria string about to be rendered.

    Returns:
        ``(decoded_text, escapes_removed, escapes_refused)``. Separate counts
        for the same reason the entity decoder keeps its two separate -- a
        caller reporting them as one number could not tell a trial this
        function fixed from a trial it declined to touch, which are opposite
        findings. The REASON for each refusal is in
        MARKDOWN_ESCAPE_DECODE_UNRESOLVED's key, on AGE_PARSE_FAILURES'
        footing.
    """
    if not text or "\\" not in text:
        return text, 0, 0

    # The spans the other decoder owns. Computed with the SAME regex it uses;
    # a second spelling here would be a second vocabulary, which is how the two
    # scraper copies in this repository drifted apart.
    chain_end = {}
    for match in _ESCAPED_ENTITY_CHAIN_RE.finditer(text):
        if match.group(0).startswith("\\"):
            chain_end[match.start()] = match.end()

    out = []
    decoded = 0
    refused = 0
    index = 0
    length = len(text)

    def _refuse(reason: str, at: int) -> None:
        MARKDOWN_ESCAPE_DECODE_UNRESOLVED[
            reason + ":" + text[at:at + _MARKDOWN_KEY_MAX_LEN]] += 1

    while index < length:
        char = text[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue

        end = chain_end.get(index)
        if end is not None:
            # Verbatim, and the cursor jumps the whole chain: the other decoder
            # reads it next, out of the string this one produced.
            out.append(text[index:end])
            index = end
            continue

        following = text[index + 1] if index + 1 < length else None

        if following is None:
            # A trailing backslash escapes nothing. Literal, determinate, not a
            # degradation -- and measured zero in the render path.
            out.append(char)
            index += 1
            continue

        if following == "\\":
            out.append("\\\\")
            _refuse(MARKDOWN_REFUSED_ESCAPED_BACKSLASH, index)
            refused += 1
            index += 2
            continue

        if following in _MARKDOWN_REFERENCE_SYNTAX_CHARS:
            out.append(char)
            out.append(following)
            _refuse(MARKDOWN_REFUSED_REFERENCE_SYNTAX, index)
            refused += 1
            index += 2
            continue

        if following in _MARKDOWN_ESCAPE_DECODE_SET:
            out.append(following)
            decoded += 1
            index += 2
            continue

        # Outside the set: a literal backslash under CommonMark. Left as
        # scraped, and deliberately NOT counted -- see
        # MARKDOWN_ESCAPE_DECODE_UNRESOLVED.
        out.append(char)
        index += 1

    return "".join(out), decoded, refused


def _decode_escaped_entities(text: str) -> Tuple[str, int, int]:
    """Restore the characters a trial's criteria text spells as HTML entities.

    THE SPONSOR'S WORDING IS RESTORED, NEVER REWRITTEN. This undoes an encoding
    the text picked up somewhere between the sponsor's submission and this
    repository's scrape; it does not paraphrase, normalise, reflow or repair
    anything. The stored corpus and the Qdrant payload are untouched -- the
    decode happens at render, so re-indexing is not a precondition for the
    judge to read a threshold correctly and every future corpus gets it too.

    WHAT IS ACTUALLY THERE, measured rather than described. The registry stores
    "\\&lt;" where the author typed "<": the character was HTML-escaped to
    "&lt;" and the ampersand of that escape was then markdown-escaped to "\\&".
    Some rows went round that loop several times, so "\\&amp;amp;gt;" and, once,
    "\\&amp;amp;amp;amp;amp;amp;amp;amp;amp;amp;lt;" also occur. See
    _ESCAPED_ENTITY_CHAIN_RE for the counts and _ENTITY_DECODE_MAX_PASSES for
    the depth histogram.

    THE FIXED POINT IS TAKEN INSIDE ONE MATCH, NOT OVER THE WHOLE STRING, and
    that is a correctness property rather than an optimisation. Re-scanning the
    substituted text would let a "&" this function just produced bind to
    whatever literal characters happen to follow it and be read as a second
    reference on the next pass -- so "\\&amp;notin 3;" would decode to
    "&notin 3;" and then, on a whole-string re-scan, to "¬in 3;", inventing a
    character the sponsor never wrote out of two unrelated fragments. Each
    matched chain is a closed, self-delimiting string; iterating within it
    cannot reach a neighbouring character, and the substitution's output is
    never re-examined.

    A MATCH THAT IS NOT A REFERENCE IS LEFT EXACTLY AS IT WAS, backslash
    included. The pattern recognises the SHAPE of a character reference, and
    "\\&AB;" has that shape without being one; html.unescape returns it
    unchanged, this function sees that nothing decoded and returns the original
    match rather than a version with the backslash quietly removed. That is a
    determinate answer -- there was nothing to decode -- so it is not counted as
    a degradation, on the footing M_CATEGORY_UNREADABLE argues for cM0.

    ONE ESCAPE IS REMOVED AND ONLY ONE: the backslash that was escaping the
    ampersand of the reference this function decoded. Nothing in this function
    can see or alter a backslash that is not immediately followed by a
    character reference.

    THAT INVARIANT SURVIVED THE MARKDOWN PASS, AND IT DID SO BY DESIGN. The
    corpus's other markdown escapes -- "\\>", "\\*", "\\[" and the rest, 69,397
    of them across 70.57% of trials -- were a separate finding when this
    function shipped and are now _decode_markdown_escapes', which runs BEFORE
    this one at the _build_trials_text call site. It would have taken the
    backslash off "\\&lt;" and left this function decoding a bare chain and
    removing zero escapes, quietly turning the sentence above into a falsehood;
    instead it SKIPS a chain span whole, so the two subjects are disjoint and
    each function's count still means what it says. Measured across both
    orders: 579 chains, unchanged.

    Args:
        text: the third-party criteria string about to be rendered.

    Returns:
        ``(decoded_text, chains_decoded, chains_refused)``. The first count is
        of chains whose decode changed the text; the second is of chains left
        exactly as scraped because substituting would have made things worse --
        the cap was reached, or the reference names no character that belongs
        in criteria prose. They are separate because a caller that reported
        them as one number could not tell a trial this function fixed from a
        trial it gave up on, which are opposite findings. The REASON for each
        refusal is in ESCAPED_ENTITY_DECODE_UNRESOLVED's key rather than in
        the return value, on AGE_PARSE_FAILURES' footing.
    """
    if not text:
        return text, 0, 0

    decoded_count = [0]
    unresolved_count = [0]

    def _refuse(reason: str, raw: str) -> None:
        """Record a chain left as scraped, under the reason it was left."""
        ESCAPED_ENTITY_DECODE_UNRESOLVED[
            reason + ":" + raw[:_ENTITY_KEY_MAX_LEN]] += 1

    def _decode(match: "re.Match") -> str:
        raw = match.group(0)
        body = raw[1:] if raw.startswith("\\") else raw

        current = body
        at_fixed_point = False
        for _ in range(_ENTITY_DECODE_MAX_PASSES):
            following = html.unescape(current)
            if following == current:
                at_fixed_point = True
                break
            current = following

        if not at_fixed_point:
            # THE CAP COUNTS DECODING PASSES, NOT LOOP ITERATIONS, and the
            # difference is a real off-by-one rather than pedantry: a chain
            # whose depth is exactly the cap has been decoded in full, and the
            # loop above exits without ever having seen a pass that changed
            # nothing. Reporting that as unresolved would leave the last
            # decodable depth permanently unreachable, making a cap of N behave
            # as N-1. The confirming call is made only here, so the common path
            # pays nothing for it.
            at_fixed_point = html.unescape(current) == current

        if not at_fixed_point:
            _refuse(ENTITY_REFUSED_PASS_CAP, raw)
            unresolved_count[0] += 1
            return raw

        if current == body:
            return raw

        # THE DECODE IS REFUSED WHEN IT WOULD DAMAGE RATHER THAN RESTORE. See
        # ESCAPED_ENTITY_DECODE_UNRESOLVED for the four measured references
        # that reach the first branch and the one that reaches the second.
        if "�" in current or not current:
            _refuse(ENTITY_REFUSED_REPLACEMENT_CHAR, raw)
            unresolved_count[0] += 1
            return raw

        decoded_count[0] += 1
        return current

    return (_ESCAPED_ENTITY_CHAIN_RE.sub(_decode, text),
            decoded_count[0], unresolved_count[0])


def _neutralize_fence_markers(text: str) -> Tuple[str, int]:
    """Spell out any fence marker inside third-party text so it cannot BE one.

    A fence isolates data only if the data cannot spell the fence. Every
    character of a trial's criteria comes from ClinicalTrials.gov, is
    re-scraped weekly, and is under no control of this project -- so a trial
    whose criteria contain ``<<<END_TRIAL_DATA nct_id=...>>>`` would close its
    own block from the inside and everything after it would read as though it
    were outside the quoted region.

    WHY A RUN REGEX RATHER THAN ``str.replace``. The obvious form,
    ``text.replace("<<<", "<< <")``, is not closed under its own output: five
    consecutive ``>`` characters replace to ``> >>`` + ``>>``, which spells
    ``>>>`` again. Any replacement that ends in the marker character can
    re-form the marker from the tail of an odd-length run, so the substitution
    is over the WHOLE maximal run (``<{3,}`` is greedy, and the character
    either side of a maximal run is by definition not that character) and the
    replacement inserts nothing but spaces between characters the run already
    had. No run of three can survive and none can be created.

    Args:
        text: the third-party string about to be interpolated.

    Returns:
        ``(neutralized_text, runs_rewritten)``. The count is of RUNS, which is
        the number of substitutions actually performed; ``<<<<<<`` is one run
        and one replacement, not two.
    """
    if not text:
        return text, 0
    hits = [0]

    def _space_out(match: "re.Match") -> str:
        hits[0] += 1
        return " ".join(match.group(0))

    return _FENCE_MARKER_RUN_RE.sub(_space_out, text), hits[0]


def _render_trial_blocks(trials: List[Dict], *,
                         log_events: bool = True) -> List[str]:
    """Render one batch of trials, ONE SELF-CONTAINED BLOCK PER TRIAL.

    A LIST, NOT A JOINED STRING, AND THAT IS THE WHOLE POINT OF THE SPLIT.
    ``parts[i]`` depends on ``trials[i]`` and on nothing else -- no cross-trial
    state reaches it; the three ``md_*`` accumulators below feed the aggregate
    LOG line and never a block -- so the blocks of a whole-batch render are
    byte-identical to the blocks any sub-batch render would produce, and
    ``"".join`` of any sub-list is exactly what that sub-batch would have been
    sent as. That makes a per-trial MEASUREMENT sliceable out of one render
    instead of costing a render of its own, which is what the Stage 5 packer
    now does. ``tests/test_agent_stage5_render_slice_equality.py`` proves the
    identity over trials carrying every tricky class rather than over clean
    text, because the three rewrites below are exactly what a naive
    "sum of the parts" would get wrong.

    THE PACKER USED TO RE-RENDER EVERY TRIAL, and the cost was not the CPU.
    ``_trial_input_tokens`` called this function once per trial with
    ``log_events=False``, so each trial was rendered THREE times per patient --
    the whole-batch stored-prompt render, the packer's measurement, and the
    chunk actually sent -- and the two refusal counters below, which are inside
    the decoders and were never suppressed, read 1.5x their true value on a
    patient whose batch did not split. Measured, not inferred. The measurement
    render is gone; the two that remain are both sends.

    ``log_events=False`` renders identical text and emits NO render events. It
    is for a caller that renders in order to MEASURE the result rather than to
    send it. Every event this function emits reports a modification of
    third-party text ON ITS WAY TO THE JUDGE; a render nobody sends made no
    such journey, and logging one attributes a rewrite to a request that was
    never issued. Measured before it was closed: on a 15-trial batch the
    packer's measurement path was 11 of the 13 remaining lines per patient.

    KEYWORD-ONLY, so a second positional argument can never be read as the
    flag, and DEFAULTING TO TRUE, so silence is something a caller asks for
    explicitly and never something it gets by forgetting. The send-like callers
    -- the per-chunk render, the whole-batch stored-prompt render, and
    ``oncotriage/evaluation/run_harness.py:build_contexts``, whose text IS
    shown to a rater -- pass nothing and are unchanged.

    THE ONE CALLER THAT STILL ASKS FOR SILENCE is the node's wrapper pricing,
    ``_user_prompt_for([], log_events=False)``, which renders the user message
    with no trials in it purely to charge its fixed token cost to every chunk.
    It renders zero trials, so today it would emit nothing whatever the flag
    said; it passes the flag because it is a MEASUREMENT render and the flag
    states which kind of render it is, not which lines happened to be reachable
    this week. Keeping one real caller is also what stops this parameter and
    ``_SilentLog`` becoming a mechanism with no subject -- the shape this
    project deletes rather than leaves declared.

    COUNTERS ARE DELIBERATELY NOT SUPPRESSED, AND THAT DECISION IS UNCHANGED.
    ``log_events`` governs the log channel and nothing else:
    ``MARKDOWN_ESCAPE_DECODE_UNRESOLVED`` and
    ``ESCAPED_ENTITY_DECODE_UNRESOLVED`` are incremented inside the decoders,
    which this function calls identically either way. What changed is not the
    flag's contract but the CALL GRAPH: there is no longer a measurement render
    over real trials for it to inflate. Suppressing the counts would have made
    one flag govern two unrelated things -- what is logged, and what is
    counted -- and would have left the redundant render in place; removing the
    render fixes the counter, the wasted work and the residual log volume at
    once, and leaves counter semantics exactly where they were.

    EVERY TRIAL IS WRAPPED IN AN EXPLICIT DATA DELIMITER, and Section 6's C6
    tells the model what a delimiter means. The trial text is third party --
    scraped from ClinicalTrials.gov and re-indexed weekly by anyone who can get
    a study registered -- and it used to sit in the same message as the patient
    record with nothing marking where it began or ended. Prose inside a
    criteria block reading like an instruction was, byte for byte,
    indistinguishable from an instruction. The block is::

        <<<TRIAL_DATA nct_id=NCT01234567 phase=PHASE2>>>
        ...inclusion criteria, as scraped but for the two rewrites below...
        ...exclusion criteria, as scraped but for the two rewrites below...
        <<<END_TRIAL_DATA nct_id=NCT01234567>>>

    THE CRITERIA BODIES ARE NO LONGER SENT BYTE-FOR-BYTE AS SCRAPED, and the
    three rewrites that touch them are the only three: _decode_markdown_escapes
    removes the backslashes the registry added to escape punctuation,
    _decode_escaped_entities restores the characters it stored as escaped HTML
    entities, and _neutralize_fence_markers spells out any bracket run. In that
    order, for the reason argued at the call site. None paraphrases: the first
    two return a character the sponsor wrote and an encoding was standing in
    for, the third inserts spaces into a run that no real trial in the corpus
    contains. Everything else -- wording, ordering, whitespace, punctuation --
    is untouched, and so is the STORED text: all three run at render, so
    oncotriage/retrieval/indexer.py's splitter still reads the escaped
    "\\<Exclusion Criteria\\>" its _HEADING_LEAD_CHARS depends on.

    THE nct_id RIDES IN BOTH FENCE LINES. In the open line because the header
    that used to carry it is gone and Section 5 tells the model to copy the id
    from the fence attribute; in the CLOSE line because a close that named
    nothing would let two adjacent blocks be misread as one under a truncated
    or reflowed render, which would silently merge one trial's exclusions into
    another's.

    NEUTRALIZATION HAPPENS BEFORE INTERPOLATION, NEVER AFTER ASSEMBLY. Each
    third-party value is passed through _neutralize_fence_markers on its way
    into the block; the assembled message is never re-scanned, because a scan
    of the assembled message would rewrite the fences this function just
    wrote. The values neutralized are the criteria bodies AND the two values
    interpolated into the fence lines themselves (nct_id, phase) -- both of
    those are scraped registry fields too, and a fence whose own attribute
    values can spell ``>>>`` is not a boundary. On every real trial in the
    corpus this changes nothing: a well-formed NCT id and a phase string
    contain no angle brackets at all.

    THE HEADER CARRIES NO ORDINAL, AND THAT IS THE POINT RATHER THAN A
    SIMPLIFICATION. It used to read ``Trial {n} (NCT..., PHASE):`` with the
    numbering restarting at 1 inside each chunk, and the ordinal was
    presentation only -- the model was told to key its output on nct_id, and
    every merge downstream matches on nct_id. But it was not free: the trials
    arrive in RETRIEVAL ORDER, so a number beside each one tells a judge where
    the pipeline ranked it, which is a bias channel pointing straight at the
    trial isolation C4 demands. The response schema's ``trial_number`` went in
    the same pass (see oncotriage/agent/response_schema.py), so the model
    neither reads a rank nor states one.

    It also removed an ambiguity the old docstring had to apologise for: a
    chunked run showed "Trial 1" twice inside one inference.

    The ordinal-free header and the ``---`` separator are BOTH gone now: the
    fence lines replace them, and the two facts the old header carried
    (nct_id, phase) are the two attributes of the open fence. Order is
    unchanged -- the trials are still rendered in the order they were ranked --
    and so are the criteria bodies. The rank is still real and still stored:
    node_finalize assigns trial_number from the position in filtered_trials and
    trial_matches.trial_number records it.
    """
    # EVERY event below goes through `emit`, never through `log`. See
    # _SilentLog for why the suppression is a rebinding rather than six guards.
    emit = log if log_events else _SILENT_LOG
    parts = []
    # The markdown decode is reported ONCE FOR THIS CALL, not once per trial.
    # See the aggregate below the loop for the measurement that forces it.
    md_rendered = 0
    md_trials_affected = 0
    md_sequences = 0
    for trial_obj in trials:
        trial = trial_obj["trial"]
        md_rendered += 1
        # str() rather than `or ""`: the values used to reach an f-string, so
        # this reproduces exactly what the f-string did with a None or a
        # non-string, including rendering it as "None". Changing that would be
        # a body change this pass does not make.
        nct_id, hits_id = _neutralize_fence_markers(str(trial["nct_id"]))
        phase, hits_phase = _neutralize_fence_markers(str(trial["phase"]))

        # DECODE BEFORE NEUTRALIZE, AND THE ORDER IS THE WHOLE ARGUMENT. A
        # decoded sequence must not be able to walk past the neutralizer: a
        # trial storing "\&gt;\&gt;\&gt;" -- or "\>\>\>", or "\&#62;" three
        # times -- carries no bracket run while it is escaped, and decoding it
        # produces ">>>", which is exactly what the fences are built from.
        # Decoding after neutralization would hand the model a run this
        # function had already declared safe. Neutralization stays last, over
        # the fully decoded text, so what it inspects is the string that will
        # actually be sent. Measured over the whole corpus: zero trials acquire
        # a run, so today this is a guard rather than a fix -- which is exactly
        # when a guard has to be in the right place.
        #
        # MARKDOWN BEFORE ENTITIES, AND BOTH DIRECTIONS WERE MEASURED BEFORE
        # THE ORDER WAS PICKED. The two orders agree on all 28,399 render-path
        # fields of this corpus, so the corpus does not decide it; constructed
        # input does, and each order has a real invention hazard:
        #
        #   entities first -- a chain can decode TO a backslash ("\&#92;",
        #       "&bsol;"), and a markdown decoder reading the result would take
        #       that produced backslash for an escape and eat the character
        #       after it. "5 \&#92;&gt; 3" becomes "5 > 3" instead of "5 \> 3":
        #       a comparator invented out of two unrelated fragments, which is
        #       the failure _decode_escaped_entities' "output is never
        #       re-examined" rule exists to prevent, one level up.
        #   markdown first -- stripping an escape can SUPPLY a character a
        #       chain needs. "5 \&gt\; 3" is a sponsor who typed "&gt;".
        #
        # Neither is safe by construction, so both are closed rather than one
        # being chosen and hoped for. The markdown decoder runs first, on raw
        # scraped text only, so it can never read what the entity decoder
        # produced; and ";" and "#" are outside its decode set, so it can never
        # build a reference for the decoder that follows. It also skips any
        # chain span whole, which keeps the two subjects disjoint and each
        # function's count meaning what it says.
        #
        # The two fence ATTRIBUTE values above are deliberately not decoded, in
        # either sense. Zero escapes and zero entities were measured in either
        # field, and not decoding them is the strictly safer half of the same
        # argument -- an escaped bracket run in an nct_id stays escaped and can
        # never become a fence.
        inclusion, esc_inc, ref_inc = _decode_markdown_escapes(
            str(trial["eligibility"]["inclusion_criteria"]))
        exclusion, esc_exc, ref_exc = _decode_markdown_escapes(
            str(trial["eligibility"]["exclusion_criteria"]))
        inclusion, dec_inc, unres_inc = _decode_escaped_entities(inclusion)
        exclusion, dec_exc, unres_exc = _decode_escaped_entities(exclusion)
        inclusion, hits_inc = _neutralize_fence_markers(inclusion)
        exclusion, hits_exc = _neutralize_fence_markers(exclusion)

        escapes = esc_inc + esc_exc
        if escapes:
            md_trials_affected += 1
            md_sequences += escapes
            # THE PER-TRIAL LINE IS DEBUG, AND THE AGGREGATE BELOW IS THE INFO.
            # Nothing is lost by the split -- this line carries exactly what
            # the INFO used to carry, nct_id and count, so an operator who
            # wants the per-trial record sets ONCOTRIAGE_LOG_LEVEL=DEBUG and
            # has it back verbatim. What changes is the DEFAULT volume, and
            # only the default.
            #
            # The entity sibling below keeps its per-trial INFO and that is a
            # measurement rather than an inconsistency: 1.4% of trials carry an
            # entity against 70.57% carrying an escape, so one line per
            # affected trial is 197 lines there and roughly one line per trial
            # per render here. The argument that licensed the entity event does
            # not transfer, which is precisely why this one moved and that one
            # did not.
            emit.debug("removed registry markdown escaping from scraped trial "
                       "text", stage=5, node="llm_classifier_evaluation",
                       event="trial_markdown_escape_decoded_trial",
                       nct_id=nct_id, count=escapes)

        refused = ref_inc + ref_exc
        if refused:
            # Its own event, not a field on the one above: this text went out
            # STILL ESCAPED, which is the opposite finding, and a reader
            # filtering on the decoded event must not be shown a line that says
            # the reverse of what happened. Same split as the entity pair.
            emit.warning("left registry markdown escaping as scraped: the "
                         "escaped character is outside the decode set",
                         stage=5, node="llm_classifier_evaluation",
                         event="trial_markdown_escape_unresolved",
                         nct_id=nct_id, count=refused)

        decoded = dec_inc + dec_exc
        if decoded:
            # INFO rather than WARNING, which is where this parts company with
            # the fence event below. A fence marker in scraped text is an
            # anomaly worth waking someone for; an escaped entity is a routine
            # registry artefact in 1.4% of the corpus, and a warning per render
            # for 197 trials is noise that would train a reader to ignore the
            # channel. It is still recorded on every render, because this is a
            # modification of third-party text on its way to the judge and the
            # record of what was sent has to say that it happened.
            emit.info("restored characters stored as escaped HTML entities "
                      "in scraped trial text",
                      stage=5, node="llm_classifier_evaluation",
                      event="trial_escaped_entity_decoded",
                      nct_id=nct_id, count=decoded)

        unresolved = unres_inc + unres_exc
        if unresolved:
            # Its own event, not a field on the one above. This trial's text
            # went out STILL ESCAPED, which is the defect rather than the fix,
            # and a reader filtering on the decoded event must not be shown a
            # line that says the opposite of what happened.
            emit.warning("left an escaped HTML entity as scraped: no fixed "
                         "point within the decode pass cap",
                         stage=5, node="llm_classifier_evaluation",
                         event="trial_escaped_entity_unresolved",
                         nct_id=nct_id, count=unresolved,
                         depth=_ENTITY_DECODE_MAX_PASSES)

        neutralized = hits_id + hits_phase + hits_inc + hits_exc
        if neutralized:
            # The nct_id reported is the NEUTRALIZED one, so the line names the
            # string that was actually sent rather than one that never left
            # this function. It fires once per render of the trial, so a batch
            # that splits reports it per chunk as well as for the whole-batch
            # render kept for logging -- that is a count of renders, not of
            # trials, and the event name says so.
            emit.warning("neutralized a fence marker inside scraped trial text",
                         stage=5, node="llm_classifier_evaluation",
                         event="trial_fence_marker_neutralized",
                         nct_id=nct_id, count=neutralized)

        parts.append(
            f"<<<TRIAL_DATA nct_id={nct_id} phase={phase}>>>\n"
            f"{inclusion}\n"
            f"{exclusion}\n"
            f"<<<END_TRIAL_DATA nct_id={nct_id}>>>\n\n"
        )

    # ONE LINE PER RENDER, NOT ONE PER TRIAL, AND THE NUMBER IS WHY. Measured
    # over the 14,324-trial corpus with the shipped decoder: 10,108 trials
    # (70.57%) carry at least one escape, 69,396 sequences in all. The per-trial
    # INFO this replaces therefore fired for roughly seven trials in ten of
    # every render, and a batch is rendered more than once -- the whole batch
    # for the stored prompt, then once per chunk that is actually sent. (It was
    # rendered a THIRD time, once per trial, for the packer's measurement; that
    # render is gone -- the packer slices the whole-batch blocks instead.) The
    # record the event exists to keep is "third-party text was modified on its
    # way to the judge, and here is how much"; that is a statement about the
    # render, and it is fully carried by the three cardinalities below.
    #
    # THE GUARD IS `if md_sequences`, MATCHING EVERY OTHER EVENT IN THIS
    # FUNCTION. The fence, entity and refusal events are each guarded by their
    # own count, so a render that changed nothing says nothing -- and without
    # the guard the empty render at the _user_prompt_for([]) call site, which
    # exists only to measure the wrapper's fixed token cost, would emit a line
    # claiming a decode pass over no trials at all. That site ALSO passes
    # log_events=False now, so the guard and the flag cover it independently;
    # neither was made redundant by the other, because the guard is about a
    # batch with nothing to report and the flag is about a render nobody sends.
    #
    # REFUSALS ARE DELIBERATELY NOT A FIELD HERE. They keep their own per-trial
    # WARNING in the loop above, unchanged, on the split this function argues: a
    # reader filtering on the decoded event must not be shown a line that says
    # the reverse of what happened. The volume argument does not reach them
    # either -- measured on the same corpus, 12 trials (0.08%) and 15 sequences
    # in total, which is rarer than the entity event that keeps its per-trial
    # INFO, and the nct_id a refusal names is the actionable field.
    if md_sequences:
        emit.info("removed registry markdown escaping from scraped trial text",
                  stage=5, node="llm_classifier_evaluation",
                  event="trial_markdown_escape_decoded",
                  total=md_rendered, trials_affected=md_trials_affected,
                  count=md_sequences)

    return parts


def _build_trials_text(trials: List[Dict], *, log_events: bool = True) -> str:
    """The rendered batch as ONE string: the blocks, joined with no separator.

    THE JOIN IS THE WHOLE BODY, and that is the property the packer's
    arithmetic rests on. ``_render_trial_blocks`` emits one self-contained
    block per trial and nothing between them, so ``len`` of this string is the
    sum of the per-block lengths exactly, and a per-trial measurement taken off
    a block is a measurement of bytes that will really be sent. Anything else
    here -- a separator, a header, a sort -- would silently make the packer's
    sum describe a string nobody sends.

    IT KEEPS ITS NAME, ITS SIGNATURE AND ITS RETURN TYPE, deliberately. Three
    callers read it as a plain ``str``: the node's per-chunk render, the node's
    wrapper pricing, and ``oncotriage/evaluation/run_harness.py:build_contexts``
    -- which is in another module and was not touched by this change. Splitting
    the render out from under it rather than changing what it returns is what
    kept that promise.

    ``log_events`` is forwarded unchanged; see ``_render_trial_blocks`` for
    what it does, what it deliberately does NOT do (the two refusal counters),
    and which caller still asks for silence.
    """
    return "".join(_render_trial_blocks(trials, log_events=log_events))


# ---------------------------------------------------------------------------
# Stage 5 INPUT packing
# ---------------------------------------------------------------------------
#
# THE OTHER AXIS. estimate_output_tokens and the two splitters built on it are
# about the RESPONSE. This is about the REQUEST, and the two are independent:
# a batch can be small enough to answer inside the output ceiling and still be
# large enough on the way in to degrade the answers, omit trials silently, and
# let reasoning leak between trials inside one prompt. oncotriage/config.py's
# MATCHING_INPUT_TOKEN_BUDGET block records the measurements.
#
# THE TWO COMPOSE, THEY DO NOT REPLACE EACH OTHER. The packer produces the
# chunks the node's existing pre-split loop then works over, so a packed chunk
# whose OUTPUT estimate is still too large is halved by the machinery that
# already existed, and a packed chunk whose response is cut off is halved
# reactively by the machinery that already existed. Nothing about merging,
# duplicate handling, out-of-set classification, refusal semantics or the
# reconciliation is duplicated here -- all of it is already chunk-aware, and
# packing only changes how the first generation of chunks is produced.

# What the token figures in the packing record were measured with. Recorded in
# provenance rather than left implicit: the packer's decisions are only
# reproducible if the estimator that made them is named, and this project has
# been through one estimator change already.
PACKING_METHOD_CHARS = f"characters/{CHARS_PER_TOKEN}"


class PackingBlockMismatchError(RuntimeError):
    """``pack_trials_by_input_tokens`` was handed blocks that are not its trials.

    A ``RuntimeError`` SUBCLASS AND DELIBERATELY NOT A ``ValueError``, on the
    footing ``UnknownModelPricingError``, ``IndexVerificationError`` and
    ``CrossEncoderLimitMismatchError`` already argue in this project: a stray
    ``except ValueError`` around a Stage 5 call must not be able to eat it.

    IT RAISES BECAUSE THE ALTERNATIVE IS SILENT AND WRONG. The packer indexes
    ``blocks`` and ``trials`` in parallel, so if their lengths disagree then
    ``costs[i]`` prices a different trial than ``trials[i]`` -- and the packer
    would go on to produce a perfectly well-formed partition, sized by the
    wrong bytes, with no error, no counter and no symptom anywhere downstream
    except worse chunking. That is precisely the class of failure this project
    exists to remove, so it is a refusal rather than a warning.
    """


def estimate_prompt_tokens(text: str) -> int:
    """Estimated tokens for a piece of prompt text.

    The same characters/CHARS_PER_TOKEN proxy File 11 uses for embedding batch
    sizing and estimate_output_tokens uses for its criteria term, applied to the
    request instead of the response. No tokenizer: tiktoken would be a new heavy
    dependency and an import cost, and it would still not be the model's own
    tokenizer -- gpt-5.6-terra publishes none.

    CHARS_PER_TOKEN is 4 against a measured 4.2-4.4 on this project's prompts,
    so this OVER-states by 5-10%. That is the direction a budget guard has to
    err in; see the constant.

    Rounded UP, for the same reason. int() truncation would let a chunk sit one
    token over the budget for every fractional remainder in it, which across
    fifteen trials is a systematic under-count of a guard.
    """
    if not text:
        return 0
    return -(-len(text) // CHARS_PER_TOKEN)


def _trial_input_tokens(block: str) -> int:
    """Estimated request tokens contributed by ONE trial's rendered block.

    MEASURED THROUGH THE SHIPPED RENDERER, never through a second formula, and
    that is unchanged -- what changed is WHERE the render comes from. This used
    to take a trial object and call ``_build_trials_text([trial_obj],
    log_events=False)`` itself, so the packer rendered every trial a second
    time purely to price it. It now takes a block that a render already
    produced, which is the same bytes by construction:
    ``_render_trial_blocks`` builds ``parts[i]`` from ``trials[i]`` alone, so
    the block a whole-batch render produced for a trial IS the block a
    one-trial render would have produced for it. Proven rather than asserted,
    over trials carrying escapes, entity chains and fence markers, in
    ``tests/test_agent_stage5_render_slice_equality.py``.

    WHY THAT MATTERED, AND IT WAS NOT THE CPU. The two refusal counters live
    inside the decoders and are deliberately not suppressed by
    ``log_events=False`` (see ``_render_trial_blocks``), so the extra render
    counted every refusal a second time. Three renders per trial per patient
    became two, and the counters stopped reading 1.5x on a batch that does not
    split.

    A ONE-LINE BODY IS THE POINT, NOT AN ARGUMENT FOR DELETING IT. The name is
    where "a trial's input cost is the token estimate of its rendered block"
    is written down; inlining ``estimate_prompt_tokens`` at the packer would
    leave that sentence in nobody's keeping, which is how the two scraper
    copies in this repository drifted apart.

    Args:
        block: one element of ``_render_trial_blocks``'s return value.

    Returns:
        The token estimate for that block, by the same
        characters/CHARS_PER_TOKEN proxy every other input figure uses.
    """
    return estimate_prompt_tokens(block)


def _pack_greedy(costs: List[int], fixed_tokens: int,
                 budget: int) -> List[List[int]]:
    """Greedy next-fit over ``costs``, in order, into chunks of ``budget``.

    ``costs`` are per-trial token estimates and the return value is a list of
    lists of INDEXES into it, so the caller can map back to trial objects
    without this function knowing what a trial is.

    NEXT-FIT, IN THE GIVEN ORDER, and both halves matter. Order is preserved
    because the trials arrive ranked and the pipeline's determinism is built on
    that ranking; a bin-packing heuristic that reorders (first-fit-decreasing
    and friends) would pack marginally tighter and would send trials to the
    judge in an order nothing else in this pipeline produces. Next-fit, with the
    order fixed, is also MONOTONE in the budget -- a larger budget never yields
    more chunks -- which is what makes the binary search in
    ``pack_trials_by_input_tokens`` correct rather than approximate.

    A cost that does not fit in an EMPTY chunk gets its own chunk anyway. It is
    the caller that flags it; dropping it is not an option this function has.
    """
    chunks: List[List[int]] = []
    current: List[int] = []
    used = 0
    for index, cost in enumerate(costs):
        if current and fixed_tokens + used + cost > budget:
            chunks.append(current)
            current = []
            used = 0
        current.append(index)
        used += cost
    if current:
        chunks.append(current)
    return chunks


def _minimum_budget_for(costs: List[int], fixed_tokens: int, lower: int,
                        max_chunks: int) -> int:
    """The smallest budget >= ``lower`` that packs ``costs`` into <= max_chunks.

    Exists only for the cap: when the configured budget would produce more
    chunks than MATCHING_MAX_INPUT_PACKED_CHUNKS allows, the budget is raised
    UNIFORMLY to the least value that fits, and no trial is dropped. See the
    constant for why the only degree of freedom is the budget.

    BINARY SEARCH IS EXACT HERE, not a heuristic, because _pack_greedy's chunk
    count is monotone non-increasing in the budget. Sketch: let f_B(j) be the
    index reached after j chunks at budget B. f_B'(0) = f_B(0) = 0 for
    B' >= B; and if chunk j+1 under B' starts at s' >= s = f_B(j), then either
    s' is already past the end of chunk j+1 under B, or the items from s' to
    that end are a suffix of a set that fit in B <= B', so B' reaches at least
    as far. So the predicate "fits in <= max_chunks" is monotone in B and the
    search finds its threshold.

    The upper bound always satisfies the predicate: fixed + sum(costs) packs
    everything into one chunk, and max_chunks >= 1. So this never returns
    without an answer, which is what "never drop a trial" rests on.
    """
    high = fixed_tokens + sum(costs)
    low = max(lower, 1)
    if len(_pack_greedy(costs, fixed_tokens, low)) <= max_chunks:
        return low
    while low < high:
        mid = (low + high) // 2
        if len(_pack_greedy(costs, fixed_tokens, mid)) <= max_chunks:
            high = mid
        else:
            low = mid + 1
    return low


def pack_trials_by_input_tokens(trials: List[Dict], fixed_tokens: int,
                                budget: int, max_chunks: int, *,
                                blocks: List[str]) -> Tuple[List[List[Dict]], Dict]:
    """Split a batch into chunks whose estimated INPUT stays under ``budget``.

    Args:
        trials: the batch, in the order Stage 4 ranked it. Never reordered.
        fixed_tokens: estimated tokens every request carries whatever is in it
            -- the system message (instructions plus this patient's record) and
            the user message's wrapper. It is charged to EVERY chunk, because
            the model reads one prompt and a budget that ignored half of it
            would not be a budget.
        budget: MATCHING_INPUT_TOKEN_BUDGET, or whatever the caller passes.
        max_chunks: MATCHING_MAX_INPUT_PACKED_CHUNKS.
        blocks: ``_render_trial_blocks(trials)``, positionally parallel to
            ``trials``. REQUIRED, KEYWORD-ONLY AND WITHOUT A DEFAULT, and all
            three are deliberate. Required with no default because the obvious
            default -- render them here when the caller does not supply them --
            is the defect this argument exists to remove: it would let a
            caller silently reinstate a second render of every trial, which
            double-counts the two refusal counters inside the decoders and
            shows up nowhere. Keyword-only so a fifth positional argument can
            never be read as one of the three integers above. Same footing as
            ``empty_database(db_path, flag)``: a plausible thing to type must
            not quietly do the harmful thing.

    Raises:
        PackingBlockMismatchError: ``blocks`` is not positionally parallel to
            ``trials``.

    Returns:
        ``(chunks, report)``. ``chunks`` is a partition of ``trials`` -- every
        trial in exactly one chunk, order preserved, no chunk empty -- and
        ``report`` is the provenance record the node publishes.

    THE INVARIANT THIS FUNCTION EXISTS TO KEEP is that a trial is never dropped.
    Every path here either places a trial or raises; there is no branch that
    discards one, and the two ways a batch can refuse to fit are both resolved
    by RAISING THE BUDGET rather than by shedding load:

      * more chunks than the cap allows -> the budget is raised uniformly to
        the least value that fits (``_minimum_budget_for``), and
        ``cap_relaxed_budget`` records it;
      * a single trial larger than the budget on its own -> it ships as its own
        over-budget chunk, and ``over_budget_chunk`` records it. There is
        nothing smaller to send it in; the alternative is not sending it.

    A false keep costs some answer quality on one chunk. A false drop costs a
    patient a trial they might be eligible for, silently. They are not
    comparable.

    THE TWO FLAGS MEASURE AGAINST DIFFERENT BUDGETS, DELIBERATELY.
    ``cap_relaxed_budget`` says the CONFIGURED budget could not fit the batch in
    ``max_chunks`` and records what it was raised to. ``over_budget_chunk`` is
    then measured against the EFFECTIVE budget, so it means "this chunk could
    not be made to fit by any amount of packing" -- which after a relaxation is
    only ever a single trial larger than the whole allowance. One flag folded
    over both budgets would report a relaxed run and an unpackable trial as the
    same finding, and they have different fixes.
    """
    report = {
        "enabled": True,
        "method": PACKING_METHOD_CHARS,
        "fixed_tokens": fixed_tokens,
        "budget_tokens_configured": budget,
        "budget_tokens": budget,
        "max_chunks": max_chunks,
        "cap_relaxed_budget": False,
        "over_budget_chunk": False,
        "trials": len(trials),
        "chunks": [],
    }
    # BEFORE THE EMPTY-BATCH RETURN, so a caller that hands [] trials and a
    # non-empty blocks list is refused rather than being told there was nothing
    # to pack. The two disagreeing is the same defect whichever side is empty.
    if len(blocks) != len(trials):
        raise PackingBlockMismatchError(
            f"pack_trials_by_input_tokens was given {len(trials)} trials and "
            f"{len(blocks)} rendered blocks. They index in parallel, so this "
            f"would have priced each trial with another trial's bytes. Pass "
            f"_render_trial_blocks(trials) for the SAME list of trials.")

    if not trials:
        # A zero-trial batch is a real state -- Stage 4 can empty the pool and
        # the graph routes elsewhere, but this node must not depend on that.
        # One empty chunk would issue a request about nothing; no chunk at all
        # is the truthful answer and the caller's loop handles it.
        return [], report

    # SLICED OUT OF THE CALLER'S ONE RENDER, never re-rendered here. This read
    # `[_trial_input_tokens(t) for t in trials]`, where that function rendered
    # each trial itself -- a whole extra render of the batch, whose only
    # visible effect was that the decoders' two refusal counters counted every
    # refusal a second time. The estimator is unchanged; only its input is.
    #
    # PAIRED ONCE, AND EVERYTHING BELOW READS THE PAIRS. Two parallel lists
    # walked by a shared index is a correspondence that any later edit can
    # break without changing a length -- reorder `trials`, filter it, and
    # `costs[i]` prices some other trial with no error and no symptom except a
    # worse partition. The length check above cannot see a PERMUTATION, so the
    # correspondence is not maintained, it is CONSUMED: a trial and its cost
    # are zipped here and travel together from this line on. `trials` is not
    # indexed again anywhere below.
    priced = [(trial_obj, _trial_input_tokens(block))
              for trial_obj, block in zip(trials, blocks)]
    costs = [cost for _, cost in priced]

    effective = budget
    index_chunks = _pack_greedy(costs, fixed_tokens, effective)
    if len(index_chunks) > max_chunks:
        effective = _minimum_budget_for(costs, fixed_tokens, budget, max_chunks)
        index_chunks = _pack_greedy(costs, fixed_tokens, effective)
        report["cap_relaxed_budget"] = True
        report["budget_tokens"] = effective

    chunks = []
    for indexes in index_chunks:
        tokens = fixed_tokens + sum(costs[i] for i in indexes)
        over = tokens > effective
        if over:
            report["over_budget_chunk"] = True
        report["chunks"].append({
            "trials": len(indexes),
            "tokens_estimated": tokens,
            "over_budget": over,
        })
        chunks.append([priced[i][0] for i in indexes])
    return chunks, report


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
        # None, never 0: this entry was BUILT here and never stood in a model
        # response, so it has no emission position and no answering call. 0
        # would name the first entry of the first call, which is a real place
        # some other trial occupies. See the stamp in the parse loop.
        "emission_index": None,
        "call_index": None,
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
            NOT_EVALUABLE_CONFLICTING_DUPLICATES:
                "The model returned more than one evaluation for this trial "
                "and they disagreed on the verdict. Not assessed.",
            NOT_EVALUABLE_CALL_FAILED:
                "The request carrying this trial did not produce a response, "
                "so the model never answered for it. Not assessed.",
        }[reason],
    }


# ---------------------------------------------------------------------------
# The stored assessment is composed, not quoted (PROMPT_VERSION 1.5.0)
# ---------------------------------------------------------------------------
#
# WHAT WAS WRONG. Section 5 orders the model to write `assessment` FIRST, as its
# reasoning, and that draft was then stored verbatim as the trial's assessment.
# Those are two jobs and the draft is only good at one of them. Audited
# assessments contradicted their own criteria arrays in three ways: they called
# a field "not documented" while the arrays quoted a value for it from the
# record; they named numeric thresholds that appear nowhere in the trial's
# criteria text; and one emitted both mandated openings at once ("No known
# disqualifiers" and "Known disqualifier:"). The arrays were right in every one
# of those cases. The stored prose was not, and the stored prose is what a
# reader sees.
#
# WHY NOT JUST REORDER THE EMISSION. Because it is not available. Strict
# Structured Outputs emits a trial object's keys ALPHABETICALLY, regardless of
# the schema's `properties` order -- measured, and argued at length in
# oncotriage/agent/response_schema.py. `assessment` sorts before `eligible` and
# before both criteria arrays, so there is no arrangement of field names that
# lets the model write its criteria before its prose. Reasoning-first is
# therefore kept: the draft still decides the verdict, and
# `reasoning_order_regression` still watches for a response that inverted it.
#
# WHAT IS DONE INSTEAD. The model contract is unchanged -- same fields, same
# order, same schema -- and the STORED assessment for an `eligible` or
# `not_eligible` trial is composed here, mechanically, out of the criterion /
# patient_value / status rows the model returned. A composed assessment cannot
# assert anything the arrays do not carry, because there is no other input to
# it. The draft is kept beside it under `assessment_draft`, IN MEMORY ONLY:
# no database column was added, so it reaches node_finalize, the API response
# and a run artifact, and it does not reach `trial_matches`.
#
# WHAT THAT COSTS, STATED RATHER THAN GLOSSED. The reason given for adding no
# column was that the draft is already durable in
# inferences.llm_classifier_raw_response -- and THAT IS TRUE ONLY OF A RUN THAT
# MADE ONE CALL. `response_text` above is ASSIGNED per chunk, not appended, so
# a run that split (a truncation, or an over-ceiling estimate) stores the LAST
# chunk's raw text and nothing else. The drafts of every trial in every earlier
# chunk are then unrecoverable from the database. That is a pre-existing
# property of the raw-response column, not something this change introduced --
# a split run's stored "raw response" has never contained most of its own
# verdicts -- but this change is the first thing to depend on it, so it is
# recorded here rather than left for a reader to discover from a missing
# answer. The fix is to accumulate the chunks into that column; it is a change
# to a stored column's contents and belongs to its own pass.
#
# A `not_evaluable` trial's arrays are EMPTY BY CONTRACT (Section 1), so there
# is nothing to compose from and the model's own text -- which the prompt
# requires to open "Not evaluable:" and to say what was missing from the
# TRIAL's criteria text, not from the patient's record -- is kept unchanged.

# The three mandated openings, as constants rather than as literals typed at
# each site. The first two are what this module now WRITES; all three are what
# the prompt tells the model to write, and the pair has to stay in step -- a
# composed opening that no longer matches the prompt's instruction would make
# the stored text disagree with the draft it replaced for a formatting reason.
ASSESSMENT_ELIGIBLE_OPENING = "No known disqualifiers."
ASSESSMENT_NOT_ELIGIBLE_OPENING = "Known disqualifier:"
ASSESSMENT_NOT_EVALUABLE_OPENING = "Not evaluable:"

# The clause an eligible assessment carries when the model recorded criteria it
# could not evaluate. It is the ONLY source of a "not documented" claim in a
# composed assessment.
ASSESSMENT_UNDOCUMENTED_OPENING = "Not documented in the patient record:"

# THE ONE patient_value that licenses that clause. Section 5 mandates this exact
# string, and it is deliberately NOT `_is_absent_patient_value` from the
# absent-data validator below: that predicate carries twenty synonyms and nine
# prefixes, on purpose, because its job is to CATCH a disqualification the model
# should not have made. Reusing it here would invert its direction -- a
# free-written patient_value would become a positive claim about the record in
# text a clinician reads. Whitespace and case are tolerated because they are
# transcription, not vocabulary; nothing else is.
ASSESSMENT_UNDOCUMENTED_PATIENT_VALUE = "Not in patient record"

# status -> how a composed line words it. The two disqualifying statuses, one
# per arm (Section 1). Both arms are scanned for both statuses: by the time
# this runs, `_normalize_arm` has already resolved a cross-arm status away, so
# in the pipeline the map is per-arm in effect -- but this function is pure and
# is unit-tested on synthetic verdicts, and a renderer that silently dropped a
# row it was handed would be reporting fewer disqualifiers than the record
# holds.
_DISQUALIFYING_STATUS_PHRASES = {
    "not_met": "not met",
    "violated": "violated",
}

_NOT_EVALUABLE_STATUS = "not_evaluable"

# What compose_assessment did, as a closed vocabulary. A caller counts these so
# the path taken is recorded rather than inferred -- and so the two members that
# should be UNREACHABLE in the pipeline are visible if they ever occur.
ASSESSMENT_COMPOSED_ELIGIBLE = "composed_eligible"
ASSESSMENT_COMPOSED_NOT_ELIGIBLE = "composed_not_eligible"
# The corrected rejection. A COMPOSED case, not a kept one and not an anomaly:
# the node produced this verdict deliberately and knows exactly what to say
# about it, which is the definition of composable here. See the text constant
# below for why keeping the draft was not an option.
ASSESSMENT_COMPOSED_UNSUPPORTED_REJECTION = "composed_unsupported_rejection"
# The other corrected rejection -- the one whose disqualifying labels were out
# of vocabulary. A SEPARATE member rather than a second marker routed to the
# case above, because the two populations know different things about the
# model's arrays and therefore store different sentences; folding them together
# would make the log line report a correction it cannot distinguish.
ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR = "composed_remap_no_survivor"
ASSESSMENT_KEPT_NOT_EVALUABLE = "kept_draft_not_evaluable"
ASSESSMENT_KEPT_NO_DISQUALIFIER = "kept_draft_no_disqualifying_row"
ASSESSMENT_KEPT_UNKNOWN_VERDICT = "kept_draft_unknown_verdict"

ASSESSMENT_CASES = (
    ASSESSMENT_COMPOSED_ELIGIBLE,
    ASSESSMENT_COMPOSED_NOT_ELIGIBLE,
    ASSESSMENT_COMPOSED_UNSUPPORTED_REJECTION,
    ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR,
    ASSESSMENT_KEPT_NOT_EVALUABLE,
    ASSESSMENT_KEPT_NO_DISQUALIFIER,
    ASSESSMENT_KEPT_UNKNOWN_VERDICT,
)

# The four that WRITE the stored assessment rather than keeping the model's
# draft. Named rather than spelled out at each site, because the composition
# pass computes `kept` as `total - composed` and a member added to the
# vocabulary without being added here would be silently counted as kept.
ASSESSMENT_COMPOSED_CASES = (
    ASSESSMENT_COMPOSED_ELIGIBLE,
    ASSESSMENT_COMPOSED_NOT_ELIGIBLE,
    ASSESSMENT_COMPOSED_UNSUPPORTED_REJECTION,
    ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR,
)

# WHAT A CORRECTED REJECTION STORES. Fixed text, no interpolation, and every
# word of it is a statement about what the NODE did rather than about the
# patient: the model rejected the trial, no row in either array carried a
# disqualifying status, and the verdict was corrected. It contains no clinical
# claim and no digits, so it cannot assert anything the arrays do not carry --
# the same property the two quoting compositions have by construction.
#
# WHY THE DRAFT COULD NOT BE KEPT, which is the whole reason this case exists.
# A corrected rejection is the ONE not_evaluable population whose arrays are
# full and whose draft is a rejection: the model wrote "Known disqualifier:
# ..." and the stored verdict says the trial was not evaluated, so the row
# contradicted itself in the column a clinician reads. Every other
# not_evaluable population is safe to keep -- a model-declared one has empty
# arrays by contract and a draft that already opens "Not evaluable:", and the
# four this node CONSTRUCTS carry purpose-written text from _unevaluable_entry.
#
# It opens with the same ASSESSMENT_NOT_EVALUABLE_OPENING the prompt mandates
# for a model-written non-evaluation, so a reader scanning the column does not
# have to learn a fourth opening to recognise a non-evaluation.
ASSESSMENT_UNSUPPORTED_REJECTION_TEXT = (
    f"{ASSESSMENT_NOT_EVALUABLE_OPENING} The model rejected this trial but "
    "cited no disqualifying criterion in either criteria array, so the "
    "verdict was corrected to not evaluable."
)

# WHAT THE OTHER CORRECTED REJECTION STORES, and it is deliberately WEAKER than
# the sentence above rather than a paraphrase of it. Same construction -- fixed
# text, no interpolation, no digits, no clinical claim -- and the same opening,
# so a reader scanning the column recognises a non-evaluation without learning
# a fourth opening.
#
# THE DIFFERENCE IS THE ONLY THING THAT MATTERS HERE. This population's
# disqualifying evidence, if there was any, was written in a label the arm's
# vocabulary does not contain, and `_normalize_arm` refuses to guess what the
# model meant by it. So this text may NOT say the model cited no disqualifying
# criterion: that is the one claim the evidence cannot support, and it is the
# claim the sentence above makes. It may not say the remapped rows were the
# disqualifiers either -- `remapped_here` is true for a remap on any row, and
# for a non-dict entry that was dropped rather than relabelled. It says what is
# left: the rejection was made, nothing now supports it, the verdict moved.
# See UNEVALUABLE_REMAP_NO_SURVIVOR for the argument in full.
#
# The arrays go on being stored verbatim in criterion_details, remapped
# statuses included, so a reader who wants to know what the model wrote reads
# them rather than this line.
ASSESSMENT_REMAP_NO_SURVIVOR_TEXT = (
    f"{ASSESSMENT_NOT_EVALUABLE_OPENING} The model rejected this trial, but "
    "after label normalisation no row in either criteria array carried a "
    "disqualifying status, so the verdict was corrected to not evaluable."
)

# The two that cannot happen if the node's own normalizer ran: Step 3 sets
# `not_eligible` only when a surviving row carries a disqualifying status, and
# Step 0 resolves every verdict into the three-member trial vocabulary. Counted
# module-level, on the AGE_PARSE_FAILURES footing, because they would mean the
# composition ran against a verdict the normalizer had not produced.
#
# THAT CLAIM WAS TRUE OF STEP 3's ASSIGNMENT AND FALSE OF THE NODE, until
# UNEVALUABLE_REJECTION_UNSUPPORTED. Step 3 never WROTE an unsupported
# `not_eligible` -- and the fall-through branch beneath it let the model's own
# unsupported `not_eligible` PASS, which reached this composition as
# KEPT_NO_DISQUALIFIER just the same. Measured on real runs at 6 of 54
# rejections, so this counter was not a backstop against a state that could not
# occur; it was the only thing recording one that did. The normalizer now
# corrects that entry to `not_evaluable` before composition sees it, which is
# what finally makes the sentence above true of the whole node. The counter
# stays, unweakened, as the detector for whatever reintroduces it.
ASSESSMENT_COMPOSITION_ANOMALIES = Counter()

_ASSESSMENT_ANOMALY_CASES = (ASSESSMENT_KEPT_NO_DISQUALIFIER,
                             ASSESSMENT_KEPT_UNKNOWN_VERDICT)


def _criteria_rows(verdict: Dict):
    """[(arm, row)] over both criteria arrays, inclusion first, in array order.

    Non-list arrays and non-dict rows are skipped rather than raised on: this
    runs after `_normalize_arm` has already dropped both shapes in the pipeline,
    and the function must be total over a hand-built dict in a test.

    INCLUSION BEFORE EXCLUSION, which is the order `_compute_match_score` walks
    the arms and the order Step 3 checks them -- not the alphabetical order the
    decoder emits them in. Either is deterministic; this one keeps every place
    in the file that iterates both arms reading the same way.
    """
    for arm, key in (("inclusion", "inclusion_criteria"),
                     ("exclusion", "exclusion_criteria")):
        rows = verdict.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                yield arm, row


def _row_text(row: Dict, field: str) -> str:
    """One quoted field of one criterion row, as a string, whitespace-trimmed.

    Trimming is the ONLY transformation applied to model text on its way into a
    composed assessment. Nothing is rephrased, truncated, capitalised or
    re-punctuated: the composed text's whole claim is that every clinical
    statement in it was written by the model into the arrays, and a renderer
    that edited the words would be making that claim about words it had changed.
    """
    value = row.get(field, "")
    return value.strip() if isinstance(value, str) else str(value).strip()


def _is_undocumented_row(row: Dict) -> bool:
    """True for a row that says, in the arrays, that the record has no data.

    Both halves are required. A `not_evaluable` status alone does not license
    the claim -- the prompt's RULE 1 also produces it for a criterion whose
    components are partly documented -- and the canonical patient_value alone
    does not either, because a row carrying it under any other status is a row
    whose status contradicts its own value.
    """
    return (row.get("status") == _NOT_EVALUABLE_STATUS
            and _row_text(row, "patient_value").casefold()
            == ASSESSMENT_UNDOCUMENTED_PATIENT_VALUE.casefold())


def assessment_composition_case(verdict: Dict) -> str:
    """Which member of ASSESSMENT_CASES applies to this verdict.

    Separated from compose_assessment so the caller can COUNT the path taken
    without re-deriving the branch, and so the two cannot disagree:
    compose_assessment calls this and branches on its answer. One decision, two
    entry points.
    """
    label = verdict.get("eligible")
    if label == TRIAL_VERDICT_NOT_EVALUABLE:
        # BEFORE the kept-draft answer, and only for the two markers this node
        # writes itself. Every other not_evaluable entry -- model-declared with
        # empty arrays, or one of the four _unevaluable_entry constructs --
        # falls through to the line below and keeps its text, which is the
        # behaviour that was already correct for them.
        #
        # TWO MARKERS, TWO CASES, ONE PER CORRECTED REJECTION. Both populations
        # are a rejection whose stored verdict is not_evaluable and whose draft
        # therefore contradicts it; they differ in what may be said about the
        # arrays, so they cannot share a case without one of them storing a
        # sentence its evidence does not support.
        reason = verdict.get("not_evaluable_reason")
        if reason == UNEVALUABLE_REJECTION_UNSUPPORTED:
            return ASSESSMENT_COMPOSED_UNSUPPORTED_REJECTION
        if reason == UNEVALUABLE_REMAP_NO_SURVIVOR:
            return ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR
        return ASSESSMENT_KEPT_NOT_EVALUABLE
    if label == TRIAL_VERDICT_ELIGIBLE:
        return ASSESSMENT_COMPOSED_ELIGIBLE
    if label == TRIAL_VERDICT_NOT_ELIGIBLE:
        for _arm, row in _criteria_rows(verdict):
            if row.get("status") in _DISQUALIFYING_STATUS_PHRASES:
                return ASSESSMENT_COMPOSED_NOT_ELIGIBLE
        # A rejection with nothing in the arrays to justify it. Composing
        # "Known disqualifier:" here would fabricate the one thing this whole
        # mechanism exists to stop: a claim the arrays do not carry.
        return ASSESSMENT_KEPT_NO_DISQUALIFIER
    return ASSESSMENT_KEPT_UNKNOWN_VERDICT


def compose_assessment(verdict: Dict) -> str:
    """The text to STORE as this trial's assessment.

    PURE. It reads one dict and returns a string; it opens nothing, counts
    nothing and mutates nothing, so it is unit-testable on a literal and the
    caller owns the recording of which case fired.

    For the two composed cases the returned text is a function of the criteria
    arrays alone, which is the property the whole change rests on: every
    clinical statement in it was written by the model into a `criterion` or a
    `patient_value`, and no number, threshold, unit or "not documented" claim
    can enter it from anywhere else. The scaffolding words this function adds
    contain no digits, so a numeric token in the output came from a row.

    For the three kept cases it returns `verdict["assessment"]` UNCHANGED --
    the model's draft. That is not a silent fallback: the caller asks
    assessment_composition_case() for the same answer and logs it, and the two
    unreachable cases also land in ASSESSMENT_COMPOSITION_ANOMALIES.
    """
    case = assessment_composition_case(verdict)

    if case == ASSESSMENT_COMPOSED_UNSUPPORTED_REJECTION:
        # A constant, not a rendering: there is nothing in the arrays to quote
        # -- their emptiness of disqualifiers IS the finding -- so quoting any
        # row would be padding the text with criteria that had nothing to do
        # with the correction.
        return ASSESSMENT_UNSUPPORTED_REJECTION_TEXT

    if case == ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR:
        # A constant for the same reason, and one further one: the rows that
        # WERE remapped are the only ones a rendering could plausibly quote,
        # and quoting them would assert they carried the disqualification --
        # the claim ASSESSMENT_REMAP_NO_SURVIVOR_TEXT exists to avoid making.
        return ASSESSMENT_REMAP_NO_SURVIVOR_TEXT

    if case == ASSESSMENT_COMPOSED_NOT_ELIGIBLE:
        sentences = []
        for arm, row in _criteria_rows(verdict):
            phrase = _DISQUALIFYING_STATUS_PHRASES.get(row.get("status"))
            if phrase is None:
                continue
            sentences.append(
                f'{arm.capitalize()} criterion "{_row_text(row, "criterion")}" '
                f'{phrase}; patient record: '
                f'"{_row_text(row, "patient_value")}".')
        return f"{ASSESSMENT_NOT_ELIGIBLE_OPENING} " + " ".join(sentences)

    if case == ASSESSMENT_COMPOSED_ELIGIBLE:
        undocumented = [f'"{_row_text(row, "criterion")}"'
                        for _arm, row in _criteria_rows(verdict)
                        if _is_undocumented_row(row)]
        if not undocumented:
            # No clause at all, rather than an empty one. "Not documented in
            # the patient record: " with nothing after it reads as a truncated
            # sentence and would be the only place in this output where a
            # reader could not tell a rendering fault from a finding.
            return ASSESSMENT_ELIGIBLE_OPENING
        return (f"{ASSESSMENT_ELIGIBLE_OPENING} "
                f"{ASSESSMENT_UNDOCUMENTED_OPENING} "
                + "; ".join(undocumented) + ".")

    draft = verdict.get("assessment", "")
    return draft if isinstance(draft, str) else str(draft)


# ---------------------------------------------------------------------------
# Suspect temporal conflicts (RULE 4): DETECTED AND COUNTED, NEVER REWRITTEN
# ---------------------------------------------------------------------------
#
# WHAT THE MODEL IS SUPPOSED TO DO. The system prompt's RULE 4 is explicit:
# where a criterion requires an ACTIVE or CURRENT condition and the record shows
# that condition resolved, inactive or in remission, the status is
# "not_evaluable" on an inclusion and "not_violated" on an exclusion -- never
# "not_met" and never "violated". A resolved condition is not evidence that a
# criterion about a current one was failed.
#
# WHAT IT SOMETIMES DOES INSTEAD, measured on real runs: an AML resolved in 1997
# marked "not_met" against a newly-diagnosed-AML criterion; a terminated
# pregnancy read as a current one; a concussion resolved in 2012 quoted to
# disqualify on active CNS leukaemia. Each is a rejection resting on a fact the
# record says is over.
#
# WHY THIS ONLY LOOKS. A simulation against an independent rater measured the
# precision of REWRITING these rows at 0.57 -- so an automatic correction would
# delete a correct rejection roughly two times in five. That is the same
# fabrication the unsupported-rejection correction exists to prevent, pointing
# the other way, and it is unacceptable at any volume. So this mechanism adds a
# key and counts; it changes no status, no verdict, no score and no assessment.
# tests/test_agent_temporal_conflict_flag.py proves that by running the node
# with the detector bypassed and diffing everything but the key.
#
# THREE LIMITS, STATED HERE RATHER THAN DISCOVERED LATER:
#
#   1. The two marker lists are HAND-AUTHORED and are a FLOOR, not a complete
#      family. "status post", "s/p", "no longer", "quiescent", "eradicated" and
#      "cured" all express the same thing and are not here. A row this predicate
#      does not match is not a row it has cleared.
#   2. The flag is a SIGNAL TO LOOK, not a judgement that the row is wrong. It
#      says two vocabularies co-occurred in one row, which is a correlation over
#      free text, not a reading of the record.
#   3. The independent rater ENDORSED some rows this predicate matches. A
#      genuinely correct rejection can quote a resolved condition -- a criterion
#      requiring an active infection is legitimately not met by a patient whose
#      infection resolved, and "not_met" is arguably right when the criterion is
#      phrased as a requirement rather than a question. That is precisely why
#      limit 2 holds and why nothing here rewrites.

TEMPORAL_CONFLICT_FIELD = "temporal_conflict_suspect"
"""The key added to a suspect criterion row, with the value ``True``.

ABSENT rather than ``False`` on every other row, which is this codebase's
convention for a detector that found nothing (``not_evaluable_reason``,
``emission_index``): a key that is always present says the detector ran on this
row, and nothing here can promise that for a row written before it existed.
Consumers must therefore read it with ``.get``.

THE MODEL CANNOT FORGE IT. The Stage 5 response schema is strict, with
``additionalProperties: false`` on the criterion object and a ``required`` list
naming exactly ``criterion``, ``patient_value`` and ``status``, so a row that
carries this key was written here. The test asserts that against the real schema
rather than assuming it, the way the unsupported-rejection marker already is.
"""

# The RESOLVED-STATE family, matched against the row's patient_value. Close
# inflections are enumerated rather than stemmed: a stemmer would also fold
# words nobody chose, and the whole value of a hand-authored list is that every
# member was argued for. Note what word boundaries buy for free -- "unresolved"
# does NOT match "resolved", because there is no boundary between "n" and "r",
# and "unresolved" means the opposite of every word in this tuple.
_RESOLVED_STATE_MARKERS = (
    "resolved", "resolve", "resolves", "resolving", "resolution",
    "remission",
    "inactive",
    "terminated", "terminate", "terminates", "terminating", "termination",
)

# The ACTIVE-REQUIREMENT family, matched against the row's criterion text.
# "inactive" does NOT match "active" here, for the same boundary reason, which
# is the one collision between the two lists and the one that would have made
# the predicate self-satisfying.
_ACTIVE_REQUIREMENT_MARKERS = (
    "active", "actively",
    "current", "currently",
    "newly diagnosed",
    "ongoing", "undergoing",
)


def _marker_pattern(marker: str) -> "re.Pattern":
    """One case-insensitive, word-boundary-anchored pattern for one marker.

    Internal whitespace becomes ``\\s+`` so "newly  diagnosed" and a marker
    broken across a line both match; the words themselves are escaped, so a
    marker is a literal and never a pattern a future editor has to think about.
    """
    return re.compile(
        r"\b" + r"\s+".join(re.escape(word) for word in marker.split()) + r"\b",
        re.IGNORECASE)


_RESOLVED_STATE_PATTERNS = tuple(
    (marker, _marker_pattern(marker)) for marker in _RESOLVED_STATE_MARKERS)
_ACTIVE_REQUIREMENT_PATTERNS = tuple(
    (marker, _marker_pattern(marker)) for marker in _ACTIVE_REQUIREMENT_MARKERS)

TEMPORAL_CONFLICT_RESOLVED_MARKERS = Counter()
"""Which resolved-state markers fired, cumulative over the process.

Keyed by OUR OWN vocabulary -- a member of ``_RESOLVED_STATE_MARKERS``, which is
a code identifier in every sense that matters -- and never by the text it
matched, which is model output about a patient. Same rule ``FIELD_DROPS``
follows for the field names it withholds.

DELIBERATELY NOT REGISTERED IN ``oncotriage/degradation.py``. Every counter in
that registry means something went wrong with the run, and its report reads "N
of M counters moved". This one moves on correct behaviour too -- limit 3 above
-- so a clean run with three suspect rows would report a degradation that did
not happen. It is an observation, not a degradation.

A ROW CONTRIBUTES EVERY MARKER IT MATCHED, not the first, because the question
these counters answer is which vocabulary members earn their place. So they sum
to at least the number of flagged rows and usually to more, and neither of them
is a row count. ``count`` in the log event is the row count.
"""

TEMPORAL_CONFLICT_ACTIVE_MARKERS = Counter()
"""Which active-requirement markers fired, cumulative. See the counter above."""


def _markers_in(text: str, patterns) -> List[str]:
    """Every marker of one family present in one string, in vocabulary order."""
    return [marker for marker, pattern in patterns if pattern.search(text)]


def temporal_conflict_markers(row: Dict):
    """``(resolved markers, active markers)`` for a suspect row, else ``None``.

    PURE. It reads one dict and returns a tuple or None; it mutates nothing,
    counts nothing and opens nothing, so it is unit-testable on a literal and
    the caller owns both the flag and the recording.

    All three conditions are required, and each is a separate gate the test
    exercises on its own:

      1. the status DISQUALIFIES -- ``not_met`` or ``violated``, read off
         ``_DISQUALIFYING_STATUS_PHRASES`` rather than respelled, so the two
         cannot drift. A ``not_evaluable`` row quoting a resolved condition is
         RULE 4 being obeyed and is not a finding;
      2. the patient_value carries a resolved-state marker;
      3. the criterion text carries an active-requirement marker.

    Both fields are read through ``_row_text``, so a non-string coerces rather
    than raising -- this runs after ``_normalize_arm`` in the pipeline but must
    be total over a hand-built dict in a test.
    """
    if not isinstance(row, dict):
        return None
    if row.get("status") not in _DISQUALIFYING_STATUS_PHRASES:
        return None
    resolved = _markers_in(_row_text(row, "patient_value"),
                           _RESOLVED_STATE_PATTERNS)
    if not resolved:
        return None
    active = _markers_in(_row_text(row, "criterion"),
                         _ACTIVE_REQUIREMENT_PATTERNS)
    if not active:
        return None
    return resolved, active


def detect_temporal_conflicts(evaluations) -> List[Dict]:
    """Flag every suspect criterion row. Returns the audit list.

    THE ONLY MUTATION IS THE ADDED KEY. No status, verdict, score, assessment
    or array membership is touched by this function or by anything it calls.

    Returns one record per flagged row -- nct_id, arm, the status that was left
    in place, and the markers of each family that fired. The records carry no
    patient_value and no criterion text: they feed a log event, and the audit of
    the text itself is ``criterion_details``, which stores the row with its flag.

    NOT IDEMPOTENT ON THE COUNTERS, deliberately. Calling it twice over one
    evaluation list re-flags rows that are already flagged (harmless, the key is
    already True) and counts their markers again (not harmless). It is not
    guarded, because a second call would be a defect and a guard would hide it;
    the node calls it exactly once, and the test asserts that against the source.
    """
    suspects: List[Dict] = []
    for evaluation in evaluations:
        if not isinstance(evaluation, dict):
            continue
        nct_id = evaluation.get("nct_id", "")
        for arm, row in _criteria_rows(evaluation):
            found = temporal_conflict_markers(row)
            if found is None:
                continue
            resolved, active = found
            row[TEMPORAL_CONFLICT_FIELD] = True
            for marker in resolved:
                TEMPORAL_CONFLICT_RESOLVED_MARKERS[marker] += 1
            for marker in active:
                TEMPORAL_CONFLICT_ACTIVE_MARKERS[marker] += 1
            suspects.append({
                "nct_id": nct_id,
                "arm": arm,
                "status": row.get("status"),
                "resolved_markers": resolved,
                "active_markers": active,
            })
    return suspects


def temporal_conflict_marker_counts(suspects: List[Dict]):
    """``(resolved, active)`` ``{marker: count}`` over ONE node call's suspects.

    Built from the returned audit list rather than read off the module counters,
    which are cumulative over the process: a log event describing this call must
    not report a total that includes the twenty patients before it.
    """
    resolved, active = Counter(), Counter()
    for record in suspects:
        resolved.update(record["resolved_markers"])
        active.update(record["active_markers"])
    return dict(sorted(resolved.items())), dict(sorted(active.items()))


def node_llm_classifier_evaluation(state: TrialMatchState) -> dict:
    """
    Stage 5: LLM classifier, criterion-level evaluation.

    HOW THE BATCH IS PARTITIONED IS CONFIGURED, in three layers, and the
    sentence that stood here -- "sends ALL filtered trials to the classifier in
    a SINGLE call" -- has been false since input packing and is false twice
    over now:

      * ``MATCHING_PER_TRIAL_CALLS_ENABLED`` (default False) makes it ONE
        request per trial, bypassing the packer, behind a dedicated cache
        warmup that is awaited alone -- no trial call is ever issued without a
        warm shared prefix ahead of it, and a warmup that cannot be
        established fails the patient rather than sending the wave uncached;
      * otherwise ``MATCHING_INPUT_PACKING_ENABLED`` (default True) groups
        trials into chunks under an input-token ceiling;
      * with both off it is the single whole-batch call it originally was.

    The two OUTPUT splitters -- the pre-split on an output estimate and the
    reactive halving on ``finish_reason == "length"`` -- are armed in all three
    and compose with whichever produced the first generation of chunks.

    Which model answers comes from ``config.MATCHING_MODEL`` and is NOT named
    here: this node was called ``node_gpt4o_evaluation`` while the judge was
    gpt-4o, the judge became gpt-5.6-terra on 2026-08-04, and the name went
    stale in place. The classifier evaluates every inclusion/exclusion
    criterion for each trial and returns structured JSON with match scores and
    explanations.

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

    # The trials are rendered by _build_trials_text, per chunk, in
    # _user_prompt_for below. Only eligibility criteria are sent: title,
    # conditions, brief summary and interventions are stripped so the judge
    # cannot perform its own disease relevance check. Relevance is enforced
    # upstream by hybrid retrieval, cross-encoder reranking and -- when it ran
    # -- the MeSH site filter. Whether it ran is what Section 2 below is
    # conditional on.
    #
    # A SECOND, DEAD COPY OF THAT RENDERER STOOD HERE and is deleted. It built
    # a local `trials_text` over the WHOLE batch that nothing ever read --
    # measured by AST, not by eye: zero Load references to the name in this
    # function, the only reference being the `+=`'s own. So every Stage 5 call
    # formatted the entire candidate set into a string and discarded it. It was
    # harmless while it agreed with _build_trials_text and stopped being
    # harmless the moment the header changed: a stale duplicate of the exact
    # text under edit, sitting above a comment that points at it, is a false
    # statement about what the model is sent.

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
    #   - Only eligibility criteria text is sent (see _build_trials_text and
    #     the note above), and RULE 3's categorically-different-diseases branch already
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
    #
    # THE PATIENT RECORD IS IN IT AS OF PROMPT_VERSION 1.6.0, which is what
    # makes "one hash per inference" a statement about the patient as well as
    # about the template, and what makes the system message the CACHED PREFIX:
    # every chunk of one patient sends these identical bytes, so the provider
    # discounts them from the second request on.
    #
    # NEUTRALIZED FIRST, by the same function and for the same reason
    # _build_trials_text neutralizes trial text. The record is assembled from
    # FHIR values this project does not author, it is about to be placed inside
    # the message C6 calls the only source of instructions, and a fence whose
    # own body can spell the closing marker is not a boundary. On every real
    # patient this changes nothing -- a summary contains no bracket runs -- and
    # the count is logged when it does.
    patient_record, _record_runs = _neutralize_fence_markers(patient_summary)
    if _record_runs:
        log.warning("neutralized a fence marker inside the patient record",
                    stage=5, node="llm_classifier_evaluation",
                    event="patient_record_fence_marker_neutralized",
                    count=_record_runs)

    system_prompt = render_system_prompt(
        mesh_filter_applied=_mesh_filter_applied,
        mesh_filter_skip_reason=_mesh_filter_reason,
        patient_record=patient_record,
    )
    # The mechanical record of what was actually sent, beside PROMPT_VERSION's
    # record of what was intended. Computed here rather than at logging time so
    # it is the hash of the string this node handed the model, not a re-render
    # from state that could disagree with it.
    system_prompt_sha256 = prompt_sha256(system_prompt)

    # ── How much of the fixed prefix is THIS PATIENT ──────────────────────
    #
    # MEASUREMENT ONLY. Nothing below reads this value: it selects no chunk,
    # moves no budget and changes no prompt byte. It is provenance, on exactly
    # the footing of system_prompt_sha256 one line above, and it is computed
    # here for the same two reasons -- it is a fact about the string this node
    # actually built, and the string is built ONCE above the split loop, so a
    # run that splits into N requests still has one patient record and reports
    # one number for it.
    #
    # THE SUBJECT IS `patient_record`, THE NEUTRALIZED TEXT, and that is the
    # whole point of measuring here rather than from `patient_summary`. The
    # neutralized string is what render_system_prompt() interpolates and
    # therefore what the model is charged for; on a summary carrying a fence
    # marker the two differ in length, and a number taken from the raw text
    # would describe a string that was never sent. The fence LINES themselves
    # (<<<PATIENT_RECORD>>> and its closer) are template, not record, and are
    # deliberately outside this measurement -- they are constant, so counting
    # them here would make a per-patient figure carry a per-template constant.
    #
    # THROUGH THE PIPELINE'S OWN ESTIMATOR, never a second formula. This is the
    # same characters/CHARS_PER_TOKEN proxy fixed_input_tokens below is built
    # from, so "record tokens" and "fixed tokens" are commensurable and a reader
    # can subtract one from the other. A private len()//4 here would be free to
    # drift from the estimator the packer actually spends its budget with.
    #
    # THE TEMPLATE'S OWN SHARE IS NOT STORED, DELIBERATELY. It is
    # fixed_input_tokens minus this value minus the user-message wrapper, all
    # three of which a reader already has, and a second column carrying a
    # derived quantity is a second copy of one fact that can go stale on its
    # own. One measurement, one home.
    #
    # RE-ESTIMATING FROM A STORED RECORD MATCHES THIS FIELD EXACTLY WHEN
    # _record_runs IS 0, which is every real patient to date: the run harness
    # persists `patient_summary` (the PRE-neutralization text, see
    # run_harness.build_record), and neutralization is the identity on a string
    # with no bracket run. When it is not 0 the two legitimately differ, the
    # warning above says so, and this field is the one that describes the send.
    patient_record_tokens = estimate_prompt_tokens(patient_record)


# ================================================================
# USER MESSAGE
# ================================================================

    # THE PATIENT RECORD IS NOT HERE ANY MORE (PROMPT_VERSION 1.6.0). The user
    # message carries this chunk's fenced trials and nothing else, so the only
    # thing that differs between two requests of one patient is the part after
    # the cached prefix. The `CLINICAL TRIALS:` heading stays: it is a
    # structural label rather than patient data, and it is the anchor the
    # dashboard's stored-prompt reader already keys on.
    def _wrap_trials(trials_text: str) -> str:
        """The user message around an ALREADY-RENDERED trials block.

        Split out of ``_user_prompt_for`` so the stored prompt below can be
        assembled from the one render this node makes instead of provoking a
        second one. The template is unchanged to the byte -- that is what makes
        ``_wrap_trials("".join(blocks))`` and the old
        ``_user_prompt_for(trials)`` the same string, which is asserted in
        tests/test_agent_stage5_render_slice_equality.py rather than assumed.
        """
        return f"""
CLINICAL TRIALS:
{trials_text}
"""

    def _user_prompt_for(chunk: List[Dict], *,
                         log_events: bool = True) -> str:
        """Render ``chunk`` and wrap it. The send path, unchanged.

        ``log_events`` is forwarded so the wrapper-pricing call below can state
        that it is a MEASUREMENT render. It defaults to True, so the sent
        render -- the only other caller -- is loud exactly as before.
        """
        return _wrap_trials(_build_trials_text(chunk, log_events=log_events))

    # ── The ONE render of this batch ──────────────────────────────────────
    # Every trial in the batch, rendered once, LOUDLY: this is the text the
    # stored prompt is made of, so its events and its two refusal counts are a
    # true record of third-party text on its way to the judge.
    #
    # IT FEEDS TWO CONSUMERS AND USED TO FEED ONE. The stored prompt below, and
    # the packer's per-trial cost measurement, which used to render every trial
    # AGAIN. Blocks are a pure function of their trial (see
    # _render_trial_blocks), so slicing is exact rather than approximate, and
    # the packer's arithmetic is still a statement about bytes that will be
    # sent.
    trial_blocks = _render_trial_blocks(trials)

    # ── Store full prompt for DB logging (system + user combined) ──────────
    # The WHOLE batch, not the chunk that happened to be sent last. When a run
    # splits, the stored prompt is the one the run would have sent unsplit,
    # which is the thing that is comparable across runs; the split itself is
    # recorded in llm_classifier_truncation_splits, not by mutating this.
    #
    # THE CONVENTION SURVIVES 1.6.0 UNCHANGED, and that is the reason this line
    # is untouched. "The prompt the run would have sent unsplit" is still
    # exactly what it holds: the system message, which is byte-identical on
    # every request this node makes, and the user message for the WHOLE batch,
    # which is what an unsplit run would have sent. What moved is which side of
    # the [SYSTEM]/[USER] marker the patient record sits on -- so the column
    # still round-trips to one well-defined request, and a reader that wants
    # the record now finds it above the marker instead of below it. The
    # PATIENT RECORD block carries its own <<<PATIENT_RECORD>>> /
    # <<<END_PATIENT_RECORD>>> delimiters precisely so that reader does not have
    # to guess where it ends.
    prompt = (f"[SYSTEM]\n{system_prompt}\n\n[USER]\n"
              f"{_wrap_trials(''.join(trial_blocks))}")

    # Everything a request carries whatever chunk is in it: the system message
    # in full, and the user message's wrapper with no trials in it. Measured
    # rather than approximated -- _user_prompt_for([]) IS the wrapper, so this
    # cannot drift from the template the way a hand-counted allowance would.
    # log_events=False because this render exists to be MEASURED and is never
    # sent. It renders zero trials, so it emits nothing and counts nothing
    # whatever the flag says; passing it states which KIND of render this is
    # rather than which lines happened to be reachable, and it is what keeps
    # the flag and _SilentLog a mechanism with a live subject.
    fixed_input_tokens = (estimate_prompt_tokens(system_prompt)
                          + estimate_prompt_tokens(
                              _user_prompt_for([], log_events=False)))

    # ------------------------------------------------------------------
    # Which call mode this patient runs in
    # ------------------------------------------------------------------
    #
    # READ ONCE, HERE, INTO A LOCAL, AND EVERY BRANCH BELOW READS THE LOCAL.
    # The constant cannot change during one patient, and a single read is what
    # makes that true of the code as well as of the configuration: a node that
    # asked the module again at the send loop could partition one way and
    # dispatch the other if anything rebound it in between.
    #
    # THE BOUND IS VALIDATED HERE AND ONLY WHEN IT WILL BE USED, before the
    # first request of the patient, where a refusal costs nothing. Validating
    # it in grouped mode would let a typo in a constant this mode never reads
    # fail a campaign that does not use it.
    #
    # THROUGH config.matching_call_mode() AND NOT THROUGH A from-IMPORT, which
    # is the ONE place this pass departs from MATCHING_INPUT_PACKING_ENABLED's
    # precedent, deliberately and for a reason that constant does not have.
    # `inferences.matching_call_mode` is written by
    # oncotriage/storage/database_logger.py from the SAME function, so the row
    # and the node cannot name different modes. A `from oncotriage.config
    # import MATCHING_PER_TRIAL_CALLS_ENABLED` here would BIND the value at
    # import, so anything that set it on the config module within a process --
    # a probe, a test, an operator in a REPL -- would move the column and not
    # the node, and every row of that run would describe a partition that never
    # happened. That is precisely the patch-point defect
    # tests/test_agent_rrf_config_ownership.py exists for, and
    # matching_provider's note in INFERENCE_COLUMN_ADDITIONS states the general
    # rule: a constant that can move WITHIN a process must not be reached
    # through a bound name.
    #
    # The bound is read live for the same reason and at the same moment, so a
    # patient's partition and its dispatch are decided from one reading.
    _per_trial_calls = (config.matching_call_mode()
                        == MATCHING_CALL_MODE_PER_TRIAL)
    _parallel_bound = config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS
    # THE PROVIDER IS VALIDATED HERE FOR THE SAME REASON THE BOUND IS: before
    # the first request of the patient, where a refusal costs nothing and names
    # the constant. Per-trial mode's cache warmup is built for the OpenAI
    # surface only; see assert_per_trial_provider_supported.
    if _per_trial_calls:
        assert_per_trial_provider_supported()
    if _per_trial_calls and _parallel_bound < 1:
        raise PerTrialParallelismError(
            "MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS must be >= 1 when "
            "MATCHING_PER_TRIAL_CALLS_ENABLED is True; it is "
            f"{_parallel_bound!r}. Use 1 for sequential "
            "per-trial calls, or set MATCHING_PER_TRIAL_CALLS_ENABLED = False.")

    # ------------------------------------------------------------------
    # Packing: bound the INPUT before the output splitters see the batch
    # ------------------------------------------------------------------
    #
    # FIRST, AND THE ORDER IS THE DESIGN. Packing produces contiguous chunks
    # under an input ceiling; the pre-split below then halves any of THOSE whose
    # output estimate is still too large, and the reactive splitter halves
    # further if a response is actually cut off. Running the halving first and
    # packing inside it would give the same partition on ordinary input and a
    # worse one in general -- a halved chunk is not a chunk the input budget
    # chose -- and it would make the pre-split's depth accounting describe
    # something other than the chunks that were sent.
    #
    # OFF REPRODUCES THE OLD BEHAVIOUR EXACTLY: initial_chunks is [trials], the
    # single-element list the pre-split loop has always started from, and every
    # branch below is the code that was already here.
    #
    # ── PER-TRIAL MODE TAKES PRECEDENCE OVER THE PACKER ────────────────────
    #
    # AND IT BYPASSES IT RATHER THAN CONFIGURING IT. A budget of one token
    # would produce nearly this partition and would be the wrong mechanism
    # twice over: llm_classifier_packing would report a packer that RAN, which
    # is the fact the provenance has to be able to deny, and the packer's own
    # invariant -- never drop a trial -- means a single trial larger than the
    # budget still ships in a chunk with its neighbour rather than alone.
    # Bypassing is what makes "one trial per request" true by construction.
    #
    # THE ORDER OF THE THREE BRANCHES IS THE PRECEDENCE. `elif` rather than a
    # second `if`, so packing cannot re-partition what this branch decided, and
    # so the two existing branches are reached under exactly the condition they
    # were reached under before -- which is what section 6 of
    # tests/test_agent_stage5_per_trial_calls.py compares as bytes.
    if _per_trial_calls:
        initial_chunks = [[t] for t in trials]
        # NOT `enabled: True` WITH ONE TRIAL PER CHUNK. The packer did not run;
        # its budget selected nothing and its cap relaxed nothing, so every
        # numeric field it would have filled is None here for the same reason
        # the packing-OFF branch below leaves them None.
        #
        # `bypassed_by` IS PRESENT ONLY ON THIS BRANCH, which is the project's
        # absent-rather-than-empty convention (TEMPORAL_CONFLICT_FIELD's, in
        # the criteria arrays). Its absence on the other two branches means
        # "nothing bypassed the packer", and adding a `None` there would have
        # changed the stored JSON of every OFF-arm row for a fact those rows
        # already state by omission -- and would have broken this pass's own
        # byte-equivalence promise.
        packing_report = {"enabled": False, "method": PACKING_METHOD_CHARS,
                          "fixed_tokens": fixed_input_tokens,
                          "budget_tokens_configured": MATCHING_INPUT_TOKEN_BUDGET,
                          "budget_tokens": None, "max_chunks": None,
                          "cap_relaxed_budget": False,
                          "over_budget_chunk": False,
                          "trials": len(trials), "chunks": [],
                          "bypassed_by": MATCHING_CALL_MODE_PER_TRIAL}
        log.info("Stage 5 is issuing one request per trial", stage=5,
                 event="per_trial_calls", chunks=len(initial_chunks),
                 total=len(trials),
                 # The bound this patient will actually use, not the constant:
                 # a patient with three trials never reaches four in flight.
                 # NO LONGER `len - 1`: the cache is written by a dedicated
                 # warmup and no trial call is held back, so the whole wave is
                 # eligible for the pool. A patient that falls back to the
                 # one-then-rest schedule reaches one fewer, and the
                 # per_trial_warmup_rejected event beside it is what says so.
                 parallel=min(_parallel_bound, len(initial_chunks)))
    elif MATCHING_INPUT_PACKING_ENABLED:
        initial_chunks, packing_report = pack_trials_by_input_tokens(
            trials, fixed_input_tokens, MATCHING_INPUT_TOKEN_BUDGET,
            MATCHING_MAX_INPUT_PACKED_CHUNKS, blocks=trial_blocks)
        # tokens_estimated IS THE WHOLE BATCH'S INPUT, not the fixed overhead.
        # It is the number the threshold beside it is a threshold ON, and a
        # reader comparing the two is asking "how far over was this patient" --
        # which is unanswerable if the field carries the constant part instead.
        log.info("packed the Stage 5 request by input token estimate", stage=5,
                 event="input_packing", chunks=len(initial_chunks),
                 total=len(trials),
                 tokens_estimated=(fixed_input_tokens + sum(
                     c["tokens_estimated"] - fixed_input_tokens
                     for c in packing_report["chunks"])),
                 threshold=packing_report["budget_tokens"],
                 degraded=(packing_report["cap_relaxed_budget"]
                           or packing_report["over_budget_chunk"]))
    else:
        initial_chunks = [trials]
        # NOT None, and not an omitted key. "Packing did not run" is a fact the
        # provenance has to be able to state, and it is different from "packing
        # ran and produced one chunk" -- which is the comparison the validation
        # experiment is built on.
        packing_report = {"enabled": False, "method": PACKING_METHOD_CHARS,
                          "fixed_tokens": fixed_input_tokens,
                          "budget_tokens_configured": MATCHING_INPUT_TOKEN_BUDGET,
                          "budget_tokens": None, "max_chunks": None,
                          "cap_relaxed_budget": False,
                          "over_budget_chunk": False,
                          "trials": len(trials), "chunks": []}

    # ------------------------------------------------------------------
    # Proactive: split before sending if the batch is expected to overflow
    # ------------------------------------------------------------------
    estimated_output = estimate_output_tokens(trials)
    split_threshold = int(MATCHING_MAX_TOKENS * MATCHING_OUTPUT_SPLIT_FRACTION)

    pending = []          # LIFO of (chunk, split_depth), so a split is depth-first
    proactive_splits = 0
    # THE GUARD IS STILL THE WHOLE BATCH'S ESTIMATE, and that is safe rather
    # than sloppy: estimate_output_tokens is monotone in the trial set (its
    # count term is linear and its criteria term is a min of two non-decreasing
    # quantities), so no packed chunk can be over the threshold when the whole
    # batch is under it. Keeping the original guard is also what makes the OFF
    # arm byte-identical to the pre-packing node rather than merely equivalent.
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
        # DEPTH 0 FOR A PACKED CHUNK, WHICH IS A DECISION AND NOT AN ACCIDENT.
        # `depth` is the TRUNCATION-split budget: how many further HALVINGS a
        # chunk may spend when the model's answer is cut off. Packing is not a
        # halving and it does not address the output ceiling, so charging it a
        # level would take budget away from the only mechanism that can recover
        # a truncated response -- and it would do so on every packed run,
        # including the ones that never truncate. The pre-split above still
        # charges its own levels, exactly as before, because those ARE halvings
        # and they are performed for the same reason the reactive ones are.
        #
        # So a packed chunk enters the loop with the full MAX_TRUNCATION_SPLITS
        # available, the same as the whole batch used to. Packing only makes
        # truncation less likely: a smaller chunk produces a smaller response.
        pending = [(c, 0) for c in reversed(initial_chunks)]

    # ------------------------------------------------------------------
    # Evaluate, splitting reactively on finish_reason == "length"
    # ------------------------------------------------------------------
    evaluations = []
    unevaluable = []              # trials accounted for without a verdict
    # Entries the model returned for a trial that is in NO sent set at all.
    # One label per ENTRY, not per distinct id, so a model that invents the
    # same id twice is reported as two fabricated verdicts -- which is what it
    # produced. The list is what the log line names; its length is what reaches
    # inferences.hallucinated_trials.
    hallucinated_ids = []
    # Entries for a real candidate of this run that belongs to a DIFFERENT
    # chunk. Counted apart and deliberately not stored: nothing is lost, the
    # id's own chunk answers it. See the block above _partition_out_of_set.
    cross_chunk_ids = []
    # One record per collapsed duplicate id: {"nct_id", "case", "count"}.
    duplicate_ids = []
    # The candidate set of the WHOLE node, which classifies a drop but never
    # causes one, and the trial objects an unevaluable entry is built from.
    # Both are computed once here rather than per chunk: they are properties of
    # the batch, and rebuilding them inside the loop would be N scans of the
    # same list for a value that cannot change.
    _batch_ids = {t["trial"]["nct_id"] for t in trials}
    _trial_by_id = {t["trial"]["nct_id"]: t for t in trials}
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
    # Cached INPUT tokens, the provider's own report of how much of this
    # request's prefix it served from cache. A SUBSET of prompt_tokens, exactly
    # as reasoning is a subset of completion_tokens, and it is accumulated the
    # same way and for the same reason: it is the measurement that says whether
    # moving the patient record into the system message bought anything.
    #
    # Read defensively and reported as ABSENT rather than as 0 when no response
    # carried it -- a stub (File 37), a fixture recorded before the field
    # existed, and a provider that does not cache at all are three different
    # facts, and only the last of them is a genuine zero.
    #
    # NOT A COST TERM. get_model_cost() prices input at one rate; cached input
    # bills lower (PRICING_CONFIG's gpt-5.6-terra note records $0.20/1M against
    # $2.00/1M) and that discount is deliberately NOT modelled here. Subtracting
    # it would make estimated_cost_usd disagree with every historical row in the
    # same column. This is a measurement for the validation run.
    cached_input_tokens = 0
    cached_input_reported = False
    # THE SAME READINGS, PER CALL, NOT SUMMED. The four accumulators above are
    # totals over every request this stage made, and a total cannot answer the
    # question INPUT packing exists to raise: whether the shared system prefix
    # is served from cache on the SECOND and later chunks of one patient. One
    # cached figure of 5,000 across three calls is equally consistent with a
    # cache that warms after the first request and one that never warms at all,
    # and those have opposite implications for what packing costs.
    #
    # Each entry also carries the chunk's split DEPTH, so a first-generation
    # packed chunk (depth 0) is separable from a chunk the output splitters
    # halved out of it. Without it a run with one packed chunk and two reactive
    # splits is indistinguishable from a run the packer cut into three.
    #
    # Appended AFTER the usage reads and the answering-model check, so an entry
    # exists only for a call whose response was accepted as this model's, and
    # BEFORE the parse, so a call whose response was unusable is still recorded
    # as having been made and billed.
    call_details: List[Dict] = []
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

    def _billed_so_far() -> Dict:
        """The tokens this node has already been billed for, for a FAILURE return.

        WHY THIS EXISTS. Every early return below ends the node without a
        verdict, and every one of them used to end it without a token figure
        too -- so ``_pipeline_provenance()``'s ``state.get(..., 0)`` supplied a
        zero and the row recorded 0 input and 0 output tokens against requests
        that had been issued and billed. Six such rows are in the production
        database, each carrying ``llm_classifier_retries = 3`` beside two
        zeros. The accumulators above hold the true figure at every one of
        those returns; nothing was missing except the two lines that carry it.

        THE FIGURE IS NOT A PARTIAL TOTAL OF SOMETHING ELSE. It is the exact
        count of what this invocation was billed before it stopped, which is
        the only honest number available and is strictly better than a zero
        that asserts no spend.

        ``calls_made`` IS THE PRESENCE MARKER AND THE GUARD IS THE POINT.
        It is incremented immediately after a response is returned and
        immediately before ``response.usage`` is read, so ``calls_made == 0``
        means no usage object was ever obtained by this invocation -- the very
        first request raised before any response arrived. The tokens that
        request may have been billed are unknown TO THIS PROCESS and are not
        recoverable from anywhere inside it, so the keys are left ABSENT rather
        than written as 0. What the caller then stores is a 0 supplied by
        ``_pipeline_provenance()``, not by a measurement here; the two are
        separable in the row because ``llm_classifier_calls`` is written on
        every return now and reads 0 while ``llm_classifier_prompt_sha256`` is
        non-NULL, which is the signature of "Stage 5 ran and no call was
        counted". Estimating from prompt length instead would put a number in
        a measurement column that no provider ever reported.

        ``llm_classifier_calls`` IS CARRIED FOR THE SAME REASON THE TOKENS ARE.
        It was written on the success return only, so a refusal after a real
        billed call logged zero calls -- the identical defect one column over,
        and the column a reader needs in order to interpret the token figure
        beside it.

        ``matching_model`` IS NOT RETURNED BY THIS HELPER and is written at the
        API-error return instead, beside the spread. That is not tidiness: the
        other three failure returns already carry it with their own arguments
        for doing so, and returning it here would put a second, silent writer
        behind three keys that already have one. It had to be added to the
        API-error return by this change, though, and the reason is this
        change: a row carrying non-zero Stage 5 tokens with a NULL
        matching_model is the one shape File 16's Query 10, the dashboard cost
        tab and ``run_harness.price_result`` all single out as unpriceable, and
        that return was the one failure path not already naming the answering
        model. A model that answered an earlier chunk of this batch did answer
        this run.

        THE CACHED AND REASONING TOTALS ARE DELIBERATELY NOT CARRIED. Both are
        subsets of the two figures above and both are recorded PER CALL in
        ``llm_classifier_call_details``, which every failure return already
        carries and which now has a database column of its own. Adding the
        summed forms here would duplicate a fact the ledger holds more
        precisely, and section 5 of tests/test_agent_state_channel_coverage.py
        pins their absence on exactly this path as the node's own design.

        WHAT IT STILL DOES NOT COVER, stated rather than glossed:
          * The failing request itself, when it raised before a response.
          * Transport-layer retries inside the OpenAI SDK, which are invisible
            to this process at every return, success included.

        WHAT IT NOW DOES COVER, and did not before per-trial dispatch existed:
        requests this node ISSUED and then never read. In per-trial mode every
        call is issued before the loop begins, so a refusal or an unparseable
        answer on the first trial abandons N-1 responses that were paid for.
        ``_account_unconsumed()`` folds those into these accumulators at each
        of those three returns, BEFORE this helper is called, so the figure it
        reports is still the exact billed total rather than a prefix of it.

        AND THE CACHE WARMUP, which is a billed request that evaluates
        nothing. It is accounted on the node thread before the wave is
        dispatched, so it is inside these accumulators at every return below
        including the ones that fire before a single trial call was made. That
        is why this helper can be non-empty on a path where no trial was
        judged at all.
          * Earlier INVOCATIONS of this node. ``retry_count`` routes the graph
            back in here and every accumulator restarts at zero, so a run that
            spent three attempts reports the last one's tokens. That is the
            pre-existing behaviour of the SUCCESS return too and is documented
            at ``run_harness.price_result``; it is not introduced or worsened
            here, and it is why the stored total is a floor.
        """
        if not calls_made:
            return {}
        return {
            "llm_classifier_input_tokens": input_tokens,
            "llm_classifier_output_tokens": output_tokens,
            "llm_classifier_calls": calls_made,
        }

    # ------------------------------------------------------------------
    # The warmup, then the wave
    # ------------------------------------------------------------------
    #
    # THE RULE IS CACHE-OR-NOTHING. `_prefetched` stays None in grouped mode
    # and nothing below runs at all; `_obtain` falls through to the identical
    # `call_matching_model(...)` the loop has always made, no thread is
    # created and no executor exists. That is what makes the OFF arm
    # byte-equivalent rather than merely equivalent.
    #
    # WHAT REPLACED WHAT. Per-trial mode shipped awaiting the FIRST REAL TRIAL
    # CALL alone so that it would write the shared prefix into the provider's
    # cache, then firing the remaining N-1 behind it. Two things were wrong
    # with that and only one of them is about money:
    #
    #   * IF THAT FIRST CALL EXHAUSTED ITS TRANSPORT RETRIES the remaining N-1
    #     went out against a cache nothing had written, at full input price,
    #     and nothing in the record distinguished that from a provider that
    #     does not cache. A cost leak that reports as an ordinary patient.
    #   * A REAL TRIAL DOUBLED AS CACHE INFRASTRUCTURE, so "this trial could
    #     not be evaluated" and "the cache was never established" were one
    #     event with one remedy, when they are two findings with two.
    #
    # So a DEDICATED WARMUP writes the cache (see call_matching_model_warmup):
    # the identical system message, the smallest user message and output
    # budget the provider permits, no trial in it. It is awaited alone. Only
    # then does the wave go out -- ALL of the trial calls, none held back,
    # bounded by MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS.
    #
    # AND IF IT CANNOT BE ESTABLISHED, NO TRIAL CALL IS ISSUED. There is no
    # uncached fallback anywhere: the patient fails through the existing
    # zero-success floor below, `_billed_so_far()` carries whatever the warmup
    # itself was billed, MAX_LLM_CLASSIFIER_RETRIES sees it and the batch
    # checkpoint resumes it. Fifteen full-price requests are worse than one
    # patient re-run.
    #
    # THE RETRY BUDGET IS THE EXISTING ONE, IN BOTH ITS LAYERS, AND NO THIRD
    # ONE IS INVENTED HERE. `OPENAI_SDK_MAX_RETRIES` is applied on the client
    # in oncotriage/config.py and covers this call inside the SDK, with the
    # SDK's own backoff honouring Retry-After -- that is the "retries" a
    # warmup exhausts before it is seen here at all. Above it,
    # `MAX_LLM_CLASSIFIER_RETRIES` is the node-level budget: the API-error
    # result this returns routes the graph back into this node through
    # `route_after_llm_classifier`, which re-runs the warmup. A third loop
    # around this call would be a fourth number in a file that already
    # reconciles three, and its wall time would not appear in the arithmetic
    # oncotriage/config.py works out beside those constants.
    #
    # WHETHER THE DISCOUNT ACTUALLY LANDS IS MEASURED, NOT ASSUMED. Every
    # call records the provider's own `prompt_tokens_details.cached_tokens`
    # into `llm_classifier_call_details`, per call, warmup included.
    #
    # THE RESULT OF EACH WAVE CALL IS AN OUTCOME, NEVER A RAISE. A worker that
    # let an exception escape would surface it at `future.result()` on the node
    # thread, in an order decided by the executor, and one trial's transport
    # failure would end the patient. Each outcome is carried back as a tagged
    # pair and interpreted BY THE SEND LOOP, in trial order, on the node
    # thread -- which is what makes the merge deterministic.
    #
    # NOTHING BUT THE HTTP CALL RUNS ON A WORKER. The user message for every
    # chunk is rendered HERE, before dispatch, for two reasons that are both
    # correctness rather than tidiness: `_render_trial_blocks` increments
    # MARKDOWN_ESCAPE_DECODE_UNRESOLVED and ESCAPED_ENTITY_DECODE_UNRESOLVED
    # inside its decoders, and `Counter[k] += 1` is a load-add-store the
    # interpreter may switch threads inside, so rendering concurrently would
    # silently lose increments from the two counters that report third-party
    # text reaching the judge; and the render emits log events that belong to
    # this patient in this order.
    #
    # THE BLOCKS ARE SLICED, NOT RE-RENDERED. `trial_blocks[i]` is the block
    # `_render_trial_blocks` already produced for `trials[i]` above, and blocks
    # are a pure function of their trial -- the identity
    # tests/test_agent_stage5_render_slice_equality.py proves and the packer
    # already depends on. Calling `_user_prompt_for([t])` here instead would
    # render every trial a SECOND time and inflate both decode counters by
    # exactly the factor the render-slice pass removed.
    def _chunk_key(chunk_):
        """The identity a prefetched response is filed under.

        THE NCT IDS, NOT ``id(chunk)``. A chunk is a list built in this
        function, and an identity key would be correct only for as long as no
        later mechanism produced an equal chunk as a different object -- which
        is exactly what the reactive splitter does. Keyed by content, a chunk
        the splitter invented has a key nothing filed and falls through to a
        live call, which is the right answer for it.

        DEFINED UNCONDITIONALLY even though only per-trial mode files anything,
        because ``_obtain`` below calls it and a helper that exists only on one
        branch is a NameError waiting for the first edit that widens the other.
        """
        return tuple(t["trial"]["nct_id"] for t in chunk_)

    _prefetched = None
    # The warmup's transport failure, when there was one. Not a boolean: the
    # floor below names the exception type and message in the error string it
    # hands the retry router, exactly as the grouped path does for a raised
    # first call, so an operator reading a failed row sees the endpoint's own
    # diagnosis rather than "the warmup failed".
    _warmup_error = None
    # The routing hint for this patient's warmup AND its wave, or None. ONE
    # READING, used twice, so the two requests cannot ask to be routed apart.
    _cache_key = (per_trial_prompt_cache_key(system_prompt_sha256)
                  if _per_trial_calls else None)
    if _per_trial_calls:
        # The order the send loop will consume in. Derived from `pending`
        # rather than from `initial_chunks` so the dispatch order is provably
        # the order the loop pops in: `pending` is a LIFO seeded reversed, so
        # reversing it back gives pop order. Reading `initial_chunks` instead
        # would be a second statement of the same fact, free to disagree with
        # the seeding line above it.
        # THE DEPTH TRAVELS WITH THE CHUNK. An unconsumed prefetched call has
        # to be able to name the split depth it was issued at, and reading it
        # back off `pending` after the loop has drained it is not possible.
        _dispatch_pairs = [(c, d) for c, d in reversed(pending)]
        _dispatch_order = [c for c, _ in _dispatch_pairs]

        def _issue(chunk_, prompt_, cache_key_):
            """One request, as an OUTCOME. Runs on a worker thread."""
            try:
                return ("ok", call_matching_model(
                    system_prompt, prompt_, prompt_cache_key=cache_key_))
            except Exception as exc:              # noqa: BLE001 -- see above
                return ("error", exc)

        # THE ALIGNMENT IS ASSERTED, NOT ASSUMED, and `zip` is exactly why: it
        # truncates silently, so a `_dispatch_order` that had stopped being
        # positionally parallel to `trial_blocks` would send trial i's prompt
        # under trial j's key with nothing raising, and the model would answer
        # about a trial nobody asked for. That is the hazard
        # PackingBlockMismatchError already exists for, one mechanism over, so
        # it is the exception raised here.
        if len(_dispatch_order) != len(trial_blocks):
            raise PackingBlockMismatchError(
                f"per-trial dispatch has {len(_dispatch_order)} chunks against "
                f"{len(trial_blocks)} rendered blocks; they must be "
                "positionally parallel")
        _prompts = {}
        for _i, _c in enumerate(_dispatch_order):
            if _chunk_key(_c) != (trials[_i]["trial"]["nct_id"],):
                raise PackingBlockMismatchError(
                    "per-trial dispatch expected chunk "
                    f"{_i} to hold exactly trials[{_i}] "
                    f"({trials[_i]['trial']['nct_id']}); it holds "
                    f"{_chunk_key(_c)}")
            _prompts[_chunk_key(_c)] = _wrap_trials(trial_blocks[_i])

        def _account_warmup(response_) -> None:
            """Fold the warmup into the record, on the node thread.

            THE SAME SEQUENCE THE SEND LOOP USES, in the same order and for the
            same reasons: count the call, accumulate the four usage figures,
            CHECK THE ANSWERING MODEL, then append the ledger row.

            WHY THE MODEL CHECK RUNS ON THE WARMUP AT ALL, which is the one
            thing here that is not simply "mirror the loop". A mismatched judge
            is a fact about the endpoint, not about a trial, and it is the same
            fact on every one of this patient's requests. Discovering it on the
            warmup fails the patient BEFORE the wave, for the price of one
            one-token request; discovering it in the loop fails it after
            fifteen full-price ones have already been issued and billed. The
            check is free to move earlier precisely because it does not depend
            on anything a trial call carries.

            THE ROW IS MARKED AND CANNOT BE MISTAKEN FOR A TRIAL. `warmup` is
            present on this row and on no other -- the absent-rather-than-empty
            convention `unconsumed` already follows in this ledger -- `trials`
            is 0 because it carried none, and `entries_emitted` stays None
            because nothing parsed it. So per-trial accounting that groups on
            `trials` or reads `entries_emitted` excludes it by construction,
            while every token it was billed is visible and is inside the
            patient's totals.

            `depth` IS None, NOT 0. Zero is a real split depth -- it is the
            depth every first-generation chunk carries -- and the warmup has no
            place in that tree at all.
            """
            nonlocal calls_made, input_tokens, output_tokens
            nonlocal reasoning_tokens, reasoning_tokens_reported
            nonlocal cached_input_tokens, cached_input_reported, model_answered
            _u = getattr(response_, "usage", None)
            _pt = getattr(_u, "prompt_tokens", None)
            _ct = getattr(_u, "completion_tokens", None)
            calls_made += 1
            if isinstance(_pt, int) and not isinstance(_pt, bool):
                input_tokens += _pt
            if isinstance(_ct, int) and not isinstance(_ct, bool):
                output_tokens += _ct
            _rt = getattr(getattr(_u, "completion_tokens_details", None),
                          "reasoning_tokens", None)
            if _rt is not None:
                reasoning_tokens += _rt
                reasoning_tokens_reported = True
            _cd = getattr(getattr(_u, "prompt_tokens_details", None),
                          "cached_tokens", None)
            if _cd is not None:
                cached_input_tokens += _cd
                cached_input_reported = True
            _expected = config.matching_wire_model()
            _returned = getattr(response_, "model", None)
            if _returned is not None and _returned != _expected:
                raise MatchingModelMismatchError(_expected, _returned)
            model_answered = _returned or model_answered
            _choices = getattr(response_, "choices", None) or []
            call_details.append({
                "call_index": calls_made,
                "depth": None,
                "trials": 0,
                "prompt_tokens": _pt,
                "completion_tokens": _ct,
                "cached_tokens": _cd,
                "reasoning_tokens": _rt,
                "finish_reason": (getattr(_choices[0], "finish_reason", None)
                                  if _choices else None),
                "entries_emitted": None,
                "warmup": True,
            })

        _prefetched = {}
        if _dispatch_pairs:
            # ── The warmup ────────────────────────────────────────────────
            #
            # HOLDING THE FIRST TRIAL CALL BACK IS THE FALLBACK, NOT THE
            # DESIGN. `_hold_first` is False unless the provider REFUSES the
            # warmup's request shape, in which case the retired one-then-rest
            # schedule is the best remaining approximation of a warm cache and
            # is taken deliberately, with a named counter, rather than
            # silently.
            _hold_first = False
            try:
                _warmup_response = call_matching_model_warmup(
                    system_prompt, prompt_cache_key=_cache_key)
            except Exception as _wu_exc:          # noqa: BLE001 -- classified
                _rejection = classify_warmup_rejection(_wu_exc)
                if _rejection is None:
                    # A TRANSPORT FAILURE. `pending` is emptied so the send
                    # loop cannot issue a single trial call -- `_obtain`'s
                    # live-call path is a real path and would otherwise send
                    # every one of them uncached, which is the exact leak this
                    # design removes. The floor below turns the empty run into
                    # the API-error result.
                    PER_TRIAL_WARMUP_DEGRADATIONS[
                        f"{WARMUP_FAILURE_KEY_PREFIX}"
                        f"{type(_wu_exc).__name__}"] += 1
                    _warmup_error = _wu_exc
                    pending.clear()
                    log.error(
                        "the Stage 5 per-trial cache warmup failed; no trial "
                        "call was issued and the patient is failed so the "
                        "retry budget and the checkpoint see it, rather than "
                        "sending every trial against a cold cache", stage=5,
                        status="error", event="per_trial_warmup_failed",
                        retry=retry_count + 1,
                        error_type=type(_wu_exc).__name__,
                        error_message=str(_wu_exc), count=len(_dispatch_pairs),
                        degraded=True)
                else:
                    PER_TRIAL_WARMUP_DEGRADATIONS[_rejection] += 1
                    _hold_first = True
                    if _rejection == WARMUP_REJECTED_CACHE_KEY:
                        # DROPPED FOR THE WAVE TOO. The provider refused this
                        # parameter, so carrying it into the fallback's calls
                        # would refuse every one of them and turn a recoverable
                        # configuration finding into a failed patient.
                        _cache_key = None
                    log.warning(
                        "the provider refused the Stage 5 per-trial cache "
                        "warmup's request shape; falling back to the "
                        "one-then-rest schedule for this patient, which holds "
                        "the first trial call back as the cache writer",
                        stage=5, event="per_trial_warmup_rejected",
                        reason=_rejection, retry=retry_count + 1,
                        error_type=type(_wu_exc).__name__,
                        error_message=str(_wu_exc), degraded=True)
            else:
                # ACCOUNTED BEFORE ANY TRIAL CALL IS ISSUED, which is what
                # makes `_account_unconsumed()` below provably unaffected by
                # the warmup: it folds what is left in `_prefetched`, and the
                # warmup never enters it.
                _account_warmup(_warmup_response)
                log.info("Stage 5 warmed the shared prefix before dispatching "
                         "the per-trial wave", stage=5,
                         event="per_trial_warmup",
                         count=len(_dispatch_pairs),
                         parallel=min(_parallel_bound, len(_dispatch_pairs)))

            if _warmup_error is None:
                if _hold_first:
                    _first, _first_depth = _dispatch_pairs[0]
                    _prefetched[_chunk_key(_first)] = _issue(
                        _first, _prompts[_chunk_key(_first)],
                        _cache_key) + (_first_depth,)
                    _rest = _dispatch_pairs[1:]
                else:
                    _rest = _dispatch_pairs
                if _rest:
                    _bound = min(_parallel_bound, len(_rest))
                    # CONTEXT PROPAGATION IS NOT OPTIONAL HERE. The correlation
                    # ID is a contextvars.ContextVar
                    # (oncotriage/observability.py), and a thread starts with an
                    # EMPTY context -- so every line a worker logged would carry
                    # NO_CORRELATION and be unjoinable to the patient that paid
                    # for it. One FRESH copy per task, taken on THIS thread: a
                    # single Context object cannot be entered concurrently, so
                    # sharing one across the pool would raise.
                    with ThreadPoolExecutor(max_workers=_bound,
                                            thread_name_prefix="stage5") as _ex:
                        _futures = []
                        for _c, _d in _rest:
                            _ctx = contextvars.copy_context()
                            _futures.append((_c, _d, _ex.submit(
                                _ctx.run, _issue, _c,
                                _prompts[_chunk_key(_c)], _cache_key)))
                        for _c, _d, _fut in _futures:
                            # `.result()` cannot raise here: `_issue` returns
                            # its exception rather than propagating it, and the
                            # only other way a future raises is cancellation,
                            # which nothing cancels.
                            _prefetched[_chunk_key(_c)] = _fut.result() + (_d,)

    def _obtain(chunk):
        """The API response for `chunk`, prefetched or issued now.

        THE ONE LINE THE SEND LOOP CHANGED. In grouped mode `_prefetched` is
        None and this is the identical call the loop always made, with the
        identical arguments; the extra frame is the whole of the difference.

        A PREFETCHED OUTCOME IS CONSUMED ONCE. It is popped rather than read,
        so a chunk that somehow reached the loop twice issues a real second
        call instead of silently replaying one response as two -- which would
        double-count tokens against a request nobody made.

        A CHUNK WITH NO PREFETCHED ENTRY IS CALLED LIVE, and that is the
        composition promise rather than a fallback nobody expects to take: the
        reactive splitter builds new chunks after dispatch, and they are
        supposed to be sent.
        """
        if _prefetched is not None:
            outcome = _prefetched.pop(_chunk_key(chunk), None)
            if outcome is not None:
                status, payload, _ = outcome
                if status == "error":
                    raise payload
                return payload
        return call_matching_model(system_prompt, _user_prompt_for(chunk))

    def _account_unconsumed() -> int:
        """Fold prefetched calls the send loop never reached into the record.

        WHY THIS EXISTS, AND IT IS THE DEFECT PER-TRIAL DISPATCH INTRODUCES.
        In grouped mode the pending queue is UNISSUED when an early return
        fires: a refusal, a parse failure or a non-list body on chunk k ends
        the node with chunks k+1..N never sent and therefore never billed, so
        ``_billed_so_far()`` is exact. Per-trial mode issues every call BEFORE
        the loop starts, so the same three returns abandon N-k responses that
        have already been paid for -- and the record would carry only the first
        k. That is the "reported a token figure no provider produced" shape
        this file removed from four failure returns once already, reintroduced
        from the other direction: not a false zero, a false TOTAL.

        SO IT IS A MEASUREMENT, NOT AN ESTIMATE. Every folded row carries the
        provider's own usage object for a request the provider really answered.
        Calls that RAISED are counted under ``abandoned:`` in
        PER_TRIAL_CALL_FAILURES and contribute no tokens -- they produced no
        usage object, and inventing one is the thing ``_billed_so_far``'s
        ``calls_made`` guard already refuses to do.

        DETERMINISTIC ORDER, by nct_id, so two runs that abandon the same set
        fold it in the same sequence and the ledger's tail is reproducible.
        ``sorted()`` materialises the keys before the loop pops, so the
        mutation is safe.

        THE WARMUP IS NEVER IN HERE, and that is by construction rather than
        by a filter: it is consumed -- read, accounted and ledgered -- on the
        node thread BEFORE `_prefetched` is populated, so it cannot be an
        abandoned response. tests/test_agent_stage5_per_trial_calls.py section
        3 asserts that rather than leaving it as reasoning.

        IT NEVER RAISES, AND THE MODEL CHECK IS DELIBERATELY NOT REPEATED HERE.
        This runs on a path that is already failing and already has a
        diagnosis; raising ``MatchingModelMismatchError`` from inside it would
        replace a named failure with an unrelated one and lose the record it
        was called to write. A mismatch on a CONSUMED call still raises, in the
        loop, exactly as before.

        ``unconsumed`` MARKS THE ROW because "no entry list was parsed" already
        has two meanings in this ledger -- the response was unusable, or the
        node stopped before reading it -- and only the second is free of any
        judgement about the model. ``entries_emitted`` stays None either way.
        """
        nonlocal calls_made, input_tokens, output_tokens
        nonlocal reasoning_tokens, reasoning_tokens_reported
        nonlocal cached_input_tokens, cached_input_reported, model_answered
        if not _prefetched:
            return 0
        folded = 0
        abandoned_errors = 0
        for _key in sorted(_prefetched):
            _status, _payload, _depth = _prefetched.pop(_key)
            if _status == "error":
                PER_TRIAL_CALL_FAILURES[
                    f"abandoned:{type(_payload).__name__}"] += 1
                abandoned_errors += 1
                continue
            _u = getattr(_payload, "usage", None)
            _pt = getattr(_u, "prompt_tokens", None)
            _ct = getattr(_u, "completion_tokens", None)
            if isinstance(_pt, int) and not isinstance(_pt, bool):
                input_tokens += _pt
            if isinstance(_ct, int) and not isinstance(_ct, bool):
                output_tokens += _ct
            _rt = getattr(getattr(_u, "completion_tokens_details", None),
                          "reasoning_tokens", None)
            if _rt is not None:
                reasoning_tokens += _rt
                reasoning_tokens_reported = True
            _cd = getattr(getattr(_u, "prompt_tokens_details", None),
                          "cached_tokens", None)
            if _cd is not None:
                cached_input_tokens += _cd
                cached_input_reported = True
            calls_made += 1
            _choices = getattr(_payload, "choices", None) or []
            call_details.append({
                "call_index": calls_made,
                "depth": _depth,
                "trials": len(_key),
                "prompt_tokens": _pt,
                "completion_tokens": _ct,
                "cached_tokens": _cd,
                "reasoning_tokens": _rt,
                "finish_reason": (getattr(_choices[0], "finish_reason", None)
                                  if _choices else None),
                "entries_emitted": None,
                "unconsumed": True,
            })
            model_answered = getattr(_payload, "model", None) or model_answered
            folded += 1
        # `or abandoned_errors` IS NOT REDUNDANT, and the disjunct that WOULD
        # have been is worth naming: an earlier draft tested `folded or
        # _prefetched`, and `_prefetched` is empty by then because the loop
        # above pops every key -- a dead branch that reads like a guard. A run
        # whose abandoned calls ALL raised folds nothing and still has
        # something to report, which is the case this arm covers.
        if folded or abandoned_errors:
            log.warning("Stage 5 stopped before reading every per-trial "
                        "response; the abandoned calls were still issued and "
                        "billed, and are recorded as unconsumed rather than "
                        "dropped from the ledger", stage=5,
                        event="per_trial_calls_abandoned", count=folded,
                        lost=abandoned_errors, degraded=True)
        return folded

    # Per-trial calls that raised, isolated to their own trial. Counted here as
    # well as in PER_TRIAL_CALL_FAILURES because the all-failed guard below is
    # a question about THIS PATIENT and a module-level counter is a question
    # about the process.
    per_trial_failed_calls = 0
    per_trial_last_error = None
    # TRIAL calls that returned a response, which is NOT `calls_made`. The
    # warmup is a billed call and is counted in `calls_made` -- correctly, it
    # is real money -- but it evaluates nothing, so a floor that tested
    # `calls_made` would be satisfied by a successful warmup and would STOP
    # FIRING for the case it exists to catch: every trial call failing while
    # the warmup succeeded, which is exactly what a total outage that begins
    # after the first request looks like. That patient would be recorded as a
    # cohort of not-evaluable trials with no error, which is the failure this
    # project exists to remove. So the floor asks about verdicts, and this is
    # the counter that answers.
    per_trial_succeeded = 0

    while pending:
        chunk, depth = pending.pop()

        try:
            response = _obtain(chunk)
            choice = response.choices[0]
            chunk_text = (choice.message.content or "").strip()
        except Exception as e:
            # API-level failure (timeout, rate limit, network error). This is
            # the parse/API budget, not the split budget.
            #
            # ── PER-TRIAL MODE ISOLATES IT TO THE TRIAL ────────────────────
            #
            # A chunk is one trial here, so "this call failed" and "this trial
            # could not be evaluated" are the same statement -- and failing the
            # whole patient would discard the N-1 calls that already succeeded
            # AND already cost money. The trial is recorded with its own
            # reason, which is what keeps a transport failure separable from
            # the model omitting an entry, and the reconciliation at the end of
            # the node still guarantees every trial is accounted for exactly
            # once.
            #
            # ISOLATION IS NOT SILENCE, AND IT IS NOT UNBOUNDED. The counter
            # below reaches the run-end degradation report, this logs at ERROR
            # per failure, and a patient whose calls ALL failed does not
            # continue: the guard under the loop returns the API-error result
            # so MAX_LLM_CLASSIFIER_RETRIES still covers a total outage exactly
            # as it does in grouped mode. Without that guard an unreachable
            # endpoint would produce a run of not-evaluable trials reported as
            # a success, which is a worse failure than the one being isolated.
            #
            # ONLY THIS SHAPE IS ISOLATED. A refusal, malformed JSON and a
            # non-list body all still end the node, unchanged, because all
            # three are decided by the SYSTEM message and the response format,
            # which are byte-identical on every call of this patient -- so they
            # are statements about the request this pipeline built rather than
            # about one trial, and re-sending is the right response to them.
            # The cost of that decision under per-trial mode is stated rather
            # than hidden: one malformed answer re-bills the whole patient's
            # N calls. A per-trial parse budget is a separate mechanism with
            # its own argument and is deliberately not invented here.
            if _per_trial_calls:
                PER_TRIAL_CALL_FAILURES[type(e).__name__] += 1
                per_trial_failed_calls += 1
                per_trial_last_error = e
                log.error("a per-trial Stage 5 call failed; recording that "
                          "trial as not evaluable and continuing with the "
                          "rest of the patient", stage=5, status="error",
                          event="per_trial_call_failed",
                          retry=retry_count + 1,
                          error_type=type(e).__name__, error_message=str(e),
                          count=len(chunk),
                          nct_ids=[t["trial"]["nct_id"] for t in chunk])
                unevaluable.extend(
                    _unevaluable_entry(t, NOT_EVALUABLE_CALL_FAILED)
                    for t in chunk
                )
                continue
            elapsed = time.time() - start
            error_msg = f"GPT-4o API error (attempt {retry_count + 1}): {str(e)}"
            log.error("Stage 5 API call failed", stage=5, status="error",
                      retry=retry_count + 1, error_type=type(e).__name__,
                      error_message=str(e))
            return {
                # Calls ISSUED before this return, never summed. A list is not a
                # count, so a short one understates nothing; see the accumulator.
                "llm_classifier_call_details": call_details,
                "evaluations": [],
                "llm_classifier_retries": retry_count + 1,
                "llm_classifier_truncation_splits": truncation_splits,
                "llm_classifier_output_tokens_estimated": estimated_output,
                # The two denominators the estimate above is read against,
                # carried for its own reason: measured BEFORE the first call, so
                # true of this run whether or not it answered. Argued in full at
                # the success return.
                "llm_classifier_output_split_threshold": split_threshold,
                "llm_classifier_output_ceiling": MATCHING_MAX_TOKENS,
                "llm_classifier_raw_response": "",
                # What the calls that DID return were billed. Empty when this
                # was the first request and no usage object ever arrived; see
                # _billed_so_far for why absent rather than zero.
                **_billed_so_far(),
                # The model that answered the earlier chunks of this batch, if
                # any. None when the first call raised, which is the same value
                # this key had before and reads as "Stage 5 obtained no
                # response". Carried because the tokens above are now non-zero
                # on this path and a token figure with no model beside it is
                # the one shape every cost reader in this project refuses to
                # price.
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
                # Carried on the failure returns for the same reason the hash
                # is: the record was measured BEFORE the first call, so it is a
                # fact about this run whether or not the run answered.
                "llm_classifier_patient_record_tokens": patient_record_tokens,
                "stage_timings": {**state.get("stage_timings", {}), "llm_classifier_evaluation": round(prior_llm_classifier_time + elapsed, 3)}
            }

        calls_made += 1
        if _per_trial_calls:
            # A TRIAL call that produced a response. See the declaration.
            per_trial_succeeded += 1
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

        # The cached half of the same reading. `prompt_tokens_details` is the
        # input-side sibling of `completion_tokens_details` and carries
        # `cached_tokens`; both getattrs are defensive for the same three
        # reasons the block above lists. An int() coercion is deliberately NOT
        # applied -- a non-numeric value would be a provider contract change and
        # is better as a TypeError here than as a plausible number in a record.
        _prompt_details = getattr(response.usage, "prompt_tokens_details", None)
        _cached = getattr(_prompt_details, "cached_tokens", None)
        if _cached is not None:
            cached_input_tokens += _cached
            cached_input_reported = True

        # The model that ANSWERED, checked against the one requested BEFORE its
        # verdicts are parsed or accumulated. Placed here rather than at logging
        # time so it fires on the first call of the first patient: a mismatch
        # discovered at log time has already spent a whole batch on the wrong
        # judge. See MatchingModelMismatchError for why this raises.
        #
        # None means the response carried no model field (a stub, or a
        # pre-migration recording). That is a different condition and falls
        # through to the existing NULL handling untouched.
        #
        # COMPARED AGAINST THE WIRE MODEL, NOT `MATCHING_MODEL`, and the two
        # are the SAME STRING whenever MATCHING_PROVIDER is "openai" --
        # config.matching_wire_model() returns MATCHING_MODEL on that branch,
        # so nothing about this check moves under the default. On Bedrock the
        # id that was SENT is a cross-Region inference profile
        # ("us.openai.gpt-5.6-terra"), so comparing the echo against
        # MATCHING_MODEL would raise on every well-behaved response. The
        # alternative -- having the adapter present MATCHING_MODEL as the echo
        # -- was rejected: it would make every stored row name a model that did
        # not serve it, which is the exact misattribution this check exists to
        # prevent, pointed the other way.
        _model_expected = config.matching_wire_model()
        _model_returned = getattr(response, "model", None)
        if _model_returned is not None and _model_returned != _model_expected:
            raise MatchingModelMismatchError(_model_expected, _model_returned)

        model_answered = _model_returned or model_answered

        # ── One line in the per-call ledger ────────────────────────────────
        #
        # `_cached` and `_reasoning` are carried as read: None means THIS
        # response reported no such breakdown, which is not zero and must not
        # be rendered as zero. The accumulators above fold them into totals;
        # this keeps the per-request fact the totals cannot reconstruct.
        #
        # `chunk_index` is the ordinal of the REQUEST, not of the packed chunk:
        # the pending queue is a LIFO seeded in packing order, so calls 1..N of
        # an unsplit run are packed chunks 1..N, and a split inserts its
        # children immediately after their parent at depth+1. `depth` is what
        # separates the two, so neither number has to be inferred.
        # `entries_emitted` is the DENOMINATOR the emission stamp needs and is
        # deliberately born as None here, then written below once the response
        # has parsed into a list. It cannot be filled at this line: this row is
        # appended BEFORE the parse, on purpose, so that a call whose response
        # was unusable is still recorded as having been made and billed.
        #
        # None therefore means "this call produced no parseable list", which is
        # every failure shape at once and is exactly the project's convention --
        # the mechanism did not run. A truncated response (finish_reason
        # "length", which `continue`s into a split), a refusal, malformed JSON
        # and a non-list body all leave it None, and none of them is a zero: a
        # zero is a model that answered with an empty array, which IS a
        # measurement and is recorded as 0.
        #
        # The row is held by reference rather than reached as `call_details[-1]`
        # so that a future append between here and the parse cannot silently
        # redirect the write onto another call's row.
        _this_call = {
            "call_index": calls_made,
            "depth": depth,
            "trials": len(chunk),
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "cached_tokens": _cached,
            "reasoning_tokens": _reasoning,
            "finish_reason": getattr(choice, "finish_reason", None),
            "entries_emitted": None,
        }
        call_details.append(_this_call)

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
            # EVERY PREFETCHED CALL WAS ALREADY ISSUED AND BILLED, so
            # abandoning the queue here must not abandon the record of
            # what it cost. A no-op in grouped mode, where nothing is
            # prefetched and the queue is genuinely unissued.
            _account_unconsumed()
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
                # Calls ISSUED before this return, never summed. A list is not a
                # count, so a short one understates nothing; see the accumulator.
                "llm_classifier_call_details": call_details,
                "evaluations": [],
                # UNINCREMENTED, deliberately. See above.
                "llm_classifier_retries": retry_count,
                # The flag the router terminates on. Written only here.
                "llm_classifier_refusal": _refusal[:_REFUSAL_PREVIEW_LEN],
                "llm_classifier_truncation_splits": truncation_splits,
                "llm_classifier_output_tokens_estimated": estimated_output,
                # The two denominators the estimate above is read against,
                # carried for its own reason: measured BEFORE the first call, so
                # true of this run whether or not it answered. Argued in full at
                # the success return.
                "llm_classifier_output_split_threshold": split_threshold,
                "llm_classifier_output_ceiling": MATCHING_MAX_TOKENS,
                # The refusal text is the whole of what the model returned, so
                # it IS the raw response for this run. Capped by the same rule
                # as the error string.
                "llm_classifier_raw_response": _refusal[:_REFUSAL_PREVIEW_LEN],
                # A model DID answer -- by declining -- so the run is not
                # anonymous, on the same argument as the parse-error path.
                "matching_model": model_answered,
                # A refusal is a completed, billed exchange: usage was read for
                # every call before this one AND for the refusing call itself,
                # above. Recording zeros here was the defect.
                **_billed_so_far(),
                "error": error_msg,
                "llm_classifier_prompt_version": PROMPT_VERSION,
                "llm_classifier_prompt_sha256": system_prompt_sha256,
                # Carried on the failure returns for the same reason the hash
                # is: the record was measured BEFORE the first call, so it is a
                # fact about this run whether or not the run answered.
                "llm_classifier_patient_record_tokens": patient_record_tokens,
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
            # EVERY PREFETCHED CALL WAS ALREADY ISSUED AND BILLED, so
            # abandoning the queue here must not abandon the record of
            # what it cost. A no-op in grouped mode, where nothing is
            # prefetched and the queue is genuinely unissued.
            _account_unconsumed()
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
                # Calls ISSUED before this return, never summed. A list is not a
                # count, so a short one understates nothing; see the accumulator.
                "llm_classifier_call_details": call_details,
                "evaluations": [],
                "llm_classifier_retries": retry_count + 1,
                "llm_classifier_truncation_splits": truncation_splits,
                "llm_classifier_output_tokens_estimated": estimated_output,
                # The two denominators the estimate above is read against,
                # carried for its own reason: measured BEFORE the first call, so
                # true of this run whether or not it answered. Argued in full at
                # the success return.
                "llm_classifier_output_split_threshold": split_threshold,
                "llm_classifier_output_ceiling": MATCHING_MAX_TOKENS,
                "llm_classifier_raw_response": chunk_text,
                # A model DID answer here -- badly, but it answered -- so the
                # run is not anonymous. Carried so that a patient whose retries
                # all end in malformed JSON still logs which model produced
                # them instead of a NULL that reads as "Stage 5 never ran".
                "matching_model": model_answered,
                # THE SENTENCE THAT USED TO STAND HERE WAS FALSE AND IS
                # DELETED. It said the token counters "are not accumulated on
                # this path at all". They are: `input_tokens +=
                # response.usage.prompt_tokens` runs for every response, above,
                # and the parse is reached only after it -- so at this return
                # the accumulators hold the exact billed figure for this
                # invocation and the response that would not parse is itself
                # inside it. Writing zeros was not a gap in the accumulation,
                # it was a gap in the return.
                **_billed_so_far(),
                "error": error_msg,
                # The prompt WAS rendered before this return -- every one of Stage
                # 5's early returns sits below the render call -- so the hash is a
                # fact about this run and is carried. A failed run is exactly the
                # run worth knowing the prompt identity of; a version that reached
                # the database only on success would be missing for the rows most
                # worth investigating. _pipeline_provenance() reads both off state.
                "llm_classifier_prompt_version": PROMPT_VERSION,
                "llm_classifier_prompt_sha256": system_prompt_sha256,
                # Carried on the failure returns for the same reason the hash
                # is: the record was measured BEFORE the first call, so it is a
                # fact about this run whether or not the run answered.
                "llm_classifier_patient_record_tokens": patient_record_tokens,
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
            # EVERY PREFETCHED CALL WAS ALREADY ISSUED AND BILLED, so
            # abandoning the queue here must not abandon the record of
            # what it cost. A no-op in grouped mode, where nothing is
            # prefetched and the queue is genuinely unissued.
            _account_unconsumed()
            elapsed = time.time() - start
            error_msg = f"GPT-4o returned non-list JSON (type={type(parsed).__name__})"
            log.error("Stage 5 returned JSON that is not a list", stage=5,
                      status="error", retry=retry_count + 1,
                      error_message=error_msg,
                      response_chars=len(chunk_text))
            # Same reasoning as the parse-error branch above.
            console.out(f"  [Stage 5] response preview: {chunk_text[:300]}")
            return {
                # Calls ISSUED before this return, never summed. A list is not a
                # count, so a short one understates nothing; see the accumulator.
                "llm_classifier_call_details": call_details,
                "evaluations": [],
                "llm_classifier_retries": retry_count + 1,
                "llm_classifier_truncation_splits": truncation_splits,
                "llm_classifier_output_tokens_estimated": estimated_output,
                # The two denominators the estimate above is read against,
                # carried for its own reason: measured BEFORE the first call, so
                # true of this run whether or not it answered. Argued in full at
                # the success return.
                "llm_classifier_output_split_threshold": split_threshold,
                "llm_classifier_output_ceiling": MATCHING_MAX_TOKENS,
                "llm_classifier_raw_response": chunk_text,
                # A model DID answer here -- badly, but it answered -- so the
                # run is not anonymous. Carried so that a patient whose retries
                # all end in malformed JSON still logs which model produced
                # them instead of a NULL that reads as "Stage 5 never ran".
                "matching_model": model_answered,
                # THE SENTENCE THAT USED TO STAND HERE WAS FALSE AND IS
                # DELETED. It said the token counters "are not accumulated on
                # this path at all". They are: `input_tokens +=
                # response.usage.prompt_tokens` runs for every response, above,
                # and the parse is reached only after it -- so at this return
                # the accumulators hold the exact billed figure for this
                # invocation and the response that would not parse is itself
                # inside it. Writing zeros was not a gap in the accumulation,
                # it was a gap in the return.
                **_billed_so_far(),
                "error": error_msg,
                # The prompt WAS rendered before this return -- every one of Stage
                # 5's early returns sits below the render call -- so the hash is a
                # fact about this run and is carried. A failed run is exactly the
                # run worth knowing the prompt identity of; a version that reached
                # the database only on success would be missing for the rows most
                # worth investigating. _pipeline_provenance() reads both off state.
                "llm_classifier_prompt_version": PROMPT_VERSION,
                "llm_classifier_prompt_sha256": system_prompt_sha256,
                # Carried on the failure returns for the same reason the hash
                # is: the record was measured BEFORE the first call, so it is a
                # fact about this run whether or not the run answered.
                "llm_classifier_patient_record_tokens": patient_record_tokens,
                "stage_timings": {**state.get("stage_timings", {}), "llm_classifier_evaluation": round(prior_llm_classifier_time + elapsed, 3)}
            }

        parsed = _unwrapped

        # ── Where in the model's answer this entry stood ────────────────────
        #
        # PROVENANCE ONLY. Nothing below reads these two fields, no verdict,
        # score, drop or sort depends on them, and neither is asked of the
        # model: the response schema is untouched and both values are computed
        # HERE, from the parsed list, by this pipeline.
        #
        # THE PLACEMENT IS THE WHOLE MECHANISM. Every step between this line
        # and `evaluations.extend(_objects)` REMOVES entries -- non-objects,
        # out-of-set ids, duplicate ids -- and the sort at the end of the node
        # reorders whatever survives. Stamping anywhere after a removal would
        # renumber the survivors and report a position the model never emitted;
        # stamping after the sort would report the pipeline's own ranking back
        # as the model's emission order, which is the exact fact this exists to
        # preserve. So it runs on the FULL parsed list, before the first drop.
        #
        # `emission_index` is 0-BASED: it is a position in a list and there is
        # no prior art to disagree with.
        #
        # `call_index` is 1-BASED, and that asymmetry is deliberate rather than
        # an oversight. `llm_classifier_call_details` -- appended above, in this
        # same loop, and returned in this same result -- numbers its calls
        # 1..N (`calls_made` is incremented before the append, and
        # tests/test_agent_state_channel_coverage.py pins "call_index is 1..N in
        # order"). Two fields of one result sharing a name and disagreeing about
        # their origin is an off-by-one join waiting to be got wrong silently,
        # so the ledger's numbering wins and an entry joins its own call record
        # by equality.
        #
        # An entry that ARRIVED carrying either key is overwritten. The fields
        # mean "where this pipeline saw this entry"; a model-supplied value
        # would be a different claim under the same name. Strict Structured
        # Outputs forbids additional properties, so this is defence against a
        # bare-array response, not an expected case.
        #
        # Non-dict entries are skipped: there is nothing to stamp, and they are
        # dropped and counted immediately below.
        for _emission_index, _entry in enumerate(parsed):
            if isinstance(_entry, dict):
                _entry["emission_index"] = _emission_index
                _entry["call_index"] = calls_made

        # ── The denominator those positions are positions OUT OF ───────────
        #
        # WITHOUT THIS THE STAMP IS NOT INTERPRETABLE. Every filter below
        # removes entries, so the surviving emission indices have GAPS, and
        # `max(emission_index) + 1` is a lower bound rather than a count: an
        # entry dropped from the END of the array leaves no trace in the
        # survivors at all. This is the only place the length is known.
        #
        # It counts the PARSED LIST, so it includes the non-object entries the
        # next block discards -- the question is "how many things did the model
        # write", not "how many were usable" -- and it is therefore >= the
        # number of stamped survivors, never <.
        #
        # Written onto the row this call already appended, so the ledger stays
        # one row per billed call and this is a field of that row rather than a
        # second parallel list to keep in step.
        _this_call["entries_emitted"] = len(parsed)

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

        # ── Entries for a trial this call did not ask about ────────────────
        #
        # Against THIS CHUNK's sent set, which is why the check is here rather
        # than after the loop: a response answers its own chunk, and the union
        # over a split batch would admit an id belonging to a different call.
        #
        # Runs before enrichment, scoring and normalization -- all of which are
        # below the loop -- so a fabricated entry reaches none of them. See the
        # block above _partition_out_of_set for why it is dropped rather than
        # repaired, and for why the trial it displaced is left to the
        # reconciliation at the end of this node rather than handled here.
        _chunk_ids = {t["trial"]["nct_id"] for t in chunk}
        _objects, _cross_chunk, _fabricated = _partition_out_of_set(
            _objects, _chunk_ids, _batch_ids)
        if _cross_chunk or _fabricated:
            # ONLY THE FABRICATED IDS REACH inferences.hallucinated_trials.
            # A cross-chunk id names a real candidate of this run and costs the
            # patient nothing; folding it into the same number would put a
            # provider's handling of a split request into a column a reader
            # treats as a hallucination rate.
            hallucinated_ids.extend(_fabricated)
            cross_chunk_ids.extend(_cross_chunk)
            log.warning("the model returned evaluations for trials this call "
                        "did not ask about; dropping them. A fabricated id "
                        "names no trial in this run; a cross-chunk id names "
                        "one another call answers. Any candidate they "
                        "displaced is recorded by the reconciliation below",
                        stage=5, event="out_of_set_entry",
                        # The ids only, in two named buckets. The rest of such
                        # an entry is the model's criterion-level prose about
                        # this patient and the structured record is durable and
                        # indexed, so it goes nowhere -- not even to the
                        # console, which the malformed-entry path uses for a
                        # type fragment. Here the id IS the whole diagnosis.
                        count=len(_cross_chunk) + len(_fabricated),
                        fabricated_count=len(_fabricated),
                        fabricated_nct_ids=_fabricated,
                        cross_chunk_count=len(_cross_chunk),
                        cross_chunk_nct_ids=_cross_chunk)

        # ── The same trial answered more than once ─────────────────────────
        #
        # Within the chunk, and that is complete rather than partial: the split
        # partitions the batch, so a repeat arriving in another chunk's
        # response was already dropped as cross-chunk above. See
        # _collapse_duplicate_entries.
        _objects, _collapsed = _collapse_duplicate_entries(_objects)
        if _collapsed:
            _identical = [d["nct_id"] for d in _collapsed
                          if d["case"] == DUPLICATE_CASE_IDENTICAL]
            _conflicting = [d["nct_id"] for d in _collapsed
                            if d["case"] == DUPLICATE_CASE_CONFLICTING]
            duplicate_ids.extend(_collapsed)
            # A conflicting id keeps NO entry, so it is accounted for here or
            # nowhere -- the reconciliation would otherwise record it as
            # "omitted from the model response", which is false: the model
            # answered twice.
            unevaluable.extend(
                _unevaluable_entry(_trial_by_id[nct_id],
                                   NOT_EVALUABLE_CONFLICTING_DUPLICATES)
                for nct_id in _conflicting
            )
            log.warning("the model returned more than one evaluation for the "
                        "same trial; collapsing. Identical verdicts keep the "
                        "first entry; conflicting verdicts are recorded as "
                        "not evaluable, because a judge that contradicts "
                        "itself about a trial has not assessed it",
                        stage=5, event="duplicate_answers",
                        count=len(_collapsed),
                        duplicate_identical_count=len(_identical),
                        duplicate_identical_nct_ids=_identical,
                        duplicate_conflicting_count=len(_conflicting),
                        duplicate_conflicting_nct_ids=_conflicting)

        evaluations.extend(_objects)

    # ── Per-trial mode: no trial was judged ───────────────────────────────
    #
    # ISOLATION HAS A FLOOR, AND THIS IS IT. Recording every trial as not
    # evaluable and returning success would turn an unreachable endpoint, an
    # expired key or a total outage into a patient with no matches, no error
    # and a clean-looking row -- the failure this project exists to remove,
    # manufactured by the mechanism that isolates a single bad call.
    #
    # TWO WAYS IN, ONE RETURN. Either the warmup could not be established, in
    # which case NO trial call was issued at all and `pending` was emptied
    # above; or every trial call that WAS issued failed. Both mean the same
    # thing to every consumer -- this patient produced no verdict and must be
    # re-run -- and giving them two returns would be two shapes for the retry
    # router to agree about. They differ only in the error string, which is
    # what an operator reads.
    #
    # `per_trial_succeeded` IS THE TEST AND NOT `calls_made`, and that is a
    # correction rather than a detail: the warmup is a billed call and is
    # counted in `calls_made`, so testing `calls_made` would leave this floor
    # satisfied by a successful warmup and silently unable to fire for the
    # total-outage case it exists for. Nor is it `per_trial_failed_calls`: the
    # question is whether ANY trial was judged, not how many were lost. One
    # survivor out of fifteen is a bad run that still produced a real verdict
    # for a real trial, and discarding it to re-bill fourteen more is the wrong
    # trade; zero survivors produced nothing to keep.
    #
    # IT RETURNS THE API-ERROR RESULT, SHAPE FOR SHAPE, so the retry router
    # sees exactly what grouped mode's raised first call gives it and
    # MAX_LLM_CLASSIFIER_RETRIES covers the outage identically.
    # `_billed_so_far()` is NOT empty on the warmup arm and that is the point:
    # a warmup that answered and then a wave that never went out is one billed
    # call, and the record says one rather than none. On the every-call-failed
    # arm it carries the warmup plus nothing, which is again the exact billed
    # total rather than an invented zero.
    if _per_trial_calls and not per_trial_succeeded and (
            _warmup_error is not None or per_trial_failed_calls):
        elapsed = time.time() - start
        if _warmup_error is not None:
            error_msg = (f"Stage 5 per-trial cache warmup error (attempt "
                         f"{retry_count + 1}): the shared prefix could not be "
                         f"warmed, so no trial call was issued; "
                         f"{type(_warmup_error).__name__}: {_warmup_error}")
            log.error("the Stage 5 per-trial cache warmup failed and no trial "
                      "call was issued; failing the patient so the retry "
                      "budget and the checkpoint see it", stage=5,
                      status="error", event="per_trial_warmup_floor",
                      retry=retry_count + 1, count=len(trials),
                      error_type=type(_warmup_error).__name__,
                      error_message=str(_warmup_error))
        else:
            error_msg = (f"Stage 5 per-trial API error (attempt "
                         f"{retry_count + 1}): all {per_trial_failed_calls} "
                         f"per-trial calls failed; last: "
                         f"{type(per_trial_last_error).__name__}: "
                         f"{per_trial_last_error}")
            log.error("every per-trial Stage 5 call failed; failing the "
                      "patient so the retry budget sees it rather than "
                      "reporting a run of not-evaluable trials as a success",
                      stage=5, status="error",
                      event="per_trial_all_calls_failed",
                      retry=retry_count + 1, count=per_trial_failed_calls,
                      error_type=type(per_trial_last_error).__name__,
                      error_message=str(per_trial_last_error))
        return {
            "llm_classifier_call_details": call_details,
            "evaluations": [],
            "llm_classifier_retries": retry_count + 1,
            "llm_classifier_truncation_splits": truncation_splits,
            "llm_classifier_output_tokens_estimated": estimated_output,
            "llm_classifier_output_split_threshold": split_threshold,
            "llm_classifier_output_ceiling": MATCHING_MAX_TOKENS,
            "llm_classifier_raw_response": "",
            **_billed_so_far(),
            "matching_model": model_answered,
            "error": error_msg,
            "llm_classifier_prompt_version": PROMPT_VERSION,
            "llm_classifier_prompt_sha256": system_prompt_sha256,
            "llm_classifier_patient_record_tokens": patient_record_tokens,
            "stage_timings": {**state.get("stage_timings", {}), "llm_classifier_evaluation": round(prior_llm_classifier_time + elapsed, 3)}
        }

    if per_trial_failed_calls:
        # SOME failed and at least one did not. Reported at WARNING because the
        # patient completed with a hole in it: the trials below reached no
        # judge at all, and the count is what separates "the model omitted
        # them" from "we never got an answer to omit".
        log.warning("some per-trial Stage 5 calls failed; those trials are "
                    "recorded as not evaluable and the patient completed",
                    stage=5, event="per_trial_calls_lost",
                    count=per_trial_failed_calls, total=calls_made,
                    degraded=True)

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
        # THE DRAFT IS SNAPSHOTTED HERE, WHICH IS THE FIRST PASS OVER THE
        # PARSED RESPONSE AND BEFORE ANY VALIDATOR HAS TOUCHED IT.
        #
        # AS OF THIS CHANGE IT IS EQUIVALENT TO SNAPSHOTTING AT COMPOSITION
        # TIME, AND THAT IS SAID PLAINLY RATHER THAN LEFT AS AN IMPLIED
        # NECESSITY. The one thing between here and the composition that used
        # to rewrite `assessment` was the absent-data validator's bracketed
        # annotation, which this same change deletes -- so nothing does, and a
        # revert harness confirmed that dropping this line changes no observed
        # value. It is kept as defence against the next validator that patches
        # the field, since the whole worth of `assessment_draft` is that it is
        # what the MODEL said and not what this pipeline made of it, and the
        # placement is held by tests/test_agent_composed_assessment.py check 5h
        # rather than by a behaviour that no longer exists to observe.
        #
        # In memory only -- there is no database column for it. See the block
        # above compose_assessment for what that costs on a SPLIT run, where
        # inferences.llm_classifier_raw_response holds the last chunk alone.
        eval_result["assessment_draft"] = eval_result.get("assessment", "")
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
                # THE ROW'S OWN RECORD OF WHAT IT SAID, written BEFORE the
                # rewrite below destroys it. `criterion_details` stores these
                # arrays verbatim, so this is what makes a stored
                # "not_evaluable" row distinguishable from one the model wrote
                # that way. Absent on every row that was not relabelled -- see
                # LABEL_REMAP_FIELD.
                c[LABEL_REMAP_FIELD] = status
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
            # THE SAME TWO FACTS ON THE ENTRY, so they reach trial_matches.
            # The audit list above feeds one log line and is then discarded;
            # everything a query needs per trial has to be on the entry, which
            # is the only thing that survives to the writer. Written only on the
            # non-canonical branch: for a canonical label the "original" IS
            # `eligible`, already stored one column away, and a repr of it would
            # be a second copy of a value that cannot disagree with itself.
            eval_result[VERDICT_ORIGINAL_LABEL_FIELD] = (
                repr(raw_verdict)[:_MALFORMED_ENTRY_PREVIEW_LEN])
            eval_result[VERDICT_ORIGINAL_TYPE_FIELD] = type(raw_verdict).__name__
        # UNCONDITIONAL, INCLUDING THE CANONICAL CASE, and that is deliberate.
        # `canonical` says this label was READ and needed no recovery; absence
        # says no normalizer ran for this entry, which is true of the four
        # _unevaluable_entry constructs appended after this loop. See
        # VERDICT_SOURCE_FIELD.
        eval_result[VERDICT_SOURCE_FIELD] = verdict_source
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
        # ONE SUBTRACTION, TWO CONSUMERS. `remapped_here` is derived from the
        # count rather than computed beside it, so the branch that corrects a
        # rejection and the number stored in trial_matches.criterion_remaps can
        # never disagree about whether this trial was remapped.
        remaps_here = len(label_remaps) - remaps_before
        eval_result[CRITERION_REMAPS_FIELD] = remaps_here
        remapped_here = remaps_here > 0

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
                "reason": UNEVALUABLE_REMAP_NO_SURVIVOR,
            })
            # The arrays are NOT touched, remapped statuses included: they are
            # what a reader consults instead of the composed sentence, which
            # deliberately says nothing about which row was rewritten.
            eval_result["eligible"] = TRIAL_VERDICT_NOT_EVALUABLE
            # THE MARKER, on the same footing as the one in the branch below:
            # the audit list feeds a log line and nothing else, so without this
            # the correction left no machine-readable trace on the ENTRY, and
            # compose_assessment kept a draft that reads "Known disqualifier:"
            # beside a verdict of not_evaluable. Its own reason constant is
            # reused rather than a new one minted -- the question this field
            # answers is "why was this not evaluated", and that string is the
            # answer already written for this branch. Like the other marker it
            # is deliberately NOT in _NOT_EVALUABLE_REASONS, whose members
            # index _unevaluable_entry's fixed explanation table, and the
            # strict response schema cannot emit the key, so an entry carrying
            # it was written here.
            eval_result["not_evaluable_reason"] = UNEVALUABLE_REMAP_NO_SURVIVOR
            _record_zero_score(eval_result, inc, exc)

        else:
            # Model-declared "not_eligible" with no surviving disqualifier and
            # no remap, or model-declared "not_evaluable" with criteria present,
            # or an UNRECOGNISED label whose criteria disqualify nobody.
            #
            # The third was the fabricated rejection this branch used to receive
            # as a settled "not_eligible" from Step 0 and pass through untouched
            # under a comment reading "verdict left as the model wrote it" --
            # which was false of exactly that case, and only that case. It is
            # recorded, with the label the model actually wrote.
            #
            # THE FIRST IS NOW CORRECTED TOO, and it is the arm that reaches
            # real model output most often: a readable rejection carrying no
            # disqualifying row at all. See UNEVALUABLE_REJECTION_UNSUPPORTED.
            #
            # The two arms are DISJOINT by construction, which is why this is
            # an elif rather than two independent tests: `verdict_unrecognized`
            # means normalize_trial_verdict returned None, and Step 0 has
            # already written not_evaluable for exactly that case, so an entry
            # still reading not_eligible here had a label the normalizer could
            # read. The elif says so; it is not an ordering preference.
            if verdict_unrecognized:
                unevaluable_trials.append({
                    "nct_id": nct_id,
                    "original_label": repr(raw_verdict)[:_MALFORMED_ENTRY_PREVIEW_LEN],
                    "reason": UNEVALUABLE_UNRECOGNIZED_VERDICT,
                })
            elif eval_result["eligible"] == TRIAL_VERDICT_NOT_ELIGIBLE:
                # `original_label` is the CANONICAL constant, not `raw_verdict`,
                # and that matches the out-of-vocabulary branch above rather
                # than the unrecognised one below-left. The model may have
                # written "Not Eligible" or boolean False and had it recovered
                # by normalize_trial_verdict; what this entry is about is the
                # missing evidence, not the spelling, and a non-canonical
                # spelling is already recorded in `verdict_normalizations` with
                # its repr and its type. Two lists, two findings, no overlap.
                unevaluable_trials.append({
                    "nct_id": nct_id,
                    "original_label": TRIAL_VERDICT_NOT_ELIGIBLE,
                    "reason": UNEVALUABLE_REJECTION_UNSUPPORTED,
                })
                # The arrays are NOT touched. They are the evidence that there
                # was no evidence, and criterion_details stores them verbatim.
                eval_result["eligible"] = TRIAL_VERDICT_NOT_EVALUABLE
                # THE MARKER, and it is the only machine-readable trace this
                # correction leaves on the ENTRY rather than in a log line.
                # compose_assessment reads it -- a corrected rejection is the
                # one not_evaluable population whose arrays are full and whose
                # draft is a rejection, so it is the one that must not keep
                # that draft. `not_evaluable_reason` is the existing field for
                # "why was this not evaluated", and this is an answer to that
                # question; it is deliberately NOT added to
                # _NOT_EVALUABLE_REASONS, whose members index
                # _unevaluable_entry's fixed explanation table.
                #
                # THE ONLY WRITER OF THIS KEY ON A MODEL-RETURNED ENTRY. The
                # model cannot supply it: the response schema is strict with
                # `additionalProperties: false` and this key is not among its
                # six properties, so an entry that carries it was written here.
                # tests/test_agent_unsupported_rejection.py asserts that
                # property of the schema rather than assuming it, because it is
                # what makes the marker trustworthy.
                eval_result["not_evaluable_reason"] = (
                    UNEVALUABLE_REJECTION_UNSUPPORTED)
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
 
                # THE ASSESSMENT IS NOT PATCHED HERE ANY MORE, AND THE DELETION
                # IS THE POINT RATHER THAN A TIDY-UP. This block used to prepend
                # "No known disqualifiers. [Validator corrected absent-data
                # disqualification.] " to the model's draft, because the draft
                # was what got stored and it still opened "Known disqualifier:"
                # after the flip. Since PROMPT_VERSION 1.5.0 the stored
                # assessment for an eligible trial is COMPOSED from the criteria
                # arrays -- which this validator has just corrected -- so a
                # write here would be overwritten unconditionally by the
                # composition pass below. A write nothing can read is the
                # declared-and-never-read shape this project reports on sight,
                # and leaving it would tell the next reader that the annotation
                # still reaches the record.
                #
                # NOTHING IS LOST. The correction is in `absent_data_corrections`
                # and in the `absent_data_correction` log event below, with its
                # count; the corrected rows now read "not_evaluable" in
                # criterion_details; and the model's original wording, opening
                # and all, is in `assessment_draft` and in
                # inferences.llm_classifier_raw_response.
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

    # ── Suspect temporal conflicts: flagged and counted, never rewritten ────
    #
    # HERE, AND THE POSITION IS THE WHOLE OF THE ORDERING GUARANTEE. Both
    # rewriters have finished: Step 1 has coerced every criterion status into
    # its arm's vocabulary, Step 3 has settled the trial verdict, and the
    # absent-data validator immediately above has already turned a
    # disqualification resting on an absent patient_value into `not_evaluable`.
    # So the arrays this scan reads are the arrays that will be stored, and a
    # row the validator corrected fails the status gate and is never flagged --
    # which matters because the two predicates overlap on real text ("No record
    # of infection; prior episode resolved 2012" satisfies both). Running this
    # first would attach a suspect flag to a row whose status the next block
    # then rewrote, leaving a contradiction in the stored record.
    #
    # BEFORE THE RECONCILIATION, and that costs nothing: the entries it appends
    # are built by `_unevaluable_entry`, which declares no `inclusion_criteria`
    # and no `exclusion_criteria` at all, so `_criteria_rows` yields nothing for
    # them wherever this runs. Scanning the model-returned population is also
    # what the finding is ABOUT -- the model disobeying RULE 4.
    #
    # See the block above `TEMPORAL_CONFLICT_FIELD` for why this only looks.
    _temporal_suspects = detect_temporal_conflicts(evaluations)
    if _temporal_suspects:
        _resolved_counts, _active_counts = temporal_conflict_marker_counts(
            _temporal_suspects)
        # INFO, not WARNING, and deliberately: a suspect row is an expected
        # observation at a measured rate rather than a fault, and every other
        # Stage 5 audit event -- label_remap, not_evaluable, verdict_
        # normalization, absent_data_correction -- reports at this level. The
        # two events that warn (reconciliation, assessment_composition_anomaly)
        # both name a state that should not occur.
        #
        # NOT ONE CLINICAL WORD IS IN THIS EVENT. `count` and `total` are
        # cardinalities, `nct_ids` are public registry identifiers, and the two
        # marker dicts are keyed by OUR OWN vocabulary. The quoted
        # patient_value and the criterion text are not here and must never be:
        # a criterion label beside a status is a clinical statement about this
        # patient, which is the rule that already keeps `response_preview` off
        # LOGGABLE_FIELDS. The text is auditable where it belongs -- in
        # `criterion_details`, on the row, beside its flag.
        log.info("criterion rows disqualify on a condition the record reports "
                 "as resolved (RULE 4); flagged for audit, not rewritten",
                 stage=5, event="temporal_conflict_suspect",
                 count=len(_temporal_suspects),
                 total=len({s["nct_id"] for s in _temporal_suspects}),
                 nct_ids=sorted({s["nct_id"] for s in _temporal_suspects
                                 if s["nct_id"]}),
                 temporal_conflict_resolved_markers=_resolved_counts,
                 temporal_conflict_active_markers=_active_counts)

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

    # ── The stored assessment is composed from the arrays ───────────────────
    #
    # LAST, AND OVER THE COMPLETE LIST, which is what makes it correct. Every
    # pass above can still move what this one reads: `_normalize_arm` rewrites a
    # criterion status, Step 3 rewrites the trial verdict, the absent-data
    # validator rewrites both and can flip a rejection to eligible, and the
    # reconciliation appends entries that were never in the response at all.
    # Composing earlier would render a state that the node then changed, and the
    # stored assessment would contradict the stored criteria -- which is the
    # defect this whole mechanism exists to remove, reintroduced by placement.
    #
    # `assessment_draft` is set with setdefault, so the model-returned entries
    # keep the snapshot taken before any validator ran, and the entries this
    # node CONSTRUCTED (a truncation floor, an exhausted split budget, a model
    # omission, conflicting duplicates) get their own fixed text as their draft.
    # Every entry carries the key, so no consumer has to test for its presence.
    _assessment_cases = Counter()
    _assessment_anomalies = []
    for _e in evaluations:
        _e.setdefault("assessment_draft", _e.get("assessment", ""))
        _case = assessment_composition_case(_e)
        _assessment_cases[_case] += 1
        if _case in _ASSESSMENT_ANOMALY_CASES:
            ASSESSMENT_COMPOSITION_ANOMALIES[_case] += 1
            _assessment_anomalies.append((_case, _e.get("nct_id", "")))
        _e["assessment"] = compose_assessment(_e)

    _composed = sum(_assessment_cases[_c] for _c in ASSESSMENT_COMPOSED_CASES)
    log.info("composed the stored assessment from the criteria arrays",
             stage=5, event="assessment_composition",
             count=_composed, total=len(evaluations),
             kept=len(evaluations) - _composed,
             reason=sorted(k for k, v in _assessment_cases.items() if v))
    if _assessment_anomalies:
        # Unreachable if the normalizer above ran: Step 3 only writes
        # not_eligible off a surviving disqualifying row, the fall-through
        # branch now corrects an unsupported model-declared one to
        # not_evaluable, and Step 0 resolves every verdict into the
        # three-member vocabulary. So this is not a degradation to absorb -- it
        # says the composition saw a verdict the normalizer did not produce,
        # and the trial kept the model's draft.
        log.warning("kept the model's draft assessment: the verdict and the "
                    "criteria arrays could not support a composed one",
                    stage=5, event="assessment_composition_anomaly",
                    count=len(_assessment_anomalies),
                    reason=sorted({c for c, _ in _assessment_anomalies}),
                    nct_ids=[n for _, n in _assessment_anomalies if n])

    # ── The per-trial record that the check ran ────────────────────────────
    #
    # Stamped on EVERY surviving evaluation, including the ones this node
    # constructed itself (a truncation floor, an exhausted split budget, a
    # model omission). Those name a trial that was in the candidate set by
    # definition, so 0 is the true answer for them as much as for a verdict the
    # model returned.
    #
    # It is written here, on the success path only, and that is what gives
    # trial_matches.hallucinated its meaning: NULL is a row from a run where
    # this node never reached this line -- an API failure, a refusal, an
    # unparseable response, or a database written before the detector existed.
    # A row that says 0 is a row that was checked.
    for _e in evaluations:
        _e["hallucinated"] = HALLUCINATION_CHECKED_CLEAN

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
        # ── What that estimate was judged against ──────────────────────
        #
        # THE SPLIT-PRESSURE MEASUREMENT, and it is stored rather than
        # recomputed for the reason oncotriage/config.py already gives
        # about this splitter: at MAX_TRIALS_FOR_EVALUATION = 15 the
        # largest estimate this node can produce is 20,625 against a
        # threshold of 28,800, so the proactive guard CANNOT FIRE and its
        # counter will read zero forever. A guard that never fires and a
        # guard that fires often are the same zero unless the headroom is
        # on the row, and both constants behind these numbers have moved
        # once already (the ceiling was 16,000 under GPT-4o).
        #
        # ON EVERY RETURN, NOT ONLY THIS ONE. Both are computed above the
        # send loop, before any request is issued, so they are facts about
        # this run whether or not it answered -- exactly the argument
        # llm_classifier_patient_record_tokens and the prompt hash are
        # carried on the failure paths by, and NOT hallucinated_trials'
        # argument, which is about a check that may not have completed.
        #
        # THE INPUT GUARD IS ABSENT HERE ON PURPOSE: its estimate and both
        # of its budgets are already inside llm_classifier_packing below,
        # and a number derivable from a stored column is not stored twice.
        "llm_classifier_output_split_threshold": split_threshold,
        "llm_classifier_output_ceiling": MATCHING_MAX_TOKENS,
        "not_evaluable_truncated": not_evaluable_truncated,
        # How many entries the model returned for a trial that is in NO sent
        # set of this run -- FABRICATED ONLY. A cross-chunk repeat is dropped
        # by the same code and counted separately, in the log event, because it
        # names a real candidate and costs the patient nothing.
        # WRITTEN ONLY HERE, on the success path, and deliberately absent
        # from every early return above: those end the node before the whole
        # response has been compared against the whole candidate set, so any
        # number they carried would be a partial count reported as a total.
        # A key that is never written leaves state.get() at None, and
        # _pipeline_provenance turns that into a NULL column meaning "the
        # detector did not run" -- the same convention as
        # llm_classifier_prompt_sha256. 0 is therefore a measurement.
        "hallucinated_trials": len(hallucinated_ids),
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
        # The cached share of llm_classifier_input_tokens, on the identical
        # convention: a subset, never an addition, and None -- not 0 -- when no
        # response reported it. See the accumulator.
        "llm_classifier_cached_input_tokens": (cached_input_tokens
                                               if cached_input_reported
                                               else None),
        # The same four readings PER CALL, in the order the calls were issued.
        # Written on every return of this node, not only here -- see the
        # accumulator for why a list is not a count.
        "llm_classifier_call_details": call_details,
        # ── What the INPUT packer did ──────────────────────────────────────
        #
        # WRITTEN ON THE SUCCESS PATH ONLY, on the hallucinated_trials
        # precedent rather than the truncation-counter one. The chunk list is a
        # record of requests that were ISSUED; a run that died at its first
        # call packed the same way and sent one of them, and publishing the
        # full plan as though it had all been sent would be a partial count
        # reported as a total. A key never written leaves state.get() at None
        # and _pipeline_provenance turns that into "the packer's record does
        # not describe this run".
        #
        # TWO KEYS RATHER THAN ONE, because they answer different questions and
        # one of them is a scalar a query can group by. The count is the
        # headline; the report is the detail behind it.
        "llm_classifier_packed_chunks": len(packing_report["chunks"]),
        "llm_classifier_packing": {
            **packing_report,
            # The identity of the prefix every one of those chunks shared. The
            # SAME VALUE as llm_classifier_prompt_sha256 and the same variable,
            # so the two cannot drift; it is repeated here because the packing
            # record's whole claim is "these N requests had one prefix", and a
            # record that does not name the prefix cannot support it.
            "prefix_sha256": system_prompt_sha256,
        },
        # The model that actually answered. File 14 logs this into
        # inferences.matching_model and prices against it, so the stored cost is
        # computed from the model that produced the tokens rather than from
        # whatever MATCHING_MODEL happens to say at read time.
        "matching_model": model_answered,
        "cross_vocab_remaps": len(label_remaps),
        # ── The two run-level provenance counters (this pass) ───────────────
        #
        # WRITTEN ON THE SUCCESS RETURN ONLY, which is hallucinated_trials'
        # convention and not the truncation counters'. Both describe a CHECK
        # rather than work that either happened or did not: 0 asserts that the
        # normalizer read every returned entry's label and every trial's
        # criteria arrays and found nothing to correct, which is a claim no run
        # that ended before this line is entitled to make. A key that is never
        # written leaves state.get() at None and _pipeline_provenance turns that
        # into a NULL column meaning "the normalizer did not report".
        #
        # NEITHER IS DERIVABLE FROM cross_vocab_remaps, which is a count of
        # remap EVENTS for the whole run.
        #
        #   verdict_normalizations  how many trial verdicts arrived in a
        #                           non-canonical spelling. A different artifact
        #                           entirely: it reached NO storage before this
        #                           pass, only one log line.
        #   remapped_trials         how many TRIALS carried at least one
        #                           criterion remap. Four remaps on one trial
        #                           and four trials with one each are the same
        #                           cross_vocab_remaps and different findings.
        #
        # BOTH ARE ALSO DERIVABLE BY JOINING trial_matches, and they are stored
        # anyway for the reason inferences.hallucinated_trials is: a COUNT over
        # a child table returns 0 for "measured none", for "these rows predate
        # the column" and for "no Stage 5 ran" alike, and those are the three
        # states this project's NULL convention exists to keep apart.
        "verdict_normalizations": len(verdict_normalizations),
        "remapped_trials": len({r["nct_id"] for r in label_remaps}),
        "error": "",  # Clear error on success
        # What was intended, and what was sent. PROMPT_VERSION is a module
        # constant rather than a state read because the render happened in
        # this process, in this call; the hash is of that exact render.
        # Both reach inferences via _pipeline_provenance() (terminal.py),
        # which is what puts them on the no-candidate and error paths too.
        "llm_classifier_prompt_version": PROMPT_VERSION,
        "llm_classifier_prompt_sha256": system_prompt_sha256,
        # How many estimated tokens of the cached prefix were THIS PATIENT's
        # record, measured on the neutralized bytes that were interpolated. See
        # the render site for why it is measured there and why the template's
        # own share is derivable rather than stored.
        "llm_classifier_patient_record_tokens": patient_record_tokens,
        "stage_timings": {**state.get("stage_timings", {}), "llm_classifier_evaluation": round(prior_llm_classifier_time + elapsed, 3)}
    }


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""
