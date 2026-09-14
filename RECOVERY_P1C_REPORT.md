# Billing closure recovery: P1c (settlement recovery repair)

Status: **Items 1 and 3 COMPLETE. Item 2 STOPPED on an unresolved premise
(evidence and recommendation below); no item-2 code was written.** No commit, no
stash, no branch change. Combined checks NOT started.

Scratchpad (all evidence): `/private/tmp/claude-501/-Users-ramyalsaffar-Ramy-C-V--V-07--LLM-Projects-03--Clinical-Trial-Patient-Match-03--Code/96822efb-5a24-48e3-b638-ea93997960c4/scratchpad`
(below: `SP`).

## Starting state

- Branch `wip/billing-closure`, HEAD `ea4c0af66b2419bd55c2251d2afdb1a0d012d9b3`
  ("WIP: add embedding bounds and settlement discrepancy recovery. NOT FOR PUSH").
  P1b's edits and `RECOVERY_P1B_REPORT.md` are IN that commit (P1b's report
  says "no commit"; the operator committed afterwards).
- `git status --porcelain`: empty at session start (`SP/evidence/git_status_start.txt`).
- Production `02- Data/03- Inferences Storage` and `08- Checkpoint`: 40 files
  hashed read-only (`shasum`) before any work: `SP/evidence/prod_start.txt`.

## Containment

Rebuilt from P1b's profile (`nonet.sb`, the audit-hook `sitecustomize`
tripwire, `run.sh`/`sx.sh`, the isolated root from
`.github/scripts/provision_ci_paths.py` run under the sandbox, exit 0, no
tripwire events). The decoy is `SP/decoy_protected/decoy.db`.

Controls, BOTH WITH THE SANDBOX ACTIVE (`SP/evidence/ctlA.txt`, `ctlC.txt`):

| arm | external connect | raw socket | DNS | loopback | sqlite `mode=ro` protected | write protected | list protected |
|---|---|---|---|---|---|---|---|
| A sandbox + tripwire | refused (tripwire) | refused (tripwire) | refused (tripwire) | allowed | refused before open (tripwire) | refused (tripwire) | refused (EPERM) |
| C sandbox only (`env -i`, tripwire off) | refused (EPERM) | refused (EPERM) | refused (gaierror 8) | allowed | refused (`unable to open`) | refused (EPERM) | refused (EPERM) |

Arm C additionally: a read of the production `08- Checkpoint/batch_runner_checkpoint.json`
and a `mode=ro` sqlite open of the production `inferences.db` were both refused.
Decoy sha256 unchanged after both arms. No arm ran outside the sandbox.

## Start digests (under containment) — `SP/evidence/digests_start.json`

renderer `5ea2c6cc...a956`, `PROMPT_VERSION` 1.11.0, `FINGERPRINT_VERSION` 8,
prompt sha (mesh True) `8baaa8b8...`, (mesh False) `db3c2b4d...` — equal to P1b's end.

## Baselines (under containment, isolated root, zero tripwire events) — `SP/base/`

| test | expected | measured |
|---|---|---|
| `test_billing_closure.py` | 319/0/0 | 319/0/0 |
| `test_campaign_billing_record.py` | 127/0 | 127/0 |
| `test_package_invariants.py` | 261/0/0 | 261/0/0 |
| `test_storage_run_metrics_flush.py` | 130/0 | 130/0 |

## Item 1 — failed acknowledgement (premise)

Read (code inspection, not yet evidence):
- `database_logger.settle_billing_attempt` returns `failed` whenever
  `run_with_write_retry` raises, including AFTER `conn.commit()` returned (a
  raise in `conn.close()`, a commit that reports an I/O error after the WAL
  frame landed). `spend.BillingRecord._write_settlement` also returns `failed`
  when the sink RAISES after its commit.
- `spend.AttemptLiability._settlement_did_not_land("failed")` takes
  `durable = reservation` WITHOUT reading the row, charges live
  `max(priced, reserved)` and writes a discrepancy only for `live - reserved`.
  With priced < reserved that is $0: no discrepancy, while the row actually
  holds `priced`.

### Driven BEFORE any edit, fresh processes, containment on — CONFIRMED

Driver `SP/repro/drive.py` + `SP/repro/child.py` (the closure test's own child
script, extracted verbatim, plus P1c injections and a one-attempt mode). Each
case: process 1 runs the REAL `main()` with the injection; processes 2 and 3 are
fresh observers against the same database and checkpoint. Zero tripwire events.
Raw JSON: `SP/repro/pre_*.json`.

| case | process 1 live `measured` | durable row after process 1 | process 2 seed | process 3 seed | discrepancy rows |
|---|---|---|---|---|---|
| `p1c_ack_failed`: real settlement COMMITS, then returns `failed` | **$0.38484** (the reservation) | `attempt settled response`, settled **$0.0032** | **$0.0032** | $0.0032 | 0 |
| `p1c_ack_raised`: real settlement COMMITS, then raises `OSError` | **$0.38484** | settled **$0.0032** | **$0.0032** | $0.0032 | 0 |

Process 1 faults: `settle:failed` (or `settle:raised:OSError`) and
`ledger_topped_up:failed`; no latch. The resumed seed is $0.38164 BELOW the live
conservative total, with no discrepancy recorded. The premise holds exactly as
the external review stated it.

## Item 2 — double failure: design evidence and STOP

### Measured: a process killed after dispatch and a live double failure leave the SAME durable state

Same harness, same containment (`SP/repro/pre_p1c_kill_after_send.json`,
`pre_p1c_double_fail_over.json`):

| case | process 1 | durable row after process 1 | markers | process 2 | process 3 |
|---|---|---|---|---|---|
| SIGKILL immediately after the provider returned, before settlement | exit -9, live figure died with it | `attempt reserved`, reserved $0.38484 | none | continues, seed $0.38484, 1 unresolved | same |
| double failure: settlement `failed`, discrepancy row `failed`, marker `ENOSPC`, response priced $20.0012 | latched `billing_record`, live $20.0012 | `attempt reserved`, reserved $0.38484 | none | continues, seed $0.38484, 1 unresolved | same |

The two durable records are identical in every column a reader can use (ids and
timestamps aside). Nothing the live process learns after dispatch can reach
durable storage in the double failure, because every such write is the thing
that failed. So:

**No design can make a resume refuse the double failure while letting a resume
continue after a process death at the same point.** The brief allows process
death to "recover conservatively OR refuse by name"; this measurement collapses
that to "refuse", for every uncatchable death (SIGKILL, OOM kill, power loss)
that lands while any billed attempt is between dispatch and settlement. With 12
workers in flight that is nearly every such death.

### Why that changes the solution (stop condition)

1. It REVERSES operator-accepted semantics pinned by P4b/P4c and the
   cumulative-spend pass: "every reader charges a reserved row at its
   reservation" and a recovered or resumed campaign CONTINUES carrying its
   unresolved reservations. Checks that would invert: `test_billing_closure.py`
   4q, 4q-ii, 4y-i, 4y-ii, 4z-i; `test_campaign_billing_record.py` 6i, 6i-i,
   6zb-ii, 6ze-ii (and the P1b checks 7h and 7p, which pin a failed settlement
   leaving a lone reserved row that a resume reads).
2. The only remedy that exists today for such a refusal is `--fresh`, which
   discards the checkpoint and re-bills every completed patient and starts the
   new campaign's budget at $0. A remedy that is safe for the double failure
   does not exist: charging the reservation is exactly the undercount being
   refused, and the live figure is not durable anywhere.
3. The harmful cell is narrower than "double failure". Classified by the brief's
   own rule (new write failing = in scope; loss or modification of a previously
   committed record = out of scope):
   - `missing` (a committed row is gone) and `conflict` (a committed row was
     settled by something else): storage integrity, report only.
   - `failed` with priced <= reservation: the reserved row already reads >= live;
     a resume at the reservation is not lower. No harm.
   - `failed` with priced > reservation: the ONLY in-scope harmful cell. It
     requires the reservation to be BELOW the real charge, which is the known
     P1b residual "Stage 5's input reservation is `chars/3`, an estimate". A
     process death on the same attempt under-counts the TRUE charge by the same
     amount today, with no live process involved.

### Recommended correction (not built)

- **R1 (recommended): make the Stage 5 reservation a true upper bound**, as P1b
  did for embeddings, at the reservation's owner
  (`evaluation._reservation_input_tokens` / the attempt's reservation). Then the
  in-scope harmful cell cannot occur, a death stays resumable at a bound that is
  really a bound, and P4b/P4c semantics stay intact. Needs documented evidence
  of a provider-enforced input limit for the wire model (context window) and a
  decision on its budget cost: a much larger per-attempt reservation, which also
  gates dispatch.
- **R2 (if R1 is rejected): refuse every unconfirmed reservation**, death
  included, with an operator decision on the remedy (an explicit acknowledgement
  that charges named reservations, printed with their amounts, versus `--fresh`).
  This rewrites the P4b/P4c recovery contract and the checks above.
- **R3: accept and document** the residual (the P1b 7z-iii bound), as today.

No item-2 code was written. P1b's 7v and 7z-iii (the stated bound) are unchanged.

## Item 1 — design (built)

After a settlement whose returned result is not `settled`/`duplicate` and not
already `missing`/`conflict` (so: `failed`, a raise, or anything unrecognised),
`AttemptLiability` READS THE STORED ROW before deciding what the durable reading
is. One reader owns the row: `database_logger.billing_attempt_stored_state`
(NEVER RAISES; `billing_attempt_settled_usd` becomes its projection).

| stored state read back | treated as | live charged | discrepancy row | latch |
|---|---|---|---|---|
| settled, same outcome, amount within 1e-9 of the priced liability | the settlement LANDED; only its acknowledgement was lost | priced | none | no |
| reserved at this attempt's reservation | `failed` (P1b's cell, now verified) | max(priced, reserved) | live - reserved if > 1e-9 | only if that row is not recorded |
| settled at another amount/outcome, or reserved at another amount | `conflict` (the committed row was changed) | max(priced, reserved, stored) | live - stored | yes |
| absent | `missing` | max(priced, reserved) | live | yes (resume refuses as incomplete) |
| could not be established (read failed / malformed) | UNVERIFIED | max(priced, reserved) — conservative | \|reserved - priced\|, which keeps the durable total >= live whether the settlement landed or not | yes, by name |

The over-count in the unverified case is at most |reserved - priced| (safe
direction). If the unverified case ALSO loses its discrepancy row and marker, it
falls into item 2's class and is reported there, not closed.

## Item 3 — design (built)

`SpendStop.trip` takes an optional `cause` for the billing-record limit; the
banner prints a sentence true of each cause (write failed / deferred to a marker
/ not priced / a committed row missing / a committed row settled by something
else / stored outcome unverifiable) and a cause-true "to continue" line instead
of "raise config.SPEND_CAP_USD", which no billing-record stop is fixed by.

## Item 1 — after the fix, same fresh-process harness (`SP/repro/post_*.json`)

| case | process 1 live | durable after process 1 | process 2 seed / remaining | latch |
|---|---|---|---|---|
| `p1c_ack_failed` | $0.0032 (verified landed) | settled $0.0032 | $0.0032 / $99.9968 = live | none |
| `p1c_ack_raised` | $0.0032 | settled $0.0032 | $0.0032 = live | none |
| `p1c_ack_failed_unverifiable` (reader disabled) | $0.38484 (conservative) | settled $0.0032 + discrepancy $0.38164 | $0.38484 = live | `billing_record`, cause `unverified` |
| `p1c_kill_after_send` (control) | died | reserved $0.38484 | $0.38484, 1 unresolved | — (unchanged) |
| `p1c_double_fail_over` (control) | $20.0012 | reserved $0.38484 | $0.38484 (below live: item 2, unchanged) | `billing_record`, cause `discrepancy_unrecorded` |

## Tests added — `tests/test_billing_closure.py` SECTION 8 (runs before SECTION 6)

- In process (`p1b_case`): 8a landed + `failed` (parity exact); 8b landed + sink
  raised; 8c priced ABOVE reservation, landed; 8d really not landed (verified
  reserved, unchanged P1b behaviour); 8e unverified, landed world (fresh reading
  == live; settled and unresolved separately; latch cause); 8f unverified, not
  landed (fresh reading above live by exactly reservation - priced); 8g foreign
  settlement + `failed` → conflict; 8h deleted row + `failed` → missing, refuses
  incomplete; 8i reserved amount changed → conflict; 8j sink without a reader →
  unverified; 8k malformed read-back → unverified; 8l the real reader against
  real rows (absent, reserved, settled, unsummable, unopenable database); 8m
  vocabularies restated equal, causes distinct, projection.
- Item 3 at the console surface: 8n every cause prints its own sentence (phrases
  written in the test, not read from the table); 8n-i "COULD NOT BE WRITTEN" only
  for the three write failures; 8n-ii no billing-record banner says raise the
  cap, CONTROL the call-ceiling banner still does; 8n-iii non-degeneracy and the
  latch records the cause only for the billing-record limit; 8p the already-
  latched refusal names the cause; 8o through `main()` (section 7's runs): the
  MISSING and CONFLICT process-1 output carries the right sentence and neither
  "COULD NOT BE WRITTEN" nor "raise config.SPEND_CAP_USD".
- Fresh processes through the real `main()` (campaign mode, 12 wire attempts, four
  outcomes): 8q-ack_failed and 8q-ack_raised (every settlement commits and loses
  its acknowledgement: 12 verified landed; process 2 seed and remaining == process
  1 live; process 3 same seed; settled compared separately, nothing open or
  reserved, zero unresolved, no discrepancy rows); 8s ack_unverifiable (latched
  `unverified`, printed; seed and remaining == live; settled separately; nothing
  open or reserved). 8t restores asserted.
- Child script: `ack_failed` / `ack_raised` / `ack_unverifiable` injections and a
  `latch_cause` field. `p1b_case` records the cause; `billing_attempt_stored_state`
  joined the restorable patch list.
- Tolerance: the file's existing `_TOL = 1e-9` (argued at its definition: IEEE
  sums of a few dozen addends drift far below it; one priced token is above 1e-8).

## Results (final tree, sandbox + tripwire, isolated root)

| test | start | end |
|---|---|---|
| `test_billing_closure.py` | 319/0/0 | **351/0/0** (+32) |
| `test_campaign_billing_record.py` | 127/0 | 127/0 |
| `test_package_invariants.py` | 261/0/0 | 261/0/0 |
| `test_storage_run_metrics_flush.py` | 130/0 | 130/0 |
| `test_spend_gate.py` | — | 165 |
| `test_spend_coverage.py` | — | 169/0 |
| `test_provider_resilience.py` | — | 203/0/0 |
| `test_degradation_counter_readers.py` | — | 160 |
| `test_storage_inference_logging_contract.py` | — | 101 |
| `test_runner_crash_record_and_db_unification.py` | — | 65 |
| `test_storage_run_identity.py` | — | 158 |
| `test_storage_schema_guards.py` | — | 136 |
| `static_checks.py` | — | 317 files compiled |
| `ci_test_buckets.py --check` | — | consistent: 149 files, 130 bucket A |

The last nine equal P1b's measured end counts. Tripwire across all runs: 27
`connect-loopback` to `127.0.0.1:1` (the suites' own closed-port probes); no
external connect, no DNS, no protected sqlite, no protected write. **No arm, test,
control, repro or plant ran outside the sandbox.** No control removed both
protections.

End state: digests equal to start (`SP/evidence/digests_end.json`); production
`02- Data/03- Inferences Storage` + `08- Checkpoint` byte-unchanged, 40 files
including side files (`SP/evidence/prod_end.txt`); decoy unchanged.

## Firing controls — `SP/p1c_plants.py`, disposable copies, live tree byte-unchanged

| control | closure result | caught by |
|---|---|---|
| C0 clean | 351/0 | — |
| C1 no read-back (`failed` trusted as reserved) | 332/19 | 8a-i..8k, **8q-ack_failed-i/ii, 8q-ack_raised-i/ii, 8s, 8s-i** |
| C2 unverified trusted as landed | 344/7 | 8e, 8e-ii, 8f, 8j, 8k, 8s, 8s-i |
| C3 landed check ignores amount and outcome | 350/1 | 8g |
| C4 unverified durable read as the reservation | 344/7 | 8e, 8e-i, 8e-ii, 8f, 8j, 8s, 8s-i |
| C5 banner ignores the cause | 347/4 | 8n, 8n-i, 8o, 8s |
| C6 absent read as reserved | 350/1 | 8h |
| C7 cap line printed for a billing-record stop | 349/2 | 8n-ii, 8o |
| C8 unverified does not latch | 346/5 | 8e-ii, 8f, 8j, 8k, 8s |
| C9 unreadable row read as absent | 350/1 | 8l |
| C10 settlement trip drops the cause | 343/8 | 8e-ii, 8g..8k, 8o, 8s |
| C11 latched refusal restored to old text | 350/1 | 8p |

None aborted.

## Changes (files)

- `oncotriage/storage/database_logger.py`: `STORED_STATE_ABSENT`,
  `billing_attempt_stored_state` (NEVER RAISES; None = not established);
  `billing_attempt_settled_usd` is now its projection; `BillingRecordSink.stored_state`;
  `RUN_STOP_REASON_BILLING_RECORD` docstring made true for every cause.
- `oncotriage/spend.py`: `BILLING_RECORD_CAUSES` (7) and `_BILLING_RECORD_BANNER`;
  `SpendStop.cause`, `trip(..., cause=None)`; per-cause banner, and no "raise the
  cap" line for a billing-record stop; `reserve` passes `unpriced` /
  `write_failed` and its latched refusal names the cause; `STORED_STATE_*`,
  `_valid_usd`, `BillingRecord._stored_state`; `_settlement_did_not_land`
  verifies before deciding; sink-contract, `settle()` and section-header comments.
- `oncotriage/agent/evaluation.py`: the Stage 5 billing-record decline log and
  exception text no longer say "could not be written"; the exception names the
  cause.
- `tests/test_billing_closure.py`: SECTION 8 and child injections (above).
- No schema change, no `SCHEMA_USER_VERSION` change, no fixture-compared field.

## Self-review findings

- Sensitive paths changed: billing settlement accounting (live ledger top-up,
  discrepancy rows), the run-level spend latch, the Stage 5 decline message.
- Failure/retry/restart/concurrency: verification is one read-only query on the
  failure path only (no happy-path cost); it runs after the settlement's own
  retry has finished on the same thread, so no in-flight commit can land after
  the read; one `AttemptLiability` per attempt id, and resolve is idempotent. A
  process death between the lost acknowledgement and the read leaves either a
  settled row (the true charge) or a reserved row (read at the reservation).
- Over-count, stated: an unverified settlement over-counts by at most
  |reservation - priced|, including a provably not-billed attempt, which is
  charged its reservation when its row cannot be read.
- Stale claims corrected: the spend-limit vocabulary docstring, the run stop
  reason docstring, two runtime messages, the section header, the sink contract.
- Not assessed: runtime cost of the verification read under load (rare path,
  unmeasured); CLAUDE.md (deferred to the combined-checks session).

## Unresolved (ranked)

1. **Item 2 is not satisfied** (see the STOP section): a double failure after the
   response still lets a fresh process continue at the reservation, below the
   live charge, exactly as P1b's 7z-iii pins. Needs an operator decision between
   R1 / R2 / R3. Required by the brief and not delivered: "item 2 driven with
   both durable writes failing, proving a fresh process refuses by name".
2. **The unverified case with its discrepancy AND marker also failing** falls into
   item 2's class: if the commit had landed, a resume reads the priced amount (the
   true charge) below the conservative live figure; if not, it reads the
   reservation.
3. **`missing` and `conflict` double failures** are storage-integrity events
   (report only, per the brief).
4. **`BillingRecord.settle()` does not verify**; it has no production caller
   (documented at the method).
5. **The call-ceiling banner** also says "raise config.SPEND_CAP_USD", which a
   ceiling stop is not fixed by. Outside item 3's banner; proposal only.
6. **The discrepancy note does not record that it came from an unverified
   read-back** (its `result` is `failed`; the console and fault key say
   `unverified`). Proposal: an optional note field, with marker validation.

## Exact next action

1. **Operator decision on item 2** (R1 recommended: bound the Stage 5 reservation
   at its owner; R2: refuse every unconfirmed reservation and rewrite the P4b/P4c
   recovery contract plus the checks listed; R3: accept the documented residual).
2. Then the combined-checks session, after preserving these uncommitted edits
   (`git status`: four modified files + this report):
   - rebuild containment from this report and fire both control arms;
   - CI bucket A in full; `tests/run_serial_tests.py` (confirm `oncotriage/config.py`
     and `oncotriage/registries/cancer_code_registry.py` byte-identical after);
   - `python fixture_replay.py`, expected to REFUSE at the shipped provider;
     confirm that, do not treat it as a regression;
   - `tests/test_agent_retrieval_observability.py` with the model cache (real
     root under the sandbox, 104/0/1 in P1b);
   - reconcile CLAUDE.md: P1b and P1c sections, `test_billing_closure.py` 351, and
     the stale counts P1b listed (spend_coverage 169, spend_gate 165, run_identity
     158, schema_guards 136, buckets 149/130), re-measured, not copied;
   - VERIFICATION QUESTION, not an assumed defect: production `inferences.db`
     reads `PRAGMA user_version` 16, CLAUDE.md's version block says 17, the billing
     notes say 18. Establish what each refers to (`SCHEMA_USER_VERSION` in code;
     the stamp an untouched older database keeps until its next
     `initialize_database()`; when each era was introduced), reading production
     only through `tests/_db_snapshot.py` or a byte copy.
