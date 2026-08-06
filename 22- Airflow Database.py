# Creat Airflow Database
########################

"""
Initialize the Airflow metadata database and configure the admin user.

THIN ENTRY POINT (item 20c, pass 3c-2)
--------------------------------------
``setup_airflow()`` moved to ``oncotriage/orchestration/airflow_setup.py``. What
is left here is a ``__main__`` guard, one call, and the failure print.

NO EXEC BOOTSTRAP. This file used to ``exec()`` "01- Imports.py" purely to get
four names -- ``airflow_path``, ``os``, ``Path`` and ``subprocess`` -- which is
the whole third-party import block of the project, including torch,
transformers, streamlit and langgraph, to run two `airflow db` subcommands.
Those four are ordinary imports in the package module now.

NO RE-EXPORT SHIM. Nothing in the repository reads this file's namespace: its
two top-level names (``setup_airflow``, ``success``) were grepped against every
.py, .md, .toml and .yml in the tree, ``setup_airflow`` has no hit outside this
file, and every ``success`` hit is a same-named local or a JSON key elsewhere.

WHAT THIS DOES TO YOUR MACHINE, unchanged from before the move: it runs
`airflow db migrate`, runs `airflow db check`, and REWRITES
{AIRFLOW_HOME}/airflow.cfg in place to add the simple_auth_manager admin user.

Run from terminal:
    python "22- Airflow Database.py"
"""

import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# See the same block in "04- FHIR Generate Data.py" and "16- Database Query.py".
# `pip install -e .` makes it a no-op; without it the code directory is added to
# sys.path and the fact is printed rather than left silent.
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

from oncotriage.orchestration.airflow_setup import setup_airflow


#------------------------------------------------------------------------------


# Run setup
# Item 20b: this call was unguarded, so loading this file ran the entire
# Airflow setup -- created the database, rewrote airflow.cfg, generated the
# admin password file. Behind the guard, loading it does nothing and
# `python "22- Airflow Database.py"` behaves exactly as before.
if __name__ == "__main__":
    success = setup_airflow()

    if not success:
        print("\n✗ Setup failed")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb 16 16:41:29 2026

@author: ramyalsaffar
"""
