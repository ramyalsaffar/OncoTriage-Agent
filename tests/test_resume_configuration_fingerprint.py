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
is inside a fresh ``tempfile.mkdtemp()``, it patches no file in the repository,
and the repository files it READS are none -- no source is parsed. The two
files the suite's writers touch
(``oncotriage/registries/cancer_code_registry.py``, ``oncotriage/config.py``)
are neither read nor written here; ``config.MATCHING_MODEL`` and
``config.DATA_SNAPSHOT_DATE`` are rebound as ATTRIBUTES in memory and restored,
which touches no file at all.

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
                 ("fingerprint", "collection_identity")),
          ["completed_stems", "count", "last_updated"])
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
    _OFFLINE = {"fingerprint_version": _fp.FINGERPRINT_VERSION,
                "llm_classifier_prompt_version": "offline-prompt",
                "matching_model_configured": "offline-model",
                "qdrant_collection": "offline_collection",
                "collection_points": 7,
                "data_snapshot_date": "2026-01-01"}


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
        ("qdrant_collection", "trial_criteria_20260101_000000", _fp.FP_CHANGED),
        ("collection_points", 1, _fp.FP_CHANGED),
        ("data_snapshot_date", "2019-05-05", _fp.FP_CHANGED),
    )
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
with rebound(_fp, "_resolve_collection",
             lambda: (_ for _ in ()).throw(AssertionError("resolver called"))):
    _study.save_ablation_checkpoint({("full_pipeline", "off")}, db_path=_DB_B,
                                    fingerprint={
                                        "fingerprint_version": _fp.FINGERPRINT_VERSION,
                                        "llm_classifier_prompt_version": "p",
                                        "matching_model_configured": "m",
                                        "qdrant_collection": "c",
                                        "collection_points": 1,
                                        "data_snapshot_date": "2026-01-01"})
    check("the ablation loader honours an explicit stamp offline too",
          drive(lambda: _study.load_ablation_checkpoint(
              db_path=_DB_B,
              fingerprint={"fingerprint_version": _fp.FINGERPRINT_VERSION,
                           "llm_classifier_prompt_version": "p",
                           "matching_model_configured": "m",
                           "qdrant_collection": "c",
                           "collection_points": 1,
                           "data_snapshot_date": "2026-01-01"})),
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

    check("with everything restored the same command succeeds again "
          "(non-degeneracy: the four refusals above are about the change, not "
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
