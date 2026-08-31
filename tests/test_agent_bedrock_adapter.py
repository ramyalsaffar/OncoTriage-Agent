######################################################################
# Stage 5 on Amazon Bedrock: the flag is off, and the adapter is right
######################################################################

"""Bedrock Adapter Test

``oncotriage/agent/bedrock_adapter.py`` translates the Stage 5 request onto
Amazon Bedrock's OpenAI-compatible Responses API, behind
``config.MATCHING_PROVIDER``. This file holds it to two claims that pull in
opposite directions:

  1. WITH THE FLAG OFF NOTHING CHANGED. Not "equivalent" -- the same client
     object, the same seven kwargs, the same values, and no Bedrock client
     built, cached or reached. Section 1 asserts that structurally (an AST pin
     on the shipped ``call_matching_model``) AND behaviourally (a recording
     stub installed through ``deps``, driven through the real function, with
     the Bedrock stand-in required to have been untouched). The twelve
     characterization fixtures replaying clean without recapture is the third
     leg of that claim and lives outside this file.

  2. WITH THE FLAG ON EVERY FIELD MAPS, BOTH DIRECTIONS. Section 2 pins the
     request field by field against the values config actually holds; section 3
     pins the response translation, including the four ``finish_reason``
     outcomes and the NULL-versus-zero distinction in usage that
     ``inferences.llm_classifier_cached_input_tokens`` exists for.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO CORPUS, NO GIT HISTORY. Every
client is a stand-in installed through ``oncotriage/agent/deps.py``; every
response is a literal dict; the only database is a temp file. ``ONCOTRIAGE_
DEFER_LOCAL_MODELS`` is set ABOVE the package imports (pass 20c-3d's ordering
lesson: ``deps`` reads it once, at its own import, which arrives transitively
on the first ``oncotriage`` import).

IT DOES EXEC -- in-memory copies of ``oncotriage/agent/bedrock_adapter.py``,
one per plant, argued at ``_EXEC_ALLOWLIST`` in
``tests/test_package_invariants.py``. A ``git show`` control is impossible for
every one of them: the module has no prior revision, and each plant is a
one-token edit INSIDE a function body to code that exists at HEAD and nowhere
else. The copies are exec'd into a real ``ModuleType`` because a function's
globals ARE the dict it was exec'd into. The shipped file is sha256'd before
the first plant and compared at the end.

NOT in ``tests/run_serial_tests.py``'s collision matrix, derived rather than
assumed: it writes only inside a ``tempfile.mkdtemp`` it removes, and the two
repository files it READS -- ``oncotriage/agent/bedrock_adapter.py`` and
``oncotriage/agent/evaluation.py`` -- are written by neither
``tests/test_registries_cancer_code_claims_audit_control.py`` (which writes
``oncotriage/registries/cancer_code_registry.py``) nor
``tests/test_config_snapshot_date_rot.py`` (which writes
``oncotriage/config.py``).

EVERY CONFIG MUTATION IS INSIDE try/finally AND THE RESTORE IS ASSERTED. This
file flips ``config.MATCHING_PROVIDER`` and seven Bedrock knobs; a leaked flip
would make every later section describe a configuration nobody shipped, and
section 9 re-reads all eight at the end.

NOTHING CALLS INTO PRODUCTION CODE BARE. Every driver returns a marker on a
raise instead of letting it escape through ``check()``'s argument list -- the
abort shape this project has shipped nine times -- so a plant that makes a
function raise produces recorded FAILURES and a summary, not one traceback
where the run owed forty results.

    python tests/test_agent_bedrock_adapter.py
"""

import ast
import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
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

import openai

from oncotriage import config
from oncotriage import degradation as _degradation
from oncotriage import run_fingerprint
from oncotriage import settings
from oncotriage.agent import bedrock_adapter as ba
from oncotriage.agent import deps
from oncotriage.agent import evaluation as _evaluation
from oncotriage.agent.response_schema import (
    RESPONSE_SCHEMA_NAME, build_response_format)
from oncotriage.storage import database_logger as _dl


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
    exactly the plants this file exists to catch. Comparing unequal to
    everything makes the same event a recorded FAILURE naming the exception.
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
    """Call fn; return its value, or a Raised marker."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:                                   # noqa: BLE001
        return Raised(exc)


def raises(fn, *args, **kwargs):
    """The exception TYPE NAME fn raised, or the string '<did not raise>'."""
    try:
        fn(*args, **kwargs)
    except Exception as exc:                                   # noqa: BLE001
        return type(exc).__name__
    return "<did not raise>"


def message_of(fn, *args, **kwargs):
    """The exception MESSAGE fn raised, or '<did not raise>'."""
    try:
        fn(*args, **kwargs)
    except Exception as exc:                                   # noqa: BLE001
        return str(exc)
    return "<did not raise>"


def at(mapping, key, default="<absent>"):
    """``mapping[key]`` that cannot raise. A missing key is a FAILURE, not an
    abort -- and a missing key is precisely what several plants produce."""
    try:
        return mapping[key]
    except Exception:                                          # noqa: BLE001
        return default


def silence(fn, *args, **kwargs):
    """Run fn with both output channels captured. Nothing suppressed is
    asserted on: every assertion reads the returned value or the database."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        return drive(fn, *args, **kwargs)


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


def exec_copy(mutate):
    """Exec a MUTATED in-memory copy of bedrock_adapter into a real module."""
    _PLANT_SEQ[0] += 1
    text = open(_ADAPTER_PATH, encoding="utf-8").read()
    planted = mutate(text)
    if planted == text:
        raise AssertionError("the plant matched nothing")
    name = f"_planted_bedrock_adapter_{_PLANT_SEQ[0]}"
    module = types.ModuleType(name)
    module.__file__ = _ADAPTER_PATH
    module.__package__ = "oncotriage.agent"
    sys.modules[name] = module
    exec(compile(planted, _ADAPTER_PATH, "exec"), module.__dict__)
    return module


_ADAPTER_PATH = os.path.abspath(ba.__file__)
_EVALUATION_PATH = os.path.abspath(_evaluation.__file__)
_ADAPTER_SHA_BEFORE = hashlib.sha256(
    open(_ADAPTER_PATH, "rb").read()).hexdigest()
_EVALUATION_SHA_BEFORE = hashlib.sha256(
    open(_EVALUATION_PATH, "rb").read()).hexdigest()

_TMP = tempfile.mkdtemp(prefix="oncotriage-bedrock-")

# THE SHIPPED VALUES, CAPTURED AT IMPORT, BEFORE ANY `provider()` BLOCK.
#
# Section 9's job is "nothing leaked" -- every knob is back to what it was when
# this file started. It used to ask that against LITERALS, which was fine while
# every one of these was a literal in config.py and became a LANDMINE the day
# BEDROCK_REGION stopped being one: it is resolved from
# ONCOTRIAGE_BEDROCK_REGION with a config default, so an operator who has
# exported that variable would have made this file fail on a check about
# LEAKAGE, naming a constant rather than a defect. That is the shape
# tests/test_fixture_call_mode_pin.py records about its own check 1a -- a test
# that fails on the change it exists to protect is a landmine, not a tripwire.
#
# THE LITERAL CLAIM DID NOT DISAPPEAR, IT MOVED TO THE THING THAT IS STILL A
# LITERAL: section 9 additionally pins BEDROCK_REGION_DEFAULT, which no
# environment variable can move, so "the shipped default is us-east-1" is still
# asserted and is now asserted about the constant that actually holds it.
_SHIPPED_AT_IMPORT = {
    name: getattr(config, name)
    for name in ("MATCHING_PROVIDER", "BEDROCK_ENDPOINT", "BEDROCK_REGION",
                 "BEDROCK_MATCHING_MODEL", "BEDROCK_SYSTEM_ROLE",
                 "BEDROCK_SERVICE_TIER", "BEDROCK_STORE",
                 "BEDROCK_PROMPT_CACHE_KEY", "BEDROCK_PROMPT_CACHE_MODE",
                 "BEDROCK_SEND_SEED_IN_EXTRA_BODY")
}


# ===========================================================================
# STAND-INS
# ===========================================================================

class _Recorder:
    """Records every kwargs dict it is handed. Returns a canned reply."""

    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class _OpenAIStub:
    """`client.chat.completions.create(...)` and nothing else."""

    def __init__(self, reply=None):
        self.recorder = _Recorder(reply)
        self.chat = types.SimpleNamespace(completions=self.recorder)


class _BedrockStub:
    """`client.responses.create(...)` and nothing else.

    IT DELIBERATELY HAS NO ``chat``. A stand-in that answered both surfaces
    could not tell the two dispatch branches apart, which is the one thing
    section 1 exists to measure.
    """

    def __init__(self, reply=None):
        self.recorder = _Recorder(reply)
        self.responses = self.recorder


RESPONSE_JSON = json.dumps({"evaluations": []})

BEDROCK_REPLY = {
    "id": "resp_probe",
    "created_at": 1755000000,
    "model": "us.openai.gpt-5.6-terra",
    "status": "completed",
    "output": [
        # A reasoning item FIRST, carrying an output_text part, so "reasoning
        # items are skipped" is a claim this fixture can actually falsify. A
        # reasoning item with no content part would make the check vacuous.
        {"type": "reasoning",
         "content": [{"type": "output_text", "text": "SHOULD NOT APPEAR"}]},
        {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": RESPONSE_JSON}]},
    ],
    "usage": {
        "input_tokens": 19000,
        "output_tokens": 2400,
        "total_tokens": 21400,
        "input_tokens_details": {"cached_tokens": 17920,
                                 "cache_write_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 0},
    },
}


@contextlib.contextmanager
def provider(name, **knobs):
    """Set MATCHING_PROVIDER (and any Bedrock knob) for one block, then restore.

    Restoration is unconditional and section 9 re-reads every one of them at
    the end -- a leaked flip would make every later section describe a
    configuration nobody shipped.
    """
    saved = {"MATCHING_PROVIDER": config.MATCHING_PROVIDER}
    for key in knobs:
        saved[key] = getattr(config, key)
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


# ===========================================================================
# SECTION 1 — THE FLAG IS OFF AND NOTHING CHANGED
# ===========================================================================

section("1. Flag OFF: the adapter is unreachable and the OpenAI request is "
        "unchanged")

_CFG_SRC = open(os.path.abspath(config.__file__), encoding="utf-8").read()
_CFG_TREE = ast.parse(_CFG_SRC)

_provider_assigns = [
    n for n in _CFG_TREE.body
    if isinstance(n, ast.Assign)
    and any(isinstance(t, ast.Name) and t.id == "MATCHING_PROVIDER"
            for t in n.targets)]

check("MATCHING_PROVIDER is assigned exactly once at config's module scope",
      len(_provider_assigns), 1)
check("...and the shipped value is the OpenAI provider",
      config.MATCHING_PROVIDER, config.MATCHING_PROVIDER_OPENAI)
check("...which is the default the whole file's claim rests on",
      config.MATCHING_PROVIDER, "openai")

# --- 1b. The shipped call, pinned by AST -----------------------------------
#
# THE BEHAVIOURAL CHECK BELOW CANNOT SEE A KWARG THAT WAS RENAMED IN BOTH THE
# CALL AND THE EXPECTATION, because it reads the expectation off config too.
# This one reads the SOURCE, so the seven keyword names and the expression each
# is built from are pinned to text.

_EVAL_TREE = ast.parse(open(_EVALUATION_PATH, encoding="utf-8").read())
_call_fn = next((n for n in ast.walk(_EVAL_TREE)
                 if isinstance(n, ast.FunctionDef)
                 and n.name == "call_matching_model"), None)
check_true("call_matching_model is present in evaluation.py", _call_fn is not None)

_chat_calls = [n for n in ast.walk(_call_fn or ast.Module(body=[], type_ignores=[]))
               if isinstance(n, ast.Call)
               and ast.unparse(n.func).endswith("chat.completions.create")]
check("exactly one chat.completions.create call in call_matching_model",
      len(_chat_calls), 1)

_PINNED_KWARGS = {
    "model": "MATCHING_MODEL",
    "messages": "[{'role': 'system', 'content': system_prompt}, "
                "{'role': 'user', 'content': user_prompt}]",
    "max_completion_tokens": "MATCHING_MAX_TOKENS",
    "reasoning_effort": "MATCHING_REASONING_EFFORT",
    "seed": "MATCHING_SEED",
    "response_format": "build_response_format()",
    "timeout": "config.get_matching_request_timeout()",
}

# A `**expansion` HAS NO `kw.arg`, so it is separated from the named keywords
# rather than sorted alongside them -- `sorted()` over a list holding None and
# strings raises, and an ABORT here would hide every check below the one it was
# meant to report. (The first version of this line did exactly that when the
# per-trial warmup pass added the expansion.)
_named_kwargs = ({kw.arg: ast.unparse(kw.value)
                  for kw in _chat_calls[0].keywords if kw.arg is not None}
                 if _chat_calls else {})
_expansions = ([ast.unparse(kw.value)
                for kw in _chat_calls[0].keywords if kw.arg is None]
               if _chat_calls else [])
check("the OpenAI call's keyword NAMES are exactly the seven it always sent",
      sorted(_named_kwargs), sorted(_PINNED_KWARGS))
for _name, _expr in sorted(_PINNED_KWARGS.items()):
    check(f"...and {_name} is still built from {_expr}",
          at(_named_kwargs, _name), _expr)
# THE EIGHTH ENTRY IS AN EXPANSION AND IT IS EMPTY UNLESS A ROUTING KEY WAS
# PASSED, which is what keeps the grouped-mode request byte-identical: the
# fixture harnesses record this call's kwargs DICT and look a recording up by a
# digest of it, so a key that were always present -- even carrying the SDK's
# NOT_GIVEN sentinel -- would change that digest for every recorded request.
check("...and the only other entry is one `**` expansion, of the per-trial "
      "cache-routing hint",
      _expansions, ["_extra_kwargs"])
check("...which is populated by exactly one guarded assignment, so it is "
      "empty whenever no routing key was given",
      sorted(ast.unparse(n) for n in ast.walk(_call_fn or ast.Module(
          body=[], type_ignores=[]))
          if isinstance(n, ast.Assign)
          and ast.unparse(n.targets[0]).startswith("_extra_kwargs")),
      ["_extra_kwargs = {}", "_extra_kwargs['prompt_cache_key'] = "
       "prompt_cache_key"])

check("the OpenAI call still reads its client from deps.get_openai_client()",
      "deps.get_openai_client()" in ast.unparse(_chat_calls[0].func)
      if _chat_calls else False, True)

# The dispatch is ABOVE the return, so the return is reached unchanged.
_returns = [n for n in ast.walk(_call_fn) if isinstance(n, ast.Return)] \
    if _call_fn else []
# THREE RATHER THAN TWO SINCE THE CONVERSE BRANCH LANDED, and this pin moving
# is the check working: it exists so that a return added anywhere in this
# function is REMARKED ON rather than absorbed. What it protects is unchanged
# and is asserted separately below -- that the OpenAI return is the LAST
# statement and is unguarded, so every provider arm sits above it and the
# default path is reached exactly as it always was.
check("call_matching_model has exactly three returns: the two Bedrock "
      "branches and the unchanged OpenAI one", len(_returns), 3)
check("...and the LAST statement of the function is still the unconditional "
      "OpenAI return, which is what 'the default path is unchanged' means",
      isinstance(_call_fn.body[-1], ast.Return) if _call_fn else False, True)
check("...naming the OpenAI client, with no provider guard around it",
      "deps.get_openai_client()" in ast.unparse(_call_fn.body[-1])
      if _call_fn else False, True)

# --- 1c. Behavioural: drive the real function ------------------------------

_openai_stub = _OpenAIStub(reply="OPENAI-REPLY")
_bedrock_stub = _BedrockStub(reply=BEDROCK_REPLY)

with overrides(openai_client=_openai_stub, bedrock_client=_bedrock_stub):
    _returned = drive(_evaluation.call_matching_model, "SYSTEM-P", "USER-P")

check("with the flag off the OpenAI client was called exactly once",
      len(_openai_stub.recorder.calls), 1)
check("...and the Bedrock stand-in was NOT touched",
      len(_bedrock_stub.recorder.calls), 0)
check("...and the object handed back is the OpenAI client's own reply",
      _returned, "OPENAI-REPLY")

_sent = _openai_stub.recorder.calls[0] if _openai_stub.recorder.calls else {}
check("the kwargs sent are exactly the seven",
      sorted(_sent), sorted(_PINNED_KWARGS))
check("model", at(_sent, "model"), config.MATCHING_MODEL)
check("messages", at(_sent, "messages"),
      [{"role": "system", "content": "SYSTEM-P"},
       {"role": "user", "content": "USER-P"}])
check("max_completion_tokens", at(_sent, "max_completion_tokens"),
      config.MATCHING_MAX_TOKENS)
check("reasoning_effort", at(_sent, "reasoning_effort"),
      config.MATCHING_REASONING_EFFORT)
check("seed", at(_sent, "seed"), config.MATCHING_SEED)
check("response_format", at(_sent, "response_format"), build_response_format())
check("timeout is the STRUCTURED object, not a bare float",
      at(_sent, "timeout") is config.get_matching_request_timeout(), True)
check("temperature is still not sent at all",
      "temperature" in _sent, False)

# --- 1d. No Bedrock client was built, cached or reached --------------------
#
# Asked through the NON-BUILDING diagnostics on purpose: a check that called
# the accessor would construct the very client it is asserting was not built.

check("no override is installed for BEDROCK_CLIENT after a flag-off call",
      deps.peek(deps.BEDROCK_CLIENT) is deps.UNSET, True)
check("...and this process has never BUILT one",
      deps.BEDROCK_CLIENT in deps.cached_keys(), False)
check("...so the seam reports it unresolved",
      deps.resolution_state(deps.BEDROCK_CLIENT), deps.RESOLVED_UNRESOLVED)

# AND THE FACTORY ITSELF REFUSES, so "no Bedrock client is built with the flag
# off" is a property of the FUNCTION rather than of today's call graph. Without
# this, the claim holds only because evaluation.py happens to dispatch above
# the call -- and would break silently the first time anything else reached it.
check("config.get_bedrock_client() REFUSES while the flag is off",
      raises(config.get_bedrock_client), "RuntimeError")
_gb_msg = message_of(config.get_bedrock_client)
for _needle in ("MATCHING_PROVIDER", "credential", "set_override"):
    check_true(f"...and the message names {_needle!r}", _needle in _gb_msg)
check("...and it did NOT cache anything on the way out",
      deps.BEDROCK_CLIENT in deps.cached_keys(), False)

# --- 1e. An unrecognised provider raises rather than defaulting -------------

with provider("bedrok"):                       # a plausible typo, not a value
    _typo = raises(_evaluation.call_matching_model, "S", "U")
check("an unrecognised MATCHING_PROVIDER RAISES rather than silently billing "
      "the incumbent", _typo, "RuntimeError")

with provider("bedrok"):
    _typo_msg = message_of(_evaluation.call_matching_model, "S", "U")
check_true("...and the message names the constant to edit",
           "MATCHING_PROVIDER" in _typo_msg
           and "oncotriage/config.py" in _typo_msg)


# ===========================================================================
# SECTION 2 — THE REQUEST MAPPING, FIELD BY FIELD
# ===========================================================================

section("2. Flag ON: every field of the Stage 5 request maps explicitly")

with provider(config.MATCHING_PROVIDER_BEDROCK):
    _req = drive(ba.build_bedrock_request, "SYSTEM-P", "USER-P")

check("build_bedrock_request returns a dict", isinstance(_req, dict), True)
check("the request keys are exactly the seven the default configuration sends",
      sorted(_req) if isinstance(_req, dict) else _req,
      ["input", "max_output_tokens", "model", "reasoning", "store", "text",
       "timeout"])

# model
with provider(config.MATCHING_PROVIDER_BEDROCK):
    check("model -> the wire id, NOT MATCHING_MODEL",
          at(_req, "model"), config.BEDROCK_MATCHING_MODEL)
    check("...which is what config.matching_wire_model() answers",
          at(_req, "model"), config.matching_wire_model())
check("...and MATCHING_MODEL is a DIFFERENT string, so the check is not "
      "vacuous", config.BEDROCK_MATCHING_MODEL == config.MATCHING_MODEL, False)

# messages -> input
_input = at(_req, "input", [])
check("messages -> input, two items", len(_input), 2)
check("the system message becomes an input item, not `instructions=`",
      "instructions" in _req if isinstance(_req, dict) else True, False)
check("item 0 carries the configured system role",
      at(_input[0] if _input else {}, "role"), config.BEDROCK_SYSTEM_ROLE)
check("item 1 is the user turn", at(_input[1] if len(_input) > 1 else {}, "role"),
      "user")
check("item 0 is the long `message` form, so a cache breakpoint has a block to "
      "attach to", at(_input[0] if _input else {}, "type"), "message")
check("item 0's content is one input_text part carrying the system prompt",
      at(_input[0] if _input else {}, "content"),
      [{"type": "input_text", "text": "SYSTEM-P"}])
check("item 1's content is one input_text part carrying the user prompt",
      at(_input[1] if len(_input) > 1 else {}, "content"),
      [{"type": "input_text", "text": "USER-P"}])

# max_completion_tokens -> max_output_tokens
check("max_completion_tokens -> max_output_tokens, value UNCHANGED",
      at(_req, "max_output_tokens"), config.MATCHING_MAX_TOKENS)
check("...and no chat-shaped max_completion_tokens survives",
      "max_completion_tokens" in _req if isinstance(_req, dict) else True, False)

# reasoning_effort -> reasoning.effort
check("reasoning_effort -> reasoning={'effort': ...}",
      at(_req, "reasoning"), {"effort": config.MATCHING_REASONING_EFFORT})
check("...carrying the configured effort verbatim, 'none' included",
      at(at(_req, "reasoning", {}), "effort"), "none")
check("...and no chat-shaped reasoning_effort survives",
      "reasoning_effort" in _req if isinstance(_req, dict) else True, False)

# response_format -> text.format
_chat_fmt = build_response_format()
_text = at(_req, "text", {})
_fmt = at(_text, "format", {}) if isinstance(_text, dict) else {}
check("response_format -> text.format", sorted(_text) if isinstance(_text, dict)
      else _text, ["format"])
check("the format is FLATTENED: type/name/schema/strict, no json_schema wrapper",
      sorted(_fmt) if isinstance(_fmt, dict) else _fmt,
      ["name", "schema", "strict", "type"])
check("type", at(_fmt, "type"), "json_schema")
check("name", at(_fmt, "name"), RESPONSE_SCHEMA_NAME)
check("strict survives the re-nesting", at(_fmt, "strict"), True)
check("the SCHEMA is the chat builder's, unwrapped rather than rebuilt",
      at(_fmt, "schema"), _chat_fmt["json_schema"]["schema"])
check("...and no chat-shaped response_format survives",
      "response_format" in _req if isinstance(_req, dict) else True, False)

# temperature / seed
check("temperature is still not sent",
      "temperature" in _req if isinstance(_req, dict) else True, False)
check("seed is NOT a top-level parameter (the Responses API has none)",
      "seed" in _req if isinstance(_req, dict) else True, False)
check("...and by default it is not smuggled through extra_body either",
      "extra_body" in _req if isinstance(_req, dict) else True, False)

# THE SEED CLAIM IS MEASURED AGAINST THE INSTALLED SDK, NOT ASSERTED.
import inspect as _inspect
from openai.resources.chat.completions import Completions as _SDKCompletions
from openai.resources.responses import Responses as _SDKResponses
_resp_params = set(_inspect.signature(_SDKResponses.create).parameters)
_chat_params = set(_inspect.signature(_SDKCompletions.create).parameters)
check("the installed SDK's responses.create really has no `seed`",
      "seed" in _resp_params, False)
check("...while chat.completions.create does, so the drop is a real "
      "difference and not a mis-reading", "seed" in _chat_params, True)
check("the SDK's responses.create does carry max_output_tokens",
      "max_output_tokens" in _resp_params, True)
check("...and reasoning", "reasoning" in _resp_params, True)
check("...and text", "text" in _resp_params, True)
check("...and store", "store" in _resp_params, True)

# THE DROP IS RECORDED, AND THE BUILDER IS PURE.
#
# `build_bedrock_request` is documented pure and is driven directly by every
# check above; a pure function that mutates a module-level counter is neither.
# The counter is bumped ONCE PER PROCESS beside the one warning, because the
# drop is a property of the CONFIGURATION: counting it per request would put a
# five-figure number in the run-end degradation report on every Bedrock run and
# make its "all counters are zero" line worthless.
_before_drop = ba.BEDROCK_ADAPTER_DEGRADATIONS[ba.DEGRADATION_SEED_DROPPED]
with provider(config.MATCHING_PROVIDER_BEDROCK):
    drive(ba.build_bedrock_request, "S", "U")
    drive(ba.build_bedrock_request, "S", "U")
check("build_bedrock_request is PURE: building a request moves no counter",
      ba.BEDROCK_ADAPTER_DEGRADATIONS[ba.DEGRADATION_SEED_DROPPED],
      _before_drop)

_seed_stub = _BedrockStub(reply=BEDROCK_REPLY)
ba._SEED_WARNED = False                       # a fresh process, for one block
try:
    with provider(config.MATCHING_PROVIDER_BEDROCK):
        with overrides(bedrock_client=_seed_stub):
            silence(ba.call_matching_model_bedrock, "S", "U")
            _after_one = ba.BEDROCK_ADAPTER_DEGRADATIONS[
                ba.DEGRADATION_SEED_DROPPED]
            silence(ba.call_matching_model_bedrock, "S", "U")
            _after_two = ba.BEDROCK_ADAPTER_DEGRADATIONS[
                ba.DEGRADATION_SEED_DROPPED]
finally:
    ba._SEED_WARNED = True
check("the first Bedrock CALL records the dropped seed -- it is never silent",
      _after_one, _before_drop + 1)
check("...and the second does not double-count a configuration fact",
      _after_two, _after_one)
check("...and that counter is registered in oncotriage/degradation.py's "
      "run-end report",
      "BEDROCK_ADAPTER_DEGRADATIONS" in _degradation.registered_names(), True)

# store
check("store is sent EXPLICITLY as False (the vendor default is True and the "
      "input is a patient record)", at(_req, "store"), False)
check("...from the config constant", at(_req, "store"), config.BEDROCK_STORE)

# timeout
check("the structured timeout is passed through untouched",
      at(_req, "timeout") is config.get_matching_request_timeout(), True)

# service_tier: omitted, not None
check("service_tier is OMITTED rather than sent as None",
      "service_tier" in _req if isinstance(_req, dict) else True, False)
with provider(config.MATCHING_PROVIDER_BEDROCK, BEDROCK_SERVICE_TIER="default"):
    _tiered = drive(ba.build_bedrock_request, "S", "U")
check("...and appears when it is set to the one supported value",
      at(_tiered, "service_tier"), "default")

# prompt caching
with provider(config.MATCHING_PROVIDER_BEDROCK,
              BEDROCK_PROMPT_CACHE_MODE="explicit",
              BEDROCK_PROMPT_CACHE_KEY="oncotriage:stage5"):
    _cached = drive(ba.build_bedrock_request, "S", "U")
check("prompt_cache_key is a first-class parameter",
      at(_cached, "prompt_cache_key"), "oncotriage:stage5")
check("prompt_cache_options travels in extra_body (the SDK has no such param)",
      at(_cached, "extra_body"), {"prompt_cache_options": {"mode": "explicit"}})
check("...and the SDK really has no prompt_cache_options parameter",
      "prompt_cache_options" in _resp_params, False)
check("...while prompt_cache_key really is one",
      "prompt_cache_key" in _resp_params, True)

# the seed escape hatch
_before_optin = ba.BEDROCK_ADAPTER_DEGRADATIONS[ba.DEGRADATION_SEED_DROPPED]
with provider(config.MATCHING_PROVIDER_BEDROCK,
              BEDROCK_SEND_SEED_IN_EXTRA_BODY=True):
    _seeded = drive(ba.build_bedrock_request, "S", "U")
check("the opt-in puts the seed in extra_body",
      at(at(_seeded, "extra_body", {}), "seed"), config.MATCHING_SEED)
check("...and stops counting a degradation, because nothing was dropped",
      ba.BEDROCK_ADAPTER_DEGRADATIONS[ba.DEGRADATION_SEED_DROPPED],
      _before_optin)

# the mantle endpoint
with provider(config.MATCHING_PROVIDER_BEDROCK,
              BEDROCK_ENDPOINT=config.BEDROCK_ENDPOINT_MANTLE,
              BEDROCK_MATCHING_MODEL="openai.gpt-5.6-terra"):
    _mantle = drive(ba.build_bedrock_request, "S", "U")
    _mantle_url = drive(config.get_bedrock_base_url)
check("the mantle endpoint accepts the BARE model id",
      at(_mantle, "model"), "openai.gpt-5.6-terra")
check("...and resolves to the /openai/v1 path the model card gives",
      _mantle_url,
      f"https://bedrock-mantle.{config.BEDROCK_REGION}.api.aws/openai/v1")
check("the runtime endpoint resolves to its own /openai/v1 path",
      drive(config.get_bedrock_base_url),
      f"https://bedrock-runtime.{config.BEDROCK_REGION}.amazonaws.com/openai/v1")


# ===========================================================================
# SECTION 3 — THE CONFIGURATION VALIDATOR
# ===========================================================================

section("3. The validator refuses a configuration that cannot work, by name")

with provider(config.MATCHING_PROVIDER_OPENAI):
    check("with the flag off the validator is a no-op",
          drive(config.validate_matching_provider_config), None)

_cases = [
    ("an in-Region model id on bedrock-runtime",
     dict(BEDROCK_ENDPOINT=config.BEDROCK_ENDPOINT_RUNTIME,
          BEDROCK_MATCHING_MODEL="openai.gpt-5.6-terra"),
     ["BEDROCK_MATCHING_MODEL", "us."]),
    ("an unknown endpoint", dict(BEDROCK_ENDPOINT="bedrock-elsewhere"),
     ["BEDROCK_ENDPOINT"]),
    ("an empty region", dict(BEDROCK_REGION=""), ["BEDROCK_REGION"]),
    ("an empty model id", dict(BEDROCK_MATCHING_MODEL=""),
     ["BEDROCK_MATCHING_MODEL"]),
    ("the priority tier, which this model does not support",
     dict(BEDROCK_SERVICE_TIER="priority"), ["BEDROCK_SERVICE_TIER"]),
    ("the flex tier, likewise", dict(BEDROCK_SERVICE_TIER="flex"),
     ["BEDROCK_SERVICE_TIER"]),
    ("a system role outside the vocabulary",
     dict(BEDROCK_SYSTEM_ROLE="assistant"), ["BEDROCK_SYSTEM_ROLE"]),
    ("a cache mode outside the vocabulary",
     dict(BEDROCK_PROMPT_CACHE_MODE="sometimes"),
     ["BEDROCK_PROMPT_CACHE_MODE"]),
]

for _label, _knobs, _needles in _cases:
    with provider(config.MATCHING_PROVIDER_BEDROCK, **_knobs):
        _msg = message_of(config.validate_matching_provider_config)
    check(f"REFUSED: {_label}", _msg == "<did not raise>", False)
    for _needle in _needles:
        check_true(f"...and the message names {_needle!r}", _needle in _msg)

with provider(config.MATCHING_PROVIDER_BEDROCK):
    check("the SHIPPED Bedrock configuration is itself valid",
          drive(config.validate_matching_provider_config), None)

# THIS PIN MOVED FROM TWO MEMBERS TO THREE, AND THE MOVE IS THE CHECK WORKING.
# A second Bedrock branch was added -- `bedrock_anthropic`, the Converse API
# serving Claude Sonnet 4.6, which is a THIRD provider rather than a mode of
# the second because it uses a different client library, credential chain,
# request shape and error classes. The argument is at MATCHING_PROVIDERS in
# oncotriage/config.py. What this file still asserts is what it always
# asserted: the vocabulary is CLOSED and its exact composition is pinned, so a
# fourth member cannot arrive unremarked.
#
# THE ORDER IS PINNED TOO, and that is not decoration: `MATCHING_PROVIDERS` is
# rendered into every refusal this file drives, so a reordering changes an
# operator-facing message.
check("the provider vocabulary is closed and has exactly three members",
      config.MATCHING_PROVIDERS,
      (config.MATCHING_PROVIDER_OPENAI, config.MATCHING_PROVIDER_BEDROCK,
       config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC))
check("...and THIS file's subject is still the Responses branch alone",
      config.MATCHING_PROVIDER_BEDROCK, "bedrock")
check("the endpoint vocabulary IS the URL table's keys, so the two cannot "
      "disagree", sorted(config.BEDROCK_BASE_URL_TEMPLATES),
      ["bedrock-mantle", "bedrock-runtime"])
check("Standard is the only tier offered, expressed as omit-or-default",
      config.BEDROCK_SERVICE_TIERS_ALLOWED, (None, "default"))


# ===========================================================================
# SECTION 4 — THE RESPONSE TRANSLATION
# ===========================================================================

section("4. The Responses reply becomes exactly what Stage 5 already consumes")

_cc = drive(ba.translate_response, BEDROCK_REPLY)
check("translate_response returns an SDK ChatCompletion",
      type(_cc).__name__, "ChatCompletion")

_choice = _cc.choices[0] if hasattr(_cc, "choices") else None
check("choices[0].message.content is the concatenated output_text",
      getattr(getattr(_choice, "message", None), "content", None),
      RESPONSE_JSON)
check("...with the reasoning item's text NOT folded in (it would break the "
      "JSON parse)",
      "SHOULD NOT APPEAR" in (getattr(getattr(_choice, "message", None),
                                      "content", "") or ""), False)
check("choices[0].message.refusal is None on a normal answer",
      getattr(getattr(_choice, "message", None), "refusal", "x"), None)
check("choices[0].finish_reason", getattr(_choice, "finish_reason", None),
      "stop")
check("response.model is the echo, PASSED THROUGH UNCHANGED",
      getattr(_cc, "model", None), "us.openai.gpt-5.6-terra")

check("usage.prompt_tokens <- input_tokens",
      _cc.usage.prompt_tokens, 19000)
check("usage.completion_tokens <- output_tokens",
      _cc.usage.completion_tokens, 2400)
check("usage.prompt_tokens_details.cached_tokens <- "
      "input_tokens_details.cached_tokens",
      _cc.usage.prompt_tokens_details.cached_tokens, 17920)
check("usage.completion_tokens_details.reasoning_tokens <- "
      "output_tokens_details.reasoning_tokens",
      _cc.usage.completion_tokens_details.reasoning_tokens, 0)
check("Bedrock's cache_write_tokens is CARRIED, not discarded at the boundary",
      (_cc.usage.prompt_tokens_details.model_extra or {}).get(
          "cache_write_tokens"), 0)

# --- NULL versus zero, which is the whole point of the cached column --------

_no_details = dict(BEDROCK_REPLY)
_no_details["usage"] = {"input_tokens": 5, "output_tokens": 1,
                        "total_tokens": 6}
_cc_nd = drive(ba.translate_response, _no_details)
check("a response that reported NO details object yields NO details object -- "
      "never one full of zeros",
      getattr(getattr(_cc_nd, "usage", None), "prompt_tokens_details", "x"),
      None)
check("...and the reasoning side likewise",
      getattr(getattr(_cc_nd, "usage", None), "completion_tokens_details", "x"),
      None)
check("...which is what Stage 5's defensive getattr turns into a stored NULL",
      getattr(getattr(getattr(_cc_nd, "usage", None),
                      "prompt_tokens_details", None), "cached_tokens", None),
      None)

_zero_details = dict(BEDROCK_REPLY)
_zero_details["usage"] = dict(BEDROCK_REPLY["usage"],
                              input_tokens_details={"cached_tokens": 0})
_cc_zd = drive(ba.translate_response, _zero_details)
check("a response that DID report zero yields 0, which is a measurement",
      _cc_zd.usage.prompt_tokens_details.cached_tokens, 0)

# --- finish_reason, all four outcomes --------------------------------------

def _reply(status, reason=None, **extra):
    r = dict(BEDROCK_REPLY)
    r["status"] = status
    if reason is not None:
        r["incomplete_details"] = {"reason": reason}
    r.update(extra)
    return r


check("completed -> stop",
      ba.translate_response(_reply("completed")).choices[0].finish_reason,
      "stop")
check("incomplete/max_output_tokens -> length, which is what drives Stage 5's "
      "truncation split",
      ba.translate_response(
          _reply("incomplete", "max_output_tokens")).choices[0].finish_reason,
      "length")
check("...and that is the constant Stage 5 actually branches on",
      _evaluation.FINISH_REASON_LENGTH, "length")
check("incomplete/content_filter -> content_filter",
      ba.translate_response(
          _reply("incomplete", "content_filter")).choices[0].finish_reason,
      "content_filter")

_before_unknown = ba.BEDROCK_ADAPTER_DEGRADATIONS[
    ba.DEGRADATION_UNKNOWN_INCOMPLETE]
_unknown = silence(ba.translate_response, _reply("incomplete", "who_knows"))
check("incomplete/<unrecognised> -> length, never stop",
      getattr(_unknown.choices[0], "finish_reason", None), "length")
check("...and it is counted, so the interpretation is visible",
      ba.BEDROCK_ADAPTER_DEGRADATIONS[ba.DEGRADATION_UNKNOWN_INCOMPLETE],
      _before_unknown + 1)

check("a FAILED response raises rather than becoming an empty verdict",
      raises(ba.translate_response, _reply("failed")),
      "BedrockResponseTranslationError")
check("a still-running response raises too",
      raises(ba.translate_response, _reply("in_progress")),
      "BedrockResponseTranslationError")
_no_model = {k: v for k, v in BEDROCK_REPLY.items() if k != "model"}
check("a response with NO model echo is refused BY NAME, not by an opaque "
      "pydantic validation error",
      raises(ba.translate_response, _no_model),
      "BedrockResponseTranslationError")
check_true("...and the message says why the echo matters",
           "which judge answered" in message_of(ba.translate_response, _no_model))
check("an empty model echo is refused the same way",
      raises(ba.translate_response, dict(BEDROCK_REPLY, model="")),
      "BedrockResponseTranslationError")
check("a response carrying an error object raises",
      raises(ba.translate_response,
             _reply("completed", error={"message": "boom"})),
      "BedrockResponseTranslationError")
check("...and the translation error is a RuntimeError, so a stray "
      "`except ValueError` cannot eat it",
      issubclass(ba.BedrockResponseTranslationError, RuntimeError), True)
check("...and is NOT a ValueError",
      issubclass(ba.BedrockResponseTranslationError, ValueError), False)

# --- a refusal --------------------------------------------------------------

_refusal_reply = dict(BEDROCK_REPLY)
_refusal_reply["output"] = [{"type": "message", "role": "assistant",
                             "content": [{"type": "refusal",
                                          "refusal": "I cannot help."}]}]
_cc_ref = drive(ba.translate_response, _refusal_reply)
check("a refusal content part lands on message.refusal",
      getattr(getattr(_cc_ref.choices[0], "message", None), "refusal", None),
      "I cannot help.")
check("...and content is empty rather than carrying the refusal text",
      getattr(getattr(_cc_ref.choices[0], "message", None), "content", None),
      "")

# --- the exact attribute chain Stage 5 reads -------------------------------
#
# Derived from evaluation.py by AST rather than retyped, so this cannot go
# stale independently of the code it describes.

_STAGE5_READS = (
    "response.choices[0]",
    "response.usage.prompt_tokens",
    "response.usage.completion_tokens",
)
_eval_src = open(_EVALUATION_PATH, encoding="utf-8").read()
for _expr in _STAGE5_READS:
    check_true(f"evaluation.py really reads {_expr}", _expr in _eval_src)

check("every attribute Stage 5 reads exists on the translated object",
      [bool(_cc.choices), _cc.usage.prompt_tokens is not None,
       _cc.usage.completion_tokens is not None,
       hasattr(_cc.choices[0], "finish_reason"),
       hasattr(_cc.choices[0].message, "content"),
       hasattr(_cc.choices[0].message, "refusal"),
       hasattr(_cc, "model")],
      [True] * 7)


# ===========================================================================
# SECTION 5 — THE CALL, THROUGH THE SEAM
# ===========================================================================

section("5. The call goes through deps, and the error taxonomy names failures")

_bedrock = _BedrockStub(reply=BEDROCK_REPLY)
_openai2 = _OpenAIStub(reply="SHOULD NOT BE USED")

with provider(config.MATCHING_PROVIDER_BEDROCK):
    with overrides(bedrock_client=_bedrock, openai_client=_openai2):
        _out = silence(_evaluation.call_matching_model, "SYS", "USR")

check("with the flag ON the Bedrock stand-in was called exactly once",
      len(_bedrock.recorder.calls), 1)
check("...and the OpenAI client was NOT touched",
      len(_openai2.recorder.calls), 0)
check("...and the value handed back is the translated ChatCompletion",
      type(_out).__name__, "ChatCompletion")
check("...carrying the model echo Stage 5 will compare",
      getattr(_out, "model", None), "us.openai.gpt-5.6-terra")

# BY AST, NOT BY SUBSTRING. The adapter's own docstring ARGUES that
# `with_options` is forbidden, so a text search for it is satisfied by the
# prose explaining the rule -- the "a file that argues about its own settings
# cannot be grepped for them" defect this project has shipped before. The scan
# below looks at CALL nodes, and its non-degeneracy probe is that it can see
# the call the module does make.
_ADAPTER_TREE = ast.parse(open(_ADAPTER_PATH, encoding="utf-8").read())
_adapter_calls = [ast.unparse(n.func) for n in ast.walk(_ADAPTER_TREE)
                  if isinstance(n, ast.Call)]
check("the adapter reaches its client through deps, not config",
      any(c == "deps.get_bedrock_client" for c in _adapter_calls), True)
check("...and there is no call to config.get_bedrock_client anywhere in it",
      any(c.endswith("config.get_bedrock_client") for c in _adapter_calls),
      False)
check("...and never through with_options, which would hand back an unwrapped "
      "client", any("with_options" in c for c in _adapter_calls), False)
check("NON-DEGENERACY: the AST scan really sees calls (it found the responses "
      "one)", any(c.endswith("responses.create") for c in _adapter_calls), True)
check("...and the word IS in the file as prose, which is what defeats a "
      "substring search",
      "with_options" in open(_ADAPTER_PATH, encoding="utf-8").read(), True)

# The model echo Stage 5 expects under each provider.
with provider(config.MATCHING_PROVIDER_BEDROCK):
    check("Stage 5 compares the echo against the WIRE model when the flag is on",
          config.matching_wire_model(), config.BEDROCK_MATCHING_MODEL)
check("...and against MATCHING_MODEL when it is off",
      config.matching_wire_model(), config.MATCHING_MODEL)

# AND THE MISMATCH MESSAGE NAMES THE RIGHT CONSTANT FOR THE PROVIDER. Under
# Bedrock the string that was SENT comes from BEDROCK_MATCHING_MODEL; telling
# an operator to edit MATCHING_MODEL there is a wrong instruction in an error
# that stops a run -- it changes the priced identity, leaves the wire id
# untouched, and the next run raises identically with the pricing key broken.
_mm_openai = str(_evaluation.MatchingModelMismatchError("a", "b"))
check("with the flag off the message names MATCHING_MODEL",
      "set MATCHING_MODEL in" in _mm_openai, True)
check("...and not the Bedrock constant",
      "BEDROCK_MATCHING_MODEL" in _mm_openai, False)
with provider(config.MATCHING_PROVIDER_BEDROCK):
    _mm_bedrock = str(_evaluation.MatchingModelMismatchError("a", "b"))
check("with the flag on it names BEDROCK_MATCHING_MODEL instead",
      "set BEDROCK_MATCHING_MODEL in" in _mm_bedrock, True)
check("...and both messages carry the provider, so a traceback says which "
      "branch raised",
      ("provider: openai" in _mm_openai, "provider: bedrock" in _mm_bedrock),
      (True, True))

# --- the error taxonomy -----------------------------------------------------

def _http_error(cls, status):
    """Build a real SDK exception without a live request."""
    import httpx
    request = httpx.Request("POST", "https://example.invalid/openai/v1/responses")
    response = httpx.Response(status, request=request, json={"error": {}})
    return cls("boom", response=response, body=None)


_taxonomy = [
    ("RateLimitError", 429, ba.ERROR_THROTTLED),
    ("AuthenticationError", 401, ba.ERROR_AUTH),
    ("PermissionDeniedError", 403, ba.ERROR_FORBIDDEN),
    ("NotFoundError", 404, ba.ERROR_NOT_FOUND),
    ("BadRequestError", 400, ba.ERROR_BAD_REQUEST),
    ("InternalServerError", 500, ba.ERROR_SERVER),
]
for _name, _status, _category in _taxonomy:
    _cls = getattr(openai, _name, None)
    check_true(f"the SDK still exposes {_name}", _cls is not None)
    if _cls is not None:
        _exc = drive(_http_error, _cls, _status)
        check(f"{_name} ({_status}) -> {_category}",
              drive(ba.classify_error, _exc) if not isinstance(_exc, Raised)
              else _exc, _category)

import httpx as _httpx
_req_obj = _httpx.Request("POST", "https://example.invalid/x")
check("APITimeoutError -> timeout",
      ba.classify_error(openai.APITimeoutError(request=_req_obj)),
      ba.ERROR_TIMEOUT)
check("APIConnectionError -> connection",
      ba.classify_error(openai.APIConnectionError(request=_req_obj)),
      ba.ERROR_CONNECTION)
check("...and timeout is checked BEFORE connection, since it subclasses it",
      issubclass(openai.APITimeoutError, openai.APIConnectionError), True)
check("a translation failure -> translation",
      ba.classify_error(ba.BedrockResponseTranslationError("x")),
      ba.ERROR_TRANSLATION)

_before_unclass = ba.BEDROCK_ADAPTER_DEGRADATIONS[ba.DEGRADATION_UNKNOWN_ERROR]
check("an exception the taxonomy does not name -> unclassified",
      ba.classify_error(ZeroDivisionError("nope")), ba.ERROR_UNCLASSIFIED)
check("...and that is COUNTED, so an unnamed failure is a finding",
      ba.BEDROCK_ADAPTER_DEGRADATIONS[ba.DEGRADATION_UNKNOWN_ERROR],
      _before_unclass + 1)
check("every category the classifier can return is in the closed vocabulary",
      sorted(set(ba.ERROR_CATEGORIES)), sorted(ba.ERROR_CATEGORIES))

# --- the degradation vocabulary is closed, AND that is enforced -------------
#
# DEGRADATION_KEYS would otherwise be a declaration nothing reads -- the
# `PASSWORD_SOURCE_ARGUMENT` shape that check 2h of
# tests/test_package_invariants.py exists to report. It is load-bearing here:
# every place the adapter bumps the counter is required to key it on a member,
# so a typo'd key becomes a failure rather than a counter nobody ever reads.

_bump_keys = sorted({
    ast.unparse(n.slice)
    for n in ast.walk(_ADAPTER_TREE)
    if isinstance(n, ast.Subscript)
    and ast.unparse(n.value).endswith("BEDROCK_ADAPTER_DEGRADATIONS")})
check("NON-DEGENERACY: the scan found the counter's subscript sites at all",
      len(_bump_keys) > 0, True)
check("every key the adapter bumps is a member of the closed vocabulary",
      [k for k in _bump_keys if getattr(ba, k, None) not in ba.DEGRADATION_KEYS],
      [])
check("...and every member of that vocabulary is a distinct string",
      len(set(ba.DEGRADATION_KEYS)), len(ba.DEGRADATION_KEYS))
check("...and the vocabulary names the seed drop, which is the one a normal "
      "Bedrock run produces",
      ba.DEGRADATION_SEED_DROPPED in ba.DEGRADATION_KEYS, True)

# a raising client is re-raised, not swallowed
_boom = _BedrockStub(reply=RuntimeError("network down"))
with provider(config.MATCHING_PROVIDER_BEDROCK):
    with overrides(bedrock_client=_boom):
        _raised = raises(_evaluation.call_matching_model, "S", "U")
check("a client failure is RE-RAISED for Stage 5's own except to handle",
      _raised, "RuntimeError")


# ===========================================================================
# SECTION 6 — THE PROVENANCE COLUMN
# ===========================================================================

section("6. inferences.matching_provider: written on every row, never "
        "backfilled")

PATIENT = {
    "patient_id": "bedrock-patient",
    "demographics": {"age": 61, "sex": "female", "birth_date": "1964-02-11"},
    "conditions": [], "medications": [], "allergies": [], "observations": [],
}


def result_dict(patient_id, **extra):
    """The minimum a terminal node emits that log_inference accepts."""
    base = {
        "patient_id": patient_id,
        "timestamp": "2026-08-21T00:00:00",
        "matching_model": "gpt-5.6-terra",
        "llm_classifier_input_tokens": 100,
        "llm_classifier_output_tokens": 20,
        "matches": [], "near_misses": [], "not_evaluable": [],
        "stage_timings": {},
    }
    base.update(extra)
    return base


def scratch_db(name):
    path = os.path.join(_TMP, name)
    _dl._INITIALIZED_DATABASES.discard(os.path.abspath(path))
    return path


def read_provider(db, patient_id):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT matching_provider FROM inferences WHERE patient_id = ?",
            (patient_id,)).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


check("the column is declared in the migration dict",
      _dl.INFERENCE_COLUMN_ADDITIONS.get("matching_provider"), "TEXT")

_db = scratch_db("provenance.db")
check("the scratch database is NOT the production one",
      os.path.abspath(_db) == os.path.abspath(
          _dl.resolve_inference_db_path(None)), False)

_written = silence(_dl.log_inference, result_dict("normal"), dict(PATIENT),
                   db_path=_db)
check("a normal row records the configured provider",
      read_provider(_db, "normal"), ["openai"])
check("...which is exactly config.MATCHING_PROVIDER's value",
      read_provider(_db, "normal"), [config.MATCHING_PROVIDER])
check("...and the write reported success",
      getattr(_written, "ok", None), True)

# The FAILURE-return shape: no matching_model, no tokens carried out.
_failure = result_dict("failed-run", matching_model=None,
                       llm_classifier_input_tokens=0,
                       llm_classifier_output_tokens=0,
                       error="GPT-4o API error (attempt 1): boom")
silence(_dl.log_inference, _failure, dict(PATIENT), db_path=_db)
check("a Stage 5 FAILURE row records the provider too -- the column is read "
      "from config, not from the result dict",
      read_provider(_db, "failed-run"), ["openai"])

with provider(config.MATCHING_PROVIDER_BEDROCK):
    silence(_dl.log_inference, result_dict("on-bedrock"), dict(PATIENT),
            db_path=_db)
check("with the flag on the same writer records 'bedrock'",
      read_provider(_db, "on-bedrock"), ["bedrock"])

# NULL versus a value: a pre-migration row is distinguishable.
_conn = sqlite3.connect(_db)
_conn.execute("INSERT INTO inferences (patient_id, timestamp) VALUES (?, ?)",
              ("pre-migration", "2026-01-01T00:00:00"))
_conn.commit()
_conn.close()
check("a row written without the column reads NULL, not a defaulted string",
      read_provider(_db, "pre-migration"), [None])
check("...and NULL is distinguishable from every legal value",
      None in config.MATCHING_PROVIDERS, False)

# The column is a plain string, deliberately.
_conn = sqlite3.connect(f"file:{_db}?mode=ro", uri=True)
_decl = {r[1]: r[2] for r in _conn.execute(
    "PRAGMA table_info(inferences)").fetchall()}
# `sqlite_sequence` is created by SQLite itself for an AUTOINCREMENT table and
# is not a table this project declared; excluding it is what makes the
# assertion about THIS schema.
_tables = [r[0] for r in _conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' "
    "AND name NOT LIKE 'sqlite_%'").fetchall()]
_conn.close()
check("matching_provider is TEXT in the real schema",
      at(_decl, "matching_provider"), "TEXT")
# `runs` JOINED THE LIST AT THE RUN-IDENTITY PASS and is NOT a lookup table for
# this column: it holds one row per batch campaign, and `matching_provider`
# remains a plain string on `inferences`. The assertion is kept EXACT rather
# than narrowed to "no table whose name mentions provider", because exact is
# what makes it fail when a lookup table IS introduced under any name.
# `run_metrics` JOINED AT THE HEALTH-PERSISTENCE PASS and is no more a lookup
# table for this column than `runs` is: it holds one row per degradation counter
# per campaign, keyed by `run_id`, and `matching_provider` remains a plain
# string on `inferences`.
check("...and no lookup table was introduced for it",
      sorted(_tables),
      ["drift_metrics", "inferences", "run_metrics", "runs", "trial_matches"])
check("...beside matching_model, which is TEXT for the same reason",
      at(_decl, "matching_model"), "TEXT")

# Nothing was backfilled.
check("no backfill statement exists anywhere in the writer",
      "UPDATE inferences SET matching_provider" in
      open(os.path.abspath(_dl.__file__), encoding="utf-8").read(), False)


# ===========================================================================
# SECTION 7 — THE RESUME FINGERPRINT, THE FIXTURE TUNABLE, THE SETTINGS TIER
# ===========================================================================

section("7. The resume gate sees a provider flip; the fixture records it")

# THE BEDROCK PASS LEFT THIS AT 2 AND SAID SO. The call-mode pass took it to 3
# BY GATING A NEW FIELD, which is what this constant is for -- so the claim
# checked here is no longer "unchanged" but "at least what the Bedrock pass
# needed, and never lowered". A LOWER value would mean a v2-stamped artifact
# silently comparing field-by-field against a shape that no longer matches it,
# which is the failure the version exists to prevent; a higher one is a later
# pass gating a field, which is the mechanism working.
check("FINGERPRINT_VERSION is at least the 2 this pass shipped, and was raised "
      "by a field being GATED rather than by anything here",
      run_fingerprint.FINGERPRINT_VERSION >= 2, True)
check("...and the gated field list has not SHRUNK below the six this pass "
      "relied on -- a field leaving the gate is what would make the provider "
      "flip invisible again",
      len(run_fingerprint.FINGERPRINT_FIELDS) >= 6, True)
check("matching_model_configured is still a GATED field",
      "matching_model_configured" in run_fingerprint.FINGERPRINT_FIELDS, True)

# `run_fingerprint.current()` IS DELIBERATELY NOT CALLED HERE. It resolves the
# Qdrant collection and probes the index, which is a live network round trip --
# and this file's whole claim is that it makes none. What is asserted instead
# is (a) by AST, that `current()` builds the gated field from
# `config.matching_wire_model()`, and (b) by value, that the function answers
# differently under the two providers, and (c) that `disagreements()` -- a pure
# function of two stamps -- reports the field. Together those are the property;
# a live `current()` would add only the round trip.

def _stamp_probe():
    """A fully-resolved stamp, keys DERIVED from FINGERPRINT_FIELDS."""
    out = {"fingerprint_version": run_fingerprint.FINGERPRINT_VERSION}
    for _f in run_fingerprint.FINGERPRINT_FIELDS:
        out[_f] = "pinned"
    return out


check("NON-DEGENERACY: a fully-pinned stamp IS resolved, so the check below "
      "is about the UNKNOWN and not about the stamp shape",
      run_fingerprint.is_resolved(_stamp_probe()), True)

_FP_SRC = open(os.path.abspath(run_fingerprint.__file__), encoding="utf-8").read()
_FP_TREE = ast.parse(_FP_SRC)
_fp_model_values = [
    ast.unparse(v)
    for n in ast.walk(_FP_TREE) if isinstance(n, ast.Dict)
    for k, v in zip(n.keys, n.values)
    if isinstance(k, ast.Constant) and k.value == "matching_model_configured"]
check("current() builds the gated field from exactly one expression",
      len(_fp_model_values), 1)
check("...and that expression is the DEGRADING wrapper, not the raising "
      "function itself",
      _fp_model_values[0] if _fp_model_values else "<none>", "_wire_model()")

# THE WRAPPER EXISTS BECAUSE current() PROMISES NOT TO RAISE. Both consumers
# call it from a main() that is about to spend money; an exception out of the
# stamp would abort the run the stamp exists to describe, and an unrecognised
# provider is exactly the input that makes matching_wire_model() raise.
check("_wire_model() answers the wire model on a good configuration",
      run_fingerprint._wire_model(), config.MATCHING_MODEL)
_before_fp = run_fingerprint.FINGERPRINT_DEGRADATIONS.copy()
with provider("bedrok"):
    _degraded_wire = silence(run_fingerprint._wire_model)
check("...and DEGRADES to UNKNOWN rather than raising on a bad one",
      _degraded_wire, run_fingerprint.UNKNOWN)
check("...counting the reason, keyed by exception type",
      [k for k in run_fingerprint.FINGERPRINT_DEGRADATIONS
       if k.startswith("matching_model_configured:")
       and run_fingerprint.FINGERPRINT_DEGRADATIONS[k] > _before_fp.get(k, 0)],
      ["matching_model_configured:RuntimeError"])
check("...and an UNKNOWN in a gated field is what makes a stamp UNRESOLVED, "
      "so compare() says 'this run did not establish' rather than 'the "
      "configuration changed'",
      run_fingerprint.is_resolved(
          {**_stamp_probe(), "matching_model_configured": run_fingerprint.UNKNOWN}),
      False)

check("with the flag off that function answers MATCHING_MODEL exactly, so "
      "every v2 stamp on disk still matches",
      config.matching_wire_model(), config.MATCHING_MODEL)
with provider(config.MATCHING_PROVIDER_BEDROCK):
    _wire_on = config.matching_wire_model()
check("with the flag on it answers the WIRE model",
      _wire_on, config.BEDROCK_MATCHING_MODEL)

def _stamp(model):
    """A complete stamp differing only in the gated model field.

    Keys are DERIVED from FINGERPRINT_FIELDS rather than enumerated, so a
    future gated field does not make this section fail for a reason that has
    nothing to do with the provider -- the lesson four test files learned when
    FINGERPRINT_VERSION went to 2.
    """
    out = {"fingerprint_version": run_fingerprint.FINGERPRINT_VERSION}
    for field in run_fingerprint.FINGERPRINT_FIELDS:
        out[field] = "pinned"
    out["matching_model_configured"] = model
    return out


_diff = run_fingerprint.disagreements(_stamp(config.MATCHING_MODEL),
                                      _stamp(_wire_on))
check("a provider flip is a DISAGREEMENT the resume gate can see",
      any(str(d).startswith("matching_model_configured") for d in _diff), True)
check("...and it is the ONLY one, so the signal is not buried",
      len(_diff), 1)
check("NON-DEGENERACY: two identical stamps disagree about nothing",
      run_fingerprint.disagreements(_stamp("x"), _stamp("x")), [])

check("MATCHING_PROVIDER is recorded in the fixture tunables dict",
      "\"MATCHING_PROVIDER\": config.MATCHING_PROVIDER," in
      open(os.path.join(os.path.dirname(os.path.dirname(_ADAPTER_PATH)),
                        "fixtures", "capture.py"), encoding="utf-8").read(),
      True)

check("the credential has its own project-prefixed variable",
      settings.ENV_BEDROCK_API_KEY, "ONCOTRIAGE_BEDROCK_API_KEY")
check("...and AWS's own name is the second tier",
      settings.ENV_AWS_BEARER_TOKEN_BEDROCK, "AWS_BEARER_TOKEN_BEDROCK")

_saved_env = {k: os.environ.get(k)
              for k in (settings.ENV_BEDROCK_API_KEY,
                        settings.ENV_AWS_BEARER_TOKEN_BEDROCK)}
try:
    for _k in _saved_env:
        os.environ.pop(_k, None)
    check("neither set -> (None, None), and the caller decides",
          settings.resolve_bedrock_api_key(), (None, None))
    check("...and config raises naming BOTH variables",
          all(n in message_of(config.get_bedrock_api_key)
              for n in (settings.ENV_BEDROCK_API_KEY,
                        settings.ENV_AWS_BEARER_TOKEN_BEDROCK)), True)

    os.environ[settings.ENV_AWS_BEARER_TOKEN_BEDROCK] = "  aws-key  "
    check("AWS's variable answers when it is the only one, whitespace stripped",
          settings.resolve_bedrock_api_key(),
          ("aws-key", settings.ENV_AWS_BEARER_TOKEN_BEDROCK))

    os.environ[settings.ENV_BEDROCK_API_KEY] = "project-key"
    check("...and the project-prefixed name WINS when both are set",
          settings.resolve_bedrock_api_key(),
          ("project-key", settings.ENV_BEDROCK_API_KEY))

    os.environ[settings.ENV_BEDROCK_API_KEY] = "   "
    check("a whitespace-only value counts as unset and falls through",
          settings.resolve_bedrock_api_key(),
          ("aws-key", settings.ENV_AWS_BEARER_TOKEN_BEDROCK))
finally:
    for _k, _v in _saved_env.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

check("the resolver never returns the value in an error message",
      "raw" in message_of(config.get_bedrock_api_key), False)

check("the three Bedrock wire ids are priced, so a run does not die at the "
      "first row", sorted(k for k in config.PRICING_CONFIG["models"]
                          if "openai.gpt-5.6-terra" in k),
      ["global.openai.gpt-5.6-terra", "openai.gpt-5.6-terra",
       "us.openai.gpt-5.6-terra"])
check("...and the configured one is among them",
      config.BEDROCK_MATCHING_MODEL in config.PRICING_CONFIG["models"], True)


# ===========================================================================
# SECTION 7b — THE FIXTURE HARNESSES REFUSE A PROVIDER THEY CANNOT HOOK
# ===========================================================================

section("7b. Capture and replay REFUSE the flag they cannot hook")

# THE FAILURE THIS PREVENTS IS THE MOST EXPENSIVE ONE IN THE PROJECT. Both
# harnesses hook deps.OPENAI_CLIENT and wrap chat.completions.create. With the
# flag on, Stage 5 reaches deps.BEDROCK_CLIENT and calls responses.create --
# so a CAPTURE would issue real billed calls and record none of them while
# assert_hooks_reach_the_agent still passed (it asserts by identity on
# OPENAI_CLIENT, and that object IS the harness's), and a REPLAY would bypass
# the OpenAI tripwire and send all twelve fixtures to a live endpoint while
# printing that it made no calls. That is pass 20c-2c's regression through a
# second provider.
#
# IMPORTED HERE RATHER THAN AT THE TOP because importing the fixture harness
# pulls in the whole agent graph, and section 1's claim that no client was ever
# BUILT is asserted against a process that has done as little as possible.
from oncotriage.fixtures import capture as _capture      # noqa: E402
from oncotriage.fixtures import replay as _replay        # noqa: E402

check("with the flag off the guard is a no-op",
      drive(_capture.assert_provider_is_hookable, "probe"), None)

with provider(config.MATCHING_PROVIDER_BEDROCK):
    _guard = raises(_capture.assert_provider_is_hookable, "install_recording_hooks")
    _guard_msg = message_of(_capture.assert_provider_is_hookable,
                            "install_recording_hooks")
check("with the flag on it REFUSES", _guard, "UnsupportedMatchingProviderError")
check("...and it is a RuntimeError, so a stray except ValueError cannot eat it",
      issubclass(_capture.UnsupportedMatchingProviderError, RuntimeError), True)
for _needle in ("MATCHING_PROVIDER", "bedrock", "responses.create",
                "BILLED", "oncotriage/config.py"):
    check_true(f"...and the message names {_needle!r}", _needle in _guard_msg)

# The guard is CALLED, by both installers, BEFORE any client is touched.
for _mod, _fn_name in ((_capture, "install_recording_hooks"),
                       (_replay, "install_replay_hooks")):
    _tree = ast.parse(open(os.path.abspath(_mod.__file__),
                           encoding="utf-8").read())
    _fn = next((n for n in ast.walk(_tree) if isinstance(n, ast.FunctionDef)
                and n.name == _fn_name), None)
    check_true(f"{_fn_name} is present", _fn is not None)
    _calls = [ast.unparse(c.func) for c in ast.walk(_fn)
              if isinstance(c, ast.Call)] if _fn else []
    check(f"{_fn_name} calls the guard",
          any(c.endswith("assert_provider_is_hookable") for c in _calls), True)
    # FIRST, not merely somewhere: a guard below the proxy construction would
    # already have resolved -- and therefore BUILT -- the real clients.
    _body = [n for n in (_fn.body if _fn else []) if not (
        isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    _first = ast.unparse(_body[0]) if _body else "<empty>"
    # Quote-agnostic: ast.unparse renders string literals with single quotes.
    check(f"...as the FIRST statement after the docstring, before any client "
          f"is resolved ({_fn_name})",
          _first.replace("'", '"').endswith(
              f'assert_provider_is_hookable("{_fn_name}")'),
          True)


# ===========================================================================
# SECTION 8 — NEGATIVE CONTROLS
# ===========================================================================

section("8. Planted defects: every mapping above is shown to be load-bearing")

# The unpatched copy must pass, or every plant below proves nothing.
_clean = exec_copy(lambda t: t.replace(
    "_SEED_WARNED = False", "_SEED_WARNED = True", 1))
with provider(config.MATCHING_PROVIDER_BEDROCK):
    _clean_req = drive(_clean.build_bedrock_request, "SYSTEM-P", "USER-P")
check("NON-DEGENERACY: an unpatched copy reproduces the shipped request",
      {k: v for k, v in _clean_req.items() if k != "timeout"}
      if isinstance(_clean_req, dict) else _clean_req,
      {k: v for k, v in _req.items() if k != "timeout"}
      if isinstance(_req, dict) else _req)

def _seed_delta(module):
    """How much one full Bedrock CALL bumps the seed-drop counter.

    Driven through the call rather than the builder, because that is where the
    bump lives -- see section 2. `_SEED_WARNED` is reset first so the probe
    measures the FIRST call of a notional process every time.
    """
    key = module.DEGRADATION_SEED_DROPPED
    module._SEED_WARNED = False
    before = module.BEDROCK_ADAPTER_DEGRADATIONS[key]
    with overrides(bedrock_client=_BedrockStub(reply=BEDROCK_REPLY)):
        silence(module.call_matching_model_bedrock, "S", "U")
    return module.BEDROCK_ADAPTER_DEGRADATIONS[key] - before


_plants = [
    ("max_output_tokens deleted from the request",
     lambda t: sub(t, '        "max_output_tokens": config.MATCHING_MAX_TOKENS,\n',
                   "", 1),
     lambda m: "max_output_tokens" in drive(m.build_bedrock_request, "S", "U"),
     True),
    ("store no longer sent, so the vendor default (retain 30 days) applies",
     lambda t: sub(t, '        "store": config.BEDROCK_STORE,\n', "", 1),
     lambda m: "store" in drive(m.build_bedrock_request, "S", "U"),
     True),
    ("strict dropped from the structured-output format",
     lambda t: sub(t, '    if "strict" in inner:\n        fmt["strict"] = inner["strict"]\n',
                   "    pass\n", 1),
     lambda m: "strict" in drive(m.build_bedrock_request,
                                 "S", "U")["text"]["format"],
     True),
    ("the reasoning effort is no longer carried",
     lambda t: sub(t, '    return {"effort": config.MATCHING_REASONING_EFFORT}',
                   "    return None", 1),
     lambda m: "reasoning" in drive(m.build_bedrock_request, "S", "U"),
     True),
    ("a truncated response is reported as a complete one",
     lambda t: sub(t, '    "max_output_tokens": FINISH_LENGTH,',
                   '    "max_output_tokens": FINISH_STOP,', 1),
     lambda m: m.translate_response(
         _reply("incomplete", "max_output_tokens")).choices[0].finish_reason,
     "length"),
    ("the input and output token counts are swapped",
     lambda t: sub(t, '    prompt_tokens = u.get("input_tokens") or 0',
                   '    prompt_tokens = u.get("output_tokens") or 0', 1),
     lambda m: m.translate_response(BEDROCK_REPLY).usage.prompt_tokens,
     19000),
    ("the refusal part is no longer recognised",
     lambda t: sub(t, '            elif kind == "refusal":',
                   '            elif kind == "refusal_DISABLED":', 1),
     lambda m: m.translate_response(
         _refusal_reply).choices[0].message.refusal,
     "I cannot help."),
    ("the system prompt goes back to the chat `system` role",
     lambda t: sub(t, "            _message_item(config.BEDROCK_SYSTEM_ROLE, system_prompt),",
                   '            _message_item("system", system_prompt),', 1),
     lambda m: drive(m.build_bedrock_request, "S", "U")["input"][0]["role"],
     config.BEDROCK_SYSTEM_ROLE),
    ("the seed drop stops being counted",
     lambda t: sub(t,
                   "    BEDROCK_ADAPTER_DEGRADATIONS[DEGRADATION_SEED_DROPPED] += 1\n    log.warning(",
                   "    log.warning(", 1),
     # A DELTA, NOT AN ABSOLUTE. The clean module is reused across the whole
     # loop, so its counter accumulates; an absolute expectation would be a
     # number that depends on how many plants ran before it.
     _seed_delta,
     1),
]

for _label, _mutate, _probe, _expected_clean in _plants:
    _planted = drive(exec_copy, _mutate)
    if isinstance(_planted, Raised):
        check(f"PLANT-FAILED: {_label}", _planted, "<a usable planted module>")
        continue
    with provider(config.MATCHING_PROVIDER_BEDROCK):
        _observed = drive(_probe, _planted)
    check(f"CAUGHT: {_label}", _observed == _expected_clean, False)

# And the same probes against the CLEAN copy must agree with the shipped value,
# or a plant "firing" would prove only that the probe is broken.
for _label, _mutate, _probe, _expected_clean in _plants:
    with provider(config.MATCHING_PROVIDER_BEDROCK):
        _observed = drive(_probe, _clean)
    check(f"...and the clean copy still reports the shipped value ({_label})",
          _observed, _expected_clean)


# ===========================================================================
# SECTION 9 — NOTHING LEAKED
# ===========================================================================

section("9. Nothing leaked: config restored, files untouched, no client built")

check("MATCHING_PROVIDER is back to the shipped value",
      config.MATCHING_PROVIDER, "openai")
# COMPARED AGAINST THE IMPORT-TIME CAPTURE, NOT AGAINST LITERALS -- see
# _SHIPPED_AT_IMPORT for why, and note that the capture is strictly the right
# instrument for a LEAKAGE check even where the literal would still work: the
# question is "is it back to what it was", and only the capture knows.
for _name in sorted(_SHIPPED_AT_IMPORT):
    if _name == "MATCHING_PROVIDER":
        continue                       # asserted by name immediately above
    check(f"config.{_name} is back to its shipped value",
          getattr(config, _name), _SHIPPED_AT_IMPORT[_name])

# NON-DEGENERACY: the capture is a comparison against a value, and a capture
# that had come back empty would make every check above pass for nothing.
check("...and the capture it is compared against is the full knob set",
      len(_SHIPPED_AT_IMPORT), 10)

# THE LITERAL PIN, ON THE CONSTANT THAT IS STILL A LITERAL. This is what an
# environment variable cannot move, so it says "the shipped default is
# us-east-1" without becoming a landmine for an operator who has exported one.
check("the shipped BEDROCK_REGION_DEFAULT is us-east-1",
      config.BEDROCK_REGION_DEFAULT, "us-east-1")
check("...and with no override set, that IS the resolved Region -- which is "
      "what makes the capture above the SHIPPED configuration rather than "
      "somebody's export",
      (config.BEDROCK_REGION_SOURCE is None,
       config.BEDROCK_REGION == config.BEDROCK_REGION_DEFAULT)
      if config.BEDROCK_REGION_SOURCE is None else (True, True), (True, True))

# ASKED THROUGH active_overrides(), NOT peek(). `peek` answers "what would this
# key resolve to without building", which includes a legitimately CACHED value
# -- so a `peek`-based check here would report any client this process built as
# a leaked override. That distinction is the whole reason both functions exist.
check("no override survives this file", sorted(deps.active_overrides()), [])
check("NO Bedrock client was ever BUILT -- every call went through a stand-in",
      deps.BEDROCK_CLIENT in deps.cached_keys(), False)
check("no OpenAI client was built either",
      deps.OPENAI_CLIENT in deps.cached_keys(), False)

check("oncotriage/agent/bedrock_adapter.py is byte-identical",
      hashlib.sha256(open(_ADAPTER_PATH, "rb").read()).hexdigest(),
      _ADAPTER_SHA_BEFORE)
check("oncotriage/agent/evaluation.py is byte-identical",
      hashlib.sha256(open(_EVALUATION_PATH, "rb").read()).hexdigest(),
      _EVALUATION_SHA_BEFORE)
check("...and the two hashes are NOT the same value, so the comparison above "
      "is not a tautology",
      _ADAPTER_SHA_BEFORE == _EVALUATION_SHA_BEFORE, False)

shutil.rmtree(_TMP, ignore_errors=True)
check("the scratch directory is gone", os.path.exists(_TMP), False)


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 74)
print("SUMMARY")
print("=" * 74)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")

if _FAILURES:
    print("\nFailures:")
    for _f in _FAILURES:
        print(f"  - {_f}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug 21 2026

@author: ramyalsaffar
"""
