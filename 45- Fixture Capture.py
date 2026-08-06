# Characterization Fixture Capture
##################################

"""
Records what the pipeline does today, so item 20 can prove what it broke.
Entry point.

The capture harness is ``oncotriage/fixtures/capture.py``; item 20c pass 3d
moved it there. This file is a ``__main__`` block and the one import it needs.

WHY IT EXISTS
-------------
Item 20 restructures ~6,000 lines across 27 files. Nothing else in this repo will
tell you whether the restructured pipeline still produces the same answers: 18-
and 19- hit a live server and check shapes, 30- through 44- test one component
each, and the ablation study measures configurations against each other rather
than against a past self. This is the missing baseline.

It is a RECORDING, not a test suite. It asserts almost nothing about whether the
pipeline is correct. It captures what the pipeline currently does, byte for byte
where that is meaningful, and 46- replays it and reports every difference. A
difference is not automatically a defect — but after a 6,000-line refactor, an
unexplained difference is the only warning you get.

THE FIXTURE FORMAT IS FROZEN AT SCHEMA VERSION 3. The twelve fixtures on disk are
v3 and ``load_fixture()`` refuses a mismatch. Fixtures are stored gzipped, one
per file, at ``compresslevel=9, mtime=0`` — the zeroed mtime is what makes two
captures of identical content produce identical bytes rather than a git diff on
every re-capture. See ``oncotriage/fixtures/capture.py`` for the field-by-field
description of every section: identity, environment, derivation, inputs,
deterministic_prefix and recordings.

THIS COSTS MONEY. Every fixture is a real end-to-end pipeline run, including a
live Stage 5 call. A full capture is twelve of them plus a cohort scan and up to
``--probe-limit`` retrieval probes.

SAFETY: THIS FILE CANNOT WRITE TO THE PRODUCTION inferences.db
---------------------------------------------------------------
It used to chain ``14- Database Logger.py``, with ``inferences_path`` redirected
to a scratch database, for one reason its own comment gave: File 13's terminal
nodes called ``_resolve_primary_cancer()``, which lived in File 14. Pass 20c-2b
moved that function to ``oncotriage/registries/primary_cancer.py`` and
``oncotriage/agent/terminal.py`` imports it from there, so the chain outlived its
reason by two passes and pass 3d removed it, along with the redirect that existed
only to make it safe.

What survives is the pair of guards, because their job was never the chain: a
``log_inference`` that RAISES, and ``_assert_database_is_isolated()``, which runs
before anything is captured and makes five checks — three non-degeneracy checks
about the resolver, one that the real writer is not bound in the module under any
name, and one that the tripwire still raises.

NO RE-EXPORT SHIM, AND THAT ANSWER CHANGED DURING THE PASS. All 101 top-level
names were grepped against every .py, .md, .toml and .yml in the tree first. The
only consumer of any of them is ``46- Fixture Replay.py``, which this same pass
converts and which now imports them from ``oncotriage.fixtures.capture``. After
that nothing chains this file and nothing reads a name out of it, so a shim would
be re-exports with no reader.

USAGE
-----
    python "45- Fixture Capture.py"                    # scan, select, capture all
    python "45- Fixture Capture.py" --scan-only        # cohort scan + case report
    python "45- Fixture Capture.py" --probe-limit 400  # widen the no-candidates hunt
    python "45- Fixture Capture.py" --only normal_1 gpt4o_retry_constructed
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

from oncotriage.fixtures.capture import main


#------------------------------------------------------------------------------


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug  4 09:15:00 2026

@author: ramyalsaffar
"""
