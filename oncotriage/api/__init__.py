"""The HTTP serving layer.

Item 20c, pass 3b.

    server   "17- FastAPI Server.py" whole -- the FastAPI app, the lifespan
             handler that compiles the LangGraph pipeline, the four endpoints
             and the shared FHIR-bundle-to-MatchResponse helper.

WHY IT IS ITS OWN SUBPACKAGE rather than a module beside the agent: the agent
answers "which trials match this patient", and this answers "how does the
outside world ask". Nothing in ``oncotriage.agent`` imports this, and nothing
here is reachable except through HTTP or through the one entry point.

``uvicorn oncotriage.api.server:app`` is now a supported way to run the server,
which it was not while the definitions lived in a file whose name contains a
space and a leading digit. ``python "17- FastAPI Server.py"`` still works and is
still what CLAUDE.md documents.

THE APP OBJECT IS BUILT AT IMPORT, and that is the one deliberate exception to
this package's "importing does nothing" rule. ASGI servers take a
``module:attribute`` reference and expect an application object to be there, so
there is nowhere else for it to be. What matters is that building it opens no
client, loads no model, touches no database and reads no file: the graph is
compiled in the LIFESPAN handler, on startup, which is where it always was. See
``server.create_app``.

This ``__init__`` imports no submodule. ``import oncotriage.api`` stays free;
the caller names the module it wants.
"""

__all__ = ["server"]


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
