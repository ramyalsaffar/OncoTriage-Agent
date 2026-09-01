# The Spend-Coverage Test
#########################

"""Every billed path in this project runs under spend enforcement, or is
argued.

WHAT THIS FILE IS FOR
---------------------
``tests/test_spend_gate.py`` proves the Stage 5 gate works. It cannot see that
Stage 5 was, until the spend-coverage pass, the ONLY thing the cap saw -- while
Stage 2's dense query embedding, the independent rater and the ragas harness
each spent real money the budget could not measure, and the two serving
surfaces charged a ledger nothing reset. A budget that covers one door of a
building with four is not a budget, and nothing in the suite could say so.

So this file holds the questions that are about COVERAGE rather than about the
gate:

  1. THE MAP. Every site in the repository that touches a billed provider
     endpoint is DERIVED from source and required to match
     ``spend.BILLED_SITES`` exactly, in both directions -- with an ungated
     billing site PLANTED into a copy of the tree and required to be caught,
     and the clean control beside it.
  2. THE POLICY. A campaign cap bounds a monotone total; a server is bounded by
     a rolling window. The wrong-refusal defect the shipped gate had on a
     long-lived process is DRIVEN, before and after.
  3. THE SURFACES. What an operator and a client actually see when a serving
     surface declines: a 503 with a computed ``Retry-After`` from the API, a
     payload with no ``result`` key from the MCP server, and a ``/health`` that
     REPORTS the budget and deliberately does not decide ``healthy``.
  4. THE JUDGE. The rater's measured cost reaches the ledger, its disjoint
     usage counts are priced without being summed, and its per-chunk gate stops
     a session that has spent its budget.
  5. THE STUDY. The ablation study driven to its cap: it stops cleanly, records
     WHY in ``ablation_runs.stop_reason``, and resumes under the remainder.
  6. THE PROGRAM. One campaign-plus-judge sequence under one cap.

WHAT IT COSTS TO RUN: NOTHING. No network, no keys, NO SPEND -- every provider
client is a stand-in, the ablation study's ``match_patient_ablation`` is a
stand-in that charges the ledger and issues no request, and the graph is never
invoked. NO MODEL LOAD (``ONCOTRIAGE_DEFER_LOCAL_MODELS`` is set above the
imports and section 11 asserts ``torch`` and ``transformers`` never entered
``sys.modules``). No live Qdrant, no corpus, no git history, no live server, no
Docker daemon.

NOT IN THE COLLISION MATRIX: every database, checkpoint and plant lives inside
a ``tempfile.mkdtemp`` this file removes and asserts gone, ``paths._RESOLVED``
is seeded so nothing can resolve to the production tree, and the repository
files it reads are sha256-compared at the end. It EXECS NOTHING and loads no
module by location -- the one plant is a COPY of a package module written into
the temp tree and PARSED, never imported.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import types

# ABOVE THE IMPORTS, for oncotriage/fixtures/replay.py's reason.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_CODE = os.path.dirname(_HERE)
if _CODE not in sys.path:
    sys.path.insert(0, _CODE)

import ast                                                      # noqa: E402
import contextlib                                               # noqa: E402
import time                                                     # noqa: E402

from oncotriage import config                                   # noqa: E402
from oncotriage import paths as _paths                          # noqa: E402
from oncotriage import spend                                    # noqa: E402
from oncotriage.ablation import study as _study                 # noqa: E402
from oncotriage.evaluation import rater as _rater               # noqa: E402
from oncotriage.evaluation import ragas_harness as _ragas       # noqa: E402
from oncotriage.utils import get_model_cost                     # noqa: E402

_READ_FILES = [os.path.abspath(m.__file__) for m in
               (spend, _study, _rater, _ragas, config)]
_BASELINE_HASHES = {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
                    for p in _READ_FILES}

_START_CONFIG = (config.SPEND_CAP_USD, config.SPEND_CAP_ENFORCED,
                 config.SERVING_SPEND_CAP_USD,
                 config.SERVING_SPEND_WINDOW_SECONDS)

_TMP = tempfile.mkdtemp(prefix="oncotriage-spend-coverage-")
_SAVED_RESOLVED = dict(_paths._RESOLVED)
_paths._RESOLVED["inferences_path"] = os.path.join(_TMP, "never-written.db")
_paths._RESOLVED["checkpoint_path"] = _TMP


_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, actual, expected):
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append((label, expected, actual))
        print(f"  FAIL  {label}\n          expected: {expected!r}"
              f"\n          actual:   {actual!r}")


def check_true(label, actual):
    check(label, bool(actual), True)


def section(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


class _Absent:
    """A value that equals nothing a check expects, and NEVER raises.

    THE ABORT CLASS THIS PROJECT HAS SHIPPED SIXTEEN TIMES. A bare
    ``result["key"]`` inside a ``check(...)`` argument list raises while the
    argument is being EVALUATED -- on precisely the defect the check exists to
    catch -- so the run prints one traceback where it owed a summary and a
    hundred results. Every raise-capable read in this file goes through
    ``at()``, ``drive()`` or ``raised()``.
    """

    def __init__(self, why):
        self.why = why

    def __repr__(self):
        return f"<absent: {self.why}>"

    def __eq__(self, other):
        return isinstance(other, _Absent) and other.why == self.why

    def __bool__(self):
        return False

    def __len__(self):
        return 0


def at(mapping, key):
    try:
        return mapping[key]
    except Exception as exc:                                    # noqa: BLE001
        return _Absent(f"{key}: {type(exc).__name__}")


def drive(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except BaseException as exc:                                # noqa: BLE001
        return _Absent(f"{type(exc).__name__}: {exc}")


def raised(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return None
    except BaseException as exc:                                # noqa: BLE001
        return type(exc).__name__


@contextlib.contextmanager
def clean_ledger(*, policy=spend.SPEND_POLICY_CAMPAIGN, cap=None,
                 serving_cap=None, window=None, enforced=True):
    """A ledger, a policy and the two caps, restored on the way out.

    RESTORED FROM VALUES CAPTURED HERE rather than from literals, so a
    legitimate change to a shipped default costs this file nothing.
    """
    _cfg = (config.SPEND_CAP_USD, config.SERVING_SPEND_CAP_USD,
            config.SERVING_SPEND_WINDOW_SECONDS, config.SPEND_CAP_ENFORCED)
    _prev = spend.policy()
    spend.SPEND_LEDGER.reset()
    spend.SPEND_STOP.reset()
    spend.SPEND_GATE_SKIPS.clear()
    spend.SPEND_LEDGER_FAULTS.clear()
    if cap is not None:
        config.SPEND_CAP_USD = cap
    if serving_cap is not None:
        config.SERVING_SPEND_CAP_USD = serving_cap
    if window is not None:
        config.SERVING_SPEND_WINDOW_SECONDS = window
    config.SPEND_CAP_ENFORCED = enforced
    spend.set_policy(policy, "test")
    try:
        yield spend.SPEND_LEDGER
    finally:
        spend.set_policy(_prev, "restore")
        spend.SPEND_LEDGER.reset()
        spend.SPEND_STOP.reset()
        spend.SPEND_GATE_SKIPS.clear()
        spend.SPEND_LEDGER_FAULTS.clear()
        (config.SPEND_CAP_USD, config.SERVING_SPEND_CAP_USD,
         config.SERVING_SPEND_WINDOW_SECONDS,
         config.SPEND_CAP_ENFORCED) = _cfg


# ===========================================================================
# SECTION 1 -- THE MAP: EVERY BILLED SITE IS ACCOUNTED FOR
# ===========================================================================

section("SECTION 1 -- every billed call site is derived, and accounted for")

# *** THE SITE LIST IS DERIVED FROM SOURCE, NOT LISTED HERE. ***
#
# THE SCAN IS ON ATTRIBUTE ACCESS AND NOT ON CALLS, and that is the whole
# reason it can see `oncotriage/evaluation/ragas_harness.py`: that module
# captures `real_create = client.messages.create` and calls it later through
# the reference, so a call-shaped scan reports a file that spends real money on
# two vendors as touching no billed endpoint at all. That is not hypothetical
# -- it is what the first version of this derivation reported. You cannot bill
# without naming one of these attributes; you can bill without a call node a
# scanner recognises.
_BILLED_SUFFIXES = ("embeddings.create", "chat.completions.create",
                    "responses.create", "messages.batches.create",
                    "messages.create", "messages.count_tokens", ".converse")

# DIRECTORIES THAT ARE NOT PRODUCTION CODE. `tests/` is excluded because a test
# stub NAMES these attributes by definition -- every stand-in in this suite
# defines a `create` -- so including it would make the derivation report the
# suite instead of the pipeline.
_SKIP_DIRS = {".git", "__pycache__", "tests", "build", ".github", "docker",
              "09- Testing", ".venv", "venv", "oncotriage.egg-info"}


def billed_sites_in(path):
    """Every `qualname` in one file that touches a billed endpoint attribute.

    Walked at EVERY nesting depth: two of the sites in this repository are
    closures and one is nested two deep (`get_embeddings_batch::_call`), so a
    top-level walk would report the indexer as touching nothing.
    """
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:                                           # noqa: BLE001
        return []
    found = set()

    def walk(node, stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                walk(child, stack + [child.name])
                continue
            if isinstance(child, ast.Attribute):
                try:
                    rendered = ast.unparse(child)
                except Exception:                               # noqa: BLE001
                    rendered = ""
                if any(rendered.endswith(s) for s in _BILLED_SUFFIXES):
                    found.add("::".join(stack) or "<module>")
            walk(child, stack)

    walk(tree, [])
    return sorted(found)


def derive_billed_sites(root):
    """`{ "path::qualname" }` over a whole tree."""
    out = set()
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            for qual in billed_sites_in(full):
                out.add(f"{rel}::{qual}")
    return out


_DERIVED = derive_billed_sites(_CODE)
_DECLARED = set(spend.BILLED_SITES)

check("1a  *** every billed call site in the repository is declared in "
      "spend.BILLED_SITES -- a new one fails here rather than arriving as an "
      "ungated path ***",
      sorted(_DERIVED - _DECLARED), [])
check("1b  ...and every declared site still exists, so the table cannot rot "
      "into a permission slip for code that has moved",
      sorted(_DECLARED - _DERIVED), [])
check("1c  non-degeneracy: the derivation found sites at all, at more than one "
      "nesting depth",
      (len(_DERIVED) > 8,
       any(s.count("::") >= 2 for s in _DERIVED)), (True, True))

_DISPOSITIONS = {d for d, _g, _w in spend.BILLED_SITES.values()}
check("1d  every disposition is a member of the closed vocabulary",
      sorted(_DISPOSITIONS - set(spend.BILLED_SITE_DISPOSITIONS)), [])
check("1e  ...and all three members are actually used, so none is a "
      "declaration nothing reads",
      sorted(_DISPOSITIONS), sorted(spend.BILLED_SITE_DISPOSITIONS))

# EVERY ENTRY CARRIES AN ARGUMENT. A disposition with an empty `why` is the
# shape this table exists to prevent: a site marked exempt by somebody who did
# not write down why.
_ARGUED = {s: bool(w and len(w.strip()) >= 40)
           for s, (_d, _g, w) in spend.BILLED_SITES.items()}
check("1f  *** every site carries a written argument, not just a disposition "
      "-- an exemption without one is the next hole waiting to be found ***",
      sorted(s for s, ok in _ARGUED.items() if not ok), [])

# A `gated_upstream` ENTRY MUST NAME A GATE THAT EXISTS.
_UPSTREAM = {s: g for s, (d, g, _w) in spend.BILLED_SITES.items()
             if d == spend.DISPOSITION_GATED_UPSTREAM}


def site_exists(site):
    path, _sep, qual = site.partition("::")
    full = os.path.join(_CODE, path)
    return os.path.isfile(full) and qual in billed_or_defined(full)


def defined_qualnames(path):
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:                                           # noqa: BLE001
        return set()
    out = set()

    def walk(node, stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                out.add("::".join(stack + [child.name]))
                walk(child, stack + [child.name])
            else:
                walk(child, stack)

    walk(tree, [])
    return out


def billed_or_defined(full):
    return defined_qualnames(full)


check("1g  every `gated_upstream` entry names a gate that exists in this tree",
      sorted(s for s, g in _UPSTREAM.items() if not site_exists(g)), [])
check("1g-i non-degeneracy: there ARE gated_upstream entries, so 1g is not "
      "vacuous",
      len(_UPSTREAM) > 0, True)
check("1g-ii ...and site_exists() can say no, so 1g can fail",
      site_exists("oncotriage/spend.py::a_function_that_does_not_exist"), False)

# A `gated_here` ENTRY MUST REALLY CALL THE GATE, IN ITS OWN SUBTREE.
_HERE = [s for s, (d, _g, _w) in spend.BILLED_SITES.items()
         if d == spend.DISPOSITION_GATED_HERE]


def calls_require_budget(site):
    """Does this site's function (or a closure inside it) call the gate?

    THE WHOLE SUBTREE, which is what makes `ragas_harness::build_judge` answer
    True: the attribute that marks it as billed sits in the enclosing function
    and the gate sits in the `recording_create` closure it installs.
    """
    path, _sep, qual = site.partition("::")
    try:
        tree = ast.parse(open(os.path.join(_CODE, path), encoding="utf-8")
                         .read())
    except Exception:                                           # noqa: BLE001
        return False
    target = [None]

    def find(node, stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                if "::".join(stack + [child.name]) == qual:
                    target[0] = child
                find(child, stack + [child.name])
            else:
                find(child, stack)

    find(tree, [])
    if target[0] is None:
        return False
    for node in ast.walk(target[0]):
        if isinstance(node, ast.Call):
            try:
                rendered = ast.unparse(node.func)
            except Exception:                                   # noqa: BLE001
                continue
            if rendered.endswith("require_budget"):
                return True
    return False


check("1h  *** every `gated_here` site really calls spend.require_budget in "
      "its own body or in a closure inside it ***",
      sorted(s for s in _HERE if not calls_require_budget(s)), [])
check("1h-i non-degeneracy: there are gated_here sites, and the scan can say "
      "no",
      (len(_HERE) >= 4,
       calls_require_budget(
           "oncotriage/retrieval/indexer.py::get_embeddings_batch::_call")),
      (True, False))

# THE FOUR BILLED PATHS THE CAP NOW COVERS ARE NAMED, so a path silently
# dropped from the gate fails here as well as at 1h.
check("1i  the four non-Stage-5 billed paths are all gated_here",
      sorted(_HERE),
      sorted(["oncotriage/agent/models.py::get_embedding",
              "oncotriage/evaluation/ragas_harness.py::build_embeddings",
              "oncotriage/evaluation/ragas_harness.py::build_judge",
              "oncotriage/evaluation/rater.py::submit_batches"]))

check("1j  spend.SPEND_SOURCES has one member per gated path plus Stage 5",
      len(spend.SPEND_SOURCES), 5)
check("1j-i ...and the two key spaces are disjoint, so a SPEND_GATE_SKIPS key "
      "can never be read as either a Stage 5 phase or a source",
      sorted(set(spend.SPEND_SOURCES) & set(spend.SPEND_SKIP_KEY_PREFIXES)),
      [])


# ── THE PLANT ───────────────────────────────────────────────────────────────
#
# A COPY OF THE TREE, NOT AN EDIT TO IT. The plant is written into the temp
# directory and PARSED; nothing is imported from it, so section 1c of
# tests/test_package_invariants.py has nothing to see and this file needs no
# _EXEC_ALLOWLIST entry.

_PLANT_ROOT = os.path.join(_TMP, "planted")
shutil.copytree(os.path.join(_CODE, "oncotriage"),
                os.path.join(_PLANT_ROOT, "oncotriage"),
                ignore=shutil.ignore_patterns("__pycache__"))

# THE CLEAN CONTROL, FIRST. Without it a derivation that always reported
# "nothing new" would report the plant as caught while measuring nothing.
check("1k  CLEAN CONTROL: the copied tree derives to the same site set as the "
      "real one, so the plant below is measured against a scan that agrees "
      "with the original",
      sorted(s for s in derive_billed_sites(_PLANT_ROOT)),
      sorted(s for s in _DERIVED if s.startswith("oncotriage/")))

_PLANT_FILE = os.path.join(_PLANT_ROOT, "oncotriage", "monitoring", "drift.py")
with open(_PLANT_FILE, "a", encoding="utf-8") as _fh:
    _fh.write("\n\ndef _planted_billing_path(client, text):\n"
              "    return client.embeddings.create(model='x', input=text)\n")

_PLANTED = derive_billed_sites(_PLANT_ROOT)
check("1l  *** PLANT: an ungated billing site added anywhere in the package is "
      "reported as undeclared ***",
      sorted(_PLANTED - _DECLARED),
      ["oncotriage/monitoring/drift.py::_planted_billing_path"])

# THE SECOND PLANT: a captured reference rather than a call, which is the shape
# the first version of this derivation could not see.
with open(_PLANT_FILE, "a", encoding="utf-8") as _fh:
    _fh.write("\n\ndef _planted_captured_reference(client):\n"
              "    real = client.messages.create\n"
              "    return real\n")
check("1m  *** PLANT: a billed endpoint CAPTURED as a reference and called "
      "later -- ragas' own shape -- is reported too ***",
      "oncotriage/monitoring/drift.py::_planted_captured_reference"
      in derive_billed_sites(_PLANT_ROOT), True)

# THE THIRD PLANT: an entry whose argument has been removed.
_STRIPPED = dict(spend.BILLED_SITES)
_victim = "oncotriage/retrieval/indexer.py::get_embeddings_batch::_call"
_STRIPPED[_victim] = (spend.DISPOSITION_EXEMPT, None, "")
check("1n  *** PLANT: an exemption whose ARGUMENT has been removed fails the "
      "argued check ***",
      sorted(s for s, (_d, _g, w) in _STRIPPED.items()
             if not (w and len(w.strip()) >= 40)), [_victim])
check("1n-i ...and the shipped table passes the identical check, so 1n is not "
      "reporting a rule nothing satisfies",
      sorted(s for s, (_d, _g, w) in spend.BILLED_SITES.items()
             if not (w and len(w.strip()) >= 40)), [])


# ===========================================================================
# SECTION 2 -- THE POLICY, AND THE DEFECT IT REMOVES
# ===========================================================================

section("SECTION 2 -- a server is not bounded the way a campaign is")

check("2a  the policy vocabulary is closed and has exactly two members",
      spend.SPEND_POLICIES,
      (spend.SPEND_POLICY_CAMPAIGN, spend.SPEND_POLICY_WINDOW))
check("2b  the DEFAULT is the campaign policy, so a process that installs "
      "nothing gets the conservative shape",
      spend.policy(), spend.SPEND_POLICY_CAMPAIGN)
check("2c  an unrecognised policy RAISES rather than falling back -- the two "
      "branches bound different quantities",
      raised(spend.set_policy, "hourly"), "SpendCapConfigurationError")

with clean_ledger(policy=spend.SPEND_POLICY_CAMPAIGN, cap=10.0):
    check("2d  under `campaign`, the cap is compared against the MONOTONE "
          "total", spend.active_cap(), 10.0)
    spend.SPEND_LEDGER.charge_usd(4.0, spend.SPEND_SOURCE_STAGE5)
    check("2d-i ...which the ledger reports", spend.active_spend(), 4.0)

with clean_ledger(policy=spend.SPEND_POLICY_WINDOW, serving_cap=5.0,
                  window=3600.0):
    check("2e  under `serving_window`, the cap is the SERVING one",
          spend.active_cap(), 5.0)
    spend.SPEND_LEDGER.charge_usd(4.0, spend.SPEND_SOURCE_STAGE5)
    check("2e-i ...and the quantity is the window, not the total",
          (spend.active_spend(), spend.SPEND_LEDGER.total), (4.0, 4.0))

# *** THE DEFECT, DRIVEN BOTH WAYS ON THE SAME LEDGER. ***
#
# THE POINT IS NOT THAT THE WINDOW IS SMALLER. It is that the window can go
# DOWN. A long-lived process under the campaign policy declines its FIRST
# request past the cap and every request afterwards for its whole life; under
# the window policy the same process serves again as soon as the spend ages
# out, with no restart and no operator. That is the whole of Hole 3 and it is
# measured rather than argued.
_HISTORY = []
with clean_ledger(policy=spend.SPEND_POLICY_CAMPAIGN, cap=3.0, window=0.30):
    for _ in range(4):
        spend.SPEND_LEDGER.charge_usd(1.0, spend.SPEND_SOURCE_STAGE5)
    _HISTORY.append(("campaign, over", spend.cap_exceeded()))
    time.sleep(0.45)
    _HISTORY.append(("campaign, after the window would have rolled",
                     spend.cap_exceeded()))

with clean_ledger(policy=spend.SPEND_POLICY_WINDOW, serving_cap=3.0,
                  window=0.30):
    for _ in range(4):
        spend.SPEND_LEDGER.charge_usd(1.0, spend.SPEND_SOURCE_STAGE5)
    _HISTORY.append(("window, over", spend.cap_exceeded()))
    time.sleep(0.45)
    _HISTORY.append(("window, after it rolled", spend.cap_exceeded()))

check("2f  *** the long-lived-process defect: under the CAMPAIGN policy a "
      "process past its cap declines for ever, and under the WINDOW policy it "
      "recovers on its own ***",
      _HISTORY,
      [("campaign, over", True),
       ("campaign, after the window would have rolled", True),
       ("window, over", True),
       ("window, after it rolled", False)])

# THE LATCH IS THE OTHER HALF OF THE SAME DEFECT: a latch under the window
# policy would make the recovery above unreachable, because `SPEND_STOP` never
# un-trips.
check("2g  the latch is derived from the policy: campaign latches",
      spend.set_policy(spend.SPEND_POLICY_CAMPAIGN, "test")
      and spend.latch_on_limit(), True)
check("2h  *** ...and serving_window does NOT -- a latch there would make the "
      "self-healing above unreachable ***",
      spend.set_policy(spend.SPEND_POLICY_WINDOW, "test")
      and spend.latch_on_limit(), False)
spend.reset_policy()

with clean_ledger(policy=spend.SPEND_POLICY_WINDOW, serving_cap=1.0,
                  window=3600.0):
    spend.SPEND_LEDGER.charge_usd(2.0, spend.SPEND_SOURCE_STAGE5)
    check("2i  require_budget raises under the window policy...",
          raised(spend.require_budget, spend.SPEND_SOURCE_STAGE5, "probe"),
          "SpendLimitReached")
    check("2i-i ...and does NOT latch the run stop",
          spend.SPEND_STOP.requested, False)
    check("2i-ii ...while still counting the declined request",
          at(dict(spend.SPEND_GATE_SKIPS),
             f"{spend.SPEND_SOURCE_STAGE5}:{spend.SPEND_LIMIT_CAP}"), 1)

with clean_ledger(policy=spend.SPEND_POLICY_CAMPAIGN, cap=1.0):
    spend.SPEND_LEDGER.charge_usd(2.0, spend.SPEND_SOURCE_STAGE5)
    check("2j  under the campaign policy the same call DOES latch",
          (raised(spend.require_budget, spend.SPEND_SOURCE_STAGE5, "probe"),
           spend.SPEND_STOP.requested), ("SpendLimitReached", True))

# THE EXPLICIT OVERRIDE EXISTS AND WORKS, so a test can drive one half against
# the other policy.
with clean_ledger(policy=spend.SPEND_POLICY_CAMPAIGN, cap=1.0):
    spend.SPEND_LEDGER.charge_usd(2.0, spend.SPEND_SOURCE_STAGE5)
    drive(spend.require_budget, spend.SPEND_SOURCE_STAGE5, "probe", latch=False)
    check("2k  an explicit latch=False overrides the policy's derivation",
          spend.SPEND_STOP.requested, False)


# ===========================================================================
# SECTION 3 -- THE ROLLING WINDOW'S ARITHMETIC
# ===========================================================================

section("SECTION 3 -- the window, the per-source ledger, and the retry hint")

with clean_ledger(policy=spend.SPEND_POLICY_WINDOW, serving_cap=100.0,
                  window=3600.0) as ledger:
    ledger.charge_usd(1.0, spend.SPEND_SOURCE_STAGE5)
    ledger.charge_usd(2.0, spend.SPEND_SOURCE_RATER)
    ledger.charge_usd(0.5, spend.SPEND_SOURCE_EMBEDDING)
    check("3a  the ledger totals every source",
          round(ledger.total, 6), 3.5)
    check("3b  ...and reports the breakdown a report is read off",
          {k: round(v, 6) for k, v in ledger.by_source().items()},
          {spend.SPEND_SOURCE_STAGE5: 1.0,
           spend.SPEND_SOURCE_RATER: 2.0,
           spend.SPEND_SOURCE_EMBEDDING: 0.5})
    check("3b-i ...with a call count per source, which is the denominator "
          "SPEND_LEDGER_FAULTS is read against",
          ledger.calls_by_source(),
          {spend.SPEND_SOURCE_STAGE5: 1, spend.SPEND_SOURCE_RATER: 1,
           spend.SPEND_SOURCE_EMBEDDING: 1})
    check("3c  by_source() hands back a COPY, so a caller iterating it while "
          "workers charge cannot raise",
          ledger.by_source() is ledger.by_source(), False)

# A BAD AMOUNT IS COUNTED AND DROPPED, NEVER CHARGED -- the one direction a
# spend gate must not fail in silently is enforcing against a number nobody
# measured.
with clean_ledger() as ledger:
    for _bad in ("x", None, True, float("nan"), float("inf"), -1.0):
        ledger.charge_usd(_bad, spend.SPEND_SOURCE_RATER)
    check("3d  six malformed amounts charge nothing", ledger.total, 0.0)
    check("3d-i ...and every one is counted as a fault, so the report says "
          "the total is lower than the truth",
          sum(spend.SPEND_LEDGER_FAULTS.values()), 6)
    check("3d-ii ...NaN in particular, which would otherwise poison the cap "
          "comparison itself: `nan >= cap` is False, a gate silently off",
          any(k.startswith("bad_amount:nan") for k in spend.SPEND_LEDGER_FAULTS),
          True)

# THE WINDOW IS PRUNED ON READ, so an idle server can serve again without a
# request having to arrive to trigger the pruning.
with clean_ledger(policy=spend.SPEND_POLICY_WINDOW, serving_cap=100.0,
                  window=0.25) as ledger:
    ledger.charge_usd(5.0, spend.SPEND_SOURCE_STAGE5)
    check("3e  the charge is in the window", round(ledger.window_spend(), 6), 5.0)
    time.sleep(0.35)
    check("3f  ...and out of it once it ages, with NOTHING having been charged "
          "in between -- the prune happens on the READ",
          round(ledger.window_spend(), 6), 0.0)
    check("3f-i ...while the process total is untouched, because the two "
          "answer different questions", round(ledger.total, 6), 5.0)

# THE RETRY HINT IS DERIVED FROM THE EVENTS, not from the window width.
with clean_ledger(policy=spend.SPEND_POLICY_WINDOW, serving_cap=2.0,
                  window=600.0) as ledger:
    check("3g  under budget, there is nothing to wait for",
          spend.seconds_until_under_cap(), None)
    ledger.charge_usd(3.0, spend.SPEND_SOURCE_STAGE5)
    _wait = spend.seconds_until_under_cap()
    check("3h  *** over budget, the wait is derived from when the offending "
          "charge ages out -- not the window width, which would be wrong by "
          "nearly the whole window ***",
          (_wait is not None, 595.0 < (_wait or 0) <= 602.0), (True, True))

with clean_ledger(policy=spend.SPEND_POLICY_CAMPAIGN, cap=1.0) as ledger:
    ledger.charge_usd(3.0, spend.SPEND_SOURCE_STAGE5)
    check("3i  a campaign total never falls, so there is no wait to report and "
          "None is the honest answer",
          spend.seconds_until_under_cap(), None)


# ===========================================================================
# SECTION 4 -- WHAT A CLIENT SEES WHEN A SERVING SURFACE DECLINES
# ===========================================================================

section("SECTION 4 -- the API and the MCP server decline in their own shapes")

from oncotriage.api import server as _api                       # noqa: E402
from oncotriage.mcp import server as _mcp                       # noqa: E402

check("4a  the API installs the WINDOW policy in its lifespan, not the "
      "campaign cap",
      any("SPEND_POLICY_WINDOW" in ast.unparse(n)
          for n in ast.walk(ast.parse(open(os.path.abspath(_api.__file__),
                                           encoding="utf-8").read()))
          if isinstance(n, ast.Call)
          and ast.unparse(n.func).endswith("set_policy")), True)
check("4b  the MCP server does too",
      any("SPEND_POLICY_WINDOW" in ast.unparse(n)
          for n in ast.walk(ast.parse(open(os.path.abspath(_mcp.__file__),
                                           encoding="utf-8").read()))
          if isinstance(n, ast.Call)
          and ast.unparse(n.func).endswith("set_policy")), True)

with clean_ledger(policy=spend.SPEND_POLICY_WINDOW, serving_cap=1.0,
                  window=600.0) as ledger:
    ledger.charge_usd(2.0, spend.SPEND_SOURCE_STAGE5)
    _exc = drive(spend.require_budget, spend.SPEND_SOURCE_STAGE5, "probe")
    _http = _api._budget_declined(
        spend.SpendLimitReached("x", limit=spend.SPEND_LIMIT_CAP))
    check("4c  *** the API answers 503 -- a server-side resource temporarily "
          "exhausted, not 429, which says the CLIENT sent too many ***",
          _http.status_code, _api.BUDGET_DECLINED_STATUS)
    check("4c-i and that constant is 503", _api.BUDGET_DECLINED_STATUS, 503)
    check("4d  *** it carries a computed Retry-After, in whole seconds ***",
          (at(_http.headers or {}, "Retry-After").isdigit()
           if isinstance(at(_http.headers or {}, "Retry-After"), str)
           else False), True)
    check("4d-i ...and the number is the derived wait rather than the window "
          "width",
          500 < int(at(_http.headers or {}, "Retry-After") or 0) <= 601, True)
    check("4e  the detail says NO MATCHING WAS PERFORMED, so a client cannot "
          "read the refusal as a finding of zero trials",
          "NO MATCHING WAS PERFORMED" in _http.detail, True)
    check("4f  ...and it does NOT leak how much this deployment has spent",
          ("$" in _http.detail or "spent" in _http.detail), False)

    # THE MCP PAYLOAD.
    _payload = _mcp._require_budget("match_patient")
    check("4g  *** the MCP tool answers with a payload carrying NO `result` "
          "key -- a model reading `result: {}` beside a caveat would summarise "
          "the caveat away ***",
          sorted(k for k in (_payload or {})
                 if k in ("result", "matches", "trial", "count")), [])
    check("4g-i ...and it says what it is",
          at(_payload or {}, "status"), "spend_limit_reached")
    check("4g-ii ...carries the clinical-use framing every tool result does",
          at(_payload or {}, "not_for_clinical_use"),
          _mcp.NOT_FOR_CLINICAL_USE)
    check("4g-iii ...and a retry hint, because unlike an absent index this "
          "clears itself",
          isinstance(at(_payload or {}, "retry_after_seconds"), float), True)

# UNDER BUDGET, BOTH SURFACES PROCEED. Without this the checks above are
# satisfied by a gate that declines everything.
with clean_ledger(policy=spend.SPEND_POLICY_WINDOW, serving_cap=100.0,
                  window=600.0) as ledger:
    ledger.charge_usd(1.0, spend.SPEND_SOURCE_STAGE5)
    check("4h  CLEAN CONTROL: under budget the MCP gate returns None and the "
          "tool proceeds", _mcp._require_budget("match_patient"), None)
    check("4h-i ...and the API's gate raises nothing",
          raised(spend.require_budget, spend.SPEND_SOURCE_STAGE5, "probe"),
          None)

# THE HEALTH ENDPOINT REPORTS AND DOES NOT DECIDE.
check("4i  *** /health carries a `spend` block ***",
      "\"spend\"" in open(os.path.abspath(_api.__file__), encoding="utf-8")
      .read() or "'spend'" in open(os.path.abspath(_api.__file__),
                                   encoding="utf-8").read(), True)


def health_decides_on_spend():
    """Does `healthy` depend on the spend state? It must NOT.

    FOLDING A BUDGET STOP INTO `healthy` IS ACTIVELY HARMFUL: docker-compose
    probes /health with `curl -f`, an unhealthy container is RESTARTED, and a
    restart empties the rolling window -- so the health check would become the
    mechanism that defeats the brake, on a loop.
    """
    tree = ast.parse(open(os.path.abspath(_api.__file__),
                          encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and getattr(node.targets[0], "id", None) == "healthy":
            return "spend" in ast.unparse(node.value)
    return None


check("4j  *** `healthy` is decided WITHOUT the spend state -- a budget stop "
      "must not make a container unhealthy, because a restart empties the "
      "window and defeats the brake ***",
      health_decides_on_spend(), False)
check("4j-i non-degeneracy: the `healthy` assignment was found at all",
      health_decides_on_spend() is not None, True)


# ===========================================================================
# SECTION 5 -- THE SERVING SURFACES, DRIVEN
# ===========================================================================

section("SECTION 5 -- the changed response shapes are DRIVEN, not read")

# *** DRIVEN RATHER THAN ASSERTED FROM SOURCE, which is the whole reason this
# section exists. Section 4 reads the translator; this runs the endpoint and
# reads what a client actually receives, because a contract change to a serving
# surface that has only been reasoned about is what the pass before this one
# refused to ship.
#
# `TestClient` IS USED WITHOUT ITS CONTEXT MANAGER, DELIBERATELY. Entering it
# runs the lifespan, which compiles the graph and probes a live Qdrant; this
# file has neither and needs neither. The two things the lifespan installs that
# matter here -- the policy and `graph` -- are installed by hand below, and
# check 4a already pins that the lifespan is where they come from in a real
# process.
_MINIMAL_BUNDLE = {"resourceType": "Bundle",
                   "entry": [{"resource": {"resourceType": "Patient",
                                           "id": "p1"}}]}


def drive_match(over_budget):
    """POST /match with the budget spent or not. Returns (status, headers, body)."""
    try:
        from starlette.testclient import TestClient
    except Exception as exc:                                    # noqa: BLE001
        return _Absent(f"no TestClient: {exc}")
    _saved_graph = _api.graph
    _saved_pipeline = _api.match_patient_to_trials
    _saved_parse = _api.parse_fhir_bundle
    _saved_log = _api.log_inference
    _calls = []
    try:
        _api.graph = object()
        _api.parse_fhir_bundle = lambda b: {"patient_id": "p1", "age": 61}
        _api.log_inference = lambda *a, **kw: os.path.join(_TMP, "unused.db")

        def _stand_in(patient_data, graph):
            _calls.append(patient_data)
            return {"patient_id": "p1", "matches": [], "near_misses": [],
                    "not_evaluable": [], "primary_condition": "x",
                    "candidates_retrieved": 0, "candidates_reranked": 0,
                    "candidates_after_rule_filter": 0,
                    "candidates_after_quality_filter": 0,
                    "candidates_evaluated": 0, "total_time_seconds": 0.0,
                    "llm_classifier_input_tokens": 0,
                    "llm_classifier_output_tokens": 0,
                    "estimated_cost_usd": 0.0, "error": ""}

        _api.match_patient_to_trials = _stand_in
        with clean_ledger(policy=spend.SPEND_POLICY_WINDOW, serving_cap=1.0,
                          window=600.0) as ledger:
            if over_budget:
                ledger.charge_usd(5.0, spend.SPEND_SOURCE_STAGE5)
            client = TestClient(_api.app, raise_server_exceptions=False)
            resp = client.post("/match", json={"fhir_bundle": _MINIMAL_BUNDLE})
            return (resp.status_code, dict(resp.headers),
                    drive(resp.json), len(_calls))
    finally:
        _api.graph = _saved_graph
        _api.match_patient_to_trials = _saved_pipeline
        _api.parse_fhir_bundle = _saved_parse
        _api.log_inference = _saved_log


_UNDER = drive_match(over_budget=False)
_OVER = drive_match(over_budget=True)

check("5a  CLEAN CONTROL: under budget, POST /match is served -- so 5b below "
      "is measured against an endpoint that can answer",
      (_UNDER[0] if not isinstance(_UNDER, _Absent) else _UNDER), 200)
check("5a-i ...and the pipeline really ran once",
      (_UNDER[3] if not isinstance(_UNDER, _Absent) else _UNDER), 1)
check("5b  *** over budget, POST /match answers 503 ***",
      (_OVER[0] if not isinstance(_OVER, _Absent) else _OVER), 503)
check("5b-i *** ...and THE PIPELINE NEVER RAN, so a declined request costs "
      "this server nothing at all -- not a parse, not a Qdrant round trip ***",
      (_OVER[3] if not isinstance(_OVER, _Absent) else _OVER), 0)
check("5b-ii ...with a Retry-After header a client can act on",
      (at(_OVER[1], "retry-after").isdigit()
       if not isinstance(_OVER, _Absent)
       and isinstance(at(_OVER[1], "retry-after"), str) else False), True)
check("5b-iii ...and a body that refuses rather than reporting an empty match",
      ("NO MATCHING WAS PERFORMED"
       in json.dumps(_OVER[2] if not isinstance(_OVER, _Absent) else {})),
      True)
check("5b-iv ...carrying no `matches` key at all",
      ("matches" in json.dumps(_OVER[2] if not isinstance(_OVER, _Absent)
                               else {})), False)


def drive_health(over_budget):
    """GET /health with the budget spent or not."""
    try:
        from starlette.testclient import TestClient
    except Exception as exc:                                    # noqa: BLE001
        return _Absent(f"no TestClient: {exc}")
    _saved = (_api.graph, _api.serving_readiness, _api.probe_serving_database)
    try:
        _api.graph = object()
        _api.serving_readiness = lambda **kw: {"status": _api.READY,
                                               "checks": []}
        _api.probe_serving_database = lambda *a, **kw: {"name": "db",
                                                        "ok": True}
        with clean_ledger(policy=spend.SPEND_POLICY_WINDOW, serving_cap=1.0,
                          window=600.0) as ledger:
            if over_budget:
                ledger.charge_usd(5.0, spend.SPEND_SOURCE_STAGE5)
            client = TestClient(_api.app, raise_server_exceptions=False)
            resp = client.get("/health")
            return resp.status_code, drive(resp.json)
    finally:
        (_api.graph, _api.serving_readiness,
         _api.probe_serving_database) = _saved


_H_UNDER = drive_health(False)
_H_OVER = drive_health(True)

check("5c  CLEAN CONTROL: /health is 200 with the budget intact",
      (_H_UNDER[0] if not isinstance(_H_UNDER, _Absent) else _H_UNDER), 200)
check("5d  *** /health STAYS 200 when the budget is spent -- a 503 here would "
      "make the container unhealthy, Docker would restart it, and the restart "
      "would empty the rolling window: the health check defeating the brake, "
      "on a loop ***",
      (_H_OVER[0] if not isinstance(_H_OVER, _Absent) else _H_OVER), 200)
check("5d-i ...while REPORTING it, so an operator can see it",
      at(at(_H_OVER[1] if not isinstance(_H_OVER, _Absent) else {}, "spend")
         or {}, "declining"), True)
check("5d-ii ...and the clean case reports the opposite, so 5d-i is not "
      "satisfied by a field that is always true",
      at(at(_H_UNDER[1] if not isinstance(_H_UNDER, _Absent) else {}, "spend")
         or {}, "declining"), False)
check("5d-iii ...with the policy, the window and the cap named",
      sorted((at(_H_OVER[1] if not isinstance(_H_OVER, _Absent) else {},
                 "spend") or {}).keys()),
      ["cap_usd", "declining", "policy", "retry_after_seconds",
       "window_seconds", "window_usd"])
check("5d-iv ...and the policy it reports is the serving one",
      at(at(_H_OVER[1] if not isinstance(_H_OVER, _Absent) else {}, "spend")
         or {}, "policy"), spend.SPEND_POLICY_WINDOW)


# THE MCP TOOL, DRIVEN THROUGH ITS REAL FUNCTION.
def drive_mcp(over_budget):
    """`match_patient_tool` with the budget spent or not. Never bills."""
    _saved = (_mcp._require_index, _mcp._parse_bundle, _mcp.get_graph,
              _mcp.match_patient_to_trials, _mcp._resolve_bundle_path)
    _calls = []
    try:
        _mcp._require_index = lambda tool: None
        _mcp._resolve_bundle_path = lambda p: "/nowhere/bundle.json"
        _mcp._parse_bundle = lambda p: {"patient_id": "p1"}
        _mcp.get_graph = lambda: object()

        def _stand_in(patient_data, graph):
            _calls.append(patient_data)
            return {"matches": [], "near_misses": []}

        _mcp.match_patient_to_trials = _stand_in
        with clean_ledger(policy=spend.SPEND_POLICY_WINDOW, serving_cap=1.0,
                          window=600.0) as ledger:
            if over_budget:
                ledger.charge_usd(5.0, spend.SPEND_SOURCE_STAGE5)
            return drive(_mcp.match_patient_tool, "bundle.json"), len(_calls)
    finally:
        (_mcp._require_index, _mcp._parse_bundle, _mcp.get_graph,
         _mcp.match_patient_to_trials, _mcp._resolve_bundle_path) = _saved


_M_UNDER, _M_UNDER_CALLS = drive_mcp(False)
_M_OVER, _M_OVER_CALLS = drive_mcp(True)

check("5e  CLEAN CONTROL: under budget the MCP tool answers with a `result`",
      ("result" in (_M_UNDER or {}), _M_UNDER_CALLS), (True, 1))
check("5f  *** over budget it answers with NO `result` key and the pipeline "
      "never ran ***",
      ("result" in (_M_OVER or {}), _M_OVER_CALLS), (False, 0))
check("5f-i ...and says which limit stopped it",
      at(_M_OVER or {}, "limit"), spend.SPEND_LIMIT_CAP)


# ===========================================================================
# SECTION 6 -- THE JUDGE
# ===========================================================================

section("SECTION 6 -- the rater's measured cost reaches the ledger")

# *** THE DISJOINT COUNTS, PINNED. ***
#
# Anthropic reports `input_tokens` as the NON-CACHED input only, with the cache
# read and the two cache-creation figures BESIDE it -- the shape
# `oncotriage/agent/bedrock_anthropic_adapter.py` had to sum back for Converse,
# because OpenAI's `prompt_tokens` INCLUDES its cached portion. `price_usage`
# must NOT sum them: each tier has its own rate, and a cache read costs a tenth
# of an uncached token. The arithmetic is pinned term by term so a "fix" that
# summed them -- which is what a reader who knew only the OpenAI shape would
# do -- fails here rather than over-charging every judged run by the cached
# amount.
_MODEL = "claude-sonnet-4-6"
_RATES = _rater.rater_pricing(_MODEL)
_USAGE = {"input_tokens": 1000, "output_tokens": 200,
          "cache_read_input_tokens": 5000,
          "cache_creation_5m": 300, "cache_creation_1h": 100}
_EXPECTED = (1000 * _RATES["input"] + 200 * _RATES["output"]
             + 5000 * _RATES["cache_read"]
             + 300 * _RATES["cache_write_5m"] + 100 * _RATES["cache_write_1h"])

check("6a  *** the five usage figures are priced at five rates and NOT summed "
      "-- the disjoint-counts trap the Converse branch already paid for ***",
      round(_rater.price_usage(_MODEL, _USAGE), 10), round(_EXPECTED, 10))
check("6a-i non-degeneracy: the five terms really differ, so 6a is not "
      "comparing one rate with itself",
      len({_RATES["input"], _RATES["output"], _RATES["cache_read"],
           _RATES["cache_write_5m"], _RATES["cache_write_1h"]}), 5)
check("6a-ii *** the WRONG arithmetic -- summing the input tiers the way an "
      "OpenAI-shaped reader would -- gives a different number, so 6a can "
      "fail ***",
      round((1000 + 5000 + 300 + 100) * _RATES["input"]
            + 200 * _RATES["output"], 10) == round(_EXPECTED, 10), False)
check("6a-iii the batch discount is applied, so this is not the standard-tier "
      "price",
      _RATES["input"] < config.RATER_PRICING["models"][_MODEL]["input_per_mtok"]
      / 1e6, True)

with clean_ledger(cap=1000.0) as ledger:
    _added = _rater.charge_batch_to_ledger(_MODEL, _USAGE)
    check("6b  *** the measured cost reaches the shared ledger ***",
          round(_added, 10), round(_EXPECTED, 10))
    check("6b-i ...under its own source, so a report can say which of the "
          "program's two spends moved the total",
          {k: round(v, 10) for k, v in ledger.by_source().items()},
          {spend.SPEND_SOURCE_RATER: round(_EXPECTED, 10)})
    check("6b-ii ...and the campaign total is the same money",
          round(ledger.total, 10), round(_EXPECTED, 10))

with clean_ledger(cap=1000.0) as ledger:
    check("6c  an unpriceable model is COUNTED and charges nothing, rather "
          "than aborting a batch already paid for",
          (_rater.charge_batch_to_ledger("no-such-model", _USAGE),
           ledger.total), (0.0, 0.0))
    check("6c-i ...and the fault says the total is lower than the truth",
          any(k.startswith("rater_unpriced:")
              for k in spend.SPEND_LEDGER_FAULTS), True)


class _BatchStub:
    """Counts `messages.batches.create` calls. Sends nothing, bills nothing."""

    def __init__(self):
        self.created = []

    @property
    def messages(self):
        return types.SimpleNamespace(batches=self)

    def create(self, requests):
        self.created.append(len(requests))
        return types.SimpleNamespace(id=f"batch_{len(self.created)}")


def drive_submit(*, spent, cap, chunks):
    """Drive the REAL submit_batches under a given budget."""
    stub = _BatchStub()
    state, path = {}, os.path.join(_TMP, "rater_state_probe.json")
    with clean_ledger(cap=cap) as ledger:
        if spent:
            ledger.charge_usd(spent, spend.SPEND_SOURCE_RATER)
        outcome = raised(_rater.submit_batches, stub, chunks, state, path,
                         "primary")
    return outcome, stub.created


_CHUNKS = [[{"custom_id": "a"}], [{"custom_id": "b"}], [{"custom_id": "c"}]]

check("6d  CLEAN CONTROL: with budget, every chunk is submitted",
      drive_submit(spent=0.0, cap=100.0, chunks=_CHUNKS), (None, [1, 1, 1]))
check("6e  *** with the budget already spent, NOT ONE batch is created ***",
      drive_submit(spent=200.0, cap=100.0, chunks=_CHUNKS),
      ("SpendLimitReached", []))

# *** THE PER-CHUNK GRAIN, WHICH IS THE OVERSHOOT BOUND. *** A gate that ran
# once before the loop would submit all three; this one stops at the chunk that
# crosses.
def drive_submit_crossing():
    stub = _BatchStub()
    state, path = {}, os.path.join(_TMP, "rater_state_probe2.json")
    with clean_ledger(cap=10.0) as ledger:
        ledger.charge_usd(9.0, spend.SPEND_SOURCE_RATER)
        _orig = stub.create

        def _create_and_charge(requests):
            out = _orig(requests)
            ledger.charge_usd(1.0, spend.SPEND_SOURCE_RATER)
            return out

        stub.create = _create_and_charge
        outcome = raised(_rater.submit_batches, stub, _CHUNKS, state, path,
                         "primary")
    return outcome, stub.created


check("6f  *** the gate is PER CHUNK: a session that crosses its cap on the "
      "first batch does not submit the other two ***",
      drive_submit_crossing(), ("SpendLimitReached", [1]))

# THE STATE SEED: a resumed session continues under the remaining budget.
check("6g  a session with a recorded spend seeds from it",
      _rater.rater_spend_before({_rater.STATE_SPEND_KEY: 12.5,
                                 "batches": [1, 2]}),
      spend.LedgerSeed(usd=12.5, rows=0, unpriced=0, runs=2,
                       source=spend.SEED_SOURCE_RATER_STATE))
check("6g-i a fresh state seeds nothing, which is a VALUE and not an absence",
      _rater.rater_spend_before({}).source, spend.SEED_SOURCE_NONE)
for _bad in ({"spend_usd": "x"}, {"spend_usd": float("nan")},
             {"spend_usd": -1}, None, {"spend_usd": True}):
    check(f"6g-ii a malformed recorded spend ({_bad!r}) seeds FRESH rather "
          f"than raising -- a judge must not refuse to start because its own "
          f"history is unreadable",
          _rater.rater_spend_before(_bad).usd, 0.0)

with clean_ledger(cap=20.0) as ledger:
    ledger.seed(_rater.rater_spend_before({_rater.STATE_SPEND_KEY: 18.0}))
    check("6h  *** a RESUMED session runs under the REMAINDER, not a fresh "
          "cap ***", round(spend.remaining(), 6), 2.0)
    ledger.charge_usd(3.0, spend.SPEND_SOURCE_RATER)
    check("6h-i ...so it stops after the batch that crosses",
          spend.cap_exceeded(), True)


# ===========================================================================
# SECTION 7 -- RAGAS
# ===========================================================================

section("SECTION 7 -- the ragas harness charges and is gated")

_JUDGE_MODEL = config.RAGAS_JUDGE_MODEL if hasattr(
    config, "RAGAS_JUDGE_MODEL") else "claude-sonnet-4-6"


class _JudgeUsage:
    def __init__(self, i, o):
        self.input_tokens = i
        self.output_tokens = o


class _EmbedUsage:
    def __init__(self, t):
        self.total_tokens = t


with clean_ledger(cap=1000.0) as ledger:
    tally = _ragas.UsageTally(judge_model=_JUDGE_MODEL,
                              embedding_model=config.EMBEDDING_MODEL)
    tally.record_judge(_JudgeUsage(1000, 200))
    tally.record_embedding(_EmbedUsage(5000))
    _cost = tally.cost(_JUDGE_MODEL, config.EMBEDDING_MODEL)
    check("7a  *** the harness's OWN reported cost and the shared ledger agree "
          "to the cent -- if they ever disagree, one of the two has stopped "
          "seeing a call ***",
          round(ledger.total, 6), round(_cost["total_usd"], 6))
    check("7a-i ...and the two vendors land under two sources",
          sorted(ledger.by_source()),
          sorted([spend.SPEND_SOURCE_RAGAS_JUDGE,
                  spend.SPEND_SOURCE_RAGAS_EMBEDDING]))
    check("7a-ii non-degeneracy: both figures are non-zero",
          (ledger.total > 0, _cost["total_usd"] > 0), (True, True))

with clean_ledger(cap=1000.0) as ledger:
    # A TALLY BUILT WITHOUT MODEL IDS still records tokens exactly as it always
    # did, and its missing prices are COUNTED rather than guessed.
    tally = _ragas.UsageTally()
    tally.record_judge(_JudgeUsage(1000, 200))
    check("7b  a tally with no model ids charges nothing...",
          ledger.total, 0.0)
    check("7b-i ...counts the fault...",
          any(k.startswith("ragas_unpriced:") for k in
              spend.SPEND_LEDGER_FAULTS), True)
    check("7b-ii ...and still records the tokens, so the harness's own report "
          "is unchanged", tally.judge_input_tokens, 1000)


def drive_ragas_seam(builder_name, over_budget):
    """Drive the REAL recording wrapper a builder installs. Bills nothing.

    THE CLIENT IS A STAND-IN whose `create` records that it was reached. The
    gate sits ABOVE the `await`, so an over-budget arm must leave that recorder
    untouched -- which is the property "a raise here means no request is
    issued" made into a measurement.
    """
    import asyncio
    reached = []

    class _AsyncClient:
        def __init__(self):
            self.messages = types.SimpleNamespace(create=self._create)
            self.embeddings = types.SimpleNamespace(create=self._create)

        async def _create(self, *a, **kw):
            reached.append(True)
            return types.SimpleNamespace(usage=None)

    client = _AsyncClient()
    if builder_name == "judge":
        real = client.messages.create
        source = spend.SPEND_SOURCE_RAGAS_JUDGE
    else:
        real = client.embeddings.create
        source = spend.SPEND_SOURCE_RAGAS_EMBEDDING

    async def recording_create(*a, **kw):
        spend.require_budget(source, "probe")
        return await real(*a, **kw)

    with clean_ledger(cap=1.0) as ledger:
        if over_budget:
            ledger.charge_usd(5.0, source)
        outcome = raised(asyncio.run, recording_create())
    return outcome, len(reached)


# THE WRAPPER'S SHAPE IS PINNED AGAINST THE SHIPPED SOURCE, so the probe above
# is a probe of what ships rather than of itself.
_RAGAS_SRC = open(os.path.abspath(_ragas.__file__), encoding="utf-8").read()


def gate_is_above_the_await(builder):
    tree = ast.parse(_RAGAS_SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == builder:
            for inner in ast.walk(node):
                if isinstance(inner, ast.AsyncFunctionDef) \
                        and inner.name == "recording_create":
                    body = inner.body
                    gate_at = [i for i, s in enumerate(body)
                               if "require_budget" in ast.unparse(s)]
                    await_at = [i for i, s in enumerate(body)
                                if "real_create" in ast.unparse(s)]
                    if not gate_at or not await_at:
                        return None
                    return max(gate_at) < min(await_at)
    return None


for _b in ("build_judge", "build_embeddings"):
    check(f"7c  *** {_b}'s gate is ABOVE the await, so a raise means NO "
          f"request was issued ***", gate_is_above_the_await(_b), True)

check("7d  CLEAN CONTROL: with budget, the wrapper reaches the client",
      drive_ragas_seam("judge", over_budget=False), (None, 1))
check("7e  *** over budget, it raises and the client is NEVER reached ***",
      drive_ragas_seam("judge", over_budget=True), ("SpendLimitReached", 0))
check("7f  ...the embedder likewise",
      (drive_ragas_seam("embed", over_budget=False),
       drive_ragas_seam("embed", over_budget=True)),
      ((None, 1), ("SpendLimitReached", 0)))


# ===========================================================================
# SECTION 8 -- THE ABLATION STUDY, DRIVEN TO ITS CAP
# ===========================================================================

section("SECTION 8 -- the study stops cleanly, records why, and resumes")

# *** THE REAL `main()` IS DRIVEN. *** Its submit loop, its `_on_done`
# callback, its executor lifecycle, its `_run_pair_unless_stopped` guard, its
# `_create_run` / `_finalize_run` writes and its closing block are the shipped
# ones. What is replaced is everything that would cost money or need a network:
#
#   build_bm25_index_from_qdrant   a live Qdrant scroll
#   build_matching_graph           a compiled StateGraph the stand-in never uses
#   load_all_patients              a corpus read
#   stratified_sample              a corpus-shaped draw
#   match_patient_ablation         THE BILLED CALL -- the stand-in CHARGES the
#                                  ledger and issues no request
#   run_fingerprint.current        an index probe over the wire
#   tracking.*                     an MLflow file store
#   CaffeinateSession              a macOS subprocess
#
# THE GRAPH IS NEVER INVOKED, so no billed call is reachable.
_STUDY_ATTRS = ("build_bm25_index_from_qdrant", "build_matching_graph",
                "load_all_patients", "stratified_sample",
                "match_patient_ablation", "log_ablation_result",
                "run_fingerprint", "tracking", "CaffeinateSession",
                "generate_summary")


def flags_key(flags):
    """A configuration's identity, from the flags dict `_process_one` is handed.

    THE PAIR IS (config, patient) AND NOT THE PATIENT ALONE, which is the
    checkpoint's own key. The first version of this harness recorded the
    patient id only -- and every patient appears once per CONFIGURATION, so
    `set(charged)` collapsed eight distinct pairs into four and the re-run
    check reported a study that had re-billed nothing as one that had.
    """
    return tuple(sorted(flags.items()))


def drive_study(db_path, *, cap, per_pair_usd, sample_size, configs,
                seed_from_db=True):
    """Run the real `main()` under a stub. Returns (exit, charged pairs)."""
    saved = {name: getattr(_study, name) for name in _STUDY_ATTRS}
    saved_argv = list(sys.argv)
    charged = []
    try:
        _study.build_bm25_index_from_qdrant = lambda: ({}, ["NCT00000001"])
        _study.build_matching_graph = lambda: object()
        _study.load_all_patients = lambda _p: [
            {"patient_id": f"p{i}", "conditions": [], "age": 60}
            for i in range(sample_size)]
        _study.stratified_sample = lambda pats, n, seed: pats[:n]
        _study.CaffeinateSession = lambda *a, **kw: contextlib.nullcontext()
        _study.generate_summary = lambda **kw: None
        _study.run_fingerprint = types.SimpleNamespace(
            current=lambda: {"fingerprint_version": 99},
            clear_cache=lambda: None,
            summary=lambda fp: "a stand-in configuration stamp",
            # A 2-TUPLE, WHICH IS WHAT `load_ablation_checkpoint` UNPACKS.
            # The first version of this stub returned a dict -- which unpacks
            # to its two KEYS, so `outcome` came back as the string "outcome",
            # the checkpoint was REFUSED, and the resume silently re-ran pairs
            # the stopped run had completed. The re-run check below is what
            # caught it, which is the argument for having written that check as
            # an exact count rather than as "fewer than eight".
            compare=lambda a, b: ("match", "a stand-in comparison"),
            refusal_lines=lambda *a, **kw: [],
            ResumeRefusal=RuntimeError,
            # `save_ablation_checkpoint` READS THIS, and a stub without it
            # raises AttributeError inside `_on_done` -- a done-CALLBACK, whose
            # exceptions concurrent.futures swallows. The checkpoint was then
            # never written, silently, and the resume re-ran everything. Found
            # by the exact re-run count below and not by reading.
            COLLECTION_IDENTITY=("qdrant_collection", "collection_points"),
            FP_MATCH="match", FP_ABSENT="absent")
        _study.tracking = types.SimpleNamespace(
            start_run=lambda *a, **kw: None,
            log_run_metrics=lambda *a, **kw: None,
            end_run=lambda *a, **kw: None,
            RUN_STATUSES=("FINISHED", "FAILED", "KILLED"))

        def _stand_in(patient_data, bm25_index, nct_ids, graph, flags):
            # THE ONLY MONEY IN THIS TEST, and it moves no request. Charging
            # here is what makes the study's own gate reachable at the grain
            # the shipped code polls it.
            spend.SPEND_LEDGER.charge_usd(per_pair_usd,
                                          spend.SPEND_SOURCE_STAGE5)
            charged.append((flags_key(flags), patient_data["patient_id"]))
            return {"error": "", "matches": [], "near_misses": [],
                    "not_evaluable": [], "stage_timings": {},
                    "primary_condition": "x", "candidates_retrieved": 0,
                    "candidates_reranked": 0,
                    "candidates_after_rule_filter": 0,
                    "candidates_after_quality_filter": 0,
                    "candidates_evaluated": 0, "mesh_dropped": 0,
                    "stage_dropped": 0, "histology_dropped": 0,
                    "llm_classifier_input_tokens": 0,
                    "llm_classifier_output_tokens": 0,
                    "estimated_cost_usd": per_pair_usd}

        _study.match_patient_ablation = _stand_in
        def _write_row(run_id, config_name, patient_data, result,
                       ablation_flags, db_path=None):
            """A stand-in writer that stores the ONE column the seed reads.

            THE REAL `log_ablation_result` IS NOT USED, because it prices the
            result against `PRICING_CONFIG` and writes thirty columns this
            harness has no honest values for. What it is replaced BY is not a
            no-op: `ablation_spend_before` sums `estimated_cost_usd` over
            `ablation_results`, so a writer that wrote nothing would make the
            resume seed read FRESH -- and check 8d would then be measuring the
            stub rather than the seed.
            """
            conn = sqlite3.connect(_study.ablation_db(db_path))
            try:
                conn.execute(
                    "INSERT INTO ablation_results (run_id, config_name, "
                    "patient_id, estimated_cost_usd) VALUES (?, ?, ?, ?)",
                    (run_id, config_name, patient_data["patient_id"],
                     result.get("estimated_cost_usd", 0.0)))
                conn.commit()
            finally:
                conn.close()

        _study.log_ablation_result = _write_row

        sys.argv = ["study", "--db", db_path, "--sample-size",
                    str(sample_size), "--configs"] + list(configs)
        _prev_cap = config.SPEND_CAP_USD
        _prev_policy = spend.policy()
        config.SPEND_CAP_USD = cap
        spend.set_policy(spend.SPEND_POLICY_CAMPAIGN, "test")
        try:
            outcome = drive(_study.main)
        finally:
            config.SPEND_CAP_USD = _prev_cap
            spend.set_policy(_prev_policy, "restore")
            spend.SPEND_LEDGER.reset()
            spend.SPEND_STOP.reset()
        return outcome, charged
    finally:
        for name, value in saved.items():
            setattr(_study, name, value)
        sys.argv = saved_argv


def run_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT config_name, status, stop_reason FROM ablation_runs "
            "ORDER BY id").fetchall()
    finally:
        conn.close()


def result_count(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM ablation_results").fetchone()[0]
    finally:
        conn.close()


_STUDY_DB = os.path.join(_TMP, "study", "ablation_results.db")
os.makedirs(os.path.dirname(_STUDY_DB), exist_ok=True)

# --- (a) THE CLEAN CONTROL, FIRST -------------------------------------------
# Without it, "the study stopped" below is equally satisfied by a study that
# could not run at all.
_CLEAN_DB = os.path.join(_TMP, "study-clean", "ablation_results.db")
os.makedirs(os.path.dirname(_CLEAN_DB), exist_ok=True)
_clean_out, _clean_charged = drive_study(
    _CLEAN_DB, cap=1000.0, per_pair_usd=0.10, sample_size=4,
    configs=["full_pipeline", "no_mesh_filter"])
check("8a  CLEAN CONTROL: with budget, the study runs every pair of every "
      "configuration", len(_clean_charged), 8)
check("8a-i ...and both configurations record COMPLETE with no stop reason",
      run_rows(_CLEAN_DB),
      [("full_pipeline", "COMPLETE", None), ("no_mesh_filter", "COMPLETE",
                                             None)])

# --- (b) DRIVEN TO ITS CAP ---------------------------------------------------
#
# THE SAMPLE IS BIGGER THAN `MAX_WORKERS` ON PURPOSE. With four patients and
# twelve workers every pair is in flight before the first one finishes, so a
# cap crossed part-way cancels NOTHING and leaves the configuration genuinely
# COMPLETE -- which is correct behaviour and is what check 8f measures on
# purpose. To exercise the STOPPED path the study has to have queued work when
# the latch trips, and that needs more pairs than workers.
_SAMPLE = max(20, config.MAX_WORKERS * 2)
_TOTAL_PAIRS = _SAMPLE * 2
_stop_out, _stop_charged = drive_study(
    _STUDY_DB, cap=3.0, per_pair_usd=1.0, sample_size=_SAMPLE,
    configs=["full_pipeline", "no_mesh_filter"])

check("8b  *** the study STOPS rather than running its whole sample ***",
      len(_stop_charged) < _TOTAL_PAIRS, True)
check("8b-i ...having spent at least the cap, so it stopped for the right "
      "reason", len(_stop_charged) >= 3, True)
check("8b-ii ...and the overshoot is bounded by the pairs already in flight, "
      "not by the whole sample",
      len(_stop_charged) <= 3 + config.MAX_WORKERS, True)

_ROWS = run_rows(_STUDY_DB)
check("8c  *** the FIRST configuration is recorded STOPPED with the reason "
      "`spend_cap` -- a column, not a fifth status, so every existing reader "
      "of `status` is unchanged ***",
      _ROWS[0] if _ROWS else _Absent("no rows"),
      ("full_pipeline", "STOPPED", _study.RUN_STOP_REASON_SPEND_CAP))
check("8c-i ...and the SECOND configuration was never opened, so a stopped "
      "study leaves no empty run row for generate_summary to average over",
      len(_ROWS), 1)

# --- (c) THE RESUME, UNDER THE REMAINDER -------------------------------------
# The SAME database, a RAISED cap. The checkpoint holds what already ran, and
# the ledger is seeded from the rows -- so the resume neither re-bills what was
# done nor gets a fresh budget.
_before = result_count(_STUDY_DB)
_seed = _study.ablation_spend_before(_STUDY_DB)
check("8d  *** the resume seeds from THIS database's own rows, which is what "
      "makes the cap a budget for the STUDY rather than for one invocation of "
      "it ***", (_seed.source, _seed.rows > 0),
      (spend.SEED_SOURCE_CAMPAIGN, True))

_resume_out, _resume_charged = drive_study(
    _STUDY_DB, cap=1000.0, per_pair_usd=1.0, sample_size=_SAMPLE,
    configs=["full_pipeline", "no_mesh_filter"])
check("8e  *** the resume runs ONLY what the stopped run did not -- every "
      "pair it re-billed would be money the checkpoint exists to save ***",
      len(_stop_charged) + len(_resume_charged), _TOTAL_PAIRS)
check("8e-i ...and no (config, patient) pair is run twice across the two "
      "invocations",
      len(set(_stop_charged) | set(_resume_charged)),
      len(_stop_charged) + len(_resume_charged))
check("8e-ii ...so the database holds the whole sample and no duplicate",
      result_count(_STUDY_DB), _TOTAL_PAIRS)

# --- (d) A STOP THAT LANDS AFTER THE COHORT IS COVERED IS NOT A STOPPED RUN ---
# The batch runner's scenario C, applied here: a cap crossed by the LAST pair
# of the LAST configuration cut nothing short.
_COVERED_DB = os.path.join(_TMP, "study-covered", "ablation_results.db")
os.makedirs(os.path.dirname(_COVERED_DB), exist_ok=True)
_cov_out, _cov_charged = drive_study(
    _COVERED_DB, cap=2.0, per_pair_usd=1.0, sample_size=2,
    configs=["full_pipeline"])
check("8f  *** a cap crossed by the LAST pair leaves the configuration "
      "COMPLETE with NO stop reason: it ran its whole sample, and a reason "
      "beside a COMPLETE status would assert a prefix that does not exist ***",
      run_rows(_COVERED_DB), [("full_pipeline", "COMPLETE", None)])
check("8f-i ...and every pair of it really ran", len(_cov_charged), 2)

# --- (e) THE REASON DERIVATION -----------------------------------------------
check("8g  the operator outranks the budget when both latches are set, so a "
      "person who asked for the stop is not sent to config.SPEND_CAP_USD",
      (_study.STOP_SWITCH.__class__.__name__,), ("_AblationStopSwitch",))
_saved_req = _study.STOP_SWITCH.requested
try:
    spend.SPEND_STOP.trip(spend.SPEND_LIMIT_CAP, "probe")
    _study.STOP_SWITCH.requested = True
    check("8g-i  both set -> operator",
          _study._stop_reason_now(), _study.RUN_STOP_REASON_OPERATOR)
    _study.STOP_SWITCH.requested = False
    check("8g-ii only spend -> spend_cap",
          _study._stop_reason_now(), _study.RUN_STOP_REASON_SPEND_CAP)
    spend.SPEND_STOP.reset()
    spend.SPEND_STOP.trip(spend.SPEND_LIMIT_CALL_CEILING, "probe")
    check("8g-iii the ceiling is its own reason -- a DEFECT REPORT, not a "
          "budget event, so an operator is sent to the traceback rather than "
          "to a cap", _study._stop_reason_now(),
          _study.RUN_STOP_REASON_CALL_CEILING)
    spend.SPEND_STOP.reset()
    check("8g-iv neither -> None", _study._stop_reason_now(), None)
finally:
    _study.STOP_SWITCH.requested = _saved_req
    spend.SPEND_STOP.reset()

# --- (f) THE SEED IS USED BY `main()`, NOT JUST CALLABLE ---------------------
#
# *** 8d ABOVE CALLS `ablation_spend_before` DIRECTLY, WHICH PROVES THE READER
# WORKS AND NOT THAT THE STUDY CONSULTS IT. *** Deleting the one `seed(...)`
# line from `main()` left every check in this section green -- found by the
# revert harness and not by reading, and it is exactly the gap that would let a
# resumed study get a fresh budget every time. So the seed is measured through
# its EFFECT: a database that already holds the whole cap's worth of spend must
# stop the next study before it bills anything.
_SEEDED_DB = os.path.join(_TMP, "study-seeded", "ablation_results.db")
os.makedirs(os.path.dirname(_SEEDED_DB), exist_ok=True)
_study.init_ablation_db(db_path=_SEEDED_DB)
_conn = sqlite3.connect(_SEEDED_DB)
try:
    _conn.execute("INSERT INTO ablation_runs (run_timestamp, config_name, "
                  "config_description, sample_size) "
                  "VALUES ('2026-01-01', 'full_pipeline', 'd', 4)")
    _conn.execute("INSERT INTO ablation_results (run_id, config_name, "
                  "patient_id, estimated_cost_usd) VALUES (1, 'prior', "
                  "'prior_patient', 50.0)")
    _conn.commit()
finally:
    _conn.close()

_seeded_out, _seeded_charged = drive_study(
    _SEEDED_DB, cap=10.0, per_pair_usd=1.0, sample_size=4,
    configs=["full_pipeline"])
check("8i  *** a study whose database already holds MORE than the cap bills "
      "NOTHING -- which is what says `main()` consults the seed rather than "
      "starting every invocation at zero ***", len(_seeded_charged), 0)

# THE CLEAN CONTROL: the identical drive against a database with no history
# runs its whole sample, so 8i is not satisfied by a study that cannot run.
_UNSEEDED_DB = os.path.join(_TMP, "study-unseeded", "ablation_results.db")
os.makedirs(os.path.dirname(_UNSEEDED_DB), exist_ok=True)
_unseeded_out, _unseeded_charged = drive_study(
    _UNSEEDED_DB, cap=10.0, per_pair_usd=1.0, sample_size=4,
    configs=["full_pipeline"])
check("8i-i CLEAN CONTROL: the same drive against a database with no history "
      "runs every pair", len(_unseeded_charged), 4)

# --- (g) THE PER-PAIR GUARD, WHICH CLOSES THE SWEEP'S EDGE -------------------
#
# THE SWEEP HAS AN EDGE AND THE GUARD IS WHAT CLOSES IT: the latch is set
# inside `_on_done` on a WORKER thread while the submit loop polls on the MAIN
# thread, so exactly one more pair can be submitted after the sweep that would
# have cancelled it. It is DRIVEN DIRECTLY rather than raced for, because a
# race that happens to lose is a check that reports the guard as working when
# it has been deleted -- which the revert harness found this section doing.
_ran = []


def _pair_body(**kwargs):
    _ran.append(kwargs)
    return "ok"


check("8j  CLEAN CONTROL: with no limit reached, the pair runs",
      (drive(_study._run_pair_unless_stopped, _pair_body, x=1), len(_ran)),
      ("ok", 1))

spend.SPEND_STOP.trip(spend.SPEND_LIMIT_CAP, "probe")
try:
    check("8k  *** once a spend limit has latched, a pair submitted in the "
          "sweep's edge REFUSES to begin rather than issuing a live billed "
          "call after the budget is gone ***",
          (raised(_study._run_pair_unless_stopped, _pair_body, x=2),
           len(_ran)), ("_PairCancelled", 1))
finally:
    spend.SPEND_STOP.reset()

_saved_req = _study.STOP_SWITCH.requested
try:
    _study.STOP_SWITCH.requested = True
    check("8k-i ...and the operator switch still does the same, so 8k did not "
          "replace one guard with the other",
          raised(_study._run_pair_unless_stopped, _pair_body, x=3),
          "_PairCancelled")
finally:
    _study.STOP_SWITCH.requested = _saved_req

def refusal_message(*, operator, budget):
    _saved = _study.STOP_SWITCH.requested
    try:
        _study.STOP_SWITCH.requested = operator
        if budget:
            spend.SPEND_STOP.trip(spend.SPEND_LIMIT_CAP, "probe")
        try:
            _study._run_pair_unless_stopped(_pair_body)
            return None
        except BaseException as exc:                            # noqa: BLE001
            return str(exc)
    finally:
        _study.STOP_SWITCH.requested = _saved
        spend.SPEND_STOP.reset()


_MSG_OP = refusal_message(operator=True, budget=False)
_MSG_SPEND = refusal_message(operator=False, budget=True)
check("8k-ii *** the two refusals carry DIFFERENT messages, because 'the "
      "operator stop switch tripped' is false of a study nobody touched ***",
      (_MSG_OP != _MSG_SPEND, "operator" in (_MSG_OP or ""),
       "spend" in (_MSG_SPEND or "")), (True, True, True))
check("8k-iii non-degeneracy: both really refused rather than returning",
      (_MSG_OP is not None, _MSG_SPEND is not None), (True, True))


check("8h  the stop-reason vocabulary is closed and NULL is the fourth "
      "reading rather than a fourth member",
      _study.RUN_STOP_REASONS,
      (_study.RUN_STOP_REASON_OPERATOR, _study.RUN_STOP_REASON_SPEND_CAP,
       _study.RUN_STOP_REASON_CALL_CEILING))


# ===========================================================================
# SECTION 9 -- ONE BUDGET OVER THE WHOLE PROGRAM
# ===========================================================================

section("SECTION 9 -- a campaign-plus-judge sequence under one cap")

# *** THE POINT OF THE PASS, IN ONE SEQUENCE. *** Four billed paths, four price
# tables, one cap. Before this pass the campaign's Stage 5 was the only one the
# budget could see, so a program that ran a campaign and then a judge could
# spend the cap twice and nothing would say so.
with clean_ledger(cap=10.0) as ledger:
    _seq = []
    # 1. The campaign's Stage 5 and its per-patient query embedding.
    ledger.charge_usd(4.0, spend.SPEND_SOURCE_STAGE5)
    ledger.charge_usd(0.5, spend.SPEND_SOURCE_EMBEDDING)
    _seq.append(("after the campaign", round(spend.remaining(), 4),
                 spend.cap_exceeded()))
    # 2. The judge, priced from a different table by a different vendor.
    ledger.charge_usd(4.0, spend.SPEND_SOURCE_RATER)
    _seq.append(("after the judge", round(spend.remaining(), 4),
                 spend.cap_exceeded()))
    # 3. Ragas, which would have been free of charge to the budget before.
    ledger.charge_usd(2.0, spend.SPEND_SOURCE_RAGAS_JUDGE)
    _seq.append(("after ragas", round(spend.remaining(), 4),
                 spend.cap_exceeded()))
    check("9a  *** the cap is reached by the SUM of four paths priced four "
          "ways, not by Stage 5 alone ***",
          _seq,
          [("after the campaign", 5.5, False),
           ("after the judge", 1.5, False),
           ("after ragas", -0.5, True)])
    check("9a-i ...and the breakdown says which of them spent what, so an "
          "operator reading a stop knows where to look",
          {k: round(v, 4) for k, v in ledger.by_source().items()},
          {spend.SPEND_SOURCE_STAGE5: 4.0,
           spend.SPEND_SOURCE_EMBEDDING: 0.5,
           spend.SPEND_SOURCE_RATER: 4.0,
           spend.SPEND_SOURCE_RAGAS_JUDGE: 2.0})
    check("9a-ii ...and every one of the four paths would now be DECLINED",
          sorted({raised(spend.require_budget, s, "probe")
                  for s in (spend.SPEND_SOURCE_STAGE5,
                            spend.SPEND_SOURCE_EMBEDDING,
                            spend.SPEND_SOURCE_RATER,
                            spend.SPEND_SOURCE_RAGAS_JUDGE)}),
          ["SpendLimitReached"])

# *** THE COUNTERFACTUAL, WHICH IS WHAT MAKES 9a A MEASUREMENT. *** With only
# Stage 5 charged -- the shipped state before this pass -- the identical
# program is still $6.00 under its cap and every path proceeds.
with clean_ledger(cap=10.0) as ledger:
    ledger.charge_usd(4.0, spend.SPEND_SOURCE_STAGE5)
    check("9b  *** BEFORE-STATE: with only Stage 5 charged, the same program "
          "reads as well within budget and the judge is admitted -- which is "
          "the hole this pass closed ***",
          (round(spend.remaining(), 4), spend.cap_exceeded(),
           raised(spend.require_budget, spend.SPEND_SOURCE_RATER, "probe")),
          (6.0, False, None))

# THE REPORT NAMES WHAT THE CAP DOES NOT COVER, on every run.
with clean_ledger(cap=10.0) as ledger:
    ledger.charge_usd(1.0, spend.SPEND_SOURCE_STAGE5)
    _lines = spend.report_lines()
    check("9c  the closing block names the policy in force",
          any("policy" in ln for ln in _lines), True)
    check("9d  *** ...and NAMES the ungated sites, unconditionally -- a reader "
          "handed a cap is owed, in the same block, the places it does not "
          "reach ***",
          all(any(site in ln for ln in _lines)
              for site in spend.BILLED_SITE_EXEMPTIONS), True)
    check("9d-i ...and says how many there are",
          any("NOT COVERED BY THE CAP" in ln for ln in _lines), True)

with clean_ledger(cap=10.0) as ledger:
    ledger.charge_usd(1.0, spend.SPEND_SOURCE_STAGE5)
    ledger.charge_usd(2.0, spend.SPEND_SOURCE_RATER)
    _lines = spend.report_lines()
    check("9e  a run with more than one spending path prints the breakdown",
          (any(spend.SPEND_SOURCE_RATER in ln for ln in _lines),
           any(spend.SPEND_SOURCE_STAGE5 in ln for ln in _lines)),
          (True, True))

with clean_ledger(cap=10.0) as ledger:
    ledger.charge_usd(1.0, spend.SPEND_SOURCE_STAGE5)
    check("9e-i ...and a run with ONE does not, because a one-row breakdown "
          "under a total it equals is noise",
          any(ln.strip().startswith(spend.SPEND_SOURCE_STAGE5)
              for ln in spend.report_lines()), False)


# ===========================================================================
# SECTION 10 -- THE STUDY'S CLOSING BLOCK SAYS WHICH STOP IT WAS
# ===========================================================================

section("SECTION 10 -- a budget stop and an operator stop read differently")


def study_close(*, spend_stop, operator_stop):
    """Render the study's closing block into a string. Never spends.

    THE SINK IS A FUNCTION WITH A DEFAULT, NOT `list.append`. That was the
    vacuity defect `tests/test_ablation_stop_and_lock.py` had to fix in its own
    5j-5l: `print_study_close` emits a bare `emit()` for a blank line, which
    `list.append` refuses -- so the whole render raised, the harness caught it,
    and three checks compared substrings against the EMPTY STRING and passed.
    """
    lines = []

    def sink(text=""):
        lines.append(str(text))

    _saved = _study.STOP_SWITCH.requested
    try:
        _study.STOP_SWITCH.requested = operator_stop
        if spend_stop:
            spend.SPEND_STOP.trip(spend.SPEND_LIMIT_CAP, "probe")
        _study.print_study_close(_study.STUDY_STATUS_STOPPED, 1.0, 1, 0, 0,
                                 db_path=_STUDY_DB, out=sink)
    finally:
        _study.STOP_SWITCH.requested = _saved
        spend.SPEND_STOP.reset()
    return "\n".join(lines)


check("10z  NON-DEGENERACY: the sink really collects lines, so the substring "
      "checks below are not comparing against an empty string -- the exact "
      "way tests/test_ablation_stop_and_lock.py's own 5j-5l were vacuous",
      len(study_close(spend_stop=False, operator_stop=False)) > 200, True)

with clean_ledger(cap=1.0):
    _budget_text = study_close(spend_stop=True, operator_stop=False)
check("10a *** a study stopped by the BUDGET does not tell an operator to `rm` "
      "a sentinel nobody wrote ***",
      ("rm " in _budget_text, "a spend limit was reached" in _budget_text),
      (False, True))
check("10a-i ...and points at the cap, which is the actual remedy",
      "config.SPEND_CAP_USD" in _budget_text, True)

with clean_ledger(cap=1.0):
    _operator_text = study_close(spend_stop=False, operator_stop=True)
check("10b CLEAN CONTROL: a study stopped by an OPERATOR still gets the "
      "sentinel block, so 10a is not satisfied by a branch that fires always",
      ("an operator asked for it" in _operator_text,
       "--clear-stop" in _operator_text), (True, True))

with clean_ledger(cap=1.0):
    _both_text = study_close(spend_stop=True, operator_stop=True)
check("10c when BOTH are set the operator's block wins, so a person who asked "
      "for the stop is not sent to a cap to explain it",
      "an operator asked for it" in _both_text, True)

with clean_ledger(cap=1.0) as ledger:
    ledger.charge_usd(0.25, spend.SPEND_SOURCE_STAGE5)
    _any_text = study_close(spend_stop=False, operator_stop=True)
check("10d the study's closing block carries the SPEND block on every path, "
      "because it is what an operator asks first about a study that stopped",
      ("SPEND" in _any_text, "campaign total" in _any_text), (True, True))


# ===========================================================================
# SECTION 11 -- NOTHING WAS SPENT, NOTHING WAS LOADED, NOTHING WAS WRITTEN
# ===========================================================================

section("SECTION 11 -- the tree is as it was found")

check("11a no model was loaded: torch never entered sys.modules",
      "torch" in sys.modules, False)
check("11b ...nor transformers", "transformers" in sys.modules, False)

_AFTER = {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
          for p in _READ_FILES}
check("11c every repository file this test reads is byte-identical to how it "
      "was found", sorted(p for p in _READ_FILES
                          if _AFTER[p] != _BASELINE_HASHES[p]), [])
check("11c-i non-degeneracy: the five hashes are five different values, so 11c "
      "is not one file compared with itself",
      len(set(_BASELINE_HASHES.values())), len(_READ_FILES))

check("11d the shipped configuration is restored",
      (config.SPEND_CAP_USD, config.SPEND_CAP_ENFORCED,
       config.SERVING_SPEND_CAP_USD, config.SERVING_SPEND_WINDOW_SECONDS),
      _START_CONFIG)
check("11e ...and the policy is back to the campaign default, so a process "
      "that imports this module is not left under a serving window",
      spend.policy(), spend.SPEND_POLICY_CAMPAIGN)

check("11f the production inference path was never resolved to anything real",
      _paths._RESOLVED["inferences_path"].startswith(_TMP), True)

_paths._RESOLVED.clear()
_paths._RESOLVED.update(_SAVED_RESOLVED)
shutil.rmtree(_TMP, ignore_errors=True)
check("11g the temp tree is gone", os.path.exists(_TMP), False)


print("\n" + "=" * 78)
print(f"RESULTS: {_RESULTS['passed']} passed, {_RESULTS['failed']} failed")
print("=" * 78)
for _label, _expected, _actual in _FAILURES:
    print(f"  FAILED: {_label}")
    print(f"     expected: {_expected!r}")
    print(f"     actual:   {_actual!r}")

sys.exit(1 if _RESULTS["failed"] else 0)
