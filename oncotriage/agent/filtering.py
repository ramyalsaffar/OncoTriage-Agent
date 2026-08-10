"""Stage 4: the rule-based filter.

Item 20c, pass 2c: "13- LangGraph Agent.py" lines 2115-2316, verbatim except
for the MeSH filter accessor.

MeSH site relevance, cancer stage ordinal, histology, age and sex, then a
dynamic quality threshold and the cost cap. The stage and histology comparisons
are integer and set operations because ``oncotriage.extraction`` did the parsing
at INDEX time -- unknown becomes None, and None means the trial passes.

``mesh_filter_applied`` is decided ONCE here, not per trial, and it is recorded:
Stage 5's system prompt asserts to the model that disease relevance "has already
been confirmed", and that sentence is only true when the filter actually ran. In
the other three cases -- ablated, no filter loaded, patient never resolved to
C04 trees -- the model used to be told a check had passed that never ran, with
nothing in the stored row saying so.

``_MESH_FILTER`` now comes from ``oncotriage.agent.deps``; File 35 stubs it.
``apply_quality_gate`` is imported from ``retrieval`` rather than duplicated,
which is the one edge this module has into another stage.

ITEM 11a CHANGED TWO THINGS HERE, one of them a behaviour change:

  * ``extract_patient_histology`` is called UNCONDITIONALLY. It used to sit
    inside ``if mesh_filter is not None:``, so a missing MeSH lookup file
    disabled the histology filter as well as the cancer site filter — two
    unrelated checks wired to one file's presence. On the degraded path
    (no MeSH filter) histology mismatches are now dropped instead of reaching
    Stage 5. On the normal path nothing changes.
  * an unparseable trial ``min_age`` / ``max_age`` is COUNTED, in the
    module-level ``AGE_PARSE_FAILURES``, and reported in the Stage 4 line. The
    recovery is unchanged — the trial is kept and the age check is skipped for
    it — because the failing value comes from ClinicalTrials.gov and there is
    no operator action that would fix it. See the counter's own note.

TWO LATER FIXES, BOTH OF WHICH CHANGE WHICH TRIALS SURVIVE:

  * ``_parse_age_bound`` CONVERTS THE UNIT. It used to take the digits and
    discard the unit, so "240 Months" -- twenty years -- was read as two hundred
    and forty years and stopped excluding anyone, and a min_age of "6 Months"
    was read as six years. The result is fractional years and must stay
    fractional. An unrecognised unit is recorded and the bound is unusable; the
    recovery is unchanged.
  * AN UNKNOWN PATIENT SEX NO LONGER EXCLUDES EVERY SEX-SPECIFIC TRIAL, and a
    sex arriving as None no longer raises. Trials kept for that reason are
    counted in ``SEX_UNKNOWN_KEPT``, apart from ``sex_dropped``, because a
    mismatch and a missing field are different findings.

Neither adds a key to the returned dict.
"""

import re
import time
from collections import Counter

from oncotriage.agent import deps
from oncotriage.agent.retrieval import apply_quality_gate
from oncotriage.agent.state import (
    AGE_FILTER_SKIP_NO_PATIENT_AGE,
    FILTER_APPLIED,
    FILTER_SKIP_ABLATED,
    HISTOLOGY_FILTER_SKIP_NO_PATIENT_HISTOLOGY,
    MESH_FILTER_APPLIED,
    MESH_FILTER_SKIP_ABLATED,
    MESH_FILTER_SKIP_NO_FILTER,
    MESH_FILTER_SKIP_NO_TREES,
    SEX_FILTER_SKIP_NOT_COMPARABLE,
    STAGE_FILTER_SKIP_NO_PATIENT_STAGE,
    TrialMatchState,
)
from oncotriage.config import MAX_TRIALS_FOR_EVALUATION, MEDCPT_SCORE_FLOOR
from oncotriage.extraction.histology import (
    extract_patient_histology,
    is_histology_mismatch,
)
from oncotriage.extraction.stage import extract_patient_stage, is_stage_mismatch
from oncotriage.observability import get_logger


log = get_logger(__name__)


#------------------------------------------------------------------------------


# ===========================================================================
# AGE-PARSE DEGRADATION RECORD (item 11a)
# ===========================================================================
#
# `Exception and Fallback Audit.md` ranked the handler below Open, HIGHEST
# PRIORITY, and recorded that item 11b did not change it: a trial whose
# min_age / max_age will not parse is KEPT, so the age filter silently does not
# run for that trial and it can reach GPT-4o for a patient outside its range.
# The direction is safe at pre-screening — false-eligible, never
# false-ineligible — but the RATE was unknown, and unknown is the defect.
#
# THIS COUNTS RATHER THAN RAISES, and that is a deliberate departure from the
# two layers above. A missing MeSH file or a missing pip package is a
# CONFIGURATION defect: one operator, one command, and every run afterwards is
# correct, so raising costs one run and fixes the class. An unparseable age
# bound is third-party DATA — whatever ClinicalTrials.gov happened to register
# for one trial — and raising on it would abort a whole patient's pipeline
# because one of 75 retrieved trials has a strange string in one field. There
# is no command the operator can run to fix ClinicalTrials.gov. Converting a
# per-trial degradation into a per-patient outage is not a safety improvement,
# so the fix is the counter the audit asked for, on the same footing as
# mesh_dropped, plus the Stage 4 line saying it happened.
#
# MODULE-LEVEL, following PARTIAL_DATE_DEGRADATIONS in oncotriage/utils.py, and
# NOT a new key in the returned dict: the twelve characterization fixtures diff
# the pipeline's output field by field, and a new field means recapturing all
# twelve — twelve live GPT-4o runs — to record something no stage reads.
#
# Keyed by which bound failed and on what text, capped in length, so a run can
# answer "how often, and on what" rather than only "how often". The NCT id is
# deliberately NOT in the key: 75 trials per patient across 22k patients would
# make this Counter unbounded, and the failing SHAPE is what a fix needs.
AGE_PARSE_FAILURES = Counter()

# Longest raw age string kept in a counter key. Long enough to see the shape of
# a real value ("6 Months", "N/A", "18 Years and older"), short enough that a
# pathological field cannot grow the key without bound.
_AGE_KEY_MAX_LEN = 40


# ===========================================================================
# AGE UNITS (the unit fix)
# ===========================================================================
#
# ClinicalTrials.gov registers an age bound as "<number> <Unit>", and the unit
# is NOT always Years. The parser below used to extract the digits and throw
# the unit away, so "240 Months" — twenty years — was read as two hundred and
# forty years and that trial's upper bound stopped excluding anyone, while a
# min_age of "6 Months" was read as six years and excluded every infant the
# trial was written for. Nothing was recorded, because digits WERE found.
#
# Conversions are calendar-average and exact enough for a boundary comparison
# against a whole-number patient age: a month is a twelfth of a year, and a
# year is 365.25 days so that the day and week factors agree with the month one
# to within a day. Hours and minutes are in the table because CT.gov registers
# them too (neonatal trials) and converting them involves no guess; leaving
# them out would send a "23 Hours" bound down the unrecognised path and quietly
# disable that trial's age check.
_AGE_UNIT_YEARS = {
    "year":   1.0,
    "month":  1.0 / 12.0,
    "week":   7.0 / 365.25,
    "day":    1.0 / 365.25,
    "hour":   1.0 / (365.25 * 24.0),
    "minute": 1.0 / (365.25 * 24.0 * 60.0),
}

# The first number in the string and the alphabetic token immediately after it,
# AS ONE MATCH so the two cannot come from different places. Two independent
# searches would pair them wrongly on a bound like "5, 240 Months", where the
# first number is 5 and the only unit belongs to 240 — read separately that is
# "five months", read together it is "5" with nothing adjacent. Group 2 is "" on
# a bare number.
#
# `findall(...)[0]` rather than `search` keeps the digit-less case failing as an
# IndexError, so it still records under the key it has always recorded under.
_AGE_BOUND_RE = re.compile(r'(\d+(?:\.\d+)?)\s*([A-Za-z]+)?')

# Any recognised unit word ANYWHERE in the string, used only when the token
# immediately after the number is not a unit at all.
#
# WHY THIS EXISTS: "18 to 65 Years" puts the word "to" where the unit goes.
# Without this, the bound would be unusable and the age check would not run for
# that trial — which is a REGRESSION against the pre-fix code, which read the
# digits and filtered at 18. It measures ZERO on the 14,324-trial corpus (every
# bound there is "<number> <Unit>") and is here so that a shape which does occur
# elsewhere cannot silently turn a working filter off.
#
# It is not a guess. The fallback fires only when EXACTLY ONE distinct unit word
# appears in the string, so "6 Months to 2 Years" — where pairing the leading
# number with either unit would be an invention — stays unusable and recorded.
_AGE_ANY_UNIT_RE = re.compile(
    r'\b(year|month|week|day|hour|minute)s?\b', re.IGNORECASE)


def _age_key_text(raw) -> str:
    """The raw bound, capped, for use in a counter key. Never raises."""
    text = str(raw)
    return text if len(text) <= _AGE_KEY_MAX_LEN else text[:_AGE_KEY_MAX_LEN] + "..."

# A bound that carried a number and NO unit at all. Not a failure — it is used,
# as years, which is exactly what the pre-fix code did with every bound — but it
# is an ASSUMPTION, and the project's rule is that a fallback path records which
# path it took. Separate from AGE_PARSE_FAILURES on purpose: everything in that
# counter is a bound the age check did NOT run on, and folding a bound that WAS
# applied into it would make `age_unparsed` uninterpretable.
AGE_UNIT_ASSUMPTIONS = Counter()


# ===========================================================================
# UNKNOWN PATIENT SEX (the sex fix)
# ===========================================================================
#
# WHAT AN UNPARSED SEX ACTUALLY HOLDS, read from the parser rather than assumed:
# `oncotriage/fhir/parser.py:_parse_demographics` sets
# `sex = patient_resource.get('gender', 'unknown')`. So an ABSENT `gender`
# element gives the string "unknown"; a `gender` present and JSON-null gives
# **None**, because `.get`'s default does not apply to a key that exists; and a
# `gender` present and empty gives "". FHIR itself also admits "other" as a
# registered value, which is a known sex the trial vocabulary (ALL / MALE /
# FEMALE) cannot express. None therefore CAN reach this filter, and the
# unguarded `.upper()` it used to meet raised AttributeError rather than
# dropping — a crash, not a drop.
#
# So there is no sentinel to key on, and this module does not invent one. The
# question the filter can actually answer is whether the patient's sex is one
# the trial's own vocabulary can be compared against, which is MALE or FEMALE
# and nothing else. Everything else — None, "", "unknown", "other", any junk —
# is not a mismatch, it is an absence of evidence.
#
# THE FAILURE DIRECTION IS ASYMMETRIC. Keeping a sex-specific trial for a
# patient whose sex never parsed costs one judged trial, and Stage 5 reads the
# criteria and can still reject it. Dropping it removes an eligible trial
# permanently and invisibly, and the funnel would report it as a sex mismatch —
# a clinical finding — when it is a missing field.
_COMPARABLE_PATIENT_SEXES = frozenset({"male", "female"})

# Trials kept because the patient's sex was not comparable, keyed by the raw
# value that reached the filter. Module-level, following AGE_PARSE_FAILURES
# above and NOT a new key in the returned dict, for the reason argued there:
# the twelve characterization fixtures diff that dict field by field.
#
# Separate from `sex_dropped` because one number cannot tell a real mismatch
# from a missing field. `sex_dropped` is a statement about the TRIALS — their
# requirement and this patient's sex disagree. This is a statement about the
# CORPUS — a patient record arrived without a usable sex — and the two have
# different owners and different fixes.
SEX_UNKNOWN_KEPT = Counter()


def _record_age_parse_failure(bound: str, raw, exc, unit=None) -> None:
    """Record one unusable trial age bound. Never raises.

    `bound` is "min_age" or "max_age". `exc` is the exception, or a string
    naming the failure kind for a failure that is not an exception (the
    unrecognised-unit case, which is a decision rather than a raise). The KIND
    is in the key because IndexError (the regex found no digits), ValueError
    (digits that float() refused) and UnknownAgeUnit (digits and a unit this
    module will not convert) are three different data problems with three
    different fixes.

    `unit` adds the offending unit as its own segment, so a counter dump answers
    "which unit do we not handle" without anyone having to re-parse the text.
    Omitted for the exception cases, whose keys are therefore unchanged.
    """
    text = _age_key_text(raw)
    kind = exc if isinstance(exc, str) else type(exc).__name__
    key = (f"{bound}:{kind}:{text}" if unit is None
           else f"{bound}:{kind}:{unit}:{text}")
    AGE_PARSE_FAILURES[key] += 1


def _parse_age_bound(raw, default, bound: str):
    """Parse one trial age bound TO YEARS. Returns a number, or None.

    THE RECOVERY IS UNCHANGED AND THAT IS THE POINT. None propagates to the
    caller, which then skips the age check for that trial and keeps it —
    byte-for-byte the outcome of the old `except (IndexError, ValueError): pass`,
    including the case where max_age is unparseable and min_age is fine: the
    old `try` wrapped both parses AND the comparison, so one bad bound meant the
    whole check was skipped rather than the good bound being applied alone.
    Applying the good bound would be defensible and it would DROP trials the old
    code kept, which is a live behaviour change dressed up as instrumentation.
    Item 11a adds the record; changing which trials survive is a different
    decision and belongs to whoever reads the counts this now produces.

    Per-bound attribution is item 11a's: the old handler could not say which of
    the two strings was the bad one, and the counter is only actionable if it can.

    THE RETURN IS FRACTIONAL YEARS AND MUST STAY FRACTIONAL. Six months is 0.5,
    not 0 and not 1. Rounding would move the boundary — and it would move it in
    the direction this fix exists to correct, since a min_age rounded down to 0
    stops excluding anybody and a max_age rounded up does the same. The only
    consumer is the numeric comparison `min_age <= patient_age <= max_age`,
    where a float works unchanged.

    An unrecognised unit is NOT guessed at. It returns None like any other
    unusable bound — same recovery, trial kept, age check skipped — and records
    under its own key naming the unit, so the fix for it is one row in
    _AGE_UNIT_YEARS rather than an archaeology session.
    """
    if not raw:
        return default

    text = str(raw)

    try:
        number_text, raw_unit = _AGE_BOUND_RE.findall(text)[0]
        number = float(number_text)
    except (IndexError, ValueError) as exc:
        _record_age_parse_failure(bound, raw, exc)
        return None

    if not raw_unit:
        # A bare number. Years is what the pre-fix code assumed for every bound,
        # so assuming it here preserves that behaviour rather than turning a
        # bound that used to filter into one that does not; it is recorded
        # because an assumption nobody can count is the defect, not the value.
        AGE_UNIT_ASSUMPTIONS[f"{bound}:no_unit:{_age_key_text(raw)}"] += 1
        return number

    raw_unit = raw_unit.lower()
    unit = raw_unit[:-1] if raw_unit.endswith("s") else raw_unit
    factor = _AGE_UNIT_YEARS.get(unit)

    if factor is None:
        # The token after the number is not a unit ("18 to 65 Years"). Recover
        # ONLY when the string names exactly one unit, so nothing is paired with
        # a number it does not belong to; anything else stays unusable.
        elsewhere = {m.group(1).lower() for m in _AGE_ANY_UNIT_RE.finditer(text)}
        if len(elsewhere) == 1:
            unit = elsewhere.pop()
            factor = _AGE_UNIT_YEARS[unit]
            AGE_UNIT_ASSUMPTIONS[
                f"{bound}:unit_not_adjacent:{unit}:{_age_key_text(raw)}"] += 1
        else:
            _record_age_parse_failure(bound, raw, "UnknownAgeUnit",
                                      unit=raw_unit)
            return None

    return number * factor


def node_rule_based_filter(state: TrialMatchState) -> dict:
    """
    Stage 4: Rule-based filtering to remove obvious mismatches.

    Fast heuristic checks before expensive GPT-4o evaluation:
        - Cancer site: patient cancer type must match trial cancer type (MeSH)  # NEW
        - Age: patient age must fall within trial's min/max age
        - Sex: patient sex must match trial's sex requirement
        - Quality gate, two independent knobs, both must pass: the UNBOOSTED
          rerank score must reach QUALITY_THRESHOLD_PERCENTILE of the surviving
          pool (computed on rerank_score_raw, so the gate measures trial
          quality and not MeSH boost membership), AND medcpt_score_max must
          reach MEDCPT_SCORE_FLOOR. A trial with no MedCPT score is not
          dropped by the second. Each knob reports its own drop count.
        - Cost cap: limit to MAX_TRIALS_FOR_EVALUATION candidates
    """
    start = time.time()

    patient_data = state["patient_data"]
    trials = state["reranked_trials"]

    demographics = patient_data["demographics"]
    conditions = patient_data["conditions"]

    patient_age = demographics.get("age")

    # `.get("sex", "unknown").lower()` raised AttributeError when `gender` was
    # present and null, because a default does not apply to a key that exists.
    # Normalised here once, and whether it is USABLE is a separate question from
    # what it says -- see _COMPARABLE_PATIENT_SEXES.
    _raw_patient_sex = demographics.get("sex")
    patient_sex = ("unknown" if _raw_patient_sex is None
                   else str(_raw_patient_sex).strip().lower())
    patient_sex_comparable = patient_sex in _COMPARABLE_PATIENT_SEXES

    # --- Ablation flags (read once, not per-trial) ---
    _ablation = state.get("ablation_flags") or {}
    _skip_mesh      = _ablation.get("skip_mesh_filter", False)
    _skip_stage     = _ablation.get("skip_stage_filter", False)
    _skip_histology = _ablation.get("skip_histology_filter", False)

    # Resolved through the dependency seam, ONCE per call. File 13 read these
    # as module globals bound at exec time, which is what Files 35, 36, 45 and
    # 46 rebound to redirect the pipeline; a module function cannot see a
    # caller's globals, so the seam is what keeps those redirects working.
    # Once per call rather than per use so one invocation cannot see two
    # different objects if an override is installed mid-flight.
    mesh_filter = deps.get_mesh_filter()

    # --- Patient histology, computed UNCONDITIONALLY (item 11a) ---
    #
    # It used to be computed INSIDE the `if mesh_filter is not None:` block
    # below, so a missing MeSH lookup file disabled the HISTOLOGY filter too —
    # a filter that reads no MeSH data, resolves no tree numbers and has nothing
    # to do with cancer site relevance. Two unrelated capabilities were wired to
    # one file's presence, and nothing said so: `histology_dropped` came back 0,
    # which is also what "checked, nothing to drop" looks like.
    #
    # BEHAVIOUR CHANGE ON THE DEGRADED PATH, and it is the intended one: with no
    # MeSH filter loaded, trials whose histology contradicts the patient's are
    # now dropped instead of being passed to GPT-4o. On the normal path
    # (mesh_filter present) nothing changes at all — this is the same call with
    # the same argument, one indent level out — which is why the twelve
    # characterization fixtures, all captured with a filter loaded, replay
    # unchanged.
    patient_histology = extract_patient_histology(conditions)

    # --- Get patient's MeSH cancer site tree numbers ---
    mesh_dropped = 0
    histology_dropped = 0
    patient_trees = set()
    if mesh_filter is not None:

        patient_trees   = state.get("patient_trees") or set()

        # Under the ablation Stage 3 never resolves the trees, so an empty set
        # here means "ablated", not "unmappable" — the ablation line below
        # says which, so do not also claim the trees were unresolvable.
        if not _skip_mesh:
            if patient_trees:
                # THE COUNT, NOT THE TREES. A MeSH C04 tree number names the
                # patient's cancer site -- "C04.588.180" is breast. Printed to
                # a terminal that was transient; in a structured record keyed by
                # a correlation ID it is a durable statement of this patient's
                # diagnosis, which is exactly what LOGGABLE_FIELDS exists to
                # keep out. The operationally useful fact is how many resolved.
                log.info("MeSH patient trees resolved", stage=4,
                         filter="mesh_site", trees_count=len(patient_trees))
            else:
                # Say which outcome this is. "pan_cancer_only" is a resolution
                # that was deliberately rejected, not a lookup that missed.
                log.info("no patient cancer trees resolved; cancer site filter "
                         "skipped", stage=4, filter="mesh_site", trees_count=0,
                         mesh_resolution=state.get("mesh_resolution")
                                         or "unrecorded")

    # --- Did the cancer site filter actually run? ---
    #
    # The per-trial condition below is loop-invariant, so it is decided once
    # here and recorded. Stage 5's system prompt asserts to the model that
    # disease relevance "has already been confirmed"; that sentence is only
    # true when this is MESH_FILTER_APPLIED. In the other three cases the model
    # was told a check passed that never ran, and no stored record said so.
    if _skip_mesh:
        mesh_filter_skip_reason = MESH_FILTER_SKIP_ABLATED
    elif mesh_filter is None:
        mesh_filter_skip_reason = MESH_FILTER_SKIP_NO_FILTER
    elif not patient_trees:
        # Covers both "unmapped" and "pan_cancer_only": state["mesh_resolution"]
        # carries which one, this carries the consequence.
        mesh_filter_skip_reason = MESH_FILTER_SKIP_NO_TREES
    else:
        mesh_filter_skip_reason = MESH_FILTER_APPLIED

    mesh_filter_applied = mesh_filter_skip_reason == MESH_FILTER_APPLIED

    # --- Extract patient cancer stage ---
    #
    # The metastasis list is passed as well as the stage-group list, and it had
    # to be added explicitly: this call site is the ONLY thing that decides
    # whether the extractor's AJCC M tier ever runs in the pipeline. The
    # observations were routed into their own field by File 07 and reached
    # nothing but the patient hash and the Stage 5 prompt, so a patient whose
    # record states distant metastasis on LOINC 21907-1 was staged from their
    # diagnosis text or not at all — and "not at all" means the stage filter
    # kept every trial for them, including trials written for early disease.
    patient_stage = extract_patient_stage(
        conditions,
        cancer_stage_observations=patient_data.get('cancer_stage_observations') or [],
        cancer_metastasis_observations=patient_data.get('cancer_metastasis_observations') or [],
    )
    
    stage_dropped = 0

    # --- Did the other four per-trial filters actually run? -----------------
    #
    # Decided HERE, once, for the same reason mesh_filter_skip_reason is decided
    # here: every one of these conditions is loop-invariant, so evaluating them
    # per trial would be four redundant tests and, worse, would leave the answer
    # nowhere a stored row can read it. Each drop counter below already existed;
    # what did not exist was the statement that the filter which owns it ran.
    #
    # ORDER MATTERS INSIDE EACH: the ablation flag is checked FIRST, because a
    # deliberately disabled filter is not a patient whose data was missing, and
    # reporting "no_patient_stage" for an ablation run would be a false fact
    # about the patient. Same precedence mesh_filter_skip_reason uses.
    if _skip_stage:
        stage_filter_skip_reason = FILTER_SKIP_ABLATED
    elif patient_stage is None:
        stage_filter_skip_reason = STAGE_FILTER_SKIP_NO_PATIENT_STAGE
    else:
        stage_filter_skip_reason = FILTER_APPLIED
    stage_filter_applied = stage_filter_skip_reason == FILTER_APPLIED

    if _skip_histology:
        histology_filter_skip_reason = FILTER_SKIP_ABLATED
    elif not patient_histology:
        histology_filter_skip_reason = HISTOLOGY_FILTER_SKIP_NO_PATIENT_HISTOLOGY
    else:
        histology_filter_skip_reason = FILTER_APPLIED
    histology_filter_applied = histology_filter_skip_reason == FILTER_APPLIED

    # Neither of these two has an ablation flag, so neither has an ablated arm.
    # Adding one "for symmetry" would declare a state the pipeline cannot reach,
    # which is the never-read-value shape check 2h reports.
    if patient_age is None:
        age_filter_skip_reason = AGE_FILTER_SKIP_NO_PATIENT_AGE
    else:
        age_filter_skip_reason = FILTER_APPLIED
    age_filter_applied = age_filter_skip_reason == FILTER_APPLIED

    if not patient_sex_comparable:
        sex_filter_skip_reason = SEX_FILTER_SKIP_NOT_COMPARABLE
    else:
        sex_filter_skip_reason = FILTER_APPLIED
    sex_filter_applied = sex_filter_skip_reason == FILTER_APPLIED

    if patient_stage is not None:
        # KNOWN vs UNKNOWN, not the ordinal. A cancer stage is a clinical fact
        # about this patient; whether the filter had one to work with is an
        # operational fact about the run, and it is the one that explains the
        # funnel. The ordinal is still in `inferences`, which is a clinical
        # store with access control; the log is not.
        log.info("patient cancer stage extracted", stage=4,
                 filter="cancer_stage", status="known")
    else:
        log.info("patient cancer stage unknown; stage filter skipped", stage=4,
                 filter="cancer_stage", status="unknown")
    
    if _skip_mesh:
        log.info("MeSH cancer site filter skipped by ablation flag "
                 "(the Stage 3 relevance boost was skipped too)",
                 stage=4, filter="mesh_site", ablation_flag="skip_mesh_filter")
    if _skip_stage:
        log.info("cancer stage filter skipped by ablation flag", stage=4,
                 filter="cancer_stage", ablation_flag="skip_stage_filter")
    if _skip_histology:
        log.info("histology mismatch filter skipped by ablation flag", stage=4,
                 filter="histology", ablation_flag="skip_histology_filter")

    filtered = []

    # The age and sex cuts below used to be bare `continue`s. Every other drop
    # in this loop already had a counter, so the two that did not were the only
    # ones a stored funnel could not account for.
    age_dropped = 0
    sex_dropped = 0

    # Trials whose age bounds would not parse, so the age check did not run for
    # them. A LOCAL, reported in the Stage 4 line below; the durable record is
    # the module-level AGE_PARSE_FAILURES counter, which also carries the text
    # that failed. It is not returned, for the fixture reason argued there.
    age_unparsed = 0

    # Sex-specific trials KEPT because the patient's sex was not comparable.
    # A LOCAL, reported below when non-zero; the durable record is the
    # module-level SEX_UNKNOWN_KEPT, which also carries the value that arrived.
    # Not returned, for the fixture reason argued at that counter.
    sex_unknown_kept = 0

    for trial_obj in trials:
        trial = trial_obj["trial"]
        eligibility = trial["eligibility"]

        # --- Cancer site filter ---
        if mesh_filter_applied:
            if not mesh_filter.is_cancer_relevant(patient_trees, trial):
                mesh_dropped += 1
                continue

        # --- Cancer stage filter ---
        #
        # THE MARKER IS THE CONDITION, not a second declaration beside it. This
        # read `if not _skip_stage: if patient_stage is not None:` and the marker
        # computed above repeated both tests; two copies of one predicate is how
        # a column comes to say a filter ran on a run where it did not. Same for
        # the three below. Behaviour is byte-for-byte what it was: each
        # *_applied is exactly the conjunction it replaces.
        if stage_filter_applied:
            if is_stage_mismatch(patient_stage, trial):
                stage_dropped += 1
                continue

        # --- Histology filter ---
        if histology_filter_applied:
            if is_histology_mismatch(patient_histology, trial):
                histology_dropped += 1
                continue

        # --- Age filter ---
        min_age_str = eligibility.get("min_age", "0 Years")
        max_age_str = eligibility.get("max_age", "999 Years")

        min_age = _parse_age_bound(min_age_str, 0, "min_age")
        max_age = _parse_age_bound(max_age_str, 999, "max_age")

        if min_age is None or max_age is None:
            # Unparseable bound: keep the trial and skip the age check, which is
            # what the bare `except ... : pass` did. It is COUNTED now, in
            # AGE_PARSE_FAILURES, so a run can say how often the age filter did
            # not run and on what text. Counted per trial rather than tracked in
            # a local, because the recovery must not become a new field in the
            # returned dict — see the note above the counter.
            age_unparsed += 1
        elif age_filter_applied and not (min_age <= patient_age <= max_age):
            age_dropped += 1
            continue

        # --- Sex filter ---
        #
        # `or "ALL"` rather than a `.get` default: the indexer writes whatever
        # ClinicalTrials.gov registered, so the key can be present and null or
        # empty, where a default does not apply and `.upper()` raised.
        trial_sex = str(eligibility.get("sex") or "ALL").upper()
        if trial_sex != "ALL":
            if sex_filter_applied:
                if trial_sex != patient_sex.upper():
                    sex_dropped += 1
                    continue
            else:
                # NOT a drop and not a mismatch: the patient's sex never parsed,
                # so this trial's requirement was never tested. Counted apart
                # from sex_dropped because the two are different findings.
                sex_unknown_kept += 1

        filtered.append(trial_obj)

    # Sort by rerank_score (highest first) — this IS the boosted score, since
    # ranking order is what the MeSH boost exists to influence.
    filtered.sort(
         key=lambda x: (x.get("rerank_score", 0), x["trial"]["nct_id"]),
         reverse=True
     )

    # Two independent quality knobs: a percentile of the UNBOOSTED fused score
    # within this pool, and an absolute floor on the trial's best MedCPT
    # cross-encoder score. A trial must pass both. quality_dropped stays the
    # total so no existing reader changes meaning; the per-knob counts are
    # reported beside it because the two overlap and their sum is not the total.
    quality_filtered, dynamic_threshold, quality_drops = apply_quality_gate(filtered)
    quality_dropped = len(filtered) - len(quality_filtered)

    candidates_after_quality = len(quality_filtered)

    # Cost cap: limit candidates sent to GPT-4o
    if len(quality_filtered) > MAX_TRIALS_FOR_EVALUATION:
        quality_filtered = quality_filtered[:MAX_TRIALS_FOR_EVALUATION]

    elapsed = time.time() - start
    
    if sex_unknown_kept:
        # Durable, keyed by what actually arrived, so a corpus-quality question
        # ("how many patients, and carrying what") is answerable across a run.
        # Once per call rather than per trial: the loop already has the count.
        SEX_UNKNOWN_KEPT[patient_sex] += sex_unknown_kept
        # `kept` and `status` are already on LOGGABLE_FIELDS, so this needs no
        # widening of the allowlist. The patient's sex VALUE is deliberately not
        # a field -- it is clinical, it goes in the in-process counter only.
        log.warning("patient sex not comparable; sex-specific trials kept "
                    "rather than dropped", stage=4, filter="sex",
                    status="unknown", kept=sex_unknown_kept)

    if not mesh_filter_applied:
        log.warning("cancer site filter did not run; Stage 5 will not assert "
                    "that disease relevance was confirmed", stage=4,
                    filter="mesh_site", skip_reason=mesh_filter_skip_reason)

    # ONE LINE PER FILTER THAT DID NOT RUN, at INFO rather than WARNING. The
    # cancer site filter above is a WARNING because its absence changes what
    # Stage 5 is TOLD; these four change only what was checked, and three of the
    # four skip reasons are ordinary properties of a patient record (no stage
    # recorded, no histology keyword, no usable birth date) rather than faults.
    # A WARNING per patient for "this patient has no cancer stage" would be a
    # warning on a large fraction of any real cohort, which is how a warning
    # channel stops being read.
    #
    # `filter` and `skip_reason` are already on LOGGABLE_FIELDS; no widening.
    for _name, _applied, _reason in (
        ("cancer_stage", stage_filter_applied, stage_filter_skip_reason),
        ("histology", histology_filter_applied, histology_filter_skip_reason),
        ("age", age_filter_applied, age_filter_skip_reason),
        ("sex", sex_filter_applied, sex_filter_skip_reason),
    ):
        if not _applied:
            log.info("Stage 4 filter did not run for this patient", stage=4,
                     filter=_name, status="skipped", skip_reason=_reason)

    log.info("rule-based filter complete", stage=4, duration_s=round(elapsed, 3),
             trials_in=len(trials), trials_out=len(quality_filtered),
             dropped=len(trials) - len(quality_filtered),
             # Every drop reason as its own field rather than folded into a
             # sentence: the whole point of the funnel is that a query can ask
             # "which stage lost the trials" without parsing prose.
             mesh_dropped=mesh_dropped, stage_dropped=stage_dropped,
             histology_dropped=histology_dropped, age_dropped=age_dropped,
             # The age filter DID NOT RUN for these -- distinct from a drop.
             age_unparsed=age_unparsed, sex_dropped=sex_dropped,
             quality_dropped=quality_dropped,
             # The two knobs, apart. They OVERLAP -- a trial can fail both --
             # so these do not sum to quality_dropped, and quality_dropped_floor
             # alone does not say whether the absolute knob did any work the
             # percentile had not already done. quality_dropped_floor_only does.
             quality_dropped_percentile=quality_drops["percentile"],
             quality_dropped_floor=quality_drops["floor"],
             quality_dropped_floor_only=quality_drops["floor_only"],
             medcpt_floor=MEDCPT_SCORE_FLOOR,
             # None when the pool reaching the gate was empty -- no cut was
             # made, so there is no score to report. round(None, 5) raises, so
             # the guard is not decoration.
             threshold=(round(dynamic_threshold, 5)
                        if dynamic_threshold is not None else None))

    return {
        "filtered_trials": quality_filtered,
        "candidates_after_rule_filter": len(filtered),
        "candidates_after_quality_filter": candidates_after_quality,
        "mesh_dropped": mesh_dropped,
        "histology_dropped": histology_dropped,
        "stage_dropped": stage_dropped,
        # The two per-trial drops that had no counter, plus the pool-level cut
        # and the score it was made at. Together with the three above they
        # account for every trial that entered this stage and did not leave it.
        "age_dropped": age_dropped,
        "sex_dropped": sex_dropped,
        "quality_dropped": quality_dropped,
        # THE TWO KNOBS, SEPARATELY. The gate stopped being one number, so one
        # counter can no longer describe it: a run that lost trials to a
        # mis-set absolute floor and a run that lost them to an unusually tight
        # pool are the same quality_dropped and different findings.
        #
        # These ARE new keys in this dict, which the item 11a note above the
        # AGE_PARSE_FAILURES counter forbids for a DEGRADATION counter. The
        # reason given there was that the twelve characterization fixtures diff
        # this dict field by field. Measured rather than inherited:
        # oncotriage/fixtures/capture.py builds its stage4 block by naming keys
        # one at a time, so a key added here is not in the fixture prefix and
        # costs no recapture. What these are is a FILTER's own accounting, not
        # a recovery record, and it belongs where every other drop count is.
        "quality_dropped_percentile": quality_drops["percentile"],
        "quality_dropped_floor": quality_drops["floor"],
        "quality_dropped_floor_only": quality_drops["floor_only"],
        # NULL rather than a forged number when the gate saw an empty pool.
        # float(None) RAISES, so the unguarded float() this replaced would have
        # taken Stage 4 down on any patient whose whole pool was removed by the
        # MeSH / stage / histology / age / sex filters above.
        "quality_threshold": (float(dynamic_threshold)
                              if dynamic_threshold is not None else None),
        # Read by Stage 5 to decide what its system prompt may assert, and
        # logged so a stored inference says whether the check ran.
        "mesh_filter_applied": mesh_filter_applied,
        "mesh_filter_skip_reason": mesh_filter_skip_reason,
        # THE SAME PAIR FOR THE FOUR FILTERS THAT HAD A DROP COUNTER AND NO
        # MARKER. Read nowhere in the pipeline -- unlike mesh_filter_applied,
        # which Stage 5 consults -- and logged for exactly the reason
        # mesh_resolution is: `stage_dropped = 0` is ambiguous on its own and
        # this is what separates "checked, nothing to drop" from "never ran".
        #
        # These ARE new keys in this dict. The rule at the AGE_PARSE_FAILURES
        # counter forbids that for a DEGRADATION counter, and these are not
        # one: they are a FILTER's own accounting, the same argument the four
        # quality_dropped_* keys above are admitted on. Re-measured rather than
        # inherited: oncotriage/fixtures/capture.py's stage4 block names its
        # keys one at a time, so a key added here is not in any fixture's
        # deterministic prefix and costs no recapture.
        "stage_filter_applied": stage_filter_applied,
        "stage_filter_skip_reason": stage_filter_skip_reason,
        "histology_filter_applied": histology_filter_applied,
        "histology_filter_skip_reason": histology_filter_skip_reason,
        "age_filter_applied": age_filter_applied,
        "age_filter_skip_reason": age_filter_skip_reason,
        "sex_filter_applied": sex_filter_applied,
        "sex_filter_skip_reason": sex_filter_skip_reason,
        "stage_timings": {**state.get("stage_timings", {}), "rule_filter": round(elapsed, 3)}
    }


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""
