# CI Test Classification and Runner
###################################

"""Which tests CI can run, which it cannot, and why -- with the reason recorded.

THE SUITE IS NOT PYTEST and never was: every check runs at module level and the
exit code is set in a `__main__` block, so there is nothing to collect and no
marker to attach. The classification therefore lives here, beside the runner,
rather than as decorators in 32 files.

THE FIVE BUCKETS
----------------
    A  parallel-safe in CI -- no network, no keys, no spend, no live server, no
       live Qdrant, no data outside the repository
    B  serial -- a member of the collision matrix in tests/run_serial_tests.py
    C  needs a live Qdrant endpoint
    D  costs money (a billed model call) or needs a live HTTP server
    E  needs data files that are not committed

CLASSIFICATION WAS DERIVED BY RUNNING, NOT BY READING. Every non-serial file was
executed with the project root pointed at a directory holding ONLY the
directory skeleton `.github/scripts/provision_ci_paths.py` creates -- which is
exactly what a CI runner has. The evidence string on each entry below is the
observed outcome, not an inference from its imports. A grep-based first pass
disagreed with the run in both directions: it marked files "needs Qdrant"
because they name `get_qdrant_client` inside a `deps.set_override` stand-in, and
it missed the four that die on a licence-gated file they never mention.

B AND E OVERLAP, AND THE BRIEF THIS WAS BUILT FROM ASSUMED THEY DO NOT.
Four of the five serial members ALSO need uncommitted data: the registry audit
needs the UMLS Metathesaurus (`MRCONSO*.RRF`, licence-gated, ~1.5 GB, not
redistributable), its control runs that audit as its own baseline subprocess,
the snapshot-date test runs `test_fhir_ecog_surfacing.py` as a subprocess and
that needs a generated Synthea corpus, and `test_degraded_dependencies.py`
needs the real patient corpus. Only `test_package_invariants.py` is runnable on
a hosted runner. Each such entry carries BOTH letters below: the bucket that
decides HOW it runs (B, through `make serial-tests`, never directly) and the
`needs` field that decides WHETHER it can. The workflow gates on `needs`.

WHY A DECLARED TABLE WITH AN ENFORCED COMPLETENESS CHECK. Deriving membership at
run time would mean running every test to find out which ones CI can run, which
is the thing being decided. So the table is declared -- and `check_complete()`
fails when any `tests/test_*.py` is absent from it, so a new test file turns
into a NAMED CI failure rather than silently never running. That is the same
trade `_EXEC_ALLOWLIST` and the never-read-name exemptions make elsewhere in
this project: a closed list is fine as long as something fails when it goes
stale.

Run from terminal:
    python .github/scripts/ci_test_buckets.py --list
    python .github/scripts/ci_test_buckets.py --check
    python .github/scripts/ci_test_buckets.py --run A
    python .github/scripts/ci_test_buckets.py --serial-preconditions

Exit codes:
    0 -- the requested action succeeded
    1 -- a test failed, or the table is incomplete
    2 -- a named test file is missing from tests/
"""

import argparse
import concurrent.futures
import os
import subprocess
import sys
import time


_CODE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TESTS_DIR = os.path.join(_CODE_DIR, "tests")


# ===========================================================================
# THE SERIAL MEMBERSHIP COMES FROM THE RUNNER, NOT FROM HERE
# ===========================================================================
# tests/run_serial_tests.py owns the collision matrix; it derived it from the
# code and it is what `make serial-tests` executes. Re-typing those five names
# here would be a second declaration that can disagree with the one that runs.
def _serial_members():
    """Return the basenames in tests/run_serial_tests.py's SERIAL_TESTS."""
    sys.path.insert(0, _TESTS_DIR)
    try:
        import run_serial_tests
    except ImportError as exc:                       # pragma: no cover
        raise RuntimeError(
            f"cannot import tests/run_serial_tests.py, which owns the "
            f"collision matrix: {exc}"
        ) from exc
    return tuple(os.path.basename(name) for name, _ in run_serial_tests.SERIAL_TESTS)


# ===========================================================================
# THE TABLE
# ===========================================================================
# (bucket, needs, evidence)
#   bucket   -- A/B/C/D/E, the single bucket the brief asks for
#   needs    -- None when CI can run it; otherwise what is absent on a runner
#   evidence -- the observed line that put it there
#
# `needs` is separate from `bucket` because a B member can also be unrunnable,
# and collapsing the two would either hide the collision constraint or hide the
# data constraint. Every non-None `needs` was read off a real run.
_A = "A"; _B = "B"; _C = "C"; _D = "D"; _E = "E"

BUCKETS = {
    # ---- A: verified green with only the directory skeleton ---------------
    "test_agent_ablation_flag_passthrough.py": (
        _A, None, "ran green in 8.3s; registries and clients replaced through deps"),
    "test_agent_age_units_and_sex_filter.py": (
        _A, None, "ran green in 1.8s; plants into in-memory copies, reads no git"),
    "test_agent_mesh_boost_and_quality_gate.py": (
        _A, None, "ran green in 1.8s once the two vendored MeSH lookups are seeded"),
    "test_agent_prompt_version.py": (
        _A, None,
        "ran green in 0.74s wall (0.00s of checks) in a `git ls-files` checkout "
        "with ONLY the skeleton: 41 passed, 0 failed, exit 0. It renders 16 "
        "strings and reads one committed JSON file -- no network, no keys, no "
        "spend, no database, no subprocess, no corpus, and NO GIT, which is the "
        "point of it (a commit recedes; the reference is the golden snapshot)"),
    "test_agent_retrieval_observability.py": (
        _A, None, "ran green in 3.5s; needs .env to EXIST, makes no live call"),
    "test_agent_summary_cancer_stage.py": (
        _A, None,
        "ran green in 2.0s, 53 checks; every patient is a literal dict, the "
        "plants go into an in-memory copy of oncotriage/agent/patient.py, and "
        "it reads no git, no corpus and no database"),
    "test_agent_structured_outputs.py": (
        _A, None,
        "ran green in 1.9s, 134 checks; the Stage 5 response schema and the "
        "refusal path. Every model response is a literal served through a "
        "deps stub, every plant doctors a COPY of a dict the shipped builder "
        "returns or an in-memory AST, and it execs nothing -- no network, no "
        "keys, no spend, no git, no corpus, no database, no subprocess"),
    "test_agent_trial_verdict_normalization.py": (
        _A, None, "ran green in 1.9s; every model response is a literal via a deps stub"),
    "test_dashboard_reproducibility_tab.py": (
        _A, None,
        "parallel-safe and needs no external data (green in 7.2s on streamlit "
        "1.45.1, identical in a depth-1 clone) -- but RED on streamlit 1.46.0, "
        "which is what pyproject.toml pins. The committed golden snapshot "
        "records element type 'vertical'; 1.46.0 emits 'flex_container'. That "
        "is a repository defect, not a CI one, and it is deliberately NOT "
        "suppressed here -- see the CI report."),
    "test_extraction_histology.py": (
        _A, None, "ran green in 0.1s, 133 checks, identical in a depth-1 clone"),
    "test_extraction_stage_m_category.py": (
        _A, None, "ran green in 2.8s; every fixture is a literal dict"),
    "test_extraction_stage_non_oncology_guard.py": (
        _A, None, "ran green in 2.6s; no corpus, no git"),
    "test_fhir_birth_date_and_demographics.py": (
        _A, None, "ran green in 2.3s"),
    "test_observability_logging.py": (
        _A, None, "ran green in 9.2s; all six stages driven with deps stand-ins"),
    "test_paths_glob_determinism.py": (
        _A, None, "ran green in 0.1s against the skeleton; asserts 13 resolvers"),
    "test_registries_cancer_codes_and_stage_extraction.py": (
        _A, None, "ran green in 0.2s"),
    "test_storage_inference_logging_contract.py": (
        _A, None, "ran green in 1.8s; temp SQLite only"),
    "test_storage_query_layer.py": (
        _A, None, "ran green in 2.6s, 191 checks, identical in a depth-1 clone"),

    # ---- B: the collision matrix ------------------------------------------
    # Bucket assignment is asserted against run_serial_tests.py in check_complete().
    "test_registries_cancer_code_claims_audit.py": (
        _B, "UMLS MRCONSO*.RRF (licence-gated, ~1.5 GB, not redistributable)",
        "'CANNOT RUN: UMLS MRCONSO not found' -- refuses rather than passing vacuously"),
    "test_registries_cancer_code_claims_audit_control.py": (
        _B, "UMLS MRCONSO*.RRF (it runs the audit above as its baseline)",
        "'File 42 exited 1 with NO defect planted, so a non-zero exit proves nothing'"),
    "test_degraded_dependencies.py": (
        _B, "the real Synthea patient corpus",
        "IndexError on an empty corpus list, then NameError: '_dry_counts' (see report)"),
    "test_config_snapshot_date_rot.py": (
        _B, "the generated scratch_ecog Synthea corpus (it runs ecog_surfacing)",
        "'baseline for test_fhir_ecog_surfacing.py is usable' failed"),
    "test_package_invariants.py": (
        _B, None,
        "ran green in 35.0s inside `make serial-tests` with only the skeleton"),

    # ---- C: needs a live Qdrant endpoint -----------------------------------
    "test_mcp_server_stdio_contract.py": (
        _C, "a live Qdrant collection and a corpus bundle path",
        "sections 4-6 make real Qdrant round trips; died resolving a bundle path"),

    # ---- E: needs uncommitted data ----------------------------------------
    "test_ablation_db_isolation.py": (
        _E, "the production ablation_results.db",
        "'the digest is a real one, not absent on both sides (non-degeneracy)' failed"),
    "test_agent_patient_hash_coverage.py": (
        _E, "the real patient corpus (sections 4 and 7 parse bundles read-only)",
        "2 failures, both corpus non-degeneracy guards"),
    "test_docker_qdrant_override_and_readiness.py": (
        _E, "a real .env carrying an https Qdrant Cloud URL",
        "121 passed, 1 failed: 'the url is a real https endpoint (non-degenerate)'"),
    "test_fhir_ecog_surfacing.py": (
        _E, "the generated scratch_ecog Synthea corpus",
        "'scratch corpus not found at .../01- Patients/scratch_ecog/fhir/'"),
    "test_fhir_parser_dict_input.py": (
        _E, "the real patient corpus",
        "'a corpus bundle was found (non-degeneracy)' failed"),
    "test_indexer_admission_filters.py": (
        _E, "mesh_non_oncology_lookup.json (UMLS-derived, deliberately not vendored)",
        "KeyError: 'diabetes mellitus' -- the non-oncology lookup is absent"),
    "test_monitoring_ecog_availability_drift.py": (
        _E, "the production inferences.db",
        "'the production table was readable, so the count is a real number' failed"),
    "test_registries_mesh_pan_cancer_resolution.py": (
        _E, "the three UMLS-derived SNOMED->CUI->MeSH crosswalks (not vendored)",
        "resolution fell back to fuzzy_stem/unmapped where snomed was expected"),
    "test_storage_ecog_logging.py": (
        _E, "the generated scratch_ecog Synthea corpus",
        "'scratch corpus not found ... Generate it first: 04- FHIR Generate Data.py'"),
    "test_storage_wipe_all_tables.py": (
        _E, "the production inferences.db (it reads its real schema)",
        "'the production schema was read and is non-degenerate' failed"),
    "test_storage_write_durability.py": (
        _E, "the production inferences.db",
        "'9c ...and it was readable, so that comparison is not None == None' failed"),
}


# Not under tests/ -- the numbered entry points and the two fixture harnesses.
# Listed so the classification the brief asked for is complete, and so nobody
# wires one of them into CI later without meeting this table first.
NON_TEST_ENTRY_POINTS = {
    "18- FastAPI Server Test.py": (
        _D, "a live server on :8000 AND real spend",
        "every POST is a billed Stage 5 call, $0.13-$0.17 per patient"),
    "19- FastAPI Server Batch Test.py": (
        _D, "a live server on :8000 AND real spend",
        "same; slices fhir_files[410:412], so it also needs the corpus"),
    "fixture_capture.py": (
        _D, "real spend", "twelve real end-to-end runs; its own docstring says COSTS MONEY"),
    "fixture_replay.py": (
        _C, "a live Qdrant whose collection digest matches the fixtures",
        "refuses at the pinned-collection gate; free, but not a CI gate"),
    "13- LangGraph Agent.py": (
        _D, "real spend when RUN_TEST_ON_EXECUTE = True",
        "one billed Stage 5 call; edit-to-arm, so a bare run is a no-op"),
    "measure_medcpt_scores.py": (
        _C, "a live Qdrant index and the corpus",
        "runs Stages 1-3 over a seeded 30-patient sample"),
}


def in_bucket(letter):
    """Test basenames in `letter`, sorted."""
    return sorted(n for n, (b, _, _) in BUCKETS.items() if b == letter)


def runnable_in_ci(letter):
    """Test basenames in `letter` whose `needs` is None."""
    return sorted(n for n, (b, needs, _) in BUCKETS.items()
                  if b == letter and needs is None)


def check_complete():
    """Every tests/test_*.py is classified, and B agrees with the serial runner.

    Returns a list of problem strings; empty means consistent.
    """
    problems = []

    on_disk = {f for f in os.listdir(_TESTS_DIR)
               if f.startswith("test_") and f.endswith(".py")}
    declared = set(BUCKETS)

    for name in sorted(on_disk - declared):
        problems.append(
            f"UNCLASSIFIED: tests/{name} is not in BUCKETS. Add it with the "
            f"outcome of a real run against the CI skeleton -- do not guess.")
    for name in sorted(declared - on_disk):
        problems.append(
            f"STALE: BUCKETS names tests/{name}, which does not exist.")

    # The B column must be exactly the serial runner's list, in both
    # directions. A member added to run_serial_tests.py and not here would be
    # run by `make serial-tests` while this table called it parallel-safe.
    try:
        serial = set(_serial_members())
    except RuntimeError as exc:
        problems.append(str(exc))
        return problems

    declared_b = set(in_bucket(_B))
    for name in sorted(serial - declared_b):
        problems.append(
            f"MATRIX DRIFT: tests/{name} is in run_serial_tests.py's "
            f"SERIAL_TESTS but is not bucket B here.")
    for name in sorted(declared_b - serial):
        problems.append(
            f"MATRIX DRIFT: tests/{name} is bucket B here but is not in "
            f"run_serial_tests.py's SERIAL_TESTS.")

    return problems


def serial_preconditions():
    """Return (ready, rows). `ready` is True when every B member can run.

    Rows are (name, needs, present) so the caller can print what is missing
    rather than just refusing. Presence is decided by asking the filesystem for
    the specific input each member named when it refused -- not by a flag
    somebody sets, which would be a second declaration.
    """
    sys.path.insert(0, _CODE_DIR)
    from oncotriage import paths

    def _has_umls():
        import glob
        return bool(glob.glob(os.path.join(paths.data_MeSH_path, "MRCONSO*.RRF")))

    def _has_corpus():
        d = paths.data_fhir_path
        return os.path.isdir(d) and any(f.endswith(".json") for f in os.listdir(d))

    def _has_scratch_ecog():
        return os.path.isdir(os.path.join(paths.data_patient_path,
                                          "scratch_ecog", "fhir"))

    probes = {
        "test_registries_cancer_code_claims_audit.py": _has_umls,
        "test_registries_cancer_code_claims_audit_control.py": _has_umls,
        "test_degraded_dependencies.py": _has_corpus,
        "test_config_snapshot_date_rot.py": _has_scratch_ecog,
        "test_package_invariants.py": lambda: True,
    }

    rows = []
    for name in in_bucket(_B):
        needs = BUCKETS[name][1]
        try:
            present = probes[name]()
        except Exception as exc:                     # noqa: BLE001 - reported
            present = False
            needs = f"{needs} (probe raised {type(exc).__name__}: {exc})"
        rows.append((name, needs, present))

    return all(present for _, _, present in rows), rows


def _run_one(name, root):
    path = os.path.join("tests", name)
    env = dict(os.environ)
    if root:
        env["ONCOTRIAGE_MAIN_PATH"] = root
    start = time.time()
    completed = subprocess.run([sys.executable, path], cwd=_CODE_DIR, env=env,
                               capture_output=True, text=True)
    return name, completed.returncode, time.time() - start, completed.stdout, completed.stderr


def run_bucket(letter, root=None, workers=None):
    """Run every CI-runnable member of `letter`. Returns an exit code.

    Bucket A members write nothing in the repository -- that is the derivation
    in tests/run_serial_tests.py, and it is what makes concurrency safe here.
    Bucket B is NEVER run through this function: it goes through
    `make serial-tests`, which holds the lock and fixes the order.
    """
    if letter == _B:
        raise RuntimeError(
            "bucket B must not be run from here. `make serial-tests` is the "
            "only entry point: two of its members rewrite source in place and "
            "restore it, the order is load-bearing, and the runner holds an "
            "flock that stops two runs from interleaving their restores.")

    # A BUCKET THAT RUNS NOTHING MUST NOT EXIT 0. `--run Z` on a letter that is
    # not a bucket, or a bucket whose every member became unrunnable, would
    # otherwise print "ran 0, failed 0" and return success -- a CI step that
    # tested nothing and looked exactly like one that passed. That is the defect
    # pass 20g fixed in Files 18 and 19, and it is easier to reintroduce in a
    # runner than anywhere else.
    members = in_bucket(letter)
    if not members:
        raise RuntimeError(
            f"{letter!r} is not a bucket with any members. Known buckets: "
            f"{sorted({b for b, _, _ in BUCKETS.values()})}.")

    names = runnable_in_ci(letter)
    if not names:
        raise RuntimeError(
            f"bucket {letter} has {len(members)} member(s) and NONE of them is "
            f"runnable in CI, so this step would have tested nothing and "
            f"exited 0. Members and what each needs:\n" +
            "\n".join(f"    {n}: {BUCKETS[n][1]}" for n in members))

    skipped = [n for n in members if n not in names]

    if workers is None:
        workers = min(4, (os.cpu_count() or 2))

    print("=" * 74)
    print(f"BUCKET {letter} — {len(names)} test files, {workers} at a time")
    print("=" * 74)
    for name in skipped:
        print(f"  NOT RUN  {name}\n           needs: {BUCKETS[name][1]}")
    if skipped:
        print()

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run_one, n, root) for n in names]
        for future in concurrent.futures.as_completed(futures):
            name, rc, elapsed, out, err = future.result()
            results.append((name, rc, elapsed, out, err))
            print(f"  {'PASS' if rc == 0 else 'FAIL':<5} exit={rc:<3} "
                  f"{elapsed:6.1f}s  {name}")

    failed = [r for r in results if r[1] != 0]

    if failed:
        print()
        print("=" * 74)
        print(f"FAILURE OUTPUT — {len(failed)} of {len(results)}")
        print("=" * 74)
        for name, rc, _, out, err in sorted(failed):
            print()
            print("-" * 74)
            print(f"{name}  (exit {rc})")
            print("-" * 74)
            sys.stdout.write(out)
            if err:
                sys.stdout.write("--- stderr ---\n")
                sys.stdout.write(err)

    print()
    print("=" * 74)
    print(f"BUCKET {letter} SUMMARY")
    print("=" * 74)
    for name, rc, elapsed, _, _ in sorted(results):
        print(f"  {'PASS' if rc == 0 else 'FAIL':<5} exit={rc:<3} {elapsed:6.1f}s  {name}")
    print()
    print(f"  ran {len(results)}, failed {len(failed)}, not run {len(skipped)}")
    return 1 if failed else 0


def _print_table():
    letters = [(_A, "parallel-safe in CI"),
               (_B, "serial (collision matrix)"),
               (_C, "needs a live Qdrant endpoint"),
               (_D, "costs money or needs a live HTTP server"),
               (_E, "needs uncommitted data files")]
    for letter, label in letters:
        names = in_bucket(letter)
        if not names:
            continue
        print()
        print(f"BUCKET {letter} — {label}  ({len(names)})")
        print("-" * 74)
        for name in names:
            _, needs, evidence = BUCKETS[name]
            mark = "   " if needs is None else "!! "
            print(f"  {mark}{name}")
            print(f"       evidence: {evidence}")
            if needs is not None:
                print(f"       NOT RUNNABLE IN CI, needs: {needs}")
    print()
    print("Entry points outside tests/ (never wired into CI)")
    print("-" * 74)
    for name, (letter, needs, evidence) in sorted(NON_TEST_ENTRY_POINTS.items()):
        print(f"  [{letter}] {name}")
        print(f"       {evidence}")
        print(f"       needs: {needs}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true",
                       help="print the whole classification and exit")
    group.add_argument("--check", action="store_true",
                       help="fail if any tests/test_*.py is unclassified")
    group.add_argument("--run", metavar="LETTER",
                       help="run every CI-runnable member of a bucket")
    group.add_argument("--serial-preconditions", action="store_true",
                       help="report whether `make serial-tests` can run here")
    parser.add_argument("--root", default=os.environ.get("ONCOTRIAGE_MAIN_PATH"),
                        help="project root passed to each test as "
                             "ONCOTRIAGE_MAIN_PATH")
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args(argv)

    if args.list:
        _print_table()
        return 0

    if args.check:
        problems = check_complete()
        if problems:
            print("CLASSIFICATION IS INCONSISTENT:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print(f"Classification consistent: {len(BUCKETS)} test files, "
              f"{len(in_bucket(_A))} in bucket A "
              f"({len(runnable_in_ci(_A))} runnable in CI), "
              f"{len(in_bucket(_B))} in bucket B "
              f"({len(runnable_in_ci(_B))} runnable in CI).")
        return 0

    if args.serial_preconditions:
        ready, rows = serial_preconditions()
        print("SERIAL SUITE PRECONDITIONS")
        print("-" * 74)
        for name, needs, present in rows:
            print(f"  {'PRESENT' if present else 'ABSENT ':<8} {name}")
            if not present:
                print(f"           needs: {needs}")
        print()
        print(f"serial_ready={'true' if ready else 'false'}")
        return 0

    missing = [n for n in in_bucket(args.run)
               if not os.path.isfile(os.path.join(_TESTS_DIR, n))]
    if missing:
        print("MISSING test file(s):")
        for name in missing:
            print(f"  - tests/{name}")
        return 2

    return run_bucket(args.run, root=args.root, workers=args.workers)


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Aug  8 2026

@author: ramyalsaffar
"""
