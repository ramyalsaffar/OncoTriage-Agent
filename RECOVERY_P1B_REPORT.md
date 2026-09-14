# Billing closure recovery: P1b (embedding reservation bound; missing and conflicting settlement parity)

Status: COMPLETE for P1b. No commit, no stash, no branch change. Combined checks NOT started.

## Starting state

- Branch `wip/billing-closure`, HEAD `2702fba0bd976ddb40cb4f463f1e54a1cb497b9d`
  ("WIP: preserve campaign spend across database restores and fresh-marker
  recovery. NOT FOR PUSH").
- `git status --short`: clean (no dirty files) at session start.
- Production files hashed read-only before any work:
  `scratchpad/evidence/prod_start.txt` (40 files under `02- Data/03- Inferences
  Storage` and `08- Checkpoint`).

## Containment

Rebuilt from the P4c profile in this session's scratchpad:

- `nonet.sb`: outbound denied except loopback; the mDNSResponder socket
  denied; `file-write*` and `file-read-data` denied under the four production
  directories and a decoy.
- `tw/`: the audit-hook `sitecustomize` tripwire.
- `isoroot/`: provisioned by `.github/scripts/provision_ci_paths.py` under the
  sandbox (exit 0).

Controls, both run WITH THE SANDBOX ACTIVE:

| arm | external connect | raw socket | DNS | loopback | sqlite `mode=ro` protected | write protected | list protected |
|---|---|---|---|---|---|---|---|
| A sandbox + tripwire | refused (tripwire) | refused (tripwire) | refused (tripwire) | allowed | refused before open (tripwire) | refused (tripwire) | refused (EPERM) |
| C sandbox only (`env -i`, tripwire off) | refused (EPERM) | refused (EPERM) | refused (gaierror 8) | allowed | refused (`unable to open`) | refused (EPERM) | refused (EPERM) |

Decoy sha256 unchanged after both arms (`evidence/decoy_before.txt`). No arm
ran outside the sandbox.

## Start digests (under containment)

`evidence/digests_start.json`: renderer digest `5ea2c6cc...a956`,
`PROMPT_VERSION` 1.11.0, `FINGERPRINT_VERSION` 8, prompt sha (mesh True)
`8baaa8b8...`, (mesh False) `db3c2b4d...`.

## Baselines (under containment, zero tripwire events)

| test | expected | measured |
|---|---|---|
| `test_billing_closure.py` | 274/0/0 | 274/0/0 |
| `test_campaign_billing_record.py` | 127/0 | 127/0 |
| `test_package_invariants.py` | 261/0/0 | 261/0/0 |
| `test_storage_run_metrics_flush.py` | 130/0 | 130/0 |

## Premises checked against current code (before editing)

**Item 1, embedding bound — CONFIRMED.** `oncotriage/agent/models.py:get_embedding`
reserved `int(len(str(text)) / PROVIDER_RESERVATION_CHARS_PER_TOKEN) + 1` input
tokens (3.0 chars/token). That is an estimate, not a bound: 5,000 four-byte
characters give 1,667 reserved tokens, and a tokenizer may bill more.
`AttemptLiability.resolve` charged the ledger the priced usage; a settlement
that then failed left the durable row RESERVED at the lower reservation.

**Item 2, missing/conflict — CONFIRMED, and the direction is worse than
session 1 reported.** `resolve` topped the live ledger up to the reservation
and wrote nothing durable. For `missing` the durable reading is $0, so a fresh
process reads LESS than live charged. Session 1 called this "safe direction";
under this brief's rule (resumed never lower than live) it is the unsafe one.
For `conflict` the reading is whatever the other settlement stored, which can
also be below live. No test drove either.

**Two further cells the same rule covers, found while enumerating:**
- `failed` with the priced response ABOVE its reservation (Stage 5's input
  estimate is also chars/3; an echoed model priced higher than the requested
  one does the same for embeddings).
- An unrecognised settlement result from a duck-typed sink.

## Evidence for the embedding bound

- The installed OpenAI SDK (openai 1.99.9), whose docstrings are generated from
  OpenAI's API specification, documents `embeddings.create` `input`: "The input
  must not exceed the max input tokens for the model (8192 tokens for all
  embedding models), cannot be an empty string ... all embedding models enforce
  a maximum of 300,000 tokens summed across all inputs in a single request."
  (`openai/types/embedding_create_params.py`, `resources/embeddings.py`.)
- OpenAI's embeddings guide (fetched 2026-09-13; the API-reference URL returned
  403): "Max input 8192" for text-embedding-3-small; encoding `cl100k_base`. It
  does not say whether an over-limit input errors or truncates.
- Not verified offline: tiktoken's `cl100k_base` file is not cached and the
  sandbox denies the download, so the byte-level argument (tokens <= UTF-8
  bytes) could not be checked against the real vocabulary. It is NOT used.

## Design (implemented)

**Item 1.** `models.embedding_reservation_input_tokens(text, model)`:
- one `str` input: the documented per-input maximum, 8,192 tokens, whatever
  its length or content;
- any other input (batch, token arrays, bytes, None): the documented
  per-request maximum, 300,000;
- a model absent from `EMBEDDING_MAX_INPUT_TOKENS_PER_INPUT`: refused before the
  budget gate, the reservation and the request (`EmbeddingReservationUnbounded`).
- The request (model, input object, timeout) is unchanged.
- Cost: 8,192 x $0.02/1M = $0.00016384 per reservation; feeds no pacer.

**Item 2.** `AttemptLiability._settlement_did_not_land(result)`:

| result | durable reading of the attempt row | live charged | shortfall row | latch | fresh process |
|---|---|---|---|---|---|
| `failed` / unrecognised, priced <= reserved | reservation | reservation | none | no | reads reservation = live |
| `failed` / unrecognised, priced > reserved | reservation | priced | priced - reserved | only if not recorded | reads priced = live |
| `conflict` | stored amount (0 if unreadable) | max(priced, reserved, stored) | live - stored (may be $0) | yes | reads live (over by stored if unreadable) |
| `missing` | 0 | max(priced, reserved) | live | yes | REFUSES `billing_record_incomplete`, printing the retained amount |

- The shortfall row is kind `settlement_discrepancy`, settled at write, id
  `<attempt_id>:settlement_discrepancy` (idempotent through
  `reserve_billing_attempt`'s insert-then-read-back).
- If the database will not take it, the sink writes a synced marker
  (`08- Checkpoint/batch_runner_billing_discrepancies/<attempt>.json`); the runner
  reconciles markers at the top of `establish_billing_campaign`, before any paid
  work, and refuses `billing_discrepancy_unrecorded` for any it cannot commit.
- If neither lands: the run latches and the fault is counted. THE STATED BOUND:
  nothing durable remains, so a fresh process reads lower by that attempt.

## Tests added (`tests/test_billing_closure.py` SECTION 7, runs before the isolation checks)

- 7a-7e: the bound's reasoning, adversarially: empty, NUL, 4-byte emoji,
  100k digits, combining marks, lone surrogate, CJK, 1 MB ASCII (all 8,192);
  batch, token arrays, bytes, None (all 300,000); undocumented, None, empty and
  unhashable models (refused); the old estimate shown NOT to bound 5,000
  one-token emoji.
- 7f-7j through the real `get_embedding`: reserved at 8,192 tokens and price;
  request kwargs unchanged (model, same input object, timeout; nothing added);
  unknown model refused with zero calls, rows and spend; a response AT the bound
  with a failed settlement leaves no shortfall; a provider billing BEYOND its
  bound, and an echoed pricier model, each produce a discrepancy row and parity.
- 7k-7q: missing (Stage 5 and Stage 2), conflict below/above live, conflict with
  an unreadable stored amount (over-counts by exactly the stored amount), an
  unrecognised result, Stage 5 priced above its reservation.
- 7r-7v: deferred marker, reconciliation, repeated reconciliation (a no-op; a
  marker left in place is the SAME row), a colliding amount FAILED, malformed
  markers unreconciled by name, `.tmp` ignored, nothing durable (the stated
  bound).
- 7w: vocabularies equal across layers; the reader refuses an unparseable note
  as incomplete, sums a conflict note, rejects a reserved discrepancy row.
- 7x-7z-iii: FRESH PROCESSES through the real `main()`: missing (process 2 and
  3 refuse `billing_record_incomplete`, exit 1, zero calls, amount printed, one
  discrepancy row); conflict (process 2 seed == process 1 live ledger and
  remaining equal; settled and unresolved compared separately; process 3 same
  seed); conflict deferred (reconciled once, printed, marker removed, no double
  count); missing deferred; missing unrecordable (stated bound).

The child script gained `settle_*` injections and reports `calls` in observe
mode.

## Results so far (all under sandbox + tripwire, zero non-loopback events)

| test | HEAD (git archive, same containment) | after |
|---|---|---|
| `test_billing_closure.py` | 274/0/0 | 319/0/0 |
| `test_campaign_billing_record.py` | 127/0 | 127/0 (run before the embedding-call fix; rerun pending) |
| `test_package_invariants.py` | 261/0/0 | 261/0/0 (rerun pending) |
| `test_storage_run_metrics_flush.py` | 130/0 | 130/0 |
| `test_spend_coverage.py` | 169/0 | 169/0 |
| `test_spend_gate.py` | 165 | 165 |
| `test_storage_run_identity.py` | 158 | 158 |
| `test_storage_schema_guards.py` | 136 | 136 |
| `test_provider_resilience.py` | not run | 203/0/0 |
| `test_degradation_counter_readers.py` | not run | 160 |
| `test_storage_inference_logging_contract.py` | not run | 101 |
| `test_runner_crash_record_and_db_unification.py` | not run | 65 |
| `test_agent_retrieval_observability.py` (real root, model cache) | 104/0/1 | 104/0/1 |

- Tripwire records seen: 26 `connect-loopback` to `127.0.0.1:1` (the suites'
  own closed-port probes). No external connect, no DNS, no protected sqlite.
- `test_agent_retrieval_observability.py` ABORTS in the isolated root at HEAD
  and after alike (FastEmbed cannot load `Qdrant/bm25` offline without the model
  cache). Environmental, not caused here; assessed with the real root instead.
- The CLAUDE.md counts for spend_coverage (165), spend_gate (164/162),
  run_identity (159) and schema_guards (135) are stale at HEAD; not moved here.

## Defects found in this session's own work

1. **Found by the first test run:** `get_embedding` still passed the old
   `len/3+1` formula to `begin_billed_attempt`; the first edit replaced only the
   block above it. 12 checks failed (reserved 1,667 tokens). Fixed.
2. **Found by the first test run:** the test's `parity_holds` required zero
   reserved rows, which is wrong for a failed settlement (its row stays
   reserved). It now expects exactly the settlements the LIVE process recorded
   as not landed, counted from its own fault keys.
3. **Found by self-review (pending the plant matrix, to avoid contaminating its
   copies):** `DISCREPANCY_DIRNAME` was inserted between
   `CAMPAIGN_REFUSAL_REASONS` and its docstring; two spend comments still say a
   non-landing settlement only tops the ledger up; the duck-typed sink contract
   omits the new optional methods; a newly created marker directory's parent is
   not synced.

3b. **Fixed after the matrix** (so its copies were not contaminated): the
   docstring order in `runner.py`; the two stale spend comments; the sink
   contract docstring; the marker directory's parent is now synced when the
   directory is created.
4. **Found by the firing controls:** C4 and C9 ABORTED the test file on a raw
   `os.listdir(_MARKERS)` in 7r-i's arguments instead of recording failures.
   Wrapped in `drive`; re-run, both now fail by name (below).

## Final results (final tree, sandbox + tripwire, isolated root)

| test | result |
|---|---|
| `test_billing_closure.py` | **319/0/0** (274 + 45) |
| `test_campaign_billing_record.py` | **127/0** |
| `test_package_invariants.py` | **261/0/0** |
| `test_storage_run_metrics_flush.py` | **130/0** |
| `test_spend_gate.py` | 165 (= HEAD) |
| `test_spend_coverage.py` | 169/0 (= HEAD) |
| `test_storage_inference_logging_contract.py` | 101 |
| `test_runner_crash_record_and_db_unification.py` | 65 |
| `static_checks.py` | 317 files compiled |
| `ci_test_buckets.py --check` | consistent: 149 files, 130 bucket A |

- Tripwire: 27 `connect-loopback` to `127.0.0.1:1` (the suites' own closed-port
  probes); no external connect, no DNS, no protected sqlite, no protected write.
- **No arm, test, control or probe ran outside the sandbox.**
- Digests at end equal start: renderer `5ea2c6cc...`, `PROMPT_VERSION` 1.11.0,
  `FINGERPRINT_VERSION` 8, both prompt shas (`evidence/digests_end.json`).
- Production `02- Data/03- Inferences Storage` and `08- Checkpoint` (40 files,
  side files included): byte-unchanged (`evidence/prod_end.txt`). Decoy
  unchanged.

## Firing controls (`scratchpad/p1b_plants.py`; disposable copies; live tree byte-unchanged after both runs)

| control | closure test | caught by |
|---|---|---|
| C0 clean | 319/0 | -- |
| C1 old `len/3+1` estimate restored | 312/7 | 7f, 7h, 7i, 7l, 7s, 7s-i, 7v |
| C2 undocumented model not refused | 317/2 | 7d, 7g |
| C3 batch reserved per-input | 318/1 | 7c |
| C4 old top-up-only settlement handling | 294/25 (re-run) | 7i..7m-i, 7x.., 7y.. |
| C5 missing does not refuse | 311/8 | 7k-i, 7l, 7r-i, 7s-i, 7w-i, 7x-i.. |
| C6 missing/conflict do not latch | 314/5 | 7k-ii, 7l, 7m-i, 7x, 7y |
| C7 conflict read as reservation | 313/6 | 7m, 7n, 7o, 7y, 7y-i, 7z |
| C8 live not raised to stored | 318/1 | 7n |
| C9 no marker fallback | 315/4 (re-run) | 7r, 7r-i, 7z, 7z-ii |
| C10 runner does not reconcile | 317/2 | 7z, 7z-ii |
| C11 non-deterministic discrepancy id | 316/3 | 7s, 7s-i, 7t |
| C12 incomplete refused as merely unreadable | 316/3 | 7x-i, 7x-iii, 7z-ii |
| C13 discrepancy rows not summed | 311/8 | 7i-i, 7j, 7m, 7o, 7q, 7w-i.. |

## Changes (files)

- `oncotriage/agent/models.py`: documented bounds, `EmbeddingReservationUnbounded`,
  `embedding_reservation_input_tokens`; `get_embedding` reserves the bound.
- `oncotriage/spend.py`: settlement/discrepancy vocabularies,
  `AttemptLiability._settlement_did_not_land`, `BillingRecord._stored_settlement`
  and `._record_discrepancy`; stale comments corrected.
- `oncotriage/storage/database_logger.py`: kind `settlement_discrepancy` (settled
  at write), `record_settlement_discrepancy`, `billing_attempt_settled_usd`,
  marker write/reconcile, `BillingRecordIncomplete`, `campaign_billing_total`
  sums discrepancies and refuses on a missing one, sink methods.
- `oncotriage/batch/runner.py`: refusal reasons `billing_record_incomplete`,
  `billing_discrepancy_unrecorded`; markers reconciled before paid work; the sink
  gets the marker directory.
- `tests/test_billing_closure.py`: SECTION 7 and child injections.
- No schema change: `kind` and `note` were already free TEXT columns, and
  `SCHEMA_USER_VERSION` did not move.

## Unresolved (ranked)

1. **Double fault leaves no durable trace.** If the database refuses the
   discrepancy row AND the marker cannot be written, a fresh process reads lower
   than live by that attempt (7v, 7z-iii). The live run latches and counts it.
   Nothing further is possible without a third durable location.
2. **Stage 5's input reservation is still `chars/3` (an estimate).** Out of
   item 1's scope. A priced response above it is handled only through the
   discrepancy path when its settlement fails (7q). Proposal: bound it from the
   model's context window, or accept the discrepancy path as the backstop.
3. **The bound assumes the provider enforces its documented limit** on the
   token count it bills, and it is priced at the REQUESTED model; an echoed
   pricier model is handled by the discrepancy path (7i, 7j), not the bound.
   The guide does not say whether over-limit input errors or truncates.
4. **The latch banner says "could not be written"** for `missing`/`conflict`,
   which is inaccurate for them. The discrepancy console line above it is
   accurate. Wording only.
5. **`missing` has no remedy but `--fresh` or restoring the database**; there
   is no operator acknowledgement path to resume the same campaign.
6. **A `failed` settlement that actually landed** makes the discrepancy row
   over-count by the shortfall (safe direction, stated in the docstring).
7. **Marker files are trusted input.** A local writer to `08- Checkpoint/` can
   inflate spend or force a refusal. Markers carry `correlation_id`, the same
   field the billing row already stores.
8. **Unmeasured:** runtime of the discrepancy path (rare by construction), and
   the budget effect of the larger embedding reservation ($0.00016384 per
   failed or unresolved embedding versus about $0.0000014 before).
9. **CLAUDE.md is stale** for this pass: no P1b section, the P1 weakness notes,
   and several counts (spend_coverage 169, spend_gate 165, run_identity 158,
   schema_guards 136, billing_closure 319, bucket counts 149/130).

## Exact next action for the combined-checks session

1. Preserve first: `git status` shows the five modified files above and the new
   `RECOVERY_P1B_REPORT.md`, uncommitted. Ask the operator before editing if
   they are not committed or backed up.
2. Rebuild containment from this report and fire both control arms.
3. Run under containment: CI bucket A in full; `tests/run_serial_tests.py`
   (confirm `oncotriage/config.py` and `cancer_code_registry.py` byte-identical
   afterwards); `python fixture_replay.py` (expected to REFUSE at the shipped
   provider, per CLAUDE.md -- confirm, do not treat as a regression).
4. `test_agent_retrieval_observability.py` needs the model cache: run it with the
   real project root under the sandbox (104/0/1 here) or provision the cache.
5. Reconcile CLAUDE.md: add the P1b section (the table above), correct the counts
   in item 9, and re-measure rather than copy.
6. VERIFICATION QUESTION (not an assumed defect): production `inferences.db`
   reads `PRAGMA user_version` 16, CLAUDE.md's version block says era 17 and the
   billing closure notes say era 18. Establish what each number refers to:
   `SCHEMA_USER_VERSION` in code, the stamp an older database carries until its
   next `initialize_database()`, and when each era was introduced. An untouched
   older database behind newer code is legitimate. Read it through
   `tests/_db_snapshot.py` or a byte copy; do not open production.
