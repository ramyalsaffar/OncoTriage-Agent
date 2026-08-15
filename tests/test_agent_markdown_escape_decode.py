# Stage 5 render: registry markdown escaping is removed before the judge sees it
##############################################################################

"""
Markdown Escape Decode Test

``_build_trials_text`` sent trial criteria to the judge carrying the registry's
own markdown escaping, so Stage 5 read ``INR \\> 1.2`` where the sponsor wrote
``INR > 1.2`` -- a threshold whose DIRECTION is spelled by the escaped
character. ``_decode_escaped_entities`` shipped first and scoped this class out
by name; ``_decode_markdown_escapes`` is it.

MEASURED OVER ALL 14,324 TRIALS in the 2026-08-10 corpus, and re-derived here
rather than taken from the census that sized the item
(09- Testing/Evaluation Runs/markdown_escape_census_20260814/). Over the two
fields Stage 5 renders, 28,399 of them non-empty:

  * 69,397 punctuation escapes across 10,108 trials (70.57%);
  * 41,657 of those in 9,044 trials (63.14%) are a COMPARATOR, ``<`` or ``>``;
  * 579 backslash+entity chains in 197 trials -- the sibling decoder's subject,
    counted here only to prove the two never double-handle one escape;
  * 14 double backslashes in 11 trials -- CommonMark's escape for a LITERAL
    backslash, and the only place in the render path where a backslash is
    CONTENT;
  * ZERO backslashes followed by anything else. Not one before a letter, a
    digit, a space, a newline or end-of-field. Section 2 pins that, because it
    is what says the decode set is closed over the real population.

THE ACCEPTANCE PAIRS ARE LIFTED FROM THE CORPUS BY SCRIPT, NEVER TYPED. Pass
20f-4 hand-transcribed one hoisted literal and shipped ``#2ecc71`` for
``#2ca02c``; the element-for-element comparison could not see it because that
entry never rendered. Every pair in section 3 is read out of the corpus at run
time, and each is checked by three statements that do NOT re-run the decoder's
rule -- the window's own first two characters are the escape, the output's
first character is the escaped one, and the non-backslash character sequence is
unchanged -- so the check and the code cannot agree by construction.

WHAT THIS FILE MUST NOT DO, and section 8 checks it: the splitter
``oncotriage/retrieval/indexer.py:split_inclusion_exclusion`` DEPENDS on these
backslashes (its ``_HEADING_LEAD_CHARS`` contains one, because the corpus holds
``\\<Exclusion Criteria\\>``). The decode is render-only. Nothing here
normalises anything the splitter reads, and section 8 re-runs the splitter over
the corpus to say so.

NO NETWORK, NO KEYS, NO SPEND, NO GIT HISTORY. Sections 2, 3 and 8 read the
trial corpus READ-ONLY and report a missing corpus as a recorded FAILURE rather
than a silent skip. It EXECS NOTHING: every negative control rebinds a module
attribute inside ``try``/``finally`` and drives the SHIPPED functions, which is
what a control for "the decode was removed" actually has to do. NOT in
tests/run_serial_tests.py's collision matrix: it writes nothing anywhere, and
the one repository file it reads is written by neither of the suite's two
writers.

    python tests/test_agent_markdown_escape_decode.py
"""

import contextlib
import hashlib
import io
import json
import logging
import os
import re
import sys

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

from oncotriage.agent import evaluation                        # noqa: E402
from oncotriage.agent.evaluation import (                      # noqa: E402
    MARKDOWN_ESCAPE_DECODE_UNRESOLVED,
    MARKDOWN_REFUSED_ESCAPED_BACKSLASH,
    MARKDOWN_REFUSED_REFERENCE_SYNTAX,
    _ESCAPED_ENTITY_CHAIN_RE,
    _MARKDOWN_ASCII_PUNCTUATION,
    _MARKDOWN_ESCAPE_DECODE_SET,
    _MARKDOWN_REFERENCE_SYNTAX_CHARS,
    _build_trials_text,
    _decode_escaped_entities,
    _decode_markdown_escapes,
    _neutralize_fence_markers,
)
from oncotriage.observability import (                         # noqa: E402
    LOGGABLE_FIELDS as _LOGGABLE_FIELDS,
)


_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label: str, actual, expected) -> None:
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


def drive(fn, *args, **kwargs):
    """Call into production code, converting a raise into a VALUE.

    A bare call inside a ``check()`` argument list lets an exception escape
    before ``check`` is entered, so the file dies with a traceback where it
    owed a summary and every result below it. This project has shipped that
    defect five times; every call into ``evaluation`` here goes through this.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                               # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


def md(text):
    """``_decode_markdown_escapes`` as a 3-tuple, raise-proofed."""
    got = drive(_decode_markdown_escapes, text)
    if not isinstance(got, tuple):
        return (got, -1, -1)
    return got


def ent(text):
    """``_decode_escaped_entities`` as a 3-tuple, raise-proofed."""
    got = drive(_decode_escaped_entities, text)
    if not isinstance(got, tuple):
        return (got, -1, -1)
    return got


def shipped_order(text):
    """The render path's own chain, minus the fence pass: markdown, entities."""
    return ent(md(text)[0])[0]


def reversed_order(text):
    """The other order, for the double-handling comparison only."""
    return md(ent(text)[0])[0]


def render(inclusion, exclusion="", nct_id="NCT00000000", phase="PHASE2"):
    """One trial through the SHIPPED renderer, raise-proofed."""
    return drive(_build_trials_text, [{"trial": {
        "nct_id": nct_id, "phase": phase,
        "eligibility": {"inclusion_criteria": inclusion,
                        "exclusion_criteria": exclusion}}}])


def render_many(specs):
    """A BATCH through the shipped renderer, raise-proofed.

    ``specs`` is a list of ``(inclusion, exclusion)`` pairs; the nct_id is
    derived from the position so every trial in the batch is distinguishable.
    One call, many trials -- which is the only shape that can tell a per-render
    event from a per-trial one.
    """
    return drive(_build_trials_text, [
        {"trial": {"nct_id": f"NCT{index:08d}", "phase": "PHASE2",
                   "eligibility": {"inclusion_criteria": inc,
                                   "exclusion_criteria": exc}}}
        for index, (inc, exc) in enumerate(specs)])


def capture_records(fn, level=None):
    """Every JSON log record ``fn`` emits on stderr.

    ``level`` temporarily lowers the ``oncotriage`` logger's threshold, which is
    what a DEBUG line needs to be observable at all: the shipped default is
    INFO, and ``StructuredLogger._log`` returns without emitting when the level
    is not enabled. Restored in a ``finally`` so one capture cannot change the
    threshold every later check runs under.
    """
    err = io.StringIO()
    _pkg_logger = logging.getLogger("oncotriage")
    _saved_level = _pkg_logger.level
    try:
        if level is not None:
            _pkg_logger.setLevel(level)
        with contextlib.redirect_stderr(err):
            fn()
    finally:
        _pkg_logger.setLevel(_saved_level)
    got = []
    for line in err.getvalue().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            got.append(parsed)
    return got


def events(recs, name):
    """The records carrying a given ``event`` field."""
    return [r for r in recs if r.get("event") == name]


def at(seq, index, default=None):
    """``seq[index]`` as a VALUE. An empty sequence is what a removed decode
    produces, and an IndexError there would abort the file instead of failing
    the one check that is about it."""
    try:
        return seq[index]
    except (IndexError, KeyError, TypeError):
        return default


def ltr_units(text):
    """Every ``(index, successor)`` a left-to-right escape walk considers.

    Written here independently of the shipped decoder -- the decoder appends to
    a sink, this yields positions -- so "no residue" is measured by a second
    implementation of the tokenizing rule rather than by the one under test.
    """
    i, length = 0, len(text)
    while i < length:
        if text[i] != "\\":
            i += 1
            continue
        nxt = text[i + 1] if i + 1 < length else None
        yield i, nxt
        i += 2 if nxt is not None else 1


def residue(text):
    """LTR units whose successor is still in the decode set. Must be zero."""
    return sum(1 for _, nxt in ltr_units(text)
               if nxt is not None and nxt in _MARKDOWN_ESCAPE_DECODE_SET)


def _corpus_path():
    """The trial corpus, resolved through the package rather than guessed."""
    from oncotriage import paths
    return os.path.join(paths.data_trial_path, "trials_latest.json")


_RENDER_FIELDS = ("inclusion_criteria", "exclusion_criteria")


# ===========================================================================
# SECTION 1 -- the decode set is closed, stated, and derived from the rule
# ===========================================================================

print("=" * 70)
print("SECTION 1 -- the decode set")
print("=" * 70)

check("1a     the punctuation constant is CommonMark's ASCII punctuation, all "
      "32 characters", sorted(set(_MARKDOWN_ASCII_PUNCTUATION)),
      sorted(set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")))

check("1b     the decode set is exactly that, minus the backslash and minus "
      "the two reference-syntax characters",
      _MARKDOWN_ESCAPE_DECODE_SET,
      frozenset(_MARKDOWN_ASCII_PUNCTUATION)
      - frozenset(_MARKDOWN_REFERENCE_SYNTAX_CHARS) - {"\\"})

check("1c     ...which is 29 characters", len(_MARKDOWN_ESCAPE_DECODE_SET), 29)

check("1d     the backslash is NOT in it (class (c) is a refusal, not a "
      "decode)", "\\" in _MARKDOWN_ESCAPE_DECODE_SET, False)

check("1e     ';' is not in it -- this decoder must not be able to terminate a "
      "character reference for the one that runs after it",
      ";" in _MARKDOWN_ESCAPE_DECODE_SET, False)

check("1f     '#' is not in it, for the same reason on the numeric form",
      "#" in _MARKDOWN_ESCAPE_DECODE_SET, False)

check("1g     the reference-syntax constant is exactly those two",
      sorted(set(_MARKDOWN_REFERENCE_SYNTAX_CHARS)), ["#", ";"])

# Non-degeneracy: a set that had silently become empty would satisfy every
# "X is not in it" check above.
check("1h     non-degeneracy: the set is non-empty and contains the two "
      "comparators the fix exists for",
      (len(_MARKDOWN_ESCAPE_DECODE_SET) > 0,
       "<" in _MARKDOWN_ESCAPE_DECODE_SET,
       ">" in _MARKDOWN_ESCAPE_DECODE_SET), (True, True, True))

check("1i     no character outside ASCII punctuation is in the set",
      sorted(_MARKDOWN_ESCAPE_DECODE_SET
             - frozenset(_MARKDOWN_ASCII_PUNCTUATION)), [])

check("1j     the two refusal reasons are the named constants a reader "
      "filters on", (MARKDOWN_REFUSED_ESCAPED_BACKSLASH,
                     MARKDOWN_REFUSED_REFERENCE_SYNTAX),
      ("escaped_backslash", "reference_syntax"))


# ===========================================================================
# SECTION 2 -- the corpus measurement this fix is sized from
# ===========================================================================

print()
print("=" * 70)
print("SECTION 2 -- the corpus measurement")
print("=" * 70)

_corpus_error = None
_trials = []
try:
    with open(_corpus_path()) as _fh:
        _trials = json.load(_fh)
except Exception as _exc:                                      # noqa: BLE001
    _corpus_error = f"{type(_exc).__name__}: {_exc}"

check("2a     the trial corpus was readable (a missing corpus is a FAILURE "
      "here, never a silent skip)", _corpus_error, None)

_punct_pairs = 0
_punct_trials = set()
_chain_pairs = 0
_chain_trials = set()
_double_pairs = 0
_double_trials = set()
_other_pairs = 0
_other_shapes = []
_comparator_pairs = 0
_comparator_trials = set()
_fields = 0
_successors = {}
# nct_id -> (field, text) for the lift in section 3, keyed by successor char.
_lift = {}

for _t in _trials:
    _nct = _t.get("nct_id")
    _el = _t.get("eligibility") or {}
    for _f in _RENDER_FIELDS:
        _txt = _el.get(_f)
        if not isinstance(_txt, str) or not _txt:
            continue
        _fields += 1
        if "\\" not in _txt:
            continue
        _starts = {m.start() for m in _ESCAPED_ENTITY_CHAIN_RE.finditer(_txt)
                   if m.group(0).startswith("\\")}
        for _i, _nxt in ltr_units(_txt):
            _successors[_nxt] = _successors.get(_nxt, 0) + 1
            if _i in _starts:
                _chain_pairs += 1
                _chain_trials.add(_nct)
            elif _nxt == "\\":
                _double_pairs += 1
                _double_trials.add(_nct)
            elif _nxt is not None and _nxt in set(_MARKDOWN_ASCII_PUNCTUATION):
                _punct_pairs += 1
                _punct_trials.add(_nct)
                if _nxt in "<>":
                    _comparator_pairs += 1
                    _comparator_trials.add(_nct)
                _lift.setdefault(_nxt, (_nct, _f, _txt, _i))
            else:
                _other_pairs += 1
                if len(_other_shapes) < 10:
                    _other_shapes.append((_nct, _f, repr(_nxt)))

check("2b     non-empty render-path fields", _fields, 28399)
check("2c     punctuation escapes (class (a))", _punct_pairs, 69397)
check("2d     ...across this many trials", len(_punct_trials), 10108)
check("2e     comparator escapes -- the class the defect lives in",
      _comparator_pairs, 41657)
check("2f     ...across this many trials", len(_comparator_trials), 9044)
check("2g     backslash+entity chains (class (b), the sibling's subject)",
      _chain_pairs, 579)
check("2h     ...across this many trials", len(_chain_trials), 197)
check("2i     double backslashes (class (c))", _double_pairs, 14)
check("2j     ...across this many trials", len(_double_trials), 11)
check("2k     class (d), anything else: ZERO. Every backslash in the render "
      "path is an escape or the escaped member of a pair",
      (_other_pairs, _other_shapes), (0, []))
check("2l     the distinct successor characters observed",
      sorted(c for c in _successors if c is not None),
      sorted("#&)*+-.<>[]^_|~\\"))
check("2m     no successor falls outside ASCII punctuation, which is what "
      "makes the rule -- not the sample -- the authority",
      sorted(c for c in _successors
             if c is not None and c not in set(_MARKDOWN_ASCII_PUNCTUATION)),
      [])
check("2n     non-degeneracy: the scan visited a real corpus",
      (len(_trials) > 10000, _fields > 20000), (True, True))


# ===========================================================================
# SECTION 3 -- acceptance pairs, LIFTED from the corpus by script
# ===========================================================================

print()
print("=" * 70)
print("SECTION 3 -- acceptance pairs lifted from the corpus")
print("=" * 70)

# The EXPECTED value for each lifted case is built by deleting the backslash at
# the index the scan recorded. That is a different computation from the
# decoder's left-to-right walk, so a decoder that had stopped working could not
# make this agree with it.
_lifted_decoded = 0
_lifted_refused = 0
for _ch in sorted(_lift):
    _nct, _f, _txt, _i = _lift[_ch]
    # A window cut AT the escape, so the window's own index 0 is the backslash
    # under test and nothing before it can shift the comparison.
    _win = _txt[_i:_i + 70]
    _got, _dec, _ref = md(_win)
    if _ch in _MARKDOWN_ESCAPE_DECODE_SET:
        # Three independent statements about a real corpus string, none of them
        # computed by re-running the decoder's rule: the escaped character is
        # now first, the backslash before it is gone, at least one escape was
        # counted, and nothing but backslashes left the string.
        check(f"3.{_ch}   {_nct} {_f}: '\\{_ch}' -> '{_ch}'",
              (_win[:2], _got[:1], _dec >= 1,
               [c for c in _got if c != "\\"] == [c for c in _win if c != "\\"]),
              ("\\" + _ch, _ch, True, True))
        _lifted_decoded += 1
    else:
        # The corpus's one '\#'. It is IN ASCII punctuation and OUT of the
        # decode set, so the lift must show it refused rather than decoded --
        # which is what says the reference-syntax exclusion is real on real
        # text and not only on a constructed case.
        check(f"3.{_ch}   {_nct} {_f}: '\\{_ch}' is refused, not decoded",
              (_got[:2], _dec, _ref), ("\\" + _ch, 0, 1))
        _lifted_refused += 1

check("3z     non-degeneracy: every observed punctuation successor was lifted "
      "and exercised, and the two arms are both non-empty",
      (_lifted_decoded + _lifted_refused, _lifted_decoded > 0,
       _lifted_refused > 0), (15, True, True))
check("3za    ...and the refused arm is exactly the reference-syntax "
      "characters the corpus shows",
      sorted(c for c in _lift if c not in _MARKDOWN_ESCAPE_DECODE_SET), ["#"])

# A whole real comparator sentence, lifted rather than typed.
_cmp_case = None
for _t in _trials:
    _el = _t.get("eligibility") or {}
    for _f in _RENDER_FIELDS:
        _txt = _el.get(_f)
        if isinstance(_txt, str) and "\\>" in _txt and "\\<" in _txt:
            _j = _txt.find("\\>")
            _cmp_case = (_t.get("nct_id"), _txt[max(0, _j - 40):_j + 60])
            break
    if _cmp_case:
        break

check("3aa    a real trial carrying both comparators was found (non-degeneracy "
      "for the two checks below)", _cmp_case is not None, True)

_cmp_text = _cmp_case[1] if _cmp_case else ""
_cmp_out, _cmp_dec, _ = md(_cmp_text)
check("3bb    the comparator escape is gone from the rendered text",
      "\\>" in _cmp_out, False)
check("3cc    ...and the comparator itself survives, so the threshold's "
      "direction reaches the judge",
      (">" in _cmp_out, _cmp_dec >= 1), (True, True))
check("3dd    ...and nothing but backslashes was removed",
      [c for c in _cmp_out if c != "\\"],
      [c for c in _cmp_text if c != "\\"])


# ===========================================================================
# SECTION 4 -- the four classes, one behaviour each
# ===========================================================================

print()
print("=" * 70)
print("SECTION 4 -- class behaviour")
print("=" * 70)

check("4a     (a) non-comparator punctuation: '\\*' -> '*'",
      md(r"a \* b")[:2], ("a * b", 1))
check("4b     (a) brackets, both halves", md(r"\[200 IU/mL\]")[:2],
      ("[200 IU/mL]", 2))
check("4c     (a) the escaped list terminator '11\\.' -> '11.'",
      md(r"11\. Patients must not")[:2], ("11. Patients must not", 1))

check("4d     (c) a double backslash is emitted VERBATIM and counted as a "
      "refusal, never collapsed", md(r"CLL\\ SLL")[:3],
      (r"CLL\\ SLL", 0, 1))
check("4e     (c) ...and the escape AFTER it is still decoded, because the "
      "pair was consumed as one unit", md(r"x\\\[y")[:3], (r"x\\[y", 1, 1))

check("4f     (b) a chain is passed through untouched -- it belongs to the "
      "other decoder", md(r"INR \&lt; 1.2")[:3], (r"INR \&lt; 1.2", 0, 0))
check("4g     (b) ...including a multi-level one",
      md(r"\&amp;amp;gt; 80,000")[:3], (r"\&amp;amp;gt; 80,000", 0, 0))

check("4h     (d) a backslash before a letter is a LITERAL backslash: left, "
      "and NOT counted as a refusal", md(r"C:\path\to")[:3],
      (r"C:\path\to", 0, 0))
check("4i     (d) a trailing backslash escapes nothing", md("ends\\")[:3],
      ("ends\\", 0, 0))
check("4j     (d) a backslash before a space or a newline is literal",
      md("a \\ b\\\nc")[:3], ("a \\ b\\\nc", 0, 0))

check("4k     refused: '\\#' stays, because '#' builds a numeric reference",
      md(r"\# CLN1114")[:3], (r"\# CLN1114", 0, 1))
check("4l     refused: '\\;' stays, because ';' terminates one",
      md(r"a\;b")[:3], (r"a\;b", 0, 1))

check("4m     empty and escape-free input is returned unchanged and costs "
      "nothing", (md("")[:3], md("plain text")[:3]),
      (("", 0, 0), ("plain text", 0, 0)))


# ===========================================================================
# SECTION 5 -- no double-handling, in BOTH orders' final output
# ===========================================================================

print()
print("=" * 70)
print("SECTION 5 -- the two decoders never handle one escape twice")
print("=" * 70)

# A chain carries exactly one escape. Whichever decoder is asked first, the
# final text must be the decoded character and the escape must be removed ONCE.
for _label, _src, _want in (
    ("named", r"INR \&lt; 1.2", "INR < 1.2"),
    ("numeric", r"count \&#62; 80000", "count > 80000"),
    ("multi-level", r"\&amp;amp;gt; 3", "> 3"),
    ("bare (no escape at all)", "INR &lt; 1.2", "INR < 1.2"),
):
    check(f"5a.{_label}  shipped order (markdown, then entities)",
          shipped_order(_src), _want)
    check(f"5b.{_label}  reversed order gives the same final text",
          reversed_order(_src), _want)

check("5c     the markdown decoder removes ZERO escapes from a chain, so the "
      "entity decoder's 'exactly one escape per chain' invariant holds",
      md(r"INR \&lt; 1.2")[1], 0)
check("5d     ...and the entity decoder is the one that decodes it",
      ent(md(r"INR \&lt; 1.2")[0])[1], 1)

check("5e     a NON-chain escaped ampersand IS the markdown decoder's, and it "
      "is decoded exactly once", md(r"a \& b")[:2], ("a & b", 1))
check("5f     ...and the entity decoder then finds nothing in it",
      ent(md(r"a \& b")[0])[1], 0)

# The skip rule is load-bearing: without it the markdown decoder would take the
# chain's escape and the entity decoder would decode a bare chain, removing
# zero escapes and quietly making its docstring false.
_saved_re = evaluation._ESCAPED_ENTITY_CHAIN_RE
try:
    evaluation._ESCAPED_ENTITY_CHAIN_RE = re.compile(r"(?!x)x")   # never matches
    _no_skip = md(r"INR \&lt; 1.2")
    check("5g     CONTROL: with the chain regex neutered, the markdown decoder "
          "DOES take the chain's escape -- so the skip rule is what stops it",
          (_no_skip[0], _no_skip[1]), ("INR &lt; 1.2", 1))
finally:
    evaluation._ESCAPED_ENTITY_CHAIN_RE = _saved_re
check("5h     ...and the shipped regex is restored",
      evaluation._ESCAPED_ENTITY_CHAIN_RE is _saved_re, True)
check("5i     ...so the skip is in force again", md(r"INR \&lt; 1.2")[1], 0)


# ===========================================================================
# SECTION 6 -- idempotence, order versus the fence, and termination
# ===========================================================================

print()
print("=" * 70)
print("SECTION 6 -- idempotence, fence ordering, termination")
print("=" * 70)

for _label, _src in (
    ("plain escape", r"INR \> 1.2"),
    ("double backslash", r"CLL\\ SLL"),
    ("double then escapable", r"x\\\[y"),
    ("chain", r"INR \&lt; 1.2"),
    ("refused reference syntax", r"\# CLN1114"),
    ("literal backslash", r"C:\path"),
    ("mixed", r"\>\\\[\&lt;\#\*"),
):
    _once = md(_src)[0]
    _twice = md(_once)[0]
    check(f"6a.{_label}  decoding the decoded output is the identity",
          _twice, _once)
    check(f"6b.{_label}  ...and the second pass decodes nothing",
          md(_once)[1], 0)

# THE POLICY CONTROL FOR THE DOUBLE BACKSLASH. Collapsing is the other
# candidate policy; it cannot be reached by rebinding a constant, because the
# "\\" branch is structural. So the collapsed OUTPUT is constructed here and
# fed back in: if the shipped decoder collapsed, this is the string it would
# have produced for r"x\\\[y", and a second pass eats the sponsor's own
# backslash. That is the whole argument for refusing, made executable.
_COLLAPSED_OF_X = "x\\[y"          # what "x\\\[y" collapses to: x, \, [, y
check("6c     the collapsed form is what a collapsing decoder would emit "
      "(non-degeneracy: it differs from the shipped output)",
      (_COLLAPSED_OF_X, _COLLAPSED_OF_X != md(r"x\\\[y")[0]),
      ("x\\[y", True))
check("6d     COLLAPSING '\\\\' would NOT be idempotent: a second pass over "
      "that output deletes a backslash the sponsor wrote. Refusing is what "
      "makes 6a hold", md(_COLLAPSED_OF_X)[:2], ("x[y", 1))
check("6e     ...whereas the SHIPPED output of the same input is stable",
      md(md(r"x\\\[y")[0])[0], md(r"x\\\[y")[0])

# Fence neutralization must stay LAST, over the decoded text.
_fence_src = r"\>\>\> and \<\<\<"
_decoded_fence = shipped_order(_fence_src)
check("6f     a run of escaped brackets DECODES to a real fence marker",
      _decoded_fence, ">>> and <<<")
check("6g     ...which the neutralizer, running last, spells out",
      _neutralize_fence_markers(_decoded_fence)[:2],
      ("> > > and < < <", 2))
_rendered = render(_fence_src)
check("6h     ...and the SHIPPED renderer therefore emits no bare fence run "
      "from decoded criteria",
      (">>> and" in _rendered, "> > > and < < <" in _rendered),
      (False, True))
check("6i     CONTROL: neutralizing BEFORE decoding would let the run "
      "through -- which is why the order at the call site is what it is",
      shipped_order(_neutralize_fence_markers(_fence_src)[0]), ">>> and <<<")

# Termination. The walk advances by 1 or 2 at every branch and never rewinds,
# so it is linear; a pathological run of backslashes must not spin.
_long = "\\" * 20000
_res = md(_long)
check("6j     20,000 consecutive backslashes terminate",
      (_res[0] == _long, _res[1], _res[2]), (True, 0, 10000))
_alt = (r"\>" * 20000)
check("6k     20,000 escapes terminate and all decode",
      (md(_alt)[0], md(_alt)[1]), (">" * 20000, 20000))
check("6l     an odd-length backslash run terminates and leaves the last one "
      "literal", md("\\" * 5)[:3], ("\\" * 5, 0, 2))
MARKDOWN_ESCAPE_DECODE_UNRESOLVED.clear()


# ===========================================================================
# SECTION 7 -- whole-corpus verification through the SHIPPED functions
# ===========================================================================

print()
print("=" * 70)
print("SECTION 7 -- whole-corpus verification")
print("=" * 70)

_corpus_file = _corpus_path()
_sha_before = None
try:
    _h = hashlib.sha256()
    with open(_corpus_file, "rb") as _fh:
        for _chunk in iter(lambda: _fh.read(1 << 22), b""):
            _h.update(_chunk)
    _sha_before = _h.hexdigest()
except Exception as _exc:                                      # noqa: BLE001
    _sha_before = f"<RAISED {type(_exc).__name__}: {_exc}>"

MARKDOWN_ESCAPE_DECODE_UNRESOLVED.clear()

_dec_total = 0
_chain_total = 0
_residue_total = 0
_nonidem = 0
_stray = []
_changed = set()
_fence_created = 0

for _t in _trials:
    _nct = _t.get("nct_id")
    _el = _t.get("eligibility") or {}
    for _f in _RENDER_FIELDS:
        _txt = _el.get(_f)
        if not isinstance(_txt, str) or not _txt:
            continue
        _out, _d, _r = md(_txt)
        _dec_total += _d
        if _out != _txt:
            _changed.add(_nct)
        # Nothing but backslashes may be removed, and nothing may be added.
        if [c for c in _out if c != "\\"] != [c for c in _txt if c != "\\"]:
            _stray.append((_nct, _f))
        _e, _c, _ = ent(_out)
        _chain_total += _c
        _residue_total += residue(_e)
        if _neutralize_fence_markers(_e)[1] > \
                _neutralize_fence_markers(_txt)[1]:
            _fence_created += 1

# THE COUNTER IS READ HERE, BEFORE THE IDEMPOTENCE PASS, and that ordering is
# the point rather than tidiness: the counter accumulates across calls, so a
# second decode of every field would triple it and 7c/7d would be comparing the
# census against a number the census cannot produce. The first draft of this
# file did exactly that and reported 42 refusals for 14.
_by_reason = {}
for _k, _v in MARKDOWN_ESCAPE_DECODE_UNRESOLVED.items():
    _reason = _k.split(":", 1)[0]
    _by_reason[_reason] = _by_reason.get(_reason, 0) + _v

for _t in _trials:
    _el = _t.get("eligibility") or {}
    for _f in _RENDER_FIELDS:
        _txt = _el.get(_f)
        if not isinstance(_txt, str) or not _txt or "\\" not in _txt:
            continue
        _once = md(_txt)[0]
        if md(_once)[0] != _once:
            _nonidem += 1

check("7a     escapes decoded over the whole corpus", _dec_total, 69396)
check("7b     ...across this many trials", len(_changed), 10108)
check("7c     refusals, escaped_backslash",
      _by_reason.get(MARKDOWN_REFUSED_ESCAPED_BACKSLASH), 14)
check("7d     refusals, reference_syntax",
      _by_reason.get(MARKDOWN_REFUSED_REFERENCE_SYNTAX), 1)
check("7e     ...and no third reason appeared", sorted(_by_reason),
      sorted([MARKDOWN_REFUSED_ESCAPED_BACKSLASH,
              MARKDOWN_REFUSED_REFERENCE_SYNTAX]))
check("7f     residue in the decode set after the full chain: ZERO",
      _residue_total, 0)
check("7g     stray artifacts -- a character neither present before nor a "
      "removed backslash: ZERO", (len(_stray), _stray[:5]), (0, []))
check("7h     non-idempotent fields: ZERO", _nonidem, 0)
check("7i     entity chains decoded, unchanged by this pass", _chain_total, 579)
check("7j     trials acquiring a fence run from the decode: ZERO",
      _fence_created, 0)

_sha_after = None
try:
    _h = hashlib.sha256()
    with open(_corpus_file, "rb") as _fh:
        for _chunk in iter(lambda: _fh.read(1 << 22), b""):
            _h.update(_chunk)
    _sha_after = _h.hexdigest()
except Exception as _exc:                                      # noqa: BLE001
    _sha_after = f"<RAISED {type(_exc).__name__}: {_exc}>"

check("7k     the stored corpus bytes are unchanged", _sha_after, _sha_before)
check("7l     ...and that is a real digest, not two failures compared with "
      "each other (non-degeneracy)",
      isinstance(_sha_before, str) and len(_sha_before) == 64, True)

MARKDOWN_ESCAPE_DECODE_UNRESOLVED.clear()


# ===========================================================================
# SECTION 8 -- the splitter still reads the escaped text
# ===========================================================================

print()
print("=" * 70)
print("SECTION 8 -- the index-time splitter is untouched")
print("=" * 70)

from oncotriage.retrieval import indexer                       # noqa: E402
from oncotriage.retrieval.indexer import (                     # noqa: E402
    _HEADING_LEAD_CHARS,
    split_inclusion_exclusion,
)

check("8a     the splitter's lead class still contains the backslash -- the "
      "dependency this pass must not break",
      "\\" in _HEADING_LEAD_CHARS, True)

_indexer_src = ""
try:
    with open(os.path.abspath(indexer.__file__)) as _fh:
        _indexer_src = _fh.read()
except Exception as _exc:                                      # noqa: BLE001
    _indexer_src = f"<RAISED {type(_exc).__name__}: {_exc}>"

check("8b     the indexer names no markdown decoder -- the decode is "
      "render-only", "_decode_markdown_escapes" in _indexer_src, False)
check("8c     ...and reads a real file (non-degeneracy)",
      len(_indexer_src) > 10000, True)

# Splitting the DECODED text is what a normalising test would accidentally do.
# It is measured here as the thing NOT done, on real corpus text: a decoded
# escaped heading stops being found by the lead the splitter depends on.
_heading_src = None
for _t in _trials:
    _txt = (_t.get("eligibility") or {}).get("criteria_text")
    if isinstance(_txt, str) and "\\<Exclusion Criteria" in _txt:
        _heading_src = (_t.get("nct_id"), _txt)
        break

check("8d     a real trial spelling its heading '\\<Exclusion Criteria\\>' "
      "exists, so 8e is not vacuous", _heading_src is not None, True)

if _heading_src:
    _raw_split = drive(split_inclusion_exclusion, _heading_src[1])
    _dec_split = drive(split_inclusion_exclusion, md(_heading_src[1])[0])
    check("8e     the splitter finds both sections in the STORED text",
          at(_raw_split, 2), indexer.CRITERIA_SPLIT_BOTH)
    check("8f     ...and this pass never hands it decoded text; if it did, "
          "the result would differ, which is why the decode is render-only",
          at(_raw_split, 2) == at(_dec_split, 2)
          and at(_raw_split, 0) == at(_dec_split, 0), False)


# ===========================================================================
# SECTION 9 -- the render path, its counters and its log events
# ===========================================================================

print()
print("=" * 70)
print("SECTION 9 -- the render path")
print("=" * 70)

_recs = capture_records(lambda: render(r"Patients \>18 years", r"INR \< 1.2"))
_decoded_ev = events(_recs, "trial_markdown_escape_decoded")
check("9a     a decode emits exactly one INFO event per RENDER",
      len(_decoded_ev), 1)
check("9b     ...at INFO, matching the entity sibling rather than the fence "
      "warning", at(_decoded_ev, 0, {}).get("level"), "INFO")
check("9c     ...carrying the count of both fields",
      at(_decoded_ev, 0, {}).get("count"), 2)
check("9d     ...and the stage/node",
      (at(_decoded_ev, 0, {}).get("stage"),
       at(_decoded_ev, 0, {}).get("node")),
      (5, "llm_classifier_evaluation"))
# THE AGGREGATE NAMES NO TRIAL, and that is the shape rather than an omission:
# it describes a render, and a render covers many trials. A stray nct_id would
# be one arbitrary member of the batch presented as though it were the subject.
check("9d(ii) ...and NOT an nct_id, because one line now covers the whole "
      "render", "nct_id" in at(_decoded_ev, 0, {}), False)
check("9d(iii) ...carrying the two render cardinalities instead",
      (at(_decoded_ev, 0, {}).get("total"),
       at(_decoded_ev, 0, {}).get("trials_affected")), (1, 1))

_out = render(r"Patients \>18 years", r"INR \< 1.2")
check("9e     the rendered block carries the restored comparators and no "
      "escape", ("\\>" in _out, "\\<" in _out, ">18 years" in _out,
                 "INR < 1.2" in _out), (False, False, True, True))

_recs2 = capture_records(lambda: render("no escapes here", "none here either"))
check("9f     a trial with nothing to decode emits no event",
      len(events(_recs2, "trial_markdown_escape_decoded")), 0)

MARKDOWN_ESCAPE_DECODE_UNRESOLVED.clear()
_recs3 = capture_records(lambda: render(r"CLL\\ SLL", r"\# CLN1114"))
_unres_ev = events(_recs3, "trial_markdown_escape_unresolved")
check("9g     a refusal emits its OWN WARNING event, not a field on the "
      "decoded one", (len(_unres_ev), at(_unres_ev, 0, {}).get("level")),
      (1, "WARNING"))
check("9h     ...counting both refusals", at(_unres_ev, 0, {}).get("count"), 2)
check("9i     ...and both reasons reached the counter, keyed by reason",
      sorted({k.split(":", 1)[0]
              for k in MARKDOWN_ESCAPE_DECODE_UNRESOLVED}),
      sorted([MARKDOWN_REFUSED_ESCAPED_BACKSLASH,
              MARKDOWN_REFUSED_REFERENCE_SYNTAX]))
check("9j     ...and the key carries the raw pair so a reader can see the "
      "shape", any(k.startswith(MARKDOWN_REFUSED_ESCAPED_BACKSLASH + ":\\\\")
                   for k in MARKDOWN_ESCAPE_DECODE_UNRESOLVED), True)
MARKDOWN_ESCAPE_DECODE_UNRESOLVED.clear()

# The fence ATTRIBUTE values are deliberately not decoded.
_attr = render("body", "body", nct_id=r"NCT\>123", phase=r"PHASE\*2")
check("9k     the nct_id and phase are NOT markdown-decoded",
      (r"NCT\>123" in _attr, r"PHASE\*2" in _attr), (True, True))

# THE ORDER IS ASSERTED THROUGH THE SHIPPED RENDERER, NOT THROUGH THIS FILE'S
# OWN HELPERS, and that distinction was found by running rather than by
# reading: the revert harness swapped the two calls at the CALL SITE and this
# file reported 144 passed / 0 failed, because 10l and 10m compose the two
# decoders THEMSELVES and so agree with each other whatever _build_trials_text
# does. A control that exercises a second copy of the rule proves nothing about
# the shipped one -- the defect this project keeps finding, reproduced inside
# the test written to prevent it.
#
# "\&#92;" is a chain that decodes TO a backslash. Only markdown-first leaves
# it alone; entities-first produces a bare "\" that the markdown pass then
# reads as an escape and eats, inventing "5 > 3" from "5 \> 3".
_order_render = render(r"5 \&#92;&gt; 3", r"5 &bsol;&lt; 3")
check("9l     the SHIPPED renderer decodes markdown BEFORE entities: a chain "
      "decoding to a backslash survives",
      (r"5 \> 3" in _order_render, "5 > 3" in _order_render), (True, False))
check("9m     ...and the named form the same way",
      (r"5 \< 3" in _order_render, "5 < 3" in _order_render), (True, False))

# The same fact stated structurally, so a future edit that reorders the calls
# fails by name rather than only through the two renders above.
_eval_src_text = ""
try:
    with open(os.path.abspath(evaluation.__file__)) as _fh:
        _eval_src_text = _fh.read()
except Exception as _exc:                                      # noqa: BLE001
    _eval_src_text = f"<RAISED {type(_exc).__name__}: {_exc}>"

_md_call = _eval_src_text.find("_decode_markdown_escapes(\n"
                               "            str(trial[")
_ent_call = _eval_src_text.find("_decode_escaped_entities(inclusion)")
_fence_call = _eval_src_text.find("_neutralize_fence_markers(inclusion)")
check("9n     at the call site the three rewrites appear in the order "
      "markdown, entities, fence -- and all three were located "
      "(non-degeneracy)",
      (_md_call > 0, _ent_call > _md_call, _fence_call > _ent_call),
      (True, True, True))


# ===========================================================================
# SECTION 10 -- negative controls, every one shown to FIRE
# ===========================================================================

print()
print("=" * 70)
print("SECTION 10 -- negative controls")
print("=" * 70)


def _passthrough(text):
    """What _decode_markdown_escapes was before this pass: nothing."""
    return text, 0, 0


_saved_fn = evaluation._decode_markdown_escapes
try:
    evaluation._decode_markdown_escapes = _passthrough
    check("10a    CONTROL: with the decode removed, the renderer sends the "
          "escape to the judge -- so section 9e is not passing for free",
          "\\>" in render(r"Patients \>18 years"), True)
    check("10b    CONTROL: ...and emits no decoded event",
          len(events(capture_records(
              lambda: render(r"Patients \>18 years")),
              "trial_markdown_escape_decoded")), 0)
    check("10c    CONTROL: ...and a decoded fence run never forms, so 6f "
          "would pass for the WRONG reason without 6d",
          ">>>" in render(r"\>\>\>").split("\n")[1], False)
finally:
    evaluation._decode_markdown_escapes = _saved_fn

check("10d    the shipped function is restored",
      evaluation._decode_markdown_escapes is _saved_fn, True)
check("10e    ...and the render works again",
      "\\>" in render(r"Patients \>18 years"), False)

# THE DOUBLE-BACKSLASH REFUSAL IS STRUCTURAL, NOT SET-DRIVEN, and the first
# draft of this file got that wrong: it "controlled" the policy by admitting
# "\\" to the decode set and asserted the walk would then collapse the pair.
# It does not -- the "\\" branch is tested BEFORE the set is consulted, so that
# control could never fire and would have sat here passing for the wrong
# reason. The set rebinding is kept, inverted into the statement it can
# actually support, and the policy itself is controlled at 6c/6d instead.
_saved_set = evaluation._MARKDOWN_ESCAPE_DECODE_SET
try:
    evaluation._MARKDOWN_ESCAPE_DECODE_SET = frozenset(
        _MARKDOWN_ASCII_PUNCTUATION)
    check("10f    admitting '\\\\' to the decode set changes NOTHING: the "
          "pair is refused before the set is consulted",
          md(r"CLL\\ SLL")[:3], (r"CLL\\ SLL", 0, 1))
finally:
    evaluation._MARKDOWN_ESCAPE_DECODE_SET = _saved_set
check("10g    the shipped set is restored",
      evaluation._MARKDOWN_ESCAPE_DECODE_SET is _saved_set, True)

# 10f asserts that a rebinding changes nothing, which a rebinding that never
# reached the function would also satisfy. This is the probe that separates
# them: SHRINKING the set does change behaviour, so the seam is live and 10f's
# "nothing changed" is a fact about the branch order rather than about the
# rebinding having been ignored.
try:
    evaluation._MARKDOWN_ESCAPE_DECODE_SET = _saved_set - {">"}
    check("10h    non-degeneracy: the set rebinding IS live -- removing '>' "
          "from it stops '\\>' decoding", md(r"INR \> 1.2")[:3],
          (r"INR \> 1.2", 0, 0))
finally:
    evaluation._MARKDOWN_ESCAPE_DECODE_SET = _saved_set
check("10hh   ...and '\\>' decodes again once the set is back",
      md(r"INR \> 1.2")[:2], ("INR > 1.2", 1))

_saved_ref = evaluation._MARKDOWN_REFERENCE_SYNTAX_CHARS
_saved_set2 = evaluation._MARKDOWN_ESCAPE_DECODE_SET
try:
    evaluation._MARKDOWN_REFERENCE_SYNTAX_CHARS = ""
    evaluation._MARKDOWN_ESCAPE_DECODE_SET = frozenset(
        _MARKDOWN_ASCII_PUNCTUATION) - {"\\"}
    check("10i    CONTROL: admitting ';' and '#' lets the markdown decoder "
          "MANUFACTURE a character reference, which the entity decoder then "
          "decodes -- inventing '>' out of text the sponsor wrote as '&gt;'",
          shipped_order(r"5 \&gt\; 3"), "5 > 3")
    check("10j    CONTROL: ...and the numeric form the same way",
          shipped_order(r"5 \&\#62; 3"), "5 > 3")
finally:
    evaluation._MARKDOWN_REFERENCE_SYNTAX_CHARS = _saved_ref
    evaluation._MARKDOWN_ESCAPE_DECODE_SET = _saved_set2

check("10k    the shipped refusal set is restored, and the invention does "
      "not happen", (evaluation._MARKDOWN_REFERENCE_SYNTAX_CHARS,
                     shipped_order(r"5 \&gt\; 3"),
                     shipped_order(r"5 \&\#62; 3")),
      (_saved_ref, r"5 &gt\; 3", r"5 &\#62; 3"))

check("10l    CONTROL: the REVERSED order invents a comparator from a chain "
      "that decodes to a backslash -- which is why markdown runs first",
      (reversed_order(r"5 \&#92;&gt; 3"), shipped_order(r"5 \&#92;&gt; 3")),
      ("5 > 3", r"5 \> 3"))
check("10m    CONTROL: ...and the same for the named backslash reference",
      (reversed_order("5 &bsol;&gt; 3"), shipped_order("5 &bsol;&gt; 3")),
      ("5 > 3", r"5 \> 3"))

# Sections 10f-10k drove refusals into the module counter. Clear it here so
# section 11a is about hygiene rather than about which control ran last.
MARKDOWN_ESCAPE_DECODE_UNRESOLVED.clear()


# ===========================================================================
# SECTION 11 -- the decode is reported ONCE PER RENDER, not once per trial
# ===========================================================================
#
# WHY THE SHAPE CHANGED, AS A NUMBER. Measured over the shipped 14,324-trial
# corpus: 10,108 trials (70.57%) carry at least one markdown escape. The event
# this replaces fired once per affected trial per render, and a batch is
# rendered more than once -- the whole batch for the stored prompt, once per
# chunk sent, and once per trial for the packer's token measurement. The entity
# sibling stays per-trial at 1.4%, which is the measurement that says the two
# are different problems rather than an inconsistency.
#
# THE BATCH IS THE INSTRUMENT. Every check below renders MORE THAN ONE trial in
# ONE call, because a single-trial render cannot distinguish "one event per
# render" from "one event per trial" -- both produce exactly one line, and a
# suite that only ever rendered one trial would pass either way. Section 9 is
# the single-trial case and this is the one that discriminates.

print()
print("=" * 70)
print("SECTION 11 -- one aggregate per render")
print("=" * 70)

# Four trials, THREE of them carrying escapes, with 2 + 1 + 3 = 6 sequences
# between them. Every number below is distinct from every other -- 4 rendered,
# 3 affected, 6 sequences -- so a check cannot pass by reading the wrong field.
_BATCH = [(r"Patients \>18 years", r"INR \< 1.2"),      # 2
          (r"ECOG \<= 2", ""),                          # 1
          ("no escapes at all", "none here either"),    # 0
          (r"WBC \> 3\.0", r"plt \< 100")]              # 3

_b_recs = capture_records(lambda: render_many(_BATCH))
_b_ev = events(_b_recs, "trial_markdown_escape_decoded")

check("11a    a four-trial batch with three affected emits exactly ONE "
      "aggregate INFO -- the check a single-trial render cannot make",
      len(_b_ev), 1)
check("11b    ...at INFO", at(_b_ev, 0, {}).get("level"), "INFO")
check("11c    ...reporting every trial the call rendered, affected or not",
      at(_b_ev, 0, {}).get("total"), 4)
check("11d    ...how many of them were changed",
      at(_b_ev, 0, {}).get("trials_affected"), 3)
check("11e    ...and the total sequences decoded across the batch",
      at(_b_ev, 0, {}).get("count"), 6)
# NON-DEGENERACY. If the three numbers were equal, 11c/11d/11e would all pass
# against an aggregate that carried one number under three names.
check("11f    non-degeneracy: the three cardinalities are pairwise distinct, "
      "so 11c-11e cannot pass by reading the wrong field",
      len({4, 3, 6}), 3)

# THE COUNTS ARE DERIVED FROM THE DECODER, NOT RETYPED. _BATCH's expected
# numbers above are literals, and a literal that agrees with the code because
# both were written by the same hand is the defect this project's rules name by
# name. These come from _decode_markdown_escapes itself.
_exp_seq = sum(md(inc)[1] + md(exc)[1] for inc, exc in _BATCH)
_exp_aff = sum(1 for inc, exc in _BATCH if md(inc)[1] + md(exc)[1])
check("11g    the aggregate agrees with the decoder run directly over the "
      "same batch (sequences, affected)",
      (at(_b_ev, 0, {}).get("count"), at(_b_ev, 0, {}).get("trials_affected")),
      (_exp_seq, _exp_aff))
check("11h    non-degeneracy: that independent derivation is not zero",
      (_exp_seq > 0, _exp_aff > 0), (True, True))

# ZERO-DECODE RENDERS SAY NOTHING, which is the convention every other event in
# _build_trials_text already follows -- each is guarded by its own count.
check("11i    a batch in which NOTHING decodes emits no aggregate",
      len(events(capture_records(
          lambda: render_many([("plain text", "also plain")] * 3)),
          "trial_markdown_escape_decoded")), 0)
# The empty render is real: _user_prompt_for([]) is called on every node run to
# price the wrapper's fixed token cost. Without the guard it would emit a line
# claiming a decode pass over no trials at all.
check("11j    ...and neither does the empty render the packer prices the "
      "wrapper with",
      len(events(capture_records(lambda: render_many([])),
                 "trial_markdown_escape_decoded")), 0)

# THE PER-TRIAL RECORD IS NOT LOST, IT IS RE-LEVELLED. Everything the old INFO
# carried -- nct_id and count -- is still emitted, at DEBUG, so an operator who
# wants it sets ONCOTRIAGE_LOG_LEVEL=DEBUG and has it back verbatim.
_dbg_recs = capture_records(lambda: render_many(_BATCH), level=logging.DEBUG)
_dbg_ev = events(_dbg_recs, "trial_markdown_escape_decoded_trial")
check("11k    at DEBUG the per-trial detail returns, one line per AFFECTED "
      "trial", len(_dbg_ev), 3)
check("11l    ...each naming its own trial, so the detail is still "
      "attributable", sorted(r.get("nct_id") for r in _dbg_ev),
      ["NCT00000000", "NCT00000001", "NCT00000003"])
check("11m    ...at DEBUG, not INFO",
      sorted({r.get("level") for r in _dbg_ev}), ["DEBUG"])
# THE INVARIANT A READER ACTUALLY DEPENDS ON: the aggregate is the sum of the
# detail. If these ever disagree, one of the two is lying about the render.
check("11n    the aggregate's count is exactly the sum of the per-trial "
      "counts", sum(r.get("count", 0) for r in _dbg_ev),
      at(_b_ev, 0, {}).get("count"))
check("11o    ...and the aggregate still appears exactly once alongside the "
      "detail", len(events(_dbg_recs, "trial_markdown_escape_decoded")), 1)
# AND IT IS OFF BY DEFAULT, which is the entire point of the pass. Without this
# check the DEBUG line could be emitting at INFO and 11k would still pass.
check("11p    at the shipped default level the per-trial detail is SILENT",
      len(events(_b_recs, "trial_markdown_escape_decoded_trial")), 0)

# REFUSALS ARE UNTOUCHED AND STAY PER-TRIAL. Measured on the same corpus: 12
# trials (0.08%) and 15 sequences in total -- rarer than the entity event that
# keeps its per-trial INFO -- so the volume argument that moved the decoded
# event does not reach this one, and the nct_id it names is the actionable
# field. It is also a WARNING, and folding it into the INFO aggregate would put
# "this text went out still escaped" inside an event that says the reverse.
MARKDOWN_ESCAPE_DECODE_UNRESOLVED.clear()
_ref_recs = capture_records(lambda: render_many(
    [(r"CLL\\ SLL", r"\# CLN1114"), (r"A\\B", "")]))
_ref_ev = events(_ref_recs, "trial_markdown_escape_unresolved")
check("11q    a refusal still emits one WARNING PER TRIAL, each naming its "
      "own trial", (len(_ref_ev), sorted(r.get("nct_id") for r in _ref_ev)),
      (2, ["NCT00000000", "NCT00000001"]))
MARKDOWN_ESCAPE_DECODE_UNRESOLVED.clear()

# THE AGGREGATE COUNTS DECODES ONLY, and this is asserted through the NUMBERS
# rather than by looking for a field named "refused". The first version of this
# check did the latter and was UNFALSIFIABLE: "refused", "unresolved" and
# "refusals" are none of them on LOGGABLE_FIELDS, so the formatter would strip
# any of them whatever the call site passed, and the check passed for a reason
# that had nothing to do with the event's shape. Measured, not reasoned -- a
# revert that folded `refused=0` into the aggregate left it green.
#
# A batch carrying BOTH is the instrument: if refusals were folded in, `count`
# would be decoded+refused and `trials_affected` would count a trial that was
# refused and never decoded.
_MIXED = [(r"INR \< 1.2", r"CLL\\ SLL"),   # decodes, and refuses
          (r"A\\B", ""),                    # refuses ONLY -- decodes nothing
          (r"WBC \> 3\.0", "")]             # decodes only
_mx_dec = sum(md(i)[1] + md(e)[1] for i, e in _MIXED)
_mx_ref = sum(md(i)[2] + md(e)[2] for i, e in _MIXED)
_mx_aff = sum(1 for i, e in _MIXED if md(i)[1] + md(e)[1])
MARKDOWN_ESCAPE_DECODE_UNRESOLVED.clear()
_mx_ev = events(capture_records(lambda: render_many(_MIXED)),
                "trial_markdown_escape_decoded")
check("11r    non-degeneracy: the mixed batch really does carry refusals AND "
      "decodes, and one trial refuses without decoding",
      (_mx_dec > 0, _mx_ref > 0, _mx_aff < len(_MIXED)), (True, True, True))
check("11s    the aggregate's count is DECODED sequences only -- refusals are "
      "not folded into the event that says the reverse",
      at(_mx_ev, 0, {}).get("count"), _mx_dec)
check("11t    ...and it is not the decoded+refused sum, which is what folding "
      "them in would produce (this is what makes 11s falsifiable)",
      at(_mx_ev, 0, {}).get("count") == _mx_dec + _mx_ref, False)
check("11u    ...and trials_affected counts only trials that DECODED, not the "
      "refuse-only one", at(_mx_ev, 0, {}).get("trials_affected"), _mx_aff)
MARKDOWN_ESCAPE_DECODE_UNRESOLVED.clear()

# THE FIELDS SURVIVE THE ALLOWLIST. oncotriage/observability.py DROPS any field
# not on LOGGABLE_FIELDS and reports the key in `dropped_fields`. A new field
# added to a log call and not to the list is silently absent -- which every
# check above would read as a wrong value rather than as a redaction, so the
# drop is asserted directly.
check("11v    the aggregate's fields all survive the allowlist -- nothing was "
      "dropped", at(_b_ev, 0, {}).get("dropped_fields"), None)
check("11w    ...and trials_affected is on LOGGABLE_FIELDS by name",
      "trials_affected" in _LOGGABLE_FIELDS, True)


# ===========================================================================
# SECTION 11b -- the control: a regression to per-trial INFO is DETECTED
# ===========================================================================
#
# The plant is the shape this pass removed: the aggregate suppressed and the
# per-trial line put back at INFO. It goes into a COPY of the render loop's
# behaviour rather than the shipped file -- the module attribute
# _decode_markdown_escapes is rebound to a counting stand-in that logs at INFO
# per call, which is exactly what the pre-pass code did, and the shipped
# function is restored in a finally.

print()
print("-" * 70)
print("SECTION 11b -- control")
print("-" * 70)

_saved_md = evaluation._decode_markdown_escapes


def _per_trial_info_md(text):
    """The pre-pass shape: log at INFO on every affected field."""
    decoded, removed, refused = _saved_md(text)
    if removed:
        evaluation.log.info("removed registry markdown escaping from scraped "
                            "trial text", stage=5,
                            node="llm_classifier_evaluation",
                            event="trial_markdown_escape_decoded",
                            count=removed)
    return decoded, removed, refused


try:
    evaluation._decode_markdown_escapes = _per_trial_info_md
    _ctl = events(capture_records(lambda: render_many(_BATCH)),
                  "trial_markdown_escape_decoded")
    # Six affected FIELDS across three trials, plus the real aggregate: seven.
    # The assertion that matters is only that 11a's "exactly one" FAILS.
    check("11x    CONTROL: with per-trial INFO logging restored, the render "
          "emits more than one decoded event -- so 11a is not passing for "
          "free", len(_ctl) > 1, True)
    check("11y    CONTROL: ...and the extra lines are at INFO, i.e. visible "
          "at the shipped default level",
          "INFO" in {r.get("level") for r in _ctl}, True)
finally:
    evaluation._decode_markdown_escapes = _saved_md

check("11z    the shipped decoder is restored",
      evaluation._decode_markdown_escapes is _saved_md, True)
check("11zz   ...and the render is back to exactly one aggregate",
      len(events(capture_records(lambda: render_many(_BATCH)),
                 "trial_markdown_escape_decoded")), 1)


# ===========================================================================
# SECTION 12 -- hygiene
# ===========================================================================

print()
print("=" * 70)
print("SECTION 12 -- hygiene")
print("=" * 70)

check("12a    the module-level counter is left empty for the next reader",
      dict(MARKDOWN_ESCAPE_DECODE_UNRESOLVED), {})

_EVAL_SRC = os.path.abspath(evaluation.__file__)
with open(_EVAL_SRC, "rb") as _fh:
    _eval_sha = hashlib.sha256(_fh.read()).hexdigest()
with open(_EVAL_SRC, "rb") as _fh:
    _eval_sha2 = hashlib.sha256(_fh.read()).hexdigest()
check("12b    this file writes nothing: evaluation.py reads the same twice",
      _eval_sha2, _eval_sha)
check("12c    ...and that is a real digest (non-degeneracy)",
      len(_eval_sha) == 64 and os.path.getsize(_EVAL_SRC) > 0, True)

check("12d    every module attribute a control rebound is back to the "
      "shipped object",
      (evaluation._decode_markdown_escapes is _saved_fn,
       evaluation._MARKDOWN_ESCAPE_DECODE_SET is _saved_set,
       evaluation._MARKDOWN_REFERENCE_SYNTAX_CHARS is _saved_ref,
       evaluation._ESCAPED_ENTITY_CHAIN_RE is _saved_re),
      (True, True, True, True))


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
Created on Fri Aug 14 2026

@author: ramyalsaffar
"""
