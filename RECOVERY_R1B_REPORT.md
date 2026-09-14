# RECOVERY R1b -- verify the shipped arm's billing terms, recompute the Stage 5 bound

Status: COMPLETE for R1b. Stopped here; the enforcement decision and the combined checks were not begun.

Written for: the operator and the session that takes the enforcement decision.

## 1. Starting state

- Branch `wip/billing-closure`, HEAD `48c0d88d3d01826e65c5b90e1e71b8b68d93595c`
  ("WIP: add Stage 5 reservation bounds and reject unproven legacy liabilities.
  NOT FOR PUSH").
- Dirty files at start: none (`git status --short` empty).
- Baselines at start, under containment:
  `test_billing_closure.py` 383/0/0, `test_campaign_billing_record.py` 128/0,
  `test_package_invariants.py` 261/0/0, `test_storage_run_metrics_flush.py`
  130/0. All four match the brief. No tripwire log was created (zero events).
- Digests at start: `renderer_digest` `5ea2c6cc...2c956`, `PROMPT_VERSION`
  1.11.0, `FINGERPRINT_VERSION` 8, rendered system prompt sha256 `8baaa8b8...`
  (MeSH applied) and `db3c2b4d...` (not applied).
- Production inventory at start: sha256 of all 40 files under
  `02- Data/03- Inferences Storage` and `08- Checkpoint`.

## 2. Containment

- OS sandbox (`sandbox-exec`): outbound network denied except loopback, the
  system DNS socket (`mDNSResponder`) denied, writes and reads denied under the
  four production directories and a decoy.
- `sitecustomize` audit-hook tripwire: refuses and records outbound connects,
  DNS lookups, sqlite connects to protected paths, and writes to protected
  paths.
- Controls fired first:
  - Arm A (sandbox + tripwire): external connect, raw socket, DNS, protected
    sqlite read, protected write and protected listing refused; loopback
    allowed; production listing and checkpoint read refused.
  - Arm C (sandbox only, tripwire off, `env -i`): the same probes refused by the
    sandbox alone.
- No arm removed both protections. Every test ran with both layers and an
  isolated project root.
- The only network egress this session: documentation fetches, listed in
  section 3, made outside the sandbox with no credentials and no inference
  endpoint.

## 3. Evidence (retrieved 2026-09-14)

| # | source | how retrieved | retrieval | sha256 of saved copy |
|---|---|---|---|---|
| E1 | AWS model card, `docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html` | WebFetch | 2026-09-14 | (rendered text; not byte-saved) |
| E2 | AWS Bedrock pricing page, `aws.amazon.com/bedrock/pricing/` | curl, raw HTML | 2026-09-14 | `d33a2695...0ae7` (895,209 bytes) |
| E3 | The pricing page's own data endpoint (the page's `data-pricing-endpoint` attribute, 70 occurrences): `b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/bedrockfoundationmodels/USD/current/bedrockfoundationmodels.json` | curl | 2026-09-14; manifest `hawkFilePublicationDate` 2026-09-11T12:44:10Z | `c5769809...c6e4` (gzip) |

E3 is not a separate document. The page renders its price cells as
`{priceOf!bedrockfoundationmodels/bedrockfoundationmodels!<key>}` placeholders
and fills them from this endpoint, so the numbers a reader sees on the page are
these. Each Sonnet 4.6 cell key on the page was mapped to its entry.

### Quoted facts

- E1: "Context window: 1M tokens"; "Max output tokens: 64K"; Standard tier
  supported, Priority and Flex not supported; prompt caching TTL "5 minutes, 1
  hour", min 1,024 tokens, 4 checkpoints. Pricing section: "This model is a
  third-party model offered and billed through AWS Marketplace ... For pricing,
  see the Amazon Bedrock Pricing page." No pricing figure, no long-context
  statement, no statement about profile pricing.
- E2: the Anthropic tables are headed "Global Cross-region Inference" and "Geo
  and In-region Cross-region Inference". Columns: "Price per 1M input tokens",
  "Price per 1M output tokens", "(batch)" input and output, "(5m cache write)",
  "(1h cache write)", "(cache read)". The row "Claude Sonnet 4.6" appears in
  both on-demand tables (and in two Reserved tables).
- E2, whole page: zero matches for "long context", "long-context", "200K",
  "200,000", "greater than", "context window", "tiered". No Claude row on the
  page has a long-context column or a separate long-context row, including
  Sonnet 4 and Sonnet 4.5.
- E3, Claude Sonnet 4.6, per 1M tokens:

  | table | input | output | batch in | batch out | 5m write | 1h write | read | regions with identical values |
  |---|---|---|---|---|---|---|---|---|
  | Global Cross-region | 3.00 | 15.00 | 1.50 | 7.50 | 3.75 | 6.00 | 0.30 | all 33 |
  | Geo and In-region | 3.30 | 16.50 | 1.65 | 8.25 | 4.125 | 6.60 | 0.33 | all 19, incl. US East (N. Virginia) and EU (London) |

## 4. Answers to P1-P4

- **P1, long-context premium: NOT SETTLED.** Neither E1 nor E2/E3 states a
  premium, a tier, or the absence of one. The pricing page shows one on-demand
  price per class for Sonnet 4.6, but it shows no long-context column for any
  Claude model, so absence there is not evidence the page would show one.
  Missing documentation does not prove the premium is zero.
- **P2, `us.` profile multiplier: SETTLED as published rates, not as a stated
  multiplier.** The Geo/In-region row is $3.30/$16.50 against Global
  $3.00/$15.00: 1.1x on every class. AWS publishes it as a separate row; no page
  states a multiplier.
- **P3, rates: SETTLED for both profiles** (table above). The cache multipliers
  follow from the published rates: read 0.10x, 5m write 1.25x, 1h write 2.00x
  of that row's input.
- **P4, double counting: NONE.** `PRICING_CONFIG["us.anthropic.claude-sonnet-4-6"]`
  is exactly the published Geo row, and the `global.` row is exactly the Global
  row. `config.stage5_attempt_bound` prices from the row directly and applies
  only the long-context multipliers; no geo factor is applied anywhere in
  `spend.py` or `utils.py` (grep). Applying 1.1x again would double count.

## 5. The bound: old versus new

Nothing that evidence establishes differs from what R1 shipped. The rates R1
used (`PRICING_CONFIG`) are exactly the published ones (P3, P4), and the one term
R1 assumed (P1) is still unsettled. So **no rate, multiplier, limit, request,
model, ceiling, truncation rule or prompt changed**, and
`STAGE5_RESERVATION_BASIS_VERSION` stays 1.

What changed is the **status** of each term:

| term | R1 status | R1b status |
|---|---|---|
| context window 1,000,000 / max output 64,000 | AWS model card | AWS model card, re-read 2026-09-14 (E1) |
| Global rates 3.00 / 15.00 / 0.30 / 3.75 / 6.00 | "MEASURED" (Marketplace, 2026-08-30) | **verified**, pricing page data (E2/E3) |
| Geo rates 3.30 / 16.50 / 0.33 / 4.125 / 6.60 | "INFERRED" (+10%) | **verified**, pricing page data (E2/E3) |
| geo multiplier on top of a geo row | not applied | not applied; applying it would double count (P4) |
| long-context 2.0x in / 1.5x out | ASSUMED | **still ASSUMED; not proven** (P1) |
| SDK wire attempts per send | 1 (config) | 1 (config, unchanged) |

Per-attempt bounds, computed by the owner (`config.stage5_attempt_bound`) under
containment, and independently re-derived in `tests/test_billing_closure.py`:

| wire model | request | R1 bound | R1b bound | status |
|---|---|---|---|---|
| `us.anthropic.claude-sonnet-4-6` (shipped) | trial, 32,000 ceiling | $9.042 | **$9.042** | rates verified; conditional on assumed multipliers |
| `us.anthropic.claude-sonnet-4-6` (shipped) | warmup, 1-token ceiling | $8.25002475 | **$8.25002475** | same |
| `global.anthropic.claude-sonnet-4-6` | trial | $8.22 | $8.22 | same |
| `global.anthropic.claude-sonnet-4-6` | warmup | $7.5000225 | $7.5000225 | same |

Arithmetic (shipped trial): input 1,000,000 x max(3.30, 0.33, 4.125 [5m TTL]) x
2.0 = $8.25; output 32,000 x 16.50 x 1.5 = $0.792; total $9.042.

**THIS IS NOT A PROVEN CEILING FOR THE SHIPPED ARM.** It is an upper bound
conditional on (a) the provider enforcing its documented window and output
ceiling, (b) billing the named model, and (c) no long-context premium above
2.0x / 1.5x. (c) is unsupported by any AWS document. The alternatives below are
**scenarios, not bounds**; none is adopted:

| scenario (shipped `us.` trial, 32,000 ceiling) | per attempt |
|---|---|
| no long-context premium (1.0 / 1.0) | $4.653 |
| assumed 2.0 / 1.5 (shipped) | $9.042 |
| assumed 2.0 / 1.5 with a 1h cache TTL | $13.992 |

The missing evidence that would settle (c): an AWS statement of Sonnet 4.6
long-context pricing on Bedrock, or a console bill line for a Sonnet 4.6 request
above 200K input tokens (A6). A Price List offer file for
`AmazonBedrockFoundationModels` would list every SKU with its usage-type
description and could show whether a long-context SKU exists. That source was
outside this session's network exception and was not fetched.

## 6. Headroom (shipped configuration, owner-computed)

`us.anthropic.claude-sonnet-4-6`, per-trial, `MAX_WORKERS` 12 x
`per_trial_parallel_bound()` 2 = 24 attempts in flight, SDK attempts 1.

| quantity | R1b (assumed 2.0 / 1.5) | scenario 1.0 / 1.0 |
|---|---|---|
| one trial attempt | $9.042 | $4.653 |
| one warmup | $8.25002475 | $4.1250165 |
| unresolved liability, 24 trial attempts in flight | **$217.01** (72.3% of $300) | $111.67 |
| possibly-billed failures that fit under $300 | **33** (the 34th crosses; $1.61 left) | 64 |
| possibly-billed failures that fit under the $25 serving window | **2** (the 3rd crosses; $6.92 left) | 5 |

`spend.py`'s gate checks accumulated MEASURED cost immediately before a request,
so today's documented overshoot bound is "the requests in flight": up to $217.01
of reservations can exist beyond the point the gate last passed.

## 7. SPEND_CAP_USD re-derivation (cap unchanged at $300)

### 7.1 What `config.py` derives today, and why it is stale

The docstring prices the campaign at gpt-5.6-terra rates ($2.00 / $12.00) with
fixture token shapes (S = 8,575 system tokens, u = 372, o = 696): $0.179 per
patient with caching, $0.411 without, and a program of $98.66 / $225.96 (campaign
budget, judge excluded). The shipped arm is Claude Sonnet 4.6 on the `us.`
profile ($3.30 / $16.50), per-trial, and its measured per-call output is far
above 696 tokens. That derivation does not describe the shipped workload.

### 7.2 The approved workload and each component's model (verified in config)

| component | size | model | budget |
|---|---|---|---|
| campaign | `CAMPAIGN_COHORT_SIZE` 500 | Stage 5 `us.anthropic.claude-sonnet-4-6`, per-trial, warmup + up to `MAX_TRIALS_FOR_EVALUATION` 15 trial calls | `SPEND_CAP_USD` |
| k=2 stability re-run | `CAMPAIGN_STABILITY_SAMPLE_SIZE` 50 | same | `SPEND_CAP_USD` |
| Stage 2 query embedding | 1 per patient-run | `text-embedding-3-small` ($0.02/1M) | `SPEND_CAP_USD` |
| judge pass | `CAMPAIGN_JUDGE_SAMPLE_SIZE` 100 patients | `gpt-5.6-terra` via OpenAI Batch (`rater.DEFAULT_MODEL`), one request per criterion decision | `RATER_SPEND_CAP_USD` $50 |
| retries | `MAX_LLM_CLASSIFIER_RETRIES` 3 node re-entries; `MATCHING_PER_TRIAL_EMPTY_RETRIES` 1; `MATCHING_CALL_MAX_ATTEMPTS` 6 wire attempts per call | Stage 5 | `SPEND_CAP_USD` |
| outside the ruled program | ablation study (7 configs x `ABLATION_SAMPLE_SIZE_DEFAULT` 100); ragas | Stage 5 / ragas judge | their own process ledgers |

### 7.3 Measured inputs (saved JSON artifacts, read without a database)

- `09- Testing/Evaluation Runs/eval_run_item7_20260903`: 30 patients,
  `us.anthropic.claude-sonnet-4-6`, per-trial, prompt 1.10.0. Per call: warmup
  prompt mean 9,548 tokens; wave prompt mean 10,370 (cached read 9,541), output
  mean 1,181 (max 3,277); 12.97 trials per patient (max 15); zero retries
  occurred.
- `eval_run_item8_20260903`: 6 patients, same arm; wave output mean 1,053;
  10.0 trials per patient. Its stored `cost_cached_usd` ($1.642334 / 6 = $0.2737)
  equals this session's recomputation from its ledger, which validates the method.
- Prompt growth to HEAD: `render_system_prompt` is 1,050 characters longer at
  1.11.0 than at 1.10.0 (~300 tokens at the measured 3.50 chars/token); added to
  every call below.
- Judge, shape 2, blind mode (`eval_run_item11_20260908_logs/rater_item7`, 176
  requests; `rater_item8`, 15): measured $0.008640 and $0.010351 per request with
  caching; no-cache estimate $0.03180 and $0.03247; the rater's own reservation
  $0.03361 and $0.03444. Decisions per patient: 243.3 (item7), 167.5 (item8).

### 7.4 Campaign budget (`SPEND_CAP_USD` = $300)

Measured estimates at the verified Geo rates, 550 patient-runs (500 + 50):

| quantity | per patient | x550 |
|---|---|---|
| cache working | $0.276 - $0.371 | **$151.78 - $203.99** |
| cache absent (a failed warmup write on every patient) | $0.594 - $0.742 | **$326.63 - $407.97** |
| worst observed patient, x550 | $0.602 (cached) / $1.061 (absent) | $330.88 / $583.32 |
| empty-verdict retry sensitivity at 16.7% of trials (one hard pair, n=12; population rate unknown) | +$0.055 | +$30.33 |
| Stage 2 embedding at its per-attempt bound ($0.00016384) | | $0.09 |

Worst-case bounds, separated from the estimates:

- one possibly-billed trial failure holds $9.042 until resolved (assumed
  multipliers);
- one death with a full wave in flight holds $217.01;
- a node parse re-entry repeats a whole patient (up to 3 times); measured rate
  zero in 36 patients, not otherwise known;
- there is no finite program worst case short of the cap itself, because every
  possibly-billed failure reserves a full bound.

Findings:

- With caching working, the measured program is $152-$204, plus up to $30 of
  empty-verdict retries: $300 covers it with $66-$148 of headroom. That is 7-16
  possibly-billed failures, or less than one death with a full wave in flight.
- With caching absent, the measured program ($327-$408) **exceeds $300**, before
  any failure reservation. The derivation in `config.py` ($225.96 worst case)
  understates it.

### 7.5 Judge (`RATER_SPEND_CAP_USD` = $50), priced separately

100 patients at 167.5-243.3 decisions each = 16,750-24,333 requests.

| basis | total |
|---|---|
| measured with caching | **$144.72 - $251.87** |
| no-cache estimate | $532.65 - $790.11 |
| rater reservation (its own bound) | $562.91 - $837.93 |

`config.py`'s judge row ($7.50 at 25,000 in / 5,000 out per patient on
claude-sonnet-4-6 batch prices) is stale on both the model and the volume: the
measured prompt is ~6,900 tokens per request, about 1.2-1.7M per patient. At
full-decision coverage the $50 rater cap covers roughly 20-35 patients, not 100.
This holds only if the judge pass rates every decision; `rater.py` supports
`--limit`, `--include-keys` and `--retest-fraction`, and the approved pass's
decision scope is not recorded in config.

### 7.6 Recommendation (no value changed)

- Keep `SPEND_CAP_USD` at $300 this session, as instructed. Treat it as
  sufficient **only** for a cache-working campaign with few failures.
- Replace the terra-based derivation in `config.py` with the measured Sonnet
  figures above (a documentation change for the enforcement session).
- Decide the enforcement rule first: without admission accounting, one death with
  a full wave commits 72% of the cap to unresolved reservations.
- Record the judge pass's decision scope. Either sample decisions to fit $50, or
  raise `RATER_SPEND_CAP_USD` by explicit ruling; a full-decision pass is
  $145-$252 measured.

## 8. Changes this session (working tree only; no commit)

No executable behaviour changed except one operator-facing message string and
two probe printouts. No rate, multiplier, limit, ceiling, request shape, model,
truncation rule, prompt or basis version moved.

| file | change |
|---|---|
| `oncotriage/config.py` | Sonnet 4.6 `PRICING_CONFIG` provenance comments: "MEASURED"/"INFERRED" replaced by the verified evidence (E2/E3), with the old provenance kept as history. Assumption (4) rewritten: what AWS documents, what it does not, why absence is not zero, what would settle it. `BEDROCK_ANTHROPIC_MATCHING_MODEL` docstring corrected. The unpriced-model refusal message no longer says the geo rows are inferred; it still names `PRICING_CONFIG` and A6, which a test pins. |
| `oncotriage/agent/bedrock_anthropic_adapter.py` | A6 (VERIFY-AT-GO-LIVE) records the verified rates and what remains open: no console bill, no documented long-context price. |
| `bedrock_probe.py` | Two printouts and two docstrings no longer say the geo rates are inferred. The `inferred` variable and the `rates_inferred` output key are unchanged, because renaming the key would change the probe's output format; the docstring says the name predates verification. |
| `tests/test_billing_closure.py` | New 9q, 9q-0, 9q-i, 9q-ii, 9q-iii (below). SECTION 9's title and SECTION 1's derivation comment now say the Sonnet multipliers are assumed. |
| `tests/test_agent_bedrock_anthropic_adapter.py` | Two check labels ("INFERRED geo rate", "the refusal says the geo rows are inferred") corrected. Assertions unchanged. |
| `tests/test_provenance_truth.py` | One comment and two check labels corrected. Assertions unchanged. |

### New checks (`tests/test_billing_closure.py`)

| check | proves |
|---|---|
| 9q | Every Sonnet 4.6 row (global, us, eu, au, jp, In-Region) carries the rates AWS publishes. The expected values are typed from E3, never read from config, so a rate edited without new evidence fails. |
| 9q-0 | Non-degeneracy: six wire ids checked, all documented Stage 5 models. |
| 9q-i | No geo factor on top of a geo row: the shipped bound's input rate is 4.125 x 2.0 and output 16.50 x 1.5; global is 3.75 x 2.0 and 15.00 x 1.5; geo/global ratio is 1.1 on both halves. |
| 9q-ii | Every Sonnet 4.6 bound basis says ASSUMED; no GPT-5.6 Terra basis does. |
| 9q-iii | The assumed multipliers are exactly 2.0 / 1.5 on every Sonnet row, so a change (including treating missing documentation as a zero premium) must come with evidence. |

### Changed assertions

None of the P4b / P4c recovery expectations, the campaign-record reservations
or the provider-resilience figure changed. They are all derived from the bound,
and the bound did not change: 4q $7.076, 4y (3, $7.826) / $6.576, 4z $7.576,
campaign `_RESERVE_EXPECTED` $5.826 and provider-resilience 5b $8.25794475 were
re-verified by running, not edited.

## 9. Tests and results (all under containment, isolated root)

| suite | start | end |
|---|---|---|
| `test_billing_closure.py` | 383 / 0 / 0 | **388 / 0 / 0** (+5: 9q family); final real-tree run 388 / 0 |
| `test_campaign_billing_record.py` | 128 / 0 | 128 / 0 |
| `test_package_invariants.py` | 261 / 0 / 0 | 261 / 0 / 0 |
| `test_storage_run_metrics_flush.py` | 130 / 0 | 130 / 0 |
| `test_provider_resilience.py` | -- | 203 / 0 / 0 |
| `test_spend_gate.py` | -- | 165 / 0 |
| `test_spend_coverage.py` | -- | 169 / 0 |
| `test_agent_bedrock_anthropic_adapter.py` | -- | 425 / 0 |
| `test_provenance_truth.py` | -- | 90 / 0 |
| `test_agent_prompt_version.py` | -- | 93 / 0 |
| `test_run_tunables_record.py` | -- | exit 0 |
| `static_checks.py` | -- | exit 0, all files compiled |
| `ci_test_buckets.py --check` | -- | consistent: 149 test files, 130 in bucket A |

No tripwire log was created by any suite run: zero events.

The R1 acceptance checks this session re-ran against the unchanged bound, as
part of `test_billing_closure.py`: adversarial text (9d: empty, NUL, 200k CJK,
emoji, combining marks, 4 MB), truncation (9o-i), cache and warmup (9b, 9p),
pricier-model echo (9o), refusal with zero provider calls (9f, 9m), fresh-process
SIGKILL after the answer (9j), settlement + discrepancy + marker total failure
(9k), legacy unproven reservations refused by name (9i, 9m), clean resume
control (9n).

## 10. Firing controls (disposable copies)

Each copy: `oncotriage/`, `tests/` and entry points copied into the scratchpad,
one anchored plant, compiled; the editable-install finder stripped; a realpath
preflight confirmed every copy imports its own package; sandbox + tripwire
active; the copy's own `tests/test_billing_closure.py` run.

### Round 1 (test file before the 9q-iii / 9q-i hardening)

| control | plant | failed checks | total |
|---|---|---|---|
| F0 clean | none | none | 388 / 0 |
| F1 | old chars/3 estimate restored | 35, incl. 9d, 9h, 9j, 9k, 9l, 9p | 353 / 35 |
| F2 | no refusal for an undocumented model | 9e, 9f, 9f-i, 9i | 384 / 4 |
| F3 | cache-write class dropped | 34, incl. 9a..9d, 9q-i | 354 / 34 |
| F4 | long-context multipliers dropped in the owner | 35, incl. 9a..9d, 9o, 9q-i | 353 / 35 |
| F5 | SDK attempts ignored | 9b-i | 387 / 1 |
| F6 | resume unproven-liability check removed | 9m, 9m-i | 386 / 2 |
| F7 | basis-version check ignored | 9i | 387 / 1 |
| F8 | node-top guard removed | 9f-ii | 387 / 1 |
| F9 | `bound_exceeded` count removed | 9h-ii, 9l | 386 / 2 |
| **F10** | shipped `us.` input rate drifts 3.30 -> 3.00 | **9q** | 387 / 1 |
| **F11** | a geo factor applied on top of a geo row | **9a, 9a-i, 9a-ii, 9b, 9b-i, 9q-i** | 382 / 6 |
| **F12** | Sonnet basis relabelled "documented" | **9q-ii** | 387 / 1 |
| **F13** | Sonnet multipliers set to 1.0 (missing docs treated as zero) | **9a, 9a-i, 9b, 9b-i, 9q-i, 9q-iii** | 382 / 6 |

All 13 plants were caught, none aborted, and no copy produced a traceback. The
tripwire recorded zero events. The real tree changed during this round only
because the 9q hardening was applied to `tests/test_billing_closure.py` mid-run
(the hash difference is that one file).

F10 is caught by 9q alone: the other checks derive from `PRICING_CONFIG` and move
with it, which is exactly why the evidence pin exists.

### Round 2 (final tree, all copies regenerated)

Identical to round 1 for 13 of 14 copies: F0 388 / 0; F2..F13 the same failed
checks as round 1, including F10 -> 9q, F11 -> 9a, 9a-i, 9a-ii, 9b, 9b-i, 9q-i,
F12 -> 9q-ii, F13 -> 9a, 9a-i, 9b, 9b-i, 9q-i, 9q-iii. Every copy imported its
own package, the tripwire recorded zero events, and the real tree (all
`oncotriage/` and `tests/` files plus `bedrock_probe.py`) was byte-unchanged by
the round. The final real-tree `test_billing_closure.py` run: 388 / 0 / 0.

**F1 aborted in round 2** with `TypeError: '<' not supported between instances of
'_Absent' and 'float'` at `tests/test_billing_closure.py` line 3478 (check
"7z-iii *** THE STATED BOUND, THROUGH main(): with the database AND the "), after recording its first 16 failures. Rerun alone under the same
containment, the same copy gave 353 / 35 with no traceback, identical to round
1. So the abort is load-dependent (four copies ran concurrently): a fresh-process
drive returned no seed, and that pre-existing comparison is not guarded. F1 was
still caught in both rounds.

## 11. Defects and findings, by severity

### Fixed this session

- **MEDIUM, provenance labels false after verification.** `config.py` PRICING
  comments, the model docstring, the unpriced-model refusal, the adapter's A6,
  two probe printouts and three test labels said the geo rows were INFERRED,
  and assumption (4) said the pricing page "did not render its Claude rows". All
  six Sonnet rows are published by AWS (E2/E3). Corrected; values unchanged.
- **LOW, SECTION 9 title claimed "a documented upper bound".** For the shipped
  arm the long-context multipliers are assumed. Retitled.
- **LOW, this session's own 9q-iii used a bare subscript** that would abort the
  file if a limits entry were removed. Now read through `at`.
- **LOW, this session's own 9q-i label claimed a 1.1x ratio the check did not
  assert.** The ratio is now asserted on both halves.

### Unresolved

- **HIGH, the shipped arm's bound is not proven.** It is conditional on an
  assumed 2.0x / 1.5x long-context ceiling that no AWS document states (P1).
  Per the evidence rule, the implementation stopped there: multipliers were
  neither raised nor lowered.
- **HIGH, headroom.** $217.01 of unresolved liability with 24 attempts in flight
  (72.3% of $300); 3 possibly-billed failures cross the $25 serving window.
- **HIGH, the campaign cap is insufficient if caching fails.** The measured
  cache-absent program is $327-$408, above $300, before any failure reservation.
- **HIGH, the judge budget does not cover a full-decision 100-patient pass.**
  Measured $145-$252 against `RATER_SPEND_CAP_USD` $50; `config.py`'s $7.50
  judge row is stale on model and volume.
- **MEDIUM, `config.py`'s `SPEND_CAP_USD` derivation prices the wrong arm**
  (gpt-5.6-terra, fixture token shapes). Reported, not rewritten (the cap value
  and its docstring are the enforcement session's decision).
- **MEDIUM, CLAUDE.md still says the geo rows are inferred** and carries the old
  cap derivation. Deferred to the combined-checks reconciliation.
- **LOW, the GPT-5.6 Terra limits (long-context 2.0 / 1.5, 1.25x write) were not
  re-read this session.** They rest on R1's evidence and are outside the
  shipped arm and this session's network exception.

- **LOW, pre-existing abort shape (not introduced here).** Line 3478
  (check "7z-iii *** THE STATED BOUND, THROUGH main(): with the database AND the ") compares `at(at(_d2, "seed"), "usd") < at(_d1, "measured")`
  unguarded. When a fresh process returns no seed, as it did under concurrent
  load in control round 2, the file aborts instead of recording a failure.
  Proposal: route the comparison through `_both_numbers`, as SECTION 9 does.
  Not changed: outside R1b's scope.

### Unverified

- No console bill has been compared against any rate (A6).
- E1 (the model card) was read through WebFetch's rendered text and not saved as
  bytes. E2 and E3 are saved with sha256.
- The per-patient measured costs use prompt 1.10.0 runs plus a computed 1.11.0
  prompt adjustment (+300 tokens per call); no 1.11.0 run was measured.
- The judge's decision scope for the approved pass is not recorded; the range
  assumes every decision is rated.
- Two WebFetch summaries of the pricing page wrongly reported no Sonnet 4.6 row.
  The raw HTML and its data endpoint are the evidence used; the summaries are
  not.

## 12. Next action: the enforcement decision

The gate in `spend.py` checks accumulated MEASURED cost immediately before a
request, so its documented overshoot is "the requests in flight", up to $217.01
at the assumed bound. Choose between:

1. **Admission accounting for concurrent liabilities.** Admit a request only
   when `measured spend + unresolved reservations + this bound <= cap`. The
   overshoot becomes zero, at the price of admitting only
   floor(($300 - spent) / $9.042) concurrent attempts. Near the cap this
   throttles hard; with 24 in flight it needs $217 of free budget.
2. **An explicitly defined permitted overshoot.** Keep the measured-cost gate and
   state the bound: in-flight attempts x per-attempt bound ($217.01 today), with
   the cap's documented ceiling being cap + overshoot.

Settle P1 before or alongside: an AWS statement or a console bill line for a
Sonnet 4.6 request above 200K input tokens. At 1.0 / 1.0 every figure above
roughly halves. Then settle the cache-absent sufficiency and the judge's decision
scope.

## End state

- **Digests.** `renderer_digest` `5ea2c6cc...`, `PROMPT_VERSION` 1.11.0,
  `FINGERPRINT_VERSION` 8 and both rendered system-prompt sha256 values are
  identical to the start. No renderer module was touched.
- **Production.** All 40 files under `02- Data/03- Inferences Storage` and
  `08- Checkpoint` are byte-identical to the start inventory. No test connected
  to a production database; the decoy is unchanged (`fed6a800...`).
- **Repository.** Branch `wip/billing-closure`, HEAD still `48c0d88d`. No commit,
  no stash, no branch change. Modified: `bedrock_probe.py`,
  `oncotriage/agent/bedrock_anthropic_adapter.py`, `oncotriage/config.py`,
  `tests/test_agent_bedrock_anthropic_adapter.py`, `tests/test_billing_closure.py`,
  `tests/test_provenance_truth.py`; new: this report.
- **Network.** The only egress was the documentation fetches in section 3
  (AWS model card, AWS pricing page HTML, the page's data endpoint JSON). No
  provider API call, no credential, no inference endpoint. No test arm escaped
  containment and no arm removed both protections.
- **Paid calls.** None.
