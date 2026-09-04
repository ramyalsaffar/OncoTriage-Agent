######################################################################
# The Stage 5 estimator's divisor: declared per arm, and which way it errs
######################################################################

"""Matching Chars Per Token Test

THE RULE THIS FILE HOLDS. The characters-per-token divisor Stage 5's estimators
use is a property of the JUDGE'S TOKENIZER, so it is declared PER ARM in
``config.MATCHING_CHARS_PER_TOKEN_BY_PROVIDER`` and answered for the live arm by
``config.matching_chars_per_token()``. ``config.CHARS_PER_TOKEN`` survives as
the OpenAI-arm and INDEXER value and is unchanged at 4.

WHY IT HAD TO STOP BEING ONE NUMBER, and it is an inequality rather than a
preference. The estimate is ``chars / D`` against a truth of ``chars / R``, so
it OVER-states -- the only direction a budget guard may err in -- exactly when
``D <= R``. Measured 2026-09-03 out of the probe's usage blocks, R is 4.2-4.4 on
gpt-5.6-terra and 3.50-3.87 on us.anthropic.claude-sonnet-4-6, so a single D
cannot satisfy both: 4 over-states by 5-10% on one arm and UNDER-states by up to
12.5% on the other. Section 2 drives that on the recorded samples rather than
restating it.

WHY A FILE OF ITS OWN RATHER THAN ADDITIONS TO THE PACKING SUITE.
``tests/test_agent_stage5_input_packing.py`` PINS THE OpenAI ARM -- correctly,
its subject is that dormant request shape -- so it can measure the divisor of
one arm and is blind by construction to the thing most worth failing on: the
OWNER answering inconsistently ACROSS arms, and the shipped arm's divisor being
raised toward its mean. That question has no home in a per-arm file.

WHAT IS DRIVEN RATHER THAN READ. Every divisor answer is taken from the live
owner with ``config.MATCHING_PROVIDER`` really set, inside try/finally with the
restore asserted. The estimators are called for real. The indexer's batch
arithmetic is driven on a fixed sample and compared against the pre-pass
formula. The exactness claim that makes the OpenAI arm's numbers provably
unchanged is driven EXHAUSTIVELY over a range rather than argued.

NO NETWORK, NO KEYS, **NO SPEND** -- no provider client of any kind is built and
no request is issued; ``deps.is_resolved`` is asserted False for all three
client keys at the end, so a real client that HAD been built is caught. NO MODEL
LOAD (``ONCOTRIAGE_DEFER_LOCAL_MODELS`` above the imports; ``torch`` and
``transformers`` asserted absent at the end), no live Qdrant, no corpus, no
database, no git history, no live server. It writes NOTHING anywhere, not even a
temp directory.

NOT in ``tests/run_serial_tests.py``'s collision matrix, derived rather than
assumed: it writes no file, and the three repository files it READS are compared
by sha256 at the end -- one of them IS ``oncotriage/config.py``, which
``tests/test_config_snapshot_date_rot.py`` rewrites in place, so an interleaved
serial run is visible here rather than silent. It EXECS NOTHING and loads no
module by location: every control is a different INPUT to a pure function, or a
module attribute rebound inside try/finally with the restore asserted.

    python tests/test_matching_chars_per_token.py
"""

import ast
import hashlib
import math
import os
import sys

# ABOVE THE PACKAGE IMPORTS ON PURPOSE. oncotriage/agent/deps.py reads this
# variable ONCE, at its own import, and `deps` arrives transitively on the first
# `oncotriage` import -- so an assignment underneath the imports reaches nothing
# and the local models load for real.
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
from oncotriage.agent import deps
from oncotriage.agent import evaluation as _ev
from oncotriage.retrieval import indexer as _ix


_PKG = os.path.dirname(os.path.abspath(oncotriage.__file__))
_WATCHED = {
    "config.py": os.path.join(_PKG, "config.py"),
    "agent/evaluation.py": os.path.join(_PKG, "agent", "evaluation.py"),
    "retrieval/indexer.py": os.path.join(_PKG, "retrieval", "indexer.py"),
}
_HASHES_AT_IMPORT = {
    k: hashlib.sha256(open(v, "rb").read()).hexdigest()
    for k, v in _WATCHED.items()
}


# ===========================================================================
# HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, actual, expected) -> None:
    """Assert equality, record the outcome, never abort the run."""
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


def section(title) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def guarded(fn, *args, **kwargs):
    """Call ``fn`` and return a MARKER instead of raising.

    A RAISE INSIDE A ``check()`` ARGUMENT LIST ABORTS THE FILE -- the run then
    reports one traceback where it owed a summary and every result below. This
    project has shipped that shape often enough that a helper is the first thing
    written; every call into production code in this file goes through it.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                               # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


class _AsProvider:
    """Set ``config.MATCHING_PROVIDER`` for a block and put it back."""

    def __init__(self, provider):
        self._provider = provider

    def __enter__(self):
        self._saved = config.MATCHING_PROVIDER
        config.MATCHING_PROVIDER = self._provider
        return self

    def __exit__(self, *exc):
        config.MATCHING_PROVIDER = self._saved
        return False


_PROVIDER_AT_IMPORT = config.MATCHING_PROVIDER


# THE MEASUREMENTS THIS PASS IS BUILT ON, transcribed from the probe tables in
# oncotriage/config.py. Each row is (label, characters, the MODEL'S OWN token
# count for that exact text), read out of a usage block rather than estimated.
_CONVERSE_SAMPLES = (
    ("rendered system prefix", 32495, 9281),
    ("trial user block A",      3504,  985),
    ("trial user block B",      1939,  538),
    ("trial user block C",      1175,  304),
)


# ===========================================================================
# SECTION 1 -- THE OWNER
# ===========================================================================

section("1. the owner: one row per arm, and one function that answers")

check("1a  the table is TOTAL over the provider vocabulary -- an arm with no "
      "row would fall through to a default, and every available default is a "
      "claim about a tokenizer nobody measured",
      sorted(config.MATCHING_CHARS_PER_TOKEN_BY_PROVIDER),
      sorted(config.MATCHING_PROVIDERS))
check("1a-i  ...and the vocabulary is non-degenerate, so 1a is not one row "
      "compared with itself",
      len(config.MATCHING_PROVIDERS) > 1, True)

check("1b  the two gpt-5.6-terra arms read CHARS_PER_TOKEN rather than a "
      "retyped 4, so the OpenAI arm and the indexer's embedding batch sizer "
      "cannot come to disagree about a value that is one measurement",
      (config.MATCHING_CHARS_PER_TOKEN_BY_PROVIDER[
           config.MATCHING_PROVIDER_OPENAI],
       config.MATCHING_CHARS_PER_TOKEN_BY_PROVIDER[
           config.MATCHING_PROVIDER_BEDROCK],
       config.CHARS_PER_TOKEN),
      (4, 4, 4))
check("1c  the shipped Converse arm carries its own MEASURED value, so the "
      "table is a per-arm declaration rather than one number written three "
      "times",
      config.MATCHING_CHARS_PER_TOKEN_BY_PROVIDER[
          config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC], 3.5)
check("1c-i  ...and the arms genuinely disagree, which is the whole reason the "
      "owner exists",
      len(set(config.MATCHING_CHARS_PER_TOKEN_BY_PROVIDER.values())) > 1, True)

_ANSWERS = {}
with _AsProvider(_PROVIDER_AT_IMPORT):
    for _p in config.MATCHING_PROVIDERS:
        config.MATCHING_PROVIDER = _p
        _ANSWERS[_p] = guarded(config.matching_chars_per_token)
check("1d  the owner answers each arm's declared row, with the provider really "
      "set rather than passed in -- a function that took the provider as an "
      "argument could not catch a caller that reads the module attribute",
      _ANSWERS, dict(config.MATCHING_CHARS_PER_TOKEN_BY_PROVIDER))
check("1d-i  ...and the provider was restored",
      config.MATCHING_PROVIDER, _PROVIDER_AT_IMPORT)

check("1e  the live arm's divisor is the shipped provider's row -- READ AT "
      "CALL TIME, so a process that moved the provider is priced with the "
      "tokenizer it will actually meet",
      config.matching_chars_per_token(),
      config.MATCHING_CHARS_PER_TOKEN_BY_PROVIDER[config.MATCHING_PROVIDER])

with _AsProvider("nonsense-provider"):
    _UNKNOWN = guarded(config.matching_chars_per_token)
check("1f  an unrecognised provider RAISES rather than returning a default -- "
      "an estimate made with a tokenizer ratio nobody chose is exactly the "
      "silent-wrong-provider failure the closed vocabulary exists to prevent",
      isinstance(_UNKNOWN, str) and _UNKNOWN.startswith("<RAISED RuntimeError"),
      True)
check("1f-i  ...and the provider was restored after the refusal",
      config.MATCHING_PROVIDER, _PROVIDER_AT_IMPORT)

check("1g  the shipped table passes its own value guard",
      guarded(config.validate_matching_chars_per_token), None)

# THE GUARD IS DRIVEN OVER A TABLE OF INPUTS, which is the natural control for a
# pure function of its argument -- and it is the ONLY way to exercise a check
# whose import-time subject is a table that is always valid. THIS FILE'S FIRST
# VERSION RE-IMPLEMENTED THE PREDICATE INSTEAD, and a revert matrix reported the
# guard's removal as MISSED: the re-implementation went on passing about the
# shipped table while the module that would refuse a bad one had stopped
# refusing anything.
_GUARD_CASES = (
    ({"p": 4}, False, "an int divisor"),
    ({"p": 3.5}, False, "a float divisor"),
    ({"p": 4, "q": 3.5}, False, "several rows, all good"),
    ({"p": True}, True, "isinstance(True, int) is True, and a True divisor "
                        "estimates one token per character"),
    ({"p": False}, True, "and a False divisor divides by zero"),
    ({"p": 0}, True, "zero is a ZeroDivisionError on every estimate"),
    ({"p": -3.5}, True, "a negative divisor estimates negative tokens"),
    ({"p": "4"}, True, "a string divisor is a TypeError at the first estimate"),
    ({"p": None}, True, "and None is what an absent row would look like"),
    ({"p": 4, "q": 0}, True, "one bad row among good ones still refuses"),
)
_GUARD_SEEN = []
for _table, _must_raise, _why in _GUARD_CASES:
    _got = guarded(config.validate_matching_chars_per_token, _table)
    _GUARD_SEEN.append((sorted(_table.items(), key=lambda kv: str(kv)),
                        isinstance(_got, str)))
check("1g-i  the value guard refuses exactly the values that cannot be a "
      "divisor and accepts the ones that can",
      _GUARD_SEEN,
      [(sorted(t.items(), key=lambda kv: str(kv)), r)
       for t, r, _w in _GUARD_CASES])
check("1g-ii  ...non-degeneracy: the table exercises BOTH outcomes, so 1g-i is "
      "not ten readings of one branch",
      sorted({r for _t, r, _w in _GUARD_CASES}), [False, True])

# STRUCTURAL, BECAUSE A GUARD WHOSE SUBJECT IS ALWAYS VALID CANNOT BE CAUGHT
# BEHAVIOURALLY. Deleting the import-time CALL changes nothing observable while
# the shipped table is good -- a revert matrix reported exactly that as MISSED
# after 1g-i had closed the guard's own removal -- so the call site is pinned
# where it is: a MODULE-LEVEL call in config.py, which is what makes a bad table
# fail at import rather than at the first estimate of a paid run.
_CFG_TREE = ast.parse(open(_WATCHED["config.py"], encoding="utf-8").read())
_GUARD_CALLS = [n for n in _CFG_TREE.body
                if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Name)
                and n.value.func.id == "validate_matching_chars_per_token"]
check("1g-iii  the guard is CALLED at config.py's module scope, so a table "
      "with a bad row refuses at import rather than at the first estimate of a "
      "campaign that has already started spending",
      len(_GUARD_CALLS), 1)
check("1g-iv  ...and it is called with no argument, so what it checks at "
      "import is the SHIPPED table rather than one the call site made up",
      (bool(_GUARD_CALLS) and not _GUARD_CALLS[0].value.args
       and not _GUARD_CALLS[0].value.keywords), True)


# ===========================================================================
# SECTION 2 -- THE GUARD DIRECTION, ON THE RECORDED MEASUREMENTS
# ===========================================================================

section("2. which way the estimate errs, driven on the model's own counts")

# THE PROPERTY IS AN INEQUALITY AND NOT AN ACCURACY TARGET. estimate >= the
# model's own count, on every recorded sample, at the arm's own divisor.
with _AsProvider(config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
    _CONVERSE_EST = [(label, chars, tokens,
                      guarded(_ev.estimate_prompt_tokens, "x" * chars))
                     for label, chars, tokens in _CONVERSE_SAMPLES]
check("2a  ON THE SHIPPED ARM THE ESTIMATE OVER-STATES EVERY RECORDED SAMPLE, "
      "which is the direction a budget guard must err in: an under-estimate "
      "ships a chunk over the threshold the packing exists to stay under",
      [(label, est >= tokens) for label, _c, tokens, est in _CONVERSE_EST],
      [(label, True) for label, _c, _t, _e in _CONVERSE_EST])
check("2a-i  ...non-degeneracy: the four samples are four different lengths "
      "and four different estimates, so 2a is not one reading four times",
      (len({c for _l, c, _t, _e in _CONVERSE_EST}),
       len({e for _l, _c, _t, e in _CONVERSE_EST})), (4, 4))

check("2b  THE DEFECT THIS CLOSES, MEASURED: the OpenAI arm's divisor of 4 "
      "UNDER-states every one of those same samples -- so before the per-arm "
      "owner the shipped arm's packer believed its chunks were smaller than "
      "they were, by up to 12.5% on the prefix",
      [(label, math.ceil(chars / config.CHARS_PER_TOKEN) >= tokens)
       for label, chars, tokens in _CONVERSE_SAMPLES],
      [(label, False) for label, _c, _t in _CONVERSE_SAMPLES])

check("2c  THE ADMISSIBLE SET IS BOUNDED BY THE SMALLEST MEASURED RATIO, and "
      "the shipped row sits at it rather than above it: a divisor above the "
      "minimum ratio would UNDER-state the densest text the arm has been "
      "measured on",
      config.MATCHING_CHARS_PER_TOKEN_BY_PROVIDER[
          config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC]
      <= min(round(c / t, 4) for _l, c, t in _CONVERSE_SAMPLES), True)
check("2c-i  ...and the binding sample is the PREFIX -- the largest and densest "
      "of the four -- named here because it is the sample a fifth measurement "
      "would have to come in under to move the row, and because a row set by a "
      "SMALL sample would be a row set by the least representative text",
      (min(_CONVERSE_SAMPLES, key=lambda r: r[1] / r[2])[0],
       max(_CONVERSE_SAMPLES, key=lambda r: r[1])[0]),
      ("rendered system prefix", "rendered system prefix"))

with _AsProvider(config.MATCHING_PROVIDER_OPENAI):
    _OPENAI_EST = guarded(_ev.estimate_prompt_tokens, "x" * 32495)
check("2d  the OpenAI arm is UNCHANGED by all of this: the same text prices at "
      "exactly len/4 rounded up, which is what it priced at before the owner "
      "existed",
      _OPENAI_EST, -(-32495 // 4))


# ===========================================================================
# SECTION 3 -- EVERY STAGE 5 ESTIMATOR READS THE OWNER
# ===========================================================================

section("3. the three estimator sites follow the arm")


# THE CRITERIA TIE-BREAKER IS CAPPED AT 0.25 x K x n, AND A FIXTURE THAT
# SATURATES THE CAP MEASURES THE CAP RATHER THAN THE DIVISOR. Sized so the term
# is comfortably under it at BOTH divisors -- 10,000 characters an arm is
# ~5,000 tokens at 4 and ~5,714 at 3.5, so the 5% term is 250 against 285 while
# the cap sits at 0.25 x K. 3b-i is the non-degeneracy check that says so.
_CRIT_CHARS_PER_ARM = 10000
_CRIT_TRIAL = [{"trial": {"nct_id": "NCT1", "title": "t", "eligibility": {
    "inclusion_criteria": "i" * _CRIT_CHARS_PER_ARM,
    "exclusion_criteria": "e" * _CRIT_CHARS_PER_ARM}}}]


def _estimator_readings(provider):
    """The three per-arm figures, taken with the provider really set."""
    with _AsProvider(provider):
        return (guarded(_ev.estimate_prompt_tokens, "y" * 7000),
                guarded(_ev.estimate_output_tokens, _CRIT_TRIAL),
                guarded(_ev.packing_method_chars))


_READINGS = {p: _estimator_readings(p) for p in config.MATCHING_PROVIDERS}
check("3a  estimate_prompt_tokens divides by the LIVE arm's value",
      {p: r[0] for p, r in _READINGS.items()},
      {p: math.ceil(7000 / d)
       for p, d in config.MATCHING_CHARS_PER_TOKEN_BY_PROVIDER.items()})
check("3b  estimate_output_tokens's criteria tie-breaker does too -- it is a "
      "capped tie-breaker rather than the driver, so the arm only moves it at "
      "the margin, but a criteria term measured with one arm's tokenizer while "
      "the count term is calibrated on another's is two judges in one estimate",
      {p: r[1] for p, r in _READINGS.items()},
      {p: int(config.MATCHING_OUTPUT_TOKENS_PER_TRIAL
              + min(2 * _CRIT_CHARS_PER_ARM / d * 0.05,
                    0.25 * config.MATCHING_OUTPUT_TOKENS_PER_TRIAL))
       for p, d in config.MATCHING_CHARS_PER_TOKEN_BY_PROVIDER.items()})
check("3b-i  ...and the arms really do give different numbers, which they "
      "only do while the criteria term is UNDER its cap: a fixture fat "
      "enough to saturate 0.25 x K x n measures the cap and reports the "
      "divisor as having no effect",
      (len({r[1] for r in _READINGS.values()}) > 1,
       max(2 * _CRIT_CHARS_PER_ARM / d * 0.05
           for d in config.MATCHING_CHARS_PER_TOKEN_BY_PROVIDER.values())
       < 0.25 * config.MATCHING_OUTPUT_TOKENS_PER_TRIAL), (True, True))
check("3c  packing_method_chars() names the divisor the packer actually used, "
      "so a run that packed on one arm cannot record another arm's divisor",
      {p: r[2] for p, r in _READINGS.items()},
      {p: f"characters/{d}"
       for p, d in config.MATCHING_CHARS_PER_TOKEN_BY_PROVIDER.items()})
check("3c-i  ...and it is a FUNCTION rather than the module constant it "
      "replaced. A label built once at import states the divisor of whichever "
      "arm the module was imported under and keeps stating it afterwards -- "
      "naming a divisor the packer did not use, in the field that exists so "
      "nobody has to guess which divisor the packer used",
      callable(getattr(_ev, "packing_method_chars", None))
      and not hasattr(_ev, "PACKING_METHOD_CHARS"), True)
check("3d  the provider was restored after all three readings",
      config.MATCHING_PROVIDER, _PROVIDER_AT_IMPORT)

# STRUCTURAL, BECAUSE A BEHAVIOURAL CHECK CANNOT SEE A FOURTH SITE ADDED LATER.
# A `CHARS_PER_TOKEN` load anywhere in the Stage 5 module is a divisor that has
# stopped following the arm, and it would be invisible on the OpenAI arm -- the
# arm every packing test pins -- because the two answers agree there.
_EV_TREE = ast.parse(open(_WATCHED["agent/evaluation.py"], encoding="utf-8").read())
_CPT_LOADS = [n for n in ast.walk(_EV_TREE)
              if isinstance(n, ast.Name) and n.id == "CHARS_PER_TOKEN"
              and isinstance(n.ctx, ast.Load)]
_CPT_ATTRS = [n for n in ast.walk(_EV_TREE)
              if isinstance(n, ast.Attribute) and n.attr == "CHARS_PER_TOKEN"]
check("3e  the Stage 5 module loads CHARS_PER_TOKEN in NEITHER reference form "
      "-- not as a bare name and not as config.CHARS_PER_TOKEN -- so no "
      "estimator can quietly go back to the one-number divisor",
      (len(_CPT_LOADS), len(_CPT_ATTRS)), (0, 0))
_OWNER_CALLS = [n for n in ast.walk(_EV_TREE)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "matching_chars_per_token"]
check("3e-i  ...non-degeneracy: the owner IS called there, and at the three "
      "sites this pass wired -- so 3e is a statement about a module that reads "
      "a divisor rather than about one that reads none",
      len(_OWNER_CALLS), 3)


# ===========================================================================
# SECTION 4 -- EXACTNESS, WHICH IS WHY THE OpenAI ARM'S NUMBERS DID NOT MOVE
# ===========================================================================

section("4. math.ceil agrees with the integer idiom it replaced")

# estimate_prompt_tokens rounded up with `-(-n // d)` while the divisor was
# always an int. It is `math.ceil(n / d)` now, because that idiom returns a
# FLOAT for a float divisor -- which would put a float in an INTEGER column and
# in the packing JSON. THE CLAIM THAT THE OpenAI ARM'S NUMBERS ARE UNCHANGED
# RESTS ENTIRELY ON THESE TWO AGREEING, so it is driven exhaustively over a
# range rather than argued from IEEE-754.
_RANGE = range(0, 20001)
check("4a  at the OpenAI arm's divisor of 4 the two agree for every length in "
      "0..20,000 -- 4 is a power of two, so n/4 is exact in binary and the "
      "quotient is the same integer either way",
      [n for n in _RANGE if math.ceil(n / 4) != -(-n // 4)], [])
check("4b  at the shipped arm's 3.5 they agree too. 3.5 is 7/2 and therefore "
      "exactly representable, and IEEE-754 division returns an exactly "
      "representable quotient exactly, so a true-integer quotient cannot come "
      "back one ulp high and ceil to the wrong integer",
      [n for n in _RANGE if math.ceil(n / 3.5) != -(-n // 3.5)], [])
check("4c  ...and the result is an int on BOTH arms, which the integer idiom "
      "is not: `-(-9 // 3.5)` is a float, and a float in "
      "llm_classifier_input_tokens_estimated is a float in an INTEGER column",
      (type(math.ceil(9 / 3.5)).__name__, type(-(-9 // 3.5)).__name__),
      ("int", "float"))
check("4d  non-degeneracy: 3.5 and 4 do NOT give the same answer, so 4a and 4b "
      "are two measurements rather than one repeated",
      len({math.ceil(9999 / 4), math.ceil(9999 / 3.5)}), 2)

# 4a-4d ARE ABOUT ARITHMETIC AND 4d-i IS ABOUT THE SHIPPED FUNCTION. A revert
# matrix put estimate_prompt_tokens back on the integer idiom and 4a-4d went on
# passing -- they were true of `math.ceil` and said nothing about the estimator
# that had stopped calling it. The float leaks into an INTEGER column, so the
# TYPE the shipped function returns is the property, on the arm whose divisor is
# a float.
_TYPES = {}
for _p in config.MATCHING_PROVIDERS:
    with _AsProvider(_p):
        _TYPES[_p] = type(guarded(_ev.estimate_prompt_tokens, "z" * 9)).__name__
check("4d-i  estimate_prompt_tokens returns an int on EVERY arm, including the "
      "one whose divisor is a float -- a float here is a float in "
      "llm_classifier_input_tokens_estimated and in the packing JSON",
      _TYPES, {p: "int" for p in config.MATCHING_PROVIDERS})
check("4d-ii  ...non-degeneracy: at least one arm's divisor IS a float, so "
      "4d-i is not three readings of the integer path",
      any(isinstance(d, float)
          for d in config.MATCHING_CHARS_PER_TOKEN_BY_PROVIDER.values()), True)
check("4e  empty text still costs nothing on every arm",
      {p: _estimator_readings(p) and guarded(_ev.estimate_prompt_tokens, "")
       for p in config.MATCHING_PROVIDERS},
      {p: 0 for p in config.MATCHING_PROVIDERS})


# ===========================================================================
# SECTION 5 -- THE INDEXER IS UNTOUCHED
# ===========================================================================

section("5. the embedding batch sizer still reads CHARS_PER_TOKEN, at 4")

check("5a  the indexer's reported method names 4, byte for byte -- a label "
      "that moved would describe an index build nobody performed",
      _ix.ESTIMATE_METHOD_CHARS, "chars/4")

# THE BATCH ARITHMETIC ON A FIXED SAMPLE, against the pre-pass formula written
# out here. The indexer computes it inline inside index_trials(), so this
# reproduces the two lines rather than calling them -- which is why the check
# beneath it pins that those lines still divide by the constant.
_SAMPLE_CHARS = [1200, 3400, 800, 15000, 2600]
_avg_tokens = (sum(_SAMPLE_CHARS) / len(_SAMPLE_CHARS)) / _ix.CHARS_PER_TOKEN
_batch = max(1, min(int(_ix.EMBED_TARGET_TOKENS_PER_REQUEST // _avg_tokens),
                    _ix.EMBED_MAX_INPUTS_PER_REQUEST))
check("5b  the batch size on a fixed sample is what the pre-pass arithmetic "
      "gives, because every term in it is unchanged",
      (round(_avg_tokens, 6), _batch),
      (round((sum(_SAMPLE_CHARS) / len(_SAMPLE_CHARS)) / 4, 6),
       max(1, min(int(_ix.EMBED_TARGET_TOKENS_PER_REQUEST
                      // ((sum(_SAMPLE_CHARS) / len(_SAMPLE_CHARS)) / 4)),
                  _ix.EMBED_MAX_INPUTS_PER_REQUEST))))
check("5b-i  ...non-degeneracy: the sample really does bind on the token "
      "budget rather than falling through to the input cap, so 5b is about "
      "the divisor",
      _batch < _ix.EMBED_MAX_INPUTS_PER_REQUEST, True)

_IX_TREE = ast.parse(open(_WATCHED["retrieval/indexer.py"], encoding="utf-8").read())
_IX_OWNER = [n for n in ast.walk(_IX_TREE)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "matching_chars_per_token"]
check("5c  the indexer does NOT read the Stage 5 owner. It talks to the OpenAI "
      "EMBEDDING endpoint whatever Stage 5's judge is, so a per-arm matching "
      "divisor reaching it would resize every embedding batch on a fact about "
      "a different model",
      len(_IX_OWNER), 0)
_IX_CPT = [n for n in ast.walk(_IX_TREE)
           if isinstance(n, ast.Name) and n.id == "CHARS_PER_TOKEN"
           and isinstance(n.ctx, ast.Load)]
check("5c-i  ...non-degeneracy: it still reads CHARS_PER_TOKEN, so 5c is not a "
      "statement about a module that divides by a literal",
      len(_IX_CPT) >= 3, True)


# ===========================================================================
# SECTION 6 -- HYGIENE
# ===========================================================================

section("6. no client, no model, no source touched")

check("6a  no OpenAI client was built. `deps.is_resolved` rather than an "
      "accessor, which would construct the very client this asserts was not "
      "built. THE SPEND TRIPWIRE: a real client here means a request could "
      "have been issued",
      deps.is_resolved(deps.OPENAI_CLIENT), False)
check("6a-i  ...and no Bedrock client of either kind either",
      (deps.is_resolved(deps.BEDROCK_CLIENT),
       deps.is_resolved(deps.BEDROCK_ANTHROPIC_CLIENT)), (False, False))
check("6b  no model was loaded",
      sorted(m for m in ("torch", "transformers") if m in sys.modules), [])
check("6c  the provider this file moved is back where it started",
      config.MATCHING_PROVIDER, _PROVIDER_AT_IMPORT)

for _label, _path in _WATCHED.items():
    check(f"6d  {_label} is byte-identical -- this file rebinds attributes, "
          f"never source",
          hashlib.sha256(open(_path, "rb").read()).hexdigest(),
          _HASHES_AT_IMPORT[_label])
check("6d-i  ...and the three hashes differ from each other, so 6d is not one "
      "file compared with itself",
      len(set(_HASHES_AT_IMPORT.values())), len(_HASHES_AT_IMPORT))


# ===========================================================================
# SUMMARY
# ===========================================================================

print(f"\n{'=' * 74}\nRESULTS:\n  passed: {_RESULTS['passed']}\n"
      f"  failed: {_RESULTS['failed']}")
if _FAILURES:
    print("\nFAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")
print("=" * 74)

sys.exit(1 if _RESULTS["failed"] else 0)
