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

THIN ENTRY POINT (item 20c, pass 3c-2) WITH A REAL CLI (pass 20f-3)
--------------------------------------------------------------------
Every function lives in ``oncotriage/orchestration/airflow_manager.py``. What is
left here is an argparse front end over four of them, and nothing else.

WHAT THIS REPLACED, AND WHY IT HAD TO GO
-----------------------------------------
Until pass 20f-3 the ``__main__`` block was a COMMENTED MENU: four statements,
three of them commented out, and the operator's documented workflow was to edit
this file to switch between them. Pass 20c-3c-2 kept that menu byte-verbatim on
purpose -- it was the interface people had been reading for months and a
relocation pass is the wrong place to redesign one -- and recorded the argparse
CLI as its own follow-up. This is it. Three things fall out of it:

  * THE MENU'S OWN COMMENT NAMED A RETIRED ROUTE. It read "# After setting
    AIRFLOW_PASSWORD: Check status", and ``AIRFLOW_PASSWORD`` stopped being a
    thing this file could set the moment the functions moved into a package
    module. That comment is gone with the menu rather than corrected.

  * THE MODULE-SCOPE IMPORT WAS A RE-EXPORT AND IS NOT ANY MORE. Five names came
    in under ``# noqa: F401`` and exactly one was called; the other four existed
    so that uncommenting a menu line would not raise ``NameError``.
    ``tests/test_package_invariants.py`` section 5b(i) carried a two-name
    exemption for that (``stop_airflow``, ``trigger_dag`` -- the two named ONLY
    in comments, which no AST walk can see). All four names are called by the
    CLI below now, and THAT EXEMPTION TABLE IS DELETED, not emptied.

  * A BARE INVOCATION NO LONGER STARTS TWO SERVERS. ``python "24- Airflow
    Manager.py"`` used to run ``start_airflow()``, so the shortest possible
    command was also the heaviest. THIS IS A CONTRACT CHANGE and it is stated
    rather than slipped in: the subcommand is required, and a bare invocation
    prints usage and exits 2. It is the same argument item 20b made when it put
    a ``__main__`` guard on this file -- launching two long-lived processes
    should take an operator saying so -- and every documented command in
    CLAUDE.md was updated in the same commit.

THE PASSWORD ROUTE, AND WHY THIS CLI HAS NO ``--password``
-----------------------------------------------------------
``oncotriage/orchestration/airflow_manager.py`` resolves the admin password
through four tiers, first match wins:

    1. an explicit ``password=`` argument   (not cached)
    2. ``airflow_manager.set_airflow_password("...")``   (in-process setter)
    3. ``ONCOTRIAGE_AIRFLOW_PASSWORD``                   (environment)
    4. ``{airflow_path}/simple_auth_manager_passwords.json.generated``

TIER 4 IS THE DEFAULT AND NEEDS NO SETUP, so ``status`` and ``trigger`` work
with no password argument at all -- which is what they always did.

There is deliberately NO ``--password VALUE`` flag. Anything on a command line
is in the process table for every user on the machine and in the shell history
of this one. ``--password-stdin`` is offered instead: it reads one line from
standard input and passes it as tier 1, which is not cached, so a one-off
password does not become the process-wide answer.

    printf '%s\\n' "$AIRFLOW_ADMIN_PW" | python "24- Airflow Manager.py" status --password-stdin

Tier 2 is not reachable from a CLI and should not be -- a setter that only
lives for the length of one process is a Python API, and it is documented at the
function.

NO EXEC BOOTSTRAP AND NO RE-EXPORT SHIM. Nothing in the repository reads this
file's namespace.

Run from terminal:
    python "24- Airflow Manager.py" start
    python "24- Airflow Manager.py" status
    python "24- Airflow Manager.py" trigger
    python "24- Airflow Manager.py" stop
    python "24- Airflow Manager.py" --help
"""

import argparse
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

# EVERY ONE OF THESE FOUR IS CALLED BELOW. That is the whole difference between
# this import block and the one it replaced: there is no name here that only a
# commented line reaches, so section 5b(i) of tests/test_package_invariants.py
# needs no exemption for this file and no longer has an exemption table at all.
from oncotriage.orchestration.airflow_manager import (
    check_dag_status,
    start_airflow,
    stop_airflow,
    trigger_dag,
)


#------------------------------------------------------------------------------


def _password_from_stdin():
    """Read one line from stdin as the admin password (tier 1).

    Returns None when ``--password-stdin`` was not given, which is what the
    package functions take as "resolve it yourself through tiers 2 to 4".

    Only the trailing newline is stripped, not surrounding whitespace: a
    password may legitimately begin or end with a space, and silently trimming
    one produces a 401 that names nothing. An EMPTY line is refused here rather
    than passed on, so the diagnosis names the flag the operator used.
    """
    line = sys.stdin.readline()
    password = line[:-1] if line.endswith("\n") else line
    if not password:
        raise SystemExit(
            "--password-stdin was given but standard input was empty. "
            "Pipe the password in, or drop the flag to use the generated "
            "password file."
        )
    return password


def build_parser():
    """The CLI. Four subcommands, one per exported operation."""
    parser = argparse.ArgumentParser(
        prog='python "24- Airflow Manager.py"',
        description="Start, stop, inspect and trigger the Airflow services.",
        epilog="The admin password is read automatically from "
               "{AIRFLOW_HOME}/simple_auth_manager_passwords.json.generated. "
               "Set ONCOTRIAGE_AIRFLOW_PASSWORD, or use --password-stdin, to "
               "override it. There is no --password flag on purpose: a command "
               "line is visible in the process table.",
    )
    parser.add_argument(
        "--airflow-home", default=None, metavar="PATH",
        help="AIRFLOW_HOME to operate on. Default: the resolved airflow_path.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    # REQUIRED. A bare invocation used to start two servers; see the docstring.
    subparsers.required = True

    subparsers.add_parser(
        "start", help="start the API server and the scheduler in the background")
    subparsers.add_parser(
        "stop", help="stop both, using the PIDs saved by `start`")

    for _name, _help in (
        ("status", "report whether the DAG is registered, its recent runs and "
                   "its tasks"),
        ("trigger", "unpause the DAG and trigger one run"),
    ):
        _sub = subparsers.add_parser(_name, help=_help)
        _sub.add_argument(
            "--password-stdin", action="store_true",
            help="read the admin password from one line of standard input "
                 "instead of the generated password file",
        )

    return parser


def main(argv=None):
    """Dispatch one subcommand. Returns the process exit code."""
    args = build_parser().parse_args(argv)
    home = args.airflow_home

    if args.command == "start":
        start_airflow(airflow_home=home)
    elif args.command == "stop":
        stop_airflow(airflow_home=home)
    elif args.command == "status":
        password = _password_from_stdin() if args.password_stdin else None
        check_dag_status(password=password, airflow_home=home)
    elif args.command == "trigger":
        password = _password_from_stdin() if args.password_stdin else None
        trigger_dag(password=password, airflow_home=home)
    else:
        # Unreachable: subparsers.required makes argparse exit 2 first. Kept so
        # that a subcommand added to the parser and not to this dispatch is a
        # named failure rather than a silent no-op that exits 0.
        raise SystemExit(f"no handler for subcommand {args.command!r}")

    return 0


#------------------------------------------------------------------------------


# Item 20b: start_airflow() was called at module level here. Loading this file --
# reading it into any namespace for any reason -- launched two long-lived server
# processes with subprocess.Popen and left them running. That is the heaviest
# import-time side effect the codebase ever had. Behind the guard, loading does
# nothing.
if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb 16 17:23:47 2026

@author: ramyalsaffar
"""
