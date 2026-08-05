"""SQLite persistence for inference results.

Item 20c, pass 2b.

    database_logger   "14- Database Logger.py" whole — the three-table schema,
                      the two additive-migration tables, ``initialize_database``
                      and ``log_inference``.

TWO THINGS CHANGED ON THE WAY IN, and neither is cosmetic:

  * ``log_inference`` takes ``db_path``. It used to read an ``inferences_path``
    global out of the shared exec namespace, which five test files rebound
    before loading File 14 in order to keep their writes off the production
    database. A module cannot see a caller's globals, so that redirect would
    have stopped working silently the moment this file became a module — five
    tests writing to the real inferences.db, each of them still printing that it
    had used a temporary one. See ``resolve_inference_db_path``.
  * ``_resolve_primary_cancer`` calls ``load_registry()`` instead of reading a
    ``_CANCER_REGISTRY`` global that "13- LangGraph Agent.py" happened to have
    defined earlier in the chain. Same cached singleton, no layering violation,
    and it no longer raises NameError in any chain that loads 14 without 13.

Importing this module opens no database. Item 20b turned schema creation into a
function for that reason and pass 2b keeps it: nine other files load File 14 or
are loaded beside it, and every one of them was touching inferences.db just by
being read.
"""

__all__ = ["database_logger"]


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
