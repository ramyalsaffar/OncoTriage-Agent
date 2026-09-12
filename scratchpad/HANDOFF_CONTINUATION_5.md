# HANDOFF — Provider-resilience completion, continuation 5

Written at capacity boundary. The working tree is preserved exactly as left.
Nothing in this file is reconstructed; anything not measured is marked MISSING.

Repo root (absolute):
`/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code`

---

## 1. Current objective and BINDING RULINGS

**Objective.** Finish items 4 and 5 of the continuation-4 work order, then the
completion boundary. Items 1, 2 and 3 are done (§3).

**BINDING RULINGS — verbatim, all remain in force:**

- no paid calls
- no network egress
- no production writes
- no commits
- preserve existing uncommitted work
- verified containment before any provider-touching test
- unknown-quota refusal in production
- pre-send classification requires a dedicated exception or evidence dispatch
  had not started
- batches.list paced under the management bucket
- client seams change only after the stub-site sweep is green under containment
- the approved scope exclusions (embedding/indexer client untouched; no
  per-deployment scope names)
- no new approval needed for already-authorized work

**Two rulings supersede earlier reasoning in this workstream and must be
followed as written above:**

1. `batches.list` IS paced under the management bucket. An earlier analysis in
   this session argued for excluding it (that pacing it could raise inside
   `reconcile_uncertain_submission`). That exclusion is NOT the ruling and must
   not be implemented. Pace it; handle the refusal per the pre-send rule.
2. Pre-send classification requires a DEDICATED EXCEPTION or positive evidence
   that dispatch had not started. An earlier plan proposed an `isinstance`
   tuple of five existing provider_resilience exception types. That is weaker
   than the ruling and must not be shipped as-is (see §7).

---

## 2. Files and baselines

### 2a. Modified (tracked) — 45 files

```
.github/scripts/ci_test_buckets.py
25- Batch Runner.py
26- Ablation Study.py
bedrock_probe.py
oncotriage/ablation/study.py
oncotriage/agent/bedrock_anthropic_adapter.py
oncotriage/agent/deps.py
oncotriage/agent/evaluation.py
oncotriage/agent/graph.py
oncotriage/agent/state.py
oncotriage/api/server.py
oncotriage/batch/runner.py
oncotriage/config.py
oncotriage/control.py
oncotriage/degradation.py
oncotriage/evaluation/ragas_harness.py
oncotriage/evaluation/rater.py
oncotriage/fixtures/capture.py
oncotriage/fixtures/replay.py
oncotriage/mcp/server.py
oncotriage/settings.py
oncotriage/spend.py
oncotriage/tracking.py
ragas_run.py
rater_run.py
tests/_control_harness.py
tests/_provider_pin.py
tests/run_serial_tests.py
tests/test_ablation_stop_and_lock.py
tests/test_agent_bedrock_adapter.py
tests/test_agent_bedrock_anthropic_adapter.py
tests/test_agent_bedrock_anthropic_per_trial.py
tests/test_agent_out_of_set_detector.py
tests/test_agent_stage5_per_trial_calls.py
tests/test_agent_structured_outputs.py
tests/test_degradation_counter_readers.py
tests/test_matching_temperature_policy.py
tests/test_package_invariants.py
tests/test_runner_preflight_and_state_faults.py
tests/test_runner_sigterm_shutdown.py
tests/test_runner_stop_switch.py
tests/test_spend_coverage.py
tests/test_spend_gate.py
tests/test_storage_packing_and_cache_columns.py
tests/test_storage_wipe_all_tables.py
```

### 2b. Untracked — 8 files

```
oncotriage/provider_quotas.py
oncotriage/provider_resilience.py
tests/test_openai_inference_seam.py
tests/test_provider_quotas_lookup.py
tests/test_provider_resilience.py
tests/test_provider_scope_lock.py
tests/test_ragas_pacing_safeguards.py
tests/test_rater_verify_before_retry.py
```

Total `git status --porcelain`: **53** entries.

### 2c. Session-start baseline hashes

**SNAPSHOT FILE PATH: MISSING.** No snapshot file was ever written to disk this
session. The values below were measured in-conversation only. Their sole
durable record is the session transcript (§6). Do not treat them as a
file-backed baseline; re-establish a written snapshot before the next
verification sweep.

| artefact (relative to repo root) | session-start sha256 | value now |
|---|---|---|
| `../02- Data/03- Inferences Storage/inferences.db` | `47bab774e152a620d1e20b8ca331e083fcb2f2307807b18544baa9b5ba6a2389` | identical |
| `../04- Results/02- Ablation/ablation_results.db` | `f2bc23c6566d2bba245b5af4d4828bdb727ce8e069d054f35a1cc45369f30eb6` | identical |
| `../09- Testing/Evaluation Runs/spend_journal.jsonl` | `a9682eb99c6ec94b84cd0164a11e15c58ebe1ecb13be977f65732a36eb75fc89` | identical (19 lines) |

| artefact | status |
|---|---|
| twelve characterization fixtures, per-file sha256 | **MISSING — never captured this session.** An aggregate "byte-identical" claim was made in an earlier continuation; it is NOT backed by hashes captured in this session and must not be cited as evidence. |
| `oncotriage/config.py` vs HEAD | 814 insertions / 39 deletions, ALL inherited handoff work. `git diff | grep -c DATA_SNAPSHOT_DATE` = **0**. Serial runner logged `config sha256 before == after` = `fb78b179…`. |

---

## 3. Requirements: completed vs unfinished

| item | verdict | evidence |
|---|---|---|
| 1. Quota ruling verification | **PASS** | Layering correct as-is; no code change needed. Control added: `tests/test_ragas_pacing_safeguards.py` §3b. Dispatch raises `QuotaUnknown`, `.family == "tokens"`, `.constant == "PROVIDER_TOKENS_PER_MINUTE"`, **send invoked 0 times**; both-configured control sends exactly once; MANAGEMENT/0-token reservation not refused. File: **66 passed / 0 failed**. |
| 2. Token estimate relabelled | **PASS** | `oncotriage/evaluation/ragas_harness.py:1727` — `CHARS_PER_TOKEN = 3.6` relabelled ESTIMATE; "always exceeds actual" implication removed; the 3.50 measurement recorded as a DIFFERENT tokenizer (Claude Sonnet 4.6, the Bedrock classifier) and therefore not supporting evidence. Verified: parses, imports, `_request_input_tokens({'messages':[{'content':'x'*360}]})` == 100 == 360/3.6. |
| 3. S1 client split | **PASS, with incident (§6)** | `config.py` `get_openai_inference_client()` (own cache, `OPENAI_INFERENCE_SDK_MAX_RETRIES = 0`); `deps.py` `OPENAI_INFERENCE_CLIENT` in `OVERRIDE_KEYS` + accessor with **inheritance**; `evaluation.py:719,1106` repointed; `capture.py` `_HOOKED_NAMES`/`_HOOK_KEYS`/`current_hook_targets`/`install_recording_hooks` extended to 5 seams; `replay.py` `install_replay_hooks` + `_probe_proxies` extended. Guard: `tests/test_openai_inference_seam.py` **33/0**, revert matrix **3/3** caught (clean control 33/0). Embedding/indexer client untouched (`agent/models.py:197`, `retrieval/indexer.py:1245`). |
| 4. S2 `_paced_management` | **FAIL — NOT IMPLEMENTED** | No code written. Design in §7. Reconnaissance complete. |
| 5. Verification debt | **FAIL — NOT STARTED** | Neither the copytree revert matrix over scope-lock wiring / four `isolate_locks` sites / rater reconcile production code, nor the ragas-venv stubbed-provider integration run. |

### Item 3 collateral repairs (all verified green)

- `tests/test_agent_bedrock_adapter.py:484,502` — stale pins updated to name the
  inference accessor AND assert the embedding accessor is absent.
- `tests/test_agent_bedrock_anthropic_adapter.py:463` — same treatment.
- `tests/test_package_invariants.py:5442` — `ACCESSORS` gained the tenth key;
  literal `9`→`10` at :5517; prose "nine"→"ten" at :5523; ":5529 "eight"→"ten"
  (that one was ALREADY stale before this session — it compared against a
  nine-element list).
- `.github/scripts/ci_test_buckets.py` — registered
  `test_ragas_pacing_safeguards.py` and `test_openai_inference_seam.py`.

---

## 4. Exact test commands and latest results

All run from repo root. Results are the LATEST measured value.

```
python tests/test_ragas_pacing_safeguards.py            # 66 passed / 0 failed
python tests/test_openai_inference_seam.py              # 33 passed / 0 failed
python tests/test_package_invariants.py                 # 261 / 0 / 0 skipped
python tests/test_agent_bedrock_adapter.py              # 296 passed / 0 failed
python tests/test_agent_bedrock_anthropic_adapter.py    # 424 passed / 0 failed
python tests/test_matching_temperature_policy.py        # 72 passed / 0 failed
python tests/test_agent_stage5_per_trial_calls.py       # 372 passed / 0 failed
python tests/test_agent_structured_outputs.py           # 158 passed / 0 failed
python tests/test_agent_out_of_set_detector.py          # 170 passed / 0 failed
python tests/test_fixture_call_mode_pin.py              # 91 passed / 0 failed
python tests/test_resume_capture_and_ragas.py           # 229 passed / 0 failed
python tests/test_evaluation_ragas_manifest.py          # 129 passed / 0 failed
python .github/scripts/ci_test_buckets.py --check       # consistent: 141 test files, 122 bucket A, 5 bucket B
python .github/scripts/static_checks.py                 # 306 files compiled
```

**Item-4 attribution baseline (captured under containment, BEFORE any rater
edit — use this to attribute any movement to the item-4 change):**

```
python tests/test_spend_coverage.py             # RESULTS: 168 passed, 0 failed   egress=0
python tests/test_rater_verify_before_retry.py  # RESULTS: 26 passed, 0 failed    egress=0   [SEE NOTE BELOW]
python tests/test_evaluation_rater.py           # Passed: 592  Failed: 0          egress=0
python tests/test_judge_independence.py         # Passed: 136  Failed: 0          egress=0
```

**NOTE added 2026-09-12 (repair session) — the 26 above is NOT drift, and the
record is annotated rather than corrected.** Re-measured under containment on
this date: `tests/test_rater_verify_before_retry.py` reports **27 passed, 0
failed, egress=0**. The delta is **one check ADDED after this baseline was
captured**, not a count that moved under a fixed file:

* the baseline states its own scope — "captured BEFORE any rater edit" — and
  item 4 was implemented afterwards;
* §7e of this same handoff predicted that item 4 would require this file to
  supply explicit test limits, which is an edit to this file;
* the file's `check(` call sites number 27 and it reports 27 passed with 0
  failed, so no check regressed into a failure or was skipped.

The original figure is left exactly as written: it was true of the tree at the
moment it was taken, and it is the attribution baseline the next reader needs.

**Stale / not re-run since the item-3 seam change — MUST be re-run:**

```
python .github/scripts/ci_test_buckets.py --run A   # last run 01:24, PRE-dates the seam split (10:34-10:36). Result then: 121 ran / 0 failed / 0 not run
python tests/run_serial_tests.py                    # last run: 5/5 in 456.9s, also PRE-dates the split
```

---

## 5. Containment — as specified vs as built

**Location (absolute):**
`/private/tmp/claude-501/-Users-ramyalsaffar-Ramy-C-V--V-07--LLM-Projects-03--Clinical-Trial-Patient-Match-03--Code/2dfeb4e6-615f-4c55-9f61-bf818c2a0a8f/scratchpad/guard/sitecustomize.py`
(1218 bytes)

| specified element | state |
|---|---|
| hook-before-imports | **BUILT.** `sitecustomize.py` on `PYTHONPATH`, executed at interpreter startup, therefore before any project import. Patches `socket.socket.connect`, `socket.socket.connect_ex`, `socket.create_connection`, `socket.getaddrinfo` — each RECORDS the calling frame to `$ONC_NET_LOG` and then RAISES. |
| self-test (guard provably fires) | **BUILT AND PASSED.** A deliberate `socket.create_connection(('example.com',443))` was blocked and logged. Record: `…/scratchpad/guard/net.log` (148 bytes). This file contains ONLY the self-test, not a leak. |
| child propagation | **PARTIAL / UNVERIFIED.** `PYTHONPATH` is inherited by `subprocess` children, so a child Python process picks the hook up — but this was NOT independently verified for the subprocess-based suites. Treat as unproven. |
| covered-process listing | **NOT BUILT.** No enumeration exists of which processes a given run spawns or whether each was covered. |
| independent OS-level restriction | **NOT BUILT. STATED BOUNDARY:** containment is Python-level only. It intercepts `socket` calls made through CPython in a process that loaded the hook. It does NOT restrict a non-Python child, a statically-linked library, or any process that bypasses the `socket` module. macOS `sandbox-exec` with `(deny network*)` is the documented option this project has used before and was NOT applied here. |
| dummy-credential proof design | **NOT BUILT.** The real key remains resolvable (`sk-proj…`, 164 chars, via `05- Keys/.env` → `paths.load_env_keys()`). No run was executed with a deliberately invalid key to prove that a leak would fail authentication rather than bill. This is the strongest missing control. |

**Reading convention:** a per-run log file is created only when an attempt is
intercepted. **Absence of the log file == zero intercepted attempts.** All
guarded runs after the incident reported `egress=0` by this convention.

---

## 6. Incident — evidence and accounting

**What happened.** Item 3 moved Stage 5 to `deps.OPENAI_INFERENCE_CLIENT`. 35
test files stub `deps.OPENAI_CLIENT`; **zero** knew the new key. `_resolve`
therefore fell through to the real factory inside harnesses that believed they
had stubbed it, and live billed calls were issued while recorders counted zero.

**Root cause (mine).** The split was implemented BEFORE the stub-site sweep.
This is now a binding ruling (§1): client seams change only after the stub-site
sweep is green under containment.

**Confirmed evidence — one response, captured as terminal output:**

```
ChatCompletion(id='chatcmpl-ENLy4XScT6cqegRBkL5L685jEwtph',
               model='gpt-5.6-terra',
               choices=[... content='{"evaluations":[]}' ...],
               usage=CompletionUsage(prompt_tokens=199, completion_tokens=14,
                                     total_tokens=213))
```

Proof it was genuine, not a fixture: `OPENAI-REPLY` is the stub's literal
(`tests/test_agent_bedrock_anthropic_adapter.py:478`) and the stub was called
**0** times; no `chatcmpl-ENLy4X…` literal exists anywhere in the tree; a real
key resolves in this environment.

**Accounting:**

| quantity | value |
|---|---|
| confirmed calls | **1** (the response above), from `tests/test_agent_bedrock_anthropic_adapter.py` |
| confirmed cost | **NOT MEASURED.** No billing endpoint was consulted (no egress permitted). 213 tokens on `gpt-5.6-terra` is the only quantitative anchor. |
| unresolved remainder | **UNQUANTIFIED.** `test_agent_bedrock_anthropic_adapter` showed 9 failing checks and drives the OpenAI arm at 3 sites (:488, :546, :637); `test_matching_temperature_policy` showed 3 failing checks incl. an empty recorder at 4c-iii/4d-i. Each implies an attempted request, but no response ids were captured for them. Upper bound is small (single digits); exact count is **UNKNOWN**. |
| production artefacts | **UNCHANGED** (all three hashes, §2c). Nothing was written. |
| spend journal | **UNCHANGED**, 19 lines. The spend is real and **unrecorded** — the journal records rater/ragas spend, not Stage 5 test calls. |
| scope bound | CI bucket A ran 01:24, split landed 10:34–10:36 → bucket A NOT implicated. The three suites that passed post-split reference the chat path only in COMMENTS (`test_package_invariants:1124`, `test_fixture_call_mode_pin:762`, `test_resume_capture_and_ragas:94`) → not implicated. |

**Evidence file paths:**

- Terminal-output evidence (the ChatCompletion) exists ONLY in the session
  transcript: `/Users/ramyalsaffar/.claude/projects/-Users-ramyalsaffar-Ramy-C-V--V-07--LLM-Projects-03--Clinical-Trial-Patient-Match-03--Code/2dfeb4e6-615f-4c55-9f61-bf818c2a0a8f.jsonl`
- **A dedicated incident evidence file was NOT written: MISSING.**
- Containment self-test log: `…/scratchpad/guard/net.log`

**Fix in place.** `deps.get_openai_inference_client()` resolves: own override →
inherited `OPENAI_CLIENT` override → build. Both override reads under ONE
`_LOCK` acquisition. Inherited value is NOT cached. Diagnostic
`openai_inference_seam_state()` returns a closed 3-member vocabulary. Verified
in all three states; `is_resolved` stays False on the inherited route.

---

## 7. Corrected item-4 design

### 7a. Closed pre-send set — RULING-COMPLIANT FORM

The ruling requires **a dedicated exception or evidence dispatch had not
started**. An `isinstance` tuple over existing `provider_resilience` types is
NOT sufficient on its own, because those types are shared and a future raise
site inside/after dispatch would silently join the set.

**Required implementation:** `provider_resilience` raises a DEDICATED marker for
refusals it makes strictly before `send()` is invoked — either a dedicated
exception class (e.g. `PreSendRefusal`, which the existing pre-send raises
subclass or are wrapped in) or an attribute set at raise time (e.g.
`exc.dispatch_started is False`) that the rater classifies on. The rater must
classify on that marker, not on a type list.

Source-derived facts (for building the marker, not for use as the set):
pre-send raises in `execute` are `ValueError` (unrecognised kind, :1500-ish),
`ZeroTokenInferenceReservation`, `AttemptBudgetConfigurationError`, and — via
`reserve` → `require_known_quota` — `QuotaUnknown`, plus `ReservationExceedsQuota`
(`provider_resilience.py:693`). `WaitCancelled` is caught at :1563 and converted
by `_cancel_now`; it is documented "never escapes `execute()`" and must NOT be
in the set. `result = send()` is at `provider_resilience.py:1569`.

### 7b. Paced reconciliation

`batches.list` inside `reconcile_uncertain_submission`
(`oncotriage/evaluation/rater.py:4221`) **IS paced** under the management
bucket, per ruling. Its refusal is a pre-send refusal and must be handled
consistently with §7a — it must not be silently converted into
`RECONCILE_UNKNOWN` as if the provider had been asked and had not answered.

### 7c. Sites, scope, and handler

- Scope: `config.PROVIDER_QUOTA_SCOPE_OPENAI_BATCH` ("openai_batch"), bucket
  `openai:batch-management`. Measured: `provider_quota` = `(None,
  'not_applicable')`; `require_known_quota(scope, 0)` raises `QuotaUnknown`
  family=**requests**. Production therefore refuses on the unknown RPM — the
  ruling.
- Reservation: `reservation_tokens=0`, `reservation_kind=RESERVATION_MANAGEMENT`,
  `classify=classify_openai_failure`, `sdk_attempts=1`. Do NOT pass
  `on_possibly_billed` or `usage_tokens_of` (management calls are not
  model-billed and settle no usage).
- Sites to pace (`oncotriage/evaluation/rater.py`):
  `:4221` `batches.list` · `:4298` `files.create` · `:4301` `batches.create` ·
  `:4399` `batches.retrieve` (in `poll_batch`) · `:4434` `files.content` ·
  `:4486` `batches.retrieve` (in `collect_results`)
- Import: add `provider_resilience` at module scope beside
  `oncotriage/evaluation/rater.py:127`. No cycle — `provider_resilience`
  imports only `config`, `control`, `observability`.
- Helper home: after `require_client` ends at `:3587`. **Its comment block at
  :3570-3587 asserts these calls are NOT under the policy and calls that an
  operator decision — it MUST be rewritten in the same edit or it will
  contradict the code.**
- Handler: extend `except Exception as exc:` at `:4307`, which today re-raises
  only `spend.SpendLimitReached` (`:4312`), to also re-raise pre-send refusals
  per §7a. Otherwise a refusal that issued no request reaches
  `reconcile_uncertain_submission` at `:4319` and is reported as an uncertain
  create.
- Exit codes (`main()`): `RaterRefusal` → 1, `SpendLimitReached` → 3, argparse
  → 2. A propagated pacer refusal must surface as a CONFIGURATION refusal (1).

### 7d. Required new controls (both specified by the operator)

1. **Pre-send refusal at the create site yields ZERO create calls AND ZERO
   reconciliation calls, with the refusal preserved to the caller.** An error
   AFTER dispatch stays in the uncertain path.
2. **A focused `poll_batch` pacing test.** `poll_batch` is reached only by
   `main()` (`rater.py:7793`, `:7845`) — no test drives it — so its pacing must
   be asserted directly, never inferred from `collect_results` coverage.

### 7e. Known blast radius (measured BEFORE any edit)

Pacing will refuse inside these suites until each supplies explicit test
limits via `tests/_provider_pin.py`:

- `test_evaluation_rater.py` — `R.collect_results` ×6 (`:873,884,918,922,2012`),
  `R.submit_batches`; stub `_StubClient` (`:810`) answers retrieve+content only;
  `_SubmitStub` (`:3088`) answers the two creates only.
- `test_spend_coverage.py`, `test_rater_verify_before_retry.py`,
  `test_judge_independence.py` — all alias the module (`rater as R` / `as
  _rater`).

Use `_provider_pin.test_quotas_only(who)` / `release_test_quotas()`. It is
derived over BOTH whole tables and leaves a `QUOTA_NOT_APPLICABLE` row alone —
correct here, since `openai_batch`'s TPM must stay `not_applicable`.

### 7f. PRE-EXISTING DEFECT found, NOT introduced, NOT yet fixed

All four `install_test_quotas` callers import `restore_test_quotas` and **never
call it** (`test_agent_bedrock_adapter:281`, `test_matching_temperature_policy:96`,
`test_agent_bedrock_anthropic_adapter:283`,
`test_agent_bedrock_anthropic_per_trial:293` — `install=1, release_calls=0` for
all four). Driven: a second install in one interpreter raises
`ProviderPinError: … already in force`. Under `pytest tests/` (one process) this
aborts at import. **Do not replicate this pattern.** Item 4's suites must
install AND release, with the release ABOVE the summary line (releasing below it
yields a run reporting "0 failed" while exiting 1 — a defect this project has
shipped three times).

---

## 8. Active background commands / processes

**NONE RUNNING.** Both background tasks completed:

| id | command | status |
|---|---|---|
| `b64drkjha` | serial bucket B (`tests/run_serial_tests.py`) | completed, exit 0, 5/5 in 456.9s |
| `blttiqv9l` | CI bucket A (`ci_test_buckets.py --run A`) | completed, exit 0, 121 ran / 0 failed / 0 not run |

Output files (absolute):
`/private/tmp/claude-501/-Users-ramyalsaffar-Ramy-C-V--V-07--LLM-Projects-03--Clinical-Trial-Patient-Match-03--Code/ddabd1d7-cdfa-4512-b1ee-55be994ef640/tasks/b64drkjha.output`
`/private/tmp/claude-501/-Users-ramyalsaffar-Ramy-C-V--V-07--LLM-Projects-03--Clinical-Trial-Patient-Match-03--Code/ddabd1d7-cdfa-4512-b1ee-55be994ef640/tasks/blttiqv9l.output`

No worktrees left behind (`/tmp/wt_head` was created and removed).

---

## 9. Next concrete action — FORCED ORDER

Do these in this order. Do not reorder; each step's ruling depends on the
previous one being green.

1. **Write a durable baseline snapshot file** to an absolute path and record
   that path. Include the three production artefacts AND per-file sha256 of the
   twelve characterization fixtures (currently MISSING, §2c).
2. **Harden containment to the specification (§5)** before any
   provider-touching test: child-propagation verification, covered-process
   listing, an independent OS-level restriction (macOS `sandbox-exec` with
   `(deny network*)`) or an explicit written boundary, and a dummy-credential
   proof run. Re-run the guard self-test and confirm it fires.
3. **Run the stub-site sweep for the rater** under containment and make it
   green. (Binding ruling: seams change only after the sweep is green. Item 4
   does not change a seam, but the same discipline is required before pacing
   production call sites that four suites drive.)
4. **Implement item 4** per §7: dedicated pre-send marker in
   `provider_resilience`, `_paced_management` in `rater.py`, all six sites
   including `batches.list`, handler at `:4307` extended, `require_client`
   comment block at `:3570-3587` rewritten.
5. **Add the two required controls** (§7d): zero-creates/zero-reconciliation
   with refusal preserved; focused `poll_batch` pacing test.
6. **Add explicit test limits** to the four affected suites, install AND
   release, release above the summary (§7e, §7f).
7. **Re-run the item-4 attribution baseline** (§4) under containment and
   attribute every delta.
8. **Item 5:** copytree revert matrix over scope-lock wiring, the four
   `isolate_locks` sites, and rater reconcile production code; then the
   ragas-venv stubbed-provider integration run (live effectiveness remains
   separately unverified and must be stated as such).
9. **Completion boundary:** per-item PASS/FAIL with evidence; then bucket A and
   bucket B once each against the finished tree; digests vs HEAD; production
   hashes vs the snapshot written in step 1.

---

## Addendum — corrections to earlier claims in this workstream

- `CHARS_PER_TOKEN` is **3.6** (module-local), not `config.CHARS_PER_TOKEN = 4`.
  An earlier report claimed the reservation was "~12% low"; that was wrong in
  the divisor AND in the direction, and compared against a ratio measured on a
  different provider's tokenizer.
- An earlier coverage sweep used `git grep`, which does not see untracked
  files, and wrongly reported the rater reconcile safeguards as untested. They
  are covered: `tests/test_rater_verify_before_retry.py:217` pins
  `RECONCILE_OUTCOMES` exhaustively at 3; `:331-347` drives the
  unreadable-evidence raise (`RaterRefusal`, code `reconcile_unreadable`,
  `creates == 1`). Use `grep -r` for any repeat of this sweep.
- `tests/test_package_invariants.py:5529` said "eight keys" while comparing
  against a nine-element list. That prose was stale BEFORE this session; it now
  reads ten.
