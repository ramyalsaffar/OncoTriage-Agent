# CONTAINMENT AND INCIDENT EVIDENCE — continuation 5

**This is the dedicated evidence file the continuation-4 handoff recorded as
MISSING (§6: "A dedicated incident evidence file was NOT written: MISSING").**

Written 2026-09-12, session `90d88517-cb01-4501-a68a-72f1ed15dda4`.
Nothing here is reconstructed. Anything not measured in THIS session is marked
as such and attributed to its source.

---

## 1. READING CONVENTION — the limit, stated before any number

A per-run interception log is created only when an attempt is intercepted.

> **An absent or empty log means ZERO RECORDED ATTEMPTS — that is, the Python
> tripwire intercepted nothing. It is NOT proof of zero egress.** It says
> nothing about a path the hook cannot see: a non-Python child, a
> statically-linked library, a C extension holding its own socket, or any code
> that bypasses CPython's `socket` module.

Every "egress=0" in this session's records means *zero recorded attempts* and
must be read that way. Total egress was **not** independently measured, and no
claim in this file should be read as measuring it.

## 2. THE CONTAINMENT AS BUILT

Location (absolute):
`/private/tmp/claude-501/-Users-ramyalsaffar-Ramy-C-V--V-07--LLM-Projects-03--Clinical-Trial-Patient-Match-03--Code/90d88517-cb01-4501-a68a-72f1ed15dda4/scratchpad/guard/`

| element | state | how it was established |
|---|---|---|
| hook before imports | BUILT | `sitecustomize.py` on `PYTHONPATH`, executed at interpreter startup |
| six intercept points | BUILT | `socket.connect`, `connect_ex`, `create_connection`, `getaddrinfo`, **`sendto`**, **`sendmsg`**. The last two are new this session: a datagram send needs no prior `connect`, so UDP bypassed all four original patches |
| self-test fires | **MEASURED** | all five drivable points blocked in-process, one run |
| child propagation | **MEASURED** | a child (`child.py`) and a **grandchild** (`grandchild.py`) were both blocked and both registered. The handoff recorded this as "PARTIAL / UNVERIFIED" |
| covered-process listing | **BUILT + MEASURED** | every interpreter loading the hook appends pid/ppid/executable/argv to `ONC_COVERED_LOG`. 5 processes in the propagation test; **127** across the interim bucket-A run |
| spawn reconciliation | **BUILT + MEASURED** | `subprocess.Popen.__init__` wrapped to RECORD (never block) every child. Spawned-but-uncovered: **NONE** |
| OS-level restriction | **BUILT + MEASURED** | `no_network.sb` + `/usr/bin/sandbox-exec`. Control proves the host CAN reach the network (DNS resolved); under the profile `getaddrinfo` fails `gaierror` and `connect` fails `PermissionError [Errno 1]`; filesystem work still succeeds. The handoff recorded this as "NOT BUILT / stated boundary" |
| dummy-credential proof | **BUILT + MEASURED** | see §3 |

**Known side effect.** The guard shadows
`/opt/anaconda3/lib/python3.13/sitecustomize.py`, which exists and adds conda
site directories (a conda packaging workaround). Nothing in this session
depended on it. First probe of this was VACUOUS — `import sitecustomize`
returns the already-loaded module however `sys.path` is filtered — and was
redone with `importlib.util.find_spec` in a clean environment.

## 3. THE DUMMY-CREDENTIAL PROOF — in the CORRECTED form

The requirement: the attempted call must **reach the network blocker and be
blocked there**, not merely fail authentication earlier.

Measured, one run, with the tripwire armed:

| fact | value |
|---|---|
| real key resolvable | yes, 164 chars, sha256 `6f4dec25aab0…` (value never printed) |
| dummy key | 152 chars, sha256 `714f349ab487…` |
| dummy is not the real key | **True** |
| `client.api_key` is the dummy | **True** — asserted, not assumed |
| result of `models.retrieve` | raised `APIConnectionError` ← `RuntimeError` (the tripwire) |
| interception log | grew **0 → 989 bytes** |
| recorded stack contains | `httpx`: **True**, `socket`: **True** |

**What this establishes.** The SDK accepted the key locally, built the request
and entered its transport; the call was stopped at the socket layer by the
tripwire. It did **not** fail authentication, and it did not fail on the key's
shape. The `openai/` package frames are absent from the captured tail only
because the recorder keeps the last six frames, which at that depth are
httpx/socket — httpx **is** the SDK's transport.

## 4. THE INCIDENT — accounting, unchanged where it was already honest

Inherited from the continuation-4 handoff §6. **Nothing in this session
re-measured the incident**; the rows below are carried forward with their
original provenance.

| quantity | value |
|---|---|
| confirmed calls | **1** — the captured `ChatCompletion(id='chatcmpl-ENLy4X…', model='gpt-5.6-terra', usage=199+14=213 tokens)` |
| confirmed cost | **NOT MEASURED.** No billing endpoint was consulted. 213 tokens is the only quantitative anchor |
| unresolved remainder | **UNQUANTIFIED.** Failing checks in two suites each imply an attempted request, but no response ids were captured for them. **No upper bound is claimed.** An earlier phrasing calling this "single digits" is unsupported and is withdrawn |
| production artefacts | UNCHANGED |
| spend journal | UNCHANGED, 19 lines. The spend is real and **unrecorded** — that journal records rater/ragas spend, not Stage 5 test calls |

**Transcript evidence, preserved at its recorded path:**
`/Users/ramyalsaffar/.claude/projects/-Users-ramyalsaffar-Ramy-C-V--V-07--LLM-Projects-03--Clinical-Trial-Patient-Match-03--Code/2dfeb4e6-615f-4c55-9f61-bf818c2a0a8f.jsonl`

## 5. WHAT THIS SESSION'S RUNS RECORDED

Every suite run this session was run under the tripwire.

| run | result | intercepted |
|---|---|---|
| final CI bucket A (124 files) | ran 124, failed 0, not run 0, exit 0 | **41** |
| interim CI bucket A (123 files) | ran 123, failed 0, not run 0, exit 0 | 41 |
| every individual suite run | see the session report | 0 recorded |

**The 41, broken down — and the distinction is the whole point:**

| count | destination | reading |
|---|---|---|
| 39 | `('127.0.0.1', 1)` | **LOOPBACK.** This is `tests/_control_harness.CLOSED_PORT_URL`, a deliberate closed-port probe eight bucket-A files use. It never leaves the machine and is not egress |
| 1 | `('169.254.169.254', 80)` | **EXTERNAL.** The EC2 instance metadata service, via botocore's credential chain, from `test_provider_quotas_lookup.py`. Blocked |
| 1 | `('config.mlflow-telemetry.io', 443)` | **EXTERNAL.** MLflow telemetry, from `test_tracking_mlflow_index.py`. Blocked |

So bucket A attempts **two** genuinely outbound connections, both to
third parties neither test is about, and both were blocked here. Run without
containment they would proceed. Neither is caused by this session's changes;
both are pre-existing and are reported as findings.

## 5b. THE SPAWN RECONCILIATION FOUND UNCOVERED CHILDREN — INCLUDING MY OWN

The covered/spawn reconciliation over the FINAL bucket-A run:

| quantity | value |
|---|---|
| processes that loaded the hook | **300** |
| recorded spawns | **815** |
| covered processes that are themselves parents of covered processes | **31** (grandchild coverage, measured) |
| spawned python children that did **NOT** load the hook | **22** |

**The 22 are not a mystery and eight of them are mine.** Every one is a child
launched with a REPLACED `PYTHONPATH`:

* **8 × `_guard_*.py`** — `tests/test_provider_revert_matrix.py`'s own guard
  scripts. `_run_guard` sets `env["PYTHONPATH"] = root` so the copy wins, which
  also DROPS the guard directory; and the copy writes its own
  `sitecustomize.py` (the MetaPathFinder stripper), which would shadow the
  tripwire's even if the directory were still on the path. **So that file's
  "egress=0" covered its parent only.** This is a defect in code written this
  session, found by the reconciliation rather than by reading.
* **10 × `c*_probe.py` / `clean_probe.py`** — `tests/test_monitoring_drift_redesign.py`'s
  control probes, same mechanism. Pre-existing.
* **`25- Batch Runner.py`, `26- Ablation Study.py`, `queries.py`, `study.py`** —
  entry-point children spawned with their own environments. Pre-existing.

**CLOSED AT THE OS LAYER, WHICH IS WHAT THE OS LAYER IS FOR.** `sandbox-exec`
is enforced by the kernel for a process AND every descendant, whatever
`PYTHONPATH` a child is handed. Both new test files were re-run under it:

```
sandbox-exec -f no_network.sb python tests/test_provider_revert_matrix.py
    -> exit 0, 8 passed / 0 failed, 5/5 plants caught
sandbox-exec -f no_network.sb python tests/test_rater_management_pacing.py
    -> exit 0, 37 passed / 0 failed          [SEE NOTE BELOW]
```

**NOTE added 2026-09-12 (repair session) — the 37 above is NOT drift, and the
record is annotated rather than corrected.** Re-measured under containment on
this date: `tests/test_rater_management_pacing.py` reports **41 passed, 0
failed, egress=0**. The delta is **four checks ADDED after this figure was
taken**, not a count that moved under a fixed file. The file is UNTRACKED, so
there is no git history to diff it against; what supports the reading is that
its `check(` call sites number 41, it reports 41 passed with 0 failed, and the
figure above was recorded while item 4 — whose own test file this is — was
still being built. A count that had DRIFTED would show as failures or as a
`check(` total that no longer matches the reported passes; neither is present.

The original figure is left exactly as written: it was true of the tree at the
moment it was taken, under the sandbox run it documents.

And the sandbox was shown to be in force for a CHILD rather than only the
parent: a subprocess launched inside it resolving `example.com` fails
`gaierror`. So the eight previously-uncovered children are now covered by
measurement rather than by construction.

### 5b-i. SUPERSEDED AT THE COMPLETION BOUNDARY — and my first method was wrong

**THE FIRST RECONCILIATION I RAN THIS SESSION WAS UNSOUND AND ITS NUMBER MUST
NOT BE QUOTED.** It matched a spawn record against a covered record by ARGV
STRING. For `python -c <code>` those two strings can never be equal: the spawn
side records the full code text as an argument, while the child's own
`sys.argv` is `['-c', …]` with the code ABSENT. Measured on the boundary run:
**37 covered rows whose own `argv[0]` is `-c`**, every one of which the string
method counted as uncovered for free. It reported **93**; the number is an
artefact of the method, not a property of the run.

The sound method ignores argv and uses the **ppid** the covered log already
records: uncovered children of parent P = (python spawns recorded by P) −
(covered rows whose ppid is P).

| boundary run | bucket A | serial B |
|---|---|---|
| intercepted attempts | **39, ALL `('127.0.0.1', 1)`** — loopback, **0 external** | **0 recorded** |
| covered processes | 323 | 34 |
| recorded spawns | 838 (367 python, 471 non-python) | 162 (150 python) |
| spawned-but-uncovered (ppid) | **63** | **117** |
| files written THIS SESSION with a shortfall | **NONE** | NONE |

**`63` AND `117` ARE UPPER BOUNDS, NOT EXACT COUNTS.** A python child reached
through an intermediate process records the INTERMEDIATE's ppid, so coverage
that really happened is attributed to a different parent and reads as a
shortfall here. The method cannot distinguish the two, and no claim above
should be read as saying it can.

All of the shortfall is attributable to **pre-existing** subprocess harnesses
that hand children their own environment — bucket A: `test_runner_preflight_
and_state_faults` (14), `test_ablation_stop_and_lock` (13),
`test_monitoring_drift_redesign` (10), `test_runner_stop_switch` (8),
`test_agent_stage5_per_trial_calls` (7), `test_runner_sigterm_shutdown` (6),
`test_agent_bedrock_anthropic_adapter` (4), `test_secret_scan_gate` (1);
serial B: `test_package_invariants` (117). **Every one of them was inside the
kernel sandbox for the whole run**, which is the layer that does not depend on
what `PYTHONPATH` a child is handed.

**THE 39 ARE ATTRIBUTED RATHER THAN ASSUMED**, by pid:
`test_runner_crash_record_and_db_unification.py` (26), two `-c` children (5+5),
`test_agent_retrieval_observability.py` (2),
`test_storage_inference_logging_contract.py` (1). All loopback. The two
genuinely EXTERNAL attempts §5 recorded — the EC2 metadata service and MLflow
telemetry — are **GONE**, which is item 3's two fixes measured at the boundary.

## 6. WHAT IS STILL NOT ESTABLISHED

1. **Total egress is not measured.** Only what the Python hook intercepted,
   plus what the OS layer denied in the two runs named in §5b.
2. ~~**The sandbox was NOT applied to the two full suite runs.**~~ **CLOSED at
   the completion boundary.** Both full runs were executed under
   `sandbox-exec -f no_network.sb` with the tripwire on `PYTHONPATH`: bucket A
   (124 files, ran 124 / failed 0 / not run 0, exit 0) and serial B (5 members,
   5/5, exit 0, 401.8s). So the no-egress claim is now measured for EVERY
   process of both runs rather than for their parents.
   **The OS layer's effect is discriminated rather than assumed**, by a control
   that needs no egress: every probe targets a CLOSED LOOPBACK PORT, so
   unsandboxed it returns `ECONNREFUSED(61)` — proving the socket layer works
   and the kernel's TCP stack was reached — and under the profile it returns
   `EPERM(1)`. Measured at three depths, parent / child / **grandchild**, with
   no tripwire on `PYTHONPATH` in either arm so the OS layer is isolated from
   the Python one. A "blocked" reading without that control would be equally
   consistent with a host that simply has no network.
3. **The incident remainder stays UNQUANTIFIED** (§4).
4. **The historical session-start hashes remain MISSING** and are not
   reconstructable; this session's baseline is
   `scratchpad/BASELINE_SNAPSHOT_CONTINUATION_5.md`.
5. ~~**`test_provider_revert_matrix.py` disarms containment for its
   children**~~ **FIXED, and the fix is MEASURED rather than argued.** The
   durable repair this entry called for was made: the copy's own
   `sitecustomize.py` is gone entirely (the editable-finder strip moved into
   `_PREFLIGHT`), and `_run_guard` now **APPENDS** to `PYTHONPATH` instead of
   replacing it, so a child CHAINS the tripwire rather than shadowing it.
   Reconciled by ppid over the boundary bucket-A run:
   `test_provider_revert_matrix.py` recorded **8 python spawns and 8 covered
   children — no shortfall**, where §5b had eight uncovered. Across all eight
   files written this session the coverage shortfall is **NONE**.
