# FHIR Parser for Patients Data
###############################

"""
FHIR Patient Parser
Extracts structured patient data from Synthea FHIR bundles
"""


#------------------------------------------------------------------------------


# Clinical status priority for condition deduplication.
# Lower number = higher priority = kept when display name duplicates exist.
# active/recurrence/relapse are the most clinically relevant for trial matching.
_CONDITION_STATUS_PRIORITY = {
    "active":     0,
    "recurrence": 0,
    "relapse":    0,
    "remission":  1,
    "inactive":   2,
    "resolved":   2,
    "unknown":    3,
}


#------------------------------------------------------------------------------


# All medication statuses passed through to GPT-4o with explicit status labels.
# Active medications: GPT-4o treats as current.
# Historical medications (completed, stopped): GPT-4o uses for prior treatment
# criteria (e.g., "prior exposure to X", "no platinum within 6 months").
# Removing historical medications would cause all prior treatment criteria
# to return not_evaluable — a significant loss of matching signal.
_ACTIVE_MED_STATUSES          = frozenset({"active", "on-hold", "draft", "intended", "unknown"})
_HISTORICAL_MED_STATUSES      = frozenset({"completed", "stopped", "ended", "cancelled", "not-taken"})

_ACTIVE_ALLERGY_STATUSES      = frozenset({"active", "unknown"})
_EXCLUDE_ALLERGY_VERIFICATION = frozenset({"refuted", "entered-in-error"})

_EXCLUDE_OBS_STATUSES         = frozenset({"entered-in-error", "cancelled", "preliminary"})
_EXCLUDE_PROC_STATUSES        = frozenset({"entered-in-error", "not-done"})


#------------------------------------------------------------------------------


def _condition_sort_key(condition: Dict):
    """
    Sort key for condition deduplication pre-sort.

    Primary: clinical_status priority (active=0 first).
    Secondary: onset_date descending (most recent first).
    Unknown dates sort last within their status group.

    date_key is always a tuple to avoid TypeError when Python compares
    conditions with known dates against conditions with unknown dates.
    Known dates use (0, <negated chars>) so they sort before unknown.
    Unknown dates use (1,) which sorts after all known-date tuples.
    """
    status   = condition.get("clinical_status", "unknown")
    priority = _CONDITION_STATUS_PRIORITY.get(status, 3)

    onset = condition.get("onset_date") or "unknown"
    if onset == "unknown":
        # (1,) sorts after all known-date tuples (0, ...)
        date_key = (1,)
    else:
        # Prepend 0 so known dates always precede unknown within same group.
        # Negate character codes to achieve descending date order.
        date_key = (0,) + tuple(-ord(ch) for ch in onset[:10])

    return (priority, date_key)


# ---------------------------------------------------------------------------
# FHIR Coding System URIs (HL7 standard identifiers)
# ---------------------------------------------------------------------------
# Used by _select_best_coding() to identify which code system a coding belongs
# to. Real EHRs may use either the canonical URI or an OID-based URI for the
# same system. Both forms are mapped to a short canonical key.
#
# Sources:
#   SNOMED CT:  https://terminology.hl7.org/CodeSystem-v3-snomed-CT.html
#   ICD-10-CM:  https://terminology.hl7.org/CodeSystem-icd10CM.html
#   LOINC:      https://terminology.hl7.org/CodeSystem-v2-0396.html
#   RxNorm:     https://terminology.hl7.org/CodeSystem-v3-rxNorm.html
#   CPT:        https://terminology.hl7.org/CodeSystem-CPT.html
#   HCPCS:      https://terminology.hl7.org/CodeSystem-HCPCS.html

_SYSTEM_URI_TO_KEY: Dict[str, str] = {
    # SNOMED CT
    "http://snomed.info/sct":                       "snomed",
    "urn:oid:2.16.840.1.113883.6.96":               "snomed",
    # ICD-10-CM (US clinical modification)
    "http://hl7.org/fhir/sid/icd-10-cm":            "icd10cm",
    "urn:oid:2.16.840.1.113883.6.90":               "icd10cm",
    # ICD-10 (WHO international edition)
    "http://hl7.org/fhir/sid/icd-10":               "icd10",
    "urn:oid:2.16.840.1.113883.6.3":                "icd10",
    # LOINC
    "http://loinc.org":                              "loinc",
    "urn:oid:2.16.840.1.113883.6.1":                "loinc",
    # RxNorm
    "http://www.nlm.nih.gov/research/umls/rxnorm":  "rxnorm",
    "urn:oid:2.16.840.1.113883.6.88":               "rxnorm",
    # CPT
    "http://www.ama-assn.org/go/cpt":               "cpt",
    "urn:oid:2.16.840.1.113883.6.12":               "cpt",
    # HCPCS
    "https://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets": "hcpcs",
    "urn:oid:2.16.840.1.113883.6.285":              "hcpcs",
}

# Per-resource-type system preference order.
# First match wins. Systems not in the list fall to the end.
_SYSTEM_PREFERENCE: Dict[str, Tuple[str, ...]] = {
    "condition":   ("snomed", "icd10cm", "icd10"),
    "observation": ("loinc", "snomed"),
    "medication":  ("rxnorm", "snomed"),
    "procedure":   ("snomed", "cpt", "hcpcs"),
}


def _select_best_coding(
    coding_list: List[Dict],
    resource_type: str,
) -> Tuple[Dict, List[Dict]]:
    """
    Select the best coding from a FHIR coding array based on system priority,
    and return all codings as normalized tuples for multi-system downstream lookup.

    Synthea bundles contain exactly one coding per resource, so this function
    returns that single coding unchanged (backward compatible). Real EHR bundles
    often contain multiple codings from different systems (SNOMED, ICD-10-CM,
    LOINC, RxNorm, local/proprietary) for the same clinical concept.

    Selection logic:
      1. Classify each coding by its system URI using _SYSTEM_URI_TO_KEY.
      2. Pick the first coding whose system matches the preference order
         for this resource_type (_SYSTEM_PREFERENCE).
      3. If no coding matches any preferred system, fall back to the first
         coding in the list (same behavior as the original parser).

    Args:
        coding_list: The "coding" array from a FHIR CodeableConcept.
                     Each element is a dict with optional keys:
                     "system", "code", "display".
        resource_type: One of "condition", "observation", "medication",
                       "procedure". Controls system preference order.

    Returns:
        Tuple of (best_coding_dict, all_codings_list):

        best_coding_dict: {"code": str, "display": str, "system_key": str}
            The selected coding's code, display, and resolved system key.
            system_key is the canonical short key (e.g., "snomed", "loinc")
            or "unknown" if the system URI was not recognized.

        all_codings_list: [{"system_key": str, "code": str, "display": str}, ...]
            Every coding in the input list, each annotated with its resolved
            system_key. Used by downstream consumers (CancerCodeRegistry,
            MeSH crosswalk) that need to check multiple code systems.
            Empty list if coding_list was empty.
    """
    if not coding_list:
        return (
            {"code": "unknown", "display": "unknown", "system_key": "unknown"},
            [],
        )

    # Annotate every coding with its resolved system key
    annotated: List[Dict] = []
    for c in coding_list:
        system_uri = (c.get("system") or "").strip()
        system_key = _SYSTEM_URI_TO_KEY.get(system_uri, "unknown")
        annotated.append({
            "system_key": system_key,
            "code":       (c.get("code") or "unknown").strip(),
            "display":    (c.get("display") or "unknown").strip(),
        })

    # Select best coding by system preference
    preference = _SYSTEM_PREFERENCE.get(resource_type, ())
    for preferred_key in preference:
        for entry in annotated:
            if entry["system_key"] == preferred_key:
                return entry, annotated

    # No preferred system found: fall back to first coding (original behavior)
    return annotated[0], annotated


def parse_fhir_bundle(bundle_path: str) -> Dict:
    """
    Extract structured patient data from FHIR bundle.

    Args:
        bundle_path: Path to FHIR JSON bundle file

    Returns:
        Structured patient dictionary with demographics, conditions,
        medications, observations, procedures
    """
    with open(bundle_path, 'r') as f:
        bundle = json.load(f)

    # Initialize patient data structure
    patient_data = {
        'patient_id':                None,
        'demographics':              {},
        'conditions':                [],
        'medications':               [],
        'observations':              [],
        'procedures':                [],
        'allergies':                 [],
        'cancer_stage_observations': [],  # mCODE TNM stage group Observations (LOINC 21908-9/21902-2/21914-7)
        'cancer_genomic_variants':   []   # mCODE genomic variant Observations (LOINC 69548-6)
    }

    # Build Medication resource lookup table first.
    # Synthea bundles use medicationReference (a pointer to a separate
    # Medication resource) instead of inline medicationCodeableConcept.
    # Without this lookup, all medications resolve to "unknown".
    med_lookup = {}
    for entry in bundle.get('entry', []):
        resource = entry.get('resource', {})
        if resource.get('resourceType') == 'Medication':
            
            # Key by resource id AND by full URN (both are used as references)
            res_id      = resource.get('id', '')
            coding_list = resource.get('code', {}).get('coding', [])
            best_coding, _ = _select_best_coding(coding_list, "medication")
            med_info = {
                'code':    best_coding["code"],
                'display': best_coding["display"],
            }
            
            med_lookup[res_id] = med_info
            # Also map the fullUrl for urn:uuid: references
            full_url = entry.get('fullUrl', '')
            if full_url:
                med_lookup[full_url] = med_info

    # Process each resource in bundle
    for entry in bundle.get('entry', []):
        resource      = entry.get('resource', {})
        resource_type = resource.get('resourceType')

        if resource_type == 'Patient':
            patient_data['patient_id']   = resource.get('id')
            patient_data['demographics'] = _parse_demographics(resource)

        elif resource_type == 'Condition':
            patient_data['conditions'].append(_parse_condition(resource))

        elif resource_type == 'MedicationRequest':
            patient_data['medications'].append(
                _parse_medication(resource, med_lookup)
            )

        elif resource_type == 'MedicationStatement':
            patient_data['medications'].append(
                _parse_medication_statement(resource, med_lookup)
            )

        elif resource_type == 'Observation':
            # Drop invalid observations before parsing.
            # entered-in-error: data entry mistake.
            # cancelled: observation was cancelled before completion.
            # preliminary: unverified result — excluded to avoid GPT-4o
            #              acting on unconfirmed lab values.

            if resource.get('status', 'unknown').lower().strip() not in _EXCLUDE_OBS_STATUSES:
                # mCODE TNM stage group LOINC codes — route to dedicated list,
                # not to observations (keeps OncologyLabRegistry uncontaminated).
                # 21908-9 = clinical stage group
                # 21902-2 = pathologic stage group
                # 21914-7 = other stage group (retreatment, autopsy)

                obs_codings = resource.get('code', {}).get('coding', [])
                obs_loinc   = next(
                    (c.get('code', '').strip()
                     for c in obs_codings
                     if 'loinc' in c.get('system', '').lower()),
                    None
                )
                
                if obs_loinc in _MCODE_STAGE_LOINCS:
                    patient_data['cancer_stage_observations'].append(
                        _parse_mcode_stage_observation(resource)
                    )
                elif obs_loinc == _MCODE_GENOMIC_VARIANT_LOINC:
                    patient_data['cancer_genomic_variants'].append(
                        _parse_mcode_genomic_variant(resource)
                    )
                else:
                    patient_data['observations'].append(_parse_observation(resource))

        elif resource_type == 'Procedure':
            # Drop invalid procedures before parsing.
            # entered-in-error: data entry mistake.
            # not-done: procedure was explicitly not performed —
            #           absence of a procedure is not the same as
            #           having performed it.

            if resource.get('status', 'unknown').lower().strip() not in _EXCLUDE_PROC_STATUSES:
                patient_data['procedures'].append(_parse_procedure(resource))

        elif resource_type == 'AllergyIntolerance':
            patient_data['allergies'].append(_parse_allergy(resource))

    # Keep ALL medications -- active and historical -- for trial matching.
    # Active medications: current treatment criteria (met/violated).
    # Historical medications: prior treatment criteria and washout periods
    #   (e.g., "prior exposure to X", "no platinum within 6 months").
    # Filtering to active-only would cause all prior treatment exclusion
    # criteria to return not_evaluable — a significant loss of signal.
    #
    # entered-in-error is the only status excluded — these are data entry
    # mistakes and have no clinical meaning.
    patient_data['medications'] = [
        m for m in patient_data['medications']
        if m.get('status', 'unknown').lower().strip() != 'entered-in-error'
    ]

    # Deduplicate by display name, keeping active over historical when both exist.
    # Sort active first so deduplicate_by_display keeps the active entry.
    patient_data['medications'] = deduplicate_by_display(
        sorted(
            patient_data['medications'],
            key=lambda m: (0 if m.get('status', 'unknown').lower().strip()
                          in _ACTIVE_MED_STATUSES else 1)
        )
    )

    # Drop clinically invalid conditions before dedup.
    # entered-in-error: data entry mistakes with no clinical meaning.
    # refuted: diagnosis was explicitly ruled out.
    # Both must be excluded before dedup so they cannot displace valid entries.
    _EXCLUDE_CONDITION_VERIFICATION = frozenset({"entered-in-error", "refuted"})
    patient_data['conditions'] = [
        c for c in patient_data['conditions']
        if c.get('verification_status', 'unknown') not in _EXCLUDE_CONDITION_VERIFICATION
    ]

    # Deduplicate conditions: sort by clinical priority + recency FIRST so the
    # most relevant entry (active > resolved, recent > old) is always kept.
    # Bug 7 fix: previous code called deduplicate_by_display directly on unsorted
    # list, so a 'resolved' condition could be kept over an 'active' one.
    patient_data['conditions'] = deduplicate_by_display(
        sorted(patient_data['conditions'], key=_condition_sort_key)
    )
    
    patient_data['allergies'] = deduplicate_by_display([
        a for a in patient_data['allergies']
        if a.get('clinical_status', 'unknown') in _ACTIVE_ALLERGY_STATUSES
        and a.get('verification_status', 'unknown') not in _EXCLUDE_ALLERGY_VERIFICATION
    ])

    return patient_data


# ---------------------------------------------------------------------------
# US Core race / ethnicity extension (HL7 US Core profile)
# ---------------------------------------------------------------------------
# The race and ethnicity extensions are complex extensions whose sub-extensions
# are an UNORDERED set identified by url, not by position:
#   ombCategory  0..5 for race, 0..1 for ethnicity, a valueCoding
#   detailed     0..*, a finer-grained valueCoding
#   text         1..1, a valueString -- the only mandatory one
#
# Reading extension[0].get('valueCoding') took whichever sub-extension the
# exporter happened to serialize first, so a bundle leading with `text` (which
# carries valueString, not valueCoding) produced {} and the field silently
# became "unknown" for every patient in that export.
_US_CORE_OMB_CATEGORY = "ombCategory"
_US_CORE_DETAILED     = "detailed"
_US_CORE_TEXT         = "text"

# Which sub-extension each parsed value came from, corpus-wide. A run whose
# race values all resolved from `text` rather than `ombCategory` is holding
# free text where downstream code expects OMB categories, and that is only
# visible if the source is counted.
DEMOGRAPHIC_SOURCE_COUNTS = Counter()

# Shape of every birthDate seen, corpus-wide. "day" is exact; "month" and
# "year" mean the age is imputed from an anchor (File 02); the rest mean no
# age was produced at all. Reported by load_all_patients().
BIRTH_DATE_PRECISION_COUNTS = Counter()


def _read_us_core_category(extension: Dict) -> Tuple[str, str]:
    """Read a US Core race/ethnicity extension by sub-extension url.

    Preference order, most standardised first: ombCategory -> detailed -> text.

    Multiple ombCategory sub-extensions are legal (US Core allows up to five
    for race) and are joined rather than truncated to the first: dropping one
    would silently re-label a multi-race patient as single-race.

    Returns:
        (value, source) where source is the sub-extension url the value came
        from, or "empty" when the extension carried nothing readable. The
        source is returned rather than logged here so the caller can both store
        it per patient and count it corpus-wide.
    """

    sub_extensions = extension.get('extension') or []

    for url in (_US_CORE_OMB_CATEGORY, _US_CORE_DETAILED):
        displays = [
            ((sub.get('valueCoding') or {}).get('display') or '').strip()
            for sub in sub_extensions
            if isinstance(sub, dict) and sub.get('url') == url
        ]
        displays = [d for d in displays if d]
        if displays:
            return "; ".join(displays), url

    for sub in sub_extensions:
        if isinstance(sub, dict) and sub.get('url') == _US_CORE_TEXT:
            text = (sub.get('valueString') or '').strip()
            if text:
                return text, _US_CORE_TEXT

    return 'unknown', 'empty'


def _parse_demographics(patient_resource: Dict) -> Dict:
    """Extract demographics from Patient resource.

    Output fields beyond the obvious ones:
      birth_date_precision: How much of the birth date the record actually
                            carried -- "day" (exact), "month"/"year" (age
                            imputed from an anchor, see File 02), "missing",
                            "unparseable", or "after_reference". An imputed or
                            absent age must never be mistaken for an exact one.
      age_reference_date:   ISO date the age was computed against, so the age
                            in a stored row can be recomputed exactly.
      race_source /
      ethnicity_source:     Which US Core sub-extension supplied the value.
    """

    birth_date_raw = patient_resource.get('birthDate', '')
    reference_date = get_age_reference_date()

    birth, precision = parse_partial_date(birth_date_raw)
    age = _calculate_age(birth, reference_date) if birth is not None else None

    if birth is not None and age is None:
        # Parsed cleanly but sits after the snapshot the corpus is anchored to:
        # the data outran DATA_SNAPSHOT_DATE, or the record is wrong. Either
        # way there is no age to state, and the reason has to survive in the row.
        precision = "after_reference"
        print(f"  WARNING: birthDate {birth_date_raw!r} is after the age reference "
              f"date {reference_date.isoformat()} — age not computed")
    elif precision == "unparseable":
        print(f"  WARNING: unparseable birthDate {birth_date_raw!r} — age not computed")

    BIRTH_DATE_PRECISION_COUNTS[precision] += 1

    # Extract sex
    sex = patient_resource.get('gender', 'unknown')

    # Extract race and ethnicity from the US Core extensions, by url.
    race,      race_source      = 'unknown', 'absent'
    ethnicity, ethnicity_source = 'unknown', 'absent'

    for ext in patient_resource.get('extension') or []:
        if not isinstance(ext, dict):
            continue
        url = ext.get('url', '')
        if 'us-core-race' in url:
            race, race_source = _read_us_core_category(ext)
        elif 'us-core-ethnicity' in url:
            ethnicity, ethnicity_source = _read_us_core_category(ext)

    DEMOGRAPHIC_SOURCE_COUNTS[f"race:{race_source}"] += 1
    DEMOGRAPHIC_SOURCE_COUNTS[f"ethnicity:{ethnicity_source}"] += 1

    return {
        'age':                  age,
        'sex':                  sex,
        'race':                 race,
        'ethnicity':            ethnicity,
        'birth_date':           birth_date_raw,
        'birth_date_precision': precision,
        'age_reference_date':   reference_date.isoformat(),
        'race_source':          race_source,
        'ethnicity_source':     ethnicity_source,
    }


def _parse_condition(condition_resource: Dict) -> Dict:
    """
    Extract condition (diagnosis) information.

    Multi-coding support: real EHRs attach multiple codings per condition
    (e.g., SNOMED + ICD-10-CM + local code). _select_best_coding picks the
    most useful code by system priority (SNOMED > ICD-10-CM > ICD-10) and
    stores all codings for downstream multi-system lookup.

    Output fields:
      code:                Best available code string (backward compatible).
      display:             Best available display string (backward compatible).
      system_key:          Resolved system key of best coding ("snomed", "icd10cm", etc.).
      codings:             List of all codings [{system_key, code, display}, ...].
                           Used by CancerCodeRegistry and MeSH crosswalk for
                           multi-system cancer detection and tree resolution.
      onset_date:          Onset date with fallback chain: onsetDateTime ->
                           onsetPeriod.start -> onsetPeriod.end -> "unknown".
      clinical_status:     Normalized lowercase (active, resolved, etc.).
      verification_status: Normalized lowercase (confirmed, refuted, etc.).
    """
    # Multi-coding selection: pick best code by system priority (SNOMED > ICD-10-CM > ICD-10),
    # and preserve all codings for downstream multi-system lookup (CancerCodeRegistry, MeSH crosswalk).
    # Replaces coding_list[0] which broke when the first coding was a local/proprietary code.
    code_obj    = condition_resource.get('code', {})
    coding_list = code_obj.get('coding', [])
    best_coding, all_codings = _select_best_coding(coding_list, "condition")

    # verificationStatus (Bug 1)
    # Normalize to lowercase so File 11's exclusion filter and dict lookups
    # work regardless of source EHR casing (FHIR spec requires lowercase,
    # but real EHRs occasionally send 'Confirmed', 'Refuted', etc.)
    ver_coding_list     = condition_resource.get('verificationStatus', {}).get('coding', [])
    verification_status = (
        ver_coding_list[0].get('code', 'unknown').lower().strip()
        if ver_coding_list else 'unknown'
    )

    # clinicalStatus — safe access + normalize
    cs_coding_list  = condition_resource.get('clinicalStatus', {}).get('coding', [])
    clinical_status = (
        cs_coding_list[0].get('code', 'unknown').lower().strip()
        if cs_coding_list else 'unknown'
    )

    # onset_date: prefer onsetDateTime, fall back to onsetPeriod.start,
    # then onsetPeriod.end, then 'unknown' (Bug 2)
    onset_date = (
        condition_resource.get('onsetDateTime')
        or condition_resource.get('onsetPeriod', {}).get('start')
        or condition_resource.get('onsetPeriod', {}).get('end')
        or 'unknown'
    )

    # Free-text uncertainty detection: real EHRs (Epic, Cerner) frequently
    # embed diagnostic uncertainty in the display name rather than setting
    # verificationStatus. If the display text contains uncertainty qualifiers,
    # override verification_status to "unconfirmed" regardless of what
    # verificationStatus says -- a confirmed "possible metastasis" is still
    # an unconfirmed diagnosis clinically.
    # Uncertainty qualifiers validated against FHIR verificationStatus vocabulary,
    # clinical NLP literature, and real-world Epic/Cerner export patterns.
    # Matches display names where diagnostic uncertainty is embedded in free text
    # rather than set via verificationStatus (common in real-world EHR exports).
    # "likely" excluded -- too broad, risks flagging confirmed diagnoses with
    # interpretive qualifiers (e.g. "likely stage II", "likely benign").
    _UNCERTAINTY_PREFIXES = (
        "possible ", "possibly ",
        "suspected ", "suspect ",
        "probable ", "probably ",
        "rule out ", "rule-out ", "r/o ",
        "questionable ",
        "query ",
        "provisional ",
        "differential ",
        "cannot exclude ",
        "vs. ", "versus ",
    )
    
    display_lower = best_coding["display"].lower().strip()
    
    if any(display_lower.startswith(p) for p in _UNCERTAINTY_PREFIXES):
        verification_status = "unconfirmed"

    return {
        'code':                best_coding["code"],
        'display':             best_coding["display"],
        'system_key':          best_coding["system_key"],
        'codings':             all_codings,
        'onset_date':          onset_date,
        'clinical_status':     clinical_status,
        'verification_status': verification_status,
    }


def _parse_medication(med_resource: Dict, med_lookup: Dict = None) -> Dict:
    """
    Extract medication information.

    Handles two FHIR patterns:
        1. medicationCodeableConcept: drug info is inline (real EHRs)
        2. medicationReference: drug info is in a separate Medication resource
           (Synthea default)

    Multi-coding support: picks RxNorm code by system priority when inline
    coding has multiple entries (RxNorm > SNOMED > first available). The
    medicationReference fallback path uses med_lookup, which is also built
    with _select_best_coding (in parse_fhir_bundle).
    """
    # Try inline medicationCodeableConcept first.
    # Multi-coding selection: pick RxNorm code by system priority (RxNorm > SNOMED > first).
    # Prepares for Tier 3 medication relevance filtering which will need RxNorm codes.
    med_obj     = med_resource.get('medicationCodeableConcept', {})
    coding_list = med_obj.get('coding', [])
    best_coding, _ = _select_best_coding(coding_list, "medication")

    code    = best_coding["code"]
    display = best_coding["display"]

    # If inline is empty, resolve via medicationReference
    if display == 'unknown' and med_lookup:
        ref    = med_resource.get('medicationReference', {}).get('reference', '')
        ref_id = ref.replace('urn:uuid:', '')

        if ref in med_lookup:
            code    = med_lookup[ref]['code']
            display = med_lookup[ref]['display']
        elif ref_id in med_lookup:
            code    = med_lookup[ref_id]['code']
            display = med_lookup[ref_id]['display']

    # End date: dispenseRequest.validityPeriod.end -> unknown
    # Real EHRs populate this for completed/stopped medications.
    end_date = (
        med_resource.get('dispenseRequest', {}).get('validityPeriod', {}).get('end')
        or 'unknown'
    )

    return {
        'code':       code,
        'display':    display,
        'start_date': med_resource.get('authoredOn', 'unknown'),
        'end_date':   end_date,
        'status':     med_resource.get('status', 'unknown')
    }


def _parse_medication_statement(stmt_resource: Dict, med_lookup: Dict = None) -> Dict:
    """
    Extract medication information from a MedicationStatement resource.

    MedicationStatement is the patient-reported or clinician-asserted record
    of medications being taken. Some EHR systems (notably Cerner/Oracle Health)
    use this as the primary medication resource instead of MedicationRequest.

    Produces the same output dict shape as _parse_medication so both resource
    types feed into the same downstream pipeline (status filter, dedup, prompt).

    Handles the same two FHIR medication patterns:
        1. medicationCodeableConcept: drug info is inline
        2. medicationReference: drug info is in a separate Medication resource

    Date field mapping:
        MedicationStatement uses effectiveDateTime / effectivePeriod (when the
        patient was taking the med), not authoredOn (when it was prescribed).
        We map to start_date for consistency with _parse_medication output.

    Status mapping:
        MedicationStatement statuses: active, completed, entered-in-error,
        intended, stopped, on-hold, unknown, not-taken.
        Mapped directly; _ACTIVE_MED_STATUSES covers both resource types.
    """
    # Try inline medicationCodeableConcept first.
    med_obj     = stmt_resource.get('medicationCodeableConcept', {})
    coding_list = med_obj.get('coding', [])
    best_coding, _ = _select_best_coding(coding_list, "medication")

    code    = best_coding["code"]
    display = best_coding["display"]

    # If inline is empty, resolve via medicationReference
    if display == 'unknown' and med_lookup:
        ref    = stmt_resource.get('medicationReference', {}).get('reference', '')
        ref_id = ref.replace('urn:uuid:', '')

        if ref in med_lookup:
            code    = med_lookup[ref]['code']
            display = med_lookup[ref]['display']
        elif ref_id in med_lookup:
            code    = med_lookup[ref_id]['code']
            display = med_lookup[ref_id]['display']

    # Date: effectiveDateTime -> effectivePeriod.start -> dateAsserted -> unknown
    start_date = (
        stmt_resource.get('effectiveDateTime')
        or stmt_resource.get('effectivePeriod', {}).get('start')
        or stmt_resource.get('dateAsserted')
        or 'unknown'
    )

    # End date: effectivePeriod.end -> unknown
    end_date = (
        stmt_resource.get('effectivePeriod', {}).get('end')
        or 'unknown'
    )

    return {
        'code':       code,
        'display':    display,
        'start_date': start_date,
        'end_date':   end_date,
        'status':     stmt_resource.get('status', 'unknown'),
    }


# mCODE TNM stage group LOINC codes
_MCODE_STAGE_LOINCS: FrozenSet[str] = frozenset({"21908-9", "21902-2", "21914-7"})

def _parse_mcode_stage_observation(obs_resource: Dict) -> Dict:
    """
    Parse a mCODE TNM stage group Observation resource.

    These use LOINC codes 21908-9 (clinical), 21902-2 (pathologic),
    21914-7 (other) and carry the stage group in valueCodeableConcept.

    Returns a minimal dict consumed by extract_patient_stage() in File 10:
        stage_display : str  — e.g. "Stage IV", "Stage IIIA", "IV"
        stage_code    : str  — SNOMED or NCIT code for the stage value
        date          : str  — effectiveDateTime or 'unknown'
        loinc         : str  — the stage type LOINC code
    """
    # Stage value lives in valueCodeableConcept
    vc          = obs_resource.get('valueCodeableConcept', {})
    vc_codings  = vc.get('coding', [])
    stage_display = vc.get('text') or ''
    stage_code    = ''

    if vc_codings:
        # Prefer display text from first coding; fall back to text field
        stage_display = vc_codings[0].get('display') or stage_display
        stage_code    = vc_codings[0].get('code') or ''

    # effectivePeriod start as fallback for date
    date = (
        obs_resource.get('effectiveDateTime')
        or obs_resource.get('effectivePeriod', {}).get('start')
        or 'unknown'
    )

    # Determine LOINC type
    obs_codings = obs_resource.get('code', {}).get('coding', [])
    loinc = next(
        (c.get('code', '').strip()
         for c in obs_codings
         if 'loinc' in c.get('system', '').lower()),
        'unknown'
    )

    # mCODE valueCodeableConcept display is often bare ("IIIC", "IV") without
    # the word "stage". Prepend it so _SNOMED_DISPLAY_STAGE_RE matches correctly.
    display_normalized = stage_display.strip()
    if display_normalized and not re.search(r'\bstage\b', display_normalized, re.IGNORECASE):
        display_normalized = f"Stage {display_normalized}"

    return {
        'stage_display': display_normalized,
        'stage_code':    stage_code.strip(),
        'date':          date,
        'loinc':         loinc,
    }

# mCODE genomic variant Observation LOINC code
_MCODE_GENOMIC_VARIANT_LOINC: str = "69548-6"

# Component LOINC codes inside a genomic variant Observation
_COMPONENT_GENE_STUDIED:      str = "48018-6"   # Gene studied [ID] — display = gene symbol e.g. "EGFR"
_COMPONENT_GENOMIC_SOURCE:    str = "48002-0"   # Genomic source class — "Somatic" / "Germline"
_COMPONENT_HGVS_CDNA:         str = "81290-9"   # HGVS cDNA change e.g. "c.2573T>G"
_COMPONENT_HGVS_PROTEIN:      str = "81252-9"   # HGVS protein change e.g. "p.Leu858Arg"


def _parse_mcode_genomic_variant(obs_resource: Dict) -> Dict:
    """
    Parse a mCODE genomic variant Observation (LOINC 69548-6).

    Structure (per mCODE STU4 spec):
      Observation.value[x]          — overall result: Present/Absent/Unknown
                                      (LOINC answer list LL1971-2)
      Observation.interpretation    — Positive / Negative / Indeterminate
      Observation.component[]       — gene name, source class, HGVS notation

    Key components extracted:
      48018-6  Gene studied [ID]         — display text = gene symbol (EGFR, KRAS, etc.)
      48002-0  Genomic source class       — Somatic / Germline
      81290-9  HGVS cDNA change          — e.g. "c.2573T>G"
      81252-9  HGVS protein change       — e.g. "p.Leu858Arg" (L858R)

    Returns a dict consumed by OncologyLabRegistry.filter_relevant_genomic_variants()
    and rendered in the Genomic & Molecular Biomarkers section of the patient summary.
    """
    # ── Top-level result value ─────────────────────────────────────────────
    result_value = None
    if 'valueCodeableConcept' in obs_resource:
        vc = obs_resource['valueCodeableConcept']
        vc_codings = vc.get('coding', [])
        result_value = (
            vc_codings[0].get('display') if vc_codings
            else vc.get('text')
        )
    elif 'valueString' in obs_resource:
        result_value = obs_resource['valueString']

    # ── Interpretation (Positive / Negative / Indeterminate) ──────────────
    interpretation = None
    interp_list = obs_resource.get('interpretation', [])
    if interp_list:
        interp_codings = interp_list[0].get('coding', [])
        interpretation = (
            interp_codings[0].get('display') if interp_codings
            else interp_list[0].get('text')
        )

    # ── Components ────────────────────────────────────────────────────────
    gene_symbol    = None
    genomic_source = None
    hgvs_cdna      = None
    hgvs_protein   = None

    for comp in obs_resource.get('component', []):
        comp_codings = comp.get('code', {}).get('coding', [])
        comp_loinc   = next(
            (c.get('code', '').strip()
             for c in comp_codings
             if 'loinc' in c.get('system', '').lower()),
            None
        )
        if not comp_loinc:
            continue

        comp_vc       = comp.get('valueCodeableConcept', {})
        comp_vc_codings = comp_vc.get('coding', [])
        comp_display  = (
            comp_vc_codings[0].get('display') if comp_vc_codings
            else comp_vc.get('text')
            or comp.get('valueString')
        )

        if comp_loinc == _COMPONENT_GENE_STUDIED:
            gene_symbol = comp_display          # e.g. "EGFR", "KRAS", "ALK"
        elif comp_loinc == _COMPONENT_GENOMIC_SOURCE:
            genomic_source = comp_display       # "Somatic" or "Germline"
        elif comp_loinc == _COMPONENT_HGVS_CDNA:
            hgvs_cdna = comp_display            # e.g. "c.2573T>G"
        elif comp_loinc == _COMPONENT_HGVS_PROTEIN:
            hgvs_protein = comp_display         # e.g. "p.Leu858Arg"

    # ── Date ──────────────────────────────────────────────────────────────
    date = (
        obs_resource.get('effectiveDateTime')
        or obs_resource.get('effectivePeriod', {}).get('start')
        or 'unknown'
    )

    # ── Build human-readable summary for GPT-4o ───────────────────────────
    # Example outputs:
    #   "EGFR p.Leu858Arg (L858R): Present | Somatic"
    #   "KRAS c.35G>T: Present"
    #   "ALK: Positive"
    parts = []
    if gene_symbol:
        label = gene_symbol
        if hgvs_protein:
            label += f" {hgvs_protein}"
        elif hgvs_cdna:
            label += f" {hgvs_cdna}"
        parts.append(label)

    result_str = result_value or interpretation
    if result_str:
        parts.append(result_str)

    if genomic_source and genomic_source.lower() != 'somatic':
        # Only show if Germline — somatic is assumed default in oncology
        parts.append(genomic_source)

    display_summary = ": ".join([parts[0], " | ".join(parts[1:])]) if len(parts) > 1 else (parts[0] if parts else "Unknown variant")

    return {
        'code':           _MCODE_GENOMIC_VARIANT_LOINC,
        'display':        display_summary,
        'gene_symbol':    gene_symbol,
        'result_value':   result_value,
        'interpretation': interpretation,
        'genomic_source': genomic_source,
        'hgvs_cdna':      hgvs_cdna,
        'hgvs_protein':   hgvs_protein,
        'value':          result_value or interpretation,  # for OncologyLabRegistry compatibility
        'date':           date,
    }


def _parse_observation(obs_resource: Dict) -> Dict:
    """
    Extract observation (lab/vital) information.

    Multi-coding support: picks LOINC code by system priority when multiple
    codings exist (LOINC > SNOMED > first available). OncologyLabRegistry
    filters observations by LOINC code; selecting a non-LOINC coding at [0]
    caused relevant labs to be silently missed.

    Handles all FHIR value types used by Synthea and real EHRs:
        valueQuantity, valueString, valueCodeableConcept, valueInteger,
        valueBoolean, valueDateTime, valueRange, valueRatio.
    """
    # Multi-coding selection: pick LOINC code when multiple codings exist.
    # OncologyLabRegistry filters by LOINC code; if [0] was a local code,
    # relevant labs (ANC, creatinine, bilirubin, etc.) were silently missed.
    code_obj    = obs_resource.get('code', {})
    coding_list = code_obj.get('coding', [])
    best_coding, _ = _select_best_coding(coding_list, "observation")

    value = None
    unit  = None

    if 'valueQuantity' in obs_resource:
        value = obs_resource['valueQuantity'].get('value')
        unit  = obs_resource['valueQuantity'].get('unit')
    elif 'valueString' in obs_resource:
        value = obs_resource['valueString']
    elif 'valueCodeableConcept' in obs_resource:
        # Coded value e.g. ECOG Performance Status
        vc_coding_list = obs_resource['valueCodeableConcept'].get('coding', [])
        vc_coding      = vc_coding_list[0] if vc_coding_list else {}
        value = vc_coding.get('display') or obs_resource['valueCodeableConcept'].get('text')
    elif 'valueInteger' in obs_resource:
        value = obs_resource['valueInteger']
    elif 'valueBoolean' in obs_resource:
        value = obs_resource['valueBoolean']
    elif 'valueDateTime' in obs_resource:
        value = obs_resource['valueDateTime']
    elif 'valueRange' in obs_resource:
        vr = obs_resource['valueRange']
        low = vr.get('low', {}).get('value')
        high = vr.get('high', {}).get('value')
        unit = vr.get('low', {}).get('unit') or vr.get('high', {}).get('unit')
        value = f"{low}-{high}" if low is not None and high is not None else (low or high)
    elif 'valueRatio' in obs_resource:
        num = obs_resource['valueRatio'].get('numerator', {}).get('value')
        den = obs_resource['valueRatio'].get('denominator', {}).get('value')
        value = f"{num}/{den}" if num is not None and den is not None else None

    return {
        'code':    best_coding["code"],
        'display': best_coding["display"],
        'value':   value,
        'unit':    unit,
        'date':    obs_resource.get('effectiveDateTime', 'unknown')
    }


def _parse_procedure(proc_resource: Dict) -> Dict:
    """
    Extract procedure information.

    Multi-coding support: picks best code by system priority
    (SNOMED > CPT > HCPCS > first available) when real EHRs attach
    multiple codings per procedure.

    Date fallback chain: performedDateTime -> performedPeriod.start ->
    performedPeriod.end -> "unknown". Covers both single-event and
    multi-day procedures (chemotherapy, radiation).
    """
    # Multi-coding selection: pick best code by system priority (SNOMED > CPT > HCPCS > first).
    # Real EHRs often send both SNOMED and CPT codings for the same procedure.
    code_obj    = proc_resource.get('code', {})
    coding_list = code_obj.get('coding', [])
    best_coding, _ = _select_best_coding(coding_list, "procedure")

    # Prefer performedDateTime; fall back to performedPeriod (Bug 4)
    date = proc_resource.get('performedDateTime')
    if not date:
        period = proc_resource.get('performedPeriod', {})
        date   = period.get('start') or period.get('end')
    date = date or 'unknown'

    return {
        'code':    best_coding["code"],
        'display': best_coding["display"],
        'date':    date,
        'status':  proc_resource.get('status', 'unknown'),
    }


def _parse_allergy(allergy_resource: Dict) -> Dict:
    """
    Extract allergy/intolerance information from an AllergyIntolerance resource.

    Extracts the allergen identity, clinical status, verification status,
    category (medication/food/environment), and criticality. Used downstream
    in _create_patient_summary to inform GPT-4o about drug allergies that
    may affect trial eligibility (e.g., "No known allergy to platinum agents").

    Fields:
      code:                Allergen code (SNOMED, RxNorm, or first available).
      display:             Allergen display name (e.g., "Penicillin", "Carboplatin").
      category:            "medication" | "food" | "environment" | "biologic" | "unknown".
      criticality:         "low" | "high" | "unable-to-assess" | "unknown".
      clinical_status:     "active" | "inactive" | "resolved" | "unknown".
      verification_status: "confirmed" | "unconfirmed" | "refuted" | "entered-in-error" | "unknown".
      onset_date:          When the allergy was identified. Fallback chain:
                           onsetDateTime -> onsetPeriod.start -> recordedDate -> "unknown".
    """
    # Allergen code (CodeableConcept with coding array)
    code_obj    = allergy_resource.get('code', {})
    coding_list = code_obj.get('coding', [])
    best_coding, _ = _select_best_coding(coding_list, "medication")

    # Category (0..* in FHIR, take first if present)
    category_list = allergy_resource.get('category', [])
    category = category_list[0] if category_list else 'unknown'

    # Criticality
    criticality = allergy_resource.get('criticality', 'unknown')

    # Clinical status (CodeableConcept)
    cs_coding_list = allergy_resource.get('clinicalStatus', {}).get('coding', [])
    clinical_status = (
        cs_coding_list[0].get('code', 'unknown').lower().strip()
        if cs_coding_list else 'unknown'
    )

    # Verification status (CodeableConcept)
    ver_coding_list = allergy_resource.get('verificationStatus', {}).get('coding', [])
    verification_status = (
        ver_coding_list[0].get('code', 'unknown').lower().strip()
        if ver_coding_list else 'unknown'
    )

    # Onset date: onsetDateTime -> onsetPeriod.start -> recordedDate -> unknown
    onset_date = (
        allergy_resource.get('onsetDateTime')
        or allergy_resource.get('onsetPeriod', {}).get('start')
        or allergy_resource.get('recordedDate')
        or 'unknown'
    )

    return {
        'code':                best_coding["code"],
        'display':             best_coding["display"],
        'category':            category,
        'criticality':         criticality,
        'clinical_status':     clinical_status,
        'verification_status': verification_status,
        'onset_date':          onset_date,
    }


def _calculate_age(birth_date, reference_date=None) -> Optional[int]:
    """Age in completed years at the run's age reference date.

    Args:
        birth_date:     Raw FHIR birthDate (any shape parse_partial_date accepts,
                        File 02), or an already-parsed date/datetime.
        reference_date: Date to age against. Defaults to get_age_reference_date()
                        -- the data snapshot date, never the current clock.

    Returns:
        Age in whole years, or None when no age can be stated. None is returned
        rather than raising for two cases, and both are recorded by the caller
        as a precision label rather than swallowed:
          - the birth date is missing or unparseable
          - the birth date falls after the reference date, which is not an age
            but a sign that the corpus outran DATA_SNAPSHOT_DATE

    Never raises. The previous implementation used a fixed '%Y-%m-%d' strptime
    and datetime.now(): it raised on the year-only, year-month and ISO-datetime
    shapes that FHIR permits, and its result moved with the clock.
    """

    birth, _precision = parse_partial_date(birth_date)
    if birth is None:
        return None

    reference = reference_date if reference_date is not None else get_age_reference_date()
    if isinstance(reference, datetime):
        reference = reference.date()

    if birth > reference:
        return None

    return relativedelta(reference, birth).years


def load_all_patients(patients_dir: str) -> List[Dict]:
    """
    Load all FHIR bundles from directory.

    Args:
        patients_dir: Path to directory containing FHIR JSON files

    Returns:
        List of parsed patient dictionaries
    """
    patients_path = Path(patients_dir)
    patient_files = list(patients_path.glob('*.json'))

    patients = []
    errors   = []

    # Corpus-wide tallies are reported for THIS load, not accumulated across
    # calls, so the printed numbers always match the directory just parsed.
    BIRTH_DATE_PRECISION_COUNTS.clear()
    DEMOGRAPHIC_SOURCE_COUNTS.clear()

    for idx, fhir_file in enumerate(patient_files, 1):
        if idx % 100 == 0 or idx == len(patient_files):
            print(f"  Parsing {idx}/{len(patient_files)} patients...")
        try:
            patient_data = parse_fhir_bundle(str(fhir_file))
            patients.append(patient_data)
        except Exception as e:
            errors.append({
                'file':  str(fhir_file),
                'error': str(e)
            })
            print(f"Error parsing {fhir_file.name}: {e}")

    print(f"Successfully parsed {len(patients)} patients")
    if errors:
        print(f"Failed to parse {len(errors)} patients")

    # Which parsing path each field took. Printed unconditionally: "every
    # birthDate was a full date" is itself a result worth stating, and a
    # corpus that drifts toward imputed ages or free-text race should be
    # visible at the point of load rather than inferred later from the rows.
    print(f"  Birth date precision: {dict(sorted(BIRTH_DATE_PRECISION_COUNTS.items()))}")
    print(f"  Age reference date:   {get_age_reference_date().isoformat()}")
    print(f"  Demographic sources:  {dict(sorted(DEMOGRAPHIC_SOURCE_COUNTS.items()))}")

    return patients


#------------------------------------------------------------------------------


# Call the parser
#----------------
if __name__ == '__main__':

    # Load all patients
    all_patients = load_all_patients(data_fhir_path)
    print(f"\nTotal patients loaded: {len(all_patients)}\n")

    # TEMPORARY: Use only first 100 for testing
    # all_patients = all_patients[:100]
    # print(f"Using subset for testing: {len(all_patients)} patients\n")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb  9 19:42:33 2026

@author: ramyalsaffar
"""