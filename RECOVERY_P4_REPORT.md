# Billing closure recovery — P4 (durability)

Status: **P4 FINISHED IN THIS SESSION.** The directory-sync gap is fixed; failure injection, commit-before-dispatch and cost are driven and measured; the production side-file culprit is established (a test, outside P4, not fixed). Stopped at P4 as instructed.

## Starting state

- Branch `wip/billing-closure`, HEAD `23db3ebc68b9fe6243155c79e904fa8737a4acd7`
  ("WIP: recovery P2, counter provenance verified. NOT FOR PUSH"), checked with
  `git branch --show-current` and `git log -1`.
- Dirty at start (the P3 session's uncommitted edits, all preserved):
  - ` M CLAUDE.md`
  - ` M oncotriage/dashboard/tabs/run_health.py`
  - ` M tests/test_billing_closure.py`
  - ` M tests/test_campaign_billing_record.py`
  - `?? RECOVERY_P3_REPORT.md`
- Production `02- Data/03- Inferences Storage/` at start (saved to the session
  scratchpad as `evidence/prod_side_files_at_start.txt`):

  | file | inode | size | mtime | birth | sha256 |
  |---|---|---|---|---|---|
  | `inferences.db` | 83649952 | 1,212,416 | 2026-09-11 13:22:43 | 2026-09-11 13:15:50 | `47bab774…` |
  | `inferences.db-shm` | 86088010 | 32,768 | 2026-09-13 18:48:43 | 2026-09-13 18:48:43 | `fd4c9fda…` |
  | `inferences.db-wal` | 86088009 | 0 | 2026-09-13 18:48:43 | 2026-09-13 18:48:43 | `e3b0c442…` (empty) |

  The database header bytes 18–19 are `02 02`, so the file is in WAL mode.

## Containment (rebuilt, and stronger than P3's recipe)

Built in this session's scratchpad (`nonet.sb`, `tw/sitecustomize.py`,
`tw/_onc_tripwire.py`, `run.sh`).

- **OS sandbox** (`sandbox-exec`):
  - outbound network is denied except loopback;
  - **the mDNSResponder unix socket is denied too**, so DNS fails at the OS
    layer (P3's profile allowed every unix socket, and DNS resolved under the
    sandbox alone);
  - **file writes are denied** under the production
    `02- Data/03- Inferences Storage`, `08- Checkpoint`,
    `09- Testing/Evaluation Runs`, `04- Results`, and a scratch decoy
    directory.
- **Tripwire as `sitecustomize`**, chaining the conda sitecustomize and built
  on **`sys.addaudithook`**, which no later code can remove. It:
  - refuses and logs non-loopback `socket.connect` and `socket.getaddrinfo`;
  - refuses and logs `sqlite3.connect` **before the file is opened** when the
    path is under a protected directory;
  - refuses writes through `open` into a protected directory;
  - logs (and allows) every connect to a watched path, with its URI mode, the
    state of the side files at that instant, and optionally the full stack.
- **Redirect:** tests run with `ONCOTRIAGE_MAIN_PATH` pointing at an isolated
  root (`isoroot/03- Code`, built by `.github/scripts/provision_ci_paths.py`).
  The root holds a byte-identical copy of the production database, copied as
  bytes (sha256 `47bab774…` both), so "production" in every test resolves to
  the copy. Tests that launch the real entry point isolate their own lock
  directory (`_control_harness.isolate_locks`).

Controls fired before any provider-touching test. The sqlite and write
controls targeted the **decoy**, never production:

| arm | external connect | raw `_socket` connect | DNS | loopback | sqlite `mode=ro` to protected | write to protected |
|---|---|---|---|---|---|---|
| A: sandbox + tripwire | refused, logged | refused, logged | refused, logged | allowed | refused before open, no side files created | refused, logged |
| B: child with its OWN `usercustomize` that reloads `socket` | same | same | same | allowed | same | same |
| B2: the same, hook dir first on `PYTHONPATH` | same | same | same | allowed | same | same |
| C: env-stripped child (`env -i`, no tripwire), sandbox only | refused (EPERM) | refused | **refused (gaierror)** | allowed | refused (OperationalError), no side files created | refused (EPERM) |

**A CONTAINMENT BREACH BY THIS SESSION, REPORTED.** A fourth arm, "D", was run
with neither the sandbox nor the tripwire, to show the probe could detect
anything at all. It made real outbound traffic:

- a TCP connect to `1.1.1.1:443`;
- a TCP connect to `8.8.8.8:53`;
- a DNS resolution of `example.com`.

There was no credential, no provider and no paid call. It also wrote a file and
side files into the scratch decoy, which were then removed. D was not rerun; A,
B, B2 and C are the controls. It should have been run under the sandbox or not
at all.

## FIRST TASK: the production `-wal` / `-shm` mtimes — culprit ESTABLISHED

**Finding.**

- **What creates the side files:** `tests/test_storage_query_layer.py`, via
  `_production_inference_rows()` (line 648, called at 658 and 4945). It opens
  the RECORDED production path through a `mode=ro` URI.
- **What deletes them:** between those two opens, the same test's cost-tab
  render (line 4077: `render_cost_tokens_tab` → Streamlit cache →
  `oncotriage/dashboard/data.py:95 load_trial_matches_data`) runs a **plain
  read-write `sqlite3.connect(paths.inferences_path)` against the unredirected
  production path**. A read-write connection is an unintended write-capable
  production access by a test.

**Mechanism, reproduced on a disposable WAL database** (`evidence/repro.py`,
SQLite 3.45.3):

| action on a WAL db | `-wal` | `-shm` |
|---|---|---|
| `mode=ro&immutable=1` open, no side files | absent | absent |
| `mode=ro` open, no side files | **created, 0 bytes** | **created, 32,768 bytes** |
| `mode=ro` again | unchanged inode | unchanged inode |
| plain read-write connect + close | **deleted** | **deleted** |
| `mode=ro` after that | **re-created, new inode** | **re-created, new inode** |

A `mode=ro` open alone leaves exactly the production state: a 0-byte `-wal` and
a 32,768-byte `-shm`.

**Evidence that it was this test in the P3 session.**

- The production side files' **birth time is 2026-09-13 18:48:43**; they are new
  inodes, not just new mtimes.
- The P3 session's `final/` batch (saved in its scratchpad) launched eight tests
  in parallel at 18:48:36, and `final/test_storage_query_layer.log` closed at
  **18:48:44**.
- The P3 report's "18:26" corresponds to its `base/` batch, whose
  `test_storage_query_layer.log` closed at 18:26:24.
- `test_dashboard_truthfulness` (the other production reader) uses
  `mode=ro&immutable=1`, which creates nothing, and it was absent from the
  18:26 batch.

**Reproduced on the isolated copy with the audit hook**
(`evidence/test_storage_query_layer.tw.jsonl`, side files removed first):

| time | open | caller | side files at that instant |
|---|---|---|---|
| t=0.000 | `mode=ro` | test line 658 | `-wal` absent, `-shm` absent |
| t=0.819 | **plain (read-write)** | test line 4077 → `data.py:95` | both present |
| t=1.352 | `mode=ro` | test line 4945 | **both absent again** (deleted by the read-write close) |

The files left behind were born at the LAST open. The copy's main file sha256
is unchanged, and so is the production file's, because the WAL was empty.

**Isolation proof.** The nine-test batch was run against the isolated root under
the sandbox and tripwire. `test_storage_query_layer` produced 3 watched connects
to the COPY and `test_dashboard_truthfulness` produced 12 (all
`immutable=1`); every other test produced none; **zero REFUSED events**.
Production state after the batch is identical to the start table (same inodes,
births and sha256s).

Not fixed (outside P4). Proposal: see "Unresolved issues".

## Baselines in containment (against the isolated root), before any edit

| test | result | expected |
|---|---|---|
| `test_billing_closure.py` | 193 / 0 / 0 skipped | 193/0 |
| `test_campaign_billing_record.py` | 111 / 0 | 111/0 |
| `test_package_invariants.py` | 261 passed, 0 failed (skip count not printed as non-zero) | 261/0/0 |
| `test_storage_run_metrics_flush.py` | 130 / 0 | 130/0 |
| also `test_storage_schema_guards` 136, `test_storage_query_layer` 498, `test_dashboard_run_health` 196, `test_dashboard_app_integration` 113, `test_dashboard_truthfulness` 157 | all 0 failed | |

A SECOND production reader was seen in the later batch:
`tests/test_storage_write_durability.py:300` opens the recorded path with
`mode=ro` (twice) — against the copy here. On a production database with no
side files it would create them too, though it has no read-write open of its
own.

## P4-relevant changed files (inherited, from the diff against `e96ec31`)

| file | P4 content |
|---|---|
| `oncotriage/storage/database_logger.py` | `BillingDurabilityUnavailable`; `_open_billing_connection` (sets `synchronous = FULL`, reads back ≥ 2; on darwin sets `fullfsync = ON`, reads back 1); used by `reserve_billing_attempt`, `settle_billing_attempt`, `set_run_billing_campaign_id`; `recover_campaign_identity` and its five states |
| `oncotriage/batch/runner.py` | `_durable_sync`; `write_campaign_record` (temp file, durable sync, `os.replace`, directory sync); `clear_campaign_record` (removal synced); `establish_billing_campaign` (recovery, refusals, record written before the stamp); `main()` refusal handler (KILLED, exit 1) |
| `oncotriage/spend.py` | `BillingRecord.reserve` raises `BillingRecordUnavailable` and latches `billing_record` when the reservation cannot be persisted |
| `oncotriage/provider_resilience.py` | `attempt_record.begin()` after the pacing wait and BEFORE `send()`; a refusal there is marked pre-send and not dispatched |
| `tests/test_billing_closure.py` | section 4 (4a..4q) |

## State found, with evidence

**Classification: PARTIAL.** The mechanisms were implemented and correct as far
as they went; one behaviour contradicted the brief, and the brief's
failure-injection and proof obligations were largely untested.

| requirement | found | evidence |
|---|---|---|
| synchronous FULL set and read back before the transaction on every billing-writing connection | **implemented** | Every writer of `billing_attempts` rows and of `runs.billing_campaign_id` goes through `_open_billing_connection` (grep of INSERT/UPDATE sites). 4b traces the statement order: the pragma comes before INSERT/UPDATE for the reservation, the settlement and the stamp. |
| readback is not proof | **not addressed** | Nothing showed a sync actually happens. |
| reservation committed and synced before dispatch | **implemented; untested** | `provider_resilience.execute` calls `attempt_record.begin()` before `send()`; 4c showed a refused reservation is not dispatched. Nothing showed the row is COMMITTED at the moment of dispatch. |
| identity file durable before the first billed call | **implemented** | `establish_billing_campaign` writes the record, then stamps the run row, before the sink is installed in `main()` (read); 4e pins the sync order. |
| resume with missing / corrupt identity: recover or refuse by name | **implemented** | 4h..4q. |
| failed commit → no dispatch, named refusal, safe restart | **untested** | Only 4c (a pragma readback refusal), in-process; no restart. |
| failed file sync | **partial** | 4f (F_FULLFSYNC refusal on the file, darwin only, in-process); no fsync failure, no `main()`, no restart. |
| failed directory sync | **CONTRADICTED** | `_durable_sync` COUNTED an F_FULLFSYNC refusal on the directory and carried on, so a campaign started with its rename only fsync-durable. An fsync failure on the directory did refuse, but nothing tested it. |
| durability bound stated | **implemented, overstated** | The comment implied `fullfsync` guarantees a drive flush. SQLite falls back to fsync silently when F_FULLFSYNC fails, and a readback cannot see that. |
| write cost measured | **claimed, not verifiable** | CLAUDE.md numbers came from a prior session with no saved evidence. |
| P3 interaction: a stamp refusal leaves the run row without an id and KILLED | **implemented, untested** | Read in `main()`; never driven. |

Unrelated to P4 but found: `CampaignBillingRefusal.lines()` had no remedy for
`campaign_record_unwritable`, so an identity-file sync failure printed "Fix the
inference database the billing record lives in", which is the wrong file.

## Changes made in this session

1. **`oncotriage/batch/runner.py` `_durable_sync`:** a directory F_FULLFSYNC
   refusal now RAISES (as the file's always did). The caller refuses the
   campaign by name before any billed call. The docstring states the reason and
   the remedy.
2. **`write_campaign_record`:** the refusal names the failed stage (writing /
   syncing the temp file / renaming / syncing the directory after the rename).
   The comment states what each stage leaves on disk and why both outcomes are
   safe.
3. **`CampaignBillingRefusal.lines()`:** a correct remedy for
   `campaign_record_unwritable`.
4. **`oncotriage/storage/database_logger.py`:** the durability bound comment now
   says a readback is not a sync. It states SQLite's silent fsync fallback, cites
   the measured F_FULLFSYNC evidence, and says durability is to the commit.
5. **`tests/test_billing_closure.py`:** 193 → **231**.
   - **4f-i, 4f-ii:** the stage is named; a directory F_FULLFSYNC refusal
     refuses and leaves the renamed record and no temp file.
   - **4r / 4r-i / 4r-ii:** at the instant the provider stand-in is called, an
     INDEPENDENT read-only connection already reads the reservation (under WAL,
     only committed data is visible), with a control sink that "reserves" in
     memory and is caught by the same read.
   - **Section 4 (continued)**, through the REAL `main()` in child processes
     (the section-5 harness gained an `inject` switch that breaks one durable
     write below the code under test), with checks **4s-*** and **4t**. For
     `commit_stamp` (a commit that raises on the run-row stamp), `stamp_weak`
     (the stamp's connection reads back synchronous OFF), `file_sync` (fsync of
     the identity temp file raises), `dir_sync` (fsync of the directory raises)
     and `dir_fullfsync` (darwin), each run checks:
     - exit 1, **zero provider calls**, the printed refusal naming its reason,
       and "NOTHING HAS BEEN BILLED";
     - the run row KILLED, finished, `billing_campaign_id` NULL, and zero
       billing rows (the P3 interaction);
     - the identity record left or absent as expected, with no temp file;
     - the failed stage named.

     Then an ordinary restart must:
     - exit 0 with 12 wire attempts;
     - reach the expected decision (`new` after `file_sync`, `continued` after
       the others);
     - stamp its run row with the recorded campaign;
     - leave every billing row that run's and settled;
     - hold a budget equal to its own rows, with none unresolved.
   - **4t / 4t-i:** a reservation commit that raises mid-run. Zero provider
     calls, the run latched under `billing_record`, zero billing rows, a
     `reserve:` fault, and the run row carrying `stop_reason = 'billing_record'`
     with its campaign id. The restart continues the campaign safely.
6. **`CLAUDE.md`:** the P4 paragraph and the count line.
7. **A FLAKY CHECK OF MY OWN, FOUND BY THE PLANT MATRIX AND FIXED.**
   `4s-commit_reserve-restart` pinned the refused run's status as FAILED. The
   matrix's P5 copy produced STOPPED, for a reason unrelated to P5: after the
   reservation latch, the status is a SCHEDULING fact. It is FAILED when both
   patients had started before the latch, and STOPPED when the latch kept the
   second from starting. Both are correct statuses. The check now accepts
   either, and 4t-i still pins what must not vary (`stop_reason =
   'billing_record'`).

## Tests and results (all in containment: sandbox + audit-hook tripwire + isolated root)

After the edits, before the flaky-check fix (batch `r2`; zero REFUSED events;
the only watched connects were `test_storage_write_durability`'s two `mode=ro`
opens of the COPY):

| test | result |
|---|---|
| `test_billing_closure.py` | **231 / 0 / 0 skipped** |
| `test_campaign_billing_record.py` | 111 / 0 |
| `test_package_invariants.py` | 261 / 0 / skipped 0 |
| `test_storage_run_metrics_flush.py` | 130 / 0 |
| `test_storage_run_identity.py` | 159 / 0 |
| `test_runner_preflight_and_state_faults.py` | 126 / 0 |
| `test_spend_gate.py` | 165 / 0 |
| `test_degradation_counter_readers.py` | 160 / 0 |
| `test_storage_schema_guards.py` | 136 / 0 |
| `test_storage_write_durability.py` | 111 / 0 |
| `static_checks.py` | 316 files compiled |
| `ci_test_buckets.py --check` | consistent, 149 test files, 130 in bucket A |

### P4 revert-plant matrix (scratchpad `p4_plants.py`)

- **Copies:** `oncotriage/`, `tests/`, the top-level `*.py` and
  `pyproject.toml`, under `plants/<name>/03- Code`.
- **Isolation:** the editable-install finder stripped; a realpath preflight
  proves the copy is what imports; each anchor count asserted to be exactly 1
  and the result `ast.parse`d; run under the sandbox and tripwire against the
  isolated root.
- **After:** the live source files and the test file were sha256-compared,
  **byte-unchanged**, and the tripwire recorded **zero events**.

| plant | result | caught by |
|---|---|---|
| P0 clean | 231 / 0 | — |
| P1 directory F_FULLFSYNC tolerated again (the reverted change) | 225 / 6 | 4f-ii, 4s-dir_fullfsync (and -i, -iii, -restart, -restart-i) |
| P2 `PRAGMA synchronous = FULL` not issued | 221 / 10 | 4b ×3, 4c, 4c-i, 4c-ii, 4s-stamp_weak (and -i, -restart, -restart-i) |
| P3 synchronous read-back not checked | 223 / 8 | 4c, 4c-i, 4c-ii, 4s-stamp_weak (and -i, -iii, -restart, -restart-i) |
| P4 reservation never committed (read back inside its own transaction) | 131 / 100 | **4r**, 4r-ii, 4d, 4t, section 1, section 5, every restart |
| P5 identity record's directory sync removed | 218 / 13 | 4e, 4f-ii, 4s-dir_sync, 4s-dir_fullfsync (and their -i, -iii, -restart) |
| P6 `main()` refusal does not finalize KILLED | 221 / 10 | every 4s-*-i (run row RUNNING) and every restart |
| P7 run row stamped BEFORE the identity record (P3 interaction) | 222 / 9 | **4s-file_sync-i** (the refused run carries an id), 4s-dir_sync-i, 4s-commit_stamp-ii, … |
| P8 temp file not removed on failure | 229 / 2 | 4f, 4s-file_sync-ii |
| P9 a reservation persistence failure swallowed (dispatch proceeds) | 227 / 4 | 4c, 4c-i, **4t**, 4t-i |

None of the nine aborted a file.

### A genuine full-disk failure (scratchpad `diskfull/probe.py`, corroboration, not a committed test)

The probe used a 4 MiB HFS+ image attached in the scratchpad, filled to
`ENOSPC`, with a real reservation carrying a 200 KB note:

- `reserve_billing_attempt` raised `BillingRecordWriteError`, wrapping
  `OperationalError: unable to open database file`. The failure came **at
  connection open, not at commit**, so the planted commit error remains the
  only commit-phase drive.
- Through the real `call_matching_model`, it raised `Stage5SpendStopped`: the
  stand-in provider was called **0** times and the latch read `[True,
  'billing_record']`.
- After freeing the space: only the earlier good reservation exists, and
  `integrity_check` is `ok`.
- The image was detached.

### Write cost, measured (scratchpad `measure_p4.py`, `evidence/measure_p4.json`)

The real `reserve_billing_attempt` / `settle_billing_attempt` ran on disposable
WAL databases on the same APFS volume as production (`/System/Volumes/Data`).
The shape was 12 patients × 16 attempts, a per-patient bound of 4, plus one
inference write per patient under the same `_WRITE_LOCK`. Lock wait was
measured by a timing proxy on that lock.

| setting (read back: synchronous, fullfsync) | serial reserve p50 / p95 | burst: reserve lock wait p50 / p95, added per attempt p50 | 1–3 s provider latency: added per attempt p50 / p95 / max | burst wall for 192 attempts |
|---|---|---|---|---|
| NORMAL, fullfsync OFF (1, 0) | 0.55 / 0.72 ms | 31 / 96 ms, 51 ms | 5.0 / 23.8 / 27.7 ms | 0.25 s |
| FULL, fullfsync OFF (2, 0) | 0.57 / 0.67 ms | 28 / 105 ms, 29 ms | 4.5 / 25.6 / 31.9 ms | 0.23 s |
| **shipped: FULL + fullfsync (2, 1)** | **14.5 / 20.0 ms** | **724 / 817 ms, 1,494 ms** | **243 / 642 / 761 ms** | **6.04 s** |

Raw syscalls after a 4 KiB write: `fsync` 0.13 ms p50, `F_FULLFSYNC` 4.7 ms p50.

**What it shows:**

1. The ~14 ms per commit with fullfsync ON, against ~0.5 ms OFF, is the
   F_FULLFSYNC cost. This is the **behavioural evidence that the pragma makes
   SQLite issue the call** on this build, which a read-back cannot give.
2. FULL costs nothing over NORMAL on this volume, because plain fsync is
   ~0.1 ms.
3. **The cost is MATERIAL under concurrency**, because every billing commit is
   serialized under the process-wide `_WRITE_LOCK`. At 1–3 s provider latency
   it adds 12% (p50) to 32% (p95) of a 2 s call. Against Stage 5's real
   seconds-to-tens-of-seconds calls it is a few percent. `log_inference` and
   the health flush queue behind the same lock (patient-end inference write:
   96 ms p50 / 471 ms p95 in the burst).

**Options, NOT chosen here** (a decision for the operator):

- (a) a billing-only lock, so billing commits stop queueing inference writes;
  the F_FULLFSYNC serialization remains per file;
- (b) group commit: batch concurrent reservations into one transaction, which
  keeps "committed before dispatch" but adds a small batching delay;
- (c) fullfsync OFF on darwin: ~25× cheaper, and it re-opens the drive-cache
  window on power loss;
- (d) accept it as it is.

The per-attempt added delay scales with the number of concurrent in-flight
attempts (12 × 4 = 48 here), not with the provider latency.

### Final runs (after the flaky-check fix, in containment)

| run | result |
|---|---|
| `test_billing_closure.py`, three copies run CONCURRENTLY | **231 / 0 / 0**, **231 / 0 / 0**, **231 / 0 / 0** |
| `test_campaign_billing_record.py` | **111 / 0** |
| `test_package_invariants.py` | **261 / 0 / skipped 0** |
| tripwire | no events in any final run |
| production `inferences.db`, `-shm`, `-wal` at end | **identical to the start table**: inodes 83649952 / 86088010 / 86088009, births 2026-09-11 13:15:50 / 2026-09-13 18:48:43 / 18:48:43, sha256 `47bab774…` / `fd4c9fda…` / `e3b0c442…` |
| production `08- Checkpoint/` | unchanged (checkpoint and results files still Sep 11 13:20 / 13:22) |

## Unresolved issues (ranked)

1. **Tests open the RECORDED production database, one of them read-write.**
   - `tests/test_storage_query_layer.py:4077` renders the cost tab while
     `paths.inferences_path` is unredirected, so `dashboard/data.py:95` opens
     production read-write. On a WAL database with uncheckpointed frames that
     close would CHECKPOINT into the production file.
   - Lines 658/4945 there, and `tests/test_storage_write_durability.py:300`,
     open it `mode=ro`, which creates side files.
   - This runs in every bucket-A execution, CI included (where the "production"
     path is the skeleton's).
   - Outside P4; not changed. **Proposal:**
     - (a) seed `paths._RESOLVED["inferences_path"]` with the test's scratch
       database around the cost-tab render;
     - (b) make the production probes `mode=ro&immutable=1` **only after
       verifying the file has no side files and is not being written**, or
       better, copy the file's bytes and count rows in the copy.

     `immutable=1` on a live file is exactly what the brief forbids, so
     "copy then read" is the right shape.
2. **The write cost is material under concurrency** (table above). A decision
   is needed among options (a)–(d).
3. **SQLite's silent fsync fallback is undetectable per commit.** On a
   filesystem that refuses F_FULLFSYNC, billing commits are fsync-durable while
   `fullfsync` reads back ON. Proposal: a one-time probe per database directory
   (`fcntl(F_FULLFSYNC)` on a temp file there) refusing by name when it fails,
   matching what the identity file already does.
4. **A commit-phase failure is driven only by a planted `OperationalError`.**
   The genuine full disk failed at open. A real commit-phase I/O failure (for
   example WAL growth hitting ENOSPC on a larger database) was not produced.
5. **Linux containers are not measured.** `fullfsync` is darwin-only; the
   container's fsync cost and the durability of its volume driver are unknown.
6. **The `dir_fullfsync` refusal is permanent on a filesystem that does not
   support F_FULLFSYNC on directories** (for example some network or exFAT
   volumes). That is by design now and named in the remedy, but no such
   filesystem was tried.
7. **A refused run leaves the identity record in place** after a
   directory-sync failure or a stamp refusal. It names a campaign with no
   spend, so continuing it is safe (driven), but a clean-looking record now
   exists for a run that never started.
8. **`4s-commit_reserve` runs with MAX_WORKERS ≥ 2**, so the refused run's
   status legitimately varies between FAILED and STOPPED. The check accepts
   both; nothing pins which.
9. **The containment breach** (control arm D) is described above.

## What was verified by running vs only read

- **Run:**
  - containment controls A/B/B2/C (and D, the breach);
  - the WAL side-file reproduction;
  - the culprit trace on the isolated copy, with full stacks;
  - every test table above, including three concurrent reruns;
  - the nine-plant matrix;
  - the full-disk probe;
  - the write-cost measurement;
  - `static_checks.py` and `ci_test_buckets.py --check`;
  - production state at start, mid-session and end.
- **Read only:**
  - that every writer of `billing_attempts` rows and
    `runs.billing_campaign_id` goes through `_open_billing_connection` (a grep
    of INSERT/UPDATE sites; 4b traces three of them);
  - that `attempt_record.begin()` precedes `send()` in
    `provider_resilience.execute` (4r measures the effect, not the line);
  - SQLite's F_FULLFSYNC fallback (from knowledge of `os_unix.c`, not
    re-inspected here; the timing evidence shows the call is issued on this
    build, not what happens when it fails);
  - that the identity record is written before the sink is installed in
    `main()`.

## End state

- Branch `wip/billing-closure`, HEAD `23db3eb`; no commit, no stash, no branch
  change, no paid call.
- **THESE EDITS ARE UNCOMMITTED** (the P3 edits plus this session's). Commit or
  back them up before the P1b session.

## Exact next action for the P1b session

1. Confirm the branch and HEAD, and that the files listed in `git status` (P3's
   and P4's edits plus `RECOVERY_P3_REPORT.md` and `RECOVERY_P4_REPORT.md`) are
   committed to the WIP branch; if not, ask for that first.
2. Rebuild containment from this report: `nonet.sb` with the mDNSResponder and
   production write denials, the audit-hook `sitecustomize` tripwire, and
   `ONCOTRIAGE_MAIN_PATH` pointed at a `provision_ci_paths.py` root holding a
   BYTE COPY of the production database. Fire controls A, B and C (never an
   unsandboxed arm). **Do not run `tests/test_storage_query_layer.py` without
   the isolated root** (unresolved item 1).
3. Baselines in containment:

   | test | expected |
   |---|---|
   | `test_billing_closure.py` | **231 / 0 / 0** |
   | `test_campaign_billing_record.py` | **111 / 0** |
   | `test_package_invariants.py` | **261 / 0 / 0** |
   | `test_storage_run_metrics_flush.py` | **130 / 0** |

4. Take up P1b as RECOVERY_P1_REPORT.md defines it. P4 is closed except the
   decisions in unresolved items 2 and 3.
