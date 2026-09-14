# Billing closure recovery — P2 (counter registry)

Status: **P2 WAS PARTIAL. Three defects were found, each driven, fixed and
re-measured.** The session stopped at P2 as instructed. P3, P4, P1b, the
cross-checks and the wider checks were NOT started. No commit, stash, branch
change or paid call was made.

## Starting state

- Branch `wip/billing-closure`, HEAD `928a265` ("WIP: billing closure recovery,
  P1 main defect repaired. NOT FOR PUSH"). Verified with
  `git branch --show-current` and `git log -1`.
- `git status --short` at start: **clean, with no dirty files.** The inherited
  work, including session 1's P1 fix and its report, is committed in the WIP
  commit. That closes RECOVERY_P1_REPORT.md unresolved item 1 (no durable
  backup).
- Production `inferences.db` sha256 at start:
  `47bab774e152a620d1e20b8ca331e083fcb2f2307807b18544baa9b5ba6a2389` (WAL
  empty). The value was identical at every later reading this session.

## Containment (rebuilt this session, control-fired before any test)

- `nonet.sb` (deny network-outbound except localhost and unix sockets) plus a
  `usercustomize.py` socket tripwire on `PYTHONPATH`, both per the appendix of
  RECOVERY_P1_REPORT.md, rebuilt in this session's scratchpad.
- Control, tripwire + sandbox: an external connect to 1.1.1.1:443 was refused
  and logged, DNS (example.com) was refused and logged, and loopback was
  allowed.
- Control, sandbox only: the external connect was refused. DNS resolved, via
  the system resolver's unix socket, which is why the tripwire is also needed.
  Loopback was allowed.
- Every test, probe and plant ran under both. **The tripwire recorded zero
  entries in every run**, so there was not even a loopback connect.
- The production database was touched once, by the real-data probe: a sqlite
  `backup()` from a `mode=ro` URI connection into a scratch copy. Its sha256 was
  unchanged before and after.

## P2-relevant changed files

In the inherited WIP commit, from the diff against `e96ec31`:

| file | P2 content |
|---|---|
| `oncotriage/storage/database_logger.py` | `run_counter_registry` table (era 18, with index); `_registered_name_rows` (writer validation); `flush_run_metrics(..., registered_names=)`; `historical_campaign_evidence`'s `counter_registration_unproven` block; the reason in `HISTORICAL_COVERAGE_REASONS` |
| `oncotriage/batch/runner.py` | `flush_health` reads `degradation.registered_names()` once and passes names and count. Every production flush goes through it (8 call sites, grepped) |
| `oncotriage/degradation.py` | `registered_names()` pre-existed; the diff only adds `BILLING_RECORD_FAULTS` and a rewritten description (not P2) |
| `tests/test_billing_closure.py` | section 2 (2a..2h) |
| `tests/test_campaign_billing_record.py` | 1f / 1f-i (required counters are registered), 6q/6s (historical path end to end, no registry case) |
| `tests/test_storage_run_metrics_flush.py` | the exact table pin gains `run_counter_registry` |

## State found, with evidence

**Classification: PARTIAL.** The writer, the table, the runner wiring and the
basic reader refusals were implemented and their tests passed: 178/0 and 91/0
in containment before any edit. Driving the design against the
brief found three defects. The probe is `probe_p2.py` in the scratchpad: real
`flush_run_metrics` and `historical_campaign_evidence` on disposable databases,
against the inherited code.

| id | defect | driven result (inherited code) |
|---|---|---|
| A | **The refusal could fail to name the counter.** `detail` was `"; ".join(notes[:10])`, and registry notes come after the per-run cohort and finalization notes. | 6 predecessors, each with a cohort and a finalization note and missing `PROVIDER_UNCONFIRMED_BILLING`: reasons included `counter_registration_unproven`, and the counter was **not** in `detail`. The brief requires refusal *by name*. |
| B | **A flush without names left the previous flush's registry in place.** The DELETE of registry rows ran only when names were given, while `run_metrics` rows were always replaced. | Flush with names, then flush with `registered_names=None`: 54 registry rows left beside the new health record, with a `written_at` different from the meta row's. The docstring's promise ("None writes no registry rows, which a reader must treat as not recorded") was false in effect. |
| C | **The reader accepted any registry as the health record's own.** It checked only the distinct-name count against the meta row. | A health record carrying a `degradation` value for a counter the registry does not name returned **covered=True**. A stale-stamped registry was not examined at all. |

Also examined and found correct: absent table → refused; empty registry for the
run → refused; count disagreement → refused; required-name check; writer
validation (count, duplicate, non-identifier, unregistered non-zero total) in a
single refused transaction; runner wiring (names and count read once); crash and
final flushes go through `flush_health`.

## What this session changed

1. **`oncotriage/storage/database_logger.py`**
   - `HistoricalEvidence.unproven_counters: tuple = ()`. It is a new trailing
     field with a default; all three constructions are in this module.
   - `historical_campaign_evidence` reads `written_at` from `run_metrics` and
     `run_counter_registry`. A registry counts as evidence only if all four
     hold:
     - (a) every row carries the meta row's `written_at`, which is the same
       timestamp the writer uses in the same transaction;
     - (b) no name repeats, and there is exactly one meta row;
     - (c) the distinct count equals the meta count;
     - (d) every counter the health record values is registered.
   - Failing (a)-(d), or having no registry at all, makes EVERY required
     counter unproven. A consistent registry lacking a counter names exactly
     that counter.
   - `detail` now leads with `unproven counter(s): …`, which is never
     truncated; the other notes keep the cap of 10.
   - `flush_run_metrics` deletes this run's registry rows on EVERY flush, when
     the table exists, and inserts only when names are given. The table check
     keeps a names-less flush working on a database this process initialized
     before the table was dropped. The docstring is corrected.
   - The `counter_registration_unproven` reason comment is updated.
2. **`tests/test_billing_closure.py`**: 178 → **190**.
   - New `field()` helper (attribute read that cannot abort).
   - `legacy_chain(..., reflush=)`, `legacy_chain_many`, `_build_lacking`.
   - 2f's fixture now models a build that genuinely lacked the counter:
     registry row AND meta count both one short, which is what the writer
     produces. Deleting only the row models a corrupt registry, now 2i-iii.
   - New checks:

     | check | what it covers |
     |---|---|
     | 2i | clean control |
     | 2i-i | no registry: all six unproven |
     | 2i-ii | lacking build: exactly one, with no inconsistency note |
     | 2i-iii | row deleted, count kept: all six |
     | 2j, 2j-i | crowded detail still names the counter |
     | 2k | names-less reflush → refused |
     | 2l | stale stamp |
     | 2m | orphan value |
     | 2n | repeated name |
     | 2n-i | two meta rows |
     | 2o | writer removes only this run's registry |
3. **`tests/test_campaign_billing_record.py`**: 91 → **97**. `_make_legacy`
   accepts a tuple of statements. New section 6F (6t) drives the REAL `main()`
   in a child process for two cases:
   - a build lacking `PROVIDER_UNCONFIRMED_BILLING`;
   - a pre-era-18 database (`DROP TABLE run_counter_registry`,
     `PRAGMA user_version = 17`; the child migrates it and the table is empty).

   Each is refused before paid work, and the printed refusal carries exactly
   `unproven counter(s): <names>;`. The run row is KILLED, there are no billing
   rows and no campaign record. 6q is the positive control: the same legacy
   state with its registry intact is covered and seeded.
4. **`CLAUDE.md`**: one paragraph under the billing closure pass's P2 account;
   the two test count lines.
5. **`RECOVERY_P2_REPORT.md`**: this file.

## Tests run and results (all in containment, zero tripwire records)

| run | code | result |
|---|---|---|
| `tests/test_billing_closure.py` | inherited | 178 / 0 |
| `tests/test_campaign_billing_record.py` | inherited | 91 / 0 |
| defect probe A/B/C/D | inherited | A, B, C reproduced (D, a duplicate row, was already caught by the count) |
| `tests/test_billing_closure.py` | after the fix, first pass | 181 / 7. All 7 were my test's `at()` subscripting a NamedTuple; fixed with `field()` |
| `tests/test_billing_closure.py` | second pass | 186 / 2. Both were my fixture modelling a corrupt registry as "a build lacking a counter"; fixture corrected and the corrupt shape kept as its own check |
| `tests/test_billing_closure.py` | after adding 2n-i (found by self-review: an untested branch) | **190 / 0** |
| `tests/test_campaign_billing_record.py` | final | **97 / 0** |
| `tests/test_storage_run_metrics_flush.py` | final (it drives the writer I edited) | 130 / 0 |
| `tests/test_package_invariants.py` | final (it statically scans the edited module) | 261 / 0 / 0 |
| real-data probe on a copy of production | final | see below |

**Real-data negative case.** The production database carries `user_version 16`
and one run (FAILED, finalized, digest present). Probed through a backup copy:

- covered: False;
- reasons: `counter_registration_unproven`, `unrecorded_billing_signal`,
  `attempt_history_not_on_rows`, `embedding_spend_not_on_rows`;
- unproven_counters: all six;
- detail leads with `unproven counter(s): INFERENCE_WRITE_FAILURES, …`, then
  `run 1 recorded no counter registry, …`, then
  `run 1 PER_TRIAL_CALL_FAILURES=73`.

Production sha256 was unchanged.

### Revert-plant matrix (disposable copies only)

The harness is `run_plants.py` in the scratchpad. For every plant:

- Setup:
  - fresh copies of `oncotriage/`, `tests/`, the top-level `*.py` and
    `pyproject.toml` in the scratchpad, with no `.git` and no `__pycache__`;
  - a `usercustomize` that strips the editable-install finder and loads the
    tripwire;
  - `PYTHONPATH=<copy>/_harness:<copy>`.
- Guards:
  - a realpath preflight asserting the copy is what imports;
  - each plant must match its anchor exactly once and `ast.parse`, or it is
    reported PLANT-FAILED (none were);
  - `sandbox-exec` and `PYTHONDONTWRITEBYTECODE=1`.
- Afterwards, the four live files were sha256-compared: **byte-unchanged**.
- R10 failing 6q/6r in `test_campaign_billing_record.py` shows the child
  processes also imported the copy.

| plant | test_billing_closure | test_campaign_billing_record | caught by |
|---|---|---|---|
| R0 clean control | 190 / 0 | 97 / 0 | — |
| R1 absent registry reads as proven | 186 / 3 | 94 / 3 | 2e, 2i-i, 2k; 6t-pre_era_18 (×3) |
| R2 written_at check removed | 188 / 1 | 97 / 0 | 2l |
| R3 duplicate-name check removed | 188 / 1 | 97 / 0 | 2n |
| R4 meta-count check removed | 187 / 2 | 97 / 0 | 2g, 2i-iii |
| R5 orphan-value check removed | 188 / 1 | 97 / 0 | 2m |
| R6 required-name check removed | 185 / 4 | 94 / 3 | 2f, 2i-ii, 2j, 2j-i; 6t-registry_lacks_counter (×3) |
| R7 detail truncates the names again | 188 / 1 | 95 / 2 | 2j-i; 6t-*-i (×2) |
| R8 `unproven_counters` not populated | 187 / 2 | 96 / 1 | 2i-ii, 2j-i; 6t-registry_lacks_counter-i |
| R9 writer leaves stale registry | 188 / 1 | 97 / 0 | 2o |
| R10 runner flush passes no names | 188 / 1 | 94 / 3 | 2c; 6q, 6r, 6t-registry_lacks_counter-i |
| R11 writer count validation removed | 187 / 2 | 97 / 0 | 2b, 2o |
| R12 whole registry proof bypassed | 177 / 12 | 91 / 6 | 2e..2n; all six 6t |
| R13 one-meta-row check removed | 189 / 1 | 97 / 0 | 2n-i |

**13 of 13 caught, none aborted, zero tripwire records.** R0-R12 ran with the
file at 189 checks; R0 and R13 re-ran at 190.

## Unresolved issues (ranked)

1. **THE HISTORICAL PATH IS EFFECTIVELY UNREACHABLE FOR ANY REAL CAMPAIGN.**
   This is a design consequence, not a code defect, and it is reasoned from the
   code rather than driven.
   - Every database written before era 18 is refused. Measured on the
     production copy.
   - An era-18 build reserves a billing row before every billed call, so an
     era-18 predecessor with inference rows almost always has billing rows. That
     is refused as `billed_run_without_campaign`, or handled by identity
     recovery, before this path is reached.
   - What remains is a hand-built or partially deleted database, which is what
     2d and 6q construct.
   - Decision needed from the operator: keep the path (it is safe, and it
     refuses) or delete it as dead code.
2. **THE REQUIRED-COUNTER SET WAS NOT RE-DERIVED.** P2 proves each counter in
   `HISTORICAL_UNRECORDED_BILLING_COUNTERS` was registered. Whether those six are
   ALL the counters whose non-zero total means billed spend missing from the rows
   was not audited: `PER_TRIAL_WARMUP_DEGRADATIONS`, `PER_TRIAL_EMPTY_*`,
   `BILLING_RECORD_FAULTS` and `SPEND_GATE_SKIPS` were not checked. The brief
   said to keep existing conditions, so nothing was added.
3. **A REGISTRY PROVES A COUNTER WAS REGISTERED, NOT THAT THE BUILD INCREMENTED
   IT ON EVERY BILLING PATH.** A build whose code failed to count an event would
   still prove a zero. Inherent to this evidence; no fix is possible from rows.
4. **SOME PLANTS ARE CAUGHT BY ONE IN-PROCESS CHECK AND NO END-TO-END CHECK.**
   R2, R3, R5, R9 and R13 each fail exactly one check in
   `test_billing_closure.py`. The stale, orphan, duplicate and two-meta shapes
   are not driven through `main()`, because they need hand-corrupted databases
   and the reader is the same function either way.
5. **`flush_run_metrics` NOW ISSUES ONE EXTRA `sqlite_master` QUERY PER FLUSH**
   (once per completed patient). Not measured. It is negligible beside the flush's
   own DELETE and INSERTs, but it is unmeasured.
6. **`written_at` EQUALITY IS AN EXACT STRING MATCH.** Both are written from one
   Python variable in one transaction, so it holds by construction. A future
   writer that stamps the two tables separately would make every historical
   campaign refuse. That is loud and safe, and it is pinned by the positive
   controls 2d and 6q.
7. **THE PRODUCTION DATABASE REPORTS `user_version 16`**, while CLAUDE.md's
   cumulative-spend pass calls it era 17. Recorded, not investigated; out of P2.

## What was verified by running vs only read

- **Run:**
  - every row of both results tables;
  - the defect probe on inherited code;
  - the real-data probe on a production copy;
  - the 13-plant matrix;
  - containment controls;
  - production sha256 at start, mid-session and end.
- **Read only:**
  - the "unreachable" argument in unresolved item 1;
  - the completeness of the required-counter set (item 2);
  - that every production flush goes through `flush_health` (grepped, 8 call
    sites);
  - that no existing database carries a stale registry, argued from item B's
    only route being a names-less flush, which no production caller issues.

## End state

- `git status --short`:
  - ` M CLAUDE.md`
  - ` M oncotriage/storage/database_logger.py`
  - ` M tests/test_billing_closure.py`
  - ` M tests/test_campaign_billing_record.py`
  - `?? RECOVERY_P2_REPORT.md`
- Branch `wip/billing-closure`, HEAD `928a265`; no stash, no commit.
- Production `inferences.db` sha256
  `47bab774e152a620d1e20b8ca331e083fcb2f2307807b18544baa9b5ba6a2389`, WAL empty.
  Unchanged.
- **THESE EDITS ARE UNCOMMITTED.** Commit them to the WIP branch, or copy them
  out, before a P3 session starts.

## Exact next action for the P3 session

1. **Preserve first.** Confirm `git branch --show-current` is
   `wip/billing-closure` and `git log -1` is `928a265`, or a later operator WIP
   commit that contains this session's four modified files and this report.
   If they are still uncommitted, ask the operator to commit or back them up
   before editing.
2. **Rebuild containment** from RECOVERY_P1_REPORT.md's appendix. The tripwire
   in this session also refuses DNS for any non-loopback name. Control-fire an
   external connect, a DNS lookup and loopback before any import.
3. **Baselines in containment:**

   | test | expected |
   |---|---|
   | `tests/test_billing_closure.py` | **190 / 0** |
   | `tests/test_campaign_billing_record.py` | **97 / 0** |
   | `tests/test_storage_run_metrics_flush.py` | **130 / 0** |
   | `tests/test_package_invariants.py` | **261 / 0 / 0** |

   Any other number means the tree moved; stop and find out why.
4. **Start P3 (one campaign identity).**
   - Read the P3 diff:
     - `runs.billing_campaign_id` and `set_run_billing_campaign_id`;
     - `queries._CAMPAIGN_EDGE_SQL`;
     - `database_logger.campaign_parent_map` replacing `campaign_run_ids`' walk;
     - `Query.optional_columns` and `queries.render_sql`;
     - the Run Health tab projection;
     - `runner.establish_billing_campaign` / `recover_campaign_identity`.
   - Run `tests/test_billing_closure.py` section 3 (3a..3g) and the
     campaign-summary checks in `tests/test_storage_query_layer.py` and
     `tests/test_storage_schema_guards.py` in containment.
   - Fire P3 plants only in disposable copies. The scratchpad harness
     `run_plants.py` does not persist; rebuild it from the description above.
5. **P3 interaction to check, because P2 touched it.** Identity recovery and the
   historical path are adjacent branches of `establish_billing_campaign`.
   Confirm that a campaign record missing with a checkpoint present still takes
   the recovery branch when billing rows exist, and the P2-hardened historical
   branch only when they do not.
