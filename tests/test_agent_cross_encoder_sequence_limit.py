# Cross-Encoder Sequence Limit: config owns it, and the checkpoint verifies it
##############################################################################

"""Cross-Encoder Sequence Limit Test

``config.CROSS_ENCODER_MAX_LENGTH`` is a property OF
``config.CROSS_ENCODER_MODEL`` -- MedCPT is BERT-shaped and carries 512 learned
position embeddings -- and before this pass the number was a bare ``512`` at
two tokenizer call sites with nothing tying it to the checkpoint at all.

WHY A DRIFTED LIMIT IS SILENT, which is the whole reason this file exists.
Every tokenizer call passes ``truncation=True``, so transformers does exactly
what the number says and raises nothing. Set the limit below the checkpoint's
real budget and Stage 3 keeps scoring, ``node_cross_encoder_rerank`` keeps
sorting, the Stage 4 quality gate keeps cutting -- the cross-encoder is simply
reading less of every trial than it could, and the only symptom is a worse
ranking. Set it above and the failure is loud but LATE: an IndexError out of
the embedding lookup, per patient, thirty frames inside Stage 3.
``oncotriage/agent/deps.py:_verify_cross_encoder_sequence_limit`` is the check
that makes it neither, and this is its behavioural half. The STRUCTURAL half is
section 2f(iii) of ``tests/test_package_invariants.py``, and neither replaces
the other: an AST scan cannot see a verifier that has stopped verifying, and a
runtime check cannot see a second literal that was never routed through it.

THE MEASUREMENT THAT DECIDED THE DESIGN, and the reason section 3 exists.
Read off the cached ``ncbi/MedCPT-Cross-Encoder`` on 2026-08-21 rather than
assumed::

    tokenizer_config.json  "model_max_length": 1000000000000000019884624838656
                           -- transformers.VERY_LARGE_INTEGER: NO limit declared
    config.json            "max_position_embeddings": 512

So a check written only against ``tokenizer.model_max_length`` -- the obvious
place, since the tokenizer is what takes ``max_length`` -- would take the
"undeclared" branch on every load of the shipped checkpoint and verify NOTHING,
forever, while looking exactly like a check that passes. The WEIGHTS are what
verify this number. Section 3 pins that asymmetry against the checkpoint's real
declaration values so nobody re-derives the wrong half later.

NO MODEL IS LOADED HERE, AND THE OVERRIDE SEAM IS NOT THE WAY TO DRIVE THIS.
``deps.set_override(deps.MEDCPT_TOKENIZER, stub)`` installs a stand-in that
``_resolve`` returns BEFORE the factory runs, so an override can never reach
the check inside the factory -- it is the wrong instrument for this subject and
section 5 proves that rather than assuming it. The verifier is a pure function
of its argument, so every arm is driven by CALLING IT with a fabricated
declaration, which is also the natural control for a pure function (the
precedent ``tests/test_agent_patient_hash_coverage.py`` sets).

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL, NO MODEL DOWNLOAD, NO
CORPUS, NO DATABASE, NO GIT HISTORY. ``ONCOTRIAGE_DEFER_LOCAL_MODELS`` is set
above the imports (the ordering lesson from pass 20c-3d) and section 4 asserts
that torch and transformers never entered ``sys.modules``.

IT EXECS NOTHING, so it needs no ``_EXEC_ALLOWLIST`` entry: every control is a
different INPUT to a pure function, or an override installed inside
``try``/``finally`` and asserted removed.

NOT in ``tests/run_serial_tests.py``'s collision matrix, derived rather than
assumed: it writes nothing anywhere, and the one repository file it READS is
``oncotriage/agent/deps.py``, which is written by neither
``tests/test_registries_cancer_code_claims_audit_control.py`` (which writes
``oncotriage/registries/cancer_code_registry.py``) nor
``tests/test_config_snapshot_date_rot.py`` (which writes
``oncotriage/config.py``).

    python tests/test_agent_cross_encoder_sequence_limit.py
"""

import ast
import os
import sys

# ABOVE THE PACKAGE IMPORTS ON PURPOSE. oncotriage/agent/deps.py reads this
# variable ONCE, at its own import, and `deps` arrives transitively on the
# first `oncotriage` import -- so an assignment underneath the imports reaches
# nothing and the local models load for real.
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

from oncotriage import config, degradation, observability
from oncotriage.agent import deps


# ===========================================================================
# HARNESS
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
        _FAILURES.append(f"{label}\n          expected: {expected}\n"
                         f"          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def check_true(label: str, condition) -> None:
    check(label, bool(condition), True)


def section(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def verify(declared, source="probe.attribute", checkpoint="probe/checkpoint"):
    """Drive the verifier and convert a RAISE into a value check() can compare.

    A BARE CALL WOULD ABORT THE RUN. The mismatch arm raises by design, and an
    exception escaping through check()'s argument list takes every check below
    it with it -- one traceback where the file owes a summary. This project has
    shipped that shape seven times; it is not shipping it an eighth.
    """
    try:
        return deps._verify_cross_encoder_sequence_limit(
            declared, source, checkpoint)
    except deps.CrossEncoderLimitMismatchError as exc:
        return ("RAISED", str(exc))
    except Exception as exc:                        # noqa: BLE001
        return ("RAISED-WRONG-TYPE", type(exc).__name__, str(exc))


def marker(outcome):
    """One comparable string for any verify() outcome.

    NOT DECORATION. verify() returns a str for the three states and a TUPLE for
    a raise, and a set holding both cannot be sorted -- `TypeError: '<' not
    supported between instances of 'tuple' and 'str'`. That TypeError fires
    exactly when a defect makes an arm raise that should not, i.e. precisely
    when this file owes a recorded failure, and it aborts the run instead. The
    revert harness caught it (R6); reading did not.
    """
    return outcome if isinstance(outcome, str) else str(outcome[0])


def at(seq, index, default=None):
    """seq[index], or `default` -- never an IndexError.

    Same reason as marker(): a bare index raises on the shape a defect
    produces, and one traceback is not forty-two recorded results.
    """
    try:
        return seq[index]
    except (IndexError, KeyError, TypeError):
        return default


def counter_delta(before, after):
    """{key: increase} for every key that moved between two counter snapshots."""
    return {k: after.get(k, 0) - before.get(k, 0)
            for k in set(before) | set(after)
            if after.get(k, 0) != before.get(k, 0)}


_CONFIGURED = config.CROSS_ENCODER_MAX_LENGTH


# ===========================================================================
# SECTION 1 -- the constant itself
# ===========================================================================

section("SECTION 1 -- config.CROSS_ENCODER_MAX_LENGTH")

check_true("1a  it exists and is a plain int",
           isinstance(_CONFIGURED, int) and not isinstance(_CONFIGURED, bool))
check_true("1b  ...and is positive and plausible for a transformer "
           "(non-degeneracy: a 0 or a negative would satisfy 'is an int' and "
           "make every comparison below meaningless)",
           0 < _CONFIGURED < deps._UNDECLARED_LIMIT_FLOOR)
check("1c  it is 512 today, which is what MedCPT's weights declare "
      "(config.json max_position_embeddings)", _CONFIGURED, 512)
check_true("1d  the checkpoint name is beside it and unchanged",
           config.CROSS_ENCODER_MODEL == "ncbi/MedCPT-Cross-Encoder")


# ===========================================================================
# SECTION 2 -- the verifier's three states and its raise
# ===========================================================================

section("SECTION 2 -- every arm of _verify_cross_encoder_sequence_limit")

check("2a  the return vocabulary is closed and a caller can branch on it "
      "exhaustively",
      sorted(deps.LIMIT_VERIFICATION_STATES),
      ["undeclared", "unreadable", "verified"])

# --- MATCHING PASSES -------------------------------------------------------
_before = dict(deps.CROSS_ENCODER_LIMIT_DEGRADATIONS)
check("2b  a declaration EQUAL to the configured limit verifies",
      verify(_CONFIGURED), deps.LIMIT_VERIFIED)
check("2c  ...and moves no degradation counter, because nothing degraded",
      counter_delta(_before, dict(deps.CROSS_ENCODER_LIMIT_DEGRADATIONS)), {})

# --- MISMATCH FIRES --------------------------------------------------------
#
# BOTH DIRECTIONS, because they fail differently and only one of them is loud
# on its own: a configured limit BELOW the checkpoint's is pure silent quality
# loss, and it is the direction a check written as "is it too big" would miss.
for _label, _declared in (("2d  BELOW the configured limit", _CONFIGURED // 2),
                          ("2e  ABOVE the configured limit", _CONFIGURED * 2)):
    _before = dict(deps.CROSS_ENCODER_LIMIT_DEGRADATIONS)
    _outcome = verify(_declared)
    check(f"{_label} RAISES CrossEncoderLimitMismatchError",
          marker(_outcome), "RAISED")
    _message = at(_outcome, 1, "") if marker(_outcome) == "RAISED" else ""
    check_true(f"{_label}: ...and the message names BOTH numbers and the "
               f"constant to fix, so the operator needs no second lookup",
               (str(_declared) in _message and str(_CONFIGURED) in _message
                and "CROSS_ENCODER_MAX_LENGTH" in _message))
    check(f"{_label}: ...and a MISMATCH is never counted -- counting it would "
          f"make a contradiction look like a missing declaration",
          counter_delta(_before, dict(deps.CROSS_ENCODER_LIMIT_DEGRADATIONS)),
          {})

check_true("2f  it is a RuntimeError subclass and deliberately NOT a "
           "ValueError, so a stray `except ValueError` around a model load "
           "cannot eat it",
           issubclass(deps.CrossEncoderLimitMismatchError, RuntimeError)
           and not issubclass(deps.CrossEncoderLimitMismatchError, ValueError))

# --- THE PLACEHOLDER IS HANDLED EXPLICITLY ---------------------------------
#
# The exact value transformers uses, typed out, because this is the arm the
# shipped checkpoint takes on every load and the one an equality-only check
# would have compared blindly against 512 and reported as a mismatch.
_VERY_LARGE_INTEGER = 1000000000000000019884624838656

_before = dict(deps.CROSS_ENCODER_LIMIT_DEGRADATIONS)
check("2g  the transformers placeholder is UNDECLARED, not a mismatch -- "
      "'this checkpoint states no limit' is a different fact from 'it states "
      "a different one'",
      verify(_VERY_LARGE_INTEGER, source="tokenizer.model_max_length"),
      deps.LIMIT_UNDECLARED)
check("2h  ...and it is COUNTED, keyed by cause and source and never by a "
      "value, so it is never silent",
      counter_delta(_before, dict(deps.CROSS_ENCODER_LIMIT_DEGRADATIONS)),
      {"undeclared_placeholder:tokenizer.model_max_length": 1})

check_true("2i  the placeholder floor is a FLOOR rather than an equality "
           "against that exact literal, so a vendor that moves the sentinel "
           "does not turn every load into a false mismatch",
           deps._UNDECLARED_LIMIT_FLOOR < _VERY_LARGE_INTEGER)
check_true("2j  ...and it is far above any real transformer's positional "
           "budget, so a genuine limit can never reach it",
           deps._UNDECLARED_LIMIT_FLOOR > 10 ** 9)

# --- ABSENT AND UNREADABLE -------------------------------------------------
_before = dict(deps.CROSS_ENCODER_LIMIT_DEGRADATIONS)
check("2k  a MISSING attribute is undeclared, not a crash",
      verify(None, source="weights.config.max_position_embeddings"),
      deps.LIMIT_UNDECLARED)
check("2l  ...counted under its own key, separate from the placeholder, "
      "because the two say different things about the checkpoint",
      counter_delta(_before, dict(deps.CROSS_ENCODER_LIMIT_DEGRADATIONS)),
      {"undeclared_missing:weights.config.max_position_embeddings": 1})

_before = dict(deps.CROSS_ENCODER_LIMIT_DEGRADATIONS)
check("2m  a NON-INTEGER declaration is unreadable, not a crash and not a "
      "mismatch", verify("512"), deps.LIMIT_UNREADABLE)
check("2n  ...keyed by the TYPE, which is what tells the next reader what the "
      "checkpoint actually put there",
      counter_delta(_before, dict(deps.CROSS_ENCODER_LIMIT_DEGRADATIONS)),
      {"unreadable:probe.attribute:str": 1})

# True == 1 in Python, so a bool that slipped through would be compared as the
# number 1 and reported as a mismatch against 512 -- naming the wrong defect.
_before = dict(deps.CROSS_ENCODER_LIMIT_DEGRADATIONS)
check("2o  a BOOL is unreadable rather than the integer 1, so it cannot be "
      "reported as a mismatch and send an operator to the wrong constant",
      verify(True), deps.LIMIT_UNREADABLE)
check("2p  ...and says it was a bool",
      counter_delta(_before, dict(deps.CROSS_ENCODER_LIMIT_DEGRADATIONS)),
      {"unreadable:probe.attribute:bool": 1})

# NON-DEGENERACY FOR THE WHOLE SECTION: the four arms above must produce four
# DIFFERENT outcomes, or every check here is satisfied by a function that
# returns one constant. The raise is folded in as its marker rather than its
# message, because the message carries the two numbers and would differ from
# itself for uninteresting reasons.
#
# THE FIRST DRAFT OF THIS CHECK EXPECTED 3 AND WAS WRONG: a mismatch is a
# FOURTH outcome, not a fourth spelling of one of the three return values, and
# that is precisely the distinction sections 2b-2p exist to hold. Recorded
# rather than quietly corrected -- it is the shape a non-degeneracy probe fails
# in when its author counts the vocabulary instead of the arms.
check("2q  the arms above produced four genuinely different outcomes "
      "(non-degeneracy)",
      sorted({marker(verify(_CONFIGURED)),
              marker(verify(_VERY_LARGE_INTEGER)),
              marker(verify("512")),
              marker(verify(_CONFIGURED + 1))}),
      ["RAISED", "undeclared", "unreadable", "verified"])


# ===========================================================================
# SECTION 3 -- the asymmetry between the two halves, pinned
# ===========================================================================

section("SECTION 3 -- which half of the checkpoint actually verifies the limit")

# THIS SECTION IS THE ONE THAT STOPS THE CHECK GOING VACUOUS. It pins what the
# shipped checkpoint declares, WITHOUT loading it: the tokenizer says nothing,
# the weights say 512. If a future edit deleted the weights-side call and kept
# only the tokenizer-side one, everything in section 2 would still pass and the
# package would verify nothing at all.
check("3a  the TOKENIZER's declaration (VERY_LARGE_INTEGER) verifies nothing, "
      "which is what the shipped checkpoint reports on every load",
      verify(_VERY_LARGE_INTEGER, source="tokenizer.model_max_length"),
      deps.LIMIT_UNDECLARED)
check("3b  the WEIGHTS' declaration (max_position_embeddings = 512) is what "
      "verifies -- so a tokenizer-only check would be permanently vacuous",
      verify(512, source="weights.config.max_position_embeddings"),
      deps.LIMIT_VERIFIED)

# AND BOTH FACTORIES CALL IT, read off the shipped source. Section 2f(iii) of
# tests/test_package_invariants.py asserts the same thing; it is repeated here
# because sections 2 and 3 are ABOUT a function whose only value is being
# called, and a behavioural file that never asks whether its subject is wired
# in is testing a library nobody uses.
_DEPS_PY = os.path.join(os.path.dirname(os.path.abspath(deps.__file__)),
                        "deps.py")
_tree = ast.parse(open(_DEPS_PY, encoding="utf-8").read(), _DEPS_PY)
_callers = sorted(
    fn.name for fn in ast.walk(_tree)
    if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
    for c in ast.walk(fn)
    if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
    and c.func.id == "_verify_cross_encoder_sequence_limit")
check("3c  both MedCPT factories call the verifier", _callers,
      ["_build_medcpt_model", "_build_medcpt_tokenizer"])


# ===========================================================================
# SECTION 4 -- the deferred path is untouched
# ===========================================================================

section("SECTION 4 -- no model was loaded to run any of the above")

check_true("4a  ONCOTRIAGE_DEFER_LOCAL_MODELS was set before deps was imported",
           deps._DEFER_LOCAL_MODELS)

_tok = deps.get_medcpt_tokenizer()
_mdl = deps.get_medcpt_model()
check("4b  the deferred tokenizer is the placeholder, so the factory returned "
      "ABOVE the verifier", type(_tok).__name__, "_DeferredLocalModel")
check("4c  ...and so does the weights factory", type(_mdl).__name__,
      "_DeferredLocalModel")

check("4d  torch never entered sys.modules", "torch" in sys.modules, False)
check("4e  ...nor transformers", "transformers" in sys.modules, False)

# The deferral must not have been achieved by the verifier swallowing something.
_before = dict(deps.CROSS_ENCODER_LIMIT_DEGRADATIONS)
deps.get_medcpt_tokenizer()
deps.get_medcpt_model()
check("4f  a deferred load moves NO limit counter, which is what says the "
      "check sits below the deferral return rather than above it",
      counter_delta(_before, dict(deps.CROSS_ENCODER_LIMIT_DEGRADATIONS)), {})


# ===========================================================================
# SECTION 5 -- the override seam short-circuits the factory (and so the check)
# ===========================================================================

section("SECTION 5 -- an installed override never reaches the verifier")

# THE BRIEF THIS FILE WAS WRITTEN FROM ASKED FOR THE CHECK TO BE DRIVEN THROUGH
# deps.set_override(). It cannot be, and that is a fact about the seam rather
# than a limitation of the test: _resolve() answers override -> cached -> build,
# so an override is returned BEFORE the factory runs and the check inside the
# factory is unreachable from it. Proved here rather than asserted, because a
# future reader will have the same idea.


class _StubTokenizer:
    """A stand-in carrying a fabricated -- and WRONG -- model_max_length."""

    model_max_length = 7   # not the configured limit, by construction

    def __call__(self, *a, **kw):
        raise AssertionError("the stub tokenizer was called; this test never "
                             "tokenizes anything")


_previous = deps.set_override(deps.MEDCPT_TOKENIZER, _StubTokenizer())
try:
    _before = dict(deps.CROSS_ENCODER_LIMIT_DEGRADATIONS)
    _got = deps.get_medcpt_tokenizer()
    check("5a  the accessor hands back the stub BY IDENTITY, so the seam is "
          "what is under test rather than a private dict",
          isinstance(_got, _StubTokenizer), True)
    check_true("5b  ...and the stub's model_max_length is genuinely not the "
               "configured limit, so a verifier that HAD run would have raised "
               "(non-degeneracy: without this the check below passes for free)",
               _StubTokenizer.model_max_length != _CONFIGURED)
    check("5c  ...yet no counter moved and nothing raised: an override "
          "short-circuits the factory, so set_override is the WRONG "
          "instrument for driving this check",
          counter_delta(_before, dict(deps.CROSS_ENCODER_LIMIT_DEGRADATIONS)),
          {})
finally:
    if isinstance(_previous, deps._Unset):
        deps.clear_override(deps.MEDCPT_TOKENIZER)
    else:
        deps.set_override(deps.MEDCPT_TOKENIZER, _previous)

check("5d  the override was removed again", 
      isinstance(deps.get_override(deps.MEDCPT_TOKENIZER), deps._Unset), True)


# ===========================================================================
# SECTION 6 -- the counter is reported, not just kept
# ===========================================================================

section("SECTION 6 -- the degradation counter has a reader")

# A COUNTER WITH NO READER LOOKS LIKE COVERAGE, which is the argument
# oncotriage/degradation.py opens with. This one moves on every real load of
# the shipped checkpoint, so a run that never reports it is a run that never
# says its cross-encoder limit went unverified.
check("6a  CROSS_ENCODER_LIMIT_DEGRADATIONS is in the run-end registry",
      "CROSS_ENCODER_LIMIT_DEGRADATIONS" in degradation.registered_names(),
      True)
# `.get`, never `[...]`: the key is ABSENT in exactly the state 6a exists to
# catch, so a bare index would raise there and take 6c and 6d with it.
check_true("6b  ...and the registry holds the SAME Counter OBJECT, not a "
           "snapshot -- a snapshot would report zero forever",
           degradation._REGISTRY.get("CROSS_ENCODER_LIMIT_DEGRADATIONS")
           is deps.CROSS_ENCODER_LIMIT_DEGRADATIONS)

_snap = degradation.snapshot()
check_true("6c  the counter this file moved appears in the run-end snapshot "
           "(non-degeneracy for 6a/6b: a registered name that never reaches "
           "the report is the same hole one level along)",
           sum(_snap.get("CROSS_ENCODER_LIMIT_DEGRADATIONS", {}).values()) > 0)

_text = "\n".join(degradation.report_lines(_snap))
check_true("6d  ...and the printed report names it and says what a non-zero "
           "means", "CROSS_ENCODER_LIMIT_DEGRADATIONS" in _text
           and "UNVERIFIED" in _text)


# ===========================================================================
# SECTION 7 -- the cross-encoder's PRECISION, the second property of the same
#              checkpoint, and the one transformers 5.x stopped deciding
# ===========================================================================

section("SECTION 7 -- config.CROSS_ENCODER_DTYPE and its verifier")

# WHY THIS SECTION EXISTS AND WHY IT DID NOT BEFORE. transformers 4.x FORCED
# float32 on every from_pretrained that did not ask for something else, so the
# precision of Stage 3 was a property of the LIBRARY and there was nothing here
# to own or to check. 5.0.0 changed the default to "auto" -- the checkpoint's
# own config.json -- so it became a property of a JSON FILE ON A THIRD-PARTY
# HUB. The failure that creates is verbatim the one sections 1-6 are about, one
# field over: a checkpoint republished at bfloat16 loads without a word, scores
# every pair, and only the ranking moves.
#
# MEASURED 2026-09-02, which is why the upgrade moved not one score of 4,300:
# ncbi/MedCPT-Cross-Encoder's config.json declares "torch_dtype": "float32", so
# 5.x's "auto" resolves to exactly what 4.x forced. 7c pins the constant to
# that value for the same reason 1c pins 512 -- the number is a FACT ABOUT THE
# CHECKPOINT, and a constant that drifted off it is the whole subject here.

_DTYPE = config.CROSS_ENCODER_DTYPE

# Sentinel for "the model has no `dtype` attribute at all", which is a
# different input from "it has one and it is None". Bound ABOVE verify_dtype
# because that function's default-argument-free body reads it at call time and
# 7g/7i drive both spellings.
_ABSENT = object()

check_true("7a  config.CROSS_ENCODER_DTYPE exists and is a plain str",
           isinstance(_DTYPE, str))
check_true("7b  ...and it is NOT a torch.dtype and NOT the 'torch.' spelling "
           "(non-degeneracy: deps.py must not import torch, so the constant "
           "has to be the string form transformers accepts)",
           bool(_DTYPE) and not _DTYPE.startswith("torch."))
check("7c  it is float32 today, which is what MedCPT's config.json declares "
      "and what transformers 4.x forced", _DTYPE, "float32")


def verify_dtype(reported, checkpoint="probe/checkpoint"):
    """Drive the dtype verifier; convert a RAISE into a comparable value.

    Same argument as verify() above: the mismatch arm raises BY DESIGN, and a
    bare call lets it escape through check()'s argument list and abort the run.
    """
    class _Model:
        pass
    m = _Model()
    if reported is not _ABSENT:
        m.dtype = reported
    try:
        return deps._verify_cross_encoder_dtype(m, checkpoint)
    except deps.CrossEncoderDtypeMismatchError as exc:
        return ("RAISED", str(exc))
    except Exception as exc:                        # noqa: BLE001
        return ("RAISED-WRONG-TYPE", type(exc).__name__, str(exc))

# --- the three non-raising arms -------------------------------------------
_before = dict(deps.CROSS_ENCODER_DTYPE_DEGRADATIONS)

check("7d  a bare 'float32' matching the constant VERIFIES",
      verify_dtype("float32"), deps.DTYPE_VERIFIED)
check("7e  ...and so does the torch spelling of the same fact, because "
      "str(torch.float32) is 'torch.float32' and comparing them as written "
      "would report a mismatch that is not one",
      verify_dtype("torch.float32"), deps.DTYPE_VERIFIED)
check("7f  ...and neither of those moved the counter, because a verified "
      "precision is not a degradation",
      counter_delta(_before, dict(deps.CROSS_ENCODER_DTYPE_DEGRADATIONS)), {})

_before = dict(deps.CROSS_ENCODER_DTYPE_DEGRADATIONS)
check("7g  a model that reports NO dtype is UNREPORTED, not a mismatch -- "
      "nobody can make a third-party object declare one, so it is counted "
      "(item 11a's line) rather than raised on",
      verify_dtype(_ABSENT), deps.DTYPE_UNREPORTED)
check("7h  ...counted under its own key",
      counter_delta(_before, dict(deps.CROSS_ENCODER_DTYPE_DEGRADATIONS)),
      {"unreported": 1})

_before = dict(deps.CROSS_ENCODER_DTYPE_DEGRADATIONS)
check("7i  an explicit None is the same fact as an absent attribute and must "
      "not be a mismatch against 'float32'",
      verify_dtype(None), deps.DTYPE_UNREPORTED)
check("7j  ...under the same key",
      counter_delta(_before, dict(deps.CROSS_ENCODER_DTYPE_DEGRADATIONS)),
      {"unreported": 1})

# --- the raising arm, in BOTH directions ----------------------------------
#
# A LOWER precision is the silent one (the ranking degrades and nothing says
# so) and a HIGHER one means the request was ignored; both are the same defect
# -- transformers returned something other than what it was asked for -- so
# both raise. Driving only one would leave a one-sided `<` or `>` passing.
_before = dict(deps.CROSS_ENCODER_DTYPE_DEGRADATIONS)
check("7k  a LOWER precision RAISES rather than degrading the ranking silently",
      marker(verify_dtype("bfloat16")), "RAISED")
check("7l  a HIGHER precision RAISES too -- the defect is 'not what was asked "
      "for', not 'smaller than what was asked for'",
      marker(verify_dtype("float64")), "RAISED")
check("7m  ...and a MISMATCH moves no counter, because it raised "
      "(non-degeneracy for 7h/7j: if a mismatch also counted, those two "
      "checks would pass against a verifier that never raises)",
      counter_delta(_before, dict(deps.CROSS_ENCODER_DTYPE_DEGRADATIONS)), {})

_raise = verify_dtype("bfloat16")
check_true("7n  the raise NAMES both sides, so the message says what was asked "
           "for and what came back",
           isinstance(_raise, tuple) and _DTYPE in at(_raise, 1, "")
           and "bfloat16" in at(_raise, 1, ""))
check_true("7o  ...and it is a RuntimeError subclass, deliberately not a "
           "ValueError, so a stray `except ValueError` around a model load "
           "cannot eat it (the UnknownModelPricingError precedent)",
           issubclass(deps.CrossEncoderDtypeMismatchError, RuntimeError)
           and not issubclass(deps.CrossEncoderDtypeMismatchError, ValueError))

# --- the UNREADABLE arm, DRIVEN rather than declared -----------------------
#
# THE FIRST VERSION OF 7p UNIONED `DTYPE_UNREADABLE` INTO ITS EXPECTED SET BY
# HAND, because no input above produces it -- which is the vacuous shape this
# project forbids: a member of a closed vocabulary that nothing ever reaches is
# a member nobody has checked, and unioning it in makes the closure check agree
# with itself. It IS reachable, by an object whose str() is empty and by one
# whose __str__ raises, so it is driven.
class _EmptyDtype:
    def __str__(self):
        return ""


class _RaisingDtype:
    def __str__(self):
        raise RuntimeError("this object refuses to describe itself")


_before = dict(deps.CROSS_ENCODER_DTYPE_DEGRADATIONS)
check("7p  a dtype whose str() is EMPTY is UNREADABLE, not a mismatch against "
      "'float32' -- an unreadable answer and a wrong answer have different "
      "remedies", verify_dtype(_EmptyDtype()), deps.DTYPE_UNREADABLE)
check("7q  ...counted under a key that NAMES THE TYPE, so the record says what "
      "could not be read rather than only that something could not",
      counter_delta(_before, dict(deps.CROSS_ENCODER_DTYPE_DEGRADATIONS)),
      {"unreadable:_EmptyDtype": 1})

_before = dict(deps.CROSS_ENCODER_DTYPE_DEGRADATIONS)
check("7r  an object whose __str__ RAISES is also UNREADABLE and does not "
      "escape the verifier -- a model load must not die because an attribute "
      "declined to render", verify_dtype(_RaisingDtype()),
      deps.DTYPE_UNREADABLE)
check("7s  ...under its own type-named key",
      counter_delta(_before, dict(deps.CROSS_ENCODER_DTYPE_DEGRADATIONS)),
      {"unreadable:_RaisingDtype": 1})

check("7t  DTYPE_VERIFICATION_STATES is CLOSED and is exactly the set of "
      "values the verifier can return, EVERY ONE OF THEM DRIVEN above, so a "
      "caller may branch on it exhaustively",
      sorted(set(deps.DTYPE_VERIFICATION_STATES)),
      sorted({marker(verify_dtype(x))
              for x in ("float32", "torch.float32", None, _ABSENT,
                        _EmptyDtype(), _RaisingDtype())}))

# --- the STRUCTURAL half: the constant is PASSED, not merely declared ------
#
# Everything above drives a pure function with a fabricated model. None of it
# can see a factory that stopped handing the constant to from_pretrained, which
# is the edit that would make all of it decorative -- the verifier would go on
# comparing "float32" against whatever "auto" happened to resolve to, and would
# agree with it on this checkpoint forever. So the call site is pinned by AST.
_DEPS_SRC = os.path.abspath(deps.__file__)
_deps_tree = ast.parse(open(_DEPS_SRC, encoding="utf-8").read(), _DEPS_SRC)
_dtype_kwargs = []
for _node in ast.walk(_deps_tree):
    if (isinstance(_node, ast.Call) and isinstance(_node.func, ast.Attribute)
            and _node.func.attr == "from_pretrained"):
        for _kw in _node.keywords:
            if _kw.arg == "dtype":
                _v = _kw.value
                _dtype_kwargs.append(
                    _v.attr if isinstance(_v, ast.Attribute)
                    else _v.id if isinstance(_v, ast.Name)
                    else f"<{type(_v).__name__}>")

check("7u  the weights load is handed CROSS_ENCODER_DTYPE by name, so the "
      "precision is a decision this project makes rather than one it inherits "
      "from the checkpoint's config.json",
      _dtype_kwargs, ["CROSS_ENCODER_DTYPE"])
check_true("7v  ...exactly ONE from_pretrained call passes it -- the tokenizer "
           "has no dtype and a `dtype=` there would be a request nothing reads "
           "(non-degeneracy for 7q: an empty list also 'contains no wrong "
           "name')", len(_dtype_kwargs) == 1)

_verify_calls = sorted(
    _n.func.id for _n in ast.walk(_deps_tree)
    if isinstance(_n, ast.Call) and isinstance(_n.func, ast.Name)
    and _n.func.id.startswith("_verify_cross_encoder"))
check("7w  ...and ALL THREE verifiers are actually called from the factories "
      "-- the sequence limit twice (tokenizer and weights), the REVISION twice "
      "(same two, and it is the weights half that reports on this checkpoint), "
      "and the dtype once (the tokenizer has no precision to check)",
      _verify_calls,
      ["_verify_cross_encoder_dtype",
       "_verify_cross_encoder_revision",
       "_verify_cross_encoder_revision",
       "_verify_cross_encoder_sequence_limit",
       "_verify_cross_encoder_sequence_limit"])

# --- the counter has a reader, same as section 6 ---------------------------
check("7x  CROSS_ENCODER_DTYPE_DEGRADATIONS is in the run-end registry",
      "CROSS_ENCODER_DTYPE_DEGRADATIONS" in degradation.registered_names(),
      True)
check_true("7y  ...and the registry holds the SAME Counter OBJECT, not a "
           "snapshot",
           degradation._REGISTRY.get("CROSS_ENCODER_DTYPE_DEGRADATIONS")
           is deps.CROSS_ENCODER_DTYPE_DEGRADATIONS)

_snap7 = degradation.snapshot()
check_true("7z  the counter this section moved appears in the run-end snapshot "
           "(non-degeneracy for 7t/7u)",
           sum(_snap7.get("CROSS_ENCODER_DTYPE_DEGRADATIONS", {}).values()) > 0)

_text7 = "\n".join(degradation.report_lines(_snap7))
check_true("7z-b ...and the printed report names it and says what a non-zero "
           "means", "CROSS_ENCODER_DTYPE_DEGRADATIONS" in _text7
           and "UNVERIFIED" in _text7)

# --- the two log fields are allowlisted, or the record says nothing --------
#
# MEASURED RATHER THAN ASSUMED, and it is why this check is here: the first
# version of the verifier emitted these two fields and observability's
# formatter DROPPED both, reporting `dropped_fields` on the record. That is the
# allowlist working, and it means a verifier whose fields are not allowlisted
# logs "verified" with nothing saying what was verified against what.
check("7z-c dtype_configured and dtype_reported are on LOGGABLE_FIELDS, so the "
      "record carries both sides of the comparison rather than being filtered "
      "down to a bare status",
      sorted(f for f in ("dtype_configured", "dtype_reported")
             if f in observability.LOGGABLE_FIELDS),
      ["dtype_configured", "dtype_reported"])


# ===========================================================================
# REPORT
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
