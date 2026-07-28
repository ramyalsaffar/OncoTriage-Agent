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