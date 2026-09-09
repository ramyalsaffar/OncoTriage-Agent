######################################################################
# One judge session over several evaluation run directories
######################################################################

"""Rater Multi-Run Selection Test

``--include-keys`` names decisions by ``patient_id|nct_id|arm|index`` and
``select_included_decisions`` resolved them against ONE run's decisions, so a
population drawn from two run directories could not be rated in one session.
Item 11's three disputed populations lived in two directories, which is why it
ran two rater sessions -- and two ``rater_run.py`` invocations would have been
two processes with two module-global ledgers and two independent $50 budgets.
The multi-directory selection removes the reason to run two sessions;
``oncotriage/spend_journal.py`` removes the exposure when somebody does anyway.

**A SINGLE DIRECTORY MUST STAY BYTE-COMPATIBLE**, and section 2 does not take
``load_runs``' word for it: it drives ``load_run(d)`` and ``load_runs([d])``
over the same fabricated corpus and compares every field, including
``problems``, the decision ORDER and each decision's nine attributes.

THREE REFUSALS ARE DRIVEN, each in the state where a merge would be WRONG
rather than merely surprising: the same directory named twice; one patient in
two directories with DIFFERENT summaries; and one decision key in two
directories. Each is paired with a CLEAN CONTROL over the same fabricator with
that one condition removed, so a refusal cannot be passing because the corpus
is broken some other way.

NO NETWORK, NO KEYS, **NO SPEND** -- no client of any kind is built, nothing is
submitted, and the one end-to-end drive is ``_prepare``, which is everything
that must hold BEFORE a cent is spent. NO MODEL LOAD
(``ONCOTRIAGE_DEFER_LOCAL_MODELS`` above the imports), no live Qdrant, no
corpus, no database, no git history, no live server.

Every run directory is FABRICATED inside a ``tempfile.mkdtemp`` that is removed
and asserted gone; the production evaluation-runs tree is never read. NOT in
``tests/run_serial_tests.py``'s collision matrix: it writes only there, and the
one repository file it reads (``oncotriage/evaluation/rater.py``) is written by
neither of the suite's two writers. It EXECS NOTHING.

    python tests/test_rater_multi_run_selection.py
"""

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile

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

from oncotriage import config                                   # noqa: E402
from oncotriage.evaluation import rater as R                     # noqa: E402

_CODE_DIR = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(R.__file__))))


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
        _FAILURES.append(f"{label}\n          expected: {expected!r}\n"
                         f"          actual:   {actual!r}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected!r}")
        print(f"          actual:   {actual!r}")


def guarded(fn, *a, **kw):
    """Call ``fn``; return a marker instead of raising. See the other files."""
    try:
        return fn(*a, **kw)
    except Exception as exc:                                   # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


def refusal_code(fn, *a, **kw):
    """The ``RaterRefusal`` code, or a marker naming what happened instead."""
    try:
        fn(*a, **kw)
    except R.RaterRefusal as exc:
        return exc.code
    except Exception as exc:                                   # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"
    return "<DID NOT REFUSE>"


TMP = tempfile.mkdtemp(prefix="oncotriage-rater-multirun-")
_SRC = os.path.join(_CODE_DIR, "oncotriage", "evaluation", "rater.py")
with io.open(_SRC, "rb") as _fh:
    _SRC_BEFORE = hashlib.sha256(_fh.read()).hexdigest()

# The reference date the lifted rules render. A fabricated manifest that
# disagreed with it would refuse for a reason that has nothing to do with the
# merge, so it is READ rather than typed.
_RUBRIC, _RUBRIC_META = R.lift_rubric()
_REF = _RUBRIC_META.get("reference_date_in_rules")


def make_run(name, patients, summary_of=None):
    """A minimal evaluation run directory ``load_run`` accepts.

    ``patients`` is ``{patient_id: [(nct_id, arm, [criterion, ...]), ...]}``.
    ``summary_of`` lets one caller give the same patient id a DIFFERENT summary
    in a second directory, which is one of the three refusals.
    """
    root = os.path.join(TMP, name)
    os.makedirs(root, exist_ok=True)
    runs = {}
    total = 0
    for pid, verdicts in patients.items():
        by_nct = {}
        for nct, arm, criteria in verdicts:
            by_nct.setdefault(nct, {"nct_id": nct, "verdict_group": "eligible"})
            by_nct[nct][f"{arm}_criteria"] = [
                {"criterion": c,
                 "status": ("not_met" if arm == R.ARM_INCLUSION
                            else "not_violated"),
                 "patient_value": f"value for {c}"}
                for c in criteria]
        n = sum(len(v.get(f"{a}_criteria") or [])
                for v in by_nct.values() for a in R.ARMS)
        total += n
        fname = f"{pid}.json"
        with io.open(os.path.join(root, fname), "w", encoding="utf-8") as fh:
            json.dump({"patient_summary":
                       {"text": (summary_of or {}).get(
                           pid, f"PATIENT RECORD for {pid}")},
                       "verdicts": list(by_nct.values())}, fh)
        runs[pid] = {"file": fname, "criterion_decisions": n}
    with io.open(os.path.join(root, "manifest.json"), "w",
                 encoding="utf-8") as fh:
        json.dump({"runs": runs, "totals": {"criterion_decisions": total},
                   "environment": {"age_reference_date": _REF}}, fh)
    return root


_PA = "aaaa1111-0000-0000-0000-000000000001"
_PB = "bbbb2222-0000-0000-0000-000000000002"
_PC = "cccc3333-0000-0000-0000-000000000003"

_DIR1 = make_run("run_one", {
    _PA: [("NCT00000001", R.ARM_INCLUSION, ["one A", "one B"]),
          ("NCT00000001", R.ARM_EXCLUSION, ["one C"])],
    _PB: [("NCT00000002", R.ARM_INCLUSION, ["two A"])],
})
_DIR2 = make_run("run_two", {
    _PC: [("NCT00000003", R.ARM_EXCLUSION, ["three A", "three B"])],
})


print("=" * 74)
print("1. THE MERGE")
print("=" * 74)

_merged = guarded(R.load_runs, [_DIR1, _DIR2])
check("1a  both directories' decisions are in one population",
      len(getattr(_merged, "decisions", [])), 6)
check("1a-i  ...and every patient's summary came with them",
      sorted(getattr(_merged, "summaries", {})), sorted([_PA, _PB, _PC]))
check("1b  run_dir stays the FIRST, a single string, because six sites read "
      "it and every one of them wants one directory",
      getattr(_merged, "run_dir", None), _DIR1)
check("1b-i  ...and run_dirs is the honest full answer, in the order named",
      list(getattr(_merged, "run_dirs", ())), [_DIR1, _DIR2])
check("1b-ii ...and every manifest is carried, not only the first's",
      [d for d, _m in getattr(_merged, "manifests", ())], [_DIR1, _DIR2])

check("1c  ORDINALS ARE RECOMPUTED OVER THE UNION -- two directories' own "
      "ordinals both start at zero, so inheriting them would give two "
      "patients one compact custom_id",
      sorted(getattr(_merged, "patient_order", {}).values()), [0, 1, 2])
check("1c-i  ...and they follow sorted patient id, which is load_run's own "
      "rule applied to the merge",
      [p for p, _o in sorted(getattr(_merged, "patient_order", {}).items(),
                             key=lambda kv: kv[1])],
      sorted([_PA, _PB, _PC]))
check("1c-ii every decision carries the RECOMPUTED index rather than its "
      "own directory's",
      sorted({(d.patient_id, d.patient_index) for d in _merged.decisions}),
      sorted({(p, o) for p, o in _merged.patient_order.items()
              if p in {d.patient_id for d in _merged.decisions}}))

check("1d  the order is the merged population's own sort key, so naming the "
      "directories the other way round builds the IDENTICAL batch",
      [d.key for d in R.load_runs([_DIR2, _DIR1]).decisions],
      [d.key for d in _merged.decisions])

_keys_both = [_merged.decisions[0].key, _merged.decisions[-1].key]
check("1e  an include list spanning both directories resolves as ONE "
      "selection",
      [d.key for d in guarded(R.select_included_decisions,
                              _merged.decisions, _keys_both)],
      _keys_both)
check("1e-i  non-degeneracy: those two keys ARE from different directories",
      len({_keys_both[0][0], _keys_both[1][0]}), 2)


print()
print("=" * 74)
print("2. A SINGLE DIRECTORY IS BYTE-COMPATIBLE -- driven, not claimed")
print("=" * 74)

_one = R.load_run(_DIR1)
_one_via = guarded(R.load_runs, [_DIR1])
check("2a  same run_dir", getattr(_one_via, "run_dir", None), _one.run_dir)
check("2a-i  same manifest", getattr(_one_via, "manifest", None), _one.manifest)
check("2a-ii same summaries",
      getattr(_one_via, "summaries", None), _one.summaries)
check("2a-iii same patient_order",
      getattr(_one_via, "patient_order", None), _one.patient_order)
check("2a-iv same problems list",
      getattr(_one_via, "problems", None), _one.problems)
check("2b  the decisions are the same objects in the same order, compared "
      "attribute by attribute rather than by identity",
      [tuple(getattr(d, f) for f in R.Decision.__slots__)
       for d in getattr(_one_via, "decisions", [])],
      [tuple(getattr(d, f) for f in R.Decision.__slots__)
       for d in _one.decisions])
check("2b-i  non-degeneracy: that comparison covers all nine attributes and "
      "a non-empty population",
      (len(R.Decision.__slots__), len(_one.decisions) > 0), (9, True))
check("2c  run_dirs defaults to the ONE directory rather than to empty, so a "
      "consumer never has to ask whether the single case populated it",
      list(getattr(_one_via, "run_dirs", ())), [_DIR1])


print()
print("=" * 74)
print("3. THE THREE REFUSALS, each with a CLEAN CONTROL")
print("=" * 74)

check("3a  the same directory named twice is a refusal",
      refusal_code(R.load_runs, [_DIR1, _DIR1]), "run_dir_duplicate")
check("3a-CONTROL  ...and naming two different ones is not",
      isinstance(guarded(R.load_runs, [_DIR1, _DIR2]), R.RunInput), True)

_DIR3 = make_run("run_three", {_PA: [("NCT00000009", R.ARM_INCLUSION,
                                      ["nine A"])]},
                 summary_of={_PA: "A COMPLETELY DIFFERENT PATIENT RECORD"})
check("3b  one patient with two DIFFERENT summaries is a refusal -- the "
      "summary is what every rating is audited against",
      refusal_code(R.load_runs, [_DIR1, _DIR3]),
      "run_merge_summary_conflict")
_DIR4 = make_run("run_four", {_PA: [("NCT00000009", R.ARM_INCLUSION,
                                     ["nine A"])]})
check("3b-CONTROL  ...and the SAME patient with the SAME summary merges, "
      "which is what says 3b is about the text and not about the id",
      len(guarded(R.load_runs, [_DIR1, _DIR4]).decisions), 5)

_DIR5 = make_run("run_five", {
    _PA: [("NCT00000001", R.ARM_INCLUSION, ["one A", "one B"])]})
check("3c  one decision key in two directories is a refusal -- an include "
      "key would name two decisions and one custom_id would be minted twice",
      refusal_code(R.load_runs, [_DIR1, _DIR5]), "run_merge_key_conflict")
check("3c-CONTROL  ...and a directory whose keys are all new merges",
      len(guarded(R.load_runs, [_DIR1, _DIR2]).decisions), 6)
check("3c-i  the refusal names the two directories, so the fix does not need "
      "a second run to find them",
      all(d in guarded(lambda: R.load_runs([_DIR1, _DIR5]))
          for d in (_DIR1, _DIR5)), True)

check("3d  no directory at all is a refusal rather than a silent default",
      refusal_code(R.load_runs, []), "run_dir_invalid")
check("3d-i  a directory with no manifest still refuses by its own name",
      refusal_code(R.load_runs, [TMP, _DIR2]), "run_dir_invalid")


print()
print("=" * 74)
print("4. THE FLAG, AND ONE SESSION END TO END THROUGH _prepare")
print("=" * 74)

_args_one = R._parse_args(["--dry-run", "--run-dir", _DIR1])
check("4a  one --run-dir yields a one-element list, which load_runs "
      "delegates straight to load_run",
      _args_one.run_dir, [_DIR1])
_args_two = R._parse_args(["--dry-run", "--run-dir", _DIR1,
                           "--run-dir", _DIR2])
check("4a-i  ...and the flag is REPEATABLE",
      _args_two.run_dir, [_DIR1, _DIR2])
check("4a-ii naming none still yields None, so the default can be resolved "
      "lazily rather than baked into the parser",
      R._parse_args(["--dry-run"]).run_dir, None)
# THE HELP TEXT IS THE ONLY PLACE AN OPERATOR LEARNS THE FLAG REPEATS. A
# capability nobody is told about is a capability nobody uses, and the next
# person would run two sessions again.
# `--help` EXITS, and SystemExit is a BaseException, so `guarded` does not
# catch it -- the first draft of this check printed the whole help to stdout
# and took the run with it.
import contextlib as _ctx                                       # noqa: E402

_buf = io.StringIO()
with _ctx.redirect_stdout(_buf):
    try:
        R._parse_args(["--help"])
    except SystemExit:
        pass
_HELP = _buf.getvalue()
check("4a-iii the help text says the flag REPEATS and says what the merge "
      "refuses, because an operator who is not told cannot use it",
      ("REPEATABLE" in _HELP, "refusal" in _HELP), (True, True))

_keys_path = os.path.join(TMP, "keys.txt")
_span = [d for d in R.load_runs([_DIR1, _DIR2]).decisions
         if d.patient_id in (_PA, _PC)]
with io.open(_keys_path, "w", encoding="utf-8") as _fh:
    _fh.write("# a population that spans two run directories\n")
    for _d in _span:
        _fh.write(R.INCLUDE_KEY_SEPARATOR.join(
            (_d.patient_id, _d.nct_id, _d.arm, str(_d.index))) + "\n")

_prepared = guarded(R._prepare, R._parse_args(
    ["--dry-run", "--blind", "--run-dir", _DIR1, "--run-dir", _DIR2,
     "--include-keys", _keys_path,
     "--output-dir", os.path.join(TMP, "out")]))
check("4b  _prepare builds ONE request index over both directories",
      len(_prepared[1].requests) if isinstance(_prepared, tuple) else _prepared,
      len(_span))
check("4b-i  ...and every requested decision is in it",
      sorted(_prepared[1].by_custom_id[r["custom_id"]].key
             for r in _prepared[1].requests)
      if isinstance(_prepared, tuple) else _prepared,
      sorted(d.key for d in _span))
check("4b-ii ...spanning both patients, which is what says the merge and not "
      "one directory answered",
      len({_prepared[1].by_custom_id[r["custom_id"]].patient_id
           for r in _prepared[1].requests})
      if isinstance(_prepared, tuple) else _prepared, 2)
check("4b-iii ...and args.run_dir was normalised to the resolved list, so "
      "the banner, the state file and the manifest report paths the loader "
      "actually opened",
      _prepared[0].run_dirs if isinstance(_prepared, tuple) else _prepared,
      (_DIR1, _DIR2))

# A KEY THAT NAMES NO DECISION IN EITHER DIRECTORY still refuses -- the merge
# must not turn "not in this run" into "not in the one I looked at".
_bad_keys = os.path.join(TMP, "bad.txt")
with io.open(_bad_keys, "w", encoding="utf-8") as _fh:
    _fh.write(f"{_PA}|NCT09999999|inclusion|0\n")
check("4c  a key that names no decision in ANY of the directories refuses",
      refusal_code(R._prepare, R._parse_args(
          ["--dry-run", "--blind", "--run-dir", _DIR1, "--run-dir", _DIR2,
           "--include-keys", _bad_keys,
           "--output-dir", os.path.join(TMP, "out2")])),
      "include_keys_unmatched")


print()
print("=" * 74)
print("5. THE REFERENCE-DATE GUARD RUNS FOR EVERY DIRECTORY")
print("=" * 74)

_DIR6 = make_run("run_six", {_PB.replace("bbbb", "dddd"):
                             [("NCT00000006", R.ARM_INCLUSION, ["six A"])]})
_m6 = os.path.join(_DIR6, "manifest.json")
with io.open(_m6, encoding="utf-8") as _fh:
    _payload = json.load(_fh)
_payload["environment"]["age_reference_date"] = "1999-01-01"
with io.open(_m6, "w", encoding="utf-8") as _fh:
    json.dump(_payload, _fh)

check("5a  a SECOND directory whose reference date has moved refuses -- "
      "reading only the first manifest would let it through on exactly the "
      "invocations the merge made possible",
      refusal_code(R._prepare, R._parse_args(
          ["--dry-run", "--blind", "--run-dir", _DIR1, "--run-dir", _DIR6,
           "--output-dir", os.path.join(TMP, "out3")])),
      "reference_date_mismatch")
check("5a-CONTROL  ...and the same pair with the date restored does not",
      isinstance(guarded(R._prepare, R._parse_args(
          ["--dry-run", "--blind", "--run-dir", _DIR1, "--run-dir", _DIR2,
           "--output-dir", os.path.join(TMP, "out4")])), tuple), True)
check("5a-i  non-degeneracy: the rules really do render a reference date, so "
      "the guard is not passing over a None",
      bool(_REF), True)
check("5a-ii ...and it is config.DATA_SNAPSHOT_DATE's, which is what makes "
      "the mismatch a real rubric fault rather than a fabrication artifact",
      _REF, config.DATA_SNAPSHOT_DATE)


print()
print("=" * 74)
print("6. ISOLATION")
print("=" * 74)

with io.open(_SRC, "rb") as _fh:
    _after = hashlib.sha256(_fh.read()).hexdigest()
check("6a  oncotriage/evaluation/rater.py is byte-unchanged", _after,
      _SRC_BEFORE)
check("6a-i  every directory this file made is inside the temp tree",
      all(os.path.abspath(d).startswith(os.path.abspath(TMP))
          for d in (_DIR1, _DIR2, _DIR3, _DIR4, _DIR5, _DIR6)), True)

shutil.rmtree(TMP, ignore_errors=True)
check("6b  the temp tree is removed and asserted gone", os.path.exists(TMP),
      False)


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
Created on Mon Sep 8 2026

@author: ramyalsaffar
"""
