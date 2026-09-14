# Billing closure recovery — P4b (P4 closure repair)

Status: **P4b COMPLETE.** All three items are fixed, driven and plant-verified under containment. Stopped at P4b as instructed: P1b and the combined checks were not started. The changes are UNCOMMITTED.

## Starting state

- Branch `wip/billing-closure`; HEAD `f526c0c92962947d2d4aa104853f4ae514b7435b`
  ("WIP: preserve P3/P4 recovery work; billing closure remains incomplete").
  Verified with `git branch --show-current` and `git log -1`.
- The working tree was clean at the start (`git status --short` empty).
- Production `02- Data/03- Inferences Storage/` at the start. Read with
  `stat` and `shasum` only, never through sqlite:

  | file | inode | size | mtime | birth | sha256 |
  |---|---|---|---|---|---|
  | `inferences.db` | 83649952 | 1,212,416 | 2026-09-11 13:22:43 | 2026-09-11 13:15:50 | `47bab774…` |
  | `inferences.db-shm` | 86088010 | 32,768 | 2026-09-13 18:48:43 | 2026-09-13 18:48:43 | `fd4c9fda…` |
  | `inferences.db-wal` | 86088009 | 0 | 2026-09-13 18:48:43 | 2026-09-13 18:48:43 | `e3b0c442…` |

  This is identical to the end-state table in RECOVERY_P4_REPORT.md.

## Containment

Built in this session's scratchpad and copied from the P4 session's files:
`nonet.sb`, `tw/sitecustomize.py`, `tw/_onc_tripwire.py`, `run.sh`, `sx.sh`.

- **OS sandbox (`sandbox-exec`).**
  - Outbound network is denied except loopback, and the mDNSResponder unix
    socket is denied too.
  - `file-write*` is denied under the production `02- Data/03- Inferences
    Storage`, `08- Checkpoint`, `09- Testing/Evaluation Runs` and `04- Results`,
    and under a scratch decoy.
  - **New this session:** `file-read-data` is denied under the same
    directories. The first control round showed that the sandbox alone allowed a
    `mode=ro` sqlite open of a file in a protected directory (arm C); only the
    tripwire refused it. With read denied, the sandbox refuses it too.
- **Tripwire** (`sitecustomize` audit hook). It refuses and logs:
  - non-loopback `connect` and `getaddrinfo`;
  - every `sqlite3.connect` under a protected directory, before open;
  - writes into a protected directory.

  It logs connects to the isolated copy.
- **Redirect.** `ONCOTRIAGE_MAIN_PATH` points at `scratchpad/isoroot`, which
  was provisioned by `.github/scripts/provision_ci_paths.py` under the sandbox.
- **Verified consistent snapshot of production.** The database was copied as
  bytes. The sha256 was `47bab774…` before the copy, after the copy, and on the
  copy. The `-wal` was 0 bytes, so the main file is a consistent point.

Controls. Every arm ran under the sandbox; none ran outside it. Targets were
public IPs, `example.com`, a closed loopback port, and the DECOY directory,
never production.

| arm | external connect | raw `_socket` | DNS | loopback | sqlite `mode=ro` to protected | write to protected |
|---|---|---|---|---|---|---|
| A2 sandbox + tripwire | refused (tripwire) | refused | refused | allowed | refused before open | refused |
| B3 + a child `usercustomize` that reloads `socket`, hook dir first | refused | refused | refused | allowed | refused | refused |
| C2 `env -i`, tripwire OFF, sandbox only | refused (EPERM) | refused | refused (gaierror 8) | allowed | refused (`unable to open`) | refused (EPERM) |

The first round (A, B, B2, C) ran before read-deny was added; its arm C allowed
the sqlite read of the decoy. The decoy's sha256 was unchanged
(`fed6a800…`), and no side files were created.

## Baselines in containment, before any edit (`scratchpad/base/`)

| test | result | expected |
|---|---|---|
| `test_billing_closure.py` | 231 / 0 / 0 | 231/0 |
| `test_campaign_billing_record.py` | 111 / 0 | 111/0 |
| `test_package_invariants.py` | 261 / 0 | 261/0/0 |
| `test_storage_run_metrics_flush.py` | 130 / 0 | 130/0 |
| `test_storage_query_layer.py` | 498 / 0 | |
| `test_storage_write_durability.py` | 111 / 0 | |
| `test_dashboard_truthfulness.py` | 157 / 0 | |

There were zero REFUSED tripwire events. The watched connects to the isolated
copy confirm the item-2 sites:

- `test_storage_write_durability.py:300` `mode=ro` ×2;
- `test_storage_query_layer.py:648` `mode=ro` ×2;
- `dashboard/data.py:95` **plain read-write** (reached from the query-layer
  cost-tab render);
- `test_dashboard_truthfulness.py` `mode=ro&immutable=1` ×12.

## Item 1: recovery gap — CONFIRMED

Driven in two FRESH processes (`scratchpad/repro1.py`) under containment, with
the real `establish_billing_campaign`.

1. **Process 1** opened run 1 and established a `new` campaign. It settled a
   $1.25 charge and left a $0.40 reservation unresolved. The run was finalized
   FAILED, with no checkpoint (zero completed patients). The durable total was
   $1.65, with 1 attempt unresolved.
2. The identity record was deleted.
3. **Process 2** opened run 2 with `resumed=False` and called
   `establish_billing_campaign`. The result was **decision `new`**, a
   **different campaign id**, **seed $0.00** and 0 unresolved. The $1.65 of
   liabilities were not in the budget.

Cause: `establish_billing_campaign` consults recovery only when
`corrupt is not None or resumed`.


### Item 1 fix

- `oncotriage/storage/database_logger.py`:
  - `latest_run_id(db_path)` is new. It returns 0 when the database or its
    `runs` table is absent, never creates the file, and RAISES
    `BillingRecordUnreadable` on a read failure.
  - `recover_campaign_identity(..., closed_through_run_id=0)`: a campaign whose
    touched runs all sit at or below the watermark is skipped before any other
    test. A watermark that is not a non-negative int yields `unreadable`.
- `oncotriage/batch/runner.py`:
  - `establish_billing_campaign` consults recovery whenever the record is
    missing or unreadable, with or without a checkpoint, passing the watermark.
    Outcomes: `recovered` continues the campaign; `no_evidence` starts `new`
    (or takes the historical path when resumed, as before); `ambiguous`,
    `inconsistent` or `unreadable` refuse by name
    (`campaign_identity_unestablished`).
  - `FRESH_MARKER_FILENAME` (`batch_runner_fresh_start.json`),
    `read_fresh_watermark()` (0 when absent; refuses by name when unreadable),
    and `record_fresh_start(db_path=None)`. The last writes the watermark
    `max(latest run id, previous watermark)` durably, through the shared
    `_write_json_durably`, which is also what `write_campaign_record` now uses.
- `25- Batch Runner.py` `--fresh`: `record_fresh_start()` runs BEFORE
  `clear_checkpoint()`. A refusal prints its block and exits 1 with nothing
  discarded.

Driven again in fresh processes after the fix (`repro1.py`, containment, zero
tripwire events):

| step | result |
|---|---|
| record deleted, no checkpoint | `recovered_from_billing_record`, same campaign, seed $1.65, 1 unresolved |
| deleted again | same campaign, seed $1.65; billing rows (2, $1.65) unchanged |
| `record_fresh_start` then record deleted | watermark 3, decision `new`, seed $0.00 |
| unreadable marker + missing record | refused `campaign_record_unreadable`, naming the marker |

### Item 3 fix

`CampaignBillingRefusal.lines()`:

- **`campaign_record_unwritable`.** The claim that a record left behind "names
  a campaign with no spend and is safe to continue or remove" is gone. The
  remedy now says:
  - the campaign may ALREADY HOLD CHARGES;
  - if it does, the next run continues it with every one of them, whether or
    not the record was left in place, or refuses by name;
  - a campaign with no charges may be replaced, which loses nothing;
  - do not remove the record to start over, because --fresh is the deliberate
    new start.
- **Closing line.** It now reads "NOTHING HAS BEEN BILLED BY THIS RUN."
- **`campaign_record_unreadable`.** Its remedy no longer says removing the
  record "makes the campaign historical", which recovery made false.
- **`write_campaign_record` comment.** The false "nothing has been billed under
  the id ... a campaign with no spend" comment was rewritten.

### Item 2 fix

- **`tests/_db_snapshot.py` (new, no `test_` prefix).**
  - `snapshot()` makes a byte copy of the main file and its `-wal`. The source's
    digests must be equal before and after, and the copy's digests must equal
    them, or it retries up to 3 times and then raises `SnapshotInconsistent`.
  - `frozen_copy()` checkpoints the COPY into one file with no side files, and
    returns its digest.
  - `ProductionConnectGuard` patches `sqlite3.connect`. It refuses any connect
    that resolves to a given path, plain or URI, before open, and records the
    attempt.
  - It never connects to the source path.
- **`test_storage_query_layer.py`.**
  - Production row counts are taken on snapshots, and section 9 says so.
  - The production main file and `-wal` are compared by bytes.
  - The guard is installed before the first reading, and "no connect attempted"
    is asserted.
  - The cost-tab render now points `paths._RESOLVED["inferences_path"]` at the
    seeded database, clears the Streamlit cache, restores both in `finally`, and
    asserts no production connect was attempted during it.
- **`test_storage_write_durability.py`.**
  - 9c compares snapshot counts plus byte digests, and asserts no connect was
    attempted.
  - New section 9e runs against a scratch WAL database:
    - a snapshot carries uncheckpointed frames;
    - `immutable=1` on the LIVE file does not see them;
    - a frozen copy does;
    - a source written during every copy raises `SnapshotInconsistent`;
    - the guard refuses its target, allows others, and uninstalls.
- **`test_dashboard_truthfulness.py`.**
  - Section 10 reads a verified FROZEN COPY of the smoke database; `immutable=1`
    is used only there.
  - 10d-i shows the copy's digest is unchanged.
  - T2-i compares the production main file and `-wal` by bytes.
  - T2-ii asserts the guard (installed before `_REAL_CONNECT` is captured)
    recorded no attempt.
  - The docstring states this.

## Tests and results (containment: sandbox with production reads denied + tripwire + isolated root)

After item 1/3 edits (`scratchpad/r1`):

| test | result | was |
|---|---|---|
| `test_billing_closure.py` | 256 / 0 / 0 | 231 |
| `test_campaign_billing_record.py` | 115 / 0 | 111 |

After item 2 (`scratchpad/r2`, `r3`):

| test | result | was |
|---|---|---|
| `test_storage_query_layer.py` | 503 / 0 | 498 |
| `test_storage_write_durability.py` | 122 / 0 | 111 |
| `test_dashboard_truthfulness.py` | 160 / 0 | 157 |
| `test_package_invariants.py` | 261 / 0 | |
| `test_storage_run_metrics_flush.py` | 130 / 0 | |
| `test_campaign_cohort_selection.py` | 116 / 0 | |
| `test_resume_configuration_fingerprint.py` | 500 / 0 | |
| `test_runner_preflight_and_state_faults.py` | 126 / 0 | |
| `static_checks.py` | all compiled | |
| `ci_test_buckets.py --check` | consistent, 149 files / 130 bucket A | |

In r2/r3 the tripwire wrote NO log file: zero events of any kind, including zero
connects to the isolated copy of production. The baseline had 17.

The guard-ordering restructure (`scratchpad/r4`) put each guard ABOVE the first
reading, and the truthfulness test now uses the shared guard installed before
`_REAL_CONNECT` is captured:

| test | result |
|---|---|
| `test_billing_closure.py` | 256 / 0 |
| `test_campaign_billing_record.py` | 115 / 0 |
| `test_dashboard_truthfulness.py` | 161 / 0 |
| `test_storage_query_layer.py` | 503 / 0 |
| `test_storage_write_durability.py` | 122 / 0 |

Again no tripwire events.

## Digests

These were computed under containment, from the live tree and from HEAD's
package extracted with `git archive`. The HEAD import was verified to come from
the extracted tree.

| | live | HEAD |
|---|---|---|
| `renderer_digest` | `5ea2c6cc…a956` | `5ea2c6cc…a956` |
| `PROMPT_VERSION` | 1.11.0 | 1.11.0 |
| `FINGERPRINT_VERSION` | 8 | — |
| `prompt_sha256(render_system_prompt('PATIENT','TRIALS', True/False))` | `8baaa8b8…` / `db3c2b4d…` | identical |

No renderer module (`agent/patient.py`, `agent/prompts.py`, `constants.py`,
`extraction/stage.py`, `utils.py`, `deid.py`) has a diff line.

## Self-review findings (critical pass over the full session diff)

Classified as fixed, residual (documented), or proposal. "Driven" means
executed; "read" means code inspection only.

**Fixed during review**

- **F1 (low).** Stale operator text. The warning `clear_campaign_record` prints
  said "remove the file to start fresh", and recovery now makes that false. It
  now names `--fresh`. Read.
- **F2 (low).** Stale docstring. `clear_checkpoint` said removing the record "is
  what ends a campaign". It now states that a FINISHED run or the `--fresh`
  watermark ends one. Read.
- **F3 (medium, test).** R1b aborted `test_storage_query_layer.py`: a refused
  production connect was outside the `try`. The connect is now inside it.
  Driven by the plant matrix.
- **F4 (test harness).** The first plant matrix's query-layer clean control
  failed 5 git-history checks because the copy had no `.git`. Copies now get a
  read-only `.git` symlink with `GIT_OPTIONAL_LOCKS=0`.

**Residuals (documented, not fixed)**

- **R1 (medium, money-safe direction).** Cross-directory adoption now also
  reaches a NEW checkpoint directory's FIRST run.
  - A second directory started on the identical configuration and cohort digest
    against the same database continues the first directory's open campaign,
    so the two share one budget. That over-counts; it never under-counts.
  - Before P4b this happened only on a resume. The rows carry no
    checkpoint-directory identity, so the two cases cannot be told apart
    without a schema change, such as a run-row checkpoint key.
  - `--fresh` in the new directory separates them.
  - Read and reasoned. The concurrent test (6D) uses different cohort digests,
    so it does not exercise this.
- **R2 (low, over-count).** A clean finish that dies between `clear_checkpoint()`
  (runner line ~4987) and `finalize_run_record(... FINISHED)` (line ~5285)
  leaves an unfinished campaign with no record. The next run recovers and
  continues it. Read, not driven.
- **R3 (low).** `record_fresh_start` replaces an UNREADABLE existing marker
  without counting it. The watermark is `max(latest run id, 0)`, which still
  closes every existing campaign, so nothing is lost. The unreadable file is not
  preserved. Read.
- **R4 (low, privacy).** The test snapshots copy the production database's bytes
  (patient data; synthetic here) into each test's temp directory. Each file
  removes its temp directory at the end, but an ABORTED run leaves the copy
  behind. The old code copied nothing, but it opened the file.
- **R5 (unmeasured, performance).** The snapshot cost is proportional to the
  database size. Each of the three files copies it 1–2 times per run:
  1.2 MB here; the production database has been 90 MB in its history.
- **R6 (race, read).** Two directories starting their first run at the same
  instant, before either stamps, both find no evidence and start two campaigns.
  A later loss of either record is then `ambiguous`, which refuses by name.
- **R7 (inherent).** A snapshot proves only that the source did not change
  while it was copied. A writer that commits between the two digest reads and
  restores identical bytes is not detectable. That is not a realistic SQLite
  pattern.
- **R8 (documentation).** RECOVERY_P4_REPORT.md unresolved item 7 ("names a
  campaign with no spend, so continuing it is safe") is superseded. The
  historical report was not edited.

**Surfaces checked with no finding**

- **Security.** No untrusted input reaches SQL: the watermark is an int
  validated before use, and every query is parameterized. The marker holds only
  an int and a timestamp. No secrets are involved.
- **Callers.** `recover_campaign_identity` has one production caller
  (`establish_billing_campaign`). The new keyword defaults to 0, so the
  existing call shape is unchanged. Grep, read.
- **Digests.** Renderer and prompt digests are unchanged; see Digests above.
- **Production.** Mid-session state was identical to the start table: same
  inodes, births and sha256. Checked with `stat` and `shasum`, no sqlite.

## Instruction issues

- **"P1b" is not defined in any report.** RECOVERY_P1_REPORT.md has no "P1b"
  section; RECOVERY_P4_REPORT.md says only "take up P1b as RECOVERY_P1_REPORT.md
  defines it". The nearest definition is P1's "Unresolved issues" items 2–8.
  Recommendation: the operator should name the P1b scope before that session
  starts.
- **Item 2's line numbers.**
  - `test_storage_query_layer.py ~658/~4945` are the two call sites of
    `_production_inference_rows` (defined at 648).
  - `~4077` is the cost-tab render, and the open itself happens in
    `dashboard/data.py:95`.
  - `test_storage_write_durability.py ~300` is `rows()`, called for the
    production path at ~403 and ~1101.

  All were confirmed by the baseline tripwire trace.

## Final acceptance (live tree, all edits in; `scratchpad/final/`)

Run under the sandbox (network denied, production reads and writes denied), the
audit-hook tripwire, and the isolated root. **No tripwire log file was produced
by any run: zero events.**

| check | result |
|---|---|
| `test_billing_closure.py` | **256 / 0 / 0** (baseline 231) |
| `test_campaign_billing_record.py` | **115 / 0** (baseline 111) |
| `test_storage_query_layer.py` | **503 / 0** (baseline 498) |
| `test_storage_write_durability.py` | **122 / 0** (baseline 111) |
| `test_dashboard_truthfulness.py` | **161 / 0** (baseline 157) |
| `test_package_invariants.py` | **261 / 0** (baseline 261/0/0) |
| `test_storage_run_metrics_flush.py` | **130 / 0** (baseline 130/0) |
| `test_campaign_cohort_selection.py` | 116 / 0 |
| `test_resume_configuration_fingerprint.py` | 500 / 0 |
| `test_runner_preflight_and_state_faults.py` | 126 / 0 |
| `.github/scripts/static_checks.py` | all files compiled |
| `.github/scripts/ci_test_buckets.py --check` | consistent: 149 test files, 130 in bucket A |

## Exact next action for the P1b session

1. **Preserve first.** Confirm `git branch --show-current` is `wip/billing-closure`
   and HEAD is `f526c0c`. `git status --short` should show this session's
   UNCOMMITTED edits:
   - modified: `25- Batch Runner.py`, `CLAUDE.md`,
     `oncotriage/batch/runner.py`, `oncotriage/storage/database_logger.py`,
     `tests/test_billing_closure.py`, `tests/test_campaign_billing_record.py`,
     `tests/test_dashboard_truthfulness.py`,
     `tests/test_storage_query_layer.py`,
     `tests/test_storage_write_durability.py`;
   - new: `tests/_db_snapshot.py`, `RECOVERY_P4B_REPORT.md`.

   If they are not committed or backed up, ask the operator before editing
   anything.
2. **Define the scope.** "P1b" has no written definition. Ask the operator to
   name it. The candidates are RECOVERY_P1_REPORT.md "Unresolved issues" items
   2–8:
   - (2) a response priced above its reservation whose settlement fails;
   - (3) `missing`/`conflict` settlement parity;
   - (4) ragas possibly-billed failures charged nowhere;
   - (5) refused embeddings charged as possibly billed;
   - (6) in-flight open reservations;
   - (7) `BillingRecord.settle` with no production caller;
   - (8) test weaknesses.
3. **Rebuild containment from this report.** Items to rebuild:
   - `nonet.sb` with outbound denied except loopback, the mDNSResponder socket
     denied, and `file-write*` AND `file-read-data` denied under the four
     production directories and a decoy;
   - the audit-hook `sitecustomize` tripwire;
   - an isolated root from `provision_ci_paths.py`, holding a verified byte copy
     of the production database.

   Fire arms A/B/C under the sandbox only.
4. **Baselines in containment.**

   | test | expected |
   |---|---|
   | `test_billing_closure.py` | **256/0/0** |
   | `test_campaign_billing_record.py` | **115/0** |
   | `test_package_invariants.py` | **261/0/0** |
   | `test_storage_run_metrics_flush.py` | **130/0** |

   The item-2 files no longer open the production database, so they are safe
   to run, but still run them against the isolated root.
5. **Combined checks** (bucket A, the serial runner, `fixture_replay`) remain
   deferred, as this brief ruled.

## Revert-plant matrix (final; `scratchpad/p4b_plants.py`, `plants/p4b_matrix.json`)

**Setup.**

- **Copies:** `oncotriage/`, `tests/`, `.github/` and the top-level `*.py`, with
  a read-only `.git` symlink and `GIT_OPTIONAL_LOCKS=0`.
- **Isolation:** the editable finder stripped; a realpath preflight proves the
  copy is what imports; every anchor count must be exactly 1, and the result is
  `ast.parse`d.
- **Containment:** the sandbox, the tripwire and the isolated root. **The
  tripwire recorded zero events.**
- **Live tree:** ten watched files, including CLAUDE.md, were hashed before and
  after and were **byte-unchanged: True**. Nothing in the live tree was edited
  while this run was in progress.
- **The first matrix run is superseded.** Its hash check read False because
  runner.py wording was edited during that run, and its query-layer clean
  control lacked `.git`.

| plant | caught by |
|---|---|
| Q0 clean | 256/0, 115/0, 503/0, 122/0, 161/0 |
| Q1 recovery only when corrupt or resumed (the original defect) | 244/12: 4q, 4q-ii, 4q-v, 4q-vii, 4q-ix, 4w-file-ii, … |
| Q2 the --fresh watermark ignored | 254/2: 4q-iv, 4q-v |
| Q3 entry point does not record the watermark | closure 255/1 (4q-x); campaign 110/5 (6v, 6y, 6y-ii, 6z, 6za-ii) |
| Q4 the old "no spend / safe to remove" text | 252/4: 4w-file-i, 4w-dir-i, 4u-file_sync, 4u-dir_sync |
| Q5 recovery writes a charge | 242/14: 4h, 4q, 4q-ii, 4q-v, 4w-* |
| Q6 unreadable marker read as absent | 255/1: 4q-vii |
| R1 cost-tab render not redirected | query layer 501/2: the render's no-production-connect check, and the file-wide guard record |
| R1b production counted directly instead of a snapshot | query layer 499/1: the guard record. A **recorded failure**; the first run ABORTED here, fixed as F3 |
| R2 write durability counts the file directly | 119/3: 9c ×3 |
| R3 truthfulness renders the live path | 140/20: 10b and the section-10 figures |
| R4 guard refuses nothing | write durability 121/1 (9e). The query layer stays 503/0, as expected: clean code makes no production connect, so a disabled guard is unobservable there |
| R5 snapshot consistency unchecked | 121/1: 9e torn-copy control |
| R6 frozen copy not checkpointed | 121/1: 9e frozen-copy check |

## End state

- Branch `wip/billing-closure`, HEAD `f526c0c`. No commit, no stash (`git stash
  list` is empty), no branch change, no paid call. No arm ran outside the
  sandbox.
- Production `02- Data/03- Inferences Storage/` at the end: identical to the
  start table. Inodes are 83649952 / 86088010 / 86088009; births are
  2026-09-11 13:15:50 / 2026-09-13 18:48:43 / 18:48:43; sha256 are
  `47bab774…` / `fd4c9fda…` / `e3b0c442…`. Checked with `stat` and `shasum`
  only. Production `08- Checkpoint/` is unchanged (Sep 11 13:20 / 13:22).
- **Uncommitted changes:** 9 modified files plus 2 new ones
  (`tests/_db_snapshot.py`, this report).

## What was verified by running vs only read

- **Run:**
  - containment controls;
  - the item-1 premise and the fixed behaviour, in fresh processes;
  - every test table above, including through the REAL `main()` and the REAL
    entry point;
  - the digests, for the live tree and HEAD;
  - the plant matrix;
  - static checks and the bucket check;
  - production state at the start, mid-session and the end.
- **Read only:**
  - residuals R1 (cross-directory adoption on a first run), R2 (the clean-finish
    crash window), R3, R6 and R7;
  - that `recover_campaign_identity` has a single production caller (grep);
  - the snapshot cost on a large database (not measured).
