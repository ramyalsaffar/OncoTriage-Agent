"""Stratified 30-patient sample extracted into its own SQLite database.

Moved out of ``28- Select Evaluation Sample.py`` by item 20c, pass 3d.
``28- Select Evaluation Sample.py`` survives as a THIN ENTRY POINT and keeps no
re-export shim: all 40 of its top-level names were grepped against every
``.py``, ``.md``, ``.toml`` and ``.yml`` in the tree, and every hit is either a
third-party import name (``os``, ``sqlite3``, ``random``, ``importlib``), an
identically-named local somewhere else (``conn``, ``row``, ``rng``, ``p``,
``patients``, ``cancer``, ``selected``, ``placeholders``, ``inference_ids``,
``cancer_type``), a name this module no longer defines (``main_path``,
``path_settings``, ``_main_path_source`` -- see the conversion note below), or
prose. Nothing reads a name out of File 28.

FILE 28 WAS UNGUARDED, and CLAUDE.md said the last such file was File 29
-------------------------------------------------------------------------
That statement is true only in its literal form -- File 29 had no function at
all -- and File 28 was the second one: it defined ``classify_cancer`` and ran
every other statement at module level with no ``if __name__ == "__main__"``
guard. So ``exec(open("28- Select Evaluation Sample.py").read())`` or any import of it
opened the production ``inferences.db``, sampled it, DELETED the existing output
database and rewrote it, as a side effect of being read. Item 20b guarded Files
15, 16, 17, 22 and 24 and pass 20c-3c-2 guarded File 29; nothing reached this
one, because nothing loads it. It is guarded now.

THE DESTINATIONS ARE REQUIRED ARGUMENTS WITH NO DEFAULTS
--------------------------------------------------------
``select_samples(source_db, output_db)`` takes both. This follows
``empty_database(db_path, flag)`` and ``download_all_collections(output_dir)``
for the same reason: this function ``os.remove()``s ``output_db`` before
rebuilding it, so ``select_samples()`` -- a plausible thing to type while
exploring a module -- must not be a command that deletes a file somebody else's
report is built on. ``default_source_db()`` and ``default_output_db()`` resolve
the historical locations lazily and CREATE NOTHING; the entry point passes them.

THE SETTINGS MODULE IS NOW A PACKAGE IMPORT, NOT A BY-LOCATION LOAD
--------------------------------------------------------------------
File 28 located ``oncotriage_settings.py`` beside itself and exec'd it through
``importlib.util.spec_from_file_location``, on the argument that this file is
not in the exec chain and should not pull in File 01's model and client imports
for two database queries. That argument was right and its conclusion is now
obsolete: ``oncotriage.paths`` imports ``oncotriage.settings`` and nothing else,
resolves every path on first READ, and costs nothing to import. The by-location
load would additionally register a SECOND copy of the settings module under the
name ``oncotriage_settings`` in ``sys.modules``, alongside the one
``oncotriage.paths`` already holds -- two modules with two ``_RESOLVED`` caches
answering the same question, which is the duplicate-import hazard
``pyproject.toml`` already argues against for the numbered scripts.

The paths themselves are also better derived than rebuilt. File 28 wrote
``main_path + "02- Data/03- Inferences Storage/inferences.db"`` as a literal,
which is ``paths.inferences_path`` spelled out -- a second construction site for
one value, and one that would not follow a renumbering of the sibling tree that
``_glob_one`` handles. Both defaults go through ``oncotriage.paths`` now.

WHAT IMPORTING THIS MODULE DOES
-------------------------------
Nothing. No path is resolved, no database is opened, no file is removed.
"""

import os
import random
import sqlite3
import threading

from oncotriage import paths


#------------------------------------------------------------------------------


# ===========================================================================
# CONFIGURATION
# ===========================================================================

SEED = 42
PATIENTS_PER_CANCER = 10

CANCER_TYPES = {
    "breast": ["breast"],
    "colon":  ["colon", "colorectal", "rectal"],
    "lung":   ["lung", "small cell", "non-small cell", "nsclc", "sclc"],
}

# The historical output location, relative to results_path. File 28 spelled the
# whole thing out from main_path; results_path is the glob-resolved parent, so a
# renumbering of "04- Results" follows here as it does everywhere else.
SAMPLE_DB_SUBDIR = "03- 30 Samples db"
SAMPLE_DB_FILENAME = "inferences_sample_30.db"

# The three tables copied into the output database, in the order File 28 read
# them out of sqlite_master (ORDER BY name). drift_metrics is created empty and
# never populated -- File 28 copied the schema of all three and rows from two,
# and that is preserved: a sample database that silently lacked the table would
# not open in a tool built against the production schema.
COPIED_TABLES = ("inferences", "trial_matches", "drift_metrics")


#------------------------------------------------------------------------------


# ===========================================================================
# LAZY PATHS
# ===========================================================================
#
# Resolved on first CALL and cached, never at import: oncotriage/paths.py globs
# the sibling tree on first read, and a module that did it at import would raise
# on any machine without one.
#
# NEITHER CREATES A DIRECTORY. This is the output_dir()/ensure_output_dir()
# lesson from pass 20c-3b: a caller that only wants to know where the file would
# go must be able to ask without anything appearing on disk. File 28 created
# nothing either -- it opened the output database, which sqlite3 creates, in a
# directory it assumed existed.

_RESOLVED = {}
_RESOLVE_LOCK = threading.RLock()


def default_source_db() -> str:
    """The production inference database this samples FROM. Read only.

    ``paths.inferences_path``. File 28 rebuilt this string from ``main_path``
    with the sibling directory names written out; this is the same value from
    the one place that owns it.
    """
    with _RESOLVE_LOCK:
        if "source_db" not in _RESOLVED:
            _RESOLVED["source_db"] = paths.inferences_path
        return _RESOLVED["source_db"]


def default_output_db() -> str:
    """Where the 30-patient extract has historically been written. Creates nothing."""
    with _RESOLVE_LOCK:
        if "output_db" not in _RESOLVED:
            _RESOLVED["output_db"] = os.path.join(
                paths.results_path, SAMPLE_DB_SUBDIR, SAMPLE_DB_FILENAME)
        return _RESOLVED["output_db"]


#------------------------------------------------------------------------------


def classify_cancer(primary_condition):
    """Map a primary_condition string onto one of the three sampled groups.

    Byte-for-byte File 28's function. Anything that matches none of the three
    keyword sets is "other" and is not sampled.
    """
    if not primary_condition:
        return "other"
    lower = primary_condition.lower()
    for cancer_type, keywords in CANCER_TYPES.items():
        if any(kw in lower for kw in keywords):
            return cancer_type
    return "other"


#------------------------------------------------------------------------------


def select_samples(source_db, output_db, seed=SEED,
                   patients_per_cancer=PATIENTS_PER_CANCER):
    """Extract 10 breast + 10 colon + 10 lung patients into a new database.

    Both paths are REQUIRED -- see the module docstring. ``output_db`` is
    DELETED if it exists and then rebuilt.

    The body is File 28's module-level statement sequence in its original order,
    with nothing reordered and nothing removed. The only substantive difference
    is that ``sys.exit``-by-exception is unchanged (the four ``assert``s and the
    ``ValueError`` still propagate) and the two connections are closed in a
    ``finally`` so a failure part way through does not leave the production
    database open -- File 28 closed them on the success path only, and a
    ``ValueError`` from the count validation left a read handle on
    ``inferences.db`` until the process exited.

    Args:
        source_db:           production inferences.db, opened read-only in effect
                             (this function issues no write against it).
        output_db:           destination; removed first if present.
        seed:                sampling seed. 42 reproduces the shipped sample.
        patients_per_cancer: how many of each of the three groups to draw.

    Returns:
        dict: the verification counts, so a caller can assert on them rather
        than parse the printed report. File 28 asserted inline and printed;
        both are kept, and the dict is what makes the function testable.
    """
    print(f"Source: {source_db}")
    print(f"Output: {output_db}")
    print(f"Seed:   {seed}")
    print()

    conn = sqlite3.connect(source_db)
    conn.row_factory = sqlite3.Row
    out_conn = None
    try:
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
            if len(pids) < patients_per_cancer:
                raise ValueError(f"Not enough {cancer_type} patients: need {patients_per_cancer}, found {len(pids)}")

        # Sample with seed 42. Local Random instance rather than random.seed():
        # seeding the process-wide state would shift the draw of every other consumer
        # of `random` in the same session. One rng shared across all three draws --
        # the loop below consumes a single continuing stream, as it did when the
        # global state was seeded once above it.
        rng = random.Random(seed)

        sampled_pids = []
        for cancer_type in ["breast", "colon", "lung"]:
            selected = rng.sample(by_type[cancer_type], patients_per_cancer)
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

        # Copy schema. The table list is a parameterised IN clause rather than
        # the interpolated literal File 28 wrote, so COPIED_TABLES is the one
        # place the set is stated; the ORDER BY name is unchanged.
        table_placeholders = ",".join("?" * len(COPIED_TABLES))
        schema_rows = conn.execute(
            f"SELECT sql FROM sqlite_master "
            f"WHERE type='table' AND name IN ({table_placeholders}) "
            f"ORDER BY name", COPIED_TABLES
        ).fetchall()

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
    finally:
        # File 28 closed both only on the success path, so a ValueError from the
        # count validation above left a handle open on the production database.
        if out_conn is not None:
            out_conn.close()
        conn.close()

    print(f"\n{'='*60}")
    print(f"OUTPUT: {output_db}")
    print(f"{'='*60}")
    print(f"  Unique patients:  {verify_patients}")
    print(f"  Total inferences: {verify_inferences}")
    print(f"  Trial matches:    {verify_matches}")
    print(f"  Breast: {verify_types['breast']}  Colon: {verify_types['colon']}  Lung: {verify_types['lung']}")
    print(f"  Seed: {seed}")
    print(f"{'='*60}")

    expected_total = patients_per_cancer * len(verify_types)
    assert verify_patients == expected_total, f"Expected {expected_total} patients, got {verify_patients}"
    assert verify_types["breast"] == patients_per_cancer, f"Expected {patients_per_cancer} breast, got {verify_types['breast']}"
    assert verify_types["colon"] == patients_per_cancer, f"Expected {patients_per_cancer} colon, got {verify_types['colon']}"
    assert verify_types["lung"] == patients_per_cancer, f"Expected {patients_per_cancer} lung, got {verify_types['lung']}"

    print("\nAll validations passed.")

    return {
        "output_db": output_db,
        "patients": verify_patients,
        "inferences": verify_inferences,
        "trial_matches": verify_matches,
        "by_type": dict(verify_types),
        "seed": seed,
    }


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
