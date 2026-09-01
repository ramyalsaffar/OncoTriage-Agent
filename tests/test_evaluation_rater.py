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

  7  ``lift_rubric``. The rater's rulebook is SLICED BY MARKER out of the
     shipped Stage 5 prompt so that both models are judged against one text,
     and until PROMPT_VERSION 1.7.0 nothing exercised the slicing at all. 1.7.0
     edits that prompt, so section 7 is the standing answer to "do all five
     spans still lift, is every marker still unique, and did the edit reach the
     rubric" -- the last of which it answers NO, by assertion rather than by
     claim, because both of 1.7.0's additions lie outside every span.

  8  BLIND MODE. The rater is no longer shown the recorded status; it assigns
     its own and agreement is computed offline. Section 8 pins the anchored
     request bodies against a hash MEASURED FROM THE PRE-BLIND MODULE, proves
     the blind request is a function of everything except the answer (two runs
     differing only in the recorded status serialize to identical bytes),
     fires every new unrated reason, round-trips the retest custom_id through
     both id forms, and computes the confusion counts and the intra-rater
     figure by hand against a planted set. Its most valuable check is 8a's
     ORDER assertion: the first version of the blind change re-sorted the
     request list, which agreed with ``load_run``'s order on a real run and
     silently reordered a planted one. Nothing else in this file would have
     seen it.

NEGATIVE CONTROLS ARE INPUT-BASED, NOT PLANTED. Every function under test here
is pure, or takes its collaborators as arguments, so the natural control is a
different INPUT that must produce a different answer -- the shape
``tests/test_agent_patient_hash_coverage.py`` uses for the same reason. That
also keeps this file out of ``_EXEC_ALLOWLIST``: it execs nothing, loads no
module by location, and patches no shipped source.

ONE VALUE IN HERE COULD NOT BE DERIVED AND IS PINNED. Section 8a's hash was
computed by loading ``git show HEAD:oncotriage/evaluation/rater.py`` into a
throwaway module before blind mode existed. The shipped test reads no git: a
commit recedes, and a check that re-derives its expectation from whatever HEAD
happens to be agrees with the code by construction. It is built over a PLANTED
rubric rather than the real one, so an edit to ``oncotriage/agent/prompts.py``
cannot fail a check about whether blind mode disturbed anchored assembly --
section 7 is what guards the rubric.

ONE CONTROL CANNOT BE INPUT-BASED AND SAYS SO. ``lift_rubric()`` takes no
arguments, so the only way to drive its cross-probe invariance refusal is to
make the renderer answer differently for one probe. Section 7j rebinds
``rater.render_system_prompt`` inside a ``try``/``finally`` and asserts the
restore BY IDENTITY. That is an attribute rebind, not a patched source: nothing
is exec'd, nothing on disk is touched, and the claim above is unaffected.

NO NETWORK, NO KEYS, NO SPEND, NO DATABASE, NO CORPUS, NO GIT HISTORY. Every
decision, response and usage object in here is a literal built in this file.
The evaluation run directories are never read -- the harness's own
``default_run_dir()`` is never called -- so this file is unaffected by whether
a run exists on disk.

IT WRITES NOTHING IN THE REPOSITORY, and section 8p2 is the one place it writes
anything at all: a fresh ``tempfile.mkdtemp`` holding two state files, removed
in a ``finally`` with the removal then ASSERTED. That block cannot be
in-memory -- ``refuse_batch_from_other_mode`` exists to read a state file off
disk, and a control that faked one would be exercising a different function.
This file is still NOT in ``tests/run_serial_tests.py``'s collision matrix:
nothing it writes is in the repository, and the only repository file it reads
is the module under test, which neither of the suite's two writers writes.

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


def refusal_code(fn, *args, **kwargs):
    """The ``code`` slug of a RaterRefusal, or a named absence.

    A refusal that fires for the right reason and one that fires for the wrong
    reason are the same ``(True, 'RaterRefusal')`` to ``raises``. The slug is
    what tells them apart, and it is the slug the structured log records -- so
    where two refusals are reachable from one call, this is the assertion that
    means something.
    """
    try:
        fn(*args, **kwargs)
        return "<did not raise>"
    except R.RaterRefusal as exc:
        return exc.code
    except Exception as exc:                                   # noqa: BLE001
        return "<raised %s>" % type(exc).__name__


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
# SECTION 7 -- THE RUBRIC LIFT
# ===========================================================================
#
# ``lift_rubric()`` slices the rater's rulebook out of the SHIPPED Stage 5
# prompt by marker, so the two models are judged against one text rather than
# two that can drift. Nothing exercised it. That is the gap this section closes,
# and it is the gap PROMPT_VERSION 1.7.0 made worth closing now: that bump edits
# the prompt these markers slice, so from here on every prompt edit needs a
# standing answer to "do all five spans still lift, and is every marker still
# unique".
#
# WHAT 1.7.0 DID TO THE RUBRIC, ASSERTED RATHER THAN ASSUMED: nothing. Both of
# its additions -- Section 5's pre-disqualification check and the extended FINAL
# REMINDER -- lie OUTSIDE every lifted span, so the rubric text is unchanged.
# That is correct rather than an oversight, and 7d is where it is written down:
# the rater already receives RULE 4 inside `evaluation_rules` and C4 inside
# `absolute_constraints`, and 1.7.0 restates them for the CLASSIFIER at the
# moment it writes a rejecting status. Restating them again for a rater that
# judges one criterion at a time, and is never shown a trial verdict, would be
# telling it not to do something it cannot do.
#
# THIS SECTION EXECS NOTHING, so the file stays out of _EXEC_ALLOWLIST. Four of
# its five controls are pure INPUT -- ``_slice_span`` takes the rendered text as
# an argument, so a doctored string IS the control. The fifth has to reach
# ``lift_rubric``, which takes none, so it rebinds one module attribute inside a
# try/finally and asserts the restore by identity.

print("\n" + "=" * 70)
print("SECTION 7 -- lift_rubric slices the shipped Stage 5 prompt")
print("=" * 70)

_PROBE_RENDERS = [drive(R.render_system_prompt, **p) for p in R._RENDER_PROBES]
check("7a  non-degeneracy: all three declared render probes produced a real "
      "prompt (a raise here would make every span check below vacuous)",
      sorted({isinstance(t, str) and len(t) > 1000 for t in _PROBE_RENDERS}),
      [True])
check("7a  ...and they are not all the same text, so 'invariant across probes' "
      "is a claim about the spans rather than about one string",
      len(set(_PROBE_RENDERS)), len(R._RENDER_PROBES))

def lifted():
    """(rubric, meta), or ("", {}) when lift_rubric refused.

    A named absence rather than a raise or a string that later gets subscripted:
    every control below drives lift_rubric into its refusal branch on purpose,
    and a helper that let one escape would abort the file on exactly the runs
    it owes a summary.
    """
    out = drive(R.lift_rubric)
    return out if isinstance(out, tuple) and len(out) == 2 else ("", {})


_LIFTED = drive(R.lift_rubric)
check("7a  lift_rubric returns without refusing",
      isinstance(_LIFTED, tuple) and len(_LIFTED) == 2, True)
_RUBRIC, _META = lifted()

check("7b  every declared span lifted, in order",
      _META.get("span_order"), [n for n, _, _ in R._RUBRIC_SPANS])
check("7b  ...each non-empty",
      sorted({v > 0 for v in (_META.get("span_chars") or {"x": 0}).values()}),
      [True])
# `drive` around the slice, not a bare call: _slice_span REFUSES rather than
# returning, and the refusal is what half this section provokes deliberately.
_SPAN_BY_NAME = {n: (s, e) for n, s, e in R._RUBRIC_SPANS}
check("7b  ...and each appears VERBATIM in all three probe renders, which is "
      "what says the rubric is the prompt's own text and not a paraphrase",
      sorted({name for name in (_META.get("span_order") or [])
              for text in _PROBE_RENDERS
              if drive(R._slice_span, text, *_SPAN_BY_NAME.get(name, ("", "")),
                       name) not in text}),
      [])

# The property _slice_span refuses on, checked directly against every variant
# the pipeline can send rather than only against the probe set.
_MARKER_COUNTS = sorted({(text.count(m), m)
                         for _n, start, end in R._RUBRIC_SPANS
                         for m in (start, end)
                         for text in _PROBE_RENDERS})
check("7c  every rubric marker occurs exactly once in every probe render",
      sorted({n for n, _m in _MARKER_COUNTS}), [1])
check("7c  non-degeneracy: there are markers to count",
      len({m for _n, m in _MARKER_COUNTS}) >= 2 * len(R._RUBRIC_SPANS) - 2, True)

# 7d -- what 1.7.0 added is OUT, and what it restates is IN.
_ONE = _PROBE_RENDERS[0] if _PROBE_RENDERS else ""
_R17 = ('BEFORE YOU WRITE "not_met" OR "violated" ON ANY CRITERION',
        "ACTIVITY (RULE 4).", "ISOLATION (C4).",
        'A trial is never "not_eligible" because another trial in this message')
check("7d  non-degeneracy: 1.7.0's reinforcement IS in the rendered prompt",
      sorted({n in _ONE for n in _R17}), [True])
check("7d  ...and NONE of it reaches the lifted rubric: both additions sit "
      "outside every span",
      sorted(n for n in _R17 if n in _RUBRIC), [])
check("7d  ...while the rules it restates ARE in the rubric, which is why that "
      "is acceptable rather than a loss",
      ("If the criterion requires an active/current condition:" in _RUBRIC,
       "C4 -- TRIAL ISOLATION" in _RUBRIC), (True, True))
check("7d  ...and the rubric still carries RULE 4's reference date, surfaced "
      "for the caller to check against the run under audit",
      bool(_META.get("reference_date_in_rules")), True)

# 7d' -- 1.9.0 SPLITS ACROSS THE SPAN BOUNDARY, AND THE SPLIT IS THE POINT.
#
# The bump made two edits and they reach different audiences. RULE 4's
# time-window branch rides INSIDE `evaluation_rules`, so the rater judges a time
# window under the same rule the classifier does -- and that is not a nicety:
# 1.9.0 requires the interval QUOTED INTO patient_value before it decides, so a
# rater lifting a rubric without that instruction would score the classifier's
# quoted-interval evidence against a rule that never asked for it, and disagree
# for rubric mismatch rather than for decision quality. The FINAL REMINDER's line
# lies OUTSIDE every span, exactly as 1.7.0's two do, and that is precedent
# rather than a gap: the reminder restates a rule the rater already holds, for a
# classifier about to emit a verdict the rater never emits.
#
# ASSERTED IN BOTH DIRECTIONS. "the rubric contains X" alone would be satisfied
# by a span widened to swallow the whole prompt, and "the rubric omits Y" alone
# by a lift that had stopped working; the pair pins the boundary itself.
_R19_IN = ("Quote the record's stated interval for that event verbatim in "
           "patient_value")
# 1.10.0 REWROTE THIS SENTENCE AND THE STALE LITERAL WOULD HAVE PASSED FOR THE
# WRONG REASON. `_R19_OUT in _RUBRIC` is False for a sentence the template no
# longer contains at all, so the "does NOT reach the rubric" check below would
# have stayed green over a reminder that had been deleted outright. What catches
# that is the non-degeneracy check under it -- both sentences must be in the
# RENDERED prompt first -- and it is the reason that check exists.
_R19_OUT = ("For any time-window criterion: an ONGOING condition or medication "
            "is inside the window whatever its interval says; otherwise the "
            "record's stated interval, quoted verbatim, decides it.")
# 1.10.0's OTHER edit, the RULE 4 gate. It rides INSIDE `evaluation_rules`, like
# 1.9.0's branch and for a sharper reason: a rater still holding 1.9.0's rule
# would score a "violated" written on a 29-year-old ACTIVE condition as a defect
# of the classifier, and disagree for rubric mismatch rather than for decision
# quality -- which is the confound this harness exists to remove.
_R110_IN = ("is present NOW and therefore present within any window reaching "
            "the reference date, whatever its interval")
check("7d' non-degeneracy: all three pinned sentences ARE in the rendered "
      "prompt, so the checks below are about the span boundary rather than "
      "about a template that lost them",
      (_R19_IN in _ONE, _R19_OUT in _ONE, _R110_IN in _ONE),
      (True, True, True))
check("7d' 1.9.0's RULE 4 branch DOES reach the lifted rubric, so the rater "
      "judges time windows under the classifier's own rule",
      _R19_IN in _RUBRIC, True)
check("7d' ...and its FINAL REMINDER line does NOT, on 1.7.0's precedent",
      _R19_OUT in _RUBRIC, False)
check("7d'' 1.10.0's ongoing gate DOES reach the lifted rubric, so the rater "
      "judges an ongoing event inside the window exactly as the classifier is "
      "told to",
      _R110_IN in _RUBRIC, True)
# WHICH span carries it, sliced per span rather than searched in the assembled
# rubric: "somewhere in the rubric" would also be satisfied by a boundary that
# had drifted and swept the branch into a neighbour, which would ship the rater a
# rule under a heading that misdescribes it.
_R19_SPANS = [n for n, (s, e) in _SPAN_BY_NAME.items()
              if _R19_IN in str(drive(R._slice_span, _ONE, s, e, n))]
check("7d' ...and the branch lands in `evaluation_rules` specifically, not in "
      "some other span that drifted over it",
      _R19_SPANS, ["evaluation_rules"])
_R110_SPANS = [n for n, (s, e) in _SPAN_BY_NAME.items()
               if _R110_IN in str(drive(R._slice_span, _ONE, s, e, n))]
check("7d'' ...and so does 1.10.0's gate, in the same span and not a neighbour "
      "that drifted over it",
      _R110_SPANS, ["evaluation_rules"])

check("7e  the meta digests one sha per span, keyed by the span names",
      sorted(_META.get("span_sha256") or {}),
      sorted(n for n, _, _ in R._RUBRIC_SPANS))
check("7e  lifting twice produces the same rubric (it is a pure function of "
      "the shipped template)",
      lifted()[1].get("rubric_sha256"), _META.get("rubric_sha256"))

# --- the controls ----------------------------------------------------------
_START, _END = R._RUBRIC_SPANS[0][1], R._RUBRIC_SPANS[0][2]
check("7f  CONTROL: a start marker occurring twice refuses",
      raises(R._slice_span, _ONE + "\n" + _START, _START, _END, "probe"),
      (True, "RaterRefusal"))
check("7g  CONTROL: a marker occurring zero times refuses",
      raises(R._slice_span, _ONE, "A MARKER NO TEMPLATE CONTAINS", _END,
             "probe"),
      (True, "RaterRefusal"))
check("7h  CONTROL: an end marker before the start refuses",
      raises(R._slice_span, _ONE, _END, _START, "probe"),
      (True, "RaterRefusal"))
# The lifted-empty branch is DEFENSIVE and the input that reaches it says so:
# the slice starts AT the start marker, so it can only strip to nothing when
# that marker is itself whitespace. No entry in _RUBRIC_SPANS is, and none
# should be -- which is exactly why the branch needs a control rather than a
# reader's assurance that it can never fire. The first version of this line
# used "AAA"/"\nBBB" and did NOT refuse: the slice was "AAA", non-empty,
# because the start marker is inside its own span. Measured, not reasoned.
check("7i  CONTROL: a span that lifts empty refuses",
      raises(R._slice_span, "X  Y", "  ", "Y", "probe"),
      (True, "RaterRefusal"))
check("7f-7i CONTROL: the same call on the real text does NOT refuse "
      "(the other half of all four)",
      raises(R._slice_span, _ONE, _START, _END, "probe"), (False, None))

# 7j -- THE INVARIANCE REFUSAL, the one thing four input controls cannot reach.
# lift_rubric takes no arguments, so the only way to make a lifted span differ
# BETWEEN probes is to make the renderer answer differently for one of them.
# That is the defect this refusal exists for: a future edit interpolating a
# run-specific value inside a span would bake one probe patient's data into
# every rater request for every criterion of every run.
_saved_render = R.render_system_prompt
_calls = {"n": 0}


def _perturbing_render(**kwargs):
    """The shipped renderer, with one lifted line altered on the SECOND probe."""
    _calls["n"] += 1
    text = _saved_render(**kwargs)
    if _calls["n"] == 2:
        text = text.replace("This rule has ZERO exceptions.",
                            "This rule has ZERO exceptions (probe 2).", 1)
    return text


try:
    R.render_system_prompt = _perturbing_render
    _p7j = raises(R.lift_rubric)
finally:
    R.render_system_prompt = _saved_render
check("7j  CONTROL: a span that is not invariant across the probes refuses",
      _p7j, (True, "RaterRefusal"))
check("7j  ...the perturbation was real: the needle it edits is in the rubric "
      "exactly once (a replace that matched nothing would make 7j a no-op "
      "reporting success)",
      _RUBRIC.count("This rule has ZERO exceptions."), 1)
check("7j  ...and the renderer was restored by identity",
      R.render_system_prompt is _saved_render, True)
check("7j  ...so the lift is clean again afterwards",
      lifted()[1].get("rubric_sha256"), _META.get("rubric_sha256"))


# ===========================================================================
# SECTION 8 -- BLIND MODE
# ===========================================================================
#
# Anchored rating shows the rater the recorded status and asks agree/disagree.
# That leaks the answer, so every anchored agreement figure is an admitted
# upper bound. Blind mode withholds the status: the rater assigns its own from
# the arm's vocabulary and agreement is computed offline by comparison.
#
# THE CENTRAL CLAIM OF THIS SECTION IS AN INVARIANCE, NOT AN ABSENCE SCAN.
# "the status does not appear in the request" is the obvious check and it is
# the weak one: all five status words legitimately appear in a blind request,
# because the rubric defines them and the decision block names the arm's
# vocabulary. A scan for the word "met" would fire on every request ever built.
# So 8c builds two requests that differ ONLY in the recorded status and
# requires the serialized bytes to be EQUAL -- the request is a function of
# everything except the status, which is the property, stated directly. The
# sentinel scan in 8d is the second, independent form of the same question, and
# both carry a control that fires by reintroducing the leak.
#
# 8a IS A PIN AND ITS VALUE WAS ESTABLISHED AGAINST THE PRE-CHANGE MODULE, not
# against the module it now guards. It was computed by loading
# ``git show HEAD:oncotriage/evaluation/rater.py`` into a throwaway module and
# hashing the anchored request bodies it produced for the planted run below.
# That measurement found a real defect in the blind change before it shipped:
# the first version re-sorted the request list, which agreed with ``load_run``'s
# own order on a real run and REORDERED a planted one. The bodies were
# identical and the sequence was not; nothing else here would have seen it.
#
# THE PIN IS DELIBERATELY RUBRIC-INDEPENDENT. It is built over a planted
# rubric string rather than ``lift_rubric()``, so an edit to
# ``oncotriage/agent/prompts.py`` -- which legitimately changes every real
# request -- does not fail a check about whether BLIND MODE disturbed anchored
# assembly. Section 7 is what guards the rubric; this guards the envelope.
#
# NO NETWORK, NO KEYS, NO SPEND, NO DISK. Every decision, response and usage
# object is a literal. The stub client from section 5 is reused.

print("\n" + "=" * 70)
print("SECTION 8 -- blind mode")
print("=" * 70)

# --- the planted run, shared by the whole section -------------------------
# Shaped so that both arms, all five statuses and two patients are present:
# the vocabulary checks below need every cell, and a single-patient plant would
# make the retest stratification degenerate.
_PLANT = [
    ("pA", 0, "NCT00000001", "inclusion", 0, "Age >= 18 years",
     "Age 61 years", "met", "matches"),
    ("pA", 0, "NCT00000001", "inclusion", 1, "ECOG 0-1", "ECOG 1", "met",
     "matches"),
    ("pA", 0, "NCT00000001", "exclusion", 0, "Prior chemotherapy",
     "Not in patient record", "not_evaluable", "matches"),
    ("pA", 0, "NCT00000002", "inclusion", 0, "Stage IV disease", "Stage 4",
     "not_met", "near_miss"),
    ("pB", 1, "NCT00000003", "exclusion", 0, "Active infection",
     "No active infection documented", "not_violated", "matches"),
    ("pB", 1, "NCT00000003", "exclusion", 1, "Pregnancy",
     "Female, no pregnancy recorded", "violated", "matches"),
    ("pB", 1, "NCT00000003", "inclusion", 0, "Measurable disease",
     "Not applicable -- no imaging", "not_evaluable", "matches"),
]
_SUMMARIES = {"pA": "PATIENT A\nAge 61 | Sex female | ECOG 1\nBreast cancer.",
              "pB": "PATIENT B\nAge 47 | Sex male | ECOG 0\nColon cancer."}
_ORDER = {"pA": 0, "pB": 1}

# A rubric that is NOT the real one, carrying only the two markers
# ``lift_arm_status_definitions`` slices on. Using a planted rubric is what
# makes 8a independent of prompts.py; it also proves the arm-definition lift
# works against text rather than against one specific prompt version.
_FIXED_RUBRIC = (
    "PLANTED RUBRIC -- not the real one.\n"
    "INCLUSION CRITERIA use exactly one status:\n"
    '"met" a\n"not_met" b\n"not_evaluable" c\n\n'
    "EXCLUSION CRITERIA use exactly one status:\n"
    '"not_violated" d\n"violated" e\n"not_evaluable" f\n\n'
    "THE TWO VOCABULARIES ARE DISJOINT AND NON-INTERCHANGEABLE.\n")


def planted_run(status_overrides=None):
    """A RunInput built from literals. Nothing on disk is consulted.

    ``status_overrides`` replaces the recorded status of every decision, which
    is how 8c produces two runs differing in nothing else.
    """
    decisions = []
    for (p, pi, n, a, i, c, v, s, g) in _PLANT:
        if status_overrides:
            s = status_overrides.get(a, s)
        decisions.append(R.Decision(
            patient_id=p, patient_index=pi, nct_id=n, arm=a, index=i,
            criterion=c, patient_value=v, status=s, verdict_group=g))
    return R.RunInput("/planted", {}, dict(_SUMMARIES), decisions,
                      dict(_ORDER))


def built(mode, run=None, retest_fraction=0.0, seed=42, rubric=None):
    """A RequestIndex over the planted run, or a marker string on a raise."""
    rubric = _FIXED_RUBRIC if rubric is None else rubric
    defs = (drive(R.lift_arm_status_definitions, rubric)
            if mode == R.MODE_BLIND else None)
    if isinstance(defs, str):
        return defs
    return drive(R.build_requests, run or planted_run(),
                 drive(R.build_system_prompt, rubric, mode=mode),
                 {"rubric_sha256": "x"}, _MODEL, 300, 0.0, "1h",
                 mode=mode, arm_definitions=defs,
                 retest_fraction=retest_fraction, retest_seed=seed)


def blob(index):
    """The serialized request list, exactly as it would go on the wire."""
    if not hasattr(index, "requests"):
        return "<no requests: %r>" % (index,)
    return json.dumps(index.requests, sort_keys=True, ensure_ascii=False)


def sha(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- 8a -- ANCHORED IS BYTE-IDENTICAL WITH THE FLAG OFF -------------------
# The value was measured against git show HEAD: before the blind change
# existed. See the section header.
_ANCHORED_PIN = \
    "bfab8d8257cfbbf4937cf275af4ba94b646a47edc54fcd7cbfda9a8ddeb15a5a"

_anch = built(R.MODE_ANCHORED)
check("8a  anchored request bodies hash to the value measured from the "
      "PRE-BLIND module (git show HEAD:), so the flag being off is "
      "byte-identical rather than merely similar",
      sha(blob(_anch)), _ANCHORED_PIN)
check("8a  non-degeneracy: the pin is over a real, non-empty request list",
      len(_anch.requests) if hasattr(_anch, "requests") else 0, len(_PLANT))
check("8a  ...and the ORDER is the run's own order, which is what the first "
      "version of build_requests silently changed",
      [r["custom_id"] for r in _anch.requests]
      if hasattr(_anch, "requests") else [],
      ["%s_%s_%s_%d" % (p, n, a, i)
       for (p, _pi, n, a, i, _c, _v, _s, _g) in _PLANT])

# CONTROL: the pin must be capable of failing. A one-field perturbation of the
# same list must not hash to it.
_perturbed = json.loads(blob(_anch))
_perturbed[0]["params"]["max_tokens"] = 301
check("8a  CONTROL: a single changed field breaks the pin",
      sha(json.dumps(_perturbed, sort_keys=True, ensure_ascii=False))
      != _ANCHORED_PIN, True)

check("8b  build_system_prompt defaults to anchored: the no-mode call and the "
      "explicit anchored call are the same string",
      drive(R.build_system_prompt, _FIXED_RUBRIC)
      == drive(R.build_system_prompt, _FIXED_RUBRIC,
               mode=R.MODE_ANCHORED), True)
check("8b  ...and the blind system prompt is a DIFFERENT string (a mode flag "
      "that changed nothing would make every check below vacuous)",
      drive(R.build_system_prompt, _FIXED_RUBRIC)
      != drive(R.build_system_prompt, _FIXED_RUBRIC, mode=R.MODE_BLIND), True)
check("8b  an unknown mode refuses rather than falling through to anchored",
      raises(R.build_system_prompt, _FIXED_RUBRIC, mode="sideways"),
      (True, "RaterRefusal"))

# --- 8c -- THE CIRCULARITY PROOF -----------------------------------------
# Two runs identical in every field except the recorded status. In blind mode
# the serialized requests must be EQUAL; in anchored mode they must differ.
_RUN_X = planted_run({"inclusion": "met", "exclusion": "violated"})
_RUN_Y = planted_run({"inclusion": "not_evaluable",
                      "exclusion": "not_evaluable"})
check("8c  non-degeneracy: the two planted runs really do differ in their "
      "recorded statuses (equal runs would make this pass for free)",
      [d.status for d in _RUN_X.decisions]
      != [d.status for d in _RUN_Y.decisions], True)
check("8c  ...and differ in NOTHING else",
      [(d.patient_id, d.nct_id, d.arm, d.index, d.criterion, d.patient_value)
       for d in _RUN_X.decisions]
      == [(d.patient_id, d.nct_id, d.arm, d.index, d.criterion,
           d.patient_value) for d in _RUN_Y.decisions], True)
check("8c  BLIND: the serialized request is byte-identical under two "
      "different recorded statuses -- the request is a function of everything "
      "EXCEPT the answer",
      blob(built(R.MODE_BLIND, _RUN_X)) == blob(built(R.MODE_BLIND, _RUN_Y)),
      True)
check("8c  CONTROL: ANCHORED requests for the same two runs DIFFER, so the "
      "comparison above can fail",
      blob(built(R.MODE_ANCHORED, _RUN_X))
      != blob(built(R.MODE_ANCHORED, _RUN_Y)), True)

# --- 8d -- THE SENTINEL SCAN, an independent form of the same question ----
# A status that cannot occur naturally, so any appearance in the serialized
# request is the recorded status having reached it. Scanned over the whole
# serialization rather than asked of the builder.
_SENTINEL = "ZZ_SENTINEL_STATUS_ZZ"
_RUN_S = R.RunInput("/planted", {}, dict(_SUMMARIES), [
    R.Decision(patient_id="pA", patient_index=0, nct_id="NCT00000001",
               arm="inclusion", index=0, criterion="Age >= 18 years",
               patient_value="Age 61 years", status=_SENTINEL,
               verdict_group="matches")], dict(_ORDER))
check("8d  BLIND: the recorded status does not appear anywhere in the "
      "serialized request",
      _SENTINEL in blob(built(R.MODE_BLIND, _RUN_S)), False)
check("8d  CONTROL: it DOES appear in the anchored request, so the scan is "
      "capable of detecting a leak",
      _SENTINEL in blob(built(R.MODE_ANCHORED, _RUN_S)), True)
check("8d  BLIND: no field LABEL implying a recorded decision survives either",
      any(needle in blob(built(R.MODE_BLIND, _RUN_S))
          for needle in ("recorded_status", "recorded_patient_value",
                         "RECORDED_DECISION")), False)
check("8d  CONTROL: all three labels are present in the anchored request",
      all(needle in blob(built(R.MODE_ANCHORED, _RUN_S))
          for needle in ("recorded_status", "recorded_patient_value",
                         "RECORDED_DECISION")), True)

# The structural half: the builder cannot be handed a status at all.
import inspect as _inspect                                    # noqa: E402
check("8e  build_blind_decision_block takes no status parameter -- leaking one "
      "is a TypeError at the call site, not a review comment",
      "status" in _inspect.signature(
          R.build_blind_decision_block).parameters, False)
check("8e  ...and passing one raises",
      raises(R.build_blind_decision_block, "inclusion", "c", "v", "defs",
             "met")[0], True)
check("8e  the blind block still names the arm's three allowed statuses",
      all(f'"{s}"' in drive(R.build_blind_decision_block, "inclusion", "c",
                            "v", "defs")
          for s in R.ARM_STATUSES["inclusion"]), True)

# --- 8f -- THE ARM DEFINITION LIFT ---------------------------------------
_DEFS = drive(R.lift_arm_status_definitions, _FIXED_RUBRIC)
check("8f  both arms lift a definition block",
      sorted(_DEFS) if isinstance(_DEFS, dict) else _DEFS,
      ["exclusion", "inclusion"])
check("8f  the inclusion block defines the inclusion vocabulary and no "
      "exclusive exclusion status",
      isinstance(_DEFS, dict)
      and all(f'"{s}"' in _DEFS["inclusion"] for s in ("met", "not_met",
                                                       "not_evaluable"))
      and not any(f'"{s}"' in _DEFS["inclusion"]
                  for s in ("violated", "not_violated")), True)
check("8f  the exclusion block does the same in reverse",
      isinstance(_DEFS, dict)
      and all(f'"{s}"' in _DEFS["exclusion"]
              for s in ("violated", "not_violated", "not_evaluable"))
      and not any(f'"{s}"' in _DEFS["exclusion"]
                  for s in ("met", "not_met")), True)
check("8f  CONTROL: a rubric whose inclusion block also defines an exclusion "
      "status refuses rather than shipping it to a blind rater",
      raises(R.lift_arm_status_definitions,
             _FIXED_RUBRIC.replace('"not_met" b', '"violated" b', 1)),
      (True, "RaterRefusal"))
check("8f  CONTROL: a rubric missing a marker refuses",
      raises(R.lift_arm_status_definitions,
             _FIXED_RUBRIC.replace(
                 "EXCLUSION CRITERIA use exactly one status:", "GONE", 1)),
      (True, "RaterRefusal"))
check("8f  ...and the real shipped rubric lifts cleanly (a planted-rubric-only "
      "check would prove nothing about the prompt actually used)",
      sorted(drive(R.lift_arm_status_definitions, _RUBRIC))
      if _RUBRIC else "<no rubric>", ["exclusion", "inclusion"])

# --- 8g -- THE BLIND PARSE, happy path per arm ---------------------------
BLIND_OK = ('{"assigned_status":"%s","patient_value_support":"supported",'
            '"rationale":"the record states it"}')


def blind_parsed(text, arm="inclusion", recorded="met"):
    out = drive(R.parse_rating, text, arm, recorded, mode=R.MODE_BLIND)
    if isinstance(out, str):
        return out, None
    rating, reason = out
    return reason, rating


for _arm in R.ARMS:
    for _status in R.ARM_STATUSES[_arm]:
        _reason, _rating = blind_parsed(BLIND_OK % _status, _arm)
        check(f"8g  {_arm}/{_status} parses cleanly", _reason, None)
        check(f"8g  {_arm}/{_status} is carried through verbatim",
              field(_rating, "assigned_status"), _status)

_reason, _rating = blind_parsed("```json\n" + (BLIND_OK % "met") + "\n```")
check("8g  a markdown fence is tolerated and RECORDED", (_reason,
      field(_rating, "fenced")), (None, True))
_reason, _rating = blind_parsed("Here you go: " + (BLIND_OK % "met") + " ok?")
check("8g  a prose preamble is carved and RECORDED", (_reason,
      field(_rating, "extracted")), (None, True))

# --- 8h -- EVERY BLIND FAILURE MODE, each in its own named bucket ---------
for _label, _text, _arm, _want in (
        ("cross-arm: an exclusion status assigned to an inclusion criterion",
         BLIND_OK % "violated", "inclusion",
         "wrong_vocabulary_assigned_status"),
        ("cross-arm: an inclusion status assigned to an exclusion criterion",
         BLIND_OK % "not_met", "exclusion",
         "wrong_vocabulary_assigned_status"),
        ("a status that is not a status at all",
         BLIND_OK % "eligible", "inclusion", "bad_assigned_status"),
        ("an empty status", BLIND_OK % "", "inclusion", "bad_assigned_status"),
        ("a null status",
         '{"assigned_status":null,"patient_value_support":"supported",'
         '"rationale":"r"}', "inclusion", "bad_assigned_status"),
        ("a numeric status",
         '{"assigned_status":3,"patient_value_support":"supported",'
         '"rationale":"r"}', "inclusion", "bad_assigned_status"),
        ("a missing assigned_status",
         '{"patient_value_support":"supported","rationale":"r"}', "inclusion",
         "wrong_keys"),
        ("a missing rationale",
         '{"assigned_status":"met","patient_value_support":"supported"}',
         "inclusion", "wrong_keys"),
        ("an extra key",
         (BLIND_OK % "met")[:-1] + ',"confidence":0.9}', "inclusion",
         "wrong_keys"),
        ("an ANCHORED-shaped response returned in blind mode", OK, "inclusion",
         "wrong_keys"),
        ("support outside the vocabulary",
         (BLIND_OK % "met").replace('"supported"', '"very_supported"', 1),
         "inclusion", "bad_support_value"),
        ("an empty rationale",
         (BLIND_OK % "met").replace('"the record states it"', '"   "', 1),
         "inclusion", "empty_rationale"),
        ("a non-string rationale",
         '{"assigned_status":"met","patient_value_support":"supported",'
         '"rationale":7}', "inclusion", "empty_rationale"),
        ("not JSON at all", "I cannot classify this.", "inclusion",
         "unparseable_json"),
        ("a JSON list, not an object", '[{"a":1}]', "inclusion",
         "not_a_json_object"),
):
    check(f"8h  {_label} -> {_want}", blind_parsed(_text, _arm)[0], _want)

check("8h  'not_evaluable' is legal on BOTH arms, so it can never be foreign",
      (blind_parsed(BLIND_OK % "not_evaluable", "inclusion")[0],
       blind_parsed(BLIND_OK % "not_evaluable", "exclusion")[0]), (None, None))
check("8h  both new reasons are declared in UNRATED_REASONS",
      all(r in R.UNRATED_REASONS for r in ("wrong_vocabulary_assigned_status",
                                           "bad_assigned_status")), True)
check("8h  both new reasons are retryable, like their anchored analogues",
      all(r in R.RETRYABLE_REASONS
          for r in ("wrong_vocabulary_assigned_status",
                    "bad_assigned_status")), True)
check("8h  a refusal is still NOT retryable (the additions did not widen the "
      "non-retryable set)",
      ("refusal" in R.RETRYABLE_REASONS,
       "api_invalid_request" in R.RETRYABLE_REASONS), (False, False))
check("8h  nothing is coerced: a cross-arm answer is unrated, never mapped "
      "onto the nearest legal member",
      blind_parsed(BLIND_OK % "violated", "inclusion")[1], None)

# --- 8i -- THE BLIND PARSE DOES NOT CONSULT THE RECORDED STATUS -----------
# The parameter exists so the two modes share one call site. If the blind
# branch read it, the mode would be anchored at the parser instead of at the
# prompt -- the same leak one layer down.
_by_recorded = {}
for _rec in list(R.ARM_STATUSES["inclusion"]) + ["not_violated", None, ""]:
    _by_recorded[_rec] = blind_parsed(BLIND_OK % "not_met", "inclusion",
                                      _rec)[1]
check("8i  a blind parse returns the identical rating for EVERY possible "
      "recorded status, including ones the arm cannot hold",
      len({json.dumps(v, sort_keys=True) for v in _by_recorded.values()}), 1)
check("8i  non-degeneracy: those parses actually produced a rating",
      field(_by_recorded["met"], "assigned_status"), "not_met")
check("8i  CONTROL: the ANCHORED parser DOES depend on the recorded status -- "
      "the same response is rated against one and refused against another",
      (parsed('{"patient_value_support":"supported",'
              '"status_verdict":"disagree","corrected_status":"not_met",'
              '"rationale":"r"}', "inclusion", "met")[0],
       parsed('{"patient_value_support":"supported",'
              '"status_verdict":"disagree","corrected_status":"not_met",'
              '"rationale":"r"}', "inclusion", "not_met")[0]),
      (None, "corrected_equals_recorded"))

# --- 8j -- OFFLINE AGREEMENT ARITHMETIC ----------------------------------
_agree = drive(R.apply_offline_agreement,
               {"assigned_status": "met"}, "met")
_disagree = drive(R.apply_offline_agreement,
                  {"assigned_status": "not_met"}, "met")
check("8j  assigned == recorded -> agree, no correction",
      (field(_agree, "status_verdict"), field(_agree, "corrected_status"),
       field(_agree, "agrees_with_recorded")), ("agree", None, True))
check("8j  assigned != recorded -> disagree, correction is the assignment",
      (field(_disagree, "status_verdict"),
       field(_disagree, "corrected_status"),
       field(_disagree, "agrees_with_recorded")),
      ("disagree", "not_met", False))
check("8j  every blind rating says its verdict is arithmetic, not a claim the "
      "model made",
      (field(_agree, "verdict_basis"), field(_disagree, "verdict_basis")),
      ("offline_comparison", "offline_comparison"))
check("8j  the model's own answer survives beside the derived pair",
      (field(_agree, "assigned_status"), field(_disagree, "assigned_status")),
      ("met", "not_met"))
check("8j  rater_implied_status reads a derived blind rating the same way it "
      "reads an anchored one, which is what lets summarize serve both",
      (drive(R.rater_implied_status, "met", _agree),
       drive(R.rater_implied_status, "met", _disagree)), ("met", "not_met"))

# --- 8k -- THE RETEST CUSTOM_ID ROUND-TRIP -------------------------------
_D = decision("p0", "NCT00000009", "inclusion", 3, "met")
for _form in (R.CUSTOM_ID_FORM_READABLE, R.CUSTOM_ID_FORM_COMPACT):
    _base = drive(R.encode_custom_id, _D, _form)
    _rt = drive(R.encode_retest_custom_id, _base)
    check(f"8k  {_form}: the retest id is the primary plus the stated suffix",
          _rt, _base + R.RETEST_SUFFIX)
    check(f"8k  {_form}: it is recognisable as a retest and the primary is not",
          (drive(R.is_retest_custom_id, _rt),
           drive(R.is_retest_custom_id, _base)), (True, False))
    check(f"8k  {_form}: BOTH ids decode to the SAME original decision key",
          (drive(R.decode_custom_id, _base, _form, {0: "p0"}),
           drive(R.decode_custom_id, _rt, _form, {0: "p0"})),
          (_D.key, _D.key))
check("8k  the suffix is inside the API's [a-zA-Z0-9_-] alphabet",
      bool(R._CUSTOM_ID_RE.match("x" + R.RETEST_SUFFIX)), True)
check("8k  it uses '-' rather than '_': a '_' suffix would be eaten by the "
      "rsplit('_', 3) both id forms decode with, shifting every field",
      "_" in R.RETEST_SUFFIX, False)
check("8k  CONTROL: a '_r2' suffix really would mis-decode, which is why the "
      "character choice is load-bearing rather than cosmetic",
      drive(R.decode_custom_id, "p0_NCT00000009_inclusion_3_r2",
            R.CUSTOM_ID_FORM_READABLE, {0: "p0"}) == _D.key, False)
# The reserve is not decoration: on the real 1.7.0 validation runs the longest
# readable id is 61 characters and the suffix is 3, which lands EXACTLY on the
# 64-character ceiling. A patient id one character longer overflows, and
# without the reserve the overflow would be discovered after the form had been
# chosen and recorded. The width below is measured rather than guessed: a
# 38-character patient id gives a 62-character readable id, which fits alone
# and does not fit with the suffix.
_LONG = decision("p" + "x" * 37, "NCT00000009", "inclusion", 3, "met")
check("8k  non-degeneracy: the probe id really does sit in the 2-character "
      "window where the reserve is what decides",
      len(drive(R.encode_custom_id, _LONG, R.CUSTOM_ID_FORM_READABLE)), 62)
check("8k  the ceiling reserve is charged before the form is chosen: an id "
      "that fits alone but not with a 3-character suffix falls back to "
      "compact rather than overflowing later",
      (drive(R.choose_custom_id_form, [_LONG]),
       drive(R.choose_custom_id_form, [_LONG],
             reserve=len(R.RETEST_SUFFIX))),
      (R.CUSTOM_ID_FORM_READABLE, R.CUSTOM_ID_FORM_COMPACT))

# --- 8l -- THE RETEST SUBSAMPLE ------------------------------------------
_all = planted_run().decisions
_sel1 = drive(R.select_retest_decisions, _all, 0.5, 42)
_sel1b = drive(R.select_retest_decisions, _all, 0.5, 42)
check("8l  the selection is deterministic: same seed, same decisions",
      [d.key for d in _sel1], [d.key for d in _sel1b])
check("8l  non-degeneracy: it selected something, and not everything",
      0 < len(_sel1) < len(_all), True)

# THE SEED CHECK IS OVER A RANGE, NOT A PAIR, and the first version of it was
# a pair and FAILED -- on a 7-decision corpus the selection has only 18
# possible outcomes, and seeds 42 and 7 happened to land on the same one. That
# is not a defect in the seeding (it is read: 8 seeds produce 6 distinct
# subsamples here), it is a two-sample test on a space too small to sample
# twice. Asserting that the selection VARIES across a range says the thing
# meant, and cannot fail on an unlucky pair.
_across = {tuple(d.key for d in drive(R.select_retest_decisions, _all, 0.5, s))
           for s in (42, 7, 1, 2, 3, 99, "alpha", "beta")}
check("8l  the seed is read: a range of seeds produces several different "
      "subsamples (a seed nothing consulted would produce exactly one)",
      len(_across) > 1, True)
check("8l  ...and a specific measured pair differs, so the property has a "
      "concrete witness rather than only an aggregate one",
      [d.key for d in _sel1]
      != [d.key for d in drive(R.select_retest_decisions, _all, 0.5, 1)], True)
check("8l  it is patient-stratified: every patient in the corpus contributes",
      {d.patient_id for d in _sel1}, {d.patient_id for d in _all})
check("8l  fraction 1.0 selects every decision",
      len(drive(R.select_retest_decisions, _all, 1.0, 42)), len(_all))
check("8l  fraction 0 selects none and does not raise",
      drive(R.select_retest_decisions, _all, 0.0, 42), [])
check("8l  a fraction outside (0, 1] refuses by name",
      (raises(R.select_retest_decisions, _all, 1.5, 42),
       raises(R.select_retest_decisions, _all, -0.2, 42)),
      ((True, "RaterRefusal"), (True, "RaterRefusal")))
check("8l  a tiny fraction still gives every patient at least one, which is "
      "what stratification means",
      {d.patient_id for d in drive(R.select_retest_decisions, _all, 0.01, 42)},
      {d.patient_id for d in _all})

# --- 8m -- TWO CUSTOM_IDS, ONE DECISION, LOSSLESSLY ----------------------
_bi = built(R.MODE_BLIND, retest_fraction=0.5, seed=42)
check("8m  the index carries more requests than decisions, by exactly the "
      "retest count",
      (len(_bi.requests), len(_bi.retest_ids),
       len(_bi.requests) - len(_bi.retest_ids)),
      (len(_PLANT) + len(_bi.retest_ids), len(_bi.retest_ids), len(_PLANT)))
check("8m  primaries and retests partition the index exactly",
      _bi.primary_ids & _bi.retest_ids, set())
check("8m  ...and together cover it",
      _bi.primary_ids | _bi.retest_ids, set(_bi.by_custom_id))
check("8m  every retest id maps to the SAME Decision object as its primary "
      "(the lossless join with two ids legally naming one decision)",
      all(_bi.by_custom_id[c] is _bi.by_custom_id[R.strip_retest_suffix(c)[0]]
          for c in _bi.retest_ids), True)
check("8m  the primary request order is untouched by the retest pass",
      [c for c in (r["custom_id"] for r in _bi.requests)
       if not R.is_retest_custom_id(c)],
      [r["custom_id"] for r in _anch.requests])
check("8m  each retest sits immediately after the primary it duplicates, so "
      "it is inside that patient's cache block",
      all(_bi.requests[i - 1]["custom_id"]
          == R.strip_retest_suffix(r["custom_id"])[0]
          for i, r in enumerate(_bi.requests)
          if R.is_retest_custom_id(r["custom_id"])), True)
check("8m  a retest request is byte-identical to its primary apart from the "
      "custom_id -- it is the same question, not a similar one",
      all(_bi.by_custom_id[c] is not None
          and json.dumps([r["params"] for r in _bi.requests
                          if r["custom_id"] == c][0], sort_keys=True)
          == json.dumps([r["params"] for r in _bi.requests
                         if r["custom_id"]
                         == R.strip_retest_suffix(c)[0]][0], sort_keys=True)
          for c in _bi.retest_ids), True)
check("8m  --retest-fraction is refused in anchored mode by name",
      raises(R.build_requests, planted_run(), "sys", {}, _MODEL, 300, 0.0,
             "1h", retest_fraction=0.5), (True, "RaterRefusal"))
check("8m  blind mode without the arm definitions refuses rather than sending "
      "a bare three-word vocabulary",
      refusal_code(R.build_requests, planted_run(), "sys", {}, _MODEL, 300,
                   0.0, "1h", mode=R.MODE_BLIND), "arm_definitions_absent")
check("8m  ...and a PARTIAL mapping refuses too: one arm defined and the other "
      "blank is a silent asymmetry in the instrument, not a KeyError to be "
      "discovered in the request loop",
      (refusal_code(R.build_requests, planted_run(), "sys", {}, _MODEL, 300,
                    0.0, "1h", mode=R.MODE_BLIND,
                    arm_definitions={"inclusion": "defs"}),
       refusal_code(R.build_requests, planted_run(), "sys", {}, _MODEL, 300,
                    0.0, "1h", mode=R.MODE_BLIND,
                    arm_definitions={"inclusion": "defs", "exclusion": "  "})),
      ("arm_definitions_absent", "arm_definitions_absent"))
check("8m  an anchored index reports no retests, so every table below is the "
      "identity on it",
      (_anch.mode, _anch.retest_ids, _anch.primary_ids == set(
          _anch.by_custom_id)), (R.MODE_ANCHORED, set(), True))

# --- 8n -- COLLECTION AND THE OFFLINE CONFUSION ARITHMETIC ---------------
# A planted response per request, with KNOWN assignments, so the confusion
# counts and the agreement rate below are computed by hand and compared.
#
#   pA/NCT1/inclusion[0]  recorded met            -> assigned met         agree
#   pA/NCT1/inclusion[1]  recorded met            -> assigned not_met  disagree
#   pA/NCT1/exclusion[0]  recorded not_evaluable  -> assigned not_evaluable
#   pA/NCT2/inclusion[0]  recorded not_met        -> assigned not_met      agree
#   pB/NCT3/exclusion[0]  recorded not_violated   -> assigned violated  disagree
#   pB/NCT3/exclusion[1]  recorded violated       -> assigned violated     agree
#   pB/NCT3/inclusion[0]  recorded not_evaluable  -> assigned not_evaluable
# 7 primaries, 5 agree, 2 disagree -> 5/7.
_ASSIGNED = {
    "pA_NCT00000001_inclusion_0": "met",
    "pA_NCT00000001_inclusion_1": "not_met",
    "pA_NCT00000001_exclusion_0": "not_evaluable",
    "pA_NCT00000002_inclusion_0": "not_met",
    "pB_NCT00000003_exclusion_0": "violated",
    "pB_NCT00000003_exclusion_1": "violated",
    "pB_NCT00000003_inclusion_0": "not_evaluable",
}
# The two retests: one repeats its primary's answer, one changes it. Chosen
# explicitly rather than derived, so the intra-rater arithmetic below is a
# hand-computed 1/2 rather than whatever the selection happened to produce.
_bi2 = built(R.MODE_BLIND, retest_fraction=1.0, seed=42)
_RETEST_ANSWER = dict(_ASSIGNED)
_RETEST_ANSWER["pA_NCT00000001_inclusion_1"] = "met"      # changed its mind


def _blind_plan(index, flip=()):
    plan = []
    for req in index.requests:
        cid = req["custom_id"]
        base, is_rt = R.strip_retest_suffix(cid)
        status = (_RETEST_ANSWER if is_rt else _ASSIGNED)[base]
        if base in flip and is_rt:
            status = _ASSIGNED[base]
        plan.append((cid, _ok(_message(BLIND_OK % status))))
    return plan


_collected = drive(R.collect_results, _StubClient(_blind_plan(_bi2)),
                   "msgbatch_blind", _bi2, _MODEL)
check("8n  every request came back rated, primaries and retests alike",
      len(_collected["rated"]) if isinstance(_collected, dict) else _collected,
      len(_bi2.requests))
check("8n  collection stamped the offline comparison onto every blind rating",
      isinstance(_collected, dict)
      and {r["verdict_basis"] for r in _collected["rated"].values()},
      {"offline_comparison"})
check("8n  ...and marked which ratings are retests",
      isinstance(_collected, dict)
      and sum(1 for r in _collected["rated"].values() if r["is_retest"]),
      len(_bi2.retest_ids))

_summary = drive(R.summarize, _bi2, _collected["rated"], {}, planted_run())
check("8n  the headline counts PRIMARIES ONLY -- a retested decision must not "
      "vote twice",
      (_summary["decisions_total"], _summary["decisions_rated"]),
      (len(_PLANT), len(_PLANT)))
check("8n  hand-computed agreement: 5 of 7 primaries agree",
      (_summary["overall_agree"], _summary["overall_disagree"]), (5, 2))
close("8n  ...so the rate is 5/7", _summary["overall_agreement_rate"], 5 / 7.0)
check("8n  hand-computed per-arm, per-status confusion counts, DIAGONAL "
      "INCLUDED",
      _summary["confusion_counts"],
      {"inclusion": {"met": {"met": 1, "not_met": 1},
                     "not_met": {"not_met": 1},
                     "not_evaluable": {"not_evaluable": 1}},
       "exclusion": {"not_violated": {"violated": 1},
                     "violated": {"violated": 1},
                     "not_evaluable": {"not_evaluable": 1}}})
check("8n  the summary says which mode produced it, and that a blind verdict "
      "is arithmetic",
      (_summary["mode"], "BLIND" in _summary["anchoring"]),
      (R.MODE_BLIND, True))
check("8n  the residual leaks are stated IN THE OUTPUT, not only in a design "
      "note -- including the patient_value one",
      "patient_value_is_the_judged_model_s_own_extract"
      in _summary["circularity_limitations"], True)

# --- the residual leak is MEASURED, not described -------------------------
# Hand-computed over the plant: 1 extract begins "Not in patient record"
# (recorded not_evaluable), 1 begins "Not applicable" (recorded
# not_evaluable), and 5 are quoted data (met, met, not_met, not_violated,
# violated -> most common is "met" at 2 of 5). A guesser seeing only the class
# is right 1 + 1 + 2 = 4 of 7; the majority status over all 7 is "met" at 2,
# tied with not_evaluable at 2 -- Counter.most_common breaks the tie by
# insertion order, which is why the assertion below reads the rate rather than
# the label.
_leak = _summary["patient_value_leak_measured"]
check("8l  the marker classes are bucketed by Stage 5's two documented "
      "conventions, prefix-matched and case-folded",
      (drive(R.patient_value_marker_class, "Not in patient record"),
       drive(R.patient_value_marker_class, "not in patient record for this"),
       drive(R.patient_value_marker_class, "Not applicable -- no imaging"),
       drive(R.patient_value_marker_class, "ECOG 1"),
       drive(R.patient_value_marker_class, None)),
      ("MARKER: not in patient record", "MARKER: not in patient record",
       "MARKER: not applicable", "quoted data", "quoted data"))
check("8n  the leak measurement buckets the plant as hand-counted",
      {k: v["decisions"] for k, v in _leak["marker_classes"].items()},
      {"MARKER: not in patient record": 1, "MARKER: not applicable": 1,
       "quoted data": 5})
close("8n  ...and a guesser seeing ONLY the extract class scores 4/7",
      _leak["status_predictable_from_marker_class"], 4 / 7.0)
check("8n  the excess over the base rate is reported beside it, so a skewed "
      "corpus cannot make the leak look small by itself",
      _leak["excess_over_base_rate"] is not None
      and abs(_leak["status_predictable_from_marker_class"]
              - _leak["majority_status_base_rate"]
              - _leak["excess_over_base_rate"]) < 1e-12, True)
check("8n  the limitation prose quotes the measured size rather than only "
      "asserting 'correlates strongly'",
      "patient_value_leak_size_on_this_run"
      in _summary["circularity_limitations"], True)
check("8n  ...and the sentence names the leakiest class AND the excess, "
      "because either alone misleads in a different direction",
      all(s in _summary["circularity_limitations"][
          "patient_value_leak_size_on_this_run"]
          for s in ("100.0%", "not in patient record",
                    "over always answering", "per-class shares")), True)
# The tie-break is deterministic and meaningful, not dict order. Both marker
# classes in the plant reach 1.0, and the first version of this ranking took
# whichever came out of the dict first -- which on the REAL run named a class
# covering 3% of decisions instead of the one covering 74%. Ranked by
# confidence, then corpus share, then name.
_TIED = [decision("p0", "NCT1", "inclusion", i, "met",
                  value="Not applicable -- x") for i in range(2)] + \
        [decision("p0", "NCT1", "exclusion", i, "not_evaluable",
                  value="Not in patient record") for i in range(8)]
check("8n  the leakiest-class tie-break prefers the class covering more of "
      "the corpus, not whichever the dict yielded first",
      "not in patient record" in drive(
          R.blind_circularity_limitations,
          drive(R.measure_patient_value_leak, _TIED)
      )["patient_value_leak_size_on_this_run"], True)
check("8n  CONTROL: reverse the sizes and the OTHER class is named, so the "
      "tie-break is reading the shares rather than a fixed string",
      "not applicable" in drive(
          R.blind_circularity_limitations,
          drive(R.measure_patient_value_leak,
                [decision("p0", "NCT1", "inclusion", i, "met",
                          value="Not applicable -- x") for i in range(8)]
                + [decision("p0", "NCT1", "exclusion", i, "not_evaluable",
                            value="Not in patient record")
                   for i in range(2)])
      )["patient_value_leak_size_on_this_run"], True)
# The prose must not assert a SIZE it has not measured. The first version said
# "the small excess" unconditionally -- false on any corpus where it is large.
check("8n  the sentence makes no hardcoded claim that the excess is small",
      "small" in _summary["circularity_limitations"][
          "patient_value_leak_size_on_this_run"], False)
check("8n  an empty decision list is reported as such rather than dividing by "
      "zero",
      drive(R.measure_patient_value_leak, [])["decisions"], 0)
# CONTROL: a corpus where the marker carries NO information must not report a
# perfect predictor. Without this, the check above would pass for a function
# that hard-coded 1.0.
_mixed = [decision("p0", "NCT1", "inclusion", i,
                   ["met", "not_met"][i % 2], value="Not in patient record")
          for i in range(10)]
close("8n  CONTROL: when the marker class splits 50/50 across statuses the "
      "measurement reports 0.5, not a perfect predictor",
      drive(R.measure_patient_value_leak,
            _mixed)["marker_classes"]["MARKER: not in patient record"][
                "share_with_that_status"], 0.5)
check("8n  CONTROL: the anchored summary carries neither the blind confusion "
      "table nor the limitations block, so 8n is not passing on a field every "
      "summary has",
      ("confusion_counts" in _summary,
       "confusion_counts" in drive(R.summarize, _anch, {}, {}, planted_run())),
      (True, False))

# --- 8o -- INTRA-RATER AGREEMENT -----------------------------------------
_rt = _summary["retest"]
check("8o  one pair per retested decision, both copies rated",
      (_rt["duplicates_submitted"], _rt["pairs"]),
      (len(_bi2.retest_ids), len(_bi2.retest_ids)))
check("8o  hand-computed: 6 of 7 pairs identical, 1 changed",
      (_rt["identical"], _rt["changed"]), (6, 1))
close("8o  ...so intra-rater agreement is 6/7",
      _rt["intra_rater_agreement_rate"], 6 / 7.0)
check("8o  the decision that moved is named, with both answers",
      [(c["arm"], c["index"], c["first_assigned"], c["second_assigned"])
       for c in _rt["changed_decisions"]],
      [("inclusion", 1, "not_met", "met")])
check("8o  it is reported SEPARATELY: the headline agreement is unchanged by "
      "the retest answers",
      (_summary["overall_agree"], _summary["overall_disagree"]), (5, 2))
check("8o  a pair whose primary failed to parse is NOT counted as unstable",
      drive(R.retest_report, _bi2,
            {c: v for c, v in _collected["rated"].items()
             if c != "pA_NCT00000001_inclusion_1"})["pairs"],
      len(_bi2.retest_ids) - 1)
check("8o  ...and is reported as an incomplete pair instead",
      drive(R.retest_report, _bi2,
            {c: v for c, v in _collected["rated"].items()
             if c != "pA_NCT00000001_inclusion_1"}
            )["pairs_incomplete"]["only_retest_rated"], 1)
check("8o  with no retest requested the block says so rather than reporting a "
      "rate over zero pairs",
      drive(R.retest_report, _anch, {})["pairs"], 0)

# --- 8p -- THE STATE FILE CANNOT BE READ ACROSS MODES --------------------
check("8p  the two modes name different state files by default",
      drive(R.state_filename, R.MODE_BLIND)
      != drive(R.state_filename, R.MODE_ANCHORED), True)
check("8p  an anchored state file resumed as blind refuses by name",
      raises(R.require_state_mode, {"mode": "anchored"}, R.MODE_BLIND, "/s"),
      (True, "RaterRefusal"))
check("8p  and the reverse",
      raises(R.require_state_mode, {"mode": "blind"}, R.MODE_ANCHORED, "/s"),
      (True, "RaterRefusal"))
check("8p  a state file with NO mode predates blind mode and reads as "
      "anchored, so an in-flight anchored resume is not broken",
      (raises(R.require_state_mode, {"batches": []}, R.MODE_ANCHORED, "/s"),
       raises(R.require_state_mode, {"batches": []}, R.MODE_BLIND, "/s")),
      ((False, None), (True, "RaterRefusal")))
check("8p  an absent state file is not a mismatch",
      raises(R.require_state_mode, None, R.MODE_BLIND, "/s"), (False, None))
# THE COMMONER MISTAKE IS NOT A STATE FILE READ ACROSS MODES, IT IS A
# FORGOTTEN FLAG. Primary custom_ids are IDENTICAL in both modes, so resuming
# a blind batch without --blind finds no state file (the default directories
# differ), polls happily, and parses blind responses under the anchored
# contract -- where every one of them is wrong_keys. 8p2 covers that.
#
# This is the ONE place in this file that touches a filesystem, and it touches
# only a fresh temp directory: the guard reads state files, so a control that
# faked them in memory would be testing a different function.
import os as _osmod                                            # noqa: E402
import shutil as _shutil                                       # noqa: E402
import tempfile as _tempfile                                   # noqa: E402

_tmp = _tempfile.mkdtemp(prefix="rater-resume-guard-")
try:
    _blind_dir = _osmod.path.join(_tmp, "rater_blind")
    _osmod.makedirs(_blind_dir)
    R.write_state(_osmod.path.join(_blind_dir, R.state_filename(R.MODE_BLIND)),
                  {"mode": "blind",
                   "batches": [{"id": "msgbatch_theblindone", "tag": "primary",
                                "chunk": 0, "requests": 7}]})
    check("8p2 resuming a BLIND batch without --blind is refused by name, "
          "before anything is polled -- the failure it prevents is every "
          "response bucketed wrong_keys, which reads as a broken rater rather "
          "than as a forgotten flag",
          refusal_code(R.refuse_batch_from_other_mode,
                       ["msgbatch_theblindone"], R.MODE_ANCHORED,
                       _osmod.path.join(_tmp, "rater"), _tmp),
          "resume_mode_mismatch")
    check("8p2 ...and the message says which flag to add",
          "--blind" in str(drive(
              lambda: R.refuse_batch_from_other_mode(
                  ["msgbatch_theblindone"], R.MODE_ANCHORED,
                  _osmod.path.join(_tmp, "rater"), _tmp))), True)
    check("8p2 CONTROL: a batch id the other mode does NOT claim passes, so "
          "the guard is reading the state file rather than refusing every "
          "resume",
          raises(R.refuse_batch_from_other_mode, ["msgbatch_unrelated"],
                 R.MODE_ANCHORED, _osmod.path.join(_tmp, "rater"), _tmp),
          (False, None))
    check("8p2 CONTROL: the same id resumed in the mode that OWNS it passes",
          raises(R.refuse_batch_from_other_mode, ["msgbatch_theblindone"],
                 R.MODE_BLIND, _blind_dir, _tmp), (False, None))
    check("8p2 an empty id list is not a mismatch",
          raises(R.refuse_batch_from_other_mode, [], R.MODE_ANCHORED,
                 _osmod.path.join(_tmp, "rater"), _tmp), (False, None))
    check("8p2 a run directory with no state file anywhere is not a mismatch",
          raises(R.refuse_batch_from_other_mode, ["msgbatch_theblindone"],
                 R.MODE_ANCHORED, _osmod.path.join(_tmp, "nope"),
                 _osmod.path.join(_tmp, "nope")), (False, None))
finally:
    _shutil.rmtree(_tmp, ignore_errors=True)
check("8p2 the temp directory was removed", _osmod.path.isdir(_tmp),
      False)

check("8p  the refusal names the mismatch by slug rather than raising "
      "generically",
      refusal_code(R.require_state_mode, {"mode": "anchored"}, R.MODE_BLIND,
                   "/s"), "state_mode_mismatch")
check("8p  the message names both modes, so an operator is not left guessing "
      "which file to move",
      all(word in str(drive(lambda: R.require_state_mode(
          {"mode": "anchored"}, R.MODE_BLIND, "/s")))
          for word in ("anchored", "blind", "--output-dir")), True)

# --- 8q -- THE MODE REACHES THE PLAN, AND THE OUTPUT DIRECTORIES DIFFER ---
check("8q  --blind and --retest-fraction are real CLI arguments",
      (drive(R._parse_args, ["--dry-run", "--blind"]).blind,
       drive(R._parse_args, ["--dry-run", "--blind",
                             "--retest-fraction", "0.1"]).retest_fraction),
      (True, 0.1))
check("8q  the flag defaults OFF, so an unmodified invocation is anchored",
      drive(R._parse_args, ["--dry-run"]).blind, False)
# The slug matters here rather than only the raise. _prepare refuses this
# BEFORE it resolves a run directory, which is both the right ordering (a flag
# combination is a configuration defect and cheap to detect) and what keeps
# this file's "the evaluation run directories are never read" claim true. A
# reordering that read the run first would still raise RaterRefusal -- with
# code "run_dir_invalid" on a machine with no such directory -- so asserting
# only that it raised would pass for the wrong reason and quietly make this
# file depend on the corpus.
check("8q  --retest-fraction without --blind is refused, by its own slug, "
      "before any run directory is resolved",
      refusal_code(R._prepare, drive(R._parse_args,
                                     ["--dry-run", "--retest-fraction",
                                      "0.1"])),
      "retest_requires_blind")


# ===========================================================================
# SECTION 9 -- THE INCLUDE LIST (--include-keys)
# ===========================================================================
#
# WHAT IS UNDER TEST. A file naming an exact set of decisions to rate, in the
# rater's own join key ``patient_id|nct_id|arm|index``. The property that
# matters is not "it selects fewer" -- it is that it selects EXACTLY what it was
# asked for or refuses. A subset request that quietly rates a partial
# intersection produces a smaller sample under the same headline and is
# indistinguishable from a clean run, and an empty intersection rates nothing,
# spends nothing and exits 0.
#
# HOW THE CONTROLS ARE BUILT. Almost everything here is a pure function of its
# argument, so the natural control is a DIFFERENT INPUT rather than a mutated
# copy of the module -- the same footing as
# ``tests/test_agent_patient_hash_coverage.py``. Every "must refuse" is paired
# with the neighbouring input that must NOT refuse, and every "must be
# byte-identical" with the input that must differ; otherwise a check that had
# stopped checking would pass by refusing everything or by agreeing with
# everything.
#
# THE STRONGEST CHECK IN THIS SECTION IS ALREADY ABOVE IT. Section 8a pins the
# serialized anchored request list against a sha measured before blind mode
# existed. It still passes, which is what says this section's flag changed no
# byte of a run that does not use it. 9a asserts the other half of that -- that
# such a run reports NO subset metadata, rather than an empty dict a reader
# would have to interpret.

print("\n" + "=" * 70)
print("SECTION 9 -- the include list: exactly what was asked, or a refusal")
print("=" * 70)

import os as _os9                                              # noqa: E402
import shutil as _shutil9                                      # noqa: E402
import tempfile as _tempfile9                                   # noqa: E402


def _key_line(d):
    """One include-list line for a Decision."""
    return R.INCLUDE_KEY_SEPARATOR.join(
        (d.patient_id, d.nct_id, d.arm, str(d.index)))


def keyfile_text(decisions, header=True):
    """The include-list text naming exactly these decisions."""
    lines = ["# planted include list"] if header else []
    lines.extend(_key_line(d) for d in decisions)
    return "\n".join(lines) + "\n"


def n_requests(index):
    """The request count, or a named absence.

    ``len(index.requests)`` aborts the whole file the moment a regression makes
    ``build_requests`` refuse -- which is exactly when this file owes a
    summary. The first version of 9i did precisely that: it passed ``limit=5``
    to a planted run holding six (arm, status) cells, the smoke selector
    refused as it should, and the run died with a traceback where it owed 46
    results. Same shape as ``field`` and ``bucket`` above.
    """
    if not hasattr(index, "requests"):
        return "<no requests: %r>" % (index,)
    return len(index.requests)


def meta_of(index, key):
    """One field of an index's subset metadata, or a named absence."""
    if not hasattr(index, "include_keys_meta"):
        return "<no index: %r>" % (index,)
    if not isinstance(index.include_keys_meta, dict):
        return "<include_keys_meta is %r>" % (index.include_keys_meta,)
    return index.include_keys_meta.get(key, "<field absent>")


def keys_of(index):
    """The selected decisions' keys in request order, or a named absence."""
    if not hasattr(index, "requests"):
        return "<no requests: %r>" % (index,)
    return [index.by_custom_id[r["custom_id"]].key for r in index.requests]


def dig(obj, *path):
    """Walk a nested mapping that may be a named absence at any depth.

    The general form of ``field`` / ``meta_of`` / ``rounded``. The revert
    harness walked this file's guards outward one call site at a time -- a
    marker reaching ``.requests``, then ``round()``, then an attribute read,
    then a subscript -- which is the argument for having one of these rather
    than four ad-hoc ones.
    """
    cur = obj
    for step in path:
        if isinstance(cur, dict):
            if step not in cur:
                return "<%r absent>" % (step,)
            cur = cur[step]
        else:
            return "<not a mapping: %r>" % (cur,)
    return cur


def has(obj, *path):
    """True/False for presence, or the marker explaining why it could not ask."""
    got = dig(obj, *path)
    if isinstance(got, str) and got.startswith("<not a mapping"):
        return got
    return not (isinstance(got, str) and got.endswith(" absent>"))


def fingerprint_of(index):
    """``include_keys_fingerprint`` on a value that may be a named absence.

    The production function reads an attribute and must NOT be widened to
    accept a non-index -- that would make it silently answer for a caller who
    passed the wrong thing. So the tolerance lives here, at the call site,
    which is where a marker can appear.
    """
    if not hasattr(index, "include_keys_meta"):
        return "<no index: %r>" % (index,)
    return R.include_keys_fingerprint(index)


def rounded(value, digits):
    """round() on a value that may be a named absence.

    A guard that returns a marker STRING is only half the fix if the call site
    then does arithmetic on it: the revert harness got past ``meta_of`` and died
    on ``round("<include_keys_meta is None>", 4)``. The guard has to reach the
    outermost operation, not the innermost read.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    return round(value, digits)


def cids_of(index):
    """The custom_ids in request order, or a named absence."""
    if not hasattr(index, "requests"):
        return "<no requests: %r>" % (index,)
    return [r["custom_id"] for r in index.requests]


def ttls_of(index):
    """Every cache_control ttl on the wire, deduplicated and sorted.

    Reads BOTH the system block and the user content blocks, because the two
    are set from one variable and a change that reached only one of them would
    otherwise pass.
    """
    if not hasattr(index, "requests"):
        return "<no requests: %r>" % (index,)
    return sorted({b["cache_control"]["ttl"] for r in index.requests
                   for b in (r["params"]["system"]
                             + r["params"]["messages"][0]["content"])
                   if "cache_control" in b})


def built_with(keys, mode=R.MODE_BLIND, run=None, meta=None, limit=0):
    """A RequestIndex over the planted run with an include list, or a marker."""
    rubric = _FIXED_RUBRIC
    defs = (drive(R.lift_arm_status_definitions, rubric)
            if mode == R.MODE_BLIND else None)
    if isinstance(defs, str):
        return defs
    return drive(R.build_requests, run or planted_run(),
                 drive(R.build_system_prompt, rubric, mode=mode),
                 {"rubric_sha256": "x"}, _MODEL, 300, 0.0, "1h",
                 mode=mode, arm_definitions=defs, limit=limit,
                 include_keys=keys, include_keys_meta=meta)


_ALL = planted_run().decisions
# Three decisions spanning both arms and both patients, so the subset cannot be
# satisfied by a prefix and cannot be a single patient -- the two degenerate
# shapes a selection bug produces.
_SUBSET = [_ALL[0], _ALL[2], _ALL[4]]
_SUBSET_KEYS = [d.key for d in _SUBSET]

# --- 9a -- NO FLAG, NO CHANGE, AND NO EMPTY DICT EITHER -------------------
check("9a  a run with no include list reports include_keys_meta None, not {} "
      "-- so 'full run' is distinguishable from 'subset, metadata lost'",
      (built(R.MODE_ANCHORED).include_keys_meta,
       built(R.MODE_BLIND).include_keys_meta), (None, None))
check("9a  and the fingerprint a state file records is None for a full run, "
      "which is what an old state file's absent key already reads as",
      fingerprint_of(built(R.MODE_BLIND)), None)

# --- 9b -- THE FULL SET THROUGH THE SUBSET PATH IS BYTE-IDENTICAL ---------
# The equivalence that matters: naming EVERY key must produce the same wire
# bytes as not passing the flag at all. If it does not, the subset path has
# reordered or re-shaped requests and every subset run is measuring something
# with a different request body from the full run it is compared against.
_all_keys = [d.key for d in _ALL]
check("9b  an include list naming every decision serialises byte-identically "
      "to the same run with no flag (blind)",
      blob(built_with(_all_keys)) == blob(built(R.MODE_BLIND)), True)
check("9b  and anchored likewise",
      blob(built_with(_all_keys, mode=R.MODE_ANCHORED))
      == blob(built(R.MODE_ANCHORED)), True)
# THE CONTROL. Without it, 9b would also pass for a filter that ignored its
# argument and always selected everything -- which is the one bug that makes
# every subset run silently rate the whole corpus and pay for it.
check("9b  CONTROL: a proper subset does NOT serialise identically, so the "
      "comparison above can fail",
      blob(built_with(_SUBSET_KEYS)) == blob(built(R.MODE_BLIND)), False)

# --- 9c -- THE REQUEST COUNT IS THE COUNT ASKED FOR ----------------------
_sub = built_with(_SUBSET_KEYS)
check("9c  the subset sends exactly one request per key",
      n_requests(_sub), len(_SUBSET_KEYS))
check("9c  and the run is bigger than the subset, so the count is not "
      "trivially the whole run",
      (len(_ALL) > len(_SUBSET_KEYS), len(_ALL)), (True, 7))
check("9c  every request maps to a requested key and nothing else",
      sorted(keys_of(_sub)), sorted(_SUBSET_KEYS))
# Patients and arms are read off the guarded key list rather than off
# ``_sub.requests``, so a revert that makes build_requests REFUSE (r5 makes the
# reconciliation fire) records failures here instead of killing the file. The
# revert harness caught this shape in the first version of this section.
check("9c  the subset spans both patients and both arms, so a selection that "
      "collapsed to one of either would be visible",
      (len({k[0] for k in keys_of(_sub)}) if isinstance(keys_of(_sub), list)
       else keys_of(_sub),
       len({k[2] for k in keys_of(_sub)}) if isinstance(keys_of(_sub), list)
       else keys_of(_sub)),
      (2, 2))

# --- 9d -- ORDER IS THE RUN'S, NEVER THE FILE'S --------------------------
# Request order must be a property of the run. If it followed the file, two
# derivation scripts sorting their output differently would produce different
# cache locality and different bytes for the same measurement.
_reversed_keys = list(reversed(_SUBSET_KEYS))
check("9d  reversing the file's key order changes no byte of the request list",
      blob(built_with(_reversed_keys)) == blob(built_with(_SUBSET_KEYS)), True)
check("9d  CONTROL: the two key lists really are in different orders",
      _reversed_keys != _SUBSET_KEYS, True)
# THE EXPECTATION IS THE RUN'S OWN SEQUENCE, NOT A SORT OF IT, and the first
# version of this line got that wrong: it re-derived the order with
# ``load_run``'s (patient_index, nct, arm, index) sort key and failed, because
# the planted run is deliberately NOT sorted -- ``planted_run`` returns the
# literals in the order ``_PLANT`` lists them. Reading a real run would have
# hidden the mistake, since there the two agree. The property ``build_requests``
# actually promises in its own comment is weaker and better: selection owns the
# order and the request loop preserves it, so the subset must be the
# order-preserving SUBSEQUENCE of whatever the caller supplied.
check("9d  and the selected order is the run's own sequence, preserved as a "
      "subsequence rather than re-derived by a sort",
      keys_of(_sub), [d.key for d in _ALL if d.key in set(_SUBSET_KEYS)])
check("9d  CONTROL: that expectation is not the same as load_run's sort key, "
      "so the check above is not satisfied by either order",
      [d.key for d in _ALL if d.key in set(_SUBSET_KEYS)]
      == sorted(_SUBSET_KEYS,
                key=lambda k: (_ORDER[k[0]], k[1], k[2], k[3])), False)

# --- 9e -- THE JOIN, ON THE SUBSET, LOSSLESSLY ---------------------------
# The whole point of the key vocabulary. Every custom_id must decode back to
# the decision it was built from, and the ids must be distinct.
_by_ord = {v: k for k, v in _ORDER.items()}
_sub_cids = cids_of(_sub)
check("9e  every subset custom_id round-trips to its own key",
      drive(lambda: [R.decode_custom_id(c, _sub.form, _by_ord)
                     for c in _sub_cids]), keys_of(_sub))
check("9e  and the ids are distinct",
      len(set(_sub_cids)) if isinstance(_sub_cids, list) else _sub_cids,
      n_requests(_sub))
check("9e  a retest duplicate on a subset still joins onto its primary's key, "
      "so the intra-rater pairing survives the filter",
      drive(lambda: all(
          R.decode_custom_id(R.encode_retest_custom_id(c), _sub.form, _by_ord)
          == R.decode_custom_id(c, _sub.form, _by_ord)
          for c in _sub_cids)), True)
_sub_retest = drive(
    R.build_requests, planted_run(),
    drive(R.build_system_prompt, _FIXED_RUBRIC, mode=R.MODE_BLIND),
    {"rubric_sha256": "x"}, _MODEL, 300, 0.0, "1h", mode=R.MODE_BLIND,
    arm_definitions=drive(R.lift_arm_status_definitions, _FIXED_RUBRIC),
    retest_fraction=1.0, include_keys=_SUBSET_KEYS)
check("9e  --retest-fraction on a subset duplicates only subset decisions, "
      "so the intra-rater measurement is over the population being rated",
      (n_requests(_sub_retest), len(getattr(_sub_retest, "retest_ids", ())),
       sorted({_sub_retest.by_custom_id[c].key
               for c in getattr(_sub_retest, "retest_ids", ())})),
      (len(_SUBSET_KEYS) * 2, len(_SUBSET_KEYS), sorted(_SUBSET_KEYS)))

# --- 9f -- UNKNOWN KEYS ARE A REFUSAL, NOT A PARTIAL INTERSECTION --------
_ghost = ("pZ", "NCT09999999", "inclusion", 0)
check("9f  a key naming no decision in the run refuses by its own slug",
      refusal_code(R.select_included_decisions, _ALL,
                   _SUBSET_KEYS + [_ghost]), "include_keys_unmatched")
check("9f  CONTROL: the same call without the ghost key does not refuse",
      refusal_code(R.select_included_decisions, _ALL, _SUBSET_KEYS),
      "<did not raise>")
check("9f  an EMPTY intersection refuses too -- the case that would otherwise "
      "rate nothing, spend nothing and exit 0",
      refusal_code(R.select_included_decisions, _ALL, [_ghost]),
      "include_keys_unmatched")
check("9f  the message names the unmatched key and how many, so a derivation "
      "script can be fixed in one pass",
      all(part in str(drive(lambda: R.select_included_decisions(
          _ALL, _SUBSET_KEYS + [_ghost])))
          for part in ("1 of 4", "pZ", "NCT09999999")), True)
# The near-miss that a looser matcher would swallow: the RIGHT patient, trial
# and arm at the WRONG index. This is the shape a positional-vocabulary
# mismatch produces, and it must refuse rather than silently rate a neighbour.
_off_by_one = (_ALL[0].patient_id, _ALL[0].nct_id, _ALL[0].arm,
               _ALL[0].index + 99)
check("9f  the right (patient, trial, arm) at a wrong index refuses -- a "
       "near-miss is not a match",
      refusal_code(R.select_included_decisions, _ALL, [_off_by_one]),
      "include_keys_unmatched")
check("9f  an arm swapped for the other arm's name refuses, not matches the "
      "same index in the other arm",
      refusal_code(R.select_included_decisions, _ALL,
                   [(_ALL[3].patient_id, _ALL[3].nct_id, "exclusion",
                     _ALL[3].index)]),
      "include_keys_unmatched")

# --- 9g -- THE FILE FORMAT, AND EVERY WAY IT CAN BE WRONG ----------------
_good = "%s|NCT00000001|inclusion|0" % _ALL[0].patient_id
check("9g  one key per line, four pipe-separated fields",
      drive(R.parse_include_keys, _good + "\n"),
      [(_ALL[0].patient_id, "NCT00000001", "inclusion", 0)])
check("9g  comments and blank lines are skipped, so a derivation script can "
      "stamp its provenance into the artifact",
      drive(R.parse_include_keys,
            "# provenance: derived from tlib9.is_temporal\n"
            "\n   \n" + _good + "   # trailing note\n"),
      [(_ALL[0].patient_id, "NCT00000001", "inclusion", 0)])
check("9g  file order is preserved in the parse (the manifest records what "
      "was ASKED for; selection re-orders onto the run)",
      drive(R.parse_include_keys,
            "pB|NCT3|exclusion|1\npA|NCT1|inclusion|0\n"),
      [("pB", "NCT3", "exclusion", 1), ("pA", "NCT1", "inclusion", 0)])
for _label, _text, _slug in (
        ("three fields", "pA|NCT1|inclusion\n", "include_key_malformed"),
        ("five fields", "pA|NCT1|inclusion|0|extra\n",
         "include_key_malformed"),
        ("a custom_id instead of a key", "pA_NCT00000001_inclusion_0\n",
         "include_key_malformed"),
        ("empty patient_id", "|NCT1|inclusion|0\n", "include_key_malformed"),
        ("empty nct_id", "pA||inclusion|0\n", "include_key_malformed"),
        ("an arm outside the vocabulary", "pA|NCT1|inclusion_criteria|0\n",
         "include_key_malformed"),
        ("a capitalised arm", "pA|NCT1|Inclusion|0\n",
         "include_key_malformed"),
        ("a non-integer index", "pA|NCT1|inclusion|first\n",
         "include_key_malformed"),
        ("a negative index", "pA|NCT1|inclusion|-1\n",
         "include_key_malformed"),
        ("a float index", "pA|NCT1|inclusion|0.0\n",
         "include_key_malformed"),
        ("a padded index that is not its own str()",
         "pA|NCT1|inclusion|00\n", "include_key_malformed"),
        ("an empty file", "", "include_keys_empty"),
        ("a file of only comments and blanks", "# nothing\n\n   \n",
         "include_keys_empty"),
        ("the same key twice", _good + "\n" + _good + "\n",
         "include_key_duplicate")):
    check(f"9g  refused: {_label}",
          refusal_code(R.parse_include_keys, _text, "f"), _slug)
check("9g  CONTROL: two DIFFERENT keys are not a duplicate",
      len(drive(R.parse_include_keys,
                "pA|NCT1|inclusion|0\npA|NCT1|inclusion|1\n")), 2)
check("9g  the malformed message names the line number, so the file is "
      "editable without re-deriving it",
      "f:3" in str(drive(lambda: R.parse_include_keys(
          "# c\n" + _good + "\npA|NCT1|inclusion\n", "f"))), True)
check("9g  the duplicate message names BOTH lines",
      all(w in str(drive(lambda: R.parse_include_keys(
          _good + "\n" + _good + "\n", "f")))
          for w in ("f:2", "line 1")), True)

# --- 9h -- THE FILE ON DISK, ITS HASH, AND THE RECONCILIATION -----------
_tmp9 = _tempfile9.mkdtemp(prefix="rater_include_")
try:
    _p_ok = _os9.path.join(_tmp9, "subset.txt")
    with open(_p_ok, "w", encoding="utf-8") as _fh:
        _fh.write(keyfile_text(_SUBSET))
    _keys9, _meta9 = drive(R.load_include_keys_file, _p_ok)
    check("9h  the file parses to the keys it names", _keys9, _SUBSET_KEYS)
    check("9h  the metadata carries the absolute path, the requested count "
          "and the format, so the manifest records a re-readable artifact",
          (_meta9["path"], _meta9["keys_requested"], _meta9["format"]),
          (_p_ok, 3, "patient_id|nct_id|arm|index"))
    check("9h  the sha256 is over the file's raw bytes",
          _meta9["sha256"], sha(keyfile_text(_SUBSET)))
    # THE HASH IS OVER BYTES, NOT OVER THE PARSED KEY SET, and that is the
    # point: a resume must refuse a file that has been re-derived even if the
    # two happen to name the same decisions, because "same set" is exactly the
    # thing the operator cannot check by eye.
    _p_same = _os9.path.join(_tmp9, "subset_recomment.txt")
    with open(_p_same, "w", encoding="utf-8") as _fh:
        _fh.write(keyfile_text(_SUBSET, header=False))
    check("9h  a file naming the same keys with different bytes hashes "
          "differently, and still parses to the same keys",
          (drive(R.load_include_keys_file, _p_same)[1]["sha256"]
           == _meta9["sha256"],
           drive(R.load_include_keys_file, _p_same)[0] == _keys9),
          (False, True))
    _p_missing = _os9.path.join(_tmp9, "not_there.txt")
    check("9h  a nonexistent file refuses before anything is priced",
          refusal_code(R.load_include_keys_file, _p_missing),
          "include_keys_absent")
    _p_empty = _os9.path.join(_tmp9, "empty.txt")
    with open(_p_empty, "w", encoding="utf-8") as _fh:
        _fh.write("# derived nothing\n")
    check("9h  an empty file on disk refuses by the empty slug, not the "
          "absent one",
          refusal_code(R.load_include_keys_file, _p_empty),
          "include_keys_empty")

    _sub9 = built_with(_keys9, meta=_meta9)
    check("9h  the index's subset metadata reconciles: keys requested == "
          "decisions selected == requests sent",
          (meta_of(_sub9, "keys_requested"),
           meta_of(_sub9, "decisions_selected"),
           n_requests(_sub9)), (3, 3, 3))
    check("9h  and it records the run's own size and the share, so a reader "
          "of summary.json knows what population the rates are over",
          (meta_of(_sub9, "decisions_in_run"),
           meta_of(_sub9, "patients_covered"),
           rounded(meta_of(_sub9, "share_of_run"), 4)),
          (7, 2, round(3 / 7.0, 4)))
    check("9h  the fingerprint a state file records is the file's sha",
          fingerprint_of(_sub9), _meta9["sha256"])
finally:
    _shutil9.rmtree(_tmp9, ignore_errors=True)
check("9h  the temp directory is removed", _os9.path.exists(_tmp9), False)

# --- 9i -- --limit AND --include-keys CANNOT BE COMBINED ----------------
check("9i  build_requests refuses the combination by its own slug",
      refusal_code(
          R.build_requests, planted_run(),
          drive(R.build_system_prompt, _FIXED_RUBRIC, mode=R.MODE_ANCHORED),
          {"rubric_sha256": "x"}, _MODEL, 300, 0.0, "1h", limit=3,
          include_keys=_SUBSET_KEYS), "include_keys_with_limit")
check("9i  CONTROL: the same call with limit=0 does not refuse",
      refusal_code(
          R.build_requests, planted_run(),
          drive(R.build_system_prompt, _FIXED_RUBRIC, mode=R.MODE_ANCHORED),
          {"rubric_sha256": "x"}, _MODEL, 300, 0.0, "1h", limit=0,
          include_keys=_SUBSET_KEYS), "<did not raise>")
# limit=6, not 5: the planted run holds six (arm, status) cells and the smoke
# selector rightly refuses a budget that cannot span them. The first version of
# this line passed 5 and killed the run -- see ``n_requests`` above.
check("9i  CONTROL: --limit alone still works, so the guard has not disabled "
      "the smoke path",
      n_requests(drive(R.build_requests, planted_run(),
                       drive(R.build_system_prompt, _FIXED_RUBRIC,
                             mode=R.MODE_ANCHORED),
                       {"rubric_sha256": "x"}, _MODEL, 300, 0.0, "1h",
                       limit=6)), 6)

# --- 9j -- THE CLI, AND THE ORDERING OF ITS REFUSALS -------------------
check("9j  --include-keys is a real argument and defaults off",
      (drive(R._parse_args, ["--dry-run", "--include-keys", "k.txt"]
             ).include_keys,
       drive(R._parse_args, ["--dry-run"]).include_keys), ("k.txt", None))
# The slug, not merely the raise, and for the reason section 8q states: on a
# machine with no evaluation-run directory _prepare would raise
# RaterRefusal("run_dir_invalid") anyway, so asserting only that it raised
# would pass for the wrong reason and make this file depend on the corpus.
check("9j  --include-keys with --limit is refused by _prepare BEFORE any run "
      "directory is resolved",
      refusal_code(R._prepare, drive(
          R._parse_args, ["--dry-run", "--include-keys", "k.txt",
                          "--limit", "5"])), "include_keys_with_limit")
check("9j  a nonexistent include file is refused before any run directory is "
      "resolved too",
      refusal_code(R._prepare, drive(
          R._parse_args, ["--dry-run", "--include-keys",
                          "/nonexistent/rater-include-keys.txt"])),
      "include_keys_absent")

# --- 9k -- THE STATE FILE CANNOT BE READ ACROSS SUBSETS ----------------
# collect_results already refuses a returned custom_id absent from the rebuilt
# index, which catches resuming a subset batch against a WIDER index. The
# reverse -- resuming a subset batch with the flag FORGOTTEN -- is not caught
# there: every returned id IS in the full index, the join succeeds, and the
# thousands never submitted come back as no_result. That is what this guard is.
_full_idx = built(R.MODE_BLIND)
_sub_idx = built_with(_SUBSET_KEYS)
_SUB_SHA = "a" * 64
check("9k  a subset state file resumed with the flag forgotten refuses",
      refusal_code(R.require_state_subset, {"include_keys_sha256": _SUB_SHA},
                   _full_idx, "/s"), "state_subset_mismatch")
# Built ONCE and checked to be an index before it is used three times: under a
# revert that makes build_requests refuse, ``_sub_sha_idx`` is a marker string
# and require_state_subset would raise AttributeError instead of recording
# three failures. The probe is what turns that into a named failure.
_sub_sha_idx = built_with(_SUBSET_KEYS, meta={"sha256": _SUB_SHA})
check("9k  the subset index under test really is an index (probe, so the three "
      "checks below cannot pass or abort for the wrong reason)",
      fingerprint_of(_sub_sha_idx), _SUB_SHA)
check("9k  and a full-run state file resumed WITH a subset refuses",
      refusal_code(R.require_state_subset, {"mode": "blind"},
                   _sub_sha_idx, "/s"), "state_subset_mismatch")
check("9k  and a DIFFERENT subset refuses",
      refusal_code(R.require_state_subset, {"include_keys_sha256": "b" * 64},
                   _sub_sha_idx, "/s"), "state_subset_mismatch")
check("9k  CONTROL: the matching subset does not refuse",
      refusal_code(R.require_state_subset, {"include_keys_sha256": _SUB_SHA},
                   _sub_sha_idx, "/s"), "<did not raise>")
# BACKWARD COMPATIBILITY, ASSERTED. Every state file on disk predates this flag
# and carries no such key. .get() returns None, which is also what a full run
# fingerprints to -- so an old file resumes rather than refusing. If that ever
# stops holding, every existing rater_state.json becomes unresumable.
check("9k  CONTROL: a state file predating the flag resumes a full run",
      refusal_code(R.require_state_subset, {"mode": "blind",
                                            "requests": 2401},
                   _full_idx, "/s"), "<did not raise>")
check("9k  CONTROL: no state file at all is not a refusal",
      refusal_code(R.require_state_subset, {}, _sub_idx, "/s"),
      "<did not raise>")
check("9k  the message names both populations and the fix",
      all(w in str(drive(lambda: R.require_state_subset(
          {"include_keys_sha256": _SUB_SHA}, _full_idx, "/s")))
          for w in ("whole run", _SUB_SHA[:12], "--output-dir")), True)

# --- 9l -- THE CACHE TTL DEFAULT -------------------------------------
# NOT A STYLE ASSERTION. The 1.8.0 blind run breached its $13.00 gate at
# $13.5831, and 88.9% of the overrun was cache WRITE tokens at the 1h premium
# (2.0x base against 1.25x at 5m): 2,463,401 write tokens cost $7.39 where 5m
# would have cost $4.62, landing the run at $10.81 under the gate. The default
# is the whole fix, so it is pinned with the reason beside it.
check("9l  the default cache TTL is 5m", R.DEFAULT_CACHE_TTL, "5m")
check("9l  and an unmodified invocation carries it onto the request",
      drive(R._parse_args, ["--dry-run"]).cache_ttl, "5m")
check("9l  1h is still reachable for a batch expected to run long",
      drive(R._parse_args, ["--dry-run", "--cache-ttl", "1h"]).cache_ttl, "1h")
check("9l  the ttl reaches every cache_control block on the wire, both the "
      "system prompt and the patient record",
      ttls_of(built_with(_SUBSET_KEYS)), ["1h"])
_five = drive(
    R.build_requests, planted_run(),
    drive(R.build_system_prompt, _FIXED_RUBRIC, mode=R.MODE_ANCHORED),
    {"rubric_sha256": "x"}, _MODEL, 300, 0.0, "5m")
check("9l  CONTROL: built at 5m, every block says 5m -- so the check above "
      "reads the argument rather than a constant",
      ttls_of(_five), ["5m"])
# The estimator prices a write into the ttl-specific bucket, and the two rates
# differ. A default change that did not reach the estimate would leave every
# dry run quoting the old premium.
_est5 = drive(R.estimate_tokens, _five, planted_run(), 4.0, "5m")
_est1 = drive(R.estimate_tokens, built_with(_SUBSET_KEYS), planted_run(),
              4.0, "1h")
check("9l  the estimate books cache writes into the ttl's own bucket",
      (has(_est5, "full_cache", "cache_creation_5m"),
       has(_est5, "full_cache", "cache_creation_1h"),
       has(_est1, "full_cache", "cache_creation_1h")), (True, False, True))
_rates9 = drive(R.rater_pricing, _MODEL)
check("9l  and a 5m write really is cheaper than a 1h write at these rates, "
      "which is the only reason the default moved",
      drive(lambda: dig(_rates9, "cache_write_5m")
            < dig(_rates9, "cache_write_1h")), True)

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
