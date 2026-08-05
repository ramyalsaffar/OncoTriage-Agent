# Imports
#--------
#
# This file has most of the libraries needed for the project.
# Paths to load from or to.
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


# Coding system keys
#-------------------
#
# The two system_key values that are NOT a named code system. They live here,
# not in the file that produces them, because producer and consumers sit in
# different exec_chain chains and cannot see each other's module constants:
# '07- FHIR Parser.py' writes them, '08- Cancer Code Registry.py' branches on
# them, and '33- Cancer Code and Stage Extraction Test.py' chains
# 01 -> 02 -> 08 -> 10 with no File 07 in it at all. File 01 is the only file
# every bootstrap loads first, so it is the only place all three can share one
# spelling. Everything else about coding systems -- the URI table
# (_SYSTEM_URI_TO_KEY) and the per-resource preference order -- stays in
# File 07, because those are facts about FHIR parsing rather than a vocabulary
# other files must agree with.
#
# The distinction between the two is load-bearing:
#
#   SYSTEM_KEY_ABSENT ("unknown")
#       Coding.system is absent or empty. FHIR permits this, and this codebase
#       MANUFACTURES it: File 08's no-codings fallback and File 13 both build
#       {"system_key": "unknown", "code": ...} from a bare code with no system.
#       Nothing is asserted about which vocabulary the code came from, so
#       lookups treat it PERMISSIVELY and try every set.
#
#   SYSTEM_KEY_UNRECOGNIZED ("unmapped")
#       Coding.system is present but is not a URI File 07 knows: a proprietary
#       or local system, a registry URI, MEDCIN, an EHR's internal dictionary.
#       That is a POSITIVE STATEMENT that the code belongs to some other
#       vocabulary, so looking it up in SNOMED or ICD-10 compares digits, not
#       concepts, and must not happen.
#
# These were one value until they were split. Collapsing them is how MEDCIN
# 315006 came to sit in File 08's SNOMED secondary set, labelled "Secondary
# malignant neoplasm of bone", matching on its digits alone.
#
# "unknown" keeps its spelling because consumers predating the split compare
# against that literal.
SYSTEM_KEY_ABSENT = "unknown"
SYSTEM_KEY_UNRECOGNIZED = "unmapped"


# Paths
#------

# Detect if running in Docker container
IS_DOCKER = os.path.exists('/.dockerenv') or os.getenv('DOCKER_CONTAINER', 'false').lower() == 'true'


# --- Locate oncotriage_settings.py -------------------------------------------
# This file is exec()'d into a caller's globals as often as it is run directly,
# so it cannot rely on a plain `import oncotriage_settings`: sys.path[0] is the
# *entry point's* directory, which is this directory today but will not be once
# passes 20b-20f move things. Three candidate directories, tried in order, and
# the one that won is printed — a settings module found somewhere unexpected is
# exactly the kind of thing that must not be silent.
#
#   _code_dir   set by the bootstrap block of whichever file exec'd this one
#   __file__    this file's own directory, when it is run as a script
#   os.getcwd() bare interactive session with neither of the above
#
# Docker takes this path too. The image copies the whole code directory to
# /app, so the module is present there as well.

def _load_path_settings():
    """Import oncotriage_settings.py by location. Returns (module, directory)."""
    candidates = []
    if isinstance(globals().get("_code_dir"), str):
        candidates.append((globals()["_code_dir"], "_code_dir from the calling script"))
    if "__file__" in globals():
        candidates.append((os.path.dirname(os.path.abspath(__file__)), "__file__ of 01- Imports.py"))
    candidates.append((os.getcwd(), "working directory"))

    for directory, how in candidates:
        candidate = os.path.join(directory, "oncotriage_settings.py")
        if not os.path.isfile(candidate):
            continue
        spec = importlib.util.spec_from_file_location("oncotriage_settings", candidate)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        print(f"[Paths] Settings module loaded from {candidate} (via {how})")
        return module, directory

    raise RuntimeError(
        "oncotriage_settings.py was not found. Searched:\n  "
        + "\n  ".join(f"{d} (via {how})" for d, how in candidates)
        + "\nIt must sit beside 01- Imports.py in the code directory."
    )


path_settings = _load_path_settings()[0]


# glob.glob(pattern)[0] on its own raises IndexError, and an IndexError names
# neither the pattern that matched nothing nor the root it was anchored to.
# Every sibling directory in the local branch is discovered by prefix glob, so
# a wrong root produces one IndexError per run and no diagnosis. Same
# discovery, same unsorted [0] — only the failure message changes.
#
# Defined outside the branch, not inside the local one, so that both branches
# leave the same set of names behind. The Docker branch does not call it; a
# name defined in one branch and not the other is the exact defect item 20a
# found in code_path.
def _glob_one(pattern, label):
    hits = glob.glob(pattern)
    if not hits:
        raise RuntimeError(
            f"No directory matched the {label} pattern: {pattern!r}\n"
            f"Project root in use: {main_path!r} (from {_main_path_source})\n"
            f"Set {path_settings.ENV_MAIN_PATH} if the root is wrong, or check "
            f"that the sibling directory exists and still ends in the expected suffix."
        )
    return hits[0]


if IS_DOCKER:
    # Docker container paths (Linux environment)
    print("🐳 Running in Docker container")

    # Provenance of main_path, recorded in both branches so a reader of a log
    # can tell a container run from a local one without inferring it.
    _main_path_source = "Docker image layout (fixed)"

    main_path = "/app/"
    # The Dockerfile does `COPY . /app/`, so the numbered scripts sit directly
    # in /app. Added in item 20a: the local branch has always defined
    # code_path and this branch never did, so any file reaching for it was
    # container-only broken.
    code_path = "/app/"
    data_path = "/app/data/"
    data_patient_path = "/app/data/patients/"
    data_fhir_path = "/app/data/patients/fhir/"
    data_trial_path = "/app/data/trials/"
    inferences_path = "/app/data/inferences.db"
    results_path = "/app/results/"
    result_fhir_explore_path = "/app/results/fhir_exploration/"
    result_ablation_path = "/app/results/ablation/"
    keys_path = "/app/"
    airflow_path = "/app/airflow_home/"
    requirements_path = "/app/requirements/"
    data_MeSH_path = "/app/data/mesh/"
    checkpoint_path = "/app/checkpoint/"
    
else:
    # Local development paths (macOS)
    print("💻 Running on local machine")

    main_path, _main_path_source = path_settings.resolve_main_path()
    print(f"[Paths] Project root: {main_path} (from {_main_path_source})")

    code_path = _glob_one(main_path + "/*Code/", "code")

    data_path = _glob_one(main_path + "/*Data/", "data")

    data_patient_path = _glob_one(data_path + "/*Patients/", "patients")

    data_fhir_path = _glob_one(data_patient_path, "FHIR bundle") + "fhir/"

    data_trial_path = _glob_one(data_path + "/*Trials/", "trials")

    data_MeSH_path = _glob_one(data_path + "/*MeSH/", "MeSH")

    inferences_path = _glob_one(data_path + "/*Inferences Storage/", "inferences") + "inferences.db"

    results_path = _glob_one(main_path + "/*Results/", "results")

    result_fhir_explore_path = _glob_one(results_path + "/*FHIR Exploration/", "FHIR exploration results")

    result_ablation_path = _glob_one(results_path + "/*Ablation/", "ablation results")

    keys_path = _glob_one(main_path + "/*Keys/", "keys")

    airflow_path = _glob_one(main_path + "/*Airflow/", "Airflow")

    requirements_path = _glob_one(main_path + "/*Requirements/", "requirements")

    checkpoint_path = _glob_one(main_path + "/*Checkpoint/", "checkpoint")


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