# MMR Redundancy Measurement -- Stage 4 kept pools

**MEASUREMENT ONLY. NO PIPELINE FILE WAS EDITED AND NO PIPELINE BEHAVIOUR CHANGED.** This report exists so the operator can rule on adopting Maximal Marginal Relevance in Stage 4 from evidence. The ruling is the operator's; nothing here implements it.

Generated: `2026-09-03T05:01:37Z`  
Script: `measure_mmr_redundancy.py`  
Module: `oncotriage/evaluation/mmr_redundancy.py` (schema 1)

## 1. What ran, and what it cost

| | |
|---|---|
| Stages driven | 1, 2, 3, 4 -- `node_query_expansion`, `node_hybrid_retrieval`, `node_cross_encoder_rerank`, `node_rule_based_filter` |
| Stage 5 | **never reached.** This module imports no evaluation node and calls no graph. |
| Retrieval arm | `full 4-channel RRF fusion (shipped)` |
| Qdrant collection | `trial_criteria_20260810_145902` (14324 points) |
| Qdrant endpoint | config.get_qdrant_url() |
| Embedding calls ATTEMPTED | **60** (`text-embedding-3-small`, one per patient, Stage 2 dense channel) |
| Embedding spend | **<= $0.000120** (upper bound over 6,000 tokens, priced through `utils.get_model_cost`) |
| ...announced before the run | <= $0.000600 over 300 patients -- the REQUESTED cohort, which the timing gate may truncate |
| AWS/Bedrock clients built | **0** (guard: armed) |
| Cross-encoder | `ncbi/MedCPT-Cross-Encoder` -- local, from cache |
| Wall clock | 28.5 min |

## 2. Cohort -- exactly reproducible

| | |
|---|---|
| Corpus | 1000 bundles |
| Requested size | 300 (this measurement's argument; `config.CAMPAIGN_COHORT_SIZE` is 500) |
| Drawn | 300 |
| Seed | `42` |
| Algorithm | `proportional largest-remainder allocation by cancer group (minimum 1 per non-empty group), then sha256(seed|stem) ascending rank within each group` |
| Digest | `b968fff45558b12c` (`sha256 over the newline-joined sorted stems`) |
| Group shares | breast=87, colorectal=121, hematologic=16, lung=5, prostate=71 |

**Timing gate.** Probed 10 patients at 30.8s each; projected 153.8 min for 300 against a 60 min budget. **GATE FIRED** -- fell back to the project's seeded 50-patient stability draw (`CAMPAIGN_STABILITY_SEED` / `CAMPAIGN_STABILITY_SAMPLE_SIZE`). Every number below is over that draw and over nothing else.

Patients measured: **50**. Bundles that failed to run: 0. Pre-slice/post-slice prefix control violations: **0** (a violation would mean the uncapped pool's first k are not the shipped kept-k, i.e. the two runs disagree). Kept trials absent from their own pool: **0** (impossible while the prefix control holds; dropped and counted rather than allowed to abort the analysis).

<details><summary>Drawn patient stems (click to expand) -- the draw is reproducible from the seed, the size and this list</summary>

```
America446_Schaden604_e851c96d-296c-c79c-ddad-b8f50e4f29b5
Bryant814_Howe413_774c12b9-2fef-52d2-d2dc-bb41428a003b
Byron202_Breitenberg711_8734d510-006a-e957-cac0-6eec54cc129a
Carmen818_Labadie908_f23853e9-08b7-c8b4-624a-c26be6bc5edf
Chrystal576_Chau65_Armstrong51_41f65a7e-8267-b731-7e57-8e40a9f35ec8
Cindie288_Teresita257_Upton904_ae14e123-1537-10d4-2bd5-a9c592b1b7eb
Courtney281_Hettinger594_05e77c13-e85b-1032-abb0-d5dc408c280e
Cris921_Maria750_Ledner144_ddca7cf4-e9ff-2651-511a-744f1aaa4dfb
Damion480_Russel238_46336a39-d7ba-e634-5d55-c1bcb402bf19
Danyell947_Hulda44_Effertz744_712c972d-92ef-4755-0d13-0917a3788dd2
Darin74_Dicki44_158f0397-ff12-a361-e960-fc658d705e47
Darwin703_Treutel973_befcc730-6e27-dd0b-0cd6-33acdf56cf5c
Denver542_Heller342_0bfbecd3-8a96-d899-020d-9998b7ac2dcf
Ellen406_Collier206_58ebba8b-1399-a495-b1da-68ed308109fe
Emory494_Runolfsdottir785_82890208-70bb-6fd3-8cd1-9f1b1e24ba1e
Faye439_Leontine92_Bartoletti50_acfb6a71-d15b-bd53-3309-23f4b5a50bad
Federico589_Orozco750_aa565a23-b37b-4fd4-213b-3189f33f5648
Federico589_Sánchez310_a6c523e9-ae2b-0892-b10d-8a74c31ba21e
Felicitas300_Karine844_Koss676_07bc6dc2-83c2-e61d-feaa-7a0f7a16588b
Floyd420_Senger904_b5078c5b-1663-71a0-d104-525f8472a58d
Gale827_Georgeanna785_Abbott774_a7338463-daf8-12cd-7605-581b3b3fdba7
Gustavo235_Stanton715_e1bf0a0f-9fea-0909-b7d3-d547786ddc97
Herb645_Bednar518_5d892f58-5d4c-bdfd-852d-0f819b838d79
Herschel574_Hintz995_9a112212-8b77-a46c-0589-6e4352d7ba73
Homero668_Calderón210_ad107b24-e2e5-3b33-eb09-12fec4d10832
Ismael683_Schneider199_5e22814e-da23-7855-5571-0280e23b026d
Jacobo456_Polanco94_b3fbe397-eb69-18c9-6e27-ff42643c3b4d
Jerónimo599_Archuleta517_da589acb-3d44-49dd-937d-ba5581f71a6b
Jewell855_Rice937_243a9d11-c052-b056-4018-383dea17d749
Jorge203_Méndez913_8aa54567-a43d-28c6-3f3f-7a5b86c2215e
Katie634_Susannah249_Bayer639_77574e0d-2d2d-92ca-a424-ad6f132d6d39
Keesha623_Michel472_Morissette863_16456dbf-f193-0e9d-1aa0-4513dd3a0321
Kevin729_Littel644_4d2cd6c1-11fe-3eda-15af-ce5932c5ec9a
Li461_Auer97_09cb2cf9-156c-5d4a-3411-6c785e688e1e
Lorena247_Lashonda618_Spinka232_e140bd5f-b39a-e02a-d903-ca0511834a1b
Lucas404_Figueroa648_5a1782fe-c9a0-0429-4273-c55a598dd651
Magdalena964_Oquendo599_90a53d9b-6a84-14c0-1d61-2ed3c53c44fd
Marco578_Roob72_38348ae8-a066-80e5-464a-0df31aa9636e
Myron933_Zboncak558_ff11d853-66af-c979-4175-9c0a584e115f
Pia232_Teodora360_Jenkins714_6b0bb630-a112-6932-e58d-bca280fc02e1
Renaldo199_Herzog843_b2c60952-42ca-e6f6-513e-1e6fc759578e
Robena997_Hayley136_Runte676_6e6d7573-a483-2d56-1fcb-02a86bd52b50
Rodger755_Jakubowski832_f3a44757-a531-f2fc-57d3-5279a67517a1
Salvatore257_Altenwerth646_a268ce21-9cc4-8996-6056-cc62d0cc7584
Scarlett814_Block661_5c71927d-1f81-d942-8171-1fde48716d6a
Soledad678_Rosario163_Nájera755_cdc6f7be-cf1c-1b25-df50-f19c4230ee8d
Sudie246_Novella551_Wuckert783_b932e380-92cb-5697-72ab-5ab148f0c3f6
Terra840_Dominique369_Daugherty69_6a454c20-9e0f-d7a2-aa81-6eaf2265bb1f
Victoria535_Esperanza675_Madrigal893_6cbd4960-5bef-a4fb-32dc-7a576600066c
Ángela136_Rael318_bfe36ed4-1e28-2d7e-ae12-e60249a1e8b5
```
</details>

## 3. The pools

`MAX_TRIALS_FOR_EVALUATION` = 15.

| | p5 | p25 | median | p75 | p95 |
|---|---|---|---|---|---|
| Post-filter pool size (pre-slice) | 7 | 16 | 19 | 22 | 28 |
| Kept (post-slice) | 7 | 15 | 15 | 15 | 15 |

**The cap binds for 38 of 50 patients (76.0%).** For the rest the post-filter pool is at or under 15 trials, so every trial that survives Stage 4 is already evaluated and **MMR has nothing to swap** -- any selector returns the whole pool. Those patients are excluded from the MMR denominator below and counted here instead.

MMR-eligible patients (pool strictly larger than kept): **38**.

### 3a. HOW MUCH INDEPENDENT EVIDENCE THIS IS -- read before section 5

| | |
|---|---|
| Patients | 50 |
| DISTINCT kept pools | **30** |
| DISTINCT post-filter pools | 36 |
| DISTINCT trials across every pool | 118 |

**WARNING: patients share pools, so the sample is narrower than the patient count.** 50 patients produce only 30 distinct kept pools and 118 distinct trials. Synthea patients within one cancer group carry near-identical condition lists, so Stage 1 builds the same expanded query, Stage 2 retrieves the same trials and Stage 3 hands back the same pool -- the degeneracy `oncotriage/evaluation/medcpt_calibration.py:pool_digest` documents for the same corpus.

The per-patient rate is still the one the rule reads, and that is deliberate: **production gates per patient**, so a pool that recurs ten times really is judged ten times and really does waste ten slots. What the per-patient rate must NOT be read as is ten independent observations of trial redundancy. Section 5 reports the per-distinct-pool figure beside it so both readings are available, and neither is presented as the other.

**This is the single largest limitation on the evidence below, and it is a property of a synthetic corpus rather than of MMR.** On a real cohort with genuinely varied condition lists the distinct-pool count would rise toward the patient count and these tables would carry correspondingly more weight.

## 4. Signal coverage -- measured before it is relied on

| Signal | Trials carrying it | Coverage |
|---|---|---|
| (a) eligibility criteria text | 118 / 118 | 100.0% |
| (b) registered interventions | 118 / 118 | 100.0% |

Similarity method: `TF-IDF cosine over eligibility criteria text; sublinear TF (1+ln tf), smoothed IDF ln((1+N)/(1+df))+1, L2-normalised rows; tokens from oncotriage.agent.text.tokenize_for_bm25; IDF fitted once over every distinct trial the run pooled`.

## 5. Redundancy inside the kept pools

Computed over each patient's **kept-k** pool -- the trials Stage 5 actually judges -- which is where a wasted slot is actually paid for.

| Cosine threshold | Pools with >=1 duplicate pair | Duplicate pairs (total) | Mean pairs/pool | Largest cluster | Reducible surplus (total slots) | Mean surplus/pool |
|---|---|---|---|---|---|---|
| 0.50 | 24 (48.0%) | 25 | 0.50 | 2 | 25 | 0.50 |
| 0.60 | 24 (48.0%) | 25 | 0.50 | 2 | 25 | 0.50 |
| 0.70 **<-- headline** | 24 (48.0%) | 25 | 0.50 | 2 | 25 | 0.50 |
| 0.80 | 3 (6.0%) | 4 | 0.08 | 2 | 4 | 0.08 |
| 0.90 | 3 (6.0%) | 4 | 0.08 | 2 | 4 | 0.08 |

Cluster-size histogram at the headline threshold (0.70): `{'2': 25}`

**The same figures over the pools MMR could actually act on.** The table above is over EVERY kept pool, which is the right population for *is Stage 5 judging redundant trials* -- a duplicate in a nine-trial pool is a wasted judgement whether or not the cap binds. It is the wrong population for *could MMR fix it*: a pool at or under the cap has nothing to promote, so MMR is powerless there however redundant it is. Cap-bound pools are also larger, and a larger pool has quadratically more pairs that could be near-duplicates.

| Population | Pools | With >=1 duplicate pair | Mean surplus/pool |
|---|---|---|---|
| All kept pools **(the rule reads this one)** | 50 | 24 (48.0%) | 0.50 |
| MMR-eligible pools only | 38 | 23 (60.5%) | 0.63 |
| DISTINCT kept pools (duplicates collapsed) | 30 | 14 (46.7%) | 0.50 |

The third row is section 3a's warning made numeric: it is the same finding with recurring pools counted once. It is NOT the rule's denominator -- production gates per patient -- but a reader who wants to know how many INDEPENDENT observations sit behind the first row should read it.

**Second signal.** Kept-pool pairs sharing at least one registered intervention: **85**; of those, **21** also reach the 0.70 text threshold. The two signals are independent -- one is the criteria prose, the other a metadata field -- so their agreement is what calibrates the text threshold from outside itself, and their disagreement is a finding rather than an error.

## 6. Offline MMR simulation

Objective, exactly as implemented:

```
MMR(d) = lambda * rel(d) - (1 - lambda) * max_{s in S} sim(d, s)

  rel  = min-max within the post-filter pool of rerank_score (the boosted
         score Stage 4 sorts on)
  sim  = the same TF-IDF cosine as section 5
  S    = the already-selected set; max over an empty S is 0.0
  ties = broken on nct_id ascending, so the result never depends on
         the order the pool was built in
```

Selection is re-run twice per patient per lambda and required to be identical. **Determinism failures: 0.**

**Control (lambda = 1.0, pure relevance).** Swapped out: 0. This must be 0 -- at lambda 1 the diversity term vanishes and MMR must reproduce the shipped selection exactly. A non-zero here would mean the relevance normalisation, not the diversity term, is moving trials, and every number below would be measuring that bug instead.

Denominator: the **38 MMR-eligible patients**, not all 50. A pool at or under the cap is returned whole by any selector; counting those as 'MMR changed nothing' would report the cap not binding as evidence about MMR.

| lambda | Patients changed | Swapped OUT | Swapped IN | OUT that are duplicates | **OUT that are DISTINCT** | Duplicate share |
|---|---|---|---|---|---|---|
| 0.3 | 36 (94.7%) | 103 | 103 | 55 | **48** | 53.4% |
| 0.5 | 36 (94.7%) | 71 | 71 | 46 | **25** | 64.8% |
| 0.7 | 28 (73.7%) | 35 | 35 | 29 | **6** | 82.9% |

**A swapped-out DISTINCT trial is a potential false drop**: a trial the shipped pipeline would have had judged, which MMR removes with no near-duplicate retained to stand in for it. It is the cost side of the trade and it is invisible in production -- it looks like a patient with fewer matches, and no counter, log line or stored row records it. A swap is only counted as a duplicate when the removed trial is at or above the threshold of a **retained** trial, or shares a registered intervention with one; the intervention signal can only exonerate a removal, never condemn one.

## 7. Findings -- the pre-registered rule, applied verbatim

> **Adopt only if redundancy is material AND swapped-out trials are almost entirely duplicates; otherwise reject.**

The two vague terms were fixed **before** this run, in `oncotriage/evaluation/mmr_redundancy.py`, and `apply_rule` reads nothing else:

- **material** = at least **25.0%** of kept pools contain a near-duplicate pair at cosine >= 0.70 **and** the mean reducible surplus is at least **1.0** trial slot per pool.
- **almost entirely duplicates** = at least **90.0%** of swapped-out trials are near-duplicates of a retained trial.

| lambda | Redundancy material? | Swaps almost all duplicates? | Distinct trials dropped | **Verdict** |
|---|---|---|---|---|
| 0.3 | NO (share 48.0% vs 25.0%; surplus 0.50 vs 1.0) | NO (53.4% vs 90.0%) | 48 | **REJECT** |
| 0.5 | NO (share 48.0% vs 25.0%; surplus 0.50 vs 1.0) | NO (64.8% vs 90.0%) | 25 | **REJECT** |
| 0.7 | NO (share 48.0% vs 25.0%; surplus 0.50 vs 1.0) | NO (82.9% vs 90.0%) | 6 | **REJECT** |

### Which way the numbers point: **REJECT, at every lambda tested.**

**The rule reads the ALL-KEPT-POOLS denominator** (section 5). Read instead over MMR-eligible pools only, the two material figures are 60.5% of pools and 0.63 slots per pool -- against bars of 25.0% and 1.0. That reading is published so a reader can apply the rule the other way from this one run; it is stated rather than substituted, because which denominator the rule uses is a choice and a choice made after seeing both is not a pre-registration.

The binding condition is stated rather than left to be inferred. Redundancy is **not** material: 48.0% of kept pools carry a near-duplicate pair (rule: 25.0%) and the mean reducible surplus is 0.50 slots per pool (rule: 1.0).

## 8. What this measurement cannot see

- **It measures text, not clinical equivalence.** Two trials of one drug at two doses share nearly all their criteria text and are not interchangeable; two trials of different drugs can share boilerplate. Cosine over criteria text is a proxy.
- **It cannot say a swapped-in trial is better.** That is a Stage 5 verdict and Stage 5 was not run. The measurement bounds the COST of MMR (distinct trials dropped); it does not measure the benefit.
- **Redundancy is a property of this index.** A re-scrape can move every number here.
- **The intervention signal under-counts.** Names are matched case-folded and exact -- no stemming, no synonyms, no substring match -- so 'Pembrolizumab' and 'Pembrolizumab 200mg' are two interventions. That is the safe direction for a corroborating signal.
- **The MeSH boost is in the relevance term**, because `rerank_score` is what Stage 4 sorts on. A simulation over `rerank_score_raw` would be over an order the pipeline does not use.

---

Re-run the analysis at other thresholds or lambdas for free, with no network and no spend, from the persisted pools:

```bash
python measure_mmr_redundancy.py --analyse-only <pools.json> --lambdas 0.2,0.4,0.6 --threshold 0.8
```
