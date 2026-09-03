# A missing clinical status is rendered as unknown, counted, and stored
##############################################################################

"""The absence of a statement never reaches the judge as a positive one.

WHY THIS FILE EXISTS
--------------------
``oncotriage/agent/patient.py`` carried a local ``_ACTIVE_STATUSES`` set with
``"unknown"`` in it, so a medication whose parsed status was unknown printed
``status: active`` into the Stage 5 record. That is not a cosmetic mislabel.
``PROMPT_VERSION`` 1.10.0's RULE 4 keys its ongoing gate on the word -- "a
medication whose status is active ... is present NOW and therefore present
within any window reaching the reference date, whatever its interval" -- so a
drug the source said nothing about answered every lookback window with "present
now", on no evidence. The SAME prompt states the opposite principle for the
condition family in as many words ("absence of a status is not evidence that a
condition is running -- reading it as ongoing would manufacture a
disqualification out of missing data"), and the condition section has always
rendered ``unknown`` verbatim. This is the medication family being brought to
the rule the other family already obeyed.

Nothing counted how often it happened, in either family, so the rate was
unmeasurable and the affected patients were unfindable.

WHAT IT HOLDS
-------------
    1. THE RENDERED LINE, byte-exact, in every spelling a status can be missing
       in -- and the unchanged renders beside them, so "unknown no longer says
       active" is not satisfied by a renderer that broke every other status.
    2. THE PARSER'S DEDUP DECISION. An entry with no usable status now LOSES a
       duplicate to one that states ``completed``, and a running status still
       wins over both. Driven in BOTH list orders, because a rank that ties
       lets bundle order decide and would pass in one order only.
    3. THE TWO DEGRADATION COUNTERS: keyed by spelling, incremented over the
       list that reached the renderer (post-filter, post-dedup) and not over
       the bundle, and REGISTERED -- which is separately enforced by
       tests/test_degradation_counter_readers.py section 1.
    4. THE TWO COLUMNS: declared, created on a fresh database, MIGRATED onto a
       pre-era-10 one carrying a row, round-tripped, and 0 shown distinguishable
       from NULL in SQL.
    5. THE ERA STAMP, and the ERA note that stamp's own rule requires.
    6. THE PROMPT TEMPLATE DID NOT MOVE. Both Section 2 variants' digests are
       recomputed here and required to be equal to each other's value at
       import -- so a template edit smuggled in beside a renderer fix fails.

NEGATIVE CONTROLS. Eight, each a small plant into an in-memory COPY of the
module under test, each paired with the shipped module's own answer to the same
probe -- without which a probe that disagreed with everything would report every
plant as caught while measuring nothing.

NO NETWORK, NO KEYS, NO SPEND, NO CORPUS, NO GIT, NO MODEL, NO LIVE SERVER.
Every patient and every bundle in here is a literal dict. It DOES open SQLite,
in section 4 only, inside a tempfile.mkdtemp it removes and asserts gone -- and
that directory is asserted to differ from the production inferences path. NOT in
the collision matrix: the three repository files it reads
(oncotriage/agent/patient.py, oncotriage/fhir/parser.py,
oncotriage/storage/database_logger.py) are written by neither of the suite's two
writers and are sha256-compared at the end.

WHY IT EXECS. Every control is a one-token edit inside a function body or a
module-level frozenset -- there is no attribute to rebind for any of them --
and ``git show`` can supply none: at HEAD the code is correct, and the revision
that HAS the defect does not have the counters, the columns or the era, so a
blob would produce a module that cannot be probed for the property at all. A
patched in-memory copy is the shape CLAUDE.md prefers over an in-place edit,
and this file is an argued member of
``tests/test_package_invariants.py``'s ``_EXEC_ALLOWLIST``.

Run from terminal:
    python tests/test_missing_clinical_status.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# THE CANDIDATE DIRECTORY IS THE PARENT OF THIS FILE'S: this file sits in
# tests/ and the package sits BESIDE tests/, not inside it. `pip install -e .`
# makes the whole block a no-op.
import os
import sys

try:
    import oncotriage  # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
         if "__file__" in globals() else None, "__file__"),
        (os.getcwd(), "cwd"),
    ):
        if _candidate and os.path.isdir(os.path.join(_candidate, "oncotriage")):
            if _candidate not in sys.path:
                sys.path.insert(0, _candidate)
            print(f"[Bootstrap] oncotriage package found at {_candidate} "
                  f"(via {_how}); added to sys.path")
            break
    else:
        raise
    del _candidate, _how

import ast
import hashlib
import shutil
import sqlite3
import tempfile
import types

from oncotriage import degradation as _degradation
from oncotriage import paths as _paths
from oncotriage import tracking as _tracking
from oncotriage.agent import patient as _patient_module
from oncotriage.agent.patient import _create_patient_summary
from oncotriage.fhir import parser as _parser
from oncotriage.storage import database_logger as _dl


# ===========================================================================
# HARNESS
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
        _FAILURES.append(f"{label}\n          expected: {expected}\n"
                         f"          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def guarded(fn):
    """Call `fn`, turning a raise into a value `check` fails on.

    A bare call into production code inside a check's ARGUMENT LIST lets a
    planted defect's exception escape while the argument is being evaluated,
    and the run then reports one traceback where it owed a summary. This
    project has shipped that shape more than a dozen times, and TWO of the
    plants below make the renderer raise.
    """
    try:
        return fn()
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        return f"raised {type(exc).__name__}: {exc}"


# THE PATHS COME FROM THE MODULES THIS PROCESS IMPORTED, never from this file's
# own location: moving the test cannot break it, and the source being planted
# into is provably the one under test rather than a same-named copy.
_SOURCES = {
    "oncotriage/agent/patient.py": os.path.abspath(_patient_module.__file__),
    "oncotriage/fhir/parser.py": os.path.abspath(_parser.__file__),
    "oncotriage/storage/database_logger.py": os.path.abspath(_dl.__file__),
}


def _sha(path):
    with open(path, encoding="utf-8") as fh:
        return hashlib.sha256(fh.read().encode()).hexdigest()


_SHA_AT_START = {rel: _sha(p) for rel, p in _SOURCES.items()}


class _PlantFailed(Exception):
    """A plant that did not apply or did not compile. Never escapes a check."""


def _plant(name, source_path, subs):
    """Exec an in-memory COPY of `source_path` with `subs` applied.

    Raises _PlantFailed -- never SyntaxError -- so a malformed plant is
    RECORDED as a failure instead of aborting the run and hiding every check
    below it. A control that takes the process down is not a control. The file
    on disk is hashed before and after and a modification is an AssertionError,
    because a control that edited the tree would be the defect it is testing.
    """
    with open(source_path, encoding="utf-8") as fh:
        source = fh.read()
    before = hashlib.sha256(source.encode()).hexdigest()
    try:
        for old, new in subs:
            if source.count(old) != 1:
                raise _PlantFailed(
                    f"plant target appears {source.count(old)} times, "
                    f"expected exactly 1: {old[:70]!r}")
            source = source.replace(old, new, 1)
        module = types.ModuleType(name)
        module.__file__ = source_path
        exec(compile(source, source_path, "exec"), module.__dict__)
    except _PlantFailed:
        raise
    except Exception as exc:            # noqa: BLE001 - reported, not raised
        raise _PlantFailed(f"{type(exc).__name__}: {exc}") from None
    finally:
        after = _sha(source_path)
        if before != after:
            raise AssertionError(f"{source_path} was modified on disk")
    return module


_CONTROL_SEQ = [0]


def _control(label, source_path, subs, probe, expected, clean_expected):
    """A negative control, with the SHIPPED module's answer beside it.

    The clean arm runs FIRST and is asserted separately: without it a probe
    that disagreed with every module would report the plant as caught while
    measuring nothing about the plant.
    """
    _CONTROL_SEQ[0] += 1
    seq = _CONTROL_SEQ[0]
    check(f"c{seq} CLEAN CONTROL: the shipped module gives the correct answer "
          f"to the same probe -- {label}",
          guarded(lambda: probe(_MODULES[source_path])), clean_expected)
    try:
        module = _plant(f"missing_status_ctl_{seq}", source_path, subs)
    except _PlantFailed as exc:
        check(f"c{seq} {label}  [THE PLANT ITSELF FAILED: {exc}]",
              "plant-failed", expected)
        return
    check(f"c{seq} {label}", guarded(lambda: probe(module)), expected)


_MODULES = {
    _SOURCES["oncotriage/agent/patient.py"]: _patient_module,
    _SOURCES["oncotriage/fhir/parser.py"]: _parser,
}
_PATIENT_SRC = _SOURCES["oncotriage/agent/patient.py"]
_PARSER_SRC = _SOURCES["oncotriage/fhir/parser.py"]


# ===========================================================================
# FIXTURES -- every one a literal
# ===========================================================================

def patient(medications=(), conditions=()):
    """The minimum _create_patient_summary reads, plus the two lists."""
    return {"patient_id": "probe-1",
            "demographics": {"age": 61, "sex": "female",
                             "birth_date": "1965-01-01", "race": "white",
                             "ethnicity": "nonhispanic"},
            "conditions": list(conditions), "observations": [],
            "medications": list(medications), "procedures": [],
            "allergies": [],
            "cancer_stage_observations": [],
            "cancer_metastasis_observations": [],
            "cancer_genomic_variants": [],
            "ecog_performance_status": {}}


# Cisplatin is in the RELEVANT tier of _classify_medication_relevance, which is
# what gets a full line WITH its status; a background medication renders its
# name only and could not show a status label at all.
def med(status=..., display="Cisplatin"):
    out = {"display": display, "start_date": "unknown", "end_date": "unknown"}
    if status is not ...:
        out["status"] = status
    return out


def _read_active_set(module):
    """The renderer's local `_ACTIVE_STATUSES`, read out of its own source.

    A FUNCTION LOCAL, so there is nothing to import and nothing to patch: the
    only way to ask what it contains is to read the assignment. Parsed with
    `ast` and evaluated with `literal_eval` rather than matched as text, so a
    reordering or a reformatting of the same set does not fail and a changed
    MEMBERSHIP does.
    """
    tree = ast.parse(open(os.path.abspath(module.__file__),
                          encoding="utf-8").read())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "_ACTIVE_STATUSES"):
            return ast.literal_eval(node.value)
    raise LookupError("_ACTIVE_STATUSES not found in " + module.__file__)


def med_lines(medications, module=_patient_module):
    """The rendered medication lines, whole, without the heading."""
    text = module._create_patient_summary(patient(medications))
    start = text.index("\nMedications:\n")
    section = text[start:text.index("\nAllergies:", start)]
    return [ln for ln in section.splitlines()
            if ln and ln not in ("Medications:",)]


# ===========================================================================
# 1. THE RENDERED LINE
# ===========================================================================

print("=" * 74)
print("1. the rendered medication line")
print("=" * 74)

_UNKNOWN_LINE = "- Cisplatin | status: unknown"

# EVERY SPELLING OF ABSENCE, and the last two are the ones a `.get(k, default)`
# does NOT catch: a key that is PRESENT and None, and a blank string. Before
# this pass the first of those did not render "active" -- it RAISED
# AttributeError from inside the render, taking the whole patient down.
for _label, _value in (("the parser's literal 'unknown'", "unknown"),
                       ("uppercase with trailing space", "UNKNOWN "),
                       ("an explicit None", None),
                       ("an empty string", ""),
                       ("whitespace only", "   "),
                       ("the key missing entirely", ...)):
    check(f"1a  {_label}: renders `status: unknown`, never `status: active`",
          guarded(lambda v=_value: med_lines([med(v)])), [_UNKNOWN_LINE])

# THE UNCHANGED RENDERS. Without these, "unknown stopped saying active" is
# equally satisfied by a renderer that broke every other status word.
for _status, _rendered in (("active", "active"),
                           ("completed", "completed"),
                           ("stopped", "stopped"),
                           ("cancelled", "cancelled"),
                           ("on-hold", "active"),
                           ("draft", "active"),
                           ("intended", "active")):
    check(f"1b  status {_status!r} is unchanged and renders {_rendered!r}",
          guarded(lambda s=_status: med_lines([med(s)])),
          [f"- Cisplatin | status: {_rendered}"])

check("1c  the local set no longer contains 'unknown', which is what 1a "
      "rests on -- read out of the function's own source rather than "
      "re-derived",
      guarded(lambda: 'unknown' in _read_active_set(_patient_module)), False)

check("1c  ...and it DOES still contain the four this pass leaves alone, so "
      "the set was narrowed rather than emptied",
      guarded(lambda: sorted(_read_active_set(_patient_module))),
      ["active", "draft", "intended", "on-hold"])

# THE CONDITION FAMILY, MEASURED RATHER THAN ASSUMED -- and the first draft of
# this check asserted the wrong thing and failed, correctly.
# `_format_condition_line` does NOT print `unknown`; it OMITS the status part
# (`elif clinical_status and clinical_status not in ("unknown", "")`). So the
# two families agree on the PRINCIPLE -- an absence is never rendered as a
# presence -- and differ in the SHAPE, and that difference is argued at
# `_ACTIVE_STATUSES`. Pinned here so a later pass that "unifies" them has to
# argue with a check rather than with a comment.
def condition_line(clinical_status):
    text = _create_patient_summary(patient(conditions=[
        {"display": "Diabetes", "clinical_status": clinical_status,
         "verification_status": "confirmed", "onset_date": "2010-01-01",
         "codings": []}]))
    start = text.index("\nConditions:\n")
    return [ln for ln in text[start:text.index("\nMedications:", start)]
            .splitlines() if ln.startswith("- ")]


_ACTIVE_COND = guarded(lambda: condition_line("active"))
_UNKNOWN_COND = guarded(lambda: condition_line("unknown"))

check("1d  the condition family is untouched by this pass and never said "
      "`active` for an unknown status: it OMITS the status part, where the "
      "medication line prints the word",
      [_UNKNOWN_COND,
       guarded(lambda: any("active" in ln for ln in _UNKNOWN_COND))],
      [["- Diabetes | 2010 | onset 16 years before reference date | "
        "[comorbidity]"], False])

check("1d  ...and the omission is real rather than the status never being "
      "rendered at all (non-degeneracy: a known status DOES appear)",
      guarded(lambda: [ln for ln in _ACTIVE_COND if "| active |" in ln]),
      ["- Diabetes | active | 2010 | onset 16 years before reference date | "
       "[comorbidity]"])


# ===========================================================================
# 2. THE PARSER'S DEDUP DECISION
# ===========================================================================

print("\n" + "=" * 74)
print("2. the medication dedup rank")
print("=" * 74)

_PATIENT_RES = {"resourceType": "Patient", "id": "p1", "gender": "female",
                "birthDate": "1965-01-01"}


def med_resource(status, code="C1", display="Cisplatin"):
    resource = {
        "resourceType": "MedicationRequest",
        "medicationCodeableConcept": {"coding": [
            {"system": "http://www.nlm.nih.gov/research/umls/rxnorm",
             "code": code, "display": display}]},
        "authoredOn": "2020-01-01",
    }
    if status is not None:
        resource["status"] = status
    return resource


def bundle(resources):
    return {"resourceType": "Bundle",
            "entry": [{"resource": r} for r in resources]}


def survivors(statuses, module=_parser):
    """The statuses that survive dedup, for one repeated display name."""
    parsed = module.parse_fhir_bundle(
        bundle([_PATIENT_RES] + [med_resource(s) for s in statuses]))
    return [m["status"] for m in parsed["medications"]]


# THE DECISION, ARGUED. `_ACTIVE_MED_STATUSES` is a SORT KEY and nothing else:
# `deduplicate_by_display` keeps the FIRST entry per display, so whatever sorts
# to tier 0 wins the duplicate. With "unknown" in that tier, an entry carrying
# no status beat a `completed` entry for the same drug -- and a `completed`
# record is a POSITIVE, DATED statement (RULE 2 of the system prompt sends the
# model to its end date for washout arithmetic) while "unknown" is the absence
# of one. The absence winning DELETES the documented record rather than merely
# mislabelling it, which is a strictly worse version of the render defect.
#
# BOTH ORDERS, ALWAYS. `sorted` is stable, so a rank that ties would let bundle
# order decide the winner and a one-order test would pass on the order that
# happened to agree.
for _order in (["unknown", "completed"], ["completed", "unknown"]):
    check(f"2a  {_order}: the DOCUMENTED status wins the duplicate",
          guarded(lambda o=_order: survivors(o)), ["completed"])

for _order in (["active", "completed"], ["completed", "active"]):
    check(f"2b  {_order}: a running status still wins over a historical one "
          f"-- unchanged by this pass",
          guarded(lambda o=_order: survivors(o)), ["active"])

for _order in (["unknown", "active"], ["active", "unknown"]):
    check(f"2c  {_order}: a running status wins over no status",
          guarded(lambda o=_order: survivors(o)), ["active"])

for _order in (["unknown", "some-status-nobody-enumerates"],
               ["some-status-nobody-enumerates", "unknown"]):
    check(f"2d  {_order}: a status this module has never heard of is still a "
          f"POSITIVE statement and beats no status",
          guarded(lambda o=_order: survivors(o)),
          ["some-status-nobody-enumerates"])

check("2e  a lone unknown-status medication is kept, not dropped: this is a "
      "dedup rank, never a filter",
      guarded(lambda: survivors(["unknown"])), ["unknown"])

check("2f  entered-in-error is still the only status FILTERED, and it is "
      "filtered before the rank can see it",
      guarded(lambda: survivors(["entered-in-error"])), [])

check("2g  the ranks are 0/1/2 and total, so no two tiers can tie and let "
      "bundle order decide",
      guarded(lambda: sorted({_parser._medication_dedup_rank({"status": s})
                              for s in ("active", "on-hold", "draft",
                                        "intended", "completed", "stopped",
                                        "wholly-unknown-token", "unknown",
                                        "", None)})),
      [0, 1, 2])


# ===========================================================================
# 3. THE TWO DEGRADATION COUNTERS
# ===========================================================================

print("\n" + "=" * 74)
print("3. the counters")
print("=" * 74)

# THE REGISTRY IS PROCESS-GLOBAL AND THIS SECTION MOVES IT. `pytest tests/`
# imports every module into ONE process, so the restore in section 6 is the
# same discipline the rest of this suite applies -- taken HERE, above the first
# parse, so it captures the registry as this file found it.
_MED_AT_IMPORT = dict(_parser.MEDICATION_STATUS_MISSING)
_COND_AT_IMPORT = dict(_parser.CONDITION_STATUS_MISSING)


def cond_resource(code="73211009", display="Diabetes", status=...):
    resource = {
        "resourceType": "Condition",
        "code": {"coding": [{"system": "http://snomed.info/sct",
                             "code": code, "display": display}]},
        "onsetDateTime": "2010-01-01",
    }
    if status is not ...:
        resource["clinicalStatus"] = {"coding": [{"code": status}]}
    return resource


def parse_and_count(resources, module=_parser):
    """Parse, returning (per-patient dict, med counter delta, cond counter)."""
    module.MEDICATION_STATUS_MISSING.clear()
    module.CONDITION_STATUS_MISSING.clear()
    parsed = module.parse_fhir_bundle(bundle([_PATIENT_RES] + list(resources)))
    return (parsed.get("missing_status_counts"),
            dict(module.MEDICATION_STATUS_MISSING),
            dict(module.CONDITION_STATUS_MISSING))


_MIXED = [med_resource("unknown", "C1", "Cisplatin"),
          med_resource("completed", "C2", "Aspirin"),
          med_resource(None, "C3", "Metformin"),
          cond_resource("254837009", "Breast cancer", "active"),
          cond_resource("73211009", "Diabetes")]

_PER_PATIENT, _MED_KEYS, _COND_KEYS = guarded(
    lambda: parse_and_count(_MIXED)) or (None, None, None)

check("3a  the per-patient dict counts what reached the renderer, per family",
      _PER_PATIENT, {"conditions": 1, "medications": 2})

check("3b  the medication counter is keyed by spelling",
      _MED_KEYS, {"unknown": 2})

check("3c  the condition counter is keyed by spelling",
      _COND_KEYS, {"unknown": 1})

# THE COLLAPSE, RECORDED RATHER THAN CLAIMED AWAY. _parse_medication and
# _parse_condition both default an omitted status to the literal "unknown", so
# a source that OMITTED the element and one that said "unknown" arrive as one
# value and no counter can separate them. The `absent` / `empty` /
# `unusable:{type}` keys are reachable only from a hand-built record. Checked
# rather than asserted in prose, because a key set that cannot be reached at
# all is a key set nobody should read a zero in.
check("3d  a source that omits `status` entirely is counted as 'unknown', "
      "which is what the parser's own default makes it -- the documented "
      "collapse, not a missed case",
      guarded(lambda: parse_and_count([med_resource(None)])[1]),
      {"unknown": 1})

check("3e  the three non-'unknown' spellings ARE classified, so the key set "
      "is not decoration -- driven directly, since a bundle cannot produce "
      "them",
      guarded(lambda: [_parser._missing_status_key(e, "status") for e in
                       ({}, {"status": None}, {"status": ""},
                        {"status": "  "}, {"status": 7},
                        {"status": "unknown"}, {"status": "active"})]),
      ["absent", "absent", "empty", "empty", "unusable:int", "unknown", None])

check("3f  counted AFTER dedup: two unknown entries for ONE drug are one "
      "surviving entry and therefore one count, not two",
      guarded(lambda: parse_and_count(
          [med_resource("unknown", "C1", "Cisplatin"),
           med_resource("unknown", "C1", "Cisplatin")])[0]),
      {"conditions": 0, "medications": 1})

check("3g  counted AFTER the exclusion filter: an entered-in-error entry with "
      "no usable status contributes nothing, because it reaches no renderer",
      guarded(lambda: parse_and_count([med_resource("entered-in-error")])[1]),
      {})

check("3h  a clean patient records a MEASURED ZERO, never an absent key: 0 "
      "and NULL are different findings one column down",
      guarded(lambda: parse_and_count(
          [med_resource("active"), cond_resource(status="active")])[0]),
      {"conditions": 0, "medications": 0})

check("3i  both counters are REGISTERED in the degradation registry (the "
      "run-end report is their reader; tests/test_degradation_counter_"
      "readers.py enforces that every counter has one)",
      guarded(lambda: sorted(
          n for n in ("CONDITION_STATUS_MISSING", "MEDICATION_STATUS_MISSING")
          if n in _degradation.registered_names())),
      ["CONDITION_STATUS_MISSING", "MEDICATION_STATUS_MISSING"])

check("3j  ...and registered by IDENTITY, so the report reads the object the "
      "parser increments rather than a snapshot of it",
      guarded(lambda: (
          _degradation._REGISTRY["CONDITION_STATUS_MISSING"]
          is _parser.CONDITION_STATUS_MISSING
          and _degradation._REGISTRY["MEDICATION_STATUS_MISSING"]
          is _parser.MEDICATION_STATUS_MISSING)),
      True)

check("3k  ...and they are in the DEGRADATION registry, not the census: they "
      "move only on a defective record, unlike this module's five "
      "characterization counters",
      guarded(lambda: sorted(
          n for n in ("CONDITION_STATUS_MISSING", "MEDICATION_STATUS_MISSING")
          if n in _degradation.census_names())),
      [])

check("3l  a non-zero counter reaches the run-end report's text",
      guarded(lambda: all(
          n in "\n".join(_degradation.report_lines(
              {"MEDICATION_STATUS_MISSING": {"unknown": 3},
               "CONDITION_STATUS_MISSING": {"unknown": 1}}))
          for n in ("MEDICATION_STATUS_MISSING", "CONDITION_STATUS_MISSING"))),
      True)


# ===========================================================================
# 4. THE TWO COLUMNS
# ===========================================================================

print("\n" + "=" * 74)
print("4. the columns, the migration and the round trip")
print("=" * 74)

_COLUMNS = ("conditions_missing_status", "medications_missing_status")

check("4a  both columns are declared in INFERENCE_COLUMN_ADDITIONS as INTEGER",
      {c: _dl.INFERENCE_COLUMN_ADDITIONS.get(c) for c in _COLUMNS},
      {c: "INTEGER" for c in _COLUMNS})

_TMP = tempfile.mkdtemp(prefix="missing-status-")
_DB = os.path.join(_TMP, "scratch.db")

# THE SCRATCH PATH IS ASSERTED TO DIFFER FROM PRODUCTION before anything is
# written, on this suite's standing pattern: a test that created a production
# database would be the defect it is testing for.
check("4b  the scratch database is NOT the production inferences path",
      guarded(lambda: os.path.abspath(_DB)
              != os.path.abspath(_dl.resolve_inference_db_path(None))),
      True)

_dl.initialize_database(_DB)


def table_columns(db, table="inferences"):
    con = sqlite3.connect(db)
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    finally:
        con.close()


check("4c  a fresh database carries both columns",
      guarded(lambda: [c for c in _COLUMNS if c in table_columns(_DB)]),
      list(_COLUMNS))

# THE MIGRATION, on a database that genuinely lacks them AND carries a row.
# Built by copying the fresh schema minus the two columns, so the "before"
# shape is derived from this module's own CREATE + additions rather than
# retyped -- a hand-written pre-era table is a shape no era has ever had, which
# is the mistake tests/test_ablation_stop_and_lock.py had to fix in its own
# migration fixture.
_OLD_DB = os.path.join(_TMP, "era9.db")
_con = sqlite3.connect(_DB)
_ddl = _con.execute(
    "SELECT sql FROM sqlite_master WHERE type='table' AND name='inferences'"
).fetchone()[0]
_con.close()
_old_ddl = _ddl
for _c in _COLUMNS:
    _old_ddl = _old_ddl.replace(f", {_c} INTEGER", "")
_con = sqlite3.connect(_OLD_DB)
_con.execute(_old_ddl)
_con.execute("INSERT INTO inferences (patient_id, timestamp) VALUES ('old', 't')")
_con.commit()
_con.close()

check("4d  the pre-migration fixture really lacks both columns "
      "(non-degeneracy: without this 4e would pass against a table that "
      "already had them)",
      guarded(lambda: [c for c in _COLUMNS if c in table_columns(_OLD_DB)]),
      [])

_dl.initialize_database(_OLD_DB)

check("4e  initialize_database ADDS both columns to an existing database",
      guarded(lambda: [c for c in _COLUMNS if c in table_columns(_OLD_DB)]),
      list(_COLUMNS))

_con = sqlite3.connect(_OLD_DB)
check("4f  ...without destroying the row that was already there, and that "
      "row reads NULL in both -- the column did not exist when it was written",
      guarded(lambda: _con.execute(
          "SELECT patient_id, conditions_missing_status, "
          "medications_missing_status FROM inferences").fetchall()),
      [("old", None, None)])
_con.close()


def log(patient_id, patient_data):
    return _dl.log_inference(
        {"patient_id": patient_id, "timestamp": f"2026-01-01T00:00:0{patient_id[-1]}",
         "matching_model": "gpt-5.6-terra"}, patient_data, db_path=_DB)


_WROTE_MEASURED = guarded(lambda: log("p1", {
    "demographics": {}, "conditions": [], "medications": [],
    "missing_status_counts": {"conditions": 3, "medications": 0}}))
_WROTE_ABSENT = guarded(lambda: log("p2", {
    "demographics": {}, "conditions": [], "medications": []}))

check("4g  both writes succeeded", guarded(
    lambda: [bool(getattr(_WROTE_MEASURED, "ok", False)),
             bool(getattr(_WROTE_ABSENT, "ok", False))]), [True, True])

_con = sqlite3.connect(_DB)
check("4h  a measured 3 and a measured 0 round-trip as themselves",
      guarded(lambda: _con.execute(
          "SELECT conditions_missing_status, medications_missing_status "
          "FROM inferences WHERE patient_id='p1'").fetchone()),
      (3, 0))

check("4i  a caller that did not build patient_data through parse_fhir_bundle "
      "stores NULL in both -- no walk was recorded, which is not zero",
      guarded(lambda: _con.execute(
          "SELECT conditions_missing_status, medications_missing_status "
          "FROM inferences WHERE patient_id='p2'").fetchone()),
      (None, None))

check("4j  0 and NULL are DISTINGUISHABLE IN SQL, which is the whole reason "
      "the measured zero is stored rather than left absent",
      guarded(lambda: _con.execute(
          "SELECT COUNT(*) FROM inferences "
          "WHERE medications_missing_status = 0").fetchone()[0]),
      1)
_con.close()

# END TO END: the parser's own dict, unmodified, through the real writer.
_E2E = guarded(lambda: _parser.parse_fhir_bundle(bundle(
    [_PATIENT_RES, med_resource("unknown", "C1", "Cisplatin"),
     cond_resource("73211009", "Diabetes")])))
guarded(lambda: log("p3", _E2E))
_con = sqlite3.connect(_DB)
check("4k  END TO END: parse_fhir_bundle -> log_inference stores the parser's "
      "own numbers with no caller in between",
      guarded(lambda: _con.execute(
          "SELECT conditions_missing_status, medications_missing_status "
          "FROM inferences WHERE patient_id='p3'").fetchone()),
      (1, 1))
_con.close()


# ===========================================================================
# 5. THE ERA STAMP AND THE PROMPT DIGEST
# ===========================================================================

print("\n" + "=" * 74)
print("5. the era stamp and the prompt template")
print("=" * 74)

check("5a  the schema era is at least 10, which is the era these two columns "
      "were added in",
      _dl.SCHEMA_USER_VERSION >= 10, True)

_con = sqlite3.connect(_DB)
check("5b  a database this code created carries that era in its header",
      guarded(lambda: _con.execute("PRAGMA user_version").fetchone()[0]),
      _dl.SCHEMA_USER_VERSION)
_con.close()

# SCHEMA_USER_VERSION's own comment: "BUMP THIS IN THE SAME COMMIT THAT CHANGES
# THE SCHEMA. A stamp that lags the schema is worse than no stamp, because a
# reader acts on it." The note is what makes the number readable.
with open(_SOURCES["oncotriage/storage/database_logger.py"],
          encoding="utf-8") as _fh:
    _DL_SRC = _fh.read()
_ERA_HEAD = _DL_SRC.split("SCHEMA_USER_VERSION = ")[0]

check("5c  the current era has a note above the constant",
      f"# ERA {_dl.SCHEMA_USER_VERSION}:" in _ERA_HEAD, True)

check("5d  ...and that note names both columns, so a reader of the era record "
      "learns what changed rather than that something did",
      all(c in _ERA_HEAD for c in _COLUMNS), True)

# THE TEMPLATE MUST NOT HAVE MOVED. This pass edits a CHANGELOG COMMENT in
# oncotriage/agent/prompts.py and nothing else in that file; a template edit
# smuggled in beside a renderer fix would change what every stored
# llm_classifier_prompt_sha256 is comparable against, and would need a
# PROMPT_VERSION bump, a fixture recapture and a rater-rubric re-slice.
#
# THE REFERENCE IS TAKEN AT IMPORT AND THE DIGESTS ARE RECOMPUTED, so this
# check is about determinism WITHIN the run; the cross-run claim ("identical to
# the values before the comment edit") is recorded in the pass notes, where the
# before-image lives. What this DOES catch standing is a template that renders
# differently on two calls, and a variant branch that collapsed to one text.
_PROMPT_AT_IMPORT = guarded(lambda: _tracking._prompt_params())
_PROMPT_NOW = guarded(lambda: _tracking._prompt_params())

check("5e  the two Section 2 variants' digests are deterministic",
      _PROMPT_AT_IMPORT, _PROMPT_NOW)

check("5f  ...and the two variants are DIFFERENT texts, so 5e is not "
      "comparing one digest with itself twice",
      guarded(lambda: _PROMPT_NOW["prompt_template_sha256_site_confirmed"]
              != _PROMPT_NOW["prompt_template_sha256_site_unconfirmed"]),
      True)

check("5g  PROMPT_VERSION did not move: the template text is unchanged, so "
      "the middle number has nothing to record",
      _PROMPT_NOW["prompt_version"], "1.10.0")

# THE COMMENT THIS PASS FIXED SAID SOMETHING THAT IS NOW FALSE. Left standing
# it would tell the next reader the collapse is still there -- which is exactly
# the staleness the 1.10.0 block itself was written to avoid.
with open(os.path.abspath(_tracking.__file__).replace(
        "tracking.py", "agent/prompts.py"), encoding="utf-8") as _fh:
    _PROMPTS_SRC = _fh.read()

check("5h  the prompts changelog no longer claims patient.py renders "
      "`status: active` for an unknown medication status as a LIVE fact",
      "renders `status: active` for a\n# medication whose parsed status is "
      "`unknown`, which is RULE 2's own collapse and\n# predates this bump."
      in _PROMPTS_SRC,
      False)

check("5i  ...and the correction names the word the renderer prints instead, "
      "so the comment states the current behaviour rather than merely "
      "dropping the old claim",
      "prints `status: unknown`" in _PROMPTS_SRC, True)


# ===========================================================================
# 6. NEGATIVE CONTROLS
# ===========================================================================

print("\n" + "=" * 74)
print("6. negative controls")
print("=" * 74)

_control(
    "REVERT: 'unknown' back in the renderer's _ACTIVE_STATUSES -- the shipped "
    "defect. The absence of a statement reaches the judge as `status: active`",
    _PATIENT_SRC,
    [('_ACTIVE_STATUSES = {"active", "on-hold", "draft", "intended"}',
      '_ACTIVE_STATUSES = {"active", "on-hold", "draft", "intended", "unknown"}')],
    lambda m: med_lines([med("unknown")], m),
    ["- Cisplatin | status: active"],
    [_UNKNOWN_LINE])

# THE SHIPPED FORM, REVERTED -- and it has TWO holes, so it gets two controls.
_SHIPPED_STATUS = ('status     = (med.get("status") or "").lower().strip() '
                   'or "unknown"')

_control(
    "REVERT: the normalization back to `.get(\"status\", \"unknown\")` -- a "
    "source sending `\"status\": null` RAISES from inside the render, because "
    "a .get default does not apply to a key that is present and None",
    _PATIENT_SRC,
    [(_SHIPPED_STATUS, 'status     = med.get("status", "unknown").lower().strip()')],
    lambda m: med_lines([med(None)], m),
    "raised AttributeError: 'NoneType' object has no attribute 'lower'",
    [_UNKNOWN_LINE])

_control(
    "REVERT: the default moved BACK ABOVE the strip -- a whitespace-only "
    "status renders `status: ` with nothing after the label, which says less "
    "than either word. This is the defect this file found in its own pass",
    _PATIENT_SRC,
    [(_SHIPPED_STATUS,
      'status     = (med.get("status") or "unknown").lower().strip()')],
    lambda m: med_lines([med("   ")], m),
    ["- Cisplatin | status: "],
    [_UNKNOWN_LINE])

# THE PRE-PASS SORT KEY, RESTORED WHOLE. Reverting the frozenset ALONE does not
# reproduce the defect -- `_medication_dedup_rank` asks `_missing_status_key`
# FIRST and returns tier 2 whatever the set says, so the two mechanisms are
# independent and either one on its own is sufficient. That is defence in depth
# rather than redundancy, and it is measured immediately below rather than
# claimed: the first draft of this control reverted the set alone, expected the
# defect and was told the module was still correct.
_control(
    "REVERT: the whole pre-pass sort key -- one expression keyed on a set "
    "containing 'unknown', so an entry with no status wins the duplicate and "
    "the documented `completed` record is DELETED",
    _PARSER_SRC,
    [('_ACTIVE_MED_STATUSES          = frozenset({"active", "on-hold", '
      '"draft", "intended"})',
      '_ACTIVE_MED_STATUSES          = frozenset({"active", "on-hold", '
      '"draft", "intended", "unknown"})'),
     ("        sorted(patient_data['medications'], key=_medication_dedup_rank)",
      "        sorted(patient_data['medications'],\n"
      "               key=lambda m: (0 if m.get('status', 'unknown')"
      ".lower().strip()\n"
      "                              in _ACTIVE_MED_STATUSES else 1))")],
    lambda m: [survivors(["completed", "unknown"], m),
               survivors(["unknown", "completed"], m)],
    [["unknown"], ["unknown"]],
    [["completed"], ["completed"]])

_control(
    "THE SET ALONE NO LONGER DECIDES: putting 'unknown' back in "
    "_ACTIVE_MED_STATUSES without also reverting the rank leaves the correct "
    "answer, because the rank tests for a missing status FIRST",
    _PARSER_SRC,
    [('_ACTIVE_MED_STATUSES          = frozenset({"active", "on-hold", '
      '"draft", "intended"})',
      '_ACTIVE_MED_STATUSES          = frozenset({"active", "on-hold", '
      '"draft", "intended", "unknown"})')],
    lambda m: survivors(["completed", "unknown"], m),
    ["completed"],
    ["completed"])

_control(
    "REVERT: the dedup rank collapsed to two tiers -- 'unknown' ties with "
    "'completed' and BUNDLE ORDER decides which record survives",
    _PARSER_SRC,
    [("    if _missing_status_key(medication, 'status') is not None:\n"
      "        return 2\n",
      "    if _missing_status_key(medication, 'status') is not None:\n"
      "        return 1\n")],
    lambda m: [survivors(["unknown", "completed"], m),
               survivors(["completed", "unknown"], m)],
    [["unknown"], ["completed"]],
    [["completed"], ["completed"]])

_control(
    "REVERT: the counting block deleted -- the per-patient dict is absent, so "
    "the two columns store NULL on every row of a real run",
    _PARSER_SRC,
    [("    patient_data['missing_status_counts'] = {\n"
      "        'conditions': _count_missing_statuses(\n"
      "            patient_data['conditions'], 'clinical_status',\n"
      "            CONDITION_STATUS_MISSING),\n"
      "        'medications': _count_missing_statuses(\n"
      "            patient_data['medications'], 'status',\n"
      "            MEDICATION_STATUS_MISSING),\n"
      "    }\n", "")],
    lambda m: parse_and_count(_MIXED, m)[0],
    None,
    {"conditions": 1, "medications": 2})

_control(
    "REVERT: the counters walked BEFORE dedup -- two duplicate entries for one "
    "drug are counted twice, so the number stops describing what was rendered",
    _PARSER_SRC,
    [("        'medications': _count_missing_statuses(\n"
      "            patient_data['medications'], 'status',\n"
      "            MEDICATION_STATUS_MISSING),",
      "        'medications': _count_missing_statuses(\n"
      "            _pre_dedup_medications, 'status',\n"
      "            MEDICATION_STATUS_MISSING),"),
     ("    # Deduplicate by display name, keeping the entry that best describes",
      "    _pre_dedup_medications = list(patient_data['medications'])\n"
      "    # Deduplicate by display name, keeping the entry that best describes")],
    lambda m: parse_and_count(
        [med_resource("unknown", "C1", "Cisplatin"),
         med_resource("unknown", "C1", "Cisplatin")], m)[0],
    {"conditions": 0, "medications": 2},
    {"conditions": 0, "medications": 1})


# ===========================================================================
# 7. CLEANUP
# ===========================================================================

print("\n" + "=" * 74)
print("7. cleanup")
print("=" * 74)

_MED_BEFORE_RESTORE = dict(_parser.MEDICATION_STATUS_MISSING)
_parser.MEDICATION_STATUS_MISSING.clear()
_parser.MEDICATION_STATUS_MISSING.update(_MED_AT_IMPORT)
_parser.CONDITION_STATUS_MISSING.clear()
_parser.CONDITION_STATUS_MISSING.update(_COND_AT_IMPORT)

check("7a  the two counters are restored to what this file found, so a "
      "single-process run of the whole suite sees no key this file added",
      [dict(_parser.MEDICATION_STATUS_MISSING),
       dict(_parser.CONDITION_STATUS_MISSING)],
      [_MED_AT_IMPORT, _COND_AT_IMPORT])

# THE NON-DEGENERACY PROBE READS WHAT SECTION 3 OBSERVED, not the registry at
# the moment of the restore. Section 3 and section 6 both CLEAR the counters
# before each parse, so the last thing to run leaves them empty -- and if the
# registry was empty at import too, comparing before-restore against at-import
# reports "the restore did nothing" for a file that moved the counters five
# times. The first draft did exactly that and failed. What is actually being
# claimed is that this file put keys in them at all, which _MED_KEYS records.
check("7a  ...and the restore is not decoration: this file really did move "
      "both counters, so there was something to put back",
      [bool(_MED_KEYS), bool(_COND_KEYS)], [True, True])

shutil.rmtree(_TMP, ignore_errors=True)
check("7b  the scratch directory was removed", os.path.exists(_TMP), False)

check("7c  every repository file this run reads is byte-identical: every "
      "plant went into an in-memory copy",
      {rel: _sha(p) for rel, p in _SOURCES.items()}, _SHA_AT_START)

check("7c  ...and the three hashes are not all one value (non-degeneracy: a "
      "comparison of one file with itself would pass for free)",
      len(set(_SHA_AT_START.values())), 3)

check("7d  all eight controls really ran, so section 6's plants are not "
      "silently absent",
      _CONTROL_SEQ[0], 8)


# ===========================================================================
# SUMMARY
# ===========================================================================

print("\n" + "=" * 74)
print("SUMMARY")
print("=" * 74)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")
if _FAILURES:
    print("\nFailures:")
    for _f in _FAILURES:
        print(f"  - {_f}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------
# Created on 2026-09-02
#------------------------------------------------------------------------------
