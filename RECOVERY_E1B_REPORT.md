# RECOVERY E1b -- admission follow-up (reservation replay, bounded wait)

Status: **⟨PENDING-STATUS⟩ -- INCOMPLETE: never filled before the session died. See section 14.**

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

**New file: `tests/test_admission_replay_and_wait.py` -- ⟨PENDING-NEWCOUNT⟩ (INCOMPLETE: never filled; section 14.3 records the saved runs),
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

⟨PENDING-CONTROLS⟩ -- INCOMPLETE: never filled. The harness finished at 12:39:37, after this report was last written (12:16:26), and no verdict was recorded. Section 14.3 inventories the saved results.

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

⟨PENDING-END⟩ -- INCOMPLETE: never filled. The only saved end-state evidence predates the final production edit; see section 14.6.

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

## 14. Recovery inventory (2026-09-14, after the context death)

Written for: the operator and the next verification session. This section is a
read-only inventory. Nothing was re-run, no source was edited, no project module
was imported, and this report is the only file written. Sections 1-13 above are
preserved as the previous session left them; the four placeholders are marked
INCOMPLETE in place and were NOT completed from the diff.

### 14.1 Revision and working tree

- Branch `wip/billing-closure`, HEAD `81fd5c7` (12:43:16, "WIP: E1b partial,
  context death mid-execution. NOT FOR PUSH"), parent `bf546f4` (E1).
  `git status --short` is empty, so the committed tree IS the working tree.
- `81fd5c7` changes 15 files (+3091/-95); the list matches section 9.
- Last working-tree modification times of those files (nothing is later):

  | time | file |
  |---|---|
  | 11:35:12-11:35:38 | `spend.py`, `config.py`, `storage/database_logger.py`, `degradation.py` |
  | 11:37:17, 11:37:27 | `batch/runner.py`, `ablation/study.py` |
  | 11:42:51 / 11:48:17 / 11:54:15 / 12:00:26 | `test_spend_coverage.py` / `ci_test_buckets.py` / `test_campaign_billing_record.py` / `test_spend_gate.py` |
  | **12:12:19** | `agent/evaluation.py`, `provider_resilience.py` (the D1 fix) |
  | **12:13:41** | `tests/test_admission_replay_and_wait.py` (the D2 timeout edit) |
  | 12:16:26 / 12:16:33 | this report / `CLAUDE.md` |

  "Final code" below means production as of 12:12:19 plus the new test as of
  12:13:41.
- Previous session's scratchpad: `/private/tmp/claude-501/<project-slug>/4c06bc5d-83aa-4669-837e-43666c429c5b/scratchpad`
  (`evidence/`, `copies/`, `controls_e1b_v2.py`, `run.sh`, `sx.sh`, `tw/`).
- The separate recovery archive was not found in the project root or in
  `15- Code Copies`. It was not searched for further; the committed tree is
  the recovery point.
- Byte comparison of the saved copies with the commit:
  - `copies/fc_fixed` (12:11:23) is identical to the committed
    `evaluation.py`, `provider_resilience.py`, `spend.py` and
    `database_logger.py`. It differs from the committed new test by 11 lines
    (the D2 edit).
  - `copies/fc_premise` (12:10:05) differs from the committed `evaluation.py`
    by 9 lines and `provider_resilience.py` by 15 lines (D1).
  - `copies/curdelay` (12:01:30) also differs in both files.

  **Any run that started before 12:12:19 did not test the D1 code.**
- No renderer module (`agent/patient.py`, `agent/prompts.py`, `constants.py`,
  `extraction/stage.py`, `utils.py`, `deid.py`) appears in
  `git diff --name-only ed61ff3 81fd5c7`.

### 14.2 What the saved logs can prove about containment

- `run.sh` does three things for each listed test:
  - runs them all CONCURRENTLY (`&`, then `wait`) under `sandbox-exec` with
    the tripwire on `PYTHONPATH`;
  - writes `<out>/<test>.log` and `<out>/<test>.tw.jsonl`;
  - appends `exit=N secs=S`.
- The tripwire opens its log only when it records something. A missing
  `.tw.jsonl` therefore means "zero records" only if the run was wrapped. The
  test logs do not record the wrapper; only the `exit=... secs=...` trailer
  identifies a `run.sh` run.
- Only two tripwire logs exist in the whole scratchpad, and neither holds an
  external connection:
  - `armA.tw.jsonl`, the containment probe, whose refusals are expected:
    connect-REFUSED 2, getaddrinfo-REFUSED 1, sqlite-REFUSED 1,
    open-write-REFUSED 1, connect-loopback 1;
  - `aff5/test_runner_crash_record_and_db_unification.tw.jsonl`:
    connect-loopback 26.
- Runs WITH the `run.sh` trailer: `base`, `aff1`-`aff5`, `aff7`.
- Runs WITHOUT it:
  - `aff6` (section 6 says foreground and sequential);
  - `final_new`;
  - `fix_cancel` (a custom `exit=` with no `secs`).

  For those three, sandbox and tripwire are CLAIMED, not evidenced.
- `run.sh` launches tests in an asynchronous list. CLAUDE.md records that a
  shell `&` sets SIGINT to SIG_IGN for the children. By their labels, the E1b
  shutdown checks set a flag rather than deliver a signal; that was not
  verified.

### 14.3 Evidence matched to the code version it tested

| evidence | window | code it covers | recorded result | status |
|---|---|---|---|---|
| `base/` | 11:21-11:23 | `bf546f4`, before any E1b edit | budget_admission 67/0, billing_closure 388/0/0, campaign_billing_record 128/0, spend_gate 167/0, package_invariants 261/0/0, run_metrics_flush 130/0 | supports section 1 baselines (pre-change) |
| `digests_start.json` | 11:20:36 | pre-change | renderer `5ea2c6cc...`, PV 1.11.0, FV 8, prompts `8baaa8b8...` / `db3c2b4d...` | supports section 1 |
| `prod_inventory_start.txt`, `armA_*`, `armC_*` | 11:16-11:20 | n/a | files present; only the armA tripwire kinds were re-examined | section 2 start-of-session probes partly checked |
| `premise_replay.json`, `sim_before.json` | 11:21 | unchanged code | sim_before: 28 `failed_held` entries, max 48 | supports section 3 as a pattern; table values not re-derived |
| `premise_after.json` | 11:37:58 | replay fix present (`spend.py` and `database_logger.py` final since 11:35); `evaluation.py` and `provider_resilience.py` PRE-D1 | scenarios: ack_fail_inprocess, commit_only, replay_same (one reserved $9 row each), two mismatch cases, stage5_ack_fail (one settled row) | stale for the Stage 5 scenario; the mismatch `reason` fields read None, so the "conflict naming `reserved_usd`" claim was not located |
| `sim_after.json` | 11:38:59 | PRE-D1 | 28 `failed_held` entries, max 0; no `timed_out` field present; `.err` has 0 tracebacks | STALE for the final code |
| `new1`-`new3`, `aff1`-`aff3`, `pr_solo` | 11:38-11:58 | intermediate trees | several non-green: aff1 spend_gate exit 1 after 604 s; aff1 spend_coverage exit 1; new1 exit 1; aff2 campaign_billing_record exit 1; aff2 spend_gate no summary and no trailer (incomplete); aff3 spend_gate exit 1; new3 and pr_solo provider_resilience 202/1 | superseded iterations; not evidence for the final code |
| `pr_diag/` | 11:56-11:58 | copies `headprod` and the tree at the time | head1-3 and cur1-3 each 203/0/0 | supports part of U9 |
| `pr_race/` | 12:01-12:02 | `headdelay` / `curdelay` copies (pre-D1) | each 202/1/0 | supports U9's delay reproduction |
| `aff4/` | 12:00:43-12:02:42 | PRE-D1, pre-D2 | spend_budget_split 79, run_identity 158, counter_readers 160, schema_guards 136, spend_gate 167, spend_coverage 169, run_metrics_flush 130, budget_admission 67, package_invariants 261, billing_closure 388; all exit 0 | matches the section 6 "earlier batches" list, correctly labelled pre-fix; does NOT cover the final code |
| `aff5/` | 12:03-12:04 | PRE-D1, pre-D2 | mcp_deid 99, attempt_provenance 55, write_durability 121, bedrock_per_trial 213, api_shutdown_gate 78, crash_record 65, per_trial_calls 372, ablation_stop_and_lock 161, campaign_billing_record 128; **provider_resilience 202/1 (exit 1)** | as above |
| `aff6/` | 12:05-12:06 | PRE-D1, pre-D2 | runner_stop_switch 146/0, runner_sigterm_shutdown 95/0 | as above; no wrapper trailer |
| `static/` | 12:02:39 | pre-D1, pre-D2 | `static_checks.py` 319 files compiled; buckets consistent, 151 files / 132 in A | STALE for the final tree |
| `end/` | 12:06:54-57 | PRE-D1 | `digests_end.json` equal to start; `prod_inventory.diff` empty | STALE as an end state (see 14.6) |
| `controls_e1b/` (v1) | 12:06-12:21 | pre-D1 | clean 68/0; a C1 log at 12:21:32 (the 600 s stall behind D2); `results.json` and `harness.err` are 0 bytes | superseded; v1 never finished |
| `fix_cancel/` | 12:10-12:11 | `fc_premise` (pre-D1) / `fc_fixed` (final production, pre-D2 test) | premise 70/1 failing 4n-i; fixed 71/0 | supports D1's evidence claim in section 10 |
| `aff7/` | started 12:12:36 via `run.sh`, concurrent | **final production** | admission_replay_and_wait 71/0 (12:12:36-12:13:31, **before the D2 edit at 12:13:41**); bedrock_per_trial 213/0; counter_readers 160/0; attempt_provenance 55/0; spend_gate 167/0; provider_resilience 203/0/0; budget_admission 67/0; per_trial_calls 372/0; package_invariants 261/0/0; campaign_billing_record 128/0; billing_closure 388/0/0; all exit 0; no tripwire log | verified on the final production code. These suites' test files are unchanged since 12:00:26 or earlier; the new test ran pre-D2 |
| `final_new/new.log` | 12:16:12-12:16:43 | **final production + final test**; imports the live `03- Code/oncotriage` | RESULTS: 71 passed, 0 failed | result verified on the final tree; containment unevidenced (no trailer, no tripwire log) |
| `controls_e1b_v2/` | 12:16:43-12:39:37 | final code, copied at run time; `live_tree_unchanged: true` | see the table below | harness finished; never written up |

`controls_e1b_v2/results.json`, as recorded:

| plant | exit | imported copy | summary | recorded failed checks | aborted |
|---|---|---|---|---|---|
| clean | 0 | yes | 71/0 | -- | no |
| C1 replay lookup removed | 1 | yes | 63/8 | 2b 2d 2e 2g 2h 3b 3c 3e | no |
| C2 replay validation removed | 1 | yes | 68/3 | 2d 2e 3c | no |
| C3 no wait | 1 | yes | 50/21 | 4a 4c-4n 4n-i 4p-4s 5a-5c | no |
| **C4 deadline reset per recheck** | **timeout (600 s)** | **not confirmed** | **none** | **none** | **yes** |
| **C5 cancellation ignored** | 1 | yes | **none** | 4k | **yes** |
| C6 no FIFO | 1 | yes | 70/1 | 4p | no |
| C7 timeout does not latch | 1 | yes | 66/5 | 4h 4i 5a-5c | no |
| C8 Stage 5 gate ignores wait latch | 1 | yes | 69/2 | 4i 6a | no |
| C9 write lock held across wait | 1 | yes | 69/2 | 4c 4e | no |
| C10 permit held during wait | 1 | yes | 68/3 | 4b 4d 4n-i | no |
| C11 runner stop reason is cap | 1 | yes | 70/1 | 5b | no |
| C12 hold-token replay doubles | 1 | yes | 70/1 | 2i | no |
| C13 require_budget ignores wait latch | 1 | yes | 70/1 | 4i | no |
| C14 settled attempt replayable | 1 | yes | 70/1 | 2e | no |
| C15 policy cancel counted failed | 1 | yes | 70/1 | 4n-i | no |

- **C4 did not fire as a recorded failure.** The run was killed by the harness
  after 600 s (log 12:22:33-12:32:34).
  - The log has no RESULTS line and no PASS/FAIL lines at all: stdout was
    lost at the kill, while stderr JSON survived.
  - `imported_copy: false` only because the origin marker was never flushed.
    The surviving `[Paths]` line shows the C4 copy's `settings.py`.
  - The log carries 2,337 "a billed attempt was not dispatched because budget
    admission declined it" warnings, reason `headroom_held`.
  - So under this plant the new test HANGS, despite D2's 5 s file-wide
    timeout. Which check hangs is unknown.
- **C5 fired in the abort shape.** Check 4k FAILED, then an uncaught
  `BudgetAdmissionDeclined` ended the file with no summary.
- **`tripwire_records: 0` for every control is weaker than it reads.** The
  harness counts an ABSENT tripwire log as 0 and drops loopback and sqlite-watch
  records. No control tripwire log survives, so 0 cannot be told apart from an
  unarmed run.
- The previous session wrote no interpretation of these results (section 8 is
  empty). The table above is transcription, not a verdict.

### 14.4 Placeholders and unfinished text

- Line 3 status, the section 6 new-test count, section 8 controls and section 12
  end state were never filled. Each is now marked INCOMPLETE in place.
- Section 6 says "The new test's final run, after the D2 timeout edit, is in
  section 12". Section 12 is empty. The run exists (`final_new`, 71/0) but was
  never written up.
- Section 13's "exact next action" predates the controls run and the end state,
  and was not revised.

### 14.5 Superseded or unsupported claims

1. **Section 6, "Affected suites on the final production code ...
   `test_admission_replay_and_wait` 71/0".** That `aff7` run ended at
   12:13:31, before the D2 test edit. Superseded by `final_new` (71/0), the only
   run of the new test on the final tree.
2. **Section 2, "every tripwire log across every suite and control run holds
   zero external records".** The two surviving logs support it. Every other
   "zero" is inferred from a log's absence, and for `aff6`, `fix_cancel` and
   `final_new` the wrapper that would arm the tripwire is not evidenced.
3. **Section 4, "both mismatches -> conflict naming `reserved_usd`".** The
   `reason` fields in `premise_after.json` read None, and the conflict detail
   was not found in the fields read. Unverified. The file also predates D1.
4. **Section 6 / U9, "passed 203/0 ... when run alone. It failed (202/1) only
   when run beside other suites".** `pr_solo/test_provider_resilience.log`
   (11:55:04-11:55:19, `run.sh`) reports 202/1. No evidence was found that
   anything else was running in that window. The contradiction is unresolved.
5. **Section 7, the 48-patient simulation.** It ran at 11:38, pre-D1. It is
   not evidence for the final code.
6. **Section 6, static checks (319 compiled; 151 / 132 bucket A).** Pre-D1 and
   pre-D2.
7. **CLAUDE.md, E1b section (written 12:16:33, committed in `81fd5c7`):**
   - "The production files were compared by bytes at session end": the only
     comparison is `end/` at 12:06:57. That is before D1, `aff7`, `final_new`
     and the 23-minute controls run. Unsupported as stated.
   - "`PROMPT_VERSION`, `FINGERPRINT_VERSION` and
     `llm_classifier_renderer_digest` are unchanged": compared only at 12:06
     (pre-D1). Supported structurally, since no renderer module is in the
     E1/E1b diff; not recomputed on the final tree.
   - "held failures: 0 at every baseline": from the pre-D1 `sim_after`. Stale.
   - "`python tests/test_admission_replay_and_wait.py  # 71`": matches
     `final_new`.
   - Counts that match final-code evidence: `test_spend_gate` 167 and
     `test_campaign_billing_record` 128 (`aff7`).
   - Count on pre-D1 evidence only: `test_spend_coverage` 169 (`aff4`).
   - It says nothing about firing controls, so it does not overclaim C4.

### 14.6 End-state claims (not recomputed)

- **The report** makes no end-state claim; section 12 is empty.
- **CLAUDE.md** claims unchanged renderer and prompt digests, and production
  files compared by bytes at session end.
- **Saved evidence:** `end/digests_end.json` equals `digests_start.json`, and
  `end/prod_inventory.diff` is empty, both at 12:06:57. That is before the last
  production edit (12:12:19) and before every run from 12:12:36 to 12:39:37.
- **Verdict:**
  - digests unchanged: plausible, since no renderer module was edited;
    UNVERIFIED on the final tree;
  - production byte-identity at session end: UNVERIFIED.

### 14.7 Remaining verification, smallest batches in order (not run)

Every batch runs under sandbox + tripwire + isolated root on `81fd5c7`. Record
the wrapper and a tripwire log path for every run. Signal-sensitive suites run in
the foreground, never under `&`.

1. **B1 -- C4 and C5 diagnosis, no edits.**
   - Re-run the C4 plant alone with unbuffered stdout (`python -u`) and a short
     harness timeout, to name the check that hangs.
   - Re-run C5 alone, to name the statement that raises after 4k.
   - Output: whether the new test needs a bound or guard. That decision, and
     any test-code change, is a separate item and precedes every batch below.
     Otherwise all new-test evidence would have to be taken twice.
2. **B2 -- the new test on the tree that results from B1:**
   `test_admission_replay_and_wait` once, wrapped, with a tripwire log; then
   the full controls v2 harness again.
3. **B3 -- suites not run since the D1 edit:** `test_spend_coverage`,
   `test_spend_budget_split`, `test_storage_run_identity`,
   `test_storage_schema_guards`, `test_storage_run_metrics_flush`,
   `test_ablation_stop_and_lock`, `test_api_shutdown_gate`,
   `test_mcp_deidentified_responses`,
   `test_runner_crash_record_and_db_unification`,
   `test_storage_write_durability`; then `test_runner_stop_switch` and
   `test_runner_sigterm_shutdown` in the foreground, one after the other.
4. **B4 -- U9:** `test_provider_resilience` alone, three times, with nothing
   else running, to settle the `pr_solo` contradiction.
5. **B5 -- claims resting on pre-D1 runs:** `sim_after.py` and the premise-after
   probe on the final tree, recording the conflict detail fields; or remove the
   "held failures: 0" claim from CLAUDE.md.
6. **B6 -- end state:** recompute the renderer digest, `PROMPT_VERSION`,
   `FINGERPRINT_VERSION` and both prompt hashes; diff the production inventory
   against `prod_inventory_start.txt`; run `static_checks.py` and
   `ci_test_buckets.py --check`.
7. Then section 13's combined checks (bucket A, bucket B, `fixture_replay.py`,
   check 7z-iii, U1, U9).

### 14.8 Review of the recovered completion claims

- **Implementation.** The replay fix, the bounded wait, D1 and D2 are all in the
  commit. The copies confirm D1 was applied between 12:10 and 12:12:19, and D2
  after 12:11:23.
- **Tests on the final code.** Only two sets of runs cover the final production
  code:
  - `aff7`: eleven files, i.e. the new test (still pre-D2) plus TEN other
    suites (corrected in B1, 14.9.4; this line said "eleven suites");
  - `final_new`: the new test post-D2, with no containment evidence.

  Everything else predates D1.
- **Firing controls.**
  - 13 of 15 plants produced recorded failures on the final code with the copy
    confirmed imported.
  - C5 was caught only by an abort.
  - C4 hung and was not caught.
  - Section 10's statement that C15 restores the old D1 call is supported (C15
    fails 4n-i).
- **Simulation and premises.** `sim_after` and `premise_after` predate D1. Their
  results cannot be carried to the final code.
- **End state.** The only end-state snapshot is 33 minutes too early. No
  saved evidence shows production files byte-identical at session end.
- **Not inferred.** A started check is not treated as finished: aff2
  spend_gate, v1 C1 and C4 have no summaries. An earlier green run is not
  treated as covering later edits: aff4, aff5 and aff6 are pre-D1.

### 14.9 Batch B1 -- C4 and C5 diagnosis (2026-09-14, 13:00-13:04)

Written for: the operator and the session that applies the fix. Diagnosis only.
No production source and no test file was edited; no commit, stash, branch
change, reset or cleanup; no paid call; no production database was opened.
`git log -1` = `81fd5c7`; `git status --short` shows only this report.

Evidence root (this session's scratchpad):
`/private/tmp/claude-501/<project-slug>/e36de6c1-18c3-486c-afd9-cd2ff3fb8701/scratchpad`
-- `evidence/containment/`, `evidence/C4/`, `evidence/C5/` (each run has
`test.log`, `tw.jsonl`, `fault/fault.<pid>.txt`, `summary.json`), driver
`b1_run.py`, wrapper `sx.sh`, tripwire `tw/`.

#### 14.9.1 Containment, and the one permitted change

- **The fix (scratchpad harness only, never the pipeline).**
  - `sx.sh` appends a `sandbox-launch` record (pid, UTC time, profile path and
    sha256, cwd, argv) to the tripwire log BEFORE `exec`. `sandbox-exec` and
    `env` exec in place, so the launched python keeps that pid.
  - `_onc_tripwire.py` writes a `tripwire-loaded` marker from EVERY process
    that imports it (pid, ppid, argv, audit hook installed). The marker carries
    a sandbox-only probe: a write into `decoy_sandbox_only`, which the profile
    denies and the tripwire does not protect, so the sandbox alone decides it.
  - `sitecustomize.py` adds diagnosis-only per-process fault dumps: one
    all-thread dump after N s, and an all-thread dump on an uncaught exception.
- **Profile.** E1b's `nonet.sb` re-pathed to this scratchpad (outbound denied
  except loopback, `mDNSResponder` denied, writes and data reads denied under
  the four production directories and the decoy), plus one line denying writes
  to `decoy_sandbox_only`. Isolated root re-provisioned with
  `provision_ci_paths.py --root` under the wrapper (exit 0).
- **Fired before use (`evidence/containment/`).**
  - provision: launch pid 44722 = marker pid 44722; its child 44730 (ppid
    44722) also marked; both probes `denied errno=1`.
  - probe: DNS `getaddrinfo-REFUSED` (x2), protected-decoy sqlite
    `sqlite-REFUSED`, child marked; raw connect to 192.0.2.1:443
    `connect-REFUSED` ("tripwire: outbound refused").
  - negative control: `python -S` (no site, so no tripwire) under the same
    wrapper -> a launch record for pid 44819 and ZERO markers. An unarmed
    process is therefore detectable, not inferred.
  - `decoy_sandbox_only` and `decoy_protected` are empty after every run.
- **Two defects in my own wrapper, found by running it:** the first launch
  record broke JSON on a quote in argv, the second on a newline. Both are
  escaped now; every tripwire log from the C4/C5 runs parses.

#### 14.9.2 C4 -- deadline reset on each recheck

- **What ran.** The v2 plant `C4_deadline_reset_per_recheck` (imported verbatim
  from `controls_e1b_v2.py`; anchor count 1, parses) in a fresh copy of
  `oncotriage/` and `tests/`, `python -u`, foreground, alone. One-shot
  all-thread dump at 40 s; process group SIGKILLed at 75 s. Live tree sha256
  unchanged; `[e1b] oncotriage imported from` = the copy.
- **Result.** exit -9 (killed by the driver at 75.0 s), no RESULTS line,
  30 checks recorded, 0 failed, no traceback. Last check reached: **4e PASS**
  (log line 467).
- **Where it hangs.** `tests/test_admission_replay_and_wait.py:1030`, the 4f
  block:
  `_exc = raised(s5_call, 9.0, calls=_calls, pacer=CountingPacer())`.
  Parent stack at 40 s (single thread): `<module>:1030 -> raised:170 ->
  s5_call:346 -> provider_resilience.execute:1837 -> _await_admission:1566 ->
  evaluation.await_admission:2131 -> _recording_await:1017 ->
  spend.HeadroomWait.await_admission:2329 -> wait_for_change:2181`.
- **Why.** 4f rebinds `_admission_preview` to always answer "fits", so each
  wait returns RECHECK after ~`ADMISSION_WAIT_RECHECK_SECONDS`, `execute`
  re-enters `attempt_record.begin()`, which declines `headroom_held` again, and
  `await_admission` is called again. The plant sets `self.deadline = now +
  timeout_s` on every call, so `remaining` never reaches 0. The recheck loop in
  `execute` (lines 1818-1850) re-enters without consuming `max_attempts`, so
  nothing else ends it. Log: 270 `headroom_held` declines, 3 waits entered,
  2 ended (4d, 4e); the third (4f) never ends.
- **Why it hangs instead of recording a failure.** The only bounds on 4f are
  the wait's own deadline (the thing C4 breaks) and the file-wide 5 s default,
  which 4f overrides to 1.5 s through the same deadline. `raised()` has no time
  bound, and 4g's elapsed-time check runs only after the call returns. A bound
  that lives inside the code under test cannot bound a defect in that code.
- **Reused evidence.** v2's `C4_deadline_reset_per_recheck.log` (600 s kill,
  2,337 declines, no summary) ran the same plant on the same code: production
  final at 12:12:19, test final at 12:13:41, harness started 12:16:43, and
  `81fd5c7` is that tree. The re-run agrees and adds the check and the stack.
  v2's stdout loss was the missing `-u`; with `-u` all 30 check lines survived
  the kill.
- **Proposed minimal fix (PROPOSAL, not applied).** Run the 4f drive on a
  daemon thread and `join(1.5 + margin)` (e.g. 6 s). If it is still alive,
  record a failing check ("4f-0 the timed wait returned within its bound"),
  then end the planted loop through the path the plant leaves intact:
  `request_stage5_shutdown()`, join, `clear_stage5_shutdown()`. 4f-4i then
  record failures instead of the file hanging. Separately, the controls
  harness should run the test with `python -u`.

#### 14.9.3 C5 -- cancellation ignored

- **What ran.** The v2 plant `C5_cancellation_ignored` (anchor count 1,
  parses), same harness, alone. Dump timer 90 s, kill at 150 s (neither
  fired). Live tree unchanged; imported the copy.
- **Result.** exit 1 after 19.8 s, no RESULTS line, 39 checks recorded,
  **1 failed: 4k**, 1 traceback. 4k actual vs expected:
  `(True, False, True, '_Absent', [], 0)` vs
  `(True, True, False, 'Stage5ShutdownRequested', [], 1)` -- not prompt,
  thread still alive, no exception, no cancelled count.
- **The statement that raises.** `tests/test_admission_replay_and_wait.py:1087`,
  `holder = begin(9.0)` inside `cancel_case`, reached from the **4l** call at
  line 1111 (the `SPEND_STOP.trip` trigger). It raises
  `BudgetAdmissionDeclined [headroom_held, process authority]`: "$0.000000
  committed plus $9.000000 held by this process's open reservations plus its
  own $9.000000 would exceed" the $10 cap.
- **Why there is $9 held after `reset_spend()`.** The 4k waiter thread leaked
  into 4l.
  - Under the plant, the top-of-loop cancellation check is gone, and the
    mid-loop `if cancelled(): continue` skips every recheck while the shutdown
    flag is set. So the waiter ignores the flag and also never notices the
    holder's release.
  - `cancel_case` joins 5 s, resolves the holder, joins 5 s again, then calls
    `clear_stage5_shutdown()` and returns. It never checks that the thread
    ended.
  - With the flag cleared, the waiter's next recheck fits, `execute`
    re-admits it and it holds $9. Log order: 4k FAIL (line 500); waiter
    "wait ended, admitted" at 20:03:51.640 (line 503, the 5th and last wait
    ending: admitted x3 = 4d, 4e, 4k-waiter; timed_out x2 = 4h, 4j); 4l's
    decline at the same millisecond (line 504); traceback (line 505).
  - At the abort the all-thread dump shows ONE thread, so the waiter had
    dispatched and exited by then. The hold it took was still counted when
    4l's `begin` ran.
- **Why the file aborted instead of recording a failure.** `cancel_case` calls
  `begin()` bare, outside `drive()`/`raised()`, at module level, so a decline
  there is an uncaught exception. The harness helpers protect the calls INSIDE
  `check()` arguments, not the case's setup.
- **Reused evidence.** v2's `C5_cancellation_ignored.log` (4k FAIL, traceback
  ending in the same `cancel_case` line 1087 from the 4l lambda) is the same
  plant on the same code; the re-run matches it.
- **Proposed minimal fix (PROPOSAL, not applied).** In `cancel_case`:
  1. guard `holder = begin(9.0)` (return an `_Absent` marker and let the
     caller's check fail) instead of calling it bare;
  2. before returning, clear the shutdown flag, resolve the holder and
     `join()` the waiter with a bound; return `thread.is_alive()` after that
     join as part of the result, so a waiter that would leak fails its own
     check (4k-4n) and the next case's `reset_spend()` runs only after it has
     ended.

#### 14.9.4 Containment markers in both runs, and the count correction

- **Markers.** Both runs: the parent plus all five section-3 children
  (`commit_then_die`, `replay`, `mismatch`, `new_attempt`, `ack_fail_stage5`),
  each with `ppid` = the parent. Five is the full set by construction: the
  file's only other `run_child` / `runner_child` calls are in 4s-4u and
  section 5, which neither run reached.
  - C4: launch pid 44894 = parent marker 44894; children 44906, 44908, 44913,
    44918, 44920.
  - C5: launch pid 45125 = parent marker 45125; children 45137, 45145, 45150,
    45152, 45159.
  - All 12 processes: `sandbox_only_write: denied errno=1`, audit hook
    installed, editable finder stripped. Each wrote its own `fault.<pid>.txt`
    header, independently confirming the sitecustomize loaded.
  - No other tripwire records in either run: no external, no loopback, no
    sqlite-watch (the test's databases live in its own temp directory).
- **Left behind, not cleaned (cleanup is outside this batch).** The killed C4
  run and the aborted C5 run each left the test's temp directory:
  `$TMPDIR/oncotriage-admission-e1b-ljsa4i7v` and `...-s36rsdnq`. Three older
  ones from the previous session remain beside them. Both plant copies were
  removed by the driver.
- **Count correction (no rerun).** Section 14.8 said "`aff7`: eleven suites".
  `aff7` holds eleven logs: `test_admission_replay_and_wait` plus TEN other
  suites. Corrected in place. Section 6's list has the same eleven names and
  does not state a count.

#### 14.9.5 Noted for a later batch (not acted on)

- CLAUDE.md's E1b section overclaims the end state. It says production files
  were compared by bytes at session end, but the only comparison was `end/`
  at 12:06:57, before D1. Its "held failures: 0" rests on the pre-D1
  `sim_after` run.
- My own driver compared only the seven E1b-touched files before and after
  each run, not the production directories (the sandbox denies reads there);
  the sandbox and tripwire refusals are the evidence nothing reached them.

#### 14.9.6 Next single batch

**B1-fix:** apply the two test-harness proposals above to
`tests/test_admission_replay_and_wait.py` (and `python -u` in the controls
harness), then re-run the clean control, C4 and C5 alone under this
containment (launch record + per-process markers). Pass criteria: clean 71+/0,
C4 and C5 each end in a RESULTS line with recorded failures and no kill or
traceback. B2 onward (14.7) waits for it.

### 14.10 Batch B1-fix -- the two test-harness fixes, and three controls (2026-09-14)

Written for: the operator and the session that runs B2. Only
`tests/test_admission_replay_and_wait.py` and the scratchpad controls harness
were edited. No production source, no commit, stash, branch change or reset,
no paid call, no network egress; no production database was opened.
`git log -1` = `81fd5c7`.

Evidence root (this session's scratchpad):
`/private/tmp/claude-501/<project-slug>/8f4a06c3-ed8a-4caf-ac86-3a08394850c7/scratchpad`
-- `evidence/{clean,C4,C5}/` (`test.log`, `tw.jsonl`, `fault/`, `summary.json`),
`evidence/*.driver.out`, driver `b1fix_run.py`, the edit script
`apply_fix.py`, and `test_before_fix.py` (the test as committed).

#### 14.10.1 What changed and why

- **`halt_with_live_worker(where)` (new harness helper).** Records a failing
  check, prints the RESULTS block and ends the process with `os._exit(1)`.
  Called only when a worker thread is still alive after every bound and after
  the cancellation meant to end it, so no configuration restore, attribute
  rebind or `reset_spend()` ever runs underneath a live worker. It skips the
  file's own temp-directory removal on that path.
- **Fix 1, check 4f.** The `s5_call` now runs on a daemon thread joined for
  6 s (the wait's own timeout is 1.5 s; 4g already requires < 2.4 s).
  - If the thread is still alive: check `4f-0` is recorded as FAILED, the
    shutdown flag is set (the plant leaves the top-of-loop cancellation
    intact), and the thread is joined again for 6 s.
  - If it still has not exited: `halt_with_live_worker("4f-0b")`.
  - Only after the thread is confirmed exited is the flag cleared and the
    `with settings(...), rebound(...)` block allowed to restore.
  - `_exc` and `_elapsed` are read from the thread's result box. 4f-4i are
    unchanged.
  - On shipped code no extra check is recorded, so the count stays 71.
- **Fix 2, `cancel_case` (4k-4n).**
  - `holder = begin(9.0)` goes through `drive()`. A decline returns
    `entered False`, `took inf`, the absent marker as the box, and the cleanup
    tuple, so the caller's check fails instead of the file aborting.
  - `holder.resolve(...)` also goes through `drive()`.
  - The box and calls the checks read are SNAPSHOTTED after the existing second
    join, before cleanup, so cleanup cannot let a waiter that ignored its
    trigger dispatch and then read as a success (this protects 4n).
  - Cleanup: clear shutdown and drain, then join for the case's own wait
    timeout + 5 s (35 s). A SPEND_STOP stays latched, so such a waiter ends at
    its own timeout rather than being re-admitted. Any reservation the waiter
    took is resolved by the retry policy as it returns.
  - `cancel_case` returns a sixth value, `cleanup = (thread alive, open holds,
    campaign queue depth)`. 4k, 4l, 4m and 4n each gained that element with
    expected `(False, 0, 0)`; after each, a live thread calls
    `halt_with_live_worker`.
  - No existing element of 4k-4n was removed or relaxed; each tuple only grew.
- **Controls harness.** `controls_e1b_v2.py` (the E1b session's scratchpad)
  now launches the test with `python -u`; the original is saved beside it as
  `controls_e1b_v2.py.pre_b1fix`. It was NOT run in this batch. The three runs
  used `b1fix_run.py`: B1's `b1_run.py` with evidence and copies redirected to
  this scratchpad, plus the profile sha256 at run time. Plants still come
  verbatim from `controls_e1b_v2.py`, and the containment (`sx.sh`,
  `nonet.sb`, `tw/`, decoys, isoroot) is B1's, unchanged.
- **Cleanup.** B1's two killed-run temp dirs,
  `oncotriage-admission-e1b-ljsa4i7v` and `...-s36rsdnq` (13:02/13:03, test
  fixtures only, 1.5 MB each), were removed. The three older ones from the
  previous session (`2aq8yzbd`, `bcxkhuek`, `fqu95qug`) were left, as they are
  not B1's.

#### 14.10.2 Clean control

- `evidence/clean/`: exit 0, 32.2 s, not killed, **`RESULTS: 71 passed, 0
  failed`**, 71 checks, 0 tracebacks. The copy was imported; the live E1b
  files were unchanged before and after.
- Profile sha256 `0e78e401...` at run time equals the launch record's and B1's.
- Markers: launch pid 48244 = parent marker 48244 (ppid 48243, the driver).
  15 descendant processes, all marked: 11 children of 48244 (48258, 48263,
  48265, 48270, 48273, 48309, 48314, 48316, 48327, 48341, 48349) and 4
  grandchildren (48323, 48337, 48346, 48357). All 16 report
  `sandbox_only_write: denied errno=1`, audit hook installed and editable finder
  stripped. No other tripwire records: none external, none loopback, no
  sqlite-watch.

#### 14.10.3 C4 -- deadline reset on each recheck

- `evidence/C4/`: the plant was copied verbatim (anchor count 1, parses) and
  the copy was imported. Exit 1 after 36.6 s, not killed.
  **`RESULTS: 68 passed, 4 failed`**, 72 checks (4f-0 exists only on this
  path), 0 tracebacks, driver exit 0 inside a 400 s perl alarm.
- Recorded failures, all intended:
  - `4f-0`: actual `'still waiting'` after the 6 s join. The shutdown flag then
    ended the worker, since `halt_with_live_worker` was not reached.
  - `4g`: `(1, 24, False)` against `(1, 1, True)`, i.e. one wait object with
    24 distinct deadlines and elapsed out of range. This is the defect itself.
  - `4h`: `Stage5ShutdownRequested`, no latch, 0 `timed_out`, where
    `Stage5SpendStopped` naming `admission_wait` was expected. The plant never
    times out.
  - `4i`: no `admission_wait` latch, so neither gate refuses.
- Everything after 4i passed, including 4k-4n with cleanup `(False, 0, 0)` and
  section 5's runner children. B1's hang is now a recorded failure.
- Markers: launch pid 48392 = parent marker 48392 (ppid 48391). 15
  descendants, all marked: children 48406, 48409, 48411, 48416, 48418, 48458,
  48466, 48471, 48500, 48508, 48518; grandchildren 48487, 48504, 48517, 48530.
  All 16 report `denied errno=1`, audit hook installed, editable finder
  stripped. No other tripwire records. Profile sha256 `0e78e401...` at launch
  and at the end.

#### 14.10.4 C5 -- cancellation ignored

- `evidence/C5/`: the plant was copied verbatim (anchor count 1, parses) and
  the copy was imported. Exit 1 after 80.9 s, not killed.
  **`RESULTS: 68 passed, 3 failed`**, 71 checks, 0 tracebacks, driver exit 0.
- Recorded failures, all intended. 4k, 4l and 4m each read
  `(True, False, True, '_Absent', ...)`: entered, but not interrupted within
  one poll, the thread still alive after 5 s, and no exception. 4k also shows 0
  `cancelled` against 1.
  - **Each cleanup element read `(False, 0, 0)`.** The waiter exited and left
    no hold and no queue entry, so 4l no longer aborts on a leaked $9 hold, and
    each case failed only on its own interruption fact.
- 4n and 4n-i PASS under this plant. 4n-i's stop is seen by the retry policy
  after a positive preview, not by `await_admission`'s cancellation. This is
  new information: v2's C5 run aborted at 4l and never reached them.
- The 80.9 s (clean: 32 s) is the cleanup joins waiting out the planted
  waiters. The 4l waiter ends only at its own 30 s timeout, since SPEND_STOP
  stays latched.
- Markers: launch pid 48532 = parent marker 48532 (ppid 48531). 15
  descendants, all marked: children 48546, 48548, 48553, 48557, 48562, 48701,
  48706, 48711, 48722, 48730, 48735; grandchildren 48718, 48729, 48734, 48742.
  All 16 report `denied errno=1`, audit hook installed, editable finder
  stripped. No other tripwire records. Profile sha256 unchanged.
- All three runs removed their own test temp directory (check 7c passed in
  each), and `b1fix_run.py` removed each plant copy. Only the three
  previous-session temp dirs remain in `$TMPDIR`.

#### 14.10.5 Still unrecorded

- **`halt_with_live_worker` has never executed.** No run reached a live worker
  after cancellation (4f-0b) or a leaked waiter (4k-0 to 4n-0). The same holds
  for the failing branch of each cleanup element. These are guards verified by
  reading, not by a firing plant.
- **`controls_e1b_v2.py` with `-u` was not run.** The full 15-plant controls
  set on the fixed test is B2's.
- C1-C3 and C6-C15 were not re-run against the edited test. Their recorded
  failures in 14.3 are from the pre-fix test.
- C4 now reports 72 checks and every other run 71, because `4f-0` is recorded
  only when it fails. A harness comparing totals across plants must allow this.
- The CLAUDE.md run-block count (`# 71`) remains correct for the clean file.
  CLAUDE.md was not edited, and its end-state overclaims (14.9.5) are carried.

#### 14.10.6 Next single batch

**B2:** on this tree, under the same containment, run the new test once and
then `controls_e1b_v2.py all` (now `-u`), foreground. Pass criteria: clean
71/0; every plant, C4 and C5 included, ends in a RESULTS line with recorded
failures and no kill or traceback.

### 14.11 Batch B2 -- the full control matrix against the edited test (2026-09-14)

Written for: the operator and the session that runs B3. Run only. No production
source, no test file and no controls-harness file was edited; no commit, stash,
branch change, reset or cleanup; no paid call; no production database opened.
`git log -1` = `0f12013` (B1-fix committed); `git status --short` clean at start.

Evidence root (this session's scratchpad):
`/private/tmp/claude-501/<project-slug>/e54c04ff-c0ed-4e3e-a393-18b4fa056c0f/scratchpad`
-- driver `b2_run.py`, `evidence/<run>/{test.log, tw.jsonl, fault/, summary.json}`,
`evidence/batch*.driver.out`.

#### 14.11.1 Method and containment

- `b2_run.py` is B1-fix's `b1fix_run.py` extended to a list of runs. Plants are
  imported verbatim from the E1b session's `controls_e1b_v2.py` PLANTS table
  (anchor count 1 and `ast.parse` asserted per plant); B1's `sx.sh`, `nonet.sb`,
  `tw/`, decoys and isoroot are used unchanged. Each run launches
  `python -u tests/test_admission_replay_and_wait.py` in the foreground
  (`subprocess.Popen`, new session), one at a time, never under `&`.
- Timeouts: per-run driver kill at 240 s (`killpg SIGKILL`), and an independent
  outer `perl alarm 580` around each driver invocation. No run was killed.
- The driver's own SIGINT disposition was `default_int_handler` in every batch.
- Per run the summary records: launch record pid, parent tripwire marker pid,
  every descendant marker with its ppid, the sandbox-only write probe, audit
  hook, editable-finder strip, every non-marker tripwire record, the profile
  sha256, the import origin against the expected copy, the live tree's touched
  files sha256 before and after, and whether `halt_with_live_worker` printed.
- `live` = the new test against the live `03- Code` tree (EXTRA_PP = the code
  dir, import origin asserted); `clean` = the unplanted copy.

#### 14.11.2 Results

Every row below: no traceback, driver not killed unless stated, import origin
equal to the expected copy (or the live tree), live tree's touched files
byte-identical before and after, profile sha256 `0e78e401...`, 16 tripwire
markers (launch pid = parent marker; 11 children + 4 grandchildren of the
parent, none unattached), every marker `sandbox_only_write: denied`, audit hook
installed, editable finder stripped, and no non-marker tripwire record (no
external, loopback or sqlite-watch).

- **live** (new test, live tree): exit 0, 31.1 s, **`RESULTS: 71 passed, 0
  failed`**. Launch pid 51409; children 51421 51426 51430 51435 51437 51480
  51485 51487 51505 51514 51521; grandchildren 51501 51510 51520 51529.
- **clean** (unplanted copy): exit 0, 31.5 s, **71/0**. Launch 51530; children
  51542 51544 51552 51554 51559 51590 51592 51599 51610 51616 51624;
  grandchildren 51603 51614 51623 51631.
- **C1 replay lookup removed**: exit 1, 41.6 s, **63/8** -- 2b 2d 2e 2g 2h 3b 3c
  3e. Intended: 2b, 2g, 2h, 3b, 3e (a replay is declined `headroom_held`, e.g.
  2g actual `(False, 1, ..., {'stage5:headroom_held': 1})`, 2h/3e end with the
  row RESERVED and zero calls). 2d, 2e, 3c also fire, as a consequence the
  plant causes rather than an unrelated failure: with no lookup the conflict
  check is never reached, so a mismatch is declined by admission or written
  instead of raising `BillingReservationConflict`. Launch 51694.
- **C2 replay validation removed**: exit 1, 30.9 s, **68/3** -- 2d 2e 3c, all
  intended (2d/2e actual `NoneType` where `BillingReservationConflict` was
  expected; 3c replays a $5 request against a $9 row). Launch 51817.
- **C3 no wait**: first attempt KILLED by the driver's own 240 s bound (58
  checks, last PASS 4u, main thread in section 5's `run_child`; no hang in the
  test, see below); evidence kept as `evidence/C3_kill240/`. Re-run alone with
  a 560 s bound: exit 1, **247.1 s**, **50/21** -- 4a 4c-4n 4n-i 4p-4s 5a-5c.
  Intended: 4a, the wait's non-degeneracy ("the waiter entered the wait"),
  actual False, and every wait-dependent assertion after it (4g actual
  `(0, 0, False)`: no wait object; 4h latches `spend_cap` instead of
  `admission_wait`; 5b run row `FAILED` with no stop reason). Launch 52623.
  - The 240 s bound was this driver's, not the matrix's (v2 used 600 s); the
    pre-fix v2 run of C3 also finished (50/21). The kill left the test's temp
    dir `oncotriage-admission-e1b-gdmugy5i` in `$TMPDIR` (a killed run cannot
    run its own removal). Not removed: this batch does no cleanup.
- **C4 deadline reset per recheck**: exit 1, 38.8 s, **68/4, 72 checks** -- 4f-0
  4g 4h 4i, all intended. 4f-0 actual `'still waiting'` after the 6 s join; 4g
  `(1, 24, False)` (one wait object, 24 distinct deadlines -- the defect); 4h
  `Stage5ShutdownRequested`, no latch; 4i no `admission_wait` refusal. 4f-0b was
  NOT reached: the shutdown flag ended the worker. Launch 53196.
- **C5 cancellation ignored**: exit 1, 81.9 s, **68/3** -- 4k 4l 4m, all
  intended: each actual `(True, False, True, '_Absent', ...)` (entered, not
  interrupted within one poll, thread alive after 5 s, no exception); every
  cleanup element read `(False, 0, 0)`. 4n and 4n-i pass, as in B1-fix
  (4n-i's stop is seen by the retry policy after a positive preview). Launch
  53321.
- **C6 no FIFO**: exit 1, 35.7 s, **70/1** -- 4p, intended: actual
  `(['N'], ['N', 'W1'], ...)`, the new arrival dispatched ahead of the head
  waiter. Launch 53532.
- **C7 timeout does not latch**: exit 1, 89.2 s, **66/5** -- 4h 4i 5a 5b 5c.
  Intended: 4h actual `[False, None]` for the latch, 4i no refusal, 5b run row
  `('FAILED', None)` instead of `('STOPPED', 'admission_wait')`, 5c checkpoint
  2 where 1 was expected. 5a is the section-5 non-degeneracy
  (`(0, 1, 3, 2)` vs `(0, 1, 2, 1)`): with no latch the runner starts a third
  patient, so it fires as a consequence of the plant, not for an unrelated
  reason. Launch 53647.
- **C8 Stage 5 gate ignores wait latch**: exit 1, 31.7 s, **69/2** -- 4i 6a,
  intended. 4i actual `('NoneType', None, 'SpendLimitReached',
  'admission_wait')`: only the Stage 5 half of 4i fails, the non-Stage-5 gate
  still refuses. 6a actual `(5, [], False)`: the real node issued 5 requests
  after an admission-wait stop. Launch 53913.
- **C9 write lock held across wait**: exit 1, 52.6 s, **69/2** -- 4c 4e,
  intended. 4c actual `[True, False, True]` (the write lock not acquirable
  during the wait); 4e the durable settlement could not land (rows stay
  `reserved`). Launch 54014.
- **C10 permit held during wait**: exit 1, 31.7 s, **68/3** -- 4b 4d 4n-i, all
  on the intended property. The differing element is the pacer's outstanding
  permit count in each: 4b `([], 1, 1)` vs `([], 0, 1)`; 4d element 7
  (`_pacer.outstanding`) 1 vs 0; 4n-i last element of the waits/permit tuple
  `(1, 1, 0, 0, 1)` vs `(1, 1, 0, 0, 0)` ("no permit outstanding"). Launch 54187.
- **C11 runner stop reason is cap**: exit 1, 31.0 s, **70/1** -- 5b, intended:
  run row `('STOPPED', 'spend_cap')` while the latch reads `admission_wait`.
  Launch 54292.
- **C12 hold-token replay doubles**: exit 1, 30.4 s, **70/1** -- 2i, intended:
  actual `(True, False, 'NoneType', 'NoneType', 1, 9.0)`, the replay not
  recognised and the two mismatches not refused. Launch 54398.
- **C13 require_budget ignores wait latch**: exit 1, 31.1 s, **70/1** -- 4i,
  intended, and on the OTHER half from C8: actual `('Stage5SpendStopped',
  'admission_wait', 'NoneType', None)`, the non-Stage-5 gate no longer refuses.
  Launch 54547.
- **C14 settled attempt replayable**: exit 1, 30.6 s, **70/1** -- 2e, intended:
  actual `('NoneType', False)`, a settled attempt replayed instead of raising
  `BillingReservationConflict`. Launch 54657.
- **C15 policy cancel counted failed**: exit 1, 30.4 s, **70/1** -- 4n-i,
  intended: waits tuple `(1, 0, 1, 0, 0)` vs `(1, 1, 0, 0, 0)`, the wait counted
  failed instead of cancelled. Launch 54758.

Markers for C1-C15 (each run): launch pid = parent marker, 11 children and 4
grandchildren of the parent, 16 total, none unattached; full pid lists in each
`summary.json`. The C3 run killed at 240 s has 10 markers (section 5 had not
started its later children), all attached, all denied/hooked/stripped.
Decoys `decoy_sandbox_only` and `decoy_protected` empty after every run.

#### 14.11.3 Verdict against the acceptance criteria

- **Clean: 71/0**, on the live tree and on the unplanted copy.
- **Every plant ends in a RESULTS line, with no traceback.** 15/15. One kill
  occurred (C3 at the driver's 240 s bound); the unkilled re-run of the same
  plant finished at 247.1 s with a RESULTS line. It is a driver-bound
  artifact, not a hang: the killed run's main thread was in section 5's
  `run_child` at 200 s and had just passed 4u, and the v2 pre-fix run of C3
  also finished.
- **Every plant makes its intended assertion fail.** 15/15. Additional failures
  in C1 (2d 2e 3c), C3 (the whole wait block) and C7 (5a) are consequences of
  the plant itself, each traced above; none fails for an unrelated reason.
- **C4 reports 72 checks** (4f-0), as allowed; every other run 71.
- **The "redundant" exception.** No plant is documented as redundant in
  sections 1-14.10 of this report, in `controls_e1b_v2.py`, in the three
  prior scratchpads' scripts, or in CLAUDE.md. None was needed: every plant
  fired its own assertion. The nearest pair, C8 and C13, both fail 4i but on
  opposite halves (Stage 5 gate vs non-Stage-5 gate), and C8 additionally
  fails 6a.
- Compared with v2 (14.3, pre-fix test): C1, C2, C3, C6-C15 give the same
  failing ids; C4 moves from a 600 s hang to 68/4; C5 from an abort after 4k to
  68/3.

#### 14.11.4 Coverage limits

- **`halt_with_live_worker` never executed** in any of the 18 runs (the
  `HALT_TEXT` string appears in no log): 4f-0b, 4k-0, 4l-0, 4m-0 and 4n-0 were
  never reached. C4's worker was ended by the shutdown flag; C5's waiters
  exited within the cleanup join.
- **The failure branch of the new cleanup value never executed.** Every
  4k/4l/4m/4n detail in every run, including C3 and C5 where those checks
  failed, reads the cleanup element `(False, 0, 0)`.
- Both are guards verified by reading only, as in B1-fix. This limitation does
  not by itself call for a repair session.
- The test's own "tripwire_records: 0" weakness (14.3) does not apply here:
  every run has its markers, and no non-marker record exists in any log.

#### 14.11.5 Edits, anything unrecorded, carried

- **No edit** to `tests/test_admission_replay_and_wait.py` or to
  `controls_e1b_v2.py`: no plant hung, aborted or lacked a RESULTS line.
  `b2_run.py` is a new scratchpad driver, not the controls harness; its per-run
  kill bound became an environment variable (`B2_KILL_S`) after the C3 kill,
  and C3 was re-run with 560 s. C4-C15 ran with 300 s.
- **Unrecorded:** nothing in this batch's scope.
- `$TMPDIR` now holds four `oncotriage-admission-e1b-*` dirs: the three from the
  earlier session plus `gdmugy5i` from the killed C3 run. Left in place (no
  cleanup this batch).
- Carried unchanged: CLAUDE.md's end-state overclaims (14.9.5); the CLAUDE.md
  run-block count `# 71` is correct for the clean file.

#### 14.11.6 Next single batch

**B3** (14.7 item 3): the suites not run since the D1 edit, under the same
containment, one at a time, foreground, `test_runner_stop_switch` and
`test_runner_sigterm_shutdown` last and sequentially.

### 14.12 Batch B3 -- affected suites lacking valid evidence (2026-09-14)

Written for: the operator and the session that runs B4. Run only. No production
source, no test file edited; no commit, stash, branch change, reset or cleanup;
no paid call; no production database connected. `git log -1` = `95c9130`;
`git status --short` clean at start.

Evidence root (this session's scratchpad):
`/private/tmp/claude-501/<project-slug>/7744765a-7add-44e7-b5f6-3c29697325b8/scratchpad`
-- driver `b3_run.py`, `evidence/<suite>/{test.log, tw.jsonl, fault/, summary.json}`.

#### 14.12.1 D1, verified

- **D1 is the cancellation-counter fix** (section 10, "a policy-seen
  cancellation was counted `failed`"). Verified by diffing the E1b session's
  `copies/fc_premise` against the tree: the only differences are
  `end_admission_wait(self, cancelled=False)` in `agent/evaluation.py` and
  `_end_admission_wait(..., cancelled=False)` plus its two `cancelled=True`
  call sites in `provider_resilience.py`. Both files mtime 12:12:19.
- **Production has not changed since.** `git diff --name-only 81fd5c7 HEAD` is
  `RECOVERY_E1B_REPORT.md` and `tests/test_admission_replay_and_wait.py` only.
  The only test file changed after D1 is the new test (B1-fix, `0f12013`),
  which no other suite imports; it invalidates no other suite.

#### 14.12.2 Suite list and reasons

Universe: section 6's affected suites. Valid evidence = a run on post-D1
production with B1's containment evidence (launch record + per-process
tripwire-loaded markers with the sandbox-only denied write).

- **Group A -- never run on post-D1 production** (last evidence `aff4`/`aff5`/
  `aff6`, all pre-D1; 14.3): `test_spend_coverage`, `test_spend_budget_split`,
  `test_storage_run_identity`, `test_storage_schema_guards`,
  `test_storage_run_metrics_flush`, `test_ablation_stop_and_lock`,
  `test_api_shutdown_gate`, `test_mcp_deidentified_responses`,
  `test_runner_crash_record_and_db_unification`,
  `test_storage_write_durability`, `test_runner_stop_switch`,
  `test_runner_sigterm_shutdown`.
- **Group B -- post-D1 production, but containment unevidenced.** The `aff7`
  runs used `run.sh`: the old tripwire wrote no load marker, so "no tripwire
  log" cannot be told from an unarmed run (14.3), and all eleven ran
  concurrently under `&`. Their test files are unchanged since. Suites:
  `test_agent_bedrock_anthropic_per_trial`, `test_degradation_counter_readers`,
  `test_agent_stage5_attempt_provenance`, `test_spend_gate`,
  `test_budget_admission`, `test_agent_stage5_per_trial_calls`,
  `test_package_invariants`, `test_campaign_billing_record`,
  `test_billing_closure`.
- **Not on the list, with reasons:**
  - `test_admission_replay_and_wait`: valid evidence from B2 (live tree 71/0
    under full containment, 14.11).
  - `test_provider_resilience`: same gap as group B, but it is B4's subject
    (alone, three times, U9); running it here would pre-empt that batch.

Expected counts are the last recorded ones (aff4-aff7), under the isolated
root: 169, 79, 158 (+1 gated skip), 136, 130, 161, 78, 99, 65, 121 (+1 gated
skip), 146, 95; and 213, 160, 55, 167, 67, 372, 261/0/0, 128, 388/0/0.

#### 14.12.3 Method and containment

- `b3_run.py` runs ONE suite per invocation: B1's `sx.sh` (sandbox launch
  record before exec), `nonet.sb` (profile sha256 recorded per run), `tw/`
  (per-process `tripwire-loaded` markers with the sandbox-only decoy write),
  B1's isoroot as `ONCOTRIAGE_MAIN_PATH`, editable finder stripped,
  `python -u tests/<suite>.py` with cwd = the live `03- Code`, `subprocess.Popen`
  (new session), foreground, never under `&`. Interpreter
  `/opt/anaconda3/bin/python3`; driver SIGINT `default_int_handler` every run.
- Bounds: per-run driver kill (`killpg SIGKILL`) 150 s for suites whose
  recorded time was <= 28 s (>= 5x); 540 s for the child-process suites;
  plus an independent outer `perl alarm` (190 s / 580 s) around each driver.
- Per run the summary records: exit, kill, elapsed, summary lines, FAIL lines,
  traceback count, a sha256 over every `oncotriage/**/*.py` and `tests/**/*.py`
  before and after (`tree_same`), launch pid vs parent marker, every marker's
  ppid, unattached markers, sandbox-denied / audit-hook / strip flags on every
  marker, and every non-marker tripwire record.
- The four `oncotriage-admission-e1b-*` temp dirs were not touched.

#### 14.12.4 Results

All rows: live tree unchanged before/after, launch pid = parent marker pid,
**unattached markers: none**, every marker `sandbox_only_write: denied`, audit
hook installed, editable finder stripped; no external tripwire record; decoys
empty.

| suite | group | bound | exit | time | result | markers | other tripwire records |
|---|---|---|---|---|---|---|---|
| test_spend_budget_split | A | 540 | 0 | 2.8 s | 79/0 | 1 | none |
| test_spend_coverage | A | 150 | 0 | 5.9 s | 169/0 | 2 | none |
| test_storage_run_identity | A | 150 | 0 | 1.5 s | 158/0, 1 skip | 2 | none |
| test_storage_schema_guards | A | 150 | 0 | 1.9 s | 136/0/0 | 1 | none |
| test_storage_run_metrics_flush | A | 150 | 0 | 25.6 s | 130/0 | 2 | none |
| test_api_shutdown_gate | A | 150 | 0 | 2.8 s | 78/0 | 1 | none |
| test_mcp_deidentified_responses | A | 150 | 0 | 1.4 s | 99/0 | 1 | none |
| test_runner_crash_record_and_db_unification | A | 150 | 0 | 12.0 s | 65/0/0 | 2 | connect-loopback 26 (closed-port probe; same count as pre-D1 `aff5`) |
| test_storage_write_durability | A | 150 | 0 | 2.5 s | 121/0, 1 skip | 1 | none |
| test_agent_bedrock_anthropic_per_trial | B | 150 | 0 | 3.5 s | 213/0 | 1 | none |
| test_degradation_counter_readers | B | 150 | 0 | 2.5 s | 160/0 | 1 | none |
| test_agent_stage5_attempt_provenance | B | 150 | 0 | 1.6 s | 55/0 | 1 | none |
| test_spend_gate | B | 150 | 0 | 3.8 s | 167/0 | 2 | none |
| test_budget_admission | B | 150 | 0 | 19.4 s | 67/0 | 23 | none |
| test_agent_stage5_per_trial_calls | B | 150 | 0 | 17.8 s | 372/0 | 1 | none |

- `test_mcp_deidentified_responses` prints 2 tracebacks with 99/0: both are the
  server's own logged `IdentifierLeakError` refusals (after checks 4k and 6e),
  which the suite drives on purpose. The pre-D1 `aff5` log has the same 2.

Continuation, longer suites (540 s driver bound, 580 s outer alarm), same
containment facts as the rows above:

| suite | group | bound | exit | time | result | markers | other tripwire records |
|---|---|---|---|---|---|---|---|
| test_ablation_stop_and_lock | A | 540 | 0 | 29.0 s | 161/0 | 1 | none |
| test_package_invariants | B | 540 | 0 | 60.6 s | 261/0/0 | 6 | none |
| test_campaign_billing_record | B | 540 | 0 | 63.7 s | 128/0 | 63 | none |

- **Containment gap: some descendant processes carry no marker, and that is not
  "unattached".** Read from the test sources, not from a probe:
  `test_ablation_stop_and_lock` (line 1505), `test_runner_stop_switch` (1139)
  and `test_runner_sigterm_shutdown` (788) build child envs as
  `dict(os.environ)` with `PYTHONPATH` REPLACED by their own hook dir, tests/
  and the repo; `test_package_invariants._run` (386-388) prepends or POPS
  `PYTHONPATH`. Those children do not import B1's `sitecustomize`, so the
  tripwire is not armed in them and they write no marker (ablation: 1 marker
  for a suite that spawns real subprocesses). What they do inherit: the
  sandbox (`sandbox-exec` policy applies to descendants), `ONCOTRIAGE_MAIN_PATH`
  = isoroot, `HF_HUB_OFFLINE=1`, and each suite's own closed-port
  `ONCOTRIAGE_QDRANT_URL`. So for those processes network denial rests on the
  sandbox alone, and their count cannot be enumerated from these logs (the
  marker count is a lower bound on processes). Not repaired: that would be a
  test or harness change outside B3.

| suite | group | bound | exit | time | result | markers | other tripwire records |
|---|---|---|---|---|---|---|---|
| test_billing_closure | B | 540 | 0 | 107.1 s | 388/0/0 | 113 | none |

- Its `[SPEND] BILLING RECORD DISCREPANCY (missing)` lines are the suite's own
  driven settlement-failure scenarios printing the reconciliation warning;
  the run is 388/0/0 with no traceback.

Last, consecutively in one foreground chain (270 s driver bound, 290 s outer
alarm each; recorded times ~14-30 s):

| suite | group | bound | exit | time | result | markers | other tripwire records |
|---|---|---|---|---|---|---|---|
| test_runner_stop_switch | A | 270 | 0 | 24.5 s | 146/0 | 2 | none |
| test_runner_sigterm_shutdown | A | 270 | 0 | 29.7 s | 95/0 | 1 | none |

Both with driver SIGINT `default_int_handler`; neither launched under `&`.

#### 14.12.5 Verdict

- **B3 RECORDED: yes.** 21 of 21 listed suites have a result; no blocker, no
  kill, no hang, no abort, no traceback other than the two expected ones above.
  21 evidence directories under `evidence/`.
- **Every suite reached its expected count with zero failures**: 169, 79,
  158 (+1 skip), 136, 130, 161, 78, 99, 65, 121 (+1 skip), 146, 95; 213, 160,
  55, 167, 67, 372, 261/0/0, 128, 388/0/0. No suite was adjusted.
- **Containment markers.** Every run: launch pid = parent marker pid;
  **unattached markers: none in any run**; every marker (228 across the batch, summed from the 21 summary.json files)
  `sandbox_only_write: denied`, audit hook installed, editable finder
  stripped; zero external tripwire records (the only non-marker records are
  `test_runner_crash_record_and_db_unification`'s 26 loopback connects);
  decoys empty; live tree byte-identical before and after every run.
- **B3 PASSED: qualified, not unconditional.**
  - 17 suites pass under containment verified to B1's per-process standard.
  - 4 suites (`test_ablation_stop_and_lock`, `test_package_invariants`,
    `test_runner_stop_switch`, `test_runner_sigterm_shutdown`) pass, but
    spawn descendants whose replaced or popped `PYTHONPATH` keeps the
    tripwire unarmed (14.12.4). For those processes only the inherited
    sandbox is in force, and it is not evidenced by a marker. If the operator
    requires a marker for every process, these four are not yet passed under
    verified containment.
- **Git.** `git log -1` = `95c9130`; `git status --short` = this report only.
  The four `oncotriage-admission-e1b-*` dirs in `$TMPDIR` are unchanged (4).

#### 14.12.6 Still unrecorded

- `test_provider_resilience` on post-D1 code under this containment (B4).
- A marker, or any process accounting, for the unmarked descendants of the
  four suites above. Closing it needs a harness change (e.g. a `usercustomize`
  or `PYTHONSTARTUP`-independent tripwire load, or a process-accounting probe
  under the sandbox); not a B3 action.
- Carried unchanged: CLAUDE.md's end-state overclaims (14.9.5).

#### 14.12.7 Next single batch

**B4** (14.7 item 4): `test_provider_resilience` alone, three times, nothing
else running, under this containment with `b3_run.py` (fresh evidence names).
