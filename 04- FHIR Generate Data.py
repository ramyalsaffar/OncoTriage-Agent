# Generate Synthea Patients with Cancer
#########################################

"""
Step 1: Generate synthetic cancer patients using Synthea
Calls Synthea JAR file via subprocess to generate FHIR patient data

Also writes and loads a custom Generic Module Framework module that records an
ECOG performance status for cancer patients. Synthea ships no such module, so
without it no bundle in the corpus carries a performance status and every
ECOG-gated trial criterion -- which is most interventional oncology criteria --
is unevaluable. See build_ecog_module() for the module and 03- Config.py for
the two uncalibrated holding values that shape it.

Every run writes a JSON run manifest next to the generated data recording the
command, the Synthea JAR hash, the ECOG module filename and content hash, the
configured score distribution and missingness fraction, and what was actually
observed in the output. That manifest is the artifact a regeneration needs.
"""


#------------------------------------------------------------------------------


# Run needed files
#-----------------
# Bootstrap comes FIRST because the configuration block below resolves
# SYNTHEA_JAR_PATH / SYNTHEA_MODULES_DIR / OUTPUT_DIR_FULL from data_patient_path,
# which 01- Imports.py defines. 03- Config.py supplies ECOG_SCORE_DISTRIBUTION
# and ECOG_MISSINGNESS_FRACTION.
_code_dir = "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code/"

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py", "03- Config.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())


#------------------------------------------------------------------------------


# Configuration
#--------------

# Population size to generate
# The generated population will mostly have healthy people and about 5~10% people with cancer
# Later, I will drop the datapoints of the healthy people and only keep the cancer patients.
POPULATION_SIZE = 22000

# Age range (adults only for cancer trials)
MIN_AGE = 18
MAX_AGE = 80

# Module filter (only cancer modules)
#
# Synthea's -m flag takes a File.pathSeparator-delimited list of glob patterns,
# matched case-insensitively against each module's key. Built-in modules are
# keyed by their path inside the JAR ("modules/lung_cancer.json"); modules
# supplied through -d are keyed by their ABSOLUTE path on disk. A pattern that
# matches nothing silently drops the module -- there is no error -- so the ECOG
# module needs a pattern of its own here. Verified empirically: with "*cancer*"
# alone, Synthea logs "Scanned 1 local modules" and then never lists the ECOG
# module among the "Loading module ..." lines, and no bundle carries an
# observation. build_module_filter_argument() joins the two, and
# generate_synthea_patients() fails the run if the load line does not appear.
MODULE_FILTER = "*cancer*"

# State for demographics
STATE = "California"

# Synthea JAR location (in patients folder)
SYNTHEA_JAR_PATH = data_patient_path + "synthea-with-dependencies.jar"

# Local module directory handed to Synthea with -d. Synthea scans it and makes
# every module found there available alongside the ones bundled in the JAR
# (subject to MODULE_FILTER above). Resolved from data_patient_path, the same
# way SYNTHEA_JAR_PATH is, so it moves with the data directory and no absolute
# path is written here.
SYNTHEA_MODULES_DIR = data_patient_path + "synthea_modules/"

# Filename of the generated ECOG module inside SYNTHEA_MODULES_DIR. The module
# is regenerated from build_ecog_module() on every run rather than being a
# checked-in asset, because its transition weights are derived from
# ECOG_SCORE_DISTRIBUTION and ECOG_MISSINGNESS_FRACTION in 03- Config.py; a
# hand-edited copy on disk would silently disagree with the config the manifest
# records.
ECOG_MODULE_FILENAME = "ecog_performance_status.json"

# Run manifest and raw Synthea log, both written into the run's output directory
RUN_MANIFEST_FILENAME = "generation_run_manifest.json"
SYNTHEA_LOG_FILENAME = "synthea_run.log"

# Output directory for generated FHIR files (temporary full population of healthy people and people with cancer)
#
# This is the LIVE corpus directory. generate_synthea_patients() takes
# output_dir as an argument so a scratch run can be pointed elsewhere without
# touching this constant.
OUTPUT_DIR_FULL = data_patient_path

# FHIR export settings
EXPORT_FHIR = "true"
EXPORT_CCDA = "false"
EXPORT_CSV  = "true"

# Limits patient history to last X years
# 0 years mean lifetime records
YEARS = 0


# ECOG performance status -- external standard facts
#---------------------------------------------------
# LOINC codes. These are facts about LOINC, not tunables, so they live here as
# named constants rather than in 03- Config.py.
#
# 89247-1 is the SCORE, and is the one mCODE's ECOGPerformanceStatus profile
# fixes Observation.code to. The other two are its siblings in LOINC and are
# named here only so that nobody "corrects" the score code to one of them:
#   89246-3  ECOG Performance Status panel  -- a panel, not a scored result
#   89262-0  ECOG Performance Status interpretation -- the text label
#            ("Fully active..."), a CodeableConcept, not an integer
ECOG_LOINC_SCORE_CODE = "89247-1"
ECOG_LOINC_SCORE_DISPLAY = "ECOG Performance Status score"
ECOG_LOINC_PANEL_CODE = "89246-3"
ECOG_LOINC_INTERPRETATION_CODE = "89262-0"

# mCODE requires category = survey (NOT laboratory: an ECOG score is a clinician
# assessment, not a specimen result). Synthea's Observation state takes the bare
# category token and the FHIR R4 exporter expands it to the
# http://terminology.hl7.org/CodeSystem/observation-category system.
ECOG_OBSERVATION_CATEGORY = "survey"

# UCUM annotation-only unit. Synthea REFUSES to load a module whose Observation
# state carries a numeric value with a blank unit ("Observations with numeric
# quantities must contain non-blank units"), so a unit has to be supplied even
# though an ECOG score is dimensionless. "{score}" is the UCUM annotation form
# for exactly this case. The unit does not survive into the final bundle --
# normalize_ecog_observations() replaces the whole valueQuantity with the
# valueInteger mCODE requires.
ECOG_SCORE_UNIT = "{score}"

# mCODE ECOGPerformanceStatus profile. Stamped onto meta.profile by
# normalize_ecog_observations(). See that function for the one element-level
# gap this claim does not cover.
MCODE_ECOG_PROFILE_URL = (
    "http://hl7.org/fhir/us/mcode/StructureDefinition/mcode-ecog-performance-status"
)

# SNOMED CT codes that Synthea's oncology modules leave ACTIVE on a patient who
# currently has cancer. The Guard state at the top of the ECOG module tests
# these with the "Active Condition" condition type, which is an OR across the
# list against HealthRecord.present -- i.e. diagnosed and not yet abated.
#
# Extracted from the module JSONs inside the Synthea JAR (lung_cancer,
# veteran_lung_cancer, breast_cancer, colorectal_cancer, veteran_prostate_cancer,
# acute_myeloid_leukemia), so the set is exactly what this JAR can produce, not
# a general oncology vocabulary. acute_myeloid_leukemia is included even though
# MODULE_FILTER does not currently load it, so that widening the filter does not
# silently leave AML patients without a performance status.
#
# Deliberately EXCLUDED, and the exclusions matter:
#   162573006 Suspected lung cancer (situation)    -- suspicion, not diagnosis
#   315268008 Suspected prostate cancer (situation) -- same
#   68496003  Polyp of colon / 713197008 Recurrent rectal polyp -- not malignant
#   92691004  Carcinoma in situ of prostate -- pre-invasive. 08- Cancer Code
#             Registry treats in-situ disease as non-cancer for cohort
#             selection, and the guard has to agree with it or the corpus
#             carries performance statuses for patients the pipeline does not
#             consider cancer patients.
#
# Deliberately INCLUDED: 94260004 (metastatic to colon, Synthea's stage IV
# colorectal code) and 94503003 (metastatic to prostate). 08- Cancer Code
# Registry rejects these as PRIMARY-cancer selections, which answers a different
# question -- "which is the index cancer" -- from the one the guard asks, which
# is "does this person have cancer right now". They do.
ECOG_GUARD_CANCER_CODES = (
    # Lung -- lung_cancer.json, veteran_lung_cancer.json
    ("254637007", "Non-small cell lung cancer (disorder)"),
    ("254632001", "Small cell carcinoma of lung (disorder)"),
    ("424132000", "Non-small cell carcinoma of lung, TNM stage 1 (disorder)"),
    ("425048006", "Non-small cell carcinoma of lung, TNM stage 2 (disorder)"),
    ("422968005", "Non-small cell carcinoma of lung, TNM stage 3 (disorder)"),
    ("423121009", "Non-small cell carcinoma of lung, TNM stage 4 (disorder)"),
    ("67811000119102", "Primary small cell malignant neoplasm of lung, TNM stage 1 (disorder)"),
    ("67821000119109", "Primary small cell malignant neoplasm of lung, TNM stage 2 (disorder)"),
    ("67831000119107", "Primary small cell malignant neoplasm of lung, TNM stage 3 (disorder)"),
    ("67841000119103", "Primary small cell malignant neoplasm of lung, TNM stage 4 (disorder)"),
    # Breast -- breast_cancer.json
    ("254837009", "Malignant neoplasm of breast (disorder)"),
    # Colorectal -- colorectal_cancer.json (one code per stage, earlier stages abated)
    ("93761005", "Primary malignant neoplasm of colon (disorder)"),
    ("109838007", "Overlapping malignant neoplasm of colon (disorder)"),
    ("363406005", "Malignant neoplasm of colon (disorder)"),
    ("94260004", "Metastatic malignant neoplasm to colon (disorder)"),
    # Prostate -- veteran_prostate_cancer.json
    ("126906006", "Neoplasm of prostate (disorder)"),
    ("94503003", "Metastatic malignant neoplasm to prostate (disorder)"),
    # Haematologic -- acute_myeloid_leukemia.json
    ("91861009", "Acute myeloid leukemia (disorder)"),
)


#------------------------------------------------------------------------------


# ECOG Performance Status Module
#--------------------------------

def _validate_ecog_config():
    """Fail before writing the module if the configured distribution is unusable.

    Synthea normalises distributed_transition weights silently, so a set that
    does not sum to 1.0 produces a distribution nobody chose and nothing says
    so. Grade 5 means "dead"; a dead patient is not a trial candidate, and the
    only thing keeping 5 out of the corpus is its absence from
    ECOG_SCORE_DISTRIBUTION, so that absence is checked rather than assumed.

    Raises:
        ValueError: on any malformed score, an out-of-range grade, grade 5, a
                    negative weight, a weight sum away from 1.0, or a
                    missingness fraction outside [0, 1).
    """
    if not ECOG_SCORE_DISTRIBUTION:
        raise ValueError("ECOG_SCORE_DISTRIBUTION (03- Config.py) is empty")

    for score, weight in ECOG_SCORE_DISTRIBUTION.items():
        if not isinstance(score, int) or isinstance(score, bool):
            raise ValueError(
                f"ECOG_SCORE_DISTRIBUTION key {score!r} is not an integer grade"
            )
        if score == 5:
            raise ValueError(
                "ECOG_SCORE_DISTRIBUTION contains grade 5 (dead). A dead patient "
                "is not a trial candidate and 5 must never be emitted."
            )
        if not 0 <= score <= 4:
            raise ValueError(
                f"ECOG_SCORE_DISTRIBUTION grade {score} is outside the emittable range 0-4"
            )
        if not isinstance(weight, (int, float)) or weight < 0:
            raise ValueError(
                f"ECOG_SCORE_DISTRIBUTION weight for grade {score} is {weight!r}; "
                "weights must be non-negative numbers"
            )

    total = sum(ECOG_SCORE_DISTRIBUTION.values())
    if abs(total - 1.0) > 1e-9:
        raise ValueError(
            f"ECOG_SCORE_DISTRIBUTION weights sum to {total!r}, not 1.0. Synthea "
            "renormalises silently, so this would produce a distribution that "
            "matches neither the config nor the manifest."
        )

    if not isinstance(ECOG_MISSINGNESS_FRACTION, (int, float)):
        raise ValueError(
            f"ECOG_MISSINGNESS_FRACTION is {ECOG_MISSINGNESS_FRACTION!r}, not a number"
        )
    if not 0.0 <= ECOG_MISSINGNESS_FRACTION < 1.0:
        raise ValueError(
            f"ECOG_MISSINGNESS_FRACTION is {ECOG_MISSINGNESS_FRACTION!r}; it must be "
            "in [0, 1). At 1.0 no patient would ever carry an observation."
        )


def _ecog_state_name(score):
    """State name for one exact score. Kept in one place: the distributed
    transition and the observation states have to agree."""
    return f"Record_ECOG_{score}"


def build_ecog_module():
    """Build the ECOG performance status Generic Module Framework module.

    Shape:

        Initial
          -> Wait_For_Active_Cancer          Guard, Active Condition
          -> Documentation_Draw              distributed_transition (missingness)
               |-> No_Documented_ECOG        Terminal, no observation at all
               `-> Await_Post_Diagnosis_Encounter   Encounter, wellness
                     -> Score_Draw           distributed_transition (score)
                          -> Record_ECOG_0..4  one Observation each, exact value
                               -> ECOG_Recorded  Terminal

    Design decisions worth naming, all of them consequential:

    GUARD MECHANISM -- "Active Condition" against ECOG_GUARD_CANCER_CODES, not
    "Attribute". Top-level modules run for every patient from birth, so an
    unguarded module would give newborns an ECOG. The Attribute alternative was
    rejected on evidence: Synthea's oncology modules set no common cancer flag.
    lung_cancer sets "lung_cancer" and veteran_prostate_cancer sets
    "prostate_cancer", but breast_cancer sets only downstream attributes
    ("breast_cancer_survival", "breast_cancer_triple_negative", ...) and
    colorectal_cancer only "colorectal_cancer_stage" -- there is no attribute
    that means "this patient has cancer". Guarding on attributes would mean
    tracking six module-private names that upstream is free to rename, and it
    would leave breast and colorectal patients with no reliable trigger. The
    condition codes are the diagnosis itself. No new attribute is introduced, so
    the Synthea rule that attribute names must be unique across all modules is
    not engaged at all.

    ONE OBSERVATION PER PATIENT -- the module terminates after recording. Real
    records carry a performance status per oncology visit and it worsens with
    progression; this corpus carries a single value fixed at the first encounter
    after diagnosis. That understates decline in patients who progress. Accepted
    because independently re-drawing the score at each later visit would be a
    random walk, which is a worse lie than a stale value, and because trial
    matching reads one performance status.

    ENCOUNTER -- an Encounter state with wellness: true, which waits for the next
    wellness encounter the encounter module produces rather than fabricating a
    visit. That satisfies "attach the observation to an encounter at or after
    diagnosis" without inventing utilization the rest of the corpus would then
    have to account for. Cost: patients who die, or who reach the end of the
    simulation, before that next encounter get no observation, so observed
    missingness always exceeds ECOG_MISSINGNESS_FRACTION. Both numbers go into
    the run manifest.

    Returns:
        dict: the module, ready to be json.dump()ed into SYNTHEA_MODULES_DIR.
    """
    _validate_ecog_config()

    scores = sorted(ECOG_SCORE_DISTRIBUTION)

    states = {
        "Initial": {
            "type": "Initial",
            "direct_transition": "Wait_For_Active_Cancer",
        },

        "Wait_For_Active_Cancer": {
            "type": "Guard",
            "remarks": [
                "MANDATORY, not a refinement. A top-level module runs for every "
                "patient in the simulation from birth, so without this guard "
                "every newborn in the population would carry an ECOG score.",
                "Active Condition is an OR across the code list, tested against "
                "the conditions currently present on the record. It passes on "
                "the first time step after a cancer diagnosis and stays passed "
                "while the diagnosis is active.",
            ],
            "allow": {
                "condition_type": "Active Condition",
                "codes": [
                    {"system": "SNOMED-CT", "code": code, "display": display}
                    for code, display in ECOG_GUARD_CANCER_CODES
                ],
            },
            "direct_transition": "Documentation_Draw",
        },

        "Documentation_Draw": {
            "type": "Simple",
            "remarks": [
                "Missingness is drawn once, before the wait for an encounter, so "
                "the patients routed to No_Documented_ECOG never enter the "
                "encounter wait at all.",
                "These patients carry NO observation. They are not given a "
                "default score, because a defaulted 0 is indistinguishable from "
                "a measured 0 downstream.",
            ],
            "distributed_transition": [
                {
                    "transition": "Await_Post_Diagnosis_Encounter",
                    "distribution": round(1.0 - ECOG_MISSINGNESS_FRACTION, 6),
                },
                {
                    "transition": "No_Documented_ECOG",
                    "distribution": round(ECOG_MISSINGNESS_FRACTION, 6),
                },
            ],
        },

        "No_Documented_ECOG": {
            "type": "Terminal",
            "remarks": [
                "Deliberate undocumented performance status. No Observation "
                "resource is produced for this patient.",
            ],
        },

        "Await_Post_Diagnosis_Encounter": {
            "type": "Encounter",
            "wellness": True,
            "remarks": [
                "wellness: true attaches the observation to the next wellness "
                "encounter produced by Synthea's encounter module, rather than "
                "creating a visit of our own. The observation therefore lands at "
                "or after diagnosis and inside a real encounter.",
                "A patient who dies or reaches the end of the simulation before "
                "that encounter is never scored, which is why observed "
                "missingness is always above the configured fraction.",
            ],
            "direct_transition": "Score_Draw",
        },

        "Score_Draw": {
            "type": "Simple",
            "remarks": [
                "One branch per exact integer score. Synthea's Observation state "
                "can carry an exact value or a uniformly-sampled range, and a "
                "uniform range over 0-4 would be wrong: the distribution of "
                "performance status among patients reaching trial screening is "
                "not flat. The shape lives in ECOG_SCORE_DISTRIBUTION "
                "(03- Config.py) and is an uncalibrated holding value.",
            ],
            "distributed_transition": [
                {
                    "transition": _ecog_state_name(score),
                    "distribution": round(ECOG_SCORE_DISTRIBUTION[score], 6),
                }
                for score in scores
            ],
        },

        "ECOG_Recorded": {"type": "Terminal"},
    }

    for score in scores:
        states[_ecog_state_name(score)] = {
            "type": "Observation",
            "category": ECOG_OBSERVATION_CATEGORY,
            "unit": ECOG_SCORE_UNIT,
            "codes": [
                {
                    "system": "LOINC",
                    "code": ECOG_LOINC_SCORE_CODE,
                    "display": ECOG_LOINC_SCORE_DISPLAY,
                }
            ],
            "exact": {"quantity": score},
            "direct_transition": "ECOG_Recorded",
        }

    return {
        "name": "ECOG Performance Status",
        "remarks": [
            "ECOG PERFORMANCE STATUS FOR ONCOLOGY TRIAL MATCHING",
            "",
            "Generated by '04- FHIR Generate Data.py' (build_ecog_module). Do not "
            "hand-edit this file: it is rewritten from ECOG_SCORE_DISTRIBUTION and "
            "ECOG_MISSINGNESS_FRACTION in '03- Config.py' on every generation run, "
            "and its sha256 is recorded in the run manifest.",
            "",
            "WHY THIS MODULE EXISTS",
            "Synthea generates no performance status. Nearly every interventional "
            "oncology trial gates on ECOG, usually 0-1 or 0-2, so without this "
            "module that entire class of eligibility criterion is unevaluable for "
            "every patient in the corpus.",
            "",
            "SCORE DISTRIBUTION -- UNCALIBRATED HOLDING VALUE",
            "Weights: " + ", ".join(
                f"ECOG {score} = {ECOG_SCORE_DISTRIBUTION[score]}" for score in scores
            ) + ".",
            "Rationale: the modelled population is patients reaching trial "
            "screening, who are selected for function, so the distribution skews "
            "toward 0 and 1. A flat 0-4 -- which is what Synthea's uniform range "
            "sampling would give -- would place 60% of the cohort at ECOG >= 2 and "
            "understate eligibility badly. The shape is plausible; it is NOT "
            "calibrated against a registry, a screening log, or any published "
            "cohort. Treat every eligibility rate computed from this corpus as "
            "conditional on it.",
            "Grade 5 (dead) is never emitted: it is a valid ECOG grade but a dead "
            "patient is not a trial candidate.",
            "",
            "MISSINGNESS FRACTION -- UNCALIBRATED HOLDING VALUE",
            f"Configured: {ECOG_MISSINGNESS_FRACTION} of cancer patients carry no "
            "ECOG observation at all.",
            "Rationale: real oncology records frequently lack a documented "
            "performance status. A corpus where every patient has one would let "
            "the pipeline evaluate an ECOG criterion for 100% of patients, an "
            "accuracy the source data does not have, and would remove 'criterion "
            "not evaluable' as an outcome. The value is a plausible order of "
            "magnitude, not a measurement.",
            "Observed missingness in a generated corpus is always HIGHER than the "
            "configured fraction, because a patient who dies or reaches the end of "
            "the simulation before the next encounter after diagnosis is also "
            "never scored. The run manifest records both numbers.",
            "",
            "CONFORMANCE",
            f"Observation.code = LOINC {ECOG_LOINC_SCORE_CODE} "
            f"({ECOG_LOINC_SCORE_DISPLAY}); category = "
            f"{ECOG_OBSERVATION_CATEGORY}; one exact integer value per state. "
            "Synthea's FHIR R4 exporter maps every numeric observation value to "
            "valueQuantity, so '04- FHIR Generate Data.py' rewrites these to the "
            "valueInteger that mCODE's ECOGPerformanceStatus profile requires "
            "after export (normalize_ecog_observations).",
        ],
        "states": states,
    }


def write_ecog_module(modules_dir=None):
    """Write the ECOG module into the local modules directory Synthea scans.

    Rewritten on every run rather than left alone if present: the module's
    transition weights are derived from 03- Config.py, so a stale file on disk
    would silently disagree with the configuration the manifest records. Which
    of the three paths was taken is printed.

    Args:
        modules_dir: Target directory. Defaults to SYNTHEA_MODULES_DIR.

    Returns:
        dict: filename, path, sha256, bytes, and status
              ("created" | "updated" | "unchanged").
    """
    modules_dir = modules_dir or SYNTHEA_MODULES_DIR
    Path(modules_dir).mkdir(parents=True, exist_ok=True)

    module_path = Path(modules_dir) / ECOG_MODULE_FILENAME
    payload = json.dumps(build_ecog_module(), indent=2).encode("utf-8")

    if not module_path.exists():
        status = "created"
    elif module_path.read_bytes() != payload:
        status = "updated"
    else:
        status = "unchanged"

    if status != "unchanged":
        module_path.write_bytes(payload)

    digest = hashlib.sha256(payload).hexdigest()

    print(f"✓ ECOG module {status}: {module_path.name}")
    print(f"  Directory: {modules_dir}")
    print(f"  sha256:    {digest}")
    print("  Scores:    " + ", ".join(
        f"{s}={ECOG_SCORE_DISTRIBUTION[s]}" for s in sorted(ECOG_SCORE_DISTRIBUTION)
    ))
    print(f"  Missing:   {ECOG_MISSINGNESS_FRACTION} (configured, uncalibrated)")

    return {
        "filename": ECOG_MODULE_FILENAME,
        "path": str(module_path),
        "sha256": digest,
        "bytes": len(payload),
        "status": status,
    }


def build_module_filter_argument():
    """Join MODULE_FILTER with a pattern that matches the ECOG module.

    Synthea splits -m on File.pathSeparator, which is os.pathsep on the same
    platform. The ECOG pattern is derived from ECOG_MODULE_FILENAME so that
    renaming the module cannot leave the filter pointing at nothing.
    """
    ecog_pattern = "*" + Path(ECOG_MODULE_FILENAME).stem + "*"
    return os.pathsep.join([MODULE_FILTER, ecog_pattern])


#------------------------------------------------------------------------------


# Main Generation Function
#--------------------------
def generate_synthea_patients(population_size=None, output_dir=None,
                              modules_dir=None, seed=None, force=False):
    """
    Generate synthetic patients using Synthea via subprocess

    This generates a full population with cancer modules plus the local ECOG
    performance status module.
    About 7-10% will have actual cancer diagnoses.

    The full population is saved to a temporary directory for filtering.

    Args:
        population_size: Patients to generate. Defaults to POPULATION_SIZE.
        output_dir:      Synthea --exporter.baseDirectory. Defaults to
                         OUTPUT_DIR_FULL, the live corpus directory. Pass a
                         scratch directory to generate without touching it.
        modules_dir:     Local module directory for -d. Defaults to
                         SYNTHEA_MODULES_DIR.
        seed:            Synthea -s population seed, or None for Synthea's own.
        force:           Generate even when output_dir/fhir already holds
                         bundles. Off by default -- see below.

    Returns:
        dict: success flag, the command, elapsed seconds, the log path, the
              modules Synthea reported loading, and ecog_module_loaded. Callers
              that only want a boolean can read result["success"].
    """
    population_size = POPULATION_SIZE if population_size is None else population_size
    output_dir = output_dir or OUTPUT_DIR_FULL
    modules_dir = modules_dir or SYNTHEA_MODULES_DIR

    print("="*80)
    print("STEP 1: GENERATE SYNTHEA PATIENTS")
    print("="*80)
    print()

    outcome = {
        "success": False,
        "command": None,
        "module_filter": None,
        "returncode": None,
        "elapsed_seconds": None,
        "log_path": None,
        "loaded_modules": [],
        "ecog_module_loaded": False,
        "failure_reason": None,
    }

    # Synthea ADDS to its output directory, it does not replace it. Generating
    # into a directory that already holds a cohort therefore silently interleaves
    # two populations -- generated under different parameters, possibly a
    # different module set -- into one corpus that nothing downstream can tell
    # apart, and '05- FHIR Clean Data.py' deletes in place from whatever it
    # finds there. OUTPUT_DIR_FULL is the LIVE corpus directory and is this
    # function's default, so the check is on by default and has to be overridden
    # deliberately.
    existing_fhir = Path(output_dir) / "fhir"
    if not force and existing_fhir.is_dir() and any(existing_fhir.glob("*.json")):
        existing_count = len(list(existing_fhir.glob("*.json")))
        print("="*80)
        print("REFUSING TO GENERATE: output directory already holds a corpus")
        print("="*80)
        print(f"  {existing_fhir}")
        print(f"  contains {existing_count} JSON bundles.")
        print()
        print("Synthea appends to this directory rather than replacing it, so this")
        print("run would interleave two populations into one indistinguishable corpus.")
        print("Pass a different --output-dir for a scratch run, or --force to add to")
        print("this one on purpose.")
        outcome["failure_reason"] = "output_dir_not_empty"
        return outcome

    # Check if Synthea JAR exists
    if not os.path.exists(SYNTHEA_JAR_PATH):
        print(f"ERROR: Synthea JAR not found at: {SYNTHEA_JAR_PATH}")
        print("Please download synthea-with-dependencies.jar and place it in:")
        print(f"  {data_patient_path}")
        print()
        print("Download from: https://github.com/synthetichealth/synthea/releases")
        outcome["failure_reason"] = "synthea_jar_missing"
        return outcome

    print(f"✓ Found Synthea JAR: {SYNTHEA_JAR_PATH}")

    if not os.path.isdir(modules_dir):
        print(f"ERROR: Local module directory not found: {modules_dir}")
        print("Run write_ecog_module() before generating.")
        outcome["failure_reason"] = "modules_dir_missing"
        return outcome

    print(f"✓ Local modules directory: {modules_dir}")

    # Create output directory if it doesn't exist
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    print(f"✓ Output directory: {output_dir}")

    module_filter_arg = build_module_filter_argument()

    # Build Synthea command
    command = [
        "java",
        "-jar",
        SYNTHEA_JAR_PATH,
        "-p", str(population_size),
        "-a", f"{MIN_AGE}-{MAX_AGE}",
        "-m", module_filter_arg,
        "-d", modules_dir,
        f"--exporter.fhir.export={EXPORT_FHIR}",
        f"--exporter.ccda.export={EXPORT_CCDA}",
        f"--exporter.csv.export={EXPORT_CSV}",
        f"--exporter.baseDirectory={output_dir}",
        f"--exporter.years_of_history={YEARS}",
        STATE
    ]
    if seed is not None:
        command[3:3] = ["-s", str(seed)]

    outcome["command"] = list(command)
    outcome["module_filter"] = module_filter_arg

    print()
    print("="*80)
    print("SYNTHEA COMMAND")
    print("="*80)
    print(" ".join(command))
    print()
    print("="*80)
    print("GENERATING PATIENTS...")
    print("="*80)
    print()
    print(f"Population size: {population_size}")
    print(f"Age range: {MIN_AGE}-{MAX_AGE} years")
    print(f"Module filter: {module_filter_arg}")
    print(f"Local modules: {modules_dir}")
    print(f"Seed: {seed if seed is not None else '(Synthea default)'}")
    print(f"State: {STATE}")
    print()
    print("This will take few moments depending on population size...")
    print("You'll see 'Loading modules...' messages from Synthea")
    print()

    # Execute Synthea
    start_time = time.time()
    log_lines = []

    try:
        # Run Synthea with live progress filtering
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=data_patient_path,
            bufsize=1
        )

        # Filter output - only show progress every 100 patients.
        # Every line is retained regardless: the module-load check below and the
        # on-disk log both read from it, and a Synthea stack trace that is
        # filtered out of the console is the exact thing this run must not lose.
        patient_count = 0
        for line in process.stdout:
            log_lines.append(line.rstrip("\n"))
            # Check if it's a patient generation line (contains patient name and location)
            if ' -- ' in line and '(' in line and 'y/o' in line:
                patient_count += 1
                if patient_count % 100 == 0:
                    print(f"  Generated {patient_count} patients...")
            # Show module loading and important messages
            elif 'Loading' in line or 'Running with options' in line or 'Loaded' in line:
                print(line.strip())

        # Wait for completion
        process.wait()
        result = process

        elapsed_time = time.time() - start_time
        outcome["returncode"] = result.returncode
        outcome["elapsed_seconds"] = round(elapsed_time, 2)

        # Persist the raw log before any early return, so a failed run is still
        # diagnosable from disk.
        log_path = Path(output_dir) / SYNTHEA_LOG_FILENAME
        log_path.write_text("\n".join(log_lines), encoding="utf-8")
        outcome["log_path"] = str(log_path)
        print()
        print(f"✓ Synthea log: {log_path}")

        outcome["loaded_modules"] = [
            line.split("Loading module", 1)[1].strip()
            for line in log_lines if "Loading module" in line
        ]
        outcome["ecog_module_loaded"] = any(
            ECOG_MODULE_FILENAME in module for module in outcome["loaded_modules"]
        )

        # Check if successful
        if result.returncode != 0:
            print()
            print("="*80)
            print("ERROR: Synthea generation failed!")
            print("="*80)
            print(f"Return code: {result.returncode}")
            print("Last 20 log lines:")
            for line in log_lines[-20:]:
                print(f"  {line}")
            outcome["failure_reason"] = f"synthea_returncode_{result.returncode}"
            return outcome

        # A module that does not match the -m filter is dropped SILENTLY -- no
        # warning, no non-zero exit, just a corpus with no ECOG in it. Fail here
        # instead of discovering it three steps downstream.
        if not outcome["ecog_module_loaded"]:
            print()
            print("="*80)
            print("ERROR: The ECOG module was not loaded by Synthea")
            print("="*80)
            print(f"Expected a 'Loading module ...{ECOG_MODULE_FILENAME}' line; none present.")
            print(f"Module filter passed to -m: {module_filter_arg}")
            print(f"Local module directory passed to -d: {modules_dir}")
            print("Modules Synthea reported loading:")
            for module in outcome["loaded_modules"]:
                print(f"  {module}")
            print()
            print("Synthea matches -m patterns against each module's key, and a")
            print("local module's key is its ABSOLUTE path. Check that the ECOG")
            print("pattern in build_module_filter_argument() matches that path.")
            outcome["failure_reason"] = "ecog_module_not_loaded"
            return outcome

        print()
        print("="*80)
        print("GENERATION COMPLETE!")
        print("="*80)
        print(f"Time elapsed: {elapsed_time/60:.1f} minutes")
        print(f"✓ ECOG module loaded by Synthea")

        # Check output directory
        fhir_dir = Path(output_dir) / "fhir"
        if fhir_dir.exists():
            patient_files = list(fhir_dir.glob("*.json"))
            print(f"✓ Generated {len(patient_files)} patient FHIR files")
            print(f"✓ Location: {fhir_dir}")
        else:
            print(f"⚠ Warning: FHIR directory not found at {fhir_dir}")
            print("Synthea may have used a different output structure")

        print()
        print("Next step: Run filter script to extract cancer patients only")
        print()

        outcome["success"] = True
        return outcome

    except FileNotFoundError:
        print()
        print("="*80)
        print("ERROR: Java not found!")
        print("="*80)
        print("Please install Java JDK 11 or newer:")
        print("  macOS: brew install openjdk@11")
        print("  Or download from: https://adoptium.net/")
        print()
        outcome["failure_reason"] = "java_not_found"
        return outcome

    except Exception as e:
        print()
        print("="*80)
        print("ERROR: Unexpected error during generation")
        print("="*80)
        print(f"Error: {e}")
        print()
        outcome["failure_reason"] = f"unexpected_error: {type(e).__name__}: {e}"
        return outcome


#------------------------------------------------------------------------------


# mCODE Normalization
#---------------------

def _is_ecog_observation(resource):
    """True if this resource is the ECOG score observation."""
    if resource.get("resourceType") != "Observation":
        return False
    for coding in resource.get("code", {}).get("coding", []):
        if coding.get("code") == ECOG_LOINC_SCORE_CODE:
            return True
    return False


def normalize_ecog_observations(output_dir=None):
    """Rewrite exported ECOG observations into the form mCODE requires.

    Synthea cannot emit this shape itself. Its FHIR R4 exporter maps EVERY
    numeric observation value through mapValueToFHIRType(), which turns any
    java.lang.Number into a Quantity -- there is no integer path -- and the
    Observation state validator refuses to load a module whose numeric value has
    a blank unit. So the module is forced to produce
    valueQuantity{value, unit "{score}"}, and mCODE's ECOGPerformanceStatus
    profile requires value[x] to be an integer. This pass closes that gap on the
    exported bundles:

      - valueQuantity -> valueInteger (the unit is dropped; an ECOG grade is
        dimensionless and "{score}" only ever existed to satisfy the loader)
      - dataAbsentReason removed -- the profile prohibits it. Synthea does not
        emit one here; it is stripped and COUNTED rather than assumed absent.
      - meta.profile gains MCODE_ECOG_PROFILE_URL alongside the US Core profile
        Synthea already stamps.

    Scope of the mCODE claim: the Observation satisfies the profile's
    element-level constraints (fixed code, survey category, integer value in
    range, effectiveDateTime, no dataAbsentReason, subject and encounter
    present). It does NOT make the bundle fully IG-valid: mCODE types
    Observation.subject as Reference(CancerPatient), and Synthea's Patient
    resource does not carry the mcode-cancer-patient profile. A strict
    whole-IG validation will still flag that reference target.

    Idempotent: an already-normalized file is left byte-identical.

    Args:
        output_dir: Run output directory (the one holding fhir/). Defaults to
                    OUTPUT_DIR_FULL.

    Returns:
        dict: counters. Any non-zero value under "anomalies" is a defect.

    Raises:
        ValueError: on an ECOG observation whose value is missing, non-integral,
                    or outside 0-4. A grade 5 or a fractional score is a module
                    defect and must reach the caller, not be rounded away.
    """
    output_dir = output_dir or OUTPUT_DIR_FULL
    fhir_dir = Path(output_dir) / "fhir"

    counts = {
        "bundles_scanned": 0,
        "bundles_rewritten": 0,
        "observations_seen": 0,
        "observations_converted": 0,
        "observations_already_integer": 0,
        "profiles_stamped": 0,
        "data_absent_reasons_removed": 0,
        "unreadable_files": 0,
        "unreadable_file_names": [],
    }

    if not fhir_dir.exists():
        raise FileNotFoundError(f"FHIR directory not found for normalization: {fhir_dir}")

    print()
    print("="*80)
    print("NORMALIZING ECOG OBSERVATIONS TO mCODE (valueQuantity -> valueInteger)")
    print("="*80)

    for path in sorted(fhir_dir.glob("*.json")):
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            # Counted and named, never swallowed: a bundle this pass could not
            # read is a bundle whose ECOG observations are still non-conformant.
            counts["unreadable_files"] += 1
            counts["unreadable_file_names"].append(f"{path.name}: {type(e).__name__}: {e}")
            continue

        counts["bundles_scanned"] += 1
        if bundle.get("resourceType") != "Bundle":
            continue

        changed = False
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if not _is_ecog_observation(resource):
                continue

            counts["observations_seen"] += 1

            if "valueQuantity" in resource:
                raw = resource["valueQuantity"].get("value")
                if raw is None:
                    raise ValueError(
                        f"{path.name}: ECOG observation has valueQuantity with no value"
                    )
                score = int(raw)
                if score != raw:
                    raise ValueError(
                        f"{path.name}: ECOG score {raw!r} is not an integer grade"
                    )
                if not 0 <= score <= 4:
                    raise ValueError(
                        f"{path.name}: ECOG score {score} is outside 0-4. Grade 5 "
                        "means dead and must never be emitted."
                    )
                del resource["valueQuantity"]
                resource["valueInteger"] = score
                counts["observations_converted"] += 1
                changed = True
            elif "valueInteger" in resource:
                score = resource["valueInteger"]
                if not isinstance(score, int) or not 0 <= score <= 4:
                    raise ValueError(
                        f"{path.name}: ECOG valueInteger {score!r} is outside 0-4"
                    )
                counts["observations_already_integer"] += 1
            else:
                raise ValueError(
                    f"{path.name}: ECOG observation carries no value[x]"
                )

            if "dataAbsentReason" in resource:
                del resource["dataAbsentReason"]
                counts["data_absent_reasons_removed"] += 1
                changed = True

            profiles = resource.setdefault("meta", {}).setdefault("profile", [])
            if MCODE_ECOG_PROFILE_URL not in profiles:
                profiles.append(MCODE_ECOG_PROFILE_URL)
                counts["profiles_stamped"] += 1
                changed = True

        if changed:
            path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
            counts["bundles_rewritten"] += 1

    print(f"  Bundles scanned:              {counts['bundles_scanned']}")
    print(f"  Bundles rewritten:            {counts['bundles_rewritten']}")
    print(f"  ECOG observations seen:       {counts['observations_seen']}")
    print(f"  Converted to valueInteger:    {counts['observations_converted']}")
    print(f"  Already integer (idempotent): {counts['observations_already_integer']}")
    print(f"  mCODE profiles stamped:       {counts['profiles_stamped']}")
    print(f"  dataAbsentReason removed:     {counts['data_absent_reasons_removed']}")
    if counts["unreadable_files"]:
        print(f"  ⚠ UNREADABLE FILES:           {counts['unreadable_files']}")
        for name in counts["unreadable_file_names"][:10]:
            print(f"      {name}")

    print()
    print("NOTE: the CSV export (observations.csv) is NOT normalized. It keeps")
    print("Synthea's numeric-with-unit form. The FHIR bundles are the corpus the")
    print("pipeline reads; the CSVs are not.")

    return counts


#------------------------------------------------------------------------------


# Verification
#--------------

def verify_generation(output_dir=None, sample_size=10):
    """
    Verify that patient files were generated successfully

    Args:
        output_dir:  Run output directory. Defaults to OUTPUT_DIR_FULL.
        sample_size: How many bundles to spot-check for valid JSON.

    Returns:
        dict: Statistics about generated patients
    """
    output_dir = output_dir or OUTPUT_DIR_FULL

    print()
    print("="*80)
    print("VERIFYING GENERATION")
    print("="*80)

    fhir_dir = Path(output_dir) / "fhir"

    if not fhir_dir.exists():
        print("✗ FHIR directory not found")
        return None

    patient_files = list(fhir_dir.glob("*.json"))

    stats = {
        "total_files": len(patient_files),
        "fhir_directory": str(fhir_dir)
    }

    print(f"✓ Total patient files: {stats['total_files']}")
    print(f"✓ FHIR directory: {stats['fhir_directory']}")

    # Sample a few files to check they're valid JSON
    print()
    sample_size = min(sample_size, len(patient_files))
    print(f"Checking {sample_size} random files for validity...")
    invalid_count = 0

    for i, file in enumerate(random.sample(patient_files, sample_size), 1):
        try:
            with open(file, 'r') as f:
                data = json.load(f)
                bundle_type = data.get('resourceType')
                if bundle_type != 'Bundle':
                    print(f"  {i}. {file.name}: WARNING - unexpected resourceType={bundle_type}")
                    invalid_count += 1
                else:
                    print(f"  {i}. {file.name}: Valid Bundle")
        except Exception as e:
            print(f"  {i}. {file.name}: ERROR - {e}")
            invalid_count += 1

    if invalid_count > 0:
        print(f"⚠ {invalid_count}/{sample_size} sampled files had issues.")
    else:
        print(f"✓ All {sample_size} sampled files are valid Bundles.")

    stats["sampled_files"] = sample_size
    stats["sampled_invalid"] = invalid_count

    print()
    print("="*80)
    print("Generation verified!")
    print("="*80)
    print()

    return stats


def summarize_ecog_coverage(output_dir=None):
    """Measure what the ECOG module actually produced in a generated corpus.

    Denominator is "patient has any condition from ECOG_GUARD_CANCER_CODES at
    any point", not "has one still active": the module fires while the diagnosis
    is active, and a patient whose stage-I code was later abated in favour of a
    stage-III code legitimately holds an observation.

    Args:
        output_dir: Run output directory. Defaults to OUTPUT_DIR_FULL.

    Returns:
        dict: counts, the observed score distribution, observed missingness, and
              the non_cancer_with_ecog / value_5_emitted / non_survey_category /
              non_integer_value violation counters, which must all be zero.
    """
    output_dir = output_dir or OUTPUT_DIR_FULL
    fhir_dir = Path(output_dir) / "fhir"

    guard_codes = {code for code, _ in ECOG_GUARD_CANCER_CODES}

    summary = {
        "patient_bundles": 0,
        "non_patient_bundles": 0,
        "unreadable_files": 0,
        "unreadable_file_names": [],
        "cancer_patients": 0,
        "cancer_patients_with_ecog": 0,
        "non_cancer_with_ecog": 0,
        "observations_total": 0,
        "patients_with_multiple_observations": 0,
        "score_counts": {},
        "missingness_fraction_observed": None,
        "value_5_emitted": 0,
        "non_integer_value": 0,
        "non_survey_category": 0,
        "missing_encounter_reference": 0,
        "missing_effective_datetime": 0,
        "data_absent_reason_present": 0,
        "missing_mcode_profile": 0,
    }

    print()
    print("="*80)
    print("ECOG COVERAGE SUMMARY")
    print("="*80)

    for path in sorted(fhir_dir.glob("*.json")):
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            # Counted and named. A bundle this pass could not read is a bundle
            # whose ECOG observations are unaccounted for, which makes every
            # count below an undercount rather than a measurement.
            summary["unreadable_files"] += 1
            summary["unreadable_file_names"].append(f"{path.name}: {type(e).__name__}: {e}")
            continue

        if bundle.get("resourceType") != "Bundle":
            summary["non_patient_bundles"] += 1
            continue

        entries = [e.get("resource", {}) for e in bundle.get("entry", [])]
        if not any(r.get("resourceType") == "Patient" for r in entries):
            # hospitalInformation / practitionerInformation bundles
            summary["non_patient_bundles"] += 1
            continue

        summary["patient_bundles"] += 1

        has_cancer = any(
            coding.get("code") in guard_codes
            for r in entries if r.get("resourceType") == "Condition"
            for coding in r.get("code", {}).get("coding", [])
        )
        observations = [r for r in entries if _is_ecog_observation(r)]

        if has_cancer:
            summary["cancer_patients"] += 1

        if not observations:
            continue

        summary["observations_total"] += len(observations)
        if len(observations) > 1:
            summary["patients_with_multiple_observations"] += 1
        if has_cancer:
            summary["cancer_patients_with_ecog"] += 1
        else:
            summary["non_cancer_with_ecog"] += 1

        for obs in observations:
            value = obs.get("valueInteger")
            if not isinstance(value, int) or isinstance(value, bool):
                summary["non_integer_value"] += 1
            else:
                if value == 5:
                    summary["value_5_emitted"] += 1
                summary["score_counts"][value] = summary["score_counts"].get(value, 0) + 1

            categories = {
                coding.get("code")
                for cat in obs.get("category", [])
                for coding in cat.get("coding", [])
            }
            if ECOG_OBSERVATION_CATEGORY not in categories:
                summary["non_survey_category"] += 1
            if not obs.get("encounter", {}).get("reference"):
                summary["missing_encounter_reference"] += 1
            if not obs.get("effectiveDateTime") and not obs.get("effectivePeriod"):
                summary["missing_effective_datetime"] += 1
            if "dataAbsentReason" in obs:
                summary["data_absent_reason_present"] += 1
            if MCODE_ECOG_PROFILE_URL not in obs.get("meta", {}).get("profile", []):
                summary["missing_mcode_profile"] += 1

    if summary["cancer_patients"]:
        summary["missingness_fraction_observed"] = round(
            1.0 - summary["cancer_patients_with_ecog"] / summary["cancer_patients"], 4
        )

    summary["score_counts"] = {str(k): v for k, v in sorted(summary["score_counts"].items())}

    print(f"  Patient bundles:                 {summary['patient_bundles']}")
    print(f"  Cancer patients:                 {summary['cancer_patients']}")
    print(f"  Cancer patients with ECOG:       {summary['cancer_patients_with_ecog']}")
    print(f"  ECOG observations total:         {summary['observations_total']}")
    print(f"  Score distribution:              {summary['score_counts']}")
    print(f"  Missingness configured:          {ECOG_MISSINGNESS_FRACTION}")
    print(f"  Missingness observed:            {summary['missingness_fraction_observed']}")
    print()
    print("  Violations (all must be 0):")
    for key in ("non_cancer_with_ecog", "value_5_emitted", "non_integer_value",
                "non_survey_category", "missing_encounter_reference",
                "missing_effective_datetime", "data_absent_reason_present",
                "missing_mcode_profile", "unreadable_files"):
        flag = "✓" if summary[key] == 0 else "✗"
        print(f"    {flag} {key}: {summary[key]}")

    return summary


#------------------------------------------------------------------------------


# Run Manifest
#--------------

def _relative_to_project(path):
    """Express a path relative to main_path so the manifest stays portable.

    main_path itself differs between the macOS checkout and the Docker image, so
    an absolute path recorded here would not resolve on the other one. Anything
    outside main_path is returned unchanged.
    """
    try:
        return str(Path(path).resolve().relative_to(Path(main_path).resolve()))
    except (ValueError, OSError):
        return str(path)


def _sha256_file(path):
    """sha256 of a file, streamed. Used for the ~180MB Synthea JAR."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_run_manifest(output_dir, generation, module_info, verification,
                       normalization, ecog_summary, population_size, seed,
                       label=None):
    """Write the JSON run manifest that makes this generation reproducible.

    File 04 used to build a stats dict, print it, return it, and drop it. That
    left the corpus on disk with no record of the population size, the seed, the
    Synthea build, the module filter, or -- once the ECOG module existed -- the
    distribution and missingness fraction the scores were drawn from. Two corpora
    generated from different holding values are indistinguishable by inspection.

    Returns:
        dict: the manifest, as written.
    """
    manifest = {
        "manifest_version": 1,
        "label": label,
        "written_at_utc": datetime.now(timezone.utc).isoformat(),
        "project": Project_Name,
        "paths_are_relative_to": "main_path (01- Imports.py)",
        "generation": {
            "population_size": population_size,
            "min_age": MIN_AGE,
            "max_age": MAX_AGE,
            "state": STATE,
            "seed": seed,
            "years_of_history": YEARS,
            # Read off the run, not recomputed: the manifest must record the
            # filter the command actually carried.
            "module_filter": generation.get("module_filter"),
            "modules_dir": _relative_to_project(SYNTHEA_MODULES_DIR),
            "output_dir": _relative_to_project(output_dir),
            "export_fhir": EXPORT_FHIR,
            "export_ccda": EXPORT_CCDA,
            "export_csv": EXPORT_CSV,
            "command": generation.get("command"),
            "returncode": generation.get("returncode"),
            "elapsed_seconds": generation.get("elapsed_seconds"),
            "synthea_log": _relative_to_project(generation["log_path"])
                           if generation.get("log_path") else None,
            "loaded_modules": generation.get("loaded_modules"),
        },
        "synthea_jar": {
            "filename": os.path.basename(SYNTHEA_JAR_PATH),
            "sha256": _sha256_file(SYNTHEA_JAR_PATH),
            "bytes": os.path.getsize(SYNTHEA_JAR_PATH),
        },
        "ecog": {
            "module_filename": module_info["filename"],
            "module_sha256": module_info["sha256"],
            "module_bytes": module_info["bytes"],
            "module_write_status": module_info["status"],
            "module_loaded_by_synthea": generation.get("ecog_module_loaded"),
            "loinc_code": ECOG_LOINC_SCORE_CODE,
            "loinc_display": ECOG_LOINC_SCORE_DISPLAY,
            "observation_category": ECOG_OBSERVATION_CATEGORY,
            "mcode_profile": MCODE_ECOG_PROFILE_URL,
            "guard_mechanism": "Guard state, Active Condition condition type",
            "guard_codes": [code for code, _ in ECOG_GUARD_CANCER_CODES],
            "score_distribution_configured": {
                str(k): v for k, v in sorted(ECOG_SCORE_DISTRIBUTION.items())
            },
            "missingness_fraction_configured": ECOG_MISSINGNESS_FRACTION,
            "values_are_calibrated": False,
            "calibration_note": (
                "score_distribution_configured and missingness_fraction_configured "
                "are UNCALIBRATED HOLDING VALUES. Neither was fitted to a registry, "
                "a screening log, or any published cohort. Every eligibility rate "
                "computed from this corpus is conditional on them."
            ),
            "missingness_note": (
                "missingness_fraction_observed exceeds the configured fraction "
                "because a patient who dies or reaches the end of the simulation "
                "before the next encounter after diagnosis is also never scored."
            ),
            "normalization": normalization,
            "observed": ecog_summary,
        },
        "verification": verification,
    }

    manifest_path = Path(output_dir) / RUN_MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print()
    print("="*80)
    print("RUN MANIFEST")
    print("="*80)
    print(f"✓ Written: {manifest_path}")

    return manifest


#------------------------------------------------------------------------------


def run_generation(population_size=None, output_dir=None, seed=None, label=None,
                   force=False):
    """Full pipeline: write module -> generate -> normalize -> verify -> manifest.

    Args:
        population_size: Defaults to POPULATION_SIZE.
        output_dir:      Defaults to OUTPUT_DIR_FULL (the LIVE corpus directory).
                         Pass a scratch directory to generate without touching it.
        seed:            Synthea -s seed, or None.
        label:           Free-text label recorded in the manifest.
        force:           Generate into an output directory that already holds
                         bundles. See generate_synthea_patients().

    Returns:
        dict: {"success": bool, "manifest": dict | None, "generation": dict}
    """
    population_size = POPULATION_SIZE if population_size is None else population_size
    output_dir = output_dir or OUTPUT_DIR_FULL

    module_info = write_ecog_module()

    generation = generate_synthea_patients(
        population_size=population_size,
        output_dir=output_dir,
        seed=seed,
        force=force,
    )
    if not generation["success"]:
        return {"success": False, "manifest": None, "generation": generation}

    normalization = normalize_ecog_observations(output_dir)
    verification = verify_generation(output_dir)
    ecog_summary = summarize_ecog_coverage(output_dir)

    manifest = write_run_manifest(
        output_dir=output_dir,
        generation=generation,
        module_info=module_info,
        verification=verification,
        normalization=normalization,
        ecog_summary=ecog_summary,
        population_size=population_size,
        seed=seed,
        label=label,
    )

    return {"success": True, "manifest": manifest, "generation": generation}


#------------------------------------------------------------------------------


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="Generate Synthea patients with an ECOG performance status module."
    )
    parser.add_argument("--population", type=int, default=POPULATION_SIZE,
                        help=f"Population size (default: {POPULATION_SIZE})")
    parser.add_argument("--output-dir", default=None,
                        help="Synthea --exporter.baseDirectory. Defaults to the LIVE "
                             "corpus directory; pass a scratch path to leave it alone.")
    parser.add_argument("--seed", type=int, default=None,
                        help="Synthea -s population seed (default: Synthea's own)")
    parser.add_argument("--label", default=None,
                        help="Free-text label recorded in the run manifest")
    parser.add_argument("--module-only", action="store_true",
                        help="Write the ECOG module and exit without generating")
    parser.add_argument("--force", action="store_true",
                        help="Generate even if the output directory already holds "
                             "bundles. Synthea appends rather than replaces, so this "
                             "interleaves populations -- off by default.")
    args = parser.parse_args()

    print()
    print("╔═══════════════════════════════════════════════════════════════════════╗")
    print(f"║                   {Project_Name}: PATIENT GENERATION                  ║")
    print("╚═══════════════════════════════════════════════════════════════════════╝")
    print()

    if args.module_only:
        write_ecog_module()
        print()
        print("Module written. Generation skipped (--module-only).")
    else:
        with CaffeinateSession("Synthea Generation"):
            outcome = run_generation(
                population_size=args.population,
                output_dir=args.output_dir,
                seed=args.seed,
                label=args.label,
                force=args.force,
            )

        if outcome["success"]:
            stats = outcome["manifest"]["verification"]

            if stats and stats['total_files'] > 0:
                print()
                print("SUCCESS! Patients generated successfully.")
                print()
                print("NEXT STEP: Run the FHIR data clean script filter to only keep the cancer patient.")
                print()
            else:
                print()
                print("WARNING: Generation completed but no files found.")
                print("Check Synthea output for errors.")
                print()
        else:
            print()
            print("FAILED: Patient generation did not complete successfully.")
            print(f"Reason: {outcome['generation'].get('failure_reason')}")
            print("Please check error messages above.")
            print()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 10:15:45 2026

@author: ramyalsaffar
"""
