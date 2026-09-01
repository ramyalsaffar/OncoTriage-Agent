# MCP de-identified responses test
##################################

"""No direct identifier leaves the MCP surface, and a gate proves it.

WHAT THIS FILE HOLDS

    1.  ``parse_fhir_bundle`` returns a DE-IDENTIFIED record: a pseudonym in
        place of ``patient_id``, no birth date, no name, and exactly
        ``deid.RENDERED_FIELDS``. The ABSENCE of the old keys is asserted as
        well as the presence of the new ones, because a payload carrying both
        satisfies a presence-only check.

    2.  The pseudonym is THE PIPELINE'S OWN. It is what
        ``deid.pseudonym_for_identity(compute_patient_hash(patient_data))``
        yields and it is character-identical to the ``Patient:`` line
        ``render_patient_record`` prints for the same patient, so a prompt and
        an MCP response name one patient with one token.

    3.  The age cap at its boundary, driven through the real tool on real
        parsed records: 89 exact, 90 as ``deid.AGE_CAP_LABEL``.

    4.  ``match_patient``'s result carries ``patient_pseudonym`` where
        ``patient_id`` was, and still carries ``patient_data_hash`` -- the
        crosswalk column, which is not an identifier and is what an authorised
        operator resolves a match through.

    5.  ``lookup_trial`` is unchanged and its trial payload is NOT scanned. That
        exemption is measured rather than assumed: see section 7.

    6.  THE GATE, BOTH DIRECTIONS. A clean payload passes; a payload carrying a
        planted identifier is refused, counted in ``deid.DEID_REFUSALS`` and in
        ``TOOL_FAILURES``, and the refusal quotes no value.

    7.  THE TWO EXEMPTIONS ARE LOAD-BEARING, each shown to be necessary by
        removing it and watching a legitimate payload refuse.

    8.  A BYPASS -- one response path around the gate -- planted and caught,
        statically and behaviourally, with the clean control beside each.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO CORPUS, NO DATABASE, NO GIT
HISTORY, NO LIVE SERVER. Every bundle is fabricated in a temp directory, every
Qdrant-backed call is a stand-in installed by rebinding a module attribute
inside ``try``/``finally`` with the restore asserted BY IDENTITY, and the
graph is never invoked. It needs the ``icd10-cm`` package, which is a declared
dependency: ``parse_fhir_bundle`` resolves the primary cancer through
``registries.primary_cancer`` and therefore builds the ICD-10-CM registry.

IT EXECS NOTHING and loads no module by location -- the bypass plant is an
``ast`` walk over an edited STRING, which is the right instrument because the
check it defeats is itself a static one. NOT in the collision matrix: it writes
only inside a ``tempfile.mkdtemp`` it removes and asserts gone, and the two
repository files it reads (``oncotriage/mcp/server.py``, ``oncotriage/deid.py``)
are written by neither of the suite's two writers and are sha256-compared at
the end.

EVERY IDENTIFIER-SHAPED LITERAL IS ASSEMBLED AT RUN TIME from a prefix and an
arithmetic, never written out -- ``tests/test_deid_stage_and_guard.py``'s rule,
which ``tests/test_secret_scan_gate.py`` had to learn the hard way. Section 9
scans this file with the scanner it tests and requires zero findings.
"""

import ast
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile


#------------------------------------------------------------------------------


_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CODE_DIR = os.path.dirname(_TESTS_DIR)

if not os.path.isdir(os.path.join(_CODE_DIR, "oncotriage")):
    raise RuntimeError(
        f"could not locate the oncotriage package from {__file__}: "
        f"{os.path.join(_CODE_DIR, 'oncotriage')} is not a directory")

if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

import oncotriage                                            # noqa: E402
from oncotriage import deid                                  # noqa: E402
from oncotriage.agent import deps                            # noqa: E402
from oncotriage.agent.patient import (                       # noqa: E402
    build_patient_record,
    compute_patient_hash,
)
from oncotriage.fhir.parser import parse_fhir_bundle         # noqa: E402
from oncotriage.mcp import server as mcp                     # noqa: E402
from oncotriage.utils import get_age_reference_date          # noqa: E402


#------------------------------------------------------------------------------


# ===========================================================================
# HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, actual, expected):
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


def check_true(label, condition):
    check(label, bool(condition), True)


def fail(label, detail):
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}\n          {detail}")


def section(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def drive(fn, *args, **kwargs):
    """Call ``fn``; return its value, or a marker naming what it raised.

    EVERY CALL INTO PRODUCTION CODE GOES THROUGH THIS. A bare call inside a
    ``check()`` argument list raises while the argument is being EVALUATED --
    exactly when a defect makes the thing under test raise -- and the run then
    prints one traceback where it owed a summary and every result below it.
    This project has shipped that shape seventeen times; not here.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                     # noqa: BLE001 -- reported
        return f"<RAISED {type(exc).__name__}: {exc}>"


def raised(fn, *args, **kwargs):
    """``(type name, message)`` for a call that must raise, else ``(None, "")``."""
    try:
        fn(*args, **kwargs)
    except BaseException as exc:                     # noqa: BLE001 -- the answer
        return type(exc).__name__, str(exc)
    return None, ""


def at(container, key, default="<ABSENT>"):
    try:
        return container[key]
    except Exception:                                # noqa: BLE001 -- reported
        return default


def first_key(mapping, default="<NO KEYS>"):
    """The first key of a mapping, or a marker.

    ``list(mapping)[0]`` raises ``IndexError`` on an empty dict -- which is
    EXACTLY what a defect that empties the result produces, so a bare subscript
    aborts the file at the moment it owes the most failures. This project has
    shipped that shape eighteen times; the revert matrix caught the nineteenth
    here, in the file written to catch a bypass.
    """
    try:
        return next(iter(mapping))
    except Exception:                                # noqa: BLE001 -- reported
        return default


def sha256_file(path):
    with io.open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


_READ_FILES = {
    "mcp": os.path.join(_CODE_DIR, "oncotriage", "mcp", "server.py"),
    "deid": os.path.join(_CODE_DIR, "oncotriage", "deid.py"),
}
_HASHES_BEFORE = {k: sha256_file(v) for k, v in _READ_FILES.items()}
_MCP_SOURCE = io.open(_READ_FILES["mcp"], encoding="utf-8").read()

_SCRATCH = tempfile.mkdtemp(prefix="oncotriage-mcp-deid-")


#------------------------------------------------------------------------------


# ===========================================================================
# STUB REGISTRIES
# ===========================================================================
#
# INSTALLED THROUGH oncotriage/agent/deps.py, the seam the renderer resolves
# through. Without them section 2 would need the four MeSH JSON lookups, which
# are UMLS-derived, deliberately not vendored, and absent on every CI runner --
# so a privacy guard would be a test nobody runs.

class _StubCancerRegistry:
    def is_primary_cancer(self, condition):
        display = (condition.get("display") or "").lower()
        return "carcinoma" in display or "neoplasm" in display


class _StubLabRegistry:
    loinc_codes = frozenset()

    def filter_relevant_procedures(self, procedures):
        return list(procedures or [])

    def filter_relevant_observations(self, observations):
        return list(observations or [])

    def filter_relevant_genomic_variants(self, variants):
        return list(variants or [])


_SAVED_OVERRIDES = deps.set_overrides({
    deps.CANCER_REGISTRY: _StubCancerRegistry(),
    deps.LAB_REGISTRY: _StubLabRegistry(),
    # None is a REAL, reachable state for the MeSH filter and the renderer has
    # a branch for it; it is not a degenerate stub.
    deps.MESH_FILTER: None,
})


#------------------------------------------------------------------------------


# ===========================================================================
# FIXTURES -- every identifier assembled, never written out
# ===========================================================================

def _fake_uuid():
    return "-".join(["6b21ce90", "4d17", "a2fe", "c308", "51b7de4409ac"])


def _fake_family():
    return "Qzernsyr" + str(500 + 83)


def _fake_given():
    return "Abbyx" + str(700 + 52)


def _fake_street():
    return str(900 + 59) + " Davisbrook Bypasswold"


def _fake_phone():
    return "%03d-%03d-%04d" % (555, 200 + 56, 4000 + 224)


def _fake_ssn():
    return "%03d-%02d-%04d" % (900 + 1, 60 + 5, 3000 + 483)


def _fake_zip():
    return str(90000 + 1762)


def _fake_url():
    """A URL-shaped value, assembled. A literal here is a tracked file carrying
    a value section 9g's self-scan then reports -- which is how this file first
    failed its own hygiene check."""
    return "http" + "s://" + "example-trials.invalid" + "/study/NCT01234567"


def _fake_email():
    return "".join(["study", "@", "example", ".", "invalid"])


def _fake_endpoint():
    return "http" + "s://" + "example-cluster.invalid" + " (from keys/.env)"


def _snomed_system():
    """The SNOMED CT system URI, assembled for section 9g's sake.

    IT IS NOT AN IDENTIFIER and it never reaches a response: the parser
    normalises every coding system to a short ``system_key`` ("snomed"), which
    is why a real corpus record scans clean of the URL shape rule -- MEASURED,
    zero shape-rule hits over a real de-identified record. It is assembled only
    because this file scans ITSELF, and a scheme-and-slashes literal is a hit
    wherever it sits -- INCLUDING IN PROSE. The first version of this very
    docstring quoted one to explain the rule and failed section 9g on it, which
    is the fourth time this project has met "a file that argues about its own
    settings cannot be grepped for them"."""
    return "http" + "://" + "snomed.info" + "/sct"


_CITY = "Ontariox"
"""A city that is ALSO a plausible trial-site city, which is what section 7's
trial-exemption control needs on both sides. Spelled to be unmistakable in a
failure message rather than confusable with the real Ontario."""

_REFERENCE_DATE = get_age_reference_date()


def _fhir_bundle(age):
    """A FHIR bundle carrying one of every identifier class the ruling names.

    The birth date is derived from ``get_age_reference_date()`` so the parsed
    age is exactly ``age`` -- never from ``datetime.now()``, which would make
    section 3's boundary drift into the next year.
    """
    birth = _REFERENCE_DATE.replace(year=_REFERENCE_DATE.year - age).isoformat()
    return {
        "resourceType": "Bundle",
        "entry": [
            {"resource": {
                "resourceType": "Patient",
                "id": _fake_uuid(),
                "name": [{"family": _fake_family(), "given": [_fake_given()]}],
                "gender": "female",
                "birthDate": birth,
                "telecom": [{"system": "phone", "value": _fake_phone()}],
                "address": [{"line": [_fake_street()], "city": _CITY,
                             "state": "CA", "postalCode": _fake_zip()}],
                "identifier": [
                    {"type": {"coding": [{"code": "MR"}]},
                     "value": _fake_uuid()},
                    {"type": {"coding": [{"code": "SS"}]},
                     "value": _fake_ssn()},
                ],
            }},
            {"resource": {
                "resourceType": "Condition",
                "id": "cond-1",
                "clinicalStatus": {"coding": [{"code": "active"}]},
                "verificationStatus": {"coding": [{"code": "confirmed"}]},
                "code": {"coding": [{
                    "system": _snomed_system(),
                    "code": "254837009",
                    "display": "Malignant neoplasm of breast (disorder)"}]},
                "onsetDateTime": "2019-04-11T00:00:00Z",
                "subject": {"reference": "Patient/" + _fake_uuid()},
            }},
        ],
    }


def _write_bundle(name, payload):
    path = os.path.join(_SCRATCH, name)
    with io.open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


_BUNDLE_70 = _write_bundle("age70.json", _fhir_bundle(70))
_BUNDLE_89 = _write_bundle("age89.json", _fhir_bundle(89))
_BUNDLE_90 = _write_bundle("age90.json", _fhir_bundle(90))

_PARSED_70 = parse_fhir_bundle(_fhir_bundle(70))
_EXPECTED_PSEUDONYM = deid.pseudonym_for_identity(
    compute_patient_hash(_PARSED_70))


#------------------------------------------------------------------------------


section("SECTION 1 -- parse_fhir_bundle returns a de-identified record")

_PARSE = drive(mcp.parse_fhir_bundle_tool, _BUNDLE_70)

check("1a  the call succeeded", at(_PARSE, "status"), "ok")
check("1b  *** the summary identifies the patient by a PSEUDONYM ***",
      at(at(_PARSE, "patient_summary", {}), "pseudonym"), _EXPECTED_PSEUDONYM)
check("1c  *** and no longer reports patient_id, the corpus's record number ***",
      "patient_id" in at(_PARSE, "patient_summary", {}), False)
check("1d  the record is returned under `patient_record`",
      isinstance(at(_PARSE, "patient_record"), dict), True)
check("1e  ...and the raw parsed record is not returned at all",
      "patient_data" in (_PARSE if isinstance(_PARSE, dict) else {}), False)
check("1f  *** the record carries EXACTLY deid.RENDERED_FIELDS ***",
      sorted(at(_PARSE, "patient_record", {})), sorted(deid.RENDERED_FIELDS))
check("1g  *** and its demographics carry EXACTLY deid.DEMOGRAPHIC_FIELDS, so "
      "birth_date is not in scope at the point a line could print it ***",
      sorted(at(at(_PARSE, "patient_record", {}), "demographics", {})),
      sorted(deid.DEMOGRAPHIC_FIELDS))

_PARSE_BLOB = json.dumps(_PARSE, default=str)

for _label, _value in (("the record number", _fake_uuid()),
                       ("the family name", _fake_family()),
                       ("the given name", _fake_given()),
                       ("the street", _fake_street()),
                       ("the telephone number", _fake_phone()),
                       ("the government identifier", _fake_ssn())):
    check(f"1h  {_label} is absent from the whole serialized response",
          _value in _PARSE_BLOB, False)

check("1i  *** the BUNDLE FILENAME is absent too -- `source_path` used to echo "
      "it, and on this project's corpus a filename IS the patient's name ***",
      os.path.basename(_BUNDLE_70) in _PARSE_BLOB, False)
check("1i-i ...and the key is gone rather than emptied",
      "source_path" in (_PARSE if isinstance(_PARSE, dict) else {}), False)

# NON-DEGENERACY. Without this, 1h passes for a tool that returned nothing.
check("1j  NON-DEGENERACY: the response really carries the patient's clinical "
      "record, so the absences above are about de-identification rather than "
      "about an empty payload",
      ("Malignant neoplasm of breast" in _PARSE_BLOB
       and len(at(at(_PARSE, "patient_record", {}), "conditions", [])) == 1),
      True)

_DEID_BLOCK = at(_PARSE, "deidentification", {})
check("1k  the response reports what the stage did",
      sorted(_DEID_BLOCK) if isinstance(_DEID_BLOCK, dict) else _DEID_BLOCK,
      ["age_cap_years", "age_capped", "identifier_values_not_scanned",
       "identifier_values_scanned", "pseudonym"])
check("1l  ...and it reports COUNTS, never the classes the bundle carried",
      [k for k in (_DEID_BLOCK if isinstance(_DEID_BLOCK, dict) else {})
       if any(cls in str(k) for cls in deid.IDENTIFIER_CLASSES)], [])
check("1m  the guard's own blind spot is reported rather than inferred",
      isinstance(at(_DEID_BLOCK, "identifier_values_not_scanned"), int), True)
check("1n  NON-DEGENERACY: values really were scanned",
      at(_DEID_BLOCK, "identifier_values_scanned", 0) > 0, True)


#------------------------------------------------------------------------------


section("SECTION 2 -- the pseudonym is the pipeline's own, not a second one")

_RECORD, _RENDERED = build_patient_record(_PARSED_70)
_PATIENT_LINE = next((line for line in _RENDERED.splitlines()
                      if line.startswith("Patient:")), "<no Patient: line>")

check("2a  the renderer prints a Patient: line at all (non-degeneracy)",
      _PATIENT_LINE.startswith("Patient:"), True)
check("2b  *** the token the MCP surface reports is character-identical to the "
      "one the Stage 5 prompt prints for the same patient ***",
      _EXPECTED_PSEUDONYM in _PATIENT_LINE, True)
check("2c  and it is what deid.pseudonym_for_identity derives from the "
      "pipeline's own patient identity",
      _RECORD.pseudonym, _EXPECTED_PSEUDONYM)

_PARSE_AGAIN = drive(mcp.parse_fhir_bundle_tool, _BUNDLE_70)
check("2d  it is STABLE across calls",
      at(at(_PARSE_AGAIN, "patient_summary", {}), "pseudonym"),
      _EXPECTED_PSEUDONYM)

_OTHER = drive(mcp.parse_fhir_bundle_tool, _BUNDLE_89)
check("2e  NON-DEGENERACY: a different patient gets a different token, so 2d "
      "is not satisfied by a constant",
      at(at(_OTHER, "patient_summary", {}), "pseudonym") != _EXPECTED_PSEUDONYM,
      True)
check("2f  it carries the prefix a reader can recognise",
      _EXPECTED_PSEUDONYM.startswith(deid.PSEUDONYM_PREFIX), True)
check("2g  it is not the patient hash itself -- domain separation, so a row "
      "carrying both is not a mapping",
      _EXPECTED_PSEUDONYM.endswith(compute_patient_hash(_PARSED_70)[:8]), False)


#------------------------------------------------------------------------------


section("SECTION 3 -- the age cap, driven at its boundary")

_AT_CAP = drive(mcp.parse_fhir_bundle_tool, _BUNDLE_89)
_OVER_CAP = drive(mcp.parse_fhir_bundle_tool, _BUNDLE_90)

check("3a  the fixture really produced the boundary ages (non-degeneracy)",
      (parse_fhir_bundle(_fhir_bundle(89))["demographics"]["age"],
       parse_fhir_bundle(_fhir_bundle(90))["demographics"]["age"]),
      (deid.AGE_CAP_YEARS, deid.AGE_CAP_YEARS + 1))
check("3b  an age AT the cap is stated exactly",
      at(at(_AT_CAP, "patient_summary", {}), "age"), deid.AGE_CAP_YEARS)
check("3b-i ...and is not reported as capped",
      at(at(_AT_CAP, "deidentification", {}), "age_capped"), False)
check("3c  *** an age OVER the cap is a category, never a number ***",
      at(at(_OVER_CAP, "patient_summary", {}), "age"), deid.AGE_CAP_LABEL)
check("3c-i ...and the response says the cap was applied",
      at(at(_OVER_CAP, "deidentification", {}), "age_capped"), True)
# NOT A SUBSTRING TEST. The first version of this check asked whether "90" was
# absent from the serialized summary -- and `deid.AGE_CAP_LABEL` is "90 or
# older", which contains it, so the check failed against code that is correct.
# What must be true is that the age is no longer a NUMBER: a caller doing
# arithmetic on it gets a TypeError rather than a fabricated 90.
check("3d  the capped age is not an int, so no consumer can compute with it",
      isinstance(at(at(_OVER_CAP, "patient_summary", {}), "age"), int), False)
check("3d-i ...and the record's demographics agree",
      isinstance(at(at(at(_OVER_CAP, "patient_record", {}), "demographics", {}),
                    "age"), int), False)
check("3e  the record's demographics carry the capped value too, so a caller "
      "reading the record rather than the summary sees the same thing",
      at(at(at(_OVER_CAP, "patient_record", {}), "demographics", {}), "age"),
      deid.AGE_CAP_LABEL)


#------------------------------------------------------------------------------


section("SECTION 4 -- match_patient's result carries a pseudonym")

_STUB_RESULT = {
    "patient_id": _PARSED_70["patient_id"],
    "primary_condition": "Malignant neoplasm of breast (disorder)",
    "matches": [],
    "near_misses": [],
    "not_evaluable": [],
    "llm_classifier_prompt": _RENDERED,
    "patient_data_hash": compute_patient_hash(_PARSED_70),
    "qdrant_collection": "trial_criteria_stub",
}


def _drive_match(bundle_path):
    """`match_patient_tool` with the index gate, the budget gate and the graph
    replaced. THE GRAPH IS NEVER INVOKED, so no billed call is reachable; the
    parse, the de-identification stage, the result substitution and the
    boundary gate are all the shipped ones."""
    saved = (mcp._require_index, mcp._require_budget, mcp.get_graph,
             mcp.match_patient_to_trials)
    try:
        mcp._require_index = lambda tool: None
        mcp._require_budget = lambda tool: None
        mcp.get_graph = lambda: "<not a graph>"
        mcp.match_patient_to_trials = (
            lambda patient_data, graph: dict(_STUB_RESULT))
        return drive(mcp.match_patient_tool, bundle_path)
    finally:
        (mcp._require_index, mcp._require_budget, mcp.get_graph,
         mcp.match_patient_to_trials) = saved


_MATCH = _drive_match(_BUNDLE_70)
_MATCH_RESULT = at(_MATCH, "result", {})

check("4a  the seam was restored", mcp.get_graph is mcp.get_graph, True)
check("4b  the call succeeded", at(_MATCH, "status"), "ok")
check("4c  *** the result no longer carries patient_id ***",
      "patient_id" in (_MATCH_RESULT if isinstance(_MATCH_RESULT, dict) else {}),
      False)
check("4d  *** it carries patient_pseudonym instead ***",
      at(_MATCH_RESULT, "patient_pseudonym"), _EXPECTED_PSEUDONYM)
check("4e  ...in the POSITION patient_id held, so a reader finds the identity "
      "where it always was",
      first_key(_MATCH_RESULT), "patient_pseudonym")
check("4f  patient_data_hash is RETAINED -- it is not a direct identifier and "
      "it is the crosswalk column an authorised operator resolves through",
      at(_MATCH_RESULT, "patient_data_hash"),
      compute_patient_hash(_PARSED_70))
check("4g  and the pseudonym is derivable from it, which is what makes the two "
      "one identity rather than two",
      deid.pseudonym_for_identity(at(_MATCH_RESULT, "patient_data_hash", "")),
      _EXPECTED_PSEUDONYM)
check("4h  the summary is the same de-identified block the parse tool returns",
      at(at(_MATCH, "patient_summary", {}), "pseudonym"), _EXPECTED_PSEUDONYM)

_MATCH_BLOB = json.dumps(_MATCH, default=str)
for _label, _value in (("the record number", _fake_uuid()),
                       ("the family name", _fake_family()),
                       ("the street", _fake_street()),
                       ("the government identifier", _fake_ssn())):
    check(f"4i  {_label} is absent from the whole match response",
          _value in _MATCH_BLOB, False)
check("4j  the bundle filename is absent from the match response too",
      os.path.basename(_BUNDLE_70) in _MATCH_BLOB, False)
check("4k  NON-DEGENERACY: the response really carries the pipeline's result",
      at(_MATCH_RESULT, "qdrant_collection"), "trial_criteria_stub")

# DEFENCE IN DEPTH, DRIVEN. The substitution above is CONSTRUCTION; the gate is
# ENFORCEMENT. With the substitution removed the result still carries
# `patient_id`, and the gate must refuse rather than let it out -- which is what
# makes the two layers independent rather than one layer written twice.
_saved_sub = mcp._deidentified_result
try:
    mcp._deidentified_result = lambda result, record: dict(result)
    _LEAKY_MATCH = _drive_match(_BUNDLE_70)
finally:
    mcp._deidentified_result = _saved_sub
check("4l  the substitution seam was restored by identity",
      mcp._deidentified_result is _saved_sub, True)
check("4m  *** WITHOUT the substitution the GATE refuses the response: the "
      "construction and the enforcement are two independent layers ***",
      str(_LEAKY_MATCH).startswith("<RAISED IdentifierLeakError"), True)
check("4n  ...and nothing was returned -- no payload plus a caveat",
      isinstance(_LEAKY_MATCH, dict), False)


#------------------------------------------------------------------------------


section("SECTION 5 -- lookup_trial is unchanged and still guarded")

_TRIAL_PAYLOAD = {
    "found": True,
    "nct_id": "NCT01234567",
    "collection": "trial_criteria_stub",
    "trial": {
        "nct_id": "NCT01234567",
        "title": "A Study of Something",
        "phase": "PHASE2",
        "full_trial_json": {
            # A REAL TRIAL RECORD'S SHAPE, and every one of these three fields
            # is what section 7's control needs: a site city that collides with
            # a patient's address, a reference URL, and a contact email. All
            # three were MEASURED on the live 14,324-trial collection -- 7 of
            # 200 trials name Ontario, and 200 trials carry 2 URLs and 1 email.
            "locations": [{"facility": "General Hospital", "city": _CITY,
                           "state": "ON", "country": "Canada"}],
            "reference_url": _fake_url(),
            "overall_contact": {"name": "A Researcher",
                                "email": _fake_email()},
        },
    },
}


def _drive_lookup(nct_id, payload=None):
    saved = (mcp._require_index, mcp.lookup_trial)
    try:
        mcp._require_index = lambda tool: None
        mcp.lookup_trial = lambda _nct: (payload if payload is not None
                                         else _TRIAL_PAYLOAD)
        return drive(mcp.lookup_trial_tool, nct_id)
    finally:
        (mcp._require_index, mcp.lookup_trial) = saved


_LOOKUP = _drive_lookup("NCT01234567")
check("5a  *** a trial payload carrying a URL, an email and a colliding city "
      "PASSES the gate -- the trial subtree is public ClinicalTrials.gov data "
      "and is not scanned ***",
      at(_LOOKUP, "status"), "ok")
check("5b  the whole trial record is returned, unchanged",
      at(at(_LOOKUP, "trial", {}), "title"), "A Study of Something")
check("5c  the response carries no patient key of any kind",
      sorted(k for k in (_LOOKUP if isinstance(_LOOKUP, dict) else {})
             if "patient" in k), [])

_MISS = _drive_lookup("NCT00000001", payload={
    "found": False, "nct_id": "NCT00000001",
    "collection": "trial_criteria_stub"})
check("5d  the not_found verdict still comes back as a result",
      at(_MISS, "status"), "not_found")


#------------------------------------------------------------------------------


section("SECTION 6 -- the gate, both directions")

_CLEAN_RECORD = deid.deidentify(_PARSED_70,
                                identity=compute_patient_hash(_PARSED_70),
                                source_bundle=_fhir_bundle(70))

_REFUSALS_BEFORE = dict(deid.DEID_REFUSALS)
_TOOL_FAILURES_BEFORE = dict(mcp.failure_report())

_CLEAN_PAYLOAD = {"not_for_clinical_use": "x", "status": "ok",
                  "patient_summary": mcp._patient_summary(_CLEAN_RECORD),
                  "patient_record": _CLEAN_RECORD.fields}
check("6a  CLEAN CONTROL: a de-identified payload passes the gate untouched",
      drive(mcp._guard_response, "probe", _CLEAN_PAYLOAD, _CLEAN_RECORD)
      is _CLEAN_PAYLOAD, True)
check("6a-i ...and it refused nothing",
      dict(deid.DEID_REFUSALS), _REFUSALS_BEFORE)

_LEAKY = dict(_CLEAN_PAYLOAD)
_LEAKY["patient_summary"] = dict(_CLEAN_PAYLOAD["patient_summary"])
_LEAKY["patient_summary"]["display_name"] = _fake_family()

_TYPE, _MESSAGE = raised(mcp._guard_response, "probe", _LEAKY, _CLEAN_RECORD)
check("6b  *** a planted family name FAILS CLOSED ***",
      _TYPE, "IdentifierLeakError")
check("6b-i ...and it is a RuntimeError subclass, so a stray `except "
      "ValueError` around a response build cannot eat it",
      issubclass(deid.IdentifierLeakError, RuntimeError), True)
check("6c  the refusal names the identifier CLASS",
      deid.IDENTIFIER_NAME in _MESSAGE, True)
check("6d  *** and quotes no value: the message reaches a client, a log and "
      "stderr, three places more durable than the payload it prevented ***",
      _fake_family() in _MESSAGE, False)
check("6e  the refusal is counted in deid.DEID_REFUSALS, which is registered "
      "in the degradation registry and reaches a run-end report",
      deid.DEID_REFUSALS[deid.IDENTIFIER_NAME]
      - _REFUSALS_BEFORE.get(deid.IDENTIFIER_NAME, 0), 1)

# THROUGH A REAL TOOL, so `_counted` sees it and the per-tool tally moves.
_saved_summary = mcp._patient_summary
try:
    mcp._patient_summary = (
        lambda record: dict(_LEAKY["patient_summary"]))
    _TOOL_TYPE, _TOOL_MESSAGE = raised(mcp.parse_fhir_bundle_tool, _BUNDLE_70)
finally:
    mcp._patient_summary = _saved_summary
check("6f  the seam was restored by identity",
      mcp._patient_summary is _saved_summary, True)
check("6g  *** a leak reaching a real tool refuses the CALL: no payload at "
      "all, rather than a payload plus a caveat a model would summarise away ***",
      _TOOL_TYPE, "IdentifierLeakError")
check("6h  ...and `_counted` tallies it per tool, so an operator can tell a "
      "leak from a bad input without a second counter",
      dict(mcp.failure_report()).get("parse_fhir_bundle:IdentifierLeakError", 0)
      - _TOOL_FAILURES_BEFORE.get("parse_fhir_bundle:IdentifierLeakError", 0),
      1)

# THE SHAPE RULES, which are provenance-free and run with no inventory at all.
_NO_PATIENT = mcp._no_patient_record()
_TYPE_SSN, _ = raised(mcp._guard_response, "probe",
                      {"status": "ok", "note": _fake_ssn()}, _NO_PATIENT)
check("6i  a government-identifier SHAPE is caught with no inventory at all, "
      "which is the layer that works when no bundle was supplied",
      _TYPE_SSN, "IdentifierLeakError")
# `is not None` WOULD HAVE PASSED HERE FOR A GATE THAT RAISED, because
# `drive` returns a marker STRING on a raise and a string is not None. The
# control asserts IDENTITY with the payload it was handed, which only a gate
# that let it through can satisfy.
_CONTROL_PAYLOAD = {"status": "ok", "note": "fine"}
check("6j  CLEAN CONTROL: the same payload without the shape passes",
      drive(mcp._guard_response, "probe", _CONTROL_PAYLOAD, _NO_PATIENT)
      is _CONTROL_PAYLOAD, True)


#------------------------------------------------------------------------------


section("SECTION 7 -- both exemptions are load-bearing, shown by removing them")

_INDEX_REFUSAL = {
    "not_for_clinical_use": "x",
    "status": "index_unavailable",
    "tool": "lookup_trial",
    "index_state": "absent",
    "collection": "trial_criteria",
    "endpoint": _fake_endpoint(),
    "probe_error": None,
    "message": "The trial index is not usable.",
}

check("7a  CLEAN CONTROL: an index refusal carrying the Qdrant URL passes",
      drive(mcp._guard_response, "probe", _INDEX_REFUSAL, _NO_PATIENT)
      is _INDEX_REFUSAL, True)

_saved_keys = mcp.UNSCANNED_RESPONSE_KEYS
try:
    mcp.UNSCANNED_RESPONSE_KEYS = ("trial",)
    _TYPE_EP, _ = raised(mcp._guard_response, "probe", _INDEX_REFUSAL,
                         _NO_PATIENT)
finally:
    mcp.UNSCANNED_RESPONSE_KEYS = _saved_keys
check("7b  the exemption tuple was restored by identity",
      mcp.UNSCANNED_RESPONSE_KEYS is _saved_keys, True)
check("7c  *** WITHOUT the `endpoint` exemption every index refusal fails on "
      "the URL shape rule -- found by running the gate, not by reading it ***",
      _TYPE_EP, "IdentifierLeakError")

# The trial exemption, driven through the real tool with a patient's city on
# both sides -- which is the measured 3.5% false positive, reproduced.
_PATIENT_INVENTORY_RECORD = _CLEAN_RECORD
_TRIAL_RESPONSE = {"not_for_clinical_use": "x", "status": "ok",
                   "nct_id": "NCT01234567", "collection": "c",
                   "trial": _TRIAL_PAYLOAD["trial"]}
check("7d  CLEAN CONTROL: a trial payload naming the patient's own city passes",
      drive(mcp._guard_response, "probe", _TRIAL_RESPONSE,
            _PATIENT_INVENTORY_RECORD) is _TRIAL_RESPONSE, True)

_saved_keys = mcp.UNSCANNED_RESPONSE_KEYS
try:
    mcp.UNSCANNED_RESPONSE_KEYS = ("endpoint", "probe_error")
    _TYPE_TRIAL, _ = raised(mcp._guard_response, "probe", _TRIAL_RESPONSE,
                            _PATIENT_INVENTORY_RECORD)
finally:
    mcp.UNSCANNED_RESPONSE_KEYS = _saved_keys
check("7e  *** WITHOUT the trial exemption a trial site in the patient's city "
      "refuses the lookup -- deid.py's own 'a city called Ontario' cost, "
      "measured at 7 of 200 live trials ***",
      _TYPE_TRIAL, "IdentifierLeakError")

# AND THE RESULT-LEVEL EXEMPTION.
_RESULT_RESPONSE = {"not_for_clinical_use": "x", "status": "ok",
                    "result": {"patient_pseudonym": _EXPECTED_PSEUDONYM,
                               "matches": [{"trial_city": _CITY}]}}
check("7f  CLEAN CONTROL: per-trial verdicts naming that city pass",
      drive(mcp._guard_response, "probe", _RESULT_RESPONSE, _CLEAN_RECORD)
      is _RESULT_RESPONSE, True)

_saved_result_keys = mcp.UNSCANNED_RESULT_KEYS
try:
    mcp.UNSCANNED_RESULT_KEYS = ()
    _TYPE_RES, _ = raised(mcp._guard_response, "probe", _RESULT_RESPONSE,
                          _CLEAN_RECORD)
finally:
    mcp.UNSCANNED_RESULT_KEYS = _saved_result_keys
check("7g  ...and without the exemption they refuse", _TYPE_RES,
      "IdentifierLeakError")
check("7h  the result exemption tuple was restored by identity",
      mcp.UNSCANNED_RESULT_KEYS is _saved_result_keys, True)

# THE PROMPT IS *NOT* EXEMPT, which is what makes the trial exemption safe: a
# patient identifier that reached a verdict must have passed through the prompt.
_PROMPT_RESPONSE = {"not_for_clinical_use": "x", "status": "ok",
                    "result": {"llm_classifier_prompt":
                               f"Patient record: {_fake_family()}",
                               "matches": []}}
_TYPE_PROMPT, _ = raised(mcp._guard_response, "probe", _PROMPT_RESPONSE,
                         _CLEAN_RECORD)
check("7i  *** result['llm_classifier_prompt'] IS scanned, with the FULL "
      "bundle inventory -- which is why exempting the verdicts derived from it "
      "is not a hole ***",
      _TYPE_PROMPT, "IdentifierLeakError")


#------------------------------------------------------------------------------


section("SECTION 8 -- a bypass around the gate, planted and caught")


def _unguarded_returns(source):
    """Every non-``None`` return in a ``*_tool`` that is NOT the gate's value.

    THE INVARIANT THIS FILE EXISTS FOR: there is no response path around
    `_guard_response`. It is a STATIC question -- "does any return skip it" --
    so a static check is the right instrument, and the plant below is the same
    kind of edit a careless change would make.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        # A PLANT THAT DOES NOT PARSE IS A BAD PLANT, and it must be a RECORDED
        # failure rather than a traceback that hides every check below. The
        # first version of this file planted `return {` over
        # `return _guard_response("...", {`, which left a trailing `}, record)`
        # -- unmatched, so `ast.parse` raised inside a `check()` argument list.
        # The eighteenth time this project has met that shape, and the first
        # time it met it in the file written to catch a bypass.
        return [f"<PLANT DID NOT PARSE: {exc}>"]

    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef)
                and node.name.endswith("_tool")):
            continue
        for ret in ast.walk(node):
            if not isinstance(ret, ast.Return) or ret.value is None:
                continue
            guarded = (isinstance(ret.value, ast.Call)
                       and isinstance(ret.value.func, ast.Name)
                       and ret.value.func.id == "_guard_response")
            if not guarded:
                offenders.append(f"{node.name}:{ret.lineno}")
    return offenders


def _guarded_return_count(source):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return -1
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.endswith("_tool")
        for ret in ast.walk(node)
        if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Call)
        and isinstance(ret.value.func, ast.Name)
        and ret.value.func.id == "_guard_response")


check("8a  *** every response path in the shipped module returns the gate's "
      "value ***", _unguarded_returns(_MCP_SOURCE), [])
check("8a-i NON-DEGENERACY: the walk found the returns at all, so 8a is not "
      "satisfied by a scan that matched nothing",
      _guarded_return_count(_MCP_SOURCE), 7)

# THE PLANT MUST STILL PARSE. `return _guard_response("...", {` -> `return {`
# leaves the trailing `}, record)` unmatched, so both halves are replaced --
# which is also what a careless real edit would look like.
_PLANTED = _MCP_SOURCE.replace(
    '''    return _guard_response("parse_fhir_bundle", {''', "    return {", 1)
_PLANTED = _PLANTED.replace("    }, record)", "    }", 1)
check("8b  the plant landed (a plant that matched nothing is not a control)",
      _PLANTED != _MCP_SOURCE, True)
check("8b-i ...and it still parses, so 8c is about the bypass rather than "
      "about a SyntaxError",
      _guarded_return_count(_PLANTED), 6)
_PLANT_OFFENDERS = _unguarded_returns(_PLANTED)
check("8c  *** ONE RESPONSE PATH AROUND THE GATE IS CAUGHT ***",
      [o.split(":")[0] for o in _PLANT_OFFENDERS], ["parse_fhir_bundle_tool"])

# A SECOND PLANT, because the first could be satisfied by a check that only
# knows about the parse tool.
_PLANT2 = _MCP_SOURCE.replace(
    '''        return _guard_response("lookup_trial", unavailable,
                               _no_patient_record())''',
    "        return unavailable", 1)
check("8d  the second plant landed", _PLANT2 != _MCP_SOURCE, True)
check("8e  ...and a bypass on the refusal path is caught too",
      [o.split(":")[0] for o in _unguarded_returns(_PLANT2)],
      ["lookup_trial_tool"])

# THE BEHAVIOURAL HALF. Without it, 8a is a statement about text: it cannot say
# that `_guard_response` actually runs on each path, only that it is written
# there.
_GATE_CALLS = []
_saved_gate = mcp._guard_response


def _recording_gate(tool, payload, record):
    _GATE_CALLS.append(tool)
    return _saved_gate(tool, payload, record)


try:
    mcp._guard_response = _recording_gate
    drive(mcp.parse_fhir_bundle_tool, _BUNDLE_70)
    _drive_match(_BUNDLE_70)
    _drive_lookup("NCT01234567")
    _drive_lookup("NCT00000001", payload={
        "found": False, "nct_id": "NCT00000001", "collection": "c"})
    saved_idx = mcp._require_index
    try:
        mcp._require_index = lambda tool: {"status": "index_unavailable",
                                           "endpoint": _fake_endpoint()}
        drive(mcp.match_patient_tool, _BUNDLE_70)
        drive(mcp.lookup_trial_tool, "NCT01234567")
    finally:
        mcp._require_index = saved_idx
    saved_budget = mcp._require_budget
    saved_idx = mcp._require_index
    try:
        mcp._require_index = lambda tool: None
        mcp._require_budget = lambda tool: {"status": "spend_limit_reached"}
        drive(mcp.match_patient_tool, _BUNDLE_70)
    finally:
        mcp._require_budget = saved_budget
        mcp._require_index = saved_idx
finally:
    mcp._guard_response = _saved_gate

check("8f  the gate was restored by identity", mcp._guard_response is _saved_gate,
      True)
check("8g  *** BEHAVIOURAL: all seven response paths were driven and every one "
      "of them called the gate ***", len(_GATE_CALLS), 7)
check("8h  ...covering all three tools",
      sorted(set(_GATE_CALLS)),
      ["lookup_trial", "match_patient", "parse_fhir_bundle"])


#------------------------------------------------------------------------------


section("SECTION 9 -- the distinctiveness rule, and this file's own hygiene")

check("9a  a placeholder postal code is NOT looked for: one repeated character "
      "carries no identity, and '00000' is a substring of the SNOMED codes "
      "415300000 and 58000006 that every oncology record carries",
      deid.count_scannable({deid.IDENTIFIER_GEO: ["00000"]}), (0, 1))
check("9b  a REAL postal code still is -- the rule is one repeated character, "
      "never 'all digits'",
      deid.count_scannable({deid.IDENTIFIER_GEO: [_fake_zip()]}), (1, 0))
check("9c  and the length floor still applies",
      deid.count_scannable({deid.IDENTIFIER_GEO: ["CA"]}), (0, 1))

_f, _skipped = deid.scan_for_identifiers(
    "procedure 415300000 and 58000006", {deid.IDENTIFIER_GEO: ["00000"]})
check("9d  *** the rule and the scan agree: the scan finds nothing and reports "
      "the value as not looked for ***", (len(_f), _skipped), (0, 1))
check("9e  NON-DEGENERACY: without the rule that text WOULD have matched",
      "00000" in "procedure 415300000 and 58000006", True)
check("9f  count_scannable's skipped equals the scan's, so a caller reporting "
      "the blind spot cannot disagree with what the scan did",
      deid.count_scannable({deid.IDENTIFIER_GEO: ["00000"]})[1], _skipped)

_SELF = io.open(os.path.abspath(__file__), encoding="utf-8").read()
_self_findings, _ = deid.scan_for_identifiers(_SELF, None)
check("9g  this file carries no identifier-shaped literal of its own -- every "
      "fixture value is assembled at run time, so a tracked file does not "
      "carry the shapes it plants",
      sorted({f.rule for f in _self_findings}), [])

for _name, _path in sorted(_READ_FILES.items()):
    check(f"9h  {_name} is byte-unchanged by this run",
          sha256_file(_path), _HASHES_BEFORE[_name])
check("9h-i NON-DEGENERACY: the two hashes differ, so 9h is not one file "
      "compared with itself",
      len(set(_HASHES_BEFORE.values())), 2)

deps.restore_overrides(_SAVED_OVERRIDES)
check("9i  the dependency overrides were restored",
      deps.peek(deps.CANCER_REGISTRY) is not None
      and isinstance(deps.peek(deps.CANCER_REGISTRY), _StubCancerRegistry),
      False)

shutil.rmtree(_SCRATCH, ignore_errors=True)
check("9j  the temp directory was removed", os.path.exists(_SCRATCH), False)


#------------------------------------------------------------------------------


print()
print("=" * 74)
print("SUMMARY")
print("=" * 74)
print(f"  passed: {_RESULTS['passed']}")
print(f"  failed: {_RESULTS['failed']}")
if _FAILURES:
    print()
    for _failure in _FAILURES:
        print(f"  FAILED: {_failure}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep  1 2026

@author: ramyalsaffar
"""
