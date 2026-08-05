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
# Item 20a: this file sits in the code directory, so __file__ locates it with
# no hardcoded path. __file__ is bound when the file is run as a script (every
# documented entry point for it) and when Spyder runfile()s it. In a bare
# interactive paste it is not bound, and the working directory is the only
# remaining candidate -- taken, but announced, never silently.
import os as _os_boot
if "__file__" in globals():
    _code_dir = _os_boot.path.dirname(_os_boot.path.abspath(__file__)) + _os_boot.sep
else:
    _code_dir = _os_boot.getcwd() + _os_boot.sep
    print(f"[Bootstrap] __file__ unbound; using the working directory as the code directory: {_code_dir}")
del _os_boot

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
# TWO INDEPENDENT MECHANISMS KEEP THIS TEST OFF THE PRODUCTION DATABASE, and the
# second arrived in pass 20c-2b because the first stopped being enough on its
# own.
#
#   1. inferences_path is rebound before File 14 is loaded. That worked only
#      because File 14 was exec'd into this namespace and read the name out of
#      it. File 14 is now a shim over oncotriage/storage/database_logger.py, and
#      a MODULE function cannot see a caller's globals; the redirect survives
#      solely because the shim keeps a wrapper passing
#      globals().get("inferences_path") down. Had the shim re-exported the
#      package function directly, section 9 below would have written a real row
#      into the real inferences.db -- including the INSERT it makes by hand --
#      while this file printed the name of a temporary one.
#   2. log_inference is called with db_path EXPLICITLY and the path it reports
#      back is asserted, which depends on no seam at all.
#
# The rebinding stays because section 9 reads back through
# sqlite3.connect(inferences_path) and the two must name the same file.

_PRODUCTION_INFERENCES_PATH = inferences_path

_TMP_DIR = tempfile.mkdtemp(prefix="oncotriage_birthdate_")
inferences_path = os.path.join(_TMP_DIR, "inferences_test.db")

with open(_code_dir + "14- Database Logger.py") as _fh:
    exec(_fh.read(), globals())


# --- THE DATABASE-ISOLATION ASSERTION IS SHOWN TO DISCRIMINATE --------------
# CLAUDE.md: an assertion that has only ever passed is not evidence that it can
# catch anything. resolve_inference_db_path(None) is what a caller that forgot
# db_path gets. It RESOLVES without connecting, so this control names the hazard
# without going near the production file.
_PACKAGE_DEFAULT_DB = resolve_inference_db_path(None)

print("\n" + "=" * 70)
print("0. the database-isolation assertion can fail")
print("=" * 70)
check("the scratch path is non-empty (non-degeneracy)",
      bool(inferences_path) and inferences_path.endswith(".db"), True)
check("the production path is non-empty (non-degeneracy)",
      bool(_PRODUCTION_INFERENCES_PATH), True)
check("omitting db_path resolves to the PRODUCTION database",
      os.path.abspath(_PACKAGE_DEFAULT_DB),
      os.path.abspath(_PRODUCTION_INFERENCES_PATH))
check("...which is NOT this test's scratch database, so passing db_path is "
      "doing real work and the check below can fail",
      os.path.abspath(_PACKAGE_DEFAULT_DB) == os.path.abspath(inferences_path),
      False)
check("...and passing db_path resolves to exactly what was passed",
      resolve_inference_db_path(inferences_path), inferences_path)


# ===========================================================================
# FIXTURES
# ===========================================================================

# A date far enough from every birthDate below that a one-day error is visible.
#
# REF IS PINNED ON PURPOSE and must not be derived. Section 4 passes it
# explicitly to _calculate_age() to test the birthday boundary exactly, which
# only works against a date that never moves. Sections that do NOT pass a
# reference get theirs from DATA_SNAPSHOT_DATE instead, and their expectations
# have to move with it -- see _expected_age_for() below.
REF = date(2026, 3, 11)


# ---------------------------------------------------------------------------
# THE RULE THIS FILE FOLLOWS
# ---------------------------------------------------------------------------
# Any assertion whose age or precision comes out of _parse_demographics() or
# parse_fhir_bundle() is computed against DATA_SNAPSHOT_DATE (03- Config.py),
# so it MOVES when the corpus is regenerated. Those expectations are derived
# from get_age_reference_date().
#
# Any assertion that passes REF explicitly is pinned deliberately and stays a
# literal.
#
# This distinction was not made until item 18e. Sections 5, 7 and 9 hardcoded
# ages of 59 and 60 that were correct against DATA_SNAPSHOT_DATE = 2026-03-11;
# when item 18b regenerated the corpus and moved the constant to 2026-08-03,
# four assertions began failing on data that was entirely correct. A test that
# goes red every time the corpus is legitimately regenerated trains people to
# ignore it.


def _expected_age_for(year: int, month: int = None, day: int = None) -> int:
    """
    Age the parser must report, at the CURRENT reference date, for a birth date
    with the given components.

    Missing components are imputed from the same anchors parse_partial_date()
    applies (PARTIAL_DATE_ANCHOR_MONTH / _DAY, 02- Utility Functions.py), so a
    year-only birthDate is anchored mid-year exactly as the parser anchors it.

    Deliberately computed with plain arithmetic rather than by calling
    parse_partial_date() or _calculate_age(): an expected value produced by the
    function under test agrees with that function by construction and proves
    nothing. The anchors are read from the config, but the age is not.
    """
    reference = get_age_reference_date()
    birth_month = PARTIAL_DATE_ANCHOR_MONTH if month is None else month
    birth_day   = PARTIAL_DATE_ANCHOR_DAY   if day   is None else day

    age = reference.year - year
    if (reference.month, reference.day) < (birth_month, birth_day):
        age -= 1          # birthday has not happened yet this year
    return age


def _birth_date_after_reference() -> str:
    """
    A birthDate guaranteed to sit AFTER the reference date, whatever the
    reference date is.

    Was the literal "2030-01-01", which was only "after" for as long as
    DATA_SNAPSHOT_DATE stayed below it -- a silent expiry date on the test.
    One day past the reference is the minimal such date and exercises the
    boundary rather than a value far away from it.

    date.fromordinal(...+1) rather than timedelta: 01- Imports.py imports
    `date` but not `timedelta`.
    """
    return date.fromordinal(get_age_reference_date().toordinal() + 1).isoformat()


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
#
# RETARGETED IN PASS 20c-2b. This used to parse "07- FHIR Parser.py", which is
# now a re-export shim holding no function definitions at all. _clock_calls()
# would have found no _calculate_age to walk, returned [] for it, and reported
# PASS on a file it had not inspected -- the check would have gone permanently
# green while proving nothing. The definitions live in the package module now,
# and that is what is parsed.
_PARSER_SOURCE = os.path.join(_code_dir, "oncotriage", "fhir", "parser.py")
_PARSER_TREE = ast.parse(open(_PARSER_SOURCE, encoding="utf-8").read())


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


# NON-DEGENERATE FIRST. _clock_calls() returns [] both for "this function makes
# no clock call" and for "this function is not in the tree I was handed", and
# the second is exactly what a stale filename produces. Both functions have to
# be present before their emptiness means anything.
_PARSER_DEFS = {_n.name for _n in ast.walk(_PARSER_TREE)
                if isinstance(_n, ast.FunctionDef)}
check("the parsed parser source actually defines the two functions under test",
      {"_calculate_age", "_parse_demographics"} <= _PARSER_DEFS, True)

# ...and the detector is not vacuous either: it must find a clock call when one
# is there. A copy of _calculate_age's tree with `date.today()` spliced in is
# parsed from a string, so nothing on disk is touched.
_DETECTOR_CONTROL = ast.parse(
    "def _calculate_age(birth_date, reference_date=None):\n"
    "    return date.today()\n"
)
check("...and _clock_calls DOES report a clock call when one is present "
      "(negative control, parsed from a string, nothing on disk touched)",
      _clock_calls("_calculate_age", _DETECTOR_CONTROL), ["today"])

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

# RULE: DERIVED. _parse_demographics() takes no reference argument -- it uses
# get_age_reference_date(), i.e. DATA_SNAPSHOT_DATE -- so every expected age
# here follows the config and comes from _expected_age_for(). The precisions
# are properties of the birthDate string alone and stay literal.

_DEMOGRAPHIC_CASES = [
    # (birthDate, expected age, expected precision)
    ("1966-03-11",             _expected_age_for(1966, 3, 11), "day"),
    ("1966-03",                _expected_age_for(1966, 3),     "month"),
    ("1966",                   _expected_age_for(1966),        "year"),
    ("1966-03-11T00:00:00Z",   _expected_age_for(1966, 3, 11), "day"),
    ("",                       None, "missing"),
    ("unknown",                None, "unparseable"),
    (_birth_date_after_reference(), None, "after_reference"),
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

# RULE: DERIVED. parse_fhir_bundle() reaches _parse_demographics(), which
# anchors on DATA_SNAPSHOT_DATE, so the expected age below follows the config.

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
    check("  bundle age is usable", _d["age"], _expected_age_for(1966))
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
# RETARGETED IN PASS 20c-2c, AND THREE OF THE FOUR CHECKS BELOW COULD HAVE GONE
# SILENTLY GREEN. "13- LangGraph Agent.py" is a re-export shim now; the whole
# agent moved to oncotriage/agent/. Against the shim:
#
#   _returned_keys("_pipeline_provenance", tree)   -> no such function -> the
#       "carries age_reference_date" checks would have compared against an empty
#       key set and FAILED, which is the one honest outcome of the four.
#   _AGENT_SRC.count("**_pipeline_provenance(state)")  -> 0, expected 3 -> FAILS.
#   [n for n in walk(tree) if ... attr == "today"]     -> [] -> PASSES. Green on
#       a file containing no code.
#   "Reference date: {get_age_reference_date()...}" in _AGENT_SRC -> False,
#       expected True -> FAILS.
#
# So the clock check -- the one this section is named for -- is precisely the one
# that would have gone quiet. The non-degeneracy block below is what stops that
# state being reachable again.
#
# The three subjects live in three modules: _pipeline_provenance and its three
# call sites in terminal.py, the Stage 5 prompt's reference-date line in
# evaluation.py, and the age path itself across both. All the agent sources are
# concatenated so a definition moving between modules does not silently empty
# this check a second time.
import glob as _glob_agent

_AGENT_SOURCES = sorted(
    _glob_agent.glob(os.path.join(_code_dir, "oncotriage", "agent", "*.py"))
)
_AGENT_SRC  = "\n".join(
    open(_f, encoding="utf-8").read() for _f in _AGENT_SOURCES
)
_AGENT_TREE = ast.parse(_AGENT_SRC)

# NON-DEGENERATE FIRST.
check("the agent sources were found and concatenated",
      len(_AGENT_SOURCES) >= 12, True)
check("...into a substantial body of source", len(_AGENT_SRC) > 200_000, True)
check("...that parses and defines _pipeline_provenance and the three terminal "
      "nodes",
      {"_pipeline_provenance", "node_finalize", "node_no_candidates",
       "node_error_handler"}
      <= {n.name for n in ast.walk(_AGENT_TREE)
          if isinstance(n, ast.FunctionDef)}, True)
# ...and the clock detector is not vacuous: it must find a today() call when one
# is present. Parsed from a string, so nothing on disk is touched.
check("...and the today()/now() detector reports one when it is there "
      "(negative control)",
      len([n for n in ast.walk(ast.parse("x = date.today()"))
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and n.func.attr == "today"]), 1)


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

# RULE: DERIVED. The row under test is built from _parse_demographics() on a
# year-only birthDate, so inferences.age is anchored on DATA_SNAPSHOT_DATE and
# the expectation follows the config. age_reference_date and
# birth_date_precision are already asserted against DATA_SNAPSHOT_DATE and a
# literal precision respectively, which is correct and unchanged.

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
    # Supplied so the row under test does not depend on _resolve_primary_cancer.
    # That deliberate avoidance is why section 9b below exercises the function
    # DIRECTLY — see the comment there.
    "primary_condition":    "Non-small cell lung cancer",
    "matches":              [],
    "near_misses":          [],
    "not_evaluable":        [],
    "stage_timings":        {},
    "age_reference_date":   DATA_SNAPSHOT_DATE,
    "birth_date_precision": "year",
}

_LOGGED_DB = None


def _log_the_row():
    global _LOGGED_DB
    _LOGGED_DB = log_inference(_LOGGED_RESULT, _LOGGED_PATIENT,
                               db_path=inferences_path)


check_no_raise("log_inference writes a row", _log_the_row)
check("...into the scratch database, not production", _LOGGED_DB, inferences_path)

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
    check("the logged age is the snapshot-anchored age",
          _row["age"], _expected_age_for(1966))
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
# 9b. _resolve_primary_cancer RESOLVES A DIAGNOSIS WITHOUT FILE 13
# ===========================================================================

print("\n" + "=" * 70)
print("9b. _resolve_primary_cancer works in a chain that never loads File 13")
print("=" * 70)

# WHY THIS CHECK EXISTS, AND WHY IT HAS TO BE IN THIS FILE.
#
# Pass 20c-2b changed _resolve_primary_cancer from reading File 13's
# _CANCER_REGISTRY global to calling load_registry(). That fixed a path which
# raised NameError in any chain that loaded File 14 without File 13 -- and then
# NOTHING EXERCISED IT. This file is the only chain in the repository that loads
# 14 without 13, and section 9 above deliberately supplies primary_condition so
# the fallback never runs. Files 32, 36 and 37 all chain 13 first, so the global
# is bound for them and none of them witnesses the fix either.
#
# A fix nothing exercises is a claim, not a fix. So the function is called
# directly, here, in the one process where the old code would have raised.
#
# The registry is built on this call -- load_registry() imports the ICD-10-CM
# release on first construction -- which is the point: nothing in this chain
# built it beforehand.

_PRIMARY_CANCER_CONDITIONS = [
    # A comorbidity that must NOT win, listed first so "returns the first
    # condition" would be visibly wrong rather than accidentally right.
    {"code": "38341003", "display": "Hypertension",
     "system_key": "snomed", "verification_status": "confirmed",
     "clinical_status": "active", "onset_date": "2019-04-02"},
    # SNOMED 254637007, the primary NSCLC code File 42 audits by name.
    {"code": "254637007", "display": "Non-small cell lung cancer",
     "system_key": "snomed", "verification_status": "confirmed",
     "clinical_status": "active", "onset_date": "2025-01-15"},
    # A METASTASIS. The registry rejects secondary neoplasms at every layer, so
    # this one must lose to the primary above.
    {"code": "94381002", "display": "Secondary malignant neoplasm of bone",
     "system_key": "snomed", "verification_status": "confirmed",
     "clinical_status": "active", "onset_date": "2026-02-01"},
]

_resolved_primary = None


def _call_resolve_primary_cancer():
    global _resolved_primary
    _resolved_primary = _resolve_primary_cancer(_PRIMARY_CANCER_CONDITIONS)


# The old code raised NameError here. check_no_raise records the exception text
# rather than aborting, so a regression reports the reason instead of killing
# the run at line 1.
check_no_raise("_resolve_primary_cancer does not raise without File 13 loaded",
               _call_resolve_primary_cancer)

# NON-DEGENERACY FIRST, and it is the whole point of this block. None is what
# this function returns for an EMPTY condition list, and it is also what a
# silently-empty registry filter would produce after falling through to
# `valid[0].get("display")` on a list whose entries had no display. Asserting
# the identity below without ruling None out would pass on a registry that
# matched nothing.
check("...and returns something at all (not None, which an empty filter also "
      "returns)", _resolved_primary is not None, True)
check("...and it is a non-empty string", bool(_resolved_primary), True)

# The identity. It must be the PRIMARY cancer -- not the first condition in the
# list, not the metastasis, not the comorbidity.
check("...and it is the primary cancer, not the first condition in the list",
      _resolved_primary, "Non-small cell lung cancer")

# The registry really was consulted. If load_registry() had returned something
# inert, the function's own fallback would have handed back valid[0], which is
# the hypertension row -- so this is the assertion that separates "the registry
# ran" from "the fallback ran and happened to look plausible".
check("...so the fallback to the first valid condition did NOT fire",
      _resolved_primary == _PRIMARY_CANCER_CONDITIONS[0]["display"], False)

# And the empty-list contract, which is the one case where None is correct.
check("an empty condition list still returns None",
      _resolve_primary_cancer([]), None)


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
