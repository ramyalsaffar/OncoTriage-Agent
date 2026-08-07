# Scrape Clinical Trials Data from the clinicaltrials.gov API
#############################################################

"""Trial RAG indexer: trial-level dense embeddings + per-field BM25 sparse.

Scrapes trials from ClinicalTrials.gov, creates trial-level embeddings, and
prepares data for hybrid BM25 + vector retrieval on Qdrant.

Moved out of ``11- RAG Trial Indexer.py`` by item 20c, pass 3a. That file is now
a thin entry point holding only its argparse ``__main__`` block: nothing in the
repository chains it, so it needs no re-export shim and its exec bootstrap is
gone.

WHAT CHANGED IN THE MOVE
------------------------
1. THE BM25 SPARSE MODEL IS NO LONGER BUILT AT MODULE LEVEL, and it is no longer
   built HERE at all. File 11 line 53 ran

       print("Loading BM25 sparse embedding model (FastEmbed)...")
       _bm25_sparse_model = SparseTextEmbedding(model_name="Qdrant/bm25")

   at exec() time. Two things were wrong with that. It is a model load at
   import, which the package forbids and ``tests/test_package_invariants.py`` section 2
   traps. And it was the SECOND independent construction of the same model: the
   agent built its own in ``oncotriage/agent/deps.py`` for the query side of the
   same job. BM25 sparse vectors are token-ID vectors over the model's
   vocabulary, so if the two ever named different models the index would be
   built with one and queried with another, Qdrant would go on returning
   results, nothing would raise, and only retrieval quality would fall. There is
   now one construction site — ``oncotriage/embedding.py`` — reached by both
   sides, and File 47 section 2f asserts the count is exactly one.

2. THE CLIENTS COME FROM ``oncotriage.config``, not from the shared exec
   namespace. ``config.get_qdrant_client()`` and ``config.get_openai_client()``
   build once and cache, and they are the same objects ``03- Config.py`` binds as
   the eager ``qdrant_client`` / ``openai_client`` names, so a chain caller and
   this module talk to one client.

   NOT ``oncotriage.agent.deps``. The deps seam exists so a test harness can
   redirect what the AGENT reaches; an index build must not be redirected by a
   stub installed for an agent test, and ``retrieval`` importing ``agent`` would
   be the wrong direction besides. ``oncotriage.retrieval.index_validator`` DOES
   use deps, because it validates the agent's retrieval path and needs the
   MedCPT accessors that only exist there.

3. Everything else it read out of the shared namespace — ``trial_dict``,
   ``EMBEDDING_MODEL``, ``EMBEDDING_DIM``, ``Project_Name``, ``checkpoint_path``,
   ``data_trial_path`` — is read off ``oncotriage.config`` / ``oncotriage.paths``
   instead. The two paths resolve on first read and cache, so importing this
   module resolves no directory.

No other line changed. ``ast.unparse`` equivalence against ``git show HEAD:`` is
asserted for all fifteen top-level definitions and every difference is one of
the three above.
"""

import hashlib
import json
import logging
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import requests
from openai import APIConnectionError, InternalServerError, RateLimitError
from qdrant_client.models import (
    CreateAlias,
    CreateAliasOperation,
    DeleteAlias,
    DeleteAliasOperation,
    Distance,
    Modifier,
    PayloadSchemaType,
    PointStruct,
    SparseIndexParams,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from tqdm import tqdm

from oncotriage import paths
from oncotriage.config import (
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    Project_Name,
    get_openai_client,
    get_qdrant_client,
    trial_dict,
)
from oncotriage.embedding import get_bm25_sparse_model
from oncotriage.extraction.histology import (
    enrich_histology_tags,
    get_histology_extraction_stats,
)
from oncotriage.extraction.stage import (
    enrich_structured_eligibility,
    get_stage_extraction_stats,
)
from oncotriage.utils import CaffeinateSession, qdrant_retry
from oncotriage.observability import console


#------------------------------------------------------------------------------


# ===========================================================================
# INDEX-TIME AGE-PARSE DEGRADATION RECORD (item 11a)
# ===========================================================================
#
# The index-time MIRROR of oncotriage/agent/filtering.py's AGE_PARSE_FAILURES,
# and `Exception and Fallback Audit.md` lists it as its own row: "a trial with
# an unparseable minimum age is kept rather than skipped. Same fix, an
# age_parse_failed counter in the scrape summary."
#
# WHAT THE HANDLER BELOW ACTUALLY DOES, which is worth stating because it is not
# the same recovery as Stage 4's. The scrape SKIPS trials whose minimum age is
# above 18 — an adult-oncology corpus does not want paediatric-only studies. An
# unparseable minimumAge therefore means the trial is KEPT, i.e. indexed,
# because the skip test could not be evaluated. Direction is the same as Stage
# 4's (keep, never drop) and so is the reasoning for counting rather than
# raising: the string comes from ClinicalTrials.gov, so there is no operator
# action that would fix it, and aborting a 292k-trial scrape over one field
# would be a worse outcome than indexing one extra trial.
#
# SEPARATE from the Stage 4 counter, and deliberately not shared. These are two
# different populations measured at two different times — every registered trial
# at scrape time, versus the ~75 retrieved for one patient at query time — and a
# single counter would silently sum them into a number that means neither.
# A module-level Counter either way, following PARTIAL_DATE_DEGRADATIONS.
INDEX_AGE_PARSE_FAILURES = Counter()

# Same cap and the same reason as filtering._AGE_KEY_MAX_LEN: keep enough of the
# raw value to see its shape, not enough for a pathological field to grow the
# key without bound.
_INDEX_AGE_KEY_MAX_LEN = 40


#------------------------------------------------------------------------------


def create_payload_indexes(collection_name: str):
    """
    Create payload indexes on the staging collection after indexing.
    Required for scroll/filter operations (e.g., fetching trials by nct_id).
    Must be called after index_trials() and before alias swap.

    Uses retry logic with exponential backoff and extended timeout (120s)
    because Qdrant Cloud may timeout on large collections (292K+ points).
    """
    def _create_index_with_retry(field_name, field_schema, max_retries=5):
        for attempt in range(1, max_retries + 1):
            try:
                get_qdrant_client().create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=field_schema,
                )
                console.out(f"✓ Payload index created: {field_name} on '{collection_name}'")
                return
            except Exception as e:
                wait_time = 2 ** attempt
                console.out(f"  Attempt {attempt}/{max_retries} failed for {field_name}: {e}")
                if attempt < max_retries:
                    console.out(f"  Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    console.out(f"  FAILED to create index {field_name} after {max_retries} attempts")
                    raise

    _create_index_with_retry("nct_id", PayloadSchemaType.KEYWORD)    


def scrape_clinicaltrials_gov(condition=None, status=None, study_type=None, age=None, max_trials=None) -> List[Dict]:
    """
    Scrape trials from ClinicalTrials.gov API v2.
    Saves incrementally after every page so progress survives interruption.
    On resume, loads checkpoint and continues from the last saved page_token.

    Args:
        condition:  MeSH term for disease (default: neoplasms = all cancers)
        status:     Trial recruitment status (RECRUITING only)
        study_type: INTERVENTIONAL filter applied post-fetch per study
        age:        Age group (defined in config, not passed to API directly)
        max_trials: Maximum number of trials to fetch

    Returns:
        List of trial dictionaries with full metadata
    """

    condition   = condition   or trial_dict["condition"]
    status      = status      or trial_dict["status"]
    study_type  = study_type  or trial_dict["study_type"]
    age         = age         or trial_dict["age"]
    max_trials  = max_trials  or trial_dict["max_trials"]
    
    base_url  = "https://clinicaltrials.gov/api/v2/studies"
    page_size = min(100, max_trials)

    # ------------------------------------------------------------------
    # Checkpoint: persists trials list + next page_token after every page.
    # Allows exact resume from where scraping was interrupted.
    # ------------------------------------------------------------------
    checkpoint_file = Path(paths.checkpoint_path) / "scrape_checkpoint.json"

    if checkpoint_file.exists():
        with open(checkpoint_file, "r") as f:
            ckpt = json.load(f)
        trials     = ckpt.get("trials", [])
        page_token = ckpt.get("page_token", None)
        console.out(f"Resuming scrape from checkpoint: {len(trials)} trials already scraped.")
    else:
        trials     = []
        page_token = None
        console.out("Starting fresh scrape from ClinicalTrials.gov...")

    console.out(f"Filters: condition={condition}, status={status}, type={study_type}, max={max_trials}")

    scrape_complete = False
    scrape_start    = time.time()
    page_num        = 0

    with tqdm(
        total=max_trials,
        initial=len(trials),
        desc="Scraping",
        unit="trial",
    ) as pbar:

        while len(trials) < max_trials:
            params = {
                "query.cond":           condition,
                "filter.overallStatus": status,
                "pageSize":             page_size,
                "format":               "json"
            }
            if page_token:
                params["pageToken"] = page_token

            try:
                response = requests.get(base_url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()

                studies = data.get("studies", [])
                if not studies:
                    console.out("No more trials found.")
                    scrape_complete = True
                    break

                page_num += 1
                added = 0

                for study in studies:
                    protocol = study.get("protocolSection", {})

                    design = protocol.get("designModule", {})
                    if design.get("studyType") != "INTERVENTIONAL":
                        continue

                    eligibility = protocol.get("eligibilityModule", {})
                    min_age_str = eligibility.get("minimumAge", "")
                    if min_age_str and "year" in min_age_str.lower():
                        try:
                            min_age = int(re.findall(r'\d+', min_age_str)[0])
                            if min_age > 18:
                                continue
                        except (IndexError, ValueError) as _age_exc:
                            # RECOVERY UNCHANGED — the trial is indexed, because
                            # the "skip paediatric-only" test could not be
                            # evaluated. RECORDED now: without this the scrape
                            # could not say whether zero such trials existed or
                            # whether the check had quietly stopped running.
                            _age_text = min_age_str
                            if len(_age_text) > _INDEX_AGE_KEY_MAX_LEN:
                                _age_text = _age_text[:_INDEX_AGE_KEY_MAX_LEN] + "..."
                            INDEX_AGE_PARSE_FAILURES[
                                f"minimumAge:{type(_age_exc).__name__}:{_age_text}"
                            ] += 1

                    # A trial tagged with a mutually exclusive pair PERMITS
                    # either histology, it does not require both. Both tags are
                    # indexed and the query-time filter intersects before it
                    # looks for an exclusive pair, so each population still
                    # matches. Counted as exclusive_pair_kept, reported below.
                    trial = parse_trial_metadata(protocol)

                    # Post-scrape oncology validation:
                    # Drop trials whose registered conditions contain no
                    # cancer/neoplasm signal. ClinicalTrials.gov returns
                    # non-cancer trials under "neoplasms" query when the
                    # word appears only in the title or eligibility text.
                    _ONCOLOGY_KEYWORDS = frozenset({
                        "neoplasm", "cancer", "carcinoma", "sarcoma",
                        "lymphoma", "leukemia", "melanoma", "glioma",
                        "myeloma", "tumor", "tumour", "malignant",
                        "malignancy", "oncology", "metastatic", "metastasis",
                    })
                    trial_conditions_lower = " ".join(
                        trial.get("conditions") or []
                    ).lower()
                    trial_keywords_lower = " ".join(
                        trial.get("keywords") or []
                    ).lower()
                    combined = trial_conditions_lower + " " + trial_keywords_lower
                    
                    if not any(kw in combined for kw in _ONCOLOGY_KEYWORDS):
                        continue
                    
                    trials.append(trial)
                    added += 1

                    if len(trials) >= max_trials:
                        break

                # Update page_token BEFORE saving checkpoint
                page_token = data.get("nextPageToken")

                # Save checkpoint after every successful page
                with open(checkpoint_file, "w") as f:
                    json.dump({"trials": trials, "page_token": page_token}, f)

                pbar.update(added)
                pbar.set_postfix({"saved": len(trials), "page": page_num})

                if not page_token:
                    scrape_complete = True
                    break

                if len(trials) >= max_trials:
                    scrape_complete = True
                    break

                time.sleep(1)

            except requests.exceptions.RequestException as e:
                console.out(f"Network error fetching trials: {e}")
                console.out("Progress saved to checkpoint — re-run to resume.")
                break

    elapsed = time.time() - scrape_start
    hrs, rem = divmod(int(elapsed), 3600)
    mins, secs = divmod(rem, 60)
    elapsed_str = f"{hrs:02d}:{mins:02d}:{secs:02d}" if hrs > 0 else f"{mins:02d}:{secs:02d}"

    console.out(f"\nTotal trials scraped: {len(trials)}  |  Scrape time: {elapsed_str}")

    _histology_stats = get_histology_extraction_stats()
    if _histology_stats.get("exclusive_pair_kept"):
        console.out(f"Trials carrying a mutually exclusive histology pair: "
              f"{_histology_stats['exclusive_pair_kept']}  (permit either "
              f"histology — both tags indexed, both populations match)")
    if any(_histology_stats.values()):
        console.out(f"Histology negation/exclusive-pair counters: {_histology_stats}")

    _stage_stats = get_stage_extraction_stats()
    if any(_stage_stats.values()):
        console.out(f"Stage negation/span/exclusion-bound counters: {_stage_stats}")

    # The age-parse counter the exception audit asked for, in the scrape summary
    # where it asked for it. Printed only when non-zero, so a clean scrape's
    # output is unchanged.
    if INDEX_AGE_PARSE_FAILURES:
        _age_total = sum(INDEX_AGE_PARSE_FAILURES.values())
        console.out(f"minimumAge UNPARSEABLE on {_age_total} trial(s) — the "
              f"paediatric-only skip did not run for them and they ARE indexed: "
              f"{dict(INDEX_AGE_PARSE_FAILURES)}")

    if scrape_complete and checkpoint_file.exists():
        checkpoint_file.unlink()
        console.out("Scrape checkpoint cleared.")

    return trials

def parse_trial_metadata(protocol: Dict) -> Dict:
    """
    Extract relevant trial metadata from protocol section
    
    Args:
        protocol: protocolSection from ClinicalTrials.gov API
        
    Returns:
        Dictionary with trial metadata
    """
    identification = protocol.get("identificationModule", {})
    description    = protocol.get("descriptionModule",    {})
    design         = protocol.get("designModule",         {})
    eligibility    = protocol.get("eligibilityModule",    {})
    contacts       = protocol.get("contactsLocationsModule", {})

    # --- NEW: conditionsModule -------------------------------------------
    # conditions: MeSH terms (e.g. "Breast Neoplasms", "Triple Negative Breast Neoplasms")
    # keywords:   free-text tags (e.g. "HER2-positive", "TNBC", "immunotherapy")
    # Both are lists of strings; missing, None, or empty → stored as [].
    #
    # Defensive guards (production API can return unexpected shapes):
    #   - `or {}` on module: protocol.get(key, {}) returns None when the key
    #     exists with an explicit null value; `or {}` converts None → {}
    #   - `or []` on list fields: same pattern — key present but value is None
    #   - `isinstance(c, str)` guard: items are normally strings, but malformed
    #     responses have been seen with ints, dicts, or None in these lists
    cond_module = protocol.get("conditionsModule") or {}
    conditions  = [
        c.strip() for c in (cond_module.get("conditions") or [])
        if isinstance(c, str) and c.strip()
    ]
    keywords    = [
        k.strip() for k in (cond_module.get("keywords") or [])
        if isinstance(k, str) and k.strip()
    ]

    # --- NEW: armsInterventionsModule ------------------------------------
    # interventions[].name: drug/device/procedure name (e.g. "Pembrolizumab")
    # We store names only — descriptions are too verbose for BM25 text.
    # Deduplicate: same drug may appear in multiple arms.
    #
    # Defensive guards:
    #   - `or {}` on module: same None-value protection as conditionsModule
    #   - `or []` on interventions list: key present but value is None
    #   - `isinstance(iv, dict)` guard: items must be dicts to call .get() on them;
    #     malformed responses can contain None, strings, or ints in this list
    arms_module   = protocol.get("armsInterventionsModule") or {}
    seen_names    = set()
    interventions = []
    for iv in (arms_module.get("interventions") or []):
        if not isinstance(iv, dict):
            continue
        name = (iv.get("name") or "").strip()
        if name and name not in seen_names:
            interventions.append(name)
            seen_names.add(name)

    eligibility_text               = eligibility.get("eligibilityCriteria", "")
    inclusion_text, exclusion_text = split_inclusion_exclusion(eligibility_text)

    trial = {
        "nct_id":               identification.get("nctId", ""),
        "title":                identification.get("officialTitle",
                                    identification.get("briefTitle", "")),
        "brief_summary":        description.get("briefSummary",       ""),
        "detailed_description": description.get("detailedDescription", ""),
        "phase":                design.get("phases", ["N/A"])[0]
                                    if design.get("phases") else "N/A",
        "study_type":           design.get("studyType", ""),
        "enrollment":           design.get("enrollmentInfo", {}).get("count", 0),
        "conditions":           conditions,    # NEW — MeSH disease terms
        "keywords":             keywords,      # NEW — study keywords
        "interventions":        interventions, # NEW — deduplicated intervention names
        "eligibility": {
            "criteria_text":      eligibility_text,
            "inclusion_criteria": inclusion_text,
            "exclusion_criteria": exclusion_text,
            "min_age":            eligibility.get("minimumAge",        ""),
            "max_age":            eligibility.get("maximumAge",        ""),
            "sex":                eligibility.get("sex",               "ALL"),
            "healthy_volunteers": eligibility.get("healthyVolunteers", False)
        },
        "locations":        extract_locations(contacts.get("locations", [])),
        "overall_contact":  (contacts.get("overallOfficials", [{}])[0]
                             if contacts.get("overallOfficials") else {}),
        "last_update":      (protocol.get("statusModule", {})
                             .get("lastUpdatePostDateStruct", {})
                             .get("date", ""))
    }

    # --- NEW: Structured eligibility (stage requirements) -----------------
    # Deterministic NER extraction of cancer stage from title + inclusion.
    # Stored in trial["structured_eligibility"], flows into Qdrant via
    # full_trial_json (line 614) with zero schema changes.
    enrich_structured_eligibility(trial)
    enrich_histology_tags(trial)

    return trial


def split_inclusion_exclusion(criteria_text: str) -> tuple:
    """Split eligibility criteria into inclusion and exclusion sections"""
    
    text_lower = criteria_text.lower()
    
    inclusion_markers = ["inclusion criteria:", "inclusion:", "patients must have"]
    exclusion_markers = ["exclusion criteria:", "exclusion:", "patients must not"]
    
    inclusion_start = -1
    for marker in inclusion_markers:
        pos = text_lower.find(marker)
        if pos != -1:
            if inclusion_start == -1 or pos < inclusion_start:
                inclusion_start = pos

    exclusion_start = -1
    for marker in exclusion_markers:
        pos = text_lower.find(marker)
        if pos != -1:
            if exclusion_start == -1 or pos < exclusion_start:
                exclusion_start = pos
    
    if inclusion_start != -1 and exclusion_start != -1:
        if inclusion_start < exclusion_start:
            inclusion_text = criteria_text[inclusion_start:exclusion_start].strip()
            exclusion_text = criteria_text[exclusion_start:].strip()
        else:
            exclusion_text = criteria_text[exclusion_start:inclusion_start].strip()
            inclusion_text = criteria_text[inclusion_start:].strip()
    elif inclusion_start != -1:
        inclusion_text = criteria_text[inclusion_start:].strip()
        exclusion_text = ""
    elif exclusion_start != -1:
        inclusion_text = ""
        exclusion_text = criteria_text[exclusion_start:].strip()
    else:
        inclusion_text = criteria_text
        exclusion_text = ""
    
    return inclusion_text, exclusion_text


def extract_locations(locations_list: List[Dict]) -> List[Dict]:
    """
    Extract location metadata for display purposes (Streamlit dashboard).
    NOT used in retrieval, ranking, or matching -- display only.

    Caps at 20 entries to control Qdrant payload size.
    US sites are sorted first so domestic locations are not dropped
    on large multinational trials where international sites are listed first.
    """
    if not locations_list:
        return []

    # Sort US sites first, then others — preserves relative order within each group
    us_sites    = [loc for loc in locations_list if loc.get("country", "").strip().lower() in ("united states", "us", "usa")]
    other_sites = [loc for loc in locations_list if loc.get("country", "").strip().lower() not in ("united states", "us", "usa")]
    sorted_locs = us_sites + other_sites

    if len(locations_list) > 20:
        logging.debug(
            "extract_locations: %d locations found, keeping 20 (US-first).",
            len(locations_list),
        )

    return [
        {
            "facility": loc.get("facility", ""),
            "city":     loc.get("city",     ""),
            "state":    loc.get("state",    ""),
            "country":  loc.get("country",  ""),
        }
        for loc in sorted_locs[:20]
    ]


def create_trial_embedding_text(trial: Dict) -> str:
    """
    Create comprehensive text for trial-level embedding and BM25 retrieval.

    Section ordering is deliberate: disease names, keywords, and drug names
    are placed at the TOP so they are indexed prominently by BM25.
    A breast cancer trial titled "A Phase II Study of Drug X in Solid Tumors"
    would score near-zero for a breast cancer query without this — the word
    "breast" only exists in conditionsModule, which was previously skipped.

    Section order:
        1. Cancer Type  — MeSH disease terms (highest BM25 signal)
        2. Keywords     — disease subtypes, biomarkers, therapy class
        3. Interventions — drug/device names (patient-medication matching)
        4. Title
        5. Summary + first 500 chars of detailed_description
        6. Phase
        7. Inclusion Criteria
        8. Exclusion Criteria
        9. Age range + Sex
    """
    parts = []

    # 1. Conditions (MeSH disease terms) — front-load for BM25
    conditions = trial.get("conditions") or []
    if conditions:
        parts.append(f"Cancer Type: {', '.join(conditions)}")

    # 2. Keywords — disease subtypes, biomarkers, therapy class
    keywords = trial.get("keywords") or []
    if keywords:
        parts.append(f"Keywords: {', '.join(keywords)}")

    # 3. Interventions — drug/device names for medication matching
    interventions = trial.get("interventions") or []
    if interventions:
        parts.append(f"Interventions: {', '.join(interventions)}")

    # 4. Title
    if trial.get("title"):
        parts.append(f"Title: {trial['title']}")

    # 5. Summary + first 500 chars of detailed_description
    if trial.get("brief_summary"):
        parts.append(f"Summary: {trial['brief_summary']}")

    detailed = (trial.get("detailed_description") or "").strip()
    if detailed:
        parts.append(f"Description: {detailed[:500]}")

    # 6. Phase
    parts.append(f"Phase: {trial.get('phase', 'N/A')}")

    # 7. Inclusion Criteria
    eligibility = trial.get("eligibility") or {}
    if eligibility.get("inclusion_criteria"):
        parts.append(f"Inclusion Criteria: {eligibility['inclusion_criteria']}")

    # 8. Exclusion Criteria
    if eligibility.get("exclusion_criteria"):
        parts.append(f"Exclusion Criteria: {eligibility['exclusion_criteria']}")

    # 9. Age + Sex
    parts.append(
        f"Age: {eligibility.get('min_age', 'N/A')} to {eligibility.get('max_age', 'N/A')}"
    )
    parts.append(f"Sex: {eligibility.get('sex', 'ALL')}")

    return "\n\n".join(parts)


def get_embeddings_batch(texts: List[str]) -> List[List[float]]:
    """
    Generate embeddings for a batch of texts in a single OpenAI API call.

    Retries automatically on transient errors (rate limit, server error,
    network failure) with exponential backoff. Gives up after 5 attempts
    and re-raises the original exception.

    Non-retryable errors (bad request, auth failure, permission denied)
    propagate immediately without retrying.

    Args:
        texts: List of strings to embed. Must not be empty.
               Caller is responsible for sizing batches within token limits.

    Returns:
        List of embedding vectors in the same order as input texts.
        Empty list if texts is empty.
    """
    if not texts:
        return []

    @retry(
        reraise=True,
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type((RateLimitError, InternalServerError, APIConnectionError)),
    )
    def _call() -> List[List[float]]:
        response = get_openai_client().embeddings.create(
            model=EMBEDDING_MODEL,
            input=texts,
        )
        # Sort by .index — OpenAI does not guarantee response order matches input order
        return [e.embedding for e in sorted(response.data, key=lambda x: x.index)]

    return _call()


def create_qdrant_collection(collection_name: str, delete_if_exists: bool = False):
    """
    Create Qdrant collection for trial-level dense + sparse BM25 vectors.

    Vector configuration:
      Dense:  OpenAI text-embedding-3-small (1536-dim, cosine)
              Used for semantic similarity retrieval in Stage 2.

      Sparse (3 named BM25 vectors, each with IDF modifier):
        title-bm25:      Trial title text. Highest retrieval signal for
                          disease name matching. JULIE Lab (TREC PM 2019 #1)
                          boosted title 3.0x in ElasticSearch.
        conditions-bm25:  MeSH conditions + keywords + interventions.
                          Disease-specific vocabulary from ClinicalTrials.gov
                          conditionsModule. Second-highest signal.
        criteria-bm25:    Full eligibility criteria text (inclusion + exclusion).
                          Longest field, contains gene names, biomarkers,
                          staging requirements. Base signal.

      Each sparse vector gets its own inverted index with independent IDF
      statistics. This is critical: "cancer" in a title has very different
      IDF than "cancer" in criteria text (where it appears in nearly every
      trial). Separate IDF per field is the production-grade equivalent of
      ElasticSearch field-level BM25.

    The IDF modifier tells Qdrant to compute and maintain inverse document
    frequency at the collection level for each sparse vector. At query time,
    Qdrant applies BM25 scoring automatically.

    Args:
        collection_name: Name of collection to create
        delete_if_exists: If True, delete existing collection first (dangerous)
    """
    if delete_if_exists:
        try:
            get_qdrant_client().delete_collection(collection_name=collection_name)
            console.out(f"Deleted existing collection: {collection_name}")
        
        except Exception as e:
            if "doesn't exist" not in str(e).lower() and "not found" not in str(e).lower():
                raise

    get_qdrant_client().create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        sparse_vectors_config={
            "title-bm25":      SparseVectorParams(modifier=Modifier.IDF, index=SparseIndexParams()),
            "conditions-bm25": SparseVectorParams(modifier=Modifier.IDF, index=SparseIndexParams()),
            "criteria-bm25":   SparseVectorParams(modifier=Modifier.IDF, index=SparseIndexParams()),
        },
    )
    
    console.out(f"Created collection: {collection_name}")
    console.out(f"  Dense vector:  {EMBEDDING_DIM}-dim cosine (OpenAI {EMBEDDING_MODEL})")
    console.out(f"  Sparse vectors: title-bm25, conditions-bm25, criteria-bm25 (BM25 + IDF)")


def create_trial_bm25_fields(trial: Dict) -> Dict[str, str]:
    """
    Build separate text fields for multi-field BM25 sparse indexing.

    Three fields, each indexed as an independent BM25 sparse vector with
    its own IDF statistics:

      title-bm25:      Trial title only. Short, high-signal text.
                        A disease name in the title is the strongest
                        indicator of trial relevance.

      conditions-bm25:  MeSH conditions + keywords + intervention names.
                        Disease-specific controlled vocabulary from
                        ClinicalTrials.gov. Second-highest signal.

      criteria-bm25:    Full eligibility criteria text (inclusion + exclusion).
                        Longest field, contains gene names, biomarkers,
                        staging details, comorbidity exclusions.

    At query time, Stage 2 searches each field separately with different
    queries (disease -> title+conditions, gene -> criteria) and fuses
    results with weighted RRF. This is the production-grade equivalent
    of ElasticSearch field-level BM25 with boost weights.

    Args:
        trial: Trial dict from parse_trial_metadata().

    Returns:
        Dict with keys "title", "conditions", "criteria" mapping to text strings.
    """
    # Title: just the trial title
    title_text = (trial.get("title") or "").strip()

    # Conditions: MeSH terms + keywords + intervention names
    # These are the disease-specific vocabulary fields
    conditions_parts = []
    for cond in (trial.get("conditions") or []):
        if cond.strip():
            conditions_parts.append(cond.strip())
    for kw in (trial.get("keywords") or []):
        if kw.strip():
            conditions_parts.append(kw.strip())
    for iv in (trial.get("interventions") or []):
        if iv.strip():
            conditions_parts.append(iv.strip())
    conditions_text = " ".join(conditions_parts)

    # Criteria: full eligibility text
    eligibility = trial.get("eligibility") or {}
    criteria_text = (eligibility.get("criteria_text") or "").strip()

    return {
        "title": title_text,
        "conditions": conditions_text,
        "criteria": criteria_text,
    }


def index_trials(trials: List[Dict], collection_name: str):
    """
    Embed and index trials at trial-level (one vector per trial).

    Embedding is batched dynamically: batch size is computed from the
    average text length of the actual trials so the per-request token
    budget stays safely under the TARGET_TOKENS limit regardless of text length.

    Checkpoints progress by nct_id so interrupted runs resume without
    re-embedding already-indexed trials.

    Checkpoint behavior:
    - Staging mode: collection_name changes every run (timestamp).
      A mismatch means the old Qdrant staging collection is gone.
      Checkpoint is discarded and all trials are re-embedded fresh.
    - Direct mode: collection_name is fixed ('trial_criteria').
      Checkpoint is valid and resume skips already-indexed trials.

    Args:
        trials:          List of trial dictionaries
        collection_name: Target Qdrant collection name
    """
    console.out(f"\nIndexing {len(trials)} trials into '{collection_name}'...")

    # ------------------------------------------------------------------
    # Checkpoint: fixed filename, not tied to collection_name.
    # Stores the cumulative set of confirmed nct_ids and the
    # collection_name from the interrupted run for mismatch detection.
    # ------------------------------------------------------------------
    embed_checkpoint_file = Path(paths.checkpoint_path) / "embed_checkpoint.json"

    if embed_checkpoint_file.exists():
        with open(embed_checkpoint_file, "r") as f:
            ckpt = json.load(f)

        saved_collection = ckpt.get("collection_name", "")
        indexed_nct_ids  = set(ckpt.get("nct_ids", []))

        if saved_collection != collection_name:
            console.out(
                f"WARNING: Checkpoint was for collection '{saved_collection}', "
                f"but current collection is '{collection_name}'. "
                f"Discarding checkpoint and starting fresh."
            )
            indexed_nct_ids = set()
            embed_checkpoint_file.unlink()
        else:
            console.out(
                f"Resuming embedding checkpoint: "
                f"{len(indexed_nct_ids)} / {len(trials)} trials already indexed."
            )
    else:
        indexed_nct_ids = set()
        console.out("No embedding checkpoint found. Starting fresh.")

    # ------------------------------------------------------------------
    # Filter: skip already-indexed and trials with missing nct_id.
    # Empty nct_id causes md5 hash collisions in Qdrant point_ids.
    # ------------------------------------------------------------------
    skipped_no_id = [t for t in trials if not t.get("nct_id")]
    if skipped_no_id:
        console.out(f"WARNING: Skipping {len(skipped_no_id)} trials with missing nct_id.")

    remaining = [
        t for t in trials
        if t.get("nct_id") and t["nct_id"] not in indexed_nct_ids
    ]

    console.out(f"Trials to embed now: {len(remaining)} "
          f"({len(indexed_nct_ids)} already done, "
          f"{len(skipped_no_id)} skipped — missing nct_id).")

    if not remaining:
        console.out("All trials already indexed. Nothing to do.")
        return

    # ------------------------------------------------------------------
    # Dynamic embedding batch size.
    # Uses character count as a token proxy (~4 chars per token).
    # Targets 800K tokens per API request, capped at 2048 inputs.
    # Computed from a sample of the first 50 trials.
    # ------------------------------------------------------------------
    CHARS_PER_TOKEN   = 4
    TARGET_TOKENS     = 100_000
    MAX_INPUTS        = 750
    QDRANT_BATCH_SIZE = 100

    sample_texts     = [create_trial_embedding_text(t) for t in remaining[:min(50, len(remaining))]]
    avg_chars        = sum(len(t) for t in sample_texts) / len(sample_texts)
    avg_tokens       = avg_chars / CHARS_PER_TOKEN
    embed_batch_size = max(1, min(int(TARGET_TOKENS // avg_tokens), MAX_INPUTS))

    console.out(f"Dynamic embedding batch size: {embed_batch_size} "
          f"(avg ~{avg_tokens:.0f} tokens/trial, target {TARGET_TOKENS:,} tokens/request)")

    # ------------------------------------------------------------------
    # Embed in dynamic batches, upsert to Qdrant in fixed batches of 100.
    # nct_ids confirmed only AFTER successful upsert — no desync possible.
    # Checkpoint saved after each Qdrant upsert.
    # ------------------------------------------------------------------
    embed_buffer  = []   # trials accumulating for next API call
    points_batch  = []   # PointStructs ready for next Qdrant upsert
    batch_nct_ids = []   # nct_ids for current Qdrant batch
    embed_start   = time.time()

    def _flush_qdrant_batch():
        """Upsert current points_batch, confirm nct_ids, save checkpoint."""
        if not points_batch:
            return
        
        qdrant_retry(
            lambda: get_qdrant_client().upsert(
                collection_name=collection_name, points=points_batch
                )
            )()
        
        indexed_nct_ids.update(batch_nct_ids)
        
        with open(embed_checkpoint_file, "w") as f:
            json.dump(
                {"nct_ids": list(indexed_nct_ids), "collection_name": collection_name},
                f
            )
        points_batch.clear()
        batch_nct_ids.clear()

    def _flush_embed_buffer(pbar):
        """Call embedding API for current embed_buffer, build PointStructs with dense + sparse vectors."""
        if not embed_buffer:
            return
        texts      = [t["embedding_text"] for t in embed_buffer]
        embeddings = get_embeddings_batch(texts)

        # Guard: API must return exactly one embedding per input.
        assert len(embeddings) == len(texts), (
            f"API returned {len(embeddings)} embeddings for {len(texts)} inputs"
        )

        # Generate BM25 sparse vectors for each field (local, no API cost)
        title_texts      = [t["bm25_fields"]["title"] for t in embed_buffer]
        conditions_texts = [t["bm25_fields"]["conditions"] for t in embed_buffer]
        criteria_texts   = [t["bm25_fields"]["criteria"] for t in embed_buffer]

        # ONE construction site for this model, shared with the agent's
        # query encoder -- see oncotriage/embedding.py. Resolved here
        # rather than at module level: File 11 built it at exec() time and
        # printed as it went, so importing the indexer loaded a model.
        #
        # The ACCESSOR is imported by name, not the module: this function
        # binds a loop variable called `embedding` two lines down, so
        # `embedding.get_bm25_sparse_model()` would be an UnboundLocalError
        # -- a name assigned anywhere in a function is local for the whole
        # of it. Caught by the shadowing scan in File 47 section 2g.
        bm25_model        = get_bm25_sparse_model()
        title_sparse      = list(bm25_model.embed(title_texts))
        conditions_sparse = list(bm25_model.embed(conditions_texts))
        criteria_sparse   = list(bm25_model.embed(criteria_texts))

        for item, embedding, t_sp, c_sp, cr_sp in zip(
            embed_buffer, embeddings, title_sparse, conditions_sparse, criteria_sparse
        ):
            trial    = item["trial"]
            nct_id   = trial["nct_id"]
            point_id = int(hashlib.md5(nct_id.encode()).hexdigest()[:16], 16)
            points_batch.append(PointStruct(
                id=point_id,
                vector={
                    "": embedding,  # default (unnamed) dense vector
                    "title-bm25": SparseVector(
                        indices=t_sp.indices.tolist(),
                        values=t_sp.values.tolist(),
                    ),
                    "conditions-bm25": SparseVector(
                        indices=c_sp.indices.tolist(),
                        values=c_sp.values.tolist(),
                    ),
                    "criteria-bm25": SparseVector(
                        indices=cr_sp.indices.tolist(),
                        values=cr_sp.values.tolist(),
                    ),
                },
                payload={
                    "nct_id":          nct_id,
                    "title":           trial["title"],
                    "phase":           trial["phase"],
                    "bm25_text":       item["embedding_text"],
                    "full_trial_json": trial
                }
            ))
            batch_nct_ids.append(nct_id)
            if len(points_batch) >= QDRANT_BATCH_SIZE:
                _flush_qdrant_batch()

        pbar.update(len(embed_buffer))
        embed_buffer.clear()

    with tqdm(total=len(remaining), desc="Embedding", unit="trial") as pbar:
        for trial in remaining:
            embedding_text = create_trial_embedding_text(trial)
            bm25_fields = create_trial_bm25_fields(trial)
            embed_buffer.append({
                "trial": trial,
                "embedding_text": embedding_text,
                "bm25_fields": bm25_fields,
            })
            if len(embed_buffer) >= embed_batch_size:
                _flush_embed_buffer(pbar)
        _flush_embed_buffer(pbar)  
        
    # To make sure the rest is also flushed and indexed 
    _flush_qdrant_batch()  # final Qdrant flush


    elapsed = time.time() - embed_start
    hrs, rem = divmod(int(elapsed), 3600)
    mins, secs = divmod(rem, 60)
    elapsed_str = f"{hrs:02d}:{mins:02d}:{secs:02d}" if hrs > 0 else f"{mins:02d}:{secs:02d}"

    console.out(f"\n✓ Indexed {len(indexed_nct_ids)} trials into '{collection_name}'  |  Embed time: {elapsed_str}")

    if embed_checkpoint_file.exists():
        embed_checkpoint_file.unlink()
        console.out("Embedding checkpoint cleared.")
        

def save_trials_to_disk(trials: List[Dict], output_path: str):
    """
    Save scraped trials to disk as a JSON backup.
    Uses a fixed filename so every run overwrites cleanly
    instead of accumulating date-stamped duplicates.

    Args:
        trials:      List of trial dictionaries to save
        output_path: Directory to write the file into (paths.data_trial_path)
    """
    if not trials:
        console.out("No trials to save.")
        return

    output_dir  = Path(output_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "trials_latest.json"

    try:
        with open(output_file, "w") as f:
            json.dump(trials, f, indent=2)
        console.out(f"Saved {len(trials)} trials to {output_file}")

    except OSError as e:
        console.out(f"WARNING: Could not save trials to disk: {e}")
        console.out("Continuing to indexing step — trials are still in memory.")
        

def swap_alias_atomic(new_collection: str, alias_name: str):
    """
    Atomically swap alias to point to new collection (zero downtime).
    Both delete and create are sent in a single API call so there is
    no window where the alias is missing between operations.

    On first run (alias does not yet exist), creates it directly.

    Args:
        new_collection: Staging collection to point the alias to
        alias_name:     Alias name (e.g., 'trial_criteria')
    """
    # Pre-check whether the alias exists so we never rely on
    # fragile error-message string matching for flow control.
    existing_aliases = [
        a.alias_name
        for a in get_qdrant_client().get_aliases().aliases
    ]
    alias_exists = alias_name in existing_aliases

    if alias_exists:
        # Atomic swap: delete old + create new in one call.
        # Qdrant applies both operations together — zero downtime.
        get_qdrant_client().update_collection_aliases(
            change_aliases_operations=[
                DeleteAliasOperation(
                    delete_alias=DeleteAlias(alias_name=alias_name)
                ),
                CreateAliasOperation(
                    create_alias=CreateAlias(
                        collection_name=new_collection,
                        alias_name=alias_name
                    )
                )
            ]
        )
        console.out(f"Swapped alias '{alias_name}' -> '{new_collection}'")

    else:
        # First run — alias does not exist yet, create it directly.
        get_qdrant_client().update_collection_aliases(
            change_aliases_operations=[
                CreateAliasOperation(
                    create_alias=CreateAlias(
                        collection_name=new_collection,
                        alias_name=alias_name
                    )
                )
            ]
        )
        console.out(f"Created alias '{alias_name}' -> '{new_collection}'")
        

def cleanup_old_collections(keep_recent: int = 1):
    """
    Delete old timestamped staging collections, keep N most recent.
    Called after alias swap so the live collection is always kept.

    Args:
        keep_recent: Number of recent timestamped collections to keep (minimum 1)
    """
    # Enforce minimum of 1 to never delete the live collection
    if keep_recent < 1:
        console.out("WARNING: keep_recent must be >= 1. Defaulting to 1.")
        keep_recent = 1

    collections = get_qdrant_client().get_collections().collections
    timestamped = [
        c.name for c in collections
        if c.name.startswith("trial_criteria_") and c.name != "trial_criteria"
    ]

    # Sort descending: newest first (YYYYMMDD_HHMMSS format sorts correctly)
    timestamped.sort(reverse=True)

    to_keep   = timestamped[:keep_recent]
    to_delete = timestamped[keep_recent:]

    if not to_delete:
        console.out(f"No old collections to clean up. Keeping: {to_keep}")
        return

    for name in to_delete:
        try:
            get_qdrant_client().delete_collection(collection_name=name)
            console.out(f"Deleted old collection: {name}")
        except Exception as e:
            console.out(f"WARNING: Could not delete collection '{name}': {e}")

    console.out(f"Kept {keep_recent} recent collection(s): {to_keep}")
    

#------------------------------------------------------------------------------


def main(use_staging: bool = True):
    """
    Main execution: scrape, embed, index with zero-downtime swap.

    Args:
        use_staging: If True, build in staging and swap atomically.
                     If False, rebuild production directly (causes downtime).
    """
    with CaffeinateSession("RAG Indexing"):
        console.out(f"=== {Project_Name}: Clinical Trial RAG Indexer ===\n")

        trials = scrape_clinicaltrials_gov()
        if not trials:
            console.out("No trials scraped. Exiting.")
            return

        save_trials_to_disk(trials, output_path=paths.data_trial_path)

        if use_staging:
            console.out("\n=== STAGING REBUILD (ZERO DOWNTIME) ===\n")
            staging_name = f"trial_criteria_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            create_qdrant_collection(staging_name, delete_if_exists=False)
            index_trials(trials, collection_name=staging_name)
            create_payload_indexes(staging_name)
            swap_alias_atomic(staging_name, "trial_criteria")
            cleanup_old_collections(keep_recent=1)

            console.out(f"\n✓ Staging rebuild complete")
            console.out(f"✓ Alias 'trial_criteria' now points to '{staging_name}'")
            console.out(f"✓ FastAPI experienced zero downtime\n")
            
        else:
            console.out("\n=== DIRECT REBUILD (CAUSES DOWNTIME) ===\n")
            create_qdrant_collection("trial_criteria", delete_if_exists=True)
            index_trials(trials, collection_name="trial_criteria")
            create_payload_indexes("trial_criteria")
            console.out(f"\n✓ Direct rebuild complete\n")

        console.out("=== Indexing Complete ===")
        console.out("Ready for hybrid BM25 + Vector retrieval!")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb 10 2026

@author: ramyalsaffar
"""
