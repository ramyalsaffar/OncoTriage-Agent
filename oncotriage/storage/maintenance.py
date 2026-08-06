# Database Maintenance
######################

"""Destructive maintenance on the inference database.

Moved out of ``15- Database Wipe All Tables.py`` by item 20c, pass 3b. That file is now a
thin entry point: the ``Flag`` switch, the ``__main__`` guard, and the one call.

THIS IS THE ONLY DESTRUCTIVE SCRIPT IN THE PROJECT, so read what did NOT change
before reading what did.

WHAT DID NOT CHANGE, and none of it is negotiable
-------------------------------------------------
``empty_database(db_path, flag)`` still takes BOTH arguments and defaults
NEITHER. It was already the exception among the storage functions -- item 20b
gave it an explicit ``db_path`` before ``log_inference`` got one -- and the
temptation on a move like this is to "tidy" it into
``empty_database(db_path=None, flag=False)`` for consistency with its
neighbours. That would be a defect:

  * ``db_path=None`` meaning "the production database" turns
    ``empty_database()`` -- a plausible thing to type at a prompt while
    exploring a module -- into a command that wipes the real inferences.db.
    Every other function in this package can afford that default because the
    worst case is a read; this one cannot.
  * ``flag=False`` as a default would make ``empty_database(path)`` a no-op that
    LOOKS like it did something, which is a different bad answer to the same
    question.

So both stay required. The function is deliberately awkward to call, and that is
its safety property.

``flag=False`` STILL DELETES NOTHING AND STILL SAYS SO. The connection is opened
either way -- that is unchanged from File 15 -- but no DELETE is issued and the
closing message names which of the two things happened. The unconditional
"Database cleared" print that preceded item 20b made the one run that mattered
indistinguishable, in a terminal scrollback, from the many that did nothing.

The switch itself, ``Flag = False``, DID NOT MOVE. It stays at module level in
"15- Database Wipe All Tables.py". It is data, it is the thing a reader opens that file to
find, and leaving it there keeps the one-line edit that arms this script exactly
where it has always been -- rather than burying it in a package module where
someone editing it would be further from the warning.

WHAT DID CHANGE
---------------
Pass 20c-3b: the SQL is now reachable without exec-ing a numbered file. Nothing
in the repository read File 15's namespace -- every top-level name it bound
(``Flag``, ``empty_database``) was grepped against every .py, .md, .toml and
.yml in the tree and the only hit is prose in CLAUDE.md -- so there is no
re-export shim and File 15 keeps no exec bootstrap.

PASS 20f-1: THE WIPE NO LONGER RAISES ON A DATABASE WITH NO AUTOINCREMENT
TABLE. ``DELETE FROM sqlite_sequence`` was issued unconditionally, and SQLite
materialises that table only once something has been declared AUTOINCREMENT --
so wiping an empty database, or one whose keys are plain rowid aliases, raised
``no such table: sqlite_sequence`` BEFORE the commit and therefore deleted
nothing while reporting an error about a table the caller never named. The
presence of the table is now read out of ``sqlite_master``, which is the same
catalogue the table loop already reads. See the comment at the statement for
why this is a lookup and not a ``try``/``except``.
``tests/test_storage_wipe_all_tables.py`` is the demonstration, including that
an unrelated ``OperationalError`` still propagates.

WHAT IMPORTING THIS MODULE DOES
-------------------------------
Nothing. No connection, no path resolution, no default anywhere that could
resolve to the production database. ``sqlite3`` is imported and not called.
"""

import sqlite3


#------------------------------------------------------------------------------


# Item 20b: opening the connection at module level meant that merely loading
# this code -- the one place in the project whose purpose is to destroy data --
# opened the production database. Every statement that touches sqlite is inside
# the function below, and in "15- Database Wipe All Tables.py" the function only runs
# under the __main__ guard.
def empty_database(db_path, flag):
    """Delete every row from every table at db_path, preserving the tables.

    Does nothing unless flag is True. That default is not a safety belt to be
    tidied away: this is the only destructive script in the project.

    Args:
        db_path: The database to empty. REQUIRED, with no default -- see the
            module docstring for why ``None`` must not mean "production".
        flag:    REQUIRED. Only True deletes anything.

    Returns:
        None. Prints which of the two things happened.
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

        # ASKED FOR RATHER THAN ASSUMED (pass 20f-1). This was an
        # unconditional `DELETE FROM sqlite_sequence`, and SQLite creates that
        # table only when something in the database has been declared
        # AUTOINCREMENT. A database without one -- an empty file, or a schema
        # using the plain INTEGER PRIMARY KEY rowid alias -- answered with
        # `sqlite3.OperationalError: no such table: sqlite_sequence`.
        #
        # THE FAILURE WAS TOTAL, NOT PARTIAL: the raise landed BEFORE the
        # commit below, so a wipe that hit it deleted nothing at all and the
        # caller got an error naming a table it had never mentioned. And
        # sqlite3.connect CREATES a file that does not exist, so a mistyped
        # path produced an empty database and then exactly that error. Pass 20b
        # reported this and did not fix it.
        #
        # The question is asked of sqlite_master, the same catalogue the loop
        # above already reads, rather than of a try/except: a bare
        # `except sqlite3.OperationalError: pass` would make this case pass and
        # would also swallow every OTHER OperationalError the statement can
        # meet -- a read-only file, a locked database -- which is precisely the
        # silent recovery this project exists to remove.
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name = 'sqlite_sequence'"
        )
        if cursor.fetchone() is not None:
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


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 12 22:02:04 2026

@author: ramyalsaffar
"""
