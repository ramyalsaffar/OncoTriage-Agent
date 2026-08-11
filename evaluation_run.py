# Evaluation Run
################

"""Run the pipeline over a stratified evaluation slice and persist what the two
downstream harnesses consume. Entry point.

THIS COSTS MONEY. Every selected patient is one real end-to-end run including a
live billed Stage 5 call. The default slice is ten patients, of the order of one
dollar at 2026-08 prices -- the twelve-fixture capture immediately before this
was written spent $1.14 in Stage 5. ``--scan-only`` classifies the cohort,
prints the selection and runs nothing, and it is free.

WHAT IT WRITES, AND FOR WHOM. One JSON per patient plus ``manifest.json``, under
a timestamped directory OUTSIDE this repository. Each record carries the run's
provenance, the exact patient-record text Stage 5 was shown, one separable
retrieval context per trial Stage 5 was sent, and every trial-level verdict with
its complete inclusion and exclusion criteria arrays. Those are the inputs to an
LLM rater of criterion decisions and to Ragas retrieval metrics; neither of
those harnesses is in this repository and neither is built here.

WHAT IT DOES NOT TOUCH. It writes no database -- ``log_inference`` is called by
the API layer and the batch runner and by nothing this reaches. It reads and
writes no characterization fixture; ``fixture_capture.py`` and
``fixture_replay.py`` are a different artifact for a different question, and the
twelve fixtures on disk are unaffected by every flag below.

WHY THIS FILE IS NOT NUMBERED. Same reason as ``fixture_capture.py``,
``fixture_replay.py``, ``measure_medcpt_scores.py`` and ``mcp_server.py``: the
numbered sequence says what you can run in pipeline order, and this is an
evaluation tool run by hand.

THIN ENTRY POINT. Every definition lives in
``oncotriage/evaluation/run_harness.py``, which documents the selection rule,
the record shape, the failure policy and what each persisted field is for.

USAGE
-----
    python evaluation_run.py --scan-only          # free: classify and report
    python evaluation_run.py                      # COSTS MONEY: 10 real runs
    python evaluation_run.py --select 30
    python evaluation_run.py --only <patient-id> --output-dir <existing run dir>
    python evaluation_run.py --output-dir /some/other/place

Exit codes:
    0 -- every selected patient produced a record and the post-check was clean
    1 -- refused before spending anything (no cohort, unusable index, unknown
         --only id, an existing manifest that could not be read)
    2 -- the run happened and something in it did not: a patient produced no
         record, or the post-check found a defect in what was written
"""


# Run needed file
#----------------
# The six-line package bootstrap. `pip install -e .` makes it a no-op.
import os
import sys

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


#------------------------------------------------------------------------------


if __name__ == "__main__":
    # Imported inside the guard, on the precedent of every other entry point
    # here: reading this file must not import langgraph, transformers or the
    # OpenAI client, and `--help` must not either. main() parses its arguments
    # before it resolves anything.
    from oncotriage.evaluation.run_harness import main

    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug 11 09:30:00 2026

@author: ramyalsaffar
"""
