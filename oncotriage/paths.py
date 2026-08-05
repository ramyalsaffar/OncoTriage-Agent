"""Every filesystem location the pipeline reads or writes.

Moved out of ``01- Imports.py`` by item 20c. ``01- Imports.py`` keeps its
third-party import block verbatim — Files 04 to 46 rely on those names being in
the shared exec namespace — and re-exports everything below.

Imports ``oncotriage.settings`` and nothing else from the project. Nothing in
this package may import this module at module scope from ``settings``: that
would be a cycle. (``settings.load_env_keys`` imports ``keys_path`` from here
INSIDE its body, at call time, which is not a cycle and is argued in that
module's docstring.)

IMPORT-TIME SIDE EFFECTS, stated because they are real. Importing this module
resolves the whole directory tree, which means it:

  * reads ONCOTRIAGE_MAIN_PATH (or falls back), and RAISES if the root does not
    exist;
  * runs one ``glob.glob`` per sibling directory, and RAISES if any pattern
    matches nothing;
  * prints which branch and which root it took.

It opens no socket, loads no model and touches no database. That is the
property the package guarantees, and ``config`` and ``utils`` do not even touch
the filesystem — which is why a caller that only wants a tunable never pays for
any of this.
"""

import glob
import os

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


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug  5 2026

@author: ramyalsaffar
"""
