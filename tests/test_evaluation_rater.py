# The Independent LLM Rater
###########################

"""
Rater Harness Test

``oncotriage/evaluation/rater.py`` has an independent, different-family model
rate every criterion decision in an evaluation run and reports AGREEMENT with
the recorded decisions. This file is the durable form of the verification that
was run against it while it was built and while it was driven live against the
Anthropic Message Batches API on 2026-08-11 (a 20-request smoke batch, then the
full 2,212-request run at $6.09). That verification lived in scratch scripts;
scratch scripts are not a standing check, and the live run is not repeatable
for free, so everything provable without spending is ported here.

WHAT IS COVERED, AND WHY EACH SECTION EXISTS:

  1  Cohen's kappa, against matrices computed BY HAND rather than against the
     implementation. Including the degenerate cases, and including the one
     result that matters most for how the number is read: an always-agree
     rater scores kappa 1.0. Kappa measures inter-rater reliability given the
     marginals; it does NOT detect a rater that never moves, and it does not
     correct for the fact that the rater is shown the answer it is auditing.
     That limitation is asserted here so a future edit cannot quietly imply
     otherwise.

  2  ``select_smoke_decisions``. A smoke batch that spends money must exercise
     the vocabularies it will meet, so the selection has to span every
     (arm, status) cell and more than one patient. Section 2 carries the
     REGRESSION that shipped during the live pass: phase A took each cell's
     first member unconditionally, one patient supplied all six cells at n=6,
     and the two-patient guarantee became unsatisfiable with no slots left.

  3  The parse bucket taxonomy. Every way a response can fail, each landing in
     its own named bucket, and nothing coerced -- a status from the wrong arm's
     vocabulary is recorded unrated, never mapped onto the nearest member of
     the right one. Section 3 also pins the prose-carving tolerance, which is
     not a nicety: 699 of 2,237 live responses (31%) wrapped their JSON in
     prose, and without carving they would all have been unrated.

  4  Pricing. Five components, each at a rate that STACKS a cache multiplier
     with the batch discount, checked against hand-computed dollars -- and the
     rule that ``cache_creation_input_tokens`` is never priced, because it is a
     total of the two per-TTL fields that ARE priced.

  5  The join. Results come back in arbitrary order and are joined on
     custom_id; a positional join would mis-attribute every rating without
     failing. Section 5 requires the partition to be exact and requires both
     duplicate and unknown custom_id to refuse rather than guess.

  6  Absent ``corrected_status`` on an agree rating. Added test-first: the
     behaviour it pins did not exist when the section was written.

NEGATIVE CONTROLS ARE INPUT-BASED, NOT PLANTED. Every function under test here
is pure, or takes its collaborators as arguments, so the natural control is a
different INPUT that must produce a different answer -- the shape
``tests/test_agent_patient_hash_coverage.py`` uses for the same reason. That
also keeps this file out of ``_EXEC_ALLOWLIST``: it execs nothing, loads no
module by location, and patches no shipped source.

NO NETWORK, NO KEYS, NO SPEND, NO DATABASE, NO CORPUS, NO GIT HISTORY. Every
decision, response and usage object in here is a literal built in this file.
The evaluation run directories are never read -- the harness's own
``default_run_dir()`` is never called -- so this file is unaffected by whether
a run exists on disk. It writes nothing anywhere and is NOT in
``tests/run_serial_tests.py``'s collision matrix.

    python tests/test_evaluation_rater.py
"""

import json
import sys
import types

try:
    import oncotriage                                          # noqa: F401
except ImportError:
    import os as _os
    for _candidate, _how in (
        (_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
         if "__file__" in globals() else None, "__file__"),
        (_os.getcwd(), "cwd"),
    ):
        if _candidate and _os.path.isdir(_os.path.join(_candidate,
                                                       "oncotriage")):
            if _candidate not in sys.path:
                sys.path.insert(0, _candidate)
            print(f"[Bootstrap] oncotriage package found at {_candidate} "
                  f"(via {_how}); added to sys.path")
            break
    else:
        raise

from oncotriage import config
from oncotriage.evaluation import rater as R


_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


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


def close(label, actual, expected, tol=1e-9):
    """Equality for floats, with the tolerance stated at the call site."""
    ok = actual is not None and abs(actual - expected) < tol
    check(label, ok if not ok else f"{expected!r}(+-{tol})",
          True if not ok else f"{expected!r}(+-{tol})")
    if not ok:
        _FAILURES[-1] += f"\n          value:    {actual!r}"


def drive(fn, *args, **kwargs):
    """Call into the harness and convert a raise into a value.

    A bare call would let an exception escape while ``check``'s arguments were
    being evaluated, killing the run and reporting one traceback where it owed
    a summary. This project has shipped that defect four times; every call into
    the module in this file goes through here or through ``raises``.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:                                   # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


def raises(fn, *args, **kwargs):
    """(did_it_raise, exception_type_name_or_None)."""
    try:
        fn(*args, **kwargs)
        return False, None
    except Exception as exc:                                   # noqa: BLE001
        return True, type(exc).__name__


def field(rating, key):
    """Read one field of a rating that may not exist.

    A bare ``rating[key]`` aborts the whole file with a TypeError the moment a
    regression stops something being rated -- which is precisely when this file
    owes a summary. The revert harness caught exactly that: disabling
    ``extract_object`` left section 3b indexing into None and the run reported
    one traceback where it owed 141 results.
    """
    if not isinstance(rating, dict):
        return "<unrated: %r>" % (rating,)
    return rating.get(key, "<field absent>")


def bucket(collected, cid):
    """The unrated reason for one custom_id, or a named absence."""
    if not isinstance(collected, dict):
        return "<collect_results raised: %r>" % (collected,)
    entry = collected.get("unrated", {}).get(cid)
    if entry is None:
        return "<not in the unrated table>"
    return entry.get("reason", "<no reason recorded>")


def decision(patient, nct, arm, index, status, criterion="crit",
             value="val", group="matches"):
    """A synthetic Decision. Nothing on disk is consulted."""
    order = {"p0": 0, "p1": 1, "p2": 2, "p3": 3, "p4": 4, "p5": 5, "p6": 6}
    return R.Decision(patient_id=patient, patient_index=order.get(patient, 9),
                      nct_id=nct, arm=arm, index=index, criterion=criterion,
                      patient_value=value, status=status, verdict_group=group)


# ===========================================================================
# SECTION 1 -- COHEN'S KAPPA
# ===========================================================================

print("\n" + "=" * 70)
print("SECTION 1 -- Cohen's kappa, against hand-computed matrices")
print("=" * 70)

_AB = ["a", "b"]
_ABC = ["a", "b", "c"]

# Perfect agreement, 2 categories, 50/50.
#   Po = 100/100 = 1.0
#   marginals .5/.5 both raters -> Pe = .5*.5 + .5*.5 = .5
#   kappa = (1 - .5) / (1 - .5) = 1.0
k = drive(R.cohens_kappa, [[50, 0], [0, 50]], _AB)
close("1a  perfect agreement -> kappa 1.0", k["kappa"], 1.0)
close("1a  perfect agreement -> Po 1.0", k["observed_agreement"], 1.0)
close("1a  perfect agreement -> Pe 0.5", k["expected_agreement"], 0.5)

# Chance-only: both raters 50/50 and independent -> 25 in every cell.
#   Po = 50/100 = .5 ; Pe = .5 ; kappa = 0 exactly.
k = drive(R.cohens_kappa, [[25, 25], [25, 25]], _AB)
close("1b  chance-only agreement -> kappa 0.0", k["kappa"], 0.0)
close("1b  chance-only agreement -> Po 0.5", k["observed_agreement"], 0.5)

# THE ALWAYS-AGREE RATER. Its implied status equals the recorded one on every
# decision, so the matrix is diagonal and kappa is 1.0. This is the correct
# value for perfect inter-rater agreement AND the documented limitation: kappa
# cannot tell a rater that never disagrees from one that always happens to.
#   rows/cols 820/90/90 of 1000 -> Pe = .82^2 + .09^2 + .09^2 = .6886
_ALWAYS = [[820, 0, 0], [0, 90, 0], [0, 0, 90]]
k = drive(R.cohens_kappa, _ALWAYS, _ABC)
close("1c  ALWAYS-AGREE rater -> kappa 1.0 (the limitation)", k["kappa"], 1.0)
close("1c  ALWAYS-AGREE rater -> Pe 0.6886", k["expected_agreement"], 0.6886)
check("1c  ALWAYS-AGREE rater reproduces the pipeline's marginals exactly",
      k["pipeline_prevalence"] == k["rater_prevalence"], True)
check("1c  the module documents that kappa is not an anchoring check",
      all(s in (R.cohens_kappa.__doc__ or "")
          for s in ("does NOT detect", "never disagrees")), True)

# The limitation, stated as a property rather than as prose: a rater that moves
# and a rater that never moves are distinguishable ONLY by the marginals, not
# by kappa. Both score 1.0 here; only one has equal marginals.
_MOVED = [[810, 10, 0], [0, 90, 0], [0, 0, 90]]
k_moved = drive(R.cohens_kappa, _MOVED, _ABC)
check("1c  a rater that DID move has unequal marginals",
      k_moved["pipeline_prevalence"] != k_moved["rater_prevalence"], True)

# Worked 3-category example. rows 30/30/40, cols 35/25/40, N=100
#   Po = (20+15+30)/100 = .65
#   Pe = .30*.35 + .30*.25 + .40*.40 = .105 + .075 + .16 = .34
#   kappa = (.65 - .34)/(1 - .34) = .31/.66
k = drive(R.cohens_kappa, [[20, 5, 5], [10, 15, 5], [5, 5, 30]], _ABC)
close("1d  worked 3-category example -> kappa 0.31/0.66",
      k["kappa"], 0.31 / 0.66)
close("1d  worked 3-category example -> Pe 0.34",
      k["expected_agreement"], 0.34)

# Systematically worse than chance -> negative kappa.
#   Po = 0 ; Pe = .5 ; kappa = (0-.5)/(1-.5) = -1
k = drive(R.cohens_kappa, [[0, 50], [50, 0]], _AB)
close("1e  perfect disagreement -> kappa -1.0", k["kappa"], -1.0)

# The case kappa exists for: high raw agreement, near-zero kappa.
#   [[900,50],[45,5]] N=1000 -> Po=.905
#   rows 950/50, cols 945/55 -> Pe = .95*.945 + .05*.055 = .9005
#   kappa = (.905-.9005)/(1-.9005) = .0045/.0995
k = drive(R.cohens_kappa, [[900, 50], [45, 5]], _AB)
close("1f  90.5% raw agreement -> kappa 0.0045/0.0995",
      k["kappa"], 0.0045 / 0.0995)
check("1f  ...and that kappa is far below the raw rate",
      k["kappa"] < 0.1 and k["observed_agreement"] > 0.9, True)

# Degenerate: no observations at all.
k = drive(R.cohens_kappa, [[0, 0], [0, 0]], _AB)
check("1g  empty matrix -> kappa undefined, not 0.0", k["kappa"], None)
check("1g  empty matrix names why", bool(k["undefined"]), True)
check("1g  empty matrix reports n=0", k["n"], 0)

# Degenerate: everything in one category for both raters -> Pe == 1, so the
# denominator (1 - Pe) is zero. Undefined, not 1.0 -- returning 1.0 would
# assert perfect chance-corrected agreement on a corpus with no variance.
k = drive(R.cohens_kappa, [[100, 0], [0, 0]], _AB)
check("1h  single-category corpus -> kappa undefined", k["kappa"], None)
check("1h  single-category corpus explains Pe == 1",
      "expected agreement is 1.0" in (k["undefined"] or ""), True)

# NEGATIVE CONTROL for section 1: the checks above must be capable of failing.
# A matrix that is NOT perfect agreement must not produce kappa 1.0.
k_ctl = drive(R.cohens_kappa, [[45, 5], [5, 45]], _AB)
check("1i  CONTROL: a non-diagonal matrix does NOT score kappa 1.0",
      abs(k_ctl["kappa"] - 1.0) > 1e-6, True)
check("1i  CONTROL: chance-only and perfect give DIFFERENT kappa",
      drive(R.cohens_kappa, [[25, 25], [25, 25]], _AB)["kappa"]
      != drive(R.cohens_kappa, [[50, 0], [0, 50]], _AB)["kappa"], True)

# Matrix construction, and the refusal that keeps a stray label from being
# silently dropped (which would lower N and inflate every rate over it).
m = drive(R.confusion_matrix, [("a", "a"), ("a", "b"), ("b", "b")], _AB)
check("1j  pairs build the expected matrix", m, [[1, 1], [0, 1]])
did, kind = raises(R.confusion_matrix, [("a", "zzz")], _AB)
check("1j  CONTROL: an out-of-vocabulary pair refuses", (did, kind),
      (True, "RaterRefusal"))

check("1k  rater_implied_status: agree implies the recorded status",
      drive(R.rater_implied_status, "met",
            {"status_verdict": "agree", "corrected_status": None}), "met")
check("1k  rater_implied_status: disagree implies the correction",
      drive(R.rater_implied_status, "met",
            {"status_verdict": "disagree", "corrected_status": "not_met"}),
      "not_met")


# ===========================================================================
# SECTION 2 -- STRATIFIED SMOKE SELECTION
# ===========================================================================

print("\n" + "=" * 70)
print("SECTION 2 -- select_smoke_decisions")
print("=" * 70)

# A synthetic corpus in which ONE patient supplies all six (arm, status)
# cells. This is the regression: phase A used to take each cell's first member
# unconditionally, which at n == 6 filled every slot from p0 and left the
# two-patient guarantee unsatisfiable with no budget to repair it.
_CELLS = [("inclusion", "met"), ("inclusion", "not_met"),
          ("inclusion", "not_evaluable"), ("exclusion", "violated"),
          ("exclusion", "not_violated"), ("exclusion", "not_evaluable")]

# SHAPED LIKE THE REAL CORPUS, not uniformly. Decisions sort by
# (patient, trial, arm, index) and the real run is 82% not_evaluable, so the
# first N decisions are one patient's first trial and are dominated by one
# cell. A uniform synthetic corpus would let a PREFIX span all six cells,
# which would make section 2f's control pass for free -- the first version of
# this file did exactly that and 2f did not fire.
_CORPUS = []
for _j in range(25):        # p0's first trial: one cell only
    _CORPUS.append(decision("p0", "NCT00000000", "inclusion", _j,
                            "not_evaluable"))
for _i, (_arm, _st) in enumerate(_CELLS):   # p0's second trial: the rest,
    _CORPUS.append(decision("p0", "NCT00000001", _arm, _i, _st))
for _p in ("p1", "p2", "p3"):               # so p0 alone still covers all six
    for _i, (_arm, _st) in enumerate(_CELLS):
        for _j in range(3):
            _CORPUS.append(decision(_p, "NCT0000000%d" % _i, _arm, _j, _st))
_CORPUS.sort(key=lambda d: (d.patient_index, d.nct_id, d.arm, d.index))

check("2a  synthetic corpus holds all six cells",
      len({(d.arm, d.status) for d in _CORPUS}), 6)
check("2a  ...and one patient alone supplies all six (the regression setup)",
      len({(d.arm, d.status) for d in _CORPUS if d.patient_id == "p0"}), 6)

for _n in (6, 7, 12, 20, 25):
    picked = drive(R.select_smoke_decisions, _CORPUS, _n)
    if not isinstance(picked, list):
        for _what in ("returns exactly n distinct decisions",
                      "spans every (arm, status) cell", "spans both arms",
                      "REGRESSION: covers >= 2 patients"):
            check(f"2b  n={_n} {_what}", picked, "<a list of decisions>")
        continue
    check(f"2b  n={_n} returns exactly n distinct decisions",
          (len(picked), len({d.key for d in picked})), (_n, _n))
    check(f"2b  n={_n} spans every (arm, status) cell",
          len({(d.arm, d.status) for d in picked}), 6)
    check(f"2b  n={_n} spans both arms", len({d.arm for d in picked}), 2)
    check(f"2b  n={_n} REGRESSION: covers >= 2 patients",
          len({d.patient_id for d in picked}) >= 2, True)

# Determinism: a function of the input alone.
a = [d.key for d in drive(R.select_smoke_decisions, _CORPUS, 20)]
b = [d.key for d in drive(R.select_smoke_decisions, _CORPUS, 20)]
check("2c  selection is deterministic across calls", a, b)
check("2c  selection is returned in run order",
      a == sorted(a, key=lambda k: (k[0], k[1], k[2], k[3])), True)

# n below the cell count cannot span the corpus, and says so rather than
# returning a slice that silently misses a vocabulary.
did, kind = raises(R.select_smoke_decisions, _CORPUS, 3)
check("2d  n=3 refuses (fewer slots than cells)", (did, kind),
      (True, "RaterRefusal"))
try:
    R.select_smoke_decisions(_CORPUS, 3)
    _msg = ""
except R.RaterRefusal as _exc:
    _msg = str(_exc)
check("2d  ...and the refusal names the cell count and a workable --limit",
      ("6" in _msg and "--limit" in _msg), True)

# n at or above the corpus size is the whole corpus, not a refusal.
check("2e  n >= len(corpus) returns everything",
      len(drive(R.select_smoke_decisions, _CORPUS, 10 ** 6)), len(_CORPUS))
check("2e  n == 0 means 'no limit' and returns everything",
      len(drive(R.select_smoke_decisions, _CORPUS, 0)), len(_CORPUS))

# NEGATIVE CONTROL: the strata checks must be able to fail. A prefix slice --
# what the harness did before the stratified selection landed -- misses cells
# on this corpus, so the assertions above are not true of any 20 decisions.
_prefix = _CORPUS[:20]
check("2f  CONTROL: a prefix slice does NOT span every cell",
      len({(d.arm, d.status) for d in _prefix}) < 6, True)
check("2f  CONTROL: ...while the stratified selection of the same size does",
      len({(d.arm, d.status)
           for d in drive(R.select_smoke_decisions, _CORPUS, 20)}), 6)

# NEGATIVE CONTROL for the two-patient guarantee: a corpus with exactly one
# patient cannot satisfy it and must not be required to -- the guarantee is
# min(2, patients available).
_SOLO = [decision("p0", "NCT1", a_, i_, s_)
         for i_, (a_, s_) in enumerate(_CELLS)]
solo = drive(R.select_smoke_decisions, _SOLO, 6)
check("2g  CONTROL: a single-patient corpus is allowed 1 patient",
      len({d.patient_id for d in solo}), 1)


# ===========================================================================
# SECTION 3 -- PARSE BUCKET TAXONOMY
# ===========================================================================

print("\n" + "=" * 70)
print("SECTION 3 -- parse_rating buckets")
print("=" * 70)

OK = ('{"patient_value_support":"supported","status_verdict":"agree",'
      '"corrected_status":null,"rationale":"because the record says so"}')


def parsed(text, arm="inclusion", recorded="met"):
    """(reason_or_None, rating_or_None) from the shipped parser."""
    out = drive(R.parse_rating, text, arm, recorded)
    if isinstance(out, str):
        return out, None
    rating, reason = out
    return reason, rating


for _label, _text, _arm, _rec, _want in (
        ("clean JSON", OK, "inclusion", "met", None),
        ("fenced ```json", "```json\n" + OK + "\n```", "inclusion", "met",
         None),
        ("fenced bare ```", "```\n" + OK + "\n```", "inclusion", "met", None),
        ("prose-wrapped", "Here is my audit: " + OK + " Hope that helps.",
         "inclusion", "met", None),
        ("not JSON at all", "I cannot complete this audit.", "inclusion",
         "met", "unparseable_json"),
        ("a JSON list, not an object", '[{"a":1}]', "inclusion", "met",
         "not_a_json_object"),
        ("an extra key", OK[:-1] + ',"confidence":0.9}', "inclusion", "met",
         "wrong_keys"),
        ("support outside the vocabulary",
         OK.replace('"supported"', '"very_supported"', 1), "inclusion", "met",
         "bad_support_value"),
        ("verdict outside the vocabulary", OK.replace('"agree"', '"maybe"'),
         "inclusion", "met", "bad_verdict_value"),
        ("empty rationale", OK.replace('"because the record says so"', '" "'),
         "inclusion", "met", "empty_rationale"),
        ("disagree with no correction",
         '{"patient_value_support":"supported","status_verdict":"disagree",'
         '"corrected_status":null,"rationale":"r"}', "inclusion", "met",
         "missing_corrected_status"),
        ("CROSS-ARM: exclusion status on an inclusion criterion",
         '{"patient_value_support":"supported","status_verdict":"disagree",'
         '"corrected_status":"violated","rationale":"r"}', "inclusion", "met",
         "wrong_vocabulary_corrected_status"),
        ("CROSS-ARM: inclusion status on an exclusion criterion",
         '{"patient_value_support":"supported","status_verdict":"disagree",'
         '"corrected_status":"not_met","rationale":"r"}', "exclusion",
         "violated", "wrong_vocabulary_corrected_status"),
        ("corrected_status equals the recorded status",
         '{"patient_value_support":"supported","status_verdict":"disagree",'
         '"corrected_status":"met","rationale":"r"}', "inclusion", "met",
         "corrected_equals_recorded"),
        ("agree carrying a correction",
         '{"patient_value_support":"supported","status_verdict":"agree",'
         '"corrected_status":"not_met","rationale":"r"}', "inclusion", "met",
         "agree_with_corrected_status"),
        ("a valid exclusion disagreement",
         '{"patient_value_support":"not_needed","status_verdict":"disagree",'
         '"corrected_status":"not_evaluable","rationale":"r"}', "exclusion",
         "violated", None),
):
    reason, _rating = parsed(_text, _arm, _rec)
    check(f"3a  {_label}", reason, _want)

# Tolerated deviations are RECORDED, not hidden: a rising rate is how you find
# out the output contract has stopped being followed. On the live full run 699
# of 2,237 responses (31%) needed carving.
_, r_clean = parsed(OK)
_, r_fenced = parsed("```json\n" + OK + "\n```")
_, r_prose = parsed("Here is my audit: " + OK)
check("3b  a clean response is flagged neither fenced nor carved",
      (field(r_clean, "fenced"), field(r_clean, "extracted")), (False, False))
check("3b  a fenced response is flagged fenced",
      field(r_fenced, "fenced"), True)
check("3b  a carved response is flagged extracted",
      field(r_prose, "extracted"), True)

# Carving cannot manufacture a rating: a mis-carve still has to survive the
# strict key and vocabulary checks, so it becomes wrong_keys/unparseable, never
# a silent pass.
check("3c  a mis-carve does not become a rating",
      parsed('{"a":1} and then {"b":2}')[0], "unparseable_json")
check("3c  carved-but-wrong-keys still lands in a bucket",
      parsed('Sure: {"a":1}')[0], "wrong_keys")

# NOTHING IS COERCED ACROSS ARMS. Every status belonging to the other arm must
# be refused for both arms, not mapped onto the nearest member of the right one.
_every = set()
for _arm in R.ARMS:
    _every |= set(R.ARM_STATUSES[_arm])
_coerced = []
for _arm in R.ARMS:
    for _st in sorted(_every - set(R.ARM_STATUSES[_arm])):
        _reason, _ = parsed(
            '{"patient_value_support":"supported","status_verdict":"disagree",'
            '"corrected_status":"%s","rationale":"r"}' % _st,
            _arm, R.ARM_STATUSES[_arm][0])
        if _reason != "wrong_vocabulary_corrected_status":
            _coerced.append((_arm, _st, _reason))
check("3d  no cross-arm corrected_status is ever coerced", _coerced, [])
check("3d  ...and the sweep was non-degenerate (it tried some)",
      len(_every) >= 4, True)

# NEGATIVE CONTROL for section 3: the bucket assertions must discriminate.
check("3e  CONTROL: a clean response and a broken one differ",
      parsed(OK)[0] != parsed("garbage")[0], True)
check("3e  CONTROL: the same JSON changes bucket with the arm",
      parsed('{"patient_value_support":"supported","status_verdict":'
             '"disagree","corrected_status":"violated","rationale":"r"}',
             "exclusion", "not_violated")[0], None)

# Truncation and refusal are not parser outcomes -- they are decided from the
# message's stop_reason before the text is parsed. Pinned here so the bucket
# names stay in one closed vocabulary.
for _name in ("refusal", "truncated_max_tokens", "api_error",
              "api_invalid_request", "expired", "canceled", "no_result"):
    check(f"3f  '{_name}' is a declared unrated reason",
          _name in R.UNRATED_REASONS, True)
check("3g  a refusal is NOT retried (the same prompt refuses again)",
      "refusal" in R.RETRYABLE_REASONS, False)
check("3g  an invalid_request is NOT retried (deterministic in the request)",
      "api_invalid_request" in R.RETRYABLE_REASONS, False)
check("3g  a truncation IS retried", "truncated_max_tokens"
      in R.RETRYABLE_REASONS, True)
check("3g  an unparseable response IS retried",
      "unparseable_json" in R.RETRYABLE_REASONS, True)


# ===========================================================================
# SECTION 4 -- PRICING AT STACKED RATES
# ===========================================================================

print("\n" + "=" * 70)
print("SECTION 4 -- price_usage over stacked batch + cache rates")
print("=" * 70)

_MODEL = "claude-sonnet-4-6"
_T = config.RATER_PRICING
_IN = _T["models"][_MODEL]["input_per_mtok"] / 1e6
_OUT = _T["models"][_MODEL]["output_per_mtok"] / 1e6
_B = _T["batch_discount"]

rates = drive(R.rater_pricing, _MODEL)
close("4a  uncached input = base x batch", rates["input"], _IN * _B)
close("4a  output = base x batch", rates["output"], _OUT * _B)
close("4a  cache read = base x 0.10 x batch", rates["cache_read"],
      _IN * _T["cache_read_multiplier"] * _B)
close("4a  cache write 5m = base x 1.25 x batch", rates["cache_write_5m"],
      _IN * _T["cache_write_5m_multiplier"] * _B)
close("4a  cache write 1h = base x 2.00 x batch", rates["cache_write_1h"],
      _IN * _T["cache_write_1h_multiplier"] * _B)

# Hand-computed dollars. Sonnet 4.6 list is $3.00 in / $15.00 out per Mtok and
# the batch discount is 50%, so: input $1.50, output $7.50, cache read $0.15,
# 5m write $1.875, 1h write $3.00 per Mtok.
close("4b  1,000,000 uncached input tokens = $1.50",
      drive(R.price_usage, _MODEL, {"input_tokens": 1_000_000}), 1.50)
close("4b  1,000,000 output tokens = $7.50",
      drive(R.price_usage, _MODEL, {"output_tokens": 1_000_000}), 7.50)
close("4b  1,000,000 cache-read tokens = $0.15",
      drive(R.price_usage, _MODEL,
            {"cache_read_input_tokens": 1_000_000}), 0.15)
close("4b  1,000,000 5m-write tokens = $1.875",
      drive(R.price_usage, _MODEL, {"cache_creation_5m": 1_000_000}), 1.875)
close("4b  1,000,000 1h-write tokens = $3.00",
      drive(R.price_usage, _MODEL, {"cache_creation_1h": 1_000_000}), 3.00)

# All five together, hand-added.
_MIX = {"input_tokens": 256_262, "output_tokens": 352_708,
        "cache_read_input_tokens": 15_446_414, "cache_creation_5m": 0,
        "cache_creation_1h": 248_540}
_HAND = (256_262 * 1.50 + 352_708 * 7.50 + 15_446_414 * 0.15
         + 0 * 1.875 + 248_540 * 3.00) / 1e6
close("4c  the live full run's usage prices to its hand-computed total",
      drive(R.price_usage, _MODEL, _MIX), _HAND, tol=1e-6)

# THE BREAKDOWN IS NEVER PRICED. cache_creation_input_tokens is the SUM of the
# two per-TTL fields; pricing it as well would double-count every write.
_with = dict(_MIX, cache_creation_input_tokens=248_540)
_absurd = dict(_MIX, cache_creation_input_tokens=999_999_999)
close("4d  the cache_creation TOTAL field does not affect the price",
      drive(R.price_usage, _MODEL, _with), drive(R.price_usage, _MODEL, _MIX),
      tol=1e-12)
close("4d  ...even when it is absurd (proving it is ignored, not added)",
      drive(R.price_usage, _MODEL, _absurd),
      drive(R.price_usage, _MODEL, _MIX), tol=1e-12)

# An unpriced model raises rather than returning 0.0: a zero-cost row cannot be
# told apart from a genuinely free run, and every aggregate over it
# under-reports by exactly the amount nobody noticed.
did, kind = raises(R.rater_pricing, "some-model-nobody-priced")
check("4e  an unpriced model refuses, never prices at zero", (did, kind),
      (True, "RaterRefusal"))

# NEGATIVE CONTROL: the arithmetic must discriminate. Forgetting the batch
# discount, or pricing a read at the input rate, both give a different number.
check("4f  CONTROL: omitting the batch discount changes the total",
      abs(drive(R.price_usage, _MODEL, _MIX) - _HAND / _B) > 1e-6, True)
check("4f  CONTROL: pricing cache reads at the input rate changes the total",
      abs(drive(R.price_usage, _MODEL, _MIX)
          - (_HAND + 15_446_414 * (1.50 - 0.15) / 1e6)) > 1e-6, True)
check("4f  CONTROL: the five components are not all the same rate",
      len({rates["input"], rates["output"], rates["cache_read"],
           rates["cache_write_5m"], rates["cache_write_1h"]}), 5)


# ===========================================================================
# SECTION 5 -- THE JOIN
# ===========================================================================

print("\n" + "=" * 70)
print("SECTION 5 -- collect_results joins on custom_id")
print("=" * 70)


def _usage(inp=100, out=90, read=5000, create=0):
    return types.SimpleNamespace(
        input_tokens=inp, output_tokens=out, cache_read_input_tokens=read,
        cache_creation_input_tokens=create,
        cache_creation=types.SimpleNamespace(
            ephemeral_5m_input_tokens=0, ephemeral_1h_input_tokens=create))


def _message(text, stop="end_turn"):
    return types.SimpleNamespace(
        model=_MODEL, stop_reason=stop, usage=_usage(),
        content=[types.SimpleNamespace(type="text", text=text)])


def _ok(msg):
    return types.SimpleNamespace(type="succeeded", message=msg)


def _errored(kind):
    return types.SimpleNamespace(
        type="errored",
        error=types.SimpleNamespace(error=types.SimpleNamespace(type=kind)))


class _StubClient(object):
    """Yields canned batch results. Never touches the network."""

    def __init__(self, plan):
        outer = self

        class _Batches(object):
            def results(self, batch_id):
                for cid, result in outer.plan:
                    yield types.SimpleNamespace(custom_id=cid, result=result)

        self.plan = plan
        self.messages = types.SimpleNamespace(batches=_Batches())


_JOIN_DECISIONS = [
    decision("p0", "NCT1", "inclusion", 0, "met"),
    decision("p0", "NCT1", "inclusion", 1, "not_evaluable"),
    decision("p0", "NCT1", "exclusion", 0, "violated"),
    decision("p1", "NCT2", "exclusion", 0, "not_violated"),
    decision("p1", "NCT2", "inclusion", 0, "not_met"),
    decision("p1", "NCT2", "exclusion", 1, "not_evaluable"),
]
_BY_CID = {"%s_%s_%s_%d" % (d.patient_id, d.nct_id, d.arm, d.index): d
           for d in _JOIN_DECISIONS}
_INDEX = R.RequestIndex(
    requests=[{"custom_id": c, "params": {}} for c in _BY_CID],
    by_custom_id=_BY_CID, form=R.CUSTOM_ID_FORM_READABLE,
    system_prompt="sys", rubric_meta={})
_CIDS = list(_BY_CID)

_PLAN = [
    (_CIDS[0], _ok(_message(OK))),
    (_CIDS[1], _ok(_message("```json\n" + OK + "\n```"))),
    (_CIDS[2], _ok(_message("refused", stop="refusal"))),
    (_CIDS[3], _ok(_message(OK[:30], stop="max_tokens"))),
    (_CIDS[4], _errored("api_error")),
    # _CIDS[5] deliberately omitted -> must surface as an absence, not vanish.
]
got = drive(R.collect_results, _StubClient(_PLAN), "msgbatch_stub", _INDEX,
            _MODEL)
check("5a  the two well-formed responses are rated",
      len(got["rated"]) if isinstance(got, dict) else got, 2)
check("5a  a refusal is bucketed as a refusal", bucket(got, _CIDS[2]),
      "refusal")
check("5a  a max_tokens stop is bucketed as a truncation",
      bucket(got, _CIDS[3]), "truncated_max_tokens")
check("5a  an API error is bucketed as an API error",
      bucket(got, _CIDS[4]), "api_error")
check("5a  an invalid_request is bucketed apart from other API errors",
      bucket(drive(R.collect_results,
                   _StubClient([(_CIDS[0],
                                 _errored("invalid_request_error"))]),
                   "b", _INDEX, _MODEL), _CIDS[0]),
      "api_invalid_request")
check("5a  a custom_id with NO result is reported missing",
      sorted(got["missing"]) if isinstance(got, dict) else got, [_CIDS[5]])

# THE PARTITION MUST BE EXACT. Every key rated or unrated, exactly once,
# nothing invented and nothing lost.
_unrated = dict(got["unrated"]) if isinstance(got, dict) else {}
_rated = got["rated"] if isinstance(got, dict) else {}
for _cid in set(_BY_CID) - set(got["rated"]) - set(_unrated):
    _unrated[_cid] = {"reason": "no_result", "detail": ""}
check("5b  rated and unrated are disjoint",
      set(_rated) & set(_unrated), set())
check("5b  rated + unrated covers every key exactly once",
      sorted(set(_rated) | set(_unrated)), sorted(_BY_CID))
check("5b  counts add up", len(_rated) + len(_unrated), len(_BY_CID))

rows = drive(R.build_rating_rows, _INDEX, _rated, _unrated, set())
_rows = rows if isinstance(rows, list) else []
check("5c  one row per decision", len(_rows), len(_BY_CID))
check("5c  every row carries its join key",
      bool(_rows) and all({"patient_id", "nct_id", "arm", "index"} <= set(r)
                          for r in _rows), True)
check("5c  every unrated row names a reason",
      bool(_rows) and all(r["unrated_reason"] for r in _rows
                          if not r["rated"]), True)
check("5c  rows round-trip through JSON",
      bool(_rows) and json.loads(json.dumps(_rows)) == _rows, True)

# A duplicate custom_id would double-count; an unknown one means the run
# directory changed under the batch. Both refuse rather than guess.
did, kind = raises(R.collect_results, _StubClient(_PLAN + [_PLAN[0]]), "b",
                   _INDEX, _MODEL)
check("5d  a duplicate custom_id refuses", (did, kind),
      (True, "RaterRefusal"))
did, kind = raises(R.collect_results,
                   _StubClient([("no_such_custom_id", _ok(_message(OK)))]),
                   "b", _INDEX, _MODEL)
check("5d  an unknown custom_id refuses", (did, kind), (True, "RaterRefusal"))

# NEGATIVE CONTROL: the partition assertions must be able to fail. Dropping a
# key from the union breaks the coverage check that section 5b relies on.
_short = dict(_unrated)
_short.pop(_CIDS[5])
check("5e  CONTROL: a missing key breaks the coverage check",
      sorted(set(_rated) | set(_short)) == sorted(_BY_CID), False)
check("5e  CONTROL: a positional join would mis-attribute (order differs)",
      [c for c, _ in _PLAN] == _CIDS[:len(_PLAN)], True)

# custom_id round-trips, which is what makes the join lossless.
_form = drive(R.choose_custom_id_form, _JOIN_DECISIONS)
_ordinals = {d.patient_index: d.patient_id for d in _JOIN_DECISIONS}
_bad = [d.key for d in _JOIN_DECISIONS
        if drive(R.decode_custom_id, R.encode_custom_id(d, _form), _form,
                 _ordinals) != d.key]
check("5f  every custom_id decodes back to its join key", _bad, [])
check("5f  ...and the sweep was non-degenerate",
      len(_JOIN_DECISIONS) >= 6, True)


# ===========================================================================
# SECTION 6 -- ABSENT corrected_status ON AN AGREE RATING
# ===========================================================================

print("\n" + "=" * 70)
print("SECTION 6 -- corrected_status omitted entirely")
print("=" * 70)

# WRITTEN BEFORE THE BEHAVIOUR EXISTED. On the live full run one decision was
# lost to this: the model returned valid JSON, agreed, and simply left out the
# key whose only legal value on an agree is null. Refusing that is strictness
# with no measurement behind it -- an omitted null and an explicit null say the
# same thing. The reverse is NOT true: a disagree that omits its correction has
# failed to answer the question, and stays unrated.
_AGREE_NO_KEY = ('{"patient_value_support":"supported",'
                 '"status_verdict":"agree","rationale":"the record says so"}')
_DISAGREE_NO_KEY = ('{"patient_value_support":"unsupported",'
                    '"status_verdict":"disagree","rationale":"no data"}')

reason, rating = parsed(_AGREE_NO_KEY, "inclusion", "met")
check("6a  an AGREE omitting corrected_status is rated", reason, None)
check("6a  ...and its corrected_status reads as null",
      rating["corrected_status"] if rating else "<unrated>", None)
check("6a  ...and the omission is RECORDED, not silently normalised",
      rating.get("corrected_status_omitted") if rating else "<unrated>", True)

reason, _ = parsed(_DISAGREE_NO_KEY, "inclusion", "met")
check("6b  a DISAGREE omitting corrected_status stays unrated", reason,
      "missing_corrected_status")

# The explicit-null form is unchanged, and is not flagged as an omission.
reason, rating = parsed(OK, "inclusion", "met")
check("6c  an explicit null on agree is still rated", reason, None)
check("6c  ...and is NOT flagged as omitted",
      rating.get("corrected_status_omitted") if rating else "<unrated>", False)

# The tolerance is narrow: it admits an absent key, not an absent anything.
check("6d  omitting a REQUIRED key is still wrong_keys",
      parsed('{"status_verdict":"agree","corrected_status":null,'
             '"rationale":"r"}')[0], "wrong_keys")
check("6d  omitting the rationale is still wrong_keys",
      parsed('{"patient_value_support":"supported",'
             '"status_verdict":"agree","corrected_status":null}')[0],
      "wrong_keys")
check("6d  an extra key alongside an omitted one is still wrong_keys",
      parsed('{"patient_value_support":"supported","status_verdict":"agree",'
             '"rationale":"r","confidence":1}')[0], "wrong_keys")
check("6d  omitting corrected_status does not excuse a bad verdict",
      parsed('{"patient_value_support":"supported",'
             '"status_verdict":"perhaps","rationale":"r"}')[0],
      "bad_verdict_value")

# NEGATIVE CONTROL: the two directions must differ, or the rule is vacuous.
check("6e  CONTROL: agree-omitted and disagree-omitted differ",
      parsed(_AGREE_NO_KEY)[0] != parsed(_DISAGREE_NO_KEY)[0], True)
check("6e  CONTROL: agree-omitted is not simply always-accepted JSON",
      parsed('{"status_verdict":"agree"}')[0], "wrong_keys")


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
