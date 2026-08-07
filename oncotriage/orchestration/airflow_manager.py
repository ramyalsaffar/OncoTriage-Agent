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

"""Start, stop, inspect and trigger the Airflow services.

Moved out of "24- Airflow Manager.py" by item 20c, pass 3c-2. That file is now
a thin entry point holding its commented usage menu and nothing else.

THE PASSWORD ROUTE IS THE POINT OF THIS MODULE'S DOCSTRING
-----------------------------------------------------------
File 24 held ``AIRFLOW_PASSWORD = None`` at module level and ``_get_password()``
filled it in through ``global``. On the healthy path it read the password
Airflow generates into
``{airflow_path}/simple_auth_manager_passwords.json.generated``. And
``start_airflow()`` printed, to the operator:

    ⚠️  SET AIRFLOW_PASSWORD in this file to use trigger/status functions!

THAT INSTRUCTION IS WHY THE ROUTE HAD TO CHANGE. Once these functions live in a
package module, "this file" is the wrong file. An operator who followed the
printed instruction and set ``AIRFLOW_PASSWORD`` at the top of
"24- Airflow Manager.py" would be binding a name in the ENTRY POINT's namespace;
``_get_password`` resolves its globals in THIS module and would never see it.
Nothing would raise. The module would fall through to the generated-password
file and authenticate with the generated password, and the operator would have
no way to tell that the value they set was ignored -- the run succeeds. Pass
3c-2 demonstrated exactly that: setting the old name in the entry point leaves
``password_source()`` reporting ``password-file``, unchanged.

Note also that the instruction was ALREADY misleading before the move. The
module comment on the same variable said "Auto-read from password file (never
set manually)", which is the opposite advice, and the auto-read is what the code
did. So the printed line told the operator to do something the code did not
support, in a file where doing it would have worked by accident.

FOUR TIERS, and the module says which one it used
--------------------------------------------------
    1. ``password=`` argument   -- check_dag_status(password=...),
                                   trigger_dag(password=...), _get_token(...)
    2. ``set_airflow_password(value)``  -- the in-process setter
    3. ``ONCOTRIAGE_AIRFLOW_PASSWORD``  -- oncotriage.settings.ENV_AIRFLOW_PASSWORD
    4. the generated password file      -- what File 24 always did

Tier 4 is still the default, because the ordinary case is that Airflow chose the
password and nobody wants to. Tiers 1-3 exist so that an operator who DOES have
a chosen password has a route that works and is named in the printed message.

``password_source()`` reports which tier answered, without returning the secret.
EVERY PRINT IN THIS MODULE NAMES THE SOURCE AND NEVER THE VALUE, with no
exception as of pass 20f-3. ``start_airflow()`` used to print the generated
password it had just read out of the file -- pass 3c-2's one deliberate
exception, kept on the argument that a local development tool may print a
locally-generated credential to the terminal of the person who generated it, and
recorded as a follow-up. What closed the follow-up is that the four-tier route
made the print POINTLESS as well as leaky: tier 4 reads the same file, so
``check_dag_status`` and ``trigger_dag`` never needed a human to have seen it.
The only consumer who does is somebody logging into the web UI, and they are now
given the file's PATH. See the comment at the call site for the rest.

MEASURED, because pass 20f-3's brief said the password also reached
``api_server.log`` and it does not: that file is the SUBPROCESS's stdout
(``stdout=open(api_log_path, 'w')`` on the ``airflow api-server`` Popen), while
these prints go to the manager's own stdout. A 12 KB ``api_server.log`` from a
real run on the development machine contains zero occurrences of "password".
The leak was the terminal, and only the terminal.

WHAT IMPORTING THIS MODULE DOES
-------------------------------
Imports ``os``, ``json``, ``time``, ``subprocess``, ``pathlib``, ``requests``,
``oncotriage.settings`` and ``oncotriage.orchestration.home``. It starts no
process, opens no socket, reads no file and resolves no path. That statement is
worth more here than anywhere else in the package: before item 20b, LOADING
File 24 called ``start_airflow()`` and left two long-lived server processes
running. The default AIRFLOW_HOME comes from ``home.resolve_airflow_home``,
the one place in the package that reads ``paths.airflow_path``.
"""

import json
import os
import subprocess
import time
from pathlib import Path

import requests

from oncotriage import settings as path_settings
from oncotriage.orchestration.home import resolve_airflow_home
from oncotriage.observability import console


#------------------------------------------------------------------------------


# =============================================================================
# Configuration
# =============================================================================
AIRFLOW_URL = "http://localhost:8080"
AIRFLOW_USERNAME = "admin"
# Password: see the module docstring. Four tiers, argument first, generated
# password file last. _PASSWORD_STATE replaces File 24's `AIRFLOW_PASSWORD =
# None` module global -- the one an operator was told to edit and, after this
# move, could no longer reach.
#
# It is a dict rather than two module globals for the same reason _TOKEN_CACHE
# below is: a dict is mutated in place, so a function that writes it needs no
# `global` statement, and there is no second name that can be rebound in a
# namespace nobody reads.
_PASSWORD_STATE: dict = {"password": None, "source": None}
_TOKEN_CACHE: dict = {"token": None, "expires_at": 0.0}

DAG_ID = "trial_refresh_weekly"

PASSWORD_SOURCE_SETTER = "set_airflow_password"
PASSWORD_SOURCE_ENV = path_settings.ENV_AIRFLOW_PASSWORD
PASSWORD_SOURCE_FILE = "password-file"
"""Every value ``password_source()`` can report, besides None.

Named constants rather than bare strings because they are what a caller
ASSERTS on -- pass 3c-2's demonstration that the old route is dead reads
``password_source()`` and compares it to one of these.

THERE IS DELIBERATELY NO ``PASSWORD_SOURCE_ARGUMENT``, and the first draft of
this module had one. It was wrong: tier 1, the ``password=`` argument, is not
cached and does not record a source, precisely so that a one-off argument does
not become the process-wide answer for every later call. A constant naming a
value ``password_source()`` can never return is the sort of quietly-wrong
detail this project treats as a defect -- a caller would assert against it and
the assertion could only ever fail. After an argument call the source is
whatever it was before, which for a fresh module is None.
"""


#------------------------------------------------------------------------------


# =============================================================================
# 1. Start Airflow Services
# =============================================================================
def start_airflow(airflow_home=None):
    """Start API server and scheduler as background processes."""
    airflow_path = resolve_airflow_home(airflow_home)

    os.environ['AIRFLOW_HOME'] = airflow_path

    console.out("=" * 70)
    console.out("STARTING AIRFLOW SERVICES")
    console.out("=" * 70)

    # Start API Server
    api_log_path = Path(airflow_path) / 'api_server.log'
    api_server = subprocess.Popen(
        ['airflow', 'api-server', '--port', '8080'],
        env=os.environ.copy(),
        stdout=open(api_log_path, 'w'),
        stderr=subprocess.STDOUT
    )
    console.out(f"\n✓ API Server started (PID: {api_server.pid})")
    console.out(f"  Logs: {Path(airflow_path) / 'api_server.log'}")

    # Start Scheduler
    scheduler_log_path = Path(airflow_path) / 'scheduler.log'
    scheduler = subprocess.Popen(
        ['airflow', 'scheduler'],
        env=os.environ.copy(),
        stdout=open(scheduler_log_path, 'w'),
        stderr=subprocess.STDOUT
    )
    console.out(f"✓ Scheduler started (PID: {scheduler.pid})")
    console.out(f"  Logs: {Path(airflow_path) / 'scheduler.log'}")

    # Save PIDs for later shutdown
    pid_file = Path(airflow_path) / 'airflow_pids.json'
    with open(pid_file, 'w') as f:
        json.dump({
            'api_server_pid': api_server.pid,
            'scheduler_pid': scheduler.pid
        }, f)

    console.out(f"\n  PIDs saved to: {pid_file}")

    # Wait for services to start
    console.out("\nWaiting for API server to start...")
    for attempt in range(30):
        try:
            response = requests.get(f"{AIRFLOW_URL}/api/v2/monitor/health", timeout=2)
            if response.status_code == 200:
                console.out(f"\n✓ API Server is healthy! (took {attempt + 1} seconds)")
                console.out(f"  UI: {AIRFLOW_URL}")
                password_file = Path(airflow_path) / 'simple_auth_manager_passwords.json.generated'
                if password_file.exists():
                    # THE SECRET IS NOT PRINTED ANY MORE (pass 20f-3). This line
                    # used to read the JSON and print `Password: <the actual
                    # password>` to whatever terminal ran the manager -- into a
                    # scrollback, a `tee`, a CI job log, a screen share, and any
                    # `script`/`asciinema` recording. Item 20c-3c-2 kept it and
                    # recorded it as a follow-up, on the argument that it is a
                    # local development tool printing a locally-generated
                    # credential to the person who generated it. That argument
                    # was true and it stopped being the whole story once the
                    # four-tier route existed: THE PASSWORD NO LONGER HAS TO BE
                    # READ BY A HUMAN AT ALL for trigger/status to work, because
                    # _get_password() reads this same file itself (tier 4). A
                    # secret printed for no consumer is a secret leaked for no
                    # reason.
                    #
                    # WHAT AN OPERATOR DOES INSTEAD, in the two cases:
                    #   * status / trigger -- nothing. Tier 4 is automatic.
                    #   * the WEB UI at {AIRFLOW_URL}, which does need a human to
                    #     type it -- read the file named below. It is 0600 in
                    #     Airflow's own home and it is where Airflow put it.
                    #
                    # The file is opened and the admin key is looked up, so
                    # "the file exists" is not mistaken for "the file has your
                    # password in it" -- but only the VERDICT is printed.
                    with open(password_file, 'r') as f:
                        passwords = json.load(f)
                    console.out(f"\n  Username: admin")
                    if passwords.get('admin'):
                        console.out(f"  Password: not printed — read it from")
                        console.out(f"            {password_file}")
                    else:
                        console.out(f"  Password: the generated file exists but holds no "
                              f"'admin' entry:")
                        console.out(f"            {password_file}")
                    # THE MESSAGE THIS REPLACED SAID "SET AIRFLOW_PASSWORD in
                    # this file", which stopped being reachable the moment these
                    # functions moved into a package module -- see the module
                    # docstring. It now names routes that exist.
                    console.out(f"\n  ℹ️  trigger/status read that file themselves; they need")
                    console.out(f"     no further setup.")
                    console.out(f"     To use a DIFFERENT password, pick one route:")
                    console.out(f"       export {path_settings.ENV_AIRFLOW_PASSWORD}='...'")
                    console.out(f"       oncotriage.orchestration.airflow_manager.set_airflow_password('...')")
                    console.out(f"       python \"24- Airflow Manager.py\" status --password-stdin")
                break
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(1)
    else:
        console.out("\n⚠️  API Server did not respond within 30 seconds. Check api_server.log")
        console.out(f"  tail -f {Path(airflow_path) / 'api_server.log'}")

    console.out(f"\n{'=' * 70}")
    console.out("Scheduler will automatically run DAG every Sunday at 2:00 AM")
    console.out(f"{'=' * 70}")


# =============================================================================
# 2. Stop Airflow Services
# =============================================================================
def stop_airflow(airflow_home=None):
    """Stop API server and scheduler using saved PIDs."""
    import signal

    airflow_path = resolve_airflow_home(airflow_home)

    pid_file = Path(airflow_path) / 'airflow_pids.json'

    if not pid_file.exists():
        console.out("✗ No PID file found. Services may not be running.")
        return

    with open(pid_file, 'r') as f:
        pids = json.load(f)

    for name, pid in pids.items():
        try:
            os.kill(pid, signal.SIGTERM)
            console.out(f"✓ Sent SIGTERM to {name} (PID: {pid})")
        except ProcessLookupError:
            console.out(f"  {name} (PID: {pid}) was already stopped")
        except PermissionError:
            console.out(f"✗ Permission denied stopping {name} (PID: {pid})")

    # Wait up to 10s for processes to exit before declaring success

    deadline = time.time() + 10
    remaining = list(pids.items())
    while remaining and time.time() < deadline:
        time.sleep(0.5)
        still_running = []
        for name, pid in remaining:
            try:
                os.kill(pid, 0)  # signal 0 = existence check, no actual signal
                still_running.append((name, pid))
            except ProcessLookupError:
                console.out(f"  {name} confirmed stopped")
        remaining = still_running

    if remaining:
        console.out(f"\n⚠️  These processes did not exit within 10s: {[n for n,_ in remaining]}")
        console.out("  You may need to kill them manually.")

    pid_file.unlink()
    console.out("\n✓ Airflow services stopped")


# =============================================================================
# 3. Get JWT Token (required for Airflow 3 REST API v2)
# =============================================================================

def set_airflow_password(password):
    """Install the admin password for this process (tier 2 of four).

    Args:
        password: The password. Stored as given -- NOT stripped, unlike the
            environment variable, because a caller passing a literal has said
            exactly what they mean.

    Raises:
        ValueError: on an empty password. Storing "" would satisfy the
            "is not None" test in ``_get_password`` and then fail at the auth
            endpoint with a 401 that names nothing, which is the vacuous-value
            failure this project's rules single out.

    This is the replacement for editing ``AIRFLOW_PASSWORD`` at the top of a
    file, and it is a FUNCTION rather than a module variable on purpose: a
    module variable is exactly what stopped working when the code moved, and
    nothing prevents the same mistake from being made again against a name.
    """
    if not password:
        raise ValueError(
            "set_airflow_password() needs a non-empty password. "
            "To go back to the generated password file, call "
            "clear_airflow_password()."
        )
    _PASSWORD_STATE["password"] = password
    _PASSWORD_STATE["source"] = PASSWORD_SOURCE_SETTER


def clear_airflow_password():
    """Forget the installed password and any cached one, and drop the token.

    The token cache has to go with it: a JWT minted with the previous password
    stays valid for its five-minute TTL, so clearing the password alone would
    leave the next call authenticating as before and reporting a source it is
    not using.
    """
    _PASSWORD_STATE["password"] = None
    _PASSWORD_STATE["source"] = None
    _TOKEN_CACHE["token"] = None
    _TOKEN_CACHE["expires_at"] = 0.0


def password_source():
    """Which tier supplied the password in use, or None if none has been.

    Diagnostic. Returns one of the three PASSWORD_SOURCE_* constants, or None.
    THREE, not four: tier 1 (the ``password=`` argument) records no source, for
    the reason argued at the constants themselves -- a one-off argument must not
    become the process-wide answer. It
    RESOLVES NOTHING and READS NOTHING -- asking where the password came from
    must not be the thing that goes and gets one. (Same rule as
    ``deps.peek``/``resolution_state``, and for the same reason.)
    """
    return _PASSWORD_STATE["source"]


def _get_password(password=None, airflow_home=None) -> str:
    """Resolve the admin password through the four tiers. Caches the result.

    Args:
        password:     An explicit password (tier 1). Wins over everything and
                      is NOT cached -- a one-off argument must not silently
                      become the process-wide answer for every later call.
        airflow_home: Where the generated password file lives (tier 4 only).

    Returns:
        The password.

    Raises:
        FileNotFoundError: no explicit password, no environment variable, and
            no generated password file.
        ValueError: the file exists but holds no admin password.

    Tier order: argument, setter, environment, file. The first three are new in
    pass 3c-2; the fourth is what File 24 did, unchanged, including both of its
    error messages.
    """
    if password is not None:
        if not password:
            raise ValueError(
                "_get_password() was given an empty password argument"
            )
        return password

    if _PASSWORD_STATE["password"] is not None:
        return _PASSWORD_STATE["password"]

    env_password, env_source = path_settings.resolve_airflow_password()
    if env_password is not None:
        _PASSWORD_STATE["password"] = env_password
        # PASSWORD_SOURCE_ENV, not env_source -- pass 20c-3i.
        #
        # They are the same string: the constant IS
        # path_settings.ENV_AIRFLOW_PASSWORD, and that is exactly what
        # resolve_airflow_password() returns as its source. But storing
        # env_source left PASSWORD_SOURCE_ENV defined and NEVER READ anywhere
        # in the repository, which is the shape that shipped
        # PASSWORD_SOURCE_ARGUMENT in this same module and is why File 47 now
        # scans for it. A constant declared for callers to assert against, and
        # never used by the code it describes, is one rename away from being a
        # value password_source() can no longer return -- with the assertion
        # still compiling and now unfailable.
        _PASSWORD_STATE["source"] = PASSWORD_SOURCE_ENV
        console.out(f"[Airflow] Admin password read from {env_source}")
        return env_password

    airflow_path = resolve_airflow_home(airflow_home)
    password_file = Path(airflow_path) / 'simple_auth_manager_passwords.json.generated'
    if not password_file.exists():
        raise FileNotFoundError(
            f"Password file not found: {password_file}\n"
            "Run start_airflow() first, or supply the password directly:\n"
            f"    export {path_settings.ENV_AIRFLOW_PASSWORD}='...'\n"
            "    airflow_manager.set_airflow_password('...')\n"
            "    check_dag_status(password='...')"
        )
    with open(password_file, 'r') as f:
        passwords = json.load(f)
    file_password = passwords.get('admin', '')
    if not file_password:
        raise ValueError("No admin password found in password file")

    _PASSWORD_STATE["password"] = file_password
    _PASSWORD_STATE["source"] = PASSWORD_SOURCE_FILE
    return file_password


def _get_token(password=None, airflow_home=None) -> str:
    """Get JWT token from Airflow API server. Cached for 5 minutes."""

    if _TOKEN_CACHE["token"] and time.time() < _TOKEN_CACHE["expires_at"]:
        return _TOKEN_CACHE["token"]
    response = requests.post(
        f"{AIRFLOW_URL}/auth/token",
        json={"username": AIRFLOW_USERNAME,
              "password": _get_password(password, airflow_home)},
        headers={"Content-Type": "application/json"},
        timeout=10
    )
    if response.status_code != 201:
        raise RuntimeError(f"Auth failed ({response.status_code}): {response.text}")
    token = response.json()["access_token"]
    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expires_at"] = time.time() + 300  # 5-minute TTL
    return token

def _auth_headers(password=None, airflow_home=None) -> dict:
    """Get authorization headers with JWT token."""
    token = _get_token(password, airflow_home)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }


# =============================================================================
# 4. Check DAG Status (replaces file 20)
# =============================================================================
def check_dag_status(password=None, airflow_home=None):
    """Check if DAG is registered and get its status via REST API v2."""
    console.out("=" * 70)
    console.out("DAG STATUS CHECK")
    console.out("=" * 70)

    headers = _auth_headers(password, airflow_home)

    # Check DAG exists
    console.out(f"\n[1] Checking DAG '{DAG_ID}'...")
    response = requests.get(
        f"{AIRFLOW_URL}/api/v2/dags/{DAG_ID}",
        headers=headers,
        timeout=10
    )

    if response.status_code == 200:
        dag_info = response.json()
        console.out(f"  ✓ DAG '{DAG_ID}' is registered!")
        console.out(f"  Active: {not dag_info.get('is_paused', True)}")

        # Airflow 3's API v2 does not return 'schedule_interval' -- that field
        # was Airflow 2's, and reading it here printed "N/A" on every run,
        # whatever the DAG was actually set to. The schedule surfaces as
        # 'timetable_summary' (a NullTimetable, i.e. schedule=None, summarises
        # as "Never, external triggers only"); 'schedule' and
        # 'timetable_description' are read as alternates in case the build
        # returns one of those instead.
        #
        # Which key the value came from is printed, so "no schedule field in
        # the response" stays distinguishable from "this DAG has no schedule".
        schedule_field = None
        for candidate in ("schedule", "timetable_summary", "timetable_description"):
            if candidate in dag_info:
                schedule_field = candidate
                break

        if schedule_field is None:
            console.out("  Schedule: UNKNOWN - no schedule field in the API response")
            console.out(f"            (keys returned: {sorted(dag_info.keys())})")
        else:
            schedule_value = dag_info[schedule_field]
            if schedule_value in (None, "", "None"):
                console.out(f"  Schedule: DISABLED - no automatic runs  [{schedule_field}]")
            else:
                console.out(f"  Schedule: {schedule_value}  [{schedule_field}]")

        console.out(f"  Tags: {[t.get('name', '') for t in dag_info.get('tags', [])]}")
    elif response.status_code == 404:
        console.out(f"  ✗ DAG '{DAG_ID}' NOT FOUND")
        console.out("  Make sure the scheduler is running and has parsed the DAG file.")
        return
    else:
        console.out(f"  ✗ Error: {response.status_code} - {response.text}")
        return

    # Get recent DAG runs
    console.out(f"\n[2] Recent DAG runs:")
    response = requests.get(
        f"{AIRFLOW_URL}/api/v2/dags/{DAG_ID}/dagRuns",
        headers=headers,
        params={"limit": 5, "order_by": "-start_date"},
        timeout=10
    )

    if response.status_code == 200:
        runs = response.json().get("dag_runs", [])
        if runs:
            for run in runs:
                console.out(f"  - Run ID: {run.get('dag_run_id', 'N/A')}")
                console.out(f"    State: {run.get('state', 'N/A')}")
                console.out(f"    Start: {run.get('start_date', 'N/A')}")
                console.out(f"    End: {run.get('end_date', 'N/A')}")
                console.out()
        else:
            console.out("  No runs yet (DAG has not been triggered)")
    else:
        console.out(f"  Error fetching runs: {response.status_code}")

    # Get tasks
    console.out(f"[3] Tasks in DAG:")
    response = requests.get(
        f"{AIRFLOW_URL}/api/v2/dags/{DAG_ID}/tasks",
        headers=headers,
        timeout=10
    )

    if response.status_code == 200:
        tasks = response.json().get("tasks", [])
        for t in tasks:
            console.out(f"  - {t.get('task_id', 'N/A')}")
    else:
        console.out(f"  Error fetching tasks: {response.status_code}")

    console.out(f"\n{'=' * 70}")


# =============================================================================
# 5. Trigger DAG Manually (replaces file 21)
# =============================================================================
def trigger_dag(password=None, airflow_home=None):
    """Trigger DAG run via REST API v2."""
    console.out("=" * 70)
    console.out("MANUALLY TRIGGERING DAG")
    console.out("=" * 70)

    headers = _auth_headers(password, airflow_home)

    # Unpause DAG first (required for first run)
    unpause_response = requests.patch(
        f"{AIRFLOW_URL}/api/v2/dags/{DAG_ID}",
        headers=headers,
        json={"is_paused": False},
        timeout=10
    )
    if unpause_response.status_code not in (200, 204):
        console.out(f"⚠️  Could not unpause DAG: {unpause_response.status_code} - {unpause_response.text}")

    # Trigger
    response = requests.post(
        f"{AIRFLOW_URL}/api/v2/dags/{DAG_ID}/dagRuns",
        headers=headers,
        json={"conf": {}},
        timeout=10
    )

    if response.status_code in (200, 201):
        run = response.json()
        console.out(f"\n✓ DAG triggered successfully!")
        console.out(f"  Run ID: {run.get('dag_run_id', 'N/A')}")
        console.out(f"  State: {run.get('state', 'N/A')}")
        console.out(f"  Logical Date: {run.get('logical_date', 'N/A')}")
        console.out(f"\n  Monitor at: {AIRFLOW_URL}/dags/{DAG_ID}")
    else:
        console.out(f"\n✗ Failed to trigger DAG")
        console.out(f"  Status: {response.status_code}")
        console.out(f"  Response: {response.text}")

    console.out(f"{'=' * 70}")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb 16 17:23:47 2026

@author: ramyalsaffar
"""
