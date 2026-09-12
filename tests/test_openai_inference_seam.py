###############################################################################
# OpenAI Inference Seam Test: a split client that cannot fall through to live
###############################################################################

"""``deps.get_openai_inference_client`` and the inheritance that makes it safe.

WHY THIS FILE EXISTS, AND IT IS AN INCIDENT RECORD RATHER THAN A DESIGN NOTE.
----------------------------------------------------------------------------
Stage 5's OpenAI client was split off from Stage 2's so the covered inference
path could take SDK retries to 0 (the retry policy's attempt budget is a TOTAL
over WIRE attempts, so an SDK retrying underneath it makes the bound true of
the policy and false of the wire) while the embedding path kept its retry,
which is its only resilience.

THE SPLIT AS FIRST WRITTEN CAUSED REAL, BILLED CALLS. Thirty-five test files
install a stub under ``deps.OPENAI_CLIENT``; none of them knew the new key
existed. Resolving the new key independently therefore made Stage 5 fall
through to the real factory INSIDE harnesses that believed they had stubbed it:
two suites built a live client and a real ``chat.completions.create`` was
billed while the recorder counted zero calls. That is the pass-20c-2c
regression -- "a live OpenAI call inside a harness that reports it made none"
-- reached through a second CLIENT instead of a second PROVIDER.

THE FIX IS INHERITANCE, AND THIS FILE IS WHAT DETECTS ITS ABSENCE. An unstubbed
inference seam resolves to an installed ``OPENAI_CLIENT`` override. That
restores the pre-split contract -- override that key and you have redirected
every OpenAI call the agent makes -- without weakening anything: a harness that
wants the two genuinely separate installs both, and ``fixtures/capture.py``
does exactly that.

WHAT THIS FILE HOLDS
--------------------
    1. THE CLOSED THREE-STATE VOCABULARY. Only ``default`` can reach the
       network, so a test meaning "nothing here can call out" must be able to
       NAME that state rather than infer it from an object's type.
    2. THE THREE ROUTES, DRIVEN. Own override wins; an OPENAI_CLIENT-only stub
       is INHERITED and returned; nothing installed is ``default``.
    3. THE INHERITED ROUTE BUILDS AND CACHES NOTHING -- the property that makes
       it safe to borrow. A cached inheritance would pin Stage 5 to a stub
       after the harness that installed it had restored the seam.
    4. THE DIAGNOSTIC AGREES WITH THE ACCESSOR in all three states. A state
       function that reports one thing while the accessor does another is
       worse than no diagnostic, because a harness would trust it.
    5. THE SPLIT ITSELF, BY AST: the two factories carry DIFFERENT retry
       constants, and Stage 5's call sites name the inference accessor while
       the embedding call site does not.
    6. BOTH KEYS ARE HOOKED by the fixture harness, which is what stops a
       capture recording nothing while billing.

WHAT IT COSTS TO RUN
--------------------
No network, no keys, NO SPEND -- and that is structural rather than hopeful:
the only state that can construct a client is ``default``, and this file never
calls the accessor in that state. It asserts ``is_resolved`` is False at the
end, so a client built by accident is a recorded failure. NO MODEL LOAD
(``ONCOTRIAGE_DEFER_LOCAL_MODELS`` above the imports), no live Qdrant, no
corpus, no database, no git history, no live server.

It writes NOTHING anywhere, not even a temp directory, and it EXECS NOTHING:
every control is a different override state handed to the real function, or an
``ast`` walk over source read as text. The one mutable thing it touches is the
deps override table, restored through ``restore_overrides`` with the restore
ASSERTED. NOT in the collision matrix; the three repository files it reads are
sha256-compared at the end.

Run from terminal:
    python tests/test_openai_inference_seam.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
import ast
import hashlib
import os
import sys

os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CODE_DIR = os.path.dirname(_TESTS_DIR)
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from oncotriage import config                                   # noqa: E402
from oncotriage.agent import deps                               # noqa: E402
from oncotriage.fixtures import capture                         # noqa: E402


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


def section(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


_PATHS = {
    "config":     os.path.abspath(config.__file__),
    "deps":       os.path.abspath(deps.__file__),
    "evaluation": os.path.join(_CODE_DIR, "oncotriage", "agent", "evaluation.py"),
}
_SHA_BEFORE = {k: hashlib.sha256(open(p, "rb").read()).hexdigest()
               for k, p in _PATHS.items()}


class Stub:
    """A stand-in with no client surface at all -- if anything tried to USE it
    as a client the failure would be loud, which is what we want from a value
    that must never be mistaken for the real thing."""

    def __init__(self, tag):
        self.tag = tag

    def __repr__(self):
        return f"<Stub {self.tag}>"


#------------------------------------------------------------------------------
section("1. The closed three-state vocabulary")
#------------------------------------------------------------------------------

check("there are exactly three states",
      len(deps.OPENAI_INFERENCE_SEAM_STATES), 3)
check("...and they are the three the accessor can take, in a stable order",
      list(deps.OPENAI_INFERENCE_SEAM_STATES),
      [deps.SEAM_OWN_OVERRIDE,
       deps.SEAM_INHERITED_FROM_OPENAI_CLIENT,
       deps.SEAM_DEFAULT])
check("...all distinct, so a caller branching on them cannot conflate two",
      len(set(deps.OPENAI_INFERENCE_SEAM_STATES)), 3)
check("the inference key is in the CLOSED override vocabulary, so a harness "
      "can install under it at all",
      deps.OPENAI_INFERENCE_CLIENT in deps.OVERRIDE_KEYS, True)


#------------------------------------------------------------------------------
section("2. The three routes, driven -- and only one can reach the network")
#------------------------------------------------------------------------------

check("PRECONDITION: nothing is installed, so this file starts from the one "
      "state that COULD build", deps.openai_inference_seam_state(),
      deps.SEAM_DEFAULT)
check("...and nothing has been built yet",
      deps.is_resolved(deps.OPENAI_INFERENCE_CLIENT), False)

# -- the inherited route, which is the one the incident was about ------------
_outer = Stub("openai_client")
_saved = deps.set_overrides({deps.OPENAI_CLIENT: _outer})

check("*** AN OPENAI_CLIENT-ONLY STUB PUTS THE SEAM IN THE INHERITED STATE ***",
      deps.openai_inference_seam_state(),
      deps.SEAM_INHERITED_FROM_OPENAI_CLIENT)
check("*** ...AND THE INFERENCE ACCESSOR RETURNS THAT STUB, so Stage 5 cannot "
      "reach a real client inside a harness that stubbed the other key ***",
      deps.get_openai_inference_client() is _outer, True)
check("*** ...HAVING BUILT AND CACHED NOTHING: an inheritance that cached "
      "would pin Stage 5 to a stub after the harness restored the seam ***",
      deps.is_resolved(deps.OPENAI_INFERENCE_CLIENT), False)

# -- own override wins ------------------------------------------------------
_inner = Stub("openai_inference_client")
_saved2 = deps.set_overrides({deps.OPENAI_INFERENCE_CLIENT: _inner})

check("with BOTH installed the state is own_override",
      deps.openai_inference_seam_state(), deps.SEAM_OWN_OVERRIDE)
check("...and the OWN stub wins, so a harness that wants the two separate "
      "gets them separate", deps.get_openai_inference_client() is _inner, True)
check("...while the other seam is untouched",
      deps.get_openai_client() is _outer, True)
check("...and the two stubs really are different objects, so the check above "
      "is not one object compared with itself", _inner is _outer, False)

deps.restore_overrides(_saved2)
check("after restoring the inner override the seam falls back to INHERITED, "
      "not to default", deps.openai_inference_seam_state(),
      deps.SEAM_INHERITED_FROM_OPENAI_CLIENT)

deps.restore_overrides(_saved)
check("THE RESTORE TOOK: the seam is back to default",
      deps.openai_inference_seam_state(), deps.SEAM_DEFAULT)
check("...and no override is left installed under either OpenAI key",
      [k for k in (deps.OPENAI_CLIENT, deps.OPENAI_INFERENCE_CLIENT)
       if deps.peek(k) is not deps.UNSET], [])


#------------------------------------------------------------------------------
section("3. The diagnostic agrees with the accessor, state for state")
#------------------------------------------------------------------------------

# A STATE FUNCTION THAT LIES IS WORSE THAN NONE, because a harness would trust
# it to decide whether it is exposed. Driven rather than argued: in each state
# the reported name and what the accessor actually hands back must agree.
_a, _b = Stub("A"), Stub("B")

_s1 = deps.set_overrides({deps.OPENAI_CLIENT: _a})
check("inherited: state and object agree",
      (deps.openai_inference_seam_state(),
       deps.get_openai_inference_client() is _a),
      (deps.SEAM_INHERITED_FROM_OPENAI_CLIENT, True))

_s2 = deps.set_overrides({deps.OPENAI_INFERENCE_CLIENT: _b})
check("own_override: state and object agree",
      (deps.openai_inference_seam_state(),
       deps.get_openai_inference_client() is _b),
      (deps.SEAM_OWN_OVERRIDE, True))

deps.restore_overrides(_s2)
deps.restore_overrides(_s1)
check("default: the state says so, and this file does NOT call the accessor "
      "here -- calling it is the one thing that could build a live client",
      deps.openai_inference_seam_state(), deps.SEAM_DEFAULT)


#------------------------------------------------------------------------------
section("4. The split itself, by AST")
#------------------------------------------------------------------------------

_cfg_tree = ast.parse(open(_PATHS["config"], encoding="utf-8").read())


def retry_constant(fn_name):
    """Which named constant this factory passes as max_retries."""
    for node in ast.walk(_cfg_tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            for call in ast.walk(node):
                if isinstance(call, ast.Call):
                    for kw in call.keywords:
                        if kw.arg == "max_retries" and isinstance(kw.value,
                                                                  ast.Name):
                            return kw.value.id
    return None


_embed_const = retry_constant("get_openai_client")
_infer_const = retry_constant("get_openai_inference_client")

check("the embedding factory carries OPENAI_SDK_MAX_RETRIES",
      _embed_const, "OPENAI_SDK_MAX_RETRIES")
check("the inference factory carries its OWN constant",
      _infer_const, "OPENAI_INFERENCE_SDK_MAX_RETRIES")
check("*** THE TWO FACTORIES DO NOT SHARE A RETRY CONSTANT -- which is the "
      "whole reason there are two clients ***", _embed_const == _infer_const,
      False)
check("the covered path's SDK retries are OFF, so the policy's attempt budget "
      "is true of the WIRE", config.OPENAI_INFERENCE_SDK_MAX_RETRIES, 0)
check("...and the uncovered path keeps its retry, which is its only "
      "resilience", config.OPENAI_SDK_MAX_RETRIES > 0, True)

# -- Stage 5's call sites name the inference accessor ------------------------
_eval_src = open(_PATHS["evaluation"], encoding="utf-8").read()
_eval_tree = ast.parse(_eval_src)
_chat_calls = [
    ast.unparse(n.func) for n in ast.walk(_eval_tree)
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    and ast.unparse(n.func).endswith("chat.completions.create")
]
check("Stage 5's chat call sites were found, so the checks below are not "
      "vacuous", len(_chat_calls) >= 1, True)
check("*** EVERY Stage 5 chat call reads the INFERENCE client ***",
      [c for c in _chat_calls if "get_openai_inference_client" not in c], [])
check("*** ...and NONE of them reads the embedding client ***",
      [c for c in _chat_calls if "deps.get_openai_client()" in c], [])


#------------------------------------------------------------------------------
section("5. Both keys are hooked by the fixture harness")
#------------------------------------------------------------------------------

# HOOKING ONLY THE OLD KEY WOULD LET EVERY STAGE 5 CALL GO TO THE REAL ENDPOINT
# WHILE THE CAPTURE REPORTED CLEAN -- which is the failure the seam exists to
# prevent, and the one this split briefly reintroduced.
check("the fixture harness hooks the embedding seam",
      deps.OPENAI_CLIENT in capture._HOOK_KEYS.values(), True)
check("*** ...AND THE INFERENCE SEAM ***",
      deps.OPENAI_INFERENCE_CLIENT in capture._HOOK_KEYS.values(), True)
check("the hooked NAMES and the hooked KEYS describe the same set, so the "
      "identity assertion covers every seam the mapping installs",
      sorted(capture._HOOKED_NAMES), sorted(capture._HOOK_KEYS))


#------------------------------------------------------------------------------
section("6. This file built nothing and changed nothing")
#------------------------------------------------------------------------------

check("*** NO OPENAI CLIENT WAS EVER BUILT -- the spend tripwire. A real "
      "client here means this file could have issued a billed request ***",
      [k for k in (deps.OPENAI_CLIENT, deps.OPENAI_INFERENCE_CLIENT)
       if deps.is_resolved(k)], [])

_sha_after = {k: hashlib.sha256(open(p, "rb").read()).hexdigest()
              for k, p in _PATHS.items()}
check("the three repository files this test reads are byte-unchanged",
      _sha_after, _SHA_BEFORE)
check("...and the three hashes are distinct, so the comparison is not one file "
      "hashed three times", len(set(_SHA_BEFORE.values())), 3)


#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
print(f"\n{'=' * 74}\nSUMMARY\n{'=' * 74}")
if _FAILURES:
    print("\nFAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")
print(f"\npassed: {_RESULTS['passed']}")
print(f"failed: {_RESULTS['failed']}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Sep 12 11:05:00 2026

@author: ramyalsaffar
"""
