# Serving readiness
##################

"""Is this process actually able to answer a match request?

Added by the Docker pass. Two dependencies of the pipeline can be MISSING while
every other signal says the system is fine, and the failure of each is a wrong
ANSWER rather than an error:

  * **An empty or absent Qdrant collection.** Stage 2 asks Qdrant for the top
    75 sparse and top 100 dense hits. A collection with no points answers both,
    successfully, with an empty list. The graph's conditional edge then routes
    to ``node_no_candidates`` and the API returns 200 with
    ``"no eligible trials found"`` -- which is the same output a patient with a
    genuinely unmatchable diagnosis produces, from a run that cost nothing and
    proved nothing. Nothing raises, no counter moves, and the stored inference
    row is well-formed. This is the exact shape of defect the project's
    "no silent recovery" rule exists to remove, and until the compose stack
    started using its own (empty) Qdrant service it was unreachable in practice
    because the cloud index was always populated.

  * **A missing MeSH lookup.** Item 11a already made ``load_mesh_filter()``
    RAISE rather than disable the Stage 4 site filter silently, and that raise is
    not weakened here -- it is SURFACED EARLIER. Before this module the raise
    happened inside the first ``POST /match``, after the container had reported
    healthy, so a stack could be green and unusable at the same time.

WHY A SEPARATE MODULE RATHER THAN A CHECK INSIDE EACH STAGE. Two callers want
the same facts at different times and with different policies, and writing the
policy into the probe would have forced one of them to be wrong:

  * ``agent/retrieval.py`` asks PER REQUEST, and must not turn a transient
    Qdrant hiccup into a total outage -- retrieval failure is already recorded
    per channel, and a probe that blocked on its own failure would be a new
    single point of failure in front of machinery designed to degrade.
  * ``api/server.py`` asks AT STARTUP, where it can afford to demand proof:
    "I could not check" is not "it is fine", and a container that cannot verify
    its index has no business reporting healthy.

So this module answers WHAT IS TRUE and the two callers decide what to do about
it. Both decisions are written at their call sites, not here.

IMPORTING THIS MODULE OPENS NOTHING. Every probe is a function call. The
package-wide rule (``tests/test_package_invariants.py`` section 2) applies here
exactly as it does everywhere else.
"""

from collections import Counter

from oncotriage import config
from oncotriage.agent import deps
from oncotriage.observability import get_logger
from oncotriage.registries import mesh
from oncotriage.settings import DegradedDependencyError


log = get_logger(__name__)


#------------------------------------------------------------------------------


# ===========================================================================
# THE CLOSED VOCABULARY OF INDEX STATES
# ===========================================================================
#
# A caller may branch on these exhaustively, which is why they are named
# constants and a tuple rather than four bare strings sprinkled across two
# modules. ``INDEX_STATES`` is READ -- ``probe_index`` asserts its own return
# value is in it, and the API reports the state string verbatim -- because a
# declared-and-never-read vocabulary is the ``PASSWORD_SOURCE_ARGUMENT`` shape
# this project has shipped once already.

INDEX_POPULATED = "populated"
"""The collection resolves and holds at least one point."""

INDEX_EMPTY = "empty"
"""The collection resolves and holds ZERO points. Retrieval would succeed and
return nothing, which is why this is a failure rather than a result."""

INDEX_ABSENT = "absent"
"""Neither a collection nor an alias of that name exists. The server answered;
it said there is nothing there. This is what a clean `docker compose up -v`
leaves behind, and it is deliberately NOT folded into ``empty``: the operator's
next command differs (create-and-index versus index)."""

INDEX_UNVERIFIABLE = "unverifiable"
"""The server could not be asked at all -- transport error, auth failure, or a
stand-in client that does not implement the probe. NOT evidence of emptiness,
and treated differently from it by both callers."""

INDEX_STATES = (INDEX_POPULATED, INDEX_EMPTY, INDEX_ABSENT, INDEX_UNVERIFIABLE)
"""Closed set. A caller may branch on it exhaustively."""

_INDEX_BLOCKING = (INDEX_EMPTY, INDEX_ABSENT)
"""The two states in which retrieval would return an empty list and report
success. These are what ``require_populated_index`` raises on."""


INDEX_PROBE_FAILURES = Counter()
"""Times the index could not be verified, keyed ``{ExceptionType}``.

Module-level, following ``PARTIAL_DATE_DEGRADATIONS`` in ``oncotriage/utils.py``
and the four counters item 11a added. It exists because
``require_populated_index`` CONTINUES on an unverifiable probe, and this
project's standing rule is that no exception is recovered from without being
recorded. A run whose retrieval looked thin can be checked against this counter
afterwards.
"""


class EmptyIndexError(RuntimeError):
    """The Qdrant collection the agent is about to query holds no points.

    A ``RuntimeError`` subclass and deliberately NOT a ``ValueError``, a
    ``KeyError`` or anything qdrant-client raises: Stage 2's own channel
    machinery wraps its Qdrant calls in ``except Exception`` and records the
    channel as failed, and an exception raised from inside that region would be
    swallowed into "one channel was unavailable" -- the report that hides
    exactly this. It is raised BEFORE any channel runs, for that reason.

    NOT a ``DegradedDependencyError``. That class's message tells the operator
    to set ``ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES`` to proceed anyway, and
    nothing honours that variable for the index -- a message naming a switch
    that does not exist for the failure it describes is worse than no message.

    Carries ``state`` (one of ``INDEX_STATES``), ``collection`` and ``endpoint``
    so a caller can branch and a report can name the server without re-probing.
    """

    def __init__(self, message, state=None, collection=None, endpoint=None):
        super().__init__(message)
        self.state = state
        self.collection = collection
        self.endpoint = endpoint


#------------------------------------------------------------------------------


# ===========================================================================
# THE INDEX PROBE
# ===========================================================================

def probe_index(client=None, collection=None) -> dict:
    """Ask the server what is in the collection. Returns a verdict dict.

    Args:
        client:     Qdrant client. Defaults to ``deps.get_qdrant_client()`` --
                    the AGENT's seam, not ``config.get_qdrant_client()``,
                    because the question is "can the agent retrieve", and a
                    stub installed for an agent test must be the thing this
                    answers about.
        collection: Defaults to ``config.COLLECTION_NAME``, which is an ALIAS.
                    ``collection_exists`` resolves aliases -- verified against a
                    live server rather than assumed -- so the alias is asked
                    directly and no alias-resolution round trip is added.

    Returns:
        ``{"state", "points", "collection", "endpoint", "error"}``. ``points``
        is None unless the state is ``populated`` or ``empty``; ``error`` is the
        ``repr`` of the exception when the state is ``unverifiable`` and None
        otherwise.

    TWO CALLS, NOT ONE, and the second is what makes the first worth making.
    ``count`` on a missing collection raises ``UnexpectedResponse`` 404 --
    measured -- which is indistinguishable at the call site from a transport
    failure unless the response is parsed, and parsing another library's error
    text is exactly the substring-for-a-definition mistake this project has
    already paid for twice. ``collection_exists`` answers the question in one
    boolean, so ``absent`` is a positive finding rather than a guess about an
    exception.

    RAISES NOTHING. Every failure comes back as a state. The callers raise.
    """
    if client is None:
        client = deps.get_qdrant_client()
    if collection is None:
        collection = config.COLLECTION_NAME

    endpoint = _endpoint_for_report()

    def _verdict(state, points=None, error=None):
        assert state in INDEX_STATES, f"unknown index state {state!r}"
        return {"state": state, "points": points, "collection": collection,
                "endpoint": endpoint, "error": error}

    try:
        exists = client.collection_exists(collection)
    except Exception as exc:
        INDEX_PROBE_FAILURES[type(exc).__name__] += 1
        return _verdict(INDEX_UNVERIFIABLE, error=repr(exc))

    if not exists:
        return _verdict(INDEX_ABSENT)

    try:
        points = client.count(collection, exact=True).count
    except Exception as exc:
        INDEX_PROBE_FAILURES[type(exc).__name__] += 1
        return _verdict(INDEX_UNVERIFIABLE, error=repr(exc))

    return _verdict(INDEX_POPULATED if points > 0 else INDEX_EMPTY,
                    points=points)


def _endpoint_for_report():
    """The endpoint string for a diagnostic, or a note saying why there is none.

    Reads ``config.qdrant_endpoint_sources()``, which resolves strings and opens
    nothing. Wrapped because a diagnostic must not be the thing that fails: a
    missing .env makes ``get_keys()`` raise, and a probe that died while
    building the message for another failure would replace a precise report with
    an unrelated traceback. The failure is RECORDED, not swallowed -- it comes
    back as the reported endpoint, so the operator sees it.
    """
    try:
        s = config.qdrant_endpoint_sources()
    except Exception as exc:
        return f"<endpoint unresolved: {type(exc).__name__}: {exc}>"
    return f"{s['url']} (from {s['url_source']})"


#------------------------------------------------------------------------------


# ===========================================================================
# THE PER-REQUEST GATE
# ===========================================================================
#
# CACHING, AND WHY IT IS ONE-WAY. A ``populated`` verdict is cached and never
# re-probed: an index does not empty itself mid-process, and paying a `count`
# round trip per patient across a twelve-thread batch run is a real cost for a
# question already answered. Every OTHER verdict is NOT cached, and that
# asymmetry is deliberate -- it is what lets a container recover on its own when
# the operator populates the index, instead of reporting `empty` until somebody
# thinks to restart it. The cost of re-probing is paid only on the path that is
# about to raise anyway, or by a caller already running against a stand-in.

_INDEX_VERIFIED = False


def reset_index_probe_cache():
    """Forget a cached ``populated`` verdict. For tests and for a re-check.

    Exists because the cache is process-wide and a test that installs a stub
    client after a real probe has run would otherwise be answered by the
    previous process state rather than by its own stub.
    """
    global _INDEX_VERIFIED
    _INDEX_VERIFIED = False


def require_populated_index(client=None, collection=None) -> dict:
    """Raise ``EmptyIndexError`` if retrieval would return nothing and say OK.

    Called at the top of Stage 2, before any channel runs.

    Policy, stated here because this is the call site that applies it:

      * ``populated``   -> return the verdict, and never probe again.
      * ``empty`` /
        ``absent``      -> RAISE. Both mean Qdrant will answer every query with
                           an empty list, and an empty list is what a legitimate
                           no-match looks like.
      * ``unverifiable`` -> COUNT, PRINT, and CONTINUE. A probe that could not
                           run is not evidence that the index is empty, and
                           blocking on it would put a new hard dependency in
                           front of machinery that already records per-channel
                           failure. The counter is ``INDEX_PROBE_FAILURES``.

    Returns:
        The verdict dict, so a caller can log ``points`` without re-probing.
    """
    global _INDEX_VERIFIED

    if _INDEX_VERIFIED:
        return {"state": INDEX_POPULATED, "points": None,
                "collection": collection or config.COLLECTION_NAME,
                "endpoint": None, "error": None}

    verdict = probe_index(client=client, collection=collection)

    if verdict["state"] == INDEX_POPULATED:
        _INDEX_VERIFIED = True
        return verdict

    if verdict["state"] in _INDEX_BLOCKING:
        raise EmptyIndexError(_empty_index_message(verdict),
                              state=verdict["state"],
                              collection=verdict["collection"],
                              endpoint=verdict["endpoint"])

    # unverifiable: recorded above by probe_index, announced here, not fatal.
    log.warning("could not verify the trial index before retrieval; "
                "continuing, retrieval failures are recorded per channel",
                event="index_probe_unverifiable", status=verdict["state"],
                collection=verdict["collection"], endpoint=verdict["endpoint"],
                error_message=str(verdict["error"]))
    return verdict


def _empty_index_message(verdict) -> str:
    """The operator-facing message for a blocking verdict.

    Written once and used by both callers so the API's 503 body and the
    pipeline's exception cannot drift apart. It names the endpoint (there are
    two possible ones now) and the command that fixes it.
    """
    if verdict["state"] == INDEX_ABSENT:
        what = (f"Qdrant has no collection or alias named "
                f"{verdict['collection']!r}.")
    else:
        what = (f"The Qdrant collection {verdict['collection']!r} holds 0 "
                f"points.")

    return (
        f"Trial index unusable: {what}\n"
        f"  Endpoint: {verdict['endpoint']}\n"
        f"  Every retrieval against it returns an empty list, which is "
        f"indistinguishable from a patient who genuinely matches no trial — so "
        f"this raises instead of producing a well-formed 'no eligible trials' "
        f"result that nothing could later tell apart from a real one.\n"
        f"  To build the index into the endpoint above:\n"
        f"      ONCOTRIAGE_QDRANT_URL='<that endpoint>' "
        f"python \"11- RAG Trial Indexer.py\" --mode direct\n"
        f"  To point this process at an index that already exists, set "
        f"ONCOTRIAGE_QDRANT_URL (and ONCOTRIAGE_QDRANT_API_KEY if that server "
        f"needs one)."
    )


#------------------------------------------------------------------------------


# ===========================================================================
# THE STARTUP GATE
# ===========================================================================

READY = "ready"
"""Every required dependency answered and is usable."""

NOT_READY = "not_ready"
"""At least one required dependency is missing or unusable. The process is
running and can report WHY; it cannot serve a match request."""


def serving_readiness() -> dict:
    """Can this process answer POST /match? Returns a report, raises nothing.

    Two required dependencies, checked in the order the pipeline would meet
    them:

      1. **The MeSH site-relevance lookups**, through
         ``deps.get_mesh_filter()``. Item 11a made the absence of the two core
         JSONs raise ``DegradedDependencyError`` naming both files and the
         rebuild command; that message is CARRIED VERBATIM into this report
         rather than re-written, because it is already the best description of
         the failure and two copies would drift. The exception is caught here
         and RECORDED in the report -- not swallowed: an unready server that
         still answers ``/health`` with the reason is strictly more diagnosable
         than one that dies at startup and leaves only a log.

         ``ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES`` still works exactly as it
         did: with it set, ``get_mesh_filter()`` returns None instead of
         raising, and this check passes. That is the operator's documented
         decision to run degraded, and this probe does not second-guess it.

      2. **The trial index**, through ``probe_index``. ``unverifiable`` counts
         as NOT ready here -- unlike at request time -- because a startup probe
         can afford to demand proof, and a container that cannot reach its own
         vector database has nothing to offer a client.

    Returns:
        ``{"status": READY|NOT_READY, "checks": [ {name, ok, detail}, ... ]}``.
    """
    checks = []

    # --- 1. MeSH -----------------------------------------------------------
    try:
        mesh_filter = deps.get_mesh_filter()
    except DegradedDependencyError as exc:
        checks.append({
            "name": "mesh_site_filter",
            "ok": False,
            "detail": str(exc),
        })
    else:
        if mesh_filter is None:
            # Reachable only through an installed override or through
            # ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES. Both are decisions somebody
            # made on purpose, and item 11a's counters already record the
            # second; saying so here keeps /health honest without contradicting
            # the operator.
            degraded = dict(mesh.MESH_FILTER_DEGRADATIONS)
            checks.append({
                "name": "mesh_site_filter",
                "ok": True,
                "detail": (f"DISABLED by a deliberate override or by "
                           f"ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES; Stage 4's "
                           f"cancer site filter will not run. Recorded "
                           f"degradations: {degraded or 'none'}"),
            })
        else:
            checks.append({
                "name": "mesh_site_filter",
                "ok": True,
                "detail": "loaded",
            })

    # --- 2. The trial index ------------------------------------------------
    verdict = probe_index()
    if verdict["state"] == INDEX_POPULATED:
        checks.append({
            "name": "trial_index",
            "ok": True,
            "detail": (f"{verdict['points']} points in "
                       f"{verdict['collection']!r} at {verdict['endpoint']}"),
        })
    elif verdict["state"] in _INDEX_BLOCKING:
        checks.append({
            "name": "trial_index",
            "ok": False,
            "detail": _empty_index_message(verdict),
        })
    else:
        checks.append({
            "name": "trial_index",
            "ok": False,
            "detail": (f"could not be verified ({verdict['error']}) at "
                       f"{verdict['endpoint']}. A startup probe treats "
                       f"'cannot tell' as 'not ready': see "
                       f"oncotriage/agent/readiness.py for why the per-request "
                       f"gate does not."),
        })

    status = READY if all(c["ok"] for c in checks) else NOT_READY
    return {"status": status, "checks": checks}


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
