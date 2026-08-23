# S3 Staging
############

"""
Stage the project ROOT tree into Amazon S3. Dry run by default.

Run from terminal:
    python s3_stage.py                      # DRY RUN: manifest, then STOP
    python s3_stage.py --json-out plan.json # ...and write the manifest as JSON
    python s3_stage.py --check-aws          # preflight only; no walk, no writes
    python s3_stage.py --execute            # the real sync (see below)

UNNUMBERED, ON THE fixture_capture.py PRECEDENT. The numbered range 04-49 is
the pipeline; this is infrastructure beside it, like `fixture_capture.py`,
`bedrock_probe.py` and `evaluation_run.py`. Numbering it would put a staging
tool in a sequence a reader walks to learn the pipeline.

THE DRY RUN IS THE GATE. A bare invocation walks the tree, applies the rulings
in `s3_staging_exclusions.json`, runs the secrets scan over what survives,
prints the manifest and the cost, and STOPS. Nothing uploads without
`--execute`, and `--execute` re-walks and re-scans rather than trusting the
report -- a manifest that was clean an hour ago says nothing about a file added
since.

WHAT `--execute` DOES TODAY: refuses, and says why. The upload half is not
built (see oncotriage/staging/s3_sync.py:UPLOAD_NOT_IMPLEMENTED). The refusal
is real code with a real test rather than a stub, because the gate that
protects the money is the part worth verifying first.

EXIT CODES
    0 -- the dry run completed and the secrets scan was clean
    1 -- the secrets scan REFUSED, or the manifest is malformed
    2 -- --execute was asked for and could not proceed (preflight, or the
         upload half not being built). Distinct from 1 because "nothing is
         wrong with your tree, the tool cannot do this yet" and "your tree
         contains a credential" are different findings with different remedies.
"""

import argparse
import json
import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# See the same block in "04- FHIR Generate Data.py" and "16- Database Query.py".
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

# EXPLICIT, not transitive. `import oncotriage` alone does not bind the `paths`
# submodule as an attribute -- it only appears because something below imports
# it, and main() reads `oncotriage.paths.main_path`. Relying on that is one
# import-graph edit away from an AttributeError in the entry point.
from oncotriage import paths as _paths
from oncotriage.observability import console
from oncotriage.staging.exclusions import (
    ManifestError,
    PathNamesUnclassified,
    build_plan,
    cross_check_path_names,
    load_manifest,
)
from oncotriage.staging.manifest import build_report, render_report, walk
from oncotriage.staging.s3_sync import execute_refusal_reason, preflight
from oncotriage.staging.secrets_scan import (
    SecretsRefusal,
    refuse_if_dirty,
    scan_files,
)


#------------------------------------------------------------------------------


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Stage the project root tree into Amazon S3. Dry run by "
                    "default; --execute is required to upload anything.")
    parser.add_argument(
        "--execute", action="store_true",
        help="Perform the real sync. Without this the run stops after the "
             "manifest.")
    parser.add_argument(
        "--check-aws", action="store_true",
        help="Run the AWS preflight and exit. No walk, no scan, no writes.")
    parser.add_argument(
        "--manifest", default=None,
        help="Path to the exclusion manifest. Default: "
             "{code_path}/s3_staging_exclusions.json")
    parser.add_argument(
        "--root", default=None,
        help="Project root to stage. Default: oncotriage.paths.main_path. "
             "Provided so a rehearsal can run against a small fabricated tree.")
    parser.add_argument(
        "--json-out", default=None,
        help="Also write the manifest as JSON to this path.")
    parser.add_argument(
        "--no-measure-excluded", action="store_true",
        help="Skip sizing the excluded subtrees. Faster; loses the 'what the "
             "rulings removed' column.")
    return parser.parse_args(argv)


def _report_preflight(result):
    console.out("")
    console.out("AWS preflight")
    console.out("-" * 78)
    console.out(f"  state  : {result.state}")
    console.out(f"  detail : {result.detail}")
    if result.region:
        console.out(f"  region : {result.region}")
    if result.identity:
        # Account id, ARN and user id only. These are what
        # `aws sts get-caller-identity` prints and none of them is a secret.
        console.out(f"  account: {result.identity['account']}")
        console.out(f"  arn    : {result.identity['arn']}")
        console.out(f"  user id: {result.identity['user_id']}")
    console.out("")


def main(argv=None):
    args = _parse_args(argv)

    if args.check_aws:
        _report_preflight(preflight())
        return 0

    # ---- the rulings -----------------------------------------------------
    try:
        manifest = load_manifest(args.manifest)
    except ManifestError as exc:
        console.out(f"\nMANIFEST ERROR\n{exc}\n")
        return 1
    plan = build_plan(manifest)

    root = args.root if args.root else _paths.main_path
    root = os.path.abspath(root)

    # ---- the PATH_NAMES cross-check --------------------------------------
    # Runs BEFORE the walk. Inclusion is "the root minus the rulings", so a
    # path variable the manifest has never heard of is the one way a future
    # directory gets missed in silence. It raises; the remedy is one edit.
    #
    # SKIPPED for a fabricated --root, because PATH_NAMES resolves against the
    # real tree and comparing it to somebody else's root would report every
    # member as outside it.
    if not args.root:
        try:
            cross_check_path_names(plan)
            console.out(f"[cross-check] all {len(_paths.PATH_NAMES)} "
                        f"PATH_NAMES members are staged or "
                        f"excluded-with-a-reason")
        except PathNamesUnclassified as exc:
            console.out(f"\nPATH_NAMES CROSS-CHECK FAILED\n{exc}\n")
            return 1

    # ---- the walk --------------------------------------------------------
    console.out(f"[walk] {root}")
    result = walk(root, plan, measure_excluded=not args.no_measure_excluded)
    console.out(f"[walk] {len(result.files):,} candidate objects")

    # ---- the secrets scan, over exactly what would upload -----------------
    console.out("[scan] filename + content, over the staged set")
    scan = scan_files(((abspath, relpath) for abspath, relpath, _s in result.files),
                      plan)

    report = build_report(result, plan, scan)
    render_report(report)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
        console.out(f"[json] manifest written to {args.json_out}")

    # ---- the refusal -----------------------------------------------------
    # AFTER the report, deliberately. The report is what tells an operator
    # which files are the problem and how big the run was; raising above it
    # would send them round the loop once per finding.
    try:
        refuse_if_dirty(scan)
    except SecretsRefusal as exc:
        console.out(f"\n{exc}\n")
        return 1

    if not args.execute:
        console.out("DRY RUN COMPLETE. Nothing was uploaded.")
        console.out("Re-run with --execute to perform the sync.")
        return 0

    # ---- --execute -------------------------------------------------------
    pre = preflight()
    _report_preflight(pre)
    refusal = execute_refusal_reason(pre)
    if refusal:
        console.out(f"CANNOT EXECUTE\n\n{refusal}\n")
        return 2

    console.out("CANNOT EXECUTE: unreachable -- execute_refusal_reason() "
                "returned None but no upload is implemented.")
    return 2


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Aug 22 2026

@author: ramyalsaffar
"""
