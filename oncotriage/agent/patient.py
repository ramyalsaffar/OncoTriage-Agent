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
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from oncotriage.agent import deps
from oncotriage.agent.state import GENOMIC_VARIANT_LOINC, _VARIANT_TEXT_PATTERN
from oncotriage.config import MAX_VARIANT_TERMS
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
    
    Captures the exact patient record state at inference time. Two inferences
    with the same hash are guaranteed to have identical input data, making
    score/eligibility differences attributable solely to GPT-4o non-determinism.
    
    Hash inputs (order-stable):
      - demographics: birth_date, sex, race, ethnicity
      - conditions: sorted by (display, onset_date)
      - medications: sorted by display
      - observations: sorted by (display, date)
      - procedures: sorted by (display, date)
      - ecog: value, date, shape and count — emitted ONLY when the bundle
        carried at least one ECOG observation (see below)
    """

    demographics = patient_data.get("demographics", {})
    conditions = patient_data.get("conditions", [])
    medications = patient_data.get("medications", [])
    observations = patient_data.get("observations", [])
    procedures = patient_data.get("procedures", [])
    ecog = patient_data.get("ecog_performance_status") or {}
    
    # Build deterministic string representation
    parts = []
    
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
    
    # Conditions (sorted for determinism)
    sorted_conds = sorted(conditions, key=lambda c: (c.get('display', ''), c.get('onset_date', '')))
    for c in sorted_conds:
        parts.append(f"cond={c.get('display', '')}|{c.get('onset_date', '')}|{c.get('clinical_status', '')}")
    
    # Medications (sorted, deduplicated by display)
    sorted_meds = sorted(set(m.get('display', '') for m in medications))
    for m in sorted_meds:
        parts.append(f"med={m}")
    
    # Observations (sorted)
    sorted_obs = sorted(observations, key=lambda o: (o.get('display', ''), o.get('date', '')))
    for o in sorted_obs:
        parts.append(f"obs={o.get('display', '')}|{o.get('value', '')}|{o.get('unit', '')}|{o.get('date', '')}")
    
    # Procedures (sorted)
    sorted_procs = sorted(procedures, key=lambda p: (p.get('display', ''), p.get('date', '')))
    for p in sorted_procs:
        parts.append(f"proc={p.get('display', '')}|{p.get('date', '')}")

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
    for m in sorted(metastasis, key=lambda o: (o.get("display", ""),
                                               o.get("date", ""))):
        parts.append(
            f"met={m.get('display', '')}"
            f"|{m.get('value', '')}"
            f"|{m.get('unit', '')}"
            f"|{m.get('date', '')}"
            f"|{m.get('metastasis_category', '')}"
        )

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


def _normalize_lab_unit(
    canonical_display: str,
    value: Any,
    unit: Optional[str],
) -> Tuple[Any, Optional[str]]:
    """
    Normalize a lab value+unit to canonical US clinical units if a conversion
    exists in _LAB_UNIT_CONVERSIONS. Returns original (value, unit) otherwise.
    Never raises.
    """
    if value is None or unit is None:
        return value, unit
    try:
        display_key = canonical_display.lower().strip()
        unit_key    = unit.lower().strip().replace(" ", "")
        for (disp, u), (target_unit, convert_fn) in _LAB_UNIT_CONVERSIONS.items():
            if disp in display_key and u == unit_key:
                return convert_fn(float(value)), target_unit
    except Exception:
        pass
    return value, unit


#------------------------------------------------------------------------------


def _create_patient_summary(patient_data: Dict) -> str:
    """
    Create compact patient summary for GPT-4o criterion-level evaluation.

    Sections:
      Demographics      : age, sex, race, ethnicity
      Performance Status: ECOG (LOINC 89247-1), or an explicit statement that
                          none is recorded. Never defaults to 0.
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
    # All procedure types, deduplicated by display name, most recent date per type.
    # Prior chemotherapy, radiation, and surgery are standard eligibility gates.
    summary += "\nProcedures:\n"
    relevant_procs = lab_registry.filter_relevant_procedures(procedures)
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
