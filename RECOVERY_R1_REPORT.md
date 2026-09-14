# RECOVERY R1 -- the Stage 5 reservation is an upper bound

Status: COMPLETE for R1. Stopped here; the combined checks were not begun.

Written for: the combined-checks session and the operator reviewing billing closure.

## 1. Starting state

- **Branch and revision.** `wip/billing-closure` at `1d0db758184baad35756e6aca55139b34a1e869a` ("WIP: verify uncertain billing settlements and clarify stop reasons. NOT FOR PUSH").
- **Dirty files at start.** None. `git status --short` was empty (saved: `evidence/git_status_start.txt` in this session's scratchpad).
- **Baselines, measured at start under containment** (not taken from notes):

  | suite | result |
  |---|---|
  | `test_billing_closure.py` | 351 / 0 / 0 |
  | `test_campaign_billing_record.py` | 127 / 0 |
  | `test_package_invariants.py` | 261 / 0 / 0 |
  | `test_storage_run_metrics_flush.py` | 130 / 0 |

  All four match the expected baselines. The tripwire recorded zero events in these runs.
- **Digests at start.** `renderer_digest` `5ea2c6cc...`, `PROMPT_VERSION` 1.11.0, `FINGERPRINT_VERSION` 8, and the sha256 of both rendered system-prompt variants.
- **Production inventory at start.** sha256 of all 40 files under `02- Data/03- Inferences Storage` and `08- Checkpoint` (`evidence/prod_start.txt`). The live `inferences.db` is `47bab774...`.

## 2. Containment

- **Two layers, both fired before any provider-touching import.**
  - The OS sandbox (`sandbox-exec`) denies outbound network, including the system DNS socket. It also denies writes to production directories and reads of protected directories.
  - The `sitecustomize` audit-hook tripwire refuses and records outbound connects, DNS lookups, and sqlite or file access to protected paths.
- **Two firing controls.**
  - Arm A: sandbox active, tripwire active. External connect, raw socket, DNS, a protected sqlite read, a protected write and a protected listing were all refused. Loopback was allowed.
  - Arm C: sandbox active, tripwire disabled. The same probes were refused by the sandbox alone.
- **No arm escaped.** No control removed both protections. Every test run in this session used both layers and an isolated project root (`ONCOTRIAGE_MAIN_PATH`, provisioned by `.github/scripts/provision_ci_paths.py`).
- **Tripwire records during test runs.** The six-suite run recorded zero events. The 59-suite run recorded 36 events, all loopback (the closed-port probes) and **zero non-loopback**. The final control round (ten copies plus the real tree) recorded zero events.

## 3. The bound and its documented basis

**The defect.** A Stage 5 attempt reserved `chars/3 + 1` input tokens plus its output ceiling, priced at the base input rate. That is an estimate, not a bound:

- a tokenizer can bill more tokens than chars/3;
- a cache write bills above base input;
- a long-context request bills above both.

When every durable write after dispatch failed (P1c item 2), a fresh process read that reservation, which was below the real charge. P1c's case was $20.00 charged against $0.38484 reserved.

**The bound, for one wire request** (`config.stage5_attempt_bound`):

```
input tokens  <= context_window_tokens
output tokens <= min(request's output ceiling, max_output_tokens)
usd = window        x max(base input, cache read, cache write[TTL]) x long-context input multiplier
    + output tokens x output rate                                  x long-context output multiplier
both halves x matching_sdk_attempts_per_call()   (1 on all three arms as shipped)
```

**Documented limits** (`config.STAGE5_ATTEMPT_LIMITS`, closed):

| wire model | context | max output | long-context in / out | cache write | source |
|---|---|---|---|---|---|
| Sonnet 4.6 on Bedrock (global/us/eu/au/jp/in-Region) | 1,000,000 | 64,000 | **2.0x / 1.5x ASSUMED** | per-TTL rows in PRICING_CONFIG | AWS model card, read 2026-09-14 |
| `*.openai.gpt-5.6-terra` (Bedrock) | 1,000,000 | 128,000 | 2.0x / 1.5x | 1.25x input | AWS model card; OpenAI model page for max output |
| `gpt-5.6-terra` (OpenAI) | 1,050,000 | 128,000 | 2.0x / 1.5x | 1.25x input | OpenAI model page, read 2026-09-14 |

**The shipped numbers.** `MATCHING_MAX_TOKENS` is 32,000 and the warmup ceiling is 1.

| wire model | trial attempt | warmup |
|---|---|---|
| `us.anthropic.claude-sonnet-4-6` (shipped) | **$9.042** | $8.25002475 |
| `global.anthropic.claude-sonnet-4-6` | $8.22 | $7.500023 |
| `gpt-5.6-terra` | $5.826 | $5.250018 |
| `us.openai.gpt-5.6-terra` | $6.1336 | $5.50002 |
| `gpt-4o-2024-08-06`, an unknown model, `None` | refused | refused |

**The assumptions, as stated in config:**

1. The provider enforces its documented window and output ceiling on the tokens it bills. A prompt over the window is a 400, which is classified `CATEGORY_CLIENT`, not billed.
2. Bedrock bills the model the request named. A response that echoes a pricier model is caught by `AttemptLiability`'s discrepancy rule, not by the bound.
3. `PRICING_CONFIG`'s rates and the multipliers are the provider's.
4. **The Sonnet 4.6 long-context premium on Bedrock is not verified.** No first-party AWS page this project could read states one, so 2.0x / 1.5x is an assumed ceiling. A Bedrock premium above it would break the bound.

**What did not change.** The request, the model, `MATCHING_MAX_TOKENS`, the warmup ceiling, truncation and the prompt are all unchanged. The pacer's token-quota reservation keeps the chars/3 estimate, because it paces a tokens-per-minute quota and is corrected to usage. The bound governs money only.

## 4. Changes

- **`oncotriage/config.py`**:
  - `STAGE5_ATTEMPT_LIMITS` and its argued basis;
  - `stage5_attempt_bound`, which is pure and raises `Stage5ReservationUnbounded`;
  - `stage5_reservation_note`;
  - `STAGE5_RESERVATION_BASIS` and `STAGE5_RESERVATION_BASIS_VERSION` (1).
- **`oncotriage/agent/evaluation.py`**:
  - `_execute_matching_call` computes the bound first, so a refusal comes before dispatch. The attempt record reserves the bound's tokens and dollars and records the basis note. `on_possibly_billed` reports the bound.
  - New `assert_stage5_reservation_bounded(per_trial=)`, called at the node top before anything is rendered.
- **`oncotriage/spend.py`**:
  - `begin_billed_attempt`, `AttemptLiability` and `BillingRecord.reserve` accept `reserved_usd` and `note`.
  - A response priced above a bounded reservation is counted `bound_exceeded:{source}` and logged `billing_bound_exceeded`. It is still charged at its price.
- **`oncotriage/storage/database_logger.py`**: `stage5_unproven_liabilities(campaign_id)` reads every Stage 5 row still reserved, or settled at its reservation (`response_unpriced`, `possibly_billed`, `abandoned`). A row is proven only when all of these hold:
  - its note records the current basis and version;
  - the bound recomputed now from that note is covered by the row's reserved input tokens, output tokens and dollars.
- **`oncotriage/batch/runner.py`**: `establish_billing_campaign` refuses paid work as `billing_reservation_unbounded` (new, in `CAMPAIGN_REFUSAL_REASONS`) when any unproven Stage 5 liability exists. The refusal names the count, the total and the first three rows. Its remedy names the provider's bill and `--fresh`, and says there is no automatic reconciliation.
- **Tests**: see sections 5 and 8.

## 5. Tests and results

### New: `tests/test_billing_closure.py` SECTION 9

| check | what it proves |
|---|---|
| **9a / 9a-i / 9a-ii** | The owner agrees with an independent derivation for every wire model, trial and warmup. Limits are typed from the documents; rates come from `PRICING_CONFIG`. The shipped results are also pinned as dollar literals. |
| **9b / 9b-i** | The cache-write class is included: TTL 1h, no cache point, Terra's 1.25x. The request ceiling is clamped to the model maximum. SDK attempts multiply both halves. |
| **9c** | The old estimate was not a bound: 20,000 CJK characters exceed chars/3. |
| **9d / 9d-i / 9d-ii** | Adversarial text through the real `_execute_matching_call`, with the policy stubbed so nothing is dispatched: empty, NUL, one character, 200k CJK, 50k emoji, combining marks, 4 MB. All reserve the same bound, while the pacer estimate still follows the text. The note is correct. |
| **9e / 9e-i** | Refusal by name for an unknown model, a priced-but-undocumented model (`gpt-4o`), `None`, an unhashable model, bool or zero ceilings and attempts, and malformed limits entries. |
| **9f / 9f-i / 9f-ii** | Refusal through the real `call_matching_model`: zero provider calls, zero billing rows, zero spend. The node guard refuses once and passes the shipped configuration in both call modes. The node calls the guard before its first dispatch call, checked by AST. |
| **9g** | The request kwargs are identical under the documented bound and under a trivial one. |
| **9h / 9h-i / 9h-ii** | The row is reserved at the bound. A response at documented maximum usage settles at or below it. A possibly-billed failure is charged the bound both live and durably. A response beyond the documented window is named `bound_exceeded` and charged at its price. |
| **9i / 9i-i / 9i-ii** | The resume reader over fabricated rows. Estimate-era rows are unproven whatever the amount, even $1,000. An older basis version, a low amount, low tokens, an undocumented model or a garbage note is unproven. A settled response, a not-billed row and an embedding row are not examined. |
| **9j** | Fresh processes, SIGKILL after the provider answered at documented maximum usage. Process 1 died with the row reserved at the bound. Process 2's seed equals the bound and covers the priced answer; process 3 agrees. |
| **9k** | Fresh processes. Settlement, discrepancy row and marker all fail after an answer at maximum usage. The live ledger is the reservation, the fresh seed equals it and covers the priced charge, and process 3 agrees. |
| **9l** | Fresh processes, P1c's $20 shape: 10,000,000 prompt tokens, ten times the documented window, with every durable write failing. Process 1 names the broken bound and latches. The fresh seed is below the live charge. This is pinned as the one cell outside assumption (1). |
| **9m / 9m-i** | Fresh processes. An old underestimated reservation, and an old settled `possibly_billed` row, each make process 2 and process 3 refuse `billing_reservation_unbounded`: exit 1, zero provider calls, naming the row and $0.384840. |
| **9n** | Clean control: one call, one settled response, and a normal resume whose seed equals the live ledger. |
| **9o** | An echo naming the priciest priced model at the wire's documented maximum usage. It is priced above what the wire model charges ($3.993 against $2.484), yet at or below the $5.826 reservation. It is not counted as a broken bound, and parity holds. |
| **9o-i** | A truncated response (`finish_reason` `length`) at the output ceiling and the documented window settles at its priced amount, at or below the reservation. The resume reader proves the row. |
| **9p** | The warmup, through the real `call_matching_model_warmup`, reserves its own bound: $5.250018, full window, one-token ceiling. A possibly-billed warmup is charged exactly that, live and durably. |

### Changed assertions, each re-derived

The re-derivations are independent: limits are typed and rates come from `PRICING_CONFIG`, never from `STAGE5_ATTEMPT_LIMITS`.

- **Closure `_RESERVE_S5`** (1f, 1h, 1t, 1v, 7k, 7m, 8a..8k).
  - Was: `price_usage(wire, chars/3 estimate, 32,000)`, $0.38.
  - Is: the independent bound for `gpt-5.6-terra` at 32,000, $5.826.
  - New check `1-bound` pins the wire and the literal.
- **Closure 7n and 8i** ("stored above live").
  - The planted stored amount was $5.00, which was above the old estimate and is below the new bound.
  - It is now $7.00, with `7n-0` asserting it is above the bound.
- **Closure 4q, 4y, 4z, 4u** and **campaign 6zb, 6ze** (P4b / P4c recovery).
  - These fabricated an unmarked Stage 5 reservation at $0.40. R1 now refuses a resume over such a row, which is correct: the campaign log shows `REFUSING TO START PAID WORK: billing_reservation_unbounded`. So the fixtures were testing the refusal instead of the recovery.
  - They now fabricate a reservation of the shape the shipped code writes: bound amount, bound tokens, basis note.
  - The recovery semantics are unchanged. Re-derived amounts:
    - 4q: $1.25 settled + $5.826 = **$7.076**, census (2, 7.076);
    - 4y: census $1.25 + $0.75 + $5.826 = **(3, 7.826)**, new-campaign seed $0.75 + $5.826 = **$6.576**;
    - 4z: $1.25 + $0.50 + $5.826 = **$7.576**;
    - 4u, 6zb, 6ze compare against values read at runtime.
- **Campaign `_RESERVE_EXPECTED` and `_expected_res`** (3a-i, 3c, 6h, 6i, 6l).
  - Now the independent Terra bound, $5.826. New check `3a-0` pins it.
- **Provider resilience 5b.**
  - `_EXPECTED5B` is now the independent Sonnet bound at max_output 321: 1,000,000 x $4.125 x 2.0 + 321 x $16.50 x 1.5 = **$8.25794475**, pinned in the non-degeneracy check.
- **Closure 9d, 9h, 9j, 9k, 9l**, a second defect in this session's own draft, found by the F1 firing control. With the old estimate planted, the reserved amount is None, and 9d's `round(None, 9)` raised at module level. The file ABORTED at 9d, so 9e..9n never ran and the control could not say what else it caught. Those comparisons now go through `_round9` / `_both_numbers`, which report a failure instead of raising.
- **Closure 9f-ii**, a defect in this session's own first draft. It searched the node source text for `call_matching_model`, and the node's comments name that function 260 lines above the guard, so the check failed on prose. It is now an AST walk over call nodes.

### Results under containment

| suite | start | this session |
|---|---|---|
| `test_billing_closure.py` | 351 / 0 | **383 / 0** (+32: the 1-bound and 7n-0 checks, and SECTION 9) |
| `test_campaign_billing_record.py` | 127 / 0 | **128 / 0** (+1: 3a-0) |
| `test_provider_resilience.py` | 201 / 0 | **203 / 0** |
| `test_spend_gate.py` | 165 | **165** |
| `test_spend_coverage.py` | 169 | **169** |
| `test_storage_run_metrics_flush.py` | 130 / 0 | **130 / 0** |
| `test_package_invariants.py` | 261 / 0 / 0 | **261 / 0 / 0** |
| the 59 bucket-A suites that drive Stage 5, spend or billing | -- | **58 pass, 1 fails** (see below) |
| `static_checks.py` | -- | **exit 0, 317 compiled** |

- **The one failure: `test_agent_retrieval_observability.py`.** It is not an R1 effect. SECTION A loads the real FastEmbed `Qdrant/bm25` model, and under `HF_HUB_OFFLINE=1` with no pre-warmed cache it raises `ValueError: Could not load model Qdrant/bm25 from any source`. That is the deferred "retrieval observability with the model cache" item.
- **Two suites print tracebacks and pass.** `test_resume_capture_and_ragas.py` (232 / 0) and `test_mcp_deidentified_responses.py` (99 / 0) print expected logged tracebacks from their own simulated kills and refusals.
- **How the 59 were chosen.** Every bucket-A test file whose source names Stage 5 dispatch, the graph, `MATCHING_MODEL`, `MATCHING_MAX_TOKENS`, the spend ledger, the wire model, or any billing-record or reservation function. This is not full bucket A.
| `ci_test_buckets.py --check` | -- | **consistent: 149 test files, 130 in bucket A** |

## 6. Coverage rule (acceptance)

- **SIGKILL after the answer (9j).** Covered: the fresh seed equals the bound, which is at least the priced charge.
- **Settlement + discrepancy + marker total failure (9k).** Covered at documented maximum usage.
- **P1c's $20.00 vs $0.38484 (9l).** Not covered, and not removable by a reservation. That shape is ten times the documented context window, so it breaks assumption (1). It is named at dispatch time (`bound_exceeded:stage5`, latched) and pinned rather than hidden. A reservation-sized old row of that shape now refuses a resume by name (9m).
- **Old unresolved reservations.** Refused by name in a fresh process (9m), never assumed covered.
- **Refusal before dispatch with zero provider calls.** 9f, and 9m through `main()`.
- **Clean control.** 9n.

## 7. Headroom

This is computed through the owner against the shipped configuration, not driven through a campaign. The shipped arm is `us.anthropic.claude-sonnet-4-6`, per-trial, `MAX_WORKERS` 12 x parallel bound 2 = 24 attempts in flight, campaign cap $300, serving cap $25.

| quantity | old estimate | documented bound |
|---|---|---|
| one trial attempt | $0.5719 (13,304 tokens for a 32,495 + 6,176-char prompt) | **$9.042** (15.8x) |
| one warmup | $0.037 | **$8.25** |
| resume seed after a death with 24 attempts in flight | $13.73 | **$217.01** (72% of the cap) |
| possibly-billed failures to exhaust the $300 cap | 524 | **33** |
| possibly-billed failures to exhaust the $25 serving window | 43.7 | **2.76** |

**This is material.**

- One death with a full wave in flight commits most of the campaign cap to unresolved reservations.
- The serving window is exhausted by three possibly-billed failures.

The live cap (`spend.cap_exceeded`) reads resolved spend, so a healthy run is unaffected until something fails or dies.

**Options, none chosen here.**

1. Verify the Bedrock Sonnet long-context premium against a console bill (A6). At 1.0x / 1.0x the trial bound is $4.65.
2. Add a `count_tokens` preflight so the input half is bounded by the measured prompt plus a documented tokenizer margin rather than the window. That is a paid-path API call.
3. Gate dispatch on `remaining >= bound` so the cap is never crossed by reservations.
4. Reduce the in-flight count (`MAX_WORKERS`, the per-trial parallel bound).
5. Add an operator acknowledgement path that reconciles old or unresolved reservations against a provider bill.

## 8. Firing controls

- **How they were run.** One disposable copy per safeguard: `oncotriage/`, `tests/` and the entry points, copied into the scratchpad. Each copy got one plant, anchor-counted and compiled.
- **Containment.** The sandbox and tripwire were active, the editable-install finder was stripped, and a realpath preflight showed each copy imports its own package. A clean copy was the control.
- **Test file.** Every copy ran the FINAL `tests/test_billing_closure.py`.
- **The real tree was not touched.**

| control | plant | SECTION 9 checks that failed | total |
|---|---|---|---|
| clean copy | none | none | **383 / 0** |
| F1 | old estimate restored in `_execute_matching_call` | 9d, 9d-i, 9h, 9h-i, 9h-ii, 9j, 9k, 9l, 9o-i, 9p (+25 in P1/P4/P1b/P1c) | 348 / 35 |
| F2 | no refusal for an undocumented model | 9e, 9f, 9f-i, 9i | 379 / 4 |
| F3 | cache-write class dropped from the bound | 9a..9d, 9h, 9h-i, 9i, 9j, 9k, 9l, 9p (+19) | 350 / 33 |
| F4 | long-context multipliers dropped | 9a..9d, 9h, 9h-i, 9i, 9j, 9k, 9l, 9o, 9p (+19) | 349 / 34 |
| F5 | SDK attempts ignored | 9b-i | 382 / 1 |
| F6 | resume unproven-liability check removed | 9m, 9m-i | 381 / 2 |
| F7 | basis-version check ignored | 9i | 382 / 1 |
| F8 | node-top guard call removed | 9f-ii | 382 / 1 |
| F9 | `bound_exceeded` count removed | 9h-ii, 9l | 381 / 2 |

- **All nine were caught; none aborted.**
- **The first round found two defects in my own checks.** F1 aborted the file at 9d, and 9f-ii read comments. Both are fixed; see section 5.
- **9o needs a long-context multiplier to fail.** Only F4 made 9o fail. Under F3 the long-context multiplier still lifts the bound above the echo's price, which is consistent with how the bound is built.

## 9. Defects and open items, by severity

### Fixed

- **HIGH: the Stage 5 reservation was not an upper bound.** A fresh process could read less than was charged after a total write failure.
- **HIGH: old estimate-era reservations were silently trusted by a resume.** They now refuse by name.
- **LOW: this session's own 9f-ii text check read comments.** It is now an AST check.
- **LOW: this session's own 9d..9l comparisons aborted the file under a planted defect.** Found by the F1 control; they are hardened.

### Unresolved

- **HIGH: headroom** (section 7). The bound is 15.8x the old estimate per trial, so possibly-billed failures and deaths consume the cap far faster. No option was chosen.
- **MEDIUM: assumption (4).** The Sonnet 4.6 Bedrock long-context premium is assumed at 2.0x / 1.5x and not verified. A higher real premium breaks the bound for the shipped arm.
- **MEDIUM: the 9l cell.** A response billed above the documented window breaks the bound. It is named live, but a total write failure then leaves a fresh seed below the charge.
- **MEDIUM: no reconciliation path for old reservations.** A campaign holding one is refused until `--fresh` or manual reconciliation. Any database with pre-R1 unresolved Stage 5 rows cannot resume.
- **LOW: the ablation study, API and MCP server install no billing sink** (unchanged). The bound reaches their ledger charges, but not a durable record.

### Unverified

- The Bedrock console bill for any of the rates or multipliers.
- A real provider response at documented maximum usage (every drive uses stand-ins).
- The headroom numbers are computed, not measured through a live campaign.

- **LOW: an embedding reservation is not "bounded".** `models.get_embedding` passes no `reserved_usd`, so an embedding priced above its P1b bound is not counted `bound_exceeded`. P1b's discrepancy rule still applies. Unchanged by R1.

## End state

- **Digests.** `renderer_digest`, `PROMPT_VERSION` 1.11.0, `FINGERPRINT_VERSION` 8 and both prompt digests are unchanged; no renderer module was touched.
- **Production files.** All 40 are byte-identical to the start inventory, and `08- Checkpoint/` is included in that. The decoy is unchanged (`fed6a800...`).
- **Tests and the production database.** No test connected to a production database. The one read of the production database was 100 header bytes plus a byte scan for section 11, with no SQLite connection; its sha256 is unchanged.
- **Repository.** Branch `wip/billing-closure`, HEAD still `1d0db758`. No commits, no stash, no branch change.
- **Working tree.** The five code files and three test files above, plus this report.
- **Paid calls.** None.

## 10. Next action for the combined-checks session

1. Run full CI bucket A under the same containment, and the serial runner (`tests/run_serial_tests.py`, bucket B). Confirm `oncotriage/config.py` and `oncotriage/registries/cancer_code_registry.py` are restored. `config.py` carries this session's edit, so it must be byte-identical to the working tree before and after.
2. Run `python fixture_replay.py`. It is expected to REFUSE at the shipped provider (`UnsupportedMatchingProviderError`, before any hook or call). Record the refusal; do not pin the provider to make it pass.
3. Run `tests/test_agent_retrieval_observability.py` with the FastEmbed model cache pre-warmed (`.github/scripts/prewarm_model_cache.py`, then `HF_HUB_OFFLINE=1`).
4. Reconcile CLAUDE.md across every recovery pass (P1, P1b, P1c, P2, P3, P4, P4b, P4c, R1). This includes the test counts above, the `SCHEMA_USER_VERSION` statements (section 11) and this pass's bound.

## 11. Verification question: user_version 16, era 17, era 18

- **16 is the production database's header.** It was read as raw bytes, with no SQLite connection. `02- Data/03- Inferences Storage/inferences.db` has header `user_version` 16 and `application_id` 0x4F4E4331. Its schema text contains `CREATE TABLE drift_reference` (era 16) and none of `CREATE TABLE billing_attempts` (era 17), `CREATE TABLE run_counter_registry` or `billing_campaign_id` (era 18). It was last initialized by an era-16 build: commit `c954625`, 2026-09-10, the drift redesign, which set 15 -> 16. Its sha256 `47bab774...` equals the start-of-session inventory.
- **17 is a working-tree era that was never committed.** `database_logger.py`'s era record defines era 17 as the `billing_attempts` table from the cumulative-spend pass. `git log -S "SCHEMA_USER_VERSION = 17"` finds no commit: commit `928a265` (2026-09-13) changed the constant 16 -> 18 in one step and introduced both `billing_attempts` and `run_counter_registry`. CLAUDE.md's "Version state, CURRENT" block ("SCHEMA_USER_VERSION 17 ... era 17 is the cumulative-spend pass's billing_attempts") describes that working tree.
- **18 is the code constant now.** `SCHEMA_USER_VERSION = 18`: era 18 is `runs.billing_campaign_id` plus the `run_counter_registry` table, from the billing closure pass.
- **A contradiction to reconcile.** CLAUDE.md's P3 paragraph calls the production smoke database "era-17". Its header says 16 and it has no `billing_attempts` table.
