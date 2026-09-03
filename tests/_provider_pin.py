# Shared Stage 5 Provider Pin for the Tests That Cover the Dormant OpenAI Arm
############################################################################

"""One implementation of "this file's subject is the OpenAI Stage 5 request".

NO ``test_`` PREFIX, DELIBERATELY, on ``tests/_control_harness.py``'s argument:
every runner this project has -- the CI bucket table's ``--run A``, ``pytest
tests/``, a ``for f in tests/test_*.py`` loop -- selects on that prefix, and a
file holding no checks would report "0 passed" and be counted as a file that
ran. ``ci_test_buckets.py``'s completeness check selects on the same prefix, so
this file is correctly outside its table rather than a hole in it.

WHY IT EXISTS
-------------
``config.MATCHING_PROVIDER`` ships ``"bedrock_anthropic"``. Two dozen test
files install a Stage 5 stand-in at ``deps.OPENAI_CLIENT`` and then drive the
node, and every one of them was written when the shipped provider was
``"openai"`` -- so their stand-in sat on the seam the dispatch happened to
reach. At the new default the dispatch reaches ``deps.BEDROCK_ANTHROPIC_CLIENT``
and ``converse`` instead, and the consequences are the two this pin removes:

  * THE STAND-IN IS NEVER CALLED, so every assertion about what Stage 5 sent
    compares against an empty recorder. MEASURED across CI bucket A at the
    moment of the flip: 27 files failed and seven of them ABORTED on a
    ``KeyError`` / ``IndexError`` / ``TypeError`` reading a result the node
    never produced.
  * ``config.get_bedrock_anthropic_client()`` BUILDS FOR REAL. Its flag guard
    is satisfied at the new default, so a test with no AWS credential reaches
    ``boto3.client("bedrock-runtime", ...)`` and botocore probes the instance
    metadata service. MEASURED, same run: **242 outbound attempts to
    169.254.169.254 across bucket A, against 0 before the flip.** No request
    reached Bedrock and nothing was billed on this machine -- there is no
    credential here for the chain to find -- but the same suite on a host that
    HAS one (an EC2 runner, a developer who has exported
    ``AWS_BEARER_TOKEN_BEDROCK``, or any process that called
    ``paths.load_env_keys()`` first -- which at the time loaded EVERY key in
    this project's ``05- Keys/.env``, and that file carries that name; the
    loader is an ALLOWLIST now, and the two Bedrock names are ON it, so
    this route is narrower and NOT closed) would
    have issued live, billed Converse requests from a test suite that reports
    it makes none.

ONE OWNER RATHER THAN A BLOCK PER FILE, AND THE REASON IS MEASURED HISTORY.
The default-flip pass pinned the retained call-mode arm in seven files by hand
and got the RELEASE placement wrong in three of them -- below the summary
rather than above it, so the outcome still decided the exit code while being
absent from the number the summary printed, i.e. a run that reported "0 failed"
and exited 1. Twenty-four hand-written copies of that block is twenty-four
chances to repeat it. Here the pin and the release are one implementation with
one argument, each file spends two lines plus one check, and ``released()``
returns something a check can fail on.

WHAT THIS PIN COSTS, STATED RATHER THAN DISCOVERED
--------------------------------------------------
The SHIPPED arm's Stage 5 behaviour is NOT covered by any file that installs
this pin. Everything those files measure -- the input packer, emission
provenance, the out-of-set detector, verdict normalization, the state channels,
the persisted packing columns, the spend gate's Stage 5 half -- is measured on
the dormant OpenAI request path. On the shipped Converse path those subjects are
covered by ``tests/test_agent_bedrock_anthropic_adapter.py`` and
``tests/test_agent_bedrock_anthropic_per_trial.py`` alone. That is the same
trade the default-flip pass recorded for the retained grouped call-mode arm,
and it is a real coverage gap rather than a formality.

IT PINS ``config``, WHICH IS THE SEAM THE NODE READS, and it does NOT pretend
to be ``config.pin_matching_call_mode``'s kind of pin. That one exists because
the fixture harness is an OPERATOR-FACING PROGRAM whose own reports must not
say the project is configured one way while the process runs another, so the
constant and the pin are kept apart by an owner function. A test file is not
such a program: nothing reads its process's configuration afterwards, and
``config.MATCHING_PROVIDER`` is the value every consumer resolves live. So this
assigns the attribute and restores it, which is exactly what the three
provider-aware test files in this suite already do inside their own
``provider()`` / ``settings()`` context managers -- and unlike them it holds for
a whole file rather than for one block.

ONE PIN PER PROCESS, AND A SECOND ONE IS REFUSED RATHER THAN NESTED. Each of
these files is its own process under every runner this project has -- the CI
bucket table's ``--run A``, ``make serial-tests``, ``python tests/x.py`` -- so
the refusal is unreachable in normal use. It is there for the one arrangement
that would reach it: several of these modules imported into ONE interpreter
(``pytest tests/``, which this project does not support and which reports "no
tests collected"), where a file that ABORTED before its release would leave a
pin behind. Nesting there would have the second file release to the FIRST
file's pinned value rather than to the shipped default, which is a silent leak
into whatever ran next; refusing is loud and names the holder.

THE PIN IS A HARD GUARD AND NOT A ``check()``. A pin that did not take leaves
every assertion below it silently measuring the other arm: not one failure but
every failure, each with a misleading message, which is the case this suite
already reserves an abort for (a wrong project root). ``ProviderPinError``
propagates out of module scope, so the file dies naming the arm it found rather
than reporting two dozen misleading failures. It is a ``RuntimeError`` subclass
and not a ``ValueError`` -- the ``UnknownModelPricingError`` precedent -- so a
stray ``except ValueError`` in a harness cannot eat it, and it is CATCHABLE by
name so the checks that exercise the guard can drive it.
"""

from oncotriage import config


_STATE = {"who": None, "previous": None}


class ProviderPinError(RuntimeError):
    """The pin could not be established, or was released without being set."""


def pin_openai_arm(who, out=print):
    """Pin Stage 5 to the OpenAI provider for this process.

    Args:
        who: the caller, named in the notice and in any refusal. A file name.
        out: where the notice goes. Injectable on ``degradation.print_report``'s
            argument -- a line nothing can exercise is how a line comes to be
            wrong -- and defaulting to ``print`` because a test file's output IS
            its report.

    Returns:
        The value that was in force before the pin.

    Raises:
        ProviderPinError: a pin is already installed, or the assignment did not
            reach ``config.MATCHING_PROVIDER``.
    """
    if _STATE["who"] is not None:
        raise ProviderPinError(
            f"{who}: a provider pin installed by {_STATE['who']!r} is already "
            f"in force. Two pins in one process means one of them releases to "
            f"the other's value, so this refuses rather than nesting.")

    previous = config.MATCHING_PROVIDER
    config.MATCHING_PROVIDER = config.MATCHING_PROVIDER_OPENAI

    # ASSERTED, NOT ASSUMED. The whole value of a hard guard is that it fires
    # here rather than as two dozen misleading failures below.
    if config.MATCHING_PROVIDER != config.MATCHING_PROVIDER_OPENAI:
        raise ProviderPinError(
            f"{who}: the provider pin did not take -- config.MATCHING_PROVIDER "
            f"reads {config.MATCHING_PROVIDER!r} after being assigned "
            f"{config.MATCHING_PROVIDER_OPENAI!r}.")

    _STATE["who"] = who
    _STATE["previous"] = previous

    # LOUD EVEN WHEN IT OVERRODE NOTHING, on `pin_call_mode_for_fixture_process`
    # 's argument: a notice that appears only when the pin changed something is
    # absent from every log taken before a flip and present after it, so the
    # reader most likely to be confused -- somebody comparing two runs across a
    # provider change -- is exactly the reader it would fail.
    out(f"[provider pin] {who}: Stage 5 PINNED to "
        f"{config.MATCHING_PROVIDER_OPENAI!r} for this process; without the "
        f"pin this process would have run {previous!r}. This file's subject is "
        f"the OpenAI request shape, which is dormant at the shipped default "
        f"and therefore covered here and nowhere else.")
    return previous


def release_openai_arm(out=print):
    """Restore the value that was in force before ``pin_openai_arm``.

    CALLED ABOVE THE SUMMARY, never below it. A release below the results line
    still decides the exit code while being absent from the number the summary
    printed, which is a run that reports "0 failed" and exits non-zero.

    Returns:
        ``(who, previous, restored_ok)`` -- a triple a check can fail on. The
        first two are recorded BEFORE the restore, so "there was a pin to
        release" cannot be satisfied by a process that never installed one.

    Raises:
        ProviderPinError: no pin was installed.
    """
    if _STATE["who"] is None:
        raise ProviderPinError(
            "release_openai_arm() was called with no pin installed. A release "
            "that tolerated this would make every 'and the pin was released' "
            "check pass in a file whose pin had been deleted.")

    who, previous = _STATE["who"], _STATE["previous"]
    config.MATCHING_PROVIDER = previous
    _STATE["who"] = None
    _STATE["previous"] = None
    restored = config.MATCHING_PROVIDER == previous
    out(f"[provider pin] {who}: released; config.MATCHING_PROVIDER is back to "
        f"{previous!r}.")
    return (who, previous, restored)


def pin_state():
    """Diagnostic: ``(who, previous)`` for the pin in force, or ``(None, None)``.

    A READ, never a build and never a mutation -- ``deps.peek``'s rule. It is
    what lets a file assert "no pin leaked" without installing or clearing one.
    """
    return (_STATE["who"], _STATE["previous"])


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Sep  2 2026

@author: ramyalsaffar
"""
