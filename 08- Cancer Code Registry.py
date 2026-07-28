# Cancer Code Registry
######################


#------------------------------------------------------------------------------


"""
cancer_code_registry.py (layman term explanation)
=================================================
Identifies the primary cancer diagnosis from a patient's full condition list.

The problem this solves: FHIR patient records list conditions in encounter order,
not clinical significance. Obesity or hypertension can appear before the actual
cancer. This module finds the real primary cancer reliably.

How it works — three detection layers in priority order:
  1. SNOMED CT exact match  : 52 curated codes covering all Synthea cancer modules
                              + mCODE STU4 root codes
  2. ICD-10-CM exact match  : Complete 2024 release (1,609 primary cancer codes)
                              loaded at startup from the icd10-cm package.
                              Handles both dot-formatted (C34.10) and
                              dot-free (C3410) codes in O(1) lookup.
  3. Display term fallback  : Only fires when code is missing/unknown.
                              Matches on definitional morphology terms
                              (carcinoma, lymphoma, leukemia, etc.).
                              Rejects secondary/metastatic display terms.

Secondary cancers (metastases) are excluded at every layer before matching.
Diagnoses marked refuted or entered-in-error are filtered out upstream
in the LangGraph agent before this module is called.

When multiple cancer conditions exist, a tiebreaker sorts by:
  confirmed > unconfirmed → active > remission → most recent onset date

Standard: mCODE STU4 (HL7) + ICD-10-CM 2024 (CMS)
Dependency: pip install icd10-cm
Works with: Synthea FHIR bundles (SNOMED) + real EHR data (ICD-10)
"""


"""
cancer_code_registry.py
=======================
Production-grade clinical terminology registry.

Contains two registries:

  1. CancerCodeRegistry
     Identifies the primary cancer diagnosis from a patient's full condition
     list. FHIR bundles list conditions in encounter order, not clinical
     significance. This registry finds the real primary cancer reliably.

     Three-layer detection:
       Layer 1 — SNOMED CT exact match   (52 codes, Synthea + mCODE roots)
       Layer 2 — ICD-10-CM exact match   (1,670 codes, full 2024 release, O(1))
       Layer 3 — Display term fallback   (only when code is absent/unknown)

     Secondary cancers (metastases) are excluded at every layer.
     When multiple cancer conditions exist, tiebreaker sorts by:
       confirmed > unconfirmed → active > remission → most recent onset date

     Standard: mCODE STU4 (HL7) + ICD-10-CM 2024 (CMS)
     Works with: Synthea FHIR (SNOMED) + real EHR data (ICD-10)
     Dependency: pip install icd10-cm

  2. OncologyLabRegistry
     Filters patient observations down to the labs and vitals that oncology
     trials actually gate on (organ function, blood counts, tumor markers).

     Uses LOINC codes — the HL7-mandated standard for lab observations used
     by both Synthea and real EHRs. LOINC codes are permanent international
     identifiers; display names are institution-specific and unreliable.

     For each relevant lab, keeps only the most recent value — trial
     eligibility criteria check current organ function, not history.
     When multiple LOINC codes map to the same lab concept (e.g. creatinine
     in serum vs blood), only the most recent reading across all variants is
     kept, preventing duplicate rows in the GPT-4o prompt.

     Standard: LOINC (Regenstrief Institute)
     Works with: Synthea FHIR (LOINC) + real EHR data (LOINC)

Usage:
    from cancer_code_registry import load_registry, load_lab_registry

    cancer_registry = load_registry()       # CancerCodeRegistry singleton
    lab_registry    = load_lab_registry()   # OncologyLabRegistry singleton
"""

logger = logging.getLogger(__name__)


# ===========================================================================
# SECTION 1 — CANCER CODE REGISTRY
# ===========================================================================

# ---------------------------------------------------------------------------
# SNOMED CT curated primary cancer codes
# ---------------------------------------------------------------------------
# mCODE defines primary cancer as descendants of SNOMED 363346000
# (Malignant neoplastic disease), EXCLUDING descendants of 128462008
# (Secondary malignant neoplastic disease).
#
# Full SNOMED hierarchy traversal requires a live terminology server with a
# UMLS license. Without it, we use a curated set covering all Synthea cancer
# module codes (verified from Synthea GitHub + Google Cloud FHIR lab) plus
# mCODE root codes and common real-EHR SNOMED codes.
# The ICD-10-CM layer covers real EHR data exhaustively (1,609 codes).

_SNOMED_PRIMARY: FrozenSet[str] = frozenset({
    # ── Lung (Synthea lung_cancer.json)
    "254637007",   # Non-small cell carcinoma of lung  ← Synthea canonical
    "424132000",   # Non-small cell lung cancer, NOS
    "413448000",   # Adenocarcinoma of lung
    "363358000",   # Malignant tumor of lung
    "254632001",   # Small cell carcinoma of lung  ← Synthea canonical (confirmed MalaCards/Orphanet)
    "1285369004",  # Small cell carcinoma of lung  ← SNOMED CT newer code
    "67811000119102",  # Primary small cell malignant neoplasm of lung, TNM stage 1
    "408512008",   # Small cell carcinoma of lung, limited stage
    "408513003",   # Small cell carcinoma of lung, extensive stage
    # ── Breast
    "254837009",   # Malignant neoplasm of breast
    "372064008",   # Malignant neoplasm of female breast
    "408643008",   # Infiltrating duct carcinoma of breast
    "372098003",   # Malignant neoplasm of male breast
    "109375007",   # Carcinoma of breast, Stage 1
    "109376008",   # Carcinoma of breast, Stage 2
    "109377004",   # Carcinoma of breast, Stage 3
    "109378009",   # Carcinoma of breast, Stage 4
    # ── Colorectal (Synthea colorectal_cancer.json)
    "363406005",   # Malignant neoplasm of colon
    "363414004",   # Malignant neoplasm of rectum
    "109841005",   # Carcinoma of colon
    "109838007",   # Overlapping malignant neoplasm of colon
    "363415003",   # Malignant neoplasm of rectosigmoid junction
    # ── Prostate
    "399068003",   # Malignant tumor of prostate
    "126906006",   # Neoplasm of prostate
    # ── Pancreas
    "363418001",   # Malignant neoplasm of pancreas
    "372003004",   # Malignant tumor of pancreas
    # ── Ovarian / gynecologic
    "363443007",   # Malignant tumor of ovary
    "254907004",   # Serous cystadenocarcinoma of ovary
    "363458008",   # Malignant tumor of uterus
    "372016000",   # Malignant tumor of cervix
    # ── Hematologic
    "91861009",    # Acute myeloid leukemia
    "413522009",   # Acute lymphoblastic leukemia
    "92814006",    # Chronic lymphocytic leukemia
    "92818009",    # Chronic myeloid leukemia
    "109989006",   # Multiple myeloma
    "118600007",   # Malignant lymphoma
    "82591004",    # Hodgkins disease
    "20312006",    # Diffuse non-Hodgkins lymphoma
    # ── Skin / melanoma
    "372244006",   # Malignant melanoma
    "254654002",   # Malignant melanoma of skin
    # ── Liver
    "109840006",   # Carcinoma of liver
    # ── Kidney / urinary
    "363516000",   # Malignant tumor of kidney
    "363518004",   # Malignant tumor of bladder
    # ── Thyroid
    "363478007",   # Malignant neoplasm of thyroid
    # ── CNS
    "393563007",   # Malignant neoplasm of brain
    "41656004",    # Glioblastoma multiforme
    "126952004",   # Neoplasm of brain
    # ── Head and neck
    "363400000",   # Malignant neoplasm of oropharynx
    "363399004",   # Malignant neoplasm of oral cavity
    # ── Testicular
    "363512001",   # Malignant neoplasm of testis
    # ── mCODE root codes
    "363346000",   # Malignant neoplastic disease  ← mCODE SNOMED root
    "93761005",    # Primary malignant neoplasm
    "415068001",   # Primary malignant neoplasm of body
})

# SNOMED secondary/metastatic — excluded from primary selection.
# Per mCODE: exclude all descendants of 128462008.
_SNOMED_SECONDARY: FrozenSet[str] = frozenset({
    "128462008",   # Secondary malignant neoplastic disease (mCODE exclusion root)
    "94260004",    # Secondary malignant neoplasm of colon
    "94222008",    # Secondary malignant neoplasm of liver
    "94225005",    # Secondary malignant neoplasm of lung
    "94229004",    # Secondary malignant neoplasm of brain
    "315006",      # Secondary malignant neoplasm of bone
})

# Display-term fallback — fires ONLY when code is missing/unknown.
# Morphological terms present in every coding system's cancer labels.
_CANCER_DISPLAY_TERMS: Tuple[str, ...] = (
    "carcinoma", "adenocarcinoma", "cancer", "malignant", "malignancy",
    "neoplasm", "lymphoma", "leukemia", "leukaemia", "melanoma",
    "sarcoma", "myeloma", "glioma", "glioblastoma", "mesothelioma",
    "blastoma",
)

# verificationStatus values that mean the diagnosis is retracted or erroneous.
_EXCLUDE_VERIFICATION: FrozenSet[str] = frozenset({"refuted", "entered-in-error"})

# clinical_status priority for tiebreaking (lower = higher priority).
_CLINICAL_STATUS_PRIORITY: Dict[str, int] = {
    "active": 0, "recurrence": 1, "relapse": 2, "remission": 3,
    "inactive": 4, "resolved": 5, "unknown": 6,
}

# ICD-10-CM alpha-suffix codes that cannot be parsed by int(c[1:3]).
# C4A — Merkel cell carcinoma        → PRIMARY   (33 codes)
# C7A — Malignant carcinoid tumors   → PRIMARY   (28 codes)
# C7B — Secondary neuroendocrine     → SECONDARY (10 codes)
_ICD10_ALPHA_PRIMARY: FrozenSet[str] = frozenset({"C4A", "C7A"})
_ICD10_ALPHA_SECONDARY: FrozenSet[str] = frozenset({"C7B"})


def _build_icd10_cancer_sets() -> Tuple[Set[str], Set[str]]:
    """
    Build ICD-10-CM primary and secondary cancer code sets from the
    full icd10-cm 2024 release (95,622 codes, no external API).

    Primary (per mCODE STU4):
        C00-C76  malignant neoplasms of primary sites
        C80-C96  malignant neoplasms without site / hematologic
        C4A      Merkel cell carcinoma  (alpha-suffix)
        C7A      Malignant carcinoid tumors  (alpha-suffix)
        D00-D09  in-situ neoplasms
        D37-D48  neoplasms of uncertain behavior

    Secondary (excluded from primary selection):
        C77-C79  secondary malignant neoplasms of lymph nodes / distant sites
        C7B      secondary neuroendocrine tumors  (alpha-suffix)
    """
    try:
        import icd10
    except ImportError:
        logger.error(
            "icd10-cm package not installed. Run: pip install icd10-cm\n"
            "ICD-10 layer will be empty; only SNOMED and display fallback active."
        )
        return set(), set()

    primary: Set[str] = set()
    secondary: Set[str] = set()

    for code_str in icd10.codes:
        c = code_str.upper().replace(".", "")

        if c and c[0] == "C" and len(c) >= 3:
            prefix3 = c[:3]
            if prefix3 in _ICD10_ALPHA_PRIMARY:
                primary.add(code_str)
                continue
            if prefix3 in _ICD10_ALPHA_SECONDARY:
                secondary.add(code_str)
                continue
            try:
                num = int(c[1:3])
                if 0 <= num <= 96:
                    if 77 <= num <= 79:
                        secondary.add(code_str)
                    else:
                        primary.add(code_str)
            except ValueError:
                logger.debug(f"Skipping unrecognized ICD-10 C-code: {code_str!r}")

        elif c and c[0] == "D" and len(c) >= 3:
            try:
                num = int(c[1:3])
                if 0 <= num <= 9:
                    primary.add(code_str)
                elif 37 <= num <= 48:
                    primary.add(code_str)
            except ValueError:
                logger.debug(f"Skipping unrecognized ICD-10 D-code: {code_str!r}")

    logger.info(
        f"ICD-10-CM loaded: {len(primary)} primary, {len(secondary)} secondary codes."
    )
    return primary, secondary


class CancerCodeRegistry:
    """
    Immutable cancer code registry. Built once at startup. Thread-safe.

    Identifies the primary cancer diagnosis from a FHIR condition list using
    a three-layer detection system: SNOMED → ICD-10-CM → display fallback.
    Secondary/metastatic codes are excluded at every layer.
    """

    def __init__(self):
        icd10_primary_raw, icd10_secondary_raw = _build_icd10_cancer_sets()

        self.snomed_primary: FrozenSet[str] = _SNOMED_PRIMARY
        self.snomed_secondary: FrozenSet[str] = _SNOMED_SECONDARY
        self.display_terms: Tuple[str, ...] = _CANCER_DISPLAY_TERMS
        self.exclude_verification: FrozenSet[str] = _EXCLUDE_VERIFICATION
        self.clinical_status_priority: Dict[str, int] = _CLINICAL_STATUS_PRIORITY

        # Pre-normalized O(1) lookup sets.
        # Both "C34.10" and "C3410" normalize to "C3410" → same bucket.
        self._icd10_primary_norm: FrozenSet[str] = frozenset(
            c.upper().replace(".", "") for c in icd10_primary_raw
        )
        self._icd10_secondary_norm: FrozenSet[str] = frozenset(
            c.upper().replace(".", "") for c in icd10_secondary_raw
        )

        logger.info(
            f"CancerCodeRegistry ready: {len(self.snomed_primary)} SNOMED + "
            f"{len(self._icd10_primary_norm)} ICD-10-CM primary codes indexed."
        )


    def is_primary_cancer(self, condition: Dict) -> bool:
        """
        Return True if this parsed FHIR condition represents a primary cancer.

        Multi-coding aware: checks ALL codings from the FHIR condition, not just
        the best-selected code. A condition is primary cancer if ANY coding matches
        a primary cancer set and NONE of its codings match a secondary set.

        Detection layers (applied per coding):
          Layer 1 -- SNOMED exact match   : Synthea + SNOMED-coded real EHRs
          Layer 2 -- ICD-10-CM match      : real EHRs (handles with/without dots)

        Display fallback (Layer 3): fires only when ALL codings are absent,
        unknown, or unrecognized. Uses morphology terms in display text.

        Secondary/metastatic codes are hard-excluded: if ANY coding matches a
        secondary set, the condition is rejected regardless of other codings.

        Backward compatible: if "codings" key is absent (older parsed data),
        falls back to single "code" field with original behavior.
        """
        codings = condition.get("codings", [])
        display = (condition.get("display") or "").lower().strip()

        # If no multi-coding data, fall back to single code field (backward compat)
        if not codings:
            code = (condition.get("code") or "").strip()
            codings = [{"system_key": "unknown", "code": code, "display": display}]

        # Pass 1: Hard exclude if ANY coding is secondary/metastatic.
        # A single secondary code means the condition is a metastasis,
        # even if another coding maps to a primary site.
        for c in codings:
            c_code = (c.get("code") or "").strip()
            c_norm = c_code.upper().replace(".", "")
            if c_norm in self._icd10_secondary_norm:
                return False
            if c_code in self.snomed_secondary:
                return False

        # Pass 2: Check if ANY coding matches a primary cancer set.
        has_recognized_code = False
        for c in codings:
            c_code = (c.get("code") or "").strip()
            c_norm = c_code.upper().replace(".", "")

            if not c_code or c_code.lower() in ("unknown", "none"):
                continue

            has_recognized_code = True

            # Layer 1: SNOMED exact match
            if c_code in self.snomed_primary:
                return True

            # Layer 2: ICD-10-CM normalized match
            if c_norm in self._icd10_primary_norm:
                return True

        # Pass 3: Display fallback -- only when no coding was recognized
        if not has_recognized_code:
            _secondary_terms = ("metastatic", "metastasis", "secondary")
            if any(sec in display for sec in _secondary_terms):
                return False
            if any(term in display for term in self.display_terms):
                return True

        return False
    

    def sort_key(self, condition: Dict) -> Tuple:
        """
        Tiebreaker sort key. Sort ascending — lowest tuple wins.

          1. verificationStatus: confirmed=0, other=1
          2. clinical_status:    active=0 ... unknown=6
          3. onset_date:         most recent first; missing last

        Usage:
            primary = sorted(cancer_conditions, key=registry.sort_key)[0]
        """
        ver = (condition.get("verification_status") or "unknown").lower()
        clin = (condition.get("clinical_status") or "unknown").lower()
        onset = condition.get("onset_date") or ""

        return (
            0 if ver == "confirmed" else 1,
            self.clinical_status_priority.get(clin, 6),
            self._invert_date(onset),
        )

    @staticmethod
    def _invert_date(onset: str) -> str:
        """
        Invert date string so ascending sort yields most-recent-first.
        Missing or unparseable dates sort last ('9999-99-99').
        """
        if not onset or onset == "unknown":
            return "9999-99-99"
        try:
            parts = onset[:10].split("-")
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            return f"{9999 - y:04d}-{99 - m:02d}-{99 - d:02d}"
        except (ValueError, IndexError):
            return "9999-99-99"


# ===========================================================================
# SECTION 2 — ONCOLOGY LAB REGISTRY
# ===========================================================================

# ---------------------------------------------------------------------------
# LOINC codes for oncology-trial-relevant observations
# ---------------------------------------------------------------------------
# LOINC (Logical Observation Identifiers Names and Codes) is the HL7-mandated
# international standard for lab and vital observations. Both Synthea and real
# EHRs use LOINC codes for observations. LOINC codes are permanent — once
# assigned, a code never changes meaning (Regenstrief Institute guarantee).
#
# This set covers every lab category that oncology trials gate on:
#   Hematologic  : ANC, WBC, platelets, hemoglobin, lymphocytes
#   Renal        : creatinine, GFR
#   Hepatic      : bilirubin, AST, ALT, alkaline phosphatase
#   Cardiac      : LVEF, QTc
#   Tumor markers: PSA, CA-125, CEA, AFP, CA 19-9, LDH
#   Other        : ECOG, INR, albumin
#
# Source: LOINC database (loinc.org), verified against Synthea value sets
# and common real-EHR oncology lab panels.
#
# Several labs have multiple LOINC codes for the same clinical concept
# (e.g. creatinine in serum vs blood, MDRD vs CKD-EPI eGFR). All variants
# are included to maximize coverage. Deduplication by canonical name in
# filter_relevant_observations() ensures only one row per lab reaches GPT-4o.

# Maps LOINC code -> canonical display name used in the GPT-4o prompt.
# The display name overrides the institution-specific EHR display name,
# giving GPT-4o consistent terminology regardless of data source.
_ONCOLOGY_LOINC: Dict[str, str] = {
    # ── Hematologic
    "26499-4":  "Neutrophils (ANC)",    # Absolute Neutrophil Count — primary LOINC
    "751-8":    "Neutrophils (ANC)",    # ANC — alternate LOINC (some systems use this)
    "6690-2":   "WBC",
    "777-3":    "Platelets",
    "718-7":    "Hemoglobin",
    "731-0":    "Lymphocytes",
    # ── Renal
    "2160-0":   "Creatinine",           # Creatinine in Serum or Plasma — primary LOINC
    "38483-4":  "Creatinine",           # Creatinine in Blood — alternate LOINC
    "33914-3":  "GFR",                  # eGFR — MDRD equation
    "62238-1":  "GFR",                  # eGFR — CKD-EPI equation (newer standard)
    # ── Hepatic
    "1975-2":   "Bilirubin (total)",
    "14629-0":  "Bilirubin (direct)",
    "1920-8":   "AST",
    "1742-6":   "ALT",
    "6768-6":   "Alkaline Phosphatase",
    # ── Cardiac
    "18041-2":  "LVEF",                 # Left Ventricular Ejection Fraction by US
    "77021-7":  "QTc interval",
    # ── Tumor markers
    "2857-1":   "PSA",                  # Prostate-Specific Antigen
    "10334-1":  "CA-125",              # Ovarian cancer marker
    "85319-2":  "CEA",                  # Colorectal cancer marker
    "1834-1":   "AFP",                  # Liver/testicular cancer marker
    "24108-3":  "CA 19-9",             # Pancreatic cancer marker
    "2532-0":   "LDH",                  # Lymphoma/melanoma marker
    # ── Performance / coagulation / nutrition
    "89243-0":  "ECOG Performance Status",
    "6301-6":   "INR",                  # INR — Platelet poor plasma
    "34714-6":  "INR",                  # INR — whole blood (alternate LOINC)
    "1751-7":   "Albumin",
}

# Pre-built frozenset for O(1) membership test.
_ONCOLOGY_LOINC_CODES: FrozenSet[str] = frozenset(_ONCOLOGY_LOINC.keys())

# Canonical display → position index for clinical category sort order.
# Built once at module load. Controls output order in GPT-4o prompt:
# hematologic first, tumor markers last (matching clinical report convention).
_CANONICAL_ORDER: Dict[str, int] = {}
_seen_canonical: set = set()

for _idx, (_code, _name) in enumerate(_ONCOLOGY_LOINC.items()):
    if _name not in _seen_canonical:
        _CANONICAL_ORDER[_name] = len(_seen_canonical)
        _seen_canonical.add(_name)

for _var in ('_idx', '_code', '_name', '_seen_canonical'):
    globals().pop(_var, None)


class OncologyLabRegistry:
    """
    Immutable LOINC-based filter for oncology-relevant lab observations.
    Built once at startup. Thread-safe.

    Filters a patient's full observation list (potentially hundreds of routine
    vitals and labs) down to the subset that oncology trials actually gate on.

    Two-stage deduplication:
      Stage 1 — Per LOINC code: keep most recent observation per code.
      Stage 2 — Per canonical name: when multiple LOINC codes map to the same
                lab concept (e.g. creatinine in serum vs blood), keep only the
                most recent across all variants. Prevents duplicate rows in the
                GPT-4o prompt.

    Result: at most one row per canonical lab name, most recent value.
    """

    def __init__(self):
        self.loinc_codes: FrozenSet[str] = _ONCOLOGY_LOINC_CODES
        self.loinc_display: Dict[str, str] = _ONCOLOGY_LOINC
        self.canonical_order: Dict[str, int] = _CANONICAL_ORDER
        logger.info(
            f"OncologyLabRegistry ready: {len(self.loinc_codes)} LOINC codes "
            f"({len(self.canonical_order)} unique lab concepts) across 7 clinical categories."
        )

    @staticmethod
    def _date_sort_key(date_str: Optional[str]) -> str:
        """
        Sort key for ISO 8601 date strings. Descending sort = most recent first.
        Handles full datetime strings (2023-11-01T10:30:00+00:00) by taking
        only the YYYY-MM-DD prefix. Missing or unknown dates sort last.
        """
        if not date_str or date_str == "unknown":
            return "0000-00-00"
        return date_str[:10]   # YYYY-MM-DD prefix — lexicographic sort is correct for ISO dates

    def filter_relevant_observations(self, observations: List[Dict]) -> List[Dict]:
        """
        Filter and deduplicate observations to oncology-relevant labs.

        Stage 1: Filter to LOINC codes in the oncology set; skip any observation
                 with no value (useless for threshold comparison).
        Stage 2: Per LOINC code — keep the most recent observation only.
        Stage 3: Per canonical name — when alternate LOINC codes exist for the
                 same lab concept, keep only the most recent across all variants.
        Stage 4: Sort by clinical category order (hematologic first, tumor markers last).

        Args:
            observations: list of dicts from _parse_observation() in 07-_FHIR_Parser.py
                          Keys: code (str), display (str), value (int|float|str|None),
                                unit (str|None), date (str|None)

        Returns:
            List of observation dicts, each with an added 'canonical_display' key.
            At most one entry per canonical lab name. Empty list if no relevant
            observations found.
        """
        if not observations:
            return []

        # Stage 1+2: filter relevant, group by LOINC code, keep most recent per code
        grouped: Dict[str, List[Dict]] = defaultdict(list)
        for obs in observations:
            code = (obs.get("code") or "").strip()
            if code in self.loinc_codes and obs.get("value") is not None:
                grouped[code].append(obs)

        if not grouped:
            return []

        per_loinc: List[Dict] = []
        for code, obs_list in grouped.items():
            most_recent = sorted(
                obs_list,
                key=lambda x: self._date_sort_key(x.get("date")),
                reverse=True
            )[0]
            enriched = dict(most_recent)
            enriched["canonical_display"] = self.loinc_display[code]
            per_loinc.append(enriched)

        # Stage 3: dedup per canonical name — keep most recent across all LOINC variants
        # Example: patient has both 2160-0 and 38483-4 (both = "Creatinine").
        # Without this step, GPT-4o would see two "Creatinine" rows.
        by_canonical: Dict[str, List[Dict]] = defaultdict(list)
        for obs in per_loinc:
            by_canonical[obs["canonical_display"]].append(obs)

        result: List[Dict] = []
        for obs_list in by_canonical.values():
            best = sorted(
                obs_list,
                key=lambda x: self._date_sort_key(x.get("date")),
                reverse=True
            )[0]
            result.append(best)

        # Stage 4: sort by clinical category order
        result.sort(
            key=lambda x: self.canonical_order.get(x.get("canonical_display", ""), 999)
        )

        return result


    def filter_relevant_genomic_variants(
            self,
            cancer_genomic_variants: List[Dict],
        ) -> List[Dict]:
        """
        Filter and deduplicate mCODE genomic variant Observations.

        Deduplication: per gene symbol, keep most recent observation only.
        Multiple sequencing runs on the same gene are common after treatment;
        most recent result is the clinically relevant one.

        Filters out Absent/Negative results — these are noise for trial
        matching. Trials gate on the PRESENCE of a biomarker, not its absence.
        Absent results are still handled by Rule 6 (not_evaluable default).

        Args:
            cancer_genomic_variants: list of dicts from _parse_mcode_genomic_variant()

        Returns:
            Deduplicated list, one entry per gene symbol, most recent,
            presence-only. Empty list if no relevant variants found.
        """
        if not cancer_genomic_variants:
            return []

        _ABSENT_VALUES = frozenset({
            "absent", "negative", "not detected", "not found",
            "wild type", "wild-type", "no mutation detected",
        })

        # Filter to present/positive results only
        present = []
        for v in cancer_genomic_variants:
            result = (v.get('result_value') or v.get('interpretation') or '').lower().strip()
            if result and result in _ABSENT_VALUES:
                continue
            present.append(v)

        if not present:
            return []

        # Deduplicate by gene symbol — keep most recent per gene
        by_gene: Dict[str, List[Dict]] = defaultdict(list)
        for v in present:
            gene = (v.get('gene_symbol') or 'unknown').strip()
            by_gene[gene].append(v)

        result_list: List[Dict] = []
        for _, variants in by_gene.items():
            most_recent = sorted(
                variants,
                key=lambda x: self._date_sort_key(x.get('date')),
                reverse=True
            )[0]
            result_list.append(most_recent)

        return result_list


    def filter_relevant_procedures(self, procedures: List[Dict]) -> List[Dict]:
        """
        Deduplicate procedures by display name, keeping the most recent
        occurrence of each procedure type.

        All procedures are clinically relevant for oncology trials (prior
        chemotherapy, radiation, surgery are standard eligibility gates).
        There is no LOINC filter — all procedure types are kept.

        Args:
            procedures: list of dicts from _parse_procedure() in 07-_FHIR_Parser.py
                        Keys: code (str), display (str), date (str|None), status (str)

        Returns:
            List of unique procedure dicts (one per display name), most recent
            date per type. Sorted most-recent-first. Procedures with missing or
            'unknown' display are excluded. Empty list if no valid procedures.
        """
        if not procedures:
            return []

        # Group by normalized display name
        grouped: Dict[str, List[Dict]] = defaultdict(list)
        for proc in procedures:
            display = (proc.get("display") or "").strip()
            if display and display.lower() != "unknown":
                grouped[display.lower()].append(proc)

        if not grouped:
            return []

        # Keep most recent per procedure type
        result: List[Dict] = []
        for _, proc_list in grouped.items():
            most_recent = sorted(
                proc_list,
                key=lambda x: self._date_sort_key(x.get("date")),
                reverse=True
            )[0]
            result.append(most_recent)

        # Sort most-recent-first
        result.sort(
            key=lambda x: self._date_sort_key(x.get("date")),
            reverse=True
        )
        return result


# ===========================================================================
# SECTION 3 — SINGLETONS
# ===========================================================================

_REGISTRY: Optional[CancerCodeRegistry] = None
_LAB_REGISTRY: Optional[OncologyLabRegistry] = None
_REGISTRY_LOCK = threading.Lock()

def load_registry() -> CancerCodeRegistry:
    """
    Return the singleton CancerCodeRegistry, building it on first call.
    Thread-safe: registry is immutable after construction.
    """
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = CancerCodeRegistry()
    return _REGISTRY


def load_lab_registry() -> OncologyLabRegistry:
    """
    Return the singleton OncologyLabRegistry, building it on first call.
    Thread-safe: registry is immutable after construction.
    """
    global _LAB_REGISTRY
    with _REGISTRY_LOCK:
        if _LAB_REGISTRY is None:
            _LAB_REGISTRY = OncologyLabRegistry()
    return _LAB_REGISTRY


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 19 13:01:00 2026

@author: ramyalsaffar
"""

