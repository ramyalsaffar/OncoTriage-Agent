# Provider Quotas: Reading Back What the Account Actually Has
#############################################################

"""Ask the provider what quota it is applying, read only, and never guess.

WHY THIS EXISTS, AND IT IS A PROMISE THIS REPOSITORY ALREADY MADE.
``config.PROVIDER_REQUESTS_PER_MINUTE``'s own docstring says of the shipped
arm's 10: "IT HAS NOT BEEN READ BACK FROM THE PROVIDER:
``provider_quotas.lookup_applied_quotas()`` is the read that would confirm it,
and on this machine it answers ``credentials_absent``". That module did not
exist, so the sentence named a function nobody could call -- the
``PASSWORD_SOURCE_ARGUMENT`` shape, applied to a whole module. This is it.

WHAT IT DOES AND DOES NOT DO
----------------------------
It answers ONE question -- "what does the provider say the applied quota is for
this scope" -- as a record carrying a CLOSED state. It is a MANAGEMENT read:
AWS Service Quotas' ``GetServiceQuota`` is not a model endpoint, bills nothing,
and consumes no tokens.

**IT NEVER SUBSTITUTES A DEFAULT AND NEVER WRITES CONFIGURATION.** Every
failure is a state with a reason; none of them returns a number. The whole
value of the read is that a figure an operator typed can be COMPARED against
one the provider reports, and a lookup that invented a value on failure would
destroy exactly that.

WHY IT LANDS ON THE UNKNOWN BRANCH HERE, AND FOR TWO INDEPENDENT REASONS
------------------------------------------------------------------------
Measured on this machine, 2026-09-11, rather than predicted:

  * ``boto3.Session().get_credentials()`` is ``None``. This project
    authenticates Bedrock with a BEARER TOKEN
    (``settings.ENV_AWS_BEARER_TOKEN_BEDROCK``), which the
    ``bedrock-runtime`` data plane accepts and which **cannot sign a Service
    Quotas request** -- SigV4 needs an access key and a secret. So the read
    needs a credential this project does not otherwise use, and its absence is
    reported rather than worked around.
  * no quota CODE is recorded for any scope. ``GetServiceQuota`` is addressed
    by ``(ServiceCode, QuotaCode)``, and AWS's quota codes are opaque strings
    (``L-…``) that differ per model and per quota. Nothing in this repository
    records one, and INVENTING one would make the read return a confident
    answer about a quota nobody identified -- which is worse than no read.
    ``config.PROVIDER_QUOTA_LOOKUP_CODES`` is where an operator puts the pair
    they read off the console's own URL, and ``None`` there is the honest
    "not recorded" this module reports.

So the live use is the operator's later step, exactly as the brief for this
module says: fill in the code pair, supply a signing credential, run it, and
compare. Until then it answers, by name, which of the two it is missing.

IMPORTS NOTHING FROM THE PROJECT BUT ``config`` AND ``observability``, on
``provider_resilience``'s footing, so anything may import it without a cycle.
boto3 is imported INSIDE the one function that needs it -- the same
third-party-in-a-function-body exemption ``import icd10`` and
``oncotriage/staging/s3_sync.py``'s own boto3 carry -- so importing this module
on a machine without boto3 works and reports it.
"""

from typing import Callable, Dict, NamedTuple, Optional

from oncotriage import config
from oncotriage import settings
from oncotriage.observability import get_logger


log = get_logger(__name__)


# ===========================================================================
# THE CLOSED STATE VOCABULARY
# ===========================================================================

LOOKUP_OK = "ok"
LOOKUP_NO_SDK = "no_sdk"
LOOKUP_CREDENTIALS_ABSENT = "credentials_absent"
LOOKUP_CODE_NOT_RECORDED = "code_not_recorded"
LOOKUP_CALL_FAILED = "call_failed"
LOOKUP_STATES = (LOOKUP_OK, LOOKUP_NO_SDK, LOOKUP_CREDENTIALS_ABSENT,
                 LOOKUP_CODE_NOT_RECORDED, LOOKUP_CALL_FAILED)
"""What is known about one scope's applied quota AFTER asking. CLOSED.

FIVE MEMBERS BECAUSE EACH NAMES A DIFFERENT NEXT ACTION, which is the same test
``config.QUOTA_NOT_APPLICABLE``'s three states are built on:

  ``ok``                  a number came back; compare it with the configured one.
  ``no_sdk``              ``pip install boto3``.
  ``credentials_absent``  supply SIGNING credentials -- a bearer token cannot
                          sign this call, so "the Bedrock key is set" is not
                          the same fact and must not read as one.
  ``code_not_recorded``   put the (ServiceCode, QuotaCode) pair in
                          ``config.PROVIDER_QUOTA_LOOKUP_CODES``; nothing is
                          wrong with the account.
  ``call_failed``         the call was made and refused -- a permission, a
                          Region, a throttle. The reason is carried verbatim.

Folding the middle three into one "unavailable" would send an operator to
their credentials for a missing constant, which is the misdiagnosis this
vocabulary exists to prevent."""


class QuotaLookup(NamedTuple):
    """One scope's answer. ``applied`` is a number ONLY when state is ``ok``."""

    scope: str
    state: str
    applied: Optional[float]
    configured: Optional[object]
    detail: str
    service_code: Optional[str]
    quota_code: Optional[str]

    def ok(self) -> bool:
        return self.state == LOOKUP_OK

    def agrees(self) -> Optional[bool]:
        """Does the provider's number match the configured one? None if unknown.

        ``None`` RATHER THAN ``False`` WHEN THE READ DID NOT HAPPEN, and that
        is the whole reason this is a method and not a comparison at the call
        site: ``lookup.applied == lookup.configured`` is ``False`` for a lookup
        that never ran, which reads as "the provider disagrees with you" -- the
        exact false accusation a three-state answer exists to avoid.
        """
        if self.state != LOOKUP_OK or self.applied is None:
            return None
        if not isinstance(self.configured, (int, float)):
            return None
        return float(self.applied) == float(self.configured)


def _assert_state_vocabulary_is_closed() -> None:
    """Refuse at import if the tuple and the constants disagree.

    A ``RuntimeError`` and not an ``assert``: ``python -O`` deletes asserts, and
    a state that is not a member would be reported to an operator as an answer
    this module never defined.
    """
    if len(set(LOOKUP_STATES)) != len(LOOKUP_STATES):
        raise RuntimeError(
            f"provider_quotas.LOOKUP_STATES holds a duplicate: {LOOKUP_STATES}")


_assert_state_vocabulary_is_closed()


# ===========================================================================
# THE READ
# ===========================================================================

def lookup_applied_quotas(
        session_factory: Optional[Callable[[], object]] = None,
        scopes=None) -> Dict[str, QuotaLookup]:
    """``{scope: QuotaLookup}`` for every scope, asking the provider once each.

    READ ONLY, AND BILLS NOTHING. ``GetServiceQuota`` is a Service Quotas
    management call: it is not a model endpoint, it consumes no tokens, and it
    is therefore not a ``spend.BILLED_SITES`` entry. It is also not paced --
    ``provider_resilience``'s pacer governs the endpoints whose quota this
    reads, and pacing the read against the quota it is asking about would be
    circular.

    Args:
        session_factory: the seam, on ``staging.s3_sync.preflight``'s
            precedent. ``None`` builds a real ``boto3.session.Session``; a
            stand-in lets every branch be driven with no network and no
            credentials, which is how this module is tested.
        scopes: which scopes to ask about. Default: every member of
            ``config.PROVIDER_QUOTA_SCOPES``, so a scope added without a
            lookup code appears in the answer as ``code_not_recorded`` rather
            than being silently absent.

    Returns:
        One record per scope. NEVER raises: this is a diagnostic, and a
        diagnostic that dies on a missing credential is one an operator cannot
        use to find out that the credential is missing.
    """
    scopes = tuple(config.PROVIDER_QUOTA_SCOPES if scopes is None else scopes)
    out: Dict[str, QuotaLookup] = {}

    def _record(scope, state, detail, applied=None, codes=(None, None)):
        rpm, _tpm = (config.provider_quota(scope)
                     if scope in config.PROVIDER_REQUESTS_PER_MINUTE
                     else (None, None))
        return QuotaLookup(scope=scope, state=state, applied=applied,
                           configured=rpm, detail=detail,
                           service_code=codes[0], quota_code=codes[1])

    # THE SESSION IS BUILT ONCE FOR EVERY SCOPE, not once per scope: building
    # one resolves the credential chain, and doing that N times would report N
    # identical failures for one fact.
    session = None
    session_error = None
    if session_factory is None:
        try:
            import boto3  # noqa: PLC0415 -- third-party, deferred on purpose
        except ImportError as exc:
            session_error = (
                LOOKUP_NO_SDK,
                f"boto3 is not installed ({exc}). Install it with "
                f"`pip install -e .` from 03- Code/, which declares it.")
        else:
            session_factory = boto3.session.Session
    if session_error is None:
        try:
            session = session_factory()
        except Exception as exc:                              # noqa: BLE001
            session_error = (
                LOOKUP_CALL_FAILED,
                f"could not build an AWS session: {type(exc).__name__}: {exc}")

    if session_error is None:
        try:
            credentials = session.get_credentials()
        except Exception as exc:                              # noqa: BLE001
            credentials = None
            session_error = (
                LOOKUP_CREDENTIALS_ABSENT,
                f"the credential chain raised: {type(exc).__name__}: {exc}")
        else:
            if credentials is None:
                # THE MESSAGE SEPARATES THE TWO CREDENTIALS ON PURPOSE. An
                # operator who has set AWS_BEARER_TOKEN_BEDROCK has "a Bedrock
                # credential" and still cannot make this call, and being told
                # "no AWS credentials" without that distinction sends them to
                # re-check a variable that is correctly set.
                # THE VARIABLE IS NAMED THROUGH THE MODULE-SCOPE IMPORT, not
                # through an inline `__import__` inside the f-string. The first
                # version did the latter across two lines, which is a
                # MULTI-LINE EXPRESSION INSIDE AN F-STRING -- PEP 701, Python
                # 3.12 -- and `pyproject.toml` declares >= 3.10 while the image
                # runs 3.11, where it is a SyntaxError. So it would have parsed
                # on this developer interpreter and made `import
                # oncotriage.provider_quotas` fail in the container. That is
                # the exact trap the cost-report date line hit once already.
                session_error = (
                    LOOKUP_CREDENTIALS_ABSENT,
                    f"no SIGNING credentials resolved. Service Quotas needs "
                    f"SigV4 -- an access key and a secret -- which is NOT what "
                    f"this project's Bedrock bearer token "
                    f"({settings.ENV_AWS_BEARER_TOKEN_BEDROCK}) provides: that "
                    f"token authenticates the bedrock-runtime DATA PLANE only. "
                    f"Run `aws configure`, or export AWS_ACCESS_KEY_ID / "
                    f"AWS_SECRET_ACCESS_KEY. This module never creates "
                    f"credentials and never writes configuration.")

    for scope in scopes:
        codes = config.PROVIDER_QUOTA_LOOKUP_CODES.get(scope)
        if session_error is not None:
            out[scope] = _record(scope, session_error[0], session_error[1],
                                 codes=codes or (None, None))
            continue
        if not codes or not all(codes):
            out[scope] = _record(
                scope, LOOKUP_CODE_NOT_RECORDED,
                f"no (ServiceCode, QuotaCode) pair is recorded for {scope!r}. "
                f"GetServiceQuota is addressed by that pair and AWS's quota "
                f"codes are opaque per-model strings, so this module will not "
                f"guess one. Read the pair off the Service Quotas console (it "
                f"is in the page's own URL) and put it in "
                f"config.PROVIDER_QUOTA_LOOKUP_CODES[{scope!r}].")
            continue
        service_code, quota_code = codes
        try:
            client = session.client("service-quotas")
            answer = client.get_service_quota(ServiceCode=service_code,
                                              QuotaCode=quota_code)
            value = (answer or {}).get("Quota", {}).get("Value")
        except Exception as exc:                              # noqa: BLE001
            out[scope] = _record(
                scope, LOOKUP_CALL_FAILED,
                f"GetServiceQuota({service_code!r}, {quota_code!r}) raised "
                f"{type(exc).__name__}: {str(exc)[:300]}", codes=codes)
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            # A RESPONSE THAT ARRIVED AND CARRIES NO NUMBER IS `call_failed`
            # AND NOT `ok`, because `ok` promises `applied` is a number and a
            # consumer branching on the state must be able to rely on that.
            out[scope] = _record(
                scope, LOOKUP_CALL_FAILED,
                f"GetServiceQuota({service_code!r}, {quota_code!r}) answered "
                f"with no numeric Quota.Value (got {value!r})", codes=codes)
            continue
        out[scope] = _record(scope, LOOKUP_OK,
                             f"Service Quotas reports {value} for "
                             f"{service_code}/{quota_code}",
                             applied=float(value), codes=codes)
    return out


def report_lines(lookups: Optional[Dict[str, QuotaLookup]] = None) -> list:
    """The read as the lines an operator reads. Always non-empty.

    IT PRINTS THE CONFIGURED FIGURE BESIDE THE APPLIED ONE, and says when the
    two could not be compared rather than printing a bare number that looks
    like a confirmation. A lookup that did not happen is the common case today
    and the block says which of the four reasons it was.
    """
    lookups = lookup_applied_quotas() if lookups is None else lookups
    lines = ["PROVIDER QUOTAS AS THE PROVIDER REPORTS THEM", "-" * 60]
    if not lookups:
        lines.append("  no scope was asked about")
    for scope in sorted(lookups):
        got = lookups[scope]
        agrees = got.agrees()
        verdict = ("not compared -- the read did not happen" if agrees is None
                   else "AGREES with the configured value" if agrees
                   else "DISAGREES with the configured value")
        lines.append(f"  scope {scope:<20} state {got.state}")
        lines.append(f"    configured {got.configured!r}; applied "
                     f"{got.applied!r}: {verdict}")
        lines.append(f"    {got.detail}")
    lines.append("  READ ONLY. This never writes configuration: a figure that "
                 "disagrees is an edit an operator makes deliberately.")
    return lines


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Sep 11 2026

@author: ramyalsaffar
"""
