# Stage 5 Patient Summary: the allergy onset date, and its hash coupling
#######################################################################

"""The date the prompt prints is the date the hash covers.

WHY THIS FILE EXISTS
--------------------
``oncotriage/fhir/parser.py`` has carried ``allergies[].onset_date`` since
allergies were parsed at all, and ``_create_patient_summary`` never printed it
-- so "no severe hypersensitivity reaction within the last 12 months", a real
oncology exclusion criterion, was unanswerable from a field the pipeline had
already read, and a stamp from 1930 reached the model looking exactly like one
from last month.

The date is rendered now, and THE HASH IS THE POINT RATHER THAN A SIDE EFFECT.
``compute_patient_hash`` excluded ``onset_date`` under an argument that was
correct while it held -- "no consumer reads it, so hashing it would move the
hash without moving the prompt", the ``value_shape`` mistake. Rendering the
date CREATES the consumer and reverses that argument exactly: leave it out and
two patients differing only in allergy onset render two different prompts under
one hash, which is the promise ``compute_patient_hash``'s own docstring makes.
Section 4 is that coupling, planted and caught.

WHAT IT HOLDS
-------------
    1. THE RENDERED LINE, in every state the field can be in, pinned as a WHOLE
       LINE. A substring test is satisfied by a date with an interval glued to
       it, which is the one thing the ruling forbids.
    2. ABSENCE RENDERS EXACTLY AS BEFORE THE FIELD WAS PRINTED, and that is
       established without a "before" image and without git. The pre-change
       line is ``' | '.join([display] + [category?] + [criticality?])`` and the
       post-change absence branch appends nothing to that list -- so "all five
       spellings of absence collapse to one text, that text carries no onset
       token, and the DATED line is that text plus one appended part" is the
       same claim, measured.
    3. NO ELAPSED INTERVAL, ANYWHERE, and no TEMPORAL_RENDER_COUNTS key. The
       ruling: every allergy stamp in this corpus resolves to a recordedDate
       decades old (measured -- 471 records, 19.1 to 99.1 years, median 73.1,
       none under five years), so an interval would be a near-constant restated
       on every allergy of every patient. Checked as an absence of the phrase
       AND as an absence of counter movement, because a renderer could compute
       one and discard it.
    4. THE COUPLING. Two records differing only in allergy onset must not share
       a hash. Driven on the shipped function, and PLANTED: a copy of
       ``oncotriage/agent/patient.py`` whose renderer prints the date while the
       hash ignores it -- the exact divergence this pass exists to prevent --
       is required to produce two identical hashes over two different prompts.
       The clean control runs the same probe against the shipped module.
    5. THE RAW/SLICED SPLIT. The line renders ``onset_date[:10]``; the hash
       takes the raw field, matching every sibling dated entry. Both halves
       driven: a perturbation of the time-of-day alone leaves the LINE
       byte-identical and MOVES the hash. That is the accepted converse of the
       value_shape rule, pinned so it stays a measurement somebody can revisit.
    6. ORDER IS UNTOUCHED. The rendered order is the parser's list order and
       the hashed order is ``_emit``'s sort of the whole line; neither may move
       under an appended field. Driven with allergies deliberately out of
       alphabetical order.

NO NETWORK, NO KEYS, NO SPEND, NO DATABASE, NO CORPUS, NO GIT, NO MODEL. Every
patient in here is a literal dict; the registries ``_create_patient_summary``
resolves are local file reads it makes itself. It writes NOTHING anywhere. NOT
in the collision matrix: the one repository file it reads,
``oncotriage/agent/patient.py``, is written by neither of the suite's two
writers and is sha256-compared at the end.

WHY IT EXECS. Section 4's plant is a one-token edit INSIDE ``compute_patient_
hash``'s f-string. There is no attribute to rebind for it, and ``git show``
cannot supply it either: at HEAD the hash is correct, and the revision that
lacks the render also lacks the divergence -- a blob would compare the fixed
module with itself. A patched in-memory copy is the shape CLAUDE.md prefers
over an in-place edit, and this file is an argued member of
``tests/test_package_invariants.py``'s ``_EXEC_ALLOWLIST``.

Run from terminal:
    python tests/test_agent_summary_allergy_onset.py

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

import hashlib
import types

from oncotriage import config as _config
from oncotriage.agent import patient as _patient_module
from oncotriage.agent.patient import (
    ALLERGY_ONSET_LABEL,
    BEFORE_REFERENCE_PHRASE,
    TEMPORAL_RENDER_COUNTS,
    _create_patient_summary,
    compute_patient_hash,
)


# EVERY DATE IN THIS FILE IS MEASURED AGAINST THIS SNAPSHOT, not the shipped
# constant and not the clock -- so section 3's "no interval" checks cannot pass
# because somebody moved DATA_SNAPSHOT_DATE. Restored in the cleanup block:
# `pytest tests/` imports every module into ONE process, and a leaked snapshot
# date would silently re-anchor every interval every later file measures.
_REAL_SNAPSHOT = _config.DATA_SNAPSHOT_DATE
_PATCHED_SNAPSHOT = "2026-08-03"
_config.DATA_SNAPSHOT_DATE = _PATCHED_SNAPSHOT

# THE COUNTER REGISTRY IS PROCESS-GLOBAL AND SECTION 3 DELIBERATELY MOVES IT.
# `pytest tests/` imports every module into ONE process, so a key this file
# added would still be there for every later file -- and this suite has files
# that read TEMPORAL_RENDER_COUNTS. Their checks are deltas or content scans, so
# nothing breaks today; the restore is the same discipline the snapshot date
# gets, applied before the state can matter rather than after it does. Taken
# HERE, above the first render, so it captures the registry as this file found
# it.
_COUNTS_AT_IMPORT = dict(TEMPORAL_RENDER_COUNTS)


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
    project has shipped that shape more than a dozen times.
    """
    try:
        return fn()
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        return f"raised {type(exc).__name__}: {exc}"


# THE PATH COMES FROM THE MODULE THIS PROCESS IMPORTED, never from this file's
# own location: moving the test cannot break it, and the source being planted
# into is provably the one under test rather than a same-named copy.
_PATIENT_SRC = os.path.abspath(_patient_module.__file__)
_SHA_AT_START = hashlib.sha256(
    open(_PATIENT_SRC, encoding="utf-8").read().encode()).hexdigest()


class _PlantFailed(Exception):
    """A plant that did not apply or did not compile. Never escapes a check."""


def _plant(name, subs):
    """Exec an in-memory COPY of the patient module with `subs` applied.

    Raises _PlantFailed -- never SyntaxError -- so a malformed plant is
    RECORDED as a failure instead of aborting the run and hiding every check
    below it. A control that takes the process down is not a control. The file
    on disk is hashed before and after and a modification is an AssertionError,
    because a control that edited the tree would be the defect it is testing.
    """
    source = open(_PATIENT_SRC, encoding="utf-8").read()
    before = hashlib.sha256(source.encode()).hexdigest()
    try:
        for old, new in subs:
            if old not in source:
                raise _PlantFailed(f"plant target absent: {old[:60]!r}...")
            source = source.replace(old, new, 1)
        module = types.ModuleType(name)
        module.__file__ = _PATIENT_SRC
        exec(compile(source, _PATIENT_SRC, "exec"), module.__dict__)
    except _PlantFailed:
        raise
    except Exception as exc:            # noqa: BLE001 - reported, not raised
        raise _PlantFailed(f"{type(exc).__name__}: {exc}") from None
    finally:
        after = hashlib.sha256(
            open(_PATIENT_SRC, encoding="utf-8").read().encode()).hexdigest()
        if before != after:
            raise AssertionError(f"{_PATIENT_SRC} was modified on disk")
    return module


_CONTROL_SEQ = [0]


def _control(label, subs, probe, expected):
    """Run a negative control against a COPY. A BAD PLANT IS A RECORDED
    FAILURE, not a crash."""
    _CONTROL_SEQ[0] += 1
    try:
        module = _plant(f"allergy_ctl_{_CONTROL_SEQ[0]}", subs)
    except _PlantFailed as exc:
        check(f"{label}  [THE PLANT ITSELF FAILED: {exc}]", "plant-failed",
              expected)
        return
    check(label, guarded(lambda: probe(module)), expected)


# ===========================================================================
# FIXTURES -- every one a literal
# ===========================================================================

# The stamp is deliberately a full ISO datetime with a time and a UTC offset,
# because that is what the corpus carries: all 471 allergy records resolve
# through _parse_allergy's fallback chain to a recordedDate of exactly this
# shape. A bare "1979-06-06" fixture would make section 5's raw/sliced split
# unmeasurable.
_STAMP = "1979-06-06T14:22:00-07:00"
_STAMP_SAME_DAY = "1979-06-06T23:59:59-07:00"   # differs ONLY after char 10
_STAMP_RENDERED = "1979-06-06"

_PENICILLIN = {"display": "Penicillin", "category": "medication",
               "criticality": "high", "onset_date": _STAMP}


def patient(allergies):
    """The minimum _create_patient_summary reads, plus the allergy list."""
    return {"patient_id": "probe-1",
            "demographics": {"age": 61, "sex": "female",
                             "birth_date": "1965-01-01", "race": "white",
                             "ethnicity": "nonhispanic"},
            "conditions": [], "observations": [], "medications": [],
            "procedures": [], "allergies": list(allergies),
            "cancer_stage_observations": [],
            "cancer_metastasis_observations": [],
            "cancer_genomic_variants": [],
            "ecog_performance_status": {}}


def render(allergies, module=_patient_module):
    return module._create_patient_summary(patient(allergies))


def section(text):
    """The Allergies section, sliced by its own heading and its successor."""
    start = text.index("\nAllergies:\n")
    return text[start:text.index("\nProcedures:", start)]


def clinical(text):
    """The summary MINUS the identity line.

    THE PSEUDONYM IS A FUNCTION OF compute_patient_hash, so it moves for ANY
    change to a hashed field -- including this one. Every check below that
    means "the clinical text did not move" has to say so explicitly, or it is
    really asserting "no hashed field changed", which is a different and much
    stronger claim that this pass deliberately makes false.
    """
    return "".join(ln for ln in text.splitlines(True)
                   if not ln.startswith("Patient: "))


def identity(text):
    """The identity line alone."""
    return next(ln for ln in text.splitlines() if ln.startswith("Patient: "))


def lines(allergies, module=_patient_module):
    """The Allergies section's rendered lines, without the heading."""
    return [ln for ln in section(render(allergies, module)).splitlines() if ln
            and ln != "Allergies:"]


def edit(record, **changes):
    out = dict(record)
    for key, value in changes.items():
        if value is _DROP:
            out.pop(key, None)
        else:
            out[key] = value
    return out


class _Drop:
    def __repr__(self):
        return "<drop the key entirely>"


_DROP = _Drop()


# ===========================================================================
# 1. THE RENDERED LINE, WHOLE, IN EVERY STATE THE FIELD CAN BE IN
# ===========================================================================

print("=" * 74)
print("1. the rendered line")
print("=" * 74)

_BASE_LINE = "- Penicillin | medication | criticality: high"
_DATED_LINE = f"{_BASE_LINE} | {ALLERGY_ONSET_LABEL}: {_STAMP_RENDERED}"

check("1a  a dated allergy renders the date as a labelled part, sliced to ten "
      "characters -- the whole line, not a substring of it",
      guarded(lambda: lines([_PENICILLIN])), [_DATED_LINE])

# THE FOUR SPELLINGS OF ABSENCE. "unknown" is the parser's own literal stand-in
# (the last arm of _parse_allergy's fallback chain) and is the one that must
# never reach the line as text -- the others are what a hand-built or
# non-Synthea record produces.
for _label, _value in (("the parser's literal 'unknown'", "unknown"),
                       ("an empty string", ""),
                       ("an explicit None", None),
                       ("the key missing entirely", _DROP)):
    check(f"1b  {_label}: the onset part is absent, the rest is untouched",
          guarded(lambda v=_value: lines([edit(_PENICILLIN, onset_date=v)])),
          [_BASE_LINE])

check("1c  the label never leaks as a bare token when there is nothing to "
      "label -- no 'onset:' with an empty or literal 'unknown' value",
      guarded(lambda: [ln for ln in
                       lines([edit(_PENICILLIN, onset_date="unknown")])
                       if ALLERGY_ONSET_LABEL in ln]), [])

check("1d  the date survives when category and criticality are the parser's "
      "'unknown', so it is not gated on a neighbouring part",
      guarded(lambda: lines([{"display": "Latex", "category": "unknown",
                              "criticality": "unknown",
                              "onset_date": "2003-01-02"}])),
      [f"- Latex | {ALLERGY_ONSET_LABEL}: 2003-01-02"])

check("1e  a patient with no allergies is unchanged: the section still states "
      "the absence rather than rendering an empty list",
      guarded(lambda: lines([])), ["- No known allergies"])


# ===========================================================================
# 2. ABSENCE RENDERS EXACTLY AS IT DID BEFORE THE FIELD WAS PRINTED
# ===========================================================================

print("\n" + "=" * 74)
print("2. absence is byte-identical to the pre-change render")
print("=" * 74)

# HOW THIS IS ESTABLISHED WITHOUT A BEFORE IMAGE, AND WHY THAT IS NOT A
# WEAKENING. The pre-change line was ' | '.join([display] + [category?] +
# [criticality?]); the post-change absence branch appends nothing to that same
# list. So the pre-change text is fully determined by the parts that did not
# change, and the three checks below are the same claim as a diff against it:
#   (i)   every spelling of absence produces ONE text (2a),
#   (ii)  that text carries no onset token (1c),
#   (iii) the DATED text is that same text plus exactly one appended part (2b).
# A git blob would have been the other route and this file deliberately needs
# no git history -- three files in this suite abort without `.git`.
_ABSENT_RENDERS = {
    _label: guarded(lambda v=_value: render([edit(_PENICILLIN, onset_date=v)]))
    for _label, _value in (("unknown", "unknown"), ("empty", ""),
                           ("none", None), ("missing", _DROP))
}
check("2a  all four spellings of absence produce ONE clinical text, byte for "
      "byte",
      len({clinical(r) for r in _ABSENT_RENDERS.values()}), 1)
check("2a  non-degeneracy: there really were four renders, so 2a is not "
      "collapsing an empty or single-member set",
      len(_ABSENT_RENDERS), 4)

# THE ONE ASYMMETRY, RECORDED RATHER THAN FIXED, and the first draft of 2a
# asserted the whole SUMMARY and failed on it -- correctly. The hash emits
# `a.get('onset_date') or ''`, so the parser's literal "unknown" hashes as the
# string "unknown" while an empty, None or absent value hashes as "". Four
# records that render identically therefore do not all hash identically, and
# the pseudonym shows it.
#
# NOT FIXED, for three reasons argued together. (i) It is the SAME converse
# violation section 5 already accepts deliberately and with a much larger
# reach: hashing the raw stamp means every time-of-day change moves the hash
# over an unchanged line. Normalising the sentinel while keeping that would be
# fixing the small case and keeping the large one. (ii) Every sibling dated
# entry -- `proc`, `met`, `obs`, the stage observations -- has the identical
# asymmetry, and one collection normalising where the rest do not is a worse
# thing to have to remember than the asymmetry itself. (iii) IT IS UNREACHABLE
# IN PRODUCTION: _parse_allergy always writes the key, as a stamp or as the
# literal "unknown", so ""/None/missing come only from a hand-built record --
# and measured over the 1,000-bundle corpus, all 471 allergy records are dated
# and none is "unknown". A normalisation would be untested-in-production
# machinery guarding a state the parser cannot produce.
check("2a  the recorded asymmetry: 'unknown' and an absent key render the same "
      "clinical text and hash differently -- accepted, argued above, and "
      "unreachable from _parse_allergy",
      guarded(lambda: (
          clinical(_ABSENT_RENDERS["unknown"])
          == clinical(_ABSENT_RENDERS["missing"]),
          compute_patient_hash(patient([edit(_PENICILLIN, onset_date="unknown")]))
          == compute_patient_hash(patient([edit(_PENICILLIN,
                                                onset_date=_DROP)])))),
      (True, False))

_ABSENT = _ABSENT_RENDERS["unknown"]
_DATED = guarded(lambda: render([_PENICILLIN]))
check("2b  the dated clinical text is the undated one with exactly one part "
      "appended to the allergy line -- the change is purely additive",
      clinical(_DATED),
      clinical(_ABSENT).replace(_BASE_LINE,
                                f"{_BASE_LINE} | {ALLERGY_ONSET_LABEL}: "
                                f"{_STAMP_RENDERED}"))
check("2b  non-degeneracy: the two really do differ, so 2b is not comparing a "
      "string with itself",
      _DATED != _ABSENT, True)
# WHAT MOVES BETWEEN THE TWO RENDERS IS EXACTLY TWO THINGS, and the second one
# is the coupling this pass exists to create rather than noise. The allergy
# section is the one this file is about. The `Patient:` pseudonym is DERIVED
# from compute_patient_hash, which hashes onset_date now -- so a record that
# gains or changes an onset MUST move it. The first draft of this check asserted
# that nothing outside the allergy section moved and FAILED, correctly; the
# claim was wrong, not the code.
check("2c  outside the allergy section and the identity line, the two renders "
      "are byte-identical",
      clinical(_DATED).replace(section(_DATED), "")
      == clinical(_ABSENT).replace(section(_ABSENT), ""), True)
check("2c  ...and the identity line DOES move, which is the hash coupling "
      "showing through into the prompt rather than a defect",
      guarded(lambda: identity(_DATED) != identity(_ABSENT)), True)


# ===========================================================================
# 3. NO ELAPSED INTERVAL, AND NO TEMPORAL COUNTER
# ===========================================================================

print("\n" + "=" * 74)
print("3. the ruling: a raw date and no interval")
print("=" * 74)

# THE RULING, AND THE MEASUREMENT BEHIND IT. All 471 allergy onsets in this
# corpus are recordedDate paperwork stamps 19.1 to 99.1 years old (median 73.1,
# none under five years), so an elapsed phrase would render a near-constant
# "decades before reference date" on every allergy of every patient. This
# section is what stops a future pass helpfully adding the suffix every other
# dated section carries -- an undocumented deliberate absence reads as an
# oversight, and a check is the only form of documentation that fails.
check("3a  the allergy line states no elapsed phrase",
      BEFORE_REFERENCE_PHRASE in _DATED_LINE, False)
# NON-DEGENERACY BY DEMONSTRATION, not by assertion: the phrase 3a looks for is
# shown to be one this renderer really emits, by rendering a PROCEDURE -- a
# section with the ordinary treatment -- through the same code path. Without
# this, 3a would pass just as happily against a misspelled constant.
_WITH_PROC = dict(patient([_PENICILLIN]),
                  procedures=[{"display": "Biopsy of breast (procedure)",
                               "code": "122548005", "date": "2026-01-01",
                               "status": "completed"}])
_PROC_RENDER = guarded(lambda: _create_patient_summary(_WITH_PROC))
check("3a  non-degeneracy: the phrase this looks for is one the renderer "
      "really emits -- a procedure line in the same render carries it",
      (BEFORE_REFERENCE_PHRASE in _PROC_RENDER,
       BEFORE_REFERENCE_PHRASE in section(_PROC_RENDER)), (True, False))

# A COUNTER IS A SECOND, INDEPENDENT WITNESS, and it has to be driven with an
# UNREADABLE date to be one. _resolve_temporal_date counts on two branches only
# -- `{key}_unreadable:{precision}` and `{key}_after_reference` -- and returns
# silently on the happy path, so a well-formed date moves nothing whatever the
# renderer does with it. The first draft probed with the ORDINARY stamp and its
# non-degeneracy control failed, correctly: over a resolvable date the check
# could not have told a renderer that computes an interval from one that does
# not.
#
# So the probe is an UNPARSEABLE onset. A renderer that reached the temporal
# machinery for this field would count it under an allergy-shaped key; with no
# TEMPORAL_KEY_* and no _dated_suffix call, nothing is counted at all.
# Snapshotting the WHOLE registry catches a key added under ANY name, which a
# check for one expected name could not.
_BAD_DATE = "not-a-date"
_COUNTS_BEFORE = dict(TEMPORAL_RENDER_COUNTS)
_BAD_LINES = guarded(lambda: lines([edit(_PENICILLIN, onset_date=_BAD_DATE)]))
check("3b  an UNPARSEABLE allergy onset moves no TEMPORAL_RENDER_COUNTS key "
      "under any name, so the temporal machinery is not reached for this field",
      {k: v for k, v in TEMPORAL_RENDER_COUNTS.items()
       if _COUNTS_BEFORE.get(k, 0) != v}, {})

# NON-DEGENERACY BY THE SAME PROBE ON A FIELD THAT DOES HAVE A KEY. Without it,
# 3b would pass in a process where the registry never moves for anything.
_COUNTS_MID = dict(TEMPORAL_RENDER_COUNTS)
_BAD_PROC = dict(_WITH_PROC,
                 procedures=[dict(_WITH_PROC["procedures"][0], date=_BAD_DATE)])
_ = guarded(lambda: _create_patient_summary(_BAD_PROC))
check("3b  non-degeneracy: the identical probe over an unparseable PROCEDURE "
      "date DOES move a key, so 3b measures this field rather than a registry "
      "that never moves",
      sorted(k for k, v in TEMPORAL_RENDER_COUNTS.items()
             if _COUNTS_MID.get(k, 0) != v),
      ["procedure_date_unreadable:unparseable"])

# AND THE UNPARSEABLE DATE IS STILL RENDERED, WHICH IS THE RULING WORKING
# RATHER THAN A GAP. This section prints what the record carries and computes
# nothing from it, so an onset the temporal machinery could not read is passed
# through as-is. That is the same treatment `criticality` gets: the renderer is
# not a validator. Pinned so the behaviour is a decision on the record.
check("3c  an unparseable onset is rendered verbatim (sliced), because this "
      "section prints the field and derives nothing from it",
      _BAD_LINES, [f"{_BASE_LINE} | {ALLERGY_ONSET_LABEL}: {_BAD_DATE}"])


# ===========================================================================
# 4. THE COUPLING -- THE POINT OF THE PASS
# ===========================================================================

print("\n" + "=" * 74)
print("4. render and hash cannot disagree")
print("=" * 74)


def _divergence(module):
    """(the two prompts differ, the two hashes differ) for two records that
    differ ONLY in allergy onset.

    A HASH THAT IGNORES A RENDERED FIELD PRODUCES (True, False), which is the
    defect: two different prompts under one hash.
    """
    a = patient([_PENICILLIN])
    b = patient([edit(_PENICILLIN, onset_date="2011-11-11T00:00:00-08:00")])
    # THE CLINICAL TEXT, NOT THE WHOLE SUMMARY. The identity line is derived
    # from the hash, so on the shipped module it moves whenever the hash does
    # -- and comparing whole summaries would make the first member of this
    # tuple true for every hashed field, whatever the renderer printed. That is
    # not a hypothetical: the first draft of 4c measured exactly that and
    # reported a working mirror plant as broken.
    return (clinical(module._create_patient_summary(a))
            != clinical(module._create_patient_summary(b)),
            module.compute_patient_hash(a) != module.compute_patient_hash(b))


check("4a  THE CLEAN CONTROL: on the shipped module the two records render "
      "different prompts AND hash differently",
      guarded(lambda: _divergence(_patient_module)), (True, True))

# THE PLANT. One field dropped from compute_patient_hash's allergy f-string,
# leaving the renderer untouched -- which is exactly the state this pass was
# written to prevent and exactly the state HEAD was in before it. It cannot
# come from `git show`: the revision that lacks this line in the hash also
# lacks it in the renderer, so a blob would produce (False, False) and measure
# nothing about the coupling.
_control("4b  THE PLANT: a hash that ignores the rendered onset produces two "
         "different prompts under ONE hash",
         [("        f\"|{a.get('onset_date') or ''}\"\n", "")],
         _divergence, (True, False))

# THE MIRROR IMAGE, for completeness of the pair: a RENDERER that stops
# printing the date while the hash keeps it. Not a defect in the same sense --
# no prompt is misattributed -- but it is the other way the two can come apart,
# and pinning both is what says section 4 is about the COUPLING rather than
# about the hash alone.
_control("4c  THE MIRROR: a renderer that stops printing the date leaves the "
         "prompts identical while the hashes still differ",
         [("                parts.append(f\"{ALLERGY_ONSET_LABEL}: "
           "{onset[:10]}\")\n", "                pass\n")],
         _divergence, (False, True))


# ===========================================================================
# 5. THE RAW STAMP IS HASHED; THE SLICE IS RENDERED
# ===========================================================================

print("\n" + "=" * 74)
print("5. raw versus sliced")
print("=" * 74)

_A = patient([_PENICILLIN])
_B = patient([edit(_PENICILLIN, onset_date=_STAMP_SAME_DAY)])

check("5a  a perturbation of the time-of-day alone leaves the rendered "
      "CLINICAL text byte-identical -- the renderer reads onset_date[:10]",
      guarded(lambda: (clinical(_create_patient_summary(_A))
                       == clinical(_create_patient_summary(_B)),
                       lines([_PENICILLIN])[0])),
      (True, _DATED_LINE))
check("5b  ...and MOVES the hash, because the raw field is hashed, matching "
      "every sibling dated entry. The accepted converse of the value_shape "
      "rule, pinned so it stays a measurement rather than a claim",
      guarded(lambda: compute_patient_hash(_A) != compute_patient_hash(_B)), True)
check("5b  non-degeneracy: the two stamps really do share their first ten "
      "characters, so 5a is not passing because nothing changed",
      (_STAMP[:10] == _STAMP_SAME_DAY[:10], _STAMP == _STAMP_SAME_DAY),
      (True, False))

# THE FULL COST OF HASHING THE RAW STAMP, STATED RATHER THAN LEFT TO BE
# DISCOVERED. The hash moves, so the PSEUDONYM moves, so the rendered summary
# is not byte-identical after all -- one line of it, and that line carries no
# clinical fact. A re-serialisation that rewrites only a stamp's UTC offset
# therefore produces a new pseudonym for an unchanged patient. Accepted for the
# reason argued at the hash entry (four sibling entries already hash raw dates
# and one collection differing is worse to remember), and pinned here so it is
# a measurement a future pass can revisit rather than a surprise.
check("5c  the stated cost: the same perturbation moves the pseudonym, and "
      "therefore one line of the summary, while no clinical line moves",
      guarded(lambda: (identity(_create_patient_summary(_A))
                       != identity(_create_patient_summary(_B)),
                       _create_patient_summary(_A)
                       != _create_patient_summary(_B))), (True, True))


# ===========================================================================
# 6. ORDER IS UNTOUCHED
# ===========================================================================

print("\n" + "=" * 74)
print("6. rendered order and hashed order")
print("=" * 74)

# THE RENDERED ORDER IS THE PARSER'S LIST ORDER and nothing here sorts it. The
# fixture is deliberately NOT alphabetical, so a renderer that started sorting
# would fail rather than agree by accident.
_THREE = [
    {"display": "Wheat (substance)", "category": "food",
     "criticality": "low", "onset_date": "1990-01-01"},
    {"display": "Aspirin", "category": "medication",
     "criticality": "low", "onset_date": "unknown"},
    {"display": "Latex", "category": "environment",
     "criticality": "high", "onset_date": "2001-02-03T10:00:00-08:00"},
]
check("6a  list order is preserved and each line is independently dated or "
      "not, with the undated one rendering exactly as it always did",
      guarded(lambda: lines(_THREE)),
      [f"- Wheat (substance) | food | criticality: low | "
       f"{ALLERGY_ONSET_LABEL}: 1990-01-01",
       "- Aspirin | medication | criticality: low",
       f"- Latex | environment | criticality: high | "
       f"{ALLERGY_ONSET_LABEL}: 2001-02-03"])

# THE HASHED ORDER IS _emit's SORT OF THE WHOLE LINE, and onset_date is
# appended LAST -- so two allergies could only reorder by tying on display,
# category and criticality, which File 07's deduplicate_by_display makes
# impossible. Driven rather than argued: the hash is invariant under a
# permutation of the input list, and it was before this field existed.
check("6b  the hash is invariant under a permutation of the allergy list, so "
      "the appended field did not make the sort key order-dependent",
      guarded(lambda: compute_patient_hash(patient(_THREE))
              == compute_patient_hash(patient(list(reversed(_THREE))))), True)
check("6b  non-degeneracy: the permuted list really is a different object "
      "order, and the RENDER does move under it",
      guarded(lambda: lines(_THREE) != lines(list(reversed(_THREE)))), True)


# ===========================================================================
# 7. CLEANUP
# ===========================================================================

print("\n" + "=" * 74)
print("7. cleanup")
print("=" * 74)

_config.DATA_SNAPSHOT_DATE = _REAL_SNAPSHOT
check("7a  config.DATA_SNAPSHOT_DATE is restored",
      _config.DATA_SNAPSHOT_DATE, _REAL_SNAPSHOT)
check("7a  ...and the restore is not a no-op, so the pin above did work",
      _REAL_SNAPSHOT == _PATCHED_SNAPSHOT
      or _config.DATA_SNAPSHOT_DATE != _PATCHED_SNAPSHOT, True)
check("7b  oncotriage/agent/patient.py is byte-identical: every plant went "
      "into an in-memory copy",
      hashlib.sha256(
          open(_PATIENT_SRC, encoding="utf-8").read().encode()).hexdigest(),
      _SHA_AT_START)
check("7c  the controls really ran, so section 4's plants are not silently "
      "absent",
      _CONTROL_SEQ[0], 2)

_COUNTS_BEFORE_RESTORE = dict(TEMPORAL_RENDER_COUNTS)
TEMPORAL_RENDER_COUNTS.clear()
TEMPORAL_RENDER_COUNTS.update(_COUNTS_AT_IMPORT)
check("7d  TEMPORAL_RENDER_COUNTS is restored to what this file found, so a "
      "single-process run of the whole suite sees no key this file added",
      dict(TEMPORAL_RENDER_COUNTS), _COUNTS_AT_IMPORT)
check("7d  ...and the restore is not a no-op: section 3 really did move the "
      "registry, so 7d is undoing something",
      _COUNTS_BEFORE_RESTORE != _COUNTS_AT_IMPORT, True)


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
# Created on 2026-08-31
#------------------------------------------------------------------------------
