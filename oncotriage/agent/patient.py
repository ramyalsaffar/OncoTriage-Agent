"""Everything the pipeline derives from one patient dict.

Item 20c, pass 2c. Four slices of "13- LangGraph Agent.py", verbatim except for
the registry accessors noted below:

    526-756    extract_genomic_variant_terms, compute_patient_hash
    4232-4606  the condition / medication relevance classifier and the lab unit
               normaliser -- three layers (ICD-10 blocks, SNOMED codes,
               blacklist keywords) plus the unit table
    4609-5059  _create_patient_summary, the Stage 5 prompt body

They are one module because they are one subject: turning a parsed FHIR bundle
into the two things the rest of the agent consumes -- the text Stage 5 reads,
and the hash that says two runs had the same input.

``compute_patient_hash`` is load-bearing beyond its size. It keys the claim that
two runs saw identical input, so it hashes the ECOG value, date, count and
selection path but deliberately NOT ``value_shape``: normalising a corpus from
valueQuantity to valueInteger must not change a hash when the prompt text is
identical. It emits nothing at all when no ECOG was present, so hashes already
logged against an ECOG-free corpus stay comparable.

THE REGISTRY ACCESSORS. ``_create_patient_summary`` read ``_CANCER_REGISTRY``,
``_LAB_REGISTRY`` and ``_MESH_FILTER`` as module globals that File 13 built at
exec time. All three now come from ``oncotriage.agent.deps``, so a test harness
can stub any of them -- File 35 stubs two -- and so importing this module builds
none of them.
"""

import hashlib
import re
from collections import Counter
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from oncotriage.agent import deps
from oncotriage.agent.state import GENOMIC_VARIANT_LOINC, _VARIANT_TEXT_PATTERN
from oncotriage.config import MAX_VARIANT_TERMS
from oncotriage.extraction.stage import (
    STAGE_NUMERALS,
    STAGE_SOURCE_CONDITION_DISPLAY,
    STAGE_SOURCE_M_CATEGORY,
    STAGE_SOURCE_METASTATIC_KEYWORD,
    STAGE_SOURCE_STAGE_GROUP,
    STAGE_SOURCES,
    extract_patient_stage_with_source,
)
from oncotriage.utils import deduplicate_by_display


#------------------------------------------------------------------------------


# MAX_VARIANT_TERMS is in 03- Config.py with the other tunables.


def extract_genomic_variant_terms(patient_data: Dict) -> Dict:
    """Collect the patient's genomic variant terms for retrieval.

    Detection is STRUCTURAL first and textual only as a fallback, because the
    text was doing all the work and doing it badly in both directions:

      - It matched too much. `"gene" in display.lower()` fired on 45,842
        non-genomic observations across the cohort ("Generalized anxiety
        disorder 7 item (GAD-7)", "General activity scale [PEG]") against 295
        genuine ones, so essentially every expanded_query and every R4 rerank
        query carried a list of GAD-7 scores and bare integers.
      - It matched too little. Not one of the four keywords appears in a mCODE
        variant display — _parse_mcode_genomic_variant (File 07) emits
        "EGFR p.Leu858Arg: Present | Somatic" — and those observations are not
        in patient_data["observations"] at all, having been routed into
        patient_data["cancer_genomic_variants"]. The structured, highest
        fidelity variant record was the one source this could never see.

    Three paths, in descending fidelity. Each is counted and returned, so a
    query built from the free-text path alone is a queryable fact rather than
    an inference:

      mcode        patient_data["cancer_genomic_variants"], every entry of
                   which carries LOINC 69548-6 by construction.
      structured   an entry in ["observations"] whose code is
                   GENOMIC_VARIANT_LOINC or which carries a non-empty
                   gene_symbol. Covers a caller that pools variants into the
                   observation list rather than using File 07's routing.
      free_text    an observation whose display matches _VARIANT_TEXT_PATTERN
                   on a word boundary.

    Returns:
        {"terms": [str, ...],           # de-duplicated, order-stable, capped
         "counts": {"mcode": int, "structured": int, "free_text": int},
         "truncated": int}              # terms dropped by MAX_VARIANT_TERMS
    """
    counts = {"mcode": 0, "structured": 0, "free_text": 0}
    terms = []
    seen = set()

    def _add(term: str, path: str) -> None:
        term = (term or "").strip()
        if not term:
            return
        counts[path] += 1
        key = term.lower()
        if key in seen:
            return
        seen.add(key)
        terms.append(term)

    def _structured_term(record: Dict) -> str:
        """Gene symbol plus HGVS notation, or the display when neither exists.

        Preferred over the display because the display carries result text
        ("Present | Somatic") that is noise in a retrieval query, while the
        gene symbol and the protein-level change are the tokens trials are
        indexed by.
        """
        gene = (record.get("gene_symbol") or "").strip()
        if not gene:
            return (record.get("display") or "").strip()
        hgvs = (record.get("hgvs_protein") or record.get("hgvs_cdna") or "").strip()
        return f"{gene} {hgvs}".strip()

    # --- Path 1: mCODE variants, already separated by File 07 ---------------
    for record in (patient_data.get("cancer_genomic_variants") or []):
        _add(_structured_term(record), "mcode")

    # --- Paths 2 and 3: the general observation pool ------------------------
    for obs in (patient_data.get("observations") or []):
        display = (obs.get("display") or "").strip()

        if obs.get("code") == GENOMIC_VARIANT_LOINC or (obs.get("gene_symbol") or "").strip():
            _add(_structured_term(obs), "structured")
            continue

        if not _VARIANT_TEXT_PATTERN.search(display.lower()):
            continue

        # Free-text shapes differ, and the old rule — prefer value, else
        # display — was right for one of them and wrong for the other:
        #
        #   "Genetic variant: BRAF (V600E)" / value "BRAF (V600E)"
        #       the display is a label and the VALUE carries the finding.
        #   "ERBB2 gene duplication [Presence] in ... by FISH" / value "Positive"
        #       the display carries the gene and the value is a result word.
        #       Preferring the value put the literal string "Positive" into the
        #       retrieval query and left ERBB2 out of it — the one genuine
        #       match in this corpus contributed nothing usable.
        #
        # A colon in the display is what separates them: it marks the label
        # form. Without one, the display is the finding.
        value = obs.get("value")
        value_str = str(value).strip() if value is not None else ""
        if ":" in display and value_str:
            cleaned = display.split(":", 1)[-1].strip()
            _add(value_str or cleaned, "free_text")
        else:
            _add(display, "free_text")

    truncated = max(0, len(terms) - MAX_VARIANT_TERMS)
    return {"terms": terms[:MAX_VARIANT_TERMS], "counts": counts,
            "truncated": truncated}


def compute_patient_hash(patient_data: Dict) -> str:
    """Compute a deterministic hash of patient data for reproducibility tracking.

    WHAT EQUAL HASHES ACTUALLY GUARANTEE, stated precisely because the previous
    wording was false. Two inferences with the same hash had the same patient
    input AS THE PIPELINE READS IT: the same Stage 5 prompt text and the same
    inputs to every Stage 4 filter. So a score or eligibility difference between
    them is attributable to model non-determinism rather than to the patient.

    That is deliberately NOT "identical input data", and the difference is not
    pedantry -- it is the rule that decides what goes in. Sub-fields the parser
    carries but no consumer reads are excluded, because including them would
    make the hash move on a re-encoding that changes no prompt and no filter,
    and the ablation study reads a hash change as an input change. The ECOG
    entry has always worked this way (see ``value_shape`` below); every field
    added since follows it, and each one names its readers.

    Hash inputs, IN EMISSION ORDER. Every collection is sorted by its emitted
    line before it is appended, so no entry depends on parse order:
      - demographics: birth_date, sex, race, ethnicity (a fixed sequence, not a
        collection, so it is the one block that is not sorted)
      - conditions: display, onset_date, clinical_status
      - medications: display only, de-duplicated
      - observations: display, value, unit, date
      - procedures: display, date
      - ecog_performance_status: value, date, observations_found, selection
      - cancer_metastasis_observations: display, value, unit, date,
        metastasis_category
      - allergies: display, category, criticality
      - cancer_genomic_variants: display, gene_symbol, hgvs_protein, hgvs_cdna,
        result_value, interpretation, date
      - cancer_stage_observations: stage_display, date, loinc

    Each name above is the patient_data KEY, not a nickname for it. The ECOG
    line used to read "ecog", which is not a field of anything --
    tests/test_agent_patient_hash_coverage.py section 6 derives the read keys
    from this function's own body and compares them against this list, so a
    name here that no longer exists, or a field read but not listed, fails.

    THE LAST FIVE ARE EMITTED ONLY WHEN PRESENT. Each is a field File 07 routes
    OUT of ``observations`` into a list of its own, so each contributes nothing
    to the five entries above it and each had to be added separately. Emitting
    nothing when the list is empty is what keeps a hash comparable across the
    addition: a patient who never carried that data hashes exactly as they did
    before the entry existed. An unconditional line would have invalidated every
    stored hash to record "still nothing".
    """

    demographics = patient_data.get("demographics", {})
    conditions = patient_data.get("conditions", [])
    medications = patient_data.get("medications", [])
    observations = patient_data.get("observations", [])
    procedures = patient_data.get("procedures", [])
    ecog = patient_data.get("ecog_performance_status") or {}
    
    # Build deterministic string representation
    parts = []

    # ---------------------------------------------------------------------
    # EVERY COLLECTION IS SORTED BY ITS EMITTED LINE, NOT BY A SUBSET OF ITS
    # FIELDS. This is a correctness fix, and the defect it removes was
    # measured rather than suspected.
    #
    # WHAT WAS WRONG. Each collection was sorted by a KEY -- observations by
    # (display, date), conditions by (display, onset_date) -- and then emitted
    # with MORE fields than the key covered. Python's sort is stable, so two
    # records sharing a key kept their PARSE order, and the line they produced
    # differed in a field the key never looked at. The hash therefore depended
    # on the order the FHIR `entry` array happened to arrive in.
    #
    # MEASURED: parsing one real bundle six times with its `entry` array
    # shuffled produced two different hashes, and the pre-change function did
    # the same on the same three shuffles -- so this is not new, and it is not
    # theoretical. The culprit on that patient was `observations`: 3,660
    # records, one tied (display, date) pair whose `value` differed. Across the
    # corpus, all 1,000 patients have at least one such tie.
    #
    # WHY IT MATTERS DESPITE THE FILE ON DISK BEING FIXED. Nothing re-orders a
    # bundle that is never rewritten -- but File 04's mCODE normalizer rewrites
    # every exported bundle, a Synthea regeneration produces new serialisations,
    # and the dashboard's reproducibility tab GROUPS BY patient_data_hash. A
    # hash that moves when the same clinical record is re-serialised splits one
    # patient into two groups and reports it as an input change.
    #
    # WHY SORTING THE LINE RATHER THAN WIDENING THE KEY. They are equivalent
    # today; they do not stay equivalent. A key listing the same fields as the
    # f-string is two lists to keep in step, and the failure mode when they
    # drift is silent and order-dependent -- exactly what is being fixed. The
    # emitted line IS the content, so sorting it cannot go stale when a field
    # is added to the string.
    #
    # THE COST IS STATED: this moves the hash of every patient in the corpus,
    # including patients carrying none of the fields added alongside it. It is
    # separable from that addition and was measured separately.
    def _emit(prefix, lines):
        """Append `lines` under `prefix`, in canonical (sorted) order."""
        parts.extend(f"{prefix}={line}" for line in sorted(lines))


    # Demographics (fixed order)
    # birth_date instead of age: birth_date is what the FHIR source actually
    # carries, so it is immutable across re-parses. age is derived from it
    # against DATA_SNAPSHOT_DATE (File 03) and is therefore also stable, but it
    # is a derived value — hashing the source keeps the hash independent of how
    # the derivation is configured. The reference date the age was computed
    # against is recorded separately, as age_reference_date.
    parts.append(f"birth_date={demographics.get('birth_date', '')}")
    parts.append(f"sex={demographics.get('sex', '')}")
    parts.append(f"race={demographics.get('race', '')}")
    parts.append(f"ethnicity={demographics.get('ethnicity', '')}")
    
    # Conditions
    _emit("cond", [
        f"{c.get('display', '')}|{c.get('onset_date', '')}|{c.get('clinical_status', '')}"
        for c in conditions
    ])

    # Medications (deduplicated by display, which is all that is emitted)
    _emit("med", set(m.get('display', '') for m in medications))

    # Observations
    _emit("obs", [
        f"{o.get('display', '')}|{o.get('value', '')}|{o.get('unit', '')}|{o.get('date', '')}"
        for o in observations
    ])

    # Procedures
    _emit("proc", [
        f"{p.get('display', '')}|{p.get('date', '')}"
        for p in procedures
    ])

    # ECOG performance status.
    #
    # It has to be in here or the docstring's promise stops holding: File 07
    # routes LOINC 89247-1 OUT of `observations` into its own field, so once
    # patients carry a score the ECOG contributes nothing to the hash above and
    # two patients differing only in performance status -- the single most
    # common gate in interventional oncology -- would hash identically.
    #
    # Emitted only when the bundle actually carried an ECOG observation. That is
    # deliberate, and it is not the same as defaulting absence to a value:
    #
    #   - A bundle with no ECOG produces no line, so its hash is byte-identical
    #     to what this function returned before ECOG existed. Every hash already
    #     logged against the current corpus -- which has no ECOG anywhere --
    #     stays comparable. An unconditional line would have invalidated all of
    #     them to record "still nothing".
    #   - observations_found, not value, is the switch. A patient whose only
    #     ECOG postdates the reference date has value None but found >= 1, and
    #     their input data genuinely differs from a patient with no observation
    #     at all, so they get a line and a different hash. Keying on value would
    #     have collapsed those two into one hash and broken the promise in the
    #     other direction.
    #
    # value_shape is deliberately NOT hashed. It records whether the source
    # bundle carried valueQuantity or valueInteger, which is a fact about
    # serialisation, not about the patient: the same score on the same date
    # produces byte-identical prompt text either way. Hashing it would make
    # running File 04's normalizer over a corpus change every ECOG patient's
    # hash while every prompt stayed the same, and the ablation study --
    # which reads a hash change as an input change -- would report a
    # difference that does not exist. Every other field here reaches the
    # prompt, so hash equality tracks prompt equality.
    if ecog.get("observations_found"):
        parts.append(
            f"ecog={ecog.get('value')}"
            f"|{ecog.get('date')}"
            f"|{ecog.get('observations_found')}"
            f"|{ecog.get('selection')}"
        )

    # Metastasis and nodal observations, for the same reason ECOG is here.
    # File 07 routes these out of `observations` into their own list, so
    # without this line the 701 observations that describe how far the disease
    # has spread would contribute nothing to the hash, and two patients
    # differing only in cM0 versus cM1 would hash identically.
    #
    # Emitted only when the list is non-empty, so a patient with no metastasis
    # observation hashes exactly as before the routing existed. Patients WITH
    # them do get a new hash: their observations moved between fields, which is
    # a real change to the parsed record and to the prompt built from it.
    metastasis = patient_data.get("cancer_metastasis_observations") or []
    _emit("met", [
        f"{m.get('display', '')}"
        f"|{m.get('value', '')}"
        f"|{m.get('unit', '')}"
        f"|{m.get('date', '')}"
        f"|{m.get('metastasis_category', '')}"
        for m in metastasis
    ])

    # Allergies, for the same reason ECOG and metastasis are here: File 07 gives
    # them a list of their own, so they contribute nothing to the five entries
    # above and two patients differing only in their allergies hashed
    # identically. They are not incidental -- _create_patient_summary renders
    # them under their own "Allergies:" heading, and drug allergy is a standing
    # exclusion criterion in oncology trials ("no known allergy to
    # platinum-based agents").
    #
    # THE THREE SUB-FIELDS ARE THE THREE THE PROMPT RENDERS: display, category
    # and criticality. What is left out and why:
    #   - code: the coding-system identity of the same allergen. Nothing reads
    #     it. Same exclusion the metastasis entry above already makes.
    #   - onset_date: no consumer reads it. Two allergies identical except for
    #     onset produce byte-identical prompt text, so hashing it would move the
    #     hash without moving the prompt -- the value_shape mistake.
    #   - clinical_status / verification_status: read by the PARSER, which
    #     admits only active, non-refuted allergies. Their effect is therefore
    #     already visible here as presence or absence, and an allergy that
    #     becomes inactive leaves the list and changes the hash by disappearing.
    allergies = patient_data.get("allergies") or []
    _emit("allergy", [
        f"{a.get('display') or ''}"
        f"|{a.get('category') or ''}"
        f"|{a.get('criticality') or ''}"
        for a in allergies
    ])

    # mCODE genomic variants. THE SHARPEST OF THE THREE, because the routing is
    # total: extract_genomic_variant_terms' own docstring records that File 07
    # takes these OUT of `observations` entirely, so a patient's biomarkers were
    # invisible to this function while driving both the retrieval query and a
    # named section of the Stage 5 prompt. Two patients differing only in EGFR
    # status hashed identically.
    #
    # SEVEN SUB-FIELDS, one per reader, and every reader is named:
    #   - gene_symbol, hgvs_protein, hgvs_cdna: extract_genomic_variant_terms'
    #     _structured_term() builds the retrieval term from them.
    #   - display: the same function's fallback when gene_symbol is absent, AND
    #     what _create_patient_summary prints for each variant.
    #   - result_value, interpretation: OncologyLabRegistry
    #     .filter_relevant_genomic_variants drops Absent/Negative results
    #     outright, so a variant flipping to "Absent" removes a whole line from
    #     the prompt. Omitting these would let that flip pass unhashed.
    #   - date: the same filter keeps the most recent observation per gene, so
    #     the date decides WHICH variant survives; it is also rendered.
    # Left out: `code` (encoding, as above), `genomic_source` (no reader), and
    # `value` (a duplicate of result_value that nothing on this path reads).
    variants = patient_data.get("cancer_genomic_variants") or []
    _emit("variant", [
        f"{v.get('display') or ''}"
        f"|{v.get('gene_symbol') or ''}"
        f"|{v.get('hgvs_protein') or ''}"
        f"|{v.get('hgvs_cdna') or ''}"
        f"|{v.get('result_value') or ''}"
        f"|{v.get('interpretation') or ''}"
        f"|{v.get('date') or ''}"
        for v in variants
    ])

    # mCODE TNM stage group observations. These are Tier 0 of
    # extract_patient_stage, which is the highest-priority tier and the one that
    # answers for the 295 corpus patients who carry a stage group -- and the
    # stage ordinal it returns drives Stage 4's stage filter, which on this
    # corpus drops on the order of a third of the trial pool for an early-stage
    # patient.
    #
    # THE OBSERVATIONS ARE HASHED, NOT THE STAGE. That is the birth_date rule
    # applied again: the ordinal is a function of these records AND of the
    # extractor's tier order and regexes, both of which have changed twice
    # recently (the AJCC M-category tier, the non-oncology guard). Hashing the
    # derived ordinal would make every patient's hash move whenever the
    # extractor was edited, while their bundle -- the thing the hash is supposed
    # to identify -- had not changed at all.
    #
    # THREE SUB-FIELDS:
    #   - stage_display: the only field the extractor reads; both its regex and
    #     its "metastatic" fallback run against this string.
    #   - date: the extractor sorts on it and takes the most recent, so it
    #     decides which observation answers when a patient was restaged.
    #   - loinc: which staging axis the observation is (clinical, pathological,
    #     other), which is what the parser routed on. It is the analogue of
    #     metastasis_category in the entry above, and it is kept for the same
    #     reason: it identifies the record rather than re-encoding its value.
    # Left out: stage_code, a second encoding of stage_display with no reader --
    # the same exclusion `code` gets everywhere else here. IF THE EXTRACTOR EVER
    # READS stage_code, this entry has to gain it, or a bundle whose code moved
    # while its display did not would change the filter without changing the
    # hash.
    stage_obs = patient_data.get("cancer_stage_observations") or []
    _emit("stage_obs", [
        f"{s.get('stage_display') or ''}"
        f"|{s.get('date') or ''}"
        f"|{s.get('loinc') or ''}"
        for s in stage_obs
    ])

    hash_input = "\n".join(parts)
    return hashlib.sha256(hash_input.encode('utf-8')).hexdigest()[:16]


#------------------------------------------------------------------------------


# ===========================================================================
# HELPER: Patient Summary (used by Stage 5)
# ===========================================================================


# ---------------------------------------------------------------------------
# Condition Relevance Filter for GPT-4o Prompt
# ---------------------------------------------------------------------------
#
# Three-layer classifier that splits conditions into:
#   Tier A (cancer):      primary cancer conditions (CancerCodeRegistry)
#   Tier B (relevant):    comorbidities that oncology trials gate on, or unknown
#   Tier C (background):  conditions confidently irrelevant to trial eligibility
#
# Layer 1 (ICD-10-CM blocks): systematic, code-based inclusion of trial-relevant
#   organ system categories.
# Layer 2 (SNOMED codes): curated high-level SNOMED concepts for the same
#   categories. Covers Synthea data where ICD-10 may not be present.
# Layer 3 (blacklist keywords): display-text exclusion of conditions that are
#   confidently irrelevant to any oncology trial eligibility criterion.
#   Only fires when Layers 1-2 produce no match.
# Default: Tier B (conservative). If uncertain, include.


# == Layer 1: ICD-10-CM relevant blocks ====================================
# Ranges define ICD-10-CM code blocks where oncology trials commonly have
# exclusion or inclusion criteria. Checked at the alpha + two-digit level.
#
# Source: ICD-10-CM 2024 chapter structure (CMS/NCHS) cross-referenced
# against common oncology trial exclusion criteria categories.
# Each tuple is (start_int, end_int) for the numeric portion after the
# alpha prefix.

_ICD10_RELEVANT_BLOCKS: Dict[str, List[Tuple[int, int]]] = {
    # A00-B99: Infectious/parasitic
    "A": [(15, 19)],                          # Tuberculosis
    "B": [(15, 20)],                          # Viral hepatitis (B15-B19) + HIV (B20)
    # D50-D89: Blood diseases + immune disorders (non-neoplastic)
    "D": [(50, 89)],
    # E00-E13: Thyroid + diabetes; E24-E27: adrenal
    "E": [(0, 13), (24, 27)],
    # F20-F31: Psychotic + bipolar disorders
    "F": [(20, 31)],
    # G40-G41: Epilepsy/seizure; G60-G65: neuropathy
    "G": [(40, 41), (60, 65)],
    # I00-I99: All circulatory (cardiac, vascular, cerebrovascular)
    "I": [(0, 99)],
    # J44-J45: COPD + asthma; J68: pneumonitis; J84: ILD/pulmonary fibrosis
    "J": [(44, 45), (68, 68), (84, 84)],
    # K70-K77: Liver diseases
    "K": [(70, 77)],
    # M05-M06: RA; M30-M36: systemic connective tissue (lupus, scleroderma, vasculitis)
    "M": [(5, 6), (30, 36)],
    # N00-N19: Renal diseases
    "N": [(0, 19)],
    # Z85: Personal history of malignant neoplasm; Z94: transplanted organ status
    "Z": [(85, 85), (94, 94)],
}


def _is_icd10_relevant(code: str) -> bool:
    """
    Check if an ICD-10-CM code falls within a trial-relevant block.
    Handles codes with or without dots.
    """
    normalized = code.upper().replace(".", "").strip()
    if len(normalized) < 3:
        return False

    alpha = normalized[0]
    blocks = _ICD10_RELEVANT_BLOCKS.get(alpha)
    if not blocks:
        return False

    try:
        num = int(normalized[1:3])
    except ValueError:
        return False

    for start, end in blocks:
        if start <= num <= end:
            return True
    return False


# == Layer 2: SNOMED relevant concepts =====================================
# High-level SNOMED CT codes for comorbidity categories that oncology trials
# commonly gate on. Covers Synthea-generated conditions where ICD-10 codes
# may not be present. Not exhaustive; Layer 3 blacklist provides safety net.

_SNOMED_RELEVANT_COMORBIDITIES: FrozenSet[str] = frozenset({
    # Cardiac
    "53741008",    # Coronary arteriosclerosis
    "22298006",    # Myocardial infarction
    "84114007",    # Heart failure
    "49436004",    # Atrial fibrillation
    "38341003",    # Hypertension
    # Diabetes
    "44054006",    # Diabetes mellitus type 2
    "73211009",    # Diabetes mellitus
    "46635009",    # Diabetes mellitus type 1
    # Renal
    "431855005",   # Chronic kidney disease
    "90708001",    # Kidney disease
    # Hepatic
    "19943007",    # Cirrhosis of liver
    "235856003",   # Hepatitis disorder
    "128302006",   # Chronic hepatitis C
    "66071002",    # Hepatitis B
    # Autoimmune / inflammatory
    "85828009",    # Autoimmune disease
    "69896004",    # Rheumatoid arthritis
    "55464009",    # Systemic lupus erythematosus
    "24700007",    # Multiple sclerosis
    "34000006",    # Crohn disease
    "64766004",    # Ulcerative colitis
    # Infectious
    "86406008",    # HIV infection
    "56717001",    # Tuberculosis
    # Hematologic
    "271737000",   # Anemia
    "74576004",    # Thrombocytopenia
    "128053003",   # Deep vein thrombosis
    "59282003",    # Pulmonary embolism
    # Neurologic
    "84757009",    # Epilepsy
    "230690007",   # Cerebrovascular accident (stroke)
    # Pulmonary
    "13645005",    # COPD
    "195967001",   # Asthma
    "700250006",   # Idiopathic pulmonary fibrosis
    # Psychiatric
    "58214004",    # Schizophrenia
    "13746004",    # Bipolar disorder
})


# == Layer 3: Blacklist keywords (display-text exclusion) ==================
# Conditions whose display text contains ANY of these keywords are classified
# as Tier C (background/summarized) when Layers 1-2 produce no match.
#
# These are confidently irrelevant to oncology trial eligibility criteria.
# Conservative: only categories where accidental exclusion of a relevant
# condition is essentially impossible.
#
# IMPORTANT: This is an EXCLUDE list. Conditions NOT matching this list
# default to Tier B (relevant). Missing a keyword here means an irrelevant
# condition stays in Tier B (wastes tokens but is safe). Adding a keyword
# incorrectly means a relevant condition drops to Tier C (unsafe).

_IRRELEVANT_CONDITION_KEYWORDS: FrozenSet[str] = frozenset({
    # Dental / oral hygiene
    "dental caries", "dental", "gingivitis", "periodont", "tooth",
    "caries",
    # Vision (non-drug-related)
    "myopia", "hypermetropia", "hyperopia", "astigmatism", "presbyopia",
    "macular degeneration",
    # Hearing
    "hearing loss", "tinnitus", "otitis media",
    # Dermatologic (cosmetic / non-inflammatory)
    "acne", "seborrheic", "alopecia", "onychomycosis",
    "contact dermatitis", "eczema", "callus", "corn of skin",
    # Musculoskeletal (mechanical / degenerative)
    "osteoarthritis", "osteoporosis", "low back pain",
    "back pain", "sprain", "strain of", "tendinitis",
    "plantar fasciitis", "bunion", "carpal tunnel",
    "rotator cuff", "meniscus",
    # Routine / preventive / administrative
    "immunization", "vaccination", "screening",
    "normal pregnancy", "finding of",
    "well child", "annual exam", "routine checkup", "encounter for",
    # Social / lifestyle (miscoded as conditions)
    "lack of physical exercise", "stress", "body mass index",
    "tobacco use", "smoker", "social isolation",
    "misuses drugs", "unhealthy alcohol",
    # Benign / minor
    "benign prostatic hyperplasia", "hemorrhoids",
    "varicose veins", "gastroesophageal reflux",
    "allergic rhinitis", "seasonal allergic",
    "sinusitis", "pharyngitis", "bronchitis",
    "urinary tract infection", "otitis externa",
    # Reproductive (non-pathological)
    "premenstrual", "menopausal", "menopause",
    "erectile dysfunction",
    # Metabolic (minor, non-trial-gating)
    "vitamin d deficiency", "iron deficiency",
    "hyperlipidemia", "hypercholesterolemia",
    # Pain syndromes
    "headache", "migraine", "fibromyalgia",
})


def _classify_condition_relevance(
    condition: Dict,
    cancer_registry,
) -> str:
    """
    Classify a condition into relevance tiers for GPT-4o prompt construction.

    Layer 0: CancerCodeRegistry -> "cancer" (Tier A)
    Layer 1: ICD-10-CM block check -> "relevant" (Tier B)
    Layer 2: SNOMED code check -> "relevant" (Tier B)
    Layer 3: Blacklist keyword check -> "background" (Tier C)
    Default: -> "relevant" (Tier B, conservative)

    Returns:
        "cancer"     : Tier A, primary cancer condition
        "relevant"   : Tier B, trial-relevant comorbidity or unknown
        "background" : Tier C, confidently irrelevant
    """
    # Layer 0: cancer
    if cancer_registry.is_primary_cancer(condition):
        return "cancer"

    # Gather all codes (backward compatible)
    codings = condition.get("codings", [])
    if not codings:
        code = (condition.get("code") or "").strip()
        if code and code.lower() not in ("unknown", "none"):
            codings = [{"system_key": "unknown", "code": code}]

    # Layer 1: ICD-10-CM block check
    for c in codings:
        if c.get("system_key") in ("icd10cm", "icd10"):
            if _is_icd10_relevant(c["code"]):
                return "relevant"

    # Layer 2: SNOMED code check
    for c in codings:
        code = c.get("code", "")
        if code in _SNOMED_RELEVANT_COMORBIDITIES:
            return "relevant"

    # Layer 3: Blacklist keyword check
    display_lower = (condition.get("display") or "").lower()
    if display_lower:
        for keyword in _IRRELEVANT_CONDITION_KEYWORDS:
            if keyword in display_lower:
                return "background"

    # Default: conservative, keep as relevant (Tier B)
    return "relevant"


_IRRELEVANT_MEDICATION_KEYWORDS: FrozenSet[str] = frozenset({
    # OTC pain / fever (NOT NSAIDs -- ibuprofen/naproxen/aspirin have
    # platelet and bleeding risk implications that some trials gate on)
    "acetaminophen",
    # Vitamins / supplements
    "vitamin", "multivitamin", "folic acid", "iron supplement",
    "fish oil", "omega-3", "zinc sulfate",
    "cholecalciferol", "ergocalciferol", "cyanocobalamin",
    "calcium carbonate", "calcium citrate",
    # Gastrointestinal (non-immunosuppressive)
    "omeprazole", "pantoprazole", "lansoprazole", "esomeprazole",
    "famotidine",
    "simethicone", "docusate", "polyethylene glycol", "bisacodyl",
    "loperamide", "antacid", "laxative", "stool softener",
    "senna", "miralax", "psyllium",
    # Allergy / cold / nasal
    "cetirizine", "loratadine", "fexofenadine", "diphenhydramine",
    "fluticasone nasal", "mometasone nasal", "oxymetazoline",
    "guaifenesin", "dextromethorphan", "saline nasal",
    # Eye care (specific safe agents only, NOT generic "eye drop")
    "artificial tears", "latanoprost", "brimonidine ophthalmic",
    # Dermatologic (topical only, non-systemic)
    "hydrocortisone cream", "moisturizer", "sunscreen",
    "benzoyl peroxide", "clotrimazole topical", "miconazole topical",
    "mupirocin", "bacitracin", "neosporin",
    # Dental / oral hygiene
    "fluoride", "chlorhexidine mouth",
    # Smoking cessation
    "nicotine patch", "nicotine gum", "varenicline",
    # Sleep (OTC)
    "melatonin",
    # Electrolytes / hydration (saline only, NOT potassium)
    "sodium chloride irrigation", "oral rehydration",
})


# ===========================================================================
# PROCEDURE RENDERING RELEVANCE
# ===========================================================================
#
# BUILT FROM WHAT THE CORPUS ACTUALLY CONTAINS, NOT FROM WHAT SOUNDS LIKELY.
# 100 patients were drawn from the 1,000-bundle Synthea corpus with
# ``random.Random(42).sample(sorted(glob(...)), 100)`` -- sorted first so the
# population is deterministic, seeded so the draw is reproducible -- and every
# distinct procedure display in them was enumerated: 317 of them. Every entry
# below is one of those 317 or a family they belong to; nothing here is
# invented. The 118 that match no layer and are kept by default are listed in
# the pass report rather than here, because a list of things the code does not
# act on is a list that rots.
#
# TWO ACCIDENTS THIS ENUMERATION CAUGHT, both by measurement and neither by
# reading, and both are the substring hazard CLAUDE.md records for the
# biomarker keywords ("ret" inside "retinopathy"):
#
#   "port"              matched "dental consultation and REPORT" -- in 100 of
#                       100 patients. A keep keyword that protected a dental
#                       line from the dental blacklist. Replaced by the
#                       explicit central-line phrases below.
#   "surgical procedure" matched "DENTAL SURGICAL PROCEDURE" in 96 of 100. Not
#                       an accident of spelling but of breadth; deleted,
#                       because a genuine surgery that matches no keyword is
#                       kept by the default anyway.
#
# WHAT THE KEEP LAYERS ARE ACTUALLY FOR, stated because it is not obvious: the
# default is KEEP, so these layers change nothing for a procedure the blacklist
# does not name. Their whole job is to OVERRIDE the blacklist. That is why a
# keyword whose only measured effect is to keep something already kept was
# removed rather than left in for symmetry.

_PROCEDURE_KEEP_SNOMED: FrozenSet[str] = frozenset({
    # -- oncology diagnostic --------------------------------------------------
    "122548005",     # Biopsy of breast
    "65575008",      # Biopsy of prostate
    "76164006",      # Biopsy of colon
    "396487001",     # Sentinel lymph node biopsy
    "443497002",     # Excision of sentinel lymph node
    "234262008",     # Excision of axillary lymph node
    "73761001",      # Colonoscopy
    "274031008",     # Rectal polypectomy
    "90226004",      # Cytopathology, smear, genital source (cervical cytology)
    "434363004",     # HER2 gene detection by FISH
    "433114000",     # HER2 gene detection by immunohistochemistry
    # -- oncology therapeutic -------------------------------------------------
    "367336001",     # Chemotherapy
    "394894008",     # Pre-operative chemotherapy
    "703423002",     # Combined chemotherapy and radiation therapy
    "33195004",      # External beam radiation therapy
    "1287742003",    # Radiotherapy
    "447759004",     # Brachytherapy of breast
    "113120007",     # Interstitial brachytherapy
    "385798007",     # Radiation therapy care
    # -- tumour resection -----------------------------------------------------
    "43075005",      # Partial resection of colon
    "90470006",      # Prostatectomy
    "392021009",     # Lumpectomy of breast
    "392023007",     # Excision of lesion of breast
    "80146002",      # Excision of appendix
    "45595009",      # Laparoscopic cholecystectomy
    # -- marrow / stem cell / organ transplant --------------------------------
    "58776007",      # Autologous bone marrow transplant
    "70536003",      # Transplant of kidney
    "711446003",     # Transplantation of kidney regime
    "428830000",     # Pretransplant evaluation of kidney recipient
    "306316000",     # Referral to transplant surgeon
    # -- transfusion ----------------------------------------------------------
    "71493000",      # Transfusion of packed red blood cells
    "180207008",     # Intravenous blood transfusion of packed cells
    "116861002",     # Transfusion of fresh frozen plasma
    "12719002",      # Platelet transfusion
    "116863004",     # Transfusion of red blood cells
    # -- major surgery (trials carry recent-major-surgery windows) ------------
    "232717009",     # Coronary artery bypass grafting
    "418824004",     # Off-pump coronary artery bypass
    "414088005",     # Emergency coronary artery bypass graft
    "359672006",     # Median sternotomy
    "63697000",      # Cardiopulmonary bypass operation
    "26212005",      # Replacement of aortic valve
    "773996000",     # Transcatheter aortic valve implantation
    "232965003",     # Implantation of cardiac ventricular assist device
    "11466000",      # Cesarean section
    "699253003",     # Surgical manipulation of joint of knee
    "387685009",     # Surgical manipulation of shoulder joint
    "177765008",     # Opening of chest
    "18286008",      # Catheter ablation of tissue of heart
    # -- vascular access / lines ----------------------------------------------
    "415070008",     # Percutaneous coronary intervention
    "392247006",     # Insertion of catheter into artery
    "65677008",      # Pulmonary catheterization with Swan-Ganz catheter
    "42825003",      # Cannulation
    "433112001",     # Percutaneous mechanical thrombectomy of portal vein
    # -- imaging of tumour sites ----------------------------------------------
    "71651007",      # Mammography
    "241055006",     # Mammogram - symptomatic
    "24623002",      # Screening mammography
    "1571000087109",  # Ultrasonography of bilateral breasts
    "241615005",     # MRI of breast
    "418023006",     # CT of chest, abdomen and pelvis
})


# Display-keyword keep families. Substring matched, lower-cased, and every one
# of them was checked against all 317 corpus displays for an accidental match --
# which is how the two above were found.
_PROCEDURE_KEEP_KEYWORDS: FrozenSet[str] = frozenset({
    # Protective: anything naming the disease survives whatever follows it.
    # This is what makes a future "lung cancer screening low-dose CT" safe from
    # the routine-screening blacklist without anyone having to notice.
    "cancer", "neoplasm", "tumor", "tumour", "oncolog", "malignan",
    "carcinoma", "lymphoma", "leukemia", "leukaemia", "myeloma", "metasta",
    # Oncology diagnostic
    "biopsy", "cytopathology", "colonoscopy", "endoscopy", "mammogra",
    "polypectomy", "sentinel lymph node",
    "human epidermal growth factor receptor",
    # Oncology therapeutic
    "chemotherapy", "immunotherapy", "radiation therapy", "radiotherapy",
    "brachytherapy", "antineoplastic",
    # Marrow, stem cell, organ transplant
    "transplant", "bone marrow", "stem cell",
    # Transfusion
    "transfusion",
    # Major surgery. NOT the bare suffixes "-ectomy"/"-otomy"/"-plasty": their
    # dominant corpus matches are "gingivectomy or gingivoplasty, per tooth"
    # (96/100) and "episiotomy" (33/100), and a real surgery that matches
    # nothing here is kept by the default in any case.
    "excision", "resection", "amputation", "bypass", "graft",
    "laparotomy", "laparoscop", "thoracotomy", "sternotomy", "craniotomy",
    "cesarean", "surgical manipulation", "valve replacement",
    # Vascular access and central lines. Spelled out rather than "port".
    "catheter", "cannulation", "central venous", "implantable port",
    "port-a-cath", "portacath", "vascular access device",
    "peripherally inserted central",
    # Imaging of tumour sites
    "computed tomography", "magnetic resonance", "ultrasonography",
    "positron emission",
    # Functional / performance status. These are ASSESSMENTS, and the blacklist
    # drops assessment instruments -- so without this line "assessment using
    # New York Heart Association classification" would be dropped, and NYHA
    # class is a cardiac gate several trials carry. Frailty likewise.
    "performance status", "eastern cooperative oncology", "karnofsky",
    "new york heart association", "frailty",
    # Critical care. "admission to" is on the blacklist as encounter
    # paperwork; an ICU admission is not paperwork.
    "intensive care",
})


# == Layer 3: the blacklist -- the ONLY thing that can drop a procedure =======
#
# Same rule as _IRRELEVANT_CONDITION_KEYWORDS, one notch stricter. A missing
# keyword here renders a useless line (tokens). A wrong keyword removes a
# procedure from the record the model judges on, silently, with no line, no
# count and no placeholder saying anything was removed. Only families where
# accidentally excluding a trial-relevant procedure is essentially impossible.
_IRRELEVANT_PROCEDURE_KEYWORDS: FrozenSet[str] = frozenset({
    # -- dental / oral -- 15 distinct types in the sample, most in ~100% of
    # patients, and the single largest contributor to the section's size.
    "dental", "gingiv", "tooth", "teeth", "denture", "periodont",
    "plaque and calculus", "oral health education", "oral examination",
    "dentist", "orthodontic",
    # -- routine immunisation. NOT the substring "immuno", which is inside
    # "immunotherapy" and "immunohistochemistry".
    "vaccine", "vaccination", "immunization", "immunisation",
    "tetanus antitoxin",
    # -- casts and splints for simple fractures
    "bone immobilization", "application of cast", "splint",
    # -- routine screening and psychosocial instruments. The keep layer's
    # disease words run first, so an oncology screening is protected.
    "screening", "assessment using", "assessment of substance use",
    "assessment of anxiety", "assessment of health and social care needs",
    "suicide risk assessment", "risk assessment", "diagnostic assessment",
    "anticipatory guidance", "education", "counseling",
    # -- administrative, documentation and encounter paperwork
    "medication reconciliation", "history taking", "history and physical",
    "patient discharge", "discharge from hospital", "discharge to ward",
    "initial patient assessment", "pre-discharge assessment",
    "individualized plan of care", "coordination of care",
    "care regimes assessment", "review of systems", "notification",
    "certification procedure", "scheduling", "documentation procedure",
    "medical records review", "information gathering", "liaising",
    "patient transfer", "transfer to", "admission to", "referral",
    "consultation for treatment", "discussion about", "telemedicine",
    "multidisciplinary", "ancillary services", "nursing care", "triage",
    "vital signs", "physical examination", "general examination",
    "evaluation procedure", "medication review", "measurement procedure",
    "preparation of patient for procedure",
    # -- specimen collection and routine lab administration. The RESULTS are
    # rendered in the Relevant Lab Values section; these lines say only that a
    # specimen was taken. Infectious serologies (HIV, HBV, HCV, TB, syphilis)
    # are deliberately NOT here -- see the report.
    "urinalysis", "urine culture", "urine specimen", "throat culture",
    "sputum examination", "blood group typing", "complete blood count",
    "hemogram", "metabolic panel", "peripheral blood smear",
    "laboratory test",
})


# How many procedures this process rendered and how many it withheld.
#
# THE AGE_PARSE_FAILURES FOOTING: a module-level Counter, counts only, read by
# whoever wants them and written by nobody else. NEVER a procedure name and
# never any clinical text -- the whole point of the drop is that the text does
# not travel, and a counter keyed by display would put it in a place with a
# longer life than the prompt.
#
# NO PER-RENDER LOG EVENT, deliberately, and on this module's own convention:
# _create_patient_summary logs nothing today, and it runs once per patient in
# thousand-patient batch runs.
#
# WHAT THE UNITS ARE, because "procedures" is ambiguous here: these count
# RENDER CANDIDATES -- the deduplicated procedure TYPES that
# filter_relevant_procedures hands the renderer -- not raw FHIR procedure
# resources, of which a patient has many more. A patient summarised twice
# counts twice; this is a process-lifetime tally, not a per-patient field.
PROCEDURE_RENDER_COUNTS = Counter()

PROCEDURE_RENDER_KEPT = "kept"
PROCEDURE_RENDER_DROPPED = "dropped"


def _classify_procedure_relevance(procedure: Dict) -> str:
    """Classify one procedure as worth rendering into the Stage 5 summary.

    THE SAME ASYMMETRY AS ``_classify_condition_relevance``, AND FOR A SHARPER
    REASON. The protective KEEP layers run FIRST, the drop decision is made ONLY
    by a confident blacklist that is consulted when no keep layer matched, and a
    procedure that matches nothing is KEPT. Read Layer 3's comment on the
    condition side: a missing blacklist keyword wastes tokens, a wrong one
    removes clinical evidence. Here the consequence is stronger than there --
    a "background" condition is still SUMMARIZED into the prompt, while a
    background procedure is not rendered at all -- so the default has to be
    keep, and it is.

    WHY A RENDERING FILTER AT ALL. Measured over 100 seeded-sample patients of
    the Synthea corpus (see the report for the draw), the Procedures section is
    40.2% to 62.9% of the summary's characters (median 50.8%), a median of 100
    rendered lines per patient, and most of it is dental work, psychosocial
    screening instruments, referrals and discharge paperwork. None of it can
    decide an oncology eligibility criterion.

    THE FHIR DATA AND THE PARSER OUTPUT ARE UNTOUCHED. This decides one thing:
    whether a line is printed. ``patient_data["procedures"]`` still carries
    every procedure, ``compute_patient_hash`` still hashes every one of them
    (it reads the parsed list, never this text), and nothing downstream of the
    prompt sees a different patient.

    Args:
        procedure: one dict from ``_parse_procedure`` -- ``code``, ``display``,
            ``date``, ``status``. Passed whole rather than as a display string
            because the keep decision reads the SNOMED code first.

    Returns:
        "relevant"   : render it (default, and the answer whenever nothing
                       matched)
        "background" : confidently irrelevant, do not render
    """
    display_lower = (procedure.get("display") or "").lower()

    # Layer 1: SNOMED code. Checked before any text, because a code is stable
    # and a display string is not.
    code = (procedure.get("code") or "").strip()
    if code and code in _PROCEDURE_KEEP_SNOMED:
        return "relevant"

    # Layer 2: keep-keyword families.
    if display_lower:
        for keyword in _PROCEDURE_KEEP_KEYWORDS:
            if keyword in display_lower:
                return "relevant"

    # Layer 3: the blacklist, and ONLY here can a procedure be dropped.
    if display_lower:
        for keyword in _IRRELEVANT_PROCEDURE_KEYWORDS:
            if keyword in display_lower:
                return "background"

    # Default: unknown means keep.
    return "relevant"


def _classify_medication_relevance(display: str) -> str:
    """
    Classify a medication as relevant or background for GPT-4o prompt.

    Args:
        display: Medication display name string.

    Returns:
        "relevant"   : Include with full detail (default)
        "background" : Confidently irrelevant, summarize
    """
    display_lower = display.lower()
    for keyword in _IRRELEVANT_MEDICATION_KEYWORDS:
        if keyword in display_lower:
            return "background"
    return "relevant"


# ── Lab Unit Normalization ─────────────────────────────────────────────────
# Converts common alternative units to canonical US clinical units before
# GPT-4o evaluation. Covers only labs in OncologyLabRegistry. Conversion
# factors are clinically validated per LOINC/UCUM standards.
# Raw FHIR data is never modified -- normalization is summary-only.
#
# Canonical units match standard US clinical trial reporting:
#   Creatinine : mg/dL   (SI: µmol/L ÷ 88.42)
#   Hemoglobin : g/dL    (SI: mmol/L × 1.6113, g/L ÷ 10)
#   Bilirubin  : mg/dL   (SI: µmol/L ÷ 17.1)
#   Calcium    : mg/dL   (SI: mmol/L × 4.008)
#   Glucose    : mg/dL   (SI: mmol/L × 18.016)
#   ANC/WBC    : cells/µL (10^3/µL × 1000, 10^9/L × 1000)
#   Platelets  : cells/µL (10^3/µL × 1000, 10^9/L × 1000)
#
# Edge cases:
#   - value/unit None   → original returned unchanged
#   - unrecognized unit → original returned unchanged
#   - float() failure   → caught, original returned unchanged
#   - Synthea data      → already in canonical units, no conversions triggered
# NOTE: substring matching on canonical_display (e.g. "bilirubin" matches both
# "Bilirubin (total)" and "Bilirubin (direct)"). Clinically harmless here
# since both use the same µmol/L → mg/dL factor, but review if new labs added.

_LAB_UNIT_CONVERSIONS: Dict[Tuple[str, str], Tuple[str, Any]] = {
    # Creatinine: µmol/L → mg/dL
    ("creatinine", "µmol/l"):   ("mg/dL", lambda v: round(v / 88.42, 2)),
    ("creatinine", "umol/l"):   ("mg/dL", lambda v: round(v / 88.42, 2)),
    # Hemoglobin: mmol/L → g/dL, g/L → g/dL
    ("hemoglobin", "mmol/l"):   ("g/dL",  lambda v: round(v * 1.6113, 1)),
    ("hemoglobin", "g/l"):      ("g/dL",  lambda v: round(v / 10, 1)),
    # Bilirubin (total + direct share same factor)
    ("bilirubin", "µmol/l"):    ("mg/dL", lambda v: round(v / 17.1, 2)),
    ("bilirubin", "umol/l"):    ("mg/dL", lambda v: round(v / 17.1, 2)),
    # Calcium: mmol/L → mg/dL
    ("calcium", "mmol/l"):      ("mg/dL", lambda v: round(v * 4.008, 1)),
    # Glucose: mmol/L → mg/dL
    ("glucose", "mmol/l"):      ("mg/dL", lambda v: round(v * 18.016, 1)),
    # ANC / Neutrophils: 10^3/µL or 10^9/L → cells/µL
    ("anc",         "10*3/ul"): ("cells/µL", lambda v: int(round(v * 1000))),
    ("anc",         "10^3/ul"): ("cells/µL", lambda v: int(round(v * 1000))),
    ("anc",         "10*9/l"):  ("cells/µL", lambda v: int(round(v * 1000))),
    ("neutrophils", "10*3/ul"): ("cells/µL", lambda v: int(round(v * 1000))),
    ("neutrophils", "10^3/ul"): ("cells/µL", lambda v: int(round(v * 1000))),
    ("neutrophils", "10*9/l"):  ("cells/µL", lambda v: int(round(v * 1000))),
    # Platelets: 10^3/µL or 10^9/L → cells/µL
    ("platelets", "10*3/ul"):   ("cells/µL", lambda v: int(round(v * 1000))),
    ("platelets", "10^3/ul"):   ("cells/µL", lambda v: int(round(v * 1000))),
    ("platelets", "10*9/l"):    ("cells/µL", lambda v: int(round(v * 1000))),
    # WBC: 10^3/µL or 10^9/L → cells/µL
    ("wbc", "10*3/ul"):         ("cells/µL", lambda v: int(round(v * 1000))),
    ("wbc", "10^3/ul"):         ("cells/µL", lambda v: int(round(v * 1000))),
    ("wbc", "10*9/l"):          ("cells/µL", lambda v: int(round(v * 1000))),
}


# ===========================================================================
# LAB UNIT NORMALIZATION DEGRADATION RECORD (item 11a)
# ===========================================================================
#
# `Exception and Fallback Audit.md` ranked _normalize_lab_unit's bare
# `except Exception: pass` Open (low): "the rate at which the normalizer gives
# up is unknown". Measured against the code, it gives up THREE ways, not one,
# and only one of them is the thing that matters:
#
#   no_value_or_unit  value or unit is None, so there is nothing to convert.
#                     Ordinary and expected — a qualitative observation, or a
#                     unitless score. NOT a failure.
#   conversion_error  float(value) or the conversion lambda raised. The value
#                     looked convertible and was not. This is the exception exit.
#   unconverted       every rule was consulted and none matched. THIS is the one
#                     the audit was worried about: a real unit reached GPT-4o
#                     unconverted, so the model sees "4.5 10*9/L" where a rule
#                     would have given "4500 cells/µL" and has to do the
#                     conversion itself against a threshold criterion.
#
# COUNTED SEPARATELY OR THE SIGNAL IS LOST. One counter over all three would be
# dominated by no_value_or_unit — the common, harmless case — and the number
# that matters would be invisible inside it. Three key namespaces in one
# Counter, following PARTIAL_DATE_DEGRADATIONS' `out_of_range:{precision}`
# shape, which is the same "one Counter, self-describing keys" pattern.
#
# The `unconverted` keys name the LAB AND THE UNIT, because a fix means adding
# a row to _LAB_UNIT_CONVERSIONS and that row needs both. The other two name the
# lab only.
#
# NOT a new key in anything the pipeline returns: the twelve characterization
# fixtures diff the pipeline's output field by field.
#
# THIS COUNTS RATHER THAN RAISES, for the reason argued at
# AGE_PARSE_FAILURES: an unrecognised unit is third-party data, the recovery is
# correct (value and unit stay paired, so nothing is mislabelled), and aborting
# a patient's evaluation over one lab row would trade a small, safe degradation
# for a total one.
LAB_UNIT_DEGRADATIONS = Counter()

LAB_UNIT_NO_VALUE_OR_UNIT = "no_value_or_unit"
LAB_UNIT_CONVERSION_ERROR = "conversion_error"
LAB_UNIT_UNCONVERTED = "unconverted"


def _normalize_lab_unit(
    canonical_display: str,
    value: Any,
    unit: Optional[str],
) -> Tuple[Any, Optional[str]]:
    """
    Normalize a lab value+unit to canonical US clinical units if a conversion
    exists in _LAB_UNIT_CONVERSIONS. Returns original (value, unit) otherwise.
    Never raises.

    Every exit that does NOT convert is recorded in LAB_UNIT_DEGRADATIONS under
    its own key namespace — see the block above for why all three are counted
    and why they are counted apart. The returned values are unchanged from
    before item 11a on every one of the four paths.
    """
    # OUTSIDE the try, and coerced rather than assumed. It used to be the first
    # statement INSIDE the try, so a non-string canonical_display raised
    # AttributeError there and was swallowed into "return the original". Moving
    # it out would have turned that swallow into a raise out of a function whose
    # contract is "never raises", so it is coerced with str() instead — the same
    # outcome as before for every caller, and no new way to fail.
    display_key = str(canonical_display or "").lower().strip()

    if value is None or unit is None or not str(unit).strip():
        # AN EMPTY-STRING UNIT COUNTS HERE, NOT AS "unconverted", and getting
        # that wrong would have made the whole counter useless. _create_patient_summary
        # calls this with `obs.get("unit") or ""`, so a unit-less observation
        # arrives as "" and never as None — every one of them would have landed
        # in the `unconverted` bucket, which is supposed to mean "a real unit
        # this table does not know reached the judge". The common harmless case
        # would then have swamped the one number the audit asked for, which is
        # the exact failure the item's "count them separately or the signal is
        # lost" warns about.
        #
        # RETURN VALUE UNCHANGED: an empty unit fell through every rule before
        # and was returned as-is, and it still is.
        LAB_UNIT_DEGRADATIONS[f"{LAB_UNIT_NO_VALUE_OR_UNIT}:{display_key}"] += 1
        return value, unit
    try:
        unit_key    = unit.lower().strip().replace(" ", "")
        for (disp, u), (target_unit, convert_fn) in _LAB_UNIT_CONVERSIONS.items():
            if disp in display_key and u == unit_key:
                return convert_fn(float(value)), target_unit
    except Exception as exc:
        # STILL BROAD, and still recovering — the docstring's "never raises" is
        # a contract Stage 5's prompt builder depends on. What changed is that
        # it is no longer silent: the exception TYPE is in the key, because a
        # ValueError from float("N/A") and a TypeError from a list value are
        # different data problems.
        LAB_UNIT_DEGRADATIONS[
            f"{LAB_UNIT_CONVERSION_ERROR}:{display_key}:{type(exc).__name__}"
        ] += 1
        return value, unit

    # Fell through every rule: a unit this table does not know reaches the judge
    # as it was recorded. The one exit of the three that is a genuine gap.
    LAB_UNIT_DEGRADATIONS[
        f"{LAB_UNIT_UNCONVERTED}:{display_key}:{unit.lower().strip()}"
    ] += 1
    return value, unit


#------------------------------------------------------------------------------


# How each extractor tier is described to the model, in the tier's own words
# rather than in the extractor's identifier. Stage 4 acts on the ordinal alone;
# the model is being asked to reason about a stage-gated criterion, and "the
# clinician recorded a stage group" and "a diagnosis name contained the word
# metastatic" are different grades of evidence for the same number.
#
# The wording lives here and the vocabulary lives in
# oncotriage/extraction/stage.py, which is the split the dashboard's
# PATIENT_OUTCOME_LABELS already uses: the module that OWNS the fact names its
# members, and the module that RENDERS them holds the prose.
_STAGE_SOURCE_PHRASES: Dict[str, str] = {
    STAGE_SOURCE_STAGE_GROUP:
        "from a recorded stage group observation",
    STAGE_SOURCE_M_CATEGORY:
        "from a recorded AJCC clinical M category observation",
    STAGE_SOURCE_CONDITION_DISPLAY:
        "from diagnosis text",
    STAGE_SOURCE_METASTATIC_KEYWORD:
        "from diagnosis text describing metastatic disease",
}

# A tier added to the extractor without a phrase here would reach this module
# as a KeyError while a prompt was being built -- inside node_llm_classifier_
# evaluation, where the graph's error handler turns it into a whole patient
# lost. Raising at import instead makes it the failure it is: an incomplete
# edit, found before anything runs.
if set(_STAGE_SOURCE_PHRASES) != set(STAGE_SOURCES):
    raise RuntimeError(
        "_STAGE_SOURCE_PHRASES and STAGE_SOURCES disagree: "
        f"phrases {sorted(_STAGE_SOURCE_PHRASES)} vs "
        f"vocabulary {sorted(STAGE_SOURCES)}"
    )


def _create_patient_summary(patient_data: Dict) -> str:
    """
    Create compact patient summary for GPT-4o criterion-level evaluation.

    Sections:
      Demographics      : age, sex, race, ethnicity
      Performance Status: ECOG (LOINC 89247-1), or an explicit statement that
                          none is recorded. Never defaults to 0.
      Cancer Stage      : the ordinal Stage 4's filter acted on, rendered as
                          the AJCC numeral with the tier that produced it, or
                          an explicit statement that none is recorded. Never
                          treats stage 0 as absent.
      Conditions        : relevance-filtered into three tiers:
                          Tier A (cancer): full detail with [neoplasm] tag
                          Tier B (relevant): full detail with [comorbidity] tag
                          Tier C (background): one summary line with count + preview
      Medications       : all unique active medications
      Allergies         : active, non-refuted allergies with category and criticality
      Procedures        : all unique procedure types, most recent date per type
      Lab Values        : LOINC-filtered oncology-relevant observations,
                          most recent value per lab concept

    Condition filtering uses _classify_condition_relevance() (three-layer:
    ICD-10 blocks, SNOMED codes, blacklist keywords). Prevents real-world
    patients with 50-100+ conditions from overwhelming GPT-4o's context
    window and attention. Tier C conditions are summarized, never dropped.

    For each lab concept and procedure type, only the most recent value is
    included. Trial eligibility criteria evaluate current status, not history.

    The cancer registry, the MeSH filter and the lab registry come from
    oncotriage.agent.deps, resolved once at the top of this function.
    """
    # Resolved through the dependency seam, ONCE per call. File 13 read these
    # as module globals bound at exec time, which is what Files 35, 36, 45 and
    # 46 rebound to redirect the pipeline; a module function cannot see a
    # caller's globals, so the seam is what keeps those redirects working.
    # Once per call rather than per use so one invocation cannot see two
    # different objects if an override is installed mid-flight.
    cancer_registry = deps.get_cancer_registry()
    lab_registry = deps.get_lab_registry()
    mesh_filter = deps.get_mesh_filter()

    demographics = patient_data["demographics"]
    conditions   = patient_data["conditions"]
    medications  = patient_data["medications"]
    observations = patient_data.get("observations") or []
    procedures   = patient_data.get("procedures") or []
    allergies    = patient_data.get("allergies") or []
    ecog         = patient_data.get("ecog_performance_status") or {}

    # ── Demographics ──────────────────────────────────────────────────────
    summary = (
        f"Age: {demographics.get('age', 'unknown')} | "
        f"Sex: {demographics.get('sex', 'unknown')} | "
        f"Race: {demographics.get('race', 'unknown')} | "
        f"Ethnicity: {demographics.get('ethnicity', 'unknown')}"
    )

    # ── Performance Status ────────────────────────────────────────────────
    # Its own named line, directly under demographics, because "ECOG 0-1" is
    # the most common single gate in interventional oncology and the model has
    # to be able to find it without inferring it from a lab list.
    #
    # `is None` is the test, never truthiness: ECOG 0 means fully active -- the
    # most eligible a patient can be -- and `if value:` would silently report
    # the best possible performance status as no performance status.
    #
    # When observations exist but none is usable, the count and the reason are
    # stated rather than collapsed into "not recorded". "No ECOG on file" and
    # "an ECOG exists but postdates this data snapshot" are different facts, and
    # a criterion evaluated against the wrong one is wrong in a way nothing
    # downstream can detect.
    summary += "\n\nPerformance Status:\n"
    ecog_value = ecog.get("value")
    if ecog_value is not None:
        ecog_date = ecog.get("date") or ""
        date_str  = ecog_date[:10] if ecog_date and ecog_date != "unknown" else "date unknown"

        # Counted against the pool the winner was actually drawn from, not
        # against every observation on the bundle. "most recent of 3" is false
        # when one of the three postdates the snapshot and is therefore later
        # than the one being reported.
        eligible = ecog.get("observations_on_or_before_reference") or 0
        excluded = ((ecog.get("observations_after_reference") or 0)
                    + (ecog.get("observations_undated") or 0))

        detail = [date_str]
        if eligible > 1:
            detail.append(f"most recent of {eligible}")
        if excluded:
            detail.append(f"{excluded} further observation(s) outside the "
                          f"{ecog.get('reference_date')} snapshot")
        summary += f"- ECOG performance status: {ecog_value} ({'; '.join(detail)})\n"
    elif ecog.get("observations_found"):
        summary += (
            f"- ECOG performance status: not available "
            f"({ecog.get('observations_found')} observation(s) on file, none usable: "
            f"{ecog.get('selection')}; reference date {ecog.get('reference_date')})\n"
        )
    else:
        summary += "- ECOG performance status: not recorded\n"

    # ── Cancer Stage ──────────────────────────────────────────────────────
    # Directly under Performance Status because these are the two most common
    # gates in interventional oncology and the model should not have to hunt
    # for either. ECOG got its own named line for that reason; stage had none,
    # while Stage 4's filter was already DROPPING trials on the ordinal — so
    # the model was resolving stage-gated criteria for a patient whose stage
    # the pipeline knew and never stated.
    #
    # THE SAME CALL STAGE 4 MAKES, with the same three inputs read from the
    # same three patient_data keys. Not a second derivation: if this section
    # and the filter could disagree, the prompt would be asserting a stage the
    # trial list was not selected under, which is the class of defect the
    # single-vocabulary rule exists to prevent. The delegate
    # extract_patient_stage() and extract_patient_stage_with_source() are one
    # implementation, so "the same call" is a fact about the code rather than a
    # promise about keeping two call sites in step.
    #
    # `is None` is the test, never truthiness — the same trap as ECOG 0 above.
    # Stage 0 is in-situ disease, a real stage that gates real trials, and
    # `if stage:` would report the earliest stage a patient can carry as no
    # stage at all.
    #
    # ABSENCE IS STATED, NOT OMITTED, on the Performance Status precedent. "No
    # stage on file" is itself a fact a stage-gated criterion needs: under the
    # system prompt's conservatism rule the model can resolve such a criterion
    # to not_evaluable from a stated absence, whereas silence leaves it to
    # infer a stage from the diagnosis text — which is the weakest of the four
    # tiers, applied without any of the guards the extractor applies.
    stage_ordinal, stage_source = extract_patient_stage_with_source(
        conditions,
        cancer_stage_observations=patient_data.get('cancer_stage_observations') or [],
        cancer_metastasis_observations=patient_data.get('cancer_metastasis_observations') or [],
    )
    if stage_ordinal is not None:
        summary += (f"\n\nCancer Stage: {STAGE_NUMERALS[stage_ordinal]} "
                    f"({_STAGE_SOURCE_PHRASES[stage_source]})\n")
    else:
        summary += "\n\nCancer Stage: not recorded in this record\n"

    # ── Conditions (relevance-filtered) ───────────────────────────────────
    # Tier A (cancer) and Tier B (relevant comorbidities) get full detail.
    # Tier C (background) is summarized in one line to save tokens.
    # Error mode is one-directional: can send slightly more, never less.
    summary += "\n\nConditions:\n"
    unique_conditions = deduplicate_by_display(conditions)

    tier_a = []  # cancer
    tier_b = []  # clinically significant comorbidity
    tier_c = []  # background (confidently irrelevant)

    for condition in unique_conditions:
        tier = _classify_condition_relevance(condition, cancer_registry)
        if tier == "cancer":
            tier_a.append(condition)
        elif tier == "relevant":
            tier_b.append(condition)
        else:
            tier_c.append(condition)

    def _format_condition_line(cond: Dict, tag: str) -> str:
        """Format a single condition for the GPT-4o prompt."""
        display = cond.get("display") or "Unknown condition"
        onset   = cond.get("onset_date") or ""
        year    = onset[:4] if onset and onset != "unknown" else None
        clinical_status = cond.get("clinical_status") or ""

        verification_status = cond.get("verification_status") or ""

        parts = [display]
        if verification_status == "unconfirmed":
            parts.append("unconfirmed")
        elif clinical_status and clinical_status not in ("unknown", ""):
            parts.append(clinical_status)
        if year:
            parts.append(year)
        parts.append(tag)
        return f"- {' | '.join(parts)}"

    def _is_neoplasm_verified(cond: Dict) -> bool:
        """Check if condition maps to MeSH C04 via any available crosswalk."""
        if mesh_filter is None:
            return False
        codings = cond.get("codings", [])
        if codings:
            for c in codings:
                code = c.get("code", "")
                if code in mesh_filter.snomed_to_trees:
                    return True
                if code in mesh_filter.icd10_to_trees:
                    return True
        else:
            code = cond.get("code", "")
            if code in mesh_filter.snomed_to_trees:
                return True
            if code in mesh_filter.icd10_to_trees:
                return True
        return False

    if not unique_conditions:
        summary += "- None\n"
    else:
        # Tier A: cancer conditions (full detail, neoplasm verification tag)
        for cond in tier_a:
            tag = "[neoplasm]" if _is_neoplasm_verified(cond) else "[neoplasm-unverified]"
            summary += _format_condition_line(cond, tag) + "\n"

        # Tier B: clinically significant comorbidities (full detail)
        for cond in tier_b:
            summary += _format_condition_line(cond, "[comorbidity]") + "\n"

        # Tier C: background conditions (one summary line)
        if tier_c:
            other_names = [
                (c.get("display") or "unknown") for c in tier_c
            ]
            preview = ", ".join(other_names[:5])
            remaining = len(other_names) - 5
            if remaining > 0:
                summary += f"- Other conditions ({len(other_names)}): {preview}, +{remaining} more\n"
            elif other_names:
                summary += f"- Other conditions ({len(other_names)}): {preview}\n"

    # ── Medications (relevance-filtered, with status and dates) ────────────
    # Relevant medications (chemo, immunotherapy, anticoagulants, steroids, etc.)
    # get full detail including status and dates. Background medications (OTC,
    # vitamins, eye drops, etc.) are summarized in one line.
    #
    # Status is critical for trial matching:
    #   active/on-hold/unknown → current treatment criteria (met/violated)
    #   completed/stopped      → prior treatment criteria and washout periods
    #
    # Both active and historical medications are included so GPT-4o can evaluate:
    #   - Current treatment exclusions ("no concurrent systemic therapy")
    #   - Prior treatment exclusions ("no prior platinum within 6 months")
    #   - Prior treatment inclusions ("must have received prior chemotherapy")
    summary += "\nMedications:\n"
    unique_meds = medications  # already deduplicated by File 07
    med_relevant = []
    med_background = []

    _ACTIVE_STATUSES = {"active", "on-hold", "draft", "intended", "unknown"}

    for med in unique_meds:
        display = med.get("display")
        if not display:
            continue

        status     = med.get("status", "unknown").lower().strip()
        start_date = med.get("start_date", "unknown")
        end_date   = med.get("end_date", "unknown")

        # Build status label
        if status in _ACTIVE_STATUSES:
            status_label = "active"
        else:
            status_label = status  # completed, stopped, cancelled, etc.

        # Build date string
        date_parts = []
        if start_date and start_date != "unknown":
            date_parts.append(f"start: {start_date[:10]}")
        if end_date and end_date != "unknown":
            date_parts.append(f"end: {end_date[:10]}")
        date_str = f" | {', '.join(date_parts)}" if date_parts else ""

        med_line = f"{display} | status: {status_label}{date_str}"

        tier = _classify_medication_relevance(display)
        if tier == "relevant":
            med_relevant.append(med_line)
        else:
            med_background.append(display)  # background meds: name only, no detail

    if not med_relevant and not med_background:
        summary += "- None\n"
    else:
        for med_line in med_relevant:
            summary += f"- {med_line}\n"

        if med_background:
            preview = ", ".join(med_background[:5])
            remaining = len(med_background) - 5
            if remaining > 0:
                summary += f"- Other medications ({len(med_background)}): {preview}, +{remaining} more\n"
            elif med_background:
                summary += f"- Other medications ({len(med_background)}): {preview}\n"
    
    # ── Allergies ─────────────────────────────────────────────────────────
    # Drug allergies are a common exclusion criterion in oncology trials
    # (e.g., "No known allergy to platinum-based agents", "No history of
    # severe hypersensitivity to monoclonal antibodies"). Providing allergy
    # data converts these criteria from "not_evaluable" to "not_violated"
    # or "violated", improving match accuracy.
    #
    # Only active, non-refuted allergies are included (filtered in parser).
    # Category and criticality are shown when available to help GPT-4o
    # assess severity-gated exclusion criteria.
    summary += "\nAllergies:\n"
    if allergies:
        for allergy in allergies:
            display     = allergy.get("display") or "Unknown allergen"
            category    = allergy.get("category") or ""
            criticality = allergy.get("criticality") or ""

            parts = [display]
            if category and category != "unknown":
                parts.append(category)
            if criticality and criticality != "unknown":
                parts.append(f"criticality: {criticality}")
            summary += f"- {' | '.join(parts)}\n"
    else:
        summary += "- No known allergies\n"
    
    # ── Procedures ────────────────────────────────────────────────────────
    # Procedure types, deduplicated by display name, most recent date per type,
    # MINUS the confidently irrelevant ones. Prior chemotherapy, radiation and
    # surgery are standard eligibility gates; dental cleanings, depression
    # screening questionnaires and discharge paperwork are not, and they were
    # 40-63% of every patient's record text.
    #
    # A DROPPED PROCEDURE LEAVES NO TRACE IN THE TEXT, and that is a decision
    # rather than an omission. A count line ("and 96 others") would spend
    # tokens telling the model about evidence it cannot use and invite it to
    # reason about what it cannot see; a placeholder would do the same for
    # less. The record of what was withheld is PROCEDURE_RENDER_COUNTS, which
    # is where a counter belongs, and the FHIR data is untouched.
    #
    # THE FILTER RUNS AFTER THE DEDUPLICATION, so it decides once per procedure
    # TYPE rather than once per resource, and the counters count the same
    # units the section renders.
    summary += "\nProcedures:\n"
    relevant_procs = []
    for proc in lab_registry.filter_relevant_procedures(procedures):
        if _classify_procedure_relevance(proc) == "background":
            PROCEDURE_RENDER_COUNTS[PROCEDURE_RENDER_DROPPED] += 1
            continue
        PROCEDURE_RENDER_COUNTS[PROCEDURE_RENDER_KEPT] += 1
        relevant_procs.append(proc)
    # UNCHANGED FROM HERE DOWN, including the "- None" arm, which is now also
    # what a patient whose every procedure was dropped renders. That is the
    # right answer for the same reason it is right for a patient with no
    # procedures at all: the section states what is worth stating, and there is
    # nothing.
    if relevant_procs:
        for proc in relevant_procs:
            display  = proc.get("display") or "Unknown procedure"
            date     = proc.get("date") or ""
            date_str = date[:10] if date and date != "unknown" else "date unknown"
            summary += f"- {display} ({date_str})\n"
    else:
        summary += "- None\n"

    # ── Relevant Lab Values ───────────────────────────────────────────────
    # LOINC-filtered: ANC, creatinine, bilirubin, AST/ALT, platelets, etc.
    # One row per lab concept (most recent reading). Routine vitals excluded.
    summary += "\nRelevant Lab Values (most recent):\n"
    relevant_obs = lab_registry.filter_relevant_observations(observations)
    if relevant_obs:
        for obs in relevant_obs:
            canonical = obs.get("canonical_display") or obs.get("display") or "Unknown"
            value     = obs.get("value")
            unit      = obs.get("unit") or ""
            date      = obs.get("date") or ""
            date_str  = date[:10] if date and date != "unknown" else "date unknown"
            value, unit = _normalize_lab_unit(canonical, value, unit)
            unit_str  = f" {unit}" if unit else ""
            summary += f"- {canonical}: {value}{unit_str} ({date_str})\n"
    else:
        summary += "- None\n"

    # ── Genomic & Molecular Biomarkers ────────────────────────────────────
    # Biomarker observations (EGFR, KRAS, ALK, PD-L1, HER2, MSI, TMB, etc.)
    # are NOT in OncologyLabRegistry (which covers organ function labs only).
    # Without this section, GPT-4o has no biomarker data and all mutation/
    # expression criteria return not_evaluable — a major loss of signal for
    # precision oncology trials.
    #
    # Detection uses keyword matching on observation display text, which
    # handles both Synthea (free-text genetic variant strings) and real EHRs
    # (LOINC-coded molecular panels).
    #
    # MATCHED ON WORD BOUNDARIES, not as substrings. Five of these keywords are
    # three-letter gene symbols and two of them fired constantly as substrings
    # of ordinary clinical English. Measured over the 1,000-patient cohort,
    # AFTER the lab-registry skip below:
    #
    #   "ret"  20,127 false matches — "Diabetic retinopathy severity level"
    #                                 (12,560), "Study observation Left/Right
    #                                 retina by OCT" (6,844), "Natriuretic
    #                                 peptide.B prohormone N-Terminal" (323)
    #   "met"   1,908 false matches — "...by High sensitivity method" (739),
    #                                 "Drugs of abuse 5 panel - Urine by Screen
    #                                 method" (408), the four metastasis
    #                                 displays below (701), "Human
    #                                 metapneumovirus RNA" (60)
    #   "alk"       0 — no display in the corpus contains the substring
    #   "msi"       0
    #   "tmb"       0
    #
    # alk, msi and tmb are kept: they never fired here, and deleting a keyword
    # that is genuinely a biomarker because this particular corpus happens not
    # to trip it would trade a false positive for a false negative on the next
    # corpus. The fix belongs at the matching layer, where it protects all five
    # at once, not in the vocabulary. Nothing is removed from this set.
    #
    # The one genuine biomarker match in the corpus, "ERBB2 gene duplication
    # [Presence] in Breast cancer specimen by FISH" (295), is word-bounded and
    # survives unchanged.
    _BIOMARKER_KEYWORDS = frozenset({
        "egfr", "kras", "alk", "ros1", "braf", "her2", "erbb2",
        "met", "ret", "ntrk", "pd-l1", "pdl1", "msi", "tmb",
        "brca", "idh1", "idh2", "pik3ca", "fgfr", "cdkn2a",
        "mutation", "variant", "fusion", "amplification",
        "deletion", "expression", "microsatellite", "tumor mutational",
        "genetic", "genomic", "molecular",
    })

    # One alternation over the keyword set. Boundary is "not a letter or digit"
    # so punctuation and hyphens still delimit: "PD-L1", "MSI-H" and "c-MET"
    # all match; "Generalized", "retinopathy" and "method" do not.
    #
    # This used to carry a _METASTASIS_KEYWORDS carve-out, because 701
    # metastasis observations reached the model only via "met" matching inside
    # "metastases" and word-bounding would have deleted them silently. They now
    # have their own routed list (File 07's _METASTASIS_LOINCS) and their own
    # prompt section, so the carve-out is gone and this set is gene and marker
    # vocabulary again.
    _BIOMARKER_PATTERN = re.compile(
        r"(?<![a-z0-9])(?:"
        + "|".join(sorted((re.escape(k) for k in _BIOMARKER_KEYWORDS),
                          key=len, reverse=True))
        + r")(?![a-z0-9])"
    )

    _BIOMARKER_STRIP_PREFIXES = (
        "genetic variant: ",
        "molecular: ",
        "genomic: ",
        "mutation analysis: ",
        "variant: ",
    )

    biomarker_obs = []
    loinc_filtered_codes = lab_registry.loinc_codes  # avoid re-showing lab obs

    for obs in observations:
        # Skip observations already shown in the lab values section
        if obs.get("code") in loinc_filtered_codes:
            continue
        display = (obs.get("display") or "").strip()
        value   = obs.get("value")
        date    = obs.get("date") or ""

        display_lower = display.lower()
        if not _BIOMARKER_PATTERN.search(display_lower):
            continue

        # Normalize display: strip verbose prefixes
        display_clean = display
        for prefix in _BIOMARKER_STRIP_PREFIXES:
            if display_lower.startswith(prefix):
                display_clean = display[len(prefix):].strip()
                break

        date_str = date[:10] if date and date != "unknown" else "date unknown"

        if value and str(value).strip():
            biomarker_obs.append(f"- {display_clean}: {value} ({date_str})")
        else:
            biomarker_obs.append(f"- {display_clean} ({date_str})")

    # mCODE structured genomic variants (LOINC 69548-6) — real EHR path.
    # These are parsed by _parse_mcode_genomic_variant in File 07 and
    # deduplicated/filtered by OncologyLabRegistry. Rendered first since
    # they are structured and higher-fidelity than keyword-matched obs.
    mcode_variants = lab_registry.filter_relevant_genomic_variants(
        patient_data.get('cancer_genomic_variants') or []
    )
    mcode_lines = []
    for v in mcode_variants:
        display  = v.get('display') or 'Unknown variant'
        date     = v.get('date') or ''
        date_str = date[:10] if date and date != 'unknown' else 'date unknown'
        mcode_lines.append(f"- {display} ({date_str})")

    # ── Metastasis & Nodal Status ─────────────────────────────────────────
    #
    # Its own section, named for what it is. These observations reached the
    # model only because "met" matched inside "metastases" in the biomarker
    # keyword set, which filed disease spread under "Genomic & Molecular
    # Biomarkers" — a section the model is told contains mutation and
    # expression findings. 701 observations across the cohort, on four LOINCs
    # (File 07's _METASTASIS_LOINCS), and nothing else carried them: they are
    # not in OncologyLabRegistry and not in _MCODE_STAGE_LOINCS.
    #
    # The M/N category is printed because it is the distinction that matters to
    # an eligibility criterion: "no distant metastases" is an M question and
    # "N0-N1 only" is an N question, and the display text alone
    # ("Lymph nodes with micrometastases [#] ...") does not say which axis it
    # is on unless the reader already knows the LOINC.
    metastasis_obs = patient_data.get("cancer_metastasis_observations") or []
    summary += "\nMetastasis & Nodal Status:\n"
    if metastasis_obs:
        for obs in metastasis_obs:
            display  = obs.get("display") or "Unknown observation"
            value    = obs.get("value")
            unit     = obs.get("unit") or ""
            category = obs.get("metastasis_category") or "?"
            date     = obs.get("date") or ""
            date_str = date[:10] if date and date != "unknown" else "date unknown"
            unit_str = f" {unit}" if unit else ""
            value_str = (f": {value}{unit_str}"
                         if value is not None and str(value).strip() else "")
            summary += f"- [{category}] {display}{value_str} ({date_str})\n"
    else:
        summary += "- None on record\n"

    summary += "\nGenomic & Molecular Biomarkers:\n"
    if mcode_lines or biomarker_obs:
        for line in mcode_lines:
            summary += line + "\n"
        for line in biomarker_obs:
            summary += line + "\n"
    else:
        summary += "- None on record\n"
        
    return summary.strip()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""
