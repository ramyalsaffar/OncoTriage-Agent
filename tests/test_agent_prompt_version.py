# Stage 5 System Prompt Version Guard
#####################################

"""The Stage 5 system prompt cannot change without somebody deciding it did.

WHY THIS FILE EXISTS
--------------------
``oncotriage/agent/prompts.py`` carries two identifiers that answer different
questions about the same text:

    PROMPT_VERSION      what a human INTENDED. Hand-maintained, and therefore
                        capable of being wrong.
    prompt_sha256(...)  what was ACTUALLY sent. Mechanical, and therefore
                        incapable of being wrong.

Nothing made them agree. An edit to the template shipped silently: the stored
``inferences.llm_classifier_prompt_sha256`` moved, ``llm_classifier_prompt_version``
did not, and two runs of "version 1.0.0" meant two different classifiers. The
disagreement was recoverable from the database AFTER the fact and nothing
raised BEFORE it. This file is what raises.

It is also the standing guard the extraction pass owed. That pass proved the
rendered prompt byte-identical across every variant and then threw the proof
away with the session -- which is pass 20f-5's lesson verbatim ("proved correct
once and then unguarded"), and this file is written so it cannot happen twice.

WHAT IT HOLDS
-------------
    1. THE AXES ARE DERIVED, NOT REMEMBERED. The variant matrix is a
       cross-product with one axis per PARAMETER of ``render_system_prompt``,
       read off ``inspect.signature``. A parameter added, removed or renamed is
       a NAMED failure rather than silent under-coverage -- the matrix cannot
       quietly stop covering an input it does not know about. The skip-reason
       axis is likewise derived: the three ``MESH_FILTER_SKIP_*`` constants come
       out of ``oncotriage/agent/state.py``, and the ``"unrecorded"`` fallback is
       lifted BY AST out of the one line in ``oncotriage/agent/evaluation.py``
       that supplies it.
    2. THE SNAPSHOT, ``tests/snapshots/prompt_version_digests.json``: the
       version, the parameter list, and one sha256 per variant.
    3. TWO FAILURE MODES WITH DIFFERENT MESSAGES, because they have different
       fixes. A digest that moved while the version held is "you edited the
       template and did not bump"; a version that moved is "regenerate so the
       new version is deliberately recorded". Reporting either as the other
       sends the reader to the wrong file.
    4. TWO INVARIANTS THE MATRIX ITSELF PROVES. Within the confirmed branch the
       skip reason is unread, so those digests must agree; across patient
       records they must not. The second is the non-degeneracy guard on the
       first -- without it, "all these digests are equal" is also satisfied by a
       render function that returns a constant. (The second axis was
       ``trial_count`` until PROMPT_VERSION 1.6.0 deleted that parameter and
       moved the patient record into this message.)
    5. THE CONTROLS, four of them, run as part of this file so they cannot go
       stale. Three drive the comparison directly with doctored inputs; the
       fourth EXECS A ONE-CHARACTER-PATCHED IN-MEMORY COPY of prompts.py, which
       is what establishes that the digests track the SHIPPED template rather
       than something this file computed for itself.

WHY THERE IS NO ``git show`` ANYWHERE IN HERE. A commit recedes. A shallow
clone, a squash, a `git archive` export or a subtree move leaves the revision
unreachable, and three files in this suite already abort in a tree with no
`.git` for exactly that reason. The reference is a committed golden file, and
it is regenerated ONLY through ``--update-snapshot`` -- never automatically,
because a snapshot that rewrites itself to accommodate the code makes whatever
the code does correct by definition.

NO NETWORK, NO KEYS, NO SPEND, NO DATABASE, NO SUBPROCESS, NO FIXTURE, NO GIT,
NO CORPUS. It renders one string per variant, hashes them, and reads one JSON
file.
Not in the collision matrix: it writes nothing in the repository except through
the explicit ``--update-snapshot`` flag, and the two source files it reads are
written by neither of the suite's two writers.

Run from terminal:
    python tests/test_agent_prompt_version.py
    python tests/test_agent_prompt_version.py --update-snapshot

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# The package bootstrap every file in tests/ carries. The candidate directory
# is the PARENT of this file's, because the package sits beside tests/ rather
# than inside it. `pip install -e .` makes the whole block a no-op.
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

import ast
import hashlib
import inspect
import json
import time

from oncotriage.agent import evaluation as _evaluation
from oncotriage.agent import prompts as _prompts
from oncotriage.agent import state as _state
from oncotriage.agent.prompts import (
    PROMPT_VERSION,
    prompt_sha256,
    render_system_prompt,
)


#------------------------------------------------------------------------------


_T_START = time.time()


# ===========================================================================
# MINIMAL ASSERTION HARNESS
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


def fail(label: str, message: str) -> None:
    """Record a failure that is not an equality comparison."""
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {message}")
    print(f"  FAIL  {label}")
    print(f"          {message}")


def detail(message: str) -> None:
    """Attach diagnostic text to the run without counting it as an outcome.

    The findings evaluate() produces are DETAIL on one recorded failure, not
    failures in their own right: counting them would make the file's failure
    total depend on how many variants happen to be in the matrix, so the same
    defect would report 1 or 16 depending on how many skip reasons state.py
    declares.
    """
    _FAILURES.append(f"          {message}")
    print(f"        {message}")


# THE PATHS COME FROM THE MODULES' OWN __file__, never from a _code_dir guess.
# Pass 20d-1 moved eleven files into tests/ and every one of them had a path
# one directory off; deriving from the imported module's __file__ also proves
# the file under inspection is the one THIS process imported rather than a
# same-named copy elsewhere on sys.path.
_PROMPTS_PATH = os.path.abspath(_prompts.__file__)
_EVALUATION_PATH = os.path.abspath(_evaluation.__file__)
_SNAPSHOT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "snapshots", "prompt_version_digests.json")
_UPDATE_SNAPSHOT = "--update-snapshot" in sys.argv

print("=" * 78)
print("STAGE 5 SYSTEM PROMPT VERSION GUARD")
print("=" * 78)
print(f"Template:  {_PROMPTS_PATH}")
print(f"Snapshot:  {_SNAPSHOT_PATH}")
print(f"Version:   {PROMPT_VERSION}")


# ===========================================================================
# SECTION 1 -- THE AXES, DERIVED FROM THE CODE
# ===========================================================================
#
# The matrix is a cross-product with one axis per parameter of
# render_system_prompt. Hardcoding the axis names would mean a parameter added
# tomorrow is simply never varied -- the snapshot would still match, every
# check would still pass, and the guard would cover strictly less than it
# claims. That is the shape this project calls a check that has stopped
# checking, so the parameter list is read off the signature and any name this
# file does not know how to drive is a NAMED failure.

print("\n" + "=" * 78)
print("SECTION 1 -- the variant axes are derived from the render signature")
print("=" * 78)

_SIG_PARAMS = tuple(inspect.signature(render_system_prompt).parameters)


def _skip_reason_constants():
    """The MESH_FILTER_SKIP_* vocabulary, read out of oncotriage/agent/state.py.

    A closed vocabulary declared in one place; reading it here rather than
    retyping it means a fourth skip reason added to state.py widens this matrix
    automatically and fails the snapshot until somebody records the new digest
    on purpose.
    """
    return {name: value for name, value in vars(_state).items()
            if name.startswith("MESH_FILTER_SKIP_") and isinstance(value, str)}


def _unrecorded_fallback():
    """The literal Stage 5 substitutes for an absent skip reason, BY AST.

    ``oncotriage/agent/evaluation.py`` builds the value the renderer is handed
    as ``state.get("mesh_filter_skip_reason") or "unrecorded"``. That string is
    a real variant of the prompt -- it is what every run whose Stage 4 never
    reported renders -- and it is not in state.py's vocabulary, so it has to
    come from the line that produces it. Returns None when the shape is no
    longer there, which the caller reports rather than papering over with the
    remembered value.
    """
    tree = ast.parse(open(_EVALUATION_PATH, encoding="utf-8").read())
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "_mesh_filter_reason"
                        for t in node.targets)):
            continue
        if not isinstance(node.value, ast.BoolOp) or not isinstance(node.value.op, ast.Or):
            continue
        for operand in node.value.values:
            if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
                return operand.value
    return None


_SKIP_CONSTANTS = _skip_reason_constants()
_FALLBACK = _unrecorded_fallback()

check("state.py declares a non-empty MESH_FILTER_SKIP_* vocabulary "
      "(non-degeneracy: an empty one would collapse the matrix silently)",
      len(_SKIP_CONSTANTS) >= 2, True)

if _FALLBACK is None:
    fail("evaluation.py's absent-skip-reason fallback was located by AST",
         "no `_mesh_filter_reason = ... or \"<literal>\"` assignment found in "
         f"{_EVALUATION_PATH}. The renderer is handed a value this matrix no "
         f"longer covers; re-derive the axis before trusting this run.")
else:
    check("evaluation.py's absent-skip-reason fallback was located by AST",
          isinstance(_FALLBACK, str) and bool(_FALLBACK), True)

# The reason axis: every declared skip reason, plus the fallback that no
# constant names. Sorted so the matrix -- and therefore the snapshot -- is
# stable across runs and machines.
_REASONS = tuple(sorted(set(_SKIP_CONSTANTS.values())
                        | ({_FALLBACK} if _FALLBACK else set())))

# One axis per parameter. The values are this file's choice; the KEYS are not.
_AXES = {
    # Both branches of the one conditional in the template.
    "mesh_filter_applied": (True, False),
    "mesh_filter_skip_reason": _REASONS,
    # THE trial_count AXIS WAS HERE AND ITS PARAMETER IS GONE (PROMPT_VERSION
    # 1.6.0). Section 5 used to render the count into an instruction
    # ("Evaluate ALL 0 trials"), which is why the zero form was a variant; the
    # instruction now counts the trials in the user message, the parameter was
    # interpolated nowhere, and it was deleted rather than left as an argument
    # the renderer accepts and discards. An axis for a parameter that does not
    # exist would fail the signature check immediately below -- which is the
    # check doing its job, not a reason to keep the parameter.
    #
    # Its replacement is patient_record, which 1.6.0 moved into this message.
    # Two values, and NEITHER IS EMPTY: "" and a real record differ in the
    # rendered bytes, but an empty-string axis point would also be satisfied by
    # a template that had stopped interpolating the argument at all. Both points
    # are non-empty and distinct, so the digest can only separate them if the
    # value genuinely reaches the text.
    #
    # They are LITERALS, never _create_patient_summary output: this matrix must
    # render the same bytes on a machine with no data directory, and a snapshot
    # keyed on a parsed bundle would be a snapshot of the corpus.
    "patient_record": ("<probe A: no patient record>",
                       "Age: 61 | Sex: female\nCancer Stage: 3"),
}

check("every parameter of render_system_prompt has an axis in this matrix",
      sorted(_SIG_PARAMS), sorted(_AXES))
check("...and this matrix drives no axis the signature does not have",
      sorted(set(_AXES) - set(_SIG_PARAMS)), [])
check("the reason axis is non-degenerate (more than one distinct value)",
      len(_REASONS) >= 2, True)

print(f"  [info] axes: " + ", ".join(f"{k}={len(v)}" for k, v in _AXES.items())
      + f"  ->  {len(_AXES['mesh_filter_applied']) * len(_REASONS) * len(_AXES['patient_record'])} variants")


# ===========================================================================
# SECTION 2 -- RENDER EVERY VARIANT AND DIGEST IT
# ===========================================================================

print("\n" + "=" * 78)
print("SECTION 2 -- render every variant")
print("=" * 78)


def _variant_id(kwargs: dict) -> str:
    """A stable key built from the PARAMETER NAMES, so a rename shows up.

    The id is derived rather than labelled ("confirmed", "unconfirmed") on
    purpose: a hand-written label survives a signature change unchanged and
    would keep the snapshot matching a matrix that had quietly stopped
    describing the function.

    IT MUST NOT RAISE ON A SIGNATURE THAT NO LONGER MATCHES THE MATRIX, and the
    first version did: a plain `kwargs[name] for name in _SIG_PARAMS` throws
    KeyError the moment a parameter is renamed, which is the exact condition
    Section 1 exists to REPORT -- so the file died with a traceback before its
    own finding could be printed. Measured, not reasoned about: renaming
    `patient_record` to `record` in a copy produced a KeyError and no summary.
    Names the signature does not mention are appended in sorted order, so the
    id is total over any kwargs dict while staying byte-identical to what it
    produced before whenever the two agree -- which is every run where the
    snapshot is meant to match.
    """
    ordered = [name for name in _SIG_PARAMS if name in kwargs]
    ordered += sorted(k for k in kwargs if k not in ordered)
    return ";".join(f"{name}={kwargs[name]!r}" for name in ordered)


def _matrix():
    """Every combination, in a deterministic order."""
    out = []
    for applied in _AXES["mesh_filter_applied"]:
        for reason in _AXES["mesh_filter_skip_reason"]:
            for record in _AXES["patient_record"]:
                out.append({"mesh_filter_applied": applied,
                            "mesh_filter_skip_reason": reason,
                            "patient_record": record})
    return out


_RENDER_ERRORS = []


def _render(render_fn, kwargs: dict) -> str:
    """Render one variant, converting a RAISE into a recorded value.

    A BARE CALL HERE WOULD ABORT THE FILE, and the case that aborts it is
    precisely the one Section 1 exists to detect: rename a parameter of
    render_system_prompt and `render_fn(**kwargs)` raises TypeError at module
    level, so the run reports one traceback where it owes 39 results -- Section
    1's named failure among them. This project has shipped that shape three
    times (test_storage_query_layer.py, test_dashboard_reproducibility_tab.py,
    test_agent_trial_verdict_normalization.py); it is not shipping it a fourth.

    The marker is returned INSTEAD of a rendered prompt, so it flows into the
    digest as a value that cannot match the snapshot and is separately reported
    by the check below. It is never silently swallowed.
    """
    try:
        return render_fn(**kwargs)
    except Exception as exc:                                     # noqa: BLE001
        _RENDER_ERRORS.append(f"{_variant_id(kwargs)}: "
                              f"{type(exc).__name__}: {exc}")
        return f"<render raised {type(exc).__name__}: {exc}>"


def _digests(render_fn) -> dict:
    """{variant id: sha256 of the rendered system prompt} for the whole matrix.

    Takes the render function as an argument so the controls below can drive
    this exact code path with a doctored renderer. A comparison harness that
    can only be run one way is a comparison harness whose failure path has
    never executed.
    """
    return {_variant_id(kw): prompt_sha256(_render(render_fn, kw))
            for kw in _matrix()}


_LIVE_DIGESTS = _digests(render_system_prompt)

check("every variant rendered without raising",
      _RENDER_ERRORS, [])
check("the matrix rendered every variant",
      len(_LIVE_DIGESTS), len(_matrix()))
check("every digest is a 64-character hex sha256 (non-degeneracy: an empty "
      "render would still produce a digest, but not this one)",
      sorted({len(d) for d in _LIVE_DIGESTS.values()}), [64])
check("the rendered prompts are non-empty",
      min(len(_render(render_system_prompt, kw)) for kw in _matrix()) > 1000,
      True)


# ===========================================================================
# SECTION 3 -- THE SNAPSHOT
# ===========================================================================

print("\n" + "=" * 78)
print("SECTION 3 -- the golden snapshot")
print("=" * 78)

# The parameter list is snapshotted alongside the digests, so a signature
# REORDER -- which changes no rendered byte and would therefore pass every
# digest comparison -- still requires a deliberate regeneration. Section 1
# already fails on an added or removed parameter; this covers the third case.
_LIVE_SNAPSHOT = {
    "prompt_version": PROMPT_VERSION,
    "render_parameters": list(_SIG_PARAMS),
    "digests": dict(sorted(_LIVE_DIGESTS.items())),
}


def _serialize(snapshot: dict) -> str:
    """The exact bytes of the golden file.

    sort_keys and a trailing newline so two regenerations of unchanged code
    produce a byte-identical file; the digests dict is pre-sorted above for the
    same reason.
    """
    return json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if _UPDATE_SNAPSHOT:
    os.makedirs(os.path.dirname(_SNAPSHOT_PATH), exist_ok=True)
    with open(_SNAPSHOT_PATH, "w", encoding="utf-8") as _fh:
        _fh.write(_serialize(_LIVE_SNAPSHOT))
    print(f"  [--update-snapshot] wrote {_SNAPSHOT_PATH}")

check("the snapshot file exists (regenerate with --update-snapshot)",
      os.path.isfile(_SNAPSHOT_PATH), True)

if os.path.isfile(_SNAPSHOT_PATH):
    _SNAP = json.loads(open(_SNAPSHOT_PATH, encoding="utf-8").read())
else:
    _SNAP = {"prompt_version": None, "render_parameters": [], "digests": {}}

check("the snapshot declares the three fields this guard reads",
      sorted(_SNAP), ["digests", "prompt_version", "render_parameters"])
check("the snapshot is non-degenerate: it records more than one digest",
      len(_SNAP.get("digests") or {}) > 1, True)


# ===========================================================================
# SECTION 4 -- THE COMPARISON, AS A PURE FUNCTION
# ===========================================================================
#
# evaluate() is separated from the live data so the controls in Section 6 can
# drive it with doctored inputs and observe which mode it reports. A comparison
# written inline against the live values can only ever be run in the state
# where it passes.

_MODE_UNBUMPED = "template-edited-without-bumping"
_MODE_VERSION_MOVED = "version-moved"
_MODE_VARIANT_SET = "variant-set-changed"
_MODE_SIGNATURE = "signature-changed"

_FIX_UPDATE = ("regenerate with `python tests/test_agent_prompt_version.py "
               "--update-snapshot`")


def evaluate(live_version, live_params, live_digests, snapshot):
    """Return [(mode, message)] for every disagreement with the snapshot.

    ORDER MATTERS AND IS THE WHOLE DESIGN. The version is compared FIRST,
    because it decides what a moved digest MEANS. With the version unchanged, a
    moved digest is an unrecorded template edit and the fix is in prompts.py.
    With the version moved, the same moved digest is the expected consequence
    of a deliberate bump and the fix is in the snapshot -- reporting it as
    "you forgot to bump" would send the reader to change a version they have
    just changed.
    """
    problems = []
    snap_version = snapshot.get("prompt_version")
    snap_digests = snapshot.get("digests") or {}
    snap_params = list(snapshot.get("render_parameters") or [])

    if list(live_params) != snap_params:
        problems.append((_MODE_SIGNATURE, (
            f"render_system_prompt's parameters are {list(live_params)}; the "
            f"snapshot records {snap_params}. The rendered text may not have "
            f"moved at all -- a reorder does not -- but the matrix this guard "
            f"drives has. Review the change, then {_FIX_UPDATE}.")))

    missing = sorted(set(snap_digests) - set(live_digests))
    added = sorted(set(live_digests) - set(snap_digests))
    if missing or added:
        problems.append((_MODE_VARIANT_SET, (
            f"the variant matrix no longer matches the snapshot: "
            f"{len(missing)} recorded variant(s) are no longer rendered "
            f"{missing[:3]}, {len(added)} new variant(s) are "
            f"{added[:3]}. A skip reason added to oncotriage/agent/state.py "
            f"does this. Confirm the new matrix is what you meant, then "
            f"{_FIX_UPDATE}.")))

    moved = sorted(k for k in set(live_digests) & set(snap_digests)
                   if live_digests[k] != snap_digests[k])

    if live_version != snap_version:
        problems.append((_MODE_VERSION_MOVED, (
            f"PROMPT_VERSION is {live_version!r}; the snapshot records "
            f"{snap_version!r}. A version bump is a deliberate act and the "
            f"snapshot has to record it deliberately too: {_FIX_UPDATE}. "
            f"({len(moved)} of {len(snap_digests)} digests also moved, which "
            f"is expected when the bump accompanied a wording change and is "
            f"NOT reported as an unbumped edit.)")))
        return problems

    for key in moved:
        problems.append((_MODE_UNBUMPED, (
            f"the rendered system prompt changed for variant {key} "
            f"({snap_digests[key][:16]} -> {live_digests[key][:16]}) while "
            f"PROMPT_VERSION stayed {live_version!r}. THE TEMPLATE WAS EDITED "
            f"WITHOUT BUMPING THE VERSION, so two runs would store the same "
            f"version against different prompts. Fix: bump PROMPT_VERSION in "
            f"oncotriage/agent/prompts.py (middle number if the meaning "
            f"changed, last if it cannot have), then {_FIX_UPDATE}.")))

    return problems


print("\n" + "=" * 78)
print("SECTION 4 -- the shipped template against the snapshot")
print("=" * 78)

_LIVE_PROBLEMS = evaluate(PROMPT_VERSION, _SIG_PARAMS, _LIVE_DIGESTS, _SNAP)

# NON-DEGENERATE FIRST. An empty matrix compared against an empty snapshot
# agrees perfectly, so the agreement below is only worth reading once the
# comparison is known to have had something to compare.
check("the comparison ran over the whole matrix and the snapshot is not empty",
      (len(_LIVE_DIGESTS) == len(_matrix()) > 1,
       len(_SNAP.get("digests") or {}) > 1),
      (True, True))

# ONE recorded outcome, carrying the MODES; the messages are detail beneath it.
# Recording one failure per variant would make the same defect report 1 or 16
# depending on how many skip reasons state.py happens to declare.
check("the shipped Stage 5 system prompt agrees with the golden snapshot",
      sorted({_mode for _mode, _ in _LIVE_PROBLEMS}), [])
for _mode, _message in _LIVE_PROBLEMS:
    detail(f"[{_mode}] {_message}")


# ===========================================================================
# SECTION 5 -- WHAT THE MATRIX ITSELF PROVES
# ===========================================================================
#
# Two properties of the template that no single digest can express, and that a
# reader would otherwise have to take on trust from a comment.

print("\n" + "=" * 78)
print("SECTION 5 -- properties the matrix proves about the template")
print("=" * 78)

# Bucketed by every axis EXCEPT the skip reason, which is what the two checks
# below are about. The key was `trial_count` alone until PROMPT_VERSION 1.6.0
# replaced that axis with `patient_record`.
_confirmed_by_count = {}
_unconfirmed_by_count = {}
for _kw in _matrix():
    _bucket = (_confirmed_by_count if _kw["mesh_filter_applied"]
               else _unconfirmed_by_count)
    _bucket.setdefault(_kw["patient_record"], set()).add(
        _LIVE_DIGESTS[_variant_id(_kw)])

# The confirmed branch never interpolates the skip reason, so every reason must
# render the same bytes for a given patient record. This is what makes the reason
# column of the confirmed rows in the snapshot redundant BY PROOF rather than
# by assertion.
check("confirmed branch: the skip reason is unread, so one digest per "
      "patient record",
      sorted({len(v) for v in _confirmed_by_count.values()}), [1])

# The unconfirmed branch DOES interpolate it, so the same test must come out
# the other way -- otherwise "one digest per trial count" above would also be
# satisfied by a renderer that ignores its arguments entirely.
check("unconfirmed branch: the skip reason IS interpolated, so one digest per "
      "reason (the non-degeneracy control on the check above)",
      sorted({len(v) for v in _unconfirmed_by_count.values()}),
      [len(_REASONS)])

# And the record axis has to move something, or the patient record is not in
# this message at all -- which is the whole of what 1.6.0 changed, so a matrix
# that could not see it would be pinning the wrong prompt. This REPLACES the
# identical check the deleted trial_count axis carried; the subject moved, the
# property did not. Flattened rather than `next(iter(v))` per bucket: that form
# raises StopIteration on an empty bucket, which is an abort where a failure is
# owed -- the same family as the guarded render above.
check("the patient record reaches the rendered text",
      len({d for v in _confirmed_by_count.values() for d in v}),
      len(_AXES["patient_record"]))
# ...and it reaches it VERBATIM rather than as a digest of itself, which no
# comparison of hashes could establish.
check("...verbatim, in both Section 2 variants",
      sorted({_AXES["patient_record"][1] in _render(render_system_prompt, _kw)
              for _kw in _matrix()
              if _kw["patient_record"] == _AXES["patient_record"][1]}),
      [True])

check("the two branches never render the same bytes",
      set().union(*_confirmed_by_count.values())
      & set().union(*_unconfirmed_by_count.values()), set())


# ===========================================================================
# SECTION 5b -- THE PINNED REINFORCEMENTS, AND THE HEADINGS STAY UNIQUE
# ===========================================================================
#
# A DIGEST SAYS THE BYTES MOVED; IT CANNOT SAY WHAT THEY SAY. Section 4 would be
# equally satisfied by a template whose 1.7.0 wording had been deleted and the
# snapshot regenerated -- the version would still read 1.7.0, every digest would
# still agree with the golden file, and the reinforcement this bump exists for
# would be gone with nothing reporting it. So the wording is pinned by CONTENT
# and by PLACE, in every variant.
#
# PLACE, not only presence, and that is the whole finding behind 1.7.0. RULE 4
# and C4 were already in the message and were already being broken; what the
# bump does is restate them at the moment a rejecting status is written. A check
# that only asked "is this sentence somewhere in the prompt" would pass over an
# edit that hoisted the block back up beside the rules it restates, which is
# precisely the arrangement measured NOT to work.
#
# 1.9.0's TWO EDITS ARE PINNED BY THE SAME MACHINERY, and the tuple is what makes
# that free rather than a second copy of these helpers: every check below --
# present exactly once, under the right heading, in EVERY variant, plus the three
# firing controls (a missing needle, a stripped needle, a hoisted needle) -- is a
# function of `_REINFORCEMENT` and therefore covers each entry the moment it is
# added. 1.9.0's finding is the same finding a third time: the model was told the
# interval EXISTS (1.8.0, a standing fact under RULE 4) and still did not use it,
# so the bump says the interval DECIDES, at the point of classification and again
# in the last thing the model reads. Presence alone would pass over an edit that
# demoted either back to prose somewhere else in the message.
#
# THE HEADINGS ARE DERIVED FROM THE RENDERED TEXT, never listed here. A section
# added tomorrow is covered without an edit, and one removed cannot leave this
# scan quietly covering less than it claims. Their uniqueness is not cosmetic:
# oncotriage/evaluation/rater.py slices the rater's rubric out of this prompt by
# marker and REFUSES on a marker that occurs twice, and
# tests/test_agent_structured_outputs.py locates the JSON template the same way.

print("\n" + "=" * 78)
print("SECTION 5b -- the 1.7.0 reinforcement, and heading uniqueness")
print("=" * 78)

# Pinned as a literal, and the duplication with the snapshot is deliberate. The
# snapshot records whatever version was current when somebody last regenerated
# it, so it agrees with a careless regeneration by construction; a literal here
# is a second place a human has to consent to a bump. It is the only line in
# this file a future bump must edit, and that is the cost being paid on purpose.
check("PROMPT_VERSION reads 1.9.0", PROMPT_VERSION, "1.9.0")

# 1.8.0 IS PINNED HERE TOO, AND ITS ADDITION IS INSIDE A DIFFERENT SECTION.
# Section 5b's scan below is about the pinned sentences and where they landed;
# 1.8.0's addition sits under RULE 4, inside SECTION 3, and the same
# heading-uniqueness machinery is what makes that assertable. Its own
# under-the-right-heading check lives in
# tests/test_agent_summary_temporal_tagging.py section 12, beside the renderer
# it describes -- the record and the rule are one change and are tested as one.
_180_RENDERS = [_render(render_system_prompt, _kw) for _kw in _matrix()]
check("1.8.0's RULE 4 addition is present exactly once in every variant",
      sorted({t.count("ELAPSED TIME IS STATED FOR YOU.") for t in _180_RENDERS}),
      [1])
check("...and the imperative it replaced is gone from every variant",
      sorted({"If event end date is known: calculate elapsed time." in t
              for t in _180_RENDERS}), [False])
check("...over a non-degenerate matrix", len(_180_RENDERS) > 1, True)

# EVERY SUPERSEDED TIME-WINDOW BRANCH, PINNED ABSENT. A template that carried BOTH
# 1.8.0's branch and 1.9.0's would satisfy every presence check below and would
# hand the model two instructions for one decision -- prefer-the-stated-interval
# beside quote-the-stated-interval, keyed on different facts (the event's END
# DATE versus the INTERVAL). That is not a hypothetical shape: it is what a merge
# resolved the wrong way produces, and a digest cannot report it because the
# digest moved for the addition either way. Both clauses of 1.8.0's branch are
# named, not just the one 1.9.0's wording most resembles.
_SUPERSEDED_TIME_WINDOW = (
    "use the elapsed time the record states beside it",     # 1.8.0, first line
    "If event end date is unknown:",                        # 1.8.0, second line
    "If event end date is known:",                          # pre-1.8.0 and 1.8.0
)
check("every superseded time-window clause is gone from every variant",
      sorted({n for t in _180_RENDERS for n in _SUPERSEDED_TIME_WINDOW
              if n in t}), [])
check("...and that scan can report one: 1.8.0's own branch text pasted back "
      "into a rendered copy is found",
      sorted({n for n in _SUPERSEDED_TIME_WINDOW
              if n in _180_RENDERS[0]
              + "\n    If event end date is known: use the elapsed time the "
                "record states beside it, or calculate it if none is stated."
              + "\n    If event end date is unknown: classification = "
                '"not_evaluable"'}),
      sorted(_SUPERSEDED_TIME_WINDOW))

# The sentences a bump added BECAUSE OF WHERE THEY SIT, and the section each must
# land in. The section is identified by the banner heading it follows, so "at the
# disqualification point" is asserted rather than described.
#
# ONE TUPLE ACROSS VERSIONS, DELIBERATELY. Every check and every control below is
# a function of this tuple, so an entry added here is covered by all of them at
# once and none of them can quietly cover less than it claims. Each entry must
# occupy A WHOLE LINE of the rendered template and no two may share a line -- the
# strip control counts removed lines against len(_REINFORCEMENT).
_REINFORCEMENT = (
    # 1.7.0 -- RULE 4 and C4 restated at the moment a rejecting status is written.
    ('BEFORE YOU WRITE "not_met" OR "violated" ON ANY CRITERION, CHECK TWO '
     'THINGS:', "SECTION 5 -- OUTPUT FORMAT"),
    ("ACTIVITY (RULE 4).", "SECTION 5 -- OUTPUT FORMAT"),
    ("ISOLATION (C4).", "SECTION 5 -- OUTPUT FORMAT"),
    ("Evidence that a condition is RESOLVED, inactive or in remission does not "
     "contradict a criterion requiring an active or current one.",
     "FINAL REMINDER"),
    ('A trial is never "not_eligible" because another trial in this message '
     'was.', "FINAL REMINDER"),
    # 1.9.0 -- quote-before-judge. The first is the decision point itself: RULE 4
    # rides under SECTION 3, which is also the span oncotriage/evaluation/rater.py
    # lifts into the independent rater's rubric, so this entry is what says the
    # rater judges time windows under the same rule the classifier does. The
    # second is the last thing the model reads before it writes, and lies OUTSIDE
    # every lifted span -- the asymmetry is 1.7.0's precedent, and
    # tests/test_evaluation_rater.py section 7d is where it is asserted.
    ("Quote the record's stated interval for that event verbatim in "
     "patient_value", "SECTION 3 -- CRITERION EVALUATION ORDER"),
    ("For any time-window criterion: the record's stated interval, quoted "
     "verbatim, decides it.", "FINAL REMINDER"),
)

# A needle this file invented, which the template must NOT contain. Without it
# "every needle is present" is also satisfied by a comparison that has stopped
# looking -- an `in` test against a substring of itself, or a helper returning
# True unconditionally.
_ABSENT_NEEDLE = ("BEFORE YOU WRITE THIS SENTENCE THE TEMPLATE HAS STOPPED "
                  "BEING THE SUBJECT OF THIS CHECK")


def _placements(text: str) -> dict:
    """{needle: heading it falls under} for every pinned needle in `text`.

    ``"<absent>"`` when the needle is not there and ``"<before any heading>"``
    when it precedes every banner, so a defect produces a readable value rather
    than a KeyError or a -1 index quietly comparing equal to something.
    """
    heads = [(text.index(h), h) for h in _banner_headings(text) if h in text]
    out = {}
    for needle, _expected in _REINFORCEMENT:
        if needle not in text:
            out[needle] = "<absent>"
            continue
        at = text.index(needle)
        prior = [h for pos, h in heads if pos < at]
        out[needle] = prior[-1] if prior else "<before any heading>"
    return out


def _banner_headings(text: str) -> list:
    """Every section banner heading, DERIVED: a line fenced by two '=' rules.

    Returns them in document order. Written as a function of the rendered text
    rather than as a constant so it cannot disagree with the template, and so
    the non-degeneracy check below has something real to count.
    """
    lines = text.split("\n")
    out = []
    for i in range(1, len(lines) - 1):
        prev, here, nxt = (lines[i - 1].strip(), lines[i].strip(),
                           lines[i + 1].strip())
        if (prev and set(prev) == {"="} and nxt and set(nxt) == {"="}
                and here and set(here) != {"="}):
            out.append(here)
    return out


def _reinforcement_findings(text: str) -> list:
    """Every way `text` fails to carry the pinned reinforcements. [] if it does."""
    found = []
    placed = _placements(text)
    for needle, expected in _REINFORCEMENT:
        n = text.count(needle)
        if n != 1:
            found.append(f"{needle[:40]!r} occurs {n} times, expected 1")
        elif placed[needle] != expected:
            found.append(f"{needle[:40]!r} is under {placed[needle]!r}, "
                         f"expected {expected!r}")
    return found


def _duplicate_headings(text: str) -> list:
    """Every banner heading that does not occur exactly once in `text`."""
    return sorted({f"{h!r} x{text.count(h)}"
                   for h in _banner_headings(text) if text.count(h) != 1})


_ALL_RENDERS = {_variant_id(kw): _render(render_system_prompt, kw)
                for kw in _matrix()}

check("non-degeneracy: the heading scan found the banners at all, in every "
      "variant (a derivation that found none would pass every uniqueness "
      "check below for free)",
      sorted({len(_banner_headings(t)) for t in _ALL_RENDERS.values()}), [10])
check("...and the two Section 2 variants declare the SAME headings, so neither "
      "branch carries a section the other lacks",
      len({tuple(_banner_headings(t)) for t in _ALL_RENDERS.values()}), 1)

check("no section heading occurs more than once in any rendered variant",
      sorted({m for t in _ALL_RENDERS.values() for m in _duplicate_headings(t)}),
      [])
check("...and that scan can report a duplicate: one heading pasted twice into "
      "a rendered copy is found",
      _duplicate_headings(sorted(_ALL_RENDERS.values())[0]
                          + "\n" + "=" * 69 + "\nFINAL REMINDER\n" + "=" * 69),
      ["'FINAL REMINDER' x2"])

check("every pinned sentence is present exactly once, and under the right "
      "heading, in EVERY variant (both Section 2 branches)",
      sorted({f for t in _ALL_RENDERS.values()
              for f in _reinforcement_findings(t)}), [])
check("...covering both branches rather than one of them (non-degeneracy on "
      "the check above)",
      (len(_ALL_RENDERS), len({kw["mesh_filter_applied"] for kw in _matrix()})),
      (len(_matrix()), 2))
check("non-degeneracy: a needle this file invented is NOT in the template, so "
      "the presence test is a test",
      sorted({_ABSENT_NEEDLE in t for t in _ALL_RENDERS.values()}), [False])
check("...and _reinforcement_findings reports a MISSING needle rather than "
      "returning empty for anything handed to it",
      _reinforcement_findings("nothing here at all"),
      [f"{n[:40]!r} occurs 0 times, expected 1" for n, _ in _REINFORCEMENT])
# PLACEMENT IS SEPARABLE FROM PRESENCE, and this is what says so: the same five
# sentences MOVED to the end of a rendered prompt are all present, exactly once
# each, and all under the wrong heading. Without this, "at the disqualification
# point" would be prose in a comment rather than a property under test.
_ONE_RENDER = sorted(_ALL_RENDERS.values())[0]
_STRIPPED = "\n".join(ln for ln in _ONE_RENDER.split("\n")
                      if not any(n in ln for n, _ in _REINFORCEMENT))
# NEITHER EXPRESSION MAY INDEX A LIST A DEFECT CAN SHORTEN. The first draft read
# `_reinforcement_findings(_STRIPPED)[i]` over `range(5)` and split on a
# separator only the "occurs" branch emits -- two IndexErrors at module level,
# either of which would abort the file on exactly the run it owes a summary.
# `split(sep, 1)[-1]` is total over any string, and the length is a value rather
# than an index.
_STRIP_FINDINGS = _reinforcement_findings(_STRIPPED)
# THE LINE COUNT IS DERIVED, NOT REMEMBERED. It read a literal 5 while the tuple
# held five entries, so adding 1.9.0's two would have failed here for arithmetic
# rather than for anything about the template. len(_REINFORCEMENT) is the same
# assertion -- one whole line per needle, no two sharing a line -- and it cannot
# go stale when the tuple grows.
check("non-degeneracy: each needle occupies a whole line of its own, so "
      "stripping them removes exactly len(_REINFORCEMENT) lines and nothing else",
      (len(_ONE_RENDER.split("\n")) - len(_STRIPPED.split("\n")),
       len(_STRIP_FINDINGS),
       sorted({f.split(" occurs ", 1)[-1] for f in _STRIP_FINDINGS})),
      (len(_REINFORCEMENT), len(_REINFORCEMENT), ["0 times, expected 1"]))
_HOISTED = (_STRIPPED + "\n" + "=" * 69 + "\nAPPENDED\n" + "=" * 69 + "\n"
            + "\n".join(n for n, _ in _REINFORCEMENT))
check("...and a needle in the wrong section is reported as misplaced, not as "
      "present",
      sorted(f.split(" is under ")[1] for f in _reinforcement_findings(_HOISTED)
             if " is under " in f),
      sorted(f"'APPENDED', expected {e!r}" for _, e in _REINFORCEMENT))


# ===========================================================================
# SECTION 6 -- THE CONTROLS
# ===========================================================================
#
# Every assertion above must be shown to FAIL when the thing it checks is
# broken. Three controls doctor the INPUTS to evaluate(); the fourth doctors
# the TEMPLATE ITSELF, which is the only one that can establish that these
# digests are computed over the shipped file rather than over something this
# test made up.

print("\n" + "=" * 78)
print("SECTION 6 -- the controls")
print("=" * 78)

_GOOD_SNAPSHOT = json.loads(_serialize(_LIVE_SNAPSHOT))   # a real round trip

# --- 6a: unpatched, the comparison passes ---------------------------------
check("6a  positive control: the live values against a snapshot of themselves "
      "report nothing",
      evaluate(PROMPT_VERSION, _SIG_PARAMS, _LIVE_DIGESTS, _GOOD_SNAPSHOT), [])

# --- 6b: a digest moves, the version does not -> mode A --------------------
_one_key = sorted(_LIVE_DIGESTS)[0]
_bent = dict(_LIVE_DIGESTS)
_bent[_one_key] = _bent[_one_key][:-1] + ("0" if _bent[_one_key][-1] != "0" else "1")
_p6b = evaluate(PROMPT_VERSION, _SIG_PARAMS, _bent, _GOOD_SNAPSHOT)
check("6b  a moved digest under an unchanged version is reported, once",
      [m for m, _ in _p6b], [_MODE_UNBUMPED])
check("6b  ...and the message names the fix: bump PROMPT_VERSION",
      "bump PROMPT_VERSION in oncotriage/agent/prompts.py" in _p6b[0][1]
      if _p6b else False, True)
check("6b  ...and names the update flag",
      "--update-snapshot" in _p6b[0][1] if _p6b else False, True)

# --- 6c: the version moves -> mode B, and NOT mode A -----------------------
_p6c = evaluate("9.9.9", _SIG_PARAMS, _bent, _GOOD_SNAPSHOT)
check("6c  a moved version is reported as its own mode",
      [m for m, _ in _p6c], [_MODE_VERSION_MOVED])
check("6c  ...and a digest that moved WITH it is not also reported as an "
      "unbumped edit",
      _MODE_UNBUMPED in [m for m, _ in _p6c], False)
check("6c  ...and the message says to regenerate the snapshot",
      "--update-snapshot" in _p6c[0][1] if _p6c else False, True)

# --- 6d: the variant set changes ------------------------------------------
_short = {k: v for k, v in list(_LIVE_DIGESTS.items())[1:]}
check("6d  a variant that disappeared from the matrix is reported",
      _MODE_VARIANT_SET in
      [m for m, _ in evaluate(PROMPT_VERSION, _SIG_PARAMS, _short, _GOOD_SNAPSHOT)],
      True)

# --- 6e: the signature changes without moving a byte -----------------------
_reordered = dict(_GOOD_SNAPSHOT)
_reordered["render_parameters"] = list(reversed(_GOOD_SNAPSHOT["render_parameters"]))
check("6e  a parameter REORDER, which moves no rendered byte, is still "
      "reported",
      [m for m, _ in evaluate(PROMPT_VERSION, _SIG_PARAMS, _LIVE_DIGESTS,
                              _reordered)],
      [_MODE_SIGNATURE])

# --- 6f: THE TEMPLATE ITSELF, patched by one character ---------------------
#
# The three controls above prove the COMPARISON discriminates. This one proves
# its SUBJECT is right: that the digests come from the template in
# oncotriage/agent/prompts.py and would move if a single character of it did.
# Without it, every check in this file would still pass against a render
# function that had been quietly disconnected from the shipped text.
#
# A PATCHED IN-MEMORY COPY, never an edit to the file. That is this project's
# stated preference and it is also what keeps this test out of the collision
# matrix -- nothing on disk is written, so no concurrent run can be corrupted
# by it. The file is hashed before and after anyway, with a non-degeneracy
# probe, because "the restore was byte-identical" is worthless if both sides of
# the comparison are the same read.

_PROMPTS_SRC = open(_PROMPTS_PATH, encoding="utf-8").read()
_SHA_BEFORE = hashlib.sha256(_PROMPTS_SRC.encode("utf-8")).hexdigest()

# One character, inside the template, in a line every variant renders. Asserted
# unique first: a needle occurring twice would patch a place this test did not
# choose, and a needle occurring zero times would make the control a no-op that
# reports success.
_NEEDLE = "You are a clinical trial pre-screening classifier."
check("6f  the patch site occurs exactly once in prompts.py",
      _PROMPTS_SRC.count(_NEEDLE), 1)
check("6f  ...and it is a line every variant renders (non-degeneracy)",
      all(_NEEDLE in _render(render_system_prompt, kw) for kw in _matrix()),
      True)

_PATCHED_SRC = _PROMPTS_SRC.replace(_NEEDLE, _NEEDLE[:-1] + "!", 1)
check("6f  the patch changes exactly one character",
      (len(_PATCHED_SRC) == len(_PROMPTS_SRC),
       sum(1 for a, b in zip(_PATCHED_SRC, _PROMPTS_SRC) if a != b)),
      (True, 1))

_patched_ns = {"__name__": "oncotriage.agent._prompts_patched_copy",
               "__file__": _PROMPTS_PATH}
exec(compile(_PATCHED_SRC, "<one-character-patched copy of prompts.py>",
             "exec"), _patched_ns)
_patched_render = _patched_ns["render_system_prompt"]

check("6f  the patched copy still renders (the control is a real prompt, not "
      "a traceback)",
      len(_render(_patched_render, _matrix()[0])) > 1000, True)

_PATCHED_DIGESTS = _digests(_patched_render)
check("6f  every variant's digest moved under the one-character patch",
      sorted(k for k in _LIVE_DIGESTS
             if _LIVE_DIGESTS[k] != _PATCHED_DIGESTS.get(k)),
      sorted(_LIVE_DIGESTS))

_p6f = evaluate(_patched_ns["PROMPT_VERSION"], _SIG_PARAMS, _PATCHED_DIGESTS,
                _GOOD_SNAPSHOT)
check("6f  ...and the guard reports every one of them as an unbumped edit",
      sorted({m for m, _ in _p6f}), [_MODE_UNBUMPED])
check("6f  ...one finding per variant, none swallowed",
      len(_p6f), len(_LIVE_DIGESTS))

# The unpatched half of the control, run AFTER the patched half so it also
# proves the exec did not disturb the live module.
check("6f  unpatched, the same comparison reports nothing (the other half of "
      "the control)",
      evaluate(PROMPT_VERSION, _SIG_PARAMS, _digests(render_system_prompt),
               _GOOD_SNAPSHOT), [])

# --- 6g: THE TEMPLATE WITH EVERY PINNED REINFORCEMENT REMOVED --------------
#
# Section 5b's controls doctor a rendered STRING, which shows the scans
# discriminate. This one shows their SUBJECT is right, the same way 6f does for
# the digests: every pinned sentence is deleted from an in-memory copy of
# prompts.py and every Section 5b finding must fire against the copy while the
# shipped module still reports clean. Without it, Section 5b would pass against
# a template it had been quietly disconnected from.
#
# The strip is LINE-BASED and its extent is asserted, because a needle that also
# appeared in the changelog comment above PROMPT_VERSION would delete a line
# this control did not choose -- and a control that removes the wrong thing
# fails, which looks exactly like a control that is working. THAT IS A LIVE
# CONSTRAINT ON THE CHANGELOG, not a hypothetical: 1.9.0's changelog argues about
# both of its own additions at length, and this check is what forced it to argue
# about them in different words than the template uses.

_STRIPPED_SRC = "\n".join(
    line for line in _PROMPTS_SRC.split("\n")
    if not any(needle in line for needle, _ in _REINFORCEMENT))
check("6g  the strip removed exactly one source line per pinned sentence and "
      "nothing else",
      (len(_PROMPTS_SRC.split("\n")) - len(_STRIPPED_SRC.split("\n")),
       _STRIPPED_SRC.count("PROMPT_VERSION = ")),
      (len(_REINFORCEMENT), _PROMPTS_SRC.count("PROMPT_VERSION = ")))

_stripped_ns = {"__name__": "oncotriage.agent._prompts_stripped_copy",
                "__file__": _PROMPTS_PATH}
exec(compile(_STRIPPED_SRC, "<1.7.0-reinforcement-stripped copy of prompts.py>",
             "exec"), _stripped_ns)
_stripped_render = _stripped_ns["render_system_prompt"]

_STRIPPED_RENDERS = [_render(_stripped_render, kw) for kw in _matrix()]
check("6g  the stripped copy still renders (the control is a real prompt, not "
      "a traceback)",
      min(len(t) for t in _STRIPPED_RENDERS) > 1000, True)
check("6g  ...and it is genuinely shorter than the shipped one, variant for "
      "variant (non-degeneracy: an unchanged copy would make 6g vacuous)",
      sorted({len(a) > len(b) for a, b in
              zip([_ALL_RENDERS[_variant_id(kw)] for kw in _matrix()],
                  _STRIPPED_RENDERS)}),
      [True])
check("6g  EVERY variant of the stripped copy is reported as missing every "
      "pinned sentence",
      sorted({len(_reinforcement_findings(t)) for t in _STRIPPED_RENDERS}),
      [len(_REINFORCEMENT)])
# The two scans are INDEPENDENT: deleting the reinforcement must not disturb the
# heading structure, or a future failure could not be read as one thing or the
# other.
check("6g  ...while its headings are untouched and still unique",
      sorted({m for t in _STRIPPED_RENDERS for m in _duplicate_headings(t)}),
      [])
check("6g  unstripped, the same scan reports nothing (the other half of the "
      "control)",
      sorted({f for kw in _matrix()
              for f in _reinforcement_findings(
                  _render(render_system_prompt, kw))}),
      [])

_SHA_AFTER = hashlib.sha256(
    open(_PROMPTS_PATH, encoding="utf-8").read().encode("utf-8")).hexdigest()
check("6f  prompts.py on disk is byte-identical (nothing was written)",
      _SHA_AFTER, _SHA_BEFORE)
check("6f  ...and that comparison is not a tautology: the file is non-empty "
      "and was re-read from disk",
      len(_PROMPTS_SRC) > 1000 and _SHA_BEFORE != hashlib.sha256(b"").hexdigest(),
      True)

# _RENDER_ERRORS was asserted empty in Section 2 over the LIVE renders only;
# every render since -- including the patched copy's sixteen -- appends to the
# same list, so re-asserting it here is what stops a raise in the control being
# swallowed by the guard that exists to stop a raise aborting the file.
check("6f  cumulative: no render anywhere in this file raised",
      _RENDER_ERRORS, [])


# ===========================================================================
# SECTION 7 -- REGENERATION IS DETERMINISTIC
# ===========================================================================
#
# --update-snapshot has to produce the SAME bytes twice for unchanged code, or
# every regeneration is a diff and the golden file stops being reviewable.
# Checked by serializing twice rather than by writing twice: writing is what
# the flag does, and this file must not write outside the flag.

print("\n" + "=" * 78)
print("SECTION 7 -- the snapshot serializes deterministically")
print("=" * 78)

check("two serializations of the same snapshot are byte-identical",
      _serialize(_LIVE_SNAPSHOT), _serialize(json.loads(_serialize(_LIVE_SNAPSHOT))))
check("the snapshot ends with exactly one trailing newline",
      _serialize(_LIVE_SNAPSHOT).endswith("}\n"), True)

# THE ON-DISK COMPARISON IS ASSERTED IN BOTH STATES rather than skipped in one.
# A `... if not _LIVE_PROBLEMS else True` guard here would be a check that
# passes for free on exactly the runs where something is wrong -- the shape
# this project treats as a check that has stopped checking. So: with the guard
# green the bytes on disk must equal what --update-snapshot would write, and
# with it red they must NOT, which independently corroborates that the findings
# above are real rather than an artefact of how the live values were built.
_ON_DISK = (open(_SNAPSHOT_PATH, encoding="utf-8").read()
            if os.path.isfile(_SNAPSHOT_PATH) else None)
check("the golden file is current when the guard is green, and stale when it "
      "is red -- never the other way round",
      (_serialize(_LIVE_SNAPSHOT) == _ON_DISK) if _ON_DISK is not None else None,
      not _LIVE_PROBLEMS)


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")
print(f"Runtime: {time.time() - _T_START:.2f}s")

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
Created on Sun Aug  9 09:00:00 2026

@author: ramyalsaffar
"""
