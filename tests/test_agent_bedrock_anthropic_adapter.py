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
FOUR repository files it READS are ``oncotriage/agent/bedrock_anthropic_
adapter.py``, ``oncotriage/agent/evaluation.py``, ``oncotriage/config.py`` and
``pyproject.toml`` (section 7d, the dependency pins). The third IS rewritten in
place by ``tests/test_config_snapshot_date_rot.py`` and neither of the suite's
two writers touches the others, so all four are sha256-compared at the end and
an interleaved serial run is visible rather than silent.

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
import subprocess
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
# DERIVED FROM THE MODULE THIS PROCESS IMPORTED, never from this file's own
# location: the child in section 7b must import the same tree the parent did.
#
# OFF `config.__file__` AND NOT `oncotriage.__file__`. The bootstrap above binds
# the name `oncotriage` only on its SUCCESS path -- its ImportError branch
# inserts a directory into sys.path and never re-imports -- so on a checkout
# without `pip install -e .` that name is UNBOUND and reading it here would
# abort the whole file at module scope. `config` is bound on every path.
_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(config.__file__)))
_ADAPTER_PATH = os.path.abspath(bac.__file__)
_EVALUATION_PATH = os.path.abspath(_evaluation.__file__)
_CONFIG_PATH = os.path.abspath(config.__file__)
# THE FOURTH FILE, added with section 7d: the dependency pins. Hashed HERE
# rather than where it is read, so the window it covers is the whole run --
# a hash taken at the point of use cannot see a write that preceded it.
_PYPROJECT_PATH = os.path.join(_CODE_DIR, "pyproject.toml")
_SHA_BEFORE = {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
               for p in (_ADAPTER_PATH, _EVALUATION_PATH, _CONFIG_PATH,
                         _PYPROJECT_PATH)}


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
# SECTION 1 — THE FLAG IS ON, AND THE ARM IT DISPLACED IS STILL EXACT
# ===========================================================================
#
# THIS SECTION USED TO BE "THE FLAG IS OFF AND NOTHING CHANGED" and its two
# opening checks pinned `config.MATCHING_PROVIDER == "openai"`. The provider
# flip made both false, and re-scoping them rather than deleting them is the
# point: what they were really worth was the DORMANT-ARM property -- "the arm
# this file is not about is unreachable and unchanged" -- and that property is
# still worth stating, now about the other arm. So the default assertion is
# inverted (this file's subject is the LIVE arm) and every check below that
# measures the OpenAI request PINS the provider explicitly, which is stronger
# than what it replaced: those checks used to hold only because the default
# happened to agree with them.

section("1. Flag ON: Converse is the shipped arm, and the OpenAI request it "
        "displaced is still byte-exact when pinned")

check("the shipped provider is the Converse branch -- which is what makes "
      "this file's subject the LIVE arm rather than a dormant one",
      config.MATCHING_PROVIDER, config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC)
check("...spelt exactly, which is what the whole file's claim rests on",
      config.MATCHING_PROVIDER, "bedrock_anthropic")
check("...and the OpenAI provider is still a member of the vocabulary, so the "
      "pins below name something real (non-degeneracy)",
      config.MATCHING_PROVIDER_OPENAI in config.MATCHING_PROVIDERS, True)

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

# PINNED EXPLICITLY. Before the provider flip this block ran on the shipped
# default and the OpenAI client answered because that WAS the default; the
# assertion was therefore about two things at once and could not distinguish
# "the OpenAI request shape is unchanged" from "somebody moved the default".
# Pinned, it measures the shape alone -- and it is the only thing in this suite
# that still measures it, since the arm is dormant.
with provider(config.MATCHING_PROVIDER_OPENAI), \
        overrides(openai_client=_openai, bedrock_client=_responses,
                  bedrock_anthropic_client=_converse):
    _got = drive(_evaluation.call_matching_model, "SYS", "USR")

check("with the provider pinned to OpenAI the OpenAI client answered", _got,
      "OPENAI-REPLY")
check("...the OpenAI client was called exactly once",
      len(_openai.recorder.calls), 1)
check("...the RESPONSES Bedrock client was NOT touched",
      len(_responses.recorder.calls), 0)
check("...and the CONVERSE Bedrock client was NOT touched -- which is the "
      "dormant-arm property this section keeps, pointed the other way",
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
# boto3 out of sys.modules.
#
# IT MATTERS MORE SINCE THE PROVIDER FLIP, NOT LESS, and the note that used to
# sit here ("on this machine boto3 is not installed at all") is no longer true
# -- boto3 IS installed (1.40.14 when this was written; pyproject pins 1.42.42
# since the Converse SDK floor), and `config.get_bedrock_anthropic_client()`'s
# flag guard is now SATISFIED by the shipped default. So an unpinned drive
# anywhere above this line builds a real botocore client, imports boto3, and
# sends botocore off down its credential chain. MEASURED at the moment of the
# flip: the drives in 1b and 1d were unpinned, this check went red, and the
# probe it reported was an outbound request to the instance metadata service.
check("boto3 is not in sys.modules after importing and driving the package",
      "boto3" in sys.modules, False)
check("...nor botocore", "botocore" in sys.modules, False)

# --- 1d. The Converse client factory refuses on any OTHER provider ---------
#
# PINNED, AND THE PIN IS LOAD-BEARING RATHER THAN TIDY. This drove the factory
# on the shipped default, which WAS "openai"; at the flipped default the guard
# is satisfied and the call BUILDS -- so the check reported an HTTPClientError
# out of botocore's credential chain instead of the RuntimeError it asserts,
# having made a real outbound attempt to do it. That is the whole failure this
# guard exists to prevent, produced by the check written to prove it does not
# happen.
with provider(config.MATCHING_PROVIDER_OPENAI):
    check("config.get_bedrock_anthropic_client() REFUSES while the provider "
          "is openai, so the guarantee is a property of the function rather "
          "than of the call graph",
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
    # THIS PIN WAS MOVED BY A LIVE MEASUREMENT AND THE OLD EXPECTATION IS
    # NAMED RATHER THAN DELETED. It required the echo back VERBATIM
    # ("anthropic.claude-sonnet-4-6"), which is what the function did until
    # 2026-09-01 -- and Stage 5 compares that string against
    # config.matching_wire_model() with `!=`, so a shorter-but-equivalent id
    # raised MatchingModelMismatchError on EVERY call, after being billed.
    # Measured against the live service: a request naming the inference profile
    # `us.anthropic.claude-sonnet-4-6` comes back with
    # additionalModelResponseFields == {"model": "claude-sonnet-4-6"}. So the
    # branch was unusable, and the check that would have caught it was pinning
    # the defect.
    _echoed, _was = bac._model_echo(
        {"additionalModelResponseFields": {"model": "anthropic.claude-sonnet-4-6"}})
    check("an echo that is a dot-boundary ALIAS of the requested id resolves "
          "TO the requested id, so Stage 5's equality passes and the stored "
          "identity stays the one that is billed and priced",
          _echoed, config.BEDROCK_ANTHROPIC_MATCHING_MODEL)
    check("...and reported as attested", _was, True)
    # THE SHAPE THE SERVICE ACTUALLY RETURNS, pinned as its own case so a
    # future change cannot satisfy the alias rule on a hypothetical spelling
    # while failing the observed one.
    _echoed_base, _was_base = bac._model_echo(
        {"additionalModelResponseFields": {"model": "claude-sonnet-4-6"}})
    check("the BASE id the live service echoes -- measured 2026-09-01 -- "
          "resolves to the requested profile id",
          _echoed_base, config.BEDROCK_ANTHROPIC_MATCHING_MODEL)
    check("...and is attested", _was_base, True)
    # THE GUARANTEE THAT MUST NOT BE LOST. A different model is returned
    # VERBATIM so Stage 5 raises. Without this the alias rule would be a way of
    # switching the mismatch check off.
    _echoed_bad, _was_bad = bac._model_echo(
        {"additionalModelResponseFields": {"model": "claude-haiku-4-5"}})
    check("an echo naming a DIFFERENT model is returned verbatim, so Stage 5's "
          "equality fails and MatchingModelMismatchError still fires",
          _echoed_bad, "claude-haiku-4-5")
    check("...and it is still reported as attested -- the service did answer; "
          "what it answered is the problem", _was_bad, True)
    # AND THE BOUNDARY IS THE DOT. A suffix at a hyphen is not a scope segment
    # and must not match, or the rule would accept any model whose name merely
    # ends the same way.
    check("a suffix that is not at a dot boundary is NOT an alias",
          bac._echo_is_alias_of("4-6",
                                config.BEDROCK_ANTHROPIC_MATCHING_MODEL), False)
    check("...while a whole dropped scope segment IS",
          bac._echo_is_alias_of("claude-sonnet-4-6",
                                config.BEDROCK_ANTHROPIC_MATCHING_MODEL), True)
    check("...and the rule is one-directional: a LONGER echo than the "
          "requested id has never been observed and is not accepted",
          bac._echo_is_alias_of("us.anthropic.claude-sonnet-4-6",
                                "claude-sonnet-4-6"), False)
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
# SECTION 7c — THE INSTALLED SDK IS PART OF THE CONFIGURATION
# ===========================================================================

section("7c. A botocore that cannot express the request is refused at "
        "validation")

# WHY THIS SECTION EXISTS. On 2026-09-03 `bedrock_probe.py` built the adapter's
# real request, called `converse`, and was refused by botocore's OWN validator:
# `Unknown parameter in input: "outputConfig"`. Nothing was signed and nothing
# was billed -- and that is the whole hazard rather than a consolation. A
# too-old SDK fails EVERY Stage 5 call at $0 of provider spend, so a campaign
# opens a `runs` row, fails every patient, and the operator's first hypothesis
# is Amazon Bedrock or their AWS account, neither of which the request reached.
#
# THE FLOOR HAS ONE OWNER AND IT IS `config`, NOT THIS FILE AND NOT THE PROBE.
# Everything below reads `config.MIN_BOTOCORE_FOR_CONVERSE_REQUEST` and
# `config.classify_botocore_version` rather than a literal, so a floor
# re-measurement moves these checks with it instead of leaving them asserting
# the old number.

check("the state vocabulary is closed and has four members",
      config.BOTOCORE_SDK_STATES,
      (config.BOTOCORE_SDK_OK, config.BOTOCORE_SDK_TOO_OLD,
       config.BOTOCORE_SDK_VERSION_UNREADABLE, config.BOTOCORE_SDK_ABSENT))
check("the refusing states are a PROPER, NON-EMPTY subset of it -- which is "
      "what makes the two non-refusing states a decision rather than an "
      "omission",
      (set(config.BOTOCORE_SDK_REFUSING_STATES) < set(config.BOTOCORE_SDK_STATES)
       and len(config.BOTOCORE_SDK_REFUSING_STATES) > 0), True)
check("absent does not refuse -- get_bedrock_anthropic_client() already "
      "raises on the ImportError, and refusing here would make the request "
      "builder untestable without boto3",
      config.BOTOCORE_SDK_ABSENT in config.BOTOCORE_SDK_REFUSING_STATES, False)
check("an unreadable version does not refuse either; it is reported",
      config.BOTOCORE_SDK_VERSION_UNREADABLE
      in config.BOTOCORE_SDK_REFUSING_STATES, False)

# --- 7c-i. The classifier, over literals, with every state reached ---------
#
# The natural control for a pure function is a different INPUT, so the table is
# the test. The BOUNDARY rows are DERIVED from the constant rather than typed,
# because a floor re-measurement must move them.
_FLOOR = config.MIN_BOTOCORE_FOR_CONVERSE_REQUEST
_AT_FLOOR = ".".join(str(n) for n in _FLOOR)
_BELOW_FLOOR = ".".join(str(n) for n in _FLOOR[:-1] + (_FLOOR[-1] - 1,))
_ABOVE_FLOOR = ".".join(str(n) for n in _FLOOR[:-1] + (_FLOOR[-1] + 1,))

_VERSIONS = [
    (_AT_FLOOR, config.BOTOCORE_SDK_OK),
    (_ABOVE_FLOOR, config.BOTOCORE_SDK_OK),
    (_BELOW_FLOOR, config.BOTOCORE_SDK_TOO_OLD),
    ("1.40.76", config.BOTOCORE_SDK_TOO_OLD),      # what shipped before this
    ("2.0.0", config.BOTOCORE_SDK_OK),
    (f"{_AT_FLOOR}.dev0", config.BOTOCORE_SDK_OK),  # a dev build OF the floor
    ("unknown", config.BOTOCORE_SDK_VERSION_UNREADABLE),
    ("", config.BOTOCORE_SDK_VERSION_UNREADABLE),
    ("v1.42.42", config.BOTOCORE_SDK_VERSION_UNREADABLE),   # a leading 'v'
    (None, config.BOTOCORE_SDK_ABSENT),
]
for _text, _want in _VERSIONS:
    check(f"classify_botocore_version({_text!r}) is {_want}",
          drive(config.classify_botocore_version, _text), _want)

check("the boundary rows are not the same string, so 'at the floor' and "
      "'below the floor' are two different measurements",
      len({_AT_FLOOR, _BELOW_FLOOR, _ABOVE_FLOOR}), 3)
check("every state in the vocabulary is REACHED by the table, so no member "
      "is asserted about without being driven",
      sorted({config.classify_botocore_version(t) for t, _ in _VERSIONS}),
      sorted(config.BOTOCORE_SDK_STATES))

# --- 7c-ii. THE BUG THE UNREADABLE STATE EXISTS TO PREVENT -----------------
#
# The natural way to write this check is `if ver and ver < floor: raise`, and
# an unreadable version then takes the `else` and is REPORTED AS COMPLIANT --
# a claim about a number nobody read. That defect shipped once in the probe's
# own preflight. The control is a reader's own implementation of the buggy
# form, shown to DISAGREE with the shipped classifier on exactly that input
# and to AGREE on every other row -- so the difference is attributable to the
# unreadable case and not to two unrelated functions.


def _bug9_reads_as_compliant(text):
    """The shape 6b shipped: a falsy release falls through to 'fine'."""
    release = config._botocore_release_tuple(text) if text is not None else None
    if release and release < config.MIN_BOTOCORE_FOR_CONVERSE_REQUEST:
        return config.BOTOCORE_SDK_TOO_OLD
    return config.BOTOCORE_SDK_OK          # <- 'unknown' lands HERE


check("the buggy form reads an unparseable version as COMPLIANT, which is the "
      "defect", _bug9_reads_as_compliant("unknown"), config.BOTOCORE_SDK_OK)
check("...and the shipped classifier does not",
      config.classify_botocore_version("unknown"),
      config.BOTOCORE_SDK_VERSION_UNREADABLE)
check("the two forms disagree ONLY on the states the shipped one added, so "
      "the difference is attributable",
      sorted({t for t, _ in _VERSIONS
              if _bug9_reads_as_compliant(t)
              != config.classify_botocore_version(t)},
             key=lambda s: (s is None, s)),
      sorted({t for t, w in _VERSIONS
              if w in (config.BOTOCORE_SDK_VERSION_UNREADABLE,
                       config.BOTOCORE_SDK_ABSENT)},
             key=lambda s: (s is None, s)))

# --- 7c-iii. The refusal, driven through the real validator ----------------
#
# The version is injected through config's own metadata cache rather than by
# installing an old botocore, so this costs nothing and cannot leave a broken
# environment behind. `sys.modules['botocore']` is popped for the block because
# an imported module WINS over the metadata by design -- see
# installed_botocore_version() -- and leaving it in would make the injection
# reach nothing.


@contextlib.contextmanager
def botocore_reporting(version):
    """Make config see `version` as the installed botocore, for one block."""
    saved_cache = config._BOTOCORE_DIST_VERSION
    saved_flag = config._BOTOCORE_SDK_REPORTED
    saved_mod = sys.modules.pop("botocore", None)
    config._BOTOCORE_DIST_VERSION = version
    config._BOTOCORE_SDK_REPORTED = False
    try:
        yield
    finally:
        config._BOTOCORE_DIST_VERSION = saved_cache
        config._BOTOCORE_SDK_REPORTED = saved_flag
        if saved_mod is not None:
            sys.modules["botocore"] = saved_mod


with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
    with botocore_reporting("1.40.76"):
        check("a botocore below the floor is REFUSED at validation",
              raises(config.validate_matching_provider_config), "RuntimeError")
        _sdk_msg = message_of(config.validate_matching_provider_config)

for _needle, _why in (
        ("1.40.76", "the INSTALLED version, so the operator can see what "
                    "they have"),
        (_AT_FLOOR, "the REQUIRED version"),
        # THE WHOLE RENDERED COMMAND, not the words in it. The first version
        # of this row asked for "pip install", "boto3" and "botocore"
        # separately -- and the revert that replaced the command outright with
        # "upgrade it." was MISSED, because the surrounding prose still
        # contains all three. A substring that appears in several places
        # cannot be evidence about one of them.
        (config.botocore_upgrade_command(),
         "the WHOLE upgrade command, verbatim -- naming BOTH distributions, "
         "because a boto3 pin below the floor CAPS botocore below it and "
         "upgrading botocore alone leaves pip reporting a broken environment"),
        ("MIN_BOTOCORE_FOR_CONVERSE_REQUEST", "the constant that owns the "
                                              "number"),
        ("outputConfig", "the field that is missing, so the refusal is "
                         "checkable against the service model"),
        ("pip install -e .", "the route that installs what pyproject declares"),
):
    check_true(f"the refusal names {_needle!r} -- {_why}", _needle in _sdk_msg)
check_true("...and it says the failure is LOCAL rather than a finding about "
           "Amazon Bedrock, which is the misdiagnosis it exists to prevent",
           "locally" in _sdk_msg)

# --- 7c-iv. It is Converse-only, and the other two branches are untouched --
for _other in (config.MATCHING_PROVIDER_OPENAI, config.MATCHING_PROVIDER_BEDROCK):
    with provider(_other):
        with botocore_reporting("1.40.76"):
            check(f"a too-old botocore does NOT refuse on {_other!r}, which "
                  f"reaches no Converse API",
                  drive(config.validate_matching_provider_config), None)

# --- 7c-v. The two non-refusing states are not silent ----------------------
with provider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
    for _version, _label in ((None, "absent"), ("unknown", "unreadable")):
        with botocore_reporting(_version):
            # NOT THROUGH `drive`. That helper installs its OWN redirect, so
            # an outer one captures nothing -- which reported this section as
            # silent when it was not, and would have made the "said once"
            # comparison a comparison of two empty strings.
            _buf = io.StringIO()
            _first = Raised(AssertionError("never ran"))
            with contextlib.redirect_stderr(_buf), contextlib.redirect_stdout(_buf):
                try:
                    _first = config.validate_matching_provider_config()
                except Exception as _exc:              # noqa: BLE001
                    _first = Raised(_exc)
                _said_once = _buf.getvalue()
                try:
                    config.validate_matching_provider_config()
                except Exception:                      # noqa: BLE001
                    pass
                _said_twice = _buf.getvalue()
            check(f"a {_label} botocore does not refuse", _first, None)
            check_true(f"...and the {_label} arm really did emit something, so "
                       f"the once-per-process comparison below is not two "
                       f"empty strings", len(_said_once) > 0)
            check_true(f"...but it is SAID, naming the floor -- silence and "
                       f"'verified' must not look identical",
                       _AT_FLOOR in _said_once and len(_said_once) > 0)
            check(f"...and said ONCE per process, because validation runs at "
                  f"the top of every Converse request",
                  _said_twice, _said_once)

# --- 7c-vi. The reader never imports botocore, which two other sections
#            assert about sys.modules and would report as red -------------
_saved_botocore = sys.modules.pop("botocore", None)
try:
    _v, _src = config.installed_botocore_version()
    check("installed_botocore_version() leaves botocore out of sys.modules -- "
          "sections 1c and 5 assert exactly that after driving the request "
          "builder, which calls the validator at its first statement",
          "botocore" in sys.modules, False)
    check("...and it still answers, from the distribution metadata",
          _src, config.BOTOCORE_VERSION_SOURCE_DISTRIBUTION)
    check("the source vocabulary is closed and the live source is a member of "
          "it -- the two sources can disagree, so which one answered is "
          "reported rather than folded away",
          (config.BOTOCORE_VERSION_SOURCES
           == (config.BOTOCORE_VERSION_SOURCE_MODULE,
               config.BOTOCORE_VERSION_SOURCE_DISTRIBUTION,
               config.BOTOCORE_VERSION_SOURCE_NONE)
           and _src in config.BOTOCORE_VERSION_SOURCES), True)
    check("...with a version that parses",
          config.classify_botocore_version(_v)
          in config.BOTOCORE_SDK_STATES, True)

    # AN IMPORTED botocore WINS, and the source says which answered. The two
    # can disagree -- a vendored copy with no dist-info, or dist-info for a
    # module something else shadows -- so the source is reported rather than
    # folded away.
    _fake = types.ModuleType("botocore")
    _fake.__version__ = "9.9.9"
    sys.modules["botocore"] = _fake
    check("an already-imported botocore answers instead of the metadata, "
          "because it is the thing that would actually run",
          config.installed_botocore_version(),
          ("9.9.9", config.BOTOCORE_VERSION_SOURCE_MODULE))

    # AN IMPORTABLE botocore THAT REPORTS NO VERSION IS NOT `absent`, AND THE
    # DIFFERENCE IS THE REMEDY. `absent` tells an operator the client build is
    # where this refuses -- which is FALSE of a botocore already in
    # sys.modules: that client WILL build, and every Converse call will then
    # fail on a version nobody could read. The first version of this reader
    # conflated the two.
    _versionless = types.ModuleType("botocore")            # no __version__
    sys.modules["botocore"] = _versionless
    _saved_dist = config._BOTOCORE_DIST_VERSION
    config._BOTOCORE_DIST_VERSION = None                   # and no metadata
    try:
        check("an importable botocore with no readable version is "
              "version_unreadable, NOT absent",
              config.botocore_sdk_state()[0],
              config.BOTOCORE_SDK_VERSION_UNREADABLE)
        check("...and it names the module as the source, which is why it is "
              "not absent", config.botocore_sdk_state()[2],
              config.BOTOCORE_VERSION_SOURCE_MODULE)
        del sys.modules["botocore"]
        check("...while the same state with NO module IS absent, so the two "
              "are distinguished by the module and not by the version",
              config.botocore_sdk_state()[0], config.BOTOCORE_SDK_ABSENT)
    finally:
        config._BOTOCORE_DIST_VERSION = _saved_dist
finally:
    sys.modules.pop("botocore", None)
    if _saved_botocore is not None:
        sys.modules["botocore"] = _saved_botocore
check("the fake was removed again", "botocore" in sys.modules, False)

# THE CACHE SENTINEL IS AN OBJECT, NOT A STRING, AND THIS IS WHAT SEES THE
# DIFFERENCE. A string sentinel is a value botocore could in principle report,
# and the cache would then read a REAL reported version as "not looked up",
# silently re-resolve, and answer about the environment instead of about what
# it was told. Driven with exactly that string: the cached value must be
# HONOURED (and therefore classified `version_unreadable`), not re-resolved.
with botocore_reporting("<not looked up>"):
    check("a version string that happens to equal the old string sentinel is "
          "still treated as a REPORTED version rather than as an empty cache",
          config.installed_botocore_version(),
          ("<not looked up>", config.BOTOCORE_VERSION_SOURCE_DISTRIBUTION))
    check("...so it classifies as unreadable rather than silently answering "
          "about the real environment",
          config.botocore_sdk_state()[0],
          config.BOTOCORE_SDK_VERSION_UNREADABLE)

# --- 7c-vii. THIS ENVIRONMENT can run the shipped provider -----------------
#
# NOT an assertion that the state is `ok`: this file's own docstring says it
# runs on a machine where boto3 is not installed, and `absent` is the right
# answer there. What it refuses is the one state that means Stage 5 cannot
# work at all.
_LIVE_STATE, _LIVE_VERSION, _LIVE_SOURCE = config.botocore_sdk_state()
print(f"  [environment] botocore {_LIVE_VERSION} ({_LIVE_SOURCE}) -> "
      f"{_LIVE_STATE}; floor {_AT_FLOOR}")
check(f"the installed botocore is not below the Converse floor "
      f"(if this fails: {config.botocore_upgrade_command()})",
      _LIVE_STATE == config.BOTOCORE_SDK_TOO_OLD, False)


# ===========================================================================
# SECTION 7d — THE PINS AND THE FLOOR CANNOT DRIFT APART
# ===========================================================================

section("7d. pyproject.toml declares a pair that satisfies the measured floor")

# WHY A TEST AND NOT A COMMENT. The floor lives in config and the pins live in
# pyproject.toml, and nothing connected them: the pin that shipped
# (`boto3==1.40.14`, botocore undeclared) not only carried no floor, it
# declared `botocore<1.41.0` and therefore FORBADE the one the Converse branch
# needs. A floor whose pin can drift below it is a floor that fires as a
# refusal on every run instead of at `pip install`.

_PYPROJECT = _PYPROJECT_PATH          # hashed at import, with the other three


def _declared_pin(name):
    """The exact `==` pin pyproject declares for `name`, or None.

    Returns None rather than raising on an unreadable file, so a broken
    pyproject.toml is a RECORDED failure in the checks below rather than a
    traceback at module level that takes the rest of this file with it.
    """
    import tomllib
    try:
        with open(_PYPROJECT, "rb") as fh:
            data = tomllib.load(fh)
    except Exception:                                          # noqa: BLE001
        return None
    prefix = f"{name}=="
    for spec in data["project"]["dependencies"]:
        if spec.replace(" ", "").startswith(prefix):
            return spec.replace(" ", "")[len(prefix):]
    return None


_PIN_BOTO3 = _declared_pin("boto3")
_PIN_BOTOCORE = _declared_pin("botocore")

check("pyproject declares an EXACT boto3 pin", _PIN_BOTO3 is not None, True)
check("pyproject declares an EXACT botocore pin -- boto3's own requirement is "
      "a RANGE, so a boto3 pin alone leaves the resolver free to pick anywhere "
      "inside it",
      _PIN_BOTOCORE is not None, True)

_REL_BOTO3 = config._botocore_release_tuple(_PIN_BOTO3)
_REL_BOTOCORE = config._botocore_release_tuple(_PIN_BOTOCORE)


def _at_or_above(release, floor):
    """Comparison that cannot raise. A MISSING pin is NOT at or above.

    A bare `release >= floor` raises TypeError when the pin is absent -- which
    is EXACTLY the revert this section exists to catch -- and a raise inside a
    `check(...)` argument list aborts the file with no summary. Measured: the
    first version of this section reported one traceback where it owed nine
    failures.
    """
    return isinstance(release, tuple) and release >= floor


def _same_minor(left, right):
    """Whether two parsed pins share major.minor. Never raises."""
    return (isinstance(left, tuple) and isinstance(right, tuple)
            and len(left) >= 2 and len(right) >= 2 and left[:2] == right[:2])


check("the declared botocore pin parses", _REL_BOTOCORE is not None, True)
check("the declared boto3 pin parses", _REL_BOTO3 is not None, True)
check(f"the declared botocore pin {_PIN_BOTOCORE} is AT OR ABOVE the measured "
      f"Converse floor {_AT_FLOOR} -- this is the check that keeps the pin and "
      f"config.MIN_BOTOCORE_FOR_CONVERSE_REQUEST from drifting apart",
      _at_or_above(_REL_BOTOCORE, config.MIN_BOTOCORE_FOR_CONVERSE_REQUEST),
      True)
check(f"the declared boto3 pin {_PIN_BOTO3} is at or above "
      f"config.MIN_BOTO3_FOR_CONVERSE_REQUEST",
      _at_or_above(_REL_BOTO3, config.MIN_BOTO3_FOR_CONVERSE_REQUEST), True)
check("the two pins share a major.minor -- boto3's requirement on botocore is "
      "capped at the next minor, so a pair from two different minors cannot "
      "resolve at all",
      _same_minor(_REL_BOTO3, _REL_BOTOCORE), True)

# --- 7d-i. VERIFIED AGAINST boto3's OWN METADATA, where it can be ----------
#
# The three checks above rest on a pattern observed in boto3's published
# metadata. This reads that metadata rather than trusting the pattern -- and it
# is the check that would have caught the shipped defect, because
# `boto3==1.40.14` declares `botocore<1.41.0` and 1.42.42 is outside it.
#
# It can only run when the INSTALLED boto3 IS the declared pin, because that is
# the only metadata on disk. When it cannot, it says so by name rather than
# passing quietly.


def _botocore_bounds_of_installed_boto3():
    """(lower, upper) release tuples boto3's metadata allows for botocore."""
    import importlib.metadata
    import re
    lower = upper = None
    for spec in importlib.metadata.requires("boto3") or []:
        if ";" in spec or not spec.replace(" ", "").startswith("botocore"):
            continue
        for op, value in re.findall(r"(>=|<=|<|>|==)\s*([0-9][0-9.]*)", spec):
            release = config._botocore_release_tuple(value)
            if op in (">=", ">", "=="):
                lower = release
            elif op in ("<", "<="):
                upper = release
    return lower, upper


try:
    import importlib.metadata as _md
    _INSTALLED_BOTO3 = _md.version("boto3")
except Exception:                                              # noqa: BLE001
    _INSTALLED_BOTO3 = None

if _INSTALLED_BOTO3 == _PIN_BOTO3:
    _lower, _upper = _botocore_bounds_of_installed_boto3()
    check("boto3's own metadata declares a botocore range at all, so the "
          "comparison below is not vacuous",
          _lower is not None and _upper is not None, True)
    check(f"the declared boto3 pin's metadata ADMITS the declared botocore "
          f"pin ({_lower} <= {_REL_BOTOCORE} < {_upper}) -- the shipped "
          f"boto3==1.40.14 declared botocore<1.41.0 and did NOT",
          (_at_or_above(_REL_BOTOCORE, _lower)
           and isinstance(_upper, tuple) and isinstance(_REL_BOTOCORE, tuple)
           and _REL_BOTOCORE < _upper), True)
else:
    print(f"  NOTE  boto3's metadata was not read: installed "
          f"{_INSTALLED_BOTO3!r} != declared {_PIN_BOTO3!r}, so the only "
          f"metadata on disk describes a different release. Run "
          f"`pip install -e .` to verify this check.")
    check("the pins are still checkable against the floors without it",
          _REL_BOTOCORE >= config.MIN_BOTOCORE_FOR_CONVERSE_REQUEST, True)

# --- 7d-ii. The floors are read by something other than this file ---------
check("config.MIN_BOTO3_FOR_CONVERSE_REQUEST is a 3-tuple of ints, so the "
      "upgrade command it renders is a real version",
      (isinstance(config.MIN_BOTO3_FOR_CONVERSE_REQUEST, tuple)
       and len(config.MIN_BOTO3_FOR_CONVERSE_REQUEST) == 3
       and all(isinstance(n, int)
               for n in config.MIN_BOTO3_FOR_CONVERSE_REQUEST)), True)
check("the upgrade command names BOTH distributions and both floors",
      (f"boto3=={config.boto3_floor_text()}" in config.botocore_upgrade_command()
       and f"botocore=={config.botocore_floor_text()}"
       in config.botocore_upgrade_command()), True)
check("MIN_BOTOCORE_MEASURED_ON is a date, printed in the refusal so the "
      "number is not an anonymous constant",
      bool(config.MIN_BOTOCORE_MEASURED_ON
           and config.MIN_BOTOCORE_MEASURED_ON in _sdk_msg), True)


# ===========================================================================
# SECTION 7b — THE CREDENTIAL GUARD, ALL SIXTEEN STATES
# ===========================================================================

section("7b. A credential boto3 must not use is refused before the client")

# WHY THIS SECTION IS DRIVEN AND NOT READ. `config._assert_bedrock_anthropic_
# credential_is_visible()` is the last thing between this process and
# `boto3.client(...)`, and the state it exists to refuse is invisible to every
# other layer: an empty `AWS_BEARER_TOKEN_BEDROCK` is not "no credential", it
# is an EMPTY BEARER TOKEN, and botocore selects bearer auth on the variable's
# PRESENCE -- `botocore/handlers.py` asks `get_token_from_environment(...) is
# not None`, verified against botocore 1.40.76 when this was written and
# RE-VERIFIED against the pinned 1.42.42 on 2026-09-03 (the predicate was
# renamed `_should_use_bearer_auth` -> `_should_prefer_bearer_auth`; the rule
# is the same, and an empty string is still `is not None`). So the SigV4
# chain is bypassed, an instance role that would have worked is never
# consulted, and the only symptom is a 401 that names nothing.
#
# THE SIXTEEN STATES ARE DRIVEN RATHER THAN SAMPLED because the two variables
# interact: one refusal is about AWS's variable alone and the other is about
# this project's variable in the ABSENCE of AWS's, and the interesting cells
# are the ones where both have an opinion.
#
# NO CLIENT IS BUILT HERE AND boto3 IS NOT IMPORTED. The guard is a standalone
# function that imports nothing, so it is called directly; the "the guard runs
# BEFORE boto3.client" claim is settled twice below -- structurally by an AST
# pin, and behaviourally in a SUBPROCESS, so this process's `boto3 is not in
# sys.modules` claim (section 1c, section 9) survives verbatim.

_CRED_VARS = (config.settings.ENV_BEDROCK_API_KEY,
              config.settings.ENV_AWS_BEARER_TOKEN_BEDROCK)
_CRED_SAVED = {_v: os.environ.get(_v) for _v in _CRED_VARS}

# Assembled rather than written out, so this file carries no literal a secret
# scanner has to be taught to ignore. `test_secret_scan_gate.py`'s own rule.
_FAKE_TOKEN = "_FAKE" + "-bedrock-token-" + str(len("placeholder"))

# (label, value) -- None means "not in os.environ at all".
_CRED_STATES = (("unset", None), ("empty", ""), ("blank", " \t\n "),
                ("real", _FAKE_TOKEN))

_REFUSE_BLANK = "blank-bearer"
_REFUSE_INVISIBLE = "project-key-invisible"
_ALLOWED = "allowed"


def _set_cred(name, value):
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def _guard_verdict():
    """One of the three outcomes above, or a Raised for anything unexpected."""
    out = drive(config._assert_bedrock_anthropic_credential_is_visible)
    if not isinstance(out, Raised):
        return _ALLOWED
    if out.kind != "RuntimeError":
        return out
    if "empty or whitespace-only value" in out.message:
        return _REFUSE_BLANK
    if "is set but" in out.message:
        return _REFUSE_INVISIBLE
    return out


# EXPECTED IS DERIVED FROM THE RULE, NOT TRANSCRIBED FROM A RUN. Transcribing
# the observed matrix would make whatever the code does correct by definition,
# which is this project's standing objection to a golden file refreshed to
# accommodate a change.
def _expected(onc_label, aws_label):
    if aws_label in ("empty", "blank"):
        return _REFUSE_BLANK                     # botocore would see a token
    if aws_label == "unset" and onc_label == "real":
        return _REFUSE_INVISIBLE                 # boto3 cannot read the other
    return _ALLOWED                              # boto3's own chain decides


try:
    _matrix = {}
    for _onc_label, _onc in _CRED_STATES:
        for _aws_label, _aws in _CRED_STATES:
            _set_cred(_CRED_VARS[0], _onc)
            _set_cred(_CRED_VARS[1], _aws)
            _matrix[(_onc_label, _aws_label)] = _guard_verdict()

    for _cell, _got in sorted(_matrix.items()):
        check(f"ONCOTRIAGE={_cell[0]:<5} AWS={_cell[1]:<5} -> "
              f"{_expected(*_cell)}", _got, _expected(*_cell))

    # NEITHER LINE MAY PUT A VERDICT IN A SET. `Raised` defines __eq__ and no
    # __hash__, so it is unhashable -- and `set(_matrix.values())` therefore
    # raises TypeError EXACTLY when a defect makes a cell answer with an
    # unexpected exception, which is when this file owes failures rather than a
    # traceback. MEASURED: revert R5 below aborted here before this was
    # written. The known outcomes are strings; anything else is repr'd.
    _known = [_v for _v in _matrix.values() if isinstance(_v, str)]
    _unknown = sorted(repr(_v) for _v in _matrix.values()
                      if not isinstance(_v, str))
    check_true("NON-DEGENERACY: the matrix exercises all three outcomes, so "
               "the expectations above are not one answer repeated",
               len(set(_known)) == 3)
    check("...and every cell resolved to a known outcome rather than an "
          "unexpected exception", _unknown, [])

    # --- 7b-ii. THE TWO STATES THAT USED TO ESCAPE ------------------------
    #
    # MEASURED AGAINST THE PRE-FIX GUARD, which tested
    # `not os.environ.get(AWS_BEARER_TOKEN_BEDROCK)` -- truthiness. A
    # WHITESPACE-ONLY value is TRUTHY, so it read as "set" there while
    # `settings.resolve_bedrock_api_key()` had already decided the same value
    # said nothing: two questions, two answers, one variable. Both cells are
    # pinned by name so a revert to either question fails here.
    check("a whitespace-only AWS token is REFUSED even when this project's "
          "variable carries a real one -- the pre-fix guard let this through",
          _matrix[("real", "blank")], _REFUSE_BLANK)
    check("...and an EMPTY one is refused for the same reason rather than "
          "being reported as 'AWS_BEARER_TOKEN_BEDROCK is not set', which is "
          "false of a variable that is set to ''",
          _matrix[("real", "empty")], _REFUSE_BLANK)

    # --- 7b-iii. THE MESSAGE CARRIES WHAT AN OPERATOR ACTS ON -------------
    _set_cred(_CRED_VARS[0], None)
    _set_cred(_CRED_VARS[1], "")
    _blank_msg = message_of(config._assert_bedrock_anthropic_credential_is_visible)
    for _needle, _why in (
            (config.settings.ENV_AWS_BEARER_TOKEN_BEDROCK, "names the variable"),
            ("is not None", "names botocore's PRESENCE test verbatim"),
            ("bypass the SigV4 chain", "names what is lost, not just what fails"),
            ("unset", "gives the first fix"),
            ("set it to a real Bedrock API key", "gives the second fix")):
        check_true(f"the blank-token refusal {_why}", _needle in _blank_msg)
    check("...and it does NOT name this project's variable when that variable "
          "is not set, so the remedy is not padded with an irrelevant one",
          config.settings.ENV_BEDROCK_API_KEY in _blank_msg, False)

    _set_cred(_CRED_VARS[0], _FAKE_TOKEN)
    _both_msg = message_of(config._assert_bedrock_anthropic_credential_is_visible)
    check_true("...and it DOES when that variable carries a real value, so the "
               "operator is not sent round the loop twice",
               config.settings.ENV_BEDROCK_API_KEY in _both_msg
               and "the same value" in _both_msg)

    # NEVER THE VALUE. The one thing every refusal in this area must not do.
    for _label, _msg in (("blank-only", _blank_msg), ("both-set", _both_msg)):
        check(f"the {_label} refusal prints no credential VALUE",
              _FAKE_TOKEN in _msg, False)

    # --- 7b-iv. AN EMPTY ONCOTRIAGE_BEDROCK_API_KEY IS NOT REFUSED --------
    #
    # ASSERTED AS A DECISION, not left as an accident. boto3 never reads that
    # name, so an empty value cannot change which credential is selected -- its
    # outcome is identical to the variable being absent, and refusing it would
    # stop a machine whose instance role works.
    for _blank in ("", "   "):
        _set_cred(_CRED_VARS[0], _blank)
        _set_cred(_CRED_VARS[1], None)
        check(f"an ONCOTRIAGE_BEDROCK_API_KEY of {_blank!r} is treated exactly "
              f"as absent, because boto3 does not read it",
              _guard_verdict(), _ALLOWED)
    check("...which is the same verdict the genuinely-absent state gets, so "
          "the claim is a comparison and not one reading",
          _matrix[("unset", "unset")], _ALLOWED)

    # --- 7b-v. ONE DERIVATION, THREE CLOSED STATES ------------------------
    check("the bearer-state vocabulary is closed and has exactly three members",
          sorted(config.BEDROCK_BEARER_STATES),
          sorted({config.BEDROCK_BEARER_ABSENT, config.BEDROCK_BEARER_BLANK,
                  config.BEDROCK_BEARER_SET}))
    check_true("...and its members are distinct, which is what the import-time "
               "guard beside it protects",
               len(set(config.BEDROCK_BEARER_STATES)) == 3)
    for _value, _state in ((None, config.BEDROCK_BEARER_ABSENT),
                           ("", config.BEDROCK_BEARER_BLANK),
                           (" \t\n ", config.BEDROCK_BEARER_BLANK),
                           (_FAKE_TOKEN, config.BEDROCK_BEARER_SET)):
        _set_cred(_CRED_VARS[1], _value)
        check(f"_bedrock_bearer_env_state() answers {_state!r} for "
              f"{'absent' if _value is None else repr(_value)}",
              config._bedrock_bearer_env_state(), _state)
    _seen_states = []
    for _v in (None, "", " ", "\t", _FAKE_TOKEN, " x "):
        _set_cred(_CRED_VARS[1], _v)
        _seen_states.append(config._bedrock_bearer_env_state())
    check("every answer it gives is a member of the vocabulary",
          sorted({_s for _s in _seen_states
                  if _s not in config.BEDROCK_BEARER_STATES}), [])
    check("...and it reaches all three, so the sweep is not one answer six "
          "times", sorted(set(_seen_states)),
          sorted(set(config.BEDROCK_BEARER_STATES)))
finally:
    for _v, _old in _CRED_SAVED.items():
        _set_cred(_v, _old)

check("both credential variables are back where this section found them",
      {_v: os.environ.get(_v) for _v in _CRED_VARS}, _CRED_SAVED)

# --- 7b-vi. BOTH REFUSALS READ ONE DERIVATION -------------------------------
#
# STRUCTURAL, because the behavioural matrix above cannot see WHICH question
# each refusal asked -- only that today they agree. The pre-fix defect was
# exactly two questions agreeing on three states out of four.
_GUARD_SRC = ast.parse(open(_CONFIG_PATH, encoding="utf-8").read())
_GUARD_FN = next((n for n in ast.walk(_GUARD_SRC)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_assert_bedrock_anthropic_credential_is_visible"),
                 None)
check_true("the guard was found in the shipped source, so the walk below is "
           "not vacuous", _GUARD_FN is not None)
_GUARD_CALLS = [n.func.id for n in ast.walk(_GUARD_FN)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)] \
    if _GUARD_FN else []
check("the guard asks for the bearer state exactly ONCE -- two calls is two "
      "readings of a variable another thread could have changed between them",
      _GUARD_CALLS.count("_bedrock_bearer_env_state"), 1)
# WALKED AS NAMES, NOT GREPPED. `ast.unparse` keeps the docstring, and this
# guard's docstring ARGUES about os.environ -- so a substring test would report
# the argument as the defect. This project has shipped that shape before.
_GUARD_ENVIRON_READS = [n for n in ast.walk(_GUARD_FN)
                        if isinstance(n, ast.Attribute) and n.attr == "environ"
                        and isinstance(n.value, ast.Name)] if _GUARD_FN else []
check("the guard reaches os.environ NOWHERE itself, so the truthiness test "
      "that disagreed with the resolver cannot come back",
      len(_GUARD_ENVIRON_READS), 0)
check_true("NON-DEGENERACY: the same walk DOES find the one read, in the "
           "derivation that owns it",
           any(isinstance(n, ast.Attribute) and n.attr == "environ"
               for f in ast.walk(_GUARD_SRC)
               if isinstance(f, ast.FunctionDef)
               and f.name == "_bedrock_bearer_env_state"
               for n in ast.walk(f)))

# --- 7b-vii. THE GUARD RUNS BEFORE boto3.client -----------------------------
_FACTORY = next((n for n in ast.walk(_GUARD_SRC)
                 if isinstance(n, ast.FunctionDef)
                 and n.name == "get_bedrock_anthropic_client"), None)
check_true("the factory was found, so the ordering pin below is not vacuous",
           _FACTORY is not None)
_guard_lines = [n.lineno for n in ast.walk(_FACTORY)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "_assert_bedrock_anthropic_credential_is_visible"] \
    if _FACTORY else []
_client_lines = [n.lineno for n in ast.walk(_FACTORY)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "client"
                 and isinstance(n.func.value, ast.Name)
                 and n.func.value.id == "boto3"] if _FACTORY else []
check("the factory calls the guard exactly once", len(_guard_lines), 1)
check("...and constructs exactly one boto3 client", len(_client_lines), 1)
check_true("...and the guard is ABOVE the construction, so no credential this "
           "project refuses can reach botocore",
           bool(_guard_lines) and bool(_client_lines)
           and _guard_lines[0] < _client_lines[0])

# --- 7b-viii. AND NOT ONE CLIENT IS BUILT, MEASURED IN A SUBPROCESS ---------
#
# THE AST PIN ABOVE PROVES AN ORDERING; THIS PROVES AN OUTCOME, and neither
# replaces the other. A pin cannot see a client built by something the factory
# calls, and a counter cannot see a second construction site added tomorrow.
#
# WHY A SUBPROCESS. `get_bedrock_anthropic_client()` imports boto3 BEFORE it
# reaches the guard -- deliberately, so a machine without boto3 is told to
# install it rather than told its credentials are wrong -- so driving the real
# factory in THIS process would put boto3 in `sys.modules` and break the claim
# section 1c and section 9 make verbatim. The child counts; the parent stays
# boto3-free.
#
# THE CHILD'S PROJECT ROOT IS A DIRECTORY THAT DOES NOT EXIST. Nothing on this
# path should resolve a path or read a .env, and if a future edit makes it do
# so the child reports an unexpected exception instead of quietly reading the
# operator's real credentials file. `paths` resolves lazily, so pointing the
# root at nothing costs nothing until something reads.

_CHILD = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
_net = []
import socket
def _boom(*a, **k):
    _net.append("connect"); raise AssertionError("network attempt")
socket.socket.connect = _boom
socket.socket.connect_ex = _boom
socket.create_connection = _boom
socket.getaddrinfo = _boom
built = []
guard = "armed"
try:
    import boto3
except Exception as exc:
    guard = "inert: boto3 not importable (%s)" % type(exc).__name__
else:
    def _counting(*a, **k):
        built.append(a[0] if a else k.get("service_name"))
        raise AssertionError("boto3.client was reached")
    boto3.client = _counting
from oncotriage import config
try:
    config.get_bedrock_anthropic_client()
    outcome = "<did not raise>"
    message = ""
except Exception as exc:
    outcome = type(exc).__name__
    message = str(exc)
print("__RESULT__" + json.dumps({
    "guard": guard, "outcome": outcome, "clients": len(built),
    "net": len(_net), "blank": "empty or whitespace-only value" in message,
    "reached_counter": "boto3.client was reached" in message}))
'''


def _child(aws_value):
    """Drive the REAL factory in a child; return its JSON report or a Raised."""
    env = dict(os.environ)
    env["PYTHONPATH"] = _CODE_DIR
    env["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
    env["ONCOTRIAGE_MAIN_PATH"] = os.path.join(
        _CODE_DIR, "__no_such_root_for_the_credential_guard__")
    env.pop(config.settings.ENV_BEDROCK_API_KEY, None)
    if aws_value is None:
        env.pop(config.settings.ENV_AWS_BEARER_TOKEN_BEDROCK, None)
    else:
        env[config.settings.ENV_AWS_BEARER_TOKEN_BEDROCK] = aws_value
    proc = subprocess.run([sys.executable, "-c", _CHILD, _CODE_DIR],
                          capture_output=True, text=True, env=env, timeout=180)
    for line in proc.stdout.splitlines():
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__"):])
    return {"guard": "<no result>", "outcome": proc.stderr.strip()[-300:],
            "clients": -1, "net": -1, "blank": False, "reached_counter": False}


_blank_child = _child("")
_real_child = _child(_FAKE_TOKEN)

check("the child's boto3 tripwire was ARMED rather than inert -- an inert "
      "counter counts nothing and passes",
      _blank_child.get("guard"), "armed")

check("a BLANK AWS token: the factory raises RuntimeError", 
      _blank_child.get("outcome"), "RuntimeError")
check("...it is the blank-token refusal and not some other RuntimeError",
      _blank_child.get("blank"), True)
check("...and ZERO boto3 clients were constructed, which is the whole claim",
      _blank_child.get("clients"), 0)
check("...and the child made no network attempt", _blank_child.get("net"), 0)

# NON-DEGENERACY. Without this the zero above is equally satisfied by a counter
# that was never installed, by a factory that returns early for an unrelated
# reason, and by a child that died before it got there.
check("NON-DEGENERACY: with a REAL-looking token the same child reaches "
      "boto3.client, so the counter is wired and the guard is what stopped "
      "the other one", _real_child.get("clients"), 1)
check("...and it got there through the counting stand-in rather than building "
      "anything", _real_child.get("reached_counter"), True)
check("...and still made no network attempt", _real_child.get("net"), 0)


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
