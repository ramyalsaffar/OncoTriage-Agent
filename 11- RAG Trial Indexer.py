# Scrape Clinical Trials Data from the clinicaltrials.gov API
#############################################################


"""
Trial RAG Indexer (TRIAL-LEVEL EMBEDDINGS + BM25)
Scrapes trials from ClinicalTrials.gov, creates trial-level embeddings,
and prepares data for hybrid BM25 + Vector retrieval.
"""


#------------------------------------------------------------------------------


# Run needed files
#-----------------
# Item 20a: this file sits in the code directory, so __file__ locates it with
# no hardcoded path. __file__ is bound when the file is run as a script (every
# documented entry point for it) and when Spyder runfile()s it. In a bare
# interactive paste it is not bound, and the working directory is the only
# remaining candidate -- taken, but announced, never silently.
import os as _os_boot
if "__file__" in globals():
    _code_dir = _os_boot.path.dirname(_os_boot.path.abspath(__file__)) + _os_boot.sep
else:
    _code_dir = _os_boot.getcwd() + _os_boot.sep
    print(f"[Bootstrap] __file__ unbound; using the working directory as the code directory: {_code_dir}")
del _os_boot

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

exec_chain(
    ["03- Config.py", "10- Structured Eligibility Extractor.py"],
    caller_file=_code_dir + "11- RAG Trial Indexer.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 03 → 10",
)


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Load BM25 sparse embedding model (FastEmbed, runs locally, no API cost)
# ---------------------------------------------------------------------------
# Used at index time to generate per-field sparse vectors for Qdrant BM25.
# Loaded once at module level, reused across all index_trials() calls.
# The model is lightweight (~1MB vocabulary file) and runs on CPU.

print("Loading BM25 sparse embedding model (FastEmbed)...")
_bm25_sparse_model = SparseTextEmbedding(model_name="Qdrant/bm25")
print("BM25 sparse model loaded.\n")


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
                qdrant_client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=field_schema,
                )
                print(f"✓ Payload index created: {field_name} on '{collection_name}'")
                return
            except Exception as e:
                wait_time = 2 ** attempt
                print(f"  Attempt {attempt}/{max_retries} failed for {field_name}: {e}")
                if attempt < max_retries:
                    print(f"  Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"  FAILED to create index {field_name} after {max_retries} attempts")
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
    checkpoint_file = Path(checkpoint_path) / "scrape_checkpoint.json"

    if checkpoint_file.exists():
        with open(checkpoint_file, "r") as f:
            ckpt = json.load(f)
        trials     = ckpt.get("trials", [])
        page_token = ckpt.get("page_token", None)
        print(f"Resuming scrape from checkpoint: {len(trials)} trials already scraped.")
    else:
        trials     = []
        page_token = None
        print("Starting fresh scrape from ClinicalTrials.gov...")

    print(f"Filters: condition={condition}, status={status}, type={study_type}, max={max_trials}")

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
                    tqdm.write("No more trials found.")
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
                        except (IndexError, ValueError):
                            pass

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
                tqdm.write(f"Network error fetching trials: {e}")
                tqdm.write("Progress saved to checkpoint — re-run to resume.")
                break

    elapsed = time.time() - scrape_start
    hrs, rem = divmod(int(elapsed), 3600)
    mins, secs = divmod(rem, 60)
    elapsed_str = f"{hrs:02d}:{mins:02d}:{secs:02d}" if hrs > 0 else f"{mins:02d}:{secs:02d}"

    print(f"\nTotal trials scraped: {len(trials)}  |  Scrape time: {elapsed_str}")

    _histology_stats = get_histology_extraction_stats()
    if _histology_stats.get("exclusive_pair_kept"):
        print(f"Trials carrying a mutually exclusive histology pair: "
              f"{_histology_stats['exclusive_pair_kept']}  (permit either "
              f"histology — both tags indexed, both populations match)")
    if any(_histology_stats.values()):
        print(f"Histology negation/exclusive-pair counters: {_histology_stats}")

    _stage_stats = get_stage_extraction_stats()
    if any(_stage_stats.values()):
        print(f"Stage negation/span/exclusion-bound counters: {_stage_stats}")

    if scrape_complete and checkpoint_file.exists():
        checkpoint_file.unlink()
        print("Scrape checkpoint cleared.")

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
        response = openai_client.embeddings.create(
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
            qdrant_client.delete_collection(collection_name=collection_name)
            print(f"Deleted existing collection: {collection_name}")
        
        except Exception as e:
            if "doesn't exist" not in str(e).lower() and "not found" not in str(e).lower():
                raise

    qdrant_client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        sparse_vectors_config={
            "title-bm25":      SparseVectorParams(modifier=Modifier.IDF, index=SparseIndexParams()),
            "conditions-bm25": SparseVectorParams(modifier=Modifier.IDF, index=SparseIndexParams()),
            "criteria-bm25":   SparseVectorParams(modifier=Modifier.IDF, index=SparseIndexParams()),
        },
    )
    
    print(f"Created collection: {collection_name}")
    print(f"  Dense vector:  {EMBEDDING_DIM}-dim cosine (OpenAI {EMBEDDING_MODEL})")
    print(f"  Sparse vectors: title-bm25, conditions-bm25, criteria-bm25 (BM25 + IDF)")


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
    print(f"\nIndexing {len(trials)} trials into '{collection_name}'...")

    # ------------------------------------------------------------------
    # Checkpoint: fixed filename, not tied to collection_name.
    # Stores the cumulative set of confirmed nct_ids and the
    # collection_name from the interrupted run for mismatch detection.
    # ------------------------------------------------------------------
    embed_checkpoint_file = Path(checkpoint_path) / "embed_checkpoint.json"

    if embed_checkpoint_file.exists():
        with open(embed_checkpoint_file, "r") as f:
            ckpt = json.load(f)

        saved_collection = ckpt.get("collection_name", "")
        indexed_nct_ids  = set(ckpt.get("nct_ids", []))

        if saved_collection != collection_name:
            print(
                f"WARNING: Checkpoint was for collection '{saved_collection}', "
                f"but current collection is '{collection_name}'. "
                f"Discarding checkpoint and starting fresh."
            )
            indexed_nct_ids = set()
            embed_checkpoint_file.unlink()
        else:
            print(
                f"Resuming embedding checkpoint: "
                f"{len(indexed_nct_ids)} / {len(trials)} trials already indexed."
            )
    else:
        indexed_nct_ids = set()
        print("No embedding checkpoint found. Starting fresh.")

    # ------------------------------------------------------------------
    # Filter: skip already-indexed and trials with missing nct_id.
    # Empty nct_id causes md5 hash collisions in Qdrant point_ids.
    # ------------------------------------------------------------------
    skipped_no_id = [t for t in trials if not t.get("nct_id")]
    if skipped_no_id:
        print(f"WARNING: Skipping {len(skipped_no_id)} trials with missing nct_id.")

    remaining = [
        t for t in trials
        if t.get("nct_id") and t["nct_id"] not in indexed_nct_ids
    ]

    print(f"Trials to embed now: {len(remaining)} "
          f"({len(indexed_nct_ids)} already done, "
          f"{len(skipped_no_id)} skipped — missing nct_id).")

    if not remaining:
        print("All trials already indexed. Nothing to do.")
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

    print(f"Dynamic embedding batch size: {embed_batch_size} "
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
            lambda: qdrant_client.upsert(
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

        title_sparse      = list(_bm25_sparse_model.embed(title_texts))
        conditions_sparse = list(_bm25_sparse_model.embed(conditions_texts))
        criteria_sparse   = list(_bm25_sparse_model.embed(criteria_texts))

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

    print(f"\n✓ Indexed {len(indexed_nct_ids)} trials into '{collection_name}'  |  Embed time: {elapsed_str}")

    if embed_checkpoint_file.exists():
        embed_checkpoint_file.unlink()
        print("Embedding checkpoint cleared.")
        

def save_trials_to_disk(trials: List[Dict], output_path: str):
    """
    Save scraped trials to disk as a JSON backup.
    Uses a fixed filename so every run overwrites cleanly
    instead of accumulating date-stamped duplicates.

    Args:
        trials:      List of trial dictionaries to save
        output_path: Directory to write the file into (data_trial_path)
    """
    if not trials:
        print("No trials to save.")
        return

    output_dir  = Path(output_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "trials_latest.json"

    try:
        with open(output_file, "w") as f:
            json.dump(trials, f, indent=2)
        print(f"Saved {len(trials)} trials to {output_file}")

    except OSError as e:
        print(f"WARNING: Could not save trials to disk: {e}")
        print("Continuing to indexing step — trials are still in memory.")
        

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
        for a in qdrant_client.get_aliases().aliases
    ]
    alias_exists = alias_name in existing_aliases

    if alias_exists:
        # Atomic swap: delete old + create new in one call.
        # Qdrant applies both operations together — zero downtime.
        qdrant_client.update_collection_aliases(
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
        print(f"Swapped alias '{alias_name}' -> '{new_collection}'")

    else:
        # First run — alias does not exist yet, create it directly.
        qdrant_client.update_collection_aliases(
            change_aliases_operations=[
                CreateAliasOperation(
                    create_alias=CreateAlias(
                        collection_name=new_collection,
                        alias_name=alias_name
                    )
                )
            ]
        )
        print(f"Created alias '{alias_name}' -> '{new_collection}'")
        

def cleanup_old_collections(keep_recent: int = 1):
    """
    Delete old timestamped staging collections, keep N most recent.
    Called after alias swap so the live collection is always kept.

    Args:
        keep_recent: Number of recent timestamped collections to keep (minimum 1)
    """
    # Enforce minimum of 1 to never delete the live collection
    if keep_recent < 1:
        print("WARNING: keep_recent must be >= 1. Defaulting to 1.")
        keep_recent = 1

    collections = qdrant_client.get_collections().collections
    timestamped = [
        c.name for c in collections
        if c.name.startswith("trial_criteria_") and c.name != "trial_criteria"
    ]

    # Sort descending: newest first (YYYYMMDD_HHMMSS format sorts correctly)
    timestamped.sort(reverse=True)

    to_keep   = timestamped[:keep_recent]
    to_delete = timestamped[keep_recent:]

    if not to_delete:
        print(f"No old collections to clean up. Keeping: {to_keep}")
        return

    for name in to_delete:
        try:
            qdrant_client.delete_collection(collection_name=name)
            print(f"Deleted old collection: {name}")
        except Exception as e:
            print(f"WARNING: Could not delete collection '{name}': {e}")

    print(f"Kept {keep_recent} recent collection(s): {to_keep}")
    

#------------------------------------------------------------------------------


def main(use_staging: bool = True):
    """
    Main execution: scrape, embed, index with zero-downtime swap.

    Args:
        use_staging: If True, build in staging and swap atomically.
                     If False, rebuild production directly (causes downtime).
    """
    with CaffeinateSession("RAG Indexing"):
        print(f"=== {Project_Name}: Clinical Trial RAG Indexer ===\n")

        trials = scrape_clinicaltrials_gov()
        if not trials:
            print("No trials scraped. Exiting.")
            return

        save_trials_to_disk(trials, output_path=data_trial_path)

        if use_staging:
            print("\n=== STAGING REBUILD (ZERO DOWNTIME) ===\n")
            staging_name = f"trial_criteria_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            create_qdrant_collection(staging_name, delete_if_exists=False)
            index_trials(trials, collection_name=staging_name)
            create_payload_indexes(staging_name)
            swap_alias_atomic(staging_name, "trial_criteria")
            cleanup_old_collections(keep_recent=1)

            print(f"\n✓ Staging rebuild complete")
            print(f"✓ Alias 'trial_criteria' now points to '{staging_name}'")
            print(f"✓ FastAPI experienced zero downtime\n")
            
        else:
            print("\n=== DIRECT REBUILD (CAUSES DOWNTIME) ===\n")
            create_qdrant_collection("trial_criteria", delete_if_exists=True)
            index_trials(trials, collection_name="trial_criteria")
            create_payload_indexes("trial_criteria")
            print(f"\n✓ Direct rebuild complete\n")

        print("=== Indexing Complete ===")
        print("Ready for hybrid BM25 + Vector retrieval!")
            

#------------------------------------------------------------------------------


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--mode',
        choices=['staging', 'direct'],
        default='staging',
        help='staging: zero downtime (default) | direct: causes downtime'
    )
    args = parser.parse_args()
    
    main(use_staging=(args.mode == 'staging'))
    

#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb 10 2026

@author: ramyalsaffar
"""