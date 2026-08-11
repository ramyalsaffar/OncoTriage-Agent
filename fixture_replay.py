# Characterization Fixture Replay
#################################

"""
Replays every fixture fixture_capture.py wrote and reports every difference.
Entry point.

RENAMED, NOT MOVED, IN PASS 20d-2 (was "46- Fixture Replay.py").
Every other test in this project now lives under tests/; these two do not, and the reason is what they
are FOR rather than where they came from. They are not tests: they are a
manually-run gate that items 22 and 64 consume -- capture COSTS MONEY (twelve
real end-to-end runs at Stage 5 prices) and replay is the free check that the
pipeline still does what it did. Nothing runs them as part of a suite, nothing
should, and putting them beside the suite would invite exactly that. They keep
their numbers only in the mapping file, tests/FILE NUMBER MAPPING.md.

The fixture directory is unaffected either way, and that was verified rather
than assumed: ``oncotriage/fixtures/capture.py:fixture_root()`` globs
``paths.main_path`` -- the PROJECT root, from ONCOTRIAGE_MAIN_PATH or the
fallback -- not the code directory, so where this entry point sits has no
bearing on where fixtures are found.

The replay harness is ``oncotriage/fixtures/replay.py``; item 20c pass 3d moved
it there. This file is a ``__main__`` block and the one import it needs.

WHAT IT DOES
------------
For each fixture:

  1. Re-parses the source FHIR bundle with the CURRENT parser, rather than
     feeding back the patient dict stored in the fixture. Reusing the stored dict
     would feed the recorded answer in as an input and make that half of the diff
     vacuous.
  2. Runs the pipeline with all three model boundaries served from the recording
     — OpenAI embeddings, the MedCPT cross-encoder, and the Stage 5 chat
     completion. No request reaches OpenAI, and no local model is loaded at all.
  3. Rebuilds the deterministic prefix with the same function that wrote it
     (``build_deterministic_prefix``, in ``oncotriage.fixtures.capture``) and
     diffs it field by field.
  4. Reports every difference by dotted field path with both values.

Qdrant is the one boundary that is NOT replayed. The trial corpus is the fixed
input the fixture is pinned against, so retrieval runs live and the recorded
per-channel NCT order is what proves the two runs asked the same questions.

THE IMPORT ABOVE IS ORDER-SENSITIVE, AND THAT IS CHECKED, NOT ASSUMED
----------------------------------------------------------------------
``oncotriage.fixtures.replay`` sets ``ONCOTRIAGE_DEFER_LOCAL_MODELS=1`` above its
own imports, because ``oncotriage.agent.deps`` reads that variable ONCE at its
own import. If anything imported the agent before this line, the read already
happened and MedCPT (~110 MB) and FastEmbed load for real while the run still
prints "Local models: not loaded". This file therefore imports NOTHING from
``oncotriage`` except the replay module, and ``main()`` calls
``assert_local_models_deferred()`` before it reads a fixture — which checks the
variable reached ``deps`` AND that neither ``torch`` nor ``transformers`` is in
``sys.modules``.

WHAT A DIFFERENCE MEANS
-----------------------
Not automatically a defect. It means the current code no longer does what it did
when the fixture was captured, and something has to explain why. The three
innocent explanations, in the order they should be checked:

  - a tunable in ``oncotriage/config.py`` changed. ``environment.tunables``
    records the ones the prefix depends on; this run prints any that moved.
  - the pinned Qdrant collection's CONTENTS changed in place. Pinning the
    resolved name catches an alias swap, not an edit to the collection behind it
    — which is why the content digest is checked too.
  - the fixture is stale for a known, intended behaviour change, and should be
    re-captured with a note.

Everything else is item 20 having changed an answer.

FIVE REFUSALS RUN BEFORE ANYTHING IS REPLAYED, in this order and for this reason:
the dependency seam (negative control FIRST — the assertion must FAIL with no
override installed, or it proves nothing), then the positive control, then the
OpenAI tripwire, then the pinned collection NAME, then its CONTENTS digest. Only
then is a fixture replayed, so a difference is never reported against a different
index.

EXIT CODES
----------
0   every fixture loaded and replayed clean.
1   at least one fixture replayed with a DIFFERENCE or a replay miss -- the
    pipeline no longer does what it did. Also the code for the five refusals
    above (seam, tripwire, collection name, collection digest), which all mean
    the same thing: what would have been replayed is not comparable.
2   at least one fixture FAILED TO LOAD and nothing that did load replayed
    differently. A stale file at an older schema version lying in the fixture
    directory; migrate or delete it. Nothing replayed differently.

1 WINS WHEN BOTH OCCUR. A stale file is housekeeping and a changed pipeline is
a finding; collapsing them into one code, which is what this file did until the
codes were split, meant a re-capture that left one old file behind was
indistinguishable from a pipeline regression. Both are always printed in the
summary -- only the code collapses -- and the summary states which code is
being returned and why.

NO RE-EXPORT SHIM. All 27 top-level names were grepped against every .py, .md,
.toml and .yml in the tree; every hit is prose in File 45, File 13 or
``oncotriage/agent/deps.py``, or an exec-bootstrap local.

USAGE
-----
    python fixture_replay.py                     # replay all, exit 0 if clean
    python fixture_replay.py --only normal_1
    python fixture_replay.py --max-diffs 5       # truncate per-fixture output
    python fixture_replay.py --fixture-dir <dir>
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

from oncotriage.fixtures.replay import main


#------------------------------------------------------------------------------


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug  4 09:16:00 2026

@author: ramyalsaffar
"""
