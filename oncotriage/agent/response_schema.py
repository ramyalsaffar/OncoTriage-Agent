"""Stage 5's response schema: the shape the prompt asks for, as an enforceable
JSON Schema for OpenAI Structured Outputs.

WHAT THIS CHANGES AND WHAT IT DOES NOT. It changes ENFORCEMENT, not the
contract. Every field name, every field order and every status vocabulary below
is lifted from the JSON template in ``oncotriage/agent/prompts.py`` Section 5,
which is unchanged and must stay unchanged -- ``tests/test_agent_prompt_version.py``
is the standing guard on that. Nothing here asks the model for anything the
prompt did not already ask for.

WHY IT IS WORTH DOING, stated as the defect it removes. The status vocabularies
are the pipeline's evidence. A misspelled or cross-arm status ("violated" on an
inclusion criterion) is resolved to "not_evaluable" by ``_normalize_arm`` in
``oncotriage/agent/evaluation.py``, which is the right recovery and is still a
LOSS: the criterion the model actually judged is gone, and enough of them gone
turns a rejection into an unevaluable trial. Under an enum the model cannot
spell it wrong -- the decoder will not emit a token outside the vocabulary.

THE ROOT IS AN OBJECT, AND SINCE PROMPT_VERSION 1.2.0 THE PROMPT SAYS SO.

    OpenAI Structured Outputs requires the ROOT of a ``json_schema`` to be an
    object; an array root is rejected by the API. So the schema wraps the array
    in a one-key object and the model emits ``{"evaluations": [...]}``. The
    wrapper is forced by the API, not chosen.

    Section 5 asked for a bare array through versions 1.0.0 and 1.1.0, which was
    prose the decoder simply overrode. 1.2.0 rewrote its three array statements
    and its JSON template to describe the object envelope, so the prompt and the
    schema now state one contract. The prompt still does not CAUSE the shape --
    the schema does -- but a reader of either is no longer told something false.

    ``oncotriage/agent/evaluation.py`` accepts BOTH shapes -- see
    ``_unwrap_evaluations`` there -- so a bare array (an old fixture, a run with
    the response format somehow absent) parses exactly as it did before. That
    tolerance is deliberately KEPT: it is what makes the response format a
    change to enforcement rather than to the parser.

THE TWO CRITERION VOCABULARIES ARE TWO SCHEMA DEFINITIONS, NOT ONE SHARED ONE.
Section 1 of the prompt states they are disjoint and non-interchangeable, and
the whole value of the enum is lost if the schema offers the union: a model
constrained to six statuses can still write "violated" on an inclusion
criterion, which is exactly the error being removed. So an inclusion criterion
and an exclusion criterion are separate object definitions differing only in
their ``status`` enum.

THE VOCABULARIES ARE DECLARED HERE AND ARE ALSO WRITTEN INSIDE
``node_llm_classifier_evaluation`` as ``_INCLUSION_STATUSES`` /
``_EXCLUSION_STATUSES``. That is two spellings of one fact, which is the shape
this project removes on sight (CROSS_ENCODER_MODEL, BM25_SPARSE_MODEL_NAME).
They are NOT consolidated here, deliberately: the node's copies are function
locals inside a 1,000-line function and hoisting them is a refactor of the
normalizer, which the pass that added this file promised not to touch. What
stands in for consolidation is a CHECK rather than a comment --
``tests/test_agent_structured_outputs.py`` section 3 reads both frozensets out
of the shipped function BY AST and requires them to equal the enums below, so
the two cannot drift silently. Consolidating them into one module-level
vocabulary is the second-ranked follow-up.

NOTHING HERE READS A FILE, OPENS A CLIENT OR CALLS A MODEL. Every function is
pure and every constant is a literal, so importing this module is free -- which
``tests/test_package_invariants.py`` section 2 requires of every package module
and section 2h requires to have a reader.
"""

from typing import Dict, List, Tuple

from oncotriage.agent.state import TRIAL_VERDICTS


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The field names, mirroring prompts.py Section 5
# ---------------------------------------------------------------------------
#
# THIS ORDER IS DESCRIPTIVE, NOT PRESCRIPTIVE, AND THE DIFFERENCE IS THE WHOLE
# POINT OF THE COMMENT.
#
# The live probe (2026-08-09, gpt-5.6-terra, strict json_schema, one call,
# $0.002400) came back with the trial object's keys in STRICTLY ALPHABETICAL
# order -- not in the schema's `properties` order, and not in the order the
# prompt then commanded. The key SET was exactly right and every enum was in
# vocabulary; only the order moved.
#
# (measured with the field still named `explanation`, which sorted between
# exclusion_criteria and inclusion_criteria; the rename to `assessment` is what
# moved it to the front, which is the entire reason for the name)
#
# So NOTHING IN THIS FILE CAN PIN EMISSION ORDER. Writing the tuple below
# alphabetically does not cause the alphabetical output -- the decoder does that
# on its own -- it just stops three statements of one fact from disagreeing:
# this tuple, the JSON template in prompts.py, and _trial_schema()'s properties
# dict all now read in the order that is actually produced. Section 2 of
# tests/test_agent_structured_outputs.py compares all three, and
# `reasoning_order_regression` in oncotriage/agent/evaluation.py watches the
# only consequence that matters -- assessment before eligible -- on the bytes of
# every response, because alphabetical emission is observed behaviour of the
# current model and not a documented API guarantee.
#
# THE CONSEQUENCE THAT IS NOT COSMETIC, and it survives: the raw response text
# has a different shape than it had before Structured Outputs, so
# llm_classifier_raw_response and any digest built over it move. Fixtures were
# already dark pending re-capture, so nothing on disk regressed -- but the
# re-capture inherits this, and phase three's classifier measurement must
# attribute what it sees to the enum AND to the reordering, not to the enum
# alone.
#
# ``tests/test_agent_structured_outputs.py`` section 2 parses the JSON template
# out of the RENDERED system prompt and compares these tuples against it
# element by element, so the mirror is measured rather than asserted in a
# comment. Hand-transcribing a moved literal is how pass 20f-4 shipped
# `#2ecc71` where the original had `#2ca02c`.
TRIAL_FIELDS: Tuple[str, ...] = (
    "assessment",
    "eligible",
    "exclusion_criteria",
    "inclusion_criteria",
    "match_score",
    "nct_id",
    "trial_number",
)

CRITERION_FIELDS: Tuple[str, ...] = (
    "criterion",
    "patient_value",
    "status",
)

# ``trial_number`` and ``match_score`` ARE IN THE SCHEMA EVEN THOUGH BOTH ARE
# OVERWRITTEN DOWNSTREAM. The model's trial_number is replaced by the rank
# position in ``oncotriage/agent/terminal.py`` and its match_score is recomputed
# over applicable criteria by ``_record_score`` / ``_record_zero_score`` on
# every branch, so neither value is read as sent.
#
# They are here anyway, and omitting them would have been a behaviour change
# rather than a tidy-up: strict mode requires `required` to name every property
# and `additionalProperties: false` to forbid the rest, so a schema without them
# makes it IMPOSSIBLE for the model to emit the two fields the prompt's own
# template shows it emitting. That changes the response text, and therefore
# llm_classifier_raw_response, every fixture digest built over it, and the
# prompt's claim about its own output format -- for no gain.


# ---------------------------------------------------------------------------
# The three vocabularies
# ---------------------------------------------------------------------------
#
# Section 1 of the system prompt, verbatim and in its stated order. The two
# criterion vocabularies overlap in exactly one member, "not_evaluable", and are
# otherwise disjoint; the test asserts both halves of that, because "disjoint"
# alone is satisfied by two vocabularies that share nothing INCLUDING the member
# they are supposed to share.
INCLUSION_STATUSES: Tuple[str, ...] = ("met", "not_met", "not_evaluable")
EXCLUSION_STATUSES: Tuple[str, ...] = ("not_violated", "violated", "not_evaluable")

# The trial-level vocabulary is NOT re-declared here. It lives in
# oncotriage/agent/state.py, where ``normalize_trial_verdict`` -- the one
# normalizer both Stage 5 and node_finalize call -- already reads it. A second
# spelling of it in this file is the exact drift this module's own docstring
# argues against for the criterion vocabularies.
TRIAL_VERDICT_ENUM: Tuple[str, ...] = tuple(TRIAL_VERDICTS)


# The key the array hangs off in the wrapper object, and the schema's name as
# the API records it. Named rather than written inline because
# ``_unwrap_evaluations`` in oncotriage/agent/evaluation.py reads the same key
# back off the response, and a literal in two files is a literal that drifts.
EVALUATIONS_KEY = "evaluations"
RESPONSE_SCHEMA_NAME = "trial_evaluations"


#------------------------------------------------------------------------------


def _criterion_schema(statuses: Tuple[str, ...]) -> Dict:
    """One criterion object, with the status enum for its arm.

    Called twice -- once per arm -- rather than shared with a six-member enum;
    see the module docstring for why the union would defeat the point.
    """
    return {
        "type": "object",
        "properties": {
            "criterion": {"type": "string"},
            "patient_value": {"type": "string"},
            "status": {"type": "string", "enum": list(statuses)},
        },
        # Strict mode requires both of these on every object, at every depth,
        # and requires `required` to name every declared property. There is no
        # optional field anywhere in this schema, which is a constraint of the
        # API rather than a modelling choice -- and it costs nothing here,
        # because the prompt's template already shows every field present on
        # every object.
        "required": list(CRITERION_FIELDS),
        "additionalProperties": False,
    }


def _trial_schema() -> Dict:
    """One trial's verdict block."""
    return {
        "type": "object",
        # IN TRIAL_FIELDS ORDER, which is the order the decoder emits. The
        # schema does not IMPOSE that order -- see the ordering block above --
        # so writing them this way buys agreement between three statements of
        # one fact (the prompt's template, TRIAL_FIELDS, and this dict) rather
        # than any behaviour. Section 2 of
        # tests/test_agent_structured_outputs.py compares all three.
        "properties": {
            # Emitted first, and that is the whole reason for its name: the
            # model writes its reasoning before it writes the verdict.
            "assessment": {"type": "string"},
            "eligible": {"type": "string", "enum": list(TRIAL_VERDICT_ENUM)},
            "exclusion_criteria": {
                "type": "array",
                "items": _criterion_schema(EXCLUSION_STATUSES),
            },
            "inclusion_criteria": {
                "type": "array",
                "items": _criterion_schema(INCLUSION_STATUSES),
            },
            # The prompt says "match_score: always 0.0". A number rather than a
            # const, because the schema's job here is the shape: the value is
            # recomputed by _record_score over applicable criteria and the
            # model's figure is never read. A `const` would additionally be
            # unsupported by strict mode's subset of JSON Schema.
            "match_score": {"type": "number"},
            "nct_id": {"type": "string"},
            "trial_number": {"type": "integer"},
        },
        "required": list(TRIAL_FIELDS),
        "additionalProperties": False,
    }


def build_response_schema() -> Dict:
    """The whole JSON Schema, root object included.

    A FUNCTION RATHER THAN A MODULE CONSTANT, and the reason is the same one
    that makes ``MATCH_TIERS`` a hazard in the dashboard: a dict at module
    scope is shared mutable state, and this one is handed to an SDK that is
    free to do what it likes with it. Building it per call costs a handful of
    dict literals and cannot be mutated by a caller into something a later
    caller sends.
    """
    return {
        "type": "object",
        "properties": {
            EVALUATIONS_KEY: {
                "type": "array",
                "items": _trial_schema(),
            },
        },
        "required": [EVALUATIONS_KEY],
        "additionalProperties": False,
    }


def build_response_format() -> Dict:
    """The ``response_format`` argument for the Stage 5 ``create()`` call.

    ``strict: True`` is the whole point: without it the schema is a hint the
    model may ignore, which is the state Stage 5 was already in via the prompt.
    With it, the API constrains decoding and a response outside the schema is
    not merely unlikely but unrepresentable -- the only two ways out are a
    REFUSAL (handled on its own path in evaluation.py) and a LENGTH truncation
    (handled by the existing split path, which is why that path is not dead).
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": RESPONSE_SCHEMA_NAME,
            "strict": True,
            "schema": build_response_schema(),
        },
    }


def schema_object_paths(schema: Dict) -> List[Tuple[str, Dict]]:
    """Every object node in a schema, as ``(json_pointer, node)`` pairs.

    Exists for the invariant test, which has to assert ``additionalProperties:
    False`` and complete ``required`` at EVERY level rather than at the levels
    somebody remembered to list. A walk cannot quietly stop covering a nesting
    depth the way an enumeration can; ``oncotriage/api/server.py``'s four
    endpoints were invisible to a top-level walk for exactly that reason.

    Kept here rather than in the test so the walk is over the same structure
    the caller sends, and so a future reader of this module can enumerate it
    without re-deriving the traversal.
    """
    found: List[Tuple[str, Dict]] = []

    def _walk(node, pointer: str) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                found.append((pointer or "/", node))
            for key, value in node.items():
                _walk(value, f"{pointer}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                _walk(value, f"{pointer}/{index}")

    _walk(schema, "")
    return found


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug  9 2026

@author: ramyalsaffar
"""
