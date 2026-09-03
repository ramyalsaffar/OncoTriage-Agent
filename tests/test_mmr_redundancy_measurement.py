# The MMR redundancy measurement: its arithmetic, and its refusals
##################################################################

"""MMR Redundancy Measurement Test

``oncotriage/evaluation/mmr_redundancy.py`` is MEASUREMENT-ONLY code that no
pipeline path imports -- and that is exactly why it needs this file. Nothing
downstream fails when it is wrong: it produces tables, a markdown report and a
verdict against a pre-registered rule, and a defect in any of them reaches the
operator as a confident number rather than as an error. The whole artefact is
evidence for a decision about what Stage 5 judges, so its arithmetic has to be
checkable without re-running the retrieval it summarises.

WHAT IS PINNED, AND WHY EACH ONE WOULD BE SILENT IF IT BROKE
--------------------------------------------------------------
*   **The lambda = 1 identity.** At lambda 1 the diversity term vanishes and
    MMR must reproduce the shipped kept-k exactly. Break the relevance
    normalisation and the report still renders -- it reports swaps, and every
    swap count below becomes a measurement of that bug wearing the label of a
    finding about MMR.
*   **The tie-break.** A selector that resolves ties by list order gives one
    answer on the pool as built and another on the same pool built in a
    different order, and the run's own determinism probe would still pass
    because it re-runs the SAME list twice.
*   **The cap context manager.** It lifts ``MAX_TRIALS_FOR_EVALUATION`` in
    ``oncotriage/agent/filtering.py``'s OWN namespace, because that module
    binds the value with a ``from ... import``. Patch ``config`` instead and
    the lift reaches nothing: the "uncapped" pool is the capped one, every rank
    below the cut is reported as absent, and the report says the cap never
    bound. That is ``tests/test_agent_rrf_config_ownership.py``'s patch-point
    lesson, one module over, and section 3 fires the wrong patch point
    deliberately and requires it to change nothing.
*   **The swap classification.** ``classify_swaps`` decides whether a removed
    trial was represented by a retained one. Let it check similarity against
    the trials MMR DROPPED rather than the ones it KEPT and every removal looks
    benign -- the number the pre-registered rule turns on inverts, silently, in
    the direction that argues for adopting the change.
*   **The rule itself.** ``apply_rule`` is the one place "material" and
    "almost entirely" are numbers. Section 6 drives synthetic summaries at
    both sides of all three thresholds, so a constant moved after the fact
    fails here rather than changing a published verdict.

EVERY CONTROL IS A DIFFERENT INPUT TO A PURE FUNCTION, which is the natural
control for a pure function and is what keeps this file out of
``_EXEC_ALLOWLIST``: it execs nothing and loads no module by location. The two
exceptions are the cap context manager, whose subject IS a module attribute and
which is driven by rebinding and asserting the restore, and section 3's wrong
patch point, which is an attribute set inside ``try``/``finally`` with the
restore asserted.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO CORPUS, NO DATABASE, NO GIT
HISTORY, NO LIVE SERVER. NO MODEL IS LOADED -- ``ONCOTRIAGE_DEFER_LOCAL_MODELS``
is set above the imports (the ordering lesson from pass 20c-3d) and section 8
asserts ``torch`` and ``transformers`` never entered ``sys.modules``. Stage 5 is
not reachable from anything here: the module under test imports no evaluation
node, and section 8 asserts that too.

IT WRITES NOTHING ANYWHERE, not even a temp directory, so it is NOT in
``tests/run_serial_tests.py``'s collision matrix -- derived rather than
assumed: the one repository file it READS is
``oncotriage/evaluation/mmr_redundancy.py``, which is written by neither
``tests/test_registries_cancer_code_claims_audit_control.py`` (which writes
``oncotriage/registries/cancer_code_registry.py``) nor
``tests/test_config_snapshot_date_rot.py`` (which writes
``oncotriage/config.py``).

    python tests/test_mmr_redundancy_measurement.py
"""

import ast
import hashlib
import os
import sys

# ABOVE THE PACKAGE IMPORTS ON PURPOSE. oncotriage/agent/deps.py reads this
# variable ONCE, at its own import, and `deps` arrives transitively on the
# first `oncotriage` import -- so an assignment underneath the imports reaches
# nothing and MedCPT loads for real.
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

import numpy as np

from oncotriage import config
from oncotriage.agent import filtering
from oncotriage.evaluation import mmr_redundancy as M


# ===========================================================================
# HARNESS
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


def check_true(label: str, condition) -> None:
    check(label, bool(condition), True)


def guarded(fn, *args, **kwargs):
    """Call ``fn`` and convert a raise into a value ``check`` fails on.

    THE ABORT SHAPE THIS PROJECT HAS SHIPPED SEVENTEEN TIMES. A bare call
    inside a ``check(...)`` argument list raises WHILE THE ARGUMENT IS BEING
    EVALUATED, so a defect that makes the function raise -- which is precisely
    what several of these sections exist to catch -- produces one traceback
    where the file owes a summary and every remaining result.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:                                    # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


def section(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


_SOURCE_PATH = os.path.abspath(M.__file__)
_SOURCE_BEFORE = hashlib.sha256(open(_SOURCE_PATH, "rb").read()).hexdigest()


# ===========================================================================
# 1. THE TEXT SIGNAL
# ===========================================================================
section("1. TF-IDF cosine -- the similarity every table and every swap reads")

_DUP_A = "patients with metastatic breast cancer and at least one prior line"
_DUP_B = "patients with metastatic breast cancer and at least one prior line"
_FAR = "subjects with advanced melanoma receiving checkpoint immunotherapy"

_rows = M.tfidf_matrix([_DUP_A, _DUP_B, _FAR, ""])
_sim = M.cosine_matrix(_rows)

check("1a. identical criteria texts score exactly 1.0",
      round(float(_sim[0, 1]), 9), 1.0)
check_true("1b. unrelated criteria texts score below the headline threshold",
           _sim[0, 2] < M.NEAR_DUPLICATE_THRESHOLD)
# NON-DEGENERACY. Without this, 1b is equally satisfied by a matrix of zeros --
# which is what a broken vectoriser produces, and it would read as "no
# redundancy anywhere" rather than as a defect.
check_true("1b-i. ...and is not zero, so 1b is about the texts rather than "
           "about an empty vocabulary", _sim[0, 2] > 0.0)
check_true("1c. an empty criteria text is an all-zero row",
           bool(np.allclose(_rows[3], 0.0)))
check("1d. ...so it is never reported as a duplicate of anything",
      [float(_sim[3, 0]), float(_sim[3, 2])], [0.0, 0.0])
check("1e. the diagonal is exactly 1.0",
      bool(np.allclose(np.diag(_sim), 1.0)), True)
check_true("1f. every cosine is inside [0, 1] -- clipped, so a 1.0000000002 "
           "cannot be read against a threshold as a miss",
           _sim.min() >= 0.0 and _sim.max() <= 1.0)

# THE TOKENIZER IS THE PIPELINE'S. A second tokenizer here would be a second
# vocabulary that could disagree with the criteria-bm25 channel about what a
# term is; this asserts the module reads the shipped one rather than its own.
_src = ast.parse(open(_SOURCE_PATH).read())
_imported = {a.name or a.asname
             for n in ast.walk(_src) if isinstance(n, ast.ImportFrom)
             for a in n.names if (n.module or "") == "oncotriage.agent.text"}
check("1g. the similarity tokenizes with the pipeline's own tokenize_for_bm25",
      sorted(_imported), ["tokenize_for_bm25"])

# CASE AND PUNCTUATION FOLD, because tokenize_for_bm25 folds them. Stated as a
# check rather than inherited silently: it is why two registrations of one
# trial's text that differ only in punctuation are one document here.
check("1h. case and trailing punctuation do not separate two identical texts",
      round(float(M.cosine_matrix(M.tfidf_matrix(
          ["Metastatic Breast Cancer, prior therapy.",
           "metastatic breast cancer prior therapy"]))[0, 1]), 9), 1.0)


# ===========================================================================
# 2. MMR -- the identity control, the diversity behaviour, the tie-break
# ===========================================================================
section("2. MMR selection")

_IDS = ["NCT0009", "NCT0001", "NCT0005", "NCT0003"]
_REL_DESC = M.normalise_relevance([0.050, 0.040, 0.030, 0.020])
_ALL_SIMILAR = np.ones((4, 4))

# THE IDENTITY CONTROL. Maximal redundancy -- every pair identical -- so ONLY
# lambda can save the relevance order. If normalisation or the objective is
# wrong, this is where it shows.
check("2a. lambda = 1 reproduces the pure relevance order under MAXIMAL "
      "redundancy (the identity control the report prints)",
      [_IDS[i] for i in guarded(M.mmr_select, _REL_DESC, _ALL_SIMILAR, 3, 1.0, _IDS)],
      ["NCT0009", "NCT0001", "NCT0005"])
check("2a-i. ...and the module's declared identity lambda IS 1.0, so the "
      "report's control is the arm this check pins",
      M.MMR_IDENTITY_LAMBDA, 1.0)

# DIVERSITY REALLY HAPPENS. Two near-identical strong trials and one weak
# distinct one: at lambda 1 the pair is kept, at 0.3 the distinct one displaces
# the second. A fixture that cannot flip is a vacuous control.
_REL_PAIR = M.normalise_relevance([1.00, 0.99, 0.10])
_SIM_PAIR = np.array([[1.0, 0.99, 0.0],
                      [0.99, 1.0, 0.0],
                      [0.0, 0.0, 1.0]])
_IDS3 = _IDS[:3]
check("2b. lambda = 1 keeps the near-duplicate pair (no diversity pressure)",
      [_IDS3[i] for i in guarded(M.mmr_select, _REL_PAIR, _SIM_PAIR, 2, 1.0, _IDS3)],
      ["NCT0009", "NCT0001"])
check("2c. lambda = 0.3 displaces the duplicate for the distinct trial",
      [_IDS3[i] for i in guarded(M.mmr_select, _REL_PAIR, _SIM_PAIR, 2, 0.3, _IDS3)],
      ["NCT0009", "NCT0005"])

# THE TIE-BREAK. Every relevance equal and every similarity equal, so nothing
# but the tie-break can decide -- and the SAME pool built in a different order
# must give the SAME answer. A selector resolving ties by list order passes the
# run's own determinism probe (it re-runs one list twice) and fails here.
_FLAT = M.normalise_relevance([0.03, 0.03, 0.03])
_FLAT_SIM = np.full((3, 3), 0.5)
np.fill_diagonal(_FLAT_SIM, 1.0)
_forward = [_IDS3[i] for i in guarded(M.mmr_select, _FLAT, _FLAT_SIM, 2, 0.5, _IDS3)]
_rev_ids = list(reversed(_IDS3))
_reversed = [_rev_ids[i] for i in guarded(M.mmr_select, _FLAT, _FLAT_SIM, 2, 0.5, _rev_ids)]
check("2d. an all-tied pool selects by nct_id ascending", _forward,
      ["NCT0001", "NCT0005"])
check("2e. ...and the SAME pool in reverse order gives the SAME answer, so "
      "the result is not an artefact of how the caller built its list",
      _reversed, _forward)

check("2f. k larger than the pool returns the whole pool rather than raising",
      len(guarded(M.mmr_select, _REL_PAIR, _SIM_PAIR, 99, 0.5, _IDS3)), 3)
check("2g. an empty pool selects nothing",
      guarded(M.mmr_select, M.normalise_relevance([]), np.zeros((0, 0)), 5,
              0.5, []), [])
check_true("2h. the selection is deterministic across two runs",
           guarded(M.assert_deterministic, _REL_PAIR, _SIM_PAIR, 2, 0.3, _IDS3))

# RELEVANCE NORMALISATION -- the term lambda mixes against the cosine.
check("2i. min-max maps a pool onto [0, 1]",
      guarded(M.normalise_relevance, [1.0, 3.0, 2.0]).tolist(), [0.0, 1.0, 0.5])
check("2j. a degenerate pool maps to all-ones rather than dividing by zero",
      guarded(M.normalise_relevance, [0.5, 0.5, 0.5]).tolist(), [1.0, 1.0, 1.0])
check("2k. a missing score is read as the pool minimum, not forged to 0.0",
      guarded(M.normalise_relevance, [None, 1.0]).tolist(), [0.0, 1.0])


# ===========================================================================
# 3. THE CAP CONTEXT MANAGER -- and the patch point that would reach nothing
# ===========================================================================
section("3. unlimited_evaluation_cap -- the pre-slice pool depends on it")

_CAP_BEFORE = filtering.MAX_TRIALS_FOR_EVALUATION


def _cap_inside():
    """What the cap reads inside the block, or a marker if entering RAISED.

    THE `with` IS GUARDED AND THAT IS NOT DEFENSIVENESS. The context manager
    ASSERTS that its rebinding took and raises when it did not -- which is
    exactly what a defect patching the wrong namespace produces. A bare `with`
    here lets that RuntimeError escape at module level, so the one revert this
    section exists to catch would abort the file and report NOTHING instead of
    a failure. Measured: it did, on the first run of this file's revert matrix.
    """
    try:
        with M.unlimited_evaluation_cap():
            return filtering.MAX_TRIALS_FOR_EVALUATION
    except Exception as exc:                                    # noqa: BLE001
        return f"<RAISED {type(exc).__name__}>"


_inside = _cap_inside()
check_true("3a. the cap is lifted inside the block",
           isinstance(_inside, int) and _inside > 10 ** 6)
check("3b. ...and restored on the way out",
      filtering.MAX_TRIALS_FOR_EVALUATION, _CAP_BEFORE)


def _cap_after_raise():
    try:
        with M.unlimited_evaluation_cap():
            raise ValueError("planted")
    except ValueError:
        pass
    except Exception as exc:                                    # noqa: BLE001
        return f"<RAISED {type(exc).__name__}>"
    return filtering.MAX_TRIALS_FOR_EVALUATION


check("3c. ...and restored even when the body raises, so a failed patient "
      "cannot leave this process with an uncapped Stage 4",
      _cap_after_raise(), _CAP_BEFORE)

# THE WRONG PATCH POINT, FIRED. `filtering` binds the value with a
# `from oncotriage.config import ...`, so setting it on `config` reaches
# NOTHING. Without this control, a future edit that "simplified" the context
# manager into a config assignment would produce a capped pool reported as
# uncapped -- and every rank below the cut reported as absent.
_CONFIG_BEFORE = config.MAX_TRIALS_FOR_EVALUATION
try:
    config.MAX_TRIALS_FOR_EVALUATION = 10 ** 9
    _via_config = filtering.MAX_TRIALS_FOR_EVALUATION
finally:
    config.MAX_TRIALS_FOR_EVALUATION = _CONFIG_BEFORE
check("3d. CONTROL: setting config.MAX_TRIALS_FOR_EVALUATION reaches "
      "filtering NOT AT ALL -- which is why the context manager patches "
      "filtering's own namespace", _via_config, _CAP_BEFORE)
check("3d-i. ...and the control restored config", config.MAX_TRIALS_FOR_EVALUATION,
      _CONFIG_BEFORE)


# ===========================================================================
# 4. REDUNDANCY WITHIN A POOL
# ===========================================================================
section("4. redundancy_for_pool -- pairs, clusters and the reducible surplus")

_POOL_SIM = np.array([[1.0, 0.90, 0.10],
                      [0.90, 1.0, 0.10],
                      [0.10, 0.10, 1.0]])
_POOL_IVS = [frozenset({"drug a"}), frozenset({"drug a"}), frozenset()]
_red = guarded(M.redundancy_for_pool, _POOL_SIM, 0.70, _POOL_IVS)

check("4a. one duplicate pair is found", _red.get("duplicate_pairs"), 1)
check("4b. of three possible pairs (upper triangle only, no self-pairs)",
      _red.get("total_pairs"), 3)
check("4c. the reducible surplus is one slot", _red.get("redundant_surplus"), 1)
check("4d. the cluster is of size two", _red.get("clusters"), [2])
check("4e. the second signal corroborates the pair",
      _red.get("intervention_and_text_pairs"), 1)
check("4f. a pool of one reports no duplication rather than itself",
      guarded(M.redundancy_for_pool, np.array([[1.0]]), 0.70)
      .get("duplicate_pairs"), 0)
check("4g. raising the threshold above the pair removes it",
      guarded(M.redundancy_for_pool, _POOL_SIM, 0.95).get("duplicate_pairs"), 0)

# TRANSITIVE CLOSURE. Three trials where only two pairs clear the threshold are
# still ONE cluster of three -- which is what a reader means by "three copies"
# and what makes the surplus 2 rather than 1.
_CHAIN = np.array([[1.0, 0.80, 0.10],
                   [0.80, 1.0, 0.80],
                   [0.10, 0.80, 1.0]])
_chain = guarded(M.redundancy_for_pool, _CHAIN, 0.70)
check("4h. a chain of near-duplicates is ONE cluster, transitively",
      _chain.get("clusters"), [3])
check("4h-i. ...and its surplus is two slots, not one",
      _chain.get("redundant_surplus"), 2)

check("4i. an empty intervention set shares nothing, including with another "
      "empty one -- missing data is not a duplicate finding",
      M.shares_intervention(frozenset(), frozenset()), False)
check("4j. ...and a real overlap does share",
      M.shares_intervention(frozenset({"a", "b"}), frozenset({"b"})), True)


# ===========================================================================
# 5. SWAP CLASSIFICATION -- the number the ruling turns on
# ===========================================================================
section("5. classify_swaps -- duplicate removal vs potential false drop")

_S_IDS = ["A", "B", "C", "D"]
_S_IX = {t: i for i, t in enumerate(_S_IDS)}
_S_DUP = np.array([[1.0, 0.95, 0.1, 0.1],
                   [0.95, 1.0, 0.1, 0.1],
                   [0.1, 0.1, 1.0, 0.1],
                   [0.1, 0.1, 0.1, 1.0]])
_S_NONE = np.eye(4)
_NO_IVS = [frozenset()] * 4

# B is dropped and A -- its near-twin -- is RETAINED: a benign removal.
_sw = guarded(M.classify_swaps, ["A", "B", "C"], ["A", "C", "D"], _S_DUP,
              _S_IX, 0.70, _NO_IVS)
check("5a. a removal whose near-twin is RETAINED counts as a duplicate",
      [_sw.get("swapped_out"), _sw.get("swapped_out_duplicate"),
       _sw.get("swapped_out_distinct")], [1, 1, 0])
check("5b. and the promoted trial is counted as swapped in",
      _sw.get("swapped_in_ids"), ["D"])

# The same removal with NO similarity anywhere: a potential false drop.
_sw2 = guarded(M.classify_swaps, ["A", "B", "C"], ["A", "C", "D"], _S_NONE,
               _S_IX, 0.70, _NO_IVS)
check("5c. a removal with no near-twin at all is a DISTINCT loss",
      [_sw2.get("swapped_out_duplicate"), _sw2.get("swapped_out_distinct")],
      [0, 1])
check("5c-i. ...and it is reported by id, so a reader can go and look at it",
      _sw2.get("swapped_out_distinct_ids"), ["B"])

# THE DIRECTION OF THE CHECK. B's near-twin is A; drop BOTH and B is NOT
# represented. A classifier comparing against the DROPPED trials instead of the
# retained ones would call this benign -- inverting the number the rule turns
# on, in the direction that argues for adopting MMR.
_sw3 = guarded(M.classify_swaps, ["A", "B", "C"], ["C", "D"], _S_DUP,
               _S_IX, 0.70, _NO_IVS)
check("5d. CONTROL: when the near-twin is ALSO dropped, the removal is "
      "DISTINCT -- representation is measured against what MMR KEPT",
      [_sw3.get("swapped_out"), _sw3.get("swapped_out_distinct")], [2, 2])

# THE SECOND SIGNAL CAN ONLY EXONERATE. B shares an intervention with the
# retained A, so a text-distinct removal is not counted as a loss.
_IVS_SHARED = [frozenset({"x"}), frozenset({"x"}), frozenset(), frozenset()]
_sw4 = guarded(M.classify_swaps, ["A", "B", "C"], ["A", "C", "D"], _S_NONE,
               _S_IX, 0.70, _IVS_SHARED)
check("5e. a shared intervention with a RETAINED trial exonerates a "
      "text-distinct removal",
      [_sw4.get("swapped_out_duplicate"), _sw4.get("swapped_out_distinct")],
      [1, 0])
_sw5 = guarded(M.classify_swaps, ["A", "B", "C"], ["C", "D"], _S_NONE,
               _S_IX, 0.70, _IVS_SHARED)
check("5f. CONTROL: it does NOT exonerate through a trial that was itself "
      "dropped", _sw5.get("swapped_out_distinct"), 2)

check("5g. an unchanged selection reports no swaps at all",
      [guarded(M.classify_swaps, ["A", "B"], ["A", "B"], _S_DUP, _S_IX, 0.70,
               _NO_IVS).get(k) for k in ("swapped_out", "swapped_in")], [0, 0])


# ===========================================================================
# 6. THE PRE-REGISTERED RULE
# ===========================================================================
section("6. apply_rule -- 'material' and 'almost entirely', as numbers")


def _summary(share, surplus, dup_share, swapped=10):
    """A synthetic corpus summary in the exact shape ``apply_rule`` reads."""
    dup = None if dup_share is None else int(round(dup_share * swapped))
    return {
        "redundancy": {f"{M.NEAR_DUPLICATE_THRESHOLD:.2f}": {
            "share_of_pools_with_a_duplicate_pair": share,
            "redundant_surplus_per_pool_mean": surplus,
        }},
        "mmr": {"0.5": {
            "duplicate_share_of_swaps": dup_share,
            "swapped_out_distinct_total": (0 if dup is None else swapped - dup),
        }},
    }


_EPS = 1e-9
_ADOPT = guarded(M.apply_rule, _summary(M.MATERIAL_POOL_SHARE,
                                        M.MATERIAL_SURPLUS_PER_POOL,
                                        M.ALMOST_ENTIRELY_DUPLICATES), "0.5")
check("6a. exactly at all three thresholds the verdict is ADOPT (the bounds "
      "are inclusive)", _ADOPT.get("verdict"), "ADOPT")

for _label, _s in (
    ("share below the bar", _summary(M.MATERIAL_POOL_SHARE - _EPS,
                                     M.MATERIAL_SURPLUS_PER_POOL,
                                     M.ALMOST_ENTIRELY_DUPLICATES)),
    ("surplus below the bar", _summary(M.MATERIAL_POOL_SHARE,
                                       M.MATERIAL_SURPLUS_PER_POOL - _EPS,
                                       M.ALMOST_ENTIRELY_DUPLICATES)),
    ("duplicate share below the bar", _summary(M.MATERIAL_POOL_SHARE,
                                               M.MATERIAL_SURPLUS_PER_POOL,
                                               M.ALMOST_ENTIRELY_DUPLICATES - _EPS)),
):
    check(f"6b. a hair under the bar flips it to REJECT -- {_label}",
          guarded(M.apply_rule, _s, "0.5").get("verdict"), "REJECT")

# BOTH HALVES OF "MATERIAL" MUST HOLD. Widespread-but-tiny and rare-but-large
# are each consistent with one condition and neither is a case for changing
# every patient's selection.
check("6c. widespread but tiny is NOT material",
      guarded(M.apply_rule, _summary(0.99, 0.10,
                                     M.ALMOST_ENTIRELY_DUPLICATES), "0.5")
      .get("redundancy_material"), False)
check("6d. rare but large is NOT material either",
      guarded(M.apply_rule, _summary(0.02, 5.0,
                                     M.ALMOST_ENTIRELY_DUPLICATES), "0.5")
      .get("redundancy_material"), False)

# A SELECTOR THAT CHANGED NOTHING IS NOT EVIDENCE FOR ADOPTING IT. `None` is
# what `summarise` produces when no trial was swapped at all, and it must not
# be read as a perfect duplicate share.
_NOSWAP = guarded(M.apply_rule, _summary(0.99, 5.0, None), "0.5")
check("6e. a lambda that swapped nothing does not clear the duplicate bar",
      _NOSWAP.get("swaps_almost_entirely_duplicates"), False)
check("6e-i. ...so its verdict is REJECT even with redundancy material",
      _NOSWAP.get("verdict"), "REJECT")

check("6f. the verdict is exactly the conjunction of the two conditions",
      [guarded(M.apply_rule, _summary(0.99, 5.0, 0.99), "0.5").get("verdict"),
       guarded(M.apply_rule, _summary(0.99, 5.0, 0.10), "0.5").get("verdict"),
       guarded(M.apply_rule, _summary(0.01, 0.1, 0.99), "0.5").get("verdict")],
      ["ADOPT", "REJECT", "REJECT"])

# THE THREE CONSTANTS ARE PINNED, so a bar moved after a run fails here rather
# than silently changing a published verdict. Values, not just presence.
check("6g. the pre-registered bars are the ones this file was written against",
      [M.MATERIAL_POOL_SHARE, M.MATERIAL_SURPLUS_PER_POOL,
       M.ALMOST_ENTIRELY_DUPLICATES], [0.25, 1.0, 0.90])


# ===========================================================================
# 7. THE AWS TRIPWIRE
# ===========================================================================
section("7. arm_boto3_guard -- the run must build no AWS client")

_built, _guard = guarded(M.arm_boto3_guard) or (None, None)
check_true("7a. the guard reports a state rather than failing silently",
           _guard in ("armed", ) or str(_guard).startswith("inert:"))

if _guard == "armed":
    import boto3
    _raised = ""
    try:
        boto3.client("bedrock-runtime")
    except RuntimeError as exc:
        _raised = str(exc)
    check_true("7b. a boto3.client call REFUSES rather than building",
               "must build no AWS client" in _raised)
    check("7c. ...and the attempt is recorded, so the report can state zero",
          _built, ["bedrock-runtime"])
else:
    # NOT A SKIP AND NOT A PASS-BY-DEFAULT. boto3 absent means the property
    # holds for a stronger reason, and that reason is RECORDED so a reader does
    # not mistake an inert guard for one that ran.
    check("7b. boto3 is not importable, so no client can be built by any path",
          _built, [])


# ===========================================================================
# 8. WHAT THE MEASUREMENT MUST NOT REACH
# ===========================================================================
section("8. Stage 5 is unreachable, and no model was loaded")

_imports = set()
for _n in ast.walk(_src):
    if isinstance(_n, ast.ImportFrom) and _n.module:
        _imports.add(_n.module)
    elif isinstance(_n, ast.Import):
        for _a in _n.names:
            _imports.add(_a.name)

check("8a. the module imports NOTHING from oncotriage.agent.evaluation -- "
      "Stage 5 is unreachable by construction, not by promise",
      sorted(m for m in _imports if "evaluation" in m and "agent" in m), [])
check("8b. ...and imports no graph builder, so no compiled graph can route on "
      "to Stage 5",
      sorted(m for m in _imports if m.endswith("agent.graph")),
      ["oncotriage.agent.graph"])
# build_initial_state is the ONLY thing taken from agent.graph. Importing
# build_matching_graph would put a Stage 5-routing graph one call away.
_from_graph = {a.name for n in ast.walk(_src) if isinstance(n, ast.ImportFrom)
               and n.module == "oncotriage.agent.graph" for a in n.names}
check("8b-i. ...and takes only build_initial_state from it, never "
      "build_matching_graph or match_patient_to_trials",
      sorted(_from_graph), ["build_initial_state"])

check("8c. the four driven nodes are exactly Stages 1-4",
      sorted(n.split(".")[-1] for n in
             {a.name for n2 in ast.walk(_src) if isinstance(n2, ast.ImportFrom)
              and n2.module in ("oncotriage.agent.retrieval",
                                "oncotriage.agent.filtering")
              for a in n2.names}
             & {"node_query_expansion", "node_hybrid_retrieval",
                "node_cross_encoder_rerank", "node_rule_based_filter"}),
      ["node_cross_encoder_rerank", "node_hybrid_retrieval",
       "node_query_expansion", "node_rule_based_filter"])

check("8d. no model was loaded by importing or driving any of the above",
      sorted(m for m in ("torch", "transformers") if m in sys.modules), [])

# THE COST IS PRICED THROUGH THE PROJECT'S ONE OWNER, so an unpriced model
# raises rather than reporting a confident zero.
_cost = guarded(M.estimate_dense_channel_cost, 300)
check("8e. the run states its cost before spending it, priced by "
      "utils.get_model_cost", _cost.get("calls"), 300)
check_true("8e-i. ...and the figure is a positive upper bound",
           isinstance(_cost.get("usd_upper_bound"), float)
           and _cost["usd_upper_bound"] > 0.0)

check("8f. the module under test was not modified by this run",
      hashlib.sha256(open(_SOURCE_PATH, "rb").read()).hexdigest(),
      _SOURCE_BEFORE)


# ===========================================================================
print("\n" + "=" * 74)
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
Created on Wed Sep  2 2026

@author: ramyalsaffar
"""
