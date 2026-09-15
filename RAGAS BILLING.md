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
- Paid startup installs a focused SQLite sink beside the canonical shared spend
  journal (`<journal>.ragas.sqlite3`). Each reservation commits before dispatch;
  settlement replaces it once. New attempts do not also append aggregate journal
  charges. The authoritative `spend_journal.total` path reads the imported legacy
  baseline plus durable attempts. Explicit pre-read `entries` remain the legacy
  analysis API, not a combined durable total.
- A hard kill leaves an unresolved reservation at its full upper bound. Paid
  startup and resume refuse while any such attempt exists, showing its ID and
  amount. `python ragas_run.py --billing-status` reads status without loading a
  run or creating a provider client. There is no automatic resend, refund or
  reconciliation command. Existing bounded retries for observed provider errors
  and metric validation remain unchanged; each wire attempt has a new UUID.
- A local attempt ID is not a provider idempotency key. The store records model,
  reservation parameters, outcome and settled token counts. It does not persist
  full responses or provider request IDs, and cannot retrieve an unknown response.
  Usage/model/tier observed before settlement supports local pricing, not a claim
  of exactly-once provider execution. A crash after receipt but before settlement
  therefore retains the full bound, even if the actual cost was smaller.

## Storage, migration and operation

Stop all old Ragas processes before upgrading. Mixed old/new writers are not
supported. One Ragas process owns a canonical journal/store at a time through a
lifetime OS lock; its async workers still run concurrently. Other processes
refuse rather than sharing stale live accounting. Read-only status remains usable.

On first use, the verified legacy campaign journal baseline is imported once.
Malformed or incomplete history refuses initialization. Existing journal bytes
are preserved. Campaign journal changes after cutover refuse; unrelated valid
budget entries do not add Ragas liability. No timestamp/amount overlap heuristic
is used. The startup identity marker is published and synced before database
creation. A crash during initialization can leave a marker with no valid store;
this intentionally refuses instead of treating history as a fresh budget.

Keep the database, `.ragas.identity`, optional `.ragas.identity.fault`, and
`.ragas.sqlite3.lock` files beside the shared journal on persistent local storage.
Do not delete, replace, move or restore individual members to reset a budget.
Moving a journal and its accounting requires preserving the whole set; changing
the configured journal selects another accounting scope. Deleting the entire set
or restoring a matching old database and marker cannot be detected by this local
mechanism. There is no external witness or distributed-store guarantee.

SQLite uses DELETE journaling and synchronous EXTRA, with fullfsync enabled and
verified on macOS. Directory publication is synced. Durability still depends on
the OS, filesystem and hardware honoring flushes; process-kill tests do not prove
power-loss durability. Two synced transactions per successful attempt impose
latency. Readback uses indexed attempt lookup; admission aggregates amounts in
SQL, and full validation occurs at startup/status. The legacy file is reparsed
when its filesystem identity/size/timestamps change, not on every request.

Integrity discrepancies persist a fault marker and refuse future paid startup.
Operator messages point to this store, not the inference database. Already
submitted requests may still complete after a storage fault; their conservative
reservations remain. Restoring or reconciling uncertain evidence is outside this
item and must not infer zero cost from a missing response.

Offline coverage: `tests/test_ragas_billing.py` and
`tests/test_ragas_durable_recovery.py`, including actual wrapper/retry execution,
precise subprocess kills, fresh-process reads, write failures/lost acknowledgments,
owner locking, migration and startup refusal. Tests use synthetic stores only.
