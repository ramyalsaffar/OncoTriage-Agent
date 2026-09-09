"""How an eligibility criterion is DIVIDED for window-scope analysis.

**ANALYSIS-SIDE ONLY. NOTHING HERE REACHES A MODEL OR A RENDERED PROMPT.**
That was established by measurement before this module was written, not
assumed: the criterion text a trial carries reaches Stage 5 through
``oncotriage/agent/evaluation.py``'s packer verbatim, and the only splitter on
that path is ``retrieval/indexer.py:split_inclusion_exclusion``, which divides
INCLUSION from EXCLUSION at index time and is a different function answering a
different question. Nothing in ``oncotriage/agent/`` imports this module and
nothing may: a division that decided what the judge SEES would be an input
change, and this one is a reading OF the text after the fact.

WHY IT IS IN THE PACKAGE AT ALL. The division that produced the published
11-pure / 19-compound split of item 10's ``pipeline_missed_gate`` cases lived
as a one-line regular expression inside ``item10_analyse.py``, in an
evaluation-run log directory outside this repository, and was then re-lifted by
AST into ``item10_adjudication_sheet.py`` and re-implemented a third time as a
head-window test in item 11's ``headwindow.py``. Three copies of one fact, in
three files nobody's test suite reads, each of which had to re-derive the
others to claim it agreed with them. This is the one owner, with the
provenance of every rule recorded beside it and a standing test that fires when
one moves.

════════════════════════════════════════════════════════════════════════════
WHAT WAS MEASURED, AND WHAT EACH FIX IS FOR
════════════════════════════════════════════════════════════════════════════

``item10_adjudication_sheet.py``'s own note names two defects in the shipped
separator ``;|\\n|(?<=[a-z])\\s+or\\s+(?=[A-Za-z])``, and item 11 §0 names a
third in the head test built on top of it:

1. **IT CUTS INSIDE PARENTHESES.** The sheet reports the census as "blocks
   containing at least one clause with unbalanced parentheses". A clause that
   begins mid-parenthetical -- ``...(except non-melanoma skin cancer`` -- is
   not a proposition and cannot be asked whether it carries a window. Fixed:
   every split here is BRACKET-AWARE and cuts only at depth 0.

2. **IT CUTS COORDINATED NOUN PHRASES.** The sheet's example is ``Moderate or
   strong CYP3A inhibitors``, split as though it were two clauses.
   **THE CUT IS RETAINED ANYWAY AND THAT IS THE FINDING RATHER THAN AN
   OMISSION** -- see ``COORDINATION_CUT_LIMIT`` below, where the measurement
   that decided it is recorded.

3. **THE HEAD TEST DISCARDED THE TAIL.** ``headwindow.py:head_of`` returns
   ``text[:marker.start()]`` -- everything before the first exception marker --
   which is right for a marker that runs to the end of the criterion and wrong
   for a PARENTHETICAL one, where the head resumes after the closing bracket.
   Measured: ``NCT06225310`` exclusion #1 variant B reads

       Prior malignancy that required treatment or has shown evidence of
       recurrence (except for non-melanoma skin cancer or adequately treated
       cervical carcinoma in situ) during the 3 years prior to randomization.

   and its window -- ``during the 3 years prior to randomization`` -- sits
   AFTER the parenthetical and governs the head. Cutting at ``except for`` and
   keeping only the prefix loses it and reports the criterion as windowed only
   inside its exception, which is the opposite of what the text says. Fixed:
   an exception is a SPAN that is excised, and what follows it is restored.

WHAT THIS MODULE DOES NOT DECIDE. It does not own the WINDOW predicate. "Does
this text state a time window" is RULE 4's vocabulary, it is owned by item 9's
``is_window_criterion``, and it is passed in as a callable rather than copied
here -- a second copy of a regular expression whose provenance is three
documents long is exactly the drift this module exists to remove. Every
function that needs it takes it as an argument.
"""

import re

# ---------------------------------------------------------------------------
# THE EXCEPTION VOCABULARY
# ---------------------------------------------------------------------------

EXCEPTION_MARKERS = (
    "with the exception of", "with exceptions", "with exception",
    "except for", "excepting", "except", "excluding", "exclusive of",
    "unless", "other than", "apart from", "save for",
)
"""Phrases that open a NARROWING clause. DECLARED, and carried verbatim.

Lifted unchanged from ``eval_run_item11_20260908_logs/headwindow.py``, which
declared them and used them to reclassify three of the published eleven. They
are kept byte-identical rather than re-derived so that this module and that
paid artifact partition the same criteria the same way, and so the
reclassification this module reproduces is attributable to the FIXES below
rather than to a widened vocabulary.

ORDERED LONGEST-FIRST WITHIN EACH PREFIX FAMILY, which is load-bearing: a
regular expression alternation is first-match-wins, so ``except`` placed before
``except for`` would match the first two words of ``except for`` and leave
``for`` at the head of the exception -- and ``with exception`` before ``with
the exception of`` would do the same. The three ``except`` forms and the three
``with exception`` forms are the pairs this actually decides.
"""

_MARKER_RE = re.compile(
    "|".join(re.escape(m) for m in EXCEPTION_MARKERS), re.IGNORECASE)

# ---------------------------------------------------------------------------
# BRACKETS
# ---------------------------------------------------------------------------

_BRACKET_PAIRS = {"(": ")", "[": "]"}
"""The bracket forms a criterion can carry. Braces are deliberately absent:
no criterion in the measured corpus uses one, and admitting a form nothing
carries is a rule with no case behind it.
"""


def bracket_depth(text):
    """Bracket nesting depth at every index of ``text``. Never raises.

    The depth AT index ``i`` is the depth of the character at ``i``: an opening
    bracket is reported at the depth it OPENS (so ``(`` in ``a(b)`` is depth 1),
    and its closer at the same depth. That convention is what lets a caller ask
    "is this cut outside every bracket" as ``depth[i] == 0``.

    UNBALANCED TEXT IS TOLERATED RATHER THAN REFUSED. Trial criteria are
    third-party data and a stray ``)`` is ordinary; item 11 §0 records two
    criteria whose two recorded texts differ by a single character, so this
    corpus's punctuation is not something to raise on. A closer with no opener
    leaves the depth at 0 and an opener with no closer leaves the tail inside a
    bracket -- which means the tail is never cut, the SAFE direction here,
    because an uncut clause is one fewer chance to manufacture a fragment that
    is not a proposition.
    """
    depths = []
    stack = []
    for ch in text or "":
        if ch in _BRACKET_PAIRS:
            stack.append(_BRACKET_PAIRS[ch])
            depths.append(len(stack))
        elif stack and ch == stack[-1]:
            depths.append(len(stack))
            stack.pop()
        else:
            depths.append(len(stack))
    return depths


def top_level_bracket_spans(text):
    """``[(open_index, close_index_exclusive), ...]`` for depth-1 brackets.

    Only the OUTERMOST brackets are returned; a nested one is inside its
    parent's span and would otherwise be excised twice.
    """
    spans = []
    stack = []
    for i, ch in enumerate(text or ""):
        if ch in _BRACKET_PAIRS:
            stack.append((i, _BRACKET_PAIRS[ch]))
        elif stack and ch == stack[-1][1]:
            start, _ = stack.pop()
            if not stack:
                spans.append((start, i + 1))
    return spans


# ---------------------------------------------------------------------------
# CLAUSE BOUNDARIES
# ---------------------------------------------------------------------------

CLAUSE_BOUNDARY_SEMICOLON = "semicolon"
CLAUSE_BOUNDARY_NEWLINE = "newline"
CLAUSE_BOUNDARY_SENTENCE = "sentence"
CLAUSE_BOUNDARY_COORDINATION = "coordination"

CLAUSE_BOUNDARIES = (CLAUSE_BOUNDARY_SEMICOLON, CLAUSE_BOUNDARY_NEWLINE,
                     CLAUSE_BOUNDARY_SENTENCE, CLAUSE_BOUNDARY_COORDINATION)
"""Every reason this module will cut. CLOSED, and each is labelled on the cut
it produced, so a consumer can say which boundary made a clause rather than
inferring it from the text.
"""

_SENTENCE_RE = re.compile(r"(?<=[a-z0-9)\]])\.\s+(?=[A-Z])")
"""A sentence end, conservatively. Requires a lower-case letter, a digit or a
closing bracket before the stop and a capital after it, which is what keeps
``>5 years previously`` and ``i.e.`` from becoming boundaries. It exists
because ``NCT06225310`` exclusion #1 variant B is TWO SENTENCES and the second
one is an allowance rather than a disqualifier; without it that allowance would
be glued to the head.
"""

_SEMICOLON_RE = re.compile(r";")
_NEWLINE_RE = re.compile(r"\n")
_COORDINATION_RE = re.compile(r"(?<=[a-z])\s+or\s+(?=[A-Za-z])")
"""The ` or ` cut, carried VERBATIM from ``item10_analyse.py``.

Byte-identical to the shipped alternation's third branch so that
``split_clauses(..., coordination=True)`` reproduces the published division
exactly once the bracket-awareness is accounted for. See
``COORDINATION_CUT_LIMIT``.
"""

COORDINATION_CUT_LIMIT = """\
THE ` or ` CUT IS A KNOWN OVER-SPLIT AND IT IS RETAINED, MEASURED AND REPORTED
RATHER THAN REMOVED OR REFINED.

MEASURED over the thirty `pipeline_missed_gate` criteria of item 10: the
shipped regular expression fires 35 times, of which 3 are inside a
parenthetical and are suppressed here by the bracket-awareness above, leaving
32 depth-0 cuts. NOT ONE of those 32 separates two independent propositions.
Every one coordinates inside a single proposition -- `HNPCC syndrome or
polyposis`, `Moderate or strong CYP3A inhibitors`, `food or drugs`, `inhibit or
induce`, `hematologic or primary solid tumor malignancy`.

AND REMOVING IT MOVES NINE OF THE THIRTY, OF WHICH FIVE MOVE THE WRONG WAY.
Driven against item 9's own `is_window_criterion` through THIS module, dropping
the cut takes nine criteria from compound to pure. Four are correct -- a
trailing window distributes over the coordination it follows, so `History of a
hematologic or primary solid tumor malignancy within the last 5 years` has no
unwindowed disqualifier and the split's `History of a hematologic` was a
fragment. FIVE ARE NOT:

  NCT07719361 excl 6   `Concurrent malignancy requiring treatment` is a
                       present-tense disqualifier and the window governs only
                       the second disjunct.
  NCT03110822 excl 4   `Impaired cardiac function`, likewise.
  NCT07612280 excl 6   `ischemic heart disease` sits unwindowed inside a
                       comma list of examples.
  NCT07155200 excl 14  `Currently using` is a present-tense state, which item
                       9's own note records is NOT a lookback window.
  NCT06150157 excl 1   the head carries no window at all; the only window is
                       inside the exception, which `classify_window_scope`
                       reports and this predicate cannot.

**AND NO TEXT SIGNAL SEPARATES THE FOUR FROM THE FIVE.** `History of a
hematologic | primary solid tumor malignancy within the last 5 years` and
`Concurrent malignancy requiring treatment | history of prior malignancy active
within 2 years` have the same shape -- two bare noun phrases, the window in the
last one only -- and opposite truths. The difference is whether the first
disjunct has a head noun of its own, which is grammar and not punctuation.

SO THE DIRECTION OF THE ERROR IS WHAT DECIDES IT. The compound arm means "this
case cannot be attributed to the gate from the text alone"; the pure arm is the
defensible headline. Over-splitting puts a case in the arm that claims less,
which is conservative; under-splitting contaminates the headline. The cut is
kept, `coordination_cut_count` says how many clauses came from it, and
`split_clauses(coordination=False)` computes the other reading -- so a consumer
states both rather than choosing one silently.
"""


class Clause(object):
    """One division of a criterion, and why the cut was made."""

    __slots__ = ("text", "start", "end", "boundary_before", "boundary_after")

    def __init__(self, text, start, end, boundary_before, boundary_after):
        self.text = text
        self.start = start
        self.end = end
        self.boundary_before = boundary_before
        self.boundary_after = boundary_after

    def __repr__(self):                                        # pragma: no cover
        return f"Clause({self.text!r})"

    def __eq__(self, other):
        if not isinstance(other, Clause):
            return NotImplemented
        return (self.text, self.start, self.end, self.boundary_before,
                self.boundary_after) == (
            other.text, other.start, other.end, other.boundary_before,
            other.boundary_after)

    def __hash__(self):
        return hash((self.text, self.start, self.end, self.boundary_before,
                     self.boundary_after))


def _cut_points(text, coordination):
    """``[(start, end, boundary)]`` for every depth-0 cut, in text order."""
    depth = bracket_depth(text)
    rules = [(_SEMICOLON_RE, CLAUSE_BOUNDARY_SEMICOLON),
             (_NEWLINE_RE, CLAUSE_BOUNDARY_NEWLINE),
             (_SENTENCE_RE, CLAUSE_BOUNDARY_SENTENCE)]
    if coordination:
        rules.append((_COORDINATION_RE, CLAUSE_BOUNDARY_COORDINATION))
    cuts = []
    for rx, label in rules:
        for m in rx.finditer(text):
            # THE WHOLE MATCH MUST BE OUTSIDE EVERY BRACKET, not merely its
            # first character: `_SENTENCE_RE` and `_COORDINATION_RE` both span
            # whitespace, and a match that begins at depth 0 and ends inside a
            # bracket would cut a bracket open.
            if any(depth[i] for i in range(m.start(), max(m.end(), m.start() + 1))):
                continue
            cuts.append((m.start(), m.end(), label))
    cuts.sort()
    # OVERLAPS ARE RESOLVED FIRST-WINS, in text order. Two rules can match the
    # same region -- `.\n` is a sentence end and a newline -- and applying both
    # would produce an empty clause between them.
    resolved = []
    for start, end, label in cuts:
        if resolved and start < resolved[-1][1]:
            continue
        resolved.append((start, end, label))
    return resolved


def split_clauses(text, coordination=True):
    """Divide ``text`` into ``Clause`` records at depth-0 boundaries.

    ``coordination`` includes the ` or ` cut. It defaults True because that is
    what the published 11/19 division used; pass False for the other reading.
    See ``COORDINATION_CUT_LIMIT``.

    Empty and whitespace-only fragments are dropped, which is what the shipped
    separator's ``if c and c.strip()`` did.
    """
    text = text or ""
    cuts = _cut_points(text, coordination)
    clauses = []
    pos = 0
    before = None
    for start, end, label in cuts:
        piece = text[pos:start]
        if piece.strip():
            clauses.append(Clause(piece.strip(), pos, start, before, label))
            before = label
        elif clauses:
            # A cut that produced nothing still records its boundary on the
            # NEXT clause, so an empty fragment does not hide why the following
            # clause begins where it does.
            before = label
        else:
            before = label
        pos = end
    tail = text[pos:]
    if tail.strip():
        clauses.append(Clause(tail.strip(), pos, len(text), before, None))
    return clauses


def coordination_cut_count(text):
    """How many depth-0 ` or ` cuts ``split_clauses`` would make. MEASURED.

    The reader for ``COORDINATION_CUT_LIMIT``: a consumer that reports a
    criterion as compound can say how much of that verdict rests on a cut the
    module itself records as an over-split.
    """
    return sum(1 for _s, _e, label in _cut_points(text or "", True)
               if label == CLAUSE_BOUNDARY_COORDINATION)


# ---------------------------------------------------------------------------
# EXCEPTION SCOPE
# ---------------------------------------------------------------------------

class ExceptionScope(object):
    """A criterion divided into its disqualifying head and its narrowings."""

    __slots__ = ("head", "exceptions", "markers", "spans")

    def __init__(self, head, exceptions, markers, spans):
        self.head = head
        self.exceptions = exceptions
        self.markers = markers
        self.spans = spans

    def __repr__(self):                                        # pragma: no cover
        return f"ExceptionScope(head={self.head!r}, n={len(self.exceptions)})"


_TRAILING_JUNK_RE = re.compile(r"[\s,;:]+$")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([;,.:])")
_LEADING_JUNK_RE = re.compile(r"^[\s,;:]+")


def _tidy(text):
    """Collapse whitespace and drop punctuation an excision left dangling.

    Three things, and all three are consequences of EXCISING A SPAN rather
    than of the source text:

      * ``History of cancer, with the exception of ...`` leaves ``History of
        cancer,`` once the exception goes, and a trailing comma is not part of
        the proposition;
      * excising a mid-string parenthetical leaves two spaces, which would
        make an otherwise identical head compare unequal;
      * excising a mid-CLAUSE exception leaves the space that preceded it
        stranded in front of the boundary that followed it -- ``History of
        cancer except in situ disease; active infection`` would otherwise
        read ``History of cancer ; active infection``. The space is removed
        BEFORE the punctuation and not the punctuation itself, because that
        boundary is what tells a later ``split_clauses`` where the head's
        clauses are.

    No rule here touches the INTERIOR of text the excision did not disturb,
    beyond collapsing runs of whitespace: this is a derived string and its job
    is to be comparable, not to be re-punctuated.
    """
    text = re.sub(r"\s+", " ", text or "")
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = _TRAILING_JUNK_RE.sub("", text)
    text = _LEADING_JUNK_RE.sub("", text)
    return text.strip()


def exception_spans(text):
    """``[(start, end)]`` of every narrowing clause, non-overlapping, in order.

    TWO SHAPES, AND THEY SCOPE DIFFERENTLY.

    **A BRACKETED EXCEPTION IS THE BRACKET.** ``Concomitant statin use (unless
    using statins >3 months prior to study drug in stable status without CK
    rise)`` narrows inside the parentheses and the head is what surrounds them.
    A bracket whose own top-level text carries a marker is excised whole,
    delimiters included.

    That test is an OVER-APPROXIMATION and its direction is stated: a bracket
    that carries a marker part-way through -- ``(median 5 years, except in
    patients over 70)`` -- is excised entirely, including the part before the
    marker. Excising more narrows the head, a narrower head is less likely to
    carry a window, and a head with no window is reported as
    ``WINDOW_SCOPE_EXCEPTION_ONLY``, which is the arm that claims LESS. The
    error is conservative. No criterion in the measured corpus has that shape.

    **A BARE EXCEPTION RUNS TO THE END OF ITS CLAUSE**, not to the end of the
    string. The clause ends at the next depth-0 ``;``, newline or sentence
    boundary -- deliberately NOT at a coordination cut, because ``except A or
    B`` is one exception naming two things and ending it at the ``or`` would
    put ``B`` back in the head as a disqualifier.
    """
    text = text or ""
    spans = []

    bracketed = set()
    for start, end in top_level_bracket_spans(text):
        inner = text[start + 1:end - 1]
        inner_depth = bracket_depth(inner)
        for m in _MARKER_RE.finditer(inner):
            if not any(inner_depth[i] for i in range(m.start(), m.end())):
                spans.append((start, end))
                bracketed.add((start, end))
                break

    depth = bracket_depth(text)
    stops = [s for s, _e, label in _cut_points(text, False)]
    for m in _MARKER_RE.finditer(text):
        if any(depth[i] for i in range(m.start(), m.end())):
            continue                      # inside a bracket; handled above
        end = len(text)
        for s in stops:
            if s >= m.end():
                end = s
                break
        spans.append((m.start(), end))

    # MERGED, because two markers in one clause -- ``other than MM unless free
    # of disease for >=5 years`` -- would otherwise excise overlapping spans
    # and the second excision would run off the end of the first.
    spans.sort()
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def exception_scope(text):
    """``ExceptionScope`` for ``text``: the head, and every narrowing excised.

    THE HEAD IS THE TEXT WITH THE EXCEPTION SPANS REMOVED AND WHAT FOLLOWS THEM
    RESTORED. That is the whole of fix 3 in this module's docstring, and it is
    what separates this from ``headwindow.py``'s ``text[:marker.start()]``.
    """
    text = text or ""
    spans = exception_spans(text)
    pieces = []
    pos = 0
    for start, end in spans:
        pieces.append(text[pos:start])
        pos = end
    pieces.append(text[pos:])
    head = _tidy("".join(pieces))
    exceptions = [_tidy(text[s:e]) for s, e in spans]
    markers = []
    for s, e in spans:
        m = _MARKER_RE.search(text[s:e])
        markers.append(m.group(0) if m else None)
    return ExceptionScope(head, [x for x in exceptions if x], markers, spans)


def head_of(text):
    """The disqualifying proposition alone. ``exception_scope(text).head``."""
    return exception_scope(text).head


# ---------------------------------------------------------------------------
# THE CLASSIFICATION
# ---------------------------------------------------------------------------

WINDOW_SCOPE_NONE = "no_window_anywhere"
WINDOW_SCOPE_HEAD = "window_on_head"
WINDOW_SCOPE_EXCEPTION_ONLY = "window_on_exception_only"

WINDOW_SCOPES = (WINDOW_SCOPE_NONE, WINDOW_SCOPE_HEAD,
                 WINDOW_SCOPE_EXCEPTION_ONLY)
"""Where a criterion's time window sits. CLOSED, and the three names are
``headwindow.py``'s own so that this module and item 11's published table
speak one vocabulary.
"""


def classify_window_scope(text, is_window):
    """Which of ``WINDOW_SCOPES`` describes ``text``.

    ``is_window`` is the RULE 4 window predicate -- item 9's
    ``is_window_criterion`` -- passed in rather than owned here. See the module
    docstring.

    The order is the one ``headwindow.py`` established and is load-bearing: a
    criterion with no window ANYWHERE is neither a gate case nor an
    exception-scoped one, and asking the head first would report it as
    ``WINDOW_SCOPE_EXCEPTION_ONLY`` -- a window on an exception that does not
    exist.
    """
    text = text or ""
    if not is_window(text):
        return WINDOW_SCOPE_NONE
    if is_window(head_of(text)):
        return WINDOW_SCOPE_HEAD
    return WINDOW_SCOPE_EXCEPTION_ONLY


def unwindowed_clauses(text, is_window, coordination=True):
    """The clauses of ``text`` that carry no window. The 11/19 predicate's half.

    A criterion is COMPOUND -- "cannot be attributed to the gate from the text
    alone" -- when it divides into more than one clause and at least one of
    them carries no window. That is ``item10_analyse.py``'s rule, reproduced;
    what changed under it is that the division is bracket-aware.
    """
    clauses = split_clauses(text, coordination=coordination)
    if len(clauses) < 2:
        return []
    return [c for c in clauses if not is_window(c.text)]


def is_compound(text, is_window, coordination=True):
    """Item 10's ``confounded_by_unwindowed_clause`` predicate, one owner."""
    return bool(unwindowed_clauses(text, is_window, coordination=coordination))
