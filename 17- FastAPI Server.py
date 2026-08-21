# FastAPI Server
################

"""
FastAPI REST API Server

THIN ENTRY POINT (item 20c, pass 3b)
------------------------------------
The app, the lifespan handler, the models and all four endpoints moved to
``oncotriage/api/server.py``. What is left here is the ``app`` re-export and the
``uvicorn.run`` call.

The old header said "Zero imports. Zero redundancy. All libraries, config, and
pipeline logic come from exec()-chaining scripts 01 → 02 → 03 → 07 → 13 → 14
into this file's namespace." That is no longer true and is the point of the
pass: there is no exec chain here at all. Reading this file no longer loads
torch, transformers, streamlit, matplotlib and langgraph, no longer builds an
OpenAI and a Qdrant client, and no longer compiles anything.

``app`` IS RE-EXPORTED AT MODULE LEVEL, AND THAT IS LOAD-BEARING.
``docker-compose.yml`` line 73 runs

    uvicorn "17- FastAPI Server:app" --host 0.0.0.0 --port 8000 --reload

and it works -- ``importlib.import_module`` does not require a valid Python
identifier, only a file the path finder can locate, so a module name with a
space and a leading digit imports fine as long as nobody writes an ``import``
STATEMENT for it. Verified rather than assumed. Removing ``app`` from this
file's namespace would break the container.

Endpoints:
    POST /match           — FHIR bundle as JSON body → matched trials
    POST /match/file      — FHIR bundle as file upload → matched trials
    GET  /health          — Health check + pipeline readiness
    GET  /pipeline/info   — Pipeline configuration and trial count

Run from terminal:
    cd ".../03- Code"
    python "17- FastAPI Server.py"          this file
    uvicorn oncotriage.api.server:app       the package module, same app

NO RE-EXPORT SHIM BEYOND ``app``. Nothing in the repository reads this file's
namespace: every top-level name it bound was grepped against every .py, .md,
.toml and .yml in the tree, and the only hits are ``app`` in docker-compose.yml
(above) and a prose mention of ``_run_matching_pipeline`` in a comment in
"25- Batch Runner.py".
"""

import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# See the same block in "04- FHIR Generate Data.py" and "11- RAG Trial
# Indexer.py". `pip install -e .` makes it a no-op; without it the code
# directory is added to sys.path and the fact is printed rather than left
# silent. Docker takes this path too: the image copies the code directory to
# /app and uvicorn's working directory is /app.
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

import uvicorn

# The ASGI application. Re-exported, not rebuilt -- `uvicorn "17- FastAPI
# Server:app"` and `uvicorn oncotriage.api.server:app` must reach the SAME
# object, or the two documented ways of running the server would be two
# different servers.
from oncotriage.api.server import app

# The port this file binds. ONE OWNER (oncotriage/config.py:API_PORT), because
# the same number is also the target of "18- FastAPI Server Test.py" and
# "19- FastAPI Server Batch Test.py"; it was written out at all three sites and
# is now written once. The value is unchanged.
#
# THE CONTAINER DOES NOT COME THROUGH HERE. docker-compose.yml runs
# `uvicorn "17- FastAPI Server:app" --host 0.0.0.0 --port 8000` -- an argument
# vector that imports `app` from this module and never reaches the __main__
# block below. So this constant governs `python "17- FastAPI Server.py"` and the
# two harnesses; the compose literals agree with it by discipline, and the
# argument for that is at API_PORT itself.
#
# Importing config here opens no client, reads no file and resolves no path:
# every factory in that module is lazy and API_PORT is a plain assignment.
from oncotriage.config import API_PORT


# ===========================================================================
# MAIN
# ===========================================================================

if __name__ == "__main__":
    # Run from terminal — NOT Spyder.
    uvicorn.run(app, host="0.0.0.0", port=API_PORT)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 19:11:06 2026

@author: ramyalsaffar
"""
