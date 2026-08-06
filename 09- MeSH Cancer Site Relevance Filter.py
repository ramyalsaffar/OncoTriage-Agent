# MeSH Cancer Site Relevance Filter
###################################

"""
Build the MeSH C04 lookup files and the three UMLS crosswalks the cancer-site
relevance filter reads at run time.

THIN ENTRY POINT (item 20c pass 2a moved the logic; pass 20e removed the shim)
------------------------------------------------------------------------------
File 09 split two ways on the way into the package and both halves stay there:

  oncotriage/registries/mesh.py                 the runtime filter --
                                                MeSHCancerFilter,
                                                load_mesh_filter,
                                                specific_cancer_trees,
                                                PAN_CANCER_TREE_MAX_DEPTH
  oncotriage/registries/mesh_crosswalk_build.py the five OFFLINE builders --
                                                build_mesh_lookup and the three
                                                crosswalks, plus build_all_lookups

``mesh`` does not import ``mesh_crosswalk_build``, and that separation is the
point: the builders parse desc2026.xml and the 1.5 GB UMLS MRCONSO release, they
run once by hand, and nothing in the pipeline calls them. Keeping them beside
the filter meant every process that wanted the filter also carried code that
opens MRCONSO line by line.

WHY THE NINE-NAME SHIM WENT. It existed because Files 05, 11, 13, 30 and 31
exec-chained this file (File 32 reached it transitively through 13). Pass 20e
measured all six: 11 became a thin entry point in pass 20c-3a, 30, 31 and 32
became modules under ``tests/`` in pass 20d-1 and import the package directly,
and 05 and 13 became thin entry points in this pass. Nothing chains this file
any more, so the re-export block was nine names with no reader -- the dead
declaration ``tests/test_package_invariants.py`` check 2h exists to catch.

WHAT IS LEFT is the ``__main__`` block, which is what
``python "09- MeSH Cancer Site Relevance Filter.py"`` runs and the only call
site of the five builders anywhere in the repository. Its imports are inside the
guard, so loading this file resolves nothing.

Run from terminal:
    python "09- MeSH Cancer Site Relevance Filter.py"
"""

import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# This is a documented entry point, so when it IS the entry point sys.path[0] is
# already the code directory and `pip install -e .` makes the block a no-op.
# It is what keeps any other invocation working, and it prints the directory it
# added -- a package resolved from an unexpected place must not be silent.
try:
    import oncotriage  # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals()
         else None, "__file__"),
        (os.getcwd(), "cwd"),
    ):
        if _candidate and os.path.isdir(os.path.join(_candidate, "oncotriage")):
            if _candidate not in sys.path:
                sys.path.insert(0, _candidate)
            print(f"[Bootstrap] oncotriage package found at {_candidate} "
                  f"(via {_how}); added to sys.path")
            break
    else:
        raise
    del _candidate, _how


# The offline builders, and the loader the validation step below calls.
# Imported at module scope rather than in the guard because neither module
# resolves a path or reads a file at import -- only data_MeSH_path does, and
# that is imported inside the guard.
from oncotriage.registries.mesh_crosswalk_build import (  # noqa: E402
    build_icd10_to_mesh_crosswalk,
    build_mesh_lookup,
    build_snomed_to_mesh_crosswalk,
    build_umls_synonym_crosswalk,
)

from oncotriage.registries.mesh import load_mesh_filter  # noqa: E402


# ===========================================================================
# MAIN: Build lookup files from raw data
# ===========================================================================


if __name__ == "__main__":

    # PASS 20e: THIS BLOCK USED TO RAW-EXEC 01 AND 02 AND CHAIN 03, for three
    # names -- Path, sys and data_MeSH_path. Not one of them needed a shared
    # namespace, and the chain cost a full OpenAI client, a Qdrant client, torch,
    # transformers, streamlit and langgraph to rebuild two JSON lookup files.
    # The three free names were re-derived with symtable before the change, not
    # taken from the comment that used to sit here.
    #
    # data_MeSH_path is imported INSIDE the guard because it is a lazy attribute
    # on oncotriage.paths and reading it resolves the sibling data tree; at
    # module scope this file would glob that tree merely by being loaded, which
    # is the hole pass 20c-2c found in oncotriage/registries/mesh.py.
    from pathlib import Path

    from oncotriage.paths import data_MeSH_path

    # --- Locate raw data files ---
    mesh_xml = str(Path(data_MeSH_path) / "desc2026.xml")
    mrconso  = str(Path(data_MeSH_path) / "MRCONSO_2025AB.RRF")

    if not Path(mesh_xml).exists():
        print(f"ERROR: MeSH XML not found at {mesh_xml}")
        print("Download from: https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/desc2026.gz")
        sys.exit(1)

    # --- Step 1: Always build MeSH hierarchy (only needs desc2026.xml) ---
    mesh_data = build_mesh_lookup(mesh_xml, output_dir=data_MeSH_path)

    # --- Step 2: Build crosswalks only if MRCONSO available ---
    if Path(mrconso).exists():
        
        build_snomed_to_mesh_crosswalk(
            mrconso, data_MeSH_path,
            mesh_uid_to_trees=mesh_data["uid_to_trees"]
        )
        
        build_icd10_to_mesh_crosswalk(
            mrconso, data_MeSH_path,
            mesh_uid_to_trees=mesh_data["uid_to_trees"]
        )
        
        build_umls_synonym_crosswalk(
            mrconso, data_MeSH_path,
            mesh_uid_to_trees=mesh_data["uid_to_trees"],
            name_to_trees=mesh_data["name_to_trees"],
        )
        
    else:
        print(f"\nNOTE: MRCONSO_2025AB.RRF not found at {mrconso}")
        print("SNOMED and ICD-10 crosswalks will not be built.")
        print("Filter will use fuzzy string matching only.")
        print("To enable crosswalks: download MRCONSO_2025AB.RRF from UMLS and re-run.\n")

    # --- Quick validation ---
    #
    # ITEM 11a CHANGED WHAT A FAILED BUILD LOOKS LIKE HERE, for the better. This
    # call used to return None when build_mesh_lookup() had not produced the two
    # core files, and the `if` below then skipped validation in silence — the
    # one command whose entire job is to build those files reported nothing when
    # it had not. It RAISES now, naming the file that is missing.
    #
    # The `else` is still reachable, and only one way: a run with
    # ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES set. It says so rather than falling
    # off the end of the block.
    mesh_filter = load_mesh_filter()
    if mesh_filter:
        print("\n--- Quick Validation ---")

        # Test: "Breast Neoplasms" should have C04.588.274
        test_name = "breast neoplasms"
        trees = mesh_filter.name_to_trees.get(test_name, [])
        print(f"  '{test_name}' → trees: {trees}")

        # Test: "Colonic Neoplasms" should have C04.588.274.476
        test_name2 = "colonic neoplasms"
        trees2 = mesh_filter.name_to_trees.get(test_name2, [])
        print(f"  '{test_name2}' → trees: {trees2}")

        # Test ancestry: breast and colonic should NOT be related
        if trees and trees2:
            related = any(
                t1.startswith(t2) or t2.startswith(t1)
                for t1 in trees for t2 in trees2
            )
            print(f"  Breast ↔ Colonic related? {related} (expected: False)")

        print("\n✓ MeSH Cancer Filter ready for use.")

    else:
        print("\n✗ VALIDATION SKIPPED — load_mesh_filter() returned None, which "
              "at this point means ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES is set "
              "and the two core lookup files were NOT built. The cancer site "
              "filter will be disabled for every run against this data "
              "directory; unset that variable to see which file is missing.")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 20 2026

@author: ramyalsaffar
"""
