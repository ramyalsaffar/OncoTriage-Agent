"""
Sample 30 patients (10 breast, 10 colon, 10 lung) from the 1,000-patient batch run.

Creates a new SQLite database (inferences_sample_30.db) containing ONLY the
sampled patients' inferences and trial_matches rows.

Sampling:
    - Seed: 42 (reproducible)
    - Source: main pass inferences, deduplicated by patient_id
    - Stratified: 10 breast, 10 colon, 10 lung by primary_condition text
    - Output includes ALL inferences for sampled patients (main + resample)

Run from terminal:
    cd ".../03- Code"
    python "28- Select 30 Samples.py"

The docstring here used to say `exec(open(".../sample_30_patients.py").read())`.
No file of that name exists anywhere in the project — item 20a checked. The
sampler is this file; that line named a predecessor that is gone. It was a
comment, so it never ran and never raised.
"""

import sqlite3
import random
import os
import importlib.util


# =====================================================================
# PATHS (from 01- Imports.py)
# =====================================================================
# This file is not part of the exec chain -- it deliberately imports only
# sqlite3 and random rather than pulling in 01's model and client imports for
# two database queries. So it resolves main_path the way 01 does, by reading
# oncotriage_settings directly, rather than duplicating 01's literal.
#
# The settings module sits beside this file in the code directory, which
# __file__ locates. In a bare interactive paste __file__ is unbound and the
# working directory is the only remaining candidate -- taken, but announced.

if "__file__" in globals():
    _code_dir = os.path.dirname(os.path.abspath(__file__))
else:
    _code_dir = os.getcwd()
    print(f"[Paths] __file__ unbound; using the working directory as the code directory: {_code_dir}")

_settings_file = os.path.join(_code_dir, "oncotriage_settings.py")
if not os.path.isfile(_settings_file):
    raise RuntimeError(
        f"oncotriage_settings.py was not found beside this file: {_settings_file!r}"
    )
_spec = importlib.util.spec_from_file_location("oncotriage_settings", _settings_file)
path_settings = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(path_settings)

main_path, _main_path_source = path_settings.resolve_main_path()
print(f"[Paths] Project root: {main_path} (from {_main_path_source})")

source_db = main_path + "02- Data/03- Inferences Storage/inferences.db"
output_db = main_path + "04- Results/03- 30 Samples db/inferences_sample_30.db"


# =====================================================================
# CONFIGURATION
# =====================================================================

SEED = 42
PATIENTS_PER_CANCER = 10

CANCER_TYPES = {
    "breast": ["breast"],
    "colon":  ["colon", "colorectal", "rectal"],
    "lung":   ["lung", "small cell", "non-small cell", "nsclc", "sclc"],
}


def classify_cancer(primary_condition):
    if not primary_condition:
        return "other"
    lower = primary_condition.lower()
    for cancer_type, keywords in CANCER_TYPES.items():
        if any(kw in lower for kw in keywords):
            return cancer_type
    return "other"


# =====================================================================
# SAMPLE
# =====================================================================

print(f"Source: {source_db}")
print(f"Output: {output_db}")
print(f"Seed:   {SEED}")
print()

conn = sqlite3.connect(source_db)
conn.row_factory = sqlite3.Row

# Get unique patients (one per patient_id, lowest inference id)
patients = conn.execute("""
    SELECT patient_id, primary_condition, MIN(id) as first_id
    FROM inferences
    GROUP BY patient_id
    ORDER BY MIN(id)
""").fetchall()

print(f"Total unique patients in DB: {len(patients)}")

# Classify by cancer type
by_type = {"breast": [], "colon": [], "lung": []}

for p in patients:
    cancer = classify_cancer(p["primary_condition"])
    if cancer in by_type:
        by_type[cancer].append(p["patient_id"])

print(f"  Breast: {len(by_type['breast'])}")
print(f"  Colon:  {len(by_type['colon'])}")
print(f"  Lung:   {len(by_type['lung'])}")
print()

# Validate
for cancer_type, pids in by_type.items():
    if len(pids) < PATIENTS_PER_CANCER:
        raise ValueError(f"Not enough {cancer_type} patients: need {PATIENTS_PER_CANCER}, found {len(pids)}")

# Sample with seed 42. Local Random instance rather than random.seed():
# seeding the process-wide state would shift the draw of every other consumer
# of `random` in the same session. One rng shared across all three draws --
# the loop below consumes a single continuing stream, as it did when the
# global state was seeded once above it.
rng = random.Random(SEED)

sampled_pids = []
for cancer_type in ["breast", "colon", "lung"]:
    selected = rng.sample(by_type[cancer_type], PATIENTS_PER_CANCER)
    sampled_pids.extend(selected)
    print(f"  Sampled {cancer_type}: {len(selected)} patients")

print(f"\nTotal sampled patients: {len(sampled_pids)}")

# Get ALL inference ids for sampled patients (main + resample)
placeholders = ",".join("?" * len(sampled_pids))

inference_ids = [r["id"] for r in conn.execute(
    f"SELECT id FROM inferences WHERE patient_id IN ({placeholders})", sampled_pids
).fetchall()]

print(f"Total inferences for sampled patients: {len(inference_ids)}")

inf_placeholders = ",".join("?" * len(inference_ids))
trial_match_count = conn.execute(
    f"SELECT COUNT(*) FROM trial_matches WHERE inference_id IN ({inf_placeholders})", inference_ids
).fetchone()[0]

print(f"Total trial matches for sampled patients: {trial_match_count}")


# =====================================================================
# CREATE OUTPUT DB
# =====================================================================

if os.path.exists(output_db):
    os.remove(output_db)
    print(f"\nRemoved existing output file.")

out_conn = sqlite3.connect(output_db)
out_cursor = out_conn.cursor()

# Copy schema
schema_rows = conn.execute("""
    SELECT sql FROM sqlite_master 
    WHERE type='table' AND name IN ('inferences', 'trial_matches', 'drift_metrics')
    ORDER BY name
""").fetchall()

for row in schema_rows:
    out_cursor.execute(row["sql"])

# Copy inferences
inf_rows = conn.execute(
    f"SELECT * FROM inferences WHERE patient_id IN ({placeholders})", sampled_pids
).fetchall()

if inf_rows:
    col_count = len(inf_rows[0])
    out_cursor.executemany(
        f"INSERT INTO inferences VALUES ({','.join('?' * col_count)})",
        [tuple(r) for r in inf_rows]
    )

# Copy trial_matches
tm_rows = conn.execute(
    f"SELECT * FROM trial_matches WHERE inference_id IN ({inf_placeholders})", inference_ids
).fetchall()

if tm_rows:
    col_count = len(tm_rows[0])
    out_cursor.executemany(
        f"INSERT INTO trial_matches VALUES ({','.join('?' * col_count)})",
        [tuple(r) for r in tm_rows]
    )

out_conn.commit()


# =====================================================================
# VERIFY
# =====================================================================

verify_patients = out_conn.execute("SELECT COUNT(DISTINCT patient_id) FROM inferences").fetchone()[0]
verify_inferences = out_conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0]
verify_matches = out_conn.execute("SELECT COUNT(*) FROM trial_matches").fetchone()[0]

verify_types = {"breast": 0, "colon": 0, "lung": 0}
for row in out_conn.execute("SELECT DISTINCT patient_id, primary_condition FROM inferences"):
    cancer = classify_cancer(row[1])
    if cancer in verify_types:
        verify_types[cancer] += 1

out_conn.close()
conn.close()

print(f"\n{'='*60}")
print(f"OUTPUT: {output_db}")
print(f"{'='*60}")
print(f"  Unique patients:  {verify_patients}")
print(f"  Total inferences: {verify_inferences}")
print(f"  Trial matches:    {verify_matches}")
print(f"  Breast: {verify_types['breast']}  Colon: {verify_types['colon']}  Lung: {verify_types['lung']}")
print(f"  Seed: {SEED}")
print(f"{'='*60}")

assert verify_patients == 30, f"Expected 30 patients, got {verify_patients}"
assert verify_types["breast"] == 10, f"Expected 10 breast, got {verify_types['breast']}"
assert verify_types["colon"] == 10, f"Expected 10 colon, got {verify_types['colon']}"
assert verify_types["lung"] == 10, f"Expected 10 lung, got {verify_types['lung']}"

print("\nAll validations passed.")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Mar 16 20:55:18 2026

@author: ramyalsaffar
"""

