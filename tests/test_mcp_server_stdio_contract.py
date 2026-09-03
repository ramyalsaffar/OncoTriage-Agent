# MCP Server stdio contract test
################################

"""The three MCP tools, their schemas, their framing, and the stdout contract.

WHAT THIS FILE HOLDS

    1.  The package is importable and ``oncotriage.mcp`` does not shadow the
        third-party ``mcp``. Asserted rather than reasoned about, because the
        two names differing only by a package prefix is the one thing about this
        layout that looks wrong at a glance.

    2.  The server builds and lists EXACTLY three tools, each with the parameter
        name a caller must supply -- ``bundle_path``, ``bundle_path``,
        ``nct_id``. This section exists because the first version of
        ``oncotriage/mcp/server.py`` advertised ``{"args": string, "kwargs":
        string}`` for all three: the failure-counting decorator wrapped each
        function as ``(*args, **kwargs)`` and the SDK derives the JSON Schema
        from ``inspect.signature``. Nothing raised, three tools listed, and no
        caller could have supplied a valid argument. It was found by printing
        the schema.

    3.  Every tool DESCRIPTION and every tool RESULT carries the
        not-for-clinical-use framing.

    4.  ``parse_fhir_bundle`` and ``lookup_trial`` called once each FOR REAL --
        a real bundle off disk, a real Qdrant round trip.

    5.  ``match_patient`` called once with the JUDGING STUBBED. See section 5
        for exactly what was replaced and what stayed real. THE LIVE BILLED
        PATH IS NOT EXERCISED BY THIS FILE OR ANY OTHER.

    6.  An unusable index produces a message and NO result -- specifically, no
        ``matches`` key, no ``trial`` key and no zero.

    7.  One wrong input per tool comes back as a clean one-line error, not a
        traceback.

    8.  THE STDOUT CONTRACT, with two negative controls that both fire.

NO NETWORK IS A LIE IN THIS FILE AND IT IS SAID PLAINLY: sections 4, 5 and 6
TALK TO THE REAL QDRANT ENDPOINT, because the readiness gate and the trial
lookup are the two things worth proving and neither means anything against a
stand-in. No OpenAI call is made anywhere, no money is spent, and nothing in the
repository or the production database is written.

IT IS NOT IN THE COLLISION MATRIX, derived rather than assumed: it writes only
inside a temp directory, patches no repository file, and the repository files it
READS (``oncotriage/mcp/*.py``) are written by neither of the suite's two
writers.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time


#------------------------------------------------------------------------------


# THE PACKAGE ROOT IS DERIVED FROM THIS FILE, not from the working directory,
# and there is a hard guard rather than a check() beneath it. A wrong root here
# is not one failure but every failure, each with a misleading message -- the
# argument the audit control and the snapshot-date test both carry.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CODE_DIR = os.path.dirname(_TESTS_DIR)

if not os.path.isdir(os.path.join(_CODE_DIR, "oncotriage")):
    raise RuntimeError(
        f"could not locate the oncotriage package from {__file__}: "
        f"{os.path.join(_CODE_DIR, 'oncotriage')} is not a directory")

if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)


#------------------------------------------------------------------------------


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label: str, actual, expected) -> None:
    """Assert equality, record the outcome, never abort the run."""
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(
            f"{label}\n          expected: {expected}\n          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def fail(label: str, detail: str) -> None:
    """Record a failure that is not an equality comparison."""
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}")
    print(f"          {detail}")


def raises(fn):
    """Return ``(type name, message)`` for a call that must raise.

    ``(None, "")`` when it did not raise, so the caller records that as a
    failure instead of the run aborting on the happy path.
    """
    try:
        fn()
    except Exception as exc:            # noqa: BLE001 -- the type is the answer
        return type(exc).__name__, str(exc)
    return None, ""


def at(seq, index, default=None):
    """``seq[index]`` or ``default``.

    THIS EXISTS BECAUSE THE PROJECT HAS SHIPPED THE OPPOSITE THREE TIMES. A bare
    subscript or ``next(...)`` over a result list raises when a defect makes the
    list short -- which is exactly the edit the surrounding section exists to
    catch -- so the run reports one traceback where it owes a summary and every
    check below it. ``tests/test_storage_query_layer.py``,
    ``tests/test_dashboard_reproducibility_tab.py`` and
    ``tests/test_docker_qdrant_override_and_readiness.py`` each had to fix that
    shape after it had already hidden a section.
    """
    try:
        return seq[index]
    except (IndexError, KeyError, TypeError):
        return default


#------------------------------------------------------------------------------


print("=" * 70)
print("SECTION 1 -- the package imports and does not shadow the SDK")
print("=" * 70)

import mcp as _sdk                                          # noqa: E402
import mcp_types as _sdk_types                              # noqa: E402

import oncotriage.mcp as _pkg                               # noqa: E402
from oncotriage.mcp import server as _server                # noqa: E402
from oncotriage import constants as _constants              # noqa: E402
from oncotriage.agent import deps as _deps                  # noqa: E402
from oncotriage.agent import readiness as _readiness        # noqa: E402

# ===========================================================================
# THIS FILE'S SUBJECT IS THE DORMANT OpenAI STAGE 5 REQUEST -- SO IT PINS IT
# ===========================================================================
#
# `config.MATCHING_PROVIDER` ships "bedrock_anthropic". Every Stage 5 stand-in
# below is installed at `deps.OPENAI_CLIENT` and wraps `chat.completions
# .create`, so at the shipped default the dispatch would reach
# `deps.BEDROCK_ANTHROPIC_CLIENT` and `converse` instead: the stand-in would
# never be called, every assertion here would compare against an empty
# recorder, and `config.get_bedrock_anthropic_client()` would BUILD -- boto3
# probing the instance metadata service from a suite that reports it makes no
# network call, and issuing live billed Converse requests on any host whose
# credential chain finds something.
#
# The pin, its cost and why it has one owner rather than a block per file are
# argued in tests/_provider_pin.py. THE SHIPPED ARM IS NOT COVERED BY THIS
# FILE; on Converse these subjects are covered by
# tests/test_agent_bedrock_anthropic_adapter.py and
# tests/test_agent_bedrock_anthropic_per_trial.py alone.
import _provider_pin                                             # noqa: E402

_PROVIDER_BEFORE_PIN = _provider_pin.pin_openai_arm(os.path.basename(__file__))


# `oncotriage.mcp` and `mcp` are different modules, and the SDK is the one that
# answers `import mcp`. Fired rather than argued: absolute-import resolution is
# the reason this layout is safe, and a check that says so is cheaper than the
# next reader re-deriving it.
check("the SDK's `mcp` is not oncotriage.mcp",
      _sdk is _pkg, False)
check("the SDK resolved to site-packages, not into the project",
      os.path.abspath(_sdk.__file__).startswith(os.path.abspath(_CODE_DIR)),
      False)
check("oncotriage.mcp resolved inside the project",
      os.path.abspath(_pkg.__file__).startswith(os.path.abspath(_CODE_DIR)),
      True)

# The pinned SDK version this server was written against. mcp 2.0.0's API is
# `from mcp.server import MCPServer`; the 1.x line's was
# `mcp.server.fastmcp.FastMCP`, so the pin and the code move together and a
# silent major bump must be visible here.
from importlib.metadata import version as _dist_version      # noqa: E402
_SDK_VERSION = _dist_version("mcp")
check("the installed SDK is the 2.x line this server targets",
      _SDK_VERSION.split(".")[0], "2")
print(f"  [info] mcp=={_SDK_VERSION}, protocol {_sdk_types.LATEST_PROTOCOL_VERSION}")


#------------------------------------------------------------------------------


print()
print("=" * 70)
print("SECTION 2 -- exactly three tools, with the parameters a caller supplies")
print("=" * 70)

import asyncio                                              # noqa: E402

_SERVER = _server.build_server()
_TOOLS = {t.name: t for t in asyncio.run(_SERVER.list_tools())}

check("three tools are listed", len(_TOOLS), 3)
check("the tool names", sorted(_TOOLS), ["lookup_trial", "match_patient", "parse_fhir_bundle"])

# THE REGRESSION THIS SECTION EXISTS FOR. `functools.wraps` in
# `oncotriage/mcp/server.py:_counted` is what makes `inspect.signature` -- and
# therefore the SDK's schema derivation -- see the real parameter instead of the
# decorator's `*args, **kwargs`. Asserting the NAME is what catches its loss;
# asserting "there is one property" would still pass on `{"args": ...}`.
_EXPECTED_PARAMS = {
    "parse_fhir_bundle": "bundle_path",
    "match_patient": "bundle_path",
    "lookup_trial": "nct_id",
}

for _name, _param in _EXPECTED_PARAMS.items():
    _tool = _TOOLS.get(_name)
    _schema = getattr(_tool, "input_schema", None) or {}
    _props = sorted((_schema.get("properties") or {}))
    check(f"{_name} advertises exactly [{_param}]", _props, [_param])
    check(f"{_name} requires {_param}",
          sorted(_schema.get("required") or []), [_param])
    check(f"{_name}'s {_param} is a string",
          (_schema.get("properties") or {}).get(_param, {}).get("type"), "string")

    # THE SECOND REGRESSION THIS SECTION EXISTS FOR. The tools were annotated
    # ``-> dict``, and mcp 2.0.0 refuses a bare ``dict`` as a structured-output
    # type -- SILENTLY, at registration: no output schema is derived, the tool
    # registers, the call succeeds, and every client gets the payload as a JSON
    # STRING in ``content`` with ``structured_content`` empty. Nothing raises,
    # and the only way to see it is to look at what a client decoded. The fix is
    # ``-> dict[str, Any]``; this is what stops it reverting.
    check(f"{_name} declares an output schema",
          isinstance(getattr(_tool, "output_schema", None), dict), True)
    check(f"{_name}'s output is an object",
          (getattr(_tool, "output_schema", None) or {}).get("type"), "object")


#------------------------------------------------------------------------------


print()
print("=" * 70)
print("SECTION 3 -- the not-for-clinical-use framing")
print("=" * 70)

_SHORT = _constants.NOT_FOR_CLINICAL_USE_SHORT
_LONG = _constants.NOT_FOR_CLINICAL_USE

# Non-degeneracy first: an empty string is `in` every string, so a framing check
# against one would pass forever while asserting nothing.
check("the short framing is non-degenerate", len(_SHORT) > 40, True)
check("the long framing is non-degenerate", len(_LONG) > 200, True)
check("both framings lead with the same claim",
      _SHORT.upper().startswith("NOT FOR CLINICAL USE")
      and _LONG.upper().startswith("NOT FOR CLINICAL USE"), True)

for _name, _tool in sorted(_TOOLS.items()):
    check(f"{_name}'s description carries the framing",
          _SHORT in (_tool.description or ""), True)

check("the server's instructions carry the long framing",
      _LONG in (_SERVER.instructions or ""), True)


#------------------------------------------------------------------------------


print()
print("=" * 70)
print("SECTION 4 -- parse and lookup, called once each FOR REAL")
print("=" * 70)

from oncotriage import paths as _paths                      # noqa: E402
import glob as _glob                                        # noqa: E402

_BUNDLES = sorted(_glob.glob(os.path.join(_paths.data_fhir_path, "*.json")))
check("the FHIR corpus is present and non-degenerate", len(_BUNDLES) > 0, True)

_BUNDLE = at(_BUNDLES, 0)

if _BUNDLE is None:
    fail("parse_fhir_bundle real call",
         "no bundle available; sections 4 and 5 could not run")
else:
    _parsed = _server.parse_fhir_bundle_tool(_BUNDLE)
    check("parse status", _parsed.get("status"), "ok")
    check("parse result carries the long framing",
          _parsed.get("not_for_clinical_use"), _LONG)
    # THE DE-IDENTIFICATION PASS MOVED BOTH OF THESE AND IT IS THE CHANGE
    # WORKING, not a relaxation. `patient_id` -- byte-identical to the bundle's
    # Medical Record Number on this corpus -- used to be reported here; it is a
    # stable pseudonym now, and `patient_data` (the raw parsed record, birth
    # date and all) is `patient_record`, exactly deid.RENDERED_FIELDS. The
    # ABSENCE of the old keys is asserted rather than only the presence of the
    # new ones, because a payload carrying both would satisfy a presence-only
    # check. tests/test_mcp_deidentified_responses.py is the file that proves
    # the rest of it.
    check("parse identifies the patient by a pseudonym, not a record number",
          str((_parsed.get("patient_summary") or {}).get("pseudonym") or "")
          .startswith("PT-"), True)
    check("...and no longer reports patient_id",
          "patient_id" in (_parsed.get("patient_summary") or {}), False)
    check("parse returned the de-identified record",
          isinstance(_parsed.get("patient_record"), dict), True)
    check("...and no longer returns the raw parsed record",
          "patient_data" in _parsed, False)
    # ECOG is reported as None rather than 0 when absent: 0 is FULLY ACTIVE.
    _ecog = (_parsed.get("patient_summary") or {}).get("ecog_performance_status")
    check("ECOG is an int in 0-4 or None, never a default zero",
          _ecog is None or (isinstance(_ecog, int) and 0 <= _ecog <= 4), True)
    print(f"  [info] parsed {os.path.basename(_BUNDLE)}: "
          f"{_parsed['patient_summary']}")

# --- lookup, against the live index ---------------------------------------
_PROBE = _readiness.probe_index()
print(f"  [info] index probe: {_PROBE['state']} "
      f"({_PROBE['points']} points in {_PROBE['collection']!r})")

if _PROBE["state"] != _readiness.INDEX_POPULATED:
    fail("lookup_trial real call",
         f"the index is {_PROBE['state']}; the real-call checks in this "
         f"section need a populated index and did not run")
else:
    # An NCT ID that is actually in the collection, taken FROM the collection
    # rather than hardcoded -- a hardcoded ID rots the next time the index is
    # rebuilt, and the failure would read as a broken lookup.
    _points, _ = _deps.get_qdrant_client().scroll(
        collection_name=_PROBE["collection"], limit=1, with_payload=True,
        with_vectors=False)
    _known = (at(_points, 0).payload or {}).get("nct_id") if _points else None
    check("an NCT ID was sampled from the live collection",
          bool(_known), True)

    if _known:
        _hit = _server.lookup_trial_tool(_known)
        check("lookup status for a known trial", _hit.get("status"), "ok")
        check("lookup result carries the long framing",
              _hit.get("not_for_clinical_use"), _LONG)
        check("lookup echoed the NCT ID", _hit.get("nct_id"), _known)
        check("lookup returned a title",
              bool((_hit.get("trial") or {}).get("title")), True)
        check("lookup returned the full scraped record",
              isinstance((_hit.get("trial") or {}).get("full_trial_json"), dict),
              True)
        # bm25_text is the tokenizer's input, not a fact about the trial.
        check("lookup does not leak bm25_text",
              "bm25_text" in (_hit.get("trial") or {}), False)
        print(f"  [info] looked up {_known}: "
              f"{str((_hit.get('trial') or {}).get('title'))[:60]}")

    # A well-formed ID that is not indexed is a RESULT, not an error, and it is
    # not confusable with a transport failure -- `lookup_trial` raises for that.
    _miss = _server.lookup_trial_tool("NCT00000001")
    check("an unindexed NCT ID is reported as not_found",
          _miss.get("status"), "not_found")
    check("not_found carries the framing too",
          _miss.get("not_for_clinical_use"), _LONG)
    check("not_found carries no trial payload", "trial" in _miss, False)


#------------------------------------------------------------------------------


print()
print("=" * 70)
print("SECTION 5 -- match_patient, with the judging stubbed")
print("=" * 70)
print("""
  WHAT IS STUBBED AND WHAT IS REAL
  --------------------------------
  Replaced, through oncotriage/agent/deps.py -- the project's own dependency
  seam, the same one the fixture harnesses use -- and NOWHERE ELSE. No source
  file is patched and the stubs exist only in this test module:

    deps.OPENAI_CLIENT   a stand-in serving BOTH `.embeddings.create` (Stage 2's
                         dense channel) and `.chat.completions.create` (Stage 5,
                         the judging). One object because that is the one object
                         production uses for both; a second override key would
                         be a bypass built for the test.
    deps.MEDCPT_SCORER   a constant scorer, so Stage 3 does not download and run
                         the 110 MB cross-encoder. This is the documented
                         (query, trial_texts) -> scores seam.

  Left REAL: the readiness probe, the Qdrant client, retrieval, the MeSH
  expansion, the rule-based filter, the graph itself, and every wrapper in
  oncotriage/mcp/server.py.

  THEREFORE: the live billed path is NOT exercised here or anywhere in the
  suite. What is proven is the plumbing -- gate, parse, graph invocation,
  result shape, framing. What is NOT proven is that a real model's response
  survives Stage 5's parser through this wrapper.
""")

_EMBED_DIM = 1536       # text-embedding-3-small; the collection's dense width.


class _StubMessage:
    def __init__(self, content):
        self.content = content


class _StubChoice:
    def __init__(self, content):
        self.message = _StubMessage(content)
        # finish_reason is read with getattr(..., None) by the evaluator, and a
        # real stop is what a complete answer looks like.
        self.finish_reason = "stop"


class _StubUsage:
    prompt_tokens = 1000
    completion_tokens = 200
    completion_tokens_details = None


class _StubChatResponse:
    def __init__(self, content):
        self.choices = [_StubChoice(content)]
        self.usage = _StubUsage()
        # None, deliberately. The evaluator raises MatchingModelMismatchError
        # when a response names a model other than MATCHING_MODEL, and None is
        # its documented "a stub answered" path. Naming the real model would be
        # this test asserting it had called something it had not.
        self.model = None


class _StubEmbeddings:
    def __init__(self, owner):
        self._owner = owner

    def create(self, model=None, input=None, timeout=None, **kwargs):
        self._owner.embedding_calls += 1
        return type("R", (), {
            "data": [type("D", (), {"embedding": [0.001] * _EMBED_DIM})()]
        })()


class _StubCompletions:
    def __init__(self, owner):
        self._owner = owner

    def create(self, model=None, messages=None, **kwargs):
        self._owner.chat_calls += 1
        self._owner.captured_messages = messages
        return _StubChatResponse(json.dumps(self._owner.verdicts))


class _StubChat:
    def __init__(self, owner):
        self.completions = _StubCompletions(owner)


class StubOpenAI:
    """The two call shapes the agent makes, and a count of each.

    Shaped after ``StubOpenAI`` / ``StubEmbeddingOpenAI`` in
    ``tests/test_agent_retrieval_observability.py`` rather than invented: those
    are the shapes this pipeline's Stage 2 and Stage 5 already drive in the
    suite, so a divergence here would be this file testing a client production
    does not have.
    """

    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.embedding_calls = 0
        self.chat_calls = 0
        self.captured_messages = None
        self.embeddings = _StubEmbeddings(self)
        self.chat = _StubChat(self)


# An EMPTY verdict list, and that is a deliberate limit rather than laziness.
# `[]` is unambiguously the valid shape -- Stage 5 requires a JSON list and
# normalises everything inside it -- so the pipeline completes and node_finalize
# partitions an empty set. Hand-authoring verdicts would mean asserting against
# a criterion schema this file would be guessing at, and a stub whose output
# shape is wrong tests the parser's error handling while claiming to test the
# happy path.
_STUB_VERDICTS = []

_stub_openai = StubOpenAI(_STUB_VERDICTS)


def _stub_scorer(query, trial_texts):
    """The MEDCPT_SCORER seam: (query, trial_texts) -> one score per text.

    A NUMPY ARRAY, NOT A LIST, and that is the seam's real contract rather than
    a detail. ``oncotriage/agent/models.py:medcpt_score_pairs`` is annotated
    ``-> "np.ndarray"`` and ``node_cross_encoder_rerank`` calls ``scores.min()``
    on the result when it records its observability line. The first version of
    this stub returned a plain list and the run died with
    ``AttributeError: 'list' object has no attribute 'min'`` -- inside a
    LangGraph node, so the traceback named the framework rather than the stub.
    A stand-in whose type is wrong tests the pipeline's reaction to a broken
    dependency while claiming to test the happy path.
    """
    import numpy as np
    return np.full(len(trial_texts), 0.5, dtype=float)


if _BUNDLE is None or _PROBE["state"] != _readiness.INDEX_POPULATED:
    fail("match_patient stubbed call",
         "needs a bundle and a populated index; did not run")
else:
    _saved = _deps.set_overrides({
        _deps.OPENAI_CLIENT: _stub_openai,
        _deps.MEDCPT_SCORER: _stub_scorer,
    })
    try:
        # THE OVERRIDE IS ASSERTED BY IDENTITY BEFORE IT IS RELIED ON. This is
        # the fixture harnesses' rule: an override that silently failed to
        # install would send Stage 5's prompt to the real endpoint, be billed,
        # and let this section report that it had stubbed everything.
        check("the OpenAI override reached the agent's seam",
              _deps.get_openai_client() is _stub_openai, True)

        _matched = _server.match_patient_tool(_BUNDLE)

        check("match status", _matched.get("status"), "ok")
        check("match result carries the long framing",
              _matched.get("not_for_clinical_use"), _LONG)
        check("match returned a result dict",
              isinstance(_matched.get("result"), dict), True)
        check("match stamped the collection it queried",
              bool((_matched.get("result") or {}).get("qdrant_collection")), True)
        check("match stamped the patient hash",
              bool((_matched.get("result") or {}).get("patient_data_hash")), True)
        # The billed endpoints were replaced, and the counters prove the stub was
        # actually reached rather than merely installed.
        check("the stubbed embedding endpoint was called",
              _stub_openai.embedding_calls > 0, True)
        print(f"  [info] stub calls: {_stub_openai.embedding_calls} embedding, "
              f"{_stub_openai.chat_calls} chat")
        print(f"  [info] result keys: {sorted((_matched.get('result') or {}))[:8]}")
    finally:
        _deps.restore_overrides(_saved)

    check("the override was removed again",
          _deps.get_openai_client() is _stub_openai, False)


#------------------------------------------------------------------------------


print()
print("=" * 70)
print("SECTION 6 -- an unusable index yields a message, never an empty answer")
print("=" * 70)


class _AbsentIndexClient:
    """A Qdrant stand-in whose collection does not exist."""

    def collection_exists(self, _collection):
        return False

    def count(self, *_a, **_k):
        raise AssertionError("count must not be reached when the collection is absent")


class _EmptyIndexClient:
    """A Qdrant stand-in whose collection exists and holds nothing.

    THIS IS THE STATE THE WHOLE GATE EXISTS FOR. Every retrieval call against it
    SUCCEEDS and returns an empty list, so without the gate the graph routes to
    node_no_candidates and produces a well-formed 'no eligible trials' result
    that nothing downstream could tell from a real one.
    """

    def collection_exists(self, _collection):
        return True

    def count(self, *_a, **_k):
        return type("C", (), {"count": 0})()


class _UnverifiableIndexClient:
    """A Qdrant stand-in that cannot be reached at all."""

    def collection_exists(self, _collection):
        raise ConnectionError("stand-in: the server could not be reached")

    def count(self, *_a, **_k):
        raise ConnectionError("stand-in: the server could not be reached")


_GATE_CASES = (
    ("absent", _AbsentIndexClient(), _readiness.INDEX_ABSENT),
    ("empty", _EmptyIndexClient(), _readiness.INDEX_EMPTY),
    ("unverifiable", _UnverifiableIndexClient(), _readiness.INDEX_UNVERIFIABLE),
)

for _label, _client, _expected_state in _GATE_CASES:
    _saved = _deps.set_overrides({_deps.QDRANT_CLIENT: _client})
    try:
        # probe_index caches nothing, but require_populated_index caches a
        # POPULATED verdict process-wide and section 5 has just produced one.
        # Clearing it is what makes these three cases about the stand-in rather
        # than about the previous section.
        _readiness.reset_index_probe_cache()

        for _tool_name, _fn, _arg in (
                ("match_patient", _server.match_patient_tool, _BUNDLE or "x.json"),
                ("lookup_trial", _server.lookup_trial_tool, "NCT00000001")):
            _out = _fn(_arg)
            check(f"{_tool_name} refuses an {_label} index",
                  _out.get("status"), "index_unavailable")
            check(f"{_tool_name} names the {_label} state",
                  _out.get("index_state"), _expected_state)
            check(f"{_tool_name} still carries the framing when refusing",
                  _out.get("not_for_clinical_use"), _LONG)
            # THE SHAPE IS THE ASSERTION. Not "matches == []" -- the absence of
            # the key. A caller cannot mistake a missing key for a finding of
            # zero, and a model summarising the payload has no empty list to
            # report as an answer.
            check(f"{_tool_name}'s {_label} refusal carries no matches key",
                  "matches" in _out, False)
            check(f"{_tool_name}'s {_label} refusal carries no result key",
                  "result" in _out, False)
            check(f"{_tool_name}'s {_label} refusal carries no trial key",
                  "trial" in _out, False)
            check(f"{_tool_name}'s {_label} message says no result is reported",
                  "NO RESULT IS BEING REPORTED" in (_out.get("message") or ""),
                  True)
    finally:
        _deps.restore_overrides(_saved)
        _readiness.reset_index_probe_cache()

# NON-DEGENERACY: the three stand-ins must not all be reporting the same state,
# which is what a gate that had stopped discriminating would look like.
check("the three gate cases produced three distinct states",
      len({s for _l, _c, s in _GATE_CASES}), 3)


#------------------------------------------------------------------------------


print()
print("=" * 70)
print("SECTION 7 -- one wrong input per tool is a clean error, not a traceback")
print("=" * 70)

_BEFORE_FAILURES = dict(_server.failure_report())

_WRONG_INPUTS = (
    ("parse_fhir_bundle", _server.parse_fhir_bundle_tool,
     os.path.join(_TESTS_DIR, "no-such-bundle-anywhere.json"), "No such file"),
    ("match_patient", _server.match_patient_tool,
     os.path.join(_TESTS_DIR, "no-such-bundle-anywhere.json"), "No such file"),
    ("lookup_trial", _server.lookup_trial_tool,
     "not-an-nct-id", "not a well-formed NCT ID"),
)

for _tool_name, _fn, _bad, _needle in _WRONG_INPUTS:
    _type, _message = raises(lambda f=_fn, b=_bad: f(b))
    check(f"{_tool_name} rejects a wrong input", _type, "ValueError")
    check(f"{_tool_name}'s message explains what was wrong",
          _needle in _message, True)
    # A message, not a rendered stack. The SDK wraps whatever a tool raises as
    # ToolError(f"Error executing tool {name}: {e}") -- read out of
    # mcp/server/mcpserver/tools/base.py line 181 -- so what reaches the client
    # is str(exc) and never a traceback. Asserting the message is one line is
    # what says this file's own error text has not grown one.
    check(f"{_tool_name}'s message is not a rendered traceback",
          "Traceback (most recent call last)" in _message, False)

# EVERY exception that left a tool was counted. The project's rule is that
# nothing is recovered from silently; these handlers count AND re-raise.
_AFTER_FAILURES = dict(_server.failure_report())
_NEW = {k: _AFTER_FAILURES[k] - _BEFORE_FAILURES.get(k, 0)
        for k in _AFTER_FAILURES
        if _AFTER_FAILURES[k] - _BEFORE_FAILURES.get(k, 0) > 0}
check("all three failures were counted",
      sorted(_NEW), ["lookup_trial:ValueError",
                     "match_patient:ValueError",
                     "parse_fhir_bundle:ValueError"])

# A FOURTH CASE, because the first version of the server got it wrong. A file
# that EXISTS but is not JSON used to let the parser's own exception through, so
# the client received json's message and nothing else:
#
#     Error executing tool parse_fhir_bundle: Expecting value: line 1 column 1
#
# Accurate, and it names neither the file nor the parameter -- a caller that
# passed one of several paths cannot tell which was wrong, and a model reading
# it has nothing to correct. `_parse_bundle` re-raises as ValueError naming the
# path. This is what stops that reverting.
_SCRATCH = tempfile.mkdtemp(prefix="oncotriage-mcp-")
_NOT_JSON = os.path.join(_SCRATCH, "not-json.json")
with open(_NOT_JSON, "w", encoding="utf-8") as _fh:
    _fh.write("this is not json at all\n")

_type, _message = raises(lambda: _server.parse_fhir_bundle_tool(_NOT_JSON))
check("a file that is not JSON is a ValueError, not the parser's own type",
      _type, "ValueError")
check("...and the message names the offending file",
      _NOT_JSON in _message, True)
check("...and names the parameter the caller has to fix",
      "bundle_path" in _message, True)

# A well-formed JSON document that is not a bundle: parses, yields no patient.
_NOT_BUNDLE = os.path.join(_SCRATCH, "not-a-bundle.json")
with open(_NOT_BUNDLE, "w", encoding="utf-8") as _fh:
    json.dump({"hello": "world"}, _fh)

_type, _message = raises(lambda: _server.parse_fhir_bundle_tool(_NOT_BUNDLE))
check("valid JSON that is not a FHIR Bundle is a ValueError", _type, "ValueError")
check("...and the message says a Patient resource is required",
      "Patient resource" in _message, True)

shutil.rmtree(_SCRATCH, ignore_errors=True)


#------------------------------------------------------------------------------


print()
print("=" * 70)
print("SECTION 8 -- the stdout contract")
print("=" * 70)

# --- 8a: the per-call guard, at the unit level ----------------------------
#
# THE CONTROL THE BRIEF ASKS FOR: make a wrapped function print, and show the
# assertion can fail. The wrapped function is replaced by name on the server
# module -- `lookup_trial` is the name `lookup_trial_tool` calls -- so nothing
# in the shipped source is edited and the substitution is undone in a finally.


def _noisy_lookup(nct_id, *_a, **_k):
    """Stands in for the wrapped package function, and prints like the real
    pipeline does."""
    print("STRAY OUTPUT FROM A WRAPPED FUNCTION")
    return {"found": False, "nct_id": "NCT00000001",
            "collection": "stand-in", "trial": None}


_real_lookup = _server.lookup_trial
_saved_gate = _deps.set_overrides({_deps.QDRANT_CLIENT: _EmptyIndexClient()})
try:
    _server.lookup_trial = _noisy_lookup
    _readiness.reset_index_probe_cache()

    # (i) THROUGH the guard: the tool is called and nothing reaches stdout.
    #     The index gate is bypassed for this one call by asking the tool's
    #     inner path directly, because the gate would refuse before reaching
    #     the noisy function.
    _captured = io.StringIO()
    with contextlib.redirect_stdout(_captured):
        with _server._stdout_to_stderr():
            _noisy_lookup("NCT00000001")
    check("8a(i)  a wrapped function's print does NOT reach stdout under the guard",
          _captured.getvalue(), "")

    # (ii) THE CONTROL. The identical call WITHOUT the guard. If this does not
    #      produce output, check (i) proves nothing -- it would be passing
    #      because the function is silent, not because the guard works.
    _captured_control = io.StringIO()
    with contextlib.redirect_stdout(_captured_control):
        _noisy_lookup("NCT00000001")
    check("8a(ii) CONTROL: the same print DOES reach stdout without the guard",
          "STRAY OUTPUT FROM A WRAPPED FUNCTION" in _captured_control.getvalue(),
          True)
finally:
    _server.lookup_trial = _real_lookup
    _deps.restore_overrides(_saved_gate)
    _readiness.reset_index_probe_cache()

check("8a     the shipped function was put back",
      _server.lookup_trial is _real_lookup, True)


# --- 8b: a real session, over a real pipe, with all of stdout captured ----

_INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": _sdk_types.LATEST_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "oncotriage-contract-test", "version": "1"},
    },
}
_INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
_CALL = {
    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
    "params": {"name": "lookup_trial", "arguments": {"nct_id": "NCT00000001"}},
}

_FRAMES = "".join(json.dumps(m) + "\n"
                  for m in (_INIT, _INITIALIZED, _LIST, _CALL))


def _run_server(argv, stdin_text, expect_ids=(1, 2, 3), timeout=240,
                path_prefix=None, cwd=None):
    """Drive the server as a real subprocess session; return (stdout, stderr, rc).

    stdout and stderr are captured SEPARATELY, which is the whole point: the
    assertion is about what is on fd 1 and nothing else.

    STDIN IS HELD OPEN UNTIL THE ANSWERS ARRIVE, and the first version of this
    helper did not do that. It used ``subprocess.run(input=...)``, which writes
    every frame and then CLOSES stdin -- so the transport's ``stdin_reader`` saw
    EOF, tore the session down, and cancelled the in-flight ``tools/call``
    before it returned. The run reported 2 protocol messages instead of 3 and
    looked exactly like a broken tool: initialize and tools/list answered,
    tools/call silently missing. It is a property of stdio servers rather than
    of this one, which is why it is written down here.

    BOTH PIPES ARE DRAINED BY THREADS. A subprocess whose stderr pipe fills
    blocks in ``write`` and never answers, and this server is deliberately
    chatty on stderr -- the entire pipeline's output goes there. Draining only
    stdout would deadlock as soon as the pipeline printed more than a pipe
    buffer.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [p for p in (path_prefix, _CODE_DIR) if p] + [env.get("PYTHONPATH", "")])
    # Unbuffered, so a stray write cannot be hidden by the process dying before
    # a flush -- which would make the stdout assertion pass for the wrong reason.
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [sys.executable] + argv,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=cwd or _CODE_DIR, env=env)

    out_lines, err_chunks = [], []

    def _drain_out():
        for line in proc.stdout:
            out_lines.append(line)

    def _drain_err():
        err_chunks.append(proc.stderr.read())

    t_out = threading.Thread(target=_drain_out, daemon=True)
    t_err = threading.Thread(target=_drain_err, daemon=True)
    t_out.start()
    t_err.start()

    try:
        proc.stdin.write(stdin_text)
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as exc:
        # The server died before reading the frames. Recorded rather than
        # swallowed; the caller's checks will report it as missing answers.
        err_chunks.append(f"\n[test] could not write frames: {exc!r}\n")

    # Wait for the answers we asked for, then stop. Polling the accumulated
    # lines rather than blocking on readline() is what keeps a server that never
    # answers from hanging the suite.
    wanted = set(expect_ids)
    deadline = time.time() + timeout
    while time.time() < deadline:
        seen = set()
        for line in list(out_lines):
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and message.get("id") is not None:
                seen.add(message["id"])
        if wanted <= seen:
            break
        if proc.poll() is not None:
            break
        time.sleep(0.05)

    with contextlib.suppress(BrokenPipeError, OSError, ValueError):
        proc.stdin.close()

    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=30)

    t_out.join(timeout=10)
    t_err.join(timeout=10)

    return "".join(out_lines), "".join(err_chunks), proc.returncode


def _classify_stdout(text):
    """Split captured stdout into (protocol messages, non-protocol lines)."""
    protocol, garbage = [], []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            garbage.append(line)
            continue
        if isinstance(message, dict) and message.get("jsonrpc") == "2.0":
            protocol.append(message)
        else:
            garbage.append(line)
    return protocol, garbage


print("  running the documented entry point: python mcp_server.py")
_out, _err, _rc = _run_server(["mcp_server.py"], _FRAMES)

_protocol, _garbage = _classify_stdout(_out)

check("8b     the session produced protocol messages at all",
      len(_protocol) >= 3, True)
if _garbage:
    fail("8b     stdout carries ONLY protocol",
         f"{len(_garbage)} non-protocol line(s), first: {_garbage[0][:120]!r}")
else:
    check("8b     stdout carries ONLY protocol", len(_garbage), 0)

_by_id = {m.get("id"): m for m in _protocol if "id" in m}
check("8b     initialize was answered", 1 in _by_id, True)
check("8b     tools/list was answered", 2 in _by_id, True)
check("8b     tools/call was answered", 3 in _by_id, True)

_listed = ((_by_id.get(2) or {}).get("result") or {}).get("tools") or []
check("8b     the live session lists three tools", len(_listed), 3)
check("8b     the live session's tool names",
      sorted(t.get("name") for t in _listed),
      ["lookup_trial", "match_patient", "parse_fhir_bundle"])

# The framing survived the wire, in the description AND in the payload.
check("8b     every listed description carries the framing over the wire",
      all(_SHORT in (t.get("description") or "") for t in _listed), True)

_call_result = (_by_id.get(3) or {}).get("result") or {}
_call_text = json.dumps(_call_result)
check("8b     the tool result carries the framing over the wire",
      _LONG in _call_text, True)

# The import banner went SOMEWHERE -- it must be on stderr, not lost.
check("8b     the import banner is on stderr",
      "[Paths]" in _err, True)
print(f"  [info] stdout: {len(_protocol)} protocol messages, "
      f"{len(_garbage)} other lines; stderr: {len(_err.splitlines())} lines")


# --- 8c: THE CONTROL for 8b ------------------------------------------------
#
# WHAT THIS CONTROL USED TO BE, AND WHY IT HAD TO CHANGE. It ran the same
# server with the import guard bypassed and required stdout to be CORRUPTED,
# on the strength of a real defect: oncotriage/paths.py printed
# "[Paths] Settings module loaded from ..." to stdout at module scope, so
# importing the server outside the guard put a non-protocol line on fd 1.
#
# The structured-logging pass fixed that print -- it goes to the console
# channel, which is stderr -- along with every other print in the package and
# the six-line bootstrap in mcp_server.py. Measured after that pass:
#
#     $ python -c "import oncotriage.mcp.server" 1>/dev/null
#     (stdout empty; the [Paths] line is on stderr)
#
# So the old control would now find NO corruption and fire its fail() -- which
# would be reporting the fix as a broken test. Deleting it is worse: 8b would
# then assert that stdout is clean with nothing showing that the guard is what
# makes it clean, and a guard removed entirely would still pass.
#
# THE CONTROL NOW PLANTS ITS OWN SUBJECT, which is strictly stronger than
# depending on a defect that happened to exist. A COPY of the package is made
# in a temp directory with a single stdout write appended to
# oncotriage/__init__.py -- standing in for exactly what the guard is retained
# for: a third-party import banner, or a print reintroduced here tomorrow. Both
# arms run against that copy, so the ONLY difference between them is the guard.
# Nothing in the repository is edited.

_PLANT = "STDOUT-NOISE-PLANTED-BY-8c"
_PLANT_ROOT = tempfile.mkdtemp(prefix="oncotriage-mcp-8c-")
try:
    shutil.copytree(os.path.join(_CODE_DIR, "oncotriage"),
                    os.path.join(_PLANT_ROOT, "oncotriage"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    # mcp_server.py is copied too, and the guarded arm is run as
    # `python <copy>/mcp_server.py`, because sys.path[0] is the SCRIPT's
    # directory and it beats PYTHONPATH. Running the repository's copy would
    # have imported the repository's package and the plant would never load.
    shutil.copy2(os.path.join(_CODE_DIR, "mcp_server.py"),
                 os.path.join(_PLANT_ROOT, "mcp_server.py"))

    _init = os.path.join(_PLANT_ROOT, "oncotriage", "__init__.py")
    with open(_init, "a", encoding="utf-8") as _fh:
        _fh.write("\n\nimport sys as _sys_8c\n"
                  "print(%r, file=_sys_8c.stdout, flush=True)\n" % _PLANT)

    # The plant must actually reach stdout on a plain import, or both arms
    # below would be clean and the control would prove nothing about the guard.
    # cwd=_PLANT_ROOT IS LOAD-BEARING AND THE FIRST VERSION GOT IT WRONG.
    # `python -c` puts the working directory at sys.path[0], AHEAD of
    # PYTHONPATH -- so with cwd left at the repository both arms imported the
    # REAL package, the plant never loaded, and the control reported "no
    # corruption" as though the guard had done it. Running from the plant root
    # is what makes `import oncotriage` find the copy. Path resolution is
    # unaffected: oncotriage/paths.py resolves from ONCOTRIAGE_MAIN_PATH or the
    # settings fallback, both absolute.
    _probe = subprocess.run(
        [sys.executable, "-c", "import oncotriage"],
        capture_output=True, text=True, cwd=_PLANT_ROOT, timeout=120,
        env={**os.environ,
             "PYTHONPATH": os.pathsep.join([_PLANT_ROOT, _CODE_DIR,
                                            os.environ.get("PYTHONPATH", "")])})
    check("8c     the plant reaches stdout on a plain import (non-degeneracy)",
          _PLANT in _probe.stdout, True)

    print("  running the CONTROL: the planted package, import guard BYPASSED")
    _ctl_out, _ctl_err, _ctl_rc = _run_server(
        ["-c", "from oncotriage.mcp.server import main; main()"], _FRAMES,
        path_prefix=_PLANT_ROOT, cwd=_PLANT_ROOT)

    print("  running the same planted package THROUGH the entry point's guard")
    _grd_out, _grd_err, _grd_rc = _run_server(
        [os.path.join(_PLANT_ROOT, "mcp_server.py")], _FRAMES,
        path_prefix=_PLANT_ROOT, cwd=_PLANT_ROOT)

    _ctl_protocol, _ctl_garbage = _classify_stdout(_ctl_out)
    _grd_protocol, _grd_garbage = _classify_stdout(_grd_out)

    check("8c     CONTROL: the control session also answered (it is a real server)",
          len(_ctl_protocol) >= 3, True)
    check("8c     the guarded planted session also answered",
          len(_grd_protocol) >= 3, True)

    # The plant landed on stdout without the guard...
    check("8c     CONTROL: the plant IS on stdout when the import guard is bypassed",
          any(_PLANT in line for line in _ctl_garbage), True)
    # ...and did not with it. This is the pair; either half alone proves nothing.
    check("8c     the plant is NOT on stdout when the guard is in place",
          any(_PLANT in line for line in _grd_garbage), False)
    check("8c     the guarded planted run put the plant on stderr instead",
          _PLANT in _grd_err, True)
    check("8c     the guarded planted run's stdout carries ONLY protocol",
          len(_grd_garbage), 0)
    check("8c     the two runs differ in exactly the way claimed",
          (len(_grd_garbage), len(_ctl_garbage) > 0), (0, True))
finally:
    shutil.rmtree(_PLANT_ROOT, ignore_errors=True)

# The repository's own entry point (8b, above) is clean for a second, stronger
# reason as of the structured-logging pass: nothing in the package writes to
# stdout at all. That is asserted directly rather than inferred from 8b, since
# 8b would also pass if the guard alone were doing the work.
_no_stdout = subprocess.run(
    [sys.executable, "-c", "import oncotriage.mcp.server"],
    capture_output=True, text=True, cwd=_CODE_DIR, timeout=300,
    env={**os.environ, "PYTHONPATH": _CODE_DIR + os.pathsep
         + os.environ.get("PYTHONPATH", ""), "PYTHONUNBUFFERED": "1"})
check("8c     importing the real server writes NOTHING to stdout",
      _no_stdout.stdout, "")
check("8c     ...and its [Paths] banner went to stderr",
      "[Paths]" in _no_stdout.stderr, True)


#------------------------------------------------------------------------------


print()
print("=" * 70)
print("SECTION 9 -- a REAL MCP client, over a real stdio subprocess")
print("=" * 70)
#
# Section 8b speaks the protocol by hand, which proves the bytes on the wire are
# well-formed but not that a client LIBRARY can drive this server: a hand-rolled
# handshake only exercises the parts the author remembered. This section uses
# the SDK's own `Client` against `python -m oncotriage.mcp` as a subprocess --
# the same command a client config block names -- so initialization, capability
# negotiation, schema transfer and result decoding are all done by code this
# file did not write.


async def _drive_real_client():
    """Connect, list, call one tool, and hand back what the client decoded."""
    from mcp import Client, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["mcp_server.py"],
        cwd=_CODE_DIR,
        env={**os.environ, "PYTHONPATH": _CODE_DIR, "PYTHONUNBUFFERED": "1"},
    )

    async with Client(stdio_client(params)) as client:
        listed = (await client.list_tools()).tools
        called = await client.call_tool(
            "lookup_trial", {"nct_id": "NCT00000001"})
        return {
            "server_name": getattr(client.server_info, "name", None),
            "server_version": getattr(client.server_info, "version", None),
            "instructions": client.instructions or "",
            "tool_names": sorted(t.name for t in listed),
            "descriptions": {t.name: (t.description or "") for t in listed},
            "schemas": {t.name: (t.input_schema or {}) for t in listed},
            "is_error": bool(getattr(called, "is_error", False)),
            "structured": called.structured_content or {},
        }


_client_type, _client_message = None, ""
_live = None
try:
    _live = asyncio.run(_drive_real_client())
except Exception as exc:                # noqa: BLE001 -- the type is the answer
    _client_type, _client_message = type(exc).__name__, str(exc)

if _live is None:
    fail("9  a real MCP client connected",
         f"the SDK client could not drive the server: "
         f"{_client_type}: {_client_message[:300]}")
else:
    check("9  a real MCP client connected and read the server identity",
          _live["server_name"], "oncotriage")
    check("9  the client saw the package version",
          _live["server_version"], _server.__version__)
    check("9  the client received three tools",
          _live["tool_names"],
          ["lookup_trial", "match_patient", "parse_fhir_bundle"])
    check("9  the client received the long framing in the instructions",
          _LONG in _live["instructions"], True)
    check("9  every description reached the client with the framing",
          all(_SHORT in d for d in _live["descriptions"].values()), True)
    # The schema survived the wire with the real parameter names -- the
    # regression section 2 exists for, re-checked where a client would see it.
    check("9  the schemas reached the client with the real parameter names",
          {n: sorted((s.get("properties") or {}))
           for n, s in _live["schemas"].items()},
          {"parse_fhir_bundle": ["bundle_path"],
           "match_patient": ["bundle_path"],
           "lookup_trial": ["nct_id"]})
    check("9  the tool call was not an error", _live["is_error"], False)
    check("9  the decoded result carries the framing",
          _live["structured"].get("not_for_clinical_use"), _LONG)
    check("9  the decoded result is the not_found verdict",
          _live["structured"].get("status"), "not_found")
    print(f"  [info] server_info: {_live['server_name']} "
          f"{_live['server_version']}; decoded keys: "
          f"{sorted(_live['structured'])}")


print()
print("=" * 70)

# --- RELEASE THE PROVIDER PIN, ABOVE THE SUMMARY ---------------------------
#
# ABOVE, NOT BELOW: a release under the results line still decides the exit
# code while being absent from the number the summary printed -- a run that
# reports "0 failed" and exits non-zero. The default-flip pass shipped exactly
# that in three of seven files, which is why the release is one function with
# one caller-visible answer rather than four hand-written lines here.
#
# THE OUTCOME IS RECORDED BEFORE THE RESTORE, so "there was a pin to release"
# cannot be satisfied by a process that never installed one.
_PIN_WHO, _PIN_PREVIOUS, _PIN_RESTORED = _provider_pin.release_openai_arm()
check("[provider pin] the OpenAI pin this file installed was released, and "
      "config.MATCHING_PROVIDER is back to the shipped provider",
      (_PIN_WHO == os.path.basename(__file__), _PIN_PREVIOUS, _PIN_RESTORED,
       _provider_pin.pin_state()),
      (True, _PROVIDER_BEFORE_PIN, True, (None, None)))

print("SUMMARY")
print("=" * 70)
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
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
