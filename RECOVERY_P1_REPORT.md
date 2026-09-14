# Billing closure recovery — session 1 of 5: P1 (ledger parity)

Status: **P1 COMPLETE for the durable billing record's paths** (Stage 5 and
Stage 2). It was found PARTIAL, one defect was fixed and measured, and the
residuals are listed under "Unresolved issues". Session stopped at P1 as
instructed; P2, P3, P4 and all wider checks were NOT started.

## Starting state

- Revision: `e96ec311edab4c9b1e07b93f1bd3f28eee5e646c` (branch `main`)
- Safety stash `billing-closure-partial`: **DOES NOT EXIST.** `git stash list` is
  empty, `refs/stash` does not resolve, and no ref matching `stash` exists.
  No stash was created (stash operations are out of scope). Instead the 31 dirty
  paths were copied into a tarball in this session's scratchpad
  (`dirty_backup_start.tgz`, sha256 `0f4a01ca815e…c75a`). That copy is session-local
  and is NOT a durable backup; see "Next action".
- Dirty files at start (28 modified, 3 untracked):

```
 M .github/scripts/ci_test_buckets.py
 M CLAUDE.md
 M oncotriage/agent/evaluation.py
 M oncotriage/agent/models.py
 M oncotriage/batch/runner.py
 M oncotriage/config.py
 M oncotriage/dashboard/tabs/run_health.py
 M oncotriage/degradation.py
 M oncotriage/evaluation/ragas_harness.py
 M oncotriage/evaluation/rater.py
 M oncotriage/monitoring/drift_reference.py
 M oncotriage/provider_resilience.py
 M oncotriage/spend.py
 M oncotriage/spend_journal.py
 M oncotriage/storage/database_logger.py
 M oncotriage/storage/queries.py
 M tests/test_agent_bedrock_adapter.py
 M tests/test_agent_stage5_per_trial_calls.py
 M tests/test_harness_lost_trial_call_visibility.py
 M tests/test_monitoring_drift_redesign.py
 M tests/test_package_invariants.py
 M tests/test_run_tunables_record.py
 M tests/test_runner_crash_record_and_db_unification.py
 M tests/test_spend_gate.py
 M tests/test_storage_packing_and_cache_columns.py
 M tests/test_storage_run_identity.py
 M tests/test_storage_run_metrics_flush.py
 M tests/test_storage_schema_guards.py
?? tests/test_agent_stage5_attempt_provenance.py
?? tests/test_billing_closure.py
?? tests/test_campaign_billing_record.py
```

## P1-relevant changed files

| file | P1 content |
|---|---|
| `oncotriage/spend.py` | `price_usage` (one pricing), `attempt_liability` (one rule), `AttemptLiability` / `begin_billed_attempt` (charges the ledger and settles the durable row from one number, tops the ledger up when a settlement does not land), `BillingRecord` live tally (`liability_snapshot`, `LIABILITY_OPEN`) |
| `oncotriage/provider_resilience.py` | `attempt_record` hook on `execute` / `execute_async`; `_record_resolve` |
| `oncotriage/agent/evaluation.py` | `_Stage5AttemptRecord` replaces `_charge_spend` and the charging half of `_charge_upper_bound` (now `_upper_bound_usd`, reporting only) |
| `oncotriage/agent/models.py` | `get_embedding` creates an `AttemptLiability`; failure → `possibly_billed`, BaseException → `abandoned` |
| `oncotriage/storage/database_logger.py` | durable half (`reserve_billing_attempt`, `settle_billing_attempt`, `campaign_billing_total`, `BillingRecordSink`) — cumulative-spend pass, consumed by P1 |
| `oncotriage/batch/runner.py` | resume seed from `campaign_billing_total` (`SEED_SOURCE_BILLING_RECORD`) — consumed by P1's "resumed remaining" |
| `tests/test_billing_closure.py` | SECTION 1 is P1 (sections 2-4 are P2-P4, section 5 end-to-end) |

## Every billed path, enumerated from the code

Found by grepping every `SPEND_LEDGER.charge*`, every `provider_resilience.execute*`
caller, every `on_possibly_billed=`/`attempt_record=`, and every `embeddings.create`.

| path | ledger | durable billing record | parity status |
|---|---|---|---|
| Stage 5 `call_matching_model` (all three provider arms, via `_execute_matching_call`) | `AttemptLiability.resolve` inside `execute` | same object | covered by P1 mechanism |
| Stage 5 `call_matching_model_warmup` (same `_execute_matching_call`) | same | same | covered; NOT previously tested |
| Stage 5 retries inside one logical call (one `AttemptLiability` per wire attempt) | same | same | covered; NOT previously tested |
| Stage 2 `models.get_embedding` | `AttemptLiability` | same | covered |
| ragas judge / embedder (`execute_async`, no `attempt_record`, no `on_possibly_billed`) | response only (`UsageTally._charge`) | none (its durable record is the spend journal, fed FROM the ledger) | agree by construction, but BOTH omit possibly-billed failures — see Unresolved |
| rater batch-management calls (`execute`, `RESERVATION_MANAGEMENT`) | batch usage charged at collection | none (journal) | not a per-attempt billed call; out of this mechanism |
| indexer / index validator `embeddings.create` | none (exempt in `spend.BILLED_SITES`) | none | agree (both zero, exempt by ruling) |

## State found

**P1 is PARTIAL, not complete.** The mechanism is implemented and its own tests
pass, but there is a real parity gap on the failure path of the retry policy.

### Implemented (verified by running, see below)
- Response / unpriced response / bad usage / possibly-billed / not-billed /
  abandoned Stage 5 attempts and response / unpriced / possibly-billed /
  abandoned embeddings: live ledger == durable total, live remaining ==
  resumed remaining. `test_billing_closure.py` section 1.
- Settlement that does not land: row stays reserved, ledger topped up.
- Idempotent resolution; open tally mirrors reserved rows.

### DEFECT FOUND: a raise between dispatch failure and resolution leaves the two ledgers apart
In both `execute` and `execute_async`, the `except Exception` branch runs
`verdict = classify(exc)` and then `pacer.settle(permit, ...)` BEFORE
`_record_resolve(...)`. If either raises, the `AttemptLiability` is never
resolved: the in-process ledger charges nothing while the durable row stays
RESERVED and is charged its reservation by every reader. The sibling
`except BaseException` clause does not catch an exception raised inside the
`except Exception` handler, so the abandoned path does not rescue it.

Driven in the live tree, in-process, against disposable databases, under the
sandbox and tripwire (probe `probe_gapA.py` in the scratchpad):

```
[classify_raises]      live measured=$0.000000 durable=$0.384842 unresolved=1
                       live remaining=10.000000 resumed remaining=9.615158 AGREE=False
[pacer_settle_raises]  live measured=$0.000000 durable=$0.384842 unresolved=1
                       live remaining=10.000000 resumed remaining=9.615158 AGREE=False
[control_ordinary_failure] live=$0.384842 durable=$0.384842 AGREE=True
```

That is the unsafe direction for a live process: it admits one reservation
(~$0.38 per occurrence at the shipped Stage 5 ceiling) that a resume refuses.
Reachability: `_classify_matching_failure` calls adapter taxonomy functions on
an arbitrary exception and is not guarded; `QuotaPacer.settle` is simple but
unguarded on that path while it IS guarded on the other two paths.

## Tests run and results

All runs: `sandbox-exec -f nonet.sb` (outbound denied except loopback/unix) AND
`PYTHONPATH=<scratch>/tripwire` (import-time `usercustomize` socket tripwire,
control-fired first: external connect and DNS refused and logged, loopback
allowed), `HF_HUB_OFFLINE=1`.

| run | code state | result | tripwire records |
|---|---|---|---|
| containment controls | n/a | tripwire refused 1.1.1.1:443 and example.com; sandbox refused 1.1.1.1:443 without tripwire; loopback allowed in both | as expected |
| `tests/test_billing_closure.py` | inherited | **135 passed / 0 failed** (CLAUDE.md says 132 — stale) | 0 |
| `tests/test_provider_resilience.py` | inherited | 203 / 0 | 0 |
| `tests/test_spend_gate.py` | inherited | 165 / 0 | 0 |
| gap probe (above) | inherited | gap reproduced; control agrees | 0 |

| `tests/test_billing_closure.py` + new checks 1o..1w-i | inherited code, BEFORE the fix | **161 passed / 16 failed** — all 16 are the three raise cases (`s5_classify_raises`, `s5_pacer_settle_raises`, `async_classify_raises`); retry, warmup and async-ordinary cases already passed | 0 |
| `tests/test_billing_closure.py` | AFTER the fix | **177 / 0** | 0 |
| `tests/test_provider_resilience.py` | after | 203 / 0 (twin-parity section unchanged) | 0 |
| `tests/test_spend_gate.py` | after | 165 / 0 | 0 |
| `tests/test_campaign_billing_record.py` | after | 91 / 0 | 0 |
| `tests/test_agent_stage5_per_trial_calls.py` | after | exit 0 (count below) | 0 |
| `tests/test_package_invariants.py` | after (it statically scans the edited module) | 261 / 0 / 0 | 0 |

Production `inferences.db` sha256 at start: `47bab774e152…2389` (WAL empty).

### Revert-plant matrix (disposable copies only)

Each plant went into a fresh `copytree` of the working tree (no `.git`, no
`__pycache__`). A `usercustomize` stripped the editable-install finder and
loaded the tripwire, and a realpath preflight asserted the COPY is what
imports. Each plant had to match its exact anchor count and to parse, or it was
reported PLANT-FAILED (none were). Every run used `sandbox-exec` and
`PYTHONDONTWRITEBYTECODE=1`. The five live files the plants target were
sha256-compared before and after: **byte-unchanged**.

| plant | result | caught by |
|---|---|---|
| R0 clean control | 178 / 0 | — |
| R1 my ordering fix reverted (both twins) | 161 / 17 | 1o-1r for the three raise cases, 1t, 1t-i, 1u, 1w-i, 1w-ii |
| R2 ledger charges responses only (the original asymmetry) | 143 / 34 | 1a/1b for every non-response class, 1f, 1g, 1g-i, 1o/1p, 1t, 1v, section 5 (5d, 5e, 5h) |
| R3 settlement-failure top-up removed | 174 / 3 | 1a/1b-s5_settlement_failed, 1h |
| R4 resolution idempotency removed | 176 / 1 | 1l |
| R5 Stage 5 failure always `not_billed` | 166 / 11 | 1d, 1s-*, 1t, 1u, 1v, 1w, 1w-i |
| R6 embedding failure left unresolved | 170 / 7 | 1a/1b/1j-emb_possibly_billed, 1g, 5d, 5e, 5g |
| R7 `on_possibly_billed` charges the ledger again (double charge) | 162 / 15 | 1a/1b-s5_possibly_billed, 1a/1b-s5_abandoned, 1o/1p, 1t, 1v, 5d, 5e, 5h |
| R8 unknown outcome is free | 176 / 1 | 1m |
| R9 embedding abandon left unresolved | 173 / 4 | 1a/1b/1j-emb_abandoned, 1g-i |
| R10 my helper does not resolve the attempt | 167 / 10 | 1o-1r for the classifier cases, 1t, 1w-i |
| R11 only the async twin reverted | 172 / 5 | 1o-1r-async_classify_raises, 1w-i |
| R12 my helper's unconfirmed bump ungated | 177 / 1 | 1w-ii |

12 of 12 plants caught, none aborted, zero external tripwire records. R1-R11
were run against the file at 177 checks and re-run at 178 for R0, R1 and R12.

Production `inferences.db` sha256 at end: `47bab774e152…2389`, unchanged (WAL
still empty).

## What this session changed

1. **`oncotriage/provider_resilience.py`** (the P1 fix).
   - In BOTH `execute` and `execute_async`, the `except Exception` branch now
     asks `classify(exc)` under its own guard, and resolves the attempt
     liability (`_record_resolve`) BEFORE `pacer.settle(...)`. The exception
     that propagates is unchanged in every case.
   - New `_settle_unclassifiable(pacer, permit, scope, on_possibly_billed,
     attempt_record, token, classify_exc)`, which never raises. It resolves the
     attempt as `unclassified` (possibly billed, at its reservation) first,
     counts `classify_failed:{scope}:{Type}` in `PROVIDER_RETRY_OUTCOMES`,
     settles the pacer under a guard, and reports to the unconfirmed-billing
     tally gated exactly as the ordinary failure branch gates it.
   - Docstrings of `PROVIDER_RETRY_OUTCOMES` and `PROVIDER_UNCONFIRMED_BILLING`
     corrected. The second claimed every unconfirmed attempt was charged to the
     ledger, which is false for ragas.
2. **`tests/test_billing_closure.py`** (untracked, inherited): +43 checks in
   section 1, 1o through 1w-ii.
   - The two raise cases on Stage 5.
   - A retry inside one logical call, which is two liabilities.
   - The per-trial warmup's failure and response.
   - The async twin with a raising classifier and with an ordinary failure.
   - The gating of the unconfirmed bump.
   - Restore checks for the pacer stand-in.
   - 135 → 178.
3. **`CLAUDE.md`**: one paragraph in the billing closure pass's P1 account, and
   the count line for this test (it said 132).
4. **`RECOVERY_P1_REPORT.md`**: this file.

Nothing else in the tree was edited. No stash, reset, checkout, clean, commit
or paid call. The inherited edits are intact: the 28 modified and 3 untracked
paths from the start are all still present, and the only new entries are this
report and the files listed above.

## Unresolved issues

Ranked by consequence.

1. **THE SAFETY STASH DOES NOT EXIST.** There is no `billing-closure-partial`,
   no `refs/stash`, and no stash reflog. The only backup of the ~31 inherited
   files is this session's scratchpad tarball, which is session-local and
   will not survive. **The uncommitted work has no durable backup.** Proposal:
   before session 2 edits anything, write a tarball of
   `git status --porcelain` paths to a sibling directory OUTSIDE the scratchpad
   and outside the repo, or have the operator commit to a branch. This is the
   operator's decision; this session did neither.
2. **A response priced ABOVE its reservation whose settlement then fails
   leaves resumed remaining HIGHER than live**, the unsafe direction for a
   resume. It is inherited, and the billing closure pass named it.
   - The durable row holds the reservation; the ledger holds the priced
     amount. No write can repair it, because the write is what failed.
   - Reachable only when actual input tokens exceed the reservation's estimate
     by more than the output ceiling's price. For Stage 5 that is practically
     unreachable, since the output ceiling dominates.
   - For embeddings the reservation is `len(text)/3 + 1` with 0 output. Text
     tokenizing below 3 chars/token (digits, non-ASCII) could exceed it, by
     sub-microdollar amounts.
   - Proposal: price the embedding reservation from a TRUE upper bound (UTF-8
     byte length, since a byte-level BPE token covers at least one byte). It
     costs nothing, because the embedding reservation feeds no pacer.
3. **A settlement returning `missing` or `conflict` tops the ledger up to the
   reservation while the durable reader charges what the row holds.** For
   `missing`, that is nothing.
   - The disagreement is in the SAFE direction: live is more conservative.
   - Parity is not exact, and no test drives `missing` or `conflict` through
     `AttemptLiability`.
   - `missing` needs the row deleted between reserve and settle.
4. **RAGAS possibly-billed failures are charged NOWHERE.**
   - `ragas_harness` calls `execute_async` with no `attempt_record` and no
     `on_possibly_billed`.
   - Its durable record is the spend journal, fed FROM the ledger, so live and
     resumed agree by construction. Both omit the failures, and the ordinary
     ones are not even counted in `PROVIDER_UNCONFIRMED_BILLING`.
   - This is an under-count of the campaign budget, not a parity break, and it
     predates the billing closure pass.
   - Proposal, not built (the brief ruled changes outside P1 out): pass
     `on_possibly_billed` pricing the judge or embedder reservation through
     `judge_pricing` / `embedding_pricing`, charged via `charge_usd` on the
     same source.
5. **Every exception from `get_embedding` is charged as possibly billed**,
   including provably-unbilled 4xx refusals. It is conservative, both ledgers
   agree, and it over-counts Stage 2 by one reservation per refused call.
6. **While an attempt is in flight, live remaining exceeds resumed remaining
   by its open reservation.** This is inherent: the live tally mirrors it
   (1k), but `remaining()` does not subtract open reservations. A kill at that
   instant is covered by the durable reader.
7. **`BillingRecord.settle` has no production caller**; only
   `tests/test_campaign_billing_record.py` uses it. It is a second resolution
   path that bypasses the ledger, kept rather than deleted since deleting it is
   outside P1. Nothing prevents a future caller from reintroducing the
   asymmetry through it.
8. **Test weaknesses.**
   - R4 (idempotency) and R8 (unknown outcome) are each caught by exactly one
     check.
   - Section 5's child processes were not individually preflighted to import
     the copy. They inherit `PYTHONPATH`, and R2/R6/R7 moving 5d/5e confirms
     they did.
   - The async-twin checks drive a hand-built `_Stage5AttemptRecord`, because
     no production path passes `attempt_record` to `execute_async`.
9. **UNVERIFIED BY THIS SESSION**: every P2, P3 and P4 claim in CLAUDE.md's
   billing closure section, CI bucket A, the serial runner, and the revert
   matrices CLAUDE.md attributes to the prior session. Sections 2-5 of
   `test_billing_closure.py` passed as a side effect of running the file.
   That is not a review of P2-P4.
10. **Scope note.** The fix extends to `execute_async` and to the counter
    docstrings. The async twin carries the identical ordering defect, and
    fixing one twin only would break the policy parity
    `test_provider_resilience.py` checks. Plant R11 shows the async half is
    independently load-bearing.

## Exact next action for session 2

1. Confirm preservation: `git status --porcelain` should show the 28 modified
   + 3 untracked paths listed at the top, plus `RECOVERY_P1_REPORT.md`, with
   `CLAUDE.md`, `oncotriage/provider_resilience.py` and
   `tests/test_billing_closure.py` carrying this session's edits. If the
   operator has not yet made a durable backup (Unresolved 1), stop and ask.
2. Rebuild containment from the appendix below: the sandbox profile plus the
   tripwire `usercustomize.py`. Control-fire both, with an external connect,
   a DNS lookup and loopback, before any import.
3. Start P2 (counter registry): read the P2 diff (`run_counter_registry` in
   `oncotriage/storage/database_logger.py`, `flush_run_metrics(...,
   registered_names=)`, `historical_campaign_evidence`'s
   `counter_registration_unproven`, and `batch/runner.py`'s flush call). Then
   run `tests/test_billing_closure.py` section 2 and
   `tests/test_campaign_billing_record.py` section 6 in containment.
   Expected at the start of session 2: 178/0 and 91/0.
4. Fire P2 plants in disposable copies only, using the same copy harness
   described in the plant-matrix section above.

## Appendix: containment used (rebuild it; the scratchpad does not persist)

`nonet.sb`:
```
(version 1)
(allow default)
(deny network-outbound)
(allow network-outbound (remote ip "localhost:*"))
(allow network-outbound (remote unix-socket))
```

Tripwire: a `usercustomize.py` on `PYTHONPATH`. It uses `usercustomize` rather
than `sitecustomize` because anaconda already ships
`/opt/anaconda3/lib/python3.13/sitecustomize.py`. It replaces
`socket.socket.connect`, `socket.socket.connect_ex`,
`socket.create_connection` and `socket.getaddrinfo`. Loopback and `AF_UNIX`
are recorded and allowed; every other target is recorded to `$ONC_TRIPWIRE_LOG`
and refused with `OSError(1)` (`gaierror` for resolution).

Invocation:
```
sandbox-exec -f nonet.sb env PYTHONPATH=<tripwire dir> ONC_TRIPWIRE_LOG=<log> \
    HF_HUB_OFFLINE=1 python tests/<file>.py
```

Copy harness extra: prepend to the copy's `usercustomize.py`
```
sys.meta_path[:] = [f for f in sys.meta_path
                    if "__editable__" not in (getattr(f, "__module__", "") or "")]
```
Also set `PYTHONPATH=<copy>/_harness:<copy>`, and preflight
`realpath(oncotriage.__file__)` inside the copy.
