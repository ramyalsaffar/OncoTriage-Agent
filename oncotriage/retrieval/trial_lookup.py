# Trial lookup by NCT ID
########################

"""Fetch ONE indexed trial by its NCT ID. Read only.

WHY THIS MODULE EXISTS AT ALL, stated first because the MCP pass was told to
find an existing function and call it rather than write one.

THERE WAS NO SUCH FUNCTION. The whole package was searched for a public entry
point that answers "give me the trial with this NCT ID", and what exists is:

  * ``oncotriage/agent/retrieval.py`` lines 606-640 -- a ``scroll`` with a
    ``should`` filter over ``nct_id``, written INLINE inside
    ``node_hybrid_retrieval`` as a backfill for trials that were ranked into
    the fusion pool but whose payload the search response did not carry. It is
    a private half of a pipeline stage, it is batched over a list the stage
    computed, and it is unreachable without invoking the stage.
  * ``oncotriage/retrieval/indexer.py`` line 148 -- a payload index created on
    ``nct_id`` whose comment says it is "required for scroll/filter operations
    (e.g., fetching trials by nct_id)". The index exists. The fetch does not.
  * ``oncotriage/retrieval/index_validator.py`` -- reads ``nct_id`` off scrolled
    points to validate their FORMAT, never to select one.

So the choice was: expose a private slice of a LangGraph node, retype the
``scroll`` inside the MCP server, or put the read where a reader belongs. The
first two are both the thing this project has removed twice already -- one job
with several construction sites (``SparseTextEmbedding`` before pass 20c-3a,
``ncbi/MedCPT-Cross-Encoder`` before pass 20f-2) -- and the failure mode is the
same each time: nothing raises when the copies disagree, the query just stops
finding things. This is the third construction site and it is the only one.

WHICH CLIENT SEAM, AND WHY IT IS THE AGENT'S. ``oncotriage.retrieval`` normally
takes its client from ``oncotriage.config``, deliberately: an index BUILD or a
BACKUP must not be redirected by a stub somebody installed for an agent test.
``index_validator`` is the standing exception, and its argument is written at
its imports -- the question it answers is "is this index healthy FOR THE AGENT
TO QUERY", so it must be answered about whatever client the agent would use.
This module is the second file with that argument and it is the same argument:
a caller asking "what does the index hold for NCT01234567" is asking about the
collection the matching pipeline retrieves from, and if a harness has pointed
the agent somewhere else then that somewhere else is the honest answer. It is
also what makes this module testable without a network.

IT RAISES RATHER THAN RETURNING AN EMPTY ANSWER FOR A TRANSPORT FAILURE, and
that asymmetry is the whole point of the module. "No trial with that ID is
indexed" and "the server could not be reached" are both, at the Qdrant API, an
empty list of points -- and a function that returned ``found: False`` for both
would be the exact defect ``oncotriage/agent/readiness.py`` was written to
remove one layer up. A caller that wants the second case as data asks
``probe_index`` first; this function assumes the index has already been found
usable and treats a failure after that as a failure.

IMPORTING THIS MODULE OPENS NOTHING. ``deps.get_qdrant_client()`` is called
inside the function, on the seam, exactly as everywhere else.
"""

import re

from oncotriage.agent import deps
from oncotriage.config import COLLECTION_NAME
from oncotriage.retrieval.index_validator import NCT_ID_PATTERN
from oncotriage.utils import qdrant_retry


#------------------------------------------------------------------------------


# ===========================================================================
# THE PAYLOAD CONTRACT
# ===========================================================================
#
# READ OFF THE WRITER, NOT OFF A MEMORY OF IT. oncotriage/retrieval/indexer.py
# builds every point's payload at one place (the `payload={...}` literal in
# `index_trials_to_qdrant`) and it carries exactly five keys:
#
#     nct_id  title  phase  bm25_text  full_trial_json
#
# `full_trial_json` is the whole scraped trial dict -- title, brief_summary,
# detailed_description, phase, study_type, enrollment, conditions, keywords,
# interventions, the eligibility sub-dict, locations, overall_contact,
# last_update, and the structured stage/histology requirements the extraction
# layer added at index time.
#
# `bm25_text` IS DELIBERATELY NOT RETURNED. It is the tokenizer's input, not a
# fact about the trial: a flattened concatenation built by
# `create_trial_embedding_text` for the sparse encoder to chew on. Handing it to
# a caller that asked for a trial would be handing back an implementation
# detail of retrieval, and it roughly doubles the payload size for a field no
# consumer can interpret. `REQUIRED_PAYLOAD_FIELDS` in index_validator.py is the
# list that says all five must be PRESENT; that is a different question from
# which of them a reader should be handed.

_RETURNED_PAYLOAD_FIELDS = ("nct_id", "title", "phase", "full_trial_json")
"""What ``lookup_trial`` copies out of a point's payload. See above for why
``bm25_text`` is excluded."""


_NCT_ID_RE = re.compile(NCT_ID_PATTERN)
"""Compiled once. The pattern itself is imported from
``oncotriage/retrieval/index_validator.py`` rather than retyped -- it is the
same claim about the same identifiers, and the validator is the module that
already enforces it over every indexed point."""


class TrialLookupError(RuntimeError):
    """The index could not be asked, so the answer is unknown rather than 'no'.

    A ``RuntimeError`` subclass and deliberately NOT a ``ValueError``: a caller
    that wrote ``except ValueError`` around the ID validation below must not
    also swallow a transport failure, because the two demand opposite responses
    -- fix your input, versus fix your cluster.

    Carries ``collection`` so a report can name what was asked without
    re-resolving it.
    """

    def __init__(self, message, collection=None):
        super().__init__(message)
        self.collection = collection


#------------------------------------------------------------------------------


# ===========================================================================
# THE LOOKUP
# ===========================================================================

def normalize_nct_id(nct_id) -> str:
    """Upper-case and strip an NCT ID, or raise ``ValueError`` saying why.

    Separate from ``lookup_trial`` because the validation is the half a caller
    can perform without a network, and an MCP tool wants to reject
    ``"nct-123"`` without opening a client to do it.

    ``NCT`` identifiers are eight digits (``NCT00000102``), and the leading
    zeros are part of the identifier rather than formatting -- ``NCT102`` is not
    a shorter spelling of anything. So a bare integer is refused rather than
    zero-padded into a guess: padding would turn a typo into a confident lookup
    of an unrelated trial.

    Raises:
        ValueError: with the offending value and the expected shape.
    """
    if not isinstance(nct_id, str):
        raise ValueError(
            f"NCT ID must be a string, got {type(nct_id).__name__}. "
            f"Expected the form NCT01234567.")

    cleaned = nct_id.strip().upper()

    if not cleaned:
        raise ValueError("NCT ID is empty. Expected the form NCT01234567.")

    if not _NCT_ID_RE.match(cleaned):
        raise ValueError(
            f"{nct_id!r} is not a well-formed NCT ID. Expected 'NCT' followed "
            f"by exactly 8 digits, e.g. NCT01234567. The leading zeros are part "
            f"of the identifier and are not supplied for you.")

    return cleaned


def lookup_trial(nct_id, client=None, collection=None) -> dict:
    """Return the indexed trial with this NCT ID, or report that there is none.

    Args:
        nct_id:     An NCT identifier. Case and surrounding whitespace are
                    normalized; anything else is refused.
        client:     Qdrant client. Defaults to ``deps.get_qdrant_client()`` --
                    the AGENT's seam. See the module docstring for why.
        collection: Defaults to ``config.COLLECTION_NAME``, which is an ALIAS.
                    Qdrant resolves aliases on ``scroll`` exactly as it does on
                    ``search``, so the caller gets whatever collection the alias
                    currently points at -- which is the same collection the
                    matching pipeline retrieved from, and the only answer worth
                    giving.

    Returns:
        ``{"found": bool, "nct_id": str, "collection": str, "trial": dict|None}``
        where ``trial`` carries ``_RETURNED_PAYLOAD_FIELDS``.

    Raises:
        ValueError:        the ID is not well formed. Nothing was asked.
        TrialLookupError:  the index could not be asked, or answered with a
                           point whose payload is missing. NOT the same as
                           ``found: False`` -- see the module docstring.

    ``limit=1`` AND NOT MORE. ``nct_id`` is unique per trial in a well-formed
    index and ``index_validator``'s duplicate check is what enforces that; this
    function is a reader and asking for a second point in order to re-audit an
    invariant another module owns would be two round trips to report a fault
    that is not this call's business.
    """
    cleaned = normalize_nct_id(nct_id)

    if client is None:
        client = deps.get_qdrant_client()
    if collection is None:
        collection = COLLECTION_NAME

    # The filter is a plain dict rather than qdrant_client.models.Filter, which
    # is what oncotriage/agent/retrieval.py's backfill already does at the only
    # other nct_id scroll in the project. Keeping the two spellings the same
    # matters more than either spelling does: they address the same payload
    # index, and a reader comparing them should not have to translate.
    scroll_filter = {"must": [{"key": "nct_id", "match": {"value": cleaned}}]}

    @qdrant_retry
    def _scroll():
        return client.scroll(
            collection_name=collection,
            scroll_filter=scroll_filter,
            limit=1,
            with_payload=True,
            with_vectors=False,
            timeout=20,
        )

    # NOT WRAPPED IN try/except. Every exception qdrant_retry does not absorb --
    # auth failure, a missing collection, a malformed filter -- reaches the
    # caller as itself. Converting them here would mean this function decides
    # what a transport failure means, and it has less information than any of
    # its callers about whether that is fatal. The project's standing rule is
    # that nothing is caught without being re-raised or counted; the honest way
    # to satisfy it in a leaf reader is to catch nothing.
    points, _next_offset = _scroll()

    if not points:
        return {"found": False, "nct_id": cleaned,
                "collection": collection, "trial": None}

    payload = getattr(points[0], "payload", None)
    if not payload:
        # The server returned a point and no payload. That is not "no such
        # trial" -- it is an index whose points cannot answer the question the
        # payload index exists to answer, and a caller told `found: False` would
        # go and index a trial that is already there.
        raise TrialLookupError(
            f"Qdrant returned a point for {cleaned} in {collection!r} with no "
            f"payload. The point exists but carries none of "
            f"{list(_RETURNED_PAYLOAD_FIELDS)}; run "
            f"'python \"12- RAG Trial Indexer Validator.py\"' to check index "
            f"health.",
            collection=collection)

    trial = {field: payload.get(field) for field in _RETURNED_PAYLOAD_FIELDS}

    return {"found": True, "nct_id": cleaned,
            "collection": collection, "trial": trial}


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
