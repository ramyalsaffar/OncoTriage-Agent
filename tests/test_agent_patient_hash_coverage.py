# Patient Hash Coverage Test
############################

"""
``compute_patient_hash`` PROMISED SOMETHING IT DID NOT DELIVER.

Its docstring said two inferences carrying the same hash are guaranteed to have
had identical input data. Three parsed fields were absent from it, and each
reaches the output by a different route:

  - ``cancer_genomic_variants`` -- File 07 routes mCODE variants OUT of
    ``observations`` entirely, so a patient's biomarkers were invisible to the
    hash while driving both the retrieval query and a named section of the
    Stage 5 prompt. Two patients differing only in EGFR status hashed
    identically.
  - ``allergies`` -- rendered under their own heading in the Stage 5 prompt.
  - ``cancer_stage_observations`` -- Tier 0 of ``extract_patient_stage``, whose
    ordinal drives Stage 4's stage filter.

``cancer_metastasis_observations`` was named in the same brief and was ALREADY
hashed (the AJCC M-category pass added it); the docstring simply never listed
it. Section 1 covers it anyway, as a regression guard for an entry nothing else
tests.

WHAT THIS FILE HOLDS
--------------------
    1. EVERY ROUTED-OUT FIELD MOVES THE HASH. All five -- the three added, plus
       metastasis and ECOG -- with the pre-change behaviour shown for the three
       so the controls are not vacuous.
    2. ABSENCE CONTRIBUTES NOTHING. Missing key, ``None`` and ``[]`` all hash
       alike, and a literal patient carrying none of the five hashes to a
       PINNED value -- so making any entry unconditional fails here rather than
       silently invalidating every stored hash.
    3. THE SUB-FIELD CHOICE IS EXECUTABLE, NOT PROSE. For each field, every
       sub-field the code claims to hash is shown to move the hash, and every
       sub-field it claims to exclude is shown NOT to. That is the docstring's
       "say which you included and why" made into a check.
    4. ORDER INDEPENDENCE. Shuffled collections hash alike; a real bundle
       parsed twice hashes alike.
    5. THE SOURCE IS HASHED, NOT THE DERIVATION. Two patients whose stage
       observations differ but whose extracted stage ORDINAL is identical must
       hash differently -- which is only true if the observations are hashed
       rather than the ordinal computed from them.
    6. THE DOCSTRING MATCHES THE CODE. Every field the function hashes is named
       in the docstring and vice versa, with a control.
    7. THE CORPUS PROPERTY. Over real bundles: stripping the three new fields
       changes a patient's hash EXACTLY when that patient carried one.

NO NETWORK, NO KEYS, NO SPEND, NO GIT HISTORY. Every fixture is a literal dict
except section 7, which parses real bundles read-only. No graph is compiled and
no model is called. Nothing anywhere is written -- not a temp file -- and the
one package file this reads (``oncotriage/agent/patient.py``, for section 6) is
written by neither of the suite's two writers, so this file is NOT in the
collision matrix.

IT EXECS NOTHING and needs no ``_EXEC_ALLOWLIST`` entry: every control is a
different INPUT to the shipped function, which is the natural control for a pure
function of its argument.

Run from terminal:
    python tests/test_agent_patient_hash_coverage.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# The package bootstrap every file in tests/ carries; the candidate directory
# is the PARENT of this file's. `pip install -e .` makes it a no-op.
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
import copy
import glob
import inspect
import json
import random

from oncotriage.agent import patient as _patient_mod
from oncotriage.constants import (
    ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS,
    ECOG_SELECTION_MOST_RECENT,
)
from oncotriage.agent.patient import compute_patient_hash as H
from oncotriage.extraction.stage import extract_patient_stage
from oncotriage.fhir.parser import parse_fhir_bundle
from oncotriage.fixtures import replay as _replay_mod
from oncotriage import paths as _PATHS


#------------------------------------------------------------------------------


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
        _FAILURES.append(f"{label}\n          expected: {expected}\n"
                         f"          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def guarded(fn):
    """Call fn; on ANY exception return a marker rather than aborting the run.

    Sections 3 and 5 feed the function records with None-valued sub-fields on
    purpose -- a sort key that compares None with str raises TypeError, which is
    one of the defects this file exists to catch. A bare call inside a check()
    argument would let that escape while the argument was being evaluated and
    the run would report one traceback where it owed a summary.
    """
    try:
        return fn()
    except BaseException as exc:                       # noqa: BLE001
        return f"RAISED {type(exc).__name__}: {exc}"


#------------------------------------------------------------------------------


# ===========================================================================
# FIXTURES -- literal dicts, shaped like what File 07 actually emits
# ===========================================================================

# Sub-field names and values are taken from real parsed records: an allergy and
# a stage observation from the Synthea corpus, and the genomic variant from the
# mcode_genomic_variant fixture. A test built on invented shapes would pass
# while the real ones went unhashed.

BASE = {
    "patient_id": "hash-test-patient",
    "demographics": {"birth_date": "1955-03-14", "sex": "female",
                     "race": "White", "ethnicity": "Not Hispanic or Latino"},
    "conditions": [{"display": "Malignant neoplasm of breast (disorder)",
                    "onset_date": "2019-04-02", "clinical_status": "active"}],
    "medications": [{"display": "Paclitaxel 100 MG Injection"}],
    "observations": [{"display": "Hemoglobin", "value": "13.1", "unit": "g/dL",
                      "date": "2020-01-05"}],
    "procedures": [{"display": "Lumpectomy", "date": "2019-05-01"}],
}

ALLERGY = {"code": "609328004", "display": "Allergic disposition (finding)",
           "category": "environment", "criticality": "low",
           "clinical_status": "active", "verification_status": "confirmed",
           "onset_date": "1930-02-25T08:58:00-08:00"}

VARIANT = {"code": "69548-6", "display": "EGFR p.Leu858Arg: Present",
           "gene_symbol": "EGFR", "result_value": "Present",
           "interpretation": None, "genomic_source": None, "hgvs_cdna": None,
           "hgvs_protein": "p.Leu858Arg", "value": "Present",
           "date": "1932-06-01T10:11:06-08:00"}

STAGE_OBS = {"stage_display": "American Joint Committee on Cancer stage IA "
                              "(qualifier value)",
             "stage_code": "1222724007",
             "date": "2000-08-20T20:46:16-07:00", "loinc": "21908-9"}

METASTASIS = {"code": "21907-1", "display": "Distant metastases.clinical [Class]",
              "value": "cM1", "unit": None,
              "date": "2001-02-02T00:00:00-08:00", "metastasis_category": "M"}

# `selection` carries the REAL spelling, imported rather than retyped. The
# literal here used to read "most_recent_on_or_before_reference" -- no trailing
# `_date` -- which the parser has never written; harmless in this file, because
# every check on it moves the hash by CHANGING the string rather than by
# matching it, and instructive because the same truncation in
# oncotriage/dashboard/tabs/performance.py was NOT harmless and shipped.
ECOG = {"value": 1, "date": "2020-02-02", "observations_found": 1,
        "selection": ECOG_SELECTION_MOST_RECENT,
        "value_shape": "valueInteger",
        # The anchor the pre-diagnosis refusal was measured against. Recorded on
        # every patient beside `reference_date`, and deliberately NOT hashed --
        # see check 3d-i.
        "primary_diagnosis_date": "2019-05-26T13:15:53-07:00"}


def with_(**fields):
    """BASE plus the named fields."""
    out = copy.deepcopy(BASE)
    out.update(copy.deepcopy(fields))
    return out


def edit(record, **changes):
    """A copy of `record` with sub-fields replaced."""
    out = copy.deepcopy(record)
    out.update(changes)
    return out


#------------------------------------------------------------------------------


# ===========================================================================
# 1. EVERY ROUTED-OUT FIELD MOVES THE HASH
# ===========================================================================

print("\n" + "=" * 70)
print("1. each field File 07 routes out of `observations` reaches the hash")
print("=" * 70)

_BARE = H(BASE)

# NON-DEGENERACY FIRST: the function must be discriminating at all. If it
# returned a constant, every "differs" below would fail and every "identical"
# would pass -- so both directions are established before anything else.
check("1a  the hash is a 16-char hex digest", (len(_BARE), int(_BARE, 16) >= 0),
      (16, True))
check("1a  ...and it discriminates on a field it always covered "
      "(non-degeneracy)",
      H(BASE) == H(with_(demographics={**BASE["demographics"], "sex": "male"})),
      False)

check("1b  allergies move the hash",
      _BARE == H(with_(allergies=[ALLERGY])), False)
check("1c  genomic variants move the hash",
      _BARE == H(with_(cancer_genomic_variants=[VARIANT])), False)
check("1d  stage observations move the hash",
      _BARE == H(with_(cancer_stage_observations=[STAGE_OBS])), False)

# Already present before this pass. Covered because nothing else tests them and
# a later edit could drop either without any other check noticing.
check("1e  metastasis observations move the hash (regression guard)",
      _BARE == H(with_(cancer_metastasis_observations=[METASTASIS])), False)
check("1f  ECOG moves the hash (regression guard)",
      _BARE == H(with_(ecog_performance_status=ECOG)), False)

# THE CONTROLS THE BRIEF ASKS FOR, stated as the defect rather than as its
# absence: two patients differing ONLY in the named field. These are the
# comparisons that returned True before this pass and must return False now.
_A1 = with_(allergies=[ALLERGY])
_A2 = with_(allergies=[edit(ALLERGY, display="Penicillin G",
                            category="medication", criticality="high")])
check("1g  two patients differing ONLY in allergies hash differently",
      H(_A1) == H(_A2), False)

_V1 = with_(cancer_genomic_variants=[VARIANT])
_V2 = with_(cancer_genomic_variants=[edit(VARIANT, display="BRAF p.Val600Glu: Present",
                                          gene_symbol="BRAF",
                                          hgvs_protein="p.Val600Glu")])
check("1h  two patients differing ONLY in genomic variants hash differently",
      H(_V1) == H(_V2), False)

_S1 = with_(cancer_stage_observations=[STAGE_OBS])
_S2 = with_(cancer_stage_observations=[
    edit(STAGE_OBS, stage_display="American Joint Committee on Cancer stage IV "
                                  "(qualifier value)", stage_code="2640006")])
check("1i  two patients differing ONLY in stage observations hash differently",
      H(_S1) == H(_S2), False)


#------------------------------------------------------------------------------


# ===========================================================================
# 2. ABSENCE CONTRIBUTES NOTHING
# ===========================================================================

print("\n" + "=" * 70)
print("2. a patient carrying none of the five hashes as if they did not exist")
print("=" * 70)

# THE ECOG RULE, APPLIED TO ALL FIVE. Each entry is emitted only when the field
# is non-empty, so adding it does not move the hash for a patient who never had
# that data -- which is what keeps hashes already logged against this corpus
# comparable. The three shapes an absent field arrives in are all driven,
# because `.get(k) or []` and `.get(k, [])` differ on a key present-but-None.
for _label, _empty in (("missing key", {}),
                       ("explicit None", {"allergies": None,
                                          "cancer_genomic_variants": None,
                                          "cancer_stage_observations": None,
                                          "cancer_metastasis_observations": None,
                                          "ecog_performance_status": None}),
                       ("empty list", {"allergies": [],
                                       "cancer_genomic_variants": [],
                                       "cancer_stage_observations": [],
                                       "cancer_metastasis_observations": [],
                                       "ecog_performance_status": {}})):
    check(f"2a  {_label}: hashes the same as a patient without the fields",
          guarded(lambda e=_empty: H(with_(**e))), _BARE)

# THE PIN. A literal patient carrying none of the five, hashed to a value fixed
# here. This is what catches an entry made unconditional: such a change moves
# this hash while every relational check above still passes, and it would
# silently invalidate every hash already stored against a corpus without the
# field. Computed from BASE above; if BASE is ever edited this must be
# regenerated ON PURPOSE.
check("2b  the pinned hash of a patient with none of the five",
      _BARE, "697e68d596099ce5")


#------------------------------------------------------------------------------


# ===========================================================================
# 3. THE SUB-FIELD CHOICE, MADE EXECUTABLE
# ===========================================================================

print("\n" + "=" * 70)
print("3. every included sub-field moves the hash; every excluded one does not")
print("=" * 70)

# WHY BOTH HALVES. "Included" alone would be satisfied by hashing the whole
# record, which is what the value_shape argument forbids: a sub-field nothing
# reads must not move the hash, or a re-encoding that changes no prompt and no
# filter reports as an input change and the ablation study misreads it. So each
# field is checked in both directions, and the EXCLUDED half is the one that can
# only be got right deliberately.

def moved(base_field, record, **change):
    """Does changing a sub-field move the hash?"""
    a = with_(**{base_field: [record]})
    b = with_(**{base_field: [edit(record, **change)]})
    return guarded(lambda: H(a) != H(b))


# --- allergies: display, category, criticality IN; code, onset_date,
#     clinical_status, verification_status OUT ------------------------------
for _sub, _val in (("display", "Penicillin G"), ("category", "medication"),
                   ("criticality", "high")):
    check(f"3a  allergies.{_sub} is hashed",
          moved("allergies", ALLERGY, **{_sub: _val}), True)
for _sub, _val in (("code", "999999"), ("onset_date", "1999-09-09T00:00:00Z"),
                   ("clinical_status", "inactive"),
                   ("verification_status", "unconfirmed")):
    check(f"3a  allergies.{_sub} is NOT hashed (nothing downstream reads it)",
          moved("allergies", ALLERGY, **{_sub: _val}), False)

# --- variants: seven in; code, genomic_source, value out ------------------
for _sub, _val in (("display", "BRAF p.Val600Glu: Present"),
                   ("gene_symbol", "BRAF"), ("hgvs_protein", "p.Val600Glu"),
                   ("hgvs_cdna", "c.1799T>A"), ("result_value", "Absent"),
                   ("interpretation", "Negative"),
                   ("date", "1999-01-01T00:00:00-08:00")):
    check(f"3b  cancer_genomic_variants.{_sub} is hashed",
          moved("cancer_genomic_variants", VARIANT, **{_sub: _val}), True)
for _sub, _val in (("code", "999999"), ("genomic_source", "somatic"),
                   ("value", "Absent")):
    check(f"3b  cancer_genomic_variants.{_sub} is NOT hashed",
          moved("cancer_genomic_variants", VARIANT, **{_sub: _val}), False)

# --- stage observations: stage_display, date, loinc in; stage_code out -----
for _sub, _val in (("stage_display", "AJCC stage IV (qualifier value)"),
                   ("date", "2011-11-11T00:00:00-08:00"), ("loinc", "21902-2")):
    check(f"3c  cancer_stage_observations.{_sub} is hashed",
          moved("cancer_stage_observations", STAGE_OBS, **{_sub: _val}), True)
check("3c  cancer_stage_observations.stage_code is NOT hashed -- a second "
      "encoding of stage_display that no consumer reads",
      moved("cancer_stage_observations", STAGE_OBS, stage_code="2640006"), False)

# The standing exclusion this file inherits rather than introduces. Recorded
# here because it is the precedent every exclusion above cites.
# ADDED BY THE ECOG PRE-DIAGNOSIS PASS, on this file's own rule that every
# sub-field is shown to move the hash or shown not to. The anchor is a fact
# about WHY a value was published or refused, not a fact about the patient: two
# runs whose ECOG value, date, count and selection agree rendered the same
# prompt text, so hashing the anchor would move a hash the prompt cannot see --
# `value_shape`'s argument, one field over. It IS reachable: a patient whose
# diagnosis is re-dated and whose ECOG is refused as a result changes
# `selection`, which IS hashed, so the outcome is never invisible.
check("3d-i  ecog.primary_diagnosis_date is NOT hashed -- the anchor explains a "
      "refusal; the refusal itself rides in `selection`, which is hashed",
      guarded(lambda: H(with_(ecog_performance_status=ECOG))
              == H(with_(ecog_performance_status=edit(
                  ECOG, primary_diagnosis_date="1999-01-01T00:00:00-08:00")))),
      True)
check("3d-ii  non-degeneracy: `selection` on the same dict IS hashed, so 3d-i "
      "is not passing because the whole ECOG block stopped contributing",
      guarded(lambda: H(with_(ecog_performance_status=ECOG))
              == H(with_(ecog_performance_status=edit(
                  ECOG, selection=ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS)))),
      False)

check("3d  ecog.value_shape is still NOT hashed -- normalising a corpus from "
      "valueQuantity to valueInteger must not move a hash whose prompt text is "
      "identical",
      guarded(lambda: H(with_(ecog_performance_status=ECOG))
              == H(with_(ecog_performance_status=edit(ECOG,
                                                      value_shape="valueQuantity")))),
      True)

# A None-valued sub-field must not raise. The parser really does emit
# interpretation=None and unit=None, and a sort key comparing None with str is
# a TypeError -- the shape that took down an earlier version of the metastasis
# sort. Driven for every one of the three new collections.
check("3e  a record whose sort sub-fields are all None does not raise",
      guarded(lambda: isinstance(H(with_(
          allergies=[{"display": None, "category": None, "criticality": None}],
          cancer_genomic_variants=[{"display": None, "gene_symbol": None,
                                    "date": None}],
          cancer_stage_observations=[{"stage_display": None, "date": None,
                                      "loinc": None}])), str)),
      True)
check("3e  ...and neither does a mixed list of None and str sort keys, which "
      "is what actually raises",
      guarded(lambda: isinstance(H(with_(
          allergies=[{"display": None}, {"display": "Penicillin G"}],
          cancer_genomic_variants=[{"gene_symbol": None},
                                   {"gene_symbol": "EGFR"}],
          cancer_stage_observations=[{"stage_display": None},
                                     {"stage_display": "Stage IV"}])), str)),
      True)


#------------------------------------------------------------------------------


# ===========================================================================
# 4. ORDER INDEPENDENCE
# ===========================================================================

print("\n" + "=" * 70)
print("4. parse order does not reach the hash")
print("=" * 70)

_MANY = with_(
    allergies=[ALLERGY,
               edit(ALLERGY, display="Penicillin G", category="medication"),
               edit(ALLERGY, display="Latex", criticality="high")],
    cancer_genomic_variants=[VARIANT,
                             edit(VARIANT, gene_symbol="BRAF",
                                  display="BRAF p.Val600Glu: Present")],
    cancer_stage_observations=[STAGE_OBS,
                               edit(STAGE_OBS, stage_display="AJCC stage IIB",
                                    date="2005-01-01T00:00:00-08:00")],
    cancer_metastasis_observations=[METASTASIS,
                                    edit(METASTASIS, value="cM0",
                                         display="Distant metastases.clinical")],
)
_ORDERED = H(_MANY)

# Non-degeneracy: a patient with one entry per list would make every shuffle a
# no-op, so the fixture is checked to have something to shuffle.
check("4a  the multi-entry fixture really has collections to reorder "
      "(non-degeneracy)",
      [len(_MANY[k]) > 1 for k in ("allergies", "cancer_genomic_variants",
                                   "cancer_stage_observations",
                                   "cancer_metastasis_observations")],
      [True, True, True, True])

_rng = random.Random(20260808)
_shuffles = []
for _ in range(12):
    _copy = copy.deepcopy(_MANY)
    for _key in ("allergies", "cancer_genomic_variants",
                 "cancer_stage_observations", "cancer_metastasis_observations",
                 "conditions", "medications", "observations", "procedures"):
        _rng.shuffle(_copy[_key])
    _shuffles.append(guarded(lambda c=_copy: H(c)))
check("4b  twelve shuffles of every collection all hash identically",
      sorted(set(_shuffles)), [_ORDERED])

check("4c  hashing the same dict twice is stable", H(_MANY), _ORDERED)

# --- 4c-ii. THE TIE-BREAK, WHICH IS WHERE THE ORDER DEPENDENCE ACTUALLY WAS -
# Sorting a collection by a KEY and emitting MORE fields than the key covers
# leaves ties broken by parse order. Two observations sharing (display, date)
# and differing in `value` are exactly that case, and the pre-change function
# hashed them differently depending on which arrived first -- measured on a real
# bundle, where one such pair among 3,660 observations was enough to make the
# hash unstable under a shuffle of the FHIR `entry` array.
#
# The shuffles in 4b would NOT catch this on their own: they shuffle a fixture
# whose records happen to have distinct keys. This pair is built to collide.
_TIED_A = [{"display": "Glucose", "value": "5.1", "unit": "mmol/L",
            "date": "2020-01-01"},
           {"display": "Glucose", "value": "9.9", "unit": "mmol/L",
            "date": "2020-01-01"}]
_TIED_B = list(reversed(_TIED_A))
check("4c  two observations with an IDENTICAL (display, date) sort key and "
      "different values hash the same in either order",
      H(with_(observations=_TIED_A)), H(with_(observations=_TIED_B)))
check("4c  ...and the pair really is tied on the old sort key, and really does "
      "differ in an emitted field (non-degeneracy -- otherwise the check above "
      "is about nothing)",
      ((_TIED_A[0]["display"], _TIED_A[0]["date"])
       == (_TIED_A[1]["display"], _TIED_A[1]["date"]),
       _TIED_A[0]["value"] != _TIED_A[1]["value"]),
      (True, True))
# The same shape for the collections whose sort key was narrower than their
# emitted line: metastasis (sorted on display+date, emitting value, unit and
# category too) and variants (sorted on three of seven emitted fields).
_MET_A = [METASTASIS, edit(METASTASIS, value="cM0")]
check("4c  tied metastasis observations differing only in value hash the same "
      "in either order",
      H(with_(cancer_metastasis_observations=_MET_A)),
      H(with_(cancer_metastasis_observations=list(reversed(_MET_A)))))
_VAR_A = [VARIANT, edit(VARIANT, result_value="Absent")]
check("4c  tied genomic variants differing only in result_value hash the same "
      "in either order",
      H(with_(cancer_genomic_variants=_VAR_A)),
      H(with_(cancer_genomic_variants=list(reversed(_VAR_A)))))

# --- 4d/4e. THE SAME PROPERTY THROUGH THE REAL PARSER ---------------------
# The shuffles above reorder a dict this file built. That proves the hash sorts
# what it is given; it does not prove the pair (parser, hash) is order-stable,
# which is the property an operator actually depends on -- a bundle whose FHIR
# `entry` array arrives in a different order must produce the same hash.
# So a real bundle is parsed twice, and then parsed again with its entries
# shuffled.
_REAL = sorted(glob.glob(_PATHS.data_fhir_path + "*.json"))
if not _REAL:
    _RESULTS["failed"] += 1
    _FAILURES.append(f"4d  no FHIR bundle available, so re-parse stability was "
                     f"not tested\n          searched: "
                     f"{_PATHS.data_fhir_path}*.json")
    print(f"  FAIL  4d  no FHIR bundle at {_PATHS.data_fhir_path}*.json")
else:
    # A patient carrying at least one of the new fields, so the new entries are
    # actually exercised rather than the check passing over a bare record.
    _chosen = None
    for _cand in _REAL[:400]:
        _pd = parse_fhir_bundle(_cand)
        if any(_pd.get(k) for k in ("allergies", "cancer_genomic_variants",
                                    "cancer_stage_observations",
                                    "cancer_metastasis_observations")):
            _chosen = _cand
            break
    check("4d  a real bundle carrying at least one routed-out field was found "
          "(non-degeneracy)", _chosen is not None, True)

    if _chosen:
        _h1 = guarded(lambda: H(parse_fhir_bundle(_chosen)))
        _h2 = guarded(lambda: H(parse_fhir_bundle(_chosen)))
        check("4d  re-parsing the same bundle twice gives the same hash",
              _h1, _h2)

        # --- 4e. THE END-TO-END PROPERTY, STATED AS THE HASH'S CONTRACT ------
        #
        # The first version of this check took ONE bundle, shuffled it, and
        # required the hash to be identical. That is the right question asked
        # the wrong way, and it made the check depend on which bundle it picked:
        # `parse_fhir_bundle` is itself order-dependent for MEDICATIONS. It
        # de-duplicates by display and keeps whichever record arrived first, and
        # the corpus contains bundles carrying both "Aspirin 81 MG Oral Tablet"
        # and "aspirin 81 MG Oral Tablet" -- so a shuffle changes which
        # capitalisation survives, the parsed dict genuinely differs, and a
        # different hash is then CORRECT rather than a defect. Measured: 1 of 30
        # bundles behaves this way.
        #
        # So the property asserted here is the hash's own: EQUAL PARSED INPUT
        # GIVES AN EQUAL HASH, over every shuffle whose parse matched. The
        # shuffles whose parse differed are counted and reported rather than
        # silently dropped -- a filter that removed every sample would leave
        # this passing vacuously, which is what the non-degeneracy check below
        # exists to prevent.
        #
        # THE PARSER'S OWN ORDER DEPENDENCE IS A FINDING, NOT SOMETHING THIS
        # FILE FIXES: choosing which capitalisation is canonical changes the
        # Stage 5 prompt text, which is a decision about the prompt and not
        # about the hash.
        # THE ONE EXCLUSION, NAMED AND BOUNDED. `parse_fhir_bundle` keeps the
        # FIRST record it sees per medication display, so a shuffle changes
        # which duplicate survives -- its start_date and status differ on every
        # shuffle of every bundle. That is invisible to the hash, which reads
        # only the SET OF DISPLAYS for medications. It becomes visible when the
        # corpus carries two spellings of one drug: a bundle holding both
        # "Aspirin 81 MG Oral Tablet" and "aspirin 81 MG Oral Tablet" yields a
        # different display set depending on order, and a different hash is then
        # CORRECT -- the parsed record really did change. Measured: 1 of 30
        # bundles.
        #
        # So shuffles whose medication display set moved are excluded, counted
        # and printed. Nothing else is excluded, and the non-degeneracy check
        # below is what stops the exclusion swallowing the whole sample -- which
        # is exactly how the first two versions of this check failed, once by
        # comparing list ORDER and once by comparing sub-fields the hash never
        # reads.
        #
        # THE PARSER'S DE-DUPLICATION IS A FINDING THIS FILE DOES NOT FIX:
        # choosing which spelling is canonical changes the Stage 5 prompt text,
        # which is a decision about the prompt rather than about the hash.
        def _med_displays(parsed):
            return sorted({m.get("display", "")
                           for m in (parsed.get("medications") or [])})

        _rng2 = random.Random(4242)
        _same_parse = _same_hash = _diff_parse = 0
        _bad = []
        for _cand in _REAL[:12]:
            with open(_cand, encoding="utf-8") as _fh:
                _bundle = json.load(_fh)
            if len(_bundle.get("entry") or []) < 2:
                continue
            _p_base = parse_fhir_bundle(_cand)
            _m_base = _med_displays(_p_base)
            _h_base = H(_p_base)
            for _ in range(3):
                _b2 = copy.deepcopy(_bundle)
                _rng2.shuffle(_b2["entry"])
                _p2 = parse_fhir_bundle(_b2)
                if _med_displays(_p2) == _m_base:
                    _same_parse += 1
                    if H(_p2) == _h_base:
                        _same_hash += 1
                    else:
                        _bad.append(os.path.basename(_cand))
                else:
                    _diff_parse += 1

        check("4e  most shuffles left the medication display set alone, so "
              "there is a real sample to assert over (non-degeneracy)",
              _same_parse > 0, True)
        check("4e  ...and the excluded ones are a small minority, not the "
              "whole sample (non-degeneracy)",
              _diff_parse < _same_parse, True)
        check("4e  parsing a bundle with its entries in a DIFFERENT ORDER "
              "gives the same hash", (_same_hash, _bad[:3]), (_same_parse, []))
        print(f"       {_same_parse} shuffles hashed under an unchanged "
              f"medication display set, {_diff_parse} excluded because the "
              f"parser's de-duplication changed that set (see the comment)")


#------------------------------------------------------------------------------


# ===========================================================================
# 5. THE SOURCE IS HASHED, NOT THE DERIVATION
# ===========================================================================

print("\n" + "=" * 70)
print("5. the stage OBSERVATIONS are hashed, not the ordinal computed from them")
print("=" * 70)

# THE birth_date RULE, APPLIED TO STAGE. The ordinal is a function of these
# records AND of the extractor's tier order and regexes, both of which have
# changed twice recently. Hashing the ordinal would make every patient's hash
# move whenever the extractor was edited while their bundle had not changed.
#
# The proof is a pair whose observations differ and whose ORDINAL agrees: if the
# ordinal were hashed they would collide.
_SAME_ORDINAL_A = [edit(STAGE_OBS, stage_display="AJCC stage IV (qualifier value)")]
_SAME_ORDINAL_B = [edit(STAGE_OBS, stage_display="Stage 4 (qualifier value)",
                        date="2003-03-03T00:00:00-08:00")]

_ord_a = guarded(lambda: extract_patient_stage(
    BASE["conditions"], cancer_stage_observations=_SAME_ORDINAL_A))
_ord_b = guarded(lambda: extract_patient_stage(
    BASE["conditions"], cancer_stage_observations=_SAME_ORDINAL_B))

check("5a  the two stage observations really do yield the SAME ordinal "
      "(non-degeneracy -- without this, 5b passes for the wrong reason)",
      (_ord_a, _ord_b, _ord_a == 4), (4, 4, True))
check("5b  ...and yet the two patients hash differently, so it is the "
      "observations that are hashed and not the ordinal",
      H(with_(cancer_stage_observations=_SAME_ORDINAL_A))
      == H(with_(cancer_stage_observations=_SAME_ORDINAL_B)),
      False)


#------------------------------------------------------------------------------


# ===========================================================================
# 6. THE DOCSTRING MATCHES THE CODE
# ===========================================================================

print("\n" + "=" * 70)
print("6. the docstring names exactly the fields the function hashes")
print("=" * 70)

# THE DOCSTRING WAS THE DEFECT. It listed five inputs and made a guarantee that
# three absent fields falsified -- and it stayed wrong through the pass that
# added metastasis, which it never mentioned. A list maintained by hand beside
# code that changes is a claim, so it is checked against the code.

_SRC = inspect.getsource(_patient_mod.compute_patient_hash)
_DOC = _patient_mod.compute_patient_hash.__doc__ or ""
_TREE = ast.parse(_SRC.lstrip())

# Every patient_data key the FUNCTION BODY reads, derived rather than listed.
_read_keys = sorted({
    node.args[0].value
    for node in ast.walk(_TREE)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
    and isinstance(node.func.value, ast.Name) and node.func.value.id == "patient_data"
    and node.args and isinstance(node.args[0], ast.Constant)
    and isinstance(node.args[0].value, str)
})
check("6a  the scan found the fields the function reads (non-degeneracy)",
      len(_read_keys) >= 8, True)

_missing_from_doc = [k for k in _read_keys if k not in _DOC]
check("6b  every field the function reads is named in the docstring",
      _missing_from_doc, [])

# ...and the reverse, so the docstring cannot promise a field that was removed.
_named_but_unread = [k for k in ("allergies", "cancer_genomic_variants",
                                 "cancer_stage_observations",
                                 "cancer_metastasis_observations",
                                 "ecog_performance_status")
                     if k in _DOC and k not in _read_keys]
check("6c  the docstring names no field the function does not read",
      _named_but_unread, [])

# CONTROL: the scan must be able to report a gap. A field name removed from a
# COPY of the docstring has to show up, or 6b passes for free.
_doc_without_allergies = _DOC.replace("allergies", "XXXX")
check("6d  control: a docstring missing a field IS reported",
      [k for k in _read_keys if k not in _doc_without_allergies], ["allergies"])

# The guarantee sentence must no longer claim what it cannot deliver.
check("6e  the docstring no longer claims 'identical input data' unqualified",
      "guaranteed to have identical input data" in _DOC, False)
check("6f  ...and it states the claim it CAN support",
      "AS THE PIPELINE READS IT" in _DOC, True)


#------------------------------------------------------------------------------


# ===========================================================================
# 7. THE CORPUS PROPERTY
# ===========================================================================

print("\n" + "=" * 70)
print("7. over real bundles: stripping the three fields moves the hash EXACTLY")
print("   when the patient carried one")
print("=" * 70)

# WHY THIS IS EXPRESSED AS A STRIP RATHER THAN AS A BEFORE/AFTER. The obvious
# form -- compare against the pre-change function out of `git show` -- makes
# this file die in a tree with no .git, which three files in this suite already
# do and which is a recorded follow-up. Stripping the three fields from a real
# patient and requiring the hash to move exactly when they were non-empty is the
# same property, self-contained, and it keeps holding after this change is
# committed.

_NEW_FIELDS = ("allergies", "cancer_genomic_variants",
               "cancer_stage_observations")
_BUNDLE_LIMIT = 250

_bundles = sorted(glob.glob(_PATHS.data_fhir_path + "*.json"))[:_BUNDLE_LIMIT]

if not _bundles:
    # A silent skip is what pass 20g removed from Files 18 and 19. An absent
    # corpus means this section tested nothing, and that is a failure with the
    # path named, not a quiet pass.
    _RESULTS["failed"] += 1
    _FAILURES.append(f"7  no FHIR bundles found, so the corpus property was "
                     f"not tested\n          searched: "
                     f"{_PATHS.data_fhir_path}*.json")
    print(f"  FAIL  7  no FHIR bundles found at {_PATHS.data_fhir_path}*.json")
else:
    _carried = _moved = _bare_same = 0
    _wrong = []
    for _b in _bundles:
        _p = parse_fhir_bundle(_b)
        _stripped = {k: v for k, v in _p.items() if k not in _NEW_FIELDS}
        _has = any(_p.get(k) for k in _NEW_FIELDS)
        _differs = H(_p) != H(_stripped)
        if _has:
            _carried += 1
            _moved += _differs
        else:
            _bare_same += not _differs
        if _has != _differs:
            _wrong.append(os.path.basename(_b))

    check("7a  the sample really contains patients carrying the new fields "
          "(non-degeneracy)", _carried > 0, True)
    check("7b  ...and patients carrying none of them (non-degeneracy)",
          _bare_same > 0, True)
    check("7c  every patient carrying one of the three changes hash when it "
          "is stripped", _moved, _carried)
    check("7d  every patient carrying none of them is unaffected",
          _bare_same, len(_bundles) - _carried)
    check("7e  ...so no bundle disagrees with the rule", _wrong[:5], [])
    print(f"       {len(_bundles)} bundles: {_carried} carried at least one of "
          f"{_NEW_FIELDS}, {len(_bundles) - _carried} carried none")


#------------------------------------------------------------------------------


# ===========================================================================
# 8. THE REPLAY GATE IS NOT COUPLED TO THIS FUNCTION
# ===========================================================================

print("\n" + "=" * 70)
print("8. fixture_replay's recipe gate compares the record, not its hash")
print("=" * 70)

# WHY THIS SECTION IS IN THIS FILE. Changing what compute_patient_hash covers
# moved all twelve fixtures' recorded hashes, and oncotriage/fixtures/replay.py
# used to make a MISMATCH FATAL for the five constructed ones -- with a message
# blaming the recipe, the donor bundle or the parser, none of which had moved.
# A fixture-integrity gate must not be coupled to a function that legitimately
# changes, so it compares the rebuilt patient_data against the recorded
# patient_data instead. That is the property it always meant, it is strictly
# stronger (a 16-hex-char truncation can collide; a dict comparison cannot, and
# it names the field), and it is what lets this pass replay 12/12 without a
# billed recapture.
#
# Checked statically because driving it needs a constructed fixture and a full
# replay, which is what fixture_replay.py itself is for. The control is what
# makes it non-vacuous.

_REPLAY_PY = os.path.abspath(_replay_mod.__file__)
_REPLAY_SRC = open(_REPLAY_PY, encoding="utf-8").read()


def _fatal_on(source, flag):
    """Does `source` make report[flag] a fatal condition?"""
    return f'if not report["{flag}"]:' in source


check("8a  the gate has a record comparison at all (non-degeneracy)",
      'report["recipe_patient_ok"]' in _REPLAY_SRC, True)
check("8b  a record mismatch is fatal", _fatal_on(_REPLAY_SRC,
                                                  "recipe_patient_ok"), True)
check("8c  a HASH mismatch is NOT fatal -- it is reported as provenance",
      _fatal_on(_REPLAY_SRC, "recipe_hash_ok"), False)
check("8d  ...but the hash is still computed and reported, so drift is visible "
      "rather than silent",
      'report["recipe_hash_ok"]' in _REPLAY_SRC, True)

# CONTROL: the detector must be able to see the old shape. Without this, 8c
# passes for any source at all -- including one where the whole block was
# deleted.
_OLD_SHAPE = _REPLAY_SRC.replace('if not report["recipe_patient_ok"]:',
                                 'if not report["recipe_hash_ok"]:')
check("8e  control: the pre-change shape IS detected as fatal-on-hash",
      _fatal_on(_OLD_SHAPE, "recipe_hash_ok"), True)
check("8e  ...and the control really differs from the shipped source",
      _OLD_SHAPE != _REPLAY_SRC, True)


#------------------------------------------------------------------------------


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")

if _FAILURES:
    print("\nFailures:")
    for _f in _FAILURES:
        print(f"  - {_f}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Aug  8 2026

@author: ramyalsaffar
"""
