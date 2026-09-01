# Stage Extraction: which stage-group Observation Tier 0 answers on
##################################################################

"""
Stage-Group Observation Sort Test

Tier 0 of ``extract_patient_stage_with_source`` walks a patient's stage-group
Observations most-recent-first and answers on the first one whose display
parses. Which one that is decides the ordinal Stage 4 filters on and -- since
the staging-date item -- the date printed into the Stage 5 prompt, so the
ordering is visible input rather than an internal detail.

IT USED TO BE ``key=lambda o: o.get('date') or '0000-00-00', reverse=True``.
Three defects, and the first one is the live one:

  1. "unknown" SORTED AS THE NEWEST OBSERVATION.
     ``oncotriage/fhir/parser.py:_parse_mcode_stage_observation`` emits the
     LITERAL STRING 'unknown' when an Observation carries neither
     effectiveDateTime nor effectivePeriod.start -- never None, never '' -- so
     the ``or '0000-00-00'`` fallback never fired for it, and 'u' outranks
     every digit. An UNDATED observation therefore beat every dated one,
     answered for the patient, and rendered as "staging date not recorded"
     while a real dated staging sat unused below it. Nothing raised:
     ``_stage_date_clause`` guards 'unknown' at the render site.
  2. A FULL ISO DATETIME CARRYING AN OFFSET WAS COMPARED AS LOCAL TEXT, with
     no stated semantic. Every one of the corpus's 585 stage-group stamps is
     such a datetime.
  3. IT DISAGREED WITH THE PROJECT'S OWN CONVENTION.
     ``OncologyLabRegistry._date_sort_key`` -- which orders the labs, genomic
     variants and procedures of the SAME rendered summary -- slices to the day
     prefix and maps BOTH missing and 'unknown' to oldest.

MEASURED OVER ALL 1,000 CORPUS BUNDLES, before and after, through a git
worktree at HEAD rather than through a re-derivation:

    winning observation changed     0
    ordinal changed                 0
    source changed                  0
    observation_date changed        0
    rendered stage clause changed   0
    full sorted order changed       0

    585 stage-group Observations; 0 carry date == 'unknown'; 0 carry a falsy
    date; 0 are bare YYYY-MM-DD (all 585 are datetimes with an offset).
    290 of 1,000 patients carry exactly TWO stage-group Observations with
    BYTE-IDENTICAL stamps -- Synthea emits one staging event twice, as
    "American Joint Committee on Cancer stage IA (qualifier value)" and as
    "Stage 1 (qualifier value)" -- so day and raw stamp both tie for 29% of
    the cohort and the position term decides. That is why it is NEGATED: a
    plain index would put the LAST record first, flipping the answering record
    for 290 patients to no observable effect, and a fix moves what the defect
    moved and nothing else. Section 5 pins that direction; control C3 shows
    what dropping the negation does.

SO THE DEFECT IS REACHABLE BY ORDINARY INPUT AND UNEXERCISED BY THIS CORPUS.
Both halves are stated because they are different claims: the parser produces
'unknown' and nothing between it and the sort guards it, and no bundle in hand
omits a staging effective date. The fix moves nothing today.

THE KEY IS A LOCAL TWIN OF THE REGISTRY'S, NOT AN IMPORT OF IT, and section 3
is the price of that: it pins the two as answering identically over a shared
corpus of inputs, so they cannot separate silently. Section 6 pins the
layering reason -- ``oncotriage/extraction/`` is a leaf and must not grow an
edge to ``oncotriage/registries/``.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO DATABASE, NO
GIT HISTORY, NO LIVE SERVER, NO CORPUS -- every fixture here is a literal
dict. It writes NOTHING anywhere, not even a temp directory. NOT in
tests/run_serial_tests.py's collision matrix: the two repository files it
reads (oncotriage/extraction/stage.py, oncotriage/registries/
cancer_code_registry.py) are written by neither of the suite's two writers,
and both are sha256-compared at the end. It DOES exec: four in-memory copies
of stage.py, one plant each -- `git show` can supply none of them, because
three revert a fix that is AT HEAD and would compare the module with itself,
and the fourth (C3) is a state no revision has ever been in.

    python tests/test_extraction_stage_observation_sort.py
"""

import ast
import hashlib
import os
import sys
import types

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

from oncotriage.extraction import stage as _stage_module
from oncotriage.extraction.stage import (
    STAGE_SOURCE_STAGE_GROUP,
    _date_sort_key,
    _stage_observation_sort_key,
    extract_patient_stage,
    extract_patient_stage_with_source,
)
from oncotriage.registries.cancer_code_registry import OncologyLabRegistry


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
    """Call `fn`, converting a raise into a value `check` can fail on.

    A raise inside a check's ARGUMENT list escapes `check` entirely and takes
    the run with it -- reporting one traceback where the file owed a summary
    and a full set of results. Every plant below can make a probe raise, and a
    plant that makes a probe raise is exactly the case these checks exist to
    report.
    """
    try:
        return fn()
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        return f"raised {type(exc).__name__}: {exc}"


_STAGE_SRC = os.path.abspath(_stage_module.__file__)
_REGISTRY_SRC = os.path.abspath(
    sys.modules["oncotriage.registries.cancer_code_registry"].__file__)

# Taken before any plant runs, so the restore assertions at the end compare
# against a real baseline rather than against themselves.
_STAGE_SHA_BEFORE = hashlib.sha256(
    open(_STAGE_SRC, encoding="utf-8").read().encode()).hexdigest()
_REGISTRY_SHA_BEFORE = hashlib.sha256(
    open(_REGISTRY_SRC, encoding="utf-8").read().encode()).hexdigest()


class _PlantFailed(Exception):
    """A plant that did not apply or did not compile. Never escapes a check."""


def _plant(path, name, subs):
    """Exec an in-memory COPY of `path` with `subs` applied.

    Raises _PlantFailed -- never SyntaxError -- so a malformed plant is a
    RECORDED failure instead of a traceback hiding every check below it, and a
    plant whose target has moved is named rather than silently applying
    nothing and reporting a working check as broken.
    """
    source = open(path, encoding="utf-8").read()
    before = hashlib.sha256(source.encode()).hexdigest()
    try:
        for old, new, expected_count in subs:
            found = source.count(old)
            if found != expected_count:
                raise _PlantFailed(
                    f"plant target occurs {found}x, expected "
                    f"{expected_count}x: {old[:70]!r}...")
            source = source.replace(old, new, expected_count)
        module = types.ModuleType(name)
        module.__file__ = path
        exec(compile(source, path, "exec"), module.__dict__)
    except _PlantFailed:
        raise
    except Exception as exc:            # noqa: BLE001 - reported, not raised
        raise _PlantFailed(f"{type(exc).__name__}: {exc}") from None
    finally:
        after = hashlib.sha256(
            open(path, encoding="utf-8").read().encode()).hexdigest()
        if before != after:
            raise AssertionError(f"{path} was modified on disk by a plant")
    return module


_CONTROL_SEQ = [0]


def _control(label, subs, probe, expected):
    """Plant into a copy of stage.py, probe it, record. A raise IS an outcome."""
    _CONTROL_SEQ[0] += 1
    try:
        module = _plant(_STAGE_SRC, f"planted_stage_{_CONTROL_SEQ[0]}", subs)
    except _PlantFailed as exc:
        check(f"{label}  [THE PLANT ITSELF FAILED: {exc}]", "plant-failed",
              expected)
        return
    check(label, guarded(lambda: probe(module)), expected)


def key(date_str):
    """_date_sort_key, guarded. Every revert of the placeholder mapping makes
    it raise on None and '' -- which is precisely the case sections 1 and 3
    exist to report, so an unguarded call would abort the file exactly when it
    owed a full set of results."""
    return guarded(lambda: _date_sort_key(date_str))


def tuple_key(index, observation):
    """_stage_observation_sort_key, guarded, for the same reason.

    Takes no `module` parameter: every caller drives the SHIPPED key, and the
    planted copies are driven through tier0() instead, which is where a
    plant's effect is observable. A parameter no caller passes is a
    declaration nothing reads.
    """
    return guarded(
        lambda: _stage_observation_sort_key(index, observation))


def obs(display, date):
    """One entry of patient_data['cancer_stage_observations'], exactly as
    oncotriage/fhir/parser.py:_parse_mcode_stage_observation emits it -- note
    that its `date` is the LITERAL STRING 'unknown' when the resource carries
    no effective date, which is the shape defect 1 is about."""
    return {"stage_display": display, "stage_code": "", "date": date,
            "loinc": "21908-9"}


def tier0(observations, module=_stage_module):
    """(ordinal, date) Tier 0 answers with, for a patient with no conditions.

    Guarded: a reverted sort key raises inside the sort, and a raise in a
    check's ARGUMENT list escapes check() and takes the run with it.
    """
    def _run():
        st = module.extract_patient_stage_with_source(
            [], cancer_stage_observations=observations)
        return (st.ordinal, st.observation_date)
    return guarded(_run)


# Realistic stamps, in the shape the corpus actually carries: a full ISO
# datetime with an offset. Two ordinals so the WINNER is observable through
# the returned value rather than having to be inferred.
_D_OLD = "2011-04-15T11:18:59-07:00"
_D_NEW = "2024-12-06T04:20:18-08:00"


print("=" * 70)
print("SECTION 1 -- _date_sort_key: the day slice and the two placeholders")
print("=" * 70)

check("1a  a full ISO datetime is reduced to its calendar day",
      key("2019-05-28T11:05:53-07:00"), "2019-05-28")
check("1b  a bare day passes through unchanged",
      key("2019-05-28"), "2019-05-28")
check("1c  the literal 'unknown' sorts OLDEST -- defect 1",
      key("unknown"), "0000-00-00")
check("1d  None sorts OLDEST",
      key(None), "0000-00-00")
check("1e  the empty string sorts OLDEST",
      key(""), "0000-00-00")
check("1f  ...and 'unknown' is BELOW every real stamp, which is the whole "
      "point of 1c",
      guarded(lambda: key("unknown") < key("1900-01-01")), True)
check("1g  NON-DEGENERACY: the key is not a constant function",
      guarded(lambda: len({key(v) for v in
                           ("unknown", "2019-05-28", "2024-01-01")})), 3)


print()
print("=" * 70)
print("SECTION 2 -- what the day slice does and does not change")
print("=" * 70)
# The docstring at the key claims the slice changes NO ordering for well-formed
# ISO stamps and that its entire behavioural content is the placeholder
# mapping. That is a measurement, not an assertion, so it is measured.

_WELL_FORMED = [
    "2011-04-15T11:18:59-07:00",
    "2011-04-15T23:00:00-05:00",
    "2011-04-15",
    "2011-04-16T04:00:00+09:00",
    "2024-12-06T04:20:18-08:00",
    "1962-04-03T13:58:12-08:00",
    "2019-05-28T11:05:53-07:00",
]
_disagree = guarded(lambda: [
    (a, b) for a in _WELL_FORMED for b in _WELL_FORMED
    if ((key(a) > key(b)) != (a > b)) and key(a) != key(b)
])
check("2a  day-prefix and whole-string ordering agree on every well-formed "
      "pair that the day key separates at all",
      _disagree, [])
check("2b  NON-DEGENERACY: those pairs are not all ties -- the day key really "
      "does separate most of them",
      guarded(lambda: sum(1 for a in _WELL_FORMED for b in _WELL_FORMED
                          if key(a) != key(b)) > 0), True)
check("2c  ...and the pairs it ties are the same-day ones, which the raw "
      "stamp then separates in the same direction the old key did",
      guarded(lambda: key("2011-04-15T11:18:59-07:00")
              == key("2011-04-15T23:00:00-05:00")
              and "2011-04-15T23:00:00-05:00" > "2011-04-15T11:18:59-07:00"),
      True)
check("2d  the ONE thing the slice changes: 'unknown' loses to a real stamp "
      "where the raw comparison had it winning",
      guarded(lambda: (key("unknown") > key("2024-12-06T04:20:18-08:00"),
                       "unknown" > "2024-12-06T04:20:18-08:00")),
      (False, True))


print()
print("=" * 70)
print("SECTION 3 -- the equivalence pin against the registry's twin")
print("=" * 70)
# The layering ruling at the key says extraction/ may not import registries/,
# so stage.py carries a local twin of OncologyLabRegistry._date_sort_key. This
# section is the price of that ruling: it is what stops the two separating
# silently. If it fails, the two copies have come apart and one is wrong.

_SHARED_INPUTS = [
    None, "", "unknown", "0000-00-00",
    "2019-05-28", "2019-05-28T11:05:53-07:00", "1962-04-03T13:58:12-08:00",
    "2024-12-06T04:20:18-08:00", "2011-04-15T23:00:00-05:00",
    "9999-12-31T23:59:59+14:00", "2019", "2019-05",
    "UNKNOWN", "Unknown", " unknown",
]
_twin = OncologyLabRegistry._date_sort_key
_mismatches = guarded(
    lambda: [v for v in _SHARED_INPUTS if _date_sort_key(v) != _twin(v)])
check("3a  the two _date_sort_key implementations agree on every shared input",
      _mismatches, [])
check("3b  NON-DEGENERACY: the shared corpus is not trivial -- it produces "
      "several distinct keys",
      guarded(lambda: len({key(v) for v in _SHARED_INPUTS}) >= 8), True)
check("3c  NON-DEGENERACY: it contains the case a naive twin gets wrong",
      guarded(lambda: "unknown" in _SHARED_INPUTS
              and key("unknown") == "0000-00-00"), True)
check("3d  NON-DEGENERACY: they are two different function objects, so 3a is "
      "not comparing one function with itself",
      _date_sort_key is _twin, False)
check("3e  ...and the case-variant spellings are NOT special-cased by either, "
      "which is a shared limit rather than a shared feature",
      guarded(lambda: (key("UNKNOWN"), _twin("UNKNOWN"))),
      ("UNKNOWN", "UNKNOWN"))


print()
print("=" * 70)
print("SECTION 4 -- Tier 0 picks the same winner whichever order it is given")
print("=" * 70)
# Each case is driven in BOTH orderings of the input list. The two
# observations carry DIFFERENT ordinals, so the winner is observable through
# the returned value rather than inferred.

# (a) THE 'unknown' CASE -- the live defect. Under the old key the undated
#     observation outranked the dated one and answered.
_unknown_pair = [obs("Stage I", "unknown"), obs("Stage IV", _D_NEW)]
check("4a  an undated ('unknown') observation LOSES to a dated one",
      tier0(_unknown_pair), (4, _D_NEW))
check("4b  ...and again with the list order reversed",
      tier0(list(reversed(_unknown_pair))), (4, _D_NEW))
check("4c  ...even when the dated one is very old, because 'unknown' is not a "
      "date at all",
      tier0([obs("Stage I", "unknown"), obs("Stage IV", "1900-01-02")]),
      (4, "1900-01-02"))
check("4d  when EVERY observation is undated, one still answers and its date "
      "is reported verbatim for the renderer to guard",
      tier0([obs("Stage II", "unknown")]), (2, "unknown"))

# (b) THE MISSING CASE -- None and '' take the same route as 'unknown'.
for _label, _missing in (("None", None), ("''", "")):
    _pair = [obs("Stage I", _missing), obs("Stage IV", _D_NEW)]
    check(f"4e  a {_label}-dated observation loses to a dated one",
          tier0(_pair), (4, _D_NEW))
    check(f"4f  ...and again with the list order reversed",
          tier0(list(reversed(_pair))), (4, _D_NEW))

# (c) THE OFFSET CASE -- two stamps whose OFFSETS differ and whose local days
#     differ. The day key decides, and it decides the same way both ways round.
_offset_pair = [obs("Stage I", "2024-03-01T23:00:00-05:00"),
                obs("Stage IV", "2024-03-02T04:00:00+09:00")]
check("4g  across differing offsets the LATER LOCAL CALENDAR DAY wins",
      tier0(_offset_pair), (4, "2024-03-02T04:00:00+09:00"))
check("4h  ...and again with the list order reversed",
      tier0(list(reversed(_offset_pair))), (4, "2024-03-02T04:00:00+09:00"))

# (d) THE EQUAL-DAY TIE -- same local day, different stamps. The day key ties
#     and the raw stamp breaks it, so the winner does NOT depend on list order.
_sameday_pair = [obs("Stage I", "2024-03-01T08:00:00-05:00"),
                 obs("Stage IV", "2024-03-01T20:00:00-05:00")]
check("4i  on one day the later stamp wins",
      tier0(_sameday_pair), (4, "2024-03-01T20:00:00-05:00"))
check("4j  ...and again with the list order reversed -- the raw stamp is a "
      "property of the RECORD, so bundle order does not reach it",
      tier0(list(reversed(_sameday_pair))), (4, "2024-03-01T20:00:00-05:00"))

# (e) THE UNREADABLE-NEWER CASE -- the property the tier's own comment claims:
#     the answering observation is the first whose display PARSES, not the
#     newest, and the date reported is that one's.
_D_MID = "2018-03-25T00:09:01-07:00"
check("4k  the newest observation is unreadable, so the newest READABLE one "
      "answers -- and the date reported is that record's, not the newest "
      "date on the bundle",
      tier0([obs("no stage here at all", _D_NEW),
             obs("Stage II", _D_MID),
             obs("Stage I", _D_OLD)]),
      (2, _D_MID))
check("4l  ...and again with the list order reversed",
      tier0(list(reversed([obs("no stage here at all", _D_NEW),
                           obs("Stage II", _D_MID),
                           obs("Stage I", _D_OLD)]))),
      (2, _D_MID))

check("4m  the thin delegate agrees with the richer form on the 'unknown' case",
      guarded(lambda: extract_patient_stage(
          [], cancer_stage_observations=_unknown_pair)), 4)
check("4n  the source is still the stage-group tier throughout",
      guarded(lambda: extract_patient_stage_with_source(
          [], cancer_stage_observations=_unknown_pair).source),
      STAGE_SOURCE_STAGE_GROUP)


print()
print("=" * 70)
print("SECTION 5 -- the full tie: byte-identical stamps, 290 corpus patients")
print("=" * 70)
# 290 of 1,000 corpus patients carry exactly two stage-group Observations with
# BYTE-IDENTICAL stamps, so day and raw stamp both tie and only the position
# term decides. There is no principled winner between two records of one
# staging event; what there is, is a shipped answer, and this pass preserved
# it. The winner here IS bundle-order dependent, which is stated rather than
# hidden -- so this is the one case NOT driven as "same winner both ways".

_dup = [obs("American Joint Committee on Cancer stage IA (qualifier value)",
            _D_OLD),
        obs("Stage 1 (qualifier value)", _D_OLD)]
check("5a  the corpus's own duplicate-stamp pair resolves to one ordinal "
      "whichever record answers, which is why 290 flips were unobservable",
      (tier0(_dup), tier0(list(reversed(_dup)))), ((1, _D_OLD), (1, _D_OLD)))

# Displays that resolve DIFFERENTLY, so the winner is observable.
_dup_split = [obs("Stage I", _D_OLD), obs("Stage IV", _D_OLD)]
check("5b  on a full tie the FIRST record in the list answers -- the shipped "
      "answer, preserved",
      tier0(_dup_split), (1, _D_OLD))
check("5c  ...and reversing the list reverses the answer, because the "
      "position term is the last resort and position is all that is left",
      tier0(list(reversed(_dup_split))), (4, _D_OLD))
check("5d  the key negates the position, which is what makes 5b true under a "
      "descending sort",
      guarded(lambda: tuple_key(0, _dup_split[0])
              > tuple_key(1, _dup_split[1])), True)
check("5e  the key's three terms are (day, raw stamp, negated position)",
      tuple_key(3, obs("Stage I", _D_OLD)), ("2011-04-15", _D_OLD, -3))
check("5f  a missing date reaches the raw term as the empty string, never as "
      "None -- None is not orderable against a str",
      tuple_key(0, obs("Stage I", None)), ("0000-00-00", "", 0))
check("5g  every key is unique because the position is, so the ordering is "
      "TOTAL and does not rest on sort stability",
      guarded(lambda: len({tuple_key(i, o)
                           for i, o in enumerate(_dup + _dup)})), 4)


print()
print("=" * 70)
print("SECTION 6 -- the layering ruling the local twin rests on")
print("=" * 70)
_stage_ast = ast.parse(open(_STAGE_SRC, encoding="utf-8").read())
_imported = set()
for _node in ast.walk(_stage_ast):
    if isinstance(_node, ast.ImportFrom) and _node.module:
        _imported.add(_node.module)
    elif isinstance(_node, ast.Import):
        _imported.update(a.name for a in _node.names)

check("6a  stage.py imports NOTHING from oncotriage.registries -- the reason "
      "the twin exists rather than an import",
      sorted(m for m in _imported if m.startswith("oncotriage.registries")),
      [])
check("6b  NON-DEGENERACY for 6a, AND a tripwire in its own right: this is "
      "the COMPLETE set of intra-package imports a leaf extraction module "
      "has. It fails on an added edge of any kind, deliberately -- a leaf "
      "that grows one is a decision, not a detail",
      sorted(m for m in _imported if m.startswith("oncotriage.")),
      ["oncotriage.constants", "oncotriage.extraction.negation"])
check("6c  ...and the registry does not import extraction either, so no "
      "import could have been added in the other direction instead",
      [n.module for n in ast.walk(
          ast.parse(open(_REGISTRY_SRC, encoding="utf-8").read()))
       if isinstance(n, ast.ImportFrom) and n.module
       and n.module.startswith("oncotriage.extraction")],
      [])
check("6d  the registry's twin is still where the ruling says it is",
      callable(getattr(OncologyLabRegistry, "_date_sort_key", None)), True)

# THE M TIER WAS EXAMINED FOR THE SAME HAZARD AND IS NOT AFFECTED, and this is
# where that examination is recorded rather than left as a sentence. It does
# not SORT at all -- its rule is "any cM1 anywhere answers", argued at the
# tier -- so no ordering of its observations exists for a placeholder to
# corrupt. What 'unknown' can reach there is the returned DATE, and the render
# site guards that value; both halves are pinned below so that adding a sort
# to that tier has to be a decision rather than an oversight.
_m_fn = next((n for n in ast.walk(_stage_ast)
              if isinstance(n, ast.FunctionDef)
              and n.name == "_m_category_stage_with_date"), None)
check("6e  the M-category tier issues no sort, so the placeholder cannot "
      "misorder it -- examined, unchanged",
      guarded(lambda: [ast.unparse(n.func) for n in ast.walk(_m_fn)
                       if isinstance(n, ast.Call)
                       and ast.unparse(n.func) in ("sorted", "list.sort")]),
      [])
check("6f  NON-DEGENERACY: the walk did find that function and it does call "
      "things",
      guarded(lambda: len([n for n in ast.walk(_m_fn)
                          if isinstance(n, ast.Call)]) > 0), True)
check("6g  an undated cM1 still answers, reporting 'unknown' verbatim for "
      "oncotriage/agent/patient.py:_stage_date_clause to guard",
      guarded(lambda: extract_patient_stage_with_source(
          [], cancer_metastasis_observations=[{
              "code": "21907-1", "display": "", "value":
              "American Joint Committee on Cancer cM1 (qualifier value)",
              "unit": None, "date": "unknown",
              "metastasis_category": "M"}])[1:]),
      ("m_category_observation", "unknown"))


print()
print("=" * 70)
print("SECTION 7 -- planted controls")
print("=" * 70)
# Every plant goes into an in-memory COPY, never the file on disk, and each
# asserts its own occurrence count so a plant that matched nothing is a named
# PLANT-FAILED rather than a working check reported as broken.

_KEY_RETURN = "    return (_date_sort_key(raw), str(raw or ''), -index)\n"
_SLICE_BODY = ('    if not date_str or date_str == "unknown":\n'
               '        return "0000-00-00"\n')

# The CLEAN CONTROL runs first. Without it, a probe that disagreed with
# everything would report every plant as caught while measuring nothing.
_control("C0  CLEAN CONTROL: an unmutated copy gives the shipped answer",
         [], lambda m: tier0(_unknown_pair, m), (4, _D_NEW))

_control("C1  the raw-string sort reinstated -> the undated observation wins "
         "again, which is the whole defect",
         [(_KEY_RETURN,
           "    return (raw or '0000-00-00', str(raw or ''), -index)\n", 1)],
         lambda m: tier0(_unknown_pair, m), (1, "unknown"))

_control("C2  the placeholder mapping dropped from _date_sort_key -> the same "
         "defect, reached through the key instead of the tuple",
         [(_SLICE_BODY, "", 1)],
         lambda m: tier0(_unknown_pair, m), (1, "unknown"))

_control("C3  the position term left un-negated (the ECOG direction) -> the "
         "LAST record of a full tie answers, flipping 290 corpus patients",
         [(_KEY_RETURN,
           "    return (_date_sort_key(raw), str(raw or ''), index)\n", 1)],
         lambda m: tier0(_dup_split, m), (4, _D_OLD))
_control("C3-clean  ...and the shipped direction keeps the first one",
         [], lambda m: tier0(_dup_split, m), (1, _D_OLD))

_control("C4  the raw-stamp term dropped -> the same-day pair falls to "
         "position and stops being order-independent",
         [(_KEY_RETURN,
           "    return (_date_sort_key(raw), '', -index)\n", 1)],
         lambda m: tier0(list(reversed(_sameday_pair)), m),
         (4, "2024-03-01T20:00:00-05:00"))
_control("C4-b  ...measured from the other side: with the raw term dropped "
         "the winner now DEPENDS on list order",
         [(_KEY_RETURN,
           "    return (_date_sort_key(raw), '', -index)\n", 1)],
         lambda m: (tier0(_sameday_pair, m)
                    == tier0(list(reversed(_sameday_pair)), m)),
         False)
_control("C4-clean  ...and with it, the winner does NOT depend on list order",
         [],
         lambda m: (tier0(_sameday_pair, m)
                    == tier0(list(reversed(_sameday_pair)), m)),
         True)


print()
print("=" * 70)
print("SECTION 8 -- nothing on disk was touched")
print("=" * 70)
check("8a  stage.py is byte-identical to what it was before any plant",
      hashlib.sha256(
          open(_STAGE_SRC, encoding="utf-8").read().encode()).hexdigest(),
      _STAGE_SHA_BEFORE)
check("8b  cancer_code_registry.py is byte-identical too",
      hashlib.sha256(
          open(_REGISTRY_SRC, encoding="utf-8").read().encode()).hexdigest(),
      _REGISTRY_SHA_BEFORE)
check("8c  NON-DEGENERACY: those two comparisons are not one file hashed "
      "twice",
      _STAGE_SHA_BEFORE == _REGISTRY_SHA_BEFORE, False)


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
Created on Sun Aug 31 2026

@author: ramyalsaffar
"""
