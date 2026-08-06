# Filter Cancer Patients Only (IN-PLACE DELETION)
##################################################

"""
Step 2: filter synthetic patients to keep only those with cancer diagnoses.
Deletes non-cancer patients directly from the fhir/ directory.

THIN ENTRY POINT (item 20c pass 3a moved the logic; pass 20e removed the shim)
------------------------------------------------------------------------------
Every definition lives in ``oncotriage/fhir/clean.py``. What is left here is a
``__main__`` guard, the argparse it needs, and one call.

WHY THIS STOPPED BEING A SHIM. Pass 3a kept a full re-export shim here for one
call site: ``34- Cohort Selector Diff Read Only.py`` chained this file and read
``has_cancer_diagnosis`` and ``_CANCER_REGISTRY`` out of the shared exec
namespace. Pass 20c-3d converted File 34 into a thin entry point over
``oncotriage/evaluation/cohort_diff.py``, which imports both from the package by
name -- so the shim's only consumer stopped existing two passes before the shim
did. Pass 20e MEASURED that rather than inheriting the claim: all fifteen
top-level names this file bound, and the string "05- FHIR Clean Data.py"
itself, were grepped across every .py, .md, .toml and .yml in the tree, and the
only functional hit left is ``tests/test_degraded_dependencies.py`` ast-parsing
this file to check the dry-run wiring -- a reader, not a chainer.

NO EXEC BOOTSTRAP. This file used to raw-exec "01- Imports.py",
"02- Utility Functions.py" and "03- Config.py" and then ``exec_chain`` Files 07
and 08. All five of those files are deleted by pass 20e; nothing chained them
either. `python "05- FHIR Clean Data.py"` behaves exactly as before, and it no
longer imports torch, transformers, streamlit or langgraph, and no longer opens
an OpenAI or a Qdrant client, in order to delete patient bundles.

WHAT MOVED WITH THE SHIM'S DOCSTRING is in ``oncotriage/fhir/clean.py``: why
``patients_dir()`` / ``manifest_path()`` / ``cancer_registry()`` are accessors
rather than module-level statements, why ``_EXCLUDE_VERIFICATION`` must not be
redefined here, and why ``dry_run`` is a parameter rather than a second helper.

Run from terminal:
    python "05- FHIR Clean Data.py"            # DELETES bundles in place
    python "05- FHIR Clean Data.py" --dry-run  # writes the plan, deletes nothing
"""

import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# See the same block in "04- FHIR Generate Data.py" and "16- Database Query.py".
# `pip install -e .` from 03- Code/ makes it a no-op; without it the code
# directory is added to sys.path and the fact is printed rather than left
# silent. This replaces the sys.path work "01- Imports.py" used to do.
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

from oncotriage.config import Project_Name  # noqa: E402
from oncotriage.fhir.clean import filter_cancer_patients_inplace  # noqa: E402


#------------------------------------------------------------------------------


if __name__ == "__main__":

    # --dry-run reaches filter_cancer_patients_inplace(dry_run=True), added by
    # item 11a. Parsed with argparse INSIDE this block so no name leaks into the
    # shared exec namespace: exec_chain sets __name__ to "_exec_chain_", so a
    # chained load never enters here at all, and File 47's shim probe -- which
    # pins this file's surface at fourteen names -- does the same.
    #
    # A FLAG RATHER THAN A PROMPT. This is the plan, not a confirmation step:
    # it writes the full list to {manifest}.dryrun and exits, so the operator
    # reads it, then runs the command again without the flag.
    import argparse as _argparse_main

    _args_main = _argparse_main.ArgumentParser(
        description="Filter the Synthea cohort to alive primary-cancer "
                    "patients, capped. DELETES BUNDLES IN PLACE.",
    )
    _args_main.add_argument(
        "--dry-run", action="store_true",
        help="scan and report exactly what would be deleted, write the full "
             "list to the deletion manifest path with a .dryrun suffix, and "
             "delete NOTHING.",
    )
    _opts_main = _args_main.parse_args()

    print()
    print("╔═══════════════════════════════════════════════════════════════════════╗")
    print(f"║                  {Project_Name}: FILTER CANCER PATIENTS              ║")
    print("╚═══════════════════════════════════════════════════════════════════════╝")
    print()

    # Filter patients (in-place deletion)
    stats = filter_cancer_patients_inplace(dry_run=_opts_main.dry_run)

    if stats and stats['dry_run']:
        print()
        print("DRY RUN — nothing was deleted.")
        print()
        print(f"Summary:")
        print(f"  - Total scanned: {stats['total_scanned']}")
        print(f"  - Cancer patients found: {stats['cancer_patients_found']} "
              f"({stats['alive_cancer_patients_found']} alive, "
              f"{stats['deceased_cancer_patients_found']} deceased)")
        print(f"  - WOULD delete, non-cancer: {stats['would_delete']['non_cancer']}")
        print(f"  - WOULD delete, deceased: {stats['would_delete']['deceased']}")
        print(f"  - WOULD delete, over cap: {stats['would_delete']['over_cap']}")
        print(f"  - WOULD delete, total: {stats['would_delete']['total']}")
        print(f"  - Files still on disk: {stats['files_remaining']}")
        print(f"  - Plan: {stats['manifest_written']}")
        print()
        print("NEXT STEP: re-run without --dry-run to perform the deletion.")
        print()
    elif stats and not stats['deletion_failures']:
        print()
        print("SUCCESS! Cancer patient filtering complete.")
        print()
        print(f"Summary:")
        print(f"  - Total scanned: {stats['total_scanned']}")
        print(f"  - Cancer patients found: {stats['cancer_patients_found']} "
              f"({stats['alive_cancer_patients_found']} alive, "
              f"{stats['deceased_cancer_patients_found']} deceased)")
        print(f"  - Non-cancer deleted: {stats['non_cancer_deleted']}")
        print(f"  - Deceased deleted: {stats['deceased_deleted']}")
        print(f"  - Extra deleted (cap): {stats['extra_deleted']}")
        print(f"  - Unknown vital status (kept): {stats['unknown_vital_status']}")
        print(f"  - Parse errors (left on disk): {stats['parse_errors']}")
        print(f"  - Final dataset: {stats['final_cancer_patients']} patients")
        print(f"  - Manifest: {stats['manifest_path']}")
        print()
        print("NEXT STEP: Run FHIR Parser to parse the cancer patients")
        print()
    elif stats:
        # Deletions failed: the directory is partially filtered. Exit non-zero
        # so a caller (or Airflow) does not treat this cohort as final.
        print()
        print("FILTERING INCOMPLETE — some deletions failed.")
        print(f"  - Deletions failed: {stats['deletion_failures']}")
        print(f"  - Files remaining: {stats['files_remaining']}")
        print(f"  - Manifest ({stats['manifest_status']}): {stats['manifest_path']}")
        print()
        print("The manifest lists every targeted file and the error for each "
              "failure. Re-run after resolving them; already-deleted files are "
              "reported as already absent.")
        print()
        sys.exit(1)
    else:
        print()
        print("Filtering failed.")
        print()
        sys.exit(1)

#------------------------------------------------------------------------------


"""
Created on Wed Feb 11 11:22:14 2026

@author: ramyalsaffar
"""
