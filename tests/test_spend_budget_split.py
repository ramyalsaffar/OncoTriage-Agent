# The Budget-Split Test
#######################

"""The judge binds its own budget, and nothing reads the other's cap.

WHAT THIS FILE IS FOR
---------------------
``tests/test_spend_gate.py`` proves the gate stops a run. ``tests/
test_spend_coverage.py`` proves every billed path reaches a gate. Neither can
see WHICH BUDGET a path is stopped by, because until the operator ruling this
pass implements there was only one -- so a defect that bound the judge to the
campaign's cap, or the campaign to the judge's, would have passed both files.

The ruling is two values: the campaign stays $300 and the rater binds its own
$50. The SHAPE is a budget table (``spend.SPEND_BUDGETS``,
``spend.BUDGET_FOR_SOURCE``), and the thing that can go silently wrong with a
table is a cross-wire: one path reading another budget's cap, or one budget
measured against another's money. This file is about exactly that.

  1. THE TABLES ARE TOTAL AND CLOSED, and a source with no budget is refused at
     import rather than defaulted.
  2. THE VALUES ARE THE RULING, and each cap's unset semantics and validation
     are the SAME as the one the single cap established -- driven on BOTH
     constants, because a second cap that read a negative as "unlimited" would
     be the defect the first one refuses.
  3. THE WIRING IS PINNED BOTH WAYS. Structurally, by AST over the shipped
     modules: the rater's gate reads the rater cap and Stage 5's reads the
     campaign's, and neither names the other's constant. Behaviourally, by
     driving each to its own cap with the other's budget untouched.
  4. THE CROSS-WIRE IS PLANTED, in an in-memory copy of ``oncotriage/spend.py``
     that maps ``rater_batch`` onto the campaign budget -- the exact defect a
     careless split ships -- and a check here has to catch it. The CLEAN
     CONTROL runs first: without it a probe that disagreed with everything
     would report every plant as caught while measuring nothing.
  5. THE SEED IS ATTRIBUTED. A resumed judge session's baseline lands in the
     rater budget and in no other, which is the half of the split a cap-only
     change would have left broken.

WHAT IT COSTS TO RUN: NOTHING. No network, no keys, NO SPEND -- no provider
client is built and no request of any kind is issued. NO MODEL LOAD
(``ONCOTRIAGE_DEFER_LOCAL_MODELS`` is set above the imports and section 7
asserts ``torch`` and ``transformers`` never entered ``sys.modules``). No live
Qdrant, no corpus, no database, no git history, no live server.

IT WRITES NOTHING ANYWHERE, not even a temp directory. NOT in the collision
matrix -- but it DOES read ``oncotriage/config.py``, which
``tests/test_config_snapshot_date_rot.py`` rewrites in place, so all four files
it reads are sha256-compared at the end and an interleaved serial run is
visible rather than silent.

IT DOES EXEC: two in-memory copies of ``oncotriage/spend.py``, one plant each,
argued at ``tests/test_package_invariants.py``'s ``_EXEC_ALLOWLIST``. ``git
show`` can supply neither -- no revision of this project has ever had a budget
table, so there is no prior version carrying the defect to compare against.
"""

import ast
import hashlib
import os
import sys
import types

# ABOVE THE IMPORTS, for oncotriage/fixtures/replay.py's reason.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_CODE = os.path.dirname(_HERE)
if _CODE not in sys.path:
    sys.path.insert(0, _CODE)

import contextlib                                               # noqa: E402

from oncotriage import config                                   # noqa: E402
from oncotriage import spend                                    # noqa: E402
from oncotriage.agent import evaluation as _evaluation          # noqa: E402
from oncotriage.evaluation import rater as _rater               # noqa: E402

_READ_FILES = [os.path.abspath(m.__file__)
               for m in (spend, _evaluation, _rater, config)]
_BASELINE_HASHES = {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
                    for p in _READ_FILES}

_START_CONFIG = (config.SPEND_CAP_USD, config.RATER_SPEND_CAP_USD,
                 config.SERVING_SPEND_CAP_USD, config.SPEND_CAP_ENFORCED)

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []
_CONTROLS_RUN = [0]


def check(label, actual, expected):
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append((label, expected, actual))
        print(f"  FAIL  {label}\n          expected: {expected!r}"
              f"\n          actual:   {actual!r}")


def section(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


class _Absent:
    """A value that equals nothing a check expects, and NEVER raises.

    THE ABORT CLASS THIS PROJECT HAS SHIPPED SIXTEEN TIMES. A bare
    ``mapping[key]`` inside a ``check(...)`` argument list raises while the
    argument is being EVALUATED -- on precisely the defect the check exists to
    catch -- so the run prints one traceback where it owed a summary and every
    result below it. Every raise-capable read in this file goes through
    ``at()``, ``drive()`` or ``raised()``.
    """

    def __init__(self, why):
        self.why = why

    def __repr__(self):
        return f"<absent: {self.why}>"

    def __eq__(self, other):
        return isinstance(other, _Absent) and other.why == self.why

    def __bool__(self):
        return False

    def __len__(self):
        return 0


def at(mapping, key):
    try:
        return mapping[key]
    except Exception as exc:                                    # noqa: BLE001
        return _Absent(f"{key}: {type(exc).__name__}")


def drive(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except BaseException as exc:                                # noqa: BLE001
        return _Absent(f"{type(exc).__name__}: {exc}")


def raised(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return None
    except BaseException as exc:                                # noqa: BLE001
        return type(exc).__name__


@contextlib.contextmanager
def budgets(*, cap=None, rater_cap=None, enforced=True,
            policy=spend.SPEND_POLICY_CAMPAIGN):
    """Both caps, the policy and a clean ledger, restored on the way out.

    RESTORED FROM VALUES CAPTURED HERE rather than from literals, so a
    legitimate change to a shipped default costs this file nothing.
    """
    saved = (config.SPEND_CAP_USD, config.RATER_SPEND_CAP_USD,
             config.SPEND_CAP_ENFORCED)
    prev = spend.policy()
    spend.SPEND_LEDGER.reset()
    spend.SPEND_STOP.reset()
    spend.SPEND_GATE_SKIPS.clear()
    spend.SPEND_LEDGER_FAULTS.clear()
    if cap is not None:
        config.SPEND_CAP_USD = cap
    if rater_cap is not None:
        config.RATER_SPEND_CAP_USD = rater_cap
    config.SPEND_CAP_ENFORCED = enforced
    spend.set_policy(policy, "test")
    try:
        yield spend.SPEND_LEDGER
    finally:
        spend.set_policy(prev, "restore")
        spend.SPEND_LEDGER.reset()
        spend.SPEND_STOP.reset()
        spend.SPEND_GATE_SKIPS.clear()
        spend.SPEND_LEDGER_FAULTS.clear()
        (config.SPEND_CAP_USD, config.RATER_SPEND_CAP_USD,
         config.SPEND_CAP_ENFORCED) = saved


_SPEND_SRC = open(_READ_FILES[0], encoding="utf-8").read()
_SPEND_TREE = ast.parse(_SPEND_SRC)


def _fn(tree, name):
    """A top-level (or one-deep) def by name, or None."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    return None


def _code_names(tree, name):
    """Every name a function\'s CODE mentions, DOCSTRING EXCLUDED. A set.

    THE DOCSTRING HAS TO GO, and leaving it in is a defect this project has met
    three times: ``rater_spend_cap``\'s own prose argues about
    ``config.SPEND_CAP_USD``, so a check asking whether the function "names"
    the campaign constant would be satisfied by the paragraph explaining why it
    does not read it. A file that argues about its own settings cannot be
    grepped for them.
    """
    node = _fn(tree, name)
    if node is None:
        return set()
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) \
            and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    out = set()
    for stmt in body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Attribute):
                out.add(ast.unparse(sub))
            elif isinstance(sub, ast.Name):
                out.add(sub.id)
            elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                out.add(sub.value)
    return out


def first(lines, prefix):
    """The first line with this prefix, or a named absence. NEVER raises.

    A bare ``[ln for ln in lines if ...][0]`` raises ``IndexError`` on exactly
    the defect the check exists to catch -- a report that stopped printing a
    budget -- so the run prints one traceback where it owed a summary. MEASURED
    rather than reasoned about: the revert that makes ``report_lines()`` print
    one budget instead of every one ABORTED this file before this helper
    existed.
    """
    for ln in lines:
        if ln.startswith(prefix):
            return ln
    return _Absent(f"no line starting {prefix!r}")


def _plant(source, old, new, occurrences=1):
    """A COPY of oncotriage/spend.py with one substitution, exec'd.

    THE PLANT ASSERTS ITS OWN OCCURRENCE COUNT, so a plant that matched nothing
    is a named failure rather than a working check reported as broken -- the
    lesson pass 20f-1 wrote down and this project has had to re-learn.
    """
    if source.count(old) != occurrences:
        return _Absent(f"PLANT-FAILED: {source.count(old)} of "
                       f"{occurrences} occurrence(s)")
    mod = types.ModuleType("oncotriage._spend_plant")
    mod.__file__ = _READ_FILES[0]
    mod.__name__ = "oncotriage.spend"
    try:
        exec(compile(source.replace(old, new, occurrences),
                     _READ_FILES[0], "exec"), mod.__dict__)
    except BaseException as exc:                                # noqa: BLE001
        return _Absent(f"{type(exc).__name__}: {exc}")
    return mod


# ===========================================================================
# SECTION 1 -- THE TABLES ARE TOTAL, CLOSED AND DERIVED
# ===========================================================================

section("SECTION 1 -- two budgets, and every billed path is assigned to one")

check("1a  *** the ruled vocabulary: exactly two budgets, campaign and "
      "rater ***",
      spend.SPEND_BUDGETS, ("campaign", "rater"))
check("1b  every SPEND_SOURCES member is assigned a budget, and nothing else "
      "is",
      sorted(spend.BUDGET_FOR_SOURCE), sorted(spend.SPEND_SOURCES))
check("1c  *** the ruling in one line: the rater path is the ONLY member of "
      "the rater budget, and every other billed path keeps the campaign's ***",
      {b: sorted(srcs) for b, srcs in spend.BUDGET_SOURCES.items()},
      {"campaign": sorted(("stage5", "query_embedding", "ragas_judge",
                           "ragas_embedding")),
       "rater": ["rater_batch"]})
check("1c-i non-degeneracy: the two budgets govern DISJOINT, non-empty sets, "
      "so 1c is not one set compared with itself",
      (bool(spend.BUDGET_SOURCES["campaign"]),
       bool(spend.BUDGET_SOURCES["rater"]),
       set(spend.BUDGET_SOURCES["campaign"])
       & set(spend.BUDGET_SOURCES["rater"])),
      (True, True, set()))
check("1d  BUDGET_SOURCES is DERIVED from BUDGET_FOR_SOURCE rather than typed "
      "a second time, so the two cannot disagree about which budget measures a "
      "path",
      {b: tuple(s for s in spend.SPEND_SOURCES
                if spend.BUDGET_FOR_SOURCE[s] == b)
       for b in spend.SPEND_BUDGETS},
      spend.BUDGET_SOURCES)
check("1e  every SEED_SOURCES member except 'fresh' is assigned a budget",
      sorted(spend.BUDGET_FOR_SEED_SOURCE),
      sorted(set(spend.SEED_SOURCES) - {spend.SEED_SOURCE_NONE}))
check("1e-i ...and 'fresh' belongs to none, which is a VALUE and not an "
      "omission: it is a zero, and adding a zero to a budget is the one thing "
      "it may not change",
      spend.seed_budget(spend.LedgerSeed()), None)
check("1f  a rater state seed lands in the RATER budget and a campaign row "
      "seed in the CAMPAIGN's",
      (spend.seed_budget(spend.LedgerSeed(
          usd=1.0, source=spend.SEED_SOURCE_RATER_STATE)),
       spend.seed_budget(spend.LedgerSeed(
           usd=1.0, source=spend.SEED_SOURCE_CAMPAIGN))),
      ("rater", "campaign"))
check("1g  an unrecognised billed path is REFUSED by name rather than read as "
      "'the campaign' -- a path with no declared budget would spend one "
      "nobody gave it",
      raised(spend.budget_for, "some_new_vendor"),
      "SpendCapConfigurationError")
check("1h  every budget can name the constant an operator edits to move it, "
      "which is the one sentence in a refusal they act on",
      sorted(spend.BUDGET_CAP_CONSTANTS.items()),
      [("campaign", "config.SPEND_CAP_USD"),
       ("rater", "config.RATER_SPEND_CAP_USD")])


# ===========================================================================
# SECTION 2 -- THE TWO VALUES, AND THE SAME SEMANTICS FOR BOTH
# ===========================================================================

section("SECTION 2 -- the ruled values, and identical unset/validation rules")

check("2a  *** the campaign cap is the operator's ruling: 300 US dollars ***",
      _START_CONFIG[0], 300.00)
check("2b  *** and the rater's is 50 -- ITS OWN budget, not a share of the "
      "campaign's ***", _START_CONFIG[1], 50.00)
check("2b-i the two are different values, so every check below that "
      "distinguishes them is distinguishing something",
      _START_CONFIG[0] != _START_CONFIG[1], True)

# THE UNSET SEMANTICS AND THE VALIDATION, DRIVEN ON *BOTH* CONSTANTS. This is
# the requirement that the rules established for one cap hold for the other:
# a second cap that read a negative as "unlimited" would be exactly the defect
# the first one refuses, arriving through the new constant.
for _label, _attr, _reader, _banner, _absent_text in (
        ("campaign", "SPEND_CAP_USD", spend.spend_cap,
         spend.describe_cap, "NO SPEND CAP IS SET"),
        ("rater", "RATER_SPEND_CAP_USD", spend.rater_spend_cap,
         spend.describe_rater_cap, "NO RATER SPEND CAP IS SET")):
    _saved = getattr(config, _attr)
    try:
        setattr(config, _attr, None)
        check(f"2c  [{_label}] None means NO CAP rather than a cap of nothing",
              _reader(), None)
        check(f"2c-i [{_label}] ...and the banner SAYS SO, so the unbounded "
              f"state is not the quiet one",
              _absent_text in _banner(), True)
        setattr(config, _attr, 0.0)
        check(f"2d  [{_label}] zero IS a cap and is honoured -- a rehearsal "
              f"of the unbilled path, not an absence", _reader(), 0.0)
        setattr(config, _attr, -1.0)
        check(f"2e  [{_label}] a NEGATIVE cap is REFUSED by name, never read "
              f"as unlimited",
              raised(_reader), "SpendCapConfigurationError")
        check(f"2e-i [{_label}] ...and it reaches the operator through the "
              f"BANNER, before anything is spent",
              "REFUSING TO READ" in _banner(), True)
        setattr(config, _attr, "50")
        check(f"2e-ii [{_label}] ...and so is a value that is not a number",
              raised(_reader), "SpendCapConfigurationError")
        setattr(config, _attr, True)
        check(f"2e-iii [{_label}] ...bool included, on this project's "
              f"standing footing: a cap of True priced as one dollar is a "
              f"budget nobody set",
              raised(_reader), "SpendCapConfigurationError")
    finally:
        setattr(config, _attr, _saved)
check("2f  ...and both constants were restored",
      (config.SPEND_CAP_USD, config.RATER_SPEND_CAP_USD),
      (_START_CONFIG[0], _START_CONFIG[1]))

check("2g  the rater banner names the RATER cap and NOT the campaign's -- the "
      "shipped judge printed 'Cap $300.00 per campaign', a bound it does not "
      "run under, naming a constant that would not move its own limit",
      ("$50.00" in spend.describe_rater_cap(),
       "$300.00" in spend.describe_rater_cap(),
       "$300.00" in spend.describe_cap()),
      (True, False, True))
check("2g-i ...and the campaign banner does not name the rater's either, so "
      "the pair discriminates in both directions",
      "$50.00" in spend.describe_cap(), False)


# ===========================================================================
# SECTION 3 -- THE WIRING, PINNED STRUCTURALLY
# ===========================================================================

section("SECTION 3 -- each gate reads its own cap, by AST over the source")

_CAMPAIGN_READER = _code_names(_SPEND_TREE, "spend_cap")
_RATER_READER = _code_names(_SPEND_TREE, "rater_spend_cap")

check("3a  *** spend_cap() reads config.SPEND_CAP_USD and names the rater's "
      "constant NOWHERE in its code ***",
      ("config.SPEND_CAP_USD" in _CAMPAIGN_READER,
       any("RATER_SPEND_CAP_USD" in n for n in _CAMPAIGN_READER)),
      (True, False))
check("3b  *** rater_spend_cap() reads RATER_SPEND_CAP_USD and names the "
      "campaign's constant NOWHERE in its code ***",
      ("RATER_SPEND_CAP_USD" in _RATER_READER,
       "SPEND_CAP_USD" in _RATER_READER
       or "config.SPEND_CAP_USD" in _RATER_READER),
      (True, False))
check("3b-i non-degeneracy: both function bodies were FOUND and both name "
      "SOMETHING, so 3a and 3b are not two walks over nothing",
      (_fn(_SPEND_TREE, "spend_cap") is not None,
       _fn(_SPEND_TREE, "rater_spend_cap") is not None,
       len(_CAMPAIGN_READER) > 3, len(_RATER_READER) > 3),
      (True, True, True, True))
check("3b-ii non-degeneracy: the docstring really was excluded -- "
      "rater_spend_cap()'s PROSE names config.SPEND_CAP_USD, so a check that "
      "kept it would be satisfied by the paragraph arguing that it does not "
      "READ it",
      "SPEND_CAP_USD" in ast.unparse(_fn(_SPEND_TREE, "rater_spend_cap")),
      True)

# THE RATER MODULE NAMES ITS OWN CONSTANT AND NOT THE CAMPAIGN'S -- executable
# code only, because this module's PROSE argues about the campaign cap and a
# grep would be satisfied by the argument. The third time this project has met
# "a file that argues about its own settings cannot be grepped for them".
_RATER_TREE = ast.parse(open(_READ_FILES[2], encoding="utf-8").read())


def _executable_strings_and_names(tree):
    """Attribute paths AND string literals, EXCLUDING every docstring.

    THE STRING HALF IS LOAD-BEARING AND ITS ABSENCE WAS MEASURED. The rater's
    remedy sentence is an f-string -- ``"Raise config.RATER_SPEND_CAP_USD"`` --
    so a pin that walked only ``ast.Attribute`` reported a module that tells an
    operator to raise the WRONG constant as clean. The revert matrix found it;
    reading did not.

    THE DOCSTRING HALF IS EQUALLY LOAD-BEARING IN THE OTHER DIRECTION. This
    module's PROSE argues at length about ``config.SPEND_CAP_USD`` -- what it
    does and does not net against -- so a scan that kept docstrings would be
    satisfied by the argument and could never be satisfied by the code. Both
    FUNCTION docstrings and ATTRIBUTE docstrings (a bare string statement after
    an assignment, which is what ``STATE_SPEND_KEY`` carries) are excluded, by
    dropping every string that IS a standalone expression statement.
    """
    doc_ids = {id(n.value) for n in ast.walk(tree)
               if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
               and isinstance(n.value.value, str)}
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute):
            out.add(ast.unparse(n))
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and id(n) not in doc_ids:
            out.add(n.value)
    return out


_RATER_CODE_NAMES = _executable_strings_and_names(_RATER_TREE)
check("3c  *** the rater names config.RATER_SPEND_CAP_USD in its executable "
      "code ***",
      "config.RATER_SPEND_CAP_USD" in _RATER_CODE_NAMES, True)
check("3c-i *** ...and names config.SPEND_CAP_USD nowhere in its executable "
      "code -- attribute reads AND string literals: a judge refused with "
      "'raise config.SPEND_CAP_USD' sends an operator to a cap that had "
      "nothing to do with the stop ***",
      sorted(n for n in _RATER_CODE_NAMES if "config.SPEND_CAP_USD" in n), [])
check("3c-i-a non-degeneracy: the scan DOES see string literals, so 3c-i is "
      "not an attribute-only walk reporting a remedy sentence as clean",
      any("RATER_SPEND_CAP_USD" in n and "config." in n
          and n not in ("config.RATER_SPEND_CAP_USD",)
          for n in _RATER_CODE_NAMES), True)
check("3c-i-b non-degeneracy: the module's PROSE does name "
      "config.SPEND_CAP_USD, so the docstring exclusion is doing work rather "
      "than being decoration",
      "config.SPEND_CAP_USD" in open(_READ_FILES[2], encoding="utf-8").read(),
      True)
check("3c-ii non-degeneracy: the walk found attribute reads at all, so 3c-i "
      "is not an empty set reported as a clean one",
      len(_RATER_CODE_NAMES) > 50, True)
# EVERY BUDGET-SELECTING CALL IN THE RATER NAMES THE RATER, and this is the
# check the revert matrix demanded: `3c` says the module knows its constant and
# says nothing about WHICH source its preflight asks about. A preflight reading
# the CAMPAIGN remainder compares a judge session's estimate against a
# campaign's balance and warns, or fails to warn, about the wrong money -- and
# that revert was MISSED until this check existed.
_RATER_ASKS = []
for _n in ast.walk(_RATER_TREE):
    if not isinstance(_n, ast.Call) or not isinstance(_n.func, ast.Attribute):
        continue
    if "spend." not in ast.unparse(_n.func):
        continue
    if _n.func.attr not in ("remaining", "cap_exceeded", "active_spend",
                            "active_cap", "require_budget",
                            "seconds_until_under_cap"):
        continue
    _RATER_ASKS.append((_n.func.attr,
                        "SPEND_SOURCE_RATER" in ast.unparse(_n)))
check("3d  *** every budget-selecting call in the rater names "
      "SPEND_SOURCE_RATER -- its own budget, never a default and never the "
      "campaign's ***",
      sorted({fn for fn, ok in _RATER_ASKS if not ok}), [])
check("3d-0 non-degeneracy: there ARE such calls, so 3d is not an empty list "
      "reported as clean", len(_RATER_ASKS) >= 2, True)
check("3d-i ...and it prints describe_rater_cap(), not describe_cap()",
      ("spend.describe_rater_cap" in _RATER_CODE_NAMES,
       "spend.describe_cap" in _RATER_CODE_NAMES), (True, False))

# STAGE 5'S SIDE OF THE SAME PIN.
_EVAL_TREE = ast.parse(open(_READ_FILES[1], encoding="utf-8").read())
_EVAL_GATE = ast.unparse(_fn(_EVAL_TREE, "_spend_gate") or ast.Module(
    body=[], type_ignores=[]))
check("3e  *** Stage 5's gate asks about SPEND_SOURCE_STAGE5, so it is bound "
      "by the campaign budget explicitly rather than by a default ***",
      ("SPEND_SOURCE_STAGE5" in _EVAL_GATE,
       "SPEND_SOURCE_RATER" in _EVAL_GATE), (True, False))
check("3e-i non-degeneracy: the gate function was found",
      _fn(_EVAL_TREE, "_spend_gate") is not None, True)

# NO CALL SITE ANYWHERE MAY OMIT THE BUDGET SELECTOR. A missed one is a
# TypeError at run time -- which is how this pass found the one it missed --
# and this is the check that finds the next one without running a batch.
_SOURCE_TAKING = ("cap_exceeded", "remaining", "active_cap", "active_spend",
                  "seconds_until_under_cap", "budget_for")
_BARE = []
for _root, _dirs, _files in os.walk(os.path.join(_CODE, "oncotriage")):
    for _f in _files:
        if not _f.endswith(".py"):
            continue
        _path = os.path.join(_root, _f)
        for _n in ast.walk(ast.parse(open(_path, encoding="utf-8").read())):
            if not isinstance(_n, ast.Call):
                continue
            # SCOPED TO `spend.X(...)`, WHICH IS THE ONLY FORM THE PACKAGE
            # USES (no module imports these names bare -- asserted by 3f-ii).
            # Unscoped, `remaining()` is a plausible method name on somebody
            # else's object, and a scan that reported one would fail for a
            # reason that has nothing to do with a budget.
            if not isinstance(_n.func, ast.Attribute):
                continue
            _fname = _n.func.attr
            if "spend." not in ast.unparse(_n.func):
                continue
            if _fname in _SOURCE_TAKING and not _n.args and not _n.keywords:
                _BARE.append(f"{os.path.relpath(_path, _CODE)}:{_n.lineno}")
            # THE SELECTOR MAY BE POSITIONAL OR KEYWORD, and the first
            # version of this scan read only the keyword form -- so it reported
            # the one shipped call site that passes it positionally as a call
            # with no budget. A check that cannot see a legal form reports a
            # working call site as a defect, which is the same class of wrong
            # as missing a broken one.
            if _fname in ("poll", "trip") \
                    and isinstance(_n.func, ast.Attribute) \
                    and "SPEND_STOP" in ast.unparse(_n.func):
                _kw = {k.arg for k in _n.keywords}
                _need = 3 if _fname == "trip" else 2
                if "source" not in _kw and len(_n.args) < _need:
                    _BARE.append(
                        f"{os.path.relpath(_path, _CODE)}:{_n.lineno}")
check("3f  *** every budget-selecting call in the package names its source: a "
      "call that omitted it would be bound by whichever budget the default "
      "chose ***", sorted(_BARE), [])
# NON-DEGENERACY, IN THE ONLY FORM THAT MEANS ANYTHING: the scan is shown to
# FIND the shipped call sites it is scoped to, so an empty finding list is a
# clean tree rather than a walk that visited nothing.
_SEEN = 0
for _root, _dirs, _files in os.walk(os.path.join(_CODE, "oncotriage")):
    for _f in _files:
        if not _f.endswith(".py"):
            continue
        for _n in ast.walk(ast.parse(
                open(os.path.join(_root, _f), encoding="utf-8").read())):
            if isinstance(_n, ast.Call) and isinstance(_n.func, ast.Attribute) \
                    and "spend." in ast.unparse(_n.func) \
                    and (_n.func.attr in _SOURCE_TAKING
                         or (_n.func.attr in ("poll", "trip")
                             and "SPEND_STOP" in ast.unparse(_n.func))):
                _SEEN += 1
check("3f-i non-degeneracy: the scan FOUND the shipped budget-selecting call "
      "sites, so 3f's empty finding list is a clean tree and not a walk over "
      "nothing", _SEEN >= 12, True)
check("3f-ii ...and no package module imports these names bare, which is what "
      "makes the `spend.` scoping in 3f complete rather than convenient",
      sorted({n for n in _SOURCE_TAKING
              if any(f"import {n}" in open(os.path.join(r, f),
                                           encoding="utf-8").read()
                     for r, _d, fs in os.walk(os.path.join(_CODE,
                                                           "oncotriage"))
                     for f in fs if f.endswith(".py"))}), [])


# ===========================================================================
# SECTION 4 -- THE WIRING, DRIVEN
# ===========================================================================

section("SECTION 4 -- each budget is driven to its cap, the other untouched")

# *** THE RULING, MEASURED. *** A judge session spends its own $50 with a
# campaign cap of $300 sitting beside it, and the campaign is not moved.
with budgets(cap=300.0, rater_cap=50.0) as ledger:
    ledger.charge_usd(49.0, spend.SPEND_SOURCE_RATER)
    _under = (round(spend.remaining(spend.SPEND_SOURCE_RATER), 4),
              spend.cap_exceeded(spend.SPEND_SOURCE_RATER),
              round(spend.remaining(spend.SPEND_SOURCE_STAGE5), 4),
              spend.cap_exceeded(spend.SPEND_SOURCE_STAGE5))
    check("4a  CLEAN CONTROL: under its own cap the judge proceeds and the "
          "campaign is untouched", _under, (1.0, False, 300.0, False))
    ledger.charge_usd(2.0, spend.SPEND_SOURCE_RATER)
    check("4b  *** the judge crosses ITS OWN $50 and is DECLINED ***",
          (spend.cap_exceeded(spend.SPEND_SOURCE_RATER),
           raised(spend.require_budget, spend.SPEND_SOURCE_RATER, "probe")),
          (True, "SpendLimitReached"))
    check("4c  *** ...and Stage 5 is STILL ADMITTED, with $300 of campaign "
          "budget untouched: neither program can starve the other ***",
          (spend.cap_exceeded(spend.SPEND_SOURCE_STAGE5),
           round(spend.remaining(spend.SPEND_SOURCE_STAGE5), 4),
           raised(spend.require_budget, spend.SPEND_SOURCE_STAGE5, "probe")),
          (False, 300.0, None))
    check("4c-i ...and the refusal NAMES the budget that stopped it and the "
          "constant that moves it",
          ("rater" in str(drive(spend.require_budget,
                                spend.SPEND_SOURCE_RATER, "probe")),
           "config.RATER_SPEND_CAP_USD"
           in str(drive(spend.require_budget,
                        spend.SPEND_SOURCE_RATER, "probe"))),
          (True, True))

# *** AND THE REVERSE. *** A campaign spends its own $300 and the judge's $50
# is still there. This is the direction the ruling protects against a long
# campaign: under one shared number the judge would have nothing left.
with budgets(cap=300.0, rater_cap=50.0) as ledger:
    ledger.charge_usd(299.0, spend.SPEND_SOURCE_STAGE5)
    ledger.charge_usd(2.0, spend.SPEND_SOURCE_EMBEDDING)
    check("4d  *** the campaign crosses its own $300 -- by the SUM of the "
          "paths assigned to it, not by Stage 5 alone ***",
          (spend.cap_exceeded(spend.SPEND_SOURCE_STAGE5),
           spend.cap_exceeded(spend.SPEND_SOURCE_EMBEDDING),
           spend.cap_exceeded(spend.SPEND_SOURCE_RAGAS_JUDGE)),
          (True, True, True))
    check("4e  *** ...and the JUDGE still has its whole $50: a campaign that "
          "ran long does not silently leave the judge nothing ***",
          (spend.cap_exceeded(spend.SPEND_SOURCE_RATER),
           round(spend.remaining(spend.SPEND_SOURCE_RATER), 4),
           raised(spend.require_budget, spend.SPEND_SOURCE_RATER, "probe")),
          (False, 50.0, None))

# THE SEED IS ATTRIBUTED, WHICH IS THE HALF A CAP-ONLY SPLIT LEAVES BROKEN.
with budgets(cap=300.0, rater_cap=50.0) as ledger:
    ledger.seed(_rater.rater_spend_before({_rater.STATE_SPEND_KEY: 45.0}))
    check("4f  *** a resumed judge session's baseline lands in the RATER "
          "budget and in NO other ***",
          (round(spend.active_spend(spend.SPEND_SOURCE_RATER), 4),
           round(spend.active_spend(spend.SPEND_SOURCE_STAGE5), 4),
           spend.seed_budget(ledger.seeded)),
          (45.0, 0.0, "rater"))
    ledger.charge_usd(6.0, spend.SPEND_SOURCE_RATER)
    check("4f-i ...so a resume continues under the REMAINDER of its own "
          "budget and stops where its own money runs out",
          spend.cap_exceeded(spend.SPEND_SOURCE_RATER), True)

with budgets(cap=300.0, rater_cap=50.0) as ledger:
    ledger.seed(spend.LedgerSeed(usd=290.0, rows=3, runs=1,
                                 source=spend.SEED_SOURCE_CAMPAIGN))
    check("4g  ...and the mirror: a resumed CAMPAIGN's baseline lands in the "
          "campaign budget and leaves the judge's whole",
          (round(spend.active_spend(spend.SPEND_SOURCE_STAGE5), 4),
           round(spend.active_spend(spend.SPEND_SOURCE_RATER), 4)),
          (290.0, 0.0))

# THE LATCH RECORDS THE BUDGET THAT STOPPED THE RUN, not "a cap".
# THE LEDGER CARRIES BOTH BUDGETS' MONEY HERE ON PURPOSE. With only the rater
# charged, `SPEND_LEDGER.total` and the rater budget's spend are the SAME
# number, so a latch that recorded the whole ledger would look correct -- and
# the revert that makes it do exactly that was MISSED until this scenario put
# $40 of campaign spend beside the judge's $11.
with budgets(cap=300.0, rater_cap=10.0) as ledger:
    ledger.charge_usd(40.0, spend.SPEND_SOURCE_STAGE5)
    ledger.charge_usd(11.0, spend.SPEND_SOURCE_RATER)
    check("4h-0 non-degeneracy: the whole ledger and the rater budget's spend "
          "are DIFFERENT numbers here, so 4h can tell them apart",
          (round(ledger.total, 4),
           round(spend.active_spend(spend.SPEND_SOURCE_RATER), 4)),
          (51.0, 11.0))
    spend.SPEND_STOP.poll(where="a probe", source=spend.SPEND_SOURCE_RATER)
    check("4h  the run latch records WHICH budget stopped it, with that "
          "budget's own spend and cap beside it -- a figure about the other "
          "program would be true and about the wrong money",
          (spend.SPEND_STOP.requested, spend.SPEND_STOP.budget,
           round(spend.SPEND_STOP.spent or 0.0, 4), spend.SPEND_STOP.cap),
          (True, "rater", 11.0, 10.0))


# ===========================================================================
# SECTION 5 -- THE CROSS-WIRE, PLANTED
# ===========================================================================

section("SECTION 5 -- the cross-wire is planted and has to be caught")


def _budget_of(mod, source):
    """What a (possibly planted) copy of spend.py says governs ``source``."""
    fn = getattr(mod, "budget_for", None)
    if fn is None:
        return _Absent("the copy has no budget_for")
    return drive(fn, source)


def _declines_rater_after_campaign(mod, *, campaign_spent):
    """Would this copy decline the JUDGE for money the CAMPAIGN spent?

    THE QUESTION THE SPLIT EXISTS TO ANSWER, asked of a module rather than of
    the shipped globals so a plant can be measured against the same drive.
    """
    ledger = getattr(mod, "SPEND_LEDGER", None)
    if ledger is None:
        return _Absent("the copy has no ledger")
    saved = (config.SPEND_CAP_USD, config.RATER_SPEND_CAP_USD,
             config.SPEND_CAP_ENFORCED)
    try:
        config.SPEND_CAP_USD = 300.0
        config.RATER_SPEND_CAP_USD = 50.0
        config.SPEND_CAP_ENFORCED = True
        ledger.reset()
        ledger.charge_usd(campaign_spent, mod.SPEND_SOURCE_STAGE5)
        return drive(mod.cap_exceeded, mod.SPEND_SOURCE_RATER)
    finally:
        ledger.reset()
        (config.SPEND_CAP_USD, config.RATER_SPEND_CAP_USD,
         config.SPEND_CAP_ENFORCED) = saved


def _rater_cap_of(mod):
    fn = getattr(mod, "rater_spend_cap", None)
    if fn is None:
        return _Absent("the copy has no rater_spend_cap")
    saved = (config.SPEND_CAP_USD, config.RATER_SPEND_CAP_USD)
    try:
        config.SPEND_CAP_USD, config.RATER_SPEND_CAP_USD = 300.0, 50.0
        return drive(fn)
    finally:
        config.SPEND_CAP_USD, config.RATER_SPEND_CAP_USD = saved


# THE CLEAN CONTROL FIRST, and it is a real exec of the shipped source with a
# substitution that changes nothing. Without it a probe that disagreed with
# everything would report every plant as caught while measuring nothing -- and
# an exec harness that could not load the UNPLANTED file would report the same.
_CLEAN = _plant(_SPEND_SRC, 'SPEND_BUDGET_RATER = "rater"',
                'SPEND_BUDGET_RATER = "rater"  # clean control')
_CONTROLS_RUN[0] += 1
check("5a  CLEAN CONTROL: an UNPLANTED copy of oncotriage/spend.py binds the "
      "judge to the rater budget and is NOT declined by $305 of campaign "
      "spend -- money that is OVER the campaign's own $300",
      (_budget_of(_CLEAN, "rater_batch"),
       _declines_rater_after_campaign(_CLEAN, campaign_spent=305.0)),
      ("rater", False))

# *** PLANT 1 -- THE MAP CROSS-WIRE. *** `rater_batch` mapped onto the campaign
# budget is the exact defect a careless split ships. IT IS CAUGHT AT IMPORT
# rather than at run time, and that is the totality guard working: the rater
# budget is then left governing NO billed path, which is a cap that can never
# be reached and a report line that can never be anything but zero.
_CROSS = _plant(_SPEND_SRC,
                "    SPEND_SOURCE_RATER: SPEND_BUDGET_RATER,",
                "    SPEND_SOURCE_RATER: SPEND_BUDGET_CAMPAIGN,")
_CONTROLS_RUN[0] += 1
check("5b  *** PLANT: mapping rater_batch onto the CAMPAIGN budget is REFUSED "
      "AT IMPORT, naming the budget left with no path -- the copy does not "
      "load at all ***",
      (isinstance(_CROSS, _Absent),
       "RuntimeError" in repr(_CROSS) and "govern no billed path"
       in repr(_CROSS)),
      (True, True))

# *** PLANT 2 -- THE SAME CROSS-WIRE, PAST THE IMPORT GUARD. *** The table is
# intact and the RESOLVER lies: `budget_for` answers "campaign" for everything.
# This is what a split that fixed the constants and not the lookup would ship,
# and no structural guard can see it -- only a drive can.
_ALWAYS = _plant(_SPEND_SRC,
                 "        return BUDGET_FOR_SOURCE[source]",
                 "        return SPEND_BUDGET_CAMPAIGN")
_CONTROLS_RUN[0] += 1
check("5c  *** PLANT: with budget_for() answering 'campaign' for every path, "
      "the judge is bound to the campaign's table ***",
      _budget_of(_ALWAYS, "rater_batch"), "campaign")
check("5c-i *** ...and is DECLINED by $305 the CAMPAIGN spent -- money "
      "another program spent, which is the whole defect the ruling removes "
      "***",
      _declines_rater_after_campaign(_ALWAYS, campaign_spent=305.0), True)
check("5c-ii *** ...and the shipped copy answers the OPPOSITE on the "
      "identical drive, which is what makes 5c-i a measurement rather than a "
      "statement about exec ***",
      (_declines_rater_after_campaign(_CLEAN, campaign_spent=305.0),
       _declines_rater_after_campaign(_ALWAYS, campaign_spent=305.0)),
      (False, True))

# *** PLANT 3 -- THE CROSS-WIRE ONE LAYER DOWN. *** The map is right and the
# RESOLVER reads the wrong constant. A split that fixed only the table would
# ship this, and it is invisible to every check about budgets.
_WRONGCAP = _plant(_SPEND_SRC,
                   '    cap = getattr(config, "RATER_SPEND_CAP_USD", None)',
                   '    cap = getattr(config, "SPEND_CAP_USD", None)')
_CONTROLS_RUN[0] += 1
check("5d  *** PLANT: rater_spend_cap() reading the campaign constant hands "
      "the judge a $300 budget, and the shipped one hands it $50 ***",
      (_rater_cap_of(_WRONGCAP), _rater_cap_of(_CLEAN)), (300.0, 50.0))
check("5d-i ...and the AST pin in 3b is what catches it WITHOUT running "
      "anything: the planted body's CODE no longer names "
      "RATER_SPEND_CAP_USD, while its docstring still does",
      "RATER_SPEND_CAP_USD" in _code_names(
          ast.parse(_SPEND_SRC.replace(
              '    cap = getattr(config, "RATER_SPEND_CAP_USD", None)',
              '    cap = getattr(config, "SPEND_CAP_USD", None)', 1)),
          "rater_spend_cap"),
      False)

check("5e  every plant was applied to an IN-MEMORY COPY and every control ran "
      "(non-degeneracy: a section whose plants silently did not apply would "
      "report this as zero)", _CONTROLS_RUN[0], 4)
check("5e-i ...and oncotriage/spend.py on disk was never written, which "
      "section 7 re-checks by hash",
      _SPEND_SRC == open(_READ_FILES[0], encoding="utf-8").read(), True)


# ===========================================================================
# SECTION 6 -- THE REPORT PRINTS EACH CAP ON THE RUNS IT GOVERNS
# ===========================================================================

section("SECTION 6 -- the closing block names every budget, every run")

with budgets(cap=300.0, rater_cap=50.0) as ledger:
    ledger.charge_usd(12.0, spend.SPEND_SOURCE_STAGE5)
    _lines = spend.report_lines()
    check("6a  *** every budget prints spent, cap and remaining -- including "
          "one this run never touched, because a budget printed only when it "
          "spent would make its silence read as coverage ***",
          sorted(b for b in spend.SPEND_BUDGETS
                 if any(ln.startswith(f"  spent {b} ") for ln in _lines)
                 and any(ln.startswith(f"  cap {b} ") for ln in _lines)
                 and any(ln.startswith(f"  remaining {b} ")
                         for ln in _lines)),
          sorted(spend.SPEND_BUDGETS))
    check("6b  ...and the two figures are the budgets' own, not the ledger's "
          "total",
          (str(first(_lines, "  spent campaign")).strip().endswith("$12.0000"),
           str(first(_lines, "  spent rater")).strip().endswith("$0.0000")),
          (True, True))
    check("6c  ...and the whole-ledger figure says NO cap is compared against "
          "it, so a reader cannot take it for a budget",
          any("all budgets" in ln and "no cap is compared" in ln
              for ln in _lines), True)

with budgets(cap=300.0, rater_cap=50.0) as ledger:
    ledger.seed(_rater.rater_spend_before(
        {_rater.STATE_SPEND_KEY: 9.0, "batches": [1, 2]}))
    check("6d  the resumed-baseline banner names the budget the baseline "
          "lands in, so a judge resuming $9 does not read as a campaign that "
          "has already spent it",
          ("rater" in spend.describe_seed(ledger.seeded),
           "campaign" in spend.describe_seed(ledger.seeded)),
          (True, False))
    check("6d-i ...and the closing block says so too",
          any("inherited" in ln and "-> rater" in ln
              for ln in spend.report_lines()), True)


# ===========================================================================
# SECTION 7 -- THE TREE IS AS IT WAS FOUND
# ===========================================================================

section("SECTION 7 -- nothing was spent, loaded or written")

check("7a  NO MODEL WAS LOADED: torch and transformers never entered "
      "sys.modules", sorted(m for m in ("torch", "transformers")
                            if m in sys.modules), [])
check("7b  no provider client was ever built, which is what says this file "
      "issued no request",
      spend.SPEND_LEDGER.calls, 0)
check("7c  the shipped configuration is restored",
      (config.SPEND_CAP_USD, config.RATER_SPEND_CAP_USD,
       config.SERVING_SPEND_CAP_USD, config.SPEND_CAP_ENFORCED),
      _START_CONFIG)
check("7d  ...and the policy is back to the campaign default",
      spend.policy(), spend.SPEND_POLICY_CAMPAIGN)
check("7e  every repository file this test reads is byte-identical to how it "
      "was found",
      {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
       for p in _READ_FILES}, _BASELINE_HASHES)
check("7e-i non-degeneracy: the four hashes are four different values, so 7e "
      "is not one file compared with itself",
      len(set(_BASELINE_HASHES.values())), len(_READ_FILES))

spend.SPEND_LEDGER.reset()
spend.SPEND_STOP.reset()
spend.SPEND_GATE_SKIPS.clear()
spend.SPEND_LEDGER_FAULTS.clear()
check("7f  the process-global ledger and latch are left clean, so a runner "
      "importing this module afterwards does not inherit a spend it did not "
      "make", (spend.SPEND_LEDGER.total, spend.SPEND_STOP.requested),
      (0.0, False))


print("\n" + "=" * 78)
print(f"RESULTS: {_RESULTS['passed']} passed, {_RESULTS['failed']} failed")
print("=" * 78)
for _label, _expected, _actual in _FAILURES:
    print(f"  FAILED: {_label}\n     expected: {_expected!r}"
          f"\n     actual:   {_actual!r}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep 01 2026

@author: ramyalsaffar
"""
