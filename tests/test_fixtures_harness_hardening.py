# Fixture Harness: the Hardening Guards
######################################

"""
Characterization Fixture Harness Hardening Test

The four guards the fixture harness gained when it was hardened against schema
bumps. Each was verified once in a scratch script and then had nothing standing
behind it; this file is that port, and it exists because THREE OF THE FOUR are
the only thing between a schema bump and a silent, expensive regression:

  1. ``read_recorded_donor_bundle()`` -- donor memory that survives a version
     bump. It reads the fixture with gzip+json and takes ONLY the donor bundle
     name, bypassing ``load_fixture()``'s version gate. Routed through the gate
     (which is what it did before), every schema bump erased the donor memory of
     every derived fixture at exactly the moment it forced a re-capture: measured
     at the v6 capture, that rebound FOUR of twelve fixtures onto different
     patients.
  2. ``choose_pool_donor()`` -- which donor ``truncation_split`` takes, and
     whether it came from memory. Hoisted out of ``main()`` so it is reachable
     at all: everything still inside ``main()`` past ``if not args.scan_only``
     costs twelve live Stage 5 calls to reach.
  3. ``stage5_cost_summary()`` -- what a capture cost. Its discriminator is the
     load-bearing part and it is NOT ``fixture_kind``: five of twelve fixtures
     are ``constructed`` and four of those are real billed runs on a derived
     INPUT. Excluding by kind reports $0.53 of a $1.14 run.
  4. ``build_deterministic_prefix()``'s no-prompt convention -- from schema v7
     it records ``None``, not ``sha256("")``, for a run that never reached
     Stage 5. ``sha256("")`` is a well-known constant that reads as a real
     digest, so a regression here republishes "a prompt was rendered" for the
     one fixture that terminates at ``node_no_candidates``.
  5. ``build_deterministic_prefix()``'s stage5 VERDICT projection -- schema v8
     widened it from six fields to ten. The four it gained are the ones nothing
     else in the harness could see: the COMPOSED ``assessment`` and the
     ``assessment_draft`` it was composed from (which has no database column
     anywhere, so these files are its only durable record), and the two
     emission-provenance stamps ``emission_index`` / ``call_index``. Section 5
     pins the exact key set, because the cost of the pre-v8 state was not a
     wrong value but a missing one: a total regression of the assessment
     composition replayed clean on all twelve.

WHAT THIS FILE COSTS AND NEEDS: nothing. No network, no keys, no spend, no live
Qdrant, no corpus, no git history, no Docker. It is NOT in the collision matrix
-- derived, not assumed: every file it writes is inside a fresh
``tempfile.mkdtemp()``, it patches no repository file, and the two package
modules it reads are written by neither of the suite's two writers.

IT NEVER TOUCHES THE REAL FIXTURE DIRECTORY. Every fixture it reads is one it
built itself in a temp directory. ``fixture_root()`` is not called, no fixture
is written to the project root, and the twelve on-disk fixtures are not opened
-- so a bug in this file cannot cost a $1.14 re-capture.

NEGATIVE CONTROLS. Every section carries at least one, and the controls run
through the SAME ``check()`` the assertions use rather than a parallel copy:
``check_detects()`` drives a deliberately wrong expectation, requires ``check()``
to record a failure, and then restores the counters. A control that lives
outside the harness it is vouching for proves nothing about the harness.
"""

import gzip
import hashlib
import json
import os
import sys
import tempfile

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
from oncotriage.fixtures.capture import (
    CASE_ABLATION,
    CASE_NORMAL,
    DONOR_FROM_MEMORY,
    DONOR_NOT_IN_POOL,
    DONOR_NO_MEMORY,
    DONOR_OUTCOMES,
    FIXTURE_KIND_CONSTRUCTED,
    FIXTURE_KIND_RECORDED,
    MALFORMED_PREFIX_CHARS,
    RETRY_BASE_NONE,
    RETRY_BASE_OUTCOMES,
    RETRY_BASE_PREFERRED,
    RETRY_BASE_PREFERRED_OK,
    RETRY_BASE_SUBSTITUTED,
    RecordingSink,
    SCHEMA_VERSION,
    _recordings_are_copied,
    build_constructed_retry_fixture,
    build_deterministic_prefix,
    choose_pool_donor,
    choose_retry_base,
    load_fixture,
    read_recorded_donor_bundle,
    retry_base_disqualification,
    sha256_text,
    stage5_cost_summary,
)


# ===========================================================================
# HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []
_QUIET = []


def check(label: str, actual, expected) -> None:
    """Assert equality, record the outcome, never abort the run."""
    if actual == expected:
        _RESULTS["passed"] += 1
        if not _QUIET:
            print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected: {expected}\n"
                         f"          actual:   {actual}")
        if not _QUIET:
            print(f"  FAIL  {label}")
            print(f"          expected: {expected}")
            print(f"          actual:   {actual}")


def check_detects(label: str, actual, expected) -> None:
    """Give check() a WRONG expectation and require it to record a failure.

    Drives the real check(), quietly, then restores the counters so the control
    does not pollute the run's totals. This is what stops the whole file passing
    vacuously: if check() ever stopped recording failures -- a stray `return`, a
    comparison that always holds -- every assertion above would still print PASS
    and only this would notice.
    """
    before_failed = _RESULTS["failed"]
    before_passed = _RESULTS["passed"]
    snapshot = list(_FAILURES)
    _QUIET.append(True)
    try:
        check(f"(control) {label}", actual, expected)
    finally:
        _QUIET.pop()
    detected = _RESULTS["failed"] == before_failed + 1
    _RESULTS["failed"] = before_failed
    _RESULTS["passed"] = before_passed
    _FAILURES[:] = snapshot
    check(f"control fires: {label}", detected, True)


def drive(fn, *args, **kwargs):
    """Call production code, turning a raise into a comparable value.

    A bare call inside check()'s argument list lets an exception escape while
    the argument is being evaluated, which kills the run and reports one
    traceback where it owes a summary. This project has shipped that shape five
    times; the scratch version of this file shipped it a sixth.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:                                   # noqa: BLE001
        return f"<raised {type(exc).__name__}: {exc}>"


def field(obj, *keys):
    """Read a nested key off a drive() result WITHOUT raising.

    drive() answers a marker STRING when production code raised, and
    subscripting that string is a TypeError at module level -- outside any
    check(), so the run dies with one traceback where it owes a summary.
    """
    cursor = obj
    for key in keys:
        if not isinstance(cursor, dict) or key not in cursor:
            return f"<no {'.'.join(map(str, keys))} in {cursor!r}>"[:140]
        cursor = cursor[key]
    return cursor


def rnd(value):
    """round() that survives a drive() marker string instead of raising."""
    return round(value, 10) if isinstance(value, (int, float)) else value


# EVERY file this test writes lives here. The real fixture directory is never
# resolved, never read and never written: fixture_root() is not called anywhere
# in this file, so a defect here cannot damage twelve fixtures that cost $1.14
# of live Stage 5 calls to produce.
_TMP = tempfile.mkdtemp(prefix="fixtures_harness_test_")


def write_gzip_fixture(name: str, payload: dict) -> str:
    """A fixture file with write_fixture()'s exact storage settings."""
    path = os.path.join(_TMP, f"{name}.json.gz")
    with gzip.GzipFile(path, "wb", compresslevel=9, mtime=0) as handle:
        handle.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return path


# ===========================================================================
# SECTION 1: read_recorded_donor_bundle() -- memory across the version gate
# ===========================================================================

print("\n" + "=" * 70)
print("SECTION 1: donor memory survives a schema bump")
print("=" * 70)

# THE VERSIONS ARE THE POINT. v2 is when derivation.donor_bundle was introduced,
# v3/v6/v7 are versions the twelve fixtures have actually sat at, and
# SCHEMA_VERSION is current. Every one must answer, because the failure this
# guards is a re-capture that repoints a fixture at a different patient BECAUSE
# its file was one bump stale.
for _version in (2, 3, 6, 7, SCHEMA_VERSION):
    _path = write_gzip_fixture(
        f"derivation_v{_version}",
        {"schema_version": _version,
         "derivation": {"recipe": "mcode_genomic_variant",
                        "donor_bundle": f"Donor_v{_version}.json"}})
    check(f"donor recovered from a v{_version} fixture",
          drive(read_recorded_donor_bundle, _path), f"Donor_v{_version}.json")

# ...and the gate those reads bypass is still shut for everything else, which is
# what makes the bypass a narrow exception rather than a hole.
_stale = write_gzip_fixture(
    "gate_probe", {"schema_version": 3,
                   "derivation": {"donor_bundle": "Gated.json"}})
_gated = drive(load_fixture, _stale)
check("load_fixture STILL refuses that same stale file",
      isinstance(_gated, str) and "RE-CAPTURE REQUIRED" in _gated, True)

# truncation_split's shape: no derivation block at all, donor in construction.
_construction = write_gzip_fixture(
    "construction_only",
    {"schema_version": SCHEMA_VERSION, "derivation": None,
     "construction": {"derived_from_bundle": "Donor_Construction.json"}})
check("donor recovered from construction.derived_from_bundle",
      drive(read_recorded_donor_bundle, _construction),
      "Donor_Construction.json")

# derivation wins when both are present: it is the recipe's own record.
_both = write_gzip_fixture(
    "both_blocks",
    {"schema_version": SCHEMA_VERSION,
     "derivation": {"donor_bundle": "FromDerivation.json"},
     "construction": {"derived_from_bundle": "FromConstruction.json"}})
check("derivation.donor_bundle outranks construction.derived_from_bundle",
      drive(read_recorded_donor_bundle, _both), "FromDerivation.json")

# --- the documented "no memory" answers, none of which may raise -------------
check("absent file -> None",
      drive(read_recorded_donor_bundle, os.path.join(_TMP, "nothing_here.json.gz")),
      None)

_corrupt = os.path.join(_TMP, "corrupt.json.gz")
with open(_corrupt, "wb") as _fh:
    _fh.write(b"this is not gzip at all")
check("corrupt gzip -> None, not a raise",
      drive(read_recorded_donor_bundle, _corrupt), None)

_truncated = os.path.join(_TMP, "truncated.json.gz")
with gzip.GzipFile(_truncated, "wb", compresslevel=9, mtime=0) as _fh:
    _fh.write(b'{"schema_version": 7, "derivation": {')
check("truncated JSON -> None, not a raise",
      drive(read_recorded_donor_bundle, _truncated), None)

check("no derivation and no construction -> None",
      drive(read_recorded_donor_bundle,
            write_gzip_fixture("bare", {"schema_version": SCHEMA_VERSION})),
      None)

check("a fixture derived from another FIXTURE reports no donor bundle",
      drive(read_recorded_donor_bundle,
            write_gzip_fixture("copied", {"schema_version": SCHEMA_VERSION,
                                          "construction": {"derived_from": "normal_1"}})),
      None)

# NEGATIVE CONTROL required by the brief: the field is PRESENT but EMPTY. An
# empty donor name is not memory -- reusing it would mean matching a pool entry
# whose bundle is "", and a truthiness test is the only thing standing between
# that and a lookup for a patient who does not exist.
check("an EMPTY donor_bundle is not reported as memory",
      drive(read_recorded_donor_bundle,
            write_gzip_fixture("empty_donor",
                               {"schema_version": SCHEMA_VERSION,
                                "derivation": {"donor_bundle": ""}})),
      None)
check("an empty construction donor is not reported as memory either",
      drive(read_recorded_donor_bundle,
            write_gzip_fixture("empty_con",
                               {"schema_version": SCHEMA_VERSION,
                                "construction": {"derived_from_bundle": ""}})),
      None)

print("  [controls]")
# Non-degeneracy: the reader is not simply answering None for everything, and
# not simply echoing one constant.
check("the reader discriminates between two files",
      drive(read_recorded_donor_bundle, _both)
      == drive(read_recorded_donor_bundle, _construction), False)
check_detects("a v3 donor read that returned None would be caught",
              drive(read_recorded_donor_bundle,
                    write_gzip_fixture("ctl_v3",
                                       {"schema_version": 3,
                                        "derivation": {"donor_bundle": "X.json"}})),
              None)


# ===========================================================================
# SECTION 2: choose_pool_donor() -- the hoisted decision
# ===========================================================================

print("\n" + "=" * 70)
print("SECTION 2: the truncation donor decision")
print("=" * 70)


def pool(*names):
    """A donor pool shaped like the selection's rows -- only `bundle` is read."""
    return [{"bundle": n, "patient_id": f"pid-{n}"} for n in names]


# --- recorded donor still available: chosen, and REMOVED from the pool -------
_donors = pool("A.json", "B.json", "C.json")
_chosen, _outcome = drive(choose_pool_donor, "B.json", _donors) or (None, None)
check("recorded donor still in the pool is chosen",
      field(_chosen, "bundle"), "B.json")
check("...and the outcome says it came from memory", _outcome, DONOR_FROM_MEMORY)
check("...and it is POPPED, so _next_donor cannot hand it out again",
      [d["bundle"] for d in _donors], ["A.json", "C.json"])

# Popping the FIRST entry is the ordinary case and must behave the same.
_donors = pool("A.json", "B.json")
_chosen, _outcome = drive(choose_pool_donor, "A.json", _donors) or (None, None)
check("the pool head can be the recorded donor", field(_chosen, "bundle"), "A.json")
check("...popped from the head", [d["bundle"] for d in _donors], ["B.json"])

# --- recorded donor no longer available: fallback, POOL UNTOUCHED -----------
# This is the case that matters most: the donor was taken as an ablation or a
# normal earlier in the same run, so reusing it would put two fixtures on one
# patient. The miss must not consume anybody.
_donors = pool("A.json", "B.json", "C.json")
_chosen, _outcome = drive(choose_pool_donor, "TAKEN.json", _donors) or (None, None)
check("recorded donor absent from the pool -> no donor", _chosen, None)
check("...and the outcome names the reason", _outcome, DONOR_NOT_IN_POOL)
check("...and the miss consumed nobody",
      [d["bundle"] for d in _donors], ["A.json", "B.json", "C.json"])

# --- no memory at all: fallback, pool untouched ------------------------------
for _empty in (None, ""):
    _donors = pool("A.json", "B.json")
    _chosen, _outcome = drive(choose_pool_donor, _empty, _donors) or (None, None)
    check(f"no recorded donor ({_empty!r}) -> no donor", _chosen, None)
    check(f"...outcome is no_memory ({_empty!r})", _outcome, DONOR_NO_MEMORY)
    check(f"...pool untouched ({_empty!r})",
          [d["bundle"] for d in _donors], ["A.json", "B.json"])

# An empty pool is a legitimate state (every ordinary patient already taken) and
# must answer not_in_pool rather than raising -- the caller's _next_donor() is
# what raises, and it says something else.
_donors = []
_chosen, _outcome = drive(choose_pool_donor, "A.json", _donors) or (None, None)
check("an empty pool answers not_in_pool rather than raising",
      (_chosen, _outcome), (None, DONOR_NOT_IN_POOL))

# A malformed pool row RAISES rather than being skipped, which is what the
# nested version did and what the hoist had to preserve. Skipping it silently
# would report "recorded donor no longer available" for a broken pool and
# repoint a fixture on the strength of that wrong diagnosis.
_malformed = drive(choose_pool_donor, "A.json", [{"patient_id": "no-bundle-key"}])
check("a pool row with no bundle name raises rather than being skipped",
      isinstance(_malformed, str) and "KeyError" in _malformed, True)

# --- the outcome vocabulary is closed and every member is reachable ----------
check("DONOR_OUTCOMES is closed and has three members", len(DONOR_OUTCOMES), 3)
check("every outcome observed above is in the vocabulary",
      sorted({DONOR_FROM_MEMORY, DONOR_NOT_IN_POOL, DONOR_NO_MEMORY}),
      sorted(DONOR_OUTCOMES))
check("the three members are distinct",
      len(set(DONOR_OUTCOMES)), 3)

# --- purity: no file is read, no path resolved ------------------------------
# choose_pool_donor is the half of the old nested helper that CAN be tested
# without a paid capture, and that is only true while it stays free of I/O.
_open_calls = []
_real_open = open
try:
    import builtins

    def _trapped_open(*a, **k):
        _open_calls.append(a[0] if a else None)
        return _real_open(*a, **k)

    builtins.open = _trapped_open
    _donors = pool("A.json")
    drive(choose_pool_donor, "A.json", _donors)
    drive(choose_pool_donor, "MISSING.json", _donors)
finally:
    builtins.open = _real_open
check("choose_pool_donor opens no file", _open_calls, [])

print("  [controls]")
check_detects("a pool that was NOT popped would be caught",
              [d["bundle"] for d in pool("A.json", "B.json")],
              ["A.json"])
check_detects("a fallback misreported as memory would be caught",
              DONOR_NOT_IN_POOL, DONOR_FROM_MEMORY)
# Non-degeneracy: the open trap can actually see a read, so `[]` above means
# "nothing was opened" rather than "the trap was never armed".
_probe = []
_real_open2 = open
try:
    import builtins

    def _probe_open(*a, **k):
        _probe.append(a[0] if a else None)
        return _real_open2(*a, **k)

    builtins.open = _probe_open
    with open(os.path.join(_TMP, "trap_probe.txt"), "w") as _fh:
        _fh.write("x")
finally:
    builtins.open = _real_open2
check("control: the open trap does observe a real open", len(_probe), 1)


# ===========================================================================
# SECTION 3: stage5_cost_summary() -- the discriminator is load-bearing
# ===========================================================================

print("\n" + "=" * 70)
print("SECTION 3: capture cost and the exclusion discriminator")
print("=" * 70)

MODEL = config.MATCHING_MODEL


def priced(model, input_tokens, output_tokens):
    """The expected cost, computed from the price table rather than from the
    function under test.

    STATED LIMIT: the table is the same one get_model_cost() reads, so this
    re-derives the ARITHMETIC and the summing, not the prices. What it does
    catch -- and what this section is about -- is a fixture counted that should
    not be, a fixture dropped that should not be, and a token total that does
    not add up.
    """
    entry = config.PRICING_CONFIG["models"][model]
    return ((input_tokens / 1_000_000) * entry["input"]
            + (output_tokens / 1_000_000) * entry["output"])


def fixture(fixture_id, calls, construction=None, kind="recorded"):
    """A fixture-shaped dict carrying only the keys the cost function reads."""
    return {
        "fixture_id": fixture_id,
        "fixture_kind": kind,
        "construction": construction,
        "recordings": {"chat_completions": [
            {"response": {"model": model,
                          "usage": {"prompt_tokens": tin,
                                    "completion_tokens": tout,
                                    "reasoning_tokens": reasoning}}}
            for (model, tin, tout, reasoning) in calls
        ]},
    }


# The four kinds side by side, exactly as the twelve on disk are made up.
COHORT = fixture("normal_1", [(MODEL, 1000, 500, 0)])
DERIVED_INPUT = fixture("mcode_genomic_variant", [(MODEL, 700, 300, 0)],
                        construction={"derived_from_bundle": "Some_Patient.json"},
                        kind="constructed")
COPIED = fixture("llm_classifier_parse_retry_constructed",
                 [(MODEL, 9999, 8888, 0)],
                 construction={"derived_from": "normal_1"}, kind="constructed")
NO_CALLS = fixture("no_candidates_pediatric_age", [],
                   construction={"derived_from_bundle": "Other_Patient.json"},
                   kind="constructed")

_all = [COHORT, DERIVED_INPUT, COPIED, NO_CALLS]
_summary = drive(stage5_cost_summary, _all)

# 1000+700 in and 500+300 out: the copied one is excluded, the empty one adds 0.
check("cost sums only the billed fixtures",
      rnd(field(_summary, "cost_usd")), rnd(priced(MODEL, 1700, 800)))
check("input tokens exclude the copied fixture",
      field(_summary, "input_tokens"), 1700)
check("output tokens exclude the copied fixture",
      field(_summary, "output_tokens"), 800)
check("the copied fixture is NAMED as excluded",
      field(_summary, "excluded_fixture_ids"),
      ["llm_classifier_parse_retry_constructed"])
check("the derived-INPUT fixture is billed, not excluded",
      "mcode_genomic_variant" in (field(_summary, "excluded_fixture_ids") or []),
      False)
check("two priced calls", field(_summary, "calls_priced"), 2)
check("the total is complete", field(_summary, "cost_complete"), True)

# A zero-call fixture on its own contributes zero and does not raise.
_zero = drive(stage5_cost_summary, [NO_CALLS])
check("a fixture with no Stage 5 calls costs 0.0", field(_zero, "cost_usd"), 0.0)
check("...and is still a complete answer", field(_zero, "cost_complete"), True)
check("...and is not reported as excluded",
      field(_zero, "excluded_fixture_ids"), [])

# The discriminator itself, both ways round.
check("construction.derived_from means the recordings were copied",
      _recordings_are_copied(COPIED), True)
check("construction.derived_from_bundle does NOT",
      _recordings_are_copied(DERIVED_INPUT), False)
check("a cohort fixture with no construction block does NOT",
      _recordings_are_copied(COHORT), False)

print("  [controls]")
# THE CONTROL THE BRIEF ASKS FOR: flipping the discriminator must move the
# total. Same four fixtures, but the copied one relabelled as a derived input --
# if the exclusion were not load-bearing, the number would not move.
_flipped = [COHORT, DERIVED_INPUT,
            fixture("llm_classifier_parse_retry_constructed",
                    [(MODEL, 9999, 8888, 0)],
                    construction={"derived_from_bundle": "normal_1.json"},
                    kind="constructed"),
            NO_CALLS]
_flipped_summary = drive(stage5_cost_summary, _flipped)
check("flipping the discriminator changes the total",
      rnd(field(_summary, "cost_usd")) == rnd(field(_flipped_summary, "cost_usd")),
      False)
check("...and the flipped total is the one that includes it",
      rnd(field(_flipped_summary, "cost_usd")),
      rnd(priced(MODEL, 1700 + 9999, 800 + 8888)))
# Excluding by fixture_kind -- the natural-looking rule -- would drop three of
# these four, and that is why the structural test exists.
check("control: fixture_kind would wrongly exclude three of the four",
      sum(1 for f in _all if f["fixture_kind"] == "constructed"), 3)
check_detects("a cost that ignored the exclusion would be caught",
              rnd(field(_summary, "cost_usd")),
              rnd(priced(MODEL, 1700 + 9999, 800 + 8888)))


# ===========================================================================
# SECTION 4: the v7 no-prompt convention
# ===========================================================================

print("\n" + "=" * 70)
print("SECTION 4: schema v7 records None, never sha256(\"\")")
print("=" * 70)

EMPTY_SHA = hashlib.sha256(b"").hexdigest()
check("sha256('') is the constant this guard is about",
      EMPTY_SHA,
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
check("...and sha256_text agrees with hashlib", sha256_text(""), EMPTY_SHA)


def prefix_for(prompt_value):
    """Drive the REAL prefix builder for a run that never reached Stage 5."""
    state = {"trials": [], "reranked_trials": [], "hybrid_results": [],
             "filtered_trials": [], "evaluations": []}
    result = {"llm_classifier_prompt": prompt_value,
              "llm_classifier_prompt_version": "1.4.0",
              "llm_classifier_prompt_sha256": None,
              "evaluations": []}
    return drive(build_deterministic_prefix, state, result, RecordingSink())


for _label, _value in (("absent (None)", None), ("empty string", "")):
    _prefix = prefix_for(_value)
    check(f"no prompt, {_label}: combined hash is None",
          field(_prefix, "stage5", "llm_classifier_combined_prompt_sha256"), None)
    check(f"no prompt, {_label}: sibling prompt hash is None",
          field(_prefix, "stage5", "llm_classifier_prompt_sha256"), None)
    check(f"no prompt, {_label}: the template VERSION is still recorded",
          field(_prefix, "stage5", "llm_classifier_prompt_version"), "1.4.0")

# Non-degeneracy: a real prompt must still be hashed, or "None" above would be
# satisfied by a builder that had stopped recording the field at all.
_real = prefix_for("SYSTEM MESSAGE...USER MESSAGE...")
check("a real prompt is still hashed",
      field(_real, "stage5", "llm_classifier_combined_prompt_sha256"),
      sha256_text("SYSTEM MESSAGE...USER MESSAGE..."))

check("the current schema version is 8", SCHEMA_VERSION, 8)

print("  [controls]")
check("control: the pre-v7 value would have been sha256('')",
      field(prefix_for(None), "stage5",
            "llm_classifier_combined_prompt_sha256") == EMPTY_SHA,
      False)
check_detects("a regression back to sha256('') would be caught",
              field(prefix_for(None), "stage5",
                    "llm_classifier_combined_prompt_sha256"),
              EMPTY_SHA)


# ===========================================================================
# SECTION 5: the v8 stage5 verdict projection
# ===========================================================================

print("\n" + "=" * 70)
print("SECTION 5: schema v8 projects ten fields onto every verdict")
print("=" * 70)

# The six the projection has always carried, and the four v8 added. Pinned as a
# SET rather than checked one at a time, because both failure directions matter
# and only one of them is a value error: a field silently DROPPED reads as
# absent on both sides of a replay and passes, and a field silently ADDED goes
# into every fixture without a version bump, which is what the bump exists to
# stop.
V8_VERDICT_KEYS = {
    "nct_id", "eligible", "match_score", "trial_number",
    "criteria_not_applicable", "not_evaluable_reason",
    "assessment", "assessment_draft",
    "emission_index", "call_index",
}

# A MODEL-RETURNED verdict and a PIPELINE-CONSTRUCTED one, because the two
# differ in exactly the place v8 is about: the constructed entry never stood in
# a response, so both stamps are None. Values are deliberately all distinct --
# no two fields share a value anywhere below -- so a projection that copied the
# wrong key into a slot cannot satisfy these checks by coincidence. That is not
# hypothetical for this pair: `assessment_draft` is seeded from `assessment` by
# setdefault in the node, so a projection reading `e.get("assessment")` into
# BOTH slots is the natural mistake, and only distinct values catch it.
_MODEL_VERDICT = {
    "nct_id": "NCT00000001",
    "eligible": "eligible",
    "match_score": 0.75,
    "trial_number": 3,
    "criteria_not_applicable": 1,
    "not_evaluable_reason": None,
    "assessment": "COMPOSED: 3 of 4 inclusion criteria confirmed.",
    "assessment_draft": "DRAFT: the patient appears to qualify.",
    "emission_index": 2,
    "call_index": 1,
}
_CONSTRUCTED_VERDICT = {
    "nct_id": "NCT00000002",
    "eligible": "not_evaluable",
    "match_score": 0.0,
    "trial_number": 7,
    "criteria_not_applicable": 0,
    "not_evaluable_reason": "model_omitted",
    "assessment": "The model returned no entry for this trial. Not assessed.",
    "assessment_draft": "The model returned no entry for this trial. Not assessed.",
    "emission_index": None,
    "call_index": None,
}
# A THIRD entry whose emission_index is 0 and whose assessment is the empty
# string. Both are falsy and neither is None, which is the distinction the
# project's NULL-vs-zero rule turns on: a projection written with `or None`,
# `or 0` or a truthiness test passes every check above and fails here.
_ZERO_VERDICT = {
    "nct_id": "NCT00000003",
    "eligible": "not_eligible",
    "match_score": 0.0,
    "trial_number": 1,
    "criteria_not_applicable": 0,
    "not_evaluable_reason": None,
    "assessment": "",
    "assessment_draft": "",
    "emission_index": 0,
    "call_index": 1,
}


def verdicts_for(matches=(), near_misses=(), not_evaluable=()):
    """Drive the REAL prefix builder and hand back its stage5.verdicts list."""
    state = {"trials": [], "reranked_trials": [], "hybrid_results": [],
             "filtered_trials": [], "evaluations": []}
    result = {"llm_classifier_prompt": "SYSTEM...USER...",
              "llm_classifier_prompt_version": "1.7.0",
              "llm_classifier_prompt_sha256": "deadbeef",
              "matches": [dict(e) for e in matches],
              "near_misses": [dict(e) for e in near_misses],
              "not_evaluable": [dict(e) for e in not_evaluable]}
    return field(drive(build_deterministic_prefix, state, result, RecordingSink()),
                 "stage5", "verdicts")


def at(seq, index):
    """Index a list that may be a drive()/field() marker string, without raising.

    A bare `verdicts[0]` is an IndexError at module level -- outside any
    check() -- so a defect that empties the projection would report ONE
    traceback where it owes the whole section. Three files in this suite have
    had to close exactly this hole.
    """
    if not isinstance(seq, list) or not -len(seq) <= index < len(seq):
        return f"<no index {index} in {seq!r}>"[:140]
    return seq[index]


_v = verdicts_for(matches=[_MODEL_VERDICT],
                  near_misses=[_ZERO_VERDICT],
                  not_evaluable=[_CONSTRUCTED_VERDICT])

check("all three verdicts are projected",
      len(_v) if isinstance(_v, list) else _v, 3)
check("...in matches + near_misses + not_evaluable order",
      [field(at(_v, i), "nct_id") for i in range(3)],
      ["NCT00000001", "NCT00000003", "NCT00000002"])
check("the projected key set is exactly the ten v8 fields",
      set(at(_v, 0)) if isinstance(at(_v, 0), dict) else at(_v, 0),
      V8_VERDICT_KEYS)
check("...on the constructed verdict too",
      set(at(_v, 2)) if isinstance(at(_v, 2), dict) else at(_v, 2),
      V8_VERDICT_KEYS)

# --- the four v8 fields, copied as stored ----------------------------------
for _key in ("assessment", "assessment_draft", "emission_index", "call_index"):
    check(f"model-returned verdict: {_key} is copied verbatim",
          field(at(_v, 0), _key), _MODEL_VERDICT[_key])
    check(f"constructed verdict: {_key} is copied verbatim",
          field(at(_v, 2), _key), _CONSTRUCTED_VERDICT[_key])

# NULL AND ZERO ARE DIFFERENT FACTS, and equality alone cannot say which one
# came back: 0 == False, "" == False, and a check written against a falsy
# expectation is satisfied by any of them. Every assertion here compares the
# VALUE and an explicit `is None` together, as one tuple, so the two halves
# cannot be satisfied separately.
for _label, _index, _key, _expected in (
        ("constructed verdict: emission_index", 2, "emission_index", None),
        ("constructed verdict: call_index",     2, "call_index",     None),
        ("emission_index 0 survives as 0",      1, "emission_index", 0),
        ("call_index 1 survives",               1, "call_index",     1),
        ("an EMPTY assessment survives as ''",  1, "assessment",     ""),
        ("an EMPTY assessment_draft survives",  1, "assessment_draft", ""),
):
    _actual = field(at(_v, _index), _key)
    check(f"{_label}, and is{'' if _expected is None else ' not'} None",
          (_actual, _actual is None), (_expected, _expected is None))

# --- the pair is two fields, not one read twice ----------------------------
check("assessment and assessment_draft are projected from DIFFERENT keys",
      field(at(_v, 0), "assessment") == field(at(_v, 0), "assessment_draft"),
      False)

# --- an entry that carries none of the four ---------------------------------
# The four are read with .get(), so a hand-built or older-shaped entry projects
# None rather than raising. That is the documented behaviour and it is the
# reason the VERSION gate exists: None here is indistinguishable from a stored
# None, which is why a v7 file may not be diffed by v8 code.
_bare = verdicts_for(matches=[{"nct_id": "NCT00000004", "eligible": "eligible"}])
check("an entry missing all four projects None rather than raising",
      [field(at(_bare, 0), k) for k in
       ("assessment", "assessment_draft", "emission_index", "call_index")],
      [None, None, None, None])

# --- non-degeneracy ---------------------------------------------------------
# Without these, every assertion above is also satisfied by a builder that
# projected one constant, or that answered None for everything.
check("non-degeneracy: the three verdicts are distinct objects",
      len({field(at(_v, i), "nct_id") for i in range(3)}), 3)
check("non-degeneracy: at least one projected assessment is non-empty",
      any(field(at(_v, i), "assessment") for i in range(3)), True)
check("non-degeneracy: at least one emission stamp is a real integer",
      sorted(i for i in (field(at(_v, j), "emission_index") for j in range(3))
             if isinstance(i, int)), [0, 2])

print("  [controls]")
check_detects("a projection that DROPPED the four v8 fields would be caught",
              set(at(_v, 0)) - {"assessment", "assessment_draft",
                                "emission_index", "call_index"},
              V8_VERDICT_KEYS)
check_detects("a projection that ADDED an unversioned field would be caught",
              set(at(_v, 0)) | {"an_unversioned_field"},
              V8_VERDICT_KEYS)
# These three drive REAL projected values against a wrong expectation. A
# control built out of literals (`0 is None`) would fire whatever the projection
# did, which is a control that proves the harness works and nothing about the
# code under it.
check_detects("assessment_draft projected from `assessment` would be caught",
              field(at(_v, 0), "assessment_draft"),
              field(at(_v, 0), "assessment"))
check_detects("a constructed stamp coerced from None to 0 would be caught",
              field(at(_v, 2), "emission_index"), 0)
check_detects("an emission_index of 0 flattened to None would be caught",
              field(at(_v, 1), "emission_index"), None)


# ===========================================================================
# SECTION 6: choose_retry_base() -- the base is picked by SHAPE, not by name
# ===========================================================================

print("\n" + "=" * 70)
print("SECTION 6: the constructed retry fixture's base is chosen by shape")
print("=" * 70)

# WHAT THIS GUARDS, AND IT IS NOT HYPOTHETICAL. build_constructed_retry_fixture
# requires a base that recorded exactly ONE Stage 5 call, while main() used to
# select that base by NAME. A capture measured the gap: `normal_1` came back
# having SPLIT into two calls -- its filtered trial set hit the model's output
# ceiling, which is what the truncation path exists for and not a defect -- the
# constructor refused it, and eleven paid fixtures were written while the
# twelfth was left on disk at the previous schema version.

# Long enough that MALFORMED_PREFIX_CHARS cuts it mid-structure, so the
# constructor's own "does the truncated payload still parse" probe is satisfied
# by real data rather than by a string chosen to be convenient.
_VERDICT_JSON = json.dumps([{
    "nct_id": "NCT00000001",
    "eligible": "eligible",
    "match_score": 0.5,
    "criteria": [{"criterion": "c" * (MALFORMED_PREFIX_CHARS * 2),
                  "patient_value": "v", "status": "met"}],
}])


def make_base(fixture_id, calls=1, kind=FIXTURE_KIND_RECORDED, labels=None,
              verdict_call_index=1):
    """A fixture with a REAL deterministic prefix, built by the shipped builder.

    The prefix is not hand-written: the constructor compares its own stage5 key
    set against the base's, so a hand-built prefix would make that comparison
    agree with this file instead of with build_deterministic_prefix().
    """
    sink = RecordingSink()
    for index in range(calls):
        sink.chat_completions.append({
            "request": {"model": "m", "messages": [{"role": "user", "content": "u"}]},
            "response": {"content": _VERDICT_JSON, "finish_reason": "stop",
                         "model": "m",
                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
            "call_index": index + 1,
        })
    state = {"trials": [], "reranked_trials": [], "hybrid_results": [],
             "filtered_trials": [], "evaluations": []}
    result = {"llm_classifier_prompt": "SYSTEM...USER...",
              "llm_classifier_prompt_version": "1.7.0",
              "llm_classifier_prompt_sha256": "deadbeef",
              "llm_classifier_calls": calls,
              "matches": [{"nct_id": "NCT00000001", "eligible": "eligible",
                           "match_score": 0.5, "trial_number": 1,
                           "criteria_not_applicable": 0,
                           "assessment": "COMPOSED.", "assessment_draft": "DRAFT.",
                           "emission_index": 0, "call_index": verdict_call_index}]}
    return {
        "fixture_id": fixture_id,
        "fixture_kind": kind,
        "case_labels": [CASE_NORMAL] if labels is None else labels,
        "captured_at_utc": "2026-01-01T00:00:00+00:00",
        "construction": None,
        "recordings": {"chat_completions": sink.chat_completions},
        "deterministic_prefix": build_deterministic_prefix(state, result, sink),
    }


# --- the preferred base still qualifies -------------------------------------
_ok = [make_base(RETRY_BASE_PREFERRED), make_base("normal_3")]
_base, _outcome, _detail = drive(choose_retry_base, _ok)
check("the preferred base is taken when it qualifies",
      (field(_base, "fixture_id"), _outcome), (RETRY_BASE_PREFERRED,
                                               RETRY_BASE_PREFERRED_OK))

# --- it split, so another single-call normal run stands in -------------------
_split = [make_base(RETRY_BASE_PREFERRED, calls=2), make_base("normal_3"),
          make_base("normal_2", calls=2)]
_base, _outcome, _detail = drive(choose_retry_base, _split)
check("a SPLIT preferred base is substituted, not used",
      (field(_base, "fixture_id"), _outcome), ("normal_3", RETRY_BASE_SUBSTITUTED))
check("...and the detail names why the preferred one was rejected",
      isinstance(_detail, str) and "2 Stage 5 call(s)" in _detail
      and RETRY_BASE_PREFERRED in _detail, True)

# --- an ABLATION run is not a substitute even when it is the only single-call
# candidate. It would carry ablation_flags into a fixture whose case_labels
# declare only the retry case. This is the real set's shape, not an invention:
# at the v8 capture the only other single-call recorded run was bm25_only.
_ablation_only = [make_base(RETRY_BASE_PREFERRED, calls=2),
                  make_base("ablation_bm25_only", labels=[CASE_ABLATION])]
_base, _outcome, _detail = drive(choose_retry_base, _ablation_only)
check("an ablation run is never substituted in",
      (_base, _outcome), (None, RETRY_BASE_NONE))
check("...and the refusal names the label that disqualified it",
      isinstance(_detail, str) and "case_labels" in _detail, True)

# --- a constructed fixture is not a base ------------------------------------
_constructed = [make_base("mcode_genomic_variant", kind=FIXTURE_KIND_CONSTRUCTED)]
check("a constructed fixture is never a base",
      drive(choose_retry_base, _constructed)[1], RETRY_BASE_NONE)

# --- determinism ------------------------------------------------------------
# Two qualifying substitutes, offered in both orders: the answer may not depend
# on which the capture loop happened to write first.
_two = [make_base(RETRY_BASE_PREFERRED, calls=2), make_base("normal_3"),
        make_base("normal_2")]
check("selection is by sorted fixture_id, not by offer order",
      [field(drive(choose_retry_base, _two)[0], "fixture_id"),
       field(drive(choose_retry_base, list(reversed(_two)))[0], "fixture_id")],
      ["normal_2", "normal_2"])

check("an empty candidate list is answered, not raised",
      drive(choose_retry_base, [])[1], RETRY_BASE_NONE)

# --- the outcome vocabulary is closed and every member is reachable ----------
# Without this, RETRY_BASE_OUTCOMES is a declaration nothing reads -- the shape
# tests/test_package_invariants.py check 2h exists to report.
check("every outcome returned is in the closed vocabulary",
      sorted({drive(choose_retry_base, c)[1]
              for c in (_ok, _split, _ablation_only, [])}
             - set(RETRY_BASE_OUTCOMES)), [])
check("...and all three members are reachable",
      sorted({drive(choose_retry_base, c)[1]
              for c in (_ok, _split, _ablation_only)}),
      sorted(RETRY_BASE_OUTCOMES))

# --- non-degeneracy: the predicate clears a good fixture ---------------------
# Without this, every check above is also satisfied by a predicate that
# disqualifies everything and a selector that always answers NONE.
check("a qualifying fixture is disqualified by nothing",
      drive(retry_base_disqualification, make_base(RETRY_BASE_PREFERRED)), "")

# --- THE LOOP IS CLOSED: what the selector picks, the constructor accepts ----
# The two are separate functions with separate copies of the same preconditions,
# which is the arrangement that lets them drift. This drives the real
# constructor on the real selection and requires a fixture back.
_picked, _, _ = drive(choose_retry_base, _split)
_built = drive(build_constructed_retry_fixture, _picked, "retry_probe")
check("the selected base is one build_constructed_retry_fixture accepts",
      field(_built, "fixture_id"), "retry_probe")
check("...and it records which base it was spliced onto",
      field(_built, "construction", "derived_from"), "normal_3")
check("...with the failing attempt spliced in FRONT",
      len(field(_built, "recordings", "chat_completions"))
      if isinstance(field(_built, "recordings", "chat_completions"), list) else None, 2)

# --- the v8 call_index guard inside the constructor --------------------------
# Added with the v8 verdict projection: a single-call base whose verdicts claim
# call 2 is a base whose copied verdicts do not describe what a replay produces.
_inconsistent = make_base("normal_9", verdict_call_index=2)
check("a single-call base whose verdicts claim call 2 is REFUSED",
      "call_index values [2]" in str(
          drive(build_constructed_retry_fixture, _inconsistent, "retry_probe")),
      True)

print("  [controls]")
check_detects("a substituted base reported as preferred would be caught",
              drive(choose_retry_base, _split)[1], RETRY_BASE_PREFERRED_OK)
check_detects("an ablation base being chosen would be caught",
              field(drive(choose_retry_base, _ablation_only)[0], "fixture_id"),
              "ablation_bm25_only")
check_detects("a predicate that cleared a split base would be caught",
              drive(retry_base_disqualification,
                    make_base(RETRY_BASE_PREFERRED, calls=2)), "")


# ===========================================================================
# CLEANUP
# ===========================================================================

import shutil                                                  # noqa: E402

shutil.rmtree(_TMP, ignore_errors=True)
check("the temp directory this file wrote into is gone",
      os.path.exists(_TMP), False)


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
Created on Tue Aug 11 2026

@author: ramyalsaffar
"""
