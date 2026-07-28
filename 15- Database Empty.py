# Empty the SQLite Database
###########################


# Connect
conn = sqlite3.connect(inferences_path)


# Create cursor
cursor = conn.cursor()


# Empty the SQLite database
# The default is False, change to True to empty the SQLite database
Flag = False

if Flag:
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'")
    tables = cursor.fetchall()
    for (table_name,) in tables:
        cursor.execute(f"DELETE FROM {table_name}")
    cursor.execute("DELETE FROM sqlite_sequence")

    conn.commit()

# Close connection
conn.close()
print("Database cleared. Tables preserved.")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 12 22:02:04 2026

@author: ramyalsaffar
"""
