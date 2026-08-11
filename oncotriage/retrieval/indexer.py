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
import threading
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
    FieldCondition,
    Filter,
    MatchValue,
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
from oncotriage.registries.mesh import (
    TRIAL_NON_ONCOLOGY,
    TRIAL_ONCOLOGY,
    TRIAL_UNRESOLVED,
    load_mesh_filter,
)
from oncotriage.utils import CaffeinateSession, get_model_cost, qdrant_retry
from oncotriage.observability import console, get_logger


log = get_logger(__name__)


#------------------------------------------------------------------------------


# ===========================================================================
# DEFECT 1: THE INDEX-TIME AGE FILTER IS DELETED, NOT WIDENED
# ===========================================================================
#
# The scrape loop used to run
#
#     min_age = int(re.findall(r'\d+', min_age_str)[0])
#     if min_age > 18:
#         continue
#
# which is not an adult filter. It is an EXACTLY-18 filter: a trial whose
# minimumAge is 19, 20 or 21 was discarded, so a 70-year-old who qualifies for
# a trial requiring 21 could never be matched to it, because the trial was
# never in the corpus. The loss happens before any gate the pipeline measures,
# so no stage-wise recall number could ever show it.
#
# WHY DELETED RATHER THAN WIDENED, and this was checked in the executable code
# before it was decided. oncotriage/agent/filtering.py:node_rule_based_filter
# parses both bounds and applies
#
#     elif patient_age is not None and not (min_age <= patient_age <= max_age):
#         age_dropped += 1
#         continue
#
# -- the full range, per patient, per request, counted into `age_dropped` and
# reported in the Stage 4 log line. So the trial's own eligibility window is
# already enforced against the actual patient at query time, and the scrape's
# copy could only ever remove trials that check would have handled correctly.
#
# Widening the scrape filter to "the oldest patient we serve" was rejected on
# the same grounds the item is about: it writes TODAY'S COHORT into the corpus.
# Change the cohort -- a younger trial population, a paediatric arm, one older
# patient -- and the corpus is silently wrong again, in the identical
# undetectable way. A corpus must describe the registry, not the roster.
#
# WHAT WENT WITH IT: INDEX_AGE_PARSE_FAILURES. That Counter existed only to
# record when the skip test above could not be evaluated. With no test there is
# nothing to fail, and a counter that can never increment is a dead declaration
# of exactly the shape tests/test_package_invariants.py check 2h exists to
# report. Stage 4's AGE_PARSE_FAILURES is untouched and is now the only
# age-parse record in the project, which is correct: it is the only place an
# age bound is still parsed.


# ===========================================================================
# DEFECT 2: THE ADMISSION SCREEN, AND WHY IT MAY ONLY DROP ONE WAY
# ===========================================================================
#
# The screen this replaces was a frozenset substring test over sixteen words.
# It held "glioma" but neither "blastoma" nor "thelioma", so a trial registered
# only as Glioblastoma, Mesothelioma, Neuroblastoma, Retinoblastoma or
# Hepatoblastoma matched nothing in it and was discarded. Measured against the
# shipped list, all five drop; measured against the C04 crosswalk, all five
# resolve to specific tree numbers.
#
# Patching the word list was rejected because the list is the defect. Any
# hand-maintained vocabulary of oncology has the same failure mode and no way
# to know it is incomplete -- the losses are silent by construction.
#
# THE CROSSWALK IS NOT TREATED AS KNOWN-GOOD. On the patient side 224 of 1,000
# patients resolve their site through fuzzy stemming rather than a crosswalk
# hit, and that path's accuracy has never been measured. Trials carry free-text
# condition strings, so this screen depends on the same resolution machinery.
# The screen is therefore built so that resolution QUALITY cannot cost a trial:
#
#   TRIAL_ONCOLOGY     -> admit
#   TRIAL_UNRESOLVED   -> ADMIT, and count. A failure to resolve is not
#                         evidence of anything.
#   TRIAL_NON_ONCOLOGY -> drop, and log the trial WITH its conditions. This is
#                         the only verdict that removes a trial, and it needs
#                         every registered condition to be a known MeSH term
#                         positively outside C04.
#
# UNRESOLVED_KEPT IS THE NUMBER THAT MATTERS and it is reported at the end of
# every scrape: it is the size of the uncertainty the screen is absorbing. A
# false keep costs a little retrieval noise that Stage 4 and the judge already
# filter per patient. A false drop is permanent, invisible and unmeasurable.
ADMISSION_SCREEN = Counter()

# MeSH top-level categories (C19, F03, ...) that justified a drop, so the
# dropped population is auditable in aggregate and not only line by line.
ADMISSION_DROPPED_CATEGORIES = Counter()

# The screen's MeSH filter, resolved once, lazily, behind a lock.
#
# NOT through oncotriage.agent.deps, on the same argument the module docstring
# already makes for the clients and that oncotriage/fhir/clean.py makes for the
# cancer registry: a stub installed for an agent test must not change which
# trials are admitted to the corpus. This calls load_mesh_filter() directly.
#
# It is allowed to RAISE. load_mesh_filter() raises DegradedDependencyError
# when the two core C04 files are missing (item 11a), and that is right here
# too: without them the screen resolves nothing, admits everything, and
# produces a corpus quietly containing non-oncology trials -- which is the
# class of defect this whole file is being changed to remove. One command
# fixes it. The OPTIONAL non-oncology layer is different and does not raise:
# its absence can only stop a drop, never cause one, so it degrades to
# "admit everything" with a counter (see mesh.classify_trial_oncology).
_SCREEN_LOCK = threading.RLock()
_SCREEN_CACHE = {}


def admission_screen_filter():
    """The MeSHCancerFilter used by the scrape's admission screen.

    Lazy and locked, matching oncotriage/agent/deps.py and
    oncotriage/fhir/clean.py: importing this module must load nothing, and the
    check-then-build sequence must not be able to build twice.
    """
    with _SCREEN_LOCK:
        if "filter" not in _SCREEN_CACHE:
            _SCREEN_CACHE["filter"] = load_mesh_filter()
        return _SCREEN_CACHE["filter"]


def reset_admission_screen_cache():
    """Drop the cached filter. For harnesses that install their own."""
    with _SCREEN_LOCK:
        _SCREEN_CACHE.clear()


def screen_trial_for_admission(trial: Dict, mesh_filter) -> bool:
    """True if `trial` may enter the corpus. Records every outcome.

    `mesh_filter` is passed in rather than resolved here so the scrape loop
    resolves it once and so a test can drive this function directly.
    A None filter admits everything and says so.
    """
    if mesh_filter is None:
        ADMISSION_SCREEN[f"{TRIAL_UNRESOLVED}:no_mesh_filter"] += 1
        return True

    result = mesh_filter.classify_trial_oncology(trial)
    verdict = result["verdict"]
    ADMISSION_SCREEN[f"{verdict}:{result['evidence']}"] += 1

    if verdict == TRIAL_ONCOLOGY:
        return True

    if verdict == TRIAL_UNRESOLVED:
        # KEPT. Logged at INFO rather than WARNING: on a registry of free-text
        # condition strings this is an expected outcome, not a fault, and the
        # aggregate is what an operator acts on.
        log.info("trial admitted unresolved by the oncology screen",
                 nct_id=trial.get("nct_id", ""), verdict=verdict,
                 evidence=result["evidence"],
                 trial_conditions=result["unresolved"] or trial.get("conditions") or [])
        return True

    if verdict == TRIAL_NON_ONCOLOGY:
        # The only drop.
        for cat in result["categories"]:
            ADMISSION_DROPPED_CATEGORIES[cat] += 1
        log.info("trial dropped by the oncology admission screen",
                 nct_id=trial.get("nct_id", ""), verdict=verdict,
                 evidence=result["evidence"],
                 trial_conditions=trial.get("conditions") or [],
                 mesh_categories=result["categories"])
        return False

    # The vocabulary is closed (TRIAL_ONCOLOGY_VERDICTS) and every member is
    # handled above, so this is unreachable unless a member is added without a
    # policy here. Admitting on an unknown verdict would be the silent drop's
    # mirror image -- a silent KEEP that hides the omission -- so it raises.
    raise RuntimeError(
        f"admission screen: unknown verdict {verdict!r} for "
        f"{trial.get('nct_id', '')!r}. Every member of "
        f"TRIAL_ONCOLOGY_VERDICTS needs an explicit policy here.")


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

    # Resolved ONCE, before the loop, for two reasons. It reads four JSON
    # lookups, and resolving it per study would do that per study. And it can
    # raise (missing C04 core, item 11a) -- which must happen before a single
    # page is fetched, not 200 pages in.
    screen_filter = admission_screen_filter()

    # A resumed checkpoint that already holds the cap is COMPLETE: the loop
    # below will not run, and without this the incompleteness raise at the end
    # would fire on a scrape that had in fact finished.
    scrape_complete = len(trials) >= max_trials
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
                # ONE PAGE, WITH RETRIES. Without this a single transient
                # ReadTimeout ends a five-minute scrape, and ClinicalTrials.gov
                # produced one on two of three full runs during this pass --
                # measured, not feared. The IncompleteScrapeError below is the
                # correctness guarantee; this is what stops it firing on a
                # fault that clears itself in two seconds.
                #
                # Every attempt is counted, so a scrape that completed only
                # because it retried eleven times is visible rather than
                # looking identical to one that never stumbled.
                data = None
                for _attempt in range(1, _SCRAPE_PAGE_ATTEMPTS + 1):
                    try:
                        response = requests.get(base_url, params=params,
                                                timeout=_SCRAPE_PAGE_TIMEOUT)
                        response.raise_for_status()
                        data = response.json()
                        break
                    except requests.exceptions.RequestException as _page_exc:
                        SCRAPE_RETRIES[type(_page_exc).__name__] += 1
                        if _attempt == _SCRAPE_PAGE_ATTEMPTS:
                            raise
                        _wait = 2 ** _attempt
                        console.out(f"  page fetch failed "
                                    f"({type(_page_exc).__name__}), attempt "
                                    f"{_attempt}/{_SCRAPE_PAGE_ATTEMPTS}; "
                                    f"retrying in {_wait}s")
                        log.warning("scrape page retry",
                                    retry=_attempt,
                                    max_retries=_SCRAPE_PAGE_ATTEMPTS,
                                    delay_s=_wait,
                                    error_type=type(_page_exc).__name__)
                        time.sleep(_wait)

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

                    # DEFECT 1: the minimumAge > 18 skip WAS HERE and is gone.
                    # Stage 4 enforces the trial's full age window against the
                    # actual patient; see the note at the top of this module.

                    # A trial tagged with a mutually exclusive pair PERMITS
                    # either histology, it does not require both. Both tags are
                    # indexed and the query-time filter intersects before it
                    # looks for an exclusive pair, so each population still
                    # matches. Counted as exclusive_pair_kept, reported below.
                    trial = parse_trial_metadata(protocol)

                    # DEFECT 2: the sixteen-word frozenset screen WAS HERE.
                    # It is now the MeSH-routed admission screen, which drops
                    # only on a positive non-oncology determination and admits
                    # -- and counts -- every trial it cannot resolve.
                    if not screen_trial_for_admission(trial, screen_filter):
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
                # Recorded, then re-raised below via scrape_complete=False.
                # This handler used to `break` and let the function RETURN the
                # partial list, which main() then indexed and PROMOTED.
                SCRAPE_INTERRUPTIONS[type(e).__name__] += 1
                console.out(f"Network error fetching trials: {e}")
                console.out("Progress saved to checkpoint — re-run to resume.")
                log.warning("scrape interrupted by a network error",
                            error_type=type(e).__name__, error_message=str(e),
                            count=len(trials))
                break

    elapsed = time.time() - scrape_start
    hrs, rem = divmod(int(elapsed), 3600)
    mins, secs = divmod(rem, 60)
    elapsed_str = f"{hrs:02d}:{mins:02d}:{secs:02d}" if hrs > 0 else f"{mins:02d}:{secs:02d}"

    console.out(f"\nTotal trials scraped: {len(trials)}  |  Scrape time: {elapsed_str}")

    # A scrape that only completed because it retried is not the same event as
    # one that never stumbled, and the corpus cannot tell them apart.
    if SCRAPE_RETRIES:
        console.out(f"Page fetches retried: {dict(SCRAPE_RETRIES)} "
                    f"({sum(SCRAPE_RETRIES.values())} total)")
        log.warning("scrape completed with retries",
                    retry=sum(SCRAPE_RETRIES.values()),
                    count=len(trials))

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

    # --- The admission screen's funnel, always reported ---------------------
    #
    # ALWAYS, not only when non-zero. A screen that dropped nothing and a
    # screen that did not run produce the same corpus, and the whole point of
    # this item is that such a difference must never again be invisible.
    _screened = sum(ADMISSION_SCREEN.values())
    _dropped = sum(v for k, v in ADMISSION_SCREEN.items()
                   if k.startswith(f"{TRIAL_NON_ONCOLOGY}:"))
    _unresolved = sum(v for k, v in ADMISSION_SCREEN.items()
                      if k.startswith(f"{TRIAL_UNRESOLVED}:"))
    console.out(f"\nOncology admission screen: {_screened:,} screened, "
                f"{_screened - _dropped:,} admitted, {_dropped:,} dropped "
                f"(positive non-oncology determination only).")
    console.out(f"  Admitted because the screen COULD NOT RESOLVE them: "
                f"{_unresolved:,}  <- the uncertainty this screen absorbs")
    console.out(f"  Verdicts: {dict(ADMISSION_SCREEN)}")
    if ADMISSION_DROPPED_CATEGORIES:
        console.out(f"  Dropped by MeSH top-level category: "
                    f"{dict(ADMISSION_DROPPED_CATEGORIES)}")
    log.info("oncology admission screen complete", screened=_screened,
             admitted=_screened - _dropped, non_oncology_dropped=_dropped,
             unresolved_kept=_unresolved)

    # --- Defect 3: the residual unsplit population --------------------------
    _unsplit = sum(v for k, v in CRITERIA_SPLIT_METHODS.items()
                   if k == CRITERIA_SPLIT_UNSPLIT)
    console.out(f"Criteria split: {dict(CRITERIA_SPLIT_METHODS)}")
    console.out(f"  UNSPLIT (whole text is inclusion, exclusion empty): "
                f"{_unsplit:,} — each carries criteria_split="
                f"'{CRITERIA_SPLIT_UNSPLIT}' as a trial-level field")
    log.info("criteria split complete", unsplit_count=_unsplit,
             total=sum(CRITERIA_SPLIT_METHODS.values()))

    if scrape_complete and checkpoint_file.exists():
        checkpoint_file.unlink()
        console.out("Scrape checkpoint cleared.")

    # ===================================================================
    # A PARTIAL SCRAPE IS NOT A CORPUS, AND MUST NOT BE RETURNED AS ONE
    # ===================================================================
    #
    # THIS RAISE EXISTS BECAUSE THE ALTERNATIVE HAPPENED, on the first real
    # run of this pass. A transient ClinicalTrials.gov read timeout hit the
    # handler above, which `break`s. `scrape_complete` stayed False -- so the
    # checkpoint was correctly KEPT -- but the function then returned 5,482 of
    # ~14,300 trials as its ordinary return value. main() had no way to tell a
    # complete corpus from a truncated one, built an index from it, and
    # promoted the alias onto a collection holding 38% of the registry. The
    # console said "re-run to resume" and the run exited 0.
    #
    # That is exactly the defect class this whole item exists to remove: a
    # silent loss upstream of every gate the pipeline measures. It was
    # pre-existing -- the `break` and the `return trials` are both original --
    # and it was invisible because nothing downstream knew what a complete
    # scrape looked like.
    #
    # Raising is right rather than harsh. The checkpoint is already on disk, so
    # re-running RESUMES from the saved page_token and costs nothing; the only
    # thing lost is the ability to promote a corpus nobody knows the size of.
    if not scrape_complete:
        raise IncompleteScrapeError(
            f"the scrape ended early with {len(trials):,} trial(s) and did NOT "
            f"reach the end of the result set. "
            f"Interruptions: {dict(SCRAPE_INTERRUPTIONS) or 'none recorded'}.\n"
            f"  The checkpoint at {checkpoint_file} has been KEPT — re-run this "
            f"command and the scrape resumes from where it stopped.\n"
            f"  Refusing to return a partial corpus: an index built from one "
            f"looks identical to an index built from a complete one, and the "
            f"trials it is missing are invisible to every downstream stage.")

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

    eligibility_text = eligibility.get("eligibilityCriteria", "")
    inclusion_text, exclusion_text, split_method = \
        split_inclusion_exclusion(eligibility_text)

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
        # DEFECT 3: criteria_split is a REAL FIELD, written here, not inferred
        # downstream. It rides into Qdrant inside full_trial_json like every
        # other key of this dict, so a downstream ingestion gate can assert on
        # it without re-implementing the splitter.
        "criteria_split":       split_method,
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


# ===========================================================================
# DEFECT 3: THE CRITERIA SPLIT, MEASURED BEFORE IT WAS CHANGED
# ===========================================================================
#
# MEASURED ON THE STORED 12,067-TRIAL CORPUS, by branch, before any edit:
#
#     both            11,218   92.96%
#     inclusion_only     299    2.48%   <- exclusion_criteria ends up EMPTY
#     exclusion_only     103    0.85%
#     neither            447    3.70%   <- exclusion_criteria ends up EMPTY
#
# THE BRIEF NAMED ONLY THE `neither` BRANCH (3.70%). The harm it describes --
# "the whole criteria text goes to inclusion and exclusion is empty" -- is
# produced by TWO branches, because `elif inclusion_start != -1` also sets
# exclusion_text = "". The real rate of trials reaching the judge with no
# exclusion section is 746, 6.18%, and 681 of those contain exclusion
# vocabulary, i.e. genuine exclusion criteria arriving under inclusion labels.
#
# WHY THE MARKERS MISSED: the old list required a COLON. "Inclusion Criteria"
# followed by a newline -- the single commonest heading style on
# ClinicalTrials.gov -- matched nothing. "Key Exclusion Criteria" matched only
# at the offset of the inner "Exclusion Criteria". And the substring search was
# unanchored, so "Patients must not be breastfeeding" INSIDE an inclusion
# bullet was taken as the start of the exclusion section: measured, that
# mis-cut 100 trials that the anchored search resolves correctly.
#
# THE POLICY IS BOTH, NOT EITHER. The brief offered a choice between "a richer
# marker list and a flag"; it also requires the unsplit state to be recorded
# per trial as a real field. Those are answers to different questions -- richer
# markers reduce the population, the flag records whatever survives -- and one
# does not substitute for the other. Measured outcome of doing both:
#
#     empty exclusion   746 (6.18%)  ->  213 (1.77%)
#     recovered                                533
#     LOST                                       0
#
# LOST == 0 IS A DESIGN CONSTRAINT, NOT AN OBSERVATION. The anchored search
# alone lost 116 splits that the old unanchored search found, because a real
# heading is not always at a line start. Losing a split is the same silent
# harm in a new place, so the anchored search FALLS BACK to the original
# substring markers whenever it finds nothing. The new split is therefore a
# strict superset of the old one by construction.
#
# ---------------------------------------------------------------------------
# 2026-08-10: THE EXCLUSION-HEADING FAMILIES, MEASURED ON THE 14,324-TRIAL
# CORPUS. The figures above are the older 12,067-trial scrape and are kept as
# history; these are today's.
#
# A SCAN OF THE 260 TRIALS THAT REACH THE JUDGE WITH NO EXCLUSION SECTION found
# that 153 of them carry a line naming exclusion, and that the misses fall into
# three families with a line-anchored heading the pattern could not describe.
# Each was counted on the corpus before it was added:
#
#     family                                        trials whose class changed
#     multi-level section number  "4.2 Exclusion Criteria"              13
#     sentence form               "The main exclusion criteria ..."      8
#     wrapper  "\[Exclusion Criteria\]" / "\<...\>" / "\- ..."          10
#     word prefix  main / general / core / case / participant           12
#     inclusion-side heading recovered (no exclusion family)             2
#     other                                                              1
#
#     both            14,034  97.975%  ->  14,075  98.262%
#     inclusion_only     178   1.243%  ->     166   1.159%
#     unsplit             82   0.572%  ->      51   0.356%
#     exclusion_only      30   0.209%  ->      32   0.223%
#
#     NO_EXCLUSION       260   1.815%  ->     217   1.515%
#     DEGRADED            82   0.572%  ->      51   0.356%
#     recovered                              46 trials changed class
#     LOST                                    0
#     `both` trials that changed class        0
#
# THE SUPERSET PROOF IS THE ACCEPTANCE CRITERION, not the recovery count, and
# it is re-run corpus-wide by tests/test_indexer_admission_filters.py section
# 3b against the pre-extension splitter lifted out of git.
#
# TWO SHAPES WERE MEASURED AND REJECTED, each because it mis-split a trial the
# pre-extension splitter split correctly -- the wrong side of the trade however
# many it recovers:
#
#   a bulleted sub-number ("* 3.1.2 Patients must not have received prior
#       treatment") let the existing `patients must not` alternative match an
#       INCLUSION bullet and cut NCT07178301's exclusion section early.
#   an escaped list terminator ("11\. Patients must not ...") did the same to
#       NCT06822010, for two recoveries.
#
# THE FUZZY MID-SENTENCE BOUNDARIES ARE A STATED LIMITATION, NOT A TARGET.
# "An individual who meets any of the following criteria will be excluded"
# and its relatives (~14 trials) have no heading to anchor on, and recognising
# them needs a different mechanism than a line-anchored pattern. They stay
# unrecognised deliberately, and the negative controls pin that.
#
# WHAT THIS DID NOT FIX, AND IT IS A DEFECT IN A DIFFERENT BRANCH. Three trials
# move `unsplit` -> `exclusion_only` (NCT06934382, NCT04581512, NCT05464082):
# their exclusion heading is now found and they have no inclusion heading at
# all, and the `exclusion_only` branch below DISCARDS everything before the
# exclusion start. That branch already drops leading text for 29 of the 30
# trials it classified before this change -- 76,052 characters, up to 12,438 in
# one trial -- so it is pre-existing rather than introduced here, and it is
# reported rather than repaired because repairing it moves 29 further trials.
#
# ---------------------------------------------------------------------------
# 2026-08-10 (SAME DAY, SEPARATE PASS): THAT DEFECT IS REPAIRED. The paragraph
# above is kept as the record of what was known when the families landed; the
# `exclusion_only` branch now keeps its leading text. Re-measured on the same
# 14,324-trial corpus AFTER the families were in, so these supersede the "29 of
# 30 / 76,052" figures above, which were the pre-families population:
#
#     exclusion_only trials                                     32
#     of which discarded leading text                           31
#     characters discarded                                  86,058
#     largest single discard        12,438  (NCT06330064, of 13,232 total)
#
# The three trials named above are in that population, and the character counts
# this file recorded for them (5,522 / 3,436 / 8,116) were their WHOLE criteria
# blocks rather than the discarded prefix. The prefixes are 3,561 / 1,777 /
# 4,914, and each is recovered in full.
#
#     both            14,075  98.262%  ->  14,106  98.478%
#     inclusion_only     166   1.159%  ->     166   1.159%
#     unsplit             51   0.356%  ->      51   0.356%
#     exclusion_only      32   0.223%  ->       1   0.007%
#
#     31 trials changed class, all of them exclusion_only -> both
#     characters recovered                              86,058
#     trials losing any text                                 0
#     `both` trials changing class                           0
#     `unsplit` / `inclusion_only` trials changing class     0
#
# The one survivor is the position-zero case: an exclusion heading with nothing
# above it has no inclusion text to recover, which is the branch behaving as it
# always did rather than a residue.
# ---------------------------------------------------------------------------

CRITERIA_SPLIT_BOTH = "both"
CRITERIA_SPLIT_INCLUSION_ONLY = "inclusion_only"
CRITERIA_SPLIT_EXCLUSION_ONLY = "exclusion_only"
CRITERIA_SPLIT_UNSPLIT = "unsplit"
CRITERIA_SPLIT_EMPTY = "empty_criteria"

CRITERIA_SPLIT_METHODS = Counter()

# The original markers. Retained verbatim as the FALLBACK, so no split the old
# code found can be lost. Substring, unanchored, colon-bearing.
_LEGACY_INCLUSION_MARKERS = ["inclusion criteria:", "inclusion:", "patients must have"]
_LEGACY_EXCLUSION_MARKERS = ["exclusion criteria:", "exclusion:", "patients must not"]

# A heading sits at the start of a line, optionally behind a bullet or a list
# number. Anchoring on that is what separates "Exclusion Criteria:" the heading
# from "...meets any exclusion criteria:" the sentence.
#
# THE WRAPPER CHARACTERS ARE BACKSLASH-ESCAPED IN THE STORED TEXT, which is
# measured rather than assumed: ClinicalTrials.gov markdown-escapes its
# punctuation, so this corpus holds "\<Exclusion Criteria\>" and
# "\[Exclusion Criteria\]" and "\- Exclusion Criteria", never the bare
# bracket. The backslash therefore sits in the class beside the bracket it
# escapes; without it the whole family stays unrecognised however many bracket
# characters are added. `>` was already here as a quote bullet.
#
# ONE LITERAL SET, and the regex class is DERIVED from it with re.escape rather
# than spelled a second time. _first_heading walks the same characters forward
# to find the heading word, and a hand-copied second spelling is exactly how
# the walk and the pattern drift apart -- a walk that stops early leaves the
# section starting with "\<" instead of "Exclusion".
_HEADING_LEAD_CHARS = "\\-*#<>[•"

# THE LIST NUMBER IN TWO SHAPES, AND THE ASYMMETRY IS MEASURED, NOT STYLISTIC.
#
#   after a bullet -- only the ORIGINAL single-level "1." / "1)". A bullet
#       followed by a dotted number is a LIST ITEM and not a section heading:
#       "* 3.1.2 Patients must not have received prior treatment" is an
#       INCLUSION bullet, and admitting a dotted number there let the existing
#       `patients must not` alternative match it, cutting NCT07178301's
#       exclusion section early against a correct "Exclusion Criteria:"
#       heading further down. Measured on the stored corpus, both directions.
#
#   at the margin, with no bullet -- multi-level "4.2", "4.1.2", "2.0", "5.2."
#       as well. Every section-numbered exclusion heading measured in this
#       corpus is at the margin, and none of them carries a bullet.
#
# Bounded to digits, dots and ONE closing dot or parenthesis, and nothing else.
# An ESCAPED terminator ("11\. Patients must not ...") is deliberately NOT
# admitted: it recovers two trials and mis-splits one that the pre-extension
# splitter split correctly, which is the wrong side of that trade.
_HEADING_LEAD_NUMBER = r"(?:\d+(?:\.\d+)+[.)]?|\d+[.)])"

_HEADING_LEAD_CLASS = "[" + "".join(re.escape(c) for c in _HEADING_LEAD_CHARS) + "]"

_HEADING_LEAD = (r"(?:^|\n)[ \t]*"
                 r"(?:" + _HEADING_LEAD_CLASS + r"+[ \t]*(?:\d+[.)][ \t]*)?"
                 r"|" + _HEADING_LEAD_NUMBER + r"[ \t]*)?")

# Everything _HEADING_LEAD can consume before the heading word, as a plain
# character set for _first_heading's walk. Same source as the class above.
_HEADING_LEAD_STRIP = "\n \t" + _HEADING_LEAD_CHARS

# Longest alternatives FIRST: "key exclusion criteria" must win over the
# "exclusion criteria" nested inside it, or the heading is cut four characters
# late. Python's `|` is first-match, not longest-match.
#
# THE WORD-PREFIXED ALTERNATIVES ARE MEASURED FAMILIES, one side at a time.
# A prefixed heading ("Main Exclusion Criteria") matches nothing today, because
# the lead requires a line start and the bare "exclusion criteria" inside it is
# five characters in. Each prefix below was counted across the whole corpus on
# BOTH sides before it was added, and the inclusion side carries only the
# prefixes the corpus actually shows:
#
#     prefix         inclusion trials   exclusion trials   added
#     the main               50                 49         both sides
#     main                   27                 30         both sides
#     general                24                 18         both sides
#     core                    6                  6         both sides
#     participant            11                 12         both sides
#     case                    0                  1         EXCLUSION ONLY
#
# `case` is the one asymmetry and it is deliberate: there is no
# "Case Inclusion Criteria" anywhere in this corpus, and inventing one would be
# a pattern with no evidence behind it. THE SYMMETRY MATTERS BEYOND TIDINESS --
# an exclusion heading recovered while its inclusion counterpart stays
# unmatched turns an `unsplit` trial into `exclusion_only`, and that branch
# discards everything before the exclusion heading. See the split-measurement
# block above for the three trials where that still happens.
_INCLUSION_HEADINGS = [
    r"the\s+main\s+inclusion\s+criteria",
    r"participant\s+inclusion\s+criteria",
    r"general\s+inclusion\s+criteria",
    r"main\s+inclusion\s+criteria",
    r"core\s+inclusion\s+criteria",
    r"key\s+inclusion\s+criteria", r"inclusion\s+criteria", r"inclusion",
    r"patients\s+must\s+have", r"eligibility\s+criteria",
    r"eligible\s+patients", r"inclusion\s+guidelines",
]
_EXCLUSION_HEADINGS = [
    r"the\s+main\s+exclusion\s+criteria",
    r"participant\s+exclusion\s+criteria",
    r"general\s+exclusion\s+criteria",
    r"main\s+exclusion\s+criteria",
    r"core\s+exclusion\s+criteria",
    r"case\s+exclusion\s+criteria",
    r"key\s+exclusion\s+criteria", r"exclusion\s+criteria", r"exclusion",
    r"patients\s+must\s+not", r"exclusionary\s+criteria",
    r"ineligibility\s+criteria", r"non-?inclusion\s+criteria",
    r"exclusion\s+guidelines", r"excluded\s+patients", r"patients?\s+excluded",
]


def _compile_headings(alternatives):
    return re.compile(_HEADING_LEAD + r"(?:" + "|".join(alternatives) + r")\b",
                      re.IGNORECASE)


_INCLUSION_RE = _compile_headings(_INCLUSION_HEADINGS)
_EXCLUSION_RE = _compile_headings(_EXCLUSION_HEADINGS)


def _first_heading(pattern, text: str) -> int:
    """Offset of the first line-anchored heading, or -1.

    The match starts at the newline/bullet/number that _HEADING_LEAD consumed,
    so the offset is walked forward to the first character of the heading
    WORD -- otherwise every section would begin with the previous section's
    line break and the bullet that introduced it.
    """
    m = pattern.search(text)
    if not m:
        return -1
    i = m.start()
    while i < len(text) and text[i] in _HEADING_LEAD_STRIP:
        i += 1
    while i < len(text) and (text[i].isdigit() or text[i] in ".)"):
        i += 1
    while i < len(text) and text[i] in " \t":
        i += 1
    return i


def _legacy_marker_position(text_lower: str, markers) -> int:
    """The old unanchored substring search, byte-for-byte in behaviour."""
    position = -1
    for marker in markers:
        pos = text_lower.find(marker)
        if pos != -1 and (position == -1 or pos < position):
            position = pos
    return position


def _section_start(pattern, markers, criteria_text: str, text_lower: str) -> int:
    """Anchored heading if there is one, else the legacy substring match."""
    pos = _first_heading(pattern, criteria_text)
    if pos != -1:
        return pos
    return _legacy_marker_position(text_lower, markers)


def split_inclusion_exclusion(criteria_text: str) -> tuple:
    """Split eligibility criteria into inclusion and exclusion sections.

    Returns:
        (inclusion_text, exclusion_text, split_method)

    THE RETURN IS A 3-TUPLE AS OF THIS ITEM. The third member is one of the
    CRITERIA_SPLIT_* constants and is stored per trial, because the unsplit
    state has to be a real recorded field rather than something a later
    consumer re-derives -- a downstream ingestion gate needs it as a standing
    check, and re-deriving it means re-implementing this function, which is how
    the two copies in this repository drifted apart in the first place.
    """
    if not criteria_text or not criteria_text.strip():
        CRITERIA_SPLIT_METHODS[CRITERIA_SPLIT_EMPTY] += 1
        return "", "", CRITERIA_SPLIT_EMPTY

    text_lower = criteria_text.lower()

    inclusion_start = _section_start(
        _INCLUSION_RE, _LEGACY_INCLUSION_MARKERS, criteria_text, text_lower)
    exclusion_start = _section_start(
        _EXCLUSION_RE, _LEGACY_EXCLUSION_MARKERS, criteria_text, text_lower)

    if inclusion_start != -1 and exclusion_start != -1:
        if inclusion_start < exclusion_start:
            inclusion_text = criteria_text[inclusion_start:exclusion_start].strip()
            exclusion_text = criteria_text[exclusion_start:].strip()
        else:
            exclusion_text = criteria_text[exclusion_start:inclusion_start].strip()
            inclusion_text = criteria_text[inclusion_start:].strip()
        method = CRITERIA_SPLIT_BOTH
    elif inclusion_start != -1:
        inclusion_text = criteria_text[inclusion_start:].strip()
        exclusion_text = ""
        method = CRITERIA_SPLIT_INCLUSION_ONLY
    elif exclusion_start != -1:
        # THE LEADING TEXT IS KEPT (2026-08-10). This branch used to discard
        # everything before the exclusion heading, and that discarded text is
        # the trial's inclusion criteria written without a heading -- it
        # vanished from the payload, from the dense embedding input, and from
        # what Stage 5 is shown. Measured on the 14,324-trial corpus: 31 of the
        # 32 trials this branch classified lost text, 86,058 characters, up to
        # 12,438 in one trial (NCT06330064).
        #
        # THE LABEL IS A BEST-EFFORT ASSIGNMENT, NOT A DETECTED HEADING, and
        # that distinction is the whole argument for it. No inclusion heading
        # was found; what is known is that a non-empty block of criteria text
        # sits above a heading that says everything below it is exclusionary,
        # so everything above it is what the trial requires. `both` is what a
        # trial with both sections present IS, and inventing a sixth constant
        # for "both, one side unlabelled" would put a value in the closed
        # vocabulary that the ingestion gate and every consumer would have to
        # learn in order to treat it exactly like `both`.
        #
        # This is the unsplit branch's own keep-and-show reasoning applied one
        # branch over: text the model can read under an imprecise label beats
        # text it can never see, which is this project's false-keep-over-
        # false-drop rule. An empty prefix still resolves to exclusion_only --
        # a heading at position zero genuinely has nothing above it.
        #
        # THE DECISION AND THE TEXT ARE STRIPPED DIFFERENTLY, AND THAT IS THE
        # WHOLE OF IT. _first_heading walks FORWARD over the bullet, wrapper or
        # list number that _HEADING_LEAD consumed, so those characters belong
        # to the heading and are left behind in the prefix by construction: a
        # criteria block opening "* Exclusion Criteria" has a prefix of "* ",
        # and a whitespace-only .strip() leaves "*" -- truthy, and a one-
        # character "inclusion section" that says nothing. So the DECISION
        # strips the lead characters too. The TEXT does not: .strip() removes
        # whitespace only, so a real section keeps every character it has,
        # including a leading bullet of its own. Found by the bullet case in
        # tests/test_indexer_admission_filters.py section 3c, not by reading.
        prefix = criteria_text[:exclusion_start].strip()
        exclusion_text = criteria_text[exclusion_start:].strip()
        if prefix.strip(_HEADING_LEAD_STRIP):
            inclusion_text = prefix
            method = CRITERIA_SPLIT_BOTH
        else:
            inclusion_text = ""
            method = CRITERIA_SPLIT_EXCLUSION_ONLY
    else:
        # UNSPLIT. The trial is KEPT -- excluding it would delete a trial to
        # fix a labelling bug, which is the silent drop this item removes --
        # and the state is recorded so a consumer can see that this trial's
        # "inclusion" text is really its whole criteria block.
        inclusion_text = criteria_text
        exclusion_text = ""
        method = CRITERIA_SPLIT_UNSPLIT

    CRITERIA_SPLIT_METHODS[method] += 1
    return inclusion_text, exclusion_text, method


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
        # WHAT THE API SAYS IT BILLED, recorded rather than discarded.
        #
        # The batch size is derived from a chars/4 token PROXY, so this module
        # has never known what an index build actually cost -- only what it
        # guessed. An estimate that is never compared with a bill is a number
        # nobody can be wrong about. Counted per call and reported by
        # index_trials(); a response without usage is counted apart rather than
        # treated as zero, because zero is also what a free call looks like.
        usage = getattr(response, "usage", None)
        if usage is not None and getattr(usage, "prompt_tokens", None) is not None:
            EMBEDDING_USAGE["prompt_tokens"] += usage.prompt_tokens
            EMBEDDING_USAGE["calls"] += 1
        else:
            EMBEDDING_USAGE["calls_without_usage"] += 1
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


# ===========================================================================
# THE SPLIT IS RE-DERIVED AT INDEX TIME, NOT TRUSTED FROM THE CORPUS FILE
# ===========================================================================
#
# THE HAZARD, MEASURED RATHER THAN IMAGINED. There are two ways into
# index_trials and only one of them has just run the splitter:
#
#   main()                     scrape -> parse_trial_metadata -> index_trials
#   the generated Airflow DAG  json.load(trials_latest.json)  -> index_trials
#
# The second hands over trials whose split was computed by whatever the
# splitter was on the day of the scrape. Every change to it after that point --
# a heading family, a branch repair -- rebuilds the index with the STALE split,
# silently, because a stored `criteria_split` string is indistinguishable from
# a freshly computed one. The heading pass hit exactly this and worked around
# it by rewriting the corpus file by hand, which is a manual step nobody will
# repeat and which the DAG cannot perform at all.
#
# WHY HERE AND NOT IN THE DAG. Putting the recompute in the generated DAG puts
# a second copy of this logic in a file built as a string -- the shape that let
# the DAG's old private scraper drift until it was building a strictly worse
# index. tests/test_indexer_criteria_split_gate.py asserts the DAG carries no
# criteria_split logic of its own, and both entry paths converge on
# index_trials, so one call here covers both by construction.
#
# EVERY FIELD DERIVED FROM THE SPLIT IS RECOMPUTED, NOT JUST THE SPLIT.
# Recomputing the split alone recreates the same disagreement one level down:
# the two enrichments below read the inclusion and exclusion sections, so a
# trial that recovers inclusion text can gain stage requirements and histology
# tags it never had, and a stored enrichment computed from the old sections
# would contradict the sections stored beside it.
#
#   RECOMPUTED HERE, because it is baked into the stored corpus:
#     eligibility.inclusion_criteria    split output
#     eligibility.exclusion_criteria    split output
#     criteria_split                    the method, read by the ingestion gate
#     structured_eligibility            title + INCLUSION, capped by EXCLUSION
#     histology_tags                    title + INCLUSION
#
#   ALREADY INDEX-TIME, so it follows for free once the above are current:
#     the dense embedding input         create_trial_embedding_text() reads
#                                       inclusion and exclusion as sections
#     the `bm25_text` payload           the same string
#     the three BM25 sparse fields      create_trial_bm25_fields() reads
#                                       criteria_text, title, conditions and
#                                       interventions -- NONE of them split-
#                                       derived, so they are unaffected either
#                                       way. Named here because "unaffected"
#                                       is a measurement, not an omission.
#
# THE SOURCE IS eligibility.criteria_text, which the splitter never modifies,
# so this is idempotent: running it on an already-current corpus changes
# nothing and running it twice is the same as running it once.
CRITERIA_RENORMALIZED = Counter()


def renormalize_criteria_derived_fields(trials: List[Dict]) -> dict:
    """Recompute every stored field derived from the criteria split, in place.

    Returns a dict of the counts, and records them in CRITERIA_RENORMALIZED.

    A trial whose `eligibility` is not a mapping is COUNTED AND SKIPPED rather
    than repaired: there is no criteria_text to split, and manufacturing the
    key would write a shape the parser never produces. Third-party data that
    cannot be read is counted; it does not raise.
    """
    counts = Counter()
    for trial in trials:
        counts["trials"] += 1
        eligibility = trial.get("eligibility")
        if not isinstance(eligibility, dict):
            counts[f"skipped:eligibility_{type(eligibility).__name__}"] += 1
            continue

        # THE TWO MUTABLE FIELDS ARE SNAPSHOT-COPIED, NOT REFERENCED. Both
        # enrichers below ASSIGN a fresh object today, so a reference would
        # compare correctly -- but an enricher that ever mutated its dict or
        # list in place would leave this comparing an object with itself and
        # reporting "unchanged" forever, which is a check that has silently
        # stopped checking. A shallow copy is enough: the stage values are
        # scalars and the tags are strings.
        _se = trial.get("structured_eligibility")
        _ht = trial.get("histology_tags")
        before = (eligibility.get("inclusion_criteria"),
                  eligibility.get("exclusion_criteria"),
                  trial.get("criteria_split"),
                  dict(_se) if isinstance(_se, dict) else _se,
                  list(_ht) if isinstance(_ht, list) else _ht)

        inclusion_text, exclusion_text, split_method = \
            split_inclusion_exclusion(eligibility.get("criteria_text") or "")
        eligibility["inclusion_criteria"] = inclusion_text
        eligibility["exclusion_criteria"] = exclusion_text
        trial["criteria_split"] = split_method

        enrich_structured_eligibility(trial)
        enrich_histology_tags(trial)

        for key, was, now in (
            ("inclusion_criteria", before[0], inclusion_text),
            ("exclusion_criteria", before[1], exclusion_text),
            ("criteria_split", before[2], split_method),
            ("structured_eligibility", before[3],
             trial.get("structured_eligibility")),
            ("histology_tags", before[4], trial.get("histology_tags")),
        ):
            if was != now:
                counts[f"changed:{key}"] += 1

    CRITERIA_RENORMALIZED.update(counts)
    return dict(counts)


class EmbeddingBudgetExceeded(RuntimeError):
    """The corpus would cost more to embed than the caller authorised.

    Raised BEFORE the first embedding call. A RuntimeError subclass for the
    same reason as UnknownModelPricingError: a spend refusal must not be
    swallowed by a caller reaching for a narrower exception.
    """


def estimate_embedding_cost(trials: List[Dict]) -> dict:
    """Exact token count and cost for embedding `trials`. Calls no API.

    Uses tiktoken when available -- the real encoder for the configured model,
    so the number is what will be billed rather than a proxy. Falls back to the
    chars/4 heuristic the batch sizer uses, and SAYS WHICH, because a estimate
    presented without its method invites the reader to trust the wrong digits.

    The import is inside the function on the project's standing exemption for
    third-party imports in function bodies: hoisting it would make importing
    the indexer pull in tiktoken for every caller that never costs anything.
    """
    texts = [create_trial_embedding_text(t) for t in trials]
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model(EMBEDDING_MODEL)
        tokens = sum(len(enc.encode(t)) for t in texts)
        method = "tiktoken"
    except Exception as exc:  # noqa: BLE001 - recorded, never silent
        EMBEDDING_USAGE[f"estimate_fallback:{type(exc).__name__}"] += 1
        tokens = sum(len(t) for t in texts) // 4
        method = "chars/4 (tiktoken unavailable)"
    return {"tokens": tokens, "method": method, "trials": len(trials),
            "cost_usd": get_model_cost(EMBEDDING_MODEL, tokens, 0)}


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
    # RE-DERIVE THE SPLIT AND EVERYTHING BELOW IT, for both entry paths.
    # See renormalize_criteria_derived_fields() for why this is here and not
    # in the DAG, and for the full list of what is recomputed.
    #
    # THE CENSUS COUNTER IS CLEARED FIRST, and that is deliberate. Every call
    # to the splitter increments CRITERIA_SPLIT_METHODS, so without this a
    # scrape-then-index run would count each trial twice -- and the counter is
    # documented as one entry per trial. Cleared IN PLACE, never rebound: the
    # readers hold the object (see oncotriage/degradation.py's note on that).
    # The scrape's own census line has already been printed by this point, and
    # what the counter holds afterwards is the census of what was INDEXED,
    # which is the more useful of the two and the only one the DAG path has.
    # ------------------------------------------------------------------
    CRITERIA_SPLIT_METHODS.clear()
    _renorm = renormalize_criteria_derived_fields(trials)
    _renorm_changed = {k[len("changed:"):]: v for k, v in _renorm.items()
                       if k.startswith("changed:")}
    console.out(f"Criteria split re-derived at index time: "
                f"{dict(CRITERIA_SPLIT_METHODS)}")
    if _renorm_changed:
        console.out(f"  STORED FIELDS WERE STALE and have been recomputed: "
                    f"{_renorm_changed}")
    else:
        console.out("  every stored field already agreed with the current "
                    "splitter; nothing was recomputed away.")
    _renorm_skipped = {k: v for k, v in _renorm.items()
                       if k.startswith("skipped:")}
    if _renorm_skipped:
        console.out(f"  WARNING: trials whose eligibility could not be read: "
                    f"{_renorm_skipped}")
    log.info("criteria split re-derived", total=_renorm.get("trials", 0),
             collection=collection_name)

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

    # --- WHAT THIS ACTUALLY COST, from the API's own usage numbers ----------
    _billed = EMBEDDING_USAGE.get("prompt_tokens", 0)
    if _billed:
        _cost = get_model_cost(EMBEDDING_MODEL, _billed, 0)
        console.out(f"  Embedding tokens BILLED: {_billed:,} over "
                    f"{EMBEDDING_USAGE.get('calls', 0):,} API call(s)")
        console.out(f"  Embedding cost: ${_cost:.4f}")
        log.info("embedding usage", model=EMBEDDING_MODEL, tokens_in=_billed,
                 calls=EMBEDDING_USAGE.get("calls", 0), cost_usd=round(_cost, 6))
    # WHAT THE RE-DERIVATION FOUND, cumulative over this process. The per-call
    # numbers were printed above; this is the counter's reader, and it exists
    # because a counter nothing reads is the dead declaration
    # tests/test_package_invariants.py check 2h reports -- a docstring naming
    # it satisfies that scan without anyone ever seeing the number.
    _renorm_total = sum(v for k, v in CRITERIA_RENORMALIZED.items()
                        if k.startswith("changed:"))
    if _renorm_total:
        console.out(f"  Stale split-derived fields corrected this process: "
                    f"{_renorm_total:,} field(s) across "
                    f"{CRITERIA_RENORMALIZED.get('trials', 0):,} trial(s) — "
                    f"{dict(CRITERIA_RENORMALIZED)}")

    if EMBEDDING_USAGE.get("calls_without_usage"):
        # Reported, never folded into the total: a missing usage block and a
        # zero-token call are different facts and only one is free.
        console.out(f"  WARNING: {EMBEDDING_USAGE['calls_without_usage']} embedding "
                    f"call(s) returned no usage block — the billed total above "
                    f"EXCLUDES them and is therefore a floor, not a total.")

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
        

# ===========================================================================
# DEFECT 4: VERIFY BEFORE THE SWAP, AND KEEP A ROLLBACK TARGET
# ===========================================================================
#
# main() used to run create -> index -> payload index -> swap -> cleanup(1).
# Two independent faults in that order:
#
#   * NOTHING WAS VERIFIED. The alias moved onto the staging collection on the
#     strength of index_trials() having returned. A run that embedded half the
#     corpus and then lost its connection, or wrote points with an empty dense
#     vector, promoted itself into production identically to a good one.
#   * THE ROLLBACK WAS DESTROYED. cleanup_old_collections(keep_recent=1) runs
#     immediately after the swap and keeps only the newest timestamped
#     collection -- which is the one just promoted. The previous good
#     collection was deleted seconds after the new one went live and before
#     anybody could have observed the new one serving traffic.
#
# WHAT VERIFICATION CANNOT DO HERE, STATED PLAINLY. The strengthened index
# validator this ought to call does not exist, and everything below is built
# from what a freshly-built collection can be asked about itself for free.
#
# THE ONE CONTENT CHECK, AND ITS EXACT SCOPE. This block used to disclaim
# content checking outright; that became false when the criteria_split
# ingestion gate landed as check 9. What the gate reads is ONE payload field,
# `criteria_split`, which records how split_inclusion_exclusion resolved each
# trial's eligibility block, and it gates on the aggregate distribution of that
# field against three absolute ceilings. It is not a judgement about any
# individual trial's text and it does not read the criteria themselves: a trial
# whose exclusion section is labelled `both` but is nonsense passes.
#
# Everything else this does NOT check is unchanged:
#
#   - RETRIEVAL QUALITY. Nothing here judges relevance. A collection whose
#     embeddings are uniformly wrong but well-formed passes every check below.
#   - THAT EACH VECTOR BELONGS TO ITS TRIAL. Detecting a shuffle would mean
#     re-embedding sampled trials and comparing, which is a paid API call, so
#     it is deliberately not done. The per-point checks confirm a dense vector
#     of the right dimension EXISTS beside the right payload, not that it
#     encodes that payload.
#   - THAT THE BM25 VOCABULARY MATCHES THE QUERY SIDE. Both sides reach one
#     construction site (oncotriage/embedding.py) and File 47 check 2f asserts
#     that statically; this run-time check only proves the sparse vectors are
#     non-empty and searchable.
#   - COMPLETENESS AGAINST ClinicalTrials.gov. The expected count is what this
#     process scraped, so a scrape that silently missed 40% of the registry
#     verifies clean.
#   - WHETHER THE SPLIT IS CORRECT, only how it was reached. A trial whose
#     exclusion heading the splitter matched is counted `both` regardless of
#     whether the resulting sections are right, and the distribution gate is
#     blind to a regression that keeps the branch counts steady.
#   - ANYTHING ABOUT THE PREVIOUS DISTRIBUTION. The ceilings are absolute
#     numbers, on purpose: the weekly DAG stores no baseline, a first build has
#     no history, and a relative gate decays into "whatever ran last time is
#     correct". A slow drift that stays under a ceiling forever is invisible
#     to the gate and visible only in the distribution it reports every run.
#
# Two things it once could not do and now can. The size-vs-live comparison
# arrived with the `compare_to` argument -- a corpus that collapsed from 12,000
# trials to 300 used to pass if all 300 were well-formed -- and the criteria
# split distribution arrived with check 9. Both were "REPORTING IS NOT
# CHECKING" entries here, and each stopped being one when something gated
# on it.

# How many points are pulled back and inspected individually.
_VERIFY_SAMPLE_SIZE = 25

# How long to wait for a `yellow` collection to finish optimizing, and how
# often to re-ask. 300s covers a 14k-point bulk build comfortably; the observed
# time to green on this corpus was under two minutes.
_STATUS_WAIT_SECONDS = 300
_STATUS_POLL_SECONDS = 10

# A staging collection holding less than this fraction of the collection it
# would replace fails verification. 0.90 admits ordinary registry churn -- the
# measured four-day drift on this corpus was 43 trials removed against 68 added,
# well under 1% -- and refuses the 45% collapse that actually shipped.
_MIN_CORPUS_RATIO = 0.90

# The three named sparse vectors every point must carry.
_SPARSE_VECTOR_NAMES = ("title-bm25", "conditions-bm25", "criteria-bm25")

# How many points one scroll page pulls back for the criteria_split census.
# The census asks for ONE nested payload key, so a page is small however large
# the stored trial is; 1000 keeps the round trip count at ~15 on this corpus.
_CRITERIA_SPLIT_PAGE = 1000

# What the census asks Qdrant for. A nested path INSIDE the stored trial blob,
# because that is where criteria_split lives -- it is not a top-level payload
# field, which is also why no server-side filter or facet can count it.
_CRITERIA_SPLIT_SELECTOR = ["full_trial_json.criteria_split"]


# Attempts per page before a fetch is treated as fatal to the scrape, and the
# per-request timeout. Five attempts with 2/4/8/16s backoff covers ~30s of
# endpoint unavailability; the timeout is 60s rather than the original 30s
# because every observed failure here was a READ timeout on a slow page, not a
# refused connection.
_SCRAPE_PAGE_ATTEMPTS = 5
_SCRAPE_PAGE_TIMEOUT = 60

SCRAPE_RETRIES = Counter()
"""Page fetches that failed and were retried, keyed by exception type.

Distinct from SCRAPE_INTERRUPTIONS, which counts only faults that ENDED a
scrape. A run that finished after eleven retries and one that never stumbled
produce the same corpus and must not produce the same record: the first is an
endpoint degrading, and the only place that is visible is here.
"""

SCRAPE_INTERRUPTIONS = Counter()
"""Network faults that ended a scrape early, keyed by exception type.

Read by the IncompleteScrapeError raised at the end of
scrape_clinicaltrials_gov, so the operator is told what stopped it and not
merely that something did.
"""


class IncompleteScrapeError(RuntimeError):
    """The scrape did not reach the end of the result set.

    A RuntimeError subclass and deliberately NOT a
    requests.exceptions.RequestException: the whole point is that it must not
    be caught by the same `except` that swallowed the underlying network fault.
    """


EMBEDDING_USAGE = Counter()
"""What the embedding API reported it billed, across this process.

`prompt_tokens` is the API's own number, not the chars/4 proxy the batch size
is derived from. `calls_without_usage` counts responses that carried no usage
block at all -- kept apart from a genuine zero, because an estimate compared
against a silently-missing bill is worse than no comparison.
"""

CLEANUP_FAILURES = Counter()
"""Old-collection deletions that failed, keyed by exception type.

Continuing is right -- a stale collection costs storage and nothing else -- but
a cleanup that failed on every collection and one with nothing to do print the
same thing, so the failures are counted as well as logged.
"""


class IndexVerificationError(RuntimeError):
    """A staging collection failed a pre-swap check. The alias is not moved.

    A RuntimeError subclass and deliberately not a ValueError or a KeyError:
    the failures below must not be swallowed by a caller reaching for a
    narrower exception, on the same argument as UnknownModelPricingError.
    """


# ===========================================================================
# THE INGESTION GATE: criteria_split, READ BACK OUT OF THE INDEX
# ===========================================================================
#
# `criteria_split` has been written by parse_trial_metadata since defect 3 and
# READ BY NOTHING. A trial whose exclusion criteria arrive under inclusion
# labels produces inverted verdicts at Stage 5 -- the judge is told "the patient
# must have" what the sponsor wrote as "the patient must not have" -- and no
# downstream stage can tell, because the criteria block is well-formed text
# either way. The flag records which branch produced it; this is what reads it.
#
# WHY IT LIVES INSIDE verify_collection RATHER THAN BESIDE IT. The escape then
# comes free: verify_collection already runs before swap_alias_atomic on both
# call paths (main() and the generated DAG's rebuild_index task), and its raise
# already means "the alias was not moved and the previous collection is still
# serving". A separate gate would need its own call site on both paths, and the
# path that forgot it would promote silently -- which is the shape of defect 4.
#
# THE CHECKS ARE ABSOLUTE, NOT RELATIVE. There is deliberately no comparison
# with last week's distribution: the weekly DAG keeps no baseline store, the
# first run of any new deployment has no history at all, and a
# relative-to-previous gate degrades gracefully into "whatever we did last time
# is correct". The thresholds below are fixed numbers derived from a census of
# the live collection, with stated headroom, and they are what a schema change
# at ClinicalTrials.gov would blow through.

# Census categories the SPLITTER CANNOT PRODUCE. They describe a point rather
# than a criteria block, so they are deliberately not CRITERIA_SPLIT_* members
# of split_inclusion_exclusion's closed return vocabulary.
#
# An absent field is not the same finding as an unreadable payload -- the first
# says the point predates the splitter contract, the second says the payload
# itself is damaged -- so they are counted apart even though the gate treats
# both as "no usable verdict".
CRITERIA_SPLIT_FIELD_ABSENT = "field_absent"
CRITERIA_SPLIT_PAYLOAD_UNREADABLE = "payload_unreadable"

# The closed vocabulary split_inclusion_exclusion can return. Any other value
# in the index came from something that is not this splitter.
CRITERIA_SPLIT_VALUES = (
    CRITERIA_SPLIT_BOTH,
    CRITERIA_SPLIT_INCLUSION_ONLY,
    CRITERIA_SPLIT_EXCLUSION_ONLY,
    CRITERIA_SPLIT_UNSPLIT,
    CRITERIA_SPLIT_EMPTY,
)

# --- The thresholds, and the census they come from ---------------------------
#
# EVERY NUMBER BELOW WAS MEASURED BEFORE IT WAS CHOSEN. The live collection
# `trial_criteria_20260810_125943` was scrolled in full on 2026-08-10 through
# scroll_criteria_split_distribution() -- 14,324 points, census total equal to
# the server's exact count, and identical to the same census over the on-disk
# `trials_latest.json` the collection was built from:
#
#     both              14,075   98.262%
#     inclusion_only       166    1.159%
#     unsplit               51    0.356%
#     exclusion_only        32    0.223%
#     empty_criteria         0    0.000%
#     field_absent           0    0.000%
#     payload_unreadable     0    0.000%
#
# THE CEILINGS DID NOT MOVE. The exclusion-heading families widened the
# splitter on 2026-08-10 and every gated fraction FELL -- degraded 0.572% ->
# 0.356%, no-exclusion 1.815% -> 1.515% -- so the headroom arguments below hold
# a fortiori and re-deriving the thresholds from a corpus they already pass
# would only ratchet them toward whatever ran last, which is the relative gate
# this one exists not to be. The census they were originally derived from,
# `trial_criteria_20260807_111807` on 2026-08-09, is kept as history: both
# 14,034 / inclusion_only 178 / unsplit 82 / exclusion_only 30, degraded
# 0.572%, no-exclusion 1.815%. That collection is still the rollback target.
#
# THE THREE FIGURES IN CIRCULATION RECONCILE ONCE EACH IS READ AS A DIFFERENT
# POPULATION, and two of them are reproduced exactly by this census:
#
#   82          the `unsplit` branch alone, on the 2026-08-09 census.
#               Reproduced: 82. Today's is 51.
#   1.82%       the EMPTY-EXCLUSION population -- unsplit + inclusion_only --
#               on the 2026-08-09 census. Reproduced: 260/14,324 = 1.815%.
#               Today's is 217/14,324 = 1.515%.
#   6.18% ->    the same empty-exclusion population on the older 12,067-trial
#   1.77%       scrape, before and after the marker fix. NOT reproducible from
#               storage: `trial_criteria_20260803_104642` was censused too and
#               is 12,067/12,067 field_absent -- it was built before this field
#               existed -- so those two are process-time measurements over a
#               scrape, not over any stored index.
#
# DEGRADED = unsplit + empty_criteria = 51, 0.356% (was 82, 0.572%, before the
# exclusion-heading families).
#
# 3.0% is that with 8.4x headroom -- it was 5.2x when the ceiling was chosen,
# and the extension widened the margin rather than the ceiling. The headroom is
# set by what can move the
# fraction rather than by taste. This population is effectively bimodal: a
# splitter that stops finding headings sends it to ~100%, while ordinary
# registry churn moves it by single trials -- the measured four-day drift on
# this corpus was 43 trials removed against 68 added, of which ~0.6% would be
# unsplit. In absolute terms 3.0% is 430 trials against a measured 51, so
# reaching it through churn alone would take tens of thousands of new trials,
# and it still sits an order of magnitude below a collapse. A tighter ceiling
# buys sensitivity to a new heading format used by 1-2% of the registry; a
# looser one stops separating the two scenarios at all.
_MAX_CRITERIA_SPLIT_DEGRADED = 0.03

# NO_EXCLUSION = unsplit + inclusion_only = 217, 1.515% (was 260, 1.815%,
# before the exclusion-heading families).
#
# THIS THRESHOLD IS AN ADDITION TO THE BRIEF THIS GATE WAS BUILT FROM, and the
# reason is a measurement rather than a preference. The brief gates the
# degraded fraction on unsplit + empty_criteria, on the argument that "a schema
# change at ClinicalTrials.gov that breaks the splitter shows up as unsplit
# exploding". That is true only when BOTH heading families stop matching.
# split_inclusion_exclusion searches for the two independently, so a change to
# the EXCLUSION heading alone leaves every inclusion heading matching, produces
# `inclusion_only` rather than `unsplit`, and the degraded fraction does not
# move at all -- while every affected trial reaches the judge with its
# exclusion criteria silently relabelled as inclusion criteria, which is the
# exact harm this gate exists to catch. `inclusion_only` is already three times
# the size of `unsplit` on this corpus for that reason (166 against 51; it was
# twice, 178 against 82, before the exclusion-heading families -- which is the
# same argument reading louder, since those families were exclusion-side).
#
# It is a SEPARATE fraction rather than a widening of the degraded one because
# the two populations are not equally suspicious: a trial that genuinely
# registers no exclusion section is legitimately `inclusion_only`, so this
# fraction has a real, corpus-composition-driven floor that `unsplit` does not.
# 5.0% is 3.3x the measured 1.515%, or 716 trials against a measured 217 -- it
# was 2.75x against 260 when the ceiling was chosen; a single-heading regression
# takes it above 90%.
_MAX_CRITERIA_SPLIT_NO_EXCLUSION = 0.05

# UNUSABLE = field_absent + payload_unreadable + any value outside the closed
# vocabulary. Measured at 0.00% -- every one of the 14,324 live points carries
# a recognised verdict -- so this one has no measurement to add headroom to.
#
# IT IS NOT A HYPOTHETICAL POPULATION. The census over
# `trial_criteria_20260803_104642` reports 12,067 of 12,067 field_absent: that
# collection was indexed before parse_trial_metadata stamped the field, and
# this gate refuses it. That is the correct verdict rather than an awkward one
# -- nothing downstream can tell whether any of those 12,067 trials had its
# exclusion criteria labelled correctly.
#
# 0.5% rather than 0.0% because the gate must not fire on a handful of points
# whose payload was truncated in transit, which is a Qdrant fault and not an
# ingestion-contract fault. Note what 0.0 would cost: one damaged payload out
# of 14,324 would refuse an otherwise perfect corpus, and the operator's only
# remedy would be to edit this constant. Below 0.5%, any population written by
# something that does not stamp the field is still refused.
_MAX_CRITERIA_SPLIT_UNUSABLE = 0.005

# One sentence, used by both raise sites, so the two cannot say different
# things about what happened to the alias.
_SWAP_REFUSED_NOTE = (
    "The alias was NOT moved: the swap is refused and the PREVIOUS COLLECTION "
    "IS STILL SERVING traffic. Inspect the staging collection, or delete it "
    "and re-run.")


def scroll_criteria_split_distribution(collection_name: str, client=None,
                                       page_size: int = _CRITERIA_SPLIT_PAGE
                                       ) -> Counter:
    """Count `criteria_split` over EVERY point in `collection_name`.

    THE FLAG IS NOT A TOP-LEVEL PAYLOAD FIELD. It rides inside the
    `full_trial_json` blob, so no server-side filter or facet can reach it and
    there is nothing to count with a `count(filter=...)` call: the only way to
    measure it is to pull every point's blob back and read it. That is what
    this does, and it is why the census is a scroll rather than a query.

    Paginated on Qdrant's own `next_page_offset`, never on an assumed single
    page: this collection is 14,324 points against a default page limit of 10.

    Returns a Counter over the values found, plus CRITERIA_SPLIT_FIELD_ABSENT
    for a point whose blob carries no such key and
    CRITERIA_SPLIT_PAYLOAD_UNREADABLE for one whose blob is missing or is not a
    JSON object. Nothing is skipped -- `sum(counter.values())` is the point
    count -- because a point silently omitted from the census is exactly the
    population the census exists to find.

    This is the ONE feeder. The standing gate below and the operator-facing
    measurement both call it; a second scroll written for the report would be a
    second implementation that can disagree with the one that gates.
    """
    client = client or get_qdrant_client()
    counts = Counter()
    offset = None

    @qdrant_retry
    def _page(next_offset):
        # A NESTED PAYLOAD SELECTOR, and the difference is not cosmetic.
        # `full_trial_json` is the whole stored trial -- ~10 KB per point, so
        # asking for it pulls ~150 MB back to read a five-character string, and
        # the DAG runs verification twice per refresh. Asking Qdrant for the
        # one key returns {"full_trial_json": {"criteria_split": "both"}}, the
        # same shape the parser below already reads.
        #
        # MEASURED, NOT ASSUMED: on 2026-08-09 both selectors were run over the
        # then-live `trial_criteria_20260807_111807` and returned IDENTICAL
        # counts (both 14,034 / inclusion_only 178 / unsplit 82 /
        # exclusion_only 30), in 10.3s and 1.7s respectively, over the same 15
        # pages. That is a record of the SELECTOR comparison, not of today's
        # census -- see the threshold block above for that.
        #
        # The failure direction is safe. A server that ignored the nested path
        # would return the whole blob, which this parser reads; one that
        # returned nothing for a key that exists would produce field_absent,
        # which FAILS the gate loudly rather than passing it.
        return client.scroll(collection_name=collection_name,
                             limit=page_size, offset=next_offset,
                             with_payload=_CRITERIA_SPLIT_SELECTOR,
                             with_vectors=False)

    while True:
        points, offset = _page(offset)
        for point in points:
            blob = (point.payload or {}).get("full_trial_json")
            if isinstance(blob, str):
                # Defensive: this writer stores a dict, but a blob written as a
                # JSON string by anything else must be read rather than
                # reported as damaged.
                try:
                    blob = json.loads(blob)
                except (ValueError, TypeError):
                    blob = None
            if not isinstance(blob, dict):
                counts[CRITERIA_SPLIT_PAYLOAD_UNREADABLE] += 1
                continue
            value = blob.get("criteria_split")
            if value is None:
                counts[CRITERIA_SPLIT_FIELD_ABSENT] += 1
            else:
                counts[str(value)] += 1
        if offset is None or not points:
            break

    return counts


def evaluate_criteria_split_distribution(
        distribution,
        max_degraded: float = _MAX_CRITERIA_SPLIT_DEGRADED,
        max_no_exclusion: float = _MAX_CRITERIA_SPLIT_NO_EXCLUSION,
        max_unusable: float = _MAX_CRITERIA_SPLIT_UNUSABLE) -> dict:
    """Judge a counted distribution. Pure: no client, no network, no raise.

    Returns a dict carrying the counts, the three gated fractions, the
    thresholds they were compared against, and a `failures` list of message
    strings -- empty when the distribution passes.

    IT RETURNS RATHER THAN RAISING so verify_collection can append the failures
    to the same list its other checks use and raise ONCE at the end naming
    everything that is wrong. A gate that raised here would report the split
    distribution and hide a simultaneously-broken sparse vector.

    THE THREE FRACTIONS:

      degraded      unsplit + empty_criteria. The splitter found no heading of
                    either family, or there was no criteria text at all.
      no_exclusion  unsplit + inclusion_only. Every trial the judge sees with
                    NO exclusion section, whichever branch produced it -- the
                    `elif inclusion_start != -1` arm sets exclusion_text = ""
                    just as the unsplit arm does. This is the population the
                    harm is actually defined over; see the constant.
      unusable      field_absent + payload_unreadable + any value outside
                    CRITERIA_SPLIT_VALUES. An unrecognised value is not a
                    fourth thing to gate on -- it is the same finding as an
                    absent field, namely that this point carries no verdict
                    this contract defines. Folding it in cannot make the gate
                    weaker, and leaving it out would let a splitter that
                    renamed its constants pass with every fraction at zero
                    while every trial in the corpus was unsplit.

    The first two OVERLAP -- `unsplit` is in both -- so they do not sum, and
    each is reported with its own count for that reason.

    A fraction EQUAL to its threshold passes; only `>` fails. The thresholds
    are ceilings on an observed rate, and a run that lands exactly on one is
    within what was authorised.
    """
    counts = Counter(distribution)
    total = sum(counts.values())
    failures = []

    unrecognised = {k: v for k, v in counts.items()
                    if k not in CRITERIA_SPLIT_VALUES
                    and k not in (CRITERIA_SPLIT_FIELD_ABSENT,
                                  CRITERIA_SPLIT_PAYLOAD_UNREADABLE)}

    degraded = (counts.get(CRITERIA_SPLIT_UNSPLIT, 0)
                + counts.get(CRITERIA_SPLIT_EMPTY, 0))
    no_exclusion = (counts.get(CRITERIA_SPLIT_UNSPLIT, 0)
                    + counts.get(CRITERIA_SPLIT_INCLUSION_ONLY, 0))
    unusable = (counts.get(CRITERIA_SPLIT_FIELD_ABSENT, 0)
                + counts.get(CRITERIA_SPLIT_PAYLOAD_UNREADABLE, 0)
                + sum(unrecognised.values()))

    measured = {
        "total": total,
        "counts": dict(sorted(counts.items())),
        "unrecognised": dict(sorted(unrecognised.items())),
        "degraded_count": degraded,
        "no_exclusion_count": no_exclusion,
        "unusable_count": unusable,
        "max_degraded": max_degraded,
        "max_no_exclusion": max_no_exclusion,
        "max_unusable": max_unusable,
    }

    # A CENSUS OVER NOTHING MUST NOT PASS. Zero points divides by zero and,
    # guarded the lazy way, would report 0.0 -- three fractions comfortably
    # under their ceilings and a gate that measured nothing looking exactly
    # like one that measured a perfect corpus.
    if total == 0:
        measured["degraded_fraction"] = None
        measured["no_exclusion_fraction"] = None
        measured["unusable_fraction"] = None
        measured["fractions"] = {}
        failures.append(
            "the criteria_split census counted 0 points, so the distribution "
            "gate MEASURED NOTHING. A collection this check cannot read must "
            f"not be promoted on the strength of it. {_SWAP_REFUSED_NOTE}")
        measured["failures"] = failures
        return measured

    degraded_fraction = degraded / total
    no_exclusion_fraction = no_exclusion / total
    unusable_fraction = unusable / total
    measured["degraded_fraction"] = degraded_fraction
    measured["no_exclusion_fraction"] = no_exclusion_fraction
    measured["unusable_fraction"] = unusable_fraction
    measured["fractions"] = {k: v / total for k, v in sorted(counts.items())}

    if degraded_fraction > max_degraded:
        failures.append(
            f"criteria_split: {degraded:,} of {total:,} points "
            f"({degraded_fraction:.2%}) carry '{CRITERIA_SPLIT_UNSPLIT}' or "
            f"'{CRITERIA_SPLIT_EMPTY}', above the {max_degraded:.2%} ceiling. "
            f"The splitter found no heading of either family in those trials, "
            f"so the whole criteria block was sent to inclusion and any "
            f"exclusion criteria it holds reach the judge as inclusion "
            f"criteria, inverting every verdict on them. A jump here is what a "
            f"heading-format change at ClinicalTrials.gov looks like. "
            f"{_SWAP_REFUSED_NOTE}")

    if no_exclusion_fraction > max_no_exclusion:
        failures.append(
            f"criteria_split: {no_exclusion:,} of {total:,} points "
            f"({no_exclusion_fraction:.2%}) carry '{CRITERIA_SPLIT_UNSPLIT}' "
            f"or '{CRITERIA_SPLIT_INCLUSION_ONLY}', above the "
            f"{max_no_exclusion:.2%} ceiling. Every one of those trials "
            f"reaches the judge with an EMPTY exclusion section. This fires "
            f"without the degraded fraction moving when only the exclusion "
            f"heading family stops matching, which is the half of a splitter "
            f"regression that unsplit alone cannot see. {_SWAP_REFUSED_NOTE}")

    if unusable_fraction > max_unusable:
        detail = (f" Unrecognised values: {measured['unrecognised']}."
                  if unrecognised else "")
        failures.append(
            f"criteria_split: {unusable:,} of {total:,} points "
            f"({unusable_fraction:.2%}) carry no usable verdict — the field is "
            f"absent, the payload is unreadable, or the value is outside the "
            f"closed vocabulary {list(CRITERIA_SPLIT_VALUES)} — above the "
            f"{max_unusable:.2%} ceiling. Those points were written by "
            f"something that is not this splitter, so nothing downstream can "
            f"tell whether their exclusion criteria are labelled correctly."
            f"{detail} {_SWAP_REFUSED_NOTE}")

    measured["failures"] = failures
    return measured


def check_criteria_split_distribution(
        distribution,
        max_degraded: float = _MAX_CRITERIA_SPLIT_DEGRADED,
        max_no_exclusion: float = _MAX_CRITERIA_SPLIT_NO_EXCLUSION,
        max_unusable: float = _MAX_CRITERIA_SPLIT_UNUSABLE) -> dict:
    """evaluate_criteria_split_distribution, but RAISING on any failure.

    The standalone form, for a caller that wants the gate on its own rather
    than as one section of verify_collection. Returns what it measured.

    It raises IndexVerificationError -- the same type verify_collection raises,
    deliberately, so a caller wrapping either one catches the same thing.
    """
    measured = evaluate_criteria_split_distribution(
        distribution, max_degraded=max_degraded,
        max_no_exclusion=max_no_exclusion, max_unusable=max_unusable)
    if measured["failures"]:
        raise IndexVerificationError(
            f"the criteria_split distribution failed "
            f"{len(measured['failures'])} check(s):\n"
            + "".join(f"    - {f}\n" for f in measured["failures"]))
    return measured


def report_criteria_split_distribution(measured: dict, out=None) -> None:
    """Print and log the whole distribution, whether it passed or failed.

    ALWAYS, not only on failure. A gate that reports only when it fires leaves
    nobody a number to compare next week's against, and this fraction is the
    only standing measurement of how much of the corpus reaches the judge with
    its exclusion criteria mislabelled.
    """
    out = out or console.out
    total = measured["total"]
    out(f"  criteria_split census over {total:,} point(s):")
    for value, n in measured["counts"].items():
        share = f"{n / total:7.3%}" if total else "       —"
        out(f"      {value:<22} {n:>7,}  {share}")

    def _pct(value):
        return "n/a" if value is None else f"{value:.3%}"

    out(f"      degraded (unsplit+empty)      "
        f"{measured['degraded_count']:>7,}  {_pct(measured['degraded_fraction'])}"
        f"  ceiling {measured['max_degraded']:.2%}")
    out(f"      no exclusion section          "
        f"{measured['no_exclusion_count']:>7,}  "
        f"{_pct(measured['no_exclusion_fraction'])}"
        f"  ceiling {measured['max_no_exclusion']:.2%}")
    out(f"      no usable verdict             "
        f"{measured['unusable_count']:>7,}  {_pct(measured['unusable_fraction'])}"
        f"  ceiling {measured['max_unusable']:.2%}")

    log.info("criteria split distribution measured",
             total=total,
             split_degraded_count=measured["degraded_count"],
             split_degraded_fraction=measured["degraded_fraction"],
             split_degraded_max=measured["max_degraded"],
             split_no_exclusion_count=measured["no_exclusion_count"],
             split_no_exclusion_fraction=measured["no_exclusion_fraction"],
             split_no_exclusion_max=measured["max_no_exclusion"],
             split_unusable_count=measured["unusable_count"],
             split_unusable_fraction=measured["unusable_fraction"],
             split_unusable_max=measured["max_unusable"],
             unsplit_count=measured["counts"].get(CRITERIA_SPLIT_UNSPLIT, 0),
             empty_criteria_count=measured["counts"].get(CRITERIA_SPLIT_EMPTY, 0),
             field_absent_count=measured["counts"].get(
                 CRITERIA_SPLIT_FIELD_ABSENT, 0),
             payload_unreadable_count=measured["counts"].get(
                 CRITERIA_SPLIT_PAYLOAD_UNREADABLE, 0))


def verify_collection(collection_name: str, expected_count: int,
                      compare_to: str = None,
                      min_ratio: float = _MIN_CORPUS_RATIO) -> dict:
    """Check a freshly built collection. Raises IndexVerificationError on any
    failure. Returns a dict of what was measured.

    Every check is free: the dense probe searches with a vector READ BACK OUT
    of the collection rather than a newly embedded one, and the sparse probes
    use the local FastEmbed model. Nothing here calls a paid API.

    Args:
        collection_name: the staging collection to check.
        expected_count:  how many trials this run intended to index.
        compare_to:      the collection this one would REPLACE. When given, a
                         staging collection holding less than `min_ratio` of
                         its point count FAILS. See below.
        min_ratio:       the floor, as a fraction of `compare_to`'s count.

    THE compare_to CHECK EXISTS BECAUSE ITS ABSENCE COST A PROMOTION. The first
    real run of this pass verified clean and promoted a collection holding
    5,482 trials over one holding 12,067, because `expected_count` is what the
    process scraped and the scrape had been truncated by a network fault. Every
    other check here asks "is this collection well-formed"; a truncated corpus
    is perfectly well-formed. Only a comparison with what it replaces can see
    it.

    It is a RATIO and not equality because a registry legitimately shrinks --
    trials stop recruiting every day, and the measured four-day drift on this
    corpus was 43 removed against 68 added. It is overridable because a
    deliberate corpus reduction is a real operation; passing compare_to=None
    skips it, and that decision is then visible at the call site rather than
    being a silent default.
    """
    console.out(f"\n=== VERIFYING '{collection_name}' BEFORE SWAP ===")
    client = get_qdrant_client()
    failures = []
    measured = {"collection": collection_name, "expected_count": expected_count}

    def _fail(msg):
        failures.append(msg)
        console.out(f"  ✗ {msg}")

    def _ok(msg):
        console.out(f"  ✓ {msg}")

    # --- 1. The collection exists and reports a usable status ---------------
    #
    # YELLOW IS NOT A FAULT AND TREATING IT AS ONE COST A BUILD. Qdrant reports
    # `yellow` while its optimizers are still constructing the HNSW index after
    # a bulk upsert -- which is the NORMAL state seconds after indexing 14,000
    # points. The first version of this check failed on anything that was not
    # green, so a run that had scraped, embedded and paid in full was refused
    # at the last step by a collection that was busy finishing, and was green
    # ninety seconds later. Every functional probe below had passed.
    #
    # The three states mean different things and now get different policies:
    #   green  -> ready
    #   yellow -> optimizers running. WAIT for it, bounded. Queries already
    #             work (Qdrant falls back to exact search), but promoting
    #             before the index is built means every request pays for that
    #             fallback, so this waits rather than shrugging.
    #   red    -> a real failure. Fail immediately, never wait.
    info = client.get_collection(collection_name=collection_name)
    status = str(getattr(info, "status", "")).lower()
    waited = 0
    while "yellow" in status and waited < _STATUS_WAIT_SECONDS:
        if waited == 0:
            console.out(f"  status is yellow (optimizers building the index); "
                        f"waiting up to {_STATUS_WAIT_SECONDS}s for green...")
        time.sleep(_STATUS_POLL_SECONDS)
        waited += _STATUS_POLL_SECONDS
        info = client.get_collection(collection_name=collection_name)
        status = str(getattr(info, "status", "")).lower()
    measured["status"] = status
    measured["status_wait_s"] = waited
    if "red" in status:
        _fail(f"status is {status!r} — this is a real failure, not optimization")
    elif "green" in status or "ok" in status:
        _ok(f"status: {status}" + (f" (after waiting {waited}s)" if waited else ""))
    else:
        _fail(f"status is still {status!r} after {waited}s. The vector index "
              f"has not finished building, so every query would fall back to "
              f"an exact scan. Re-run verification once it settles.")

    # --- 2. Point count matches what this run intended to index -------------
    actual = client.count(collection_name=collection_name, exact=True).count
    measured["actual_count"] = actual
    if actual == expected_count:
        _ok(f"point count: {actual:,} == {expected_count:,} scraped")
    else:
        _fail(f"point count {actual:,} != {expected_count:,} scraped "
              f"(missing {expected_count - actual:,})")

    # --- 3. Vector configuration -------------------------------------------
    vectors = info.config.params.vectors
    dense = vectors.get("") if isinstance(vectors, dict) else vectors
    if dense is None:
        _fail("no dense vector configured")
    else:
        if dense.size == EMBEDDING_DIM:
            _ok(f"dense vector: {dense.size} dims")
        else:
            _fail(f"dense vector is {dense.size} dims, expected {EMBEDDING_DIM}")
        if str(dense.distance).lower().endswith("cosine"):
            _ok("dense distance: cosine")
        else:
            _fail(f"dense distance is {dense.distance}, expected COSINE")

    sparse_cfg = info.config.params.sparse_vectors or {}
    for name in _SPARSE_VECTOR_NAMES:
        if name not in sparse_cfg:
            _fail(f"sparse vector {name!r} is not configured")
        elif getattr(sparse_cfg[name], "modifier", None) is None:
            _fail(f"sparse vector {name!r} has no IDF modifier")
        else:
            _ok(f"sparse vector {name!r} configured with IDF")

    # --- 4. A sample of points is well-formed -------------------------------
    sample, _ = client.scroll(collection_name=collection_name,
                              limit=_VERIFY_SAMPLE_SIZE,
                              with_payload=True, with_vectors=True)
    measured["sampled"] = len(sample)
    if not sample:
        _fail("scroll returned no points at all")
    else:
        bad_dense = bad_sparse = bad_payload = 0
        probe_vector = None
        for p in sample:
            vec = p.vector if isinstance(p.vector, dict) else {"": p.vector}
            d = vec.get("")
            if not d or len(d) != EMBEDDING_DIM:
                bad_dense += 1
            elif probe_vector is None:
                probe_vector = list(d)
            for name in _SPARSE_VECTOR_NAMES:
                sv = vec.get(name)
                # criteria-bm25 is legitimately empty when a trial registers no
                # eligibility text, so emptiness is only a fault if the field
                # is missing entirely.
                if sv is None:
                    bad_sparse += 1
                    break
            payload = p.payload or {}
            if not payload.get("nct_id") or not payload.get("full_trial_json"):
                bad_payload += 1
        if bad_dense:
            _fail(f"{bad_dense}/{len(sample)} sampled points have a missing or "
                  f"wrong-sized dense vector")
        else:
            _ok(f"{len(sample)} sampled points carry a {EMBEDDING_DIM}-dim dense vector")
        if bad_sparse:
            _fail(f"{bad_sparse}/{len(sample)} sampled points are missing a "
                  f"named sparse vector")
        else:
            _ok(f"{len(sample)} sampled points carry all three sparse vectors")
        if bad_payload:
            _fail(f"{bad_payload}/{len(sample)} sampled points lack nct_id or "
                  f"full_trial_json in the payload")
        else:
            _ok(f"{len(sample)} sampled points carry nct_id and full_trial_json")

        # --- 5. The payload index works (this is what scroll-by-nct_id needs)
        probe_nct = (sample[0].payload or {}).get("nct_id")
        if probe_nct:
            found, _ = client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(must=[FieldCondition(
                    key="nct_id", match=MatchValue(value=probe_nct))]),
                limit=1, with_payload=True)
            if found:
                _ok(f"payload index resolves nct_id {probe_nct}")
            else:
                _fail(f"payload index did NOT resolve nct_id {probe_nct}")

        # --- 6. Dense search answers, using a vector from the collection ----
        if probe_vector is not None:
            hits = client.query_points(collection_name=collection_name,
                                       query=probe_vector, limit=5,
                                       with_payload=True).points
            if hits:
                _ok(f"dense search returned {len(hits)} hits")
            else:
                _fail("dense search returned nothing for a vector taken from "
                      "this very collection")

    # --- 7. Each BM25 channel answers a real query --------------------------
    bm25 = get_bm25_sparse_model()
    for name, probe in ((("title-bm25"), "cancer"),
                        ("conditions-bm25", "carcinoma"),
                        ("criteria-bm25", "patients")):
        emb = next(bm25.query_embed(probe))
        indices = emb.indices.tolist()
        if not indices:
            _fail(f"{name}: probe {probe!r} produced no BM25 terms")
            continue
        hits = client.query_points(
            collection_name=collection_name,
            query=SparseVector(indices=indices, values=emb.values.tolist()),
            using=name, limit=5, with_payload=False).points
        if hits:
            _ok(f"{name}: probe {probe!r} returned {len(hits)} hits")
        else:
            _fail(f"{name}: probe {probe!r} returned NOTHING — the sparse "
                  f"index is present but not answering")

    # --- 8. Is this a plausible replacement for what is live? --------------
    if compare_to:
        try:
            live_count = client.count(collection_name=compare_to, exact=True).count
        except Exception as e:
            # Counted and reported, never swallowed. A comparison that could
            # not be made must not read as a comparison that passed.
            CLEANUP_FAILURES[f"compare_count:{type(e).__name__}"] += 1
            live_count = None
            _fail(f"could not count '{compare_to}' to compare against: "
                  f"{type(e).__name__}: {e}. The size check DID NOT RUN.")
        if live_count:
            measured["live_count"] = live_count
            floor = int(live_count * min_ratio)
            measured["size_floor"] = floor
            if actual >= floor:
                _ok(f"size vs live '{compare_to}': {actual:,} >= {floor:,} "
                    f"({min_ratio:.0%} of {live_count:,})")
            else:
                _fail(f"{actual:,} points is only "
                      f"{actual / live_count:.1%} of the live collection "
                      f"'{compare_to}' ({live_count:,}). Below the "
                      f"{min_ratio:.0%} floor, so this looks like a TRUNCATED "
                      f"corpus rather than a rebuild. If the reduction is "
                      f"intended, re-run passing a lower min_ratio.")
    else:
        console.out("  - size-vs-live check SKIPPED (no collection to compare "
                    "against; this is a first build or it was disabled)")

    # --- 9. THE INGESTION GATE: is criteria_split still being written? ------
    #
    # A CONTENT CHECK, and the only one here. Every check above asks whether
    # the collection is well-formed; this one asks whether the eligibility text
    # inside it is labelled the way the pipeline assumes. It is a full scroll
    # of one payload key -- ~15 round trips and ~9s on a 14k corpus, no model
    # call, no paid API -- and it reports on every run, pass or fail.
    split_counts = scroll_criteria_split_distribution(collection_name,
                                                      client=client)
    split_report = evaluate_criteria_split_distribution(split_counts)
    measured["criteria_split"] = split_report
    report_criteria_split_distribution(split_report)
    for message in split_report["failures"]:
        _fail(message)

    # THE CENSUS MUST HAVE COVERED THE WHOLE COLLECTION, or every fraction
    # above is computed over a subset and diluted by exactly the points it
    # missed. A scroll that stopped after one page reports a corpus of 1,000
    # with three tidy fractions and no error; a scroll that repeated a page
    # reports 20,000 and dilutes the degraded population by half. Neither is
    # visible in the distribution itself -- only against the count section 2
    # already took, which is why this is asserted here rather than inside the
    # pure evaluator, which has nothing to compare against.
    if split_report["total"] != actual:
        _fail(f"the criteria_split census covered {split_report['total']:,} "
              f"point(s) but the collection holds {actual:,}. Every fraction "
              f"reported above is computed over that subset, so the gate did "
              f"not measure what it claims to have measured. {_SWAP_REFUSED_NOTE}")
    if not split_report["failures"]:
        _ok(f"criteria_split distribution within every ceiling "
            f"({split_report['degraded_count']:,} degraded, "
            f"{split_report['no_exclusion_count']:,} without an exclusion "
            f"section, {split_report['unusable_count']:,} unusable of "
            f"{split_report['total']:,})")

    measured["failures"] = failures
    if failures:
        raise IndexVerificationError(
            f"'{collection_name}' failed {len(failures)} pre-swap check(s) and "
            f"the alias was NOT moved:\n"
            + "".join(f"    - {f}\n" for f in failures)
            + "  The live collection is untouched. Inspect the staging "
              "collection, or delete it and re-run.")

    console.out(f"=== '{collection_name}' PASSED every pre-swap check ===")
    log.info("staging collection verified", collection=collection_name,
             count=measured.get("actual_count"), total=expected_count)
    return measured


def resolve_alias_target(alias_name: str):
    """Which collection `alias_name` currently points at, or None."""
    for a in get_qdrant_client().get_aliases().aliases:
        if a.alias_name == alias_name:
            return a.collection_name
    return None


def cleanup_old_collections(keep_recent: int = 2, alias_name: str = "trial_criteria"):
    """
    Delete old timestamped staging collections, keep N most recent.

    THE DEFAULT IS 2, NOT 1, AND THAT IS DEFECT 4's SECOND HALF. At 1 the only
    collection kept is the one the alias was just moved onto, so the previous
    good collection -- the only thing a rollback could point back at -- was
    destroyed in the same run that promoted its replacement. Keeping two means
    a bad promotion can be undone with a single update_collection_aliases call
    against a collection that is still there.

    THE ALIAS TARGET IS NEVER DELETED, whatever `keep_recent` says and whatever
    its name sorts like. The old code kept the newest N by NAME and relied on
    the alias pointing at the newest, which is true only when the swap
    succeeded; after a failed or skipped swap it is exactly false, and the
    collection actually serving traffic was the one sorted out of the keep
    window.

    Args:
        keep_recent: Number of recent timestamped collections to keep (min 2)
        alias_name:  Alias whose target must survive regardless
    """
    if keep_recent < 2:
        console.out(f"WARNING: keep_recent={keep_recent} would leave no rollback "
                    f"target. Using 2.")
        keep_recent = 2

    collections = get_qdrant_client().get_collections().collections
    timestamped = [
        c.name for c in collections
        if c.name.startswith("trial_criteria_") and c.name != "trial_criteria"
    ]

    # Sort descending: newest first (YYYYMMDD_HHMMSS format sorts correctly)
    timestamped.sort(reverse=True)

    live = resolve_alias_target(alias_name)
    to_keep   = timestamped[:keep_recent]
    to_delete = [n for n in timestamped[keep_recent:] if n != live]

    if live and live not in to_keep:
        # The alias points at something outside the keep window. Report it
        # loudly rather than quietly protecting it: it means the swap did not
        # land where this run thinks it did.
        console.out(f"WARNING: alias '{alias_name}' points at '{live}', which is "
                    f"NOT in the {keep_recent} newest collections. It will be "
                    f"kept anyway.")
        log.warning("alias target is outside the cleanup keep window",
                    collection=live, kept=len(to_keep))
        to_keep = to_keep + [live]

    if not to_delete:
        console.out(f"No old collections to clean up. Keeping: {to_keep}")
        return

    for name in to_delete:
        try:
            get_qdrant_client().delete_collection(collection_name=name)
            console.out(f"Deleted old collection: {name}")
        except Exception as e:
            # Counted, not only printed: a cleanup that silently failed for
            # every collection looks identical to one with nothing to do.
            CLEANUP_FAILURES[type(e).__name__] += 1
            console.out(f"WARNING: Could not delete collection '{name}': {e}")
            log.warning("could not delete old collection", collection=name,
                        error_type=type(e).__name__, error_message=str(e))

    console.out(f"Kept {len(to_keep)} collection(s): {to_keep}")
    console.out(f"  Rollback target: "
                f"{[n for n in to_keep if n != live] or ['NONE — this is the first build']}")
    

#------------------------------------------------------------------------------


def main(use_staging: bool = True, compare_to: str = None,
         run_cleanup: bool = True, max_cost_usd: float = None):
    """
    Main execution: scrape, embed, index with zero-downtime swap.

    Args:
        use_staging: If True, build in staging and swap atomically.
                     If False, rebuild production directly (causes downtime).
        compare_to:  Collection the size floor is measured against. None means
                     "whatever the alias points at", which is the right default
                     and the WRONG answer when the alias itself is bad -- see
                     below.
        run_cleanup: False keeps every existing collection. Use it when the
                     retained set is being preserved deliberately.

    WHY compare_to IS A PARAMETER, AND IT IS NOT HYPOTHETICAL. The size floor
    defends against promoting a truncated corpus, and it measures against the
    collection being replaced. That baseline is the alias target -- which is
    exactly the thing that is wrong when a truncated corpus has ALREADY been
    promoted. On this project that happened: a network fault truncated a scrape
    to 5,482 trials, the alias moved onto it, and the next run's floor would
    have been 90% of 5,482 rather than of the 12,067 that preceded it. A guard
    whose reference point is the previous failure is not a guard.

    So the baseline is nameable. None keeps the ordinary behaviour; an explicit
    name is logged as an override so the run says what it measured against
    rather than leaving a reader to infer it.
    """
    with CaffeinateSession("RAG Indexing"):
        console.out(f"=== {Project_Name}: Clinical Trial RAG Indexer ===\n")

        trials = scrape_clinicaltrials_gov()
        if not trials:
            console.out("No trials scraped. Exiting.")
            return

        save_trials_to_disk(trials, output_path=paths.data_trial_path)

        # --- THE SPEND GATE, BEFORE THE FIRST EMBEDDING CALL ---------------
        #
        # The corpus size is whatever the registry holds today, so the bill is
        # not knowable until after the scrape -- and the scrape is free while
        # the embedding is not. This is the only point where both facts exist.
        _estimate = estimate_embedding_cost(trials)
        console.out(f"\nEmbedding estimate: {_estimate['trials']:,} trials, "
                    f"{_estimate['tokens']:,} tokens via {_estimate['method']} "
                    f"-> ${_estimate['cost_usd']:.4f} at the configured "
                    f"{EMBEDDING_MODEL} price")
        log.info("embedding cost estimated", model=EMBEDDING_MODEL,
                 tokens_estimated=_estimate["tokens"],
                 total=_estimate["trials"],
                 cost_usd=round(_estimate["cost_usd"], 6))
        if max_cost_usd is not None and _estimate["cost_usd"] > max_cost_usd:
            raise EmbeddingBudgetExceeded(
                f"embedding this corpus is estimated at "
                f"${_estimate['cost_usd']:.4f}, above the authorised "
                f"${max_cost_usd:.4f}.\n"
                f"  {_estimate['trials']:,} trials, {_estimate['tokens']:,} "
                f"tokens ({_estimate['method']}).\n"
                f"  NOTHING HAS BEEN EMBEDDED and the alias has not moved. The "
                f"scraped corpus is on disk; re-run with a higher "
                f"max_cost_usd to proceed.")

        if use_staging:
            console.out("\n=== STAGING REBUILD (ZERO DOWNTIME) ===\n")
            staging_name = f"trial_criteria_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            # What the alias points at BEFORE anything moves, so the rollback
            # target can be named in the report rather than inferred later.
            previous = resolve_alias_target("trial_criteria")

            # The size floor's baseline. Named explicitly when the alias target
            # is not trustworthy; see this function's docstring.
            baseline = compare_to or previous
            if compare_to and compare_to != previous:
                console.out(f"SIZE FLOOR BASELINE OVERRIDDEN: measuring against "
                            f"'{compare_to}', NOT the current alias target "
                            f"'{previous}'.")
                log.warning("size floor baseline overridden",
                            collection=compare_to, reason="explicit_compare_to")
            if previous:
                try:
                    previous_count = get_qdrant_client().count(
                        collection_name=previous, exact=True).count
                except Exception as e:
                    # Counted and reported. This is a diagnostic read; failing
                    # it must not stop a rebuild, but it must not be silent.
                    CLEANUP_FAILURES[f"previous_count:{type(e).__name__}"] += 1
                    previous_count = None
                    log.warning("could not count the live collection",
                                collection=previous, error_type=type(e).__name__,
                                error_message=str(e))
                console.out(f"Live collection before this run: {previous} "
                            f"({previous_count if previous_count is not None else '?'} points)")
            else:
                console.out("No live collection yet — this is the first build.")

            create_qdrant_collection(staging_name, delete_if_exists=False)
            index_trials(trials, collection_name=staging_name)
            create_payload_indexes(staging_name)

            # DEFECT 4: VERIFY, THEN SWAP. This raises IndexVerificationError
            # on any failure, which leaves the alias exactly where it was and
            # the staging collection in place for inspection.
            #
            # `compare_to` is what makes the count check mean anything:
            # expected_count is what THIS PROCESS scraped, so a truncated
            # scrape agrees with itself. Only the live collection knows how big
            # the corpus is supposed to be.
            verify_collection(staging_name, expected_count=len(trials),
                              compare_to=baseline)

            swap_alias_atomic(staging_name, "trial_criteria")

            # keep_recent=2 so `previous` survives as the rollback target.
            if run_cleanup:
                cleanup_old_collections(keep_recent=2, alias_name="trial_criteria")
            else:
                console.out("Cleanup SKIPPED (run_cleanup=False): every existing "
                            "collection is retained.")
                log.info("cleanup skipped by request", mode="no_cleanup")

            console.out(f"\n✓ Staging rebuild complete")
            console.out(f"✓ Alias 'trial_criteria' now points to '{staging_name}'")
            console.out(f"✓ FastAPI experienced zero downtime")
            if previous:
                console.out(f"✓ ROLLBACK TARGET RETAINED: '{previous}'")
                console.out(f"    to roll back, point the alias back at it with "
                            f"swap_alias_atomic('{previous}', 'trial_criteria')\n")
            else:
                console.out("")

        else:
            console.out("\n=== DIRECT REBUILD (CAUSES DOWNTIME) ===\n")
            create_qdrant_collection("trial_criteria", delete_if_exists=True)
            index_trials(trials, collection_name="trial_criteria")
            create_payload_indexes("trial_criteria")
            # Verified AFTER the fact here, because direct mode has already
            # replaced production by the time there is anything to verify.
            # That is what "causes downtime" means and it is why staging is the
            # default; the check still runs so a bad direct build is at least
            # LOUD rather than silent.
            verify_collection("trial_criteria", expected_count=len(trials))
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
