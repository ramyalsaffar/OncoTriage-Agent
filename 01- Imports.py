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

if IS_DOCKER:
    # Docker container paths (Linux environment)
    print("🐳 Running in Docker container")
    
    main_path = "/app/"
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
    
    main_path = "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/"
    
    code_path = glob.glob(main_path + "/*Code/")[0]
    
    data_path = glob.glob(main_path + "/*Data/")[0]
    
    data_patient_path = glob.glob(data_path + "/*Patients/")[0]
    
    data_fhir_path = glob.glob(data_patient_path)[0] + "fhir/"
    
    data_trial_path = glob.glob(data_path + "/*Trials/")[0]
    
    data_MeSH_path = glob.glob(data_path + "/*MeSH/")[0]
    
    inferences_path = glob.glob(data_path + "/*Inferences Storage/")[0] + "inferences.db"
    
    results_path = glob.glob(main_path + "/*Results/")[0]
    
    result_fhir_explore_path = glob.glob(results_path + "/*FHIR Exploration/")[0]
    
    result_ablation_path = glob.glob(results_path + "/*Ablation/")[0]
    
    keys_path = glob.glob(main_path + "/*Keys/")[0]
    
    airflow_path = glob.glob(main_path + "/*Airflow/")[0]
    
    requirements_path = glob.glob(main_path + "/*Requirements/")[0]
    
    checkpoint_path = glob.glob(main_path + "/*Checkpoint/")[0]
    

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