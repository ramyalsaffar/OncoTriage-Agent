# OncoTriage Agent

**Production-grade oncology clinical trial matching pipeline.**  
Criterion-level eligibility evaluation | LangGraph orchestration | Hybrid retrieval | GPT-4o reasoning

![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=flat&logo=python&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.1-000000?style=flat&logo=langchain&logoColor=white)
![OpenAI](https://img.shields.io/badge/GPT--4o-2024--08--06-412991?style=flat&logo=openai&logoColor=white)
![Qdrant](https://img.shields.io/badge/Qdrant-1.16.2-DC244C?style=flat&logo=qdrant&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.135.1-009688?style=flat&logo=fastapi&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-1.55.0-FF4B4B?style=flat&logo=streamlit&logoColor=white)
![Airflow](https://img.shields.io/badge/Apache%20Airflow-3.1.7-017CEE?style=flat&logo=apacheairflow&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=white)
![License: CC BY-NC-ND 4.0](https://img.shields.io/badge/License-CC%20BY--NC--ND%204.0-lightgrey.svg)

---

## Overview

OncoTriage Agent is a production-ready AI pipeline that automatically identifies eligible clinical trials for cancer patients from their electronic health record (EHR) data. Given a patient FHIR R4 bundle, the system evaluates the patient against a continuously refreshed database of actively recruiting oncology trials from ClinicalTrials.gov and returns a ranked list of candidate trials with full criterion-level transparency.

The system is designed for real-world clinical deployment. Every component accounts for real EHR variability -- multi-coding systems, incomplete data, Cerner and Epic export patterns, mCODE-structured staging and genomic variants -- not just the synthetic data used for development and validation.

> **Status:** This project development is complete and a manuscript is in preparation. The repository is private pending publication.

---

## Results

All figures below are from the same 1,000-patient full batch run (Synthea, California demographics, age 18-80, cancer modules only, seed 42). The accuracy evaluation was conducted on a stratified 30-patient subsample (10 breast, 10 colon, 10 lung) drawn from the same batch.

### Production Run -- Match Outcomes (1,000 Patients)

| Metric | Value |
|---|---|
| Total patients processed | 1,000 |
| Any Match (Full + Partial) | **97.2%** (1,069 patients) |
| Full Match (match_score = 1.0) | 6.0% (66 patients) |
| Partial Match (match_score < 1.0) | 91.2% (1,003 patients) |
| No Match | 2.8% (31 patients) |
| Pipeline error rate | 0.5% (6 errors) |

### Accuracy Evaluation -- 4 Levels (30-Patient Stratified Sample, 4,128 Criteria)

| Level | Metric | Result |
|---|---|---|
| 1 | Criterion-level classification accuracy (raw) | **99.2%** (4,097 / 4,128) |
| 1 | Criterion-level classification accuracy (adjusted) | **99.8%** (4,121 / 4,128) |
| 2 | Trial-level verdict accuracy | **100.0%** (325 / 325) |
| 3 | Evidence faithfulness (zero hallucinated disqualifiers) | **100.0%** (262 / 262) |
| 4 | Absent-data violation rate (global invariant) | **0.0%** (0 / 262) |

**Adjusted accuracy** counts 24 of the 31 flagged cases as correct after deep analysis confirmed the `not_evaluable` classification was justified (e.g., parent-to-child terminology mismatch, outcome not confirmed by procedure record, count not documented). The remaining 2 true errors were wrong status vocabulary, both caught and corrected by the downstream status normalizer.

**Evidence Faithfulness and Absent-Data Rate are novel evaluation metrics.** No published clinical trial matching system reports them. Every disqualifying classification cites actual patient data. Zero cases of GPT-4o fabricating evidence. Zero cases of absent patient data being used as a disqualifier.

### Criterion-Level Status Distribution (30-Patient Sample)

| Criteria Type | not_evaluable | Positive status | Disqualifying status |
|---|---|---|---|
| Inclusion (2,002 total) | 66.4% | met: 28.6% | not_met: 4.9% |
| Exclusion (2,126 total) | 78.7% | not_violated: 13.5% | violated: 7.7% |

The high `not_evaluable` rate reflects the system's conservative design: absent patient data is never a disqualifier. This is by design, not a limitation.

### Reproducibility (99 Patients Re-Tested, 1,112 Trial Evaluations)

| Metric | Value |
|---|---|
| Identical classification rate | **94.1%** (1,046 / 1,112 trial evaluations) |
| Eligibility decision flips | 5.9% (66 / 1,112) |
| Identical match scores -- all trials | 81.3% |
| Identical match scores -- not_eligible trials | **100.0%** (scores hardcoded to 0.0) |
| Identical match scores -- eligible trials | 71.8% |
| Avg score spread (all trials) | 0.0341 |
| Avg score spread (eligible trials only) | 0.0442 |

Not-eligible trials achieve 100% score reproducibility by design -- scores are hardcoded to 0.0 by the pipeline. Variance in eligible trials reflects inherent LLM non-determinism at temperature=0 on borderline criteria.

### Cost Analysis (1,100 Inferences)

| Metric | Value |
|---|---|
| Total cost | $111.79 |
| Average cost per patient | $0.1022 |
| Median cost per patient | $0.1151 |
| Projected cost per 1,000 patients | $102.18 |
| GPT-4o output tokens (80.7% of cost) | $90.23 |
| GPT-4o input tokens (19.3% of cost) | $21.56 |

Output tokens dominate cost because GPT-4o output tokens are priced 4x higher than input tokens and carry the full criterion-level eligibility assessments for all evaluated trials.

### Latency and Throughput (1,100 Inferences)

| Metric | Value |
|---|---|
| Median end-to-end latency | 281.6s (~4.7 minutes) |
| 95th percentile latency | 361.3s |
| Max latency | 1,512.9s |
| GPT-4o evaluation latency (median) | 74.2s |
| Throughput | 13 patients/hour |

### Comparison with Published Systems

| System | Criterion Accuracy | Verdict Accuracy | Evidence Faithfulness | Absent-Data Rate |
|---|---|---|---|---|
| **OncoTriage (this work)** | **99.2% / 99.8%** | **100.0%** | **100.0%** | **0.0%** |
| TrialGPT (NIH, Nature Comms 2024) | 87.3% | Not reported | Not reported | Not reported |
| TrialMatchAI (arXiv May 2025) | >90% | Not reported | Not reported | Not reported |
| PRISM / OncoLLM (npj Dig Med 2024) | 75.3% | Not reported | Not reported | Not reported |
| Human experts (TrialGPT study) | 88.7--90.0% | Not reported | Not reported | Not reported |

**Caveats (in the interest of full transparency):**

1. OncoTriage accuracy is self-evaluated by Claude on Synthea-generated data, not physician-annotated on real EHR data.
2. TrialGPT's 87.3% was evaluated by 3 physicians against ground truth -- a higher-rigor evaluation standard.
3. Evidence Faithfulness and Absent-Data Rate are novel metrics with no published baseline for comparison.
4. Synthea patients have structurally cleaner data than real EHRs, which may inflate accuracy relative to real-world performance.
5. The reproducibility figure (94.1%) is from 99 re-tested patients across 1,112 trial evaluations from the same 1,000-patient batch run.

---

## Technology Stack

| Component | Technology |
|---|---|
| Orchestration | LangGraph (StateGraph with conditional edges, cyclic retry, error handler) |
| LLM -- eligibility evaluation | OpenAI GPT-4o (gpt-4o-2024-08-06) |
| Embeddings | OpenAI text-embedding-3-small (1,536 dimensions) |
| Cross-encoder reranker | ncbi/MedCPT-Cross-Encoder (via HuggingFace Transformers) |
| Sparse retrieval | Qdrant/bm25 via FastEmbed SparseTextEmbedding |
| Vector database | Qdrant (cloud, zero-downtime aliased collections) |
| API layer | FastAPI + Uvicorn + SlowAPI rate limiting |
| Workflow scheduling | Apache Airflow 3.1.7 (TaskFlow API, weekly trial refresh) |
| Monitoring dashboard | Streamlit + Plotly (9-tab observability suite) |
| Persistence | SQLite (inferences, trial matches, drift metrics) |
| Drift detection | scipy.stats (KS test, PSI, z-score) |
| Retry and resilience | tenacity (exponential backoff) |
| Clinical ontologies | MeSH 2026, ICD-10-CM 2024, SNOMED CT, LOINC, UMLS 2025AB |
| Input standard | FHIR R4 Bundle |
| Deployment | Docker (Linux) + macOS, auto-detected at runtime |

---

## Clinical Ontology Infrastructure

The pipeline integrates five clinical ontology systems, each serving a distinct role:

- **MeSH 2026** -- cancer site hierarchy for disease query expansion and trial-patient site matching
- **ICD-10-CM 2024** -- full 2024 release for real EHR primary coding path (O(1) lookup)
- **SNOMED CT** -- curated primary cancer code detection (53 codes + mCODE STU4 roots)
- **LOINC** -- oncology lab observation filtering, mCODE TNM stage and genomic variant routing
- **UMLS Metathesaurus 2025AB** -- SNOMED-to-MeSH and ICD-10-to-MeSH crosswalks via CUI bridge

---

## Real-World EHR Compatibility

The pipeline is built for real EHR deployment, not just synthetic data. Design accommodations include:

- Multi-coding system resolution (SNOMED, ICD-10-CM, RxNorm, LOINC, CPT, HCPCS) with both canonical URI and OID-based URI forms -- covering Epic, Cerner, and other major EHR vendors
- MedicationStatement support for Cerner/Oracle Health
- Active and historical medication retention for washout period and prior treatment criteria
- Free-text clinical diagnosis uncertainty detection in condition display names (16 confirmed qualifiers)
- mCODE STU4 structured staging (TNM stage group Observations, LOINC 21908-9/21902-2/21914-7)
- mCODE genomic variant Observations (LOINC 69548-6) with HGVS notation parsing
- All 8 FHIR observation value types
- SI-to-US lab unit normalization (7 lab categories) at summary time -- raw FHIR never modified

---

## Observability and Evaluation Infrastructure

- **44-column SQLite inference log** -- full funnel counts, stage latencies, token usage, cost, reproducibility hash, ablation flags
- **9-tab Streamlit monitoring dashboard** -- Overview, Match Quality, Patient Explorer, Trial Explorer, Patient Demographics, Performance, Cost and Tokens, Drift Detection, Reproducibility
- **Statistical drift detection** -- KS test, PSI, and z-score monitoring across data, retrieval, and performance metrics
- **Systematic ablation study framework** -- 7 configurations, stratified sampling across 16 cancer groups, bootstrapped 95% CIs, Wilcoxon signed-rank tests with effect sizes
- **Airflow-orchestrated weekly trial refresh** -- zero-downtime atomic alias swap, verify_index health check, crash-safe checkpoint

---

## Limitations

1. Validated on Synthea-generated synthetic data. Real EHR validation (Epic/Cerner export) is a planned next step.
2. Accuracy evaluation is self-evaluated by Claude, not physician-annotated against ground truth.
3. Synthea data is structurally cleaner than real EHR data, which may inflate accuracy relative to real-world performance.
4. ECOG performance status and organ function lab thresholds are evaluated by the LLM rather than deterministic parsing -- a known roadmap item.

---

## Publication

A manuscript describing the methodology, evaluation framework, and results is in preparation.

---

*Stack: Python | LangGraph | GPT-4o | Qdrant | MedCPT | FastAPI | Streamlit | Airflow*
