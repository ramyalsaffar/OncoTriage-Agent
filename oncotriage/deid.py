"""The de-identification stage: what may be rendered, and the guard that proves it.

Between ``oncotriage/fhir/parser.py`` and ``oncotriage/agent/patient.py``'s
renderer, as its own step with its own name. The point of the separation is
that the guarantee becomes ARCHITECTURAL rather than an accident of what the
renderer happens to print: ``render_patient_record`` is handed a record built
here, so a field that is not in ``RENDERED_FIELDS`` is not in scope at the
point a line could be written from it.

=============================================================================
WHAT WAS ALREADY TRUE, MEASURED BEFORE ANYTHING WAS WRITTEN
=============================================================================

The finding that shaped this module: **the rendered summary already contained
no direct identifier, and the parser is why.** ``_parse_demographics`` reads
``birthDate``, ``gender`` and the two US Core extensions and NOTHING ELSE --
no ``name``, no ``address``, no ``telecom``, no ``identifier`` -- and no other
per-resource parser reads them either. So the whole of

    name.family, name.given, name.prefix, telecom.phone, address.line,
    address.city, address.state, address.postalCode, address.geo (lat/long),
    identifier.Medical Record Number, identifier.Social Security Number,
    identifier.Driver's license number, identifier.Passport Number,
    identifier.<untyped> (2,610 of them on one bundle)

is dropped before ``patient_data`` exists. Measured, not read: every one of
the 1,000 corpus bundles was harvested for those values and every rendered
summary was scanned for them, at a four-character floor. **Zero hits.**

So this stage's job is NARROWER THAN CREATING THAT PROPERTY -- it is
GUARANTEEING it, against a future parser that starts carrying a field, against
free text that carries an identifier the parser had no way to recognise, and
against a renderer edit that prints something it should not. That is worth
saying plainly, because a reader who believes this module is what stops names
reaching the model will not understand what actually does.

**ONE DIRECT IDENTIFIER DID REACH ``patient_data``**: ``patient_id``, the FHIR
``Patient.id``, which on this corpus is byte-identical to the Medical Record
Number in ``identifier[]``. It is a record number under any reading. The
renderer never printed it; it is not in ``RENDERED_FIELDS``, so it cannot be.

=============================================================================
THE RULING THIS IMPLEMENTS
=============================================================================

A Limited Data Set shape with pseudonymization, not Safe Harbor:

  DATES STAY, IN FULL.       Every elapsed interval, window and precision
                             behaviour of the renderer is untouched. Safe
                             Harbor's year-only dates would destroy the
                             temporal machinery ``PROMPT_VERSION`` 1.8.0 and
                             1.9.0 exist to feed, and that machinery is
                             validated.
  AGES STAY, CAPPED AT 89.   ``AGE_CAP_YEARS``. An age over the cap renders as
                             ``AGE_CAP_LABEL`` instead of a number.
  DIRECT IDENTIFIERS DO NOT  names, geography below state, phone, email,
  REACH THE PROMPT.          record numbers, insurance identifiers, and any
                             identifier-shaped value from the bundle.
  THE IDENTITY IS A          ``pseudonym``, derived from the pipeline's own
  PSEUDONYM.                 patient identity and stable across runs.

**STRICTER THAN AN LDS ON ONE AXIS, AND THAT IS THE OPERATOR'S RULING RATHER
THAN AN OVERSIGHT.** 45 CFR 164.514(e)(2) permits town or city, State and ZIP
code in a Limited Data Set. The ruling permits STATE and forbids everything
below it, so ``address.city`` and ``address.postalCode`` are treated as
identifiers here and ``address.state`` is not. The practical consequence is
also the reason a scan for state would be unusable: ``"CA"`` is two characters
and is a substring of the tumour marker ``CA 19-9``, so scanning for it would
fail nearly every patient in an oncology corpus. It is below
``_MIN_EXACT_MATCH_CHARS`` and would be skipped in any case.

=============================================================================
THE PSEUDONYM: WHY IT IS DERIVED FROM THE CLINICAL HASH
=============================================================================

``pseudonym = PSEUDONYM_PREFIX + sha256(PSEUDONYM_DOMAIN | identity)[:N]``,
where ``identity`` is ``compute_patient_hash(patient_data)`` -- the pipeline's
existing patient identity, which is what the ruling names.

**NOT ``sha256(patient_id)``, and the reason is a measurement rather than a
preference.** ``patient_id`` is on almost every log line this pipeline emits
(``log.info("match started", patient_id=...)``), so a pseudonym derived from it
would be re-identifiable by anyone holding the logs plus the prompts -- two
artifacts that routinely land in the same observability store. The ruling says
the mapping lives ONLY in the local database. ``patient_data_hash`` is
**never logged** (checked across the package: it appears only as an
``inferences`` column and in ``compute_patient_hash``'s own comments), so a
pseudonym derived from it is recoverable only by someone holding that database.

The mapping therefore already exists, in the one place the ruling allows and in
no other, with no schema change: ``inferences`` carries both ``patient_id`` and
``patient_data_hash``, and ``pseudonym_for_identity`` is the one function that
relates them. Nothing writes a pseudonym column and nothing needs one.

**DOMAIN-SEPARATED RATHER THAN THE HASH ITSELF.** If the pseudonym were the
patient hash verbatim, any row or artifact carrying both ``patient_id`` and
``patient_data_hash`` would BE the mapping, which is what the ruling forbids
outside the database. One extra sha256 with a fixed prefix makes the prompt's
token and the database's hash different strings, so recovering one from the
other requires this function.

**THE RESIDUAL, STATED.** Any deterministic pseudonym is confirmable by
somebody who already holds the same record: they can recompute it. That is
inherent -- the alternative is a random pseudonym stored in a database, which
would make the rendered prompt non-deterministic, machine-dependent, and
unusable by the fixture harness and by every reproducibility comparison this
project runs. Under 45 CFR 164.514(c) a re-identification code must not be
"derived from or related to information about the individual"; this one is
derived from the clinical record, so it would NOT satisfy Safe Harbor's coding
condition. It is offered under the LDS shape the ruling chose, where a code is
permitted, and the point is recorded here rather than discovered later.

=============================================================================
WHAT THIS STAGE DOES NOT DO: IT DOES NOT REWRITE CLINICAL TEXT
=============================================================================

Condition displays, medication names, procedure displays and observation values
are third-party free text this project does not author. An identifier CAN
appear inside one. This stage does not scrub them, and that is a decision:

  - a scrubber that edits clinical text deletes clinical evidence silently,
    which is the failure ``_classify_procedure_relevance`` already argues is
    worse than the tokens it saves, one layer up;
  - the words overlap. A city called Ontario, a family name that is also a
    syndrome eponym, a street called Parkinson -- a redactor cannot tell them
    apart and neither can this module.

So the structured fields are guaranteed BY CONSTRUCTION (a field not in
``RENDERED_FIELDS`` cannot be printed) and free text is guaranteed BY
ENFORCEMENT: ``scan_for_identifiers`` reads the RENDERED text, and Stage 5
fails the patient rather than sending a prompt that carries a hit. Fail clean,
resumable, never silently degraded -- the shape
``oncotriage/agent/evaluation.py``'s cache-or-nothing floor already has.

=============================================================================
THE COST OF THE GUARD, STATED RATHER THAN DISCOVERED
=============================================================================

A false positive fails a patient who would otherwise have been matched. Two
sources, both real:

  1. **A NAME THAT IS ALSO A CLINICAL WORD.** On real data a patient named
     Hunter makes ``Hunter syndrome`` a hit. Word boundaries do not help --
     it IS a whole word. The scan runs anyway, in the fail-safe direction,
     because the alternative is a control that cannot see the one leak route
     that matters. Synthea's names carry digit suffixes (``Ernser583``), so
     this is invisible on the corpus and would not be on a hospital extract.
     THE REMEDY IS NOT AN ALLOWLIST: a name in the rendered record is either a
     leak or a coincidence, and only a person looking at that patient can say
     which.
  2. **A SHAPE RULE THAT MATCHES CLINICAL TEXT.** ``_SHAPE_RULES`` is
     deliberately five narrow patterns and not a general "long digit run":
     LOINC codes (``89247-1``), lab values and dates are all digit runs, and a
     rule broad enough to catch an unknown record number would catch them.
     Every rule below was run against all 1,000 rendered corpus summaries and
     fired on none of them; a rule that fires on ordinary clinical text is a
     wrong rule, not a finding.

This module imports NOTHING from the project. That is required rather than
tidy: it is on the render path, and
``oncotriage/run_fingerprint.py:RENDERER_MODULES`` hashes the render path's
transitive closure -- so an import here would pull that module into the
resume-gate digest and into
``tests/test_resume_configuration_fingerprint.py``'s closed round trip. It also
means the guard cannot log; Stage 5 logs on its behalf, which is where the
correlation ID and the stage number are anyway.
"""

import hashlib
import re
from collections import Counter
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple


#------------------------------------------------------------------------------


# ===========================================================================
# THE AGE CAP
# ===========================================================================
#
# A FACT ABOUT AN EXTERNAL STANDARD, so it is a named constant here rather than
# a tunable in oncotriage/config.py -- the same rule that keeps LOINC codes and
# MeSH tree numbers inline. 45 CFR 164.514(b)(2)(i)(C): all elements of dates
# indicative of such age, "except that such ages and elements may be aggregated
# into a single category of age 90 or older". 89 is therefore the last age that
# may be stated exactly, and the cap is a `>` comparison against it.
#
# THE LABEL IS THE REGULATION'S OWN WORDING. "90 or older" is what the text
# says, and a model reading it needs no glossary.
AGE_CAP_YEARS = 89
AGE_CAP_LABEL = "90 or older"

# How many patients this moves is a property of the corpus and not of the code,
# and it was measured rather than assumed: see the pass report for the count at
# the 2026-08-03 snapshot.


# ===========================================================================
# THE PSEUDONYM
# ===========================================================================

PSEUDONYM_PREFIX = "PT-"
"""What the rendered token starts with, so a reader of a stored prompt can see
at a glance that the record is pseudonymous rather than having to prove the
absence of something."""

PSEUDONYM_DOMAIN = "oncotriage/deid/pseudonym/v1"
"""Domain separation. Prefixed to the identity before hashing so the emitted
token is not the patient hash itself -- see the module docstring. The trailing
``v1`` is the derivation's version: changing anything about how a pseudonym is
built changes this string, so two eras of pseudonym cannot be silently
compared."""

PSEUDONYM_HEX_CHARS = 12
"""Length of the hex tail. 12 hex characters is 48 bits: at the 22,000-patient
scale this project generates, the birthday-collision probability is about
8.6e-10, which is below anything else that can go wrong with a run. Shorter
would start to collide; longer buys nothing a reader can use."""

PSEUDONYM_UNKNOWN = PSEUDONYM_PREFIX + "unidentified"
"""What is rendered when no identity was supplied. A NAMED SENTINEL rather than
an empty string or a raise: a caller rendering a hand-built patient record --
which ten test files do -- has no clinical hash to derive from, and refusing
would make this stage impossible to exercise without a real bundle. It is
deliberately not derivable from anything, so it says "this record carries no
identity" and never "this is patient X"."""


def pseudonym_for_identity(identity: Optional[str]) -> str:
    """The stable, opaque token for one patient identity.

    Args:
        identity: the pipeline's own patient identity --
            ``compute_patient_hash(patient_data)``. ``None`` or empty yields
            ``PSEUDONYM_UNKNOWN``.

    Deterministic and pure: the same identity always yields the same token, on
    any machine, in any process, with no key material, no database and no
    filesystem. That is what lets the rendered prompt stay reproducible, which
    the fixture harness and every cross-run comparison depend on.
    """
    if not identity:
        return PSEUDONYM_UNKNOWN
    digest = hashlib.sha256(
        f"{PSEUDONYM_DOMAIN}|{identity}".encode("utf-8")).hexdigest()
    return PSEUDONYM_PREFIX + digest[:PSEUDONYM_HEX_CHARS]


# ===========================================================================
# THE IDENTIFIER VOCABULARY
# ===========================================================================
#
# CLOSED, and closed for a reason a comment cannot enforce on its own: the
# scan's findings are reported by CLASS and never by value, so an operator
# reading a refusal acts on the class name alone. A class invented at a call
# site would reach them as a word nothing defines.

IDENTIFIER_NAME = "name"
IDENTIFIER_GEO = "geo_below_state"
IDENTIFIER_TELECOM = "telecom"
IDENTIFIER_RECORD_NUMBER = "record_number"
IDENTIFIER_GOVERNMENT_ID = "government_id"
IDENTIFIER_INSURANCE_ID = "insurance_id"
IDENTIFIER_URL = "url"
IDENTIFIER_OTHER = "other_identifier"

IDENTIFIER_CLASSES: Tuple[str, ...] = (
    IDENTIFIER_NAME,
    IDENTIFIER_GEO,
    IDENTIFIER_TELECOM,
    IDENTIFIER_RECORD_NUMBER,
    IDENTIFIER_GOVERNMENT_ID,
    IDENTIFIER_INSURANCE_ID,
    IDENTIFIER_URL,
    IDENTIFIER_OTHER,
)
"""Every class a finding may carry. ``address.state`` has NO class here, which
is the ruling: state is permitted and everything below it is not."""

# Which HL7 v2-0203 identifier-type codes name a government identifier rather
# than a record number. Read off the type coding, never guessed from the value:
# a Social Security Number and a Medical Record Number are the same shape.
_GOVERNMENT_ID_TYPE_CODES: FrozenSet[str] = frozenset({
    "SS",    # Social Security Number
    "SB",    # Social Beneficiary Identifier
    "DL",    # Driver's licence number
    "PPN",   # Passport number
    "TAX",   # Tax ID number
})

_INSURANCE_ID_TYPE_CODES: FrozenSet[str] = frozenset({
    "MB",    # Member Number
    "MC",    # Patient's Medicare number
    "MA",    # Patient Medicaid number
    "SN",    # Subscriber Number
    "NIIP",  # National Insurance Payor Identifier
})

_RECORD_NUMBER_TYPE_CODES: FrozenSet[str] = frozenset({
    "MR",    # Medical Record Number
    "PI",    # Patient internal identifier
    "PRN",   # Provider number
    "ACSN",  # Accession ID
})

# FHIR resource types whose ``identifier[]`` entries are about a PERSON. A
# Coverage's identifier is an insurance identifier by construction; a
# Practitioner's name is not the patient's, but it is still a name in the
# patient's record and the ruling names names without qualifying whose.
_INSURANCE_RESOURCE_TYPES: FrozenSet[str] = frozenset({"Coverage"})


# ===========================================================================
# HARVESTING: WHAT THE SOURCE ACTUALLY CARRIES
# ===========================================================================


def _add(inventory: Dict[str, set], cls: str, value: Any) -> None:
    """Record one value under one class, skipping what cannot be scanned for."""
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    inventory.setdefault(cls, set()).add(text)


def _as_list(value: Any) -> List:
    """A FHIR repeating element as a list, or [] for anything that is not one.

    NOT DECORATION. Several of the fields harvested below are repeating in the
    spec and singular in the wild: ``Organization.name`` is a plain STRING, and
    a hand-built or exported bundle can carry a bare dict where a list belongs.
    ``for x in (resource.get("name") or [])`` over a string iterates its
    CHARACTERS -- every one of which fails the ``isinstance(x, dict)`` test
    below, so it is harmless and it is also a per-character loop over every
    organisation name in the bundle. Over a dict it iterates the KEYS, which is
    harmless in the same way and just as meaningless.

    Returning [] makes both cases explicit rather than accidentally safe, which
    is the difference between a guard and a coincidence.
    """
    return value if isinstance(value, list) else []


def _classify_identifier_entry(entry: Dict, resource_type: str) -> str:
    """Which class one FHIR ``identifier[]`` entry belongs to.

    Read off ``type.coding[].code`` -- the HL7 v2-0203 code -- because the
    VALUE cannot be classified: an SSN, an MRN and a member number are all
    digit strings, and guessing from shape is how a government identifier gets
    filed as a record number and reported to an operator under the wrong name.
    An entry with no recognised type is ``IDENTIFIER_OTHER``, which is scanned
    exactly as hard as the rest; the class only decides what the refusal says.
    """
    if resource_type in _INSURANCE_RESOURCE_TYPES:
        return IDENTIFIER_INSURANCE_ID
    codings = _as_list((entry.get("type") or {}).get("coding"))
    for coding in codings:
        if not isinstance(coding, dict):
            continue
        code = str(coding.get("code") or "").strip().upper()
        if code in _GOVERNMENT_ID_TYPE_CODES:
            return IDENTIFIER_GOVERNMENT_ID
        if code in _INSURANCE_ID_TYPE_CODES:
            return IDENTIFIER_INSURANCE_ID
        if code in _RECORD_NUMBER_TYPE_CODES:
            return IDENTIFIER_RECORD_NUMBER
    return IDENTIFIER_OTHER


def harvest_identifiers(bundle: Dict) -> Dict[str, List[str]]:
    """Every direct-identifier value a FHIR bundle carries, by class.

    Walks EVERY resource, not only ``Patient``. A Practitioner's name, an
    Organization's address and a Coverage's member number are all in the
    patient's bundle and all of them are identifiers the ruling names; and a
    leak into free text is exactly as likely to carry a clinician's name as
    the patient's.

    Args:
        bundle: the decoded FHIR bundle. READ, never written.

    Returns:
        ``{class: [value, ...]}`` with the values sorted, so two harvests of
        one bundle are byte-comparable. Absent classes are absent rather than
        empty, so a caller cannot mistake "this bundle carries no phone" for
        "phones were not looked for".

    Never raises on a malformed bundle: every read is a ``.get`` with a type
    check, because a bundle that cannot be harvested must not take down the
    patient it belongs to before the guard has had a chance to run.
    """
    inventory: Dict[str, set] = {}
    for entry in _as_list(bundle.get("entry")):
        if not isinstance(entry, dict):
            continue
        resource = entry.get("resource")
        if not isinstance(resource, dict):
            continue
        resource_type = str(resource.get("resourceType") or "")

        # -- names -----------------------------------------------------------
        for name in _as_list(resource.get("name")):
            if not isinstance(name, dict):
                continue
            _add(inventory, IDENTIFIER_NAME, name.get("family"))
            for part in ("given", "prefix", "suffix"):
                for value in _as_list(name.get(part)):
                    _add(inventory, IDENTIFIER_NAME, value)
            _add(inventory, IDENTIFIER_NAME, name.get("text"))

        # -- telecom ---------------------------------------------------------
        for telecom in _as_list(resource.get("telecom")):
            if isinstance(telecom, dict):
                _add(inventory, IDENTIFIER_TELECOM, telecom.get("value"))

        # -- geography BELOW state -------------------------------------------
        # `state` and `country` are deliberately not harvested: the ruling
        # permits state, and a two-letter token is unscannable in any case.
        for address in _as_list(resource.get("address")):
            if not isinstance(address, dict):
                continue
            for line in _as_list(address.get("line")):
                _add(inventory, IDENTIFIER_GEO, line)
            _add(inventory, IDENTIFIER_GEO, address.get("city"))
            _add(inventory, IDENTIFIER_GEO, address.get("district"))
            _add(inventory, IDENTIFIER_GEO, address.get("postalCode"))
            _add(inventory, IDENTIFIER_GEO, address.get("text"))
            for ext in _as_list(address.get("extension")):
                if not isinstance(ext, dict):
                    continue
                for sub in _as_list(ext.get("extension")):
                    if isinstance(sub, dict) and sub.get("url") in ("latitude",
                                                                    "longitude"):
                        _add(inventory, IDENTIFIER_GEO, sub.get("valueDecimal"))

        # -- identifiers -----------------------------------------------------
        for ident in _as_list(resource.get("identifier")):
            if isinstance(ident, dict):
                _add(inventory,
                     _classify_identifier_entry(ident, resource_type),
                     ident.get("value"))

        # -- the Patient resource's own id, which IS the record number here --
        # Every resource has an ``id``; only the Patient's identifies the
        # person, and on this corpus it is byte-identical to the MRN. The
        # others are intra-bundle references and harvesting them would put
        # 2,610 UUIDs in the inventory for no gain -- _SHAPE_RULES catches a
        # stray UUID of any provenance already.
        if resource_type == "Patient":
            _add(inventory, IDENTIFIER_RECORD_NUMBER, resource.get("id"))

    return {cls: sorted(values) for cls, values in sorted(inventory.items())}


def identifiers_from_parsed_record(patient_data: Dict) -> Dict[str, List[str]]:
    """The identifiers the PARSED record carries, for a caller with no bundle.

    This is the production inventory, and it is short because the parser is
    short: ``patient_id`` is the only direct identifier that survives parsing.
    ``demographics['birth_date']`` is deliberately NOT in it -- the ruling
    keeps full dates, so a birth date in the record is permitted rather than a
    leak, and scanning for it would fail every patient whose record legitimately
    prints a date from the same day.

    WHY THE SHORT INVENTORY IS NOT A WEAK GUARD. It is one of three layers:
    this exact-match list, ``_SHAPE_RULES`` (which is provenance-free and
    catches an SSN, a phone, an email, a URL or a UUID whatever carried it),
    and ``harvest_identifiers`` when a caller has the bundle to hand. The first
    catches a parser that starts carrying ``patient_id`` into a rendered field;
    the second catches free text; the third is the complete answer and needs
    the source.
    """
    inventory: Dict[str, set] = {}
    _add(inventory, IDENTIFIER_RECORD_NUMBER, patient_data.get("patient_id"))
    return {cls: sorted(values) for cls, values in sorted(inventory.items())}


def merge_inventories(*inventories: Optional[Dict[str, Iterable[str]]]
                      ) -> Dict[str, List[str]]:
    """Union several inventories into one, class by class."""
    merged: Dict[str, set] = {}
    for inventory in inventories:
        for cls, values in (inventory or {}).items():
            for value in values:
                _add(merged, cls, value)
    return {cls: sorted(values) for cls, values in sorted(merged.items())}


# ===========================================================================
# THE SHAPE RULES
# ===========================================================================
#
# PROVENANCE-FREE: these fire on the rendered text whatever carried the value,
# so they are the layer that works when no bundle was supplied. Each was run
# against all 1,000 rendered corpus summaries and fired on none of them.
#
# WHAT IS DELIBERATELY ABSENT, because a rule that fires on ordinary clinical
# text is a wrong rule rather than a finding:
#
#   a bare long digit run   LOINC codes are digits with a check digit
#                           ("89247-1"), lab values are digits, and dates are
#                           digits. Any rule wide enough to catch an unknown
#                           record number catches all three.
#   a street-address shape  "959 Davis Bypass" is <number> <word> <word>, and
#                           so is "2 Aspirin Tablet". The address line is in
#                           the harvested inventory instead, where it is
#                           matched exactly and cannot be confused.
#   a personal-name shape   there is no such shape.
_SHAPE_RULES: Tuple[Tuple[str, str, "re.Pattern"], ...] = (
    (IDENTIFIER_GOVERNMENT_ID, "ssn",
     re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    (IDENTIFIER_TELECOM, "phone",
     re.compile(r"\b\d{3}-\d{3}-\d{4}\b")),
    (IDENTIFIER_TELECOM, "email",
     re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    (IDENTIFIER_URL, "url",
     re.compile(r"\bhttps?://[^\s<>\"]+", re.IGNORECASE)),
    (IDENTIFIER_RECORD_NUMBER, "uuid",
     re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)),
)

SHAPE_RULE_NAMES: Tuple[str, ...] = tuple(rule for _, rule, _ in _SHAPE_RULES)
"""The rule names, for a test that pins the set without retyping the patterns."""

_MIN_EXACT_MATCH_CHARS = 4
"""Below this an exact-match scan is noise rather than evidence. ``CA`` is a
state and a tumour marker; ``US`` is a country and a word inside ``ultrasound``
only if boundaries are ignored. Four characters is where a harvested value
starts being distinctive enough that a hit is worth failing a patient over.
Values shorter than this are SKIPPED, and ``skipped_short`` on the result says
how many, so a reader can see the guard's own blind spot rather than infer it.
"""


# ===========================================================================
# THE RECORD
# ===========================================================================

RENDERED_FIELDS: Tuple[str, ...] = (
    "demographics",
    "conditions",
    "medications",
    "observations",
    "procedures",
    "allergies",
    "ecog_performance_status",
    "cancer_stage_observations",
    "cancer_genomic_variants",
    "cancer_metastasis_observations",
)
"""Exactly the keys ``render_patient_record`` reads, and therefore exactly the
keys the de-identified record carries.

THIS TUPLE IS THE GUARANTEE. A field absent from it is absent from the object
the renderer is handed, so no renderer edit can print it -- not because a rule
forbids it but because the name is not in scope. ``patient_id`` is the one key
of ``parse_fhir_bundle``'s output that is deliberately NOT here.

A renderer that starts reading an eleventh key gets a ``KeyError`` on its first
run rather than a silent ``None``, which is the review point this design is
for: adding a field to the prompt means adding it here, and adding it here
means somebody decided it may be disclosed."""

DEMOGRAPHIC_FIELDS: Tuple[str, ...] = ("age", "sex", "race", "ethnicity")
"""Exactly the four demographic keys the renderer prints.

``birth_date``, ``birth_date_precision``, ``age_reference_date``,
``race_source`` and ``ethnicity_source`` are all dropped -- not because any of
them is forbidden (the ruling keeps full dates) but because the renderer does
not read them, and a field that travels to the prompt boundary unread is a
field one edit away from being printed. Minimisation is cheaper than review."""


class DeidentifiedRecord:
    """One patient's record, de-identified, plus what the guard needs.

    ``fields`` is what the renderer is handed. ``inventory`` never travels with
    it and is never serialised: it holds RAW identifier values, which is
    exactly the thing this stage exists to keep out of artifacts, so it lives
    for the length of one patient's evaluation and no longer.

    ``__slots__`` is non-empty and closed, so a caller cannot stash a value on
    a record and a later reader cannot trust a field nothing set.
    """

    __slots__ = ("fields", "pseudonym", "inventory", "age_capped")

    def __init__(self, fields: Dict[str, Any], pseudonym: str,
                 inventory: Dict[str, List[str]], age_capped: bool):
        self.fields = fields
        self.pseudonym = pseudonym
        self.inventory = inventory
        self.age_capped = age_capped

    def __repr__(self) -> str:
        """Names the pseudonym and COUNTS the inventory; never prints a value.

        A debugger rendering locals, a log line formatting the object and a
        bare name at a prompt all reach this, and every one of them is a place
        an identifier must not appear.
        """
        counts = {cls: len(v) for cls, v in self.inventory.items()}
        return (f"<DeidentifiedRecord {self.pseudonym} "
                f"fields={len(self.fields)} inventory={counts} "
                f"age_capped={self.age_capped}>")


DEID_REFUSALS = Counter()
"""How many patients this guard refused to send, keyed by identifier class.

THE AGE_PARSE_FAILURES FOOTING: a module-level Counter, counts only, keyed by
the CLASS and never by the matched value or any clinical text. The whole point
of a refusal is that the value does not travel, and a counter keyed by value
would put it somewhere with a longer life than the prompt it was kept out of.

A process-lifetime tally, not a per-patient field: adding a key to Stage 5's
result dict would move every characterization fixture for something no stage
reads. Registered in ``oncotriage/degradation.py`` so it reaches the run-end
report."""

DEID_AGE_CAPPED = "age_capped"
"""The census key. NOT a degradation -- a capped age is the stage working --
which is why it is counted under its own name and read from the census block
rather than the degradation one."""

DEID_CENSUS = Counter()
"""What the stage DID, as opposed to what it refused. Census, not degradation:
see ``oncotriage/degradation.py``'s two registries and the argument for the
split."""


def _cap_age(age: Any) -> Tuple[Any, bool]:
    """``(rendered_age, was_capped)``.

    ``isinstance(age, bool)`` is excluded before the int test, and THE
    EXCLUSION CHANGES NO OUTCOME AT THE CURRENT CAP -- measured, not assumed.
    ``True`` is 1 and ``False`` is 0, both below 89, so a bool that fell
    through to ``age > AGE_CAP_YEARS`` would compare False and be returned
    unchanged and uncapped, which is exactly what the guard returns. A revert
    harness removed it and the standing test stayed green.

    IT IS KEPT ANYWAY AND THE REASON IS STATED RATHER THAN IMPLIED: this is the
    one place an age is TYPED, and the guard is what says a bool is not an age
    -- so a later edit that lowers the cap, compares the other way, or does
    arithmetic on the value inherits the exclusion instead of rediscovering
    ``True == 1``. It is a type contract, not a live branch, and it must not be
    described as one.

    A ``None`` age is returned unchanged: the renderer already prints
    ``unknown`` for it, and capping an absent age would state a bound nothing
    measured.
    """
    if isinstance(age, bool) or not isinstance(age, int):
        return age, False
    if age > AGE_CAP_YEARS:
        return AGE_CAP_LABEL, True
    return age, False


def deidentify(patient_data: Dict,
               identity: Optional[str] = None,
               source_bundle: Optional[Dict] = None) -> DeidentifiedRecord:
    """The stage. Parsed record in, de-identified record out.

    Args:
        patient_data: ``parse_fhir_bundle``'s output. READ, NEVER MUTATED --
            asserted rather than promised: the returned ``fields`` holds new
            container objects for everything it changes, and the caller's dict
            and every list inside it come back untouched. That is what keeps
            ``compute_patient_hash`` reading the same record it always read.
        identity: the pipeline's patient identity --
            ``compute_patient_hash(patient_data)``. Passed in rather than
            computed here because computing it would mean importing
            ``oncotriage.agent.patient``, which imports this module.
        source_bundle: the decoded FHIR bundle, when the caller has one. Adds
            the FULL identifier inventory to the guard. Optional because the
            production path through the graph holds only ``patient_data``; see
            the module docstring's three layers.

    Returns:
        A ``DeidentifiedRecord``. ``fields`` carries exactly
        ``RENDERED_FIELDS``; the demographics inside it carry exactly
        ``DEMOGRAPHIC_FIELDS``.

    Never raises on a shape it does not recognise. A missing key becomes the
    empty value the renderer already handles, because a patient whose record is
    odd must reach the guard rather than dying above it.
    """
    demographics_in = patient_data.get("demographics") or {}
    age, was_capped = _cap_age(demographics_in.get("age"))
    if was_capped:
        DEID_CENSUS[DEID_AGE_CAPPED] += 1

    demographics = {
        "age": age,
        "sex": demographics_in.get("sex"),
        "race": demographics_in.get("race"),
        "ethnicity": demographics_in.get("ethnicity"),
    }

    # EVERY LIST IS COPIED AT THE TOP LEVEL AND ITS MEMBERS ARE NOT. The copy
    # is what stops a future renderer edit that sorts or pops in place from
    # reaching the caller's parsed record -- which would move
    # compute_patient_hash under it, silently. The MEMBERS are shared on
    # purpose: they are the clinical records themselves, this stage does not
    # rewrite one, and deep-copying 3,660 observation dicts per patient would
    # be real work for a guarantee nothing needs.
    fields: Dict[str, Any] = {"demographics": demographics}
    for key in RENDERED_FIELDS:
        if key == "demographics":
            continue
        value = patient_data.get(key)
        if key == "ecog_performance_status":
            fields[key] = dict(value) if isinstance(value, dict) else value
        else:
            fields[key] = list(value) if isinstance(value, list) else (value or [])

    inventory = merge_inventories(
        identifiers_from_parsed_record(patient_data),
        harvest_identifiers(source_bundle) if source_bundle else None,
    )

    return DeidentifiedRecord(
        fields=fields,
        pseudonym=pseudonym_for_identity(identity),
        inventory=inventory,
        age_capped=was_capped,
    )


# ===========================================================================
# THE GUARD
# ===========================================================================


class IdentifierLeakError(RuntimeError):
    """A rendered patient record carried a direct identifier.

    A ``RuntimeError`` subclass and deliberately NOT a ``ValueError``, on the
    ``UnknownModelPricingError`` / ``IndexVerificationError`` precedent: a stray
    ``except ValueError`` around a prompt build must not be able to eat this.

    THE MESSAGE NAMES THE CLASSES AND NEVER THE VALUES. The exception text
    reaches ``inferences.error``, the console and the structured log -- three
    durable places -- and an exception that quoted the identifier it found
    would write it into all of them, which is worse than the prompt it
    prevented.
    """

    def __init__(self, findings: Sequence["IdentifierFinding"]):
        self.findings = tuple(findings)
        classes = sorted({f.identifier_class for f in self.findings})
        rules = sorted({f.rule for f in self.findings})
        super().__init__(
            f"the rendered patient record carried "
            f"{len(self.findings)} direct-identifier match(es) in "
            f"class(es) {classes} via {rules}; the prompt was not sent. "
            f"The matched values are deliberately not quoted here."
        )


class IdentifierFinding:
    """One hit: the class, the rule that found it, and WHERE -- never WHAT."""

    __slots__ = ("identifier_class", "rule", "start", "length")

    def __init__(self, identifier_class: str, rule: str, start: int,
                 length: int):
        self.identifier_class = identifier_class
        self.rule = rule
        self.start = start
        self.length = length

    def __repr__(self) -> str:
        return (f"<IdentifierFinding {self.identifier_class} via {self.rule} "
                f"at {self.start}+{self.length}>")

    def __eq__(self, other) -> bool:
        return (isinstance(other, IdentifierFinding)
                and (self.identifier_class, self.rule, self.start, self.length)
                == (other.identifier_class, other.rule, other.start,
                    other.length))

    def __hash__(self) -> int:
        return hash((self.identifier_class, self.rule, self.start, self.length))


EXACT_RULE = "exact"
"""The rule name every inventory match reports. One name rather than one per
class, because the CLASS already says what was found and the rule says how."""


def scan_for_identifiers(text: str,
                         inventory: Optional[Dict[str, Iterable[str]]] = None,
                         ) -> Tuple[List[IdentifierFinding], int]:
    """Scan rendered text for direct identifiers. Returns ``(findings, skipped)``.

    Two layers, both always run:

      EXACT      every value in ``inventory``, case-insensitively, at or above
                 ``_MIN_EXACT_MATCH_CHARS``. Catches a name, an address line or
                 a record number that reached the text however it got there.
      SHAPE      ``_SHAPE_RULES``, provenance-free. Catches an SSN, a phone, an
                 email, a URL or a UUID that no inventory knew about -- which
                 is the case that matters when no bundle was supplied.

    ``skipped`` is how many inventory values were below the length floor and
    therefore not looked for. It is RETURNED rather than swallowed so a caller
    can report the guard's own blind spot; a scan that silently declined to
    look for half its inventory would read exactly like a clean one.

    FINDINGS CARRY OFFSETS, NOT TEXT. ``start`` and ``length`` locate the hit
    for a human with the record in front of them and carry nothing to a log.

    Case-insensitive, because a renderer that lower-cases a display is still
    disclosing the name it lower-cased. Substring rather than word-bounded, and
    that is the fail-safe direction: ``959 Davis Bypass`` inside a longer line
    is still an address, and a boundary rule would miss a value glued to
    punctuation.
    """
    findings: List[IdentifierFinding] = []
    skipped = 0
    haystack = text.lower()

    for cls, values in sorted((inventory or {}).items()):
        for value in sorted(values):
            needle = str(value).strip()
            if len(needle) < _MIN_EXACT_MATCH_CHARS:
                skipped += 1
                continue
            start = haystack.find(needle.lower())
            if start >= 0:
                findings.append(
                    IdentifierFinding(cls, EXACT_RULE, start, len(needle)))

    for cls, rule, pattern in _SHAPE_RULES:
        for match in pattern.finditer(text):
            findings.append(IdentifierFinding(cls, rule, match.start(),
                                              match.end() - match.start()))

    # Sorted so two scans of one record report in one order, which is what
    # makes a refusal message comparable across runs.
    findings.sort(key=lambda f: (f.start, f.identifier_class, f.rule))
    return findings, skipped


def assert_no_identifiers(text: str, record: DeidentifiedRecord) -> int:
    """Raise ``IdentifierLeakError`` if the rendered record carries one.

    THE ENFORCEMENT POINT. Called at the prompt boundary, before any model
    call, so a hit costs nothing and sends nothing.

    Returns the number of inventory values skipped for being too short, so the
    caller can report the blind spot. Raises on any finding; there is no
    threshold and no "minor" class, because every class in
    ``IDENTIFIER_CLASSES`` is one the ruling names.

    Each refused class is counted in ``DEID_REFUSALS`` before the raise, so a
    run that lost patients this way says so in its run-end block whatever the
    caller does with the exception.
    """
    findings, skipped = scan_for_identifiers(text, record.inventory)
    if findings:
        for cls in sorted({f.identifier_class for f in findings}):
            DEID_REFUSALS[cls] += 1
        raise IdentifierLeakError(findings)
    return skipped


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug 31 16:00:00 2026

@author: ramyalsaffar
"""
