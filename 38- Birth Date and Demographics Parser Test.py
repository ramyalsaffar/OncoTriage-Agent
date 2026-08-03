# Birth Date and Demographics Parser Test
#########################################

"""
Birth Date and Demographics Parser Test

Covers the four defects in File 07's demographics path (item 12):

  1. birthDate was parsed with datetime.strptime(value, '%Y-%m-%d'). FHIR types
     Patient.birthDate as `date`, whose value is legally YYYY, YYYY-MM or
     YYYY-MM-DD, and real exports also ship a full ISO dateTime in the field.
     Three of those four shapes raised, and the exception propagated out of
     parse_fhir_bundle and failed the entire bundle. HIPAA Safe Harbor
     de-identification produces the year-only form by design.

  2. Age was computed from datetime.now(), so the same bundle parsed on two
     days produced two different ages. Age is printed into the Stage 5 system
     prompt, while compute_patient_hash() (File 13) keys on birth_date and
     cannot observe the clock: two runs could share a patient_data_hash — this
     project's "identical input" guarantee — and still send GPT-4o different
     prompt text. Age is now computed against DATA_SNAPSHOT_DATE (File 03) and
     that reference date is recorded on every run.

  3. The Stage 5 prompt's own "Reference date:" line was date.today(), so every
     washout window the model reasoned over ("no platinum within 6 months")
     silently widened as the clock advanced, against a patient corpus that was
     frozen at generation time.

  4. Race and ethnicity were read as extension[0].get('valueCoding'). The US
     Core race extension's sub-extensions are an unordered set keyed by url
     (ombCategory, detailed, text), and `text` carries valueString, not
     valueCoding — so a bundle that serializes text first silently produced
     "unknown" for every patient in that export.

Covers:
    1. parse_partial_date (File 02) accepts year, year-month, full date, ISO
       datetime with Z / numeric offset / fractional seconds, and reports the
       precision it actually got.
    2. Out-of-range and junk values degrade or return None with a label —
       never raise.
    3. get_age_reference_date() returns DATA_SNAPSHOT_DATE and refuses to fall
       back to the clock when the constant is unusable.
    4. _calculate_age is clock-independent: its default equals its result
       against an explicitly passed DATA_SNAPSHOT_DATE, its birthday boundary
       is exact, and STRUCTURAL — neither it nor _parse_demographics contains
       a now()/today() call.
    5. _parse_demographics survives every birthDate shape, and records the
       precision and the reference date it used.
    6. Race and ethnicity are read by sub-extension url: text-first ordering,
       ombCategory in any position, multi-category race, detailed-only
       fallback, and a missing extension.
    7. END TO END — parse_fhir_bundle() on a bundle whose birthDate is
       year-only returns a patient instead of raising.
    8. STRUCTURAL — File 13 stamps age_reference_date onto every terminal
       result via _pipeline_provenance(), and no longer prints date.today()
       into the Stage 5 prompt.
    9. END TO END — log_inference() writes age_reference_date and
       birth_date_precision into a throwaway database.

No network and no LLM: File 13 is inspected as source rather than executed, so
no model or Qdrant client is touched. The database is a temporary file; the
real inferences.db is never opened — File 14 is exec'd after inferences_path is
repointed, so it is not listed in the chain below.

Run from terminal (or F5 in Spyder):
    python "38- Birth Date and Demographics Parser Test.py"

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
_code_dir = "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code/"

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

# 14 is deliberately NOT chained: it connects at load time, and this test
# repoints inferences_path at a temporary file first (see below).
exec_chain(
    ["03- Config.py", "07- FHIR Parser.py"],
    caller_file=_code_dir + "38- Birth Date and Demographics Parser Test.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 03 → 07",
)


#------------------------------------------------------------------------------


import ast
import shutil
import tempfile
import textwrap


# ===========================================================================
# MINIMAL ASSERTION HARNESS
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
        _FAILURES.append(
            f"{label}\n          expected: {expected}\n          actual:   {actual}"
        )
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def check_no_raise(label: str, fn):
    """Call fn(); pass if it returns, fail with the exception if it raises.

    The defect under test is an exception escaping the parser, so "did not
    raise" has to be an assertion in its own right rather than an implicit
    property of the test run.
    """
    try:
        value = fn()
    except Exception as exc:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          raised: {type(exc).__name__}: {exc}")
        print(f"  FAIL  {label}")
        print(f"          raised: {type(exc).__name__}: {exc}")
        return None
    _RESULTS["passed"] += 1
    print(f"  PASS  {label}")
    return value


def check_raises(label: str, fn, expected) -> None:
    """Assert fn() raises `expected`; record the outcome on every path.

    Refusing to fall back is the assertion here, so "raised the right type" has
    to be recorded as a pass rather than inferred from the absence of a report.
    """
    try:
        fn()
    except expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
        return
    except Exception as exc:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected: {expected.__name__}"
                         f"\n          actual:   {type(exc).__name__}: {exc}")
        print(f"  FAIL  {label} (raised {type(exc).__name__}, not {expected.__name__})")
        return
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          expected: {expected.__name__}"
                     f"\n          actual:   returned without raising")
    print(f"  FAIL  {label} (returned without raising)")


# ===========================================================================
# THROWAWAY DATABASE
# ===========================================================================
# inferences_path is rebound BEFORE File 14 is exec'd, because File 14 opens
# its connection and creates its tables at load time. The real inferences.db is
# never touched by this test.

_TMP_DIR = tempfile.mkdtemp(prefix="oncotriage_birthdate_")
inferences_path = os.path.join(_TMP_DIR, "inferences_test.db")

with open(_code_dir + "14- Database Logger.py") as _fh:
    exec(_fh.read(), globals())


# ===========================================================================
# FIXTURES
# ===========================================================================

# A date far enough from every birthDate below that a one-day error is visible.
REF = date(2026, 3, 11)


def patient_resource(birth_date, extensions=None) -> dict:
    """Minimal FHIR Patient resource carrying the fields under test."""
    resource = {
        "resourceType": "Patient",
        "id":           "birthdate-test-patient",
        "gender":       "male",
        "birthDate":    birth_date,
    }
    if extensions is not None:
        resource["extension"] = extensions
    return resource


RACE_URL      = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-race"
ETHNICITY_URL = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity"


def omb(display, code="2106-3"):
    return {"url": "ombCategory",
            "valueCoding": {"system": "urn:oid:2.16.840.1.113883.6.238",
                            "code": code, "display": display}}


def detailed(display, code="1004-1"):
    return {"url": "detailed",
            "valueCoding": {"system": "urn:oid:2.16.840.1.113883.6.238",
                            "code": code, "display": display}}


def text_ext(value):
    return {"url": "text", "valueString": value}


# ===========================================================================
# 1. PARTIAL DATE SHAPES
# ===========================================================================

print("\n" + "=" * 70)
print("1. parse_partial_date accepts every shape FHIR permits")
print("=" * 70)

# (raw value, expected date, expected precision)
_SHAPES = [
    ("1965-04-12",                    date(1965, 4, 12),  "day"),
    ("1965-04",                       date(1965, 4, 15),  "month"),   # day -> mid-month anchor
    ("1965",                          date(1965, 7, 15),  "year"),    # month/day -> mid-year anchor
    ("1965-04-12T00:00:00Z",          date(1965, 4, 12),  "day"),
    ("1965-04-12T13:45:02-07:00",     date(1965, 4, 12),  "day"),
    ("1965-04-12T13:45:02.123456Z",   date(1965, 4, 12),  "day"),
    ("1965-04-12 13:45:02",           date(1965, 4, 12),  "day"),
    ("  1965-04-12  ",                date(1965, 4, 12),  "day"),     # whitespace tolerated
    (date(1965, 4, 12),               date(1965, 4, 12),  "day"),     # already a date
    (datetime(1965, 4, 12, 9, 30),    date(1965, 4, 12),  "day"),     # already a datetime
]

for _raw, _expected_date, _expected_precision in _SHAPES:
    _parsed = check_no_raise(f"parses {_raw!r} without raising",
                             lambda r=_raw: parse_partial_date(r))
    if _parsed is not None:
        check(f"  {_raw!r} -> {_expected_date}", _parsed[0], _expected_date)
        check(f"  {_raw!r} precision", _parsed[1], _expected_precision)

# Leap day is a real full date and must not be degraded.
check("leap day 2000-02-29 parses at day precision",
      parse_partial_date("2000-02-29"), (date(2000, 2, 29), "day"))


# ===========================================================================
# 2. UNUSABLE VALUES DEGRADE OR REPORT — THEY DO NOT RAISE
# ===========================================================================

print("\n" + "=" * 70)
print("2. Out-of-range and junk values are labelled, never raised")
print("=" * 70)

_UNUSABLE = [
    ("",                 None,               "missing"),
    ("   ",              None,               "missing"),
    (None,               None,               "missing"),
    ("unknown",          None,               "unparseable"),
    ("12/04/1965",       None,               "unparseable"),   # non-ISO ordering
    ("65-04-12",         None,               "unparseable"),   # two-digit year
    ("19650412",         None,               "unparseable"),   # no separators
    (12345,              None,               "unparseable"),   # not a string at all
    # Shape matches but a component is out of range: keep what is usable and
    # say how much was kept, rather than discarding the record.
    ("1965-13-01",       date(1965, 7, 15),  "year"),          # month 13 -> year only
    ("1965-02-30",       date(1965, 2, 15),  "month"),         # day 30 in Feb -> month only
    ("1900-02-29",       date(1900, 2, 15),  "month"),         # 1900 is not a leap year
    ("0000-01-01",       None,               "unparseable"),   # year 0 has no date
]

for _raw, _expected_date, _expected_precision in _UNUSABLE:
    _parsed = check_no_raise(f"handles {_raw!r} without raising",
                             lambda r=_raw: parse_partial_date(r))
    if _parsed is not None:
        check(f"  {_raw!r} -> {_expected_date} / {_expected_precision}",
              _parsed, (_expected_date, _expected_precision))

# The degradation is counted, not just survived: a well-formed but impossible
# date is a data-quality signal, and the recovery must not be its only trace.
PARTIAL_DATE_DEGRADATIONS.clear()
parse_partial_date("1965-02-30")   # day rejected -> month
parse_partial_date("1965-13-01")   # day rejected, then month rejected -> year
check("out-of-range components are counted",
      dict(PARTIAL_DATE_DEGRADATIONS),
      {"out_of_range:day": 2, "out_of_range:month": 1})


# ===========================================================================
# 3. THE RUN'S AGE REFERENCE DATE
# ===========================================================================

print("\n" + "=" * 70)
print("3. get_age_reference_date resolves DATA_SNAPSHOT_DATE, never the clock")
print("=" * 70)

check("DATA_SNAPSHOT_DATE is defined in File 03",
      isinstance(globals().get("DATA_SNAPSHOT_DATE"), str), True)
check("reference date is the configured snapshot date",
      get_age_reference_date().isoformat(), DATA_SNAPSHOT_DATE)
check("reference date is a date, not a datetime",
      type(get_age_reference_date()), date)

# An unusable snapshot date must be loud. Falling back to today() here would
# restore the drift the constant exists to remove, and would do it silently.
_REAL_SNAPSHOT = DATA_SNAPSHOT_DATE
for _bad in ("", "2026", "2026-03", "not a date"):
    DATA_SNAPSHOT_DATE = _bad
    check_raises(f"unusable DATA_SNAPSHOT_DATE {_bad!r} raises",
                 get_age_reference_date, ValueError)
DATA_SNAPSHOT_DATE = _REAL_SNAPSHOT
check("snapshot date restored after the negative cases",
      get_age_reference_date().isoformat(), _REAL_SNAPSHOT)


# ===========================================================================
# 4. AGE IS A FUNCTION OF THE DATA, NOT OF THE CLOCK
# ===========================================================================

print("\n" + "=" * 70)
print("4. _calculate_age is clock-independent and exact at the boundary")
print("=" * 70)

# Birthday boundary against a fixed reference: the day before, the day itself,
# and the day after must be 59, 60, 60.
check("day before the birthday",  _calculate_age("1966-03-12", REF), 59)
check("on the birthday",          _calculate_age("1966-03-11", REF), 60)
check("day after the birthday",   _calculate_age("1966-03-10", REF), 60)

# Imputed precisions still yield a usable age.
check("year-only birth date yields an age",   _calculate_age("1966", REF), 59)
check("year-month birth date yields an age",  _calculate_age("1966-03", REF), 59)
check("ISO datetime birth date yields an age",
      _calculate_age("1966-03-11T04:05:06Z", REF), 60)

# No usable age: None, not 0 and not an exception.
check("missing birth date -> None",      _calculate_age("", REF), None)
check("unparseable birth date -> None",  _calculate_age("unknown", REF), None)
check("birth after the reference -> None (not a negative age)",
      _calculate_age("2030-01-01", REF), None)

# The default reference is the configured snapshot, so the default result and
# the explicit result are the same value on any day this test is ever run.
_SAMPLE_BIRTHS = ["1945-01-01", "1966-03-11", "1966-03-12", "1980", "1980-06", "2000-02-29"]
_snapshot = date.fromisoformat(DATA_SNAPSHOT_DATE)
for _bd in _SAMPLE_BIRTHS:
    check(f"default reference == DATA_SNAPSHOT_DATE for {_bd!r}",
          _calculate_age(_bd), _calculate_age(_bd, _snapshot))

# STRUCTURAL guard. The equality above holds by construction today; this fails
# the moment a now()/today() call is reintroduced anywhere in the age path.
_PARSER_TREE = ast.parse(open(_code_dir + "07- FHIR Parser.py").read())


def _clock_calls(function_name: str, tree) -> list:
    """Names of any datetime.now / datetime.utcnow / date.today call inside fn."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr in ("now", "utcnow", "today")):
                    found.append(inner.func.attr)
    return found


check("_calculate_age contains no clock call",
      _clock_calls("_calculate_age", _PARSER_TREE), [])
check("_parse_demographics contains no clock call",
      _clock_calls("_parse_demographics", _PARSER_TREE), [])


# ===========================================================================
# 5. _parse_demographics SURVIVES EVERY SHAPE AND RECORDS WHAT IT DID
# ===========================================================================

print("\n" + "=" * 70)
print("5. _parse_demographics records precision and reference date")
print("=" * 70)

_DEMOGRAPHIC_CASES = [
    # (birthDate, expected age, expected precision)
    ("1966-03-11",             60,   "day"),
    ("1966-03",                59,   "month"),
    ("1966",                   59,   "year"),
    ("1966-03-11T00:00:00Z",   60,   "day"),
    ("",                       None, "missing"),
    ("unknown",                None, "unparseable"),
    ("2030-01-01",             None, "after_reference"),
]

for _bd, _expected_age, _expected_precision in _DEMOGRAPHIC_CASES:
    _demo = check_no_raise(f"birthDate {_bd!r} parses without raising",
                           lambda b=_bd: _parse_demographics(patient_resource(b)))
    if _demo is None:
        continue
    check(f"  {_bd!r} age", _demo["age"], _expected_age)
    check(f"  {_bd!r} precision", _demo["birth_date_precision"], _expected_precision)
    check(f"  {_bd!r} keeps the raw birth_date", _demo["birth_date"], _bd)
    check(f"  {_bd!r} records the reference date",
          _demo["age_reference_date"], DATA_SNAPSHOT_DATE)

# The precision tally is what makes an imputed corpus visible at load time.
BIRTH_DATE_PRECISION_COUNTS.clear()
for _bd in ("1966-03-11", "1966-03-11", "1966", ""):
    _parse_demographics(patient_resource(_bd))
check("precision counter tallies each shape",
      dict(BIRTH_DATE_PRECISION_COUNTS), {"day": 2, "year": 1, "missing": 1})


# ===========================================================================
# 6. RACE AND ETHNICITY READ BY SUB-EXTENSION URL
# ===========================================================================

print("\n" + "=" * 70)
print("6. US Core race/ethnicity read by url, not by array position")
print("=" * 70)

# THE DEFECT: `text` serialized first. extension[0].get('valueCoding') is {}
# because text carries valueString, so both fields became "unknown".
_text_first = _parse_demographics(patient_resource("1966-03-11", [
    {"url": RACE_URL,      "extension": [text_ext("White"), omb("White")]},
    {"url": ETHNICITY_URL, "extension": [text_ext("Not Hispanic or Latino"),
                                         omb("Not Hispanic or Latino", "2186-5")]},
]))
check("text-first race resolves to the OMB category", _text_first["race"], "White")
check("text-first race source is ombCategory",
      _text_first["race_source"], "ombCategory")
check("text-first ethnicity resolves", _text_first["ethnicity"], "Not Hispanic or Latino")
check("text-first ethnicity source is ombCategory",
      _text_first["ethnicity_source"], "ombCategory")

# ombCategory first (the Synthea ordering) must keep working.
_omb_first = _parse_demographics(patient_resource("1966-03-11", [
    {"url": RACE_URL,      "extension": [omb("Black or African American", "2054-5"),
                                         text_ext("Black")]},
    {"url": ETHNICITY_URL, "extension": [omb("Hispanic or Latino", "2135-2"),
                                         text_ext("Hispanic")]},
]))
check("ombCategory-first race", _omb_first["race"], "Black or African American")
check("ombCategory-first ethnicity", _omb_first["ethnicity"], "Hispanic or Latino")

# US Core allows up to five ombCategory for race. Truncating to the first would
# silently re-label a multi-race patient as single-race.
_multi = _parse_demographics(patient_resource("1966-03-11", [
    {"url": RACE_URL, "extension": [omb("White"),
                                    omb("Asian", "2028-9"),
                                    text_ext("White + Asian")]},
]))
check("multi-category race keeps every category", _multi["race"], "White; Asian")

# detailed is the next best coded value when no OMB category was sent.
_detailed_only = _parse_demographics(patient_resource("1966-03-11", [
    {"url": RACE_URL, "extension": [detailed("Cherokee"), text_ext("Cherokee")]},
]))
check("detailed used when no ombCategory", _detailed_only["race"], "Cherokee")
check("detailed is reported as the source",
      _detailed_only["race_source"], "detailed")

# text is the only mandatory sub-extension: use it rather than reporting unknown.
_text_only = _parse_demographics(patient_resource("1966-03-11", [
    {"url": RACE_URL, "extension": [text_ext("Other Race")]},
]))
check("text used when nothing coded is present", _text_only["race"], "Other Race")
check("text is reported as the source", _text_only["race_source"], "text")

# Empty and absent are distinguishable, and neither raises.
_empty = _parse_demographics(patient_resource("1966-03-11", [
    {"url": RACE_URL, "extension": []},
]))
check("empty race extension -> unknown", _empty["race"], "unknown")
check("empty race extension -> source 'empty'", _empty["race_source"], "empty")

_absent = _parse_demographics(patient_resource("1966-03-11"))
check("absent race extension -> unknown", _absent["race"], "unknown")
check("absent race extension -> source 'absent'", _absent["race_source"], "absent")
check("absent ethnicity extension -> source 'absent'",
      _absent["ethnicity_source"], "absent")

# Malformed entries must not take the bundle down with them.
_malformed = check_no_raise("malformed extension list does not raise",
    lambda: _parse_demographics(patient_resource("1966-03-11", [
        "not-a-dict",
        {"url": RACE_URL, "extension": ["not-a-dict", {"url": "ombCategory"}, omb("White")]},
    ])))
if _malformed is not None:
    check("malformed sub-extensions skipped, valid one still read",
          _malformed["race"], "White")


# ===========================================================================
# 7. END TO END — A YEAR-ONLY BUNDLE PARSES
# ===========================================================================

print("\n" + "=" * 70)
print("7. parse_fhir_bundle survives a Safe Harbor year-only birthDate")
print("=" * 70)

_BUNDLE_PATH = os.path.join(_TMP_DIR, "year_only_bundle.json")
with open(_BUNDLE_PATH, "w") as _fh:
    json.dump({
        "resourceType": "Bundle",
        "type":         "collection",
        "entry": [
            {"resource": patient_resource("1966", [
                {"url": RACE_URL,      "extension": [text_ext("White"), omb("White")]},
                {"url": ETHNICITY_URL, "extension": [omb("Not Hispanic or Latino", "2186-5")]},
            ])},
            {"resource": {
                "resourceType": "Condition",
                "clinicalStatus":     {"coding": [{"code": "active"}]},
                "verificationStatus": {"coding": [{"code": "confirmed"}]},
                "code": {"coding": [{"system": "http://snomed.info/sct",
                                     "code": "254637007",
                                     "display": "Non-small cell lung cancer"}]},
                "onsetDateTime": "2025-01-15",
            }},
        ],
    }, _fh)

_parsed_bundle = check_no_raise("year-only bundle parses instead of raising",
                                lambda: parse_fhir_bundle(_BUNDLE_PATH))
if _parsed_bundle is not None:
    _d = _parsed_bundle["demographics"]
    check("  bundle age is usable", _d["age"], 59)
    check("  bundle precision is 'year'", _d["birth_date_precision"], "year")
    check("  bundle reference date recorded", _d["age_reference_date"], DATA_SNAPSHOT_DATE)
    check("  bundle race read past the leading text sub-extension", _d["race"], "White")
    check("  bundle still parsed its condition", len(_parsed_bundle["conditions"]), 1)


# ===========================================================================
# 8. STRUCTURAL — FILE 13 RECORDS THE REFERENCE DATE, NOT THE CLOCK
# ===========================================================================

print("\n" + "=" * 70)
print("8. File 13 stamps the reference date and drops date.today()")
print("=" * 70)

# File 13 is read, not executed: executing it would load the cross-encoder and
# build a Qdrant client, neither of which this test needs.
_AGENT_SRC  = open(_code_dir + "13- LangGraph Agent.py").read()
_AGENT_TREE = ast.parse(_AGENT_SRC)


def _returned_keys(function_name: str, tree) -> set:
    """String keys of the dict literal a single-return function returns."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return) and isinstance(inner.value, ast.Dict):
                    return {k.value for k in inner.value.keys
                            if isinstance(k, ast.Constant)}
    return set()


_provenance_keys = _returned_keys("_pipeline_provenance", _AGENT_TREE)
check("_pipeline_provenance carries age_reference_date",
      "age_reference_date" in _provenance_keys, True)
check("_pipeline_provenance carries birth_date_precision",
      "birth_date_precision" in _provenance_keys, True)

# _pipeline_provenance is spread into all three terminal results, so a key in
# it reaches node_finalize, node_no_candidates and node_error_handler alike.
check("all three terminal nodes spread _pipeline_provenance",
      _AGENT_SRC.count("**_pipeline_provenance(state)"), 3)

# Source text would match the explanatory comment above the prompt, so this is
# an AST check: no .today() call may survive anywhere in File 13.
check("File 13 makes no date.today() call",
      [n for n in ast.walk(_AGENT_TREE)
       if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
       and n.func.attr == "today"], [])
check("the Stage 5 prompt reference date is the age reference date",
      "Reference date: {get_age_reference_date().isoformat()}" in _AGENT_SRC, True)


# ===========================================================================
# 9. END TO END — THE REFERENCE DATE REACHES THE DATABASE
# ===========================================================================

print("\n" + "=" * 70)
print("9. log_inference writes the age provenance columns")
print("=" * 70)

_LOGGED_PATIENT = {
    "patient_id":   "birthdate-test-patient",
    "demographics": _parse_demographics(patient_resource("1966", [
        {"url": RACE_URL, "extension": [text_ext("White"), omb("White")]},
    ])),
    "conditions":   [{"display": "Non-small cell lung cancer",
                      "clinical_status": "active", "onset_date": "2025-01-15"}],
    "medications":  [],
    "observations": [],
    "procedures":   [],
    "allergies":    [],
}

_LOGGED_RESULT = {
    "patient_id":           "birthdate-test-patient",
    "timestamp":            "2026-03-11T00:00:00",
    # Supplied so File 14 does not fall back to _resolve_primary_cancer(),
    # which needs the cancer registry from File 08 — not chained by this test.
    "primary_condition":    "Non-small cell lung cancer",
    "matches":              [],
    "near_misses":          [],
    "not_evaluable":        [],
    "stage_timings":        {},
    "age_reference_date":   DATA_SNAPSHOT_DATE,
    "birth_date_precision": "year",
}

check_no_raise("log_inference writes a row", lambda: log_inference(_LOGGED_RESULT, _LOGGED_PATIENT))

_conn = sqlite3.connect(inferences_path)
_conn.row_factory = sqlite3.Row
_row = _conn.execute(
    "SELECT * FROM inferences WHERE patient_id = 'birthdate-test-patient' "
    "ORDER BY id DESC LIMIT 1"
).fetchone()

check("a row was written", _row is not None, True)
if _row is not None:
    check("inferences.age_reference_date is the snapshot date",
          _row["age_reference_date"], DATA_SNAPSHOT_DATE)
    check("inferences.birth_date_precision recorded",
          _row["birth_date_precision"], "year")
    check("the logged age is the snapshot-anchored age", _row["age"], 59)
    check("the logged race came from the OMB category", _row["race"], "White")

# A result dict that never reported keeps NULL: "not recorded" must not read as
# "computed against today".
_conn.execute(
    "INSERT INTO inferences (patient_id, timestamp) VALUES ('unreported', '2026-03-11')"
)
_unreported = _conn.execute(
    "SELECT age_reference_date, birth_date_precision FROM inferences "
    "WHERE patient_id = 'unreported'"
).fetchone()
check("unreported run stores NULL reference date",
      _unreported["age_reference_date"], None)
check("unreported run stores NULL precision",
      _unreported["birth_date_precision"], None)
_conn.close()


# ===========================================================================
# CLEANUP + SUMMARY
# ===========================================================================

shutil.rmtree(_TMP_DIR, ignore_errors=True)

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")

if _FAILURES:
    print("\nFailures:")
    for _f in _FAILURES:
        print(textwrap.indent(f"  - {_f}", ""))

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Aug  3 2026

@author: ramyalsaffar
"""
