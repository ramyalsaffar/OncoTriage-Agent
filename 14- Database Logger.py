# Database Schema and Logger
############################
#
# ITEM 20c, PASS 2b: THIS FILE IS A SHIM.
#
# Every definition moved to oncotriage/storage/database_logger.py. This file
# survives because Files 17, 25, 26, 32, 36, 37, 38, 40 and 45 exec-chain it and
# read these names out of the shared namespace with no import statement of their
# own.
#
# The name list below is the RUNTIME surface of File 14 as it stood before this
# pass: the file was exec'd into a throwaway namespace with its free names
# pre-seeded, and every resulting binding recorded. Seven names, all of them
# still here.
#
# TWO THINGS CHANGED IN THE MODULE, and they are argued in full in its
# docstring. In summary:
#
#   log_inference now takes db_path       so that the five files which redirect
#                                         logging away from the production
#                                         database can say so explicitly rather
#                                         than by rebinding a global a module
#                                         function cannot see;
#   _resolve_primary_cancer calls         instead of reading File 13's
#   load_registry()                       _CANCER_REGISTRY out of the shared
#                                         namespace, which was a layering
#                                         violation AND unbound in any chain
#                                         that loaded 14 without 13.
#
# AND ONE IN PASS 2c: _resolve_primary_cancer is no longer DEFINED in the
# storage module at all. It moved to oncotriage/registries/primary_cancer.py,
# because it is a domain question about SNOMED and ICD-10 codes that opens no
# database, and because File 13's terminal nodes call it too -- which made the
# agent depend on the storage layer for a registry lookup. Both import it from
# the registries package now. This shim still re-exports it, from the storage
# module, which re-exports it in turn: the name reaches the shared exec
# namespace exactly as it always did.
#
# Item 20b's property is unchanged and still the important one: loading this
# file opens no database. It used to run every CREATE TABLE and every additive
# migration against the production inferences.db as a side effect of the exec
# chain, and nine other files load it or are loaded beside it.


#------------------------------------------------------------------------------


from oncotriage.storage.database_logger import (
    INFERENCE_COLUMN_ADDITIONS,
    TRIAL_MATCH_COLUMN_ADDITIONS,
    _INITIALIZED_DATABASES,
    _ensure_database,
    _resolve_primary_cancer,
    initialize_database,
    resolve_inference_db_path,
)

from oncotriage.storage.database_logger import (
    log_inference as _package_log_inference,
)


#------------------------------------------------------------------------------


# THE LATE-BINDING WRAPPER
#-------------------------
#
# The SAME seam File 02 uses for get_model_cost, resolve_qdrant_collection and
# get_age_reference_date, and it exists here for the same reason: the package
# function takes as an argument a value the exec chain has always supplied as a
# shared global.
#
# WHY IT IS LOAD-BEARING. Five files rebind inferences_path at a temporary
# database and only then load this file, so that their writes cannot reach the
# production inferences.db:
#
#     36- Logging Contract Test.py            line 129, exec at 131
#     37- Retrieval Observability Test.py     line 174, exec at 176
#     38- Birth Date and Demographics ...     line 187, exec at 189
#     40- ECOG Logging Test.py                line 153, exec at 155
#     45- Fixture Capture.py                  line 383, chained at 386
#
# A module-level function cannot see a caller's globals, so the moment
# log_inference started importing inferences_path from oncotriage.paths, all
# five would have written real rows into the real database while still printing
# the name of the temporary file each thought it was using. Silent in both
# directions, which is why this wrapper exists AND why all five now pass
# db_path explicitly as well. Either mechanism alone is sufficient; both
# together mean neither is the single point of failure.
#
# This function is DEFINED INSIDE THE EXEC'D TEXT, so its __globals__ IS the
# shared namespace and globals().get() below is a live read, not a snapshot
# taken at load. That is the whole trick.
#
# globals().get(), not globals()[...]: File 14 can be exec'd into a namespace
# that never loaded File 01 -- a bare probe, a future caller -- and there the
# right answer is "nothing was supplied", which resolve_inference_db_path turns
# into the configured production path. A KeyError would be a worse answer than
# the default the package already has.
def log_inference(result, patient_data, db_path=None):
    """Log an inference, defaulting db_path to the shared namespace's value.

    See oncotriage/storage/database_logger.py for the real docstring. Returns
    the database path actually used, so a caller can assert where it wrote.
    """
    if db_path is None:
        db_path = globals().get("inferences_path")
    return _package_log_inference(result, patient_data, db_path=db_path)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 12 13:26:56 2026
@author: ramyalsaffar
"""
