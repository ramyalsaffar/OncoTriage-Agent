# Empty the SQLite Database
###########################


# ===========================================================================
# EXEC CHAIN: 01
# ===========================================================================
# This file reads two names it does not define -- inferences_path and
# sqlite3 -- and both come from 01- Imports.py. Nothing here calls
# exec_chain or any utility, so 02 is not loaded; nothing reads a config
# constant, so 03 is not loaded.
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


# Empty the SQLite database
# The default is False, change to True to empty the SQLite database
Flag = False


# Item 20b: opening the connection at module level meant that merely loading
# this file -- the one file in the project whose purpose is to destroy data --
# opened the production database. Every statement that touches sqlite is now
# inside the function below, and the function only runs under the __main__
# guard. The switch stays at module level: it is data, it is the thing a reader
# comes to this file to find, and leaving it here keeps the one-line edit that
# arms this script exactly where it has always been.
def empty_database(db_path, flag):
    """Delete every row from every table at db_path, preserving the tables.

    Does nothing unless flag is True. That default is not a safety belt to be
    tidied away: this is the only destructive script in the project.
    """
    # Connect
    conn = sqlite3.connect(db_path)

    # Create cursor
    cursor = conn.cursor()

    if flag:

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'")
        tables = cursor.fetchall()
        for (table_name,) in tables:
            cursor.execute(f"DELETE FROM {table_name}")
        cursor.execute("DELETE FROM sqlite_sequence")

        conn.commit()

    # Close connection
    conn.close()

    # The unconditional "Database cleared" print this replaced ran even when
    # Flag was False, so the one run that mattered and the many that did
    # nothing were indistinguishable in a terminal scrollback.
    if flag:
        print(f"Database cleared, tables preserved: {db_path}")
    else:
        print(f"Flag is False -- nothing was deleted. Database untouched: {db_path}")


if __name__ == "__main__":
    empty_database(inferences_path, Flag)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 12 22:02:04 2026

@author: ramyalsaffar
"""
