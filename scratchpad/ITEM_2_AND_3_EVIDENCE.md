# ITEM 2 AND ITEM 3 — EVIDENCE, continuation 5

Companion to `CONTAINMENT_AND_INCIDENT_EVIDENCE.md`. Section 1 of that file's
reading convention applies here unchanged: **an empty interception log means
ZERO RECORDED ATTEMPTS, never proven zero egress.**

---

## ITEM 2 — THE RAGAS STUBBED SCORING RUN

**PASS.** Both the judge and the embedding paths were exercised through the
SHIPPED builders on the venv interpreter, with provider responses stubbed at
the client seam, and the paced `execute_async` twins were the path taken for
both.

### How it was run

`09- Testing/ragas-venv/bin/python` against a TEMPORARY harness held in the
session scratchpad (`ragas_stubbed_scoring.py`) — outside the repository, not
registered in any CI bucket, and not part of the suite. `openai.AsyncOpenAI` is
replaced by a stub class BEFORE either builder runs, so `real_create` inside
each shipped wrapper is a stub method and no socket is opened.

The stub patch point is `openai.AsyncOpenAI` and **not** a module attribute of
the harness: `build_judge` and `build_embeddings` each do
`from openai import AsyncOpenAI` INSIDE the function body, so a name rebound on
`ragas_harness` would reach nothing.

### What was measured

| | |
|---|---|
| judge requests | **5** |
| embedding requests | **2** |
| strings embedded | **4** |
| scores produced | **2 scored / 0 unscored** |
| `PROVIDER_PACING_WAITS` | `{"ragas_embedding:acquired": 2, "ragas_judge:acquired": 5}` |
| `PROVIDER_RETRY_OUTCOMES` | `{}` |

| metric | status | value | judge calls | embed calls |
|---|---|---|---|---|
| `faithfulness` | **scored** | 0.0 | 2 | 0 |
| `response_relevancy` | **scored** | 0.8597133774015853 | 3 | **2** |

**THE PACED TWIN IS THE PATH TAKEN, AND THAT IS THE CLAIM THIS RUN EXISTS TO
SETTLE.** `PROVIDER_PACING_WAITS` carries an `acquired` count for BOTH scopes.
Those counters are incremented only inside `provider_resilience.execute_async`,
so a run that had bypassed the wrapper — or reached the client by any other
route — would leave them empty while still producing scores.

**FAITHFULNESS NEVER TOUCHES THE EMBEDDER AND THAT IS CORRECT, NOT A GAP.**
`build_metrics` hands the embedder to `AnswerRelevancy` alone, so
`METRIC_RESPONSE_RELEVANCY` is the only metric that can exercise that path. An
earlier probe that selected faithfulness alone reported 1 judge call and 0
embedding calls, which is why the shipped harness selects both.

### The refusal

With the judge's RPM set to `None` (UNKNOWN) AFTER the builders had run:

```
faithfulness:         status=unscored
reason: the requests quota for scope 'ragas_judge' is UNKNOWN, so nothing may
        be dispatched under it. ... Nothing has been sent.
judge calls reached:  +0
embed calls reached:  +0
```

The pacer refused and **no call reached the stub**, which is what makes it a
refusal rather than a failed call. It is set after the build deliberately:
`_refuse_unpaced_scope` runs inside `build_judge`, so an UNKNOWN quota present
at build time refuses to construct the judge at all — a different guard, at a
different site, with a different remedy.

### What the VALUES are worth — stated rather than glossed

**THE TWO SCORES ARE ARTEFACTS OF THE STUB AND ARE NOT MEASUREMENTS OF RAGAS
QUALITY.** The synthesiser returns `0` for every integer field, so every
faithfulness verdict is "unfaithful" and 0.0 follows by construction; the
0.8597 is the cosine between hash-derived vectors this harness invents. What is
real is the call path, the pacing, the refusal, and that a `Score` of status
`scored` came out of the shipped `_score_one`.

### Two facts about the installed stack, measured rather than assumed

1. **instructor 1.15.4 runs this judge in JSON *mode*, not `json_schema`
   mode.** `response_format` on the wire is exactly `{"type": "json_object"}`
   and names no model. The target model's schema is carried in the PROMPT and
   the reply is validated client-side — which is why the first attempt's
   Pydantic errors named `StatementGen` and `AnswerRelevanceOutput` while the
   request mentioned neither. The harness therefore extracts the schema from
   the messages, generically, so it holds no copy of ragas's internal models.
2. `ragas 0.4.3`, `instructor 1.15.4`, `openai 1.99.9`;
   `OpenAIEmbeddings.aembed_texts` awaits `client.embeddings.create` and reads
   `response.data[i].embedding` — the seam `build_embeddings` reassigns.

### Nothing was installed and nothing was left patched

| | |
|---|---|
| installed distributions | **182 before, 182 after, digest `54481b1546ddd790` both** — UNCHANGED |
| `openai.AsyncOpenAI` | restored, asserted **by identity** |
| `config.PROVIDER_REQUESTS_PER_MINUTE` | restored, by identity |
| `config.PROVIDER_TOKENS_PER_MINUTE` | restored, by identity |

**`--dry-run` REMAINS THE LABELLED FREE PATH.** Nothing in this pass relabels
it, and this stubbed run is not offered as a substitute for it: it issues no
provider request at all, where `--dry-run` prices what a real run would cost.

---

## ITEM 3 — FOCUSED CLEANUP

**PASS**, all four parts.

| part | evidence |
|---|---|
| `_make_copy` guarded, cleanup in `finally` | `tests/test_provider_revert_matrix.py` — failures are returned as problem STRINGS rather than aborting the matrix; **8 passed / 0 failed, 5/5 plants caught** |
| the matrix's serial classification settled explicitly | the collision-matrix exclusion argument is written AT `tests/test_provider_revert_matrix.py`, per the precedent that the argument lives at the file |
| `_ENTRY_POINTS` extended to all four lock-taking entry points | `tests/test_provider_scope_lock.py` — `_EXITSTACK_ENTRY_POINTS` now has a CONSUMER loop, not just a table. **94 passed / 0 failed** |
| the two external test attempts removed | quotas lookup **29/0, egress 0**; MLflow index **114/0, egress 0** |

### The fourth entry point pair needed its own table, and why

`rater_run.py` and `ragas_run.py` take the allowance through
`contextlib.ExitStack.enter_context` and hold it for the whole process — they
have **no run lock** to nest inside, unlike the two campaign drivers' nested
`with`. A single table would have forced one shape onto both. The consumer loop
asserts, per file: the allowance is entered via `enter_context`; the scope is
**never a string literal** (so a re-ruled table moves the lock with it); and
BOTH `AlreadyPacing` and `ScopeLockUnavailable` clauses are present.

It carries its own controls: an `ast`-copy strip that reports none, a
non-degeneracy check that the unmutated scan found calls in BOTH files, and a
check that `ragas_run.py` holds MORE THAN ONE allowance — the judge's scope and
the embedder's are different provider limits, which is the shape that makes a
single-lock check wrong for that file.

### The two external attempts

- **`tests/test_provider_quotas_lookup.py`** — `AWS_EC2_METADATA_DISABLED=true`
  via `setdefault`, chosen over a stand-in session because it PRESERVES what
  section 5 exercises (the real credential chain) rather than replacing it.
  Measured both ways. The false comment "It opens no socket" was corrected in
  the same edit.
- **`tests/test_tracking_mlflow_index.py`** — `MLFLOW_DISABLE_TELEMETRY=true`
  above the bootstrap.
