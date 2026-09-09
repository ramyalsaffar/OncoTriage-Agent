"""The judge-independence guard, and the OpenAI machinery the port added.

WHAT THIS COVERS, AND WHY IT IS ONE FILE RATHER THAN TWO. The guard and the
port are the same event: both judge surfaces moved to `gpt-5.6-terra` on OpenAI
BECAUSE the classifier had moved to Claude, and the guard exists so that pairing
cannot silently become circular again. Splitting them would put the mechanism in
one file and the reason in another.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE PROVIDER OF ANY KIND. Every response is
a literal, every client is a stand-in, and section 9 ASSERTS that rather than
claiming it: `openai.OpenAI` is replaced by a constructor that raises for the
duration of the batch drives, so a code path that reached for a real client
fails here instead of billing. No model is loaded
(ONCOTRIAGE_DEFER_LOCAL_MODELS is set above the imports and torch/transformers
are asserted absent at the end). No corpus, no database, no git history, no live
server.

IT WRITES ONLY INSIDE A `tempfile.mkdtemp` IT REMOVES AND ASSERTS GONE, so it is
NOT in the collision matrix -- but it DOES read `oncotriage/config.py`, which
`tests/test_config_snapshot_date_rot.py` rewrites in place, so the three
repository files it reads are sha256-compared at the end and an interleaved
serial run is visible rather than silent.

IT EXECS NOTHING and loads no module by location. Every control is a different
INPUT to a pure function, an injected `environ`/`classifier` argument, or a
module attribute rebound inside `try`/`finally` with the restore asserted BY
IDENTITY -- which is the natural instrument here, because the two things under
test are a pure classifier and a request builder.

**THE CLASSIFIER IS INJECTED RATHER THAN THE PROVIDER FLIPPED.** `assess`,
`require_independent_judge` and `assert_import_time_independence` all take a
`classifier=` argument for exactly this reason: driving the same-family arm by
setting `config.MATCHING_PROVIDER` would leave every check after it running
under a configuration it did not ask for, which is the process-global leak
`tests/_provider_pin.py` was written to stop somebody repeating by hand.
"""

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import types

os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_CODE = os.path.dirname(_HERE)
if _CODE not in sys.path:
    sys.path.insert(0, _CODE)

from oncotriage import config, spend                             # noqa: E402
from oncotriage.evaluation import judge_independence as J        # noqa: E402
from oncotriage.evaluation import rater as R                     # noqa: E402
from oncotriage.evaluation import ragas_harness as G             # noqa: E402


_PASSED = []
_FAILED = []


def check(label, actual, expected=True):
    ok = actual == expected
    (_PASSED if ok else _FAILED).append(label)
    print(("  PASS  " if ok else "  FAIL  ") + label)
    if not ok:
        print(f"          expected: {expected!r}")
        print(f"          actual:   {actual!r}")


def section(title):
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def raised(fn, *a, **kw):
    """The exception TYPE NAME, or None. Never lets a raise end the run.

    Every driver in this file goes through it. A bare call inside a `check()`
    argument list raises while the argument is being evaluated, which ends the
    file with one traceback where it owed a summary -- the abort shape this
    project has shipped repeatedly and which its own notes count.
    """
    try:
        fn(*a, **kw)
    except BaseException as exc:                                # noqa: BLE001
        return type(exc).__name__
    return None


def value_or_marker(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except BaseException as exc:                                # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


def sha256_file(path):
    with io.open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


_WATCHED = {p: sha256_file(os.path.join(_CODE, p)) for p in (
    "oncotriage/config.py",
    "oncotriage/evaluation/rater.py",
    "oncotriage/evaluation/judge_independence.py",
)}
_TMP = tempfile.mkdtemp(prefix="judge_independence_")


# ===========================================================================
section("SECTION 1 -- families are compared, not model strings")
# ===========================================================================

# THE CASE THE WHOLE GUARD TURNS ON. A Claude served by Amazon Bedrock carries
# a routing prefix and a vendor segment; compare the STRINGS and it is a
# different model from `claude-sonnet-4-6`, which is true and irrelevant. It is
# the same weights, the same training data and the same failure modes, so it is
# the same family -- and the shipped classifier IS that id.
for _model, _want in (
        ("gpt-5.6-terra", J.FAMILY_OPENAI),
        ("gpt-4o-2024-08-06", J.FAMILY_OPENAI),
        ("o3-mini", J.FAMILY_OPENAI),
        ("chatgpt-4o-latest", J.FAMILY_OPENAI),
        ("text-embedding-3-small", J.FAMILY_OPENAI),
        ("us.openai.gpt-5.6-terra", J.FAMILY_OPENAI),
        ("global.openai.gpt-5.6-terra", J.FAMILY_OPENAI),
        ("openai.gpt-5.6-terra", J.FAMILY_OPENAI),
        ("claude-sonnet-4-6", J.FAMILY_ANTHROPIC),
        ("us.anthropic.claude-sonnet-4-6", J.FAMILY_ANTHROPIC),
        ("anthropic.claude-opus-4-8", J.FAMILY_ANTHROPIC),
        ("gemini-2.5-pro", J.FAMILY_GOOGLE),
        ("meta.llama3-70b", J.FAMILY_META),
        ("mistral-large-2411", J.FAMILY_MISTRAL),
        ("command-r-plus", J.FAMILY_COHERE),
        ("some-model-nobody-has-heard-of", J.FAMILY_UNKNOWN),
        ("", J.FAMILY_UNKNOWN),
        (None, J.FAMILY_UNKNOWN)):
    check(f"1a  family_of({_model!r}) is {_want}", J.family_of(_model), _want)

check("1b  *** the two ids for ONE set of Claude weights resolve to ONE "
      "family -- the string comparison a naive guard would make calls them "
      "different and passes the defect straight through ***",
      J.family_of("claude-sonnet-4-6")
      == J.family_of("us.anthropic.claude-sonnet-4-6"), True)
check("1b  CONTROL: the two strings really do differ, so 1b is not comparing "
      "one value with itself",
      "claude-sonnet-4-6" != "us.anthropic.claude-sonnet-4-6", True)
check("1c  ...and the same holds across the OpenAI arm's three wire ids",
      len({J.family_of(m) for m in ("gpt-5.6-terra",
                                    "us.openai.gpt-5.6-terra",
                                    "global.openai.gpt-5.6-terra")}), 1)

# UNKNOWN IS A VALUE AND NOT A GAP. An id this guard cannot classify is an
# unanswered question, and an unanswered question is not evidence of
# independence.
check("1d  an unrecognisable judge is NOT reported independent",
      J.assess("some-model-nobody-has-heard-of",
               classifier="claude-sonnet-4-6")["verdict"],
      J.VERDICT_UNKNOWN_FAMILY)
check("1d  ...and neither is an unrecognisable CLASSIFIER, which is the half "
      "a guard written from the judge's side would miss",
      J.assess("gpt-5.6-terra", classifier="mystery-model-9000")["verdict"],
      J.VERDICT_UNKNOWN_FAMILY)
check("1e  the verdict vocabulary is closed and has three members, because "
      "'same family' and 'cannot tell' have different remedies",
      sorted(J.VERDICTS),
      sorted([J.VERDICT_INDEPENDENT, J.VERDICT_SAME_FAMILY,
              J.VERDICT_UNKNOWN_FAMILY]))


# ===========================================================================
section("SECTION 2 -- the effective classifier, and the shipped pairing")
# ===========================================================================

check("2a  the classifier under audit is what Stage 5 SENDS, read through "
      "the one function that answers that",
      J.classifier_model(), config.matching_wire_model())
check("2a  ...which is NOT config.MATCHING_MODEL at the shipped provider -- "
      "the confusion that let a Claude-judging-Claude configuration ship "
      "under a docstring saying 'different family'",
      J.classifier_model() != config.MATCHING_MODEL, True)
check("2b  the shipped pairing is INDEPENDENT: an OpenAI judge against an "
      "Anthropic classifier",
      J.assess(R.DEFAULT_MODEL)["verdict"], J.VERDICT_INDEPENDENT)
check("2b  ...and both judge surfaces ship the same judge, so a reader "
      "comparing a rater run with a ragas run is comparing one model",
      R.DEFAULT_MODEL, G.DEFAULT_JUDGE_MODEL)

# *** THE NON-DEGENERACY PROBE. *** Every check above would also pass against a
# guard that answered "independent" to everything, so the same-family arm is
# driven explicitly -- with an INJECTED classifier, never a provider flip.
check("2c  *** the guard REFUSES the configuration that shipped: a Claude "
      "judge against the Claude classifier ***",
      raised(J.require_independent_judge, "claude-sonnet-4-6", "probe",
             classifier="us.anthropic.claude-sonnet-4-6", environ={}),
      "JudgeIndependenceError")
check("2c  ...and it refuses the mirror image too -- an OpenAI judge against "
      "an OpenAI classifier, which is what a flip back to "
      "MATCHING_PROVIDER='openai' would produce",
      raised(J.require_independent_judge, "gpt-5.6-terra", "probe",
             classifier="gpt-5.6-terra", environ={}),
      "JudgeIndependenceError")
check("2c  CONTROL: the cross-family pair does NOT refuse, so 2c is about the "
      "pairing rather than a guard that refuses everything",
      raised(J.require_independent_judge, "gpt-5.6-terra", "probe",
             classifier="us.anthropic.claude-sonnet-4-6", environ={}),
      None)
check("2d  an unnameable family refuses too: not proven is not proven safe",
      raised(J.require_independent_judge, "mystery-9000", "probe",
             classifier="us.anthropic.claude-sonnet-4-6", environ={}),
      "JudgeIndependenceError")

_msg = value_or_marker(J.require_independent_judge, "claude-opus-4-8", "probe",
                       classifier="us.anthropic.claude-sonnet-4-6",
                       environ={})
check("2e  the refusal names BOTH models, the shared family, and what the "
      "number would actually be measuring",
      all(t in str(_msg) for t in ("claude-opus-4-8", "claude-sonnet-4-6",
                                   "anthropic", "family agreement")), True)
check("2e  ...and names the override, so an operator who means it is not left "
      "guessing at a variable",
      J.ENV_ALLOW_SAME_FAMILY_JUDGE in str(_msg), True)


# ===========================================================================
section("SECTION 3 -- the override is explicit, closed, and recorded")
# ===========================================================================

check("3a  the source vocabulary is closed, and every source this function "
      "can produce is in it -- the field reaches a manifest, so a member it "
      "does not declare is a value no reader knows about",
      sorted(J.OVERRIDE_SOURCES),
      sorted([J.OVERRIDE_SOURCE_UNSET, J.OVERRIDE_SOURCE_ENV]))
check("3a  ...and both members are actually reachable, so the tuple is not "
      "half declaration and half aspiration",
      sorted({J.resolve_same_family_override(environ=e)[1]
              for e in ({}, {J.ENV_ALLOW_SAME_FAMILY_JUDGE: "1"})}),
      sorted(J.OVERRIDE_SOURCES))
check("3a  unset means forbidden",
      J.resolve_same_family_override(environ={}),
      (False, J.OVERRIDE_SOURCE_UNSET, None))
for _raw in J.OVERRIDE_TRUE:
    check(f"3a  {_raw!r} permits",
          J.resolve_same_family_override(
              environ={J.ENV_ALLOW_SAME_FAMILY_JUDGE: _raw})[0], True)
for _raw in J.OVERRIDE_FALSE:
    check(f"3a  {_raw!r} forbids",
          J.resolve_same_family_override(
              environ={J.ENV_ALLOW_SAME_FAMILY_JUDGE: _raw})[0], False)

# *** A TYPO MUST NOT READ AS 'OFF'. *** It is the safe direction, and it is
# still a guess -- and the thing being guessed at is whether a circular
# measurement may be published.
check("3b  *** an unrecognised value RAISES rather than being read as off ***",
      raised(J.resolve_same_family_override,
             environ={J.ENV_ALLOW_SAME_FAMILY_JUDGE: "flase"}),
      "JudgeIndependenceError")

_rec = value_or_marker(J.require_independent_judge, "claude-sonnet-4-6",
                       "probe", classifier="us.anthropic.claude-sonnet-4-6",
                       environ={J.ENV_ALLOW_SAME_FAMILY_JUDGE: "yes"})
check("3c  *** with the override set, a same-family pair PROCEEDS ***",
      isinstance(_rec, dict) and _rec["verdict"], J.VERDICT_SAME_FAMILY)
check("3c  ...and the record says the override was applied, so an artifact "
      "cannot show a clean verdict for a run only the switch permitted",
      isinstance(_rec, dict) and _rec.get("override_applied"), True)
check("3c  ...and it carries the variable name and the literal that was set, "
      "so the artifact records the operator's own words",
      isinstance(_rec, dict)
      and (_rec["independence_override"]["variable"],
           _rec["independence_override"]["value_as_given"]),
      (J.ENV_ALLOW_SAME_FAMILY_JUDGE, "yes"))
check("3c  ...and the whole record serialises, because it goes in a manifest",
      isinstance(_rec, dict)
      and json.loads(json.dumps(_rec)) == _rec, True)

_clean = value_or_marker(J.require_independent_judge, "gpt-5.6-terra", "probe",
                         classifier="us.anthropic.claude-sonnet-4-6",
                         environ={})
check("3d  CONTROL: an INDEPENDENT run records override_applied as absent "
      "rather than false-and-applied, so the two are distinguishable",
      isinstance(_clean, dict) and _clean.get("override_applied"), None)
check("3d  ...and still records that the switch was unset, so a reader is "
      "not left inferring it",
      isinstance(_clean, dict)
      and _clean["independence_override"]["source"], J.OVERRIDE_SOURCE_UNSET)


# ===========================================================================
section("SECTION 4 -- layer 1 is at import, and layer 2 after the flag")
# ===========================================================================

check("4a  layer 1 raises on a same-family DEFAULT",
      raised(J.assert_import_time_independence, "claude-sonnet-4-6", "mod",
             classifier="us.anthropic.claude-sonnet-4-6", environ={}),
      "JudgeIndependenceError")
check("4a  CONTROL: it returns on the shipped default",
      raised(J.assert_import_time_independence, R.DEFAULT_MODEL, "mod",
             classifier="us.anthropic.claude-sonnet-4-6", environ={}),
      None)
check("4a  ...and the override lifts it, so an operator can still import the "
      "module they mean to run",
      raised(J.assert_import_time_independence, "claude-sonnet-4-6", "mod",
             classifier="us.anthropic.claude-sonnet-4-6",
             environ={J.ENV_ALLOW_SAME_FAMILY_JUDGE: "1"}),
      None)
_l1 = value_or_marker(J.assert_import_time_independence, "claude-sonnet-4-6",
                      "oncotriage/evaluation/rater.py::DEFAULT_MODEL",
                      classifier="us.anthropic.claude-sonnet-4-6", environ={})
check("4a  the layer-1 message says the DEFAULT is wrong rather than that "
      "this run is -- an operator who typed nothing must not be sent to a "
      "flag they did not use",
      "SHIPPED DEFAULT" in str(_l1)
      and "DEFAULT_MODEL" in str(_l1), True)

# *** BOTH LAYERS ARE LOAD-BEARING, AND NEITHER SUBSUMES THE OTHER. ***
check("4b  *** layer 1 CANNOT see a judge named on the command line: it is "
      "given the module default and knows nothing of --model ***",
      raised(J.assert_import_time_independence, R.DEFAULT_MODEL, "mod",
             classifier="us.anthropic.claude-sonnet-4-6", environ={}),
      None)
check("4b  ...which is exactly the case layer 2 catches, on the SAME "
      "classifier -- so a `--model claude-sonnet-4-6` is refused although "
      "the default was fine",
      raised(J.require_independent_judge, "claude-sonnet-4-6", "prepare",
             classifier="us.anthropic.claude-sonnet-4-6", environ={}),
      "JudgeIndependenceError")

# Both judge modules call layer 1 at MODULE SCOPE. Asserted by AST rather than
# by reading, and scoped to the module body so a call nested in a function
# would not satisfy it.
import ast                                                       # noqa: E402


def _module_scope_guard_calls(path):
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    found = []
    for node in tree.body:
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call)
                    and "assert_import_time_independence"
                    in ast.unparse(sub.func)):
                found.append(ast.unparse(sub.func))
    return found


for _mod in ("oncotriage/evaluation/rater.py",
             "oncotriage/evaluation/ragas_harness.py"):
    check(f"4c  {_mod} calls layer 1 at MODULE SCOPE, so a same-family "
          f"configuration cannot even be imported",
          len(_module_scope_guard_calls(os.path.join(_CODE, _mod))), 1)
check("4c  non-degeneracy: the walk finds nothing in a module that has no "
      "such call, so 4c is not reporting a constant",
      _module_scope_guard_calls(
          os.path.join(_CODE, "oncotriage/evaluation/judge_independence.py")),
      [])


# ===========================================================================
section("SECTION 5 -- the pipeline is untouched by any of this")
# ===========================================================================

# THE REQUIREMENT IS THAT CLASSIFIER-ONLY USE IS UNAFFECTED, and the strongest
# form of it is structural: nothing in the pipeline's import graph reaches this
# module at all, so there is no configuration of it that can stop a batch run.
# *** SCANNED BY AST AND NOT BY SUBSTRING, AND THE FIRST VERSION OF THIS BLOCK
# *** WAS THE SUBSTRING VERSION AND REPORTED A FALSE POSITIVE ON THE FIRST RUN.
# `oncotriage/config.py` NAMES this module in the comment beside the OpenAI
# pricing rows -- prose explaining why the judge moved -- and a `"..." in text`
# scan read that argument as an import. It is this project's own recurring
# lesson ("a file that argues about its own settings cannot be grepped for
# them"), met again in the file written to check an import graph.
def _imports_guard(path):
    """Does this module IMPORT the guard, at any depth? Prose does not count."""
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if "judge_independence" in ((node.module or "")
                                        + " "
                                        + " ".join(a.name for a in
                                                   node.names)):
                return True
        elif isinstance(node, ast.Import):
            if any("judge_independence" in a.name for a in node.names):
                return True
    return False


_PIPELINE = ("oncotriage/agent/evaluation.py", "oncotriage/agent/graph.py",
             "oncotriage/batch/runner.py", "oncotriage/api/server.py",
             "oncotriage/mcp/server.py", "oncotriage/config.py",
             "oncotriage/spend.py")
for _mod in _PIPELINE:
    check(f"5a  {_mod} does not import the guard, so no configuration of it "
          f"can stop a classifier-only run",
          _imports_guard(os.path.join(_CODE, _mod)), False)
check("5a  non-degeneracy: the two modules that DO import it are found by the "
      "same walk, so 5a is not a scan that matches nothing",
      sorted(m for m in ("oncotriage/evaluation/rater.py",
                         "oncotriage/evaluation/ragas_harness.py")
             if _imports_guard(os.path.join(_CODE, m))),
      ["oncotriage/evaluation/ragas_harness.py",
       "oncotriage/evaluation/rater.py"])
check("5a  ...and config.py, which NAMES the guard in prose, is correctly "
      "reported as not importing it -- which a substring scan gets wrong",
      "judge_independence" in io.open(
          os.path.join(_CODE, "oncotriage/config.py"),
          encoding="utf-8").read(), True)

# *** THE FIRST VERSION OF THIS CHECK ASSERTED THE OPPOSITE, AND WAS WRONG. ***
# It required the guard to import `config` from INSIDE `classifier_model()`, on
# the reasoning that importing the guard should then resolve nothing. That is a
# violation of this project's standing rule -- no package module may import
# another from a function body, because a deferred import is a dependency no
# scan of an import block can see -- and
# `tests/test_package_invariants.py` check 1b caught it. The rule wins, the
# import is hoisted, and this check is inverted rather than deleted, because
# the property still worth pinning is that the guard's project dependency is
# EXACTLY ONE MODULE and it is `config`.
_guard_tree = ast.parse(io.open(
    os.path.join(_CODE, "oncotriage/evaluation/judge_independence.py"),
    encoding="utf-8").read())
_guard_project_imports = sorted({
    (n.module or "") + "." + a.name
    for n in ast.walk(_guard_tree) if isinstance(n, ast.ImportFrom)
    for a in n.names
    if (n.module or "").startswith("oncotriage")})
check("5b  the guard's ONLY project dependency is config -- the module that "
      "answers what Stage 5 sends, and nothing else",
      _guard_project_imports, ["oncotriage.config"])
check("5b  ...and it is at MODULE SCOPE, where a scan of the import block can "
      "see it, rather than deferred into a function body",
      any(isinstance(n, ast.ImportFrom)
          and (n.module or "").startswith("oncotriage")
          for n in _guard_tree.body), True)


# ===========================================================================
section("SECTION 6 -- OpenAI usage semantics are translated, not renamed")
# ===========================================================================

# *** THE TRAP. *** Anthropic reports DISJOINT counts; OpenAI's prompt_tokens
# INCLUDES its cached part. A rename prices the cached tokens twice.
_U = {"prompt_tokens": 3000, "completion_tokens": 250,
      "prompt_tokens_details": {"cached_tokens": 2560},
      "completion_tokens_details": {"reasoning_tokens": 180}}
_T = R.translate_openai_usage(_U)
check("6a  *** uncached input is prompt_tokens MINUS cached_tokens ***",
      _T["input_tokens"], 440)
check("6a  ...and the cached part is carried separately, at its own rate",
      _T["cache_read_input_tokens"], 2560)
check("6a  ...and the two add back to what the vendor reported, which is the "
      "reconciliation a rename would break",
      _T["input_tokens"] + _T["cache_read_input_tokens"],
      _U["prompt_tokens"])
check("6a  CONTROL: the WRONG reading -- taking prompt_tokens as uncached "
      "input, which is what a rename does -- gives a different, higher price",
      R.price_usage("gpt-5.6-terra",
                    {"input_tokens": 3000, "output_tokens": 250,
                     "cache_read_input_tokens": 2560})
      > R.price_usage("gpt-5.6-terra", _T), True)
check("6b  reasoning tokens are recorded but NOT priced separately: they are "
      "inside completion_tokens and a second term would double-charge",
      (_T["reasoning_tokens"],
       R.price_usage("gpt-5.6-terra", _T)
       == R.price_usage("gpt-5.6-terra",
                        dict(_T, reasoning_tokens=99999))),
      (180, True))

# *** ABSENT AND ZERO ARE DIFFERENT FACTS. *** GPT-5.6 introduced a cache-write
# charge and the installed SDK's usage model declares no field for it, so a
# report of $0 of write cost has to say which of the two it means.
check("6c  *** a usage block with no write field reports the write as "
      "UNREPORTED rather than as a measured zero ***",
      (_T["cache_write_tokens"], _T["cache_write_reported"]), (0, False))
_W = R.translate_openai_usage(
    dict(_U, prompt_tokens_details={"cached_tokens": 100,
                                    "cache_creation_tokens": 900}))
check("6c  ...and a block that DOES carry one is reported as measured",
      (_W["cache_write_tokens"], _W["cache_write_reported"]), (900, True))
check("6c  ...and the write is priced at 1.25x input, the rate GPT-5.6 "
      "introduced, at batch",
      round(R.price_usage("gpt-5.6-terra", {"cache_write_tokens": 1_000_000}),
            6),
      round(2.00 * 1.25 * 0.50, 6))

# A cached count larger than the prompt it is a breakdown OF cannot be priced.
# Clamped rather than allowed negative: a negative input class silently REFUNDS
# money in the total.
_BAD = R.translate_openai_usage(
    {"prompt_tokens": 100, "completion_tokens": 5,
     "prompt_tokens_details": {"cached_tokens": 900}})
check("6d  an impossible cached count is clamped to zero, never negative",
      _BAD["input_tokens"], 0)
check("6d  ...and the discrepancy is recorded rather than absorbed",
      _BAD["prompt_reconcile_mismatch_tokens"], 800)

check("6e  a usage block that is a pydantic-style OBJECT reads identically to "
      "the dict form -- the batch path parses JSON and the probe gets models",
      R.translate_openai_usage(types.SimpleNamespace(
          prompt_tokens=3000, completion_tokens=250,
          prompt_tokens_details=types.SimpleNamespace(cached_tokens=2560),
          completion_tokens_details=types.SimpleNamespace(
              reasoning_tokens=180))),
      _T)

_tot = R._usage_totals()
R._accumulate_usage(_tot, None)
check("6f  a response with NO usage block is counted, not treated as free",
      (_tot["usage_absent"], _tot["responses"]), (1, 1))


# ===========================================================================
section("SECTION 7 -- the reservation, which is what a batch cap needs")
# ===========================================================================

def _req(chars=1000, cid="x"):
    return {"custom_id": cid,
            "params": {"model": "gpt-5.6-terra",
                       "max_completion_tokens": 4096,
                       "messages": [
                           {"role": "system", "content": "s" * chars},
                           {"role": "user", "content": [
                               {"type": "text", "text": "p" * chars},
                               {"type": "text", "text": "d" * chars}]}]}}


_rates = R.rater_pricing("gpt-5.6-terra")
check("7a  *** the worst-case input rate is the CACHE WRITE rate, not the "
      "uncached one -- 'assume no cache hits' under-reserves by 25% of every "
      "token the provider chose to cache ***",
      R.worst_case_input_rate(_rates), _rates["cache_write"])
check("7a  non-degeneracy: the write rate really is the dearest of the three, "
      "so 7a is not naming whichever key came first",
      _rates["cache_write"] > _rates["input"] > _rates["cache_read"], True)
check("7a  CONTROL: on a row with no write premium it collapses to the "
      "classical 'no cache hits' reading",
      R.worst_case_input_rate({"input": 3.0, "cache_read": 0.3}), 3.0)

_res = R.reserve_batch_liability("gpt-5.6-terra", [_req()] * 10, 4096, 4.0)
check("7b  output is reserved at the FULL ceiling, because reasoning bills as "
      "output and no smaller figure is a bound",
      _res["output_tokens_assumed"], 10 * 4096)
check("7b  ...and the arithmetic is the two terms and nothing else",
      round(_res["reserved_usd"], 10),
      round(_res["input_tokens_assumed"] * _rates["cache_write"]
            + _res["output_tokens_assumed"] * _rates["output"], 10))

check("7c  ASSUMED_OUTPUT_TOKENS is None: the 110 measured on a "
      "NON-reasoning model is not a smaller figure, it is the wrong quantity",
      R.ASSUMED_OUTPUT_TOKENS, None)


# ===========================================================================
section("SECTION 8 -- the strict response schema")
# ===========================================================================

for _mode, _keys in ((R.MODE_ANCHORED, R.RATING_KEYS),
                     (R.MODE_BLIND, R.BLIND_RATING_KEYS)):
    _fmt = R.build_response_format(_mode)
    _schema = _fmt["json_schema"]["schema"]
    check(f"8a  {_mode}: the schema is strict and closed",
          (_fmt["json_schema"]["strict"],
           _schema["additionalProperties"]), (True, False))
    check(f"8a  {_mode}: every contract key is required -- strict mode has no "
          f"optional properties, and nullability is a type union instead",
          sorted(_schema["required"]), sorted(_keys))
    check(f"8a  {_mode}: the property set IS the contract's key set",
          sorted(_schema["properties"]), sorted(_keys))

_anch = R.build_response_format(R.MODE_ANCHORED)["json_schema"]["schema"]
check("8b  the anchored schema admits a NULL corrected_status, which is the "
      "one value an 'agree' may carry -- refusing it would make every "
      "agreement unparseable",
      any(b.get("type") == "null"
          for b in _anch["properties"]["corrected_status"]["anyOf"]), True)
check("8c  *** the status enum is the UNION of both arms, deliberately: "
      "constraining it per arm would delete the wrong-arm measurement "
      "parse_rating already takes ***",
      sorted(R.ALL_STATUSES),
      sorted({s for v in R.ARM_STATUSES.values() for s in v}))
check("8c  ...and it is DERIVED from ARM_STATUSES, so a status added to an "
      "arm cannot be legal at the parser and rejected by the schema",
      set(R.ALL_STATUSES) >= set(R.ARM_STATUSES[R.ARM_EXCLUSION]), True)
check("8d  an unknown mode returns None rather than raising: the caller has "
      "already refused it by then",
      R.build_response_format("sideways"), None)


# ===========================================================================
section("SECTION 9 -- the batch mechanics, against a stand-in that BILLS "
        "NOTHING and a real-client constructor that RAISES")
# ===========================================================================

# THE NO-SPEND TRIPWIRE. Every drive below runs with `openai.OpenAI` replaced
# by a constructor that raises, so a path that reached for a real client fails
# here rather than building one against the project's own credentials. Restored
# by identity in the `finally`.
import openai as _openai                                         # noqa: E402

_REAL_OPENAI = _openai.OpenAI


def _no_real_clients(*a, **kw):
    raise AssertionError("a REAL OpenAI client was constructed by a test that "
                         "must not build one")


class _BatchStub:
    """Files in, one JSONL out. Never touches the network."""

    def __init__(self, rows, status="completed", output=True):
        outer = self
        self.rows = rows
        self.uploads = []
        self.created = []
        self.status = status

        class _Files:
            def create(self, file=None, purpose=None, **_kw):
                name, payload = file
                outer.uploads.append((name, purpose, payload))
                return types.SimpleNamespace(id="file_1")

            def content(self, file_id):
                return "".join(json.dumps(r) + "\n" for r in outer.rows)

        class _Batches:
            def create(self, **kw):
                outer.created.append(kw)
                return types.SimpleNamespace(id="batch_1")

            def retrieve(self, bid):
                return types.SimpleNamespace(
                    id=bid, status=outer.status,
                    output_file_id="out" if output else None,
                    error_file_id=None,
                    request_counts=types.SimpleNamespace(
                        total=len(outer.rows), completed=len(outer.rows),
                        failed=0))

        self.files = _Files()
        self.batches = _Batches()


_openai.OpenAI = _no_real_clients
try:
    _reqs = [_req(cid="a"), _req(cid="b")]
    check("9a  a request serialises to the Batch API's own line shape",
          sorted(R.to_batch_line(_reqs[0])),
          ["body", "custom_id", "method", "url"])
    check("9a  ...naming the endpoint the batch itself is created with, from "
          "ONE constant -- the two must agree or the batch is rejected",
          R.to_batch_line(_reqs[0])["url"], R.BATCH_ENDPOINT)
    _jsonl = R.batch_jsonl(_reqs)
    check("9a  the uploaded file is one JSON object per line, newline "
          "terminated",
          (len(_jsonl.splitlines()), _jsonl.endswith("\n")), (2, True))
    check("9a  ...and every line parses back to its own custom_id",
          [json.loads(ln)["custom_id"] for ln in _jsonl.splitlines()],
          ["a", "b"])

    _stub = _BatchStub([])
    _state, _path = {}, os.path.join(_TMP, "state.json")
    _before = spend.SPEND_LEDGER.total
    _ids = value_or_marker(R.submit_batches, _stub, [_reqs], _state, _path,
                           "primary")
    check("9b  submit uploads a FILE and then creates a batch naming it",
          (len(_stub.uploads), _stub.created[0]["input_file_id"]),
          (1, "file_1"))
    check("9b  ...with purpose='batch', which is what the API requires",
          _stub.uploads[0][1], "batch")
    check("9b  ...and the completion window the discount is for",
          _stub.created[0]["completion_window"], R.BATCH_COMPLETION_WINDOW)
    check("9b  the batch id is written to the state file BEFORE polling, so "
          "an interrupted session can resume it",
          json.load(io.open(_path, encoding="utf-8"))["batches"][0]["id"],
          "batch_1")

    # *** THE FREE VISIBILITY CHECK IS CALLED, NOT MERELY DEFINED. ***
    # It was written, documented in `spend.BILLED_SITES`, and left with NO CALL
    # SITE -- a function nobody runs, which is the dead-declaration shape this
    # project deletes. `test_package_invariants` check 2h scans module-level
    # CONSTANTS and cannot see an unused function, so nothing else would have
    # caught it.
    _main_src = io.open(os.path.join(_CODE, "oncotriage/evaluation/rater.py"),
                        encoding="utf-8").read()
    _main = next(n for n in ast.walk(ast.parse(_main_src))
                 if isinstance(n, ast.FunctionDef) and n.name == "main")
    _calls = [ast.unparse(n.func) for n in ast.walk(_main)
              if isinstance(n, ast.Call)]
    check("9c-0 *** main() actually CALLS the free visibility check, so a key "
          "without access to the judge is a refusal before the upload rather "
          "than N identically-errored rows in a batch that was paid for ***",
          _calls.count("model_is_visible"), 1)

    class _Blind:
        class models:
            @staticmethod
            def retrieve(m):
                raise RuntimeError("model_not_found")

    check("9c-0 ...and it answers (False, <why>) rather than raising, so the "
          "caller decides what a missing model means",
          R.model_is_visible(_Blind, "gpt-5.6-terra")[0], False)
    check("9c-0 ...and names what went wrong, so the refusal is actionable",
          "model_not_found" in R.model_is_visible(_Blind, "x")[1], True)

    class _Seeing:
        class models:
            @staticmethod
            def retrieve(m):
                return types.SimpleNamespace(id=m)

    check("9c-0 CONTROL: a key that CAN see the model is not reported blind, "
          "and the echoed id comes back",
          R.model_is_visible(_Seeing, "gpt-5.6-terra"),
          (True, "gpt-5.6-terra"))

    # THE JOIN, AND THE FOUR TERMINAL STATUSES.
    check("9c  the terminal and pending statuses partition the vocabulary, "
          "so an unknown one is a refusal rather than a spin to timeout",
          sorted(R.BATCH_STATUSES),
          sorted(set(R.BATCH_TERMINAL_STATUSES)
                 | set(R.BATCH_PENDING_STATUSES)))
    check("9c  ...and they are disjoint",
          set(R.BATCH_TERMINAL_STATUSES) & set(R.BATCH_PENDING_STATUSES),
          set())
    check("9c  `batch_failed` is a reason of its own -- a batch that never "
          "ran is not every request answering badly",
          "batch_failed" in R.UNRATED_REASONS, True)
    check("9c  ...and it is NOT retryable: an identical resubmission is "
          "rejected identically",
          "batch_failed" in R.RETRYABLE_REASONS, False)

    # *** THE RAW REPLIES ARE PAID EVIDENCE AND ARE NOT OVERWRITABLE. ***
    _p, _how = R.persist_raw_replies(_TMP, "batch_9", "line one\n")
    check("9d  the raw JSONL is written under the batch id", _how, "written")
    check("9d  an identical re-retrieval is a no-op, so --resume stays usable",
          R.persist_raw_replies(_TMP, "batch_9", "line one\n")[1], "unchanged")
    check("9d  *** but DIFFERENT content under one batch id REFUSES rather "
          "than truncating the only untransformed record of what was paid "
          "for ***",
          raised(R.persist_raw_replies, _TMP, "batch_9", "something else\n"),
          "RaterRefusal")
    check("9d  ...and the original bytes are still there afterwards",
          io.open(_p, encoding="utf-8").read(), "line one\n")
finally:
    _openai.OpenAI = _REAL_OPENAI
check("9e  the real client constructor is restored BY IDENTITY, so no check "
      "after this section runs under a stand-in",
      _openai.OpenAI is _REAL_OPENAI, True)


# ===========================================================================
section("SECTION 9b -- the WIRE FACTS, measured on a real paid batch")
# ===========================================================================

# *** THIS IS A REAL RESPONSE BODY, COPIED FROM THE RAW JSONL A PAID PROBE
# *** WROTE ON 2026-09-08 (6 requests, batch
# *** batch_6aa09bc83110819091cc6a2eb15e4299, $0.0596 at batch rates).
#
# It is here because every one of the four capability questions the port had to
# answer is answered by these bytes, and a stub written from the documentation
# would answer them the way the documentation says rather than the way the API
# does. Two of the four came back DIFFERENT from what was assumed:
#
#   * `cache_write_tokens` EXISTS, under that exact name, inside
#     prompt_tokens_details -- which `config.PRICING_CONFIG`'s own note said
#     OpenAI does not bill for. It does, on this model.
#   * `system_fingerprint` is null, so there is no second identity to pin
#     beside the model id -- which is what makes `model` the only answer to
#     "which weights produced this rating".
_WIRE = {
    "id": "chatcmpl-probe", "object": "chat.completion",
    "model": "gpt-5.6-terra", "service_tier": "default",
    "system_fingerprint": None,
    "choices": [{"index": 0, "finish_reason": "stop", "message": {
        "role": "assistant", "refusal": None,
        "content": '{"patient_value_support":"not_needed","status_verdict":'
                   '"agree","corrected_status":null,"rationale":"The record '
                   'contains no explicit finding."}'}}],
    "usage": {"prompt_tokens": 6684, "completion_tokens": 69,
              "total_tokens": 6753,
              "prompt_tokens_details": {"cached_tokens": 3202,
                                        "cache_write_tokens": 2627,
                                        "audio_tokens": 0},
              "completion_tokens_details": {"reasoning_tokens": 0,
                                            "audio_tokens": 0,
                                            "accepted_prediction_tokens": 0,
                                            "rejected_prediction_tokens": 0}},
}
_WT = R.translate_openai_usage(_WIRE["usage"])
check("9b-a *** the cache-write field the live API sends IS found by the "
      "translator -- the SDK's typed usage model does not declare it, so it "
      "arrives as an extra and a getattr-based reader would price it at zero "
      "***",
      (_WT["cache_write_tokens"], _WT["cache_write_reported"]), (2627, True))
check("9b-a ...and it is the FIRST name the translator tries, so a real "
      "response never reaches the speculative fallbacks",
      R.translate_openai_usage(
          {"prompt_tokens": 10, "completion_tokens": 1,
           "prompt_tokens_details": {"cached_tokens": 0,
                                     "cache_write_tokens": 7,
                                     "cache_creation_tokens": 999}}
      )["cache_write_tokens"], 7)
check("9b-b the uncached remainder reconciles against what the vendor "
      "reported, on a REAL response rather than a fabricated one",
      _WT["input_tokens"] + _WT["cache_read_input_tokens"],
      _WIRE["usage"]["prompt_tokens"])
check("9b-c the structured-output contract was honoured: the content parses "
      "as an object carrying exactly the anchored contract's keys",
      sorted(json.loads(_WIRE["choices"][0]["message"]["content"])),
      sorted(R.RATING_KEYS))
check("9b-c ...including an explicit null corrected_status, which strict mode "
      "requires and which the parser accepts",
      json.loads(
          _WIRE["choices"][0]["message"]["content"])["corrected_status"], None)
check("9b-d there is NO system_fingerprint on this model, so `model` read off "
      "the response is the only identity a rating can record -- which is why "
      "`rated_by` exists and why the manifest counts answering ids",
      _WIRE["system_fingerprint"], None)
check("9b-e reasoning tokens are inside completion_tokens: the two details "
      "blocks do not add to the total, they break it down",
      _WIRE["usage"]["prompt_tokens"] + _WIRE["usage"]["completion_tokens"],
      _WIRE["usage"]["total_tokens"])


# ===========================================================================
section("SECTION 10 -- hygiene")
# ===========================================================================

check("10a no model was loaded: this file asserts a judge's configuration, "
      "not a tensor",
      [m for m in ("torch", "transformers") if m in sys.modules], [])
check("10b no boto3 client library was pulled in either",
      "boto3" in sys.modules, False)
for _rel, _want in _WATCHED.items():
    check(f"10c {_rel} is byte-unchanged",
          sha256_file(os.path.join(_CODE, _rel)), _want)
check("10c non-degeneracy: the three watched hashes differ, so 10c is not one "
      "file compared with itself three times",
      len(set(_WATCHED.values())), 3)
shutil.rmtree(_TMP, ignore_errors=True)
check("10d the temp directory is gone", os.path.exists(_TMP), False)


print("\n" + "=" * 74)
print(f"Passed: {len(_PASSED)}")
print(f"Failed: {len(_FAILED)}")
print("=" * 74)
for _f in _FAILED:
    print("  FAILED: " + _f)
sys.exit(1 if _FAILED else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep  8 12:00:00 2026

@author: ramyalsaffar
"""
