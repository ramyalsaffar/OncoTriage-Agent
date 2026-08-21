# RRF Fusion Constants: config owns them, and retrieval READS them
######################################################################

"""RRF Config Ownership Test

The five RRF fusion constants -- ``RRF_K`` and the four channel multipliers
``RRF_WEIGHT_TITLE`` / ``RRF_WEIGHT_CONDITIONS`` / ``RRF_WEIGHT_CRITERIA`` /
``RRF_WEIGHT_DENSE`` -- were function-local literals inside
``node_hybrid_retrieval``, and Stage 3 carried a SECOND literal ``60`` as a
module-level ``RERANK_RRF_K`` under a comment asserting the two were equal.
They are ``oncotriage/config.py``'s now, imported by name, and the second
literal is deleted: one owner makes the two-stage equality structural rather
than a claim in a comment.

THE PATCH POINT IS THE POINT, AND IT IS THIS FILE'S REASON FOR EXISTING.
``agent/retrieval.py`` does ``from oncotriage.config import RRF_WEIGHT_TITLE``,
which BINDS the value into retrieval's own module namespace at import. A check
written against ``oncotriage.config.RRF_WEIGHT_TITLE`` therefore reaches
NOTHING -- it would pass forever whether or not the moved names are the ones
the fusion actually reads, which is the silent-pass shape this project has
shipped and caught before. Section 2 fires that patch point deliberately and
requires it to change nothing; section 3 patches ``retrieval``'s own namespace
and requires the fusion order to MOVE.

A FIXTURE THAT CANNOT FLIP IS A VACUOUS CONTROL. The fabricated ranks are
chosen so the title weight ALONE decides the order and the k term is identical
on both sides -- A = (W_TITLE + W_CRITERIA)/k against
B = (W_CONDITIONS + W_DENSE)/k, 3.0/k against 2.5/k -- so overriding W_TITLE
to 0.1 gives 1.1/k against 2.5/k: a real reversal, not a tie broken
differently. Section 3b asserts the flip against the baseline rather than
against a literal, and section 4 requires removing the override to restore the
baseline exactly.

SECTIONS 1-5 DO NOT BY THEMSELVES PROVE THE SHIPPED NODE IS THE CODE DOING
THAT ARITHMETIC -- ``fuse()`` is a re-derivation from retrieval's own module
globals. Section 6 closes that gap without a live Qdrant: it reads the shipped
source and requires the four fusion terms INSIDE ``node_hybrid_retrieval`` to
be built from the imported NAMES, with the RRF numerator 1.0 the only numeric
literal left in any of them. Its own control substitutes a literal 2.0 for a
weight name in an in-memory AST copy and requires the anchor to catch it.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO CORPUS, NO GIT HISTORY, NO
DATABASE. Nothing is imported that builds a model: ``ONCOTRIAGE_DEFER_LOCAL_
MODELS`` is set above the imports (the ordering lesson from pass 20c-3d) and
the arithmetic is re-derived rather than driven through Stage 2.

NOT in tests/run_serial_tests.py's collision matrix, derived rather than
assumed: it writes nothing anywhere -- no temp copies, no in-place edits, and
the one plant goes into an in-memory ``ast`` copy -- and the one repository
file it READS is ``oncotriage/agent/retrieval.py``, which is written by
neither ``tests/test_registries_cancer_code_claims_audit_control.py`` (which
writes ``oncotriage/registries/cancer_code_registry.py``) nor
``tests/test_config_snapshot_date_rot.py`` (which writes
``oncotriage/config.py``).

    python tests/test_agent_rrf_config_ownership.py
"""

import ast
import inspect
import os
import sys

# ABOVE THE PACKAGE IMPORTS ON PURPOSE. oncotriage/agent/deps.py reads this
# variable ONCE, at its own import, and `deps` arrives transitively on the
# first `oncotriage` import -- so an assignment underneath the imports reaches
# nothing and the local models load for real.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

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

from oncotriage import config
from oncotriage.agent import retrieval


# ===========================================================================
# HARNESS
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


def check_true(label: str, condition) -> None:
    check(label, bool(condition), True)


def section(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


_WEIGHT_NAMES = ("RRF_WEIGHT_TITLE", "RRF_WEIGHT_CONDITIONS",
                 "RRF_WEIGHT_CRITERIA", "RRF_WEIGHT_DENSE")
_ALL_NAMES = ("RRF_K",) + _WEIGHT_NAMES


# ===========================================================================
# THE FABRICATED RANK FIXTURE
# ===========================================================================
#
# Two trials. A is top of title + criteria; B is top of conditions + dense.
# All four ranks are 0, so the k term is identical on both sides and only the
# weights differ. See the module docstring for why that matters.
_TITLE = {"A": 0}
_CONDITIONS = {"B": 0}
_CRITERIA = {"A": 0}
_VECTOR = {"B": 0}


def fuse():
    """Stage 2's weighted RRF, re-derived from retrieval's OWN module globals.

    Reads through the module object rather than closing over imported values,
    because the whole question is which binding the fusion consults.
    """
    k = retrieval.RRF_K
    w_title = retrieval.RRF_WEIGHT_TITLE
    w_conditions = retrieval.RRF_WEIGHT_CONDITIONS
    w_criteria = retrieval.RRF_WEIGHT_CRITERIA
    w_dense = retrieval.RRF_WEIGHT_DENSE
    scores = {}
    for name in ("A", "B"):
        score = 0.0
        if name in _TITLE:
            score += w_title * (1.0 / (k + _TITLE[name]))
        if name in _CONDITIONS:
            score += w_conditions * (1.0 / (k + _CONDITIONS[name]))
        if name in _CRITERIA:
            score += w_criteria * (1.0 / (k + _CRITERIA[name]))
        if name in _VECTOR:
            score += w_dense * (1.0 / (k + _VECTOR[name]))
        scores[name] = score
    return [n for n, _ in sorted(scores.items(), key=lambda x: (x[1], x[0]),
                                 reverse=True)]


# ===========================================================================
# SECTION 0 -- non-degeneracy: the values, and that they are not zero
# ===========================================================================
section("SECTION 0 -- the bound values are config's, and are the old values")

check("retrieval's bound constants ARE config's",
      tuple(getattr(retrieval, n) for n in _ALL_NAMES),
      tuple(getattr(config, n) for n in _ALL_NAMES))
check("...and they are the pre-move values, so the promotion is "
      "VALUE-PRESERVING for every caller",
      tuple(getattr(config, n) for n in _ALL_NAMES),
      (60, 2.0, 1.5, 1.0, 1.0))
check("...and none of them is zero or None (a zero weight would make every "
      "flip below meaningless)",
      [n for n in _ALL_NAMES if not getattr(config, n)], [])


# ===========================================================================
# SECTION 1 -- the baseline order
# ===========================================================================
section("SECTION 1 -- the fixture's baseline order")

_BASE = fuse()
check("pre-change fusion order: the title weight dominates", _BASE, ["A", "B"])
check_true("...and the fixture CAN flip -- the two sides are not tied "
           "(non-degeneracy)",
           (config.RRF_WEIGHT_TITLE + config.RRF_WEIGHT_CRITERIA)
           != (config.RRF_WEIGHT_CONDITIONS + config.RRF_WEIGHT_DENSE))


# ===========================================================================
# SECTION 2 -- patching oncotriage.config must reach NOTHING
# ===========================================================================
section("SECTION 2 -- the wrong patch point, fired deliberately")

_cfg_saved = config.RRF_WEIGHT_TITLE
try:
    config.RRF_WEIGHT_TITLE = 0.1
    check("patching oncotriage.config reaches NOTHING -- a from-import is a "
          "binding, so a check written against this patch point would pass "
          "silently forever", fuse(), _BASE)
    check_true("...and the patch really was installed on config "
               "(non-degeneracy: this is not passing because nothing "
               "happened)", config.RRF_WEIGHT_TITLE == 0.1)
finally:
    config.RRF_WEIGHT_TITLE = _cfg_saved
check("config was restored", config.RRF_WEIGHT_TITLE, _cfg_saved)


# ===========================================================================
# SECTION 3 -- patching retrieval's own namespace MUST move the order
# ===========================================================================
section("SECTION 3 -- the right patch point flips the fusion")

_ret_saved = retrieval.RRF_WEIGHT_TITLE
try:
    retrieval.RRF_WEIGHT_TITLE = 0.1
    _OVERRIDDEN = fuse()
    check("with the title weight overridden in retrieval's namespace the "
          "fusion order CHANGES -- the imported name is the one read",
          _OVERRIDDEN, ["B", "A"])
    check_true("...and it differs from the baseline (non-degenerate)",
               _OVERRIDDEN != _BASE)
finally:
    retrieval.RRF_WEIGHT_TITLE = _ret_saved


# ===========================================================================
# SECTION 4 -- removing the override restores the pre-change order
# ===========================================================================
section("SECTION 4 -- the override is removable, and the baseline returns")

check("override removed: the order matches the pre-change order exactly",
      fuse(), _BASE)
check("...and the value is restored by identity, not by re-import",
      retrieval.RRF_WEIGHT_TITLE, config.RRF_WEIGHT_TITLE)


# ===========================================================================
# SECTION 5 -- the same, for RRF_K, at BOTH stages
# ===========================================================================
section("SECTION 5 -- ONE RRF_K, read by Stage 2 and by Stage 3")

# Stage 3 fuses across QUERIES, not across fields, and weights none of them.
# The module-level RERANK_RRF_K that used to hold a second literal 60 -- under
# a comment asserting the two were equal -- is deleted; section 6e checks that
# structurally, and this checks that the surviving name is the one it reads.
def _stage3_fuse(k):
    per_query = {"X": [0, 5, 5], "Y": [4, 1, 1]}
    scores = {n: sum(1.0 / (k + r) for r in ranks)
              for n, ranks in per_query.items()}
    return [n for n, _ in sorted(scores.items(), key=lambda x: (x[1], x[0]),
                                 reverse=True)]


_S3_BASE = _stage3_fuse(retrieval.RRF_K)
_k_saved = retrieval.RRF_K
try:
    retrieval.RRF_K = 1
    check_true("Stage 3's fusion reads the SAME RRF_K name: the order moves "
               "when it is overridden",
               _stage3_fuse(retrieval.RRF_K) != _S3_BASE)
finally:
    retrieval.RRF_K = _k_saved
check("...and is restored", _stage3_fuse(retrieval.RRF_K), _S3_BASE)
check("Stage 2 and Stage 3 read ONE constant, not two equal literals",
      retrieval.RRF_K, config.RRF_K)


# ===========================================================================
# SECTION 6 -- the SHIPPED node's own fusion expression, by AST
# ===========================================================================
section("SECTION 6 -- the shipped node builds its terms from the names")

_RETRIEVAL_SRC = inspect.getsource(retrieval)
_TREE = ast.parse(_RETRIEVAL_SRC)
_NODES = {n.name: n for n in ast.walk(_TREE) if isinstance(n, ast.FunctionDef)}


def _fusion_terms(fn_node):
    """(names, numeric literals) for every `score += ...` in fn_node."""
    out = []
    for n in ast.walk(fn_node):
        if (isinstance(n, ast.AugAssign) and isinstance(n.op, ast.Add)
                and isinstance(n.target, ast.Name) and n.target.id == "score"):
            names = sorted({x.id for x in ast.walk(n.value)
                            if isinstance(x, ast.Name)})
            nums = sorted({x.value for x in ast.walk(n.value)
                           if isinstance(x, ast.Constant)
                           and isinstance(x.value, (int, float))
                           and not isinstance(x.value, bool)})
            out.append((names, nums))
    return out


check_true("node_hybrid_retrieval was located in the shipped source",
           "node_hybrid_retrieval" in _NODES)
_TERMS = _fusion_terms(_NODES["node_hybrid_retrieval"])
check("node_hybrid_retrieval has exactly the four fusion terms",
      len(_TERMS), 4)
check("each term names its config weight and config.RRF_K -- and the ONLY "
      "numeric literal left in any of them is the RRF numerator 1.0",
      _TERMS,
      [(["RRF_K", "RRF_WEIGHT_TITLE", "nct_id", "title_ranks"], [1.0]),
       (["RRF_K", "RRF_WEIGHT_CONDITIONS", "conditions_ranks", "nct_id"], [1.0]),
       (["RRF_K", "RRF_WEIGHT_CRITERIA", "criteria_ranks", "nct_id"], [1.0]),
       (["RRF_K", "RRF_WEIGHT_DENSE", "nct_id", "vector_ranks"], [1.0])])

# The five names are module-level from-import bindings out of oncotriage.config
# -- not function locals, and not re-assigned anywhere in the module.
_imported = set()
for _n in ast.walk(_TREE):
    if isinstance(_n, ast.ImportFrom) and _n.module == "oncotriage.config":
        _imported |= {a.asname or a.name for a in _n.names}
check("all five come from oncotriage.config",
      sorted(set(_ALL_NAMES) - _imported), [])
check_true("...and that import walk found other config names too "
           "(non-degeneracy)", len(_imported) > len(_ALL_NAMES))

_rebound = sorted({t.id
                   for _n in ast.walk(_TREE)
                   if isinstance(_n, (ast.Assign, ast.AugAssign, ast.AnnAssign))
                   for t in ast.walk(_n.targets[0]
                                     if isinstance(_n, ast.Assign)
                                     else _n.target)
                   if isinstance(t, ast.Name) and t.id in _ALL_NAMES})
check("...and none of the five is re-assigned anywhere in the module (a "
      "function-local would shadow the import for the whole function)",
      _rebound, [])

check("RERANK_RRF_K is gone from the module entirely",
      [n.id for n in ast.walk(_TREE)
       if isinstance(n, ast.Name) and n.id == "RERANK_RRF_K"], [])
check("...and no string literal in the module names it either, so the second "
      "owner is not documented back into existence",
      [n.value for n in ast.walk(_TREE)
       if isinstance(n, ast.Constant) and isinstance(n.value, str)
       and "RERANK_RRF_K" in n.value], [])

# --- THE PLANT ------------------------------------------------------------
#
# Non-degeneracy for the two checks above: a re-hardcoded weight must be
# caught. The substitution goes into an in-memory copy; nothing on disk is
# touched, which is why this file writes nothing and is not in the collision
# matrix.
_PLANT_FROM = "score += RRF_WEIGHT_TITLE * (1.0 /"
_PLANT_TO = "score += 2.0 * (1.0 /"
check("the plant's anchor appears exactly once in the shipped source",
      _RETRIEVAL_SRC.count(_PLANT_FROM), 1)
_planted_src = _RETRIEVAL_SRC.replace(_PLANT_FROM, _PLANT_TO, 1)
check_true("...and the plant changed the source (non-degeneracy)",
           _planted_src != _RETRIEVAL_SRC)
_planted_nodes = {n.name: n for n in ast.walk(ast.parse(_planted_src))
                  if isinstance(n, ast.FunctionDef)}
_planted_terms = _fusion_terms(_planted_nodes["node_hybrid_retrieval"])
check("CONTROL: a re-hardcoded weight is caught -- the term loses its name "
      "and gains a numeric literal",
      _planted_terms[0], (["RRF_K", "nct_id", "title_ranks"], [1.0, 2.0]))
check_true("CONTROL: ...so the shipped expectation and the planted one differ",
           _planted_terms != _TERMS)
check_true("CONTROL: ...and the other three terms are untouched, so the plant "
           "is as narrow as it claims",
           _planted_terms[1:] == _TERMS[1:])

# A second plant, for the RERANK_RRF_K check: a module that reintroduced the
# name must be reported.
_reintroduced = ast.parse(_RETRIEVAL_SRC
                          + "\n\nRERANK_RRF_K = 60\n_x = RERANK_RRF_K\n")
check("CONTROL: a reintroduced RERANK_RRF_K is caught",
      sorted({n.id for n in ast.walk(_reintroduced)
              if isinstance(n, ast.Name) and n.id == "RERANK_RRF_K"}),
      ["RERANK_RRF_K"])


# ===========================================================================
# SECTION 7 -- config is the owner, and says so once
# ===========================================================================
section("SECTION 7 -- one declaration per constant, in config")

_CONFIG_SRC = open(os.path.abspath(config.__file__), encoding="utf-8").read()
_CONFIG_TREE = ast.parse(_CONFIG_SRC)
_declared = [t.id for n in _CONFIG_TREE.body if isinstance(n, ast.Assign)
             for t in n.targets
             if isinstance(t, ast.Name) and t.id in _ALL_NAMES]
check("every one of the five is declared at config's module scope",
      sorted(_declared), sorted(_ALL_NAMES))
check("...exactly once each", len(_declared), len(set(_declared)))


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 74)
print("SUMMARY")
print("=" * 74)
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
Created on Thu Aug 20 2026

@author: ramyalsaffar
"""
