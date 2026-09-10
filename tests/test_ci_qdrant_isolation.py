# CI Qdrant Isolation Test
#########################

"""The three isolation mechanisms that keep bucket A off the live index.

WHAT THIS FILE IS FOR, AND WHY IT IS NOT A GENERAL NETWORK INSTRUMENT
---------------------------------------------------------------------
Bucket A is classified as making no live call. Before the isolation pass, FIVE
of its files reached the production Qdrant Cloud endpoint between them --
MEASURED with a recorder that logged and REFUSED every outbound attempt it
intercepted, at 2 + 26 + 10 + 2 + 1 = 41 connection attempts. That instrument
reports what it saw in the processes it was loaded into; TOTAL EGRESS FROM A
RUN WAS NOT INDEPENDENTLY MEASURED, and this file does not measure it either. Three mechanisms now prevent that, and NOTHING
ASSERTED ANY OF THEM: they were verified once, by hand, and a property nothing
enforces is a property that rots.

This file is small on purpose. It does NOT sweep bucket A for egress -- that
would be a general instrument, it would be slow, and it would report a number
rather than a cause. It asserts the three specific mechanisms, each with a
control that FIRES:

  (a) the CI runner hands every test child the closed-port endpoint, EVEN WHEN
      a cloud URL is exported;
  (b) the three per-file guards apply the shared rule, through its one owner,
      early enough to matter;
  (c) SECTION E of tests/test_agent_retrieval_observability.py skips BY NAME
      when ONCOTRIAGE_QDRANT_PROBE_URL is unset, rather than falling back to
      the pipeline's own client.

THE OFFLINE RECORDER IS THE REPOSITORY'S OWN, and it has a real in-process
subject here rather than being decoration. Section 1 uses it to measure the
PREMISE every one of the three mechanisms rests on: that constructing a Qdrant
client reaches its endpoint at all, and that pointing that endpoint at a closed
port confines it to loopback. Same four primitives, same named-function rule
and the same recorded lesson as tests/test_dashboard_run_health.py and
tests/test_dashboard_reproducibility_tab.py -- named functions rather than
lambdas, because a guard that walks back a fixed number of frames names its own
stand-in and its control then passes for the wrong reason.

WHAT THIS FILE DOES NOT ESTABLISH, STATED RATHER THAN IMPLIED
-------------------------------------------------------------
It does not measure total egress from a bucket-A run. It measures the attempts
its own recorder INTERCEPTS in this process, and the environment a child WOULD
receive. The standing claim that bucket A runs offline rests on these three
checks plus the hosted CI run under HF_HUB_OFFLINE=1 -- never on a measurement
of total egress, which nothing in this repository takes.

Section 2 patches `subprocess.run` inside the runner module and spawns NOTHING;
section 3 parses source and spawns one short subprocess for a premise; section
4 runs one test file twice, which is the only way to get a firing behavioural
control for a branch that lives in another file. That last one is why this file
needs the FastEmbed cache warm -- the same precondition the file it drives has,
and what `.github/scripts/prewarm_model_cache.py` guarantees in CI.

No keys, no spend, no live Qdrant, no model of its own, no database, no git
history, no Docker daemon. It writes only inside a tempfile.mkdtemp it removes
and asserts gone. It EXECS NOTHING and loads no module by location: every
control is an attribute rebound inside try/finally with the restore asserted by
identity, an ast walk over an in-memory copy, or a COPY of a file run as a
subprocess from a temp directory.
"""

import ast
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import traceback


_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CODE_DIR = os.path.dirname(_TESTS_DIR)
_SCRIPTS_DIR = os.path.join(_CODE_DIR, ".github", "scripts")
for _p in (_CODE_DIR, _TESTS_DIR, _SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _control_harness as _harness                                # noqa: E402


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, actual, expected):
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected: {expected!r}\n"
                         f"          actual:   {actual!r}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected!r}")
        print(f"          actual:   {actual!r}")


def check_true(label, actual):
    check(label, bool(actual), True)


def fail(label, detail):
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}")
    print(f"          {detail}")


class _Absent:
    """A named absence. Falsy, so `len(x or [])` and `if x` behave."""

    def __init__(self, why):
        self.why = why

    def __bool__(self):
        return False

    def __repr__(self):
        return f"<absent: {self.why}>"


def at(seq, i, why="index out of range"):
    """`seq[i]`, or a named absence. A control that ABORTS is not a control."""
    try:
        return seq[i]
    except (IndexError, KeyError, TypeError):
        return _Absent(why)


# ===========================================================================
# THE OFFLINE RECORDER
# ===========================================================================

_NETWORK_ATTEMPTS = []
_REAL_SOCKET_CONNECT = socket.socket.connect
_REAL_SOCKET_CONNECT_EX = socket.socket.connect_ex
_REAL_CREATE_CONNECTION = socket.create_connection
_REAL_GETADDRINFO = socket.getaddrinfo

_GUARD_FRAMES = {"_blocked", "_network_caller", "_guard_connect",
                 "_guard_connect_ex", "_guard_create_connection",
                 "_guard_getaddrinfo"}


def _network_caller():
    for frame in reversed(traceback.extract_stack()):
        if os.path.basename(frame.filename) == "socket.py":
            continue
        if frame.name in _GUARD_FRAMES:
            continue
        return f"{os.path.basename(frame.filename)}:{frame.lineno} in {frame.name}"
    return "unknown"


def _blocked(call_name, target):
    where = _network_caller()
    host = target[0] if isinstance(target, (tuple, list)) and target else target
    _NETWORK_ATTEMPTS.append({"call": call_name, "host": str(host),
                              "target": repr(target), "caller": where})
    raise OSError(f"[offline guard] {call_name} to {target!r} blocked; "
                  f"attempted from {where}")


def _guard_connect(self, address, *a, **k):
    return _blocked("socket.connect", address)


def _guard_connect_ex(self, address, *a, **k):
    return _blocked("socket.connect_ex", address)


def _guard_create_connection(address, *a, **k):
    return _blocked("socket.create_connection", address)


def _guard_getaddrinfo(host, port, *a, **k):
    return _blocked("socket.getaddrinfo", (host, port))


def _arm_offline_guard():
    socket.socket.connect = _guard_connect
    socket.socket.connect_ex = _guard_connect_ex
    socket.create_connection = _guard_create_connection
    socket.getaddrinfo = _guard_getaddrinfo


def _disarm_offline_guard():
    socket.socket.connect = _REAL_SOCKET_CONNECT
    socket.socket.connect_ex = _REAL_SOCKET_CONNECT_EX
    socket.create_connection = _REAL_CREATE_CONNECTION
    socket.getaddrinfo = _REAL_GETADDRINFO


def _hosts_reached(thunk):
    """Every host `thunk` tried to reach, with the guard armed. Never raises."""
    del _NETWORK_ATTEMPTS[:]
    _arm_offline_guard()
    try:
        thunk()
    except BaseException:
        pass
    finally:
        _disarm_offline_guard()
    return sorted({a["host"] for a in _NETWORK_ATTEMPTS})


# NEVER CONNECTED TO -- IT IS A STRING IN AN ENVIRONMENT DICT AND NOTHING
# ELSE. Section 2 exports it to prove the runner overrides an exported cloud
# endpoint, and the only consumer of that environment is a `subprocess.run`
# this file REPLACES with a recorder, so no process is created and no
# connection is attempted. It is a `.invalid` name (RFC 6761: guaranteed never
# to resolve) so that even a future edit which did try to reach it could not
# find a real host.
_FAKE_CLOUD_HOST = "fake-cloud.example.invalid"
_FAKE_CLOUD_URL = f"https://{_FAKE_CLOUD_HOST}:6333"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


# ===========================================================================
# SECTION 1 -- THE PREMISE: A QDRANT CLIENT TALKS TO ITS ENDPOINT
# ===========================================================================
# Every mechanism below rests on one fact about qdrant-client: it goes to
# whatever URL it was given. If that were false, redirecting the URL would buy
# nothing and all three guards would be theatre.
#
# THE PROPERTY IS ASSERTED ON A QUERY, WHICH IS SYNCHRONOUS, AND BOTH
# ENDPOINTS ARE LOOPBACK. Two facts about qdrant-client were measured while
# writing this, and both shaped what is below:
#
#   1. `QdrantClient(url=...)` records ZERO attempts at the moment it returns,
#      ONE about two seconds later, and TWO after a query -- the compatibility
#      check runs on a BACKGROUND THREAD. That is why `deps.get_qdrant_client()`
#      alone leaked one connection out of
#      tests/test_storage_inference_logging_contract.py with no query made.
#      `check_compatibility=False` removes that thread outright: measured 0
#      after the constructor, 0 after 2.5 s, 1 only on an explicit query.
#   2. A thread that connects LATER is a thread that connects after this
#      section's guard has been disarmed. THE FIRST VERSION OF THIS FILE HAD
#      THAT DEFECT: it pointed one probe at a `.example.invalid` name, and a
#      full bucket-A run under an independent recorder caught one unguarded
#      outbound attempt to it -- from this file, in a pass whose whole subject
#      is that tests do not reach out.
#
# So: the thread is disabled, AND both probes are loopback, so nothing can
# leave the machine even if that flag ever stops working. Two DIFFERENT
# loopback addresses is all the premise needs -- it says the client goes to the
# endpoint it was GIVEN, which is exactly what redirecting the endpoint relies
# on. The threaded behaviour is recorded above and NOT asserted: a check whose
# outcome depends on how long a thread took goes red on a loaded runner for a
# reason unrelated to its subject, and the version-check thread is declared out
# of scope by the operator.

print("=" * 74)
print("SECTION 1 -- a Qdrant client talks to the endpoint it was given (the")
print("             premise every guard below rests on)")
print("=" * 74)

from qdrant_client import QdrantClient                            # noqa: E402

# A SECOND LOOPBACK ADDRESS, not a routable one. 127.0.0.0/8 is loopback
# entirely, so a connection here is refused by the kernel and no packet and no
# DNS query leaves the machine.
_OTHER_LOOPBACK_HOST = "127.0.0.2"
_OTHER_LOOPBACK_URL = f"http://{_OTHER_LOOPBACK_HOST}:1"


def _query_against(url):
    """One synchronous round trip. Returns the hosts it tried to reach.

    `check_compatibility=False` is what makes this deterministic: without it
    the constructor starts a thread that connects on its own schedule, and this
    function would sometimes attribute one probe's attempt to the next one.
    """
    client = QdrantClient(url=url, timeout=2, check_compatibility=False)
    return _hosts_reached(client.get_collections)


_other_hosts = _query_against(_OTHER_LOOPBACK_URL)
check("1a  a query goes to the host the URL named", _other_hosts,
      [_OTHER_LOOPBACK_HOST])

_closed_hosts = _query_against(_harness.CLOSED_PORT_URL)
check("1b  ...and a different URL sends it somewhere else -- so the endpoint "
      "decides where a process talks", _closed_hosts, ["127.0.0.1"])
check("1b  ...non-degeneracy: each probe really did try to connect",
      bool(_other_hosts) and bool(_closed_hosts), True)
check("1b  ...and the two are distinguishable, so 1a can fail",
      _other_hosts == _closed_hosts, False)
check("1c  neither probe left the machine: every host reached is loopback",
      set(_other_hosts + _closed_hosts) <= _LOOPBACK_HOSTS
      | {_OTHER_LOOPBACK_HOST}, True)

# THE RECORDER'S OWN CONTROL. Same guard, a real outbound call, and the frame
# it reports must be the function that made it -- not one of the guard's own.
def _offline_control_call():
    socket.getaddrinfo("example.invalid", 80)


del _NETWORK_ATTEMPTS[:]
_arm_offline_guard()
try:
    _offline_control_call()
    _control_raised = False
except OSError:
    _control_raised = True
finally:
    _disarm_offline_guard()

_first = at(_NETWORK_ATTEMPTS, 0, "no attempt recorded")
check("1d  the recorder raises on a real outbound call (the control)",
      _control_raised, True)
check("1d  ...and records exactly one attempt", len(_NETWORK_ATTEMPTS), 1)
check("1d  ...naming the frame that made it, not one of the guard's own",
      (_first.get("caller", "") if isinstance(_first, dict)
       else str(_first)).split(" in ")[-1],
      "_offline_control_call")
check("1d  ...and the guard is disarmed afterwards",
      (socket.socket.connect is _REAL_SOCKET_CONNECT
       and socket.socket.connect_ex is _REAL_SOCKET_CONNECT_EX
       and socket.create_connection is _REAL_CREATE_CONNECTION
       and socket.getaddrinfo is _REAL_GETADDRINFO), True)


# ===========================================================================
# SECTION 2 -- (a) THE RUNNER HANDS EVERY CHILD THE CLOSED PORT
# ===========================================================================
# The real `_run_one`, with `subprocess.run` replaced by a recorder, so the
# thing under test is the env a child WOULD receive rather than a reproduction
# of how it is built. NOTHING IS SPAWNED.

print()
print("=" * 74)
print("SECTION 2 -- (a) the CI runner isolates every test child")
print("=" * 74)

import ci_test_buckets as _runner                                 # noqa: E402


class _Completed:
    returncode = 0
    stdout = ""
    stderr = ""


def _child_env(name="test_ci_qdrant_isolation.py", root=None):
    """The environment `_run_one` would hand a child. Spawns nothing."""
    captured = {}

    def _recording_run(argv, **kwargs):
        captured["env"] = dict(kwargs.get("env") or {})
        captured["argv"] = list(argv)
        return _Completed()

    real = _runner.subprocess.run
    _runner.subprocess.run = _recording_run
    try:
        _runner._run_one(name, root)
    finally:
        _runner.subprocess.run = real
    check_true("        (the recorder was restored by identity)",
               _runner.subprocess.run is real)
    return captured


_SAVED_ENV = {k: os.environ.get(k)
              for k in (_harness.QDRANT_URL_ENV, _harness.QDRANT_API_KEY_ENV)}
os.environ[_harness.QDRANT_URL_ENV] = _FAKE_CLOUD_URL
os.environ[_harness.QDRANT_API_KEY_ENV] = "a-key-that-must-not-travel"

_cap = _child_env()
_child = _cap.get("env", {})

check("2a  with a cloud URL exported, the child still gets the closed port",
      _child.get(_harness.QDRANT_URL_ENV), _harness.CLOSED_PORT_URL)
check("2a  ...and it is NOT the exported value (the redirect is real)",
      _child.get(_harness.QDRANT_URL_ENV) == _FAKE_CLOUD_URL, False)
check("2b  the child carries no Qdrant API key at all",
      _harness.QDRANT_API_KEY_ENV in _child, False)
check("2b  ...non-degeneracy: the parent DID have one to drop",
      os.environ.get(_harness.QDRANT_API_KEY_ENV), "a-key-that-must-not-travel")
check("2c  the runner announces the override rather than overriding silently",
      (_runner._isolation_note() or "").count(_FAKE_CLOUD_URL), 1)
check("2c  ...and says nothing when nothing was exported (so the ABSENCE of "
      "the line is not evidence of a missing guard)",
      _runner._harness().qdrant_isolation_note({}, "probe"), None)

# THE FIRING CONTROL. The isolation function is rebound to a no-op -- the
# pre-fix behaviour exactly -- and the assertion above must then fail. An
# attribute rebind inside try/finally, restored and asserted BY IDENTITY, which
# is this repository's no-exec control shape.
_real_isolate = _runner._isolate_qdrant
try:
    _runner._isolate_qdrant = lambda env: env
    _unguarded = _child_env().get("env", {})
finally:
    _runner._isolate_qdrant = _real_isolate

check("2d  CONTROL: with the isolation removed the child receives the "
      "exported cloud URL",
      _unguarded.get(_harness.QDRANT_URL_ENV), _FAKE_CLOUD_URL)
check("2d  ...and the key travels with it, so 2b can fail too",
      _unguarded.get(_harness.QDRANT_API_KEY_ENV),
      "a-key-that-must-not-travel")
check("2d  ...and the real function was restored by identity",
      _runner._isolate_qdrant is _real_isolate, True)

for _k, _v in _SAVED_ENV.items():
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v
check("2e  this file restored the two variables it borrowed",
      {k: os.environ.get(k) for k in _SAVED_ENV}, _SAVED_ENV)


# ===========================================================================
# SECTION 3 -- (b) THE PER-FILE GUARDS HOLD ON DIRECT RUNS
# ===========================================================================
# Three files carry their own guard, because the runner's only covers a run
# THROUGH the runner and each of these is routinely launched by hand.
#
# TWO PROPERTIES, and the second is the one that breaks silently.
#   1. the rule comes from its ONE owner rather than a private copy;
#   2. for the two in-process files, it is applied BEFORE the first import that
#      can resolve the endpoint. `oncotriage/config.py` caches the resolved
#      endpoint in `_QDRANT_ENDPOINT_CACHE` for the life of the process, so a
#      guard placed one import too late is a guard that does nothing -- and
#      nothing about the file's output would say so.
#
# The caching premise is MEASURED below rather than cited, so this ordering
# rule is not guarding a property that does not exist.

print()
print("=" * 74)
print("SECTION 3 -- (b) the three per-file guards")
print("=" * 74)

_IN_PROCESS_GUARDED = ("test_runner_crash_record_and_db_unification.py",
                       "test_storage_inference_logging_contract.py")
_CHILD_ENV_GUARDED = "test_observability_logging.py"


def _tree(name):
    with open(os.path.join(_TESTS_DIR, name), encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=name)


def _calls_isolate(node):
    """Every `<anything>.isolate_qdrant(...)` call inside `node`."""
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "isolate_qdrant"]


def _first_resolving_import(tree):
    """Index of the first import that can resolve the Qdrant endpoint.

    A BARE `import oncotriage` IS NOT ONE, and that is measured rather than
    assumed: `oncotriage/__init__.py` is a docstring and nothing else, so it
    binds no path and opens no client. What resolves is an import of a
    SUBMODULE -- `from oncotriage.x import y`, `from oncotriage import y`, or
    `import oncotriage.x`. Treating the bare form as resolving would force
    every guarded file to put its guard above its own bootstrap, which is a
    different file layout for no gain.
    """
    for i, stmt in enumerate(tree.body):
        if isinstance(stmt, ast.ImportFrom):
            if stmt.module == "oncotriage" or (
                    stmt.module or "").startswith("oncotriage."):
                return i
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.name.startswith("oncotriage."):
                    return i
    return None


def _isolate_stmt_index(tree):
    for i, stmt in enumerate(tree.body):
        if _calls_isolate(stmt):
            return i
    return None


for _name in _IN_PROCESS_GUARDED:
    _t = _tree(_name)
    _guard_i = _isolate_stmt_index(_t)
    _import_i = _first_resolving_import(_t)
    check_true(f"3a  {_name} applies the shared rule at module scope",
               _guard_i is not None)
    check_true(f"3a  ...and it imports the owner rather than copying it "
               f"({_name})",
               any(isinstance(n, ast.Import)
                   and any(a.name == "_control_harness" for a in n.names)
                   for n in ast.walk(_t)))
    if _guard_i is None or _import_i is None:
        fail(f"3b  {_name} ordering is checkable",
             f"guard index {_guard_i}, first resolving import {_import_i}")
    else:
        check(f"3b  ...and it runs BEFORE the first endpoint-resolving import "
              f"({_name})", _guard_i < _import_i, True)

# THE SPAWNER'S SHAPE IS DIFFERENT AND IS CHECKED DIFFERENTLY. It builds a
# child environment per drive, so the rule belongs inside that function and
# BEFORE the spawn, not at module scope.
_t_child = _tree(_CHILD_ENV_GUARDED)
_driver = next((n for n in ast.walk(_t_child)
                if isinstance(n, ast.FunctionDef) and n.name == "_run_driver"),
               None)
if _driver is None:
    fail(f"3c  {_CHILD_ENV_GUARDED} still has a _run_driver to check",
         "no FunctionDef named _run_driver")
else:
    _iso = _calls_isolate(_driver)
    check(f"3c  {_CHILD_ENV_GUARDED}'s _run_driver isolates the child env",
          len(_iso), 1)
    _spawn = [n for n in ast.walk(_driver)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "run"]
    check("3c  ...non-degeneracy: it really does spawn (so ordering matters)",
          len(_spawn) >= 1, True)
    check("3c  ...and the isolation is on a line above the spawn",
          bool(_iso) and bool(_spawn) and _iso[0].lineno < _spawn[0].lineno,
          True)

# --- the ordering rule's premise, measured ---------------------------------
_PREMISE = (
    "import os, sys;"
    "sys.path.insert(0, %r);"
    "from oncotriage import config;"
    "first = config.get_qdrant_url();"
    "os.environ['ONCOTRIAGE_QDRANT_URL'] = 'http://127.0.0.1:2';"
    "second = config.get_qdrant_url();"
    "print('PREMISE', first, second, sep='|')" % _CODE_DIR
)
_env = dict(os.environ)
_env[_harness.QDRANT_URL_ENV] = _harness.CLOSED_PORT_URL
_env.pop(_harness.QDRANT_API_KEY_ENV, None)
_p = subprocess.run([sys.executable, "-c", _PREMISE], cwd=_CODE_DIR, env=_env,
                    capture_output=True, text=True, timeout=120)
_line = next((l for l in _p.stdout.splitlines() if l.startswith("PREMISE|")),
             None)
if _line is None:
    fail("3d  the caching premise could be measured",
         f"no PREMISE line; rc={_p.returncode}; stderr tail: "
         f"{_p.stderr[-400:]!r}")
else:
    _, _first, _second = _line.split("|", 2)
    check("3d  the endpoint is resolved ONCE per process, so a guard placed "
          "after the first resolving import would do nothing",
          (_first, _second),
          (_harness.CLOSED_PORT_URL, _harness.CLOSED_PORT_URL))
    check("3d  ...non-degeneracy: the value it kept is the one set BEFORE the "
          "import, not the one set after",
          _second == "http://127.0.0.1:2", False)

# --- the firing controls ----------------------------------------------------
# AST copies, not exec: the check under test is itself static, so a plant that
# a static walk can see is the right instrument.


def _without_guard(tree):
    """`tree` with every module-scope isolate_qdrant statement removed."""
    out = ast.Module(body=[s for s in tree.body if not _calls_isolate(s)],
                     type_ignores=[])
    return out


def _guard_moved_late(tree):
    """`tree` with the guard statement moved BELOW the first resolving import."""
    gi = _isolate_stmt_index(tree)
    ii = _first_resolving_import(tree)
    if gi is None or ii is None:
        return None
    body = list(tree.body)
    stmt = body.pop(gi)
    body.insert(ii, stmt)          # ii is now one past the resolving import
    return ast.Module(body=body, type_ignores=[])


_ctl_name = _IN_PROCESS_GUARDED[0]
_ctl_tree = _tree(_ctl_name)
_stripped = _without_guard(_ctl_tree)
check(f"3e  CONTROL: with the guard statement removed, the check reports it "
      f"missing ({_ctl_name})", _isolate_stmt_index(_stripped), None)
check("3e  ...non-degeneracy: the unmodified tree DOES have one",
      _isolate_stmt_index(_ctl_tree) is not None, True)

_late = _guard_moved_late(_ctl_tree)
if _late is None:
    fail("3f  CONTROL: the guard could be moved late", "indices unavailable")
else:
    _g, _i = _isolate_stmt_index(_late), _first_resolving_import(_late)
    check("3f  CONTROL: with the guard moved below the first resolving "
          "import, the ordering check fails", _g < _i if (
              _g is not None and _i is not None) else None, False)
    check("3f  ...and it is still present, so 3e and 3f fail for different "
          "reasons", _g is not None, True)


# ===========================================================================
# SECTION 4 -- (c) SECTION E SKIPS BY NAME
# ===========================================================================
# BEHAVIOURAL, AND THE CONTROL IS A REAL RUN OF A REVERTED COPY. A structural
# check could say the fallback is gone; only a run can say the file records a
# COUNTED skip that names the variable an operator has to set. The reverted
# copy is written to a temp directory and run from there -- the shipped file is
# never edited, and its sha256 is compared at the end.
#
# BOTH ARMS RUN WITH THE ENDPOINT ON THE CLOSED PORT, so the reverted arm --
# which resolves the pipeline's own client on purpose -- reaches loopback
# rather than the live index. That is what lets this control exist at all.

print()
print("=" * 74)
print("SECTION 4 -- (c) SECTION E is opt-in and skips by name")
print("=" * 74)

_PROBE_VAR = "ONCOTRIAGE_QDRANT_PROBE_URL"
_TARGET = "test_agent_retrieval_observability.py"
_TARGET_PATH = os.path.join(_TESTS_DIR, _TARGET)

import hashlib                                                     # noqa: E402

with open(_TARGET_PATH, "rb") as _fh:
    _TARGET_SHA_BEFORE = hashlib.sha256(_fh.read()).hexdigest()

_TMP = tempfile.mkdtemp(prefix="oncotriage_ci_isolation_")


def _run_target(path, extra_env=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [_CODE_DIR, _TESTS_DIR, env.get("PYTHONPATH", "")])
    _harness.isolate_qdrant(env)
    env.pop(_PROBE_VAR, None)
    env.update(extra_env or {})
    return subprocess.run([sys.executable, path], cwd=_CODE_DIR, env=env,
                          capture_output=True, text=True, timeout=900)


def _skipped_count(text):
    for line in text.splitlines():
        if line.strip().startswith("Skipped:"):
            try:
                return int(line.split(":", 1)[1].strip().split()[0])
            except (ValueError, IndexError):
                return _Absent("Skipped: line did not parse")
    return _Absent("no Skipped: line")


_shipped = _run_target(_TARGET_PATH)
check("4a  the shipped file exits 0 with the probe variable unset",
      _shipped.returncode, 0)
check("4a  ...and records exactly one SKIP", _skipped_count(_shipped.stdout), 1)
check("4a  ...naming the variable an operator has to set",
      _PROBE_VAR in _shipped.stdout, True)
check("4a  ...and saying it does NOT fall back to the pipeline's own client",
      "does NOT fall back" in _shipped.stdout, True)

# --- the reverted copy ------------------------------------------------------
with open(_TARGET_PATH, encoding="utf-8") as _fh:
    _src = _fh.read()

_NEW_BLOCK_START = "_PROBE_URL_ENV = \"ONCOTRIAGE_QDRANT_PROBE_URL\""
_NEW_BLOCK_END = "empty SparseVector and returns zero points.\")"
_OLD_BLOCK = '''_probe_url = os.environ.get("ONCOTRIAGE_QDRANT_PROBE_URL")
_probe_collection = os.environ.get("ONCOTRIAGE_QDRANT_PROBE_COLLECTION",
                                   COLLECTION_NAME)

try:
    _probe_client = (QdrantClient(url=_probe_url, timeout=15)
                     if _probe_url else deps.get_qdrant_client())
    _probe_points = _probe_client.query_points(
        collection_name=_probe_collection,
        query=SparseVector(indices=[], values=[]),
        using="title-bm25",
        limit=5,
        with_payload=False,
    ).points
    print("  PROBE  empty SparseVector accepted")
except Exception as _probe_error:
    print("  SKIP   no reachable Qdrant (%s)" % type(_probe_error).__name__)'''

if _NEW_BLOCK_START not in _src or _NEW_BLOCK_END not in _src:
    fail("4b  the SECTION E block could be located for the control",
         "one of the two anchors is absent; the plant would be a no-op")
else:
    _i = _src.index(_NEW_BLOCK_START)
    _j = _src.index(_NEW_BLOCK_END) + len(_NEW_BLOCK_END)
    _reverted = _src[:_i] + _OLD_BLOCK + _src[_j:]
    check("4b  the plant really changed the source (not a no-op)",
          _reverted != _src, True)
    check("4b  ...and it restored the fallback the fix removed",
          "deps.get_qdrant_client()" in _reverted[_i:_i + len(_OLD_BLOCK)],
          True)

    _copy = os.path.join(_TMP, _TARGET)
    with open(_copy, "w", encoding="utf-8") as _fh:
        _fh.write(_reverted)

    _ctl = _run_target(_copy)
    check("4c  CONTROL: the pre-fix file records NO skip at all",
          _skipped_count(_ctl.stdout), 0)
    check("4c  ...so 4a's count of 1 can fail",
          _skipped_count(_ctl.stdout) == _skipped_count(_shipped.stdout),
          False)
    check("4c  ...and the pre-fix file does NOT name the variable in a skip "
          "message", "does NOT fall back" in _ctl.stdout, False)

# --- and the fallback is forbidden STRUCTURALLY, not only behaviourally ----
# MEASURED WHY THIS IS SEPARATE: restoring the fallback line ALONE changes
# nothing observable, because `if not _probe_url:` short-circuits above it and
# the line is unreachable. A revert matrix reported that plant as uncaught, and
# it was right to -- a plant that is not a behaviour change is not a test of
# anything. But a fallback sitting there unreachable is a trap re-armed for
# whoever next touches the guard above it, so it is forbidden on its own terms.
# AST, so the comment that explains the removal is invisible to the check.


def _get_qdrant_client_calls(tree):
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "get_qdrant_client"]


_target_tree = ast.parse(_src, filename=_TARGET)
check("4e  the target calls deps.get_qdrant_client() nowhere at all",
      len(_get_qdrant_client_calls(_target_tree)), 0)
if _NEW_BLOCK_START in _src and _NEW_BLOCK_END in _src:
    check("4e  CONTROL: the reverted copy does call it, so 4e can fail",
          len(_get_qdrant_client_calls(ast.parse(_reverted))), 1)

with open(_TARGET_PATH, "rb") as _fh:
    _TARGET_SHA_AFTER = hashlib.sha256(_fh.read()).hexdigest()
check("4d  the shipped file was never edited", _TARGET_SHA_AFTER,
      _TARGET_SHA_BEFORE)

shutil.rmtree(_TMP, ignore_errors=True)
check("4d  ...and the temp directory is gone", os.path.exists(_TMP), False)


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 74)
print("SUMMARY")
print("=" * 74)
print(f"  Passed: {_RESULTS['passed']}")
print(f"  Failed: {_RESULTS['failed']}")

if _FAILURES:
    print("\nFAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")

if _RESULTS["failed"]:
    sys.exit(1)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Sep 10 2026

@author: ramyalsaffar
"""
