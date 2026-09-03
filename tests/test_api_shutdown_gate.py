# The API's Stage 5 shutdown gate, and the two halves it is made of
###################################################################

"""API Shutdown Gate Test

WHAT THIS COVERS, AND WHY IT IS TWO MECHANISMS RATHER THAN ONE.

`oncotriage/api/server.py` was the last Stage 5 caller with no shutdown gate.
`25- Batch Runner.py` and `26- Ablation Study.py` both ask Stage 5 to stop
issuing requests the moment a SIGTERM arrives; this service did not, so a
`docker stop` could SIGKILL it with up to three further full-price rounds still
to be issued -- 2400 s of billable work against a 620 s grace period, in either
arm. `docker-compose.yml` recorded that as a known, unfixed shortfall.

THE FIX IS A SIGNAL HANDLER **PLUS** A LIFESPAN HOOK, AND THE SIGNAL HALF IS
THE LOAD-BEARING ONE. A lifespan shutdown hook alone -- the obvious
implementation, and the one this pass was asked for -- does not bound anything,
and that is a fact about uvicorn rather than an opinion. Read `Server.shutdown()`
in uvicorn 0.40:

    for server in self.servers: server.close()
    for connection in ...: connection.shutdown()
    await asyncio.wait_for(self._wait_tasks_to_complete(), ...)   # <-- drains
    if not self.force_exit:
        await self.lifespan.shutdown()                            # <-- only now

`_wait_tasks_to_complete()` spins while an in-flight request task exists, so the
lifespan shutdown event is delivered AFTER the drain it was meant to bound --
and under `force_exit` it is never delivered at all. MEASURED, against a real
uvicorn with a real SIGTERM and a request genuinely in flight: with the signal
gate the flag flipped 0.012 s after the signal, mid-request; with the lifespan
hook alone the in-flight request polled for twelve seconds and never saw it.

SO THE LIFESPAN HALF IS NOT DECORATION EITHER. It covers every shutdown that
arrives WITHOUT a signal, which is a real set: a host setting
`Server.should_exit`, `uvicorn.Server.serve()` run with
`install_signal_handlers=False`, and -- the case that makes any of this
drivable in-process -- `starlette.testclient.TestClient`, which runs the
application on an anyio portal THREAD, where `signal.signal` raises
`ValueError` and no signal gate can be installed at all.

HOW THE TWO HALVES ARE DRIVEN, STATED PLAINLY BECAUSE THEY ARE DRIVEN
DIFFERENTLY.

    THE LIFESPAN HALF -- `fastapi.testclient.TestClient` as a CONTEXT MANAGER,
    which is starlette's own documented way to run the lifespan (entering it
    sends `lifespan.startup`, leaving it sends `lifespan.shutdown`). Section 4
    additionally ASSERTS that the portal really is a non-main thread, so the
    "and this is why the lifespan half exists" claim is measured rather than
    asserted about somebody else's implementation.

    THE SIGNAL HALF -- installed for real with `signal.signal`, then INVOKED
    DIRECTLY through `signal.getsignal(...)`. A real signal is deliberately not
    delivered: the handler chains to uvicorn's, and in a bare test process the
    previous disposition is `SIG_DFL`, so a real SIGTERM would terminate the
    run rather than measure it. Invoking the installed object exercises the
    same code the kernel would -- the flag, the announcement and the chain --
    and the chain is measured by installing a RECORDING handler underneath it
    first, which is stronger than watching a process die.

NO BILLED CALL, NO NETWORK, NO KEYS, NO LIVE SERVER, NO LIVE QDRANT, NO MODEL
LOAD, NO CORPUS, NO GIT HISTORY, NO DOCKER DAEMON. The graph is a stand-in and
the readiness probes are replaced, so the app starts without opening a client;
Stage 5 is driven with a counting stub installed through
`oncotriage/agent/deps.py` and the requests are recorded rather than sent. IT
EXECS NOTHING and loads no module by location, so it needs no `_EXEC_ALLOWLIST`
entry.

NOT IN `tests/run_serial_tests.py`'s COLLISION MATRIX, derived rather than
declared: it writes nothing in the repository -- every database is a scratch
file inside a `tempfile.mkdtemp` it removes and asserts gone -- and of the two
repository files it READS as text, `oncotriage/api/server.py` and
`18- FastAPI Server Test.py`, neither is written by either of the suite's two
writers. Both are sha256-compared at the end.

THE FLAG IS PROCESS-GLOBAL AND THIS FILE CLEANS UP AFTER ITSELF. Every scenario
that sets it clears it in a `finally`, and section 8 asserts the process ends
with the flag clear and no signal handler of this module's left installed --
because `pytest tests/` imports every module into ONE process, and a leaked flag
would make a later file's Stage 5 run fail with no request having been issued.

Run from terminal:
    python tests/test_api_shutdown_gate.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""

import ast
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import sys
import tempfile
import threading

# ABOVE THE PACKAGE IMPORTS ON PURPOSE. oncotriage/agent/deps.py reads this
# variable ONCE, at its own import; an assignment underneath the imports reaches
# nothing. Nothing here needs MedCPT or FastEmbed, and this is the second line
# of defence that says so.
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
from oncotriage import paths as _paths
from oncotriage.agent import deps
from oncotriage.agent import evaluation as _evaluation
from oncotriage.agent.evaluation import node_llm_classifier_evaluation

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


def guarded(fn, *args, **kwargs):
    """Call `fn` and convert a raise into a value `check` can FAIL on.

    A CHECK THAT ABORTS IS NOT A CHECK -- this project has shipped that shape
    fourteen times, each one a defect making the thing under test raise while
    `check()`'s argument was being evaluated, and each one reporting a single
    traceback where it owed a summary and every remaining result.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                               # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


def at(mapping, key, default="<MISSING>"):
    """`mapping[key]`, or a NAMED absence -- never an IndexError/KeyError."""
    if not isinstance(mapping, dict):
        return f"<NOT-A-DICT {mapping!r}>"
    return mapping.get(key, default)


#------------------------------------------------------------------------------


# ===========================================================================
# THE SCRATCH TREE, AND THE PATHS THAT MUST NOT REACH PRODUCTION
# ===========================================================================
#
# `paths._RESOLVED` IS SEEDED BEFORE ANYTHING IS BUILT, so nothing in this file
# -- including a defect in it -- can resolve to the production database. The
# same seam `tests/test_ablation_db_isolation.py` and
# `tests/test_dashboard_run_health.py` use, and it is restored in section 8.

_TMP = tempfile.mkdtemp(prefix="oncotriage-api-shutdown-gate-")
_RESOLVED_BEFORE = dict(_paths._RESOLVED)
_paths._RESOLVED["inferences_path"] = os.path.join(_TMP, "scratch.db")

_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(config.__file__)))
_SERVER_PY = os.path.join(_CODE_DIR, "oncotriage", "api", "server.py")
_FILE_18 = os.path.join(_CODE_DIR, "18- FastAPI Server Test.py")

for _needed in (_SERVER_PY, _FILE_18):
    if not os.path.isfile(_needed):
        # A HARD GUARD, NOT A check(): a wrong root is not one failure but
        # every failure, each with a misleading message.
        raise SystemExit(f"[API gate] not found: {_needed}")

_SERVER_TEXT = open(_SERVER_PY, encoding="utf-8").read()
_FILE_18_TEXT = open(_FILE_18, encoding="utf-8").read()
_SERVER_SHA = hashlib.sha256(_SERVER_TEXT.encode("utf-8")).hexdigest()
_FILE_18_SHA = hashlib.sha256(_FILE_18_TEXT.encode("utf-8")).hexdigest()

import oncotriage.api.server as server                          # noqa: E402


# ── THE APPLICATION UNDER TEST, WITH NOTHING EXPENSIVE IN ITS LIFESPAN ─────
#
# THE MODULE'S OWN `app` IS DELIBERATELY NOT USED. Its lifespan compiles the
# real graph and runs the real readiness probes, which opens a Qdrant client
# and reads the MeSH lookups -- a network call and a licence-gated file, in a
# test that promises neither. `create_app()` is the factory this module exists
# to provide for exactly that reason, and the three stand-ins below are
# installed on the MODULE, which is where `lifespan` resolves them.
#
# THE LIFESPAN ITSELF IS THE REAL ONE. Nothing about the gate is stubbed: the
# same `lifespan` function the deployed server runs is what these clients drive.

_REAL = {
    "build_matching_graph": server.build_matching_graph,
    "serving_readiness": server.serving_readiness,
    "probe_serving_database": server.probe_serving_database,
}

_READY = {"ready": True}


def _install_stand_ins():
    server.build_matching_graph = lambda: object()
    server.serving_readiness = lambda extra_checks=None: {
        "status": server.READY if _READY["ready"] else "not_ready",
        "checks": [{"name": "stub", "ok": _READY["ready"], "detail": "stub"}],
    }
    server.probe_serving_database = lambda: {
        "name": "inference database", "ok": True, "detail": "stub"}


def _restore_stand_ins():
    for name, real in _REAL.items():
        setattr(server, name, real)


_install_stand_ins()

from fastapi.testclient import TestClient                       # noqa: E402


def fresh_client():
    """A TestClient over a FRESH application built from the real factory."""
    return TestClient(server.create_app())


#------------------------------------------------------------------------------


# ===========================================================================
# 1.  PRECONDITIONS AND THE VOCABULARY
# ===========================================================================

section("1. Preconditions: the flag ships clear and the signal set is closed")

check("1a  the Stage 5 shutdown flag ships CLEAR, so nothing above this line "
      "has left one set and every measurement below is about this file",
      (_evaluation.stage5_shutdown_requested(),
       _evaluation.stage5_shutdown_reason()),
      (False, None))

check("1b  the gate names exactly SIGINT and SIGTERM -- SIGTERM is what "
      "`docker stop`, systemd and Kubernetes send FIRST, SIGINT is what a "
      "terminal sends",
      sorted(s.name for s in server.SHUTDOWN_GATE_SIGNALS),
      ["SIGINT", "SIGTERM"])

check("1c  the signal set is a closed TUPLE, so a caller may branch on it "
      "exhaustively and nothing can append to it at run time",
      isinstance(server.SHUTDOWN_GATE_SIGNALS, tuple), True)

check("1d  the gate is NOT armed at import -- importing this module must "
      "install no signal handler, on the package's standing rule that "
      "importing a module does nothing",
      server.shutdown_gate_armed(), False)

check("1e  ...and the degradation counter starts empty, so every key section "
      "4 reads was put there by this file",
      dict(server.SHUTDOWN_GATE_DEGRADATIONS), {})


#------------------------------------------------------------------------------


# ===========================================================================
# 2.  THE SIGNAL HALF: IT SETS THE FLAG AND IT CHAINS
# ===========================================================================
#
# THE PREVIOUS DISPOSITION IS INSTALLED BY THIS FILE, and that is what makes
# the chain measurable. In a bare process `signal.getsignal(SIGTERM)` is
# `SIG_DFL`, so a chain to it would terminate the run; installing a recording
# callable first reproduces the DEPLOYED shape -- where the previous handler is
# uvicorn's `Server.handle_exit` -- and lets the chain be counted rather than
# inferred from a process dying.

section("2. The signal half sets the flag and chains to what it replaced")

_chained = []


def _recording_previous(signum, frame):
    _chained.append(signum)


_saved_dispositions = {s: signal.getsignal(s)
                       for s in server.SHUTDOWN_GATE_SIGNALS}
try:
    for _s in server.SHUTDOWN_GATE_SIGNALS:
        signal.signal(_s, _recording_previous)

    server._install_shutdown_signal_gate()

    check("2a  the gate reports itself armed", server.shutdown_gate_armed(),
          True)
    check("2b  ...on both signals",
          sorted(s.name for s in server._PREVIOUS_SIGNAL_HANDLERS),
          ["SIGINT", "SIGTERM"])
    check("2c  ...and it really replaced the disposition (non-degeneracy: an "
          "install that did nothing would leave the recorder in place and "
          "make 2e pass for the wrong reason)",
          signal.getsignal(signal.SIGTERM) is _recording_previous, False)

    _installed = signal.getsignal(signal.SIGTERM)
    check("2d  the installed object is a callable the kernel could invoke",
          callable(_installed), True)

    # INVOKED DIRECTLY. See the module docstring for why a real signal is not
    # delivered: the chain would reach SIG_DFL in a bare process and kill the
    # run instead of measuring it.
    _r = guarded(_installed, int(signal.SIGTERM), None)

    check("2e  invoking it does not raise", _r, None)
    check("2f  ...it sets the flag",
          _evaluation.stage5_shutdown_requested(), True)
    check("2g  ...with a reason naming the SIGNAL, which is what tells an "
          "operator reading the shutdown line that the handler ran and the "
          "drain was bounded -- as opposed to the lifespan half, which fires "
          "after the drain",
          ("SIGTERM" in (_evaluation.stage5_shutdown_reason() or ""),
           "signal 15" in (_evaluation.stage5_shutdown_reason() or "")),
          (True, True))
    check("2h  ...and it CHAINED exactly once to what it replaced. Without "
          "this the server would set the flag and then never stop, trading a "
          "bounded drain for a container that has to be SIGKILLed",
          _chained, [int(signal.SIGTERM)])

    # THE FIRST REASON WINS, which is what stops a second signal arriving
    # during teardown from overwriting the diagnosis of the one being acted on.
    _first = _evaluation.stage5_shutdown_reason()
    _r2 = guarded(signal.getsignal(signal.SIGINT), int(signal.SIGINT), None)
    check("2i  a SECOND signal chains too, and keeps the FIRST reason",
          (_r2, _evaluation.stage5_shutdown_reason() == _first,
           _chained), (None, True, [int(signal.SIGTERM), int(signal.SIGINT)]))

    # IDEMPOTENCE. A second install must not capture its OWN handler as "the
    # previous one" -- that builds a chain that calls itself, and the symptom
    # is a RecursionError inside a signal handler.
    _before = dict(server._PREVIOUS_SIGNAL_HANDLERS)
    server._install_shutdown_signal_gate()
    check("2j  a second install is a no-op, so the gate cannot capture itself "
          "as the previous disposition and build a chain that recurses",
          server._PREVIOUS_SIGNAL_HANDLERS == _before, True)

    # RESTORE, BY IDENTITY.
    server._remove_shutdown_signal_gate()
    check("2k  removing the gate reports it disarmed",
          server.shutdown_gate_armed(), False)
    check("2l  ...and puts the previous disposition back on both signals",
          (signal.getsignal(signal.SIGTERM) is _recording_previous,
           signal.getsignal(signal.SIGINT) is _recording_previous),
          (True, True))

    # CONTROL: a restore must not clobber somebody ELSE's handler.
    def _third_party(signum, frame):
        pass

    server._install_shutdown_signal_gate()
    signal.signal(signal.SIGTERM, _third_party)
    server._remove_shutdown_signal_gate()
    check("2m  CONTROL: when a THIRD PARTY has replaced the handler since, the "
          "restore leaves theirs alone -- restoring over it would be worse "
          "than leaving ours in place, and identity is what distinguishes the "
          "two",
          signal.getsignal(signal.SIGTERM) is _third_party, True)
    check("2n  ...and the gate still reports itself disarmed, so a later "
          "install is not blocked by an entry it could no longer restore",
          server.shutdown_gate_armed(), False)
    # A PARTIAL ARM MUST NOT LEAVE A HANDLER NOBODY CAN RESTORE, and the
    # first version of the installer did exactly that: on the thread-wide
    # refusal it CLEARED its dictionary and returned, so a signal installed
    # before the failing one stayed live with the disposition it replaced
    # FORGOTTEN. Unreachable for that cause -- `signal.signal` refuses every
    # signal off the main thread -- and reachable for the per-signal `OSError`
    # branch, which is why the invariant is not left to depend on which
    # exception arrived. Found by reading this file's own code back, not by a
    # failing check, which is why the check exists now.
    #
    # THE SIGNAL MODULE IS SHIMMED ON THE SERVER MODULE, not patched globally:
    # `signal.signal` is process-wide state and a test that broke it for
    # everyone would be a worse defect than the one it measures.
    class _FailSecond:
        """Everything the installer reads, with the SECOND install raising."""

        SIG_DFL = signal.SIG_DFL
        SIG_IGN = signal.SIG_IGN
        Signals = signal.Signals

        def __init__(self):
            self.installed = []

        def getsignal(self, sig):
            return signal.getsignal(sig)

        def signal(self, sig, handler):
            # THE GATE'S OWN HANDLER IS WHAT FAILS, and a RESTORE is allowed
            # through. That models "the refusal arrived after one signal was
            # already armed" -- the state the unwind exists for -- rather than
            # "this thread cannot call signal.signal at all", where the unwind
            # could not run either and there would be nothing to measure.
            if getattr(handler, "__name__", "") == "_gate":
                if self.installed:
                    raise ValueError("signal only works in main thread")
                self.installed.append(sig)
            return signal.signal(sig, handler)

    _shim = _FailSecond()
    _real_signal_module = server.signal
    server.signal = _shim
    try:
        server._install_shutdown_signal_gate()
    finally:
        server.signal = _real_signal_module

    check("2t  a PARTIAL install leaves NOTHING armed -- neither a live "
          "handler with its predecessor forgotten, nor a dictionary entry "
          "that outlives it",
          (server.shutdown_gate_armed(),
           dict(server._PREVIOUS_SIGNAL_HANDLERS)), (False, {}))
    # A RESTORE THAT FAILS KEEPS ITS ENTRY, because the handler is still live
    # and its predecessor is still needed. Popping it would make
    # `shutdown_gate_armed()` answer False about a process that has this
    # module's handler installed -- a dictionary describing an intention rather
    # than the process.
    class _FailRestore:
        SIG_DFL = signal.SIG_DFL
        SIG_IGN = signal.SIG_IGN
        Signals = signal.Signals

        def getsignal(self, sig):
            return signal.getsignal(sig)

        def signal(self, sig, handler):
            if getattr(handler, "__name__", "") != "_gate":
                raise OSError("restore refused")
            return signal.signal(sig, handler)

    server._install_shutdown_signal_gate()
    _armed_before = dict(server._PREVIOUS_SIGNAL_HANDLERS)
    _restore_fails_before = sum(
        v for k, v in server.SHUTDOWN_GATE_DEGRADATIONS.items()
        if k.startswith("restore:"))
    server.signal = _FailRestore()
    try:
        server._remove_shutdown_signal_gate()
    finally:
        server.signal = _real_signal_module

    check("2v  a restore that FAILS keeps its entry, so the gate still "
          "reports itself armed -- which is true, the handler is still live "
          "-- and the disposition to put back is not thrown away",
          (server.shutdown_gate_armed(),
           server._PREVIOUS_SIGNAL_HANDLERS == _armed_before), (True, True))
    check("2w  ...and the failure is COUNTED rather than swallowed",
          sum(v for k, v in server.SHUTDOWN_GATE_DEGRADATIONS.items()
              if k.startswith("restore:")) > _restore_fails_before, True)

    # ...and a later attempt with a working `signal` really does undo it, so
    # the kept entry is not a leak of its own.
    # THE EXPECTED DISPOSITION IS THE ONE THE INSTALL RECORDED, not a literal.
    # 2m ran before this and left `_third_party` on SIGTERM, so that is what
    # this install captured -- and asserting `_recording_previous` here failed,
    # which is the check catching the test rather than the code.
    # `at()`, NOT A SUBSCRIPT. A defect that narrows SHUTDOWN_GATE_SIGNALS to
    # one member -- exactly what check 1b and 2b exist to catch -- leaves no
    # SIGTERM entry here, and a bare `_armed_before[signal.SIGTERM]` then
    # raises KeyError while `check()`'s argument is being evaluated, reporting
    # one traceback where this file owes a summary and seventy-six results.
    # MEASURED: the revert harness aborted this file exactly that way, which is
    # the FIFTEENTH time this project has met that shape and the first time it
    # was caught before shipping.
    _gated = sorted(server.SHUTDOWN_GATE_SIGNALS,
                    key=lambda s: s.name)[-1] if server.SHUTDOWN_GATE_SIGNALS \
        else None
    _entry = at(_armed_before, _gated, None)
    _expected_back = _entry[0] if isinstance(_entry, tuple) else "<NO ENTRY>"
    server._remove_shutdown_signal_gate()
    check("2x  ...and a later attempt, with signal.signal working again, "
          "restores it after all -- so the kept entry is a deferral rather "
          "than a leak of its own",
          (server.shutdown_gate_armed(),
           _gated is not None
           and signal.getsignal(_gated) is _expected_back),
          (False, True))

    check("2u  ...and the one it DID install was unwound, so the disposition "
          "this file put there is what is in force (non-degeneracy: the shim "
          "really did install one before refusing)",
          (len(_shim.installed) == 1,
           signal.getsignal(_shim.installed[0]) is _recording_previous
           if _shim.installed else None),
          (True, True))
finally:
    for _s, _d in _saved_dispositions.items():
        signal.signal(_s, _d)
    _evaluation.clear_stage5_shutdown()


# ── `_chain_to` OVER EVERY DISPOSITION `signal.getsignal` CAN RETURN ───────
#
# The two that are NOT callables are the ones a naive `previous(signum, frame)`
# would crash on, and one of them -- SIG_DFL -- is what a bare process hands
# back, so this is the disposition a developer running the server outside
# uvicorn actually meets.

section("2b. _chain_to handles every disposition, including the two that are "
        "not callables")

_seen = []
check("2o  a CALLABLE previous handler is called with (signum, frame)",
      guarded(server._chain_to,
              lambda sig, frm: _seen.append((sig, frm)), 15, "FRAME"),
      None)
check("2p  ...and it really received both arguments (non-degeneracy)",
      _seen, [(15, "FRAME")])

check("2q  SIG_IGN is reproduced by doing nothing -- the signal was being "
      "ignored, so ignoring it is faithful",
      guarded(server._chain_to, signal.SIG_IGN, 15, None), None)

check("2r  None -- a handler installed outside Python, which cannot be called "
      "from it -- returns rather than raising. Unreachable from the install "
      "path, which refuses to arm in that case, and handled anyway so the "
      "function is total",
      guarded(server._chain_to, None, 15, None), None)

# SIG_DFL IS DRIVEN FOR REAL, ON A SIGNAL WHOSE DEFAULT ACTION IS *IGNORE*.
# SIGWINCH is the one POSIX signal this project can raise without terminating
# the run, so the branch that "restores the default and re-raises" is measured
# rather than reasoned about -- on any of the others the measurement would be
# the test process dying.
_winch_before = signal.getsignal(signal.SIGWINCH)
try:
    signal.signal(signal.SIGWINCH, lambda s, f: None)
    _dfl = guarded(server._chain_to, signal.SIG_DFL,
                   int(signal.SIGWINCH), None)
    check("2s  SIG_DFL restores the default and RE-RAISES, so a fatal signal "
          "stays fatal -- doing nothing here would convert a SIGTERM into a "
          "no-op, which is the failure 'chaining is mandatory' names",
          (_dfl, signal.getsignal(signal.SIGWINCH)),
          (None, signal.SIG_DFL))
finally:
    signal.signal(signal.SIGWINCH, _winch_before)


#------------------------------------------------------------------------------


# ===========================================================================
# 3.  NO STAGE 5 REQUEST IS ISSUED AFTER THE FLAG IS SET
# ===========================================================================
#
# THE LINK, WHICH IS WHAT THIS FILE OWNS. The Stage 5 gate's own behaviour --
# every phase, every counter key, both arms -- is
# `tests/test_agent_stage5_per_trial_calls.py` section 8b's subject and is not
# re-tested here. What is tested here is that THE FLAG THE API SETS IS THE ONE
# THE NODE READS: one module-level boolean, set through the API's own path, and
# a real Stage 5 node driven against a COUNTING stub on either side of it.
#
# NOTHING IS SENT. The stub records the kwargs it was handed and returns a
# literal; it is installed through `oncotriage/agent/deps.py`, the seam the
# whole project redirects the agent with, and the assertion is a count of
# recorded requests rather than of anything on a wire.

section("3. A real Stage 5 node issues nothing once the flag is set")


class _Usage:
    prompt_tokens = 100
    completion_tokens = 10
    completion_tokens_details = None
    prompt_tokens_details = None


class _Msg:
    refusal = None

    def __init__(self, content):
        self.content = content


class _Choice:
    finish_reason = "stop"

    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    model = config.MATCHING_MODEL

    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = _Usage()


def _ids_in(kwargs):
    """The nct_ids fenced into one request's user message, in order.

    THE STUB ANSWERS THE TRIALS IT WAS ASKED ABOUT, and it has to: a stub that
    returns a fixed body names a trial the chunk did not carry, the out-of-set
    detector reconciles it, and the patient completes carrying an `error`. That
    is a defect in the stub reported as a defect in the pipeline -- which the
    first version of this file did, and check 3b is what caught it.
    """
    return re.findall(r"<<<TRIAL_DATA nct_id=(\S+) ",
                      kwargs["messages"][1]["content"])


def _body_for(ids):
    return json.dumps({"evaluations": [
        {"nct_id": i, "eligible": "eligible", "match_score": 0.0,
         "assessment": "No known disqualifiers.",
         "inclusion_criteria": [{"criterion": "Age 18+",
                                 "patient_value": "61", "status": "met"}],
         "exclusion_criteria": []} for i in ids]})


class _CountingStub:
    """Records every `chat.completions.create` and answers it. Sends nothing."""

    def __init__(self):
        self.requests = []
        self._lock = threading.Lock()
        outer = self

        class _Completions:
            def create(self, **kwargs):
                with outer._lock:
                    outer.requests.append(kwargs)
                return _Resp(_body_for(_ids_in(kwargs)))

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


_PATIENT = {
    "patient_id": "api-shutdown-gate-patient",
    "demographics": {"age": 61, "sex": "female", "race": "white",
                     "ethnicity": "not hispanic or latino"},
    "conditions": [{"code": "254837009",
                    "display": "Malignant neoplasm of breast (disorder)",
                    "verification_status": "confirmed"}],
    "medications": [], "allergies": [], "observations": [], "procedures": [],
}

_TRIALS = [{"trial": {
    "nct_id": "NCT%08d" % i, "title": f"Trial {i}", "phase": "PHASE2",
    "eligibility": {"inclusion_criteria": "Inclusion Criteria:\n- " + "x" * 200,
                    "exclusion_criteria": "Exclusion Criteria:\n- " + "x" * 200},
}} for i in range(3)]


def run_stage5():
    """Drive the REAL Stage 5 node once. Returns `(result, stub)`."""
    stub = _CountingStub()
    deps.set_override(deps.OPENAI_CLIENT, stub)
    try:
        state = {
            "patient_data": _PATIENT,
            "filtered_trials": _TRIALS,
            "llm_classifier_retries": 0,
            "mesh_filter_applied": True,
            "mesh_filter_skip_reason": "applied",
            "stage_timings": {},
        }
        return guarded(node_llm_classifier_evaluation, state), stub
    finally:
        deps.clear_override(deps.OPENAI_CLIENT)


_R_OPEN, _S_OPEN = run_stage5()
check_true("3a  CONTROL: with the flag CLEAR the node really does issue "
           "requests -- without this every count below is satisfied by a node "
           "that was broken for some other reason",
           len(_S_OPEN.requests) > 0)
# `error` IS THE EMPTY STRING ON THE SUCCESS PATH, not absent and not None,
# which is why this tests FALSINESS rather than `is None`. Found by running:
# the first version of this check compared against None, failed, and the
# failure was in the check rather than in the node.
check("3b  ...and the patient completes, with an empty error and a verdict "
      "per trial",
      (bool(at(_R_OPEN, "error", "")),
       len(at(_R_OPEN, "evaluations", []))), (False, len(_TRIALS)))

try:
    _evaluation.request_stage5_shutdown("SIGTERM (signal 15) at the API")
    _R_STOP, _S_STOP = run_stage5()
    check("3c  with the flag SET, the node issues NOTHING -- not a trial "
          "call, and in per-trial mode not even the cache warmup",
          len(_S_STOP.requests), 0)
    check("3d  ...and the patient FAILS rather than being published with a "
          "handful of verdicts. A partial success is the dangerous outcome: "
          "`_on_done` checkpoints a success, so a resume would skip that "
          "patient forever",
          (bool(at(_R_STOP, "error", "")), at(_R_STOP, "evaluations")),
          (True, []))
    check("3e  ...and the error NAMES the shutdown, so an operator reading a "
          "failed row they produced by stopping the server is not sent "
          "looking for an endpoint fault that never happened",
          "shutdown" in str(at(_R_STOP, "error", "")).lower(), True)
finally:
    _evaluation.clear_stage5_shutdown()


#------------------------------------------------------------------------------


# ===========================================================================
# 4.  THE LIFESPAN HALF, THROUGH THE TEST CLIENT
# ===========================================================================
#
# `TestClient` AS A CONTEXT MANAGER IS STARLETTE'S OWN WAY TO RUN A LIFESPAN:
# entering it sends `lifespan.startup` and leaving it sends `lifespan.shutdown`.
# Nothing about the gate is stubbed -- the `lifespan` function these clients
# drive is the one the deployed server runs.

section("4. The lifespan half, driven with the TestClient's lifespan support")

_thread_seen = {}
_real_install = server._install_shutdown_signal_gate


def _watching_install():
    _thread_seen["main"] = (threading.current_thread()
                            is threading.main_thread())
    return _real_install()


server._install_shutdown_signal_gate = _watching_install
try:
    with fresh_client() as client:
        check("4a  the STARTUP half cleared the flag, so a second application "
              "in one process cannot inherit the first one's shutdown and "
              "fail every request it serves with no call issued",
              _evaluation.stage5_shutdown_requested(), False)
        check("4b  ...and the app is serving (non-degeneracy: a lifespan that "
              "raised would satisfy 4a by never having run)",
              client.get("/health").status_code, 200)

    check("4c  leaving the context ran the SHUTDOWN half and set the flag",
          _evaluation.stage5_shutdown_requested(), True)
    check("4d  ...with a reason naming the LIFESPAN rather than a signal, "
          "which is how an operator tells a bounded drain from an unbounded "
          "one",
          _evaluation.stage5_shutdown_reason(),
          "the API lifespan shutdown event")
finally:
    server._install_shutdown_signal_gate = _real_install
    _evaluation.clear_stage5_shutdown()

check("4e  the TestClient really ran the lifespan on a NON-MAIN thread, which "
      "is WHY the lifespan half has to exist: `signal.signal` refuses there, "
      "so no signal gate can be installed in this configuration at all",
      _thread_seen.get("main"), False)

check("4f  ...and the refusal was RECORDED rather than swallowed, so a "
      "process running without a signal gate says so instead of looking "
      "identical to one that has it",
      server.SHUTDOWN_GATE_DEGRADATIONS.get("not_main_thread", 0) >= 1, True)

check("4g  ...and the gate is left DISARMED after such a refusal, never "
      "half-armed on one signal of the two",
      (server.shutdown_gate_armed(), dict(server._PREVIOUS_SIGNAL_HANDLERS)),
      (False, {}))

check("4h  the report names the state rather than staying silent about it -- "
      "silence and 'armed' would otherwise look identical in a bring-up log",
      any("NOT armed" in line
          for line in server.shutdown_gate_report_lines()), True)

# THE STARTUP CLEAR, DRIVEN DIRECTLY. 4a shows the flag clear inside the
# context, which is also what a process that never set one looks like.
_evaluation.request_stage5_shutdown("a stale shutdown from an earlier run")
try:
    with fresh_client() as client:
        check("4i  a flag left set BEFORE startup is cleared by it -- the "
              "measurement 4a cannot make, because a never-set flag looks the "
              "same",
              (_evaluation.stage5_shutdown_requested(),
               _evaluation.stage5_shutdown_reason()), (False, None))
finally:
    _evaluation.clear_stage5_shutdown()


#------------------------------------------------------------------------------


# ===========================================================================
# 5.  /health ANSWERS 503 WHEN IT IS NOT SERVICEABLE, AND AGREES WITH ITSELF
# ===========================================================================
#
# THE STATUS CODE IS WHAT `curl -f` IN THE COMPOSE HEALTHCHECK READS, so it is
# what makes `docker compose ps` say `unhealthy`. The BODY carries the same
# verdict, and the two disagreeing is a defect in the handler rather than a
# fact about its dependencies -- which is the half `18- FastAPI Server Test.py`
# could not see, because the body was printed and never read.

section("5. /health: the code and the body, both directions")

with fresh_client() as client:
    _ok = client.get("/health")
    _ok_body = guarded(_ok.json)
    check("5a  a serviceable server answers 200", _ok.status_code, 200)
    check("5b  ...and its body agrees",
          (at(_ok_body, "status"), at(_ok_body, "pipeline_ready")),
          ("healthy", True))
_evaluation.clear_stage5_shutdown()

_READY["ready"] = False
try:
    with fresh_client() as client:
        _bad = client.get("/health")
        _bad_body = guarded(_bad.json)
        check("5c  a server missing a dependency answers 503, which is what "
              "turns a green-but-unusable container red",
              _bad.status_code, 503)
        check("5d  ...and its body agrees",
              at(_bad_body, "status"), "unhealthy")
        check("5e  ...while `pipeline_ready` stays TRUE, because the graph did "
              "compile. It is one field among several rather than the whole "
              "answer, which is the whole reason `serving_ready` sits beside "
              "it: this was the field that reported true while the server was "
              "unusable",
              at(_bad_body, "pipeline_ready"), True)
finally:
    _READY["ready"] = True
    _evaluation.clear_stage5_shutdown()

# THE ASSERTION FILE 18 MAKES, DRIVEN AS A PREDICATE. A 200 whose body says
# "unhealthy" means the handler disagrees with itself; nothing in this repo
# could see that before, and it is the case that would pass silently.
def _file18_verdict(status_code, body):
    """True when File 18's Test 1 would record NO failure for this response."""
    if status_code != 200:
        return False
    return (body.get("status") == "healthy"
            and body.get("pipeline_ready") is True)


check("5f  the predicate File 18 applies accepts a real 200/healthy",
      _file18_verdict(200, {"status": "healthy", "pipeline_ready": True}),
      True)
check("5g  ...rejects a 503, which it already did",
      _file18_verdict(503, {"status": "unhealthy", "pipeline_ready": True}),
      False)
check("5h  ...and rejects a 200 whose BODY says unhealthy -- the case that "
      "used to pass silently, because the body was printed and never read",
      _file18_verdict(200, {"status": "unhealthy", "pipeline_ready": True}),
      False)
check("5i  ...and a 200 that reports the graph never compiled",
      _file18_verdict(200, {"status": "healthy", "pipeline_ready": False}),
      False)


#------------------------------------------------------------------------------


# ===========================================================================
# 6.  FILE 18 REALLY RECORDS THOSE FAILURES
# ===========================================================================
#
# THE STRUCTURAL HALF, AND IT DOES NOT REPLACE SECTION 5. A predicate driven
# here cannot see a File 18 that computes the right verdict and never appends
# it to `failures`; an AST walk cannot see a verdict computed wrongly. File 18
# needs a LIVE SERVER and costs money, so it cannot be driven from CI at all --
# which is exactly why the shape of its check is worth pinning from a test that
# can run.

section("6. File 18's Test 1 records the failures rather than printing them")

_f18 = guarded(ast.parse, _FILE_18_TEXT)
check("6a  File 18 parses (non-degeneracy: every walk below is over this tree)",
      isinstance(_f18, ast.Module), True)


def _main_of(tree):
    if not isinstance(tree, ast.Module):
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    return None


_MAIN = _main_of(_f18)
check("6b  ...and carries a `main()` -- the guard File 18 puts every "
      "executable statement behind, because loading that file used to BE "
      "running it",
      _MAIN is not None, True)


def _appends_to_failures(node):
    """Every `failures.append(...)` argument in `node`, unparsed."""
    out = []
    for sub in ast.walk(node) if node is not None else ():
        if (isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "append"
                and isinstance(sub.func.value, ast.Name)
                and sub.func.value.id == "failures"
                and sub.args):
            out.append(ast.unparse(sub.args[0]))
    return out


_APPENDS = _appends_to_failures(_MAIN)
_HEALTH_APPENDS = [a for a in _APPENDS if "/health" in a]

check("6c  Test 1 records MORE THAN ONE distinct failure for GET /health -- "
      "the status code, the status field and pipeline_ready. One would mean "
      "the body is still being printed and not read",
      len(_HEALTH_APPENDS) >= 3, True)
check("6d  ...and one of them names the status CODE, so the summary "
      "distinguishes an endpoint that refused from one that answered "
      "something unparseable",
      any("status_code" in a for a in _HEALTH_APPENDS), True)
check("6e  ...one names the body's `status` field",
      any("'status'" in a or '"status"' in a for a in _HEALTH_APPENDS), True)
check("6f  ...and one names `pipeline_ready`",
      any("pipeline_ready" in a for a in _HEALTH_APPENDS), True)
check("6g  CONTROL: the walk is not satisfied by any append anywhere -- there "
      "are appends for the OTHER three tests too, and they are excluded",
      len(_APPENDS) > len(_HEALTH_APPENDS), True)
check("6h  CONTROL: a `main` with no appends at all reports none, so 6c "
      "cannot pass over an empty walk",
      _appends_to_failures(_main_of(ast.parse(
          "def main():\n    failures = []\n    return failures\n"))), [])


#------------------------------------------------------------------------------


# ===========================================================================
# 7.  AN ERA-3 `runs` DATABASE REPORTS PARTIAL, NOT A QUERY ERROR
# ===========================================================================
#
# `runs.matching_call_mode` IS ADDITIVE, added by the call-mode pass, and
# `queries.run_summary` and `queries.campaign_summary` both DECLARE it. So a
# database last written between the run-identity pass and that one has both run
# tables, has `inferences.run_id`, and cannot answer two of the tab's four
# queries -- and the availability loader reported `present`, sending the tab
# down its normal path where `_load_run_query` caught the raise, called
# `st.error` and handed back an empty frame. The tab then printed "the run
# tables are present and hold no rows" underneath the error saying they could
# not be asked.
#
# THE REQUIREMENT IS DERIVED FROM THE QUERIES' OWN DECLARATIONS rather than
# hand-listed, which is the actual fix: the loader used to name ONE column,
# `inferences.run_id`, and `runs.resumed` and then `runs.matching_call_mode`
# arrived afterwards with nothing here noticing either time.

section("7. The availability loader over three schema eras")

from oncotriage.storage import database_logger as _dbl          # noqa: E402
from oncotriage.storage import queries as _queries              # noqa: E402
import streamlit as _st                                         # noqa: E402
from oncotriage.dashboard import data as _dashboard_data        # noqa: E402


def _era_database(name, drop_from_runs=None, drop_run_id=False):
    """A real database built by the project's own initializer, then aged.

    THE SCHEMA IS THE REAL ONE, so an era is produced by REMOVING what a later
    era added rather than by hand-typing a CREATE TABLE that can drift from the
    writer's.
    """
    path = os.path.join(_TMP, f"{name}.db")
    _dbl.initialize_database(path)
    conn = sqlite3.connect(path)
    try:
        for table, column in (("runs", drop_from_runs),
                              ("inferences", "run_id" if drop_run_id else None)):
            if not column:
                continue
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            keep = [c for c in cols if c != column]
            conn.execute(f"CREATE TABLE _tmp AS SELECT {','.join(keep)} "
                         f"FROM {table}")
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE _tmp RENAME TO {table}")
        conn.commit()
    finally:
        conn.close()
    return path


def _availability(path):
    _paths._RESOLVED["inferences_path"] = path
    _st.cache_data.clear()
    return _dashboard_data.load_run_tracking_availability()


_ERA_CURRENT = _era_database("era_current")
_ERA_3 = _era_database("era_3", drop_from_runs="matching_call_mode")
_ERA_2 = _era_database("era_2", drop_from_runs="resumed")
_ERA_NO_RUN_ID = _era_database("era_no_run_id", drop_run_id=True)

_A_CURRENT = _availability(_ERA_CURRENT)
_A_3 = _availability(_ERA_3)
_A_2 = _availability(_ERA_2)
_A_NO_ID = _availability(_ERA_NO_RUN_ID)

check("7a  a CURRENT database reports present with nothing missing",
      (_A_CURRENT["availability"], _A_CURRENT["missing"]),
      (_dashboard_data.RUN_TRACKING_PRESENT, []))

check("7b  an ERA-3 database -- both run tables, `inferences.run_id`, and no "
      "`runs.matching_call_mode` -- reports PARTIAL and NAMES the column, "
      "instead of reporting present and producing a query error in the tab",
      (_A_3["availability"], _A_3["missing"]),
      (_dashboard_data.RUN_TRACKING_PARTIAL, ["runs.matching_call_mode"]))

check("7c  ...and an ERA-2 database does too, for the column that arrived one "
      "era earlier. The requirement is DERIVED, so this is covered without "
      "anybody having listed it",
      (_A_2["availability"], _A_2["missing"]),
      (_dashboard_data.RUN_TRACKING_PARTIAL, ["runs.resumed"]))

check("7d  a database with the run tables and no `inferences.run_id` still "
      "reports PARTIAL, which is the case the loader always covered",
      (_A_NO_ID["availability"], "inferences.run_id" in _A_NO_ID["missing"],
       _A_NO_ID["has_run_id"]),
      (_dashboard_data.RUN_TRACKING_PARTIAL, True, False))

check("7e  NON-DEGENERACY: the era-3 database really does have both run "
      "tables and the run_id column, so 7b is about the COLUMN half of the "
      "guard rather than about a table the other half already refused",
      (sorted(_A_3["tables"]), _A_3["has_run_id"]),
      (sorted(_queries.RUN_TABLES), True))

# WHAT THE OLD LOADER WOULD HAVE DONE, and why `present` was the wrong answer.
_conn = sqlite3.connect(_ERA_3)
try:
    _era3_run = guarded(_queries.run, _conn, _dashboard_data.RUN_SUMMARY_QUERY)
finally:
    _conn.close()
check("7f  the query the tab would have run against that database RAISES, "
      "which is what reporting `present` would have turned into an st.error "
      "plus a caption saying the tables hold no rows",
      isinstance(_era3_run, str) and "MissingTableError" in _era3_run, True)

check("7g  every key the availability loader derives from is a REGISTERED "
      "query, checked at import of the dashboard module and asserted here "
      "too, so a typo cannot become an empty frame at render time",
      sorted(k for k in _dashboard_data.RUN_TAB_QUERY_KEYS
             if k not in _queries.QUERIES_BY_KEY), [])

check("7h  ...and the four loaders and the derivation ask about the SAME four "
      "queries (non-degeneracy: two copies of that list is how a fifth run "
      "query joins the tab without joining its availability check)",
      len(set(_dashboard_data.RUN_TAB_QUERY_KEYS)), 4)

check("7i  the vocabulary the tab branches on is still closed and still "
      "contains what this section produced",
      {_A_CURRENT["availability"], _A_3["availability"]}
      <= set(_dashboard_data.RUN_TRACKING_STATES), True)


#------------------------------------------------------------------------------


# ===========================================================================
# 8.  ISOLATION
# ===========================================================================

section("8. Isolation: nothing leaked, nothing in the repository was written")

_restore_stand_ins()
_paths._RESOLVED.clear()
_paths._RESOLVED.update(_RESOLVED_BEFORE)

check("8a  the Stage 5 shutdown flag is CLEAR at the end -- a leaked flag "
      "would make every later file's Stage 5 run fail with no request issued, "
      "and `pytest tests/` shares one process",
      (_evaluation.stage5_shutdown_requested(),
       _evaluation.stage5_shutdown_reason()), (False, None))

check("8b  no signal handler of this module's is left installed",
      (server.shutdown_gate_armed(),
       dict(server._PREVIOUS_SIGNAL_HANDLERS)), (False, {}))

check("8c  the dependency seam carries no override this file installed",
      deps.peek(deps.OPENAI_CLIENT) is deps.UNSET, True)

check("8d  the three module stand-ins were put back, so a later import in "
      "this process gets the real factory",
      [getattr(server, _n) is _r for _n, _r in _REAL.items()],
      [True, True, True])

check("8e  paths._RESOLVED is restored, so nothing later can resolve to this "
      "file's scratch tree", _paths._RESOLVED, _RESOLVED_BEFORE)

check("8f  oncotriage/api/server.py is byte-unchanged",
      hashlib.sha256(open(_SERVER_PY, "rb").read()).hexdigest(), _SERVER_SHA)
check("8g  18- FastAPI Server Test.py is byte-unchanged",
      hashlib.sha256(open(_FILE_18, "rb").read()).hexdigest(), _FILE_18_SHA)

shutil.rmtree(_TMP, ignore_errors=True)
check("8h  the scratch tree is gone", os.path.exists(_TMP), False)


#------------------------------------------------------------------------------


print(f"\n{'=' * 74}")

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

print(f"  {_RESULTS['passed']} passed, {_RESULTS['failed']} failed")
print(f"{'=' * 74}")
if _FAILURES:
    print("\nFAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")
sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug 27 2026

@author: ramyalsaffar
"""
