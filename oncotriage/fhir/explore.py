# Explore Synthea Patient Data
################################

"""Descriptive analysis of the cancer patient dataset.

Generates statistics, distributions, and visualizations. Uses CSV files from the
Synthea export plus our filtered JSON patient IDs.

Cancer detection and cancer-stage extraction are NOT implemented here. They are
delegated to ``oncotriage.registries.cancer_code_registry`` (File 08) and
``extract_patient_stage()`` (File 10) — the same code paths
``oncotriage.fhir.clean`` uses to decide which patients stay on disk and the
agent uses at query time. A dataset table produced here therefore describes the
same cohort the results tables describe.

Moved out of ``06- FHIR Explore.py`` by item 20c, pass 3a. That file is now a
thin entry point holding only its ``__main__`` block: nothing in the repository
chains it, so it needs no re-export shim and its exec bootstrap is gone.

WHAT CHANGED IN THE MOVE
------------------------
File 06 did five things at module load that a package module must not do:

    CSV_DIR / JSON_DIR / OUTPUT_DIR = ...     resolved three globs
    Path(OUTPUT_DIR).mkdir(...)               CREATED A DIRECTORY
    _CANCER_REGISTRY = load_registry()        imported icd10, built every code set
    sns.set_style("whitegrid")                mutated matplotlib global state
    plt.rcParams[...] = ...                   for the whole importing process

The three paths and the registry became ``csv_dir()``, ``json_dir()``,
``output_dir()`` and ``cancer_registry()``, resolved on first call and cached.
``output_dir()`` also performs the mkdir, so the directory still exists before
anything writes into it on EVERY call path rather than only when this module
happened to be loaded — the side effect moved, it did not disappear, and its new
home is the accessor every write target already goes through.

The styling became ``apply_plot_style()``, called at the top of ``main()`` and of
each of the seven functions that touch ``plt``. That is seven extra call lines
and it is deliberate: styling applied at import reached a direct caller of
``analyze_demographics()`` for free, and dropping it into ``main()`` alone would
have silently changed how a directly-called analysis renders.

matplotlib, seaborn and pandas ARE imported at module scope, unlike the
third-party imports deferred into function bodies elsewhere in this package.
They are this module's whole subject — seven of its twelve functions plot — and
nothing but ``06- FHIR Explore.py`` imports it, so the cost lands only on the
one caller that was always going to pay it. ``47- Package Split Test.py``
section 2 pre-imports all three before arming its traps, the same allowance it
already makes for openai, qdrant_client, numpy and langgraph, so the claim it
proves stays exactly "importing an oncotriage module reads no file".
"""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from oncotriage import paths
from oncotriage.config import Project_Name
from oncotriage.extraction.stage import extract_patient_stage
from oncotriage.registries.cancer_code_registry import (
    get_cancer_classification_stats,
    load_registry,
    reset_cancer_classification_stats,
)

# datetime is imported as a MODULE-level name from the class, matching
# "01- Imports.py"'s `from datetime import datetime`, because
# analyze_demographics() calls datetime.now() and generate_summary_report()
# calls datetime.now().strftime().
from datetime import datetime


#------------------------------------------------------------------------------


# The lazily-resolved configuration
#----------------------------------
#
# Plain accessor functions rather than a PEP 562 module __getattr__: a module
# __getattr__ is consulted for attribute access on the MODULE, not for a global
# name lookup inside a function body, so `OUTPUT_DIR` written bare inside
# analyze_demographics() would be a NameError rather than a lazy read.

_RESOLVED = {}


def csv_dir():
    """Synthea's CSV export directory. Resolved on first call, cached.

    Frequently absent: EXPORT_CSV is off in '04- FHIR Generate Data.py' because
    the CSV export does not fit on disk at POPULATION_SIZE. load_and_filter_csv()
    reports the miss per file rather than failing.
    """
    if "csv_dir" not in _RESOLVED:
        _RESOLVED["csv_dir"] = paths.data_patient_path + "csv/"
    return _RESOLVED["csv_dir"]


def json_dir():
    """The FHIR bundle directory the cohort is read from. Cached."""
    if "json_dir" not in _RESOLVED:
        _RESOLVED["json_dir"] = paths.data_fhir_path
    return _RESOLVED["json_dir"]


def output_dir():
    """Where the plots and the summary report are written. Cached.

    RESOLVES AND CREATES. File 06 ran `Path(OUTPUT_DIR).mkdir(parents=True,
    exist_ok=True)` at module level, so the directory existed before any caller
    could write into it. Moving the mkdir here keeps that true on every call
    path — every write target in this module goes through this function — while
    keeping the import itself free of filesystem work.
    """
    if "output_dir" not in _RESOLVED:
        resolved = paths.result_fhir_explore_path
        Path(resolved).mkdir(parents=True, exist_ok=True)
        _RESOLVED["output_dir"] = resolved
    return _RESOLVED["output_dir"]


def cancer_registry():
    """The same registry instance the cohort filter and the agent use. Cached.

    NOT routed through ``oncotriage.agent.deps``: the deps seam redirects what
    the AGENT reaches, and a stub installed for an agent test must not change
    what an exploratory cohort table reports.
    """
    if "cancer_registry" not in _RESOLVED:
        _RESOLVED["cancer_registry"] = load_registry()
    return _RESOLVED["cancer_registry"]


def apply_plot_style():
    """Apply the module's matplotlib / seaborn styling. Idempotent.

    These three statements ran at module import in File 06, which meant that
    importing the file restyled every plot in the process, whether or not it
    went on to draw one. They are applied here instead, by every function that
    draws — so no call path loses the styling and no importer gains it.
    """
    sns.set_style("whitegrid")
    plt.rcParams['figure.figsize'] = (12, 6)
    plt.rcParams['font.size'] = 10


# Names for the stage ordinals File 10 produces. AJCC stage groups run 0
# (in situ) through IV (distant), so these are facts about the staging
# system, not tunables — only the wording of the bucket labels is local.
STAGE_LABELS = {
    0: 'Stage 0/In Situ',
    1: 'Stage I',
    2: 'Stage II',
    3: 'Stage III',
    4: 'Stage IV/Metastatic',
}
STAGE_UNSPECIFIED = 'Unspecified'


#------------------------------------------------------------------------------


# Helper Functions
#------------------
def flag_primary_cancer(df_conditions):
    """
    Add an IS_CANCER column marking each condition row as a primary cancer.

    Detection is delegated to CancerCodeRegistry.is_primary_cancer() (File 08),
    which is what File 05 uses to decide which patient bundles survive on disk
    and what File 13 uses to pick the primary diagnosis. This file previously
    matched a keyword list against DESCRIPTION, and the two disagreed in both
    directions on this very dataset — "Suspected lung cancer (situation)" and
    "Metastatic malignant neoplasm to colon" are keyword hits that the registry
    rejects — so the exploratory cohort was not the cohort the pipeline runs on.

    Two recorded differences from File 05, both properties of the CSV export
    rather than of the detection logic:
      - Synthea's CSV carries no verificationStatus column, so refuted /
        entered-in-error rows cannot be skipped here. Synthea does not emit
        those statuses, so the cohorts still agree on this dataset.
      - The CSV carries one SNOMED code per row, so the registry runs on its
        single-code path rather than its multi-coding path.

    Args:
        df_conditions: conditions.csv DataFrame (must be a copy, not a slice)

    Returns:
        The same DataFrame, with IS_CANCER added.
    """
    if 'CODE' not in df_conditions.columns:
        # No code column: every row falls to the registry's display-term
        # fallback. Say so — a run classified this way is weaker evidence.
        print("  Note: conditions.csv has no CODE column — cancer detection "
              "falls back to display-term matching inside the registry")
        codes = [''] * len(df_conditions)
    else:
        codes = df_conditions['CODE']

    df_conditions['IS_CANCER'] = [
        cancer_registry().is_primary_cancer({
            'code': '' if pd.isna(code) else str(code).strip(),
            'display': '' if pd.isna(desc) else str(desc),
        })
        for code, desc in zip(codes, df_conditions['DESCRIPTION'])
    ]

    return df_conditions


def print_cancer_classification_stats():
    """Report which registry layer decided each condition (File 08 counters)."""
    stats = get_cancer_classification_stats()
    print("\nCancer classification paths (CancerCodeRegistry):")
    for path, count in stats.items():
        if count:
            print(f"  {path}: {count}")


def get_filtered_patient_ids():
    """
    Get the list of patient IDs from our filtered JSON files
    
    Returns:
        set: Patient IDs that we kept after filtering
    """
    json_path = Path(json_dir())
    patient_files = list(json_path.glob("*.json"))
    
    total_files = len(patient_files)
    print(f"Found {total_files} JSON files to process")
    print()
    
    patient_ids = set()
    errors = 0
    
    for idx, file in enumerate(patient_files, 1):
        # Progress indicator every 100 files
        if idx % 100 == 0 or idx == total_files:
            progress = (idx / total_files) * 100
            print(f"  Processing: {idx}/{total_files} files ({progress:.1f}%)")
        
        try:
            with open(file, 'r') as f:
                bundle = json.load(f)
                
            # Extract patient ID from bundle
            for entry in bundle.get('entry', []):
                resource = entry.get('resource', {})
                if resource.get('resourceType') == 'Patient':
                    patient_id = resource.get('id')
                    if patient_id:
                        patient_ids.add(patient_id)
                    break
        except Exception as e:
            errors += 1
            if errors <= 5:  # Only show first 5 errors
                print(f"  Warning: Could not extract ID from {file.name}: {e}")
    
    print()
    print(f"✓ Extracted {len(patient_ids)} unique patient IDs")
    if errors > 0:
        print(f"⚠ {errors} files had errors")
    print()
    
    return patient_ids


def load_and_filter_csv(filename, patient_ids):
    """
    Load a Synthea CSV file and filter to our 1000 patients
    
    Boolean-mask selection returns a view, and every caller here goes on to
    add a column (AGE, IS_CANCER, ...). Copying once at the source is what
    makes those assignments land on a real frame instead of a slice.

    Args:
        filename: CSV filename (e.g., 'patients.csv')
        patient_ids: Set of patient IDs to keep

    Returns:
        DataFrame filtered to our patients (an independent copy)
    """
    csv_path = Path(csv_dir()) / filename

    if not csv_path.exists():
        print(f"Warning: {filename} not found at {csv_path}")
        return None

    df = pd.read_csv(csv_path)

    # Filter to our patient IDs
    if 'PATIENT' in df.columns:
        df_filtered = df[df['PATIENT'].isin(patient_ids)].copy()
        print(f"Loaded {filename}: {len(df)} total → {len(df_filtered)} filtered")
        return df_filtered
    elif 'Id' in df.columns:
        df_filtered = df[df['Id'].isin(patient_ids)].copy()
        print(f"Loaded {filename}: {len(df)} total → {len(df_filtered)} filtered")
        return df_filtered
    else:
        print(f"Warning: No patient ID column found in {filename}")
        return df


# Analysis Functions
#--------------------
def analyze_demographics(df_patients):
    """
    Analyze patient demographics
    
✅ Normal/Expected Behavior
Mean age: 45-55 years (cancer increases with age, but we included 18-80 range)
Distribution: Right-skewed (more older patients than younger)
Range: 18-80 years (matches our generation parameters)
No gaps or spikes in the histogram

🚨 Abnormal Behavior
Mean age < 30 or > 70 → Too young/old, trials won't match well
Uniform distribution → Unrealistic (cancer isn't evenly distributed by age)
Large gaps (e.g., no patients 40-50) → Data generation problem
Spike at exactly 18 or 80 → Boundary artifacts

Action if abnormal: Regenerate with adjusted age range


✅ Normal/Expected Behavior
40-60% female, 40-60% male (roughly balanced)
Slight skew is OK (breast cancer affects more women)

🚨 Abnormal Behavior
<30% or >70% of either gender → Extreme imbalance
Exactly 50/50 → Too perfect, suspicious
<100 patients of either gender → Not enough diversity

Action if abnormal: Regenerate with larger population, check Synthea gender settings


✅ Normal/Expected Behavior
White: 50-70% (California demographics)
Hispanic: 20-40%
Asian: 10-20%
Black: 5-10%
Other: <5%
At least 3-4 races represented

🚨 Abnormal Behavior
One race >90% → No diversity, trials often need diverse populations
<3 races represented → Too homogeneous
Zero Hispanic patients → Unrealistic for California

Action if abnormal: Check Synthea demographics settings, regenerate if needed

    """
    apply_plot_style()
    print("\n" + "="*80)
    print("DEMOGRAPHICS ANALYSIS")
    print("="*80 + "\n")
    
    # Basic stats
    print(f"Total patients: {len(df_patients)}")
    print(f"\nGender distribution:")
    print(df_patients['GENDER'].value_counts())
    print(f"\nGender percentages:")
    print(df_patients['GENDER'].value_counts(normalize=True) * 100)
    
    # Age distribution
    today = datetime.now().date()
    df_patients['AGE'] = pd.to_datetime(df_patients['BIRTHDATE']).apply(
        lambda dob: today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    )    
    print(f"\nAge statistics:")
    print(df_patients['AGE'].describe())
    
    # Race/Ethnicity
    print(f"\nRace distribution:")
    print(df_patients['RACE'].value_counts())
    print(f"\nEthnicity distribution:")
    print(df_patients['ETHNICITY'].value_counts())
    
    # Visualizations
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    # Gender pie chart with counts
    gender_counts = df_patients['GENDER'].value_counts()
    def make_autopct(values):
        def my_autopct(pct):
            total = sum(values)
            val = int(round(pct*total/100.0))
            return f'{val}\n({pct:.1f}%)'
        return my_autopct
    
    axes[0, 0].pie(gender_counts.values, 
                   labels=gender_counts.index, 
                   autopct=make_autopct(gender_counts.values),
                   startangle=90)
    axes[0, 0].set_title('Gender Distribution', fontsize=14, fontweight='bold')
    
    # Age histogram
    axes[0, 1].hist(df_patients['AGE'], bins=20, edgecolor='black', alpha=0.7)
    axes[0, 1].set_title('Age Distribution', fontsize=14, fontweight='bold')
    axes[0, 1].set_xlabel('Age (years)', fontsize=12)
    axes[0, 1].set_ylabel('Count', fontsize=12)
    axes[0, 1].axvline(df_patients['AGE'].mean(), color='red', linestyle='--', 
                       linewidth=2, label=f'Mean: {df_patients["AGE"].mean():.1f}')
    axes[0, 1].legend(fontsize=10)
    axes[0, 1].grid(alpha=0.3)
    
    # Race bar chart with counts
    race_counts = df_patients['RACE'].value_counts()
    bars1 = axes[1, 0].bar(range(len(race_counts)), race_counts.values, alpha=0.7, edgecolor='black')
    axes[1, 0].set_xticks(range(len(race_counts)))
    axes[1, 0].set_xticklabels(race_counts.index, rotation=45, ha='right')
    axes[1, 0].set_title('Race Distribution', fontsize=14, fontweight='bold')
    axes[1, 0].set_xlabel('Race', fontsize=12)
    axes[1, 0].set_ylabel('Count', fontsize=12)
    axes[1, 0].grid(axis='y', alpha=0.3)
    
    # Add count labels on race bars
    for bar, count in zip(bars1, race_counts.values):
        height = bar.get_height()
        axes[1, 0].text(bar.get_x() + bar.get_width()/2., height + 5,
                       str(count), ha='center', va='bottom', fontsize=10, fontweight='bold')
    
    # Ethnicity bar chart with counts
    ethnicity_counts = df_patients['ETHNICITY'].value_counts()
    bars2 = axes[1, 1].bar(range(len(ethnicity_counts)), ethnicity_counts.values, alpha=0.7, edgecolor='black')
    axes[1, 1].set_xticks(range(len(ethnicity_counts)))
    axes[1, 1].set_xticklabels(ethnicity_counts.index, rotation=45, ha='right')
    axes[1, 1].set_title('Ethnicity Distribution', fontsize=14, fontweight='bold')
    axes[1, 1].set_xlabel('Ethnicity', fontsize=12)
    axes[1, 1].set_ylabel('Count', fontsize=12)
    axes[1, 1].grid(axis='y', alpha=0.3)
    
    # Add count labels on ethnicity bars
    for bar, count in zip(bars2, ethnicity_counts.values):
        height = bar.get_height()
        axes[1, 1].text(bar.get_x() + bar.get_width()/2., height + 5,
                       str(count), ha='center', va='bottom', fontsize=10, fontweight='bold')
    
    plt.tight_layout()
    plt.savefig(output_dir() + 'demographics.png', dpi=300, bbox_inches='tight')
    print(f"\n✓ Saved demographics plot: {output_dir()}demographics.png")
    plt.close()
    
    return df_patients

def analyze_conditions(df_conditions):
    """
    Analyze cancer diagnoses

✅ Normal/Expected Behavior
Top 3 cancers: Breast, Lung, Colorectal (most common cancers)
Breast cancer: 35-50% (most common cancer overall)
Lung cancer: 10-20%
Colorectal cancer: 15-25%
At least 5-8 different cancer types present

🚨 Abnormal Behavior
One cancer type >70% → Not enough diversity
Rare cancers dominate (e.g., 50% melanoma) → Unrealistic
<4 cancer types → Too limited for comprehensive trial matching
Zero lung or breast cancer → Data generation failed

Action if abnormal: Regenerate with -m "*cancer*" flag, increase population size


✅ Normal/Expected Behavior
Breast: 35-50%
Colorectal: 20-30%
Lung: 10-20%
Prostate: 8-15% (only males)
Other: 5-15%

🚨 Abnormal Behavior
"Other" category >30% → Too many unclassified cancers
Any category <5% (except Other) → Not enough patients for that type
Prostate cancer in females → Data corruption!

Action if abnormal: Check cancer categorization logic, verify Synthea modules loaded correctly
    
    """
    apply_plot_style()
    print("\n" + "="*80)
    print("CANCER DIAGNOSES ANALYSIS")
    print("="*80 + "\n")
    
    # Filter to primary cancer conditions only. IS_CANCER is set once in
    # main() by flag_primary_cancer(), which routes through File 08's registry.
    # .copy() because CATEGORY is added below.
    df_cancer = df_conditions[df_conditions['IS_CANCER']].copy()

    print(f"Total condition records: {len(df_conditions)}")
    print(f"Primary cancer condition records: {len(df_cancer)}")
    print(f"Unique patients with primary cancer: {df_cancer['PATIENT'].nunique()}")
    
    # Top cancer types
    print(f"\nTop 10 cancer diagnoses:")
    top_cancers = df_cancer['DESCRIPTION'].value_counts().head(10)
    print(top_cancers)
    
    # Visualization
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    
    # Top 10 cancer types bar chart (HORIZONTAL with counts)
    bars = axes[0].barh(range(len(top_cancers)), top_cancers.values)
    axes[0].set_yticks(range(len(top_cancers)))
    axes[0].set_yticklabels(top_cancers.index)
    axes[0].set_title('Top 10 Cancer Diagnoses', fontsize=14, fontweight='bold')
    axes[0].set_xlabel('Count', fontsize=12)
    axes[0].set_ylabel('Cancer Type', fontsize=12)
    
    # Add count labels on bars
    for i, (bar, count) in enumerate(zip(bars, top_cancers.values)):
        axes[0].text(count + 5, i, str(count), 
                    va='center', fontsize=10, fontweight='bold')
    
    # Cancer type categories (simplified)
    def categorize_cancer(description):
        description_lower = description.lower()
        if 'breast' in description_lower:
            return 'Breast Cancer'
        elif 'lung' in description_lower:
            return 'Lung Cancer'
        elif 'colon' in description_lower or 'colorectal' in description_lower:
            return 'Colorectal Cancer'
        elif 'prostate' in description_lower:
            return 'Prostate Cancer'
        elif 'melanoma' in description_lower:
            return 'Melanoma'
        else:
            return 'Other Cancer'
    
    df_cancer['CATEGORY'] = df_cancer['DESCRIPTION'].apply(categorize_cancer)
    category_counts = df_cancer['CATEGORY'].value_counts()
    
    # Pie chart with counts and percentages
    def make_autopct(values):
        def my_autopct(pct):
            total = sum(values)
            val = int(round(pct*total/100.0))
            return f'{val}\n({pct:.1f}%)'
        return my_autopct
    
    axes[1].pie(category_counts.values, 
                labels=category_counts.index, 
                autopct=make_autopct(category_counts.values),
                startangle=90)
    axes[1].set_title('Cancer Type Categories', fontsize=14, fontweight='bold')
    
    plt.tight_layout()
    plt.savefig(output_dir() + 'cancer_types.png', dpi=300, bbox_inches='tight')
    print(f"\n✓ Saved cancer types plot: {output_dir()}cancer_types.png")
    plt.close()
    
    return df_cancer


def analyze_medications(df_medications):
    """
    Analyze cancer medications
    
✅ Normal/Expected Behavior
Top drugs include: Chemotherapy agents, pain relievers, anti-nausea, steroids
Examples: Tamoxifen, Paclitaxel, Cisplatin, Ondansetron, Dexamethasone
Most patients: 3-10 medications (realistic for cancer treatment)
At least 20+ unique medications across all patients

🚨 Abnormal Behavior
Top medications are NOT cancer-related (e.g., only antibiotics, blood pressure meds) → Cancer treatment not modeled
Mean medications <2 per patient → Treatment regimens too simple
Mean medications >20 per patient → Unrealistic polypharmacy
<10 unique medications total → Too limited

Action if abnormal: Check if Synthea cancer modules include treatment protocols, regenerate with full cancer modules
    
    """
    apply_plot_style()
    print("\n" + "="*80)
    print("MEDICATIONS ANALYSIS")
    print("="*80 + "\n")
    
    print(f"Total medication records: {len(df_medications)}")
    print(f"Unique patients on medications: {df_medications['PATIENT'].nunique()}")
    print(f"Unique medications: {df_medications['DESCRIPTION'].nunique()}")
    
    # Top medications
    print(f"\nTop 15 medications:")
    top_meds = df_medications['DESCRIPTION'].value_counts().head(15)
    print(top_meds)
    
    # Medications per patient
    meds_per_patient = df_medications.groupby('PATIENT').size()
    print(f"\nMedications per patient:")
    print(meds_per_patient.describe())
    
    # Visualization
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    
    # Top 15 medications
    top_meds.plot(kind='barh', ax=axes[0])
    axes[0].set_title('Top 15 Medications')
    axes[0].set_xlabel('Number of Patients')
    axes[0].set_ylabel('Medication')
    
    # Medications per patient distribution
    axes[1].hist(meds_per_patient, bins=30, edgecolor='black')
    axes[1].set_title('Medications per Patient Distribution')
    axes[1].set_xlabel('Number of Medications')
    axes[1].set_ylabel('Number of Patients')
    axes[1].axvline(meds_per_patient.mean(), color='red', linestyle='--', label=f'Mean: {meds_per_patient.mean():.1f}')
    axes[1].legend()
    
    plt.tight_layout()
    plt.savefig(output_dir() + 'medications.png', dpi=300, bbox_inches='tight')
    print(f"\n✓ Saved medications plot: {output_dir()}medications.png")
    plt.close()


def generate_summary_report(df_patients, df_cancer, patient_ids, data_source: str = "Synthea synthetic patient generator"):
    """
    Generate a text summary report
    """
    report_path = output_dir() + 'summary_report.txt'
    
    with open(report_path, 'w') as f:
        f.write("="*80 + "\n")
        f.write(f"{Project_Name}: PATIENT DATASET SUMMARY\n")
        f.write("="*80 + "\n\n")
        
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        # Dataset overview
        f.write("DATASET OVERVIEW\n")
        f.write("-" * 80 + "\n")
        f.write(f"Total patients: {len(patient_ids)}\n")
        f.write(f"Data source: {data_source}\n")
        f.write(f"Age range: 18-80 years\n")
        f.write(f"Module filter: Cancer patients only\n\n")
        
        # Demographics
        f.write("DEMOGRAPHICS\n")
        f.write("-" * 80 + "\n")
        f.write(f"Gender:\n")
        for gender, count in df_patients['GENDER'].value_counts().items():
            pct = count / len(df_patients) * 100
            f.write(f"  {gender}: {count} ({pct:.1f}%)\n")
        
        f.write(f"\nAge statistics:\n")
        f.write(f"  Mean: {df_patients['AGE'].mean():.1f} years\n")
        f.write(f"  Median: {df_patients['AGE'].median():.1f} years\n")
        f.write(f"  Range: {df_patients['AGE'].min():.0f} - {df_patients['AGE'].max():.0f} years\n\n")
        
        # Cancer types
        f.write("CANCER DIAGNOSES\n")
        f.write("-" * 80 + "\n")
        f.write("Detection: CancerCodeRegistry (File 08), SNOMED/ICD-10-CM codes\n")
        f.write(f"Unique patients with primary cancer: {df_cancer['PATIENT'].nunique()}\n")
        f.write(f"Total primary cancer condition records: {len(df_cancer)}\n\n")
        
        f.write("Top 5 cancer types:\n")
        for idx, (cancer, count) in enumerate(df_cancer['DESCRIPTION'].value_counts().head(5).items(), 1):
            f.write(f"  {idx}. {cancer}: {count}\n")
        
        f.write("\n" + "="*80 + "\n")
    
    print(f"\n✓ Saved summary report: {report_path}")
    
    
def analyze_cancer_stages(df_cancer, df_conditions):
    """Extract and visualize cancer staging

✅ Normal/Expected Behavior
Stage I: 25-35% (early detection)
Stage II: 20-30%
Stage III: 15-25%
Stage IV/Metastatic: 15-25%
Unspecified: <20% (some diagnoses don't include stage)
Progression: More Stage I than Stage IV (realistic)

🚨 Abnormal Behavior
Stage IV >50% → Too many metastatic patients (unrealistic for trial population)
Unspecified >50% → Synthea didn't generate staging info properly
Zero Stage I or II → No early-stage patients (trials need mix)
All same stage → Data generation bug

Action if abnormal: Regenerate, check if Synthea cancer modules include staging

    Args:
        df_cancer:     primary cancer condition rows (defines the cohort)
        df_conditions: all condition rows (the text stage is read from)
    """
    apply_plot_style()

    print("\n" + "="*80)
    print("CANCER STAGE ANALYSIS")
    print("="*80 + "\n")

    # Stage comes from File 10's extract_patient_stage() — the same function
    # Stage 4 of the pipeline calls. The local extract_stage() this replaces
    # tested 'stage i' before 'stage iii' and 'stage iv', and 'stage i' is a
    # prefix of both, so every stage above I was reported as Stage I and none
    # of the abnormality thresholds above could ever fire.
    #
    # Read over ALL conditions of the cancer patients, not only the rows the
    # registry marked primary: the pipeline also sees the whole condition
    # list, so "Metastatic malignant neoplasm to colon" still contributes its
    # Stage IV through extract_patient_stage()'s metastatic tier even though
    # the registry keeps that row out of the primary-cancer cohort.
    cancer_patients = set(df_cancer['PATIENT'])
    df_staged = df_conditions[df_conditions['PATIENT'].isin(cancer_patients)].copy()

    df_staged['STAGE_ORDINAL'] = pd.array(
        [
            extract_patient_stage([{'display': '' if pd.isna(d) else str(d)}])
            for d in df_staged['DESCRIPTION']
        ],
        dtype='Int64',   # nullable: <NA> is "no stage in this row", not 0
    )

    # Count UNIQUE PATIENTS per stage (not condition records)
    # Strategy: For each patient, take their WORST stage (highest ordinal).
    # max() skips <NA>; a patient with no staged row at all stays <NA>.
    worst_ordinal = df_staged.groupby('PATIENT')['STAGE_ORDINAL'].max()
    patient_stages = worst_ordinal.map(
        lambda o: STAGE_UNSPECIFIED if pd.isna(o) else STAGE_LABELS[int(o)]
    )

    # Fixed bucket order, zeros kept: "zero Stage I or II" is one of the
    # abnormalities this plot exists to show, and a dropped bar hides it.
    stage_order = [STAGE_LABELS[o] for o in sorted(STAGE_LABELS)] + [STAGE_UNSPECIFIED]
    stage_counts = patient_stages.value_counts().reindex(stage_order, fill_value=0)

    # Calculate percentages
    stage_percentages = (stage_counts / stage_counts.sum()) * 100

    print(f"Total cancer patients: {len(patient_stages)}")
    print(f"Primary cancer condition records: {len(df_cancer)}")
    print(f"Condition records searched for stage text: {len(df_staged)}")
    print()
    print("Patient distribution by stage:")
    for stage, count in stage_counts.items():
        pct = (count / len(patient_stages)) * 100
        print(f"  {stage}: {count} patients ({pct:.1f}%)")
    print()
    
    # Visualization
    plt.figure(figsize=(12, 7))
    bars = plt.bar(range(len(stage_counts)), stage_counts.values, alpha=0.7, edgecolor='black')
    plt.xticks(range(len(stage_counts)), stage_counts.index, rotation=45, ha='right')
    plt.title('Cancer Stage Distribution\n(Unique Patients by Most Advanced Stage)', fontsize=14, fontweight='bold')
    plt.xlabel('Cancer Stage', fontsize=12)
    plt.ylabel('Number of Patients', fontsize=12)
    plt.grid(axis='y', alpha=0.3)
    
    # Add labels with COUNT and PERCENTAGE
    for bar, count, pct in zip(bars, stage_counts.values, stage_percentages.values):
        height = bar.get_height()
        plt.text(bar.get_x() + bar.get_width()/2., height + 5,
                f'{count}\n({pct:.1f}%)', 
                ha='center', va='bottom', fontsize=10, fontweight='bold')
    
    plt.tight_layout()
    plt.savefig(output_dir() + 'cancer_stages.png', dpi=300, bbox_inches='tight')
    
    # Print warning if too many unspecified
    unspecified_pct = stage_percentages.get('Unspecified', 0)
    if unspecified_pct > 50:
        print(f"⚠️  WARNING: {unspecified_pct:.1f}% of patients have unspecified stage")
        print("   This reflects Synthea's cancer module limitations - staging not always generated")
        print("   This is also realistic (real EHR data often lacks complete staging)")
    
    print(f"\n✓ Saved cancer stages plot")
    plt.close()
    

def analyze_age_by_cancer(df_patients, df_cancer):
    """Age distribution for each major cancer type
    
✅ Normal/Expected Behavior
Breast cancer: Mean age 50-60 (peak incidence)
Lung cancer: Mean age 60-70 (older patients)
Colorectal cancer: Mean age 55-65
Prostate cancer: Mean age 65-75 (older males)
Each cancer has 15+ year age range (diversity within type)

🚨 Abnormal Behavior
All cancers same mean age → Unrealistic (different cancers affect different ages)
Breast cancer mean age >65 → Too old (typically younger)
Prostate cancer mean age <60 → Too young (typically older)
<10 year age range per cancer → Not enough diversity

Action if abnormal: Regenerate with wider age range, check if age filters are too restrictive
    
    """
    apply_plot_style()
    # Merge patient age with cancer type
    df_cancer_age = df_cancer.merge(
        df_patients[['Id', 'AGE']], 
        left_on='PATIENT', 
        right_on='Id',
        how='left'
    )
    
    # Get cancer categories with at least 20 patients
    category_counts = df_cancer_age['CATEGORY'].value_counts()
    categories = category_counts[category_counts >= 20].index.tolist()
    
    if len(categories) == 0:
        print("Not enough patients per category for age analysis")
        return
    
    # Dynamic subplot grid
    n_categories = len(categories)
    n_cols = 2
    n_rows = (n_categories + 1) // 2  # Ceiling division
    
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(14, 5*n_rows))
    
    # Flatten axes for easy iteration
    if n_rows == 1:
        axes = axes.reshape(1, -1)
    axes = axes.flatten()
    
    for idx, category in enumerate(categories):
        df_cat = df_cancer_age[df_cancer_age['CATEGORY'] == category]
        
        axes[idx].hist(df_cat['AGE'].dropna(), bins=15, edgecolor='black', alpha=0.7)
        axes[idx].set_title(f'{category}\n(n={len(df_cat)} patients)', 
                           fontsize=12, fontweight='bold')
        axes[idx].set_xlabel('Age (years)', fontsize=10)
        axes[idx].set_ylabel('Count', fontsize=10)
        axes[idx].axvline(df_cat['AGE'].mean(), color='red', linestyle='--', 
                        linewidth=2, label=f'Mean: {df_cat["AGE"].mean():.1f}')
        axes[idx].legend(fontsize=9)
        axes[idx].grid(alpha=0.3)
    
    # Hide empty subplots
    for idx in range(len(categories), len(axes)):
        axes[idx].axis('off')
    
    plt.tight_layout()
    plt.savefig(output_dir() + 'age_by_cancer_type.png', dpi=300, bbox_inches='tight')
    print(f"✓ Saved age by cancer type plot (n={len(categories)} cancer types)")
    plt.close()
    

def analyze_patients_per_cancer_type(df_cancer):
    """Count UNIQUE PATIENTS per cancer category
    
✅ Normal/Expected Behavior
Breast: 350-500 patients (most common)
Colorectal: 200-300 patients
Lung: 100-200 patients
Prostate: 80-150 patients
Total adds up to ~1000 (some patients may have multiple cancers)

🚨 Abnormal Behavior
Total patients >1050 → Many patients have multiple cancers (check data quality)
Any type <50 patients → Not enough for meaningful evaluation
Breast <300 or >600 → Unrealistic proportion
Sum of all categories ≠ ~1000 → Patients counted multiple times or missing

Action if abnormal: Check if patients have duplicate/multiple cancer diagnoses, verify filtering logic

    """
    apply_plot_style()
    
    # Get unique patients per category
    patients_per_category = df_cancer.groupby('CATEGORY')['PATIENT'].nunique().sort_values(ascending=False)
    
    print("\nUnique Patients per Cancer Type:")
    print(patients_per_category)
    
    plt.figure(figsize=(10, 6))
    bars = plt.bar(range(len(patients_per_category)), patients_per_category.values, alpha=0.7, edgecolor='black')
    plt.xticks(range(len(patients_per_category)), patients_per_category.index, rotation=45, ha='right')
    plt.title('Unique Patients per Cancer Type', fontsize=14, fontweight='bold')
    plt.xlabel('Cancer Type', fontsize=12)
    plt.ylabel('Number of Patients', fontsize=12)
    plt.grid(axis='y', alpha=0.3)
    
    for bar, count in zip(bars, patients_per_category.values):
        height = bar.get_height()
        plt.text(bar.get_x() + bar.get_width()/2., height + 2,
                str(count), ha='center', va='bottom', fontsize=10, fontweight='bold')
    
    plt.tight_layout()
    plt.savefig(output_dir() + 'patients_per_cancer_type.png', dpi=300, bbox_inches='tight')
    print(f"✓ Saved patients per cancer type plot")
    plt.close()


def analyze_comorbidities(df_conditions, df_patients):
    """
    Analyze non-cancer comorbidities
    
✅ Normal/Expected Behavior
Patients have 3-8 total conditions (including cancer + comorbidities)
Cancer conditions: 1-2 per patient (primary cancer, maybe metastases)
Non-cancer conditions: 2-6 per patient (realistic for age 40-60)
Common comorbidities present:

Hypertension (30-40% of patients)
Diabetes (15-25% of patients)
Hyperlipidemia/High cholesterol (20-30%)
Obesity (20-30%)
Asthma/COPD (10-15%)
Arthritis (15-25%)

Age correlation: Older patients have MORE conditions

🚨 Abnormal Behavior
Patients have ONLY cancer, no other conditions → Unrealistic (older adults always have comorbidities)
Mean conditions <2 per patient → Synthea didn't generate medical history
Mean conditions >15 per patient → Excessive, unrealistic
No hypertension or diabetes → Missing common conditions (data generation bug)
Rare conditions >30% → Statistical impossibility (e.g., 40% have lupus)
Young patients (age 20-30) have 10+ conditions → Age mismatch


    """
    apply_plot_style()
    print("\n" + "="*80)
    print("COMORBIDITIES ANALYSIS")
    print("="*80 + "\n")
    
    # Separate cancer vs non-cancer conditions using the same IS_CANCER column
    # the diagnosis analysis used — one registry decision per row, not a second
    # keyword pass that would split the rows differently.
    df_cancer_cond = df_conditions[df_conditions['IS_CANCER']]
    df_other_cond = df_conditions[~df_conditions['IS_CANCER']]

    # Conditions per patient
    cancer_per_patient = df_cancer_cond.groupby('PATIENT').size()
    other_per_patient = df_other_cond.groupby('PATIENT').size()
    total_per_patient = df_conditions.groupby('PATIENT').size()

    print(f"Total condition records: {len(df_conditions)}")
    print(f"Primary cancer conditions: {len(df_cancer_cond)}")
    print(f"Non-cancer conditions (incl. secondary/metastatic): {len(df_other_cond)}")
    print()
    
    print("Conditions per patient (including cancer):")
    print(total_per_patient.describe())
    print()
    
    print("Non-cancer conditions per patient:")
    print(other_per_patient.describe())
    print()
    
    # Top non-cancer conditions
    print("Top 15 non-cancer comorbidities:")
    top_comorbidities = df_other_cond['DESCRIPTION'].value_counts().head(15)
    print(top_comorbidities)
    print()
    
    # Check for common expected conditions
    common_expected = {
        'Hypertension': ['hypertension', 'high blood pressure'],
        'Diabetes': ['diabetes'],
        'Hyperlipidemia': ['hyperlipidemia', 'cholesterol'],
        'Obesity': ['obesity', 'overweight'],
        'COPD/Asthma': ['asthma', 'copd', 'chronic obstructive']
    }
    
    print("Prevalence of common conditions:")
    total_patients = df_conditions['PATIENT'].nunique()
    for condition_name, keywords in common_expected.items():
        pattern = '|'.join(keywords)
        count = df_other_cond[df_other_cond['DESCRIPTION'].str.lower().str.contains(pattern, na=False)]['PATIENT'].nunique()
        percentage = (count / total_patients) * 100
        print(f"  {condition_name}: {count} patients ({percentage:.1f}%)")
    
    # Visualizations
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    
    # Total conditions per patient histogram
    axes[0, 0].hist(total_per_patient, bins=20, edgecolor='black', alpha=0.7)
    axes[0, 0].set_title('Total Conditions per Patient\n(Cancer + Comorbidities)', fontsize=12, fontweight='bold')
    axes[0, 0].set_xlabel('Number of Conditions')
    axes[0, 0].set_ylabel('Number of Patients')
    axes[0, 0].axvline(total_per_patient.mean(), color='red', linestyle='--', 
                       label=f'Mean: {total_per_patient.mean():.1f}')
    axes[0, 0].legend()
    axes[0, 0].grid(alpha=0.3)
    
    # Non-cancer conditions per patient histogram
    axes[0, 1].hist(other_per_patient, bins=20, edgecolor='black', alpha=0.7, color='orange')
    axes[0, 1].set_title('Non-Cancer Conditions per Patient\n(Comorbidities Only)', fontsize=12, fontweight='bold')
    axes[0, 1].set_xlabel('Number of Conditions')
    axes[0, 1].set_ylabel('Number of Patients')
    axes[0, 1].axvline(other_per_patient.mean(), color='red', linestyle='--', 
                       label=f'Mean: {other_per_patient.mean():.1f}')
    axes[0, 1].legend()
    axes[0, 1].grid(alpha=0.3)
    
    # Top 10 comorbidities bar chart
    top_10 = df_other_cond['DESCRIPTION'].value_counts().head(10)
    bars = axes[1, 0].barh(range(len(top_10)), top_10.values, alpha=0.7, edgecolor='black')
    axes[1, 0].set_yticks(range(len(top_10)))
    axes[1, 0].set_yticklabels(top_10.index)
    axes[1, 0].set_title('Top 10 Non-Cancer Comorbidities', fontsize=12, fontweight='bold')
    axes[1, 0].set_xlabel('Count')
    axes[1, 0].grid(axis='x', alpha=0.3)
    
    for i, (bar, count) in enumerate(zip(bars, top_10.values)):
        axes[1, 0].text(count + 5, i, str(count), va='center', fontsize=9, fontweight='bold')
    
    # Prevalence of common conditions
    prevalence_data = {}
    for condition_name, keywords in common_expected.items():
        pattern = '|'.join(keywords)
        count = df_other_cond[df_other_cond['DESCRIPTION'].str.lower().str.contains(pattern, na=False)]['PATIENT'].nunique()
        prevalence_data[condition_name] = (count / total_patients) * 100
    
    bars2 = axes[1, 1].bar(range(len(prevalence_data)), prevalence_data.values(), alpha=0.7, edgecolor='black', color='green')
    axes[1, 1].set_xticks(range(len(prevalence_data)))
    axes[1, 1].set_xticklabels(prevalence_data.keys(), rotation=45, ha='right')
    axes[1, 1].set_title('Prevalence of Common Conditions', fontsize=12, fontweight='bold')
    axes[1, 1].set_ylabel('Percentage of Patients (%)')
    axes[1, 1].grid(axis='y', alpha=0.3)
    axes[1, 1].set_ylim(0, max(prevalence_data.values()) * 1.2)
    
    for bar, pct in zip(bars2, prevalence_data.values()):
        height = bar.get_height()
        axes[1, 1].text(bar.get_x() + bar.get_width()/2., height + 1,
                       f'{pct:.1f}%', ha='center', va='bottom', fontsize=9, fontweight='bold')
    
    plt.tight_layout()
    plt.savefig(output_dir() + 'comorbidities.png', dpi=300, bbox_inches='tight')
    print(f"\n✓ Saved comorbidities plot: {output_dir()}comorbidities.png")
    plt.close()


#------------------------------------------------------------------------------


# Main Analysis
#---------------

def main():
    """
    Run complete data exploration
    """
    apply_plot_style()

    print("\n" + "╔" + "═"*78 + "╗")
    print("║" + " "*20 + f"{Project_Name}: DATA EXPLORATION" + " "*24 + "║")
    print("╚" + "═"*78 + "╝\n")
    
    # Step 1: Get filtered patient IDs
    print("="*80)
    print("STEP 1: LOADING FILTERED PATIENT IDs")
    print("="*80 + "\n")
    
    patient_ids = get_filtered_patient_ids()
    
    if not patient_ids:
        print("ERROR: No patient IDs found. Run filter script first!")
        return
    
    # Step 2: Load and filter CSV files
    print("\n" + "="*80)
    print("STEP 2: LOADING AND FILTERING CSV FILES")
    print("="*80 + "\n")
    
    df_patients = load_and_filter_csv('patients.csv', patient_ids)
    df_conditions = load_and_filter_csv('conditions.csv', patient_ids)
    df_medications = load_and_filter_csv('medications.csv', patient_ids)
    
    if df_patients is None:
        print("ERROR: Could not load patients.csv. Make sure CSV export was enabled during generation!")
        return

    # Classify conditions once, with the pipeline's registry, so every
    # downstream analysis splits the rows the same way.
    if df_conditions is not None:
        reset_cancer_classification_stats()
        df_conditions = flag_primary_cancer(df_conditions)
        print_cancer_classification_stats()

    # Step 3: Run analyses
    print("\n" + "="*80)
    print("STEP 3: RUNNING ANALYSES")
    print("="*80)
    
    df_patients = analyze_demographics(df_patients)
    
    if df_conditions is not None:
        df_cancer = analyze_conditions(df_conditions)
        
        analyze_cancer_stages(df_cancer, df_conditions)
        analyze_patients_per_cancer_type(df_cancer)
        analyze_age_by_cancer(df_patients, df_cancer)
    
    else:
        df_cancer = None
        print("\nWarning: conditions.csv not found, skipping cancer analysis")
    
    if df_medications is not None:
        analyze_medications(df_medications)
    else:
        print("\nWarning: medications.csv not found, skipping medication analysis")
    
    if df_conditions is not None:
        analyze_comorbidities(df_conditions, df_patients)
    
    # Step 4: Generate summary report
    print("\n" + "="*80)
    print("STEP 4: GENERATING SUMMARY REPORT")
    print("="*80)
    
    if df_cancer is not None:
        generate_summary_report(df_patients, df_cancer, patient_ids)
    
    # Final summary
    print("\n" + "="*80)
    print("EXPLORATION COMPLETE!")
    print("="*80 + "\n")
    print(f"Output directory: {output_dir()}")
    print(f"Generated files:")
    print(f"  - demographics.png")
    print(f"  - cancer_types.png")
    print(f"  - medications.png")
    print(f"  - comorbidities.png")
    print(f"  - summary_report.txt")
    print()


#------------------------------------------------------------------------------


"""
Created on Wed Feb 11 12:34:51 2026

@author: ramyalsaffar
"""
