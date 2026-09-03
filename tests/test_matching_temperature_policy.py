######################################################################
# The Stage 5 temperature rule: declared per arm, sent, or recorded
######################################################################

"""Matching Temperature Policy Test

THE RULE THIS FILE HOLDS. ``config.MATCHING_TEMPERATURE`` is 0.0 -- a
PIPELINE-level determinism setting rather than a per-provider one -- and Stage 5
requests it on every arm whose model accepts the parameter, OMITS it where the
model does not, and RECORDS which of the two happened. What decides that is a
DECLARED capability (``config.MATCHING_TEMPERATURE_MODEL_ACCEPTS``), never a
try-and-catch of a 400: a discovery mechanism that costs a signed request cannot
tell a model restriction from a throttled account, and on the shipped per-trial
arm the first request of a patient is the cache warmup, whose failure fails the
patient rather than degrading.

WHY A FILE OF ITS OWN RATHER THAN THREE ADDITIONS TO THREE ADAPTER TESTS. The
rule has ONE owner -- four functions in ``oncotriage/config.py`` -- and three
kinds of consumer: the two request builders, the three degradation records, and
the durable provenance (the resume fingerprint, the run row, the tracking index,
the fixture block). A per-adapter file can measure its own arm's WIRE and
nothing else; the thing most worth failing on is the OWNER answering
inconsistently across arms, which no per-arm file can see. The wire itself is
still pinned where it belongs: ``tests/test_agent_bedrock_anthropic_adapter.py``
section 3 for the arm that sends it, ``tests/test_agent_bedrock_adapter.py``
sections 1 and 1e for the arm that drops and counts it.

WHAT IS DRIVEN RATHER THAN READ. Every capability answer is taken from the live
owner with ``config.MATCHING_PROVIDER`` really set, inside try/finally with the
restore asserted; the value guard is driven over a table of inputs, which is the
natural control for a pure function of its argument; the OpenAI arm's counter is
driven through the REAL ``call_matching_model`` with a recording stand-in
installed through ``oncotriage/agent/deps.py``; and the fingerprint field is
taken from the real ``run_fingerprint`` helper with the collection resolver
replaced, so no index is probed.

NO NETWORK, NO KEYS, **NO SPEND** -- no provider client of any kind is built:
the one call site driven here is handed a recorder through the deps seam, and
``deps.peek`` is asserted UNSET at the end so a real client that had been built
would be caught. NO MODEL LOAD (``ONCOTRIAGE_DEFER_LOCAL_MODELS`` is set above
the imports, and ``torch``/``transformers`` are asserted absent at the end), no
live Qdrant, no corpus, no database, no git history, no live server. It writes
NOTHING anywhere, not even a temp directory.

NOT in ``tests/run_serial_tests.py``'s collision matrix, derived rather than
assumed: it writes no file, and the two repository files it READS
(``oncotriage/config.py`` and ``oncotriage/agent/evaluation.py``) are compared
by sha256 at the end -- the first of them IS rewritten in place by
``tests/test_config_snapshot_date_rot.py``, so an interleaved serial run is
visible here rather than silent. It EXECS NOTHING and loads no module by
location: every control is a different INPUT to a pure function, or a module
attribute rebound inside try/finally with the restore asserted.

    python tests/test_matching_temperature_policy.py
"""

import hashlib
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
from oncotriage import degradation
from oncotriage import run_fingerprint as _fp
from oncotriage.agent import bedrock_adapter as _ba
from oncotriage.agent import bedrock_anthropic_adapter as _bac
from oncotriage.agent import deps
from oncotriage.agent import evaluation as _ev
from oncotriage.storage import database_logger as _dl


_PKG = os.path.dirname(os.path.abspath(oncotriage.__file__))
_WATCHED = {
    "config.py": os.path.join(_PKG, "config.py"),
    "agent/evaluation.py": os.path.join(_PKG, "agent", "evaluation.py"),
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


class _Rebound:
    """Set config attributes for a block and put them back. Restore asserted."""

    def __init__(self, **values):
        self._values = values
        self._saved = {}

    def __enter__(self):
        for k, v in self._values.items():
            self._saved[k] = getattr(config, k)
            setattr(config, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            setattr(config, k, v)
        return False


_AT_IMPORT = {
    "MATCHING_PROVIDER": config.MATCHING_PROVIDER,
    "MATCHING_TEMPERATURE": config.MATCHING_TEMPERATURE,
    "BEDROCK_ANTHROPIC_THINKING": config.BEDROCK_ANTHROPIC_THINKING,
}


# ===========================================================================
# SECTION 1 -- THE CONSTANT AND ITS GUARD
# ===========================================================================

section("1. the constant, and the guard that refuses a value it could not send")

check("1a MATCHING_TEMPERATURE is 0.0 -- the shipped determinism rule. THIS IS "
      "THE ONE CHECK IN THIS FILE THAT PINS THE DECISION rather than the "
      "mechanism, and it is the one to read first when it fails: everything "
      "else here derives from the owner and would follow a deliberate change, "
      "so a failure here means the DECISION moved",
      config.MATCHING_TEMPERATURE, 0.0)
check("1a-i ...and it is a float rather than an int, so the value that reaches "
      "a request and the value that reaches a record are the same type",
      isinstance(config.MATCHING_TEMPERATURE, float), True)

# THE GUARD IS DRIVEN OVER A TABLE, which is the natural control for a pure
# function of its argument -- and it is what makes an import-time check
# exercisable at all without exec'ing a patched copy of a 4,000-line module.
_GUARD_CASES = (
    # (value, must it raise, why)
    (None, False, "the declared opt-out: send it nowhere"),
    (0.0, False, "the shipped value"),
    (config.MATCHING_TEMPERATURE_MIN, False, "the floor, inclusive"),
    (config.MATCHING_TEMPERATURE_MAX, False, "the ceiling, inclusive"),
    (0, False, "an int is a number and is accepted"),
    (-0.001, True, "below the floor"),
    (1.0001, True, "above the ceiling"),
    (2.0, True, "legal on OpenAI's chat parameter and NOT on Anthropic's, "
                "which is exactly what bounding at the intersection means"),
    (True, True, "a bool is not a temperature -- isinstance(True, int) is True, "
                 "so without the explicit exclusion it would sail through as "
                 "1.0, the provider default silently claimed as a choice"),
    (False, True, "the same trap at the other end: False would read as 0.0, "
                  "which is the RIGHT number for the WRONG reason"),
    ("0.0", True, "a string that looks like the value"),
)
for _value, _must_raise, _why in _GUARD_CASES:
    _outcome = guarded(config.validate_matching_temperature, _value)
    check(f"1b validate_matching_temperature({_value!r}) "
          f"{'REFUSES' if _must_raise else 'accepts'} -- {_why}",
          isinstance(_outcome, str) and _outcome.startswith("<RAISED"),
          _must_raise)

check("1b-i ...and every refusal is a RuntimeError, not a ValueError -- a "
      "stray `except ValueError` around a model call must not eat it",
      sorted({guarded(config.validate_matching_temperature, v).split(" ")[1]
              .rstrip(":")
              for v, must, _ in _GUARD_CASES if must}),
      ["RuntimeError"])
check("1b-ii ...and the refusal NAMES the constant an operator has to edit",
      all("MATCHING_TEMPERATURE" in guarded(
              config.validate_matching_temperature, v)
          for v, must, _ in _GUARD_CASES if must), True)
check("1b-iii the table is not degenerate: it drives both outcomes",
      (any(m for _, m, _ in _GUARD_CASES),
       any(not m for _, m, _ in _GUARD_CASES)), (True, True))
check("1c the SHIPPED constant passes its own guard (which is what the "
      "import-time call asserts, driven here rather than assumed)",
      guarded(config.validate_matching_temperature,
              config.MATCHING_TEMPERATURE), None)


# ===========================================================================
# SECTION 2 -- THE CAPABILITY, DECLARED PER ARM
# ===========================================================================

section("2. the capability is DECLARED per arm, and the owner answers for the "
        "LIVE one")

check("2a every provider in the closed vocabulary has a declared row -- TOTAL, "
      "so an arm added without one cannot fall through to a default (every "
      "available default is a claim: False silently stops asking for "
      "determinism, True sends a parameter nobody checked)",
      sorted(config.MATCHING_TEMPERATURE_MODEL_ACCEPTS),
      sorted(config.MATCHING_PROVIDERS))
check("2a-i ...and the vocabulary is non-empty, so 2a is not two empty sets",
      len(config.MATCHING_PROVIDERS) >= 3, True)
check("2a-ii every declared value is a real bool, not a truthy string",
      sorted({type(v).__name__
              for v in config.MATCHING_TEMPERATURE_MODEL_ACCEPTS.values()}),
      ["bool"])

check("2b the two GPT-5.6 Terra arms declare NO -- probed live 2026-08-04, and "
      "the restriction is the MODEL's, so the endpoint does not change it",
      (config.MATCHING_TEMPERATURE_MODEL_ACCEPTS[
           config.MATCHING_PROVIDER_OPENAI],
       config.MATCHING_TEMPERATURE_MODEL_ACCEPTS[
           config.MATCHING_PROVIDER_BEDROCK]),
      (False, False))
check("2b-i and the Converse arm declares YES -- inferenceConfig.temperature is "
      "a modeled member of that request shape",
      config.MATCHING_TEMPERATURE_MODEL_ACCEPTS[
          config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC], True)
check("2b-ii ...so the declaration is not uniform, which is what makes every "
      "per-arm check below non-vacuous",
      len(set(config.MATCHING_TEMPERATURE_MODEL_ACCEPTS.values())), 2)

# --- the owner, driven per arm --------------------------------------------
_PER_ARM = {}
for _arm in config.MATCHING_PROVIDERS:
    with _Rebound(MATCHING_PROVIDER=_arm):
        _PER_ARM[_arm] = (guarded(config.matching_temperature_capability),
                          guarded(config.matching_temperature_sent),
                          guarded(config.matching_temperature_record))

check("2c the owner answers `supported` on exactly the arms that declare YES, "
      "and `model_rejects_parameter` on the rest",
      {a: v[0] for a, v in _PER_ARM.items()},
      {a: (config.MATCHING_TEMPERATURE_SUPPORTED
           if config.MATCHING_TEMPERATURE_MODEL_ACCEPTS[a]
           else config.MATCHING_TEMPERATURE_MODEL_REJECTS)
       for a in config.MATCHING_PROVIDERS})
check("2c-i every answer is a member of the closed vocabulary",
      sorted({v[0] for v in _PER_ARM.values()}
             - set(config.MATCHING_TEMPERATURE_CAPABILITIES)), [])
check("2d ...and what is SENT follows it: the value on the arms that can take "
      "it, None -- the field OMITTED -- on the arms that cannot",
      {a: v[1] for a, v in _PER_ARM.items()},
      {a: (config.MATCHING_TEMPERATURE
           if config.MATCHING_TEMPERATURE_MODEL_ACCEPTS[a] else None)
       for a in config.MATCHING_PROVIDERS})
check("2d-i and the RECORD spells both states one way: the repr of the float, "
      "or the documented sentinel",
      {a: v[2] for a, v in _PER_ARM.items()},
      {a: (repr(float(config.MATCHING_TEMPERATURE))
           if config.MATCHING_TEMPERATURE_MODEL_ACCEPTS[a]
           else config.MATCHING_TEMPERATURE_NOT_SENT)
       for a in config.MATCHING_PROVIDERS})
check("2d-ii ...and the record is a string on every arm, because it is stored "
      "in a TEXT column and compared with != by the resume gate",
      sorted({type(v[2]).__name__ for v in _PER_ARM.values()}), ["str"])
check("2d-iii ...and the two states are DIFFERENT strings (non-degeneracy: "
      "with one arm's answer equal to the other's, 2d-i would pass for free)",
      len({v[2] for v in _PER_ARM.values()}), 2)

# MATCHING_TEMPERATURE IS FORCED HERE RATHER THAN INHERITED, and that is the
# check's own premise made explicit: `matching_temperature_sent()` answers None
# for the OPT-OUT before it ever asks which provider is live -- correctly, since
# "send nothing anywhere" is true on any arm -- so with the constant at None
# 2e-i would be asserting about a branch it never reached.
with _Rebound(MATCHING_PROVIDER="not-a-provider", MATCHING_TEMPERATURE=0.0):
    _unknown_capability = guarded(config.matching_temperature_capability)
    _unknown_sent = guarded(config.matching_temperature_sent)
check("2e an unrecognised provider RAISES rather than getting a default -- a "
      "temperature decision made for a provider nobody recognises is the "
      "silent-wrong-provider failure the closed vocabulary exists to prevent",
      str(_unknown_capability).startswith("<RAISED RuntimeError"), True)
check("2e-i ...and the refusal reaches the SENT answer too, so a builder "
      "asking what to put on the wire cannot get None (which reads as 'omit "
      "it') for a provider that was never recognised",
      str(_unknown_sent).startswith("<RAISED RuntimeError"), True)
check("2e-ii ...and the provider was put back after that probe",
      config.MATCHING_PROVIDER, _AT_IMPORT["MATCHING_PROVIDER"])


# ===========================================================================
# SECTION 3 -- THE TWO WAYS NOTHING IS SENT, WHICH ARE DIFFERENT FINDINGS
# ===========================================================================

section("3. the opt-out and the thinking mode: two Nones, two meanings")

with _Rebound(MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC,
              MATCHING_TEMPERATURE=None):
    _optout = (guarded(config.matching_temperature_capability),
               guarded(config.matching_temperature_sent),
               guarded(config.matching_temperature_record))
check("3a the OPT-OUT sends nothing even on the arm that could take it -- "
      "MATCHING_TEMPERATURE = None is the documented pre-determinism behaviour, "
      "kept",
      _optout[1], None)
check("3a-i ...and the ARM's capability is UNCHANGED by it. The opt-out is the "
      "operator's answer and the capability is the judge's; collapsing them "
      "would make the degradation record unable to say which happened",
      _optout[0], config.MATCHING_TEMPERATURE_SUPPORTED)
check("3a-ii ...and the record reads the documented sentinel, so a run row "
      "says `not_sent` rather than NULL -- which is what distinguishes it from "
      "a row written before the column existed",
      _optout[2], config.MATCHING_TEMPERATURE_NOT_SENT)

with _Rebound(MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC,
              BEDROCK_ANTHROPIC_THINKING="adaptive"):
    _thinking = (guarded(config.matching_temperature_capability),
                 guarded(config.matching_temperature_sent))
check("3b EXTENDED THINKING FLIPS THE CAPABILITY OFF: with thinking on the "
      "provider fixes its own sampling, so a temperature would be recorded and "
      "ignored -- a record that disagrees with what happened",
      _thinking[0], config.MATCHING_TEMPERATURE_THINKING_ENABLED)
check("3b-i ...and nothing is sent",
      _thinking[1], None)
for _mode in (None, "disabled"):
    with _Rebound(
            MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC,
            BEDROCK_ANTHROPIC_THINKING=_mode):
        _off = guarded(config.matching_temperature_capability)
    check(f"3b-ii thinking={_mode!r} does NOT flip it -- so 3b is about the "
          f"MODE rather than about the constant being read at all, and the "
          f"two ways of saying 'no extended thinking' agree",
          _off, config.MATCHING_TEMPERATURE_SUPPORTED)

with _Rebound(MATCHING_PROVIDER=config.MATCHING_PROVIDER_OPENAI,
              BEDROCK_ANTHROPIC_THINKING="adaptive"):
    _other_arm = guarded(config.matching_temperature_capability)
check("3c the thinking mode does NOT reach an arm it is not a setting of. The "
      "model declaration is asked FIRST, so an arm whose model rejects the "
      "parameter reports THAT rather than a Converse-only knob -- which is what "
      "sends a reader to the judge instead of to a setting that changes nothing "
      "for them",
      _other_arm, config.MATCHING_TEMPERATURE_MODEL_REJECTS)


# ===========================================================================
# SECTION 4 -- THE RECORD: THREE COUNTERS, ONE FOR EACH ARM THAT DROPS
# ===========================================================================

section("4. every arm that drops the temperature RECORDS that it did")

check("4a the two Bedrock arms and the OpenAI arm each declare the SAME key, "
      "so a run-end report groups the finding under one name whichever arm was "
      "live",
      sorted({_ba.DEGRADATION_TEMPERATURE_DROPPED,
              _bac.DEGRADATION_TEMPERATURE_DROPPED,
              _ev.DEGRADATION_TEMPERATURE_DROPPED}),
      ["temperature_not_expressible"])
check("4a-i ...and each is in its own module's closed vocabulary, so a typo at "
      "a bump site is catchable rather than producing a counter nobody reads",
      (_ba.DEGRADATION_TEMPERATURE_DROPPED in _ba.DEGRADATION_KEYS,
       _bac.DEGRADATION_TEMPERATURE_DROPPED in _bac.DEGRADATION_KEYS,
       _ev.DEGRADATION_TEMPERATURE_DROPPED
       in _ev.OPENAI_PARAMETER_DEGRADATION_KEYS),
      (True, True, True))
check("4b all three counters are on the run-end degradation report -- a "
      "counter with no reader is the defect oncotriage/degradation.py exists "
      "to remove",
      sorted(n for n in ("BEDROCK_ADAPTER_DEGRADATIONS",
                         "BEDROCK_ANTHROPIC_DEGRADATIONS",
                         "OPENAI_PARAMETER_DEGRADATIONS")
             if n in degradation.registered_names()),
      ["BEDROCK_ADAPTER_DEGRADATIONS", "BEDROCK_ANTHROPIC_DEGRADATIONS",
       "OPENAI_PARAMETER_DEGRADATIONS"])


# --- the OpenAI arm, driven through the REAL call --------------------------
#
# NO CLIENT IS BUILT. The recorder is installed through the deps seam, which is
# what `call_matching_model` resolves through; `deps.peek` is asserted UNSET at
# the end of this file, so a real client that HAD been built would be caught.
class _Recorder:
    def __init__(self):
        self.calls = []

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return "REPLY"


_rec = _Recorder()
_saved_override = deps.set_overrides({deps.OPENAI_CLIENT: _rec})
_saved_warned = _ev._TEMPERATURE_WARNED
_before = _ev.OPENAI_PARAMETER_DEGRADATIONS[_ev.DEGRADATION_TEMPERATURE_DROPPED]
try:
    _ev._TEMPERATURE_WARNED = False            # a fresh process, for one block
    with _Rebound(MATCHING_PROVIDER=config.MATCHING_PROVIDER_OPENAI):
        guarded(_ev.call_matching_model, "SYS", "USR")
        _after_one = _ev.OPENAI_PARAMETER_DEGRADATIONS[
            _ev.DEGRADATION_TEMPERATURE_DROPPED]
        guarded(_ev.call_matching_model, "SYS", "USR")
        _after_two = _ev.OPENAI_PARAMETER_DEGRADATIONS[
            _ev.DEGRADATION_TEMPERATURE_DROPPED]
        _sent_kwargs = dict(_rec.calls[0]) if _rec.calls else {}
    # THE OPT-OUT ARM, on the same recorder and the same fresh flag.
    _ev._TEMPERATURE_WARNED = False
    _before_optout = _ev.OPENAI_PARAMETER_DEGRADATIONS[
        _ev.DEGRADATION_TEMPERATURE_DROPPED]
    with _Rebound(MATCHING_PROVIDER=config.MATCHING_PROVIDER_OPENAI,
                  MATCHING_TEMPERATURE=None):
        guarded(_ev.call_matching_model, "SYS", "USR")
        _after_optout = _ev.OPENAI_PARAMETER_DEGRADATIONS[
            _ev.DEGRADATION_TEMPERATURE_DROPPED]
        _optout_calls = len(_rec.calls)
finally:
    _ev._TEMPERATURE_WARNED = _saved_warned
    deps.restore_overrides(_saved_override)

check("4c the first OpenAI CALL records the dropped temperature -- the "
      "pipeline asked for one and this model will not take it, and that is "
      "never silent",
      _after_one, _before + 1)
check("4c-i ...and the second does not double-count a fact about the "
      "CONFIGURATION: 1 says everything 45,000 would, and a counter guaranteed "
      "non-zero per call makes the run-end report's CLEAN line worthless",
      _after_two, _after_one)
check("4c-ii ...and the request that carried the drop went out unchanged -- "
      "NO temperature kwarg, which is what lets the twelve characterization "
      "fixtures replay without recapture",
      "temperature" in _sent_kwargs, False)
check("4c-iii ...and it really was a request rather than a call that never "
      "happened (non-degeneracy)",
      sorted(_sent_kwargs) if _sent_kwargs else [],
      ["max_completion_tokens", "messages", "model", "reasoning_effort",
       "response_format", "seed", "timeout"])
check("4d the declared OPT-OUT records NOTHING -- an operator who asked for no "
      "temperature has degraded nothing, and a counter that moved anyway would "
      "make this key mean 'this arm was live' rather than 'something was lost'",
      _after_optout, _before_optout)
check("4d-i ...and the request still went out under it, so 4d is a silent "
      "counter rather than a call that did not happen",
      _optout_calls > 1, True)


# --- the Converse arm's conditional drop -----------------------------------
#
# DRIVEN THROUGH THE REAL WARN-ONCE rather than through a call, because that
# function is where the counter is bumped and it needs no client -- the request
# builder is documented PURE and deliberately moves no counter, which is
# asserted below. Three arms of one condition: thinking ON (counted), thinking
# off (silent), and the opt-out (silent).
def _converse_drop(**knobs):
    """Reset the once-flag, drive the real warn, return the counter delta."""
    saved_flag = (_bac._DROPPED_WARNED, _bac._TEMPERATURE_WARNED)
    before = _bac.BEDROCK_ANTHROPIC_DEGRADATIONS[
        _bac.DEGRADATION_TEMPERATURE_DROPPED]
    try:
        # BOTH LATCHES, because they are separate on this arm on purpose -- see
        # `_warn_temperature_dropped_once`. Resetting only the shared one would
        # make every case below report 0 and the section pass for free.
        _bac._DROPPED_WARNED = False           # a fresh process, for one block
        _bac._TEMPERATURE_WARNED = False
        with _Rebound(
                MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC,
                **knobs):
            guarded(_bac._warn_dropped_parameters_once)
            return (_bac.BEDROCK_ANTHROPIC_DEGRADATIONS[
                _bac.DEGRADATION_TEMPERATURE_DROPPED] - before)
    finally:
        _bac._DROPPED_WARNED, _bac._TEMPERATURE_WARNED = saved_flag

check("4e the Converse arm counts the drop WHEN THINKING IS ON -- the one "
      "state in which this arm asks for a temperature and cannot carry it",
      _converse_drop(BEDROCK_ANTHROPIC_THINKING="adaptive"), 1)
check("4e-i ...and is SILENT with thinking off, which is the shipped "
      "configuration: zero here means 'the arm sent what the pipeline asked "
      "for', not 'nobody looked'",
      _converse_drop(BEDROCK_ANTHROPIC_THINKING="disabled"), 0)
check("4e-ii ...and silent under the declared opt-out even with thinking on, "
      "because nothing was asked for and therefore nothing was lost",
      _converse_drop(BEDROCK_ANTHROPIC_THINKING="adaptive",
                     MATCHING_TEMPERATURE=None), 0)

_before_pure = _bac.BEDROCK_ANTHROPIC_DEGRADATIONS[
    _bac.DEGRADATION_TEMPERATURE_DROPPED]
with _Rebound(MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC,
              BEDROCK_ANTHROPIC_THINKING="adaptive"):
    guarded(_bac.build_converse_request, "S", "U")
check("4e-iii build_converse_request stays PURE: building the request that "
      "DROPS the temperature moves no counter -- the drop is a property of the "
      "configuration and is counted once, not once per request",
      _bac.BEDROCK_ANTHROPIC_DEGRADATIONS[
          _bac.DEGRADATION_TEMPERATURE_DROPPED],
      _before_pure)


# ===========================================================================
# SECTION 5 -- THE DURABLE RECORD
# ===========================================================================

section("5. the fingerprint, the column and the run row all spell it one way")

check("5a the stamp GATES it: a resume across a temperature change refuses "
      "rather than putting two sampling regimes in one artifact",
      "matching_temperature_sent" in _fp.FINGERPRINT_FIELDS, True)
check("5a-i ...and the version was bumped with it, which is what makes an "
      "older artifact answer FP_VERSION once instead of comparing a missing "
      "field against a live value",
      _fp.FINGERPRINT_VERSION >= 7, True)

_saved_resolver = _fp._resolve_collection
try:
    # THE COLLECTION RESOLVER IS REPLACED so `current()` probes no index. That
    # is the only reason this section can run offline; the field under test is
    # a pure config read either way.
    _fp._resolve_collection = lambda: ("stub-collection", 1)
    _fp.clear_cache()
    _stamp = guarded(_fp.current)
finally:
    _fp._resolve_collection = _saved_resolver
    _fp.clear_cache()
check("5b the live stamp carries the OWNER's answer, not a second read of "
      "MATCHING_TEMPERATURE",
      (_stamp or {}).get("matching_temperature_sent")
      if isinstance(_stamp, dict) else _stamp,
      config.matching_temperature_record())
check("5b-i ...and the resolver was put back",
      _fp._resolve_collection is _saved_resolver, True)
check("5c the run row has a column of the same NAME, so a stamp, a row and a "
      "tracking parameter cannot spell one fact three ways",
      "matching_temperature_sent" in _dl.RUN_FINGERPRINT_COLUMNS, True)
check("5c-i ...declared TEXT, because the column must hold a number AND the "
      "documented `not_sent` -- and the two candidate ways to do that in a "
      "REAL column are both worse (NULL collides with 'this row predates the "
      "column', and a sentinel number is a temperature nobody asked for)",
      _dl.RUN_COLUMN_ADDITIONS.get("matching_temperature_sent"), "TEXT")
check("5c-ii ...and it is NOT in RUN_FINGERPRINT_INTEGER_COLUMNS, whose int "
      "guard would blank exactly the sentinel this column exists to record",
      "matching_temperature_sent" in _dl.RUN_FINGERPRINT_INTEGER_COLUMNS,
      False)
check("5d the stamp's field order and the column order agree, so the INSERT "
      "fills the column it names",
      list(_dl.RUN_FINGERPRINT_COLUMNS),
      ["fingerprint_version"] + list(_fp.FINGERPRINT_FIELDS))


# ===========================================================================
# SECTION 6 -- NOTHING WAS LEFT BEHIND
# ===========================================================================

section("6. restores, and the no-spend tripwire")

for _name, _was in _AT_IMPORT.items():
    check(f"6a config.{_name} is back to what it was at import",
          getattr(config, _name), _was)

check("6a-i ...and the capture it is compared against is non-degenerate",
      len(_AT_IMPORT) >= 3, True)

check("6b no deps override was left installed",
      sorted(k for k in deps.OVERRIDE_KEYS if deps.peek(k) is not deps.UNSET),
      [])
check("6b-i NO OpenAI client was ever BUILT -- asked through the NON-BUILDING "
      "diagnostic, because calling the accessor would construct the very "
      "client this asserts was not built. THE SPEND TRIPWIRE: a real client "
      "here means the recorder was bypassed and a request could have been "
      "issued",
      deps.is_resolved(deps.OPENAI_CLIENT), False)
check("6b-ii ...and no Bedrock client of either kind either",
      (deps.is_resolved(deps.BEDROCK_CLIENT),
       deps.is_resolved(deps.BEDROCK_ANTHROPIC_CLIENT)), (False, False))

check("6c no model was loaded",
      sorted(m for m in ("torch", "transformers") if m in sys.modules), [])

for _label, _path in _WATCHED.items():
    check(f"6d {_label} is byte-identical -- this file rebinds attributes, "
          f"never source",
          hashlib.sha256(open(_path, "rb").read()).hexdigest(),
          _HASHES_AT_IMPORT[_label])
check("6d-i ...and the two hashes differ from each other, so 6d is not one "
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
