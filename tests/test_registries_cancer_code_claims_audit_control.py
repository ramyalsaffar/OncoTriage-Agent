# Cancer Code Registry Audit — Negative Control
###############################################

"""
Negative control for 'tests/test_registries_cancer_code_claims_audit.py'
(was File 43, controlling File 42; see tests/FILE NUMBER MAPPING.md).

A test that passes proves nothing on its own. File 42 asserts that every code
and category in File 08 matches an external authority, and the only way to know
those assertions can FAIL is to break File 08 on purpose and watch File 42
catch it. This file does that, fourteen times, and restores File 08 after
each: ten defects in the ICD-10 half and four in the SNOMED half.

WHY THIS IS A COMMITTED FILE AND NOT A ONE-OFF
----------------------------------------------
It has already earned its keep once. Three of the cases below --
_ICD10_C_SECONDARY_LO 77->78, _ICD10_C_SECONDARY_HI 79->78 and
_ICD10_D_NEOPLASM_BLOCK_MAX 49->50 -- were planted and File 42 PASSED. Its
boundary assertions had been written as `_ICD10_C_SECONDARY_LO - 1, _LO, _HI,
_HI + 1`, i.e. derived from the very constants under test, so moving a constant
moved the test with it and registry and test agreed on a wrong answer. Nothing
else would have found that: the audit looked thorough, ran green, and was 30%
ineffective. File 42 now pins those blocks to ICD-10-CM literals.

That failure mode -- an assertion computed from the thing it audits -- is easy
to reintroduce and invisible from a passing run, so the control stays.

HOW IT WORKS
------------
For each case: copy File 08 aside, apply one textual defect, run File 42 as a
SUBPROCESS (so File 08 is re-read from disk rather than served from an already
imported module), record whether File 42 exited non-zero, restore File 08, and
verify the restore by sha256 before moving on.

File 08 is never left modified. The restore is verified after every single
case, and again at the end, and a failed restore aborts immediately rather than
continuing to plant defects on top of a corrupted file.

A CLEAN RUN IS CHECKED FIRST. If File 42 cannot run at all -- missing MRCONSO,
missing icd10 package, a syntax error -- it exits non-zero for every case and
every defect would look "caught" while nothing was actually being tested. The
clean run must exit 0 before any defect is planted.

RUNTIME: about 65 s warm, 95 s cold, over 15 File 42 invocations (one baseline
plus fourteen cases). It was ~125 s for ten cases before File 42 gained its
MRCONSO extract cache; a control nobody runs because it is slow protects
nothing, so the cost is kept low deliberately.

A COLD RUN IS SLOWER, AND THAT IS THE POINT. The four SNOMED cases plant codes
File 08 has never contained, so they cannot be in a cache built beforehand.
File 42 must then do a real MRCONSO lookup for each -- see rule 2 of the cache
in File 42. If a cache miss were mistaken for "not in SNOMED", those cases
would still be "caught", with the wrong verdict, and this control would certify
an audit that had stopped working. Warm and cold verdicts must be identical;
they were checked and are.

Run from terminal (or F5 in Spyder):
    python tests/test_registries_cancer_code_claims_audit_control.py
    (was: python "43- Cancer Code Registry Audit Negative Control.py")

Exit codes:
    0 -- every planted defect was caught, the module restored byte-for-byte
    1 -- a defect went uncaught, the clean run failed, an anchor was not
         found, or File 08 could not be restored
"""


# Run needed file
#----------------
# THIS FILE IMPORTS NOTHING FROM THE PROJECT, AND THAT IS DELIBERATE. It edits
# the registry module as TEXT and runs the audit in a subprocess, so importing
# the module under manipulation into THIS process would serve no purpose and
# would leave a cached, pre-plant copy of it in sys.modules for the whole run.
# It used to exec "01- Imports.py" and "02- Utility Functions.py" for their
# stdlib names alone; those are imported directly now.
#
# THE REPOSITORY ROOT IS THE PARENT OF THIS FILE'S DIRECTORY (pass 20d-2). It
# used to be this file's own directory, which was right while the file sat in
# the code directory and is one level off from tests/.
#
# IT IS NOT DERIVED FROM `oncotriage.__file__`, which is what every other moved
# test does, and the exception is the whole point of the paragraph above: that
# derivation requires importing the package, and this file's contract is that it
# imports none of it. The guard below is what replaces the import -- both files
# this control operates on are asserted to exist before anything is planted, so
# a wrong root is a named failure on line one rather than an "anchor not found"
# on all fourteen cases, which is what it would otherwise look like.
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

if "__file__" in globals():
    _code_dir = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))) + os.sep
else:
    _code_dir = os.getcwd() + os.sep
    print(f"[Bootstrap] __file__ unbound; using the working directory as the code directory: {_code_dir}")


#------------------------------------------------------------------------------


# ===========================================================================
# EXTERNAL FACTS
# ===========================================================================

# THE FILE THE DEFECTS ARE PLANTED IN.
#
# ITEM 20c, PASS 2a RETARGETED THIS. It was
# `_code_dir + "08- Cancer Code Registry.py"`. File 08 is now a re-export shim
# holding nothing but import statements, so every anchor below would have been
# missing from it and every case would have reported "anchor not found" --
# which this control does treat as a FAILURE rather than a skip, so it would
# have gone red rather than silently certifying nothing. It is retargeted at
# the module that actually holds the code, where all 14 anchors were confirmed
# to appear exactly once.
#
# The hash-and-restore contract moves with it: this file is copied aside,
# patched once per case, restored from the backup after each case, and the
# restore is verified by sha256 both per-case and at the end.
_FILE_08 = _code_dir + "oncotriage/registries/cancer_code_registry.py"
_FILE_42 = _code_dir + "tests/test_registries_cancer_code_claims_audit.py"

# BOTH PATHS ARE ASSERTED BEFORE ANYTHING IS PLANTED (pass 20d-2). This file
# derives the repository root from its own location rather than from an imported
# module -- see the bootstrap note above for why it may not import the package --
# so a wrong root has to fail HERE, loudly, naming the file it could not find.
#
# Without this, a wrong root produces exactly the failure mode this control is
# supposed to be immune to: the patch target would not exist, every anchor would
# be "not found", and all fourteen cases would report as failures that look like
# the registry was restructured. Fourteen wrong diagnoses instead of one right
# one. NOT a check(): a missing target means nothing below can run at all.
for _needed, _what in ((_FILE_08, "the registry module this control patches"),
                       (_FILE_42, "the audit this control runs as a subprocess")):
    if not os.path.isfile(_needed):
        raise AssertionError(
            f"{_what} is not where this file expects it: {_needed}. The "
            f"repository root was derived as {_code_dir!r} from this file's own "
            f"location, so either this file moved or its target did."
        )

# Where the interpreter would cache a compiled copy of the file above.
#
# THIS IS NEW IN PASS 2a AND IT IS LOAD-BEARING. Before the move, File 08 was
# exec()'d, never imported, so no .pyc of it ever existed and a planted defect
# always reached File 42. The module IS imported, so it is byte-compiled, and a
# .pyc is validated against the source's (mtime-in-SECONDS, size). Two cases
# planted inside the same clock second whose patched files happen to be the
# same length would leave the second case running the first case's bytecode --
# the control would report a pass for a defect it never executed.
#
# Rather than reason about how likely that is, the subprocess is run with
# bytecode writing OFF and any existing cache for this module removed first.
# Nothing writes a .pyc, so nothing can read a stale one.
_PYCACHE_DIR = os.path.join(os.path.dirname(_FILE_08), "__pycache__")


# ===========================================================================
# THE PLANTED DEFECTS
# ===========================================================================
# (label, exact text to find in File 08, replacement)
#
# Every anchor is matched EXACTLY ONCE. A case whose anchor is missing or
# ambiguous is a FAILURE, not a skip: it means File 08 has been restructured
# and this control is no longer testing what it claims to.
#
# The first five are the ICD-10 category assignments, the next four the block
# boundaries, the last two the comment text itself. The SNOMED sets are covered
# by their own cases at the end.

_PLANTED_DEFECTS = [
    # ---- category assignments -------------------------------------------
    ("alpha PRIMARY: C7A -> C7B (a secondary category placed in the primary set)",
     '_ICD10_ALPHA_PRIMARY: FrozenSet[str] = frozenset({"C4A", "C7A"})',
     '_ICD10_ALPHA_PRIMARY: FrozenSet[str] = frozenset({"C4A", "C7B"})'),

    ("alpha SECONDARY: C7B -> C4A (a primary category placed in the secondary set)",
     '_ICD10_ALPHA_SECONDARY: FrozenSet[str] = frozenset({"C7B"})',
     '_ICD10_ALPHA_SECONDARY: FrozenSet[str] = frozenset({"C4A"})'),

    ("alpha NON_INVASIVE: D3A -> D4A (a category that does not exist)",
     '_ICD10_ALPHA_NON_INVASIVE: FrozenSet[str] = frozenset({"D3A"})',
     '_ICD10_ALPHA_NON_INVASIVE: FrozenSet[str] = frozenset({"D4A"})'),

    ("seed PRIMARY: C97 -> C99 (seeding a category that is not real)",
     '_ICD10_SEED_PRIMARY: FrozenSet[str] = frozenset({"C97"})',
     '_ICD10_SEED_PRIMARY: FrozenSet[str] = frozenset({"C99"})'),

    # ---- block boundaries ------------------------------------------------
    # This one was always caught: the release check is independent of the
    # constant, so it does not share the tautology the next three had.
    ("boundary _ICD10_C_BLOCK_MAX 97 -> 95 (C96 falls off the end of the range)",
     "_ICD10_C_BLOCK_MAX          = 97",
     "_ICD10_C_BLOCK_MAX          = 95"),

    # THE THREE THAT EXPOSED THE TAUTOLOGY. Keep them. If File 42's boundary
    # assertions are ever rewritten in terms of the constants again, these are
    # the only things that will notice.
    ("boundary _ICD10_C_SECONDARY_LO 77 -> 78 (C77 stops classifying secondary)"
     "  [exposed the tautology]",
     "_ICD10_C_SECONDARY_LO       = 77",
     "_ICD10_C_SECONDARY_LO       = 78"),

    ("boundary _ICD10_C_SECONDARY_HI 79 -> 78 (C79 stops classifying secondary)"
     "  [exposed the tautology]",
     "_ICD10_C_SECONDARY_HI       = 79",
     "_ICD10_C_SECONDARY_HI       = 78"),

    ("boundary _ICD10_D_NEOPLASM_BLOCK_MAX 49 -> 50 (D50, chapter 3, wrongly admitted)"
     "  [exposed the tautology]",
     "_ICD10_D_NEOPLASM_BLOCK_MAX = 49",
     "_ICD10_D_NEOPLASM_BLOCK_MAX = 50"),

    # ---- the comments themselves ----------------------------------------
    # The original defect class: File 08's claim and the standard disagree.
    ("comment title: C4A 'Merkel cell carcinoma' -> 'Merkel cell lymphoma'",
     "#   C4A = Merkel cell carcinoma -> PRIMARY",
     "#   C4A = Merkel cell lymphoma -> PRIMARY"),

    ("comment set: D3A relabelled NON_INVASIVE -> PRIMARY",
     "#   D3A = Benign neuroendocrine tumors -> NON_INVASIVE",
     "#   D3A = Benign neuroendocrine tumors -> PRIMARY"),

    # ---- the SNOMED sets -------------------------------------------------
    # The ICD-10 cases above were written first because the ICD-10 audit was
    # new. The SNOMED half of File 42 is OLDER and had no committed control at
    # all -- which is backwards, because the SNOMED sets are where both
    # original defects lived (408512008 "Body mass index 40+" labelled as lung
    # cancer, and MEDCIN 315006 labelled as a metastasis).
    #
    # One case per assertion File 42 makes about a SNOMED code.
    #
    # These four also exercise the MRCONSO extract cache's second rule. Every
    # code planted here is one File 08 has never contained, so it is
    # necessarily absent from a cache built before the plant. If a cache miss
    # were treated as "not in SNOMED", cases B, C and D would all be "caught"
    # with the verdict NOT_IN_SNOMED -- the right exit code for entirely the
    # wrong reason, and the control would certify an audit that had stopped
    # working. Warm and cold verdicts are compared for exactly this reason.

    # A. Assertion 1 -- the code must exist in SNOMEDCT_US.
    #    20312006 was in _SNOMED_PRIMARY until item 18b removed it, labelled
    #    "Diffuse non-Hodgkins lymphoma". It is absent from SNOMEDCT_US
    #    entirely; SNOMED's code for that concept is 109962001.
    ("SNOMED: AML code 91861009 -> 20312006 (absent from SNOMED entirely)",
     '"91861009",        # Acute myeloid leukemia (disorder)',
     '"20312006",        # Acute myeloid leukemia (disorder)'),

    # B. Assertion 3 -- a primary-set code must name a malignancy.
    #    396275006 is Osteoarthritis: a real, current SNOMED concept that is
    #    not a neoplasm of any kind. This is the 408512008 defect exactly --
    #    a real code, a real lookup, a wrong concept.
    ("SNOMED: breast code 254837009 -> 396275006 (real code, not a malignancy)",
     '"254837009",       # Malignant neoplasm of breast (disorder)',
     '"396275006",       # Malignant neoplasm of breast (disorder)'),

    # C. Assertion 2 -- the comment must match the fully specified name.
    #    The code stays correct and only the organ in the comment changes, so
    #    nothing about the classification is wrong -- only the claim is. This
    #    is the cheapest defect to introduce and the hardest to see by reading.
    ("SNOMED: 363406005 comment renamed colon -> stomach (right code, wrong claim)",
     '"363406005",       # Malignant neoplasm of colon (disorder)',
     '"363406005",       # Malignant neoplasm of stomach (disorder)'),

    # D. Assertion 3b -- a primary-set code must NOT name a secondary concept.
    #    94381002 is a real, current, correctly-labelled SNOMED metastasis
    #    code. It simply does not belong in the PRIMARY set, and mCODE
    #    excludes descendants of 128462008 from primary selection.
    ("SNOMED: brain code 126952004 -> 94381002 (a metastasis in the PRIMARY set)",
     '"126952004",       # Neoplasm of brain (disorder)',
     '"94381002",        # Metastatic malignant neoplasm to liver (disorder)'),
]


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================
# Same shape as Files 33 and 42: record every outcome, never abort on the first
# failure, exit non-zero at the end.

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label: str, actual, expected) -> None:
    """Assert equality, record the outcome, never abort the run."""
    if actual == expected:
        _RESULTS["passed"] += 1
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected: {expected}\n          actual:   {actual}")


def fail(label: str, detail: str) -> None:
    """Record an outright failure that is not an equality comparison."""
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _clear_pycache() -> None:
    """Remove any compiled copy of the registry module.

    Called before every File 42 run. See the _PYCACHE_DIR note above: a stale
    .pyc would make a planted defect invisible to the subprocess, and a control
    that cannot deliver its defect is worse than no control.
    """
    shutil.rmtree(_PYCACHE_DIR, ignore_errors=True)


def _run_file_42():
    """Run File 42 as a subprocess. Returns (returncode, failing_labels)."""
    _clear_pycache()
    _env = dict(os.environ)
    _env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, _FILE_42],
        capture_output=True, text=True, cwd=_code_dir, env=_env,
    )
    out = proc.stdout + proc.stderr
    labels = sorted({
        line.strip()[len("FAIL "):].strip()
        for line in out.splitlines()
        if line.strip().startswith("FAIL ")
    })
    return proc.returncode, labels


#------------------------------------------------------------------------------


# ===========================================================================
# THE CONTROL
# ===========================================================================

print()
print("=" * 78)
print("NEGATIVE CONTROL — File 42 must catch every planted defect")
print("=" * 78)

_PRISTINE_SHA = _sha256(_FILE_08)
_BACKUP = os.path.join(tempfile.mkdtemp(prefix="oncotriage_negctrl_"),
                       "08_pristine.py")
shutil.copy2(_FILE_08, _BACKUP)

print(f"  target:   {os.path.relpath(_FILE_08, _code_dir)}")
print(f"  sha256:   {_PRISTINE_SHA}")
print(f"  backup:   {_BACKUP}")
print(f"  cases:    {len(_PLANTED_DEFECTS)}")
print()

_ROWS = []

try:
    # -- Clean run first. Without this, a File 42 that cannot run at all would
    #    exit non-zero for every case and every defect would read as "caught"
    #    while nothing was tested.
    print("  Baseline: running File 42 unmodified...", flush=True)
    _clean_rc, _clean_labels = _run_file_42()
    check("File 42 passes on an unmodified File 08 (baseline)", _clean_rc, 0)
    if _clean_rc != 0:
        fail("baseline is usable",
             f"File 42 exited {_clean_rc} with NO defect planted, so a non-zero "
             f"exit proves nothing about the cases below. First failures: "
             f"{_clean_labels[:3]}")
        print(f"  BASELINE FAILED (exit {_clean_rc}) — the control cannot run.")
    else:
        print("  Baseline OK (exit 0).")
        print()

        for _case_no, (_label, _old, _new) in enumerate(_PLANTED_DEFECTS, 1):
            _source = open(_BACKUP, encoding="utf-8").read()
            _hits = _source.count(_old)
            if _hits != 1:
                fail(f"anchor for [{_label}] is unique in File 08",
                     f"found {_hits} occurrences of {_old!r}; File 08 has been "
                     f"restructured and this case is no longer testing what it "
                     f"claims to")
                _ROWS.append((_label, None, ["ANCHOR NOT UNIQUE"]))
                continue

            with open(_FILE_08, "w", encoding="utf-8") as _fh:
                _fh.write(_source.replace(_old, _new))

            _rc, _labels = _run_file_42()
            _caught = _rc != 0
            check(f"caught: {_label}", _caught, True)
            _ROWS.append((_label, _caught, _labels))
            # Progress line, printed as the run goes: File 42 is invoked once
            # per case and the whole control takes over a minute, so a silent
            # run is indistinguishable from a hung one. The per-case verdict
            # detail is reported together at the end.
            print(f"  [{_case_no:2d}/{len(_PLANTED_DEFECTS)}] "
                  f"{'caught' if _caught else 'MISSED'}  {_label[:64]}", flush=True)

            # Restore and verify BEFORE the next case, so a failed restore can
            # never compound into a second planted defect.
            shutil.copy2(_BACKUP, _FILE_08)
            _restored = _sha256(_FILE_08)
            if _restored != _PRISTINE_SHA:
                fail("File 08 restored after each case",
                     f"after [{_label}] the restore produced {_restored}, "
                     f"expected {_PRISTINE_SHA}. ABORTING before planting more.")
                break

finally:
    # Restore unconditionally: an exception anywhere above must not leave a
    # planted defect in File 08.
    shutil.copy2(_BACKUP, _FILE_08)
    _FINAL_SHA = _sha256(_FILE_08)
    shutil.rmtree(os.path.dirname(_BACKUP), ignore_errors=True)


# ===========================================================================
# REPORT
# ===========================================================================

print()
print("-" * 78)
print("Per-case detail")
print("-" * 78)
for _label, _caught, _labels in _ROWS:
    print(f"  {'CAUGHT' if _caught else 'MISSED <-- BUG'}  {_label}")
    for _l in _labels[:4]:
        print(f"            -> {_l}")
    if len(_labels) > 4:
        print(f"            -> ... and {len(_labels) - 4} more failing assertions")

check("File 08 is byte-identical to how it started", _FINAL_SHA, _PRISTINE_SHA)

print()
print("=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"Cases planted: {len(_ROWS)}")
print(f"Caught:        {sum(1 for _, c, _ in _ROWS if c)}")
print(f"Missed:        {sum(1 for _, c, _ in _ROWS if c is False)}")
print(f"File 08 sha256 before: {_PRISTINE_SHA}")
print(f"File 08 sha256 after:  {_FINAL_SHA}")
print(f"Restored byte-identical: {_FINAL_SHA == _PRISTINE_SHA}")
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
Created on Mon Aug  3 23:10:00 2026

@author: ramyalsaffar
"""
