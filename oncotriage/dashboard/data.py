"""
Dashboard data loaders.

The three ``@st.cache_data(ttl=60)`` readers of ``inferences.db``, moved
verbatim out of "21- Streamlit Dashboard.py" in pass 20c-3c-1.

THE DATABASE PATH IS READ THROUGH ``paths``, NOT IMPORTED BY NAME. Writing
``from oncotriage.paths import inferences_path`` at module scope would be an
ATTRIBUTE read, which fires the lazy resolver in ``oncotriage/paths.py`` and
globs the whole sibling data tree at IMPORT time -- the exact hole pass 20c-2c
found in ``oncotriage/registries/mesh.py``. Reading ``paths.inferences_path``
inside each function body resolves it on first CALL instead, which is when the
dashboard actually wants the database.

THESE THREE ARE NOT MERGED INTO ``oncotriage/storage/queries.py`` AND THAT IS
DELIBERATE. Consolidating the query layer is its own item; mixing a relocation
with a redesign is what makes an equivalence proof stop meaning anything. They
are moved exactly as they were, SQL included.
"""

import sqlite3

import pandas as pd
import streamlit as st

from oncotriage import paths


@st.cache_data(ttl=60)
def load_inferences_data():
    """
    Load all inference data from SQLite. Cached for 60 seconds.
    
    Returns empty DataFrame on error to allow Streamlit to handle gracefully.
    """
    conn = None
    try:
        conn = sqlite3.connect(paths.inferences_path)
        df = pd.read_sql_query("SELECT * FROM inferences", conn)
        
        if df.empty:
            return pd.DataFrame()  # Return empty DataFrame, not None
            
        # Convert timestamp to datetime
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        return df
        
    except Exception as e:
        st.error(f"Database error: {e}")
        return pd.DataFrame()  # Return empty DataFrame, not None
    
    finally:
        if conn:
            conn.close()


@st.cache_data(ttl=60)
def load_trial_matches_data():
    """Load trial matches from SQLite. Cached for 60 seconds."""
    conn = None
    try:
        conn = sqlite3.connect(paths.inferences_path)
        df = pd.read_sql_query("SELECT * FROM trial_matches", conn)
        
        if df.empty:
            return pd.DataFrame()
            
        return df
        
    except Exception as e:
        st.error(f"Database error: {e}")
        return pd.DataFrame()
    
    finally:
        if conn:
            conn.close()


@st.cache_data(ttl=60)
def load_drift_metrics_data():
    """Load drift metrics from SQLite. Cached for 60 seconds."""
    conn = None
    try:
        conn = sqlite3.connect(paths.inferences_path)
        df = pd.read_sql_query("SELECT * FROM drift_metrics ORDER BY timestamp DESC", conn)
        
        if df.empty:
            return pd.DataFrame()
            
        # Convert timestamp to datetime
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        return df
        
    except Exception as e:
        st.error(f"Drift metrics error: {e}")
        return pd.DataFrame()
    
    finally:
        if conn:
            conn.close()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
