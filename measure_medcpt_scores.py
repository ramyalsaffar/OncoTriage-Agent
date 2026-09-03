# MedCPT score calibration entry point
######################################

"""Measure the ``medcpt_score_max`` distribution and propose MEDCPT_SCORE_FLOOR.

    python measure_medcpt_scores.py
    python measure_medcpt_scores.py --sample-total 60 --seed 42
    python measure_medcpt_scores.py --floor -9.14 --json /tmp/medcpt.json

THIN ENTRY POINT. Every definition lives in
``oncotriage/evaluation/medcpt_calibration.py``, which documents what is run,
what it costs and how the sample is drawn.

IT IS NOT FREE, AND IT IS NOT EXPENSIVE. Stages 1-3 only, stopping before the
rule filter and before the billed eligibility call: ONE
``text-embedding-3-small`` call PER PATIENT -- not one per rerank query, which
is what this said until it was measured -- over ``SAMPLE_TOTAL`` patients.
$0.000074 for the whole run on the shipped corpus, 2026-09-03. MedCPT is local
and Qdrant is a read.

WHY THIS FILE IS NOT NUMBERED. Same reason as ``fixture_capture.py``,
``fixture_replay.py`` and ``mcp_server.py``: the numbered sequence says what you
can run in pipeline order, and this is a calibration tool run by hand after an
index rebuild, a rerank-query change, a cross-encoder checkpoint change or a
change to the GROUPING its pool is drawn through -- the four things that make
the floor in ``oncotriage/config.py`` stale.

Exit codes:
    0 -- the measurement completed
    1 -- no patient could be measured
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
    # THE CaffeinateSession IS INSIDE main(), NOT AROUND IT, and that is not a
    # style choice. Wrapping the call meant `--help` spawned a `caffeinate`
    # subprocess and killed it again just to print usage -- a side effect on a
    # pure query, which is the same defect pass 20c-3b removed from
    # fhir/explore.py's output_dir(). main() parses its arguments first and
    # takes the lock only around the measurement.
    from oncotriage.evaluation.medcpt_calibration import main

    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug  7 2026

@author: ramyalsaffar
"""
