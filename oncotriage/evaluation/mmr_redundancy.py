# MMR Redundancy Measurement (Stages 1-4 only)
#############################################

"""Quantify near-duplicate redundancy inside Stage 4's kept pools, and simulate
Maximal Marginal Relevance offline, so the operator can rule on adopting MMR
FROM EVIDENCE rather than from the plausibility of the idea.

THIS MODULE CHANGES NO PIPELINE BEHAVIOUR AND IS NOT ON ANY PIPELINE PATH. It
reads what Stages 1-4 produce and re-selects a top-k OFFLINE, in its own list.
Nothing it computes is written back into a state dict, a database, a fixture or
a config constant. The ruling is the operator's; this file's whole job is to
put numbers under it.

WHAT IT RUNS AND WHAT IT COSTS
-------------------------------
Stages 1, 2, 3 and 4, stopping dead before Stage 5:

    node_query_expansion        deterministic, no LLM, free
    node_hybrid_retrieval       Qdrant reads + ONE text-embedding-3-small call
    node_cross_encoder_rerank   MedCPT, local, free
    node_rule_based_filter      deterministic, free

STAGE 5 IS NEVER REACHED, and that is structural rather than a promise: this
module imports no evaluation node, calls no graph, and the four functions above
are the only pipeline entry points it names. ``oncotriage/agent/evaluation.py``
appears nowhere in its import list.

IT IS NOT FREE AND SAYING IT WERE WOULD BE THE CLAIM THIS PROJECT DOES NOT
MAKE. ``oncotriage/agent/models.py:get_embedding`` is called exactly once per
patient by Stage 2's dense channel -- measured at
``oncotriage/agent/retrieval.py``'s ``_dense_query``, which is submitted once
per invocation. At ``text-embedding-3-small``'s $0.02/1M tokens an expanded
query of well under a hundred tokens costs on the order of $0.000002, so a
500-patient run is a fraction of a cent. ``estimate_dense_channel_cost()``
states it and ``main()`` prints it before the first call.

    WHY THE DENSE CHANNEL IS PAID FOR RATHER THAN ABLATED. The pipeline fuses
    four channels by weighted RRF and ``config.RRF_WEIGHT_DENSE`` is one of
    them, so a run under the shipped ``retrieval_mode="bm25_only"`` ablation
    produces a DIFFERENT pool -- different members, different order, therefore
    a different kept-k. Measuring redundancy on that pool and ruling on MMR
    from it would be ruling about a pipeline that does not ship. The
    ``--bm25-only`` flag exists so a reader can see that arm and see the
    difference; it is not the default and the report says which arm produced
    its numbers.

HOW THE PRE-SLICE POOL IS OBTAINED, AND WHY IT IS NOT A RE-IMPLEMENTATION
--------------------------------------------------------------------------
``node_rule_based_filter`` returns ``filtered_trials`` ALREADY CUT to
``config.MAX_TRIALS_FOR_EVALUATION``; the pool it cut is a local whose LENGTH
survives as ``candidates_after_quality_filter`` and whose MEMBERS do not. This
measurement needs both sides of that cut -- the kept-k is what Stage 5 judges,
and the ranks below it are what MMR would have to promote from.

So the node is run TWICE per patient, unchanged, and the second run is under
``unlimited_evaluation_cap()``: a context manager that rebinds
``oncotriage.agent.filtering.MAX_TRIALS_FOR_EVALUATION`` and restores it in a
``finally``.

    THE PATCH POINT IS ``filtering``'s OWN NAMESPACE AND NOT ``config``, and
    that is the lesson ``tests/test_agent_rrf_config_ownership.py`` exists to
    teach: ``oncotriage/agent/filtering.py`` does ``from oncotriage.config
    import MAX_TRIALS_FOR_EVALUATION``, which BINDS THE VALUE into that
    module's globals at import. Setting ``config.MAX_TRIALS_FOR_EVALUATION``
    reaches nothing and the run would silently measure a 15-trial pool while
    reporting it had lifted the cap. ``unlimited_evaluation_cap`` asserts that
    the rebinding took, and asserts the restore, so a patch that reached
    nothing is a named failure rather than a quiet one.

    AND THE EQUIVALENCE IS CHECKED PER PATIENT, NEVER ASSUMED. The cut is a
    prefix of an already-sorted list, so the uncapped pool's first k members
    must BE the capped run's output. ``run_stages_1_to_4`` compares the two id
    lists on every patient and records a violation rather than trusting the
    argument; Stage 4 costs about three milliseconds, so the control is free.

THE TWO REDUNDANCY SIGNALS
---------------------------
(a) TF-IDF COSINE over each trial's eligibility criteria text. Implemented here
    over ``numpy`` -- which ``pyproject.toml`` declares -- rather than with
    ``sklearn``, which IS installed on the development machine and is NOT in
    that file. Using it would make this measurement depend on a package the
    project does not declare, which is how an undeclared dependency becomes
    load-bearing.

    THE TOKENIZER IS THE PIPELINE'S OWN, ``agent/text.py:tokenize_for_bm25``,
    and not a new one. That function is what the ``criteria-bm25`` channel
    tokenizes both the index and the query with, so a similarity computed over
    its tokens is computed in the same term space the retrieval channel scores
    in. A second tokenizer here would be a second vocabulary that could
    disagree with the pipeline's about what a term is.

    THE IDF IS FITTED ONCE OVER EVERY DISTINCT TRIAL THE RUN SAW, not per
    pool. A pool holds tens of documents and an IDF estimated over tens of
    documents is noise -- every term in a 15-document pool has a document
    frequency between 1 and 15, so the weighting says more about the pool's
    size than about the corpus. Fitting globally also makes one patient's
    similarity numbers comparable with another's, which per-pool IDF does not.

(b) SHARED INTERVENTION, from ``full_trial_json["interventions"]`` -- the
    deduplicated intervention names ``oncotriage/retrieval/indexer.py`` writes
    at index time. MEASURED PRESENT BEFORE BEING RELIED ON: ``coverage`` in
    every report says on what fraction of pooled trials the field is non-empty,
    so a reader is never shown an intervention statistic computed over a corpus
    that does not carry the field.

    IT IS A SECOND, INDEPENDENT SIGNAL AND NOT A TIE-BREAK. Its value is that
    it is not derived from the text: where the two agree, the text threshold is
    calibrated by something outside itself; where they disagree, the
    disagreement is the finding. Neither is promoted to ground truth.

WHY A THRESHOLD AT ALL, AND WHY IT IS REPORTED AT FIVE VALUES
---------------------------------------------------------------
"Near-duplicate" is not a property of a pair, it is a property of a pair AND a
cut-off, and a single chosen cut-off is a judgement wearing a measurement's
clothes -- which is the defect ``oncotriage/evaluation/medcpt_calibration.py``
was written to remove one stage over. ``NEAR_DUPLICATE_THRESHOLD`` is the
headline because a number has to be, and every table is reported across
``SENSITIVITY_THRESHOLDS`` as well, so a reader who disagrees with the headline
can read their own cut-off off the same run.

WHAT THIS MEASUREMENT CANNOT SEE
---------------------------------
Stated here rather than discovered by a reader:

*   IT MEASURES TEXT, NOT CLINICAL EQUIVALENCE. Two trials of the same drug at
    two doses share almost all of their criteria text and are not
    interchangeable to a patient; two trials of different drugs may share
    boilerplate. Cosine over criteria text is a proxy and the report says so.
*   IT CANNOT SAY WHETHER A SWAPPED-IN TRIAL IS BETTER. It can say a distinct
    trial was dropped, which is the cost; whether the trial promoted in its
    place is a better match is a Stage 5 verdict and Stage 5 is not run.
*   THE POOL IS THIS INDEX'S. Redundancy is a property of the indexed corpus,
    so a re-scrape can move every number here.
"""


# Imports
#--------
import contextlib
import glob
import hashlib
import json
import os
import time

import numpy as np

from oncotriage import paths
from oncotriage.agent import deps
from oncotriage.agent import filtering as _filtering
from oncotriage.agent.filtering import node_rule_based_filter
from oncotriage.agent.graph import build_initial_state
from oncotriage.agent.retrieval import (
    node_cross_encoder_rerank,
    node_hybrid_retrieval,
    node_query_expansion,
)
from oncotriage.agent.text import tokenize_for_bm25
from oncotriage.config import (
    CAMPAIGN_COHORT_SIZE,
    CROSS_ENCODER_MODEL,
    EMBEDDING_MODEL,
    MAX_TRIALS_FOR_EVALUATION,
)
from oncotriage.evaluation import cohort as campaign_cohort
from oncotriage.evaluation import cohort_groups as campaign_cohort_groups
from oncotriage.fhir.parser import parse_fhir_bundle
from oncotriage.observability import console, correlation_scope
from oncotriage.utils import (
    CaffeinateSession,
    get_model_cost,
    resolve_qdrant_collection,
)


#------------------------------------------------------------------------------


# ===========================================================================
# CONFIGURATION -- every number this measurement turns on, with its argument
# ===========================================================================

SCHEMA_VERSION = 1
"""The persisted-pool format. ``load_pools`` refuses a mismatch.

``oncotriage/fixtures/capture.py``'s convention: a reader that silently accepts
an older shape compares fields that are not there and reports them as absent.
"""

SIMILARITY_METHOD = (
    "TF-IDF cosine over eligibility criteria text; sublinear TF (1+ln tf), "
    "smoothed IDF ln((1+N)/(1+df))+1, L2-normalised rows; tokens from "
    "oncotriage.agent.text.tokenize_for_bm25; IDF fitted once over every "
    "distinct trial the run pooled"
)
"""The one-line statement of ``tfidf_matrix``, recorded in every artefact.

``cohort.DRAW_ALGORITHM``'s argument: a value the artefact carries cannot go
stale silently, where a sentence in a docstring can. A reader with this string
and the trial texts can recompute any similarity in the report.
"""

NEAR_DUPLICATE_THRESHOLD = 0.70
"""Headline cosine at or above which a pair is called a near-duplicate.

NOT DERIVED FROM THIS CORPUS, and that is deliberate rather than lazy: a
threshold fitted to the data it then measures agrees with itself by
construction. 0.70 is the conventional near-duplicate cut for L2-normalised
TF-IDF cosine -- two documents sharing roughly 70% of their weighted term mass.

IT IS A HEADLINE AND NOT A RULING. Every table is reported at
``SENSITIVITY_THRESHOLDS`` as well, and the intervention signal is reported
beside it precisely so this number can be judged from outside itself.
"""

SENSITIVITY_THRESHOLDS = (0.50, 0.60, 0.70, 0.80, 0.90)
"""Every cut-off the tables are reported at. Includes the headline."""

MMR_LAMBDAS = (0.3, 0.5, 0.7)
"""The lambdas simulated. Lower = more diversity pressure.

The operator's brief names these three. ``lambda = 1.0`` is the identity --
pure relevance, which reproduces the shipped selection exactly -- and it is
asserted as a control rather than reported as a finding.
"""

MMR_IDENTITY_LAMBDA = 1.0
"""The control lambda. At 1.0 MMR must reproduce the shipped kept-k exactly.

WITHOUT IT THE SIMULATION HAS NO ZERO. A swap count is only meaningful if the
selector is known to agree with the pipeline when the diversity term is switched
off; a bug in the relevance normalisation would otherwise show up as "MMR
changes things", which is what the run is trying to measure.
"""

RELEVANCE_NORMALISATION = "min-max within the post-filter pool"
"""How ``rerank_score`` is mapped into [0, 1] before MMR mixes it with cosine.

MIN-MAX AND NOT RAW. The two terms of the MMR objective must live on one scale
or lambda means nothing: fused RRF scores run about 0.01..0.06 and cosine runs
0..1, so an unnormalised mix at lambda 0.5 is a diversity-only selector wearing
a balanced label. Min-max is used rather than z-scoring because the objective
needs a bounded relevance on the same [0, 1] interval the cosine occupies.

    A DEGENERATE POOL -- every score equal -- maps to all-ones rather than to a
    division by zero, so relevance stops discriminating and MMR falls through
    to the diversity term and then to the id tie-break. That is the honest
    answer for a pool with no score spread, and it is deterministic.

THE SCORE IS ``rerank_score``, THE BOOSTED ONE, because that is the field
Stage 4 sorts on and therefore the one that decides the shipped kept-k. Gating
uses ``rerank_score_raw``; ranking uses this. Using the raw score here would
simulate MMR over an order the pipeline does not use.
"""

TIMING_PROBE_PATIENTS = 10
"""How many patients the in-code timing gate measures before projecting."""

TIMING_BUDGET_SECONDS = 60 * 60
"""The projected wall-clock budget above which the gate falls back.

THE GATE IS THE SCRIPT'S DECISION AND NOT THE OPERATOR'S. It measures
``TIMING_PROBE_PATIENTS`` real patients, projects the full cohort, and if the
projection exceeds this it drops to the project's own seeded 50-patient
stability draw -- ``cohort.CohortSelection.stability_stems`` -- and says so in
every artefact it writes. A budget that a human is invited to override after
seeing the number is a budget that is always overridden.
"""

MIN_POOL_FOR_MMR = 2
"""Below this a pool cannot be reordered and contributes nothing to MMR.

A pool at or under the cap is returned whole by any selector, so its swap count
is zero BY CONSTRUCTION rather than by measurement. Counting those patients in
a "patients with zero swaps" denominator would report the cap not binding as
evidence that MMR changes nothing, which is the degenerate reading this
project's non-degeneracy probes exist to prevent. They are counted and reported
SEPARATELY.
"""


#------------------------------------------------------------------------------


# ===========================================================================
# WHAT A RUN COSTS, STATED BEFORE IT IS SPENT
# ===========================================================================

def estimate_dense_channel_cost(patients: int,
                                tokens_per_query: int = 100) -> dict:
    """The Stage 2 dense-channel spend for ``patients`` patients.

    ONE CALL PER PATIENT. ``node_hybrid_retrieval`` submits exactly one
    ``_dense_query`` per invocation and that closure makes exactly one
    ``get_embedding`` call; Stages 1, 3 and 4 call no priced endpoint at all.

    PRICED THROUGH ``oncotriage/utils.py:get_model_cost``, WHICH IS THIS
    PROJECT'S ONE OWNER OF PRICING ARITHMETIC. An earlier draft of this
    function reached into ``config.PRICING_CONFIG`` and did the division
    itself, which is a second implementation of a priced calculation -- the
    shape item 38 had to remove when the dashboard and the query layer had
    drifted into two answers for one cost. It also gets the loud failure for
    free: an unpriced model raises ``UnknownModelPricingError`` rather than
    returning a confident zero, which for a cost figure printed before a run
    is the difference between a refusal and a lie.

    NAMED ``estimate_dense_channel_cost`` AND NOT ``estimate_embedding_cost``
    because ``oncotriage/retrieval/indexer.py`` already has the latter and it
    answers a different question -- the INDEX build's exact token count for a
    list of trials, with tiktoken. Two functions of one name in one project
    that take different arguments and mean different things is a reader's trap.

    ``tokens_per_query`` is a deliberate OVER-estimate of an expanded query --
    they measure in the tens -- so the figure printed before a run is an upper
    bound rather than a guess that could be low. This function makes no API
    call and needs no tokeniser to be honest, because it is labelled a bound.
    """
    tokens = patients * tokens_per_query
    return {
        "model": EMBEDDING_MODEL,
        "calls": patients,
        "tokens_upper_bound": tokens,
        "usd_upper_bound": get_model_cost(EMBEDDING_MODEL, tokens, 0),
        "tokens_per_query_assumed": tokens_per_query,
    }


#------------------------------------------------------------------------------


# ===========================================================================
# THE TEXT SIGNAL
# ===========================================================================

def criteria_text_of(trial: dict) -> str:
    """The eligibility text a similarity is computed over.

    ``criteria_text`` is the whole block as ClinicalTrials.gov registered it.
    The inclusion/exclusion halves are used ONLY as a fallback, joined, when
    the whole block is absent -- ``oncotriage/retrieval/indexer.py`` writes all
    three and a trial carrying the split but not the source is a shape the
    splitter can produce.

    RETURNS "" RATHER THAN RAISING on a trial with no eligibility text at all.
    Such a trial is real (the indexer admits it) and it must appear in the
    coverage figure rather than take the run down; ``tfidf_matrix`` gives an
    empty document a zero row, whose cosine with everything is 0, so an
    unmeasurable trial is never reported as a duplicate of anything.
    """
    elig = trial.get("eligibility") or {}
    whole = (elig.get("criteria_text") or "").strip()
    if whole:
        return whole
    halves = [(elig.get("inclusion_criteria") or "").strip(),
              (elig.get("exclusion_criteria") or "").strip()]
    return "\n".join(h for h in halves if h)


def tfidf_matrix(docs) -> np.ndarray:
    """L2-normalised sublinear TF-IDF rows for ``docs``. See SIMILARITY_METHOD.

    Returns an ``(n_docs, n_terms)`` float64 array. An empty document, or one
    whose every token is out of vocabulary, yields an all-zero row -- whose
    cosine with everything is exactly 0, which is the honest answer for a
    document that carries no measurable content.

    THE VOCABULARY IS BUILT FROM ``docs`` AND THE CALLER DECIDES WHAT THAT IS.
    ``measure`` passes every DISTINCT trial the run pooled, which is what makes
    one patient's numbers comparable with another's; a caller passing a single
    pool would get a per-pool IDF and the module header says why that is worse.
    """
    tokenised = [tokenize_for_bm25(d or "") for d in docs]
    vocab = {}
    for toks in tokenised:
        for t in toks:
            if t not in vocab:
                vocab[t] = len(vocab)

    n_docs = len(tokenised)
    if not vocab or not n_docs:
        return np.zeros((n_docs, 0), dtype=np.float64)

    tf = np.zeros((n_docs, len(vocab)), dtype=np.float64)
    for i, toks in enumerate(tokenised):
        for t in toks:
            tf[i, vocab[t]] += 1.0

    # Sublinear TF. A criteria block that repeats a term forty times is not
    # forty times as much about that term, and raw counts let one long
    # boilerplate section dominate a whole row.
    with np.errstate(divide="ignore"):
        sub = np.where(tf > 0, 1.0 + np.log(np.where(tf > 0, tf, 1.0)), 0.0)

    df = (tf > 0).sum(axis=0).astype(np.float64)
    # Smoothed IDF, sklearn's `smooth_idf=True` convention reproduced rather
    # than invented: adding one document to the count keeps a term appearing in
    # every document at a non-zero weight instead of annihilating the row.
    idf = np.log((1.0 + n_docs) / (1.0 + df)) + 1.0

    mat = sub * idf
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    # A zero row stays a zero row; dividing it by 1.0 is what keeps it one
    # rather than producing nan and poisoning every cosine it appears in.
    return mat / np.where(norms > 0.0, norms, 1.0)


def cosine_matrix(rows: np.ndarray) -> np.ndarray:
    """Pairwise cosine for L2-normalised ``rows`` -- a plain Gram matrix.

    Clipped into [0, 1] and its diagonal forced to 1.0. TF-IDF weights are
    non-negative so a true cosine cannot leave that interval; what CAN leave it
    is floating-point error at the ends, and a 1.0000000002 read against a
    threshold is a comparison decided by rounding.
    """
    if rows.size == 0:
        n = rows.shape[0]
        out = np.zeros((n, n), dtype=np.float64)
        np.fill_diagonal(out, 1.0)
        return out
    sim = np.clip(rows @ rows.T, 0.0, 1.0)
    np.fill_diagonal(sim, 1.0)
    return sim


#------------------------------------------------------------------------------


# ===========================================================================
# THE INTERVENTION SIGNAL
# ===========================================================================

def interventions_of(trial: dict) -> frozenset:
    """The trial's intervention names, case-folded and stripped.

    Case-folding only. No stemming, no synonym table and no substring matching:
    "Pembrolizumab" and "Pembrolizumab 200mg" are different registered names
    and deciding they are one intervention is a clinical judgement this
    measurement is not entitled to make. The consequence -- that this signal
    UNDER-counts shared interventions -- is stated in the report rather than
    engineered around, and it is the safe direction for a signal whose job is
    to corroborate the text one.
    """
    return frozenset(
        str(name).strip().lower()
        for name in (trial.get("interventions") or [])
        if str(name).strip()
    )


def shares_intervention(a: frozenset, b: frozenset) -> bool:
    """True when two trials name at least one intervention in common.

    An EMPTY set shares nothing, including with another empty set. Two trials
    that both failed to register an intervention are not thereby the same
    intervention, and reporting them as a match would turn missing data into a
    duplicate finding -- which is the direction that inflates the case for MMR.
    """
    return bool(a and b and (a & b))


#------------------------------------------------------------------------------


# ===========================================================================
# DRIVING STAGES 1-4
# ===========================================================================

@contextlib.contextmanager
def unlimited_evaluation_cap():
    """Run Stage 4 with ``MAX_TRIALS_FOR_EVALUATION`` lifted, then restore it.

    See the module header for why the patch point is ``filtering``'s own
    namespace. Both the rebinding and the restore are ASSERTED: a patch that
    reached nothing would otherwise produce a capped pool reported as uncapped,
    which is a silently wrong measurement rather than a failure.
    """
    saved = _filtering.MAX_TRIALS_FOR_EVALUATION
    _filtering.MAX_TRIALS_FOR_EVALUATION = 10 ** 9
    if _filtering.MAX_TRIALS_FOR_EVALUATION != 10 ** 9:      # pragma: no cover
        _filtering.MAX_TRIALS_FOR_EVALUATION = saved
        raise RuntimeError(
            "the cap rebinding did not take: oncotriage.agent.filtering "
            "still reports MAX_TRIALS_FOR_EVALUATION = "
            f"{_filtering.MAX_TRIALS_FOR_EVALUATION!r}. The uncapped pool "
            "would be the capped one and every rank below the cut would be "
            "reported as absent.")
    try:
        yield
    finally:
        _filtering.MAX_TRIALS_FOR_EVALUATION = saved
        if _filtering.MAX_TRIALS_FOR_EVALUATION != saved:    # pragma: no cover
            raise RuntimeError(
                "the cap could not be restored; this process would keep an "
                "uncapped Stage 4 for every later caller.")


def _trial_record(entry: dict) -> dict:
    """The persisted shape of one pooled trial. Scores plus what a signal needs.

    THE WHOLE ``full_trial_json`` IS NOT PERSISTED -- it is ~10 kB per trial and
    a 500-patient run pools tens of thousands. What is kept is every score the
    ranking uses, the criteria text the text signal reads and the intervention
    names the second signal reads, so the analysis is fully re-runnable from the
    file without another Qdrant read or another embedding call.
    """
    trial = entry["trial"]
    return {
        "nct_id":           trial.get("nct_id", ""),
        "title":            trial.get("title", ""),
        "phase":            trial.get("phase", ""),
        "rerank_score":     entry.get("rerank_score"),
        "rerank_score_raw": entry.get("rerank_score_raw"),
        "medcpt_score_max": entry.get("medcpt_score_max"),
        "mesh_boost_tier":  entry.get("mesh_boost_tier"),
        "criteria_text":    criteria_text_of(trial),
        "interventions":    sorted(interventions_of(trial)),
    }


def run_stages_1_to_4(patient_data: dict, ablation_flags: dict = None) -> dict:
    """Stages 1-4 for one patient. Returns the pre-slice pool and the kept-k.

    The four nodes are called in order, directly, rather than through the
    compiled graph -- ``medcpt_calibration.rerank_one``'s pattern and its
    reason: the graph's conditional edges route on to Stage 5, which is a
    billed call. Calling four nodes in order is the same code the graph would
    run for those four.

    Returns a dict carrying ``pool`` (the FULL post-quality-gate pool, sorted,
    uncapped), ``kept_ids`` (what the shipped cap actually returns) and
    ``prefix_ok`` -- the per-patient control that the uncapped pool's first k
    ARE the capped run's output.
    """
    state = build_initial_state(patient_data, ablation_flags)
    state.update(node_query_expansion(state))
    state.update(node_hybrid_retrieval(state))
    state.update(node_cross_encoder_rerank(state))

    # THE SHIPPED RUN FIRST, so what is compared against is what production
    # would have produced rather than a reconstruction of it. dict(state) so
    # neither call can see the other's writes.
    capped = node_rule_based_filter(dict(state))
    with unlimited_evaluation_cap():
        uncapped = node_rule_based_filter(dict(state))

    kept_ids = [t["trial"]["nct_id"] for t in capped["filtered_trials"]]
    pool = [_trial_record(t) for t in uncapped["filtered_trials"]]
    pool_ids = [r["nct_id"] for r in pool]

    return {
        "pool": pool,
        "kept_ids": kept_ids,
        # The control. Compared, never assumed; `measure` counts violations and
        # the report prints the count even when it is zero.
        "prefix_ok": pool_ids[:len(kept_ids)] == kept_ids,
        "cap": MAX_TRIALS_FOR_EVALUATION,
        "cap_binds": len(pool) > MAX_TRIALS_FOR_EVALUATION,
        "reranked": len(state["reranked_trials"]),
        "candidates_after_rule_filter": capped["candidates_after_rule_filter"],
        "candidates_after_quality_filter": capped["candidates_after_quality_filter"],
        "retrieval_degraded": bool(state.get("retrieval_degraded")),
    }


#------------------------------------------------------------------------------


# ===========================================================================
# REDUNDANCY WITHIN ONE POOL
# ===========================================================================

def pool_digest(nct_ids) -> str:
    """A stable identity for a pool: the sha256 of its SORTED trial ids.

    ``oncotriage/evaluation/medcpt_calibration.py:pool_digest``'s function and
    its reason, which applies here with more force. Synthea patients within one
    cancer group carry near-identical condition lists, so Stage 1 builds the
    same expanded query, Stage 2 retrieves the same trials and Stage 3 hands
    back the same pool -- and the redundancy tables are then weighted by how
    often a pool RECURS rather than by how many distinct pools were seen.

    WITHOUT THIS THE HEADLINE IS A COHORT ARTEFACT WEARING A SAMPLE SIZE. "50
    patients" is the number of times the gate ran, which is the right weight
    for a per-patient rate -- production judges per patient, so a pool that
    recurs ten times really is judged ten times -- and it is the WRONG number
    to read as independent evidence about trial redundancy. Both are reported.

    SORTED, NOT IN RANK ORDER, which is the mistake that file records making:
    ``json.dumps(..., sort_keys=True)`` sorts DICT KEYS, not list elements, so
    two pools holding the same trials in a different order hashed differently
    and were counted as two. For an identity, order carries no information, and
    an order-sensitive digest OVERSTATES distinctness -- the direction that
    flatters the sample.
    """
    joined = "\n".join(sorted(nct_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _clusters(n: int, pairs) -> list:
    """Connected components over ``pairs`` -- union-find, no dependency.

    A "duplicate cluster" is the transitive closure of the near-duplicate
    relation, which is what a reader means by "three copies of the same trial"
    even when one of the three pairs sits below the threshold.

    Returns component member-index lists of size >= 2, sorted, largest first
    then by first member -- a total order, so two runs over one input report
    the same clusters in the same order.
    """
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in pairs:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = [sorted(v) for v in groups.values() if len(v) > 1]
    out.sort(key=lambda g: (-len(g), g[0]))
    return out


def redundancy_for_pool(sim: np.ndarray, threshold: float,
                        interventions=None) -> dict:
    """Duplicate pairs, clusters and the intervention corroboration for a pool.

    ``sim`` is the pool's own cosine submatrix. ``interventions`` is the
    per-trial frozenset list in the same order, or None to skip that signal.

    The upper triangle only -- a pair is counted once -- and the diagonal is
    excluded, so a pool of one reports no duplication rather than reporting
    itself as a duplicate of itself.
    """
    n = int(sim.shape[0])
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            if sim[i, j] >= threshold:
                pairs.append((i, j))

    total_pairs = n * (n - 1) // 2
    clusters = _clusters(n, pairs)
    members = sorted({i for pair in pairs for i in pair})

    out = {
        "pool_size": n,
        "total_pairs": total_pairs,
        "duplicate_pairs": len(pairs),
        "duplicate_pair_rate": (len(pairs) / total_pairs) if total_pairs else 0.0,
        "trials_in_a_duplicate_pair": len(members),
        "clusters": [len(c) for c in clusters],
        "largest_cluster": max((len(c) for c in clusters), default=0),
        "has_duplicate": bool(pairs),
        # The reducible surplus: how many trials could be removed if every
        # cluster were collapsed to one representative. Reported because it is
        # the quantity an MMR advocate is really claiming exists.
        "redundant_surplus": sum(len(c) - 1 for c in clusters),
    }

    if interventions is not None:
        shared = 0
        agree = 0
        for i in range(n):
            for j in range(i + 1, n):
                same = shares_intervention(interventions[i], interventions[j])
                shared += int(same)
                if same and sim[i, j] >= threshold:
                    agree += 1
        out["intervention_pairs"] = shared
        out["intervention_and_text_pairs"] = agree
    return out


#------------------------------------------------------------------------------


# ===========================================================================
# MMR
# ===========================================================================

def normalise_relevance(scores) -> np.ndarray:
    """Min-max ``scores`` into [0, 1]. See RELEVANCE_NORMALISATION.

    A missing score is read as the pool minimum rather than as zero: zero is a
    real fused-RRF value and forging one would rank an unscored trial against
    trials that were scored. A degenerate pool maps to all-ones.
    """
    vals = np.array([0.0 if s is None else float(s) for s in scores],
                    dtype=np.float64)
    if vals.size == 0:
        return vals
    lo, hi = float(vals.min()), float(vals.max())
    if hi <= lo:
        return np.ones_like(vals)
    return (vals - lo) / (hi - lo)


def mmr_select(relevance: np.ndarray, sim: np.ndarray, k: int, lam: float,
               ids) -> list:
    """Standard MMR. Returns the selected indices, in selection order.

    The objective, exactly:

        MMR(d) = lambda * rel(d) - (1 - lambda) * max_{s in S} sim(d, s)

    with ``S`` the already-selected set and ``max`` over an empty ``S`` taken
    as 0.0 -- so the first pick is ``argmax rel``, which is what makes
    ``lambda = 1`` reproduce a pure relevance ranking.

    DETERMINISM IS BY CONSTRUCTION, NOT BY SORT STABILITY. Each step picks
    ``min`` over the key ``(-score, nct_id)``, so a tie is broken on the trial
    id ascending and never on the order the caller happened to build its pool
    in -- the lesson ``oncotriage/extraction/stage.py``'s observation sort had
    to learn. Two runs over one pool therefore return one answer, which
    ``assert_deterministic`` checks by running it twice.
    """
    n = int(relevance.shape[0])
    k = min(k, n)
    selected = []
    remaining = list(range(n))
    # The running max similarity to the selected set, updated incrementally --
    # O(n*k) rather than O(n*k^2), and identical arithmetic either way.
    max_sim = np.zeros(n, dtype=np.float64)

    while remaining and len(selected) < k:
        best = min(
            remaining,
            key=lambda i: (-(lam * float(relevance[i])
                             - (1.0 - lam) * float(max_sim[i])), ids[i]),
        )
        selected.append(best)
        remaining.remove(best)
        if remaining:
            max_sim = np.maximum(max_sim, sim[best])
    return selected


def classify_swaps(baseline_ids, mmr_ids, sim, index_of, threshold: float,
                   interventions=None) -> dict:
    """What MMR changed, and whether each removal was a duplicate or a loss.

    A trial in ``baseline_ids`` and not in ``mmr_ids`` is a SWAP-OUT. It is
    "duplicate" when it is at or above ``threshold`` of SOME TRIAL MMR
    RETAINED -- the claim MMR makes for itself, that what it removed is
    represented by what it kept -- and "distinct" otherwise.

    A DISTINCT SWAP-OUT IS A POTENTIAL FALSE DROP and is the number the ruling
    turns on: it is a trial the shipped pipeline would have had judged and MMR
    would not, with no near-duplicate standing in for it. It is returned by id
    and not only counted, so a reader can go and look at one.
    """
    kept = set(mmr_ids)
    out_ids = [t for t in baseline_ids if t not in kept]
    in_ids = [t for t in mmr_ids if t not in set(baseline_ids)]

    duplicate_out, distinct_out = [], []
    for tid in out_ids:
        i = index_of[tid]
        represented = any(sim[i, index_of[o]] >= threshold for o in mmr_ids)
        if not represented and interventions is not None:
            # THE SECOND SIGNAL CAN ONLY EXONERATE, NEVER CONDEMN. A removal
            # the text calls distinct but which shares an intervention with a
            # retained trial is not clearly a loss, and counting it as one
            # would overstate the cost of MMR. The reverse -- letting text
            # similarity alone condemn -- is not done anywhere.
            represented = any(
                shares_intervention(interventions[i], interventions[index_of[o]])
                for o in mmr_ids)
        (duplicate_out if represented else distinct_out).append(tid)

    return {
        "swapped_out": len(out_ids),
        "swapped_in": len(in_ids),
        "swapped_out_duplicate": len(duplicate_out),
        "swapped_out_distinct": len(distinct_out),
        "swapped_out_distinct_ids": distinct_out,
        "swapped_in_ids": in_ids,
    }


def assert_deterministic(relevance, sim, k, lam, ids) -> bool:
    """Run ``mmr_select`` twice on one input and require an identical answer.

    The brief's determinism requirement, made a MEASUREMENT rather than a
    claim. It is cheap -- the selection is microseconds -- so it runs on every
    patient rather than on a sampled one.
    """
    return (mmr_select(relevance, sim, k, lam, ids)
            == mmr_select(relevance, sim, k, lam, ids))


#------------------------------------------------------------------------------


# ===========================================================================
# THE COHORT
# ===========================================================================

def select_cohort(size: int, seed=None, out=None):
    """The measurement cohort, drawn with the campaign's own machinery.

    ``oncotriage/evaluation/cohort.py`` and ``cohort_groups.py``, exactly as
    ``oncotriage/batch/runner.py`` calls them -- the stratified draw, the same
    grouper, the same digest. Nothing about the draw is re-implemented here, so
    a cohort this measurement reports is a cohort the campaign could run.

    ``size`` is passed EXPLICITLY rather than left to
    ``config.CAMPAIGN_COHORT_SIZE``. The configured campaign is 500 and this
    measurement's brief is 300, and a measurement that silently drew a
    different number from the one it reported would be the defect this project
    calls a false record. The report states both.

    Returns ``(CohortSelection, fhir_files)``.
    """
    emit = console.out if out is None else out
    fhir_files = sorted(glob.glob(os.path.join(paths.data_fhir_path, "*.json")))
    emit(f"[Cohort] corpus: {len(fhir_files)} bundles at {paths.data_fhir_path}")
    emit("[Cohort] grouping the corpus for the stratified draw "
         "(cached after the first run; ~3 min cold)...")
    group_map = campaign_cohort_groups.group_map(fhir_files)
    selection = campaign_cohort.select(
        fhir_files, size=size, seed=seed,
        group_of=campaign_cohort_groups.grouper(group_map))
    for line in selection.describe():
        emit(line)
    return selection, fhir_files


def files_for_stems(fhir_files, stems) -> list:
    """The bundle paths whose stems are in ``stems``, sorted by path.

    ``cohort.select`` already does this for the cohort; the FALLBACK draw is a
    list of stems (``stability_stems``) with no paths attached, so the timing
    gate needs the mapping back. Sorted by path for ``draw``'s reason: the
    processing order must not be an artefact of the seed.
    """
    wanted = set(stems)
    by_stem = {}
    for path in fhir_files:
        by_stem.setdefault(campaign_cohort.stem_of(path), []).append(path)
    return sorted(by_stem[s][0] for s in sorted(wanted) if s in by_stem)


#------------------------------------------------------------------------------


# ===========================================================================
# THE RUN -- STAGES 1-4 OVER THE COHORT, WITH THE TIMING GATE
# ===========================================================================

def collect_pools(files, fallback_files=None, budget_seconds=TIMING_BUDGET_SECONDS,
                  probe_patients=TIMING_PROBE_PATIENTS, ablation_flags=None,
                  out=None) -> dict:
    """Drive Stages 1-4 over ``files``, with the in-code timing gate.

    THE GATE IS THIS FUNCTION'S DECISION. It times the first
    ``probe_patients`` real patients, projects the full list, prints the
    projection, and if it exceeds ``budget_seconds`` it TRUNCATES the run to
    ``fallback_files`` -- the project's seeded 50-patient stability draw --
    keeping the patients it has already measured that are in that draw and
    continuing with the rest. Nothing about it asks a human.

    Every artefact records ``gate``, so a reader always knows whether the
    numbers are over the requested cohort or over the fallback, and never has
    to infer it from a count.
    """
    emit = console.out if out is None else out
    results = {}
    prefix_violations = []
    parse_failures = []

    planned = list(files)
    gate = {
        "probe_patients": probe_patients,
        "budget_seconds": budget_seconds,
        "requested_patients": len(planned),
        "fired": False,
        "projected_seconds": None,
        "seconds_per_patient": None,
        "final_patients": len(planned),
        "fallback_available": fallback_files is not None,
    }

    started = time.perf_counter()
    idx = 0
    # AN UPPER BOUND ON THE EMBEDDING CALLS ACTUALLY MADE, and the reason it is
    # counted here rather than derived from the cohort size: the timing gate can
    # TRUNCATE the run, so the requested cohort is not what was attempted, and
    # a cost figure priced against the request would name a spend that never
    # happened. It counts drives ENTERED rather than patients kept -- a patient
    # the gate later discards was still paid for, and a bundle that failed to
    # PARSE was not, because the parse precedes Stage 2.
    attempted = 0
    while idx < len(planned):
        path = planned[idx]
        stem = campaign_cohort.stem_of(path)
        t0 = time.perf_counter()
        try:
            patient_data = parse_fhir_bundle(path)
            attempted += 1
            with correlation_scope():
                record = run_stages_1_to_4(patient_data, ablation_flags)
        except Exception as exc:                                # noqa: BLE001
            # COUNTED, NEVER SWALLOWED. A patient this measurement cannot run is
            # a patient the cohort claims and the tables do not cover, and the
            # difference between the two counts is the only thing that says so.
            parse_failures.append({"stem": stem, "error": repr(exc)})
            emit(f"[{idx + 1:>4}/{len(planned)}] {stem[:44]:<44} FAILED "
                 f"{type(exc).__name__}")
            idx += 1
            continue
        elapsed = time.perf_counter() - t0

        record["stem"] = stem
        record["seconds"] = elapsed
        results[stem] = record
        if not record["prefix_ok"]:
            prefix_violations.append(stem)

        emit(f"[{idx + 1:>4}/{len(planned)}] {stem[:44]:<44} "
             f"pool={len(record['pool']):>3} kept={len(record['kept_ids']):>2} "
             f"{'CAP' if record['cap_binds'] else '   '} {elapsed:6.1f}s")

        idx += 1

        # --- THE GATE, once, after exactly `probe_patients` MEASURED patients
        if not gate["fired"] and gate["projected_seconds"] is None \
                and len(results) == probe_patients:
            # THE PROBE WINDOW INCLUDES THE ONE-OFF MODEL LOADS -- MedCPT, the
            # FastEmbed BM25 encoder and the MeSH filter all resolve inside the
            # first patient -- so the per-patient figure is an OVER-estimate by
            # roughly (load time / probe_patients). That is deliberate and it is
            # the safe direction: it makes the gate slightly more likely to fire
            # and fall back, never less. Subtracting the load would mean timing
            # the loads separately and trusting that subtraction, which is more
            # machinery for a number whose only job is to compare against a
            # budget.
            per = (time.perf_counter() - started) / max(len(results), 1)
            projected = per * len(planned)
            gate["seconds_per_patient"] = per
            gate["projected_seconds"] = projected
            emit("")
            emit(f"[Gate] {probe_patients} patients measured at {per:.1f}s each; "
                 f"projection for {len(planned)} is {projected / 60.0:.1f} min "
                 f"against a {budget_seconds / 60.0:.0f} min budget.")
            if projected > budget_seconds and fallback_files is not None:
                gate["fired"] = True
                keep = set(fallback_files)
                # Everything already measured stays measured -- it is paid for
                # -- and the remaining plan becomes the fallback draw. `results`
                # is filtered at the end so the tables cover the fallback and
                # nothing else.
                planned = planned[:idx] + [p for p in fallback_files
                                           if p not in set(planned[:idx])]
                gate["final_patients"] = len(keep)
                emit(f"[Gate] FIRED. Falling back to the project's seeded "
                     f"{len(fallback_files)}-patient stability draw. Every "
                     f"number in this run is over that draw, and every "
                     f"artefact says so.")
            elif projected > budget_seconds:
                emit("[Gate] over budget and NO fallback draw was supplied; "
                     "continuing with the requested cohort.")
            else:
                emit("[Gate] within budget; running the requested cohort.")
            emit("")

    if gate["fired"]:
        keep = {campaign_cohort.stem_of(p) for p in fallback_files}
        results = {k: v for k, v in results.items() if k in keep}
        gate["final_patients"] = len(results)

    return {
        "results": results,
        "patients_attempted": attempted,
        "gate": gate,
        "prefix_violations": prefix_violations,
        "parse_failures": parse_failures,
        "wall_seconds": time.perf_counter() - started,
    }


#------------------------------------------------------------------------------


# ===========================================================================
# PERSISTENCE -- so the analysis is re-runnable for free
# ===========================================================================

def save_pools(run: dict, path: str, provenance: dict) -> str:
    """Write the pools and their provenance. The analysis reads this, not Qdrant.

    WHY THE POOLS ARE PERSISTED AT ALL. Retrieval is the whole cost of this
    measurement -- Stage 3 is tens of seconds per patient and Stage 2 is the
    only priced call -- while the redundancy tables and the lambda sweep are
    milliseconds. Separating them means a reader can re-run the analysis at a
    different threshold, a different lambda or a corrected formula WITHOUT
    re-paying for retrieval and without another network call, and it means the
    numbers in the report are reproducible from a file rather than only from a
    live index that moves under a re-scrape.
    """
    payload = {
        "schema_version": SCHEMA_VERSION,
        "provenance": provenance,
        "gate": run["gate"],
        "prefix_violations": run["prefix_violations"],
        "parse_failures": run["parse_failures"],
        "patients_attempted": run.get("patients_attempted"),
        "wall_seconds": run["wall_seconds"],
        "patients": run["results"],
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True)
    os.replace(tmp, path)
    return path


def load_pools(path: str) -> dict:
    """Read a file ``save_pools`` wrote. Refuses a schema mismatch by version."""
    with open(path) as handle:
        payload = json.load(handle)
    got = payload.get("schema_version")
    if got != SCHEMA_VERSION:
        raise RuntimeError(
            f"{path} is schema_version {got!r} and this build reads "
            f"{SCHEMA_VERSION!r}. Re-run the collection rather than analysing "
            f"a shape whose fields may mean something else.")
    return payload


#------------------------------------------------------------------------------


# ===========================================================================
# THE ANALYSIS
# ===========================================================================

def _percentiles(values, points=(5, 25, 50, 75, 95)) -> dict:
    if not values:
        return {str(p): None for p in points}
    arr = np.asarray(values, dtype=np.float64)
    return {str(p): float(np.percentile(arr, p)) for p in points}


def analyse(payload: dict, thresholds=SENSITIVITY_THRESHOLDS,
            headline=NEAR_DUPLICATE_THRESHOLD, lambdas=MMR_LAMBDAS,
            cap=None) -> dict:
    """Every table the report prints, from persisted pools. No network, no cost.

    ONE GLOBAL TF-IDF SPACE. Every DISTINCT trial across every pool is
    vectorised once and each pool's cosine submatrix is sliced out of that one
    space, so a similarity means the same thing in two patients' tables. See
    the module header for why per-pool IDF would not.
    """
    patients = payload["patients"]
    cap = MAX_TRIALS_FOR_EVALUATION if cap is None else cap

    # --- one vocabulary over every distinct trial the run saw ---------------
    texts, order = {}, []
    ivs = {}
    for rec in patients.values():
        for trial in rec["pool"]:
            tid = trial["nct_id"]
            if tid not in texts:
                texts[tid] = trial.get("criteria_text") or ""
                ivs[tid] = frozenset(trial.get("interventions") or [])
                order.append(tid)
    rows = tfidf_matrix([texts[t] for t in order])
    global_index = {t: i for i, t in enumerate(order)}
    # ONE DENSE N-BY-N GRAM OVER EVERY DISTINCT TRIAL, and the cost is stated
    # rather than discovered: it is O(N^2) in the number of DISTINCT trials the
    # run pooled, not in the number of patients. On this corpus that is a few
    # hundred at most -- Synthea patients share pools heavily, which
    # `pool_digest` measures -- so the matrix is small. A corpus whose patients
    # produce genuinely distinct pools would grow it quadratically, and at that
    # point the right change is to slice per pool from a sparse representation
    # rather than to materialise the whole gram. Named so the next reader meets
    # it as a known limit instead of as a memory error.
    gram = cosine_matrix(rows)

    coverage = {
        "distinct_trials": len(order),
        "with_criteria_text": sum(1 for t in order if texts[t].strip()),
        "with_interventions": sum(1 for t in order if ivs[t]),
    }
    coverage["criteria_text_coverage"] = (
        coverage["with_criteria_text"] / len(order)) if order else 0.0
    coverage["intervention_coverage"] = (
        coverage["with_interventions"] / len(order)) if order else 0.0

    per_patient = {}
    determinism_failures = []
    orphaned_kept = {}

    for stem, rec in sorted(patients.items()):
        pool = rec["pool"]
        ids = [t["nct_id"] for t in pool]
        # A KEPT ID THAT IS NOT IN THE POOL IS IMPOSSIBLE WHEN `prefix_ok`
        # HOLDS -- the cut is a prefix of the sorted pool -- and it must not be
        # able to take the analysis down if it ever does not. `classify_swaps`
        # indexes `index_of[tid]` for every baseline id, so one orphan would
        # raise KeyError and abort every table for every patient.
        #
        # DROPPED AND COUNTED, NEVER SILENTLY. The count is reported beside the
        # prefix-control violations, so a narrowing shows up as a number rather
        # than as tables that are quietly about fewer trials than they claim.
        _pool_ids = set(ids)
        kept_ids = [t for t in rec["kept_ids"] if t in _pool_ids]
        if len(kept_ids) != len(rec["kept_ids"]):
            orphaned_kept[stem] = len(rec["kept_ids"]) - len(kept_ids)
        idx = [global_index[t] for t in ids]
        # The pool's own cosine block, sliced out of the one global space.
        sim = gram[np.ix_(idx, idx)] if idx else np.zeros((0, 0))
        pool_ivs = [ivs[t] for t in ids]
        local_index = {t: i for i, t in enumerate(ids)}

        # --- (2) redundancy, over the KEPT-k, at every threshold -----------
        kept_local = [local_index[t] for t in kept_ids]
        ksim = sim[np.ix_(kept_local, kept_local)] if kept_local else np.zeros((0, 0))
        kept_ivs = [pool_ivs[i] for i in kept_local]
        red = {f"{th:.2f}": redundancy_for_pool(ksim, th, kept_ivs)
               for th in thresholds}

        # --- (3) MMR, over the FULL pool, at every lambda -------------------
        relevance = normalise_relevance([t.get("rerank_score") for t in pool])
        mmr = {}
        eligible = len(pool) >= MIN_POOL_FOR_MMR and len(pool) > len(kept_ids)
        for lam in tuple(lambdas) + (MMR_IDENTITY_LAMBDA,):
            picked = mmr_select(relevance, sim, len(kept_ids), lam, ids)
            picked_ids = [ids[i] for i in picked]
            swaps = classify_swaps(kept_ids, picked_ids, sim, local_index,
                                   headline, pool_ivs)
            swaps["selected_ids"] = picked_ids
            mmr[f"{lam:.1f}"] = swaps
            if not assert_deterministic(relevance, sim, len(kept_ids), lam, ids):
                determinism_failures.append((stem, lam))

        per_patient[stem] = {
            "pool_size": len(pool),
            "kept": len(kept_ids),
            "pool_digest": pool_digest(ids),
            "kept_digest": pool_digest(kept_ids),
            "cap_binds": rec.get("cap_binds", len(pool) > cap),
            "mmr_eligible": eligible,
            "ranks_16_to_20": [t["nct_id"] for t in pool[cap:cap + 5]],
            "redundancy": red,
            "mmr": mmr,
        }

    return {
        "coverage": coverage,
        "per_patient": per_patient,
        "determinism_failures": determinism_failures,
        "orphaned_kept": orphaned_kept,
        "thresholds": [f"{t:.2f}" for t in thresholds],
        "headline_threshold": headline,
        "lambdas": [f"{l:.1f}" for l in lambdas],
        "identity_lambda": f"{MMR_IDENTITY_LAMBDA:.1f}",
        "cap": cap,
        "corpus": summarise(per_patient, thresholds, lambdas),
    }


def summarise(per_patient: dict, thresholds, lambdas) -> dict:
    """The corpus-wide roll-up. Every rate names the denominator it used.

    THE MMR DENOMINATOR IS ``mmr_eligible`` AND NOT EVERY PATIENT. A pool at or
    under the cap is returned whole by any selector, so counting those patients
    as "MMR changed nothing" reports the CAP NOT BINDING as evidence about MMR.
    Both numbers are here; neither is presented as the other.
    """
    stems = sorted(per_patient)
    n = len(stems)
    eligible = [s for s in stems if per_patient[s]["mmr_eligible"]]
    binds = [s for s in stems if per_patient[s]["cap_binds"]]

    # DISTINCT POOLS, first occurrence wins. See pool_digest.
    _seen, _distinct = set(), []
    for stem in stems:
        d = per_patient[stem]["kept_digest"]
        if d in _seen:
            continue
        _seen.add(d)
        _distinct.append(stem)

    out = {
        "patients": n,
        "patients_cap_binds": len(binds),
        "patients_mmr_eligible": len(eligible),
        "distinct_kept_pools": len(_distinct),
        "distinct_full_pools": len({per_patient[s]["pool_digest"] for s in stems}),
        "pool_sizes": _percentiles([per_patient[s]["pool_size"] for s in stems]),
        "kept_sizes": _percentiles([per_patient[s]["kept"] for s in stems]),
        "redundancy": {},
        "mmr": {},
    }

    for th in thresholds:
        key = f"{th:.2f}"
        rows = [per_patient[s]["redundancy"][key] for s in stems]
        with_dup = [r for r in rows if r["has_duplicate"]]
        out["redundancy"][key] = {
            "pools_with_a_duplicate_pair": len(with_dup),
            "share_of_pools_with_a_duplicate_pair": (len(with_dup) / n) if n else 0.0,
            "duplicate_pairs_total": sum(r["duplicate_pairs"] for r in rows),
            "duplicate_pairs_per_pool_mean": (
                sum(r["duplicate_pairs"] for r in rows) / n) if n else 0.0,
            "largest_cluster_max": max((r["largest_cluster"] for r in rows), default=0),
            "cluster_size_histogram": _histogram(
                [c for r in rows for c in r["clusters"]]),
            "redundant_surplus_total": sum(r["redundant_surplus"] for r in rows),
            "redundant_surplus_per_pool_mean": (
                sum(r["redundant_surplus"] for r in rows) / n) if n else 0.0,
            "trials_in_a_duplicate_pair_total": sum(
                r["trials_in_a_duplicate_pair"] for r in rows),
            "intervention_pairs_total": sum(
                r.get("intervention_pairs", 0) for r in rows),
            "intervention_and_text_pairs_total": sum(
                r.get("intervention_and_text_pairs", 0) for r in rows),
        }

    # --- THE SAME REDUNDANCY, OVER THE POOLS MMR COULD ACTUALLY ACT ON -----
    #
    # A SECOND DENOMINATOR, REPORTED RATHER THAN SUBSTITUTED. The tables above
    # are over EVERY kept pool, which is the right population for "is Stage 5
    # judging redundant trials" -- a duplicate in a 9-trial pool is a wasted
    # judgement whether or not the cap binds. It is the WRONG population for
    # "could MMR fix it": a pool at or under the cap has nothing to promote, so
    # MMR is powerless there however redundant it is.
    #
    # The two answers can differ a lot and in either direction, because
    # cap-bound pools are LARGER and a larger pool has quadratically more pairs
    # to be near-duplicates. Reporting only the first would understate the case
    # for MMR; reporting only the second would overstate it by dropping every
    # pool where redundancy exists and MMR is no help. `apply_rule` reads the
    # FIRST -- stated at the rule and in the report -- and this is published
    # beside it so a reader can apply the rule the other way from one run.
    for th in thresholds:
        key = f"{th:.2f}"
        rows_e = [per_patient[s]["redundancy"][key] for s in eligible]
        with_dup_e = [r for r in rows_e if r["has_duplicate"]]
        out["redundancy"][key]["eligible_denominator"] = len(eligible)
        out["redundancy"][key]["eligible_pools_with_a_duplicate_pair"] = len(with_dup_e)
        out["redundancy"][key]["eligible_share_of_pools_with_a_duplicate_pair"] = (
            (len(with_dup_e) / len(eligible)) if eligible else 0.0)
        out["redundancy"][key]["eligible_redundant_surplus_per_pool_mean"] = (
            (sum(r["redundant_surplus"] for r in rows_e) / len(eligible))
            if eligible else 0.0)

    for th in thresholds:
        key = f"{th:.2f}"
        rows_d = [per_patient[s]["redundancy"][key] for s in _distinct]
        with_dup_d = [r for r in rows_d if r["has_duplicate"]]
        out["redundancy"][key]["distinct_denominator"] = len(_distinct)
        out["redundancy"][key]["distinct_pools_with_a_duplicate_pair"] = len(with_dup_d)
        out["redundancy"][key]["distinct_share_of_pools_with_a_duplicate_pair"] = (
            (len(with_dup_d) / len(_distinct)) if _distinct else 0.0)
        out["redundancy"][key]["distinct_redundant_surplus_per_pool_mean"] = (
            (sum(r["redundant_surplus"] for r in rows_d) / len(_distinct))
            if _distinct else 0.0)

    for lam in tuple(lambdas) + (MMR_IDENTITY_LAMBDA,):
        key = f"{lam:.1f}"
        rows = [per_patient[s]["mmr"][key] for s in eligible]
        changed = [r for r in rows if r["swapped_out"]]
        out["mmr"][key] = {
            "denominator_patients": len(eligible),
            "patients_changed": len(changed),
            "share_changed": (len(changed) / len(eligible)) if eligible else 0.0,
            "swapped_out_total": sum(r["swapped_out"] for r in rows),
            "swapped_in_total": sum(r["swapped_in"] for r in rows),
            "swapped_out_duplicate_total": sum(
                r["swapped_out_duplicate"] for r in rows),
            "swapped_out_distinct_total": sum(
                r["swapped_out_distinct"] for r in rows),
            "swapped_out_per_patient_mean": (
                sum(r["swapped_out"] for r in rows) / len(eligible)) if eligible else 0.0,
            "duplicate_share_of_swaps": (
                sum(r["swapped_out_duplicate"] for r in rows)
                / sum(r["swapped_out"] for r in rows))
            if sum(r["swapped_out"] for r in rows) else None,
        }
    return out


def _histogram(values) -> dict:
    out = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return {k: out[k] for k in sorted(out, key=int)}


#------------------------------------------------------------------------------


# ===========================================================================
# THE PRE-REGISTERED RULE
# ===========================================================================
#
# THE OPERATOR'S RULE, VERBATIM: "adopt only if redundancy is material AND
# swapped-out trials are almost entirely duplicates; otherwise reject."
#
# "Material" and "almost entirely" are not numbers, and a rule applied with
# numbers chosen after the tables are on screen is not a rule -- it is a
# preference with a rule's vocabulary. The three constants below fix them
# BEFORE the run, they are the only place they are written down, and
# `apply_rule` reads nothing else. A reader who disagrees with them can
# recompute the verdict from the same tables, which is the whole point of
# publishing them rather than embedding them in prose.

MATERIAL_POOL_SHARE = 0.25
"""Share of kept-k pools that must contain at least one near-duplicate pair.

A quarter. Below that the condition MMR exists to fix is a minority event, and
a reordering applied to every patient to serve a minority is paid for by the
majority.
"""

MATERIAL_SURPLUS_PER_POOL = 1.0
"""Mean reducible surplus per pool, in trial slots, for redundancy to be material.

ONE WHOLE SLOT. ``redundant_surplus`` is how many trials could be removed if
every duplicate cluster collapsed to one representative, so this says the
average patient is losing at least one of their fifteen evaluated slots to a
trial already represented. Below one slot the ceiling on what MMR could recover
is under a fifteenth of the pool, which is not worth a change to what the
pipeline judges.

BOTH CONDITIONS MUST HOLD. Widespread-but-tiny (many pools, a fraction of a
slot each) and rare-but-large (a handful of pools, several slots) are both
consistent with a single condition and neither is a case for changing every
patient's selection.
"""

ALMOST_ENTIRELY_DUPLICATES = 0.90
"""Share of swapped-out trials that must be near-duplicates of a retained trial.

NINE IN TEN. The asymmetry that sets it: a duplicate removed costs nothing --
its near-twin is still judged -- while a DISTINCT trial removed is a trial the
patient would have been assessed against and now is not, and that loss appears
in no counter, no log line and no stored row. It looks like a patient with
fewer matches. That is ``medcpt_calibration``'s argument for taking the lower
of two floors, one stage over: when an error is invisible, the bar in front of
it is set high.
"""


def apply_rule(summary: dict, lam: str) -> dict:
    """The verdict for one lambda. Reads the three constants above and nothing else."""
    red = summary["redundancy"][f"{NEAR_DUPLICATE_THRESHOLD:.2f}"]
    mmr = summary["mmr"][lam]

    share = red["share_of_pools_with_a_duplicate_pair"]
    surplus = red["redundant_surplus_per_pool_mean"]
    dup_share = mmr["duplicate_share_of_swaps"]

    material = (share >= MATERIAL_POOL_SHARE
                and surplus >= MATERIAL_SURPLUS_PER_POOL)
    # A lambda that swapped NOTHING has no duplicate share -- `None`, not 1.0.
    # It fails the second condition, and that is correct rather than harsh: a
    # selector that changes nothing is not evidence for adopting it.
    clean = dup_share is not None and dup_share >= ALMOST_ENTIRELY_DUPLICATES

    return {
        "lambda": lam,
        "material_share": share,
        "material_share_required": MATERIAL_POOL_SHARE,
        "material_share_met": share >= MATERIAL_POOL_SHARE,
        "material_surplus": surplus,
        "material_surplus_required": MATERIAL_SURPLUS_PER_POOL,
        "material_surplus_met": surplus >= MATERIAL_SURPLUS_PER_POOL,
        "redundancy_material": material,
        "duplicate_share_of_swaps": dup_share,
        "duplicate_share_required": ALMOST_ENTIRELY_DUPLICATES,
        "swaps_almost_entirely_duplicates": clean,
        "distinct_swapped_out": mmr["swapped_out_distinct_total"],
        "verdict": "ADOPT" if (material and clean) else "REJECT",
    }


#------------------------------------------------------------------------------


# ===========================================================================
# THE REPORT
# ===========================================================================

def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _num(x, nd=2) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def render_report(payload: dict, analysis: dict) -> str:
    """The markdown report, as one string. One text, however many callers.

    ``CohortSelection.describe``'s shape: a consumer prints this rather than
    composing its own, so the file on disk and anything else that reports this
    run cannot come to describe it two ways.
    """
    prov = payload["provenance"]
    gate = payload["gate"]
    summ = analysis["corpus"]
    head = f"{NEAR_DUPLICATE_THRESHOLD:.2f}"
    L = []
    a = L.append

    a("# MMR Redundancy Measurement -- Stage 4 kept pools")
    a("")
    a("**MEASUREMENT ONLY. NO PIPELINE FILE WAS EDITED AND NO PIPELINE "
      "BEHAVIOUR CHANGED.** This report exists so the operator can rule on "
      "adopting Maximal Marginal Relevance in Stage 4 from evidence. The "
      "ruling is the operator's; nothing here implements it.")
    a("")
    a(f"Generated: `{prov['generated_utc']}`  ")
    a(f"Script: `{prov['entry_point']}`  ")
    a(f"Module: `oncotriage/evaluation/mmr_redundancy.py` "
      f"(schema {payload['schema_version']})")
    a("")

    # ---- what ran -------------------------------------------------------
    a("## 1. What ran, and what it cost")
    a("")
    a("| | |")
    a("|---|---|")
    a("| Stages driven | 1, 2, 3, 4 -- `node_query_expansion`, "
      "`node_hybrid_retrieval`, `node_cross_encoder_rerank`, "
      "`node_rule_based_filter` |")
    a("| Stage 5 | **never reached.** This module imports no evaluation node "
      "and calls no graph. |")
    a(f"| Retrieval arm | `{prov['retrieval_arm']}` |")
    a(f"| Qdrant collection | `{prov['qdrant_collection']}` "
      f"({prov['qdrant_points']} points) |")
    a(f"| Qdrant endpoint | {prov['qdrant_endpoint_source']} |")
    _real = prov.get("cost_realised") or prov["cost"]
    a(f"| Embedding calls ATTEMPTED | **{_real['calls']}** "
      f"(`{_real['model']}`, one per patient, Stage 2 dense channel) |")
    a(f"| Embedding spend | **<= ${_real['usd_upper_bound']:.6f}** "
      f"(upper bound over {_real['tokens_upper_bound']:,} tokens, priced "
      f"through `utils.get_model_cost`) |")
    a(f"| ...announced before the run | <= "
      f"${prov['cost']['usd_upper_bound']:.6f} over "
      f"{prov['cost']['calls']} patients -- the REQUESTED cohort, which the "
      f"timing gate may truncate |")
    a(f"| AWS/Bedrock clients built | **{prov['boto3_clients_built']}** "
      f"(guard: {prov['boto3_guard']}) |")
    a(f"| Cross-encoder | `{prov['cross_encoder_model']}` -- local, from cache |")
    a(f"| Wall clock | {payload['wall_seconds'] / 60.0:.1f} min |")
    a("")

    # ---- cohort ---------------------------------------------------------
    a("## 2. Cohort -- exactly reproducible")
    a("")
    c = prov["cohort"]
    a("| | |")
    a("|---|---|")
    a(f"| Corpus | {c['corpus_size']} bundles |")
    a(f"| Requested size | {c['cohort_requested']} "
      f"(this measurement's argument; `config.CAMPAIGN_COHORT_SIZE` is "
      f"{prov['configured_cohort_size']}) |")
    a(f"| Drawn | {c['cohort_size']} |")
    a(f"| Seed | `{c['cohort_seed']}` |")
    a(f"| Algorithm | `{c['algorithm']}` |")
    a(f"| Digest | `{c['cohort_digest']}` (`{c['digest_algorithm']}`) |")
    a(f"| Group shares | " + ", ".join(
        f"{g}={n}" for g, n in sorted(c["group_counts"].items())) + " |")
    a("")
    # THE PROJECTION IS REPORTED ONLY WHEN IT WAS TAKEN. An earlier draft
    # printed `projected_seconds or 0`, so a run too short to reach the probe
    # size rendered "projected 0.0 min" beside "probed 10 patients" -- a
    # measurement nobody made, in the shape of one that had been. That is the
    # forged-zero this project's NULL convention exists to forbid, and it is
    # worse here than a missing line because a reader would take it as
    # evidence the gate had cleared the run.
    if gate["projected_seconds"] is None:
        a(f"**Timing gate.** NOT EVALUATED -- the run measured "
          f"{gate['requested_patients']} patient(s), fewer than the "
          f"{gate['probe_patients']} the gate probes before it projects, so "
          f"no projection was taken and none is reported. The "
          f"{gate['budget_seconds'] / 60.0:.0f} min budget was never tested.")
    else:
        a(f"**Timing gate.** Probed {gate['probe_patients']} patients at "
          f"{_num(gate['seconds_per_patient'], 1)}s each; projected "
          f"{_num(gate['projected_seconds'] / 60.0, 1)} min for "
          f"{gate['requested_patients']} against a "
          f"{gate['budget_seconds'] / 60.0:.0f} min budget. "
          + (f"**GATE FIRED** -- fell back to the project's seeded "
             f"{gate['final_patients']}-patient stability draw "
             f"(`CAMPAIGN_STABILITY_SEED` / "
             f"`CAMPAIGN_STABILITY_SAMPLE_SIZE`). Every number below is over "
             f"that draw and over nothing else."
             if gate["fired"] else
             "Gate did not fire; the full requested cohort ran."))
    a("")
    a(f"Patients measured: **{summ['patients']}**. "
      f"Bundles that failed to run: {len(payload['parse_failures'])}. "
      f"Pre-slice/post-slice prefix control violations: "
      f"**{len(payload['prefix_violations'])}** "
      f"(a violation would mean the uncapped pool's first k are not the "
      f"shipped kept-k, i.e. the two runs disagree). Kept trials absent from "
      f"their own pool: **{len(analysis.get('orphaned_kept') or {})}** "
      f"(impossible while the prefix control holds; dropped and counted "
      f"rather than allowed to abort the analysis).")
    a("")
    a("<details><summary>Drawn patient stems (click to expand) -- the draw is "
      "reproducible from the seed, the size and this list</summary>")
    a("")
    a("```")
    for stem in sorted(analysis["per_patient"]):
        a(stem)
    a("```")
    a("</details>")
    a("")

    # ---- pools ----------------------------------------------------------
    a("## 3. The pools")
    a("")
    a(f"`MAX_TRIALS_FOR_EVALUATION` = {analysis['cap']}.")
    a("")
    a("| | p5 | p25 | median | p75 | p95 |")
    a("|---|---|---|---|---|---|")
    ps = summ["pool_sizes"]
    ks = summ["kept_sizes"]
    a(f"| Post-filter pool size (pre-slice) | {_num(ps['5'],0)} | "
      f"{_num(ps['25'],0)} | {_num(ps['50'],0)} | {_num(ps['75'],0)} | "
      f"{_num(ps['95'],0)} |")
    a(f"| Kept (post-slice) | {_num(ks['5'],0)} | {_num(ks['25'],0)} | "
      f"{_num(ks['50'],0)} | {_num(ks['75'],0)} | {_num(ks['95'],0)} |")
    a("")
    a(f"**The cap binds for {summ['patients_cap_binds']} of "
      f"{summ['patients']} patients "
      f"({_pct(summ['patients_cap_binds'] / summ['patients'] if summ['patients'] else 0)}).** "
      f"For the rest the post-filter pool is at or under "
      f"{analysis['cap']} trials, so every trial that survives Stage 4 is "
      f"already evaluated and **MMR has nothing to swap** -- any selector "
      f"returns the whole pool. Those patients are excluded from the MMR "
      f"denominator below and counted here instead.")
    a("")
    a(f"MMR-eligible patients (pool strictly larger than kept): "
      f"**{summ['patients_mmr_eligible']}**.")
    a("")
    a("### 3a. HOW MUCH INDEPENDENT EVIDENCE THIS IS -- read before section 5")
    a("")
    a(f"| | |")
    a(f"|---|---|")
    a(f"| Patients | {summ['patients']} |")
    a(f"| DISTINCT kept pools | **{summ['distinct_kept_pools']}** |")
    a(f"| DISTINCT post-filter pools | {summ['distinct_full_pools']} |")
    a(f"| DISTINCT trials across every pool | "
      f"{analysis['coverage']['distinct_trials']} |")
    a("")
    if summ["distinct_kept_pools"] < summ["patients"]:
        a(f"**WARNING: patients share pools, so the sample is narrower than "
          f"the patient count.** {summ['patients']} patients produce only "
          f"{summ['distinct_kept_pools']} distinct kept pools and "
          f"{analysis['coverage']['distinct_trials']} distinct trials. Synthea "
          f"patients within one cancer group carry near-identical condition "
          f"lists, so Stage 1 builds the same expanded query, Stage 2 "
          f"retrieves the same trials and Stage 3 hands back the same pool -- "
          f"the degeneracy "
          f"`oncotriage/evaluation/medcpt_calibration.py:pool_digest` "
          f"documents for the same corpus.")
        a("")
        a("The per-patient rate is still the one the rule reads, and that is "
          "deliberate: **production gates per patient**, so a pool that "
          "recurs ten times really is judged ten times and really does waste "
          "ten slots. What the per-patient rate must NOT be read as is ten "
          "independent observations of trial redundancy. Section 5 reports "
          "the per-distinct-pool figure beside it so both readings are "
          "available, and neither is presented as the other.")
        a("")
        a("**This is the single largest limitation on the evidence below, and "
          "it is a property of a synthetic corpus rather than of MMR.** On a "
          "real cohort with genuinely varied condition lists the distinct-pool "
          "count would rise toward the patient count and these tables would "
          "carry correspondingly more weight.")
        a("")

    # ---- coverage -------------------------------------------------------
    cov = analysis["coverage"]
    a("## 4. Signal coverage -- measured before it is relied on")
    a("")
    a("| Signal | Trials carrying it | Coverage |")
    a("|---|---|---|")
    a(f"| (a) eligibility criteria text | {cov['with_criteria_text']} / "
      f"{cov['distinct_trials']} | {_pct(cov['criteria_text_coverage'])} |")
    a(f"| (b) registered interventions | {cov['with_interventions']} / "
      f"{cov['distinct_trials']} | {_pct(cov['intervention_coverage'])} |")
    a("")
    a(f"Similarity method: `{SIMILARITY_METHOD}`.")
    a("")

    # ---- redundancy -----------------------------------------------------
    a("## 5. Redundancy inside the kept pools")
    a("")
    a("Computed over each patient's **kept-k** pool -- the trials Stage 5 "
      "actually judges -- which is where a wasted slot is actually paid for.")
    a("")
    a("| Cosine threshold | Pools with >=1 duplicate pair | Duplicate pairs "
      "(total) | Mean pairs/pool | Largest cluster | Reducible surplus "
      "(total slots) | Mean surplus/pool |")
    a("|---|---|---|---|---|---|---|")
    for th in analysis["thresholds"]:
        r = summ["redundancy"][th]
        mark = " **<-- headline**" if th == head else ""
        a(f"| {th}{mark} | {r['pools_with_a_duplicate_pair']} "
          f"({_pct(r['share_of_pools_with_a_duplicate_pair'])}) | "
          f"{r['duplicate_pairs_total']} | "
          f"{_num(r['duplicate_pairs_per_pool_mean'])} | "
          f"{r['largest_cluster_max']} | {r['redundant_surplus_total']} | "
          f"{_num(r['redundant_surplus_per_pool_mean'])} |")
    a("")
    hr = summ["redundancy"][head]
    a("Cluster-size histogram at the headline threshold "
      f"({head}): `{hr['cluster_size_histogram']}`")
    a("")
    a("**The same figures over the pools MMR could actually act on.** The "
      "table above is over EVERY kept pool, which is the right population for "
      "*is Stage 5 judging redundant trials* -- a duplicate in a nine-trial "
      "pool is a wasted judgement whether or not the cap binds. It is the "
      "wrong population for *could MMR fix it*: a pool at or under the cap "
      "has nothing to promote, so MMR is powerless there however redundant it "
      "is. Cap-bound pools are also larger, and a larger pool has "
      "quadratically more pairs that could be near-duplicates.")
    a("")
    a("| Population | Pools | With >=1 duplicate pair | Mean surplus/pool |")
    a("|---|---|---|---|")
    a(f"| All kept pools **(the rule reads this one)** | {summ['patients']} | "
      f"{hr['pools_with_a_duplicate_pair']} "
      f"({_pct(hr['share_of_pools_with_a_duplicate_pair'])}) | "
      f"{_num(hr['redundant_surplus_per_pool_mean'])} |")
    a(f"| MMR-eligible pools only | {hr['eligible_denominator']} | "
      f"{hr['eligible_pools_with_a_duplicate_pair']} "
      f"({_pct(hr['eligible_share_of_pools_with_a_duplicate_pair'])}) | "
      f"{_num(hr['eligible_redundant_surplus_per_pool_mean'])} |")
    a(f"| DISTINCT kept pools (duplicates collapsed) | "
      f"{hr['distinct_denominator']} | "
      f"{hr['distinct_pools_with_a_duplicate_pair']} "
      f"({_pct(hr['distinct_share_of_pools_with_a_duplicate_pair'])}) | "
      f"{_num(hr['distinct_redundant_surplus_per_pool_mean'])} |")
    a("")
    a("The third row is section 3a's warning made numeric: it is the same "
      "finding with recurring pools counted once. It is NOT the rule's "
      "denominator -- production gates per patient -- but a reader who wants "
      "to know how many INDEPENDENT observations sit behind the first row "
      "should read it.")
    a("")
    a(f"**Second signal.** Kept-pool pairs sharing at least one registered "
      f"intervention: **{hr['intervention_pairs_total']}**; of those, "
      f"**{hr['intervention_and_text_pairs_total']}** also reach the {head} "
      f"text threshold. The two signals are independent -- one is the "
      f"criteria prose, the other a metadata field -- so their agreement is "
      f"what calibrates the text threshold from outside itself, and their "
      f"disagreement is a finding rather than an error.")
    a("")

    # ---- MMR ------------------------------------------------------------
    a("## 6. Offline MMR simulation")
    a("")
    a("Objective, exactly as implemented:")
    a("")
    a("```")
    a("MMR(d) = lambda * rel(d) - (1 - lambda) * max_{s in S} sim(d, s)")
    a("")
    a("  rel  = " + RELEVANCE_NORMALISATION + " of rerank_score (the boosted")
    a("         score Stage 4 sorts on)")
    a("  sim  = the same TF-IDF cosine as section 5")
    a("  S    = the already-selected set; max over an empty S is 0.0")
    a("  ties = broken on nct_id ascending, so the result never depends on")
    a("         the order the pool was built in")
    a("```")
    a("")
    a(f"Selection is re-run twice per patient per lambda and required to be "
      f"identical. **Determinism failures: "
      f"{len(analysis['determinism_failures'])}.**")
    a("")
    ident = analysis["identity_lambda"]
    im = summ["mmr"][ident]
    a(f"**Control (lambda = {ident}, pure relevance).** Swapped out: "
      f"{im['swapped_out_total']}. This must be 0 -- at lambda 1 the diversity "
      f"term vanishes and MMR must reproduce the shipped selection exactly. "
      f"A non-zero here would mean the relevance normalisation, not the "
      f"diversity term, is moving trials, and every number below would be "
      f"measuring that bug instead.")
    a("")
    a(f"Denominator: the **{summ['patients_mmr_eligible']} MMR-eligible "
      f"patients**, not all {summ['patients']}. A pool at or under the cap is "
      f"returned whole by any selector; counting those as 'MMR changed "
      f"nothing' would report the cap not binding as evidence about MMR.")
    a("")
    a("| lambda | Patients changed | Swapped OUT | Swapped IN | "
      "OUT that are duplicates | **OUT that are DISTINCT** | Duplicate share |")
    a("|---|---|---|---|---|---|---|")
    for lam in analysis["lambdas"]:
        m = summ["mmr"][lam]
        a(f"| {lam} | {m['patients_changed']} "
          f"({_pct(m['share_changed'])}) | {m['swapped_out_total']} | "
          f"{m['swapped_in_total']} | {m['swapped_out_duplicate_total']} | "
          f"**{m['swapped_out_distinct_total']}** | "
          f"{_pct(m['duplicate_share_of_swaps'])} |")
    a("")
    a("**A swapped-out DISTINCT trial is a potential false drop**: a trial the "
      "shipped pipeline would have had judged, which MMR removes with no "
      "near-duplicate retained to stand in for it. It is the cost side of the "
      "trade and it is invisible in production -- it looks like a patient with "
      "fewer matches, and no counter, log line or stored row records it. A "
      "swap is only counted as a duplicate when the removed trial is at or "
      "above the threshold of a **retained** trial, or shares a registered "
      "intervention with one; the intervention signal can only exonerate a "
      "removal, never condemn one.")
    a("")

    # ---- the rule -------------------------------------------------------
    a("## 7. Findings -- the pre-registered rule, applied verbatim")
    a("")
    a("> **Adopt only if redundancy is material AND swapped-out trials are "
      "almost entirely duplicates; otherwise reject.**")
    a("")
    a("The two vague terms were fixed **before** this run, in "
      "`oncotriage/evaluation/mmr_redundancy.py`, and `apply_rule` reads "
      "nothing else:")
    a("")
    a(f"- **material** = at least **{_pct(MATERIAL_POOL_SHARE)}** of kept "
      f"pools contain a near-duplicate pair at cosine >= "
      f"{NEAR_DUPLICATE_THRESHOLD:.2f} **and** the mean reducible surplus is "
      f"at least **{MATERIAL_SURPLUS_PER_POOL:.1f}** trial slot per pool.")
    a(f"- **almost entirely duplicates** = at least "
      f"**{_pct(ALMOST_ENTIRELY_DUPLICATES)}** of swapped-out trials are "
      f"near-duplicates of a retained trial.")
    a("")
    a("| lambda | Redundancy material? | Swaps almost all duplicates? | "
      "Distinct trials dropped | **Verdict** |")
    a("|---|---|---|---|---|")
    verdicts = {}
    for lam in analysis["lambdas"]:
        v = apply_rule(summ, lam)
        verdicts[lam] = v
        a(f"| {lam} | {'YES' if v['redundancy_material'] else 'NO'} "
          f"(share {_pct(v['material_share'])} vs "
          f"{_pct(v['material_share_required'])}; surplus "
          f"{_num(v['material_surplus'])} vs "
          f"{_num(v['material_surplus_required'],1)}) | "
          f"{'YES' if v['swaps_almost_entirely_duplicates'] else 'NO'} "
          f"({_pct(v['duplicate_share_of_swaps'])} vs "
          f"{_pct(v['duplicate_share_required'])}) | "
          f"{v['distinct_swapped_out']} | **{v['verdict']}** |")
    a("")
    if all(v["verdict"] == "REJECT" for v in verdicts.values()):
        a("### Which way the numbers point: **REJECT, at every lambda tested.**")
    elif all(v["verdict"] == "ADOPT" for v in verdicts.values()):
        a("### Which way the numbers point: **ADOPT, at every lambda tested.**")
    else:
        a("### Which way the numbers point: **MIXED** -- see the verdict "
          "column per lambda.")
    a("")
    first = verdicts[analysis["lambdas"][0]]
    _hr = summ["redundancy"][f"{NEAR_DUPLICATE_THRESHOLD:.2f}"]
    a(f"**The rule reads the ALL-KEPT-POOLS denominator** (section 5). Read "
      f"instead over MMR-eligible pools only, the two material figures are "
      f"{_pct(_hr['eligible_share_of_pools_with_a_duplicate_pair'])} of pools "
      f"and {_num(_hr['eligible_redundant_surplus_per_pool_mean'])} slots per "
      f"pool -- against bars of {_pct(MATERIAL_POOL_SHARE)} and "
      f"{_num(MATERIAL_SURPLUS_PER_POOL, 1)}. That reading is published so a "
      f"reader can apply the rule the other way from this one run; it is "
      f"stated rather than substituted, because which denominator the rule "
      f"uses is a choice and a choice made after seeing both is not a "
      f"pre-registration.")
    a("")
    a(f"The binding condition is stated rather than left to be inferred. "
      f"Redundancy is "
      f"{'material' if first['redundancy_material'] else '**not** material'}: "
      f"{_pct(first['material_share'])} of kept pools carry a near-duplicate "
      f"pair (rule: {_pct(MATERIAL_POOL_SHARE)}) and the mean reducible "
      f"surplus is {_num(first['material_surplus'])} slots per pool "
      f"(rule: {_num(MATERIAL_SURPLUS_PER_POOL, 1)}).")
    a("")

    # ---- limits ---------------------------------------------------------
    a("## 8. What this measurement cannot see")
    a("")
    a("- **It measures text, not clinical equivalence.** Two trials of one "
      "drug at two doses share nearly all their criteria text and are not "
      "interchangeable; two trials of different drugs can share boilerplate. "
      "Cosine over criteria text is a proxy.")
    a("- **It cannot say a swapped-in trial is better.** That is a Stage 5 "
      "verdict and Stage 5 was not run. The measurement bounds the COST of "
      "MMR (distinct trials dropped); it does not measure the benefit.")
    a("- **Redundancy is a property of this index.** A re-scrape can move "
      "every number here.")
    a("- **The intervention signal under-counts.** Names are matched "
      "case-folded and exact -- no stemming, no synonyms, no substring match "
      "-- so 'Pembrolizumab' and 'Pembrolizumab 200mg' are two interventions. "
      "That is the safe direction for a corroborating signal.")
    a("- **The MeSH boost is in the relevance term**, because `rerank_score` "
      "is what Stage 4 sorts on. A simulation over `rerank_score_raw` would "
      "be over an order the pipeline does not use.")
    a("")
    a("---")
    a("")
    a("Re-run the analysis at other thresholds or lambdas for free, with no "
      "network and no spend, from the persisted pools:")
    a("")
    a("```bash")
    a(f"python {prov['entry_point']} --analyse-only "
      f"<pools.json> --lambdas 0.2,0.4,0.6 --threshold 0.8")
    a("```")
    return "\n".join(L)


#------------------------------------------------------------------------------


# ===========================================================================
# THE AWS TRIPWIRE
# ===========================================================================

def arm_boto3_guard() -> tuple:
    """Patch ``boto3.client`` to RECORD and REFUSE. Returns ``(state, guard)``.

    ``tests/test_mcp_server_stdio_contract.py``'s tripwire, adopted verbatim in
    shape and in reasoning:

    *   IT PATCHES ``boto3.client`` AND NOT a named factory, because the
        question is "did this process construct an AWS client", not "did one
        function run". ``oncotriage/config.py`` imports boto3 INSIDE the
        function that needs it, so the module object in ``sys.modules`` is the
        one looked up at call time and this patch reaches it.
    *   IT REFUSES RATHER THAN RECORDING AND BUILDING. Building "just to see"
        is how a run that reports it spends nothing spends something: botocore
        resolves credentials during construction and can probe the instance
        metadata service.
    *   boto3 ABSENT IS RECORDED, NOT SKIPPED AND NOT PASSED. The property
        holds for a stronger reason, and a reader must not mistake an inert
        guard for one that ran.

    THE SHIPPED PROVIDER IS ``bedrock_anthropic``, so this is not decoration:
    any path that reached Stage 5 would build a Converse client here and be
    stopped, loudly, with the attempt recorded in the report.
    """
    built = []
    try:
        import boto3
    except Exception as exc:                                    # noqa: BLE001
        return built, f"inert: boto3 not importable ({type(exc).__name__})"

    def _refusing_client(*args, **kwargs):
        built.append(args[0] if args else kwargs.get("service_name"))
        raise RuntimeError(
            "oncotriage/evaluation/mmr_redundancy.py: boto3.client() was "
            "called. This measurement drives Stages 1-4 only and must build no "
            "AWS client at all; see arm_boto3_guard().")

    boto3.client = _refusing_client
    return built, "armed"


#------------------------------------------------------------------------------


# ===========================================================================
# ENTRY POINT
# ===========================================================================

REPORT_FILENAME = "MMR REDUNDANCY MEASUREMENT.md"
POOLS_FILENAME = "mmr_redundancy_pools.json"


def _default_out_dir() -> str:
    """Where the POOLS JSON lands when no ``--out-dir`` is given.

    ``paths.results_path`` -- an EXISTING path variable. A new one would mean
    editing ``oncotriage/paths.py``, which is a pipeline file this measurement
    is not permitted to touch, and would need a matching entry in the Docker
    table and the CI directory skeleton besides.

    RESOLVED LAZILY, INSIDE A FUNCTION. Reading it at module scope would glob
    the sibling data tree at import, which is the hole pass 20c-2c closed in
    ``registries/mesh.py``.
    """
    return paths.results_path


def _default_report_path() -> str:
    """Where the MARKDOWN REPORT lands when no ``--report`` is given.

    THE CODE ROOT, NOT THE RESULTS TREE, AND THE SPLIT IS THE POINT.

    ``04- Results/`` is a sibling directory and is NOT in the repository --
    only ``03- Code/`` is version-controlled. A report an operator rules from
    belongs with the other records that are: ``Exception and Fallback
    Audit.md``, ``FIXTURE CAPTURE RECORD.md``, ``DOCKER CLEAN BRING-UP.md``.
    Written to the results tree it would be a finding that no commit carries,
    that no review sees and that a ``down -v`` or a tidy-up can delete.

    THE POOLS JSON GOES THE OTHER WAY, deliberately: it is DATA -- megabytes of
    criteria text, one file per run, regenerable -- and the same argument that
    puts the report in git keeps that out of it.

    ``paths.code_path`` rather than ``__file__``'s grandparent: the path
    variable is what the rest of the project resolves the code directory by,
    and it is correct inside the container where ``__file__`` arithmetic would
    also work but would be a second derivation of one fact.
    """
    return os.path.join(paths.code_path, REPORT_FILENAME)


def main(argv=None) -> int:
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(
        description="Measure near-duplicate redundancy in Stage 4's kept "
                    "pools and simulate MMR offline. MEASUREMENT ONLY -- "
                    "changes no pipeline behaviour and never reaches Stage 5.")
    parser.add_argument("--patients", type=int, default=300,
                        help="cohort size to draw (default 300; "
                             "config.CAMPAIGN_COHORT_SIZE is not used, so the "
                             "measurement states the size it drew)")
    parser.add_argument("--seed", default=None,
                        help="cohort seed (default config.CAMPAIGN_COHORT_SEED)")
    parser.add_argument("--budget-minutes", type=float,
                        default=TIMING_BUDGET_SECONDS / 60.0,
                        help="timing-gate budget; over it the run falls back "
                             "to the project's seeded 50-patient stability draw")
    parser.add_argument("--threshold", type=float,
                        default=NEAR_DUPLICATE_THRESHOLD,
                        help=f"headline near-duplicate cosine "
                             f"(default {NEAR_DUPLICATE_THRESHOLD})")
    parser.add_argument("--lambdas", default=None,
                        help="comma-separated MMR lambdas (default "
                             + ",".join(str(x) for x in MMR_LAMBDAS) + ")")
    parser.add_argument("--out-dir", default=None,
                        help="where the pools JSON goes (default: the "
                             "project's results directory -- it is data, and "
                             "it is not version-controlled)")
    parser.add_argument("--report", default=None,
                        help="explicit path for the markdown report "
                             "(default: the code root, beside the project's "
                             "other records, because it IS one)")
    parser.add_argument("--analyse-only", default=None, metavar="POOLS_JSON",
                        help="skip retrieval entirely and re-analyse a pools "
                             "file written by an earlier run. FREE: no "
                             "network, no embedding call, no model load.")
    parser.add_argument("--bm25-only", action="store_true",
                        help="run the shipped retrieval_mode='bm25_only' "
                             "ablation: no OpenAI client is built and nothing "
                             "is spent, but the fused pool loses the dense "
                             "channel and is NOT the pool production builds")
    args = parser.parse_args(argv)

    lambdas = (MMR_LAMBDAS if not args.lambdas else
               tuple(float(x) for x in args.lambdas.split(",") if x.strip()))
    out_dir = args.out_dir or _default_out_dir()

    # THE GUARD IS ARMED FOR THE WHOLE RUN AND BEFORE ANYTHING ELSE, including
    # the --analyse-only path -- an analysis that built an AWS client would be
    # exactly as wrong as a collection that did.
    boto3_built, boto3_guard = arm_boto3_guard()

    # ---- the free path ---------------------------------------------------
    if args.analyse_only:
        payload = load_pools(args.analyse_only)
        analysis = analyse(payload, headline=args.threshold, lambdas=lambdas)
        text = render_report(payload, analysis)
        report_path = args.report or _default_report_path()
        with open(report_path, "w") as handle:
            handle.write(text + "\n")
        console.out(f"Report written to {report_path}")
        console.out(f"boto3 clients built: {len(boto3_built)} ({boto3_guard})")
        return 0

    # ---- the collecting path --------------------------------------------
    cost = estimate_dense_channel_cost(args.patients)
    console.out("=" * 78)
    console.out("MMR REDUNDANCY MEASUREMENT -- Stages 1-4 only, Stage 5 never "
                "reached")
    console.out("=" * 78)
    if args.bm25_only:
        console.out("[Cost] --bm25-only: no embedding call, nothing is spent. "
                    "THE POOL IS NOT PRODUCTION'S -- the dense channel is "
                    "absent from the RRF fusion.")
    else:
        console.out(f"[Cost] Stage 2 dense channel: <= {cost['calls']} "
                    f"{cost['model']} calls, upper bound "
                    f"${cost['usd_upper_bound']:.6f}. Stages 1, 3 and 4 call "
                    f"no priced endpoint.")
    console.out(f"[Guard] boto3.client: {boto3_guard}")

    selection, fhir_files = select_cohort(args.patients, args.seed)
    fallback_files = files_for_stems(fhir_files, selection.stability_stems)

    ablation_flags = {"retrieval_mode": "bm25_only"} if args.bm25_only else None

    with CaffeinateSession("mmr-redundancy"):
        run = collect_pools(
            selection.files,
            fallback_files=fallback_files,
            budget_seconds=args.budget_minutes * 60.0,
            ablation_flags=ablation_flags,
        )

    collection = resolve_qdrant_collection()
    try:
        points = deps.get_qdrant_client().get_collection(collection).points_count
    except Exception as exc:                                    # noqa: BLE001
        # Provenance, not a dependency. A count that could not be read is
        # RECORDED as unreadable rather than forged into a number.
        points = f"unreadable ({type(exc).__name__})"

    # THE REALISED COST, priced through the same one owner as the announced
    # bound. Both are reported: the bound is what the run PROMISED before it
    # spent anything and is priced against the REQUESTED cohort, and this is
    # what it actually attempted after the timing gate had its say. Reporting
    # only the first names a spend that did not happen; reporting only the
    # second loses the promise the operator authorised the run on.
    realised = estimate_dense_channel_cost(run.get("patients_attempted") or 0)

    provenance = {
        "generated_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z"),
        "entry_point": "measure_mmr_redundancy.py",
        "retrieval_arm": ("bm25_only ABLATION -- not the shipped pool"
                          if args.bm25_only else
                          "full 4-channel RRF fusion (shipped)"),
        "qdrant_collection": collection,
        "qdrant_points": points,
        "qdrant_endpoint_source": "config.get_qdrant_url()",
        "cross_encoder_model": CROSS_ENCODER_MODEL,
        "configured_cohort_size": CAMPAIGN_COHORT_SIZE,
        "cohort": selection.record(),
        "cost_realised": (realised if not args.bm25_only else
                          {"model": "none (bm25_only ablation)", "calls": 0,
                           "tokens_upper_bound": 0, "usd_upper_bound": 0.0,
                           "tokens_per_query_assumed": 0}),
        "cost": (cost if not args.bm25_only else
                 {"model": "none (bm25_only ablation)", "calls": 0,
                  "tokens_upper_bound": 0, "usd_upper_bound": 0.0,
                  "tokens_per_query_assumed": 0}),
        "boto3_guard": boto3_guard,
        "boto3_clients_built": len(boto3_built),
        "similarity_method": SIMILARITY_METHOD,
        "rule": {
            "material_pool_share": MATERIAL_POOL_SHARE,
            "material_surplus_per_pool": MATERIAL_SURPLUS_PER_POOL,
            "almost_entirely_duplicates": ALMOST_ENTIRELY_DUPLICATES,
        },
    }

    os.makedirs(out_dir, exist_ok=True)
    pools_path = os.path.join(out_dir, POOLS_FILENAME)
    save_pools(run, pools_path, provenance)
    console.out(f"\nPools written to {pools_path}")

    if not run["results"]:
        console.out("NO PATIENT COULD BE MEASURED -- nothing to analyse.")
        return 1

    payload = load_pools(pools_path)
    analysis = analyse(payload, headline=args.threshold, lambdas=lambdas)
    text = render_report(payload, analysis)
    report_path = args.report or _default_report_path()
    with open(report_path, "w") as handle:
        handle.write(text + "\n")

    # The console summary is deliberately SHORT and points at the report. Two
    # renderings of one measurement is two things that can disagree.
    summ = analysis["corpus"]
    head = f"{args.threshold:.2f}"
    console.out("")
    console.out("=" * 78)
    console.out(f"patients measured        : {summ['patients']}")
    console.out(f"cap binds for            : {summ['patients_cap_binds']}")
    console.out(f"MMR-eligible             : {summ['patients_mmr_eligible']}")
    console.out(f"pools with a duplicate   : "
                f"{summ['redundancy'][head]['pools_with_a_duplicate_pair']} "
                f"({_pct(summ['redundancy'][head]['share_of_pools_with_a_duplicate_pair'])})")
    console.out(f"prefix control violations: {len(run['prefix_violations'])}")
    console.out(f"determinism failures     : {len(analysis['determinism_failures'])}")
    console.out(f"boto3 clients built      : {len(boto3_built)} ({boto3_guard})")
    for lam in analysis["lambdas"]:
        v = apply_rule(summ, lam)
        console.out(f"  lambda {lam}: {v['verdict']}  "
                    f"(distinct trials dropped: {v['distinct_swapped_out']})")
    console.out("=" * 78)
    console.out(f"Report written to {report_path}")
    return 0


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Sep  2 2026

@author: ramyalsaffar
"""
