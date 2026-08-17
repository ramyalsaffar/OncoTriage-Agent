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
