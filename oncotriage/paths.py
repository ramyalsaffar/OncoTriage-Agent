"""Every filesystem location the pipeline reads or writes.

Moved out of ``01- Imports.py`` by item 20c. ``01- Imports.py`` keeps its
third-party import block verbatim — Files 04 to 46 rely on those names being in
the shared exec namespace — and re-exports everything below.

Imports ``oncotriage.settings`` and nothing else from the project. ``settings``
must never import this module in either direction — at module scope OR inside a
function body — because ``paths`` reads it while resolving the root.

``load_env_keys`` LIVES HERE as of pass 20c-2a. Pass 20c-1 put it in
``settings`` and reached ``keys_path`` through an import deferred into the
function body; that worked, but a deferred import is a dependency no static scan
of the import block can see, and the whole point of the package split is an
import graph that can be read. It is here instead, beside the ``keys_path`` it
defaults to, and the deferral is gone. ``config`` imports ``paths`` and
``settings`` and still never imports ``utils``, so the original cycle stays
broken.

IMPORT-TIME SIDE EFFECTS, stated because they are real. Importing this module
resolves the whole directory tree, which means it:

  * reads ONCOTRIAGE_MAIN_PATH (or falls back), and RAISES if the root does not
    exist;
  * runs one ``glob.glob`` per sibling directory, and RAISES if any pattern
    matches nothing;
  * prints which branch and which root it took.

It opens no socket, loads no model, touches no database and reads no file — the
globs stat directories, they do not open them. ``load_env_keys`` is the only
thing here that opens a file, and it is a function, not a module-level
statement.
"""

import glob
import os

from dotenv import load_dotenv

from oncotriage import settings as path_settings


# Detect if running in Docker container
IS_DOCKER = os.path.exists('/.dockerenv') or os.getenv('DOCKER_CONTAINER', 'false').lower() == 'true'


def _load_path_settings():
    """Return (settings module, its directory).

    COMPATIBILITY SHAPE. Before item 20c this function searched three candidate
    directories for ``oncotriage_settings.py`` and loaded it by file location,
    because ``01- Imports.py`` is ``exec()``'d as often as it is run and could
    not rely on ``sys.path``. A package module has no such problem: the import
    above is the whole resolution.

    Kept, and kept returning the same 2-tuple, because ``01- Imports.py``
    defined the name and this pass drops no name that file defined. It has one
    caller today and that caller is the shim.
    """
    return path_settings, os.path.dirname(os.path.abspath(path_settings.__file__))


print(f"[Paths] Settings module loaded from {path_settings.__file__} "
      f"(via the oncotriage package)")


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


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
#
# Moved here from oncotriage/settings.py by pass 20c-2a. It defaults to
# keys_path, resolved a few lines above, so this is the module it belongs in
# and the module where it needs no deferred import.
#
# To create the .env file:
#   ## create .txt file first, and clean it if it has any text due to fresh
#   ## creation!
#   ## add the text you needed!
#   ## rename it to .env
#   ## use a terminal with this (get to the targeted folder first):
#   ## mv .env.txt .env
#   ## to view the .env in Finder on Mac, hit: command + shift + .

REQUIRED_ENV_KEYS = ("OPENAI_API_KEY", "QDRANT_URL", "QDRANT_API_KEY")
"""The three variables a .env must define. Named here rather than repeated in
the two loops below, which is how one of them once came to be cleared but not
validated."""


def load_env_keys(keys_dir=None):
    """Load API keys from the .env file in `keys_dir`.

    Args:
        keys_dir: Directory holding the .env. Defaults to ``keys_path``, the
            module-level value resolved above. Kept as an override so a caller
            that already knows its credentials directory — a container, a test
            fixture — does not have to agree with the glob.

    Returns:
        {'openai': ..., 'qdrant_url': ..., 'qdrant_key': ...}

    Raises:
        FileNotFoundError: no .env at that location.
        ValueError: the file loaded but did not define all three keys.
    """
    if keys_dir is None:
        keys_dir = keys_path

    env_path = path_settings.with_trailing_sep(keys_dir) + '.env'

    # Validate file exists
    if not os.path.exists(env_path):
        raise FileNotFoundError(f".env file not found at: {env_path}")

    # Clear previous env vars to avoid stale values
    for key in REQUIRED_ENV_KEYS:
        os.environ.pop(key, None)

    # Load from file
    load_dotenv(dotenv_path=env_path, override=True)

    # Validate all keys loaded
    keys = {
        'openai': os.getenv('OPENAI_API_KEY'),
        'qdrant_url': os.getenv('QDRANT_URL'),
        'qdrant_key': os.getenv('QDRANT_API_KEY')
    }

    missing = [k for k, v in keys.items() if v is None]
    if missing:
        raise ValueError(f"Missing keys in .env file: {missing}")

    return keys


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug  5 2026

@author: ramyalsaffar
"""
