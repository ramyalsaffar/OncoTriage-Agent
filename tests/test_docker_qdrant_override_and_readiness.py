# Docker: the Qdrant override, the readiness gate, the seeded lookups, the version
##################################################################################

"""Everything the Docker pass added, and a planted failure for every assertion.

THE FOUR THINGS UNDER TEST, and the defect each one closes:

    1. **The Qdrant endpoint override.** ``paths.load_env_keys()`` POPS
       ``QDRANT_URL`` out of ``os.environ`` and rewrites it from the .env (with
       ``override=True`` when this was measured; an ALLOWLIST write now, and
       ``QDRANT_URL`` is in the allowlist, so the guarantee is unchanged), so
       no environment variable could redirect Qdrant --
       measured inside the running container, where
       ``QDRANT_URL: http://qdrant:6333`` was set, popped, and the client still
       opened Qdrant Cloud. The pop is DELIBERATE (a stale exported credential
       must not shadow the credentials file) and is kept. A second,
       project-prefixed variable beats it: ``ONCOTRIAGE_QDRANT_URL``. Section 1
       drives all four required behaviours, each in its own subprocess because
       ``config`` caches the answer per process.

    2. **The empty-index gate.** A Qdrant collection with zero points answers
       every retrieval query SUCCESSFULLY with an empty list, the graph routes
       to ``node_no_candidates``, and the API returns 200 with "no eligible
       trials" -- indistinguishable from a patient who genuinely matches
       nothing. Sections 2 and 3 drive all four index states through
       ``probe_index`` and both policies through ``require_populated_index``.

    3. **"Healthy" now means "serviceable".** Six containers reported healthy on
       a stack whose ``/app/data/mesh/`` was empty and whose first ``POST
       /match`` died in Stage 1. Section 4 drives ``serving_readiness`` and
       section 5 drives the real ``GET /health`` through FastAPI's TestClient,
       asserting the STATUS CODE, which is what ``curl -f`` in the compose
       healthcheck reads.

    4. **The seeded MeSH lookups and the derived version.** Section 6 drives
       ``docker/prepare_paths.py:seed_mesh_core`` including its hash refusal;
       section 7 drives ``docker/app_version.py`` and asserts by AST that the
       Dockerfile's version label is not a literal any more.

NO NETWORK, NO KEYS, NO SPEND, NO DOCKER DAEMON. Every Qdrant client here is a
stand-in installed through ``oncotriage.agent.deps`` or passed as an argument;
no graph is compiled and no model is loaded. Section 1 runs subprocesses, and
each one imports ``oncotriage.config`` only -- which opens nothing.

WHY IT IS NOT IN THE COLLISION MATRIX, derived rather than assumed. It writes
only inside a temporary directory. It READS four repository files --
``Dockerfile``, ``docker-compose.yml``, ``oncotriage/agent/retrieval.py`` and
``docker/mesh-core/PROVENANCE.json`` -- and none of them is written by either of
the suite's two writers (``oncotriage/registries/cancer_code_registry.py`` and
``oncotriage/config.py``). It imports ``oncotriage.config`` in section 1's
subprocesses, but only to read resolved strings; it asserts nothing about
``DATA_SNAPSHOT_DATE``, which is the one value the snapshot-date test rewrites.

Run from terminal:
    python tests/test_docker_qdrant_override_and_readiness.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# The package bootstrap every file in tests/ carries; the candidate directory
# is the PARENT of this file's. `pip install -e .` makes it a no-op.
import os
import sys

try:
    import oncotriage  # noqa: F401
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

import ast
import copy
import hashlib
import importlib
import json
import re
import shutil
import subprocess
import tempfile

import oncotriage as _oncotriage_pkg
from oncotriage import settings as _settings
from oncotriage.agent import deps as _deps
from oncotriage.agent import readiness as _readiness


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
        _FAILURES.append(f"{label}\n          expected: {expected}\n"
                         f"          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def fail(label: str, why: str) -> None:
    """Record a failure that is not an equality mismatch."""
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {why}")
    print(f"  FAIL  {label}")
    print(f"          {why}")


def raises(fn):
    """(exception type name, message) for a call that must raise, else (None, '')."""
    try:
        fn()
    except Exception as exc:            # noqa: BLE001 -- the type is the answer
        return type(exc).__name__, str(exc)
    return None, ""


# THE REPOSITORY ROOT IS DERIVED FROM THE PACKAGE, not from this file's parent.
# tests/ has moved once already (pass 20d-1) and files that walked up from their
# own location all broke; a path taken from the module this process actually
# imported cannot resolve to a same-named copy somewhere else.
_CODE_DIR = os.path.dirname(os.path.dirname(
    os.path.abspath(_oncotriage_pkg.__file__)))

_SCRATCH = tempfile.mkdtemp(prefix="oncotriage_docker_pass_")


def _repo(*parts):
    return os.path.join(_CODE_DIR, *parts)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1: the Qdrant endpoint override
# ===========================================================================
#
# ONE SUBPROCESS PER CASE, and that is forced rather than tidy.
# `config._resolve_qdrant_endpoint` caches per process by design (the whole
# point is that the answer is announced once and cannot change under a running
# pipeline), and `paths.load_env_keys` mutates os.environ. Six cases in one
# process would test the cache, not the resolution.
#
# THE FOUR REQUIRED BEHAVIOURS, each mapped to the case that proves it:
#
#   * an accidental exported QDRANT_URL still cannot shadow .env  -> case B
#   * a deliberate override DOES redirect, and logs which source  -> cases C, D
#   * the compose file can set it for the container               -> case C + 1e
#   * the host default is unchanged                               -> case A
#
# Case E is the discriminating one: BOTH variables set, and the deliberate one
# must win. Without it, case B would also be satisfied by an implementation that
# ignored the environment entirely.

print("\n" + "=" * 74)
print("SECTION 1: ONCOTRIAGE_QDRANT_URL beats the .env; QDRANT_URL still does not")
print("=" * 74)

# THE PROBE READS THE FUNCTIONS THE CLIENT IS BUILT FROM, not the reporter.
#
# Its first version printed `config.qdrant_endpoint_sources()` alone, and a
# revert harness caught that: reverting `get_qdrant_url()` to
# `return get_keys()['qdrant_url']` -- the exact pre-pass behaviour, the whole
# defect -- left every assertion in this section PASSING, because
# `qdrant_endpoint_sources()` still resolved through the override. The section
# was measuring the report and not the decision, which is the same shape as an
# assertion that agrees with the code by construction.
#
# So the probe reads `get_qdrant_url()` and `get_qdrant_api_key()` -- the two
# functions `get_qdrant_client()` passes to QdrantClient -- and 1i asserts by
# AST that those are the two it passes. No client is constructed: qdrant-client
# probes the server's version on construction, which would be a network call.
#
# The KEY is never printed. What is printed is a classification, which is all
# any assertion here needs and is not a credential.
_PROBE = (
    "from oncotriage import config;"
    "u = config.get_qdrant_url();"
    "k = config.get_qdrant_api_key();"
    "s = config.qdrant_endpoint_sources();"
    "kind = 'none' if k is None else ('named' if k == 'a-local-key' else 'env-file');"
    "print('RESULT', u, s['url_source'], s['api_key_source'], kind,"
    " 'AGREE' if s['url'] == u else 'DISAGREE', sep='|')"
)


def _resolve_in_subprocess(env_overrides):
    """(url, url_source, key_source, stdout+stderr) from a fresh interpreter."""
    env = dict(os.environ)
    # Start from a clean slate so the developer's own shell cannot decide a case.
    for name in ("QDRANT_URL", "QDRANT_API_KEY",
                 _settings.ENV_QDRANT_URL, _settings.ENV_QDRANT_API_KEY):
        env.pop(name, None)
    env.update(env_overrides)

    proc = subprocess.run([sys.executable, "-c", _PROBE], cwd=_CODE_DIR,
                          env=env, capture_output=True, text=True)
    blob = proc.stdout + proc.stderr
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT|"):
            _, url, url_src, key_src, kind, agree = line.split("|", 5)
            return url, url_src, f"{key_src}", blob, kind, agree
    return None, None, None, blob, None, None


_ENV_URL = _settings.ENV_QDRANT_URL
_ENV_KEY = _settings.ENV_QDRANT_API_KEY

_a_url, _a_src, _a_key, _a_log, _a_kind, _a_agree = _resolve_in_subprocess({})
_b_url, _b_src, _b_key, _b_log, _b_kind, _b_agree = _resolve_in_subprocess(
    {"QDRANT_URL": "http://accidental-export:6333"})
_c_url, _c_src, _c_key, _c_log, _c_kind, _c_agree = _resolve_in_subprocess(
    {_ENV_URL: "http://qdrant:6333"})
_d_url, _d_src, _d_key, _d_log, _d_kind, _d_agree = _resolve_in_subprocess(
    {_ENV_URL: "http://qdrant:6333", _ENV_KEY: "a-local-key"})
_e_url, _e_src, _e_key, _e_log, _e_kind, _e_agree = _resolve_in_subprocess(
    {"QDRANT_URL": "http://accidental-export:6333",
     _ENV_URL: "http://qdrant:6333"})

# --- 1a. the host default is unchanged --------------------------------------
# NON-DEGENERACY FIRST. Every comparison below is against `_a_url`, and if the
# .env could not be read at all the resolver would have raised and _a_url would
# be None -- against which "the accidental export did not change it" is true and
# meaningless.
if _a_url is None:
    fail("the .env resolves at all (non-degeneracy for all of section 1)",
         f"the baseline subprocess produced no RESULT line. Output:\n{_a_log}")
else:
    check("1a  no override: the endpoint comes from the .env",
          _a_src, "keys/.env")
    check("1a  ...and so does the api key", _a_key, "keys/.env")
    check("1a  ...and the url is a real https endpoint (non-degenerate)",
          _a_url.startswith("https://") and len(_a_url) > 20, True)

    # --- 1b. an accidental export still cannot shadow the .env --------------
    check("1b  an exported QDRANT_URL does NOT redirect the client",
          _b_url, _a_url)
    check("1b  ...and the source still reports the .env", _b_src, "keys/.env")

    # --- 1c. a deliberate override does redirect ---------------------------
    check("1c  ONCOTRIAGE_QDRANT_URL redirects the client",
          _c_url, "http://qdrant:6333")
    check("1c  ...and it is NOT the .env url (the redirect is real)",
          _c_url == _a_url, False)
    check("1c  ...and the source names the variable that won", _c_src, _ENV_URL)
    check("1c  ...and with no key named, NO key is sent",
          _c_key, "none (URL overridden, no key named)")
    check("1c  ...and the decision is printed, not merely taken",
          "[Qdrant] endpoint http://qdrant:6333 (from ONCOTRIAGE_QDRANT_URL)"
          in _c_log, True)

    check("1c  ...and get_qdrant_api_key() really returns None, not the .env "
          "key (the value, not the label)", _c_kind, "none")

    # --- 1d. a named key is used -------------------------------------------
    check("1d  ONCOTRIAGE_QDRANT_API_KEY is used when the URL is overridden",
          _d_key, _ENV_KEY)
    check("1d  ...and it is the OVERRIDE's value that comes back", _d_kind,
          "named")
    check("1d  ...control: with no override the .env's key is what comes back",
          _a_kind, "env-file")

    # THE REPORTER AND THE DECIDER MUST AGREE. `qdrant_endpoint_sources()` is
    # what GET /pipeline/info publishes and what a bring-up log prints;
    # `get_qdrant_url()` is what the client is built from. If they ever diverge,
    # every report in this project would describe an endpoint the pipeline is
    # not using -- and this section, which reads both, is the only thing that
    # would notice.
    check("1c  the reported endpoint IS the one the client is built from, in "
          "every case",
          [_a_agree, _b_agree, _c_agree, _d_agree, _e_agree],
          ["AGREE"] * 5)

    # --- 1e. the deliberate one beats the accidental one -------------------
    # THE DISCRIMINATING CASE. 1b alone would also pass on an implementation
    # that ignored os.environ entirely.
    check("1e  with BOTH set, the project-prefixed variable wins",
          _e_url, "http://qdrant:6333")
    check("1e  ...and the accidental one reaches nothing",
          "accidental-export" in _e_url, False)

# --- 1f. a malformed override raises, naming the variable -------------------
_f_url, _f_src, _f_key, _f_log, _f_kind, _f_agree = _resolve_in_subprocess(
    {_ENV_URL: "qdrant:6333"})
check("1f  a value that is not a URL raises rather than being guessed at",
      _f_url is None, True)
check("1f  ...and the message names the variable",
      _ENV_URL in _f_log and "which is not a URL" in _f_log, True)

# --- 1g. the resolvers themselves, in-process, including the empty case -----
# `_from_env` would have appended os.sep to both of these. That is the reason
# they are separate functions, and it is asserted rather than described.
_saved_env = {k: os.environ.get(k) for k in (_ENV_URL, _ENV_KEY)}
try:
    os.environ[_ENV_URL] = "  http://spaced:6333  \n"
    check("1g  whitespace and a trailing newline are stripped",
          _settings.resolve_qdrant_url(), ("http://spaced:6333", _ENV_URL))
    check("1g  ...and no separator is appended (the _from_env trap)",
          _settings.resolve_qdrant_url()[0].endswith(os.sep), False)

    os.environ[_ENV_URL] = "   "
    check("1g  an empty value is 'not set', not an empty endpoint",
          _settings.resolve_qdrant_url(), (None, None))

    os.environ[_ENV_KEY] = "  secret-key\n"
    check("1g  the key is stripped too", _settings.resolve_qdrant_api_key(),
          ("secret-key", _ENV_KEY))
    check("1g  ...and carries no trailing separator",
          _settings.resolve_qdrant_api_key()[0].endswith(os.sep), False)
finally:
    for _k, _v in _saved_env.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

# --- 1h. the pop is still there, and this is the CONTROL for 1b -------------
# 1b asserts that an exported QDRANT_URL does not win. That would also be true
# of an implementation that never read the environment for credentials at all --
# so the mechanism 1b depends on is asserted directly, against the source.
_paths_src = open(_repo("oncotriage", "paths.py"), encoding="utf-8").read()
_pop_tree = ast.parse(_paths_src)
_pops = [n for n in ast.walk(_pop_tree)
         if isinstance(n, ast.Call)
         and isinstance(n.func, ast.Attribute) and n.func.attr == "pop"]
check("1h  load_env_keys still pops the credential names from os.environ "
      "(the mechanism 1b relies on, kept deliberately)",
      len(_pops) >= 1, True)
check("1h  ...and REQUIRED_ENV_KEYS still names QDRANT_URL",
      "QDRANT_URL" in _paths_src and "REQUIRED_ENV_KEYS" in _paths_src, True)

# --- 1i. the client is BUILT from the two functions section 1 measured -------
# The subprocess probe reads get_qdrant_url() and get_qdrant_api_key(). That is
# only the right thing to read if those are what QdrantClient is handed, and
# nothing in a subprocess can see that without opening a connection. Asserted by
# AST instead, with a control.
_config_tree = ast.parse(open(_repo("oncotriage", "config.py"),
                              encoding="utf-8").read())
_client_fn = next((n for n in ast.walk(_config_tree)
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "get_qdrant_client"), None)

if _client_fn is None:
    fail("1i  config.get_qdrant_client exists",
         "not found; the probe in this section may be reading the wrong thing")
else:
    _qc = next((n for n in ast.walk(_client_fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "QdrantClient"), None)
    _kw = {k.arg: k for k in (_qc.keywords if _qc else [])}
    _called = {name: (isinstance(_kw[name].value, ast.Call)
                      and isinstance(_kw[name].value.func, ast.Name)
                      and _kw[name].value.func.id == fn)
               for name, fn in (("url", "get_qdrant_url"),
                                ("api_key", "get_qdrant_api_key"))
               if name in _kw}
    check("1i  QdrantClient(url=...) is get_qdrant_url(), the function section "
          "1 measured", _called.get("url"), True)
    check("1i  QdrantClient(api_key=...) is get_qdrant_api_key()",
          _called.get("api_key"), True)
    check("1i  ...control: the scan can tell a different callee apart",
          isinstance(_kw.get("timeout"), ast.keyword)
          and isinstance(_kw["timeout"].value, ast.Call), False)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2: probe_index reports all four states
# ===========================================================================

print("\n" + "=" * 74)
print("SECTION 2: the index probe distinguishes populated / empty / absent / "
      "unverifiable")
print("=" * 74)


class _Count:
    def __init__(self, n):
        self.count = n


class _StubQdrant:
    """A Qdrant client that answers exactly the two calls the probe makes.

    Deliberately NOT a MagicMock. A mock answers `collection_exists` with a
    truthy Mock and `count(...).count` with another Mock, so `points > 0` is
    True and the probe reports `populated` whatever it was handed -- a stub that
    makes every assertion pass is the shape this project keeps finding.
    """

    def __init__(self, exists=True, points=0, raise_on=None):
        self._exists = exists
        self._points = points
        self._raise_on = raise_on
        self.calls = []

    def collection_exists(self, name):
        self.calls.append(("collection_exists", name))
        if self._raise_on == "collection_exists":
            raise ConnectionError("stub: no route to host")
        return self._exists

    def count(self, name, exact=True):
        self.calls.append(("count", name, exact))
        if self._raise_on == "count":
            raise TimeoutError("stub: read timed out")
        return _Count(self._points)


_v_pop = _readiness.probe_index(client=_StubQdrant(exists=True, points=12067),
                                collection="trial_criteria")
_v_empty = _readiness.probe_index(client=_StubQdrant(exists=True, points=0),
                                  collection="trial_criteria")
_v_absent = _readiness.probe_index(client=_StubQdrant(exists=False),
                                   collection="trial_criteria")
_v_unver = _readiness.probe_index(
    client=_StubQdrant(raise_on="collection_exists"), collection="trial_criteria")
_v_unver2 = _readiness.probe_index(
    client=_StubQdrant(exists=True, raise_on="count"), collection="trial_criteria")

check("2a  a populated collection reports 'populated'",
      _v_pop["state"], _readiness.INDEX_POPULATED)
check("2a  ...and carries the count", _v_pop["points"], 12067)
check("2b  a collection with zero points reports 'empty', NOT populated",
      _v_empty["state"], _readiness.INDEX_EMPTY)
check("2b  ...and 0 is reported as 0, not as None", _v_empty["points"], 0)
check("2c  a missing collection reports 'absent', not 'empty'",
      _v_absent["state"], _readiness.INDEX_ABSENT)
check("2c  ...and does not invent a count", _v_absent["points"], None)
check("2d  a transport failure on the existence call reports 'unverifiable'",
      _v_unver["state"], _readiness.INDEX_UNVERIFIABLE)
check("2d  ...and carries the exception, not a guess",
      "ConnectionError" in (_v_unver["error"] or ""), True)
check("2d  a transport failure on the COUNT is also 'unverifiable'",
      _v_unver2["state"], _readiness.INDEX_UNVERIFIABLE)
check("2d  ...naming the second call's exception",
      "TimeoutError" in (_v_unver2["error"] or ""), True)

# The counter. `unverifiable` is the one state that does not block, so the rule
# "no exception recovered from without being recorded" is what makes it safe.
check("2e  every unverifiable probe is COUNTED by exception type",
      _readiness.INDEX_PROBE_FAILURES["ConnectionError"] >= 1
      and _readiness.INDEX_PROBE_FAILURES["TimeoutError"] >= 1, True)

# The probe must not raise, ever: two callers rely on it as a diagnostic, and a
# diagnostic that dies replaces a precise report with an unrelated traceback.
_exc, _msg = raises(lambda: _readiness.probe_index(
    client=_StubQdrant(raise_on="collection_exists")))
check("2f  probe_index itself raises nothing (it is a diagnostic)", _exc, None)

# Non-degeneracy: the stub was actually asked. Without this, a probe that
# short-circuited and returned a canned verdict would pass everything above.
_witness = _StubQdrant(exists=True, points=5)
_readiness.probe_index(client=_witness, collection="trial_criteria")
check("2g  the probe really asked the client (non-degeneracy)",
      _witness.calls,
      [("collection_exists", "trial_criteria"), ("count", "trial_criteria", True)])
check("2g  ...and asked for an EXACT count, not an estimate",
      _witness.calls[1][2], True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3: require_populated_index applies the per-request policy
# ===========================================================================

print("\n" + "=" * 74)
print("SECTION 3: Stage 2's gate raises on empty/absent and continues on "
      "unverifiable")
print("=" * 74)

_readiness.reset_index_probe_cache()

_exc_empty, _msg_empty = raises(lambda: _readiness.require_populated_index(
    client=_StubQdrant(exists=True, points=0), collection="trial_criteria"))
check("3a  an EMPTY index raises", _exc_empty, "EmptyIndexError")
check("3a  ...and the message says why an empty result set is not an answer",
      "indistinguishable from a patient who genuinely matches no trial"
      in _msg_empty, True)
check("3a  ...and names the command that fixes it",
      "11- RAG Trial Indexer.py" in _msg_empty, True)

_readiness.reset_index_probe_cache()
_exc_absent, _msg_absent = raises(lambda: _readiness.require_populated_index(
    client=_StubQdrant(exists=False), collection="trial_criteria"))
check("3b  an ABSENT collection raises too", _exc_absent, "EmptyIndexError")
check("3b  ...and says the collection does not exist, not that it is empty",
      "no collection or alias named" in _msg_absent, True)

# THE POSITIVE CONTROL. Without it, 3a and 3b are satisfied by a gate that
# raises unconditionally.
_readiness.reset_index_probe_cache()
_exc_ok, _ = raises(lambda: _readiness.require_populated_index(
    client=_StubQdrant(exists=True, points=1), collection="trial_criteria"))
check("3c  a POPULATED index does not raise (control: the gate is not "
      "unconditional)", _exc_ok, None)

# One point is enough. A threshold nobody chose would be a second tunable.
check("3c  ...and one point is enough; the gate is about zero, not about depth",
      _readiness.probe_index(client=_StubQdrant(exists=True, points=1))["state"],
      _readiness.INDEX_POPULATED)

# The cache is one-way, and both directions are asserted.
_readiness.reset_index_probe_cache()
_readiness.require_populated_index(client=_StubQdrant(exists=True, points=7))
_after_good = _StubQdrant(exists=True, points=0)
_exc_cached, _ = raises(lambda: _readiness.require_populated_index(
    client=_after_good))
check("3d  a POPULATED verdict is cached: the next call does not re-probe",
      (_exc_cached, _after_good.calls), (None, []))

_readiness.reset_index_probe_cache()
_bad1 = _StubQdrant(exists=True, points=0)
raises(lambda: _readiness.require_populated_index(client=_bad1))
_bad2 = _StubQdrant(exists=True, points=0)
raises(lambda: _readiness.require_populated_index(client=_bad2))
check("3d  ...but a BLOCKING verdict is NOT cached, so a stack recovers on its "
      "own once the index is populated",
      len(_bad2.calls) > 0, True)

# The unverifiable policy: count, print, continue. This is the branch that could
# silently turn the whole gate off, so it is asserted in both directions.
_readiness.reset_index_probe_cache()
_before = _readiness.INDEX_PROBE_FAILURES["ConnectionError"]
_exc_unver, _ = raises(lambda: _readiness.require_populated_index(
    client=_StubQdrant(raise_on="collection_exists")))
check("3e  an UNVERIFIABLE probe does not block the request", _exc_unver, None)
check("3e  ...and is counted, so a thin run can be checked afterwards",
      _readiness.INDEX_PROBE_FAILURES["ConnectionError"] - _before, 1)

_readiness.reset_index_probe_cache()


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4: serving_readiness
# ===========================================================================

print("\n" + "=" * 74)
print("SECTION 4: serving readiness is the conjunction of MeSH and the index")
print("=" * 74)


class _StubMeshFilter:
    """Stands in for a loaded MeSHCancerFilter. Only its presence is read."""


def _readiness_with(mesh_value, qdrant_client):
    """Run serving_readiness with both dependencies installed through deps."""
    _readiness.reset_index_probe_cache()
    saved = _deps.set_overrides({
        _deps.MESH_FILTER: mesh_value,
        _deps.QDRANT_CLIENT: qdrant_client,
    })
    try:
        return _readiness.serving_readiness()
    finally:
        _deps.restore_overrides(saved)


_r_ok = _readiness_with(_StubMeshFilter(), _StubQdrant(exists=True, points=99))
_r_no_index = _readiness_with(_StubMeshFilter(), _StubQdrant(exists=False))
_r_unver = _readiness_with(_StubMeshFilter(),
                           _StubQdrant(raise_on="collection_exists"))

check("4a  both dependencies present -> ready", _r_ok["status"], _readiness.READY)
check("4a  ...and every check reports ok",
      [c["ok"] for c in _r_ok["checks"]], [True, True])
check("4a  ...and the index check states the count and the endpoint",
      "99 points" in _r_ok["checks"][1]["detail"], True)

check("4b  an absent index -> NOT ready", _r_no_index["status"],
      _readiness.NOT_READY)
check("4b  ...and the failing check is named",
      [c["name"] for c in _r_no_index["checks"] if not c["ok"]], ["trial_index"])

# THE ASYMMETRY WITH SECTION 3e, ASSERTED. The same verdict that does NOT block
# a request DOES block readiness, and that difference is a decision rather than
# an accident, so it is checked rather than described.
check("4c  an UNVERIFIABLE index -> NOT ready, unlike at request time",
      _r_unver["status"], _readiness.NOT_READY)
check("4c  ...and says so in the words of the decision",
      "'cannot tell' as 'not ready'" in _r_unver["checks"][1]["detail"], True)

# The MeSH half. The DegradedDependencyError item 11a raises is CARRIED, not
# rewritten -- so a caller of /health sees the same message, naming both files
# and the rebuild command, that a POST /match would have produced.
_mesh_error = _settings.degraded_dependency_error(
    "mesh_c04_core",
    "core lookup file(s) not found:\n    - /app/data/mesh/mesh_c04_lookup.json",
    'python "09- MeSH Cancer Site Relevance Filter.py"')


class _RaisingMeshFactory:
    """Installed as the MESH_FILTER override; deps hands it back, and
    serving_readiness never calls it -- so the raise has to come from the
    accessor. The override IS the object, so a raise is arranged by making
    get_mesh_filter itself the thing under test instead. See below."""


_saved_mesh = _deps.set_overrides({_deps.QDRANT_CLIENT:
                                   _StubQdrant(exists=True, points=1)})
_real_get_mesh = _readiness.deps.get_mesh_filter
try:
    _readiness.reset_index_probe_cache()

    def _boom():
        raise _mesh_error

    _readiness.deps.get_mesh_filter = _boom
    _r_no_mesh = _readiness.serving_readiness()
finally:
    _readiness.deps.get_mesh_filter = _real_get_mesh
    _deps.restore_overrides(_saved_mesh)

check("4d  a missing MeSH lookup -> NOT ready", _r_no_mesh["status"],
      _readiness.NOT_READY)
check("4d  ...and item 11a's message is CARRIED VERBATIM, not rewritten",
      "09- MeSH Cancer Site Relevance Filter.py"
      in _r_no_mesh["checks"][0]["detail"]
      and "mesh_c04_lookup.json" in _r_no_mesh["checks"][0]["detail"], True)
check("4d  ...and the raise is recorded rather than swallowed: the process is "
      "still running and can be asked",
      _r_no_mesh["checks"][0]["ok"], False)

# A deliberately-degraded MeSH filter (None) is NOT a failure: item 11a's
# opt-out is the operator's decision and this probe must not second-guess it.
_r_degraded = _readiness_with(None, _StubQdrant(exists=True, points=1))
check("4e  a DELIBERATELY disabled MeSH filter (None) is ready, and says so",
      _r_degraded["status"], _readiness.READY)
check("4e  ...while still reporting that Stage 4's site filter will not run",
      "DISABLED" in _r_degraded["checks"][0]["detail"], True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 5: GET /health returns 503, which is what `curl -f` reads
# ===========================================================================
#
# THE STATUS CODE IS THE WHOLE MECHANISM. docker-compose.yml probes /health with
# `curl -f`, which fails on 4xx/5xx and succeeds on 200. A body that says
# "unhealthy" with a 200 status leaves the container green, which is the defect.

print("\n" + "=" * 74)
print("SECTION 5: the compose healthcheck can actually see the failure")
print("=" * 74)

try:
    from fastapi.testclient import TestClient
except ImportError as _exc:                      # pragma: no cover
    fail("section 5 can run (fastapi TestClient importable)", repr(_exc))
    TestClient = None

if TestClient is not None:
    from oncotriage.api import server as _server

    # The graph is NOT compiled: `lifespan` is skipped by driving the routes
    # directly rather than through the context manager, and `graph` is set by
    # hand. Compiling it costs nothing here but importing langgraph's compiled
    # object is not what this section is about, and `/health` reads only whether
    # the name is None.
    _saved_graph = _server.graph
    _saved_all = _deps.set_overrides({_deps.MESH_FILTER: _StubMeshFilter()})
    try:
        _server.graph = object()          # "the graph compiled"

        with TestClient(_server.app, raise_server_exceptions=False) as _c:
            # TestClient's context manager RUNS lifespan, which compiles the
            # real graph. It is entered once, with a populated stub installed,
            # so startup is the healthy case and the unhealthy ones below are
            # per-request.
            _deps.set_override(_deps.QDRANT_CLIENT,
                               _StubQdrant(exists=True, points=42))
            _readiness.reset_index_probe_cache()
            _ok = _c.get("/health")

            _deps.set_override(_deps.QDRANT_CLIENT, _StubQdrant(exists=False))
            _readiness.reset_index_probe_cache()
            _bad = _c.get("/health")

            _deps.set_override(_deps.QDRANT_CLIENT,
                               _StubQdrant(exists=True, points=0))
            _readiness.reset_index_probe_cache()
            _empty = _c.get("/health")

        check("5a  a serviceable server answers /health 200", _ok.status_code, 200)
        check("5a  ...and says healthy", _ok.json()["status"], "healthy")
        check("5a  ...and pipeline_ready is still reported",
              _ok.json()["pipeline_ready"], True)

        check("5b  an ABSENT index makes /health 503, so `curl -f` fails",
              _bad.status_code, 503)
        check("5b  ...and the body names the failing dependency",
              [c["name"] for c in _bad.json()["checks"] if not c["ok"]],
              ["trial_index"])
        check("5b  ...while pipeline_ready is STILL true — which is exactly the "
              "field that used to report health on its own",
              _bad.json()["pipeline_ready"], True)

        check("5c  an EMPTY index makes /health 503 as well",
              _empty.status_code, 503)

        # THE RECOVERY PROPERTY. /health re-probes rather than reporting what
        # startup found, so populating the index turns the container green with
        # no restart. Asserted by going 200 -> 503 -> 200 in one process.
        _deps.set_override(_deps.QDRANT_CLIENT,
                           _StubQdrant(exists=True, points=12))
        _readiness.reset_index_probe_cache()
        with TestClient(_server.app, raise_server_exceptions=False) as _c2:
            _recovered = _c2.get("/health")
        check("5d  /health recovers to 200 once the index is populated, with no "
              "restart", _recovered.status_code, 200)
    finally:
        _server.graph = _saved_graph
        _deps.restore_overrides(_saved_all)
        _readiness.reset_index_probe_cache()

# --- 5e. the compose file really probes this endpoint -----------------------
# Every assertion above is about a status code that only matters if the
# healthcheck reads it. Checked against the file rather than assumed.
#
# THE COMMENTS ARE STRIPPED FIRST, and the first version of this section is why.
# `"ONCOTRIAGE_QDRANT_API_KEY" in _compose_text` was False-expected and came
# back True -- because the comment beside the setting EXPLAINS that the key is
# deliberately not set. A substring search over a file that argues about its own
# settings reads the argument as a setting. Same lesson as pass 20e's section
# 1c, which reported nine docstrings as exec-chain calls, and as the BM25
# construction-site check: a substring is not a declaration.
_compose_text = open(_repo("docker-compose.yml"), encoding="utf-8").read()


def _compose_settings():
    """Every `key: value` line in the compose file, comments removed.

    Not a YAML parse: the file uses merge keys and anchors, and resolving them
    would answer a different question ("what does service X end up with") than
    the one here ("is this setting written down"). What is needed is the set of
    lines that are settings rather than prose.
    """
    out = {}
    for raw in _compose_text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.strip().partition(":")
        out.setdefault(key.strip().lstrip("- "), []).append(value.strip())
    return out


_settings_in_compose = _compose_settings()

check("5e  the fastapi healthcheck curls /health with -f (so a 503 is a "
      "failure)",
      'curl", "-f", "http://localhost:8000/health' in _compose_text, True)
check("5e  ...and the compose file sets ONCOTRIAGE_QDRANT_URL at the shared "
      "app environment, so every service gets it",
      _settings_in_compose.get("ONCOTRIAGE_QDRANT_URL"), ["http://qdrant:6333"])
check("5e  ...and does NOT set the plain QDRANT_URL, which would do nothing",
      "QDRANT_URL" in _settings_in_compose, False)
check("5e  ...and names no api key for it (the .env's cloud key must not be "
      "forwarded to the sidecar)",
      "ONCOTRIAGE_QDRANT_API_KEY" in _settings_in_compose, False)
check("5e  ...and the comment-stripper is not vacuous: it still sees settings",
      _settings_in_compose.get("DOCKER_CONTAINER"), ['"true"'])

# --- 5f. a containerised run can name the build that produced it ------------
# WITHOUT THIS THE RECORD DEGRADES SILENTLY-ish: every containerised run wrote
# `image_identity_source = 'containerised_unrecorded'`, which
# oncotriage/environment.py counts and warns about but which no configuration
# could clear -- so the degradation was permanent rather than actionable.
#
# WHAT IS CHECKED IS THE WIRING, NOT THE VALUE. `image_identity()`'s own three
# states are driven in tests/test_run_environment_record.py (1k/1l/1m); what
# only this file can see is that docker-compose.yml actually supplies the
# channel, at the SHARED anchor so all five app services get it, from the SAME
# variable the APP_VERSION build arg reads.
_image_tag_in_compose = _settings_in_compose.get("ONCOTRIAGE_IMAGE_TAG")
_app_version_arg = _settings_in_compose.get("APP_VERSION")


def _interpolated_variable(values):
    """The `${NAME` a compose setting interpolates, or None.

    Returns the NAME rather than the whole expression so the two settings can
    be compared on their SOURCE while differing, correctly, in their defaults:
    the build arg defaults to a sentinel a guard rejects and the environment
    variable defaults to empty. Comparing the raw strings would fail on that
    difference, which is the one thing about them that must differ.
    """
    if not values or len(values) != 1:
        return None
    match = re.match(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)", values[0].strip())
    return match.group(1) if match else None


check("5f  the compose file supplies the image-identity channel at the shared "
      "app environment, so every app service can name its build",
      _image_tag_in_compose is not None, True)
check("5f  ...and it is INTERPOLATED, not a literal (a hardcoded tag would be "
      "the same string on every build ever made here)",
      _interpolated_variable(_image_tag_in_compose) is not None, True)
check("5f  ...from the SAME variable the APP_VERSION build arg reads, so the "
      "recorded identity cannot disagree with the label STAGE 2 guards",
      _interpolated_variable(_image_tag_in_compose),
      _interpolated_variable(_app_version_arg))
check("5f  ...and NON-DEGENERACY: that variable was really found",
      _interpolated_variable(_app_version_arg), "ONCOTRIAGE_APP_VERSION")
# THE DEFAULT IS EMPTY AND MUST STAY EMPTY. `:-unset` -- which is right for the
# build arg, where STAGE 2's `RUN --check` REJECTS the sentinel -- would here be
# a fake identity that is never counted, because nothing on this path rejects
# anything: settings._resolve_image_field would return the literal "unset" and
# environment.image_identity() would report it as a build_tag, replacing an
# honest degradation with a value that looks like an answer.
check("5f  ...and it defaults to EMPTY, so an unsupplied version still "
      "degrades and counts rather than recording a sentinel as an identity",
      _image_tag_in_compose, ["${ONCOTRIAGE_APP_VERSION:-}"])


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6: the MeSH core lookups are seeded, verified, and never overwritten
# ===========================================================================

print("\n" + "=" * 74)
print("SECTION 6: seed_mesh_core")
print("=" * 74)


def _load_prepare_paths():
    """Import docker/prepare_paths.py as an ordinary top-level module.

    It is not a package module -- it lives in docker/ and is copied to
    /usr/local/lib/oncotriage-docker/ in the image -- so no import statement in
    the tree reaches it, and the obvious way to load it here is
    ``importlib.util.spec_from_file_location``.

    THAT IS EXACTLY WHAT SECTION 1c OF tests/test_package_invariants.py FORBIDS,
    repository-wide, with no allowlist for by-location loads at all -- and the
    first version of this file did it anyway, having written a comment
    predicting the conflict instead of avoiding it. The invariants file caught
    it: `('tests/test_docker_qdrant_override_and_readiness.py', 828,
    'chain-call', "importlib.util.spec_from_file_location(...)")`.
    A rule that survives because everyone who trips it gets an exemption is not
    a rule.

    Putting the directory on sys.path and importing by NAME is an ordinary
    import: it consults sys.path, it registers one module under one name, and it
    is the same mechanism every numbered entry point's six-line bootstrap uses.
    sys.path is restored so nothing later in this process resolves a stray
    top-level name out of docker/.
    """
    docker_dir = _repo("docker")
    sys.path.insert(0, docker_dir)
    try:
        return importlib.import_module("prepare_paths")
    finally:
        if sys.path and sys.path[0] == docker_dir:
            sys.path.pop(0)


_pp = _load_prepare_paths()

_SRC_DIR = _repo("docker", "mesh-core")
_MANIFEST = os.path.join(_SRC_DIR, "PROVENANCE.json")

check("6a  the vendored lookups are in the build context", os.path.isdir(_SRC_DIR),
      True)
check("6a  ...with a provenance manifest", os.path.isfile(_MANIFEST), True)

_manifest = json.load(open(_MANIFEST, encoding="utf-8"))
check("6a  ...naming exactly the two files load_mesh_filter() REQUIRES",
      sorted(_manifest["files"]),
      ["mesh_c04_lookup.json", "mesh_tree_to_name.json"])

# The manifest must describe the files that are actually there. A hash check
# whose expectations were copied from nothing is a hash check that cannot fail.
_manifest_ok = True
for _name, _entry in _manifest["files"].items():
    _p = os.path.join(_SRC_DIR, _name)
    _actual = hashlib.sha256(open(_p, "rb").read()).hexdigest()
    if _actual != _entry["sha256"]:
        _manifest_ok = False
        fail(f"6a  PROVENANCE.json matches the shipped {_name}",
             f"manifest says {_entry['sha256']}, file is {_actual}")
if _manifest_ok:
    check("6a  PROVENANCE.json's hashes match the shipped files", True, True)

# --- 6b. a clean volume is seeded ------------------------------------------
_dest_clean = os.path.join(_SCRATCH, "mesh_clean")
_rows_clean = _pp.seed_mesh_core(source_dir=_SRC_DIR, dest_dir=_dest_clean)
check("6b  a clean data volume is seeded with both files",
      sorted((n, s) for n, s in _rows_clean),
      [("mesh_c04_lookup.json", "seeded"), ("mesh_tree_to_name.json", "seeded")])
check("6b  ...and the bytes on disk are the vendored bytes",
      hashlib.sha256(open(os.path.join(_dest_clean,
                                       "mesh_c04_lookup.json"), "rb").read()
                     ).hexdigest(),
      _manifest["files"]["mesh_c04_lookup.json"]["sha256"])

# --- 6c. it is idempotent and never overwrites ------------------------------
# The `docker compose cp` route in DOCKER CLEAN BRING-UP.md stays usable only if
# a file already in the volume is left alone -- otherwise every restart would
# stamp the vendored copy back over a newer one.
_dest_present = os.path.join(_SCRATCH, "mesh_present")
os.makedirs(_dest_present)
_sentinel = os.path.join(_dest_present, "mesh_c04_lookup.json")
open(_sentinel, "w", encoding="utf-8").write('{"i am": "a newer lookup"}')
_rows_present = _pp.seed_mesh_core(source_dir=_SRC_DIR, dest_dir=_dest_present)
check("6c  a file already in the volume is reported present, not re-seeded",
      dict(_rows_present)["mesh_c04_lookup.json"], "present")
check("6c  ...and is byte-unchanged",
      open(_sentinel, encoding="utf-8").read(), '{"i am": "a newer lookup"}')
check("6c  ...while the file that WAS missing is seeded (control: 'present' is "
      "not returned for everything)",
      dict(_rows_present)["mesh_tree_to_name.json"], "seeded")

# --- 6d. a corrupted vendored file is REFUSED -------------------------------
# THE PLANTED FAILURE. A truncated lookup still parses as JSON and still loads;
# the only symptom is a Stage 4 filter that recognises fewer descriptors. The
# plant is applied to a COPY of the source directory, never to the repository.
_bad_src = os.path.join(_SCRATCH, "mesh_src_corrupt")
shutil.copytree(_SRC_DIR, _bad_src)
_victim = os.path.join(_bad_src, "mesh_c04_lookup.json")

# THE PLANT HAS TO STAY VALID JSON, and the first version of it did not.
# Cutting the text at the halfway character and appending "}" produced a file
# that json.loads REJECTS -- so the plant tested "a mangled file is refused",
# which any check would catch, instead of the case this guard exists for: a file
# that loads cleanly and is missing half its descriptors. Dropping keys from the
# parsed object is the realistic shape of a truncated or half-written lookup,
# and it is the one a `json.load` guard would wave straight through.
_full = json.load(open(_victim, encoding="utf-8"))
_keys = sorted(_full)
_halved = {k: _full[k] for k in _keys[:len(_keys) // 2]}
with open(_victim, "w", encoding="utf-8") as _fh:
    json.dump(_halved, _fh, indent=2)
_exc_bad, _msg_bad = raises(
    lambda: _pp.seed_mesh_core(source_dir=_bad_src,
                               dest_dir=os.path.join(_SCRATCH, "mesh_never")))
check("6d  a vendored file whose sha256 does not match is REFUSED",
      _exc_bad, "RuntimeError")
check("6d  ...and the message names both hashes",
      "expected sha256" in _msg_bad and "actual   sha256" in _msg_bad, True)
check("6d  ...and nothing was written to the destination",
      os.path.isdir(os.path.join(_SCRATCH, "mesh_never"))
      and os.listdir(os.path.join(_SCRATCH, "mesh_never")) == [], True)
# The truncated file is still valid JSON -- which is the point of the check.
try:
    _reloaded = json.loads(open(_victim, encoding="utf-8").read())
    _still_json = True
except ValueError:
    _reloaded = None
    _still_json = False
check("6d  ...and the corrupted file would still have LOADED (why a hash and "
      "not a json.load is the guard)", _still_json, True)
check("6d  ...while having lost half its descriptors (the damage is real, not "
      "cosmetic)",
      _still_json and 0 < len(_reloaded) < len(_full), True)

# --- 6e. an absent source is soft ------------------------------------------
_rows_none = _pp.seed_mesh_core(source_dir=os.path.join(_SCRATCH, "nope"),
                                dest_dir=os.path.join(_SCRATCH, "mesh_x"))
check("6e  an image without the vendored directory reports source-missing "
      "rather than refusing to boot",
      _rows_none, [("(docker/mesh-core)", "source-missing")])

# --- 6f. a malformed manifest raises ----------------------------------------
_bad_manifest_src = os.path.join(_SCRATCH, "mesh_src_badmanifest")
shutil.copytree(_SRC_DIR, _bad_manifest_src)
open(os.path.join(_bad_manifest_src, "PROVENANCE.json"), "w",
     encoding="utf-8").write('{"no files key": true}')
_exc_bm, _msg_bm = raises(
    lambda: _pp.seed_mesh_core(source_dir=_bad_manifest_src,
                               dest_dir=os.path.join(_SCRATCH, "mesh_y")))
check("6f  an unreadable manifest raises rather than seeding unverified files",
      _exc_bm, "RuntimeError")
check("6f  ...and says what the manifest must contain",
      "'files' object" in _msg_bm, True)

# --- 6g. the Dockerfile actually ships the directory ------------------------
#
# INSTRUCTIONS ONLY, NOT THE WHOLE TEXT, and that repair is the same one section
# 5e needed. The first version asked `"/app/data/mesh" not in _dockerfile` and
# came back False -- because the comment ABOVE the COPY explains that the files
# deliberately do not go there. A file that argues about its own instructions
# cannot be searched as a string.
_dockerfile = open(_repo("Dockerfile"), encoding="utf-8").read()


def _dockerfile_instructions(text):
    """The Dockerfile's instruction lines, comments and continuations joined."""
    out, pending = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1].rstrip() + " "
            continue
        out.append((pending + line).strip())
        pending = ""
    if pending:
        out.append(pending.strip())
    return out


_instructions = _dockerfile_instructions(_dockerfile)
_copies = [i for i in _instructions if i.upper().startswith(("COPY ", "ADD "))]

check("6g  the Dockerfile copies docker/mesh-core into the image",
      any("docker/mesh-core/" in c for c in _copies), True)
check("6g  ...to the image-only path prepare_paths.py reads",
      any("docker/mesh-core/ /usr/local/lib/oncotriage-docker/mesh-core/" in c
          for c in _copies), True)
check("6g  ...and NO instruction copies anything into /app/data, which would "
      "restore the volume-init race pass 20g fixed",
      [c for c in _copies if "/app/data" in c], [])
check("6g  ...control: the instruction scan is not empty (it found the other "
      "COPYs too)", len(_copies) >= 4, True)
check("6g  ...and the seeding source is the same directory the Dockerfile "
      "writes to",
      _pp._MESH_CORE_DIR, "/usr/local/lib/oncotriage-docker/mesh-core")


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 7: one version string, and a build that cannot ship a wrong one
# ===========================================================================

print("\n" + "=" * 74)
print("SECTION 7: the image version label is derived")
print("=" * 74)

_app_version_path = _repo("docker", "app_version.py")


def _run_app_version(args):
    proc = subprocess.run([sys.executable, _app_version_path] + args,
                          cwd=_CODE_DIR, capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


_rc, _out, _err = _run_app_version([])
check("7a  app_version.py prints the package version", _out,
      _oncotriage_pkg.__version__)
check("7a  ...and exits 0", _rc, 0)
check("7a  ...and it is non-degenerate (not empty, not 'unset')",
      bool(_out) and _out != "unset", True)

_rc_ok, _, _ = _run_app_version(["--check", _oncotriage_pkg.__version__])
check("7b  --check passes on the right version", _rc_ok, 0)

_rc_bad, _, _err_bad = _run_app_version(["--check", "1.0.0"])
check("7c  --check FAILS on the stale literal this pass removed", _rc_bad, 1)
check("7c  ...and names both values", "1.0.0" in _err_bad
      and _oncotriage_pkg.__version__ in _err_bad, True)

_rc_unset, _, _err_unset = _run_app_version(["--check", "unset"])
check("7d  --check FAILS on the compose sentinel, so a bare "
      "`docker compose build` cannot mislabel the image", _rc_unset, 1)
check("7d  ...and points at `make build`", "make build" in _err_unset, True)

# The Dockerfile must actually use it. Three halves: the label is an ARG
# reference, no LABEL carries a literal version, and the guard runs.
#
# READ OFF THE INSTRUCTIONS, for the third time in this file and for the third
# time because the first version did not. `'version="1.0.0"' in _dockerfile` was
# False-expected and came back True: the header comment RECORDS that the literal
# was removed, quoting it. Three separate assertions in this file were satisfied
# or defeated by prose about themselves.
_labels = [i for i in _instructions if i.upper().startswith("LABEL ")]
_label_text = " ".join(_labels)

check("7e  a LABEL exists to check (non-degeneracy)", len(_labels) >= 1, True)
check("7e  the Dockerfile's version label is an ARG reference, not a literal",
      'version="${APP_VERSION}"' in _label_text, True)
check("7e  ...and no LABEL carries a hardcoded version anywhere",
      any(_lit in _label_text for _lit in ('version="1.0.0"', 'version="2.0.0"')),
      False)
check("7e  ...and the build runs the guard",
      any("app_version.py --check" in i for i in _instructions), True)
check("7f  compose supplies the ARG, defaulting to the rejected sentinel",
      "APP_VERSION: ${ONCOTRIAGE_APP_VERSION:-unset}" in _compose_text, True)

# THE COUNT. Every version literal in the tree that is not the one declaration.
# Scoped to the files that can declare one; the Dockerfile's `LABEL version` is
# covered above, and this catches a re-typed number anywhere in the package.
_version_literals = []
for _root, _dirs, _files in os.walk(_repo("oncotriage")):
    _dirs[:] = [d for d in _dirs if d != "__pycache__"]
    for _f in _files:
        if not _f.endswith(".py"):
            continue
        _p = os.path.join(_root, _f)
        _tree = ast.parse(open(_p, encoding="utf-8").read())
        for _n in ast.walk(_tree):
            # A docstring or comment mentioning 2.0.0 is prose; an assignment or
            # a call argument carrying it is a second declaration.
            if isinstance(_n, ast.Assign):
                for _t in _n.targets:
                    if isinstance(_t, ast.Name) and _t.id == "__version__":
                        continue
            if (isinstance(_n, ast.keyword) and _n.arg == "version"
                    and isinstance(_n.value, ast.Constant)
                    and isinstance(_n.value.value, str)):
                _version_literals.append((_p, _n.value.value))
check("7g  no package module passes a version= STRING LITERAL anywhere "
      "(every one derives it)", _version_literals, [])


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 8: the Stage 2 gate is wired in, and wired in FIRST
# ===========================================================================
#
# Sections 2 and 3 test the gate as a function. This one asserts that
# node_hybrid_retrieval actually calls it, and calls it BEFORE the channel
# machinery -- which is the property that keeps its raise out of the
# `except Exception` that records a channel as failed. A gate that ran after the
# channels would be swallowed into "one channel was unavailable", which is the
# report that hides this exact fault.

print("\n" + "=" * 74)
print("SECTION 8: the gate is called, and called before the channels")
print("=" * 74)

_retrieval_path = _repo("oncotriage", "agent", "retrieval.py")
_retrieval_src = open(_retrieval_path, encoding="utf-8").read()
_retrieval_tree = ast.parse(_retrieval_src)

_node = next((n for n in ast.walk(_retrieval_tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "node_hybrid_retrieval"), None)

if _node is None:
    fail("8  node_hybrid_retrieval is in oncotriage/agent/retrieval.py",
         "not found; this section is not testing what it claims to")
else:
    def _gate_and_try_lines(fn_node):
        """(first gate-call line, first Try line) inside `fn_node`."""
        gate = [n.lineno for n in ast.walk(fn_node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "require_populated_index"]
        tries = [n.lineno for n in ast.walk(fn_node) if isinstance(n, ast.Try)]
        return (min(gate) if gate else None, min(tries) if tries else None)

    _gate_line, _try_line = _gate_and_try_lines(_node)

    check("8a  node_hybrid_retrieval calls require_populated_index",
          _gate_line is not None, True)
    check("8b  ...and there IS channel exception handling to be ahead of "
          "(non-degeneracy)", _try_line is not None, True)
    if _gate_line and _try_line:
        check("8b  ...and the gate runs BEFORE it", _gate_line < _try_line, True)

    # THE PLANTED FAILURE, in a COPY. The shipped file is not touched; it is
    # hashed before and after to say so.
    _before_hash = hashlib.sha256(open(_retrieval_path, "rb").read()).hexdigest()

    _copy_tree = ast.parse(_retrieval_src)
    _copy_node = next(n for n in ast.walk(_copy_tree)
                      if isinstance(n, ast.FunctionDef)
                      and n.name == "node_hybrid_retrieval")
    _copy_node.body = [
        s for s in _copy_node.body
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Call)
                and isinstance(s.value.func, ast.Name)
                and s.value.func.id == "require_populated_index")
    ]
    _stripped_gate, _ = _gate_and_try_lines(_copy_node)
    check("8c  CONTROL: with the call stripped from an AST copy, 8a fails",
          _stripped_gate is None, True)

    _after_hash = hashlib.sha256(open(_retrieval_path, "rb").read()).hexdigest()
    check("8c  ...and the shipped file was never touched",
          _after_hash, _before_hash)

    # And a control for the ORDERING check, so 8b is not satisfied by any file
    # that happens to have a gate somewhere.
    #
    # THE LINE NUMBERS HAVE TO MOVE WITH THE STATEMENT. The first version of
    # this control appended `copy.deepcopy(_gate_stmt)` to the stripped body and
    # expected the ordering check to fail -- it did not, because a deepcopy
    # keeps the ORIGINAL lineno, so `min(gate)` was still the early one and the
    # control reported that a late gate passes the "gate is early" check. The
    # control was measuring nothing while looking like it worked. Every AST node
    # in the moved statement is pushed past the end of the function with
    # `ast.increment_lineno`, which is what actually relocates it.
    # `next(..., None)` AND A GUARD, not a bare `next`.
    #
    # THIS IS THE THIRD TIME THIS PROJECT HAS SHIPPED THE SAME DEFECT and the
    # first version of this file had it too, found by a revert harness rather
    # than by reading. A bare `next(...)` raises StopIteration when the gate is
    # absent -- which is EXACTLY the edit this section exists to catch -- so
    # removing the gate from retrieval.py made the run abort at this line and
    # print one traceback where it owed a summary and 114 other results. Same
    # shape as tests/test_storage_query_layer.py's `QUERIES_BY_KEY["k"]` and
    # tests/test_dashboard_reproducibility_tab.py's `_refs[2]`. A check that
    # takes the run down with it reports a crash, not a finding.
    _gate_stmt = next((s for s in _node.body
                       if isinstance(s, ast.Expr)
                       and isinstance(s.value, ast.Call)
                       and isinstance(s.value.func, ast.Name)
                       and s.value.func.id == "require_populated_index"), None)

    if _gate_stmt is None:
        fail("8d  CONTROL: a gate placed AFTER the channel handling fails the "
             "ordering check",
             "there is no gate statement in node_hybrid_retrieval to relocate, "
             "so this control could not run. 8a above is the finding.")
    else:
        _reordered = copy.deepcopy(_copy_node)
        _late_stmt = copy.deepcopy(_gate_stmt)
        ast.increment_lineno(_late_stmt, 10_000)
        _reordered.body = _reordered.body + [_late_stmt]
        _late_gate, _late_try = _gate_and_try_lines(_reordered)
        check("8d  CONTROL: the relocated gate really moved (non-degeneracy for "
              "the control itself)",
              _late_gate is not None and _late_gate > (_try_line or 0), True)
        check("8d  CONTROL: a gate placed AFTER the channel handling fails the "
              "ordering check",
              _late_gate is not None and _late_try is not None
              and _late_gate < _late_try, False)


#------------------------------------------------------------------------------


# ===========================================================================
# SUMMARY
# ===========================================================================

shutil.rmtree(_SCRATCH, ignore_errors=True)

print("\n" + "=" * 74)
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
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
