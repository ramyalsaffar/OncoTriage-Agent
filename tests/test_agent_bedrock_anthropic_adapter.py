######################################################################
# Stage 5 on Bedrock Converse: the flag is off, and the mapping is right
######################################################################

"""Bedrock Converse (Anthropic Claude) Adapter Test

``oncotriage/agent/bedrock_anthropic_adapter.py`` translates the Stage 5
request onto Amazon Bedrock's **Converse** API, behind
``config.MATCHING_PROVIDER == "bedrock_anthropic"``. This file holds it to
three claims:

  1. WITH THE FLAG OFF NOTHING CHANGED. Not "equivalent" -- the same client
     object, the same kwargs, and no Converse client built, cached or reached,
     and boto3 not imported. Section 1 asserts that structurally (an AST pin on
     the shipped ``call_matching_model``) AND behaviourally (recording stubs
     for all three providers installed through ``deps``, driven through the
     real function, with both Bedrock stand-ins required to have been
     untouched).

  2. WITH THE FLAG ON EVERY FIELD MAPS, BOTH DIRECTIONS. Sections 3 and 4 pin
     the request and the response field by field against the values config
     actually holds -- including the two mappings that are not renames: the
     schema travelling as a serialized STRING, and the DISJOINT usage
     arithmetic that OpenAI's convention requires be summed back together.

  3. THE SHIPPED SCHEMA IS INSIDE BEDROCK'S DOCUMENTED SUBSET. Section 2
     re-derives that walk rather than trusting the measurement taken when this
     module was written, so a schema edit that introduced ``minLength`` or
     ``additionalProperties: true`` fails here rather than as a 400 mid-campaign.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO CORPUS, NO GIT HISTORY, NO
DATABASE -- and NO boto3. Every client is a stand-in installed through
``oncotriage/agent/deps.py``; every response is a literal dict; the request
builder and the response translator import no AWS library at all, which is what
makes this file runnable on a machine where boto3 is not installed. That is not
a hypothetical: it is the machine this module was written on.
``ONCOTRIAGE_DEFER_LOCAL_MODELS`` is set ABOVE the package imports (pass
20c-3d's ordering lesson: ``deps`` reads it once, at its own import, which
arrives transitively on the first ``oncotriage`` import).

IT DOES EXEC -- in-memory copies of
``oncotriage/agent/bedrock_anthropic_adapter.py``, one per plant, to be argued
at ``_EXEC_ALLOWLIST`` in ``tests/test_package_invariants.py``. A ``git show``
control is impossible for every one of them: the module has no prior revision,
and each plant is a one-token edit INSIDE a function body to code that exists at
HEAD and nowhere else. The copies are exec'd into a real ``ModuleType`` because
a function's globals ARE the dict it was exec'd into. The shipped file is
sha256'd before the first plant and compared at the end.

NOT in ``tests/run_serial_tests.py``'s collision matrix, derived rather than
assumed: it writes nothing anywhere -- not even a temp directory -- and the
three repository files it READS are ``oncotriage/agent/bedrock_anthropic_
adapter.py``, ``oncotriage/agent/evaluation.py`` and ``oncotriage/config.py``.
The last IS rewritten in place by
``tests/test_config_snapshot_date_rot.py``, so all three are sha256-compared at
the end and an interleaved serial run is visible rather than silent.

EVERY CONFIG MUTATION IS INSIDE try/finally AND THE RESTORE IS ASSERTED.
Section 9 re-reads every knob this file touches, and derives the list from
config itself rather than retyping it.

NOTHING CALLS INTO PRODUCTION CODE BARE. Every driver returns a marker on a
raise instead of letting it escape through ``check()``'s argument list -- the
abort shape this project has shipped fourteen times -- so a plant that makes a
function raise produces recorded FAILURES and a summary, not one traceback.

    python tests/test_agent_bedrock_anthropic_adapter.py
"""

import ast
import contextlib
import hashlib
import io
import json
import os
import sys
import types

# ABOVE THE PACKAGE IMPORTS ON PURPOSE. See the module docstring.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

try:
    import oncotriage                                          # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
         if "__file__" in globals() else None, "__file__"),
        (os.getcwd(), "cwd"),
    ):
        if _candidate and os.path.isdir(os.path.join(_candidate, "oncotriage")):
            if _candidate not in sys.path:
                sys.path.insert(0, _candidate)
            print(f"[Bootstrap] oncotriage package found at {_candidate} "
                  f"(via {_how}); added to sys.path")
            break
    else:
        raise
    del _candidate, _how

from oncotriage import config
from oncotriage import degradation as _degradation
from oncotriage.agent import bedrock_anthropic_adapter as bac
from oncotriage.agent import deps
from oncotriage.agent import evaluation as _evaluation
from oncotriage.agent.response_schema import (
    RESPONSE_SCHEMA_NAME, build_response_format, build_response_schema)


# ===========================================================================
# HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, actual, expected):
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected: {expected}\n"
                         f"          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def check_true(label, condition):
    check(label, bool(condition), True)


def section(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


class Raised:
    """What a driver returns instead of letting an exception escape.

    A BARE CALL INSIDE ``check(...)`` RAISES WHILE THE ARGUMENT IS BEING
    EVALUATED, which aborts the file with no summary -- and it does so on
    exactly the plants this file exists to catch.
    """

    __slots__ = ("kind", "message")

    def __init__(self, exc):
        self.kind = type(exc).__name__
        self.message = str(exc)

    def __eq__(self, other):
        return isinstance(other, Raised) and self.kind == other.kind

    def __repr__(self):
        return f"<raised {self.kind}: {self.message[:90]}>"


def drive(fn, *args, **kwargs):
    """Call fn with both output channels captured; return its value or Raised.

    OUTPUT IS CAPTURED BECAUSE THE ADAPTER LOGS. Several drivers below hit the
    once-per-process WARNING paths on purpose, and a test whose PASS lines are
    interleaved with JSON log records is a test nobody reads. Nothing
    suppressed is asserted on: every assertion reads the returned value or a
    counter.
    """
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            return fn(*args, **kwargs)
    except Exception as exc:                                   # noqa: BLE001
        return Raised(exc)


def raises(fn, *args, **kwargs):
    """The exception TYPE NAME fn raised, or the string '<did not raise>'."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            fn(*args, **kwargs)
    except Exception as exc:                                   # noqa: BLE001
        return type(exc).__name__
    return "<did not raise>"


def message_of(fn, *args, **kwargs):
    """The exception MESSAGE fn raised, or '<did not raise>'."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            fn(*args, **kwargs)
    except Exception as exc:                                   # noqa: BLE001
        return str(exc)
    return "<did not raise>"


def at(mapping, *keys, default="<absent>"):
    """A chained lookup that cannot raise. A missing key is a FAILURE, not an
    abort -- and a missing key is precisely what several plants produce."""
    node = mapping
    for key in keys:
        try:
            node = node[key]
        except Exception:                                      # noqa: BLE001
            return default
    return node


def sub(text, old, new, expect):
    """Replace, refusing a plant that did not match exactly `expect` times.

    A PLANT THAT MATCHED NOTHING REPORTS 'MISSED' AGAINST A CHECK THAT WORKS,
    which is a different finding from a weak check and has cost this project a
    pass before. It raises here instead.
    """
    seen = text.count(old)
    if seen != expect:
        raise AssertionError(
            f"plant matched {seen} time(s), expected {expect}: {old[:70]!r}")
    return text.replace(old, new)


_PLANT_SEQ = [0]
_ADAPTER_PATH = os.path.abspath(bac.__file__)
_EVALUATION_PATH = os.path.abspath(_evaluation.__file__)
_CONFIG_PATH = os.path.abspath(config.__file__)
_SHA_BEFORE = {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
               for p in (_ADAPTER_PATH, _EVALUATION_PATH, _CONFIG_PATH)}


def exec_copy(mutate):
    """Exec a MUTATED in-memory copy of the adapter into a real module."""
    _PLANT_SEQ[0] += 1
    text = open(_ADAPTER_PATH, encoding="utf-8").read()
    planted = mutate(text)
    if planted == text:
        raise AssertionError("the plant matched nothing")
    name = f"_planted_bac_{_PLANT_SEQ[0]}"
    module = types.ModuleType(name)
    module.__file__ = _ADAPTER_PATH
    module.__package__ = "oncotriage.agent"
    sys.modules[name] = module
    exec(compile(planted, _ADAPTER_PATH, "exec"), module.__dict__)
    return module


# THE SHIPPED VALUES, CAPTURED AT IMPORT, BEFORE ANY `provider()` BLOCK.
#
# DERIVED FROM CONFIG RATHER THAN RETYPED: every module-level name beginning
# BEDROCK_ANTHROPIC_, plus MATCHING_PROVIDER. A knob added to config tomorrow
# joins the leak check for free, which a hand-written list cannot do -- and a
# hand-written list is how this file would come to assert that nothing leaked
# while something did.
_KNOB_NAMES = tuple(sorted(
    n for n in vars(config) if n.startswith("BEDROCK_ANTHROPIC_")))
_SHIPPED_AT_IMPORT = {n: getattr(config, n)
                      for n in ("MATCHING_PROVIDER",) + _KNOB_NAMES}


# ===========================================================================
# STAND-INS
# ===========================================================================

class _Recorder:
    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def create(self, **kwargs):
        return self(**kwargs)


class _OpenAIStub:
    """`client.chat.completions.create(...)` and nothing else."""

    def __init__(self, reply=None):
        self.recorder = _Recorder(reply)
        self.chat = types.SimpleNamespace(completions=self.recorder)


class _ResponsesStub:
    """`client.responses.create(...)` -- the OTHER Bedrock branch's surface."""

    def __init__(self, reply=None):
        self.recorder = _Recorder(reply)
        self.responses = self.recorder


class _ConverseStub:
    """`client.converse(...)` and nothing else.

    IT DELIBERATELY HAS NEITHER ``chat`` NOR ``responses``. A stand-in that
    answered every surface could not tell the three dispatch branches apart,
    which is the one thing section 1 exists to measure.
    """

    def __init__(self, reply=None):
        self.recorder = _Recorder(reply)

    def converse(self, **kwargs):
        return self.recorder(**kwargs)

    @property
    def calls(self):
        return self.recorder.calls


RESPONSE_JSON = json.dumps({"evaluations": []})

# THE CANONICAL REPLY. Deliberately carries a `reasoningContent` block FIRST
# with text in it, so "reasoning blocks are skipped" is a claim this fixture can
# falsify -- a reasoning block with no text would make that check vacuous. And
# deliberately carries a CACHE READ, because the disjointness arithmetic is
# identity-like at zero and the whole point is that it is not a rename.
CONVERSE_REPLY = {
    "ResponseMetadata": {"RequestId": "req-0123456789"},
    "output": {"message": {"role": "assistant", "content": [
        {"reasoningContent": {"reasoningText": {"text": "SHOULD NOT APPEAR"}}},
        {"text": RESPONSE_JSON},
    ]}},
    "stopReason": "end_turn",
    "usage": {
        "inputTokens": 1080,
        "outputTokens": 2400,
        "totalTokens": 3480,
        "cacheReadInputTokens": 17920,
        "cacheWriteInputTokens": 0,
    },
}


@contextlib.contextmanager
def provider(name=None, **knobs):
    """Set MATCHING_PROVIDER (and any knob) for one block, then restore."""
    saved = {"MATCHING_PROVIDER": config.MATCHING_PROVIDER}
    for key in knobs:
        saved[key] = getattr(config, key)
    if name is not None:
        config.MATCHING_PROVIDER = name
    for key, value in knobs.items():
        setattr(config, key, value)
    try:
        yield
    finally:
        for key, value in saved.items():
            setattr(config, key, value)


@contextlib.contextmanager
def overrides(**mapping):
    """Install deps overrides for one block and remove them after."""
    saved = deps.set_overrides({getattr(deps, k.upper()): v
                                for k, v in mapping.items()})
    try:
        yield
    finally:
        deps.restore_overrides(saved)


@contextlib.contextmanager
def counters_zeroed():
    """Clear the adapter's counter for one block and restore it after.

    THE COUNTER IS PROCESS-GLOBAL AND SEVERAL SECTIONS BUMP IT ON PURPOSE, so a
    section that asserts "this bumped exactly once" has to start from a known
    state or it is asserting about every section above it as well.
    """
    saved = dict(bac.BEDROCK_ANTHROPIC_DEGRADATIONS)
    bac.BEDROCK_ANTHROPIC_DEGRADATIONS.clear()
    try:
        yield bac.BEDROCK_ANTHROPIC_DEGRADATIONS
    finally:
        bac.BEDROCK_ANTHROPIC_DEGRADATIONS.clear()
        bac.BEDROCK_ANTHROPIC_DEGRADATIONS.update(saved)


# ===========================================================================
# SECTION 1 — THE FLAG IS OFF AND NOTHING CHANGED
# ===========================================================================

section("1. Flag OFF: the Converse branch is unreachable and the OpenAI "
        "request is unchanged")

check("the shipped provider is still OpenAI",
      config.MATCHING_PROVIDER, config.MATCHING_PROVIDER_OPENAI)
check("...spelt exactly, which is what the whole file's claim rests on",
      config.MATCHING_PROVIDER, "openai")

# --- 1a. The dispatch, pinned by AST ---------------------------------------
#
# THE BEHAVIOURAL CHECK BELOW CANNOT SEE A BRANCH THAT WAS ADDED ABOVE THE
# OPENAI RETURN AND ALSO MADE UNREACHABLE. This one reads the SOURCE, so the
# ORDER of the three arms is pinned: every provider arm must precede the
# unconditional OpenAI return, and the OpenAI return must be last.
_EVAL_SRC = open(_EVALUATION_PATH, encoding="utf-8").read()
_EVAL_TREE = ast.parse(_EVAL_SRC)
_call_fn = next((n for n in ast.walk(_EVAL_TREE)
                 if isinstance(n, ast.FunctionDef)
                 and n.name == "call_matching_model"), None)
check_true("call_matching_model is present in evaluation.py", _call_fn is not None)

if _call_fn is not None:
    _body_src = ast.unparse(_call_fn)
    check_true("the Converse adapter is reached from call_matching_model",
               "call_matching_model_bedrock_anthropic" in _body_src)
    check_true("...and the Responses adapter still is too",
               "call_matching_model_bedrock(" in _body_src)
    # The LAST statement of the function is the unconditional OpenAI return.
    _last = _call_fn.body[-1]
    check("the final statement is still the unconditional OpenAI return",
          isinstance(_last, ast.Return), True)
    check_true("...and it names the OpenAI client, unguarded",
               "get_openai_client" in ast.unparse(_last))
    # Every provider dispatch is an `if` ABOVE it.
    _dispatch_ifs = [n for n in _call_fn.body
                     if isinstance(n, ast.If)
                     and "MATCHING_PROVIDER" in ast.unparse(n.test)]
    check("there are exactly three provider guards above that return "
          "(bedrock, bedrock_anthropic, and the unrecognised-provider raise)",
          len(_dispatch_ifs), 3)

# --- 1b. Behavioural: drive the real function ------------------------------
#
# ALL THREE CLIENTS ARE INSTALLED AT ONCE. Installing only the OpenAI one would
# make "the Converse client was untouched" true because nothing could have
# reached it; installing all three makes it a statement about the DISPATCH.
_openai = _OpenAIStub(reply="OPENAI-REPLY")
_responses = _ResponsesStub(reply="RESPONSES-REPLY")
_converse = _ConverseStub(reply=CONVERSE_REPLY)

with overrides(openai_client=_openai, bedrock_client=_responses,
               bedrock_anthropic_client=_converse):
    _got = drive(_evaluation.call_matching_model, "SYS", "USR")

check("with the flag off the OpenAI client answered", _got, "OPENAI-REPLY")
check("...the OpenAI client was called exactly once",
      len(_openai.recorder.calls), 1)
check("...the RESPONSES Bedrock client was NOT touched",
      len(_responses.recorder.calls), 0)
check("...and the CONVERSE Bedrock client was NOT touched",
      len(_converse.calls), 0)

_sent = _openai.recorder.calls[0] if _openai.recorder.calls else {}
check("the OpenAI request still names MATCHING_MODEL",
      at(_sent, "model"), config.MATCHING_MODEL)
check("...still sends max_completion_tokens",
      at(_sent, "max_completion_tokens"), config.MATCHING_MAX_TOKENS)
check("...still sends the seed",
      at(_sent, "seed"), config.MATCHING_SEED)
check("...still sends reasoning_effort",
      at(_sent, "reasoning_effort"), config.MATCHING_REASONING_EFFORT)
check("...still sends the chat-shaped response_format",
      at(_sent, "response_format"), build_response_format())
check("...and no Converse field leaked into it",
      [k for k in _sent if k in ("modelId", "outputConfig", "system",
                                 "inferenceConfig")], [])

# --- 1c. boto3 was not imported --------------------------------------------
#
# THE STRONGEST FORM OF "no client was built", and it is free: importing the
# adapter, importing the package and driving the OpenAI path must all leave
# boto3 out of sys.modules. If boto3 is INSTALLED and something else imported
# it this check would be a false alarm, so it reports what it found rather than
# asserting blind -- and on this machine boto3 is not installed at all, which
# is itself the point that the request builder needs none.
check("boto3 is not in sys.modules after importing and driving the package",
      "boto3" in sys.modules, False)
check("...nor botocore", "botocore" in sys.modules, False)

# --- 1d. The Converse client factory refuses while the flag is off ---------
check("config.get_bedrock_anthropic_client() REFUSES while the provider is "
      "openai, so the guarantee is a property of the function rather than of "
      "the call graph",
      raises(config.get_bedrock_anthropic_client), "RuntimeError")
check_true("...and the refusal names the override seam a test should use",
           "BEDROCK_ANTHROPIC_CLIENT"
           in message_of(config.get_bedrock_anthropic_client))

# --- 1e. An unrecognised provider raises rather than defaulting ------------
with provider("bedrock_anthropi"):        # one character short, on purpose
    check("a MISSPELT provider raises rather than silently billing the "
          "incumbent",
          raises(_evaluation.call_matching_model, "SYS", "USR"), "RuntimeError")
    check_true("...and the message names the accepted vocabulary",
               "bedrock_anthropic" in message_of(
                   config.validate_matching_provider_config))


# ===========================================================================
# SECTION 2 — THE SHIPPED SCHEMA IS INSIDE BEDROCK'S DOCUMENTED SUBSET
# ===========================================================================

section("2. The Stage 5 schema uses no JSON Schema feature Bedrock forbids")

# structured-output.html, "Supported JSON Schema features", read 2026-08-30.
# The NOT-supported list, verbatim: recursive schemas; external $ref
# references; numerical constraints (minimum, maximum, multipleOf); string
# constraints (minLength, maxLength); additionalProperties set to anything
# other than false.
_FORBIDDEN_KEYWORDS = ("minimum", "maximum", "multipleOf",
                       "minLength", "maxLength")


def _forbidden_features(node, path="$"):
    """Every Bedrock-forbidden construct in a schema, as (pointer, keyword)."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _FORBIDDEN_KEYWORDS:
                found.append((f"{path}/{key}", key))
            if key == "additionalProperties" and value is not False:
                found.append((f"{path}/additionalProperties", "additionalProperties"))
            if key == "$ref" and isinstance(value, str) and not value.startswith("#"):
                found.append((f"{path}/$ref", "external $ref"))
            found.extend(_forbidden_features(value, f"{path}/{key}"))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(_forbidden_features(value, f"{path}[{i}]"))
    return found


_SCHEMA = build_response_schema()
check("the shipped Stage 5 schema uses NO feature Bedrock's structured-output "
      "subset forbids", _forbidden_features(_SCHEMA), [])

# NON-DEGENERACY. A walk that matched nothing would satisfy the check above for
# the wrong reason -- and it is the reason a walk fails silently.
_POISONED = json.loads(json.dumps(_SCHEMA))
_POISONED["properties"]["evaluations"]["items"]["properties"]["match_score"]["minimum"] = 0
check("...and the walk can see one when there is one to see",
      [k for _, k in _forbidden_features(_POISONED)], ["minimum"])
_POISONED2 = json.loads(json.dumps(_SCHEMA))
_POISONED2["additionalProperties"] = True
check("...including a widened additionalProperties",
      [k for _, k in _forbidden_features(_POISONED2)], ["additionalProperties"])

check("the schema is JSON-serializable, which Converse requires of it",
      isinstance(json.dumps(_SCHEMA), str), True)
check("...and round-trips unchanged, so nothing is lost in the string form",
      json.loads(json.dumps(_SCHEMA)), _SCHEMA)


# ===========================================================================
# SECTION 3 — THE REQUEST, FIELD BY FIELD
# ===========================================================================

section("3. Flag ON: the Converse request maps field by field")

with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
    _req = drive(bac.build_converse_request, "SYSTEM TEXT", "USER TEXT")

check("build_converse_request returns a dict", isinstance(_req, dict), True)
check("modelId is the configured wire model",
      at(_req, "modelId"), config.BEDROCK_ANTHROPIC_MATCHING_MODEL)
check("...which is what matching_wire_model() answers on this provider",
      at(_req, "modelId"),
      (lambda: [config.__setattr__("MATCHING_PROVIDER",
                                   config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC),
                config.matching_wire_model(),
                config.__setattr__("MATCHING_PROVIDER",
                                   config.MATCHING_PROVIDER_OPENAI)][1])())

check("the system prompt is the top-level `system` parameter, not a message",
      at(_req, "system", 0, "text"), "SYSTEM TEXT")
check("...and a cachePoint FOLLOWS it, which is where the stable prefix ends",
      at(_req, "system", 1, "cachePoint"),
      {"type": "default", "ttl": config.BEDROCK_ANTHROPIC_CACHE_TTL})
check("...and there is nothing else in `system`", len(at(_req, "system", default=[])), 2)

check("the user prompt is one text block on one user message",
      at(_req, "messages"),
      [{"role": "user", "content": [{"text": "USER TEXT"}]}])
check("NO cachePoint sits in `messages` -- the per-trial text differs on every "
      "call, so a checkpoint there is a cache WRITE per call with no read to "
      "follow it", "cachePoint" in json.dumps(at(_req, "messages", default=[])),
      False)

check("maxTokens is MATCHING_MAX_TOKENS, not recomputed",
      at(_req, "inferenceConfig", "maxTokens"), config.MATCHING_MAX_TOKENS)
check("...and nothing else is in inferenceConfig -- temperature is still not "
      "sent", sorted(at(_req, "inferenceConfig", default={})), ["maxTokens"])

# --- the structured-output mapping, which is the risky one -----------------
_fmt = at(_req, "outputConfig", "textFormat", default={})
check("outputConfig.textFormat.type is json_schema", at(_fmt, "type"), "json_schema")
_js = at(_fmt, "structure", "jsonSchema", default={})
check("...the schema hangs off structure.jsonSchema", sorted(_js), ["name", "schema"])
check("...the NAME is the Stage 5 schema's own", at(_js, "name"), RESPONSE_SCHEMA_NAME)
check("...THE SCHEMA IS A STRING, which is what Converse's member is",
      isinstance(at(_js, "schema"), str), True)
check("...and it deserializes to EXACTLY build_response_schema(), so it was "
      "unwrapped rather than rebuilt",
      json.loads(at(_js, "schema", default="null")), build_response_schema())
check("...with no whitespace nobody asked for",
      ", " in at(_js, "schema", default=""), False)
check("`strict` is NOT forwarded -- Converse has no such member on jsonSchema; "
      "the field IS the constrained decode", "strict" in _js, False)
check("no description is sent by default", "description" in _js, False)

check("effort is OMITTED while BEDROCK_ANTHROPIC_EFFORT is None",
      "effort" in at(_req, "outputConfig", default={}),
      config.BEDROCK_ANTHROPIC_EFFORT is not None)

check("thinking travels in additionalModelRequestFields, the one escape hatch "
      "Converse offers",
      at(_req, "additionalModelRequestFields"),
      {"thinking": {"type": config.BEDROCK_ANTHROPIC_THINKING}})

check("the model echo is ASKED FOR through additionalModelResponseFieldPaths",
      at(_req, "additionalModelResponseFieldPaths"), [bac.MODEL_ECHO_POINTER])

check("serviceTier is OMITTED, which IS Standard",
      "serviceTier" in _req, config.BEDROCK_ANTHROPIC_SERVICE_TIER is not None)

# --- what must NOT be on the wire ------------------------------------------
#
# THE SERIALIZED SCHEMA IS EXCLUDED FROM THE SEARCH. A substring test over the
# whole request would false-FAIL the day a Stage 5 schema field is named
# something containing "seed" or "store" -- a check that fails on a change it
# has no opinion about. Measured today: none of the four needles appears in the
# schema string, so excluding it changes no current answer and removes a
# landmine. The exclusion is asserted non-vacuous below.
_req_no_schema = json.loads(json.dumps(_req))
_req_no_schema["outputConfig"]["textFormat"]["structure"]["jsonSchema"]["schema"] = ""
_flat = json.dumps(_req_no_schema)
check("the schema really was excluded from the search corpus, so these four "
      "checks are about the REQUEST rather than about field names",
      len(_flat) < len(json.dumps(_req)), True)
check("NO seed anywhere on the wire -- Converse has no such field",
      "seed" in _flat, False)
check("NO reasoning_effort -- the OpenAI vocabulary is not Anthropic's",
      "reasoning_effort" in _flat, False)
check("NO store -- the Converse API states it retains nothing, so the "
      "Responses branch's retention parameter has no analogue to send",
      "store" in _flat, False)
check("NO temperature -- MATCHING_TEMPERATURE is None and the field is optional",
      "temperature" in _flat, False)
check("NO toolConfig -- Stage 5 defines no tools, which is why stopReason "
      "tool_use raises", "toolConfig" in _flat, False)
check("NO timeout kwarg -- botocore takes it on the CLIENT, not per call",
      "timeout" in _req, False)

check("the whole request is JSON-serializable, which botocore requires",
      isinstance(_flat, str), True)

# --- the knobs actually reach the request ----------------------------------
with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC,
              BEDROCK_ANTHROPIC_CACHE_TTL="1h",
              BEDROCK_ANTHROPIC_EFFORT="high",
              BEDROCK_ANTHROPIC_SERVICE_TIER="default",
              BEDROCK_ANTHROPIC_THINKING="adaptive",
              BEDROCK_ANTHROPIC_SCHEMA_DESCRIPTION="a description"):
    _req2 = drive(bac.build_converse_request, "S", "U")
check("a 1h TTL reaches the cachePoint",
      at(_req2, "system", 1, "cachePoint", "ttl"), "1h")
check("an effort reaches outputConfig.effort",
      at(_req2, "outputConfig", "effort"), "high")
check("a service tier reaches serviceTier.type",
      at(_req2, "serviceTier"), {"type": "default"})
check("an adaptive thinking mode reaches additionalModelRequestFields",
      at(_req2, "additionalModelRequestFields", "thinking", "type"), "adaptive")
check("a schema description reaches jsonSchema.description",
      at(_req2, "outputConfig", "textFormat", "structure", "jsonSchema",
         "description"), "a description")

with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC,
              BEDROCK_ANTHROPIC_CACHE_TTL=None,
              BEDROCK_ANTHROPIC_THINKING=None,
              BEDROCK_ANTHROPIC_REQUEST_MODEL_ECHO=False):
    _req3 = drive(bac.build_converse_request, "S", "U")
check("a None TTL omits the cachePoint ENTIRELY rather than sending a null",
      at(_req3, "system"), [{"text": "S"}])
check("a None thinking omits additionalModelRequestFields entirely -- an empty "
      "object is a field botocore would send",
      "additionalModelRequestFields" in _req3, False)
check("turning the echo request off removes the pointer",
      "additionalModelResponseFieldPaths" in _req3, False)

# --- the strict guard -------------------------------------------------------
#
# NOT A HYPOTHETICAL. `strict` is the one chat-form field with no Converse
# target, and the direction of the loss is the surprising one: Converse would
# go on constraining a decode the caller had just asked NOT to constrain.
with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
    _saved_builder = bac.build_response_format
    try:
        bac.build_response_format = lambda: {
            "type": "json_schema",
            "json_schema": {"name": "x", "strict": False, "schema": {}}}
        check("a chat form with strict=False is REFUSED rather than silently "
              "translated into a constrained call",
              raises(bac.build_converse_request, "S", "U"),
              "BedrockConverseTranslationError")
        bac.build_response_format = lambda: {"type": "json_schema"}
        check("...and a chat form that lost its json_schema object is refused too",
              raises(bac.build_converse_request, "S", "U"),
              "BedrockConverseTranslationError")
    finally:
        bac.build_response_format = _saved_builder
check("the builder was restored by identity",
      bac.build_response_format is _saved_builder, True)


# ===========================================================================
# SECTION 4 — THE RESPONSE TRANSLATION
# ===========================================================================

section("4. The Converse reply becomes the ChatCompletion Stage 5 consumes")

with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC), counters_zeroed():
    _c = drive(bac.translate_response, CONVERSE_REPLY)

check("translate_response returns a real ChatCompletion",
      type(_c).__name__, "ChatCompletion")
check("the id is the botocore request id, which is traceable",
      getattr(_c, "id", None), "req-0123456789")
check("content is the text block", getattr(_c, "choices", [None])[0].message.content
      if not isinstance(_c, Raised) else _c, RESPONSE_JSON)
check("...so the reasoningContent block was SKIPPED",
      "SHOULD NOT APPEAR" in (_c.choices[0].message.content or "")
      if not isinstance(_c, Raised) else True, False)
check("refusal is None on an ordinary completion",
      _c.choices[0].message.refusal if not isinstance(_c, Raised) else "?", None)
check("finish_reason for end_turn is stop",
      _c.choices[0].finish_reason if not isinstance(_c, Raised) else "?", "stop")

# --- THE USAGE ARITHMETIC, WHICH IS NOT A RENAME ---------------------------
#
# 1080 non-cached + 17920 cache read + 0 cache write = 19000. A rename would
# report 1080 and under-price this call by 94% of its input.
_u = _c.usage if not isinstance(_c, Raised) else None
check("prompt_tokens SUMS the three disjoint Converse counts, per AWS's own "
      "formula -- a rename here under-reports money on every cache hit",
      getattr(_u, "prompt_tokens", None), 19000)
check("completion_tokens is outputTokens", getattr(_u, "completion_tokens", None), 2400)
check("total_tokens is DERIVED from the two above, so the three agree",
      getattr(_u, "total_tokens", None), 21400)
check("cached_tokens is cacheReadInputTokens",
      getattr(getattr(_u, "prompt_tokens_details", None), "cached_tokens", None),
      17920)
check("the cache WRITE count is carried rather than discarded at the boundary",
      at(getattr(_u, "prompt_tokens_details", None).model_dump()
         if _u is not None and _u.prompt_tokens_details is not None else {},
         "cache_write_tokens"), 0)
check("completion_tokens_details is ABSENT -- Converse reports no reasoning "
      "count, and a zero would tell the column the model reasoned not at all",
      getattr(_u, "completion_tokens_details", "<missing>"), None)

# --- NULL versus zero, which is the whole point of the cached column -------
check("no cache fields at all -> NO prompt_tokens_details (the response did "
      "not report), rather than one full of zeros",
      "prompt_tokens_details" in bac._usage_block(
          {"inputTokens": 10, "outputTokens": 5}), False)
check("...while an explicit 0 IS carried (the provider cached nothing), which "
      "is a different finding",
      at(bac._usage_block({"inputTokens": 10, "outputTokens": 5,
                           "cacheReadInputTokens": 0}),
         "prompt_tokens_details", "cached_tokens"), 0)
check("a response with no usage at all still produces addable integers, "
      "because Stage 5 adds them unconditionally",
      {k: v for k, v in bac._usage_block(None).items()},
      {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
with counters_zeroed() as _ctr:
    bac._usage_block(None)
    check("...and the absence is COUNTED rather than silent",
          _ctr.get(bac.DEGRADATION_NO_USAGE), 1)

# --- every documented stop reason -------------------------------------------
check("all nine documented stopReason values are declared",
      len(set(bac.STOP_REASONS_DOCUMENTED)), 9)
check("...and they are exactly the ones API_runtime_Converse.html lists",
      sorted(bac.STOP_REASONS_DOCUMENTED),
      sorted(["end_turn", "tool_use", "max_tokens", "stop_sequence",
              "guardrail_intervened", "content_filtered",
              "malformed_model_output", "malformed_tool_use",
              "model_context_window_exceeded"]))

_EXPECTED_FINISH = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "guardrail_intervened": "content_filter",
    "content_filtered": "content_filter",
    "malformed_model_output": "stop",
    "malformed_tool_use": "stop",
    "tool_use": "BedrockConverseTranslationError",
}
check("the expectation table covers EVERY documented value, so a value added "
      "to the vocabulary and not to the mapping fails here",
      sorted(_EXPECTED_FINISH), sorted(bac.STOP_REASONS_DOCUMENTED))

with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC), counters_zeroed() as _ctr:
    for _sr, _want in sorted(_EXPECTED_FINISH.items()):
        if _want.endswith("Error"):
            check(f"stopReason {_sr!r} RAISES -- Stage 5 sends no tools, so it "
                  f"means the request was not the one this adapter built",
                  raises(bac._finish_reason, _sr), _want)
        else:
            check(f"stopReason {_sr!r} -> finish_reason {_want!r}",
                  drive(bac._finish_reason, _sr), _want)
    check("...and the two malformed values were COUNTED",
          _ctr.get(bac.DEGRADATION_MALFORMED_OUTPUT), 2)
    check("an UNDOCUMENTED stopReason maps to stop and is counted, so a "
          "vocabulary that grows is visible rather than silent",
          drive(bac._finish_reason, "a_value_aws_added_later"), "stop")
    check("...counted", _ctr.get(bac.DEGRADATION_UNKNOWN_STOP_REASON), 1)
    check("an ABSENT stopReason is treated the same way",
          drive(bac._finish_reason, None), "stop")

check("'length' is the string Stage 5's reactive split is armed by, and it is "
      "what max_tokens maps to",
      _EXPECTED_FINISH["max_tokens"], _evaluation.FINISH_REASON_LENGTH)

# --- the model echo, both directions ---------------------------------------
with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC), counters_zeroed() as _ctr:
    _echoed, _was = bac._model_echo(
        {"additionalModelResponseFields": {"model": "anthropic.claude-sonnet-4-6"}})
    check("an echo under the bare key is used", _echoed,
          "anthropic.claude-sonnet-4-6")
    check("...and reported as attested", _was, True)
    _echoed2, _was2 = bac._model_echo(
        {"additionalModelResponseFields": {"/model": "us.anthropic.claude-sonnet-4-6"}})
    check("an echo under the POINTER spelling is used too -- AWS does not "
          "document which key a one-segment pointer comes back under",
          _echoed2, "us.anthropic.claude-sonnet-4-6")
    check("...and reported as attested", _was2, True)
    _echoed3, _was3 = bac._model_echo({})
    check("with no echo the REQUESTED id is used", _echoed3,
          config.BEDROCK_ANTHROPIC_MATCHING_MODEL)
    check("...and it is reported as NOT attested, which is what the caller "
          "records", _was3, False)
    check("...and reading the echo does not itself count anything -- the "
          "counter belongs to the caller that decides",
          dict(_ctr), {})

# THE SUBSTITUTION IS COUNTED WHEN translate_response USES IT. The
# once-per-process guard is cleared first, because a section that asserts "this
# bumped" cannot be at the mercy of whether an earlier section already tripped
# the guard.
with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC), counters_zeroed() as _ctr:
    bac._MODEL_ECHO_WARNED = False
    drive(bac.translate_response, CONVERSE_REPLY)
    check("translate_response COUNTS a missing echo, so 'matching_model records "
          "what was requested' is on the run-end report rather than only in a "
          "docstring", _ctr.get(bac.DEGRADATION_NO_MODEL_ECHO), 1)
    drive(bac.translate_response, CONVERSE_REPLY)
    check("...once per process, not once per call -- a per-call bump on a "
          "22,000-patient run makes the report's CLEAN line worthless",
          _ctr.get(bac.DEGRADATION_NO_MODEL_ECHO), 1)
    bac._MODEL_ECHO_WARNED = False

with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC), counters_zeroed() as _ctr:
    _with_echo = dict(CONVERSE_REPLY)
    _with_echo["additionalModelResponseFields"] = {"model": "SOMETHING-ELSE"}
    _ce = drive(bac.translate_response, _with_echo)
    check("an echo that ARRIVES is passed through unchanged, so "
          "MatchingModelMismatchError has something real to compare",
          getattr(_ce, "model", None), "SOMETHING-ELSE")
    check("...and nothing is counted when the attestation was available",
          _ctr.get(bac.DEGRADATION_NO_MODEL_ECHO), None)

# --- THE REFUSAL ROUTE, WHICH CONVERSE EXPRESSES THROUGH stopReason --------
#
# WITHOUT THIS MAPPING THE BRANCH REINTRODUCES A DEFECT STAGE 5 ALREADY
# REMOVED. `evaluation.REFUSAL_ERROR_PREFIX`'s own block records it: a refusal
# read as a parse failure is RETRIED up to MAX_LLM_CLASSIFIER_RETRIES times
# "at full price, against a model that had already declined". Converse has no
# refusal content block, so a naive translation leaves message.refusal
# permanently None and every guardrail block buys three billed calls.
check("Stage 5 still has a refusal route to reach",
      hasattr(_evaluation, "REFUSAL_ERROR_PREFIX"), True)
check("...and it reads message.refusal, which is the field this branch has to "
      "populate", "refusal" in ast.unparse(next(
          n for n in ast.walk(ast.parse(_EVAL_SRC))
          if isinstance(n, ast.FunctionDef) and n.name == "_refusal_text")), True)

for _sr in ("guardrail_intervened", "content_filtered"):
    with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC), counters_zeroed():
        _blocked = dict(CONVERSE_REPLY)
        _blocked["stopReason"] = _sr
        _blocked["output"] = {"message": {"role": "assistant", "content": []}}
        _rc = drive(bac.translate_response, _blocked)
    _msg = _rc.choices[0].message if not isinstance(_rc, Raised) else None
    check(f"stopReason {_sr!r} sets a refusal, so Stage 5 TERMINATES rather "
          f"than re-sending a request a deterministic block will refuse again",
          bool(getattr(_msg, "refusal", None)), True)
    check(f"...naming the raw stopReason, so inferences.error gets back to the "
          f"API fact", _sr in (getattr(_msg, "refusal", "") or ""), True)
    check(f"...and Stage 5's own extractor reads it as a refusal",
          bool(_evaluation._refusal_text(_msg)), True)
    check(f"...while finish_reason stays content_filter, which is accurate",
          _rc.choices[0].finish_reason if not isinstance(_rc, Raised) else "?",
          "content_filter")

check("the refusal vocabulary is exactly the two decline reasons -- a "
      "malformed output is the model failing, not the system declining",
      sorted(bac.REFUSAL_TEXT), sorted(bac._STOP_REASON_REFUSALS))
check("...and both are documented stopReason values",
      [s for s in bac._STOP_REASON_REFUSALS
       if s not in bac.STOP_REASONS_DOCUMENTED], [])
for _sr in ("end_turn", "max_tokens", "malformed_model_output", "stop_sequence"):
    with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC), counters_zeroed():
        _other = dict(CONVERSE_REPLY)
        _other["stopReason"] = _sr
        _oc = drive(bac.translate_response, _other)
    check(f"stopReason {_sr!r} does NOT set a refusal -- an absent field is "
          f"what Stage 5 reads as 'not refused'",
          _oc.choices[0].message.refusal if not isinstance(_oc, Raised) else "?",
          None)

# --- an empty message is counted -------------------------------------------
with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC), counters_zeroed() as _ctr:
    _empty = {"stopReason": "end_turn",
              "output": {"message": {"role": "assistant", "content": []}},
              "usage": {"inputTokens": 1, "outputTokens": 1}}
    _ec = drive(bac.translate_response, _empty)
    check("a reply carrying no text block still translates rather than "
          "raising -- Stage 5's parse is where an empty answer is handled",
          getattr(_ec, "choices", [None])[0].message.content
          if not isinstance(_ec, Raised) else _ec, "")
    check("...and the absence is COUNTED",
          _ctr.get(bac.DEGRADATION_NO_MESSAGE), 1)

# AN EMPTY TEXT BLOCK IS NO MESSAGE. Testing the block LIST rather than the
# joined result made these two shapes -- identical to every consumer -- differ
# in whether they reached the run-end report. Found by driving the four shapes
# rather than by reading.
for _label, _content in (("a text block carrying null", [{"text": None}]),
                         ("a text block carrying ''", [{"text": ""}]),
                         ("only a reasoning block",
                          [{"reasoningContent": {"reasoningText":
                                                 {"text": "x"}}}])):
    with counters_zeroed() as _ctr:
        _got = drive(bac._content_text,
                     {"message": {"role": "assistant", "content": _content}})
        check(f"{_label} -> empty content", _got, "")
        check(f"...and IS counted, exactly as no block at all is",
              _ctr.get(bac.DEGRADATION_NO_MESSAGE), 1)


# ===========================================================================
# SECTION 5 — THE ERROR TAXONOMY
# ===========================================================================

section("5. The error taxonomy names every documented Converse failure")


def _fake(name, **attrs):
    """An exception of a given CLASS NAME, since that is what the classifier
    reads. Fabricated rather than imported, because botocore synthesises its
    service exception classes at CLIENT CONSTRUCTION -- there is no module to
    import ThrottlingException from -- and because boto3 is not installed here."""
    cls = type(name, (Exception,), {})
    exc = cls("fabricated")
    for k, v in attrs.items():
        setattr(exc, k, v)
    return exc


_TAXONOMY = {
    "ThrottlingException": bac.ERROR_THROTTLED,
    "ModelNotReadyException": bac.ERROR_NOT_READY,
    "AccessDeniedException": bac.ERROR_FORBIDDEN,
    "ResourceNotFoundException": bac.ERROR_NOT_FOUND,
    "ValidationException": bac.ERROR_VALIDATION,
    "ModelErrorException": bac.ERROR_MODEL_ERROR,
    "ModelTimeoutException": bac.ERROR_TIMEOUT,
    "ServiceUnavailableException": bac.ERROR_SERVER,
    "InternalServerException": bac.ERROR_SERVER,
    "ParamValidationError": bac.ERROR_LOCAL_PARAMS,
    "NoCredentialsError": bac.ERROR_CREDENTIALS,
    "NoRegionError": bac.ERROR_CREDENTIALS,
    "EndpointConnectionError": bac.ERROR_CONNECTION,
    "ConnectTimeoutError": bac.ERROR_CONNECTION,
    "ReadTimeoutError": bac.ERROR_TIMEOUT,
}
for _name, _cat in sorted(_TAXONOMY.items()):
    check(f"{_name} -> {_cat}", bac.classify_error(_fake(_name)), _cat)

check("all nine modeled Converse exceptions are covered",
      sorted(n for n in _TAXONOMY
             if n.endswith("Exception")),
      sorted(["ThrottlingException", "ModelNotReadyException",
              "AccessDeniedException", "ResourceNotFoundException",
              "ValidationException", "ModelErrorException",
              "ModelTimeoutException", "ServiceUnavailableException",
              "InternalServerException"]))

check("a ClientError is classified by the CODE inside it, which is the one "
      "class botocore raises for every modeled service error",
      bac.classify_error(_fake("ClientError",
                               response={"Error": {"Code": "ThrottlingException"}})),
      bac.ERROR_THROTTLED)
check("...a ClientError with no usable code falls through to unclassified",
      bac.classify_error(_fake("ClientError", response={"Error": {}})),
      bac.ERROR_UNCLASSIFIED)
check("...and a `response` that is not a dict does not raise",
      bac.classify_error(_fake("ClientError", response="not a dict")),
      bac.ERROR_UNCLASSIFIED)

check("the adapter's own translation error is named",
      bac.classify_error(bac.BedrockConverseTranslationError("x")),
      bac.ERROR_TRANSLATION)

with counters_zeroed() as _ctr:
    check("an error nobody named is UNCLASSIFIED rather than mis-labelled",
          bac.classify_error(_fake("SomethingNew")), bac.ERROR_UNCLASSIFIED)
    check("...and is COUNTED, because an unnamed error class is a finding",
          _ctr.get(bac.DEGRADATION_UNKNOWN_ERROR), 1)

check("classify_error imports no AWS library to do any of that",
      "botocore" in sys.modules or "boto3" in sys.modules, False)


# ===========================================================================
# SECTION 6 — THE CLOSED VOCABULARIES
# ===========================================================================

section("6. Every vocabulary this module publishes is closed and complete")

check("the provider vocabulary has three members",
      config.MATCHING_PROVIDERS,
      (config.MATCHING_PROVIDER_OPENAI, config.MATCHING_PROVIDER_BEDROCK,
       config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC))
check("...with no duplicates, so a member added twice cannot hide one lost",
      len(set(config.MATCHING_PROVIDERS)), len(config.MATCHING_PROVIDERS))
check("...and every one of them is a value matching_wire_model() can answer "
      "for, which is what makes the vocabulary usable rather than merely "
      "closed",
      sorted(p for p in config.MATCHING_PROVIDERS
             if isinstance(drive(
                 lambda name: [config.__setattr__("MATCHING_PROVIDER", name),
                               config.matching_wire_model()][1], p), Raised)),
      [])
config.MATCHING_PROVIDER = _SHIPPED_AT_IMPORT["MATCHING_PROVIDER"]
check("None is not a legal provider, so a NULL column value is "
      "distinguishable from every legal one",
      None in config.MATCHING_PROVIDERS, False)

# DERIVED, NOT RETYPED: every DEGRADATION_* constant in the module must be in
# DEGRADATION_KEYS. A key added at a bump site and left out of the tuple is
# exactly the typo that produces a counter nobody reads.
_declared = {v for n, v in vars(bac).items()
             if n.startswith("DEGRADATION_") and isinstance(v, str)}
check("DEGRADATION_KEYS is complete -- every DEGRADATION_* constant is in it",
      sorted(_declared - set(bac.DEGRADATION_KEYS)), [])
check("...and carries nothing that is not one of them",
      sorted(set(bac.DEGRADATION_KEYS) - _declared), [])
check("...with no duplicates", len(set(bac.DEGRADATION_KEYS)),
      len(bac.DEGRADATION_KEYS))

_err_declared = {v for n, v in vars(bac).items()
                 if n.startswith("ERROR_") and isinstance(v, str)}
check("ERROR_CATEGORIES is complete", sorted(_err_declared - set(bac.ERROR_CATEGORIES)), [])
check("...and closed", sorted(set(bac.ERROR_CATEGORIES) - _err_declared), [])
check("every category the name table can produce is in the vocabulary",
      sorted(set(bac._ERROR_NAME_TO_CATEGORY.values()) - set(bac.ERROR_CATEGORIES)),
      [])

check("the counter is registered on the run-end degradation report",
      "BEDROCK_ANTHROPIC_DEGRADATIONS" in
      [n for n, _, _ in _degradation._REGISTRY_SPEC], True)
check("...bound to THIS module's counter object, not to a copy",
      next((c for n, c, _ in _degradation._REGISTRY_SPEC
            if n == "BEDROCK_ANTHROPIC_DEGRADATIONS"), None)
      is bac.BEDROCK_ANTHROPIC_DEGRADATIONS, True)
check("...and it is a SECOND counter beside the Responses branch's, so the "
      "report can say WHICH branch degraded",
      bac.BEDROCK_ANTHROPIC_DEGRADATIONS is
      sys.modules["oncotriage.agent.bedrock_adapter"].BEDROCK_ADAPTER_DEGRADATIONS,
      False)

check("the deps override key is closed-set registered",
      deps.BEDROCK_ANTHROPIC_CLIENT in deps.OVERRIDE_KEYS, True)
check("...and is a THIRD key rather than a reuse of BEDROCK_CLIENT, because "
      "the two hold objects of different types",
      deps.BEDROCK_ANTHROPIC_CLIENT == deps.BEDROCK_CLIENT, False)
check("an unknown override key still raises rather than being ignored",
      raises(deps.set_override, "bedrock_anthropic_clientt", object()),
      "KeyError")


# ===========================================================================
# SECTION 7 — THE VALIDATOR REFUSES WHAT CANNOT WORK
# ===========================================================================

section("7. Every knob that cannot work is refused locally, naming the constant")

with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
    check("the SHIPPED configuration is itself valid",
          drive(config.validate_matching_provider_config), None)

_BAD = [
    ("BEDROCK_ANTHROPIC_MATCHING_MODEL", "", "BEDROCK_ANTHROPIC_MATCHING_MODEL"),
    ("BEDROCK_ANTHROPIC_MATCHING_MODEL", "anthropic.claude-sonnet-4-6",
     "names no inference profile"),
    ("BEDROCK_ANTHROPIC_MATCHING_MODEL", "gpt.made-up", "names no inference profile"),
    ("BEDROCK_ANTHROPIC_CACHE_TTL", "30m", "BEDROCK_ANTHROPIC_CACHE_TTL"),
    ("BEDROCK_ANTHROPIC_THINKING", "enabled", "BEDROCK_ANTHROPIC_THINKING"),
    ("BEDROCK_ANTHROPIC_EFFORT", "none", "BEDROCK_ANTHROPIC_EFFORT"),
    ("BEDROCK_ANTHROPIC_SERVICE_TIER", "priority", "BEDROCK_ANTHROPIC_SERVICE_TIER"),
    ("BEDROCK_ANTHROPIC_SERVICE_TIER", "flex", "BEDROCK_ANTHROPIC_SERVICE_TIER"),
    ("BEDROCK_ANTHROPIC_SERVICE_TIER", "reserved", "ACCOUNT-level"),
]
for _knob, _value, _needle in _BAD:
    with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC, **{_knob: _value}):
        _msg = message_of(config.validate_matching_provider_config)
        check(f"{_knob}={_value!r} is refused", _msg != "<did not raise>", True)
        check(f"...naming {_needle!r}", _needle in _msg, True)

# THE BARE MODEL ID IS THE INTERESTING ONE: it is a legal Bedrock id and is
# unusable in every Region this project is likely to run in.
with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC,
              BEDROCK_ANTHROPIC_MATCHING_MODEL="anthropic.claude-sonnet-4-6"):
    _msg = message_of(config.validate_matching_provider_config)
check_true("the bare-id refusal explains that In-Region is unsupported in "
           "us-east-1 rather than only that a prefix is missing",
           "In-Region" in _msg and "us-east-1" in _msg)
check_true("...and names the one Region where the bare id IS correct",
           "eu-west-2" in _msg)

for _prefix in config.BEDROCK_ANTHROPIC_PROFILE_PREFIXES:
    with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC,
                  BEDROCK_ANTHROPIC_MATCHING_MODEL=
                  f"{_prefix}anthropic.claude-sonnet-4-6"):
        check(f"a {_prefix!r}-prefixed id is accepted",
              drive(config.validate_matching_provider_config), None)

# The Region check is SHARED and must still fire on this branch.
with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC, BEDROCK_REGION=""):
    _msg = message_of(config.validate_matching_provider_config)
check_true("an empty Region is refused on the Converse branch too",
           "BEDROCK_REGION" in _msg)
check_true("...and the message names where it would have landed for THIS "
           "branch (boto3), not the other branch's URL table",
           "boto3" in _msg)
with provider(config.MATCHING_PROVIDER_BEDROCK, BEDROCK_REGION=""):
    _msg_r = message_of(config.validate_matching_provider_config)
check_true("...while the Responses branch still names ITS landing site",
           "BEDROCK_BASE_URL_TEMPLATES" in _msg_r)
with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC, BEDROCK_REGION="us east 1"):
    check("a Region carrying whitespace is refused",
          raises(config.validate_matching_provider_config), "RuntimeError")

# Every priced wire id must actually be priced -- get_model_cost RAISES, and a
# branch whose model is unpriced cannot write an inferences row at all.
from oncotriage.utils import get_model_cost                     # noqa: E402
for _prefix in config.BEDROCK_ANTHROPIC_PROFILE_PREFIXES + ("",):
    _key = f"{_prefix}anthropic.claude-sonnet-4-6"
    _cost = drive(get_model_cost, _key, 1_000_000, 1_000_000)
    check(f"{_key} is priced in PRICING_CONFIG", isinstance(_cost, float), True)
# AND THE VALIDATOR REFUSES AN UNPRICED ONE BEFORE ANY CALL. This check exists
# because the loop above FOUND that hole: `au.` and `jp.` were accepted
# prefixes with no pricing row, so that configuration would have passed
# validation, spent a live Stage 5 call and only then raised from inside the
# writer. Two rows and one refusal closed it; this is what keeps it closed.
with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC,
              BEDROCK_ANTHROPIC_MATCHING_MODEL="us.anthropic.claude-not-priced"):
    _msg = message_of(config.validate_matching_provider_config)
check_true("a model with no PRICING_CONFIG row is refused BEFORE any billed "
           "call, rather than raising from inside the writer afterwards",
           "PRICING_CONFIG" in _msg)
check_true("...and the refusal says the geo rows are inferred", "A6" in _msg)
check("every accepted profile prefix yields a PRICED model id, which is the "
      "invariant the two must satisfy TOGETHER",
      sorted(p for p in config.BEDROCK_ANTHROPIC_PROFILE_PREFIXES + ("",)
             if f"{p}anthropic.claude-sonnet-4-6"
             not in config.PRICING_CONFIG["models"]), [])

check("the shipped default's price is the INFERRED geo rate, and the global "
      "row is the MEASURED one -- they differ, which is what says the "
      "distinction was actually made",
      get_model_cost("us.anthropic.claude-sonnet-4-6", 1_000_000, 0)
      == get_model_cost("global.anthropic.claude-sonnet-4-6", 1_000_000, 0),
      False)


# ===========================================================================
# SECTION 8 — PLANTED DEFECTS
# ===========================================================================

section("8. Every claim above is shown to FAIL when the code stops holding it")

# EACH PLANT IS A ONE-TOKEN EDIT TO AN IN-MEMORY COPY, never to the shipped
# file, and each PROBE returns a value the shipped module answers differently.
# A plant whose probe agrees with the shipped answer is reported as MISSED --
# which is a finding about the CHECK, not about the code.


def _p_usage_rename(t):
    """THE MONEY DEFECT: the disjoint sum reverted to a rename."""
    return sub(t, "prompt_tokens = non_cached + (cache_read or 0) + (cache_write or 0)",
               "prompt_tokens = non_cached", 1)


def _p_schema_object(t):
    """The schema sent as an OBJECT, which Converse's member is not."""
    return sub(t, '"schema": json.dumps(inner["schema"], sort_keys=False,\n'
                  '                             separators=(",", ":")),',
               '"schema": inner["schema"],', 1)


def _p_no_cache_point(t):
    """The cache breakpoint dropped -- the per-trial design's whole premise."""
    return sub(t, "    point = _cache_point()\n", "    point = None\n", 1)


def _p_truncation_to_stop(t):
    """max_tokens mapped to stop -- the reactive split never fires again."""
    return sub(t, '"max_tokens": FINISH_LENGTH,', '"max_tokens": FINISH_STOP,', 1)


def _p_silent_echo(t):
    """The missing model echo substituted WITHOUT being recorded."""
    return sub(t, "    if not echoed:\n        _warn_model_echo_once()\n",
               "    if not echoed:\n        pass\n", 1)


def _p_strict_ignored(t):
    """The strict guard removed -- an unconstrained request silently constrained."""
    return sub(t, '    if inner.get("strict") is not True:',
               '    if False:', 1)


def _p_zero_filled_cache(t):
    """An absent cache reading defaulted to zero -- NULL and 0 conflated."""
    return sub(t, "    if cache_read is not None or cache_write is not None:",
               "    if True:", 1)


def _p_tool_use_mapped(t):
    """tool_use folded into the table instead of raising."""
    return sub(t, '    "end_turn": FINISH_STOP,',
               '    "end_turn": FINISH_STOP,\n    "tool_use": FINISH_STOP,', 1)


def _p_drops_uncounted(t):
    """The dropped seed and effort no longer reach the degradation report."""
    return sub(t, "    BEDROCK_ANTHROPIC_DEGRADATIONS[DEGRADATION_SEED_DROPPED] += 1",
               "    pass", 1)


def _p_no_refusal(t):
    """The refusal left permanently None -- three billed retries per block."""
    return sub(t, "    refusal = REFUSAL_TEXT.get(stop_reason)",
               "    refusal = None", 1)


def _probe_refusal_set(mod):
    with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
        blocked = dict(CONVERSE_REPLY)
        blocked["stopReason"] = "guardrail_intervened"
        rc = drive(mod.translate_response, blocked)
    return bool(getattr(
        getattr(rc, "choices", [None])[0].message
        if not isinstance(rc, Raised) else None, "refusal", None))


def _p_echo_not_requested(t):
    """The echo pointer never asked for -- A3 could never be settled."""
    return sub(t, '        kwargs["additionalModelResponseFieldPaths"] = '
                  '[MODEL_ECHO_POINTER]',
               '        pass', 1)


def _probe_prompt_tokens(mod):
    return at(mod._usage_block(CONVERSE_REPLY["usage"]), "prompt_tokens")


def _probe_schema_is_str(mod):
    with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
        req = mod.build_converse_request("S", "U")
    return isinstance(
        at(req, "outputConfig", "textFormat", "structure", "jsonSchema",
           "schema"), str)


def _probe_has_cache_point(mod):
    with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
        req = mod.build_converse_request("S", "U")
    return any("cachePoint" in b for b in at(req, "system", default=[]))


def _probe_max_tokens(mod):
    with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
        return drive(mod._finish_reason, "max_tokens")


def _probe_echo_counted(mod):
    mod.BEDROCK_ANTHROPIC_DEGRADATIONS.clear()
    mod._MODEL_ECHO_WARNED = False
    with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
        drive(mod.translate_response, CONVERSE_REPLY)
    return mod.BEDROCK_ANTHROPIC_DEGRADATIONS.get(
        mod.DEGRADATION_NO_MODEL_ECHO)


def _probe_strict_refused(mod):
    saved = mod.build_response_format
    try:
        mod.build_response_format = lambda: {
            "type": "json_schema",
            "json_schema": {"name": "x", "strict": False, "schema": {}}}
        with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
            return raises(mod.build_converse_request, "S", "U")
    finally:
        mod.build_response_format = saved


def _probe_details_absent(mod):
    return "prompt_tokens_details" in mod._usage_block(
        {"inputTokens": 10, "outputTokens": 5})


def _probe_tool_use_raises(mod):
    with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
        return raises(mod._finish_reason, "tool_use")


def _probe_seed_counted(mod):
    mod.BEDROCK_ANTHROPIC_DEGRADATIONS.clear()
    mod._DROPPED_WARNED = False
    with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
        drive(mod._warn_dropped_parameters_once)
    return mod.BEDROCK_ANTHROPIC_DEGRADATIONS.get(mod.DEGRADATION_SEED_DROPPED)


def _probe_echo_requested(mod):
    with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
        req = mod.build_converse_request("S", "U")
    return "additionalModelResponseFieldPaths" in req


_plants = [
    ("the disjoint usage sum reverted to a rename (UNDER-REPORTS MONEY)",
     _p_usage_rename, _probe_prompt_tokens, 19000),
    ("the schema sent as an object rather than a string",
     _p_schema_object, _probe_schema_is_str, True),
    ("the cache breakpoint dropped",
     _p_no_cache_point, _probe_has_cache_point, True),
    ("max_tokens mapped to stop, disarming the truncation split",
     _p_truncation_to_stop, _probe_max_tokens, "length"),
    ("the missing model echo substituted SILENTLY",
     _p_silent_echo, _probe_echo_counted, 1),
    ("the strict guard removed -- an unconstrained chat form would be "
     "silently constrained",
     _p_strict_ignored, _probe_strict_refused,
     "BedrockConverseTranslationError"),
    ("an absent cache reading zero-filled, conflating NULL with 0",
     _p_zero_filled_cache, _probe_details_absent, False),
    ("tool_use folded into the table instead of raising",
     _p_tool_use_mapped, _probe_tool_use_raises,
     "BedrockConverseTranslationError"),
    ("the dropped seed no longer counted",
     _p_drops_uncounted, _probe_seed_counted, 1),
    ("the model echo never asked for",
     _p_echo_not_requested, _probe_echo_requested, True),
    ("a guardrail block no longer sets a refusal, so Stage 5 retries it twice "
     "more at full price", _p_no_refusal, _probe_refusal_set, True),
]
check("every plant carries a mutator and a probe -- a placeholder row would "
      "report itself as caught while testing nothing",
      [lbl for lbl, m, pr, _ in _plants if m is None or pr is None], [])

for _label, _mutate, _probe, _expected_clean in _plants:
    _planted = drive(exec_copy, _mutate)
    if isinstance(_planted, Raised):
        check(f"PLANT-FAILED: {_label}", _planted, "<a usable planted module>")
        continue
    _observed = drive(_probe, _planted)
    check(f"CAUGHT: {_label}", _observed != _expected_clean, True)

# THE CONTROL FOR THE CONTROLS. Every probe above must report the CLEAN answer
# against the shipped module -- otherwise a probe that always disagrees would
# report every plant as caught while measuring nothing.
for _label, _mutate, _probe, _expected_clean in _plants:
    check(f"...and the SHIPPED module gives the clean answer for: {_label}",
          drive(_probe, bac), _expected_clean)


# ===========================================================================
# SECTION 9 — NOTHING LEAKED
# ===========================================================================

section("9. Every knob is back where it started, and no file was written")

for _name, _value in sorted(_SHIPPED_AT_IMPORT.items()):
    check(f"{_name} is back to what it was at import",
          getattr(config, _name), _value)

check("the leak check covers every BEDROCK_ANTHROPIC_ knob config declares, "
      "derived rather than retyped",
      sorted(n for n in vars(config) if n.startswith("BEDROCK_ANTHROPIC_")),
      sorted(_KNOB_NAMES))
check_true("...and there is more than one of them, so the derivation is not "
           "vacuous", len(_KNOB_NAMES) >= 8)

check("no deps override was left installed",
      [k for k in deps.OVERRIDE_KEYS if deps.peek(k) is not deps.UNSET], [])

for _path, _sha in sorted(_SHA_BEFORE.items()):
    check(f"{os.path.basename(_path)} is byte-identical -- every plant went "
          f"into an in-memory copy",
          hashlib.sha256(open(_path, "rb").read()).hexdigest(), _sha)

check("boto3 was never imported by anything this file did",
      "boto3" in sys.modules, False)


# ===========================================================================

print(f"\n{'=' * 74}")
print("RESULTS:")
print(f"  passed: {_RESULTS['passed']}")
print(f"  failed: {_RESULTS['failed']}")
if _FAILURES:
    print("\nFAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")
print(f"{'=' * 74}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Aug 30 2026

@author: ramyalsaffar
"""
