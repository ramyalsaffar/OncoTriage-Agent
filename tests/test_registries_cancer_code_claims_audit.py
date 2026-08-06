# Cancer Code Registry Audit Test — every SNOMED code checked against UMLS
###########################################################################

"""
Audits _SNOMED_PRIMARY and _SNOMED_SECONDARY in
'oncotriage/registries/cancer_code_registry.py' against the UMLS Metathesaurus,
and fails if any entry is not what the comment beside it says it is. (It is
still called "File 08" throughout what follows: that is where the sets were
written, and item 20c pass 2a moved them into the package. The path this file
reads comes from the imported module's own __file__, so it cannot name the shim
by accident.)

WHY THIS EXISTS
---------------
File 08's SNOMED sets are curated by hand, and a curated set of opaque numeric
identifiers cannot be reviewed by reading it. Two defects lived in it:

    408512008  commented "Small cell carcinoma of lung, limited stage".
               It is "Body mass index 40+ - severely obese (finding)", and
               Synthea's wellness_encounters module emits it as a Condition, so
               every severely obese patient in the corpus classified as having
               a primary lung cancer. 48 non-cancer patients reached a
               1,000-patient cancer cohort. Nothing failed, because the code was
               in the set, the set was consulted, and the lookup succeeded --
               only the COMMENT was wrong, and a comment is not executable.

    315006     commented "Secondary malignant neoplasm of bone". It is not a
               SNOMED concept at all; it appears in UMLS only under MEDCIN
               ("antiphospholipid antibody syndrome with hemorrhagic disorder")
               and RxNorm. It matched anyway because is_primary_cancer()
               compared digits without reading system_key.

Both are the same class of defect: a claim in a comment that nothing checks.
This test makes the comment an assertion.

WHAT IT ASSERTS, per code, for every code in both sets:
    1. the code exists in SNOMEDCT_US
    2. File 08's comment matches the SNOMED fully specified name
    3. primary codes name a malignancy and not a secondary/metastatic concept
    4. secondary codes name a secondary/metastatic concept

and, for the five hand-typed ICD-10 categories and four block boundaries:
    5. the category is confirmed by a source, and which source is reported
    6. File 08's comment matches the official category title
    7. the category sits in the constant its comment names, and the registry
       actually classified it that way
    8. the four boundary constants match ICD-10-CM, and the blocks either side
       of each boundary classify as the standard says
    9. every chapter-2 code lands in exactly one of the three sets

TWO COMPARISON RULES, AND WHY THEY DIFFER
-----------------------------------------
The SNOMED comment check is a PREFIX match (after whitespace normalisation):
File 08's comment must START WITH the fully specified name. SNOMED entries
carry trailing annotations that are part of the record, not part of the
concept -- "Malignant neoplastic disease (disorder)  <- mCODE root",
"...(disorder)" followed by a note that a code is colon-specific or retired --
and demanding exact equality would force those notes out of the set definition
and into prose, where they are further from the code they qualify.

The ICD-10 category check is EXACT equality. Those five comments are written in
a fixed machine-readable form -- "<CATEGORY> = <title> -> <SET>" -- which this
test parses, so the title field has nowhere for an annotation to live and
nothing to be permissive about. Exact equality is the strongest rule available
and is used wherever it is available.

The rule is therefore: exact where the format is controlled, prefix where the
record legitimately carries more than the concept name. Neither is permissive
about the concept itself -- a comment naming a different concept fails both.

SOURCE
------
UMLS Metathesaurus MRCONSO.RRF under data_MeSH_path -- the same file File 09
uses for its SNOMED -> CUI -> MeSH crosswalk, so this adds no new dependency.
SAB=SNOMEDCT_US in the 2025AB release carries 532,287 distinct codes INCLUDING
retired concepts (SUPPRESS=O), so absence from it is evidence a code is not a
SNOMED identifier, not evidence that it is merely old.

If MRCONSO is absent the test FAILS. It does not skip and it does not fall back
to a weaker check: an audit that quietly does not run is worse than no audit,
because the exit code still says 0.

Run from terminal (or F5 in Spyder):
    python tests/test_registries_cancer_code_claims_audit.py
    (was: python "42- Cancer Code Registry Audit Test.py")

Exit codes:
    0 -- every code verified
    1 -- one or more failures, or MRCONSO unavailable
"""


# Run needed file
#----------------
# PASS 20d-2: THIS FILE IMPORTS THE PACKAGE. It used to exec "01- Imports.py"
# and "02- Utility Functions.py" into its own globals and then exec_chain()
# "08- Cancer Code Registry.py", which is how every registry name below used to
# arrive. Item 20c pass 2a moved File 08's definitions into
# oncotriage/registries/cancer_code_registry.py, so each name comes from the
# module that defines it.
#
# THE CANDIDATE DIRECTORY IS THE PARENT OF THIS FILE'S, not this file's own.
# The same block the other tests carry looks one level up because this file now
# sits in tests/ and the package sits BESIDE tests/, not inside it.
# `pip install -e .` makes the whole block a no-op.
import glob
import json
import os
import re
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

from oncotriage.paths import data_MeSH_path
from oncotriage.registries import cancer_code_registry as _ccr
from oncotriage.registries.cancer_code_registry import (
    _ICD10_ALPHA_NON_INVASIVE,
    _ICD10_ALPHA_PRIMARY,
    _ICD10_ALPHA_SECONDARY,
    _ICD10_C_BLOCK_MAX,
    _ICD10_C_SECONDARY_HI,
    _ICD10_C_SECONDARY_LO,
    _ICD10_D_NEOPLASM_BLOCK_MAX,
    _ICD10_SEED_PRIMARY,
    _SNOMED_PRIMARY,
    _SNOMED_SECONDARY,
    load_registry,
)


#------------------------------------------------------------------------------


# ===========================================================================
# EXTERNAL-STANDARD FACTS
# ===========================================================================

# MRCONSO.RRF is pipe-delimited with no header. Field positions are fixed by
# the UMLS release format, not by us. 0-indexed:
_MRCONSO_SAB = 11    # source abbreviation, e.g. SNOMEDCT_US
_MRCONSO_TTY = 12    # term type, e.g. FN / PT / OAF
_MRCONSO_CODE = 13   # the source's own code
_MRCONSO_STR = 14    # the term string

_SNOMED_SAB = "SNOMEDCT_US"

# ICD-10 source abbreviations, in the order a category prefix is looked up.
#
# ICD10CM is the US clinical modification and is what File 08 classifies. Its
# UMLS subset stops at category C96, so C97 -- a real ICD-10-CM category that
# the icd10-cm package also omits -- is not in it. ICD10 is the WHO
# international edition and does carry C97. The third source exists for exactly
# that code; every category reports which source confirmed it, so a fact
# resting on the WHO edition rather than the US modification is visible rather
# than assumed.
_ICD10CM_SAB = "ICD10CM"
_ICD10_WHO_SAB = "ICD10"

# ICD-10 category term types. HT is the hierarchy term (the category title),
# AB the abbreviated form, PT the preferred term.
_ICD10_TTY_PREFERENCE = ("HT", "PT", "AB")

# Term-type preference when reading a concept's name.
#
# FN is the fully specified name and is what this audit compares against. The
# rest are for RETIRED concepts, which have no active FN: OAF is the obsolete
# fully specified name, OAP the obsolete preferred term. A retired code is not
# an error here -- real EHR records still carry them, and File 08 keeps
# 372064008 deliberately for that reason -- but which form was used is reported
# so a retired entry is never mistaken for a current one.
_TTY_PREFERENCE = ("FN", "OAF", "PT", "OAP", "SY", "OAS")

# Words that make a SNOMED term a malignancy. A primary-set entry whose name
# contains none of these is not a cancer code, whatever the comment says.
#
# "neoplasm" is deliberately in the list even though a neoplasm can be benign:
# SNOMED's disorder names for several sites File 08 admits are exactly
# "Neoplasm of X" (126906006 prostate, 126952004 brain), and the benign cases
# are excluded elsewhere -- by _NON_INVASIVE_DISPLAY_TERMS on the display path
# and by the ICD-10 D00-D49 block on the coded path.
_MALIGNANCY_TERMS = (
    "malignant", "malignancy", "carcinoma", "cancer", "neoplasm", "leukemia",
    "leukaemia", "lymphoma", "melanoma", "myeloma", "sarcoma", "glioma",
    "glioblastoma", "mesothelioma", "blastoma",
)

# Words that make a SNOMED term a SECONDARY (metastatic) concept.
_SECONDARY_TERMS = ("metastatic", "metastasis", "metastases", "secondary")


#------------------------------------------------------------------------------


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================
# Same shape as '33- Cancer Code and Stage Extraction Test.py': record every
# outcome, never abort on the first failure, exit non-zero at the end. A
# registry audit that stopped at the first bad code would hide the rest.

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label: str, actual, expected) -> None:
    """Assert equality, record the outcome, never abort the run."""
    if actual == expected:
        _RESULTS["passed"] += 1
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected: {expected}\n          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def fail(label: str, detail: str) -> None:
    """Record an outright failure that is not an equality comparison."""
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}")
    print(f"          {detail}")


def passed(label: str) -> None:
    _RESULTS["passed"] += 1


#------------------------------------------------------------------------------


# ===========================================================================
# FILE 08 COMMENT EXTRACTION
# ===========================================================================
#
# ITEM 20c, PASS 2a RETARGETED THIS AT THE MODULE. The two extractors below do
# not read the registry's data structures -- they read its SOURCE, because the
# whole point is that a comment and the code it sits beside can disagree, and
# only the text has both. That source used to be "08- Cancer Code Registry.py";
# it is now oncotriage/registries/cancer_code_registry.py, and File 08 is a
# re-export shim holding nothing but import statements.
#
# READING THE SHIM WOULD NOT HAVE RAISED. It returns an EMPTY dict, and what
# that does to this audit was MEASURED rather than guessed, on a copy of this
# file pointed back at the shim:
#
#   correct target                   197 checks, 0 failed
#   shim target, guard removed       175 checks, 42 failed
#
# So it does go red -- the claim "the audit would pass having checked nothing"
# would have been an over-claim, and is not made here. What it does instead is
# worse in a quieter way: 22 assertions DISAPPEAR. The per-category ICD-10 loop
# is `for _cat in sorted(_ICD10_CLAIMS)`, and an empty dict means its body never
# runs, so those checks are neither passed nor failed -- they are absent, and
# absence does not show up in a Failed: count. The other 42 turn into
# "comment does not match UMLS" failures that name the wrong cause.
#
# The guard below converts both into one failure that names the file and the
# zero. It is a diagnosis, not a safety net.
#
# "tests/test_registries_cancer_code_claims_audit_control.py" plants its
# defects into
# this same file and hashes its restore against it.

# PASS 20d-2: the path comes from the imported module's own __file__ rather than
# from a _code_dir guess. That was correct only while this file sat beside the
# package; from tests/ it would have been one level off and every read below
# would have raised.
#
# IT ALSO CLOSES A HOLE THE GUESS LEFT OPEN, and this file is the one place in
# the repository where that matters most: the negative control plants defects
# into cancer_code_registry.py IN PLACE and then runs THIS file as a subprocess
# to prove each defect is caught. A hand-built path could name a different copy
# of the module than the one this process imported -- the audit would then read
# pristine text while the imported registry carried the plant, and every case
# would report "not caught" for the wrong reason. Asking the module where it
# lives makes the text and the behaviour the same file by construction.
_REGISTRY_SOURCE = os.path.abspath(_ccr.__file__)

def extract_file08_claims() -> dict:
    """
    Read the inline comment beside every quoted code in File 08's source.

    The comment is the CLAIM under audit, so it is read from the source text
    rather than from any structure the module exposes -- the whole point is
    that the comment and the code can disagree, and only the source has both.

    A trailing annotation after two spaces and an arrow (e.g. "  <- mCODE
    root") is not part of the claim and is cut. Everything before it must match
    the SNOMED fully specified name.

    Returns:
        dict: code -> claim string (first occurrence wins, which is the set
              definition; later mentions in prose are ignored).
    """
    source = open(_REGISTRY_SOURCE, encoding="utf-8").read()
    claims = {}
    for match in re.finditer(r'"(\d+)",\s*#\s*(.+)', source):
        code, comment = match.group(1), match.group(2)
        # Cut trailing annotations: "  <-", "  --", or a bare arrow.
        for marker in ("  ←", "←", "  --"):
            if marker in comment:
                comment = comment.split(marker, 1)[0]
        claims.setdefault(code, comment.strip())
    return claims


def extract_icd10_category_claims() -> dict:
    """
    Read File 08's machine-checkable ICD-10 category lines.

    Format, fixed by agreement with File 08's comment block:

        #   <CATEGORY> = <official title> -> <SET>

    with SET one of PRIMARY / SECONDARY / NON_INVASIVE. These five categories
    are the only ICD-10 assignments a human made; every other ICD-10 code is
    derived from the installed release by _build_icd10_cancer_sets().

    Returns:
        dict: category -> {"title": str, "set": str}
    """
    source = open(_REGISTRY_SOURCE, encoding="utf-8").read()
    claims = {}
    pattern = r'^#\s+([A-Z]\d[A-Z0-9])\s*=\s*(.+?)\s*->\s*(PRIMARY|SECONDARY|NON_INVASIVE)\s*$'
    for match in re.finditer(pattern, source, re.MULTILINE):
        claims[match.group(1)] = {"title": match.group(2).strip(),
                                  "set": match.group(3)}
    return claims


def normalize(term: str) -> str:
    """Lowercase and collapse whitespace. Nothing else is removed: the
    comparison below is a prefix match against the fully specified name, and
    stripping punctuation would let 'Malignant neoplasm of colon' pass for
    'Malignant neoplasm of colon, TNM stage 4'."""
    return re.sub(r"\s+", " ", term).strip().lower()


#------------------------------------------------------------------------------


# ===========================================================================
# MRCONSO LOOKUP
# ===========================================================================

def locate_mrconso() -> str:
    """
    Find MRCONSO.RRF under data_MeSH_path.

    Globbed rather than hardcoded to the 2025AB filename so a release bump does
    not silently turn this audit off -- but if nothing matches, the test FAILS
    rather than skipping.
    """
    candidates = sorted(glob.glob(os.path.join(data_MeSH_path, "MRCONSO*.RRF")))
    if not candidates:
        print()
        print("=" * 70)
        print("CANNOT RUN: UMLS MRCONSO not found")
        print("=" * 70)
        print(f"  Looked for: {os.path.join(data_MeSH_path, 'MRCONSO*.RRF')}")
        print()
        print("  This audit compares every SNOMED code in File 08 against the")
        print("  UMLS Metathesaurus. Without it there is nothing to compare")
        print("  against, so it fails rather than passing vacuously.")
        print()
        print("  MRCONSO.RRF is the same file '09- MeSH Cancer Site Relevance")
        print("  Filter.py' uses for its SNOMED -> CUI -> MeSH crosswalk.")
        print("  Download the UMLS Metathesaurus Full Subset (a UMLS licence is")
        print("  required) and place MRCONSO.RRF in the MeSH data directory.")
        print()
        sys.exit(1)
    return candidates[-1]


def load_terms(mrconso_path: str, wanted_by_sab: dict) -> dict:
    """
    ONE streaming pass over MRCONSO for every source this test needs.

    MRCONSO is ~2.2 GB, so it is read line by line and filtered on the way
    through. A single pass serves the SNOMED codes and both ICD-10 sources:
    three passes would triple the only expensive part of this test.

    Args:
        wanted_by_sab: {SAB: set_of_codes}

    Returns:
        dict: (sab, code) -> {tty: term}. Absent key means that source has no
              row for that code.
    """
    found = {}
    with open(mrconso_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            fields = line.split("|")
            if len(fields) <= _MRCONSO_STR:
                continue
            sab = fields[_MRCONSO_SAB]
            wanted = wanted_by_sab.get(sab)
            if not wanted:
                continue
            code = fields[_MRCONSO_CODE]
            if code not in wanted:
                continue
            found.setdefault((sab, code), {}).setdefault(
                fields[_MRCONSO_TTY], fields[_MRCONSO_STR]
            )
    return found


# ---------------------------------------------------------------------------
# MRCONSO extract cache
# ---------------------------------------------------------------------------
# MRCONSO is ~2.2 GB and this test needs about 45 rows out of it. Streaming the
# whole file cost ~10.6 s per run, and File 43 runs this test eleven times, so
# the control cost over two minutes -- slow enough that a control stops being
# run, which is the failure mode it exists to prevent.
#
# The cache holds only the extracted rows and lives BESIDE THE MRCONSO FILE, at
# a path derived from locate_mrconso()'s answer. No new configuration path is
# assumed or invented: if this test can find MRCONSO it can find its cache, and
# if MRCONSO moves the cache moves with it.
#
# THREE RULES, ALL LOAD-BEARING:
#
#   1. The cache is keyed on the release FILENAME plus the file's SIZE and
#      MODIFICATION TIME. Any of the three differing rebuilds it. Filename
#      alone would not notice a re-downloaded 2025AB; size alone would not
#      notice an edit that preserved length.
#
#   2. A CODE NOT IN THE CACHE TRIGGERS A REAL LOOKUP, and the answer is added.
#      A cache miss is never an absence verdict. This is the rule that keeps
#      File 43 honest: it plants SNOMED codes that were not in File 08 when the
#      cache was built, so they are necessarily absent from it. If a miss read
#      as "not in SNOMED", File 42 would report NOT_IN_SNOMED for codes that
#      ARE in SNOMED, every plant would be "caught" for the wrong reason, and
#      the negative control would certify an audit that no longer works.
#
#      Absence is therefore recorded EXPLICITLY: a code looked up and not found
#      goes in "absent", which is a verdict; a code in neither map has simply
#      never been looked up, which is not.
#
#   3. An unreadable or malformed cache is REPORTED and rebuilt, never silently
#      ignored. A cache that quietly resets itself every run looks like a cache
#      and performs like none.

_CACHE_VERSION = 1
_CACHE_SUFFIX = ".oncotriage_audit_cache.json"


def cache_path_for(mrconso_path: str) -> str:
    """The cache sits beside MRCONSO, named after it."""
    return mrconso_path + _CACHE_SUFFIX


def _source_fingerprint(mrconso_path: str) -> dict:
    """Release name + size + mtime. All three, see rule 1."""
    stat = os.stat(mrconso_path)
    return {
        "filename": os.path.basename(mrconso_path),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _cache_key(sab: str, code: str) -> str:
    """JSON object keys must be strings, so (sab, code) is flattened."""
    return f"{sab}|{code}"


def load_cache(mrconso_path: str):
    """
    Read the extract cache.

    Returns:
        tuple: (terms, absent, status)
            terms   {"SAB|CODE": {tty: term}}  -- looked up and found
            absent  set of "SAB|CODE"          -- looked up and NOT found
            status  human-readable account of which path was taken

    Every rejection path returns empty maps, so the caller rebuilds. Which
    path was taken is returned rather than printed here, so the caller reports
    it in one place.
    """
    path = cache_path_for(mrconso_path)
    fingerprint = _source_fingerprint(mrconso_path)

    if not os.path.exists(path):
        return {}, set(), "no cache yet, building"

    try:
        with open(path, encoding="utf-8") as fh:
            blob = json.load(fh)
    except Exception as exc:
        return {}, set(), (f"UNREADABLE ({type(exc).__name__}: {exc}), rebuilding")

    if not isinstance(blob, dict):
        return {}, set(), "MALFORMED (not a JSON object), rebuilding"
    if blob.get("cache_version") != _CACHE_VERSION:
        return {}, set(), (f"version {blob.get('cache_version')!r} != "
                           f"{_CACHE_VERSION}, rebuilding")

    stored = blob.get("source")
    if stored != fingerprint:
        differing = [k for k in fingerprint
                     if not isinstance(stored, dict) or stored.get(k) != fingerprint[k]]
        return {}, set(), (f"STALE (differs on {differing or 'source block'}), "
                           f"rebuilding")

    terms = blob.get("terms")
    absent = blob.get("absent")
    if not isinstance(terms, dict) or not isinstance(absent, list):
        return {}, set(), "MALFORMED (terms/absent wrong type), rebuilding"

    return terms, set(absent), f"hit ({len(terms)} cached, {len(absent)} known-absent)"


def save_cache(mrconso_path: str, terms: dict, absent: set) -> str:
    """
    Write the cache atomically. Returns a status string.

    A cache that cannot be written is NOT fatal -- the audit already has its
    answers -- but it is reported, because the next run will silently pay the
    full 2.2 GB scan again and the only symptom is slowness.
    """
    path = cache_path_for(mrconso_path)
    payload = {
        "cache_version": _CACHE_VERSION,
        "source": _source_fingerprint(mrconso_path),
        "note": ("Extract of the rows "
                 "'tests/test_registries_cancer_code_claims_audit.py' "
                 "needs from MRCONSO. Derived data, safe to delete: it is "
                 "rebuilt on the next run. 'absent' lists codes looked up "
                 "against this exact release and NOT found."),
        "terms": terms,
        "absent": sorted(absent),
    }
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return f"written ({os.path.getsize(path) / 1024:.0f} KB)"
    except OSError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return f"NOT WRITTEN ({type(exc).__name__}: {exc}) -- next run rescans"


def resolve_terms(mrconso_path: str, wanted_by_sab: dict):
    """
    Answer every (sab, code) from the cache, falling back to a real MRCONSO
    scan for anything not yet looked up.

    Returns:
        tuple: (terms_by_sab, report) where terms_by_sab is the same
               {(sab, code): {tty: term}} shape load_terms() returns, so the
               rest of this file is unchanged.
    """
    terms, absent, status = load_cache(mrconso_path)
    report = {"cache_status": status, "scanned": False,
              "looked_up": 0, "newly_absent": 0, "save": "not needed"}

    # Rule 2: anything in NEITHER map has never been looked up.
    missing = {}
    for sab, codes in wanted_by_sab.items():
        unknown = {c for c in codes
                   if _cache_key(sab, c) not in terms
                   and _cache_key(sab, c) not in absent}
        if unknown:
            missing[sab] = unknown

    if missing:
        report["scanned"] = True
        report["looked_up"] = sum(len(v) for v in missing.values())
        fresh = load_terms(mrconso_path, missing)
        for (sab, code), term_map in fresh.items():
            terms[_cache_key(sab, code)] = term_map
        for sab, codes in missing.items():
            for code in codes:
                if (sab, code) not in fresh:
                    absent.add(_cache_key(sab, code))
                    report["newly_absent"] += 1
        report["save"] = save_cache(mrconso_path, terms, absent)

    out = {}
    for sab, codes in wanted_by_sab.items():
        for code in codes:
            term_map = terms.get(_cache_key(sab, code))
            if term_map:
                out[(sab, code)] = term_map
    return out, report


def preferred_name_from(term_map: dict, preference):
    """Pick a concept's name by the given term-type preference.

    Falls through to the alphabetically-first term type only when none of the
    preferred ones is present, so an unexpected TTY is still reported rather
    than read as "not found". Returns (name, tty), or (None, None) if the map
    is empty.
    """
    for tty in preference:
        if tty in term_map:
            return term_map[tty], tty
    if term_map:
        tty = sorted(term_map)[0]
        return term_map[tty], tty
    return None, None


def preferred_name(term_map: dict):
    """SNOMED concept name, by _TTY_PREFERENCE. Returns (name, tty)."""
    return preferred_name_from(term_map, _TTY_PREFERENCE)


#------------------------------------------------------------------------------


# ===========================================================================
# THE AUDIT
# ===========================================================================

print()
print("=" * 70)
print("CANCER CODE REGISTRY AUDIT — File 08 SNOMED sets vs UMLS")
print("=" * 70)

def require_icd10():
    """
    Import the icd10-cm release, or fail the way locate_mrconso() fails.

    File 08 imports icd10 lazily inside _build_icd10_cancer_sets(), so a
    missing package surfaces here as a bare ImportError with a traceback and no
    indication that this is a fixable environment problem rather than a defect
    in the registry. The MRCONSO path already fails with instructions; this one
    now matches it. It still FAILS -- it does not skip the ICD-10 section,
    because an audit that quietly does not run still exits 0.
    """
    try:
        import icd10 as _icd10
    except ImportError as exc:
        print()
        print("=" * 70)
        print("CANNOT RUN: the icd10-cm package is not installed")
        print("=" * 70)
        print(f"  ImportError: {exc}")
        print()
        print("  This audit checks File 08's five hand-typed ICD-10 category")
        print("  prefixes and four block boundaries against the installed")
        print("  ICD-10-CM release. Without the package there is nothing to")
        print("  check them against, so it fails rather than passing vacuously.")
        print()
        print("  It is the same release File 08 itself loads at import time")
        print("  (_build_icd10_cancer_sets), so the registry cannot classify")
        print("  ICD-10 codes without it either.")
        print()
        print("      pip install icd10-cm")
        print()
        sys.exit(1)
    return _icd10


icd10 = require_icd10()

_REG = load_registry()
_CLAIMS = extract_file08_claims()
_ICD10_CLAIMS = extract_icd10_category_claims()

# BOTH CLAIM DICTS MUST BE NON-EMPTY BEFORE ANYTHING IS COMPARED.
#
# Every assertion below that checks a comment against the standard is a lookup
# into one of these two dicts. An empty dict does not fail any of them -- it
# makes the ones inside a `for ... in _ICD10_CLAIMS` loop stop running
# altogether, and turns the rest into failures that blame the standard rather
# than the path.
#
# That is not hypothetical. It is precisely what item 20c, pass 2a would have
# produced if _REGISTRY_SOURCE had been left pointing at "08- Cancer Code
# Registry.py" after the registry moved into the package: the shim holds import
# statements and comments, no `"code",  # claim` lines and no
# `#   C4A = ... -> PRIMARY` lines, so both regexes match nothing and both dicts
# come back empty. Demonstrated on a copy -- see the measurement in the section
# header above. The visible damage is 42 misleading failures; the invisible
# damage is 22 assertions that stop running at all.
#
# The floors are deliberately loose. They are here to catch "the file I read had
# none of this in it", not to pin a count that a legitimate edit would move --
# the exact-set assertions further down do that job. Observed on 2026-08-05,
# before and after the move, identically: 40 code claims and 5 category claims.
# 20 leaves room for codes to be retired (item 18b retired one) while still
# being a hundred times further from zero than an empty read.
#
# The category floor is exact rather than loose because the category set IS
# exact: _HAND_TYPED_CATEGORIES below asserts it equals five named prefixes, so
# a sixth would fail there and be seen, not hidden here.
check("claims were extracted from the registry source at all (non-degeneracy)",
      len(_CLAIMS) >= 20, True)
check("ICD-10 category claims were extracted at all (non-degeneracy)",
      len(_ICD10_CLAIMS) >= 5, True)
if not _CLAIMS or not _ICD10_CLAIMS:
    fail("the registry source is readable and has the shape this audit parses",
         f"{_REGISTRY_SOURCE!r} yielded {len(_CLAIMS)} code claims and "
         f"{len(_ICD10_CLAIMS)} category claims. Every comparison below is a "
         f"lookup into those dicts, so an empty one means this audit is about "
         f"to pass without checking anything. Refusing to continue.")
    print()
    print(f"Passed: {_RESULTS['passed']}")
    print(f"Failed: {_RESULTS['failed']}")
    for _f in _FAILURES:
        print(f"  - {_f}")
    sys.exit(1)

_MRCONSO_PATH = locate_mrconso()
print(f"  MRCONSO:  {_MRCONSO_PATH}")
print(f"  SNOMED primary:   {len(_SNOMED_PRIMARY)} codes")
print(f"  SNOMED secondary: {len(_SNOMED_SECONDARY)} codes")
print(f"  ICD-10 categories: {len(_ICD10_CLAIMS)} hand-typed prefixes")
print()

_ALL_CODES = set(_SNOMED_PRIMARY) | set(_SNOMED_SECONDARY)
_ICD10_CATEGORIES = set(_ICD10_CLAIMS)
_TERMS_BY_SAB, _CACHE_REPORT = resolve_terms(_MRCONSO_PATH, {
    _SNOMED_SAB:    _ALL_CODES,
    _ICD10CM_SAB:   _ICD10_CATEGORIES,
    _ICD10_WHO_SAB: _ICD10_CATEGORIES,
})
_TERMS = {code: t for (sab, code), t in _TERMS_BY_SAB.items() if sab == _SNOMED_SAB}

print(f"  Cache:    {cache_path_for(_MRCONSO_PATH)}")
print(f"  Status:   {_CACHE_REPORT['cache_status']}")
if _CACHE_REPORT["scanned"]:
    print(f"  Scanned MRCONSO (2.2 GB) for {_CACHE_REPORT['looked_up']} code(s) "
          f"not previously looked up; {_CACHE_REPORT['newly_absent']} not found")
    print(f"  Cache:    {_CACHE_REPORT['save']}")
else:
    print("  No MRCONSO scan needed: every code was already looked up")
print(f"  Resolved {len(_TERMS)}/{len(_ALL_CODES)} SNOMED codes under SAB={_SNOMED_SAB}")
print()


# ---------------------------------------------------------------------------
# Set-level invariants
# ---------------------------------------------------------------------------
print("-" * 70)
print("Set-level invariants")
print("-" * 70)

check("primary and secondary sets are disjoint",
      sorted(set(_SNOMED_PRIMARY) & set(_SNOMED_SECONDARY)), [])
check("every primary entry is all digits",
      sorted(c for c in _SNOMED_PRIMARY if not c.isdigit()), [])
check("every secondary entry is all digits",
      sorted(c for c in _SNOMED_SECONDARY if not c.isdigit()), [])
check("every code has an inline comment in File 08",
      sorted(c for c in _ALL_CODES if c not in _CLAIMS), [])
print(f"  ({_RESULTS['passed']} set-level assertions passed)" if not _RESULTS["failed"] else "")


# ---------------------------------------------------------------------------
# Per-code audit
# ---------------------------------------------------------------------------
def audit_code(code: str, section: str) -> dict:
    """
    Run every per-code assertion. Returns a row for the report table.

    section is "primary" or "secondary" and decides assertion 4.
    """
    claim = _CLAIMS.get(code, "")
    # verdicts is a LIST, not a single value. A code can fail more than one
    # assertion -- 408512008 fails both the comment check and the malignancy
    # check -- and keeping only the last one made the table under-report the
    # very defect this file exists to surface.
    row = {"code": code, "section": section, "claim": claim,
           "umls": None, "tty": None, "verdicts": []}

    # 1. The code must exist under SAB=SNOMEDCT_US.
    term_map = _TERMS.get(code)
    if not term_map:
        fail(f"[{section}] {code} exists in SNOMEDCT_US",
             f"absent from SAB={_SNOMED_SAB} in {os.path.basename(_MRCONSO_PATH)} "
             f"(532k codes incl. retired). File 08 claims: {claim!r}. "
             f"This is not a SNOMED identifier.")
        row["verdicts"].append("NOT_IN_SNOMED")
        return row
    passed(f"[{section}] {code} exists in SNOMEDCT_US")

    name, tty = preferred_name(term_map)
    row["umls"], row["tty"] = name, tty

    # 2. The File 08 comment must match the fully specified name.
    #    Prefix match, so a trailing annotation is allowed but a different
    #    concept is not.
    if not normalize(claim).startswith(normalize(name)):
        fail(f"[{section}] {code} comment matches UMLS",
             f"File 08 says {claim!r}\n"
             f"          UMLS [{tty}] says {name!r}\n"
             f"          Rewrite the comment in File 08 to the UMLS name.")
        row["verdicts"].append("COMMENT_MISMATCH")
    else:
        passed(f"[{section}] {code} comment matches UMLS")

    lowered = name.lower()

    # 3 / 4. The name must be the right KIND of concept for its set.
    if section == "primary":
        if not any(t in lowered for t in _MALIGNANCY_TERMS):
            fail(f"[primary] {code} names a malignancy",
                 f"UMLS [{tty}] name {name!r} contains no malignancy term "
                 f"{_MALIGNANCY_TERMS}")
            row["verdicts"].append("NOT_A_MALIGNANCY")
        else:
            passed(f"[primary] {code} names a malignancy")

        if any(t in lowered for t in _SECONDARY_TERMS):
            fail(f"[primary] {code} is not a secondary concept",
                 f"UMLS [{tty}] name {name!r} reads as secondary/metastatic but "
                 f"the code sits in _SNOMED_PRIMARY")
            row["verdicts"].append("SECONDARY_IN_PRIMARY")
        else:
            passed(f"[primary] {code} is not a secondary concept")
    else:
        if not any(t in lowered for t in _SECONDARY_TERMS):
            fail(f"[secondary] {code} names a secondary/metastatic concept",
                 f"UMLS [{tty}] name {name!r} contains no secondary term "
                 f"{_SECONDARY_TERMS}")
            row["verdicts"].append("NOT_SECONDARY")
        else:
            passed(f"[secondary] {code} names a secondary/metastatic concept")

    return row


print()
print("-" * 70)
print("Per-code audit")
print("-" * 70)

_ROWS = []
for _code in sorted(_SNOMED_PRIMARY, key=lambda c: (len(c), c)):
    _ROWS.append(audit_code(_code, "primary"))
for _code in sorted(_SNOMED_SECONDARY, key=lambda c: (len(c), c)):
    _ROWS.append(audit_code(_code, "secondary"))


# ---------------------------------------------------------------------------
# Report table — printed whether or not anything failed, because the value of
# this audit is the table, not the exit code.
# ---------------------------------------------------------------------------
print()
print("-" * 70)
print("Verified codes")
print("-" * 70)
for _row in _ROWS:
    _flag = "OK " if not _row["verdicts"] else "!! "
    _tty = f"[{_row['tty']}]" if _row["tty"] else "[--]"
    print(f"  {_flag}{_row['section'][:4]:4s} {_row['code']:16s} {_tty:6s} {_row['umls']}")
    if _row["verdicts"]:
        print(f"      ALL FAILURES: {', '.join(_row['verdicts'])}")
        print(f"      File 08 says: {_row['claim']!r}")

_RETIRED = [r for r in _ROWS if r["tty"] in ("OAF", "OAP", "OAS")]
if _RETIRED:
    print()
    print("  RETIRED concepts (no active fully specified name) — kept on purpose,")
    print("  legacy real-EHR records still carry them:")
    for _row in _RETIRED:
        print(f"    {_row['code']:16s} [{_row['tty']}] {_row['umls']}")


# ===========================================================================
# ICD-10-CM HAND-CURATED INPUTS
# ===========================================================================
# Nine facts: five category prefixes and four block boundaries. Everything else
# in File 08's ICD-10 layer is derived from the installed icd10-cm release at
# import time, so these nine are the entire hand-typed surface -- and they carry
# the same gap _SNOMED_PRIMARY carried, where a wrong assignment is a comment
# nothing checks.
#
# The block logic is NOT re-implemented here. Every assertion below asks the
# registry what it decided (via the three normalized sets it built) rather than
# recomputing the answer, so a test that agreed with a buggy implementation
# cannot pass.

print()
print("-" * 70)
print("ICD-10 category prefixes")
print("-" * 70)

_PRIM = _REGISTRY_ICD10_PRIMARY = _REG._icd10_primary_norm
_SECO = _REG._icd10_secondary_norm
_NONI = _REG._icd10_non_invasive_norm

_SET_TO_CONSTANT = {
    "PRIMARY":      ("_ICD10_ALPHA_PRIMARY / _ICD10_SEED_PRIMARY",
                     set(_ICD10_ALPHA_PRIMARY) | set(_ICD10_SEED_PRIMARY)),
    "SECONDARY":    ("_ICD10_ALPHA_SECONDARY", set(_ICD10_ALPHA_SECONDARY)),
    "NON_INVASIVE": ("_ICD10_ALPHA_NON_INVASIVE", set(_ICD10_ALPHA_NON_INVASIVE)),
}
_SET_TO_NORM = {"PRIMARY": _PRIM, "SECONDARY": _SECO, "NON_INVASIVE": _NONI}

# Parenthesised deliberately: "-" binds tighter than "|", so without the outer
# parentheses this computes (SEED - CLAIMS) and unions the rest back in, which
# reports every alpha category as missing whatever the comments say.
_HAND_TYPED_CATEGORIES = (set(_ICD10_ALPHA_PRIMARY)
                          | set(_ICD10_ALPHA_SECONDARY)
                          | set(_ICD10_ALPHA_NON_INVASIVE)
                          | set(_ICD10_SEED_PRIMARY))
check("every hand-typed ICD-10 category has a machine-readable line",
      sorted(_HAND_TYPED_CATEGORIES - set(_ICD10_CLAIMS)), [])
check("every machine-readable line names a real hand-typed category",
      sorted(set(_ICD10_CLAIMS) - _HAND_TYPED_CATEGORIES), [])

_ICD10_ROWS = []
for _cat in sorted(_ICD10_CLAIMS):
    _claim = _ICD10_CLAIMS[_cat]
    _title, _want_set = _claim["title"], _claim["set"]
    _row = {"category": _cat, "claim": _title, "set": _want_set,
            "source": None, "official": None, "verdicts": []}

    # -- resolve the official title. Three sources, tried in order, and which
    #    one answered is recorded: a category confirmed only by the WHO edition
    #    is a weaker fact than one confirmed by ICD-10-CM, and the difference
    #    must be visible rather than averaged away.
    _pkg = icd10.find(_cat)
    _cm = preferred_name_from(_TERMS_BY_SAB.get((_ICD10CM_SAB, _cat), {}),
                              _ICD10_TTY_PREFERENCE)
    _who = preferred_name_from(_TERMS_BY_SAB.get((_ICD10_WHO_SAB, _cat), {}),
                               _ICD10_TTY_PREFERENCE)
    if _cm[0]:
        _row["official"], _row["source"] = _cm[0], f"UMLS SAB={_ICD10CM_SAB} [{_cm[1]}]"
    elif _pkg is not None and getattr(_pkg, "description", None):
        _row["official"], _row["source"] = _pkg.description, "icd10-cm package"
    elif _who[0]:
        _row["official"], _row["source"] = _who[0], f"UMLS SAB={_ICD10_WHO_SAB} [{_who[1]}]"

    if _row["official"] is None:
        fail(f"[icd10] {_cat} is confirmed by a source",
             f"not found in UMLS SAB={_ICD10CM_SAB}, nor in the installed "
             f"icd10-cm release, nor in UMLS SAB={_ICD10_WHO_SAB}. "
             f"File 08 claims: {_title!r}. Nothing confirms this category.")
        _row["verdicts"].append("NO_SOURCE")
        _ICD10_ROWS.append(_row)
        continue
    passed(f"[icd10] {_cat} is confirmed by a source")

    # -- the comment must match the official title.
    if normalize(_title) != normalize(_row["official"]):
        fail(f"[icd10] {_cat} comment matches the standard",
             f"File 08 says {_title!r}\n"
             f"          {_row['source']} says {_row['official']!r}")
        _row["verdicts"].append("TITLE_MISMATCH")
    else:
        passed(f"[icd10] {_cat} comment matches the standard")

    # -- the comment's declared set must be the constant it actually sits in.
    _const_name, _const_members = _SET_TO_CONSTANT[_want_set]
    if _cat not in _const_members:
        fail(f"[icd10] {_cat} is in the constant its comment names",
             f"comment declares -> {_want_set} ({_const_name}) but {_cat} is "
             f"not a member of it")
        _row["verdicts"].append("WRONG_CONSTANT")
    else:
        passed(f"[icd10] {_cat} is in the constant its comment names")

    # -- and the registry must actually have classified it that way. This is
    #    the assertion that catches a constant that is populated but never
    #    consulted by _build_icd10_cancer_sets().
    _landed = [name for name, s in (("PRIMARY", _PRIM), ("SECONDARY", _SECO),
                                    ("NON_INVASIVE", _NONI)) if _cat in s]
    if _landed != [_want_set]:
        fail(f"[icd10] {_cat} lands in exactly its declared set",
             f"comment declares -> {_want_set}, registry put it in {_landed or 'NO SET'}")
        _row["verdicts"].append("WRONG_SET")
    else:
        passed(f"[icd10] {_cat} lands in exactly its declared set")

    _ICD10_ROWS.append(_row)

for _row in _ICD10_ROWS:
    _flag = "OK " if not _row["verdicts"] else "!! "
    print(f"  {_flag}{_row['category']:5s} -> {_row['set']:13s} {_row['official']}")
    print(f"        confirmed by: {_row['source']}")
    if _row["verdicts"]:
        print(f"        ALL FAILURES: {', '.join(_row['verdicts'])}")
        print(f"        File 08 says: {_row['claim']!r}")

# C97's seed exists because the installed package omits it. If a release ever
# adds it the seed is a no-op, and that should be known rather than discovered.
check("C97 is still absent from the installed icd10-cm release "
      "(the reason _ICD10_SEED_PRIMARY exists)",
      "C97" in icd10.codes, False)


print()
print("-" * 70)
print("ICD-10 block boundaries")
print("-" * 70)

def _show(label, ok):
    """The boundary assertions are few and load-bearing, so unlike the 160
    per-code SNOMED assertions they print on success too."""
    print(f"  {'OK ' if ok else '!! '}{label}")


# _ICD10_C_BLOCK_MAX = 97 -- no C-code with numeric block digits above 97
# exists in the release. Asserted against the release, not against the constant.
_C_CODES = [c for c in icd10.codes if c.startswith("C")]
_C_BLOCKS = {int(c[1:3]) for c in _C_CODES if c[1:3].isdigit()}
_over = sorted(b for b in _C_BLOCKS if b > _ICD10_C_BLOCK_MAX)
check(f"_ICD10_C_BLOCK_MAX ({_ICD10_C_BLOCK_MAX}): no C-block above it in the release",
      _over, [])
_show(f"_ICD10_C_BLOCK_MAX = {_ICD10_C_BLOCK_MAX}: release C-blocks run {min(_C_BLOCKS):02d}-{max(_C_BLOCKS):02d}, none above", not _over)
check("...and the constant is not needlessly loose "
      "(the release's highest C-block is at or below it)",
      max(_C_BLOCKS) <= _ICD10_C_BLOCK_MAX, True)
_show(f"...highest C-block in the release is C{max(_C_BLOCKS):02d}", max(_C_BLOCKS) <= _ICD10_C_BLOCK_MAX)


def _classify(code):
    """Which of the three normalized sets a code landed in. Asks the registry;
    does not recompute the block logic."""
    norm = code.upper().replace(".", "")
    return [name for name, s in (("PRIMARY", _PRIM), ("SECONDARY", _SECO),
                                 ("NON_INVASIVE", _NONI)) if norm in s]


def _first_code_in_block(letter, block):
    """A real code from the release in the given category, or None."""
    prefix = f"{letter}{block:02d}"
    for c in sorted(icd10.codes):
        if c.startswith(prefix):
            return c
    return None


# The block numbers below are LITERALS, deliberately.
#
# They were originally written as _ICD10_C_SECONDARY_LO - 1, _LO, _HI, _HI + 1,
# which is a tautology: change the constant and the test moves with it, so the
# registry and the test agree on a wrong answer and nothing fails. The negative
# control proved it -- LO 77->78, HI 79->78 and D_MAX 49->50 were all planted
# and all three passed. These are facts about ICD-10-CM chapter 2 (CMS FY2024),
# so they are written out and the constants are checked AGAINST them.
_ICD10CM_C_BLOCK_MAX_STANDARD    = 97   # chapter 2 malignant range is C00-C97
_ICD10CM_C_SECONDARY_LO_STANDARD = 77   # C77-C79 secondary / metastatic sites
_ICD10CM_C_SECONDARY_HI_STANDARD = 79
_ICD10CM_D_BLOCK_MAX_STANDARD    = 49   # D00-D49 rest of chapter 2; D50+ is ch.3

check("_ICD10_C_BLOCK_MAX matches the standard",
      _ICD10_C_BLOCK_MAX, _ICD10CM_C_BLOCK_MAX_STANDARD)
check("_ICD10_C_SECONDARY_LO matches the standard",
      _ICD10_C_SECONDARY_LO, _ICD10CM_C_SECONDARY_LO_STANDARD)
check("_ICD10_C_SECONDARY_HI matches the standard",
      _ICD10_C_SECONDARY_HI, _ICD10CM_C_SECONDARY_HI_STANDARD)
check("_ICD10_D_NEOPLASM_BLOCK_MAX matches the standard",
      _ICD10_D_NEOPLASM_BLOCK_MAX, _ICD10CM_D_BLOCK_MAX_STANDARD)
_show(f"constants vs standard: C_BLOCK_MAX={_ICD10_C_BLOCK_MAX}, "
      f"SECONDARY={_ICD10_C_SECONDARY_LO}-{_ICD10_C_SECONDARY_HI}, "
      f"D_BLOCK_MAX={_ICD10_D_NEOPLASM_BLOCK_MAX}",
      (_ICD10_C_BLOCK_MAX, _ICD10_C_SECONDARY_LO, _ICD10_C_SECONDARY_HI,
       _ICD10_D_NEOPLASM_BLOCK_MAX)
      == (_ICD10CM_C_BLOCK_MAX_STANDARD, _ICD10CM_C_SECONDARY_LO_STANDARD,
          _ICD10CM_C_SECONDARY_HI_STANDARD, _ICD10CM_D_BLOCK_MAX_STANDARD))

# Behaviour at the boundary, at literal blocks: C76 and C80 bracket the
# secondary range and must classify PRIMARY; C77 and C79 are its endpoints and
# must classify SECONDARY.
for _blk, _want in ((76, "PRIMARY"), (77, "SECONDARY"),
                    (79, "SECONDARY"), (80, "PRIMARY")):
    _c = _first_code_in_block("C", _blk)
    if _c is None:
        fail(f"[icd10] C{_blk:02d} has a code in the release",
             f"no C{_blk:02d}* code found; the boundary cannot be tested")
    else:
        _got = _classify(_c)
        check(f"C{_blk:02d} ({_c}) classifies {_want}", _got, [_want])
        _show(f"C{_blk:02d} ({_c}) -> {_got or ['NO SET']}, expected {_want}", _got == [_want])

# _ICD10_D_NEOPLASM_BLOCK_MAX = 49 -- D49 is non-invasive, D50 is chapter 3 and
# must be in none of the three sets.
# Literal blocks again, for the same reason.
_d49 = _first_code_in_block("D", 49)
_d50 = _first_code_in_block("D", 50)
if _d49 is None:
    fail("[icd10] D49 has a code in the release", "no D49* code found")
else:
    _g49 = _classify(_d49)
    check(f"D49 ({_d49}) classifies NON_INVASIVE", _g49, ["NON_INVASIVE"])
    _show(f"D49 ({_d49}) -> {_g49 or ['NO SET']}, expected NON_INVASIVE", _g49 == ["NON_INVASIVE"])
if _d50 is None:
    fail("[icd10] D50 has a code in the release", "no D50* code found")
else:
    _g50 = _classify(_d50)
    check(f"D50 ({_d50}) is in NO set (chapter 3, not a neoplasm)", _g50, [])
    _show(f"D50 ({_d50}) -> {_g50 or ['NO SET']}, expected NO SET", _g50 == [])


print()
print("-" * 70)
print("ICD-10 coverage: every chapter-2 code lands in exactly one set")
print("-" * 70)

# Every C code, and every D code with block digits <= 49, must belong to
# EXACTLY ONE of the three sets. Not zero (silently dropped, the D3A bug), not
# two (a code both admitted and excluded, where the answer depends on which
# check runs first).
_COVERAGE_CODES = list(_C_CODES)
for _c in icd10.codes:
    if not _c.startswith("D"):
        continue
    if _c[1:3].isdigit() and int(_c[1:3]) <= _ICD10_D_NEOPLASM_BLOCK_MAX:
        _COVERAGE_CODES.append(_c)
    elif _c[:3] in _ICD10_ALPHA_NON_INVASIVE:
        _COVERAGE_CODES.append(_c)

_uncovered, _multi = [], []
for _c in _COVERAGE_CODES:
    _landed = _classify(_c)
    if len(_landed) == 0:
        _uncovered.append(_c)
    elif len(_landed) > 1:
        _multi.append((_c, _landed))

print(f"  codes checked: {len(_COVERAGE_CODES)} "
      f"({len(_C_CODES)} C-codes + {len(_COVERAGE_CODES) - len(_C_CODES)} D-codes)")
# The assertions compare a TRUNCATED sample so a large regression does not
# print thousands of codes, but the totals are printed alongside: a message
# showing 20 offenders when there are 900 reads as a small problem.
print(f"  missing from all three sets: {len(_uncovered)}")
print(f"  in more than one set:        {len(_multi)}")
check(f"no chapter-2 code is missing from all three sets "
      f"(total missing: {len(_uncovered)}, sample of up to 20 shown)",
      _uncovered[:20], [])
check(f"no chapter-2 code is in more than one set "
      f"(total multi-set: {len(_multi)}, sample of up to 20 shown)",
      _multi[:20], [])


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Codes audited: {len(_ROWS)}  ({len(_SNOMED_PRIMARY)} primary, "
      f"{len(_SNOMED_SECONDARY)} secondary)")
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
Created on Mon Aug  3 21:30:00 2026

@author: ramyalsaffar
"""
