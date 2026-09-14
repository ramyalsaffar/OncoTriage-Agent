# RECOVERY E1b -- admission follow-up (reservation replay, bounded wait)

Status: **⟨PENDING-STATUS⟩**

Written for: the operator and the combined-checks session.

## 1. Starting state

- Branch `wip/billing-closure`, HEAD `bf546f4` ("WIP: add atomic admission
  across campaign and process budgets. NOT FOR PUSH"). Dirty files at start:
  none (`git status --short` empty).
- Digests at start (under containment): `renderer_digest`
  `5ea2c6cc...c2a956`, `PROMPT_VERSION` 1.11.0, `FINGERPRINT_VERSION` 8,
  rendered system prompt `8baaa8b8...` (MeSH applied) / `db3c2b4d...` (not
  applied). Identical to E1's recorded start and end digests.
- Production inventory at start: sha256 of all 40 files under
  `02- Data/03- Inferences Storage` and `08- Checkpoint`
  (`evidence/prod_inventory_start.txt` in this session's scratchpad).
- Baselines re-measured at start under containment: `test_budget_admission`
  67/0, `test_billing_closure` 388/0/0, `test_campaign_billing_record` 128/0,
  `test_spend_gate` 167/0, `test_package_invariants` 261/0/0,
  `test_storage_run_metrics_flush` 130/0. All matched.

## 2. Containment

- OS sandbox (`sandbox-exec`, E1's profile re-pathed to this session):
  outbound denied except loopback, the `mDNSResponder` socket denied, writes
  and data reads denied under both production directories,
  `09- Testing/Evaluation Runs`, `04- Results` and a decoy.
- `sitecustomize` audit-hook tripwire (E1's, unchanged).
- Isolated project root provisioned with `provision_ci_paths.py`.
- Fired before any provider-touching import:
  - Arm A (sandbox + tripwire): external connect, raw socket, DNS, protected
    sqlite read-only connect, protected write, protected listing all refused;
    loopback allowed; production listings and the checkpoint read refused;
    the tripwire logged each refusal.
  - Arm C (sandbox only, tripwire off, `env -i`): the same probes refused by
    the sandbox alone.
- Every later run used both protections. The firing controls (section 8) run
  each plant under the same sandbox and tripwire, with the editable finder
  stripped. **No arm removed both protections, and no arm escaped: every
  tripwire log across every suite and control run holds zero external
  records** (loopback and database-watch records only).
- No paid call, no network egress, no commit, no stash, no branch change.

## 3. Premise verdicts (driven on the unchanged code)

### Item 1 -- reservation replay counted twice: CONFIRMED

`scratchpad/premise_replay.py`, every scenario a fresh process, temp database,
cap $10:

| scenario | result on unchanged code |
|---|---|
| `spend.begin_billed_attempt` $9, commit succeeds then the acknowledgement fails once (retryable `database is locked` raised after `commit()`) | the write-retry re-ran the transaction, counted its OWN committed $9 as held, and declined `headroom_held`; one RESERVED $9 row left behind; nothing held in process; one decline counted |
| process B replays the committed $9 reservation (same attempt id, same fields) | `BillingAdmissionDeclined` `headroom_held` |
| process C replays the same id with $5 under admission | `headroom_held` -- the MISMATCH is masked by the spurious decline |
| process C replays the same id with $5 without admission | `BillingRecordWriteError` (the non-admission read-back already refuses) |
| `evaluation.call_matching_model`, cap 1.5x the bound, acknowledgement failure | `Stage5SpendStopped`, **0 provider calls**, one RESERVED row at the bound left stranded |

Consequence: a lost acknowledgement fails the patient and strands a reserved
row that every reader charges at its upper bound for the life of the campaign.

### Item 2 -- held declines fail patients: CONFIRMED

E1's simulation (section B of its `throughput.py`, unchanged) re-run on the
unchanged code (`evidence/sim_before.json`). A `headroom_held` decline fails the
patient and never latches:

| committed before | assumed 2.0/1.5: completed / failed_held (cache working, absent) | no-premium: completed / failed_held |
|---|---|---|
| $0, $60 | 48/0, 48/0 | 48/0, 48/0 |
| $83 | 40/8, 37/11 | 48/0, 48/0 |
| $120 | 8/40, 15/33 | 48/0, 48/0 |
| $190 | 2/46, 3/45 | 33/15, 31/17 |
| $250 | 0/48, 2/46 | 0/48, 3/45 |
| $285 | 0/48, 0/48 | 1/47, 0/48 |

The counts move by a few patients between runs (thread timing); the pattern
matches E1's.

## 4. Item 1 -- the fix

- Owner: `database_logger.reserve_billing_attempt_outcome`. The old
  `reserve_billing_attempt` is now a wrapper returning the attempt id.
- ONE `BEGIN IMMEDIATE` transaction for the lookup, the admission and the
  insert, on both the admission and the non-admission path. The attempt id is
  looked up FIRST:
  - no row: admission is decided and the row inserted (outcome `written`);
  - a row identical in every immutable field: nothing inserted, no admission,
    outcome `replayed`, logged `billing_reservation_replayed`;
  - a row that disagrees: `BillingReservationConflict` (a
    `BillingRecordWriteError`) naming the fields; nothing written or reused.
- Fields compared for an attempt row: campaign, run, kind, source, model, both
  reserved token counts, reserved amount, reservation note, and state (must
  still be `reserved` -- a settled attempt cannot be replayed).
  `correlation_id` and timestamps are not compared. Historical and
  settlement-discrepancy rows keep their writers' idempotence: campaign, run,
  kind, amount, settled state and settled amount (plus outcome for historical).
- A replay is an ACCOUNTING operation, not permission to send. Every production
  dispatch creates a new attempt id (`spend.BillingRecord.reserve`), so the only
  production replay is the write retry of a transaction whose commit landed and
  whose acknowledgement was lost. Nothing has been dispatched against it.
- The process authority applies the same rule: `SpendLedger.admit_hold` with a
  token already held for the same source and amount admits without doubling;
  the same token for anything else raises `ValueError`.
- Verified at the probe level on the fixed code (`evidence/premise_after.json`,
  fresh processes): acknowledgement loss -> admitted, one row, $9 held once;
  process B replay -> `replayed`, one row; both mismatches -> conflict naming
  `reserved_usd`; Stage 5 -> exactly one provider call, one settled row.

## 5. Item 2 -- the bounded wait

**Where it sits.** The wait lives between two admission checks of one wire
attempt:

1. `provider_resilience.execute` takes a paced slot, then calls the attempt
   record's `begin()` (the admission).
2. On a `headroom_held` decline it REFUNDS the pacer permit (settled not billed),
   then asks the record's `await_admission(exc, cancelled)`.
3. `_Stage5AttemptRecord.await_admission` delegates to `spend.HeadroomWait`.
4. On `recheck` the policy re-asks cancellation, then loops back to step 1
   without spending a policy attempt (nothing was sent). On `cancelled` it
   raises the cancellation exception. A timeout raises `Stage5SpendStopped`
   with limit `admission_wait`.

**Locks.** During the wait nothing is held: the ledger lock, the database
transaction, `_WRITE_LOCK`, every liability's resolve lock and the pacer permit
are all released. The only lock taken is `spend.ADMISSION_QUEUE`'s own condition
lock, which `Condition.wait` releases while sleeping and which is never held
while anything else is called.

**Timeout.** `config.admission_wait_timeout_seconds()` =
`ADMISSION_WAIT_RELEASE_ROUNDS (2) x MATCHING_REQUEST_TIMEOUT_SECONDS (300) x
SDK attempts per call (1)` = **600 s** as shipped, overridable by
`ADMISSION_WAIT_TIMEOUT_SECONDS` (validated: positive, finite, not bool).
Argument: every hold present when a waiter queued belongs to an attempt on the
wire, which is released within one request read budget, and the attempts
admitted ahead of it finish within a second. **Uncalibrated.**

**Deadline.** ONE monotonic deadline per wait, set when the attempt first enters
the queue. A recheck that is declined again reuses the same `HeadroomWait` and
the same deadline. A later wire attempt of the same logical call gets a new
wait.

**Cancellation interval.** `config.PROVIDER_WAIT_POLL_SECONDS` = **0.25 s**. The
predicate is Stage 5's existing `_stage5_cancellation`: shutdown flag
(SIGTERM/Ctrl-C), the operator's drain where it applies, every spend stop, cap
exceeded.

**Wake-ups.** A settling liability notifies the queue, after the ledger charge
and again after the durable settlement. The head waiter then asks a READ-ONLY
preview: `SpendLedger.admission_preview`, or `database_logger.
preview_billing_admission` through the sink's `admission_preview`. Only a
positive preview sends it back for a paced slot and a real admission. Releases
by another process notify nothing, so the head also rechecks every
`config.ADMISSION_WAIT_RECHECK_SECONDS` = 1.0 s. That is one read-only query per
second per waiting budget.

**Fairness.** First-in, first-out within the process. While waiters are queued
on a budget, a waiting-capable attempt that is not the head is declined
`headroom_held` (detail "N earlier attempt(s) in this process are waiting")
before it can take released headroom, and joins the queue. Queue entries expire
at deadline + timeout, so an entry whose thread died is dropped and counted
(`SPEND_LEDGER_FAULTS["admission:stale_waiter_dropped"]`).

**Timeout outcome.**
- Campaign policy: `SPEND_STOP.trip(SPEND_LIMIT_ADMISSION_WAIT)`.
  - The runner stops starting patients and cancels queued ones.
  - `evaluation._spend_gate` and `spend.require_budget` refuse further
    dispatches at `admission_wait` (the cap comparison would not, because the
    budget is not spent).
  - The attempt's patient fails its Stage 5 call and is NOT checkpointed. Its
    unfinished work is neither completed nor clinically failed.
  - The run row is STOPPED with `stop_reason = admission_wait`, a fifth
    `RUN_STOP_REASONS` member; the ablation study's restated vocabulary gained
    it too. The banner and the runner's closing block say to resume, not to
    raise the cap.
- Rolling-window policy (API/MCP): no latch; only that request is refused.

**Permanent exhaustion** (`budget_exhausted`) is unchanged: no wait, latches
`spend_cap`.

**Counter.** `SPEND_ADMISSION_WAITS` (registered), `{source}:{outcome}`:
`entered`, `admitted`, `timed_out`, `cancelled`, `exhausted`, `failed`.

**Not covered.** Stage 2's embedding, the rater and any direct
`begin_billed_attempt` caller keep E1's immediate decline and are not queued.
`execute_async` does not wait (nothing async passes an attempt record).

## 6. Tests and results (all under the sandbox and the tripwire, isolated root)

**New file: `tests/test_admission_replay_and_wait.py` -- ⟨PENDING-NEWCOUNT⟩,
bucket A.** What it drives:

| requirement | checks |
|---|---|
| replay adds no liability and no provider call | 2a-2f, 2i, 2j |
| commit succeeds, acknowledgement fails, retry -- in process and through Stage 5 | 2g, 2h |
| the same, in FRESH processes: commit-then-die, replay, mismatch, new attempt, Stage 5 ack loss | 3a-3e |
| a waiter holds nothing: no provider call, no permit, no hold; the ledger lock, `_WRITE_LOCK` and the queue lock all acquirable during the wait | 4a-4c |
| a real worker releases headroom and the waiter is admitted (process and durable authority) | 4d, 4e |
| the deadline is not reset by rechecks; timeout stops the run cleanly; nothing further is issued | 4f-4i |
| serving policy: timeout refuses one request, no latch | 4j |
| shutdown, spend stop and drain interrupt the wait within one poll interval; drain does not cancel where it does not apply | 4k-4n |
| a stop seen by the retry policy after a positive preview ends the wait `cancelled` (self-review fix) | 4n-i |
| permanent exhaustion latches and does not wait | 4o |
| FIFO, with its in-process control | 4p, 4p-i |
| cap boundary below / at / above | 4q |
| settle once, release once across several waiters | 4r |
| a process killed while waiting reserved nothing; a fresh process counts the holder once | 4s-4u |
| real `main()`, `workers=2`: timeout -> run STOPPED `admission_wait`; checkpoint holds only completed patients; a fresh process resumes exactly the unfinished ones; completed work preserved; clean control | 5a-5g |
| the real Stage 5 node after the latch issues no request | 6a |
| isolation restored | 7a-7d |

E1's death-before-commit and death-after-commit checks (section 7 of
`test_budget_admission.py`) ran green at 67/0.

**Affected suites on the final production code** (after the D1 fix, one
concurrent batch, zero tripwire records): `test_admission_replay_and_wait` 71/0,
`test_provider_resilience` 203/0/0, `test_agent_stage5_per_trial_calls` 372,
`test_agent_stage5_attempt_provenance` 55, `test_agent_bedrock_anthropic_per_trial`
213, `test_spend_gate` 167, `test_budget_admission` 67, `test_billing_closure`
388/0/0, `test_campaign_billing_record` 128, `test_package_invariants` 261/0/0,
`test_degradation_counter_readers` 160. The new test's final run, after the D2
timeout edit, is in section 12.

**Earlier batches (before the self-review fix), all green:**
`test_budget_admission` 67, `test_billing_closure` 388/0/0,
`test_spend_budget_split` 79, `test_storage_run_identity` 158 + 1 gated skip,
`test_storage_schema_guards` 136, `test_degradation_counter_readers` 160,
`test_spend_coverage` 169, `test_package_invariants` 261/0/0,
`test_storage_run_metrics_flush` 130, `test_spend_gate` 167,
`test_ablation_stop_and_lock` 161, `test_agent_stage5_per_trial_calls` 372,
`test_runner_crash_record_and_db_unification` 65, `test_api_shutdown_gate` 78,
`test_mcp_deidentified_responses` 99, `test_agent_stage5_attempt_provenance` 55,
`test_agent_bedrock_anthropic_per_trial` 213, `test_storage_write_durability`
121 + 1 gated skip, `test_campaign_billing_record` 128,
`test_runner_stop_switch` 146 and `test_runner_sigterm_shutdown` 95. The two
runner suites ran in the foreground, one after the other, with SIGINT at its
default disposition.

`static_checks.py`: 319 files compiled. `ci_test_buckets.py --check`:
consistent, 151 test files, 132 in bucket A.

**`test_provider_resilience`: one check fails under load, and the cause is a
race in its own harness, present at HEAD.** The check is "every attempt
started >= one paced interval after the one before". It passed 203/0 three
times on the HEAD production code and three times on the current code when run
alone. It failed (202/1) only when run beside other suites. `Scripted.converse`
reads the shared virtual clock when a call is logged, while other threads'
pacer waits advance that clock. So a thread descheduled between leaving the
pacer and logging records a later virtual time than its permit. A 20 ms real
delay planted before that read, in disposable copies, makes the check fail
identically on HEAD and on the current tree (2 runs each), and it is the only
check that fails. Not fixed here: it is a test-harness defect, not an E1b one.

## 7. The 48-patient simulation after the fix

`scratchpad/sim_after.py`: E1's scenario, now routed through the real
`provider_resilience.execute` and `_Stage5AttemptRecord` with a stand-in pacer,
timeout 600 s (`evidence/sim_after.json`).

| scenario | committed before | completed | failed_held | exhausted | stopped in flight | not started | latched | waits entered = admitted + cancelled + exhausted |
|---|---|---|---|---|---|---|---|---|
| assumed, cache working | $0-$60 | 48 | 0 | 0 | 0 | 0 | -- | 0 |
| | $83 / $120 / $190 / $250 | 48 | 0 | 0 | 0 | 0 | -- | 379 / 627 / 744 / 762 (all admitted) |
| | $285 | 24 | 0 | 1 | 11 | 12 | spend_cap | 569 = 551 + 17 + 1 |
| assumed, cache absent | $0-$60 | 48 | 0 | 0 | 0 | 0 | -- | 0 |
| | $83 / $120 / $190 / $250 | 48 | 0 | 0 | 0 | 0 | -- | 527 / 644 / 752 / 762 (all admitted) |
| | $285 | 12 | 0 | 0 | 12 | 24 | spend_cap | 251 = 227 + 23 + 1 |
| no-premium, cache working | $0-$120 | 48 | 0 | 0 | 0 | 0 | -- | 0 |
| | $190 / $250 / $285 | 48 | 0 | 0 | 0 | 0 | -- | 476 / 723 / 765 (all admitted) |
| no-premium, cache absent | $0-$120 | 48 | 0 | 0 | 0 | 0 | -- | 0 |
| | $190 / $250 | 48 | 0 | 0 | 0 | 0 | -- | 590 / 754 (all admitted) |
| | $285 | 24 | 0 | 1 | 11 | 12 | spend_cap | 415 = 393 + 21 + 1 |

- **failed_held is 0 at every baseline** (before the fix: up to 48 of 48).
- No wait timed out. Held and queued are 0 at the end of every run.
- At $285 the run reaches the cap: one attempt is declined `budget_exhausted`,
  the campaign latches `spend_cap`, and the queued waiters are cancelled by the
  latch. That is the exhaustion path working, not a wait failure.
- The simulation uses a stand-in pacer and instant provider responses, so wall
  time is not a production figure.

## 8. Firing controls (disposable copies, sandbox active, editable finder stripped)

Harness `scratchpad/controls_e1b_v2.py`. Each plant breaks ONE safeguard in a
copy of `oncotriage/` and `tests/`, asserts its anchor occurs exactly once and
that the planted file parses, and runs the new test against the copy. A
preflight confirms the copy is what imports. The live tree's touched files are
sha256-compared before and after.

⟨PENDING-CONTROLS⟩

## 9. Changes

| file | change |
|---|---|
| `oncotriage/storage/database_logger.py` | `reserve_billing_attempt_outcome` (lookup first, replay / conflict), `ReservationResult`, `RESERVATION_*`, `BillingReservationConflict`, `_reservation_replay_mismatches`, `preview_billing_admission`, `BillingRecordSink.admission_preview`, `RUN_STOP_REASON_ADMISSION_WAIT` |
| `oncotriage/spend.py` | `admit_hold` token replay rule; `admission_preview`; `SPEND_ADMISSION_WAITS` and its vocabularies; `SPEND_LIMIT_ADMISSION_WAIT`; `BudgetAdmissionWaitTimeout`, `AdmissionQueue`, `ADMISSION_QUEUE`, `HeadroomWait`; `AttemptLiability(admission_wait=)` with the FIFO check and queue notifications; `require_budget` refuses at the latch; banner |
| `oncotriage/provider_resilience.py` | the reserve / admit / wait loop in `execute`; `_await_admission`, `_end_admission_wait(cancelled=)`; restated verdict vocabulary |
| `oncotriage/agent/evaluation.py` | `_Stage5AttemptRecord` wait (`begin`, `await_admission`, `end_admission_wait(cancelled=)`); `_spend_gate` refuses at the latch; the warmup floor's sentence for an admission-wait stop |
| `oncotriage/config.py` | `ADMISSION_WAIT_TIMEOUT_SECONDS`, `ADMISSION_WAIT_RELEASE_ROUNDS`, `ADMISSION_WAIT_RECHECK_SECONDS`, `admission_wait_timeout_seconds()`; poll docstring |
| `oncotriage/batch/runner.py` | stop reason, `ADMISSION_QUEUE.reset()`, resume guidance |
| `oncotriage/ablation/study.py` | stop reason mapping and closing text |
| `oncotriage/degradation.py` | `SPEND_ADMISSION_WAITS` registered |
| `tests/test_admission_replay_and_wait.py` | new |
| `tests/test_spend_gate.py` | `SPEND_LIMITS` 4 members, counter set, 8d-i stop-reason pin (+ admission_wait), short wait timeout in `run_node` |
| `tests/test_spend_coverage.py`, `tests/test_campaign_billing_record.py` | vocabulary pin; conflict checked by `isinstance` |
| `.github/scripts/ci_test_buckets.py` | bucket A entry |
| `CLAUDE.md` | SUPERSEDED note on E1's section; E1b section at the end |

## 10. Self-review of the full diff

**Fixed during the review:**

- **D1 -- a policy-seen cancellation was counted `failed`.**
  - Severity: low.
  - Location: `provider_resilience.execute` (the `WaitCancelled` branch and the
    re-check after a `recheck` verdict) -> `_Stage5AttemptRecord.
    end_admission_wait`.
  - Trigger: a shutdown, spend stop or drain lands after the wait's last poll
    and before re-admission.
  - Consequence: the wait counter reports `failed` for a cancellation,
    contradicting its own definition; an operator reading the degradation
    report is sent looking for a fault.
  - Evidence: new check 4n-i on the unfixed code reported `(entered, cancelled,
    failed) = (1, 0, 1)`; fixed copy 71/0; control C15 restores the old call.
  - Fix: `end_admission_wait(cancelled=True)` on both cancellation paths.
- **D2 -- a planted defect made the new test sit out the shipped 600 s wait.**
  - Severity: medium (for the evidence, not production).
  - Location: `tests/test_admission_replay_and_wait.py`, sections without their
    own timeout, and its child processes.
  - Trigger: any regression that turns a replay into a held decline (control
    C1).
  - Consequence: the C1 control stalled over ten minutes in 2h instead of
    failing.
  - Evidence: the first control run's C1 log ends at "waiting for held budget
    headroom ... delay_s 600.0".
  - Fix: a file-wide 5 s wait timeout (restored by 7a) and the same default in
    child processes.

**Unresolved (not defects in the shipped behaviour, but limits):**

- **U1 -- a leaked hold converts churn into a stop.** A hold that is never
  released (the backlog's un-maintained liability case) now makes every Stage 5
  waiter wait the full timeout and then stops the run, where before E1b it failed
  patients immediately. Medium. Remedy is the backlog item, not the wait.
- **U2 -- a server request can block a worker for up to 600 s** under the
  rolling-window policy. Medium for the API and MCP surfaces.
- **U3 -- FIFO is Stage 5 only.** Embedding, rater and direct callers do not
  queue and can take released headroom ahead of queued Stage 5 waiters. Low.
- **U4 -- each positive-preview recheck spends a paced interval** (a refunded
  permit does not refund spacing). Low; only the head rechecks.
- **U5 -- under the durable authority the queued-decline message's committed
  and held figures come from the process ledger.** Low; the decision itself
  uses the right authority.
- **U6 -- the ablation study does not reset `ADMISSION_QUEUE` between studies
  in one process.** Low; entries end with their waits and stale ones expire.
- **U7 -- the timeout (600 s) and the 1.0 s recheck are uncalibrated.**
- **U8 -- pre-existing: a resumed batch run records FAILED** because
  `load_results()` includes the earlier run's errored entry; check 5e does not
  pin that status.
- **U9 -- the pacing race in `test_provider_resilience`** (section 6).

**Unverified:**

- Cross-process release under the durable authority is covered by the 1 s
  recheck and by 4e (one process); two processes contending for one budget
  with one waiting was not driven.
- The wait was driven through the real retry policy and the real attempt
  record (`s5_call`), through the real node after a latch (6a) and through the
  real `main()` (section 5); a full per-trial wave waiting on the Converse arm
  was not driven.
- No production-scale timing of the wait or of the 1 s durable recheck.

## 11. Carried forward unchanged

- (a) The Sonnet 4.6 long-context pricing statement remains an open external
  requirement.
- (b) The cap may be insufficient if caching fails, and the judge budget may
  not cover a full 100-patient pass -- both projections.
- (c) Check 7z-iii at line 3478 aborts under load, and the combined checks must
  establish WHY its child returned no seed.
- (d) Backlog: the maintained per-campaign liability total, the sibling-latch
  cross-directory case, the call-ceiling claim consumed by a decline, held spend
  absent from `report_lines()`.

## 12. End state

⟨PENDING-END⟩

## 13. Exact next action for the combined-checks session

Under the same containment (sandbox + tripwire + isolated root), on this
working tree:

1. Run full CI bucket A through `ci_test_buckets.py --run A`, then the serial
   runner (bucket B), `fixture_replay.py` and
   `test_agent_retrieval_observability.py`, all deferred here.
2. Establish why check 7z-iii (line 3478) aborts under load: its child returned
   no seed.
3. Decide whether U1 (leaked hold -> timeout stop) needs the maintained
   per-campaign liability total before a paid run.
4. Decide on U9: fix the `test_provider_resilience` harness race (log the
   permit's start time rather than reading the shared clock at log time), or
   run that suite alone in the combined checks.
