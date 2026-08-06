"""Airflow: the database, the generated DAG, and the service manager.

Item 20c, pass 3c-2.

    airflow_setup     "22- Airflow Database.py" whole -- `airflow db migrate`,
                      `airflow db check`, and the in-place rewrite of
                      airflow.cfg that configures the admin user.

    dag_generator     "23- Airflow DAG.py" whole -- the three string pieces
                      that make up the `trial_refresh_weekly` DAG, and the
                      writer that puts them under {airflow_path}/dags/.

    airflow_manager   "24- Airflow Manager.py" whole -- start and stop the two
                      background processes, and check / trigger the DAG through
                      the REST API v2.

WHY THIS IS ITS OWN SUBPACKAGE. It is the only part of the project that manages
a SECOND scheduler process rather than doing work itself. Nothing in the
pipeline imports it, and it imports nothing from ``agent``, ``retrieval`` or
``storage``: the DAG it generates re-implements the scrape and the index build
as a self-contained string, because the scheduler parses that string in a
process that has none of this package on its path. That duplication is a real
cost and it is recorded in ``dag_generator``'s docstring, not hidden here.

THE THREE FILES SHARE ONE THING AND IT IS ``airflow_path``. Each reads it
lazily, through ``oncotriage.paths``, inside a function, and each takes an
``airflow_home`` argument that overrides it. None of them resolves a directory
at import.

WHAT NONE OF THESE MODULES DOES AT IMPORT: run a subprocess, start a process,
open a socket, write a file, create a directory, or resolve a path. That is
worth stating for this subpackage in particular, because before pass 3c-2 two
of the three numbered files DID exactly those things when merely loaded --
"22-" ran the whole Airflow setup and "24-" launched two long-lived server
processes with subprocess.Popen. Item 20b put both behind ``__main__`` guards;
this pass moves the code somewhere a guard is not the only thing standing
between a reader and a running scheduler.

This ``__init__`` imports no submodule, so ``import oncotriage.orchestration``
pulls in neither ``requests`` nor ``subprocess`` work. The caller names the
module it wants.
"""

__all__ = ["airflow_setup", "dag_generator", "airflow_manager"]


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
