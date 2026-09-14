# RECOVERY R1c -- the AWS Price List, and which pricing outcome it supports

Status: COMPLETE for R1c. Outcome **2c** (neither a full-window single rate nor a
pricing threshold is established). Nothing was built. Stopped here; the
enforcement design and the combined checks were not begun.

Written for: the operator and the enforcement session.

## 1. Starting state

- Branch `wip/billing-closure`, HEAD `d284f160e63554e99b411cda3ae02297b2d7b5d8`
  ("docs: verify Bedrock pricing provenance and record unresolved billing
  assumptions. NOT FOR PUSH").
- Dirty files at start: none (`git status --short` empty).
- Baselines, measured at start under containment (both layers, isolated root):
  `test_billing_closure.py` 388/0/0, `test_campaign_billing_record.py` 128/0,
  `test_package_invariants.py` 261/0/0, `test_storage_run_metrics_flush.py`
  130/0. All four match the brief. Zero tripwire records.
- Digests at start: `renderer_digest` `5ea2c6cc...c2a956`, `PROMPT_VERSION`
  1.11.0, `FINGERPRINT_VERSION` 8, rendered system prompt sha256 `8baaa8b8...`
  (MeSH applied) and `db3c2b4d...` (not applied).
- Production inventory at start: sha256 of all 40 files under
  `02- Data/03- Inferences Storage` and `08- Checkpoint`.
- Shipped configuration read through the owner under containment: provider
  `bedrock_anthropic`, wire `us.anthropic.claude-sonnet-4-6`, per-trial,
  `MATCHING_MAX_TOKENS` 32,000, warmup ceiling 1, TTL 5m, `MAX_WORKERS` 12 x
  parallel bound 2, SDK attempts 1, caps $300 / $25 serving / $50 rater.

## 2. Containment

- OS sandbox (`sandbox-exec`): outbound network denied except loopback, the
  system DNS socket (`mDNSResponder`) denied, writes and data reads denied under
  `02- Data/03- Inferences Storage`, `08- Checkpoint`, `09- Testing/Evaluation
  Runs`, `04- Results` and a decoy.
- `sitecustomize` audit-hook tripwire: refuses and records outbound connects, DNS
  lookups, sqlite connects to protected paths and writes to protected paths.
- Controls fired first:
  - Arm A (sandbox + tripwire): external connect, raw socket connect, DNS, a
    protected sqlite read-only connect, a protected write and a protected listing
    all refused; loopback allowed; production directory listings and the
    checkpoint read refused.
  - Arm C (sandbox only, tripwire off, `env -i`): the same probes refused by the
    sandbox alone.
- No arm removed both protections, and no arm escaped. Every test ran with both
  layers and an isolated project root provisioned by
  `.github/scripts/provision_ci_paths.py`.
- The only network egress this session was the documentation fetches in
  section 3, made outside the sandbox with `curl` under `env -i` (no credentials,
  no AWS SDK, no inference endpoint, no provider API).

## 3. Sources (all retrieved 2026-09-14; raw bytes saved in this session's scratchpad `r1c_evidence/`)

| # | source | retrieved (UTC) | bytes | sha256 |
|---|---|---|---|---|
| S1 | Price List offer index `pricing.us-east-1.amazonaws.com/offers/v1.0/aws/index.json` (publicationDate 2026-09-14T06:37:14Z) | 09:46:27 | 90,012 | `133e7bc1c68d97057ea34d8a2795c20eee794c1ad61a0991f1b67a4ac5d09113` |
| S2 | `.../AmazonBedrockFoundationModels/current/index.json` (publication 2026-09-11T12:44:10Z, version 20260911124410) | 09:46:38 | 6,611,124 | `c21fff8ee033192dba63cdfe4155b4e07598aadd7da63736e365f17f1c73de1f` |
| S3 | `.../AmazonBedrock/current/index.json` (publication 2026-09-11T12:44:08Z) | 09:46:39 | 15,988,980 | `35e5fb04a014dd2147da5058ae75eb6fb8a6178f39058ac3aa4b39f151191417` |
| S4 | `.../AmazonBedrockService/current/index.json` (publication 2026-09-11T12:44:10Z) | 09:46:39 | 387,729 | `63602c5e57e7353d513d8abd8554ba8082f834fa45c4c3cd5fea6e59a2c9fb61` |
| S5 | `.../AmazonBedrockFoundationModels/current/region_index.json` | 09:53:53 | 6,704 | `f8c13e730b2ff29250039f6efbf62f40021c5651808d7eef23855788f9836911` |
| S6 | `.../AmazonBedrockFoundationModels/20260911124410/us-east-1/index.json` | 09:53:54 | 459,097 | `4878b1e83d772e2d4463e54ea9b6ac0ff16ae7252144f4dd367195b2b4b520c1` |
| S7 | `.../AmazonBedrock/current/region_index.json` | 09:53:53 | 6,302 | `ed6296bf982469cadde6b17d178206f9f37e28eb8d4105eb54d401ca704e4c1f` |
| S8 | `.../AmazonBedrock/20260911124408/us-east-1/index.json` | 09:53:53 | 1,420,545 | `c854008e445a3da07c5c216d30aa08e3d641da2be32c55a99ec95f4342243a68` |
| S9 | AWS Bedrock pricing page `aws.amazon.com/bedrock/pricing/` (raw HTML) | 09:48:39 | 895,209 | `52eb8052cb252cf31444d70b2f2c7d60534329f4e82e42510eeaef57eaefc29b` |
| S10 | AWS model card, Claude Sonnet 4.6 `docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html` (raw HTML) | 09:48:39 | 69,508 | `3cfd1def21bb16fa8204facb440eb58d956fecefd80d26a320631a38fa78cffc` |
| S11 | AWS Service Terms `aws.amazon.com/service-terms/` (raw HTML) | 09:48:39 | 1,060,094 | `a3806a4a0ab5c37fc00bb2c2cde0882179a431d29484360661e74073579e6bd1` |
| S12 | AWS model card, GPT-5.6 Terra `docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-56-terra.html` (raw HTML) | 09:48:40 | 76,673 | `0c9893445b7181ec2f2dfa5764b272f74c6b0e71d809f4651e654b7e706c48fe` |

Notes on scope: S12 is a Bedrock pricing document for a different model. It was
fetched as the control for how AWS documents a long-context premium, and it is
disclosed here because the exception named the Sonnet 4.6 model card and the
Bedrock pricing and pricing-terms documents. S9's sha256 differs from R1b's
(`d33a2695...`) at the identical byte length; the page carries dynamic
content, and R1b's price conclusions rested on its data endpoint, not the HTML
bytes.

## 4. What the Price List establishes, and what it does not

### 4.1 Sonnet 4.6 on Bedrock (S2, confirmed in S6)

- `servicename` "Claude Sonnet 4.6 (Amazon Bedrock Edition)": 620 SKUs, term type
  `OnDemand` only, `termAttributes` empty on every offer.
- Product attributes are exactly `location`, `locationType`, `operation` (empty),
  `regionCode`, `servicename`, `usagetype`. **No attribute describes input size,
  context window, tier or request length.**
- 22 usage types (region prefix removed), every one priced once, identical in
  every region where it appears, `beginRange` 0, `endRange` Inf:

  | usage type | USD | unit | regions |
  |---|---|---|---|
  | `InputTokenCount` / `_Global` | 3.30 / 3.00 | 1M tokens | 19 / 33 |
  | `OutputTokenCount` / `_Global` | 16.50 / 15.00 | 1M tokens | 19 / 33 |
  | `CacheReadInputTokenCount` / `_Global` | 0.33 / 0.30 | 1M tokens | 19 / 33 |
  | `CacheWriteInputTokenCount` / `_Global` | 4.125 / 3.75 | 1M tokens | 19 / 33 |
  | `CacheWrite1hInputTokenCount` / `_Global` | 6.60 / 6.00 | 1M tokens | 19 / 33 |
  | `InputTokenCount_Batch` / `_Global_Batch` | 1.65 / 1.50 | 1M tokens | 19 / 33 |
  | `OutputTokenCount_Batch` / `_Global_Batch` | 8.25 / 7.50 | 1M tokens | 19 / 33 |
  | `Reserved_{1,3}Month_{Input,Output}TPM_{Geo,Global}` | 0.198 / 0.18 / 0.99 / 0.90 (1 month); 0.1782 / 0.162 / 0.891 / 0.81 (3 month) | 1M TPM Hour | 32 each |

- Descriptions are of the form "AWS Marketplace software usage|us-east-1|Million
  Input Tokens Regional CRIS" / "...Global". None mentions context or size.
- `beginRange`/`endRange` in the Price List are usage-volume tiers, not per-request
  size. "0 to Inf" says nothing about how a large request is metered.
- S3, S4 and S8 carry **no** Sonnet 4.6 SKU.
- The Global and Geo on-demand rates equal `PRICING_CONFIG` exactly
  (re-confirming R1b's P3/P4 from a second first-party source).
- S9 and S10 re-read: the model card says "1M token context window", "Context
  window: 1M tokens", "Max output tokens: 64K", Marketplace product
  `prod-ffvjxvh4ltq64`, and no price. The pricing page has no "long context",
  "200K", "200,000" or "272K" text. S11 has none of those terms.

**Established:** the published on-demand price dimensions for Sonnet 4.6 on
Bedrock, and that none of them is conditioned on input size.

### 4.2 Why that absence does not prove "no premium" (controls from the same publication)

- **Negative control -- a documented premium the Price List omits.** S12 (the
  AWS model card) documents commercial-region GPT-5.6 Terra pricing, short
  context "(272K input tokens or fewer)" Geo CRIS $2.20 in / $2.75 30m write /
  $0.22 read / $13.20 out, and "long context (more than 272K input tokens)"
  $4.40 / $5.50 / $0.44 / $19.80. **No offer file (S2, S3, S4, S8) lists
  commercial-region Terra at all**; Terra and Luna appear only in S3 for
  `us-gov-west-1` (Terra GovCloud long-ctx standard $5.28 / $6.60 / $0.528 /
  $23.76, which does match S12's GovCloud long-context row). So the Price List
  omits a first-party documented AWS long-context premium for a third-party
  Bedrock model, and its silence about Sonnet 4.6 cannot be read as a statement.
- **Positive control -- the format can express a Claude long-context tier.** S4
  lists Claude Sonnet 4 long-context SKUs (`Claude4Sonnet-{input-tokens,
  output-tokens, cache-read-input-token-count, cache-write-input-token-count}-long-context-cross-region-global`),
  `inferenceType` "Input tokens long context" / "Output tokens long context", in
  5 regions. Ratios to the same offer's base rows: input 2.0, cache read 2.0,
  cache write 2.0, output 1.5 (us-east-1: $0.006 / $0.0006 / $0.0075 / $0.0225
  per 1K against $0.003 / $0.0003 / $0.00375 / $0.015).
- **And that tier is itself partial.** Sonnet 4's base SKUs sit in the
  Marketplace offer (S2) in 22 regions, including Geo (non-global) usage types;
  its long-context SKUs sit in a different offer (S4), global CRIS only, 5
  regions, and carry no threshold attribute. A Claude model's billing can
  therefore be split across offers with incomplete coverage.
- **Not established by any source:** whether Bedrock bills a Sonnet 4.6 request
  above some input size at a premium; any threshold (no "200K" appears for
  Sonnet 4.6 in any source; Terra's 272K is Terra's); whether any premium would
  stay within 2.0x / 1.5x.
- What the Price List does add: the only Claude long-context structure AWS
  publishes (Sonnet 4) is exactly the 2.0x input/cache, 1.5x output structure
  assumed for Sonnet 4.6. That is supporting context for the assumed ceiling's
  shape, for a sibling model. It is not evidence about Sonnet 4.6.

## 5. Outcome: 2c, and why not 2a or 2b

- **2a rejected.** It requires one verified rate to cover the full 1M window. The
  offer lists one rate per class, but section 4.2's negative control shows the
  Price List omits documented premiums, so an unconditioned rate there does not
  verify full-window coverage.
- **2b rejected.** No pricing threshold for Sonnet 4.6 on Bedrock is established
  by any source, so there is nothing to guard at. (Counting would also be
  unresolved: no provider-supported offline token count for the complete Converse
  request was examined, and character estimates are insufficient by the brief.)
- **2c applied.** Nothing built. The 2.0x / 1.5x multipliers remain an ASSUMED
  ceiling. No rate, multiplier, threshold, limit, request, model, ceiling,
  truncation rule, prompt, cap, evaluation scope or basis version changed.
  `STAGE5_RESERVATION_BASIS_VERSION` stays 1.

### What would settle it

- A first-party AWS statement of how Claude Sonnet 4.6 requests are priced on
  Bedrock above any input size: an AWS documentation page, a Price List SKU
  published for it, or a written answer from AWS Billing / Support. Either "no
  premium at any input size" (then 2a: multipliers 1.0/1.0) or "premium X above
  threshold T" (then 2b, subject to an offline counting method that covers the
  whole Converse request).
- Not a paid call: one request's bill verifies that request, not the pricing
  conditions.
- Uninspected, and outside this session's exception: the AWS Marketplace listing
  for `prod-ffvjxvh4ltq64` (its pricing dimensions). It would not settle the
  question alone, because Sonnet 4's long-context SKUs were billed outside its
  Marketplace offer (section 4.2).

## 6. Bounds and headroom (unchanged; owner-computed and independently re-derived)

Owner (`config.stage5_attempt_bound`, under containment) and an independent
calculation from the S2 rates and S10 limits agree to the cent.

| quantity | assumed 2.0 / 1.5 (shipped, unproven) | scenario 1.0 / 1.0 (not adopted) |
|---|---|---|
| one trial attempt, `us.` profile, 32,000 ceiling | **$9.042** | $4.653 |
| one warmup, `us.` profile, 1-token ceiling | **$8.25002475** | $4.1250165 |
| one trial attempt, `global.` profile | $8.22 | $4.23 |
| unresolved liability, 24 attempts in flight | **$217.008** (72.3% of $300) | $111.672 (37.2%) |
| possibly-billed failures that fit under $300 | **33** ($1.61 left; the 34th crosses) | 64 ($2.21 left; the 65th crosses) |
| possibly-billed failures that fit under the $25 serving window | **2** ($6.92 left; the 3rd crosses) | 5 ($1.74 left; the 6th crosses) |

Arithmetic (shipped trial): 1,000,000 x max(3.30, 0.33, 4.125 [5m]) x 2.0 = $8.25;
32,000 x 16.50 x 1.5 = $0.792; total $9.042. **This is not a proven ceiling.**

## 7. Changes (working tree only; no commit, no stash)

| file | change |
|---|---|
| `oncotriage/config.py` | Comment only, assumption (4) of `STAGE5_ATTEMPT_LIMITS`: records the Price List inspection, the Terra negative control, the Sonnet 4 positive control, and replaces "a console bill line" as the settling evidence with a first-party AWS statement. No executable line, rate, multiplier or `basis` string changed. |
| `RECOVERY_R1C_REPORT.md` | New: this report. |

No test was added or changed: no mechanism changed, and the Sonnet 4.6 rates and
the ASSUMED labelling are already pinned (`test_billing_closure.py` 9q family).

## 8. Tests and results (under containment, isolated root)

Re-run after the comment edit, all with both containment layers:

| suite | start | end |
|---|---|---|
| `test_billing_closure.py` | 388 / 0 / 0 | 388 / 0 / 0 |
| `test_campaign_billing_record.py` | 128 / 0 | 128 / 0 |
| `test_package_invariants.py` | 261 / 0 / 0 | 261 / 0 / 0 |
| `test_storage_run_metrics_flush.py` | 130 / 0 | 130 / 0 |
| `test_agent_bedrock_anthropic_adapter.py` | -- | 425 / 0 |
| `test_provenance_truth.py` | -- | 90 / 0 |
| `static_checks.py` | -- | exit 0, 317 compiled |
| `ci_test_buckets.py --check` | -- | consistent: 149 test files, 130 in bucket A |

- Zero tripwire records in every suite run (no log file created); the only
  tripwire log this session is Arm A's own control probe.
- The project's secrets scanner (`scan_bytes`, `scan_filename`) reports no
  finding on `oncotriage/config.py` or this report.
- R1's adversarial, fresh-process, legacy-refusal and firing-control checks were
  NOT re-driven as a separate matrix: no bound changed, so the brief's re-verify
  condition did not trigger. They ran unchanged as part of
  `test_billing_closure.py` (9d, 9f, 9i-9p, 9q family) and passed.
- Not run this session (deferred to combined checks by the brief): full bucket A,
  the serial runner, `fixture_replay.py`, retrieval observability.

## 9. Unresolved issues

- **HIGH, the shipped arm's bound is still unproven.** It rests on assumed 2.0x /
  1.5x multipliers (section 5).
- **HIGH, headroom** (section 6): one death with a full wave in flight commits
  $217.01 (72.3% of $300); 3 possibly-billed failures cross the $25 serving
  window.
- **Carried from R1b, not re-examined:** the campaign cap is insufficient if
  caching fails (measured cache-absent program $327-$408); the judge budget does
  not cover a full-decision 100-patient pass; `config.py`'s `SPEND_CAP_USD`
  derivation prices the wrong arm; CLAUDE.md still says the geo rows are inferred.
- **Carry forward for combined checks:** `tests/test_billing_closure.py` check
  7z-iii (label at line 3472, the unguarded comparison at line 3478 in the current file, re-confirmed R1c; "THE STATED BOUND, THROUGH main(): with the database
  AND the ...") compares `at(at(_d2, "seed"), "usd") < at(_d1, "measured")`
  unguarded and aborts the file under load when a fresh process returns no seed.
  Open question: why its child process returned no seed under concurrent load in
  R1b's control round 2. Proposal: route the comparison through `_both_numbers`.
  Not changed here.

## 10. Next action for the enforcement session

Atomic budget admission. Checking available budget and reserving it must be one
operation across concurrent workers:

1. Under one lock (or one serialized DB transaction), compute
   `measured spend + every unresolved reservation + this attempt's bound` and
   compare it with the cap; on admit, write the reservation in the same critical
   section, so no two workers can both see the same free budget.
2. Count each liability once. A resumed balance already includes durable
   reservations from earlier processes (`campaign_billing_total` charges a
   reserved row at its reservation); the in-process ledger must not add those
   same rows again, and a live `AttemptLiability` must not be counted both as an
   open reservation and as its settled charge.
3. Decide the admission rule explicitly: zero overshoot (admits only
   floor((cap - committed) / $9.042) concurrent attempts at the assumed bound) or
   a stated permitted overshoot. At 1.0 / 1.0 every figure roughly halves, so
   settling section 5's AWS statement first changes the throughput cost.

## 11. Self-review findings

- **LOW, fixed in draft: remedy text.** The pre-existing config comment named "a
  console bill line" as the settling evidence; that is a paid call and verifies
  one request only. Replaced with a first-party AWS statement.
- **LOW, not changed: stale parenthetical.** The same comment still says the
  2.0x / 1.5x class was "not re-read in R1b" for Terra. S12 re-reads it in R1c:
  Geo CRIS long context $4.40 / $5.50 / $0.44 / $19.80 against short $2.20 /
  $2.75 / $0.22 / $13.20 (2.0x input, cache write and read; 1.5x output), matching
  `_TERRA_BEDROCK_LIMITS`. The parenthetical is true as written and was left.
- **Scope disclosure: S12 (Terra model card) was fetched** as the control for the
  absence argument; it is an AWS Bedrock pricing document but not one the
  exception named by title.
- **Claim limits.** "No offer lists commercial Terra" was checked across S2, S3, S4
  (aggregate) and S8 (us-east-1 regional); other regional files were not fetched
  individually (the aggregate `current/index.json` covers all regions). The
  admission-design note in section 10 about `campaign_billing_total` charging a
  reserved row at its reservation comes from CLAUDE.md and code reading, not a
  test run this session.
- **No sensitive path changed.** No billing, budget, authentication, deletion or
  overwrite code was touched; the diff is one comment block.

## End state

- **Digests.** `renderer_digest` `5ea2c6cc...c2a956`, `PROMPT_VERSION` 1.11.0,
  `FINGERPRINT_VERSION` 8 and both rendered system-prompt sha256 values identical
  to the start (`diff` empty).
- **Production.** All 40 files under `02- Data/03- Inferences Storage` and
  `08- Checkpoint` byte-identical to the start inventory. No test connected to a
  production database; the decoy is unchanged (`fed6a800...`).
- **Repository.** Branch `wip/billing-closure`, HEAD still `d284f160`. No commit, no
  stash (stash list empty), no branch change. Modified: `oncotriage/config.py`
  (comment only). New: this report.
- **Network.** Only the documentation fetches in section 3 (AWS Price List offer
  files, the Bedrock pricing page, two AWS model cards, the AWS Service Terms). No
  provider API call, no credential, no inference endpoint. No test arm escaped
  containment and no arm removed both protections.
- **Paid calls.** None.
