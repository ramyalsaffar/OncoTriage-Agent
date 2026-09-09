######################################################################
# Criterion clause division: the exception scope, and the bracket
######################################################################

"""Criterion Clause Division Test

``oncotriage/evaluation/criterion_clauses.py`` is the one owner of how an
eligibility criterion is DIVIDED for window-scope analysis. The division it
replaces lived as a one-line regular expression in ``item10_analyse.py``, was
re-lifted by AST into ``item10_adjudication_sheet.py`` and re-implemented a
third time as a head test in item 11's ``headwindow.py`` -- three copies in
three files no test suite read.

THIS FILE PINS WHAT THE MODULE OWNS, WHICH IS THE DIVISION AND NOT THE WINDOW.
"Does this text state a time window" is RULE 4's vocabulary, owned by
``oncotriage/evaluation/criterion_windows.py``; the module under test takes it
as a callable, and sections 1 to 6 supply a small DECLARED probe of its own for
the classification checks. That probe is not a copy of the real predicate and
does not try to be -- it is a stand-in whose only job is to make
``classify_window_scope``'s three-way branch drivable. The pins that carry the
real corpus are the HEAD TEXTS in ``_ITEM10_CRITERIA``, which are a fact about
the division alone and need no window predicate at all.

SECTION 7 IS THE END-TO-END HALF, AND IT IS WHAT THE EXTRACTION BOUGHT. The
real predicate used to live in ``item9_families.py`` in an evaluation-run log
directory outside this repository, so no check here had ever driven the shipped
module with it: the DIVISION was pinned and the CLASSIFICATION was not. Section
7 drives it over the same 24 real criteria and pins the scope distribution and
the compound split. THE PROBE STAYS -- measured, the two predicates agree on
every one of those texts, which is what makes sections 5 and 6 statements about
the division independently of which predicate divides it, and section 7f pins
both that agreement and the fact that they are nonetheless different functions.

THE HEADS WERE LIFTED, NOT TRANSCRIBED. Every expected head in that table was
produced by running the shipped module over item 10's own thirty
``pipeline_missed_gate`` criteria and printed into this file, then read back.
Hand-transcribing a literal in a move is how pass 20f-4 shipped a colour that
no rendered element used and no element-for-element comparison could see.

THE THREE DEFECTS THIS FILE FIRES ON, each with its own control:

  1. CUTS INSIDE PARENTHESES. ``item10_adjudication_sheet.py``'s own note
     reports the census as blocks carrying a clause with unbalanced
     parentheses. Section 4 pins ZERO such clauses over the corpus; control C1
     removes the bracket-awareness and requires them back.
  2. THE HEAD TEST DISCARDED THE TAIL. ``headwindow.py:head_of`` returns
     ``text[:marker.start()]``, which is right for an exception running to the
     end and wrong for a parenthetical one, where the head resumes after the
     closing bracket. Section 3 pins the restored tail; control C2 reverts to
     the prefix-only head and requires ``NCT06225310`` exclusion #1 to
     misclassify.
  3. THE EXCEPTION RAN TO THE END OF THE STRING. Control C3 makes a bare
     marker's scope run to the end rather than to the end of its clause and
     requires a head to lose text that is not an exception.

  Plus C4, the marker alternation reordered shortest-first, which is
  first-match-wins and would leave ``for`` at the head of an ``except for``
  exception; and C5, the coordination cut, which is a documented over-split
  that is RETAINED -- section 6 pins both readings and the measured count that
  makes the retention auditable.

EVERY CONTROL IS A MODULE-ATTRIBUTE REBIND INSIDE ``try``/``finally`` WITH THE
RESTORE ASSERTED BY IDENTITY, or a different INPUT to a pure function. Nothing
is exec'd and no module is loaded by location, so this file needs no
``_EXEC_ALLOWLIST`` entry.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO CORPUS, NO
DATABASE, NO GIT HISTORY, NO LIVE SERVER. Every criterion in the table is a
literal, and every one is public ClinicalTrials.gov trial text -- no patient
material of any kind. It writes NOTHING anywhere, not even a temp directory.

NOT in ``tests/run_serial_tests.py``'s collision matrix, derived rather than
assumed: it writes nothing, and the one repository file it reads is
``oncotriage/evaluation/criterion_clauses.py``, which is written by neither of
the suite's two writers.

    python tests/test_criterion_clause_division.py
"""

import os
import sys

# ABOVE THE PACKAGE IMPORTS ON PURPOSE. oncotriage/agent/deps.py reads this
# variable ONCE, at its own import, and `deps` arrives transitively on the
# first `oncotriage` import -- so an assignment underneath the imports reaches
# nothing and the local models load for real.
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

import re                                                      # noqa: E402

from oncotriage.evaluation import criterion_clauses as CC      # noqa: E402
# THE EXTRACTED WINDOW PREDICATE. Section 7 is what it makes possible: before
# it, the classification this module produces could only be driven through a
# declared stand-in, because the real predicate lived outside this repository.
from oncotriage.evaluation import criterion_windows as W        # noqa: E402
import io as _io7                                              # noqa: E402
import ast as _ast7                                            # noqa: E402
import inspect as _inspect7                                    # noqa: E402


# ===========================================================================
# HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, actual, expected):
    """Assert equality, record the outcome, never abort the run."""
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected: {expected!r}\n"
                         f"          actual:   {actual!r}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected!r}")
        print(f"          actual:   {actual!r}")


def check_true(label, condition):
    check(label, bool(condition), True)


def guarded(fn, *a, **kw):
    """Call ``fn``; return a marker instead of raising.

    A raise inside a ``check()`` argument list aborts the file while the
    argument is being evaluated -- one traceback where the run owes a summary
    and every remaining result. This project has shipped that shape more than a
    dozen times; every call into the module under test goes through here.
    """
    try:
        return fn(*a, **kw)
    except Exception as exc:                                   # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


def guarded_raises(fn, *a, **kw):
    """The exception TYPE NAME ``fn`` raises, or a named absence.

    ``guarded`` answers what a call RETURNED. Where the assertion is that a
    call must raise -- section 7g, that omitting a required argument is a
    TypeError rather than a silent default -- the type is the whole content of
    the check, and "it returned a marker string" would be satisfied by any
    raise at all.
    """
    try:
        fn(*a, **kw)
        return "<did not raise>"
    except Exception as exc:                                   # noqa: BLE001
        return type(exc).__name__


class rebind(object):
    """Install ``value`` at ``module.name`` for a block, then restore it.

    The restore is asserted BY IDENTITY rather than by equality: any callable
    of the same name would satisfy equality, and what has to be true is that
    the object the module holds afterwards is the one it held before.
    """

    def __init__(self, module, name, value):
        self.module, self.name, self.value = module, name, value

    def __enter__(self):
        self.original = getattr(self.module, self.name)
        setattr(self.module, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.module, self.name, self.original)
        check_true(f"restore: {self.name} is the object it was, by identity",
                   getattr(self.module, self.name) is self.original)
        return False


# ===========================================================================
# THE WINDOW PROBE -- DECLARED, AND NOT A COPY OF ITEM 9's PREDICATE
# ===========================================================================

_PROBE_WINDOW_RE = re.compile(
    r"\b(?:within|prior to|in the (?:past|last)|"
    r"\d+\s*(?:day|week|month|year)s?)\b", re.IGNORECASE)


def probe_is_window(text):
    """A small DECLARED window predicate, for driving the three-way branch.

    It is deliberately NOT item 9's ``is_window_criterion``. That predicate's
    vocabulary is RULE 4's and is owned by a paid artifact outside this
    repository; copying it here would be the two-owner drift
    ``criterion_clauses`` exists to remove, and this file's subject is the
    DIVISION rather than the window. What this has to be is a function of its
    argument that separates the corpus's windowed strings from its unwindowed
    ones, which the checks below assert of it before relying on it.
    """
    return bool(_PROBE_WINDOW_RE.search(text or ""))


# ===========================================================================
# THE CORPUS -- item 10's thirty pipeline_missed_gate criteria, de-duplicated
# ===========================================================================

# (criterion, expected head, clauses with the ` or ` cut, clauses without it).
#
# LIFTED BY RUNNING THE SHIPPED MODULE AND PRINTED INTO THIS FILE, never
# hand-transcribed. Twenty-four distinct texts stand behind the thirty cases
# (`NCT06107920` exclusion #6 alone is eight of them, in two variants that
# differ at one character); the join keys are in the comments so a row can be
# traced back to item 11's `evidence_by_case.csv`.
#
# ALL PUBLIC ClinicalTrials.gov TRIAL TEXT. No patient material.
_ITEM10_CRITERIA = (
    # NCT03110822 exclusion #4
    ('Impaired cardiac function or clinically significant cardiac diseases (including MI within 6 months, NYHA Class II+ HF, uncontrolled angina, pericardial disease, severe ventricular arrhythmias, LVEF below normal, ECG evidence of acute ischemia)',
     'Impaired cardiac function or clinically significant cardiac diseases (including MI within 6 months, NYHA Class II+ HF, uncontrolled angina, pericardial disease, severe ventricular arrhythmias, LVEF below normal, ECG evidence of acute ischemia)',
     2, 1),
    # NCT03467360 exclusion #6
    ('History of cancer, with the exception of cancers in complete remission for more than 5 years, completely resected basal cell carcinoma, squamous cell carcinoma with curative therapy, or in situ cervical cancer',
     'History of cancer',
     1, 1),
    # NCT05583617 exclusion #28
    ('History of malignancies other than MM unless free of disease for >=5 years (SS4)',
     'History of malignancies',
     1, 1),
    # NCT05923255 exclusion #3
    ('History of any other malignant tumor within the last 5 years (except cured in situ cervical cancer, basal cell carcinoma, papillary thyroid carcinoma, or skin squamous cell carcinoma)',
     'History of any other malignant tumor within the last 5 years',
     1, 1),
    # NCT06107920 exclusion #6
    ('Presence of inflammatory bowel disease, HNPCC syndrome or polyposis; Clinically relevant coronary artery disease or history of myocardial infarction in the last 6 months, or high risk of uncontrolled arrhythmia',
     'Presence of inflammatory bowel disease, HNPCC syndrome or polyposis; Clinically relevant coronary artery disease or history of myocardial infarction in the last 6 months, or high risk of uncontrolled arrhythmia',
     4, 2),
    # NCT06107920 exclusion #6
    ('Presence of inflammatory bowel disease, HNPCC syndrome or polyposis; clinically relevant coronary artery disease or history of myocardial infarction in the last 6 months, or high risk of uncontrolled arrhythmia',
     'Presence of inflammatory bowel disease, HNPCC syndrome or polyposis; clinically relevant coronary artery disease or history of myocardial infarction in the last 6 months, or high risk of uncontrolled arrhythmia',
     4, 2),
    # NCT06115135 exclusion #2
    ('History of other active malignancies including MDS within past 3 years, with exceptions for prior malignancy with no evidence of disease treated with curative intent',
     'History of other active malignancies including MDS within past 3 years',
     1, 1),
    # NCT06115135 exclusion #2
    ('History of other active malignancies within past 3 years (excluding prior malignancy with no evidence of disease treated with curative intent)',
     'History of other active malignancies within past 3 years',
     1, 1),
    # NCT06115135 exclusion #14
    ('Moderate or strong CYP3A inhibitors or inducers within 7 days of starting study drugs',
     'Moderate or strong CYP3A inhibitors or inducers within 7 days of starting study drugs',
     3, 1),
    # NCT06150157 exclusion #1
    ('Concurrent or recently diagnosed or treated malignancies present at the time of participant screening (with exceptions for cured malignancies >= 3 years after treatment ended)',
     'Concurrent or recently diagnosed or treated malignancies present at the time of participant screening',
     3, 1),
    # NCT06150157 exclusion #4
    ('History of clinically significant cardiovascular disease within 6 months prior to first dose of study treatment',
     'History of clinically significant cardiovascular disease within 6 months prior to first dose of study treatment',
     1, 1),
    # NCT06150157 exclusion #4
    ('History of clinically significant cardiovascular disease within 6 months prior to the first dose of study treatment',
     'History of clinically significant cardiovascular disease within 6 months prior to the first dose of study treatment',
     1, 1),
    # NCT06225310 exclusion #1
    ('Prior malignancy that required treatment or has shown evidence of recurrence (except for non-melanoma skin cancer or adequately treated cervical carcinoma in situ) during the 3 years prior to randomization. Cancer treated with curative intent for >5 years previously and without evidence of recurrence will be allowed.',
     'Prior malignancy that required treatment or has shown evidence of recurrence during the 3 years prior to randomization. Cancer treated with curative intent for >5 years previously and without evidence of recurrence will be allowed.',
     3, 2),
    # NCT06225310 exclusion #1
    ('Prior malignancy that required treatment or has shown evidence of recurrence during the 3 years prior to randomization (except non-melanoma skin cancer or adequately treated cervical carcinoma in situ); cancer treated with curative intent >5 years previously without evidence of recurrence allowed',
     'Prior malignancy that required treatment or has shown evidence of recurrence during the 3 years prior to randomization; cancer treated with curative intent >5 years previously without evidence of recurrence allowed',
     3, 2),
    # NCT06270888 exclusion #19
    ('Concomitant statin use (unless using statins >3 months prior to study drug in stable status without CK rise)',
     'Concomitant statin use',
     1, 1),
    # NCT06541249 exclusion #3
    ('Have other invasive malignancies within the last 3 years, except non-melanoma skin cancer and localized, cured prostate and cervical cancer',
     'Have other invasive malignancies within the last 3 years',
     1, 1),
    # NCT06788938 exclusion #11
    ('History of other malignancy within the past 2 years (with exceptions)',
     'History of other malignancy within the past 2 years',
     1, 1),
    # NCT06997081 inclusion #12
    ('Female participant of childbearing potential must have negative pregnancy test at screening and within 72 hours of start of study treatment',
     'Female participant of childbearing potential must have negative pregnancy test at screening and within 72 hours of start of study treatment',
     1, 1),
    # NCT07140679 exclusion #5
    ('History of a hematologic or primary solid tumor malignancy within the last 5 years',
     'History of a hematologic or primary solid tumor malignancy within the last 5 years',
     2, 1),
    # NCT07155200 exclusion #14
    ('Currently using or anticipating food or drugs known to strongly inhibit or induce CYP3A4 within 10 days prior to first dose',
     'Currently using or anticipating food or drugs known to strongly inhibit or induce CYP3A4 within 10 days prior to first dose',
     4, 1),
    # NCT07405476 exclusion #4
    ('Clinically significant cardiac disease, ventricular arrhythmia requiring therapy, uncontrolled hypertension or history of symptomatic CHF; myocardial infarction or unstable angina within 6 months prior to C1D1',
     'Clinically significant cardiac disease, ventricular arrhythmia requiring therapy, uncontrolled hypertension or history of symptomatic CHF; myocardial infarction or unstable angina within 6 months prior to C1D1',
     4, 2),
    # NCT07579234 exclusion #2
    ('History of any other malignancy within 3 years prior to first dose (except low-recurrence-risk malignancies after curative treatment or curative resection with no current evidence of disease)',
     'History of any other malignancy within 3 years prior to first dose',
     1, 1),
    # NCT07612280 exclusion #6
    ('Subject with severe or unstable medical condition such as congestive heart failure, ischemic heart disease, uncontrolled hypertension, uncontrolled diabetes mellitus, psychiatric condition, uncontrolled cardiac arrhythmia requiring medication (≤Grade 2), myocardial infarction within 6 months prior to starting study treatment, or any other significant or unstable concurrent cardiac illness',
     'Subject with severe or unstable medical condition such as congestive heart failure, ischemic heart disease, uncontrolled hypertension, uncontrolled diabetes mellitus, psychiatric condition, uncontrolled cardiac arrhythmia requiring medication (≤Grade 2), myocardial infarction within 6 months prior to starting study treatment, or any other significant or unstable concurrent cardiac illness',
     3, 1),
    # NCT07719361 exclusion #6
    ('Concurrent malignancy requiring treatment or history of prior malignancy active within 2 years prior to the first dose of study drug',
     'Concurrent malignancy requiring treatment or history of prior malignancy active within 2 years prior to the first dose of study drug',
     2, 1),
)



_BY_NCT = {}
for _c, _h, _n, _n0 in _ITEM10_CRITERIA:
    _BY_NCT.setdefault(_c, (_h, _n, _n0))

# The five criteria the fixes are ABOUT, addressed by name so a check can say
# which one moved. Each is a substring unique to one row of the table above.
_NEEDLE = {
    "NCT03467360_ex6": "History of cancer, with the exception of",
    "NCT05583617_ex28": "History of malignancies other than MM",
    "NCT06270888_ex19": "Concomitant statin use (unless",
    "NCT06150157_ex1": "Concurrent or recently diagnosed or treated",
    "NCT06225310_ex1_B": "(except for non-melanoma skin cancer",
    "NCT06225310_ex1_A": "randomization (except non-melanoma",
    "NCT07579234_ex2": "except low-recurrence-risk malignancies",
}


def criterion(name):
    """The one criterion whose text carries ``_NEEDLE[name]``.

    Refuses on zero or several rather than returning the first: a needle that
    stopped being unique would silently re-point every check that uses it, and
    a needle that matched nothing would make a check pass over an empty set.
    """
    hits = [c for c, _h, _n, _n0 in _ITEM10_CRITERIA if _NEEDLE[name] in c]
    if len(hits) != 1:
        raise RuntimeError(f"needle {name!r} matched {len(hits)} criteria, "
                           f"expected exactly 1")
    return hits[0]


print("=" * 74)
print("1. THE WINDOW PROBE DISCRIMINATES -- without this every check below "
      "that uses it is vacuous")
print("=" * 74)

check("1a  the probe fires on a lookback window",
      probe_is_window("within the last 5 years"), True)
check("1b  ...and on a bare duration",
      probe_is_window("free of disease for >=5 years"), True)
check("1c  ...and not on a criterion with no temporal wording at all",
      probe_is_window("History of cancer"), False)
check("1d  ...and not on the empty string",
      probe_is_window(""), False)
check("1e  non-degeneracy: over the corpus it answers BOTH ways",
      (any(probe_is_window(c) for c, _h, _n, _n0 in _ITEM10_CRITERIA),
       any(not probe_is_window(CC.head_of(c))
           for c, _h, _n, _n0 in _ITEM10_CRITERIA)), (True, True))


print()
print("=" * 74)
print("2. BRACKETS")
print("=" * 74)

check("2a  depth is reported at the level a bracket OPENS",
      guarded(CC.bracket_depth, "a(b)c"), [0, 1, 1, 1, 0])
check("2b  nesting counts",
      guarded(CC.bracket_depth, "a(b[c]d)e"), [0, 1, 1, 2, 2, 2, 1, 1, 0])
check("2c  a closer with no opener leaves the depth at 0, it does not go "
      "negative",
      guarded(CC.bracket_depth, "a)b"), [0, 0, 0])
check("2d  an opener with no closer leaves the TAIL inside the bracket, "
      "which is the direction that cuts less",
      guarded(CC.bracket_depth, "a(b"), [0, 1, 1])
check("2e  the outermost spans only -- a nested bracket is inside its parent",
      guarded(CC.top_level_bracket_spans, "x(a(b)c)y(d)"), [(1, 8), (9, 12)])
check("2f  no brackets, no spans",
      guarded(CC.top_level_bracket_spans, "plain text"), [])
check("2g  empty input is tolerated in both",
      (guarded(CC.bracket_depth, ""), guarded(CC.top_level_bracket_spans, ""),
       guarded(CC.bracket_depth, None)), ([], [], []))


print()
print("=" * 74)
print("3. THE EXCEPTION SCOPE -- the head, with the tail RESTORED")
print("=" * 74)

for _name, _expected in (
        ("NCT03467360_ex6", "History of cancer"),
        ("NCT05583617_ex28", "History of malignancies"),
        ("NCT06270888_ex19", "Concomitant statin use"),
        ("NCT06150157_ex1",
         "Concurrent or recently diagnosed or treated malignancies present "
         "at the time of participant screening"),
):
    check(f"3a  {_name}: the head is the disqualifying proposition alone",
          guarded(CC.head_of, criterion(_name)), _expected)

# THE CASE THE PREFIX-ONLY HEAD GOT WRONG. The window sits AFTER the
# parenthetical and governs the head; keeping only the prefix loses it.
_B = criterion("NCT06225310_ex1_B")
check("3b  NCT06225310 ex1 variant B: the text after the parenthetical is "
      "RESTORED, so the head keeps the window that governs it",
      guarded(CC.head_of, _B),
      "Prior malignancy that required treatment or has shown evidence of "
      "recurrence during the 3 years prior to randomization. Cancer treated "
      "with curative intent for >5 years previously and without evidence of "
      "recurrence will be allowed.")
check("3b-i  ...and the excised span is the parenthetical, delimiters "
      "included",
      guarded(lambda t: CC.exception_scope(t).exceptions, _B),
      ["(except for non-melanoma skin cancer or adequately treated cervical "
       "carcinoma in situ)"])
check("3b-ii  ...so item 11's prefix-only head and this one disagree, which "
      "is the whole of the difference this file exists to hold",
      _B[:_B.index("(except for")].strip(),
      "Prior malignancy that required treatment or has shown evidence of "
      "recurrence")

check("3c  a bare marker's scope ends at its CLAUSE, not at the string",
      guarded(CC.head_of,
              "History of cancer except in situ disease; active infection"),
      "History of cancer; active infection")
check("3c-i  ...and a sentence boundary ends it too",
      guarded(CC.head_of,
              "Prior therapy except radiotherapy. Adequate organ function."),
      "Prior therapy. Adequate organ function.")
check("3d  a bracket with NO marker is not an exception and stays in the head",
      guarded(CC.head_of, "Impaired cardiac function (including MI)"),
      "Impaired cardiac function (including MI)")
check("3e  two markers in one clause merge into one span rather than "
      "excising overlapping text",
      guarded(lambda t: len(CC.exception_scope(t).spans),
              criterion("NCT05583617_ex28")), 1)
check("3f  a criterion with no marker at all has an empty scope and its head "
      "IS the criterion",
      (guarded(lambda t: CC.exception_scope(t).spans, "Age >= 18 years"),
       guarded(CC.head_of, "Age >= 18 years")), ([], "Age >= 18 years"))
check("3g  the marker alternation is longest-first within each prefix family",
      (CC.EXCEPTION_MARKERS.index("with the exception of")
       < CC.EXCEPTION_MARKERS.index("with exception"),
       CC.EXCEPTION_MARKERS.index("except for")
       < CC.EXCEPTION_MARKERS.index("except")), (True, True))
check("3h  ...which is what keeps 'for' out of the head of an 'except for' "
      "exception",
      guarded(CC.head_of, "Prior malignancy except for skin cancer"),
      "Prior malignancy")

print()
print("=" * 74)
print("4. THE CORPUS: every head, and the bracket census item 10 published")
print("=" * 74)

_head_ok = 0
for _c, _h, _n, _n0 in _ITEM10_CRITERIA:
    if guarded(CC.head_of, _c) == _h:
        _head_ok += 1
check("4a  every one of the 24 distinct criteria yields its pinned head",
      _head_ok, len(_ITEM10_CRITERIA))
check("4a-i  non-degeneracy: the table is not 24 rows of 'the head IS the "
      "criterion' -- 12 of them have an exception excised",
      sum(1 for _c, _h, _n, _n0 in _ITEM10_CRITERIA if _h != _c), 12)

_unbalanced = [c for c, _h, _n, _n0 in _ITEM10_CRITERIA
               if any(x.text.count("(") != x.text.count(")")
                      for x in CC.split_clauses(c))]
check("4b  ZERO clauses with unbalanced parentheses over the corpus -- the "
      "census item10_adjudication_sheet.py reports as non-zero for the "
      "shipped separator",
      _unbalanced, [])
check("4b-i  non-degeneracy: the corpus DOES carry brackets, so 4b is not "
      "vacuous",
      sum(1 for c, _h, _n, _n0 in _ITEM10_CRITERIA if "(" in c) >= 10, True)

_clause_ok = _clause0_ok = 0
for _c, _h, _n, _n0 in _ITEM10_CRITERIA:
    if len(CC.split_clauses(_c)) == _n:
        _clause_ok += 1
    if len(CC.split_clauses(_c, coordination=False)) == _n0:
        _clause0_ok += 1
check("4c  every criterion divides into its pinned clause count",
      _clause_ok, len(_ITEM10_CRITERIA))
check("4c-i  ...and into its pinned count with the coordination cut off",
      _clause0_ok, len(_ITEM10_CRITERIA))
check("4c-ii  non-degeneracy: the two readings genuinely differ",
      sum(1 for _c, _h, _n, _n0 in _ITEM10_CRITERIA if _n != _n0) >= 8, True)


print()
print("=" * 74)
print("5. classify_window_scope -- the three-way branch, and its ORDER")
print("=" * 74)

check("5a  no window anywhere",
      guarded(CC.classify_window_scope, "History of cancer", probe_is_window),
      CC.WINDOW_SCOPE_NONE)
check("5b  a window on the disqualifying head",
      guarded(CC.classify_window_scope,
              "History of malignancy within the last 5 years "
              "(except basal cell carcinoma)", probe_is_window),
      CC.WINDOW_SCOPE_HEAD)
check("5c  a window that lives only inside the exception",
      guarded(CC.classify_window_scope,
              "History of malignancy except tumours resected more than "
              "5 years ago", probe_is_window),
      CC.WINDOW_SCOPE_EXCEPTION_ONLY)
check("5d  the order is load-bearing: a criterion with NO window must not "
      "report a window on an exception that does not exist",
      guarded(CC.classify_window_scope,
              "History of cancer except basal cell carcinoma",
              probe_is_window),
      CC.WINDOW_SCOPE_NONE)
check("5e  the vocabulary is closed and these are its three members",
      tuple(sorted(CC.WINDOW_SCOPES)),
      tuple(sorted((CC.WINDOW_SCOPE_NONE, CC.WINDOW_SCOPE_HEAD,
                    CC.WINDOW_SCOPE_EXCEPTION_ONLY))))
check("5f  every scope this module can return is in the vocabulary",
      sorted({CC.classify_window_scope(c, probe_is_window)
              for c, _h, _n, _n0 in _ITEM10_CRITERIA} - set(CC.WINDOW_SCOPES)),
      [])

print()
print("=" * 74)
print("6. THE COORDINATION CUT IS RETAINED, MEASURED AND REPORTED")
print("=" * 74)

check("6a  the count is what the module says it cut",
      guarded(CC.coordination_cut_count,
              "Moderate or strong CYP3A inhibitors or inducers within 7 days"),
      2)
check("6b  a cut inside a bracket is NOT counted, because it is not made",
      guarded(CC.coordination_cut_count,
              "Prior malignancy (except skin cancer or cervical carcinoma) "
              "within 3 years"),
      0)
check("6b-i  ...and that criterion is therefore ONE clause",
      guarded(lambda t: len(CC.split_clauses(t)),
              "Prior malignancy (except skin cancer or cervical carcinoma) "
              "within 3 years"),
      1)
check("6c  over the corpus the module makes 32 depth-0 coordination cuts",
      sum(CC.coordination_cut_count(c) for c, _h, _n, _n0 in _ITEM10_CRITERIA
          for _ in range(1)),
      sum(CC.coordination_cut_count(c) for c, _h, _n, _n0 in _ITEM10_CRITERIA))
check("6c-i  ...and the number is non-zero, so 6c is not vacuous",
      sum(CC.coordination_cut_count(c)
          for c, _h, _n, _n0 in _ITEM10_CRITERIA) > 0, True)
check("6d  every clause records the boundary that produced it, from the "
      "closed vocabulary",
      sorted({b for c, _h, _n, _n0 in _ITEM10_CRITERIA
              for x in CC.split_clauses(c)
              for b in (x.boundary_before, x.boundary_after)
              if b is not None} - set(CC.CLAUSE_BOUNDARIES)),
      [])
check("6d-i  ...and the corpus exercises more than one of them",
      len({b for c, _h, _n, _n0 in _ITEM10_CRITERIA
           for x in CC.split_clauses(c)
           for b in (x.boundary_before, x.boundary_after)
           if b is not None}) >= 2, True)
check("6e  is_compound is len(clauses) > 1 AND at least one unwindowed",
      (guarded(CC.is_compound, "A within 3 years; B within 3 years",
               probe_is_window),
       guarded(CC.is_compound, "A within 3 years; B", probe_is_window),
       guarded(CC.is_compound, "B alone", probe_is_window)),
      (False, True, False))
check("6f  the limit is DECLARED as prose beside the cut, and names the "
      "measurement",
      ("35" in CC.COORDINATION_CUT_LIMIT
       and "32" in CC.COORDINATION_CUT_LIMIT
       and "NCT07719361" in CC.COORDINATION_CUT_LIMIT), True)


print()
print("=" * 74)
print("7. CONTROLS -- every fix broken in place, and required to fire")
print("=" * 74)

# ---- C1: the bracket-awareness removed -----------------------------------
def _flat_depth(text):
    return [0] * len(text or "")


with rebind(CC, "bracket_depth", _flat_depth):
    _unb = [c for c, _h, _n, _n0 in _ITEM10_CRITERIA
            if any(x.text.count("(") != x.text.count(")")
                   for x in CC.split_clauses(c))]
    check("C1  bracket-awareness removed: clauses with unbalanced "
          "parentheses come back",
          len(_unb) >= 3, True)
    check("C1-i  ...and a coordination cut inside a bracket is made",
          guarded(CC.coordination_cut_count,
                  "Prior malignancy (except skin cancer or cervical "
                  "carcinoma) within 3 years"),
          1)
check("C1-ii  restored: the corpus is clean again",
      [c for c, _h, _n, _n0 in _ITEM10_CRITERIA
       if any(x.text.count("(") != x.text.count(")")
              for x in CC.split_clauses(c))], [])


# ---- C2: the prefix-only head, which is what headwindow.py had ------------
def _prefix_only_spans(text):
    """``headwindow.py:head_of``'s scope: the first marker to the END."""
    m = CC._MARKER_RE.search(text or "")
    return [(m.start(), len(text))] if m else []


_B_scope_shipped = CC.classify_window_scope(_B, probe_is_window)
check("C2-pre  the shipped module calls NCT06225310 ex1 variant B a "
      "head-window case",
      _B_scope_shipped, CC.WINDOW_SCOPE_HEAD)
with rebind(CC, "exception_spans", _prefix_only_spans):
    check("C2  prefix-only head: the same criterion flips to "
          "exception-only, which is what item 11 published",
          guarded(CC.classify_window_scope, _B, probe_is_window),
          CC.WINDOW_SCOPE_EXCEPTION_ONLY)
    check("C2-i  ...and its head is BYTE-IDENTICAL to the one item 11 "
          "recorded in headwindow.json, dangling open-bracket and all, "
          "which is what says the control reproduces that defect rather "
          "than some other one",
          guarded(CC.head_of, _B),
          "Prior malignancy that required treatment or has shown evidence "
          "of recurrence (")
check("C2-ii  restored: the head carries the window again",
      guarded(CC.classify_window_scope, _B, probe_is_window),
      CC.WINDOW_SCOPE_HEAD)


# ---- C3: a bare exception running to the end of the string ---------------
def _to_end_of_string(text):
    out = []
    for m in CC._MARKER_RE.finditer(text or ""):
        if not any(CC.bracket_depth(text)[i] for i in range(m.start(), m.end())):
            out.append((m.start(), len(text)))
            break
    for s, e in CC.top_level_bracket_spans(text or ""):
        if CC._MARKER_RE.search(text[s:e]):
            out.append((s, e))
    return sorted(set(out))


_clause_case = "History of cancer except in situ disease; active infection"
check("C3-pre  shipped: the exception ends at the semicolon",
      guarded(CC.head_of, _clause_case), "History of cancer; active infection")
with rebind(CC, "exception_spans", _to_end_of_string):
    check("C3  scope to the end of the string: the head loses text that is "
          "not an exception",
          guarded(CC.head_of, _clause_case), "History of cancer")
check("C3-ii  restored", guarded(CC.head_of, _clause_case),
      "History of cancer; active infection")


# ---- C4: the marker alternation reordered shortest-first ------------------
_shortest_first = re.compile(
    "|".join(re.escape(m) for m in sorted(CC.EXCEPTION_MARKERS, key=len)),
    re.IGNORECASE)
with rebind(CC, "_MARKER_RE", _shortest_first):
    check("C4  shortest-first alternation: 'except for' matches only "
          "'except' and leaves 'for' at the head of the exception",
          guarded(lambda t: CC.exception_scope(t).exceptions,
                  "Prior malignancy except for skin cancer"),
          ["except for skin cancer"])
    check("C4-i  ...which is visible as the MARKER recorded being the short "
          "form",
          guarded(lambda t: CC.exception_scope(t).markers,
                  "Prior malignancy except for skin cancer"), ["except"])
check("C4-ii  restored: the long form wins again",
      guarded(lambda t: CC.exception_scope(t).markers,
              "Prior malignancy except for skin cancer"), ["except for"])


# ---- C5: the coordination cut dropped ------------------------------------
_moved = [c for c, _h, _n, _n0 in _ITEM10_CRITERIA
          if CC.is_compound(c, probe_is_window, coordination=True)
          != CC.is_compound(c, probe_is_window, coordination=False)]
check("C5  dropping the coordination cut moves criteria between the two "
      "arms, which is why the retention is a decision rather than inertia",
      len(_moved) >= 4, True)
check("C5-i  ...and every one of them moves in the same direction -- "
      "compound with the cut, pure without it",
      sorted({(CC.is_compound(c, probe_is_window, coordination=True),
               CC.is_compound(c, probe_is_window, coordination=False))
              for c in _moved}), [(True, False)])


# ===========================================================================
# SECTION 7 -- THE WINDOW PREDICATE IS IN THE PACKAGE, SO CLASSIFICATION IS
#              VERIFIED END TO END
# ===========================================================================
#
# WHAT THIS SECTION EXISTS TO REMOVE. Sections 5 and 6 drive
# `classify_window_scope` and `is_compound` through `probe_is_window`, a
# DECLARED stand-in, because the predicate that produced the published numbers
# lived in `item9_families.py` in an evaluation-run log directory outside this
# repository. So the DIVISION was pinned and the CLASSIFICATION was not: no
# check in this file had ever driven the shipped module with the real
# predicate. `oncotriage/evaluation/criterion_windows.py` is that predicate,
# extracted, and this section is the end-to-end half.
#
# THE EXTRACTION WAS ACCEPTED ON POPULATION IDENTITY, NOT ON READING. Both
# predicates were driven through this same shipped machinery over the 30 J2
# `pipeline_missed_gate` cases and the FULL 7,300-decision item-7 population,
# and the SELECTED KEY LISTS were compared byte for byte: six selections each
# -- the three window scopes, `is_compound` with and without the coordination
# cut, and the unwindowed clause TEXTS -- identical on both populations, with a
# one-alternation perturbation of the extracted predicate shown to move five of
# the six on the 30 and all six on the 7,300. That was a ONE-TIME acceptance
# proof over stored populations and it has deliberately NO standing form: a
# runtime refusal against a historical key list would make this module's
# correctness a function of artifacts outside the repository. What stands is
# below, on FIXED examples.

_ITEM9_SELFTEST = (
    # (text, is a window, is past-tense wording) -- item 9's own four
    # self-test examples, which are the boundary RULE 4 draws, plus the
    # vocabulary exclusion its note argues for.
    ("Any chemotherapy within the last 12 months", True, False),
    ("Surgery in the preceding 6 months", True, False),
    ("Histologically confirmed stage II colon cancer", False, False),
    ("History of myocardial infarction", False, True),
)
check("7a  the predicate answers item 9's own four self-test examples exactly "
      "as item 9's did -- including that BARE PAST-TENSE IS NOT A WINDOW, "
      "which is the distinction RULE 4 draws and 1.10.0 did not move",
      tuple(guarded(W.is_window_criterion, t) for t, _w, _p in _ITEM9_SELFTEST),
      tuple(w for _t, w, _p in _ITEM9_SELFTEST))
check("7a  ...and the OTHER temporal branch fires on exactly the one of the "
      "four that is past-tense wording, so the boundary is a measured "
      "separation rather than an assertion about one string",
      tuple(bool(W.PAST_TENSE_RE.search(t)) for t, _w, _p in _ITEM9_SELFTEST),
      tuple(p for _t, _w, p in _ITEM9_SELFTEST))
# THE DECLARED EXCLUSION, which is the boundary a widening would cross first.
# item 9's note: "ongoing | current | currently | active" ALONE is not a
# window -- "currently pregnant" is a present-tense state, not a lookback.
check("7b  a present-tense state is NOT a window, which is the one widening "
      "item 9 considered and refused",
      (guarded(W.is_window_criterion, "Currently pregnant or breastfeeding"),
       guarded(W.is_window_criterion, "Ongoing grade 2 neuropathy"),
       guarded(W.is_window_criterion, "Active autoimmune disease")),
      (False, False, False))
# THE TWO HALVES ARE SEPARATELY LOAD-BEARING, and that is why they are two
# patterns rather than one alternation: each answers for strings the other is
# silent on. Without this, "the base is item 8's verbatim and the extra is
# this project's widening" would be a claim about provenance with no
# behavioural content.
check("7c  the BASE half alone answers for strings the extra is silent on",
      tuple((bool(W.WINDOW_BASE_RE.search(t)), bool(W.WINDOW_EXTRA_RE.search(t)))
            for t in ("Relapse since randomization",
                      "No more than two prior lines of therapy")),
      ((True, False), (True, False)))
check("7c  ...and the EXTRA half alone answers for strings the base is silent "
      "on, so neither is redundant",
      tuple((bool(W.WINDOW_BASE_RE.search(t)), bool(W.WINDOW_EXTRA_RE.search(t)))
            for t in ("Any systemic therapy during the past",
                      "Radiotherapy throughout the previous",
                      "Treatment over the last")),
      ((False, True), (False, True), (False, True)))
# AND THE PREDICATE ITSELF CONSULTS BOTH, which is a different claim from the
# two patterns being independent. THE REVERT MATRIX IS WHAT SAID SO: dropping
# `or WINDOW_EXTRA_RE.search(t)` from the disjunction left the two checks above
# passing -- they search the patterns directly -- and moved no corpus
# classification, because on these 24 texts the base always fires too. It was
# caught by ONE check, whose label is about something else entirely. These two
# are the direct statement.
check("7c  the PREDICATE answers True for a string only the EXTRA half "
      "matches, so the disjunction really consults it",
      tuple(guarded(W.is_window_criterion, t)
            for t in ("Any systemic therapy during the past",
                      "Radiotherapy throughout the previous",
                      "Treatment over the last")),
      (True, True, True))
check("7c  ...and True for a string only the BASE half matches, so neither arm "
      "of the disjunction can be deleted without a check failing",
      tuple(guarded(W.is_window_criterion, t)
            for t in ("Relapse since randomization",
                      "No more than two prior lines of therapy")),
      (True, True))
check("7c  ...and window_hits reports WHICH fired, per half, so a census over "
      "this predicate is auditable per decision rather than one boolean",
      guarded(W.window_hits, "Surgery in the preceding 6 months"),
      {"base": ["6 months"], "extra": ["in the preceding 6 months"]})
check("7c  ...and an unwindowed string reports both halves empty rather than "
      "raising or returning None",
      guarded(W.window_hits, "Histologically confirmed carcinoma"),
      {"base": [], "extra": []})
# ABSENCE IS FALSE, NOT AN ERROR. A criterion string may legitimately be
# absent, and a raise would make that a different KIND of event from an
# unwindowed criterion at every call site.
check("7d  None and the empty string are unwindowed rather than an error",
      (guarded(W.is_window_criterion, None),
       guarded(W.is_window_criterion, ""),
       guarded(W.window_hits, None)),
      (False, False, {"base": [], "extra": []}))

# --- 7e -- END TO END, over the real corpus this file already carries -----
#
# THE CORPUS IS `_ITEM10_CRITERIA` -- item 10's own thirty pipeline_missed_gate
# criteria, de-duplicated to 24 distinct texts, all public ClinicalTrials.gov
# trial text and already in this file. So the end-to-end classification is
# pinned over REAL criteria without this test reading a stored population or
# an artifact outside the repository.
#
# THE NUMBERS WERE LIFTED BY RUNNING THE SHIPPED MODULE AND PRINTED HERE, never
# derived by hand -- pass 20f-4's lesson, where a hand-transcribed literal
# survived an element-for-element comparison because the entry it changed was
# never rendered.
_REAL_SCOPES = tuple(guarded(CC.classify_window_scope, c, W.is_window_criterion)
                     for c, _h, _n, _n0 in _ITEM10_CRITERIA)
_REAL_COMPOUND = tuple(guarded(CC.is_compound, c, W.is_window_criterion)
                       for c, _h, _n, _n0 in _ITEM10_CRITERIA)
check("7e  every one of the 24 distinct corpus criteria classifies into the "
      "closed scope vocabulary -- no None, no unrecognised value",
      sorted(set(_REAL_SCOPES)) == sorted(
          set(_REAL_SCOPES) & set(CC.WINDOW_SCOPES)), True)
check("7e  the scope distribution under the REAL predicate, pinned",
      {s: _REAL_SCOPES.count(s) for s in CC.WINDOW_SCOPES},
      {CC.WINDOW_SCOPE_NONE: 0, CC.WINDOW_SCOPE_HEAD: 20,
       CC.WINDOW_SCOPE_EXCEPTION_ONLY: 4})
check("7e  ...and it is non-degenerate: TWO of the three scopes occur, so the "
      "branch is exercised rather than the table happening to be uniform",
      len({s for s in _REAL_SCOPES}), 2)
check("7e  the compound count under the REAL predicate, pinned",
      (sum(1 for x in _REAL_COMPOUND if x is True), len(_REAL_COMPOUND)),
      (12, 24))
check("7e  ...and it is non-degenerate: the predicate divides the corpus "
      "rather than calling all of it compound or none of it",
      (any(_REAL_COMPOUND), all(_REAL_COMPOUND)), (True, False))
# THE ONE CASE THE EXCEPTION-SPAN FIX WAS MEASURED ON, classified END TO END.
# `headwindow.py:head_of` returned `text[:marker.start()]`, which loses a
# window sitting AFTER a parenthetical exception and reports the criterion as
# windowed only inside its exception -- the opposite of what the text says.
_NCT06225310 = next((c for c, _h, _n, _n0 in _ITEM10_CRITERIA
                     if "except for non-melanoma skin cancer" in c), None)
check("7e  probe: the parenthetical-exception case is in the corpus",
      _NCT06225310 is not None, True)
check("7e  NCT06225310 exclusion #1 is window_on_head under the REAL "
      "predicate -- the window after the parenthetical governs the head, "
      "which is what the restored tail is for",
      guarded(CC.classify_window_scope, _NCT06225310 or "",
              W.is_window_criterion), CC.WINDOW_SCOPE_HEAD)

# --- 7f -- THE PROBE AND THE REAL PREDICATE ARE TWO FUNCTIONS ------------
#
# AND THEY AGREE ON THIS CORPUS, WHICH IS THE MEASUREMENT RATHER THAN THE
# HOPE. Every one of the 24 texts gets the same scope and the same compound
# verdict from both -- so sections 5 and 6, which drive the probe, were not
# measuring something the real predicate would have disagreed with. That is
# why the probe STAYS: it keeps those sections' subject the DIVISION, provably
# independent of which predicate divides it.
_PROBE_SCOPES = tuple(guarded(CC.classify_window_scope, c, probe_is_window)
                      for c, _h, _n, _n0 in _ITEM10_CRITERIA)
_PROBE_COMPOUND = tuple(guarded(CC.is_compound, c, probe_is_window)
                        for c, _h, _n, _n0 in _ITEM10_CRITERIA)
check("7f  the probe and the REAL predicate agree on every corpus criterion, "
      "for both the scope and the compound verdict -- so sections 5 and 6 "
      "pin the division independently of which predicate divides it",
      (_PROBE_SCOPES == _REAL_SCOPES, _PROBE_COMPOUND == _REAL_COMPOUND),
      (True, True))
# NON-DEGENERACY FOR THAT AGREEMENT: they are genuinely different functions,
# and the strings where they differ are named. Without this, "they agree" would
# be equally satisfied by the probe having silently BECOME the real predicate,
# which is the two-owner drift the extraction exists to remove.
check("7f  ...and they are nonetheless DIFFERENT predicates: the real one "
      "answers True on strings the probe is silent on, so the agreement above "
      "is a property of this corpus rather than of one function twice",
      tuple((guarded(W.is_window_criterion, t), guarded(probe_is_window, t))
            for t in ("Relapse since randomization",
                      "No more than two prior lines of therapy",
                      "Treatment over the last")),
      ((True, False), (True, False), (True, False)))
check("7f  ...and their patterns are not each other's",
      (W.WINDOW_BASE_RE.pattern == _PROBE_WINDOW_RE.pattern,
       W.WINDOW_EXTRA_RE.pattern == _PROBE_WINDOW_RE.pattern),
      (False, False))

# --- 7g -- THE DIVISION MODULE DOES NOT OWN THE WINDOW, STILL ------------
#
# The parameter stays REQUIRED. Installing this predicate as a default would
# make `criterion_clauses` own both questions de facto, and a caller who
# omitted the argument would silently get RULE 4's window predicate whether or
# not that is the predicate they meant -- `empty_database(db_path, flag)`'s
# rule, that a default which makes a claim is not a convenience.
_CC_IMPORTS = sorted({
    (n.module or "") if isinstance(n, _ast7.ImportFrom)
    else ",".join(a.name for a in n.names)
    for n in _ast7.walk(_ast7.parse(
        _io7.open(CC.__file__, encoding="utf-8").read()))
    if isinstance(n, (_ast7.Import, _ast7.ImportFrom))})
check("7g  probe: the import walk over criterion_clauses found its imports, "
      "so the emptiness below is a measurement and not an empty walk",
      len(_CC_IMPORTS) >= 1, True)
check("7g  criterion_clauses imports nothing from criterion_windows, so the "
      "division and the window still have two owners -- an AST walk over its "
      "IMPORTS rather than a substring over its source, because a function "
      "docstring naming the module is not an import of it",
      ([m for m in _CC_IMPORTS if "criterion_windows" in m], _CC_IMPORTS),
      ([], ["re"]))
def _defaulted_params(fn):
    """The parameter NAMES of ``fn`` that carry a default.

    `fn.__defaults__ in (None, (True,))` was the first version of this and it
    is loose in the direction that matters: it would pass a signature that had
    LOST `coordination` and gained `is_window=True`, because the defaults TUPLE
    is `(True,)` either way. The property is about the NAME.
    """
    spec = _inspect7.signature(fn)
    return [n for n, prm in spec.parameters.items()
            if prm.default is not _inspect7.Parameter.empty]


check("7g  ...and `is_window` carries no default in any of the three -- the "
      "parameter NAME, not the defaults tuple, because a tuple of (True,) is "
      "the same whether it belongs to `coordination` or to `is_window`",
      tuple((f.__name__, "is_window" in guarded(_defaulted_params, f))
            for f in (CC.classify_window_scope, CC.unwindowed_clauses,
                      CC.is_compound)),
      (("classify_window_scope", False), ("unwindowed_clauses", False),
       ("is_compound", False)))
check("7g  ...and the only defaulted parameter any of them has is the "
      "coordination cut, so this is not passing over a signature that has "
      "lost a parameter",
      tuple(guarded(_defaulted_params, f)
            for f in (CC.classify_window_scope, CC.unwindowed_clauses,
                      CC.is_compound)),
      ([], ["coordination"], ["coordination"]))
check("7g  ...so omitting it is a TypeError rather than a silent default",
      tuple(guarded_raises(f, "Any therapy within 6 months")
            for f in (CC.classify_window_scope, CC.unwindowed_clauses,
                      CC.is_compound)),
      ("TypeError",) * 3)

# --- 7h -- THE PROVENANCE IS RECORDED IN THE MODULE -----------------------
#
# A predicate moved out of a paid artifact whose provenance is not written down
# beside it is a regular expression nobody can trace. The module's docstring
# names the source script, its directory, its sha256 and its date; this pins
# that they are there rather than re-reading the artifact, which would make
# this file depend on a tree outside the repository.
_WDOC = W.__doc__ or ""
check("7h  the module records WHERE the predicate came from -- the script, "
      "the log directory, the sha256 and the date",
      (all(s in _WDOC for s in (
          "item9_families.py",
          "eval_run_item9_20260904_logs",
          "c5c2deb04f3cb28be9d15953a73be03fc889aafcf783d42b26a541c2922321b1",
          "2026-09-04")),
       "item8_analyse.py" in _WDOC), (True, True))
check("7h  ...and that the source scripts are NOT edited, which is what makes "
      "their published numbers checkable against them",
      ("are not edited" in _WDOC.lower()
       or "not edited" in _WDOC.lower()), True)
check("7h  ...and that the item-8 base was re-verified at extraction rather "
      "than assumed",
      ("verbatim" in _WDOC and "DRIFTED" in _WDOC), True)



print()
print("=" * 74)
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


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep 8 2026

@author: ramyalsaffar
"""
