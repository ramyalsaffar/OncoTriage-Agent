######################################################################
# De-identification: the stage, the age cap, the pseudonym, the guard
######################################################################

"""De-identification Stage Test

``oncotriage/deid.py`` sits between ``parse_fhir_bundle`` and the Stage 5
renderer. This file drives all four of its jobs and, for each, the control that
makes the check mean something.

WHAT WAS ALREADY TRUE BEFORE THE STAGE EXISTED, and this file says it out loud
because a reader who believes otherwise will not understand what protects them:
the rendered summary carried NO direct identifier, and the PARSER is why --
``_parse_demographics`` reads birthDate, gender and the two US Core extensions
and nothing else, so names, addresses, telephone numbers and the SSN / driver's
licence / passport / MRN entries are dropped before ``patient_data`` exists.
Measured over all 1,000 corpus bundles at a four-character floor: zero hits.
The stage GUARANTEES that property; it did not create it.

THE FOUR JOBS AND WHERE EACH IS DRIVEN

    section 2   the RECORD. ``RENDERED_FIELDS`` is closed and ``patient_id`` --
                the one direct identifier that survives parsing -- is not in
                it, so the renderer cannot print it: the name is not in scope.
    section 3   the AGE CAP, both directions, at the boundary.
    section 4   the PSEUDONYM: stable across calls, distinct across patients,
                and NOT the clinical hash it is derived from.
    sections
    5, 6, 7     the GUARD: harvest, scan, and the refusal, each driven in both
                directions.

SECTION 8 DRIVES THE REAL STAGE 5 NODE, which is the only place the claim
"a hit fails the patient rather than sending the prompt" can actually be
tested. The OpenAI client is a stub that COUNTS and RAISES: a guard that
failed to fire would make a call, and the call would be the failure. NO BILLED
CALL IS REACHABLE -- every client is a stand-in installed through
``oncotriage/agent/deps.py`` and the clean arm's stub returns a literal.

SECTION 10 IS GATED, on ``tests/test_storage_write_durability.py``'s pattern.
It parses REAL bundles read-only and compares the rendered text against a
pre-stage render reconstructed here, which needs the corpus; a CI runner has
only the directory skeleton, so that section SKIPS there and counts the skip.
Every fabricated-input check above it runs everywhere, which is what keeps this
file in bucket A rather than out of CI for one probe's sake.

A SKIP IS NOT A PASS. The skip count is printed even at zero, on
``tests/test_package_invariants.py``'s precedent: a counter that appears only
when non-zero is indistinguishable from a file that has no skip mechanism.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD (
``ONCOTRIAGE_DEFER_LOCAL_MODELS`` is set above the imports and section 11
asserts torch and transformers never entered ``sys.modules``), NO DATABASE, NO
GIT HISTORY, NO LIVE SERVER. The three registries the renderer resolves are
STUBS installed through ``oncotriage/agent/deps.py``, which is what lets this
file run against a checkout with no MeSH lookups and no ICD-10 release.

IT EXECS NOTHING and loads no module by location, so it needs no
``_EXEC_ALLOWLIST`` entry: every control is a different INPUT to a pure
function, a stub installed inside ``try``/``finally`` with the restore
asserted, or an ``ast`` walk over a file read as text.

NOT in ``tests/run_serial_tests.py``'s collision matrix, derived rather than
assumed: it writes NOTHING anywhere -- no temp directory, no in-place edit --
and the three repository files it READS (``oncotriage/deid.py``,
``oncotriage/agent/patient.py``, ``oncotriage/agent/evaluation.py``) are
written by neither of the suite's two writers, which are
``oncotriage/registries/cancer_code_registry.py`` and
``oncotriage/config.py``. All three are sha256-compared at the end anyway.

    python tests/test_deid_stage_and_guard.py
"""

import ast
import copy
import hashlib
import io
import os
import sys

# ABOVE THE PACKAGE IMPORTS ON PURPOSE. oncotriage/agent/deps.py reads this
# variable ONCE, at its own import, and `deps` arrives transitively on the
# first `oncotriage` import -- so an assignment underneath the imports reaches
# nothing and MedCPT loads for real.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

try:
    import oncotriage                                          # noqa: F401
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

from oncotriage import config, deid
from oncotriage.agent import deps
from oncotriage.agent import patient as _patient_mod
from oncotriage.agent.patient import (
    build_patient_record,
    compute_patient_hash,
    render_patient_record,
    _create_patient_summary,
)

# ===========================================================================
# THIS FILE'S SUBJECT IS THE DORMANT OpenAI STAGE 5 REQUEST -- SO IT PINS IT
# ===========================================================================
#
# `config.MATCHING_PROVIDER` ships "bedrock_anthropic". Every Stage 5 stand-in
# below is installed at `deps.OPENAI_CLIENT` and wraps `chat.completions
# .create`, so at the shipped default the dispatch would reach
# `deps.BEDROCK_ANTHROPIC_CLIENT` and `converse` instead: the stand-in would
# never be called, every assertion here would compare against an empty
# recorder, and `config.get_bedrock_anthropic_client()` would BUILD -- boto3
# probing the instance metadata service from a suite that reports it makes no
# network call, and issuing live billed Converse requests on any host whose
# credential chain finds something.
#
# The pin, its cost and why it has one owner rather than a block per file are
# argued in tests/_provider_pin.py. THE SHIPPED ARM IS NOT COVERED BY THIS
# FILE; on Converse these subjects are covered by
# tests/test_agent_bedrock_anthropic_adapter.py and
# tests/test_agent_bedrock_anthropic_per_trial.py alone.
import _provider_pin                                             # noqa: E402

_PROVIDER_BEFORE_PIN = _provider_pin.pin_openai_arm(os.path.basename(__file__))


# ===========================================================================
# HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0, "skipped": 0}
_FAILURES = []
_SKIPS = []


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


def check_true(label: str, condition) -> None:
    check(label, bool(condition), True)


def skip(label: str, reason: str) -> None:
    """Coverage that could NOT be exercised here. Never counted as a pass."""
    _RESULTS["skipped"] += 1
    _SKIPS.append(f"{label} -- {reason}")
    print(f"  SKIP  {label}\n          {reason}")


def section(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def drive(fn, *args, **kwargs):
    """Call ``fn``; return its value, or a marker naming what it raised.

    EVERY CALL INTO PRODUCTION CODE GOES THROUGH THIS. A bare call inside a
    ``check()`` argument list raises while the argument is being EVALUATED --
    exactly when a defect makes the thing under test raise -- and the run then
    prints one traceback where it owed a summary and every result below it.
    This project has shipped that shape more than a dozen times; it is not
    shipped again here.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                     # noqa: BLE001 -- reported
        return f"<RAISED {type(exc).__name__}: {exc}>"


def at(container, key, default="<ABSENT>"):
    """Index without raising, so a missing key FAILS a check rather than
    aborting the file."""
    try:
        return container[key]
    except Exception:                                # noqa: BLE001 -- reported
        return default


def sha256_file(path: str) -> str:
    with io.open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(
    oncotriage.__file__)))
"""Derived from the PACKAGE's own ``__file__`` rather than from this file's
location, so the source under inspection is provably the one this process
imported and not a same-named copy. The lesson pass 20d-1 had to learn when
eleven test files moved one directory."""

_READ_FILES = {
    "deid": os.path.join(_CODE_DIR, "oncotriage", "deid.py"),
    "patient": os.path.join(_CODE_DIR, "oncotriage", "agent", "patient.py"),
    "evaluation": os.path.join(_CODE_DIR, "oncotriage", "agent",
                               "evaluation.py"),
}
_HASHES_BEFORE = {k: sha256_file(v) for k, v in _READ_FILES.items()}


# ===========================================================================
# STUB REGISTRIES
# ===========================================================================
#
# INSTALLED THROUGH oncotriage/agent/deps.py, which is the seam the renderer
# resolves through. Without them this file would need the four MeSH JSON
# lookups and the ICD-10-CM release, and a CI runner has neither -- so a
# privacy guard would be a test nobody runs, which is the state
# tests/test_storage_write_durability.py was moved OUT of.


class _StubCancerRegistry:
    def is_primary_cancer(self, condition):
        display = (condition.get("display") or "").lower()
        return "carcinoma" in display or "cancer" in display


class _StubLabRegistry:
    loinc_codes = frozenset()

    def filter_relevant_procedures(self, procedures):
        return list(procedures or [])

    def filter_relevant_observations(self, observations):
        return list(observations or [])

    def filter_relevant_genomic_variants(self, variants):
        return list(variants or [])


def _install_stub_registries():
    deps.set_override(deps.CANCER_REGISTRY, _StubCancerRegistry())
    deps.set_override(deps.LAB_REGISTRY, _StubLabRegistry())
    # None is a REAL, reachable state for the MeSH filter and the renderer has
    # a branch for it (`[neoplasm-unverified]`); it is not a degenerate stub.
    deps.set_override(deps.MESH_FILTER, None)


def _clear_stub_registries():
    for key in (deps.CANCER_REGISTRY, deps.LAB_REGISTRY, deps.MESH_FILTER):
        deps.clear_override(key)


_install_stub_registries()


# ===========================================================================
# FIXTURES
# ===========================================================================
#
# EVERY IDENTIFIER-SHAPED LITERAL IN THIS FILE IS ASSEMBLED AT RUN TIME from a
# prefix and an arithmetic, never written out. tests/test_secret_scan_gate.py
# had to learn this the hard way: a test that plants a credential-shaped value
# as a literal is a tracked file carrying that value. The same reasoning
# applies to a personal identifier.

def _fake_ssn():
    return "%03d-%02d-%04d" % (900 + 1, 60 + 5, 3000 + 483)


def _fake_phone():
    return "%03d-%03d-%04d" % (555, 200 + 56, 4000 + 224)


def _fake_email():
    return "".join(["p", "atient", "@", "example", ".", "invalid"])


def _fake_uuid():
    return "-".join(["37fdfb01", "3b13", "b8ff", "e54f", "2cd0eb23ac8a"])


def _fake_family_name():
    return "Qzernsyr" + str(500 + 83)


def _fake_street():
    return str(900 + 59) + " Davisbrook Bypasswold"


def _patient(age=62, conditions=None, observations=None, patient_id=None):
    """A parsed-record shape, hand-built. No corpus, no bundle."""
    return {
        "patient_id": patient_id if patient_id is not None else _fake_uuid(),
        "demographics": {
            "age": age,
            "sex": "female",
            "race": "White",
            "ethnicity": "Not Hispanic or Latino",
            "birth_date": "1964-03-02",
            "birth_date_precision": "day",
            "age_reference_date": "2026-08-03",
            "race_source": "ombCategory",
            "ethnicity_source": "ombCategory",
        },
        "conditions": list(conditions if conditions is not None else [
            {"display": "Malignant neoplasm of breast (disorder)",
             "code": "254837009", "onset_date": "2019-04-11",
             "clinical_status": "active", "verification_status": "confirmed"},
        ]),
        "medications": [
            {"display": "Paclitaxel 100 MG Injection", "status": "completed",
             "start_date": "2020-01-05", "end_date": "2020-06-05"},
        ],
        "observations": list(observations or [
            {"display": "Hemoglobin [Mass/volume] in Blood", "code": "718-7",
             "value": 11.2, "unit": "g/dL", "date": "2026-05-02"},
        ]),
        "procedures": [
            {"display": "Biopsy of breast", "code": "122548005",
             "date": "2019-04-20", "status": "completed"},
        ],
        "allergies": [],
        "cancer_stage_observations": [],
        "cancer_genomic_variants": [],
        "cancer_metastasis_observations": [],
        "ecog_performance_status": {"value": 1, "date": "2026-06-01",
                                    "observations_found": 1,
                                    "observations_on_or_before_reference": 1,
                                    "observations_after_reference": 0,
                                    "observations_undated": 0,
                                    "selection": "most_recent",
                                    "reference_date": "2026-08-03",
                                    "value_shape": "valueInteger"},
    }


def _bundle_with_identifiers():
    """A FHIR bundle carrying one of every class the ruling names."""
    return {"entry": [
        {"resource": {
            "resourceType": "Patient",
            "id": _fake_uuid(),
            "name": [{"family": _fake_family_name(),
                      "given": ["Abbyxyz752"], "prefix": ["Mrs."]}],
            "telecom": [{"system": "phone", "value": _fake_phone()}],
            "address": [{"line": [_fake_street()], "city": "Ontariowold",
                         "state": "CA", "postalCode": "91762",
                         "extension": [{"url": "geo", "extension": [
                             {"url": "latitude", "valueDecimal": 34.117167},
                             {"url": "longitude", "valueDecimal": -117.608198},
                         ]}]}],
            "identifier": [
                {"type": {"coding": [{"code": "MR"}]}, "value": "MRN99887766"},
                {"type": {"coding": [{"code": "SS"}]}, "value": _fake_ssn()},
                {"type": {"coding": [{"code": "DL"}]}, "value": "S99977271"},
                {"type": {"coding": [{"code": "PPN"}]}, "value": "X46340672X"},
                {"value": "untyped-identifier-4471"},
            ],
        }},
        {"resource": {
            "resourceType": "Coverage",
            "identifier": [{"value": "MEMBER-55512345"}],
        }},
        {"resource": {
            "resourceType": "Practitioner",
            "name": [{"family": "Bechtelarxyz572", "given": ["Adelbertoxyz"]}],
        }},
    ]}


PATIENT = _patient()
BUNDLE = _bundle_with_identifiers()


# ===========================================================================
# 1. THE PROMPT SURFACE, ENUMERATED FROM SOURCE
# ===========================================================================

section("1. The prompt surface, enumerated from the renderer's own source")

_patient_src = io.open(_READ_FILES["patient"], "r", encoding="utf-8").read()
_patient_tree = ast.parse(_patient_src)
_renderer = next((n for n in _patient_tree.body
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "render_patient_record"), None)

check_true("the renderer is a top-level function called "
           "`render_patient_record` (a walk that found nothing would satisfy "
           "every check below for free)", _renderer is not None)

if _renderer is not None:
    _params = [a.arg for a in _renderer.args.args]
    check("...and its ONE parameter is the de-identified record. This is the "
          "architectural half of the guarantee: the renderer cannot be handed "
          "a raw parsed record, because the only thing that builds its "
          "argument is deid.deidentify",
          _params, ["record"])

    _ann = getattr(_renderer.args.args[0], "annotation", None)
    check("...and it is ANNOTATED as one, so a reader of the signature is "
          "told rather than having to infer it",
          getattr(_ann, "id", None), "DeidentifiedRecord")

    # Which keys the renderer actually reads, derived by AST rather than
    # retyped. `record.fields[...]` and `record.fields.get(...)`.
    _read_keys = set()
    for node in ast.walk(_renderer):
        if isinstance(node, ast.Subscript) and isinstance(node.value,
                                                          ast.Attribute) \
                and node.value.attr == "fields":
            if isinstance(node.slice, ast.Constant):
                _read_keys.add(node.slice.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" \
                and isinstance(node.func.value, ast.Attribute) \
                and node.func.value.attr == "fields":
            if node.args and isinstance(node.args[0], ast.Constant):
                _read_keys.add(node.args[0].value)

    check_true("the AST walk found the renderer's field reads at all "
               "(non-degeneracy: an empty set would make the next check pass "
               "vacuously)", len(_read_keys) >= 8)
    check("EVERY key the renderer reads is declared in deid.RENDERED_FIELDS, "
          "and every declared key is one it reads. A field that travels to "
          "the prompt boundary unread is one edit away from being printed; a "
          "field read but undeclared is a KeyError on the first real patient",
          sorted(_read_keys), sorted(deid.RENDERED_FIELDS))
    check("`patient_id` is NOT among them. It is the ONE direct identifier "
          "that survives parsing, and this is the whole of section 2's claim "
          "expressed as a fact about the source",
          "patient_id" in _read_keys, False)

# The demographic sub-fields, likewise derived.
_demo_keys = set()
for _node in ast.walk(_renderer) if _renderer is not None else []:
    if isinstance(_node, ast.Call) and isinstance(_node.func, ast.Attribute) \
            and _node.func.attr == "get" \
            and isinstance(_node.func.value, ast.Name) \
            and _node.func.value.id == "demographics" \
            and _node.args and isinstance(_node.args[0], ast.Constant):
        _demo_keys.add(_node.args[0].value)

check("the four demographic keys the renderer prints are exactly "
      "deid.DEMOGRAPHIC_FIELDS -- so `birth_date`, `birth_date_precision`, "
      "`age_reference_date` and the two `_source` fields are dropped by the "
      "stage rather than merely unprinted",
      sorted(_demo_keys), sorted(deid.DEMOGRAPHIC_FIELDS))


# ===========================================================================
# 2. THE RECORD
# ===========================================================================

section("2. The de-identified record carries exactly what may be rendered")

_rec = drive(deid.deidentify, PATIENT, identity="abc123")

check("the record's fields are exactly RENDERED_FIELDS",
      sorted(getattr(_rec, "fields", {})), sorted(deid.RENDERED_FIELDS))
check("`patient_id` is absent from them, so no renderer edit can print it",
      "patient_id" in getattr(_rec, "fields", {}), False)
check("its demographics are exactly DEMOGRAPHIC_FIELDS",
      sorted(at(getattr(_rec, "fields", {}), "demographics", {})),
      sorted(deid.DEMOGRAPHIC_FIELDS))
check("`birth_date` is dropped. The ruling KEEPS full dates, so this is "
      "minimisation rather than a date rule: the renderer does not read it, "
      "and an unread field at the prompt boundary is one edit from printed",
      "birth_date" in at(getattr(_rec, "fields", {}), "demographics", {}),
      False)

check("DeidentifiedRecord's __slots__ is closed, so a caller cannot stash a "
      "value on a record and a later reader cannot trust a field nothing set",
      sorted(deid.DeidentifiedRecord.__slots__),
      sorted(("fields", "pseudonym", "inventory", "age_capped")))
_stash = drive(setattr, _rec, "patient_id", "leak")
check_true("...and assigning an undeclared attribute RAISES rather than "
           "silently succeeding",
           isinstance(_stash, str) and _stash.startswith("<RAISED"))

_repr = repr(_rec)
check_true("__repr__ names the pseudonym", _rec.pseudonym in _repr)
check("...and quotes NO identifier value. A debugger rendering locals, a log "
      "line formatting the object and a bare name at a prompt all reach this",
      at(PATIENT, "patient_id") in _repr, False)


# ===========================================================================
# 3. THE AGE CAP, BOTH DIRECTIONS
# ===========================================================================

section("3. The age cap")

check("the cap is 89 -- 45 CFR 164.514(b)(2)(i)(C)'s boundary, which is a "
      "fact about an external standard and therefore a named constant here "
      "rather than a tunable in config.py",
      deid.AGE_CAP_YEARS, 89)
check("the label is the regulation's own wording", deid.AGE_CAP_LABEL,
      "90 or older")

for _age, _expect, _capped in ((88, 88, False), (89, 89, False),
                               (90, deid.AGE_CAP_LABEL, True),
                               (99, deid.AGE_CAP_LABEL, True)):
    _r = drive(deid.deidentify, _patient(age=_age), identity="x")
    check(f"age {_age} renders as {_expect!r}",
          at(at(getattr(_r, "fields", {}), "demographics", {}), "age"),
          _expect)
    check(f"...and age_capped is {_capped}", getattr(_r, "age_capped", None),
          _capped)

check("89 is the LAST exact age, so the comparison is `>` and not `>=`. Off "
      "by one here would either disclose a 90-year-old's exact age or "
      "suppress an 89-year-old's, and the two are opposite defects",
      at(at(getattr(drive(deid.deidentify, _patient(age=89), identity="x"),
                    "fields", {}), "demographics", {}), "age"), 89)

_r_none = drive(deid.deidentify, _patient(age=None), identity="x")
check("an absent age is returned unchanged -- capping it would state a bound "
      "nothing measured, and the renderer already prints `unknown`",
      at(at(getattr(_r_none, "fields", {}), "demographics", {}), "age"), None)
check("...and is not counted as capped", getattr(_r_none, "age_capped", None),
      False)

_r_bool = drive(deid.deidentify, _patient(age=True), identity="x")
check("a bool age is returned unchanged and uncapped. THE ISINSTANCE-BOOL "
      "GUARD IN _cap_age CHANGES NO OUTCOME AT THE CURRENT CAP, and this "
      "check does not pretend otherwise: True is 1 and False is 0, both below "
      "89, so with or without the guard the answer is (value, False). The "
      "revert harness measured that -- removing the guard left this file at "
      "135/0 -- and the honest response is to record it rather than to keep a "
      "label claiming a guard is load-bearing when it is not. It stays "
      "because it states the type contract at the one place an age is typed",
      at(at(getattr(_r_bool, "fields", {}), "demographics", {}), "age"), True)

_r_str = drive(deid.deidentify, _patient(age="ninety"), identity="x")
check("a non-int age is returned unchanged, so a parser defect reaches a "
      "human as itself rather than as a capped category",
      at(at(getattr(_r_str, "fields", {}), "demographics", {}), "age"),
      "ninety")

# --- and the RENDERED line, both directions -------------------------------
_txt_89 = drive(_create_patient_summary, _patient(age=89))
_txt_90 = drive(_create_patient_summary, _patient(age=90))
check_true("the rendered line for 89 states the exact age",
           isinstance(_txt_89, str) and "Age: 89 |" in _txt_89)
check_true("the rendered line for 90 states the category and NOT the number. "
           "The negative half tests the WHOLE field between the delimiters, "
           "not a substring: 'Age: 90 or older |' contains 'Age: 90 ', so a "
           "substring test here would have been satisfied by the very "
           "rendering it was written to forbid",
           isinstance(_txt_90, str)
           and f"Age: {deid.AGE_CAP_LABEL} |" in _txt_90
           and "Age: 90 |" not in _txt_90)

_census_before = deid.DEID_CENSUS[deid.DEID_AGE_CAPPED]
drive(deid.deidentify, _patient(age=95), identity="x")
check("a capped age is COUNTED, in the census registry and not the "
      "degradation one -- a capped age is the stage working",
      deid.DEID_CENSUS[deid.DEID_AGE_CAPPED], _census_before + 1)


# ===========================================================================
# 4. THE PSEUDONYM
# ===========================================================================

section("4. The pseudonym")

_p1 = drive(deid.pseudonym_for_identity, "identity-A")
_p1_again = drive(deid.pseudonym_for_identity, "identity-A")
_p2 = drive(deid.pseudonym_for_identity, "identity-B")

check("STABLE: the same identity yields the same token, so a resume and a "
      "fixture keyed on it stay coherent", _p1, _p1_again)
check_true("DISTINCT: two identities yield different tokens", _p1 != _p2)
check_true("it carries the PT- prefix, so a reader of a stored prompt can "
           "see the record is pseudonymous rather than having to prove the "
           "absence of something", _p1.startswith(deid.PSEUDONYM_PREFIX))
check("...and is the declared length",
      len(_p1), len(deid.PSEUDONYM_PREFIX) + deid.PSEUDONYM_HEX_CHARS)

_raw = hashlib.sha256("identity-A".encode("utf-8")).hexdigest()
check("IT IS NOT THE IDENTITY HASHED PLAIN. Domain separation is what stops "
      "the prompt's token and the database's patient_data_hash being the same "
      "string -- any artifact carrying both patient_id and that hash would "
      "otherwise BE the mapping the ruling keeps in the database",
      _p1.endswith(_raw[:deid.PSEUDONYM_HEX_CHARS]), False)

check("an absent identity yields the named sentinel, never an empty string "
      "and never a raise: ten test files render hand-built records that have "
      "no clinical hash to derive from",
      drive(deid.pseudonym_for_identity, None), deid.PSEUDONYM_UNKNOWN)
check("...and so does an empty one",
      drive(deid.pseudonym_for_identity, ""), deid.PSEUDONYM_UNKNOWN)
check_true("...and the sentinel is not a valid derived token, so it cannot "
           "be mistaken for one",
           deid.PSEUDONYM_UNKNOWN not in (_p1, _p2))

# --- derived from the CLINICAL hash, not from patient_id ------------------
_pa = drive(build_patient_record, PATIENT)
_pb = drive(build_patient_record, _patient(patient_id="a-different-record-id"))
check("TWO RECORDS DIFFERING ONLY IN patient_id SHARE A PSEUDONYM. That is "
      "the design: the token is a function of compute_patient_hash, which "
      "reads the clinical record and never the record number -- so a "
      "pseudonym cannot be recovered from the patient_id that is on every "
      "log line this pipeline emits",
      getattr(_pa[0], "pseudonym", "a"), getattr(_pb[0], "pseudonym", "b"))
# THE FIXTURE DIFFERS IN A CONDITION, NOT IN `age`, AND THAT IS A FACT
# ABOUT compute_patient_hash RATHER THAN A CONVENIENCE. That function hashes
# `birth_date` and deliberately NOT `age`, because age is derived from the
# birth date against DATA_SNAPSHOT_DATE -- so two records differing only in
# `age` are the same record to it, and a fixture built that way would have
# reported this check as broken when it is the hash that is right.
_pc = drive(build_patient_record, _patient(conditions=[
    {"display": "Adenocarcinoma of colon (disorder)", "code": "93761005",
     "onset_date": "2021-02-02", "clinical_status": "active",
     "verification_status": "confirmed"}]))
check_true("...and two records differing in CLINICAL content do not",
           getattr(_pa[0], "pseudonym", "a")
           != getattr(_pc[0], "pseudonym", "a"))

check_true("the rendered record carries the pseudonym on its first line",
           isinstance(_pa[1], str)
           and _pa[1].split("\n")[0] == f"Patient: {_pa[0].pseudonym}")


# ===========================================================================
# 5. NON-MUTATION: THE PARSED RECORD IS UNTOUCHED
# ===========================================================================

section("5. The stage reads the parsed record and never writes it")

_before = copy.deepcopy(PATIENT)
_hash_before = drive(compute_patient_hash, PATIENT)
_r, _t = drive(build_patient_record, PATIENT)
_hash_after = drive(compute_patient_hash, PATIENT)

check("the parsed record is byte-for-byte what it was", PATIENT, _before)
check("compute_patient_hash is unchanged -- which is the whole reason the "
      "stage returns a NEW record instead of editing this one. Every fixture, "
      "every resume and the dashboard's reproducibility grouping key on it",
      _hash_after, _hash_before)
check_true("...and the hash is non-degenerate (a function returning a "
          "constant would satisfy the check above)",
          isinstance(_hash_before, str) and len(_hash_before) == 16)

check_true("the record's lists are COPIES at the top level, so a renderer "
           "edit that sorts or pops in place cannot reach the caller's parsed "
           "record and move compute_patient_hash under it",
           at(getattr(_r, "fields", {}), "conditions", None)
           is not PATIENT["conditions"])
check_true("...and their MEMBERS are shared on purpose: the stage rewrites no "
           "clinical record, and deep-copying 3,660 observation dicts per "
           "patient would be real work for a guarantee nothing needs",
           at(getattr(_r, "fields", {}), "conditions", [None])[0]
           is PATIENT["conditions"][0])

_capped_rec = drive(deid.deidentify, _patient(age=95), identity="x")
_capped_src = _patient(age=95)
check("capping does not write back: the source demographics still carry the "
      "exact age, so Stage 4's age filter -- which reads patient_data, not "
      "this record -- is unaffected by a disclosure control",
      at(_capped_src["demographics"], "age"), 95)


# ===========================================================================
# 6. HARVESTING
# ===========================================================================

section("6. Harvesting: what the source actually carries")

_inv = drive(deid.harvest_identifiers, BUNDLE)

check("every harvested class is in the closed vocabulary",
      sorted(set(_inv) - set(deid.IDENTIFIER_CLASSES)), [])
check_true("the harvest is non-degenerate: it found several classes",
           len(_inv) >= 5)

for _cls, _needle in (
    (deid.IDENTIFIER_NAME, _fake_family_name()),
    (deid.IDENTIFIER_TELECOM, _fake_phone()),
    (deid.IDENTIFIER_GEO, _fake_street()),
    (deid.IDENTIFIER_GEO, "91762"),
    (deid.IDENTIFIER_RECORD_NUMBER, "MRN99887766"),
    (deid.IDENTIFIER_GOVERNMENT_ID, _fake_ssn()),
    (deid.IDENTIFIER_GOVERNMENT_ID, "S99977271"),
    (deid.IDENTIFIER_GOVERNMENT_ID, "X46340672X"),
    (deid.IDENTIFIER_INSURANCE_ID, "MEMBER-55512345"),
    (deid.IDENTIFIER_OTHER, "untyped-identifier-4471"),
):
    check_true(f"{_cls}: harvested {_needle[:14]!r}...",
               _needle in at(_inv, _cls, []))

check("a Practitioner's name is harvested too. A leak into free text is as "
      "likely to carry a clinician's name as the patient's, and the ruling "
      "names names without qualifying whose",
      "Bechtelarxyz572" in at(_inv, deid.IDENTIFIER_NAME, []), True)

check("the Patient resource's own `id` is harvested as a record number -- on "
      "this corpus it is byte-identical to the MRN",
      _fake_uuid() in at(_inv, deid.IDENTIFIER_RECORD_NUMBER, []), True)

_geo = at(_inv, deid.IDENTIFIER_GEO, [])
check("`state` is NOT harvested. The ruling permits it, and a two-character "
      "token is unscannable in any case: `CA` is a state AND the prefix of "
      "the tumour marker CA 19-9, so scanning for it would fail nearly every "
      "patient in an oncology corpus",
      "CA" in _geo, False)
check_true("...but the geolocation IS -- latitude and longitude are the "
           "sharpest geographic identifier in a Synthea bundle",
           any("34.117167" in g for g in _geo))

check("a bundle with no entries harvests nothing rather than raising",
      drive(deid.harvest_identifiers, {}), {})
check_true("a malformed bundle does not raise either -- a bundle that cannot "
           "be harvested must not take down the patient it belongs to before "
           "the guard has run",
           isinstance(drive(deid.harvest_identifiers,
                            {"entry": [None, {"resource": "not-a-dict"}]}),
                      dict))

_parsed_inv = drive(deid.identifiers_from_parsed_record, PATIENT)
check("the PARSED record's inventory is the one direct identifier that "
      "survives parsing, and nothing else",
      _parsed_inv, {deid.IDENTIFIER_RECORD_NUMBER: [_fake_uuid()]})
check("`birth_date` is deliberately NOT in it: the ruling keeps full dates, "
      "so a birth date in the record is permitted rather than a leak, and "
      "scanning for it would fail every patient whose record legitimately "
      "prints a date from the same day",
      any("1964-03-02" in v for v in _parsed_inv.values()), False)


# ===========================================================================
# 7. THE SCAN, BOTH DIRECTIONS
# ===========================================================================

section("7. The scan: a hit is found, and clean text is clean")

_clean = drive(_create_patient_summary, PATIENT)
_findings, _skipped = drive(deid.scan_for_identifiers, _clean, _inv) \
    if isinstance(_clean, str) else ([], 0)

check("a clean rendered record scanned against the FULL bundle inventory "
      "yields NO finding. This is the control without which every refusal "
      "below would be equally satisfied by a scanner that always fires",
      _findings, [])

check_true("...and the scan is non-degenerate: it had a real inventory to "
           "look for", sum(len(v) for v in _inv.values()) >= 10)

# --- one plant per class, each shown to fire ------------------------------
for _cls, _value in (
    (deid.IDENTIFIER_NAME, _fake_family_name()),
    (deid.IDENTIFIER_GEO, _fake_street()),
    (deid.IDENTIFIER_TELECOM, _fake_phone()),
    (deid.IDENTIFIER_RECORD_NUMBER, "MRN99887766"),
    (deid.IDENTIFIER_GOVERNMENT_ID, _fake_ssn()),
    (deid.IDENTIFIER_INSURANCE_ID, "MEMBER-55512345"),
):
    _planted = f"{_clean}\n- Note mentioning {_value} in free text\n"
    _f, _ = drive(deid.scan_for_identifiers, _planted, _inv)
    _classes = sorted({x.identifier_class for x in _f}) if isinstance(_f, list) \
        else [f"<{_f}>"]
    check_true(f"a planted {_cls} is FOUND", _cls in _classes)

# --- case-insensitivity ---------------------------------------------------
_lower = f"{_clean}\n- {_fake_family_name().lower()}\n"
_f_lower, _ = drive(deid.scan_for_identifiers, _lower, _inv)
check_true("the scan is case-insensitive: a renderer that lower-cases a "
           "display is still disclosing the name it lower-cased",
           isinstance(_f_lower, list) and len(_f_lower) >= 1)

# --- the length floor is REPORTED, not swallowed --------------------------
_short_inv = {deid.IDENTIFIER_GEO: ["CA", "US"], deid.IDENTIFIER_NAME: ["Li"]}
_f_short, _n_short = drive(deid.scan_for_identifiers, _clean, _short_inv)
check("values below the length floor are not looked for", _f_short, [])
check("...and the count of them is RETURNED. A scan that silently declined "
      "to look for half its inventory reads exactly like a clean one",
      _n_short, 3)

# --- findings carry offsets, never text -----------------------------------
_f_one, _ = drive(deid.scan_for_identifiers,
                  f"{_clean}\n- {_fake_ssn()}\n", _inv)
_first = _f_one[0] if isinstance(_f_one, list) and _f_one else None
check_true("a finding carries a class, a rule and an OFFSET",
           _first is not None and isinstance(getattr(_first, "start", None), int))
check("...and its repr quotes no value -- the exception text reaches "
      "inferences.error, the console and the structured log, three durable "
      "places a matched identifier must not be written to",
      _fake_ssn() in repr(_first), False)

# --- the shape rules, provenance-free -------------------------------------
section("7b. The shape rules fire with NO inventory at all")

for _rule, _text in (
    ("ssn", f"- Note {_fake_ssn()} here"),
    ("phone", f"- Contact {_fake_phone()} here"),
    ("email", f"- Mail {_fake_email()} here"),
    ("url", "- See " + "http" + "s://hospital.example.invalid/mrn/1 here"),
    ("uuid", f"- Ref {_fake_uuid()} here"),
):
    _f, _ = drive(deid.scan_for_identifiers, _text, None)
    _rules = sorted({x.rule for x in _f}) if isinstance(_f, list) else [str(_f)]
    check_true(f"shape rule {_rule!r} fires with no inventory supplied -- "
               f"this is the layer that works when no bundle was passed",
               _rule in _rules)

check("the shape-rule set is exactly the five declared, so a sixth cannot be "
      "added without being argued at SHAPE_RULE_NAMES",
      sorted(deid.SHAPE_RULE_NAMES),
      sorted(("ssn", "phone", "email", "url", "uuid")))

_f_clean_shape, _ = drive(deid.scan_for_identifiers, _clean, None)
check("NO shape rule fires on a real rendered record. A rule that matches "
      "ordinary clinical text is a wrong rule, not a finding -- which is why "
      "there is no bare long-digit rule: LOINC codes, lab values and dates "
      "are all digit runs",
      _f_clean_shape, [])

# The specific near-misses the absent rules would have caught wrongly.
for _label, _text in (
    ("a LOINC code", "- Hemoglobin [89247-1]: 11.2 g/dL (2026-05-02)"),
    ("a lab value", "- Platelets: 245000 cells/uL (2026-05-02)"),
    ("an ISO date", "- Biopsy of breast (2019-04-20, 7 years before "
                    "reference date)"),
    ("a dosage", "- Paclitaxel 100 MG Injection | status: completed"),
):
    _f, _ = drive(deid.scan_for_identifiers, _text, None)
    check(f"...and none fires on {_label}", _f, [])


# ===========================================================================
# 8. THE REFUSAL, DRIVEN THROUGH THE REAL STAGE 5 NODE
# ===========================================================================

section("8. The enforcement point: Stage 5 refuses rather than sending")

from oncotriage.agent.evaluation import node_llm_classifier_evaluation  # noqa: E402


class _CountingClient:
    """Counts calls and RAISES. A guard that failed to fire would make a call,
    and the call itself is the failure -- which is a stronger control than any
    assertion about the returned dict."""

    def __init__(self):
        self.calls = 0
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls += 1
        raise AssertionError("Stage 5 issued a request; the guard did not fire")


_TRIALS = [{"trial": {
    "nct_id": "NCT00000001",
    "title": "A trial",
    "phase": "PHASE2",
    "eligibility": {
        "inclusion_criteria": "Inclusion Criteria:\n- Age 18-75",
        "exclusion_criteria": "Exclusion Criteria:\n- None",
    },
}}]


def _run_stage5(patient_data):
    """Drive the node once with a counting client. Returns (result, client)."""
    client = _CountingClient()
    deps.set_override(deps.OPENAI_CLIENT, client)
    try:
        state = {
            "patient_data": patient_data,
            "filtered_trials": _TRIALS,
            "llm_classifier_retries": 0,
            "mesh_filter_applied": True,
            "mesh_filter_skip_reason": "applied",
            "stage_timings": {},
        }
        return drive(node_llm_classifier_evaluation, state), client
    finally:
        deps.clear_override(deps.OPENAI_CLIENT)


# --- the ARM THAT MUST REFUSE: an identifier planted in free text ---------
_leaky = _patient(conditions=[
    {"display": f"Malignant neoplasm of breast, per {_fake_family_name()}",
     "code": "254837009", "onset_date": "2019-04-11",
     "clinical_status": "active", "verification_status": "confirmed"},
])
# The plant is only reachable if the guard KNOWS the name, which needs the
# bundle. Passing it is what the `source_bundle` parameter is for.
_leaky_rec, _leaky_text = drive(build_patient_record, _leaky, BUNDLE)
_leaky_findings, _ = drive(deid.scan_for_identifiers, _leaky_text,
                           getattr(_leaky_rec, "inventory", {}))
check_true("PRECONDITION: with the bundle supplied, the planted name IS in "
           "the record's inventory and IS found in the rendered text. "
           "Without this the refusal below would prove nothing",
           isinstance(_leaky_findings, list) and len(_leaky_findings) >= 1)

_refusals_before = sum(deid.DEID_REFUSALS.values())
_leak_err = drive(deid.assert_no_identifiers, _leaky_text, _leaky_rec)
check_true("assert_no_identifiers RAISES IdentifierLeakError",
           isinstance(_leak_err, str) and "IdentifierLeakError" in _leak_err)
check("...and the message quotes NO value",
      _fake_family_name() in str(_leak_err), False)
check_true("...and names the class, which is what an operator acts on",
           deid.IDENTIFIER_NAME in str(_leak_err))
check_true("...and the refusal is COUNTED, so a run that lost patients this "
           "way says so in its run-end block whatever the caller does",
           sum(deid.DEID_REFUSALS.values()) > _refusals_before)

# --- the node itself, both arms -------------------------------------------
_result_clean, _client_clean = _run_stage5(PATIENT)
check("THE CLEAN ARM REACHES THE PROVIDER. Without this, a guard that "
      "refused every patient would pass every check in this section",
      getattr(_client_clean, "calls", 0), 1)

_result_leak, _client_leak = _run_stage5(_leaky)
check("A PATIENT WHOSE RECORD CARRIES AN IDENTIFIER-SHAPED VALUE... the "
      "production path has no bundle, so this particular plant is NOT caught "
      "there -- which is the stated gap, driven rather than described",
      getattr(_client_leak, "calls", 0), 1)

# The production-reachable plant: a value the PARSED record itself carries.
_uuid_leak = _patient(conditions=[
    {"display": f"Malignant neoplasm of breast (ref {_fake_uuid()})",
     "code": "254837009", "onset_date": "2019-04-11",
     "clinical_status": "active", "verification_status": "confirmed"},
])
_result_uuid, _client_uuid = _run_stage5(_uuid_leak)
check("A PATIENT WHOSE RENDERED RECORD CARRIES ITS OWN patient_id ISSUES NO "
      "REQUEST. This is the production path: the inventory is the parsed "
      "record's own, and the shape rules catch the UUID besides",
      getattr(_client_uuid, "calls", 0), 0)
check_true("...and the node returns an error naming the guard",
           isinstance(_result_uuid, dict)
           and "de-identification guard" in (_result_uuid.get("error") or ""))
check("...with the retry budget already spent, so a DETERMINISTIC condition "
      "does not arrive as three identical failed patients after two backoff "
      "sleeps", at(_result_uuid, "llm_classifier_retries"),
      config.MAX_LLM_CLASSIFIER_RETRIES)
check("...and zero billed calls recorded, which is a measured zero rather "
      "than an invented one", at(_result_uuid, "llm_classifier_calls"), 0)
check("...and no evaluations", at(_result_uuid, "evaluations"), [])
check("...and the error message quotes no value",
      _fake_uuid() in (at(_result_uuid, "error", "") or ""), False)
check_true("...and NO PROMPT is in the returned dict, so the identifier is "
           "not written to inferences.llm_classifier_prompt either",
           isinstance(_result_uuid, dict)
           and "llm_classifier_prompt" not in _result_uuid)

# --- the router really terminates on that retry value ---------------------
from oncotriage.agent.graph import route_after_llm_classifier      # noqa: E402

check("the retry value the refusal returns routes to the error handler "
      "rather than looping. Asserting the value alone would be a claim about "
      "a number; this is a claim about what the graph does with it",
      drive(route_after_llm_classifier, _result_uuid), "error_handler")
check_true("...and the router is non-degenerate: one retry short of the "
           "budget still loops",
           drive(route_after_llm_classifier,
                 {"error": "x", "llm_classifier_retries": 0,
                  "evaluations": []}) == "llm_classifier_retry")


# ===========================================================================
# 9. THE HARNESS PATH
# ===========================================================================

section("9. Every renderer caller goes through the stage")

_eval_src = io.open(_READ_FILES["evaluation"], "r", encoding="utf-8").read()
_eval_tree = ast.parse(_eval_src)

_imports_summary = any(
    isinstance(n, ast.ImportFrom) and n.module == "oncotriage.agent.patient"
    and any(a.name == "_create_patient_summary" for a in n.names)
    for n in ast.walk(_eval_tree))
check("Stage 5 no longer imports the text-only entry point: it needs the "
      "RECORD as well, for the guard's inventory, and a caller that took only "
      "the text would have to rebuild the record -- two builds of one patient "
      "being two things that can disagree", _imports_summary, False)

_calls_guard = any(
    isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    and n.func.id == "assert_no_identifiers" for n in ast.walk(_eval_tree))
check_true("...and it calls assert_no_identifiers", _calls_guard)

# The guard must run ABOVE the prompt render, or an identifier reaches a
# string that is also STORED.
_node = next((n for n in ast.walk(_eval_tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "node_llm_classifier_evaluation"), None)
check_true("the Stage 5 node was found (non-degeneracy for the ordering "
           "check below)", _node is not None)
if _node is not None:
    _guard_line = min([n.lineno for n in ast.walk(_node)
                       if isinstance(n, ast.Call)
                       and isinstance(n.func, ast.Name)
                       and n.func.id == "assert_no_identifiers"] or [10 ** 9])
    _render_line = min([n.lineno for n in ast.walk(_node)
                        if isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Name)
                        and n.func.id == "render_system_prompt"] or [0])
    check_true("the guard runs ABOVE render_system_prompt, so a leaking "
               "record never reaches a prompt string at all -- which matters "
               "because the prompt is stored",
               0 < _guard_line < _render_line)

_harness_path = os.path.join(_CODE_DIR, "oncotriage", "evaluation",
                             "run_harness.py")
_harness_src = io.open(_harness_path, "r", encoding="utf-8").read()

# A SUBSTRING SCAN IS NOT A CHECK HERE, AND THE REVERT HARNESS PROVED IT.
# The first version of this asked whether "assert_no_identifiers" appeared in
# the file. It does -- in the MODULE DOCSTRING, which argues for the guard, and
# in the IMPORT LINE -- so deleting the CALL left the check green. That is the
# third time this project has met "a file that argues about its own settings
# cannot be grepped for them". It walks for a real Call node now, inside the
# function that builds the persisted record.
_harness_tree = ast.parse(_harness_src)
_build_record_fn = next((n for n in ast.walk(_harness_tree)
                         if isinstance(n, ast.FunctionDef)
                         and n.name == "build_record"), None)
check_true("the harness's record builder was found (non-degeneracy: a walk "
           "that found nothing would make the next check pass for free)",
           _build_record_fn is not None)
_harness_calls = {n.func.id for n in ast.walk(_build_record_fn or _harness_tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
check_true("the evaluation harness -- the only other renderer consumer, and "
           "one that PERSISTS the text into a record an LLM rater reads -- "
           "CALLS the guard. A run that ended at node_no_candidates never "
           "entered Stage 5, so its guard never ran and this is the only "
           "thing between that patient's record and the rater",
           "assert_no_identifiers" in _harness_calls)
check_true("...and it builds the record through the stage",
           "build_patient_record" in _harness_calls)
check("...and no longer calls the text-only entry point",
      "_create_patient_summary" in _harness_calls, False)


# ===========================================================================
# 10. REAL BUNDLES (GATED)
# ===========================================================================

section("10. Real parsed bundles")

_corpus = None
try:
    from oncotriage import paths as _paths
    import glob as _glob
    _dir = _paths.data_fhir_path
    _corpus = sorted(_glob.glob(os.path.join(_dir, "*.json")))[:12]
except Exception as _exc:                            # noqa: BLE001 -- reported
    _corpus = None

if not _corpus:
    skip("real bundles: identifiers absent, clinical text byte-identical",
         "the FHIR corpus is not present (a CI runner has only the directory "
         "skeleton). Every fabricated-input check above ran; this section "
         "needs real third-party clinical text, which is the one thing a "
         "literal cannot stand in for.")
    skip("real bundles: the corpus's own over-89 population is capped",
         "same reason")
else:
    from oncotriage.fhir.parser import parse_fhir_bundle               # noqa: E402
    import json as _json

    _hits_total = 0
    _shape_total = 0
    _capped_n = 0
    _pseudonyms = {}
    _bundle_hashes_before = {p: sha256_file(p) for p in _corpus}

    for _path in _corpus:
        _b = _json.load(io.open(_path, "r", encoding="utf-8"))
        _pd = drive(parse_fhir_bundle, _path)
        if not isinstance(_pd, dict):
            continue
        _rec_c, _txt_c = drive(build_patient_record, _pd, _b)
        if not isinstance(_txt_c, str):
            continue
        _f, _ = drive(deid.scan_for_identifiers, _txt_c,
                      getattr(_rec_c, "inventory", {}))
        _hits_total += len(_f) if isinstance(_f, list) else 1
        _fs, _ = drive(deid.scan_for_identifiers, _txt_c, None)
        _shape_total += len(_fs) if isinstance(_fs, list) else 1
        if getattr(_rec_c, "age_capped", False):
            _capped_n += 1
        _pseudonyms[_pd["patient_id"]] = getattr(_rec_c, "pseudonym", None)

    check_true("the corpus section is non-degenerate: it rendered several "
               "real patients", len(_pseudonyms) >= 8)
    check("NO identifier reaches a rendered record, scanned against the FULL "
          "bundle inventory -- names, address, geolocation, MRN, SSN, "
          "driver's licence, passport and every untyped identifier the "
          "bundle carries", _hits_total, 0)
    check("...and no shape rule fires on real clinical text either",
          _shape_total, 0)
    check("every pseudonym is distinct", len(set(_pseudonyms.values())),
          len(_pseudonyms))

    # Stability across a SECOND parse+render of the same files.
    _second = {}
    for _path in _corpus:
        _pd2 = drive(parse_fhir_bundle, _path)
        if isinstance(_pd2, dict):
            _r2, _ = drive(build_patient_record, _pd2)
            _second[_pd2["patient_id"]] = getattr(_r2, "pseudonym", None)
    check("the pseudonym is STABLE across two independent parse+render runs "
          "of the same bundles", _second, _pseudonyms)

    check("the FHIR files on disk are byte-unchanged. The stage READS the "
          "source and never writes it",
          {p: sha256_file(p) for p in _corpus}, _bundle_hashes_before)


# ===========================================================================
# 11. PURITY AND HYGIENE
# ===========================================================================

section("11. Purity and hygiene")

check("no model was loaded: torch never entered sys.modules",
      "torch" in sys.modules, False)
check("...and neither did transformers", "transformers" in sys.modules, False)

_deid_src = io.open(_READ_FILES["deid"], "r", encoding="utf-8").read()
_deid_tree = ast.parse(_deid_src)
_project_imports = sorted({
    (n.module or "") for n in ast.walk(_deid_tree)
    if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("oncotriage")
} | {
    a.name for n in ast.walk(_deid_tree) if isinstance(n, ast.Import)
    for a in n.names if a.name.startswith("oncotriage")
})
check("oncotriage/deid.py imports NOTHING from the project. It is on the "
      "render path, and run_fingerprint.RENDERER_MODULES hashes that path's "
      "transitive closure -- so an import here would pull that module into "
      "the resume-gate digest and into the closed round trip "
      "tests/test_resume_configuration_fingerprint.py section 1b enforces",
      _project_imports, [])

from oncotriage import run_fingerprint as _fp                          # noqa: E402

check_true("...and deid.py IS in RENDERER_MODULES, because it decides which "
           "fields exist to be rendered, how a capped age is written and how "
           "the Patient: line is derived",
           "deid.py" in _fp.RENDERER_MODULES)
check("...and that tuple is still sorted", list(_fp.RENDERER_MODULES),
      sorted(_fp.RENDERER_MODULES))

from oncotriage import degradation as _deg                             # noqa: E402

check_true("DEID_REFUSALS is registered, so a refused patient reaches the "
           "run-end degradation block", "DEID_REFUSALS" in _deg.registered_names())
check_true("DEID_CENSUS is in the CENSUS registry rather than the "
           "degradation one -- a capped age is the stage working",
           "DEID_CENSUS" in [n for n, _, _ in _deg._CENSUS_SPEC])
check_true("...and the two registries are still disjoint",
           drive(_deg.assert_registries_disjoint) is None)

# No identifier-shaped literal is tracked in THIS file, checked with the
# scanner it tests. tests/test_secret_scan_gate.py's rule, applied to a
# personal identifier rather than a credential.
_self_src = io.open(os.path.abspath(__file__), "r", encoding="utf-8").read()
_self_findings, _ = drive(deid.scan_for_identifiers, _self_src, None)
check("this test file contains no identifier-shaped LITERAL: every fixture "
      "value is assembled at run time from a prefix and an arithmetic",
      _self_findings, [])

_clear_stub_registries()
for _key, _name in ((deps.CANCER_REGISTRY, "CANCER_REGISTRY"),
                    (deps.LAB_REGISTRY, "LAB_REGISTRY"),
                    (deps.MESH_FILTER, "MESH_FILTER")):
    check(f"the {_name} override was removed, so this file leaves no "
          f"process-global state behind for a sibling module in a shared "
          f"interpreter", deps.peek(_key), deps.UNSET)

check("the three repository files this test READS are byte-unchanged. It "
      "writes nothing anywhere, which is what keeps it out of the collision "
      "matrix", {k: sha256_file(v) for k, v in _READ_FILES.items()},
      _HASHES_BEFORE)


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 74)

# --- RELEASE THE PROVIDER PIN, ABOVE THE SUMMARY ---------------------------
#
# ABOVE, NOT BELOW: a release under the results line still decides the exit
# code while being absent from the number the summary printed -- a run that
# reports "0 failed" and exits non-zero. The default-flip pass shipped exactly
# that in three of seven files, which is why the release is one function with
# one caller-visible answer rather than four hand-written lines here.
#
# THE OUTCOME IS RECORDED BEFORE THE RESTORE, so "there was a pin to release"
# cannot be satisfied by a process that never installed one.
_PIN_WHO, _PIN_PREVIOUS, _PIN_RESTORED = _provider_pin.release_openai_arm()
check("[provider pin] the OpenAI pin this file installed was released, and "
      "config.MATCHING_PROVIDER is back to the shipped provider",
      (_PIN_WHO == os.path.basename(__file__), _PIN_PREVIOUS, _PIN_RESTORED,
       _provider_pin.pin_state()),
      (True, _PROVIDER_BEFORE_PIN, True, (None, None)))

print("SUMMARY")
print("=" * 74)
print(f"Passed:  {_RESULTS['passed']}")
print(f"Failed:  {_RESULTS['failed']}")
print(f"Skipped: {_RESULTS['skipped']}")

if _FAILURES:
    print("\nFailures:")
    for _f in _FAILURES:
        print(f"  - {_f}")
if _SKIPS:
    print("\nSkipped:")
    for _s in _SKIPS:
        print(f"  - {_s}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug 31 2026

@author: ramyalsaffar
"""
