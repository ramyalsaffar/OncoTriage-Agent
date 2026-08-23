# Staging AWS Preflight
#######################

"""
The AWS half: preflight now, bucket guardrails and upload once credentials exist.

WHAT IS HERE AND WHAT IS DELIBERATELY NOT. ``preflight()`` is complete and is
exercised by the standing test. The bucket creation, the five guardrails, the
sync and the Qdrant snapshot are NOT written yet, and that is a decision rather
than an omission: this project's standing rule is that every assertion must be
shown to FAIL when the thing it checks is broken, and there is no AWS account
here to break. Shipping four hundred lines of unverifiable upload code behind a
flag would put code in the tree that has never been run against the service it
names, which is worse than an honest refusal -- and the refusal is itself
testable, so the gate that protects the money is the part that is verified.

``execute_refusal_reason()`` is what ``--execute`` calls. It returns a reason
string when the run must not proceed and ``None`` when it may, so the entry
point has one place to ask and the test has one function to drive.

boto3 IS IMPORTED INSIDE THE FUNCTIONS. The dry run must work on a machine with
no AWS SDK -- which is the machine this was written on -- so a module-scope
import would make the free half depend on the paid half. This is the same
third-party-in-a-function-body exemption ``import icd10`` and ``import torch``
already carry; check 1b forbids deferring a PACKAGE import and exempts these.
"""

from oncotriage import config
from oncotriage.observability import get_logger

log = get_logger(__name__)


#------------------------------------------------------------------------------


# The three states preflight can report. CLOSED, and a caller may branch on it
# exhaustively -- deps.RESOLUTION_STATES' shape, and the reason it is a
# vocabulary rather than a bool is that the remedies differ: an absent SDK is a
# pip install, absent credentials are an `aws configure`, and a wrong region is
# a one-line edit. A bool would send all three to the same page.
PREFLIGHT_OK = "ok"
PREFLIGHT_NO_SDK = "no_sdk"
PREFLIGHT_NO_CREDENTIALS = "no_credentials"
PREFLIGHT_WRONG_REGION = "wrong_region"
PREFLIGHT_CALL_FAILED = "call_failed"

PREFLIGHT_STATES = (PREFLIGHT_OK, PREFLIGHT_NO_SDK, PREFLIGHT_NO_CREDENTIALS,
                    PREFLIGHT_WRONG_REGION, PREFLIGHT_CALL_FAILED)


class PreflightResult:
    """What preflight established. No decorator (check 2i)."""

    __slots__ = ("state", "detail", "identity", "region")

    def __init__(self, state, detail, identity=None, region=None):
        self.state = state
        self.detail = detail
        self.identity = identity   # {"account", "arn", "user_id"} or None
        self.region = region

    def ok(self):
        return self.state == PREFLIGHT_OK


def preflight(session_factory=None):
    """Establish that AWS is usable and that it is the RIGHT AWS.

    ``session_factory`` is the seam: ``None`` builds a real boto3 session, and
    the standing test passes a stand-in so every branch is driven with no
    network and no credentials.

    IT NEVER RETURNS OR LOGS A SECRET. The identity block is account id, ARN and
    user id -- all of which are non-secret by design and are exactly what
    ``aws sts get-caller-identity`` prints. No access key, no session token.
    """
    if session_factory is None:
        try:
            import boto3  # noqa: PLC0415 -- third-party, deferred on purpose
        except ImportError as exc:
            return PreflightResult(
                PREFLIGHT_NO_SDK,
                f"boto3 is not installed ({exc}). Install it with "
                f"`pip install -e .` from 03- Code/, which now declares it.")
        session_factory = boto3.session.Session

    try:
        session = session_factory()
    except Exception as exc:                      # noqa: BLE001
        return PreflightResult(
            PREFLIGHT_CALL_FAILED,
            f"could not build an AWS session: {type(exc).__name__}: {exc}")

    credentials = None
    try:
        credentials = session.get_credentials()
    except Exception as exc:                      # noqa: BLE001
        return PreflightResult(
            PREFLIGHT_NO_CREDENTIALS,
            f"the credential chain raised: {type(exc).__name__}: {exc}")

    if credentials is None:
        return PreflightResult(
            PREFLIGHT_NO_CREDENTIALS,
            "no AWS credentials resolved. Run `aws configure` with the IAM "
            "user's access key, or export AWS_ACCESS_KEY_ID / "
            "AWS_SECRET_ACCESS_KEY. This tool never creates credentials.")

    region = session.region_name
    if region != config.S3_STAGING_REGION:
        return PreflightResult(
            PREFLIGHT_WRONG_REGION,
            f"the session region is {region!r} and this project stages to "
            f"{config.S3_STAGING_REGION!r} (config.S3_STAGING_REGION). A "
            f"bucket's region is fixed for its lifetime, so this refuses "
            f"rather than creating one on the wrong continent.",
            region=region)

    try:
        identity = session.client("sts").get_caller_identity()
    except Exception as exc:                      # noqa: BLE001
        return PreflightResult(
            PREFLIGHT_CALL_FAILED,
            f"sts:GetCallerIdentity failed: {type(exc).__name__}: {exc}",
            region=region)

    return PreflightResult(
        PREFLIGHT_OK,
        "credentials resolved and sts:GetCallerIdentity answered",
        identity={
            "account": identity.get("Account"),
            "arn": identity.get("Arn"),
            "user_id": identity.get("UserId"),
        },
        region=region)


# The upload half is not built. This is the one place that says so, so the
# entry point does not have to carry the argument and the test has one function
# to drive.
UPLOAD_NOT_IMPLEMENTED = (
    "The upload half is not built yet, on purpose.\n"
    "\n"
    "This pass built and verified the LOCAL half only: the rulings, the "
    "secrets refusal,\n"
    "the walk, the manifest and the cost estimate. Bucket creation, the five "
    "guardrails,\n"
    "the sync, the Qdrant snapshot and the post-sync verification need an AWS "
    "account to\n"
    "be written against and, more to the point, to be shown to FAIL when "
    "broken -- which\n"
    "is this project's standing rule for every assertion. Untested upload code "
    "behind a\n"
    "flag is worse than no upload code: it looks finished.\n"
    "\n"
    "What to run when credentials exist:\n"
    "    python s3_stage.py --check-aws        # preflight only, no writes\n"
    "then this file gains create_bucket / sync / snapshot, each with the "
    "guardrail read\n"
    "back after it is set.")


def execute_refusal_reason(preflight_result):
    """Why ``--execute`` must not proceed, or ``None`` if it may.

    One function so the entry point has one question to ask and the standing
    test has one thing to drive. Today it refuses unconditionally once
    preflight passes, because the upload is not built; that arm is what the
    test pins, so the day the upload lands the pin fails and forces this
    docstring to be rewritten rather than quietly outlived.
    """
    if not preflight_result.ok():
        return (f"AWS preflight did not pass ({preflight_result.state}).\n"
                f"  {preflight_result.detail}")
    return UPLOAD_NOT_IMPLEMENTED
