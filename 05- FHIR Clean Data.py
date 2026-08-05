# Filter Cancer Patients Only (IN-PLACE DELETION)
##################################################

"""
Step 2: Filter synthetic patients to keep only those with cancer diagnoses
Deletes non-cancer patients directly from fhir/ directory

RE-EXPORT SHIM (item 20c, pass 3a)
----------------------------------
The definitions moved to ``oncotriage/fhir/clean.py``. This file re-exports
every name it used to bind and keeps its ``__main__`` block, so
``python "05- FHIR Clean Data.py"`` behaves exactly as before.

IT IS THE ONLY ONE OF THE FIVE FILES CONVERTED IN THIS PASS THAT KEEPS A SHIM,
and the reason is a single call site: ``34- Cohort Selector Diff.py`` line 68
chains this file and calls ``has_cancer_diagnosis()`` out of the shared exec
namespace. File 05 is therefore a LIBRARY as well as a script. Files 04, 06, 11
and 12 have no chain consumer anywhere in the repository -- verified by grepping
every top-level name each of them defines against every .py, .md, .toml and .yml
in the tree -- so those four dropped their exec bootstraps entirely and became
thin entry points.

THE CHAIN OF 07 AND 08 STAYS. File 34's chain label is "01 → 02 → 03 → 05 (→ 07
→ 08)" and it reads ``_select_best_coding`` (File 07) and ``_CANCER_REGISTRY``
(bound below) straight out of the namespace this file leaves behind. The package
module imports those two for itself; the chain below is what puts them in the
CALLER's namespace, which is a different job and still this file's.

THREE NAMES ARE RESOLVED EAGERLY HERE AND LAZILY IN THE PACKAGE.
``PATIENTS_DIR``, ``_MANIFEST_PATH`` and ``_CANCER_REGISTRY`` were module-level
statements in File 05, so loading it globbed the sibling data tree and built the
whole ICD-10-CM code set. A package module may not do any of that at import (see
CLAUDE.md), so they became ``patients_dir()``, ``manifest_path()`` and
``cancer_registry()`` -- resolved on first call, cached. This shim calls all
three and binds the eager names, which is the same thing ``03- Config.py`` does
for ``openai_client`` / ``qdrant_client``: the chain sees the values it always
saw, and they are the SAME objects the package hands out, because each accessor
caches.
"""


# Run needed files
#-----------------
# Bootstrap comes FIRST because the re-exports below need `oncotriage` to be
# importable, which 01- Imports.py arranges when the package is not pip-installed.
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

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py", "03- Config.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())


#------------------------------------------------------------------------------


# File 07 is chained for _select_best_coding(): the cohort filter must read a
# condition's codings exactly the way the pipeline's parser does, or the set of
# patients on disk stops agreeing with the set of patients the pipeline calls
# cancer patients. File 08 supplies the registry names File 34 also reads.
exec_chain(
    ["07- FHIR Parser.py", "08- Cancer Code Registry.py"],
    caller_file=_code_dir + "05- FHIR Clean Data.py",
    caller_globals=globals(),
    chain_label="07 → 08",
)


#------------------------------------------------------------------------------


# The re-exports
#---------------
# Explicit, never `import *`. A star import over a module whose surface changes
# would silently change what this file puts into the shared exec namespace, and
# the shared namespace is precisely what File 34 reads.
#
# The three accessors come in under private aliases, are called once, and are
# then DELETED. That keeps this file's surface exactly the fourteen names File 05
# bound before the move -- "47- Package Split Test.py" section 5 pins that list
# and fails on an addition as loudly as on a deletion, because a name this file
# adds is a name the next file in a chain would silently pick up.
from oncotriage.fhir.clean import (
    CAP,
    RANDOM_SEED,
    _DELETION_COUNTS,
    _delete_manifested,
    _write_manifest,
    cancer_registry as _cancer_registry_accessor,
    filter_cancer_patients_inplace,
    has_cancer_diagnosis,
    manifest_path as _manifest_path_accessor,
    patient_death_status,
    patients_dir as _patients_dir_accessor,
)

# Directory with all patients (will delete non-cancer ones in-place)
PATIENTS_DIR = _patients_dir_accessor()

# Deletion manifest path, written before anything is unlinked
_MANIFEST_PATH = _manifest_path_accessor()

# The same registry instance the pipeline classifies conditions with. File 34
# reads this name directly (lines 134, 220, 227).
#
# _EXCLUDE_VERIFICATION is NOT redefined here. File 08 owns it
# (08- Cancer Code Registry.py, module level) and the registry exposes it as
# .exclude_verification. A second frozenset with the same values today is a
# second frozenset with different values the day one of them is edited, and
# under the exec() chain the later definition silently wins for every file
# loaded after this one. Read it off the registry instead.
_CANCER_REGISTRY = _cancer_registry_accessor()

del _patients_dir_accessor, _manifest_path_accessor, _cancer_registry_accessor


#------------------------------------------------------------------------------


if __name__ == "__main__":

    print()
    print("╔═══════════════════════════════════════════════════════════════════════╗")
    print(f"║                  {Project_Name}: FILTER CANCER PATIENTS              ║")
    print("╚═══════════════════════════════════════════════════════════════════════╝")
    print()

    # Filter patients (in-place deletion)
    stats = filter_cancer_patients_inplace()

    if stats and not stats['deletion_failures']:
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
