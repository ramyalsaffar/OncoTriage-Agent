# Creat Airflow Database
########################

"""Initialise the Airflow metadata database and configure the admin user.

Moved out of "22- Airflow Database.py" by item 20c, pass 3c-2. That file is now
a thin entry point: a ``__main__`` guard, one call, and the failure print.

WHAT THIS DOES TO A MACHINE, stated first because it is not read-only
--------------------------------------------------------------------
``setup_airflow()`` runs ``airflow db migrate`` (creates or upgrades
airflow.db), runs ``airflow db check``, and REWRITES ``airflow.cfg`` IN PLACE to
add the ``simple_auth_manager_users`` line. None of that is reversible by
running it again with a flag. It is idempotent in the sense that a second run
finds the database migrated and the user already configured and changes
nothing -- but the first run on a given AIRFLOW_HOME is a real, durable change
to a directory outside this repository.

That is why it is here rather than at module level: before item 20b, LOADING
"22- Airflow Database.py" did all three. Item 20b put the call behind a
``__main__`` guard; this pass makes the code importable so that a reader can
open it -- or a test can call it against a scratch AIRFLOW_HOME -- without the
guard being the only thing in the way.

THE PATH IS AN ARGUMENT NOW, and it defaults to the resolved one
----------------------------------------------------------------
File 22's ``setup_airflow()`` took no arguments and read ``airflow_path`` out of
the shared exec namespace. A package function cannot do that, and resolving the
path at import would break the package rule that importing a module resolves no
directory. So it is ``setup_airflow(airflow_home=None)``, where ``None`` means
``oncotriage.paths.airflow_path``, resolved ON THE CALL.

That is the same shape ``log_inference(db_path=None)`` and
``empty_database(db_path, flag)`` settled on, with one difference worth naming:
this default is allowed to exist because the operation is not destructive to
project DATA. It creates and migrates an Airflow database; it does not delete
inferences. ``empty_database`` gets no default for the opposite reason.

WHAT IMPORTING THIS MODULE DOES
-------------------------------
Imports ``os``, ``subprocess`` and ``pathlib``. It runs no subprocess, resolves
no path, reads no file and writes nothing. The default AIRFLOW_HOME comes from
``oncotriage.orchestration.home.resolve_airflow_home``, which is the ONE place
in the package that reads ``paths.airflow_path``; see that module for why it is
not written out here.
"""

import os
import subprocess
from pathlib import Path

from oncotriage.orchestration.home import resolve_airflow_home


#------------------------------------------------------------------------------


def setup_airflow(airflow_home=None):
    """
    Initialize Airflow 3.1.7 with Simple Auth Manager (default).
    Users configured via airflow.cfg file.
    """
    airflow_path = resolve_airflow_home(airflow_home)

    # Set Airflow home
    os.environ['AIRFLOW_HOME'] = airflow_path

    print(f"Airflow Home: {airflow_path}\n")

    # =========================================================================
    # Step 1: Database migration
    # =========================================================================
    db_path = Path(airflow_path) / 'airflow.db'

    if db_path.exists():
        print(f"✓ Database exists: {db_path}")
    else:
        print(f"Initializing database: {db_path}")

    try:
        result = subprocess.run(
            ['airflow', 'db', 'migrate'],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy()
        )
        if result.stdout:
            print(result.stdout)
        print("✓ Database ready")
    except subprocess.CalledProcessError as e:
        print(f"✗ Database migration failed:")
        print(e.stderr)
        return False

    # =========================================================================
    # Step 2: Verify database
    # =========================================================================
    print("\nVerifying database...")
    try:
        result = subprocess.run(
            ['airflow', 'db', 'check'],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy()
        )
        if result.stdout:
            print(result.stdout)
        print("✓ Database verified")
    except subprocess.CalledProcessError as e:
        print(f"✗ Database check failed:")
        print(e.stderr)
        return False

    # =========================================================================
    # Step 3: Configure admin user in airflow.cfg
    # =========================================================================
    print("\nConfiguring admin user...")

    cfg_path = Path(airflow_path) / 'airflow.cfg'

    if not cfg_path.exists():
        print(f"✗ Config file not found: {cfg_path}")
        print("  Run 'airflow db migrate' first to generate config")
        return False

    # Read existing config
    with open(cfg_path, 'r') as f:
        lines = f.readlines()

    # Check if user already configured
    user_configured = any('simple_auth_manager_users' in line and 'admin' in line for line in lines)

    if user_configured:
        print("✓ Admin user already configured")
    else:
        # Find [core] section and add user configuration
        new_lines = []
        core_section_found = False
        user_line_added = False

        for line in lines:
            new_lines.append(line)

            # Add user config right after [simple_auth_manager] section header
            if line.strip() == '[simple_auth_manager]' and not user_line_added:
                core_section_found = True
                new_lines.append('simple_auth_manager_users = "admin:admin"\n')
                user_line_added = True

        # If [simple_auth_manager] section not found, add it at the end
        if not core_section_found:
            new_lines = new_lines + ['\n[simple_auth_manager]\n', 'simple_auth_manager_users = "admin:admin"\n', '\n']

        # Write updated config
        with open(cfg_path, 'w') as f:
            f.writelines(new_lines)

        print("✓ Admin user configured in airflow.cfg")

    # =========================================================================
    # Step 4: Password file info
    # =========================================================================
    password_file = Path(airflow_path) / 'simple_auth_manager_passwords.json.generated'

    print(f"\n{'='*70}")
    print("✓ AIRFLOW SETUP COMPLETE")
    print(f"{'='*70}")
    print(f"Database: {db_path}")
    print(f"Config: {cfg_path}")
    print(f"\nUser: admin")
    print(f"Password will be auto-generated and saved to:")
    print(f"  {password_file}")
    print(f"  (Also printed in webserver logs on first start)")
# =============================================================================
#     print(f"\nNext steps:")
#     print(f"  1. Start webserver: airflow api-server --port 8080")  # ✅ VERIFIED: Correct syntax
#     print(f"  2. Check webserver logs for admin password")
#     print(f"  3. Start scheduler: airflow scheduler")  # ✅ VERIFIED: Correct command
#     print(f"  4. Access UI: http://localhost:8080")
#     print(f"  5. Login with username 'admin' and auto-generated password")
#
# =============================================================================

    print(f"\nNext step: Run file 23, then file 24 to start services")
    print(f"{'='*70}\n")

    return True


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb 16 16:41:29 2026

@author: ramyalsaffar
"""
