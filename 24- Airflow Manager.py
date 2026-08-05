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
# ===========================================================================
# EXEC CHAIN: 01
# ===========================================================================
# The functions below read airflow_path, os, Path, subprocess, json,
# requests and time -- all from 01- Imports.py. A first pass over this
# file with a plain AST walk also reported 'attempt', 'name' and
# 'candidate' as free and attributed them to 01/02; they are function
# locals and comprehension targets. A scope-aware symtable pass, which is
# what settled this list, does not report them. 02 and 03 are not needed.
#
# Item 20a: this file sits in the code directory, so __file__ locates it with
# no hardcoded path. __file__ is bound when the file is run as a script (every
# documented entry point for it) and when Spyder runfile()s it. In a bare
# interactive paste it is not bound, and the working directory is the only
# remaining candidate -- taken, but announced, never silently.
import os as _os_boot
if "__file__" in globals():
    _code_dir = _os_boot.path.dirname(_os_boot.path.abspath(__file__)) + _os_boot.sep
else:
    _code_dir = _os_boot.getcwd() + _os_boot.sep
    print(f"[Bootstrap] __file__ unbound; using the working directory as the code directory: {_code_dir}")
del _os_boot

with open(_code_dir + "01- Imports.py") as _fh:
    exec(_fh.read(), globals())


#------------------------------------------------------------------------------




# =============================================================================
# Configuration
# =============================================================================
AIRFLOW_URL = "http://localhost:8080"
AIRFLOW_USERNAME = "admin"
# Password is auto-generated on first start. Check:
#   1. The API server terminal output
#   2. Or the file: {airflow_path}/simple_auth_manager_passwords.json.generated
AIRFLOW_PASSWORD = None  # Auto-read from password file (never set manually)
_TOKEN_CACHE: dict = {"token": None, "expires_at": 0.0}

DAG_ID = "trial_refresh_weekly"


# =============================================================================
# 1. Start Airflow Services
# =============================================================================
def start_airflow():
    """Start API server and scheduler as background processes."""
    os.environ['AIRFLOW_HOME'] = airflow_path

    print("=" * 70)
    print("STARTING AIRFLOW SERVICES")
    print("=" * 70)

    # Start API Server
    api_log_path = Path(airflow_path) / 'api_server.log'
    api_server = subprocess.Popen(
        ['airflow', 'api-server', '--port', '8080'],
        env=os.environ.copy(),
        stdout=open(api_log_path, 'w'),
        stderr=subprocess.STDOUT
    )
    print(f"\n✓ API Server started (PID: {api_server.pid})")
    print(f"  Logs: {Path(airflow_path) / 'api_server.log'}")

    # Start Scheduler
    scheduler_log_path = Path(airflow_path) / 'scheduler.log'
    scheduler = subprocess.Popen(
        ['airflow', 'scheduler'],
        env=os.environ.copy(),
        stdout=open(scheduler_log_path, 'w'),
        stderr=subprocess.STDOUT
    )
    print(f"✓ Scheduler started (PID: {scheduler.pid})")
    print(f"  Logs: {Path(airflow_path) / 'scheduler.log'}")

    # Save PIDs for later shutdown
    pid_file = Path(airflow_path) / 'airflow_pids.json'
    with open(pid_file, 'w') as f:
        json.dump({
            'api_server_pid': api_server.pid,
            'scheduler_pid': scheduler.pid
        }, f)

    print(f"\n  PIDs saved to: {pid_file}")

    # Wait for services to start
    print("\nWaiting for API server to start...")
    for attempt in range(30):
        try:
            response = requests.get(f"{AIRFLOW_URL}/api/v2/monitor/health", timeout=2)
            if response.status_code == 200:
                print(f"\n✓ API Server is healthy! (took {attempt + 1} seconds)")
                print(f"  UI: {AIRFLOW_URL}")
                password_file = Path(airflow_path) / 'simple_auth_manager_passwords.json.generated'
                if password_file.exists():
                    with open(password_file, 'r') as f:
                        passwords = json.load(f)
                    print(f"\n  Username: admin")
                    print(f"  Password: {passwords.get('admin', 'check api_server.log')}")
                    print(f"\n  ⚠️  SET AIRFLOW_PASSWORD in this file to use trigger/status functions!")
                break
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(1)
    else:
        print("\n⚠️  API Server did not respond within 30 seconds. Check api_server.log")
        print(f"  tail -f {Path(airflow_path) / 'api_server.log'}")
    
    print(f"\n{'=' * 70}")
    print("Scheduler will automatically run DAG every Sunday at 2:00 AM")
    print(f"{'=' * 70}")


# =============================================================================
# 2. Stop Airflow Services
# =============================================================================
def stop_airflow():
    """Stop API server and scheduler using saved PIDs."""
    import signal

    pid_file = Path(airflow_path) / 'airflow_pids.json'

    if not pid_file.exists():
        print("✗ No PID file found. Services may not be running.")
        return

    with open(pid_file, 'r') as f:
        pids = json.load(f)

    for name, pid in pids.items():
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"✓ Sent SIGTERM to {name} (PID: {pid})")
        except ProcessLookupError:
            print(f"  {name} (PID: {pid}) was already stopped")
        except PermissionError:
            print(f"✗ Permission denied stopping {name} (PID: {pid})")

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
                print(f"  {name} confirmed stopped")
        remaining = still_running

    if remaining:
        print(f"\n⚠️  These processes did not exit within 10s: {[n for n,_ in remaining]}")
        print("  You may need to kill them manually.")
    
    pid_file.unlink()
    print("\n✓ Airflow services stopped")


# =============================================================================
# 3. Get JWT Token (required for Airflow 3 REST API v2)
# =============================================================================

def _get_password() -> str:
    """Read admin password from auto-generated file (cached after first call)."""
    global AIRFLOW_PASSWORD
    if AIRFLOW_PASSWORD is None:
        password_file = Path(airflow_path) / 'simple_auth_manager_passwords.json.generated'
        if not password_file.exists():
            raise FileNotFoundError(
                f"Password file not found: {password_file}\n"
                "Run start_airflow() first."
            )
        with open(password_file, 'r') as f:
            passwords = json.load(f)
        AIRFLOW_PASSWORD = passwords.get('admin', '')
        if not AIRFLOW_PASSWORD:
            raise ValueError("No admin password found in password file")
    return AIRFLOW_PASSWORD


def _get_token() -> str:
    """Get JWT token from Airflow API server. Cached for 5 minutes."""

    if _TOKEN_CACHE["token"] and time.time() < _TOKEN_CACHE["expires_at"]:
        return _TOKEN_CACHE["token"]
    response = requests.post(
        f"{AIRFLOW_URL}/auth/token",
        json={"username": AIRFLOW_USERNAME, "password": _get_password()},
        headers={"Content-Type": "application/json"},
        timeout=10
    )
    if response.status_code != 201:
        raise RuntimeError(f"Auth failed ({response.status_code}): {response.text}")
    token = response.json()["access_token"]
    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expires_at"] = time.time() + 300  # 5-minute TTL
    return token

def _auth_headers() -> dict:
    """Get authorization headers with JWT token."""
    token = _get_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }


# =============================================================================
# 4. Check DAG Status (replaces file 20)
# =============================================================================
def check_dag_status():
    """Check if DAG is registered and get its status via REST API v2."""
    print("=" * 70)
    print("DAG STATUS CHECK")
    print("=" * 70)

    headers = _auth_headers()

    # Check DAG exists
    print(f"\n[1] Checking DAG '{DAG_ID}'...")
    response = requests.get(
        f"{AIRFLOW_URL}/api/v2/dags/{DAG_ID}",
        headers=headers,
        timeout=10
    )

    if response.status_code == 200:
        dag_info = response.json()
        print(f"  ✓ DAG '{DAG_ID}' is registered!")
        print(f"  Active: {not dag_info.get('is_paused', True)}")

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
            print("  Schedule: UNKNOWN - no schedule field in the API response")
            print(f"            (keys returned: {sorted(dag_info.keys())})")
        else:
            schedule_value = dag_info[schedule_field]
            if schedule_value in (None, "", "None"):
                print(f"  Schedule: DISABLED - no automatic runs  [{schedule_field}]")
            else:
                print(f"  Schedule: {schedule_value}  [{schedule_field}]")

        print(f"  Tags: {[t.get('name', '') for t in dag_info.get('tags', [])]}")
    elif response.status_code == 404:
        print(f"  ✗ DAG '{DAG_ID}' NOT FOUND")
        print("  Make sure the scheduler is running and has parsed the DAG file.")
        return
    else:
        print(f"  ✗ Error: {response.status_code} - {response.text}")
        return

    # Get recent DAG runs
    print(f"\n[2] Recent DAG runs:")
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
                print(f"  - Run ID: {run.get('dag_run_id', 'N/A')}")
                print(f"    State: {run.get('state', 'N/A')}")
                print(f"    Start: {run.get('start_date', 'N/A')}")
                print(f"    End: {run.get('end_date', 'N/A')}")
                print()
        else:
            print("  No runs yet (DAG has not been triggered)")
    else:
        print(f"  Error fetching runs: {response.status_code}")

    # Get tasks
    print(f"[3] Tasks in DAG:")
    response = requests.get(
        f"{AIRFLOW_URL}/api/v2/dags/{DAG_ID}/tasks",
        headers=headers,
        timeout=10
    )

    if response.status_code == 200:
        tasks = response.json().get("tasks", [])
        for t in tasks:
            print(f"  - {t.get('task_id', 'N/A')}")
    else:
        print(f"  Error fetching tasks: {response.status_code}")

    print(f"\n{'=' * 70}")


# =============================================================================
# 5. Trigger DAG Manually (replaces file 21)
# =============================================================================
def trigger_dag():
    """Trigger DAG run via REST API v2."""
    print("=" * 70)
    print("MANUALLY TRIGGERING DAG")
    print("=" * 70)

    headers = _auth_headers()

    # Unpause DAG first (required for first run)
    unpause_response = requests.patch(
        f"{AIRFLOW_URL}/api/v2/dags/{DAG_ID}",
        headers=headers,
        json={"is_paused": False},
        timeout=10
    )
    if unpause_response.status_code not in (200, 204):
        print(f"⚠️  Could not unpause DAG: {unpause_response.status_code} - {unpause_response.text}")

    # Trigger
    response = requests.post(
        f"{AIRFLOW_URL}/api/v2/dags/{DAG_ID}/dagRuns",
        headers=headers,
        json={"conf": {}},
        timeout=10
    )

    if response.status_code in (200, 201):
        run = response.json()
        print(f"\n✓ DAG triggered successfully!")
        print(f"  Run ID: {run.get('dag_run_id', 'N/A')}")
        print(f"  State: {run.get('state', 'N/A')}")
        print(f"  Logical Date: {run.get('logical_date', 'N/A')}")
        print(f"\n  Monitor at: {AIRFLOW_URL}/dags/{DAG_ID}")
    else:
        print(f"\n✗ Failed to trigger DAG")
        print(f"  Status: {response.status_code}")
        print(f"  Response: {response.text}")

    print(f"{'=' * 70}")


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