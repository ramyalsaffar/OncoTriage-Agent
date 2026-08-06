# Characterization Fixture Replay
#################################

"""
Replays every fixture 45- wrote and reports every difference. Entry point.

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

EXIT CODE
---------
0 only when every fixture replays clean. Any difference, any replay miss, any
collection mismatch, any load failure -> 1.

NO RE-EXPORT SHIM. All 27 top-level names were grepped against every .py, .md,
.toml and .yml in the tree; every hit is prose in File 45, File 13 or
``oncotriage/agent/deps.py``, or an exec-bootstrap local.

USAGE
-----
    python "46- Fixture Replay.py"                     # replay all, exit 0 if clean
    python "46- Fixture Replay.py" --only normal_1
    python "46- Fixture Replay.py" --max-diffs 5       # truncate per-fixture output
    python "46- Fixture Replay.py" --fixture-dir <dir>
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
