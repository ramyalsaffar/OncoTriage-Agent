# Resume Configuration Fingerprint Test
######################################

"""Three paid harnesses resume from persisted state. None of them recorded what
that state was produced UNDER, and one of them silently overwrote the record of
what it was.

WHAT WAS BROKEN, PER HARNESS
----------------------------
``oncotriage/evaluation/run_harness.py``  ``main()`` overwrote
    ``manifest["environment"]`` on EVERY invocation, ``--only`` re-runs into an
    existing directory included. So one directory could hold records from two
    prompt versions or two Qdrant collections while its environment block
    described only the last writer -- and both downstream consumers
    (``rater.py``, ``ragas_harness.py``) iterate ``manifest["runs"]`` whole.
``oncotriage/batch/runner.py``  ``load_checkpoint()`` on an UNREADABLE file
    warned and returned an empty set, which on a resume is a silent decision to
    re-run every patient an earlier process completed at a live Stage 5 call
    each. And a readable checkpoint said WHAT was done and never what it was
    done under.
``oncotriage/ablation/study.py``  the same two, per database file.

WHAT THIS FILE HOLDS
--------------------
    1. ``oncotriage/run_fingerprint.py``: the stamp's shape, its cache, and
       every one of the five ``FP_OUTCOMES`` driven to its own answer.
    1b. ``llm_classifier_renderer_digest``: the module set DERIVED by a static
       closure rather than trusted, what the AST normalisation does and does
       not see, and the gap itself closed -- a one-character renderer edit with
       NO ``PROMPT_VERSION`` bump, made in a COPY of the package, shown to move
       the digest and to produce FP_CHANGED naming that field.
    2. ``utils.preserve_corrupt_file``: numbering, exhaustion, and the MOVE vs
       COPY distinction -- which is the difference between a refusal that
       sticks and one that is loud once and silent afterwards.
    3. The batch checkpoint: round trip, every mismatch class, the legacy
       no-fingerprint file, the corrupt file, and the two things that must
       NEVER happen on a refusal -- a deletion, and a silent re-run.
    4. The ablation checkpoint: the same, with pass 20f-3's per-database
       isolation shown intact.
    5. ``run_harness.main()`` DRIVEN END TO END with the paid and networked
       seams replaced: a fresh directory, a resume that skips and re-runs, an
       ``--only`` into a matching directory (whose environment must come out
       BYTE-IDENTICAL), a refusal into a mismatched one that writes nothing,
       and the override recording itself.
    6. Negative controls. Every gate is re-run with the thing it checks
       reverted, and each must FAIL.

IT COSTS AND NEEDS NOTHING: no network, no keys, no spend, no live Qdrant, no
live server, no corpus, no git history, no Docker. Every Qdrant resolution is
replaced at ``run_fingerprint._resolve_collection``, and ``run_one_patient`` --
the only function in the evaluation harness that would call a model -- is
replaced by a stand-in whose installation is ASSERTED BY IDENTITY before any
scenario runs, so a stand-in that failed to take would fail here rather than
reaching the OpenAI endpoint.

IT EXECS NOTHING. Every control is either a different INPUT to a pure function
(the natural control for one) or an attribute rebind inside try/finally with
the restore asserted BY IDENTITY -- ``tests/test_evaluation_rater.py`` section
7j's shape. So it needs no ``_EXEC_ALLOWLIST`` entry.

NOT IN THE COLLISION MATRIX, derived rather than assumed: everything it writes
is inside a fresh ``tempfile.mkdtemp()`` and it patches no file in the
repository. SECTION 1b DOES READ REPOSITORY SOURCE -- that sentence read "the
repository files it READS are none" until the renderer-digest pass, and it is
corrected rather than left standing. What it reads is exactly
``run_fingerprint.RENDERER_MODULES`` (``oncotriage/agent/patient.py``,
``oncotriage/agent/prompts.py``, ``oncotriage/constants.py``,
``oncotriage/extraction/stage.py``, ``oncotriage/utils.py``) plus the two
consumer banners, and NOT ``oncotriage/config.py``: the closure records an
excluded module by name from the import statement and never descends into it,
so neither of the two files the suite's writers touch
(``oncotriage/registries/cancer_code_registry.py``, ``oncotriage/config.py``)
is opened. ``config.MATCHING_MODEL`` and ``config.DATA_SNAPSHOT_DATE`` are
rebound as ATTRIBUTES in memory and restored, which touches no file at all. The
renderer edit in 1b goes into a ``shutil.copy2`` COPY under the scratch
directory reached through ``_package_dir()``, and every hashed module's sha256
is compared before and after to say so.

Run from terminal:
    python tests/test_resume_configuration_fingerprint.py

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

import contextlib
import hashlib
import io
import json
import shutil
import tempfile
from pathlib import Path

from oncotriage import config as _config
from oncotriage import paths as _paths
from oncotriage import run_fingerprint as _fp
from oncotriage import utils as _utils
from oncotriage.ablation import study as _study
from oncotriage.batch import runner as _runner
from oncotriage.evaluation import run_harness as _rh


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


def check_true(label: str, actual) -> None:
    check(label, bool(actual), True)


def raises(fn):
    """``(type name, message)`` for a call that must raise, else ``(None, "")``.

    EVERY CALL INTO PRODUCTION CODE IN THIS FILE GOES THROUGH THIS OR THROUGH
    ``drive``. A bare call inside a ``check(...)`` argument list lets a planted
    or genuine exception escape while the argument is being EVALUATED, which
    kills the run and reports one traceback where it owed a summary. This
    project has shipped that shape four times; it is not shipping it again.
    """
    try:
        fn()
    except BaseException as exc:        # noqa: BLE001 -- the type is the answer
        return type(exc).__name__, str(exc)
    return None, ""


def drive(fn, default="<raised>"):
    """Call ``fn``; return its value, or a marker naming what it raised."""
    try:
        return fn()
    except BaseException as exc:        # noqa: BLE001 -- recorded, not raised
        return f"{default} {type(exc).__name__}: {exc}"


@contextlib.contextmanager
def rebound(module, name, replacement):
    """Rebind one module attribute, restore it, and ASSERT the restore.

    An attribute rebind rather than a patched source, so this file execs
    nothing. The restore is checked BY IDENTITY inside the manager rather than
    hoped for, because a control that leaves a stub installed poisons every
    check after it and looks like a cascade of unrelated failures.
    """
    original = getattr(module, name)
    setattr(module, name, replacement)
    try:
        yield
    finally:
        setattr(module, name, original)
        check_true(f"[restore] {module.__name__}.{name} is the original object",
                   getattr(module, name) is original)


def digest(path):
    """sha256 of a file, or the string 'absent'."""
    if not os.path.exists(path):
        return "absent"
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(obj):
    """A stable byte string for a JSON-able object, for a byte-identity claim."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")


def read_json(path):
    with io.open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, payload):
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


_SCRATCH = tempfile.mkdtemp(prefix="oncotriage-fingerprint-")


#------------------------------------------------------------------------------


# ===========================================================================
# A CONTROLLED FINGERPRINT
# ===========================================================================
#
# THE SEAM IS `_resolve_collection`, NOT `current`. Replacing `current` would
# leave the shipped function -- the cache, the lock, the copy, the UNKNOWN
# handling -- untested, and every check below would be about the stand-in. This
# replaces only the two live Qdrant round trips and drives the real one.

_COLLECTION = {"name": "trial_criteria_20260807_111807", "points": 12067}


def _stub_resolve():
    return _COLLECTION["name"], _COLLECTION["points"]


_REAL_RESOLVE = _fp._resolve_collection
_fp._resolve_collection = _stub_resolve

print()
print("=" * 70)
print("SECTION 0  the stand-in is installed, asserted before anything uses it")
print("=" * 70)

check_true("the collection resolver in force IS this file's stand-in "
           "(non-degeneracy: without this every check below is about the real "
           "one and would make live Qdrant calls)",
           _fp._resolve_collection is _stub_resolve)
check_true("...and it is not the shipped one",
           _fp._resolve_collection is not _REAL_RESOLVE)


def fingerprint_now():
    """A freshly resolved stamp under whatever is currently configured."""
    _fp.clear_cache()
    return _fp.current()


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1  the stamp itself
# ===========================================================================

print()
print("=" * 70)
print("SECTION 1  oncotriage/run_fingerprint.py")
print("=" * 70)

_fp.clear_cache()
_one = _fp.current()

check("current() carries the version plus exactly FINGERPRINT_FIELDS",
      sorted(_one), sorted(("fingerprint_version",) + _fp.FINGERPRINT_FIELDS))
check("...and the collection is the resolved backing name, not the alias",
      _one["qdrant_collection"], _COLLECTION["name"])
check_true("...which is NOT config.COLLECTION_NAME (non-degeneracy: a gate on "
           "the alias is a gate that can never fire)",
           _one["qdrant_collection"] != _config.COLLECTION_NAME)
check("...and the point count came from the probe of THAT name",
      _one["collection_points"], _COLLECTION["points"])
check("...and the model is the configured one",
      _one["matching_model_configured"], _config.MATCHING_MODEL)
check("...and the snapshot date is the configured one",
      _one["data_snapshot_date"], _config.DATA_SNAPSHOT_DATE)

check_true("a fully resolved stamp reports is_resolved", _fp.is_resolved(_one))

# --- the cache -----------------------------------------------------------
_COLLECTION["name"] = "trial_criteria_CHANGED_MIDRUN"
_two = _fp.current()
check("the stamp is CACHED: a resolver that changed mid-run does not move it "
      "(a run is one configuration)",
      _two["qdrant_collection"], "trial_criteria_20260807_111807")
_three = _fp.current(refresh=True)
check("...and refresh=True does resolve again",
      _three["qdrant_collection"], "trial_criteria_CHANGED_MIDRUN")
_fp.clear_cache()
check("...and clear_cache() drops it too",
      _fp.current()["qdrant_collection"], "trial_criteria_CHANGED_MIDRUN")
_COLLECTION["name"] = "trial_criteria_20260807_111807"
_fp.clear_cache()

_a = _fp.current()
_a["qdrant_collection"] = "mutated by a caller"
check("current() returns a COPY: mutating it does not reach the cache",
      _fp.current()["qdrant_collection"], "trial_criteria_20260807_111807")

# --- the five outcomes, each driven ---------------------------------------
_base = _fp.current()

check("compare(itself) is FP_MATCH", _fp.compare(_base, _base)[0], _fp.FP_MATCH)

for _field, _value in (("llm_classifier_prompt_version", "0.0.1-other"),
                       ("matching_model_configured", "some-other-model"),
                       ("qdrant_collection", "trial_criteria_20260101_000000"),
                       ("collection_points", 11999),
                       ("data_snapshot_date", "2020-01-01")):
    _stale = dict(_base)
    _stale[_field] = _value
    _outcome, _detail = _fp.compare(_stale, _base)
    check(f"a different {_field} is FP_CHANGED", _outcome, _fp.FP_CHANGED)
    check_true(f"...and the refusal names the field", _field in _detail)
    check_true(f"...and both values", repr(_value) in _detail
               and repr(_base[_field]) in _detail)

check("no stamp at all is FP_ABSENT (unknown provenance, never a pass)",
      _fp.compare(None, _base)[0], _fp.FP_ABSENT)
check("an empty stamp is FP_ABSENT", _fp.compare({}, _base)[0], _fp.FP_ABSENT)
check("a stamp with no fingerprint_version is FP_ABSENT -- the legacy case",
      _fp.compare({k: _base[k] for k in _fp.FINGERPRINT_FIELDS}, _base)[0],
      _fp.FP_ABSENT)
check_true("...and the two FP_ABSENT details are DIFFERENT text: 'no stamp at "
           "all' and 'a stamp with no version' are different findings with "
           "different histories",
           _fp.compare(None, _base)[1]
           != _fp.compare({k: _base[k] for k in _fp.FINGERPRINT_FIELDS},
                          _base)[1])
check_true("...and it says so rather than blaming a field",
           "before configuration fingerprinting existed"
           in _fp.compare({k: _base[k] for k in _fp.FINGERPRINT_FIELDS},
                          _base)[1])

_future = dict(_base, fingerprint_version=_fp.FINGERPRINT_VERSION + 1)
check("a different fingerprint_version is FP_VERSION, not FP_CHANGED",
      _fp.compare(_future, _base)[0], _fp.FP_VERSION)

_unres = dict(_base, qdrant_collection=_fp.UNKNOWN)
check("a current stamp carrying UNKNOWN is FP_UNRESOLVED",
      _fp.compare(_base, _unres)[0], _fp.FP_UNRESOLVED)
check_true("...and it is asked FIRST, so a matching stored stamp does not "
           "make it look like a match",
           _fp.compare(_unres, _unres)[0] == _fp.FP_UNRESOLVED)
check_true("...and is_resolved says why", not _fp.is_resolved(_unres))

check("every outcome compare() can answer is in the closed FP_OUTCOMES",
      sorted({_fp.compare(x, y)[0]
              for x, y in ((_base, _base), (None, _base), (_future, _base),
                           (dict(_base, data_snapshot_date="2000-01-01"), _base),
                           (_base, _unres))}),
      sorted({_fp.FP_MATCH, _fp.FP_ABSENT, _fp.FP_VERSION, _fp.FP_CHANGED,
              _fp.FP_UNRESOLVED}))

# --- a missing key is a disagreement, not a pass --------------------------
_partial = {k: v for k, v in _base.items() if k != "collection_points"}
_lines = _fp.disagreements(_partial, _base)
check("a field the stored stamp does not carry is ONE disagreement",
      len(_lines), 1)
check_true("...printed as <not recorded> rather than as a value",
           _fp.NOT_RECORDED in _lines[0])
check("disagreements(None, ...) reports every field rather than raising",
      len(_fp.disagreements(None, _base)), len(_fp.FINGERPRINT_FIELDS))
check("disagreements over a non-dict does not raise either",
      len(_fp.disagreements("not a dict", _base)), len(_fp.FINGERPRINT_FIELDS))

# --- the degradation path -------------------------------------------------
_fp.FINGERPRINT_DEGRADATIONS.clear()
with rebound(_fp, "_resolve_collection", lambda: (_fp.UNKNOWN, _fp.UNKNOWN)):
    _bad = fingerprint_now()
    check("an unresolvable collection produces UNKNOWN and does not raise",
          _bad["qdrant_collection"], _fp.UNKNOWN)
    check("...and never a plausible-looking zero for the point count",
          _bad["collection_points"], _fp.UNKNOWN)
_fp.clear_cache()

# The counter is driven through the REAL _resolve_collection, with the two
# calls inside it made to fail, so the shipped keys are the ones exercised.
_fp.FINGERPRINT_DEGRADATIONS.clear()


def _boom():
    raise ConnectionError("no route to host")


with rebound(_utils, "resolve_qdrant_collection", _boom):
    with rebound(_fp, "_resolve_collection", _REAL_RESOLVE):
        _bad2 = fingerprint_now()
check("a client that cannot be built degrades to UNKNOWN",
      _bad2["qdrant_collection"], _fp.UNKNOWN)
check("...and is counted under the field and the exception type",
      dict(_fp.FINGERPRINT_DEGRADATIONS),
      {"qdrant_collection:ConnectionError": 1})
_fp.FINGERPRINT_DEGRADATIONS.clear()
_fp.clear_cache()

check_true("the counter is in the degradation registry, so a run reports it",
           "FINGERPRINT_DEGRADATIONS"
           in __import__("oncotriage.degradation", fromlist=["x"])
           .registered_names())

# --- the shared refusal text ---------------------------------------------
_r = _fp.refusal_lines(_fp.FP_CHANGED, "a: 1 -> 2", "the thing", ["do X"])
check_true("a refusal names the outcome", _fp.FP_CHANGED in _r[0])
check_true("...and the artifact", "the thing" in _r[0])
check_true("...and the caller's own remediation",
           any("do X" in line for line in _r))
check_true("...and states the collection gate's limit when fields were compared",
           any(_fp.COLLECTION_IDENTITY in line for line in _r))
check_true("...and does NOT state it when no comparison happened",
           not any(_fp.COLLECTION_IDENTITY in line for line
                   in _fp.refusal_lines(_fp.FP_ABSENT, "d", "a", ["r"])))
check_true("...nor for FP_VERSION, which refuses BEFORE any field is compared",
           not any(_fp.COLLECTION_IDENTITY in line for line
                   in _fp.refusal_lines(_fp.FP_VERSION, "d", "a", ["r"])))
check_true("...nor for FP_UNRESOLVED",
           not any(_fp.COLLECTION_IDENTITY in line for line
                   in _fp.refusal_lines(_fp.FP_UNRESOLVED, "d", "a", ["r"])))




#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1b  the renderer digest
# ===========================================================================
#
# THE GAP THIS CLOSES. `llm_classifier_prompt_version` is hand-maintained and
# oncotriage/agent/prompts.py says so; the convention is that a renderer change
# bumps it, and nothing enforced the convention. An edit to
# `_create_patient_summary`, to a temporal helper or to the stage extractor that
# forgot the bump changed every rendered prompt while the resume gate reported
# FP_MATCH, and the resumed batch / ablation / evaluation run mixed two eras
# into one artifact.
#
# THIS SECTION READS REPOSITORY SOURCE, WHICH THE REST OF THIS FILE DOES NOT.
# The five modules it reads are oncotriage/run_fingerprint.py's RENDERER_MODULES
# and none of them is written by either of the suite's two writers
# (oncotriage/registries/cancer_code_registry.py, oncotriage/config.py), so the
# collision-matrix derivation is unchanged -- and the closure below DOES NOT
# DESCEND into an excluded module, which is what keeps oncotriage/config.py
# unopened rather than merely unhashed.

print()
print("=" * 70)
print("SECTION 1b  llm_classifier_renderer_digest")
print("=" * 70)

import ast as _ast                                            # noqa: E402

_PKG_ROOT = os.path.dirname(os.path.abspath(_fp.__file__))
_DIGEST = "llm_classifier_renderer_digest"


def _rel_path(rel):
    return os.path.join(_PKG_ROOT, *rel.split("/"))


def at(mapping, key=_DIGEST):
    """``mapping[key]``, or a marker naming the absence. NEVER raises.

    EVERY read of the digest out of a stamp in this section goes through this.
    A bare ``stamp["llm_classifier_renderer_digest"]`` raises KeyError while
    ``check()``'s argument is being EVALUATED when the field is gone -- which
    is exactly the defect the section exists to catch -- so the run reported
    one traceback where it owed a summary and eight recorded failures. This
    project has shipped that shape eight times; the revert harness caught it
    here on the FIRST plant, which is the argument for controls restated as an
    event.
    """
    if not isinstance(mapping, dict) or key not in mapping:
        return f"<no {key}>"
    return mapping[key]


def stamp_now(refresh=False):
    """``current()``, or a marker naming what it raised. NEVER raises.

    ``current()`` reads five files. A revert that makes an unreadable renderer
    module RAISE instead of degrading is precisely what section (g) asserts
    against, and a bare call there killed the run instead of recording it.
    """
    return drive(lambda: _fp.current(refresh=refresh), default="<current raised>")


# --- (a) the field is in the stamp and in the gate ------------------------
_fp.clear_cache()
_now = _fp.current()

check_true("the renderer digest is a GATED field, not merely a recorded one",
           "llm_classifier_renderer_digest" in _fp.FINGERPRINT_FIELDS)
check("...and current() carries it", type(at(_now)), str)
check("...as a full sha256 hex digest",
      (len(str(at(_now))),
       set(str(at(_now))) <= set("0123456789abcdef")),
      (64, True))
check("...and it is NOT the UNKNOWN sentinel (non-degeneracy: an unreadable "
      "module answers UNKNOWN, and every check below would then be comparing "
      "one sentinel with another)",
      at(_now) == _fp.UNKNOWN, False)
check("the stamp's version says this field set is version 5. It was 2 until "
      "`matching_call_mode` was gated, 3 until the cohort pass gated "
      "`campaign_cohort_size` and `campaign_cohort_seed`, and 4 until the "
      "environment-record pass gated `cross_encoder_revision`; each bump is "
      "what makes every older artifact answer FP_VERSION once rather than have "
      "its missing field compared against a live value",
      _fp.FINGERPRINT_VERSION, 5)

# --- (b) the module set is DERIVED, not trusted ---------------------------
# A static closure from the two render entry points over every module-level
# name each reaches, transitively. A module it reaches must be in
# RENDERER_MODULES or in RENDERER_MODULES_EXCLUDED -- the round trip is CLOSED,
# so a helper moved to a new module fails here rather than silently escaping
# the digest, which is the one rot a hand-written module list is prone to.


def _package_module_exists(dotted):
    return os.path.exists(os.path.join(
        os.path.dirname(_PKG_ROOT), *dotted.split(".")) + ".py")


def _dotted_to_rel(dotted):
    return dotted[len("oncotriage."):].replace(".", "/") + ".py"


_INDEX_CACHE = {}


def _index(rel):
    """Module-level defs, constants, from-imported names and module aliases."""
    if rel in _INDEX_CACHE:
        return _INDEX_CACHE[rel]
    with io.open(_rel_path(rel), "r", encoding="utf-8") as fh:
        tree = _ast.parse(fh.read())
    defs, consts, names, mods = {}, {}, {}, {}
    for node in tree.body:
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                             _ast.ClassDef)):
            defs[node.name] = node
        elif isinstance(node, _ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, _ast.Name):
                    consts[tgt.id] = node
        elif isinstance(node, _ast.AnnAssign) and isinstance(node.target,
                                                             _ast.Name):
            consts[node.target.id] = node
        elif isinstance(node, _ast.ImportFrom) and node.module \
                and node.module.startswith("oncotriage"):
            for alias in node.names:
                # `from oncotriage.agent import deps` binds a MODULE, not a
                # name in oncotriage/agent.py -- which does not exist. Told
                # apart by asking the filesystem, not by guessing from the
                # spelling.
                sub = f"{node.module}.{alias.name}"
                if _package_module_exists(sub):
                    mods[alias.asname or alias.name] = sub
                else:
                    names[alias.asname or alias.name] = (node.module, alias.name)
        elif isinstance(node, _ast.Import):
            for alias in node.names:
                if alias.name.startswith("oncotriage") \
                        and _package_module_exists(alias.name):
                    mods[alias.asname or alias.name.split(".")[-1]] = alias.name
    _INDEX_CACHE[rel] = (defs, consts, names, mods)
    return _INDEX_CACHE[rel]


_reached = set()
_walked = set()


def _walk(dotted, name):
    rel = _dotted_to_rel(dotted)
    _reached.add(rel)
    if rel not in _fp.RENDERER_MODULES:
        # Record it, do not descend. Excluding a module excludes its subtree --
        # which is what keeps oncotriage/config.py, a file one of the suite's
        # two writers rewrites in place, from being opened by this test at all.
        return
    if (rel, name) in _walked:
        return
    _walked.add((rel, name))
    defs, consts, names, mods = _index(rel)
    node = defs.get(name) or consts.get(name)
    if node is None:
        return
    for sub in _ast.walk(node):
        if isinstance(sub, _ast.Attribute) and isinstance(sub.value, _ast.Name) \
                and sub.value.id in mods:
            _walk(mods[sub.value.id], sub.attr)
        elif isinstance(sub, _ast.Name) and isinstance(sub.ctx, _ast.Load):
            if sub.id in defs or sub.id in consts:
                _walk(dotted, sub.id)
            elif sub.id in names:
                _walk(*names[sub.id])
            elif sub.id in mods:
                _reached.add(_dotted_to_rel(mods[sub.id]))


_walk("oncotriage.agent.patient", "_create_patient_summary")
_walk("oncotriage.agent.prompts", "render_system_prompt")

check_true("the closure is non-degenerate: it walked into more than one "
           "definition (a walker that resolved nothing would satisfy every "
           "check below for free)", len(_walked) > 30)
check("every module the render-path closure reaches is DECLARED -- hashed or "
      "argued-excluded, never neither",
      sorted(_reached - set(_fp.RENDERER_MODULES)
             - set(_fp.RENDERER_MODULES_EXCLUDED)), [])
check("...and every HASHED module is one the closure actually reaches "
      "(non-degeneracy the other way: a module in the tuple that nothing on "
      "the render path touches is a digest that refuses for no reason)",
      sorted(set(_fp.RENDERER_MODULES) - _reached), [])
check("...and the two tuples are disjoint",
      sorted(set(_fp.RENDERER_MODULES) & set(_fp.RENDERER_MODULES_EXCLUDED)),
      [])
check("the closure reaches exactly the declared set, both tuples",
      sorted(_reached),
      sorted(set(_fp.RENDERER_MODULES) | set(_fp.RENDERER_MODULES_EXCLUDED)))
check_true("...and oncotriage/config.py is excluded rather than hashed, so "
           "the collision-matrix derivation for this file still holds",
           "config.py" in _fp.RENDERER_MODULES_EXCLUDED
           and "config.py" not in _fp.RENDERER_MODULES)

# The premise that lets docstrings be stripped: nothing on the render path
# reads a __doc__. Asserted rather than assumed, over the hashed set.
_doc_readers = sorted(
    rel for rel in _fp.RENDERER_MODULES
    if any(isinstance(n, _ast.Attribute) and n.attr == "__doc__"
           for n in _ast.walk(_ast.parse(
               io.open(_rel_path(rel), encoding="utf-8").read()))))
check("no hashed module reads a __doc__, which is what makes stripping "
      "docstrings from the digest input a reduction and not a hole",
      _doc_readers, [])

# --- (c) what the digest does and does not see ----------------------------
_probe_dir = tempfile.mkdtemp(dir=_SCRATCH)
_probe = os.path.join(_probe_dir, "probe.py")


def _normalized(text):
    with io.open(_probe, "w", encoding="utf-8") as fh:
        fh.write(text)
    return _fp.normalized_module_source(_probe)


_BASE_SRC = ('"""A docstring."""\n'
             'X = 1          # a comment\n'
             'def f(a):\n'
             '    """Another docstring."""\n'
             '    return a + X\n')
check("a COMMENT-only edit does not change the normalized source -- which is "
      "why a documentation pass over patient.py does not refuse every resume",
      _normalized(_BASE_SRC),
      _normalized(_BASE_SRC.replace("# a comment", "# a different comment")))
check("...nor does a DOCSTRING edit",
      _normalized(_BASE_SRC),
      _normalized(_BASE_SRC.replace("Another docstring", "Rewritten entirely")))
check("...nor does reformatting",
      _normalized(_BASE_SRC), _normalized(_BASE_SRC.replace("X = 1  ", "X=1")))
check_true("...but a one-character EXECUTABLE edit does",
           _normalized(_BASE_SRC)
           != _normalized(_BASE_SRC.replace("a + X", "a - X")))
check_true("...and so does a string LITERAL edit, which is how every section "
           "heading in the summary is spelled",
           _normalized(_BASE_SRC)
           != _normalized(_BASE_SRC.replace("X = 1", 'X = "1"')))
check("a function whose body is only a docstring still unparses",
      _normalized('def g():\n    """only this."""\n'), "def g():\n    pass")

# --- (d) the digest itself ------------------------------------------------
_d1 = _fp.renderer_digest()
check("renderer_digest() is deterministic within a process",
      _d1, _fp.renderer_digest())
_per = _fp.renderer_module_digests()
check("renderer_module_digests() covers exactly RENDERER_MODULES",
      sorted(_per), sorted(_fp.RENDERER_MODULES))
check_true("...and every per-module digest is distinct (non-degeneracy: five "
           "equal digests would mean the reader is not reading five files)",
           len(set(_per.values())) == len(_fp.RENDERER_MODULES))
check("the stamp's digest IS renderer_digest()", at(_now), _d1)
# The digest is a pure function of (algorithm tag, path, normalized source) --
# demonstrated by recomputing it here from the parts and requiring agreement.
_recomputed = hashlib.sha256("\n".join(
    [_fp.RENDERER_DIGEST_ALGORITHM]
    + [f"{rel}:{_per[rel]}" for rel in _fp.RENDERER_MODULES]
).encode("utf-8")).hexdigest()
check("the digest is exactly sha256 over the tag and the path:hex lines, in "
      "RENDERER_MODULES order", _d1, _recomputed)
check_true("...and the path is part of it, so moving a module moves the digest "
           "even with its text untouched",
           hashlib.sha256("\n".join(
               [_fp.RENDERER_DIGEST_ALGORITHM]
               + [f"moved/{rel}:{_per[rel]}" for rel in _fp.RENDERER_MODULES]
           ).encode("utf-8")).hexdigest() != _d1)
check_true("...and so is the ALGORITHM TAG, so a change to the normalisation "
           "cannot silently re-base what two runs are comparing",
           hashlib.sha256("\n".join(
               [_fp.RENDERER_DIGEST_ALGORITHM + "-changed"]
               + [f"{rel}:{_per[rel]}" for rel in _fp.RENDERER_MODULES]
           ).encode("utf-8")).hexdigest() != _d1)
check_true("...and the ORDER is RENDERER_MODULES', not an accident of dict "
           "iteration", tuple(_per) == tuple(_fp.RENDERER_MODULES))

# --- (e) THE GAP, CLOSED: a renderer edit with NO version bump ------------
# The whole pass in one check. The seam is _package_dir(): pointing it at a
# COPY of the package makes the shipped renderer_digest() read the copy, so
# nothing in the repository is written and the real modules are never touched.
_copy_root = os.path.join(tempfile.mkdtemp(dir=_SCRATCH), "oncotriage")
os.makedirs(_copy_root)
for _rel in _fp.RENDERER_MODULES:
    _dst = os.path.join(_copy_root, *_rel.split("/"))
    os.makedirs(os.path.dirname(_dst), exist_ok=True)
    shutil.copy2(_rel_path(_rel), _dst)

_REPO_HASHES_BEFORE = {rel: digest(_rel_path(rel))
                       for rel in _fp.RENDERER_MODULES}

with rebound(_fp, "_package_dir", lambda: _copy_root):
    check("pointing _package_dir at an untouched COPY reproduces the digest "
          "exactly (non-degeneracy: without this, the change below could be "
          "the copy rather than the edit)", _fp.renderer_digest(), _d1)

    _fp.clear_cache()
    _pre_edit_stamp = stamp_now()

    # ONE CHARACTER, in the renderer, with no PROMPT_VERSION bump: the "- None"
    # a patient with no procedures renders becomes "- none".
    _patient_copy = os.path.join(_copy_root, "agent", "patient.py")
    _txt = io.open(_patient_copy, encoding="utf-8").read()
    check("the marker this edit moves occurs exactly where expected "
          "(non-degeneracy: an edit that matched nothing would leave the "
          "digest equal and look like a gate that failed to fire)",
          _txt.count('summary += "\\nProcedures:\\n"'), 1)
    io.open(_patient_copy, "w", encoding="utf-8").write(
        _txt.replace('summary += "\\nProcedures:\\n"',
                     'summary += "\\nprocedures:\\n"'))

    _fp.clear_cache()
    _post_edit_stamp = stamp_now()

    check_true("A ONE-CHARACTER RENDERER EDIT WITH NO VERSION BUMP MOVES THE "
               "DIGEST -- the gap this field exists to close",
               at(_post_edit_stamp) != at(_pre_edit_stamp))
    check("...while llm_classifier_prompt_version is UNCHANGED, which is "
          "precisely the case the old five-field gate could not see",
          at(_post_edit_stamp, "llm_classifier_prompt_version"),
          at(_pre_edit_stamp, "llm_classifier_prompt_version"))
    check("...and every other gated field is unchanged too, so the refusal "
          "cannot be blamed on anything else",
          [f for f in _fp.FINGERPRINT_FIELDS
           if at(_post_edit_stamp, f) != at(_pre_edit_stamp, f)],
          [_DIGEST])

    _outcome, _detail = _fp.compare(_pre_edit_stamp, _post_edit_stamp)
    check("compare() against the pre-edit stamp answers FP_CHANGED",
          _outcome, _fp.FP_CHANGED)
    check_true("...and NAMES the field", "llm_classifier_renderer_digest"
               in _detail)
    check_true("...and the refusal states what the renderer gate does not "
               "cover, on COLLECTION_IDENTITY's argument",
               any(_fp.RENDERER_COVERAGE in line for line in
                   _fp.refusal_lines(_outcome, _detail, "an artifact", ["fix"])))
    check_true("...and renderer_module_digests() says WHICH module moved",
               _fp.renderer_module_digests()["agent/patient.py"]
               != _per["agent/patient.py"])
    check("...and only that one",
          sorted(rel for rel in _fp.RENDERER_MODULES
                 if _fp.renderer_module_digests()[rel] != _per[rel]),
          ["agent/patient.py"])

    # A comment-only edit to the SAME copied module must NOT move it.
    io.open(_patient_copy, "w", encoding="utf-8").write(_txt)      # restore
    _fp.clear_cache()
    check("restoring the copy restores the digest",
          at(stamp_now()), at(_pre_edit_stamp))
    io.open(_patient_copy, "w", encoding="utf-8").write(
        _txt.replace("# ── Procedures ─",
                     "# ── Procedures (a documentation pass) ─"))
    _fp.clear_cache()
    check("a comment-only edit to the real renderer does NOT move the digest, "
          "so a documentation pass does not refuse every resume",
          at(stamp_now()), at(_pre_edit_stamp))

_fp.clear_cache()
check("the repository's own renderer modules were never written",
      {rel: digest(_rel_path(rel)) for rel in _fp.RENDERER_MODULES},
      _REPO_HASHES_BEFORE)
check("...and the shipped digest is back to what it was before the copy",
      at(stamp_now()), _d1)

# --- (f) a version-1 stamp is FP_VERSION, never FP_CHANGED ----------------
# The one behaviour change this pass ships, driven rather than described.
_v1 = {k: v for k, v in _now.items() if k != "llm_classifier_renderer_digest"}
_v1["fingerprint_version"] = 1
_v1_outcome, _v1_detail = _fp.compare(_v1, _now)
check("EVERY ARTIFACT STAMPED AT VERSION 1 ANSWERS FP_VERSION -- the shape "
      "changed, and a field it never recorded must not be reported as a "
      "configuration that changed", _v1_outcome, _fp.FP_VERSION)
check_true("...and it is NOT FP_CHANGED", _v1_outcome != _fp.FP_CHANGED)
check_true("...and the detail names both versions",
           "1" in _v1_detail and str(_fp.FINGERPRINT_VERSION) in _v1_detail)
check_true("...and the refusal tells an operator this is expected once after a "
           "bump, rather than leaving them hunting an edit that never happened",
           any("first contact" in line for line in
               _fp.refusal_lines(_v1_outcome, _v1_detail, "an artifact",
                                 ["clear it"])))
check_true("...and that clause is NOT printed for FP_CHANGED, which is a real "
           "configuration difference",
           not any("first contact" in line for line in
                   _fp.refusal_lines(_fp.FP_CHANGED, "d", "a", ["r"])))

# A v1 stamp that ALSO differs in a field is still FP_VERSION: the shape is
# asked before any field, so the answer cannot be a field diff computed across
# two different field sets.
check("a version-1 stamp that also differs in a gated field is STILL "
      "FP_VERSION, because the shape is asked first",
      _fp.compare(dict(_v1, matching_model_configured="something-else"),
                  _now)[0], _fp.FP_VERSION)

# --- (f-newer) A STAMP FROM THE FUTURE IS THE OPPOSITE REMEDY -------------
#
# BOTH DIRECTIONS OF THE SAME OUTCOME, DRIVEN. Until this pass every
# version mismatch got one message, and that message told an operator to clear
# the artifact -- correct for an OLDER stamp, and exactly wrong for a NEWER
# one, where the artifact is fine and THIS BUILD IS BEHIND IT. A checkout
# rolled back one commit, a container running last week's image against this
# week's checkpoint, two machines at different revisions sharing a volume: all
# three land here, and `--fresh` would discard hours of paid work a newer build
# can still continue.
#
# THE OUTCOME IS STILL FP_VERSION, deliberately. The fields are equally
# uncomparable in both directions, and a sixth member of a closed vocabulary
# that `_refuse_checkpoint` keys a counter on and `run_harness` branches on
# would be a change every consumer has to learn for a difference that is
# entirely in the remedy. THE STORAGE LAYER MAKES THE SAME CALL one layer down:
# `initialize_database` refuses to LOWER a `PRAGMA user_version` it finds ahead
# of its own and says so, leaving the file alone.
_newer = dict(_now)
_newer["fingerprint_version"] = _fp.FINGERPRINT_VERSION + 1
_nw_outcome, _nw_detail = _fp.compare(_newer, _now)
check("A STAMP FROM A NEWER BUILD IS FP_VERSION, exactly as an older one is: "
      "the fields cannot be compared in either direction",
      _nw_outcome, _fp.FP_VERSION)
check_true("...and the detail says NEWER, in as many words, so the direction "
           "is in the first line an operator reads",
           "NEWER" in _nw_detail)
check_true("...and names the remedy as checking out the version that wrote it",
           "Check out the version that wrote it" in _nw_detail)
check_true("...and says NOT to discard the artifact, which is what every "
           "caller's own remediation below it would do",
           "do NOT discard" in _nw_detail
           or "DO NOT" in _nw_detail.upper())
check_true("...and states that nothing is wrong with the artifact -- the "
           "storage layer's own sentence for the same situation",
           "NOTHING IS WRONG WITH THE ARTIFACT" in _nw_detail)
# THE OTHER DIRECTION, ON THE SAME PROBE, so this is a measurement rather than
# a statement about one string: an OLDER stamp must NOT get any of it.
check_true("an OLDER stamp gets none of that: it says neither NEWER nor "
           "'check out'",
           not any(s in _v1_detail for s in ("NEWER", "Check out")))
check_true("...and the older direction still tells the operator that clearing "
           "once is the whole remediation",
           any("first contact" in line for line in
               _fp.refusal_lines(_v1_outcome, _v1_detail, "an artifact",
                                 ["clear it"], recorded=_v1)))

# THE REFUSAL WARNS ABOVE THE CALLER'S OWN REMEDIATION, which is the half the
# detail cannot do: those lines are the caller's -- "--fresh",
# "--fresh-start", "point --output-dir elsewhere" -- and `refusal_lines`
# appends them. Passing the stored stamp is what lets the warning be printed
# first.
_nw_lines = _fp.refusal_lines(_nw_outcome, _nw_detail, "the checkpoint",
                              ["    python \"25- Batch Runner.py\" --fresh"],
                              recorded=_newer)
check_true("the refusal tells the operator NOT to run the commands below it",
           any("DO NOT RUN THE COMMANDS BELOW" in line for line in _nw_lines))
def _line_index(lines, needle):
    """Where `needle` first appears, or None. NEVER RAISES.

    A bare `[...][0]` raises IndexError EXACTLY when the warning is missing --
    which is the defect the check exists to catch -- so the file would print one
    traceback where it owes a named failure. Measured: reverting the branch in a
    copy aborted this file until this existed.
    """
    for index, line in enumerate(lines):
        if needle in line:
            return index
    return None


_I_WARN = _line_index(_nw_lines, "DO NOT RUN THE COMMANDS BELOW")
_I_REM = _line_index(_nw_lines, "--fresh")
check("...and that warning is printed ABOVE them, not after",
      (None not in (_I_WARN, _I_REM)) and _I_WARN < _I_REM,
      True)
check_true("...and the 'clearing once is the whole remediation' clause is NOT "
           "printed for a newer stamp, because it is false of one",
           not any("first contact" in line for line in _nw_lines))
check_true("...and WITHOUT the stored stamp the older-direction clause is used "
           "-- the pre-existing behaviour, kept as the default so no caller "
           "that does not know the direction is broken",
           any("first contact" in line for line in
               _fp.refusal_lines(_nw_outcome, _nw_detail, "the checkpoint",
                                 ["x"])))

# THE COMPARISON IS GUARDED, because the version comes out of a JSON file a
# corrupt write or another tool may have produced. `"4" > 3` is a TypeError
# raised out of the one function whose job is to decide whether a refusal is
# safe.
check("a non-integer version is NOT read as newer; it falls through to the "
      "ordinary mismatch, which is true of any unreadable value",
      [_fp.recorded_version_is_newer(dict(_now, fingerprint_version=v))
       for v in ("4", [4], 4.5, None, True, _fp.FINGERPRINT_VERSION,
                 _fp.FINGERPRINT_VERSION - 1, _fp.FINGERPRINT_VERSION + 1)],
      [False, False, False, False, False, False, False, True])
check("...and compare() answers FP_VERSION without RAISING for every one of "
      "them, which is the property the guard exists for: an unreadable version "
      "must not turn a refusal into a TypeError out of the function deciding "
      "whether the refusal is safe",
      sorted({drive(lambda v=v: _fp.compare(
                  dict(_now, fingerprint_version=v), _now)[0])
              for v in ("4", [4], 4.5, True, 99)}),
      [_fp.FP_VERSION])
check("a non-dict `recorded` is not newer either -- refusal_lines is called "
      "with whatever the caller had, including None",
      [_fp.recorded_version_is_newer(x)
       for x in (None, "", [], 3, {"fingerprint_version": 3})],
      [False, False, False, False, False])


# --- (f2) A STAMP SHORT OF A GATED FIELD IS NOT A STAMP THAT AGREES -------
# Found by the revert harness rather than by reading, and it is a property of
# is_resolved() rather than of the digest -- the digest is only what surfaced
# it. `is_resolved` used to ask `.get(f) != UNKNOWN`; None is not UNKNOWN, so a
# stamp missing a gated field reported RESOLVED, `disagreements()` then
# compared NOT_RECORDED with NOT_RECORDED, found them EQUAL, and `compare()`
# answered FP_MATCH. A hand-built stamp missing exactly the field a version
# bump added would have been reported as AGREEING with a run that has it. The
# version gate catches that particular case one branch earlier, and a guard
# that depends on another guard running first is not a guard.
_short = {k: v for k, v in _now.items() if k != _DIGEST}
check_true("a stamp MISSING a gated field is NOT resolved -- absent is not "
           "established", not _fp.is_resolved(_short))
check_true("...non-degeneracy: the same stamp WITH the field is resolved",
           _fp.is_resolved(_now))
check("...and as THIS run's stamp it answers FP_UNRESOLVED, never FP_MATCH",
      drive(lambda: _fp.compare(_now, _short)[0]), _fp.FP_UNRESOLVED)
check_true("...naming the field that was never established",
           _DIGEST in str(drive(lambda: _fp.compare(_now, _short)[1])))
check("...and two stamps that are BOTH short do not agree with each other "
      "either, which is the case that used to come back FP_MATCH",
      drive(lambda: _fp.compare(_short, _short)[0]), _fp.FP_UNRESOLVED)
check("...and nothing raises while formatting any of it",
      drive(lambda: _fp.compare(_short, _short)[1] is not None), True)

# --- (g) an unreadable renderer module degrades, and does not raise -------
_fp.FINGERPRINT_DEGRADATIONS.clear()
with rebound(_fp, "_package_dir", lambda: os.path.join(_SCRATCH, "no-such-pkg")):
    _fp.clear_cache()
    _missing = stamp_now()
    check("a renderer module that cannot be read answers UNKNOWN rather than "
          "raising", at(_missing), _fp.UNKNOWN)
    check("...and is counted under the field and the exception type",
          dict(_fp.FINGERPRINT_DEGRADATIONS),
          {"llm_classifier_renderer_digest:FileNotFoundError": 1})
    check("...and compare() then answers FP_UNRESOLVED, whose remediation is "
          "not 'clear the artifact' at all",
          drive(lambda: _fp.compare(_now, _missing)[0]), _fp.FP_UNRESOLVED)
    check_true("...naming the field that could not be established",
               _DIGEST in str(drive(lambda: _fp.compare(_now, _missing)[1])))
_fp.FINGERPRINT_DEGRADATIONS.clear()
_fp.clear_cache()

_bad_syntax_dir = os.path.join(tempfile.mkdtemp(dir=_SCRATCH), "oncotriage")
for _rel in _fp.RENDERER_MODULES:
    _dst = os.path.join(_bad_syntax_dir, *_rel.split("/"))
    os.makedirs(os.path.dirname(_dst), exist_ok=True)
    shutil.copy2(_rel_path(_rel), _dst)
with io.open(os.path.join(_bad_syntax_dir, "constants.py"), "a",
             encoding="utf-8") as _fh:
    _fh.write("\ndef (:\n")
with rebound(_fp, "_package_dir", lambda: _bad_syntax_dir):
    _fp.clear_cache()
    check("a renderer module that cannot be PARSED degrades the same way",
          at(stamp_now()), _fp.UNKNOWN)
    check("...counted under the parse failure's own type",
          dict(_fp.FINGERPRINT_DEGRADATIONS),
          {"llm_classifier_renderer_digest:SyntaxError": 1})
_fp.FINGERPRINT_DEGRADATIONS.clear()
_fp.clear_cache()

# --- (h) the resolve-once cache still holds, with a file read in it -------
# The digest reads five files. It has to be inside the SAME cached resolution
# as the collection, or a run whose source is edited mid-flight would stamp two
# checkpoints differently and the file would then refuse itself.
_fp.clear_cache()
_cached = stamp_now()
with rebound(_fp, "renderer_digest", lambda: "0" * 64):
    check("the digest is resolved ONCE per process: a resolver that changed "
          "mid-run does not move the cached stamp",
          at(stamp_now()), at(_cached))
    check("...and refresh=True does read it again (non-degeneracy: without "
          "this the check above would pass against a stamp that never "
          "consulted the resolver at all)",
          at(stamp_now(refresh=True)), "0" * 64)
_fp.clear_cache()
check("...and clear_cache() puts the real one back", at(stamp_now()), _d1)

# --- (i) the summary sentence has ONE owner -------------------------------
check_true("summary() names the renderer digest, so the banner an operator "
           "sees at startup carries the value a later refusal would name",
           _fp.summary(_now).startswith("prompt ")
           and str(at(_now))[:12] in _fp.summary(_now))
check("...and compare()'s FP_MATCH detail IS that sentence, not a sixth copy "
      "of it", _fp.compare(_now, _now)[1], _fp.summary(_now))
check("summary() over a stamp missing every field formats rather than raising "
      "-- a diagnosis must survive the state it is diagnosing",
      drive(lambda: _fp.NOT_RECORDED in _fp.summary({})), True)

# summary() spells its six fields out, because "prompt 1.9.0, model X,
# collection Y (N points)" is a sentence a person reads and a derived
# "field=value; ..." join is not. The cost of a hand-written sentence is that
# it can fall behind the tuple -- which is the defect this pass just fixed one
# level up, in the two banners -- so the round trip is CLOSED here instead: a
# seventh gated field fails this until summary() names it.
_sentinels = {f: f"SENTINEL{i}VALUE" for i, f in enumerate(_fp.FINGERPRINT_FIELDS)}
_summ = _fp.summary(_sentinels)
check("summary() names the VALUE of every gated field, so a field added to the "
      "tuple cannot leave the banner and the FP_MATCH detail naming one fewer "
      "fact than the gate compares",
      sorted(f for f, v in _sentinels.items() if v[:12] not in _summ), [])
check_true("...non-degeneracy: a value NOT in the stamp is not in the sentence "
           "either, so the check above is not satisfied by a formatter that "
           "prints everything", "SENTINEL99VALUE"[:12] not in _summ)

_banner_sources = "\n".join(
    io.open(os.path.join(_PKG_ROOT, rel), encoding="utf-8").read()
    for rel in ("batch/runner.py", "ablation/study.py"))
check("neither consumer banner enumerates the gated fields any more: they "
      "call run_fingerprint.summary(), so a field added to the gate cannot "
      "leave a banner naming one fewer fact than the gate compares",
      _banner_sources.count("_fingerprint['llm_classifier_prompt_version']"), 0)
check_true("...and both do call it (non-degeneracy for the check above, which "
           "a deleted banner would also satisfy)",
           _banner_sources.count("run_fingerprint.summary(_fingerprint)") == 2)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1c  matching_call_mode
# ===========================================================================
#
# WHAT THIS FIELD CLOSES, and it is not a marginal one.
# `config.matching_call_mode()` decides whether Stage 5 sends ONE request
# carrying several trials or one request PER TRIAL. Before it was gated, NOT ONE
# field of this stamp moved with it -- the flag is a bool nothing else is a
# function of, and `matching_model_configured` is the same wire id in both arms
# because it is the same judge. So a grouped-mode checkpoint resumed under
# per-trial mode answered FP_MATCH, skipped every patient the grouped process
# had completed, ran the rest in the other arm, and put both into one
# `inferences` table with nothing in it saying so.
#
# THE VALUE COMES FROM THE ONE OWNER AND THAT IS ASSERTED BY BEHAVIOUR, not by
# reading the source: the flag is moved on the config MODULE and the stamp is
# required to follow it. A `from oncotriage.config import
# MATCHING_PER_TRIAL_CALLS_ENABLED` in run_fingerprint would bind the value at
# import and fail exactly this check, and a second copy of the flag-to-mode
# mapping would fail it the day the mapping changed.

print()
print("=" * 70)
print("SECTION 1c  matching_call_mode")
print("=" * 70)

check_true("it is a GATED field, not merely a recorded one -- which is what "
           "makes a resume across the two arms a refusal rather than a note",
           "matching_call_mode" in _fp.FINGERPRINT_FIELDS)

_fp.clear_cache()
_mode_now = stamp_now()
check("current() carries it, and its value is a member of the pipeline's own "
      "closed vocabulary rather than a free string",
      at(_mode_now, "matching_call_mode") in _config.MATCHING_CALL_MODES, True)
check("...and it agrees with the ONE owner, config.matching_call_mode()",
      at(_mode_now, "matching_call_mode"), _config.matching_call_mode())
check("...(non-degeneracy: the vocabulary really has two distinct members, so "
      "the checks below can tell the arms apart)",
      len(set(_config.MATCHING_CALL_MODES)), 2)
check("no arm collides with this module's UNKNOWN sentinel. A mode literally "
      "spelled 'unknown' would make a resolved stamp report itself unresolved, "
      "so every gate would answer FP_UNRESOLVED and refuse every resume for a "
      "configuration that was perfectly well established",
      [m for m in _config.MATCHING_CALL_MODES if m == _fp.UNKNOWN], [])

# --- THE STAMP FOLLOWS THE FLAG AT CALL TIME, IN BOTH DIRECTIONS ------------
#
# BOTH DIRECTIONS, because a function that ignored the flag and returned a
# constant would satisfy a one-way check. The flag is restored in a `finally`
# and the restore is asserted, on this project's standing rule for a module
# attribute rebound by a test.
_saved_flag = _config.MATCHING_PER_TRIAL_CALLS_ENABLED
try:
    _config.MATCHING_PER_TRIAL_CALLS_ENABLED = True
    _fp.clear_cache()
    _stamp_on = at(stamp_now(), "matching_call_mode")
    _config.MATCHING_PER_TRIAL_CALLS_ENABLED = False
    _fp.clear_cache()
    _stamp_off = at(stamp_now(), "matching_call_mode")
finally:
    _config.MATCHING_PER_TRIAL_CALLS_ENABLED = _saved_flag
    _fp.clear_cache()

check("the stamp reads the flag LIVE off the config module, in both "
      "directions -- so a from-import binding at run_fingerprint's own import, "
      "or a hardcoded constant, fails here",
      (_stamp_on, _stamp_off),
      (_config.MATCHING_CALL_MODE_PER_TRIAL, _config.MATCHING_CALL_MODE_GROUPED))
check("...and the flag was restored",
      _config.MATCHING_PER_TRIAL_CALLS_ENABLED, _saved_flag)

# --- THE REFUSAL, WHICH IS THE WHOLE POINT ---------------------------------
def _mode_stamp(mode):
    """A fully-resolved stamp differing from its sibling in the arm ALONE.

    Keys DERIVED from FINGERPRINT_FIELDS, never enumerated, so the next gated
    field cannot make this build an under-shaped stamp that reports
    FP_UNRESOLVED for a reason that has nothing to do with the arm.
    """
    out = {f: f"offline-{f}" for f in _fp.FINGERPRINT_FIELDS}
    out["fingerprint_version"] = _fp.FINGERPRINT_VERSION
    out["matching_call_mode"] = mode
    return out

_GROUPED = _mode_stamp(_config.MATCHING_CALL_MODE_GROUPED)
_PER_TRIAL = _mode_stamp(_config.MATCHING_CALL_MODE_PER_TRIAL)

check("both probe stamps are fully resolved (non-degeneracy: an UNKNOWN in "
      "either would make every comparison below FP_UNRESOLVED, which passes a "
      "'not FP_MATCH' check for entirely the wrong reason)",
      (_fp.is_resolved(_GROUPED), _fp.is_resolved(_PER_TRIAL)), (True, True))
check("...and they differ in the ARM ALONE",
      [f for f in _fp.FINGERPRINT_FIELDS if _GROUPED[f] != _PER_TRIAL[f]],
      ["matching_call_mode"])

_mode_outcome, _mode_detail = _fp.compare(_GROUPED, _PER_TRIAL)
check("a grouped artifact against a per-trial run is FP_CHANGED -- NOT "
      "FP_MATCH, which is what the five-field gate answered",
      _mode_outcome, _fp.FP_CHANGED)
check_true("...and the detail NAMES THE FIELD AND BOTH MODES, so an operator "
           "reads what moved rather than being told something did",
           "matching_call_mode" in _mode_detail
           and repr(_config.MATCHING_CALL_MODE_GROUPED) in _mode_detail
           and repr(_config.MATCHING_CALL_MODE_PER_TRIAL) in _mode_detail)
check("...and the same arm on both sides still resumes, so the gate has not "
      "simply been made to refuse everything",
      _fp.compare(_GROUPED, _GROUPED)[0], _fp.FP_MATCH)
check("...in the other direction too", _fp.compare(_PER_TRIAL, _GROUPED)[0],
      _fp.FP_CHANGED)

check_true("summary() names the arm, so the banner a consumer prints before "
           "spending states which arm it is about to run",
           str(_config.MATCHING_CALL_MODE_PER_TRIAL) in _fp.summary(_PER_TRIAL))
check_true("...and disagrees with the other arm's banner (non-degeneracy: a "
           "summary() that dropped the field would satisfy neither)",
           _fp.summary(_PER_TRIAL) != _fp.summary(_GROUPED))

# --- A v2 ARTIFACT ANSWERS FP_VERSION, NOT FP_CHANGED ----------------------
#
# The version bump's DESIGNED cost, asserted rather than assumed. A stamp
# written before this field existed must not have its missing arm compared
# against a live one and reported as a configuration change that may never have
# happened -- a true refusal for a false reason.
_v2 = {k: v for k, v in _GROUPED.items() if k != "matching_call_mode"}
_v2["fingerprint_version"] = 2
_v2_outcome, _v2_detail = _fp.compare(_v2, _PER_TRIAL)
check("a v2 stamp answers FP_VERSION, before any field is compared",
      _v2_outcome, _fp.FP_VERSION)
check_true("...and the detail names both versions rather than the arm",
           "2" in _v2_detail and str(_fp.FINGERPRINT_VERSION) in _v2_detail
           and "matching_call_mode" not in _v2_detail)
check_true("...and the refusal says the SHAPE changed and that clearing once "
           "is the whole remediation, which is the one outcome whose cause may "
           "be nothing at all",
           any("stamp SHAPE" in line for line in
               _fp.refusal_lines(_v2_outcome, _v2_detail, "an artifact",
                                 ["fix"])))

# --- IT DEGRADES RATHER THAN RAISING --------------------------------------
#
# `current()`'s contract is that it never raises: both consumers call it from a
# main() that is about to spend money, and an exception out of the STAMP would
# abort the run the stamp exists to describe. The owner cannot fail today, which
# is exactly why the guard is checked rather than argued away.
_saved_owner = _config.matching_call_mode
_before_deg = _fp.FINGERPRINT_DEGRADATIONS.copy()
try:
    def _boom():
        raise RuntimeError("the owner grew a lookup and it failed")
    _config.matching_call_mode = _boom
    _degraded_mode = drive(_fp._call_mode, default="<_call_mode raised>")
finally:
    _config.matching_call_mode = _saved_owner
check("an owner that raises is recorded as UNKNOWN rather than escaping",
      _degraded_mode, _fp.UNKNOWN)
check("...and the owner was restored BY IDENTITY",
      _config.matching_call_mode is _saved_owner, True)
check("...and the reason was counted, keyed by field and exception type",
      [k for k in _fp.FINGERPRINT_DEGRADATIONS
       if k.startswith("matching_call_mode:")
       and _fp.FINGERPRINT_DEGRADATIONS[k] > _before_deg.get(k, 0)],
      ["matching_call_mode:RuntimeError"])
check("...and an UNKNOWN arm makes the whole stamp unresolved, so compare() "
      "answers FP_UNRESOLVED rather than reporting a configuration change",
      _fp.compare(_GROUPED,
                  dict(_PER_TRIAL, matching_call_mode=_fp.UNKNOWN))[0],
      _fp.FP_UNRESOLVED)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2  utils.preserve_corrupt_file
# ===========================================================================

print()
print("=" * 70)
print("SECTION 2  preserving a state file that could not be read")
print("=" * 70)

_pdir = tempfile.mkdtemp(dir=_SCRATCH)
_pfile = os.path.join(_pdir, "state.json")

Path(_pfile).write_text("truncated{")
_kept, _err, _key = _utils.preserve_corrupt_file(_pfile, ".corrupt")
check("a MOVE reports the sidecar", os.path.basename(_kept), "state.json.corrupt")
check("...and no error", (_err, _key), (None, None))
check("...and the original is GONE from its own path", os.path.exists(_pfile), False)

Path(_pfile).write_text("truncated again{")
_kept2, _, _ = _utils.preserve_corrupt_file(_pfile, ".corrupt")
check("a second collision is NUMBERED rather than overwriting the first",
      os.path.basename(_kept2), "state.json.corrupt.1")
check("...so the first sidecar survives",
      Path(_kept).read_text(), "truncated{")

Path(_pfile).write_text("checkpoint payload")
_kept3, _, _ = _utils.preserve_corrupt_file(_pfile, ".corrupt", keep_original=True)
check("a COPY also produces a sidecar", os.path.basename(_kept3),
      "state.json.corrupt.2")
check("...and LEAVES the original in place -- which is what makes a checkpoint "
      "refusal sticky instead of loud once and silent afterwards",
      Path(_pfile).read_text(), "checkpoint payload")

check("a limit of 0 refuses by name rather than looping",
      _utils.preserve_corrupt_file(_pfile, ".corrupt", limit=0)[2],
      _utils.PRESERVE_EXHAUSTED)
check("an unrenameable path returns the error rather than raising",
      _utils.preserve_corrupt_file(
          os.path.join(_pdir, "does-not-exist"), ".corrupt")[2],
      "FileNotFoundError")


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3  the batch checkpoint
# ===========================================================================

print()
print("=" * 70)
print("SECTION 3  oncotriage/batch/runner.py checkpoint")
print("=" * 70)

_CKDIR = tempfile.mkdtemp(dir=_SCRATCH)
_paths_had = "checkpoint_path" in _paths._RESOLVED
_paths_was = _paths._RESOLVED.get("checkpoint_path")
_paths._RESOLVED["checkpoint_path"] = _CKDIR

try:
    _CK = _runner._checkpoint_path()
    check_true("the checkpoint under test is inside the scratch directory "
               "(non-degeneracy: without this every write below is production)",
               str(_CK).startswith(_CKDIR))

    _fp.clear_cache()
    _runner.CHECKPOINT_FAULTS.clear()

    # --- (a) round trip under an unchanged configuration ------------------
    _runner.save_checkpoint({"patient-a", "patient-b"})
    _stored = read_json(_CK)
    check("the checkpoint records what it was produced under",
          _stored["fingerprint"], _fp.current())
    check("...and names what its collection comparison compares",
          _stored["collection_identity"], _fp.COLLECTION_IDENTITY)
    check("...and the three pre-pass keys are unchanged",
          sorted(k for k in _stored if k not in
                 ("fingerprint", "collection_identity",
                  _runner.CHECKPOINT_COHORT_DIGEST_KEY)),
          ["completed_stems", "count", "last_updated"])
    # THE COHORT DIGEST IS A SIBLING OF `fingerprint`, NOT A KEY INSIDE IT, and
    # the check above is what pins that: the stamp compared one line up is
    # `_fp.current()` VERBATIM, so a seventh key smuggled into it would make the
    # checkpoint's stamp stop equalling the one the module produces. It is None
    # here because `save_checkpoint` was called with no cohort -- which is the
    # "this caller selected no cohort" state `load_checkpoint` skips the
    # membership comparison for.
    check("...and the cohort digest is written as its own key, defaulting to "
          "None for a caller that selected no cohort",
          (_runner.CHECKPOINT_COHORT_DIGEST_KEY in _stored,
           _stored.get(_runner.CHECKPOINT_COHORT_DIGEST_KEY)),
          (True, None))
    check("an unchanged configuration resumes exactly as before",
          _runner.load_checkpoint(), {"patient-a", "patient-b"})

    # --- (a2) THE OFFLINE SEAM ------------------------------------------
    #
    # A supplied stamp must reach the comparison WITHOUT resolving anything.
    # This is not a nicety: the first version of this pass had no such
    # parameter, so stamping the checkpoint silently gave load_checkpoint() and
    # save_checkpoint() -- which had touched no network in their lives -- a
    # live Qdrant dependency, and every caller without an endpoint got a
    # refusal for a reason that had nothing to do with its checkpoint. The
    # resolver is made to RAISE for the duration, so a resolution that did
    # happen could not be mistaken for a lucky one.
    # DERIVED FROM FINGERPRINT_FIELDS, NOT ENUMERATED. This was six literal
    # keys, and the renderer-digest pass is what proved that shape wrong: a
    # stamp missing a newly gated field is not a stamp with one field missing,
    # it is a stamp `compare()` cannot establish -- so the two checks below
    # failed for a reason that had nothing to do with what they assert. The
    # VALUES are irrelevant here and only that both sides are handed the SAME
    # object matters; deriving the KEYS is what keeps that true across the next
    # bump.
    _OFFLINE = dict(
        {_f: f"offline-{_f}" for _f in _fp.FINGERPRINT_FIELDS},
        fingerprint_version=_fp.FINGERPRINT_VERSION)
    check("the offline stamp carries exactly what the gate compares "
          "(non-degeneracy: a stamp short of a field is FP_UNRESOLVED, not a "
          "match, so a stale literal here would fail the two checks below for "
          "the wrong reason)",
          sorted(_OFFLINE),
          sorted(("fingerprint_version",) + _fp.FINGERPRINT_FIELDS))


    def _must_not_resolve():
        raise AssertionError("the collection resolver was called")

    _fp.clear_cache()
    with rebound(_fp, "_resolve_collection", _must_not_resolve):
        _runner.save_checkpoint({"offline-1"}, fingerprint=_OFFLINE)
        check("save_checkpoint with an explicit stamp resolves NOTHING",
              read_json(_CK)["fingerprint"], _OFFLINE)
        check("...and load_checkpoint with the same stamp resumes, offline",
              drive(lambda: _runner.load_checkpoint(fingerprint=_OFFLINE)),
              {"offline-1"})
        check("...while a DIFFERENT explicit stamp still refuses (the seam is "
              "a seam, not a bypass)",
              raises(lambda: _runner.load_checkpoint(
                  fingerprint=dict(_OFFLINE, collection_points=8)))[0],
              "ResumeRefusal")
        check("...and omitting it DOES resolve (non-degeneracy: without this "
              "the three checks above would pass against a function that "
              "never consults the resolver at all)",
              raises(_runner.load_checkpoint)[0], "AssertionError")
    _fp.clear_cache()
    _runner.CHECKPOINT_FAULTS.clear()
    _runner.save_checkpoint({"patient-a", "patient-b"})

    # --- (b) every mismatch class refuses, naming the field ---------------
    _CASES = (
        ("llm_classifier_prompt_version", "9.9.9", _fp.FP_CHANGED),
        ("matching_model_configured", "gpt-4o-2024-08-06", _fp.FP_CHANGED),
        # THE ARM, DRIVEN THROUGH THE REAL load_checkpoint RATHER THAN THROUGH
        # compare(). This is the concrete harm the field was gated for: a
        # grouped-mode checkpoint resumed under per-trial mode used to skip
        # every patient the grouped process had completed and run the rest in
        # the other arm, into one inferences table, silently. The stored value
        # is whichever arm this process is NOT in, so the case is a real
        # mismatch on either setting of the flag.
        ("matching_call_mode",
         (_config.MATCHING_CALL_MODE_PER_TRIAL
          if _config.matching_call_mode() == _config.MATCHING_CALL_MODE_GROUPED
          else _config.MATCHING_CALL_MODE_GROUPED),
         _fp.FP_CHANGED),
        ("qdrant_collection", "trial_criteria_20260101_000000", _fp.FP_CHANGED),
        ("collection_points", 1, _fp.FP_CHANGED),
        ("data_snapshot_date", "2019-05-05", _fp.FP_CHANGED),
        # THE COHORT, DRIVEN THROUGH THE REAL GATE. The concrete harm: a
        # checkpoint drawn at one seed or one size resumed under another skips
        # the FIRST cohort's completed patients and runs the SECOND cohort's
        # remainder into the same table, so every rate over the artifact is
        # computed across two cohorts presented as one.
        ("campaign_cohort_size", 7, _fp.FP_CHANGED),
        ("campaign_cohort_seed", 999999, _fp.FP_CHANGED),
        # THE RERANKER'S HUB COMMIT, DRIVEN THROUGH THE REAL GATE. The concrete
        # harm: config.CROSS_ENCODER_MODEL is a REPOSITORY, so an unpinned load
        # resolves whatever `main` pointed at when the cache was filled -- and a
        # campaign resumed after that pointer moved has half its patients ranked
        # by one set of weights and half by another, in one table, with every
        # artifact naming the identical checkpoint string and nothing raising.
        # The value is a well-formed 40-hex id that is not the pinned one, so
        # the case is a real mismatch whatever the pin is set to.
        ("cross_encoder_revision",
         "ffffffffffffffffffffffffffffffffffffffff", _fp.FP_CHANGED),
    )
    # EVERY GATED FIELD IS EITHER IN THIS TABLE OR HAS ITS OWN SECTION, and the
    # round trip is closed here so a field gated in a later pass cannot be added
    # to the stamp and left undriven through the real checkpoint gate -- which
    # is what "the gate refuses on it" is actually worth.
    check("the mismatch table plus the two fields with their own sections "
          "covers every gated field",
          sorted(set(_fp.FINGERPRINT_FIELDS)
                 - {f for f, _, _ in _CASES}
                 - {"llm_classifier_renderer_digest"}),
          [])
    for _field, _value, _want in _CASES:
        _stale = read_json(_CK)
        _stale["fingerprint"][_field] = _value
        write_json(_CK, _stale)
        _before = digest(_CK)
        _type, _msg = raises(_runner.load_checkpoint)
        check(f"a checkpoint written under a different {_field} REFUSES",
              _type, "ResumeRefusal")
        check_true(f"...naming the field", _field in _msg)
        check_true(f"...and both values", repr(_value) in _msg)
        check_true("...and naming the remediation", "--fresh" in _msg)
        check("...and DELETING NOTHING", digest(_CK), _before)
        check_true("...and the file is still where it was", os.path.exists(_CK))

    check("each refusal is counted under the outcome that refused it",
          dict(_runner.CHECKPOINT_FAULTS),
          {f"refused:{_fp.FP_CHANGED}": len(_CASES)})
    _runner.CHECKPOINT_FAULTS.clear()

    # --- (c) the legacy checkpoint: unknown provenance --------------------
    write_json(_CK, {"completed_stems": ["p1", "p2", "p3"],
                     "last_updated": "2026-08-01T00:00:00", "count": 3})
    _before = digest(_CK)
    _type, _msg = raises(_runner.load_checkpoint)
    check("a checkpoint with NO fingerprint refuses as unknown provenance",
          _type, "ResumeRefusal")
    # TWO DISTINCT FP_ABSENT MESSAGES, and this is the first: a checkpoint
    # written by the pre-pass writer carries no `fingerprint` KEY at all. The
    # other -- a stamp present but carrying no version -- is what a pre-pass
    # evaluation MANIFEST looks like, and section 1 drives it. Asserting the
    # wrong one of the two is how the first draft of this check failed, which
    # is itself the argument for asserting the text rather than the outcome.
    check_true("...saying no fingerprint was recorded rather than blaming a "
               "field",
               "no configuration fingerprint was recorded" in _msg)
    check("...and is counted as FP_ABSENT",
          dict(_runner.CHECKPOINT_FAULTS),
          {f"refused:{_fp.FP_ABSENT}": 1})
    check("...and nothing was deleted", digest(_CK), _before)
    check_true("...and it was NOT silently adopted -- a second call refuses too",
               raises(_runner.load_checkpoint)[0] == "ResumeRefusal")
    _runner.CHECKPOINT_FAULTS.clear()

    # --- (d) a version bump is its own diagnosis --------------------------
    write_json(_CK, {"completed_stems": ["p1"], "count": 1,
                     "fingerprint": dict(_fp.current(),
                                         fingerprint_version=99)})
    _type, _msg = raises(_runner.load_checkpoint)
    check("a checkpoint stamped by another VERSION refuses", _type,
          "ResumeRefusal")
    check_true("...as a version difference, not as a configuration change",
               "fingerprint_version" in _msg and "99" in _msg)
    _runner.CHECKPOINT_FAULTS.clear()

    # --- (e) an unresolvable CURRENT configuration refuses ----------------
    _runner.save_checkpoint({"p1"})
    with rebound(_fp, "_resolve_collection", lambda: (_fp.UNKNOWN, _fp.UNKNOWN)):
        _fp.clear_cache()
        _type, _msg = raises(_runner.load_checkpoint)
    _fp.clear_cache()
    check("a run that cannot establish its OWN configuration refuses",
          _type, "ResumeRefusal")
    check_true("...saying that, rather than listing five changed fields",
               "could not be established" in _msg)
    _runner.CHECKPOINT_FAULTS.clear()

    # --- (f) the corrupt checkpoint --------------------------------------
    for _payload, _label, _phase in (
            ("{not json at all", "a truncated file", "load:"),
            ('["a", "b"]', "a JSON array where an object belongs", "shape:"),
            ('{"completed_stems": "p1,p2"}', "completed_stems that is not a list",
             "shape:")):
        Path(_CK).write_text(_payload)
        _before = digest(_CK)
        _type, _msg = raises(_runner.load_checkpoint)
        check(f"{_label} REFUSES rather than starting fresh", _type,
              "ResumeRefusal")
        check_true("...saying what continuing would have cost",
                   "silently re-run every patient" in _msg)
        check("...and the checkpoint is STILL THERE, byte-identical",
              digest(_CK), _before)
        check_true("...and a copy was preserved beside it",
                   any(f.startswith("checkpoint") and ".corrupt" in f
                       for f in os.listdir(_CKDIR))
                   or any(".corrupt" in f for f in os.listdir(_CKDIR)))
        check_true(f"...counted under {_phase}",
                   any(k.startswith(_phase) for k in _runner.CHECKPOINT_FAULTS))
        _runner.CHECKPOINT_FAULTS.clear()

    check_true("THE REFUSAL IS STICKY: a second invocation refuses too, so the "
               "cohort cannot be silently re-billed by running the command "
               "again",
               raises(_runner.load_checkpoint)[0] == "ResumeRefusal")

    _sidecars = [f for f in os.listdir(_CKDIR) if ".corrupt" in f]
    check("each corruption got its own numbered sidecar rather than "
          "overwriting the previous evidence",
          len(_sidecars), len(set(_sidecars)))
    check_true("...and there is more than one (non-degeneracy)",
               len(_sidecars) >= 3)

    # --- (g) the remediation actually works -------------------------------
    _runner.clear_checkpoint()
    check("clear_checkpoint() removes it", os.path.exists(_CK), False)
    check("...and the next load is a clean fresh start, not a refusal",
          _runner.load_checkpoint(), set())

    # --- (h) absent is not a refusal --------------------------------------
    check("no checkpoint at all returns an empty set and refuses nothing",
          _runner.load_checkpoint(), set())

    # --- (i) NEGATIVE CONTROLS -------------------------------------------
    print()
    print("  -- negative controls: the gate reverted must FAIL --")

    _runner.save_checkpoint({"p1"})
    _stale = read_json(_CK)
    _stale["fingerprint"]["llm_classifier_prompt_version"] = "9.9.9"
    write_json(_CK, _stale)

    check("(control precondition) the shipped gate refuses this checkpoint",
          raises(_runner.load_checkpoint)[0], "ResumeRefusal")

    with rebound(_fp, "compare", lambda rec, cur: (_fp.FP_MATCH, "reverted")):
        _reverted = drive(_runner.load_checkpoint)
        check_true("WITH THE COMPARISON REVERTED the same checkpoint is "
                   "accepted -- so the assertion above is a real check and not "
                   "a property of the fixture",
                   _reverted == {"p1"})

    Path(_CK).write_text("{truncated")

    def _pre_pass_load():
        """load_checkpoint as it stood before this pass: warn, start fresh."""
        try:
            return set(read_json(_CK).get("completed_stems", []))
        except Exception:                              # noqa: BLE001
            return set()

    check("(control) the PRE-PASS corrupt handler returns an empty set -- the "
          "silent full re-bill this pass removes",
          _pre_pass_load(), set())
    check("...while the shipped one refuses",
          raises(_runner.load_checkpoint)[0], "ResumeRefusal")
    _runner.CHECKPOINT_FAULTS.clear()

finally:
    if _paths_had:
        _paths._RESOLVED["checkpoint_path"] = _paths_was
    else:
        _paths._RESOLVED.pop("checkpoint_path", None)

check("the checkpoint path resolver was restored",
      "checkpoint_path" in _paths._RESOLVED, _paths_had)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4  the ablation checkpoint
# ===========================================================================

print()
print("=" * 70)
print("SECTION 4  oncotriage/ablation/study.py checkpoint")
print("=" * 70)

_ADIR = tempfile.mkdtemp(dir=_SCRATCH)
_DB_A = os.path.join(_ADIR, "study_a.db")
_DB_B = os.path.join(_ADIR, "study_b.db")
_CK_A = _study._ablation_checkpoint_path(_DB_A)
_CK_B = _study._ablation_checkpoint_path(_DB_B)

_fp.clear_cache()
_study.CHECKPOINT_FAULTS.clear()

check_true("pass 20f-3's per-database isolation is intact: two databases get "
           "two checkpoints", _CK_A != _CK_B)
check_true("...and neither is the production one",
           _CK_A != _study._ablation_checkpoint_path())

_study.save_ablation_checkpoint({("full_pipeline", "p1")}, db_path=_DB_A)
check("the ablation checkpoint records what produced it",
      read_json(_CK_A)["fingerprint"], _fp.current())
check("an unchanged configuration resumes exactly as before",
      _study.load_ablation_checkpoint(db_path=_DB_A),
      {("full_pipeline", "p1")})
check("...and B, which was never written, is still a clean empty resume",
      _study.load_ablation_checkpoint(db_path=_DB_B), set())

_fp.clear_cache()
# ONE derived stamp handed to both sides. It was two enumerated literals, which
# is two chances to be short of a gated field after a version bump -- and a
# stamp short of one is FP_UNRESOLVED, so the refusal names the digest and says
# nothing about the isolation this section is testing.
_ABL_OFFLINE = dict({_f: f"offline-{_f}" for _f in _fp.FINGERPRINT_FIELDS},
                    fingerprint_version=_fp.FINGERPRINT_VERSION)
with rebound(_fp, "_resolve_collection",
             lambda: (_ for _ in ()).throw(AssertionError("resolver called"))):
    _study.save_ablation_checkpoint({("full_pipeline", "off")}, db_path=_DB_B,
                                    fingerprint=_ABL_OFFLINE)
    check("the ablation loader honours an explicit stamp offline too",
          drive(lambda: _study.load_ablation_checkpoint(
              db_path=_DB_B, fingerprint=_ABL_OFFLINE)),
          {("full_pipeline", "off")})
_study.clear_ablation_checkpoint(db_path=_DB_B)
_fp.clear_cache()

_stale = read_json(_CK_A)
_stale["fingerprint"]["matching_model_configured"] = "gpt-4o-2024-08-06"
write_json(_CK_A, _stale)
_before = digest(_CK_A)
_type, _msg = raises(lambda: _study.load_ablation_checkpoint(db_path=_DB_A))
check("a study checkpoint from a different model REFUSES", _type,
      "ResumeRefusal")
check_true("...naming the field and both values",
           "matching_model_configured" in _msg
           and "gpt-4o-2024-08-06" in _msg)
check_true("...and the remediation is the flag THIS entry point has",
           "--fresh-start" in _msg)
check_true("...pointed at THIS database, not the production checkpoint",
           _DB_A in _msg)
check("...and nothing was deleted", digest(_CK_A), _before)
check("...and B is untouched by A's refusal",
      _study.load_ablation_checkpoint(db_path=_DB_B), set())

# THE ARM, THROUGH THIS GATE TOO. The three consumers share one comparator, so
# the mechanism is proved by the batch drive -- what this adds is that the
# ABLATION entry point's own remediation and its own --db are named on an arm
# refusal, which is the thing an operator acts on and the one part of a refusal
# that is NOT shared. A study resumed across the arms would put grouped and
# per-trial rows into one ablation_results.db, whose whole purpose is comparing
# configurations.
_stale = read_json(_CK_A)
_stale["fingerprint"]["matching_call_mode"] = (
    _config.MATCHING_CALL_MODE_PER_TRIAL
    if _config.matching_call_mode() == _config.MATCHING_CALL_MODE_GROUPED
    else _config.MATCHING_CALL_MODE_GROUPED)
write_json(_CK_A, _stale)
_before = digest(_CK_A)
_type, _msg = raises(lambda: _study.load_ablation_checkpoint(db_path=_DB_A))
check("a study checkpoint from a different STAGE 5 CALL MODE refuses", _type,
      "ResumeRefusal")
check_true("...naming the field and BOTH arms",
           "matching_call_mode" in _msg
           and repr(_config.MATCHING_CALL_MODE_GROUPED) in _msg
           and repr(_config.MATCHING_CALL_MODE_PER_TRIAL) in _msg)
check_true("...with this entry point's own remediation and its own --db",
           "--fresh-start" in _msg and _DB_A in _msg)
check("...and nothing was deleted", digest(_CK_A), _before)

write_json(_CK_A, {"completed": [["full_pipeline", "p1"]], "count": 1})
_type, _msg = raises(lambda: _study.load_ablation_checkpoint(db_path=_DB_A))
check("a legacy study checkpoint refuses as unknown provenance", _type,
      "ResumeRefusal")
check_true("...and says no fingerprint was recorded",
           "no configuration fingerprint was recorded" in _msg)

Path(_CK_A).write_text("{truncated")
_before = digest(_CK_A)
_type, _msg = raises(lambda: _study.load_ablation_checkpoint(db_path=_DB_A))
check("a corrupt study checkpoint refuses rather than starting fresh", _type,
      "ResumeRefusal")
check_true("...saying what continuing would have cost",
           "silently re-run every (config, patient) pair" in _msg)
check("...and the checkpoint is still there, byte-identical",
      digest(_CK_A), _before)
check_true("...and a copy was preserved",
           any(".corrupt" in f for f in os.listdir(_ADIR)))
check_true("...and the refusal is sticky",
           raises(lambda: _study.load_ablation_checkpoint(db_path=_DB_A))[0]
           == "ResumeRefusal")

_study.clear_ablation_checkpoint(db_path=_DB_A)
check("the remediation clears THAT checkpoint", os.path.exists(_CK_A), False)
check("...and the next load is a clean fresh start",
      _study.load_ablation_checkpoint(db_path=_DB_A), set())
check_true("...and B's checkpoint path was never created by any of it",
           not os.path.exists(_CK_B))

_study.CHECKPOINT_FAULTS.clear()


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 5  run_harness.main(), driven
# ===========================================================================

print()
print("=" * 70)
print("SECTION 5  the evaluation run harness, end to end")
print("=" * 70)

_BUNDLES = tempfile.mkdtemp(dir=_SCRATCH)
for _i in range(4):
    Path(os.path.join(_BUNDLES, f"bundle_{_i}.json")).write_text("{}")

_rows_had = "data_fhir_path" in _paths._RESOLVED
_rows_was = _paths._RESOLVED.get("data_fhir_path")
_paths._RESOLVED["data_fhir_path"] = _BUNDLES + os.sep


def _row(index, path, stage, diagnosis):
    return {
        "patient_id": f"patient-{index}",
        "bundle": f"bundle_{index}.json",
        "path": os.path.join(_BUNDLES, f"bundle_{index}.json"),
        "primary_diagnosis": diagnosis,
        "stage": stage,
        "expansion_path": path,
        "mesh_resolution": "specific",
        "ecog": 1,
    }


_ROWS = [
    _row(0, "expansion_path_fallback", None, "Neoplasm of unknown origin"),
    _row(1, "mesh", 2, "Malignant neoplasm of breast"),
    _row(2, "mesh", 3, "Malignant neoplasm of colon"),
    _row(3, "mesh", 4, "Small cell carcinoma of lung"),
]


class _StubReadiness:
    INDEX_POPULATED = "populated"

    @staticmethod
    def probe_index(client=None, collection=None):
        return {"state": "populated", "points": _COLLECTION["points"],
                "collection": _config.COLLECTION_NAME,
                "endpoint": "stub://none", "error": None}


_CALLS = {"n": 0, "patients": []}


def _stub_run_one_patient(selection_entry, graph):
    """A post-check-clean record, with no model call anywhere near it."""
    _CALLS["n"] += 1
    row = selection_entry["row"]
    _CALLS["patients"].append(row["patient_id"])
    record = {
        "schema_version": _rh.RECORD_SCHEMA_VERSION,
        "patient_id": row["patient_id"],
        "run": {k: None for k in _rh.REQUIRED_RUN_KEYS},
        "patient_summary": {"text": "PATIENT RECORD", "error": None},
        "contexts": [],
        "verdicts": [{"nct_id": "NCT00000001", "eligible": "eligible",
                      "inclusion_criteria": [{"criterion": "c"}],
                      "exclusion_criteria": []}],
        "criterion_decision_count": 1,
        "result": {},
    }
    record["run"].update({
        "cost": {"cost_usd": 0.0, "cost_complete": True, "notes": []},
        "duration_s": 0.0, "problems": [],
    })
    return record, {"status": _rh.STATUS_OK, "terminal_node": "node_finalize",
                    "error": None}


def run_main(argv):
    """Drive main() with every paid and networked seam replaced."""
    with rebound(_rh, "scan_cohort", lambda paths_: list(_ROWS)), \
            rebound(_rh, "readiness", _StubReadiness), \
            rebound(_rh, "run_one_patient", _stub_run_one_patient), \
            rebound(_rh, "compiled_graph", lambda: "<stub graph>"):
        check_true("[seam] the paid runner in force is this file's stand-in",
                   _rh.run_one_patient is _stub_run_one_patient)
        return drive(lambda: _rh.main(argv), default="<main raised>")


_OUT = os.path.join(_SCRATCH, "eval_run_scenario")

try:
    # --- (a) a fresh directory: no gate, nothing to resume ----------------
    _CALLS["n"] = 0
    _fp.clear_cache()
    _code = run_main(["--select", "3", "--output-dir", _OUT])
    check("a fresh run succeeds", _code, _rh.EXIT_OK)
    check("...and ran every selected patient", _CALLS["n"], 3)

    _man = read_json(os.path.join(_OUT, "manifest.json"))
    check("the manifest records the resolved backing collection, not the alias",
          _man["environment"]["qdrant_collection"], _COLLECTION["name"])
    check_true("...which differs from collection_alias (non-degeneracy: before "
               "this pass those two fields held the same string on every "
               "manifest ever written, so a gate on the first could never fire)",
               _man["environment"]["qdrant_collection"]
               != _man["environment"]["collection_alias"])
    check("...and states what the collection comparison compares",
          _man["environment"]["collection_identity"], _fp.COLLECTION_IDENTITY)
    check("...and seeds environment_history with era 0",
          [e["era"] for e in _man["environment_history"]], [0])
    check("...and every record entry is stamped with its era",
          sorted({e["environment_era"] for e in _man["runs"].values()}), [0])
    check("...and the invocation says it was not a resume",
          _man["invocations"][-1]["resume"], False)

    _ENV_BYTES = canonical(_man["environment"])
    _HIST_BYTES = canonical(_man["environment_history"])

    # --- (b) --only into a MATCHING directory -----------------------------
    _CALLS["n"] = 0
    _code = run_main(["--select", "3", "--output-dir", _OUT,
                      "--only", "patient-1"])
    check("--only into a matching-environment directory proceeds", _code,
          _rh.EXIT_OK)
    check("...running only the named patient", _CALLS["patients"][-1:],
          ["patient-1"])
    _man = read_json(os.path.join(_OUT, "manifest.json"))
    check("...and the stored environment is BYTE-IDENTICAL",
          canonical(_man["environment"]), _ENV_BYTES)
    check("...and no era was added", canonical(_man["environment_history"]),
          _HIST_BYTES)

    # --- (c) --resume: skip the completed, re-run the failed --------------
    _man = read_json(os.path.join(_OUT, "manifest.json"))
    _man["runs"]["patient-2"]["status"] = _rh.STATUS_FAILED
    _man["runs"]["patient-2"]["error"] = "ConnectionError: broke mid-run"
    write_json(os.path.join(_OUT, "manifest.json"), _man)

    _CALLS["n"] = 0
    _CALLS["patients"] = []
    _code = run_main(["--select", "3", "--output-dir", _OUT, "--resume"])
    check("--resume succeeds", _code, _rh.EXIT_OK)
    check("...and runs ONLY the failed patient", _CALLS["patients"],
          ["patient-2"])
    _man = read_json(os.path.join(_OUT, "manifest.json"))
    _inv = _man["invocations"][-1]
    check("...the invocation records that it resumed", _inv["resume"], True)
    check("...and which patients it skipped", sorted(_inv["patients_skipped"]),
          ["patient-0", "patient-1"])
    check_true("...with a reason naming the status and the record file",
               all("'ok'" in r and ".json" in r
                   for r in _inv["skip_reasons"].values()))
    check_true("...and a reason for the one it ran",
               "run:status" in _inv["run_reasons"]["patient-2"])
    check("...and the stored environment is STILL byte-identical",
          canonical(_man["environment"]), _ENV_BYTES)

    # --- (d) a skip requires the record ON DISK, not just the entry -------
    _man = read_json(os.path.join(_OUT, "manifest.json"))
    _victim = _man["runs"]["patient-1"]["file"]
    os.remove(os.path.join(_OUT, _victim))
    _CALLS["patients"] = []
    _code = run_main(["--select", "3", "--output-dir", _OUT, "--resume"])
    check("a manifest entry whose record is MISSING is re-run, not skipped",
          _CALLS["patients"], ["patient-1"])
    _inv = read_json(os.path.join(_OUT, "manifest.json"))["invocations"][-1]
    check_true("...and the reason says the file is not there",
               "not in the output directory"
               in _inv["run_reasons"]["patient-1"])

    # --- (e) pipeline_error re-runs, and says why -------------------------
    _man = read_json(os.path.join(_OUT, "manifest.json"))
    _man["runs"]["patient-0"]["status"] = _rh.STATUS_PIPELINE_ERROR
    _man["runs"]["patient-0"]["error"] = "the graph's own error handler"
    write_json(os.path.join(_OUT, "manifest.json"), _man)
    _CALLS["patients"] = []
    run_main(["--select", "3", "--output-dir", _OUT, "--resume"])
    check("a pipeline_error entry is re-run", _CALLS["patients"], ["patient-0"])

    # --- (f) --resume with nothing to resume ------------------------------
    _EMPTY = os.path.join(_SCRATCH, "eval_run_empty")
    _CALLS["patients"] = []
    _code = run_main(["--select", "2", "--output-dir", _EMPTY, "--resume"])
    check("--resume against a directory with no manifest runs everything",
          len(_CALLS["patients"]), 2)
    check("...and succeeds rather than refusing", _code, _rh.EXIT_OK)

    # --- (g) --resume without --output-dir is a refusal -------------------
    _CALLS["n"] = 0
    _code = run_main(["--resume"])
    check("--resume with no --output-dir REFUSES", _code, _rh.EXIT_PRECONDITION)
    check("...having run nothing", _CALLS["n"], 0)

    # --- (h) THE ENVIRONMENT REFUSAL --------------------------------------
    print()
    print("  -- the environment guard --")

    _MAN_PATH = os.path.join(_OUT, "manifest.json")
    _MAN_BEFORE = digest(_MAN_PATH)
    _FILES_BEFORE = sorted(os.listdir(_OUT))

    _COLLECTION["name"] = "trial_criteria_20260901_000000"
    _CALLS["n"] = 0
    _code = run_main(["--select", "3", "--output-dir", _OUT])
    check("a run against a DIFFERENT collection refuses", _code,
          _rh.EXIT_PRECONDITION)
    check("...having spent nothing", _CALLS["n"], 0)
    check("...and written nothing: the manifest is byte-identical",
          digest(_MAN_PATH), _MAN_BEFORE)
    check("...and no file was added or removed", sorted(os.listdir(_OUT)),
          _FILES_BEFORE)

    _CALLS["n"] = 0
    _code = run_main(["--select", "3", "--output-dir", _OUT, "--resume"])
    check("--resume into a mismatched directory refuses too", _code,
          _rh.EXIT_PRECONDITION)
    check("...having spent nothing", _CALLS["n"], 0)
    check("...and written nothing", digest(_MAN_PATH), _MAN_BEFORE)

    _model_was = _config.MATCHING_MODEL
    _config.MATCHING_MODEL = "gpt-4o-2024-08-06"
    _COLLECTION["name"] = "trial_criteria_20260807_111807"
    _code = run_main(["--select", "3", "--output-dir", _OUT])
    check("a run under a different MODEL refuses", _code,
          _rh.EXIT_PRECONDITION)
    check("...and written nothing", digest(_MAN_PATH), _MAN_BEFORE)
    _config.MATCHING_MODEL = _model_was

    _snap_was = _config.DATA_SNAPSHOT_DATE
    _config.DATA_SNAPSHOT_DATE = "2020-06-30"
    _code = run_main(["--select", "3", "--output-dir", _OUT])
    check("a run under a different SNAPSHOT DATE refuses", _code,
          _rh.EXIT_PRECONDITION)
    check("...and written nothing", digest(_MAN_PATH), _MAN_BEFORE)
    _config.DATA_SNAPSHOT_DATE = _snap_was

    _points_was = _COLLECTION["points"]
    _COLLECTION["points"] = _points_was - 40
    _code = run_main(["--select", "3", "--output-dir", _OUT])
    check("a collection with the same name and a different POINT COUNT "
          "refuses -- an in-place re-index", _code, _rh.EXIT_PRECONDITION)
    check("...and written nothing", digest(_MAN_PATH), _MAN_BEFORE)
    _COLLECTION["points"] = _points_was

    # THE ARM, DRIVEN THROUGH THE REAL main(). The evaluation harness is the one
    # consumer with an escape hatch, so both halves of requirement 5 are here:
    # a mode change refuses by default, and --allow-environment-change admits it
    # (that half is exercised by the override group below, whose OVERRIDABLE_
    # OUTCOMES membership is what this case's FP_CHANGED outcome buys).
    _arm_was = _config.MATCHING_PER_TRIAL_CALLS_ENABLED
    _config.MATCHING_PER_TRIAL_CALLS_ENABLED = not _arm_was
    _code = run_main(["--select", "3", "--output-dir", _OUT])
    check("a run under a different STAGE 5 CALL MODE refuses -- the resume "
          "that used to run two arms into one artifact", _code,
          _rh.EXIT_PRECONDITION)
    check("...and written nothing", digest(_MAN_PATH), _MAN_BEFORE)
    check("...and the refusal is an OVERRIDABLE outcome, so the operator who "
          "means it has --allow-environment-change and the one who does not "
          "has a stop",
          _rh.environment_gate(read_json(_MAN_PATH), _fp.current(refresh=True))[0]
          in _rh.OVERRIDABLE_OUTCOMES, True)
    _config.MATCHING_PER_TRIAL_CALLS_ENABLED = _arm_was
    _fp.clear_cache()
    check("...and the flag was restored",
          _config.MATCHING_PER_TRIAL_CALLS_ENABLED, _arm_was)

    check("with everything restored the same command succeeds again "
          "(non-degeneracy: the five refusals above are about the change, not "
          "about the directory)",
          run_main(["--select", "3", "--output-dir", _OUT, "--resume"]),
          _rh.EXIT_OK)

    # --- (i) THE OVERRIDE, and what it records ----------------------------
    print()
    print("  -- the override --")

    _COLLECTION["name"] = "trial_criteria_20260901_000000"
    _CALLS["patients"] = []
    _code = run_main(["--select", "3", "--output-dir", _OUT, "--resume",
                      "--allow-environment-change"])
    check("--allow-environment-change proceeds", _code, _rh.EXIT_OK)

    _man = read_json(_MAN_PATH)
    check("...and the STORED environment is byte-identical: a paid run's "
          "record of itself is never overwritten",
          canonical(_man["environment"]), _ENV_BYTES)
    check("...the new configuration is a NEW era",
          [e["era"] for e in _man["environment_history"]], [0, 1])
    check("...recorded as an override", _man["environment_history"][1]["override"],
          True)
    check("...with the outcome that would have refused",
          _man["environment_history"][1]["outcome"], _fp.FP_CHANGED)
    check_true("...and the field that differed named",
               any("qdrant_collection" in d for d in
                   _man["environment_history"][1]["differing_fields"]))
    check("...and era 1's environment IS the new configuration",
          _man["environment_history"][1]["environment"]["qdrant_collection"],
          "trial_criteria_20260901_000000")
    check("...the invocation records the override",
          (_man["invocations"][-1]["environment_override"],
           _man["invocations"][-1]["environment_era"]), (True, 1))
    check_true("...and every record this invocation wrote is stamped era 1",
               all(_man["runs"][p]["environment_era"] == 1
                   for p in _CALLS["patients"]))
    check_true("...while the ones it did not touch are still era 0 -- which is "
               "the only thing in the artifact that says which records came "
               "from which pipeline",
               any(e["environment_era"] == 0 for e in _man["runs"].values()))

    _HIST2 = canonical(_man["environment_history"])
    run_main(["--select", "3", "--output-dir", _OUT, "--resume",
              "--allow-environment-change"])
    check("a SECOND invocation under the same overridden configuration reuses "
          "era 1 rather than appending a duplicate",
          canonical(read_json(_MAN_PATH)["environment_history"]), _HIST2)

    _COLLECTION["name"] = "trial_criteria_20260807_111807"

    # --- (j) an unresolvable configuration is NOT overridable -------------
    with rebound(_fp, "_resolve_collection", lambda: (_fp.UNKNOWN, _fp.UNKNOWN)):
        _MAN_NOW = digest(_MAN_PATH)
        _code = run_main(["--select", "3", "--output-dir", _OUT, "--resume",
                          "--allow-environment-change"])
        check("--allow-environment-change does NOT admit an UNRESOLVED "
              "configuration: there would be nothing to record as the era",
              _code, _rh.EXIT_PRECONDITION)
        check("...and nothing was written", digest(_MAN_PATH), _MAN_NOW)
    _fp.clear_cache()

    # --- (k) --scan-only stays free and prints the plan -------------------
    _CALLS["n"] = 0
    _code = run_main(["--select", "3", "--output-dir", _OUT, "--resume",
                      "--scan-only"])
    check("--scan-only --resume is free and succeeds", _code, _rh.EXIT_OK)
    check("...and ran nothing", _CALLS["n"], 0)

    # --- (l) NEGATIVE CONTROLS -------------------------------------------
    print()
    print("  -- negative controls: the guard reverted must FAIL --")

    def _pre_pass_record_environment(manifest, environment, outcome,
                                     fingerprint, override_used):
        """record_environment as main() behaved before this pass: assign."""
        manifest["environment"] = environment
        return 0

    _COLLECTION["name"] = "trial_criteria_20260901_000000"
    with rebound(_rh, "environment_gate",
                 lambda m, f: (_fp.FP_MATCH, "reverted")):
        with rebound(_rh, "record_environment", _pre_pass_record_environment):
            run_main(["--select", "3", "--output-dir", _OUT, "--resume"])
            _mixed = read_json(_MAN_PATH)
            check_true("WITH THE GUARD AND THE PRESERVE BOTH REVERTED the "
                       "stored environment IS overwritten by a different "
                       "configuration -- the defect this pass removes, and the "
                       "proof that the byte-identity checks above are real",
                       canonical(_mixed["environment"]) != _ENV_BYTES)
    _COLLECTION["name"] = "trial_criteria_20260807_111807"

    # resume_actions is a pure function of its arguments, so its controls are
    # different INPUTS -- the natural control for one.
    _entry = {"row": {"patient_id": "patient-9"}}
    _fake_manifest = {"runs": {"patient-9": {"status": _rh.STATUS_OK,
                                             "file": "nope.json"}}}
    check("(control) an ok entry naming an absent file is NOT skipped",
          _rh.resume_actions([_entry], _fake_manifest, _OUT, True)[0]["action"],
          _rh.ACTION_RUN_RECORD_MISSING)
    _fake_manifest["runs"]["patient-9"]["file"] = None
    check("(control) an ok entry naming NO file is not skipped either",
          _rh.resume_actions([_entry], _fake_manifest, _OUT, True)[0]["action"],
          _rh.ACTION_RUN_RECORD_MISSING)
    check("(control) without --resume nothing is skipped at all",
          _rh.resume_actions([_entry], _fake_manifest, _OUT, False)[0]["action"],
          _rh.ACTION_RUN_NOT_RESUMING)

    check("record_environment REFUSES to add an era without an override, so a "
          "caller that skipped the gate cannot rewrite the baseline silently",
          raises(lambda: _rh.record_environment(
              {"environment": dict(_fp.current(), data_snapshot_date="1999-01-01"),
               "environment_history": [{"era": 0, "environment": dict(
                   _fp.current(), data_snapshot_date="1999-01-01")}]},
              _fp.current(), _fp.FP_CHANGED, _fp.current(), False))[0],
          "RuntimeError")

finally:
    if _rows_had:
        _paths._RESOLVED["data_fhir_path"] = _rows_was
    else:
        _paths._RESOLVED.pop("data_fhir_path", None)

check("the bundle path resolver was restored",
      "data_fhir_path" in _paths._RESOLVED, _rows_had)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6  the partition, and what this file left behind
# ===========================================================================

print()
print("=" * 70)
print("SECTION 6  closed vocabularies and cleanup")
print("=" * 70)

check("RESUME_SKIP_STATUSES + RESUME_RERUN_STATUSES partition RUN_STATUSES",
      sorted(set(_rh.RESUME_SKIP_STATUSES) | set(_rh.RESUME_RERUN_STATUSES)),
      sorted(_rh.RUN_STATUSES))
check("...disjointly",
      sorted(set(_rh.RESUME_SKIP_STATUSES) & set(_rh.RESUME_RERUN_STATUSES)), [])
check("pipeline_error is on the RE-RUN side, as documented",
      _rh.STATUS_PIPELINE_ERROR in _rh.RESUME_RERUN_STATUSES, True)
check("nothing_to_evaluate is on the SKIP side",
      _rh.STATUS_NOTHING_TO_EVALUATE in _rh.RESUME_SKIP_STATUSES, True)

check("every action resume_actions can answer is in the closed RESUME_ACTIONS",
      sorted({a["action"] for a in _rh.resume_actions(
          [{"row": {"patient_id": "x"}}], None, _OUT, True)}
          | {_rh.ACTION_SKIP, _rh.ACTION_RUN_STATUS,
             _rh.ACTION_RUN_RECORD_MISSING, _rh.ACTION_RUN_NOT_RESUMING}),
      sorted(_rh.RESUME_ACTIONS))

_fp._resolve_collection = _REAL_RESOLVE
check_true("the shipped collection resolver was put back",
           _fp._resolve_collection is _REAL_RESOLVE)
_fp.clear_cache()
_fp.FINGERPRINT_DEGRADATIONS.clear()
_runner.CHECKPOINT_FAULTS.clear()
_study.CHECKPOINT_FAULTS.clear()

check("config.MATCHING_MODEL was restored", _config.MATCHING_MODEL, _model_was)
check("config.DATA_SNAPSHOT_DATE was restored", _config.DATA_SNAPSHOT_DATE,
      _snap_was)

shutil.rmtree(_SCRATCH, ignore_errors=True)
check("every file this test wrote is gone", os.path.exists(_SCRATCH), False)


#------------------------------------------------------------------------------


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 70)
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
Created on Thu Aug 20 09:00:00 2026

@author: ramyalsaffar
"""
