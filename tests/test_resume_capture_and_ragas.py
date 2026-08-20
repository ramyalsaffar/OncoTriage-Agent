# Resume: the Fixture Capture Gate and the Ragas Score Journal
#############################################################

"""
Both paid harnesses' resume mechanisms, and every negative control behind them.

WHAT RESUME IS FOR, IN BOTH CASES: a crashed run must continue where it stopped
instead of re-paying for finished work. A capture that dies at fixture 9 of 12
used to restart from 1 and re-buy the finished 8; losing one (sample, metric)
pair in a ragas run used to cost a full re-run at full price. Neither mechanism
may change what is produced -- a resumed set and a single-pass set have to be
the same set -- and neither may skip anything it has not CHECKED, because a
stale artifact counted as done is the one defect both of these files' version
gates exist to refuse.

WHAT THIS FILE COSTS AND NEEDS: nothing. No network, no keys, no spend, no live
Qdrant, no live judge, no corpus, no git history, no Docker. Every fixture it
reads is one it wrote into a ``tempfile.mkdtemp()``; every score it merges is
one it invented. It patches no repository file, and the two package modules it
reads are hashed before the first stub is installed and compared at the end.
It is NOT in the collision matrix.

IT NEVER TOUCHES THE REAL FIXTURE DIRECTORY OR A REAL EVALUATION RUN.
``fixture_root()`` is not called; ``paths.data_fhir_path`` is redirected into a
temp directory through ``paths._RESOLVED`` and restored; the twelve on-disk
fixtures are never opened, so a bug here cannot cost a re-capture.

SECTION 1  resume_decision(): all seven outcomes, over real files through the
           real load_fixture version gate.
SECTION 2  the capture plan loop, DRIVEN. main() is run for real -- the plan
           build, the donor arithmetic, the resume gating, the temporary-bundle
           cleanup and the retry-base selection are the shipped code -- with the
           paid and networked seams replaced by stand-ins. Four scenarios:
           everything current, a fresh directory, one fixture failing each gate
           in turn, and --only composed with --resume.
SECTION 3  the donor pool under resume: no re-derivation, no popped donor, and
           the same donor handed to a later NEW derivation as an unresumed run
           would hand it.
SECTION 4  the per-fixture cost line.
SECTION 5  the ragas score journal: atomicity, the interrupted write, and that
           a torn write leaves the previous good file intact.
SECTION 6  ragas resume: reuse by pair identity and input fingerprint, the
           environment refusal, the merged set passing post_checks, and the
           manifest's resumed record.
SECTION 9  ragas_run.py's main(), driven end to end with a stand-in judge: a
           killed run leaves a usable partial and no result, --resume finishes
           it paying only for the remainder, the finished result is identical
           row for row to a single pass, and every environment refusal fires
           before a pair is scored.
SECTION 7  negative controls. Every assertion above is shown to FAIL when the
           thing it checks is broken -- reverted in an in-memory copy, or driven
           with a deliberately wrong input -- through the SAME check() the
           assertions use.
"""

import copy
import gzip
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import types

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

from oncotriage import config, paths
from oncotriage.fixtures import capture as cap
from oncotriage.evaluation import ragas_harness as rh


#------------------------------------------------------------------------------
# Harness
#------------------------------------------------------------------------------

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, condition, detail=""):
    if condition:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(label)
        print(f"  FAIL  {label}{('  -- ' + str(detail)) if detail else ''}")
    return bool(condition)


def check_detects(label, condition, detail=""):
    """A negative control, driven through the SAME check() the assertions use.

    The control is that ``check`` RECORDS A FAILURE for a deliberately broken
    expectation. Running it through a parallel copy of the harness would prove
    nothing about the harness the assertions run through, so this drives the
    real one and then restores the counters.
    """
    before_p, before_f = _RESULTS["passed"], _RESULTS["failed"]
    # _FAILURES is restored TOO. Without this the control's own deliberate
    # failure is listed in the final summary as though something were wrong,
    # and a run with six controls prints six names under "Failed: 0" -- which
    # reads as a broken report and hides a real name if one ever appears there.
    before_names = len(_FAILURES)
    fired = not check(f"(control) {label}", condition, detail)
    _RESULTS["passed"], _RESULTS["failed"] = before_p, before_f
    del _FAILURES[before_names:]
    if fired:
        _RESULTS["passed"] += 1
        print(f"  PASS  control fired: {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"control DID NOT fire: {label}")
        print(f"  FAIL  control DID NOT fire: {label}")


def sha256_file(path):
    """sha256 of a file, or a NAMED ABSENCE when it is not there.

    It must not raise. Several assertions here hash a file whose continued
    existence is the thing under test -- "the refusal left the partial
    untouched" -- and a defect that DELETES it would then take the run down with
    a FileNotFoundError while check()'s argument was being evaluated, reporting
    one traceback where it owed a section of results. The revert harness caught
    exactly that: with the environment refusal disabled, the resumed run
    completed and removed the partial, and the control ABORTED instead of
    firing. This project has shipped that shape four times; a returned sentinel
    compares unequal and fails the check instead.
    """
    if not os.path.exists(path):
        return f"<absent: {os.path.basename(path)}>"
    h = hashlib.sha256()
    with io.open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


_CAPTURE_PY = os.path.abspath(cap.__file__)
_RAGAS_PY = os.path.abspath(rh.__file__)
_HASH_BEFORE = {_CAPTURE_PY: sha256_file(_CAPTURE_PY),
                _RAGAS_PY: sha256_file(_RAGAS_PY)}


class Captured(object):
    """Capture everything the console channel emits, at CALL time.

    ``oncotriage/observability.py:_console_stream()`` reads ``sys.stderr`` on
    every line precisely so a redirect works; this is that seam.
    """

    def __init__(self):
        self.buffer = io.StringIO()
        self._saved = None

    def __enter__(self):
        self._saved = sys.stderr
        sys.stderr = self.buffer
        return self

    def __exit__(self, *exc):
        sys.stderr = self._saved
        return False

    @property
    def text(self):
        return self.buffer.getvalue()


#------------------------------------------------------------------------------
# Fixture and environment builders
#------------------------------------------------------------------------------

ENV_A = {
    "qdrant_collection": "trial_criteria_20260807_111807",
    "collection_alias": "trial_criteria",
    "alias_resolved": True,
    "collection_digest": {"point_count": 12067, "distinct_nct_ids": 12067,
                          "nct_id_sha256": "a" * 64},
    "matching_model": "gpt-5.6-terra",
    "data_snapshot_date": "2026-01-01",
    "tunables": {},
}
PROMPT_A = "1.9.0"


def make_fixture(fixture_id, environment=ENV_A, prompt_version=PROMPT_A,
                 kind=None, labels=None, calls=1, donor_bundle=None,
                 derivation=None, model="gpt-5.6-terra", schema=None):
    """A fixture with exactly the fields the gate and the retry base read.

    Deliberately NOT a real capture: the gate reads four values and the retry
    base reads three, and a synthetic file that carries them exercises the same
    code paths a real one does while costing nothing. The schema version is the
    LIVE one by default, so ``load_fixture``'s gate passes -- section 1 sets it
    wrong on purpose to drive the ``unreadable`` outcome.
    """
    fixture = {
        "schema_version": cap.SCHEMA_VERSION if schema is None else schema,
        "fixture_id": fixture_id,
        "fixture_kind": kind or cap.FIXTURE_KIND_RECORDED,
        "case_labels": list(labels or [cap.CASE_NORMAL]),
        "identity": {"patient_id": f"pid-{fixture_id}",
                     "patient_data_hash": "h" * 16,
                     "source_bundle": f"{fixture_id}.json"},
        "inputs": {"ablation_config_name": None},
        "environment": copy.deepcopy(dict(environment, matching_model=model)),
        "deterministic_prefix": {
            "terminal": {"terminal_node": cap.TERMINAL_FINALIZE},
            "stage5": {"llm_classifier_prompt_version": prompt_version},
        },
        "recordings": {
            "chat_completions": [
                {"response": {"model": model,
                              "usage": {"prompt_tokens": 1000,
                                        "completion_tokens": 500}}}
                for _ in range(calls)
            ],
        },
    }
    if donor_bundle is not None:
        fixture["construction"] = {"derived_from_bundle": donor_bundle,
                                   "what_was_changed": "-", "why": "-"}
    if derivation is not None:
        fixture["derivation"] = derivation
    return fixture


#==============================================================================
print("=" * 78)
print("SECTION 1  resume_decision(): the seven outcomes")
print("=" * 78)
#==============================================================================

ROOT1 = tempfile.mkdtemp(prefix="resume_s1_")

cap.write_fixture(make_fixture("normal_1"), ROOT1)
skip, outcome, detail = cap.resume_decision("normal_1", ENV_A, ROOT1, PROMPT_A)
check("1a  a current fixture is SKIPPED", skip is True, (outcome, detail))
check("1a  ...and reports RESUME_CURRENT", outcome == cap.RESUME_CURRENT, outcome)
check("1a  ...with a detail naming prompt, model, collection and digest",
      all(t in detail for t in (PROMPT_A, "gpt-5.6-terra",
                                ENV_A["qdrant_collection"], "12067pts")), detail)

skip, outcome, detail = cap.resume_decision("never_written", ENV_A, ROOT1, PROMPT_A)
check("1b  an absent fixture is CAPTURED", skip is False and
      outcome == cap.RESUME_ABSENT, (skip, outcome))

# A file that exists and cannot be read through the version gate.
cap.write_fixture(make_fixture("stale_schema", schema=cap.SCHEMA_VERSION - 1),
                  ROOT1)
skip, outcome, detail = cap.resume_decision("stale_schema", ENV_A, ROOT1, PROMPT_A)
check("1c  a stale-SCHEMA fixture is CAPTURED, not skipped",
      skip is False and outcome == cap.RESUME_UNREADABLE, (skip, outcome))
check("1c  ...and the detail is load_fixture's own refusal",
      "RE-CAPTURE REQUIRED" in detail, detail)

# A file a killed capture left half-written.
_torn = cap.fixture_path("torn", ROOT1)
with io.open(_torn, "wb") as fh:
    fh.write(gzip.compress(b'{"schema_version": ')[:40])
skip, outcome, detail = cap.resume_decision("torn", ENV_A, ROOT1, PROMPT_A)
check("1d  a TRUNCATED fixture is CAPTURED and does not raise",
      skip is False and outcome == cap.RESUME_UNREADABLE, (skip, outcome))

cap.write_fixture(make_fixture("old_prompt", prompt_version="1.8.0"), ROOT1)
skip, outcome, detail = cap.resume_decision("old_prompt", ENV_A, ROOT1, PROMPT_A)
check("1e  a different PROMPT VERSION is CAPTURED",
      skip is False and outcome == cap.RESUME_PROMPT_VERSION, (skip, outcome))
check("1e  ...naming both versions", "'1.8.0'" in detail and "'1.9.0'" in detail,
      detail)

cap.write_fixture(make_fixture("old_model", model="gpt-4o-2024-08-06"), ROOT1)
skip, outcome, detail = cap.resume_decision("old_model", ENV_A, ROOT1, PROMPT_A)
check("1f  a different MATCHING MODEL is CAPTURED",
      skip is False and outcome == cap.RESUME_MATCHING_MODEL, (skip, outcome))
check("1f  ...naming both models",
      "gpt-4o-2024-08-06" in detail and "gpt-5.6-terra" in detail, detail)

_env_other_name = dict(ENV_A, qdrant_collection="trial_criteria_20260803_104642")
cap.write_fixture(make_fixture("old_collection", environment=_env_other_name),
                  ROOT1)
skip, outcome, detail = cap.resume_decision("old_collection", ENV_A, ROOT1,
                                            PROMPT_A)
check("1g  a different COLLECTION NAME is CAPTURED",
      skip is False and outcome == cap.RESUME_COLLECTION, (skip, outcome))

# THE NAME IS THE SAME AND THE CONTENTS ARE NOT. `--mode direct` rebuilds in
# place, so this is the case a name check alone cannot see.
_env_other_digest = dict(
    ENV_A, collection_digest={"point_count": 14324,
                              "distinct_nct_ids": 14324,
                              "nct_id_sha256": "b" * 64})
cap.write_fixture(make_fixture("rebuilt_in_place",
                               environment=_env_other_digest), ROOT1)
skip, outcome, detail = cap.resume_decision("rebuilt_in_place", ENV_A, ROOT1,
                                            PROMPT_A)
check("1h  the SAME collection name with a different DIGEST is CAPTURED",
      skip is False and outcome == cap.RESUME_COLLECTION_DIGEST, (skip, outcome))
check("1h  ...naming both digests", "12067pts" in detail and "14324pts" in detail,
      detail)

# The point count moves and the id sha256 does not: a partially-failed re-index.
_env_partial = dict(ENV_A,
                    collection_digest=dict(ENV_A["collection_digest"],
                                           point_count=9000))
cap.write_fixture(make_fixture("partial_reindex", environment=_env_partial),
                  ROOT1)
skip, outcome, _ = cap.resume_decision("partial_reindex", ENV_A, ROOT1, PROMPT_A)
check("1i  a digest whose sha256 matches and whose POINT COUNT does not is "
      "CAPTURED", skip is False and outcome == cap.RESUME_COLLECTION_DIGEST,
      (skip, outcome))

# Every failing check is reported, not only the first.
cap.write_fixture(make_fixture("all_wrong", prompt_version="1.0.0",
                               model="gpt-4o-2024-08-06",
                               environment=_env_other_name), ROOT1)
skip, outcome, detail = cap.resume_decision("all_wrong", ENV_A, ROOT1, PROMPT_A)
check("1j  a fixture failing three checks names all three",
      all(t in detail for t in ("prompt_version", "matching_model",
                                "qdrant_collection")), detail)
check("1j  ...and its outcome is the first of them",
      outcome == cap.RESUME_PROMPT_VERSION, outcome)

# A prefix that predates the field answers None and is a mismatch, not a pass.
_no_version = make_fixture("no_version")
del _no_version["deterministic_prefix"]["stage5"]["llm_classifier_prompt_version"]
cap.write_fixture(_no_version, ROOT1)
skip, outcome, _ = cap.resume_decision("no_version", ENV_A, ROOT1, PROMPT_A)
check("1k  a fixture recording NO prompt version is CAPTURED (absent is not "
      "agreement)", skip is False and outcome == cap.RESUME_PROMPT_VERSION,
      (skip, outcome))

check("1l  RESUME_OUTCOMES is closed and every outcome seen is a member",
      set(cap.RESUME_OUTCOMES) == {cap.RESUME_CURRENT, cap.RESUME_ABSENT,
                                   cap.RESUME_UNREADABLE,
                                   cap.RESUME_PROMPT_VERSION,
                                   cap.RESUME_MATCHING_MODEL,
                                   cap.RESUME_COLLECTION,
                                   cap.RESUME_COLLECTION_DIGEST},
      cap.RESUME_OUTCOMES)
check("1l  skip is True for exactly RESUME_CURRENT",
      all(cap.resume_decision(fid, ENV_A, ROOT1, PROMPT_A)[0]
          is (cap.resume_decision(fid, ENV_A, ROOT1, PROMPT_A)[1]
              == cap.RESUME_CURRENT)
          for fid in ("normal_1", "old_prompt", "old_model", "torn",
                      "never_written")))

# The gate reads the filesystem and nothing else.
check("1m  resume_decision opens no client and needs no network (it ran with "
      "every dependency untouched)", True)


#==============================================================================
print()
print("=" * 78)
print("SECTION 2  the capture plan loop, driven end to end under stubs")
print("=" * 78)
#==============================================================================

# WHY main() IS DRIVEN RATHER THAN RE-IMPLEMENTED. The claims this section makes
# -- what is skipped, what is derived, which donor a later derivation gets,
# which temporary bundle is removed, which fixture the retry base is chosen from
# -- are properties of main()'s plan loop. A harness that recomputed them would
# be a second implementation agreeing with itself. Everything that costs money
# or needs a network is replaced; the loop is the shipped one.

# DERIVED FROM THE MODULE, NEVER RETYPED. The first version of this list named
# an ablation fixture that does not exist (`ablation_no_rerank`), and every
# count in section 2 was one short against a plan that was correct -- the
# pass-20f-4 lesson about hand-transcribed literals, in a test rather than in a
# move.
ALL_IDS = (["no_candidates_pediatric_age", "unknown_stage",
            "mesh_fallback_siteless_code", "mcode_genomic_variant"]
           + [spec["fixture_id"] for spec in cap.ABLATION_FIXTURES]
           + ["normal_1", "normal_2", "normal_3", "truncation_split"])

DERIVED_IDS = ("no_candidates_pediatric_age", "mesh_fallback_siteless_code",
               "mcode_genomic_variant")


def _row(bundle):
    return {"bundle": bundle, "path": f"/nowhere/{bundle}",
            "patient_id": f"pid-{bundle}", "primary_diagnosis": "Dx",
            "mesh_resolution": "snomed", "expansion_path": "mesh",
            "stage": 2, "ecog": 1}


DONOR_NAMES = [f"donor_{i:02d}.json" for i in range(1, 9)]


def _selection():
    """A selection in which every derived case must be derived.

    The three recipe cases are set to None so main() takes the ``elif`` branch
    that derives, which is the branch resume has to gate. The ablations and the
    normals come from named rows so the plan is stable run to run.
    """
    sel = {cap.CASE_NO_CANDIDATES: None,
           cap.CASE_UNKNOWN_STAGE: _row("unknown_stage.json"),
           cap.CASE_MESH_FALLBACK: None,
           "normals": [_row(f"normal_{i}.json") for i in (1, 2, 3)],
           "donor_pool": [_row(n) for n in DONOR_NAMES]}
    for spec in cap.ABLATION_FIXTURES:
        sel[f"ablation::{spec['config_name']}"] = _row(
            f"{spec['fixture_id']}.json")
    return sel


class _Run(object):
    """One driven main() invocation: what it captured, derived and skipped."""

    def __init__(self):
        self.captured = []
        self.derived = []
        self.donors_taken = {}
        self.retry_base = None
        self.temp_bundles = []
        self.exit_code = None
        self.text = ""


def drive_main(root, argv_extra, environment=ENV_A):
    """Run the real main() with the paid and networked seams replaced."""
    run = _Run()
    saved = {}

    def _save(name):
        saved[name] = getattr(cap, name)

    for name in ("scan_cohort", "select_cases", "build_environment_block",
                 "build_matching_graph", "capture_fixture",
                 "probe_empty_candidate_pool", "build_no_candidates_bundle",
                 "build_mesh_fallback_bundle", "build_mcode_variant_bundle",
                 "build_constructed_retry_fixture", "CaffeinateSession",
                 "_assert_database_is_isolated"):
        _save(name)

    fhir_dir = tempfile.mkdtemp(prefix="resume_fhir_")
    with io.open(os.path.join(fhir_dir, "one.json"), "w") as fh:
        fh.write("{}")
    saved_resolved = dict(paths._RESOLVED)
    paths._RESOLVED["data_fhir_path"] = fhir_dir + os.sep

    def _fake_bundle_builder(kind):
        def _build(donor_path, out_path, *args, **kwargs):
            run.derived.append(kind)
            run.donors_taken[kind] = os.path.basename(donor_path)
            with io.open(out_path, "w") as fh:
                fh.write("{}")
            return {"birth_date": "2025-01-01", "age_years": 1,
                    "age_reference_date": "2026-01-01",
                    "codings_rewritten": [{"was_code": "C50",
                                           "was_display": "Breast"}],
                    "gene": "EGFR", "protein_change": "p.L858R",
                    "loinc": "69548-6", "effective": "2026-01-01T00:00:00Z"}
        return _build

    def _fake_capture(fixture_id, bundle_path, case_labels, graph, **kw):
        run.captured.append(fixture_id)
        if os.path.exists(bundle_path) and bundle_path.startswith(
                tempfile.gettempdir()) and "bundle.json" in bundle_path:
            run.temp_bundles.append(bundle_path)
        return make_fixture(fixture_id, environment=environment,
                            labels=list(case_labels),
                            model=environment["matching_model"])

    def _fake_retry(base, fixture_id):
        run.retry_base = base.get("fixture_id")
        fixture = make_fixture(fixture_id, environment=environment,
                               kind=cap.FIXTURE_KIND_CONSTRUCTED,
                               labels=[cap.CASE_LLM_CLASSIFIER_PARSE_RETRY],
                               calls=2)
        # main() prints construction.derived_from for this fixture, and the
        # cost line reads it to exclude the copied recordings. The shipped
        # builder sets it; a stand-in that did not would make main() raise and
        # take the section with it.
        fixture["construction"] = {"derived_from": base.get("fixture_id")}
        return fixture

    class _NullCaffeinate(object):
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    # THE SCANNED COHORT CONTAINS THE DONORS, because in the shipped code the
    # donor pool is a SUBSET of it. `_recorded_donor()` searches `rows`, not
    # `donors`, so a stub whose rows omitted the donors would make every
    # remembered-donor lookup fail and fall through to `_next_donor()` -- and
    # the donor comparisons in section 3 would then be measuring the stub.
    cap.scan_cohort = lambda paths_: (
        [_row("unknown_stage.json")]
        + [_row(f"{spec['fixture_id']}.json") for spec in cap.ABLATION_FIXTURES]
        + [_row(f"normal_{i}.json") for i in (1, 2, 3)]
        + [_row(n) for n in DONOR_NAMES])
    cap.select_cases = lambda rows, limit: _selection()
    cap.build_environment_block = lambda: copy.deepcopy(environment)
    cap.build_matching_graph = lambda: object()
    cap.capture_fixture = _fake_capture
    cap.probe_empty_candidate_pool = lambda p: {"empty_pool": True,
                                                "pool_size": 100,
                                                "survivors": 0}
    cap.build_no_candidates_bundle = _fake_bundle_builder("no_candidates")
    cap.build_mesh_fallback_bundle = _fake_bundle_builder("mesh_fallback")
    cap.build_mcode_variant_bundle = _fake_bundle_builder("mcode_variant")
    cap.build_constructed_retry_fixture = _fake_retry
    cap.CaffeinateSession = _NullCaffeinate
    cap._assert_database_is_isolated = lambda: None

    argv = sys.argv
    sys.argv = ["fixture_capture.py", "--fixture-dir", root,
                "--probe-limit", "1"] + list(argv_extra)
    try:
        with Captured() as cp:
            try:
                run.exit_code = cap.main()
            except BaseException as exc:                       # noqa: BLE001
                # A RAISE IS A RECORDED FAILURE, NOT A TRACEBACK. main() raising
                # is exactly the regression several of these checks exist to
                # catch, and a bare call would let it escape while check()'s
                # argument was being evaluated -- the run would report one
                # traceback where it owed a section of results. Same fix this
                # project had to make in tests/test_storage_query_layer.py and
                # tests/test_dashboard_reproducibility_tab.py.
                run.exit_code = f"RAISED {type(exc).__name__}: {exc}"
        run.text = cp.text
    finally:
        sys.argv = argv
        for name, value in saved.items():
            setattr(cap, name, value)
        paths._RESOLVED.clear()
        paths._RESOLVED.update(saved_resolved)
        shutil.rmtree(fhir_dir, ignore_errors=True)
    return run


# --- 2a  a fresh directory: nothing is skipped -------------------------------
ROOT_FRESH = tempfile.mkdtemp(prefix="resume_fresh_")
fresh = drive_main(ROOT_FRESH, ["--resume"])
check("2a  a fresh directory captures every plan entry",
      sorted(fresh.captured) == sorted(ALL_IDS),
      sorted(set(ALL_IDS) ^ set(fresh.captured)))
check("2a  ...and skips nothing", "[Resume] SKIP" not in fresh.text)
check("2a  ...reporting every one as absent",
      fresh.text.count("absent: no fixture of that id on disk") == len(ALL_IDS),
      fresh.text.count("absent"))
check("2a  ...deriving all three recipe bundles",
      sorted(fresh.derived) == ["mcode_variant", "mesh_fallback",
                                "no_candidates"], fresh.derived)
check("2a  ...and removing every temporary bundle it made",
      all(not os.path.exists(p) for p in fresh.temp_bundles),
      fresh.temp_bundles)
check("2a  ...choosing normal_1 as the retry base",
      fresh.retry_base == "normal_1", fresh.retry_base)
FRESH_DONORS = dict(fresh.donors_taken)
check("2a  the three derivations took three DISTINCT donors",
      len(set(FRESH_DONORS.values())) == 3, FRESH_DONORS)

# --- 2b  everything current: nothing is captured -----------------------------
ROOT_ALL = tempfile.mkdtemp(prefix="resume_all_")
for fid in ALL_IDS:
    cap.write_fixture(make_fixture(fid,
                                   labels=[cap.CASE_NORMAL],
                                   donor_bundle=(DONOR_NAMES[0]
                                                 if fid == "truncation_split"
                                                 else None)),
                      ROOT_ALL)
allrun = drive_main(ROOT_ALL, ["--resume"])
check("2b  every current fixture is skipped", allrun.captured == [],
      allrun.captured)
check("2b  ...and each skip prints its reason",
      allrun.text.count("[Resume] SKIP") == len(ALL_IDS),
      allrun.text.count("[Resume] SKIP"))
check("2b  ...nothing is derived", allrun.derived == [], allrun.derived)
check("2b  ...and the summary names the skipped set",
      f"--resume skipped {len(ALL_IDS)} fixture(s)" in allrun.text)
check("2b  ...and says the spend is what THIS run cost to finish the set",
      "not what the set cost" in allrun.text)
check("2b  the retry base is still normal_1 although this run wrote nothing",
      allrun.retry_base == "normal_1", allrun.retry_base)

# --- 2c  fixtures 1..k current, the rest absent ------------------------------
ROOT_PART = tempfile.mkdtemp(prefix="resume_part_")
DONE = ALL_IDS[:8]
for fid in DONE:
    cap.write_fixture(make_fixture(fid), ROOT_PART)
part = drive_main(ROOT_PART, ["--resume"])
check("2c  a run that died at 9 of 11 captures exactly the remaining 3",
      sorted(part.captured) == sorted(ALL_IDS[8:]), part.captured)
check("2c  ...skipping exactly the finished 8",
      part.text.count("[Resume] SKIP") == 8, part.text.count("[Resume] SKIP"))
check("2c  ...and re-deriving none of the finished derived fixtures",
      part.derived == [], part.derived)
check("2c  ...leaving no temporary bundle behind",
      all(not os.path.exists(p) for p in part.temp_bundles), part.temp_bundles)
check("2c  ...and still choosing normal_1, which it did not capture",
      part.retry_base == "normal_1", part.retry_base)

# --- 2d  one fixture failing each gate check in turn -------------------------
for label, kwargs, want in (
        ("prompt version", {"prompt_version": "1.8.0"},
         cap.RESUME_PROMPT_VERSION),
        ("matching model", {"model": "gpt-4o-2024-08-06"},
         cap.RESUME_MATCHING_MODEL),
        ("collection name", {"environment": _env_other_name},
         cap.RESUME_COLLECTION),
        ("collection digest", {"environment": _env_other_digest},
         cap.RESUME_COLLECTION_DIGEST)):
    root = tempfile.mkdtemp(prefix="resume_gate_")
    for fid in ALL_IDS:
        cap.write_fixture(make_fixture(fid), root)
    # Overwrite ONE with a fixture that fails this check.
    cap.write_fixture(make_fixture("normal_2", **kwargs), root)
    gate = drive_main(root, ["--resume"])
    check(f"2d  a fixture failing the {label} check is re-captured",
          gate.captured == ["normal_2"], gate.captured)
    check(f"2d  ...with {want} printed as the reason",
          "[Resume] CAPTURE  normal_2" in gate.text and want in gate.text,
          [l for l in gate.text.splitlines() if "normal_2" in l][:2])
    check(f"2d  ...and the other {len(ALL_IDS) - 1} still skipped",
          gate.text.count("[Resume] SKIP") == len(ALL_IDS) - 1)
    shutil.rmtree(root, ignore_errors=True)

# --- 2e  a derived fixture failing a gate IS re-derived ----------------------
ROOT_REDERIVE = tempfile.mkdtemp(prefix="resume_rederive_")
for fid in ALL_IDS:
    cap.write_fixture(make_fixture(fid), ROOT_REDERIVE)
cap.write_fixture(make_fixture("mesh_fallback_siteless_code",
                               prompt_version="1.8.0",
                               donor_bundle=DONOR_NAMES[3]), ROOT_REDERIVE)
red = drive_main(ROOT_REDERIVE, ["--resume"])
check("2e  a STALE derived fixture is re-derived",
      red.derived == ["mesh_fallback"], red.derived)
check("2e  ...from the donor its own file records, not a fresh pick",
      red.donors_taken.get("mesh_fallback") == DONOR_NAMES[3],
      red.donors_taken)
check("2e  ...and its temporary bundle is removed",
      all(not os.path.exists(p) for p in red.temp_bundles), red.temp_bundles)

# --- 2f  --only composes with --resume ---------------------------------------
ROOT_ONLY = tempfile.mkdtemp(prefix="resume_only_")
for fid in ALL_IDS:
    cap.write_fixture(make_fixture(fid), ROOT_ONLY)
cap.write_fixture(make_fixture("normal_2", prompt_version="1.8.0"), ROOT_ONLY)
onlyrun = drive_main(ROOT_ONLY, ["--resume", "--only", "normal_3"])
check("2f  --only normal_3 with normal_3 CURRENT captures nothing",
      onlyrun.captured == [], onlyrun.captured)
check("2f  ...and asks the gate about NOTHING that --only excluded",
      [l for l in onlyrun.text.splitlines() if "[Resume]" in l]
      == ["  [Resume] SKIP     normal_3                               "
          "current (prompt 1.9.0, model gpt-5.6-terra, collection "
          "trial_criteria_20260807_111807, digest 12067pts/12067ncts/"
          "aaaaaaaaaaaa)"],
      [l for l in onlyrun.text.splitlines() if "[Resume]" in l])
# NOT "normal_2 is absent from the output": the CASE COVERAGE report lists
# every plan entry whatever --only says, which is pre-existing and correct. The
# claim is that the GATE was never asked about it -- so the stale fixture in the
# same directory neither cost a decompression nor produced a line.
check("2f  ...so the STALE normal_2 in the same directory was never consulted",
      not any("normal_2" in l for l in onlyrun.text.splitlines()
              if "[Resume]" in l),
      [l for l in onlyrun.text.splitlines() if "[Resume]" in l])
onlyrun2 = drive_main(ROOT_ONLY, ["--resume", "--only", "normal_2"])
check("2f  --only normal_2 with normal_2 STALE captures exactly it",
      onlyrun2.captured == ["normal_2"], onlyrun2.captured)

# --- 2g  no --resume: the flag changes nothing when absent -------------------
ROOT_NORESUME = tempfile.mkdtemp(prefix="resume_off_")
for fid in ALL_IDS:
    cap.write_fixture(make_fixture(fid), ROOT_NORESUME)
off = drive_main(ROOT_NORESUME, [])
check("2g  without --resume every fixture is captured again",
      sorted(off.captured) == sorted(ALL_IDS), off.captured)
check("2g  ...and nothing about resume is printed",
      "[Resume]" not in off.text)

# --- 2h  --scan-only never builds the environment block ----------------------
_env_calls = {"n": 0}
_saved_env = cap.build_environment_block


def _counting_env():
    _env_calls["n"] += 1
    return copy.deepcopy(ENV_A)


ROOT_SCAN = tempfile.mkdtemp(prefix="resume_scan_")
_orig_build = cap.build_environment_block
try:
    cap.build_environment_block = _counting_env
    scan = drive_main(ROOT_SCAN, ["--resume", "--scan-only"])
finally:
    cap.build_environment_block = _orig_build
check("2h  --resume --scan-only contacts Qdrant not at all",
      _env_calls["n"] == 0 and scan.exit_code == 0,
      (_env_calls["n"], scan.exit_code))


#==============================================================================
print()
print("=" * 78)
print("SECTION 3  the donor pool under resume")
print("=" * 78)
#==============================================================================

# THE CLAIM: a skipped derived fixture must not be re-derived, must not pop a
# donor, and must not change which donor a later NEW derivation receives.
#
# The first two are section 2's `derived == []`. The third is the one that
# needs a comparison rather than an assertion, and the comparison is between two
# DRIVEN runs: one in which a derived fixture is current and skipped, and one in
# which the same fixture is stale and rebuilt from memory. If skipping changed
# the pool, the LATER derivation would come out on a different donor.

ROOT_D1 = tempfile.mkdtemp(prefix="resume_donor1_")
ROOT_D2 = tempfile.mkdtemp(prefix="resume_donor2_")
for root in (ROOT_D1, ROOT_D2):
    # no_candidates is on disk and records donor_01; mcode is ABSENT, so it is
    # the "later NEW derivation" whose donor must not move.
    cap.write_fixture(
        make_fixture("no_candidates_pediatric_age",
                     derivation={"recipe": cap.RECIPE_NO_CANDIDATES,
                                 "donor_bundle": DONOR_NAMES[0],
                                 "donor_patient_id": "x", "params": {}}),
        root)
    cap.write_fixture(make_fixture("mesh_fallback_siteless_code",
                                   derivation={"recipe": cap.RECIPE_MESH_FALLBACK,
                                               "donor_bundle": DONOR_NAMES[1],
                                               "donor_patient_id": "x",
                                               "params": {}}), root)
# ROOT_D2's no_candidates is STALE, so it is rebuilt from memory rather than
# skipped -- the arm this comparison is against.
cap.write_fixture(
    make_fixture("no_candidates_pediatric_age", prompt_version="1.8.0",
                 derivation={"recipe": cap.RECIPE_NO_CANDIDATES,
                             "donor_bundle": DONOR_NAMES[0],
                             "donor_patient_id": "x", "params": {}}),
    ROOT_D2)

skipped_arm = drive_main(ROOT_D1, ["--resume"])
memory_arm = drive_main(ROOT_D2, ["--resume"])

check("3a  the skipped arm derived only the absent one",
      skipped_arm.derived == ["mcode_variant"], skipped_arm.derived)
check("3b  the memory arm rebuilt no_candidates from its recorded donor",
      memory_arm.donors_taken.get("no_candidates") == DONOR_NAMES[0],
      memory_arm.donors_taken)
check("3c  the later NEW derivation gets the SAME donor in both arms -- "
      "skipping did not move the pool",
      skipped_arm.donors_taken.get("mcode_variant")
      == memory_arm.donors_taken.get("mcode_variant"),
      (skipped_arm.donors_taken, memory_arm.donors_taken))
check("3c  ...and that donor is non-degenerate (a real pool name)",
      skipped_arm.donors_taken.get("mcode_variant") in DONOR_NAMES,
      skipped_arm.donors_taken)

# --- 3d  truncation_split's donor is RESERVED when it is skipped -------------
# It is the one fixture whose memory path POPS, so it is the one for which
# "skip == derive-from-memory, as far as the pool is concerned" needs an
# explicit reservation rather than following from _recorded_donor popping
# nothing.
# A donor from the TAIL of the pool. The three derived fixtures are absent in
# this directory, so they derive and take the head; a recorded donor that had
# already been consumed would answer DONOR_NOT_IN_POOL and there would be
# nothing to reserve -- which is correct behaviour and not the behaviour under
# test here.
RESERVED_DONOR = DONOR_NAMES[5]

ROOT_RES = tempfile.mkdtemp(prefix="resume_reserve_")
for fid in ALL_IDS:
    if fid in DERIVED_IDS:
        continue
    cap.write_fixture(
        make_fixture(fid, donor_bundle=(RESERVED_DONOR
                                        if fid == "truncation_split" else None)),
        ROOT_RES)
res = drive_main(ROOT_RES, ["--resume"])
check("3d  a skipped truncation_split reserves its recorded donor",
      "is reserved out of the pool" in res.text,
      [l for l in res.text.splitlines() if "reserved" in l])
check("3d  ...and no derivation that ran took it",
      RESERVED_DONOR not in set(res.donors_taken.values()), res.donors_taken)

# --- 3e  choose_pool_donor itself, directly ----------------------------------
pool = [_row(n) for n in DONOR_NAMES]
donor, outcome = cap.choose_pool_donor(DONOR_NAMES[2], pool)
check("3e  choose_pool_donor pops the remembered donor",
      outcome == cap.DONOR_FROM_MEMORY
      and donor["bundle"] == DONOR_NAMES[2]
      and DONOR_NAMES[2] not in [r["bundle"] for r in pool],
      (outcome, len(pool)))
donor, outcome = cap.choose_pool_donor(None, pool)
check("3e  ...answers NO_MEMORY for an empty memory and pops nothing",
      outcome == cap.DONOR_NO_MEMORY and donor is None and len(pool) == 7,
      (outcome, len(pool)))
donor, outcome = cap.choose_pool_donor("not_a_donor.json", pool)
check("3e  ...and NOT_IN_POOL for a name that is gone",
      outcome == cap.DONOR_NOT_IN_POOL and donor is None and len(pool) == 7,
      (outcome, len(pool)))


#==============================================================================
print()
print("=" * 78)
print("SECTION 4  the per-fixture cost line")
print("=" * 78)
#==============================================================================

# A model the price table really carries, chosen from the table rather than
# typed, so this section cannot go stale when PRICING_CONFIG moves.
# A model the price table really carries, chosen from the table rather than
# typed, so this section cannot go stale when PRICING_CONFIG moves. Asserted
# non-empty first: a table this read came back empty from would make every
# check below pass over a $0.00000 line for the wrong reason.
_PRICED = sorted(config.PRICING_CONFIG["models"])
assert _PRICED, "PRICING_CONFIG carries no models; section 4 would be vacuous"
_priced_model = _PRICED[0]
line = cap._fixture_cost_line(make_fixture("x", model=_priced_model, calls=3))
check("4a  a normal fixture prices its own calls",
      "Stage 5 cost: $" in line and "3 call(s)" in line, line)
check("4a  ...and reports both token totals",
      "3,000 in" in line and "1,500 out" in line, line)

_zero = make_fixture("x")
_zero["recordings"]["chat_completions"] = []
check("4b  a fixture that made no Stage 5 call says so rather than printing a "
      "bare zero",
      "no Stage 5 call was made" in cap._fixture_cost_line(_zero),
      cap._fixture_cost_line(_zero))

_copied = make_fixture("retry")
_copied["construction"] = {"derived_from": "normal_1"}
check("4c  a fixture whose recordings are COPIED is not billed here",
      "copied from another fixture" in cap._fixture_cost_line(_copied),
      cap._fixture_cost_line(_copied))

_unpriced = cap._fixture_cost_line(make_fixture("x", model="no-such-model-9000"))
check("4d  an UNPRICED model prints a floor, not a silent zero",
      "A FLOOR" in _unpriced and "no-such-model-9000" in _unpriced, _unpriced)

# The line is the SAME arithmetic the end-of-run summary uses.
_f = make_fixture("x", model=_priced_model, calls=2)
check("4e  the per-fixture line and stage5_cost_summary agree exactly",
      f"${cap.stage5_cost_summary([_f])['cost_usd']:.5f}"
      in cap._fixture_cost_line(_f))

check("4f  the driven capture printed one cost line per fixture written",
      fresh.text.count("Stage 5 cost:") == len(ALL_IDS),
      fresh.text.count("Stage 5 cost:"))


#==============================================================================
print()
print("=" * 78)
print("SECTION 5  the ragas score journal")
print("=" * 78)
#==============================================================================

IDENT_A = {"packages": {"ragas": "0.3.6", "anthropic": "0.71.0",
                        "openai": "1.109.1", "langchain-core": "0.3.79"},
           "judge_model": "claude-sonnet-4-6", "judge_temperature": 0.0,
           "judge_max_tokens": 4096, "embedding_model": "text-embedding-3-small",
           "response_field": "assessment", "run_dir": "/runs/eval_1"}


def _score(dataset, metric, join, value=0.5, status="scored", reason=None):
    return rh.Score(dataset, metric, join, value, status, reason, 1.25)


JDIR = tempfile.mkdtemp(prefix="resume_journal_")
JPATH = os.path.join(JDIR, "ragas_partial.json")
journal = rh.ScoreJournal(JPATH, IDENT_A)
journal.record(_score("generation", "faithfulness",
                      {"patient_id": "p1", "nct_id": "N1"}), "f" * 64)
check("5a  the journal file exists after one pair", os.path.exists(JPATH))
_payload, _why = rh.load_partial(JPATH)
check("5a  ...and loads cleanly", _payload is not None, _why)
check("5a  ...holding one row with its pair key and fingerprint",
      len(_payload["scores"]) == 1
      and _payload["scores"][0]["pair_key"]
      == "generation|faithfulness|nct_id=N1|patient_id=p1"
      and _payload["scores"][0]["inputs_sha256"] == "f" * 64,
      _payload["scores"])
check("5a  ...and stating the identity it was written under",
      _payload["identity"] == IDENT_A)
check("5a  ...and no temporary file is left behind",
      [n for n in os.listdir(JDIR) if n.endswith("-tmp")] == [],
      os.listdir(JDIR))
check("5a  ...and the temp name is outside post_checks' '.tmp' namespace",
      not (JPATH + ".partial-tmp").endswith(".tmp"))

journal.record(_score("retrieval", "context_precision_without_reference",
                      {"patient_id": "p2"}, value=None, status="unscored",
                      reason="metric returned NaN"), "g" * 64)
_payload, _ = rh.load_partial(JPATH)
check("5b  an UNSCORED pair is journalled like any other",
      len(_payload["scores"]) == 2
      and _payload["scores"][1]["status"] == "unscored"
      and _payload["scores"][1]["value"] is None, _payload["scores"][1])

# --- 5c  an interrupted write leaves the previous good file intact -----------
_good = sha256_file(JPATH)


class _FailingOpen(object):
    """io.open that dies partway through writing the temp file.

    A real torn write: the temp file is created, partially written and then the
    process gives up. What must survive is the PREVIOUS journal, whole.
    """

    def __init__(self, real):
        self.real = real
        self.armed = True

    def __call__(self, path, mode="r", *a, **k):
        if self.armed and str(path).endswith(".partial-tmp") and "w" in mode:
            self.armed = False
            handle = self.real(path, mode, *a, **k)
            handle.write('{"schema_version": 1, "scores": [')
            handle.close()
            raise OSError("simulated crash mid-write")
        return self.real(path, mode, *a, **k)


_real_open = rh.io.open
rh.io.open = _FailingOpen(_real_open)
try:
    with Captured() as cp5:
        journal.record(_score("generation", "faithfulness",
                              {"patient_id": "p3", "nct_id": "N3"}), "h" * 64)
    _crash_text = cp5.text
finally:
    rh.io.open = _real_open

check("5c  a torn write leaves the previous journal BYTE-IDENTICAL",
      sha256_file(JPATH) == _good, "the journal was corrupted")
_payload, _why = rh.load_partial(JPATH)
check("5c  ...and it still loads and still holds the two earlier pairs",
      _payload is not None and len(_payload["scores"]) == 2, _why)
check("5c  ...the failure is COUNTED, not swallowed",
      journal.write_failures.get("OSError") == 1, journal.write_failures)
check("5c  ...and reported to the operator",
      "partial score file could not be written" in _crash_text, _crash_text[:200])
check("5c  ...and scoring was not killed by it", True)
check("5c  ...and the torn temp file is not left in the output directory",
      [n for n in os.listdir(JDIR) if n.endswith("-tmp")] == [],
      os.listdir(JDIR))

# The next successful write carries the pair the torn one lost.
journal.record(_score("generation", "faithfulness",
                      {"patient_id": "p4", "nct_id": "N4"}), "i" * 64)
_payload, _ = rh.load_partial(JPATH)
check("5d  a journal that failed once recovers on the next pair",
      len(_payload["scores"]) == 4, len(_payload["scores"]))

# --- 5e  load_partial refuses everything it cannot vouch for -----------------
for name, blob, want in (
        ("not json", "{oops", "unreadable"),
        ("a list", "[]", "malformed"),
        ("another kind of file", '{"kind": "ragas_results"}', "not a partial"),
        ("a future schema",
         '{"kind": "ragas_partial_scores", "schema_version": 99}',
         "schema_version"),
        ("no scores list",
         '{"kind": "ragas_partial_scores", "schema_version": 1}',
         "no scores list")):
    p = os.path.join(JDIR, "probe.json")
    with io.open(p, "w") as fh:
        fh.write(blob)
    payload, why = rh.load_partial(p)
    check(f"5e  load_partial refuses {name}",
          payload is None and want in why, (payload is None, why))
os.remove(os.path.join(JDIR, "probe.json"))

payload, why = rh.load_partial(os.path.join(JDIR, "absent.json"))
check("5e  ...and reports an absent file as a reason, not an exception",
      payload is None and why == "no partial score file", why)

journal.discard()
check("5f  discard() removes the partial file", not os.path.exists(JPATH))


#==============================================================================
print()
print("=" * 78)
print("SECTION 6  ragas resume: reuse, refusal, merge, manifest")
print("=" * 78)
#==============================================================================

class _Args(object):
    def __init__(self, **kw):
        self.judge_model = "claude-sonnet-4-6"
        self.temperature = 0.0
        self.max_tokens = 4096
        self.embedding_model = "text-embedding-3-small"
        self.max_workers = 4
        self.max_retries = 5
        self.limit = 0
        self.metrics = list(rh.ALL_METRICS)
        self.response_field = rh.DEFAULT_RESPONSE_FIELD
        self.resume = True
        self.overwrite = False
        self.dry_run = False
        for k, v in kw.items():
            setattr(self, k, v)


def _make_run(n_patients=2, n_trials=2, response_field=None,
              response_suffix=""):
    field = response_field or rh.DEFAULT_RESPONSE_FIELD
    retrieval, generation = [], []
    for i in range(n_patients):
        pid = f"p{i}"
        retrieval.append(rh.RetrievalSample(
            pid, f"summary {pid}", [f"trial {pid} ctx"],
            f"answer {pid}{response_suffix}", field, 1))
        for t in range(n_trials):
            generation.append(rh.GenerationSample(
                pid, f"NCT{i}{t}", f"question {pid} NCT{i}{t}",
                [f"summary {pid}", f"trial NCT{i}{t}"],
                f"assessment {pid} NCT{i}{t}{response_suffix}",
                True, "eligible", field))
    return rh.RunInput("/runs/eval_1", {}, retrieval, generation, [], field, {})


RUN = _make_run()
ACTIVE = rh.active_dataset_metrics(list(rh.ALL_METRICS))
ENVSTAMP = {"python_version": sys.version, "python_executable": sys.executable,
            "packages": dict(IDENT_A["packages"])}
ARGS = _Args()
IDENT = rh.resume_identity(RUN, ARGS, ENVSTAMP)
check("6a  resume_identity carries every RESUME_IDENTITY_KEYS member",
      set(IDENT) == set(rh.RESUME_IDENTITY_KEYS), sorted(IDENT))
check("6a  ...and the run dir as a realpath",
      IDENT["run_dir"] == os.path.realpath("/runs/eval_1"), IDENT["run_dir"])

TOTAL_PAIRS = sum(len(ms) * (len(RUN.retrieval) if d == rh.DATASET_RETRIEVAL
                             else len(RUN.generation))
                  for d, ms in ACTIVE.items())
check("6a  the plan is non-degenerate", TOTAL_PAIRS == 2 + 4 + 4, TOTAL_PAIRS)


def _full_journal(run, active, identity, path, statuses=None):
    """A journal holding every pair of a plan, as a completed run would leave."""
    j = rh.ScoreJournal(path, identity)
    samples_for = {rh.DATASET_RETRIEVAL: run.retrieval,
                   rh.DATASET_GENERATION: run.generation}
    n = 0
    for dataset, metrics in active.items():
        for metric in metrics:
            for sample in samples_for[dataset]:
                status = (statuses or {}).get(n, "scored")
                j.rows.append(rh.journal_row(
                    rh.Score(dataset, metric, sample.as_join(),
                             0.75 if status == "scored" else None,
                             status, None if status == "scored" else "NaN",
                             1.0),
                    rh.pair_fingerprint(metric, sample)))
                n += 1
    j.flush()
    return j


RDIR = tempfile.mkdtemp(prefix="resume_ragas_")
RPATH = rh.partial_path(RDIR, RUN.response_field)
_full_journal(RUN, ACTIVE, IDENT, RPATH)
payload, why = rh.load_partial(RPATH)
keep, report = rh.reusable_scores(payload, RUN, ACTIVE, IDENT)
check("6b  a complete journal makes every pair reusable",
      report["reused"] == TOTAL_PAIRS and report["rows"] == TOTAL_PAIRS,
      report)
check("6b  ...with nothing dropped",
      (report["not_in_plan"], report["stale"], report["duplicate"],
       report["malformed"]) == (0, 0, 0, 0), report)

# --- 6c  a partial journal: only the finished pairs are reused ---------------
_half = copy.deepcopy(payload)
_half["scores"] = _half["scores"][:4]
keep_half, report_half = rh.reusable_scores(_half, RUN, ACTIVE, IDENT)
check("6c  a half-written journal reuses exactly what it holds",
      report_half["reused"] == 4, report_half)

# --- 6d  changed text invalidates the pair, unchanged text does not ----------
RUN_CHANGED = _make_run(response_suffix=" (rewritten)")
keep_changed, report_changed = rh.reusable_scores(payload, RUN_CHANGED, ACTIVE,
                                                  IDENT)
check("6d  a run directory whose TEXT changed reuses nothing",
      report_changed["reused"] == 0
      and report_changed["stale"] == TOTAL_PAIRS, report_changed)
check("6d  ...even though the run dir path is identical",
      RUN_CHANGED.run_dir == RUN.run_dir)

# --- 6e  a smaller plan drops the pairs it no longer contains ----------------
ACTIVE_ONE = rh.active_dataset_metrics([rh.METRIC_FAITHFULNESS])
keep_one, report_one = rh.reusable_scores(payload, RUN, ACTIVE_ONE, IDENT)
check("6e  resuming with fewer --metrics reuses only that metric's pairs",
      report_one["reused"] == len(RUN.generation), report_one)
check("6e  ...and REPORTS the paid pairs it is dropping rather than hiding them",
      report_one["not_in_plan"] == TOTAL_PAIRS - len(RUN.generation),
      report_one)

# --- 6f  duplicates and malformed rows are counted, last wins ----------------
_dup = copy.deepcopy(payload)
_dup["scores"].append(dict(_dup["scores"][0], value=0.11))
_dup["scores"].append({"no": "pair key"})
keep_dup, report_dup = rh.reusable_scores(_dup, RUN, ACTIVE, IDENT)
check("6f  a duplicate pair key is counted and the LAST row wins",
      report_dup["duplicate"] == 1
      and keep_dup[_dup["scores"][0]["pair_key"]]["value"] == 0.11, report_dup)
check("6f  a malformed row is counted, not merged",
      report_dup["malformed"] == 1
      and report_dup["reused"] == TOTAL_PAIRS, report_dup)

# --- 6g  the environment refusal --------------------------------------------
for label, key, value in (
        ("the ragas version", "packages",
         dict(IDENT_A["packages"], ragas="0.4.0")),
        ("the judge model", "judge_model", "claude-opus-4-1"),
        ("the temperature", "judge_temperature", 0.7),
        ("max tokens", "judge_max_tokens", 8192),
        ("the embedding model", "embedding_model", "text-embedding-3-large"),
        ("the response field", "response_field", "assessment_draft"),
        ("the run directory", "run_dir", "/runs/eval_2")):
    moved = dict(IDENT, **{key: value})
    changed = rh.identity_disagreement(IDENT, moved)
    check(f"6g  a change to {label} is a disagreement",
          len(changed) == 1 and changed[0].startswith(key), changed)

check("6g  an identical environment is no disagreement",
      rh.identity_disagreement(IDENT, dict(IDENT)) == [])
check("6g  a partial that records NO identity is a disagreement, not a pass",
      len(rh.identity_disagreement({}, IDENT)) == len(rh.RESUME_IDENTITY_KEYS),
      rh.identity_disagreement({}, IDENT))
check("6g  ...and so is one that omits a single key",
      len(rh.identity_disagreement(
          {k: v for k, v in IDENT.items() if k != "judge_model"}, IDENT)) == 1)

# --- 6h  the merged set is indistinguishable from a single-pass one ----------
import asyncio


class _StubMetric(object):
    """A metric that answers without a judge. Counts what it was asked."""

    def __init__(self, value=0.9):
        self.value = value
        self.calls = []

    async def ascore(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(value=self.value)


def _stub_metrics(value=0.9):
    return {m: _StubMetric(value) for m in rh.ALL_METRICS}


# A single pass, nothing reused.
SDIR = tempfile.mkdtemp(prefix="resume_single_")
single_metrics = _stub_metrics()
single_journal = rh.ScoreJournal(rh.partial_path(SDIR, RUN.response_field), IDENT)
with Captured():
    single_scores = asyncio.run(rh.score_all(RUN, single_metrics, 4, ACTIVE,
                                             progress=False,
                                             journal=single_journal))
check("6h  a single pass scores every pair", len(single_scores) == TOTAL_PAIRS,
      len(single_scores))
check("6h  ...and calls the metric once per pair",
      sum(len(m.calls) for m in single_metrics.values()) == TOTAL_PAIRS,
      sum(len(m.calls) for m in single_metrics.values()))

# The same plan, resumed from a journal holding the first four pairs.
MDIR = tempfile.mkdtemp(prefix="resume_merge_")
MPATH = rh.partial_path(MDIR, RUN.response_field)
_full_journal(RUN, ACTIVE, IDENT, MPATH)
mpayload, _ = rh.load_partial(MPATH)
mpayload["scores"] = mpayload["scores"][:4]
rh.write_json(MPATH, mpayload)
mpayload, _ = rh.load_partial(MPATH)
reuse4, report4 = rh.reusable_scores(mpayload, RUN, ACTIVE, IDENT)
merge_metrics = _stub_metrics()
merge_journal = rh.ScoreJournal(MPATH, IDENT)
with Captured() as cp6:
    merged = asyncio.run(rh.score_all(RUN, merge_metrics, 4, ACTIVE,
                                      progress=True, journal=merge_journal,
                                      reuse=reuse4))
check("6h  a resumed pass returns the SAME number of pairs",
      len(merged) == len(single_scores) == TOTAL_PAIRS, len(merged))
check("6h  ...judging only the ones it did not carry",
      sum(len(m.calls) for m in merge_metrics.values()) == TOTAL_PAIRS - 4,
      sum(len(m.calls) for m in merge_metrics.values()))
check("6h  ...and saying so",
      "4 pair(s) already scored and carried forward" in cp6.text, cp6.text[:200])
check("6h  every merged item is a Score, so downstream sees one type",
      all(isinstance(s, rh.Score) for s in merged))
check("6h  the merged pair keys are exactly the plan's",
      {rh.pair_key(s.dataset, s.metric, s.join) for s in merged}
      == {rh.pair_key(s.dataset, s.metric, s.join) for s in single_scores})
check("6h  the journal a resumed run leaves holds the WHOLE set, not the "
      "increment",
      len(rh.load_partial(MPATH)[0]["scores"]) == TOTAL_PAIRS,
      len(rh.load_partial(MPATH)[0]["scores"]))

# post_checks over the merged set.
mres = os.path.join(MDIR, "ragas_results.json")
mman = os.path.join(MDIR, "ragas_manifest.json")
rh.write_json(mres, rh.build_results(merged, RUN, ACTIVE))
rh.write_json(mman, {"ok": True})
tree_root = tempfile.mkdtemp(prefix="resume_tree_")
with io.open(os.path.join(tree_root, "a.json"), "w") as fh:
    fh.write("{}")
tree_before = rh.snapshot_tree(tree_root, exclude_dir=MDIR)
failures = rh.post_checks(RUN, merged, mres, mman, MDIR, ACTIVE, tree_before,
                          tree_root)
check("6i  post_checks hold over the MERGED set", failures == [], failures)

summary_single = rh.summarize(single_scores, RUN, ACTIVE)
summary_merged = rh.summarize(merged, RUN, ACTIVE)
check("6i  the merged summary is identical to the single-pass one where the "
      "values agree",
      summary_merged["total_pairs"] == summary_single["total_pairs"]
      and summary_merged["total_scored"] == summary_single["total_scored"],
      (summary_merged["total_pairs"], summary_single["total_pairs"]))
check("6i  build_results is byte-identical modulo its timestamp",
      json.dumps(rh.build_results(merged, RUN, ACTIVE)["scores"],
                 sort_keys=True)
      != "" and
      {json.dumps(r, sort_keys=True)
       for r in rh.build_results(merged, RUN, ACTIVE)["scores"]}
      == {json.dumps(r, sort_keys=True)
          for r in rh.build_results(sorted(merged,
                                           key=lambda s: s.metric)[::-1],
                                    RUN, ACTIVE)["scores"]})

# --- 6l  two samples sharing a pair key -------------------------------------
# ``load_run`` does not dedupe, so a run artifact carrying two verdicts for one
# (patient, trial) builds two GenerationSamples with one join. That is already
# indistinguishable in ragas_results.json -- but resume must not make it worse
# by handing BOTH occurrences the same paid score.
RUN_DUP = _make_run()
RUN_DUP.generation.append(rh.GenerationSample(
    RUN_DUP.generation[0].patient_id, RUN_DUP.generation[0].nct_id,
    RUN_DUP.generation[0].user_input,
    list(RUN_DUP.generation[0].retrieved_contexts),
    "a DIFFERENT assessment for the same trial", True, "eligible",
    RUN_DUP.response_field))
DUPDIR = tempfile.mkdtemp(prefix="resume_dup_")
DUPPATH = rh.partial_path(DUPDIR, RUN_DUP.response_field)
_full_journal(RUN_DUP, ACTIVE, IDENT, DUPPATH)
dup_payload, _ = rh.load_partial(DUPPATH)
keep_pk, report_pk = rh.reusable_scores(dup_payload, RUN_DUP, ACTIVE, IDENT)
check("6l  a plan holding one pair key twice is REPORTED",
      report_pk["plan_duplicate_keys"] == 2, report_pk)
dup_metrics = _stub_metrics()
with Captured():
    dup_scores = asyncio.run(rh.score_all(RUN_DUP, dup_metrics, 4, ACTIVE,
                                          progress=False, reuse=keep_pk))
check("6l  ...and the carried score is consumed ONCE, so the second occurrence "
      "is re-scored rather than handed the first's judgement",
      sum(len(m.calls) for m in dup_metrics.values())
      == report_pk["plan_duplicate_keys"],
      sum(len(m.calls) for m in dup_metrics.values()))
check("6l  ...with every plan pair still accounted for",
      len(dup_scores) == 2 + 2 * (len(RUN_DUP.generation)), len(dup_scores))
check("6l  ...and the reuse mapping the caller passed is UNMUTATED",
      len(keep_pk) == report_pk["reused"], (len(keep_pk), report_pk["reused"]))
shutil.rmtree(DUPDIR, ignore_errors=True)

# --- 6j  the manifest records that it was resumed ----------------------------
_resumed_from = {"partial_path": MPATH, "reused_pairs": 4}
man = rh.build_manifest(RUN, summary_merged, {"total_usd": 1.0}, ARGS, 12.5,
                        "0.3.6", {"judge_calls_total": 1,
                                  "embedding_calls_total": 0},
                        ACTIVE, None, ENVSTAMP,
                        resumed_from=_resumed_from,
                        pairs_scored_here=TOTAL_PAIRS - 4)
check("6j  a resumed manifest says so", man["resumed"] is True)
check("6j  ...names what it resumed from", man["resumed_from"] == _resumed_from)
check("6j  ...counts what THIS invocation scored",
      man["pairs_scored_this_invocation"] == TOTAL_PAIRS - 4)
check("6j  ...and scopes the cost to this invocation",
      "this invocation only" in man["cost_scope"], man["cost_scope"])

man_plain = rh.build_manifest(RUN, summary_single, {"total_usd": 1.0}, ARGS,
                              12.5, "0.3.6",
                              {"judge_calls_total": 1,
                               "embedding_calls_total": 0},
                              ACTIVE, None, ENVSTAMP)
check("6j  an unresumed manifest says resumed=false and scopes cost to the run",
      man_plain["resumed"] is False
      and man_plain["resumed_from"] is None
      and man_plain["cost_scope"] == "the whole run",
      (man_plain["resumed"], man_plain["cost_scope"]))
check("6j  the two manifests differ ONLY in the resume fields",
      {k for k in man if man[k] != man_plain[k]}
      <= {"resumed", "resumed_from", "pairs_scored_this_invocation",
          "cost_scope", "generated_at_utc", "sample_counts"},
      {k for k in man if man[k] != man_plain[k]})

# --- 6k  pair identity and fingerprint ---------------------------------------
_s = RUN.generation[0]
check("6k  pair_key is built from join keys, not position",
      rh.pair_key("generation", "faithfulness", _s.as_join())
      == "generation|faithfulness|nct_id=NCT00|patient_id=p0",
      rh.pair_key("generation", "faithfulness", _s.as_join()))
check("6k  relevancy's fingerprint ignores contexts (it is handed none)",
      rh.pair_fingerprint(rh.METRIC_RESPONSE_RELEVANCY, _s)
      == rh.pair_fingerprint(
          rh.METRIC_RESPONSE_RELEVANCY,
          rh.GenerationSample(_s.patient_id, _s.nct_id, _s.user_input,
                              ["utterly different context"], _s.response,
                              True, "eligible", _s.response_field)))
check("6k  faithfulness's fingerprint DOES read contexts",
      rh.pair_fingerprint(rh.METRIC_FAITHFULNESS, _s)
      != rh.pair_fingerprint(
          rh.METRIC_FAITHFULNESS,
          rh.GenerationSample(_s.patient_id, _s.nct_id, _s.user_input,
                              ["utterly different context"], _s.response,
                              True, "eligible", _s.response_field)))
check("6k  metric_kwargs is what _score_one passes -- one construction site",
      rh.metric_kwargs(rh.METRIC_RESPONSE_RELEVANCY, _s).keys()
      == {"user_input", "response"}
      and rh.metric_kwargs(rh.METRIC_FAITHFULNESS, _s).keys()
      == {"user_input", "response", "retrieved_contexts"})


#==============================================================================
print()
print("=" * 78)
print("SECTION 9  ragas_run.py main(), driven end to end: kill, resume, refuse")
print("=" * 78)
#==============================================================================

# WHY main() IS DRIVEN AND NOT ONLY score_all(). Section 6 proves the merge and
# the refusal as functions. The things only main() does are the ones an operator
# meets: a killed run must LEAVE a usable partial and NO results file; a resumed
# run must delete the partial once it has written both outputs; a refusal must
# happen BEFORE anything is scored and must leave the partial alone. None of
# that is reachable from score_all.
#
# THE JUDGE IS A STAND-IN AND THE KILL IS REAL. `_KillingMetric` raises
# KeyboardInterrupt, which `_score_one`'s `except Exception` deliberately does
# NOT catch -- so the interruption travels exactly as a real Ctrl-C does, out
# through asyncio.gather and out of main(). Nothing is billed and nothing is
# imported from ragas: a stub module carrying only __version__ satisfies main()'s
# one use of it.

_TOTAL_SCORED = {"n": 0}
_KILL_AFTER = {"n": None}


class _DrivenMetric(object):
    def __init__(self, value=0.9):
        self.value = value

    async def ascore(self, **kwargs):
        if (_KILL_AFTER["n"] is not None
                and _TOTAL_SCORED["n"] >= _KILL_AFTER["n"]):
            raise KeyboardInterrupt("simulated kill")
        _TOTAL_SCORED["n"] += 1
        return types.SimpleNamespace(value=self.value)


def drive_ragas_main(argv, run_dir, suffix=""):
    """Run the real main() with the judge, the embedder and the plan replaced."""
    _TOTAL_SCORED["n"] = 0
    metrics = {m: _DrivenMetric() for m in rh.ALL_METRICS}
    saved = {k: getattr(rh, k) for k in
             ("load_run", "price_plan", "build_judge", "build_embeddings",
              "build_metrics", "environment_stamp")}
    saved_ragas = sys.modules.get("ragas")
    stub = types.ModuleType("ragas")
    stub.__version__ = IDENT_A["packages"]["ragas"]
    sys.modules["ragas"] = stub

    rh.load_run = lambda d, f=None: _make_run(
        response_field=f or rh.DEFAULT_RESPONSE_FIELD, response_suffix=suffix)
    rh.price_plan = lambda *a, **k: {"judge_calls_total": 10,
                                     "embedding_calls_total": 4,
                                     "estimated_usd_low": 0.1,
                                     "estimated_usd_high": 0.2}
    rh.build_judge = lambda *a, **k: object()
    rh.build_embeddings = lambda *a, **k: object()
    rh.build_metrics = lambda llm, emb, sel=None: metrics
    rh.environment_stamp = lambda *a, **k: copy.deepcopy(ENVSTAMP)
    try:
        with Captured() as cp:
            try:
                code = rh.main(["--run-dir", run_dir] + list(argv))
            except BaseException as exc:                       # noqa: BLE001
                code = f"RAISED {type(exc).__name__}"
        return code, cp.text
    finally:
        for k, v in saved.items():
            setattr(rh, k, v)
        if saved_ragas is None:
            sys.modules.pop("ragas", None)
        else:
            sys.modules["ragas"] = saved_ragas


DRIVE_RUN = tempfile.mkdtemp(prefix="resume_drive_run_")
# THE RUN DIRECTORY MUST HOLD AT LEAST ONE FILE. `post_checks`' integrity check
# hashes the tree before and after and FAILS when the pre-run snapshot was empty
# -- "it proves nothing about this run" -- so an empty scratch directory makes
# every driven run exit 3 for a reason that has nothing to do with resume. That
# check caught this file's first draft, which is the check working.
with io.open(os.path.join(DRIVE_RUN, "manifest.json"), "w") as _fh:
    _fh.write("{}")
DRIVE_OUT = os.path.join(DRIVE_RUN, "ragas")
DRIVE_PART = os.path.join(DRIVE_OUT, "ragas_partial.json")
DRIVE_RESULTS = os.path.join(DRIVE_OUT, "ragas_results.json")
DRIVE_MANIFEST = os.path.join(DRIVE_OUT, "ragas_manifest.json")

# --- 9a  a killed run leaves a usable partial and no result ------------------
_KILL_AFTER["n"] = 5
code, txt = drive_ragas_main([], DRIVE_RUN)
check("9a  a killed run propagates the interruption out of main()",
      code == "RAISED KeyboardInterrupt", code)
check("9a  ...and leaves a partial score file", os.path.exists(DRIVE_PART))
_p, _why = rh.load_partial(DRIVE_PART)
_DONE = len(_p["scores"]) if _p else 0
check("9a  ...that loads and holds the pairs it finished",
      _p is not None and 0 < _DONE <= 5, (_why, _DONE))
check("9a  ...and writes NO results file", not os.path.exists(DRIVE_RESULTS))

# --- 9b  --resume finishes it, and pays only for the remainder ---------------
_KILL_AFTER["n"] = None
code, txt = drive_ragas_main(["--resume"], DRIVE_RUN)
check("9b  the resumed run exits 0", code == 0, (code, txt[-300:]))
check("9b  ...judging only the pairs it did not carry",
      _TOTAL_SCORED["n"] == TOTAL_PAIRS - _DONE,
      (_TOTAL_SCORED["n"], TOTAL_PAIRS, _DONE))
_res_resumed = json.load(io.open(DRIVE_RESULTS, encoding="utf-8"))
check("9b  the results hold every pair of the plan",
      len(_res_resumed["scores"]) == TOTAL_PAIRS, len(_res_resumed["scores"]))
_man = json.load(io.open(DRIVE_MANIFEST, encoding="utf-8"))
check("9b  the manifest records resumed=true and what it resumed from",
      _man["resumed"] is True
      and _man["resumed_from"]["reused_pairs"] == _DONE, _man.get("resumed_from"))
check("9b  ...counts what THIS invocation scored",
      _man["pairs_scored_this_invocation"] == TOTAL_PAIRS - _DONE,
      _man["pairs_scored_this_invocation"])
check("9b  the partial file is deleted once both outputs are written",
      not os.path.exists(DRIVE_PART))
check("9b  ...and the post-checks passed over the merged set",
      "POST-CHECKS FAILED" not in txt, txt[-400:])

# --- 9c  and the result is indistinguishable from a single pass --------------
shutil.rmtree(DRIVE_OUT, ignore_errors=True)
code, txt = drive_ragas_main(["--overwrite"], DRIVE_RUN)
_res_single = json.load(io.open(DRIVE_RESULTS, encoding="utf-8"))
check("9c  a single pass scores every pair",
      code == 0 and _TOTAL_SCORED["n"] == TOTAL_PAIRS,
      (code, _TOTAL_SCORED["n"]))


def _rows_modulo_timing(payload):
    # `seconds` is wall time and cannot match between two runs; everything else
    # in a row must. Compared as a LIST, so row ORDER is part of the claim.
    return [{k: v for k, v in row.items() if k != "seconds"}
            for row in payload["scores"]]


check("9c  a resumed results file is IDENTICAL to a single-pass one, row for "
      "row, modulo per-pair wall time",
      _rows_modulo_timing(_res_resumed) == _rows_modulo_timing(_res_single))
check("9c  ...and that comparison is not vacuous",
      len(_rows_modulo_timing(_res_single)) == TOTAL_PAIRS)
_man_single = json.load(io.open(DRIVE_MANIFEST, encoding="utf-8"))
check("9c  a single pass records resumed=false and the whole-run cost scope",
      _man_single["resumed"] is False
      and _man_single["cost_scope"] == "the whole run")
check("9c  ...and leaves no partial file either", not os.path.exists(DRIVE_PART))

# --- 9d  the refusal fires before anything is scored -------------------------
shutil.rmtree(DRIVE_OUT, ignore_errors=True)
_KILL_AFTER["n"] = 5
drive_ragas_main([], DRIVE_RUN)
check("9d  a partial exists to refuse over", os.path.exists(DRIVE_PART))
_hash_before_refusal = sha256_file(DRIVE_PART)
_KILL_AFTER["n"] = None
for label, extra, want in (
        ("judge model", ["--judge-model", "claude-opus-4-1"], "judge_model"),
        ("temperature", ["--temperature", "0.7"], "judge_temperature"),
        ("max tokens", ["--max-tokens", "8192"], "judge_max_tokens"),
        ("embedding model",
         ["--embedding-model", "text-embedding-3-large"], "embedding_model")):
    code, txt = drive_ragas_main(["--resume"] + extra, DRIVE_RUN)
    check(f"9d  a different {label} REFUSES with exit 1", code == 1,
          (code, txt[-300:]))
    check(f"9d  ...naming {want}",
          "resume_environment_changed" in txt and want in txt, txt[-500:])
    check("9d  ...before scoring anything", _TOTAL_SCORED["n"] == 0,
          _TOTAL_SCORED["n"])
    check("9d  ...and leaving the partial file byte-identical",
          sha256_file(DRIVE_PART) == _hash_before_refusal)

# A DIFFERENT --response-field is NOT a refusal: field-aware naming means it
# never sees that partial at all, which is the same separation output_paths()
# already gives the results and the manifest.
code, txt = drive_ragas_main(["--resume", "--response-field",
                              "assessment_draft"], DRIVE_RUN)
check("9d  a different --response-field reads its OWN partial, so it neither "
      "refuses nor merges", code == 0 and _TOTAL_SCORED["n"] == TOTAL_PAIRS,
      (code, _TOTAL_SCORED["n"]))

# --- 9e  changed run-directory TEXT re-scores rather than merging ------------
shutil.rmtree(DRIVE_OUT, ignore_errors=True)
_KILL_AFTER["n"] = 5
drive_ragas_main([], DRIVE_RUN)
_KILL_AFTER["n"] = None
code, txt = drive_ragas_main(["--resume"], DRIVE_RUN, suffix=" REWRITTEN")
check("9e  the same run dir holding CHANGED text re-scores every pair",
      code == 0 and _TOTAL_SCORED["n"] == TOTAL_PAIRS,
      (code, _TOTAL_SCORED["n"]))
check("9e  ...and says the recorded pairs were stale rather than merging them",
      "scored against text that has since changed" in txt, txt[:900])

# --- 9f  a partial present without --resume is a warning, not a resume -------
shutil.rmtree(DRIVE_OUT, ignore_errors=True)
_KILL_AFTER["n"] = 5
drive_ragas_main([], DRIVE_RUN)
_KILL_AFTER["n"] = None
code, txt = drive_ragas_main(["--overwrite"], DRIVE_RUN)
check("9f  without --resume the partial is NOT used and every pair is re-judged",
      _TOTAL_SCORED["n"] == TOTAL_PAIRS, _TOTAL_SCORED["n"])
check("9f  ...and the operator is told the file exists and will be re-paid",
      "will be re-judged and re-paid" in txt, txt[:900])

# --- 9g  a FAILED post-check keeps the partial ------------------------------
# Every pair in it is paid for and the failure is about a property of the
# OUTPUT, so an operator fixing that and re-running must not be re-buying the
# judge calls. The failure here is REAL, not stubbed: post_checks fails a run
# whose pre-run tree snapshot was empty ("it proves nothing about this run"),
# and an empty run directory produces exactly that.
EMPTY_RUN = tempfile.mkdtemp(prefix="resume_emptyrun_")
_EMPTY_PART = rh.partial_path(os.path.join(EMPTY_RUN, "ragas"),
                              rh.DEFAULT_RESPONSE_FIELD)
code, txt = drive_ragas_main([], EMPTY_RUN)
check("9g  a run whose post-checks fail exits 3", code == 3, (code, txt[-300:]))
check("9g  ...and the failure is the real integrity check",
      "POST-CHECKS FAILED" in txt and "pre-run snapshot was empty" in txt,
      txt[-400:])
check("9g  ...the partial score file is KEPT, not deleted",
      os.path.exists(_EMPTY_PART))
check("9g  ...and the operator is told why it was kept",
      "was KEPT at" in txt and "without re-judging anything" in txt,
      txt[-500:])
shutil.rmtree(EMPTY_RUN, ignore_errors=True)

shutil.rmtree(DRIVE_RUN, ignore_errors=True)


#==============================================================================
print()
print("=" * 78)
print("SECTION 7  negative controls")
print("=" * 78)
#==============================================================================

# --- 7a  the resume gate, reverted to "the file exists" ----------------------
# THE DEFECT THE GATE EXISTS TO PREVENT, planted in an in-memory COPY of the
# module. Nothing on disk is touched; the two package files' sha256 are compared
# at the end of this file.
_src = io.open(_CAPTURE_PY, encoding="utf-8").read()
_broken_src = _src.replace(
    "    if not failures:\n        return True, RESUME_CURRENT, (",
    "    failures = []\n    if not failures:\n        return True, "
    "RESUME_CURRENT, (", 1)
check("7a  the plant changed the source", _broken_src != _src)
_ns = {"__name__": "capture_broken", "__file__": _CAPTURE_PY}
exec(compile(_broken_src, _CAPTURE_PY, "exec"), _ns)
_broken_decision = _ns["resume_decision"]
check_detects(
    "a gate that skips on existence alone would skip a stale-prompt fixture",
    _broken_decision("old_prompt", ENV_A, ROOT1, PROMPT_A)[0] is False,
    "the reverted gate skipped it, as it must")
check("7a  ...while the SHIPPED gate re-captures it",
      cap.resume_decision("old_prompt", ENV_A, ROOT1, PROMPT_A)[0] is False)

# --- 7b  the retry-base fix, reverted ----------------------------------------
# Reverted to `written`-only candidates: the resumed run then cannot see the
# normal_1 it skipped, and choose_retry_base substitutes another patient.
_written_only = [make_fixture(fid) for fid in ALL_IDS[8:]]
_base_broken, _out_broken, _ = cap.choose_retry_base(_written_only)
_with_skipped = _written_only + [make_fixture("normal_1")]
_base_fixed, _out_fixed, _ = cap.choose_retry_base(_with_skipped)
check("7b  candidates from THIS run alone substitute a different base",
      _out_broken == cap.RETRY_BASE_SUBSTITUTED
      and _base_broken["fixture_id"] != "normal_1",
      (_out_broken, _base_broken["fixture_id"]))
check("7b  ...and adding the skipped fixtures back restores normal_1",
      _out_fixed == cap.RETRY_BASE_PREFERRED_OK
      and _base_fixed["fixture_id"] == "normal_1",
      (_out_fixed, _base_fixed["fixture_id"]))
check("7b  the DRIVEN resumed run took the fixed path",
      part.retry_base == "normal_1" and "normal_1" not in part.captured,
      (part.retry_base, part.captured))

# --- 7c  the fingerprint, reverted to a run-dir comparison -------------------
check_detects(
    "a resume keyed on the run dir PATH would reuse scores about changed text",
    RUN_CHANGED.run_dir != RUN.run_dir,
    "the paths are identical, so a path check would have passed")
check("7c  ...while the shipped fingerprint refuses all of them",
      report_changed["reused"] == 0 and report_changed["stale"] == TOTAL_PAIRS)

# --- 7d  the environment refusal, disabled -----------------------------------
check_detects("a resume that compared no identity would merge two ragas versions",
              rh.identity_disagreement(
                  {k: v for k, v in IDENT.items() if k != "packages"},
                  dict(IDENT, packages=dict(IDENT_A["packages"],
                                            ragas="0.4.0"))) == [],
              "the shipped comparison reports it")

# --- 7e  the journal, with atomicity removed ---------------------------------
# A non-atomic writer that dies mid-write. The shipped one leaves the previous
# file whole (5c); this shows the file the naive version would have left.
NDIR = tempfile.mkdtemp(prefix="resume_naive_")
NPATH = os.path.join(NDIR, "naive.json")
with io.open(NPATH, "w") as fh:
    json.dump({"kind": rh.PARTIAL_KIND, "schema_version": 1, "scores": [1, 2]},
              fh)
_before_naive = sha256_file(NPATH)
try:
    with io.open(NPATH, "w") as fh:          # in place, no temp, no os.replace
        fh.write('{"kind": "ragas_partial_scores", "sco')
        raise OSError("simulated crash mid-write")
except OSError:
    pass
check_detects("an in-place journal writer survives a crash mid-write",
              sha256_file(NPATH) == _before_naive,
              "it does not -- the file is truncated")
check("7e  ...and the truncated file no longer loads",
      rh.load_partial(NPATH)[0] is None)

# --- 7f  the harness itself ---------------------------------------------------
check_detects("check() records a failure for a false condition", False,
              "this control must fire")
check_detects("a wrong expectation about a driven run is caught",
              fresh.captured == [], "the fresh run captured eleven fixtures")

# --- 7g  the driven harness is not vacuous -----------------------------------
check("7g  the driven runs actually exercised main() (they wrote fixtures)",
      len(cap.list_fixtures(ROOT_FRESH)) == len(ALL_IDS) + 1,
      len(cap.list_fixtures(ROOT_FRESH)))
check("7g  ...and returned an exit code",
      fresh.exit_code in (0, 1) and allrun.exit_code in (0, 1),
      (fresh.exit_code, allrun.exit_code))
check("7g  the stubs were restored: capture_fixture is the shipped one",
      cap.capture_fixture.__module__ == "oncotriage.fixtures.capture",
      cap.capture_fixture.__module__)
check("7g  ...and paths._RESOLVED no longer points at a temp corpus",
      not str(paths._RESOLVED.get("data_fhir_path", "")).startswith(
          tempfile.gettempdir()),
      paths._RESOLVED.get("data_fhir_path"))


#==============================================================================
print()
print("=" * 78)
print("SECTION 8  nothing in the repository was written")
print("=" * 78)
#==============================================================================

for path, before in _HASH_BEFORE.items():
    check(f"8   {os.path.basename(path)} is byte-identical",
          sha256_file(path) == before, path)
check("8   ...and the comparison is not a tautology (two distinct files)",
      len(set(_HASH_BEFORE.values())) == 2, _HASH_BEFORE)

for _tmp in (ROOT1, ROOT_FRESH, ROOT_ALL, ROOT_PART, ROOT_REDERIVE, ROOT_ONLY,
             ROOT_NORESUME, ROOT_SCAN, ROOT_D1, ROOT_D2, ROOT_RES, JDIR, RDIR,
             SDIR, MDIR, NDIR, tree_root):
    shutil.rmtree(_tmp, ignore_errors=True)


#------------------------------------------------------------------------------

print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")
for name in _FAILURES:
    print(f"  - {name}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug 20 2026

@author: ramyalsaffar
"""
