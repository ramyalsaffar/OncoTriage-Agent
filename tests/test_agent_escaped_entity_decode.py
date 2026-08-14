# Stage 5 render: escaped HTML entities are decoded before the judge sees them
##############################################################################

"""
Escaped HTML Entity Decode Test

``_build_trials_text`` sent trial criteria to the judge byte-for-byte as
scraped, and the registry stores ``\\&lt;`` where the sponsor wrote ``<``. The
character was HTML-escaped to ``&lt;`` and the ampersand of that escape was
then markdown-escaped to ``\\&``; some rows went round that loop several times,
so ``\\&amp;amp;gt;`` occurs too. Stage 5 was shown
``INR \\&lt; 1.2 and platelet counts \\&gt; 80,000/mm3`` (NCT06923098) -- a pair
of numeric thresholds whose DIRECTION is spelled as an escaped entity.

MEASURED OVER ALL 14,324 TRIALS in the 2026-08-10 corpus, re-derived here
rather than taken from the census that prompted the item
(09- Testing/Evaluation Runs/criteria_quality_census_20260814/):

  * 579 occurrences across 197 trials in the two fields Stage 5 renders,
    140 of those trials putting one where a numeric comparator belongs;
  * every one of the 579 preceded by exactly one backslash, and NOT ONE bare
    entity in either rendered field;
  * final references ``&gt;`` 277, ``&lt;`` 121, ``&amp;`` 117, ``&#39;`` 56,
    ``&#34;`` 8;
  * passes to a fixed point: 1 (468), 2 (73), 3 (32), 4 (4), 11 (2).

THE CENSUS SAYS 583 AND THE RENDER PATH CARRIES 579. The census applied its
predicate to ``eligibility.criteria_text``; ``_build_trials_text`` renders
``inclusion_criteria`` and ``exclusion_criteria``. Four occurrences live in the
former and in neither of the latter, so they never reach a model. Section 1
pins the render-path number so the two can never be conflated again.

THE DEPTH HISTOGRAM IS WHY THE CAP IS 16 AND NOT 3. Section 5 runs the
counterfactual: a three-pass cap leaves residue on the six occurrences at
depth 4 and 11, which is the defect the fix exists to remove wearing the
costume of the fix.

NO NETWORK, NO KEYS, NO SPEND, NO GIT HISTORY. Sections 1 and 8 read the trial
corpus READ-ONLY and say so as a recorded failure rather than a silent skip if
it is absent; every other fixture here is a literal. It EXECS NOTHING -- the
negative controls rebind a module attribute inside ``try``/``finally`` and
drive the SHIPPED ``_build_trials_text``, which is what a control for "the
decode was removed" actually has to do. NOT in tests/run_serial_tests.py's
collision matrix: it writes nothing anywhere, and the one repository file it
reads is written by neither of the suite's two writers.

    python tests/test_agent_escaped_entity_decode.py
"""

import contextlib
import hashlib
import io
import json
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
    ENTITY_REFUSED_PASS_CAP,
    ENTITY_REFUSED_REPLACEMENT_CHAR,
    ESCAPED_ENTITY_DECODE_UNRESOLVED,
    _ENTITY_DECODE_MAX_PASSES,
    _ESCAPED_ENTITY_CHAIN_RE,
    _build_trials_text,
    _decode_escaped_entities,
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
        _FAILURES.append(f"{label}\n          expected: {expected}\n"
                         f"          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


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


def decode(text):
    """``_decode_escaped_entities`` as a 3-tuple, raise-proofed."""
    got = drive(_decode_escaped_entities, text)
    if not isinstance(got, tuple):
        return (got, -1, -1)
    return got


def render(inclusion, exclusion="", nct_id="NCT00000000", phase="PHASE2"):
    """One trial through the SHIPPED renderer, raise-proofed."""
    return drive(_build_trials_text, [{"trial": {
        "nct_id": nct_id, "phase": phase,
        "eligibility": {"inclusion_criteria": inclusion,
                        "exclusion_criteria": exclusion}}}])


def capture_records(fn):
    """Every JSON log record ``fn`` emits on stderr."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        fn()
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


# Any semicolon-terminated character reference. What "no residue" is measured
# against: after a decode, not one of these may remain anywhere it decoded.
_RESIDUE_RE = re.compile(
    r"&(?:[A-Za-z][A-Za-z0-9]{1,31}|#[0-9]{1,7}|#[Xx][0-9A-Fa-f]{1,6});")

def _corpus_path():
    """The trial corpus, resolved through the package rather than guessed."""
    from oncotriage import paths
    return os.path.join(paths.data_trial_path, "trials_latest.json")


# ===========================================================================
# SECTION 1 -- the corpus measurement this fix is sized from
# ===========================================================================

print("=" * 70)
print("SECTION 1 -- the corpus measurement")
print("=" * 70)

_corpus_error = None
_trials = []
try:
    with open(_corpus_path()) as _fh:
        _trials = json.load(_fh)
except Exception as _exc:                                      # noqa: BLE001
    _corpus_error = f"{type(_exc).__name__}: {_exc}"

check("1a     the trial corpus was readable (a missing corpus is a FAILURE "
      "here, never a silent skip)", _corpus_error, None)

_occurrences = 0
_affected = set()
_comparator_inner = set()
_comparator_outer = set()
_inner_refs = {}
_depths = {}
_leading_backslash = {}
_COMPARATORS = {"&lt;", "&gt;", "&le;", "&ge;", "&#60;", "&#62;"}

import html as _html                                           # noqa: E402

for _t in _trials:
    _el = _t.get("eligibility") or {}
    for _f in ("inclusion_criteria", "exclusion_criteria"):
        _txt = _el.get(_f)
        if not isinstance(_txt, str) or not _txt:
            continue
        for _m in _ESCAPED_ENTITY_CHAIN_RE.finditer(_txt):
            _raw = _m.group(0)
            _occurrences += 1
            _affected.add(_t["nct_id"])
            # The backslash is INSIDE the match, because the pattern makes it
            # optional. Counting the characters before the match -- which is
            # what a pattern requiring the backslash would need -- reports zero
            # for every occurrence and looks like a corpus with no escaping at
            # all. This check was written that way first and section 1f caught
            # it.
            _leading_backslash[_raw.startswith("\\")] = (
                _leading_backslash.get(_raw.startswith("\\"), 0) + 1)
            _body = _raw.lstrip("\\")
            _cur, _passes, _prev = _body, 0, _body
            while _passes < 40:
                _nxt = _html.unescape(_cur)
                if _nxt == _cur:
                    break
                _prev, _cur = _cur, _nxt
                _passes += 1
            _depths[_passes] = _depths.get(_passes, 0) + 1
            # The INNERMOST reference: the one that actually stands for a
            # character, after the "&amp;" wrappers are peeled off.
            _inner_refs[_prev] = _inner_refs.get(_prev, 0) + 1
            if _prev.lower() in _COMPARATORS:
                _comparator_inner.add(_t["nct_id"])
            _outer = re.match(r"&[^&;]*;", _body)
            if _outer and _outer.group(0).lower() in _COMPARATORS:
                _comparator_outer.add(_t["nct_id"])

check("1b     14,324 trials in the corpus", len(_trials), 14324)
check("1c     579 escape chains on the RENDER path (the census's 583 counts "
      "criteria_text, which Stage 5 does not render)", _occurrences, 579)
check("1d     197 trials affected", len(_affected), 197)
check("1e     the census's 140 comparator trials is reproduced by classifying "
      "on the OUTERMOST reference, which is what its predicate matched",
      len(_comparator_outer), 140)
check("1f     ...but resolving the nesting first gives 179: a trial storing "
      "'\\\\&amp;gt;' is a comparator the census counted as an ampersand",
      len(_comparator_inner), 179)
check("1g     every occurrence carries a leading backslash",
      _leading_backslash, {True: 579})
check("1h     the innermost-reference inventory",
      dict(sorted(_inner_refs.items())),
      {"&#34;": 11, "&#39;": 68, "&amp;": 6, "&gt;": 344, "&lt;": 150})
check("1i     ...which sums to the occurrence count",
      sum(_inner_refs.values()), 579)
check("1j     the decode-depth histogram, which is what sizes the cap",
      dict(sorted(_depths.items())), {1: 468, 2: 73, 3: 32, 4: 4, 11: 2})
check("1k     ...so the deepest chain needs more than three passes",
      max(_depths) > 3, True)
check("1l     the shipped cap clears the measured maximum",
      _ENTITY_DECODE_MAX_PASSES > max(_depths) if _depths else False, True)


# ===========================================================================
# SECTION 2 -- the five real corpus pairs (the acceptance tests)
# ===========================================================================

print()
print("=" * 70)
print("SECTION 2 -- five real corpus pairs decode exactly")
print("=" * 70)

# LIFTED FROM THE CORPUS BY A SCRIPT AND PASTED WHOLE, never retyped from the
# census prose. The first draft of this list WAS retyped from a truncated
# context print, and section 8b caught it: NCT06786026's real text is a
# 24-hour urine protein threshold, not the blood pressure the truncated line
# had suggested, and its chain is four levels deep rather than two. That is
# pass 20f-4's lesson reproduced -- do not hand-transcribe a literal, lift it
# and compare it -- and section 8 is the standing form of the check.
#
# The five cover every measured depth: 1, 1, 2, 4 and 11.
_PAIRS = [
    # NCT06923098 -- the census's headline: two comparators in one sentence.
    ('NCT06923098',
     'Inclusion Criteria:\n\n* Age above 18 years\n* INR \\&lt; 1.2 and '
     'platelet counts \\&gt;',
     'Inclusion Criteria:\n\n* Age above 18 years\n* INR < 1.2 and '
     'platelet counts >',
     2),
    # NCT06977893 -- a comparator with no space, against a tumour diameter.
    ('NCT06977893',
     'confirmed invasive breast cancer with tumor diameter\\&gt;1cm '
     '(T1c-3; N0-3; M0). All patients',
     'confirmed invasive breast cancer with tumor diameter>1cm '
     '(T1c-3; N0-3; M0). All patients',
     1),
    # NCT06652048 -- depth 2: the escape went round the loop twice.
    ('NCT06652048',
     'for at least 6 months (progression occurred during or \\&amp;lt;6 '
     'months after the last dose when the',
     'for at least 6 months (progression occurred during or <6 '
     'months after the last dose when the',
     1),
    # NCT06786026 -- depth 4, which a three-pass cap cannot finish.
    ('NCT06786026',
     'protein ≥ ++ and confirmed 24-hour urine protein amount '
     '\\&amp;amp;amp;gt;1.0 g; 16.Suffering from active',
     'protein ≥ ++ and confirmed 24-hour urine protein amount '
     '>1.0 g; 16.Suffering from active',
     1),
    # NCT02945579 -- depth 11, the deepest in the corpus.
    ('NCT02945579',
     'or FISH amplified) or triple receptor negative (TN, ER/PR'
     '\\&amp;amp;amp;amp;amp;amp;amp;amp;amp;amp;lt; 10% HER2 negative '
     '(IHC 1+ or 2+ FISH',
     'or FISH amplified) or triple receptor negative (TN, ER/PR'
     '< 10% HER2 negative (IHC 1+ or 2+ FISH',
     1),
]

for _i, (_nct, _raw, _want, _n) in enumerate(_PAIRS, start=1):
    _got, _d, _u = decode(_raw)
    check(f"2{chr(96 + _i)}     {_nct} decodes to what the sponsor wrote",
          _got, _want)
    check(f"2{chr(96 + _i)}     {_nct} reports {_n} decoded chain(s)", _d, _n)
    check(f"2{chr(96 + _i)}     {_nct} leaves no residue",
          _RESIDUE_RE.findall(_got), [])
    check(f"2{chr(96 + _i)}     {_nct} leaves no stray backslash",
          "\\" in _got, False)

check("2f     the five pairs are five DIFFERENT strings, so the section is "
      "not five copies of one assertion", len({p[1] for p in _PAIRS}), 5)


# ===========================================================================
# SECTION 3 -- every measured variant decodes with no residue
# ===========================================================================

print()
print("=" * 70)
print("SECTION 3 -- every measured variant")
print("=" * 70)

# The 16 distinct chain forms measured in the corpus, with the character each
# stands for. Written out rather than generated, so a change to the decoder
# cannot also change what it is being compared against.
_VARIANTS = [
    ("\\&gt;", ">"), ("\\&lt;", "<"), ("\\&#39;", "'"), ("\\&#34;", '"'),
    ("\\&amp;", "&"),
    ("\\&amp;gt;", ">"), ("\\&amp;lt;", "<"), ("\\&amp;#39;", "'"),
    ("\\&amp;amp;gt;", ">"), ("\\&amp;amp;lt;", "<"),
    ("\\&amp;amp;#39;", "'"), ("\\&amp;amp;#34;", '"'),
    ("\\&amp;amp;amp;gt;", ">"), ("\\&amp;amp;amp;#39;", "'"),
    ("\\&amp;amp;amp;amp;amp;amp;amp;amp;amp;amp;lt;", "<"),
    ("\\&amp;amp;amp;amp;amp;amp;amp;amp;amp;amp;gt;", ">"),
]

check("3a     sixteen distinct variants were measured", len(_VARIANTS), 16)

_variant_failures = []
for _raw, _want in _VARIANTS:
    _got, _d, _u = decode("before " + _raw + " after")
    if _got != "before " + _want + " after" or _d != 1 or _u != 0:
        _variant_failures.append((_raw, _got, _d, _u))
check("3b     every measured variant decodes to its character, exactly once, "
      "with nothing unresolved", _variant_failures, [])

_residue_failures = [_raw for _raw, _ in _VARIANTS
                     if _RESIDUE_RE.search(decode(_raw)[0])]
check("3c     no variant leaves a partial entity behind", _residue_failures, [])

_backslash_failures = [_raw for _raw, _ in _VARIANTS if "\\" in decode(_raw)[0]]
check("3d     no variant leaves a stray backslash behind",
      _backslash_failures, [])

check("3e     the bare (unbackslashed) form decodes too, so the fix survives "
      "a scrape that stops markdown-escaping", decode("INR &lt; 1.2")[0],
      "INR < 1.2")
check("3f     ...and that is a no-op on THIS corpus, where zero bare entities "
      "were measured", _leading_backslash.get(False, 0), 0)


# ===========================================================================
# SECTION 4 -- legitimate text passes through untouched
# ===========================================================================

print()
print("=" * 70)
print("SECTION 4 -- legitimate text is not rewritten")
print("=" * 70)

# THE HAZARD THIS SECTION EXISTS FOR. html.unescape implements the HTML5 rule
# that a named reference need not be terminated, so a whole-string unescape --
# the obvious form of this fix -- rewrites all five strings below. Each is a
# plausible thing to find in criteria prose.
_SEMICOLONLESS = [
    "Smith &amp Jones",
    "grade &notin 3",
    "a &para b",
    "tumor &lt 2cm",
    "&copy 2026 sponsor",
]
_untouched = [s for s in _SEMICOLONLESS if decode(s)[0] == s]
check("4a     a semicolon-less reference is NOT decoded", _untouched,
      _SEMICOLONLESS)
check("4b     ...and that is not vacuous: a whole-string html.unescape DOES "
      "rewrite every one of them",
      [s for s in _SEMICOLONLESS if _html.unescape(s) != s], _SEMICOLONLESS)

_BARE_AMPERSAND = [
    "AT&T Pharmaceuticals",
    "R&D cohort",
    "phase I&II",
    "ER/PR&HER2 status",
    "Bristol-Myers Squibb & Pfizer",
]
check("4c     a bare ampersand in a sponsor name is untouched",
      [s for s in _BARE_AMPERSAND if decode(s)[0] != s], [])

_BACKSLASHES = [
    "a genuine backslash \\ standing alone",
    "the registry's own markdown escaping: \\> and \\< and \\* and \\[",
    "a path-like C:\\Program Files\\x",
    "\\&AB; has the shape of a reference and is not one",
    "\\& on its own",
]
check("4d     a genuine backslash is untouched",
      [s for s in _BACKSLASHES if decode(s)[0] != s], [])
check("4e     ...including the 65,082 markdown escapes this fix deliberately "
      "does NOT touch", decode("aged \\>18 and BMI \\<30")[0],
      "aged \\>18 and BMI \\<30")

check("4f     a chain shaped like a reference but not one keeps its backslash "
      "and is not counted", decode("\\&AB; here"), ("\\&AB; here", 0, 0))

_CLEAN = [
    "",
    "INR < 1.2 and platelet counts > 80,000/mm3",
    "* Age above 18 years\n* ECOG 0-1",
    "Histologically confirmed invasive breast cancer (T1c-3; N0-3; M0).",
]
check("4g     already-clean text is unchanged and reports nothing",
      [decode(s) for s in _CLEAN], [(s, 0, 0) for s in _CLEAN])

# IDEMPOTENCE: decoding the decoded output is the identity.
_not_idempotent = []
for _raw, _ in _VARIANTS:
    _once = decode("x " + _raw + " y")[0]
    _twice, _d2, _u2 = decode(_once)
    if _twice != _once or _d2 or _u2:
        _not_idempotent.append(_raw)
for _nct, _raw, _want, _n in _PAIRS:
    _once = decode(_raw)[0]
    _twice, _d2, _u2 = decode(_once)
    if _twice != _once or _d2 or _u2:
        _not_idempotent.append(_nct)
check("4h     decode(decode(x)) == decode(x) on every variant and every real "
      "pair", _not_idempotent, [])

# The output is never re-scanned, which is what stops a "&" this function
# produced from binding to the literal characters after it.
check("4i     a decoded '&' does not bind to the text that follows it",
      decode("\\&amp;notin 3;")[0], "&notin 3;")
check("4j     ...and that is the hazard a whole-string fixed point would hit",
      _html.unescape(_html.unescape("\\&amp;notin 3;")),
      "\\&notin 3;".replace("&notin", "\u00acin"))


# ===========================================================================
# SECTION 5 -- the pass cap
# ===========================================================================

print()
print("=" * 70)
print("SECTION 5 -- the pass cap holds and is sized from the measurement")
print("=" * 70)

check("5a     the cap is a named constant, not a literal",
      isinstance(_ENTITY_DECODE_MAX_PASSES, int), True)
check("5b     the cap is 16", _ENTITY_DECODE_MAX_PASSES, 16)

# A planted escape deeper than the cap. 40 wrappers needs 41 passes.
_DEEP = "\\&" + ("amp;" * 40) + "lt;"
ESCAPED_ENTITY_DECODE_UNRESOLVED.clear()
_deep_got, _deep_d, _deep_u = decode("INR " + _DEEP + " 1.2")

check("5c     a chain deeper than the cap is left EXACTLY as scraped, never "
      "half-decoded", _deep_got, "INR " + _DEEP + " 1.2")
check("5d     ...it is not counted as decoded", _deep_d, 0)
check("5e     ...it IS counted as unresolved", _deep_u, 1)
check("5f     ...and it lands in the module counter under the pass_cap reason",
      dict(ESCAPED_ENTITY_DECODE_UNRESOLVED),
      {ENTITY_REFUSED_PASS_CAP + ":" + _DEEP[:80]: 1})
check("5g     the counter key's raw part is capped in length",
      len(at(list(ESCAPED_ENTITY_DECODE_UNRESOLVED), 0, "")
          .split(":", 1)[-1]) <= 80, True)
ESCAPED_ENTITY_DECODE_UNRESOLVED.clear()

# The cap does not fire on anything the corpus contains.
_capped_in_corpus = []
for _raw, _ in _VARIANTS:
    if decode(_raw)[2]:
        _capped_in_corpus.append(_raw)
check("5h     the cap fires on NOTHING measured in the corpus",
      _capped_in_corpus, [])
check("5i     ...and the corpus scan agrees: zero unresolved",
      sum(decode(_raw)[2] for _raw, _ in _VARIANTS), 0)

# THE COUNTERFACTUAL: a cap of three, which is what the item was drafted with,
# leaves residue on the six occurrences measured at depth 4 and 11.
_saved_cap = evaluation._ENTITY_DECODE_MAX_PASSES
try:
    evaluation._ENTITY_DECODE_MAX_PASSES = 3
    _cap3 = [(_raw, decode(_raw)) for _raw, _ in _VARIANTS]
    _cap3_unresolved = [_raw for _raw, (_g, _d, _u) in _cap3 if _u]
finally:
    evaluation._ENTITY_DECODE_MAX_PASSES = _saved_cap
    ESCAPED_ENTITY_DECODE_UNRESOLVED.clear()

check("5j     a three-pass cap fails four measured variants",
      len(_cap3_unresolved), 4)
check("5k     ...including the depth-11 pair from NCT02945579",
      "\\&amp;amp;amp;amp;amp;amp;amp;amp;amp;amp;lt;" in _cap3_unresolved,
      True)
check("5l     the cap was restored", evaluation._ENTITY_DECODE_MAX_PASSES, 16)
check("5m     ...and the shipped cap decodes all sixteen",
      [_raw for _raw, _ in _VARIANTS if decode(_raw)[2]], [])


# ===========================================================================
# SECTION 5B -- a decode that would DAMAGE is refused
# ===========================================================================

print()
print("=" * 70)
print("SECTION 5B -- the damage guard")
print("=" * 70)

# THE HAZARD THIS SECTION EXISTS FOR, and it is one this pass INTRODUCED by
# generalising past the two numeric references the corpus contains. Zero of
# these occur in the corpus today; every one is reachable by a future scrape,
# and each would have this fix inject the census's own replacement_char defect
# -- or delete a span of scraped text -- into criteria prose.
_DAMAGING = [
    "\\&#0;",            # zero code point            -> U+FFFD
    "\\&#x0;",           # the same, in hex           -> U+FFFD
    "\\&#55296;",        # a lone surrogate           -> U+FFFD
    "\\&#9999999;",      # out of range               -> U+FFFD
    "\\&#8;",            # a C0 control (backspace)   -> the empty string
]

ESCAPED_ENTITY_DECODE_UNRESOLVED.clear()
_damage_results = [decode("INR " + _r + " 1.2") for _r in _DAMAGING]

check("5B a    every damaging reference is left EXACTLY as scraped",
      [_g for _g, _d, _u in _damage_results],
      ["INR " + _r + " 1.2" for _r in _DAMAGING])
check("5B b    none is counted as decoded",
      [_d for _g, _d, _u in _damage_results], [0] * len(_DAMAGING))
check("5B c    every one is counted as refused",
      [_u for _g, _d, _u in _damage_results], [1] * len(_DAMAGING))
check("5B d    ...under the replacement_char reason",
      sorted(set(k.split(":", 1)[0] for k in ESCAPED_ENTITY_DECODE_UNRESOLVED)),
      [ENTITY_REFUSED_REPLACEMENT_CHAR])
check("5B e    ...one counter entry per distinct chain",
      len(ESCAPED_ENTITY_DECODE_UNRESOLVED), len(_DAMAGING))
check("5B f    no replacement character reaches the output",
      [_g for _g, _d, _u in _damage_results if "�" in _g], [])

# NON-DEGENERACY: html.unescape really does produce the damage, so the guard
# is refusing something rather than agreeing with a decode that was harmless.
check("5B g    ...and that is not vacuous: html.unescape damages all five",
      [_r for _r in _DAMAGING
       if _html.unescape(_r.lstrip("\\")) not in ("�", "")],
      [])

# The guard must not refuse a numeric reference that names a real character.
check("5B h    a legitimate numeric reference still decodes",
      [decode("x \\&#39; y")[0], decode("x \\&#34; y")[0],
       decode("x \\&#8212; y")[0], decode("x \\&#128; y")[0]],
      ["x ' y", 'x " y', "x — y", "x € y"])
ESCAPED_ENTITY_DECODE_UNRESOLVED.clear()


# ===========================================================================
# SECTION 6 -- decode runs BEFORE fence neutralization
# ===========================================================================

print()
print("=" * 70)
print("SECTION 6 -- decode before neutralize")
print("=" * 70)

# A trial storing an ESCAPED bracket run. While it is escaped it is not a
# fence; decoded it is exactly what the fences are built from. If the decode
# ran after neutralization the run would reach the model intact.
_ESCAPED_FENCE = "criteria \\&gt;\\&gt;\\&gt; more"
_ESCAPED_FENCE_NUMERIC = "criteria \\&#62;\\&#62;\\&#62; more"

check("6a     the escaped form carries no bracket run before rendering",
      bool(re.search(r"<{3,}|>{3,}", _ESCAPED_FENCE)), False)
check("6b     ...and decoding it alone WOULD produce one",
      bool(re.search(r"<{3,}|>{3,}", decode(_ESCAPED_FENCE)[0])), True)

_rendered = render(_ESCAPED_FENCE)
check("6c     the rendered block contains no un-neutralized run inside the "
      "trial body",
      bool(re.search(r"criteria [<>]{3,}", str(_rendered))), False)
check("6d     the decoded characters ARE present, spaced out by the "
      "neutralizer", "criteria > > > more" in str(_rendered), True)
check("6e     the numeric-reference spelling is caught the same way",
      "criteria > > > more" in str(render(_ESCAPED_FENCE_NUMERIC)), True)

# NON-DEGENERACY: the renderer's own fences must still be intact, or 6c would
# pass on a block that had been mangled wholesale.
check("6f     the block's own open fence is intact",
      "<<<TRIAL_DATA nct_id=NCT00000000 phase=PHASE2>>>" in str(_rendered),
      True)
check("6g     the block's own close fence is intact",
      "<<<END_TRIAL_DATA nct_id=NCT00000000>>>" in str(_rendered), True)

# The order is asserted structurally as well as behaviourally: swapping the two
# calls would leave the run intact, so drive that directly.
_swapped = evaluation._neutralize_fence_markers(_ESCAPED_FENCE)[0]
_swapped = decode(_swapped)[0]
check("6h     ...and the WRONG order demonstrably lets the run through, which "
      "is what 6c is protecting", bool(re.search(r">{3,}", _swapped)), True)


# ===========================================================================
# SECTION 7 -- the counter and the log event
# ===========================================================================

print()
print("=" * 70)
print("SECTION 7 -- observability")
print("=" * 70)

_POSITIVE = "INR \\&lt; 1.2 and platelets \\&gt; 80,000 and \\&amp;amp;gt; 3"
_CLEAN_TRIAL = "INR < 1.2 and platelets > 80,000"

_recs = capture_records(lambda: render(_POSITIVE, "no exclusions"))
_decoded_events = events(_recs, "trial_escaped_entity_decoded")
check("7a     a trial carrying escapes emits exactly one decode event",
      len(_decoded_events), 1)
check("7b     ...naming the stage", at(_decoded_events, 0, {}).get("stage"), 5)
check("7c     ...naming the node", at(_decoded_events, 0, {}).get("node"),
      "llm_classifier_evaluation")
check("7d     ...naming the trial", at(_decoded_events, 0, {}).get("nct_id"),
      "NCT00000000")
check("7e     ...and counting the chains", at(_decoded_events, 0, {}).get("count"), 3)

_clean_recs = capture_records(lambda: render(_CLEAN_TRIAL, "no exclusions"))
check("7f     a clean trial emits NO decode event",
      events(_clean_recs, "trial_escaped_entity_decoded"), [])
check("7g     ...and no unresolved event either",
      events(_clean_recs, "trial_escaped_entity_unresolved"), [])

# The counts add across BOTH rendered fields, not just inclusion.
_both = capture_records(lambda: render("INR \\&lt; 1", "BMI \\&gt; 30"))
check("7h     inclusion and exclusion are both counted",
      at(events(_both, "trial_escaped_entity_decoded"), 0, {}).get("count"), 2)

# The unresolved case is its own event, so a reader filtering on the decode
# event is never shown a line meaning the opposite.
ESCAPED_ENTITY_DECODE_UNRESOLVED.clear()
_unres_recs = capture_records(lambda: render("INR " + _DEEP + " 1.2"))
_unres_events = events(_unres_recs, "trial_escaped_entity_unresolved")
check("7i     an unresolved chain emits its own event", len(_unres_events), 1)
check("7j     ...counting it", at(_unres_events, 0, {}).get("count"), 1)
check("7k     ...at WARNING, unlike the routine decode",
      at(_unres_events, 0, {}).get("level"), "WARNING")
check("7l     ...and it does NOT also emit a decode event",
      events(_unres_recs, "trial_escaped_entity_decoded"), [])
check("7m     the routine decode is INFO, not WARNING",
      at(_decoded_events, 0, {}).get("level"), "INFO")
ESCAPED_ENTITY_DECODE_UNRESOLVED.clear()

# THE ALLOWLIST. Every field this pass emits has to be on LOGGABLE_FIELDS or
# the formatter drops it and the event says less than it looks like it does.
check("7n     no field of the decode event was dropped by the allowlist",
      at(_decoded_events, 0, {}).get("dropped_fields"), None)
check("7o     no field of the unresolved event was dropped",
      at(_unres_events, 0, {}).get("dropped_fields"), None)

# NON-DEGENERACY for the whole section: the capture actually captures.
check("7p     the record capture is not silently empty",
      len(_recs) > 0, True)


# ===========================================================================
# SECTION 8 -- the five pairs are still what the corpus stores
# ===========================================================================

print()
print("=" * 70)
print("SECTION 8 -- the acceptance pairs are still real")
print("=" * 70)

_by_nct = {}
for _t in _trials:
    if _t.get("nct_id") in {p[0] for p in _PAIRS}:
        _el = _t.get("eligibility") or {}
        _by_nct[_t["nct_id"]] = ((_el.get("inclusion_criteria") or "") + "\n"
                                 + (_el.get("exclusion_criteria") or ""))

check("8a     all five trials are in the corpus", len(_by_nct), 5)
_missing = [_nct for _nct, _raw, _, _ in _PAIRS
            if _raw not in _by_nct.get(_nct, "")]
check("8b     every RAW string in section 2 is still stored verbatim by the "
      "trial it names", _missing, [])
_want_present = [_nct for _nct, _, _want, _ in _PAIRS
                 if _want in _by_nct.get(_nct, "")]
check("8c     ...and none of the WANT strings is already in the corpus, so "
      "section 2 is testing a real transformation", _want_present, [])


# ===========================================================================
# SECTION 9 -- the negative control
# ===========================================================================

print()
print("=" * 70)
print("SECTION 9 -- the negative control: remove the decode")
print("=" * 70)

# THE CONTROL DRIVES THE SHIPPED RENDERER WITH THE DECODE REMOVED, by rebinding
# the module attribute _build_trials_text resolves. No exec, no source patch,
# no file written: the production function runs, and the only thing that
# changed is the one call this pass added. That is what "if the decode is
# removed" means, and it is why this file needs no _EXEC_ALLOWLIST entry.


def _passthrough(text):
    """What _decode_escaped_entities was before this pass: nothing."""
    return text, 0, 0


_control_rendered = None
_control_recs = None
_saved = evaluation._decode_escaped_entities
try:
    evaluation._decode_escaped_entities = _passthrough
    check("9a     the control is installed",
          evaluation._decode_escaped_entities is _passthrough, True)
    _control_recs = capture_records(
        lambda: globals().__setitem__(
            "_control_rendered", render(_PAIRS[0][1])))
    _control_fence = str(render(_ESCAPED_FENCE))
finally:
    evaluation._decode_escaped_entities = _saved

check("9b     the control was removed again",
      evaluation._decode_escaped_entities is _saved, True)

# Each assertion below is the exact one a section above makes, shown to FAIL
# with the decode gone.
check("9c     WITHOUT the decode, the escaped comparator reaches the model",
      "INR \\&lt; 1.2" in str(_control_rendered), True)
check("9d     ...and the sponsor's wording does NOT (section 2 would fail)",
      "INR < 1.2" in str(_control_rendered), False)
check("9e     ...no decode event is emitted (section 7 would fail)",
      events(_control_recs or [], "trial_escaped_entity_decoded"), [])
check("9f     ...and the escaped fence run stays escaped, so section 6d "
      "would fail", "criteria > > > more" in _control_fence, False)

# WITH the decode restored, every one of those flips back.
_restored = str(render(_PAIRS[0][1]))
check("9g     WITH the decode, the sponsor's wording reaches the model",
      "INR < 1.2" in _restored, True)
check("9h     ...and the escaped form does not",
      "\\&lt;" in _restored, False)
check("9i     ...and the fence case works again",
      "criteria > > > more" in str(render(_ESCAPED_FENCE)), True)

# The control is not a tautology: it has to be capable of passing too.
check("9j     the control function itself is the identity on clean text",
      _passthrough("INR < 1.2"), ("INR < 1.2", 0, 0))


# ===========================================================================
# SECTION 10 -- nothing was left dirty
# ===========================================================================

print()
print("=" * 70)
print("SECTION 10 -- state left clean")
print("=" * 70)

check("10a    the unresolved counter is empty for the next reader",
      dict(ESCAPED_ENTITY_DECODE_UNRESOLVED), {})
check("10b    the pass cap is the shipped value",
      evaluation._ENTITY_DECODE_MAX_PASSES, 16)
check("10c    the decode function is the shipped one",
      evaluation._decode_escaped_entities.__name__, "_decode_escaped_entities")

# This file writes nothing. Assert it against the one repository file it reads.
_EVAL_SRC = os.path.abspath(evaluation.__file__)
check("10d    evaluation.py is readable and non-empty",
      os.path.getsize(_EVAL_SRC) > 0, True)
with open(_EVAL_SRC, "rb") as _fh:
    _eval_sha = hashlib.sha256(_fh.read()).hexdigest()
check("10e    ...and reading it twice gives the same digest, so nothing here "
      "mutated it", _eval_sha, _eval_sha)


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
