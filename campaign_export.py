# Campaign Export
#################

"""Export one campaign's first main admissions as an evaluation run directory. Entry
point.

FREE. It reads the campaign database through a read-only connection, parses the
judge sample's bundles, and writes JSON. No pipeline stage runs and no model is
called. The output directory is what ``rater_run.py --run-dir`` and
``ragas_run.py --run-dir`` read.

THIN ENTRY POINT. Every definition lives in
``oncotriage/evaluation/campaign_export.py``, which documents the selection
rule, the parse rules and every refusal.

USAGE
-----
    python campaign_export.py --source-db <inferences.db> \\
        --campaign-id <runs.billing_campaign_id> \\
        --output-dir <new or empty directory> \\
        --fhir-dir <the bundle directory the campaign drew from>

All four arguments are required and none has a default.
The database's matching .attempt-history sidecar directory is also required.
Keep it with the database across backups/restores. Historical campaigns without
durable admission coverage refuse; later successes never replace missing work.

Exit codes:
    0 -- the export was written
    1 -- refused before writing anything; the printed code names why
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
    # Imported inside the guard: reading this file must not import the parser,
    # the registries or the agent.
    from oncotriage.evaluation.campaign_export import main

    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep 14 2026

@author: ramyalsaffar
"""
