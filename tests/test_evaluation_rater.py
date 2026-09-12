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

  9  THE RESUME PATH'S PROVENANCE GUARDS. Five of them, and each answers a
     different "what produced the answers this resume is about to join":
     ``require_state_for_resume`` (9o -- a batch id with no state file records
     nothing, and made the other four fall silent), ``require_state_mode``,
     ``require_state_subset`` (9k), ``require_state_shape`` (9k) and
     ``require_state_rubric`` (9p -- a field written since this module's first
     commit and read back by nothing). 9q is the one section in this file that
     drives ``main()``, and it exists because a guard that is never REACHED
     passes every check written about the guard: it requires each refusal to
     fire with ZERO outbound network attempts, which they did not before the
     block was moved above the visibility check.

NO NETWORK, AND IT IS MEASURED RATHER THAN CLAIMED. Section 9q replaces
``socket.socket.connect``, ``connect_ex``, ``socket.create_connection`` and
``socket.getaddrinfo`` with a recorder that RAISES, arms it around each
``main()`` drive, disarms it in a ``finally``, and asserts the disarm. A firing
CONTROL runs first -- a real outbound call, blocked and recorded -- so a zero
attempt count is a measurement rather than an absence of instrumentation. Every
other section in this file touches no socket at all.

NO SPEND, NO DATABASE, NO GIT HISTORY. Every decision, response and usage
object outside section 9q is a literal built in this file.

TWO CLAIMS THIS FILE USED TO MAKE ARE NARROWER THAN THEY READ, and both are
section 9q's doing. "NO KEYS": 9q's non-degeneracy control puts a FABRICATED
``OPENAI_API_KEY`` in ``os.environ`` for one drive, inside a ``try``/``finally``
that restores whatever was there -- it has to, because the property being
measured is that the local guards run ABOVE ``require_client``, and an
invocation with no key at all cannot distinguish "refused by a guard" from
"refused for want of a credential". "THE EVALUATION RUN DIRECTORIES ARE NEVER
READ" is still true of the PROJECT's -- ``default_run_dir()`` is never called --
but 9q fabricates its own minimal run directory under ``tempfile.mkdtemp`` and
points ``--run-dir`` at it, because ``main()`` cannot be driven without one.

IT WRITES NOTHING IN THE REPOSITORY, and three sections write anything at all:
8p2 (two state files), 9o (one deliberately unreadable state file) and 9q (a
fabricated run directory, an output directory and several state files). All
three use a fresh ``tempfile.mkdtemp``, remove it in a ``finally``, and then
ASSERT the removal. None can be in-memory: ``refuse_batch_from_other_mode``
exists to read a state file off disk, ``read_state``'s decode-failure branch
needs a real malformed file, and ``main()`` resolves paths.
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

import _provider_pin                                             # noqa: E402

# EXPLICIT TEST QUOTA LIMITS, AND NO PROVIDER PIN -- see
# `tests/_provider_pin.test_quotas_only` for why the two are separable and why
# this file must NOT pin an arm. It drives the real `collect_results` and
# `submit_batches`, whose management calls are paced under
# `config.PROVIDER_QUOTA_SCOPE_OPENAI_BATCH`; the shipped config leaves that
# scope's requests/minute UNKNOWN, which REFUSES before the send.
# THE NAME IS A LITERAL, NOT `os.path.basename(__file__)`, AND THAT IS THIS
# FILE'S OWN CONVENTION RATHER THAN A SHORTCUT. Unlike its siblings this module
# binds NO module-scope `os` -- the bootstrap imports it as `_os` and each later
# section takes its own alias (`_osmod`, `_os9`, `_os9k`) -- so the usual
# expression raises `NameError` here and aborts the file before its first
# check. MEASURED: it did, at this line, and the run reported no summary at all.
_PIN_WHO = "test_evaluation_rater.py"
_provider_pin.test_quotas_only(_PIN_WHO)


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


def walk(value, *keys, default="<absent>"):
    """Walk into a ``drive`` result without ever raising.

    ``drive`` protects the CALL; a subscript on its marker string, or on a
    legitimately-absent block, aborts the file just as surely. Every
    ``drive(...)[k]`` in this file goes through here.
    """
    cur = value
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k, default)
        elif isinstance(cur, (list, tuple)) and isinstance(k, int):
            cur = cur[k] if -len(cur) <= k < len(cur) else default
        else:
            return default
    return cur


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


# --- 1z -- THE EMPTY MATRIX RETURNS THE SAME RECORD SHAPE ----------------
#
# FOUND BY A REAL PAID RUN, NOT BY READING. The `n == 0` branch omitted three
# keys the full return carries while still returning a non-empty `categories`,
# and `print_summary` iterates `categories` and subscripts `pipeline_counts`
# unconditionally -- so a single-armed population (the criteria-reference
# probe's five decisions are all exclusions, leaving the inclusion arm empty)
# crashed the console report AFTER all three artifacts had been written.
_EMPTY = drive(R.cohens_kappa, [[0, 0], [0, 0]], ("a", "b"))
_FULL = drive(R.cohens_kappa, [[1, 0], [0, 1]], ("a", "b"))
check("1z  the empty-matrix record has the SAME key set as a populated one, "
      "so a consumer can read it by key",
      sorted(_EMPTY) if isinstance(_EMPTY, dict) else _EMPTY,
      sorted(_FULL) if isinstance(_FULL, dict) else _FULL)
check("1z  non-degeneracy: that key set is not empty",
      len(_FULL) > 8 if isinstance(_FULL, dict) else False, True)
check("1z  ...and the counts it reports are zeros over the SAME categories, "
      "never an empty dict a caller would KeyError on",
      (_EMPTY.get("pipeline_counts"), _EMPTY.get("rater_counts"))
      if isinstance(_EMPTY, dict) else _EMPTY,
      ({"a": 0, "b": 0}, {"a": 0, "b": 0}))
check("1z  ...and every category it names is in those counts, which is the "
      "exact pairing print_summary walks",
      all(c in (_EMPTY.get("pipeline_counts") or {})
          and c in (_EMPTY.get("rater_counts") or {})
          for c in (_EMPTY.get("categories") or []))
      if isinstance(_EMPTY, dict) else False, True)
check("1z  CONTROL: the crash is reproducible from the pre-fix shape -- a "
      "record missing the key, walked the way print_summary walks it",
      "KeyError" in str(drive(
          lambda rec: [rec["pipeline_counts"][c] for c in rec["categories"]],
          {"categories": ["a"], "pipeline_counts": {}})), True)
check("1z  ...and the same walk over the SHIPPED empty record does not raise",
      drive(lambda rec: [rec["pipeline_counts"][c] for c in rec["categories"]],
            _EMPTY), [0, 0])


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


# ── THE STUB IS A BATCH OUTPUT FILE NOW, NOT A RESULT ITERATOR ───────────
#
# Anthropic streamed typed result objects out of `messages.batches.results`;
# the OpenAI Batch API writes a JSONL FILE and hands back its id, so the stub
# below is a two-endpoint fake: `batches.retrieve` returns a status and a file
# id, and `files.content` returns the bytes. Every response is a plain DICT,
# because that is what the harness parses -- a SimpleNamespace stub would let a
# `getattr`-based reader pass while the real, dict-shaped path reported every
# response as free.
#
# NO NETWORK, NO KEYS, NO SPEND. Every byte here is a literal.
_JUDGE = "gpt-5.6-terra"


def _usage(inp=100, out=90, cached=5000, reasoning=0, write=None):
    """An OpenAI usage block. `inp` is UNCACHED; prompt_tokens is the sum."""
    block = {"prompt_tokens": inp + cached, "completion_tokens": out,
             "total_tokens": inp + cached + out,
             "prompt_tokens_details": {"cached_tokens": cached},
             "completion_tokens_details": {"reasoning_tokens": reasoning}}
    if write is not None:
        block["prompt_tokens_details"]["cache_creation_tokens"] = write
    return block


def _message(text, stop="stop", refusal=None, model=None, usage=None):
    """One `response.body`: a ChatCompletion, as JSON."""
    return {"id": "chatcmpl-stub", "object": "chat.completion",
            "model": model or _JUDGE,
            "usage": _usage() if usage is None else usage,
            "choices": [{"index": 0, "finish_reason": stop,
                         "message": {"role": "assistant", "content": text,
                                     "refusal": refusal}}]}


def _ok(body):
    return {"response": {"status_code": 200, "request_id": "req_stub",
                         "body": body}, "error": None}


def _errored(kind, code=500):
    return {"response": {"status_code": code, "request_id": "req_stub",
                         "body": {"error": {"type": kind, "message": kind}}},
            "error": {"code": kind, "message": kind}}


class _StubClient(object):
    """Serves one canned batch output file. Never touches the network."""

    def __init__(self, plan, status="completed", output=True, error_plan=None):
        outer = self
        self.plan = plan
        self.status = status
        self.error_plan = error_plan or []
        self.reads = []

        def _lines(rows):
            return "".join(
                json.dumps(dict(result, custom_id=cid), ensure_ascii=False)
                + "\n" for cid, result in rows)

        class _Batches(object):
            def retrieve(self, batch_id):
                return types.SimpleNamespace(
                    id=batch_id, status=outer.status,
                    output_file_id=("out_file" if output else None),
                    error_file_id=("err_file" if outer.error_plan else None),
                    request_counts=types.SimpleNamespace(
                        total=len(outer.plan), completed=len(outer.plan),
                        failed=0))

        class _Files(object):
            def content(self, file_id):
                outer.reads.append(file_id)
                if file_id == "err_file":
                    return _lines(outer.error_plan)
                return _lines(outer.plan)

        self.batches = _Batches()
        self.files = _Files()


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
    # A REFUSAL IS ITS OWN FIELD ON THIS VENDOR, not a finish_reason. The old
    # plan set stop="refusal", which is Anthropic's shape; here the content is
    # null and `message.refusal` carries the text.
    (_CIDS[2], _ok(_message(None, refusal="I cannot help with that"))),
    (_CIDS[3], _ok(_message(OK[:30], stop="length"))),
    (_CIDS[4], _errored("server_error")),
    # _CIDS[5] deliberately omitted -> must surface as an absence, not vanish.
]
got = drive(R.collect_results, _StubClient(_PLAN), "batch_stub", _INDEX,
            _JUDGE)
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
                                 _errored("invalid_request_error", 400))]),
                   "b", _INDEX, _JUDGE), _CIDS[0]),
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
                   _INDEX, _JUDGE)
check("5d  a duplicate custom_id refuses", (did, kind),
      (True, "RaterRefusal"))
did, kind = raises(R.collect_results,
                   _StubClient([("no_such_custom_id", _ok(_message(OK)))]),
                   "b", _INDEX, _JUDGE)
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

# --- 7d''' -- 1.11.0 SPLITS ACROSS THE BOUNDARY THREE WAYS -----------
#
# The bump made three operator-approved changes and they reach different
# audiences, so the boundary is pinned for each of them IN BOTH DIRECTIONS --
# "the rubric contains X" alone is satisfied by a span widened to swallow the
# prompt, "the rubric omits Y" alone by a lift that had stopped working.
#
# (a) RULE 4's closing paragraph. INSIDE `evaluation_rules`, and required
#     rather than tidy: the A2 population this bump answers was measured WITH a
#     rater, and a rater still holding 1.10.0's rule would score the fix's own
#     non-disqualifying answer as a defect -- disagreeing for rubric mismatch
#     rather than for decision quality, which is the confound this harness
#     exists to remove.
# (b) RULE 2's `status: unknown` routing. INSIDE, for the same reason: the rater
#     judges medication criteria and must reach the same arm.
# (c) The patient_value evidence-trail sentence. OUTSIDE, on the standing rule
#     that Section 5 describes the pipeline's OUTPUT ENVELOPE and the rater has
#     its own -- the same reason C6 is restated rather than lifted.
#
# The Section 5 and FINAL REMINDER restatements of (a) are OUTSIDE too, on
# 1.7.0's precedent, and their sentence is a SUBSTRING of the RULE 4 paragraph
# -- so it is pinned by OCCURRENCE COUNT rather than by presence: exactly one,
# the one inside the rule. A count is what distinguishes "the rubric carries the
# rule" from "a span drifted over the reminder as well".
_R111_RULE4 = ("For a criterion about current disease, do not treat resolved or "
               "inactive disease as active.")
_R111_TAIL = ("Apply the existing missing-evidence rule when current status "
              "cannot be established.")
_R111_RULE2 = ("A medication line reading `status: unknown` provides no "
               "evidence by itself")
_R111_OUT = ("When the evidence used for this verdict explicitly states a "
             "clinical status, preserve that status in `patient_value`")
check("7d''' non-degeneracy: all four 1.11.0 sentences ARE in the rendered "
      "prompt, so the checks below are about the span boundary rather than "
      "about a template that lost them",
      (_R111_RULE4 in _ONE, _R111_TAIL in _ONE, _R111_RULE2 in _ONE,
       _R111_OUT in _ONE),
      (True, True, True, True))
check("7d''' 1.11.0's RULE 4 paragraph DOES reach the lifted rubric, so "
      "the rater judges a resolved quote under the classifier's own rule",
      _R111_RULE4 in _RUBRIC, True)
check("7d''' ...and so does RULE 2's `status: unknown` routing",
      _R111_RULE2 in _RUBRIC, True)
check("7d''' ...while the patient_value evidence-trail sentence does NOT, "
      "because Section 5 is the pipeline's output envelope and the rater has "
      "its own",
      _R111_OUT in _RUBRIC, False)
check("7d''' ...and the restatement sentence appears in the rubric EXACTLY "
      "ONCE -- the one inside RULE 4. The Section 5 and FINAL REMINDER copies "
      "are outside every span, so a second occurrence means a boundary drifted "
      "over one of them",
      _RUBRIC.count(_R111_TAIL), 1)
check("7d''' ...and it really occurs three times in the RENDERED prompt, "
      "so that count of one is a boundary measurement rather than a template "
      "that only ever had one copy",
      _ONE.count(_R111_TAIL), 3)
_R111_SPANS = [n for n, (s, e) in _SPAN_BY_NAME.items()
               if _R111_RULE4 in str(drive(R._slice_span, _ONE, s, e, n))]
check("7d''' ...and the paragraph lands in `evaluation_rules` specifically, "
      "not in a neighbour that drifted over it",
      _R111_SPANS, ["evaluation_rules"])
_R111_R2_SPANS = [n for n, (s, e) in _SPAN_BY_NAME.items()
                  if _R111_RULE2 in str(drive(R._slice_span, _ONE, s, e, n))]
check("7d''' ...and so does RULE 2's routing sentence",
      _R111_R2_SPANS, ["evaluation_rules"])

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


def built(mode, run=None, retest_fraction=0.0, seed=42, rubric=None,
          shape=None):
    """A RequestIndex over the planted run, or a marker string on a raise.

    ``shape`` defaults to whatever a run built today is, so every check that
    does not care follows the shipped shape. 8a passes
    ``REQUEST_SHAPE_HISTORICAL`` explicitly: a pin measured against history can
    only go on asking its question if history is still producible, and the
    system prompt and the request bodies must both be built at the shape or the
    module refuses them as disagreeing.
    """
    rubric = _FIXED_RUBRIC if rubric is None else rubric
    shape = R.REQUEST_SHAPE_VERSION if shape is None else shape
    defs = (drive(R.lift_arm_status_definitions, rubric)
            if mode == R.MODE_BLIND else None)
    if isinstance(defs, str):
        return defs
    return idx(drive(R.build_requests, run or planted_run(),
                     drive(R.build_system_prompt, rubric, mode=mode,
                           shape_version=shape),
                     {"rubric_sha256": "x"}, _MODEL, 300, None,
                     mode=mode, arm_definitions=defs,
                     retest_fraction=retest_fraction, retest_seed=seed,
                     structured_output=False, shape_version=shape))


class _RefusedIndex(object):
    """A stand-in for a ``RequestIndex`` that ``build_requests`` refused.

    ``built`` returns a marker STRING on a refusal, and a string has no
    ``.requests`` -- so a defect that makes the builder refuse aborts this file
    at module level while a ``check`` argument is being evaluated, reporting one
    traceback where it owes 434 results. That is the shape this project has
    shipped eighteen times; the revert matrix for the criteria-reference change
    found it here, where it became REACHABLE when ``build_requests`` learned to
    refuse a system prompt and a request shape that disagree.

    Every attribute is the empty answer, so every check below FAILS and names
    itself instead of vanishing.
    """

    def __init__(self, marker):
        self.marker = marker
        self.requests = []
        self.by_custom_id = {}
        self.retest_ids = set()
        self.primary_ids = set()
        self.retest_meta = {}
        self.include_keys_meta = None
        self.reference_by_custom_id = {}
        self.reference_meta = {}
        self.system_prompt = ""
        self.rubric_meta = {}
        self.mode = None
        self.form = None
        self.shape_version = None

    def __repr__(self):
        return f"<refused: {self.marker}>"


def idx(value):
    """A built index, or a stand-in that cannot abort the caller."""
    return value if hasattr(value, "requests") else _RefusedIndex(value)


def blob(index):
    """The serialized request list, exactly as it would go on the wire."""
    if not hasattr(index, "requests"):
        return "<no requests: %r>" % (index,)
    return json.dumps(index.requests, sort_keys=True, ensure_ascii=False)


def sha(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- 8a -- ANCHORED IS BYTE-IDENTICAL WITH THE FLAG OFF -------------------
#
# ** THE PIN SURVIVED THE PORT TO OPENAI, AND HOW IT SURVIVED IS THE POINT. **
#
# The value below was measured against `git show HEAD:` before blind mode
# existed, over the ANTHROPIC request body. The port to the OpenAI Batch API
# necessarily changes that body -- `system` becomes a message, `max_tokens`
# becomes `max_completion_tokens`, the `cache_control` breakpoints go away --
# so hashing the new list against the old value could only fail.
#
# THE OBVIOUS RESPONSE IS TO RE-MEASURE THE PIN AGAINST THE PORTED MODULE, AND
# IT IS THE WRONG ONE: a golden value refreshed to accommodate the change it is
# meant to guard makes whatever the code does correct by definition. This
# project's own rule, from `tests/test_dashboard_reproducibility_tab.py`.
#
# WHAT THE PIN WAS ACTUALLY ABOUT is the CONTENT and the ORDER: the system
# prompt text, each patient block, each decision block, the custom_ids and the
# sequence they are built in. None of that moved -- the port changed the
# envelope those strings travel in and nothing else. So the check reconstructs
# the ANTHROPIC envelope from the ported requests and hashes THAT against the
# original value. It fails if a single character of rendered text moved, if the
# order moved, or if a custom_id moved; it passes only because the port really
# was a transport change.
#
# `_as_anthropic_body` LIVES HERE AND NOT IN THE MODULE, deliberately. It is a
# statement about history, it has no production caller, and putting it in
# `rater.py` would be a dead declaration -- the shape
# `tests/test_package_invariants.py` check 2h exists to report.
_ANCHORED_PIN = \
    "bfab8d8257cfbbf4937cf275af4ba94b646a47edc54fcd7cbfda9a8ddeb15a5a"


def _as_anthropic_body(request):
    """One ported request, re-expressed in the pre-port wire shape.

    A pure re-envelope: it moves strings, it invents none. The `1h` ttl and the
    `temperature: 0.0` are what `built()` passed BEFORE the port and are
    restored here so the comparison is against the same historical request.
    """
    params = request["params"]
    system_text = params["messages"][0]["content"]
    user_parts = params["messages"][1]["content"]
    cc = {"type": "ephemeral", "ttl": "1h"}
    return {
        "custom_id": request["custom_id"],
        "params": {
            "model": params["model"],
            "max_tokens": params["max_completion_tokens"],
            "system": [{"type": "text", "text": system_text,
                        "cache_control": dict(cc)}],
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": user_parts[0]["text"],
                 "cache_control": dict(cc)},
                {"type": "text", "text": user_parts[1]["text"]},
            ]}],
            "temperature": 0.0,
        },
    }


_anch = built(R.MODE_ANCHORED, shape=R.REQUEST_SHAPE_HISTORICAL)
_anch_historical = ([_as_anthropic_body(r) for r in _anch.requests]
                    if hasattr(_anch, "requests") else [])
check("8a  the anchored requests' CONTENT and ORDER still hash to the value "
      "measured from the PRE-BLIND, PRE-PORT module (git show HEAD:), once "
      "the OpenAI envelope is translated back -- so the port moved the "
      "transport and not one character of what is asked",
      sha(json.dumps(_anch_historical, sort_keys=True, ensure_ascii=False)),
      _ANCHORED_PIN)
check("8a  non-degeneracy: the translation is over the PORTED body, so the "
      "check would fail if build_requests had stopped emitting one",
      bool(_anch_historical)
      and all("max_completion_tokens" in r["params"]
              for r in _anch.requests), True)
check("8a  CONTROL: the translation is not a no-op -- the ported and the "
      "historical shapes really differ",
      sha(blob(_anch)) != _ANCHORED_PIN, True)
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
_perturbed = json.loads(
    json.dumps(_anch_historical, sort_keys=True, ensure_ascii=False))
# GUARDED, AND THE REASON IS THE DEFECT THIS SHAPE HAS ALWAYS BEEN. A bare
# `_perturbed[0]` raises IndexError exactly when a defect has made `built()`
# refuse and return a marker -- which is when this file owes a summary and 433
# results, not a traceback. It became REACHABLE when `build_requests` learned
# to refuse a system prompt and a request shape that disagree; the shape was
# always here.
check("8a  CONTROL: non-degeneracy -- there is a request to perturb",
      bool(_perturbed), True)
if _perturbed:
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

# A DICT OR AN EMPTY ONE, never a marker string: `summarize` legitimately
# omits the blind-only keys when the mode is not blind, and a defect that makes
# `built` refuse produces exactly that -- so `_summary.get("confusion_counts")`
# raises KeyError and aborts the file. Every read below is `.get`.
_summary = drive(R.summarize, _bi2, _collected["rated"], {}, planted_run())
if not isinstance(_summary, dict):
    _summary = {"<refused>": _summary}
check("8n  the headline counts PRIMARIES ONLY -- a retested decision must not "
      "vote twice",
      (_summary.get("decisions_total"), _summary.get("decisions_rated")),
      (len(_PLANT), len(_PLANT)))
check("8n  hand-computed agreement: 5 of 7 primaries agree",
      (_summary.get("overall_agree"), _summary.get("overall_disagree")), (5, 2))
close("8n  ...so the rate is 5/7", _summary.get("overall_agreement_rate"), 5 / 7.0)
check("8n  hand-computed per-arm, per-status confusion counts, DIAGONAL "
      "INCLUDED",
      _summary.get("confusion_counts"),
      {"inclusion": {"met": {"met": 1, "not_met": 1},
                     "not_met": {"not_met": 1},
                     "not_evaluable": {"not_evaluable": 1}},
       "exclusion": {"not_violated": {"violated": 1},
                     "violated": {"violated": 1},
                     "not_evaluable": {"not_evaluable": 1}}})
check("8n  the summary says which mode produced it, and that a blind verdict "
      "is arithmetic",
      (_summary.get("mode"), "BLIND" in (_summary.get("anchoring") or "")),
      (R.MODE_BLIND, True))
check("8n  the residual leaks are stated IN THE OUTPUT, not only in a design "
      "note -- including the patient_value one",
      "patient_value_is_the_judged_model_s_own_extract"
      in (_summary.get("circularity_limitations") or {}), True)

# --- the residual leak is MEASURED, not described -------------------------
# Hand-computed over the plant: 1 extract begins "Not in patient record"
# (recorded not_evaluable), 1 begins "Not applicable" (recorded
# not_evaluable), and 5 are quoted data (met, met, not_met, not_violated,
# violated -> most common is "met" at 2 of 5). A guesser seeing only the class
# is right 1 + 1 + 2 = 4 of 7; the majority status over all 7 is "met" at 2,
# tied with not_evaluable at 2 -- Counter.most_common breaks the tie by
# insertion order, which is why the assertion below reads the rate rather than
# the label.
# A DICT OR AN EMPTY ONE, for `_summary`'s reason one level down: the
# blind-only block is legitimately absent whenever the mode is not blind.
_leak = _summary.get("patient_value_leak_measured") or {}
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
      {k: v["decisions"] for k, v in (_leak.get("marker_classes") or {}).items()},
      {"MARKER: not in patient record": 1, "MARKER: not applicable": 1,
       "quoted data": 5})
close("8n  ...and a guesser seeing ONLY the extract class scores 4/7",
      _leak.get("status_predictable_from_marker_class"), 4 / 7.0)
check("8n  the excess over the base rate is reported beside it, so a skewed "
      "corpus cannot make the leak look small by itself",
      _leak.get("excess_over_base_rate") is not None
      and abs(_leak.get("status_predictable_from_marker_class")
              - _leak.get("majority_status_base_rate")
              - _leak.get("excess_over_base_rate")) < 1e-12, True)
check("8n  the limitation prose quotes the measured size rather than only "
      "asserting 'correlates strongly'",
      "patient_value_leak_size_on_this_run"
      in (_summary.get("circularity_limitations") or {}), True)
check("8n  ...and the sentence names the leakiest class AND the excess, "
      "because either alone misleads in a different direction",
      all(s in ((_summary.get("circularity_limitations") or {}).get(
          "patient_value_leak_size_on_this_run") or "")
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
      "small" in ((_summary.get("circularity_limitations") or {}).get(
          "patient_value_leak_size_on_this_run") or ""), False)
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
_rt = _summary.get("retest") or {}
check("8o  one pair per retested decision, both copies rated",
      (_rt.get("duplicates_submitted"), _rt.get("pairs")),
      (len(_bi2.retest_ids), len(_bi2.retest_ids)))
check("8o  hand-computed: 6 of 7 pairs identical, 1 changed",
      (_rt.get("identical"), _rt.get("changed")), (6, 1))
close("8o  ...so intra-rater agreement is 6/7",
      _rt.get("intra_rater_agreement_rate"), 6 / 7.0)
check("8o  the decision that moved is named, with both answers",
      [(c["arm"], c["index"], c["first_assigned"], c["second_assigned"])
       for c in (_rt.get("changed_decisions") or [])],
      [("inclusion", 1, "not_met", "met")])
check("8o  it is reported SEPARATELY: the headline agreement is unchanged by "
      "the retest answers",
      (_summary.get("overall_agree"), _summary.get("overall_disagree")), (5, 2))
check("8o  a pair whose primary failed to parse is NOT counted as unstable",
      walk(drive(R.retest_report, _bi2,
                  {c: v for c, v in _collected["rated"].items()
                   if c != "pA_NCT00000001_inclusion_1"}), "pairs"),
      len(_bi2.retest_ids) - 1)
check("8o  ...and is reported as an incomplete pair instead",
      walk(drive(R.retest_report, _bi2,
                 {c: v for c, v in _collected["rated"].items()
                  if c != "pA_NCT00000001_inclusion_1"}),
           "pairs_incomplete", "only_retest_rated"), 1)
check("8o  with no retest requested the block says so rather than reporting a "
      "rate over zero pairs",
      walk(drive(R.retest_report, _anch, {}), "pairs"), 0)

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
                 {"rubric_sha256": "x"}, _MODEL, 300, None,
                 mode=mode, arm_definitions=defs, limit=limit,
                 include_keys=keys, include_keys_meta=meta,
                 structured_output=False)


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

# --- 9k -- ...AND NOT ACROSS REQUEST SHAPES ----------------------------
#
# The subset guard above asks WHICH POPULATION a resume joins onto. This asks
# WHICH INSTRUMENT produced the answers being joined, and nothing else can:
# primary custom_ids are identical across shapes, so a shape-1 batch resumed by
# shape-2 code joins cleanly, parses cleanly, reports no coverage hole and
# moves no counter -- and writes three artifacts all recording shape 2 over
# answers that were never shown a trial's criteria.
#
# MEASURED BEFORE THE GUARD WAS WRITTEN, AND PINNED HERE: with `mode`, the
# subset and the batch id all matching, `require_state_mode`,
# `require_state_subset` and `refuse_batch_from_other_mode` ALL pass on a
# shape-1 state resumed by shape-2 code. The first check below is those three,
# so the section states the gap it closes rather than only the closure.
import ast as _ast9k                                             # noqa: E402
import io as _io9k                                               # noqa: E402
import os as _os9k                                               # noqa: E402
import shutil as _shutil9k                                       # noqa: E402
import tempfile as _tempfile9k                                   # noqa: E402


def _fn9k(name):
    """``R.<name>``, or a stand-in that makes every check FAIL by name.

    ``refusal_code(R.require_state_shape, ...)`` looks the attribute up while
    ``check``'s ARGUMENTS are being evaluated, so a revert that deletes the
    guard raises there and aborts the file -- one traceback where it owes 463
    results. This project has shipped that shape eighteen times, and the revert
    matrix for this section found it here on its first run.
    """
    fn = getattr(R, name, None)
    if fn is not None:
        return fn

    def _absent(*_a, **_k):
        raise AttributeError("oncotriage.evaluation.rater has no %r" % name)

    return _absent


# Bound ONCE, defensively, for the same reason: a revert that deletes the
# constant must FAIL the checks below rather than abort them.
_SHAPE_KEY = getattr(R, "STATE_SHAPE_KEY", "<no STATE_SHAPE_KEY>")
_SHAPE_STATE = {"mode": R.MODE_BLIND, "include_keys_sha256": None,
                "batches": [{"id": "batch_shape", "tag": "primary"}]}
_shape_idx = built(R.MODE_BLIND)
check("9k  the index under test really is an index, built at the shipped "
      "shape (probe, so nothing below passes or aborts for the wrong reason)",
      getattr(_shape_idx, "shape_version", "<no attr>"),
      R.REQUEST_SHAPE_VERSION)
check("9k  the OTHER guards do not see a shape at all -- mode, subset and the "
      "cross-mode batch check all pass on a state submitted at another shape",
      tuple(refusal_code(fn, *args) for fn, args in (
          (R.require_state_mode, (dict(_SHAPE_STATE, request_shape_version=1),
                                  R.MODE_BLIND, "/s")),
          (R.require_state_subset, (dict(_SHAPE_STATE,
                                         request_shape_version=1),
                                    _shape_idx, "/s")),
          (R.refuse_batch_from_other_mode, (["batch_shape"], R.MODE_BLIND,
                                            "/nonexistent-out", None)))),
      ("<did not raise>",) * 3)

check("9k  a shape-1 state file resumed by shape-2 code refuses",
      refusal_code(_fn9k("require_state_shape"),
                   dict(_SHAPE_STATE, request_shape_version=1),
                   R.REQUEST_SHAPE_CRITERIA_REFERENCE, "/s"),
      "state_shape_mismatch")
check("9k  and the reverse -- a shape-2 state file resumed by shape-1 code",
      refusal_code(_fn9k("require_state_shape"),
                   dict(_SHAPE_STATE, request_shape_version=2),
                   R.REQUEST_SHAPE_HISTORICAL, "/s"),
      "state_shape_mismatch")
check("9k  a value outside REQUEST_SHAPES is a mismatch and names itself "
      "rather than raising KeyError on the notes table",
      refusal_code(_fn9k("require_state_shape"),
                   dict(_SHAPE_STATE, request_shape_version=99),
                   R.REQUEST_SHAPE_VERSION, "/s"),
      "state_shape_mismatch")
# `True == 1` and `1.0 == 1` are both True in Python, and this refusal invites a
# hand edit -- so a hand-editable file is exactly where a JSON `true` or `1.0`
# turns up. A bare `==` would ADOPT either as shape 1 and resume on a claim
# nobody made, which is the one thing the guard exists to prevent.
check("9k  a bool is NOT read as the shape it equals -- True would otherwise "
      "sail through as shape 1",
      refusal_code(_fn9k("require_state_shape"),
                   dict(_SHAPE_STATE, request_shape_version=True),
                   R.REQUEST_SHAPE_HISTORICAL, "/s"), "state_shape_mismatch")
check("9k  ...and neither is a float, for the same reason",
      refusal_code(_fn9k("require_state_shape"),
                   dict(_SHAPE_STATE, request_shape_version=1.0),
                   R.REQUEST_SHAPE_HISTORICAL, "/s"), "state_shape_mismatch")
check("9k  ...and a shape recorded as a STRING is a mismatch, not a match",
      refusal_code(_fn9k("require_state_shape"),
                   dict(_SHAPE_STATE, request_shape_version="2"),
                   R.REQUEST_SHAPE_CRITERIA_REFERENCE, "/s"),
      "state_shape_mismatch")
check("9k  non-degeneracy: those three really do compare equal to the shape "
      "they are refused against, so the checks above are about the TYPE test "
      "rather than about the value",
      (True == R.REQUEST_SHAPE_HISTORICAL, 1.0 == R.REQUEST_SHAPE_HISTORICAL,
       "2" == R.REQUEST_SHAPE_CRITERIA_REFERENCE), (True, True, False))
check("9k  CONTROL: the matching shape does not refuse, at either shape",
      tuple(refusal_code(_fn9k("require_state_shape"),
                         dict(_SHAPE_STATE, request_shape_version=s), s, "/s")
            for s in R.REQUEST_SHAPES),
      ("<did not raise>",) * len(R.REQUEST_SHAPES))
check("9k  CONTROL: no state file at all is not a refusal -- that is the "
      "first submit, and --resume against a fresh --output-dir",
      tuple(refusal_code(_fn9k("require_state_shape"), s, R.REQUEST_SHAPE_VERSION,
                         "/s") for s in ({}, None)),
      ("<did not raise>",) * 2)

# ABSENT REFUSES, AND THE POLICY IS FROM EVIDENCE RATHER THAN FROM CAUTION.
# `require_state_mode` reads an absent `mode` as anchored because every such
# file on disk was in fact written by an anchored run -- a claim about the
# artifacts, and it holds. The same claim about the shape is FALSE: the
# criteria-reference probe submitted at shape 2 before this field existed, and
# its state file records no shape while the manifest beside it records 2. So
# the absent population has at least one shape-2 member and "absent means 1"
# would relabel it. This is the check that fails if anyone later "fixes" the
# refusal by defaulting.
check("9k  an ABSENT shape refuses rather than being read as shape 1",
      refusal_code(_fn9k("require_state_shape"), dict(_SHAPE_STATE),
                   R.REQUEST_SHAPE_VERSION, "/s"), "state_shape_absent")
check("9k  ...and refuses under shape-1 code too, so it is 'unknown' rather "
      "than 'not the shipped shape'",
      refusal_code(_fn9k("require_state_shape"), dict(_SHAPE_STATE),
                   R.REQUEST_SHAPE_HISTORICAL, "/s"), "state_shape_absent")
check("9k  the absent refusal is a DIFFERENT code from the mismatch, because "
      "the remedies differ -- resume with matching code, versus establish the "
      "shape or submit afresh",
      refusal_code(_fn9k("require_state_shape"), dict(_SHAPE_STATE),
                   R.REQUEST_SHAPE_VERSION, "/s")
      != refusal_code(_fn9k("require_state_shape"),
                      dict(_SHAPE_STATE, request_shape_version=1),
                      R.REQUEST_SHAPE_CRITERIA_REFERENCE, "/s"), True)
# THE ADOPTION INSTRUCTION IS CONDITIONED ON EVIDENCE, AND THIS CHECK WAS
# SPLIT WHEN IT BECAME SO. It used to require the message to quote
# STATE_SHAPE_KEY unconditionally, which was right while adoption was always
# offered. It no longer is: the ONLY evidence of a batch's shape is the
# uploaded input file, addressed by the `input_file_id` `submit_batches`
# records per batch, and 16 of the 19 state files on disk carry none -- so a
# message that quotes the key an operator must NOT write by hand, on a file
# where nothing can establish the value, is the invitation this pass removed.
# Both branches are pinned, in both directions, so neither can acquire the
# other's wording.
_SHAPE_STATE_EVIDENCED = dict(
    _SHAPE_STATE,
    batches=[{"id": "batch_shape", "tag": "primary",
              "input_file_id": "file-evidence"},
             {"id": "batch_other", "tag": "primary"}])


def _absent_msg(state):
    return str(drive(lambda: _fn9k("require_state_shape")(
        dict(state), R.REQUEST_SHAPE_VERSION, "/s")))


_MSG_BARE = _absent_msg(_SHAPE_STATE)
_MSG_EVID = _absent_msg(_SHAPE_STATE_EVIDENCED)
check("9k  the absent message refuses to guess out loud, whichever branch it "
      "takes", all(w in m for m in (_MSG_BARE, _MSG_EVID)
                   for w in ("records no request shape", "NOT read as",
                             "--output-dir")), True)
check("9k  with NO input_file_id recorded there is nothing to adopt from, so "
      "the message does NOT quote the key an operator would hand-write, and "
      "says why", (_SHAPE_KEY in _MSG_BARE,
                   "THERE IS NO EVIDENCE TO ADOPT FROM" in _MSG_BARE,
                   "carries an input_file_id" in _MSG_BARE),
      (False, True, True))
check("9k  with an input_file_id recorded it names THE CHECK -- the file to "
      "retrieve, the SYSTEM message, the exact marker, and only then the key "
      "to write",
      all(w in _MSG_EVID for w in ("file-evidence", "files.content", "SYSTEM",
                                   R.FENCE_TRIAL_CRITERIA_OPEN, _SHAPE_KEY)),
      True)
# THE USER-PART WARNING IS THE HALF THE BRIEF FOR THIS PASS GOT WRONG, and it
# is pinned because getting it wrong is a FALSE NEGATIVE: shape 2 omits the
# reference block for any decision whose criteria could not be verified, so a
# batch every one of whose references was absent carries user parts identical
# to shape 1. Checking the user parts would read such a batch as shape 1 and
# the operator would then write a 1 into the file as established fact.
check("9k  ...and it warns off the user parts by name, because a shape-2 "
      "batch with every reference absent carries shape-1 user parts",
      "DO NOT CHECK THE USER PARTS" in _MSG_EVID, True)
check("9k  ...and it names the batches the check CANNOT be run for, rather "
      "than promising a check for all of them",
      "batch_other" in _MSG_EVID, True)
# THE MARKER CLAIM THE MESSAGE MAKES IS TRUE OF THE SHIPPED BUILDER, in all
# four (mode, shape) combinations. Without this the instruction could name a
# marker that is not actually the discriminator -- which is how an operator
# comes to record a wrong shape having followed the instructions exactly.
_MARKER_TRUTH = tuple(
    (m, s, R.FENCE_TRIAL_CRITERIA_OPEN in str(drive(
        R.build_system_prompt, "RUBRIC", mode=m, shape_version=s)))
    for m in (R.MODE_ANCHORED, R.MODE_BLIND) for s in R.REQUEST_SHAPES)
check("9k  the marker the instruction names IS the discriminator: present in "
      "every shape-2 system prompt and absent from every shape-1 one, both "
      "modes",
      _MARKER_TRUTH,
      ((R.MODE_ANCHORED, 1, False), (R.MODE_ANCHORED, 2, True),
       (R.MODE_BLIND, 1, False), (R.MODE_BLIND, 2, True)))
# AND THE LOCAL ARTIFACTS REALLY DO NOT CARRY IT, which is why the check is a
# provider round trip the OPERATOR makes rather than one this module makes.
# `submit_batches` builds the JSONL in memory and uploads it; the three local
# writers are the state file, the raw RESPONSE JSONL and the three JSON
# reports. A future edit that started persisting the requests would make a
# local check possible and should move the instruction -- this check is what
# would notice.
check("9k  nothing local records the submitted requests: submit_batches "
      "uploads the JSONL it builds and no writer in the module writes it",
      ("batch_jsonl(chunk).encode" in _inspect.getsource(R.submit_batches),
       any("batch_jsonl" in _inspect.getsource(f)
           for f in (R.write_state, R.persist_raw_replies, R.write_json))),
      (True, False))
check("9k  the mismatch message names both shapes, what joining them would "
      "corrupt, and the fix",
      all(w in str(drive(lambda: _fn9k("require_state_shape")(
          dict(_SHAPE_STATE, request_shape_version=1),
          R.REQUEST_SHAPE_CRITERIA_REFERENCE, "/s")))
          for w in ("records request shape 1", "builds shape 2",
                    "rater_manifest.json", "ratings.json", "summary.json",
                    "--output-dir")), True)
# DERIVED, NOT RETYPED: both shapes are described from REQUEST_SHAPE_NOTES, so
# a note edited in one place cannot leave the refusal describing the old one.
check("9k  both descriptions come from REQUEST_SHAPE_NOTES",
      all(R.REQUEST_SHAPE_NOTES[s] in str(drive(lambda: _fn9k("require_state_shape")(
          dict(_SHAPE_STATE, request_shape_version=1),
          R.REQUEST_SHAPE_CRITERIA_REFERENCE, "/s")))
          for s in R.REQUEST_SHAPES), True)

# --- 9k -- ...AND THE SHAPE REACHES THE STATE FILE AT SUBMISSION -------
#
# TWO HALVES, AND NEITHER REPLACES THE OTHER. An AST scan cannot see a value
# carried and then serialized wrongly; a round trip cannot see a field that was
# never put in the dict main() writes. The guard above is worth nothing if the
# field never lands -- it would then refuse EVERY resume forever.
_MAIN_SRC = _ast9k.parse(_io9k.open(R.__file__, encoding="utf-8").read())
_main_fn = next((n for n in _ast9k.walk(_MAIN_SRC)
                 if isinstance(n, _ast9k.FunctionDef) and n.name == "main"),
                None)
check("9k  main() was located (probe, so the scans below cannot pass over an "
      "empty walk)", _main_fn is not None, True)
# BOUND ONCE, HERE, AND EMPTY WHEN main() COULD NOT BE FOUND. Sections 9o and
# 9q both read it; the first version defined it inside 9o BELOW its first
# reader and the file aborted on a NameError, which is the shape a `check`
# cannot report.
_MAIN_TXT = _ast9k.unparse(_main_fn) if _main_fn is not None else ""


def _state_update_pairs(fn):
    """(key source, value source) for main()'s ``state.update({...})``."""
    for node in _ast9k.walk(fn) if fn is not None else ():
        if not (isinstance(node, _ast9k.Call)
                and isinstance(node.func, _ast9k.Attribute)
                and node.func.attr == "update"
                and isinstance(node.func.value, _ast9k.Name)
                and node.func.value.id == "state"
                and node.args and isinstance(node.args[0], _ast9k.Dict)):
            continue
        d = node.args[0]
        return [(_ast9k.unparse(k), _ast9k.unparse(v))
                for k, v in zip(d.keys, d.values)]
    return "<no state.update({...}) in main()>"


_UPDATE = _state_update_pairs(_main_fn)
check("9k  main()'s state.update({...}) was located (probe)",
      isinstance(_UPDATE, list) and len(_UPDATE) > 1, True)
check("9k  it records the request shape, under the module's own key constant "
      "rather than a retyped string, from the index the requests were built "
      "from",
      [v for k, v in (_UPDATE if isinstance(_UPDATE, list) else [])
       if k == "STATE_SHAPE_KEY"], ["index.shape_version"])
check("9k  the state key is the same spelling the three written artifacts "
      "use, so a refusal and the manifest beside it can be compared",
      _SHAPE_KEY, "request_shape_version")


class _SubmitStub(object):
    """``files.create`` + ``batches.create``, and nothing else. No network.

    ``_StubClient`` above serves a canned OUTPUT file for ``collect_results``;
    this is the other half of the API surface, and it exists so the check below
    can drive the REAL ``submit_batches`` -- the only place a state file is
    written on the submit path -- rather than asserting about ``write_state``
    in isolation.
    """

    def __init__(self):
        outer = self
        self.uploaded = []

        class _Files(object):
            def create(self, file, purpose):
                outer.uploaded.append(file[0])
                return types.SimpleNamespace(id="file-stub")

        class _Batches(object):
            def create(self, **kw):
                return types.SimpleNamespace(
                    id="batch-stub-%d" % len(outer.uploaded))

        self.files = _Files()
        self.batches = _Batches()


_shape_tmp = _tempfile9k.mkdtemp(prefix="oncotriage-rater-shape-")
try:
    _state_path = _os9k.path.join(_shape_tmp, R.state_filename(R.MODE_BLIND))
    # The dict main() holds when `submit_batches` is entered, with the one
    # field under test taken FROM THE INDEX rather than typed here -- so a
    # defect that stops `build_requests` recording a shape fails this too.
    _live = {"mode": R.MODE_BLIND,
             _SHAPE_KEY: getattr(_shape_idx, "shape_version", None)}
    _submitted = drive(R.submit_batches, _SubmitStub(),
                       [[{"custom_id": "c0", "params": {"model": "m"}}]],
                       _live, _state_path, "primary")
    _written = drive(R.read_state, _state_path)
    check("9k  submit_batches really wrote the state file (probe)",
          isinstance(_written, dict) and bool(_written.get("batches")), True)
    check("9k  and the shape is in it, at submission time, beside the batch "
          "id it was submitted with",
          walk(_written, _SHAPE_KEY), R.REQUEST_SHAPE_VERSION)
    # A resume of exactly that file, by this code, must proceed. Without this
    # the guard could be satisfied by a field that never matches anything.
    check("9k  and a resume of that very file by this code proceeds",
          refusal_code(_fn9k("require_state_shape"), _written,
                       R.REQUEST_SHAPE_VERSION, _state_path),
          "<did not raise>")
finally:
    _shutil9k.rmtree(_shape_tmp, ignore_errors=True)
check("9k  the temp directory is gone", _os9k.path.isdir(_shape_tmp), False)

# THE GUARD IS CALLED, AND FROM THE SAME try/except AS THE OTHER TWO. A refusal
# raised outside that block would escape main() as a traceback instead of the
# "REFUSED: ..." line and the exit 1 the other two produce.
check("9k  main() calls require_state_shape, with the index's shape and the "
      "state file path",
      "require_state_shape(state, index.shape_version, state_path)"
      in (_ast9k.unparse(_main_fn) if _main_fn is not None else ""), True)


def _calls_inside_try(fn, name):
    """How many times ``name`` is called from inside a ``try`` body of ``fn``."""
    found = 0
    for node in _ast9k.walk(fn) if fn is not None else ():
        if not isinstance(node, _ast9k.Try):
            continue
        for stmt in node.body:
            for call in _ast9k.walk(stmt):
                if (isinstance(call, _ast9k.Call)
                        and isinstance(call.func, _ast9k.Name)
                        and call.func.id == name):
                    found += 1
    return found


check("9k  and the call is inside a try, exactly as often as the subset guard "
      "it sits beside, so the refusal prints and exits 1 rather than escaping",
      (_calls_inside_try(_main_fn, "require_state_shape"),
       _calls_inside_try(_main_fn, "require_state_subset")), (1, 1))

# --- 9l -- THERE IS NO CACHE TTL, AND THE FLAGS THAT SET ONE ARE GONE ----
#
# WHAT THIS BLOCK USED TO PIN, and why it could not simply be deleted. It held
# `DEFAULT_CACHE_TTL == "5m"` with the whole measurement behind it: the 1.8.0
# blind run breached its $13.00 gate at $13.5831, 88.9% of the overrun was
# cache WRITE tokens at the 1h premium, and re-priced at 5m the run lands at
# $10.81. That is real history and it is kept in git.
#
# IT DESCRIBES A MECHANISM THIS ARM DOES NOT HAVE. OpenAI's prompt cache is
# automatic: no `cache_control`, no breakpoint, no TTL to choose, nothing to
# send. So the checks are replaced rather than removed, and what replaces them
# is the property the flags used to be a proxy for -- that the ONE remaining
# cache lever, the shared request prefix, is actually built.
#
# THE ABSENCE IS PINNED IN BOTH DIRECTIONS. A reinstated `--cache-ttl` would
# be a flag an operator sets deliberately, sees echoed in the plan banner and
# the manifest, and which reaches nothing -- the dead-tunable shape this
# project deletes rather than tolerates.
for _gone in ("DEFAULT_CACHE_TTL",):
    check("9l  %s is gone: there is no TTL to choose on this vendor, and a "
          "constant that cannot reach the wire is one an operator would set "
          "and be silently ignored on" % _gone,
          hasattr(R, _gone), False)
_ns = drive(R._parse_args, ["--dry-run"])
check("9l  ...and so are the two flags that set it: neither survives on the "
      "parsed namespace, so nothing downstream can read one and believe it "
      "was honoured",
      [a for a in ("cache_ttl", "no_cache") if hasattr(_ns, a)], [])
def _argparse_rejects(flag, value="1h"):
    """Does argparse REFUSE this flag? True when it exits, False when it takes.

    ``SystemExit`` IS NOT AN ``Exception`` -- argparse calls ``sys.exit(2)`` on
    an unknown flag, and this file's own ``raises()`` helper catches
    ``Exception``, so a bare drive of ``_parse_args`` here does not fail the
    check, it ENDS THE RUN at exit code 2 with no summary and every check below
    unreported. That is the abort shape this project has shipped repeatedly and
    it is why this helper exists rather than an inline call. argparse also
    writes its usage to stderr, which is captured so a passing run stays
    readable.
    """
    import contextlib
    import io as _io
    buf = _io.StringIO()
    try:
        with contextlib.redirect_stderr(buf):
            R._parse_args(["--dry-run", flag, value])
    except SystemExit:
        return True
    except Exception:                                           # noqa: BLE001
        return True
    return False


check("9l  ...and argparse REFUSES them rather than ignoring them, so an "
      "operator who types one is told rather than quietly given the default",
      [f for f in ("--cache-ttl", "--no-cache") if not _argparse_rejects(f)],
      [])
check("9l  CONTROL: a flag that IS defined, given a value of its own type, "
      "is not reported as rejected -- so the check above discriminates "
      "between an absent flag and a mistyped value",
      _argparse_rejects("--max-tokens", "512"), False)
check("9l  no request carries a cache_control block, because there is no such "
      "field on this API",
      any("cache_control" in json.dumps(r["params"])
          for r in built(R.MODE_ANCHORED).requests), False)

# *** THE ONE CACHE LEVER THAT IS LEFT: THE SHARED PREFIX. ***
#
# OpenAI caches on the longest common PREFIX of the request, so the order of
# the parts is the mechanism. System prompt, then patient record, then the one
# part that differs per decision. Reverse the last two and every request for a
# patient diverges at its second part, the cache serves nothing, and NOTHING
# RAISES -- the only trace is `cached_tokens` reading 0 on a bill nobody
# queried. That is why this is pinned rather than left to the shape of the
# code.
_pref = built(R.MODE_ANCHORED)
_by_patient = {}
for _r in _pref.requests:
    _d = _pref.by_custom_id[_r["custom_id"]]
    _parts = _r["params"]["messages"]
    _prefix = (_parts[0]["content"], _parts[1]["content"][0]["text"])
    _by_patient.setdefault(_d.patient_id, set()).add(_prefix)
check("9l  every request for one patient shares an identical (system prompt, "
      "patient record) prefix -- which is the whole of the cache strategy "
      "now",
      {p: len(v) for p, v in _by_patient.items()},
      {p: 1 for p in _by_patient})
check("9l  non-degeneracy: there is more than one request per patient, so "
      "the check above is not one prefix compared with itself",
      max([sum(1 for r in _pref.requests
               if _pref.by_custom_id[r["custom_id"]].patient_id == p)
           for p in _by_patient] or [0]) > 1, True)
check("9l  ...and the two patients' prefixes DIFFER, so the check is about "
      "the record rather than about a constant",
      len({tuple(sorted(v))[0] for v in _by_patient.values() if v}),
      len(_by_patient))
check("9l  the per-decision block is the LAST part, after the record: it is "
      "what must differ, and anything after it would be outside the cached "
      "prefix for no reason",
      all(len(r["params"]["messages"][1]["content"]) == 2
          for r in _pref.requests), True)

# --- 9m -- THE REPLY CEILING IS PER MODE ---------------------------------
# Item 9 measured 179 of 990 BLIND messages (18.1%) stopping on ``max_tokens``
# at the shipped ceiling of 300, with 27 still cut off after the harness's 2x
# retry -- 27 decisions unrated, which is a lost measurement. Output bills on
# tokens GENERATED, so a higher ceiling costs nothing and cannot change a reply
# that already fit.
#
# THE ANCHORED VALUE IS PINNED HERE ON PURPOSE, AND NOT BECAUSE 300 IS ENOUGH
# FOR IT. Anchored's own six 300-ceiling runs stop on ``max_tokens`` at 17.8%,
# the same rate as blind. 300 is kept because ``max_tokens`` is a serialized
# field of the anchored request body that 8a hashes as comparable history, so
# moving it would leave 8a passing -- it passes 300 as a literal -- while the
# property it asserts stopped holding for a real invocation. This check is what
# turns that from a habit into a pin: raising the anchored default fails HERE,
# where the reason is written down, rather than nowhere.
# ** AND THE PORT MOVED BOTH CEILINGS, INCLUDING THE ONE THIS BLOCK PINNED AT
# ** 300 SPECIFICALLY TO KEEP 8a HONEST.
#
# The old reasoning was: `max_tokens` is a serialized field of the anchored
# request body, 8a hashes that body as comparable history, so moving the
# default would leave 8a passing on a literal while the property stopped being
# true of a real invocation. That argument was correct and its premise is gone
# -- 8a now pins the CONTENT through a translated envelope, and the ceiling is
# `max_completion_tokens`, a field the historical Anthropic body did not have.
#
# WHAT FORCED THE MOVE IS THE JUDGE, NOT THE PIN. Reasoning tokens count
# against this ceiling AND are generated BEFORE the answer, so at medium effort
# a 300-token ceiling truncates before the first character of JSON: every
# response comes back `finish_reason="length"` with empty content, every
# decision buckets `truncated_max_tokens`, and the retry doubles a number still
# nowhere near enough.
check("9m  both ceilings leave room for reasoning, which is generated BEFORE "
      "the answer and counted against this same budget",
      (R.DEFAULT_MAX_TOKENS, R.DEFAULT_MAX_TOKENS_BLIND), (4096, 1536))
check("9m  non-degeneracy: the anchored ceiling is far above the pre-port "
      "one, which is what the reasoning budget costs",
      R.DEFAULT_MAX_TOKENS >= 4 * 300, True)

# ** AND THE BLIND CEILING IS NO LONGER BORROWED. It is DERIVED, so the check
# ** re-runs the derivation rather than retyping the answer: a constant and a
# ** comment claiming where it came from can disagree, and only one of the two
# ** governs a paid request.
_M = R.MAX_TOKENS_BLIND_MEASURED
import math as _math                                            # noqa: E402
_RAW = max(_M["completion_max"] * 1.5, _M["completion_p99"] * 2.0)
_DERIVED = int(_math.ceil(_RAW / 256.0)) * 256
check("9m  the blind ceiling IS max(observed max x 1.5, p99 x 2.0) rounded "
      "up to a 256 multiple, recomputed from the recorded measurement",
      R.DEFAULT_MAX_TOKENS_BLIND, _DERIVED)
check("9m  ...and it clears the observed maximum with real headroom, which "
      "is what a multiplier is FOR -- 191 replies on one judge on one day "
      "is not the maximum of the distribution",
      R.DEFAULT_MAX_TOKENS_BLIND >= int(1.5 * _M["completion_max"]), True)
check("9m  ...and it is still far above the visible object alone, because "
      "reasoning is generated FIRST and truncation lands before the first "
      "character of JSON",
      R.DEFAULT_MAX_TOKENS_BLIND > _M["reasoning_p99"], True)
check("9m  the measurement records that NOTHING truncated, without which "
      "the maximum it reports is the old ceiling rather than the model's",
      _M["truncated"], 0)
check("9m  ...and it names its own n, judge and effort, so a reader can see "
      "what the number is provisional ON",
      sorted(k for k in ("n", "judge_model", "reasoning_effort", "source",
                         "measured_on") if _M.get(k)),
      ["judge_model", "measured_on", "n", "reasoning_effort", "source"])
check("9m  a truncation at the new ceiling is still bucketed loudly rather "
      "than absorbed",
      "truncated_max_tokens" in R.UNRATED_REASONS
      and "truncated_max_tokens" in R.RETRYABLE_REASONS, True)
check("9m  ...and it reaches the wire under the field a reasoning model "
      "requires, not the legacy one",
      sorted(k for k in walk(built(R.MODE_ANCHORED).requests, 0, "params",
                             default={})
             if "token" in k), ["max_completion_tokens"])

# The table is TOTAL over MODES. A mode with no ceiling is a paid request
# governed by a number nobody chose.
check("9m  the table names every mode exactly once",
      tuple(sorted(R.MAX_TOKENS_BY_MODE)), tuple(sorted(R.MODES)))
check("9m  ...and each entry is that mode's own constant",
      (R.MAX_TOKENS_BY_MODE[R.MODE_ANCHORED],
       R.MAX_TOKENS_BY_MODE[R.MODE_BLIND]),
      (R.DEFAULT_MAX_TOKENS, R.DEFAULT_MAX_TOKENS_BLIND))

check("9m  resolve_max_tokens gives each mode its table entry",
      (drive(R.resolve_max_tokens, R.MODE_ANCHORED),
       drive(R.resolve_max_tokens, R.MODE_BLIND)),
      (R.DEFAULT_MAX_TOKENS, R.DEFAULT_MAX_TOKENS_BLIND))
check("9m  an explicit ceiling wins in either mode -- the mode decides only "
      "when the operator named nothing",
      (drive(R.resolve_max_tokens, R.MODE_ANCHORED, 777),
       drive(R.resolve_max_tokens, R.MODE_BLIND, 777)), (777, 777))
check("9m  an unknown mode refuses rather than falling through to a ceiling",
      refusal_code(R.resolve_max_tokens, "sideways"), "unknown_mode")

# A ceiling that cannot produce a rating must not reach a batch. bool is its
# own case: isinstance(True, int) is True, so True would otherwise resolve to 1.
for _bad, _label in ((0, "zero"), (-1, "negative"), (True, "a bool"),
                     (3.5, "a float"), ("300", "a string")):
    check(f"9m  {_label} is refused before anything is submitted",
          refusal_code(R.resolve_max_tokens, R.MODE_BLIND, _bad),
          "bad_max_tokens")

# --- 9m -- ...AND IT REACHES THE WIRE ------------------------------------
# The two checks above are about a pure function. These are about the request
# actually built, which is the only thing the API sees.
check("9m  argparse defaults to None, which is the only value that can mean "
      "'the operator named none' -- a numeric default cannot be told apart "
      "from an operator who typed that same number",
      drive(R._parse_args, ["--dry-run"]).max_tokens, None)
check("9m  ...and an explicit value survives parsing",
      drive(R._parse_args, ["--dry-run", "--max-tokens", "777"]).max_tokens,
      777)

# --help IS A PAID PATH'S FIRST COMMAND AND IT HAD NO CHECK. argparse
# %-expands every help string, so one literal percent sign anywhere in this
# parser makes ``--help`` raise instead of printing -- and the percent that
# belongs in this option's help is the whole reason the default moved. Found by
# running, not by reading: the first version of this section shipped an
# unescaped "18.1%" and took --help down with a TypeError from inside argparse.
def _help_text():
    import contextlib, io as _io
    buf = _io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            R._parse_args(["--help"])
    except SystemExit:
        return buf.getvalue()
    except Exception as exc:                                   # noqa: BLE001
        return "<RAISED %s: %s>" % (type(exc).__name__, exc)
    return buf.getvalue()


_help = _help_text()
check("9m  --help renders rather than raising out of argparse's own "
      "%-expansion", _help.startswith("<RAISED"), False)
check("9m  ...and it names both ceilings, so an operator reads the mode "
      "default rather than guessing it",
      (str(R.DEFAULT_MAX_TOKENS) in _help,
       str(R.DEFAULT_MAX_TOKENS_BLIND) in _help), (True, True))
check("9m  ...and the literal percent survives expansion as one percent",
      "18.1% of the time" in " ".join(_help.split()), True)

_ceil_anch = built(R.MODE_ANCHORED)
_ceil_blind = built(R.MODE_BLIND)
check("9m  CONTROL: the 8a pin's request list is still built at the 300 the "
      "helper hands it, so this section moves no byte of it",
      sorted({r["params"]["max_completion_tokens"]
              for r in _ceil_anch.requests})
      if hasattr(_ceil_anch, "requests") else [], [300])
check("9m  build_requests carries whatever ceiling it is handed onto EVERY "
      "request, in both modes",
      (sorted({r["params"]["max_completion_tokens"]
               for r in _ceil_blind.requests})
       if hasattr(_ceil_blind, "requests") else []), [300])

_wired = drive(R.build_requests, planted_run(),
               drive(R.build_system_prompt, _FIXED_RUBRIC, mode=R.MODE_BLIND),
               {"rubric_sha256": "x"}, _MODEL,
               drive(R.resolve_max_tokens, R.MODE_BLIND), None,
               mode=R.MODE_BLIND,
               arm_definitions=drive(R.lift_arm_status_definitions,
                                     _FIXED_RUBRIC))
check("9m  a blind run that names no ceiling puts the mode's own default on "
      "every request",
      sorted({r["params"]["max_completion_tokens"] for r in _wired.requests})
      if hasattr(_wired, "requests") else [], [R.DEFAULT_MAX_TOKENS_BLIND])
check("9m  non-degeneracy: that request list is not empty",
      len(_wired.requests) if hasattr(_wired, "requests") else 0, len(_PLANT))

# _prepare owns the resolution and WRITES IT BACK, because the plan banner, the
# retry's 2x, the manifest and the ledger all read args.max_tokens afterwards.
# Left at None, three of them would report a ceiling the wire never carried.
# _prepare needs a run directory, so this drives the flag half of it: the
# resolution sits above every refusal that needs the corpus, so a bad ceiling
# is refused before a run is even resolved.
check("9m  _prepare refuses a bad ceiling from the FLAGS ALONE, before a run "
      "directory is resolved",
      refusal_code(R._prepare, drive(R._parse_args,
                                     ["--dry-run", "--blind",
                                      "--max-tokens", "0"])),
      "bad_max_tokens")
check("9m  CONTROL: the same invocation with a legal ceiling gets PAST the "
      "ceiling guard -- so the check above reads the ceiling rather than "
      "some other precondition of the same call",
      refusal_code(R._prepare, drive(R._parse_args,
                                     ["--dry-run", "--blind",
                                      "--max-tokens", "1"]))
      != "bad_max_tokens", True)

# --- 9n -- TRUNCATION IS STILL ACCOUNTED FOR AT THE LOWER CEILING -----------
#
# Lowering a reply ceiling is only safe while a reply that hits it is still a
# LOUD, COUNTED, RETRIED event rather than a short answer nobody notices. 5a
# above drives that through the real `collect_results` and is ceiling-
# independent by construction -- it keys on `finish_reason`, not on a number.
# What is pinned HERE is the retry, which is the ONE place the ceiling itself
# reaches the recovery path: the harness resubmits a truncated decision at
# TWICE `args.max_tokens`, so the retry ceiling moves with the default and a
# decision still cut off after it is UNRATED and reported as such.
_RETRY_MULTIPLIER = 2
_blind_ceiling = drive(R.resolve_max_tokens, R.MODE_BLIND)
check("9n  a truncation is a REPORTED unrated reason and a RETRYABLE one, "
      "which is what makes a lower ceiling safe rather than quiet",
      ("truncated_max_tokens" in R.UNRATED_REASONS,
       "truncated_max_tokens" in R.RETRYABLE_REASONS), (True, True))
check("9n  ...and the retry's ceiling is DERIVED from the resolved one, so it "
      "moved with this pass rather than staying at the old default",
      _blind_ceiling * _RETRY_MULTIPLIER, 3072)
check("9n  ...which still clears the measured blind maximum with room, so "
      "the recovery path was not narrowed past what the data supports",
      _blind_ceiling * _RETRY_MULTIPLIER
      >= 3 * _M["completion_max"], True)
check("9n  the source doubles args.max_tokens rather than naming a literal, "
      "which is what makes the two above facts about the same number",
      "args.max_tokens * 2" in _inspect.getsource(R.main), True)


# --- 9o -- A RESUME WITH NO STATE FILE IS REFUSED -------------------------
#
# THE GAP THIS CLOSES WAS MEASURED BY DRIVING main(), NOT BY READING IT.
# `--resume batch_x` against an empty --output-dir reached the poll with all
# FIVE provenance guards having returned without comparing anything: each opens
# `if not state: return`, which is correct for a first submit and is the exact
# opposite of correct on a resume, where the batch exists, was paid for, and
# this directory records nothing about it. The session then writes three
# artifacts stamping TODAY'S mode, subset, shape, rubric and JUDGE onto
# answers it cannot establish anything about.
#
# THE FIVE "SILENT ON NO STATE" READINGS ARE PINNED FIRST, because they are the
# premise: if any of them ever started refusing an empty state on its own, this
# section's subject would be gone and its checks would pass for the wrong
# reason.
_R9o = _fn9k
check("9o  premise: each of the other five guards is SILENT on an empty state "
      "-- which is why one guard has to know it is a resume",
      tuple(refusal_code(fn, *args) for fn, args in (
          (_R9o("require_state_mode"), ({}, R.MODE_BLIND, "/s")),
          (_R9o("require_state_subset"), ({}, built(R.MODE_BLIND), "/s")),
          (_R9o("require_state_shape"), ({}, R.REQUEST_SHAPE_VERSION, "/s")),
          (_R9o("require_state_rubric"), ({}, {R.RUBRIC_SHA_KEY: "x"}, "/s")),
          (_R9o("require_state_model"), ({}, R.DEFAULT_MODEL, "/s")))),
      ("<did not raise>",) * 5)
check("9o  a resume with no state file REFUSES, by its own code",
      refusal_code(_R9o("require_state_for_resume"), {}, "batch_x", "/nope"),
      "resume_without_state")
check("9o  ...and a SUBMIT with no state file does not, because that is the "
      "first submit and there is nothing to disagree with",
      refusal_code(_R9o("require_state_for_resume"), {}, None, "/nope"),
      "<did not raise>")
check("9o  ...and a resume WITH a state file does not either -- the five "
      "guards beside it are what judge its contents",
      refusal_code(_R9o("require_state_for_resume"),
                   {"mode": R.MODE_BLIND}, "batch_x", "/nope"),
      "<did not raise>")
# TYPE CONFUSION, the bool/float analogue. `read_state` returns None or a
# parsed value, and a file holding `[]`, `0`, `false` or `"x"` parses. All of
# them are recorded-nothing, so all of them must refuse rather than being read
# as "a state file exists".
check("9o  a state file that parses to something falsy-or-not-a-record still "
      "refuses a resume: [] , 0, false, \"\" and {} are all recorded-nothing",
      tuple(refusal_code(_R9o("require_state_for_resume"), v, "batch_x", "/n")
            for v in ([], 0, False, "", {})),
      ("resume_without_state",) * 5)
# THE MESSAGE MUST NAME ALL FOUR FIELDS, because the operator's question is
# "what exactly is unknown". Naming them from the module's own constants is
# what stops the message going stale when a fifth is added.
_MSG9o = str(drive(lambda: _R9o("require_state_for_resume")(
    {}, "batch_x,batch_y", "/tmp/nope/rater_state_blind.json")))
check("9o  the message names the batches, the file, all four provenance "
      "fields from the module's own constants, and the three artifacts that "
      "would carry unbacked values",
      all(w in _MSG9o for w in ("batch_x,batch_y", "/tmp/nope",
                                R.INCLUDE_KEYS_STATE_KEY, R.STATE_SHAPE_KEY,
                                R.RUBRIC_SHA_KEY, "rater_manifest.json",
                                "ratings.json", "summary.json")), True)
check("9o  ...and states that there is no flag which admits it, so a reader "
      "does not go looking for one",
      "no flag that admits this" in _MSG9o, True)
# ABSENT AND UNREADABLE ARE DIFFERENT OPERATOR ERRORS. `read_state` reports a
# decode failure as None, so the guard cannot tell them apart from its argument
# -- it asks the filesystem, and the two remedies (wrong directory / broken
# file) are distinguished in the text.
_tmp9o = _tempfile9k.mkdtemp(prefix="oncotriage-rater-9o-")
try:
    _bad = _os9k.path.join(_tmp9o, R.state_filename(R.MODE_BLIND))
    with _io9k.open(_bad, "w", encoding="utf-8") as _fh:
        _fh.write("{not json")
    check("9o  read_state reports an unreadable file as None (probe)",
          drive(R.read_state, _bad), None)
    _msg_bad = str(drive(lambda: _R9o("require_state_for_resume")(
        drive(R.read_state, _bad) or {}, "batch_x", _bad)))
    _empty_obj = _os9k.path.join(_tmp9o, "empty_" + R.STATE_FILENAME_BLIND)
    with _io9k.open(_empty_obj, "w", encoding="utf-8") as _fh:
        _fh.write("{}")
    check("9o  a state file holding a literal {} is READABLE and records "
          "nothing (probe: it is not a decode failure)",
          drive(R.read_state, _empty_obj), {})
    _msg_empty = str(drive(lambda: _R9o("require_state_for_resume")(
        drive(R.read_state, _empty_obj) or {}, "batch_x", _empty_obj)))
    check("9o  a PRESENT state file that records nothing refuses and says the "
          "file is there, rather than saying it does not exist -- the remedies "
          "differ (wrong directory versus a file that records nothing)",
          ("records nothing this module can read as state" in _msg_bad,
           "does not exist" in _msg_bad,
           "records nothing this module can read as state" in _msg_empty,
           "does not exist" in _msg_empty), (True, False, True, False))
    check("9o  ...and an ABSENT one says THAT, on the same call",
          "does not exist" in str(drive(lambda: _R9o(
              "require_state_for_resume")({}, "batch_x",
                                          _bad + ".missing"))), True)
    # A DECODED NON-OBJECT IS NOT A STATE FILE, and the alternative to refusing
    # it here was five copies of one isinstance test. `[1]`, `3` and `"x"` are
    # valid JSON, so json.load returned them, `or {}` in main() left them alone
    # because they are truthy, and the first guard's `state.get("mode")` was an
    # AttributeError -- which `except RaterRefusal` does not catch, so an
    # operator with a corrupt file got a traceback instead of the REFUSED line
    # and exit 1 that every other state fault produces.
    _nonobj = {}
    for _i, _payload in enumerate(("[1]", "3", '"x"', "null", "true")):
        _q = _os9k.path.join(_tmp9o, "nonobj%d.json" % _i)
        with _io9k.open(_q, "w", encoding="utf-8") as _fh:
            _fh.write(_payload)
        _nonobj[_payload] = drive(R.read_state, _q)
    check("9o  read_state returns None for a payload that decodes to a "
          "NON-OBJECT, so the four guards' 'no state' branch covers it and "
          "nothing calls .get on a list",
          _nonobj, {"[1]": None, "3": None, '"x"': None, "null": None,
                    "true": None})
    # NON-DEGENERACY: it still returns a real mapping for a real state file, so
    # the readings above are about the payload's TYPE rather than about
    # read_state having stopped working.
    _okp = _os9k.path.join(_tmp9o, "ok.json")
    with _io9k.open(_okp, "w", encoding="utf-8") as _fh:
        _fh.write('{"mode": "blind"}')
    check("9o  non-degeneracy: a real object still round-trips",
          drive(R.read_state, _okp), {"mode": "blind"})
    # AND THE WHOLE CHAIN: a non-object on disk, through read_state, through
    # main()'s `or {}`, reaches the resume guard's REFUSAL rather than a
    # traceback. This is the property; the readings above are the mechanism.
    _q0 = _os9k.path.join(_tmp9o, "nonobj0.json")
    check("9o  ...so a resume against a state file holding [1] refuses by "
          "name instead of escaping main() as an AttributeError",
          refusal_code(_R9o("require_state_for_resume"),
                       drive(R.read_state, _q0) or {}, "batch_x", _q0),
          "resume_without_state")
    # AND refuse_batch_from_other_mode HAD THE SAME HOLE against the OTHER
    # mode's file, which is why the fix is in read_state rather than in the
    # five guards: this call reads a file this session did not write.
    _other = _os9k.path.join(_tmp9o, R.state_filename(R.MODE_ANCHORED))
    with _io9k.open(_other, "w", encoding="utf-8") as _fh:
        _fh.write("[1]")
    check("9o  ...and the cross-mode guard survives a non-object in the OTHER "
          "mode's state file, which it reads through the same function",
          refusal_code(R.refuse_batch_from_other_mode, ["batch_x"],
                       R.MODE_BLIND, _tmp9o, None), "<did not raise>")
finally:
    _shutil9k.rmtree(_tmp9o, ignore_errors=True)
check("9o  the temp directory is gone", _os9k.path.isdir(_tmp9o), False)
# A RESUME THAT NAMES NO PARSEABLE ID IS ITS OWN REFUSAL. `--resume ,` is
# truthy, so it clears main()'s "nothing to do" check, and the id list it
# produces is EMPTY -- the poll loop runs zero times, every decision comes back
# no_result, and the run exits 3 reporting nil coverage. Pre-existing, adjacent
# to this guard's subject, and closed here under a separate code because the
# remedy differs: name a batch, versus resume from the right directory.
check("9o  a resume naming NO parseable batch id refuses under its own code",
      tuple(refusal_code(_R9o("require_state_for_resume"),
                         {"mode": R.MODE_BLIND}, v, "/s")
            for v in (",", " ", ",,", " , ")),
      ("resume_names_no_batch",) * 4)
check("9o  ...and it fires even when the state file is in perfect order, "
      "because a flag naming nothing is a defect whatever the file records",
      refusal_code(_R9o("require_state_for_resume"),
                   {"mode": R.MODE_BLIND, "batches": [{"id": "b"}]},
                   ",", "/s"), "resume_names_no_batch")
check("9o  ...and it is a DIFFERENT code from the missing-state refusal",
      refusal_code(_R9o("require_state_for_resume"), {}, ",", "/s")
      != refusal_code(_R9o("require_state_for_resume"), {}, "batch_x", "/s"),
      True)
check("9o  ...and a resume naming ONE id among empties proceeds, so the check "
      "is about naming nothing rather than about punctuation",
      refusal_code(_R9o("require_state_for_resume"), {"mode": R.MODE_BLIND},
                   ",batch_x,", "/s"), "<did not raise>")
# AND THE LIST THE GUARD PARSES IS THE LIST main() POLLS -- one expression, in
# main(), so a flag the guard accepted cannot become an empty poll list.
check("9o  main() builds its resume id list with the same filter the guard "
      "applies, so the two cannot disagree about what names a batch",
      ("if b.strip()" in _MAIN_TXT,
       _MAIN_TXT.count("args.resume.split(',')")
       + _MAIN_TXT.count('args.resume.split(",")')), (True, 1))
# CALLED, FIRST, AND FROM THE SAME try AS THE OTHERS. It has to be first: it is
# the only one that can tell a resume from a first submit, and every guard
# after it is a no-op on the state it refuses.
check("9o  main() calls it, with the resume flag and the state path",
      "require_state_for_resume(state, args.resume, state_path)" in _MAIN_TXT,
      True)
check("9o  ...inside a try, exactly as often as the subset guard beside it",
      (_calls_inside_try(_main_fn, "require_state_for_resume"),
       _calls_inside_try(_main_fn, "require_state_subset")), (1, 1))
check("9o  ...and BEFORE the five it gates, in main()'s own source order",
      [n for n in ("require_state_for_resume", "require_state_mode",
                   "require_state_subset", "require_state_shape",
                   "require_state_rubric", "require_state_model")
       if n in _MAIN_TXT][0], "require_state_for_resume")


# --- 9p -- ...AND THE RUBRIC THE ANSWERS WERE JUDGED AGAINST --------------
#
# `rubric_sha256` was written into the state file by the commit that ADDED this
# module and was read back by NOTHING, so a resume across an edit to the
# shipped Stage 5 prompt joined answers judged against one rulebook onto
# artifacts recording the other. Nothing else catches it: the rubric is not in
# the custom_id, so the join succeeds and every rating parses.
_RUBRIC_STATE = {"mode": R.MODE_BLIND, "include_keys_sha256": None,
                 R.STATE_SHAPE_KEY: R.REQUEST_SHAPE_VERSION,
                 R.RUBRIC_SHA_KEY: "a" * 64,
                 "batches": [{"id": "batch_rubric"}]}
_META_A = {R.RUBRIC_SHA_KEY: "a" * 64, "source_prompt_version": "9.9.9"}
_META_B = {R.RUBRIC_SHA_KEY: "b" * 64, "source_prompt_version": "9.9.9"}
check("9p  an EQUAL rubric digest proceeds, and the guard hands back the "
      "value it proved equal",
      drive(_R9o("require_state_rubric"), dict(_RUBRIC_STATE), _META_A, "/s"),
      "a" * 64)
check("9p  a DIFFERENT recorded digest refuses, by its own code",
      refusal_code(_R9o("require_state_rubric"), dict(_RUBRIC_STATE),
                   _META_B, "/s"), "state_rubric_mismatch")
# ABSENT REFUSES, AND THE POLICY IS FROM A SURVEY. Unlike the shape, the absent
# population here is EMPTY: the field predates `mode`, predates
# `include_keys_sha256`, predates `input_file_id`, and all nineteen state files
# under 09- Testing/Evaluation Runs/ carry it. So absence means the file was not
# written by this writer, and there is no legacy resume to strand.
check("9p  an ABSENT rubric digest refuses, under a code of its own",
      refusal_code(_R9o("require_state_rubric"),
                   {k: v for k, v in _RUBRIC_STATE.items()
                    if k != R.RUBRIC_SHA_KEY}, _META_A, "/s"),
      "state_rubric_absent")
check("9p  ...and the three codes are distinct, because the remedies are",
      len({refusal_code(_R9o("require_state_rubric"), dict(_RUBRIC_STATE),
                        _META_B, "/s"),
           refusal_code(_R9o("require_state_rubric"),
                        {k: v for k, v in _RUBRIC_STATE.items()
                         if k != R.RUBRIC_SHA_KEY}, _META_A, "/s"),
           refusal_code(_R9o("require_state_rubric"),
                        dict(_RUBRIC_STATE, rubric_sha256=1), _META_A, "/s")}),
      3)
# TYPE CONFUSION, AND THE REVERT MATRIX CORRECTED THIS BLOCK'S OWN PREMISE. The
# first version guarded the equality with `isinstance(found, str) and found ==
# want` by analogy with the shape guard's bool/float exclusion -- and deleting
# that isinstance changed NOTHING any check could see, because no bool, int,
# float, list or dict equals a hex string. The shape guard's exclusion IS live
# (`True == 1`); this one was dead code. So the type decision is now a branch of
# its own, ABOVE the equality, and what these checks pin is that a non-digest
# is reported as a file that records nothing rather than as a prompt that moved
# -- two different remedies.
check("9p  a non-string digest is its own refusal rather than a mismatch: "
      "true, 1, 1.0, a list and a dict all land in the malformed branch",
      tuple(refusal_code(_R9o("require_state_rubric"),
                         dict(_RUBRIC_STATE, rubric_sha256=v), _META_A, "/s")
            for v in (True, 1, 1.0, ["a" * 64], {"sha": "a" * 64})),
      ("state_rubric_malformed",) * 5)
check("9p  ...and a string that merely differs is a MISMATCH, so the type "
      "branch has not swallowed the value branch",
      refusal_code(_R9o("require_state_rubric"),
                   dict(_RUBRIC_STATE, rubric_sha256="b" * 64),
                   _META_A, "/s"), "state_rubric_mismatch")
# THE ORDER IS PINNED, because it is what makes every branch reachable: absent,
# then type, then value. Reordered any other way one branch becomes dead -- put
# the equality first and a non-digest is a mismatch; put the type check above
# the absent check and `None` is reported as a malformed digest.
_RUBRIC_SRC = _ast9k.unparse(next(
    (n for n in _ast9k.walk(_MAIN_SRC)
     if isinstance(n, _ast9k.FunctionDef)
     and n.name == "require_state_rubric"), _ast9k.parse("def _m(): pass")))
_ORDER_AT = {c: _RUBRIC_SRC.find(c)
             for c in ("state_rubric_absent", "state_rubric_malformed",
                       "state_rubric_mismatch", "found == want")}
check("9p  the guard tests absent, then type, then value -- in that order, so "
      "no branch is unreachable",
      (all(v >= 0 for v in _ORDER_AT.values()),
       _ORDER_AT["state_rubric_absent"] < _ORDER_AT["state_rubric_malformed"]
       < _ORDER_AT["found == want"] < _ORDER_AT["state_rubric_mismatch"]),
      (True, True))
check("9p  ...and the equality is BARE, not guarded by an isinstance that "
      "cannot change an outcome -- the type branch above it is what decides",
      "isinstance(found, str) and found == want" in _RUBRIC_SRC, False)
# WEAKENED-COMPARISON ANALOGUE: a prefix comparison would accept a digest that
# shares its first twelve characters -- which is exactly what the message
# PRINTS, so it is the plausible mistake.
check("9p  the comparison is not on the printed prefix: a digest agreeing for "
      "12 characters and differing after still refuses",
      refusal_code(_R9o("require_state_rubric"),
                   dict(_RUBRIC_STATE, rubric_sha256="a" * 12 + "c" * 52),
                   _META_A, "/s"), "state_rubric_mismatch")
_MSG9p = str(drive(lambda: _R9o("require_state_rubric")(
    dict(_RUBRIC_STATE), _META_B, "/s")))
check("9p  the mismatch message states BOTH digests, what joining them "
      "corrupts, where the rules come from, and the remedy",
      all(w in _MSG9p for w in ("a" * 12, "b" * 12, "prompts.py", "9.9.9",
                                "rater_manifest.json", "ratings.json",
                                "summary.json", "--output-dir")), True)
_MSG9p_ABSENT = str(drive(lambda: _R9o("require_state_rubric")(
    {"mode": R.MODE_BLIND}, _META_A, "/s")))
check("9p  ...and the absent message directs to a fresh directory, offers NO "
      "JSON snippet to paste, and says why an operator cannot establish this "
      "field by inspection the way they can a shape",
      ("--output-dir" in _MSG9p_ABSENT,
       ('"%s":' % R.RUBRIC_SHA_KEY) in _MSG9p_ABSENT,
       "none to add by hand" in _MSG9p_ABSENT,
       R.STATE_SHAPE_KEY in _MSG9p_ABSENT),
      (True, False, True, True))
# NON-DEGENERACY FOR THAT SNIPPET TEST: the shape's own evidence branch DOES
# print exactly that shape of snippet, so the absence above is a property of
# this message rather than of the substring never appearing anywhere.
check("9p  non-degeneracy: the shape guard's evidenced branch DOES print a "
      "paste-able snippet, so the absence above is a real difference",
      ('"%s":' % R.STATE_SHAPE_KEY) in _MSG_EVID, True)
# IT REACHES THE STATE FILE, and from the index rather than from a retyped
# string -- the round trip the shape guard already carries, one field over.
check("9p  main()'s state.update records the rubric digest under the module's "
      "own key constant, read from the index's rubric metadata",
      [v for k, v in (_UPDATE if isinstance(_UPDATE, list) else [])
       if k == "RUBRIC_SHA_KEY"], ["index.rubric_meta[RUBRIC_SHA_KEY]"])
check("9p  ...and lift_rubric writes the digest under that same constant, so "
      "the manifest and the state file cannot spell it two ways",
      (R.RUBRIC_SHA_KEY, R.RUBRIC_SHA_KEY in (drive(R.lift_rubric) or ("", {}))[1]),
      ("rubric_sha256", True))
check("9p  main() calls require_state_rubric, with the index's metadata",
      "require_state_rubric(state, index.rubric_meta, state_path)"
      in _MAIN_TXT, True)
check("9p  ...inside a try, exactly as often as the shape guard beside it",
      (_calls_inside_try(_main_fn, "require_state_rubric"),
       _calls_inside_try(_main_fn, "require_state_shape")), (1, 1))
# A LIVE ROUND TRIP AGAINST THE REAL RULEBOOK, and it is `lift_rubric()`
# rather than `built()`'s metadata for a reason found by running: `built`
# passes the PLANTED ``{"rubric_sha256": "x"}``, so a round trip over it
# compares "x" with "x" and an `isinstance(..., str)` probe passes on "x". The
# real digest is what `main()` compares, so it is what has to be exercised, and
# the non-degeneracy check is now that it is a 64-character hex digest.
_LIVE_META = (drive(R.lift_rubric) or ("", {}))[1]
_LIVE_SHA = walk(_LIVE_META, R.RUBRIC_SHA_KEY)
check("9p  non-degeneracy: lift_rubric really produced a 64-character hex "
      "digest, so the round trip below is over a real value and not over a "
      "placeholder",
      (isinstance(_LIVE_SHA, str), len(_LIVE_SHA) if isinstance(_LIVE_SHA, str)
       else -1,
       all(c in "0123456789abcdef" for c in _LIVE_SHA)
       if isinstance(_LIVE_SHA, str) else False), (True, 64, True))
check("9p  a resume of a state file recording the LIVE rubric proceeds",
      refusal_code(_R9o("require_state_rubric"),
                   {"mode": R.MODE_BLIND, R.RUBRIC_SHA_KEY: _LIVE_SHA},
                   _LIVE_META, "/s"), "<did not raise>")
check("9p  ...and one recording anything else does not",
      refusal_code(_R9o("require_state_rubric"),
                   {"mode": R.MODE_BLIND, R.RUBRIC_SHA_KEY: "z" * 64},
                   _LIVE_META, "/s"), "state_rubric_mismatch")
# AND THE PLANTED DIGEST built() USES IS NOT THE LIVE ONE, which is the fact
# that made the first version of the two checks above vacuous. Pinned so the
# next reader is not tempted to reach for `built()` here.
check("9p  ...and built()'s planted rubric metadata is NOT the live digest, "
      "which is why this section does not use it",
      walk(getattr(built(R.MODE_BLIND), "rubric_meta", {}),
           R.RUBRIC_SHA_KEY) == _LIVE_SHA, False)


# --- 9q -- EVERY LOCAL GUARD FIRES BEFORE THE FIRST NETWORK TOUCH ---------
#
# THE COST OF THE OLD ORDER WAS NOT THE ROUND TRIP, AND THAT WAS MEASURED
# RATHER THAN ARGUED. The five local guards sat BELOW the free
# `models.retrieve` visibility check, so on a machine that cannot reach the API
# every one of them was unreachable: a state file from the other mode, a state
# file with no recorded shape, and `--resume` against a directory with no state
# file ALL THREE reported
#
#     REFUSED: this key cannot see the judge model 'gpt-5.6-terra'
#              (APIConnectionError: Connection error.)
#
# after three attempts on api.openai.com -- three because the SDK retries. That
# is a MISDIAGNOSIS, not a wasted round trip: it sends an operator to their
# credentials when the fault is a forgotten flag or a wrong directory.
#
# THIS SECTION DRIVES THE REAL main() WITH EVERY SOCKET ENTRY POINT TRAPPED and
# requires each refusal to fire with ZERO outbound attempts. It is the only
# check in this file that goes through the entry point; the ones above are about
# the guards, and a guard that is never reached passes all of them.
#
# NO KEY, NO SPEND, NO NETWORK: the guards now run above `require_client`, so
# these invocations do not even resolve a credential -- which is itself the
# property being pinned, and check 9q's last two lines are what say so.
import socket as _socket9q                                       # noqa: E402
import traceback as _tb9q                                        # noqa: E402

_NET9q = []
_REAL9q = {"connect": _socket9q.socket.connect,
           "connect_ex": _socket9q.socket.connect_ex,
           "create_connection": _socket9q.create_connection,
           "getaddrinfo": _socket9q.getaddrinfo}


class _NetworkTouched9q(Exception):
    """Raised by the trap. Named so a recorded attempt is attributable."""


def _trap9q(name):
    def _f(*a, **_k):
        frames = [fr for fr in _tb9q.extract_stack()[:-1]
                  if "_trap9q" not in fr.name]
        where = ("%s:%d" % (_os9k.path.basename(frames[-1].filename),
                            frames[-1].lineno)) if frames else "?"
        _NET9q.append((name, where))
        raise _NetworkTouched9q("%s from %s" % (name, where))
    return _f


def _arm9q():
    _socket9q.socket.connect = _trap9q("connect")
    _socket9q.socket.connect_ex = _trap9q("connect_ex")
    _socket9q.create_connection = _trap9q("create_connection")
    _socket9q.getaddrinfo = _trap9q("getaddrinfo")


def _disarm9q():
    _socket9q.socket.connect = _REAL9q["connect"]
    _socket9q.socket.connect_ex = _REAL9q["connect_ex"]
    _socket9q.create_connection = _REAL9q["create_connection"]
    _socket9q.getaddrinfo = _REAL9q["getaddrinfo"]


# THE FIRING CONTROL, FIRST. A trap that does not fire would make every reading
# below "0 attempts" for the wrong reason, which is the shape this project
# reserves a control for.
_NET9q[:] = []
_arm9q()
try:
    _ctl = drive(_socket9q.create_connection, ("api.openai.com", 443), 1)
finally:
    _disarm9q()
check("9q  CONTROL: the trap fires on a real outbound call and records it, so "
      "a zero below is a measurement rather than an absence of instrumentation",
      (str(_ctl).startswith("<RAISED _NetworkTouched9q"), len(_NET9q)),
      (True, 1))
check("9q  ...and it is disarmed afterwards, so nothing later in this file "
      "runs under it", (_socket9q.create_connection is
                        _REAL9q["create_connection"],
                        _socket9q.socket.connect is _REAL9q["connect"]),
      (True, True))


def _run_main9q(argv):
    """(rc, network_attempts, console_text) from the REAL main(), trapped."""
    _NET9q[:] = []
    _buf = []
    _real_out = R.console.out
    R.console.out = lambda *a, **_k: _buf.append(
        " ".join(str(x) for x in a))
    _arm9q()
    try:
        rc = R.main(argv)
    except BaseException as exc:                                # noqa: BLE001
        rc = "<RAISED %s: %s>" % (type(exc).__name__, str(exc)[:120])
    finally:
        _disarm9q()
        R.console.out = _real_out
    return rc, len(_NET9q), "\n".join(_buf)


# A REAL, MINIMAL EVALUATION RUN DIRECTORY. Written here rather than borrowed
# from disk: this file may not read the project's own evaluation runs, and a
# fabricated one is what makes the drive a statement about main() rather than
# about a corpus.
_tmp9q = _tempfile9k.mkdtemp(prefix="oncotriage-rater-9q-")
try:
    _run9q = _os9k.path.join(_tmp9q, "eval_run_9q")
    _os9k.makedirs(_run9q)
    with _io9k.open(_os9k.path.join(_run9q, "p0.json"), "w",
                    encoding="utf-8") as _fh:
        json.dump({"patient_summary": {"text": "Patient: PT-9q\nAge: 61 years\n"},
                   "verdicts": [{"nct_id": "NCT09000001",
                                 "verdict_group": "matches",
                                 "inclusion_criteria": [
                                     {"criterion": "Confirmed carcinoma",
                                      "status": "met",
                                      "patient_value": "adenocarcinoma"}],
                                 "exclusion_criteria": [
                                     {"criterion": "Prior therapy",
                                      "status": "absent",
                                      "patient_value": "none"}]}]}, _fh)
    with _io9k.open(_os9k.path.join(_run9q, "manifest.json"), "w",
                    encoding="utf-8") as _fh:
        json.dump({"runs": {"pat-9q": {"file": "p0.json"}}}, _fh)
    _out9q = _os9k.path.join(_tmp9q, "out")
    _sp9q = _os9k.path.join(_out9q, R.state_filename(R.MODE_BLIND))

    def _write_state9q(payload):
        if not _os9k.path.isdir(_out9q):
            _os9k.makedirs(_out9q)
        with _io9k.open(_sp9q, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def _argv9q(*extra):
        return ["--blind", "--run-dir", _run9q, "--output-dir",
                _out9q] + list(extra)

    # IT RECORDS A MODEL, and that is not padding. Every one of the nineteen
    # real state files carries one, `require_state_model` refuses a file that
    # does not, and the first version of this fixture omitted it -- so the
    # cross-mode scenario and the non-degeneracy control below both refused at
    # the model guard instead of reaching what they were written to measure.
    # A fixture that a shipped guard rejects is not a control.
    # AND IT RECORDS A CEILING, FOR THE REASON THE PARAGRAPH ABOVE GIVES ABOUT
    # THE MODEL, one field over. `require_state_max_tokens` refuses a file that
    # records none, so without this every scenario below would refuse at the
    # ceiling guard instead of reaching what it was written to measure -- which
    # is exactly what happened when that guard was added: three checks here
    # went red, and they were the guard working rather than a defect.
    #
    # DERIVED FROM THE MODE, never `DEFAULT_MAX_TOKENS_BLIND` written out: the
    # fixture is a blind state file, `resolve_max_tokens` is what main() will
    # compare against, and a literal here would silently stop matching the day
    # the blind ceiling moves.
    _GOOD9q = {"mode": R.MODE_BLIND, "include_keys_sha256": None,
               R.STATE_SHAPE_KEY: R.REQUEST_SHAPE_VERSION,
               R.STATE_MODEL_KEY: R.DEFAULT_MODEL,
               R.STATE_MAX_TOKENS_KEY: R.resolve_max_tokens(R.MODE_BLIND),
               "batches": [{"id": "batch_9q"}]}

    # (a) THE ITEM-1 REFUSAL, at the entry point, offline.
    _rc, _n, _txt = _run_main9q(_argv9q("--resume", "batch_9q"))
    check("9q  --resume with no state file: main() returns 1, refuses by name, "
          "and touches the network ZERO times",
          (_rc, _n, "REFUSED" in _txt, "state file" in _txt), (1, 0, True, True))
    # AND THE OUTPUT DIRECTORY IS NOT CREATED. `os.makedirs(out_dir)` used to
    # precede the state read, so a refused invocation left an empty directory
    # behind -- and the next `--resume` against it then finds a directory with
    # no state file, which is this very refusal one step later.
    check("9q  ...and it creates no output directory, so a refused invocation "
          "leaves nothing behind for the next one to trip on",
          _os9k.path.isdir(_out9q), False)

    # (b) THE MODE GUARD, offline.
    _write_state9q({"mode": R.MODE_ANCHORED, "batches": [{"id": "batch_9q"}]})
    _rc, _n, _txt = _run_main9q(_argv9q("--resume", "batch_9q"))
    check("9q  a state file from the OTHER mode: 1, zero network, and the "
          "message names the two modes rather than the key",
          (_rc, _n, "written by a 'anchored' run" in _txt), (1, 0, True))

    # (c) THE SHAPE GUARD, offline.
    _write_state9q({k: v for k, v in _GOOD9q.items()
                    if k != R.STATE_SHAPE_KEY})
    _rc, _n, _txt = _run_main9q(_argv9q("--resume", "batch_9q"))
    check("9q  a state file with no recorded shape: 1, zero network",
          (_rc, _n, "records no request shape" in _txt), (1, 0, True))

    # (d) THE SUBSET GUARD, offline. A state written against an include list
    #     resumed with the flag forgotten.
    _write_state9q(dict(_GOOD9q, include_keys_sha256="f" * 64))
    _rc, _n, _txt = _run_main9q(_argv9q("--resume", "batch_9q"))
    check("9q  a state file written against a DIFFERENT subset: 1, zero "
          "network", (_rc, _n, "include list sha256" in _txt), (1, 0, True))

    # (e) THE RUBRIC GUARD, offline.
    _write_state9q(dict(_GOOD9q, rubric_sha256="e" * 64))
    _rc, _n, _txt = _run_main9q(_argv9q("--resume", "batch_9q"))
    check("9q  a state file recording a DIFFERENT rubric: 1, zero network",
          (_rc, _n, "records rubric eeeeeeeeeeee" in _txt), (1, 0, True))

    # (e2) THE MODEL GUARD, offline -- a MISMATCH. The live population: of the
    #      nineteen state files on disk, sixteen record `claude-sonnet-4-6`
    #      and were written before the 2026-09-08 port moved DEFAULT_MODEL to
    #      `gpt-5.6-terra`. Resuming one of those today is exactly this.
    _write_state9q(dict(_GOOD9q, rubric_sha256=_LIVE_SHA,
                        **{R.STATE_MODEL_KEY: "claude-sonnet-4-6"}))
    _rc, _n, _txt = _run_main9q(_argv9q("--resume", "batch_9q"))
    check("9q  a state file recording a DIFFERENT judge: 1, zero network, and "
          "the message names both models rather than the key",
          (_rc, _n, "records judge 'claude-sonnet-4-6'" in _txt,
           repr(R.DEFAULT_MODEL) in _txt), (1, 0, True, True))
    check("9q  ...and it offers --model as the remedy, which is the only "
          "same-invocation fix any of the three provenance guards has",
          "--model 'claude-sonnet-4-6'" in _txt, True)
    # (e3) THE MODEL GUARD, offline -- ABSENT.
    _write_state9q({k: v for k, v in dict(_GOOD9q,
                                          rubric_sha256=_LIVE_SHA).items()
                    if k != R.STATE_MODEL_KEY})
    _rc, _n, _txt = _run_main9q(_argv9q("--resume", "batch_9q"))
    check("9q  a state file with no recorded judge: 1, zero network, and it "
          "is NOT read as the default",
          (_rc, _n, "records no model" in _txt,
           "is NOT read as the default" in _txt), (1, 0, True, True))

    # (f) THE CROSS-MODE BATCH GUARD, offline. It reads the OTHER mode's state
    #     file, which is the one guard that consults a file this session did
    #     not write -- and it used to sit ~150 lines and one round trip below
    #     the visibility check, at the resume fork.
    # (g) THE CEILING GUARD, offline. A state file whose batches were
    # submitted at a DIFFERENT reply ceiling. It is the one provenance fault
    # that also SPENDS if it is missed -- the retry pass rebuilds from this
    # session's index and would resubmit truncations at this session's ceiling
    # doubled -- so the message is required to name both numbers and the flag.
    _write_state9q(dict(_GOOD9q, rubric_sha256=_LIVE_SHA,
                        **{R.STATE_MAX_TOKENS_KEY: 300}))
    _rc, _n, _txt = _run_main9q(_argv9q("--resume", "batch_9q"))
    check("9q  a state file recording a DIFFERENT reply ceiling: 1, zero "
          "network, and the message names both ceilings and the flag",
          (_rc, _n, "300" in _txt,
           str(R.resolve_max_tokens(R.MODE_BLIND)) in _txt,
           "--max-tokens" in _txt), (1, 0, True, True, True))
    # AND A FILE THAT RECORDS NO CEILING AT ALL IS REFUSED RATHER THAN READ AS
    # THIS SESSION'S. Every one of the nineteen state files on disk is in that
    # population, and the ones that predate the field include batches submitted
    # at 300 and at 600 -- so "absent means today's default" would attribute
    # their answers to a ceiling that did not produce them.
    _write_state9q({k: v for k, v in dict(_GOOD9q,
                                          rubric_sha256=_LIVE_SHA).items()
                    if k != R.STATE_MAX_TOKENS_KEY})
    _rc, _n, _txt = _run_main9q(_argv9q("--resume", "batch_9q"))
    check("9q  a state file with no recorded ceiling: 1, zero network, and it "
          "is NOT read as this session's",
          (_rc, _n, "no max_tokens" in _txt), (1, 0, True))

    # THE LIVE DIGEST, not built()'s planted one: main() lifts the real
    # rulebook, so a planted digest here makes the rubric guard refuse first
    # and this check measure that instead of the cross-mode one.
    _write_state9q(dict(_GOOD9q, rubric_sha256=_LIVE_SHA,
                        batches=[{"id": "batch_mine"}]))
    # THE MODEL IS THE SHIPPED DEFAULT HERE, from `_GOOD9q`, so the model guard
    # passes and this check measures the cross-mode guard rather than it.
    with _io9k.open(_os9k.path.join(_out9q, R.state_filename(R.MODE_ANCHORED)),
                    "w", encoding="utf-8") as _fh:
        json.dump({"mode": R.MODE_ANCHORED,
                   "batches": [{"id": "batch_theirs"}]}, _fh)
    _rc, _n, _txt = _run_main9q(_argv9q("--resume", "batch_theirs"))
    check("9q  a batch the ANCHORED state file claims, resumed blind: 1, zero "
          "network, and the message names the flag rather than the rater",
          (_rc, _n, "was submitted by a 'anchored' run" in _txt),
          (1, 0, True))

    # THE NON-DEGENERACY CONTROL FOR ALL OF THEM. An invocation whose state file is
    # entirely in order must get PAST every local guard and only then touch the
    # network -- otherwise "zero attempts" above would be satisfied by a main()
    # that refuses everything for some unrelated reason.
    _os9k.remove(_os9k.path.join(_out9q, R.state_filename(R.MODE_ANCHORED)))
    _write_state9q(dict(_GOOD9q, rubric_sha256=_LIVE_SHA))
    _prev_key = _os9k.environ.get("OPENAI_API_KEY")
    _os9k.environ["OPENAI_API_KEY"] = (
        "-".join(("sk", "9q", "not", "a", "real", "key")) + "0" * 12)
    try:
        _rc, _n, _txt = _run_main9q(_argv9q("--resume", "batch_9q"))
    finally:
        if _prev_key is None:
            _os9k.environ.pop("OPENAI_API_KEY", None)
        else:
            _os9k.environ["OPENAI_API_KEY"] = _prev_key
    check("9q  CONTROL: a state file in order gets PAST every local guard and "
          "reaches the network -- so the zeros above are the guards "
          "firing, not main() refusing for something else",
          (_n > 0, "REFUSED" in _txt), (True, True))
    check("9q  ...and what it reaches the network FOR is the visibility check, "
          "which is the one thing below that genuinely needs the wire",
          "cannot see the judge model" in _txt, True)
finally:
    _shutil9k.rmtree(_tmp9q, ignore_errors=True)
check("9q  the temp directory is gone", _os9k.path.isdir(_tmp9q), False)

# THE ORDER IS PINNED STRUCTURALLY TOO, because the drive above proves it for
# the invocations it drives and an AST pin proves it for every one. Both are
# needed: a scan cannot see a guard that is called and whose refusal is
# swallowed, and a drive cannot see a seventh guard added below the network.
_LOCAL_GUARDS9q = ("require_state_for_resume", "require_state_mode",
                   "require_state_subset", "require_state_shape",
                   "require_state_rubric", "require_state_model",
                   "require_state_max_tokens",
                   "refuse_batch_from_other_mode")
# AND THE LIST IS CHECKED AGAINST THE MODULE, which is what stops the pin
# under-reporting rather than failing. The check below asks "is every guard in
# this tuple above the network"; a guard added to the module and NOT to the
# tuple is simply not asked about, so the pin goes on passing while covering
# less -- the corpus-that-silently-shrinks shape, and it is not hypothetical:
# `require_state_max_tokens` was added and every check here stayed green until
# this comparison was written.
_MODULE_GUARDS9q = tuple(sorted(
    n for n in dir(R)
    if n.startswith("require_state_") and callable(getattr(R, n, None))))
check("9q  the pinned guard list IS every require_state_* the module defines "
      "-- so a guard added without an entry here fails rather than going "
      "unchecked",
      sorted(g for g in _LOCAL_GUARDS9q if g.startswith("require_state_")),
      list(_MODULE_GUARDS9q))
check("9q  non-degeneracy: the module really defines several of them, so the "
      "comparison above is not two empty lists",
      len(_MODULE_GUARDS9q) >= 6, True)


def _first_at9q(needle):
    """Where ``needle`` first appears in main()'s unparsed source, or -1."""
    return _MAIN_TXT.find(needle)


_NET_CALL9q = _first_at9q("model_is_visible(")
check("9q  main() calls model_is_visible (probe, so the ordering pins below "
      "cannot pass over an absent needle)", _NET_CALL9q >= 0, True)
check("9q  EVERY local provenance guard is called before it, in main()'s own "
      "source order",
      [g for g in _LOCAL_GUARDS9q
       if not (0 <= _first_at9q(g + "(") < _NET_CALL9q)], [])
check("9q  ...and require_client, which is local but was above them, is now "
      "below -- so a refused state file needs no credential at all",
      0 <= _first_at9q("require_client()") and
      all(_first_at9q(g + "(") < _first_at9q("require_client()")
          for g in _LOCAL_GUARDS9q), True)
check("9q  ...and the output directory is created only after the guards, so a "
      "refusal leaves nothing on disk",
      0 <= _first_at9q("makedirs(out_dir") and
      all(_first_at9q(g + "(") < _first_at9q("makedirs(out_dir")
          for g in _LOCAL_GUARDS9q), True)
# THE RESUME IDS ARE PARSED ONCE. `refuse_batch_from_other_mode` had to move
# above the fork that used to build them, so the list is built early and the
# fork reads it -- and a second `args.resume.split` would be a second list that
# could disagree with the one the guard checked.
check("9q  the resume ids are parsed exactly once in main(), so the list the "
      "cross-mode guard checked is the list that gets polled",
      _MAIN_TXT.count("args.resume.split(',')")
      + _MAIN_TXT.count('args.resume.split(",")'), 1)


# --- 9r -- ...AND WHO ANSWERED THEM --------------------------------------
#
# `model` was written into the state file by the commit that ADDED this module
# and was read back by NOTHING, so a resume across a judge change joined one
# model's answers onto artifacts recording another. THE POPULATION IS ON DISK:
# commit d0f4519 moved DEFAULT_MODEL from `claude-sonnet-4-6` to
# `gpt-5.6-terra`, and SIXTEEN of the nineteen state files under
# 09- Testing/Evaluation Runs/ were written before it and record the Anthropic
# judge; the other three are item 11's two blind sessions and the
# criteria-reference probe. Counted, not estimated.
_MODEL_A = "gpt-5.6-terra"
_MODEL_B = "claude-sonnet-4-6"
_MODEL_STATE = {"mode": R.MODE_BLIND, "include_keys_sha256": None,
                R.STATE_SHAPE_KEY: R.REQUEST_SHAPE_VERSION,
                R.RUBRIC_SHA_KEY: "a" * 64,
                R.STATE_MODEL_KEY: _MODEL_A,
                "batches": [{"id": "batch_model"}]}
check("9r  the key constant is the name the artifacts already use, so a "
      "refusal and the manifest beside it are one spelling",
      R.STATE_MODEL_KEY, "model")
check("9r  an EQUAL model proceeds, and the guard hands back the value it "
      "proved equal",
      drive(_R9o("require_state_model"), dict(_MODEL_STATE), _MODEL_A, "/s"),
      _MODEL_A)
check("9r  a DIFFERENT recorded model refuses, by its own code",
      refusal_code(_R9o("require_state_model"), dict(_MODEL_STATE),
                   _MODEL_B, "/s"), "state_model_mismatch")
# ABSENT REFUSES, AND THE POLICY IS FROM A SURVEY. Like the rubric and unlike
# the shape, the absent population is EMPTY: all nineteen state files on disk
# carry `model`, every one as a string, and the field was in the commit that
# added this module. So absence means the file was not written by this writer,
# and there is no legacy resume to strand.
check("9r  an ABSENT model refuses, under a code of its own",
      refusal_code(_R9o("require_state_model"),
                   {k: v for k, v in _MODEL_STATE.items()
                    if k != R.STATE_MODEL_KEY}, _MODEL_A, "/s"),
      "state_model_absent")
check("9r  ...and it is NOT read as this session's model, which is the "
      "guess that would attribute one judge's answers to another",
      refusal_code(_R9o("require_state_model"),
                   {"mode": R.MODE_BLIND, "batches": [{"id": "b"}]},
                   R.DEFAULT_MODEL, "/s"), "state_model_absent")
check("9r  ...and the three codes are distinct, because the remedies are",
      len({refusal_code(_R9o("require_state_model"), dict(_MODEL_STATE),
                        _MODEL_B, "/s"),
           refusal_code(_R9o("require_state_model"),
                        {k: v for k, v in _MODEL_STATE.items()
                         if k != R.STATE_MODEL_KEY}, _MODEL_A, "/s"),
           refusal_code(_R9o("require_state_model"),
                        dict(_MODEL_STATE, model=1), _MODEL_A, "/s")}), 3)
# TYPE CONFUSION, the rubric guard's finding inherited rather than repeated: a
# model id is a string, so no bool, int, float, list or dict equals one and an
# `isinstance(...) and found == want` guard on the EQUALITY would be dead code.
# The type decision is therefore a branch of its own ABOVE the equality, and
# what it buys is the right DIAGNOSIS -- a file that is not a record of
# anything, which is a different remedy from "the judge changed".
check("9r  a non-string model is its own refusal rather than a mismatch: "
      "true, 1, 1.0, a list and a dict all land in the malformed branch",
      tuple(refusal_code(_R9o("require_state_model"),
                         dict(_MODEL_STATE, model=v), _MODEL_A, "/s")
            for v in (True, 1, 1.0, [_MODEL_A], {"model": _MODEL_A})),
      ("state_model_malformed",) * 5)
check("9r  ...and a string that merely differs is a MISMATCH, so the type "
      "branch has not swallowed the value branch",
      refusal_code(_R9o("require_state_model"),
                   dict(_MODEL_STATE, model=_MODEL_B), _MODEL_A, "/s"),
      "state_model_mismatch")
# THE ORDER IS PINNED, because it is what makes every branch reachable: absent,
# then type, then value. Put the equality first and a non-string is a mismatch;
# put the type check above the absent check and `None` is reported as a
# malformed id.
_MODEL_SRC = _ast9k.unparse(next(
    (n for n in _ast9k.walk(_MAIN_SRC)
     if isinstance(n, _ast9k.FunctionDef)
     and n.name == "require_state_model"), _ast9k.parse("def _m(): pass")))
_MORDER = {c: _MODEL_SRC.find(c)
           for c in ("state_model_absent", "state_model_malformed",
                     "state_model_mismatch", "found == model")}
check("9r  the guard tests absent, then type, then value -- in that order, so "
      "no branch is unreachable",
      (all(v >= 0 for v in _MORDER.values()),
       _MORDER["state_model_absent"] < _MORDER["state_model_malformed"]
       < _MORDER["found == model"] < _MORDER["state_model_mismatch"]),
      (True, True))
check("9r  ...and the equality is BARE, not guarded by an isinstance that "
      "cannot change an outcome -- the type branch above it is what decides",
      "isinstance(found, str) and found == model" in _MODEL_SRC, False)
# WEAKENED-COMPARISON ANALOGUES. A model id is a string with structure -- a
# family prefix and a version -- so the plausible weakenings are a prefix
# match, a case-fold and a "family" match. All three would ACCEPT a real judge
# change: `claude-sonnet-4-6` and `claude-sonnet-4-5` share a prefix and a
# family, and `GPT-5.6-Terra` differs from `gpt-5.6-terra` only in case while
# naming the same weights nobody has proved are the same weights.
check("9r  the comparison is not on a prefix: an id sharing every character "
      "but the last still refuses",
      refusal_code(_R9o("require_state_model"),
                   dict(_MODEL_STATE, model="claude-sonnet-4-5"),
                   "claude-sonnet-4-6", "/s"), "state_model_mismatch")
check("9r  ...nor on the family: two ids sharing a vendor prefix and nothing "
      "else still refuse",
      refusal_code(_R9o("require_state_model"),
                   dict(_MODEL_STATE, model="gpt-4o"), _MODEL_A, "/s"),
      "state_model_mismatch")
check("9r  ...nor case-folded: an id differing only in case still refuses, "
      "because nothing establishes that two spellings name one set of weights",
      refusal_code(_R9o("require_state_model"),
                   dict(_MODEL_STATE, model=_MODEL_A.upper()), _MODEL_A, "/s"),
      "state_model_mismatch")
check("9r  ...and whitespace is not stripped either -- a padded id is a "
      "different string and this guard does not decide it is not",
      refusal_code(_R9o("require_state_model"),
                   dict(_MODEL_STATE, model=" " + _MODEL_A), _MODEL_A, "/s"),
      "state_model_mismatch")
_MSG9r = str(drive(lambda: _R9o("require_state_model")(
    dict(_MODEL_STATE), _MODEL_B, "/s")))
check("9r  the mismatch message states BOTH models, names what joining them "
      "corrupts, and offers the two remedies",
      all(w in _MSG9r for w in (repr(_MODEL_A), repr(_MODEL_B),
                                "rater_manifest.json", "ratings.json",
                                "summary.json", "--model", "--output-dir")),
      True)
# THE OVERWRITE IS THE SHARPEST CONSEQUENCE AND THE MESSAGE SAYS SO. main()'s
# state.update runs BELOW these guards and is written out on the resume path
# before any batch is polled, so a resume under a different model destroys the
# record of which model was asked -- the mistake erases its own evidence.
check("9r  ...and it names the overwrite, which is the consequence no later "
      "check could recover from",
      ("destroys the evidence" in _MSG9r, "before the first poll" in _MSG9r),
      (True, True))
_MSG9r_ABSENT = str(drive(lambda: _R9o("require_state_model")(
    {"mode": R.MODE_BLIND, "batches": [{"id": "b1"}]}, _MODEL_A, "/s")))
check("9r  the absent message directs to a fresh directory and offers NO "
      "paste-able snippet, because the absent population is empty and a "
      "hand-written id would be adopted as fact",
      ("--output-dir" in _MSG9r_ABSENT,
       ('"%s":' % R.STATE_MODEL_KEY) in _MSG9r_ABSENT), (True, False))
# NON-DEGENERACY FOR THAT SNIPPET TEST: the shape guard's evidenced branch DOES
# print exactly that shape of snippet, so the absence above is a property of
# this message rather than of the substring never appearing anywhere.
check("9r  non-degeneracy: the shape guard's evidenced branch DOES print a "
      "paste-able snippet, so the absence above is a real difference",
      ('"%s":' % R.STATE_SHAPE_KEY) in _MSG_EVID, True)
# WHERE THE EVIDENCE EXISTS THE CHECK IS NAMED, and where it does not the
# message says so rather than naming a check that cannot be run. Same shape as
# the shape guard's, and the evidence is the same file: `batch_jsonl` writes
# `model` into every uploaded request body.
_MSG9r_EVID = str(drive(lambda: _R9o("require_state_model")(
    {"mode": R.MODE_BLIND,
     "batches": [{"id": "b1", "input_file_id": "file-9r"}]}, _MODEL_A, "/s")))
check("9r  ...and where an input_file_id exists the message names the check "
      "over it, the batch it belongs to, and that it bills nothing",
      all(w in _MSG9r_EVID for w in ("b1 -> file-9r", "files.content",
                                     "bills nothing", "`model` field")), True)
check("9r  ...while a file recording no input_file_id is told there is no "
      "handle at all, rather than being told to run a check it cannot",
      ("input_file_id" in _MSG9r_ABSENT, "files.content" in _MSG9r_ABSENT),
      (True, False))
# AND THE EVIDENCE THE MESSAGE NAMES IS REAL: the uploaded request body carries
# the model. Read off the shipped builder rather than asserted.
_BODY9r = walk(drive(R.to_batch_line,
                     {"custom_id": "c", "params": {"model": _MODEL_A}}),
               "body", "model")
check("9r  non-degeneracy: the uploaded request line really carries the "
      "model, so the check the absent message names can actually be run",
      _BODY9r, _MODEL_A)
# IT REACHES THE STATE FILE, from args rather than from a retyped string.
check("9r  main()'s state.update records the judge under the module's own key "
      "constant, from the parsed argument",
      [v for k, v in (_UPDATE if isinstance(_UPDATE, list) else [])
       if k == "STATE_MODEL_KEY"], ["args.model"])
check("9r  ...and it does so under the constant rather than a literal, so the "
      "writer and the guard cannot spell one fact two ways",
      [k for k, _v in (_UPDATE if isinstance(_UPDATE, list) else [])
       if k == "'model'"], [])
check("9r  main() calls require_state_model, with the parsed model argument",
      "require_state_model(state, args.model, state_path)" in _MAIN_TXT, True)
check("9r  ...inside a try, exactly as often as the rubric guard beside it",
      (_calls_inside_try(_main_fn, "require_state_model"),
       _calls_inside_try(_main_fn, "require_state_rubric")), (1, 1))
# THE PLACEMENT IS PINNED. It is the third of the provenance trio and it runs
# LAST of them, because its remedy is a flag while shape's and rubric's are
# different code or a different directory -- so an operator meets the widest
# fault first. And it runs BEFORE the cross-mode guard, which reads a file this
# session did not write.
check("9r  it runs after the shape and rubric guards and before the "
      "cross-mode one, in main()'s own source order",
      [g for g in ("require_state_shape", "require_state_rubric",
                   "require_state_model", "refuse_batch_from_other_mode")
       if _MAIN_TXT.find(g + "(") >= 0]
      == sorted(("require_state_shape", "require_state_rubric",
                 "require_state_model", "refuse_batch_from_other_mode"),
                key=lambda g: _MAIN_TXT.find(g + "(")), True)
# AND THE COLLECTION-TIME REPORT IS STILL THERE. This guard is its complement,
# not its replacement: that one reads the ids off the RESPONSES, after the poll
# and the spend, and prints rather than refusing.
check("9r  the collection-time divergence report survives, so the two "
      "measurements -- what was ASKED and what ANSWERED -- both remain",
      ("ANSWERING MODEL(S) differ" in _MAIN_TXT,
       "answering_models" in _MAIN_TXT), (True, True))

# --- 9s -- THE REPLY CEILING IS PROVENANCE TOO ----------------------------
#
# ** THE ITEM THIS SECTION CLOSES WAS NOT THE ONE IT WAS ASKED TO CLOSE, AND
# ** THE DIFFERENCE IS THE FINDING.
#
# The ask was to raise "the anchored comparison arm's 300-token ceiling, which
# permanently loses 0.63% of decisions to truncation". Verified first, and the
# premise does not hold of this tree:
#
#   * `R.DEFAULT_MAX_TOKENS` is 4096 and has been since the OpenAI port. 9m
#     pins it, and pins that it is at least 4x the pre-port 300.
#   * The surviving 300 is a literal `built()` in this file hands
#     `build_requests`, so that 8a's historical hash stays PRODUCIBLE. It
#     governs no paid request, and 9m's own control pins that it does not move.
#     Raising it would BREAK 8a -- `_as_anthropic_body` emits it as
#     `max_tokens` and the pin hashes it -- which is the opposite of the ask.
#   * The 0.63% is a measurement of six `rater_pack_validation_20260812` runs
#     submitted at 300, before the port. It is history; no constant moved today
#     un-loses those decisions.
#
# WHAT WAS ACTUALLY MISSING IS A GUARD, and it is the live half of the same
# thought: the ceiling IS part of the request identity -- `max_completion_
# tokens` is a serialized field of every request body -- and nothing recorded
# or compared it. `main()` wrote mode, subset, shape, rubric and model into the
# state file and not this.
#
# AND IT IS THE ONE PROVENANCE FIELD WHOSE MISMATCH ALSO SPENDS. The other four
# mislabel a session. This one reaches the retry pass, which rebuilds from
# `index.requests` -- built at THIS session's ceiling -- and resubmits
# truncations at `args.max_tokens * 2`. A batch submitted at 300 and resumed
# today retries at 8192.
_MT_STATE = {R.STATE_MAX_TOKENS_KEY: 300, "batches": [{"id": "b1"}]}
_MT_FID = {R.STATE_MAX_TOKENS_KEY: None,
           "batches": [{"id": "b1", "input_file_id": "file_x"}]}
_R9s = _R9o                     # the same by-name accessor sections 9o/9r use

check("9s  a first submit is not a disagreement -- no state file, nothing to "
      "compare", drive(_R9s("require_state_max_tokens"), {}, 4096, "/s"), None)
check("9s  the same ceiling passes and returns it",
      drive(_R9s("require_state_max_tokens"),
            {R.STATE_MAX_TOKENS_KEY: 4096}, 4096, "/s"), 4096)
check("9s  a DIFFERENT ceiling refuses",
      refusal_code(_R9s("require_state_max_tokens"), dict(_MT_STATE), 4096,
                   "/s"), "state_max_tokens_mismatch")
check("9s  an ABSENT ceiling refuses rather than being read as this "
      "session's -- the files that predate the field include 300- and "
      "600-ceiling batches",
      refusal_code(_R9s("require_state_max_tokens"),
                   {"batches": [{"id": "b1"}]}, 4096, "/s"),
      "state_max_tokens_absent")
# ── THE THREE CODES ARE DISTINCT, because their remedies are: adopt from
#    evidence, correct a malformed value, or pass --max-tokens.
check("9s  absent, malformed and mismatch are three codes, not one",
      len({refusal_code(_R9s("require_state_max_tokens"),
                        {"batches": [{"id": "b1"}]}, 4096, "/s"),
           refusal_code(_R9s("require_state_max_tokens"),
                        {R.STATE_MAX_TOKENS_KEY: {}}, 4096, "/s"),
           refusal_code(_R9s("require_state_max_tokens"),
                        dict(_MT_STATE), 4096, "/s")}), 3)
# ── EVERY JSON TYPE IS DRIVEN, AND THIS IS THE CHECK THAT FOUND A REAL BUG IN
#    THE GUARD. Its first draft followed `require_state_shape` and funnelled
#    every non-int into the MISMATCH branch -- which is sound there, because
#    that message only QUOTES the value. This one does ARITHMETIC on it (the
#    refusal states what the retry would resubmit at, which is `found * 2`),
#    and `{} * 2` is a TypeError. A state file recording an object turned the
#    refusal into an uncaught traceback out of main()'s `except RaterRefusal`
#    -- the abort-instead-of-refuse shape this project has shipped eighteen
#    times. Found by driving every type, not by reading.
for _bad, _label in (({}, "an object"), ([], "a list"), ("4096", "a string"),
                     (4096.0, "a float"), (True, "a bool")):
    check(f"9s  {_label} is a NAMED refusal and not a traceback",
          refusal_code(_R9s("require_state_max_tokens"),
                       {R.STATE_MAX_TOKENS_KEY: _bad}, 4096, "/s"),
          "state_max_tokens_malformed")
# ── bool AND float SPECIFICALLY, because `True == 1` and `4096.0 == 4096`.
#    This refusal invites a hand edit, so a hand-edited file is exactly where
#    they turn up, and a bare comparison would ADOPT either as a claim nobody
#    made. `resolve_max_tokens` refuses the same two on the way in.
check("9s  a bool is not adopted as 1 even when the session ceiling IS 1",
      refusal_code(_R9s("require_state_max_tokens"),
                   {R.STATE_MAX_TOKENS_KEY: True}, 1, "/s"),
      "state_max_tokens_malformed")
check("9s  ...and a float is not adopted as the equal int",
      refusal_code(_R9s("require_state_max_tokens"),
                   {R.STATE_MAX_TOKENS_KEY: 4096.0}, 4096, "/s"),
      "state_max_tokens_malformed")
# ── THE ADOPTION IS EVIDENCE-CONDITIONED, on `require_state_shape`'s pattern:
#    an input_file_id is the ONLY handle on what was sent, so where there is
#    none the invitation is WITHDRAWN rather than softened.
_MT_NOEV = str(drive(lambda: _R9s("require_state_max_tokens")(
    {"batches": [{"id": "b1"}]}, 4096, "/s")))
_MT_EV = str(drive(lambda: _R9s("require_state_max_tokens")(
    dict(_MT_FID, **{R.STATE_MAX_TOKENS_KEY: None}), 4096, "/s")))
check("9s  with no input_file_id the refusal offers NO way to hand-write the "
      "ceiling and directs to --output-dir",
      ("NO EVIDENCE TO ADOPT FROM" in _MT_NOEV,
       "--output-dir" in _MT_NOEV,
       "max_completion_tokens" in _MT_NOEV), (True, True, False))
check("9s  ...and with one it names the file, the field to read and the key "
      "to record",
      all(w in _MT_EV for w in ("file_x", "max_completion_tokens",
                                "client.files.content",
                                R.STATE_MAX_TOKENS_KEY)), True)
check("9s  non-degeneracy: the two messages really differ, so the pair above "
      "is not one message compared with itself", _MT_NOEV != _MT_EV, True)
# ── THE MISMATCH MESSAGE NAMES THE SPEND, which is what separates this guard
#    from the other four.
_MT_MSG = str(drive(lambda: _R9s("require_state_max_tokens")(
    dict(_MT_STATE), 4096, "/s")))
check("9s  the mismatch message states BOTH ceilings, what the retry would "
      "resubmit at, and the flag that repairs it",
      all(w in _MT_MSG for w in ("300", "4096", "8192", "--max-tokens",
                                 "--output-dir")), True)
check("9s  ...and it says the retry SPENDS, not merely that a label is wrong",
      "money" in _MT_MSG, True)
# ── THE BRANCH ORDER, on 9r's pin and for its reason: absent, then type, then
#    value. Put the equality first and a non-int is a mismatch; put the type
#    check above the absent one and `None` is reported as malformed.
_MT_SRC = _ast9k.unparse(next(
    (n for n in _ast9k.walk(_MAIN_SRC)
     if isinstance(n, _ast9k.FunctionDef)
     and n.name == "require_state_max_tokens"),
    _ast9k.parse("def _m(): pass")))
_MTORD = {c: _MT_SRC.find(c)
          for c in ("state_max_tokens_absent", "state_max_tokens_malformed",
                    "found == max_tokens", "state_max_tokens_mismatch")}
check("9s  the guard tests absent, then type, then value -- in that order, so "
      "no branch is unreachable",
      (all(v >= 0 for v in _MTORD.values()),
       _MTORD["state_max_tokens_absent"] < _MTORD["state_max_tokens_malformed"]
       < _MTORD["found == max_tokens"] < _MTORD["state_max_tokens_mismatch"]),
      (True, True))
# ── AND THE CEILING IS WRITTEN, without which the guard can only ever refuse.
check("9s  main() records the ceiling in the state file under the module's "
      "own key constant",
      "STATE_MAX_TOKENS_KEY: args.max_tokens" in _MAIN_TXT, True)
check("9s  ...and it is `args.max_tokens`, which `_prepare` already resolved "
      "and wrote back -- not a second resolve that could name a ceiling the "
      "wire never carried",
      "STATE_MAX_TOKENS_KEY: resolve_max_tokens(" in _MAIN_TXT, False)

# --- 9s -- THE PROVENANCE KEY LIST IS DERIVED, NOT RETYPED ----------------
#
# `require_state_for_resume`'s refusal used to enumerate FOUR provenance keys
# by hand and its docstring counted to four in three sentences. It was written
# when there were four guards; `require_state_model` was added afterwards and
# neither moved -- so for two passes the message that tells an operator what an
# unverified resume leaves unknown DID NOT MENTION THE JUDGE, the widest of
# them. Nothing failed, because a message is not a check.
check("9s  STATE_PROVENANCE_KEYS names one key per require_state_* guard that "
      "compares a field (every guard except the resume gate itself, which "
      "compares nothing)",
      len(R.STATE_PROVENANCE_KEYS),
      len([g for g in _MODULE_GUARDS9q if g != "require_state_for_resume"]))
check("9s  ...and every member is the module's own key constant, so a refusal "
      "cannot name a key that does not exist",
      sorted(R.STATE_PROVENANCE_KEYS),
      sorted({"mode", R.INCLUDE_KEYS_STATE_KEY, R.STATE_SHAPE_KEY,
              R.RUBRIC_SHA_KEY, R.STATE_MODEL_KEY, R.STATE_MAX_TOKENS_KEY}))
_RESUME_MSG = str(drive(lambda: R.require_state_for_resume(
    {}, "batch_x", "/s")))
check("9s  the resume refusal RENDERS that tuple, so every provenance field "
      "is named -- including the judge, which the hand-written list omitted",
      [k for k in R.STATE_PROVENANCE_KEYS if k not in _RESUME_MSG], [])
check("9s  non-degeneracy: the refusal really was produced (an empty message "
      "would satisfy the check above vacuously)",
      "resume_without_state" in str(
          drive(lambda: refusal_code(R.require_state_for_resume, {},
                                     "batch_x", "/s"))), True)



print()
# RELEASED ABOVE THE SUMMARY, NEVER BELOW IT: a release below the results line
# still decides the exit code while being absent from the number printed.
_QUOTA_WHO, _QUOTA_RESTORED = _provider_pin.release_test_quotas()
check("[provider pin] the test quota limits this file installed were released, "
      "and both tables are back to the shipped values",
      (_QUOTA_WHO, _QUOTA_RESTORED), (_PIN_WHO, True))

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
