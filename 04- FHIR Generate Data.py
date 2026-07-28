# Generate Synthea Patients with Cancer
#########################################

"""
Step 1: Generate synthetic cancer patients using Synthea
Calls Synthea JAR file via subprocess to generate FHIR patient data
"""


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
MODULE_FILTER = "*cancer*"

# State for demographics
STATE = "California"

# Synthea JAR location (in patients folder)
SYNTHEA_JAR_PATH = data_patient_path + "synthea-with-dependencies.jar"

# Output directory for generated FHIR files (temporary full population of healthy people and people with cancer)
OUTPUT_DIR_FULL = data_patient_path

# FHIR export settings
EXPORT_FHIR = "true"
EXPORT_CCDA = "false"
EXPORT_CSV  = "true"

# Limits patient history to last X years
# 0 years mean lifetime records
YEARS = 0


# Main Generation Function
#--------------------------
def generate_synthea_patients():
    """
    Generate synthetic patients using Synthea via subprocess
    
    This generates a full population with cancer modules.
    About 7-10% will have actual cancer diagnoses.
    
    The full population is saved to a temporary directory for filtering.
    """
    
    print("="*80)
    print("STEP 1: GENERATE SYNTHEA PATIENTS")
    print("="*80)
    print()
    
    # Check if Synthea JAR exists
    if not os.path.exists(SYNTHEA_JAR_PATH):
        print(f"ERROR: Synthea JAR not found at: {SYNTHEA_JAR_PATH}")
        print("Please download synthea-with-dependencies.jar and place it in:")
        print(f"  {data_patient_path}")
        print()
        print("Download from: https://github.com/synthetichealth/synthea/releases")
        return False
    
    print(f"✓ Found Synthea JAR: {SYNTHEA_JAR_PATH}")
    
    # Create output directory if it doesn't exist
    Path(OUTPUT_DIR_FULL).mkdir(parents=True, exist_ok=True)
    print(f"✓ Output directory: {OUTPUT_DIR_FULL}")
    
    # Build Synthea command
    command = [
        "java",
        "-jar",
        SYNTHEA_JAR_PATH,
        "-p", str(POPULATION_SIZE),
        "-a", f"{MIN_AGE}-{MAX_AGE}",
        "-m", MODULE_FILTER,
        f"--exporter.fhir.export={EXPORT_FHIR}",
        f"--exporter.ccda.export={EXPORT_CCDA}",
        f"--exporter.csv.export={EXPORT_CSV}",
        f"--exporter.baseDirectory={OUTPUT_DIR_FULL}",
        f"--exporter.years_of_history={YEARS}",
        STATE
    ]
    
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
    print(f"Population size: {POPULATION_SIZE}")
    print(f"Age range: {MIN_AGE}-{MAX_AGE} years")
    print(f"Module filter: {MODULE_FILTER}")
    print(f"State: {STATE}")
    print()
    print("This will take few moments depending on population size...")
    print("You'll see 'Loading modules...' messages from Synthea")
    print()
    
    # Execute Synthea
    start_time = time.time()
    
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
        
        # Filter output - only show progress every 100 patients
        patient_count = 0
        for line in process.stdout:
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
        
        # Check if successful
        if result.returncode != 0:
            print()
            print("="*80)
            print("ERROR: Synthea generation failed!")
            print("="*80)
            print(f"Return code: {result.returncode}")
            return False
        
        elapsed_time = time.time() - start_time
        
        print()
        print("="*80)
        print("GENERATION COMPLETE!")
        print("="*80)
        print(f"Time elapsed: {elapsed_time/60:.1f} minutes")
        
        # Check output directory
        fhir_dir = Path(OUTPUT_DIR_FULL) / "fhir"
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
        
        return True
        
    except FileNotFoundError:
        print()
        print("="*80)
        print("ERROR: Java not found!")
        print("="*80)
        print("Please install Java JDK 11 or newer:")
        print("  macOS: brew install openjdk@11")
        print("  Or download from: https://adoptium.net/")
        print()
        return False
    
    except Exception as e:
        print()
        print("="*80)
        print("ERROR: Unexpected error during generation")
        print("="*80)
        print(f"Error: {e}")
        print()
        return False


def verify_generation():
    """
    Verify that patient files were generated successfully
    
    Returns:
        dict: Statistics about generated patients
    """
    print()
    print("="*80)
    print("VERIFYING GENERATION")
    print("="*80)
    
    fhir_dir = Path(OUTPUT_DIR_FULL) / "fhir"
    
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
    sample_size = min(10, len(patient_files))
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
    
    print()
    print("="*80)
    print("Generation verified!")
    print("="*80)
    print()
    
    return stats


#------------------------------------------------------------------------------


if __name__ == "__main__":
    
    print()
    print("╔═══════════════════════════════════════════════════════════════════════╗")
    print(f"║                   {Project_Name}: PATIENT GENERATION                  ║")
    print("╚═══════════════════════════════════════════════════════════════════════╝")
    print()
    
    # Generate patients
    success = generate_synthea_patients()
    
    if success:
        # Verify generation
        stats = verify_generation()
        
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
        print("Please check error messages above.")
        print()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 10:15:45 2026

@author: ramyalsaffar
"""