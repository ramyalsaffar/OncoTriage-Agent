# Campaign Cohort and Programme Sample Selection Test
####################################################

"""The ruled evaluation programme's three draws, and the gate that stops two
cohorts being merged into one artifact.

WHAT IT COVERS
--------------
``oncotriage/evaluation/cohort.py`` (the draw, the digest, the record), the two
gated fields the cohort added to ``oncotriage/run_fingerprint.py``, the
membership guard in ``oncotriage/batch/runner.py:load_checkpoint``, and the
k=2 re-run ``run_resample`` performs when its caller names the sample.

NO NETWORK, NO KEYS, **NO SPEND**, no live Qdrant, NO MODEL LOAD
(``ONCOTRIAGE_DEFER_LOCAL_MODELS`` is set above the imports and section 10
asserts ``torch`` and ``transformers`` never entered ``sys.modules``), no live
server, no git history, NO CORPUS -- every population in this file is either
fabricated in a temp directory or a list of invented stems, and the one
section that would use the real corpus is GATED and reports a SKIP when it is
absent rather than failing. ``run_fingerprint._resolve_collection`` is replaced
so no index probe is attempted; ``process_patient`` and the graph are
stand-ins and THE GRAPH IS NEVER INVOKED, so no billed call is reachable.

NOT IN THE COLLISION MATRIX -- derived rather than assumed. It writes only
inside a ``tempfile.mkdtemp()`` it removes and asserts gone, ``paths._RESOLVED``
is seeded and restored, and the three repository files it READS
(``oncotriage/evaluation/cohort.py``, ``oncotriage/batch/runner.py``,
``oncotriage/run_fingerprint.py``) are written by neither of the suite's two
writers and are sha256-compared at the end.

IT EXECS NOTHING and loads no module by location, so it needs no
``_EXEC_ALLOWLIST`` entry. Every control is a different INPUT to a pure
function, a real state created on disk, an attribute rebind inside
``try``/``finally`` with the restore asserted, or an ``ast`` walk over a source
file that is parsed and never executed.

WHY THE STRATIFICATION CHECK USES A FABRICATED POPULATION CARRYING THE REAL
CORPUS'S GROUP COUNTS. The property under test -- a simple random draw inherits
the population's group proportions -- is a property of the DRAW, and measuring
it against the live corpus would mean parsing 1,000 bundles and building the
ICD-10-CM registry, which was MEASURED at 170.9 s on the development machine
and needs a corpus no CI runner has. The corpus's real composition was measured
once, is recorded at ``_CORPUS_GROUPS`` with its date, and the population this
file draws from carries exactly those counts -- so the check is against the
real composition without being against the real files.

Run from terminal:
    python tests/test_campaign_cohort_selection.py

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

# No local model is reached here, and the flag is set before the agent is
# imported: a stand-in forgotten in a future edit becomes a named RuntimeError
# instead of a 110 MB download.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import ast
import contextlib
import glob
import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
from collections import Counter

from oncotriage import config as _config
from oncotriage import paths as _paths
from oncotriage import run_fingerprint as _fp
from oncotriage.batch import runner as _runner
from oncotriage.evaluation import cohort as _cohort
from oncotriage.storage import database_logger as _dl


#------------------------------------------------------------------------------


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0, "skipped": 0}
_FAILURES = []
_SKIPS = []


def check(label, actual, expected):
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


def fail(label, detail):
    """Record a failure that is not an equality comparison."""
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}")
    print(f"          {detail}")


def skip(label, reason):
    """Coverage that could NOT be exercised here. NEVER counted as a pass.

    ``tests/test_package_invariants.py``'s mechanism, adopted for the same
    reason: a skip that is silently added to the pass count is a check that
    stopped running and said nothing.
    """
    _RESULTS["skipped"] += 1
    _SKIPS.append(f"{label}: {reason}")
    print(f"  SKIP  {label}  ({reason})")


def guarded(fn, *args, **kwargs):
    """Call into production code, turning ANY raise into a value check() fails on.

    NOT DEFENSIVE PADDING. This suite has shipped the same abort sixteen times:
    a bare call inside a ``check(...)`` argument, where a planted or reverted
    defect raises, the exception escapes while the argument is being evaluated,
    and the run reports ONE TRACEBACK where it owed a summary and N results.
    Sections 4, 8 and 9 deliberately create failing conditions, so every driver
    goes through here.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return {"__raised__": f"{type(exc).__name__}: {exc}"}


def silence(fn, *args, **kwargs):
    """Run fn with BOTH output channels captured; return its value."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        return guarded(fn, *args, **kwargs)


def loud(fn, *args, **kwargs):
    """silence(), but returning (value, captured_text) for output assertions."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        value = guarded(fn, *args, **kwargs)
    return value, buf.getvalue()


def raised(value):
    """Whether ``guarded`` turned a call into a recorded raise."""
    return isinstance(value, dict) and "__raised__" in value


def raise_text(value):
    """The recorded raise's text, or "" for anything that did not raise.

    NOT ``value.get("__raised__", "")``, and the difference is the abort this
    project has now shipped seventeen times -- found here by the revert matrix
    and not by reading. When a planted defect makes a guard STOP REFUSING,
    ``load_checkpoint`` returns the completed SET, ``set.get`` does not exist,
    and the AttributeError escapes while a ``check(...)`` argument is being
    evaluated: the run reported one traceback where it owed a summary and 110
    results. The refusal-text checks are exactly the ones that fire on that
    defect, so they are exactly the ones that must not raise on it.
    """
    return value["__raised__"] if raised(value) else ""


def digest_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


#------------------------------------------------------------------------------


# THE SHIPPED VALUES, CAPTURED AT IMPORT AND BEFORE ANY SECTION MOVES THEM.
# Sections 7 and 9 rebind the cohort constants on TWO modules -- `config` and
# `cohort`, which binds them by value at its own import -- and every restore
# reads this dict rather than re-reading whichever module it is restoring. A
# restore derived from the thing being restored is a restore that agrees with
# whatever it finds.
_CFG_AT_IMPORT = {
    "size":      _config.CAMPAIGN_COHORT_SIZE,
    "seed":      _config.CAMPAIGN_COHORT_SEED,
    "stability": _config.CAMPAIGN_STABILITY_SAMPLE_SIZE,
}

_TMP = tempfile.mkdtemp(prefix="oncotriage-cohort-test-")

_COHORT_SRC = os.path.abspath(_cohort.__file__)
_RUNNER_SRC = os.path.abspath(_runner.__file__)
_FP_SRC = os.path.abspath(_fp.__file__)
_SHA_BEFORE = {p: digest_file(p)
               for p in (_COHORT_SRC, _RUNNER_SRC, _FP_SRC)}


# THE CORPUS'S REAL GROUP COMPOSITION, MEASURED ONCE AND RECORDED HERE.
#
# Produced on 2026-09-01 by parsing all 1,000 bundles under
# `paths.data_fhir_path` with `oncotriage.fhir.parser.parse_fhir_bundle` and
# grouping each with `oncotriage.ablation.study._get_patient_group` -- the same
# function the ablation study's own stratified draw uses. It took 170.9 s.
#
# IT IS A LITERAL AND THAT IS DELIBERATE. Re-deriving it here would make this
# file need the corpus, the ICD-10-CM release and three minutes, which is what
# section 2's own docstring paragraph argues against. What the literal buys is
# that the property below is demonstrated against a REAL composition -- a
# heavily unbalanced one, 40.5% down to 1.6% -- rather than against a uniform
# fiction where every sampler looks correct.
_CORPUS_GROUPS = {
    "colorectal":  405,
    "breast":      290,
    "prostate":    237,
    "hematologic":  52,
    "lung":         16,
}


def _fabricated_population(counts):
    """One stem per patient, labelled by group. Returns ``(stems, group_of)``.

    The stems are NOT sequential inside a group: a draw that took a contiguous
    slice of the input list would inherit the proportions perfectly and would
    not be a random draw at all, so the labels are interleaved and the check
    below would pass for the wrong reason if they were not.
    """
    stems, group_of = [], {}
    index = 0
    remaining = dict(counts)
    while any(remaining.values()):
        for group in counts:
            if remaining.get(group):
                stem = f"Patient{index:04d}_{group[:2]}_uuid{index:04d}"
                stems.append(stem)
                group_of[stem] = group
                remaining[group] -= 1
                index += 1
    return stems, group_of


#------------------------------------------------------------------------------


print("=" * 78)
print("1. THE DRAW IS A PURE, PUBLISHED FUNCTION")
print("=" * 78)
print()

_POP = [f"p{i:03d}" for i in range(100)]

check("1a draw() is deterministic: the same population, size and seed give the "
      "same members",
      _cohort.draw(_POP, 10, 7), _cohort.draw(_POP, 10, 7))

check("1b ...and a DIFFERENT seed gives a different set (non-degeneracy: two "
      "identical answers would satisfy 1a for a function that ignores its "
      "seed)",
      _cohort.draw(_POP, 10, 7) == _cohort.draw(_POP, 10, 8), False)

check("1c the input ORDER does not reach the answer -- the draw is over a SET",
      _cohort.draw(list(reversed(_POP)), 10, 7), _cohort.draw(_POP, 10, 7))

check("1d ...and the answer is returned SORTED BY STEM, not by rank, so the "
      "processing order does not move with the seed",
      _cohort.draw(_POP, 10, 7), sorted(_cohort.draw(_POP, 10, 7)))

check("1e a size at or above the population takes all of it -- which is what "
      "keeps a corpus smaller than the ruled cohort runnable",
      (_cohort.draw(_POP, 100, 7), _cohort.draw(_POP, 500, 7)),
      (sorted(_POP), sorted(_POP)))

check("1f size=None means the whole population",
      _cohort.draw(_POP, None, 7), sorted(_POP))

check("1g size=0 draws nothing (and does not fall through to 'all')",
      _cohort.draw(_POP, 0, 7), [])

_neg = guarded(_cohort.draw, _POP, -1, 7)
check("1h a negative size RAISES rather than silently slicing from the end",
      raised(_neg), True)

# THE RANK KEY, DIRECTLY. A reader recomputing a membership implements exactly
# this, so it is pinned as an arithmetic identity rather than only through
# draw().
_expected_rank = (hashlib.sha256("7|p001".encode("utf-8")).hexdigest(), "p001")
check("1i rank_key is sha256('<seed>|<stem>') with the stem as the tie-break",
      _cohort.rank_key(7, "p001"), _expected_rank)

check("1j ...and draw() really orders on it: the drawn set is exactly the "
      "lowest-ranked k",
      _cohort.draw(_POP, 10, 7),
      sorted(sorted(_POP, key=lambda s: _cohort.rank_key(7, s))[:10]))

check("1k the seed is stringified, so an int and its string spell one draw",
      _cohort.draw(_POP, 10, 7), _cohort.draw(_POP, 10, "7"))

# ── THE DUPLICATE REFUSAL ───────────────────────────────────────────────────
_dup = guarded(_cohort.draw, ["a", "b", "a"], 2, 7)
check("1l a duplicate stem RAISES rather than being de-duplicated: two bundles "
      "sharing a stem are ONE patient to the checkpoint, so the cohort would "
      "be short of the count it reports",
      raised(_dup), True)
check("1m ...and the refusal names the repeated stem",
      "'a'" in raise_text(_dup), True)

# ── THE DIGEST ──────────────────────────────────────────────────────────────
check("1n digest() is a property of the SET, not of the order",
      _cohort.digest(["b", "a"]), _cohort.digest(["a", "b"]))

check("1o ...and it MOVES when one member changes (non-degeneracy: a constant "
      "digest would satisfy 1n)",
      _cohort.digest(["a", "b"]) == _cohort.digest(["a", "c"]), False)

check("1p digest() is DIGEST_CHARS hex characters",
      (len(_cohort.digest(["a"])), _cohort.DIGEST_CHARS,
       set(_cohort.digest(["a"])) <= set("0123456789abcdef")),
      (_cohort.DIGEST_CHARS, 16, True))

check("1q ...and it is the documented construction, not something else",
      _cohort.digest(["a", "b"]),
      hashlib.sha256("a\nb".encode("utf-8")).hexdigest()[:16])

check("1r stem_of strips the directory and the extension, for str and Path "
      "alike",
      (_cohort.stem_of("/x/y/Ann_1_uuid.json"),
       _cohort.stem_of(os.path.join("/x", "y", "Ann_1_uuid.json"))),
      ("Ann_1_uuid", "Ann_1_uuid"))


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("2. THE COHORT DRAW, AGAINST THE CORPUS'S REAL GROUP COMPOSITION")
print("=" * 78)
print()

_STEMS, _GROUP_OF = _fabricated_population(_CORPUS_GROUPS)
_CORPUS_N = sum(_CORPUS_GROUPS.values())

check("2a the fabricated population is the size the corpus was measured at",
      (len(_STEMS), _CORPUS_N), (1000, 1000))

check("2b ...and carries the measured group counts exactly",
      dict(Counter(_GROUP_OF.values())), dict(_CORPUS_GROUPS))

_A = _cohort.draw(_STEMS, _config.CAMPAIGN_COHORT_SIZE,
                  _config.CAMPAIGN_COHORT_SEED)
_B = _cohort.draw(_STEMS, _config.CAMPAIGN_COHORT_SIZE,
                  _config.CAMPAIGN_COHORT_SEED)

check("2c THE 300 DRAWN TWICE FROM THE SEED IS THE SAME MEMBERSHIP",
      _A == _B, True)

check("2d ...and it really is 300, not a truncation or the whole population",
      (len(_A), len(_A) < len(_STEMS)),
      (_config.CAMPAIGN_COHORT_SIZE, True))

check("2e ...and every member came from the population",
      set(_A) <= set(_STEMS), True)

# ── THE PROPERTY THE SIMPLE-RANDOM DECISION RESTS ON ────────────────────────
#
# A simple random draw inherits the population's group proportions IN
# EXPECTATION. The tolerance is stated as an absolute share, and it is CHECKED
# FOR NON-DEGENERACY below: a tolerance wide enough to accept any draw is a
# check that has stopped checking.
_TOLERANCE = 0.05
_drawn = Counter(_GROUP_OF[s] for s in _A)
print(f"  ....  group composition, corpus -> cohort "
      f"(tolerance {_TOLERANCE:.0%} absolute):")
_worst = 0.0
for _g in sorted(_CORPUS_GROUPS, key=lambda k: -_CORPUS_GROUPS[k]):
    _pop_share = _CORPUS_GROUPS[_g] / _CORPUS_N
    _coh_share = _drawn.get(_g, 0) / len(_A)
    _worst = max(_worst, abs(_pop_share - _coh_share))
    print(f"        {_g:<12} {_CORPUS_GROUPS[_g]:>4} ({_pop_share:>5.1%})"
          f"  ->  {_drawn.get(_g, 0):>3} ({_coh_share:>5.1%})")
    check(f"2f the cohort's {_g} share tracks the corpus's",
          abs(_pop_share - _coh_share) <= _TOLERANCE, True)

# NON-DEGENERACY, AND IT POINTS THE OTHER WAY FROM THE OBVIOUS ONE. The
# interesting failure is not "the deviation is large" -- 2h is the control for
# that -- it is "the deviation is ZERO", which is what a PROPORTIONALLY
# STRATIFIED draw produces and which would mean 2f had been passing about a
# sampler this pass deliberately did not build. A simple random draw deviates.
check("2h the deviations are NON-ZERO: a proportional stratified draw would "
      f"match every share exactly, and this one does not (worst "
      f"{_worst:.4f}, tolerance {_TOLERANCE})",
      0.0 < _worst <= _TOLERANCE, True)

# A DRAW THAT IS NOT RANDOM FAILS THE SAME CHECK -- without this, 2f is
# satisfied by any function that happens to spread members out.
_biased = sorted(s for s in _STEMS
                 if _GROUP_OF[s] == "colorectal")[:_config.CAMPAIGN_COHORT_SIZE]
_biased_share = Counter(_GROUP_OF[s] for s in _biased)["colorectal"] / len(_biased)
check("2i CONTROL: a draw that takes one group only FAILS the same tolerance, "
      "so 2f is a real test of the draw rather than of the tolerance",
      abs(_CORPUS_GROUPS["colorectal"] / _CORPUS_N
          - _biased_share) <= _TOLERANCE, False)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("3. THE TWO PROGRAMME SAMPLES ARE INDEPENDENT DRAWS FROM THE COHORT")
print("=" * 78)
print()

_FILES = [os.path.join("/corpus", s + ".json") for s in _STEMS]
_SEL = _cohort.select(_FILES)
_SEL2 = _cohort.select(_FILES)

check("3a select() is deterministic across the whole selection",
      (_SEL.stems == _SEL2.stems,
       _SEL.stability_stems == _SEL2.stability_stems,
       _SEL.judge_stems == _SEL2.judge_stems),
      (True, True, True))

check("3b the cohort is the configured size and seed",
      (_SEL.size, _SEL.requested_size, _SEL.seed),
      (_config.CAMPAIGN_COHORT_SIZE, _config.CAMPAIGN_COHORT_SIZE,
       _config.CAMPAIGN_COHORT_SEED))

check("3c the two samples are the configured sizes and seeds",
      (_SEL.stability_size, _SEL.stability_seed,
       _SEL.judge_size, _SEL.judge_seed),
      (_config.CAMPAIGN_STABILITY_SAMPLE_SIZE, _config.CAMPAIGN_STABILITY_SEED,
       _config.CAMPAIGN_JUDGE_SAMPLE_SIZE, _config.CAMPAIGN_JUDGE_SEED))

check("3d BOTH SAMPLES ARE DRAWN FROM THE COHORT, not from the corpus -- a "
      "stability member the campaign never ran has nothing to be stable about",
      (set(_SEL.stability_stems) <= set(_SEL.stems),
       set(_SEL.judge_stems) <= set(_SEL.stems)),
      (True, True))

check("3e ...and neither is the whole cohort (non-degeneracy: a sample equal "
      "to its population is trivially a subset of it)",
      (len(_SEL.stability_stems) < len(_SEL.stems),
       len(_SEL.judge_stems) < len(_SEL.stems)),
      (True, True))

# ── INDEPENDENCE ────────────────────────────────────────────────────────────
_overlap = set(_SEL.stability_stems) & set(_SEL.judge_stems)
_expected_overlap = (_SEL.stability_size * _SEL.judge_size / _SEL.size)
print(f"  ....  the two samples overlap in {len(_overlap)} patient(s); two "
      f"independent draws of {_SEL.stability_size} and {_SEL.judge_size} from "
      f"{_SEL.size} expect {_expected_overlap:.1f}")

check("3f THE 50 IS NOT A SUBSET OF THE 100 -- which is exactly what a shared "
      "seed would make it, because one rank function over one population makes "
      "the smaller draw a strict prefix of the larger",
      set(_SEL.stability_stems) <= set(_SEL.judge_stems), False)

check("3g ...and the realised overlap is near the hypergeometric expectation "
      "rather than at either extreme (0 would mean the draws were forced "
      "apart, 50 that they were forced together)",
      abs(len(_overlap) - _expected_overlap) <= 12, True)

check("3h ...and it is REPORTED rather than left to be recomputed",
      _SEL.record()["sample_overlap"], len(_overlap))

# CONTROL: the same seed really does nest them.
_nested = _cohort.select(_FILES, stability_seed=91, judge_seed=91)
check("3i CONTROL: given ONE seed, the smaller sample IS a strict subset of "
      "the larger -- which is the failure the import guard exists to prevent",
      set(_nested.stability_stems) <= set(_nested.judge_stems), True)

check("3j ...and the shipped seeds differ, which is what stops that",
      _config.CAMPAIGN_STABILITY_SEED == _config.CAMPAIGN_JUDGE_SEED, False)

# THE IMPORT GUARD, DRIVEN. The module raises at import when the two are equal;
# re-running that statement's condition here is the closest a live process can
# get without re-importing under a patched config.
_guard_src = ast.parse(open(_COHORT_SRC, encoding="utf-8").read())
_guard_raises = [
    n for n in ast.walk(_guard_src)
    if isinstance(n, ast.If)
    and isinstance(n.test, ast.Compare)
    and ast.dump(n.test).count("CAMPAIGN_STABILITY_SEED") == 1
    and ast.dump(n.test).count("CAMPAIGN_JUDGE_SEED") == 1
    and any(isinstance(b, ast.Raise) for b in n.body)]
check("3k the module carries a MODULE-LEVEL guard that raises when the two "
      "sample seeds are equal", len(_guard_raises), 1)
check("3l ...and it is a RuntimeError, not an assert (`python -O` deletes "
      "asserts, and this is a correctness guard)",
      any(isinstance(b, ast.Raise)
          and isinstance(b.exc, ast.Call)
          and getattr(b.exc.func, "id", None) == "RuntimeError"
          for b in _guard_raises[0].body) if _guard_raises else False, True)

# ── RECOMPUTABILITY BY THE DOCUMENTED ALGORITHM ─────────────────────────────
#
# A READER'S OWN IMPLEMENTATION, written out here from DRAW_ALGORITHM and
# nothing else -- deliberately NOT by calling cohort.draw, which would agree
# with itself by construction. This is what the published string has to be
# worth.
def _readers_draw(population, k, seed):
    ranked = sorted(population,
                    key=lambda s: (hashlib.sha256(
                        f"{seed}|{s}".encode("utf-8")).hexdigest(), s))
    return sorted(ranked[:k])


check("3m A READER RECOMPUTES THE COHORT from the seed, the size and the file "
      "list alone",
      _readers_draw(_STEMS, _config.CAMPAIGN_COHORT_SIZE,
                    _config.CAMPAIGN_COHORT_SEED), _SEL.stems)

check("3n ...and both samples, from the cohort it just recomputed",
      (_readers_draw(_SEL.stems, _config.CAMPAIGN_STABILITY_SAMPLE_SIZE,
                     _config.CAMPAIGN_STABILITY_SEED),
       _readers_draw(_SEL.stems, _config.CAMPAIGN_JUDGE_SAMPLE_SIZE,
                     _config.CAMPAIGN_JUDGE_SEED)),
      (_SEL.stability_stems, _SEL.judge_stems))

check("3o ...and the published algorithm string is what the record carries, so "
      "the reader is not reading a docstring that could have gone stale",
      _SEL.record()["algorithm"], _cohort.DRAW_ALGORITHM)

# ── A SHORT CORPUS ──────────────────────────────────────────────────────────
_SHORT = _cohort.select(_FILES[:40])
check("3p a corpus smaller than the ruled cohort selects ALL of it",
      (_SHORT.size, _SHORT.requested_size),
      (40, _config.CAMPAIGN_COHORT_SIZE))
check("3q ...and says so, rather than reporting 40 as though it had been the "
      "plan",
      any("SHORT" in line for line in _SHORT.describe()), True)
check("3r ...and a full-size cohort does NOT print that line (non-degeneracy)",
      any("SHORT" in line for line in _SEL.describe()), False)

# A SATURATED STABILITY SAMPLE DOUBLES THE CAMPAIGN'S SPEND AND SAYS SO. It is
# the ordinary state of a smoke corpus -- a cohort smaller than the configured
# 50 -- and `draw()` taking all of a short population is correct; not saying so
# is not.
_SAT = _cohort.select(_FILES[:20], size=10, stability_size=50)
check("3s a stability sample at or above the cohort's size is ANNOUNCED as "
      "running every patient twice",
      any("WHOLE cohort" in line for line in _SAT.describe()), True)
check("3t ...and a normal cohort does NOT print it (non-degeneracy)",
      any("WHOLE cohort" in line for line in _SEL.describe()), False)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("4. THE RESUME GATE: SEED, SIZE AND MEMBERSHIP")
print("=" * 78)
print()

check("4a the two cohort facts are GATED FIELDS of the shared stamp",
      ("campaign_cohort_size" in _fp.FINGERPRINT_FIELDS,
       "campaign_cohort_seed" in _fp.FINGERPRINT_FIELDS),
      (True, True))

# 4b PINS THE LIVE VERSION AND IT HAS MOVED SINCE THIS CHECK WAS WRITTEN.
# The cohort-selection pass bumped it 3 -> 4 to add `campaign_cohort_size` and
# `campaign_cohort_seed`, and this check recorded that. The reranker-pinning
# pass (commit 1f657ca, "record per-run environment and model identities and
# pin the reranker revision") then bumped it 4 -> 5 to add
# `cross_encoder_revision` -- so the literal 4 stopped being the live value and
# started being a record of when this check was last read. It sat FAILING on a
# developer tree, in a bucket-A file, for the whole of the intervening period.
#
# WHY THE PIN IS KEPT RATHER THAN DERIVED. `_fp.FINGERPRINT_VERSION` compared
# against itself is a tautology; the point of a literal here is that a bump
# forces somebody to come to this line and state which pass moved it and what
# it added, because a bump makes EVERY EXISTING ARTIFACT answer FP_VERSION once
# and that cost belongs in a changelog a reader can find. The cost of the
# literal is exactly what happened above: it goes stale silently unless the
# bumping pass runs this file. That is the trade, and it is the same one
# `RUN_FINGERPRINT_COLUMNS`' round trip in
# tests/test_storage_run_identity.py makes in the other direction.
check("4b ...and the stamp's version was bumped with them. LIVE VALUE IS 5: "
      "3 -> 4 added the two cohort fields (the cohort-selection pass), "
      "4 -> 5 added cross_encoder_revision (the reranker-pinning pass, "
      "1f657ca). A v4 artifact answers FP_VERSION once rather than having a "
      "missing field compared against a live value",
      _fp.FINGERPRINT_VERSION, 5)
# THE TWO COHORT FIELDS ARE STILL GATED, which is what 4b was really about --
# without this, a later bump that DROPPED them would keep 4b green by moving
# the version alone.
check("4b-i ...and both cohort fields are still gated at that version",
      [f for f in ("campaign_cohort_size", "campaign_cohort_seed")
       if f in _fp.FINGERPRINT_FIELDS],
      ["campaign_cohort_size", "campaign_cohort_seed"])

check("4c ...and `summary()` names them, so the banner an operator reads is "
      "not one field short of what the gate compares",
      ("cohort" in _fp.summary({f: "x" for f in _fp.FINGERPRINT_FIELDS})), True)


def _stamp(**overrides):
    """A stamp shaped like current(), built from literals. No Qdrant, no model.

    The KEYS come from the module, so a field gated in a later pass appears here
    automatically rather than making this file's stamp silently short.
    """
    base = {"fingerprint_version": _fp.FINGERPRINT_VERSION}
    for field in _fp.FINGERPRINT_FIELDS:
        base[field] = f"literal-{field}"
    base["campaign_cohort_size"] = _config.CAMPAIGN_COHORT_SIZE
    base["campaign_cohort_seed"] = _config.CAMPAIGN_COHORT_SEED
    base.update(overrides)
    return base


check("4d a stamp differing ONLY in the cohort size is FP_CHANGED, naming the "
      "field",
      _fp.compare(_stamp(campaign_cohort_size=7), _stamp())[0], _fp.FP_CHANGED)
check("4e ...and the detail names it",
      "campaign_cohort_size"
      in _fp.compare(_stamp(campaign_cohort_size=7), _stamp())[1], True)

check("4f a stamp differing ONLY in the cohort seed is FP_CHANGED, naming the "
      "field",
      _fp.compare(_stamp(campaign_cohort_seed=999), _stamp())[0],
      _fp.FP_CHANGED)
check("4g ...and the detail names it",
      "campaign_cohort_seed"
      in _fp.compare(_stamp(campaign_cohort_seed=999), _stamp())[1], True)

check("4h CONTROL: two identical stamps still MATCH, so 4d-4g are about the "
      "cohort rather than about compare() refusing everything",
      _fp.compare(_stamp(), _stamp())[0], _fp.FP_MATCH)

check("4i a v3 stamp -- every artifact written before this pass -- answers "
      "FP_VERSION, which is the shape change and not a configuration change",
      _fp.compare(dict(_stamp(), fingerprint_version=3), _stamp())[0],
      _fp.FP_VERSION)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("5. THE CHECKPOINT CARRIES THE MEMBERSHIP AND REFUSES A DRIFTED CORPUS")
print("=" * 78)
print()

_CKDIR = os.path.join(_TMP, "checkpoint")
os.makedirs(_CKDIR, exist_ok=True)
_saved_resolved = dict(_paths._RESOLVED)
_paths._RESOLVED["checkpoint_path"] = _CKDIR + os.sep

_STAMP = _stamp()
_DIGEST = _SEL.digest

silence(_runner.save_checkpoint, {"a", "b"},
        fingerprint=_STAMP, cohort_digest=_DIGEST)
_stored = json.load(open(_runner._checkpoint_path(), encoding="utf-8"))

check("5a the checkpoint records the drawn membership's digest",
      _stored.get(_runner.CHECKPOINT_COHORT_DIGEST_KEY), _DIGEST)

check("5b ...as a SIBLING of `fingerprint`, not a key inside it -- that object "
      "is `current()` verbatim and its shape is the contract compare() walks",
      _stored["fingerprint"], _STAMP)

check("5c an unchanged cohort resumes",
      silence(_runner.load_checkpoint, fingerprint=_STAMP,
              cohort_digest=_DIGEST), {"a", "b"})

_moved = silence(_runner.load_checkpoint, fingerprint=_STAMP,
                 cohort_digest="0000000000000000")
check("5d A MEMBERSHIP THAT MOVED IS REFUSED -- same seed, same size, a "
      "different 300, which the shared stamp cannot see",
      "ResumeRefusal" in raise_text(_moved), True)

check("5e ...and the refusal says what moved and why the stamp agreed",
      all(word in raise_text(_moved)
          for word in ("campaign_cohort_digest", "MEMBERSHIP", "corpus")), True)

check("5f ...and it is counted, like every other checkpoint refusal",
      _runner.CHECKPOINT_FAULTS[f"refused:{_fp.FP_CHANGED}"] >= 1, True)

check("5g ...and the checkpoint was NOT deleted by the refusal",
      os.path.exists(_runner._checkpoint_path()), True)

_none = silence(_runner.load_checkpoint, fingerprint=_STAMP)
check("5h a caller that selected no cohort skips the membership comparison "
      "entirely -- it has no membership to have changed",
      _none, {"a", "b"})

# A CHECKPOINT WITH NO DIGEST AT ALL, met by a run that HAS one.
_no_key = dict(_stored)
_no_key.pop(_runner.CHECKPOINT_COHORT_DIGEST_KEY)
with open(_runner._checkpoint_path(), "w", encoding="utf-8") as _h:
    json.dump(_no_key, _h)
_absent = silence(_runner.load_checkpoint, fingerprint=_STAMP,
                  cohort_digest=_DIGEST)
check("5i a checkpoint that does not STATE its membership is a disagreement, "
      "never a pass -- ragas_harness.identity_disagreement's rule",
      raised(_absent), True)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("6. MEMBERSHIP DRIFT: ONE PATIENT SWAPPED IN THE CORPUS")
print("=" * 78)
print()

# THE PLANT IS IN THE CORPUS, NOT IN THE CODE. A bundle removed and another
# added is the ordinary way a membership moves at a FIXED seed and size, and it
# is the one case the shared stamp provably cannot see.
_CORPUS_A = os.path.join(_TMP, "corpus_a")
os.makedirs(_CORPUS_A, exist_ok=True)
for _s in _STEMS[:400]:
    with open(os.path.join(_CORPUS_A, _s + ".json"), "w", encoding="utf-8") as _h:
        json.dump({"resourceType": "Bundle", "entry": []}, _h)

_files_a = sorted(glob.glob(os.path.join(_CORPUS_A, "*.json")))
_sel_a = _cohort.select(_files_a)

# ONE PATIENT SWAPPED: remove a member of the corpus, add a new one.
os.remove(os.path.join(_CORPUS_A, _STEMS[0] + ".json"))
with open(os.path.join(_CORPUS_A, "PatientZZZZ_xx_uuidZZZZ.json"), "w",
          encoding="utf-8") as _h:
    json.dump({"resourceType": "Bundle", "entry": []}, _h)

_files_b = sorted(glob.glob(os.path.join(_CORPUS_A, "*.json")))
_sel_b = _cohort.select(_files_b)

check("6a the corpus is the same SIZE after the swap, so nothing counts it as "
      "changed", (len(_files_a), len(_files_b)), (400, 400))

check("6b ...and the cohort's seed and size are unmoved, so the SHARED STAMP "
      "still matches -- which is the whole reason the digest exists",
      _fp.compare(_stamp(), _stamp())[0], _fp.FP_MATCH)

check("6c THE MEMBERSHIP MOVED", _sel_a.stems == _sel_b.stems, False)

check("6d ...and the digest moved with it",
      _sel_a.digest == _sel_b.digest, False)

_drifted = silence(_runner.load_checkpoint, fingerprint=_STAMP,
                   cohort_digest=_sel_b.digest)
# (the checkpoint on disk from section 5 still carries _SEL.digest, restored
#  below before this comparison is trusted)
with open(_runner._checkpoint_path(), "w", encoding="utf-8") as _h:
    json.dump(dict(_stored, **{_runner.CHECKPOINT_COHORT_DIGEST_KEY:
                               _sel_a.digest}), _h)

_clean = silence(_runner.load_checkpoint, fingerprint=_STAMP,
                 cohort_digest=_sel_a.digest)
check("6e CLEAN CONTROL: the UNDRIFTED membership resumes, so 6f is about the "
      "swap rather than about a guard that refuses everything",
      _clean, {"a", "b"})

_caught = silence(_runner.load_checkpoint, fingerprint=_STAMP,
                  cohort_digest=_sel_b.digest)
check("6f THE PLANT IS CAUGHT: a resume under the drifted corpus is refused",
      "ResumeRefusal" in raise_text(_caught), True)

check("6g ...and the refusal names both digests, so an operator can see which "
      "way it moved",
      _sel_a.digest in raise_text(_caught)
      and _sel_b.digest in raise_text(_caught), True)

# HOW MANY PATIENTS ACTUALLY MOVED. Reported rather than asserted at a number:
# removing one member of the population re-ranks nothing (the rank is per stem)
# but shifts the k-boundary, so the drawn set changes by a small amount that is
# a property of the corpus rather than of the code.
_moved_in = set(_sel_b.stems) - set(_sel_a.stems)
_moved_out = set(_sel_a.stems) - set(_sel_b.stems)
print(f"  ....  the swap moved {len(_moved_out)} patient(s) out of the cohort "
      f"and {len(_moved_in)} in")
check("6h ...and it is a SMALL move, not a reshuffle -- which is what makes it "
      "the dangerous case: a resume would look almost right",
      len(_moved_out) <= 5 and len(_moved_out) >= 1, True)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("7. THE RUNNER PROCESSES THE COHORT AND NOTHING ELSE")
print("=" * 78)
print()

# THE REAL main(), WITH FIVE STAND-INS. Everything the cohort touches is the
# shipped code: the real cohort.select, the real load_checkpoint, the real
# save_checkpoint, the real run_batch, the real _on_done, the real
# start_run_record. What is replaced is what needs a network, a model or money.
_RUN_DB = os.path.join(_TMP, "run.db")
_RUN_CP = os.path.join(_TMP, "run_cp")
_RUN_CORPUS = os.path.join(_TMP, "run_corpus")
os.makedirs(_RUN_CP, exist_ok=True)
os.makedirs(_RUN_CORPUS, exist_ok=True)
_RUN_STEMS = [f"Run{i:03d}_x_uuid{i:03d}" for i in range(40)]
for _s in _RUN_STEMS:
    with open(os.path.join(_RUN_CORPUS, _s + ".json"), "w", encoding="utf-8") as _h:
        json.dump({"resourceType": "Bundle", "entry": []}, _h)

_SMALL_COHORT = 12
_SMALL_STABILITY = 5


class _TrackingStandIn:
    def __init__(self):
        self.calls = []

    def start_run(self, **kwargs):
        self.calls.append(("start_run", kwargs))

    def log_run_metrics(self, *a, **k):
        self.calls.append(("log_run_metrics", None))

    def end_run(self, **kwargs):
        self.calls.append(("end_run", kwargs.get("status")))


_PATCHED = ("build_bm25_index_from_qdrant", "build_matching_graph", "tracking",
            "process_patient")
_SEEN = []


def _recording_patient(fhir_path=None, graph=None, is_resample=False,
                       run_id=None, db_path=None):
    """Records which patient was asked for. Issues no request of any kind."""
    _SEEN.append((_cohort.stem_of(fhir_path), bool(is_resample)))
    return {"patient_id": _cohort.stem_of(fhir_path), "status": "success",
            "eligible_matches": 1, "near_misses": 0, "not_evaluable": 0,
            "total_time": 0.01, "timestamp": "2026-09-01T00:00:00",
            "error": "", "is_resample": is_resample}


def _drive_main(cohort_size, stability_size, corpus=None):
    """Run the REAL main() against the scratch tree. Returns (text, exc, tracking)."""
    saved = {n: getattr(_runner, n) for n in _PATCHED}
    saved_resolved = dict(_paths._RESOLVED)
    saved_sizes = (_config.CAMPAIGN_COHORT_SIZE,
                   _config.CAMPAIGN_STABILITY_SAMPLE_SIZE)
    saved_resolve = _fp._resolve_collection
    tracking = _TrackingStandIn()
    buf = io.StringIO()
    try:
        _config.CAMPAIGN_COHORT_SIZE = cohort_size
        _config.CAMPAIGN_STABILITY_SAMPLE_SIZE = stability_size
        # THE SIZES ARE SET ON `config` AND THE COHORT MODULE FOLLOWS, because
        # it reads them through `from ... import`. It does NOT: that binds at
        # import. So the module's own names are set too, and BOTH are restored;
        # 7a asserts the drive really saw the small cohort, which is what makes
        # this rebinding a measurement rather than an assumption.
        _cohort.CAMPAIGN_COHORT_SIZE = cohort_size
        _cohort.CAMPAIGN_STABILITY_SAMPLE_SIZE = stability_size
        _paths._RESOLVED["data_fhir_path"] = (corpus or _RUN_CORPUS) + os.sep
        _paths._RESOLVED["inferences_path"] = _RUN_DB
        _paths._RESOLVED["checkpoint_path"] = _RUN_CP + os.sep
        _fp._resolve_collection = lambda: ("trial_criteria_test", 12345)
        _fp.clear_cache()
        _runner.build_bm25_index_from_qdrant = lambda *a, **k: (object(), ["NCT1"])
        _runner.build_matching_graph = lambda *a, **k: object()
        _runner.tracking = tracking
        _runner.process_patient = _recording_patient
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                results = _runner.main()
                return buf.getvalue(), None, tracking, results
            except BaseException as exc:               # noqa: BLE001 -- returned
                return buf.getvalue(), exc, tracking, []
    finally:
        for name, value in saved.items():
            setattr(_runner, name, value)
        (_config.CAMPAIGN_COHORT_SIZE,
         _config.CAMPAIGN_STABILITY_SAMPLE_SIZE) = saved_sizes
        _cohort.CAMPAIGN_COHORT_SIZE = saved_sizes[0]
        _cohort.CAMPAIGN_STABILITY_SAMPLE_SIZE = saved_sizes[1]
        _fp._resolve_collection = saved_resolve
        _fp.clear_cache()
        _paths._RESOLVED.clear()
        _paths._RESOLVED.update(saved_resolved)
        _dl._INITIALIZED_DATABASES.discard(os.path.abspath(_RUN_DB))


_SEEN.clear()
_text, _exc, _tracking, _RESULTS_LIST = _drive_main(
    _SMALL_COHORT, _SMALL_STABILITY)

_expected_cohort = _cohort.draw(_RUN_STEMS, _SMALL_COHORT,
                                _config.CAMPAIGN_COHORT_SEED)
_main_seen = sorted(s for s, is_r in _SEEN if not is_r)

check("7z main() completed without raising",
      None if _exc is None else f"{type(_exc).__name__}: {_exc}", None)

check("7a EXACTLY THE SELECTED PATIENTS WERE PROCESSED -- not the corpus",
      _main_seen, _expected_cohort)

check("7b ...and that really is a SUBSET of the corpus (non-degeneracy: with "
      "the cohort equal to the corpus this check passes for free)",
      (len(_main_seen), len(_RUN_STEMS)), (_SMALL_COHORT, 40))

check("7c the cohort block was announced on the console",
      "[Cohort]" in _text, True)

check("7d ...naming the digest a reader would recompute",
      _cohort.digest(_expected_cohort) in _text, True)

# ── THE RUN ROW ─────────────────────────────────────────────────────────────
_conn = sqlite3.connect(_RUN_DB)
_conn.row_factory = sqlite3.Row
_row = _conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
_conn.close()

check("7e the run row carries the cohort's seed and configured size (as stamp "
      "columns, so a resume gates on them)",
      (_row["campaign_cohort_seed"], _row["campaign_cohort_size"]),
      (_config.CAMPAIGN_COHORT_SEED, _SMALL_COHORT))

check("7f ...the count actually SELECTED",
      _row["cohort_size"], _SMALL_COHORT)

check("7g ...the membership digest",
      _row["cohort_digest"], _cohort.digest(_expected_cohort))

check("7h ...and both sample seeds and sizes",
      (_row["stability_sample_seed"], _row["stability_sample_size"],
       _row["judge_sample_seed"], _row["judge_sample_size"]),
      (_config.CAMPAIGN_STABILITY_SEED, _SMALL_STABILITY,
       _config.CAMPAIGN_JUDGE_SEED,
       min(_config.CAMPAIGN_JUDGE_SAMPLE_SIZE, _SMALL_COHORT)))

_start = [k for n, k in _tracking.calls if n == "start_run"][0]
check("7i the tracking index logs the COHORT's size, not the corpus's -- a "
      "value the run did not use is a false record",
      _start["params"]["patient_count"], _SMALL_COHORT)

check("7j ...and the re-run pass's real count and seed, not RESAMPLE_COUNT",
      (_start["params"]["resample_count"], _start["params"]["resample_seed"]),
      (_SMALL_STABILITY, _config.CAMPAIGN_STABILITY_SEED))


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("8. THE k=2 STABILITY RE-RUN")
print("=" * 78)
print()

_expected_stability = _cohort.draw(_expected_cohort, _SMALL_STABILITY,
                                   _config.CAMPAIGN_STABILITY_SEED)
_resampled = sorted(s for s, is_r in _SEEN if is_r)

check("8a THE SAME NAMED SAMPLE WAS RE-RUN -- the programme's stability draw, "
      "not this pass's own random one",
      _resampled, _expected_stability)

check("8b ...and it is drawn from the COHORT, so every member has a first "
      "observation to pair with",
      set(_expected_stability) <= set(_expected_cohort), True)

_pairs = Counter(s for s, _ in _SEEN)
check("8c EVERY STABILITY MEMBER HAS TWO OBSERVATIONS AND NOBODY ELSE DOES",
      (sorted(s for s, n in _pairs.items() if n == 2),
       sorted(s for s, n in _pairs.items() if n == 1)),
      (_expected_stability,
       sorted(set(_expected_cohort) - set(_expected_stability))))

# STORED DISTINGUISHABLY, AT THE LAYER THIS DRIVE REACHES. `process_patient` is
# a stand-in here -- it must be, it is one live billed Stage 5 call per patient
# -- so no `inferences` row is written and the DB cannot be asked. What IS the
# shipped record of the two observations is the results list `main()` returns
# and `batch_runner_results.json` holds, where the re-run entry carries
# `is_resample`. That flag is written by the REAL `_on_done` and the REAL
# `append_result`.
_res_pairs = Counter(r["patient_id"] for r in _RESULTS_LIST)
check("8d ...and the second observation is RECORDED, one entry per run",
      sorted(p for p, n in _res_pairs.items() if n == 2), _expected_stability)

check("8e ...DISTINGUISHABLE from the first: exactly one of each pair carries "
      "is_resample",
      sorted(r["patient_id"] for r in _RESULTS_LIST if r.get("is_resample")),
      _expected_stability)

# AND AT THE DATABASE LAYER, WHICH THIS DRIVE CANNOT REACH, THE DISTINCTION IS
# `MIN(id)` PER PATIENT -- which is what "each judged patient's FIRST-run
# verdicts" resolves to, and what oncotriage/evaluation/sampling.py already
# uses. Demonstrated against the real schema rather than asserted, because a
# claim about a query nobody ran is not a claim.
_FIRST_DB = os.path.join(_TMP, "first_run.db")
silence(_dl.initialize_database, _FIRST_DB)
_conn = sqlite3.connect(_FIRST_DB)
_conn.execute("INSERT INTO inferences (patient_id, timestamp) VALUES (?, ?)",
              ("pat-1", "2026-09-01T00:00:00"))
_conn.execute("INSERT INTO inferences (patient_id, timestamp) VALUES (?, ?)",
              ("pat-1", "2026-09-01T01:00:00"))
_conn.commit()
_first = _conn.execute(
    "SELECT id, timestamp FROM inferences WHERE patient_id='pat-1' "
    "ORDER BY id").fetchall()
_min = _conn.execute(
    "SELECT MIN(id) FROM inferences WHERE patient_id='pat-1'").fetchone()[0]
_conn.close()
check("8f two observations of one patient are two rows, and MIN(id) is the "
      "FIRST -- the query the judge pass's 'first-run verdicts' resolves to",
      (len(_first), _min, _min == _first[0][0]), (2, _first[0][0], True))

check("8g the run row's stability_sample_size is what was actually re-run",
      _row["stability_sample_size"], len(_expected_stability))

# ── A REQUESTED MEMBER THAT DID NOT COMPLETE IS NOT REPLACED ────────────────
_RS_TEXT = []


def _out(line=""):
    _RS_TEXT.append(str(line))


_missing_files = [os.path.join(_RUN_CORPUS, s + ".json")
                  for s in _expected_cohort]
_completed = set(_expected_cohort) - {_expected_stability[0]}
_SEEN.clear()
_saved_pp = _runner.process_patient
_saved_start = _runner._start_patient_unless_stopped
try:
    _runner.process_patient = _recording_patient
    silence(_runner.run_resample,
            fhir_files=_missing_files, completed_ids=_completed,
            bm25_index=object(), nct_ids=["NCT1"], graph=object(),
            results_list=[], run_id=None, db_path=_RUN_DB,
            resample_stems=_expected_stability)
finally:
    _runner.process_patient = _saved_pp
    _runner._start_patient_unless_stopped = _saved_start

_re_seen = sorted(s for s, _ in _SEEN)
check("8h a requested member that did not complete the main pass is DROPPED",
      _re_seen, sorted(_expected_stability[1:]))

check("8i ...and is NOT replaced by a patient that happened to succeed, which "
      "would make 'the 50' a set nobody can recompute",
      len(_re_seen), len(_expected_stability) - 1)

_, _rs_text = loud(
    _runner.run_resample,
    fhir_files=_missing_files, completed_ids=_completed,
    bm25_index=object(), nct_ids=["NCT1"], graph=object(),
    results_list=[], run_id=None, db_path=_RUN_DB,
    resample_stems=_expected_stability)
check("8j ...and the shortfall is SAID, so the campaign's own log states how "
      "many members reached two observations",
      "k=1" in _rs_text and "did not complete" in _rs_text, True)

# THE FALLBACK IS STILL THERE, AND IT STILL READS RESAMPLE_COUNT. Two facts,
# by AST over the shipped function: a `resample_stems is None` branch, and a
# load of RESAMPLE_COUNT inside it. Without the second, "not silently
# repurposed" would be satisfied by a branch that had stopped using the
# constant -- which is exactly the dead-tunable shape this project deletes.
_rs_fn = [n for n in ast.walk(ast.parse(open(_RUNNER_SRC, encoding="utf-8").read()))
          if isinstance(n, ast.FunctionDef) and n.name == "run_resample"]
check("8k the shipped run_resample was found (non-degeneracy: an empty walk "
      "would make 8l and 8m pass for free)", len(_rs_fn), 1)
_rs_names = {n.id for n in ast.walk(_rs_fn[0]) if isinstance(n, ast.Name)}
check("8l ...it takes `resample_stems`, defaulting to None",
      ("resample_stems" in [a.arg for a in _rs_fn[0].args.args],
       _rs_fn[0].args.defaults[-1].value if _rs_fn[0].args.defaults else "?"),
      (True, None))
check("8m ...and the fallback still reads RESAMPLE_COUNT and RESAMPLE_SEED, so "
      "the constants are not silently repurposed",
      ({"RESAMPLE_COUNT", "RESAMPLE_SEED"} <= _rs_names), True)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("9. A RESUME UNDER A CHANGED COHORT IS REFUSED BY THE REAL RUNNER")
print("=" * 78)
print()

# The checkpoint on disk was written by section 7's completed run... which
# CLEARED it, because every patient succeeded. Re-create one for the same
# cohort so the refusal is about the change and not about an absent file.
_paths._RESOLVED["checkpoint_path"] = _RUN_CP + os.sep
_saved_resolve = _fp._resolve_collection
try:
    _fp._resolve_collection = lambda: ("trial_criteria_test", 12345)
    _fp.clear_cache()
    _cohort.CAMPAIGN_COHORT_SIZE = _SMALL_COHORT
    _config.CAMPAIGN_COHORT_SIZE = _SMALL_COHORT
    _live = _fp.current()
    silence(_runner.save_checkpoint, set(_expected_cohort[:3]),
            fingerprint=_live, cohort_digest=_cohort.digest(_expected_cohort))

    _ok = silence(_runner.load_checkpoint, fingerprint=_live,
                  cohort_digest=_cohort.digest(_expected_cohort))
    check("9a CLEAN CONTROL: the unchanged cohort resumes",
          _ok, set(_expected_cohort[:3]))

    # A CHANGED SIZE. The stamp moves, so this is the FINGERPRINT message.
    _config.CAMPAIGN_COHORT_SIZE = _SMALL_COHORT + 1
    _cohort.CAMPAIGN_COHORT_SIZE = _SMALL_COHORT + 1
    _fp.clear_cache()
    _bigger = _fp.current()
    _r = silence(_runner.load_checkpoint, fingerprint=_bigger,
                 cohort_digest=_cohort.digest(
                     _cohort.draw(_RUN_STEMS, _SMALL_COHORT + 1,
                                  _config.CAMPAIGN_COHORT_SEED)))
    check("9b A RESUME UNDER A CHANGED COHORT SIZE IS REFUSED",
          "ResumeRefusal" in raise_text(_r), True)
    check("9c ...with the FINGERPRINT message, naming the field and both values",
          all(w in raise_text(_r)
              for w in ("REFUSED", "campaign_cohort_size",
                        str(_SMALL_COHORT + 1))), True)
    check("9d ...and it names the caller's own remediation",
          "--fresh" in raise_text(_r), True)

    # A CHANGED SEED.
    _config.CAMPAIGN_COHORT_SIZE = _SMALL_COHORT
    _cohort.CAMPAIGN_COHORT_SIZE = _SMALL_COHORT
    _saved_seed = _config.CAMPAIGN_COHORT_SEED
    _config.CAMPAIGN_COHORT_SEED = _saved_seed + 1
    _cohort.CAMPAIGN_COHORT_SEED = _saved_seed + 1
    _fp.clear_cache()
    _reseeded = _fp.current()
    _r2 = silence(_runner.load_checkpoint, fingerprint=_reseeded,
                  cohort_digest=_cohort.digest(
                      _cohort.draw(_RUN_STEMS, _SMALL_COHORT, _saved_seed + 1)))
    check("9e A RESUME UNDER A CHANGED COHORT SEED IS REFUSED",
          "ResumeRefusal" in raise_text(_r2), True)
    check("9f ...with the FINGERPRINT message, naming the field",
          "campaign_cohort_seed" in raise_text(_r2), True)
    _config.CAMPAIGN_COHORT_SEED = _saved_seed
    _cohort.CAMPAIGN_COHORT_SEED = _saved_seed
finally:
    # BOTH MODULES, AND THE `_cohort` HALF IS THE ONE THAT MATTERS. That module
    # binds the four constants with `from oncotriage.config import ...`, which
    # copies the VALUE at import -- so setting `config.X` alone reaches nothing
    # and restoring `config.X` alone leaves the cohort module holding the test's
    # value for every check below. The first version of this file did exactly
    # that, and section 10's live-corpus check then passed while drawing a
    # 12-patient cohort: it compared the module's leaked 12 against the config's
    # leaked 12 and agreed with itself. 10d is the probe that would have caught
    # it and is why it is written as a literal.
    _fp._resolve_collection = _saved_resolve
    _fp.clear_cache()
    _config.CAMPAIGN_COHORT_SIZE = _CFG_AT_IMPORT["size"]
    _config.CAMPAIGN_COHORT_SEED = _CFG_AT_IMPORT["seed"]
    _cohort.CAMPAIGN_COHORT_SIZE = _CFG_AT_IMPORT["size"]
    _cohort.CAMPAIGN_COHORT_SEED = _CFG_AT_IMPORT["seed"]
    _cohort.CAMPAIGN_STABILITY_SAMPLE_SIZE = _CFG_AT_IMPORT["stability"]


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("10. HYGIENE")
print("=" * 78)
print()

check("10a no model was loaded: torch and transformers never entered "
      "sys.modules",
      [m for m in ("torch", "transformers") if m in sys.modules], [])

# EVERY REBOUND CONSTANT IS BACK, ON BOTH MODULES, AND THE EXPECTATION IS THE
# RULED VALUE WRITTEN OUT rather than read off whichever module is being
# checked. See section 9's `finally`: the first version of this file leaked a
# 12-patient cohort into `_cohort` and section 10 agreed with the leak.
check("10d the ruled programme's constants are what config declares",
      (_config.CAMPAIGN_COHORT_SIZE, _config.CAMPAIGN_COHORT_SEED,
       _config.CAMPAIGN_STABILITY_SAMPLE_SIZE, _config.CAMPAIGN_STABILITY_SEED,
       _config.CAMPAIGN_JUDGE_SAMPLE_SIZE, _config.CAMPAIGN_JUDGE_SEED),
      (500, 42, 50, 43, 100, 44))
check("10e ...and the cohort module's own bindings agree with them, so nothing "
      "this file rebound is still installed",
      (_cohort.CAMPAIGN_COHORT_SIZE, _cohort.CAMPAIGN_COHORT_SEED,
       _cohort.CAMPAIGN_STABILITY_SAMPLE_SIZE),
      (_config.CAMPAIGN_COHORT_SIZE, _config.CAMPAIGN_COHORT_SEED,
       _config.CAMPAIGN_STABILITY_SAMPLE_SIZE))

# THE REAL CORPUS, IF THERE IS ONE. Cheap: a glob and a hash, no parse.
_paths._RESOLVED.clear()
_paths._RESOLVED.update(_saved_resolved)
try:
    _real_dir = _paths.data_fhir_path
    _real = sorted(glob.glob(_real_dir + "*.json"))
except Exception:                                      # noqa: BLE001 -- reported
    _real = []
if _real:
    _r1 = _cohort.select(_real)
    _r2 = _cohort.select(_real)
    check("10f the LIVE corpus draws deterministically",
          (_r1.stems, _r1.digest), (_r2.stems, _r2.digest))
    check("10g ...and the shipped configuration selects the ruled cohort from "
          "it (or all of it, if the corpus is short)",
          _r1.size, min(_config.CAMPAIGN_COHORT_SIZE, len(_real)))
    print(f"  ....  live corpus: {len(_real)} bundles, cohort {_r1.size} "
          f"(digest {_r1.digest}), stability {_r1.stability_size}, judge "
          f"{_r1.judge_size}, overlap {_r1.record()['sample_overlap']}")
else:
    skip("10f-10g the live-corpus draw",
         "no FHIR corpus is resolvable here; the draw is exercised against "
         "fabricated populations in sections 1-3")

for _p, _was in _SHA_BEFORE.items():
    check(f"10h {os.path.basename(_p)} is byte-identical",
          digest_file(_p), _was)
check("10i ...and the three hashes are not all the same file (non-degeneracy)",
      len(set(_SHA_BEFORE.values())), 3)

check("10j every path this file wrote is inside its own temp directory",
      all(str(p).startswith(_TMP)
          for p in (_RUN_DB, _RUN_CP, _RUN_CORPUS, _CKDIR, _CORPUS_A)), True)

shutil.rmtree(_TMP, ignore_errors=True)
check("10k the scratch directory was removed", os.path.exists(_TMP), False)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"  passed:  {_RESULTS['passed']}")
print(f"  failed:  {_RESULTS['failed']}")
print(f"  skipped: {_RESULTS['skipped']}   (a skip is NOT a pass)")
if _FAILURES:
    print()
    print("FAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")
if _SKIPS:
    print()
    print("SKIPPED:")
    for _s in _SKIPS:
        print(f"  - {_s}")
print("=" * 78)

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep  1 2026

@author: ramyalsaffar
"""
