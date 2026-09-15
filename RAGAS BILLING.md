# Ragas request billing

Each judge and embedding wire attempt reserves budget before dispatch. The retry
owner settles the reservation once: a confirmed rejection costs zero, a priced
response costs its reported usage, and a possibly billed failure, cancellation
in flight, or unpriceable response retains the conservative reservation.

The judge explicitly requests `service_tier="default"`, matching its existing
standard-price policy. Conflicting requested tiers refuse before dispatch.
Model-visible messages, models, scoring parameters and output ceilings are unchanged.

## Supported bounds

The current judge is `gpt-5.6-terra`. Its bound uses the 1,050,000-token context
limit, the actual requested output ceiling (128,000 if absent), and the maximum
long-context/cache-write rates. At the stored $2/$12 per-million base rates and
4,096 output tokens, one reservation is **$5.323728**. This is a worst case,
not a prediction of the bill. Unknown models or request shapes refuse.

Embedding bounds use at most 8,192 tokens per input and 300,000 per request.
Both string and token-array inputs are recognized. Only documented embedding
IDs with existing stored prices are admitted; no price rows were added. The
shipped `text-embedding-3-small` reserves **$0.00016384** for one input and at
most **$0.006** for a batch.

Sources verified September 15, 2026: [Terra limits and pricing](https://developers.openai.com/api/docs/models/gpt-5.6-terra),
[embedding limits](https://developers.openai.com/api/reference/resources/embeddings/methods/create),
and [Chat Completions usage fields](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).
The SDK preserves the documented `prompt_tokens_details.cache_write_tokens`
field even though its installed schema does not declare it. Writes are part of
prompt tokens, so pricing adds only their premium to uncached input. Reasoning
is already included in completion tokens. Long-context multipliers apply when
the full prompt exceeds 272,000 tokens.

## Reports and practical limits

- `cost.total_usd` remains measured response cost. `budget_liability_usd` adds
  `unconfirmed_liability_usd`; per-attempt outcomes and unpriced reasons explain
  the difference. Unpriced responses remain available to scoring.
- Missing or inconsistent usage, missing cache-write counts on a partly
  uncached prompt, or an unexpected echoed model/tier retain the **full**
  reservation. Absence of a write count does not prove zero writes.
- Atomic holds can refuse concurrent requests while measured spend remains
  below the cap. This path does not queue for headroom. Small budgets may not
  fit even one judge reservation. No throughput guarantee is made.
- Every settlement checkpoints through the existing run journal. A later
  dispatch first retries pending writes and refuses unresolved pending or
  conflicting records. Resume seeds the same settled liability; optional
  `accounting_basis` metadata labels new entries without reinterpreting old ones.
- **Hard-kill limitation:** open requests have no durable attempt row. A kill
  before settlement or between settlement and a verified checkpoint can lose
  liability. Existing already-dispatched concurrent requests also cannot be
  recalled after a checkpoint fault. No new journal or database was introduced.

Offline regression coverage is in `tests/test_ragas_billing.py`; it drives both
actual wrappers and the real retry owner, including fresh-process journal reads.
