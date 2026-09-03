# Stage 5: Structured Outputs, and the refusal path
###################################################

"""The schema Stage 5 enforces is the shape the prompt already asked for.

WHAT THE CHANGE UNDER TEST IS. ``oncotriage/agent/evaluation.py`` now sends a
strict ``json_schema`` ``response_format``, built by
``oncotriage/agent/response_schema.py``, on the one call it makes to the
matching model, and the output field the model reasons in is named
``assessment`` so that strict mode's alphabetical key emission puts that
reasoning BEFORE the verdict.

THE PROMPT MOVED ONCE, DELIBERATELY, AT PROMPT_VERSION 1.1.0 -- the field rename
and Section 5's two ordering sentences. ``tests/test_agent_prompt_version.py``
is the guard, and its golden snapshot was regenerated through its own
``--update-snapshot`` flag rather than by hand. Everything else about the shape
asked for is unchanged apart from one deletion: PROMPT_VERSION 1.3.0 removed
``trial_number`` from the contract, so the model is asked for SIX fields, and
the three vocabularies are untouched. The payload literals below carry the six
-- a stub does not validate against the schema, so a seventh field left here
would be this file simulating a response the contract now forbids.

WHY IT IS WORTH ENFORCING, in one sentence: a criterion status outside its arm's
vocabulary is resolved to "not_evaluable" by ``_normalize_arm``, which is the
right recovery and is still a lost judgement, and under an enum the model cannot
produce one.

WHAT THIS FILE HAS TO ESTABLISH, and why each is not obvious:

  1. THE SCHEMA MIRRORS THE PROMPT. Not "looks similar to" -- the field names
     and their ORDER are parsed out of the JSON template in the RENDERED system
     prompt and compared element by element. A schema hand-transcribed from a
     brief rather than from the template is how pass 20f-4 shipped `#2ecc71`
     where the original had `#2ca02c`, past an element-for-element render
     comparison, on an entry no data exercised.
  2. THE TWO CRITERION VOCABULARIES ARE COMPLETE AND DISJOINT. Disjoint alone is
     the wrong assertion and would pass on two vocabularies that do not share
     "not_evaluable" -- which they must. So the overlap is asserted to be
     EXACTLY that one member, in both directions.
  3. THEY AGREE WITH THE NORMALIZER. The same two vocabularies are written a
     second time as function locals inside ``node_llm_classifier_evaluation``.
     Two spellings of one fact is the shape this project removes on sight, and
     they are not consolidated here because that is a refactor of the
     normalizer. What stands in for consolidation is section 3, which reads both
     frozensets out of the SHIPPED function by AST and requires them to equal the
     schema's enums -- so the two cannot drift silently even while they are two.
  4. STRICT MODE'S STRUCTURAL RULES HOLD AT EVERY LEVEL. ``additionalProperties:
     false`` and a ``required`` naming every property, at every nesting depth,
     found by a WALK rather than by an enumeration of the levels somebody
     remembered. ``oncotriage/api/server.py``'s four endpoints were invisible to
     a top-level walk for exactly this reason.
  5. THE REFUSAL PATH RETURNS THE DOCUMENTED SHAPE WITHOUT TOUCHING THE PARSER.
     Both halves are driven, not read: the key set is DERIVED by running the
     parse-error path and comparing, and "without touching the parser" is proved
     by replacing ``json.loads`` with a tripwire that raises.

NO NETWORK, NO KEYS, NO SPEND, NO GIT, NO CORPUS, NO DATABASE, NO SUBPROCESS.
Every model response is a literal served by a stub installed through
``oncotriage/agent/deps.py``. Not in the collision matrix, derived: it writes
nothing anywhere -- every plant is a doctored COPY of a dict the shipped code
returns, or an AST copy in memory -- and the three source files it reads are
written by neither of the suite's two writers.

IT EXECS NOTHING, so it needs no ``_EXEC_ALLOWLIST`` entry, and that is a
property of how the controls are built rather than an accident. ``build_response_schema()``
returns a FRESH dict on every call, so a control can doctor a copy and re-run the
same predicate over it -- which is a stronger control than patching source,
because the thing under test is the predicate the shipped schema is judged by.

Run from terminal:
    python tests/test_agent_structured_outputs.py
"""

import ast
import builtins
import contextlib
import copy
import hashlib
import io
import json
import os
import socket
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

from oncotriage.agent import deps
from oncotriage.agent import evaluation as _evaluation_module
from oncotriage.agent import graph as _graph_module
from oncotriage.agent import response_schema as _schema_module
from oncotriage.agent.evaluation import (
    REFUSAL_ERROR_PREFIX,
    REFUSALS_OBSERVED,
    _unwrap_evaluations,
    node_llm_classifier_evaluation,
)
from oncotriage.agent.graph import route_after_llm_classifier
from oncotriage.agent.prompts import render_system_prompt
from oncotriage.agent.response_schema import (
    CRITERION_FIELDS,
    EVALUATIONS_KEY,
    EXCLUSION_STATUSES,
    INCLUSION_STATUSES,
    RESPONSE_SCHEMA_NAME,
    TRIAL_FIELDS,
    TRIAL_VERDICT_ENUM,
    build_response_format,
    build_response_schema,
    schema_object_paths,
)
from oncotriage.agent.state import TRIAL_VERDICTS
from oncotriage.config import MAX_LLM_CLASSIFIER_RETRIES

# ===========================================================================
# THIS FILE'S SUBJECT IS THE DORMANT OpenAI STAGE 5 REQUEST -- SO IT PINS IT
# ===========================================================================
#
# `config.MATCHING_PROVIDER` ships "bedrock_anthropic". Every Stage 5 stand-in
# below is installed at `deps.OPENAI_CLIENT` and wraps `chat.completions
# .create`, so at the shipped default the dispatch would reach
# `deps.BEDROCK_ANTHROPIC_CLIENT` and `converse` instead: the stand-in would
# never be called, every assertion here would compare against an empty
# recorder, and `config.get_bedrock_anthropic_client()` would BUILD -- boto3
# probing the instance metadata service from a suite that reports it makes no
# network call, and issuing live billed Converse requests on any host whose
# credential chain finds something.
#
# The pin, its cost and why it has one owner rather than a block per file are
# argued in tests/_provider_pin.py. THE SHIPPED ARM IS NOT COVERED BY THIS
# FILE; on Converse these subjects are covered by
# tests/test_agent_bedrock_anthropic_adapter.py and
# tests/test_agent_bedrock_anthropic_per_trial.py alone.
import _provider_pin                                             # noqa: E402

_PROVIDER_BEFORE_PIN = _provider_pin.pin_openai_arm(os.path.basename(__file__))


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


# A RAISE IS AN OUTCOME, NOT A REASON TO ABORT. This project has shipped the
# opposite five times -- tests/test_storage_query_layer.py,
# tests/test_dashboard_reproducibility_tab.py,
# tests/test_docker_qdrant_override_and_readiness.py,
# tests/test_agent_age_units_and_sex_filter.py and
# tests/test_agent_trial_verdict_normalization.py all had to fix it -- and every
# time the shape was the same: a defect the file exists to catch makes production
# code raise, the raise escapes through check()'s argument list while it is being
# evaluated, and the run reports one traceback where it owed a summary and N
# results. Every driver below converts a raise into a VALUE.
def guarded(fn, *args, **kwargs):
    """Call fn, returning a named marker string instead of propagating."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        return f"raised {type(exc).__name__}: {exc}"


def at(sequence, index, default="<absent>"):
    """sequence[index], or a named absence. A bare index on a short list is the
    same abort-instead-of-fail defect as an unguarded call: the plants below
    deliberately produce empty lists."""
    try:
        return sequence[index]
    except (IndexError, KeyError, TypeError):
        return default


_SOURCES = {
    "response_schema.py": os.path.abspath(_schema_module.__file__),
    "evaluation.py": os.path.abspath(_evaluation_module.__file__),
    "graph.py": os.path.abspath(_graph_module.__file__),
}


def _sha256_of(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# Taken BEFORE anything runs, compared at the end. Nothing here writes a
# repository file; section 9 is what makes that a measurement rather than a
# claim.
_SHA_BEFORE = {name: _sha256_of(path) for name, path in _SOURCES.items()}


# ===========================================================================
# SECTION 1 -- building the schema is pure and free
# ===========================================================================

print("=" * 75)
print("SECTION 1 -- the builder opens nothing and shares nothing")
print("=" * 75)

_SCHEMA = build_response_schema()
_FORMAT = build_response_format()

check("build_response_schema() returns an object schema",
      _SCHEMA.get("type"), "object")
check("build_response_format() declares json_schema",
      _FORMAT.get("type"), "json_schema")
check("strict mode is on -- without it the schema is a hint",
      _FORMAT.get("json_schema", {}).get("strict"), True)
check("the schema carries the declared name",
      _FORMAT.get("json_schema", {}).get("name"), RESPONSE_SCHEMA_NAME)
check("response_format embeds the same schema build_response_schema returns",
      _FORMAT.get("json_schema", {}).get("schema"), _SCHEMA)

# TWO CALLS ARE EQUAL AND NOT THE SAME OBJECT. This is the whole argument for
# the builder being a function rather than a module constant: a shared nested
# dict handed to an SDK is mutable state with no owner.
_second = build_response_schema()
check("two builds are equal", _second == _SCHEMA, True)
check("two builds are DISTINCT objects (no shared module-level dict)",
      _second is _SCHEMA, False)
_second["properties"][EVALUATIONS_KEY]["items"]["properties"].pop("nct_id", None)
check("mutating one build does not reach another",
      "nct_id" in build_response_schema()["properties"][EVALUATIONS_KEY]
      ["items"]["properties"], True)

# Importing a package module opens nothing; so must building this schema, which
# is called once per Stage 5 request. Traps armed, build performed, traps FIRED
# afterwards so the reading is not "the traps were never installed".
_trap_hits = []


def _trapped(name):
    def _raise(*_a, **_k):
        _trap_hits.append(name)
        raise AssertionError(f"{name} was called")
    return _raise


_saved_traps = (builtins.open, io.open, socket.socket, socket.create_connection)
builtins.open, io.open = _trapped("builtins.open"), _trapped("io.open")
socket.socket = _trapped("socket.socket")
socket.create_connection = _trapped("socket.create_connection")
try:
    _under_traps = guarded(build_response_format)
finally:
    (builtins.open, io.open, socket.socket,
     socket.create_connection) = _saved_traps

check("building the response format opens no file and no socket",
      _under_traps, _FORMAT)
check("...and no trap fired during the build", _trap_hits, [])

# NON-DEGENERACY: the traps were real. Without this the check above is also
# satisfied by four functions that were never installed.
builtins.open = _trapped("builtins.open")
try:
    _fired = guarded(builtins.open, "/nonexistent")
finally:
    builtins.open = _saved_traps[0]
check("non-degeneracy: the open trap fires when it is called",
      _fired.startswith("raised AssertionError"), True)


# ===========================================================================
# SECTION 2 -- the schema mirrors the prompt's JSON template
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 2 -- field names and order come from prompts.py, not from a brief")
print("=" * 75)


def _extract_json_template(prompt_text: str):
    """The JSON OBJECT under 'JSON template:' in a rendered system prompt.

    Sliced by BRACE COUNTING with string awareness rather than by a regex or a
    rindex of '}': the template contains braced prose above it and quoted text
    inside it, and a slice that ends at the wrong brace produces a
    JSONDecodeError that reads like a prompt defect rather than a test defect.

    IT COUNTS BRACES, NOT BRACKETS, AND THAT IS NOT INTERCHANGEABLE HERE. The
    previous version looked for the first '[' and would still have "worked"
    against the 1.2.0 envelope -- it would have found the array under
    "evaluations" and returned the trial list, silently never seeing the
    envelope it exists to check. A slice that succeeds for the wrong reason is
    the shape this suite keeps having to remove.
    """
    marker = "JSON template:"
    start_of_marker = prompt_text.index(marker) + len(marker)
    start = prompt_text.index("{", start_of_marker)
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(prompt_text)):
        char = prompt_text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(prompt_text[start:index + 1])
    raise ValueError("the JSON template's object is not closed")


# Rendered from the SHIPPED template, in the variant Stage 5 sends when the MeSH
# filter ran. Section 5's output-format block is outside the Section 2 variant
# switch, so either variant carries the same template; the confirmed one is
# chosen because it is the ordinary path.
# A declared stand-in for the patient record, which PROMPT_VERSION 1.6.0
# moved into the system message. It exists only so the template renders;
# nothing below reads it, and every span this file inspects (Section 5, the
# JSON template, the output contract) is outside the PATIENT RECORD block.
_PROBE_RECORD = "<probe: no patient record>"

_PROMPT = render_system_prompt(mesh_filter_applied=True,
                               mesh_filter_skip_reason="applied",
                               patient_record=_PROBE_RECORD)
_TEMPLATE_DOC = guarded(_extract_json_template, _PROMPT)

check("the JSON template parses out of the rendered prompt",
      isinstance(_TEMPLATE_DOC, dict), True)
# THE ENVELOPE THE DECODER ACTUALLY PRODUCES. Strict mode forces an object root,
# and since PROMPT_VERSION 1.2.0 the template shows one instead of a bare array.
check("the template's root is the one-key evaluations envelope",
      sorted(_TEMPLATE_DOC) if isinstance(_TEMPLATE_DOC, dict) else _TEMPLATE_DOC,
      [EVALUATIONS_KEY])
_TEMPLATE = (_TEMPLATE_DOC.get(EVALUATIONS_KEY)
             if isinstance(_TEMPLATE_DOC, dict) else None)
check("...holding an array of trial objects", isinstance(_TEMPLATE, list), True)
# NON-DEGENERACY FIRST. An empty template compared against a schema would
# satisfy every "every entry agrees" assertion below for free.
check("non-degeneracy: the template holds more than one trial",
      len(_TEMPLATE) > 1 if isinstance(_TEMPLATE, list) else False, True)
# THE PROSE AND THE TEMPLATE MUST AGREE ABOUT THE ENVELOPE. Section 5's three
# array statements were rewritten at 1.2.0; a template updated without them, or
# the reverse, is the disagreement this pass existed to remove.
check("Section 5's prose no longer asks for a bare JSON array",
      "valid JSON array" in _PROMPT, False)
check("...and names the object envelope instead",
      'single key "evaluations"' in _PROMPT, True)
# The needle dropped its leading "trials " at PROMPT_VERSION 1.6.0. Section 5
# used to open "Evaluate ALL {trial_count} trials in the one array"; it now
# counts the trials in the USER MESSAGE instead, because every chunk of a split
# or packed batch carries this identical system message and a whole-batch number
# in it instructed the model to answer about trials it had not been shown. The
# PROPERTY this check is about -- one array, under that key -- is unchanged, so
# the needle is narrowed to the part that states it rather than to the sentence
# that happened to carry it.
check("...and asks for all trials in the one array under that key",
      'in the one array under "evaluations"' in _PROMPT, True)
# ...and the count is about the MESSAGE, not about a number the system prompt
# cannot know per chunk. This is the half of 1.6.0 the guard above cannot see.
check("...and the completeness instruction names the user message rather than "
      "a batch-wide trial count",
      ("Evaluate EVERY trial in the user message" in _PROMPT,
       "Evaluate ALL" in _PROMPT), (True, False))

# SECTION 5 NAMES THE FIELDS IN PROSE AS WELL AS SHOWING THEM IN THE TEMPLATE,
# AND THAT PROSE LINE WAS THE ONE STATEMENT OF TRIAL_FIELDS NOTHING CHECKED.
# The template is compared field-for-field below and the schema is built from
# the tuple, so those two cannot drift; the sentence a reader of the prompt
# actually reads could, and did not have to move when trial_number was removed
# at 1.3.0 for the response to still validate. A prompt that lists a field the
# schema forbids is a prompt commanding output the API will reject.
#
# THE COMPARISON IS ON THE WHOLE LINE, NOT A SUBSTRING, and that was measured
# rather than assumed: the first version of this check asked
# `", ".join(TRIAL_FIELDS) in _PROMPT`, and when the 1.3.0 prompt edit was
# reverted in an in-memory copy it still PASSED -- the seven-field line begins
# with the six-field list, so the needle matched a prose line that named a
# field the schema forbids. Same defect class as the unanchored plant
# tests/test_package_invariants.py had to fix.
_FIELD_LIST_ANCHOR = "emits them in this order:\n"
_field_line = (_PROMPT.split(_FIELD_LIST_ANCHOR, 1)[1].split("\n", 1)[0]
               if _FIELD_LIST_ANCHOR in _PROMPT else "<anchor not found>")
check("non-degeneracy: Section 5's field-list line was located at all",
      _field_line != "<anchor not found>", True)
check("Section 5's prose field list is exactly TRIAL_FIELDS, in order, and "
      "nothing else",
      _field_line, ", ".join(TRIAL_FIELDS))
# The stale spelling, named rather than inferred: trial_number is gone from the
# contract, so no sentence may still describe seven fields or name it.
check("...and no trial_number survives anywhere in the rendered prompt",
      "trial_number" in _PROMPT, False)
check("...nor the seven-field count it was part of",
      "seven fields" in _PROMPT, False)

_schema_trial = _SCHEMA["properties"][EVALUATIONS_KEY]["items"]
_schema_trial_fields = tuple(_schema_trial["properties"])
_schema_inc_crit = _schema_trial["properties"]["inclusion_criteria"]["items"]
_schema_exc_crit = _schema_trial["properties"]["exclusion_criteria"]["items"]

check("TRIAL_FIELDS is exactly the schema's trial properties, in order",
      _schema_trial_fields, tuple(TRIAL_FIELDS))
check("CRITERION_FIELDS is exactly the inclusion criterion's properties",
      tuple(_schema_inc_crit["properties"]), tuple(CRITERION_FIELDS))
check("CRITERION_FIELDS is exactly the exclusion criterion's properties",
      tuple(_schema_exc_crit["properties"]), tuple(CRITERION_FIELDS))

for _i, _t in enumerate(_TEMPLATE if isinstance(_TEMPLATE, list) else []):
    check(f"template trial {_i}: field names and ORDER match the schema",
          tuple(_t), tuple(TRIAL_FIELDS))
    for _arm in ("inclusion_criteria", "exclusion_criteria"):
        for _j, _c in enumerate(_t.get(_arm, [])):
            check(f"template trial {_i}.{_arm}[{_j}]: criterion fields and ORDER",
                  tuple(_c), tuple(CRITERION_FIELDS))

# Every status the template actually writes must be IN the schema's enum for its
# arm. A subset check, because the template exercises a subset by design (its
# exclusion arms only ever show "not_evaluable"); the completeness of the
# vocabularies is section 3's job, from a source that states all of them.
_tmpl_inc = sorted({c["status"] for t in (_TEMPLATE or [])
                    for c in t.get("inclusion_criteria", [])})
_tmpl_exc = sorted({c["status"] for t in (_TEMPLATE or [])
                    for c in t.get("exclusion_criteria", [])})
_tmpl_verdicts = sorted({t["eligible"] for t in (_TEMPLATE or [])})

check("non-degeneracy: the template writes inclusion statuses at all",
      len(_tmpl_inc) > 0, True)
check("every inclusion status in the template is in the schema's enum",
      [s for s in _tmpl_inc if s not in INCLUSION_STATUSES], [])
check("every exclusion status in the template is in the schema's enum",
      [s for s in _tmpl_exc if s not in EXCLUSION_STATUSES], [])
check("every trial verdict in the template is in the schema's enum",
      [v for v in _tmpl_verdicts if v not in TRIAL_VERDICT_ENUM], [])

# THE INVERSE, so the enum cannot contain a status the prompt never defines. The
# prompt states all of them in Section 1 as quoted literals, so presence of the
# quoted token in the rendered text is the derivation available without parsing
# prose.
for _status in tuple(INCLUSION_STATUSES) + tuple(EXCLUSION_STATUSES) + tuple(TRIAL_VERDICT_ENUM):
    check(f"the prompt defines the enum member {_status!r}",
          f'"{_status}"' in _PROMPT, True)


# ===========================================================================
# SECTION 3 -- the vocabularies: complete, disjoint, and agreed with the node
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 3 -- the two criterion vocabularies")
print("=" * 75)

check("the schema's inclusion enum is INCLUSION_STATUSES, in order",
      tuple(_schema_inc_crit["properties"]["status"]["enum"]),
      tuple(INCLUSION_STATUSES))
check("the schema's exclusion enum is EXCLUSION_STATUSES, in order",
      tuple(_schema_exc_crit["properties"]["status"]["enum"]),
      tuple(EXCLUSION_STATUSES))
check("the trial-level enum is state.TRIAL_VERDICTS and is not re-declared",
      tuple(_schema_trial["properties"]["eligible"]["enum"]),
      tuple(TRIAL_VERDICTS))

# DISJOINT EXCEPT FOR EXACTLY ONE SHARED MEMBER. Asserting bare disjointness
# would PASS on two vocabularies that do not share "not_evaluable" -- which
# every arm must have, because it is the status the whole prompt resolves
# uncertainty to.
_shared = set(INCLUSION_STATUSES) & set(EXCLUSION_STATUSES)
check("the two vocabularies share exactly one member", sorted(_shared),
      ["not_evaluable"])
check("the disqualifying statuses are disjoint",
      sorted(set(INCLUSION_STATUSES) & set(EXCLUSION_STATUSES) - {"not_evaluable"}),
      [])
check("no inclusion status is an exclusion status other than not_evaluable",
      sorted(s for s in INCLUSION_STATUSES
             if s in EXCLUSION_STATUSES and s != "not_evaluable"), [])
check("each vocabulary has three members and no duplicates",
      (len(set(INCLUSION_STATUSES)), len(INCLUSION_STATUSES),
       len(set(EXCLUSION_STATUSES)), len(EXCLUSION_STATUSES)), (3, 3, 3, 3))


def _node_local_vocabularies(source_path: str):
    """The two frozensets written inside node_llm_classifier_evaluation.

    Read BY AST out of the shipped file rather than imported, because they are
    function locals: there is no other way to reach them, and the point of
    reaching them is that they are a SECOND spelling of the enums above. A
    substring search would find the names in this file's own prose; a walk over
    the function's body cannot.
    """
    with open(source_path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "node_llm_classifier_evaluation":
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Assign):
                continue
            for target in inner.targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id not in ("_INCLUSION_STATUSES", "_EXCLUSION_STATUSES"):
                    continue
                value = inner.value
                # frozenset({...}) -- the literal is the call's one argument.
                if (isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id == "frozenset"
                        and value.args):
                    found[target.id] = ast.literal_eval(value.args[0])
                else:
                    found[target.id] = ast.literal_eval(value)
    return found


_NODE_VOCAB = guarded(_node_local_vocabularies, _SOURCES["evaluation.py"])

check("non-degeneracy: both node-local vocabularies were located by the walk",
      sorted(_NODE_VOCAB) if isinstance(_NODE_VOCAB, dict) else _NODE_VOCAB,
      ["_EXCLUSION_STATUSES", "_INCLUSION_STATUSES"])
check("non-degeneracy: neither located vocabulary is empty",
      all(len(v) == 3 for v in _NODE_VOCAB.values())
      if isinstance(_NODE_VOCAB, dict) else False, True)
check("the normalizer's inclusion vocabulary equals the schema's enum",
      _NODE_VOCAB.get("_INCLUSION_STATUSES") if isinstance(_NODE_VOCAB, dict)
      else _NODE_VOCAB, set(INCLUSION_STATUSES))
check("the normalizer's exclusion vocabulary equals the schema's enum",
      _NODE_VOCAB.get("_EXCLUSION_STATUSES") if isinstance(_NODE_VOCAB, dict)
      else _NODE_VOCAB, set(EXCLUSION_STATUSES))


# ===========================================================================
# SECTION 4 -- strict mode's structural rules, at every level
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 4 -- additionalProperties false and complete required, everywhere")
print("=" * 75)


def _strict_defects(schema):
    """Every object node violating strict mode's two structural rules.

    Returns a sorted list of "pointer: reason" strings, so a failure NAMES the
    level rather than reporting a boolean. The walk is recursive by
    construction -- see schema_object_paths -- because an enumeration of levels
    silently stops covering a depth somebody adds later.
    """
    defects = []
    for pointer, node in schema_object_paths(schema):
        if node.get("additionalProperties") is not False:
            defects.append(f"{pointer}: additionalProperties is not False")
        declared = list(node.get("properties", {}))
        required = list(node.get("required", []))
        if sorted(required) != sorted(declared):
            defects.append(f"{pointer}: required {sorted(required)} != "
                           f"properties {sorted(declared)}")
    return sorted(defects)


_OBJECT_NODES = schema_object_paths(_SCHEMA)
# NON-DEGENERACY: an empty walk makes "no defects" true for free. Four object
# levels: the root, one trial, and one criterion per arm.
check("non-degeneracy: the walk finds four object levels",
      len(_OBJECT_NODES), 4)
check("every object level is present by pointer",
      sorted(p for p, _ in _OBJECT_NODES),
      sorted(["/",
              f"/properties/{EVALUATIONS_KEY}/items",
              f"/properties/{EVALUATIONS_KEY}/items/properties/"
              "inclusion_criteria/items",
              f"/properties/{EVALUATIONS_KEY}/items/properties/"
              "exclusion_criteria/items"]))
check("the shipped schema has no strict-mode structural defect",
      _strict_defects(_SCHEMA), [])
check("the root requires exactly the evaluations key",
      (list(_SCHEMA["properties"]), _SCHEMA["required"]),
      ([EVALUATIONS_KEY], [EVALUATIONS_KEY]))
check("the evaluations key holds an array of trial objects",
      (_SCHEMA["properties"][EVALUATIONS_KEY]["type"],
       _schema_trial["type"]), ("array", "object"))


# ===========================================================================
# SECTION 5 -- the call site sends it, and the fixtures record it
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 5 -- response_format reaches create(), and reaches the fixture")
print("=" * 75)


def _create_keywords(source_path: str):
    """The keyword names on the create() call inside call_matching_model.

    By AST rather than by grep: the file's own docstring names response_format
    several times in prose, and a substring search cannot tell an argument from
    an argument being discussed.
    """
    with open(source_path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "call_matching_model":
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "create"):
                    return inner
    return None


_CREATE = _create_keywords(_SOURCES["evaluation.py"])
_CREATE_KWS = [k.arg for k in _CREATE.keywords] if _CREATE is not None else []

check("non-degeneracy: the create() call was located and carries keywords",
      len(_CREATE_KWS) > 3, True)
check("response_format is passed to create()",
      "response_format" in _CREATE_KWS, True)
# THE PRE-EXISTING PARAMETERS ARE STILL THERE. The pass promised not to
# restructure the call; this is what says it did not.
check("the call still carries every pre-existing generation parameter",
      [k for k in ("model", "messages", "max_completion_tokens",
                   "reasoning_effort", "seed", "timeout")
       if k not in _CREATE_KWS], [])
check("no SDK method switch: it is still chat.completions.create",
      ast.unparse(_CREATE.func).endswith("chat.completions.create")
      if _CREATE is not None else False, True)
# with_options() returns a NEW client object and would hand back an UNWRAPPED
# client, bypassing the fixture proxy entirely. The file's own docstring is the
# argument; this is the check.
#
# BY AST ATTRIBUTE, NOT BY SUBSTRING, and the first version of this check was
# the substring and FAILED -- correctly, on the module docstring, which names
# with_options four times explaining why it is forbidden. A file that argues
# about a construct cannot be grepped for that construct; the same defect the
# Docker pass hit on Dockerfile comments.
def _attribute_names(tree):
    return {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}


_EVAL_TREE = ast.parse(open(_SOURCES["evaluation.py"], encoding="utf-8").read())
check("with_options() is never CALLED in the module (prose about it is fine)",
      "with_options" in _attribute_names(_EVAL_TREE), False)
check("non-degeneracy: the attribute walk finds attributes at all, and finds "
      "the one the call site does use",
      "create" in _attribute_names(_EVAL_TREE), True)
check("non-degeneracy: a planted with_options IS seen by the same walk",
      "with_options" in _attribute_names(
          ast.parse("client.with_options(max_retries=1).chat")), True)

_rf_kw = next((k for k in (_CREATE.keywords if _CREATE is not None else [])
               if k.arg == "response_format"), None)
check("response_format is BUILT at the call, not bound to a module constant",
      ast.unparse(_rf_kw.value) if _rf_kw is not None else "<absent>",
      "build_response_format()")

# The fixture contract: the recorder and the replayer must both carry the key,
# in the same block, or a fixture stops being able to see the schema.
for _name, _module_path in (
    ("capture", os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(_evaluation_module.__file__))), "fixtures", "capture.py")),
    ("replay", os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(_evaluation_module.__file__))), "fixtures", "replay.py")),
):
    _src = guarded(lambda p: open(p, encoding="utf-8").read(), _module_path)
    check(f"fixtures/{_name}.py records response_format in the request block",
          '"response_format": copy.deepcopy(kwargs.get("response_format"))'
          in _src if isinstance(_src, str) else _src, True)


# ===========================================================================
# SECTION 6 -- the envelope, both shapes
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 6 -- _unwrap_evaluations accepts the object and the bare array")
print("=" * 75)

_ARRAY = [{"nct_id": "NCT1"}]

check("a bare array is returned unchanged (the pre-pass shape)",
      _unwrap_evaluations(_ARRAY), _ARRAY)
check("...and is the SAME list, not a copy",
      _unwrap_evaluations(_ARRAY) is _ARRAY, True)
check("the wrapper object is unwrapped",
      _unwrap_evaluations({EVALUATIONS_KEY: _ARRAY}), _ARRAY)
check("an empty array under the key is a valid empty result, not a failure",
      _unwrap_evaluations({EVALUATIONS_KEY: []}), [])
check("a dict without the key is not unwrapped",
      _unwrap_evaluations({"trials": _ARRAY}), None)
check("a dict whose key holds a non-list is NOT coerced to empty",
      _unwrap_evaluations({EVALUATIONS_KEY: {"a": 1}}), None)
check("a bare string is not unwrapped", _unwrap_evaluations("NCT1"), None)
check("a bare number is not unwrapped", _unwrap_evaluations(7), None)
check("None is not unwrapped", _unwrap_evaluations(None), None)


# ===========================================================================
# SECTION 7 -- the refusal path
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 7 -- a refusal is not a parse failure")
print("=" * 75)

PATIENT = {
    "patient_id": "structured-outputs-patient",
    "demographics": {"age": 62, "sex": "female", "race": "white",
                     "ethnicity": "not hispanic or latino"},
    "conditions": [{"code": "254837009", "display": "Breast cancer",
                    "verification_status": "confirmed"}],
    "medications": [], "allergies": [], "observations": [], "procedures": [],
}


def _trial(nct_id):
    return {
        "nct_id": nct_id, "title": "A Study of Something", "phase": "Phase 2",
        "conditions": ["Breast Neoplasms"], "mesh_terms": ["Breast Neoplasms"],
        "eligibility": {"inclusion_criteria": "Adults with breast cancer",
                        "exclusion_criteria": "Pregnancy",
                        "min_age": 18, "max_age": 99, "sex": "ALL"},
    }


class _Message:
    """A response message. `refusal` is set only when a refusal is being
    driven, and is ABSENT (not None) on the ordinary stub -- which is also the
    shape tests/test_agent_retrieval_observability.py's stub has, so the
    getattr default is exercised rather than assumed."""

    def __init__(self, content, refusal=None):
        self.content = content
        if refusal is not None:
            self.refusal = refusal


class _Choice:
    def __init__(self, content, refusal=None, finish_reason="stop"):
        self.message = _Message(content, refusal)
        self.finish_reason = finish_reason


class _Usage:
    prompt_tokens = 1000
    completion_tokens = 200


class _Response:
    def __init__(self, content, refusal=None, finish_reason="stop"):
        self.choices = [_Choice(content, refusal, finish_reason)]
        self.usage = _Usage()
        # None keeps MatchingModelMismatchError out of a test that is not
        # about it; the node handles a missing model field explicitly.
        self.model = None


class StubOpenAI:
    """Serves one chosen response. No network, no key, no spend."""

    def __init__(self, content, refusal=None, finish_reason="stop"):
        self._content = content
        self._refusal = refusal
        self._finish_reason = finish_reason
        self.calls = 0
        self.seen_response_format = None
        self.chat = self
        self.completions = self

    def create(self, model, messages, **kwargs):
        self.calls += 1
        self.seen_response_format = kwargs.get("response_format")
        return _Response(self._content, self._refusal, self._finish_reason)


def log_records(stderr_text):
    """Every structured log record on a captured stderr, as dicts.

    oncotriage/observability.py writes one JSON object per line to stderr, so a
    line that does not parse is console UI rather than a record and is skipped.
    Returns a list, never an index -- a caller reaching for [0] on a path that
    emitted nothing is the abort-instead-of-fail defect again.
    """
    records = []
    for line in (stderr_text or "").splitlines():
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def log_events(stderr_text):
    """The `event` field of every record that carries one.

    NOT every record has one, and that is a fact about the shipped code rather
    than about this helper: Stage 5's parse-failure record carries
    status/error_type and NO event field. The non-degeneracy assertion below is
    therefore over RECORDS -- the first version of it was over events, and it
    reported the refusal path's distinctness as a failure of the parse path to
    log at all.
    """
    return [r["event"] for r in log_records(stderr_text) if r.get("event")]


def run_stage5(content, refusal=None, finish_reason="stop", nct_ids=("NCT00000001",)):
    """Drive Stage 5 with a stubbed model. Returns (result, stub)."""
    state = {
        "patient_data": PATIENT,
        "filtered_trials": [{"trial": _trial(n), "rerank_score": 5.0,
                             "rerank_score_raw": 5.0} for n in nct_ids],
        "llm_classifier_retries": 0,
        "mesh_filter_applied": True,
        "mesh_filter_skip_reason": "applied",
        "stage_timings": {},
    }
    stub = StubOpenAI(content, refusal, finish_reason)
    saved = deps.set_overrides({"openai_client": stub})
    err = io.StringIO()
    out = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            result = node_llm_classifier_evaluation(state)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        result = {"evaluations": [], "raised": f"{type(exc).__name__}: {exc}"}
    finally:
        deps.restore_overrides(saved)
    # Hung off the stub rather than added to the return tuple, so every existing
    # two-name unpacking below keeps working and no call site has to be edited
    # to read one thing.
    stub.stderr = err.getvalue()
    return result, stub


# ---- the ordinary path still works, and now sends the schema --------------
_GOOD = json.dumps({EVALUATIONS_KEY: [{
    "nct_id": "NCT00000001", "match_score": 0.0,
    "inclusion_criteria": [{"criterion": "Age 18-75",
                            "patient_value": "62", "status": "met"}],
    "exclusion_criteria": [{"criterion": "Pregnancy",
                            "patient_value": "Not applicable -- male",
                            "status": "not_violated"}],
    "assessment": "No known disqualifiers.", "eligible": "eligible"}]})

_ok_result, _ok_stub = run_stage5(_GOOD)
check("the wrapper-object response parses end to end",
      len(_ok_result.get("evaluations", [])), 1)
check("...and yields the model's verdict",
      at(_ok_result.get("evaluations", []), 0).get("eligible")
      if _ok_result.get("evaluations") else "<absent>", "eligible")
check("the call really carried a strict json_schema response_format",
      (_ok_stub.seen_response_format or {}).get("type"), "json_schema")
check("...naming the schema the module builds",
      (_ok_stub.seen_response_format or {}).get("json_schema", {}).get("name"),
      RESPONSE_SCHEMA_NAME)
check("no refusal was recorded on the ordinary path",
      "llm_classifier_refusal" in _ok_result, False)

# THE BARE ARRAY STILL PARSES. The prompt still asks for one and every
# recording made before this pass holds one.
_arr_result, _ = run_stage5(json.dumps(json.loads(_GOOD)[EVALUATIONS_KEY]))
check("the bare-array response still parses end to end",
      len(_arr_result.get("evaluations", [])), 1)

# ---- the parse-error path, driven, to DERIVE the key set ------------------
_parse_result, _ = run_stage5("this is not json")
check("non-degeneracy: the parse-error path was actually reached",
      _parse_result.get("error", "").startswith("GPT-4o JSON parse error"), True)

# THE NON-LIST DIAGNOSIS STILL NAMES THE TYPE THE MODEL SENT. The unwrap
# returns None when neither envelope is present, so assigning its result over
# `parsed` before the guard -- which is what the first draft of this change did
# -- makes every failure on this path report `type=NoneType`, the type of the
# failure itself, instead of the dict or string that caused it. That message is
# the only diagnosis the path produces.
_nonlist, _ = run_stage5(json.dumps({"trials": [{"nct_id": "NCT00000001"}]}))
check("a dict with the wrong key is reported as type=dict, not type=NoneType",
      _nonlist.get("error"), "GPT-4o returned non-list JSON (type=dict)")
_nonlist_str, _ = run_stage5(json.dumps("NCT00000001"))
check("a bare JSON string is reported as type=str",
      _nonlist_str.get("error"), "GPT-4o returned non-list JSON (type=str)")

# ---- the refusal path -----------------------------------------------------
_REFUSAL_TEXT = "I can't help with clinical eligibility determinations."
_ref_result, _ref_stub = run_stage5(None, refusal=_REFUSAL_TEXT)

check("a refusal returns no evaluations", _ref_result.get("evaluations"), [])
check("the error names it a refusal",
      _ref_result.get("error", "").startswith(REFUSAL_ERROR_PREFIX), True)
check("the error carries the model's own words",
      _REFUSAL_TEXT in _ref_result.get("error", ""), True)
check("the model identity is carried (None here: the stub reports no model)",
      "matching_model" in _ref_result, True)
check("the prompt identity is carried",
      ("llm_classifier_prompt_version" in _ref_result,
       bool(_ref_result.get("llm_classifier_prompt_sha256"))), (True, True))
check("the refusal text is the raw response for this run",
      _ref_result.get("llm_classifier_raw_response"), _REFUSAL_TEXT)
check("the routing flag is set", bool(_ref_result.get("llm_classifier_refusal")),
      True)

# NO PARSE RETRY IS SPENT. The state went in at 0 and must come back at 0 --
# the parse-error path, driven above, comes back at 1, which is what makes this
# a discriminating reading rather than a reading of a default.
check("a refusal does not spend a parse retry",
      _ref_result.get("llm_classifier_retries"), 0)
check("non-degeneracy: the parse-error path DOES spend one",
      _parse_result.get("llm_classifier_retries"), 1)

# THE KEY SET IS DERIVED FROM THE PARSE-ERROR RETURN, not retyped here. The
# instruction that shaped this path was "follow the existing parse-error return
# as the template for which keys the result must carry", and a hand-typed list
# would agree with whatever was written rather than with that return.
_ref_keys = set(_ref_result)
_parse_keys = set(_parse_result)
check("the refusal result carries every key the parse-error result does",
      sorted(_parse_keys - _ref_keys), [])
check("...and exactly one more: the routing flag",
      sorted(_ref_keys - _parse_keys), ["llm_classifier_refusal"])

# THE PARSER IS NOT ENTERED. Proved by replacing json.loads with a tripwire, in
# the module the node resolves it through, rather than by reading the code.
_tripwire_hits = []


def _json_loads_tripwire(*_a, **_k):
    _tripwire_hits.append("json.loads")
    raise AssertionError("the refusal path entered the JSON parser")


_saved_loads = _evaluation_module.json.loads
_evaluation_module.json.loads = _json_loads_tripwire
try:
    _ref_no_parse, _ = run_stage5(None, refusal=_REFUSAL_TEXT)
finally:
    _evaluation_module.json.loads = _saved_loads

check("the refusal path does not call json.loads", _tripwire_hits, [])
check("...and still returns the refusal",
      _ref_no_parse.get("error", "").startswith(REFUSAL_ERROR_PREFIX), True)

# NON-DEGENERACY: the tripwire was real and installed on the right object.
_evaluation_module.json.loads = _json_loads_tripwire
try:
    _parse_with_tripwire, _ = run_stage5("this is not json")
finally:
    _evaluation_module.json.loads = _saved_loads
check("non-degeneracy: the tripwire fires on a path that DOES parse",
      _tripwire_hits, ["json.loads"])

# A refusal beside unparseable content is still a refusal: the refusal is read
# before the content is, which is what stops it being misreported.
_ref_and_garbage, _ = run_stage5("}{ not json", refusal=_REFUSAL_TEXT)
check("a refusal alongside unparseable content reports the refusal",
      _ref_and_garbage.get("error", "").startswith(REFUSAL_ERROR_PREFIX), True)

# An empty-string refusal is NOT a refusal. The API sends null when the model
# did not refuse; "" is the same absence and must not open the path.
_empty_ref, _ = run_stage5(_GOOD, refusal="")
check("an empty refusal string is not treated as a refusal",
      len(_empty_ref.get("evaluations", [])), 1)

check("the refusal counter moved", REFUSALS_OBSERVED.total() > 0, True)

# A DISTINCT STRUCTURED LOG EVENT. The whole point of the separate path is that
# a query counting refusals must not also be counting malformed JSON, and
# before this both arrived as status=error with error_type=JSONDecodeError.
_ref_stderr = _ref_stub.stderr
_parse_stderr = run_stage5("this is not json")[1].stderr
check("non-degeneracy: both paths emit structured records at all",
      (len(log_records(_ref_stderr)) > 0,
       len(log_records(_parse_stderr)) > 0), (True, True))
check("the refusal emits event='refusal'",
      "refusal" in log_events(_ref_stderr), True)
check("the parse failure does NOT emit event='refusal'",
      "refusal" in log_events(_parse_stderr), False)
# Both are status=error, which is why the EVENT is what separates them: a
# consumer counting refusals cannot key on severity.
check("both paths do report status=error, so severity cannot separate them",
      (any(r.get("status") == "error" for r in log_records(_ref_stderr)),
       any(r.get("status") == "error" for r in log_records(_parse_stderr))),
      (True, True))
check("the refusal record carries no error_type (it is not an exception)",
      [r.get("error_type") for r in log_records(_ref_stderr)
       if r.get("event") == "refusal"], [None])
check("...whereas the parse failure names the exception type",
      any(r.get("error_type") == "JSONDecodeError"
          for r in log_records(_parse_stderr)), True)

# ---- the reasoning-first order guard --------------------------------------
#
# The field is named `assessment` so alphabetical emission puts it before
# `eligible`. That ordering is OBSERVED behaviour of the current model, not a
# documented API guarantee, so the guard turns a silent regression into an
# event. It must warn and must NOT fail the run.

check("assessment sorts before eligible -- the whole reason for the name",
      "assessment" < "eligible", True)

# THE GUARD'S RULE, APPLIED TO THE PROMPT'S OWN TEMPLATE. The template is an
# EXAMPLE of a conforming response, so a template that would trip the guard is
# a prompt showing the model the shape the guard exists to report. Checked on
# the rendered TEXT with the guard's own two needles, not on the parsed dict,
# because the guard reads bytes.
_TPL_TEXT = _PROMPT[_PROMPT.index("JSON template:"):]
check("the prompt's template puts assessment before eligible, as the guard "
      "requires of a real response",
      0 <= _TPL_TEXT.find('"assessment"') < _TPL_TEXT.find('"eligible"'), True)
# NON-DEGENERACY: both needles are actually present in the sliced text, so the
# comparison above is over two real positions rather than two -1s.
check("non-degeneracy: both needles occur in the template text",
      (_TPL_TEXT.find('"assessment"') >= 0,
       _TPL_TEXT.find('"eligible"') >= 0), (True, True))

_ORDERED = json.dumps({EVALUATIONS_KEY: [{
    "assessment": "No known disqualifiers.", "eligible": "eligible",
    "exclusion_criteria": [], "inclusion_criteria": [
        {"criterion": "Age 18-75", "patient_value": "62", "status": "met"}],
    "match_score": 0.0, "nct_id": "NCT00000001"}]})
_REVERSED = json.dumps({EVALUATIONS_KEY: [{
    "eligible": "eligible", "assessment": "No known disqualifiers.",
    "exclusion_criteria": [], "inclusion_criteria": [
        {"criterion": "Age 18-75", "patient_value": "62", "status": "met"}],
    "match_score": 0.0, "nct_id": "NCT00000001"}]})

_ord_result, _ord_stub = run_stage5(_ORDERED)
_rev_result, _rev_stub = run_stage5(_REVERSED)

check("the ordinary (assessment-first) response raises no order event",
      "reasoning_order_regression" in log_events(_ord_stub.stderr), False)
check("a verdict-before-assessment response DOES raise the event",
      "reasoning_order_regression" in log_events(_rev_stub.stderr), True)
# NON-DEGENERACY: both runs are otherwise identical and both SUCCEEDED, so the
# difference above is the key order and nothing else.
check("non-degeneracy: both responses parsed and produced a verdict",
      (len(_ord_result.get("evaluations", [])),
       len(_rev_result.get("evaluations", []))), (1, 1))
check("the guard does NOT fail the run: the verdict still comes through",
      at(_rev_result.get("evaluations", []), 0).get("eligible")
      if _rev_result.get("evaluations") else "<absent>", "eligible")
check("...and no error is set by the guard", _rev_result.get("error"), "")
# THE NEEDLES ARE QUOTED, AND THE FIRST VERSION OF THIS CHECK COULD NOT SEE
# WHETHER THEY WERE. It fed a response whose verdict key came last and whose
# value was "not_eligible", and asserted no event -- which passes with an
# UNQUOTED needle too, because a key always precedes its own value, so the
# first hit is the key either way. The revert harness reported it as MISSED,
# which is the only reason it was found: the assertion was vacuous.
#
# This one discriminates. The word "eligible" appears inside a CRITERION placed
# before "assessment", so:
#   quoted   -- the first '"eligible"' is the key, which is after assessment
#               -> no event, correct
#   unquoted -- the first 'eligible' is inside the criterion prose, before
#               assessment -> a false event on a perfectly ordered response
_PROSE_ELIGIBLE = json.dumps({EVALUATIONS_KEY: [{
    "inclusion_criteria": [{"criterion": "Not eligible for curative surgery",
                            "patient_value": "Documented", "status": "met"}],
    "assessment": "No known disqualifiers.",
    "exclusion_criteria": [], "match_score": 0.0,
    "nct_id": "NCT00000001", "eligible": "eligible"}]})
_pe_result, _pe_stub = run_stage5(_PROSE_ELIGIBLE)
check("the word 'eligible' in criterion prose does not fake an order event",
      "reasoning_order_regression" in log_events(_pe_stub.stderr), False)
# NON-DEGENERACY: the prose really is positioned where an unquoted needle would
# trip, i.e. before the assessment key.
check("non-degeneracy: the prose hit really does precede the assessment key",
      (_PROSE_ELIGIBLE.find("eligible") < _PROSE_ELIGIBLE.find('"assessment"'),
       _PROSE_ELIGIBLE.find('"eligible"') > _PROSE_ELIGIBLE.find('"assessment"')),
      (True, True))
check("non-degeneracy: that response parsed and produced a verdict",
      at(_pe_result.get("evaluations", []), 0).get("eligible")
      if _pe_result.get("evaluations") else "<absent>", "eligible")

# And a not_eligible VALUE is still not mistaken for the key.
_NOT_ELIG = json.dumps({EVALUATIONS_KEY: [{
    "assessment": "Known disqualifier: creatinine 3.4.",
    "exclusion_criteria": [], "inclusion_criteria": [
        {"criterion": "Creatinine", "patient_value": "3.4", "status": "not_met"}],
    "match_score": 0.0, "nct_id": "NCT00000001",
    "eligible": "not_eligible"}]})
_ne_result, _ne_stub = run_stage5(_NOT_ELIG)
check("a not_eligible VALUE does not raise the order event",
      "reasoning_order_regression" in log_events(_ne_stub.stderr), False)
check("non-degeneracy: that response really did carry not_eligible",
      at(_ne_result.get("evaluations", []), 0).get("eligible")
      if _ne_result.get("evaluations") else "<absent>", "not_eligible")

# ---- the router terminates on it -----------------------------------------
_ref_state = {"error": _ref_result.get("error"),
              "llm_classifier_retries": 0,
              "llm_classifier_refusal": _REFUSAL_TEXT,
              "evaluations": []}
check("the router sends a refusal to the error handler, not to a retry",
      route_after_llm_classifier(_ref_state), "error_handler")

# NON-DEGENERACY, AND IT IS THE WHOLE ARGUMENT FOR THE FLAG. The identical
# state WITHOUT the flag retries -- forever, because nothing increments the
# count on this path -- so the flag is what converts an infinite loop into a
# terminal route.
_no_flag = dict(_ref_state)
_no_flag.pop("llm_classifier_refusal", None)
check("non-degeneracy: the same state without the flag would retry",
      route_after_llm_classifier(_no_flag), "llm_classifier_retry")
check("the flag outranks a retry budget that is not exhausted",
      (MAX_LLM_CLASSIFIER_RETRIES > 0,
       route_after_llm_classifier({**_ref_state,
                                   "llm_classifier_retries": 0})),
      (True, "error_handler"))
# The pre-existing routes are untouched.
check("success still routes to finalize",
      route_after_llm_classifier({"evaluations": [{"nct_id": "x"}], "error": ""}),
      "finalize")
check("an exhausted parse budget still routes to the error handler",
      route_after_llm_classifier({"evaluations": [], "error": "boom",
                                  "llm_classifier_retries":
                                      MAX_LLM_CLASSIFIER_RETRIES}),
      "error_handler")


# ===========================================================================
# SECTION 8 -- every assertion above, shown to FAIL
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 8 -- the controls")
print("=" * 75)

# Each control doctors a COPY of what the shipped code returns and re-runs the
# SAME predicate the section above ran. Nothing on disk is touched, and the
# thing under test is the predicate rather than a restatement of it.

# EVERY MUTATION BELOW IS NON-DESTRUCTIVE ABOUT ITS OWN PRECONDITION, and this
# was found by running rather than by reading. Control 8a's first version did a
# bare `.pop("additionalProperties")`, which raises KeyError when the key is
# ALREADY GONE -- which is precisely the state the revert harness's r7 creates.
# So the one revert that removes additionalProperties made this file die at
# module level and report a traceback where it owed 134 results: a control that
# aborts is not a control, and it aborted on the defect it exists to catch.
# `.pop(k, None)` and a guarded remove are the fix.

# 8a -- a missing additionalProperties at the DEEPEST level, which is the one an
# enumeration of levels would most easily miss.
_c = copy.deepcopy(_SCHEMA)
_c["properties"][EVALUATIONS_KEY]["items"]["properties"]["inclusion_criteria"] \
    ["items"].pop("additionalProperties", None)
check("control 8a: a dropped additionalProperties three levels down is caught",
      len(_strict_defects(_c)) >= 1, True)

# 8b -- an incomplete `required`, the other half of strict mode.
_c = copy.deepcopy(_SCHEMA)
_req = _c["properties"][EVALUATIONS_KEY]["items"]["required"]
if "assessment" in _req:
    _req.remove("assessment")
check("control 8b: a property missing from required is caught",
      len(_strict_defects(_c)) >= 1, True)

# 8c -- additionalProperties present but TRUE. `is not False` rather than a
# truthiness test is what makes this fire.
_c = copy.deepcopy(_SCHEMA)
_c["additionalProperties"] = True
check("control 8c: additionalProperties True is caught",
      len(_strict_defects(_c)), 1)

# 8d -- the walk itself. A non-recursive walk would report one level.
check("control 8d: the walk reaches every depth, not just the root",
      len(schema_object_paths({"type": "object", "properties":
                               {"a": {"type": "object", "properties":
                                      {"b": {"type": "object",
                                             "properties": {}}}}}})), 3)

# 8e -- the field-name mirror. A transcription slip in ONE field name.
_c = copy.deepcopy(_SCHEMA)
_props = _c["properties"][EVALUATIONS_KEY]["items"]["properties"]
_props["nctid"] = _props.pop("nct_id", {"type": "string"})
check("control 8e: a misspelled field name breaks the mirror",
      tuple(_props) == tuple(TRIAL_FIELDS), False)

# 8f -- the field ORDER. Same names, reordered: the set comparison a lazier
# check would use cannot see this.
_c = copy.deepcopy(_SCHEMA)
_reordered = dict(reversed(list(
    _c["properties"][EVALUATIONS_KEY]["items"]["properties"].items())))
check("control 8f: reordered fields break the ORDER mirror",
      tuple(_reordered) == tuple(TRIAL_FIELDS), False)
check("control 8f: ...while a set comparison would NOT have caught it",
      set(_reordered) == set(TRIAL_FIELDS), True)

# 8g -- the vocabularies merged into one six-member enum, which is the exact
# regression this schema exists to prevent: it would let the model write
# "violated" on an inclusion criterion and still satisfy the schema.
_merged = tuple(dict.fromkeys(tuple(INCLUSION_STATUSES) + tuple(EXCLUSION_STATUSES)))
check("control 8g: a merged enum stops being the inclusion vocabulary",
      _merged == tuple(INCLUSION_STATUSES), False)
check("control 8g: ...and would admit an exclusion status on an inclusion arm",
      "violated" in _merged, True)

# 8h -- the shared-member assertion. Two vocabularies sharing nothing.
_no_share = tuple(s for s in INCLUSION_STATUSES if s != "not_evaluable")
check("control 8h: vocabularies sharing no member fail the overlap assertion",
      sorted(set(_no_share) & set(EXCLUSION_STATUSES)) == ["not_evaluable"],
      False)

# 8i -- the AST vocabulary walk, against a copy of the source with the
# normalizer's frozenset changed. Parsed from a STRING, never written to disk.
_eval_src = open(_SOURCES["evaluation.py"], encoding="utf-8").read()
_planted = _eval_src.replace(
    '_INCLUSION_STATUSES = frozenset({"met", "not_met", "not_evaluable"})',
    '_INCLUSION_STATUSES = frozenset({"met", "not_met", "violated"})', 1)
check("non-degeneracy: the plant changed the source text",
      _planted != _eval_src, True)


def _vocab_from_text(text):
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "node_llm_classifier_evaluation":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Assign):
                    for target in inner.targets:
                        if (isinstance(target, ast.Name)
                                and target.id == "_INCLUSION_STATUSES"):
                            return ast.literal_eval(inner.value.args[0])
    return None


check("control 8i: a drifted normalizer vocabulary is caught by the AST read",
      _vocab_from_text(_planted) == set(INCLUSION_STATUSES), False)
check("control 8i: ...and the unplanted source still agrees",
      _vocab_from_text(_eval_src) == set(INCLUSION_STATUSES), True)

# 8j -- the create() keyword check, against an AST copy with the keyword removed.
_ast_copy = ast.parse(_eval_src)
for _node in ast.walk(_ast_copy):
    if isinstance(_node, ast.FunctionDef) and _node.name == "call_matching_model":
        for _inner in ast.walk(_node):
            if (isinstance(_inner, ast.Call)
                    and isinstance(_inner.func, ast.Attribute)
                    and _inner.func.attr == "create"):
                _inner.keywords = [k for k in _inner.keywords
                                   if k.arg != "response_format"]
_stripped_kws = []
for _node in ast.walk(_ast_copy):
    if isinstance(_node, ast.FunctionDef) and _node.name == "call_matching_model":
        for _inner in ast.walk(_node):
            if (isinstance(_inner, ast.Call)
                    and isinstance(_inner.func, ast.Attribute)
                    and _inner.func.attr == "create"):
                _stripped_kws = [k.arg for k in _inner.keywords]
check("control 8j: a create() without response_format is caught",
      "response_format" in _stripped_kws, False)
check("control 8j: non-degeneracy -- the stripped copy still has its other kwargs",
      len(_stripped_kws) > 3, True)

# 8k -- the envelope. A response that agreed about the wrapper and not about the
# contents must NOT be coerced into an empty verdict set.
check("control 8k: a non-list under the key is not silently emptied",
      _unwrap_evaluations({EVALUATIONS_KEY: "NCT1"}), None)
check("control 8k: ...whereas a genuine empty list IS a result",
      _unwrap_evaluations({EVALUATIONS_KEY: []}), [])

# 8l -- the refusal reading. A response with no refusal attribute at all must
# take the ordinary path; this is the getattr default, and the ordinary stub
# above already exercises it, so the control is the inverse: content=None with
# NO refusal must NOT be reported as a refusal.
_none_content, _ = run_stage5(None)
check("control 8l: content=None with no refusal is a parse error, not a refusal",
      (_none_content.get("error", "").startswith(REFUSAL_ERROR_PREFIX),
       _none_content.get("error", "").startswith("GPT-4o JSON parse error")),
      (False, True))

# 8m -- the derived key-set comparison. A refusal return missing one of the
# parse-error keys must be caught.
_short = {k: v for k, v in _ref_result.items()
          if k != "llm_classifier_prompt_sha256"}
check("control 8m: a refusal return missing a documented key is caught",
      sorted(_parse_keys - set(_short)), ["llm_classifier_prompt_sha256"])

# 8n -- the router. The flag under a FALSY value must not terminate: a refusal
# is only a refusal when there is one.
check("control 8n: a falsy refusal flag does not hijack the route",
      route_after_llm_classifier({**_ref_state, "llm_classifier_refusal": ""}),
      "llm_classifier_retry")


# ===========================================================================
# SECTION 9 -- nothing in the repository was written
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 9 -- every plant was in memory")
print("=" * 75)

for _name, _path in _SOURCES.items():
    check(f"{_name} is byte-identical to its pre-run state",
          _sha256_of(_path), _SHA_BEFORE[_name])

check("non-degeneracy: the three baseline hashes are distinct",
      len(set(_SHA_BEFORE.values())), 3)
check("non-degeneracy: a hash of a different byte string differs",
      _sha256_of(_SOURCES["response_schema.py"])
      == hashlib.sha256(b"").hexdigest(), False)


# ===========================================================================

print("\n" + "=" * 75)

# --- RELEASE THE PROVIDER PIN, ABOVE THE SUMMARY ---------------------------
#
# ABOVE, NOT BELOW: a release under the results line still decides the exit
# code while being absent from the number the summary printed -- a run that
# reports "0 failed" and exits non-zero. The default-flip pass shipped exactly
# that in three of seven files, which is why the release is one function with
# one caller-visible answer rather than four hand-written lines here.
#
# THE OUTCOME IS RECORDED BEFORE THE RESTORE, so "there was a pin to release"
# cannot be satisfied by a process that never installed one.
_PIN_WHO, _PIN_PREVIOUS, _PIN_RESTORED = _provider_pin.release_openai_arm()
check("[provider pin] the OpenAI pin this file installed was released, and "
      "config.MATCHING_PROVIDER is back to the shipped provider",
      (_PIN_WHO == os.path.basename(__file__), _PIN_PREVIOUS, _PIN_RESTORED,
       _provider_pin.pin_state()),
      (True, _PROVIDER_BEFORE_PIN, True, (None, None)))

print(f"RESULTS: {_RESULTS['passed']} passed, {_RESULTS['failed']} failed")
print("=" * 75)
if _FAILURES:
    print("\nFAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")

sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug  9 2026

@author: ramyalsaffar
"""
