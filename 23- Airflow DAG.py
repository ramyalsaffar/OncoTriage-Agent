# Create DAG
############

# After running this file, in a terminal, run these:
    ## Terminal 1: Start API server
    ## export AIRFLOW_HOME="/path/to/airflow/"
    ## airflow api-server --port 8080

    # Terminal 2: Start scheduler
    ## export AIRFLOW_HOME="/path/to/airflow/"
    ## airflow scheduler
    
    # Check DAG status via UI: http://localhost:8080
    # Or trigger manually: airflow dags trigger trial_refresh_weekly


#------------------------------------------------------------------------------

# Create DAG
############

# After running this file (which writes the DAG to the dags/ folder),
# use file 24, Airflow Manager, to start Airflow services, check status, and trigger.


#------------------------------------------------------------------------------


# ===========================================================================
# EXEC CHAIN: 01
# ===========================================================================
# Until this block existed, `python "23- Airflow DAG.py"` died on the next
# statement with NameError: name 'Path' is not defined. The file had no
# bootstrap and only ever ran inside a Spyder session where 01 had already
# been exec'd into the same namespace.
#
# 01 alone, and nothing else. The generator -- the code in this file, as
# opposed to the DAG text it writes out -- reads exactly six names it does not
# define, and 01 supplies all six:
#
#   Path             pathlib, imported by 01
#   airflow_path     01's path block, used just below for the dags/ directory
#   path_settings    01's settings-module handle, for the ONCOTRIAGE_* names
#   code_path        }  the three fallbacks baked into the generated DAG's
#   keys_path        }  path block further down
#   data_trial_path  }
#
# Every other name in this file is defined here or lives inside dag_content,
# which is a string: those names are resolved by Airflow when it parses the
# generated DAG, in a process that has none of this namespace, so they are not
# dependencies of the generator. 02 is not loaded because nothing here calls
# exec_chain or any utility function, and 03 is not loaded because the DAG
# reads its config out of 03 at task time, through PROJECT_CODE_PATH, not from
# this namespace. Chaining either would be loading a file this one does not use.
#
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

with open(_code_dir + "01- Imports.py") as _fh:
    exec(_fh.read(), globals())


#------------------------------------------------------------------------------


# DAG file content (head + generated path block + tail — see the path block below)
dag_content_head = '''"""
Weekly Trial Refresh DAG

Scrapes clinical trials, rebuilds Qdrant index.
Schedule: read from AIRFLOW_DAG_SCHEDULE in 03- Config.py (None = no automatic
runs; the DAG stays registered and manually triggerable).

Airflow 3.1.7 | TaskFlow API | Pure Python (no BashOperator)
"""

import ast
import os
import sys
import json
import time
import re
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

import pendulum
from airflow.sdk import dag, task


# =============================================================================
# Escape-safe constants (avoids issues with triple-quoted string writing)
# =============================================================================
DIGIT_PATTERN = re.compile(chr(92) + "d+")       # matches \\d+ (digits)
NEWLINE_SEP = chr(10) + chr(10)                    # matches "\\n\\n" (double newline)
'''


# =============================================================================
# Project paths for the generated DAG  (item 20a)
# =============================================================================
# Airflow parses the generated DAG in the scheduler's own process, from
# {airflow_path}/dags/. That process has not run exec_chain and has none of
# 01- Imports.py's names, and the DAG file does not sit in the code directory,
# so it cannot find itself with __file__ either. Environment variables are the
# only mechanism that reaches it, and Airflow passes them through.
#
# The fallbacks are not literals in this file. They are whatever
# 01- Imports.py resolved at DAG-generation time, rendered in with repr(), so
# the generator and the generated file cannot drift.
#
# One value changes meaning as a result. KEYS_PATH used to be a literal
# ".../04- Keys/", a directory that does not exist -- the keys live in
# "05- Keys/", which is what 01- Imports.py's glob has always found. The
# generated DAG's _read_key() therefore raised FileNotFoundError on every run.
# Sourcing the fallback from 01 fixes that as a side effect; it is called out
# here because it is a behaviour change, not just a path move.
#
# dag_content is split in three around this block because it is a plain
# triple-quoted string, not an f-string: the DAG body contains dict literals
# and f-strings of its own, so neither .format() nor an f-prefix can be
# applied to it.

_dag_path_block = '''

# =============================================================================
# Project Paths (fallbacks resolved by 01- Imports.py when this file was
# generated; override any of them with the environment variable named beside
# it, which is how a container or a different checkout retargets this DAG)
# =============================================================================
def _path_from_env(var_name, fallback):
    """Environment variable if set to a non-empty value, else the fallback.

    An empty string counts as unset: `export ONCOTRIAGE_KEYS_PATH=` is a
    normal way to clear a variable, and taking it literally would turn every
    absolute path below into a relative one against the scheduler's working
    directory.
    """
    raw = os.environ.get(var_name)
    if raw is not None and raw.strip() != "":
        resolved = raw.strip().rstrip("/") + "/"
        print("[DAG paths] " + var_name + " -> " + resolved)
        return resolved
    return fallback


PROJECT_CODE_PATH = _path_from_env(%r, %r)
KEYS_PATH = _path_from_env(%r, %r)
DATA_TRIAL_PATH = _path_from_env(%r, %r)
''' % (
    path_settings.ENV_CODE_PATH,       path_settings.with_trailing_sep(code_path),
    path_settings.ENV_KEYS_PATH,       path_settings.with_trailing_sep(keys_path),
    path_settings.ENV_DATA_TRIAL_PATH, path_settings.with_trailing_sep(data_trial_path),
)


dag_content_tail = '''

# =============================================================================
# Config (loaded dynamically from 03- Config.py inside each task)
# =============================================================================
def _config_literal(name):
    """Read one module-level literal out of 03- Config.py without running it.

    Used for values needed at DAG PARSE time, in the scheduler's own process.
    _load_config() below cannot serve that: it execs file 03, which calls
    load_env_keys() (defined in file 02) and constructs the OpenAI and Qdrant
    clients, so parsing a DAG would need keys and a network. Reading the
    assignment out of the AST needs neither.

    Raises if the name is absent. A schedule that cannot be read is a config
    defect, and defaulting to some other schedule here would silently restore
    automatic runs that were deliberately turned off.
    """
    config_path = PROJECT_CODE_PATH + "03- Config.py"
    with open(config_path, "r") as f:
        tree = ast.parse(f.read(), filename=config_path)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise RuntimeError(f"{name} is not assigned at module level in {config_path}")


def _load_config() -> dict:
    """Exec file 03 into an isolated namespace and extract config values."""
    ns = {}
    config_path = PROJECT_CODE_PATH + "03- Config.py"
    with open(config_path, "r") as f:
        exec(f.read(), ns)
    return {
        "COLLECTION_NAME":    ns["COLLECTION_NAME"],
        "EMBEDDING_MODEL":    ns["EMBEDDING_MODEL"],
        "EMBEDDING_DIM":      ns["EMBEDDING_DIM"],
        "MAX_TRIALS":         ns["trial_dict"]["max_trials"],
        "TRIAL_CONDITION":    ns["trial_dict"]["condition"],
        "TRIAL_STATUS":       ns["trial_dict"]["status"],
        "TRIAL_STUDY_TYPE":   ns["trial_dict"]["study_type"],
        "RERANK_SCORE_THRESHOLD": ns["RERANK_SCORE_THRESHOLD"],
    }


# =============================================================================
# Utility: Read API key from Keys.txt (matches 02- Utility Functions.py)
# =============================================================================
def _read_key(path, startswith_text):
    with open(path + "Keys.txt", "r") as file:
        for line in file:
            if line.startswith(startswith_text):
                clean_line = line.strip().removeprefix(startswith_text + ":")
                return clean_line
    return "None Found"


# =============================================================================
# Helper functions (exact logic from 11- RAG Trial Indexer.py)
# =============================================================================
def _split_inclusion_exclusion(criteria_text: str) -> tuple:
    """Split eligibility criteria into inclusion and exclusion sections."""
    text_lower = criteria_text.lower()

    inclusion_markers = ["inclusion criteria:", "inclusion:", "patients must"]
    exclusion_markers = ["exclusion criteria:", "exclusion:", "patients must not"]

    inclusion_start = -1
    for marker in inclusion_markers:
        pos = text_lower.find(marker)
        if pos != -1 and (inclusion_start == -1 or pos < inclusion_start):
            inclusion_start = pos

    exclusion_start = -1
    for marker in exclusion_markers:
        pos = text_lower.find(marker)
        if pos != -1 and (exclusion_start == -1 or pos < exclusion_start):
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


def _extract_locations(locations_list: List[Dict]) -> List[Dict]:
    """Extract location information."""
    us_locs = [loc for loc in locations_list if loc.get("country", "").upper() in ("US", "UNITED STATES")]
    other_locs = [loc for loc in locations_list if loc.get("country", "").upper() not in ("US", "UNITED STATES")]
    sorted_locs = (us_locs + other_locs)[:20]
    return [
        {
            "facility": loc.get("facility", ""),
            "city": loc.get("city", ""),
            "state": loc.get("state", ""),
            "country": loc.get("country", "")
        }
        for loc in sorted_locs
    ]


def _parse_trial_metadata(protocol: Dict) -> Dict:
    """Extract relevant trial metadata from protocol section."""
    identification = protocol.get("identificationModule", {})
    description = protocol.get("descriptionModule", {})
    design = protocol.get("designModule", {})
    eligibility = protocol.get("eligibilityModule", {})
    contacts = protocol.get("contactsLocationsModule", {})

    eligibility_text = eligibility.get("eligibilityCriteria", "")
    inclusion_text, exclusion_text = _split_inclusion_exclusion(eligibility_text)

    trial = {
        "nct_id": identification.get("nctId", ""),
        "title": identification.get("officialTitle", identification.get("briefTitle", "")),
        "brief_summary": description.get("briefSummary", ""),
        "detailed_description": description.get("detailedDescription", ""),
        "phase": design.get("phases", ["N/A"])[0] if design.get("phases") else "N/A",
        "study_type": design.get("studyType", ""),
        "enrollment": design.get("enrollmentInfo", {}).get("count", 0),
        "eligibility": {
            "criteria_text": eligibility_text,
            "inclusion_criteria": inclusion_text,
            "exclusion_criteria": exclusion_text,
            "min_age": eligibility.get("minimumAge", ""),
            "max_age": eligibility.get("maximumAge", ""),
            "sex": eligibility.get("sex", "ALL"),
            "healthy_volunteers": eligibility.get("healthyVolunteers", False)
        },
        "locations": _extract_locations(contacts.get("locations", [])),
        "overall_contact": contacts.get("overallOfficials", [{}])[0] if contacts.get("overallOfficials") else {},
        "last_update": protocol.get("statusModule", {}).get("lastUpdatePostDateStruct", {}).get("date", "")
    }

    return trial


def _create_trial_embedding_text(trial: Dict) -> str:
    """Create comprehensive text for trial-level embedding."""
    parts = []

    if trial.get("title"):
        parts.append(f"Title: {trial['title']}")

    if trial.get("brief_summary"):
        parts.append(f"Summary: {trial['brief_summary']}")

    parts.append(f"Phase: {trial.get('phase', 'N/A')}")

    eligibility = trial.get("eligibility", {})
    if eligibility.get("inclusion_criteria"):
        parts.append(f"Inclusion Criteria: {eligibility['inclusion_criteria']}")

    if eligibility.get("exclusion_criteria"):
        parts.append(f"Exclusion Criteria: {eligibility['exclusion_criteria']}")

    parts.append(f"Age: {eligibility.get('min_age', 'N/A')} to {eligibility.get('max_age', 'N/A')}")
    parts.append(f"Sex: {eligibility.get('sex', 'ALL')}")

    return NEWLINE_SEP.join(parts)


# =============================================================================
# DAG Definition
# =============================================================================
# Resolved once per scheduler parse. None means no timetable: the DAG is still
# registered and can be triggered by hand, it just never fires on its own.
# See the AIRFLOW_DAG_SCHEDULE block in 03- Config.py for why it is off.
DAG_SCHEDULE = _config_literal("AIRFLOW_DAG_SCHEDULE")


@dag(
    dag_id="trial_refresh_weekly",
    description="Weekly clinical trial index rebuild",
    schedule=DAG_SCHEDULE,
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    default_args={
        "owner": "trialmatch",
        "depends_on_past": False,
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
    },
    tags=["trialmatch", "production"],
)
def trial_refresh_weekly():

    @task(task_id="scrape_and_save")
    def scrape_and_save() -> str:
        """Scrape trials from ClinicalTrials.gov and save to disk."""
        import requests as req

        cfg = _load_config()
        MAX_TRIALS = cfg["MAX_TRIALS"]
        TRIAL_CONDITION = cfg["TRIAL_CONDITION"]
        TRIAL_STATUS = cfg["TRIAL_STATUS"]
        TRIAL_STUDY_TYPE = cfg["TRIAL_STUDY_TYPE"]

        base_url = "https://clinicaltrials.gov/api/v2/studies"
        trials = []
        page_token = None
        page_size = min(100, MAX_TRIALS)

        print(f"Scraping trials from ClinicalTrials.gov...")
        print(f"Filters: condition={TRIAL_CONDITION}, status={TRIAL_STATUS}, type={TRIAL_STUDY_TYPE}")

        while len(trials) < MAX_TRIALS:
            params = {
                "query.cond": TRIAL_CONDITION,
                "filter.overallStatus": TRIAL_STATUS,
                "filter.studyType": TRIAL_STUDY_TYPE,
                "pageSize": page_size,
                "format": "json"
            }

            if page_token:
                params["pageToken"] = page_token

            try:
                response = req.get(base_url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()

                studies = data.get("studies", [])
                if not studies:
                    print("No more trials found")
                    break

                for study in studies:
                    protocol = study.get("protocolSection", {})

                    design = protocol.get("designModule", {})
                    if design.get("studyType") != "INTERVENTIONAL":
                        continue

                    eligibility = protocol.get("eligibilityModule", {})
                    min_age_str = eligibility.get("minimumAge", "")
                    if min_age_str and "year" in min_age_str.lower():
                        try:
                            min_age = int(DIGIT_PATTERN.findall(min_age_str)[0])
                            if min_age > 18:
                                continue
                        except (IndexError, ValueError):
                            pass

                    trial = _parse_trial_metadata(protocol)
                    trials.append(trial)

                    if len(trials) >= MAX_TRIALS:
                        break

                print(f"Fetched {len(trials)} trials so far...")

                page_token = data.get("nextPageToken")
                if not page_token:
                    break

                time.sleep(1)

            except req.exceptions.RequestException as e:
                print(f"Error fetching trials: {e}")
                break

        if not trials:
            raise ValueError("No trials scraped")

        output_file = DATA_TRIAL_PATH + f"trials_{datetime.now().strftime('%Y%m%d')}.json"
        with open(output_file, "w") as f:
            json.dump(trials, f, indent=2)

        print(f"Total trials scraped and saved: {len(trials)}")
        return output_file


    @task(task_id="rebuild_index")
    def rebuild_index(trials_file: str) -> str:
        """Embed trials into staging collection, then atomic alias swap."""
        from openai import OpenAI
        from qdrant_client import QdrantClient
        from qdrant_client.models import (
            Distance, VectorParams, PointStruct,
            AliasOperations, CreateAlias, DeleteAlias
        )

        cfg = _load_config()
        COLLECTION_NAME = cfg["COLLECTION_NAME"]
        EMBEDDING_MODEL = cfg["EMBEDDING_MODEL"]
        EMBEDDING_DIM   = cfg["EMBEDDING_DIM"]

        openai_api_key = _read_key(path=KEYS_PATH, startswith_text="OpenAI key")
        qdrant_url     = _read_key(path=KEYS_PATH, startswith_text="Qdrant url")
        qdrant_api_key = _read_key(path=KEYS_PATH, startswith_text="Qdrant key")

        openai_client  = OpenAI(api_key=openai_api_key)
        qdrant_client  = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)

        with open(trials_file, "r") as f:
            trials = json.load(f)

        # --- Staging pattern (zero downtime) ---
        staging_name = f"trial_criteria_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        qdrant_client.create_collection(
            collection_name=staging_name,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE)
        )
        print(f"Created staging collection: {staging_name}")

        points_batch = []
        for trial_idx, trial in enumerate(trials):
            embedding_text = _create_trial_embedding_text(trial)

            response = openai_client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=embedding_text
            )
            embedding = response.data[0].embedding

            point_id = int(hashlib.md5(trial["nct_id"].encode()).hexdigest()[:16], 16)

            point = PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    "nct_id":          trial["nct_id"],
                    "title":           trial["title"],
                    "phase":           trial["phase"],
                    "bm25_text":       embedding_text,
                    "full_trial_json": trial
                }
            )
            points_batch.append(point)

            if len(points_batch) >= 100:
                qdrant_client.upsert(collection_name=staging_name, points=points_batch)
                points_batch = []

            if (trial_idx + 1) % 100 == 0:
                print(f"Embedded {trial_idx + 1} trials...")

            time.sleep(0.1)

        if points_batch:
            qdrant_client.upsert(collection_name=staging_name, points=points_batch)

        print(f"Indexed {len(trials)} trials into staging: {staging_name}")

        # --- Atomic alias swap ---
        try:
            qdrant_client.update_collection_aliases(
                change_aliases_operations=[
                    AliasOperations(delete_alias=DeleteAlias(alias_name=COLLECTION_NAME)),
                    AliasOperations(create_alias=CreateAlias(collection_name=staging_name, alias_name=COLLECTION_NAME))
                ]
            )
            print(f"Swapped alias '{COLLECTION_NAME}' -> '{staging_name}'")
        except Exception as e:
            if "not found" in str(e).lower():
                qdrant_client.update_collection_aliases(
                    change_aliases_operations=[
                        AliasOperations(create_alias=CreateAlias(collection_name=staging_name, alias_name=COLLECTION_NAME))
                    ]
                )
                print(f"Created alias '{COLLECTION_NAME}' -> '{staging_name}'")
            else:
                raise

        # --- Cleanup old collections (keep 2 most recent backups) ---
        collections = qdrant_client.get_collections().collections
        timestamped = sorted(
            [c.name for c in collections if c.name.startswith("trial_criteria_")],
            reverse=True
        )
        for old in timestamped[2:]:  # keep current + 1 backups
            qdrant_client.delete_collection(collection_name=old)
            print(f"Deleted old collection: {old}")

        print(f"Zero-downtime rebuild complete.")
        return staging_name


    @task(task_id="verify_index")
    def verify_index(staging_name: str):
        """Verify the new staging collection has sufficient points."""
        from qdrant_client import QdrantClient

        qdrant_url     = _read_key(path=KEYS_PATH, startswith_text="Qdrant url")
        qdrant_api_key = _read_key(path=KEYS_PATH, startswith_text="Qdrant key")
        client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)

        info = client.get_collection(staging_name)
        point_count = info.points_count

        print(f"Staging collection '{staging_name}' has {point_count} points")

        cfg = _load_config()
        min_expected = max(50, int(cfg["MAX_TRIALS"] * 0.5))
        if point_count < min_expected:
            raise ValueError(f"Expected >= {min_expected} trials (50% of MAX_TRIALS={cfg['MAX_TRIALS']}), found {point_count}")

        print("Index health check passed")


    # TaskFlow dependency chain (XCom passes data automatically)
    trials_file  = scrape_and_save()
    staging_name = rebuild_index(trials_file)
    verify_index(staging_name)


# Register DAG
trial_refresh_weekly()
'''


dag_content = dag_content_head + _dag_path_block + dag_content_tail


#------------------------------------------------------------------------------


# Write DAG file
# Item 20b: creating the dags/ directory and writing the file were both module
# level, so loading this file wrote to disk. dag_content itself stays at module
# level -- it is a string, building it touches nothing, and keeping it out here
# is what guarantees the generated bytes are unaffected by this change.
def write_dag_file(dags_root):
    """Write the generated DAG under dags_root, unless a different one is there.

    Returns the destination path. Refuses to overwrite a file whose text
    differs from dag_content, in case it was edited in place.
    """
    dag_dir = Path(dags_root) / 'dags'
    dag_dir.mkdir(parents=True, exist_ok=True)
    dag_file = dag_dir / 'trial_refresh_weekly.py'

    # If it exists
    if dag_file.exists():
        if dag_file.read_text() == dag_content:
            print(f"✓ DAG file already exists and matches this generator: {dag_file}")
        else:
            print(f"! DAG file already exists and DIFFERS from this generator: {dag_file}")
            print("  Not overwriting, in case it was edited in place.")
            print("  The scheduler parses that file, not the string in this one, so every")
            print("  edit made here -- including the schedule -- is inert until it is replaced.")
            print("  Delete it and re-run this file to regenerate.")
    else:
        dag_file.write_text(dag_content)
        print(f"✓ New DAG file created: {dag_file}")

    return dag_file


if __name__ == "__main__":
    write_dag_file(airflow_path)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb 16 17:08:13 2026

@author: ramyalsaffar
"""