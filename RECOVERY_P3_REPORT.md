# Billing closure recovery — P3 (one campaign identity)

Status: **P3 IS CORRECT IN CODE. Its summary-surface prose was stale, and its
tests left three reverts uncaught and one aborting. All of that is fixed and
re-measured.** The session stopped at P3 as instructed: P4, P1b, the
cross-checks and the wider checks were not started. No commit, stash, branch
change or paid call.

## Starting state

- Branch `wip/billing-closure`, HEAD `23db3eb` ("WIP: recovery P2, counter
  provenance verified. NOT FOR PUSH"). Checked with `git branch --show-current`
  and `git log -1`.
- `git status --short` at start: clean (no dirty files). The P2 session's edits
  and report are in the WIP commit.
- Production `inferences.db` sha256 at start:
  `47bab774e152a620d1e20b8ca331e083fcb2f2307807b18544baa9b5ba6a2389`.

## Containment

Rebuilt in this session's scratchpad from RECOVERY_P1_REPORT.md's appendix:
`nonet.sb` plus a socket tripwire (`usercustomize.py`). Control-fired before any
test, and the results match the P2 session:

| arm | external connect | DNS | loopback |
|---|---|---|---|
| tripwire + sandbox | refused and logged | refused and logged | allowed |
| sandbox only | refused | **resolved** | (not tried) |

The tripwire was later rebuilt as a `sitecustomize.py`, which then chains the
interpreter's own sitecustomize. The reason: the new 6G checks launch the entry
point with a test `usercustomize` hook, and a usercustomize tripwire would be
shadowed by it. That arrangement was control-fired too: a hook usercustomize ran,
AND DNS was refused and logged.

## P3-relevant changed files (inherited, from the diff against `e96ec31`)

| file | P3 content |
|---|---|
| `oncotriage/storage/database_logger.py` | `runs.billing_campaign_id` (era 18, additive); `set_run_billing_campaign_id` (durable, NULL-only UPDATE, read back); `campaign_parent_map` (the stitch in Python); `campaign_run_ids` rebuilt on it |
| `oncotriage/storage/queries.py` | `_CAMPAIGN_EDGE_SQL` (billing id first, then the resume rule confined to runs with no id); `billing_campaign_id` projected in `run_summary` and `campaign_summary`; `Query.optional_columns` + `render_sql` |
| `oncotriage/batch/runner.py` | `establish_billing_campaign` stamps the run row before the first billed call; `clear_checkpoint` removes the campaign record |
| `oncotriage/dashboard/tabs/run_health.py` | `billing campaign` column in both tables |
| `tests/test_billing_closure.py` | section 3 (3a..3g), synthetic run rows |

## State found, with evidence

**Classification: FULLY IMPLEMENTED IN CODE, PARTIAL ON THE SUMMARY SURFACE.**
The stitch, the stamp, the projection and the `--fresh` separation are all
correct. Three pieces of operator-facing prose on the Run Health tab still
described the old resume-only rule.

What existing tests covered, stated precisely (the first draft of this report
overstated the gap):

- `tests/test_billing_closure.py` **5i/5i-i** already drive two REAL `main()`
  processes through a zero-success restart and read `campaign_summary`: one row,
  two runs, the budget's id, `resumed` 0,0.
- Nothing drove `--fresh` through the entry point.
- Nothing read the Run Health tables over real-process data.
- Nothing checked that grouping neither duplicates nor omits billing rows or
  charges.
- Nothing pinned the resume rule's billing-id filter.
- Section 3 builds run rows by hand.
- 6d..6f in `test_campaign_billing_record.py` proved the BUDGET continues and
  read no summary.

Baselines in containment, before any edit (all match; zero tripwire records):
`test_billing_closure` 190/0, `test_campaign_billing_record` 97/0,
`test_storage_query_layer` 498/0, `test_storage_schema_guards` 136/0,
`test_dashboard_run_health` 196/0, `test_dashboard_app_integration` 113/0.
(CLAUDE.md said 135 for schema_guards. The file reports 136, including the P1
recovery's 1a-i.)

### Probe through the REAL entry point (scratchpad `p3_probe.py` + hook)

`25- Batch Runner.py` was launched as a subprocess with stand-ins arriving
through `usercustomize`:

- the BM25 index, the graph, tracking, the resample pass and the fingerprint
  are stand-ins;
- the patient runs the REAL Stage 5 node against a stub OpenAI client, so the
  billing record is written by the real retry policy and sink;
- the real `log_inference` writes inference rows.

The databases were read back through `queries.run` and the Run Health table
builders. **54/54 checks passed.**

| scenario | what was driven | summary surface |
|---|---|---|
| S1 | three zero-success restarts, then a clean finish, then a fourth run | runs 1-3 read `resumed` 0,0,0, all under ONE billing id; `campaign_summary` is one row `1 -> 2 -> 3` with that id; both dashboard tables show it; the clean finish cleared the record; run 4 is a new id and a second row `4` |
| S2 | two zero-success runs, then `--fresh` | two rows, `1 -> 2` and `3`, with different ids; the `--fresh` run prints `(new)` |
| S3 | checkpoint resume, then `--fresh`, then a resume of the fresh campaign | `resumed` 0,1,0,1; rows `1 -> 2` and `3 -> 4`, each under its own id |
| S4 | two checkpoint directories, one database, runs interleaved A,B,A,B | rows `1 -> 3` and `2 -> 4`, each under its own id |

Grouping invariants were checked on every database:

- every run is in exactly one summary row;
- `sum(runs)` equals the run count;
- the summary cost column sums to the inference costs;
- each summary row carries at most one billing id, and it is the one its runs
  carry;
- every billing row sits in its own campaign's row;
- every billing campaign is exactly one row;
- each durable total equals its rows;
- the Python stitch equals the SQL stitch on every run.

Every entry-point invocation exited 1. Each time the cause was the
reconciliation reporting "No writes were attempted at all": the stand-in writes
inference rows directly rather than through the runner's write ledger. That is
a harness artifact, confirmed from the printed block, and every run row was
finalized.

### Edge probes (scratchpad `p3_edges.py`)

- **A refused resume after a billing-era run** (`resumed = 1`, no billing id,
  KILLED) is its own campaign in SQL and in Python. That is correct: it billed
  nothing and ran under no budget.
- **A legacy root continued by TWO billing campaigns** (A and B, both
  `resumed = 1`) shows ONE summary row whose `billing_campaign_id` is `MAX` = B.
  Campaign A's id is hidden, while every run and cost is still counted once.
  **It is unreachable through the runner.** The only decision that assigns a
  fresh id to a `resumed = 1` run is `historical_evidence`, and
  `historical_campaign_evidence` refuses under `billed_run_without_campaign`
  when any stitched predecessor already has billing rows. This was established
  by reading `database_logger.py` near line 5770, not by a drive. Reported as a
  residual and not changed.

## What this session changed

1. **`oncotriage/dashboard/tabs/run_health.py`**, prose only:
   - `CAMPAIGN_CAPTION` now states both stitch rules, says a zero-success
     restart (`resumed` = 0) is one campaign as it is one budget, and says
     `resumed` still means only a checkpoint handover;
   - `CAMPAIGN_SINGLE_STATEMENT` and the Campaigns metric help no longer say
     "resumed" is the only continuation;
   - the "campaign of ONE run" phrase that `test_dashboard_run_health` 8a pins
     is kept.
2. **`tests/test_campaign_billing_record.py`**: 97 → **111**.
   - **6f-i..6f-v** read `campaign_summary`, `run_summary` and both Run Health
     tables over the database the two REAL `main()` processes of 6A wrote.
     They require one campaign row for the one budget, apply the grouping
     invariants (`grouping_faults`), and add a non-degeneracy check that the
     campaign really billed.
   - **6u..6z** are new sub-section 6G, `--fresh` through the REAL
     `25- Batch Runner.py`:
     - the child script now doubles as a `usercustomize` hook (copied, not
       exec'd; config from `ONC_BILLING_HOOK_CFG`) and calls `main()` only
       when not hooked;
     - a preflight proves the flag was parsed, the stand-in ran and the runner
       imported from the tree under test;
     - the entry point gets its own lock directory
       (`_control_harness.isolate_locks`), so the provider-allowance lock
       cannot collide with a concurrent bucket-A file or an operator's run;
     - checks: `--fresh` gives a new id; the next no-flag run continues the
       fresh campaign; run rows are `resumed` 0,0,0 with ids C1,C2,C2; the
       summary shows two rows `1` and `2 -> 3`; the grouping invariants hold;
       both campaigns billed; and the no-flag run's seed equals C2's rows with
       ONE prior run, which is what separates "fresh reset" from "fresh
       ignored".
3. **`tests/test_billing_closure.py`**: 190 → **193**.
   - **3h** and its control **3h-i**: the legacy rule's
     `billing_campaign_id IS NULL` filter, in SQL and Python. Both new
     databases are also in the 3e pin loop.
   - **3f-i**: the caption names the billing-campaign rule, and the stale help
     sentence is gone.
4. **`CLAUDE.md`**: one P3 recovery paragraph and the two count lines.
5. **Two abort shapes the plant matrix found, both fixed:**
   - **inherited 3f** subscripted `_table["billing campaign"]` bare, so a
     dropped column aborted all of `test_billing_closure.py` with a KeyError
     and no summary. It now goes through `drive`.
   - **my own 6f-iv** did `list(frame.get(...))`, and `DataFrame.get` returns
     None for a missing column, so the same plant aborted
     `test_campaign_billing_record.py` with a TypeError. It is now `column()`,
     which returns a named absence.
6. **3f-i was strengthened after the matrix MISSED R7.** The first version
   checked only for the substring "billing campaign", and a reverted rule
   sentence still contained that phrase elsewhere in the caption. It now
   requires the rule sentence itself, with whitespace normalized.

## Tests run and results (all in containment; tripwire recorded nothing in any run)

| run | result |
|---|---|
| baselines, before edits | 190 / 97 / 498 / 136 / 196 / 113, all 0 failed |
| `p3_probe.py` (real entry point, S1-S4) | 54 / 0 |
| `p3_edges.py` | refused-run case correct; tree case shows one id (residual) |
| **final** `test_billing_closure.py` | **193 / 0** |
| **final** `test_campaign_billing_record.py` | **111 / 0** |
| final `test_package_invariants.py` | 261 / 0 / 0 (section 1c accepts the copied hook) |
| final `test_dashboard_run_health.py` / `_app_integration.py` / `_truthfulness.py` | 196 / 113 / 157, 0 failed |
| final `test_storage_query_layer.py` / `_schema_guards.py` | 498 / 136, 0 failed |
| `static_checks.py`, `ci_test_buckets.py --check` | all compiled; 149 test files, consistent |
| production `inferences.db` sha256 at end | `47bab774…` unchanged |

### Revert-plant matrix (disposable copies)

The harness is scratchpad `run_plants.py`:

- **copy:** `oncotriage/`, `tests/`, the top-level `*.py` and `pyproject.toml`;
- **isolation:** the sitecustomize tripwire, with the editable-install finder
  stripped;
- **preflight:** the copy is what imports;
- **plants:** each is anchor-counted and `ast.parse`d;
- **after:** the six live files were sha256-compared, **byte-unchanged**.

The 6G entry-point children ran against the copy, as R5's 6v failure shows.

| plant | test_billing_closure | test_campaign_billing_record | caught by |
|---|---|---|---|
| R0 clean | 193 / 0 | 111 / 0 | — |
| R1 SQL billing-id branch dead | 188 / 5 | 108 / 3 | 3a, 3c, 3e, 3f, 5i; 6f-ii, 6f-iv, 6y |
| R2 run row not stamped | 190 / 3 | 104 / 7 | 4h-i, 4l, 5i; 6f-i..iv, 6x, 6y, 6y-i |
| R3 SQL resume rule without the billing-id filter | 191 / 2 | 111 / 0 | **3h (new), 3e** |
| R4 Python resume rule stores billing-era runs | 191 / 2 | 111 / 0 | **3h (new), 3e** |
| R5 `--fresh` keeps the campaign record | 193 / 0 | 107 / 4 | **6v, 6y, 6y-ii, 6z (new)** |
| R6 campaign table without the column | 192 / 1 (was ABORT) | 110 / 1 (was ABORT) | 3f, 6f-iv |
| R7 caption rule sentence reverted | 192 / 1 (was MISSED) | 111 / 0 | 3f-i |
| R8 Python billing-id branch dead | 192 / 1 | 111 / 0 | 3e |

Three things were uncovered before this session:

- **R3 and R4** were caught by nothing: 3e compares SQL with Python, and no
  database exercised the filter.
- **R5** was caught by nothing: no test drove `--fresh`.
- **R6** aborted a file instead of failing a check.

## Unresolved issues (ranked)

1. **A legacy root continued by two billing campaigns displays one id.**
   `MAX(billing_campaign_id)` in `campaign_summary` hides the other; runs and
   costs are still counted once. It is unreachable through the runner, as
   argued above, but this was established by reading, not driven. Proposal
   (not built): add `COUNT(DISTINCT r.billing_campaign_id)` to the summary,
   and render the id as a flagged "multiple" when it is greater than 1.
2. **The summary's cost column is `inferences.estimated_cost_usd`
   (final-attempt), not the billing record.**
   - A campaign row's `total_cost_usd` understates what the budget holds
     whenever retries or unwritten attempts occurred.
   - That is the inherited meaning of the column, and P3 did not change it,
     but the display now puts a billing id beside a cost the billing id does
     not sum.
   - Proposal: project `campaign_billing_total` beside it, labelled.
3. **A refused run (billing id NULL) after a checkpointed campaign stands
   alone, while a second refused resume stitches onto the first** (both have
   no id). That is display-only; neither billed.
4. **6f-ii overlaps the pre-existing 5i**: the same property on a different
   scenario. Kept, because 6f adds the grouping invariants and the tables.
5. **Caption pinning is textual (3f-i).** A reworded but still-correct caption
   fails it.
6. **The entry-point invocations in the probe and in 6G exit 1**: the stand-in
   writes rows outside the runner's write ledger, so reconciliation reports
   nothing attempted. 6G therefore asserts run finalization and the dumps, not
   exit codes.
7. **Production `inferences.db-shm` / `-wal` carry an mtime from this session's
   baseline run** (18:26). The main file is byte-unchanged. The cause is an
   inherited test opening the file read-only on a WAL database. Not
   investigated; out of P3.

## What was verified by running vs only read

- **Run:**
  - every table above;
  - the S1-S4 probe through the real entry point;
  - the edge probes;
  - the 9-plant matrix;
  - containment controls;
  - the production digest at start and end.
- **Read only:**
  - the unreachability of unresolved item 1;
  - that refusals happen before `set_run_billing_campaign_id` for the
    refusal reasons named;
  - the argument that a billing campaign is continued only under an identical
    configuration (FP_MATCH in `continued`, same stamp in recovery).

## End state

- `git status --short`:
  - ` M CLAUDE.md`
  - ` M oncotriage/dashboard/tabs/run_health.py`
  - ` M tests/test_billing_closure.py`
  - ` M tests/test_campaign_billing_record.py`
  - `?? RECOVERY_P3_REPORT.md`
- Branch `wip/billing-closure`, HEAD `23db3eb`; no commit, no stash.
- **THESE EDITS ARE UNCOMMITTED.** Commit them to the WIP branch, or back them
  up, before the P4 session.

## Exact next action for the P4 session

1. **Preserve and check the tree:**
   - confirm the branch is `wip/billing-closure`;
   - confirm HEAD is `23db3eb` plus an operator WIP commit containing the five
     files above;
   - if they are uncommitted, ask for them to be committed first.
2. **Rebuild containment.** Use `nonet.sb` plus the tripwire as a
   **sitecustomize** that chains the conda sitecustomize (see "Containment"),
   because `test_campaign_billing_record.py` 6G now launches the entry point
   with a usercustomize hook. Control-fire an external connect, DNS, loopback,
   and "a hook usercustomize still runs".
3. **Run baselines in containment:**

   | test | expected |
   |---|---|
   | `test_billing_closure.py` | **193 / 0** |
   | `test_campaign_billing_record.py` | **111 / 0** |
   | `test_package_invariants.py` | **261 / 0 / 0** |
   | `test_storage_run_metrics_flush.py` | **130 / 0** |

4. **Start P4 (durability):**
   - read `_open_billing_connection`, the `synchronous` / `fullfsync`
     read-back, `BillingDurabilityUnavailable`, `write_campaign_record` /
     `_durable_sync` / `clear_campaign_record`, and
     `recover_campaign_identity`;
   - run `test_billing_closure.py` section 4 (4a..4q) in containment;
   - fire P4 plants in disposable copies only.
   Note the interaction with P3: `set_run_billing_campaign_id` uses
   `_open_billing_connection`, so a P4 refusal there must still leave the run
   row without a billing id and the run KILLED, never stamped with an unsynced
   id.
