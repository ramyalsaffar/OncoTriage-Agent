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
    response = deps.get_openai_client().embeddings.create(
        model=config.EMBEDDING_MODEL,
        input=text,
        # The STRUCTURED Timeout, so an unreachable host still fails on the
        # SDK's 5s connect phase rather than waiting out the 30s read budget.
        #
        # CALLED, not imported. In the package the structured timeouts are lazy,
        # because building one constructs a throwaway OpenAI client to read the
        # SDK's own default connect phase -- so importing the value would need
        # credentials at import, which is exactly what pass 20c-1 removed from
        # File 03.
        timeout=config.get_embedding_request_timeout(),
    )
    return response.data[0].embedding


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""
