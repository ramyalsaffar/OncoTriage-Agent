##############################################################################
# Stage 5 render: a render nobody sends emits no render events
##############################################################################

"""
Render Event Suppression Test

``_build_trials_text`` emits five distinct events from six call sites, and every
one of them reports a MODIFICATION OF THIRD-PARTY TEXT ON ITS WAY TO THE JUDGE:
the markdown-escape aggregate, its per-trial DEBUG detail, the markdown refusal,
the entity decode, the entity refusal and the fence neutralization.

THREE CALLERS NOW, AND ALL THREE ARE SENDS. It was four, and the one that was
not a send is gone:

  1. the per-chunk render of what is actually sent to the model
     (``oncotriage/agent/evaluation.py``, ``_user_prompt_for(chunk)``)
  2. the whole-batch render, which IS the stored prompt kept for the database
     (``_render_trial_blocks(trials)`` in the node)
  3. ``oncotriage/evaluation/run_harness.py:build_contexts`` -- renders one
     trial at a time for the offline rater, and that text IS consumed. SEND-LIKE
     and deliberately OUT OF SCOPE; section 7 asserts it still logs.

  RETIRED. ``_trial_input_tokens`` used to render ONE trial at a time purely to
  price its contribution to the input budget and then throw the string away.
  It dominated the residual volume -- measured on a seeded 15-trial sample from
  the shipped corpus, 11 of the 13 remaining lines per patient came from it --
  and silencing it was this file's original subject. The render-slice pass
  removed the render instead: the packer is handed the blocks the whole-batch
  render already produced, and ``_trial_input_tokens`` now prices a block
  rather than making one.

WHY THE RENDER WAS REMOVED RATHER THAN LEFT SILENT, which is what section 4 is
now about. Silencing the log channel never touched the two REFUSAL COUNTERS
inside the decoders, and those are totals rather than sets: a phantom render
makes them a count of renders, not of refusals. Every trial was rendered three
times per patient -- stored prompt, packer measurement, chunk sent -- so both
counters read exactly 1.5x on a batch that did not split. Two renders remain
and both are sends.

THE ONE CALLER THAT STILL ASKS FOR SILENCE is the node's wrapper pricing,
``_user_prompt_for([], log_events=False)``, which renders the user message with
no trials in it to charge its fixed cost to every chunk. It renders zero trials,
so it would emit nothing whatever the flag said; it passes the flag because it
IS a measurement render, and because a flag with no caller is a mechanism with
no subject.

THE SUPPRESSION IS UNIFORM BY CONSTRUCTION, NOT BY SIX GUARDS. The render binds
``emit = log if log_events else _SILENT_LOG`` and logs through ``emit``; there
is no ``log`` reference left inside the function for a new call site to reach.
Section 5 asserts that by AST, which is also how the ONE event this file cannot
trigger is covered: ``trial_escaped_entity_unresolved`` needs a chain that is
still moving after ``_ENTITY_DECODE_MAX_PASSES``, and ``html.unescape``
collapses a nested ``&amp;`` run in a single pass, so no input reaches it -- it
is 0 across all 14,324 trials. Proving that site structurally is stronger than
omitting it, and the test says which of the two it did.

THE FLAG IS STILL LOGGING-ONLY AND THE COUNTERS ARE STILL NOT SUPPRESSED.
``MARKDOWN_ESCAPE_DECODE_UNRESOLVED`` and ``ESCAPED_ENTITY_DECODE_UNRESOLVED``
live inside the decoders, which the render calls identically either way, so a
silent render lands the same counts key for key as a loud one -- section 4's 4a
and 4d, unchanged and still passing. What section 4 no longer asserts is that a
MEASUREMENT render adds a third set of them; 4c used to pin that as deliberate
and now measures its absence, driving the sequence a no-split patient produces
and requiring exactly two renders' worth. The argument for the change, and for
why suppression would have been the wrong fix, is written at 4's header.

THE RENDER IS ``_render_trial_blocks`` AND THIS FILE'S AST CHECKS FOLLOW IT
THERE. ``_build_trials_text`` is the join over the blocks and holds no event
site at all, so section 5 pointed at it would pass over an empty body.

NO NETWORK, NO KEYS, NO SPEND, NO MODEL CALL, NO GIT HISTORY, NO CORPUS. It
EXECS NOTHING: every control rebinds a module attribute inside ``try``/
``finally`` and drives the SHIPPED function. NOT in tests/run_serial_tests.py's
collision matrix -- it writes nothing anywhere, and the two repository files it
reads are written by neither of the suite's two writers.

    python tests/test_agent_render_event_suppression.py
"""

import ast
import contextlib
import hashlib
import io
import json
import logging
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

from oncotriage.agent import evaluation                        # noqa: E402
from oncotriage.agent.evaluation import (                      # noqa: E402
    ESCAPED_ENTITY_DECODE_UNRESOLVED,
    MARKDOWN_ESCAPE_DECODE_UNRESOLVED,
    _SilentLog,
    _build_trials_text,
    _decode_markdown_escapes,
    _render_trial_blocks,
    _trial_input_tokens,
    estimate_prompt_tokens,
    pack_trials_by_input_tokens,
)
from oncotriage.observability import StructuredLogger          # noqa: E402


# Captured BEFORE anything runs, so section 9 can prove the level was put
# back. Reading it twice at the end would compare a value with itself --
# which is what the first draft of 9d did, and it could never have failed.
_LOGGER_LEVEL_AT_IMPORT = logging.getLogger("oncotriage").level

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
    before ``check`` is entered, so the file dies with a traceback where it owed
    a summary and every result below it. This project has shipped that defect
    six times; every call into ``evaluation`` here goes through this.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                               # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


def at(seq, index, default=None):
    """``seq[index]`` as a VALUE, so a short sequence fails one check rather
    than aborting the file. A defect that suppresses an event is exactly what
    makes these sequences short."""
    try:
        return seq[index]
    except (IndexError, KeyError, TypeError):
        return default


def trial(inclusion, exclusion="", nct_id="NCT00000000"):
    """A trial object in the shape ``_build_trials_text`` reads."""
    return {"trial": {"nct_id": nct_id, "phase": "PHASE2",
                      "eligibility": {"inclusion_criteria": inclusion,
                                      "exclusion_criteria": exclusion}}}


# THE PLANTED BATCH, built so that FIVE of the six call sites fire on a send.
# Each line is annotated with what it is for; a batch that exercised only the
# markdown aggregate would prove nothing about "uniformly".
#
# THE SECOND TRIAL DECODES NO MARKDOWN, and that is a fact about the code
# rather than an oversight: ``\&gt;`` is an ENTITY CHAIN, and the markdown
# decoder skips a chain span whole so the two decoders' subjects stay disjoint.
# The first draft of this file assumed it counted as a markdown decode and
# asserted ``trials_affected == 2`` against a render that reports 1. Measured,
# not reasoned -- which is why the third trial now carries a real escape, so
# the aggregate's three cardinalities are 4 / 2 / 2 rather than 4 / 1 / 1 and a
# check cannot pass by reading the wrong field.
_BATCH = [
    trial(r"INR \< 1.2", r"CLL\\ SLL", "NCT10000001"),   # md decode + md refusal
    trial(r"\&gt; 3 mg", "", "NCT10000002"),             # entity decode ONLY
    trial(r"a run >>> here, ALT \> 2", "", "NCT10000003"),  # fence + md decode
    trial("nothing to do at all", "", "NCT10000004"),    # a quiet trial
]

_RENDER_EVENT_PREFIXES = ("trial_markdown_", "trial_escaped_", "trial_fence_")


def records(fn, level=None):
    """Every JSON log record ``fn`` emits on stderr.

    ``level`` temporarily lowers the ``oncotriage`` logger's threshold. The
    measurement-path assertions run at DEBUG deliberately: "emits nothing" has
    to mean nothing AT ANY LEVEL, and a check run only at the shipped INFO
    default would be satisfied by a per-trial DEBUG line still being emitted.
    Restored in a ``finally`` so one capture cannot change the threshold every
    later check runs under.
    """
    err = io.StringIO()
    package_logger = logging.getLogger("oncotriage")
    saved = package_logger.level
    try:
        if level is not None:
            package_logger.setLevel(level)
        with contextlib.redirect_stderr(err):
            fn()
    finally:
        package_logger.setLevel(saved)
    out = []
    for line in err.getvalue().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def render_events(recs):
    """Only the events ``_build_trials_text`` itself emits."""
    return [r for r in recs
            if str(r.get("event", "")).startswith(_RENDER_EVENT_PREFIXES)]


def counter_totals():
    """Both module refusal counters as one comparable pair."""
    return (sum(MARKDOWN_ESCAPE_DECODE_UNRESOLVED.values()),
            sum(ESCAPED_ENTITY_DECODE_UNRESOLVED.values()))


def clear_counters():
    MARKDOWN_ESCAPE_DECODE_UNRESOLVED.clear()
    ESCAPED_ENTITY_DECODE_UNRESOLVED.clear()


_EVAL_PATH = os.path.abspath(evaluation.__file__)
_EVAL_SRC = ""
try:
    with open(_EVAL_PATH, encoding="utf-8") as _fh:
        _EVAL_SRC = _fh.read()
except Exception as _exc:                                      # noqa: BLE001
    _EVAL_SRC = f"<RAISED {type(_exc).__name__}: {_exc}>"

_EVAL_SHA_BEFORE = hashlib.sha256(_EVAL_SRC.encode()).hexdigest()


def function_node(name):
    """The named FunctionDef out of the shipped source, or None."""
    try:
        tree = ast.parse(_EVAL_SRC, _EVAL_PATH)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def render_function_node():
    """The RENDER's FunctionDef -- ``_render_trial_blocks``.

    THE RENDER MOVED OUT FROM UNDER ``_build_trials_text`` and this file's
    subject moved with it. The render-slice pass split the per-trial loop --
    the six event sites, the ``emit`` binding, the two decoder calls -- into
    ``_render_trial_blocks``, which returns the blocks; ``_build_trials_text``
    is now the join over them and holds no event site at all. Section 5 asserts
    that separation rather than assuming it, so pointing this at the joiner
    would make every check in that section pass over an empty function body.
    """
    return function_node("_render_trial_blocks")


_RENDER_NODE = render_function_node()
_JOIN_NODE = function_node("_build_trials_text")


def logger_calls(node, receiver):
    """``<receiver>.<level>(...)`` calls inside ``node``, as level names."""
    if node is None:
        return []
    levels = {"debug", "info", "warning", "error", "exception"}
    found = []
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr in levels
                and isinstance(sub.func.value, ast.Name)
                and sub.func.value.id == receiver):
            found.append(sub.func.attr)
    return found


print("=" * 70)
print("SECTION 1 -- the four callers, read off the shipped source")
print("=" * 70)


def call_kwargs(source, path_label, name="_build_trials_text"):
    """Every ``name(...)`` call in ``source``, as kwarg-name sets."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return f"<UNPARSEABLE {path_label}>"
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == name):
            out.append(frozenset(kw.arg for kw in node.keywords))
    return out


_EVAL_CALLS = call_kwargs(_EVAL_SRC, "evaluation.py")
check("1a     the shipped evaluation.py was read and parsed (non-degeneracy)",
      (_EVAL_SRC.startswith("<RAISED"), _RENDER_NODE is None), (False, False))

# FOUR PATHS, THREE CALL SITES, and the difference is the correction this
# section had to make. Paths 1 and 2 are not two call sites: BOTH go through the
# single `_build_trials_text(chunk)` inside the nested helper `_user_prompt_for`,
# which the node calls three times -- with the chunk being sent, with the whole
# batch for the stored prompt, and with [] to price the wrapper. So evaluation.py
# holds TWO sites (that one and the measurement), run_harness.py holds the third,
# and a check counting sites as though they were paths reports 2 where it
# expected 3. The distinction matters beyond arithmetic: one edit to
# `_user_prompt_for` moves the send and the stored prompt together.
check("1b     evaluation.py holds exactly ONE _build_trials_text call site -- "
      "inside _user_prompt_for, which serves both the sent chunk and the "
      "wrapper pricing",
      len(_EVAL_CALLS) if isinstance(_EVAL_CALLS, list) else _EVAL_CALLS, 1)
check("1c     ...and it FORWARDS log_events rather than fixing it, which is "
      "what lets one helper serve a loud caller and a silent one",
      sorted(set().union(*_EVAL_CALLS)) if isinstance(_EVAL_CALLS, list)
      else _EVAL_CALLS, ["log_events"])

# THE BLOCK RENDERER IS THE OTHER ENTRY POINT AND IT IS THE ONE THAT MATTERS.
# _build_trials_text is a join over it, so counting only _build_trials_text
# calls would now miss the node's single whole-batch render entirely -- the
# render this file exists to be about.
_BLOCK_CALLS = call_kwargs(_EVAL_SRC, "evaluation.py",
                           name="_render_trial_blocks")
check("1b(ii) ...and TWO _render_trial_blocks call sites: the joiner, which "
      "forwards the flag and is a PASS-THROUGH rather than a render of its "
      "own, and the node's single whole-batch render, which feeds BOTH the "
      "stored prompt and the packer's per-trial costs",
      len(_BLOCK_CALLS) if isinstance(_BLOCK_CALLS, list) else _BLOCK_CALLS, 2)
check("1b(iii) ...and exactly one of the two forwards log_events while the "
      "other passes none -- the node's render IS the stored prompt, so it is "
      "a send and is loud by not asking otherwise",
      sorted((sorted(k) for k in _BLOCK_CALLS), key=len)
      if isinstance(_BLOCK_CALLS, list) else _BLOCK_CALLS,
      [[], ["log_events"]])
check("1d     the joiner forwards the flag to the render and does nothing "
      "else -- one call, and it is the render",
      [n.func.id
       for n in (ast.walk(_JOIN_NODE) if _JOIN_NODE else [])
       if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)],
      ["_render_trial_blocks"])
def _upf_call_args():
    """The argument spelling of every real ``_user_prompt_for(...)`` CALL.

    By AST, not by substring: this file's first draft counted the name in the
    source text and got 6 for 3, because two of the hits are inside comments
    that quote the call. A comment is invisible to an AST walk, which is the
    property that makes the count mean what it says.
    """
    try:
        tree = ast.parse(_EVAL_SRC, _EVAL_PATH)
    except SyntaxError:
        return "<UNPARSEABLE>"
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_user_prompt_for"):
            out.append(ast.unparse(node.args[0]) if node.args else "<none>")
    return sorted(out)


check("1d(ii) the node calls _user_prompt_for TWICE now -- the chunk being "
      "sent and [] to price the wrapper. The third call, `_user_prompt_for("
      "trials)` for the stored prompt, is gone: the stored prompt is assembled "
      "from the one render the node already makes, which is the whole fix",
      _upf_call_args(), ["[]", "chunk"])

# PATH 3 WAS THE MEASUREMENT RENDER AND IT NO LONGER EXISTS. What is checked
# here is its ABSENCE: _trial_input_tokens used to render a trial to price it,
# which made every trial's refusals count a third time. It now prices a block
# the node's one render already produced.
_TIT_NODE = function_node("_trial_input_tokens")
_TIT_SRC = ""
_TIT_BODY = ""
if _TIT_NODE is not None:
    try:
        _TIT_SRC = ast.unparse(_TIT_NODE)
        # THE DOCSTRING IS STRIPPED BEFORE ANY SUBSTRING TEST. It argues at
        # length about the render this function no longer does, naming
        # _build_trials_text, _render_trial_blocks and log_events -- so a test
        # over the whole unparse would read the argument as the defect.
        _body = [n for n in _TIT_NODE.body
                 if not (isinstance(n, ast.Expr)
                         and isinstance(n.value, ast.Constant)
                         and isinstance(n.value.value, str))]
        _TIT_BODY = "\n".join(ast.unparse(n) for n in _body)
    except Exception as _exc:                                  # noqa: BLE001
        _TIT_SRC = f"<RAISED {type(_exc).__name__}: {_exc}>"
        _TIT_BODY = _TIT_SRC
check("1e(0)  _trial_input_tokens was located and unparsed (non-degeneracy: "
      "an absent function would satisfy every `not in` below for free)",
      (_TIT_NODE is not None, _TIT_SRC.startswith("<RAISED")), (True, False))


def _silencing_call_args():
    """First positional argument of every call passing ``log_events=False``.

    ACROSS THE WHOLE MODULE, not inside one function, because the question is
    "who in this file asks for a silent render" and the answer must not depend
    on which helper the asking happens to go through.
    """
    try:
        tree = ast.parse(_EVAL_SRC, _EVAL_PATH)
    except SyntaxError:
        return ["<UNPARSEABLE>"]
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if (kw.arg == "log_events" and isinstance(kw.value, ast.Constant)
                    and kw.value.value is False):
                out.append(ast.unparse(node.args[0]) if node.args else "<none>")
    return out
check("1e     _trial_input_tokens NO LONGER RENDERS AT ALL -- it prices a "
      "block somebody else rendered, so it cannot ask for silence and cannot "
      "add a phantom refusal count. READ OFF THE BODY WITH THE DOCSTRING "
      "STRIPPED: the docstring names all three of these while explaining that "
      "they are gone, so a substring test over the whole unparse would report "
      "the explanation as the defect",
      ("log_events" in _TIT_BODY, "_build_trials_text" in _TIT_BODY,
       "_render_trial_blocks" in _TIT_BODY),
      (False, False, False))
check("1e(i)  non-degeneracy: that body is a real statement, not an empty "
      "string left behind by the docstring strip",
      len(_TIT_BODY) > 10 and "estimate_prompt_tokens" in _TIT_BODY, True)
check("1e(ii-a) ...and it takes ONE parameter, a rendered BLOCK, so there is "
      "no trial object left for it to render",
      [a.arg for a in (_TIT_NODE.args.args if _TIT_NODE else [])], ["block"])
check("1e(ii) ...and it is a pure call to the ONE estimator, so no second "
      "formula was introduced when the render was taken away from it",
      [n.func.id for n in (ast.walk(_TIT_NODE) if _TIT_NODE else [])
       if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)],
      ["estimate_prompt_tokens"])
check("1e(iii) THE ONE CALLER THAT STILL ASKS FOR SILENCE is the wrapper "
      "pricing -- a render that is measured and never sent, which is exactly "
      "what the flag is for. Without it the flag and _SilentLog would be a "
      "mechanism with no subject",
      sorted(_silencing_call_args()), ["[]"])

# PATH 4 lives in another module and must NOT have been touched.
_HARNESS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(_EVAL_PATH)), "evaluation", "run_harness.py")
_HARNESS_SRC = ""
try:
    with open(_HARNESS_PATH, encoding="utf-8") as _fh:
        _HARNESS_SRC = _fh.read()
except Exception as _exc:                                      # noqa: BLE001
    _HARNESS_SRC = f"<RAISED {type(_exc).__name__}: {_exc}>"
_HARNESS_CALLS = call_kwargs(_HARNESS_SRC, "run_harness.py")
check("1f     run_harness.py was read (non-degeneracy)",
      _HARNESS_SRC.startswith("<RAISED"), False)
check("1g     path 4 (build_contexts) calls the renderer and passes NO "
      "log_events -- send-like, deliberately out of scope",
      _HARNESS_CALLS, [frozenset()])
check("1h     FOUR renderer call sites in the package and no fifth -- the\n"
      "       joiner's own call, the joiner site inside _user_prompt_for, the\n"
      "       node's whole-batch render, and run_harness.py's. It was three,\n"
      "       and the arithmetic is not a regression: the joiner's call is a\n"
      "       PASS-THROUGH reached only from _build_trials_text's own callers,\n"
      "       so the number of times a trial is RENDERED went DOWN by one --\n"
      "       which is the whole change, and 4c measures it",
      len(_EVAL_CALLS) + len(_BLOCK_CALLS) + len(_HARNESS_CALLS), 4)


print()
print("=" * 70)
print("SECTION 2 -- the measurement path is silent, and otherwise identical")
print("=" * 70)

# THE MEASUREMENT PATH IS THE SILENT RENDER ITSELF NOW. It used to be
# `_trial_input_tokens(trial)`, which rendered; that function no longer
# renders, so driving it here would emit nothing for a reason that has nothing
# to do with the flag -- a check passing because its subject is gone. What is
# still real, and still the flag's whole job, is a render asked to be silent.
clear_counters()
_measure_recs = records(
    lambda: drive(_build_trials_text, _BATCH, log_events=False),
    level=logging.DEBUG)
check("2a     a render asked for silence emits ZERO render events AT DEBUG -- "
      "the strictest level, so no per-trial detail hides under the default",
      len(render_events(_measure_recs)), 0)
check("2b     ...and zero records of any kind from this module",
      [r for r in _measure_recs
       if r.get("logger") == "oncotriage.agent.evaluation"], [])

# NON-DEGENERACY: the same batch through a SEND render is loud. Without this,
# 2a passes for a batch that never had anything to say.
clear_counters()
_send_recs = records(lambda: drive(_build_trials_text, _BATCH),
                     level=logging.DEBUG)
check("2c     non-degeneracy: the SAME batch on a send render is loud",
      len(render_events(_send_recs)) > 0, True)
check("2d     ...and covers five distinct events, so 2a is about a set and "
      "not about one line",
      len({r.get("event") for r in render_events(_send_recs)}), 5)

# IDENTICAL TEXT and IDENTICAL TOKENS -- suppression must be logging and
# nothing else.
_texts_loud = [drive(_build_trials_text, [t]) for t in _BATCH]
_texts_quiet = [drive(_build_trials_text, [t], log_events=False) for t in _BATCH]
check("2e     the rendered text is byte-identical with and without logging",
      _texts_quiet, _texts_loud)
check("2f     non-degeneracy: that text is not empty",
      all(isinstance(t, str) and len(t) > 50 for t in _texts_loud), True)
# THE PACKER'S FIGURE IS SLICED OUT OF ONE RENDER AND MUST STILL BE THE PRICE
# OF THE BYTES THAT GET SENT. This is the behavioural half of the render-slice
# pass; the exhaustive version, over every tricky content class and over
# batches rather than this file's four trials, is
# tests/test_agent_stage5_render_slice_equality.py. It is repeated here in one
# line because THIS file is the one that would go green if somebody
# "simplified" the packer back into re-rendering.
_blocks_batch = drive(_render_trial_blocks, _BATCH)
check("2g     the token count the packer reads -- sliced out of ONE whole-"
      "batch render -- is identical to pricing each trial's own loud render",
      [drive(_trial_input_tokens, b) for b in _blocks_batch],
      [estimate_prompt_tokens(t) for t in _texts_loud])
# `n > 0` ON A DRIVE RESULT IS A RAISE WAITING TO HAPPEN. drive() turns an
# exception into a "<RAISED ...>" STRING, and `str > int` is a TypeError that
# escapes while check()'s argument list is being evaluated -- killing the run
# and reporting one traceback where it owes a summary. Found by the revert
# harness, not by reading: reverting _trial_input_tokens to render again makes
# every one of these a string. This project has shipped that shape nine times.
_tok_2h = [drive(_trial_input_tokens, b) for b in _blocks_batch]
check("2h     non-degeneracy: those token counts are non-zero and differ "
      "between trials",
      (all(isinstance(n, int) and n > 0 for n in _tok_2h),
       len(set(_tok_2h)) > 1),
      (True, True))
check("2h(ii) ...and the batch render produced one block per trial, so 2g is "
      "not comparing two lists that happen to be the same length by accident",
      len(_blocks_batch), len(_BATCH))


print()
print("=" * 70)
print("SECTION 3 -- paths 1 and 2 are unchanged")
print("=" * 70)

_by_event = {}
for _r in render_events(_send_recs):
    _by_event.setdefault(_r.get("event"), []).append(_r)

check("3a     the markdown aggregate fires once for the whole render",
      len(_by_event.get("trial_markdown_escape_decoded", [])), 1)

# EXPECTATIONS DERIVED FROM THE DECODER, NEVER TYPED. A literal that agrees
# with the code because the same hand wrote both is the defect this project
# names by name, and the first draft of this file shipped exactly that.
_exp_affected = sum(
    1 for t in _BATCH
    if sum(at(drive(_decode_markdown_escapes,
                    str(t["trial"]["eligibility"][f])), 1, 0)
           for f in ("inclusion_criteria", "exclusion_criteria")))
_exp_sequences = sum(
    at(drive(_decode_markdown_escapes,
             str(t["trial"]["eligibility"][f])), 1, 0)
    for t in _BATCH for f in ("inclusion_criteria", "exclusion_criteria"))
check("3b     ...reporting every trial rendered and the ones that decoded, "
      "both derived from the decoder rather than typed here",
      (at(_by_event.get("trial_markdown_escape_decoded", []), 0, {}).get("total"),
       at(_by_event.get("trial_markdown_escape_decoded", []), 0, {})
       .get("trials_affected"),
       at(_by_event.get("trial_markdown_escape_decoded", []), 0, {}).get("count")),
      (len(_BATCH), _exp_affected, _exp_sequences))
check("3b(ii) non-degeneracy: the batch really does have some trials decoding "
      "and some not, so 3b is not comparing a run of equal numbers",
      (0 < _exp_affected < len(_BATCH), _exp_sequences > 0), (True, True))
check("3c     the per-trial markdown detail is at DEBUG, one per affected trial",
      (len(_by_event.get("trial_markdown_escape_decoded_trial", [])),
       {r.get("level")
        for r in _by_event.get("trial_markdown_escape_decoded_trial", [])}),
      (_exp_affected, {"DEBUG"}))
check("3d     the markdown refusal is a per-trial WARNING naming its trial",
      [(r.get("level"), r.get("nct_id"))
       for r in _by_event.get("trial_markdown_escape_unresolved", [])],
      [("WARNING", "NCT10000001")])
check("3e     the entity decode is a per-trial INFO naming its trial",
      [(r.get("level"), r.get("nct_id"))
       for r in _by_event.get("trial_escaped_entity_decoded", [])],
      [("INFO", "NCT10000002")])
check("3f     the fence neutralization is a per-trial WARNING naming its trial",
      [(r.get("level"), r.get("nct_id"))
       for r in _by_event.get("trial_fence_marker_neutralized", [])],
      [("WARNING", "NCT10000003")])
# THE SIXTH SITE, and why it is proven structurally instead of by example.
check("3g     trial_escaped_entity_unresolved cannot be triggered by any "
      "input -- html.unescape collapses a nested chain in one pass, and it is "
      "0 across the whole corpus -- so section 5 covers that site by AST",
      len(_by_event.get("trial_escaped_entity_unresolved", [])), 0)


print()
print("=" * 70)
print("SECTION 4 -- the flag is logging-only, and the phantom counts are gone")
print("=" * 70)

# WHAT THIS SECTION USED TO ASSERT, AND WHY IT WAS RIGHT UNTIL IT WAS NOT.
#
# 4a and 4d are UNCHANGED and still say the thing the flag promises: it governs
# the log channel and nothing else, so a silent render lands exactly the same
# refusal counts, key for key, as a loud one. That was true before the
# render-slice pass and it is true after it -- the flag's contract did not
# move.
#
# 4c DID move, and it is the only check in this file that had to. It read:
#
#     "the MEASUREMENT path still increments them -- the documented
#      inflation, asserted rather than left to prose"
#
# ...and it drove `_trial_input_tokens(trial)`, which rendered the trial a
# second time to price it. The counters really did land a second set of counts,
# the check really did assert it, and the docstring on the renderer really did
# argue for it as "a known inflation rather than an oversight" on the ground
# that a refusal is a fact about the CORPUS, which a measurement render
# observes as truly as a sent one.
#
# THAT ARGUMENT WAS TRUE ABOUT ONE RENDER AND FALSE ABOUT THE COUNTER. The
# counter is a total, not a set: it does not record "this shape exists", it
# records HOW MANY TIMES a refusal happened, and a phantom render makes that
# number a count of renders rather than of refusals. Measured on a no-split
# patient, every trial was rendered three times -- the whole-batch stored
# prompt, the packer's measurement, the chunk actually sent -- so both counters
# read exactly 1.5x what two send renders would have left.
#
# THE FIX WAS TO REMOVE THE RENDER, NOT TO SUPPRESS THE COUNT. Suppressing
# would have made one flag govern two unrelated things -- what is logged and
# what is counted -- and would have left the redundant work and the residual
# log volume in place. So the flag's semantics are untouched (4a, 4d) and the
# call graph is what changed (4c, below).

clear_counters()
for _t in _BATCH:
    drive(_build_trials_text, [_t])
_counts_loud = counter_totals()

clear_counters()
for _t in _BATCH:
    drive(_build_trials_text, [_t], log_events=False)
_counts_quiet = counter_totals()

check("4a     both refusal counters land identically with and without logging",
      _counts_quiet, _counts_loud)
check("4b     non-degeneracy: at least one counter actually moved, so 4a is "
      "not comparing two zeroes", sum(_counts_loud) > 0, True)

# --- 4c: THE PHANTOM COUNTS ARE GONE, MEASURED AS A PRODUCTION SEQUENCE -----
#
# Driven as the sequence a real patient produces rather than as one function
# call, because the defect was never in a function -- it was in how many times
# the node reached the renderer. A no-split patient is:
#
#   BEFORE   the whole-batch stored-prompt render
#            + one render PER TRIAL for the packer's measurement
#            + the render of the single chunk that is sent          = 3
#   AFTER    the whole-batch render, which now feeds the stored prompt AND
#            the packer
#            + the render of the single chunk that is sent          = 2
#
# The wrapper pricing is in both arms and contributes nothing: it renders zero
# trials.
def _one_render_counts():
    clear_counters()
    drive(_render_trial_blocks, _BATCH)
    return counter_totals()


def _no_split_patient_counts():
    """The counter totals a no-split patient leaves, as the node produces them."""
    clear_counters()
    _blocks = drive(_render_trial_blocks, _BATCH)          # THE one render
    drive(_build_trials_text, [], log_events=False)        # wrapper pricing
    drive(pack_trials_by_input_tokens, _BATCH, 500, 12000, 5, blocks=_blocks)
    drive(_build_trials_text, _BATCH)                      # the chunk sent
    return counter_totals()


_TRUTH = _one_render_counts()
_NO_SPLIT = _no_split_patient_counts()

# NON-DEGENERACY, AND IT IS ABOUT ONE COUNTER RATHER THAN BOTH -- MEASURED,
# NOT ASSUMED. _BATCH moves the MARKDOWN refusal counter (the "CLL\\ SLL"
# trial) and cannot move the ENTITY refusal counter: section 3g establishes
# that trial_escaped_entity_unresolved is unreachable from any input this
# renderer can be handed, because html.unescape collapses a nested chain in a
# single pass. So the entity component of every tuple below is 0 == 2 * 0,
# which is TRUE and says nothing; the markdown component is what carries the
# arithmetic, and this check is what stops the pair reading as though both did.
check("4b(ii) non-degeneracy: one render of this batch leaves a real, "
      "non-zero MARKDOWN refusal count, so the identity below is not two "
      "zeroes -- and the entity counter is 0 by 3g's structural finding",
      (at(_TRUTH, 0, 0) > 0, at(_TRUTH, 1, -1)), (True, 0))
check("4c     A NO-SPLIT PATIENT NOW LEAVES EXACTLY TWO RENDERS' WORTH OF "
      "REFUSAL COUNTS, NOT THREE. Both remaining renders are SENDS -- the "
      "stored prompt and the chunk -- so the counters count refusals that "
      "reached a judge, and nothing else",
      _NO_SPLIT, tuple(2 * n for n in _TRUTH))
check("4c(ii) ...and that is strictly less than the three renders' worth the "
      "packer used to add, which is the defect this pass removed. Compared as "
      "a SUM because only one of the two counters is reachable from this "
      "batch; per-counter equality is 4c above",
      sum(_NO_SPLIT) < 3 * sum(_TRUTH), True)
check("4c(iv) ...and the ratio is exactly 2/3 -- the 1.5x phantom removed, "
      "stated as the number rather than as an inequality",
      (sum(_NO_SPLIT), 3 * sum(_TRUTH)),
      (2 * sum(_TRUTH), 3 * sum(_TRUTH)))
def _render_then_pack_counts():
    """Counters after ONE render followed by a pack over its blocks."""
    clear_counters()
    _blocks = drive(_render_trial_blocks, _BATCH)
    drive(pack_trials_by_input_tokens, _BATCH, 500, 12000, 5, blocks=_blocks)
    return counter_totals()


check("4c(iii) THE PACKER ITSELF NOW COUNTS NOTHING. Handed the blocks it "
      "renders nothing at all, so a render followed by a pack leaves exactly "
      "what the render alone left -- the single fact the whole change rests on",
      _render_then_pack_counts(), _TRUTH)

clear_counters()
_keys_loud = None
for _t in _BATCH:
    drive(_build_trials_text, [_t])
_keys_loud = dict(MARKDOWN_ESCAPE_DECODE_UNRESOLVED)
clear_counters()
for _t in _BATCH:
    drive(_build_trials_text, [_t], log_events=False)
check("4d     ...and the counter KEYS match too, not merely the totals",
      dict(MARKDOWN_ESCAPE_DECODE_UNRESOLVED), _keys_loud)
check("4e     non-degeneracy: those keys are a real, non-empty reason map",
      len(_keys_loud) > 0, True)
clear_counters()


print()
print("=" * 70)
print("SECTION 5 -- the suppression is uniform BY CONSTRUCTION")
print("=" * 70)

_log_calls = logger_calls(_RENDER_NODE, "log")
_emit_calls = logger_calls(_RENDER_NODE, "emit")
check("5a     the render contains NO bare `log.` call -- there is nothing for "
      "a new call site to reach past the switch", _log_calls, [])
check("5b     ...and every one of the six event sites goes through `emit`",
      len(_emit_calls), 6)
check("5c     non-degeneracy: those six cover all four levels the render uses",
      sorted(set(_emit_calls)), ["debug", "info", "warning"])
check("5d     `emit` is bound from log_events and from nothing else",
      "emit = log if log_events else _SILENT_LOG" in _EVAL_SRC, True)
check("5e     log_events is KEYWORD-ONLY, so a stray positional argument can "
      "never be read as the flag",
      [a.arg for a in (_RENDER_NODE.args.kwonlyargs if _RENDER_NODE else [])],
      ["log_events"])
check("5f     ...and defaults to True, so silence is asked for and never "
      "inherited by forgetting",
      [getattr(d, "value", "<none>")
       for d in (_RENDER_NODE.args.kw_defaults if _RENDER_NODE else [])], [True])


print()
print("=" * 70)
print("SECTION 6 -- the sink is substitutable for the real logger")
print("=" * 70)

_real_surface = {m for m in dir(StructuredLogger) if not m.startswith("_")}
_sink_surface = {m for m in dir(_SilentLog) if not m.startswith("_")}
check("6a     _SilentLog implements every public method of StructuredLogger, "
      "so a future call site at a new level cannot raise inside a render",
      sorted(_real_surface - _sink_surface), [])
check("6b     non-degeneracy: that surface is not empty",
      len(_real_surface) >= 5, True)
def call_level(instance, name):
    """``instance.name("msg", ...)`` as a VALUE, attribute lookup included.

    THE ATTRIBUTE LOOKUP HAS TO BE INSIDE THIS, not in a ``drive(getattr(...))``
    argument list. The first draft wrote the latter, and when the s6 revert
    removed a method from the sink the ``getattr`` raised at module level and
    took the ENTIRE FILE with it -- 54 checks replaced by one traceback, from
    the control written to detect exactly that removal. It is the same defect
    ``drive`` exists to prevent, reintroduced one call deeper, and it is the
    reason 6a reads a method SET rather than calling each one blind.
    """
    try:
        return getattr(instance, name)("msg", stage=5, count=1)
    except BaseException as exc:                               # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


check("6c     every level method accepts the project's call shape and returns "
      "None", [call_level(_SilentLog(), m)
                for m in sorted(_real_surface - {"std"})],
      [None] * len(_real_surface - {"std"}))
# 6d USED TO READ `_SILENT_LOG is _SILENT_LOG`, which is True for every object
# in Python and could never have failed -- the second tautology this file
# shipped, after a 9d that compared the logger level with itself. The question
# it was trying to ask is "is the sink built once at module scope, or freshly
# per render", and that is answered by what the RENDER REFERENCES: a name, not
# a constructor call.
_sink_ctor_calls = 0
for _sub in (ast.walk(_RENDER_NODE) if _RENDER_NODE else []):
    if (isinstance(_sub, ast.Call) and isinstance(_sub.func, ast.Name)
            and _sub.func.id == "_SilentLog"):
        _sink_ctor_calls += 1
check("6d     the render reaches the module-level singleton and never builds a "
      "sink of its own", _sink_ctor_calls, 0)
check("6d(ii) ...and the singleton it reaches is bound at module scope",
      isinstance(getattr(evaluation, "_SILENT_LOG", None), _SilentLog), True)
check("6e     ...and it is an instance of the documented class",
      isinstance(evaluation._SILENT_LOG, _SilentLog), True)


print()
print("=" * 70)
print("SECTION 7 -- path 4 (the rater harness) still logs")
print("=" * 70)

# build_contexts renders one trial at a time, like the measurement path, but its
# text IS shown to a rater. It must therefore stay LOUD. Driven through the real
# function rather than asserted from its source.
try:
    from oncotriage.evaluation.run_harness import build_contexts
except Exception as _exc:                                      # noqa: BLE001
    build_contexts = None
    print(f"  NOTE  build_contexts import raised: "
          f"{type(_exc).__name__}: {_exc}")

check("7a     build_contexts imported (non-degeneracy for 7b)",
      build_contexts is not None, True)
if build_contexts is not None:
    _h_recs = records(lambda: drive(build_contexts, _BATCH), level=logging.DEBUG)
    check("7b     the rater harness render still emits its render events -- "
          "send-like, out of scope, and shown unchanged rather than assumed",
          len(render_events(_h_recs)) > 0, True)
    check("7c     ...and it produced a context per trial",
          len(at(drive(build_contexts, _BATCH), 0, [])), len(_BATCH))
else:
    check("7b(na) build_contexts could not be imported, so path 4 was NOT "
          "verified -- recorded as a FAILURE rather than skipped", True, False)
    check("7c(na) ...and neither was its per-trial context count", True, False)
clear_counters()


print()
print("=" * 70)
print("SECTION 8 -- negative controls, every one shown to FIRE")
print("=" * 70)

# THE REGRESSION THIS PASS EXISTS TO PREVENT: the measurement path logging
# again. Rebinding _SILENT_LOG to the real logger is precisely that -- the flag
# is still passed, the switch still runs, and the silent branch stops being
# silent. It is the shape a future edit would take if somebody "simplified" the
# sink away.
_saved_sink = evaluation._SILENT_LOG
try:
    evaluation._SILENT_LOG = evaluation.log
    # DRIVEN THROUGH A SILENT RENDER, not through _trial_input_tokens: that
    # function no longer renders, so it would emit nothing whether the sink
    # were rebound or not and this control would report the regression as
    # absent while proving nothing.
    _ctl = records(lambda: drive(_build_trials_text, _BATCH, log_events=False),
                   level=logging.DEBUG)
    check("8a     CONTROL: with the sink rebound to the real logger, the "
          "measurement path is loud again -- so 2a is not passing for free",
          len(render_events(_ctl)) > 0, True)
    check("8b     CONTROL: ...and it is the same event set a send emits, "
          "which is what makes the misattribution possible",
          len({r.get("event") for r in render_events(_ctl)}) >= 4, True)
finally:
    evaluation._SILENT_LOG = _saved_sink
check("8c     the shipped sink is restored",
      evaluation._SILENT_LOG is _saved_sink, True)
clear_counters()
check("8d     ...and the silent render is silent again",
      len(render_events(records(
          lambda: drive(_build_trials_text, _BATCH, log_events=False),
          level=logging.DEBUG))), 0)

# THE OTHER DIRECTION: a caller that forgets the flag gets logging. This is what
# says the DEFAULT is the loud one, and it is the control for 5f.
clear_counters()
check("8e     CONTROL: rendering the measurement batch WITHOUT the flag is "
      "loud, so the silence in 2a comes from the flag and not from the batch",
      len(render_events(records(
          lambda: [drive(_build_trials_text, [t]) for t in _BATCH],
          level=logging.DEBUG))) > 0, True)
clear_counters()


print()
print("=" * 70)
print("SECTION 9 -- hygiene")
print("=" * 70)

check("9a     the module-level counters are left empty for the next reader",
      (sum(MARKDOWN_ESCAPE_DECODE_UNRESOLVED.values()),
       sum(ESCAPED_ENTITY_DECODE_UNRESOLVED.values())), (0, 0))

_after = ""
try:
    with open(_EVAL_PATH, encoding="utf-8") as _fh:
        _after = _fh.read()
except Exception as _exc:                                      # noqa: BLE001
    _after = f"<RAISED {type(_exc).__name__}: {_exc}>"
_EVAL_SHA_AFTER = hashlib.sha256(_after.encode()).hexdigest()
check("9b     this file writes nothing: evaluation.py hashes the same twice",
      _EVAL_SHA_AFTER, _EVAL_SHA_BEFORE)
check("9c     ...and that is a real digest (non-degeneracy)",
      len(_EVAL_SHA_BEFORE) == 64 and not _EVAL_SRC.startswith("<RAISED"), True)
check("9d     the package logger level is back where it started -- every\n"
      "       capture at DEBUG restored it",
      logging.getLogger("oncotriage").level, _LOGGER_LEVEL_AT_IMPORT)
check("9e     non-degeneracy: that level is a real threshold, so 9d is not\n"
      "       comparing two unset values",
      _LOGGER_LEVEL_AT_IMPORT in (logging.DEBUG, logging.INFO,
                                  logging.WARNING, logging.ERROR,
                                  logging.CRITICAL), True)


print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)
if _FAILURES:
    print()
    for _f in _FAILURES:
        print(f"  FAILED: {_f}")
    print()
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 2026-08-15
"""
