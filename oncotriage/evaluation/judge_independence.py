"""The judge may not come from the family that produced the text it judges.

WHY THIS MODULE EXISTS, AND IT IS NOT A STYLE RULE. Both judge surfaces in this
project -- ``oncotriage/evaluation/rater.py`` and
``oncotriage/evaluation/ragas_harness.py`` -- state in their own docstrings that
they use a DIFFERENT VENDOR FAMILY from the one that produced the decisions,
and both were WRONG when this module was written. Stage 5 had moved to Claude
Sonnet 4.6 over Bedrock Converse (``config.MATCHING_PROVIDER`` is
``bedrock_anthropic``); both judges still pinned ``claude-sonnet-4-6`` on the
Anthropic API. So the shipped configuration measured FAMILY AGREEMENT while
every docstring, manifest and summary said it measured decision quality, and
nothing anywhere could tell the difference.

That is the exact failure this project's own doctrine names: a claim kept in
prose, contradicted by the code, with no check able to see it. A comment cannot
notice a provider flip three modules away. This can.

**THE UNIT OF COMPARISON IS THE FAMILY, NEVER THE HOSTING PLATFORM.** A Claude
model served by Amazon Bedrock is still an Anthropic model: the weights, the
training data and the failure modes are Anthropic's, and routing the request
through another company's endpoint changes none of them. So
``us.anthropic.claude-sonnet-4-6`` and ``claude-sonnet-4-6`` are ONE family, and
``us.openai.gpt-5.6-terra`` and ``gpt-5.6-terra`` are another. Comparing the
model STRINGS would have called those pairs different and passed the shipped
defect straight through, which is why ``family_of`` exists at all.

TWO LAYERS, AND NEITHER REPLACES THE OTHER.

  LAYER 1 -- ``assert_import_time_independence()``. Called at MODULE SCOPE by
  both judge surfaces, so a same-family CONFIGURATION cannot be imported, let
  alone run. It reads the shipped defaults and fails fast, before argparse,
  before a run directory is resolved, before credentials. What it cannot see is
  a judge model supplied at run time by ``--model``.

  LAYER 2 -- ``require_independent_judge(model, where)``. Called immediately
  before judged spend, on the EFFECTIVE model -- after CLI flags, environment
  and any override have been resolved. What it cannot see is a defect that
  makes the module unimportable, which is layer 1's job and is a better failure
  than a run that gets as far as a credential prompt.

A one-layer design fails in one of two ways. Import-time alone is defeated by
``--model claude-sonnet-4-6``. Call-time alone lets a same-family default sit in
the file indefinitely, discovered only by whoever next runs a paid pass.

**THE OVERRIDE IS EXPLICIT, NAMED, AND RECORDED IN EVERY MANIFEST IT PERMITS.**
``ONCOTRIAGE_ALLOW_SAME_FAMILY_JUDGE`` is not a boolean shrug: it is resolved by
its own function (never ``settings._from_env``, which appends a separator --
the sixth victim's argument, restated), it accepts a CLOSED vocabulary, an
unrecognised value RAISES rather than reading as "off", and every consumer puts
``independence_override`` into the artifact it writes. A same-family run is a
legitimate experiment -- measuring how much of an agreement rate is family
agreement is a real question -- and it must be legible in the output forever,
not just in whoever's shell history set it.

**THIS MODULE IS NOT IN THE PIPELINE'S IMPORT GRAPH AND MUST NOT BE.** It is
imported by the two judge harnesses and by their tests. Stage 5, the batch
runner, the API and the MCP server reach none of it, so a classifier-only run is
completely unaffected by anything here -- which is the requirement, and it is
asserted by ``tests/test_judge_independence.py`` rather than left as an
intention.

IT IMPORTS ``oncotriage.config`` AND NOTHING ELSE FROM THE PROJECT. The
effective classifier is ``config.matching_wire_model()`` -- the ONE function
that answers what is actually sent -- so this guard follows a provider flip for
free rather than carrying a second copy of that resolution.
"""

import os
import re

from oncotriage import config


class JudgeIndependenceError(RuntimeError):
    """The configured judge shares a family with the classifier under audit.

    Deliberately a ``RuntimeError`` subclass and deliberately NOT a
    ``ValueError``: a stray ``except ValueError`` around argument handling must
    not be able to swallow the one thing standing between a circular
    measurement and a published number. ``UnknownModelPricingError``'s argument
    in ``oncotriage/utils.py``, applied to a measurement rather than to a price.
    """


#------------------------------------------------------------------------------
# Families
#------------------------------------------------------------------------------


FAMILY_OPENAI = "openai"
FAMILY_ANTHROPIC = "anthropic"
FAMILY_GOOGLE = "google"
FAMILY_META = "meta"
FAMILY_MISTRAL = "mistral"
FAMILY_COHERE = "cohere"
FAMILY_UNKNOWN = "unknown"

FAMILIES = (FAMILY_OPENAI, FAMILY_ANTHROPIC, FAMILY_GOOGLE, FAMILY_META,
            FAMILY_MISTRAL, FAMILY_COHERE, FAMILY_UNKNOWN)
"""Every family this guard can name. CLOSED, and ``unknown`` is a member.

``unknown`` is a VALUE rather than a gap, and the distinction decides the
guard's behaviour: an unrecognised model id is not evidence of independence, so
it is reported as unknown and treated as NOT PROVEN rather than as PROVEN
DIFFERENT. See ``require_independent_judge``.
"""

# Ordered longest-prefix-first is irrelevant here because every rule is a
# regex over the WHOLE id after the platform prefix is stripped; what matters is
# that the rules are disjoint, which `_assert_family_rules_disjoint` checks
# against a fixture set at import.
_FAMILY_RULES = (
    # OpenAI: gpt-*, o1..o9, chatgpt-*, text-embedding-*, davinci/babbage.
    (FAMILY_OPENAI, re.compile(
        r"^(?:gpt[-.]|chatgpt[-.]|o[1-9](?:[-_.]|$)|text-embedding[-.]|"
        r"davinci|babbage|codex[-.])")),
    # Anthropic: claude-*.
    (FAMILY_ANTHROPIC, re.compile(r"^claude[-.]")),
    # Google: gemini-*, gemma-*, text-bison, palm.
    (FAMILY_GOOGLE, re.compile(r"^(?:gemini[-.]|gemma[-.]|text-bison|palm)")),
    # Meta: llama-*, meta-llama/*.
    (FAMILY_META, re.compile(r"^(?:meta[-.])?llama[-.0-9]")),
    # Mistral: mistral-*, mixtral-*, ministral-*.
    (FAMILY_MISTRAL, re.compile(r"^(?:mi[sx]tral[-.]|ministral[-.])")),
    # Cohere: command-*, embed-english-*.
    (FAMILY_COHERE, re.compile(r"^(?:command[-.]|embed-english|embed-multi)")),
)

_PLATFORM_PREFIXES = ("us.", "eu.", "ap.", "au.", "jp.", "global.",
                      "us-gov.", "bedrock/", "azure/", "vertex_ai/",
                      "openrouter/")
"""Routing prefixes a HOSTING PLATFORM adds, stripped before the family rules.

**THIS IS THE HALF THAT MAKES THE GUARD WORK ON THIS PROJECT.** The shipped
classifier is ``us.anthropic.claude-sonnet-4-6`` -- a Bedrock geographic
cross-Region inference profile. Strip ``us.`` and it is
``anthropic.claude-sonnet-4-6``; strip the vendor segment and it is
``claude-sonnet-4-6``, which is the same family as the Anthropic API's own id
for the same weights. A guard that compared raw strings would report those two
as different models, which is true and irrelevant, and would have passed the
defect this module was written for.

Stripping is ITERATIVE, because ``us.anthropic.`` is two segments and a single
pass would leave ``anthropic.claude-...`` for the vendor step to handle -- which
it does, but only because the vendor step runs after. Both steps are needed and
neither alone is sufficient: ``global.openai.gpt-5.6-terra`` needs both.
"""

_VENDOR_SEGMENTS = {
    "anthropic": FAMILY_ANTHROPIC,
    "openai": FAMILY_OPENAI,
    "google": FAMILY_GOOGLE,
    "meta": FAMILY_META,
    "mistral": FAMILY_MISTRAL,
    "cohere": FAMILY_COHERE,
}
"""A leading ``<vendor>.`` or ``<vendor>/`` segment names the family outright.

Bedrock, Vertex and OpenRouter all put the developer's name in the model id, so
this is the STRONGEST signal available and it is consulted before the pattern
rules. ``us.anthropic.claude-sonnet-4-6`` is answered here, by the word
``anthropic``, rather than by a regex that has to know what a Claude id looks
like on three platforms.
"""


def normalise_model_id(model):
    """Lower-case, whitespace-stripped, platform prefixes removed.

    Returns ``""`` for anything that is not a non-empty string, which
    ``family_of`` then reports as ``unknown`` rather than raising: a judge model
    that is ``None`` is a defect, and the honest report of it is "I cannot name
    this family", which is refused by the caller.
    """
    if not isinstance(model, str):
        return ""
    text = model.strip().lower()
    changed = True
    while changed:
        changed = False
        for prefix in _PLATFORM_PREFIXES:
            if text.startswith(prefix) and len(text) > len(prefix):
                text = text[len(prefix):]
                changed = True
    return text


def family_of(model):
    """The developer family behind a model id, or ``unknown``.

    NEVER RAISES, and that is deliberate. Both callers below need to
    DISTINGUISH "these are the same family" from "I could not tell", and a
    raise here would collapse the second into an exception the caller would
    have to re-classify. The refusal decision lives in
    ``require_independent_judge``, where the two cases get different messages.
    """
    text = normalise_model_id(model)
    if not text:
        return FAMILY_UNKNOWN
    # The vendor segment first: it is a NAME rather than a pattern, so it is
    # the strongest evidence available and it is what answers every Bedrock id.
    head = re.split(r"[./]", text, maxsplit=1)[0]
    if head in _VENDOR_SEGMENTS:
        return _VENDOR_SEGMENTS[head]
    for family, rule in _FAMILY_RULES:
        if rule.match(text):
            return family
    return FAMILY_UNKNOWN


# The rules must not both match one id, or `family_of` would answer by tuple
# order -- an ordering nobody chose, deciding whether a paid measurement is
# circular. Checked at import over a fixture set rather than asserted in prose,
# and a RuntimeError rather than an `assert`, which `python -O` deletes.
_DISJOINTNESS_FIXTURES = (
    "gpt-5.6-terra", "gpt-4o-2024-08-06", "o3-mini", "chatgpt-4o-latest",
    "text-embedding-3-small", "claude-sonnet-4-6", "claude-opus-4-8",
    "gemini-2.5-pro", "gemma-3-27b", "llama-3.3-70b", "meta.llama3-70b",
    "mistral-large-2411", "mixtral-8x22b", "command-r-plus",
)


def _assert_family_rules_disjoint():
    for probe in _DISJOINTNESS_FIXTURES:
        text = normalise_model_id(probe)
        hits = [fam for fam, rule in _FAMILY_RULES if rule.match(text)]
        if len(hits) > 1:
            raise RuntimeError(
                f"_FAMILY_RULES are not disjoint: {probe!r} matches {hits}. "
                f"family_of() would answer by tuple order, which is an "
                f"ordering nobody chose deciding whether a paid measurement "
                f"is circular.")


_assert_family_rules_disjoint()


#------------------------------------------------------------------------------
# The override
#------------------------------------------------------------------------------


ENV_ALLOW_SAME_FAMILY_JUDGE = "ONCOTRIAGE_ALLOW_SAME_FAMILY_JUDGE"

OVERRIDE_TRUE = ("1", "true", "yes", "on")
OVERRIDE_FALSE = ("0", "false", "no", "off", "")
"""The CLOSED vocabulary. Anything else RAISES.

A switch that decides whether a circular measurement may be published must not
itself be tolerant of a value nobody meant: ``ONCOTRIAGE_ALLOW_SAME_FAMILY_JUDGE
= "flase"`` read as "off" is the safe direction and read as "on" is a published
number nobody can defend, and neither is worth the guess.
``settings.resolve_allow_degraded_registries``' rule, applied to a measurement.
"""

OVERRIDE_SOURCE_UNSET = "unset"
OVERRIDE_SOURCE_ENV = "environment"
OVERRIDE_SOURCES = (OVERRIDE_SOURCE_UNSET, OVERRIDE_SOURCE_ENV)


def resolve_same_family_override(environ=None):
    """``(allowed, source, raw)`` -- never a bare bool.

    ``raw`` is carried so a manifest can record the literal an operator typed
    rather than this function's reading of it. ``environ`` is injectable so the
    check can be driven without mutating the process, which is what lets a test
    exercise the raising branch without leaving a variable set for every check
    after it.

    It is deliberately NOT routed through ``oncotriage/settings.py``: that
    module's ``_from_env`` appends ``os.sep`` -- correct for a directory, and
    it would make ``"1"`` read as ``"1/"`` here, so the flag could never match
    again and the override would be silently dead. Sixth victim of that helper,
    after the Airflow password, the inferences DB, the degraded-registry flag,
    the log level and the Bedrock key.
    """
    env = os.environ if environ is None else environ
    raw = env.get(ENV_ALLOW_SAME_FAMILY_JUDGE)
    source = OVERRIDE_SOURCE_UNSET if raw is None else OVERRIDE_SOURCE_ENV
    # THE VOCABULARY IS CLOSED AND THIS IS WHAT MAKES THAT TRUE RATHER THAN
    # DECLARED. `source` reaches every manifest this guard permits a run to
    # write, so a member added here and not to the tuple would put a value in a
    # durable field that no reader of the tuple knows about. Not an `assert`:
    # `python -O` deletes those, and this one guards a field in an artifact.
    if source not in OVERRIDE_SOURCES:
        raise JudgeIndependenceError(
            f"{source!r} is not one of OVERRIDE_SOURCES {OVERRIDE_SOURCES}; a "
            f"source this module can produce but has not declared would reach "
            f"a manifest as a value no reader knows about.")
    if raw is None:
        return False, source, None
    text = str(raw).strip().lower()
    if text in OVERRIDE_TRUE:
        return True, source, raw
    if text in OVERRIDE_FALSE:
        return False, source, raw
    raise JudgeIndependenceError(
        f"{ENV_ALLOW_SAME_FAMILY_JUDGE}={raw!r} is not a value this switch "
        f"understands. Use one of {OVERRIDE_TRUE} to permit a same-family "
        f"judge, or one of {OVERRIDE_FALSE} to forbid it. It is refused rather "
        f"than read as 'off' because a typo deciding whether a circular "
        f"measurement may be published is not a guess worth making.")


#------------------------------------------------------------------------------
# The classifier under audit
#------------------------------------------------------------------------------


def classifier_model():
    """The model id Stage 5 actually sends, whatever the provider.

    ``config.matching_wire_model()`` is the ONE function that answers this --
    ``config.MATCHING_MODEL`` is the OpenAI arm's priced identity and reads
    ``gpt-5.6-terra`` even while the shipped provider is Converse, which is
    exactly the confusion that let the same-family defect ship.

    THE IMPORT IS AT MODULE SCOPE AND WAS DEFERRED INTO THIS FUNCTION IN THE
    FIRST VERSION. Deferring it looked like a virtue -- importing this guard
    would then resolve nothing -- and it violates this project's standing rule
    that no package module may import another from a function body, because a
    deferred import is a dependency no scan of an import block can see.
    ``tests/test_package_invariants.py`` check 1b caught it. The cost of
    hoisting is nil: both consumers of this module already import ``config`` at
    their own module scope, and importing ``config`` opens no client and
    resolves no path.
    """
    return config.matching_wire_model()


def classifier_family():
    return family_of(classifier_model())


#------------------------------------------------------------------------------
# The two layers
#------------------------------------------------------------------------------


VERDICT_INDEPENDENT = "independent"
VERDICT_SAME_FAMILY = "same_family"
VERDICT_UNKNOWN_FAMILY = "unknown_family"
VERDICTS = (VERDICT_INDEPENDENT, VERDICT_SAME_FAMILY, VERDICT_UNKNOWN_FAMILY)
"""CLOSED, and three members rather than a bool, because the remedies differ.

``same_family`` means change the judge. ``unknown_family`` means this guard
cannot name one of the two ids, so the answer is NOT PROVEN and the remedy is a
rule in ``_FAMILY_RULES`` (or ``_VENDOR_SEGMENTS``) -- a different edit in a
different file. Collapsing them into "not independent" would send an operator to
the wrong one, and collapsing them into a bool would lose both.
"""


def assess(judge_model, classifier=None):
    """The full independence record for one (judge, classifier) pair.

    Returns a plain dict, so every caller can drop it straight into a manifest
    without a serialiser. ``classifier`` is injectable for the same reason
    ``environ`` is above: a test must be able to drive the same-family arm
    without flipping ``config.MATCHING_PROVIDER`` for every check after it.
    """
    clf = classifier_model() if classifier is None else classifier
    judge_fam = family_of(judge_model)
    clf_fam = family_of(clf)
    if judge_fam == FAMILY_UNKNOWN or clf_fam == FAMILY_UNKNOWN:
        verdict = VERDICT_UNKNOWN_FAMILY
    elif judge_fam == clf_fam:
        verdict = VERDICT_SAME_FAMILY
    else:
        verdict = VERDICT_INDEPENDENT
    return {
        "judge_model": judge_model,
        "judge_family": judge_fam,
        "classifier_model": clf,
        "classifier_family": clf_fam,
        "verdict": verdict,
    }


def _refusal_text(record, where, override_hint=True):
    if record["verdict"] == VERDICT_SAME_FAMILY:
        why = (f"the judge {record['judge_model']!r} and the classifier "
               f"{record['classifier_model']!r} are BOTH "
               f"{record['judge_family']} models. An agreement rate between "
               f"two models of one family measures family agreement, not "
               f"decision quality, and every number the run publishes would "
               f"say the opposite.")
    else:
        unknown = [name for name, fam in
                   (("judge", record["judge_family"]),
                    ("classifier", record["classifier_family"]))
                   if fam == FAMILY_UNKNOWN]
        why = (f"this guard cannot name the family of the "
               f"{' and '.join(unknown)} model "
               f"({record['judge_model']!r} / "
               f"{record['classifier_model']!r}). An id it cannot classify is "
               f"NOT evidence of independence -- it is an unanswered question, "
               f"and it is refused rather than assumed because the assumption "
               f"is the one that costs money. Add a rule to "
               f"oncotriage/evaluation/judge_independence.py::_FAMILY_RULES "
               f"or _VENDOR_SEGMENTS.")
    tail = ""
    if override_hint:
        tail = (f" To run anyway -- which is a legitimate experiment and is "
                f"recorded in every artifact the run writes -- set "
                f"{ENV_ALLOW_SAME_FAMILY_JUDGE}=1.")
    return f"REFUSED at {where}: {why}{tail}"


def require_independent_judge(judge_model, where, classifier=None,
                              environ=None):
    """LAYER 2. Raise unless the EFFECTIVE pair is cross-family, or overridden.

    Returns the record to put in the manifest, including
    ``independence_override``, so a caller cannot record a clean verdict for a
    run that was only permitted by the switch: the two facts come back from one
    call and are written together.

    **CALLED IMMEDIATELY BEFORE JUDGED SPEND, ON THE EFFECTIVE MODEL.** Not at
    argument parsing, where ``--model`` has been read but a later default could
    still move it, and not after the first request, which is the whole point.
    """
    record = assess(judge_model, classifier=classifier)
    allowed, source, raw = resolve_same_family_override(environ=environ)
    record["independence_override"] = {
        "variable": ENV_ALLOW_SAME_FAMILY_JUDGE,
        "allowed": allowed,
        "source": source,
        "value_as_given": raw,
    }
    record["checked_at"] = where
    if record["verdict"] == VERDICT_INDEPENDENT:
        return record
    if allowed:
        record["override_applied"] = True
        return record
    record["override_applied"] = False
    raise JudgeIndependenceError(_refusal_text(record, where))


def assert_import_time_independence(judge_model, where, classifier=None,
                                    environ=None):
    """LAYER 1. The same question, asked of the SHIPPED DEFAULT, at import.

    A separate function from ``require_independent_judge`` rather than a call to
    it, for two reasons that are about the failure rather than about the check:

      * the message must say the DEFAULT is wrong, not that this run is wrong.
        An operator who has typed nothing needs to be told the file ships
        misconfigured, and pointing them at ``--model`` would be pointing at a
        flag they did not use.
      * it returns nothing. A record built at import would be a module-level
        snapshot of a question whose answer can change before the run -- the
        exact staleness ``require_independent_judge`` is called to avoid -- and
        a manifest that recorded it would be recording the wrong pair whenever
        ``--model`` was given.
    """
    record = assess(judge_model, classifier=classifier)
    if record["verdict"] == VERDICT_INDEPENDENT:
        return
    allowed, _source, _raw = resolve_same_family_override(environ=environ)
    if allowed:
        return
    raise JudgeIndependenceError(
        _refusal_text(record, where)
        + f" This is the module's SHIPPED DEFAULT rather than anything you "
          f"typed: change the default judge model in {where}, or flip the "
          f"classifier, or set the variable above.")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep  8 09:00:00 2026

@author: ramyalsaffar
"""
