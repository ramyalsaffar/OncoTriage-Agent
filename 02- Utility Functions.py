# Supportive functions
#---------------------
#
# ITEM 20c: THIS FILE IS A SHIM.
#
# Everything below moved into oncotriage/utils.py, except load_env_keys, which
# moved out of the utils/config pair entirely. That split is the point of the
# pass:
#
#     this file  read PRICING_CONFIG, COLLECTION_NAME, qdrant_client and
#                DATA_SNAPSHOT_DATE out of File 03
#     File 03    called load_env_keys() out of this file, at its line 194
#
# Under exec() into one shared namespace both directions resolve at runtime and
# nobody notices. As modules it is a hard import cycle. load_env_keys was the
# ONLY thing config needed from utils and it needs nothing from config, so
# moving it out broke the cycle: oncotriage.config imports oncotriage.paths and
# never imports oncotriage.utils. (Pass 20c-1 put it in oncotriage.settings,
# which needed a deferred import to reach keys_path; pass 20c-2a moved it to
# oncotriage.paths, where keys_path already lives and no deferral is needed.)
#
# This file is still raw-exec'd by all 31 bootstraps in the codebase, straight
# after "01- Imports.py" and before any exec_chain call — exec_chain itself is
# defined here. So every name it used to define is re-exported below, BY NAME.
# No star import: a shim whose surface is "whatever the module happens to
# expose" stops being a contract.
#
# It carries no sys.path bootstrap of its own. It cannot run without
# "01- Imports.py" having run first and never could — it has always used `os`,
# `re`, `time`, `httpx`, `Counter` and `logging` out of File 01's import block
# without importing them — and File 01 is where the package is put on sys.path.


#------------------------------------------------------------------------------


# To create the .env file:
    ## create .txt file first, and clean it if it has any text due to fresh creation!
    ## add the text you needed!
    ## rename it to .env
    ## use a terminal with this (get to the targeted folder first):
    ## mv .env.txt .env
    ## to view the .env in Finder on Mac, hit: command + shift + .

# load_env_keys now lives in oncotriage/paths.py. It is re-exported here
# because '03- Config.py' called it (that call is now get_keys()) and because
# '16- Database Query.py' documents it as one of its five free names and can
# still call it through the chain. Its keys_dir argument defaults to
# oncotriage.paths.keys_path, which is the same directory File 01 binds.
from oncotriage.paths import load_env_keys


#------------------------------------------------------------------------------


from oncotriage.utils import (
    deduplicate_by_display,
    UnknownModelPricingError,
    exec_chain,
    qdrant_retry,
    PARTIAL_DATE_ANCHOR_MONTH,
    PARTIAL_DATE_ANCHOR_DAY,
    PARTIAL_DATE_DEGRADATIONS,
    _PARTIAL_DATE_PATTERNS,
    parse_partial_date,
    CaffeinateSession,
)

# Imported under private aliases and wrapped below. These three read a value
# out of the shared exec namespace at CALL time, and four files depend on that.
from oncotriage.utils import get_model_cost as _get_model_cost_pkg
from oncotriage.utils import resolve_qdrant_collection as _resolve_qdrant_collection_pkg
from oncotriage.utils import get_age_reference_date as _get_age_reference_date_pkg


#------------------------------------------------------------------------------


# THE THREE LATE-BINDING WRAPPERS
#--------------------------------
#
# A module-level function cannot see its caller's globals. These three could,
# because they were defined inside the text exec'd into the shared namespace,
# and that is not a detail — it is a seam the test and fixture harnesses use:
#
#   '45- Fixture Capture.py'            rebinds qdrant_client to a recording proxy
#   '46- Fixture Replay.py'             rebinds it to a replaying proxy
#
# THAT LIST USED TO HAVE THREE MORE ENTRIES AND NOW HAS TWO, which is a fact
# about the consumers rather than about this seam:
#
#   Files 36 and 37 rebound qdrant_client until pass 20c-2c. They install
#   deps.set_override(deps.QDRANT_CLIENT, ...) now; File 37's swap_globals()
#   survives as a tool it no longer uses on this name.
#   File 38 rebound DATA_SNAPSHOT_DATE until pass 20d-1. It sets
#   config.DATA_SNAPSHOT_DATE now -- the attribute the package function reads at
#   call time -- for the four values that must raise.
#
# All three live in tests/ as of pass 20d-1; tests/FILE NUMBER MAPPING.md is
# the mapping. The wrappers stay for Files 45 and 46, which are still in the
# chain and still rebind.
#
# The package functions take the value as an argument instead. These wrappers
# are defined HERE, inside the exec'd text, so their __globals__ IS the shared
# namespace and `globals().get(...)` is still resolved at call time — exactly
# the behaviour the four files above were written against.
#
# Each wrapper keeps the signature its caller uses. `None` means "not supplied"
# for the first two, because neither PRICING_CONFIG nor a client is ever
# legitimately None, so a chain that loaded File 02 without File 03 (File 44
# does exactly that) falls through to the package's own config value rather
# than crashing.

def get_model_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """See oncotriage.utils.get_model_cost. Prices against the shared
    namespace's PRICING_CONFIG when File 03 is in the chain."""
    return _get_model_cost_pkg(model_name, input_tokens, output_tokens,
                               pricing_config=globals().get("PRICING_CONFIG"))


def resolve_qdrant_collection() -> str:
    """See oncotriage.utils.resolve_qdrant_collection. Talks to the shared
    namespace's qdrant_client, which may be a stub or a fixture proxy."""
    return _resolve_qdrant_collection_pkg(
        client=globals().get("qdrant_client"),
        collection_name=globals().get("COLLECTION_NAME"),
    )


def get_age_reference_date():
    """See oncotriage.utils.get_age_reference_date. Reads the shared
    namespace's DATA_SNAPSHOT_DATE, always passing it through explicitly — ""
    is one of the values that must raise, so it cannot double as "unset"."""
    return _get_age_reference_date_pkg(globals().get("DATA_SNAPSHOT_DATE", ""))


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb  9 21:43:44 2026

@author: ramyalsaffar
"""
