"""
The Streamlit monitoring dashboard (pass 20c-3c-1).

"21- Streamlit Dashboard.py" was 5,481 lines and bootstrapped by exec'ing Files
01 and 02 and chaining File 03. Streamlit RE-RUNS THE WHOLE SCRIPT ON EVERY
INTERACTION and ``exec_chain`` caches nothing, so every click re-read and
re-executed all three files -- which, because "03- Config.py" calls the client
factories at shim load, meant every click also CONSTRUCTED AN OPENAI CLIENT AND
A QDRANT CLIENT for a dashboard that uses neither. As modules, each body runs
once per process.

That is a semantic change as well as a speedup: module-level mutable state now
persists across reruns instead of being rebuilt. The dashboard has exactly two
such objects, ``MATCH_TIERS`` and ``MATCH_TIER_COLORS`` in
``oncotriage.dashboard.tiers``, and the argument that persistence is safe for
both is recorded there and re-derived by check 6a of
"47- Package Split Test.py".

THIS ``__init__`` IMPORTS NOTHING. Importing ``oncotriage.dashboard`` must not
pull in streamlit, plotly and pandas -- "47- Package Split Test.py" imports
every package module under a socket / sqlite / open trap, and a convenience
re-export here would put the whole dashboard behind every one of those imports.
Reach for ``oncotriage.dashboard.app.main`` directly.
"""


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
