# RECOVERY E1 -- atomic budget admission

Status: **COMPLETE for E1.** Stopped here. The combined checks (full bucket A,
the serial runner, `fixture_replay.py`, retrieval observability) were not begun,
no pricing research was done, and no rate, multiplier, cap or evaluation scope
changed. No paid call, no commit, no stash, no branch change, no network egress.

Written for: the operator and the combined-checks session.

## 1. Starting state

- Branch `wip/billing-closure`, HEAD `ed61ff3` ("docs: record AWS Price List
  findings and unresolved pricing evidence. NOT FOR PUSH"). Dirty files at
  start: none.
- Baselines at start, under containment: `test_billing_closure.py` 388/0/0,
  `test_campaign_billing_record.py` 128/0, `test_package_invariants.py` 261/0/0,
  `test_storage_run_metrics_flush.py` 130/0. All match the brief. Zero tripwire
  records.
- Digests at start: `renderer_digest` `5ea2c6cc...c2a956`, `PROMPT_VERSION`
  1.11.0, `FINGERPRINT_VERSION` 8, rendered system prompt `8baaa8b8...` (MeSH
  applied) / `db3c2b4d...` (not applied).
- Production inventory at start: sha256 of all 40 files under
  `02- Data/03- Inferences Storage` and `08- Checkpoint`.

## 2. Containment

- OS sandbox (`sandbox-exec`, R1c's profile with this session's paths): outbound
  denied except loopback, the `mDNSResponder` socket denied, writes and data
  reads denied under both production directories, `09- Testing/Evaluation Runs`,
  `04- Results` and a decoy.
- `sitecustomize` audit-hook tripwire (R1c's, unchanged): refuses and records
  non-loopback connects, DNS, sqlite connects to protected paths, protected
  writes.
- Fired before any provider-touching import:
  - Arm A (sandbox + tripwire): external connect, raw socket, DNS, protected
    sqlite read-only connect, protected write, protected listing all refused;
    loopback allowed; production listings and the checkpoint read refused.
  - Arm C (sandbox only, tripwire off, `env -i`): the same probes refused.
- **No arm removed both protections and no arm escaped.** Every test and every
  firing control ran with both layers and an isolated project root
  (`provision_ci_paths.py`). No run produced a tripwire record. Child processes
  inherited both layers.
- How "no record" was established for the final run:
  - The tripwire writes its log file only when it records something, and it
    records every connect, loopback included.
  - The final run left no `.tw.jsonl` file at all.
  - A probe under the identical environment confirmed the tripwire loads
    unconditionally through `sitecustomize`. The probe's external connect was
    refused and logged, and its loopback connect was logged
    (`evidence/final_twprobe/`).
- No test connects to a production database: every database is a temp file;
  the production inventory is byte-identical at the end (section 9).

## 3. Premise verdict: CONFIRMED, measured on the unchanged code

- Code: `spend.cap_exceeded` compared `seed + measured` only; `require_budget`
  and Stage 5's `_spend_gate` call it before a request. The ledger was charged in
  `AttemptLiability.resolve`, after the response. `AttemptLiability.__init__`
  (the reservation, created inside the retry policy after the pacer's wait)
  compared nothing against any cap. The open-liability tally
  (`BILLING_RECORD._note_open`) existed and nothing read it for admission.
  `config.SPEND_CAP_USD` stated the overshoot bound as "the requests in flight"
  at their measured price, "within about a dollar and a half".
- Driven (`scratchpad/premise_probe.py`, evidence `premise_probe.json`): two real
  threads released by a barrier, each reserving $9.00 against a $10.00 cap.
  - Both passed the call-site gate and `require_budget`, and both liabilities
    were created. While both were open `remaining()` still read $10.00. After
    resolution the ledger read **$18.00, an $8.00 overshoot**.
  - Identical with the durable sink installed: two committed rows, durable total
    $18.00.
- The stated bound was false under the liability rule: a possibly-billed failure
  is charged its whole reservation, so the exposure is the sum of the
  reservations in flight (R1c: $217.008 at 24 attempts), not a few measured
  requests.
- Cross-process sharing of one budget is REACHABLE: `recover_campaign_identity`
  treats a campaign whose latest run is not FINISHED as open, so a second
  checkpoint directory can adopt a campaign whose process is still running.
- After the change the same probe declines the second worker (`headroom_held`)
  under both authorities; measured $9.00, durable $9.00.

## 4. The admission design and where the atomicity lives

**Rule.** A billed attempt is admitted only when
`committed + held + this reservation <= cap` (equality admitted, tolerance
1e-9), decided and recorded as ONE step, before the durable reservation and
before dispatch. Otherwise it is declined by name with zero provider calls.

**Two authorities** (`spend.ADMISSION_AUTHORITIES`), chosen per attempt by
`spend.durable_admission_applies`:

| authority | when | atomic step |
|---|---|---|
| durable | campaign budget, campaign policy, installed sink declaring `supports_admission` (the batch runner's `BillingRecordSink`) | `database_logger.reserve_billing_attempt(..., admission_cap=)`: `BEGIN IMMEDIATE` takes the database write lock BEFORE the read; `campaign_liabilities` sums the campaign's rows in SQL; `admission_decision` decides; the row is inserted and committed only if it fits, else rolled back. In-process threads are also serialised by `_WRITE_LOCK`; other processes on the same database by SQLite's write lock |
| process | everything else (API, MCP, ablation, window policy, tests without the shipped sink) | `SpendLedger.admit_hold`: under the ledger's one lock, read committed spend (`_committed_locked`, pinned equal to `budget_spend`), sum this process's open holds on the budget's sources, compare, and record the hold only if it fits |

**Committed vs held.** In process: committed = what `budget_spend` reads (seed
plus charges, or the rolling window); held = this process's open holds. Durably:
committed = every settled row plus every row still RESERVED by another run (a
sibling or a dead process -- indistinguishable, never assumed to release);
held = this run's own reserved rows.

**Counted once.** The durable authority reads the database, never the seed, so a
resumed balance (read from the same rows at startup) and live reservations
cannot be added twice. The process authority reads the seed plus charges, and
holds are separate, released in the same critical section that adds the charge.

**Settle / release exactly once.**
- `SpendLedger.charge_usd(usd, source, release=token)` removes the hold inside
  `_commit`'s critical section, so a settlement REPLACES the held amount with the
  actual charge; only the unused difference returns.
- `not_billed` releases the whole hold.
- `AttemptLiability.resolve` runs under a per-liability lock; duplicates and
  out-of-order completions read the first resolution.
- A reservation that raises after admission releases its hold.

**Decline reasons** (`ADMISSION_DECLINE_REASONS`, closed; the durable two are
restated in `database_logger` and pinned equal):
- `budget_exhausted` (committed + reservation > cap): latches the run under the
  campaign policy (`SPEND_LIMIT_CAP`).
- `headroom_held`: never latches.
- `reservation_unpriced` (process authority, cap in force).
- Counted in `SPEND_ADMISSION_DECLINES` (registered, on the run-end report).

**Exception path.** `spend.BudgetAdmissionDeclined` is a `SpendLimitReached`
(limit `spend_cap`). Stage 5's `_Stage5AttemptRecord.begin` converts it to
`Stage5SpendStopped` carrying `admission_reason`. The retry policy refunds the
permit as not billed and marks the refusal pre-send, so it is never classified
as a provider failure and `_account_unconsumed` never counts it abandoned.
Stage 2's channel degrades. The warmup floor says "held headroom" rather than
"a spend limit was reached" for a held decline.

**Amounts are supplied.** Admission compares whatever reservation it is handed;
nothing re-derives or depends on the Stage 5 multipliers.

**Preserved:**
- The call-site gates still run first with their own counters and latches.
- The unverified-record refusal still refuses first (`require_budget`, before
  admission).
- A reservation that cannot be persisted still latches `billing_record`.
- Legacy unproven reservations still refuse at resume (runner, untouched).
- P4b/P4c recovery is untouched.
- Enforcement off (`SPEND_CAP_ENFORCED=False`) or no readable cap admits
  everything and still tracks holds.

## 5. Tests and results (all under both containment layers)

Final run, same tree as the report:

| suite | result |
|---|---|
| `tests/test_budget_admission.py` (NEW) | **67 passed, 0 failed** |
| `tests/test_spend_gate.py` | **167/0** (was 164), 5 consecutive runs green |
| `tests/test_billing_closure.py` | **388/0/0** |
| `tests/test_campaign_billing_record.py` | **128/0** |
| `tests/test_agent_stage5_attempt_provenance.py` | **55/0** |
| `tests/test_package_invariants.py` | **261/0/0** |
| `tests/test_storage_run_metrics_flush.py` | **130/0** |
| `test_degradation_counter_readers` 160, `test_spend_coverage` 169, `test_spend_budget_split` 79, `test_provider_resilience` 203, `test_agent_stage5_per_trial_calls` 372 | all 0 failed |
| `ci_test_buckets.py --check` | consistent: 150 test files, 131 in bucket A |
| `static_checks.py` | 318 files compiled |

Earlier in the session, after the production change, also green:
- `test_ablation_stop_and_lock` 161
- `test_agent_bedrock_adapter` 297
- `test_agent_bedrock_anthropic_adapter` 425
- `test_agent_bedrock_anthropic_per_trial` 213
- `test_agent_degraded_run_and_reporting` 118
- `test_agent_per_trial_trial_cap` 31
- `test_agent_stage5_input_packing` 133
- `test_agent_structured_outputs` 158
- `test_api_shutdown_gate` 78
- `test_judge_independence` 137
- `test_matching_temperature_policy` 73
- `test_mcp_deidentified_responses` 99
- `test_rater_batch_spend_accounting` 124
- `test_rater_verify_before_retry` 27
- `test_runner_crash_record_and_db_unification` 65
- `test_spend_checkpoint_backstop` 54
- `test_spend_hard_kill_journaling` 45
- `test_spend_journal` 312
- `test_storage_inference_logging_contract` 101
- `test_storage_write_durability` 121
- `test_runner_stop_switch` **146** run alone in the foreground. In a
  `&`-backgrounded batch it failed only its SIGINT arms: the documented launcher
  artifact.

**What `test_budget_admission.py` drives**, each with real code:
- **2a/2b** process authority: two real threads × 40 rounds and eight threads ×
  20 rounds -- exactly one admitted per round, `headroom_held`, no latch.
- **2c/2d** the same through the durable authority; **2e** no campaign ever
  exceeds its cap.
- **2f** four CHILD PROCESSES on one database released together, three rounds:
  exactly one admitted, three `budget_exhausted`, one $9 row.
- **3** full wave (24 threads, $216 cap) admitted with equality, 25th declined,
  released; second wave admits floor((216 − 12·price)/9) = 23 -- only the unused
  difference returns. Both authorities.
- **4** resumed $5 (settled $2 + dead run's reserved $3) + $1 held + $4 = cap
  admitted, $4.000001 `headroom_held` reporting $5 committed / $1 held, $5.000001
  `budget_exhausted` latched. Both authorities.
- **5** boundary through `evaluation.call_matching_model` with the owner's
  supplied bound: just below and exactly at dispatch once; just above → zero
  provider calls, no row, `Stage5SpendStopped` naming the decline, counted,
  latched, on the degradation report.
- **6** nine threads resolve one attempt with three outcomes (one amount, one
  charge, one resolution, no conflict), and out-of-order duplicated resolution
  releasing each hold once. Both authorities.
- **7** fresh processes:
  - SIGKILL at the commit after the INSERT inside the admission transaction →
    no row survives, and a fresh process admits the full cap.
  - SIGKILL inside the provider call after commit → row stays RESERVED at the
    bound, and a fresh process counts it ($1.000001 more exhausted, $1 fits).
  - Non-dispatch is never assumed: there is no mechanism that proves it, so a
    committed reservation is never released by death.
- **8** the REAL `main()` in child processes:
  - cap exactly the reservation → dispatched.
  - $0.000001 below → zero calls, no billing row, latched, the patient's error
    names the decline, `runs.stop_reason = spend_cap`.
  - Clean control: two patients dispatch and settle, and a second invocation
    continues the same campaign seeded with the durable total and dispatches
    again.
- **9** preservation: unverified record refuses first (no hold, no call);
  reservation failure after admission releases the hold and still latches
  `billing_record`; declined request never opens or resolves a liability;
  rolling window never latches; measurement mode.
- **10** the SQL summation equals `campaign_billing_total` over 16 row shapes
  (all three readings reached).

**Firing controls** (`scratchpad/controls.py`, evidence `controls2_g*.json`):
- Mechanics: each plant is applied to a disposable copy of `oncotriage/` and
  `tests/` (anchor counted, `ast.parse`d), with the editable finder stripped,
  both containment layers, and a realpath preflight confirming the copy
  imported. The live tree was sha256-unchanged across all runs; zero tripwire
  records.
- Clean control: 67/0.

| plant | caught by |
|---|---|
| P1 process cap comparison removed | 9 checks (2a, 2b, 3b-3d, 4d, 4e, 9c, 9d) |
| P2 process read outside the lock (+5 ms) | 2a, 2b, 3d |
| P3 durable decision removed | 14 checks incl. 2f, 5c-5e, 7f, 8b, 8c |
| P3b durable `BEGIN IMMEDIATE` removed | 2f (cross-process) only |
| P4 charge without releasing the hold | 3c, 3d, 4f, 6b |
| P5a process seed counted twice | 4b-4f (first run ABORTED; guard added, now 4 recorded failures) |
| P5b durable own holds counted twice | 6 checks |
| P6 resolve-once lock removed | 6a both authorities |
| P7a / P7b equality refused (process / durable) | 7 / 9 checks |
| P8a exhausted does not latch | 4e, 5d, 8b, 8c |
| P8b held latches | 2a, 2c |
| P9 durable decline treated as a write failure | 12 checks |
| P10 hold leaks when the reservation fails | 9b |
| P11b SQL: typeof and upper bound both removed | 10a |
| P11c SQL: upper bound removed, typeof kept | 10a |
| P12 Stage 5 does not convert the decline | 5c, 5d, 9c |
| P13 no admission at all | 23 checks |
| **P11 SQL: typeof removed alone** | **NOT caught -- redundant predicate** (section 8) |

**Changed assertions in existing suites, each re-derived:**
- `test_spend_gate.py`:
  - `run_node` now SUPPLIES one call's price as the reservation (budgets there
    are written in whole calls; at the ~$5.83 real bound nothing fits).
  - 1l pins four spend counters.
  - Section 5 measures ZERO overshoot: the warmup plus one trial issued, peers
    declined `headroom_held` while a request is provably held in flight; it
    previously asserted the overshoot E1 removes.
  - 9e-9h: a bypassed call-site gate no longer reaches the wire, so each plant
    is caught by its accounting (missing phase declines, never-charged ledger,
    leaked holds).
  - 9e drops an admission flag: which later check stops the queued call is
    timing-dependent, and both outcomes were measured in consecutive runs.
- `test_billing_closure.py` 1v: a retry is two reservations; that case's cap is
  2 × the independently derived bound.
- `test_campaign_billing_record.py` 6A and `test_agent_stage5_attempt_provenance.py`
  scenario A / X1:
  - The reservation is supplied as one response's price.
  - The cap moves 3.5 → 4.5 responses (the old cap relied on the overshoot).
  - A10 now asserts two `budget_exhausted` admission declines and zero
    call-site declines.

## 6. Throughput cost (`scratchpad/throughput.py`, evidence `throughput.json`)

Shipped configuration read through the owners: `bedrock_anthropic`,
`us.anthropic.claude-sonnet-4-6`, MAX_WORKERS 12 × parallel bound 2 = 24
attempts in flight, 15 trials, cap $300, serving cap $25.

| | assumed 2.0/1.5 (shipped, unproven) | no-premium 1.0/1.0 (scenario) |
|---|---|---|
| trial / warmup reservation | $9.042 / $8.250 | $4.653 / $4.125 |
| held at full parallelism | $217.01 | $111.67 |
| committed spend where throttling begins | **$82.99** | **$188.33** |
| committed spend where no trial fits | $290.96 | $295.35 |
| attempts admissible at $0 / $100 / $200 / $250 / $290 | 33 / 22 / 11 / 5 / 1 | 64 / 42 / 21 / 10 / 2 |
| serving window ($25): attempts in flight at $0 | 2 | 5 |

**Behaviour when held headroom blocks otherwise-runnable work.** Simulated with
real threads through the real process authority: 48 patients, 12 workers, a
warmup then two lanes, actual charges at $0.0108 (cache working) or $0.0262
(cache absent), from committed baselines.

| committed before | assumed: completed / failed_held | no-premium: completed / failed_held |
|---|---|---|
| $0, $60 | 48 / 0 | 48 / 0 |
| $83 | 38 / 10 (cache working), 37 / 11 (absent) | 48 / 0 |
| $120 | 9 / 39, 8 / 40 | 48 / 0 |
| $190 | 5 / 43, 2 / 46 | 34 / 14, 30 / 18 |
| $250 | 2 / 46, 1 / 47 | 0 / 48, 5 / 43 |
| $285 | 0 / 48 | 0 / 48, 1 / 47 |

- **A held decline FAILS the patient and never latches.** Near the cap the run
  therefore does not stop cleanly: it keeps starting patients that fail (not
  checkpointed, resumed later) until committed spend leaves no room even for one
  reservation, which is what triggers `budget_exhausted` and the latch.
- The simulation's latencies are milliseconds, so it measures the admission
  outcome pattern, not wall time.

**Durable authority cost** (disposable DB, fullfsync on):

| campaign rows | SQL aggregate p50 | reservation without / with admission p50 |
|---|---|---|
| 0 | 0.003 ms | 14.7 / 13.7 ms |
| 1,000 | 1.2 ms | 13.6 / 17.2 ms |
| 10,000 | 12.0 ms | 14.2 / 25.6 ms |
| 20,000 | 22.9 ms | 14.4 / 37.9 ms |

- Linear in the campaign's rows, per reservation, under `_WRITE_LOCK`, so total
  aggregate work over a campaign grows quadratically.
- A 500-patient per-trial campaign reaches roughly 8-10k rows.
- Not measured under 12-worker contention.

## 7. Changes (working tree only)

| file | change |
|---|---|
| `oncotriage/spend.py` | admission vocabulary and counter; ledger holds (`admit_hold`, `record_hold`, `release_hold`, `held_usd`, `held_count`, `_committed_locked`; `charge_usd(..., release=)`); `BudgetAdmissionDeclined`, `admission_terms`, `durable_admission_applies`, `admission_declined`, `_admit_in_process`; `BillingRecord.reserve(admission_cap=)` and durable-decline mapping; `AttemptLiability` admits before reserving, releases on reserve failure, resolves once under a lock |
| `oncotriage/storage/database_logger.py` | `BILLING_ADMISSION_*`, `BillingAdmissionDeclined`, `CampaignLiabilities`, `campaign_liabilities` (SQL), `admission_decision`; `reserve_billing_attempt(admission_cap=)` with `BEGIN IMMEDIATE` read-decide-insert; `BillingRecordSink.supports_admission` |
| `oncotriage/agent/evaluation.py` | decline → `Stage5SpendStopped` with `admission_reason`; warmup floor sentence for a held decline |
| `oncotriage/degradation.py` | registers `SPEND_ADMISSION_DECLINES` |
| `oncotriage/config.py` | docstring only: the false overshoot statement replaced (renderer digest unaffected; config is excluded from it) |
| `tests/test_budget_admission.py` | NEW, 67 checks |
| `tests/test_spend_gate.py`, `test_billing_closure.py`, `test_campaign_billing_record.py`, `test_agent_stage5_attempt_provenance.py` | re-derived expectations (section 5) |
| `.github/scripts/ci_test_buckets.py` | new file declared in bucket A |
| `CLAUDE.md` | appended E1 section; earlier overshoot statements marked superseded, not rewritten |
| `RECOVERY_E1_REPORT.md` | this report |

## 8. Self-review findings

**Fixed (in my own work, found by running):**
- **P5a aborted the new test** (medium, test harness).
  - Trigger: unguarded module-level `begin(1.0)` in section 4.
  - Consequence: one traceback hid every later check.
  - Fix: all module-level `begin` calls are driven and guarded; P5a now reports
    4 failures.
- **9e's re-derivation was wrong twice** (low, test).
  - First it credited the pacer cancellation, then admission; consecutive runs
    showed both outcomes.
  - Fix: assert only the deterministic observables (requests issued,
    wave-phase declines); stable 5/5.
- **Docstring claim about the SQL guard was false twice** (low, documentation).
  - `typeof` is redundant: P11 not caught. The upper bound is load-bearing: P11c
    caught; it is the only predicate excluding infinity.
  - Fix: the docstring states this. The redundant predicate is kept, labelled.

**Unresolved (proposals, not built):**
1. **High -- held declines fail patients instead of waiting** (throughput,
   section 6).
   - Trigger: committed spend above $82.99 (assumed bound) with full
     parallelism.
   - Consequence: patients fail and are resumed; near the cap the run churns
     without latching.
   - Recommendation: bounded wait for released headroom, cancellable by the
     shutdown/stop checks, or adaptive concurrency. A decision for the operator:
     the brief specified decline.
2. **Medium -- sibling-induced latch.**
   - Trigger: two checkpoint directories sharing one campaign.
   - Consequence: a live sibling's in-flight reservation is "committed" to this
     process, so the decline is `budget_exhausted` and latches the run.
   - Conservative, and reachable only through cross-directory recovery (already
     an open item).
3. **Medium -- durable admission cost grows with campaign rows** (~23 ms at 20k,
   under the process-wide write lock).
   - Recommendation: a maintained per-campaign liability aggregate updated in the
     reserve/settle transactions, with this SQL kept as a verifier.
4. **Low -- ceiling claim consumed by an admission decline.** `_spend_gate`
   takes a call-ceiling claim before admission. The patient fails anyway.
5. **Low -- an unresolved liability** (`attempt_record_unresolved`) holds its
   reservation for the process's life; conservative, and it matches the durable
   row staying reserved.
6. **Low -- `spend.report_lines()` shows no held total**; declines appear only on
   the degradation report.
7. **Not addressed -- paths without `AttemptLiability`** (rater Batch API, ragas)
   are not admitted here, unchanged.
8. **Not addressed -- a `KeyboardInterrupt`** delivered between an in-process
   admission and its `try` could leak one hold in a dying process.

**Unverified / not assessed:**
- Durable admission under real 12-worker campaign contention.
- Linux (no fullfsync) timings.
- A real network remote.
- The API/MCP surfaces' rendering of `BudgetAdmissionDeclined`: they handle
  `SpendLimitReached`, and their suites passed, but no check drives an
  admission decline through them.
- `test_agent_retrieval_observability` failed in this session only because
  FastEmbed's model is not cached in the isolated root and the sandbox forbids
  download. It is deferred to combined checks per the brief.

**Instruction note.** "Decline by name" and "report the throughput cost"
together imply the item-1 behaviour. If the intended semantic was "wait for
released headroom", the brief should say so.

## 9. End state

- Digests unchanged: `renderer_digest` `5ea2c6cc...`, `PROMPT_VERSION` 1.11.0,
  `FINGERPRINT_VERSION` 8, both rendered-prompt hashes identical to start.
- Production inventory: all 40 files byte-identical to the start snapshot.
- Branch `wip/billing-closure`, HEAD `ed61ff3`, no stash, nothing committed.

## 10. Carried-forward open items

- (a) The Sonnet 4.6 long-context pricing statement remains an open external
  requirement. Final dollar-coverage acceptance of admission depends on a
  supported reservation bound; this session tested admission with SUPPLIED
  amounts only.
- (b) The cap may be insufficient if caching fails, and the judge budget may not
  cover a full 100-patient pass. Both are projections from earlier runs, not
  measured program costs. Section 6 now adds that at the assumed bound admission
  throttles a campaign from $83 of committed spend.
- (c) Check 7z-iii at line 3478 aborts under load. The combined checks must
  establish WHY its child process returned no seed, not merely guard the
  comparison.

## 11. Exact next action

Decide item 8.1 (decline versus bounded wait for held headroom) before the
combined checks. Then run the combined-checks session: full bucket A under
containment (with FastEmbed provisioned in the isolated root), the serial
runner, `fixture_replay.py`, retrieval observability, and the 7z-iii
investigation.
