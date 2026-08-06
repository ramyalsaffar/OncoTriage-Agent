# Filter Cancer Patients Only (IN-PLACE DELETION)
##################################################

"""Step 2: keep only patients with a primary cancer diagnosis, alive, capped.

Moved out of ``05- FHIR Clean Data.py`` by item 20c, pass 3a. That file survives
as an explicit re-export shim, and it is the ONLY one of the five files this pass
converted that keeps one: ``34- Cohort Selector Diff.py`` chains File 05 and
calls ``has_cancer_diagnosis()`` out of the shared exec namespace, so File 05 is
a library as well as a script and both roles have to keep working. Files 04, 06,
11 and 12 have no chain consumer at all and became thin entry points.

WHAT CHANGED, and why it had to
-------------------------------
The logic is unchanged. What changed is WHEN three module-level statements run.
File 05 did all three at load:

    PATIENTS_DIR     = data_fhir_path                       -> resolves a glob
    _MANIFEST_PATH   = os.path.join(checkpoint_path, ...)   -> resolves a glob
    _CANCER_REGISTRY = load_registry()                      -> imports icd10 and
                                                               builds the whole
                                                               ICD-10-CM set

Every one of those violates the package's import rule -- "importing a package
module opens no client, loads no model, touches no database, reads no file and
resolves no directory" -- and the first two are the exact defect pass 20c-2b
removed from ``oncotriage/paths.py``: a module that resolves the sibling data
tree at import cannot be imported at all on a machine that does not have it.
``47- Package Split Test.py`` check 2c imports every package module in its own
subprocess with the root pointed at a directory that does not exist, so this one
would have failed the moment it landed.

All three are now accessors that resolve on FIRST CALL and cache:
``patients_dir()``, ``manifest_path()``, ``cancer_registry()``. The shim calls
all three at load and binds the eager names the exec chain expects, so a caller
going through File 05 sees exactly what it always saw -- the same strings, and
the SAME registry object, because the accessor caches.

THE COUNTERS ARE MODULE STATE AND STAY MODULE STATE. ``_DELETION_COUNTS`` is a
plain dict of ints, mutated by ``_delete_manifested`` and read by
``filter_cancer_patients_inplace`` at the end of the run. The shim imports the
dict itself, not a copy, so the shim's ``_DELETION_COUNTS`` and this module's are
one object and a caller reading it after a run sees the run's numbers.

ITEM 11a ADDED TWO GUARDS AND A PARAMETER
-----------------------------------------
``require_intact_registry()`` runs before anything is scanned and raises if the
cancer registry is missing a detection layer -- REGARDLESS of
ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES, which is the one place in the package that
variable is deliberately not honoured. A degraded registry here means a missing
pip package deletes patient bundles. See that function for the full argument.

``filter_cancer_patients_inplace(dry_run=True)`` scans and plans exactly as
usual, writes the plan to ``{manifest_path()}.dryrun`` and unlinks nothing. It
is a PARAMETER rather than a new exported helper because File 47 section 5 pins
this module's shim surface at fourteen names, and because a plan produced by a
second implementation is a plan that can disagree with the deletion.
"""

import json
import os
import random
import threading
from datetime import datetime, timezone
from pathlib import Path

from oncotriage import paths, settings
from oncotriage.config import COHORT_MANIFEST_FILENAME, COHORT_MANIFEST_FLUSH_EVERY
from oncotriage.fhir.parser import _select_best_coding
from oncotriage.registries.cancer_code_registry import load_registry


#------------------------------------------------------------------------------


# Configuration
#--------------

# The max number of patients with cancer
CAP = 1000

# Seed for the reproducible down-sample to CAP patients
RANDOM_SEED = 42


# _EXCLUDE_VERIFICATION is NOT redefined here. The cancer code registry owns it
# and exposes it as .exclude_verification. A second frozenset with the same
# values today is a second frozenset with different values the day one of them
# is edited, and under the exec() chain the later definition silently wins for
# every file loaded after this one. Read it off the registry instead.


#------------------------------------------------------------------------------


# The three lazily-resolved dependencies
#---------------------------------------
#
# Each was a module-level statement in File 05 and each is now resolved on first
# call and cached. Plain functions rather than a PEP 562 module __getattr__,
# unlike oncotriage/paths.py: a module __getattr__ is consulted for attribute
# access on the MODULE and not for a global name lookup inside a function body,
# so `PATIENTS_DIR` written bare inside filter_cancer_patients_inplace() would be
# a NameError rather than a lazy read. Every call site below therefore calls the
# accessor, which is visible in the source and cannot be got wrong.

_RESOLVED = {}

# THE CACHE IS LOCKED (pass 20c-3b), for consistency with oncotriage.agent.deps
# and oncotriage.paths, both of which guard their caches the same way.
#
# Pass 3a wrote `if name not in _RESOLVED: _RESOLVED[name] = build()`. Each of
# those two dict operations is atomic under the GIL; the SEQUENCE is not. Two
# threads entering cancer_registry() together can both miss and both call
# load_registry(), and while load_registry() is itself a cached singleton --
# so the two would converge on one object -- patients_dir() and manifest_path()
# have no such backstop and the pattern is what is being copied when someone
# adds a fourth accessor here.
#
# An RLock rather than a Lock, matching deps: an accessor holds it while calling
# a factory, and a factory that reached another accessor in this module would
# deadlock on a plain Lock. Nothing does that today; the cost of the RLock is
# nothing, and the cost of being wrong about it later is a hang.
#
# THIS FILE IS NOT MULTI-THREADED TODAY. filter_cancer_patients_inplace() runs
# on one thread. The lock is here because the accessors are importable and the
# shim calls all three at load, so "only File 05 calls these" is a property of
# today's callers rather than of this module.
_RESOLVE_LOCK = threading.RLock()


def patients_dir():
    """The directory this file deletes from. ``paths.data_fhir_path``, cached.

    Resolved on first call, never at import. File 05 assigned it at module level
    and every consumer of that assignment paid a glob over the sibling data tree
    just by loading the file.
    """
    with _RESOLVE_LOCK:
        if "patients_dir" not in _RESOLVED:
            _RESOLVED["patients_dir"] = paths.data_fhir_path
        return _RESOLVED["patients_dir"]


def manifest_path():
    """Where the deletion manifest is written. Resolved on first call, cached."""
    with _RESOLVE_LOCK:
        if "manifest_path" not in _RESOLVED:
            _RESOLVED["manifest_path"] = os.path.join(paths.checkpoint_path,
                                                      COHORT_MANIFEST_FILENAME)
        return _RESOLVED["manifest_path"]


def cancer_registry():
    """The same registry instance the pipeline classifies conditions with.

    ``load_registry()`` is itself a cached singleton, so this returns the object
    the agent, the storage logger and File 06 all use -- one registry per
    process, whoever asks for it first.

    NOT routed through ``oncotriage.agent.deps.get_cancer_registry()``, and that
    is deliberate. The deps seam exists so a harness can redirect what the AGENT
    reaches; this is the cohort filter, which decides which bundles stay on disk.
    A stub registry installed for an agent test must not silently change which
    patients a deletion pass removes.
    """
    with _RESOLVE_LOCK:
        if "cancer_registry" not in _RESOLVED:
            _RESOLVED["cancer_registry"] = load_registry()
        return _RESOLVED["cancer_registry"]


def require_intact_registry():
    """Raise unless the cancer registry has every detection layer. Returns it.

    THE DEGRADED-MODE OPT-OUT DOES NOT REACH THIS PATH, and that is the whole
    reason this function exists rather than a bare ``cancer_registry()`` call.

    ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES lets a run continue with the ICD-10-CM
    layer absent. For the AGENT that is a defensible trade: a patient is scored
    against fewer trials, or against trials a fuller registry would have
    filtered, and the row in ``inferences.db`` is still there to re-run. For
    THIS module it is not a trade at all. ``filter_cancer_patients_inplace()``
    UNLINKS patient bundles on the strength of
    ``CancerCodeRegistry.is_primary_cancer()``, and Synthea's corpus is
    regenerable only by re-running File 04 with the same seed -- the deletion
    itself is irreversible within a run.

    With the ICD-10 layer gone, `is_primary_cancer` loses BOTH directions at
    once: ICD-10-coded cancers stop being recognised, so their bundles are
    deleted as non-cancer; and the D00-D49 / C77-C79 exclusion sets stop
    rejecting, so in-situ and metastatic-only records can be admitted by the
    display-term fallback. A missing `pip install icd10-cm` would delete the
    dataset, which is precisely the failure the environment variable would
    otherwise re-create with the operator's own blessing. So the variable is not
    consulted here at all: this refuses on the REGISTRY's reported state.

    Raises:
        settings.DegradedDependencyError: naming the absent layer(s).
    """
    registry = cancer_registry()

    # getattr, not attribute access. This function must also refuse a registry
    # object that predates `degraded_layers` or a stand-in that does not carry
    # it: "the object cannot tell me whether it is intact" is not the same
    # answer as "it is intact", and only one of the two may proceed to delete.
    degraded = getattr(registry, "degraded_layers", None)
    if degraded is None:
        raise settings.DegradedDependencyError(
            f"the cohort filter cannot verify the cancer registry: "
            f"{type(registry).__name__} does not report `degraded_layers`, so "
            f"there is no way to tell an intact registry from one missing a "
            f"detection layer.\n"
            f"  This path DELETES patient bundles from the corpus on that "
            f"registry's verdicts and will not do so unverified.",
            layer=None,
        )

    if degraded:
        raise settings.DegradedDependencyError(
            f"REFUSING TO DELETE: the cancer registry is missing detection "
            f"layer(s) {', '.join(degraded)}.\n"
            f"  This path unlinks patient bundles on "
            f"CancerCodeRegistry.is_primary_cancer(), and a degraded registry "
            f"gets it wrong in BOTH directions -- unrecognised cancers are "
            f"deleted as non-cancer, and non-invasive or metastatic-only "
            f"records the exclusion sets would have rejected can be admitted "
            f"by the display-term fallback.\n"
            f"  Fix it:   pip install icd10-cm\n"
            f"  {settings.ENV_ALLOW_DEGRADED_REGISTRIES} DOES NOT APPLY HERE. "
            f"It permits a degraded agent run, whose output is a row in a "
            f"database; this deletes data. Run "
            f"filter_cancer_patients_inplace(dry_run=True) to see the plan "
            f"without touching anything.",
            layer=degraded[0],
        )

    return registry


#------------------------------------------------------------------------------


# Deletion Manifest
#------------------
#
# Every unlink in this file is manifest-backed. The rule is: nothing is
# deleted that was not written to the manifest first, and every outcome
# (deleted / already absent / failed, with the error text) is written back.
# The deletion is in-place and irreversible, so a half-finished run must still
# be able to answer "which 4,300 files did it remove before it died".

# Outcome counters for the whole run. No except: branch below leaves without
# incrementing one of these.
#
# A DRY RUN NEVER TOUCHES THESE. `would_delete` is its own key precisely so a
# dry run cannot look like a real one in the numbers a caller reads afterwards:
# the shim re-exports this dict itself, not a copy, so whatever a dry run wrote
# here would still be here when the real run was inspected.
_DELETION_COUNTS = {
    "deleted":        0,
    "already_absent": 0,
    "failed":         0,
    "manifest_write_failed": 0,
    "would_delete":   0,
}

# What a dry run appends to the manifest filename. A SEPARATE FILE, not a
# suppressed write: the plan is the entire product of a dry run, and printing it
# to a console that scrolls is not a record. Writing it to the real manifest
# path would be worse still -- it would overwrite the account of the last real
# deletion with a description of one that never happened, and that account is
# the only thing that says which 4,300 bundles are gone.
DRY_RUN_MANIFEST_SUFFIX = ".dryrun"


def _write_manifest(manifest, path=None):
    """
    Persist the manifest atomically (temp file + os.replace + fsync).

    Args:
        manifest: the manifest dict.
        path:     destination. Defaults to manifest_path(); a dry run passes
                  manifest_path() + DRY_RUN_MANIFEST_SUFFIX.

    Returns True on success. A manifest write that fails is recorded in
    _DELETION_COUNTS and raised: losing the record is not a recoverable
    condition, because the next thing this file does is delete data that only
    the manifest describes.
    """
    target = path or manifest_path()
    tmp_path = target + ".tmp"
    try:
        with open(tmp_path, "w") as fh:
            json.dump(manifest, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
        return True
    except OSError as e:
        _DELETION_COUNTS["manifest_write_failed"] += 1
        print(f"  FATAL: cannot write deletion manifest {target}: {e}")
        raise


def _delete_manifested(manifest, phase_key, files, reason, progress_every=500,
                       dry_run=False, manifest_target=None):
    """
    Delete `files`, recording every outcome in the manifest.

    Args:
        manifest:        the live manifest dict (mutated and rewritten here)
        phase_key:       manifest["phases"] key for this batch, e.g. "non_cancer"
        files:           list of Path objects to remove
        reason:          why these files are being removed (goes in the manifest)
        progress_every:  console progress interval
        dry_run:         when True, NOTHING is unlinked. Every target lands in
                         phase["would_delete"] instead of phase["deleted"], and
                         the manifest is written to `manifest_target`.
        manifest_target: where to write the manifest; None means the real path.

    Returns:
        dict: this phase's record — planned/deleted/would_delete/
        already_absent/failed.

    The plan is written and fsync'd before the first unlink, so a crash at any
    point leaves the full target list on disk plus every outcome recorded up to
    the last flush. Individual failures do not abort the loop: aborting halfway
    would leave the remaining targets untouched AND unexplained, whereas
    finishing the sweep leaves a complete account. The caller decides what a
    non-zero failure count means.

    THE DRY-RUN BRANCH IS ONE `if` AROUND THE UNLINK, on purpose. Everything
    else -- the plan, the manifest shape, the flush interval, the progress
    lines, the phase status -- runs exactly as it does for real, so what a dry
    run reports is produced by the same code that would do the deleting rather
    than by a second implementation that could disagree with it.
    """
    phase = {
        "reason":         reason,
        "status":         "in_progress",
        "dry_run":        dry_run,
        "planned_count":  len(files),
        "planned":        [f.name for f in files],
        "deleted":        [],
        # Populated only by a dry run. Present and empty on a real run so the
        # two manifests have the same shape and a reader never has to guess
        # whether a missing key means "none" or "this file is from before".
        "would_delete":   [],
        "already_absent": [],
        "failed":         [],
    }
    manifest["phases"][phase_key] = phase
    manifest["status"] = "dry_run_in_progress" if dry_run else "in_progress"
    _write_manifest(manifest, manifest_target)

    for idx, patient_file in enumerate(files, 1):

        if dry_run:
            # NO unlink. The existence check is still made, because "already
            # absent" is a real outcome a plan should report and it costs one
            # stat() call.
            #
            # ONLY `would_delete` IS TOUCHED IN _DELETION_COUNTS. `already_absent`
            # is recorded in the PHASE, which belongs to this plan, and not in
            # the module counter, which is the record of what a run DID. The
            # shim re-exports that dict itself rather than a copy, so a number a
            # dry run left in it would still be there when someone inspected it
            # after a real run — and "targets already absent: 3" printed by a
            # run that unlinked nothing is exactly the kind of trace a dry run
            # must not leave.
            if patient_file.exists():
                phase["would_delete"].append(patient_file.name)
                _DELETION_COUNTS["would_delete"] += 1
            else:
                phase["already_absent"].append(patient_file.name)

            if idx % COHORT_MANIFEST_FLUSH_EVERY == 0:
                _write_manifest(manifest, manifest_target)
            if idx % progress_every == 0:
                print(f"  [DRY RUN] Would delete {idx}/{len(files)} files...")
            continue

        try:
            patient_file.unlink()
            phase["deleted"].append(patient_file.name)
            _DELETION_COUNTS["deleted"] += 1

        except FileNotFoundError:
            # Already gone — a re-run over a partially filtered directory, or a
            # concurrent writer. Not an error, but it is not a deletion either.
            phase["already_absent"].append(patient_file.name)
            _DELETION_COUNTS["already_absent"] += 1
            print(f"  WARNING: already absent, not deleted: {patient_file.name}")

        except OSError as e:
            phase["failed"].append({
                "file":  patient_file.name,
                "error": f"{type(e).__name__}: {e}",
            })
            _DELETION_COUNTS["failed"] += 1
            print(f"  ERROR deleting {patient_file.name}: {type(e).__name__}: {e}")

        if idx % COHORT_MANIFEST_FLUSH_EVERY == 0:
            _write_manifest(manifest, manifest_target)

        if idx % progress_every == 0:
            print(f"  Deleted {idx}/{len(files)} files...")

    if dry_run:
        # "planned", never "complete": a dry-run phase must not be mistakable
        # for a finished deletion by anything reading the manifest later.
        phase["status"] = "planned"
    else:
        phase["status"] = "complete" if not phase["failed"] else "partial"
    _write_manifest(manifest, manifest_target)

    return phase


#------------------------------------------------------------------------------


# Filter Functions
#------------------

def has_cancer_diagnosis(bundle_data):
    """
    Determine whether a patient FHIR bundle contains an active primary cancer diagnosis.

    Uses CancerCodeRegistry (SNOMED + ICD-10-CM 2024) for code-based detection —
    the same registry used downstream in File 10. This guarantees that every patient
    kept by File 05 will be recognized as a cancer patient by the matching pipeline.

    Coding selection goes through File 07's _select_best_coding(), the same
    function parse_fhir_bundle() uses, and the full annotated coding list is
    handed to is_primary_cancer() under the "codings" key. This replaced a
    coding_list[0] read that trusted Synthea to emit SNOMED first:

      - Positional reads break on any bundle whose Condition.code.coding array
        is ordered differently — which is the normal case in real EHR data and
        the reason _select_best_coding() exists at all.
      - Passing a single {code, display} dict pushed is_primary_cancer() onto
        its backward-compatible path, where the code carries system_key
        "unknown" and only ONE coding is examined. The multi-coding logic —
        exclude if ANY coding is secondary/metastatic or non-invasive, admit if
        ANY coding matches a primary set — never ran. A condition coded
        C78.00 (secondary lung) in position 1 and a primary site code in
        position 0 was admitted; the pipeline, reading all codings, rejects it.

    The cohort filter and the pipeline now classify a condition through the
    same code path, so a patient kept here cannot be dropped downstream for
    not being a cancer patient.

    Skips conditions marked refuted or entered-in-error (verificationStatus),
    using File 08's set via the registry rather than a local copy.
    Secondary/metastatic conditions are excluded inside is_primary_cancer().
    Display-name keyword matching is intentionally avoided unless no coding is
    usable — SNOMED codes are stable and unambiguous; display strings are not.

    Args:
        bundle_data: parsed FHIR Bundle dict (from json.load)

    Returns:
        tuple: (has_cancer: bool, cancer_types: list[str])
            has_cancer   — True if at least one primary cancer condition found
            cancer_types — list of display strings for matched cancer conditions
                           (empty list if none found)
    """
    if not bundle_data or not isinstance(bundle_data, dict):
        return False, []

    registry = cancer_registry()
    cancer_types = []

    for entry in bundle_data.get('entry', []):
        resource = entry.get('resource', {})
        if not resource or resource.get('resourceType') != 'Condition':
            continue

        # Skip retracted / data-entry-error conditions
        ver_codings = resource.get('verificationStatus', {}).get('coding', [])
        verification = ver_codings[0].get('code', 'unknown').lower().strip() if ver_codings else 'unknown'

        if verification in registry.exclude_verification:
            continue

        # Resolve the coding array the same way the pipeline's parser does:
        # system-preference selection for the representative code, and every
        # coding annotated with its system_key for the multi-coding checks in
        # is_primary_cancer(). No positional assumption about coding order.
        coding_list = resource.get('code', {}).get('coding', [])
        best_coding, all_codings = _select_best_coding(coding_list, "condition")
        condition = {
            'code':    best_coding['code'],
            'display': best_coding['display'],
            'codings': all_codings,
        }

        if registry.is_primary_cancer(condition):
            display = best_coding['display']
            if not display or display == 'unknown':
                display = 'Unknown cancer'
            cancer_types.append(display)

    return len(cancer_types) > 0, cancer_types


def patient_death_status(bundle_data):
    """
    Read vital status off the Patient resource already in memory.

    Called from the same pass as has_cancer_diagnosis() so the bundle is parsed
    once. A second pass over the cohort would mean re-reading and re-parsing
    tens of gigabytes of JSON to learn one boolean.

    FHIR R4 types Patient.deceased[x] as a choice of deceasedBoolean or
    deceasedDateTime, and BOTH have to be read: Synthea writes
    deceasedDateTime, real exports frequently write deceasedBoolean, and a
    consumer that checks only one silently reads every patient as alive.
    Absence of the element means alive -- that is the FHIR default, not a
    guess.

    Returns:
        tuple: (deceased, evidence)
            deceased  True / False, or None when the bundle carries no Patient
                      resource at all and vital status is therefore UNKNOWN.
                      None is NOT False: the caller must not delete on it.
            evidence  the element and value the verdict came from, for the
                      manifest.
    """
    if not bundle_data or not isinstance(bundle_data, dict):
        return None, 'bundle is not a dict'

    for entry in bundle_data.get('entry', []):
        resource = entry.get('resource', {})
        if not isinstance(resource, dict) or resource.get('resourceType') != 'Patient':
            continue

        # deceasedDateTime first: when a record carries both, a date is the
        # more specific statement and the one Synthea emits.
        deceased_datetime = resource.get('deceasedDateTime')
        if deceased_datetime:
            return True, f'deceasedDateTime={deceased_datetime}'

        deceased_boolean = resource.get('deceasedBoolean')
        if deceased_boolean is True:
            return True, 'deceasedBoolean=true'
        if deceased_boolean is False:
            return False, 'deceasedBoolean=false'

        return False, 'no deceased[x] element (FHIR default: alive)'

    return None, 'no Patient resource in bundle'


def filter_cancer_patients_inplace(dry_run=False):
    """
    Delete non-cancer patients, then deceased cancer patients, then cap at CAP
    (in-place filtering).

    Args:
        dry_run: when True, scan and plan exactly as usual, print exactly what
            would be removed, write the plan to
            ``{manifest_path()}.dryrun`` -- and unlink NOTHING. The returned
            stats carry ``dry_run: True`` and every ``*_deleted`` count is 0,
            with the counts that matter under ``would_delete``. Default False,
            so the documented invocation ``python "05- FHIR Clean Data.py"``
            behaves exactly as it always has.

    IT IS A PARAMETER, NOT A SECOND FUNCTION. "47- Package Split Test.py"
    section 5 pins the File 05 shim's shared-namespace surface at exactly
    fourteen names and fails on an addition, so a `plan_cancer_patient_filter()`
    helper would either fail that check or have to be hidden from the shim --
    and a dry run reachable only through the package, while the deletion is
    reachable from the exec chain, is the wrong way round. A parameter also
    guarantees the plan and the deletion cannot drift: they are one code path
    with one `if` around the unlink.

    THE REGISTRY IS VERIFIED BEFORE ANYTHING IS SCANNED. `require_intact_registry()`
    raises if the cancer registry is missing a detection layer, and it does so
    whatever ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES says -- see that function for
    why the opt-out stops at this door. The check runs in dry-run mode too: a
    plan computed from a degraded registry is a wrong plan, and reporting it as
    a preview would be worse than refusing, because a reader would take it for
    the truth about their corpus.

    Three phases in that order, and the order is load-bearing:

        non_cancer  no primary cancer condition (has_cancer_diagnosis)
        deceased    has cancer, but Patient.deceased[x] says they are dead
        over_cap    alive cancer patients beyond CAP, seeded sample

    The deceased phase runs BEFORE the cap so the cap samples from alive
    patients only; capping first would spend cohort slots on the dead and then
    delete them, leaving well under CAP. Vital status is read in the same scan
    pass as the cancer check, from the same parsed bundle -- see
    patient_death_status().

    All three deletion phases go through _delete_manifested(), so the target list is
    on disk before anything is unlinked and every outcome is written back. A
    phase that hits IO errors finishes its sweep, marks itself "partial", and
    the run returns a non-zero deletion_failures count instead of exiting as if
    it had succeeded.

    Files that fail to parse are counted, listed in the manifest, left on disk,
    and excluded from the cohort. They are NOT part of the cap sampling pool:
    a bundle that could not be read has not been shown to contain a cancer
    diagnosis, and letting one occupy a slot in a 1,000-patient cancer cohort
    is the same silent-recovery defect this project exists to remove. Their
    presence in the directory is reported, since the directory then holds more
    .json files than the cohort has patients.

    Returns:
        dict: Statistics about filtering
    """

    print("="*80)
    if dry_run:
        print("STEP 2: FILTER CANCER PATIENTS — DRY RUN (NOTHING IS DELETED)")
    else:
        print("STEP 2: FILTER CANCER PATIENTS (IN-PLACE DELETION)")
    print("="*80)
    print()

    # BEFORE the scan, and before the manifest, and in dry-run mode too.
    require_intact_registry()

    # Where the plan or the record goes. A dry run gets its own file so it
    # cannot overwrite the account of the last real deletion.
    manifest_target = (manifest_path() + DRY_RUN_MANIFEST_SUFFIX
                       if dry_run else manifest_path())

    directory = patients_dir()

    # Check directory exists
    patients_path = Path(directory)
    if not patients_path.exists():
        print(f"ERROR: Patient directory not found: {directory}")
        return None

    # Get all patient files
    patient_files = list(patients_path.glob("*.json"))

    print(f"Patient directory: {directory}")
    print(f"Total patients: {len(patient_files)}")
    print()

    print("="*80)
    print("SCANNING PATIENTS FOR CANCER DIAGNOSES...")
    print("="*80)
    print()

    cancer_patients = []          # every primary-cancer patient, alive or not
    alive_cancer_patients = []    # the cap sampling pool
    deceased_cancer_patients = []
    unknown_vital_status = []     # cancer, but no Patient resource to read
    non_cancer_patients = []
    cancer_counts = {}
    error_patients = []
    death_evidence = {}           # filename -> which element decided it

    # Check each patient.
    #
    # ONE pass, TWO questions. Vital status is read from the same parsed bundle
    # as the cancer check: the corpus is tens of gigabytes and a second sweep to
    # learn one boolean per patient would cost as much as the whole scan.
    #
    # A patient with no primary cancer goes to non_cancer regardless of vital
    # status, so the deceased phase only ever targets cancer patients and the
    # two counts never overlap.
    for idx, patient_file in enumerate(patient_files, 1):
        if idx % 500 == 0:
            print(f"  Processed {idx}/{len(patient_files)} patients...")

        try:
            with open(patient_file, 'r') as f:
                bundle = json.load(f)

            has_cancer, cancer_types = has_cancer_diagnosis(bundle)
            deceased, evidence = patient_death_status(bundle)

            if not has_cancer:
                non_cancer_patients.append(patient_file)
                continue

            cancer_patients.append(patient_file)

            # Count cancer types
            for cancer in cancer_types:
                cancer_counts[cancer] = cancer_counts.get(cancer, 0) + 1

            death_evidence[patient_file.name] = evidence
            if deceased is True:
                deceased_cancer_patients.append(patient_file)
            elif deceased is None:
                # Vital status could not be read. NOT treated as deceased: the
                # deletion is irreversible and there is no evidence to delete
                # on. Kept in the cap pool, counted, and named in the manifest
                # so a non-zero count is visible rather than inferred.
                unknown_vital_status.append(patient_file)
                alive_cancer_patients.append(patient_file)
            else:
                alive_cancer_patients.append(patient_file)

        except Exception as e:
            print(f"  ERROR processing {patient_file.name}: {e}")
            error_patients.append(patient_file)

    print(f"  Processed {len(patient_files)}/{len(patient_files)} patients... \nDONE")
    print()

    # Results
    print("="*80)
    print("FILTERING RESULTS")
    print("="*80)
    print()
    print(f"Total patients scanned: {len(patient_files)}")
    print(f"Cancer patients found: {len(cancer_patients)}")
    print(f"  ... alive:    {len(alive_cancer_patients)}")
    print(f"  ... deceased: {len(deceased_cancer_patients)}")
    print(f"Non-cancer patients: {len(non_cancer_patients)}")
    cancer_pct = len(cancer_patients) / len(patient_files) * 100 if patient_files else 0.0
    alive_pct = len(alive_cancer_patients) / len(patient_files) * 100 if patient_files else 0.0
    print(f"Cancer percentage: {cancer_pct:.1f}%  (alive cancer: {alive_pct:.1f}%)")
    if unknown_vital_status:
        print(f"Cancer patients with UNREADABLE vital status (kept): {len(unknown_vital_status)}")
    if error_patients:
        print(f"Files with parse errors (skipped): {len(error_patients)}")
    print()

    if cancer_counts:
        print("Cancer types distribution:")
        for cancer_type, count in sorted(cancer_counts.items(),
                                         key=lambda x: x[1],
                                         reverse=True)[:15]:
            print(f"  • {cancer_type}: {count}")

        if len(cancer_counts) > 15:
            print(f"  ... and {len(cancer_counts) - 15} more types")
    print()

    # Open the deletion manifest before anything is unlinked.
    manifest = {
        'created_utc':          datetime.now(timezone.utc).isoformat(),
        'directory':            str(patients_path),
        'cap':                  CAP,
        'random_seed':          RANDOM_SEED,
        'selector':             '_select_best_coding("condition") + '
                                'CancerCodeRegistry.is_primary_cancer(codings=...)',
        'scanned':              len(patient_files),
        'cancer_found':         len(cancer_patients),
        'alive_cancer_found':   len(alive_cancer_patients),
        'deceased_cancer_found': len(deceased_cancer_patients),
        'non_cancer_found':     len(non_cancer_patients),
        'vital_status_source':  'Patient.deceasedDateTime, then Patient.deceasedBoolean; '
                                'absent = alive (FHIR default)',
        # Cancer patients whose vital status could not be read. Kept in the
        # cohort -- there is no evidence to delete on -- and named here so a
        # non-zero count cannot pass as zero.
        'unknown_vital_status_retained': sorted(f.name for f in unknown_vital_status),
        'unparseable_retained': sorted(f.name for f in error_patients),
        'dry_run':              dry_run,
        'status':               'planned',
        'phases':               {},
    }
    _write_manifest(manifest, manifest_target)
    print(f"{'DRY RUN plan' if dry_run else 'Deletion manifest'}: {manifest_target}")
    print()

    # STEP 1: Delete non-cancer patients (if any)
    non_cancer_deleted = 0
    non_cancer_would_delete = 0

    if non_cancer_patients:
        print("="*80)
        print(f"STEP 1: {'WOULD DELETE' if dry_run else 'DELETE'} "
              f"{len(non_cancer_patients)} NON-CANCER PATIENTS")
        print("="*80)
        print()

        phase = _delete_manifested(
            manifest,
            phase_key='non_cancer',
            files=non_cancer_patients,
            reason='no primary cancer condition found by has_cancer_diagnosis()',
            progress_every=500,
            dry_run=dry_run,
            manifest_target=manifest_target,
        )
        non_cancer_deleted = len(phase['deleted'])
        non_cancer_would_delete = len(phase['would_delete'])

        if dry_run:
            print(f"  [DRY RUN] Would delete "
                  f"{non_cancer_would_delete}/{len(non_cancer_patients)} "
                  f"non-cancer patients (nothing removed)")
        else:
            print(f"  Deleted {non_cancer_deleted}/{len(non_cancer_patients)} non-cancer patients... \nDONE")
        if phase['failed'] or phase['already_absent']:
            print(f"  NOT deleted: {len(phase['failed'])} failed, "
                  f"{len(phase['already_absent'])} already absent (see manifest)")
        print()
    else:
        print("="*80)
        print("STEP 1: NO NON-CANCER PATIENTS TO DELETE")
        print("="*80)
        print()

    # STEP 2: Delete DECEASED cancer patients
    #
    # A dead patient cannot enrol on a trial. Leaving them in produced a cohort
    # that was 57.7% deceased in the 2026-08-03 regeneration, so every
    # eligibility rate the pipeline reported was diluted by patients who could
    # never have been eligible for anything.
    #
    # Its own phase, its own phase key, its own count. Folding these into the
    # non-cancer phase would say "no primary cancer condition found" about a
    # patient who had one, and would make the two reasons for deletion
    # indistinguishable in the only record of the deletion.
    #
    # BEFORE the cap, so the cap samples from alive patients only. Capping
    # first would spend cohort slots on the dead and then delete them, leaving
    # far fewer than CAP.
    deceased_deleted = 0
    deceased_would_delete = 0

    if deceased_cancer_patients:
        print("="*80)
        print(f"STEP 2: {'WOULD DELETE' if dry_run else 'DELETE'} "
              f"{len(deceased_cancer_patients)} DECEASED CANCER PATIENTS")
        print("="*80)
        print()

        phase = _delete_manifested(
            manifest,
            phase_key='deceased',
            files=deceased_cancer_patients,
            reason='Patient.deceased[x] indicates the patient is dead; a deceased '
                   'patient is not a trial candidate',
            progress_every=500,
            dry_run=dry_run,
            manifest_target=manifest_target,
        )
        deceased_deleted = len(phase['deleted'])
        deceased_would_delete = len(phase['would_delete'])

        # The element each verdict came from, so the manifest can be audited
        # without the bundles -- which this run is about to delete.
        phase['evidence'] = {f.name: death_evidence[f.name]
                             for f in deceased_cancer_patients}
        _write_manifest(manifest, manifest_target)

        if dry_run:
            print(f"  [DRY RUN] Would delete "
                  f"{deceased_would_delete}/{len(deceased_cancer_patients)} "
                  f"deceased patients (nothing removed)")
        else:
            print(f"  Deleted {deceased_deleted}/{len(deceased_cancer_patients)} deceased patients... \nDONE")
        if phase['failed'] or phase['already_absent']:
            print(f"  NOT deleted: {len(phase['failed'])} failed, "
                  f"{len(phase['already_absent'])} already absent (see manifest)")
        print()
    else:
        # Record the phase even with nothing to delete. An ABSENT phase key and
        # a phase key with planned_count 0 are different claims: the first is
        # what a File 05 that has no deceased phase at all leaves behind, and
        # that is exactly what the previous version of this file wrote. "Zero
        # deceased patients in the cohort" is an acceptance criterion, and the
        # manifest is the only record of it, so it says so explicitly.
        manifest['phases']['deceased'] = {
            'reason':         'Patient.deceased[x] indicates the patient is dead; a '
                              'deceased patient is not a trial candidate',
            'status':         'planned' if dry_run else 'complete',
            'dry_run':        dry_run,
            'planned_count':  0,
            'planned':        [],
            'deleted':        [],
            'would_delete':   [],
            'already_absent': [],
            'failed':         [],
            'note':           'the vital-status check RAN and found no deceased '
                              'cancer patients; it was not skipped',
        }
        _write_manifest(manifest, manifest_target)
        print("="*80)
        print("STEP 2: NO DECEASED CANCER PATIENTS TO DELETE")
        print("="*80)
        print("  (checked; recorded in the manifest as a zero-count phase)")
        print()

    # STEP 3: Cap at CAP patients (if needed)
    #
    # The sampling pool is the ALIVE confirmed-cancer list, not a re-glob of the
    # directory: a re-glob also picks up the bundles that failed to parse and
    # the deceased bundles whose deletion may have failed, and can hand either
    # a slot in the cohort. Sorted for deterministic input ordering before the
    # seeded random.sample.
    remaining_files = sorted(alive_cancer_patients)
    extra_deleted = 0
    extra_would_delete = 0

    if error_patients:
        print(f"NOTE: {len(error_patients)} unparseable bundle(s) left on disk and "
              f"excluded from the cap pool (listed in the manifest).")
        print()

    if len(remaining_files) > CAP:
        print("="*80)
        print(f"STEP 3: CAP DATASET AT {CAP} PATIENTS")
        print("="*80)
        print()
        print(f"Current alive cancer patients: {len(remaining_files)}")
        print(f"Target: {CAP} patients")
        print(f"Need to remove: {len(remaining_files) - CAP} patients")
        print()

        # Reproducible random sampling. Local Random instance rather than
        # random.seed(): seeding the process-wide state would shift the draw
        # of every other consumer of `random` in the same session.
        rng = random.Random(RANDOM_SEED)

        # Randomly select CAP to KEEP
        patients_to_keep = rng.sample(remaining_files, CAP)
        patients_to_keep_set = set(patients_to_keep)

        # Delete the rest
        patients_to_remove = [f for f in remaining_files if f not in patients_to_keep_set]

        print(f"Randomly selecting {CAP} patients to keep (seed={RANDOM_SEED})...")
        print(f"{'Would delete' if dry_run else 'Deleting'} "
              f"{len(patients_to_remove)} extra cancer patients...")
        print()

        phase = _delete_manifested(
            manifest,
            phase_key='over_cap',
            files=patients_to_remove,
            reason=f'alive cancer patient beyond CAP={CAP} (seed={RANDOM_SEED})',
            progress_every=100,
            dry_run=dry_run,
            manifest_target=manifest_target,
        )
        extra_deleted = len(phase['deleted'])
        extra_would_delete = len(phase['would_delete'])

        if dry_run:
            print(f"  [DRY RUN] Would delete "
                  f"{extra_would_delete}/{len(patients_to_remove)} extra "
                  f"patients (nothing removed)")
        else:
            print(f"  Deleted {extra_deleted}/{len(patients_to_remove)} extra patients... \n\nDONE")
        if phase['failed'] or phase['already_absent']:
            print(f"  NOT deleted: {len(phase['failed'])} failed, "
                  f"{len(phase['already_absent'])} already absent (see manifest)")
        print()
    else:
        # Under-cap is a REPORTED outcome, not a quiet one: it means
        # POPULATION_SIZE (04- FHIR Generate Data.py) was sized too low for the
        # alive-cancer yield, and the corpus is smaller than CAP asked for.
        print("="*80)
        print(f"STEP 3: ALREADY AT OR BELOW {CAP} PATIENTS")
        print("="*80)
        print()
        if len(remaining_files) < CAP:
            print(f"! Only {len(remaining_files)} alive cancer patients available, "
                  f"CAP is {CAP}. The cohort is {CAP - len(remaining_files)} short.")
            print("  Raise POPULATION_SIZE in '04- FHIR Generate Data.py' and "
                  "regenerate; this run cannot be topped up (Synthea appends).")
            print()

    # FINAL STATS
    final_files = list(patients_path.glob("*.json"))
    deletion_failures = _DELETION_COUNTS["failed"]

    if dry_run:
        # "planned", never "complete". Anything reading this file later must be
        # able to tell a plan from a record of work done, and the word is the
        # only thing that says which.
        manifest['status'] = 'planned'
    else:
        manifest['status'] = 'complete' if not deletion_failures else 'partial'
    manifest['finished_utc'] = datetime.now(timezone.utc).isoformat()
    manifest['counts'] = dict(_DELETION_COUNTS)
    manifest['files_remaining'] = len(final_files)
    _write_manifest(manifest, manifest_target)

    print("="*80)
    if dry_run:
        print("DRY RUN COMPLETE — NOTHING WAS DELETED")
    else:
        print("FILTERING COMPLETE!" if not deletion_failures else "FILTERING FINISHED WITH FAILURES")
    print("="*80)
    print()
    if dry_run:
        _would_total = (non_cancer_would_delete + deceased_would_delete
                        + extra_would_delete)
        print(f"• Non-cancer patients that WOULD be deleted: {non_cancer_would_delete}")
        print(f"• Deceased cancer patients that WOULD be deleted: {deceased_would_delete}")
        print(f"• Extra alive cancer patients that WOULD be deleted (cap): {extra_would_delete}")
        print(f"• TOTAL that would be removed: {_would_total}")
        print(f"• Files currently in directory: {len(final_files)} → "
              f"{len(final_files) - _would_total} after a real run")
        print(f"• Directory: {patients_path}  (UNCHANGED)")
        print(f"• Plan written to: {manifest_target}")
        print(f"• Every filename is in that file, under phases[*].would_delete.")
        print()
        print("  Re-run without dry_run=True to perform the deletion.")
    else:
        print(f"✓ Non-cancer patients deleted: {non_cancer_deleted}")
        print(f"✓ Deceased cancer patients deleted: {deceased_deleted}")
        print(f"✓ Extra alive cancer patients deleted (for cap): {extra_deleted}")
        print(f"✓ Files remaining in directory: {len(final_files)}")
        print(f"✓ Directory: {patients_path}")
        print(f"✓ Manifest: {manifest_path()} (status: {manifest['status']})")
    if deletion_failures:
        print(f"✗ Deletions that FAILED: {deletion_failures} — the directory is "
              f"partially filtered. Per-file errors are in the manifest.")
    if _DELETION_COUNTS["already_absent"]:
        print(f"! Targets already absent: {_DELETION_COUNTS['already_absent']}")
    if error_patients:
        print(f"! Unparseable bundles left on disk (not in cohort): {len(error_patients)}")
    print()

    stats = {
        # FIRST, and always present. A caller that forgets to look at it is one
        # thing; a caller that cannot tell a dry run's stats from a real run's
        # is another, and the three *_deleted keys below read 0 in both a clean
        # dry run and a real run that found nothing to do.
        'dry_run': dry_run,
        'would_delete': {
            'non_cancer': non_cancer_would_delete,
            'deceased':   deceased_would_delete,
            'over_cap':   extra_would_delete,
            'total':      (non_cancer_would_delete + deceased_would_delete
                           + extra_would_delete),
        },
        'total_scanned': len(patient_files),
        'cancer_patients_found': len(cancer_patients),
        'alive_cancer_patients_found': len(alive_cancer_patients),
        'deceased_cancer_patients_found': len(deceased_cancer_patients),
        'unknown_vital_status': len(unknown_vital_status),
        'final_cancer_patients': len(final_files) - len(error_patients),
        'files_remaining': len(final_files),
        'non_cancer_deleted': non_cancer_deleted,
        'deceased_deleted': deceased_deleted,
        'extra_deleted': extra_deleted,
        'percentage': len(cancer_patients)/len(patient_files)*100 if patient_files else 0,
        'alive_percentage': len(alive_cancer_patients)/len(patient_files)*100 if patient_files else 0,
        'cancer_types': cancer_counts,
        'directory': str(patients_path),
        'parse_errors': len(error_patients),
        'deletion_failures': deletion_failures,
        'deletions_already_absent': _DELETION_COUNTS['already_absent'],
        # The file that was actually written -- the .dryrun copy on a dry run.
        # 'manifest_path' keeps naming the real manifest, unchanged, because
        # existing callers read it as "where the record of deletions lives".
        'manifest_path': manifest_path(),
        'manifest_written': manifest_target,
        'manifest_status': manifest['status'],
    }

    return stats


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 11:22:14 2026

@author: ramyalsaffar
"""
