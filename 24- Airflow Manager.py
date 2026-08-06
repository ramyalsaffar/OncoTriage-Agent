# Airflow Manager (Start, Stop, Status, Trigger)
##################################################
#
# All operations are in Python.
#
# Airflow 3.1.7 requires two background processes:
#   1. API Server (web UI + REST API)
#   2. Scheduler (parses DAGs + executes tasks)
#
# These are started via subprocess.Popen (unavoidable for server processes).
# All status checks and DAG triggering use the REST API v2 via requests.
#
###############################################################################

"""
Start, stop, inspect and trigger the Airflow services.

THIN ENTRY POINT (item 20c, pass 3c-2)
--------------------------------------
Every function moved to ``oncotriage/orchestration/airflow_manager.py``. What is
left here is the commented usage menu, unchanged, behind its ``__main__`` guard.

THE PASSWORD ROUTE CHANGED, AND IT HAD TO
------------------------------------------
This file used to hold ``AIRFLOW_PASSWORD = None`` at module level, and
``start_airflow()`` printed "SET AIRFLOW_PASSWORD in this file to use
trigger/status functions!". Once the functions live in a package module, setting
a name HERE cannot reach THEIR globals -- and nothing would raise: the module
would fall through to the generated password file and authenticate successfully
with a password the operator did not choose.

So the route is explicit now, four tiers, and the printed message names them:

    check_dag_status(password="...")             # argument
    airflow_manager.set_airflow_password("...")  # in-process setter
    export ONCOTRIAGE_AIRFLOW_PASSWORD='...'     # environment
    (otherwise: {airflow_path}/simple_auth_manager_passwords.json.generated)

``airflow_manager.password_source()`` reports which tier answered, and never the
secret.

NOTE ON THE MENU BELOW: it is kept BYTE-VERBATIM from before the move, including
its comment "# After setting AIRFLOW_PASSWORD: Check status". That comment names
the RETIRED route. It is left in place because the menu is the interface an
operator has been reading for months and this pass does not redesign it -- see
the four tiers above for what to do instead. Replacing the commented menu with a
real argparse CLI is the right end state and is a redesign; it is recorded as a
follow-up, not built here.

NO EXEC BOOTSTRAP and NO RE-EXPORT SHIM. Nothing in the repository reads this
file's namespace -- all twelve top-level names were grepped against every .py,
.md, .toml and .yml in the tree, and not one has a hit outside this file.

Run from terminal:
    python "24- Airflow Manager.py"
"""

import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# See the same block in "04- FHIR Generate Data.py" and "16- Database Query.py".
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

# start_airflow is the one the menu below calls live. The other four are
# imported because the menu's COMMENTED lines name them: uncommenting a line is
# the documented way to use this file, and an import that only appears when you
# uncomment something is a NameError waiting for the operator who does.
# set_airflow_password is here for the same reason -- it is the password route
# the docstring above tells you to use, and it has to be reachable from this
# namespace for that instruction to be true.
from oncotriage.orchestration.airflow_manager import (  # noqa: F401
    check_dag_status,
    set_airflow_password,
    start_airflow,
    stop_airflow,
    trigger_dag,
)


#------------------------------------------------------------------------------


# =============================================================================
# USAGE: Uncomment ONE of the following to run
# =============================================================================
# Item 20b: start_airflow() was called here unguarded. Loading this file --
# reading it into any namespace for any reason -- launched two long-lived
# server processes with subprocess.Popen and left them running. That is the
# heaviest import-time side effect in the codebase, and it is why item 20b's
# own instructions say not to run this file.
#
# Behind the guard, loading does nothing and
# `python "24- Airflow Manager.py"` behaves exactly as before.
if __name__ == "__main__":

    # First time: Start services
    start_airflow()

    # After setting AIRFLOW_PASSWORD: Check status
    # check_dag_status()

    # Manually trigger a run
    # trigger_dag()

    # When done: Stop services
    # stop_airflow()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb 16 17:23:47 2026

@author: ramyalsaffar
"""
