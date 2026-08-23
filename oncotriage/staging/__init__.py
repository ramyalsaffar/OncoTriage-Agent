# Staging Subpackage
####################

"""
Stage the project ROOT tree into Amazon S3.

FOUR MODULES, ONE JOB EACH:

    exclusions.py    the rulings -- loads and validates s3_staging_exclusions.json,
                     classifies a path, and cross-checks PATH_NAMES.
    secrets_scan.py  the guarantee -- a filename AND content scan over the set
                     that is about to upload, and a hard refusal on any hit.
    manifest.py      the walk, the dry-run report and the cost estimate.
    s3_sync.py       the AWS half: preflight, the bucket guardrails, the upload.

NOTHING HERE IMPORTS boto3 AT MODULE SCOPE, and that is a requirement rather
than a style: the DRY RUN must run on a machine with no AWS SDK and no
credentials at all, which is the machine this subpackage was written on. The
import lives inside the functions that need it, which is the same third-party
in-a-function-body exemption `import icd10` and `import torch` already carry
(see check 1b in tests/test_package_invariants.py -- it forbids deferring a
PACKAGE import and deliberately exempts third-party ones).

THE ORDER OF THE TWO GATES IS LOAD-BEARING. The secrets scan runs BEFORE any
network call and its refusal is terminal: nothing uploads if one candidate file
looks credential-shaped. It is not a filter that drops the offending file,
because a filter answers "which files are safe" and this project needs the
answer to "is this whole set safe", and those differ exactly when the scan is
wrong about one file.
"""
