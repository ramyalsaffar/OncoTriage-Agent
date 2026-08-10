# Package Invariants
####################

"""
Proves the `oncotriage` package's structural invariants.

RENAMED IN PASS 20d-2, from "47- Package Split Test.py". It was named for the
pass that created it (item 20c, the split), and that pass ends at 20e. What it
actually holds is a standing contract about the package -- what importing it may
do, what may import what, which names must exist and which must not, what a
wheel must ship -- and none of that stops mattering when the split is finished.
The number-to-name mapping is in tests/FILE NUMBER MAPPING.md.

THE INVARIANTS, which are what the sections below are organised around:

  * importing any package module opens no client, loads no model, touches no
    database, reads no file, creates no directory, resolves no directory and
    spawns no process (section 2, under twelve traps);
  * `oncotriage.config` never imports `oncotriage.utils` -- the cycle item 20c
    removed -- and no module imports another from inside a function body;
  * `SparseTextEmbedding("Qdrant/bm25")` has exactly ONE construction site, and
    the MedCPT cross-encoder checkpoint is NAMED in exactly one place, with the
    tokenizer and the weights loaded from that one name (section 2f);
  * no module-level import is shadowed by a function-local, and no name is
    declared and never read anywhere in the repository;
  * every subpackage on disk is declared in pyproject.toml, at any depth;
  * the dependency seam hands one shared object per key under MAX_WORKERS
    threads, building each exactly once;
  * the shims still re-export what the exec chain reads out of them.

WHAT WAS CHANGED by the split this originally proved
----------------------------------------------------
Files 01, 02, 03, 08, 09 and 10 stopped being the definitions and became
re-export shims over a real Python package:

WHAT WAS CHANGED
----------------
Files 01, 02, 03, 08, 09 and 10 stopped being the definitions and became
re-export shims over a real Python package:

    oncotriage/settings.py    env-var names, path resolution
    oncotriage/paths.py       IS_DOCKER, _glob_one, every path variable,
                              load_env_keys
    oncotriage/constants.py   SYSTEM_KEY_ABSENT / SYSTEM_KEY_UNRECOGNIZED
    oncotriage/config.py      every tunable + LAZY client/keys accessors
    oncotriage/utils.py       cost, retry, partial dates, exec_chain, caffeinate

    oncotriage/registries/cancer_code_registry.py   File 08, whole
    oncotriage/registries/mesh.py                   File 09's filter half
    oncotriage/registries/mesh_crosswalk_build.py   File 09's five offline
                                                    builders
    oncotriage/extraction/negation.py               _is_negated, the one name
                                                    File 10's two halves shared
    oncotriage/extraction/stage.py                  File 10 to line 698
    oncotriage/extraction/histology.py              File 10 from line 699

Pass 20c-3a added six more and turned four numbered files into thin entry points:

    oncotriage/embedding.py                 THE ONE construction site for the
                                            FastEmbed BM25 sparse model. It was
                                            built in three independent places --
                                            File 11 at index time, deps at query
                                            time, File 12 inside its own smoke
                                            test -- for the two halves of one
                                            job. See check 2f.
    oncotriage/fhir/clean.py                File 05, whole. File 05 keeps a full
                                            re-export shim: File 34 chains it.
    oncotriage/fhir/generate.py             File 04, whole
    oncotriage/fhir/explore.py              File 06, whole
    oncotriage/retrieval/indexer.py         File 11, whole
    oncotriage/retrieval/index_validator.py File 12, whole

    Files 04, 06, 11 and 12 have NO exec bootstrap at all now -- nothing in the
    repository chains them, so there is no shared namespace to feed.

Pass 20c-2b added two more, and corrected one thing pass 2a shipped:

    oncotriage/fhir/parser.py               File 07, whole
    oncotriage/storage/database_logger.py   File 14, whole -- with log_inference
                                            taking db_path and
                                            _resolve_primary_cancer calling
                                            load_registry()

    oncotriage/paths.py   resolution is LAZY now. It used to run at import, so
                          `import oncotriage.config` raised on any machine
                          without the sibling directory tree. See check 2b.

THE CYCLE THAT MADE THIS NON-TRIVIAL
------------------------------------
    '02- Utility Functions.py' read PRICING_CONFIG, COLLECTION_NAME,
                               qdrant_client and DATA_SNAPSHOT_DATE from File 03
    '03- Config.py'            called load_env_keys(), from File 02, at line 194

Under exec() into one shared namespace both directions resolve at call time and
nothing complains. As modules it is an ImportError. load_env_keys moved out of
the pair -- to oncotriage.settings in pass 20c-1, and to oncotriage.paths in
pass 20c-2a, beside the keys_path it defaults to, which is what let its own
import stop being deferred into a function body.

WHAT THIS FILE CHECKS, and how each check could fail
----------------------------------------------------
  1. THE CYCLE IS GONE. oncotriage.config and oncotriage.utils import cleanly in
     BOTH orders, from a directory that is not the code directory. Structurally,
     config.py's AST contains no reference to oncotriage.utils anywhere --
     module level or inside a function body.

     NEGATIVE CONTROL, and it changed what this file claims. A COPY of the
     package with `from oncotriage.utils import get_model_cost` added back to
     config.py is caught by the structural check, and `import oncotriage.utils`
     against it dies with "most likely due to a circular import" -- but
     `import oncotriage.config` against the same copy SUCCEEDS. A reintroduced
     cycle is order-dependent, so the import-order pair is a smoke test and the
     STRUCTURAL check is the actual guard. Both halves are asserted, including
     the one that is inconvenient.

  1b. NO ONCOTRIAGE MODULE IMPORTS ANOTHER FROM A FUNCTION BODY. An ast walk of
     every file in the package, ignoring third-party imports -- File 08's
     `import icd10` inside _build_icd10_cancer_sets() is deliberate and must
     stay. NEGATIVE CONTROL: a copy with a deferred package import added to
     settings.resolve_keys_path() is caught, AND is shown to still import
     cleanly, which is the whole reason a static scan is needed for this one.

  2. IMPORTING TOUCHES NOTHING LIVE. A subprocess replaces socket.socket with a
     class that raises on construction, and socket.create_connection,
     sqlite3.connect, builtins.open and io.open with functions that raise, then
     imports all thirteen package modules. Proved by patching, not by reading the
     source. Every trap is fired afterwards and must raise, so a run where the
     patches silently did nothing fails instead of passing vacuously. The `open`
     traps arrived with oncotriage.registries.mesh, whose load_mesh_filter()
     reads four JSON lookups and must do it in a function.

  2b. PATH RESOLUTION IS LAZY. A subprocess with ONCOTRIAGE_MAIN_PATH pointed at
     a directory that does not exist must still `import oncotriage.config` and
     read MAX_WORKERS out of it, and must resolve NO path at import — the fix
     for a defect pass 20c-2a shipped, where importing config globbed the whole
     sibling tree and raised on any machine that did not have it. Section 2
     could not see this: glob.glob() uses os.scandir, not open(). NON-DEGENERATE
     BOTH WAYS: reading a path against the unreachable root must still raise
     with a message naming the variable, and the same read against the real root
     must return a directory that exists.

  3. THE CLIENT FACTORIES ARE LAZY AND CACHED. Counting fakes are installed over
     oncotriage.config.OpenAI and .QdrantClient before any call. Construction
     count must be 0 after import (lazy), 1 after the first call, still 1 after
     the second (cached), and the two returned objects must be the same object.
     Identity alone would also hold for a module-level singleton built at
     import, which is exactly what this pass removed -- the 0-after-import count
     is what separates them.

  4. get_age_reference_date RESOLVES DATA_SNAPSHOT_DATE BY IMPORT AND STILL
     RAISES. A COPY of the package has its config.py rewritten to a partial date
     ("2026-08"); a subprocess against the copy must raise ValueError naming the
     constant, never fall back to today(). The copy is then rewritten back and
     shown to return date(2026, 8, 3) again. Nothing is edited in place.

  5. NO NAME WAS DROPPED. Two inventories, because the two passes need different
     evidence. Files 01/02/03 are checked against an ast-derived list from
     commit 3780ba1. Files 08/09/10 are checked against a RUNTIME-derived list:
     each was exec'd into a throwaway namespace before the move and every
     binding recorded, because File 08 assigns _seen_canonical at module level
     and then deletes it, and an ast list would have re-exported a name that
     never existed. Both directions are asserted -- nothing missing, nothing
     added.

  2f. EXACTLY ONE CONSTRUCTION SITE FOR THE BM25 SPARSE MODEL, counted by ast
     over every package file, with a negative control that plants a second one
     in a copy and shows the detector finds it. Both sides of the model -- the
     indexer that writes the document vectors and the agent that encodes the
     query scored against them -- must reach the same accessor. Two independent
     loaders of a token-ID vocabulary is a silent retrieval-quality failure: the
     dot product still computes, nothing raises, no counter moves.

  2g. NO FUNCTION-LOCAL SHADOWS A MODULE-LEVEL IMPORT. An ast scan over every
     package file. This caught two real defects during pass 3a -- a `config`
     local in stage1_index_health() and an `embedding` loop variable in
     _flush_embed_buffer() -- each of which would have turned a module attribute
     read into UnboundLocalError at RUN time, invisibly to any import test.
     NEGATIVE CONTROL: a copy with the module-level `import config` put back is
     shown to be caught.

  5b. THE FILE 10 SPLIT HAS EXACTLY ONE SHARED NAME. Re-derived against the
     shipped modules rather than asserted in a comment: stage.py and
     histology.py must reference nothing the other defines, and the one name
     they both reach for must be _is_negated, out of negation.py.

  5c. THE LAZY DEPENDENCY PROXY ANSWERS FOR WHAT IT WRAPS. _LazyAgentDependency
     forwarded __getattr__ and __call__ only, so bool(), len(), iter(), `in`,
     `==`, hash() and repr() answered about the WRAPPER. == in particular
     answered False when the wrapped object WAS the operand, which is the exact
     question a fixture harness asks of this seam. Demonstrated against a copy
     of the class with the six delegations stripped, which must get them wrong.

  6. THE THREE LATE-BINDING WRAPPERS STILL BIND LATE. File 02's shim is exec'd
     into a throwaway namespace holding a fake PRICING_CONFIG, a stub Qdrant
     client and a DATA_SNAPSHOT_DATE that differs from the package's. All three
     wrappers must use the namespace's values, because '36- Logging Contract
     Test.py', '37- Retrieval Observability Test.py', '38- Birth Date and
     Demographics Parser Test.py', 'fixture_capture.py' and
     'fixture_replay.py' all depend on exactly that.

WHY THIS FILE DOES NOT EXEC-CHAIN 01 AND 02
-------------------------------------------
Every other test file starts by exec'ing "01- Imports.py". This one must not:
File 01 imports torch, transformers, streamlit and langgraph into the process,
and check 2 asserts those are ABSENT after a package import. A test of import
purity that first imports everything would be measuring its own bootstrap. So
this file imports the standard library it needs directly, and every check that
needs the package runs in a SUBPROCESS.

NO NETWORK, NO MODEL, NO DATABASE, NO API KEY. Every check here runs without
credentials. That is deliberate: it is the only test in the suite that can run
on a fresh checkout before a .env exists.

Run from terminal (or F5 in Spyder):
    python tests/test_package_invariants.py
    (was: python "47- Package Split Test.py")

Exit codes:
    0 -- every check passed
    1 -- at least one check failed
"""

import ast
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from datetime import date


# THE REPOSITORY ROOT IS THE PARENT OF THIS FILE'S DIRECTORY (pass 20d-2). It
# used to be this file's own directory, which was right while the file sat in
# the code directory and is one level off from tests/.
#
# IT IS NOT DERIVED FROM `oncotriage.__file__`, and here the reason is stronger
# than for the other two files that decline that derivation. THIS FILE MUST NOT
# IMPORT THE PACKAGE AT ALL: section 2 asserts that importing it pulls in no
# model-bearing library and opens nothing, and it proves that by arming traps in
# a SUBPROCESS that has imported nothing yet. Importing oncotriage here to find
# a directory would put the package in this process's sys.modules before a
# single trap was set -- measuring its own bootstrap, which is the defect the
# "WHY THIS FILE DOES NOT EXEC-CHAIN 01 AND 02" note below exists to avoid.
#
# The guard below is what replaces the import: the package directory must be
# where this derivation says it is, or nothing after it means anything.
if "__file__" in globals():
    _code_dir = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))) + os.sep
else:
    _code_dir = os.getcwd() + os.sep
    print(f"[Bootstrap] __file__ unbound; using the working directory as the code directory: {_code_dir}")

_PKG_DIR = os.path.join(_code_dir, "oncotriage")

# NOT a check(): every one of the 283 checks below reads either _PKG_DIR or a
# path under _code_dir, so a wrong root is not one failure, it is all of them,
# each with a misleading message. It fails here instead, naming the directory.
if not os.path.isdir(_PKG_DIR):
    raise AssertionError(
        f"the oncotriage package is not where this file expects it: {_PKG_DIR}. "
        f"The repository root was derived as {_code_dir!r} from this file's own "
        f"location (tests/ -> its parent), so either this file moved or the "
        f"package did."
    )


#------------------------------------------------------------------------------


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================
# Same shape as Files 33, 42, 43 and 44: record every outcome, never abort on
# the first failure, exit non-zero at the end.

_RESULTS = {"passed": 0, "failed": 0, "skipped": 0}
_FAILURES = []
_SKIPS = []


def check(label: str, actual, expected) -> None:
    """Assert equality, record the outcome, never abort the run."""
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected: {expected}\n          actual:   {actual}")
        print(f"  FAIL  {label}")


def fail(label: str, detail: str) -> None:
    """Record an outright failure that is not an equality comparison."""
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}")


def skip(label: str, reason: str) -> None:
    """Record coverage that could NOT be exercised on this platform.

    A SKIP IS NOT A PASS AND IS NEVER COUNTED AS ONE. It has its own counter,
    its own line in the summary and its own list, so a run that could not
    exercise something says so instead of reporting a smaller green number that
    reads identically to a full one. It does not affect the exit code: the
    thing skipped is not broken, it is absent.

    The only caller today is the `caffeine` guard -- see the pre-import in
    _PURITY. `caffeine` is a macOS-only dependency (pyproject declares it
    `sys_platform == "darwin"`), so on Linux there is no caffeine import for
    the purity probes to have to arrange around, and the coverage that the
    package's own guarded import of it stays clean is genuinely unavailable
    rather than passing.
    """
    _RESULTS["skipped"] += 1
    _SKIPS.append(f"{label}\n          {reason}")
    print(f"  SKIP  {label}")


#------------------------------------------------------------------------------



def _bound_names(path: str) -> set:
    """Every top-level name a module binds: defs, classes, assignments, imports."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _run(code: str, cwd: str, extra_path: str = None):
    """Run `code` in a subprocess. Returns (returncode, stdout, stderr)."""
    env = dict(os.environ)
    if extra_path:
        env["PYTHONPATH"] = extra_path + os.pathsep + env.get("PYTHONPATH", "")
    else:
        env.pop("PYTHONPATH", None)
    proc = subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env,
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _last_json(stdout: str):
    """Parse the last line of stdout as JSON.

    The package prints path-resolution lines on import, so a subprocess's
    result cannot be the whole of stdout. Returns None if the last line is not
    JSON, which the caller must report rather than swallow.
    """
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                return None
    return None


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("PACKAGE SPLIT TEST — item 20c")
print("=" * 78)
print(f"  code directory:  {_code_dir}")
print(f"  package:         {_PKG_DIR}")


# ===========================================================================
# 0. THE PACKAGE IS IMPORTABLE FROM A DIRECTORY THAT IS NOT THE CODE DIRECTORY
# ===========================================================================

print("\n" + "=" * 78)
print("0. `pip install -e .` — import oncotriage.config from anywhere")
print("=" * 78)

_ELSEWHERE = tempfile.mkdtemp(prefix="oncotriage_pkgtest_")

_rc, _out, _err = _run(
    "import oncotriage.config as c; import json; print(json.dumps({'name': c.Project_Name}))",
    cwd=_ELSEWHERE)
check("import oncotriage.config succeeds from a foreign working directory, "
      "with PYTHONPATH unset", _rc, 0)
if _rc != 0:
    fail("the editable install is in place",
         f"`pip install -e .` from {_code_dir} is what makes this work.\n"
         f"          stderr tail: {_err.strip().splitlines()[-1:]}")
    # Every later subprocess still needs to run, so fall back to PYTHONPATH and
    # SAY SO. A silent fallback here would make the rest of this file report on
    # an arrangement nobody is actually shipping.
    _FALLBACK_PATH = _code_dir
    print(f"  [Fallback] adding {_code_dir} to PYTHONPATH for the remaining checks")
else:
    _FALLBACK_PATH = None
    _payload = _last_json(_out)
    check("...and the module that answered is the real one",
          (_payload or {}).get("name"), "OncoTriage Agent")


# ===========================================================================
# 1. THE CYCLE IS GONE
# ===========================================================================

print("\n" + "=" * 78)
print("1. config and utils import in both orders; config never imports utils")
print("=" * 78)

for _first, _second in (("config", "utils"), ("utils", "config")):
    _rc, _out, _err = _run(
        f"import oncotriage.{_first}; import oncotriage.{_second}; print('{{\"ok\": true}}')",
        cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
    check(f"oncotriage.{_first} then oncotriage.{_second} imports cleanly", _rc, 0)
    if _rc != 0:
        fail(f"import order {_first} -> {_second}",
             f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-3:]}")


def _mentions_module(path: str, dotted: str) -> bool:
    """True if `path`'s AST imports `dotted` ANYWHERE — including inside a
    function body, which is where a deferred import would hide."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "") == dotted or (node.module or "").startswith(dotted + "."):
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == dotted or alias.name.startswith(dotted + "."):
                    return True
        # `from oncotriage import utils` — the module is the package, the name
        # is the submodule. Caught here rather than above.
        if isinstance(node, ast.ImportFrom) and (node.module or "") == "oncotriage":
            for alias in node.names:
                if "oncotriage." + alias.name == dotted:
                    return True
    return False


_CONFIG_PY = os.path.join(_PKG_DIR, "config.py")
_UTILS_PY = os.path.join(_PKG_DIR, "utils.py")

check("oncotriage/config.py does not import oncotriage.utils, anywhere",
      _mentions_module(_CONFIG_PY, "oncotriage.utils"), False)

# NON-DEGENERATE. The check above would also pass on a config.py with no
# imports at all, or on a detector that never returns True. Both are ruled out
# here: config must import settings, and utils must import config.
check("...and the detector is not vacuous: config DOES import oncotriage.paths",
      _mentions_module(_CONFIG_PY, "oncotriage.paths"), True)
check("...and utils DOES import oncotriage.config (the surviving direction)",
      _mentions_module(_UTILS_PY, "oncotriage.config"), True)


# --- NEGATIVE CONTROL: put the cycle back, in a COPY, and watch it bite ------
# CLAUDE.md: an assertion that has only ever passed is not evidence that it can
# catch anything. A copy of the package gets the removed edge added back to
# config.py.
#
# WHAT THIS CONTROL ACTUALLY FOUND, and it changed the design of this file.
# A reintroduced config -> utils import is ORDER-DEPENDENT:
#
#   import oncotriage.utils   -> ImportError: cannot import name
#                                'get_model_cost' from partially initialized
#                                module 'oncotriage.utils' (most likely due to
#                                a circular import)
#   import oncotriage.config  -> SUCCEEDS, silently
#
# The second one survives because config's cycle edge runs before utils has
# defined anything, while utils' own `from oncotriage import config` resolves
# to the half-built module in sys.modules and never touches an attribute until
# call time. So the import-order checks above are NOT a guard against the cycle
# coming back: they would both pass with it in place. The STRUCTURAL check is
# the guard, and this control is what demonstrates the difference rather than
# assuming it. Both facts are asserted below, including the uncomfortable one.

print("\n  Negative control: reintroducing the cycle in a COPY of the package")

_BROKEN_ROOT = tempfile.mkdtemp(prefix="oncotriage_cycle_")
shutil.copytree(_PKG_DIR, os.path.join(_BROKEN_ROOT, "oncotriage"))
_BROKEN_CONFIG = os.path.join(_BROKEN_ROOT, "oncotriage", "config.py")

_src = open(_BROKEN_CONFIG, encoding="utf-8").read()

# LINE-ANCHORED, and that is a repair rather than a tidy-up. The needle used to
# be the bare substring "from oncotriage import paths", and the Docker pass added
# a second name to that import in config.py. Written as `from oncotriage import
# paths, settings`, the substring still MATCHED -- in the middle of the line --
# so the control spliced its planted import into the middle of a statement and
# produced
#
#     from oncotriage import paths
#     from oncotriage.utils import get_model_cost, settings
#
# which is a SyntaxError-free import of a name utils does not export. The copied
# package then failed to import for a reason that had nothing to do with the
# cycle, and the check below -- whose whole point is that this order SUCCEEDS --
# reported a failure that was true of the control and false of the package.
#
# A control that plants the wrong thing is worse than no control: it fails, so
# it looks like it is working. Anchoring on the newlines means a future edit to
# that import line finds NO match and hits the `fail()` underneath, which says
# so by name.
_needle = "\nfrom oncotriage import paths\n"
if _needle not in _src:
    fail("the negative control can find its insertion point",
         f"{_needle!r} is not in the copied config.py; this control is not "
         f"testing what it claims to")
else:
    # The needle carries its own trailing newline now, so the planted line has
    # to carry one too -- without it the plant is concatenated onto whatever
    # follows and the copy is a SyntaxError rather than a cycle.
    _planted = _needle + "from oncotriage.utils import get_model_cost\n"
    open(_BROKEN_CONFIG, "w", encoding="utf-8").write(
        _src.replace(_needle, _planted, 1))

    check("the structural detector CATCHES a reintroduced config -> utils import",
          _mentions_module(_BROKEN_CONFIG, "oncotriage.utils"), True)

    # The order that exposes it.
    _rc_u, _out_u, _err_u = _run("import oncotriage.utils", cwd=_ELSEWHERE,
                                 extra_path=_BROKEN_ROOT)
    check("with the cycle back, `import oncotriage.utils` FAILS", _rc_u != 0, True)
    check("...and it fails AS a circular import, not as something else",
          "circular import" in _err_u or "partially initialized module" in _err_u, True)

    # The order that hides it. Asserted, not glossed over: this is why the
    # structural check exists and why check 1's import-order pair is a smoke
    # test rather than the guard.
    _rc_c, _out_c, _err_c = _run("import oncotriage.config", cwd=_ELSEWHERE,
                                 extra_path=_BROKEN_ROOT)
    check("...while `import oncotriage.config` still succeeds, which is exactly "
          "why the structural check is the guard", _rc_c, 0)

    if _rc_u == 0:
        fail("the negative control actually broke something",
             "the copied package with the cycle restored imported cleanly in "
             "BOTH orders, so neither the import checks nor this control is "
             "detecting anything")

shutil.rmtree(_BROKEN_ROOT, ignore_errors=True)


# ===========================================================================
# 1b. NO ONCOTRIAGE MODULE IMPORTS ANOTHER FROM INSIDE A FUNCTION BODY
# ===========================================================================

print("\n" + "=" * 78)
print("1b. every oncotriage -> oncotriage import is at module scope")
print("=" * 78)

# WHY THIS RULE EXISTS. Pass 20c-1 put load_env_keys in oncotriage.settings and
# reached keys_path through `from oncotriage.paths import keys_path` written
# INSIDE the function body, because paths imports settings and a module-scope
# import would have been a cycle. It worked. It was also invisible: check 1
# above reads config.py's import block, and a dependency that is not in an
# import block cannot be seen by any scan of import blocks. Pass 20c-2a moved
# the function to paths, where the value already lives, and made the absence of
# deferred package imports a checked property rather than a habit.
#
# THIRD-PARTY IMPORTS IN FUNCTION BODIES ARE NOT COVERED AND MUST NOT BE.
# cancer_code_registry._build_icd10_cancer_sets() does `import icd10` in its
# body on purpose: hoisting it would make importing the registry load the whole
# ICD-10-CM release, which is exactly the import-time work section 2 proves the
# package does not do. The rule is about oncotriage-to-oncotriage edges, which
# are the ones that form cycles and the ones a reader needs the import block to
# be honest about.

_PKG_FILES = sorted(
    os.path.join(root, name)
    for root, _dirs, files in os.walk(_PKG_DIR)
    for name in files
    if name.endswith(".py") and "__pycache__" not in root
)

check("the package file list is non-empty and covers all six subpackages",
      len(_PKG_FILES) >= 75
      and any(f.endswith("registries/mesh.py") for f in _PKG_FILES)
      and any(f.endswith("extraction/negation.py") for f in _PKG_FILES)
      and any(f.endswith("fhir/parser.py") for f in _PKG_FILES)
      and any(f.endswith("storage/database_logger.py") for f in _PKG_FILES)
      and any(f.endswith("agent/deps.py") for f in _PKG_FILES)
      and any(f.endswith("retrieval/indexer.py") for f in _PKG_FILES)
      and any(f.endswith("embedding.py") for f in _PKG_FILES)
      # Pass 20c-3c-1: the dashboard. Named here so the two subpackages cannot
      # vanish from the tree without this scan noticing.
      and any(f.endswith("dashboard/data.py") for f in _PKG_FILES)
      and any(f.endswith("dashboard/tabs/reproducibility.py") for f in _PKG_FILES)
      # Pass 20c-3c-2: orchestration, and the Qdrant backup that joined
      # retrieval. Named for the same reason as the dashboard pair above.
      and any(f.endswith("orchestration/dag_generator.py") for f in _PKG_FILES)
      and any(f.endswith("orchestration/airflow_manager.py") for f in _PKG_FILES)
      and any(f.endswith("retrieval/qdrant_backup.py") for f in _PKG_FILES)
      # Pass 20c-3d: the last three subpackages. fixtures/capture.py is named
      # here for the same reason dashboard/data.py is -- it is the module whose
      # disappearance would take the whole characterization baseline with it,
      # and it is the one module in the package that carries a database
      # tripwire.
      and any(f.endswith("ablation/study.py") for f in _PKG_FILES)
      and any(f.endswith("evaluation/cohort_diff.py") for f in _PKG_FILES)
      and any(f.endswith("fixtures/capture.py") for f in _PKG_FILES)
      and any(f.endswith("fixtures/replay.py") for f in _PKG_FILES),
      True)

# EVERY SUBPACKAGE MUST BE DECLARED IN pyproject.toml. setuptools does not
# recurse into a listed package, so a subpackage present in the tree and absent
# from the `packages` list is importable from an EDITABLE install (which maps
# the source tree) and MISSING from a built wheel. That difference does not
# surface until someone builds one. Read as text rather than with a TOML parser
# because tomllib would only tell us the list parses, not that it matches the
# directory tree.
_PYPROJECT = open(os.path.join(_code_dir, "pyproject.toml"), encoding="utf-8").read()

# RECURSIVE AS OF PASS 20c-3i, and the one-level version had already been
# outrun by the tree it was scanning.
#
# It was an os.listdir of _PKG_DIR, so it could only ever see subpackages one
# directory deep. oncotriage.dashboard.tabs has been nested since pass 20c-3c-1
# -- which noticed, and answered by asserting that ONE nested name separately,
# as a literal string search against pyproject.toml. That works for exactly as
# long as somebody remembers to add a line per nesting, which is the same bet
# the pyproject `packages` list itself makes and the reason this check exists to
# cover it. The second nested subpackage would have been invisible to both
# halves: absent from the listdir, so absent from the "every subpackage on disk
# is declared" comparison, and absent from the hand-written string search too.
#
# The consequence is not a test failure, it is a shipping defect: setuptools
# does not recurse into a listed package, so an undeclared subpackage is present
# in an EDITABLE install (which maps the source tree) and MISSING from a built
# wheel. That difference does not surface until someone builds one.
#
# os.walk instead, keyed on the presence of __init__.py, to any depth.
_SUBPACKAGE_DIRS = sorted(
    "oncotriage." + os.path.relpath(root, _PKG_DIR).replace(os.sep, ".")
    for root, _dirs, files in os.walk(_PKG_DIR)
    if "__init__.py" in files and os.path.abspath(root) != os.path.abspath(_PKG_DIR)
    and "__pycache__" not in root
)
check("the tree has the subpackages this pass expects (non-degeneracy)",
      _SUBPACKAGE_DIRS,
      # api, batch and monitoring are new in pass 20c-3b -- the serving layer.
      # dashboard is new in pass 20c-3c-1. orchestration is new in pass
      # 20c-3c-2 -- Files 22, 23 and 24, the Airflow layer. (File 29 landed in
      # oncotriage.retrieval, which was already here, so that pass adds exactly
      # one name.) oncotriage.dashboard.tabs is in this list for the first time
      # in pass 20c-3i: the scan above reaches it now instead of it needing a
      # hand-written check of its own.
      # ablation, evaluation and fixtures are new in pass 20c-3d, the last
      # conversion pass: Files 26 and 27, Files 28 and 34, and Files 45 and 46.
      # oncotriage.mcp is the MCP pass: the stdio Model Context Protocol server
      # over the same pipeline the API serves. It is named beside the
      # third-party `mcp` it imports, which is safe because Python 3 resolves
      # imports absolutely; tests/test_mcp_server_stdio_contract.py section 1
      # fires that rather than arguing it.
      ["oncotriage.ablation", "oncotriage.agent", "oncotriage.api",
       "oncotriage.batch",
       "oncotriage.dashboard", "oncotriage.dashboard.tabs",
       "oncotriage.evaluation", "oncotriage.extraction", "oncotriage.fhir",
       "oncotriage.fixtures", "oncotriage.mcp",
       "oncotriage.monitoring", "oncotriage.orchestration",
       "oncotriage.registries", "oncotriage.retrieval",
       "oncotriage.storage"])
check("the scan reaches NESTED subpackages, not just the top level -- "
      "oncotriage.dashboard.tabs is two deep and setuptools does not recurse "
      "into a listed package",
      "oncotriage.dashboard.tabs" in _SUBPACKAGE_DIRS, True)
check("every subpackage on disk is declared in pyproject.toml, so a built "
      "wheel carries it",
      sorted(p for p in _SUBPACKAGE_DIRS if f'"{p}"' not in _PYPROJECT), [])

# NEGATIVE CONTROL, and the reason it plants at DEPTH TWO: the check above is
# one that already existed in a one-level form and passed. A control that
# planted a top-level subpackage would have fired against the OLD scan too and
# would prove nothing about what this pass changed. This one is invisible to a
# listdir and visible to the walk.
_NESTED_CONTROL_ROOT = tempfile.mkdtemp(prefix="oncotriage-nested-")
try:
    _control_pkg = os.path.join(_NESTED_CONTROL_ROOT, "oncotriage")
    shutil.copytree(_PKG_DIR, _control_pkg,
                    ignore=shutil.ignore_patterns("__pycache__"))
    _planted = os.path.join(_control_pkg, "dashboard", "tabs", "planted")
    os.makedirs(_planted)
    open(os.path.join(_planted, "__init__.py"), "w").close()

    _control_found = sorted(
        "oncotriage." + os.path.relpath(root, _control_pkg).replace(os.sep, ".")
        for root, _dirs, files in os.walk(_control_pkg)
        if "__init__.py" in files
        and os.path.abspath(root) != os.path.abspath(_control_pkg)
        and "__pycache__" not in root
    )
    check("the recursive scan FINDS a subpackage planted three deep, where a "
          "one-level os.listdir would see nothing (negative control)",
          "oncotriage.dashboard.tabs.planted" in _control_found, True)
    check("...and it is undeclared in pyproject.toml, so the wheel check "
          "catches it too",
          sorted(p for p in _control_found if f'"{p}"' not in _PYPROJECT),
          ["oncotriage.dashboard.tabs.planted"])
    check("...while the one-level listdir the old scan used misses it entirely, "
          "which is what makes this control discriminating",
          "oncotriage.dashboard.tabs.planted" in sorted(
              "oncotriage." + name for name in os.listdir(_control_pkg)
              if os.path.isfile(os.path.join(_control_pkg, name, "__init__.py"))),
          False)
finally:
    shutil.rmtree(_NESTED_CONTROL_ROOT, ignore_errors=True)


def _function_body_imports(path: str):
    """Every import statement nested inside a def/class in `path`.

    Returns [(qualified_module, enclosing_name, lineno)]. A relative import
    (``from . import x``) counts as an oncotriage import: node.level > 0 means
    it can only resolve inside this package.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    found = []
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for node in ast.walk(scope):
            if isinstance(node, ast.ImportFrom):
                module = ("." * node.level) + (node.module or "")
                found.append((module, scope.name, node.lineno))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    found.append((alias.name, scope.name, node.lineno))
    return found


def _deferred_package_imports(paths):
    """Function-body imports that resolve inside the oncotriage package."""
    out = []
    for path in paths:
        for module, scope, lineno in _function_body_imports(path):
            if module.startswith(".") or module == "oncotriage" or module.startswith("oncotriage."):
                out.append(f"{os.path.relpath(path, _code_dir)}:{lineno} "
                           f"in {scope}() -> {module}")
    return sorted(out)


# NON-DEGENERATE FIRST. The check below passes on an empty list, and an empty
# list is exactly what a broken walker returns. So: the walker must find at
# least one function-body import somewhere in the package, and it must find the
# specific one that is supposed to be there.
_ALL_BODY_IMPORTS = sorted(
    f"{os.path.relpath(p, _code_dir)}:{ln} in {scope}() -> {mod}"
    for p in _PKG_FILES for mod, scope, ln in _function_body_imports(p)
)
check("the walker finds function-body imports at all (non-degeneracy)",
      len(_ALL_BODY_IMPORTS) >= 1, True)
check("...specifically the deliberate third-party one, `import icd10` inside "
      "_build_icd10_cancer_sets",
      any("_build_icd10_cancer_sets() -> icd10" in e for e in _ALL_BODY_IMPORTS), True)

check("no oncotriage module imports another oncotriage module from a "
      "function body", _deferred_package_imports(_PKG_FILES), [])

# --- NEGATIVE CONTROL: put a deferred package import back, in a COPY --------
# Section 1's control showed that a reintroduced module-scope cycle is
# order-dependent and can import cleanly. A DEFERRED one is worse: it never
# fails at import at all, in any order, because it does not run until the
# function is called. Nothing but this scan would notice it, so this scan has
# to be shown to notice it.

print("\n  Negative control: reintroducing a deferred package import in a COPY")

_DEFERRED_ROOT = tempfile.mkdtemp(prefix="oncotriage_deferred_")
shutil.copytree(_PKG_DIR, os.path.join(_DEFERRED_ROOT, "oncotriage"))
_DEFERRED_SETTINGS = os.path.join(_DEFERRED_ROOT, "oncotriage", "settings.py")

# resolve_keys_path, deliberately: it is NOT called while paths.py is being
# imported. resolve_main_path IS -- putting the deferred import there makes the
# copy fail at import with a genuine partially-initialized-module error, which
# would be a different (and louder) defect than the silent one this control is
# about.
_src = open(_DEFERRED_SETTINGS, encoding="utf-8").read()
_needle = "def resolve_keys_path(fallback):\n"
if _needle not in _src:
    fail("the deferred-import control can find its insertion point",
         f"{_needle!r} is not in the copied settings.py; this control is not "
         f"testing what it claims to")
else:
    open(_DEFERRED_SETTINGS, "w", encoding="utf-8").write(_src.replace(
        _needle,
        _needle + '    """Reintroduced deferred import."""\n'
                  "    from oncotriage.paths import keys_path  # reintroduced\n", 1))

    _copied = sorted(
        os.path.join(root, name)
        for root, _dirs, files in os.walk(os.path.join(_DEFERRED_ROOT, "oncotriage"))
        for name in files
        if name.endswith(".py") and "__pycache__" not in root
    )
    _caught = _deferred_package_imports(_copied)
    check("the scan CATCHES a reintroduced deferred package import",
          len(_caught), 1)
    check("...and names the file, the function and the module it found",
          bool(_caught) and "settings.py" in _caught[0]
          and "resolve_keys_path()" in _caught[0]
          and "oncotriage.paths" in _caught[0], True)

    # And the reason the scan is needed at all: the copy still imports fine.
    _rc, _out, _err = _run("import oncotriage.settings, oncotriage.paths; print('{}')",
                           cwd=_ELSEWHERE, extra_path=_DEFERRED_ROOT)
    check("...while the copy still imports cleanly in both directions, which is "
          "why a runtime check could never find this", _rc, 0)

shutil.rmtree(_DEFERRED_ROOT, ignore_errors=True)


# ===========================================================================
# 1c. THE EXEC CHAIN IS DEAD  (pass 20e)
# ===========================================================================

print("\n" + "=" * 78)
print("1c. nothing exec's a numbered file, chains one, or loads one by location")
print("=" * 78)

# WHAT THIS REPLACES, AND WHY IT IS NOT A SMALLER CHECK THAN WHAT IT REPLACES.
#
# Until pass 20e this file carried four pinned inventories -- the names Files
# 01/02/03 bound before item 20c, and the RUNTIME names Files 05, 07, 08, 09,
# 10, 13 and 14 bound before passes 2a/2b/2c/3a -- and a probe that exec'd each
# shim into a bare namespace and compared what it bound against its inventory.
# Those checks answered one question: "does the shim still deliver to the exec
# chain what the exec chain reads out of it".
#
# THE EXEC CHAIN NO LONGER EXISTS, so the question has no subject. Pass 20e
# measured every consumer of every shim -- each re-exported name, and each
# shim's FILENAME as a string, against every .py, .md, .toml and .yml in the
# tree -- found the last five chainers (05, 09, 13, 18, 19) were chaining for
# consumers that had themselves been converted one or two passes earlier, and
# deleted seven files: 01, 02, 03, 08, 10, 14 and oncotriage_settings.py. The
# other five became thin entry points.
#
# A retired inventory is a check that stopped running, so this section is what
# takes its place, and it is STRICTLY WIDER in the one way that matters: the
# inventories asserted a property of ten named files, and this asserts a
# property of EVERY file in the repository, including files written tomorrow.
# The old checks could not have caught a new file that started exec'ing; this
# one does.
#
# THE FIRST VERSION OF THIS SCAN HAD THREE DEFECTS AND ITS OWN CONTROLS FOUND
# ALL THREE, WHICH IS THE ARGUMENT FOR CONTROLS RESTATED AS AN EVENT:
#
#   1. It matched the bare substring "exec_chain" inside any string literal, so
#      it reported nine DOCSTRINGS -- including the one you are reading, and
#      including oncotriage/utils.py's record of why exec_chain was deleted --
#      as violations. The documentation of the fix read as the defect, which is
#      the exact trap the drift test's `inferences_path` check already carries a
#      note about. The literal search now requires a CALL shape.
#
#   2. Its "raw exec of a numbered file" arm looked for a numbered filename
#      INSIDE the exec() call. Not one of the five real bootstraps was written
#      that way -- every one of them was `open(_code_dir + "01- Imports.py")` on
#      one line and `exec(_fh.read(), globals())` on the next, or a `for` loop
#      over a tuple of filenames. So the arm would have missed all five files it
#      was written to catch, and it took the negative control to say so.
#
#   3. Rewritten to match code-shaped SUBSTRINGS inside string literals, it
#      then missed `ns["exec_chain"](...)` -- the subscript form, which is the
#      form the retired shim probe itself used -- because the quote characters
#      sit between the name and the paren, so "exec_chain(" is not a substring
#      of it. String literals are RE-PARSED as Python now, which catches every
#      call form at once and, as a side effect, stops reporting prose.
#
# WHAT IT DOES INSTEAD, and why this shape cannot miss the real thing: you
# cannot run a numbered file's text without exec(), runpy, or an importlib
# by-location load. So the check is on the MECHANISM rather than on the
# filename:
#
#   Form A  a call whose unparsed callee mentions exec_chain,
#           spec_from_file_location, SourceFileLoader or runpy. Unparsed, so
#           the SUBSCRIPT form ns["exec_chain"](...) is seen -- which is not
#           hypothetical, it is the form the retired shim probe used.
#   Form B  any call to the exec builtin, anywhere, outside a closed and argued
#           allowlist.
#   Form C  either of the above appearing inside a STRING LITERAL, found by
#           RE-PARSING the literal as Python and running Forms A and B over the
#           result. A subprocess probe's source is an ordinary string to any AST
#           walk; prose does not parse, so it is not reported.
#
# THIS FILE IS EXCLUDED FROM THE FORM C SEARCH, for the same reason check 2h
# excludes it from its read corpus: the paragraph above names every pattern the
# scan looks for, so counting this file's own strings would report it as the
# violator.

_CHAIN_CALL_MARKERS = ("exec_chain", "spec_from_file_location",
                       "SourceFileLoader", "runpy")

# Cheap pre-filter before the expensive step below. A string literal that does
# not mention any of these cannot be an exec-chain bootstrap, and most of this
# repository's string literals are prose.
_STRING_PREFILTER = ("exec", "spec_from_file_location", "SourceFileLoader",
                     "runpy")

# THE exec() ALLOWLIST IS CLOSED AND HAS FIVE MEMBERS, EACH WITH AN ARGUMENT.
#
# tests/test_storage_query_layer.py unparses two PRE-FIX functions out of a git
# blob and exec's them into a throwaway namespace, so that its negative controls
# run the code item 38 replaced rather than a retyped copy of it. That is a
# legitimate exec of text that is not a file in the working tree, and it is the
# only one. An entry added here has to carry the same kind of argument.
#
# tests/test_observability_logging.py (the structured-logging pass) exec's a
# PATCHED COPY of oncotriage/observability.py to plant two defects it then
# requires to fire: the correlation ID as a module-level global instead of a
# ContextVar, and the field allowlist not being consulted. The argument is the
# same shape and it is CLAUDE.md's own instruction: "prefer a demonstration
# that mutates a COPY of the source and execs it over one that edits a file in
# place". The alternative -- editing observability.py, running, restoring -- is
# the in-place shape that cost pass 20d-1 an edit to config.py, and it would put
# a window in the run during which the shipped logger is wrong. Nothing here
# execs a file in the working tree, which is what this section is about.
# tests/test_indexer_admission_filters.py (the scrape-admission pass) compiles
# ONE named top-level function out of a git blob of the pre-fix
# oncotriage/retrieval/indexer.py and exec's it into a throwaway namespace, so
# that its negative controls run the splitter that actually shipped rather than
# a retyped paraphrase of it. Same argument as test_storage_query_layer.py, and
# the same reason it cannot simply import the old module: the pre-fix indexer
# imports openai and qdrant_client at module scope, so importing it to reach one
# pure string function would build neither client but would drag both libraries
# in. Only the one FunctionDef is compiled, into a namespace holding `re`.
# Nothing here execs a file in the working tree.
#
# tests/test_extraction_histology.py (Test 9) and
# tests/test_agent_age_units_and_sex_filter.py both exec a PATCHED COPY of a
# shipped module -- histology.py with re.IGNORECASE stripped from the two lung
# abbreviation patterns, filtering.py with the age-unit conversion or the sex
# predicate reverted -- to plant the defects their controls then require to
# fire. Same argument as test_observability_logging.py, and the same CLAUDE.md
# instruction: "prefer a demonstration that mutates a COPY of the source and
# execs it over one that edits a file in place".
#
# THE SECOND FILE COULD NOT USE THE git show ROUTE THE OTHER THREE MEMBERS USE,
# and the reason is worth recording rather than being rediscovered. Its most
# valuable control compares the shipped Stage 4 node against one wired to the
# pre-fix histology extractor. Read out of git that control is worthless twice
# over: HEAD now CARRIES the fix, so it would compare the fixed module with
# itself; and exec'ing a pre-fix filtering.py runs its
# `from oncotriage.extraction.histology import ...` line, which resolves to the
# LIVE module, so the "old" side runs the NEW extractor and reports no
# difference for the wrong reason. That is not hypothetical -- it happened, and
# the committed control asserts the trap directly before relying on the wiring.
# Neither file execs a file in the working tree; both hash the source before and
# after and fail if it moved.
#
# tests/test_extraction_stage_m_category.py (the AJCC M-category item) is the
# sixth member and the argument is the same one again: it execs a PATCHED COPY
# of oncotriage/extraction/stage.py -- the tier deleted, cM0 read as a stage,
# the tier hoisted above the stage group, the LOINC guard replaced by the
# metastasis_category field, the counter removed, the regex re-anchored on \b --
# and of oncotriage/agent/filtering.py with the new call-site argument reverted.
# THAT LAST ONE IS WHY THE git show ROUTE WAS NOT AVAILABLE, for the reason
# recorded for the fifth member: the control has to leave the EXTRACTOR entirely
# correct and revert only the CALL SITE, which is a state no commit ever had.
# Nothing execs a file in the working tree; both source files are hashed before
# any plant runs and compared at the end, with a non-degeneracy probe so the
# comparison cannot be a tautology.
#
# tests/test_extraction_stage_non_oncology_guard.py (the CKD item) is the
# seventh, same shape again: it execs a PATCHED COPY of
# oncotriage/extraction/stage.py with the patient-side guard call deleted, its
# vocabulary widened to a bare organ word, its counter key shared with the
# trial side, its counter removed, its finditer reverted to search, and the
# guard wrongly applied to the mCODE stage-group tier. Six defects, six
# controls, none of them a state any commit ever had -- the guard has never
# existed on the patient side before, so `git show` has no version to compare
# with. Nothing execs a file in the working tree; the source is hashed before
# any plant and compared at the end against a baseline, with a non-degeneracy
# probe so that comparison cannot be a tautology.
#
# tests/test_agent_trial_verdict_normalization.py (the trial-verdict item) is
# the eighth, and it execs a PATCHED COPY of three modules:
# oncotriage/agent/evaluation.py with the fabricated-rejection clobber restored,
# with the non-object drop reverted, with either audit append deleted, with the
# malformed counter silenced and with the disqualification check made to yield
# to an unreadable label; oncotriage/agent/terminal.py with Stage 6's
# fall-through to near_misses restored; and oncotriage/agent/state.py with the
# normalizer made to guess a verdict and with its bool test moved behind a dict
# lookup so `1` resolves as `True`.
#
# git show COULD NOT SUPPLY ANY OF THEM, for three separate reasons rather than
# one. normalize_trial_verdict has never existed before, so there is no prior
# revision to compare with. Several controls revert ONE line while leaving the
# rest of the item correct -- a state no commit ever had. And the trap the
# fifth member recorded applies here in full: an exec'd copy of evaluation.py
# runs its own `from oncotriage.agent.state import ...`, which resolves to the
# LIVE, already-correct module, so a control planted in state.py is invisible
# through a planted evaluation module. That is not reasoned about in the test,
# it is ASSERTED as a precondition before any control runs, and the two state.py
# controls are probed directly for exactly that reason.
#
# Section 8 uses the same mechanism for the opposite purpose: it RECONSTRUCTS
# the pre-fix module by reverting this item's two behavioural lines in memory,
# and requires it to agree with the shipped module on ten well-formed responses
# -- a git blob would be the fixed module the moment the item is committed,
# which is the failure test_storage_query_layer.py had to fix, and a shallow
# clone has no history at all. Nothing execs a file in the working tree; all
# three sources are hashed before any plant and compared at the end, with a
# non-degeneracy probe so the comparison cannot be a tautology.
# tests/test_agent_prompt_version.py (the Stage 5 prompt-version guard) is the
# ninth, and its argument is the shortest of the nine. Every other check in that
# file compares digests it computed itself against digests it wrote to a golden
# file, so all of them would still pass against a render function that had been
# quietly disconnected from the shipped template -- the digests would simply
# agree with each other. Its control therefore execs a copy of
# oncotriage/agent/prompts.py with ONE CHARACTER of the template changed, and
# requires all sixteen variant digests to move; that is what establishes that
# the subject of the guard is the shipped text.
#
# git show could not supply it for the ordinary reason and one extra. The
# ordinary one: a one-character perturbation of the CURRENT template is a state
# no commit has ever had. The extra one is the point of the whole file -- it
# exists because a commit recedes, so reaching for `git show` inside it would
# reintroduce the dependency it was written to remove, and three files in this
# suite already abort in a tree with no `.git`.
#
# Nothing execs a file in the working tree: the patch is a string built in
# memory, prompts.py is hashed before and after with a non-degeneracy probe,
# and the file writes nothing in the repository except through an explicit
# --update-snapshot flag.
_EXEC_ALLOWLIST = {"tests/test_storage_query_layer.py",
                   "tests/test_agent_prompt_version.py",
                   "tests/test_observability_logging.py",
                   "tests/test_indexer_admission_filters.py",
                   "tests/test_extraction_histology.py",
                   "tests/test_agent_age_units_and_sex_filter.py",
                   "tests/test_extraction_stage_m_category.py",
                   "tests/test_extraction_stage_non_oncology_guard.py",
                   "tests/test_agent_trial_verdict_normalization.py",
                   # Plants into in-memory copies of agent/evaluation.py and
                   # agent/terminal.py. `git show` cannot supply these
                   # controls: the out-of-set detector has no prior revision,
                   # and several plants revert ONE line while leaving the rest
                   # of the pass correct (the chunk/node sent-set confusion,
                   # the isinstance guard, the per-trial stamp) -- a state no
                   # commit ever had. An exec'd copy of evaluation.py also
                   # binds the LIVE state and observability modules, which is
                   # what makes a plant in evaluation.py itself the only thing
                   # a probe through it can observe.
                   "tests/test_agent_out_of_set_detector.py",
                   # Plants into an in-memory copy of agent/patient.py. Two of
                   # its seven controls are one-token edits INSIDE a function
                   # body -- `is not None` made truthy, and the two observation
                   # arguments dropped from the extractor call -- so there is
                   # no attribute to rebind, and `git show` cannot supply them
                   # either: the Cancer Stage section is new, so every revision
                   # that HAS it also has it correct.
                   "tests/test_agent_summary_cancer_stage.py",
                   # Plants into in-memory copies of agent/evaluation.py (the
                   # TRIAL_DATA fence render and its marker neutralization) and
                   # agent/prompts.py (the C6 data boundary). `git show` cannot
                   # supply any of the ten: C6 has no prior revision at all,
                   # and several controls revert ONE line while leaving the
                   # rest of the pass correct -- the fence attributes left raw
                   # while the bodies are neutralized, the close fence emitted
                   # without its id, the run regex swapped for the str.replace
                   # form that was never shipped, C6 rendered into one Section 2
                   # variant only. None of those is a state any commit has had.
                   "tests/test_agent_trial_data_fencing.py"}


def _repo_py_files():
    """Every .py in the repository: package, numbered scripts and tests alike.

    A WALK, not an os.listdir. Pass 20d-2 found check 2h blind to tests/ for
    exactly the reason a one-level listing is always wrong here -- a corpus that
    silently covers less does not fail, it reports FEWER findings, which reads
    as a clean repository.
    """
    out = []
    for dirpath, dirnames, filenames in os.walk(_code_dir.rstrip(os.sep)):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", ".git", "build",
                                    "oncotriage.egg-info", ".vscode")]
        for name in sorted(filenames):
            if name.endswith(".py"):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def _scan_calls(tree, rel, allowed_exec, at_line=None, suffix=""):
    """Chain calls and exec calls in one parsed tree."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = ast.unparse(node.func)
        lineno = at_line if at_line is not None else node.lineno
        if any(m in callee for m in _CHAIN_CALL_MARKERS):
            found.append((rel, lineno, "chain-call" + suffix,
                          ast.unparse(node)[:100]))
        elif callee == "exec" and node.args and not allowed_exec:
            # node.args required: prose writes the bare word "exec()", and a
            # call with no arguments is never a bootstrap. Without this the
            # scan reported nineteen docstrings that merely DISCUSS the exec
            # chain -- the documentation of the fix reading as the defect.
            found.append((rel, lineno, "exec-call" + suffix,
                          ast.unparse(node)[:100]))
    return found


def _chain_violations(paths, skip_strings_in=(), allowlist=_EXEC_ALLOWLIST):
    """(relpath, lineno, form, text) for every exec-chain reference found.

    A STRING LITERAL IS RE-PARSED AS PYTHON RATHER THAN SUBSTRING-MATCHED, and
    that is not fastidiousness -- it is the only thing that catches the form
    this file's own retired shim probe used. `ns["exec_chain"](...)` inside a
    subprocess-probe string does not contain the substring "exec_chain(",
    because the quote characters sit between the name and the paren. The
    negative control at the bottom of this section is what established that:
    the substring version of this scan did not fire on it.

    Re-parsing also solves the opposite problem for free. Prose does not parse
    as Python, so a docstring explaining why exec_chain was deleted -- and nine
    of them do, deliberately, because a deleted mechanism has to leave its
    argument behind -- is not reported.
    """
    found = []
    for path in paths:
        rel = os.path.relpath(path, _code_dir).replace(os.sep, "/")
        try:
            source = open(path, encoding="utf-8").read()
        except OSError as exc:                                    # noqa: PERF203
            found.append((rel, 0, "unreadable", str(exc)))
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            found.append((rel, exc.lineno or 0, "unparseable", str(exc)))
            continue
        skip_strings = os.path.abspath(path) in skip_strings_in
        allowed_exec = rel in allowlist
        found += _scan_calls(tree, rel, allowed_exec)
        if skip_strings:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)):
                continue
            if not any(m in node.value for m in _STRING_PREFILTER):
                continue
            for candidate in (node.value, textwrap.dedent(node.value)):
                try:
                    inner = ast.parse(candidate)
                except (SyntaxError, ValueError):
                    continue
                found += _scan_calls(inner, rel, allowed_exec,
                                     at_line=node.lineno,
                                     suffix="-in-a-string")
                break
    return sorted(set(found))


_REPO_ALL_PY = _repo_py_files()
_THIS_FILE = os.path.abspath(__file__) if "__file__" in globals() else ""

# NON-DEGENERATE FIRST. A corpus that lost the numbered scripts would report
# nothing and look exactly like a clean repository -- the same failure shape
# pass 20d-2 found in check 2h's read corpus.
check("the scan corpus covers the whole repository, not just the package",
      len(_REPO_ALL_PY) >= 50, True)
check("...including the numbered entry points",
      any(os.path.basename(p).startswith("13- ") for p in _REPO_ALL_PY), True)
check("...and tests/",
      any(os.sep + "tests" + os.sep in p for p in _REPO_ALL_PY), True)

_VIOLATIONS = _chain_violations(_REPO_ALL_PY, skip_strings_in={_THIS_FILE})
check("nothing in the repository calls exec_chain, calls exec(), or loads a "
      "module by location", _VIOLATIONS, [])
if _VIOLATIONS:
    for _rel, _ln, _form, _text in _VIOLATIONS:
        print(f"       {_form:20} {_rel}:{_ln}  {_text}")

# THE ALLOWLIST IS NOT A GET-OUT. Every member must still contain the exec it
# is excused for, or the entry is a stale line excusing nothing.
_allowlist_still_needed = []
for _allowed in sorted(_EXEC_ALLOWLIST):
    _p = os.path.join(_code_dir, _allowed.replace("/", os.sep))
    if not os.path.isfile(_p):
        _allowlist_still_needed.append(f"{_allowed}: file is gone")
        continue
    if not _chain_violations([_p], allowlist=set()):
        _allowlist_still_needed.append(f"{_allowed}: no longer calls exec()")
check("...and every allowlisted file still needs its entry, so the allowlist "
      "cannot go stale unnoticed", _allowlist_still_needed, [])

# The deleted files are gone, asserted rather than assumed. A shim left on disk
# and merely unreferenced is a shim someone re-wires.
_DELETED_IN_20E = ("01- Imports.py", "02- Utility Functions.py", "03- Config.py",
                   "08- Cancer Code Registry.py",
                   "10- Structured Eligibility Extractor.py",
                   "14- Database Logger.py", "oncotriage_settings.py")
check("every shim pass 20e deleted is actually absent from the code directory",
      sorted(n for n in _DELETED_IN_20E
             if os.path.exists(os.path.join(_code_dir, n))), [])
# ...and the survivors are still there, so the check above is not passing
# because the code directory itself went missing.
check("...while the numbered entry points that survived are still present",
      sorted(n for n in ("05- FHIR Clean Data.py", "07- FHIR Parser.py",
                         "13- LangGraph Agent.py", "20- Drift Detection.py")
             if not os.path.exists(os.path.join(_code_dir, n))), [])

check("oncotriage.utils no longer defines exec_chain",
      "exec_chain" in _bound_names(os.path.join(_PKG_DIR, "utils.py")), False)

# --- NEGATIVE CONTROLS, one per form, each planted and each fired -----------
# Written to a temp directory and scanned there. Nothing in the repository is
# edited, which is the shape CLAUDE.md prefers over an in-place plant.
#
# THE THIRD ONE IS THE IMPORTANT ONE. It is written the way the five real
# bootstraps were written -- filename on the open() line, exec() on the next --
# which is the shape the first version of this scan could not see.
_CTRL_DIR = tempfile.mkdtemp(prefix="oncotriage_chain_ctrl_")
_CONTROLS = {
    "bare exec_chain call":
        'exec_chain(["03- Config.py"], caller_file=__file__,\n'
        '           caller_globals=globals())\n',
    "subscript exec_chain call":
        'ns = {}\nns["exec_chain"](["03- Config.py"], 1, 2)\n',
    "the real bootstrap shape: open() on one line, exec() on the next":
        '_code_dir = "/x/"\n'
        'with open(_code_dir + "01- Imports.py") as _fh:\n'
        '    exec(_fh.read(), globals())\n',
    "the real loop shape":
        'for _b in ("01- Imports.py", "02- Utility Functions.py"):\n'
        '    with open(_code_dir + _b) as _fh:\n'
        '        exec(_fh.read(), globals())\n',
    "by-location load":
        'import importlib.util\n'
        'spec = importlib.util.spec_from_file_location("x", "/tmp/x.py")\n',
    "hidden in a probe string":
        '_PROBE = """\\nns["exec_chain"](chain, caller_globals=ns)\\n"""\n',
}
for _label, _body in _CONTROLS.items():
    _ctrl_path = os.path.join(_CTRL_DIR, "ctrl.py")
    with open(_ctrl_path, "w", encoding="utf-8") as _fh:
        _fh.write(_body)
    _hit = _chain_violations([_ctrl_path])
    check(f"negative control fires: {_label}", len(_hit) >= 1, True)
    os.remove(_ctrl_path)

# AND THE OTHER DIRECTION. A file that merely TALKS about the exec chain in
# prose must NOT be reported, or the scan is one people route around by not
# writing documentation -- and nine of this repository's docstrings do exactly
# that, deliberately, because a deleted mechanism has to leave its argument
# behind.
_prose_path = os.path.join(_CTRL_DIR, "prose.py")
with open(_prose_path, "w", encoding="utf-8") as _fh:
    _fh.write('"""exec_chain used to load this file; pass 20e deleted it.\n\n'
              'It set __name__ to "_exec_chain_" while exec\'ing.\n"""\n')
check("...and prose about exec_chain is NOT reported, so the scan does not "
      "punish keeping the argument for a deleted mechanism",
      _chain_violations([_prose_path]), [])
os.remove(_prose_path)
shutil.rmtree(_CTRL_DIR, ignore_errors=True)




# ===========================================================================
# 2. IMPORTING TOUCHES NO SOCKET, NO DATABASE, NO MODEL
# ===========================================================================

print("\n" + "=" * 78)
print("2. importing every package module under a socket / sqlite trap")
print("=" * 78)

# WHAT IS TRAPPED, and why each one.
#
#   socket.socket        replaced by a SUBCLASS that raises in __init__, not by
#                        a plain function: `ssl.py` does `class SSLSocket(socket)`
#                        at import time and a function cannot be subclassed.
#                        Raising before super().__init__ means no file
#                        descriptor is ever allocated.
#   socket.create_connection   the other way a client opens a connection.
#   sqlite3.connect      "touches no database".
#   builtins.open        "reads no JSON". Added in pass 2a, when
#   io.open              oncotriage.registries.mesh arrived: load_mesh_filter()
#                        reads four JSON lookups and MUST do it in a function,
#                        not at import. Both bindings are patched because they
#                        are separate references to the same function -- and
#                        pathlib.Path.open() goes through io.open, so patching
#                        only builtins.open would leave every Path read open.
#
# io.open_code is deliberately NOT patched: that is what the import machinery
# itself uses to read a .py file, and trapping it would trap the very imports
# under test.
#
# THE THIRD-PARTY IMPORTS HAPPEN BEFORE THE TRAP IS ARMED. openai pulls in
# sysconfig, which on macOS reads /System/Library/CoreServices to work out the
# OS version, and that read is not this package's doing. Pre-importing them
# makes the claim exactly "importing an oncotriage module reads no file",
# which is the claim worth making. Verified: with the pre-imports removed, the
# run dies inside _osx_support, which is how this was found.
#
# The heavy-module list is what "loads no model" means: torch / transformers /
# sentence_transformers carry MedCPT, and icd10 is the full ICD-10-CM release
# that _build_icd10_cancer_sets() imports INSIDE its body. fastembed is
# deliberately absent from the list -- qdrant_client imports it transitively, so
# its presence says nothing about this package, and importing it loads no
# weights.
_PURITY = r"""
import builtins, io, json, socket, sqlite3, subprocess, sys

import dotenv, httpx, openai, qdrant_client, tenacity                     # noqa: F401
# caffeine IS PRE-IMPORTED ONLY WHERE IT EXISTS, and the guard is the whole
# reason this file can run on Linux. pyproject declares it
# `caffeine==0.5; sys_platform == "darwin"`, because the package's module body
# ends in `on()` -> `subprocess.Popen(['caffeinate', ...])` and `caffeinate` is
# a macOS binary. So:
#
#   macOS  -- installed. The import happens HERE, before the traps below are
#             armed, exactly as it always did; `oncotriage.utils` then finds it
#             in sys.modules and its own guarded import is a no-op. Nothing
#             about this run changes.
#   Linux  -- absent. There is nothing to pre-import, `oncotriage.utils`'s
#             `except Exception` records CAFFEINE_IMPORT_ERROR, and a failed
#             import spawns no process and opens no file (the path finder uses
#             os.scandir), so every trap below stays quiet on its own merit.
#
# `except Exception`, not `except ImportError`, for the same reason
# oncotriage/utils.py uses the broad one: on a Linux box that HAS the package
# installed anyway, the failure is a FileNotFoundError out of that Popen, not
# an ImportError. The flag is reported so the parent can record a SKIP rather
# than silently covering less.
try:
    import caffeine                                                       # noqa: F401
    _caffeine_preimported = True
except Exception:                                                         # noqa: BLE001
    _caffeine_preimported = False
import collections, glob, logging, os, pathlib, re, threading, typing     # noqa: F401
import xml.etree.ElementTree                                              # noqa: F401
# Pre-imported for the same reason as the block above: the agent imports these
# at module scope, and their own import chains touch files that are not this
# package's doing. numpy and rank_bm25 read nothing; langgraph is listed here
# rather than in `heavy` because it is a graph library, not a model.
import numpy, rank_bm25, langgraph.graph                                  # noqa: F401
# Pass 20c-3a: oncotriage.fhir.explore imports these THREE AT MODULE SCOPE, and
# deliberately -- seven of its twelve functions plot, and nothing but
# "06- FHIR Dataset Characterization.py" imports it. matplotlib reads matplotlibrc and its font
# cache at import, and pandas reads its own configuration, so without this
# pre-import the trap would fire on THEIR file access rather than on anything
# this package does. Same allowance, for the same reason, as the block above.
import matplotlib, matplotlib.pyplot, pandas, seaborn                     # noqa: F401
# Pass 20c-3b: the serving layer's three module-scope third-party dependencies.
# scipy.stats is imported at module scope by oncotriage.monitoring.drift and has
# to be -- SCIPY_AVAILABLE is that module's answer to "can this run", and a flag
# that could not be read until after the first call that needed it would be
# useless. fastapi and slowapi are oncotriage.api.server's whole subject. All
# three read files of their own at import (scipy loads its config, fastapi pulls
# in pydantic's compiled core), which is not this package's doing. Same
# allowance, for the same reason, as the two blocks above.
import scipy.stats, fastapi, slowapi                                      # noqa: F401
# tqdm and uvicorn: the batch runner draws a progress bar, and the API entry
# point serves. Neither loads a model; both are listed so the trap measures this
# package rather than its dependencies' import machinery.
import tqdm, uvicorn                                                      # noqa: F401
# Pass 20c-3c-2: oncotriage.orchestration.airflow_manager imports requests at
# module scope -- every one of its status and trigger functions is an HTTP call,
# so there is no version of that module that does not need it. requests reads
# certifi's CA bundle location and charset_normalizer's tables at import, which
# is not this package's doing. Same allowance, for the same reason, as the four
# blocks above. It is NOT on the `heavy` list: it opens no socket at import (the
# socket trap below is armed and would catch that), and it loads no model.
import requests                                                           # noqa: F401


class Blocked(RuntimeError):
    pass


_real_socket = socket.socket


class BlockedSocket(_real_socket):
    def __init__(self, *args, **kwargs):
        raise Blocked("socket.socket() was constructed")


def _blocked(*args, **kwargs):
    raise Blocked("a blocked call was made")


socket.socket = BlockedSocket
socket.create_connection = _blocked
sqlite3.connect = _blocked
builtins.open = _blocked
io.open = _blocked
# PASS 20c-3c-2 ADDED THE SUBPROCESS TRAPS, and this pass is why they exist.
# Before item 20b, LOADING "22- Airflow Database.py" ran `airflow db migrate`
# and `airflow db check` through subprocess.run, and loading
# "24- Airflow Manager.py" launched TWO long-lived server processes through
# subprocess.Popen and left them running -- the heaviest import-time side effect
# the project has ever had. None of the four traps above can see either one: a
# subprocess opens no socket, no database and no file IN THIS PROCESS.
#
# Both names are patched on the `subprocess` MODULE, which is what
# `subprocess.run(...)`/`subprocess.Popen(...)` resolve at CALL time -- and both
# orchestration modules write exactly that, `import subprocess` plus a qualified
# call, so the patch is in their path. A module that had done `from subprocess
# import Popen` at import would hold the original and escape this; check 7's AST
# scan is the backstop for that shape.
#
# PASS 20c-3i REWROTE THE TWO LINES BELOW INTO THE BLOCK THAT FOLLOWS, because
# the paragraph above contains a claim that is FALSE, and it was measured rather
# than argued about. It says a module doing `from subprocess import Popen` would
# hold the original and escape. It would not: `from X import name` is an
# ATTRIBUTE READ performed when the import statement RUNS, and every one of the
# 48 imports below runs AFTER this patch, so a from-import in any package module
# binds `_blocked`. Measured directly -- attribute form, module-alias form and
# from-import form were each fired against a patched subprocess and all three
# raised. The three controls at the bottom of this probe now assert that instead
# of the comment claiming it.
#
# WHAT DOES ESCAPE, also measured, and it is not a reference form at all:
#
#   * os.system, os.posix_spawn, os.posix_spawnp, os.fork and the os.exec*
#     family spawn a process WITHOUT going through the subprocess module. They
#     escaped both traps completely.
#   * subprocess.call / check_call / check_output / getoutput did NOT escape --
#     each builds a Popen through the subprocess module's own global, which is
#     patched. Same for os.popen. That is a CPython implementation detail rather
#     than a documented guarantee, so both are trapped explicitly below; relying
#     on an internal call graph for a safety property is how a trap goes quiet
#     across a version bump with nothing to show for it.
#   * a module imported BEFORE this point that did `from subprocess import run`
#     holds the ORIGINAL and is genuinely out of reach of an attribute patch.
#     That is the only true from-import escape, and the sweep at the bottom is
#     what finds it: it walks sys.modules for any surviving reference to the
#     originals. Today it finds none.
_SPAWN_ORIGINALS = {"subprocess.run": subprocess.run,
                    "subprocess.Popen": subprocess.Popen}

subprocess.Popen = _blocked
subprocess.run = _blocked

# CLOSE THE PRE-BOUND FROM-IMPORTS, rather than merely reporting them.
#
# The sweep at the bottom of this probe found a real one on its first run:
# prompt_toolkit.application.application.Popen, a `from subprocess import Popen`
# executed while some earlier import was loading. An attribute patch on the
# subprocess module cannot reach a binding that was taken before it, so that
# reference was a live spawn route through the whole trapped section.
#
# Nothing in this package imports prompt_toolkit, so no oncotriage module could
# have reached it -- which is the argument for reporting instead of fixing, and
# it is the wrong argument. A trap whose coverage depends on which third-party
# packages happen to be installed is not a trap, it is a coincidence; the next
# dependency to do the same thing arrives without warning. Every holder is
# rebound to _blocked here, and the sweep below then asserts none survive.
_CLOSED_HOLDERS = []
for _mname, _mod in list(sys.modules.items()):
    if _mod is None or _mname in ("subprocess", "__main__"):
        continue
    _d = getattr(_mod, "__dict__", None)
    if not isinstance(_d, dict):
        continue
    for _attr, _val in list(_d.items()):
        if any(_val is _orig for _orig in _SPAWN_ORIGINALS.values()):
            setattr(_mod, _attr, _blocked)
            _CLOSED_HOLDERS.append(f"{_mname}.{_attr}")
# The os-level spawn routes, which no subprocess patch can see.
os.system = _blocked
os.popen = _blocked
os.posix_spawn = _blocked
os.posix_spawnp = _blocked
os.execv = _blocked
os.execve = _blocked
os.execvp = _blocked
# fork is trapped last and named separately because trapping it would break
# multiprocessing if anything below used it. Nothing does -- this probe runs one
# process and imports 48 modules -- and a module that FORKED at import would be
# the worst offender the project has ever had.
os.fork = _blocked

import oncotriage.constants
import oncotriage.settings
import oncotriage.paths
import oncotriage.config
import oncotriage.utils
import oncotriage.embedding
import oncotriage.registries.cancer_code_registry
import oncotriage.registries.mesh
import oncotriage.registries.mesh_crosswalk_build
import oncotriage.extraction.negation
import oncotriage.extraction.stage
import oncotriage.extraction.histology
import oncotriage.fhir.parser
import oncotriage.fhir.clean
import oncotriage.fhir.generate
import oncotriage.fhir.explore
import oncotriage.retrieval.indexer
import oncotriage.retrieval.index_validator
# Pass 20c-3c-2. qdrant_backup is File 29, which was the LAST UNGUARDED FILE in
# the repository: every statement in it was at module level, so loading it
# created a directory, listed every collection over the network and wrote one
# JSON per collection. Three of this section's five traps -- socket, open,
# io.open -- are exactly the ones that file would have fired, and it is the
# single strongest reason this check exists at all.
import oncotriage.retrieval.qdrant_backup
import oncotriage.orchestration
import oncotriage.orchestration.home
# airflow_setup ran `airflow db migrate` and rewrote airflow.cfg at load before
# item 20b; airflow_manager launched TWO long-lived server processes with
# subprocess.Popen. Neither may do anything at import, and no trap here can see
# a subprocess -- so check 7 (below) reads their ASTs for module-level calls,
# and this import is what proves the cheaper properties.
import oncotriage.orchestration.airflow_setup
import oncotriage.orchestration.airflow_manager
# dag_generator is the one where the trap does real work: File 23 assembled
# dag_content at module level, %-formatted with THREE lazy paths, so importing
# the old shape resolved three directories. The `open` trap would not catch
# that (glob uses os.scandir) -- check 2c's per-module sweep is what does --
# but the module must also not WRITE the DAG at import, which this catches.
import oncotriage.orchestration.dag_generator
import oncotriage.storage.database_logger
import oncotriage.storage.maintenance
import oncotriage.storage.queries
import oncotriage.api
import oncotriage.api.server
import oncotriage.monitoring
import oncotriage.monitoring.drift
import oncotriage.batch
import oncotriage.batch.runner
import oncotriage.registries.primary_cancer
import oncotriage.agent
import oncotriage.agent.deps
import oncotriage.agent.state
import oncotriage.agent.text
import oncotriage.agent.models
import oncotriage.agent.patient
import oncotriage.agent.mesh_expansion
import oncotriage.agent.retrieval
import oncotriage.agent.filtering
import oncotriage.agent.evaluation
import oncotriage.agent.terminal
import oncotriage.agent.graph
import oncotriage.agent.display

# langgraph LEFT THE LIST in pass 20c-2c. oncotriage.agent.graph imports
# StateGraph at module scope, which it must -- build_matching_graph is the
# module's whole subject. It loads no weights. torch and transformers stay, and
# they are the ones that matter: pass 2c made the MedCPT load lazy, so an agent
# import that pulled either in would mean the laziness had been undone.
heavy = [m for m in ("torch", "transformers", "sentence_transformers",
                     "streamlit", "icd10") if m in sys.modules]

armed = {}
for _name, _fn, _args in (("socket", socket.socket, (socket.AF_INET, socket.SOCK_STREAM)),
                          ("sqlite3", sqlite3.connect, (":memory:",)),
                          # The path never has to exist: the trap raises
                          # before anything reaches the filesystem, and a path
                          # that does exist would let a FAILED trap silently
                          # succeed instead of raising FileNotFoundError.
                          ("open", builtins.open, ("/oncotriage-trap-probe",)),
                          ("io.open", io.open, ("/oncotriage-trap-probe",)),
                          # Pass 20c-3c-2. The command never has to be real:
                          # the trap raises before anything is spawned, and a
                          # real command would let a FAILED trap succeed
                          # quietly instead of raising FileNotFoundError.
                          ("subprocess.run", subprocess.run,
                           (["oncotriage-trap-probe"],)),
                          ("subprocess.Popen", subprocess.Popen,
                           (["oncotriage-trap-probe"],)),
                          # Pass 20c-3i: the os-level spawn routes, which no
                          # subprocess patch reaches. os.system was measured
                          # ESCAPING both original traps.
                          ("os.system", os.system, ("oncotriage-trap-probe",)),
                          ("os.popen", os.popen, ("oncotriage-trap-probe",)),
                          ("os.posix_spawn", os.posix_spawn,
                           ("/oncotriage-trap-probe", ["x"], {})),
                          ("os.execv", os.execv,
                           ("/oncotriage-trap-probe", ["x"])),
                          ("os.fork", os.fork, ())):
    try:
        _fn(*_args)
        armed[_name] = False
    except Blocked:
        armed[_name] = True

# EVERY REFERENCE FORM, ONE CONTROL EACH (pass 20c-3i).
#
# A trap that patches a module attribute is only as good as the way callers
# NAME the thing it patches, and this project has already shipped three defects
# of exactly that class -- File 36's walk covered ast.Name and would have missed
# the attribute form, File 47's own BM25 check matched the bare name and
# fastembed.SparseTextEmbedding evaded it, and the paragraph above this probe
# asserted a from-import escape that does not exist. So the three forms are
# fired rather than reasoned about:
#
#   attribute     subprocess.run(...)              -- what both orchestration
#                                                     modules actually write
#   module alias  import subprocess as sp; sp.run  -- same module object, so
#                                                     this must be caught too
#   from-import   from subprocess import run; run  -- reads the attribute when
#                                                     the import RUNS, which is
#                                                     after this patch
#
# A future edit that swapped the module patch for, say, a sys.modules
# replacement would keep the first two passing and break the third.
_forms = {}


def _fires(fn, *args):
    try:
        fn(*args)
        return False
    except Blocked:
        return True


_forms["attribute"] = _fires(subprocess.run, ["oncotriage-trap-probe"])

import subprocess as _sp_alias
_forms["module_alias"] = _fires(_sp_alias.run, ["oncotriage-trap-probe"])

from subprocess import run as _run_fromimport
_forms["from_import"] = _fires(_run_fromimport, ["oncotriage-trap-probe"])

from subprocess import Popen as _popen_fromimport
_forms["from_import_popen"] = _fires(_popen_fromimport,
                                     ["oncotriage-trap-probe"])

# THE ONE GENUINE FROM-IMPORT ESCAPE, swept again AFTER the closure above and
# after all 48 imports: a module that bound the original into its own globals
# before the patch, or one that arrived during the imports and did the same.
# Nothing may hold either original by the end.
#
# __main__ is excluded because _SPAWN_ORIGINALS itself lives there -- this
# probe's own dict of originals is not an escape hatch, it is the thing doing
# the looking.
_holders = sorted(
    f"{_mname}.{_attr}"
    for _mname, _mod in list(sys.modules.items())
    if _mod is not None and _mname not in ("subprocess", "__main__")
    and isinstance(getattr(_mod, "__dict__", None), dict)
    for _attr, _val in list(_mod.__dict__.items())
    if any(_val is _orig for _orig in _SPAWN_ORIGINALS.values())
)

# THE CLOSURE MECHANISM ITSELF NEEDS A CONTROL. An empty `_holders` is also
# what a sweep that looks at nothing returns. So plant a module holding the
# original -- the exact shape prompt_toolkit had -- re-run the closure over it,
# and require it to be caught and rebound.
import types as _types
_planted_mod = _types.ModuleType("oncotriage_planted_holder")
_planted_mod.Popen = _SPAWN_ORIGINALS["subprocess.Popen"]
sys.modules["oncotriage_planted_holder"] = _planted_mod
_planted_seen = []
for _mname, _mod in list(sys.modules.items()):
    if _mod is None or _mname in ("subprocess", "__main__"):
        continue
    _d = getattr(_mod, "__dict__", None)
    if not isinstance(_d, dict):
        continue
    for _attr, _val in list(_d.items()):
        if any(_val is _orig for _orig in _SPAWN_ORIGINALS.values()):
            setattr(_mod, _attr, _blocked)
            _planted_seen.append(f"{_mname}.{_attr}")
_planted_now_blocked = _fires(_planted_mod.Popen, ["oncotriage-trap-probe"])
del sys.modules["oncotriage_planted_holder"]

print(json.dumps({"heavy": heavy, "armed": armed, "forms": _forms,
                  "caffeine_preimported": _caffeine_preimported,
                  "prebound_holders": _holders,
                  "closed_holders": sorted(_CLOSED_HOLDERS),
                  "control_found_planted":
                      "oncotriage_planted_holder.Popen" in _planted_seen,
                  "control_planted_now_blocked": _planted_now_blocked}))
"""

# 33 as of pass 20c-3a: oncotriage.embedding, fhir.clean, fhir.generate,
# fhir.explore, retrieval.indexer and retrieval.index_validator joined the 27
# from pass 2c (which were the twelve agent modules, oncotriage.agent itself,
# oncotriage.registries.primary_cancer and the fourteen from earlier passes).
#
# THREE OF THE SIX WERE THE WORST OFFENDERS IN THE PROJECT before this pass.
# "11- RAG Trial Indexer.py" built SparseTextEmbedding at module level, so
# reading the indexer loaded a model. "06- FHIR Dataset Characterization.py" resolved three globs,
# CREATED A DIRECTORY, built the whole ICD-10-CM registry and mutated matplotlib's
# global style, all at exec time. "05- FHIR Clean Data.py" resolved two globs and
# built the registry. Every one of those is now behind an accessor, and this
# probe is what says so.
#
# THE AGENT IS THE HARDEST CASE IN THIS FILE. "13- LangGraph Agent.py" loaded
# MedCPT (~110 MB) and FastEmbed at exec() time, so importing it was the single
# most expensive thing in the project, and twelve files chained it. The traps
# below say it now loads NOTHING -- and the `heavy` list is what says the models
# specifically did not arrive, which no open/socket trap could tell you.
#
# oncotriage.storage.database_logger stays the case that matters most for the
# sqlite3 trap: its whole subject is a SQLite database, and before item 20b this
# import created three tables in the production inferences.db.
#
# PASS 20c-3b ADDS NINE: storage.maintenance, storage.queries, api, api.server,
# monitoring, monitoring.drift, batch, batch.runner and the primary_cancer entry
# that was already counted -- five modules and three package __init__ files.
# Three of the five are the ones that matter here:
#
#   storage.maintenance   is the DELETE loop. "15- Database Wipe All Tables.py" opened the
#                         production database at module level until item 20b;
#                         this import must open nothing at all.
#   storage.queries       is forty queries that File 16 ran AT LOAD TIME, every
#                         one of them against the production database. This is
#                         the single largest behaviour change of the pass and
#                         the sqlite3 trap is what says it landed.
#   api.server            builds the FastAPI app at import, which is the one
#                         deliberate exception to "importing does nothing". The
#                         traps are what bound that exception: an object is
#                         constructed, and no socket, database, file or model is
#                         touched while doing it.
#
# PASS 20c-3c-2 ADDS SIX: retrieval.qdrant_backup, orchestration,
# orchestration.home, orchestration.airflow_setup, orchestration.dag_generator
# and orchestration.airflow_manager -- five modules and one package __init__.
# It also adds TWO TRAPS, subprocess.run and subprocess.Popen, because three of
# the six are the only modules in the package that spawn processes and no
# existing trap could see one. The three that matter here:
#
#   retrieval.qdrant_backup   was "29- Download Qdrant Data.py", the LAST file
#                             in the repository with no __main__ guard and no
#                             function at all. Loading it created a directory,
#                             listed every Qdrant collection and scrolled every
#                             point over the network. The open, io.open and
#                             socket traps are all three of the things it did.
#   orchestration.airflow_setup    ran `airflow db migrate` and `airflow db
#                             check` at load before item 20b -- subprocess.run.
#   orchestration.airflow_manager  launched the API server AND the scheduler at
#                             load before item 20b and left them running --
#                             subprocess.Popen, the heaviest import-time side
#                             effect this project has ever had.
_MODULES_UNDER_TRAP = 48

_rc, _out, _err = _run(_PURITY, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check(f"all {_MODULES_UNDER_TRAP} package modules import with open, io.open, "
      f"socket.socket, socket.create_connection, sqlite3.connect, "
      f"subprocess.run and subprocess.Popen patched to raise", _rc, 0)
if _rc != 0:
    fail("import purity",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    _armed = _payload.get("armed") or {}
    # NON-DEGENERATE, and this is the important part: a subprocess where the
    # patches silently did nothing would also exit 0. Each trap is fired after
    # the imports and must raise, so a run that proved nothing fails instead of
    # passing. The dict is checked whole, not key by key, so a trap that
    # disappears from the probe is a failure rather than an unnoticed omission.
    check("every trap was ARMED after the imports (socket, sqlite3, open, "
          "io.open, subprocess.run/Popen, os.system/popen/posix_spawn/execv/fork)",
          _armed,
          {"socket": True, "sqlite3": True, "open": True, "io.open": True,
           "subprocess.run": True, "subprocess.Popen": True,
           # Pass 20c-3i. os.system was MEASURED escaping the two subprocess
           # traps: it spawns through the C library and never touches the
           # subprocess module. So did os.posix_spawn, os.execv and os.fork.
           "os.system": True, "os.popen": True, "os.posix_spawn": True,
           "os.execv": True, "os.fork": True})
    check("no model-bearing library was imported (torch / transformers / "
          "sentence_transformers / streamlit / langgraph / icd10)",
          _payload.get("heavy"), [])

    # THE ONE THING THIS PROBE COVERS LESS OF WHERE caffeine IS ABSENT. On
    # macOS the pre-import is what keeps `caffeine`'s own module-level
    # `subprocess.Popen(['caffeinate', ...])` outside the trap window, so a run
    # there proves that oncotriage.utils' guarded import of it stays clean.
    # Off darwin the package is not installed (pyproject marks it
    # `sys_platform == "darwin"`), so there is no such import to prove anything
    # about. Recorded as a SKIP, never as a pass: the counter is separate and
    # the summary prints it, so a Linux run cannot be mistaken for a full one.
    if not _payload.get("caffeine_preimported"):
        skip("oncotriage.utils' caffeine import stays inside the pre-import "
             "window (section 2)",
             "caffeine is not installed on this platform -- pyproject declares "
             "it `sys_platform == \"darwin\"`, and its module body spawns the "
             "macOS `caffeinate` binary. Run this file on macOS to cover it.")

    # --- every reference form is covered, with a control each (20c-3i) -----
    #
    # THE RULE THIS ENFORCES, and it is in CLAUDE.md because three defects have
    # already come from breaking it: a check that names a symbol must cover the
    # bare name, the attribute form AND the from-import binding, each with its
    # own control. The comment beside the subprocess traps used to CLAIM the
    # from-import form escaped; it does not, because `from X import name` is an
    # attribute read performed when the import runs and the traps are armed
    # first. Measuring it is what replaced the claim.
    check("the subprocess trap catches all three reference forms -- attribute, "
          "module alias and from-import -- each fired rather than argued",
          _payload.get("forms"),
          {"attribute": True, "module_alias": True,
           "from_import": True, "from_import_popen": True})

    # THE ONE FORM AN ATTRIBUTE PATCH CANNOT REACH: a module imported BEFORE
    # the trap that bound the original into its own globals. Firing the three
    # forms above would never reveal it -- they all resolve through the patched
    # module. The probe walks sys.modules for surviving references, REBINDS
    # every one it finds, and then sweeps again; this asserts the second sweep
    # is clean.
    #
    # THE FIRST RUN OF THIS FOUND A REAL ONE. prompt_toolkit's application
    # module does `from subprocess import Popen`, and it is loaded transitively
    # before the traps are armed, so it held a live spawn route through the
    # entire trapped section. Nothing in this package imports prompt_toolkit,
    # which is why reporting it would have been tempting and wrong: a trap whose
    # coverage depends on which third-party packages happen to be installed is a
    # coincidence, not a guarantee.
    check("no module holds a pre-bound reference to the real subprocess.run or "
          "subprocess.Popen after the closure pass -- the only from-import form "
          "an attribute patch cannot reach",
          _payload.get("prebound_holders"), [])
    # NON-DEGENERATE. An empty result above is also what a sweep that inspects
    # nothing produces, so the probe plants a module holding the original --
    # exactly prompt_toolkit's shape -- and requires the closure to find it and
    # to leave it raising.
    check("...and the closure CATCHES a planted holder (negative control)",
          _payload.get("control_found_planted"), True)
    check("...and the planted holder's reference RAISES afterwards, so the "
          "closure rebinds rather than merely listing",
          _payload.get("control_planted_now_blocked"), True)


# ===========================================================================
# 2b. PATH RESOLUTION IS LAZY: IMPORTING config NEEDS NO SIBLING TREE
# ===========================================================================

print("\n" + "=" * 78)
print("2b. oncotriage.config imports with the project root made unreachable")
print("=" * 78)

# THE DEFECT THIS CHECKS FOR, which shipped in pass 20c-2a and is fixed in 2b.
#
# oncotriage/paths.py resolved every sibling directory as a module-level
# assignment, so importing it globbed the whole tree and RAISED if any pattern
# matched nothing. oncotriage/config.py imports paths for load_env_keys, so
# `import oncotriage.config` inherited that: on any machine without the sibling
# tree — a wheel installed into a fresh environment, a CI checkout of "03- Code"
# on its own, a container built before its data volume is mounted — importing
# the config module to read MAX_WORKERS died with a RuntimeError about a glob.
#
# Section 2 above could not catch it. glob.glob() uses os.scandir, not open(),
# so the resolution slipped through every trap in that probe while still being
# the single largest import-time dependency in the package.
#
# The probe below points ONCOTRIAGE_MAIN_PATH at a directory that does not
# exist, which is the loudest possible version of "the tree is not there":
# settings.require_existing_directory() rejects it before any glob runs. Then it
# imports config and reads a tunable, and only afterwards touches a path.

_UNREACHABLE_ROOT = os.path.join(tempfile.gettempdir(),
                                 "oncotriage-root-that-does-not-exist")


def _run_with_env(code: str, cwd: str, extra_env: dict, extra_path: str = None):
    """_run(), plus environment overrides. A None value deletes the variable."""
    env = dict(os.environ)
    if extra_path:
        env["PYTHONPATH"] = extra_path + os.pathsep + env.get("PYTHONPATH", "")
    else:
        env.pop("PYTHONPATH", None)
    for key, value in extra_env.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    proc = subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env,
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


# The order inside the probe is the whole point: import, read a tunable, and
# only THEN read a path. Reporting all three in one payload means a failure
# says which of the three steps was the one that broke.
_LAZY_PATHS = r'''
import json
result = {}

import oncotriage.config as cfg
result["imported"] = True
result["tunable"] = cfg.MAX_WORKERS

import oncotriage.paths as paths
result["nothing_resolved_at_import"] = sorted(paths._RESOLVED)

# Importing the FUNCTION must not resolve anything either -- its default
# argument is keys_path, and a default evaluated at import would defeat this.
from oncotriage.paths import load_env_keys           # noqa: F401
result["nothing_resolved_by_importing_load_env_keys"] = sorted(paths._RESOLVED)

try:
    value = paths.data_fhir_path
    result["read_raised"] = None
    result["read_value"] = value
except Exception as exc:
    result["read_raised"] = type(exc).__name__
    result["read_message"] = str(exc)

print(json.dumps(result))
'''

_rc, _out, _err = _run_with_env(
    _LAZY_PATHS, cwd=_ELSEWHERE,
    extra_env={"ONCOTRIAGE_MAIN_PATH": _UNREACHABLE_ROOT},
    extra_path=_FALLBACK_PATH)

check("the lazy-paths probe ran", _rc, 0)
if _rc != 0:
    fail("importing oncotriage.config with the project root unreachable",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    check("oncotriage.config imports with the project root unreachable",
          _payload.get("imported"), True)
    check("...and a tunable is readable out of it (12 = MAX_WORKERS)",
          _payload.get("tunable"), 12)
    check("...and importing oncotriage.paths resolved NO path",
          _payload.get("nothing_resolved_at_import"), [])
    check("...and importing load_env_keys resolved no path either",
          _payload.get("nothing_resolved_by_importing_load_env_keys"), [])
    # NON-DEGENERATE. Everything above would also hold for a paths module that
    # had simply stopped resolving anything, ever. The read must still fail, and
    # fail with the message that names the variable to set.
    check("...while actually READING a path still raises",
          _payload.get("read_raised"), "RuntimeError")
    # The variable name is on the SECOND line of require_existing_directory's
    # message ("Set ONCOTRIAGE_MAIN_PATH to the correct location"), so the whole
    # message is carried across, not just its first line.
    check("...and the message names ONCOTRIAGE_MAIN_PATH, so the fix is findable",
          "ONCOTRIAGE_MAIN_PATH" in (_payload.get("read_message") or ""), True)
    check("...and it did NOT quietly return a path",
          "read_value" in _payload, False)

# The other half of the non-degeneracy: with the root restored, the same read
# must SUCCEED and produce a real directory. Without this, a paths module that
# raised unconditionally would pass every check above.
_rc, _out, _err = _run_with_env(
    _LAZY_PATHS, cwd=_ELSEWHERE,
    extra_env={"ONCOTRIAGE_MAIN_PATH": None},
    extra_path=_FALLBACK_PATH)
if _rc != 0:
    fail("the same probe against the real tree",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    check("with the root reachable again, the same read succeeds",
          _payload.get("read_raised"), None)
    check("...and returns a directory that exists",
          os.path.isdir(_payload.get("read_value") or ""), True)
    check("...and importing was STILL lazy on the machine that has the tree",
          _payload.get("nothing_resolved_at_import"), [])


# ===========================================================================
# 2c. NO PACKAGE MODULE RESOLVES A PATH AT IMPORT — checked one by one
# ===========================================================================

print("\n" + "=" * 78)
print("2c. every package module imports without resolving a single path")
print("=" * 78)

# WHY THIS IS PER-MODULE AND NOT ONE IMPORT OF EVERYTHING.
#
# Check 2b (pass 20c-2b) proved oncotriage.config imports without resolving the
# tree. It checked ONE module, and pass 20c-2c found the hole that left:
# oncotriage/registries/mesh.py carried
#
#     from oncotriage.paths import data_MeSH_path
#
# at module scope. A `from X import name` is an ATTRIBUTE READ, so it fires the
# lazy resolver — that one line globbed the whole sibling directory tree for
# anything that imported the MeSH filter, and oncotriage.agent.deps imports it.
# So importing the AGENT raised on a machine without the data tree, which is the
# exact defect pass 2b existed to remove, surviving one module over for a whole
# pass because nothing checked the other modules.
#
# Every module is now imported in ITS OWN subprocess, with ONCOTRIAGE_MAIN_PATH
# pointed at a directory that does not exist. Its own subprocess matters: import
# order would otherwise hide a second offender behind the first, and a module
# that resolved a path would be indistinguishable from one that merely imported
# a module that did.

_ALL_PKG_MODULES = sorted(
    os.path.relpath(f, _code_dir)[:-3].replace(os.sep, ".")
    for f in _PKG_FILES
    if not f.endswith("__init__.py")
)

check("the module list is the size the tree says it is (non-degeneracy)",
      len(_ALL_PKG_MODULES) >= 61, True)
check("...and includes the one that used to resolve a path at import",
      "oncotriage.registries.mesh" in _ALL_PKG_MODULES, True)
# Pass 20c-3a's three worst offenders, named individually so a module dropped
# from the tree cannot quietly leave this sweep. fhir.explore is the one that
# CREATED A DIRECTORY at import; retrieval.indexer is the one that LOADED A
# MODEL at import.
for _added in ("oncotriage.embedding", "oncotriage.fhir.clean",
               "oncotriage.fhir.generate", "oncotriage.fhir.explore",
               "oncotriage.retrieval.indexer",
               "oncotriage.retrieval.index_validator"):
    check(f"...and covers {_added} (new in pass 20c-3a)",
          _added in _ALL_PKG_MODULES, True)
# Pass 20c-3b's five. storage.queries is the one that matters most: File 16 ran
# forty queries against the production database at load time, so this sweep is
# what says it does not any more.
for _added in ("oncotriage.storage.maintenance", "oncotriage.storage.queries",
               "oncotriage.api.server", "oncotriage.monitoring.drift",
               "oncotriage.batch.runner"):
    check(f"...and covers {_added} (new in pass 20c-3b)",
          _added in _ALL_PKG_MODULES, True)

# Pass 20c-3c-1's thirteen. dashboard.data is the one that matters most here:
# it is the module that reads inferences.db, and the whole reason it says
# `paths.inferences_path` inside each function body rather than importing the
# name at module scope is so that THIS sweep -- run with the project root
# pointed at a directory that does not exist -- passes. A `from
# oncotriage.paths import inferences_path` there is an ATTRIBUTE read, which
# fires the lazy resolver at import; that is the exact defect pass 20c-2c found
# one module over in registries/mesh.py.
_DASHBOARD_MODULES = [
    "oncotriage.dashboard.app",
    "oncotriage.dashboard.data",
    "oncotriage.dashboard.sidebar",
    "oncotriage.dashboard.tiers",
    "oncotriage.dashboard.tabs.cost_tokens",
    "oncotriage.dashboard.tabs.demographics",
    "oncotriage.dashboard.tabs.drift",
    "oncotriage.dashboard.tabs.match_quality",
    "oncotriage.dashboard.tabs.overview",
    "oncotriage.dashboard.tabs.patient_explorer",
    "oncotriage.dashboard.tabs.performance",
    "oncotriage.dashboard.tabs.reproducibility",
    "oncotriage.dashboard.tabs.trial_explorer",
]
check("...and covers all thirteen dashboard modules (new in pass 20c-3c-1)",
      sorted(m for m in _DASHBOARD_MODULES if m not in _ALL_PKG_MODULES), [])

# Pass 20c-3d's six. fixtures.replay is the one that matters most: it is the
# only module in the package with a MODULE-LEVEL SIDE EFFECT -- it sets
# ONCOTRIAGE_DEFER_LOCAL_MODELS above its own imports, because
# oncotriage.agent.deps reads that variable once at ITS import and an assignment
# underneath would reach nothing and load ~110 MB of MedCPT on every replay.
# This sweep runs each module in its own subprocess with the project root
# pointed at a directory that does not exist, so it also proves that the side
# effect is the ONLY thing importing it does.
_PASS_3D_MODULES = [
    "oncotriage.ablation.study",
    "oncotriage.ablation.analysis",
    "oncotriage.evaluation.sampling",
    "oncotriage.evaluation.cohort_diff",
    "oncotriage.fixtures.capture",
    "oncotriage.fixtures.replay",
]
check("...and covers all six pass-20c-3d modules",
      sorted(m for m in _PASS_3D_MODULES if m not in _ALL_PKG_MODULES), [])

# Pass 20c-3c-2's five. retrieval.qdrant_backup is the one that matters most:
# "29- Download Qdrant Data.py" was the LAST file in the repository with no
# __main__ guard and no function -- every statement ran at module level, so
# loading it created a directory, listed every Qdrant collection and scrolled
# every point over the network. orchestration.dag_generator is second: File 23
# assembled dag_content at module level and the middle third of it is
# %-formatted with code_path, keys_path and data_trial_path, so building it
# resolved THREE lazy paths at import. That is precisely what this sweep, run
# with the project root pointed at a directory that does not exist, catches.
for _added in ("oncotriage.retrieval.qdrant_backup",
               "oncotriage.orchestration.home",
               "oncotriage.orchestration.airflow_setup",
               "oncotriage.orchestration.dag_generator",
               "oncotriage.orchestration.airflow_manager"):
    check(f"...and covers {_added} (new in pass 20c-3c-2)",
          _added in _ALL_PKG_MODULES, True)

_PER_MODULE_PROBE = (
    "import json, sys\n"
    "import oncotriage.paths as _p\n"
    "import %s\n"
    "print(json.dumps({'resolved': sorted(_p._RESOLVED)}))\n"
)


def _probe_one_module(module: str):
    """Import ONE module in its own subprocess. Returns (module, complaint|None)."""
    rc, out, err = _run_with_env(
        _PER_MODULE_PROBE % module, cwd=_ELSEWHERE,
        extra_env={"ONCOTRIAGE_MAIN_PATH": _UNREACHABLE_ROOT},
        extra_path=_FALLBACK_PATH)
    if rc != 0:
        return module, f"import FAILED: {(err.strip().splitlines() or ['?'])[-1][:90]}"
    payload = _last_json(out) or {}
    if payload.get("resolved"):
        return module, f"resolved {payload['resolved']}"
    return module, None


# RUN THEM CONCURRENTLY (pass 20c-3a). Serially, 26 modules took about nine
# minutes -- every one pays a fresh interpreter start plus openai, qdrant_client
# and (for the agent modules) langgraph, and pass 3a takes it to 33. A test
# nobody runs because it is slow is a test that is not protecting anything.
#
# A THREAD pool, not a process pool, and that is the right tool rather than a
# compromise: each unit of work is already its own subprocess, so the parent
# thread spends its entire life blocked in subprocess.run() with the GIL
# released. Adding worker PROCESSES would add a second layer of interpreter
# startup to fork off a process that only waits.
#
# The probes are independent BY CONSTRUCTION -- separate processes, no shared
# state, one read-only source tree -- which is the property that makes this safe
# and is why the sweep was written per-module in the first place. Results are
# collected into a dict and sorted before the assertion, so the report is
# deterministic however the pool happens to schedule them.
_eager = {}
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as _pool:
    for _module, _complaint in _pool.map(_probe_one_module, _ALL_PKG_MODULES):
        if _complaint:
            _eager[_module] = _complaint

check("no package module resolves a path (or fails) when imported with the "
      "project root unreachable",
      sorted(f"{m}: {why}" for m, why in _eager.items()), [])


# ===========================================================================
# 2d. IMPORTING THE AGENT LOADS NO MODEL, WITH THE DEFERRAL SWITCH UNSET
# ===========================================================================

print("\n" + "=" * 78)
print("2d. the agent imports with ONCOTRIAGE_DEFER_LOCAL_MODELS unset")
print("=" * 78)

# "13- LangGraph Agent.py" loaded MedCPT and FastEmbed at exec() time, lines
# 414-434, unless ONCOTRIAGE_DEFER_LOCAL_MODELS=1 was set BEFORE the exec. That
# switch existed for one caller — fixture_replay.py — and every other file
# that chained File 13 paid ~110 MB and tens of seconds just by being read.
#
# Pass 20c-2c made the loads lazy, so the switch must no longer matter AT IMPORT.
# Section 2 above already imports the agent under traps, but it inherits this
# process's environment; this probe DELETES the variable, so a regression that
# moved the load back to import time cannot hide behind a value someone else set.
#
# The switch itself is not gone and is checked to still exist: it is the second
# line of defence, turning a forgotten stand-in into a named RuntimeError rather
# than a silent real model call.

_NO_DEFER = r'''
import json, sys
import oncotriage.agent.deps as d
import oncotriage.agent.graph          # the module that imports every stage
print(json.dumps({
    "defer_flag_seen": d._DEFER_LOCAL_MODELS,
    "switch_name": d.DEFER_LOCAL_MODELS_ENV,
    "heavy": [m for m in ("torch", "transformers", "sentence_transformers")
              if m in sys.modules],
    "nothing_cached": sorted(d._CACHE),
}))
'''

_rc, _out, _err = _run_with_env(_NO_DEFER, cwd=_ELSEWHERE,
                                extra_env={"ONCOTRIAGE_DEFER_LOCAL_MODELS": None},
                                extra_path=_FALLBACK_PATH)
check("the agent imports with the deferral switch unset", _rc, 0)
if _rc != 0:
    fail("agent import without the deferral switch",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    # NON-DEGENERATE: the switch really was unset, so "no model loaded" is not
    # the deferral placeholder path being taken.
    check("...with the switch genuinely OFF, so this is not the placeholder path",
          _payload.get("defer_flag_seen"), False)
    check("...and the switch still exists, as the second line of defence",
          _payload.get("switch_name"), "ONCOTRIAGE_DEFER_LOCAL_MODELS")
    check("...and NO model-bearing library was imported",
          _payload.get("heavy"), [])
    check("...and deps built and cached nothing at all",
          _payload.get("nothing_cached"), [])


# ===========================================================================
# 2e. THE DEPENDENCY SEAM
# ===========================================================================

print("\n" + "=" * 78)
print("2e. deps overrides are what the agent reaches, and they are checkable")
print("=" * 78)

# THE DEFECT THIS SEAM REPLACES, and it is the reason pass 20c-2c happened.
#
# Files 45 and 46 redirected the pipeline by rebinding four names --
# openai_client, qdrant_client, _bm25_query_model, medcpt_score_pairs -- in the
# shared exec namespace. That worked only because every project file was exec'd
# into one dict. A module function resolves its globals in its own module, so
# those rebindings would have reached NOTHING: fixture_replay.py would have
# sent every Stage 5 prompt to the real OpenAI endpoint, been billed for it, and
# still reported that all twelve fixtures replayed clean. Nothing would raise.
#
# Everything below runs in a subprocess with no credentials required: the
# accessors are exercised with overrides installed, so no real client is ever
# built.

_SEAM = r'''
import json
from oncotriage.agent import deps, models

sentinels = {k: object() for k in deps.OVERRIDE_KEYS}
result = {}

# 1. Nothing installed -> get_override is UNSET for every key.
result["unset_before"] = [k for k in deps.OVERRIDE_KEYS
                          if deps.get_override(k) is not deps.UNSET]

# 2. An unknown key is REFUSED, not ignored. A silently-dropped override is the
#    failure this whole module exists to make impossible.
try:
    deps.set_override("openai_clientt", object())
    result["typo_refused"] = False
except KeyError:
    result["typo_refused"] = True

# 3. Every typed accessor returns the override, by identity.
saved = deps.set_overrides(sentinels)
accessors = {
    "openai_client":    deps.get_openai_client,
    "qdrant_client":    deps.get_qdrant_client,
    "bm25_query_model": deps.get_bm25_query_model,
    "medcpt_tokenizer": deps.get_medcpt_tokenizer,
    "medcpt_model":     deps.get_medcpt_model,
    "cancer_registry":  deps.get_cancer_registry,
    "lab_registry":     deps.get_lab_registry,
    "mesh_filter":      deps.get_mesh_filter,
}
result["accessor_identity"] = sorted(
    k for k, fn in accessors.items() if fn() is not sentinels[k]
)

# 4. MEDCPT_SCORER has no accessor here on purpose: its default lives in
#    models, because deps must not import models. models.score_pairs dispatches.
calls = []
deps.set_override(deps.MEDCPT_SCORER, lambda q, t: calls.append((q, tuple(t))) or "scored")
result["scorer_dispatched"] = models.score_pairs("q", ["a", "b"]) == "scored"
result["scorer_saw_the_args"] = calls == [("q", ("a", "b"))]

# 5. restore_overrides puts everything back, and CLEARS what had no previous
#    value rather than pinning it to whatever it resolved to.
deps.restore_overrides(saved)
deps.clear_override(deps.MEDCPT_SCORER)
result["unset_after"] = [k for k in deps.OVERRIDE_KEYS
                         if deps.get_override(k) is not deps.UNSET]
result["active_after"] = deps.active_overrides()

# 6. The context manager restores on the way out, including on an exception.
probe = object()
try:
    with deps.override(deps.QDRANT_CLIENT, probe):
        result["ctx_inside"] = deps.get_qdrant_client() is probe
        raise ValueError("boom")
except ValueError:
    pass
result["ctx_cleared_after_raise"] = deps.get_override(deps.QDRANT_CLIENT) is deps.UNSET

print(json.dumps(result))
'''

_rc, _out, _err = _run(_SEAM, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("the seam probe ran", _rc, 0)
if _rc != 0:
    fail("dependency seam", f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    check("no override is installed on a fresh import", _payload.get("unset_before"), [])
    check("an unknown override key raises rather than being ignored",
          _payload.get("typo_refused"), True)
    check("every typed accessor returns the installed override, by identity",
          _payload.get("accessor_identity"), [])
    check("models.score_pairs dispatches to the MEDCPT_SCORER override",
          _payload.get("scorer_dispatched"), True)
    check("...and hands it (query, trial_texts) unchanged",
          _payload.get("scorer_saw_the_args"), True)
    # NON-DEGENERATE, and this is the half that matters: an accessor that
    # ALWAYS returned the sentinel would pass check 3 and would also leave the
    # overrides installed forever. restore must actually restore.
    check("restore_overrides clears every override that had no previous value",
          _payload.get("unset_after"), [])
    check("...and active_overrides() then reports none",
          _payload.get("active_after"), [])
    check("the override context manager installs inside the block",
          _payload.get("ctx_inside"), True)
    check("...and restores even when the block raises",
          _payload.get("ctx_cleared_after_raise"), True)


# ===========================================================================
# 2f. EXACTLY ONE CONSTRUCTION SITE FOR THE BM25 SPARSE MODEL
# ===========================================================================

print("\n" + "=" * 78)
print("2f. the FastEmbed BM25 sparse model is constructed in exactly one place")
print("=" * 78)

# THE HAZARD THIS CLOSES, and it is a correctness hazard rather than a tidiness
# one.
#
# Before pass 20c-3a, SparseTextEmbedding("Qdrant/bm25") was constructed THREE
# times, independently:
#
#     "11- RAG Trial Indexer.py" line 53      index time, module level
#     oncotriage/agent/deps.py                query time, lazily
#     "12- RAG Trial Indexer Validator.py"    inside stage2_retrieval_tests()
#
# The first two are the two halves of ONE job: File 11 writes each trial's three
# BM25 fields into Qdrant's sparse vectors, and the agent encodes the patient
# query that is scored against them. BM25 sparse vectors are TOKEN-ID vectors
# over the model's vocabulary, so if the two sides ever named different models,
# the query's indices would address different terms than the documents' indices
# do. Qdrant computes a dot product over whatever indices it is handed: it would
# go on returning results, nothing would raise, no counter would move, and the
# only symptom would be that retrieval quality fell.
#
# The third one is worse. A VALIDATOR carrying its own encoder cannot detect the
# drift it exists to detect -- it would report "All 5 queries returned results"
# against an index built with a vocabulary it does not share.
#
# There is now one construction site and both sides reach it. This check is what
# stops a fourth appearing.
#
# COUNTED BY AST, NOT BY GREP. A grep cannot tell a call from a mention in a
# docstring, and three of this package's docstrings now name the class precisely
# because they explain why there is only one call.
#
# BOTH CALL SHAPES ARE MATCHED (pass 20c-3b). The pass-3a detector matched
# ast.Name only, so it saw
#
#     from fastembed import SparseTextEmbedding
#     SparseTextEmbedding(model_name="Qdrant/bm25")        <-- caught
#
# and was blind to the attribute form, which needs no import line at all and is
# what anyone reaching for the class from a module that already imports fastembed
# would naturally write:
#
#     import fastembed
#     fastembed.SparseTextEmbedding(model_name="Qdrant/bm25")   <-- MISSED
#
# A second construction site written that way would have passed this check
# silently, which is precisely the failure the check exists to prevent: two
# independently-loaded BM25 vocabularies produce a dot product over mismatched
# token IDs, so Qdrant keeps returning results, nothing raises, and only
# retrieval quality falls. Both forms are counted now, and the negative control
# below plants ONE OF EACH so neither branch of the detector can rot unnoticed.


def _sparse_model_constructions(path: str):
    """Line numbers where SparseTextEmbedding(...) is CALLED in `path`.

    Matches both `SparseTextEmbedding(...)` (ast.Name) and
    `something.SparseTextEmbedding(...)` (ast.Attribute).
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    hits = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        func = n.func
        if isinstance(func, ast.Name) and func.id == "SparseTextEmbedding":
            hits.append(n.lineno)
        elif isinstance(func, ast.Attribute) and func.attr == "SparseTextEmbedding":
            hits.append(n.lineno)
    return sorted(hits)


_construction_sites = {}
for _f in _PKG_FILES:
    _hits = _sparse_model_constructions(_f)
    if _hits:
        _construction_sites[os.path.relpath(_f, _code_dir)] = _hits

check("exactly one package file constructs SparseTextEmbedding",
      sorted(_construction_sites), ["oncotriage/embedding.py"])
check("...and it constructs it exactly once",
      len(_construction_sites.get("oncotriage/embedding.py", [])), 1)

# NON-DEGENERATE. Everything above would also hold if the detector simply never
# matched anything -- a renamed class, a broken walk, a _PKG_FILES list that had
# gone empty. The detector is shown to FIND constructions in a copy that has
# them planted in it, ONE OF EACH CALL SHAPE, so a detector that lost either
# branch fails here rather than going quietly green on the shipped tree.
_BM25_PLANT_ROOT = tempfile.mkdtemp(prefix="oncotriage_bm25_")
try:
    shutil.copytree(_PKG_DIR, os.path.join(_BM25_PLANT_ROOT, "oncotriage"))
    _PLANTED = os.path.join(_BM25_PLANT_ROOT, "oncotriage", "retrieval", "indexer.py")
    with open(_PLANTED, "a", encoding="utf-8") as _fh:
        # The bare-name form the pass-3a detector already caught...
        _fh.write('\n\ndef _planted_second_loader():\n'
                  '    return SparseTextEmbedding(model_name="Qdrant/bm25")\n')
    check("the detector CATCHES a bare-name construction planted in a copy",
          len(_sparse_model_constructions(_PLANTED)), 1)

    # ...and the ATTRIBUTE form, which it did NOT catch before pass 20c-3b.
    # This is the shape that needs no import line, so it is the one someone
    # writes without noticing they have created a second vocabulary.
    with open(_PLANTED, "a", encoding="utf-8") as _fh:
        _fh.write('\n\nimport fastembed\n\n'
                  'def _planted_third_loader():\n'
                  '    return fastembed.SparseTextEmbedding(model_name="Qdrant/bm25")\n')
    check("...and the ATTRIBUTE form, fastembed.SparseTextEmbedding(...), which "
          "the pass-3a bare-name detector was blind to",
          len(_sparse_model_constructions(_PLANTED)), 2)

    check("...and the shipped indexer has none of either, which is what makes "
          "the planted ones the only difference",
          _sparse_model_constructions(
              os.path.join(_PKG_DIR, "retrieval", "indexer.py")), [])
finally:
    shutil.rmtree(_BM25_PLANT_ROOT, ignore_errors=True)

# The two SIDES must reach the same accessor, which is a different claim from
# "there is one construction". Asserted structurally: both files must name
# get_bm25_sparse_model.
_DEPS_PY = os.path.join(_PKG_DIR, "agent", "deps.py")
_INDEXER_PY = os.path.join(_PKG_DIR, "retrieval", "indexer.py")
_VALIDATOR_PY = os.path.join(_PKG_DIR, "retrieval", "index_validator.py")


def _calls_name(path: str, name: str) -> bool:
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == name:
            return True
        if isinstance(func, ast.Attribute) and func.attr == name:
            return True
    return False


check("the agent's query encoder reaches the one accessor",
      _calls_name(_DEPS_PY, "get_bm25_sparse_model"), True)
check("...and so does the indexer, which wrote the vectors it is scored against",
      _calls_name(_INDEXER_PY, "get_bm25_sparse_model"), True)
check("...and the validator reaches it through the agent's own accessor, so it "
      "tests the encoder the agent actually uses",
      _calls_name(_VALIDATOR_PY, "get_bm25_query_model"), True)


# ===========================================================================
# 2f(ii). EXACTLY ONE PLACE NAMES THE MEDCPT CROSS-ENCODER CHECKPOINT
# ===========================================================================

print("\n" + "=" * 78)
print("2f(ii). the MedCPT cross-encoder checkpoint is named in exactly one place")
print("=" * 78)

# THE ASYMMETRY THIS CLOSES (pass 20f-2).
#
# 2f above gave "Qdrant/bm25" a named constant, ONE construction site and this
# check. The OTHER local model in the pipeline had neither: before this pass
# "ncbi/MedCPT-Cross-Encoder" was written out six times, as a bare literal,
# with nothing connecting any copy to any other --
#
#     oncotriage/agent/deps.py          line 583   the tokenizer load
#     oncotriage/agent/deps.py          line 600   the weights load
#     oncotriage/api/server.py          line 393   the stage-3 line of
#                                                  GET /pipeline/info
#     oncotriage/storage/database_logger.py  932   inferences.cross_encoder_model,
#                                                  written on every row
#     oncotriage/fixtures/capture.py    line 1270  every fixture's environment
#                                                  block
#     tests/test_storage_query_layer.py line 524   a seeded row (see below)
#
# THE OPERATIVE PAIR IS THE FIRST TWO, and the hazard has the same shape as the
# BM25 one: a cross-encoder tokenizes its (query, document) pair with the
# tokenizer trained alongside the weights. Point one literal at another
# checkpoint and the token IDs address a vocabulary the embedding matrix was not
# trained on -- and transformers raises NOTHING, because both halves are
# BERT-shaped and the call is type-correct. Stage 3 would go on returning
# scores, node_cross_encoder_rerank would sort them, the Stage 4 quality gate
# would drop some, and the only symptom would be that the ranking was noise.
# Nothing raises, no counter moves, retrieval quality falls: the sentence is
# copied from 2f above because the failure is the same one.
#
# The other four are REPORTS of what ran -- a row, a fixture, an endpoint -- and
# a report that names a model the process did not load is worse than no report,
# because it is the artefact somebody trusts six months later.
#
# WHAT IS DELIBERATELY OUTSIDE THIS SCAN. It covers _PKG_FILES, exactly as 2f
# does, and the sixth site above is in tests/. That one STAYS a literal and the
# reason is at the line: it is a value seeded into a temporary database standing
# in for what a row written months ago holds, beside _MODEL_A =
# "gpt-4o-2024-08-06" and a hardcoded pricing_version. Making a stored
# historical value track what the pipeline loads today is the opposite of what
# that column means. Every OTHER copy was a load or a live report.
#
# DOCSTRINGS ARE EXEMPT, and 2f's detector makes the same allowance by counting
# calls rather than text: several docstrings in this package now name the
# checkpoint precisely because they explain why there is only one literal.
# Both directions are controlled below -- a docstring mention must NOT be
# reported, an f-string one MUST be.
#
# THE LIMIT, STATED: a literal deliberately split across concatenation
# ("ncbi/MedCPT-" "Cross-Encoder") escapes a substring match on constants, as it
# escapes 2f's call detector's equivalents. This catches the shapes somebody
# writes without noticing they have created a second name, which is the failure
# mode, rather than the shapes somebody writes to evade a check.

_MEDCPT_FRAGMENT = "MedCPT-Cross-Encoder"


def _docstring_constant_ids(tree):
    """id() of every ast.Constant that IS a docstring in `tree`."""
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            out.add(id(body[0].value))
    return out


def _checkpoint_literals(path):
    """Line numbers of every non-docstring string literal naming the checkpoint.

    ast.walk descends into JoinedStr, so the f-string form -- which needs no
    assignment and is what anyone interpolating the name into a message writes
    -- is counted with the plain one.
    """
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    skip = _docstring_constant_ids(tree)
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _MEDCPT_FRAGMENT in node.value
        and id(node) not in skip
    )


_checkpoint_sites = {}
for _f in _PKG_FILES:
    _hits = _checkpoint_literals(_f)
    if _hits:
        _checkpoint_sites[os.path.relpath(_f, _code_dir)] = _hits

check("exactly one package file writes the MedCPT checkpoint as a literal",
      sorted(_checkpoint_sites), ["oncotriage/config.py"])
check("...and it writes it exactly once",
      len(_checkpoint_sites.get("oncotriage/config.py", [])), 1)

# AND THAT ONE LITERAL IS THE CONSTANT, not a string that happens to sit in the
# same file. Without this, a literal moved into config.py as an argument to
# something else would satisfy both checks above while leaving
# CROSS_ENCODER_MODEL bound to whatever it liked.
_CONFIG_PY = os.path.join(_PKG_DIR, "config.py")
_cross_encoder_assignments = []
for _node in ast.parse(open(_CONFIG_PY, encoding="utf-8").read()).body:
    if isinstance(_node, ast.Assign) and isinstance(_node.value, ast.Constant):
        for _t in _node.targets:
            if isinstance(_t, ast.Name) and _t.id == "CROSS_ENCODER_MODEL":
                _cross_encoder_assignments.append(_node.value.value)
check("...and that literal is the value of config.CROSS_ENCODER_MODEL",
      _cross_encoder_assignments, ["ncbi/MedCPT-Cross-Encoder"])

# THE TWO LOADS MUST BOTH BE HANDED THAT NAME. This is the claim 2f makes with
# "both SIDES reach the same accessor", in the form this model needs: there is
# no shared accessor to reach, because the tokenizer and the weights are two
# different transformers entry points, so what has to be shared is the ARGUMENT.


def _from_pretrained_arguments(path):
    """(lineno, how the first argument is written) for every from_pretrained call.

    Both reference forms are recognised -- the bare name `CROSS_ENCODER_MODEL`
    and the attribute form `config.CROSS_ENCODER_MODEL` -- because a check that
    named one would pass over the other, and this package uses the second.
    A literal argument is reported as its repr, so a regression names itself.
    """
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "from_pretrained"):
            continue
        arg = node.args[0] if node.args else None
        if isinstance(arg, ast.Name):
            out.append((node.lineno, arg.id))
        elif isinstance(arg, ast.Attribute):
            out.append((node.lineno, arg.attr))
        elif isinstance(arg, ast.Constant):
            out.append((node.lineno, repr(arg.value)))
        else:
            out.append((node.lineno, f"<{type(arg).__name__}>"))
    return out


_deps_loads = _from_pretrained_arguments(_DEPS_PY)
check("the two MedCPT loads in deps.py are both handed CROSS_ENCODER_MODEL, so "
      "the tokenizer and the weights cannot be edited apart",
      sorted(name for _ln, name in _deps_loads),
      ["CROSS_ENCODER_MODEL", "CROSS_ENCODER_MODEL"])

# NON-DEGENERATE in the way that matters here: the assertion above is satisfied
# by a file with no from_pretrained call at all only if the sorted list is
# empty, which it is not -- but say so explicitly, because "deps.py stopped
# loading MedCPT" and "deps.py loads it correctly" must not print the same.
check("...and there are exactly two of them, so a third load cannot appear "
      "unnamed", len(_deps_loads), 2)

# No OTHER package module may load a checkpoint. deps.py is the seam; a second
# module calling from_pretrained is a second model in the process, whether or
# not it names the same string.
_other_loaders = sorted(
    os.path.relpath(_f, _code_dir) for _f in _PKG_FILES
    if _f != _DEPS_PY and _from_pretrained_arguments(_f)
)
check("...and no other package module calls from_pretrained at all",
      _other_loaders, [])

# --- NEGATIVE CONTROLS, one per reference form, each planted and each fired ---
#
# Everything above is also what a detector that never matches returns. Each form
# is planted in a COPY and the detector is required to find it; the docstring
# tolerance is fired in the other direction, because an exemption that exempts
# nothing looks identical to one doing real work.
_MEDCPT_PLANT_ROOT = tempfile.mkdtemp(prefix="oncotriage_medcpt_")
try:
    shutil.copytree(_PKG_DIR, os.path.join(_MEDCPT_PLANT_ROOT, "oncotriage"))
    _PLANTED_DEPS = os.path.join(_MEDCPT_PLANT_ROOT, "oncotriage", "agent",
                                 "deps.py")

    check("the checkpoint detector reports NOTHING on the shipped deps.py, "
          "which is what makes each plant below the only difference",
          _checkpoint_literals(_PLANTED_DEPS), [])

    with open(_PLANTED_DEPS, "a", encoding="utf-8") as _fh:
        _fh.write('\n\ndef _planted_bare_literal():\n'
                  '    return "ncbi/MedCPT-Cross-Encoder"\n')
    check("...CATCHES a bare literal planted in a copy",
          len(_checkpoint_literals(_PLANTED_DEPS)), 1)

    with open(_PLANTED_DEPS, "a", encoding="utf-8") as _fh:
        _fh.write('\n\ndef _planted_fstring(x):\n'
                  '    return f"loading ncbi/MedCPT-Cross-Encoder for {x}"\n')
    check("...and the F-STRING form, which needs no assignment and is what "
          "anyone interpolating the name into a message writes",
          len(_checkpoint_literals(_PLANTED_DEPS)), 2)

    with open(_PLANTED_DEPS, "a", encoding="utf-8") as _fh:
        _fh.write('\n\ndef _planted_docstring():\n'
                  '    """Explains why ncbi/MedCPT-Cross-Encoder is named once."""\n'
                  '    return None\n')
    check("...and a DOCSTRING mention is still not reported, so the prose that "
          "argues for this check does not fail it",
          len(_checkpoint_literals(_PLANTED_DEPS)), 2)

    # The from_pretrained check, fired in both reference forms it accepts and
    # in the form it must reject.
    _ARG_PROBE = os.path.join(_MEDCPT_PLANT_ROOT, "argprobe.py")
    for _label, (_code, _expected) in {
        "a literal argument is REPORTED AS THE LITERAL, so the regression "
        "names itself": (
            'AutoTokenizer.from_pretrained("ncbi/MedCPT-Cross-Encoder")\n',
            [(1, "'ncbi/MedCPT-Cross-Encoder'")]),
        "the BARE-NAME reference form is recognised": (
            'AutoTokenizer.from_pretrained(CROSS_ENCODER_MODEL)\n',
            [(1, "CROSS_ENCODER_MODEL")]),
        "the ATTRIBUTE reference form is recognised -- the one this package "
        "actually uses": (
            'AutoTokenizer.from_pretrained(config.CROSS_ENCODER_MODEL)\n',
            [(1, "CROSS_ENCODER_MODEL")]),
    }.items():
        with open(_ARG_PROBE, "w", encoding="utf-8") as _fh:
            _fh.write(_code)
        check(f"from_pretrained scan: {_label}",
              _from_pretrained_arguments(_ARG_PROBE), _expected)
finally:
    shutil.rmtree(_MEDCPT_PLANT_ROOT, ignore_errors=True)


# ===========================================================================
# 2g. NO FUNCTION-LOCAL SHADOWS A MODULE-LEVEL IMPORT
# ===========================================================================

print("\n" + "=" * 78)
print("2g. no function binds a local with the same name as a module-level import")
print("=" * 78)

# THE DEFECT THIS CAUGHT, in this very pass, twice.
#
# Converting a file that read names out of the shared exec namespace means
# prefixing those reads with the module they now come from. Two of the five
# conversions collided with a LOCAL VARIABLE that already had that name:
#
#   index_validator.stage1_index_health()   binds `config = info.config.params.vectors`
#   indexer._flush_embed_buffer()           binds `embedding` as a zip() loop variable
#
# In Python a name assigned ANYWHERE in a function is local for the WHOLE of it,
# so `config.COLLECTION_NAME` three lines above that assignment is not a module
# attribute read -- it is UnboundLocalError. The validator would have died in its
# first check on every run, and the indexer would have died the first time it
# flushed an embedding batch, i.e. partway through a real index build.
#
# Neither was caught by importing the module, because both are runtime paths.
# Both were caught by this scan, which is why it is now permanent.
#
# It is a WARNING-LEVEL smell in general and an ERROR here: the package's
# convention is that a module-level import name (`paths`, `config`, `deps`,
# `embedding`) is reachable from every function body, and a local that shadows
# one silently withdraws that.


def _own_scope_bindings(func):
    """Names bound in `func`'s OWN scope. Nested scopes excluded.

    Nested function and class bodies are NOT descended into: a name local to an
    inner function is not local to the outer one, so counting it would flag
    index_trials() for a variable that only _flush_embed_buffer() binds -- a
    false positive that would eventually make this check something people work
    around rather than fix.

    Comprehension targets are excluded for the same reason: since Python 3 a
    comprehension has its own scope, so `[x for config in items]` does not bind
    `config` in the enclosing function and cannot shadow anything there.
    """
    scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    comprehensions = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    bound = set()

    # Parameters belong to this scope. Defaults and decorators do not -- they are
    # evaluated in the enclosing one -- so only func.args is walked, not func.
    for node in ast.walk(func.args):
        if isinstance(node, ast.arg):
            bound.add(node.arg)

    def walk(nodes):
        for node in nodes:
            if isinstance(node, scopes):
                # `def inner():` binds `inner` HERE, but nothing inside it does.
                name = getattr(node, "name", None)
                if name:
                    bound.add(name)
                continue
            if isinstance(node, comprehensions):
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound.add((alias.asname or alias.name).split(".")[0])
            walk(ast.iter_child_nodes(node))

    walk(func.body)
    return bound


def _shadowed_imports(path: str):
    """[(function, [shadowed names])] for `path`, every function scope in it."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    imported = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imported.add((alias.asname or alias.name).split(".")[0])

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        clash = sorted(_own_scope_bindings(node) & imported)
        if clash:
            found.append((node.name, clash))
    return found


_shadows = {}
for _f in _PKG_FILES:
    _hits = _shadowed_imports(_f)
    if _hits:
        _shadows[os.path.relpath(_f, _code_dir)] = _hits

check("no function in the package shadows one of its module's imports",
      sorted(f"{f}: {hits}" for f, hits in _shadows.items()), [])

# NON-DEGENERATE, IN BOTH DIRECTIONS. An empty result is also what a broken
# walker returns, and a walker that flagged everything would be worked around
# rather than fixed. The scanner is run against a table of six snippets written
# into a temporary file: the two REAL pass-3a defects reproduced verbatim, and
# four cases it must NOT report.
#
# The two false-positive guards are the reason the scanner is scope-precise
# rather than a flat ast.walk:
#
#   nested_only    a name local to an INNER function is not local to the outer,
#                  so a flat walk would flag index_trials() for a variable only
#                  _flush_embed_buffer() binds.
#   comprehension  since Python 3 a comprehension target has its own scope, so
#                  `[x for config in items]` shadows nothing.
_SHADOW_CASES = {
    # REAL DEFECT 1 -- index_validator.stage1_index_health, reproduced.
    "the real `config` local in stage1_index_health": (
        "from oncotriage import config\n"
        "def stage1_index_health():\n"
        "    if config.COLLECTION_NAME in x:\n"
        "        pass\n"
        "    config = info.config.params.vectors\n",
        [("stage1_index_health", ["config"])]),
    # REAL DEFECT 2 -- indexer._flush_embed_buffer: a zip() loop variable in a
    # NESTED function. The inner function is what must be named, not the outer.
    "the real `embedding` loop variable in a nested flush function": (
        "from oncotriage import embedding\n"
        "def index_trials(trials):\n"
        "    def _flush(buf):\n"
        "        m = embedding.get_bm25_sparse_model()\n"
        "        for item, embedding, t in zip(a, b, c):\n"
        "            pass\n"
        "    _flush(1)\n",
        [("_flush", ["embedding"])]),
    "a shadowing PARAMETER, which is the same defect by another route": (
        "from oncotriage import paths\n"
        "def g(paths):\n"
        "    return paths\n",
        [("g", ["paths"])]),
    "a nested-only local does NOT flag its enclosing function": (
        "import json\n"
        "def outer():\n"
        "    def inner():\n"
        "        json = 1\n"
        "        return json\n"
        "    return inner\n",
        [("inner", ["json"])]),
    "a comprehension target shadows nothing (its own scope since Python 3)": (
        "import json\n"
        "def f(items):\n"
        "    return [json for json in items]\n",
        []),
    "a clean function is reported clean": (
        "from oncotriage import paths\n"
        "def g(directory):\n"
        "    return paths.data_fhir_path + directory\n",
        []),
}

_SHADOW_DIR = tempfile.mkdtemp(prefix="oncotriage_shadow_")
try:
    for _label, (_code, _expected) in _SHADOW_CASES.items():
        _probe_path = os.path.join(_SHADOW_DIR, "probe.py")
        with open(_probe_path, "w", encoding="utf-8") as _fh:
            _fh.write(_code)
        check(f"shadow scan: {_label}", _shadowed_imports(_probe_path), _expected)
finally:
    shutil.rmtree(_SHADOW_DIR, ignore_errors=True)

# And the shipped fix itself: the validator must NOT import `config` as a module,
# because stage1_index_health() genuinely binds a local of that name.
check("...and the shipped validator imports the config NAMES, not the module, "
      "which is what makes its `config` local harmless",
      "from oncotriage import config\n" in open(
          os.path.join(_PKG_DIR, "retrieval", "index_validator.py"),
          encoding="utf-8").read(), False)
check("...while still reading COLLECTION_NAME out of the config module",
      "COLLECTION_NAME," in open(
          os.path.join(_PKG_DIR, "retrieval", "index_validator.py"),
          encoding="utf-8").read(), True)


# ===========================================================================
# 2h. NOTHING IS DECLARED AND NEVER READ  (pass 20c-3i)
# ===========================================================================

print("\n" + "=" * 78)
print("2h. no unused import, no module constant that nothing anywhere reads")
print("=" * 78)

# WHY THIS EXISTS, and it is a limit of the equivalence proofs rather than a
# style preference.
#
# Every conversion pass since 20c-2a has been accepted on an EQUIVALENCE PROOF:
# ast.unparse the definitions on both sides and require them to match. That
# proof is powerful and it has one blind spot it cannot close by construction --
# IT ONLY COMPARES WHAT IS THERE. A name that is DECLARED and never READ is
# equivalent to itself on both sides of every diff, forever, and no amount of
# unparsing will ever mention it.
#
# Pass 20c-3c-2 shipped one: orchestration/airflow_manager.py declared
# PASSWORD_SOURCE_ARGUMENT, a constant naming a value password_source() can
# NEVER return, and it survived a full pass with an equivalence proof and 244
# checks. It was found by reading, not by testing. Pass 20c-3i found two more of
# the same shape by running this scan for the first time:
#
#   PASSWORD_SOURCE_ENV      declared for callers to assert against, and the one
#                            place that could have used it stored the resolver's
#                            own string instead -- so the constant was inert and
#                            one rename away from naming a value the function
#                            could no longer return.
#   deps.RESOLUTION_STATES   documented as "every value resolution_state() can
#                            return ... so a caller can branch on it
#                            exhaustively", read by nothing.
#
# Both are now load-bearing (see airflow_manager and check 5c respectively), so
# this scan is what turned a comment into an invariant twice.
#
# So the scan runs EVERY TIME, not on suspicion. Three shapes:
#
#   (i)   a module-level import bound and never read in its own module;
#   (ii)  a module-level CONSTANT that no .py file in the repository reads;
#   (iii) shadowed names -- already covered by 2g above, which is the reason
#         this section does not repeat it. Named here so a reader looking for
#         the third shape finds where it lives rather than concluding it is
#         missing.

# THE READ CORPUS HAD A BLIND DIRECTORY, and pass 20d-2 closed it.
#
# This was `_PKG_FILES` plus an os.listdir of the code directory. That was the
# whole repository right up until pass 20d-1 moved eleven readers into tests/,
# and pass 20d-2 moved six more plus the serial runner -- so all EIGHTEEN .py
# files under tests/, which is every test this project has except Files 18 and
# 19, were invisible to the only scan that can see a name nothing reads.
#
# WHY THAT MATTERS MORE THAN IT SOUNDS. The equivalence proof every conversion
# pass is accepted on compares what is THERE; a constant that is declared and
# never read is equivalent to itself on both sides of every diff, forever. This
# scan is the only thing in the repository that can see that shape, and
# PASSWORD_SOURCE_ARGUMENT, PASSWORD_SOURCE_ENV and deps.RESOLUTION_STATES are
# three constants it has already caught. Pointing it at a corpus with a hole in
# it does not make it fail -- it makes it report FEWER findings, which reads
# exactly like a clean package.
#
# Measured rather than predicted: pass 20d-1 predicted this would fail and it
# did not, because no package constant happened to be read only by a moved test.
# "No such constant exists today" is not the same as "the scan covers the tree",
# and the second is what this is.
#
# os.walk, not listdir, and for the reason section 1's subpackage scan already
# records: tests/ is one level deep today and a scan that assumes depth is a
# scan that stops working the day someone nests a directory in it.
#
# SHOWN TO MATTER, 2026-08-06, out of band -- not shipped here, because this
# pass's acceptance criterion is that this file report the same 283 checks it
# reported before the move. PLANTED_ONLY_READ_BY_TESTS was appended to
# oncotriage/constants.py and, in cases A and C, read from a file under tests/.
# Both files were hashed before and after and both restored byte-identically:
#
#   A  read ONLY from tests/, corpus as shipped -> NOT reported. 283 passed,
#      0 failed, exit 0.  <- the capability this widening buys
#   C  the same two plants, run against a COPY of this file with `+ _TEST_PY`
#      stripped (the pre-20d-2 corpus) -> REPORTED. 282 passed, 1 failed,
#      exit 1.  <- the FALSE POSITIVE the widening removes
#   B  planted with no reader anywhere at all -> REPORTED. 282 passed,
#      1 failed, exit 1.  <- the scan still bites
#
# A alone would prove nothing: a scan that reported nothing would also pass it.
# C is what shows the corpus is the variable, and B is what shows the scan is
# still capable of a finding.
# AND THE SAME HOLE, ONE DIRECTORY OUT: `docker/` (pass 20f-3)
# -------------------------------------------------------------
# The corpus was _PKG_FILES + an os.listdir of the code directory + tests/. An
# os.listdir does not descend, so `docker/prepare_paths.py` and
# `docker/generate_dag.py` were outside it -- and BOTH IMPORT FROM THE PACKAGE.
# prepare_paths.py:107 reads `paths._DOCKER_PATHS`, and it is that table's ONLY
# reader anywhere outside oncotriage/paths.py itself.
#
# It is latent today rather than a live false positive, and the distinction is
# the same one pass 20d-1 drew about tests/: paths.py reads its own constant
# (`_RESOLVERS` is built from it, and the two-table guard compares it), so the
# scan sees a read and reports nothing. The hole is what happens NEXT -- a
# package constant added tomorrow whose only reader is in docker/ is reported as
# dead, and the operator's fix is to delete a name the container needs.
#
# SHOWN TO BITE, out of band, with the touched file hashed before and after and
# restored byte-identically:
#
#   A  PLANTED_ONLY_READ_BY_DOCKER in oncotriage/constants.py, read only from
#      docker/prepare_paths.py, corpus as shipped -> NOT reported.  <- the
#      capability this widening buys
#   C  the same plant against a copy of this file with `+ _DOCKER_PY` stripped
#      -> REPORTED.  <- the FALSE POSITIVE the widening removes
#   B  planted with no reader anywhere -> REPORTED.  <- the scan still bites
#
# C is the row that matters: without it, A is also satisfied by a scan that had
# stopped working.
#
# os.walk, not listdir, for the reason above -- twice over now.
_TESTS_DIR = os.path.join(_code_dir, "tests")
_DOCKER_DIR = os.path.join(_code_dir, "docker")


def _py_under(directory):
    return sorted(
        os.path.join(root, name)
        for root, _dirs, files in os.walk(directory)
        for name in files
        if name.endswith(".py") and "__pycache__" not in root
    )


_TEST_PY = _py_under(_TESTS_DIR)
_DOCKER_PY = _py_under(_DOCKER_DIR)

_REPO_PY = sorted(
    _PKG_FILES
    + [os.path.join(_code_dir, n) for n in os.listdir(_code_dir)
       if n.endswith(".py")]
    + _TEST_PY
    + _DOCKER_PY
)

# NON-DEGENERACY, AS A GUARD RATHER THAN A check(). The whole point of the
# paragraph above is that a corpus which silently covers less produces fewer
# findings and looks identical to a clean one, so a shrunken corpus must not be
# survivable. It raises for the same reason the root guard above raises: the two
# checks fed by _REPO_PY are not merely wrong when the corpus is wrong, they are
# vacuously right, and reporting a pass on a corpus that no longer exists is the
# failure this whole section is about.
#
# It is a guard and not a check ALSO so that pass 20d-2 leaves this file's count
# at exactly the 283 it had before the move. A pass that widens coverage must
# not be indistinguishable, in the number it prints, from a pass that added an
# assertion.
if not (len(_TEST_PY) >= 18
        and any(f.endswith("test_package_invariants.py") for f in _TEST_PY)
        and any(f.endswith("test_extraction_histology.py") for f in _TEST_PY)):
    raise AssertionError(
        f"the read corpus lost tests/: found {len(_TEST_PY)} file(s) under "
        f"{_TESTS_DIR}. Since pass 20d-2 that directory holds every test in the "
        f"project, and the two checks fed by _REPO_PY report FEWER unread names "
        f"when it is missing -- which looks exactly like a clean package."
    )

# The same guard for docker/, for the same reason and in the same shape. It
# names prepare_paths.py explicitly because that file is the only reader of
# oncotriage/paths.py:_DOCKER_PATHS outside paths.py itself: lose it from the
# corpus and that table becomes a candidate finding the moment paths.py stops
# reading its own constant.
if not (len(_DOCKER_PY) >= 2
        and any(f.endswith("prepare_paths.py") for f in _DOCKER_PY)):
    raise AssertionError(
        f"the read corpus lost docker/: found {len(_DOCKER_PY)} file(s) under "
        f"{_DOCKER_DIR}. Both files there import from the package, and the two "
        f"checks fed by _REPO_PY report FEWER unread names when it is missing "
        f"-- which looks exactly like a clean package."
    )


def _all_reads(paths, blob_exclude=()):
    """Every identifier read anywhere in `paths`, plus every string literal.

    THIS FILE IS EXCLUDED FROM THE STRING CORPUS, and that exclusion is the
    difference between a check and a tautology. This file pins several
    historical name inventories as lists of string literals -- _PRE_20C_NAMES,
    _PRE_2A_RUNTIME_NAMES, the File 21 surface list -- and it also holds the
    exemption dict below. Counted as reads, those strings mask any constant they
    happen to mention, and the exemption list would be READ BY THE SCAN THAT
    CONSULTS IT: name a constant as exempt and it stops being reported for that
    reason alone, whether or not the exemption is removed later. That was the
    first version's behaviour and it reported zero findings; excluding this
    file's strings takes it to three, two of which are the ones the exemptions
    were written for and one of which was new.

    Its ast Name/Attribute/import reads still count. Only its string LITERALS
    are dropped.

    A READ, NOT A BINDING. ``ast.Name`` and ``ast.Attribute`` are only counted
    when their ctx is NOT Store, and this is the whole check rather than a
    detail: the first version of this function counted every Name node, so
    ``PASSWORD_SOURCE_ENV = ...`` counted as a read OF ITSELF, every constant in
    the package looked read, and the scan below passed VACUOUSLY. It was the two
    negative controls at the bottom of this section that said so -- they are the
    reason the defect lasted one run instead of shipping, which is the argument
    for controls stated as a fact rather than a principle.

    THE STRING LITERALS ARE NOT SLOPPINESS, they are the third reference form.
    This file reads oncotriage.paths.PATH_NAMES through
    ``getattr(module, "PATH_NAMES", ())`` and reads several package names inside
    the source of subprocess probes, which are ordinary Python strings to any
    AST walk. A scan that counted only ast.Name and ast.Attribute would report
    PATH_NAMES as dead -- which it did, on its first run, and which is exactly
    the "a check that names a symbol must cover every reference form" rule this
    section is an instance of. Substring matching over string constants is
    deliberately generous: a false NEGATIVE here is a missed defect the reader
    can still find, while a false POSITIVE is a check people learn to route
    around.

    A ``from X import NAME`` is a read too, and it is the fourth form -- it is
    how "03- Config.py" re-exports the tunables and how every shim reaches the
    package. It is neither a Name nor an Attribute node, so it is collected
    explicitly.
    """
    names, blobs = set(), []
    for path in paths:
        try:
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
        except SyntaxError:
            continue
        collect_strings = os.path.abspath(path) not in blob_exclude
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                if not isinstance(node.ctx, ast.Store):
                    names.add(node.id)
            elif isinstance(node, ast.Attribute):
                if not isinstance(node.ctx, ast.Store):
                    names.add(node.attr)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name != "*":
                        names.add(alias.name.split(".")[-1])
            elif (collect_strings and isinstance(node, ast.Constant)
                  and isinstance(node.value, str)):
                blobs.append(node.value)
    return names, "\n".join(blobs)


def _named_in(name, blob):
    """True if `name` appears in `blob` on WORD BOUNDARIES.

    A plain ``in`` test was the first version and it was too generous in a way
    that mattered: ``ECOG_LOINC_PANEL_CODE`` is a substring of
    ``_ECOG_LOINC_PANEL_CODE``, a DIFFERENT constant in a different module, so
    a bare substring match reported the first as read because the second was
    named somewhere. Two constants whose names differ by a leading underscore
    are exactly the pair this project keeps (fhir/generate.py and
    fhir/parser.py each name the same LOINC codes), so this is the common case
    rather than a corner.
    """
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
                     blob) is not None


def _unused_imports(path):
    """Module-level import bindings never read in their own module.

    ``__init__.py`` files are skipped whole: their entire job is to bind names
    for somebody else to import, so "unread here" is their normal state. A line
    carrying ``noqa`` is skipped for the same reason, explicitly declared.
    """
    if os.path.basename(path) == "__init__.py":
        return []
    src = open(path, encoding="utf-8").read()
    lines = src.splitlines()
    tree = ast.parse(src, path)
    bound = {}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or (
                    alias.name.split(".")[0] if isinstance(node, ast.Import)
                    else alias.name)
                bound.setdefault(local, node.lineno)
    reads = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            reads.add(node.id)
        elif isinstance(node, ast.Attribute):
            reads.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            reads.add(node.value)
    return sorted(
        f"{local} (line {lineno})"
        for local, lineno in bound.items()
        if local not in reads and "noqa" not in lines[lineno - 1]
    )


def _module_constants(path):
    """Module-level ALL_CAPS assignment targets, with their line numbers."""
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    out = []
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        for name in targets:
            stripped = name.lstrip("_")
            if stripped and stripped.upper() == stripped and not stripped.isdigit():
                out.append((name, node.lineno))
    return out


# --- (i) unused imports ----------------------------------------------------
_unused = {}
for _f in _PKG_FILES:
    _hits = _unused_imports(_f)
    if _hits:
        _unused[os.path.relpath(_f, _code_dir)] = _hits

check("no package module binds an import it never reads",
      sorted(f"{f}: {hits}" for f, hits in _unused.items()), [])

# --- (ii) never-read module constants --------------------------------------
#
# THE EXEMPTIONS ARE A CLOSED LIST WITH AN ARGUMENT EACH, not a suppression
# file. A constant earns a place here only by being documentation whose whole
# purpose is to be visible in the source and never used; anything else is a
# defect and gets fixed instead. The list being closed is the point -- a new
# never-read constant fails this check rather than joining the list quietly.
_UNREAD_CONSTANT_EXEMPTIONS = {
    # fhir/generate.py names the two LOINC codes ADJACENT to the ECOG score
    # code, explicitly so that "nobody 'corrects' the score code to one of
    # them" -- its own comment says so, in the source, above the assignments.
    # A code whose purpose is to be read by a human and never by the program is
    # the one legitimate instance of this shape, and both are external-standard
    # facts, which CLAUDE.md requires be named constants rather than tunables.
    "oncotriage/fhir/generate.py": ["ECOG_LOINC_PANEL_CODE",
                                    "ECOG_LOINC_INTERPRETATION_CODE"],
    # THE SECOND ENTRY WAS `oncotriage/dashboard/tiers.py: TRIAL_STATUS_FULL`
    # AND PASS 20f-3 DELETED THE CONSTANT. It was dead before the split --
    # "21- Streamlit Dashboard.py" bound it at line 3853 and referenced it
    # nowhere, checked against `git show ae3f6c6^` -- and it was also WRONG: the
    # per-trial classifiers in patient_explorer and trial_explorer return
    # '✅ Eligible' for their top bucket, so it named a value the per-trial
    # vocabulary cannot produce. Its string belonged to the per-PATIENT
    # vocabulary, which is where pass 20f-3 put it (PATIENT_OUTCOME_FULL and the
    # three beside it, all read). See the argued change to the pinned File 21
    # surface in section 6f -- deleting an entry from a pin is a check that
    # stops running, and that one is argued where it happens rather than here.
    # THE THIRD ENTRY WAS `oncotriage/fixtures/capture.py: TERMINAL_ERROR`, AND
    # PASS 20f-3 MADE IT LOAD-BEARING RATHER THAN DELETING IT -- the one of the
    # five follow-ups on this list that asked for the opposite of a deletion.
    # It completes a closed three-member vocabulary; removing it would have left
    # TERMINAL_FINALIZE and TERMINAL_NO_CANDIDATES named beside each other and
    # told a reader those are the only two values result["terminal_node"] can
    # carry, which is false.
    #
    # verify_recording_complete() now names the error-handler case explicitly,
    # so the constant is read by the code it describes. Pass 20e predicted that
    # branch "would improve the diagnosis and change no outcome"; the first half
    # holds and THE SECOND HALF WAS WRONG, which is why it was worth doing in a
    # pass that could say so. The old arm only refused an error run when NO
    # Stage 5 exchange was recorded. An exception thrown after Stage 5 answered
    # left n_chat >= 1, nothing complained, and the fixture was written -- with
    # a prefix stamped by the error handler's placeholders. That fixture is
    # refused now. See the branch itself for the argument.

    # ----------------------------------------------------------------------
    # SEVEN ENTRIES ADDED BY PASS 20e, AND NOT ONE OF THEM IS NEW CODE.
    # ----------------------------------------------------------------------
    # Every one of these was READ, by this scan's own rules, by a line in a
    # deleted re-export shim: a `from oncotriage.X import NAME` counts as a
    # read (see _all_reads), and Files 03, 07, 08, 10 and 13 listed them.
    # Deleting the shims removed the reads and the constants became visible for
    # the first time. THAT IS THE FINDING: for as long as a shim re-exported a
    # module's whole surface, this scan could not see a dead name in it,
    # because the shim read everything by construction. It is the same blind
    # spot pass 20d-2 found in the scan's DIRECTORY corpus, in a different
    # dimension, and it is why the shim deletions had to be paired with a run
    # of this check rather than assumed harmless.
    #
    # They are split into two kinds, because they are not the same problem.

    # KIND ONE: closed vocabularies and decision records -- the TERMINAL_ERROR
    # shape above, exactly. Each names a value that exists so a reader can see
    # it, and deleting it would tell the next reader something false.
    #
    #   _NOT_EVALUABLE_REASONS is the closed tuple of the three reasons Stage 5
    #   can stamp; all three MEMBERS are read individually, and the tuple is
    #   what says the list is closed. Naming two of three would read as "these
    #   are the only two", which is false.
    "oncotriage/agent/evaluation.py": ["_NOT_EVALUABLE_REASONS"],
    #   _HISTORICAL_MED_STATUSES is the documented COMPLEMENT of
    #   _ACTIVE_MED_STATUSES (which is read, at the medication sort key).
    #   Nothing tests membership in it because "not active and not excluded" is
    #   how the parser reaches historical; the set is the record of which
    #   statuses that phrase covers, and its comment block above it is the
    #   argument for keeping historical medications at all.
    #   The two ECOG LOINC codes are the SAME entry the exemption for
    #   fhir/generate.py already carries, in the module that parses rather than
    #   the one that generates: 89247-1 is the score, and 89246-3 (panel) and
    #   89262-0 (interpretation) are named beside it precisely so nobody
    #   "corrects" the routing to a sibling that carries no integer grade.
    "oncotriage/fhir/parser.py": ["_HISTORICAL_MED_STATUSES",
                                  "_ECOG_LOINC_PANEL_CODE",
                                  "_ECOG_LOINC_INTERPRETATION_CODE"],

    # KIND TWO: GENUINELY DEAD, reported rather than deleted, and each is a
    # ranked follow-up of pass 20e rather than a decision it made.
    #
    #   BATCH_SIZE AND EXPANSION_TEMPERATURE WERE EXEMPTED HERE AND ARE NOW
    #   DELETED (pass 20f-2), so the entry is gone rather than kept. This is the
    #   shape an exemption is supposed to have: pass 20e recorded the finding
    #   with a named follow-up ("delete both, or wire BATCH_SIZE into the
    #   runner's progress reporting, and say which in CLAUDE.md"), and the
    #   follow-up closed it. Both were TUNABLES that did nothing -- CLAUDE.md
    #   tells an operator every tunable lives in oncotriage/config.py, so an
    #   operator setting either was entitled to an effect neither had.
    #   EXPANSION_TEMPERATURE's own comment said why ("Stage 1 uses no LLM") and
    #   BATCH_SIZE claimed to be "patients per progress-reporting batch" while
    #   oncotriage/batch/runner.py has no batch at all -- one thread pool and a
    #   tqdm bar that advances per patient. Deleting was chosen over wiring
    #   because wiring meant inventing a chunking layer to make the progress
    #   report coarser. The two "still needed" / "still exists" checks below are
    #   what force the entry out with the constants: leaving it here would fail
    #   the second, which is exactly what a staleness guard is for.
    #
    #   THE THIRD ENTRY WAS `oncotriage/extraction/stage.py: _PATIENT_STAGE_RE`
    #   AND PASS 20f-3 DELETED THE REGEX, so the entry is gone rather than kept
    #   -- the same shape BATCH_SIZE and EXPANSION_TEMPERATURE took above. Pass
    #   20e called it "the one of the three that should simply go": a compiled
    #   regex with no reader at all, differing from _SNOMED_DISPLAY_STAGE_RE
    #   (immediately above it, and the one extract_patient_stage() actually
    #   uses at both match sites) only by an optional "tnm " prefix that the
    #   survivor's \b already admits. The two guards below are what force the
    #   entry out with the constant, in both directions.
}

_reads, _string_blob = _all_reads(
    _REPO_PY, blob_exclude={os.path.abspath(__file__)} if "__file__" in globals()
    else {os.path.abspath(os.path.join(
        _code_dir, "tests", "test_package_invariants.py"))})
_never_read = []
for _f in _PKG_FILES:
    _rel = os.path.relpath(_f, _code_dir).replace(os.sep, "/")
    _exempt = _UNREAD_CONSTANT_EXEMPTIONS.get(_rel, [])
    for _name, _lineno in _module_constants(_f):
        if _name in _exempt or _name in _reads or _named_in(_name, _string_blob):
            continue
        _never_read.append(f"{_rel}:{_lineno} {_name}")

check("every module-level constant in the package is read by something, "
      "somewhere in the repository (or is exempted with an argument)",
      sorted(_never_read), [])

# THE EXEMPTIONS ARE NOT A GET-OUT: each one must still be genuinely unread, or
# it is a stale line hiding the fact that the code moved on. This is the same
# non-degeneracy discipline the rest of the file applies -- an exemption that
# suppresses nothing looks identical to one doing real work.
_dead_exemptions = []
for _rel, _names in _UNREAD_CONSTANT_EXEMPTIONS.items():
    for _name in _names:
        if _name in _reads or _named_in(_name, _string_blob):
            _dead_exemptions.append(f"{_rel}:{_name}")
check("...and every exemption is still needed -- an exempted constant that "
      "something now reads must lose its exemption, not keep it",
      sorted(_dead_exemptions), [])

# The exemptions must still EXIST. An exemption for a constant that has since
# been deleted or renamed is a line that silences nothing and hides the fact
# that the list is stale.
_missing_exemptions = []
for _rel, _names in _UNREAD_CONSTANT_EXEMPTIONS.items():
    _declared = {n for n, _ln in _module_constants(
        os.path.join(_code_dir, _rel.replace("/", os.sep)))}
    _missing_exemptions += [f"{_rel}:{n}" for n in _names if n not in _declared]
check("...and every exempted constant still exists, so the exemption list "
      "cannot go stale unnoticed",
      sorted(_missing_exemptions), [])

# --- NEGATIVE CONTROLS -----------------------------------------------------
#
# "[] unused" is also what a broken scanner returns. Both scans are fired
# against planted cases in a temporary file, and BOTH DIRECTIONS are covered:
# what must be reported, and what must not.
_UNREAD_DIR = tempfile.mkdtemp(prefix="oncotriage-unread-")
try:
    _probe = os.path.join(_UNREAD_DIR, "probe.py")

    # (i) unused-import controls
    for _label, (_code, _expected) in {
        "a bound-and-never-read import is REPORTED": (
            "import os\nimport sys\nprint(os.sep)\n", ["sys (line 2)"]),
        "an import read only through an attribute is NOT reported": (
            "import os\nprint(os.sep)\n", []),
        "an aliased import read under its alias is NOT reported": (
            "import subprocess as sp\nsp.run([])\n", []),
        "an aliased import NEVER read is REPORTED under its alias": (
            "import subprocess as sp\nprint('subprocess')\n", ["sp (line 1)"]),
        "a from-import read as a bare name is NOT reported": (
            "from typing import Dict\nx: Dict = {}\n", []),
        "a noqa line is exempt, as declared": (
            "import sys  # noqa: F401\n", []),
    }.items():
        with open(_probe, "w", encoding="utf-8") as _fh:
            _fh.write(_code)
        check(f"unused-import scan: {_label}", _unused_imports(_probe), _expected)

    # (ii) never-read-constant controls, INCLUDING the three reference forms a
    # constant can be read through. The string-literal form is the one that
    # produced a false positive on the first run of this scan (PATH_NAMES,
    # reached through getattr(module, "PATH_NAMES")), so it is controlled here
    # rather than trusted.
    _reader = os.path.join(_UNREAD_DIR, "reader.py")
    for _label, (_decl, _use, _expect_reported) in {
        "a constant nothing reads is REPORTED":
            ("PLANTED_DEAD = 1\n", "", True),
        "...read as a bare name is not":
            ("PLANTED_DEAD = 1\n", "from probe import PLANTED_DEAD\n"
                                   "print(PLANTED_DEAD)\n", False),
        "...read as an attribute is not":
            ("PLANTED_DEAD = 1\n", "import probe\nprint(probe.PLANTED_DEAD)\n",
             False),
        "...read through a string literal is not (getattr, or a name inside "
        "the source of a subprocess probe)":
            ("PLANTED_DEAD = 1\n",
             "import probe\nprint(getattr(probe, 'PLANTED_DEAD'))\n", False),
        "...named only in a COMMENT is still REPORTED, because a comment is "
        "not a read":
            ("PLANTED_DEAD = 1\n", "# PLANTED_DEAD is mentioned here\n", True),
    }.items():
        with open(_probe, "w", encoding="utf-8") as _fh:
            _fh.write(_decl)
        with open(_reader, "w", encoding="utf-8") as _fh:
            _fh.write(_use)
        _r, _b = _all_reads([_probe, _reader])
        _reported = not ("PLANTED_DEAD" in _r
                         or _named_in("PLANTED_DEAD", _b))
        check(f"unread-constant scan: {_label}", _reported, _expect_reported)

    # THE CONTROL THAT ALREADY EARNED ITS KEEP. The first version of
    # _all_reads() added every ast.Name node without checking ctx, so
    # `PLANTED_DEAD = 1` counted as a read OF ITSELF and the whole scan passed
    # vacuously over the real package. These two cases are what said so, on the
    # first run, before it shipped. Pinned explicitly so the ctx test cannot be
    # dropped again.
    with open(_probe, "w", encoding="utf-8") as _fh:
        _fh.write("PLANTED_DEAD = 1\nPLANTED_DEAD += 1\nfor PLANTED_DEAD in []:\n"
                  "    pass\n")
    _r, _b = _all_reads([_probe])
    check("unread-constant scan: an assignment TARGET is not a read -- the "
          "defect that made the first version of this scan vacuous",
          "PLANTED_DEAD" in _r, False)
    with open(_probe, "w", encoding="utf-8") as _fh:
        _fh.write("PLANTED_DEAD = 1\nprint(PLANTED_DEAD)\n")
    _r, _b = _all_reads([_probe])
    check("...while a genuine load in the same file IS a read, so the ctx test "
          "did not simply blind the scanner",
          "PLANTED_DEAD" in _r, True)

    # WORD BOUNDARIES. `ECOG_LOINC_PANEL_CODE` is a substring of
    # `_ECOG_LOINC_PANEL_CODE`, a different constant in a different module, and
    # this project keeps exactly that pair. A plain `in` test reported the first
    # as read because the second was named.
    check("unread-constant scan: a name is NOT considered read because a "
          "longer name contains it",
          _named_in("ECOG_LOINC_PANEL_CODE", "'_ECOG_LOINC_PANEL_CODE',"),
          False)
    check("...while the name itself, on a boundary, IS found",
          _named_in("ECOG_LOINC_PANEL_CODE", "getattr(m, 'ECOG_LOINC_PANEL_CODE')"),
          True)
finally:
    shutil.rmtree(_UNREAD_DIR, ignore_errors=True)


# ===========================================================================
# 2i. NO DEFINITION LOST A DECORATOR IN A CONVERSION PASS  (pass 20c-3i)
# ===========================================================================

print("\n" + "=" * 78)
print("2i. the decorator inventory of the whole package, pinned")
print("=" * 78)

# THE DEFECT THIS PINS, and it was found by an equivalence proof rather than by
# a check, which is why there is a check now.
#
# Pass 20c-3c-1 extracted the dashboard by slicing each definition from the
# lineno ast reports -- and AST REPORTS A DECORATED FUNCTION'S lineno AT THE
# `def`, NOT AT THE DECORATOR. So @st.fragment was silently dropped from four
# tabs. That is a real behaviour change (a fragment re-runs in isolation;
# without it every interaction in those tabs re-runs the whole app) and nothing
# but the ast.unparse comparison against git would have caught it. The SAME
# slicing approach moved Files 07 through 25.
#
# Pass 20c-3i swept it properly: every FunctionDef / AsyncFunctionDef /
# ClassDef in the package, AT EVERY NESTING DEPTH, compared against the
# pre-split version of the numbered file it came from, with the origin commit
# derived from `git log --diff-filter=A` rather than declared. 404 definitions,
# 314 matched to an origin, 18 carrying decorators, ZERO mismatches, and zero
# decorated origin definitions that failed to reproduce. Three negative controls
# (api/server.py, dashboard/tabs/drift.py, agent/retrieval.py) were each
# stripped in an AST copy and each stopped agreeing with its origin.
#
# WHY THE SWEEP IS NOT THIS CHECK. It needs git history, so it would fail in a
# checkout without it and would compare against whatever HEAD happened to be.
# What survives here is the INVENTORY: the exact decorator list of every
# decorated definition in the package. A future edit that drops one fails, and
# a new decorated definition has to be declared rather than merely appearing.
#
# DEPTH MATTERS AND IS THE REASON THE FIRST SWEEP WAS INCOMPLETE. A top-level
# walk reported api/server.py's four endpoints as having no counterpart at all,
# because create_app() nests them -- so the four definitions in the package
# carrying the MOST decorators were the four a top-level scan could not see.
# Those four also separate their decorators from the `def` with BLANK LINES,
# which is invisible to ast (decorator_list is on the node) and fatal to any
# check written against adjacency.

_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _decorated_definitions(path):
    """{qualified name: [unparsed decorators]} at EVERY depth, decorated only."""
    out = {}

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _DEF_TYPES):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                if child.decorator_list:
                    out[qual] = [ast.unparse(d) for d in child.decorator_list]
                walk(child, qual)
            else:
                walk(child, prefix)

    walk(ast.parse(open(path, encoding="utf-8").read(), path), "")
    return out


_DECORATOR_INVENTORY = {
    # Both qdrant_retry helpers are NESTED inside the node that uses them.
    "oncotriage/agent/retrieval.py::build_bm25_index_from_qdrant._scroll_page":
        ["qdrant_retry"],
    "oncotriage/agent/retrieval.py::node_hybrid_retrieval._batch_scroll":
        ["qdrant_retry"],
    # Nested inside create_app(), with BLANK LINES between decorator and def.
    "oncotriage/api/server.py::lifespan": ["asynccontextmanager"],
    "oncotriage/api/server.py::create_app.health_check": ["app.get('/health')"],
    "oncotriage/api/server.py::create_app.pipeline_info":
        ["app.get('/pipeline/info')"],
    "oncotriage/api/server.py::create_app.match_patient_endpoint":
        ["app.post('/match', response_model=MatchResponse)",
         "limiter.limit(RATE_LIMIT)"],
    "oncotriage/api/server.py::create_app.match_patient_file":
        ["app.post('/match/file', response_model=MatchResponse)",
         "limiter.limit(RATE_LIMIT)"],
    "oncotriage/dashboard/data.py::load_inferences_data":
        ["st.cache_data(ttl=60)"],
    "oncotriage/dashboard/data.py::load_trial_matches_data":
        ["st.cache_data(ttl=60)"],
    "oncotriage/dashboard/data.py::load_drift_metrics_data":
        ["st.cache_data(ttl=60)"],
    # The four that pass 20c-3c-1 dropped and its equivalence proof recovered.
    "oncotriage/dashboard/tabs/drift.py::render_drift_detection_tab":
        ["st.fragment"],
    "oncotriage/dashboard/tabs/patient_explorer.py::render_patient_explorer_tab":
        ["st.fragment"],
    "oncotriage/dashboard/tabs/reproducibility.py::render_reproducibility_tab":
        ["st.fragment"],
    "oncotriage/dashboard/tabs/trial_explorer.py::render_trial_explorer_tab":
        ["st.fragment"],
    "oncotriage/registries/cancer_code_registry.py::CancerCodeRegistry._invert_date":
        ["staticmethod"],
    # _date_sort_key belongs to OncologyLabRegistry, NOT CancerCodeRegistry --
    # two classes in one module, and the qualified name is what distinguishes
    # them. A bare-name inventory could not.
    "oncotriage/registries/cancer_code_registry.py::OncologyLabRegistry._date_sort_key":
        ["staticmethod"],
    # Pass 20c-3d. compute_collection_digest()'s paging closure is the one
    # decorated definition in the three subpackages that pass added, and it is
    # NESTED -- a top-level walk would report it as absent, which is the shape
    # that hid api/server.py's four endpoints from the first version of this
    # scan.
    "oncotriage/fixtures/capture.py::compute_collection_digest._page":
        ["qdrant_retry"],
    # ---- The MCP pass ----------------------------------------------------
    # The stdio server and the trial lookup it wraps. Five of these six are in
    # oncotriage/mcp/server.py and one is the lookup's paging-free scroll.
    #
    # `_counted.decorate.wrapper` carrying `functools.wraps(fn)` is the entry
    # in this table that is load-bearing rather than descriptive, and it is
    # worth a sentence because it looks like the most cosmetic line here. The
    # MCP SDK derives each tool's JSON Schema by calling `inspect.signature` on
    # the registered callable, and `inspect.signature` follows `__wrapped__`,
    # which is what `functools.wraps` sets. Without it the three tools
    # advertised `{"args": string, "kwargs": string}` -- the decorator's own
    # `*args, **kwargs` -- and no caller could have supplied a valid argument.
    # Nothing raised; three tools listed; it was found by printing the schema.
    # tests/test_mcp_server_stdio_contract.py section 2 asserts the parameter
    # NAMES, and this entry is what makes the decorator's loss visible here too.
    "oncotriage/mcp/server.py::_stdout_to_stderr": ["contextlib.contextmanager"],
    "oncotriage/mcp/server.py::_counted.decorate.wrapper": ["functools.wraps(fn)"],
    "oncotriage/mcp/server.py::parse_fhir_bundle_tool":
        ["_counted('parse_fhir_bundle')"],
    "oncotriage/mcp/server.py::match_patient_tool": ["_counted('match_patient')"],
    "oncotriage/mcp/server.py::lookup_trial_tool": ["_counted('lookup_trial')"],
    "oncotriage/retrieval/trial_lookup.py::lookup_trial._scroll": ["qdrant_retry"],
    "oncotriage/registries/mesh.py::MeSHCancerFilter._stem": ["staticmethod"],
    # The structured-logging pass. `_Console` is a namespace of @staticmethods
    # rather than a module of bare functions so that `console.out` reads at
    # 1,100 call sites the way `print` did; `progress` stacks
    # @contextlib.contextmanager UNDER @staticmethod, and the order is
    # load-bearing (the other way round decorates the staticmethod descriptor,
    # which is not callable). `correlation_scope` is the ID's set-and-reset
    # pair, which is why it is a context manager and not a setter.
    "oncotriage/observability.py::_Console.out": ["staticmethod"],
    "oncotriage/observability.py::_Console.banner": ["staticmethod"],
    "oncotriage/observability.py::_Console.attach_bar": ["staticmethod"],
    "oncotriage/observability.py::_Console.detach_bar": ["staticmethod"],
    "oncotriage/observability.py::correlation_scope":
        ["contextlib.contextmanager"],
    "oncotriage/observability.py::StructuredLogger.std": ["property"],
    "oncotriage/retrieval/indexer.py::get_embeddings_batch._call": [
        "retry(reraise=True, stop=stop_after_attempt(5), "
        "wait=wait_exponential(multiplier=1, min=2, max=60), "
        "retry=retry_if_exception_type((RateLimitError, InternalServerError, "
        "APIConnectionError)))"],
}

_found_decorators = {}
for _f in _PKG_FILES:
    _rel = os.path.relpath(_f, _code_dir).replace(os.sep, "/")
    for _qual, _decs in _decorated_definitions(_f).items():
        _found_decorators[f"{_rel}::{_qual}"] = _decs

check("the decorator inventory of the package is exactly what it was after the "
      "conversion passes -- same definitions, same decorators, at every depth",
      _found_decorators, _DECORATOR_INVENTORY)
check("...and it is non-degenerate: the four @st.fragment tabs pass 20c-3c-1 "
      "silently dropped are in it",
      sorted(k for k, v in _found_decorators.items() if v == ["st.fragment"]),
      ["oncotriage/dashboard/tabs/drift.py::render_drift_detection_tab",
       "oncotriage/dashboard/tabs/patient_explorer.py::render_patient_explorer_tab",
       "oncotriage/dashboard/tabs/reproducibility.py::render_reproducibility_tab",
       "oncotriage/dashboard/tabs/trial_explorer.py::render_trial_explorer_tab"])
check("...and it reaches definitions NESTED inside a function, which a "
      "top-level walk cannot see -- the four api/server.py endpoints live "
      "inside create_app() and carry the most decorators in the package",
      sorted(k for k in _found_decorators if "create_app." in k),
      ["oncotriage/api/server.py::create_app.health_check",
       "oncotriage/api/server.py::create_app.match_patient_endpoint",
       "oncotriage/api/server.py::create_app.match_patient_file",
       "oncotriage/api/server.py::create_app.pipeline_info"])

# NEGATIVE CONTROL: strip a decorator from an AST COPY of the real file -- never
# the file itself -- and require the inventory to stop matching. Aimed at
# api/server.py because its decorators are the ones separated from the def by
# blank lines, which is the shape any adjacency-based check would miss.
_DEC_DIR = tempfile.mkdtemp(prefix="oncotriage-decorator-")
try:
    _server_src = open(os.path.join(_PKG_DIR, "api", "server.py"),
                       encoding="utf-8").read()
    _server_tree = ast.parse(_server_src)
    _stripped_any = False
    for _node in ast.walk(_server_tree):
        if isinstance(_node, _DEF_TYPES) and _node.name == "match_patient_endpoint":
            _node.decorator_list = []
            _stripped_any = True
    check("the control found the definition it strips (non-degeneracy)",
          _stripped_any, True)
    _copy = os.path.join(_DEC_DIR, "server_copy.py")
    with open(_copy, "w", encoding="utf-8") as _fh:
        _fh.write(ast.unparse(_server_tree))
    _control = _decorated_definitions(_copy)
    check("the inventory CATCHES a decorator removed from a copy: the "
          "definition disappears from the decorated set",
          "create_app.match_patient_endpoint" in _control, False)
    check("...while the three other endpoints in the same copy keep theirs, so "
          "the control removed one thing rather than breaking the parser",
          sorted(k for k in _control if "create_app." in k),
          ["create_app.health_check", "create_app.match_patient_file",
           "create_app.pipeline_info"])
finally:
    shutil.rmtree(_DEC_DIR, ignore_errors=True)


# ===========================================================================
# 3. THE CLIENT FACTORIES ARE LAZY AND CACHED
# ===========================================================================

print("\n" + "=" * 78)
print("3. get_openai_client / get_qdrant_client build once, on first call")
print("=" * 78)

# Counting fakes replace the two constructors BEFORE anything calls a factory,
# and get_keys is stubbed so no .env is needed. Counts, not just identity: two
# calls returning the same object would ALSO hold for a module-level singleton
# built at import, which is what this pass removed. `built_at_import == 0` is
# the check that separates the two.
_LAZY = r'''
import json
import oncotriage.config as cfg

calls = {"openai": 0, "qdrant": 0}

class FakeOpenAI:
    def __init__(self, *args, **kwargs):
        calls["openai"] += 1
        self.timeout = _FakeTimeout()

class _FakeTimeout:
    connect = 5.0

class FakeQdrant:
    def __init__(self, *args, **kwargs):
        calls["qdrant"] += 1

cfg.OpenAI = FakeOpenAI
cfg.QdrantClient = FakeQdrant
cfg.get_keys = lambda: {"openai": "sk-fake", "qdrant_url": "http://fake",
                        "qdrant_key": "fake"}

built_at_import = dict(calls)

a1 = cfg.get_openai_client()
after_first_openai = calls["openai"]
a2 = cfg.get_openai_client()
after_second_openai = calls["openai"]

q1 = cfg.get_qdrant_client()
after_first_qdrant = calls["qdrant"]
q2 = cfg.get_qdrant_client()
after_second_qdrant = calls["qdrant"]

print(json.dumps({
    "built_at_import": built_at_import,
    "openai": [after_first_openai, after_second_openai, a1 is a2, a1 is not None],
    "qdrant": [after_first_qdrant, after_second_qdrant, q1 is q2, q1 is not None],
}))
'''

_rc, _out, _err = _run(_LAZY, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("the laziness probe ran", _rc, 0)
if _rc != 0:
    fail("client factory laziness",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    check("no client was constructed at import time",
          _payload.get("built_at_import"), {"openai": 0, "qdrant": 0})
    # [after first call, after second call, same object, not None]
    # The OpenAI count is 2 after the first call, not 1: get_openai_client()
    # resolves its structured timeout first, and that builds one throwaway
    # client to read the SDK's default connect phase. The second call must not
    # move it -- both the client and the timeout are cached.
    check("get_openai_client: 2 constructions on first call (client + the "
          "throwaway the timeout reads), 2 after the second, same object, non-None",
          _payload.get("openai"), [2, 2, True, True])
    check("get_qdrant_client: 1 construction on first call, 1 after the "
          "second, same object, non-None",
          _payload.get("qdrant"), [1, 1, True, True])


# ===========================================================================
# 4. get_age_reference_date RESOLVES BY IMPORT AND STILL REFUSES TO GUESS
# ===========================================================================

print("\n" + "=" * 78)
print("4. get_age_reference_date reads config, and raises rather than today()")
print("=" * 78)

# STRUCTURAL first: the function must not read globals(), which is how it used
# to resolve the constant when every project file shared one exec namespace.
_utils_tree = ast.parse(open(_UTILS_PY, encoding="utf-8").read())
_fn = next((n for n in ast.walk(_utils_tree)
            if isinstance(n, ast.FunctionDef) and n.name == "get_age_reference_date"), None)
if _fn is None:
    fail("get_age_reference_date is defined in oncotriage/utils.py",
         "no FunctionDef of that name; the rest of section 4 tests nothing")
else:
    _calls = [n.func.id for n in ast.walk(_fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    _attrs = [n.attr for n in ast.walk(_fn) if isinstance(n, ast.Attribute)]
    check("get_age_reference_date does not call globals()", "globals" in _calls, False)
    check("...and it does not call today() or now()",
          ("today" in _attrs) or ("now" in _attrs), False)
    # NON-DEGENERATE: the two checks above would pass on an empty function.
    check("...and it does resolve the constant through the config module",
          any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "getattr"
              and any(isinstance(a, ast.Name) and a.id == "config" for a in n.args)
              for n in ast.walk(_fn)),
          True)

# BEHAVIOURAL, against a COPY of the package. CLAUDE.md prefers a copy over an
# in-place edit, and nothing here touches the shipped files at all.
_COPY_ROOT = tempfile.mkdtemp(prefix="oncotriage_snapshot_")
shutil.copytree(_PKG_DIR, os.path.join(_COPY_ROOT, "oncotriage"))
_COPY_CONFIG = os.path.join(_COPY_ROOT, "oncotriage", "config.py")

_GOOD_LINE = 'DATA_SNAPSHOT_DATE = "2026-08-03"'
_PARTIAL_LINE = 'DATA_SNAPSHOT_DATE = "2026-08"'

_copy_src = open(_COPY_CONFIG, encoding="utf-8").read()
check("the copied config carries the snapshot-date assignment to rewrite",
      _GOOD_LINE in _copy_src, True)

_PROBE = r'''
import json
from datetime import date
from oncotriage.utils import get_age_reference_date
try:
    value = get_age_reference_date()
    print(json.dumps({"raised": None, "value": value.isoformat(),
                      "is_today": value == date.today()}))
except ValueError as exc:
    print(json.dumps({"raised": "ValueError", "message": str(exc)}))
'''

if _GOOD_LINE in _copy_src:
    # -- broken: a partial date must raise
    open(_COPY_CONFIG, "w", encoding="utf-8").write(
        _copy_src.replace(_GOOD_LINE, _PARTIAL_LINE, 1))
    _rc, _out, _err = _run(_PROBE, cwd=_ELSEWHERE, extra_path=_COPY_ROOT)
    _payload = _last_json(_out) or {}
    check("a partial DATA_SNAPSHOT_DATE raises ValueError",
          _payload.get("raised"), "ValueError")
    check("...and the message names the constant, so the fix is findable",
          "DATA_SNAPSHOT_DATE" in (_payload.get("message") or ""), True)
    check("...and it did NOT quietly return a date",
          "value" in _payload, False)

    # -- restored: the real date comes back
    open(_COPY_CONFIG, "w", encoding="utf-8").write(_copy_src)
    check("the copy is restored byte-for-byte",
          open(_COPY_CONFIG, encoding="utf-8").read() == _copy_src, True)
    _rc, _out, _err = _run(_PROBE, cwd=_ELSEWHERE, extra_path=_COPY_ROOT)
    _payload = _last_json(_out) or {}
    check("with the constant restored, the reference date is 2026-08-03",
          _payload.get("value"), date(2026, 8, 3).isoformat())
    # NON-DEGENERATE: 2026-08-03 must not be today, or "never today()" would be
    # satisfied by coincidence. This check goes red on 2026-08-03 itself, which
    # is the correct behaviour -- on that day the test cannot tell the two apart
    # and should not claim to.
    check("...and that is not simply today's date",
          _payload.get("is_today"), False)

shutil.rmtree(_COPY_ROOT, ignore_errors=True)


# ===========================================================================
# 5. EVERY NUMBERED FILE IS A THIN ENTRY POINT, AND EVERY PACKAGE IMPORT
#    ANYWHERE IN THE REPOSITORY RESOLVES  (re-derived, pass 20e)
# ===========================================================================

print("\n" + "=" * 78)
print("5. no numbered file re-exports; every `from oncotriage... import` resolves")
print("=" * 78)

# WHAT THIS REPLACES. Until pass 20e section 5 pinned four historical name
# inventories and exec'd each shim into a bare namespace to prove it still
# delivered them. Every one of those inventories described a contract with the
# exec chain, and pass 20e ended the exec chain and deleted seven of the ten
# files. See section 1c for the retirement argument; this section is the half
# of the old section that is still answerable, RE-DERIVED rather than edited
# down.
#
# The old section asked two questions. Only one of them survives:
#
#   "does the shim bind exactly the names it used to"   -- no subject any more.
#   "does every name a numbered file imports out of      -- still real, and now
#    the package actually EXIST there"                     asked of every file
#                                                          in the repository
#                                                          rather than of three.
#
# THE SECOND QUESTION IS WIDER THAN IT WAS. The old probe collected
# `from oncotriage.X import ...` out of Files 01, 02 and 03 only. This one
# collects it out of every .py in the tree -- the numbered entry points, the
# eighteen tests, the two fixture harnesses -- so an import of a name the
# package lost is caught wherever it is written, and it is caught WITHOUT
# running the file, which for Files 18, 19 and 13 matters because running them
# costs money.
#
# AND IT ADDS ONE THE OLD SECTION COULD NOT ASK. A thin entry point is defined
# by what it does NOT do: it must not put names into anyone else's namespace,
# because there is no longer anyone else's namespace to put them into. The
# structural form of that is "no module-level `from oncotriage... import` whose
# names the file's own __main__ block does not use" -- which is a re-export in
# all but name, and is what every deleted shim was.

# --- 5a. every package import in the repository resolves -------------------

_WANTED = {}
for _path in _REPO_ALL_PY:
    try:
        _tree = ast.parse(open(_path, encoding="utf-8").read())
    except SyntaxError:
        continue
    for _node in ast.walk(_tree):
        if isinstance(_node, ast.ImportFrom) and (_node.module or "").startswith("oncotriage"):
            for _alias in _node.names:
                if _alias.name != "*":
                    _WANTED.setdefault(_node.module, set()).add(_alias.name)
_WANTED = {k: sorted(v) for k, v in sorted(_WANTED.items())}

# NON-DEGENERATE. A probe over an empty set proves nothing, and the number is
# a floor rather than a pin so that adding an import is not a failure.
check("the repository imports at least 200 names out of the package (a probe "
      "over an empty set would prove nothing)",
      sum(len(v) for v in _WANTED.values()) >= 200, True)
check("...spread over at least 20 package modules",
      len(_WANTED) >= 20, True)

# WHY THIS DOES NOT USE hasattr FOR EVERY NAME (inherited from the retired
# probe, and still the right call). oncotriage.paths has a PEP 562 __getattr__
# and a path name RESOLVES when it is read. On a healthy tree hasattr returns
# True; on a checkout without the sibling directories the resolver raises
# RuntimeError, and Python does not convert that to AttributeError -- so hasattr
# PROPAGATES it and this probe would abort with a traceback instead of reporting
# which names are missing. It would fail on exactly the machine the package
# split exists to work on. The question is "does the package EXPOSE this name",
# not "can it be resolved right now", and PATH_NAMES answers the first without
# attempting the second.
_IMPORT_PROBE = r'''
import importlib, json, sys
missing = []
unimportable = []
checked_via_path_names = []
for module_name, names in json.loads(sys.argv[1] if len(sys.argv) > 1 else "{}").items():
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:                                    # noqa: BLE001
        unimportable.append(f"{module_name}: {type(exc).__name__}: {exc}")
        continue
    lazy = set(getattr(module, "PATH_NAMES", ()))
    for name in names:
        if name in lazy:
            checked_via_path_names.append(module_name + "." + name)
            continue
        if hasattr(module, name):
            continue
        # A SUBMODULE IS NOT AN ATTRIBUTE UNTIL SOMETHING IMPORTS IT, and
        # `from oncotriage import config` is the commonest import shape in this
        # package. hasattr(oncotriage, "config") is False in a process that has
        # not imported it, so the first version of this probe reported seven
        # perfectly good modules as missing names. Try the submodule import
        # before concluding anything.
        try:
            importlib.import_module(module_name + "." + name)
        except ImportError:
            missing.append(module_name + "." + name)
print(json.dumps({"missing": missing,
                  "unimportable": unimportable,
                  "checked_via_path_names": sorted(checked_via_path_names)}))
'''

_rc, _out, _err = _run(
    _IMPORT_PROBE.replace("sys.argv[1] if len(sys.argv) > 1 else \"{}\"",
                          repr(json.dumps(_WANTED))),
    cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("the package-surface probe ran", _rc, 0)
if _rc == 0:
    _payload = _last_json(_out) or {}
    check("every name imported anywhere in the repository exists on the "
          "package module it is imported from",
          _payload.get("missing"), [])
    check("...and every one of those modules imported at all",
          _payload.get("unimportable"), [])
    # NON-DEGENERATE. "missing == []" is also what a probe that checked nothing
    # returns. The lazy-path branch must have been taken -- if PATH_NAMES ever
    # stopped being exported, every path name would fall through to hasattr and
    # this probe would be back to aborting on a tree it cannot resolve.
    _via_paths = [n for n in (_payload.get("checked_via_path_names") or [])
                  if n.startswith("oncotriage.paths.")]
    check("...and the lazy path names were checked by membership rather than "
          "by hasattr, so a broken tree cannot abort this probe",
          len(_via_paths) >= 5, True)
else:
    fail("package surface probe",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")

# --- 5b(i). no numbered file re-exports ------------------------------------
#
# A re-export is a module-level `from oncotriage... import NAME` whose NAME the
# file itself never reads. Under the exec chain that was the whole POINT of a
# shim; with the chain gone it is a name put into a namespace nobody reads,
# which is the dead declaration check 2h exists to catch -- and, worse, it is
# the first half of rebuilding a shim.
#
# `import oncotriage` on its own is exempt and must be: it is the six-line
# sys.path bootstrap every entry point carries, and its whole job is the side
# effect.

_NUMBERED_FILES = sorted(
    os.path.join(_code_dir, n) for n in os.listdir(_code_dir)
    if re.match(r"^\d\d- .*\.py$", n)
)
check("the numbered-file scan found the entry points",
      len(_NUMBERED_FILES) >= 20, True)

_REEXPORTERS = {}
for _path in _NUMBERED_FILES:
    _src = open(_path, encoding="utf-8").read()
    _tree = ast.parse(_src)
    _imported_at_module_scope = set()
    for _node in _tree.body:
        if isinstance(_node, ast.ImportFrom) and (_node.module or "").startswith("oncotriage"):
            for _alias in _node.names:
                _imported_at_module_scope.add(_alias.asname or _alias.name)
    if not _imported_at_module_scope:
        continue
    # Reads: any Name load or Attribute value anywhere in the file, plus any
    # string literal, on the same generous basis as check 2h -- a false
    # negative here is a missed re-export a reader can still find, a false
    # positive is a check people route around.
    _read = set()
    for _node in ast.walk(_tree):
        if isinstance(_node, ast.Name) and not isinstance(_node.ctx, ast.Store):
            _read.add(_node.id)
        elif isinstance(_node, ast.Attribute):
            _read.add(_node.attr)
        elif isinstance(_node, ast.Constant) and isinstance(_node.value, str):
            for _candidate in list(_imported_at_module_scope):
                if _candidate in _node.value:
                    _read.add(_candidate)
    _unread = sorted(_imported_at_module_scope - _read)
    if _unread:
        _REEXPORTERS[os.path.basename(_path)] = _unread

# THERE IS NO EXEMPTION TABLE HERE ANY MORE (pass 20f-3), AND THE TABLE IS
# DELETED RATHER THAN EMPTIED.
#
# It held one entry: `{"24- Airflow Manager.py": ["stop_airflow", "trigger_dag"]}`.
# That file imported five names and called one; the other four existed so that
# uncommenting a line of its `__main__` MENU would not raise NameError. Two of
# the four were additionally named in its module docstring, so the
# string-literal arm of the read scan above already counted them -- the two
# exempted were the two named ONLY in COMMENTS, which no AST walk can see. The
# asymmetry was the argument for an exemption rather than a widening: counting
# comments as reads would let any dead import be excused by mentioning it in a
# comment, which is the opposite of what this check is for.
#
# Pass 20f-3 replaced that menu with a real argparse CLI -- the follow-up pass
# 20c-3c-2 recorded and this file's exemption comment repeated. All four
# functions are now CALLED by `main()`, so all four are ordinary reads and the
# finding is gone at its source.
#
# WHY THE DICT AND ITS STALENESS CHECK GO TOGETHER. The check was "...and the
# one exemption is still needed", i.e. every key in the dict is still a file the
# scan reports. With an empty dict that check iterates nothing and passes for
# free -- a check that has stopped checking, which is the exact shape this
# project treats as a defect (see the retirements pass 20e argued, and
# `PASSWORD_SOURCE_ARGUMENT` before them). Keeping an empty dict would ALSO
# invite the next re-export to be silenced by adding a line rather than by being
# fixed. The scan itself is unchanged and is now unconditional: any numbered
# file that imports a package name it never reads is reported, with no way to
# opt out. THIS FILE'S CHECK COUNT DROPS BY ONE, and that is the whole of the
# movement pass 20f-3 makes here.
_REEXPORT_FINDINGS = dict(_REEXPORTERS)
check("no numbered file imports a package name at module scope that it never "
      "reads, i.e. none of them is a re-export shim (no exemptions)",
      _REEXPORT_FINDINGS, {})
if _REEXPORT_FINDINGS:
    for _name, _unread in sorted(_REEXPORT_FINDINGS.items()):
        print(f"       {_name}: {_unread}")

# NEGATIVE CONTROL. The detector must be shown to fire, or "no re-exporters"
# is indistinguishable from a walk that found nothing to look at. A copy of a
# real entry point with a re-export line planted at module scope.
_RX_DIR = tempfile.mkdtemp(prefix="oncotriage_reexport_ctrl_")
_RX_PATH = os.path.join(_RX_DIR, "99- Planted.py")
with open(_RX_PATH, "w", encoding="utf-8") as _fh:
    _fh.write("import os\n"
              "from oncotriage.config import MAX_WORKERS, PRICING_CONFIG\n"
              "if __name__ == '__main__':\n"
              "    print(MAX_WORKERS)\n")


def _unread_package_imports(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    imported = {a.asname or a.name for n in tree.body
                if isinstance(n, ast.ImportFrom)
                and (n.module or "").startswith("oncotriage")
                for a in n.names}
    read = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Store):
            read.add(n.id)
        elif isinstance(n, ast.Attribute):
            read.add(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            for c in list(imported):
                if c in n.value:
                    read.add(c)
    return sorted(imported - read)


check("negative control: a planted re-export IS reported",
      _unread_package_imports(_RX_PATH), ["PRICING_CONFIG"])
check("...and the name the planted file genuinely uses is NOT reported, so the "
      "detector distinguishes a re-export from an import",
      "MAX_WORKERS" in _unread_package_imports(_RX_PATH), False)
shutil.rmtree(_RX_DIR, ignore_errors=True)



# ===========================================================================
# 5b. THE FILE 10 SPLIT HAS EXACTLY ONE SHARED NAME
# ===========================================================================

print("\n" + "=" * 78)
print("5b. stage and histology share exactly one name, and it lives in negation")
print("=" * 78)

# THE EVIDENCE FOR THE SPLIT, re-derived here rather than asserted in a comment.
#
# "10- Structured Eligibility Extractor.py" was two extractors in one file, and
# the claim that it splits cleanly rests on exactly one measurement: how many
# top-level names in one half are referenced by the other. The answer was 1 --
# _is_histology_negated() calls _is_negated() -- which is why negation.py
# exists and why the split is a fact rather than a preference.
#
# The measurement is repeated against the SHIPPED modules, so the claim decays
# into a failure if someone later adds a second edge instead of moving the
# shared name into negation.py where it belongs.
#
# A grep could not have settled this. It cannot tell a call from a mention in
# a docstring, and File 10's docstrings mention _is_negated by name.

_STAGE_PY = os.path.join(_PKG_DIR, "extraction", "stage.py")
_HIST_PY = os.path.join(_PKG_DIR, "extraction", "histology.py")
_NEG_PY = os.path.join(_PKG_DIR, "extraction", "negation.py")


def _top_level_names(path: str) -> set:
    """Names a module binds at top level, imports EXCLUDED.

    Imports are excluded on purpose: stage.py imports _is_negated, and counting
    that as a definition would make the two halves look like they define the
    same name rather than share one.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _loaded_names(path: str) -> set:
    """Every Name read anywhere in the module."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


_STAGE_DEFS, _HIST_DEFS, _NEG_DEFS = (_top_level_names(f) for f in
                                      (_STAGE_PY, _HIST_PY, _NEG_PY))

# NON-DEGENERATE FIRST. Every one of the three checks below is an intersection,
# and an intersection with an empty set is empty. If any of these modules
# stopped defining anything -- a bad slice, a truncated file -- the edge counts
# would all read zero and this section would certify a split that no longer
# exists.
check("stage.py, histology.py and negation.py all define a plausible number "
      "of top-level names (non-degeneracy)",
      len(_STAGE_DEFS) >= 20 and len(_HIST_DEFS) >= 20 and len(_NEG_DEFS) == 4,
      True)

check("histology.py references nothing that stage.py defines",
      sorted(_loaded_names(_HIST_PY) & _STAGE_DEFS), [])
check("stage.py references nothing that histology.py defines",
      sorted(_loaded_names(_STAGE_PY) & _HIST_DEFS), [])
check("the one name they DO share is _is_negated, and it lives in negation.py",
      sorted(_NEG_DEFS & (_loaded_names(_STAGE_PY) | _loaded_names(_HIST_PY))),
      ["_is_negated"])
check("...and both halves import it rather than redefining it",
      "_is_negated" in _STAGE_DEFS or "_is_negated" in _HIST_DEFS, False)


# ===========================================================================
# 5c. THE DEPS SEAM ANSWERS WITHOUT BUILDING  (re-derived, pass 20e)
# ===========================================================================

print("\n" + "=" * 78)
print("5c. deps.peek / resolution_state / RESOLUTION_STATES: answers, no build")
print("=" * 78)

# WHAT THIS SECTION USED TO BE, AND WHY IT COULD NOT SURVIVE UNCHANGED.
#
# Until pass 20e it exercised `_LazyAgentDependency`, a proxy class defined in
# "13- LangGraph Agent.py"'s re-export shim. That class existed for one reason:
# an exec-chain caller reads a NAME out of a namespace and cannot call an
# accessor, so the shim had to BIND medcpt_tokenizer, medcpt_model and
# _bm25_query_model to something -- and binding the real objects would have
# loaded MedCPT (~110 MB) and FastEmbed for the seven files that chained File 13
# and never scored a pair.
#
# Pass 20e deleted that shim, having measured that no file in the repository
# chains it. THE PROXY WENT WITH IT AND IS NOT REBUILT: every consumer now calls
# deps.get_medcpt_tokenizer() / get_medcpt_model() / get_bm25_query_model()
# directly, which is lazier than the proxy was and, being the real accessor,
# cannot answer wrongly about an object it never consulted. So this section
# cannot keep testing a class that does not exist, and it must not be quietly
# dropped either: three facts it established are about the PACKAGE and are still
# load-bearing.
#
#   1. deps.peek() and deps.resolution_state() must not BUILD. They are the
#      diagnostic path -- a debugger rendering locals, a log line formatting an
#      object, a harness reporting what it redirected -- and a diagnostic that
#      costs 110 MB is a diagnostic nobody runs. This is measured by COUNTING
#      FACTORY CALLS, which is the only thing that separates the two shapes:
#      both return a plausible answer.
#   2. peek() must distinguish "nothing installed" from a legitimately cached
#      None. MESH_FILTER is genuinely None on a degraded run, so `is None`
#      cannot mean "unresolved"; that is what deps.UNSET is for.
#   3. deps.RESOLUTION_STATES documents itself as the CLOSED set of values
#      resolution_state() can return, "so a caller can branch on it
#      exhaustively". Nothing in the repository read it until pass 20c-3i wired
#      it in here -- it was a declaration with nothing holding it to the code,
#      the same shape as PASSWORD_SOURCE_ARGUMENT. If this section simply
#      retired, that tuple would go back to being unread, and check 2h would
#      report it. It is read here, and both observed states are checked for
#      membership, so a fourth state added to the function and not to the tuple
#      fails.
#
# THE RULE THE PROXY TAUGHT IS RECORDED IN oncotriage/agent/deps.py rather than
# here, because it is advice to whoever writes the next proxy: CPython looks an
# implicit special method up on the TYPE, never through __getattr__, so a proxy
# forwarding only __getattr__ and __call__ answers bool(), ==, len, iter, `in`
# and repr() about ITSELF -- confidently, and wrongly.

_SEAM_DEMO = r'''
import json

from oncotriage.agent import deps

CALLS = {"n": 0}


class Sentinel:
    """Unambiguous stand-in. Its repr is distinctive so it can be looked for."""

    def __repr__(self):
        return "<seam-sentinel>"


SENTINEL = Sentinel()


def counting_factory():
    CALLS["n"] += 1
    return SENTINEL


# The key under test is MESH_FILTER, deliberately: it is the one key whose real
# value may legitimately be None, so it is the only one where "unresolved" and
# "cached None" have to be told apart by something other than the value.
KEY = deps.MESH_FILTER

result = {}

# --- 1. UNRESOLVED: neither query builds, and peek says UNSET, not None ------
deps.clear_override(KEY)
deps._CACHE.pop(KEY, None)
result["state_unresolved"] = deps.resolution_state(KEY)
result["peek_unresolved_is_UNSET"] = deps.peek(KEY) is deps.UNSET
result["peek_unresolved_is_not_None"] = deps.peek(KEY) is not None
result["is_resolved_unresolved"] = deps.is_resolved(KEY)
result["calls_after_unresolved_queries"] = CALLS["n"]

# --- 2. OVERRIDE: both queries see it, still without building ----------------
deps.set_override(KEY, SENTINEL)
result["state_with_override"] = deps.resolution_state(KEY)
result["peek_is_sentinel"] = deps.peek(KEY) is SENTINEL
result["is_resolved_with_override"] = deps.is_resolved(KEY)
result["calls_after_override_queries"] = CALLS["n"]

# --- 3. the accessor DOES hand the agent the override ------------------------
# Non-degeneracy for everything above: if the override never reached the
# accessor, "peek returns the sentinel" would be a fact about peek alone.
result["accessor_returns_sentinel"] = deps.get_mesh_filter() is SENTINEL

deps.clear_override(KEY)
result["state_after_override_removed"] = deps.resolution_state(KEY)

# --- 4. CACHED: a build happens once, and then the queries are free again ----
# A cached value is the third state, and it must be reachable without the
# queries being what reached it.
_before_build = CALLS["n"]
# Written directly rather than by calling the accessor: calling it would load
# the real MeSH lookups off disk, which section 2 forbids this file's
# subprocesses from doing and which this section does not need. What is under
# test is that the QUERIES do not build, not how the cache got filled.
deps._CACHE[KEY] = SENTINEL
result["state_cached"] = deps.resolution_state(KEY)
result["peek_cached_is_sentinel"] = deps.peek(KEY) is SENTINEL
result["cached_keys_contains"] = KEY in deps.cached_keys()
result["calls_after_cached_queries"] = CALLS["n"] - _before_build

# --- 5. the closed set ------------------------------------------------------
result["observed_states"] = sorted({result["state_unresolved"],
                                    result["state_with_override"],
                                    result["state_cached"]})
result["all_observed_in_closed_set"] = all(
    s in deps.RESOLUTION_STATES for s in result["observed_states"])
result["closed_set_size"] = len(deps.RESOLUTION_STATES)

# --- 6. an unknown key is refused, not silently ignored ----------------------
# OVERRIDE_KEYS is closed for the same reason: a dropped override is the failure
# this module exists to prevent, and the quiet version of it is the dangerous
# one.
try:
    deps.set_override("no-such-key", SENTINEL)
    result["unknown_key_raises"] = False
except KeyError:
    result["unknown_key_raises"] = True

deps._CACHE.pop(KEY, None)
print(json.dumps(result))
'''

_rc, _out, _err = _run(_SEAM_DEMO, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("the deps-seam demonstration ran", _rc, 0)
if _rc != 0:
    fail("deps seam demonstration",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}

    # --- the three states, each observed rather than assumed ---------------
    check("an untouched key reports 'unresolved'",
          _payload.get("state_unresolved"), "unresolved")
    check("...and peek returns UNSET for it, NOT None -- MESH_FILTER may be "
          "legitimately None, so the two have to be distinguishable",
          _payload.get("peek_unresolved_is_UNSET"), True)
    check("...and UNSET is not None, so the distinction is real",
          _payload.get("peek_unresolved_is_not_None"), True)
    check("...and is_resolved agrees", _payload.get("is_resolved_unresolved"), False)
    check("an installed override reports 'override'",
          _payload.get("state_with_override"), "override")
    check("...and peek returns the installed object",
          _payload.get("peek_is_sentinel"), True)
    check("...and is_resolved agrees", _payload.get("is_resolved_with_override"), True)
    check("a cached value reports 'cached'", _payload.get("state_cached"), "cached")
    check("...and peek returns it", _payload.get("peek_cached_is_sentinel"), True)
    check("...and cached_keys() lists the key",
          _payload.get("cached_keys_contains"), True)

    # --- NON-DEGENERACY: the override actually reaches the agent's accessor.
    # Without this, every assertion above is a statement about peek() rather
    # than about the seam, and would hold just as well if set_override wrote
    # into a dict nothing else read.
    check("the accessor hands back the override, so peek is reporting the "
          "seam rather than a private dict",
          _payload.get("accessor_returns_sentinel"), True)
    check("...and removing the override puts the key back to unresolved, so "
          "the state query reads live state rather than a constant",
          _payload.get("state_after_override_removed"), "unresolved")

    # --- THE MEASUREMENT THIS SECTION EXISTS FOR: no query built anything.
    # Counted factory calls, not inspected values: a query that resolved would
    # return exactly the same answers.
    check("NOT ONE of the unresolved-state queries called a factory",
          _payload.get("calls_after_unresolved_queries"), 0)
    check("...nor did the override-state queries",
          _payload.get("calls_after_override_queries"), 0)
    check("...nor did the cached-state queries",
          _payload.get("calls_after_cached_queries"), 0)

    # --- the closed set, and why it is checked here ------------------------
    check("all three observed states are members of deps.RESOLUTION_STATES, "
          "which is what makes that tuple a closed set rather than a comment",
          _payload.get("all_observed_in_closed_set"), True)
    check("...and the set is the size it claims (non-degeneracy: membership in "
          "an empty or one-element tuple would prove nothing)",
          _payload.get("closed_set_size"), 3)
    check("...and all three of its members were actually observed, so the "
          "tuple is exhausted rather than sampled",
          _payload.get("observed_states"), ["cached", "override", "unresolved"])

    # --- the key set is closed too -----------------------------------------
    check("an override for an unknown key raises KeyError rather than being "
          "silently ignored", _payload.get("unknown_key_raises"), True)



# ===========================================================================
# 5d. THE DEPS SEAM UNDER MAX_WORKERS THREADS
# ===========================================================================

print("\n" + "=" * 78)
print("5d. MAX_WORKERS threads through every accessor: one object, one build")
print("=" * 78)

# WHY THIS EXISTS NOW AND NOT EARLIER.
#
# oncotriage/agent/deps.py has only ever RUN single-threaded. Every harness that
# exercised it -- Files 35, 36, 37, 45, 46 -- drives one patient at a time on
# one thread. Pass 20c-3a moved the whole override-then-cache sequence inside
# the lock on the argument that "25- Batch Runner.py" drives MAX_WORKERS = 12
# threads through it, and that argument was correct and UNTESTED: the batch
# runner needs a Qdrant index, an OpenAI key and 22,000 bundles, so nothing in
# the test suite had ever put two threads through an accessor at once.
#
# Pass 20c-3b makes the batch runner a package module, so the claim is now
# checkable without any of that. This drives MAX_WORKERS threads at every
# accessor simultaneously, with COUNTING FACTORIES installed in place of the
# real ones, and asserts the two properties the seam exists to provide:
#
#   1. every thread gets THE SAME object for a key. Two Qdrant clients is two
#      connection pools, and the per-patient latency figures in inferences.db
#      would then describe two different transports with nothing in the row
#      saying so.
#   2. the factory ran EXACTLY ONCE per key. "same object" alone would also hold
#      if the factory ran twelve times and eleven results were discarded -- for
#      a client that opens a pool, or a model that loads 110 MB, that is a real
#      cost that identity cannot see.
#
# The counting factories are installed by monkeypatching deps' own private
# builders in the subprocess, which is legitimate here in a way it is not in
# production code: the object under test IS the caching machinery, so the thing
# that must be replaced is what it caches.
#
# A BARRIER, not just a thread pool. Threads that start staggered would let the
# first one finish building before the second even asks, and the race under test
# would never occur. threading.Barrier makes all twelve arrive at the accessor
# together.

_DEPS_CONCURRENCY = r'''
import json, threading
from concurrent.futures import ThreadPoolExecutor

from oncotriage.agent import deps
from oncotriage.config import MAX_WORKERS

# Counting stand-ins for every real factory. Each records that it ran and
# returns a distinct object, so "the same object" is a real claim rather than a
# consequence of everything being None.
BUILDS = {}
LOCK = threading.Lock()


class Built:
    def __init__(self, key):
        self.key = key

    def __repr__(self):
        return f"<Built {self.key}>"


def make_factory(key):
    def factory():
        with LOCK:
            BUILDS[key] = BUILDS.get(key, 0) + 1
        # A build that is instantaneous cannot lose a race. Yielding the GIL
        # here is what gives a second thread the chance to enter and get it
        # wrong, which is the whole point of the exercise.
        for _ in range(200):
            pass
        return Built(key)
    return factory


ACCESSORS = {
    deps.OPENAI_CLIENT:    ("get_openai_client", "_OPENAI"),
    deps.QDRANT_CLIENT:    ("get_qdrant_client", "_QDRANT"),
    deps.BM25_QUERY_MODEL: ("get_bm25_query_model", "_BM25"),
    deps.MEDCPT_TOKENIZER: ("get_medcpt_tokenizer", "_TOK"),
    deps.MEDCPT_MODEL:     ("get_medcpt_model", "_MODEL"),
    deps.CANCER_REGISTRY:  ("get_cancer_registry", "_CANCER"),
    deps.LAB_REGISTRY:     ("get_lab_registry", "_LAB"),
    deps.MESH_FILTER:      ("get_mesh_filter", "_MESH"),
}

# Rebuild each accessor over a counting factory, keeping deps' own _resolve --
# the machinery under test -- untouched.
PATCHED = {}
for key, (accessor_name, _label) in ACCESSORS.items():
    PATCHED[key] = (lambda k=key, f=make_factory(key): deps._resolve(k, f))

N = MAX_WORKERS
barrier = threading.Barrier(N)
results = {key: [] for key in ACCESSORS}
results_lock = threading.Lock()


def worker(_i):
    barrier.wait()                      # all N arrive together
    got = {}
    for key, call in PATCHED.items():
        got[key] = call()
    with results_lock:
        for key, value in got.items():
            results[key].append(value)


with ThreadPoolExecutor(max_workers=N) as pool:
    list(pool.map(worker, range(N)))

same_object = {key: (len({id(v) for v in vals}) == 1) for key, vals in results.items()}
counts = {key: len(vals) for key, vals in results.items()}

print(json.dumps({
    "workers": N,
    "keys": sorted(ACCESSORS),
    "observations_per_key": sorted(set(counts.values())),
    "keys_with_one_object": sorted(k for k, ok in same_object.items() if ok),
    "keys_with_more_than_one_object": sorted(k for k, ok in same_object.items() if not ok),
    "builds": {k: BUILDS.get(k, 0) for k in sorted(ACCESSORS)},
    "cached_keys": deps.cached_keys(),
}))
'''

_rc, _out, _err = _run(_DEPS_CONCURRENCY, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("the deps concurrency probe ran", _rc, 0)
if _rc != 0:
    fail("deps concurrency", f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    _keys = _payload.get("keys") or []

    # NON-DEGENERATE FIRST. All of this passes trivially with one thread, or
    # with zero keys, or if every worker silently died.
    check("it ran with MAX_WORKERS threads, and MAX_WORKERS is more than one",
          (_payload.get("workers") or 0) > 1, True)
    check("every accessor key was exercised", len(_keys), 8)
    check("every key was observed by every worker (no worker died silently)",
          _payload.get("observations_per_key"), [_payload.get("workers")])

    check("every key handed the same object to all MAX_WORKERS threads",
          _payload.get("keys_with_more_than_one_object"), [])
    check("...for every one of the eight keys, not just the ones that happened "
          "to be fast", sorted(_payload.get("keys_with_one_object") or []),
          sorted(_keys))
    check("each factory ran EXACTLY ONCE, so 'same object' is not twelve builds "
          "with eleven thrown away",
          sorted(set((_payload.get("builds") or {}).values())), [1])
    check("...and deps reports exactly those eight keys as cached, through the "
          "non-building query added in pass 20c-3b",
          sorted(_payload.get("cached_keys") or []), sorted(_keys))


# ===========================================================================
# 5e. THE INFERENCE WRITE LOCK
# ===========================================================================

print("\n" + "=" * 78)
print("5e. concurrent log_inference writes are serialized, and every row lands")
print("=" * 78)

# WHAT THIS REPLACES.
#
# "25- Batch Runner.py" lines 65-73 wrapped log_inference in a lock IN ITS OWN
# NAMESPACE, by rebinding the name after chaining File 14. That protected the
# batch runner and NOTHING ELSE, and there is a second concurrent writer:
# "17- FastAPI Server.py" calls log_inference from loop.run_in_executor(...),
# once per in-flight request, on the event loop's thread pool. Two overlapping
# POST /match requests wrote to one SQLite file through two connections with
# nothing serializing them.
#
# Pass 20c-3b moves the lock into oncotriage/storage/database_logger.py, beside
# the writes it protects. Both callers get it; neither has to know.
#
# WHERE THE RACE ACTUALLY IS, MEASURED RATHER THAN ASSUMED.
#
# The first version of this check drove N threads at an ALREADY-INITIALIZED
# database and expected the unlocked control to lose rows. IT DID NOT: both
# arms landed every row. That result is reported here rather than tuned away,
# because it says something true. On the steady-state INSERT path, SQLite's own
# file locking plus the sqlite3 module's 5-second busy timeout already serialize
# two connections writing to one file, and at this project's contention -- a few
# milliseconds of writing at the end of a ~70-second per-patient pipeline --
# nothing waits long enough to time out. The lock is not what saves that path.
#
# THE PATH IT DOES SAVE IS THE SCHEMA MIGRATION, and there the loss is real,
# silent, and reproducible:
#
#   _ensure_database -> initialize_database runs CREATE TABLE IF NOT EXISTS and
#   then, for each entry in INFERENCE_COLUMN_ADDITIONS, a PRAGMA table_info
#   check followed by an ALTER TABLE ADD COLUMN. ALTER TABLE ADD COLUMN has no
#   IF NOT EXISTS form -- the PRAGMA check IS the guard. Two threads arriving at
#   a fresh database both read the PRAGMA (column absent) and both issue the
#   ALTER; the second gets
#
#       sqlite3.OperationalError: duplicate column name: retrieval_degraded
#
#   which propagates into log_inference's try block, is caught by its
#   `except sqlite3.Error` -- the handler that exists so a logging fault cannot
#   kill the pipeline -- printed as "Database logging failed (non-critical)",
#   and the row is GONE. A run that lost rows this way reports success.
#
# That is exactly the defect class this project exists to remove, and it was
# reachable from the API path, on every first request against a new or migrated
# database, for as long as the only lock in the project lived in File 25.
#
# BOTH SCENARIOS ARE MEASURED BELOW and both results are asserted, so the honest
# finding (the lock does not change the steady-state insert path) is recorded
# rather than quietly dropped.
#
# THE CONTROL IS A COPY, never an edit in place: the module source is parsed,
# every `with _WRITE_LOCK:` is replaced by its own body, and the result is
# exec'd under a different module name.
#
# REPEATED TRIALS, because a race is not deterministic. Measured over 20 trials
# at 24 threads: the unlocked arm lost rows in 18, the locked arm in 0. Eight
# trials puts the chance of a false "clean" run at roughly 1e-8, and the locked
# arm must be clean in ALL of them.

_WRITE_LOCK_DEMO = r'''
import ast, io, json, os, contextlib, sqlite3, tempfile, threading
from concurrent.futures import ThreadPoolExecutor

from oncotriage.config import MAX_WORKERS
from oncotriage.storage import database_logger


# Twice MAX_WORKERS, with two rows each rather than many: the race is at the
# FIRST write against a fresh database, so what matters is how many threads
# arrive at the migration together, not how much they write afterwards.
THREADS = max(2 * MAX_WORKERS, 24)
ROWS_PER_THREAD = 2
TOTAL = THREADS * ROWS_PER_THREAD
TRIALS = 8


def make_result(i):
    """The minimum a terminal node emits that log_inference will accept."""
    return {
        "patient_id": f"patient-{i:04d}",
        "timestamp": f"2026-08-05T00:00:{i % 60:02d}",
        "matching_model": "gpt-5.6-terra",
        "llm_classifier_input_tokens": 10,
        "llm_classifier_output_tokens": 5,
        "matches": [], "near_misses": [], "not_evaluable": [],
        "stage_timings": {},
    }


PATIENT = {"demographics": {}, "conditions": [], "medications": [], "allergies": []}


def drive(log_fn, db_path):
    """THREADS threads x ROWS_PER_THREAD writes each, all starting together."""
    barrier = threading.Barrier(THREADS)
    errors = []

    def worker(t):
        barrier.wait()
        for r in range(ROWS_PER_THREAD):
            try:
                log_fn(make_result(t * ROWS_PER_THREAD + r), PATIENT, db_path=db_path)
            except Exception as exc:                              # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")

    # log_inference prints one line per row; silenced so the JSON payload is the
    # last thing on stdout.
    with contextlib.redirect_stdout(io.StringIO()):
        with ThreadPoolExecutor(max_workers=THREADS) as pool:
            list(pool.map(worker, range(THREADS)))

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0]
        distinct = conn.execute(
            "SELECT COUNT(DISTINCT patient_id) FROM inferences").fetchone()[0]
    finally:
        conn.close()
    return {"rows": rows, "distinct": distinct, "errors": errors[:2],
            "error_count": len(errors)}


# --- the control: a COPY of the module with every `with _WRITE_LOCK:` removed
source = open(database_logger.__file__, encoding="utf-8").read()
tree = ast.parse(source)


class StripLock(ast.NodeTransformer):
    removed = 0

    def visit_With(self, node):
        self.generic_visit(node)
        names = [i.context_expr.id for i in node.items
                 if isinstance(i.context_expr, ast.Name)]
        if "_WRITE_LOCK" in names:
            StripLock.removed += 1
            return node.body
        return node


stripped = ast.fix_missing_locations(StripLock().visit(tree))
unlocked_ns = {"__name__": "database_logger_unlocked",
               "__file__": database_logger.__file__}
exec(compile(ast.unparse(stripped), "<database_logger_unlocked>", "exec"), unlocked_ns)

TMP = tempfile.mkdtemp(prefix="oncotriage_writelock_")

# --- SCENARIO A: the schema already exists --------------------------------
# Reported because it is the honest finding: the lock changes nothing here.
steady = {}
for label, log_fn, init_fn in (
        ("locked", database_logger.log_inference, database_logger.initialize_database),
        ("unlocked", unlocked_ns["log_inference"], unlocked_ns["initialize_database"])):
    db = os.path.join(TMP, f"steady-{label}.db")
    with contextlib.redirect_stdout(io.StringIO()):
        init_fn(db)
    steady[label] = drive(log_fn, db)

# --- SCENARIO B: a FRESH database, threads race the migration -------------
fresh = {"locked": [], "unlocked": []}
for label, log_fn in (("locked", database_logger.log_inference),
                      ("unlocked", unlocked_ns["log_inference"])):
    for trial in range(TRIALS):
        # A fresh file AND a fresh memo: _INITIALIZED_DATABASES is what stops
        # the second call re-running the migration, so a stale entry would make
        # every trial after the first one prove nothing.
        db = os.path.join(TMP, f"fresh-{label}-{trial}.db")
        if label == "locked":
            database_logger._INITIALIZED_DATABASES.clear()
        else:
            unlocked_ns["_INITIALIZED_DATABASES"].clear()
        fresh[label].append(drive(log_fn, db))

print(json.dumps({
    "threads": THREADS,
    "trials": TRIALS,
    "expected_rows": TOTAL,
    "locks_stripped": StripLock.removed,
    "steady": steady,
    "fresh_lossy_trials": {
        label: sum(1 for r in runs if r["rows"] != TOTAL)
        for label, runs in fresh.items()
    },
    "fresh_worst_loss": {
        label: TOTAL - min(r["rows"] for r in runs)
        for label, runs in fresh.items()
    },
    "fresh_raised": {
        label: sum(r["error_count"] for r in runs) for label, runs in fresh.items()
    },
}))
'''

_rc, _out, _err = _run(_WRITE_LOCK_DEMO, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("the write-lock demonstration ran", _rc, 0)
if _rc != 0:
    fail("write lock", f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-6:]}")
else:
    _payload = _last_json(_out) or {}
    _expected = _payload.get("expected_rows")
    _trials = _payload.get("trials")
    _steady = _payload.get("steady") or {}
    _lossy = _payload.get("fresh_lossy_trials") or {}

    # NON-DEGENERATE FIRST. Every assertion below passes trivially with one
    # thread, one trial, or a "control" that is a copy of the same module.
    check("it ran with more than one thread", (_payload.get("threads") or 0) > 1, True)
    check("...writing more than one row", (_expected or 0) > 1, True)
    check("...over more than one trial, because a race is not deterministic",
          (_trials or 0) > 1, True)
    check("the control actually removed the lock (all three sites)",
          _payload.get("locks_stripped"), 3)

    # SCENARIO A, reported honestly: the lock does NOT change this path.
    check("steady state, WITH the lock: every row lands",
          (_steady.get("locked") or {}).get("rows"), _expected)
    check("steady state, WITHOUT the lock: every row ALSO lands -- SQLite's own "
          "file locking already serializes two connections on one file, so this "
          "is not what the lock is for",
          (_steady.get("unlocked") or {}).get("rows"), _expected)

    # SCENARIO B: the migration race, which is what the lock is for.
    check(f"fresh database, WITH the lock: zero lossy trials out of {_trials}",
          _lossy.get("locked"), 0)
    check("...and nothing raised in any of them",
          (_payload.get("fresh_raised") or {}).get("locked"), 0)

    # THE CONTROL. If this passed, the check above would be measuring nothing.
    check("fresh database, WITHOUT the lock: rows are LOST (negative control)",
          (_lossy.get("unlocked") or 0) >= 1, True)
    print(f"       control: {_lossy.get('unlocked')}/{_trials} trials lost rows, "
          f"worst loss {(_payload.get('fresh_worst_loss') or {}).get('unlocked')} "
          f"of {_expected}; locked arm lost nothing in "
          f"{_trials}/{_trials} trials")



# ===========================================================================
# 6. THE DASHBOARD (pass 20c-3c-1)
# ===========================================================================

print("\n" + "=" * 78)
print("6. the dashboard: mutable state, the cache contract, and import purity")
print("=" * 78)

# WHY THE DASHBOARD NEEDS CHECKS OF ITS OWN.
#
# STREAMLIT RE-RUNS THE WHOLE SCRIPT ON EVERY INTERACTION. Before this pass,
# "21- Streamlit Dashboard.py" exec'd Files 01 and 02 and chained File 03 at the
# top, and exec_chain caches NOTHING -- it opens and exec()s every file on every
# call. So every button, every filter and every tab click re-read and
# re-executed all three, and because "03- Config.py" calls the client factories
# at shim load, every one of them also constructed an OpenAI client and a Qdrant
# client for a dashboard that uses neither.
#
# Moving the code into modules means each body runs ONCE per process. That is a
# large improvement and it is also a semantic change: module-level mutable state
# now persists across reruns instead of being rebuilt from scratch each time.
# The checks below are the ones that make that change safe rather than merely
# hoped for.

_DASH_DIR = os.path.join(_PKG_DIR, "dashboard")
_TIERS_PY = os.path.join(_DASH_DIR, "tiers.py")
_DATA_PY = os.path.join(_DASH_DIR, "data.py")


# --- 6a. the tier vocabulary is never mutated ------------------------------
#
# MATCH_TIERS (a list) and MATCH_TIER_COLORS (a dict) are the ONLY module-level
# mutable objects the dashboard defines or touches. Everything else it binds at
# module level is a str, and everything it reads out of the package
# (Project_Name, MAX_TRIALS_FOR_EVALUATION, inferences_path) is immutable.
#
# Under the old bootstrap both were rebuilt on every rerun, so a mutation would
# have been erased before the next click and could not accumulate. As modules
# they live for the life of the process, and a mutation would leak into every
# subsequent rerun for every user of that server. Nothing mutates them today.
# This check is what makes that a property rather than an accident.

_MUTATORS = ("append", "extend", "insert", "remove", "pop", "clear", "update",
             "setdefault", "sort", "reverse", "popitem")


def _mutations_of(paths, targets):
    """Every write THROUGH one of `targets` in `paths`.

    Catches three shapes: a subscript store (``X[k] = v``), a mutating method
    call (``X.update(...)``), and a subscript delete. Aliases are followed one
    hop -- ``tier_colors = MATCH_TIER_COLORS`` appears in three tabs, and a
    write through the alias is a write through the object.
    """
    found = []
    for path in paths:
        tree = ast.parse(open(path, encoding="utf-8").read())
        watched = set(targets)
        # one alias hop: any local bound DIRECTLY to a watched name
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Name)
                    and node.value.id in targets):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        watched.add(tgt.id)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.Delete)):
                tgts = (node.targets if isinstance(node, (ast.Assign, ast.Delete))
                        else [node.target])
                for tgt in tgts:
                    if (isinstance(tgt, ast.Subscript)
                            and isinstance(tgt.value, ast.Name)
                            and tgt.value.id in watched):
                        found.append(f"{os.path.basename(path)}:{node.lineno} "
                                     f"{ast.unparse(node)[:60]}")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _MUTATORS
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in watched):
                found.append(f"{os.path.basename(path)}:{node.lineno} "
                             f"{ast.unparse(node)[:60]}")
    return sorted(found)


_DASH_FILES = sorted(
    os.path.join(root, name)
    for root, _dirs, files in os.walk(_DASH_DIR)
    for name in files
    if name.endswith(".py") and "__pycache__" not in root
)
check("the dashboard has the fifteen modules this pass created "
      "(non-degeneracy: a scan over an empty file list proves nothing)",
      len(_DASH_FILES), 15)

_TIER_NAMES = ("MATCH_TIERS", "MATCH_TIER_COLORS")
check("nothing in the dashboard mutates MATCH_TIERS or MATCH_TIER_COLORS, "
      "directly or through an alias",
      _mutations_of(_DASH_FILES, _TIER_NAMES), [])

# NON-DEGENERATE, and this is the part that matters: "[] mutations" is also what
# a scanner that looks at nothing returns. A COPY of the tree gets one write of
# each shape planted into it, and the same scanner must find all three. Mutating
# a copy rather than the shipped file is the rule CLAUDE.md sets out, and it
# means a crash here cannot leave the package edited.
_MUT_DIR = tempfile.mkdtemp(prefix="oncotriage-tiermut-")
try:
    _mut_copy = os.path.join(_MUT_DIR, "dashboard")
    shutil.copytree(_DASH_DIR, _mut_copy,
                    ignore=shutil.ignore_patterns("__pycache__"))
    _planted = os.path.join(_mut_copy, "tiers.py")
    _src = open(_planted, encoding="utf-8").read()
    _src += (
        "\n\ndef _planted_defect():\n"
        "    MATCH_TIERS.append('Sixth Tier')\n"          # mutating method
        "    MATCH_TIER_COLORS['Full Match'] = '#000000'\n"  # subscript store
        "    alias = MATCH_TIER_COLORS\n"
        "    alias.update({'No Match': '#ffffff'})\n"     # write through alias
    )
    open(_planted, "w", encoding="utf-8").write(_src)
    _caught = _mutations_of([_planted], _TIER_NAMES)
    check("...and the scanner CATCHES a planted mutation of each of the three "
          "shapes it claims to cover (method, subscript, alias)",
          len(_caught), 3)
finally:
    shutil.rmtree(_MUT_DIR, ignore_errors=True)

check("the tiers module still holds the tier vocabulary the tabs import "
      "(non-degeneracy for the scan above)",
      all(n in open(_TIERS_PY, encoding="utf-8").read()
          for n in _TIER_NAMES + ("classify_trial_score", "enrich_match_tiers")),
      True)


# --- 6b. the @st.cache_data contract ---------------------------------------
#
# @st.cache_data used to be APPLIED AFRESH on every rerun, because the whole
# script re-executed; it is now applied once, at import. The 60-second TTL is
# unchanged by that, and the reason is a fact about streamlit rather than about
# this project: a cached function's identity in the cache is
#
#     md5(func.__module__, func.__qualname__, inspect.getsource(func))
#
# -- streamlit/runtime/caching/cache_utils.py:_make_function_key -- and NOT the
# function OBJECT. So the cache already survived reruns before this pass (had it
# keyed on identity, ttl=60 would never have had anything to expire), and moving
# the loaders into a module changes only the __module__ component: a different
# key, still stable, still 60 seconds, with one cold miss on the first launch.
#
# This is asserted against the INSTALLED streamlit rather than trusted, because
# the whole argument for "the TTL still behaves as it did" rests on it. If a
# future streamlit keys on identity, the dashboard silently stops caching --
# three SQLite reads of the full inferences table on every widget interaction --
# and nothing else in this repository would notice.
_CACHE_KEY_PROBE = r"""
import inspect, json
from streamlit.runtime.caching import cache_utils
src = inspect.getsource(cache_utils._make_function_key)
print(json.dumps({
    "keys_on_module":   "__module__" in src,
    "keys_on_qualname": "__qualname__" in src,
    "keys_on_source":   "getsource" in src,
    "keys_on_id":       "id(func)" in src,
}))
"""
_rc, _out, _err = _run(_CACHE_KEY_PROBE, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("the streamlit cache-key probe ran", _rc, 0)
if _rc == 0:
    check("streamlit keys a cached function on (module, qualname, source) and "
          "NOT on the function object's identity, which is what makes the "
          "60s TTL survive the move into a module",
          _last_json(_out),
          {"keys_on_module": True, "keys_on_qualname": True,
           "keys_on_source": True, "keys_on_id": False})
else:
    fail("streamlit cache-key probe",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-3:]}")

# The three loaders must still CARRY the decorator, with the same TTL. An
# equivalence proof over ast.unparse would catch a dropped decorator on a
# function it compares -- and that is exactly how the decorator loss on four
# @st.fragment tabs was found while this pass was being written -- but this
# states the TTL itself, which no diff against the original would flag if
# someone later "tuned" it.
_data_tree = ast.parse(open(_DATA_PY, encoding="utf-8").read())
_decorated = {
    node.name: [ast.unparse(d) for d in node.decorator_list]
    for node in _data_tree.body if isinstance(node, ast.FunctionDef)
}
check("all three loaders carry @st.cache_data(ttl=60), unchanged",
      _decorated,
      {"load_inferences_data": ["st.cache_data(ttl=60)"],
       "load_trial_matches_data": ["st.cache_data(ttl=60)"],
       "load_drift_metrics_data": ["st.cache_data(ttl=60)"]})

# THE REFRESH BUTTON STILL REACHES THE LOADERS, WHICH NOW LIVE IN ANOTHER
# MODULE. st.cache_data.clear() is a CACHE-wide clear, not a per-function one --
# `render_sidebar` is in oncotriage.dashboard.sidebar and the three loaders are
# in oncotriage.dashboard.data, and before this pass they shared one namespace.
_sidebar_src = open(os.path.join(_DASH_DIR, "sidebar.py"), encoding="utf-8").read()
check("the sidebar's Refresh button still calls the cache-wide clear",
      "st.cache_data.clear()" in _sidebar_src and "st.rerun()" in _sidebar_src,
      True)
_CLEAR_PROBE = r"""
import json, streamlit as st
print(json.dumps({
    # A bound method on the cache object, not a per-function attribute: it
    # empties every @st.cache_data entry in the process whatever module
    # defined the function.
    "clear_is_callable": callable(st.cache_data.clear),
    "per_function_clear_also_exists": callable(
        getattr(st.cache_data, "clear", None)),
}))
"""
_rc, _out, _err = _run(_CLEAR_PROBE, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
if _rc == 0:
    check("st.cache_data.clear() is a cache-wide clear, so it reaches loaders "
          "defined in a different module than the button",
          (_last_json(_out) or {}).get("clear_is_callable"), True)
else:
    fail("cache clear probe", f"exit {_rc}")


# --- 6c. importing the dashboard touches nothing ---------------------------
#
# SEPARATE FROM SECTION 2, AND DELIBERATELY SO. Section 2 asserts that no
# model-bearing library arrives, and STREAMLIT IS ON THAT LIST -- it is there to
# say that importing the agent does not drag the dashboard in. The dashboard's
# own modules import streamlit at module scope because every render function
# needs it, so folding them into section 2 would have meant deleting streamlit
# from that list and weakening a claim that is worth keeping. They get their own
# trap run instead, with streamlit and plotly pre-imported for the same reason
# section 2 pre-imports matplotlib and seaborn for oncotriage.fhir.explore, and
# with torch / transformers / icd10 still forbidden.
_DASHBOARD_PURITY = r"""
import builtins, io, json, socket, sqlite3, sys

import numpy, pandas                                                      # noqa: F401
import openai, qdrant_client, dotenv, httpx, tenacity                     # noqa: F401
# Same guard, same reason, as section 2's pre-import block -- see the long note
# there. caffeine is a macOS-only dependency; where it is absent there is no
# import for this probe to arrange around, and the parent records a SKIP.
try:
    import caffeine                                                       # noqa: F401
    _caffeine_preimported = True
except Exception:                                                         # noqa: BLE001
    _caffeine_preimported = False
# The dashboard's module-scope third-party dependencies. streamlit reads its
# config file and plotly loads its package data at import; neither is this
# package's doing, which is the same allowance section 2 makes for matplotlib.
import streamlit, plotly.express, plotly.graph_objects, plotly.subplots    # noqa: F401


class Blocked(RuntimeError):
    pass


_real_socket = socket.socket


class BlockedSocket(_real_socket):
    def __init__(self, *args, **kwargs):
        raise Blocked("socket.socket() was constructed")


def _blocked(*args, **kwargs):
    raise Blocked("a blocked call was made")


socket.socket = BlockedSocket
socket.create_connection = _blocked
sqlite3.connect = _blocked
builtins.open = _blocked
io.open = _blocked

import oncotriage.paths as _p
import oncotriage.dashboard
import oncotriage.dashboard.data
import oncotriage.dashboard.sidebar
import oncotriage.dashboard.tiers
import oncotriage.dashboard.app
import oncotriage.dashboard.tabs
import oncotriage.dashboard.tabs.overview
import oncotriage.dashboard.tabs.performance
import oncotriage.dashboard.tabs.cost_tokens
import oncotriage.dashboard.tabs.demographics
import oncotriage.dashboard.tabs.patient_explorer
import oncotriage.dashboard.tabs.match_quality
import oncotriage.dashboard.tabs.trial_explorer
import oncotriage.dashboard.tabs.drift
import oncotriage.dashboard.tabs.reproducibility

heavy = [m for m in ("torch", "transformers", "sentence_transformers", "icd10")
         if m in sys.modules]

armed = {}
for _name, _fn, _args in (("socket", socket.socket, (socket.AF_INET, socket.SOCK_STREAM)),
                          ("sqlite3", sqlite3.connect, (":memory:",)),
                          ("open", builtins.open, ("/oncotriage-trap-probe",)),
                          ("io.open", io.open, ("/oncotriage-trap-probe",))):
    try:
        _fn(*_args)
        armed[_name] = False
    except Blocked:
        armed[_name] = True

print(json.dumps({"heavy": heavy, "armed": armed,
                  "caffeine_preimported": _caffeine_preimported,
                  "resolved": sorted(_p._RESOLVED)}))
"""

_rc, _out, _err = _run(_DASHBOARD_PURITY, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("all fifteen dashboard modules import with open, io.open, socket.socket, "
      "socket.create_connection and sqlite3.connect patched to raise", _rc, 0)
if _rc != 0:
    fail("dashboard import purity",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    check("every trap was ARMED after the dashboard imports",
          _payload.get("armed"),
          {"socket": True, "sqlite3": True, "open": True, "io.open": True})
    check("importing the dashboard loads no model-bearing library",
          _payload.get("heavy"), [])
    # THE ONE THAT MATTERS FOR data.py. Reading inferences_path at module scope
    # would resolve the sibling data tree at import; reading it inside the
    # function body resolves it on first call.
    check("importing the dashboard resolves NO path -- data.py reads "
          "paths.inferences_path inside the function body, not at module scope",
          _payload.get("resolved"), [])
    # Same skip, same reason, as section 2's. See the note there.
    if not _payload.get("caffeine_preimported"):
        skip("oncotriage.utils' caffeine import stays inside the pre-import "
             "window (section 6c, the dashboard probe)",
             "caffeine is not installed on this platform -- pyproject declares "
             "it `sys_platform == \"darwin\"`. Run this file on macOS to cover "
             "it.")


# --- 6d. the three loaders read the path lazily and their SQL is unchanged --
#
# The path is the ONE thing this pass was allowed to change in the loaders, and
# the SQL is the thing it was specifically not allowed to change. Both are
# stated here so a later edit that "tidies" either one goes red.
# READ THE AST, NOT THE TEXT. data.py's docstring EXPLAINS the rule by quoting
# the very line it must not contain ("from oncotriage.paths import
# inferences_path"), so a substring scan reports the module as violating the
# rule its own documentation is stating. That is not a hypothetical: the first
# version of this check was a substring scan and it went red on a correct
# module. The same trap caught check 6e below.
_data_imports = [
    (node.module, sorted(a.name for a in node.names))
    for node in _data_tree.body if isinstance(node, ast.ImportFrom)
]
check("data.py does NOT import inferences_path by name (that would be an "
      "attribute read, and would resolve the tree at import)",
      [m for m, names in _data_imports
       if m == "oncotriage.paths" or "inferences_path" in names],
      [])
check("...it imports the paths MODULE instead",
      ("oncotriage", ["paths"]) in _data_imports, True)
_data_src = open(_DATA_PY, encoding="utf-8").read()
check("...and all three loaders read the attribute at call time",
      _data_src.count("sqlite3.connect(paths.inferences_path)"), 3)

_SQL = sorted(
    node.args[0].value
    for node in ast.walk(_data_tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and node.func.attr == "read_sql_query"
    and node.args and isinstance(node.args[0], ast.Constant)
)
check("the three loaders' SQL is byte-for-byte what File 21 had",
      _SQL,
      ["SELECT * FROM drift_metrics ORDER BY timestamp DESC",
       "SELECT * FROM inferences",
       "SELECT * FROM trial_matches"])


# --- 6e. File 21 is a thin entry point -------------------------------------
#
# Nothing in the repository chains it or reads a name out of it: every top-level
# name it bound was grepped against every .py, .md, .toml and .yml in the tree
# and every hit is inside File 21 itself, prose in a .md, or the `streamlit run`
# command in docker-compose.yml. So it keeps NO re-export shim -- unlike File 05
# (File 34 chains it), File 13 and File 20 (File 41 chains it).
_F21 = open(os.path.join(_code_dir, "21- Streamlit Dashboard.py"),
            encoding="utf-8").read()
_f21_tree = ast.parse(_F21)
# BY AST, for the same reason as check 6d: File 21's docstring explains at
# length that the exec bootstrap is gone and why, so it necessarily contains the
# words "exec()" and "exec_chain". A substring scan calls that a violation. The
# question is whether the file CALLS either one, which only the AST answers.
_f21_calls = sorted({
    node.func.id for node in ast.walk(_f21_tree)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    and node.func.id in ("exec", "exec_chain")
})
check("File 21 calls neither exec() nor exec_chain()", _f21_calls, [])
# ...and reads neither name at all, which also rules out `f = exec` or passing
# exec_chain somewhere. Names, not text.
_f21_names = sorted({
    node.id for node in ast.walk(_f21_tree)
    if isinstance(node, ast.Name) and node.id in ("exec", "exec_chain")
})
check("...and does not reference either name in any other position",
      _f21_names, [])
check("File 21 binds no function of its own",
      sorted(n.name for n in _f21_tree.body if isinstance(n, ast.FunctionDef)), [])
_f21_imports = sorted(
    f"{node.module}.{a.name}" for node in ast.walk(_f21_tree)
    if isinstance(node, ast.ImportFrom) for a in node.names
)
check("File 21 imports main from the package",
      "oncotriage.dashboard.app.main" in _f21_imports, True)
check("...and guards the call, so reading the file still does nothing",
      any(isinstance(n, ast.If)
          and "__name__" in ast.unparse(n.test)
          and "__main__" in ast.unparse(n.test)
          for n in _f21_tree.body), True)
# docker-compose must still launch the dashboard with `streamlit run` on File
# 21. The file being a valid script under that invocation is what makes the
# container keep working.
#
# THIS USED TO BE A SUBSTRING MATCH and item 21 broke it without breaking the
# property:
#
#     check(..., 'streamlit run "21- Streamlit Dashboard.py"' in _COMPOSE, True)
#
# Item 21 changed the command from a shell string, `bash -c 'exec streamlit run
# "21- ..."'`, to an argument VECTOR. The vector needs no shell and no quoting —
# which is the whole reason the quotes were there — so the literal substring is
# gone while the command is the same command. The check failed; nothing was
# wrong.
#
# That is the third instance in this project of the failure mode CLAUDE.md
# already records twice ("a substring is not a definition"): File 47's own BM25
# construction-site check, which `fastembed.SparseTextEmbedding(...)` evaded,
# and File 49's revision selector, which matched its own deletion comment. So
# this asks the actual question — parse the compose file, take the streamlit
# service's command, reduce it to tokens whichever form it is written in, and
# require `streamlit`, `run`, <script> to appear as three consecutive tokens.
# The script name is compared EXACTLY; a typo in it still fails.
_COMPOSE = open(os.path.join(_code_dir, "docker-compose.yml"), encoding="utf-8").read()


def _compose_command_tokens(compose_text, service):
    """The service's command as a token list, for either compose command form.

    Handles the three shapes that mean the same thing here:
      * a string            -> shlex
      * a list of arguments -> already tokens
      * ["bash", "-c", "..."] (or sh) -> shlex of the script, since that is
        where the real command lives. Without this the wrapper would hide it.
    """
    import shlex

    import yaml

    command = yaml.safe_load(compose_text)["services"][service].get("command")
    if command is None:
        return []
    if isinstance(command, str):
        return shlex.split(command)
    tokens = list(command)
    if len(tokens) >= 3 and os.path.basename(tokens[0]) in ("bash", "sh") \
            and tokens[1] in ("-c", "-lc"):
        return shlex.split(tokens[2])
    return tokens


def _launches_dashboard(tokens, script="21- Streamlit Dashboard.py"):
    want = ["streamlit", "run", script]
    return any(tokens[i:i + 3] == want for i in range(len(tokens)))


_STREAMLIT_TOKENS = _compose_command_tokens(_COMPOSE, "streamlit")
check("docker-compose still launches the dashboard by the same command",
      _launches_dashboard(_STREAMLIT_TOKENS), True)
# NON-DEGENERATE: an empty or unparsed command would satisfy nothing above but
# would also make the check meaningless if the helper silently returned [].
check("...and the command was actually parsed (not an empty token list)",
      len(_STREAMLIT_TOKENS) >= 3, True)
# NEGATIVE CONTROLS. Each is the real check run against a doctored compose text,
# and each must FAIL. Without these, "does the command launch the dashboard"
# would be indistinguishable from "does the helper return something truthy".
check("...control: a compose file running a DIFFERENT script does not pass",
      _launches_dashboard(_compose_command_tokens(
          _COMPOSE.replace("21- Streamlit Dashboard.py",
                           "21- Streamlit Dashboard BACKUP.py"), "streamlit")),
      False)
check("...control: dropping `run` from the command does not pass",
      _launches_dashboard([t for t in _STREAMLIT_TOKENS if t != "run"]), False)
check("...control: the shell-string form is still recognised (it is the form "
      "this file shipped with before item 21)",
      _launches_dashboard(_compose_command_tokens(
          'services: {streamlit: {command: '
          '[bash, -c, \'exec streamlit run "21- Streamlit Dashboard.py" '
          '--server.port=8501\']}}', "streamlit")),
      True)


# --- 6f. every name File 21 used to bind still exists ----------------------
#
# Pinned at the commit before this pass, extracted by exec'ing the original into
# a throwaway namespace and subtracting the base chain (01 -> 02 -> 03) -- the
# same method the other inventories in this file use. Written down rather than
# recomputed from HEAD, because the point is to pin what the surface WAS.
#
# The three bootstrap leftovers -- _bootstrap, _code_dir, _fh -- are deliberately
# absent: they were exec-chain scaffolding, they are private, and nothing read
# them. Everything else must still be reachable.
#
# ONE NAME WAS REMOVED FROM THIS PIN (pass 20f-3), AND REMOVING A NAME FROM A
# PIN IS A CHECK THAT STOPS RUNNING, so it is argued here rather than in a
# commit message -- the same discipline pass 20e applied to the four inventories
# it retired.
#
# The name is `TRIAL_STATUS_FULL`. What this pin asserts is "every name File 21
# bound is still REACHABLE somewhere in the package", and its purpose is to
# catch a name LOST in the twelve-way split -- a real hazard, because the split
# was done by slicing definitions out of a 5,481-line file and a dropped slice
# is invisible. It is not, and was never, an assertion that File 21's surface
# may not shrink deliberately.
#
# So the question this pin can answer about TRIAL_STATUS_FULL is "did the split
# lose it", and the answer is on record: it did not, it was carried into
# oncotriage/dashboard/tiers.py, and it sat there for two passes with check 2h
# reporting it as never-read and an exemption arguing why it was kept. Pass
# 20f-3 deleted it on the merits (see that exemption's replacement text above),
# so this pin's subject is gone. Keeping the entry would fail the probe below
# CORRECTLY -- the name really is not exported any more -- and the failure would
# say "the split lost a name", which is false.
#
# THE PIN IS 21 NAMES NOW, NOT 22, and the two counts below move with it. The
# other 21 are untouched, which is the property that keeps this check able to
# fail: it is still the whole of File 21's surface minus one name whose deletion
# is written down in three places.
_PRE_3C_F21_NAMES = [
    "MATCH_TIERS", "MATCH_TIER_COLORS",
    "TRIAL_STATUS_PARTIAL", "TRIAL_STATUS_REJECTED",
    "TRIAL_STATUS_UNCONFIRMED",
    "classify_trial_score", "enrich_match_tiers",
    "load_drift_metrics_data", "load_inferences_data", "load_trial_matches_data",
    "main",
    "render_cost_tokens_tab", "render_drift_detection_tab",
    "render_match_quality_tab", "render_overview_tab",
    "render_patient_demographics_tab", "render_patient_explorer_tab",
    "render_performance_tab", "render_reproducibility_tab",
    "render_sidebar", "render_trial_explorer_tab",
]
check("the recorded File 21 name list is the size it was extracted at, "
      "less the one name pass 20f-3 deleted with an argument",
      len(_PRE_3C_F21_NAMES), 21)

_F21_SURFACE_PROBE = r"""
import importlib, json, sys
wanted = json.loads(sys.argv[1])
missing = []
for name, module_name in wanted.items():
    module = importlib.import_module(module_name)
    if not hasattr(module, name):
        missing.append(module_name + "." + name)
print(json.dumps({"missing": missing, "checked": len(wanted)}))
"""
_F21_HOMES = {
    "MATCH_TIERS": "oncotriage.dashboard.tiers",
    "MATCH_TIER_COLORS": "oncotriage.dashboard.tiers",
    "TRIAL_STATUS_PARTIAL": "oncotriage.dashboard.tiers",
    "TRIAL_STATUS_REJECTED": "oncotriage.dashboard.tiers",
    "TRIAL_STATUS_UNCONFIRMED": "oncotriage.dashboard.tiers",
    "classify_trial_score": "oncotriage.dashboard.tiers",
    "enrich_match_tiers": "oncotriage.dashboard.tiers",
    "load_drift_metrics_data": "oncotriage.dashboard.data",
    "load_inferences_data": "oncotriage.dashboard.data",
    "load_trial_matches_data": "oncotriage.dashboard.data",
    "main": "oncotriage.dashboard.app",
    "render_sidebar": "oncotriage.dashboard.sidebar",
    "render_overview_tab": "oncotriage.dashboard.tabs.overview",
    "render_performance_tab": "oncotriage.dashboard.tabs.performance",
    "render_cost_tokens_tab": "oncotriage.dashboard.tabs.cost_tokens",
    "render_patient_demographics_tab": "oncotriage.dashboard.tabs.demographics",
    "render_patient_explorer_tab": "oncotriage.dashboard.tabs.patient_explorer",
    "render_match_quality_tab": "oncotriage.dashboard.tabs.match_quality",
    "render_trial_explorer_tab": "oncotriage.dashboard.tabs.trial_explorer",
    "render_drift_detection_tab": "oncotriage.dashboard.tabs.drift",
    "render_reproducibility_tab": "oncotriage.dashboard.tabs.reproducibility",
}
check("...and every one of them has a recorded home in the package",
      sorted(_F21_HOMES), sorted(_PRE_3C_F21_NAMES))

_rc, _out, _err = _run(
    _F21_SURFACE_PROBE.replace('sys.argv[1]', repr(json.dumps(_F21_HOMES))),
    cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("the File 21 surface probe ran", _rc, 0)
if _rc == 0:
    _payload = _last_json(_out) or {}
    check("every name File 21 bound before this pass is still exported by the "
          "module that now owns it", _payload.get("missing"), [])
    check("...over all 21 names (a probe over an empty set proves nothing)",
          _payload.get("checked"), 21)
else:
    fail("File 21 surface probe",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")


#------------------------------------------------------------------------------


shutil.rmtree(_ELSEWHERE, ignore_errors=True)


#------------------------------------------------------------------------------


# ===========================================================================
# REPORT
# ===========================================================================

print()
print("=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")
# ALWAYS PRINTED, even at zero. A skip count that only appears when non-zero
# makes its absence ambiguous -- the reader cannot tell a run with nothing
# skipped from a run by a version of this file that had no skip mechanism.
print(f"Skipped: {_RESULTS['skipped']}   (a skip is NOT a pass and is not "
      f"counted as one)")

if _SKIPS:
    print("\nSkipped — coverage NOT exercised on this platform:")
    for _s in _SKIPS:
        print(f"  - {_s}")

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
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
