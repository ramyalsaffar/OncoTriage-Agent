"""Does a criterion state a TIME WINDOW. RULE 4's window vocabulary, one owner.

**ANALYSIS-SIDE ONLY. NOTHING HERE REACHES A MODEL OR A RENDERED PROMPT.**
``criterion_clauses`` establishes the same property of itself by measurement,
and the same measurement covers this module: the criterion text a trial carries
reaches Stage 5 through ``oncotriage/agent/evaluation.py``'s packer verbatim,
nothing in ``oncotriage/agent/`` imports either module, and nothing may. A
predicate that decided what the judge SEES would be an input change; this is a
reading OF the text after the fact.

WHY IT IS IN THE PACKAGE AT ALL. ``criterion_clauses`` -- the module that
divides a criterion -- deliberately does NOT own this question, and says so:
"it is passed in as a callable rather than copied here". That was right, and it
left the callable itself living outside the repository, in an evaluation-run
log directory, so the standing test could pin the DIVISION and had to supply a
declared stand-in for the WINDOW. Its own docstring records the consequence:
"That probe is not a copy of item 9's predicate and does not try to be." So the
classification the module exists to produce -- ``classify_window_scope``'s
three-way branch, ``is_compound``'s 11/19 split -- was never verified end to
end against the predicate that actually produced the published numbers. This is
that predicate, in the package, so it can be.

**THE PARAMETER STAYS REQUIRED, AND THAT IS A DECISION.** Nothing here is
installed as a default for ``criterion_clauses``'s ``is_window`` argument. That
argument is the statement that the division and the window are two questions
with two owners, and a default makes one module own both de facto -- a caller
who omits it would silently get RULE 4's window predicate whether or not that
is the predicate they meant. ``empty_database(db_path, flag)``'s rule: a
default that makes a claim is not a convenience.

════════════════════════════════════════════════════════════════════════════
PROVENANCE -- WHERE EVERY RULE CAME FROM
════════════════════════════════════════════════════════════════════════════

THE SOURCE SCRIPT is ``item9_families.py``, which lives at

    09- Testing/Evaluation Runs/eval_run_item9_20260904_logs/item9_families.py

with a BYTE-IDENTICAL copy at ``eval_run_item10_20260904_logs/`` (both
sha256 ``c5c2deb04f3cb28be9d15953a73be03fc889aafcf783d42b26a541c2922321b1``,
mtimes 2026-09-04 01:06:21 and 2026-09-04 02:23:58). Item 11's
``headwindow.py`` does not re-implement it -- it loads that file by location
and calls ``IF.is_window_criterion``, so there has only ever been ONE
implementation and this is a move rather than a reconciliation.

**THOSE SCRIPTS ARE PAID ARTIFACTS AND ARE NOT EDITED.** They produced
published numbers; a file that produced a result and was then changed is a file
whose result cannot be checked against it. They keep their own copy and go on
running against it. This is the importable owner for every FUTURE consumer, and
the one-time proof that the two agree is recorded below.

THE VOCABULARY, in the three layers item 9 built it from and argued at each:

  1. **RULE 4 itself**, which names the two branches a temporal criterion can
     take -- a WINDOW ("within the last 12 months") and PAST-TENSE WORDING
     ("history of", "prior", "previous"). The second is explicitly a DIFFERENT
     branch, so a past-tense criterion carrying no window is NOT windowed.
     That distinction is why this is not a bare "does it mention time" regex,
     and ``PAST_TENSE_RE`` below is the other branch, named so the boundary is
     checkable rather than asserted.
  2. **``item8_analyse.py:_WINDOW_HINT``**, the classifier this project already
     used to answer the same question in the immediately preceding item,
     carried VERBATIM as the base alternation so that item 8's Candidate C and
     item 9's family (a) are the same predicate on the same text.
  3. **``oncotriage/agent/patient.py``'s rendered temporal vocabulary** -- the
     other side of the comparison the model is asked to make. The record states
     elapsed time as "<n> <unit>s before reference date"; a criterion's window
     is what that interval is compared AGAINST, so the units the record can
     speak in bound the units a window can be written in. That is
     ``_RECORD_UNITS``.

WHAT IS DELIBERATELY NOT IN THE VOCABULARY, carried from item 9's own note
because it is the boundary a later widening would cross first: "ongoing |
current | currently | active" ALONE is not a window. A criterion saying
"currently pregnant" is a present-tense state, not a lookback, and RULE 4's
window branch is about a window.

**THE ITEM-8 BASE WAS RE-VERIFIED AT EXTRACTION TIME, BY AST, NOT ASSUMED.**
``item9_families.py`` carries ``_assert_item8_base_verbatim``, which re-reads
item 8's file and lifts ``_WINDOW_HINT``'s pattern out of it. That guard cannot
come with the predicate -- it reads a file outside this repository, and
importing a package module reads no file -- so it was run once, here, at the
extraction: item 8's lifted pattern equals ``_ITEM8_WINDOW_HINT_SRC`` character
for character, and the drift probe that plants a change into the comparison was
shown to answer DRIFTED. The check has no standing form in the package because
its subject is a frozen artifact; ``tests/test_criterion_clause_division.py``
pins this module's OWN patterns instead, which is the fact that can still move.

**AND THE EXTRACTION WAS PROVED BY POPULATION IDENTITY RATHER THAN BY READING.**
Every rule below was lifted out of ``item9_families.py`` by AST span and
written here without being retyped -- pass 20f-4's lesson, where a
hand-transcribed literal survived an element-for-element render comparison
because the entry it changed was never rendered. The acceptance proof then
drove BOTH predicates through the shipped ``criterion_clauses`` machinery over
the 30 J2 cases and the full item-7 population and compared the SELECTED KEY
LISTS byte for byte -- six selections each: the three window scopes,
``is_compound`` with and without the coordination cut, and the unwindowed
clause TEXTS.

    population   keys    selection blob sha256
    J2-30          30    a723113269cc489d05bce134cfe08df389a64708d8aef485f263b4465f5d7a2e
    item7-full   7300    5f61873f9ec9d5b87c8403dcff7ddca14cbd0532719f5da8aa9cf821b7b95e7a

Identical from both predicates on both populations, with a one-alternation
perturbation of the extracted predicate shown to move five of the six
selections on the 30 and all six on the 7,300 -- so the comparison can fail.
The two digests are recorded because the proof itself is a one-time instrument
run outside this repository: without them a later re-derivation would have
nothing to compare against, and they are the only part of it that survives.
**THEY ARE A RECORD AND NOT A GATE.** Nothing here reads them, and nothing
should: a runtime refusal against a historical key list would make this
module's correctness a function of artifacts outside the repository. What
stands is ``tests/test_criterion_clause_division.py`` section 7, which pins
this predicate's behaviour on FIXED examples.
"""

import re

# ---------------------------------------------------------------------------
# THE WINDOW VOCABULARY
#
# EVERY LINE FROM HERE TO `PAST_TENSE_RE` IS `item9_families.py`'s OWN SOURCE,
# LIFTED BY AST SPAN AND NOT RETYPED. The comments are item 9's arguments,
# carried with the rules they argue for.
# ---------------------------------------------------------------------------

# Carried VERBATIM from item8_analyse.py:_WINDOW_HINT, which lives in another
# item's log directory outside this repository.
#
# DECLARED EDIT AT EXTRACTION. item9_families.py's own comment here continued
# "check `_assert_item8_base_verbatim` below re-reads item 8's file and refuses
# if the two have drifted". THAT GUARD DOES NOT COME WITH THE PREDICATE and
# must not: it reads a file outside this repository, and importing a package
# module reads no file. It was RUN ONCE, at the extraction, and answered
# `verbatim` with its own drift probe answering `DRIFTED` -- see the module
# docstring. Leaving the sentence would have named a check this module does not
# have, which reads as a guarantee nobody provides.
_ITEM8_WINDOW_HINT_SRC = (
    r"\b(within|prior to|since|in the (?:past|last)|no more than|"
    r"at least \d+\s*(?:day|week|month|year)|\d+\s*(?:day|week|month|year)s?\b)"
)

# The units the RECORD can state an interval in. Read off patient.py's own
# elapsed vocabulary rather than listed: a window written in a unit the record
# cannot speak is a window the model cannot decide by comparison, and the
# renderer's ladder is what bounds the set.
_RECORD_UNITS = ("day", "week", "month", "year")

# ADDITIVE to the item 8 base, and every member is argued.
#   - "in the preceding|previous <n> <unit>" and "over the (past|last)": the
#     same lookback the base catches under "in the past|last", written the two
#     other ways ClinicalTrials.gov writes it.
#   - "<n> <unit>s of|from|before|after|post|prior": a bare duration adjacent
#     to an anchor, which the base's trailing "\d+\s*unit s?\b" already catches;
#     kept explicit so the census can say which alternation fired.
#   - "ongoing|current|currently|active" ALONE is NOT here. A criterion saying
#     "currently pregnant" is a present-tense state, not a lookback window, and
#     RULE 4's window branch is about a window. Item 8's Candidate C counted two
#     such flips as "windowed" because its regex matched the "within the next 24
#     weeks" clause in the SAME criterion -- which is a genuine window, so the
#     base is right there and no widening is needed.
_EXTRA_WINDOW_SRC = (
    r"\b(?:"
    r"in the (?:preceding|previous)\s+\d*\s*(?:%(u)s)s?"
    r"|over the (?:past|last)"
    r"|(?:during|throughout) the (?:past|last|preceding|previous)"
    r"|(?:less|more|greater|fewer) than \d+\s*(?:%(u)s)s?"
    r"|(?:up to|at most) \d+\s*(?:%(u)s)s?"
    r"|(?:%(u)s)s? (?:of|before|after|post|prior to|preceding) "
    r")" % {"u": "|".join(_RECORD_UNITS)}
)

WINDOW_BASE_RE = re.compile(_ITEM8_WINDOW_HINT_SRC, re.I)
WINDOW_EXTRA_RE = re.compile(_EXTRA_WINDOW_SRC, re.I)

# RULE 4's OTHER temporal branch, named so the census can report how much of the
# corpus is temporal-but-not-windowed. NOT part of family (a).
PAST_TENSE_RE = re.compile(r"\b(history of|prior|previous|previously|"
                           r"ever (?:had|been)|past)\b", re.I)


def window_hits(text):
    """Which alternations fired, so the classifier is auditable per decision."""
    t = text or ""
    return {
        "base": sorted({m.group(0).lower() for m in WINDOW_BASE_RE.finditer(t)}),
        "extra": sorted({m.group(0).lower().strip()
                         for m in WINDOW_EXTRA_RE.finditer(t)}),
    }


def is_window_criterion(text):
    """Does ``text`` state a time window? RULE 4's window branch, as a rule.

    THE PREDICATE ITSELF, and the two-line body is item 9's own, unedited. It
    is a disjunction rather than one alternation because the two halves have
    different provenance and different standing: the BASE is item 8's
    classifier carried verbatim, so that item's Candidate C and this are the
    same predicate on the same text, and the EXTRA is this project's declared
    widening, every member of which is argued at ``_EXTRA_WINDOW_SRC``. Keeping
    them apart is what lets ``window_hits`` say which one fired.

    ``None`` and ``""`` are False rather than an error: this is asked of a
    criterion string that may legitimately be absent, and a raise there would
    make an absent criterion a different KIND of event from an unwindowed one
    at every call site.

    PAST-TENSE WORDING IS NOT A WINDOW. "History of myocardial infarction" is
    False here and True under ``PAST_TENSE_RE`` -- RULE 4's other temporal
    branch, which 1.10.0 did not move. That pair is the boundary this predicate
    exists to draw, and it is pinned in the standing test rather than asserted.
    """
    t = text or ""
    return bool(WINDOW_BASE_RE.search(t) or WINDOW_EXTRA_RE.search(t))
