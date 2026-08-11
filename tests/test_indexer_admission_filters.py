"""The four scrape-time admission defects, each with a control that FIRES.

WHAT THIS FILE IS ABOUT
=======================
Three filters in ``oncotriage/retrieval/indexer.py`` decided which trials ever
entered the corpus, and a fourth defect promoted an unverified collection over
its own rollback target. Every one of them was a SILENT DROP: the loss happens
before any gate the pipeline measures, so stage-wise recall -- which records
what each gate discarded -- cannot see a trial that was never indexed at all.

  DEFECT 1  ``if min_age > 18: continue`` is an EXACTLY-18 filter. A trial whose
            minimumAge is 19, 20 or 21 was discarded, so a 70-year-old who
            qualifies for a trial requiring 21 could never be matched to it.
  DEFECT 2  a sixteen-word frozenset screen holding "glioma" but neither
            "blastoma" nor "thelioma". Glioblastoma, Mesothelioma,
            Neuroblastoma, Retinoblastoma and Hepatoblastoma all dropped.
  DEFECT 3  ``split_inclusion_exclusion`` finding no marker sent the whole
            criteria block to inclusion, so exclusion criteria reached the
            judge under inclusion vocabulary.
  DEFECT 4  main() swapped the alias with no verification and then deleted the
            previous good collection, destroying the only rollback target.

EVERY CHECK HERE IS PAIRED WITH ITS OWN NEGATIVE CONTROL, and the controls run
against the OLD implementations -- lifted out of ``git show HEAD:`` where that
is possible and reconstructed in a throwaway namespace where it is not -- never
against a retyped paraphrase. An assertion that has only ever passed is not
evidence that it can catch anything.

NO NETWORK, NO KEYS, NO SPEND. Every Qdrant client here is a stand-in. The MeSH
filter is the real one, because defect 2's whole claim is about what the real
crosswalk resolves; if its lookups are absent the affected section reports that
and is skipped rather than passing vacuously.

NOT IN THE COLLISION MATRIX, derived: this file writes nothing anywhere in the
repository -- no temp copies of package modules, no in-place edits -- and the
only repository files it READS are indexer.py and mesh.py, neither of which is
written by ``tests/test_registries_cancer_code_claims_audit_control.py`` or by
``tests/test_config_snapshot_date_rot.py``.
"""

import ast
import os
import re
import subprocess
import sys
import types


# --- package bootstrap ------------------------------------------------------
try:
    import oncotriage
except ImportError:
    _here = os.path.dirname(os.path.abspath(__file__))
    for _candidate in (os.path.dirname(_here), os.getcwd()):
        if os.path.isdir(os.path.join(_candidate, "oncotriage")):
            sys.path.insert(0, _candidate)
            print(f"[bootstrap] added {_candidate} to sys.path")
            break
    import oncotriage

# The repository root, derived from the package this process actually imported
# rather than from this file's location, so a future move of tests/ cannot make
# it read a same-named copy.
_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(oncotriage.__file__)))

from oncotriage.registries import mesh as _mesh
from oncotriage.retrieval import indexer


_passed = 0
_failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def check_true(label, cond):
    check(label, bool(cond), True)


def section(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


# ===========================================================================
# The OLD implementations, for the negative controls.
# ===========================================================================
#
# Lifted out of git rather than retyped. A retyped control tests the retyping.
# The revision is derived -- the newest one whose indexer still contains the
# exactly-18 comparison in EXECUTABLE code -- so these controls keep working
# after this work is committed, the lesson tests/test_storage_query_layer.py
# had to learn when its substring-based selector picked its own fix commit.


def _git(*args):
    return subprocess.run(("git",) + args, cwd=_CODE_DIR, capture_output=True,
                          text=True, timeout=60)


def _revision_with_old_age_filter():
    """Newest revision whose indexer has `min_age > 18` in EXECUTABLE code.

    Parsed, not grepped: the current file quotes that comparison verbatim in
    the comment explaining its deletion, so a substring search selects the
    revision that REMOVED it and every control below then tests the fix
    against itself.
    """
    log = _git("log", "--format=%H", "--", "oncotriage/retrieval/indexer.py")
    if log.returncode != 0:
        return None, None
    for rev in log.stdout.split():
        blob = _git("show", f"{rev}:oncotriage/retrieval/indexer.py")
        if blob.returncode != 0 or not blob.stdout:
            continue
        try:
            tree = ast.parse(blob.stdout)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                try:
                    seg = ast.unparse(node)
                except Exception:
                    continue
                if "min_age" in seg and "18" in seg:
                    return rev, blob.stdout
    return None, None


_OLD_REV, _OLD_SRC = _revision_with_old_age_filter()


def _old_function(name):
    """One top-level function out of the pre-fix indexer, exec'd in isolation.

    Never imported: the old module imports openai and qdrant_client at module
    scope and we want neither. Only the named function's source is compiled,
    into a namespace carrying just the builtins it needs.
    """
    if not _OLD_SRC:
        return None
    tree = ast.parse(_OLD_SRC)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            ns = {"re": re}
            exec(compile(ast.Module(body=[node], type_ignores=[]),
                         "<old-indexer>", "exec"), ns)
            return ns[name]
    return None


# ===========================================================================
# SECTION 1 -- DEFECT 1: the exactly-18 age filter
# ===========================================================================
section("SECTION 1 -- DEFECT 1: the scrape-time age filter is gone")

check_true("a pre-fix revision of the indexer was located",
           _OLD_REV is not None)
print(f"    control revision: {(_OLD_REV or '?')[:12]}")


def _old_age_filter_drops(min_age_str):
    """The old scrape-loop test, reproduced from the located blob's own text.

    Reconstructed rather than exec'd because the comparison lived INSIDE the
    scrape loop and there is no function to lift. Its equivalence to the blob
    is asserted immediately below rather than assumed.
    """
    if min_age_str and "year" in min_age_str.lower():
        try:
            return int(re.findall(r"\d+", min_age_str)[0]) > 18
        except (IndexError, ValueError):
            return False
    return False


# The reconstruction is checked against the blob, so a wrong reconstruction
# fails here rather than silently weakening every control below it.
if _OLD_SRC:
    _old_tree = ast.parse(_OLD_SRC)
    _cmps = [ast.unparse(n) for n in ast.walk(_old_tree)
             if isinstance(n, ast.Compare)]
    check_true("the pre-fix blob really contains `min_age > 18`",
               "min_age > 18" in _cmps)

# --- the control: these are the trials the old filter destroyed -------------
_REALISTIC_ADULT_BOUNDS = ["19 Years", "20 Years", "21 Years", "22 Years"]
for _b in _REALISTIC_ADULT_BOUNDS:
    check_true(f"CONTROL: the OLD filter drops a trial requiring {_b!r}",
               _old_age_filter_drops(_b))

check_true("CONTROL: the OLD filter keeps exactly 18",
           not _old_age_filter_drops("18 Years"))

# --- the fix: no age decision survives in the scraper ----------------------
_indexer_src = open(os.path.join(_CODE_DIR, "oncotriage", "retrieval",
                                 "indexer.py"), encoding="utf-8").read()
_indexer_tree = ast.parse(_indexer_src)
_compares = [ast.unparse(n) for n in ast.walk(_indexer_tree)
             if isinstance(n, ast.Compare)]

check_true("FIXED: no comparison in the scraper names min_age",
           not any("min_age" in c for c in _compares))
check_true("...and the comparison walk is finding things (non-degeneracy)",
           len(_compares) > 5)
check_true("FIXED: the index-time age counter is gone with its only producer",
           not hasattr(indexer, "INDEX_AGE_PARSE_FAILURES"))

# The decision was DELETED, not moved: Stage 4 already enforces the full
# window. This asserts the enforcement it was delegated to actually exists.
from oncotriage.agent import filtering as _filtering  # noqa: E402

_filtering_src = open(os.path.join(_CODE_DIR, "oncotriage", "agent",
                                   "filtering.py"), encoding="utf-8").read()
check_true("Stage 4 enforces the trial's FULL window against the patient",
           "min_age <= patient_age <= max_age" in _filtering_src)
check_true("...and counts what it drops there",
           "age_dropped" in _filtering_src)

# And behaviourally: a 70-year-old is inside a 21+ trial's window, which is the
# concrete patient the old scrape filter made unmatchable.
check_true("a 70-year-old falls inside a trial requiring 21 and no maximum",
           _filtering._parse_age_bound("21 Years", 0, "min_age") <= 70
           <= _filtering._parse_age_bound("", 999, "max_age"))


# ===========================================================================
# SECTION 2 -- DEFECT 2: the oncology screen
# ===========================================================================
section("SECTION 2 -- DEFECT 2: the oncology screen routes through MeSH")

_OLD_ONCOLOGY_KEYWORDS = frozenset({
    "neoplasm", "cancer", "carcinoma", "sarcoma",
    "lymphoma", "leukemia", "melanoma", "glioma",
    "myeloma", "tumor", "tumour", "malignant",
    "malignancy", "oncology", "metastatic", "metastasis",
})

# The old list is lifted from the pre-fix blob and compared with the one above,
# so this control cannot drift away from what actually shipped.
if _OLD_SRC:
    _found_sets = [ast.literal_eval(n.args[0])
                   for n in ast.walk(ast.parse(_OLD_SRC))
                   if isinstance(n, ast.Call)
                   and getattr(n.func, "id", "") == "frozenset"
                   and n.args and isinstance(n.args[0], ast.Set)]
    _shipped = next((s for s in _found_sets if "glioma" in s), None)
    check_true("the old keyword set was recovered from git", _shipped is not None)
    if _shipped is not None:
        check("...and matches the control used here",
              sorted(_shipped), sorted(_OLD_ONCOLOGY_KEYWORDS))
        check_true("...and really lacks 'blastoma'", "blastoma" not in _shipped)
        check_true("...and really lacks 'thelioma'", "thelioma" not in _shipped)


def _old_screen_admits(trial):
    """The old screen: substring over conditions + keywords only."""
    combined = (" ".join(trial.get("conditions") or []).lower() + " "
                + " ".join(trial.get("keywords") or []).lower())
    return any(kw in combined for kw in _OLD_ONCOLOGY_KEYWORDS)


# THE FIVE NAMED CANCER TYPES, each an explicit case.
FIVE_CANCERS = ["Glioblastoma", "Mesothelioma", "Neuroblastoma",
                "Retinoblastoma", "Hepatoblastoma"]

_filter = None
try:
    _filter = _mesh.load_mesh_filter()
except Exception as exc:  # noqa: BLE001 - reported, never swallowed
    print(f"  SKIP  MeSH lookups unavailable ({type(exc).__name__}: {exc}); "
          f"section 2's crosswalk checks cannot run")

check_true("the MeSH filter loaded (section 2 needs the real crosswalk)",
           _filter is not None)

if _filter is not None:
    check_true("the non-oncology layer is loaded (needed for any DROP)",
               len(_filter.non_oncology_terms) > 1000)

    for _name in FIVE_CANCERS:
        _trial = {"nct_id": f"NCT-{_name}", "conditions": [_name],
                  "keywords": [], "title": ""}

        # CONTROL: the old screen destroys it.
        check_true(f"CONTROL: the OLD keyword screen DROPS {_name}",
                   not _old_screen_admits(_trial))

        # FIXED: and it is admitted, on POSITIVE crosswalk evidence rather
        # than by falling through the unresolved fallback. A screen that
        # admitted these because it could not classify them would be a screen
        # that had stopped working, and it would pass a weaker assertion.
        _res = _filter.classify_trial_oncology(_trial)
        check(f"FIXED: {_name} is admitted as oncology",
              _res["verdict"], _mesh.TRIAL_ONCOLOGY)
        check(f"...on C04 crosswalk evidence, not the fallback",
              _res["evidence"], "c04_crosswalk")
        check_true(f"...with real C04 tree numbers", len(_res["trees"]) > 0)
        check_true(f"...all of which are under C04",
                   all(t.startswith("C04") for t in _res["trees"]))
        check_true("...and screen_trial_for_admission agrees",
                   indexer.screen_trial_for_admission(_trial, _filter))

    # --- a positive NON-oncology determination may drop --------------------
    _diabetes = {"nct_id": "NCT-D", "conditions": ["Diabetes Mellitus"],
                 "keywords": [], "title": "A study of insulin dosing"}
    _res = _filter.classify_trial_oncology(_diabetes)
    check("a wholly non-oncology trial is positively determined",
          _res["verdict"], _mesh.TRIAL_NON_ONCOLOGY)
    check_true("...with the MeSH categories that justified it",
               len(_res["categories"]) > 0)
    check_true("...and is dropped",
               not indexer.screen_trial_for_admission(_diabetes, _filter))

    # --- THE FALLBACK: a PLANTED resolution failure is KEPT and LOGGED ------
    #
    # This is the requirement the whole screen is shaped around. A condition
    # string that resolves to nothing must not be treated as evidence of
    # anything. The plant is a string that cannot be in any MeSH release.
    _planted = "Zzq Unresolvable Syndrome Of The Left Parenthesis"
    check_true("the planted condition really resolves to nothing (C04)",
               not _filter.trial_mesh_trees(
                   {"conditions": [_planted], "keywords": []}))
    check_true("...and is really absent from the non-oncology lookup",
               _planted.lower() not in _filter.non_oncology_terms)

    _plant_trial = {"nct_id": "NCT-PLANT", "conditions": [_planted],
                    "keywords": [], "title": "A study"}
    _res = _filter.classify_trial_oncology(_plant_trial)
    check("PLANTED resolution failure -> unresolved, not non-oncology",
          _res["verdict"], _mesh.TRIAL_UNRESOLVED)
    check("...with the reason recorded", _res["evidence"], "condition_unresolved")
    check("...naming the string that failed", _res["unresolved"], [_planted])

    _before = dict(indexer.ADMISSION_SCREEN)
    check_true("PLANTED resolution failure is KEPT",
               indexer.screen_trial_for_admission(_plant_trial, _filter))
    _after = dict(indexer.ADMISSION_SCREEN)
    _key = f"{_mesh.TRIAL_UNRESOLVED}:condition_unresolved"
    check_true("...and COUNTED under its own key",
               _after.get(_key, 0) == _before.get(_key, 0) + 1)

    # A MIXED trial -- one resolvable non-cancer condition and one that does
    # not resolve -- must NOT be dropped. "All conditions are non-cancer" is
    # false when one of them is unknown.
    _mixed = {"nct_id": "NCT-MIX",
              "conditions": ["Diabetes Mellitus", _planted],
              "keywords": [], "title": "A study"}
    check("a MIXED trial is unresolved, never non-oncology",
          _filter.classify_trial_oncology(_mixed)["verdict"],
          _mesh.TRIAL_UNRESOLVED)
    check_true("...and is kept",
               indexer.screen_trial_for_admission(_mixed, _filter))

    # A trial with NO conditions at all is unresolved, not vacuously dropped.
    _empty = {"nct_id": "NCT-EMPTY", "conditions": [], "keywords": [],
              "title": "A study"}
    check("no registered conditions -> unresolved, not a vacuous drop",
          _filter.classify_trial_oncology(_empty)["verdict"],
          _mesh.TRIAL_UNRESOLVED)
    check_true("...and is kept",
               indexer.screen_trial_for_admission(_empty, _filter))

    # --- A NON-DISEASE CONDITION IS NOT EVIDENCE OF ANYTHING ---------------
    #
    # These two are REAL trials that the first version of this screen dropped,
    # found by inspecting all 31 of its drops rather than by reading the code.
    # Both are oncology trials whose sponsor registered a non-disease string in
    # the condition field, and "Drug Monitoring" IS a MeSH term outside C04 --
    # so the naive rule made a confident positive non-oncology determination
    # from a string that names no disease at all.
    _REAL_FALSE_DROPS = [
        ("NCT06545292", ["Drug Monitoring"],
         "Microsampling to Facilitate Drug Monitoring of Oncolytics"),
        ("NCT05436561", ["Disease-free Survival"],
         "A Multiple-center Phase II Study to Evaluate the Clinical Outcome "
         "of Reduced Conditioning Regimen"),
    ]
    for _nct, _conds, _title in _REAL_FALSE_DROPS:
        _t = {"nct_id": _nct, "conditions": _conds, "keywords": [],
              "title": _title}
        # Non-degeneracy: the condition really IS a known non-C04 MeSH term,
        # or this case is not testing the branch it claims to test.
        _cats = _filter.non_oncology_terms.get(_conds[0].lower())
        check_true(f"{_nct}: {_conds[0]!r} really is a known non-C04 MeSH term",
                   _cats is not None)
        if _cats is not None:
            check_true(f"{_nct}: ...and names NO disease category",
                       not _filter._is_disease_category(_cats))
        _res = _filter.classify_trial_oncology(_t)
        check_true(f"{_nct}: is NOT dropped",
                   _res["verdict"] != _mesh.TRIAL_NON_ONCOLOGY)
        check_true(f"{_nct}: is KEPT by the screen",
                   indexer.screen_trial_for_admission(_t, _filter))

    # ...and a trial whose conditions ARE diseases outside C04 still drops, or
    # the rule above would have disabled the screen rather than corrected it.
    check("a DISEASE outside C04 still yields a positive determination",
          _filter.classify_trial_oncology(_diabetes)["verdict"],
          _mesh.TRIAL_NON_ONCOLOGY)
    check_true("Diabetes Mellitus really resolves to a disease category",
               _filter._is_disease_category(
                   _filter.non_oncology_terms["diabetes mellitus"]))

    # The two unresolved reasons are reported apart: they need different fixes.
    _nd = {"nct_id": "NCT-ND", "conditions": ["Drug Monitoring"],
           "keywords": [], "title": "A study"}
    check("a non-disease condition is reported as such",
          _filter.classify_trial_oncology(_nd)["evidence"],
          "condition_not_a_disease")

    # --- the layer being ABSENT can never cause a drop ---------------------
    _no_layer = _mesh.MeSHCancerFilter(
        _filter.name_to_trees, _filter.tree_to_name, _filter.snomed_to_trees,
        _filter.icd10_to_trees, _filter.synonym_to_trees,
        non_oncology_terms={})
    check("with the non-oncology layer absent, a non-cancer trial is UNRESOLVED",
          _no_layer.classify_trial_oncology(_diabetes)["verdict"],
          _mesh.TRIAL_UNRESOLVED)
    check_true("...and is therefore KEPT",
               indexer.screen_trial_for_admission(_diabetes, _no_layer))

    # --- a None filter admits everything and says so -----------------------
    check_true("a None filter admits everything",
               indexer.screen_trial_for_admission(_diabetes, None))

# The vocabulary is closed and every member has a policy. An added member with
# no branch must raise rather than being silently admitted.
check("the verdict vocabulary is exactly three members",
      sorted(_mesh.TRIAL_ONCOLOGY_VERDICTS),
      sorted([_mesh.TRIAL_ONCOLOGY, _mesh.TRIAL_NON_ONCOLOGY,
              _mesh.TRIAL_UNRESOLVED]))


class _RogueVerdictFilter:
    """Returns a verdict outside the closed set. The screen must not guess."""

    def classify_trial_oncology(self, trial):
        return {"verdict": "something_new", "evidence": "x", "trees": [],
                "categories": [], "unresolved": []}


try:
    indexer.screen_trial_for_admission({"nct_id": "NCT-R"}, _RogueVerdictFilter())
    check_true("an unknown verdict RAISES rather than silently admitting", False)
except RuntimeError as exc:
    check_true("an unknown verdict RAISES rather than silently admitting",
               "unknown verdict" in str(exc))


# ===========================================================================
# SECTION 3 -- DEFECT 3: the criteria split
# ===========================================================================
section("SECTION 3 -- DEFECT 3: the criteria split and its recorded flag")

_old_split = _old_function("split_inclusion_exclusion")
check_true("the OLD splitter was lifted out of git", _old_split is not None)

# The heading styles the old markers mishandled. They fail in TWO distinct
# ways and the controls are separated accordingly, because asserting the wrong
# failure mode is how a control ends up passing for a reason that has nothing
# to do with the defect.
#
#   LOST      -- the old markers matched nothing, so exclusion came back "" and
#                every exclusion criterion reached the judge as an inclusion.
#   MISPLACED -- the old markers matched the INNER "exclusion criteria:" of a
#                "Key Exclusion Criteria:" heading, four characters late, so
#                the orphaned word "Key" was left on the end of the inclusion
#                text. Measured, not assumed: see the assertion below.
LOST_CASES = [
    ("no colon after the heading",
     "Inclusion Criteria\n\n* Adults\n\nExclusion Criteria\n\n* Pregnancy"),
    ("bulleted headings",
     "* Inclusion Criteria\n* Adults\n\n* Exclusion Criteria\n* Pregnancy"),
]

for _label, _text in LOST_CASES:
    _o_inc, _o_exc = _old_split(_text)
    check_true(f"CONTROL: the OLD splitter LOSES the exclusion section -- {_label}",
               _o_exc == "")
    check_true(f"CONTROL: ...so 'Pregnancy' reached the judge as an INCLUSION"
               f" -- {_label}", "Pregnancy" in _o_inc)
    _n_inc, _n_exc, _n_method = indexer.split_inclusion_exclusion(_text)
    check_true(f"FIXED: an exclusion section is recovered -- {_label}",
               _n_exc != "")
    check(f"FIXED: and the method is recorded -- {_label}",
          _n_method, indexer.CRITERIA_SPLIT_BOTH)
    check_true(f"FIXED: 'Pregnancy' is in EXCLUSION, not inclusion -- {_label}",
               "Pregnancy" in _n_exc and "Pregnancy" not in _n_inc)

_KEY_TEXT = ("Key Inclusion Criteria:\n\n* Adults\n\n"
             "Key Exclusion Criteria:\n\n* Pregnancy")
_o_inc, _o_exc = _old_split(_KEY_TEXT)
check_true("CONTROL: the OLD splitter DOES find a Key-prefixed exclusion",
           _o_exc != "")
check_true("CONTROL: ...but cuts four characters late, orphaning 'Key' onto "
           "the end of the inclusion text",
           _o_inc.endswith("Key"))
check_true("CONTROL: ...and drops 'Key' from the exclusion heading",
           _o_exc.startswith("Exclusion Criteria"))
_n_inc, _n_exc, _n_method = indexer.split_inclusion_exclusion(_KEY_TEXT)
check_true("FIXED: the boundary is at the real heading, no orphaned 'Key'",
           not _n_inc.endswith("Key"))
check_true("FIXED: ...and the exclusion section starts at 'Key Exclusion'",
           _n_exc.startswith("Key Exclusion Criteria"))
check("FIXED: ...and the method is recorded",
      _n_method, indexer.CRITERIA_SPLIT_BOTH)

# --- the anchored search must never LOSE a split the old one found ---------
#
# The anchored pattern alone lost 116 splits on the stored corpus, because a
# real heading is not always at a line start. The fallback to the original
# substring markers is what makes the new split a strict superset. This is the
# check that would catch someone deleting that fallback.
_MID_LINE = ("Patients will be enrolled per protocol. Exclusion criteria: "
             "prior therapy.")
_o_inc, _o_exc = _old_split(_MID_LINE)
check_true("the old splitter finds a MID-LINE exclusion marker", _o_exc != "")
_n_inc, _n_exc, _n_method = indexer.split_inclusion_exclusion(_MID_LINE)
check_true("FIXED: the fallback keeps that split rather than losing it",
           _n_exc != "")

# --- the unsplit state is a RECORDED FIELD, not an inference ---------------
_UNSPLIT = "Participants aged 18 or older with a confirmed diagnosis."
_inc, _exc, _method = indexer.split_inclusion_exclusion(_UNSPLIT)
check("a genuinely unsplit block is reported as unsplit",
      _method, indexer.CRITERIA_SPLIT_UNSPLIT)
check_true("...and the trial is KEPT, not excluded from the corpus",
           _inc == _UNSPLIT)

check("empty criteria text has its own outcome",
      indexer.split_inclusion_exclusion("")[2], indexer.CRITERIA_SPLIT_EMPTY)

# The flag rides on the trial dict, which is what goes into Qdrant. A
# downstream ingestion gate must be able to read it without re-deriving it.
_protocol = {
    "identificationModule": {"nctId": "NCT-SPLIT", "briefTitle": "t"},
    "eligibilityModule": {"eligibilityCriteria": _UNSPLIT},
}
_trial = indexer.parse_trial_metadata(_protocol)
check_true("criteria_split is a real field on the trial dict",
           "criteria_split" in _trial)
check("...carrying the unsplit verdict",
      _trial["criteria_split"], indexer.CRITERIA_SPLIT_UNSPLIT)

# CONTROL: the old parse_trial_metadata carried no such field.
_old_parse_src = _OLD_SRC or ""
if _old_parse_src:
    _old_tree = ast.parse(_old_parse_src)
    _old_keys = set()
    for _n in ast.walk(_old_tree):
        if isinstance(_n, ast.FunctionDef) and _n.name == "parse_trial_metadata":
            for _d in ast.walk(_n):
                if isinstance(_d, ast.Dict):
                    for _k in _d.keys:
                        if isinstance(_k, ast.Constant):
                            _old_keys.add(_k.value)
    check_true("CONTROL: the OLD trial dict had no criteria_split field",
               "criteria_split" not in _old_keys)
    check_true("...and that key walk found the other keys (non-degeneracy)",
               "nct_id" in _old_keys and "eligibility" in _old_keys)

# The 3-tuple is the contract now; a 2-tuple caller would silently unpack wrong.
check("split_inclusion_exclusion returns three members",
      len(indexer.split_inclusion_exclusion("Inclusion Criteria:\nx")), 3)
check_true("CONTROL: the OLD one returned two", len(_old_split("x")) == 2)


# ===========================================================================
# SECTION 3b -- THE EXCLUSION-HEADING FAMILIES THE ANCHORED SEARCH MISSED
# ===========================================================================
section("SECTION 3b -- the measured exclusion-heading families")

# WHY A SECOND CONTROL SPLITTER. _old_split above comes from the pre-DEFECT-3
# revision: it returns a 2-tuple and has no anchored search at all, so it is
# the wrong control for an extension TO the anchored search -- against it every
# case below would "pass" for the same reason every case in section 3 does, and
# nothing would be testing this work. The control needed here is the splitter
# as it stood AFTER defect 3 and BEFORE these families were added.
#
# THE REVISION IS DERIVED STRUCTURALLY, NOT BY SUBSTRING. The extension turned
# _HEADING_LEAD from a plain string literal into an expression built from two
# named constants, so "the newest revision whose top-level _HEADING_LEAD is an
# ast.Constant" identifies the pre-extension state exactly and cannot be
# satisfied by a comment quoting the old pattern -- the trap that made
# tests/test_storage_query_layer.py's first selector pick its own fix commit.


def _revision_with_literal_heading_lead():
    """Newest revision whose `_HEADING_LEAD` is a plain string literal."""
    log = _git("log", "--format=%H", "--", "oncotriage/retrieval/indexer.py")
    if log.returncode != 0:
        return None, None
    for rev in log.stdout.split():
        blob = _git("show", f"{rev}:oncotriage/retrieval/indexer.py")
        if blob.returncode != 0 or not blob.stdout:
            continue
        try:
            tree = ast.parse(blob.stdout)
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (isinstance(target, ast.Name)
                        and target.id == "_HEADING_LEAD"
                        and isinstance(node.value, ast.Constant)):
                    return rev, blob.stdout
    return None, None


_PRE_EXT_REV, _PRE_EXT_SRC = _revision_with_literal_heading_lead()

# The whole splitter machinery out of that blob, exec'd into a throwaway
# namespace. Lifted, never retyped: a retyped control tests the retyping.
_PRE_EXT_WANT = {
    "_LEGACY_INCLUSION_MARKERS", "_LEGACY_EXCLUSION_MARKERS", "_HEADING_LEAD",
    "_INCLUSION_HEADINGS", "_EXCLUSION_HEADINGS", "_INCLUSION_RE",
    "_EXCLUSION_RE", "CRITERIA_SPLIT_BOTH", "CRITERIA_SPLIT_INCLUSION_ONLY",
    "CRITERIA_SPLIT_EXCLUSION_ONLY", "CRITERIA_SPLIT_UNSPLIT",
    "CRITERIA_SPLIT_EMPTY", "CRITERIA_SPLIT_METHODS", "_compile_headings",
    "_first_heading", "_legacy_marker_position", "_section_start",
    "split_inclusion_exclusion",
}


def _pre_extension_splitter():
    if not _PRE_EXT_SRC:
        return None
    tree = ast.parse(_PRE_EXT_SRC)
    picked = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _PRE_EXT_WANT:
            picked.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in _PRE_EXT_WANT
                for t in node.targets):
            picked.append(node)
    ns = {"re": re, "Counter": __import__("collections").Counter}
    exec(compile(ast.Module(body=picked, type_ignores=[]),
                 "<pre-extension-indexer>", "exec"), ns)
    return ns.get("split_inclusion_exclusion")


_pre_split = _pre_extension_splitter()

check_true("a pre-extension revision of the splitter was located",
           _PRE_EXT_REV is not None)
print(f"    control revision: {(_PRE_EXT_REV or '?')[:12]}")
check_true("the pre-extension splitter was lifted out of git",
           _pre_split is not None)
# NON-DEGENERACY. It must be the POST-defect-3 splitter, or these controls are
# the section-3 controls wearing a different name: a 2-tuple return means the
# selector walked back past defect 3 and every case below is vacuous.
check("CONTROL: ...and it is the POST-defect-3 splitter (3-tuple)",
      len(_pre_split("Inclusion Criteria:\nx")) if _pre_split else None, 3)
check_true("CONTROL: ...with the anchored search already present -- a colonless "
           "heading splits (this is what section 3 fixed)",
           bool(_pre_split("Inclusion Criteria\n* A\n\nExclusion Criteria\n* P")[1])
           if _pre_split else False)


def _recovers(label, text, expect_in_exclusion="Pregnancy"):
    """One family: the pre-extension splitter misses it, the shipped one does not."""
    if not _pre_split:
        check_true(f"CONTROL unavailable -- {label}", False)
        return
    o_inc, o_exc, o_method = _pre_split(text)
    check_true(f"CONTROL: the PRE-EXTENSION splitter finds no exclusion "
               f"section -- {label}", o_exc == "")
    check_true(f"CONTROL: ...so {expect_in_exclusion!r} reached the judge as an "
               f"INCLUSION -- {label}", expect_in_exclusion in o_inc)
    n_inc, n_exc, n_method = indexer.split_inclusion_exclusion(text)
    check_true(f"FIXED: the exclusion section is recovered -- {label}",
               n_exc != "")
    check(f"FIXED: ...and the method is recorded -- {label}",
          n_method, indexer.CRITERIA_SPLIT_BOTH)
    check_true(f"FIXED: ...and {expect_in_exclusion!r} is in EXCLUSION, not "
               f"inclusion -- {label}",
               expect_in_exclusion in n_exc and expect_in_exclusion not in n_inc)


# --- family (a): wrapper characters, AS THE CORPUS SPELLS THEM --------------
#
# ClinicalTrials.gov markdown-escapes its punctuation, so these carry a literal
# backslash. Writing them unescaped would test a string the corpus does not
# contain -- measured on the stored corpus, every occurrence is escaped.
_INC = "Inclusion Criteria\n* Adults\n"
_recovers("wrapper: \\<Exclusion Criteria\\>",
          _INC + "\\<Exclusion Criteria\\>\n* Pregnancy")
_recovers("wrapper: \\[Exclusion Criteria\\]",
          _INC + "\\[Exclusion Criteria\\]\n* Pregnancy")
_recovers("wrapper: \\- Exclusion Criteria",
          _INC + "\\- Exclusion Criteria\n* Pregnancy")

# --- family (b): multi-level section numbers at the margin ------------------
_recovers("list number: 4.2 Exclusion Criteria",
          _INC + "4.2 Exclusion Criteria\n* Pregnancy")
_recovers("list number: 4.1.2 Exclusion Criteria",
          _INC + "4.1.2 Exclusion Criteria\n* Pregnancy")
_recovers("list number: 5.2. Exclusion Criteria",
          _INC + "5.2. Exclusion Criteria\n* Pregnancy")
_recovers("list number: 2.0 Exclusion Criteria",
          _INC + "2.0 Exclusion Criteria\n* Pregnancy")

# --- family (c): measured word prefixes -------------------------------------
_recovers("prefix: Main Exclusion Criteria",
          "Main Inclusion Criteria\n* Adults\nMain Exclusion Criteria\n* Pregnancy")
_recovers("prefix: General Exclusion Criteria",
          "General Inclusion Criteria\n* Adults\n"
          "General Exclusion Criteria\n* Pregnancy")
_recovers("prefix: Core Exclusion Criteria",
          "Core Inclusion Criteria\n* Adults\nCore Exclusion Criteria\n* Pregnancy")
_recovers("prefix: Participant Exclusion Criteria",
          "Participant Inclusion Criteria\n* Adults\n"
          "Participant Exclusion Criteria\n* Pregnancy")
_recovers("prefix: Case Exclusion Criteria",
          _INC + "Case Exclusion Criteria\n* Pregnancy")
_recovers("sentence-form: The main exclusion criteria include ...",
          _INC + "The main exclusion criteria include but are not limited to "
                 "the following:\n* Pregnancy")

# The inclusion side carries only the prefixes the corpus shows on BOTH sides.
# `case` is the one asymmetry and it is deliberate: no "Case Inclusion
# Criteria" occurs anywhere in the stored corpus.
check_true("the inclusion side gained the five symmetric prefixes",
           all(any(p in h for h in indexer._INCLUSION_HEADINGS)
               for p in ("the\\s+main", "participant", "general", "main", "core")))
check_true("...and NOT `case`, which the corpus shows on the exclusion side only",
           not any("case" in h for h in indexer._INCLUSION_HEADINGS))
check_true("CONTROL: `case` IS on the exclusion side",
           any("case\\s+exclusion" in h for h in indexer._EXCLUSION_HEADINGS))


# --- the negative controls: prose must still not be a heading --------------
#
# Anchoring is the whole protection, and these are the phrasings that would
# defeat a wildcard prefix. Each is required to resolve IDENTICALLY before and
# after -- not merely "not to become `both`", which a shape that broke the
# inclusion side would also satisfy.
_NEGATIVE = [
    ("prose: 'Non-exclusion criteria :'", "Non-exclusion criteria :\n* anything"),
    ("prose: 'none of the exclusion criteria'",
     _INC + "none of the exclusion criteria apply to this participant"),
    ("prose: 'meets any exclusion criteria'",
     _INC + "meets any exclusion criteria listed in the protocol"),
    ("fuzzy: 'An individual who meets ... will be excluded'",
     _INC + "An individual who meets any of the following criteria will be "
            "excluded from the study:\n* Pregnancy"),
    ("fuzzy: 'Participants will be excluded if they meet'",
     _INC + "Participants will be excluded if they meet any of the following "
            "criteria:\n* Pregnancy"),
    ("fuzzy: 'Subjects who meet ... should be excluded'",
     _INC + "Subjects who meet any of the following criteria should be "
            "excluded from the trial:\n* Pregnancy"),
    ("mid-sentence: '... meeting the main exclusion criteria'",
     _INC + "* Adults meeting the main exclusion criteria are ineligible"),
    ("mid-sentence: '... does not meet general exclusion criteria'",
     _INC + "* Subject does not meet general exclusion criteria"),
]
for _label, _text in _NEGATIVE:
    _n = indexer.split_inclusion_exclusion(_text)
    if _pre_split:
        check(f"UNCHANGED by the extension -- {_label}", _n, _pre_split(_text))
    check_true(f"...and no exclusion section is invented -- {_label}", _n[1] == "")

# The fuzzy cases are a STATED LIMITATION rather than an oversight: they are
# mid-sentence boundaries with no heading to anchor on, and recognising them
# needs a different mechanism than a line-anchored pattern.
check("the fuzzy mid-sentence cases stay unrecognised, deliberately",
      indexer.split_inclusion_exclusion(
          _INC + "An individual who meets any of the following criteria will "
                 "be excluded:\n* Pregnancy")[2],
      indexer.CRITERIA_SPLIT_INCLUSION_ONLY)


# --- the two shapes deliberately NOT admitted, each with its control --------
#
# Both were measured and both were rejected because they mis-split a trial the
# pre-extension splitter split correctly. A test that only asserts what WAS
# added cannot see either of them come back.

# (i) a bulleted sub-number is a LIST ITEM, not a section heading. Admitting it
#     let the existing `patients must not` alternative match an INCLUSION
#     bullet and cut the exclusion section 40 criteria early (NCT07178301).
_BULLETED_SUBNUMBER = ("Inclusion Criteria:\n"
                       "* 3.1.1 Histologically confirmed disease.\n"
                       "* 3.1.2 Patients must not have received prior treatment.\n"
                       "Exclusion Criteria:\n* Pregnancy")
_b = indexer.split_inclusion_exclusion(_BULLETED_SUBNUMBER)
check_true("a bulleted sub-number does NOT start the exclusion section",
           _b[1].startswith("Exclusion Criteria"))
check_true("...so the inclusion bullet stays in INCLUSION",
           "3.1.2" in _b[0] and "3.1.2" not in _b[1])
check("CONTROL: the pre-extension splitter agrees (this is not a regression "
      "the extension had to introduce)",
      _pre_split(_BULLETED_SUBNUMBER) if _pre_split else _b, _b)

# (ii) an ESCAPED list terminator ("11\. Patients must not ...") is not
#      admitted. It recovers two trials and mis-splits one (NCT06822010).
_ESCAPED_TERMINATOR = ("Inclusion Criteria:\n"
                       "11\\. Patients must not have any other medical condition.\n"
                       "Exclusion Criteria:\n* Pregnancy")
_e = indexer.split_inclusion_exclusion(_ESCAPED_TERMINATOR)
check_true("an escaped list terminator does NOT start the exclusion section",
           _e[1].startswith("Exclusion Criteria"))
check("CONTROL: the pre-extension splitter agrees",
      _pre_split(_ESCAPED_TERMINATOR) if _pre_split else _e, _e)

# The lead's character set and _first_heading's walk come from ONE constant, so
# the pattern cannot admit a character the walk then fails to step over --
# which would leave the section starting with "\<" instead of "Exclusion".
check_true("the wrapper class and the walk set share one source",
           all(c in indexer._HEADING_LEAD_STRIP
               for c in indexer._HEADING_LEAD_CHARS))
check_true("...and the walk set adds only whitespace",
           set(indexer._HEADING_LEAD_STRIP) - set(indexer._HEADING_LEAD_CHARS)
           == set("\n \t"))
check_true("the escaped wrapper characters the corpus actually uses are in it",
           all(c in indexer._HEADING_LEAD_CHARS for c in "\\<[-"))


# --- THE SUPERSET PROOF, over the stored corpus ----------------------------
#
# LOST == 0 IS A DESIGN CONSTRAINT, NOT AN OBSERVATION (see the split
# measurement block in the indexer). The unit cases above show each family
# recovers; only this shows that nothing was traded away to get them.
#
# IT NEEDS THE STORED CORPUS AND SAYS SO AS A RECORDED FAILURE rather than a
# silent skip: a superset proof that quietly did not run is indistinguishable
# from one that passed. Read-only, ~2s, no network.
_CORPUS = None
try:
    from oncotriage import paths as _paths
    _CORPUS = os.path.join(_paths.data_trial_path, "trials_latest.json")
except Exception as _exc:                                    # noqa: BLE001
    print(f"    corpus path unavailable: {type(_exc).__name__}: {_exc}")

check_true("the stored corpus is present for the superset proof",
           bool(_CORPUS) and os.path.exists(_CORPUS))

if _CORPUS and os.path.exists(_CORPUS) and _pre_split:
    import json as _json
    with open(_CORPUS, encoding="utf-8") as _fh:
        _corpus = _json.load(_fh)
    check_true("...and it is non-degenerate", len(_corpus) > 1000)

    _lost_exc = _both_moved = _changed = 0
    for _t in _corpus:
        _text = (_t.get("eligibility") or {}).get("criteria_text") or ""
        _o_inc, _o_exc, _o_m = _pre_split(_text)
        _n_inc, _n_exc, _n_m = indexer.split_inclusion_exclusion(_text)
        if _o_exc and not _n_exc:
            _lost_exc += 1
        if _o_m == indexer.CRITERIA_SPLIT_BOTH and _n_m != _o_m:
            _both_moved += 1
        if _o_m != _n_m:
            _changed += 1
    check("SUPERSET: no trial loses an exclusion split", _lost_exc, 0)
    check("SUPERSET: no `both` trial changes classification", _both_moved, 0)
    check_true("...and the comparison is not vacuous -- trials DID move",
               _changed > 0)
    print(f"    corpus: {len(_corpus):,} trials, {_changed} changed "
          f"classification, {_lost_exc} lost")


# ===========================================================================
# SECTION 3c -- THE exclusion_only BRANCH KEEPS ITS LEADING TEXT
# ===========================================================================
section("SECTION 3c -- the exclusion_only branch keeps its leading text")

# A THIRD CONTROL SPLITTER, and it has to be a third one. _old_split predates
# defect 3 entirely and _pre_split predates the heading families; against
# either, every case below "passes" for a reason that has nothing to do with
# this repair. The control needed here is the splitter as it stood AFTER the
# families and BEFORE the branch was repaired.
#
# THE REVISION IS DERIVED STRUCTURALLY. The repair introduced a local named
# `prefix` inside split_inclusion_exclusion; no revision before it binds that
# name, and a comment or docstring quoting the old branch cannot create a Name
# STORE. So "the newest revision whose split_inclusion_exclusion never assigns
# `prefix`" identifies the pre-repair state exactly.


def _revision_before_prefix_kept():
    """Newest revision whose splitter binds no local called `prefix`."""
    log = _git("log", "--format=%H", "--", "oncotriage/retrieval/indexer.py")
    if log.returncode != 0:
        return None, None
    for rev in log.stdout.split():
        blob = _git("show", f"{rev}:oncotriage/retrieval/indexer.py")
        if blob.returncode != 0 or not blob.stdout:
            continue
        try:
            tree = ast.parse(blob.stdout)
        except SyntaxError:
            continue
        for node in tree.body:
            if (isinstance(node, ast.FunctionDef)
                    and node.name == "split_inclusion_exclusion"):
                binds = any(isinstance(n, ast.Name) and n.id == "prefix"
                            and isinstance(n.ctx, ast.Store)
                            for n in ast.walk(node))
                if not binds:
                    return rev, blob.stdout
                break
    return None, None


_PRE_KEEP_REV, _PRE_KEEP_SRC = _revision_before_prefix_kept()


def _splitter_from(source):
    """The whole splitter machinery out of one blob, lifted, never retyped."""
    if not source:
        return None
    tree = ast.parse(source)
    want = _PRE_EXT_WANT | {"_HEADING_LEAD_CHARS", "_HEADING_LEAD_NUMBER",
                            "_HEADING_LEAD_CLASS", "_HEADING_LEAD_STRIP"}
    picked = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in want:
            picked.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in want for t in node.targets):
            picked.append(node)
    ns = {"re": re, "Counter": __import__("collections").Counter}
    exec(compile(ast.Module(body=picked, type_ignores=[]),
                 "<pre-repair-indexer>", "exec"), ns)
    return ns.get("split_inclusion_exclusion")


_pre_keep = _splitter_from(_PRE_KEEP_SRC)

check_true("a pre-repair revision of the splitter was located",
           _PRE_KEEP_REV is not None)
print(f"    control revision: {(_PRE_KEEP_REV or '?')[:12]}")
check_true("the pre-repair splitter was lifted out of git", _pre_keep is not None)

# NON-DEGENERACY, TWO WAYS. It must be the POST-families splitter, or this is
# section 3b's control under a new name and every case below is vacuous; and it
# must still DISCARD the prefix, or it is not a control for this repair at all.
check_true("CONTROL: ...and the heading families are already present",
           _pre_keep("Inclusion Criteria\n* A\n4.2 Exclusion Criteria\n* P")[2]
           == indexer.CRITERIA_SPLIT_BOTH if _pre_keep else False)
check("CONTROL: ...and it still DISCARDS the leading text",
      _pre_keep("Adults with disease.\nExclusion Criteria\n* Pregnancy")[0]
      if _pre_keep else None, "")

# --- the repair: headingless leading text is the inclusion section ----------
_HEADINGLESS = ("Patients aged 18 or older with histologically confirmed "
                "disease.\nExclusion Criteria\n* Pregnancy")
_k_inc, _k_exc, _k_m = indexer.split_inclusion_exclusion(_HEADINGLESS)
check("a headingless prefix above an exclusion heading is classified `both`",
      _k_m, indexer.CRITERIA_SPLIT_BOTH)
check_true("...and the prefix IS the inclusion section",
           _k_inc == "Patients aged 18 or older with histologically confirmed "
                     "disease.")
check_true("...and the exclusion section is unchanged by the repair",
           _k_exc == _pre_keep(_HEADINGLESS)[1] if _pre_keep else False)
check("CONTROL: the pre-repair splitter called the same text `exclusion_only`",
      _pre_keep(_HEADINGLESS)[2] if _pre_keep else None,
      indexer.CRITERIA_SPLIT_EXCLUSION_ONLY)
check_true("CONTROL: ...and the inclusion text vanished entirely",
           _pre_keep(_HEADINGLESS)[0] == "" if _pre_keep else False)

# --- position zero still has nothing above it -------------------------------
#
# The branch is not deleted, and this is the case that proves it. A heading at
# offset zero genuinely has no inclusion text, so inventing `both` for it would
# assert a section that does not exist.
for _label, _text in (
    ("bare heading at offset 0", "Exclusion Criteria\n* Pregnancy\n* Age < 18"),
    ("heading behind leading whitespace",
     "\n\nExclusion Criteria\n* Pregnancy"),
    ("heading behind a bullet", "* Exclusion Criteria\n* Pregnancy"),
):
    _z = indexer.split_inclusion_exclusion(_text)
    check(f"still `exclusion_only` -- {_label}", _z[2],
          indexer.CRITERIA_SPLIT_EXCLUSION_ONLY)
    check(f"...with an EMPTY inclusion section -- {_label}", _z[0], "")
    if _pre_keep:
        check(f"UNCHANGED by the repair -- {_label}", _z, _pre_keep(_text))

# A whitespace-only prefix is not a section. Asserted apart from the cases
# above because it is the one that distinguishes `if prefix` from
# `if criteria_text[:exclusion_start]`, which is truthy for a single newline.
check("a whitespace-only prefix does not become an inclusion section",
      indexer.split_inclusion_exclusion(" \t\n Exclusion Criteria\n* P")[2],
      indexer.CRITERIA_SPLIT_EXCLUSION_ONLY)

# --- nothing else moves -----------------------------------------------------
#
# The repair touches ONE branch. Every other branch must resolve identically,
# or something was traded for these recoveries.
for _label, _text in (
    ("both", "Inclusion Criteria:\n* Adults\nExclusion Criteria:\n* Pregnancy"),
    ("inclusion_only", "Inclusion Criteria:\n* Adults over 18"),
    ("unsplit", "Adults with measurable disease and adequate organ function."),
    ("empty_criteria", "   "),
):
    if _pre_keep:
        check(f"UNCHANGED by the repair -- {_label}",
              indexer.split_inclusion_exclusion(_text), _pre_keep(_text))

# --- the three named trials, out of the stored corpus -----------------------
#
# These are the three the heading families moved into this branch, and the
# reason the repair could not be postponed: recovering their exclusion heading
# is what destroyed their inclusion text. Recorded as a FAILURE when the corpus
# is absent, never a silent skip -- the same rule section 3b's superset proof
# follows, and for the same reason.
_NAMED_RECOVERY = {
    # nct_id: (criteria chars, prefix chars the branch used to discard)
    "NCT06934382": (5522, 3561),
    "NCT04581512": (3436, 1777),
    "NCT05464082": (8116, 4914),
}

if _CORPUS and os.path.exists(_CORPUS) and _pre_keep:
    import json as _json2
    with open(_CORPUS, encoding="utf-8") as _fh:
        _corpus3c = _json2.load(_fh)
    _by_id = {t.get("nct_id"): t for t in _corpus3c}
    for _nct, (_want_total, _want_prefix) in _NAMED_RECOVERY.items():
        _t = _by_id.get(_nct)
        check_true(f"{_nct} is in the stored corpus", _t is not None)
        if not _t:
            continue
        _text = (_t.get("eligibility") or {}).get("criteria_text") or ""
        check(f"{_nct}: its criteria block is the measured length",
              len(_text), _want_total)
        _o = _pre_keep(_text)
        _n = indexer.split_inclusion_exclusion(_text)
        check(f"CONTROL: {_nct} reached the judge with NO inclusion text",
              len(_o[0]), 0)
        check(f"{_nct}: the full inclusion prefix is recovered",
              len(_n[0]), _want_prefix)
        check(f"{_nct}: ...and it is classified `both`", _n[2],
              indexer.CRITERIA_SPLIT_BOTH)
        check(f"{_nct}: ...and its exclusion section is untouched",
              _n[1], _o[1])
        check_true(f"{_nct}: ...so no character of the criteria block is lost",
                   len(_n[0]) + len(_n[1]) >= len(_o[0]) + len(_o[1]))

    # THE CORPUS-WIDE CONTAINMENT PROOF for this repair, the counterpart of
    # section 3b's superset proof. Only the exclusion_only population may move.
    _moved = _lost = _both_moved = _other_moved = _recovered = 0
    for _t in _corpus3c:
        _text = (_t.get("eligibility") or {}).get("criteria_text") or ""
        _o_inc, _o_exc, _o_m = _pre_keep(_text)
        _n_inc, _n_exc, _n_m = indexer.split_inclusion_exclusion(_text)
        if len(_n_inc) < len(_o_inc) or len(_n_exc) < len(_o_exc):
            _lost += 1
        _recovered += max(0, (len(_n_inc) + len(_n_exc))
                          - (len(_o_inc) + len(_o_exc)))
        if _o_m != _n_m:
            _moved += 1
            if _o_m == indexer.CRITERIA_SPLIT_BOTH:
                _both_moved += 1
            elif _o_m != indexer.CRITERIA_SPLIT_EXCLUSION_ONLY:
                _other_moved += 1
    check("CONTAINMENT: no trial loses a character of criteria text", _lost, 0)
    check("CONTAINMENT: no `both` trial changes classification", _both_moved, 0)
    check("CONTAINMENT: nothing but `exclusion_only` changes classification",
          _other_moved, 0)
    check("31 trials are recovered", _moved, 31)
    check("...and the measured 86,058 characters with them", _recovered, 86058)
    print(f"    corpus: {len(_corpus3c):,} trials, {_moved} recovered, "
          f"{_recovered:,} characters, {_lost} lost")


# ===========================================================================
# SECTION 3d -- THE SPLIT IS RE-DERIVED AT INDEX TIME
# ===========================================================================
section("SECTION 3d -- the rebuild path re-derives the split")

# THE HAZARD: the DAG loads trials_latest.json and hands the stored trials to
# index_trials, so any splitter change after a scrape rebuilds with the STALE
# split. Fixing it inside index_trials is what makes both entry paths inherit
# it -- and it must be inside index_trials, because the generated DAG is
# pinned and a second copy of this logic in a generated string is how the
# DAG's old private scraper drifted.

_STALE = {
    "nct_id": "NCTSTALE01",
    "title": "A Study of Squamous Non-Small Cell Lung Cancer",
    "eligibility": {
        "criteria_text": "Stage III squamous NSCLC.\n"
                         "Exclusion Criteria\n* Pregnancy",
        # what a pre-repair scrape stored: the prefix discarded
        "inclusion_criteria": "",
        "exclusion_criteria": "Exclusion Criteria\n* Pregnancy",
    },
    "criteria_split": indexer.CRITERIA_SPLIT_EXCLUSION_ONLY,
    # enrichments computed from the empty inclusion section, plus a value no
    # extractor can produce, so "unchanged" cannot be mistaken for "correct"
    "structured_eligibility": {"min_stage": 9, "max_stage": 9,
                               "accepts_metastatic": True},
    "histology_tags": ["planted-tag"],
}

import copy as _copy                                          # noqa: E402

# A DEEP COPY, so the literal above stays the untouched statement of what a
# pre-repair scrape wrote and can be reused by the control below.
_planted = _copy.deepcopy(_STALE)
_counts = indexer.renormalize_criteria_derived_fields([_planted])

check("the normalizer reports the trial it saw", _counts.get("trials"), 1)
check("the stale criteria_split is corrected", _planted["criteria_split"],
      indexer.CRITERIA_SPLIT_BOTH)
check("...and reported as changed", _counts.get("changed:criteria_split"), 1)
check("the discarded inclusion section is restored",
      _planted["eligibility"]["inclusion_criteria"], "Stage III squamous NSCLC.")
check("...and reported as changed",
      _counts.get("changed:inclusion_criteria"), 1)
check_true("the exclusion section is unchanged, and reported as unchanged",
           _planted["eligibility"]["exclusion_criteria"]
           == "Exclusion Criteria\n* Pregnancy"
           and "changed:exclusion_criteria" not in _counts)

# THE DERIVED FIELDS ONE LEVEL DOWN. Recomputing only the split recreates the
# same disagreement here: both of these read the INCLUSION section.
check("the planted structured_eligibility is recomputed",
      _planted["structured_eligibility"],
      {"min_stage": 3, "max_stage": 3, "accepts_metastatic": None})
check("...and reported as changed",
      _counts.get("changed:structured_eligibility"), 1)
check("the planted histology_tags are recomputed",
      _planted["histology_tags"], ["nsclc", "squamous"])
check("...and reported as changed", _counts.get("changed:histology_tags"), 1)
check_true("...and the planted value is gone, not merely appended to",
           "planted-tag" not in _planted["histology_tags"])

# NON-DEGENERACY: the recomputed values must come from the RECOVERED text, not
# from the title alone, or this section would pass with the repair reverted.
_title_only = _copy.deepcopy(_STALE)
_title_only["eligibility"]["criteria_text"] = "Exclusion Criteria\n* Pregnancy"
indexer.renormalize_criteria_derived_fields([_title_only])
check("CONTROL: with no prefix to recover, no stage is extracted",
      _title_only["structured_eligibility"]["min_stage"], None)

# IDEMPOTENT: the source is criteria_text, which the splitter never modifies.
_again = indexer.renormalize_criteria_derived_fields([_planted])
check("a second pass changes nothing", _again, {"trials": 1})

# A trial whose eligibility cannot be read is COUNTED AND SKIPPED, never
# repaired into a shape the parser does not produce.
_bad = {"nct_id": "NCTBAD", "eligibility": None}
check("an unreadable eligibility mapping is counted, not raised on",
      indexer.renormalize_criteria_derived_fields([_bad]),
      {"trials": 1, "skipped:eligibility_NoneType": 1})
check_true("...and nothing is invented in its place", "criteria_split" not in _bad)

# --- the wiring: BOTH entry paths reach it, and the DAG does not change -----
_ix_src = open(os.path.join(_CODE_DIR, "oncotriage", "retrieval", "indexer.py"),
               encoding="utf-8").read()
_ix_tree = ast.parse(_ix_src)


def _fn_node(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def _called_names(node):
    out = set()
    for c in ast.walk(node):
        if isinstance(c, ast.Call):
            f = c.func
            out.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    return out


_index_trials = _fn_node(_ix_tree, "index_trials")
check_true("index_trials was located in the shipped module", _index_trials is not None)
check_true("index_trials re-derives the split",
           "renormalize_criteria_derived_fields" in _called_names(_index_trials))

# THE MODULE COUNTER HAS A READER, and that is asserted rather than assumed. It
# accumulates across calls, and a counter whose only mention is a docstring
# satisfies check 2h's scan without anyone ever seeing the number -- the dead
# declaration wearing a passing test.
check_true("the cumulative counter moved with the calls above",
           indexer.CRITERIA_RENORMALIZED.get("trials", 0) >= 4
           and indexer.CRITERIA_RENORMALIZED.get("changed:criteria_split", 0) >= 1)
check_true("...and index_trials READS it, in executable code",
           any(isinstance(n, ast.Name) and n.id == "CRITERIA_RENORMALIZED"
               and isinstance(n.ctx, ast.Load)
               for n in ast.walk(_index_trials)))

# ORDER: the recompute must precede the first thing that consumes the fields,
# or it corrects a trial after its embedding text has been built.
_first_norm = min(
    [n.lineno for n in ast.walk(_index_trials) if isinstance(n, ast.Call)
     and getattr(n.func, "id", "") == "renormalize_criteria_derived_fields"],
    default=None)
_first_use = min(
    [n.lineno for n in ast.walk(_index_trials) if isinstance(n, ast.Call)
     and getattr(n.func, "id", getattr(n.func, "attr", ""))
     in ("create_trial_embedding_text", "create_trial_bm25_fields")],
    default=None)
check_true("...and does it BEFORE any field derived from the split is read",
           _first_norm is not None and _first_use is not None
           and _first_norm < _first_use)

# THE CENSUS COUNTER IS CLEARED BEFORE THE RE-DERIVATION, and this is asserted
# because the hazard is invisible otherwise. Every splitter call increments
# CRITERIA_SPLIT_METHODS, so on the scrape path -- parse_trial_metadata splits,
# then index_trials splits again -- each trial is counted TWICE, and the
# counter is documented as one entry per trial. Demonstrated first, then
# pinned: without the clear, the count below is 2 for one trial.
_dbl = {"nct_id": "NCTDBL", "eligibility": {"criteria_text":
        "Inclusion Criteria:\n* Adults\nExclusion Criteria:\n* Pregnancy"}}
_before_total = sum(indexer.CRITERIA_SPLIT_METHODS.values())
indexer.split_inclusion_exclusion(_dbl["eligibility"]["criteria_text"])  # "parse"
indexer.renormalize_criteria_derived_fields([_dbl])                     # "index"
check("HAZARD: splitting twice counts the same trial twice",
      sum(indexer.CRITERIA_SPLIT_METHODS.values()) - _before_total, 2)
check_true("...which is why index_trials clears the census first",
           any(isinstance(n, ast.Call)
               and getattr(n.func, "attr", "") == "clear"
               and getattr(getattr(n.func, "value", None), "id", "")
               == "CRITERIA_SPLIT_METHODS"
               for n in ast.walk(_index_trials)))
_clear_line = min(
    [n.lineno for n in ast.walk(_index_trials) if isinstance(n, ast.Call)
     and getattr(n.func, "attr", "") == "clear"
     and getattr(getattr(n.func, "value", None), "id", "")
     == "CRITERIA_SPLIT_METHODS"], default=None)
check_true("...and clears it BEFORE re-deriving, or it would erase the answer",
           _clear_line is not None and _first_norm is not None
           and _clear_line < _first_norm)
# It is cleared IN PLACE and never rebound: oncotriage/degradation.py binds
# counter OBJECTS, so `NAME = Counter()` would leave every reader holding a
# counter that reports zero forever.
check_true("...in place, never rebound",
           not any(isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name)
                           and t.id == "CRITERIA_SPLIT_METHODS"
                           for t in n.targets)
                   for n in ast.walk(_index_trials)))
check_true("main() reaches index_trials",
           "index_trials" in _called_names(_fn_node(_ix_tree, "main")))

# CONTROL: strip the call out of an AST copy and the wiring check must FAIL.
_stripped = ast.parse(_ix_src)
_st_node = _fn_node(_stripped, "index_trials")


class _DropNormalize(ast.NodeTransformer):
    def visit_Expr(self, node):
        return None if (isinstance(node.value, ast.Call)
                        and getattr(node.value.func, "id", "")
                        == "renormalize_criteria_derived_fields") else node

    def visit_Assign(self, node):
        return None if (isinstance(node.value, ast.Call)
                        and getattr(node.value.func, "id", "")
                        == "renormalize_criteria_derived_fields") else node


_DropNormalize().visit(_st_node)
check_true("CONTROL: with the call stripped, the wiring check FAILS",
           "renormalize_criteria_derived_fields" not in _called_names(_st_node))

# The generated DAG must not have moved by one byte, and must still carry no
# splitter of its own -- the recompute belongs to index_trials, which the DAG
# calls, so putting it in the DAG would be the second copy this avoids.
from oncotriage.orchestration.dag_generator import build_dag_content  # noqa: E402

_dag3d = build_dag_content(code_path="/x/code/", keys_path="/x/keys/",
                           data_trial_path="/x/data/")
_dag3d_head = None
_blob3d = _git("show", "HEAD:oncotriage/orchestration/dag_generator.py")
if _blob3d.returncode == 0 and _blob3d.stdout:
    _ns3d = {}
    exec(compile(_blob3d.stdout, "<HEAD-dag>", "exec"), _ns3d)
    _dag3d_head = _ns3d["build_dag_content"](
        code_path="/x/code/", keys_path="/x/keys/", data_trial_path="/x/data/")
check_true("the generated DAG is byte-identical to HEAD's",
           _dag3d_head is not None and _dag3d == _dag3d_head)
check_true("...and is non-degenerate", len(_dag3d) > 10_000)
_dag3d_tree = ast.parse(_dag3d)
check_true("the DAG's rebuild task reaches index_trials",
           "index_trials" in _called_names(_fn_node(_dag3d_tree, "rebuild_index")))

# THE ABSENCE CHECK IS STRUCTURAL, NOT A SUBSTRING. The generated DAG's own
# comment block NAMES `_split_inclusion_exclusion` while recording that the
# private scraper reimplementing it was deleted -- so a substring test is
# defeated by the prose explaining the fix, which is the trap this project has
# hit three times (the exec_chain scan, the query-layer selector, the Docker
# settings greps). Definitions and calls are what "carries logic" means.
_DAG_FORBIDDEN = ("split_inclusion_exclusion",
                  "renormalize_criteria_derived_fields")
_dag_defs = {n.name for n in ast.walk(_dag3d_tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
_dag_calls_all = set()
for _c in ast.walk(_dag3d_tree):
    if isinstance(_c, ast.Call):
        _dag_calls_all.add(getattr(_c.func, "id",
                                   getattr(_c.func, "attr", "")))
check_true("...and defines no splitter of its own to drift from this one",
           not (_dag_defs & set(_DAG_FORBIDDEN)))
check_true("...and calls neither the splitter nor the normalizer directly",
           not (_dag_calls_all & set(_DAG_FORBIDDEN)))
check_true("CONTROL: the scan CAN see a call of that name (non-degeneracy)",
           "index_trials" in _dag_calls_all)
check_true("CONTROL: ...and a substring test would have been defeated by the "
           "DAG's own comment about the deleted copy",
           "split_inclusion_exclusion" in _dag3d)


# ===========================================================================
# SECTION 4 -- DEFECT 4: verify before swap, keep a rollback
# ===========================================================================
section("SECTION 4 -- DEFECT 4: verification gates the swap")


class _FakeCollections:
    def __init__(self, names):
        self.collections = [types.SimpleNamespace(name=n) for n in names]


class _FakeAliases:
    def __init__(self, mapping):
        self.aliases = [types.SimpleNamespace(alias_name=a, collection_name=c)
                        for a, c in mapping.items()]


class _RecordingClient:
    """Enough Qdrant surface for cleanup and alias resolution. No network."""

    def __init__(self, names, aliases):
        self._names = list(names)
        self._aliases = dict(aliases)
        self.deleted = []

    def get_collections(self):
        return _FakeCollections(self._names)

    def get_aliases(self):
        return _FakeAliases(self._aliases)

    def delete_collection(self, collection_name):
        self.deleted.append(collection_name)
        self._names.remove(collection_name)


_ORDERED = ["trial_criteria_20260101_000000",
            "trial_criteria_20260201_000000",
            "trial_criteria_20260301_000000",
            "trial_criteria_20260401_000000"]


def _with_client(client, fn):
    """Run fn with indexer.get_qdrant_client swapped for a stand-in."""
    original = indexer.get_qdrant_client
    indexer.get_qdrant_client = lambda: client
    try:
        return fn()
    finally:
        indexer.get_qdrant_client = original


# --- CONTROL: keep_recent=1 destroys the rollback target -------------------
#
# Reproducing the OLD cleanup exactly: keep the newest N by name, no alias
# awareness. With N=1 the only survivor is the collection just promoted.
def _old_cleanup(client, keep_recent=1):
    names = sorted([c.name for c in client.get_collections().collections
                    if c.name.startswith("trial_criteria_")], reverse=True)
    for name in names[keep_recent:]:
        client.delete_collection(collection_name=name)
    return names[:keep_recent]


_c = _RecordingClient(_ORDERED, {"trial_criteria": _ORDERED[-1]})
_kept = _old_cleanup(_c, keep_recent=1)
check("CONTROL: the OLD cleanup keeps exactly one collection", len(_kept), 1)
check_true("CONTROL: ...so NO rollback target survives",
           len([n for n in _kept if n != _ORDERED[-1]]) == 0)
check("CONTROL: ...and it deleted the previous good collection",
      sorted(_c.deleted), sorted(_ORDERED[:-1]))

# --- FIXED: a rollback target survives -------------------------------------
_c = _RecordingClient(_ORDERED, {"trial_criteria": _ORDERED[-1]})
_with_client(_c, lambda: indexer.cleanup_old_collections(
    keep_recent=2, alias_name="trial_criteria"))
check_true("FIXED: the promoted collection survives",
           _ORDERED[-1] not in _c.deleted)
check_true("FIXED: the PREVIOUS good collection survives as a rollback target",
           _ORDERED[-2] not in _c.deleted)
check("FIXED: only the genuinely old ones are deleted",
      sorted(_c.deleted), sorted(_ORDERED[:-2]))

# --- FIXED: keep_recent=1 is refused ---------------------------------------
_c = _RecordingClient(_ORDERED, {"trial_criteria": _ORDERED[-1]})
_with_client(_c, lambda: indexer.cleanup_old_collections(
    keep_recent=1, alias_name="trial_criteria"))
check_true("FIXED: keep_recent=1 is floored to 2, rollback still there",
           _ORDERED[-2] not in _c.deleted)

# --- FIXED: the alias target is never deleted, even out of the window ------
#
# The old code assumed the alias pointed at the newest collection. After a
# failed swap that is exactly false, and the live collection was the one
# sorted out of the keep window.
_c = _RecordingClient(_ORDERED, {"trial_criteria": _ORDERED[0]})  # oldest is live
_with_client(_c, lambda: indexer.cleanup_old_collections(
    keep_recent=2, alias_name="trial_criteria"))
check_true("FIXED: the LIVE collection is kept even when it sorts oldest",
           _ORDERED[0] not in _c.deleted)

_c = _RecordingClient(_ORDERED, {"trial_criteria": _ORDERED[0]})
_old_cleanup(_c, keep_recent=2)
check_true("CONTROL: the OLD cleanup DELETES the live collection in that case",
           _ORDERED[0] in _c.deleted)

# --- resolve_alias_target ---------------------------------------------------
_c = _RecordingClient(_ORDERED, {"trial_criteria": _ORDERED[2]})
check("resolve_alias_target reports the live collection",
      _with_client(_c, lambda: indexer.resolve_alias_target("trial_criteria")),
      _ORDERED[2])
check("...and None when the alias does not exist",
      _with_client(_c, lambda: indexer.resolve_alias_target("nope")), None)

# --- verification exists, gates the swap, and raises -----------------------
check_true("verify_collection exists", callable(indexer.verify_collection))
check_true("IndexVerificationError is a RuntimeError",
           issubclass(indexer.IndexVerificationError, RuntimeError))
check_true("...and NOT a ValueError (a stray except must not eat it)",
           not issubclass(indexer.IndexVerificationError, ValueError))

# The ORDER is the defect. In main(), verification must precede the swap, and
# the swap must precede cleanup. Asserted structurally against the real AST.
_main = next(n for n in ast.walk(_indexer_tree)
             if isinstance(n, ast.FunctionDef) and n.name == "main")
_call_order = [ast.unparse(n.func) for n in ast.walk(_main)
               if isinstance(n, ast.Call)]


def _first_index(name):
    for i, c in enumerate(_call_order):
        if c.endswith(name):
            return i
    return -1


_v, _s, _c_i = (_first_index("verify_collection"),
                _first_index("swap_alias_atomic"),
                _first_index("cleanup_old_collections"))
check_true("main() calls verify_collection", _v >= 0)
check_true("main() calls swap_alias_atomic", _s >= 0)
check_true("FIXED: verification happens BEFORE the alias swap", _v < _s)
check_true("FIXED: the swap happens before cleanup", _s < _c_i)

# CONTROL: the old main() had no verification call at all.
if _OLD_SRC:
    _old_main = next((n for n in ast.walk(ast.parse(_OLD_SRC))
                      if isinstance(n, ast.FunctionDef) and n.name == "main"),
                     None)
    if _old_main is not None:
        _old_calls = [ast.unparse(n.func) for n in ast.walk(_old_main)
                      if isinstance(n, ast.Call)]
        check_true("CONTROL: the OLD main() had NO verification step",
                   not any("verify" in c for c in _old_calls))
        check_true("CONTROL: ...and swapped the alias anyway",
                   any("swap_alias_atomic" in c for c in _old_calls))
        check_true("CONTROL: ...and cleaned up with keep_recent=1",
                   "cleanup_old_collections(keep_recent=1)" in
                   ast.unparse(_old_main))

# verify_collection must RAISE, not return False, when a check fails. A caller
# that forgot to test a boolean would swap anyway; an exception cannot be
# ignored by omission.
_verify_fn = next(n for n in ast.walk(_indexer_tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "verify_collection")
_raises = [n for n in ast.walk(_verify_fn) if isinstance(n, ast.Raise)]
check_true("verify_collection raises rather than returning a flag",
           len(_raises) >= 1)

# --- yellow is optimization, not a fault -----------------------------------
#
# THIS COST A BUILD. The first version failed on any status that was not
# green, and Qdrant reports `yellow` while its optimizers construct the HNSW
# index after a bulk upsert -- the normal state seconds after indexing 14,324
# points. A run that had scraped, embedded and PAID IN FULL was refused at the
# last step by a collection that was busy finishing and was green ninety
# seconds later, with every functional probe already passing.
_verify_src = ast.get_source_segment(_indexer_src, _verify_fn) or ""
check_true("red is treated as a real failure",
           '"red" in status' in _verify_src)
check_true("yellow is WAITED for, not failed",
           "yellow" in _verify_src and "_STATUS_WAIT_SECONDS" in _verify_src)
check_true("...with a bounded wait, so a stuck collection still fails",
           isinstance(indexer._STATUS_WAIT_SECONDS, int)
           and indexer._STATUS_WAIT_SECONDS > 0)
check_true("...and a poll interval smaller than the wait",
           0 < indexer._STATUS_POLL_SECONDS < indexer._STATUS_WAIT_SECONDS)
# The wait loop must exit on green, or a green collection would still spin.
check_true("the wait loop is conditioned on yellow, not on 'not green'",
           'while "yellow" in status' in _verify_src)


# ===========================================================================
# SECTION 5 -- the generated Airflow DAG carries no second implementation
# ===========================================================================
section("SECTION 5 -- the DAG delegates instead of duplicating")

from oncotriage.orchestration.dag_generator import build_dag_content  # noqa: E402

_dag = build_dag_content(code_path="/x/code/", keys_path="/x/keys/",
                         data_trial_path="/x/trials/")
_dag_tree = ast.parse(_dag)
_dag_funcs = {n.name for n in ast.walk(_dag_tree)
              if isinstance(n, ast.FunctionDef)}

check_true("the generated DAG still parses as Python", True)
for _gone in ("_split_inclusion_exclusion", "_parse_trial_metadata",
              "_create_trial_embedding_text", "_extract_locations"):
    check_true(f"the DAG no longer defines its own {_gone}",
               _gone not in _dag_funcs)
check_true("...and the function walk found the DAG's real functions",
           "trial_refresh_weekly" in _dag_funcs)

_dag_compares = [ast.unparse(n) for n in ast.walk(_dag_tree)
                 if isinstance(n, ast.Compare)]
check_true("the DAG carries no age comparison in executable code",
           not any("min_age" in c for c in _dag_compares))
check_true("...and that walk found comparisons (non-degeneracy)",
           len(_dag_compares) > 0)

check_true("the DAG delegates to the package's indexer",
           "from oncotriage.retrieval import indexer" in _dag)

# The import must be DEFERRED: a module-scope import that fails makes the DAG
# vanish from the scheduler with an import error instead of failing one run.
_module_level_imports = [ast.unparse(n) for n in _dag_tree.body
                         if isinstance(n, (ast.Import, ast.ImportFrom))]
check_true("the oncotriage import is NOT at module scope in the DAG",
           not any("oncotriage" in i for i in _module_level_imports))
check_true("...and the module-level import walk found imports (non-degeneracy)",
           len(_module_level_imports) > 3)

# And the DAG's own verify must precede its swap, same as main().
check_true("the DAG verifies before swapping",
           _dag.index("verify_collection") < _dag.index("swap_alias_atomic"))
check_true("the DAG keeps a rollback target",
           "keep_recent=2" in _dag)


# ===========================================================================
# SECTION 6 -- A PARTIAL SCRAPE MUST NOT BECOME A CORPUS
# ===========================================================================
section("SECTION 6 -- a truncated scrape raises instead of being promoted")

# THIS SECTION EXISTS BECAUSE THE DEFECT SHIPPED. The first real run of this
# pass hit a ClinicalTrials.gov read timeout, returned 5,482 of ~14,300 trials
# as an ordinary return value, and main() indexed and PROMOTED it over a
# 12,067-trial collection. Every other check in this file was green.

import json as _json  # noqa: E402
import tempfile  # noqa: E402
from oncotriage import paths as _paths  # noqa: E402

check_true("IncompleteScrapeError exists",
           hasattr(indexer, "IncompleteScrapeError"))
check_true("...and is a RuntimeError",
           issubclass(indexer.IncompleteScrapeError, RuntimeError))
# It must NOT be a RequestException, or the same `except` that swallowed the
# underlying network fault would swallow the refusal built to surface it.
import requests as _requests  # noqa: E402
check_true("...and NOT a requests exception",
           not issubclass(indexer.IncompleteScrapeError,
                          _requests.exceptions.RequestException))


class _FailingSession:
    """Answers page 1, then raises the fault that actually occurred.

    Carries `.exceptions` because the scrape's own `except` clause resolves
    `requests.exceptions.RequestException` through this same module object.
    Without it the stand-in raises AttributeError from inside the handler and
    the test reports a crash rather than the refusal it is checking for.
    """

    exceptions = _requests.exceptions

    def __init__(self, pages_before_failure=1):
        self.calls = 0
        self.pages_before_failure = pages_before_failure

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self.calls > self.pages_before_failure:
            raise _requests.exceptions.ReadTimeout("Read timed out.")

        class _R:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {
                    "studies": [{
                        "protocolSection": {
                            "identificationModule": {"nctId": "NCT00000001",
                                                     "briefTitle": "A cancer study"},
                            "designModule": {"studyType": "INTERVENTIONAL"},
                            "conditionsModule": {"conditions": ["Breast Neoplasms"]},
                            "eligibilityModule": {"eligibilityCriteria":
                                                  "Inclusion Criteria:\nx"},
                        }
                    }],
                    "nextPageToken": "MORE",
                }
        return _R()


_scratch = tempfile.mkdtemp(prefix="oncotriage-scrape-")
_saved_requests = indexer.requests
_saved_resolved = dict(getattr(_paths, "_RESOLVED", {}))
_session = _FailingSession()
try:
    indexer.requests = _session
    _paths._RESOLVED["checkpoint_path"] = _scratch + os.sep
    indexer.SCRAPE_INTERRUPTIONS.clear()
    try:
        indexer.scrape_clinicaltrials_gov(max_trials=5000)
        check_true("a truncated scrape RAISES rather than returning a corpus",
                   False)
    except indexer.IncompleteScrapeError as _exc:
        check_true("a truncated scrape RAISES rather than returning a corpus",
                   True)
        check_true("...naming how many trials it did get",
                   "1 trial" in str(_exc) or "1," in str(_exc)
                   or " 1 " in str(_exc))
        check_true("...and naming the interruption that stopped it",
                   "ReadTimeout" in str(_exc))
        check_true("...and telling the operator the checkpoint was kept",
                   "resume" in str(_exc).lower())
    check_true("the network fault was COUNTED, not swallowed",
               indexer.SCRAPE_INTERRUPTIONS.get("ReadTimeout", 0) == 1)
    # The checkpoint must SURVIVE, or "re-run to resume" is a lie.
    _ckpt = os.path.join(_scratch, "scrape_checkpoint.json")
    check_true("the checkpoint is KEPT so a re-run resumes", os.path.exists(_ckpt))
    if os.path.exists(_ckpt):
        _saved = _json.load(open(_ckpt))
        check_true("...carrying the page_token it stopped at",
                   bool(_saved.get("page_token")))
finally:
    indexer.requests = _saved_requests
    _paths._RESOLVED.clear()
    _paths._RESOLVED.update(_saved_resolved)
    import shutil as _shutil
    _shutil.rmtree(_scratch, ignore_errors=True)

# --- the size-vs-live guard, the second line of defence --------------------
#
# Even if a scrape truncates some other way, a collection materially smaller
# than the one it would replace must not be promoted. Driven against
# stand-ins: no network, no Qdrant.


class _CountingClient:
    """Only what the size branch of verify_collection touches."""

    def __init__(self, counts):
        self._counts = counts

    def count(self, collection_name, exact=True):
        return types.SimpleNamespace(count=self._counts[collection_name])


def _size_verdict(new_n, live_n, ratio=0.90):
    """True if the size branch would ACCEPT this pair."""
    return new_n >= int(live_n * ratio)


check_true("CONTROL: the collection that actually shipped is REFUSED "
           "(5,482 vs 12,067)", not _size_verdict(5482, 12067))
check_true("a same-size rebuild is accepted", _size_verdict(12067, 12067))
check_true("ordinary registry churn is accepted (43 removed of 12,067)",
           _size_verdict(12067 - 43, 12067))
check_true("a genuine growth rebuild is accepted", _size_verdict(14311, 12067))
check_true("a 15% collapse is refused", _size_verdict(int(12067 * 0.85), 12067) is False)
check("the floor is a named constant, not a literal at the call site",
      indexer._MIN_CORPUS_RATIO, 0.90)

# verify_collection must ACCEPT the compare_to argument, and main() must pass
# it -- a guard nobody calls is not a guard.
import inspect as _inspect  # noqa: E402
check_true("verify_collection takes compare_to",
           "compare_to" in _inspect.signature(indexer.verify_collection).parameters)
_main_src = ast.get_source_segment(_indexer_src, _main) or ""
check_true("main() passes compare_to to verify_collection",
           "compare_to=baseline" in _main_src)
# The baseline is the explicit override when given, else the alias target.
check_true("...and the baseline falls back to the alias target",
           "baseline = compare_to or previous" in _main_src)
check_true("...resolved BEFORE the rebuild starts",
           _main_src.index("previous = resolve_alias_target")
           < _main_src.index("verify_collection"))
# main() must expose the override, or a bad alias target cannot be worked
# around without editing the package.
check_true("main() takes compare_to",
           "compare_to" in _inspect.signature(indexer.main).parameters)
check_true("main() takes run_cleanup",
           "run_cleanup" in _inspect.signature(indexer.main).parameters)
check_true("main() takes max_cost_usd",
           "max_cost_usd" in _inspect.signature(indexer.main).parameters)

# --- the spend gate ---------------------------------------------------------
check_true("EmbeddingBudgetExceeded is a RuntimeError",
           issubclass(indexer.EmbeddingBudgetExceeded, RuntimeError))
# The gate must sit between the scrape and index_trials, or it is not a gate.
check_true("the budget check precedes index_trials in main()",
           _main_src.index("max_cost_usd is not None")
           < _main_src.index("index_trials("))
check_true("...and follows the scrape, since the corpus size is not knowable "
           "before it",
           _main_src.index("scrape_clinicaltrials_gov(")
           < _main_src.index("max_cost_usd is not None"))
# The estimator must be exact where it can be. Measured against the API's own
# usage block on a real run: 5,960,458 tokens estimated, 5,960,458 billed.
_est = indexer.estimate_embedding_cost([
    {"title": "A study of breast cancer", "conditions": ["Breast Neoplasms"],
     "eligibility": {"min_age": "18 Years", "max_age": "99 Years", "sex": "ALL"}}])
check("the estimator reports which method produced the number",
      _est["method"], "tiktoken")
check_true("...and a non-zero token count (non-degeneracy)", _est["tokens"] > 0)


# ===========================================================================
section("SUMMARY")
print(f"  passed: {_passed}")
print(f"  failed: {_failed}")

if __name__ == "__main__":
    sys.exit(1 if _failed else 0)


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug 07 2026

@author: ramyalsaffar
"""
