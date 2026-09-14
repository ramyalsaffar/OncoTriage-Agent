"""The two model calls: the MedCPT cross-encoder and the OpenAI embedding.

Item 20c, pass 2c: "13- LangGraph Agent.py" lines 437-520.

TWO CHANGES, both forced by the split and both visible in the ast.unparse diff:

  * ``medcpt_score_pairs`` reaches the tokenizer and the model through
    ``oncotriage.agent.deps`` instead of through two module-level globals that
    File 13 bound at exec time. The models are now loaded on first use.
  * ``get_embedding`` reaches the OpenAI client through ``deps`` and resolves
    its structured timeout by CALLING ``config.get_embedding_request_timeout()``
    rather than reading an ``EMBEDDING_REQUEST_TIMEOUT`` name. That constant is
    lazy in the package -- building it constructs a throwaway OpenAI client to
    read the SDK's own default connect phase -- so importing it at module scope
    would need credentials at import, which is the defect pass 20c-1 removed
    from File 03 and pass 20c-2b removed from paths.

``score_pairs`` IS THE DISPATCHER, and the reason it exists is worth stating.
Files 45 and 46 hook the whole ``(query, trial_texts) -> scores`` function,
because the fixtures record SCORES; replaying at the model level would mean
fabricating a logits tensor and a matching tokenizer output. So MEDCPT_SCORER is
an override key, and its DEFAULT cannot live in ``deps`` -- ``models`` imports
``deps``, and the reverse edge would be a cycle. The dispatch lives here, beside
the default, and every caller inside the agent uses ``score_pairs``.
"""

from typing import List

from oncotriage import config
from oncotriage import spend
from oncotriage.agent import deps


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Cross-encoder scoring seam
# ---------------------------------------------------------------------------

def medcpt_score_pairs(query: str, trial_texts: List[str]) -> "np.ndarray":
    """Score one query against every trial text with the MedCPT cross-encoder.

    Lifted out of node_cross_encoder_rerank unchanged so the model call is a
    named function rather than a block inside a loop. Two callers need it to
    be one: the reranking loop, and a recording harness that has to capture
    every (query, trial_texts) -> scores pair and serve them back without a
    model (45-/46- Fixture Capture/Replay).

    Returns a 1-D float array, one score per trial text, in input order.
    """
    pairs = [[query, trial_text] for trial_text in trial_texts]

    # Both halves through the seam, resolved once per call and LOADED ON FIRST
    # USE. File 13 bound them as module globals at exec() time, so all twelve
    # files that chain it paid ~110 MB and tens of seconds just by being read --
    # including the six that never score a pair.
    medcpt_tokenizer = deps.get_medcpt_tokenizer()
    medcpt_model = deps.get_medcpt_model()

    # `import torch` IS INSIDE THIS FUNCTION, deliberately, and it is exempt
    # from the package's no-deferred-import rule for the same reason
    # `import icd10` inside _build_icd10_cancer_sets() is: the rule covers
    # oncotriage-to-oncotriage edges, which are the ones that form cycles.
    #
    # At module scope it would make `import oncotriage.agent.models` -- which
    # retrieval imports, which the FastAPI server imports -- pull in torch and
    # everything torch pulls in. Measured: torch's own import chain reaches
    # dill, which OPENS /dev/null at import time, so it is not merely slow, it
    # breaks the claim that importing an agent module reads no file. The only
    # code here that needs torch is this function, and this function cannot run
    # without a MedCPT model anyway.
    import torch

    with torch.no_grad():
        encoded = medcpt_tokenizer(
            pairs,
            truncation=True,
            padding=True,
            return_tensors="pt",
            # THE LIMIT IS THE CHECKPOINT'S, so it is config's and not a
            # literal here (see config.CROSS_ENCODER_MAX_LENGTH). `truncation`
            # above means transformers will do exactly what this number says
            # without complaint, so a number that stopped matching the
            # checkpoint would quietly feed the cross-encoder less of every
            # trial and only the ranking would say so.
            max_length=config.CROSS_ENCODER_MAX_LENGTH,
        )
        return (
            medcpt_model(**encoded)
            .logits.squeeze(dim=1)
            .detach()
            .cpu()
            .numpy()
        )


# ---------------------------------------------------------------------------
# The scorer seam
# ---------------------------------------------------------------------------

def score_pairs(query: str, trial_texts: List[str]):
    """medcpt_score_pairs, unless a harness installed deps.MEDCPT_SCORER.

    EVERY CALLER INSIDE THE AGENT USES THIS, not medcpt_score_pairs directly.

    Why the whole function is the seam rather than the model behind it: Files 45
    and 46 record and replay SCORES -- one float per trial text, in input order,
    keyed by the query and a sha256 of the texts. Replaying at the model level
    would mean fabricating a logits tensor of the right shape and a tokenizer
    output to go with it, which is a second implementation of the thing under
    test. Replacing the function is one hook and cannot drift.

    Why the dispatch is HERE and not in deps: deps owns the override registry
    and the key, but its default lives in this module, and deps must not import
    models -- models imports deps for the tokenizer and the model, and the
    reverse edge would be a cycle. Every other override key resolves its default
    inside deps.

    The override is called with the same two arguments and must return the same
    shape: a 1-D float array, one score per trial text, in input order.
    """
    override = deps.get_override(deps.MEDCPT_SCORER)
    if override is not deps.UNSET:
        return override(query, trial_texts)
    return medcpt_score_pairs(query, trial_texts)


# ---------------------------------------------------------------------------
# Embedding Helper (self-contained, no dependency on RAG Indexer)
# ---------------------------------------------------------------------------

# THE EMBEDDING RESERVATION'S UPPER BOUND (P1b). Facts about the provider's API,
# inline as named constants per the project rule, with their source.
#
# THE SOURCE. The OpenAI Python SDK installed here (openai 1.99.9), whose request
# docstrings are generated from OpenAI's API specification, documents ``input``
# of ``embeddings.create`` as: "The input must not exceed the max input tokens
# for the model (8192 tokens for all embedding models), cannot be an empty
# string ... In addition to the per-input token limit, all embedding models
# enforce a maximum of 300,000 tokens summed across all inputs in a single
# request." OpenAI's embeddings guide lists "Max input 8192" for
# text-embedding-3-small (read 2026-09-13).
#
# WHY A PROVIDER LIMIT AND NOT THE TEXT. The previous reservation was
# ``len(text) / PROVIDER_RESERVATION_CHARS_PER_TOKEN + 1``, an ESTIMATE: a
# tokenizer can emit more tokens than characters / 3 (digits, rare scripts,
# byte-fallback), so a response could be priced above its reservation, and a
# settlement that then failed left the durable row -- read at the reservation --
# below what the live ledger charged. The documented limit is a bound on the
# provider's OWN token count, so it holds whatever the tokenizer, the Unicode
# content or any per-request overhead is: a successful response to one string
# input reports at most 8,192 prompt tokens, and any input the endpoint accepts
# at most 300,000. A UTF-8 byte count would also bound a byte-level BPE's token
# count, but only on the unverified premise that the endpoint adds no tokens of
# its own; the documented limit needs no such premise.
#
# WHAT IT DOES NOT COVER, STATED. (1) A provider that bills beyond its own
# documented limit. (2) Pricing: the reservation is priced at the REQUESTED
# model, and a response is priced at the model it ECHOES; an echo naming a
# model with a higher rate can exceed the reservation. Both are caught by
# ``spend.AttemptLiability``'s settlement-discrepancy rule rather than by this
# bound. (3) The token count is not the whole cost only if the price table
# grows an output rate for embeddings; ``get_embedding`` passes 0 output.
#
# THE COST OF THE BOUND: 8,192 tokens at $0.02 per 1M is $0.00016384 per
# reservation, charged only while an attempt is unresolved or when it fails.
# This reservation feeds no pacer.

EMBEDDING_MAX_INPUT_TOKENS_PER_INPUT = {"text-embedding-3-small": 8192}
"""Documented maximum tokens of ONE input string, per embedding model. A model
absent here has no established bound and is refused before dispatch."""

EMBEDDING_MAX_INPUT_TOKENS_PER_REQUEST = 300_000
"""Documented maximum tokens summed across all inputs of one request, for all
embedding models. The bound for any input that is not a single string."""


class EmbeddingReservationUnbounded(RuntimeError):
    """No sound upper bound on an embedding request's billed tokens could be
    established, so it was NOT dispatched (P1b). A ``RuntimeError`` and not a
    ``ValueError``, on ``UnknownModelPricingError``'s footing; Stage 2's dense
    channel records it and degrades to BM25-only, as for any channel failure."""


def embedding_reservation_input_tokens(text, model) -> int:
    """The input-token upper bound to reserve for one embedding request. RAISES
    ``EmbeddingReservationUnbounded`` when the model has no documented bound.

    One ``str`` input: the model's documented per-input maximum, independent of
    the string's length or content. Anything else the request may carry (a list
    of strings, token arrays): the documented per-request maximum. The request
    itself is never altered here -- this decides only what is reserved.
    """
    try:
        per_input = EMBEDDING_MAX_INPUT_TOKENS_PER_INPUT.get(model)
    except TypeError:
        per_input = None
    if (isinstance(per_input, bool) or not isinstance(per_input, int)
            or per_input <= 0):
        raise EmbeddingReservationUnbounded(
            f"embedding model {model!r} has no documented maximum input token "
            f"count in EMBEDDING_MAX_INPUT_TOKENS_PER_INPUT, so no upper bound "
            f"on what the request is billed can be reserved; it was not "
            f"dispatched")
    if isinstance(text, str):
        return per_input
    return max(per_input, EMBEDDING_MAX_INPUT_TOKENS_PER_REQUEST)


def get_embedding(text: str) -> List[float]:
    """Generate embedding for text using OpenAI.

    Defined here so the agent file is fully self-contained.
    The RAG Indexer (08) has its own copy used at indexing time.
    This copy is used at inference time only.

    ONE RETRY MECHANISM, NOT TWO. This function used to carry

        @retry(reraise=True, stop=stop_after_attempt(5),
               wait=wait_exponential(multiplier=1, min=2, max=60),
               retry=retry_if_exception_type((RateLimitError,
                     InternalServerError, APIConnectionError)))

    on top of the SDK's own retries. APITimeoutError SUBCLASSES
    APIConnectionError, so a timeout was retried by both: up to 5 x 2 = 10
    attempts for a single embedding, a number nobody chose. The decorator was
    removed in item 29d and the SDK retry (OPENAI_SDK_MAX_RETRIES, File 03)
    kept, because it is the only one that can be scoped without breaking the
    fixture harness, and because it honours Retry-After on a 429 where blind
    exponential backoff cannot. The full argument and the trade-off are in File
    03's budget reconciliation.

    Attempts now: 1 + OPENAI_SDK_MAX_RETRIES = 2. Worst case ~68s.

    NOT SILENTLY LESS ROBUST. The SDK retries the same conditions this
    decorator did -- connection errors, 408/409/429, 5xx -- so what was lost is
    attempt COUNT and backoff CEILING, not coverage of a failure class. A
    persistent failure now raises out of this function after 2 attempts instead
    of 10, which reaches Stage 2's channel accounting sooner and is recorded
    there rather than absorbed by a five-minute retry storm nobody sees.

    BOUNDED, on its own budget. EMBEDDING_REQUEST_TIMEOUT_SECONDS is a separate
    constant from MATCHING_REQUEST_TIMEOUT_SECONDS on purpose: 300s is sized
    for a request that generates thousands of tokens and this one generates
    none. File 03 records how the value was arrived at, and states plainly that
    it comes from the call's SHAPE rather than from a measurement, because no
    embedding latency has ever been measured here.
    """
    # ── THE SPEND GATE ────────────────────────────────────────────────────
    #
    # STAGE 2's DENSE CHANNEL IS A BILLED CALL, ONCE PER PATIENT, AND UNTIL THE
    # SPEND-COVERAGE PASS IT WAS INVISIBLE TO THE CAP. The gate instrumented
    # Stage 5 and the module docstring said so; this call sits in the same
    # process, in the same pipeline, on the same patient, and spent money the
    # budget could not see.
    #
    # IT IS CENTS AND IT IS GATED ANYWAY. text-embedding-3-small is $0.02 per
    # million input tokens and a query is a few dozen, so this path is four
    # orders of magnitude below Stage 5's -- which is an argument about how
    # much a hole leaks, not about whether it is one. The rule this pass is
    # built on is that a billed path is gated or is argued at
    # `spend.BILLED_SITE_EXEMPTIONS`, and "too small to matter" is not an
    # argument a future edit to `EMBEDDING_MODEL` or to the query builder
    # cannot invalidate without anybody re-reading it.
    #
    # IT RAISES, AND STAGE 2 ALREADY HANDLES THAT CORRECTLY. Each retrieval
    # channel is wrapped in its own `except Exception`, so a refusal here
    # degrades to BM25-only with `CHANNEL_*` recording the loss -- the same
    # accounting an unreachable endpoint produces. That is the right shape: a
    # patient whose budget ran out mid-run gets a recorded degradation rather
    # than a silent full-price dense search.
    # THE RESERVATION'S BOUND IS DECIDED BEFORE ANYTHING ELSE, because a model
    # with no documented bound is refused before the budget gate, the durable
    # reservation or the request (P1b). See embedding_reservation_input_tokens.
    _reserved_tokens = embedding_reservation_input_tokens(
        text, config.EMBEDDING_MODEL)
    spend.require_budget(spend.SPEND_SOURCE_EMBEDDING,
                         "Stage 2's dense retrieval channel")
    # ── THE DURABLE RESERVATION, BEFORE THE REQUEST ────────────────────────
    #
    # A campaign's cumulative billing record (spend.BILLING_RECORD) counts this
    # call too: the embedding is billed in the same budget as Stage 5 and is on
    # no inference row, so a resumed campaign could not otherwise see it. The
    # reservation is the PROVIDER'S DOCUMENTED MAXIMUM for one request of this
    # shape (P1b) -- not an estimate from the text's length, which a tokenizer
    # can exceed -- and a response settles at its real usage. With no sink
    # installed this is a no-op. A reservation that cannot be persisted RAISES,
    # and Stage 2's channel handler degrades to BM25-only exactly as for an
    # unreachable endpoint -- nothing is dispatched.
    #
    # A FAILED REQUEST IS CHARGED AT THE RESERVATION IN BOTH LEDGERS (the
    # billing closure pass), because nothing here can say the provider did not
    # bill it. It used to settle at the reservation DURABLY while the in-process
    # ledger charged nothing, so a live process admitted spending a resume would
    # refuse. `AttemptLiability.resolve` charges the ledger and settles the row
    # from one number; see spend.attempt_liability.
    _reservation = spend.begin_billed_attempt(
        spend.SPEND_SOURCE_EMBEDDING, config.EMBEDDING_MODEL, _reserved_tokens,
        0, where="Stage 2's dense retrieval channel")
    try:
        response = deps.get_openai_client().embeddings.create(
            model=config.EMBEDDING_MODEL,
            input=text,
            # The STRUCTURED Timeout, so an unreachable host still fails on the
            # SDK's 5s connect phase rather than waiting out the 30s read budget.
            #
            # CALLED, not imported. In the package the structured timeouts are
            # lazy, because building one constructs a throwaway OpenAI client to
            # read the SDK's own default connect phase -- so importing the value
            # would need credentials at import, which is exactly what pass 20c-1
            # removed from File 03.
            timeout=config.get_embedding_request_timeout(),
        )
    except Exception:
        _reservation.resolve(spend.BILLING_OUTCOME_POSSIBLY_BILLED)
        raise
    except BaseException:
        _reservation.resolve(spend.BILLING_OUTCOME_ABANDONED)
        raise
    # ── THE CHARGE, IMMEDIATELY AFTER THE RESPONSE ────────────────────────
    #
    # `usage.completion_tokens` DOES NOT EXIST ON AN EMBEDDING RESPONSE and is
    # passed as a literal 0 rather than read with a getattr default. The two
    # spellings price identically today; they differ the day the SDK grows the
    # field, when a getattr would silently start billing output tokens for a
    # call that produces none. The embeddings usage block is
    # `{prompt_tokens, total_tokens}` -- `total_tokens` is deliberately NOT
    # used, because it is the SUM and `get_model_cost` would then charge the
    # input rate against a figure that already includes it.
    #
    # THE MODEL IS THE ECHOED ONE, falling back to the configured id, which is
    # `_Stage5AttemptRecord.response`'s rule one module over: the provider bills
    # what it answered with.
    _usage = getattr(response, "usage", None)
    _reservation.resolve(
        spend.BILLING_OUTCOME_RESPONSE,
        model=getattr(response, "model", None) or config.EMBEDDING_MODEL,
        prompt_tokens=getattr(_usage, "prompt_tokens", None),
        completion_tokens=0)
    return response.data[0].embedding


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""
