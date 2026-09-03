# MMR redundancy measurement entry point
#######################################

"""Measure near-duplicate redundancy in Stage 4's kept pools and simulate MMR.

    python measure_mmr_redundancy.py
    python measure_mmr_redundancy.py --patients 300 --budget-minutes 60
    python measure_mmr_redundancy.py --analyse-only <pools.json> --threshold 0.8
    python measure_mmr_redundancy.py --bm25-only          # $0, NOT production's pool

THIN ENTRY POINT. Every definition lives in
``oncotriage/evaluation/mmr_redundancy.py``, which documents what is run, what
it costs, how the cohort is drawn and what the measurement cannot see.

MEASUREMENT ONLY. IT CHANGES NO PIPELINE BEHAVIOUR. It drives four nodes,
reads what they produce and re-selects a top-k in its own list. Nothing it
computes is written back into a state dict, a database, a fixture or a config
constant, and the MMR ruling it reports against is the operator's to make.

IT IS NOT FREE, AND IT IS NOT EXPENSIVE. Stages 1-4 only, stopping before the
billed eligibility call: Stage 2's dense channel makes ONE
``text-embedding-3-small`` call per patient and Stages 1, 3 and 4 call no
priced endpoint at all. Three hundred patients is an upper bound of about
$0.0006, printed before the first call. STAGE 5 IS NEVER REACHED, and that is
structural rather than a promise -- the module imports no evaluation node and
calls no graph -- while ``boto3.client`` is patched to record and REFUSE for
the whole run, with the count reported in the artefact.

WHY THIS FILE IS NOT NUMBERED. ``measure_medcpt_scores.py``'s reason, exactly:
the numbered sequence says what you can run in pipeline order, and this is an
analysis run by hand to answer one question about a possible change.

Exit codes:
    0 -- the measurement completed and the report was written
    1 -- no patient could be measured (nothing to report over)
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
    # The import is INSIDE the guard, as it is in every other entry point here:
    # reading this file must resolve no path, build no client and load no
    # model, so `--help` costs nothing. The CaffeinateSession is inside main()
    # for measure_medcpt_scores.py's reason -- wrapping the call would spawn a
    # `caffeinate` subprocess just to print usage.
    from oncotriage.evaluation.mmr_redundancy import main

    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Sep  2 2026

@author: ramyalsaffar
"""
