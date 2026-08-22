##############################################################################
# Stage 5 packer: the per-trial measurement is a SLICE of one render
##############################################################################

"""
Render Slice Equality Test

THE PACKER PRICES EACH TRIAL WITHOUT RENDERING IT. ``pack_trials_by_input_
tokens`` is handed ``_render_trial_blocks(trials)`` -- the blocks the node's one
whole-batch render already produced -- and takes each trial's cost off its own
block. Before the render-slice pass it called ``_trial_input_tokens(trial)``,
which rendered the trial AGAIN, so every trial was rendered three times per
patient: the whole-batch stored-prompt render, the packer's measurement, and
the chunk actually sent.

THE COST WAS NOT THE CPU. The two refusal counters live inside the decoders and
are deliberately NOT suppressed by ``log_events=False`` -- that flag governs the
log channel and nothing else, argued at ``_render_trial_blocks`` -- so the extra
render counted every refusal a second time. On a no-split patient both counters
read exactly 1.5x. ``tests/test_agent_render_event_suppression.py`` section 4 is
where that arithmetic is measured; THIS file is about the thing that made
removing the render legitimate.

WHAT MUST BE TRUE FOR THE SLICE TO BE HONEST, and it is one property:

    the block a WHOLE-BATCH render produces for a trial is byte-identical to
    the block a ONE-TRIAL render produces for it.

It holds because ``_render_trial_blocks`` builds ``parts[i]`` from
``trials[i]`` and from nothing else -- the three ``md_*`` accumulators in that
loop feed the aggregate LOG line and never a block. That is an argument. This
file is the measurement, and it is taken over trials carrying every class the
render actually transforms rather than over clean text, because the three
rewrites -- the markdown escape decode, the escaped-entity decode and the fence
neutralization -- are exactly what a naive "sum of the parts" would get wrong.

  SEVEN CLASSES, each with a trial of its own and each asserted to be
  non-degenerate (section 1 requires every one of them to change the text it is
  applied to, so a class that silently stopped being transformed cannot pass by
  rendering to itself):

    markdown escapes            "INR \\> 1.2"
    markdown refusal            "CLL\\\\SLL"        (escaped backslash, kept)
    entity chains               "ALT &gt; 3"
    entity refusal              a reference that decodes to no usable character
    fence markers               ">>>" inside criteria text
    fence markers in a fence attribute (nct_id / phase)
    all of them in one trial

  AND SIX SHAPES, because "additive" is a claim about every partition and not
  only about singletons: the whole batch, every prefix, every single trial,
  every adjacent pair, one reversed batch, and the empty batch.

WHY THE COMPARISON IS AGAINST A ONE-TRIAL RENDER RATHER THAN AGAINST A STORED
EXPECTATION. A golden file of expected blocks would have to be regenerated
whenever the prompt legitimately changed, which makes whatever the code does
correct by definition. The one-trial render is the thing the packer USED to do,
so this compares the new measurement against the old one directly -- and it
keeps working across every future prompt version without a regeneration step.

NO NETWORK, NO KEYS, NO SPEND, NO MODEL CALL, NO LIVE QDRANT, NO CORPUS, NO
DATABASE, NO GIT HISTORY. It EXECS NOTHING -- every control is a different
INPUT to a pure function, or an attribute rebind inside ``try``/``finally``
with the restore asserted -- so it needs no ``_EXEC_ALLOWLIST`` entry. NOT in
tests/run_serial_tests.py's collision matrix: it writes nothing anywhere, and
the one repository file it reads (oncotriage/agent/evaluation.py) is written by
neither of the suite's two writers.

    python tests/test_agent_stage5_render_slice_equality.py
"""

import ast
import hashlib
import itertools
import os
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

from oncotriage.agent import evaluation as _evaluation          # noqa: E402
from oncotriage.agent.evaluation import (                       # noqa: E402
    ESCAPED_ENTITY_DECODE_UNRESOLVED,
    MARKDOWN_ESCAPE_DECODE_UNRESOLVED,
    PackingBlockMismatchError,
    _build_trials_text,
    _render_trial_blocks,
    _trial_input_tokens,
    estimate_prompt_tokens,
    pack_trials_by_input_tokens,
)


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, actual, expected):
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append((label, actual, expected))
        print(f"  FAIL  {label}")
        print(f"        actual  : {repr(actual)[:300]}")
        print(f"        expected: {repr(expected)[:300]}")


def drive(fn, *args, **kwargs):
    """Call ``fn`` and turn a raise into a VALUE ``check`` can fail on.

    A bare call inside a ``check(...)`` argument list lets an exception escape
    while the argument is being evaluated, which kills the run and reports one
    traceback where it owes a summary. This project has shipped that shape nine
    times; every call into production code in this file goes through here.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                               # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


def at(seq, index, default=None):
    """``seq[index]`` when that is meaningful, ``default`` otherwise.

    A drive() result is a "<RAISED ...>" STRING when the call raised, and both
    of the obvious readings of ``result[0]`` on one are wrong: it does not
    raise (so nothing is recorded) and it yields a CHARACTER (so a comparison
    reports a confident False about the wrong thing).
    """
    if isinstance(seq, (list, tuple)) and -len(seq) <= index < len(seq):
        return seq[index]
    return default


def section(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def clear_counters():
    MARKDOWN_ESCAPE_DECODE_UNRESOLVED.clear()
    ESCAPED_ENTITY_DECODE_UNRESOLVED.clear()


def counter_totals():
    return (sum(MARKDOWN_ESCAPE_DECODE_UNRESOLVED.values()),
            sum(ESCAPED_ENTITY_DECODE_UNRESOLVED.values()))


_EVAL_PATH = os.path.abspath(_evaluation.__file__)
_EVAL_SRC_BEFORE = open(_EVAL_PATH, encoding="utf-8").read()
_EVAL_SHA_BEFORE = hashlib.sha256(_EVAL_SRC_BEFORE.encode()).hexdigest()


# ===========================================================================
# THE SEVEN CONTENT CLASSES
# ===========================================================================
#
# Every string below is the shape ClinicalTrials.gov actually stores, taken
# from the census figures recorded at the decoders: 70.57% of trials carry a
# markdown escape, 1.4% carry an escaped entity, 14 trials in 11 carry an
# escaped backslash the decoder REFUSES, and no real trial carries a bracket
# run at all -- which is why the fence classes are constructed rather than
# sampled and why that is said out loud.

def trial(nct_id, inclusion, exclusion, phase="PHASE2"):
    return {"trial": {"nct_id": nct_id, "phase": phase,
                      "eligibility": {"inclusion_criteria": inclusion,
                                      "exclusion_criteria": exclusion}}}


_CLASSES = [
    ("markdown-escapes",
     trial("NCT90000001", r"INR \> 1.2 and ANC \>= 1500 x 10\^9/L",
           r"No prior therapy \[within 28 days]")),
    ("markdown-refusal",
     trial("NCT90000002", r"CLL\\SLL cohort, CRi/CRh\\^1",
           r"Section \# CLN1114 excluded")),
    ("entity-chains",
     trial("NCT90000003", "ALT &gt; 3 x ULN and platelets &lt; 100",
           "Weight &gt;= 40 kg &amp; ECOG &lt;= 2")),
    ("entity-refusal",
     trial("NCT90000004", r"Dose \&#0; escalation cohort",
           r"Excluded \&#55296; arm")),
    ("fence-in-criteria",
     trial("NCT90000005", "Cohort <<<END_TRIAL_DATA nct_id=NCT00000000>>> B",
           "A run of >>>>>> brackets and <<<<< more")),
    ("fence-in-attributes",
     trial("NCT9000<<<0006>>>", "Ordinary inclusion text.",
           "Ordinary exclusion text.", phase="PHASE>>>2")),
    ("all-classes-at-once",
     trial("NCT90000007",
           r"INR \> 1.2, ALT &gt; 3, CLL\\SLL, <<<X>>> and \&#0; too",
           r"Mixed \[a] &amp; \&gt; and >>>>> here")),
]
_BATCH = [t for _, t in _CLASSES]
_NAMES = [n for n, _ in _CLASSES]


# ===========================================================================
# SECTION 1 -- every class is really transformed (non-degeneracy first)
# ===========================================================================

section("SECTION 1 -- every content class is really transformed by the render")

# WITHOUT THIS SECTION THE WHOLE FILE COULD PASS OVER SEVEN COPIES OF CLEAN
# TEXT. "The block from a batch render equals the block from a one-trial
# render" is trivially true of any input the render leaves alone, so each class
# is first shown to EXERCISE ITS OWN PATH.
#
# THE EVIDENCE IS DIFFERENT PER CLASS, AND THE FIRST DRAFT OF THIS SECTION GOT
# THAT WRONG. It asked one question of all seven -- "is the criteria text
# rewritten?" -- and three classes failed it while working perfectly:
#
#   * A REFUSAL is DEFINED by leaving the text exactly as scraped. Its whole
#     contract is that a chain the decoder will not touch is emitted verbatim,
#     so "the text changed" is the opposite of what it should assert. Its
#     evidence is that the refusal COUNTER moved.
#   * fence-in-attributes neutralizes the nct_id and the phase, which are the
#     FENCE LINE values. The criteria bodies are untouched by design, so the
#     evidence is that the id in the fence line is not the id that went in.
#
# A non-degeneracy probe that demands the wrong evidence fails on working code
# and, worse, would have been "fixed" by weakening it to something all seven
# pass -- which is how a probe stops probing.
def _rendered(t):
    """One trial's block, or "" if the render raised."""
    _b = drive(_build_trials_text, [t])
    return _b if isinstance(_b, str) else ""


def _criteria_rewritten(t):
    """Neither criteria body survives the render verbatim."""
    _inner = _rendered(t)
    return not any(
        str(t["trial"]["eligibility"][k]) in _inner
        for k in ("inclusion_criteria", "exclusion_criteria"))


def _markdown_refused(t):
    clear_counters()
    _rendered(t)
    _moved = sum(MARKDOWN_ESCAPE_DECODE_UNRESOLVED.values()) > 0
    clear_counters()
    return _moved


def _entity_refused(t):
    clear_counters()
    _rendered(t)
    _moved = sum(ESCAPED_ENTITY_DECODE_UNRESOLVED.values()) > 0
    clear_counters()
    return _moved


def _attributes_neutralized(t):
    """The fence line does not carry the id and phase that went in."""
    _inner = _rendered(t)
    return (str(t["trial"]["nct_id"]) not in _inner
            and str(t["trial"]["phase"]) not in _inner)


_EVIDENCE = {
    "markdown-escapes": ("its criteria text is rewritten", _criteria_rewritten),
    "markdown-refusal": ("the markdown REFUSAL counter moves, which is what a "
                         "refusal means -- the text is left as scraped",
                         _markdown_refused),
    "entity-chains": ("its criteria text is rewritten", _criteria_rewritten),
    "entity-refusal": ("the entity REFUSAL counter moves", _entity_refused),
    "fence-in-criteria": ("its criteria text is rewritten", _criteria_rewritten),
    "fence-in-attributes": ("the FENCE LINE values are neutralized; the "
                            "criteria bodies are untouched by design",
                            _attributes_neutralized),
    "all-classes-at-once": ("its criteria text is rewritten",
                            _criteria_rewritten),
}

for _name, _t in _CLASSES:
    _why, _probe = _EVIDENCE[_name]
    check(f"1a  [{_name}] this class really exercises its path -- {_why}",
          drive(_probe, _t), True)

check("1b  non-degeneracy: seven distinct classes were declared and none was "
      "silently duplicated", (len(_CLASSES), len(set(_NAMES))), (7, 7))
check("1c  ...and every declared class has evidence of its own, so none is "
      "carried by another's probe",
      sorted(_EVIDENCE), sorted(_NAMES))
check("1d  the two REFUSAL classes really are the ones whose text survives "
      "verbatim -- stated as a measurement, because it is the fact that made "
      "the first version of 1a wrong",
      [_criteria_rewritten(_t) for _n, _t in _CLASSES
       if _n in ("markdown-refusal", "entity-refusal")], [False, False])
clear_counters()


# ===========================================================================
# SECTION 2 -- THE SLICE EQUALITY, over six shapes
# ===========================================================================

section("SECTION 2 -- a batch render's blocks ARE the one-trial renders")

_blocks = drive(_render_trial_blocks, _BATCH)
check("2a  the whole-batch render produced one block per trial",
      len(_blocks) if isinstance(_blocks, list) else _blocks, len(_BATCH))

_solo = [drive(_build_trials_text, [t]) for t in _BATCH]
check("2b  EVERY block of the whole-batch render is byte-identical to that "
      "trial's own one-trial render -- the property the packer's arithmetic "
      "rests on", _blocks, _solo)
check("2c  non-degeneracy: those blocks are all different from each other, so "
      "2b is not comparing seven copies of one string",
      len(set(_solo)), len(_solo))
check("2d  ...and none of them is empty",
      all(isinstance(b, str) and len(b) > 40 for b in _solo), True)

# --- every PREFIX ----------------------------------------------------------
_prefix_ok = []
for _k in range(len(_BATCH) + 1):
    _sub = drive(_render_trial_blocks, _BATCH[:_k])
    _prefix_ok.append(_sub == _blocks[:_k] if isinstance(_sub, list) else _sub)
check("2e  every PREFIX of the batch renders to the matching prefix of the "
      "whole batch's blocks, empty prefix included",
      _prefix_ok, [True] * (len(_BATCH) + 1))

# --- every ADJACENT PAIR ---------------------------------------------------
_pair_ok = []
for _i in range(len(_BATCH) - 1):
    _sub = drive(_render_trial_blocks, _BATCH[_i:_i + 2])
    _pair_ok.append(_sub == _blocks[_i:_i + 2] if isinstance(_sub, list)
                    else _sub)
check("2f  every ADJACENT PAIR renders to the matching two blocks -- a chunk "
      "is a contiguous run, so this is the shape the packer actually sends",
      _pair_ok, [True] * (len(_BATCH) - 1))

# --- REVERSED, which is the strongest of the six ---------------------------
# A block that depended on its POSITION rather than on its trial would survive
# every prefix and pair test above and fail here.
_rev = drive(_render_trial_blocks, list(reversed(_BATCH)))
check("2g  a REVERSED batch renders to the reversed blocks, so a block depends "
      "on its trial and not on its position",
      _rev, list(reversed(_blocks)))

# --- the JOIN is exactly the concatenation ---------------------------------
check("2h  _build_trials_text of the batch IS the blocks joined with nothing "
      "between them", drive(_build_trials_text, _BATCH), "".join(_blocks))
check("2i  ...so the batch's length is the sum of the block lengths exactly, "
      "which is what makes a per-trial measurement addable",
      len(drive(_build_trials_text, _BATCH)), sum(len(b) for b in _blocks))
check("2j  the empty batch renders to no blocks and to the empty string",
      (drive(_render_trial_blocks, []), drive(_build_trials_text, [])),
      ([], ""))


# ===========================================================================
# SECTION 3 -- the MEASUREMENT, not just the bytes
# ===========================================================================

section("SECTION 3 -- the sliced token figure equals the old per-render one")

check("3a  the packer's per-trial token figure, sliced out of ONE render, is "
      "identical to pricing each trial's own render -- which is exactly what "
      "_trial_input_tokens used to compute by rendering",
      [drive(_trial_input_tokens, b) for b in _blocks],
      [estimate_prompt_tokens(s) for s in _solo])
# isinstance FIRST, then the comparison. drive() returns a "<RAISED ...>"
# STRING on an exception, and `str > int` raises inside check()'s argument
# list -- an abort where a recorded failure is owed.
_tok = [drive(_trial_input_tokens, b) for b in _blocks]
check("3b  non-degeneracy: those figures are non-zero and differ between "
      "trials, so 3a is not comparing seven copies of one number",
      (all(isinstance(n, int) and n > 0 for n in _tok),
       len(set(_tok)) > 1),
      (True, True))
check("3c  _trial_input_tokens is the ONE estimator and not a second formula: "
      "for every block it agrees with estimate_prompt_tokens exactly",
      [drive(_trial_input_tokens, b) for b in _blocks],
      [estimate_prompt_tokens(b) for b in _blocks])


# ===========================================================================
# SECTION 4 -- the packer renders NOTHING
# ===========================================================================

section("SECTION 4 -- handed blocks, the packer renders nothing")

clear_counters()
drive(_render_trial_blocks, _BATCH)
_after_render = counter_totals()
check("4a  non-degeneracy: one render of this batch moves BOTH refusal "
      "counters, so section 4 is arithmetic between real numbers",
      tuple(n > 0 for n in _after_render), (True, True))

clear_counters()
_b = drive(_render_trial_blocks, _BATCH)
drive(pack_trials_by_input_tokens, _BATCH, 500, 12000, 5, blocks=_b)
check("4b  a render followed by a pack over its blocks leaves EXACTLY what "
      "the render alone left -- the packer adds no phantom refusal",
      counter_totals(), _after_render)

clear_counters()
drive(pack_trials_by_input_tokens, _BATCH, 500, 12000, 5, blocks=_b)
check("4c  ...and packing over already-rendered blocks with no render at all "
      "moves neither counter", counter_totals(), (0, 0))

# THE PRODUCTION SEQUENCE, both arms, so the multiplier is measured rather than
# reasoned about. BEFORE is reconstructed from the shipped functions -- one
# render per trial is what _trial_input_tokens used to do -- rather than from a
# git blob, because the point is the SHAPE of the sequence and not the identity
# of the old function.
clear_counters()
drive(_render_trial_blocks, _BATCH)                     # stored prompt
for _t in _BATCH:
    drive(_build_trials_text, [_t], log_events=False)   # the OLD measurement
drive(_build_trials_text, _BATCH)                       # the chunk sent
_before_seq = counter_totals()

clear_counters()
_b2 = drive(_render_trial_blocks, _BATCH)               # stored prompt + packer
drive(pack_trials_by_input_tokens, _BATCH, 500, 12000, 5, blocks=_b2)
drive(_build_trials_text, _BATCH)                       # the chunk sent
_after_seq = counter_totals()

check("4d  a no-split patient used to leave THREE renders' worth of refusal "
      "counts", _before_seq, tuple(3 * n for n in _after_render))
check("4e  ...and now leaves TWO, both of them sends",
      _after_seq, tuple(2 * n for n in _after_render))
check("4f  ...which is the 1.5x phantom removed, stated as the ratio",
      (sum(_after_seq) * 3, sum(_before_seq) * 2),
      (sum(_before_seq) * 2, sum(_before_seq) * 2))
clear_counters()


# ===========================================================================
# SECTION 5 -- the blocks-vs-trials correspondence is REFUSED, not assumed
# ===========================================================================

section("SECTION 5 -- a blocks list that is not the trials list is refused")

# WHY THIS IS A RAISE AND NOT A WARNING. The packer indexes the two in
# parallel, so a disagreement prices each trial with another trial's bytes and
# still produces a perfectly well-formed partition -- no error, no counter, no
# symptom anywhere downstream except a worse chunking. That is the silent class
# this project exists to remove.
_short = drive(pack_trials_by_input_tokens, _BATCH, 500, 12000, 5,
               blocks=_blocks[:-1])
check("5a  too FEW blocks is refused by name",
      isinstance(_short, str) and "PackingBlockMismatchError" in _short, True)
_long = drive(pack_trials_by_input_tokens, _BATCH, 500, 12000, 5,
              blocks=_blocks + _blocks[:1])
check("5b  too MANY blocks is refused by name",
      isinstance(_long, str) and "PackingBlockMismatchError" in _long, True)
_empty_trials = drive(pack_trials_by_input_tokens, [], 500, 12000, 5,
                      blocks=_blocks)
check("5c  ...and an EMPTY trial list with blocks is refused too, rather than "
      "returning the honest-looking 'nothing to pack' the empty-batch branch "
      "would otherwise give it",
      isinstance(_empty_trials, str)
      and "PackingBlockMismatchError" in _empty_trials, True)
# INDEXED THROUGH at(), because drive() returns a STRING on a raise and a
# string's [0] is a character rather than an IndexError -- so a bare [0] here
# would silently compare a character with [] and report a confident False for
# the wrong reason, which is worse than the abort it avoids.
check("5d  the matching empty pair is NOT refused -- a zero-trial batch is a "
      "real state and must still return no chunks",
      at(drive(pack_trials_by_input_tokens, [], 500, 12000, 5, blocks=[]),
         0, "<not a (chunks, report) pair>"), [])
check("5e  the refusal is a RuntimeError subclass and deliberately not a "
      "ValueError, so a stray `except ValueError` around a Stage 5 call "
      "cannot eat it",
      (issubclass(PackingBlockMismatchError, RuntimeError),
       issubclass(PackingBlockMismatchError, ValueError)), (True, False))
check("5f  the message names both counts, so an operator is not left to "
      "diff two lists by hand",
      all(part in (_short if isinstance(_short, str) else "")
          for part in (str(len(_BATCH)), str(len(_blocks) - 1),
                       "_render_trial_blocks")), True)

# --- BLOCKS IS REQUIRED, WITH NO DEFAULT -----------------------------------
# The default that would have been convenient -- render them here when the
# caller does not supply them -- is the defect this argument exists to remove:
# it lets a caller silently reinstate a second render of every trial.
_no_blocks = drive(pack_trials_by_input_tokens, _BATCH, 500, 12000, 5)
check("5g  omitting blocks is a TypeError, not a silent re-render",
      isinstance(_no_blocks, str) and "TypeError" in _no_blocks, True)
_sig = __import__("inspect").signature(pack_trials_by_input_tokens)
check("5h  ...and it is KEYWORD-ONLY, so a fifth positional argument can "
      "never be read as one of the three integers",
      (_sig.parameters["blocks"].kind.name,
       _sig.parameters["blocks"].default is _sig.empty),
      ("KEYWORD_ONLY", True))


# ===========================================================================
# SECTION 6 -- the packer's partition is unchanged by any of this
# ===========================================================================

section("SECTION 6 -- packing decisions are the same as pricing each render")

# THE POINT OF SECTIONS 2 AND 3 IS THAT THE PACKER CANNOT TELL. Driven over a
# spread of budgets including one that forces the cap relaxation, comparing the
# shipped packer against one fed costs computed the OLD way -- by rendering
# each trial on its own.
_old_style_blocks = [drive(_build_trials_text, [t]) for t in _BATCH]
_budget_rows = []
for _budget in (400, 700, 1100, 2000, 12000):
    _new = drive(pack_trials_by_input_tokens, _BATCH, 300, _budget, 5,
                 blocks=_blocks)
    _old = drive(pack_trials_by_input_tokens, _BATCH, 300, _budget, 5,
                 blocks=_old_style_blocks)
    # NOT just `_new == _old`: two identical "<RAISED ...>" strings compare
    # equal, so a defect that makes BOTH arms raise would read as agreement.
    _budget_rows.append(_new == _old and isinstance(_new, tuple))
check("6a  every budget packs identically whether the costs come from the "
      "batch render's blocks or from per-trial renders",
      _budget_rows, [True] * 5)

# max_chunks=2 on the smallest budget is what reaches the cap relaxation; the
# spread above never did, and a flag path nothing exercises is a flag path
# nothing checks.
_shapes = set()
for _budget, _cap in ((400, 2), (400, 5), (700, 5), (1100, 5), (2000, 5),
                      (12000, 5)):
    _res = drive(pack_trials_by_input_tokens, _BATCH, 300, _budget, _cap,
                 blocks=_blocks)
    _chunks = at(_res, 0, [])
    _report = at(_res, 1, {})
    _shapes.add((len(_chunks), _report.get("cap_relaxed_budget"),
                 _report.get("over_budget_chunk")))
check("6b  non-degeneracy: those budgets produce genuinely different "
       "partitions, so 6a is not five copies of one comparison",
      len(_shapes) > 1, True)
check("6c  ...and at least one of them exercised the cap relaxation or an "
      "over-budget chunk, so the flag paths are covered too",
      any(row[1] or row[2] for row in _shapes), True)

# --- EVERY TRIAL IS PLACED, WHICH IS THE PACKER'S OWN INVARIANT ------------
_all_placed = []
for _budget in (400, 700, 1100, 2000, 12000):
    _chunks = at(drive(pack_trials_by_input_tokens, _BATCH, 300, _budget, 5,
                       blocks=_blocks), 0, [])
    _all_placed.append([t["trial"]["nct_id"] for c in _chunks for t in c])
check("6d  every trial is placed exactly once, in order, at every budget -- "
      "the never-drop invariant, re-checked through the new argument",
      _all_placed, [[t["trial"]["nct_id"] for t in _BATCH]] * 5)

# --- THE PAIRING IS CONSUMED, NOT MAINTAINED -------------------------------
# `trials` is not indexed anywhere below the zip, so a later edit that reorders
# it cannot desync the costs. Asserted by AST because it is a property of the
# source rather than of a run: a permutation is invisible to the length check.
_pack_node = None
for _n in ast.walk(ast.parse(_EVAL_SRC_BEFORE, _EVAL_PATH)):
    if (isinstance(_n, ast.FunctionDef)
            and _n.name == "pack_trials_by_input_tokens"):
        _pack_node = _n
check("6e  the packer was located by AST (non-degeneracy for 6f)",
      _pack_node is not None, True)
_trial_subscripts = [
    ast.unparse(_n) for _n in (ast.walk(_pack_node) if _pack_node else [])
    if isinstance(_n, ast.Subscript) and isinstance(_n.value, ast.Name)
    and _n.value.id == "trials"]
check("6f  `trials` is never SUBSCRIPTED in the packer: the trial and its cost "
      "are zipped once and travel together, so a reorder below that line "
      "cannot price a trial with another trial's bytes",
      _trial_subscripts, [])
_priced_subscripts = [
    ast.unparse(_n) for _n in (ast.walk(_pack_node) if _pack_node else [])
    if isinstance(_n, ast.Subscript) and isinstance(_n.value, ast.Name)
    and _n.value.id == "priced"]
check("6g  non-degeneracy: the pairs ARE subscripted, so 6f is reporting a "
      "real change of shape rather than an empty walk",
      len(_priced_subscripts) > 0, True)


# ===========================================================================
# SECTION 7 -- hygiene
# ===========================================================================

section("SECTION 7 -- hygiene")

# CLEARED EXPLICITLY, then checked. The check is not a measurement of the
# renders above -- it is the promise that this file hands the process on clean,
# and the clear is how the promise is kept. Its non-degeneracy is 4a, which
# showed these counters moving for real earlier in the run.
clear_counters()
check("7a  the module-level counters are left empty for the next reader",
      counter_totals(), (0, 0))
_after_src = ""
try:
    with open(_EVAL_PATH, encoding="utf-8") as _fh:
        _after_src = _fh.read()
except Exception as _exc:                                      # noqa: BLE001
    _after_src = f"<RAISED {type(_exc).__name__}: {_exc}>"
check("7b  this file writes nothing: evaluation.py hashes the same twice",
      hashlib.sha256(_after_src.encode()).hexdigest(), _EVAL_SHA_BEFORE)
check("7c  ...and that is a real digest (non-degeneracy)",
      len(_EVAL_SHA_BEFORE) == 64 and not _EVAL_SRC_BEFORE.startswith("<RAISED"),
      True)


print()
print("=" * 74)
print("SUMMARY")
print("=" * 74)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")
if _FAILURES:
    print("\nFailures:")
    for _label, _actual, _expected in _FAILURES:
        print(f"  - {_label}")
        print(f"        actual  : {repr(_actual)[:300]}")
        print(f"        expected: {repr(_expected)[:300]}")

sys.exit(1 if _RESULTS["failed"] else 0)


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 2026-08-22
"""
