"""ONE CANCER GROUPING VOCABULARY IN THE PACKAGE, AND ONE ALLOCATOR.

WHAT THIS EXISTS TO CATCH, AND IT HAS ALREADY HAPPENED ONCE. Two groupers for
one concept lived in this project for the whole of its life:
``oncotriage/ablation/study.py:_cancer_group_key`` (fifteen anatomical groups)
and ``oncotriage/evaluation/sampling.py:classify_cancer`` (three, plus
"other"). The narrow one was fitted to a retired corpus in which "other" was
one patient; on the corpus this project runs today it is 289 of 1,000, so the
evaluation sampler drew from 71% of the corpus and the calibration pool behind
``config.MEDCPT_SCORE_FLOOR`` was drawn the same way. NOTHING FAILED: a
classifier that answers "other" is not a classifier that errors.

That is the drift defect this project keeps finding -- ``CROSS_ENCODER_MODEL``,
``"Qdrant/bm25"``, ``_LATEST_RUN_PER_CONFIG_SQL``, ``_RUN_HEALTH_CASE_SQL`` --
and the answer is always the same: one owner and a STANDING check that no
second one appears. A grep would not do: the second vocabulary need not use the
first's words.

HOW IT IS PINNED. The keyword table is a module-level literal in exactly one
package file, and it is found BY SHAPE rather than by name -- any module-level
assignment whose value is a literal collection mapping short lower-case group
names onto collections of lower-case keyword strings, and whose keyword set
overlaps the owner's. A second vocabulary spelled ``TUMOUR_SITES`` with
different words is still caught if it partitions cancers the same way, and the
planted control below is exactly that shape under a different name.

WHAT IT DOES NOT COVER, stated rather than implied. A grouper written as an
if/elif chain over ``str.startswith`` carries no collection literal and would
escape this scan. That is a real hole and it is the reason the check is
literal-shaped rather than absent: the hole is narrower than the class, and a
partial pin that names its own limit beats a missing one.

NO NETWORK, NO KEYS, NO SPEND, no live Qdrant, NO MODEL LOAD, no corpus, no
database, no git history, no live server. It writes NOTHING anywhere, not even
a temp directory. NOT in the collision matrix: the package files it reads are
written by neither of the suite's two writers. It EXECS NOTHING and loads no
module by location -- every control is a different INPUT to an ``ast`` walk
over a string, which is the right instrument because the check it controls is
itself static. Bucket A.
"""

import ast
import os
import random
import sys
from collections import defaultdict

os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CODE_DIR = os.path.dirname(_TESTS_DIR)
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from oncotriage.evaluation import cohort as _cohort              # noqa: E402
from oncotriage.evaluation import sampling as _sampling          # noqa: E402
from oncotriage.registries import primary_cancer as _pc          # noqa: E402
import oncotriage as _pkg                                        # noqa: E402

_PKG_DIR = os.path.dirname(os.path.abspath(_pkg.__file__))

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

    A check whose ARGUMENT raises takes the file down with no summary -- the
    abort shape this project has shipped repeatedly. A marker string fails the
    comparison and names what happened.
    """
    try:
        return fn(*a, **kw)
    except Exception as exc:            # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


#------------------------------------------------------------------------------
# THE SCANNER
#------------------------------------------------------------------------------

_OWNER_REL = os.path.join("registries", "primary_cancer.py")

# Every keyword the owner's table carries. A candidate literal is reported only
# if it OVERLAPS this set, so an unrelated string table -- a MeSH tree map, a
# LOINC panel -- is not a finding.
_OWNER_KEYWORDS = {kw for _, kws in _pc.CANCER_GROUP_KEYWORDS for kw in kws}


def _literal(node):
    """``ast.literal_eval`` or None. A non-literal cannot be a static table."""
    try:
        return ast.literal_eval(node)
    except Exception:                   # noqa: BLE001
        return None


def _looks_like_group_table(value) -> bool:
    """Does this literal map group names onto keyword collections?

    Accepts both shapes the two retired vocabularies used: a dict of
    ``{name: [kw, ...]}`` (``CANCER_TYPES``) and a sequence of
    ``(name, [kw, ...])`` pairs (``_cancer_group_key``'s local list).
    """
    if isinstance(value, dict):
        pairs = list(value.items())
    elif isinstance(value, (list, tuple)) and value:
        pairs = []
        for item in value:
            if not (isinstance(item, (list, tuple)) and len(item) == 2):
                return False
            pairs.append((item[0], item[1]))
    else:
        return False
    if not pairs:
        return False
    words = set()
    for name, kws in pairs:
        if not isinstance(name, str) or not name or name != name.lower():
            return False
        if not isinstance(kws, (list, tuple, set, frozenset)) or not kws:
            return False
        for kw in kws:
            if not isinstance(kw, str) or kw != kw.lower():
                return False
            words.add(kw)
    return bool(words & _OWNER_KEYWORDS)


def find_group_tables(source: str, path: str) -> list:
    """Every module-level group-table literal in ``source``, as ``(path, name)``.

    MODULE LEVEL ONLY IS DELIBERATE AND IS A NAMED LIMIT. The retired ablation
    vocabulary was a FUNCTION-LOCAL list, so a scan restricted to module scope
    would have missed the very defect this file is about. It walks every
    ``Assign`` at any depth for that reason.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign):
            continue
        value = _literal(node.value)
        if value is None or not _looks_like_group_table(value):
            continue
        for target in node.targets:
            name = getattr(target, "id", None)
            if name:
                found.append((path, name))
    return found


def scan_package() -> list:
    hits = []
    for root, _dirs, files in os.walk(_PKG_DIR):
        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, _PKG_DIR)
            with open(full, encoding="utf-8") as fh:
                hits.extend(find_group_tables(fh.read(), rel))
    return sorted(hits)


#------------------------------------------------------------------------------
section("SECTION 1 -- exactly one group-table literal in the package")
#------------------------------------------------------------------------------

_hits = guarded(scan_package)
check("1a. exactly one module carries a cancer group table",
      _hits, [(_OWNER_REL, "CANCER_GROUP_KEYWORDS")])

# NON-DEGENERACY: a scanner that matched nothing would satisfy 1a's shape only
# by returning [], which it does not -- but a scanner whose keyword overlap
# test was broken would return [] and 1a would read as "no second vocabulary".
check("1a-i. the scan is non-degenerate -- it found the owner",
      len(_hits) >= 1, True)

section("SECTION 1b -- the scanner catches a planted second vocabulary")

# THE PLANT IS A DIFFERENT NAME AND DIFFERENT WORDS, which is the case a grep
# for `CANCER_TYPES` or for the owner's group names would miss.
_PLANT_DICT = '''
TUMOUR_SITES = {
    "thorax": ["lung", "bronch"],
    "gut":    ["colon", "rectum"],
}
'''
check("1b-a. a dict-shaped second vocabulary is caught",
      guarded(find_group_tables, _PLANT_DICT, "planted.py"),
      [("planted.py", "TUMOUR_SITES")])

# The retired ablation vocabulary's exact shape: a FUNCTION-LOCAL list of pairs.
_PLANT_LOCAL = '''
def classify(display):
    buckets = [
        ("lung",   ["lung", "pulmonary"]),
        ("breast", ["breast"]),
    ]
    for name, kws in buckets:
        if any(k in display for k in kws):
            return name
    return "other"
'''
check("1b-b. a FUNCTION-LOCAL pair list is caught -- the retired shape",
      guarded(find_group_tables, _PLANT_LOCAL, "planted.py"),
      [("planted.py", "buckets")])

# CLEAN CONTROL. Without it the two plants above are equally satisfied by a
# scanner that reports every literal it meets.
_CLEAN = '''
LOINC_PANELS = {"ecog": ["89247-1"], "stage": ["21908-9"]}
GREETINGS = ["hello", "goodbye"]
RRF_WEIGHTS = {"title": 3.0, "dense": 1.0}
def f():
    rows = [("a", 1), ("b", 2)]
    return rows
'''
check("1b-c. CLEAN CONTROL: unrelated literals are not reported",
      guarded(find_group_tables, _CLEAN, "clean.py"), [])

# A table with the right SHAPE and no overlapping keyword is not a finding --
# this is what keeps the check from reporting every string table in the tree.
_PLANT_NO_OVERLAP = '''
DIETS = {"vegan": ["tofu", "lentil"], "keto": ["butter"]}
'''
check("1b-d. a same-shaped table with no cancer keyword is NOT reported",
      guarded(find_group_tables, _PLANT_NO_OVERLAP, "planted.py"), [])


#------------------------------------------------------------------------------
section("SECTION 2 -- the two consumers reach the owner, by IDENTITY")
#------------------------------------------------------------------------------

from oncotriage.ablation import study as _study                  # noqa: E402

check("2a. sampling.classify_cancer IS the owner's function",
      _sampling.classify_cancer is _pc.cancer_group_key, True)
check("2b. study._cancer_group_key IS the owner's function",
      _study._cancer_group_key is _pc.cancer_group_key, True)
check("2c. study._get_patient_group IS the owner's function",
      _study._get_patient_group is _pc.patient_cancer_group, True)

# The retired names are GONE, not merely unused. A surviving `CANCER_TYPES`
# would be a second vocabulary that check 1a cannot see once it is no longer a
# literal collection of the right shape.
check("2d. sampling.CANCER_TYPES is gone",
      hasattr(_sampling, "CANCER_TYPES"), False)
check("2e. sampling.PATIENTS_PER_CANCER is gone",
      hasattr(_sampling, "PATIENTS_PER_CANCER"), False)


#------------------------------------------------------------------------------
section("SECTION 3 -- the vocabulary itself")
#------------------------------------------------------------------------------

check("3a. CANCER_GROUPS is derived from the table plus the two sentinels",
      list(_pc.CANCER_GROUPS),
      [n for n, _ in _pc.CANCER_GROUP_KEYWORDS]
      + [_pc.CANCER_GROUP_OTHER, _pc.CANCER_GROUP_UNRESOLVED])
check("3b. every group name is distinct", len(set(_pc.CANCER_GROUPS)),
      len(_pc.CANCER_GROUPS))
check("3c. the table is a tuple, not a mutable module-level list",
      isinstance(_pc.CANCER_GROUP_KEYWORDS, tuple), True)
check("3d. 'other' and 'unknown' are different values",
      _pc.CANCER_GROUP_OTHER != _pc.CANCER_GROUP_UNRESOLVED, True)

# THE MEASURED DEFECT, AS A CHECK. These three were "other" under the retired
# vocabulary and are 289 of the 1,000-bundle corpus.
for _display, _want in (
        ("Malignant neoplasm of prostate (disorder)", "prostate"),
        ("Multiple myeloma (disorder)", "hematologic"),
        ("Acute myeloid leukemia, disease (disorder)", "hematologic"),
        ("Small cell carcinoma of lung (disorder)", "lung"),
        ("Malignant neoplasm of breast (disorder)", "breast")):
    check(f"3e. {_display[:40]!r} -> {_want}",
          guarded(_pc.cancer_group_key, _display), _want)

check("3f. a display matching nothing is 'other'",
      guarded(_pc.cancer_group_key, "Sprained ankle"), _pc.CANCER_GROUP_OTHER)
check("3g. None is 'other', not a crash and not 'unknown'",
      guarded(_pc.cancer_group_key, None), _pc.CANCER_GROUP_OTHER)
check("3h. ORDER IS LOAD-BEARING: 'small cell lung' resolves to lung",
      guarded(_pc.cancer_group_key, "small cell lung carcinoma"), "lung")


#------------------------------------------------------------------------------
section("SECTION 4 -- one allocator, and it is exact")
#------------------------------------------------------------------------------

_CORPUS = {"colorectal": 405, "breast": 290, "prostate": 237,
           "hematologic": 52, "lung": 16}

_alloc = guarded(_cohort.allocate_proportional, _CORPUS, 500)
check("4a. the allocation sums to exactly the request",
      sum(_alloc.values()) if isinstance(_alloc, dict) else _alloc, 500)
check("4b. no group is asked for more than it holds",
      all(_alloc[g] <= _CORPUS[g] for g in _CORPUS)
      if isinstance(_alloc, dict) else _alloc, True)
check("4c. every non-empty group is represented",
      all(_alloc[g] >= 1 for g in _CORPUS)
      if isinstance(_alloc, dict) else _alloc, True)
check("4d. the measured 500-cohort allocation",
      _alloc, {"colorectal": 203, "breast": 145, "prostate": 118,
               "hematologic": 26, "lung": 8})

_abl = guarded(_cohort.allocate_proportional,
               {"colorectal": 203, "breast": 145, "prostate": 118,
                "hematologic": 26, "lung": 8}, 100)
check("4e. the measured 100-ablation allocation sums to 100",
      sum(_abl.values()) if isinstance(_abl, dict) else _abl, 100)
check("4f. lung survives the second stage at the floor",
      _abl.get("lung") if isinstance(_abl, dict) else _abl, 1)

check("4g. a size at or above the population takes everyone",
      guarded(_cohort.allocate_proportional, _CORPUS, 5000), _CORPUS)
check("4h. size 0 allocates nothing",
      sum(guarded(_cohort.allocate_proportional, _CORPUS, 0).values()), 0)

# THE FLOOR CANNOT ALWAYS BE AFFORDED, and the allocator must not overshoot to
# grant it. Three groups, size 2: two groups get one each, largest first.
_tiny = guarded(_cohort.allocate_proportional,
                {"a": 100, "b": 50, "c": 1}, 2)
check("4i. a size below the group count still sums exactly",
      sum(_tiny.values()) if isinstance(_tiny, dict) else _tiny, 2)
check("4j. ...and the floor goes to the largest groups",
      _tiny, {"a": 1, "b": 1, "c": 0})

# EVERY TIE IS BROKEN ON THE NAME, so the allocation cannot depend on the order
# the caller happened to build its dict.
_fwd = guarded(_cohort.allocate_proportional, {"a": 10, "b": 10, "c": 10}, 4)
_rev = guarded(_cohort.allocate_proportional, {"c": 10, "b": 10, "a": 10}, 4)
check("4k. the allocation is independent of input dict order", _fwd, _rev)
check("4k-i. ...and is non-degenerate (an uneven split really happened)",
      sorted(_fwd.values()) if isinstance(_fwd, dict) else _fwd, [1, 1, 2])

check("4l. a negative population raises rather than allocating",
      str(guarded(_cohort.allocate_proportional, {"a": -1}, 1))[:9], "<RAISED V")
check("4m. a negative size raises",
      str(guarded(_cohort.allocate_proportional, _CORPUS, -1))[:9], "<RAISED V")


#------------------------------------------------------------------------------
section("SECTION 5 -- the stratified draw is reproducible and total")
#------------------------------------------------------------------------------

_POP = [f"p{i:04d}" for i in range(1000)]
_GROUPS = ["colorectal"] * 405 + ["breast"] * 290 + ["prostate"] * 237 \
          + ["hematologic"] * 52 + ["lung"] * 16
_MAP = dict(zip(_POP, _GROUPS))
_g = _MAP.__getitem__

_d1 = guarded(_cohort.stratified_draw, _POP, 500, 42, _g)
_d2 = guarded(_cohort.stratified_draw, list(reversed(_POP)), 500, 42, _g)
check("5a. the same seed and population draw the same set",
      _d1 == _d2, True)
check("5b. ...and that is not vacuous -- it drew 500",
      len(_d1[0]) if isinstance(_d1, tuple) else _d1, 500)
check("5c. the per-group counts are the allocation",
      _d1[1] if isinstance(_d1, tuple) else _d1,
      {"breast": 145, "colorectal": 203, "hematologic": 26, "lung": 8,
       "prostate": 118})
check("5d. a different seed draws a different set",
      guarded(_cohort.stratified_draw, _POP, 500, 43, _g)[0] != _d1[0], True)
check("5e. the result is sorted by stem, not by rank",
      _d1[0] == sorted(_d1[0]) if isinstance(_d1, tuple) else _d1, True)
check("5f. a duplicate stem raises rather than being collapsed",
      str(guarded(_cohort.stratified_draw, _POP + ["p0000"], 10, 42, _g))[:9],
      "<RAISED V")

# EVERY GROUP THE POPULATION HAD IS A KEY, including any that drew zero.
_small = guarded(_cohort.stratified_draw, _POP, 3, 42, _g)
check("5g. group_counts names every population group, zeros included",
      sorted(_small[1]) if isinstance(_small, tuple) else _small,
      ["breast", "colorectal", "hematologic", "lung", "prostate"])
check("5g-i. ...and the zeros are really there",
      sorted(_small[1].values()) if isinstance(_small, tuple) else _small,
      [0, 0, 1, 1, 1])


#------------------------------------------------------------------------------
section("SECTION 6 -- select() records WHICH draw ran")
#------------------------------------------------------------------------------

_FILES = [f"/corpus/{s}.json" for s in _POP]
_by_path = lambda p: _MAP[os.path.splitext(os.path.basename(p))[0]]

_strat = guarded(_cohort.select, _FILES, group_of=_by_path)
_plain = guarded(_cohort.select, _FILES)

check("6a. the stratified selection says it was stratified",
      _strat.stratified, True)
check("6b. the unstratified one says it was not", _plain.stratified, False)
check("6c. the record names the STRATIFIED algorithm",
      _strat.record()["algorithm"], _cohort.STRATIFIED_DRAW_ALGORITHM)
check("6d. ...and the simple-random record names the other one",
      _plain.record()["algorithm"], _cohort.DRAW_ALGORITHM)
check("6e. the two draws are actually different sets",
      _strat.digest != _plain.digest, True)
check("6f. the record carries the cohort's group counts",
      _strat.record()["group_counts"], _strat.group_counts)
check("6g. ...and the corpus's, for comparison",
      _strat.record()["corpus_group_counts"],
      {"colorectal": 405, "breast": 290, "prostate": 237,
       "hematologic": 52, "lung": 16})
check("6h. an unstratified record carries empty group counts",
      (_plain.record()["group_counts"],
       _plain.record()["corpus_group_counts"]), ({}, {}))

# THE SUBSAMPLE REUSES THE COHORT'S GROUPER WITHOUT BEING HANDED ONE.
_sub, _subgroups = guarded(_cohort.CohortSelection.subsample,
                           _strat, 100, 99)
check("6i. subsample draws from the cohort", set(_sub) <= set(_strat.stems), True)
check("6j. ...at the requested size", len(_sub), 100)
check("6k. ...stratified, using the cohort's own grouper",
      sum(_subgroups.values()), 100)
check("6l. ...and reproducibly",
      _cohort.CohortSelection.subsample(_strat, 100, 99)[0], _sub)
check("6m. a subsample of an UNSTRATIFIED cohort reports no groups",
      _cohort.CohortSelection.subsample(_plain, 100, 99)[1], {})

# THE TWO PROGRAMME SAMPLES ARE STILL SIMPLE RANDOM DRAWS FROM THE COHORT.
check("6n. the stability sample is inside the cohort",
      set(_strat.stability_stems) <= set(_strat.stems), True)
check("6o. the judge sample is inside the cohort",
      set(_strat.judge_stems) <= set(_strat.stems), True)
check("6p. the two are not nested -- distinct seeds",
      set(_strat.stability_stems) <= set(_strat.judge_stems), False)

_desc = guarded(_strat.describe)
check("6q. describe() prints a per-group line for every corpus group",
      all(any(f" {g:<13s} " in line for line in _desc)
          for g in _strat.corpus_group_counts), True)


#------------------------------------------------------------------------------
section("SECTION 7 -- the ablation draw cannot empty a guaranteed stratum")
#------------------------------------------------------------------------------
#
# WHAT THIS CATCHES. `oncotriage/ablation/study.py:stratified_sample` used to
# round each group's share INDEPENDENTLY and then TRIM any overshoot by
# shuffling the concatenated sample and truncating. The trim cannot see strata,
# so the single patient the `max(1, ...)` floor granted to a small group was
# exactly as likely to be truncated as any of the two hundred from the largest
# -- and losing it emptied the group, because it was the group's only member.
#
# MEASURED ON THE REAL CORPUS PROPORTIONS, and it is not a corner case: the old
# body overshoots at EVERY sample size tested (6, 8, 10, 12, 15, 20, 30), so
# the trim always ran. At size 6 with the shipped ABLATION_SEED it empties lung
# outright; at size 10, 43 of the first 200 seeds empty at least one stratum.
#
# THE CONTROL IS THE OLD RULE, DRIVEN. It is written out below rather than
# exec'd out of a patched module copy -- this file execs nothing and needs no
# _EXEC_ALLOWLIST entry -- and section 7c verifies the transcription against
# `git show HEAD:` where git history is available, so the control is not merely
# a retyping the author also wrote the assertion for.

_ABL_POP = {"colorectal": 405, "breast": 290, "prostate": 237,
            "hematologic": 52, "lung": 16}


def _fake_patients(populations):
    """One patient dict per member, id-labelled by group.

    `stratified_sample` groups through `_get_patient_group`, which needs a
    registry and a condition list -- so these carry a real SNOMED-shaped
    condition per group and the group is read back off the patient_id prefix,
    which is what the counting below asserts on. The GROUPING is not what
    section 7 measures (sections 2 and 3 do that); the ALLOCATION is.
    """
    out = []
    for group, n in sorted(populations.items()):
        for i in range(n):
            out.append({"patient_id": f"{group}-{i:04d}", "conditions": []})
    return out


def _group_of_fake(patient):
    return patient["patient_id"].rsplit("-", 1)[0]


def _old_stratified_sample(patients, sample_size, seed, group_of):
    """THE RETIRED BODY. Round per group, floor at 1, then shuffle-and-trim.

    Transcribed from `oncotriage/ablation/study.py` as it stood before the
    allocator convergence, with `_get_patient_group(patient, registry)`
    replaced by the injected `group_of` so the control needs no registry.
    Section 7c checks that substitution is the ONLY difference.
    """
    if len(patients) <= sample_size:
        return sorted(patients, key=lambda p: p["patient_id"])
    rng = random.Random(seed)
    cancer_groups = defaultdict(list)
    for patient in patients:
        cancer_groups[group_of(patient)].append(patient)
    total = len(patients)
    sampled = []
    for group_name in sorted(cancer_groups):
        group = cancer_groups[group_name]
        share = max(1, round(len(group) / total * sample_size))
        share = min(share, len(group))
        sampled.extend(rng.sample(group, share))
    if len(sampled) > sample_size:
        trim_rng = random.Random(seed)
        trim_rng.shuffle(sampled)
        sampled = sampled[:sample_size]
    sampled.sort(key=lambda p: p["patient_id"])
    return sampled


def _counts(sample):
    out = {}
    for p in sample:
        g = _group_of_fake(p)
        out[g] = out.get(g, 0) + 1
    return out


_PATIENTS = _fake_patients(_ABL_POP)

# --- 7a THE PLANT: the retired rule, driven, loses the floor ----------------
_old_6 = guarded(_old_stratified_sample, _PATIENTS, 6, 42, _group_of_fake)
_old_6_counts = _counts(_old_6) if isinstance(_old_6, list) else _old_6
check("7a. PLANT: the retired round-and-trim EMPTIES lung at size 6, seed 42",
      _old_6_counts.get("lung", 0) if isinstance(_old_6_counts, dict)
      else _old_6_counts, 0)
check("7a-i. ...while still returning the requested total, so nothing raised "
      "and nothing in the run said a stratum had been lost",
      len(_old_6) if isinstance(_old_6, list) else _old_6, 6)

# --- 7b THE CLEAN CONTROL: the shipped allocator keeps it -------------------
#
# `stratified_sample` reaches its grouper through the module-level
# `_get_patient_group`, so the rebind below is the seam. Restored in a
# `finally` and asserted BY IDENTITY -- any callable of the same name would
# satisfy an equality test.
_saved_group = _study._get_patient_group
_saved_registry = _study.deps.get_cancer_registry
try:
    _study._get_patient_group = lambda patient, registry: _group_of_fake(patient)
    _study.deps.get_cancer_registry = lambda: None
    _new_6 = guarded(_study.stratified_sample, _PATIENTS, 6, 42)
    _new_10 = guarded(_study.stratified_sample, _PATIENTS, 10, 42)
    _new_100 = guarded(_study.stratified_sample, _PATIENTS, 100, 42)
finally:
    _study._get_patient_group = _saved_group
    _study.deps.get_cancer_registry = _saved_registry

check("7b-restore. the grouper seam was restored BY IDENTITY",
      _study._get_patient_group is _saved_group, True)
check("7b-restore-i. ...and so was the registry accessor",
      _study.deps.get_cancer_registry is _saved_registry, True)

_new_6_counts = _counts(_new_6) if isinstance(_new_6, list) else _new_6
check("7b. CLEAN CONTROL: the shipped draw KEEPS lung at size 6, seed 42",
      _new_6_counts.get("lung", 0) if isinstance(_new_6_counts, dict)
      else _new_6_counts, 1)
check("7b-i. ...at exactly the requested total",
      len(_new_6) if isinstance(_new_6, list) else _new_6, 6)
check("7b-ii. ...and every one of the five groups is represented",
      sorted(_new_6_counts) if isinstance(_new_6_counts, dict) else _new_6_counts,
      ["breast", "colorectal", "hematologic", "lung", "prostate"])

# NON-DEGENERACY: 7a and 7b must be about the SAME input. Without this, 7a
# would be satisfied by a control that had silently been handed a population
# with no lung in it at all.
check("7a/7b NON-DEGENERATE: both were handed the same population",
      _ABL_POP["lung"] > 0 and len(_PATIENTS) == sum(_ABL_POP.values()), True)

# --- 7d the shipped draw is exact at every size, so nothing needs trimming ---
_new_10_counts = _counts(_new_10) if isinstance(_new_10, list) else _new_10
check("7d. size 10 hits the target exactly",
      len(_new_10) if isinstance(_new_10, list) else _new_10, 10)
check("7d-i. ...with every group still represented",
      min(_new_10_counts.values()) if isinstance(_new_10_counts, dict)
      else _new_10_counts, 1)
check("7d-ii. ...and a size above the smallest group does not over-draw it",
      _new_10_counts.get("lung", 0) <= _ABL_POP["lung"]
      if isinstance(_new_10_counts, dict) else _new_10_counts, True)
# --- 7g THE INPUT-ORDER FIX, which is a second defect the convergence closed --
#
# `random.Random.sample` reads its population POSITIONALLY, and `patients`
# arrives in whatever order `load_all_patients` globbed -- so before the
# per-group sort the study drawn on two machines holding the same corpus could
# differ. Determinism is a stated property of this pipeline and this was a hole
# in it. Driven by shuffling the input, which is exactly what a different
# filesystem order is.
_shuffled = list(_PATIENTS)
random.Random(7).shuffle(_shuffled)
_saved_group2 = _study._get_patient_group
_saved_registry2 = _study.deps.get_cancer_registry
try:
    _study._get_patient_group = lambda patient, registry: _group_of_fake(patient)
    _study.deps.get_cancer_registry = lambda: None
    _shuf_10 = guarded(_study.stratified_sample, _shuffled, 10, 42)
    _old_shuf_10 = guarded(_old_stratified_sample, _shuffled, 10, 42,
                           _group_of_fake)
    _old_ord_10 = guarded(_old_stratified_sample, _PATIENTS, 10, 42,
                          _group_of_fake)
finally:
    _study._get_patient_group = _saved_group2
    _study.deps.get_cancer_registry = _saved_registry2
check("7g-restore. the grouper seam was restored BY IDENTITY",
      _study._get_patient_group is _saved_group2, True)
check("7g. the shipped draw is invariant to input order",
      [p["patient_id"] for p in _shuf_10] if isinstance(_shuf_10, list)
      else _shuf_10,
      [p["patient_id"] for p in _new_10] if isinstance(_new_10, list)
      else _new_10)
# NON-DEGENERACY: without this, 7g is satisfied by a draw that ignores its
# input entirely. The RETIRED body is shown to be order-DEPENDENT on the same
# two inputs, so 7g measures the sort rather than a coincidence.
check("7g-i. NON-DEGENERATE: the retired body drew a DIFFERENT sample from "
      "the same corpus in a different order",
      ([p["patient_id"] for p in _old_shuf_10]
       == [p["patient_id"] for p in _old_ord_10])
      if isinstance(_old_shuf_10, list) and isinstance(_old_ord_10, list)
      else "<not driven>", False)
check("7g-ii. ...and the shuffle really changed the input order",
      [p["patient_id"] for p in _shuffled] != [p["patient_id"] for p in _PATIENTS],
      True)

check("7e. size 100 hits the target exactly",
      len(_new_100) if isinstance(_new_100, list) else _new_100, 100)

# THE RETIRED RULE OVERSHOOTS AT EVERY SIZE, which is what made the trim
# unconditional. Driven rather than asserted from the docstring.
_overshoots = []
for _size in (6, 8, 10, 12, 15, 20, 30):
    _ideal = {g: max(1, round(n / sum(_ABL_POP.values()) * _size))
              for g, n in _ABL_POP.items()}
    _overshoots.append(sum(_ideal.values()) - _size)
check("7f. the retired rule overshot at EVERY tested size, so the trim "
      "was unconditional rather than a corner case",
      all(o > 0 for o in _overshoots), True)
check("7f-i. ...and the shipped allocator overshoots at none of them",
      [sum(_cohort.allocate_proportional(_ABL_POP, n).values()) - n
       for n in (6, 8, 10, 12, 15, 20, 30)], [0] * 7)

# --- 7c the transcription is verified against git, where git exists ---------
#
# A CONTROL THE AUTHOR ALSO WROTE THE ASSERTION FOR IS A CONTROL THAT TESTS THE
# TRANSCRIPTION. This lifts the pre-convergence `stratified_sample` out of
# `git show HEAD:` and compares its body with the local copy's, tolerating
# exactly the one substitution the docstring declares. A tree with no `.git`
# RECORDS that it could not check rather than failing or aborting -- this file
# is bucket A and must not acquire a git dependency.

def _git_body(rev_path):
    import subprocess
    try:
        out = subprocess.run(["git", "show", rev_path], cwd=_CODE_DIR,
                             capture_output=True, text=True, timeout=30)
    except Exception as exc:                    # noqa: BLE001
        return f"<NO GIT: {type(exc).__name__}>"
    if out.returncode != 0:
        return "<NO GIT: git show failed>"
    for node in ast.walk(ast.parse(out.stdout)):
        if isinstance(node, ast.FunctionDef) and node.name == "stratified_sample":
            return node
    return "<NOT FOUND at that revision>"


def _normalise(fn_node):
    """The ALLOCATION, as an unparsed statement sequence.

    WHAT IS STRIPPED AND WHY THE SCOPE IS DECLARED RATHER THAN IMPLIED. The
    retired body ended with a per-group console report -- an `sampled_ids` set
    comprehension, a loop and three `console.out` calls -- which decides
    nothing and which the control has no need of. So the comparison is cut at
    `sampled.sort(...)`, INCLUSIVE: everything that decides which patients are
    returned is compared, and the reporting is not.

        THE CUT IS ANCHORED ON A STATEMENT, NOT ON A COUNT. Anchoring on "the
        first N statements" would silently compare less the moment a statement
        was inserted, which is the shape that makes a scan report fewer
        findings and read like a clean result. If the anchor is not found the
        whole body is returned, so a rename fails the comparison loudly rather
        than passing over a truncated one.

    Docstrings and any remaining bare `console.out` are dropped; those are the
    two declared differences between a shipped function and a control that has
    no console.
    """
    class _StripConsole(ast.NodeTransformer):
        """Drop every `*.out(...)` statement AT ANY DEPTH.

        A TOP-LEVEL-ONLY STRIPPER WAS THE FIRST VERSION AND IT WAS WRONG: the
        retired body's early-return branch prints before returning, so one
        `console.out` sat NESTED inside an `if` and survived, and the
        comparison failed for a difference the control had correctly declared.
        Found by running it.
        """

        def visit_Expr(self, node):               # noqa: N802 -- ast API
            if (isinstance(node.value, ast.Call)
                    and getattr(node.value.func, "attr", None) == "out"):
                return None
            return node

    stripped = _StripConsole().visit(
        ast.parse(ast.unparse(fn_node)).body[0])
    kept = []
    for n in stripped.body:
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant):
            continue                              # docstring
        kept.append(n)
        if ast.unparse(n).startswith("sampled.sort("):
            break                                 # the cut: allocation ends here
    return "\n".join(ast.unparse(n) for n in kept)


_git_fn = _git_body("HEAD:oncotriage/ablation/study.py")
if isinstance(_git_fn, str):
    check(f"7c. the git transcription check could not run ({_git_fn}) -- "
          f"RECORDED, not skipped silently. The DRIVE above is unaffected.",
          _git_fn.startswith("<"), True)
else:
    _local_fn = None
    for _n in ast.walk(ast.parse(open(os.path.abspath(__file__),
                                      encoding="utf-8").read())):
        if isinstance(_n, ast.FunctionDef) and _n.name == "_old_stratified_sample":
            _local_fn = _n
    check("7c-found. the local control was located", _local_fn is not None, True)
    _git_src = _normalise(_git_fn) if _local_fn is not None else ""
    _loc_src = _normalise(_local_fn) if _local_fn is not None else ""
    # THE ONE DECLARED SUBSTITUTION, applied to the git side so the comparison
    # is equality rather than equality-with-a-tolerance.
    _git_src = (_git_src
                .replace("registry = deps.get_cancer_registry()\n", "")
                .replace("_get_patient_group(patient, registry)",
                         "group_of(patient)"))
    check("7c. the control reproduces the retired body exactly, modulo the "
          "one declared substitution (the injected grouper)",
          _loc_src, _git_src)
    check("7c-i. ...and that comparison is non-degenerate (both are non-empty)",
          bool(_git_src) and bool(_loc_src), True)


#------------------------------------------------------------------------------
section("SECTION 8 -- ONE definition of which condition is the primary cancer")
#------------------------------------------------------------------------------
#
# WHAT THIS CATCHES. `patient_cancer_group` was MOVED byte-for-byte out of the
# ablation study, and the byte-for-byte move preserved a second, disagreeing
# definition of "the primary cancer": it applied no `verification_status`
# filter, so a REFUTED cancer could decide a patient's group, while
# `_resolve_primary_cancer_condition` -- the derivation the PIPELINE stages,
# expands and records `inferences.primary_condition` on -- drops refuted
# conditions first. A patient could be staged by one condition and sampled by
# another, with nothing failing when the two disagreed.
#
# MEASURED: 0 of the 1,000 corpus patients change group, and the cohort digest
# is byte-identical. The census that explains that was RUN: 53,040 conditions,
# every one confirmed or unconfirmed, ZERO refuted, ZERO entered-in-error, ZERO
# patients with conditions and no cancer. Neither separating input exists in
# this corpus -- so THESE CONSTRUCTED PATIENTS ARE THE ONLY THING IN THE
# PROJECT THAT CAN EXERCISE THE FIX, which is why the section is here rather
# than being a corpus measurement in a bucket-E file.

_REFUTED = {"display": "Malignant neoplasm of breast (disorder)",
            "code": "254837009", "system": "http://snomed.info/sct",
            "verification_status": "refuted"}
_CONFIRMED = {"display": "Malignant neoplasm of prostate (disorder)",
              "code": "399068003", "system": "http://snomed.info/sct",
              "verification_status": "confirmed"}
_NON_CANCER = {"display": "Sprained ankle", "code": "44465007",
               "system": "http://snomed.info/sct",
               "verification_status": "confirmed"}

check("8a. a REFUTED cancer does not decide the group -- the confirmed one does",
      guarded(_pc.patient_cancer_group,
              {"conditions": [_REFUTED, _CONFIRMED]}), "prostate")
# THE DISCRIMINATING CONTROL, and the first version of it was WRONG in a way
# that taught something about the resolver. It asserted that a refuted cancer
# ALONE answers UNRESOLVED; it answers "breast", because
# `_resolve_primary_cancer_condition`'s step 1 falls back to the unfiltered
# list when the verification filter empties it ("fallback: use all if filter
# empties list"). That arm is the RESOLVER's, it is documented there, and this
# grouper now INHERITS it -- which is what one definition means. It is checked
# as inherited behaviour below rather than asserted away.
#
# What discriminates instead is the SAME TWO CONDITIONS with the refuted flag
# flipped: if the filter did nothing, both inputs would resolve identically.
_CONFIRMED_BREAST = dict(_REFUTED, verification_status="confirmed")
check("8a-i. flipping the refuted flag on the SAME pair changes the answer, "
      "so 8a is about the FILTER and not about which display sorted first",
      guarded(_pc.patient_cancer_group,
              {"conditions": [_CONFIRMED_BREAST, _CONFIRMED]}), "breast")
check("8a-ii. ...and the confirmed condition alone answers its own group, so "
      "neither answer is hardcoded",
      guarded(_pc.patient_cancer_group, {"conditions": [_CONFIRMED]}),
      "prostate")
check("8a-iii. INHERITED, NOT RE-DECIDED: when EVERY condition is refuted the "
      "resolver's own 'use all if the filter empties the list' arm fires, and "
      "this grouper follows it rather than second-guessing it",
      guarded(_pc.patient_cancer_group, {"conditions": [_REFUTED]}), "breast")

check("8b. no conditions at all is UNRESOLVED",
      guarded(_pc.patient_cancer_group, {"conditions": []}),
      _pc.CANCER_GROUP_UNRESOLVED)
# THE RESOLVER'S FALLBACK ARM. It returns the first VALID condition when no
# cancer is found, so `inferences.primary_condition` records something for a
# non-cancer patient. Grouping on that display would put a patient with NO
# cancer into "other" -- "we found their cancer and it is not one of the
# fifteen" -- which is the conflation CANCER_GROUP_UNRESOLVED exists to
# prevent. The predicate is asked of the resolver's ANSWER, so there is still
# one selection.
check("8c. a non-cancer condition is UNRESOLVED and NOT 'other' -- the "
      "resolver's fallback arm is detected rather than grouped",
      guarded(_pc.patient_cancer_group, {"conditions": [_NON_CANCER]}),
      _pc.CANCER_GROUP_UNRESOLVED)
check("8c-i. NON-DEGENERATE: 'other' is reachable at all, so 8c is a "
      "statement about this input rather than about a dead branch",
      guarded(_pc.cancer_group_key, "Malignant neoplasm of the spleen"),
      _pc.CANCER_GROUP_OTHER)

# THE STRUCTURAL HALF. A behavioural check cannot see a grouper that reproduces
# the resolver's answers today by walking the conditions itself -- which is
# what the retired body did, correctly, until the resolver changed underneath
# it. This requires the ONE derivation to be CALLED.
_pcg_src = ast.unparse(
    [n for n in ast.walk(ast.parse(open(os.path.abspath(_pc.__file__),
                                        encoding="utf-8").read()))
     if isinstance(n, ast.FunctionDef)
     and n.name == "patient_cancer_group"][0])
check("8d. patient_cancer_group CALLS the resolver rather than re-deriving",
      "_resolve_primary_cancer_condition(" in _pcg_src, True)
check("8d-i. ...and does not walk the condition list itself",
      any(tok in _pcg_src for tok in ("sort_key", "for c in", "sorted(")), False)


#------------------------------------------------------------------------------
print(f"\n{'=' * 74}")
print(f"RESULTS: {_PASSED} passed, {_FAILED} failed")
print("=" * 74)
sys.exit(1 if _FAILED else 0)
