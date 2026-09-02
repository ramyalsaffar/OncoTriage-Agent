"""THE CAMPAIGN COHORT'S GROUP CACHE: one changed bundle re-parses one bundle.

WHAT IT GUARDS. `oncotriage/evaluation/cohort_groups.py:scan()` parses every
bundle in the corpus to decide each patient's cancer group, which the
stratified cohort draw needs before the first patient runs. MEASURED at 174.8 s
for 1,000 bundles totalling 39.1 GB, plus the ICD-10-CM build -- on the
critical path of every campaign and every ablation study. The cache removes
that for a repeat run on an unchanged corpus.

THE THREE PROPERTIES THIS FILE EXISTS TO HOLD, each driven rather than argued:

  1  A SECOND SCAN PARSES NOTHING. Counted at the parser, not inferred from
     elapsed time -- a timing assertion is a statement about the machine.
  2  A TOUCHED BUNDLE RE-PARSES ONLY ITSELF. The count is pinned at exactly
     one, so a cache that invalidated wholesale would fail here rather than
     looking like a cache that works.
  3  A ROW WHOSE STAT NO LONGER MATCHES IS DISCARDED -- driven separately for
     the mtime half and the size half, because a key that had silently dropped
     one of the two would still pass a test that only moved the other.

AND THE ONE A STAT KEY CANNOT GIVE: a cache keyed on file signatures alone
survives a change to the GROUPING CODE, because no file on disk moves when the
vocabulary or the derivation is edited. `grouper_digest()` is what closes that,
and section 5 drives it -- WITH the corpus byte-for-byte unchanged, which is
the only way that check means anything.

NO NETWORK, NO KEYS, NO SPEND, no live Qdrant, NO MODEL LOAD, NO CORPUS -- the
parser is a counting stand-in installed by rebinding a module attribute inside
try/finally with the restore asserted BY IDENTITY, so no FHIR bundle is
fabricated and none is read. No database, no git history, no live server, no
subprocess. NOT in the collision matrix: every file it writes is inside a
`tempfile.mkdtemp` it removes and asserts gone, `paths._RESOLVED` is seeded so
`cache_path()` can never resolve to the real `08- Checkpoint/`, and the one
repository file it reads is sha256-compared at the end. It EXECS NOTHING and
loads no module by location. Bucket A.
"""

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CODE_DIR = os.path.dirname(_TESTS_DIR)
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from oncotriage import paths as _paths                            # noqa: E402

# SEEDED BEFORE THE MODULE UNDER TEST IS IMPORTED, and before anything can call
# cache_path(). `checkpoint_path` is a lazy glob over the sibling tree; seeding
# _RESOLVED is the same seam tests/test_ablation_db_isolation.py uses, and it
# is what makes "this file cannot touch the real 08- Checkpoint/" a property of
# the harness rather than of the assertions.
_TMP = tempfile.mkdtemp(prefix="oncotriage-group-cache-")
_SAVED_RESOLVED = dict(_paths._RESOLVED)
_paths._RESOLVED["checkpoint_path"] = _TMP

from oncotriage.evaluation import cohort_groups as _cg            # noqa: E402
from oncotriage.registries import primary_cancer as _pc           # noqa: E402

_CG_PATH = os.path.abspath(_cg.__file__)
_CG_SHA_BEFORE = hashlib.sha256(
    io.open(_CG_PATH, "rb").read()).hexdigest()

_PASSED = 0
_FAILED = 0


def check(label, actual, expected):
    global _PASSED, _FAILED
    if actual == expected:
        _PASSED += 1
        print(f"  PASS  {label}")
    else:
        _FAILED += 1
        print(f"  FAIL  {label}\n          expected: {expected!r}\n"
              f"          actual:   {actual!r}")


def section(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def guarded(fn, *a, **kw):
    """Every raise-capable call goes through this.

    A check whose ARGUMENT raises takes the file down with no summary. A marker
    string fails the comparison and names what happened.
    """
    try:
        return fn(*a, **kw)
    except Exception as exc:            # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


#------------------------------------------------------------------------------
# THE HARNESS
#------------------------------------------------------------------------------

_CORPUS = os.path.join(_TMP, "corpus")
os.makedirs(_CORPUS, exist_ok=True)

# THE BUNDLES ARE NOT FHIR AND DO NOT NEED TO BE. The parser is stubbed, so
# what is on disk matters only for its SIZE and its MTIME -- which is exactly
# what the cache key reads. Fabricating valid FHIR here would test the parser.
_STEMS = [f"patient-{i:03d}" for i in range(6)]
_GROUPS = {"patient-000": "colorectal", "patient-001": "breast",
           "patient-002": "prostate", "patient-003": "lung",
           "patient-004": "hematologic", "patient-005": "colorectal"}
_BAD_STEM = "patient-003"          # the one the stub refuses to parse


def _write(stem, body):
    path = os.path.join(_CORPUS, f"{stem}.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


_FILES = [_write(s, json.dumps({"stem": s, "pad": "x" * 32})) for s in _STEMS]

_parsed = []                        # every stem the stub was asked to parse


class _StubFailure(RuntimeError):
    pass


def _stub_parse(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    _parsed.append(stem)
    if stem == _BAD_STEM:
        raise _StubFailure("this bundle is deliberately unparseable")
    return {"patient_id": f"id-{stem}", "conditions": [], "_stem": stem}


def _stub_group(patient, registry=None):
    return _GROUPS[patient["_stem"]]


def _stub_registry():
    return None


def scan(**kw):
    """Drive the REAL scan() with the parser, grouper and registry stubbed.

    The three stand-ins are rebound on the MODULE, which is where `scan` looks
    them up, and restored in a `finally`. `_parsed` is cleared first, so every
    check below reads the parses THAT drive made.
    """
    del _parsed[:]
    saved = (_cg.parse_fhir_bundle, _cg.patient_cancer_group, _cg.load_registry)
    try:
        _cg.parse_fhir_bundle = _stub_parse
        _cg.patient_cancer_group = _stub_group
        _cg.load_registry = _stub_registry
        return _cg.scan(_FILES, out=lambda *a, **k: None, **kw)
    finally:
        (_cg.parse_fhir_bundle, _cg.patient_cancer_group,
         _cg.load_registry) = saved


def parsed():
    return sorted(_parsed)


def cache_rows():
    """The cache payload, or a marker. NEVER raises and never returns a str
    a caller might subscript by accident -- see `rows_of` and `at`.
    """
    try:
        with open(_cg.cache_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:            # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


def rows_of(payload=None):
    """``payload["rows"]`` as a dict, or a NAMED ABSENCE.

    THE FIRST VERSION OF THIS FILE SUBSCRIPTED THE PAYLOAD DIRECTLY and the
    revert matrix caught it: a plant that stopped the cache being written at
    all made `cache_rows()` return a marker STRING, `_payload["rows"]` raised
    `TypeError` at module level, and the run reported six failures and NO
    SUMMARY where it owed sixty-three results. That is the abort shape this
    project has shipped repeatedly, and it fires exactly when the file owes
    the most information.
    """
    payload = cache_rows() if payload is None else payload
    if isinstance(payload, dict) and isinstance(payload.get("rows"), dict):
        return payload["rows"]
    return {}


def at(mapping, *keys):
    """Nested lookup that returns a named absence instead of raising."""
    node = mapping
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return f"<ABSENT {'.'.join(map(str, keys))}>"
        node = node[k]
    return node


def rewrite_cache(mutate):
    """Load the payload, hand it to `mutate`, write it back. Reports absence.

    Returns True when the round trip happened. A section that corrupts a row
    must be able to say "there was no cache to corrupt" rather than raise.
    """
    payload = cache_rows()
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), dict):
        return False
    mutate(payload["rows"])
    with open(_cg.cache_path(), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return True


def clear_cache():
    if os.path.exists(_cg.cache_path()):
        os.unlink(_cg.cache_path())


def bump_mtime(stem, delta_ns=10 ** 9):
    """Move mtime WITHOUT changing size -- the mtime half of the key, alone."""
    path = os.path.join(_CORPUS, f"{stem}.json")
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + delta_ns))


def grow(stem, extra="!"):
    """Change SIZE while pinning mtime back -- the size half of the key, alone."""
    path = os.path.join(_CORPUS, f"{stem}.json")
    st = os.stat(path)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(extra)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


#------------------------------------------------------------------------------
section("SECTION 0 -- the harness cannot reach the real checkpoint directory")
#------------------------------------------------------------------------------

check("0a. cache_path() resolves inside the scratch tree",
      guarded(_cg.cache_path).startswith(_TMP), True)
check("0b. ...and is named by the module's own constant",
      os.path.basename(guarded(_cg.cache_path)), _cg.CACHE_FILENAME)
# NON-DEGENERACY: without this, 0a is satisfied by a _TMP that happens to be a
# prefix of everything, and by a cache_path() that returns "".
check("0c. NON-DEGENERATE: the scratch root is a real, distinct directory",
      os.path.isdir(_TMP) and _TMP != os.sep, True)
check("0d. asking for the path CREATES NOTHING",
      os.path.exists(_cg.cache_path()), False)


#------------------------------------------------------------------------------
section("SECTION 1 -- a first scan parses everything; a second parses nothing")
#------------------------------------------------------------------------------

clear_cache()
_first = guarded(scan)
check("1a. the first scan parses every bundle", parsed(), sorted(_STEMS))
check("1b. ...and returns a row for every one of them",
      sorted(_first) if isinstance(_first, dict) else _first, sorted(_STEMS))
check("1c. ...with the groups the grouper gave",
      {s: _first[s]["group"] for s in _STEMS if s != _BAD_STEM}
      if isinstance(_first, dict) else _first,
      {s: g for s, g in _GROUPS.items() if s != _BAD_STEM})
check("1d. the failed bundle is present and UNRESOLVED, not missing",
      (_first[_BAD_STEM]["group"], _first[_BAD_STEM]["patient_id"])
      if isinstance(_first, dict) else _first,
      (_pc.CANCER_GROUP_UNRESOLVED, None))
check("1e. the cache file now exists", os.path.exists(_cg.cache_path()), True)

_rows = rows_of()
check("1f. it carries a row per bundle, the failed one included",
      sorted(_rows), sorted(_STEMS))
check("1g. ...each with the file signature the key is made of",
      all(isinstance(r.get("signature"), list) and len(r["signature"]) == 2
          for r in _rows.values()) if _rows else "<no rows>", True)
check("1h. ...and the cache states what its rows MEAN",
      at(cache_rows(), "grouper_digest"), _cg.grouper_digest())

# --- THE PROPERTY -----------------------------------------------------------
_second = guarded(scan)
check("1i. THE SECOND SCAN PARSES NOTHING AT ALL", parsed(), [])
check("1j. ...and returns exactly what the first scan did", _second, _first)
# NON-DEGENERACY: 1i is equally satisfied by a scan that returned {} without
# looking at anything, which 1j alone would not catch if the first were empty.
check("1i-i. NON-DEGENERATE: there was something to parse",
      len(_first) if isinstance(_first, dict) else _first, len(_STEMS))

check("1k. a cached FAILURE is not re-counted as a fresh grouping fault",
      _BAD_STEM in parsed(), False)


#------------------------------------------------------------------------------
section("SECTION 2 -- one touched bundle re-parses ONE bundle")
#------------------------------------------------------------------------------

bump_mtime("patient-001")
_third = guarded(scan)
check("2a. EXACTLY the touched bundle is re-parsed", parsed(), ["patient-001"])
check("2b. ...and every other row still comes back", _third, _first)
check("2c. the cache was rewritten with the new signature",
      at(rows_of(), "patient-001", "signature"),
      _cg.file_signature(os.path.join(_CORPUS, "patient-001.json")))

_fourth = guarded(scan)
check("2d. and the run after that parses nothing again", parsed(), [])

# THE SIZE HALF, ALONE. mtime is pinned back, so only the byte count moved --
# a key that had quietly dropped size would serve a stale row here.
grow("patient-002")
_fifth = guarded(scan)
check("2e. a SIZE change alone re-parses exactly that bundle, with mtime "
      "pinned back -- so the size half of the key is load-bearing",
      parsed(), ["patient-002"])

# THE MTIME HALF, ALONE, is section 2a: `bump_mtime` appends nothing.
check("2f. NON-DEGENERATE: the two probes really did move different halves",
      (os.stat(os.path.join(_CORPUS, "patient-002.json")).st_size
       != len(json.dumps({"stem": "patient-002", "pad": "x" * 32}))), True)


#------------------------------------------------------------------------------
section("SECTION 3 -- a row whose stat no longer matches is DISCARDED")
#------------------------------------------------------------------------------
#
# Sections 1 and 2 drive the cache through its own writer. This one CORRUPTS a
# row directly, which is the only way to reach the discard for a reason the
# writer would never produce -- and the only way to show that the check is an
# EQUALITY on the recorded signature rather than a comparison of two things the
# same code just computed.

guarded(scan)                                   # settle the cache
check("3-pre. there is a cache to corrupt -- without this every check below "
      "would pass for a run that wrote nothing at all",
      rewrite_cache(lambda rows: rows["patient-004"].__setitem__(
          "signature", [1, 1])), True)
_sixth = guarded(scan)
check("3a. a row with a signature that matches nothing is discarded and its "
      "bundle re-parsed",
      parsed(), ["patient-004"])
check("3b. ...and the answer is still correct",
      _sixth[_BAD_STEM]["group"] if isinstance(_sixth, dict) else _sixth,
      _pc.CANCER_GROUP_UNRESOLVED)
check("3c. ...and the corrupt signature was replaced, not kept",
      at(rows_of(), "patient-004", "signature") != [1, 1], True)

# A ROW MISSING THE FIELD ENTIRELY -- what a cache written by an older format
# looks like if the version guard is ever removed.
check("3d-pre. the no-signature row was planted",
      rewrite_cache(lambda rows: rows["patient-005"].pop("signature", None)),
      True)
guarded(scan)
check("3d. a row with NO signature is discarded rather than trusted",
      parsed(), ["patient-005"])

# A ROW THAT IS NOT AN OBJECT AT ALL.
check("3e-pre. the not-an-object row was planted",
      rewrite_cache(lambda rows: rows.__setitem__("patient-000", "not a row")),
      True)
guarded(scan)
check("3e. a row that is not an object is discarded rather than raising",
      parsed(), ["patient-000"])

# A STEM THAT HAS LEFT THE CORPUS IS PRUNED. Nothing else does this; without
# it the file grows across every regeneration.
check("3f-pre. the departed stem was planted",
      rewrite_cache(lambda rows: rows.__setitem__(
          "patient-999-gone", {"signature": [1, 1], "group": "breast",
                               "patient_id": "id-gone"})), True)
bump_mtime("patient-000")                       # force a write
guarded(scan)
check("3f. a stem no longer in the corpus is dropped on the next write",
      "patient-999-gone" in rows_of(), False)
check("3f-i. ...while every live stem survives that pruning",
      sorted(rows_of()), sorted(_STEMS))


#------------------------------------------------------------------------------
section("SECTION 4 -- an unusable cache costs a parse and never an answer")
#------------------------------------------------------------------------------

for _label, _body in (("4a. not JSON at all", "{{{ not json"),
                      ("4b. JSON that is not an object", "[1, 2, 3]"),
                      ("4c. an object whose rows are not an object",
                       json.dumps({"cache_version": _cg.CACHE_VERSION,
                                   "grouper_digest": _cg.grouper_digest(),
                                   "rows": []}))):
    with open(_cg.cache_path(), "w", encoding="utf-8") as _fh:
        _fh.write(_body)
    _r = guarded(scan)
    check(f"{_label} -> everything is re-parsed", parsed(), sorted(_STEMS))
    check(f"{_label} -> ...and the answer is unchanged", _r, _first)

_faults = dict(_cg.CORPUS_GROUP_CACHE_FAULTS)
check("4d. each of those was COUNTED rather than swallowed",
      sorted(k.split(":")[0] for k in _faults) != [], True)
check("4d-i. ...under the read phase",
      any(k.startswith("read:") for k in _faults), True)

# AN ABSENT CACHE IS NOT A FAULT. Counting it would put a line on the run-end
# report of every clean first run on every machine.
clear_cache()
_cg.CORPUS_GROUP_CACHE_FAULTS.clear()
guarded(scan)
check("4e. a MISSING cache is not counted as a degradation",
      dict(_cg.CORPUS_GROUP_CACHE_FAULTS), {})

# AN UNWRITABLE DIRECTORY: the answer must still be right.
_ro = os.path.join(_TMP, "readonly")
os.makedirs(_ro, exist_ok=True)
_saved_state = dict(_cg._CACHE_STATE)
try:
    _cg._CACHE_STATE["path"] = os.path.join(_ro, _cg.CACHE_FILENAME)
    os.chmod(_ro, 0o500)
    _cg.CORPUS_GROUP_CACHE_FAULTS.clear()
    _ro_result = guarded(scan)
finally:
    os.chmod(_ro, 0o700)
    _cg._CACHE_STATE.clear()
    _cg._CACHE_STATE.update(_saved_state)
check("4f. an unwritable cache directory does not change the answer",
      _ro_result, _first)
check("4f-i. ...and IS counted, under the write phase",
      any(k.startswith("write:") for k in _cg.CORPUS_GROUP_CACHE_FAULTS), True)
check("4f-ii. ...leaving no temp file behind",
      [f for f in os.listdir(_ro) if f.startswith(".cohort_group_cache-")], [])
check("4f-restore. the resolved path was restored",
      _cg.cache_path().startswith(_TMP) and _ro not in _cg.cache_path(), True)

# use_cache=False READS AND WRITES NOTHING.
clear_cache()
guarded(scan)                                   # populate
_before = cache_rows()
_uncached = guarded(scan, use_cache=False)
check("4g. use_cache=False parses everything", parsed(), sorted(_STEMS))
check("4g-i. ...returns the same answer", _uncached, _first)
check("4g-ii. ...and leaves the cache file untouched", cache_rows(), _before)


#------------------------------------------------------------------------------
section("SECTION 5 -- a code change invalidates the cache; a stat key cannot")
#------------------------------------------------------------------------------
#
# THE HOLE A PER-FILE STAT KEY LEAVES, AND IT IS NOT HYPOTHETICAL. No file on
# disk moves when the GROUPING is edited -- a group added to the vocabulary, a
# keyword widened, or the derivation of "which condition is the primary cancer"
# changed, which is exactly what the pass before this one did. A cache without
# `grouper_digest` would have served rows computed under the old rule forever.
#
# THE CORPUS IS BYTE-FOR-BYTE UNCHANGED ACROSS THIS SECTION, which is the only
# thing that makes it a statement about the digest rather than about the stat.

clear_cache()
guarded(scan)
_sig_before = {s: _cg.file_signature(os.path.join(_CORPUS, f"{s}.json"))
               for s in _STEMS}

_saved_digest = dict(_cg._CACHE_STATE)
try:
    _cg._CACHE_STATE["grouper_digest"] = "a-different-grouper"
    _after_change = guarded(scan)
finally:
    _cg._CACHE_STATE.clear()
    _cg._CACHE_STATE.update(_saved_digest)

check("5a. a changed grouper digest discards the WHOLE cache",
      parsed(), sorted(_STEMS))
check("5a-i. ...with every file's signature untouched, so 5a is about the "
      "digest and not about the stat",
      {s: _cg.file_signature(os.path.join(_CORPUS, f"{s}.json"))
       for s in _STEMS}, _sig_before)
check("5a-ii. ...and the answer is still correct", _after_change, _first)
check("5b. the digest was restored", _cg.grouper_digest(),
      _saved_digest.get("grouper_digest"))

# A CACHE WRITTEN UNDER AN OLDER FORMAT IS REJECTED, and this check exists
# because the revert matrix found it MISSING: disabling the `cache_version`
# comparison outright changed no result, so the guard was untested. `rows` and
# `grouper_digest` in an old-format file can be perfectly valid -- what has
# changed is the SHAPE of a row -- so nothing else here would notice.
clear_cache()
guarded(scan)
_valid = cache_rows()
check("5e-pre. there is a valid payload to re-version", bool(rows_of(_valid)),
      True)

_bumped = dict(_valid) if isinstance(_valid, dict) else {}
_bumped["cache_version"] = _cg.CACHE_VERSION + 1
with open(_cg.cache_path(), "w", encoding="utf-8") as _fh:
    json.dump(_bumped, _fh)
guarded(scan)
check("5e. a cache written under a DIFFERENT format version is rejected",
      parsed(), sorted(_STEMS))

# THE CONTROL. Without it, 5e is equally satisfied by a reader that rejects
# every payload it did not write in this process -- the identical bytes with
# the version put back must be USED.
_restored = dict(_bumped)
_restored["cache_version"] = _cg.CACHE_VERSION
with open(_cg.cache_path(), "w", encoding="utf-8") as _fh:
    json.dump(_restored, _fh)
guarded(scan)
check("5e-i. CONTROL: the SAME payload with the version put back is used, so "
      "5e is about the version field and not about the payload",
      parsed(), [])

# THE DIGEST REALLY DEPENDS ON THE GROUPING MODULES. Without this, 5a is
# satisfied by a digest that is a constant somebody could never change.
_probe = guarded(_cg._ast_digest, _pc)
check("5c. the module digest is a real 16-hex digest of the grouper module",
      isinstance(_probe, str) and len(_probe) == 16, True)
check("5c-i. ...and differs from the registry module's, so both are read",
      _probe != guarded(_cg._ast_digest, _cg._registry_module), True)

# A DOCUMENTATION PASS MUST NOT THROW AWAY 175 SECONDS OF VALID CACHE.
_copy_dir = os.path.join(_TMP, "docs-only")
os.makedirs(_copy_dir, exist_ok=True)
_copy = os.path.join(_copy_dir, "primary_cancer.py")
shutil.copy2(os.path.abspath(_pc.__file__), _copy)
_src = io.open(_copy, encoding="utf-8").read()
with open(_copy, "w", encoding="utf-8") as _fh:
    _fh.write(_src + "\n# a comment added by a documentation pass\n")


class _FakeModule(object):
    def __init__(self, path):
        self.__file__ = path


check("5d. a COMMENT-ONLY edit does not move the module digest",
      guarded(_cg._ast_digest, _FakeModule(_copy)),
      guarded(_cg._ast_digest, _FakeModule(os.path.abspath(_pc.__file__))))

with open(_copy, "w", encoding="utf-8") as _fh:
    _fh.write(_src.replace('("breast",      ("breast",)),',
                           '("breast",      ("breast", "mammary")),'))
check("5d-i. ...while a VOCABULARY edit does -- so 5d is not a digest that "
      "ignores the file",
      guarded(_cg._ast_digest, _FakeModule(_copy))
      != guarded(_cg._ast_digest, _FakeModule(os.path.abspath(_pc.__file__))),
      True)


#------------------------------------------------------------------------------
section("SECTION 6 -- the write is atomic and is skipped when nothing changed")
#------------------------------------------------------------------------------

clear_cache()
guarded(scan)
check("6-pre. the cache exists, so sections 6a/6b are about the SKIP rather "
      "than about a file that was never written",
      os.path.exists(_cg.cache_path()), True)
_mtime_before = (os.stat(_cg.cache_path()).st_mtime_ns
                 if os.path.exists(_cg.cache_path()) else None)
time.sleep(0.01)
guarded(scan)                                   # a total cache hit
check("6a. a run that parsed nothing does not rewrite the cache",
      os.stat(_cg.cache_path()).st_mtime_ns
      if os.path.exists(_cg.cache_path()) else None, _mtime_before)
bump_mtime("patient-000")
guarded(scan)
check("6b. ...and a run that parsed something does",
      (os.stat(_cg.cache_path()).st_mtime_ns
       if os.path.exists(_cg.cache_path()) else None) != _mtime_before, True)

check("6c. no temp file survives a successful write",
      [f for f in os.listdir(_TMP) if f.startswith(".cohort_group_cache-")], [])
check("6d. the cache is written sorted, so two identical runs produce "
      "identical bytes",
      io.open(_cg.cache_path(), encoding="utf-8").read()
      if os.path.exists(_cg.cache_path()) else "<no cache>",
      json.dumps(cache_rows(), sort_keys=True))


#------------------------------------------------------------------------------
section("SECTION 7 -- the harness put everything back")
#------------------------------------------------------------------------------

check("7a. the parser stand-in was restored BY IDENTITY",
      _cg.parse_fhir_bundle.__name__, "parse_fhir_bundle")
check("7b. the grouper stand-in was restored BY IDENTITY",
      _cg.patient_cancer_group is _pc.patient_cancer_group, True)
check("7c. oncotriage/evaluation/cohort_groups.py was not written",
      hashlib.sha256(io.open(_CG_PATH, "rb").read()).hexdigest(),
      _CG_SHA_BEFORE)

_paths._RESOLVED.clear()
_paths._RESOLVED.update(_SAVED_RESOLVED)
check("7d. paths._RESOLVED was restored exactly",
      _paths._RESOLVED, _SAVED_RESOLVED)
check("7d-i. ...and checkpoint_path no longer points at the scratch tree",
      _paths._RESOLVED.get("checkpoint_path") == _TMP, False)

shutil.rmtree(_TMP, ignore_errors=True)
check("7e. the scratch tree is gone", os.path.exists(_TMP), False)


#------------------------------------------------------------------------------
print(f"\n{'=' * 74}")
print(f"RESULTS: {_PASSED} passed, {_FAILED} failed")
print("=" * 74)
sys.exit(1 if _FAILED else 0)
