# CI Path Provisioning
######################

"""Create the sibling data-directory skeleton that a CI checkout does not have.

WHY THIS FILE EXISTS
--------------------
Only `03- Code/` is version-controlled. Every data path in
`oncotriage/paths.py` is resolved by GLOB against the PROJECT ROOT -- the
repository's parent -- and `_glob_one` RAISES when nothing matches. That raise
is correct (pass 20f-1 made an ambiguous or absent path loud rather than
guessing), and it means a bare clone cannot resolve a single path variable.

Measured, not assumed: with the root pointed at an empty directory, 22 of the
27 non-serial test files die before their first check, all with the same
`RuntimeError: No directory matched the data pattern`. They are not failing on
their subject matter -- they never reach it.

WHAT THIS CREATES, AND WHAT IT DELIBERATELY DOES NOT
-----------------------------------------------------
DIRECTORIES ONLY, plus the two MeSH lookups this repository already vendors.
It fabricates no patient data, no trial corpus, no inference database and no
credentials. A test that needs those still fails, loudly, and that is the
point: the classification in `.github/scripts/ci_test_buckets.py` records which
tests those are, and inventing inputs to make them pass would be the exact
defect this project's non-degeneracy rule exists to catch.

THE DIRECTORY NAMES ARE THIS FILE'S CHOICE, and they have to be. The globs are
suffix patterns (`{root}/*Data/`, `{data}/*MeSH/`), so any prefix works; these
mirror the numbering the development tree uses so a CI failure path reads the
same as a local one. `_glob_one` raises when MORE than one directory matches,
so the skeleton must not be created beside a real data tree -- hence
`--root` is required and never defaults to the developer's project root.

THE MeSH SEEDING IS REUSED, NOT REIMPLEMENTED. `docker/prepare_paths.py`
already copies these two files with a sha256 check against PROVENANCE.json,
write-to-temp + `os.replace`, and a never-overwrite rule. That function takes
both directories as arguments, so CI calls it with CI's directories. A second
copy of that logic is a second copy to drift, and the hash check is the half
that matters: a truncated lookup is still valid JSON.

THE PLACEHOLDER .env IS NOT A CREDENTIAL AND CANNOT BECOME ONE. `load_env_keys()`
raises when the file is absent AND when any of the three keys is missing, so a
file has to exist for the package to import at all. The values written here are
literal placeholders. Nothing in bucket A makes a live call -- every client is
replaced through `oncotriage/agent/deps.py` -- and if one ever did, a
placeholder key gets a 401 rather than a bill. An existing .env is NEVER
overwritten, so running this against a real tree is safe.

Run from terminal:
    python .github/scripts/provision_ci_paths.py --root /path/to/ci-root

Exit codes:
    0 -- the skeleton is present and all fourteen path variables resolve
    1 -- a directory could not be created, or a path still does not resolve
"""

import argparse
import os
import sys


# The repository root, derived from this file rather than from the working
# directory: CI steps run from the checkout, but a developer may not.
_CODE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# The skeleton, as {path-variable-name: value} in the shape `prepare()` expects
# -- a value ending in a separator is a directory, a value with an extension is
# a file whose PARENT is created. It is written as a literal table rather than
# derived from `oncotriage.paths` because it CANNOT be derived: reading any path
# variable is what raises until these directories exist.
#
# The names on the left are the path variables each entry satisfies. They are
# checked against `paths.PATH_NAMES` at the end of main(), so an entry added to
# one table and not the other is a failure rather than a silent gap.
def _skeleton(root):
    data = os.path.join(root, "02- Data")
    results = os.path.join(root, "04- Results")
    return {
        "data_path":                data + os.sep,
        "data_patient_path":        os.path.join(data, "01- Patients") + os.sep,
        "data_fhir_path":           os.path.join(data, "01- Patients", "fhir") + os.sep,
        "data_trial_path":          os.path.join(data, "02- Trials") + os.sep,
        "inferences_path":          os.path.join(data, "03- Inferences Storage", "inferences.db"),
        "data_MeSH_path":           os.path.join(data, "04- MeSH") + os.sep,
        "results_path":             results + os.sep,
        "result_fhir_explore_path": os.path.join(results, "01- FHIR Exploration") + os.sep,
        "result_ablation_path":     os.path.join(results, "02- Ablation") + os.sep,
        # The MLflow file-backed tracking store (the tracking pass). A DIRECTORY
        # and nothing else -- no experiment is created here. `mlflow` creates
        # its own `meta.yaml` on first use, and a skeleton that pre-created one
        # would be fabricating tracking state, which is the line the header
        # above draws.
        "result_tracking_path":     os.path.join(results, "06- MLflow Tracking") + os.sep,
        "keys_path":                os.path.join(root, "05- Keys") + os.sep,
        "airflow_path":             os.path.join(root, "06- Airflow") + os.sep,
        "checkpoint_path":          os.path.join(root, "08- Checkpoint") + os.sep,
        # code_path globs `{root}/*Code/`. The checkout supplies it; the
        # workflow checks the repository out INTO the root under a matching
        # name. Listed so the PATH_NAMES cross-check below is exhaustive.
        "code_path":                os.path.join(root, "03- Code") + os.sep,
    }


_PLACEHOLDER_ENV = (
    "# Written by .github/scripts/provision_ci_paths.py for CI only.\n"
    "# THESE ARE NOT CREDENTIALS. load_env_keys() raises when the file is\n"
    "# absent or a key is missing, so the file has to exist for the package to\n"
    "# import. No test in CI bucket A makes a live call -- every client is\n"
    "# replaced through oncotriage/agent/deps.py.\n"
    "OPENAI_API_KEY=ci-placeholder-not-a-real-key\n"
    "QDRANT_URL=http://127.0.0.1:6333\n"
    "QDRANT_API_KEY=ci-placeholder-not-a-real-key\n"
)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Create the CI data-directory skeleton and verify every "
                    "path variable resolves.")
    parser.add_argument("--root", required=True,
                        help="the PROJECT root -- the parent of the checkout. "
                             "Required and never defaulted: the skeleton must "
                             "not be created beside a real data tree, because "
                             "two matches make _glob_one raise.")
    args = parser.parse_args(argv)

    root = os.path.abspath(args.root)
    print("=" * 74)
    print("CI PATH PROVISIONING")
    print("=" * 74)
    print(f"Project root: {root}")
    print(f"Repository:   {_CODE_DIR}")
    print()

    # `prepare()` and `seed_mesh_core()` come from the Docker provisioning
    # module. It is not a package module -- it sits in docker/ -- so it is
    # reached by putting that directory on sys.path, which is what the
    # Dockerfile does too. Importing it resolves no path: every path it reads is
    # read inside a function.
    sys.path.insert(0, os.path.join(_CODE_DIR, "docker"))
    import prepare_paths
    from oncotriage import paths

    # THE DOCKER BRANCH MAKES EVERY CHECK BELOW VACUOUS, so it is refused.
    #
    # `oncotriage/paths.py` sets
    # `IS_DOCKER = os.path.exists('/.dockerenv') or DOCKER_CONTAINER == 'true'`,
    # and under it `_RESOLVERS` becomes `_DOCKER_PATHS` -- fourteen literal
    # `/app/...` strings. Those resolve without globbing anything, so the
    # verification at the end of this function passes whatever this function
    # created, `ONCOTRIAGE_MAIN_PATH` is ignored entirely, and the tests then
    # look for a .env at `/app/.env` that nobody wrote.
    #
    # FOUND BY RUNNING, not by reading: this whole job was rehearsed inside a
    # `python:3.11-slim` container, where `/.dockerenv` exists. Provisioning
    # reported "all 13 path variables resolve" and seven bucket A tests then
    # died on `/app/.env`. GitHub-hosted runners are VMs and have no
    # `/.dockerenv`, so the shipped workflow is unaffected -- but a `container:`
    # key, a containerised self-hosted runner or a future base image would
    # reintroduce it silently, and a provisioning step that cannot fail is worse
    # than none.
    if paths.IS_DOCKER:
        print("FATAL: oncotriage.paths.IS_DOCKER is True, so the package is "
              "using its Docker path table (/app/...) and would ignore "
              "ONCOTRIAGE_MAIN_PATH entirely.")
        print("  This script provisions the LOCAL glob branch. Running it here "
              "would create directories nothing reads and then 'verify' a set "
              "of literal constants, which cannot fail.")
        print("  Cause: /.dockerenv exists" if os.path.exists("/.dockerenv")
              else "  Cause: DOCKER_CONTAINER is set to 'true'")
        print("  In CI, run this job on a VM runner (ubuntu-latest is a VM and "
              "has no /.dockerenv), not inside a `container:`.")
        return 1

    # ---- the directories -------------------------------------------------
    table = _skeleton(root)
    try:
        rows = prepare_paths.prepare(table=table)
    except RuntimeError as exc:
        print(f"FATAL: {exc}")
        return 1
    for name, value, status in rows:
        print(f"  {status:<15s} {name:<26s} {value}")
    print()

    # ---- the two vendored MeSH lookups ------------------------------------
    # REUSED from docker/prepare_paths.py: sha256-verified against
    # PROVENANCE.json, written atomically, never overwriting.
    mesh_rows = prepare_paths.seed_mesh_core(
        source_dir=os.path.join(_CODE_DIR, "docker", "mesh-core"),
        dest_dir=table["data_MeSH_path"],
    )
    for filename, status in mesh_rows:
        print(f"  mesh {status:<10s} {filename}")
    if any(status == "source-missing" for _, status in mesh_rows):
        print("  NOTE: a vendored lookup was missing; load_mesh_filter() will "
              "raise DegradedDependencyError and every test that reaches Stage 4 "
              "will fail. That is item 11a working, not a CI defect.")
    print()

    # ---- the placeholder .env --------------------------------------------
    env_path = os.path.join(table["keys_path"], ".env")
    if os.path.exists(env_path):
        print(f"  env  kept       {env_path} (already present, NOT overwritten)")
    else:
        with open(env_path, "w", encoding="utf-8") as fh:
            fh.write(_PLACEHOLDER_ENV)
        print(f"  env  written    {env_path} (placeholders, not credentials)")
    print()

    # ---- the verification, which is the point ------------------------------
    # Creating the directories proves nothing; resolving every path variable
    # through the package's own resolver does. This runs in a subprocess so the
    # root is read from the environment exactly as a test process reads it.
    print("Verifying every path variable resolves...")
    import subprocess
    probe = (
        "import os, sys\n"
        "from oncotriage import paths\n"
        "bad = []\n"
        "for n in paths.PATH_NAMES:\n"
        "    try:\n"
        "        getattr(paths, n)\n"
        "    except Exception as exc:\n"
        "        bad.append((n, type(exc).__name__, str(exc).splitlines()[0]))\n"
        "for n, t, m in bad:\n"
        "    print(f'  UNRESOLVED {n}: {t}: {m}')\n"
        "print('PATH_NAMES=' + ','.join(paths.PATH_NAMES))\n"
        "sys.exit(1 if bad else 0)\n"
    )
    env = dict(os.environ, ONCOTRIAGE_MAIN_PATH=root)
    completed = subprocess.run([sys.executable, "-c", probe], cwd=_CODE_DIR,
                               env=env, capture_output=True, text=True)
    sys.stdout.write(completed.stdout)
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        print("FATAL: at least one path variable does not resolve.")
        return 1

    # ---- the two tables must agree ----------------------------------------
    # A path variable added to oncotriage/paths.py and not to _skeleton() would
    # resolve on a developer machine and raise in CI, which is the failure mode
    # this whole file exists to remove. Derived from the probe's own output so
    # it cannot be a stale copy.
    declared = set(table)
    reported = set()
    for line in completed.stdout.splitlines():
        if line.startswith("PATH_NAMES="):
            reported = {n for n in line.split("=", 1)[1].split(",") if n}
    # main_path and _main_path_source are computed, not globbed -- they have no
    # directory to create and so are legitimately absent from the skeleton.
    reported -= {"main_path", "_main_path_source"}
    missing = reported - declared
    extra = declared - reported
    if missing or extra:
        print()
        print("FATAL: the skeleton and oncotriage/paths.py disagree.")
        if missing:
            print(f"  in paths.py, not provisioned here: {sorted(missing)}")
        if extra:
            print(f"  provisioned here, not in paths.py: {sorted(extra)}")
        return 1

    print(f"  all {len(reported)} path variables resolve.")
    print()
    print("Provisioning complete. No patient data, trial corpus, inference")
    print("database or credential was created.")
    return 0


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Aug  8 2026

@author: ramyalsaffar
"""
