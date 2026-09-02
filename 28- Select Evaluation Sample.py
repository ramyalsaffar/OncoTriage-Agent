# Select the Evaluation Sample
##############################

"""
Draw the stratified evaluation sample — entry point.

Creates a new SQLite database containing ONLY the sampled patients' inferences
and trial_matches rows. The sampler itself is
``oncotriage/evaluation/sampling.py``; item 20c pass 3d moved it there.

RENAMED IN PASS 20e, from "28- Select 30 Samples.py". The old name baked the
SAMPLE SIZE into a filename, which is the one thing about this file most likely
to change: the size is 10 per cancer group, three groups, and it is a
CONSTANT IN THE SAMPLER, not a property of the entry point. Widening the sample
to 20 per group under a file called "Select 30 Samples" would leave the
filename asserting something false with nothing to catch it -- a filename is not
checked by anything. The current size is stated below and in the sampler, where
it can be kept true. The number stays 28 so that every note and document naming
File 28 still resolves; see "PIPELINE SEQUENCE.md".

Current size: 10 breast + 10 colon + 10 lung = 30, seed 42.

Sampling
--------
    Seed 42, reproducible.
    Source: the production inferences.db, deduplicated by patient_id.
    Stratified: 10 breast, 10 colon, 10 lung, classified on primary_condition.
    The output carries ALL inferences for each sampled patient (main + resample).

THIS FILE HAD NO ``__main__`` GUARD BEFORE ITEM 20c PASS 3d
------------------------------------------------------------
Every statement but one function ran at module level, so reading this file into
any namespace opened the production inferences.db, sampled it, DELETED the
existing output database and rewrote it — as a side effect of being read. Item
20b guarded Files 15, 16, 17, 22 and 24 and pass 3c-2 guarded File 29; nothing
reached this one, because nothing loads it. CLAUDE.md's claim that File 29 was
"the LAST UNGUARDED FILE in the repository" is true only in its literal form —
File 29 had no function at all — and this was the second one.

An earlier version of this docstring said the sampler was
``exec(open(".../sample_30_patients.py").read())``. No file of that name exists
anywhere in the project; item 20a checked. It was a comment, so it never ran and
never raised.

THE DESTINATION IS AN ARGUMENT. ``select_samples(source_db, output_db)`` requires
both and has no defaults, on the ``empty_database(db_path, flag)`` and
``download_all_collections(output_dir)`` precedent: this function ``os.remove``s
the output database before rebuilding it, so a plausible thing to type while
exploring a module must not delete the file somebody's evaluation report was
built from. ``--source-db`` and ``--output-db`` override; with no flags this
entry point passes the two ``default_*`` accessors, which resolve the historical
locations and create nothing.

Run from terminal:
    cd ".../03- Code"
    python "28- Select Evaluation Sample.py"
    python "28- Select Evaluation Sample.py" --output-db <scratch>/sample.db
"""

import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# See the same block in "04- FHIR Generate Data.py" and "29- Download Qdrant
# Data.py". `pip install -e .` from 03- Code/ makes it a no-op.
try:
    import oncotriage  # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals()
         else None, "__file__"),
        (os.getcwd(), "cwd"),
    ):
        if _candidate and os.path.isdir(os.path.join(_candidate, "oncotriage")):
            if _candidate not in sys.path:
                sys.path.insert(0, _candidate)
            print(f"[Bootstrap] oncotriage package found at {_candidate} "
                  f"(via {_how}); added to sys.path")
            break
    else:
        raise
    del _candidate, _how

import argparse

from oncotriage.evaluation.sampling import (
    SAMPLE_DB_FILENAME,
    SAMPLE_DB_SUBDIR,
    SAMPLE_TOTAL,
    default_output_db,
    default_source_db,
    select_samples,
)


#------------------------------------------------------------------------------


def _parse_args(argv=None):
    """Both paths default to None so the historical locations are resolved
    INSIDE the guard rather than while the parser is built -- they are lazy
    globs over the sibling tree, and `--help` must not fire them.

    THE RENDERED NUMBERS COME FROM THE SAMPLER, NOT FROM THIS FILE. The
    description used to say "30-patient" and the --output-db help used to quote
    "03- 30 Samples db/inferences_sample_30.db", both retyped -- three literals
    describing a default this file does not own, in the same file whose own
    RENAME (pass 20e, from "28- Select 30 Samples.py") was argued on exactly
    that ground. Widening the draw would have left the help advertising a
    destination the program no longer writes to, and `--help` is the one place
    a reader checks before overriding it. SAMPLE_DB_SUBDIR and
    SAMPLE_DB_FILENAME are the same two strings default_output_db() joins onto
    results_path, so the help cannot disagree with the default.

    IMPORTING THEM RESOLVES NOTHING. All three are computed from
    SAMPLE_TOTAL at the sampler's import -- one int and two strings, no
    filesystem -- and the lazy glob is in default_output_db(),
    which is still called only inside the guard. The {results_path}
    placeholder is kept verbatim for that reason -- rendering the real
    directory here would fire the glob while the parser is built.
    """
    parser = argparse.ArgumentParser(
        description=f"Extract a seeded, stratified {SAMPLE_TOTAL}-patient "
                    "sample of inferences.db into its own database."
    )
    parser.add_argument(
        "--source-db", default=None,
        help="Inference database to sample FROM. Default: paths.inferences_path",
    )
    parser.add_argument(
        "--output-db", default=None,
        help="Destination. REMOVED AND REBUILT if it exists. Default: "
             f"{{results_path}}/{SAMPLE_DB_SUBDIR}/{SAMPLE_DB_FILENAME}",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    _args = _parse_args()
    select_samples(
        _args.source_db if _args.source_db else default_source_db(),
        _args.output_db if _args.output_db else default_output_db(),
    )


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Mar 16 20:55:18 2026

@author: ramyalsaffar
"""
