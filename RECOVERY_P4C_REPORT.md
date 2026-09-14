# Billing closure recovery — P4c (the --fresh marker)

Status: **P4c COMPLETE.** The defect was confirmed, fixed, driven end to end in fresh processes and plant-verified under containment. The work stopped at P4c: P1b and the combined checks were not started. The changes are UNCOMMITTED.

## Starting state

- Branch `wip/billing-closure`; HEAD `6a3b2973f0e7a557118fe500269bbb93974d627b`
  ("WIP: repair missing-identity recovery and isolate database tests").
  Verified with `git branch --show-current` and `git log -1`.
- The working tree was clean at the start: `git status --porcelain` was empty.
  P4b's edits are in HEAD.
- Production `02- Data/03- Inferences Storage/` at the start. Read with `stat`
  and `shasum` only:

  | file | inode | size | sha256 |
  |---|---|---|---|
  | `inferences.db` | 83649952 | 1,212,416 | `47bab774…` |
  | `-shm` | 86088010 | 32,768 | `fd4c9fda…` |
  | `-wal` | 86088009 | 0 | `e3b0c442…` |

- Production `08- Checkpoint/` holds `batch_runner_checkpoint.json` (`4b4e0f25…`)
  and `batch_runner_results.json` (`2b8e8481…`), among others. There is **no
  `batch_runner_fresh_start.json`**, so production holds no legacy marker.
  Full listing: `scratchpad/evidence/prod_start.txt`.

## Containment

P4b's profile, rebuilt in this session's scratchpad. Details:

- **`nonet.sb`** denies:
  - outbound traffic except loopback;
  - the mDNSResponder socket;
  - `file-write*` and `file-read-data` under the four production directories
    and a decoy.
- **The tripwire** is a `sitecustomize` audit hook.
- **The isolated root** comes from `provision_ci_paths.py`, run under the
  sandbox. No production copy was made: nothing in this item's suites reads
  production.

Controls. Every arm ran under the sandbox; none ran outside it.

| arm | external connect | raw socket | DNS | loopback | sqlite `mode=ro` to protected | write to protected | list protected |
|---|---|---|---|---|---|---|---|
| A2 sandbox + tripwire | refused (tripwire) | refused | refused | allowed | refused before open | refused | refused (EPERM) |
| C2 `env -i`, tripwire OFF, sandbox only | refused (EPERM) | refused | refused (gaierror 8) | allowed | refused (`unable to open`) | refused | refused |

With the sandbox only, a read of the production checkpoint file and a sqlite
open inside the production inferences directory were both refused. The decoy
sha256 was unchanged.

## (a) The premise — CONFIRMED (API level, fresh processes)

Driver: `scratchpad/repro_p4c.py`. Each phase is a new interpreter under the
sandbox and tripwire. **The tripwire log was never created: zero events.**

| step | result |
|---|---|
| build | 20 runs; that database was copied aside as the OLDER backup; the database was continued to run 100 |
| `--fresh` (`record_fresh_start` + `clear_checkpoint`) | marker `closed_through_run_id: 100` |
| restore | the backup was copied over the database |
| bill | run **21**, decision `new`, $1.25 settled + $0.40 reserved, total $1.65, 1 unresolved, finalized FAILED, no checkpoint |
| identity record deleted; observe | run 22: **decision `new`, a different campaign, seed $0.00, 0 unresolved**; the billing record still holds 2 rows / $1.65 / 1 reserved |
| observe again | run 23: `continued` on the new campaign, seed $0.00 |

**Cause.** `recover_campaign_identity` skips every campaign whose touched runs
are all `<= closed_through_run_id`. Run ids are not identities across database
swaps: run 21 of the restored database is a different run from run 21 of the
database the marker was written against. `record_fresh_start` also keeps the
higher cutoff. The result is an undercount.

## Baselines in containment, before any edit (`scratchpad/base/`)

| test | result | expected |
|---|---|---|
| `test_billing_closure.py` | 256 / 0 / 0 | 256/0/0 |
| `test_campaign_billing_record.py` | 115 / 0 | 115/0 |
| `test_package_invariants.py` | 261 / 0 / 0 | 261/0/0 |
| `test_storage_run_metrics_flush.py` | 130 / 0 | 130/0 |

No tripwire log was created by any of the four: zero events.

## (b) Design, stated before building

**Closed campaign IDENTITIES, each with a run snapshot.** The marker becomes
version 2:

`{"version": 2, "closed_campaigns": {"<campaign_id>": [[run_id, started_at], ...]}, ...}`

- **What `--fresh` closes.** Every billing campaign the database names
  (`runs.billing_campaign_id` ∪ `billing_attempts.campaign_id`), plus the
  campaign the identity record names if it is readable. Each campaign gets the
  set of runs it touched at that moment, as `(id, started_at)` pairs.
- **Earlier closures are preserved.** A v2 marker's closures are unioned
  per campaign.
- **How recovery uses it.** A campaign is skipped only when every run it
  touches now is in its closure snapshot.

Why the snapshot, and not ids alone. This is a refinement of the brief's
design, argued from a case the brief asks to check.

- **The crash case.** `--fresh` persists the marker and dies before
  `clear_checkpoint()`, so the identity record still names C1.
  1. The next ordinary run continues C1 and bills more under it.
  2. The record is then deleted.
  3. Ids alone would still exclude C1, and the post-crash charges would be
     forgotten.
- **With the snapshot**, the continuation run is a touched run outside the
  snapshot. The closure is void, and C1 is judged as an open campaign again.
  Over-count is possible; undercount is not.
- **Why the pair and not the run id alone.** Run ids are reused across a
  restored database, which is the defect itself. `started_at` is compared for
  EQUALITY only, never ordered, so no clock assumption is made.

**Legacy v1 (run-cutoff) markers are never converted.**

- **Record missing.** Recovery runs twice: once ignoring the cutoff, once
  applying it.
  - If both give the same state and campaign, the cutoff decides nothing and
    the run proceeds.
  - Otherwise the run is refused by name (`fresh_marker_unverifiable`) before
    any billed call.
- **Record present.** The marker is not consulted, exactly as before.
- **`--fresh` over a v1 marker.** It writes v2 closures from the current
  database. The old cutoff is kept as provenance only
  (`superseded_legacy_marker`) and decides nothing. Dropping its exclusion can
  only add recovery candidates: over-count or a named refusal, never
  undercount.
- **`--fresh` over an unreadable marker.** The file is copied aside, counted
  and announced, then replaced. `--fresh` is the gesture that closes what is
  there. The closures it held are lost, and losing them can only over-count.

**Durability is unchanged.** Writes use the shared `_write_json_durably`. A
marker that cannot be written refuses `--fresh` with exit 1, before
`clear_checkpoint()`.

`latest_run_id` loses its only caller and is deleted.

## (a, continued) Before/after on a discriminating scenario, fresh processes

`repro_p4c.py`, revised. Each phase is a new interpreter under the sandbox and
tripwire, with the editable finder stripped. `runner_file` is printed on every
phase, which proves which tree ran.

1. Build: run 5 bills campaign C_old ($3.00 settled) and ends FAILED, so C_old
   is open. The database is copied aside at 20 runs and continued to 100.
2. `--fresh`.
3. The older copy is restored.
4. Run 21 bills a new campaign: $1.25 settled + $0.40 reserved.
5. The identity record is deleted.
6. Observe.

| arm | marker written | observe at run 22 |
|---|---|---|
| **BEFORE**: HEAD from `git archive`, `scratchpad/headtree` | v1 `closed_through_run_id: 100` | **`new`, seed $0.00, 0 unresolved.** The billing record holds 3 rows / $4.65 / 1 reserved |
| **AFTER**: live, patched | v2 `closed_campaigns: {C_old: [[5, started_at]]}` | **`recovered_from_billing_record`, the run-21 campaign, seed $1.65, 1 unresolved.** C_old's closed $3.00 is correctly excluded: not $4.65, and no ambiguity refusal |

No tripwire log was created in either arm: zero events.

## Implementation (live tree)

- `oncotriage/storage/database_logger.py`:
  - `latest_run_id` deleted.
  - New `normalise_closed_campaigns` (the one validator), `_touched_run_pairs`
    (the one definition of "touched", with no parameter lists) and
    `campaign_closure_snapshot`.
  - `recover_campaign_identity(..., closed_campaigns=None,
    closed_through_run_id=0)`: identity closures are voided by any touched run
    outside the snapshot. The legacy cutoff is kept for the runner's comparison
    only. An ambiguity refusal now carries the closure notes.
- `oncotriage/batch/runner.py`:
  - `FRESH_MARKER_VERSION = 2`, `FRESH_MARKER_LEGACY_VERSION = 1`, and a
    `FreshMarker` NamedTuple.
  - `read_fresh_marker` (v1 and v2, strict shape) replaces
    `read_fresh_watermark`.
  - `record_fresh_start` writes v2 closures. It unions earlier v2 closures,
    keeps a v1 cutoff as `superseded_legacy_marker` only, and copies aside and
    counts an unreadable marker (`CHECKPOINT_FAULTS["fresh_marker_unreadable"]`).
  - `_recover_under_fresh_marker` runs the legacy two-way comparison. The new
    refusal `fresh_marker_unverifiable` has a remedy.
- `25- Batch Runner.py`: comment only. The order is unchanged: marker, then
  `clear_checkpoint()`.

## Digests (containment, live vs HEAD `git archive`; the import origin was verified)

| | live | HEAD |
|---|---|---|
| `renderer_digest` | `5ea2c6cc…a956` | identical |
| `PROMPT_VERSION` | 1.11.0 | 1.11.0 |
| `FINGERPRINT_VERSION` | 8 | 8 |
| `prompt_sha256` (mesh True / False) | `8baaa8b8…` / `db3c2b4d…` | identical |

The six renderer modules have zero diff lines.

## Tests, first pass after the fix (`scratchpad/r2`)

- **`test_billing_closure.py`: 274 / 0 / 0** (was 256).
  - `4q-iii/vi/viii` were rewritten for identity closures.
  - New checks `4y..4zd`:
    - restored database;
    - the crash between marker and clear;
    - `(id, started_at)` identity and malformed closure maps;
    - legacy markers: disagreeing is refused, moot proceeds, a present record is
      not consulted, conversion keeps provenance only;
    - malformed markers and `--fresh` over an unreadable one;
    - a record-only campaign.
- **`test_campaign_billing_record.py`: 120 / 7.** All seven failures were the
  same assumption in my new checks: that a stand-in run of the REAL entry point
  exits 0.
  - **Finding, read from code:** `reconcile_writes` sets `complete` only when
    `attempted > 0`. The harness's patient stand-in never writes through the
    runner's ledgered writer, so every such run finishes `main()` and exits
    **1** through `reconciliation_exit_code()`.
  - This is pre-existing. The P3/P4b entry-point checks never assert a return
    code.
  - A refusal also exits 1, so the checks now tell a completed run from a
    refusal by content: the reconciliation block is present, and there is no
    refusal block and no traceback.

## Tests, second pass (`scratchpad/r3`)

- **`test_campaign_billing_record.py`: 127 / 0** (was 115).
  - `6za-ii`'s message and label were updated.
  - New checks `6zb..6ze`, all through the REAL entry point or fresh child
    processes:
    - the restored-database scenario with both charge classes, and repeated
      recovery;
    - a legacy marker refused with no patient started, and `--fresh` over it;
    - an unreadable marker refused;
    - an unwritable marker, where `--fresh` exits 1 and discards nothing;
    - the crash between marker persistence and checkpoint removal.
  - A `fresh_only` child mode was added.
- No tripwire log was created: zero events.

## Self-review notes (in progress)

- **Callers.** `recover_campaign_identity` gained `closed_campaigns` before
  `closed_through_run_id`. A grep found no positional caller anywhere: the runner
  and every test pass keywords.
- **Counters.** `CHECKPOINT_FAULTS` is never cleared, so
  `fresh_marker_unreadable` recorded by `--fresh` reaches the run-end report of
  the same process.
- **Scan cost.** `runs.billing_campaign_id` has no index. The pre-existing
  recovery already scans it once per candidate campaign. The snapshot adds one
  scan per campaign in the database, and recovery adds one per closed candidate.
  Being measured (`perf_p4c.py`).

## Affected suites and static checks (`scratchpad/r4`, containment, live tree)

| check | result |
|---|---|
| `test_package_invariants.py` | 261 / 0 / 0 |
| `test_storage_run_metrics_flush.py` | 130 / 0 |
| `test_runner_preflight_and_state_faults.py` | 126 / 0 |
| `test_campaign_cohort_selection.py` | 116 / 0 |
| `test_resume_configuration_fingerprint.py` | 500 / 0 |
| `test_storage_schema_guards.py` | 136 / 0 |
| `static_checks.py` | 317 files compiled |
| `ci_test_buckets.py --check` | consistent: 149 test files, 130 in bucket A |

No tripwire log was created by any run: zero events. Full bucket A is deferred,
as the brief rules.

## Unresolved issues (P4c-scoped; details in the final report)

- **U1 (medium, over-count or refusal; never undercount).** `--fresh` over a
  version-1 marker keeps the cutoff as provenance only. The cutoff may have
  closed campaigns that live only in another copy of the database. If that copy
  is later swapped back in and its identity record is lost, those campaigns are
  recovery candidates again: continued (over-count) or refused as ambiguous.
  Unavoidable without converting the cutoff, which the brief forbids.
- **U2 (low).** A version-1 marker whose cutoff decides the answer refuses
  ordinary paid startup until an operator restores the record or runs `--fresh`.
  That includes a legitimate P4b-era `--fresh`, which can no longer be told apart
  from a restored database. Production `08- Checkpoint/` holds no marker, so no
  production directory is affected. P4b was never merged.
- **U3 (low).** `--fresh` over an unreadable marker replaces it, after copying
  it aside and counting it. Its closures are lost (over-count only).
- **U4 (low, scale).** The marker holds every campaign in the database with
  every run it touched, and `--fresh` unions closures, so it grows with history.
  `runs.billing_campaign_id` is unindexed. See the perf measurement.
- **U5 (inherited, unchanged).** The P4b residuals still stand:
  - cross-directory adoption on a first run (R1);
  - the clean-finish crash window (R2);
  - the concurrent first-run race (R6).

  A voided closure can now also be reached by another directory adopting a
  closed campaign; it is judged open (over-count or ambiguous refusal).

## Exact next action for the P1b session

1. **Preserve first.** Check `git branch --show-current` is `wip/billing-closure`,
   HEAD is `6a3b297`, and `git status` shows this session's UNCOMMITTED edits:
   - modified: `25- Batch Runner.py`, `CLAUDE.md`,
     `oncotriage/batch/runner.py`, `oncotriage/storage/database_logger.py`,
     `tests/test_billing_closure.py`, `tests/test_campaign_billing_record.py`;
   - new: `RECOVERY_P4C_REPORT.md`.

   If they are not committed or backed up, ask the operator before editing
   anything.
2. **Rebuild containment** from this report and fire the controls under the
   sandbox:
   - `nonet.sb`: outbound denied except loopback; the mDNSResponder socket
     denied; `file-write*` and `file-read-data` denied on the four production
     directories and a decoy;
   - the audit-hook `sitecustomize` tripwire;
   - an isolated root from `provision_ci_paths.py`.
3. **Baselines in containment.**

   | test | expected |
   |---|---|
   | `test_billing_closure.py` | **274/0/0** |
   | `test_campaign_billing_record.py` | **127/0** |
   | `test_package_invariants.py` | **261/0/0** |
   | `test_storage_run_metrics_flush.py` | **130/0** |

4. **P1b scope** (from the operator's brief):
   - **Embedding reservation bound.** Establish and verify a conservative upper
     bound on an embedding request's reservation. Byte length is only a
     proposal: verify it against the tokenizer's real worst case (multi-byte
     UTF-8, pathological strings, and truncation by the endpoint) before relying
     on it.
   - **Settlement parity.** Test MISSING and CONFLICTING settlements for
     live-versus-resumed parity, with an explicit conservative treatment.
     `RECOVERY_P1_REPORT.md` "Unresolved issues" items 2 and 3 are the
     starting evidence.
   - **Out of scope.** Ragas stays separate. The combined checks (bucket A, the
     serial runner, `fixture_replay`) stay deferred.

## Revert-plant matrix (`scratchpad/p4c_plants.py`, `plants/p4c_matrix.json`)

**Setup.**
- **Copies:** `oncotriage/`, `tests/`, `.github/` and the top-level `*.py`, with
  a read-only `.git` symlink.
- **Isolation:** the editable finder stripped; a realpath preflight proves the
  copy is what imports; every anchor count must be exactly 1, and the result is
  `ast.parse`d.
- **Containment:** the sandbox, the tripwire and the isolated root. **The
  tripwire recorded zero events.**
- **Live tree:** the watched files (`database_logger.py`, `runner.py`,
  `25- Batch Runner.py` and both test files) were **byte-unchanged**.
- **Outcome:** every plant was caught as RECORDED failures; none aborted.

| plant | caught by |
|---|---|
| P0 clean | 274/0, 127/0 |
| P1 closures ignored | closure 264/10 (4q-iv, 4q-v, 4q-vi, 4y, 4y-i, 4y-ii, 4z-ii, 4za, 4zb, 4zb-iii); campaign 120/7 (6v, 6y, 6y-ii, 6z, 6za-ii, 6zb, 6zc-i) |
| P2 closure never voided (the crash case) | closure 272/2 (4z-i, 4za-i); campaign 126/1 (6ze-ii) |
| P3 run identity by number only | closure 273/1 (4za-i) |
| P4 legacy cutoff applied on trust | closure 272/2 (4zb, 4zb-iii); campaign 125/2 (6zc, 6zc-i) |
| P5 a later `--fresh` drops earlier closures | closure 273/1 (4q-vi) |
| P6 entry point continues after a marker refusal (P4b guarantee) | campaign 126/1 (6zd-i) |
| P7 unreadable marker read as absent | closure 272/2 (4q-vii, 4zc-ii); campaign 126/1 (6zc-ii) |
| P8 bool version accepted | closure 273/1 (4zc) |
| P9 normaliser accepts a bool or negative run id | closure 272/2 (4za-ii, 4zc) |
| P10 unreadable marker not preserved | closure 273/1 (4zc-ii) |
| P11 record-named campaign not closed | closure 273/1 (4zd) |
| **P12 the original defect** (`--fresh` writes a run cutoff, and recovery applies it) | closure 264/10 (4q-iii, 4q-v, 4q-vi, 4y-i, 4y-ii, 4z-i, …); campaign 120/7 (6za-iii, 6zb, 6zb-ii, 6zb-iii, 6zc, 6zc-i, …) |

## Performance (`perf_p4c.py`, containment, zero tripwire events)

**Setup.** A scratch database in which every run shares one stamp and cohort, so
every campaign is a recovery candidate: the worst case. Timings are the best of
three.

| scale | HEAD recovery | live recovery, no closures | snapshot (`--fresh`) | live recovery, all closed | marker |
|---|---|---|---|---|---|
| 201 campaigns / 2,002 runs / 10,001 billing rows | 46 ms | 46 ms | 48 ms | 93 ms | 78 KB |
| 1,001 campaigns / 10,002 runs / 50,001 billing rows | 1.55 s | 1.60 s | 1.61 s | 3.46 s | 395 KB |

**Findings.**
- **Growth.** It is super-linear, because `runs.billing_campaign_id` is
  unindexed: one full `runs` scan per campaign.
- **Pre-existing cost.** Recovery without closures costs the same as at HEAD.
- **What P4c adds.** The snapshot, taken once per `--fresh`, and a second scan
  per closed candidate. Recovery with every campaign closed is about 2× slower.
- **Where the cost lands.** Only on the record-missing path and on `--fresh`,
  before any billed call; the ordinary continued path is unaffected.
- **Not measured.** Real campaign histories, and the legacy two-way comparison
  (2× recovery, legacy markers only).
- **Proposal, out of scope.** An index on `runs.billing_campaign_id`, which is a
  schema-era change.

## Self-review (critical pass over the full session diff)

**Invariant and owner.** A billing campaign's charges may be excluded from a
restart only when this checkpoint directory's operator closed that exact
campaign with `--fresh`, and only while it has billed in no run since.
- The OWNER is `recover_campaign_identity`.
- There is one definition of "touched": `_touched_run_pairs`.
- There is one validator: `normalise_closed_campaigns`.
- **Bypass paths checked.** Its only production caller is
  `_recover_under_fresh_marker`, and `establish_billing_campaign` reaches it on
  every record-missing or unreadable path. `record_fresh_start` is the only
  writer. A record-present run never reads the marker, as before.

**Money path.** Voiding a closure can only ADD candidates, so it cannot
undercount. A closure applies only to runs present at `--fresh` time,
identified by uuid campaign and `(id, started_at)` run, so a restored database,
a swapped database or another database's campaigns cannot match it by accident.

**Failure, retry and restart.**
- The marker write is durable, and a failure exits 1 before
  `clear_checkpoint()` (driven: 6zd-i, P6).
- A crash after the marker and before the clear is driven (4z, 6ze).
- Repeated recovery adds nothing (4y-ii, 6zb-iii).
- A legacy or unreadable marker refuses by name before any billed call (4zb,
  6zc, 6zc-ii).

**Concurrency.** `--fresh` runs under the checkpoint directory's run lock.
Another directory billing in a snapshot run after the snapshot does not void
the closure, which is correct: that campaign is closed for THIS directory.

**Boundaries, driven (4za-ii, 4zc).** Covered inputs:
- a bool, negative or text cutoff;
- a missing or malformed closure map;
- an unknown version and a bool version;
- a non-object marker;
- non-dict provenance;
- an empty v2 marker, which reads.

A billed run with no run row gives `started_at` None, and the pair is still
compared.

**Clock.** `started_at` is compared for equality only; no ordering or "now" is
used.

**Security and privacy.** Campaign ids from the marker are used only as dict
keys and never interpolated into SQL; every query is parameterized. The marker
holds campaign uuids and run timestamps, with no patient data. The console
warnings include file paths and JSON error text only.

**Documentation.** Stale claims were corrected:
- the `clear_checkpoint` docstring;
- the marker comment block;
- the entry-point comment;
- the recovery docstring;
- CLAUDE.md's P4b "What is not done" item 1, which described the run-number
  watermark;
- the test counts.

The historical P4b report was not edited.

**Found and fixed during this session.**
- **My closure-test patch** would have run `4zb`/`4zc` against the wrong
  checkpoint directory (`paths._RESOLVED` had been repointed); it was caught
  before running.
- **My first end-to-end checks** asserted exit 0 for stand-in entry-point runs,
  which always exit 1 through the reconciliation verdict. They now tell a
  completed run from a refusal by content.
- **The perf script** swapped two INSERT parameters; it was caught by review
  before any measurement.

## End state

- Branch `wip/billing-closure`, HEAD `6a3b297`. No commit, no stash, no branch
  change, no paid call. No arm, test, plant or probe ran outside the sandbox.
- Production `02- Data/03- Inferences Storage/` and `08- Checkpoint/` are
  IDENTICAL to the start: inodes, sizes, mtimes, births, sha256, and the
  checkpoint listing and hashes. Checked with `stat` and `shasum` only
  (`scratchpad/evidence/prod_start.txt` vs `prod_end.txt`).
- Renderer and prompt digests are unchanged (see Digests).

## Verified by running vs only read

- **Run:**
  - the containment controls;
  - the premise, before and after, in fresh processes, against HEAD and the
    live code;
  - every test table above, including through the REAL entry point;
  - the plant matrix;
  - the digests;
  - the static and bucket checks;
  - the performance measurement;
  - production state at the start and the end.
- **Read only:**
  - U1–U5;
  - the absence of positional callers (grep);
  - that `CHECKPOINT_FAULTS` is never cleared (grep);
  - the cause of the stand-in exit code (`reconcile_writes`);
  - the concurrency reasoning for other checkpoint directories.
