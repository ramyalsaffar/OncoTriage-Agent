# Imports
#--------
#
# This file has most of the libraries needed for the project.
# Paths to load from or to.
#
# ITEM 20c: THIS FILE IS NOW HALF SHIM.
#
# The third-party import block below is UNCHANGED and stays here verbatim.
# Files 04 to 46 are exec'd into one shared namespace and reach for `np`, `pd`,
# `Path`, `Dict`, `OpenAI`, `QdrantClient`, `st`, `torch` and eighty more with
# no import statement of their own. Those names have to be bound in the caller's
# globals, and only an exec'd file can do that. Moving them into a package would
# break every one of those files at once, which is not what this pass is for.
#
# The CODING-SYSTEM SENTINELS and the PATH BLOCK did move, into
# oncotriage.constants and oncotriage.paths, and are re-exported below. They
# moved because they are data and resolution logic, not namespace plumbing:
# nothing about them needs a shared namespace, and oncotriage.paths is now
# importable by anything, including code that is not part of the exec chain.
#
###############################################################################


#------------------------------------------------------------------------------


# Libraries
#----------
import numpy as np
import requests
import json
import time
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TypedDict, Annotated, Set, FrozenSet, Any
from datetime import datetime, timezone, date
from openai import OpenAI, RateLimitError, InternalServerError, APIConnectionError

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, SearchRequest, AliasOperations, CreateAliasOperation, DeleteAliasOperation, CreateAlias, DeleteAlias, PayloadSchemaType, SparseVectorParams, Modifier, SparseVector, SparseIndexParams
from qdrant_client.http.exceptions import UnexpectedResponse
import httpx

from fastembed import SparseTextEmbedding

from dotenv import load_dotenv
import os
import glob
import hashlib
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder
import subprocess
import shutil
import random
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from dateutil.relativedelta import relativedelta

from langgraph.graph import StateGraph, START, END

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import sys
import tempfile
from contextlib import asynccontextmanager

import importlib.util

import builtins

import uvicorn
import nest_asyncio
import asyncio

from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch

import sqlite3

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from scipy.stats import ks_2samp

import traceback

from tenacity import (retry, stop_after_attempt, wait_exponential, retry_if_exception_type)

from tqdm import tqdm

import logging

from collections import defaultdict, Counter

import argparse

import caffeine as _caffeine_mod

import xml.etree.ElementTree as ET

import threading
from concurrent.futures import ThreadPoolExecutor


#------------------------------------------------------------------------------


# --- Make the oncotriage package importable ----------------------------------
# This file is EXEC'd, not imported, so sys.path[0] is the ENTRY POINT's
# directory, which is this directory for every documented entry point but is
# not guaranteed to be. `pip install -e .` also makes the package importable
# from anywhere, and that is the supported arrangement; this block is what keeps
# a checkout that has not been installed working exactly as it did before.
#
# Three candidate directories, tried in order, and the one that won is printed —
# a package found somewhere unexpected is exactly the kind of thing that must
# not be silent. These are the same three candidates, for the same reasons, that
# _load_path_settings() used to search for oncotriage_settings.py:
#
#   _code_dir   set by the bootstrap block of whichever file exec'd this one
#   __file__    this file's own directory, when it is run as a script
#   os.getcwd() bare interactive session with neither of the above
#
# Docker takes this path too. The image copies the whole code directory to
# /app, so the package is present there as well.
#
# Every later block in this file, and the shims in Files 02 and 03, depend on
# this having run. Files 02 and 03 carry no copy of it because they are never
# loaded without this file first — all 31 bootstraps in the codebase exec
# "01- Imports.py" before "02- Utility Functions.py", and they have to: File 02
# has always used `os`, `re`, `time`, `httpx`, `Counter` and `logging` out of
# the block above without importing them.

def _ensure_oncotriage_importable():
    """Import the oncotriage package, extending sys.path only if it is absent.

    Returns the (path, how) that made it work, or (None, "already importable").
    """
    try:
        import oncotriage  # noqa: F401
        return None, "already importable"
    except ImportError:
        pass

    candidates = []
    if isinstance(globals().get("_code_dir"), str):
        candidates.append((globals()["_code_dir"], "_code_dir from the calling script"))
    if "__file__" in globals():
        candidates.append((os.path.dirname(os.path.abspath(__file__)), "__file__ of 01- Imports.py"))
    candidates.append((os.getcwd(), "working directory"))

    for directory, how in candidates:
        if not os.path.isfile(os.path.join(directory, "oncotriage", "__init__.py")):
            continue
        if directory not in sys.path:
            sys.path.insert(0, directory)
        import oncotriage  # noqa: F401
        print(f"[Bootstrap] oncotriage package found at {directory} (via {how}); added to sys.path")
        return directory, how

    raise RuntimeError(
        "The 'oncotriage' package was not importable and was not found. Searched:\n  "
        + "\n  ".join(f"{d} (via {how})" for d, how in candidates)
        + "\nIt must sit beside 01- Imports.py in the code directory, or be "
          "installed with `pip install -e .` from that directory."
    )


_ensure_oncotriage_importable()


#------------------------------------------------------------------------------


# Coding system keys
#-------------------
# Moved to oncotriage/constants.py by item 20c; the argument for why the two
# values live in one shared place, and why collapsing them is a defect, is there
# in full. Re-exported here because '07- FHIR Parser.py',
# '08- Cancer Code Registry.py' and '13- LangGraph Agent.py' read them out of
# the shared namespace.
from oncotriage.constants import SYSTEM_KEY_ABSENT, SYSTEM_KEY_UNRECOGNIZED


# Paths
#------
# Moved to oncotriage/paths.py by item 20c. Importing that module is what
# resolves the tree — it prints the branch it took and the project root, exactly
# as this file used to — and every name below is the SAME object the package
# holds, not a copy.
#
# `path_settings` is oncotriage.settings, which is also what
# `oncotriage_settings.py` re-exports. '23- Airflow DAG.py' reads
# path_settings.ENV_CODE_PATH / .with_trailing_sep() off this name.
#
# Explicitly, by name. A star import would bind glob, os and every private
# helper in oncotriage.paths into the shared namespace, where the next file to
# be added would inherit them without asking.
from oncotriage.paths import (
    IS_DOCKER,
    _load_path_settings,
    path_settings,
    _glob_one,
    _main_path_source,
    main_path,
    code_path,
    data_path,
    data_patient_path,
    data_fhir_path,
    data_trial_path,
    data_MeSH_path,
    inferences_path,
    results_path,
    result_fhir_explore_path,
    result_ablation_path,
    keys_path,
    airflow_path,
    requirements_path,
    checkpoint_path,
)


#------------------------------------------------------------------------------


# Python display settings
#------------------------
pd.set_option('display.max_rows', 500)
pd.set_option("display.max_columns", 500)
pd.set_option("display.max_colwidth", 250)
pd.set_option('display.width', 1000)
pd.set_option('display.precision', 5)  # this will help me see big numbers without python converting it to exponential
pd.options.display.float_format = '{:.4f}'.format


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb  9 19:41:46 2026

@author: ramyalsaffar
"""
