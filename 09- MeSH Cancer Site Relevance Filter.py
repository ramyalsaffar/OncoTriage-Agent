# MeSH Cancer Site Relevance Filter
###################################
#
# ITEM 20c, PASS 2a: THIS FILE IS A SHIM, plus its __main__ block.
#
# The file split in two on the way into the package:
#
#   oncotriage/registries/mesh.py                 PAN_CANCER_TREE_MAX_DEPTH
#                                                 onward — the runtime filter
#   oncotriage/registries/mesh_crosswalk_build.py lines 57-696 — the five
#                                                 offline builders
#
# Logic byte-for-byte unchanged in both. The split exists because the builders
# parse desc2026.xml and the 1.5 GB UMLS MRCONSO release, they run once by hand,
# and they are called from NOWHERE in the pipeline — verified by grep across
# every file in the repository, where File 09's own __main__ block below is the
# only call site. Keeping them beside the filter meant every process that wanted
# the filter also carried code that opens MRCONSO line by line.
#
# `mesh` does not import `mesh_crosswalk_build`. This shim imports both, because
# its contract is File 09's whole pre-pass surface and because the __main__
# block calls the builders and the loader in one run.
#
# Files 05, 11, 13, 30 and 31 exec-chain this file; File 32 reaches it
# transitively through File 13.
#
# Explicit, by name, never a star import.
#
# The __main__ block below is unchanged. It bootstraps 01 -> 02 -> 03 itself,
# which is where its Path, sys and data_MeSH_path come from; the imports above
# supply only the builders and the loader it calls.


#------------------------------------------------------------------------------


# --- Make the oncotriage package importable ----------------------------------
# Files 08 and 10 need no block like this: they are only ever reached through
# exec_chain, which always runs after "01- Imports.py", and File 01 is what puts
# the code directory on sys.path. THIS file is different — it is a documented
# entry point (`python "09- MeSH Cancer Site Relevance Filter.py"` rebuilds the
# MeSH lookups), and when it is the entry point its own bootstrap has not run
# yet at the moment these imports execute.
#
# sys.path[0] is the entry point's directory, so running it from the code
# directory already works, as does `pip install -e .`. This block is what keeps
# any other invocation working, and it prints the directory it added — a package
# resolved from an unexpected place must not be silent.
try:  # pragma: no cover - the happy path is the normal one
    import oncotriage as _oncotriage_pkg  # noqa: F401
except ImportError:
    import os as _os_pkg
    import sys as _sys_pkg
    _here_pkg = _os_pkg.path.dirname(_os_pkg.path.abspath(__file__)) if "__file__" in globals() \
        else _os_pkg.getcwd()
    if _here_pkg not in _sys_pkg.path:
        _sys_pkg.path.insert(0, _here_pkg)
        print(f"[Bootstrap] oncotriage was not importable; added {_here_pkg} to sys.path")
    import oncotriage as _oncotriage_pkg  # noqa: F401
    del _os_pkg, _sys_pkg, _here_pkg

# Deleted rather than left bound. This file is exec'd into the shared namespace
# by five other files, and a probe name left behind there is a name the next
# file to be written inherits without asking. "47- Package Split Test.py"
# asserts this shim adds NOTHING to the pre-pass surface, and caught this leak.
del _oncotriage_pkg


# The offline builders. Imported here, and only here: nothing in the
# pipeline calls them, and the filter module does not import them either.
from oncotriage.registries.mesh_crosswalk_build import (  # noqa: E402
    build_all_lookups,
    build_icd10_to_mesh_crosswalk,
    build_mesh_lookup,
    build_snomed_to_mesh_crosswalk,
    build_umls_synonym_crosswalk,
)


# The runtime filter. load_mesh_filter() is what File 13 calls.
from oncotriage.registries.mesh import (
    MeSHCancerFilter,
    PAN_CANCER_TREE_MAX_DEPTH,
    load_mesh_filter,
    specific_cancer_trees,
)




# ===========================================================================
# MAIN: Build lookup files from raw data
# ===========================================================================


if __name__ == "__main__":

    # Paths — uses project path convention from 01- Imports.py
    # Item 20a: this file sits in the code directory, so __file__ locates it with
    # no hardcoded path. __file__ is bound when the file is run as a script (every
    # documented entry point for it) and when Spyder runfile()s it. In a bare
    # interactive paste it is not bound, and the working directory is the only
    # remaining candidate -- taken, but announced, never silently.
    import os as _os_boot
    if "__file__" in globals():
        _code_dir = _os_boot.path.dirname(_os_boot.path.abspath(__file__)) + _os_boot.sep
    else:
        _code_dir = _os_boot.getcwd() + _os_boot.sep
        print(f"[Bootstrap] __file__ unbound; using the working directory as the code directory: {_code_dir}")
    del _os_boot

    for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
        with open(_code_dir + _bootstrap) as _fh:
            exec(_fh.read(), globals())

    exec_chain(
        ["03- Config.py"],
        caller_file=_code_dir + "09- MeSH Cancer Site Relevance Filter.py",
        caller_globals=globals(),
        chain_label="01 → 02 → 03",
    )

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
