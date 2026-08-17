# Characterization fixture captures: what each one cost and what it moved

**THE FIXTURES ARE NOT IN GIT AND THIS FILE IS THE ONLY PLACE THEIR PROVENANCE
LANDS.** They live at `09- Testing/Characterization Fixtures/`, a sibling of
`03- Code/` outside the version-controlled tree, so a capture leaves no commit
of its own: the twelve `.json.gz` files change on disk and the repository looks
untouched. Everything a later reader needs — what the run cost, which model and
prompt version produced it, which Qdrant collection it is pinned to, and which
verdicts moved against the set it replaced — has no other home.

It sits at the code root beside `DOCKER CLEAN BRING-UP.md`, `Exception and
Fallback Audit.md` and `PIPELINE SEQUENCE.md`, which is where this project puts
a cross-cutting operational record. It accretes **one section per capture,
newest first**. `tests/FILE NUMBER MAPPING.md` is the model for the shape: an
artefact rather than a memory.

**Every number below is re-derived from the fixtures themselves** — token counts
from each recorded response's `usage` block, call counts from
`len(recordings.chat_completions)`, the prompt version from
`deterministic_prefix.stage5.llm_classifier_prompt_version`, the pin and the
sampling settings from `environment`, and the dollar figures from
`oncotriage.utils.get_model_cost` against `PRICING_CONFIG`. None of it is typed
from a prior report.

---

## 2026-08-17 UTC — full recapture at PROMPT_VERSION 1.9.0

Twelve fixtures, captured `2026-08-17T18:01:29Z` to `2026-08-17T18:29:55Z`. Schema version
**8** throughout — 1.9.0 is a prompt edit, not a recording-format edit,
so no bump was needed and none was made.

**WHY IT WAS RUN.** `780219a` shipped 1.9.0: temporal quote-before-judge in RULE 4's
time-window branch and one added line in the FINAL REMINDER. The fixtures on disk were
captured at **1.8.0**, so replay was red. Re-measured against the backup immediately
before this note rather than quoted: **0/12 clean, exit 1, 84 changed / 56 removed / 1
added**, with `prompt_version` and both prompt hashes moving on all twelve and three
replay misses on `mesh_fallback_siteless_code`. Stale-recording artefacts, not defects.

**THE BEFORE-REPLAY IS NOT A STABLE MEASUREMENT, and this pass is what established
that.** An earlier characterization of the same red state recorded 84 changed / **13**
removed / 1 added. The changed and added counts reproduce exactly; the removed count
does not, and the reason is structural: **Qdrant is not in `recordings`**, so replay
re-runs Stage 2 against the LIVE index while serving Stage 3 and Stage 5 from the
recording. A stale fixture therefore accumulates removals as live retrieval drifts away
from the pool it recorded. `mesh_fallback_siteless_code` (139 fields) and `normal_1` (42)
carry almost all of the growth. A before-number is a statement about a moment, and this
one is timestamped by the run above it.

### Environment

| | |
|---|---|
| prompt version | **1.8.0 → 1.9.0** (all twelve, both sides) |
| matching model | `gpt-5.6-terra` |
| collection pin | **`trial_criteria_20260810_145902`** (alias `trial_criteria` resolved to it; digest 14,324 points / 14,324 distinct NCT IDs / `479c37ec56a766fe…`) |
| temperature | `None` — the provider default; the model rejects the parameter |
| seed | `42`, best-effort |
| reasoning effort | `none` |
| max completion tokens | `32000` |
| pricing | `PRICING_CONFIG` dated 2026-08-04 — $2.00/M in, $12.00/M out |

### Spend, per fixture, against the pre-run estimate

Old and new both re-derived from the two fixture sets' recorded usage. The `1.5x stop`
column is the per-fixture ceiling the run was gated on, lifted from the 1.8.0 section
above by parsing it rather than retyping it.

| fixture | calls o→n | input o→n | output o→n | $ old | $ new | 1.5× stop |
|---|---|---|---|---|---|---|
| `ablation_bm25_only` | 2 → 2 | 21,385 → 21,503 | 6,547 → 6,642 | $0.12133 | $0.12271 | $0.182 |
| `ablation_no_cross_encoder` | 4 → 4 | 45,207 → 45,443 | 8,339 → 8,592 | $0.19048 | $0.19399 | $0.286 |
| `ablation_vector_only` | 2 → 2 | 22,032 → 22,150 | 7,545 → 7,858 | $0.13460 | $0.13860 | $0.202 |
| `llm_classifier_parse_retry_constructed` | 0 → 0 | 0 → 0 | 0 → 0 | $0.00000 | $0.00000 | not billable |
| `mcode_genomic_variant` | 1 → 1 | 9,939 → 9,998 | 4,772 → 4,783 | $0.07714 | $0.07739 | $0.116 |
| `mesh_fallback_siteless_code` | 4 → 5 | 44,629 → 54,195 | 10,730 → 10,498 | $0.21802 | $0.23437 | $0.327 |
| `no_candidates_pediatric_age` | 0 → 0 | 0 → 0 | 0 → 0 | $0.00000 | $0.00000 | n/a |
| `normal_1` | 3 → 3 | 34,171 → 34,348 | 5,999 → 5,930 | $0.14033 | $0.13986 | $0.210 |
| `normal_2` | 3 → 3 | 32,408 → 32,585 | 11,088 → 11,173 | $0.19787 | $0.19925 | $0.297 |
| `normal_3` | 1 → 1 | 11,335 → 11,394 | 5,731 → 5,899 | $0.09144 | $0.09358 | $0.137 |
| `truncation_split` | 3 → 3 | 30,608 → 30,785 | 11,813 → 11,787 | $0.20297 | $0.20301 | $0.304 |
| `unknown_stage` | 2 → 2 | 21,210 → 21,328 | 9,983 → 10,093 | $0.16222 | $0.16377 | $0.243 |
| **billable total** | **25 → 26** | **272,924 → 283,729** | **82,547 → 83,255** | **$1.53641** | **$1.56652** | ceiling $2.53192 |

**THE SET ON DISK IS WORTH $1.56652; THE PASS SPENT $1.79231. THE DIFFERENCE IS NOT AN
ERROR AND IT IS THE MOST IMPORTANT LINE IN THIS SECTION.** `mesh_fallback_siteless_code`
was captured twice — $0.22579 for a run that had to be discarded and $0.23437 for the one that
replaced it. See "The fixture that had to be captured twice" below. A total that
reported only the set's value would understate what the pass cost by exactly the
discarded run, and a total that reported only the spend would misdescribe what is on
disk.

The pre-run estimate was **$1.68795** — the 1.8.0 actual plus a MEASURED input
delta of +59 tokens per call (both templates rendered with identical arguments and
tokenised) and a STATED assumption of +15% output. The set came in
**-7.2% against that estimate** and **+2.0% against the set it replaced**. The ceiling
($2.53192) was never approached; no per-fixture stop was reached, and the
stop-decision function was shown to fire on every one of its branches before a cent was
spent (eleven controls, `gate_control.py`).

**THE OUTPUT-UPLIFT ASSUMPTION WAS WRONG BY AN ORDER OF MAGNITUDE, in the safe
direction.** Budgeted +15%, measured **+0.9%** across the billable set. Quoting an
interval into `patient_value` replaces a phrase like "Not in patient record" with a
dated one; it does not lengthen the response materially. **That is the measured cost of
the quote instruction**, and it is the number a future prompt edit of this shape should
budget from.

### The fixture that had to be captured twice

**`mesh_fallback_siteless_code`'s FIRST 1.9.0 capture RECORDED A DEGRADED RUN, and the
pipeline's own degradation record is what caught it.** Mid-capture the machine lost
network for a few seconds. Stage 2's `conditions` BM25 channel timed out:

```
retrieval_degraded    : 1          (was 0)
retrieval_channels_ok : 3 of 4
conditions            : {'status': 'failed', 'count': 0,
                         'error': 'ResponseHandlingException: [Errno 60] Operation timed out'}
```

One of four retrieval channels contributed nothing, so the RRF pool differed, so the
trial texts handed to the cross-encoder differed, so their digest differed. **The fixture
was internally consistent and replayed FATAL**: `no recorded cross-encoder pass for
query='Malignant neoplastic disease (disorder)' n_pairs=100
trial_texts_sha256=0ac59148…`. The same incident produced two `APITimeoutError` retries
in Stage 5.

**IT WAS DIAGNOSED RATHER THAN RE-RUN ON SUSPICION.** The replay was repeated three times
against the unrepaired fixture and asked for the SAME digest every time, which
establishes that live retrieval is deterministic right now and that the CAPTURE was the
outlier — not the replay. The digest it asked for is the one the 1.8.0 fixture recorded,
so the pool the degraded capture missed is the pool this collection actually returns.

The re-capture came back `retrieval_degraded=0`, four channels of four, the
cross-encoder digest back at `0ac59148…`, and **replays clean**.

**A DEGRADED RUN MUST NOT BE SHIPPED AS A CHARACTERIZATION BASELINE.** It would have
failed replay forever — the transient never reproduces — and every future comparison
would have been against a run that lost a quarter of its BM25 retrieval. This is item
11b's degradation record doing precisely the job it exists for: the fault was visible in
a recorded field rather than inferred from a mysterious diff.

**`mesh_fallback_siteless_code` GENUINELY MOVED 4 → 5 STAGE 5 CALLS, and that survives
the repair.** The repaired capture records `llm_classifier_retries=0` and
`llm_classifier_truncation_splits=0` with five `stop` finishes, so it is the input packer
splitting a request that previously fit — 1.9.0's +59-token system prompt is enough to
push it over. Exactly the effect the 1.8.0 section recorded for that pass, and it is the
whole of the billable set's call-count change.

### Verdict movement

**13 of 103 shared verdicts moved (12.6%), across 9 of the 11 billable fixtures. 0 verdicts gone, 0 new — every comparison below is
like-for-like.**

| fixture | trial | movement | own criteria text | time-window flip |
|---|---|---|---|---|
| `ablation_bm25_only` | NCT05949983 | not_eligible → eligible | same | no |
| `ablation_no_cross_encoder` | NCT06652672 | not_evaluable → not_eligible | same | yes |
| `ablation_vector_only` | NCT04333706 | not_eligible → eligible | same | no |
| `llm_classifier_parse_retry_constructed` | NCT06839001 | not_eligible → eligible | same | no |
| `mcode_genomic_variant` | NCT05949983 | eligible → not_evaluable | same | yes |
| `mesh_fallback_siteless_code` | NCT05443425 | eligible → not_eligible | **changed** | no |
| `mesh_fallback_siteless_code` | NCT06910761 | eligible → not_eligible | same | yes |
| `normal_2` | NCT07432399 | eligible → not_eligible | same | no |
| `normal_3` | NCT06839001 | not_eligible → eligible | same | no |
| `truncation_split` | NCT06839001 | not_eligible → eligible | same | no |
| `unknown_stage` | NCT03026140 | eligible → not_eligible | same | no |
| `unknown_stage` | NCT04185272 | eligible → not_eligible | same | yes |
| `unknown_stage` | NCT06940947 | eligible → not_eligible | same | no |

Direction is near-balanced — **5 `not_eligible → eligible`, 6 `eligible → not_eligible`, 2 involving `not_evaluable`** — which is what
sampling looks like and not what a systematic prompt effect looks like.

**12 of the 13 moved verdicts sit on BYTE-IDENTICAL criteria text.** The
collection pin did not move and no decode changed, so unlike 1.8.0 there is no
markdown-escape population to attribute movement to. The one exception is
`mesh_fallback_siteless_code / NCT05443425`, whose trial block is assembled into a
different request by the 4 → 5 packing change — a batching difference, not a text edit.

**THE ATTRIBUTION CAVEAT APPLIES TO EVERY ROW.** Fresh calls are not byte-deterministic,
temperature is the provider default and the seed is best-effort, and the system prompt
changed for every trial in every fixture. Old-vs-new differences therefore mix a real
input effect with sampling noise and cannot be separated by this comparison.

**Independence.** `llm_classifier_parse_retry_constructed` is built from `normal_3`'s
recordings, so `NCT06839001` moving in both is ONE observation reported twice; the third
occurrence, in `truncation_split`, is a different patient and is independent.

### The stop gate tripped, and what the decomposition shows

The gate was "more than 3 of the 11 billable fixtures move at verdict level → stop for a
human read". **9 did.**

**IT IS THE SAME MISSTATEMENT THE 1.8.0 SECTION ALREADY DIAGNOSED, INHERITED VERBATIM.**
That section closed by recording that the gate "was specified at fixture granularity
where a per-verdict escape-aware rate was correct" and that "a future gate should be
stated per verdict". Stated per verdict this run is
**12.6%**, against a last-measured noise floor of **11.9%** (under 1.5.0 at
k=2). That is at the floor, not above it.

**BUT THE GATE IS HONOURED RATHER THAN OVERRIDDEN, because the decomposition found
something a rate cannot show.** Only 4 of the 13 movements involve a status flip on a
time-window criterion — the only rows 1.9.0's mechanism can reach. Three of those four
are the mechanism working. One is the mechanism failing.

**WORKING — the strongest single piece of evidence in this capture:**

```
unknown_stage / NCT04185272
  criterion : Patient treated with immune-suppressors or long-course corticosteroids <12 months
  1.8.0     : not_evaluable
              24 HR tacrolimus 1 MG ... | status: active | start: 2026-07-31
  1.9.0     : violated
              24 HR tacrolimus 1 MG ... | status: active | start: 2026-07-31 (3 days before reference date)
```

The interval was quoted verbatim into `patient_value` and the window was then decided
against it. 1.8.0 had the same date and returned "not evaluable"; 1.9.0 returns a
correct determination. Two more (`NCT05949983` "Surgery in the last 3 weeks",
`NCT06910761` "…within 7 days prior to day 1") move `not_evaluable → not_violated` the
same way.

**FAILING — and this is the finding the human read exists to surface:**

```
ablation_no_cross_encoder / NCT06652672
  criterion : Previous colorectal cancer within the last 5 years
  1.8.0     : not_violated   <- correct
  1.9.0     : violated       <- WRONG
  patient_value, BYTE-IDENTICAL in both:
     Primary malignant neoplasm of colon (disorder) | resolved | 1997
     | not active; onset 28 years before reference date
```

**The model quoted the interval and classified against it.** 28 years is not within 5,
and the row it wrote down says so. This is not a case 1.9.0 failed to reach — it is a
case 1.9.0 reached and got wrong, and it moved the trial's verdict from `not_evaluable`
to `not_eligible`, which is a wrong rejection on a decades-old event: **the exact failure
class the blind rater run measured and that 1.9.0 was written to remove.**

So the honest reading of this set is that quote-before-judge **reduces but does not
eliminate** the class. One counterexample is not a rate, and n=13 movements over 103
verdicts cannot support one. What it does support is that the next measurement should be
a blind rater run over 1.9.0 against the same adjudicated cases as
`rater_blind_1_8_0_20260816/`, which is the harness built for this question; a
twelve-fixture characterization set is not.

### Verification

| check | result |
|---|---|
| replay before (re-measured against the backup) | 0/12 clean, exit 1 — 84 changed / 56 removed / 1 added |
| replay after the first pass | 11/12 clean — `mesh_fallback_siteless_code` FATAL, 1 miss |
| replay after the repair | **12/12 clean, exit 0, zero replay misses** |
| `tests/test_fixtures_harness_hardening.py` | **116 passed, 0 failed** |
| production `inferences.db` | mtime, size and all three row counts unmoved (1,106 / 12,862 / 26) |
| corpus | 1,000 bundles, sha256 `ea45a7a30832452b…` unchanged |
| repository | clean apart from this note |
| cost gate | 11 controls, every branch shown to fire, before any spend |

**Content sanity (i): the 1.9.0 mechanism is visible in the fixtures for the first
time.** Criterion rows whose `patient_value` quotes a stated interval, counted across
every recorded Stage 5 response:

| fixture | rows quoting an interval o→n | on a TIME-WINDOW criterion o→n |
|---|---|---|
| `ablation_bm25_only` | 22 → 20 | 3 → 2 |
| `ablation_no_cross_encoder` | 10 → 11 | 3 → 4 |
| `ablation_vector_only` | 11 → 15 | 0 → 1 |
| `llm_classifier_parse_retry_constructed` | 30 → 25 | 6 → 6 |
| `mcode_genomic_variant` | 21 → 29 | 3 → 4 |
| `mesh_fallback_siteless_code` | 13 → 28 | 2 → 5 |
| `no_candidates_pediatric_age` | 0 → 0 | 0 → 0 |
| `normal_1` | 8 → 25 | 2 → 5 |
| `normal_2` | 25 → 31 | 6 → 8 |
| `normal_3` | 30 → 25 | 6 → 6 |
| `truncation_split` | 51 → 52 | 8 → 12 |
| `unknown_stage` | 11 → 31 | 0 → 6 |
| **total** | **232 → 292** | **39 → 59** |

```
ablation_no_cross_encoder / NCT07612280   exclusion, not_violated
  criterion    : Subject who underwent radiotherapy within 2 weeks prior to start of study drug
  patient_value: External beam radiation therapy procedure (2001-08-29, 24 years before reference date)

ablation_vector_only / NCT07407920         exclusion, not_violated
  criterion    : Blood transfusion within 2 weeks before collection of blood for ctDNA testing
  patient_value: Intravenous blood transfusion of packed cells (procedure) (1982-03-02, 44 years before reference date)
```

**Content sanity (ii): the patient summary did NOT move, and that is the correct
result.** Elapsed-time phrases in the rendered system prompt total **1,416 across the
twelve fixtures, identical on both sides** (`unknown_stage` 99 → 99, `normal_1` 186 →
186, and so on). 1.8.0 put the intervals in the record; 1.9.0 changed only what the model
is told to do with one at the moment it decides a window. A count that had moved would
mean the summary generator had been disturbed, which nothing in `780219a` touches.

### The two sets, by sha256

**The old column is a DURABLE backup this time.** The 1.8.0 section records its own
pre-spend copy surviving only in an ephemeral session scratchpad. This pass copied the
set it was about to overwrite to
`09- Testing/Fixture Backups/1.8.0__pre-1.9.0-recapture/` before the first billable call,
and verified all twelve hashes there equal both the live set at that moment and the 1.8.0
section's "on disk now" column — so the two records are provably about the same bytes.
Every log, ledger and analysis script behind this section is beside it in
`09- Testing/Fixture Backups/1.9.0-recapture-evidence/`.

The hash is meaningful because the writer zeroes the gzip mtime
(`oncotriage/fixtures/capture.py:1406`), so sha256 is a function of content alone.
Reproduce either column with `shasum -a 256 *.json.gz` in the directory concerned.

| fixture | pre-recapture (1.8.0) | on disk now (1.9.0) |
|---|---|---|
| `ablation_bm25_only` | `8da2d66bb1a50e302899937755d08a61cebf15709598cfb25157618aa63efde7` | `92799a40da680897ceb2853d85d2296cfcf46c79d8b95bba61112c319c576b33` |
| `ablation_no_cross_encoder` | `78156d32dbe0e173737f97e29266d167a4062226feedbe342350c2b24b32a6b9` | `8ff4bd2e6b2619fe6c96a117e36ac3cb96b60bc33d9412128bfb9d761b922407` |
| `ablation_vector_only` | `4e7de88ec3451cf215ecf15336bae7185b8713b0f9f218e6c4739ee02cffabc2` | `e119635ad06ae16736562c3b53df18870451ecbbf745db5b99bc6f3d43800db2` |
| `llm_classifier_parse_retry_constructed` | `61dca2b033e0a7ba55ed9c039f74b9523377854f84e3a4cfbec1cb91539ac8e1` | `d0e06819b455562a53f654556b54f1b6ee003f2665b1643b4e7318dea0a9558c` |
| `mcode_genomic_variant` | `3cd66b88d46e0da3080b266f708ec34e3f2b58930c3b74585b17f863698f180c` | `03aa7183c0eec13f854b171f2fa18ee6137e15254006cc392bdfc0e8fb82fe47` |
| `mesh_fallback_siteless_code` | `b1bf34febaceb65682f3ddb9dc4eb0cd6562127e64280ab6dfb97657e5d5e009` | `133211eb1d12828d0a88c985b71c88a7b913829b4c5d9ce7bcc91764b2d82de2` |
| `no_candidates_pediatric_age` | `6331a482cb3e1e8706db3c878574fa51e257638f2d562448073d89db6cdb6b9a` | `b8b599d5e274e5da6323e64c43554f4d2d77f90f1573b993d9f7434f64a414fd` |
| `normal_1` | `d29f0fb754d8c2e5dcc857d8c499113771cadc034d2fa0a7744c586429f1c545` | `8645749c568929634e5355f82c27a23a19a590524f873a412416d299c83c617c` |
| `normal_2` | `e271c1589690f8019ea778a5d0529f4e940cf5837fe1d41b585148e7d930234c` | `58a632b132e1ce41eb0e1522946687057a70161589f46093cc0aa798da6755e7` |
| `normal_3` | `cda425fbe55990e10bb66f7b93267045378be916dad850d632fda411a784c8d3` | `f104365b093d342af0ad7e6c4a4a0a5a0b4de628ec7240fb1e026e44d313f2bc` |
| `truncation_split` | `7adb58d68f3c7d1d77a3c3c2cb37c54d5cf8ef6fe78d82e1039b9cfd7e4366de` | `932e067c6af01f70407c9a2c586f8bfe211329951cc81c8f1cf7cca63ed5ad76` |
| `unknown_stage` | `e690c43bc1bb9ee2fcea4da530f8cddc4d9388f2729dcafa480bae0b277d0f91` | `2461a3eee3589dc32a8c3568c25293aeb57e072be8e7183105508f34bf73b49b` |

---

## 2026-08-17 UTC — full recapture at PROMPT_VERSION 1.8.0

Twelve fixtures, captured `2026-08-17T02:00:09Z` to `2026-08-17T02:12:50Z`
(2026-08-16 evening local). Schema version **8** throughout — no field changed
shape, so no bump was needed.

**WHY IT WAS RUN.** Three committed changes altered what Stage 5 reads: the
escaped-entity decode (`843d8e2`), the registry markdown-escape strip
(`5772c7b`), and the 1.8.0 elapsed-time annotation on every dated value in the
patient summary (`895e83c`). The fixtures on disk were captured at **1.7.0**,
before all three. Replay against them was red in a known shape — 0/12 clean, every
field difference under `stage5.*` with stages 1–4 byte-identical, and five
fixtures carrying replay misses because Stage 5 responses are served positionally
by call cursor and 1.8.0's packing wanted more calls than the old recordings
held. Those were stale-recording artefacts, not defects.

### Environment

| | |
|---|---|
| prompt version | **1.7.0 → 1.8.0** (all twelve, both sides) |
| matching model | `gpt-5.6-terra` |
| collection pin | **`trial_criteria_20260810_145902`** (alias `trial_criteria` resolved to it; digest 14,324 points / 14,324 distinct NCT IDs / `479c37ec56a766fe…`) |
| temperature | `None` — the provider default; the model rejects the parameter |
| seed | `42`, best-effort |
| reasoning effort | `none` |
| max completion tokens | `32000` |
| pricing | `PRICING_CONFIG` dated 2026-08-04 — $2.00/M in, $12.00/M out |

### Spend, per fixture, against the pre-run estimate

Old and new both re-derived from the two fixture sets' recorded usage. The
`1.5x stop` column is the per-fixture ceiling the run was gated on.

| fixture | calls o→n | input o→n | output o→n | $ old | $ new | 1.5× stop |
|---|---|---|---|---|---|---|
| `ablation_bm25_only` | 1 → 2 | 11,422 → 21,385 | 6,217 → 6,547 | $0.09745 | $0.12133 | $0.177 |
| `ablation_no_cross_encoder` | 3 → 4 | 32,013 → 45,207 | 8,845 → 8,339 | $0.17017 | $0.19048 | $0.356 |
| `ablation_vector_only` | 2 → 2 | 20,387 → 22,032 | 7,391 → 7,545 | $0.12947 | $0.13460 | $0.200 |
| `llm_classifier_parse_retry_constructed` | 2 → 2 | 20,938 → 22,670 | 11,874 → 11,462 | $0.18436 | $0.18288 | not billable |
| `mcode_genomic_variant` | 1 → 1 | 9,142 → 9,939 | 4,217 → 4,772 | $0.06889 | $0.07714 | $0.106 |
| `mesh_fallback_siteless_code` | 3 → 4 | 32,324 → 44,629 | 11,410 → 10,730 | $0.20157 | $0.21802 | $0.341 |
| `no_candidates_pediatric_age` | 0 → 0 | 0 → 0 | 0 → 0 | $0.00000 | $0.00000 | n/a |
| `normal_1` | 2 → 3 | 21,205 → 34,171 | 5,665 → 5,999 | $0.11039 | $0.14033 | $0.206 |
| `normal_2` | 2 → 3 | 22,046 → 32,408 | 10,086 → 11,088 | $0.16512 | $0.19787 | $0.280 |
| `normal_3` | 1 → 1 | 10,469 → 11,335 | 5,937 → 5,731 | $0.09218 | $0.09144 | $0.141 |
| `truncation_split` | 3 → 3 | 28,016 → 30,608 | 12,009 → 11,813 | $0.20014 | $0.20297 | $0.309 |
| `unknown_stage` | 2 → 2 | 19,708 → 21,210 | 9,275 → 9,983 | $0.15072 | $0.16222 | $0.231 |
| **billable total** | **20 → 25** | **206,732 → 272,924** | **81,052 → 82,547** | **$1.38609** | **$1.53641** | ceiling $2.34852 |

**$1.53641, inside the $1.49272–$1.56568 estimate band, +10.8% on the set it
replaced. No per-fixture stop was reached and the ceiling was not approached.**
The estimate anticipated 13.1–18.9% record growth and a call count of 20 → 23–27;
the outturn was +10.8% and 25 calls.

**`llm_classifier_parse_retry_constructed` is excluded from the total.** It makes
no live call — it is its base fixture's recordings with one truncated response
spliced in front — so counting it would double-bill its base. That is the
harness's own discriminator in `stage5_cost_summary()`, and it is not
`fixture_kind`: four of the five `constructed` fixtures are real billed runs on a
derived *input*.

**Embeddings are not priced here.** The recordings store input text rather than
token counts, and the cohort probe's embeddings are recorded nowhere. The figure
is Stage 5 only.

**Where the extra calls came from.** Five of the eleven live fixtures gained a
call (`ablation_bm25_only` 1→2, `ablation_no_cross_encoder` 3→4,
`mesh_fallback_siteless_code` 3→4, `normal_1` 2→3, `normal_2` 2→3). That is
1.8.0's input packer splitting a request that previously fit in one, not a retry
and not a truncation. `truncation_split` still records exactly one truncation
split, which is what that fixture exists to hold.

### The constructed fixture's base was substituted, and it is a continuation

`choose_retry_base()` selects by **shape** — one recorded Stage 5 call — never by
name, because whether a run splits is a property of the model and that patient's
filtered trial set on the day. `normal_1`, the historical preference, came back
having made **three** calls and was rejected:

```
[retry base] normal_3 substituted for normal_1 — normal_1: recorded 3 Stage 5
call(s), not exactly one to splice a failing attempt in front of
```

`normal_3` stayed at exactly one call and took the base. **This is a continuation
rather than a new event**: the fixture set being replaced was already built from
`normal_3`, so the constructed fixture has not moved patient. A substitution is
correct behaviour, not a defect.

### Verdict movement

**12 of 95 shared verdicts moved (12.6%); 9 coincide with trials whose criteria
text genuinely changed (markdown escapes decoded); the residual 3 (3.2%) sat on
byte-identical criteria and are attributable to the changed patient summary or
sampling, indistinguishable — below the last measured noise floor (11.9%,
measured under 1.5.0 at k=2; the current floor will be measured from the upcoming
k=3 runs).**

Movement touched 7 of the 11 fixtures that produce verdicts. Direction was
near-balanced: 6 `not_eligible → eligible`, 5 `eligible → not_eligible`, 1
`eligible → not_evaluable`. No trial gained or lost a verdict outright — 0 gone,
0 new — so every comparison above is like-for-like. All nine of the
criteria-changed movers carried a markdown escape in their 1.7.0 text.

| fixture | trial | movement | own criteria changed |
|---|---|---|---|
| `ablation_bm25_only` | NCT05949983 | eligible → not_eligible | no |
| `ablation_no_cross_encoder` | NCT02172651 | eligible → not_eligible | yes |
| `ablation_no_cross_encoder` | NCT03026140 | eligible → not_eligible | yes |
| `ablation_no_cross_encoder` | NCT06652672 | eligible → not_evaluable | yes |
| `ablation_vector_only` | NCT07407920 | not_eligible → eligible | yes |
| `mesh_fallback_siteless_code` | NCT05443425 | not_eligible → eligible | yes |
| `normal_2` | NCT05773144 | not_eligible → eligible | yes |
| `normal_3` | NCT06839001 | eligible → not_eligible | no |
| `unknown_stage` | NCT03026140 | not_eligible → eligible | yes |
| `unknown_stage` | NCT04185272 | not_eligible → eligible | yes |
| `unknown_stage` | NCT06567782 | eligible → not_eligible | yes |
| `unknown_stage` | NCT06940947 | not_eligible → eligible | no |

**THE ATTRIBUTION CAVEAT APPLIES TO EVERY ROW.** Fresh calls are not
byte-deterministic and the prompts changed materially at 1.8.0, so old-vs-new
verdict differences mix real input effects with sampling noise. "Own criteria
changed" narrows *which* input moved for that trial; it does not isolate the
cause, because the patient summary changed for every fixture as well.

One reading that is **not** available: `NCT03026140` moved in opposite directions
in two fixtures, but those are two different patients, so that is expected and is
no evidence of instability.

**THE STOP-GATE TRIPPED AND THE REASON IS WORTH KEEPING.** The run's gate was
"more than 3 of the 11 billable fixtures move at verdict level → stop for a human
read", and 7 did. It was specified at **fixture granularity where a per-verdict
escape-aware rate was correct** — movement was expected under 1.8.0, and a
fixture-level count trips on exactly the outcome the change was made to produce.
The run stopped before this note was written; the human read the decomposition
above and accepted. A future gate should be stated per verdict and computed after
excluding trials whose input text changed, which measures the model wandering
rather than the fix working.

### The two sets, by sha256 (amended 2026-08-16)

**THE RECORD SHIPPED WITHOUT THE ONE FIELD THAT IDENTIFIES A FIXTURE FILE.**
Every number above is re-derived from fixture *contents*; none of it says which
BYTES those contents came out of, so a reader holding a `.json.gz` had no way to
ask "is this the set that produced the table above". Both sides are recorded
here rather than only the replaced one: a provenance note naming one side of a
substitution can be used to verify neither — the old set to say what was
replaced, the new set because it is what is on disk now and what a later replay
result is a statement about.

**The hash is meaningful because the writer zeroes the gzip mtime.**
`oncotriage/fixtures/capture.py:1406` writes `gzip.GzipFile(path, "wb",
compresslevel=9, mtime=0)`, so two captures of identical content produce
identical bytes and sha256 is a function of content alone. Without that, every
hash here would be a timestamp.

Reproduce either column with `shasum -a 256 *.json.gz` in the directory
concerned.

| fixture | pre-recapture (1.7.0) | on disk now (1.8.0) |
|---|---|---|
| `ablation_bm25_only` | `4f65291ca2ca457bd161a8b6ff324b340b349343112bab688112d104bb6013dd` | `8da2d66bb1a50e302899937755d08a61cebf15709598cfb25157618aa63efde7` |
| `ablation_no_cross_encoder` | `29f1f252ce26d635580c0a2e07a85a28a9df4a2cd7e1c3125515e870c4bf63f7` | `78156d32dbe0e173737f97e29266d167a4062226feedbe342350c2b24b32a6b9` |
| `ablation_vector_only` | `be432a07345161905fc2a57d23fd694d263983f54989f7153bc61fe1976bb168` | `4e7de88ec3451cf215ecf15336bae7185b8713b0f9f218e6c4739ee02cffabc2` |
| `llm_classifier_parse_retry_constructed` | `bed42d5b61584d8df3daffc5f319cef728727a5dd7bcefd469c57741b9a58324` | `61dca2b033e0a7ba55ed9c039f74b9523377854f84e3a4cfbec1cb91539ac8e1` |
| `mcode_genomic_variant` | `54b6f9604c0a35c55368ff61929ed799982fc73e012c6ac7aac6aee46cb610e2` | `3cd66b88d46e0da3080b266f708ec34e3f2b58930c3b74585b17f863698f180c` |
| `mesh_fallback_siteless_code` | `46e9a7a31fb65c11548990e90ec136d0c2c5fd5b4e9602709ae15509ee358309` | `b1bf34febaceb65682f3ddb9dc4eb0cd6562127e64280ab6dfb97657e5d5e009` |
| `no_candidates_pediatric_age` | `57f76c0a542354e125965eaadb621d6c1e8d0829d3fe538303846b57298c1ede` | `6331a482cb3e1e8706db3c878574fa51e257638f2d562448073d89db6cdb6b9a` |
| `normal_1` | `d3e12d7dfea11ed4520aab63e611439eeab7feb2cf1b6af496352463adfa12cc` | `d29f0fb754d8c2e5dcc857d8c499113771cadc034d2fa0a7744c586429f1c545` |
| `normal_2` | `ea5982efb7cb499a1bf8b23bca71e981cae2f6b58c94e7e5196cdcd7d94c0243` | `e271c1589690f8019ea778a5d0529f4e940cf5837fe1d41b585148e7d930234c` |
| `normal_3` | `e89d91f5e56028c62ac1d1afb75dda65bf18855f0fe35e8d9627c8c60d8f3927` | `cda425fbe55990e10bb66f7b93267045378be916dad850d632fda411a784c8d3` |
| `truncation_split` | `ca1103f69055ab8427e3a2a5f6732c173d895d6e9239578ac8ccec176f70c362` | `7adb58d68f3c7d1d77a3c3c2cb37c54d5cf8ef6fe78d82e1039b9cfd7e4366de` |
| `unknown_stage` | `15a70d72e958aae66287515b96cf501b47099bdc8436a2f8ef8482cb51812f1e` | `e690c43bc1bb9ee2fcea4da530f8cddc4d9388f2729dcafa480bae0b277d0f91` |

**PROVENANCE OF THE OLD COLUMN, STATED BECAUSE IT IS WEAKER THAN THE NEW ONE.**
The 1.7.0 files were overwritten in place by the recapture and are not in git.
The column above was hashed from a copy taken by the capture session before it
spent anything, which survived on disk at
`/private/tmp/claude-501/…/ad145b6e-…/scratchpad/fixtures_before/`. **That is a
session scratchpad and it is ephemeral** — the twelve hashes are recorded here
precisely because the directory holding them will not last.

It was checked to be the right set rather than assumed: all twelve carry
`deterministic_prefix.stage5.llm_classifier_prompt_version == "1.7.0"` against
the live set's `1.8.0`, `captured_at_utc` in the range
`2026-08-13T21:10:23Z`–`21:34:14Z` (the 1.7.0 capture, one recapture cycle
earlier), and all twelve differ byte-for-byte from the files on disk now — so
the two columns above are 24 distinct hashes and not a table that would look the
same if the backup had been a copy of the current set.

### Verification

| check | result |
|---|---|
| replay before | 0/12 clean, exit 1 — all differences under `stage5.*`, 5 fixtures miss-bearing |
| replay after | **12/12 clean, exit 0, zero replay misses** |
| the 5 formerly miss-bearing fixtures | all clean; they now exercise the behavioural diff instead of aborting on a missing recording |
| `tests/test_fixtures_harness_hardening.py` | 116 passed, 0 failed |
| production `inferences.db` | mtime and all three row counts unmoved (1,106 / 12,862 / 26) |
| corpus | 1,000 bundles, sha256 unchanged |
| repository | clean apart from this note |

**Content sanity, on the recaptured prompts.** Markdown escapes went from 5–48
per fixture to **0 in all twelve** — a bare comparator now stands where an escape
did:

```
NCT02172651  1.7.0: ... ≤ 2.5 × institutional ULN, or \<5x ULN if clearly attributable ...
             1.8.0: ... ≤ 2.5 × institutional ULN, or  <5x ULN if clearly attributable ...
NCT04662294  1.7.0: ... primordial cells in bone marrow is \> 5% ... and/or \> 0.01% ...
             1.8.0: ... primordial cells in bone marrow is  > 5% ... and/or  > 0.01% ...
NCT03026140  1.7.0: * dMMR cohorts 3+6: \>cT3 and/or N+
             1.8.0: * dMMR cohorts 3+6:  >cT3 and/or N+
```

Elapsed-time phrases in the patient summary rose 3–6× per fixture, stated beside
the date rather than replacing it:

```
- ECOG performance status: 1 (1971-10-12, 54 years before reference date)
- Malignant neoplasm of breast (disorder) | active | 1971 | onset 55 years before reference date | [neoplasm]
- Overlapping malignant neoplasm of colon (disorder) | resolved | 2005 | not active; onset 21 years before reference date | [neoplasm-unverified]
```

**THE ESCAPED-ENTITY DECODER IS UNEXERCISED BY THIS FIXTURE SET, and that is a
measurement rather than an assumption.** Counting HTML entity references
(`&gt;`, `&ge;`, `&#8805;` and the like) across every recorded Stage 5 request
gives **zero on both sides** — these trials carry none, so `843d8e2` has no
coverage here. The markdown-escape half of the same finding is covered heavily.
Closing it needs an entity-bearing trial in the set; recorded as a follow-up
rather than implied by the clean replay.
