# Every Counter in the Package Has a Production Reader
######################################################

"""A counter with no reader looks like coverage. This is what says otherwise.

THE CLASS OF DEFECT. ``oncotriage/degradation.py`` exists because sixteen
module-level counters were written carefully and read by nothing at the end of a
run. That pass built the registry; it did not build anything that would notice
the NEXT counter added without a reader, and by the time of the counter-reader
audit there were nine more -- two of them
(``MARKDOWN_ESCAPE_DECODE_UNRESOLVED``, ``ESCAPED_ENTITY_DECODE_UNRESOLVED``)
added by the pass immediately before it.

WHY NO EXISTING CHECK COULD SEE THEM, and it is worth stating because the
obvious answer is wrong. ``tests/test_package_invariants.py`` check 2h reports a
module-level name that is declared and never READ -- but ``C[key] += 1`` is an
``ast.Name`` in LOAD context (the STORE is on the enclosing ``Subscript``), so
every increment reads as a read and a write-only counter satisfies 2h on its
first use. Widening 2h is the wrong fix: its subject is dead declarations and
this one's is live declarations with a dead audience.

WHAT SECTION 1 ACTUALLY ASSERTS. For every module-level ``Counter()`` in the
package, in BOTH declaration forms -- the plain ``NAME = Counter()`` and the
annotated ``NAME: Dict[str, int] = Counter()``, which is the form the two
counters above use and the form the first version of the audit script MISSED --
one of three things must hold:

    (a) it is in ``degradation._REGISTRY`` (named in ``_REGISTRY_SPEC``, or
        added at its owner's module scope through ``register()``);
    (b) it is in ``degradation._CENSUS``;
    (c) it is in ``_READER_EXEMPTIONS`` below, which names the PRODUCTION FILE
        that reads it -- and that file is then checked, by AST, to contain a
        genuine READ of that name.

(c) is the half that makes this more than a list. An exemption whose named
reader has been deleted or renamed FAILS, so the table cannot rot into a
permission slip.

TEST FILES ARE NOT READERS, and that is the whole point of the audit that
produced this file: both decode counters had four test files reading them and
still nothing an operator could see. ``tests/`` is excluded from the read
corpus by construction below.

WHAT IT COSTS: nothing. No network, no keys, no spend, no live Qdrant, no live
server, no model load, no corpus, no git history. The one database is a temp
file in a directory this file removes and then asserts gone, and
``paths._RESOLVED`` is seeded so nothing can resolve to the production tree.

IT EXECS NOTHING, so it needs no ``_EXEC_ALLOWLIST`` entry. Every control is a
different INPUT to a function of its argument, an ``ast`` walk over an
in-memory copy, or a registry entry removed inside ``try``/``finally`` with the
restore asserted. It writes nothing in the repository -- the four package files
it reads are sha256-compared at the end -- so it is NOT in the collision
matrix, which is derived rather than declared: none of the four is written by
either of the suite's two writers.

Run from terminal:
    python tests/test_degradation_counter_readers.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""

import ast
import hashlib
import io
import os
import sqlite3
import sys
import tempfile
import shutil
from contextlib import redirect_stderr

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

from oncotriage import degradation
from oncotriage import paths
from oncotriage.ablation import study as _study
from oncotriage.agent import evaluation as _evaluation
from oncotriage.agent import patient as _patient
from oncotriage import deid as _deid
from oncotriage.batch import runner as _runner
from oncotriage.retrieval import indexer as _indexer
from oncotriage.storage import database_logger as _dl


#------------------------------------------------------------------------------


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
        _FAILURES.append(f"{label}\n          expected: {expected}\n"
                         f"          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def check_true(label, condition):
    check(label, bool(condition), True)


def quiet(fn, *args, **kwargs):
    """Run fn with stderr captured. Both channels write there."""
    buf = io.StringIO()
    with redirect_stderr(buf):
        value = fn(*args, **kwargs)
    return value, buf.getvalue()


def drive(fn, *args, **kwargs):
    """Call fn, converting a raise into a value ``check`` can FAIL on.

    NOT decoration. A bare call inside a ``check(...)`` argument list lets a
    planted defect's exception escape while the argument is being evaluated,
    which prints one traceback where the run owes a summary and every result
    below it. This project has shipped that shape nine times; the fix is
    mechanical and belongs in every file that drives production code.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                      # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


def at(mapping, key):
    """``mapping[key]`` that names an absence instead of raising KeyError."""
    try:
        return mapping[key]
    except BaseException as exc:                      # noqa: BLE001
        return f"<NO SUCH KEY {key!r}: {type(exc).__name__}: {exc}>"


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(oncotriage.__file__)))
_PKG = os.path.join(_ROOT, "oncotriage")


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# The four package files this file READS. Hashed now, compared at the end, so a
# claim that it writes nothing in the repository is measured rather than made.
_WATCHED = {
    rel: _sha(os.path.join(_ROOT, rel))
    for rel in ("oncotriage/degradation.py",
                "oncotriage/retrieval/indexer.py",
                "oncotriage/ablation/study.py",
                "oncotriage/batch/runner.py")
}


#------------------------------------------------------------------------------


# ===========================================================================
# THE LOAD / STORE CLASSIFIER
# ===========================================================================
#
# THE ONE THING THIS FILE HAS TO GET RIGHT. `C[k] += 1` binds the Name `C` in
# LOAD context -- the Store sits on the Subscript -- so the naive question "is
# this name ever loaded" answers YES for a counter nothing has ever read, which
# is exactly the population under audit. What decides is the PARENT.

_WRITE_METHODS = {"clear", "update", "subtract", "pop", "popitem", "setdefault"}


def _parents(tree):
    out = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def classify_ref(node, parents):
    """READ / WRITE / DECL for one Name or Attribute node."""
    parent = parents.get(node)
    if isinstance(parent, ast.Subscript) and parent.value is node:
        # ONE TEST, AND IT COVERS THE AUGMENTED FORM TOO. `C[k] += 1` gives the
        # enclosing Subscript ctx=Store -- MEASURED, not assumed: ast.AugStore
        # was removed in Python 3.9 and an AugAssign target now carries plain
        # Store. The first version of this function had a second branch walking
        # up to the AugAssign, which was therefore UNREACHABLE; the revert
        # harness found it by deleting that branch and watching nothing fail.
        # Dead code in a classifier is worse than dead code elsewhere: it reads
        # as coverage of a case the live branch might one day stop handling.
        return "WRITE" if isinstance(getattr(parent, "ctx", None),
                                     (ast.Store, ast.Del)) else "READ"
    if isinstance(parent, ast.Attribute) and parent.value is node:
        return "WRITE" if parent.attr in _WRITE_METHODS else "READ"
    if isinstance(getattr(node, "ctx", None), (ast.Store, ast.Del)):
        return "DECL"
    return "READ"


def refs_in(path, name):
    """{"READ": n, "WRITE": n, "DECL": n} for one name in one file."""
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    parents = _parents(tree)
    out = {"READ": 0, "WRITE": 0, "DECL": 0}
    for node in ast.walk(tree):
        hit = ((isinstance(node, ast.Name) and node.id == name)
               or (isinstance(node, ast.Attribute) and node.attr == name))
        if hit:
            out[classify_ref(node, parents)] += 1
    return out


def top_level_scripts(root):
    """The unnumbered, importable-name entry points at the repository root.

    WHY THE SCAN DOES NOT STOP AT ``oncotriage/``. This project has twice
    shipped a check whose corpus silently covered less than it read as
    covering -- ``tests/`` was invisible to check 2h until pass 20d-2 and
    ``docker/`` until 20f-3 -- and a corpus that covers less does not fail, it
    reports FEWER findings, which looks exactly like a clean tree. The
    numbered entry points cannot be walked as MODULES here (spaces, leading
    digits), but they are walked as TEXT below, which is all an ``ast`` parse
    needs.
    """
    return [os.path.join(root, f) for f in sorted(os.listdir(root))
            if f.endswith(".py")]


def module_counters(pkg_dir, extra_files=()):
    """[(relpath, name, lineno)] for every module-level Counter() in a corpus.

    BOTH DECLARATION FORMS. The first version of the audit script that produced
    this file walked ``ast.Assign`` only and therefore could not see
    ``NAME: Dict[str, int] = Counter()`` -- so it reported four fewer counters
    than exist, and the four it could not see included the two the audit was
    pointed at. A declaration form is a reference form and the project's rule
    covers it.

    MODULE LEVEL ONLY, which is the subject: a Counter local to a function is
    rebuilt per call and has no run-lifetime totals for anybody to read.
    """
    files = []
    for base, _dirs, names in os.walk(pkg_dir):
        if "__pycache__" in base:
            continue
        files += [os.path.join(base, n) for n in sorted(names)
                  if n.endswith(".py")]
    files += list(extra_files)

    found = []
    for full in files:
        tree = ast.parse(open(full, encoding="utf-8").read(), full)
        for node in tree.body:                       # MODULE LEVEL ONLY
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign):
                targets, value = [node.target], node.value
            else:
                continue
            if value is None or not isinstance(value, ast.Call):
                continue
            func = value.func
            is_counter = ((isinstance(func, ast.Name) and func.id == "Counter")
                          or (isinstance(func, ast.Attribute)
                              and func.attr == "Counter"))
            if not is_counter:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    found.append((os.path.relpath(full, _ROOT),
                                  target.id, node.lineno))
    return sorted(found)


#------------------------------------------------------------------------------


# ===========================================================================
# 1. EVERY COUNTER IN THE PACKAGE IS REGISTERED OR HAS A NAMED, VERIFIED READER
# ===========================================================================

print("=" * 74)
print("1. every module-level Counter has a production reader")
print("=" * 74)

# THE EXEMPTION TABLE IS CLOSED AND EVERY ENTRY NAMES ITS READER.
#
# counter name -> (the package file that owns it, the package file that READS
# it, one line saying why it is not in either registry).
#
# The reader is checked by AST below, so an entry whose reader was deleted or
# renamed fails and the table cannot become a permission slip. Note several
# rows name the OWNING file as the reader: that is the correct answer for an
# index-time or study-scoped counter, and it is the shape
# `degradation.py`'s docstring rules for both of them.
_READER_EXEMPTIONS = {
    # --- oncotriage/retrieval/indexer.py: index-time, read by its own blocks.
    # degradation.py excludes all eight by name; importing the indexer into the
    # registry would put a scrape module in every batch run's import graph.
    "ADMISSION_SCREEN": ("oncotriage/retrieval/indexer.py",
                         "oncotriage/retrieval/indexer.py", "index-time"),
    "ADMISSION_DROPPED_CATEGORIES": ("oncotriage/retrieval/indexer.py",
                                     "oncotriage/retrieval/indexer.py",
                                     "index-time"),
    "CRITERIA_SPLIT_METHODS": ("oncotriage/retrieval/indexer.py",
                               "oncotriage/retrieval/indexer.py", "index-time"),
    "CRITERIA_RENORMALIZED": ("oncotriage/retrieval/indexer.py",
                              "oncotriage/retrieval/indexer.py", "index-time"),
    "SCRAPE_RETRIES": ("oncotriage/retrieval/indexer.py",
                       "oncotriage/retrieval/indexer.py", "index-time"),
    "SCRAPE_INTERRUPTIONS": ("oncotriage/retrieval/indexer.py",
                             "oncotriage/retrieval/indexer.py", "index-time"),
    "EMBEDDING_USAGE": ("oncotriage/retrieval/indexer.py",
                        "oncotriage/retrieval/indexer.py", "index-time"),
    "CLEANUP_FAILURES": ("oncotriage/retrieval/indexer.py",
                         "oncotriage/retrieval/indexer.py",
                         "index-time; reader added by the counter-reader audit"),
    # --- oncotriage/ablation/study.py: study-scoped, read by its own main().
    "CHECKPOINT_WRITE_FAILURES": ("oncotriage/ablation/study.py",
                                  "oncotriage/ablation/study.py",
                                  "study-scoped"),
    # --- oncotriage/fhir/parser.py: CHARACTERIZATION, read by load_all_patients().
    "BIRTH_DATE_PRECISION_COUNTS": ("oncotriage/fhir/parser.py",
                                    "oncotriage/fhir/parser.py", "census"),
    "DEMOGRAPHIC_SOURCE_COUNTS": ("oncotriage/fhir/parser.py",
                                  "oncotriage/fhir/parser.py", "census"),
    "ECOG_VALUE_SHAPE_COUNTS": ("oncotriage/fhir/parser.py",
                                "oncotriage/fhir/parser.py", "census"),
    "ECOG_SELECTION_COUNTS": ("oncotriage/fhir/parser.py",
                              "oncotriage/fhir/parser.py", "census"),
    # Added by the ECOG pre-diagnosis pass. Same footing as the four above:
    # load_all_patients() prints it at the end of its own pass, and this module
    # has an end of its own where oncotriage/degradation.py's does not. It is
    # NOT a degradation -- 'compared' is the guard working -- but a key other
    # than 'compared' means the guard was asked for and could not run, which is
    # what the printed line exists to make visible.
    "ECOG_ANCHOR_COUNTS": ("oncotriage/fhir/parser.py",
                           "oncotriage/fhir/parser.py", "census"),
    # --- oncotriage/mcp/server.py: a long-lived SERVER, so there is no run end
    # for a run-end report to attach to. tool_failure_summary() is its reader.
    "TOOL_FAILURES": ("oncotriage/mcp/server.py", "oncotriage/mcp/server.py",
                      "server, not a run"),
    # --- oncotriage/api/server.py: a long-lived SERVER, the TOOL_FAILURES
    # case one module over. There is no run end for a run-end report to attach
    # to, and it cannot be registered either: oncotriage/degradation.py binds
    # the counter OBJECTS of the modules it names, so naming this one would put
    # FastAPI, slowapi and pydantic into every batch run's import graph.
    # shutdown_gate_report_lines() is the reader, and the STARTUP banner prints
    # it -- so an operator learns at bring-up that the Stage 5 shutdown gate is
    # not armed, rather than from a bill after a `docker stop`.
    "SHUTDOWN_GATE_DEGRADATIONS": ("oncotriage/api/server.py",
                                   "oncotriage/api/server.py",
                                   "server, not a run"),
    # --- mcp_server.py: an ENTRY POINT, outside the package, and the one
    # counter the root-level half of the scan finds. It cannot be registered:
    # oncotriage/degradation.py is a package module and cannot import a
    # top-level script, and the guard it counts runs during the IMPORT WINDOW,
    # before any registry could have been built. _report_guard_failures() is
    # its reader.
    "GUARD_FAILURES": ("mcp_server.py", "mcp_server.py",
                       "entry point, and it counts faults from the import "
                       "window itself"),
}

# CHECKPOINT_FAULTS is the one name owned by TWO modules -- batch/runner.py
# (registered through register()) and ablation/study.py (read by its own
# main()). It is handled explicitly below rather than through the table, whose
# keys are names: a table keyed by name cannot express "one of these is
# registered and the other is exempt", and the audit script that produced this
# file got exactly that wrong on its first run and credited the batch runner's
# reader to the study's counter.
#
# THE OPERATOR-CONTROL PASS ADDED TWO MORE, AND IT FOUND THEM BY READING RATHER
# THAN BY FAILING. `oncotriage/ablation/study.py` grew a STOP_SWITCH_FAULTS and
# a RUN_RECORD_FAILURES of its own; both names were ALREADY in the registry --
# the first from `oncotriage/batch/runner.py`, the second from
# `oncotriage/storage/database_logger.py` -- so the `_name in _registered`
# branch below credited another module's registration to the study's counter and
# this section PASSED with two brand-new write-only counters in the package.
# That is the exact conflation this table exists to prevent, arrived at twice.
_DUAL_OWNED = {"CHECKPOINT_FAULTS": {"oncotriage/batch/runner.py": "registered",
                                     "oncotriage/ablation/study.py": "exempt"},
               "STOP_SWITCH_FAULTS": {"oncotriage/batch/runner.py": "registered",
                                      "oncotriage/ablation/study.py": "exempt"},
               "RUN_RECORD_FAILURES": {
                   "oncotriage/storage/database_logger.py": "registered",
                   "oncotriage/ablation/study.py": "exempt"}}

_SCRIPTS = [f for f in top_level_scripts(_ROOT)
            if os.path.basename(f) not in ("setup.py",)]
_ALL = module_counters(_PKG, extra_files=_SCRIPTS)
check_true("the scan reaches the repository root as well as the package "
           "(non-degeneracy: with no top-level file in the corpus, a counter "
           "declared there would be invisible and the section would pass)",
           len(_SCRIPTS) >= 5)
check_true("the package holds a plausible number of module-level Counters "
           "(non-degeneracy: a walk that found nothing would pass everything "
           "below for free)", len(_ALL) >= 40)

_registered = set(degradation.registered_names())
_census = set(degradation.census_names())

_unaccounted = []
for _rel, _name, _lineno in _ALL:
    if _name in _DUAL_OWNED:
        _expected = at(_DUAL_OWNED[_name], _rel)
        if _expected == "registered" and _name in _registered:
            continue
        if _expected == "exempt":
            continue
        _unaccounted.append((_rel, _name, _lineno, f"dual-owned: {_expected}"))
        continue
    if _name in _registered or _name in _census:
        continue
    if _name in _READER_EXEMPTIONS:
        continue
    _unaccounted.append((_rel, _name, _lineno, "no registry entry, no exemption"))

check("EVERY module-level Counter is registered, census-registered or "
      "exempted with a named reader", _unaccounted, [])

# --- the exemptions are not a permission slip -------------------------------
for _name, (_owner, _reader, _why) in sorted(_READER_EXEMPTIONS.items()):
    _owner_path = os.path.join(_ROOT, _owner)
    _reader_path = os.path.join(_ROOT, _reader)
    check_true(f"{_name}: its owning file {_owner} exists",
               os.path.isfile(_owner_path))
    _counts = drive(refs_in, _reader_path, _name)
    check_true(f"{_name}: its named reader {_reader} contains a genuine READ "
               f"({_why})",
               isinstance(_counts, dict) and _counts.get("READ", 0) >= 1)

# --- and neither are the dual-owned ones ------------------------------------
#
# DRIVEN FROM THE TABLE rather than written out per name, so a fourth
# dual-owned counter cannot be added to `_DUAL_OWNED` -- which would silence
# the completeness check above for it -- without also being subjected to these
# three. `_DUAL_OWNED` is otherwise exactly the permission slip the exemption
# table is careful not to be.
_STUDY_PATH = os.path.join(_ROOT, "oncotriage/ablation/study.py")
_DUAL_OTHER = {"STOP_SWITCH_FAULTS": _runner,
               "RUN_RECORD_FAILURES": _dl,
               "CHECKPOINT_FAULTS": _runner}
for _dual, _owners in sorted(_DUAL_OWNED.items()):
    _registrar = [f for f, role in _owners.items() if role == "registered"]
    check(f"{_dual}: exactly one owner is the registered one",
          len(_registrar), 1)
    check_true(f"{_dual}: ...and that copy IS in the registry",
               _dual in _registered)
    _study_reads = drive(refs_in, _STUDY_PATH, _dual)
    check_true(f"{_dual}: the ablation study's copy is READ in its own module",
               isinstance(_study_reads, dict)
               and _study_reads.get("READ", 0) >= 1)
    check_true(f"{_dual}: ...and it is genuinely a second OBJECT, not the same "
               "one imported -- without this the exemption would be satisfied "
               "by a module that merely re-exported the registered counter",
               getattr(_study, _dual)
               is not getattr(_DUAL_OTHER[_dual], _dual))

# --- CONTROL: the classifier does not call an increment a read ---------------
# Without this the whole section passes for free: every counter is incremented
# somewhere, so a classifier that scored `C[k] += 1` as a READ would report the
# package as fully covered whatever the truth.
_CONTROL_SRC = '''
from collections import Counter
WRITE_ONLY = Counter()
READ_TOO = Counter()

SUBSCRIPT_READ = Counter()

def bump():
    WRITE_ONLY["x"] += 1
    WRITE_ONLY["y"] = 2
    READ_TOO["x"] += 1
    SUBSCRIPT_READ["x"] += 1

def report():
    return sum(READ_TOO.values()) + SUBSCRIPT_READ["x"]
'''
_ctrl_dir = tempfile.mkdtemp(prefix="counter-readers-")
_ctrl = os.path.join(_ctrl_dir, "control_module.py")
open(_ctrl, "w", encoding="utf-8").write(_CONTROL_SRC)
_wo = refs_in(_ctrl, "WRITE_ONLY")
_rt = refs_in(_ctrl, "READ_TOO")
check("CONTROL: a counter that is only incremented and assigned scores ZERO "
      "reads (`C[k] += 1` binds the Name in Load context -- this is the trap)",
      _wo["READ"], 0)
check("CONTROL: ...and both of its mutations score as writes", _wo["WRITE"], 2)
check("CONTROL: a counter whose values are summed scores a read",
      _rt["READ"], 1)
check("CONTROL: ...and its increment is still a write", _rt["WRITE"], 1)
# BOTH DIRECTIONS OF THE SUBSCRIPT BRANCH, and the second is here because the
# revert harness measured that it was missing. Scoring a read as a WRITE is the
# SAFE direction -- it under-counts readers, so a covered counter reports as
# unaccounted and somebody is sent to look at a false alarm -- and it went
# uncaught, because every exempted counter has several reads and this file only
# asks for one. A control that covers one direction of a two-way branch is half
# a control whichever direction happens to be the dangerous one.
_sr = refs_in(_ctrl, "SUBSCRIPT_READ")
check("CONTROL: a counter read by SUBSCRIPT (`C['k']` in a value position, "
      "which oncotriage/retrieval/indexer.py genuinely does) scores a read",
      _sr["READ"], 1)
check("CONTROL: ...and its increment is still a write", _sr["WRITE"], 1)

# --- CONTROL: the annotated declaration form is seen ------------------------
# The form the two counters this pass registered actually use, and the form the
# first version of the audit missed entirely.
# ITS OWN DIRECTORY. The first version wrote it beside the classifier control
# above and then walked the shared directory, so the walk found that file's two
# counters as well and the check failed against a list it had itself polluted.
_ann_dir = os.path.join(_ctrl_dir, "annotated")
os.makedirs(_ann_dir)
open(os.path.join(_ann_dir, "annotated_module.py"), "w",
     encoding="utf-8").write(
    "from collections import Counter\n"
    "from typing import Dict\n"
    "PLAIN = Counter()\n"
    "ANNOTATED: Dict[str, int] = Counter()\n"
    "NOT_A_COUNTER: Dict[str, int] = {}\n"
    "\n"
    "def f():\n"
    "    NESTED = Counter()\n"
    "    return NESTED\n")
_ctrl_found = {n for _r, n, _l in module_counters(_ann_dir, extra_files=())}
check("CONTROL: both declaration forms are found; a plain dict is not, and "
      "neither is a Counter local to a function (MODULE-level is the subject)",
      sorted(_ctrl_found), ["ANNOTATED", "PLAIN"])

# --- CONTROL: removing a registration makes section 1 fail -------------------
# `.pop(name, None)` AND NOT `.pop(name)`. The key this control removes is the
# very key the revert that deletes its registration removes, so a bare pop
# raises KeyError exactly when this file owes a recorded failure -- one
# traceback where the run owes a summary and every result below it. Measured:
# the first version of this file ABORTED on two of eleven reverts for precisely
# that reason.
_popped = degradation._REGISTRY.pop("MARKDOWN_ESCAPE_DECODE_UNRESOLVED", None)
check_true("MARKDOWN_ESCAPE_DECODE_UNRESOLVED was registered, so the control "
           "below has something to remove", _popped is not None)
try:
    _live = set(degradation.registered_names()) | set(degradation.census_names())
    _would_fail = [n for _r, n, _l in _ALL
                   if n not in _live and n not in _READER_EXEMPTIONS
                   and n not in _DUAL_OWNED]
    check("CONTROL: a counter dropped from the registry becomes unaccounted "
          "(so section 1 can fail)",
          _would_fail, ["MARKDOWN_ESCAPE_DECODE_UNRESOLVED"])
finally:
    if _popped is not None:
        degradation._REGISTRY["MARKDOWN_ESCAPE_DECODE_UNRESOLVED"] = _popped
check_true("...and the registry was restored",
           "MARKDOWN_ESCAPE_DECODE_UNRESOLVED"
           in degradation.registered_names())


#------------------------------------------------------------------------------


# ===========================================================================
# 2. THE THREE NEW REGISTRATIONS ARE PRESENT AND CARRY A MEANING
# ===========================================================================

print("\n" + "=" * 74)
print("2. the three counters the audit found write-only are registered")
print("=" * 74)

_NEWLY_REGISTERED = (
    ("MARKDOWN_ESCAPE_DECODE_UNRESOLVED",
     _evaluation.MARKDOWN_ESCAPE_DECODE_UNRESOLVED),
    ("ESCAPED_ENTITY_DECODE_UNRESOLVED",
     _evaluation.ESCAPED_ENTITY_DECODE_UNRESOLVED),
    ("ASSESSMENT_COMPOSITION_ANOMALIES",
     _evaluation.ASSESSMENT_COMPOSITION_ANOMALIES),
)

for _name, _obj in _NEWLY_REGISTERED:
    check_true(f"{_name} is in the registry",
               _name in degradation.registered_names())
    # BY IDENTITY, not by name. A registry entry bound to a DIFFERENT Counter
    # object -- a snapshot taken at import, say -- would satisfy every
    # name-based check above and report zero forever, which is the failure
    # degradation.py's own docstring warns about for `NAME = Counter()` inside
    # a function.
    check_true(f"...and it is bound to the OBJECT the module increments",
               at(degradation._REGISTRY, _name) is _obj)
    _meaning = at(degradation._MEANINGS, _name)
    check_true(f"...and it carries a meaning line the report can print",
               isinstance(_meaning, str) and len(_meaning) > 40)

check_true("the registry and the census share no name (the import-time guard's "
           "subject)",
           set(degradation.registered_names())
           & set(degradation.census_names()) == set())
# AND THE GUARD ITSELF, DRIVEN. The line above observes that the live pair does
# not collide; it says nothing about whether anything would notice if it did.
# The guard takes both registries as arguments precisely so a colliding pair can
# be handed to it without breaking the module -- which would abort the import
# rather than record a failure.
check("the live pair passes the guard",
      drive(degradation.assert_registries_disjoint), None)
_collide = drive(degradation.assert_registries_disjoint,
                 {"SHARED": None, "a": None}, {"SHARED": None, "b": None})
check_true("CONTROL: a name in BOTH registries raises, and the message names "
           "it", isinstance(_collide, str) and "RAISED RuntimeError" in _collide
           and "SHARED" in _collide)
check("CONTROL: ...while a pair that merely overlaps in NOTHING passes",
      drive(degradation.assert_registries_disjoint, {"a": None}, {"b": None}),
      None)


#------------------------------------------------------------------------------


# ===========================================================================
# 3. A NEWLY REGISTERED COUNTER REACHES THE REPORT AND THE EVENT
# ===========================================================================

print("\n" + "=" * 74)
print("3. a newly registered counter is named by the run-end report")
print("=" * 74)

_saved = {name: dict(degradation._REGISTRY[name])
          for name in degradation.registered_names()}
try:
    degradation.clear_all()
    # Real keys, in the shape the two decoders actually produce.
    _evaluation.MARKDOWN_ESCAPE_DECODE_UNRESOLVED[
        "escaped_backslash:\\\\ CLN1114"] += 2
    _evaluation.ESCAPED_ENTITY_DECODE_UNRESOLVED["pass_cap:&amp;amp;lt;"] += 1
    _evaluation.ASSESSMENT_COMPOSITION_ANOMALIES["kept_no_disqualifier"] += 3

    _snap = degradation.snapshot()
    check("all three are in the snapshot and nothing else is",
          sorted(_snap), ["ASSESSMENT_COMPOSITION_ANOMALIES",
                          "ESCAPED_ENTITY_DECODE_UNRESOLVED",
                          "MARKDOWN_ESCAPE_DECODE_UNRESOLVED"])
    _text = "\n".join(degradation.report_lines(_snap))
    for _name, _ in _NEWLY_REGISTERED:
        check_true(f"the console block names {_name}", _name in _text)
    check_true("...and prints its key and its count",
               "kept_no_disqualifier" in _text and "3" in _text)
    check_true("...and no longer claims the run was clean", "CLEAN" not in _text)

    _totals, _out = quiet(degradation.log_summary, _snap)
    check("the event's totals cover all three",
          _totals, {"MARKDOWN_ESCAPE_DECODE_UNRESOLVED": 2,
                    "ESCAPED_ENTITY_DECODE_UNRESOLVED": 1,
                    "ASSESSMENT_COMPOSITION_ANOMALIES": 3})
    # THE KEYS CARRY SCRAPED TRIAL TEXT -- both decoders key on a slice of the
    # criteria field. `totals()` is what keeps them out of a durable record,
    # and this is what says it still does now that they are registered.
    check_true("the structured event carries counter NAMES and no counter KEY, "
               "so the scraped text in these keys stays off the durable record",
               "CLN1114" not in _out and "amp" not in _out
               and '"degradation_totals"' in _out)
    check_true("...and nothing was dropped by the field allowlist "
               "(non-degeneracy)", '"dropped_fields"' not in _out)
finally:
    degradation.clear_all()
    for _name, _values in _saved.items():
        degradation._REGISTRY[_name].update(_values)


#------------------------------------------------------------------------------


# ===========================================================================
# 4. AND IT REACHES `run_metrics`, DRIVEN THROUGH THE REAL FLUSH
# ===========================================================================
#
# The requirement is that a newly registered counter flows into the persisted
# health record "automatically through the existing flush". Automatic is
# exactly the kind of claim that is true by reading and false by running, so
# this drives oncotriage/batch/runner.py:flush_health against a real SQLite
# file built by the real initialize_database and reads the row back out.

print("\n" + "=" * 74)
print("4. it reaches run_metrics through the existing flush (driven)")
print("=" * 74)

_tmp = tempfile.mkdtemp(prefix="counter-readers-db-")
_DB = os.path.join(_tmp, "scratch_inferences.db")

# paths._RESOLVED is seeded so nothing in this process can resolve to the
# production database, on tests/test_ablation_db_isolation.py's precedent.
_saved_resolved = dict(paths._RESOLVED)
paths._RESOLVED["inferences_path"] = os.path.join(_tmp, "never_written.db")
try:
    check_true("the scratch database is NOT the production one "
               "(non-degeneracy for every isolation claim below)",
               os.path.abspath(_DB)
               != os.path.abspath(_dl.resolve_inference_db_path(None)))

    _dl.initialize_database(_DB)
    _run_id = _dl.start_run_record("counter_reader_test", db_path=_DB)
    check_true("a run row was opened", isinstance(_run_id, int))

    _saved4 = {name: dict(degradation._REGISTRY[name])
               for name in degradation.registered_names()}
    try:
        degradation.clear_all()
        _evaluation.ESCAPED_ENTITY_DECODE_UNRESOLVED["pass_cap:&amp;lt;"] += 5

        _flushed, _ = quiet(_runner.flush_health, _run_id, db_path=_DB)
        check("flush_health reported success", _flushed, True)

        with sqlite3.connect(_DB) as _conn:
            _rows = dict(_conn.execute(
                "SELECT name, value FROM run_metrics "
                "WHERE run_id = ? AND category = ?",
                (_run_id, _dl.RUN_METRIC_CATEGORY_DEGRADATION)).fetchall())
            _meta = dict(_conn.execute(
                "SELECT name, value FROM run_metrics "
                "WHERE run_id = ? AND category = ?",
                (_run_id, _dl.RUN_METRIC_CATEGORY_META)).fetchall())

        check("the newly registered counter is a run_metrics ROW, with its "
              "total, and it got there with no edit to the flush",
              at(_rows, "ESCAPED_ENTITY_DECODE_UNRESOLVED"), 5)
        check("...and it is the only degradation row, so the flush wrote what "
              "moved rather than everything", sorted(_rows),
              ["ESCAPED_ENTITY_DECODE_UNRESOLVED"])
        check("...and counters_registered counts the WIDENED registry",
              at(_meta, _dl.RUN_METRIC_META_COUNTERS_REGISTERED),
              len(degradation.registered_names()))
        check("...and counters_nonzero is the one that moved",
              at(_meta, _dl.RUN_METRIC_META_COUNTERS_NONZERO), 1)
        # THE ROW IS THE TOTAL, NEVER THE KEY. Same rule as the log event, and
        # it matters more here: run_metrics is durable and run-keyed.
        with sqlite3.connect(_DB) as _conn:
            _names = [r[0] for r in _conn.execute(
                "SELECT name FROM run_metrics WHERE run_id = ?",
                (_run_id,)).fetchall()]
        check_true("no run_metrics row is keyed by the counter's own KEY, "
                   "which carries scraped trial text",
                   all("pass_cap" not in n and "&" not in n for n in _names))

        # --- CONTROL: an UNREGISTERED counter does not reach the table -------
        # Without this the check above is satisfied by a flush that writes
        # every counter in the package regardless of registration.
        # `.pop(name, None)` for the reason section 1's control gives.
        _pop = degradation._REGISTRY.pop("ESCAPED_ENTITY_DECODE_UNRESOLVED",
                                         None)
        check_true("ESCAPED_ENTITY_DECODE_UNRESOLVED was registered, so this "
                   "control has something to remove", _pop is not None)
        try:
            _run_id2 = _dl.start_run_record("counter_reader_control",
                                            db_path=_DB)
            _flushed2, _ = quiet(_runner.flush_health, _run_id2, db_path=_DB)
            with sqlite3.connect(_DB) as _conn:
                _rows2 = dict(_conn.execute(
                    "SELECT name, value FROM run_metrics "
                    "WHERE run_id = ? AND category = ?",
                    (_run_id2, _dl.RUN_METRIC_CATEGORY_DEGRADATION)).fetchall())
            check("CONTROL: with the registration removed the counter is "
                  "absent from run_metrics (the pre-pass state, reproduced)",
                  "ESCAPED_ENTITY_DECODE_UNRESOLVED" in _rows2, False)
            check_true("CONTROL: ...while the counter itself is still non-zero, "
                       "so the absence is the registry's and not the counter's",
                       _pop is not None and sum(_pop.values()) == 5)
        finally:
            if _pop is not None:
                degradation._REGISTRY["ESCAPED_ENTITY_DECODE_UNRESOLVED"] = _pop
    finally:
        degradation.clear_all()
        for _name, _values in _saved4.items():
            degradation._REGISTRY[_name].update(_values)
finally:
    paths._RESOLVED.clear()
    paths._RESOLVED.update(_saved_resolved)


#------------------------------------------------------------------------------


# ===========================================================================
# 5. THE INDEXER'S NEW READER PRINTS CLEANUP_FAILURES
# ===========================================================================

print("\n" + "=" * 74)
print("5. the indexer prints CLEANUP_FAILURES at the end of a build")
print("=" * 74)

_saved_cleanup = dict(_indexer.CLEANUP_FAILURES)
try:
    _indexer.CLEANUP_FAILURES.clear()
    _lines = []
    check("with nothing to report the reader prints nothing and SAYS it "
          "printed nothing", drive(_indexer.report_cleanup_failures,
                                   out=_lines.append), False)
    check("...and emitted no line (a zero line every build trains a reader to "
          "skip the place the real one appears)", _lines, [])

    # All three fault shapes at once, because the key is what distinguishes
    # "a collection was not deleted" from "the size floor did not run".
    _indexer.CLEANUP_FAILURES["UnexpectedResponse"] += 2
    _indexer.CLEANUP_FAILURES["compare_count:TimeoutError"] += 1
    _indexer.CLEANUP_FAILURES["previous_count:ConnectionError"] += 1
    _lines = []
    _, _logged = quiet(drive, _indexer.report_cleanup_failures,
                       out=_lines.append)
    _text = "\n".join(_lines)
    check_true("the reader emitted something", bool(_lines))
    check_true("...naming the grand total", "4" in _text)
    for _key in ("UnexpectedResponse", "compare_count:TimeoutError",
                 "previous_count:ConnectionError"):
        check_true(f"...and the key {_key}", _key in _text)
    check_true("...and it says a size-floor read failing means THAT CHECK DID "
               "NOT RUN, which is the half a reader must not miss",
               "DID NOT RUN" in _text)
    check_true("...and a structured event went out with the total",
               '"event": "cleanup_failures"' in _logged
               and '"total": 4' in _logged)

    # --- THE RAISE PATH, WHICH IS THE ONE THAT MATTERS -----------------------
    # verify_collection increments CLEANUP_FAILURES under `compare_count:` and
    # then RAISES, so a reader called at the end of a successful build is
    # skipped by exactly the build whose size floor did not run. The context
    # manager is what closes that, and this drives it both ways.
    def _raising_build():
        """A build that moves the counter and then fails verification."""
        with _indexer.cleanup_failures_reported():
            _indexer.CLEANUP_FAILURES["compare_count:TimeoutError"] += 1
            raise _indexer.IndexVerificationError("planted")

    _raised, _out_raise = quiet(drive, _raising_build)
    check_true("the build's exception is NOT suppressed by the reporter",
               isinstance(_raised, str)
               and "RAISED IndexVerificationError: planted" in _raised)
    # The manager has no `out` seam -- it is production wiring, not a reader --
    # so this asserts on the captured console stream, which is where the real
    # build's line goes.
    check_true("...and the tally was still printed on the way out, which a "
               "call at the end of main() would have skipped",
               "compare_count:TimeoutError" in _out_raise
               and "FAILED this build" in _out_raise)
    check_true("...naming the running total, so the raise path gets the same "
               "report the success path does",
               str(sum(_indexer.CLEANUP_FAILURES.values())) in _out_raise)

    # AND THE CLEAN EXIT, which is the other half: a manager that reported only
    # from an exception path would be silent on every successful build.
    def _clean_build():
        with _indexer.cleanup_failures_reported():
            return "finished"

    check_true("(the counter is non-zero going in, so the clean-exit check is "
               "not vacuous)", sum(_indexer.CLEANUP_FAILURES.values()) > 0)
    _returned, _out_clean = quiet(drive, _clean_build)
    check("a build that does NOT raise returns its value unchanged",
          _returned, "finished")
    check_true("...and still gets the tally",
               "FAILED this build" in _out_clean)

    # --- the wiring: main() reports however the build ends -------------------
    _ix_tree = ast.parse(open(_indexer.__file__, encoding="utf-8").read(),
                         _indexer.__file__)
    _main = next((n for n in ast.walk(_ix_tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    check_true("indexer.main() was found (non-degeneracy for the checks below)",
               _main is not None)
    _withs = [n for n in ast.walk(_main) if isinstance(n, ast.With)] if _main else []
    _cm_names = [i.context_expr.func.id
                 for w in _withs for i in w.items
                 if isinstance(i.context_expr, ast.Call)
                 and isinstance(i.context_expr.func, ast.Name)]
    check("main() enters the reporting context manager exactly once",
          _cm_names.count("cleanup_failures_reported"), 1)
    check_true("...and it is on the OUTERMOST with, so it covers the whole "
               "build rather than one arm of it",
               any("cleanup_failures_reported" in
                   [i.context_expr.func.id for i in w.items
                    if isinstance(i.context_expr, ast.Call)
                    and isinstance(i.context_expr.func, ast.Name)]
                   for w in _withs
                   if w in _main.body) if _main else False)
    # The reader must be reached from a `finally`, not from the happy path of
    # the generator: `yield` then a bare call would skip it on a raise.
    _cm = next((n for n in ast.walk(_ix_tree)
                if isinstance(n, ast.FunctionDef)
                and n.name == "cleanup_failures_reported"), None)
    check_true("the context manager was found (non-degeneracy)", _cm is not None)
    _tries = [n for n in ast.walk(_cm) if isinstance(n, ast.Try)] if _cm else []
    check_true("...and it calls the reader from a finally, so a raising build "
               "still reports",
               any(any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                       and c.func.id == "report_cleanup_failures"
                       for s in t.finalbody for c in ast.walk(s))
                   for t in _tries))
    check_true("...and it catches nothing, so it cannot swallow "
               "IndexVerificationError",
               all(not t.handlers for t in _tries))
finally:
    _indexer.CLEANUP_FAILURES.clear()
    _indexer.CLEANUP_FAILURES.update(_saved_cleanup)


#------------------------------------------------------------------------------


# ===========================================================================
# 6. THE ABLATION STUDY'S CHECKPOINT_FAULTS READER
# ===========================================================================

print("\n" + "=" * 74)
print("6. the ablation study prints its own CHECKPOINT_FAULTS")
print("=" * 74)

_saved_faults = dict(_study.CHECKPOINT_FAULTS)
try:
    _study.CHECKPOINT_FAULTS.clear()
    _lines = []
    check("with nothing to report it prints nothing",
          drive(_study.report_checkpoint_faults, out=_lines.append), False)
    check("...and emits no line", _lines, [])

    _study.CHECKPOINT_FAULTS["refused:configuration_changed"] += 1
    _study.CHECKPOINT_FAULTS["load:JSONDecodeError"] += 2
    _lines = []
    check("with faults it reports",
          drive(_study.report_checkpoint_faults, out=_lines.append), True)
    _text = "\n".join(_lines)
    check_true("...naming the total and both keys",
               "3" in _text and "refused:configuration_changed" in _text
               and "load:JSONDecodeError" in _text)
    check_true("...and saying that a refusal means the study covers only what "
               "it executed itself, which is what stops 'COMPLETE' being read "
               "as 'the whole cohort'", "DECLINED" in _text)

    _st_tree = ast.parse(open(_study.__file__, encoding="utf-8").read(),
                         _study.__file__)
    _st_main = next((n for n in ast.walk(_st_tree)
                     if isinstance(n, ast.FunctionDef) and n.name == "main"),
                    None)
    check_true("study.main() was found (non-degeneracy)", _st_main is not None)

    # ── REACHABILITY, NOT ADJACENCY (the operator-control pass) ────────────
    #
    # THIS CHECK USED TO REQUIRE THE CALL TO BE A DIRECT STATEMENT OF main(),
    # and it went stale the first time the reader was moved one frame down --
    # into `print_study_close`, the block main() calls on both its exit paths.
    # The property it exists to hold is that THE READER RUNS WHEN A STUDY ENDS;
    # which function literally contains the call is an implementation detail,
    # and pinning it made a refactor that PRESERVED the property look like one
    # that broke it.
    #
    # A NAME LIST WOULD ROT THE SAME WAY, one move later, so the frame is
    # DERIVED: a transitive walk over the module's own top-level functions.
    # `main -> print_study_close -> report_checkpoint_faults` resolves, and so
    # would the old direct shape, and so will the next one.
    #
    # THE WALK IS BOUNDED BY THE MODULE. A call to something this module does
    # not define is not followed -- there is nothing to follow it into -- which
    # is what keeps it finite without a depth limit, and `seen` is what makes
    # recursion terminate.
    _st_funcs = {n.name: n for n in _st_tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def _calls_of(node):
        return {c.func.id for c in ast.walk(node)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}

    def _reaches(root, target):
        """Does `root` call `target`, directly or through this module?"""
        seen, stack = set(), [root]
        while stack:
            name = stack.pop()
            if name in seen or name not in _st_funcs:
                continue
            seen.add(name)
            called = _calls_of(_st_funcs[name])
            if target in called:
                return True
            stack.extend(called)
        return False

    check_true("the module-level function table is non-degenerate (a walk that "
               "found no functions would make every reachability answer below "
               "False for the wrong reason)", len(_st_funcs) >= 10)
    check_true("...and it holds both frames this path runs through",
               "main" in _st_funcs and "print_study_close" in _st_funcs)
    check("study.main() REACHES report_checkpoint_faults",
          _reaches("main", "report_checkpoint_faults"), True)
    check("...and reaches the other two study counters' readers too, so an "
          "interrupted or stopped study reports its degradations rather than "
          "only a finished one",
          [_reaches("main", _r) for _r in ("report_stop_switch_faults",
                                           "report_run_record_failures")],
          [True, True])
    # CONTROLS: the walk must be able to answer False, and must not answer True
    # by finding a name that is merely mentioned.
    check("CONTROL: a reader nothing calls is NOT reachable",
          _reaches("main", "report_checkpoint_faults_that_does_not_exist"),
          False)
    check("CONTROL: ...and a function that is defined but never called from "
          "main() is not reachable either, so the walk is following edges "
          "rather than scanning the file",
          _reaches("report_checkpoint_faults", "main"), False)
finally:
    _study.CHECKPOINT_FAULTS.clear()
    _study.CHECKPOINT_FAULTS.update(_saved_faults)


#------------------------------------------------------------------------------


# ===========================================================================
# 7. THE CENSUS BLOCK -- SEPARATE FROM THE DEGRADATION BLOCK, AND WIRED UP
# ===========================================================================

print("\n" + "=" * 74)
print("7. the census counters have a reader, and it is not the degradation one")
print("=" * 74)

check("the census registry holds exactly the five counters the degradation "
      "registry excludes for moving on correct behaviour. DEID_CENSUS is the "
      "fifth: a capped age is oncotriage/deid.py's stage working, not a fault, "
      "so it is here and DEID_REFUSALS -- which counts patients that were NOT "
      "evaluated -- is in the degradation block instead",
      sorted(degradation.census_names()),
      ["DEID_CENSUS", "PROCEDURE_RENDER_COUNTS",
       "TEMPORAL_CONFLICT_ACTIVE_MARKERS",
       "TEMPORAL_CONFLICT_RESOLVED_MARKERS", "TEMPORAL_RENDER_COUNTS"])

for _name, _obj in (("DEID_CENSUS", _deid.DEID_CENSUS),
                    ("PROCEDURE_RENDER_COUNTS", _patient.PROCEDURE_RENDER_COUNTS),
                    ("TEMPORAL_RENDER_COUNTS", _patient.TEMPORAL_RENDER_COUNTS),
                    ("TEMPORAL_CONFLICT_RESOLVED_MARKERS",
                     _evaluation.TEMPORAL_CONFLICT_RESOLVED_MARKERS),
                    ("TEMPORAL_CONFLICT_ACTIVE_MARKERS",
                     _evaluation.TEMPORAL_CONFLICT_ACTIVE_MARKERS)):
    check_true(f"{_name} is bound BY IDENTITY to the object its module "
               f"increments", at(degradation._CENSUS, _name) is _obj)

_saved_census = {n: dict(degradation._CENSUS[n])
                 for n in degradation.census_names()}
try:
    degradation.clear_census()
    _empty = degradation.census_snapshot()
    check("a zero census snapshots to nothing", _empty, {})
    _empty_text = "\n".join(degradation.census_report_lines(_empty))
    # DERIVED FROM THE REGISTRY, NOT RETYPED. The number was the literal 4 and
    # went stale the first time a census counter was added, which is the shape
    # this file exists to catch one layer up.
    check_true("...and the block SAYS SO rather than printing nothing",
               f"All {len(degradation.census_names())} census counters are "
               f"zero" in _empty_text)
    # A CENSUS HAS NO VERDICT TO GIVE. The degradation block's zero case says
    # "CLEAN"; this one must not, because zero here on a run that matched
    # patients is itself a finding.
    check_true("...and it does NOT call zero clean", "CLEAN" not in _empty_text)

    _patient.PROCEDURE_RENDER_COUNTS["kept"] += 7
    _patient.PROCEDURE_RENDER_COUNTS["dropped"] += 3
    _evaluation.TEMPORAL_CONFLICT_RESOLVED_MARKERS["in remission"] += 2
    _snap = degradation.census_snapshot()
    check("only the census counters that moved are in the snapshot",
          sorted(_snap), ["PROCEDURE_RENDER_COUNTS",
                          "TEMPORAL_CONFLICT_RESOLVED_MARKERS"])
    check("...with their keys and counts",
          at(_snap, "PROCEDURE_RENDER_COUNTS"), {"dropped": 3, "kept": 7})
    check("census_totals sums each counter's keys",
          degradation.census_totals(_snap),
          {"PROCEDURE_RENDER_COUNTS": 10,
           "TEMPORAL_CONFLICT_RESOLVED_MARKERS": 2})
    _text = "\n".join(degradation.census_report_lines(_snap))
    check_true("the block names the counter, its keys and its meaning",
               "PROCEDURE_RENDER_COUNTS" in _text and "kept" in _text
               and "dropped" in _text)
    check_true("...and states in its own heading that these are NOT "
               "degradations, so the two blocks cannot be read as one",
               "NOT degradations" in _text)

    # --- THE SEPARATION, MEASURED --------------------------------------------
    # The ruling these four are excluded under is that the degradation block
    # reads "N of M counters moved" and every entry in it means something went
    # wrong. This is what says a moved census counter does not appear there.
    _deg_snap = degradation.snapshot()
    check("a moved CENSUS counter is absent from the DEGRADATION snapshot",
          [n for n in _deg_snap if n in degradation.census_names()], [])
    _deg_text = "\n".join(degradation.report_lines(_deg_snap))
    check_true("...and the degradation block still reports the run as CLEAN, "
               "so 'N of M counters moved' keeps its meaning",
               "CLEAN" in _deg_text)
    check_true("...and does not name a census counter",
               "PROCEDURE_RENDER_COUNTS" not in _deg_text)

    # --- the wiring into the batch runner's end-of-run block -----------------
    _rn_tree = ast.parse(open(_runner.__file__, encoding="utf-8").read(),
                         _runner.__file__)
    _rn_main = next((n for n in ast.walk(_rn_tree)
                     if isinstance(n, ast.FunctionDef) and n.name == "main"),
                    None)
    check_true("runner.main() was found (non-degeneracy)", _rn_main is not None)
    _attrs = [n.func.attr for n in ast.walk(_rn_main)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and isinstance(n.func.value, ast.Name)
              and n.func.value.id == "degradation"] if _rn_main else []
    check("main() takes the census snapshot exactly once",
          _attrs.count("census_snapshot"), 1)
    check("...and still takes the degradation snapshot exactly once, so this "
          "pass did not disturb the one-instant guarantee",
          _attrs.count("snapshot"), 1)
    _ps = next((n for n in ast.walk(_rn_tree)
                if isinstance(n, ast.FunctionDef) and n.name == "print_summary"),
               None)
    check_true("print_summary() was found (non-degeneracy)", _ps is not None)
    check_true("...and it takes census_snapshot as a keyword argument",
               _ps is not None
               and "census_snapshot" in [a.arg for a in _ps.args.args])
    _ps_calls = [n.func.attr for n in ast.walk(_ps)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and isinstance(n.func.value, ast.Name)
                 and n.func.value.id == "degradation"] if _ps else []
    check_true("...and prints BOTH blocks",
               "print_census_report" in _ps_calls
               and "print_report" in _ps_calls)

    # --- CONTROL: clear_census does not touch the degradation registry -------
    # The reason clear_census exists as a second function: every existing
    # caller of clear_all() saves and restores _REGISTRY and nothing else, so a
    # widened clear_all would silently zero four counters they never put back.
    from oncotriage import utils as _utils
    _utils.QDRANT_RETRIES["_planted"] += 1
    degradation.clear_census()
    check("CONTROL: clear_census zeroed the census",
          degradation.census_snapshot(), {})
    check_true("CONTROL: ...and left the degradation registry alone",
               sum(_utils.QDRANT_RETRIES.values()) == 1)
    _utils.QDRANT_RETRIES.clear()
finally:
    degradation.clear_census()
    for _name, _values in _saved_census.items():
        degradation._CENSUS[_name].update(_values)


#------------------------------------------------------------------------------


# ===========================================================================
# 8. THIS FILE WROTE NOTHING IN THE REPOSITORY
# ===========================================================================

print("\n" + "=" * 74)
print("8. nothing in the repository was written")
print("=" * 74)

for _rel, _expected in sorted(_WATCHED.items()):
    check(f"{_rel} is byte-identical", _sha(os.path.join(_ROOT, _rel)),
          _expected)
check_true("...and the four hashes are not all the same value "
           "(non-degeneracy: a comparison of one file with itself would pass "
           "for free)", len(set(_WATCHED.values())) == 4)

for _dirpath in (_ctrl_dir, _tmp):
    shutil.rmtree(_dirpath, ignore_errors=True)
    check(f"the scratch directory {os.path.basename(_dirpath)} was removed",
          os.path.exists(_dirpath), False)


#------------------------------------------------------------------------------


print("\n" + "=" * 74)
print("SUMMARY")
print("=" * 74)
print(f"  passed: {_RESULTS['passed']}")
print(f"  failed: {_RESULTS['failed']}")
if _FAILURES:
    print("\nFAILURES:")
    for _failure in _FAILURES:
        print(f"  - {_failure}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug 22 2026

@author: ramyalsaffar
"""
