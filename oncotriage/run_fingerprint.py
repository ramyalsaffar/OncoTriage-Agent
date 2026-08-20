# Run Configuration Fingerprint
###############################

"""What configuration produced a partial artifact, and whether this run may
continue it.

THE PROBLEM THIS EXISTS FOR. Three paid harnesses persist partial state and
resume from it: ``oncotriage/evaluation/run_harness.py`` (a manifest plus one
JSON per patient), ``oncotriage/batch/runner.py`` (a set of completed filename
stems) and ``oncotriage/ablation/study.py`` (a set of completed
``(config, patient)`` pairs). Every one of them recorded WHAT was done and none
of them recorded WHAT IT WAS DONE UNDER. So a resume after a prompt edit, a
model change or an index rebuild silently produced one artifact holding two
configurations' results, with nothing in it saying so -- and every mean,
every rate and every comparison computed over it is a number about nothing.

The fix is one stamp, compared before anything is skipped and before anything
is spent. It is deliberately ONE MODULE with three callers rather than three
comparisons, on this project's own repeated finding: two copies of a rule that
must agree are two copies that can disagree, and here disagreement means one
harness refuses a resume while another accepts the same one.

WHAT IS IN THE STAMP, AND WHY EACH FIELD IS THERE
-------------------------------------------------
    llm_classifier_prompt_version   a prompt edit moves every Stage 5 verdict.
                                    It is the one input that is neither a
                                    tunable, a model nor an index.
    matching_model_configured       the model REQUESTED. What answered is
                                    recorded per call and can legitimately be a
                                    dated snapshot of the same alias; what was
                                    asked for is what a resume would ask for
                                    again.
    qdrant_collection               the RESOLVED backing collection, never the
                                    alias. The alias is a constant -- that is
                                    what an alias is for -- so a gate on it is a
                                    gate that can never fire.
    collection_points               how many points were in that collection.
                                    See COLLECTION IDENTITY below: this is the
                                    weaker half of the pair, deliberately, and
                                    it is what catches a re-index in place.
    data_snapshot_date              patient age is computed against it, age
                                    drives Stage 4's age filter, and the filter
                                    decides which trials survive to be judged.
                                    Two eras of this constant are two different
                                    cohorts wearing one cohort's name.

WHAT IS DELIBERATELY NOT IN IT, each with its reason. ``collection_alias``: an
alias may be repointed at the SAME backing collection, in which case the two
runs are comparable and a gate on the alias name would refuse them; the
resolved name is the fact. ``age_reference_date``: a pure function of
``data_snapshot_date``, so gating both would be one fact counted twice, and the
one that is a configured constant is the one to gate. The tunables:
``fixture_replay.py:diff_tunables()`` already reports a tunable change on every
replay, and the three consumers here have no equivalent -- recorded as an open
limitation below rather than half-implemented here.

COLLECTION IDENTITY IS NAME PLUS POINT COUNT, AND THAT IS WEAKER THAN THE
FIXTURE HARNESS'S GATE
-------------------------------------------------------------------------
``oncotriage/fixtures/capture.py:compute_collection_digest()`` scrolls every
point's ``nct_id`` and hashes the sorted set, which catches a same-count
content swap. This does not. It compares the resolved NAME (which catches the
weekly alias swap, the overwhelmingly common case) and the POINT COUNT (which
catches a partial re-index and a deleted-trial run). What it misses is a
collection rebuilt in place to exactly the same size with different contents.

The gate is the weaker one on purpose and the reason is layering, not cost.
``compute_collection_digest`` lives in ``oncotriage/fixtures/capture.py``, and
two of this module's three consumers may not import it: ``oncotriage/batch``
and ``oncotriage/ablation`` are production and experiment code, and making
either depend on the characterization-fixture harness -- which imports the
agent, the parser and the storage layer -- is the wrong direction for a
dependency. Moving the digest to a neutral module is the right end state and it
is a refactor with its own equivalence proof; mixing a relocation into a gate
pass is what makes an equivalence proof stop meaning anything.

SO THE LIMITATION IS STATED WHEREVER THE GATE'S ANSWER IS: in
``COLLECTION_IDENTITY``, which every consumer writes into its own artifact, and
in the refusal text. A weaker gate that says it is weaker is a gate; a weaker
gate that does not is a claim.

RESOLUTION IS CACHED FOR THE PROCESS, AND THAT IS A CORRECTNESS ARGUMENT
------------------------------------------------------------------------
``current()`` resolves the collection and counts its points ONCE and caches the
answer. ``oncotriage/batch/runner.py`` writes its checkpoint after every
patient, and a stamp resolved per write would be tens of thousands of live
round trips -- but the reason it is cached is not the saving. A run is ONE
configuration. If the weekly alias swap lands mid-run, a per-write stamp would
put two collections into one checkpoint and the file would then refuse itself;
a per-run stamp records the collection the run STARTED against, which is what
every patient in it was actually matched against for as long as the process's
BM25 index and clients were built.

``clear_cache()`` is the seam, called by each consumer at the top of its run
so two runs in one process each resolve, and by tests.

NOTHING HERE RAISES ON A FAILURE TO RESOLVE. ``current()`` records ``UNKNOWN``
and counts it; ``compare()`` then answers ``FP_UNRESOLVED``, and the CALLER
decides. That is ``probe_index``'s arrangement and for its reason: this is a
diagnostic, and a diagnostic that raises replaces the finding with a traceback.

WHAT IMPORTING THIS MODULE DOES. Nothing: no client, no model, no path
resolution, no network. Every resolution is inside ``current()``.
"""

import threading
from collections import Counter

from oncotriage import config
from oncotriage import utils
from oncotriage.agent import readiness
from oncotriage.agent.prompts import PROMPT_VERSION
from oncotriage.observability import get_logger


log = get_logger(__name__)


#------------------------------------------------------------------------------


# ===========================================================================
# CONSTANTS
# ===========================================================================

FINGERPRINT_VERSION = 1
"""Bumped when the FIELD SET changes, never when a field's value changes.

It is what separates "this artifact was stamped by a writer that recorded
fewer facts than this one gates on" from "these two configurations differ".
Without it a stamp predating a new field would compare that field's absence
against a live value and report a configuration change that never happened --
a true refusal for a false reason, which is worse than no reason.
"""

UNKNOWN = "unknown"
"""What a field reads when it could not be established.

The same documented sentinel ``oncotriage/tracking.py`` uses, and never an
omitted key: an absent key and a key reading UNKNOWN are different findings,
and only the second is a statement that somebody tried.
"""

COLLECTION_IDENTITY = "name+points"
"""What the collection comparison actually compares, as a value a consumer
writes into its own artifact. See COLLECTION IDENTITY in the module docstring.
Read by every consumer and by the refusal text, so it cannot go stale silently.
"""

# The gated fields, in the order a refusal lists them. CLOSED: `current()`
# produces exactly these keys and `compare()` walks exactly these keys, so a
# field added to one and not the other fails the round trip rather than being
# recorded and never gated -- the shape that lets a stamp look complete while
# gating on less than it records.
FINGERPRINT_FIELDS = (
    "llm_classifier_prompt_version",
    "matching_model_configured",
    "qdrant_collection",
    "collection_points",
    "data_snapshot_date",
)

# How compare() answered. A CLOSED set, for the reason
# fixtures.capture.RESUME_OUTCOMES and agent.state.TRIAL_VERDICTS are closed: a
# caller may branch on it exhaustively, and a new member has to be a change
# every caller sees rather than a string falling through an if/elif chain.
#
# EVERY MEMBER BUT THE FIRST IS A REFUSAL, and each names a DIFFERENT
# remediation -- which is the whole reason they are not one "mismatch" member.
# Clearing the artifact is right for FP_CHANGED and FP_ABSENT and wrong for
# FP_UNRESOLVED, where the artifact is fine and the endpoint is not.
FP_MATCH = "match"                      # every gated field agrees: resume
FP_ABSENT = "unknown_provenance"        # nothing recorded, or a stamp with no version
FP_VERSION = "fingerprint_version"      # a different stamp SHAPE
FP_CHANGED = "configuration_changed"    # a gated field genuinely differs
FP_UNRESOLVED = "current_unresolved"    # THIS run's configuration is not establishable
FP_OUTCOMES = (FP_MATCH, FP_ABSENT, FP_VERSION, FP_CHANGED, FP_UNRESOLVED)

NOT_RECORDED = "<not recorded>"
"""What a disagreement line prints for a field the stored stamp does not carry.

``oncotriage/evaluation/ragas_harness.py:identity_disagreement`` established
the rule this module follows: a missing key in the recorded identity is a
disagreement, never a pass. A partial artifact that does not state what it ran
under cannot be shown to have run under this.
"""

FINGERPRINT_DEGRADATIONS = Counter()
"""Why the CURRENT configuration could not be fully established, keyed by field
and exception type (``qdrant_collection:ConnectionError``,
``collection_points:unverifiable``).

Non-zero means some ``current()`` call in this process produced an UNKNOWN, and
therefore that every resume gate consulted afterwards refused with
FP_UNRESOLVED. Registered in ``oncotriage/degradation.py``'s spec table rather
than through ``register()``: this module does not import that one, so the
primary route applies.
"""


_RESOLVED = {}
"""The per-process cache. Keyed 'fingerprint'. See the module docstring."""

_RESOLVE_LOCK = threading.RLock()
"""Locked for the same reason ``oncotriage/agent/deps.py:_resolve`` is, and the
call site that forces it is real: ``oncotriage/batch/runner.py`` saves its
checkpoint from ``_on_done``, a done-CALLBACK, which runs on a WORKER thread --
so ``MAX_WORKERS`` threads can reach an unwarmed cache at once. ``if k not in d:
d[k] = build()`` is two atomic operations and one non-atomic sequence, and here
the cost of losing that race is not a wasted round trip but TWO resolutions that
can straddle an alias swap and disagree.

The lock is the second line of defence. The first is that every consumer warms
this cache on its main thread before its pool exists."""


#------------------------------------------------------------------------------


# ===========================================================================
# TAKING THE STAMP
# ===========================================================================

def clear_cache() -> None:
    """Drop the cached stamp so the next ``current()`` resolves again.

    Called at the top of each consumer's run -- ``clear_write_ledger()``'s
    precedent in the batch runner -- so two runs in one process do not share a
    collection resolution, and by tests.
    """
    with _RESOLVE_LOCK:
        _RESOLVED.pop("fingerprint", None)


def _resolve_collection():
    """``(name, points)`` for the collection this run will query.

    THE POINT COUNT IS TAKEN OF THE RESOLVED NAME, NOT OF THE ALIAS, and that
    is the one thing in this function that is not obvious. Resolving the alias
    and counting it are two round trips, and an alias swap between them would
    stamp collection A with collection B's point count -- a fingerprint that
    was never true of anything. ``probe_index(collection=resolved)`` closes the
    window: whatever name came back is the name that was counted.

    (That failure mode is not hypothetical in this project.
    ``oncotriage/tracking.py:configuration_params`` records the same defect
    found by running: its first version resolved the collection twice and the
    two calls could disagree.)

    Neither half raises. ``resolve_qdrant_collection`` already swallows its own
    failures and falls back to the alias string, so the only thing that can
    escape it is a client that cannot be BUILT; ``probe_index`` raises nothing
    by contract and reports ``unverifiable`` instead.
    """
    try:
        name = utils.resolve_qdrant_collection()
    except Exception as exc:                                   # noqa: BLE001
        FINGERPRINT_DEGRADATIONS[f"qdrant_collection:{type(exc).__name__}"] += 1
        log.warning("the backing collection could not be resolved; this run's "
                    "configuration cannot be fingerprinted",
                    event="fingerprint_degraded", status="degraded",
                    error_type=type(exc).__name__)
        return UNKNOWN, UNKNOWN

    try:
        probe = readiness.probe_index(collection=name)
    except Exception as exc:                                   # noqa: BLE001
        # probe_index's contract is that it raises nothing. This is the belt on
        # the braces: a client that cannot be built raises inside
        # deps.get_qdrant_client() BEFORE the probe's own try blocks are
        # entered, so the contract holds and this branch is still reachable.
        FINGERPRINT_DEGRADATIONS[f"collection_points:{type(exc).__name__}"] += 1
        return name, UNKNOWN

    if probe["points"] is None:
        # `absent` and `unverifiable` both land here. Neither is a count, and
        # neither may be defaulted to 0 -- an empty collection reports 0 and is
        # a MEASUREMENT, while these two are the absence of one.
        FINGERPRINT_DEGRADATIONS[f"collection_points:{probe['state']}"] += 1
        return name, UNKNOWN

    return name, probe["points"]


def current(refresh: bool = False) -> dict:
    """This run's configuration stamp. Resolved once per process, then cached.

    Args:
        refresh: resolve again even if a stamp is cached. For a caller that
            genuinely wants a second reading (a test asserting the cache, or a
            long-lived process that has reconfigured); NOT used by any consumer
            during a run, because a run is one configuration.

    Returns a dict carrying ``fingerprint_version`` plus exactly
    ``FINGERPRINT_FIELDS``. Any field may read ``UNKNOWN``; none is ever
    omitted, and none is ever defaulted to a plausible-looking value.
    """
    with _RESOLVE_LOCK:
        if refresh:
            _RESOLVED.pop("fingerprint", None)
        if "fingerprint" not in _RESOLVED:
            name, points = _resolve_collection()
            _RESOLVED["fingerprint"] = {
                "fingerprint_version": FINGERPRINT_VERSION,
                "llm_classifier_prompt_version": PROMPT_VERSION,
                "matching_model_configured": config.MATCHING_MODEL,
                "qdrant_collection": name,
                "collection_points": points,
                "data_snapshot_date": config.DATA_SNAPSHOT_DATE,
            }
        # A COPY. The consumers put this straight into a JSON payload they then
        # mutate around, and a shared dict would let one consumer's edit reach
        # another's stamp.
        return dict(_RESOLVED["fingerprint"])


def is_resolved(fingerprint: dict) -> bool:
    """Whether every gated field of this stamp was established.

    A stamp carrying UNKNOWN is written down anyway -- the artifact should
    record what was known -- but it can never be shown to AGREE with anything,
    which is what ``compare()`` reads this for.
    """
    return all(fingerprint.get(f) != UNKNOWN for f in FINGERPRINT_FIELDS)


#------------------------------------------------------------------------------


# ===========================================================================
# COMPARING TWO STAMPS
# ===========================================================================

def disagreements(recorded, current_fp: dict) -> list:
    """Every gated field that differs, as ``"field: was -> now"`` lines.

    Empty means they agree. A field the recorded stamp does not carry is a
    DISAGREEMENT and prints as ``<not recorded>`` --
    ``ragas_harness.identity_disagreement``'s rule, and the reason is the same:
    an artifact that does not state what it ran under cannot be shown to have
    run under this. ``recorded`` may be None or any non-dict; both mean the
    same thing and neither raises, because this is only ever called to explain
    a refusal and a formatter that raises while formatting a refusal replaces
    the diagnosis with a traceback.
    """
    if not isinstance(recorded, dict):
        recorded = {}
    changed = []
    for field in FINGERPRINT_FIELDS:
        was = recorded.get(field, NOT_RECORDED)
        now = current_fp.get(field, NOT_RECORDED)
        if was != now:
            changed.append(f"{field}: {was!r} -> {now!r}")
    return changed


def compare(recorded, current_fp: dict) -> tuple:
    """``(outcome, detail)`` -- may this run continue ``recorded``'s artifact?

    ``outcome`` is one of ``FP_OUTCOMES`` and only ``FP_MATCH`` permits a
    resume. ``detail`` is one line for a console, naming every differing field
    rather than the first, because an operator reading a refusal wants the
    whole disagreement.

    THE ORDER OF THE CHECKS IS THE ORDER OF THE DIAGNOSES, and it is chosen so
    that the answer names the thing the operator can act on:

      1. THIS run unestablishable -> FP_UNRESOLVED. Asked first because
         comparing against an UNKNOWN would report every field as changed and
         send an operator to clear a perfectly good checkpoint when the actual
         fault is an unreachable endpoint. It is also the only outcome whose
         remediation is not about the artifact at all.
      2. nothing recorded, or a stamp with no version -> FP_ABSENT. Every
         artifact written before this module existed is here. It is NOT folded
         into FP_CHANGED: "produced by an unknown configuration" and "produced
         by a different one" are different findings, and only the first is
         silent about which fields moved.
      3. a different stamp SHAPE -> FP_VERSION, before any field is compared,
         so a field this version gates and that version never recorded is not
         reported as a configuration change.
      4. fields -> FP_CHANGED or FP_MATCH.
    """
    if not is_resolved(current_fp):
        unknown = [f for f in FINGERPRINT_FIELDS
                   if current_fp.get(f) == UNKNOWN]
        outcome, detail = FP_UNRESOLVED, (
            "this run's own configuration could not be established ("
            + ", ".join(f"{f}={UNKNOWN}" for f in unknown)
            + "), so it cannot be shown to match anything")
    elif not isinstance(recorded, dict) or not recorded:
        outcome, detail = FP_ABSENT, (
            "no configuration fingerprint was recorded, so what produced it is "
            "unknown")
    elif recorded.get("fingerprint_version") is None:
        outcome, detail = FP_ABSENT, (
            "the stored state carries no fingerprint_version: it was written "
            "before configuration fingerprinting existed, so what produced it "
            "is unknown")
    elif recorded.get("fingerprint_version") != FINGERPRINT_VERSION:
        outcome, detail = FP_VERSION, (
            f"fingerprint_version {recorded.get('fingerprint_version')!r} != "
            f"{FINGERPRINT_VERSION!r}: the stored state records a different set "
            f"of facts than this version gates on, so the two cannot be "
            f"compared field by field")
    else:
        changed = disagreements(recorded, current_fp)
        if changed:
            outcome, detail = FP_CHANGED, "; ".join(changed)
        else:
            outcome, detail = FP_MATCH, (
                f"prompt {current_fp['llm_classifier_prompt_version']}, model "
                f"{current_fp['matching_model_configured']}, collection "
                f"{current_fp['qdrant_collection']} "
                f"({current_fp['collection_points']} points), snapshot "
                f"{current_fp['data_snapshot_date']}")

    assert outcome in FP_OUTCOMES, f"unknown fingerprint outcome {outcome!r}"
    return outcome, detail


class ResumeRefusal(RuntimeError):
    """A stored artifact may not be continued by this run.

    A ``RuntimeError`` subclass and deliberately NOT a ``ValueError``, on
    ``oncotriage/utils.py:UnknownModelPricingError``'s precedent: a stray
    ``except ValueError`` around a json parse is exactly what would swallow the
    one thing standing between a misconfigured resume and a wrong artifact.

    ``outcome`` is the ``FP_OUTCOMES`` member, so a caller can branch on the
    finding without parsing the message; the message carries the whole
    diagnosis and the caller's own remediation, already formatted by
    ``refusal_lines``.
    """

    def __init__(self, message, outcome=FP_CHANGED):
        super().__init__(message)
        self.outcome = outcome


def refusal_lines(outcome: str, detail: str, artifact: str,
                  remediation) -> list:
    """The refusal, as the lines every consumer prints. One text, three callers.

    ``remediation`` is the caller's -- clearing a checkpoint, pointing
    ``--output-dir`` elsewhere and passing an override flag are three different
    commands and only the caller knows which. Everything ABOVE it is shared, so
    three harnesses cannot come to describe the same refusal three ways.

    THE COLLECTION LIMITATION IS PRINTED ON EVERY REFUSAL THAT COMPARED
    FIELDS, and only on those. FP_CHANGED is the only outcome that got as far
    as comparing a collection: FP_ABSENT and FP_UNRESOLVED never reached the
    fields, FP_VERSION refuses BEFORE them, and FP_MATCH is not a refusal at
    all -- no caller passes it here, which is why it is not in the test below.
    Stating the limits of a comparison that did not run would be noise
    pretending to be rigour.
    """
    lines = [f"REFUSED ({outcome}): {artifact}", f"    {detail}"]
    if outcome == FP_CHANGED:
        lines.append(f"    collection identity compared as "
                     f"{COLLECTION_IDENTITY}: a collection rebuilt in place to "
                     f"the same point count with different contents would not "
                     f"be detected here")
    lines.extend(f"    {line}" for line in
                 (remediation if isinstance(remediation, (list, tuple))
                  else [remediation]))
    return lines


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug 20 09:00:00 2026

@author: ramyalsaffar
"""
