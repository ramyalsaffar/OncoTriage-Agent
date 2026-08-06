"""Characterization fixtures: record what the pipeline does, and prove it still does.

Item 20c, pass 3d.

    capture   "45- Fixture Capture.py" whole -- the schema, the recording sink,
              the four proxies, ``build_deterministic_prefix``, the fixture I/O,
              the three derivation recipes, the constructed retry fixture, the
              cohort scan and the selection.

    replay    "46- Fixture Replay.py" whole -- the replay stand-ins, the OpenAI
              tripwire, the field-by-field diff, and the five refusals that run
              before anything is replayed.

THIS IS THE HARNESS THAT VERIFIES EVERY OTHER PASS OF ITEM 20c, which makes
"12/12 replayed clean" the one result in this project that proves nothing on its
own: a harness that has stopped OBSERVING also replays clean. Two things guard
against that, and both are in ``replay``:

  * ``assert_hooks_reach_the_agent()`` is run as a NEGATIVE CONTROL first, with
    no override installed, and the run refuses to proceed unless it FAILS. The
    thing it guards costs money to get wrong -- see ``oncotriage/agent/deps.py``
    -- so an assertion that had only ever passed would be worth nothing here.
  * ``assert_local_models_deferred()`` measures that MedCPT and FastEmbed really
    were not loaded, rather than relying on the environment variable having been
    set in the right order.

``replay`` imports ``capture``; ``capture`` imports nothing from ``replay``. The
edge is one-way and deliberate: the two files must build the deterministic
prefix and apply a derivation recipe with the SAME code, or the diff compares
one implementation against another instead of comparing today's pipeline against
yesterday's.

IMPORTING ``replay`` SETS ``ONCOTRIAGE_DEFER_LOCAL_MODELS=1``, above its own
imports, and that is the one module-level side effect anywhere in this package.
It has to be at import: ``oncotriage.agent.deps`` reads the variable once, at
its own import, so an assignment below the import block would reach nothing and
silently load ~110 MB of model on every replay. See that module's docstring.

Nothing else in the repository imports either module.
"""


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
