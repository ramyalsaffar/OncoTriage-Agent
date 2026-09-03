######################################################################
# The Bedrock go-live probe. IT SPENDS MONEY, AND ONLY WHEN TOLD TO.
######################################################################

"""Settle, with live calls, every fact the Bedrock adapter took from documentation.

    python bedrock_probe.py --i-understand-this-bills

WITHOUT THAT FLAG IT DOES NOTHING AND EXITS 2. Not a confirmation prompt: a
prompt is answered by a person who has stopped reading, and this file is the
one command in the project that reaches a billed endpoint on purpose. The flag
is long, ugly and unabbreviated so it cannot be typed by accident or completed
by a shell.

WHAT IT COSTS. Two identical calls by default -- the second exists to prove the
prompt cache warms -- over a deliberately small prompt. At the model card's
Standard/short-context rates that is fractions of a cent. It prints its own
cost from PRICING_CONFIG before it exits, and prints the raw usage block so the
console's billing can be reconciled against it.

WHY IT IS A TOP-LEVEL SCRIPT AND NOT A TEST. `fixture_capture.py`'s precedent:
a thing that spends money must not sit in `tests/`, where a suite runner or a
CI job could reach it. Nothing imports this file, and `ci_test_buckets.py`
never sees it.

WHAT IT ANSWERS, in the order the adapter's VERIFY-AT-GO-LIVE list numbers them
(oncotriage/agent/bedrock_adapter.py):

  (1)(2) reasoning effort 'none' and the {"effort": ...} shape -- accepted, and
         echoed back in response.reasoning.
  (3)    structured output as text.format with strict:true -- accepted, the
         output PARSES against the real Stage 5 schema, and the format is
         echoed back. The third check is the one that catches the dangerous
         outcome: accepted, no error, and silently not enforced.
  (4)    seed. Only under --probe-seed, which issues one EXTRA billed call with
         the seed smuggled through extra_body.
  (5)    the cached-token field name, and whether the cache warms across two
         identical calls.
  (6)    which endpoint and Region actually served it.
  (7)    the model echo against the configured id.
  (8)    the cost, priced from PRICING_CONFIG's Bedrock rows.
  (10)   store=false is accepted.

TWO BRANCHES, ONE PROBE, AND `--provider` SELECTS. Everything above describes
the DEFAULT branch, `--provider bedrock`: the OpenAI-compatible Responses API
serving GPT-5.6 Terra. `--provider bedrock_anthropic` probes the CONVERSE API
serving Claude Sonnet 4.6 instead, and answers a different, lettered list --
A1..A10 at the top of `oncotriage/agent/bedrock_anthropic_adapter.py`. They are
lettered rather than numbered so that a report naming "(3)" and one naming
"(A1)" cannot be confused, because both are about structured output and they
are about different APIs.

    python bedrock_probe.py --i-understand-this-bills \
        --provider bedrock_anthropic

The two branches share the refusal gate, the summary and the schema validator
and nothing below that. In ranked order the Converse branch answers: A1 whether
the Stage 5 schema is accepted AND enforced (the only failure that makes the
branch useless rather than degraded, and the one whose dangerous outcome is
silent non-enforcement); A2 whether the prompt cache warms and whether the
disjoint-usage arithmetic holds against a real response (the failure that costs
money silently); A3 whether a model echo can be recovered at all, which decides
whether MatchingModelMismatchError is live on that branch; then A4/A5 thinking
and effort, A6 cost against rows that are partly INFERRED, A7 the truncation
stop reason (only under --probe-truncation, one extra billed call), and A8 the
service tier.

IT FORCES THE PROVIDER IN ITS OWN PROCESS, and says so. `config.MATCHING_
PROVIDER` stays "openai" in the file; this script sets it on the module for the
duration of its own run so the adapter builds a Bedrock request. Nothing is
written to disk, no `inferences` row is produced, and the pipeline is not
invoked -- this calls the adapter's own request builder directly, so what is
probed is the request Stage 5 would actually send rather than a hand-written
one beside it.

EXIT CODES
  0  every check passed
  1  a check failed, or the call raised
  2  the confirmation flag was not given (nothing was called, nothing billed)
"""

import argparse
import hashlib
import json
import os
import sys
import time


try:
    import oncotriage                                          # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.abspath(__file__))
         if "__file__" in globals() else None, "__file__"),
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


CONFIRM_FLAG = "--i-understand-this-bills"


# ===========================================================================
# PACING, AND WHY A PROBE NEEDS IT
# ===========================================================================
#
# MEASURED IN THE AWS CONSOLE 2026-08-30, not inferred: this account's APPLIED
# requests-per-minute quota for Claude Sonnet 4.6 cross-region inference is
# **10**, and the increase request to 10,000 was DENIED. Tokens per minute is
# 6,000,000; tokens per day is 10,800,000 and is not adjustable.
#
# WHY A PACER AND NOT A RETRY. botocore's standard retry mode would absorb a
# self-inflicted 429 and the probe would still finish -- having measured its
# own retry loop rather than the thing it was pointed at. The cache readings
# (A2, A12) are statements about a SEQUENCE of requests, and a request whose
# position in that sequence was decided by a backoff is a request whose reading
# cannot be attributed. A probe that throttles itself has spent money to
# produce a number nobody can use.
#
# THE MARGIN IS A FACTOR RATHER THAN A SUBTRACTION, so it stays correct if the
# ceiling is ever raised. At the shipped defaults (ceiling 10, margin 1.5) the
# effective rate is 6.7 requests per minute -- a third of headroom against a
# limit whose enforcement window AWS does not document as a clean 60-second
# bucket, so "exactly 10 in any 60 seconds" is not a safe reading of it.
#
# IT IS NOT APPLIED TO --probe-throttle, which exists to exceed the ceiling on
# purpose and builds its own unpaced, unretried client to do it.


class _Pacer:
    """Hold each billed call at least `gap` seconds after the previous one."""

    def __init__(self, max_rpm, margin):
        self.max_rpm = max_rpm
        self.margin = margin
        self.gap = 0.0 if max_rpm <= 0 else (60.0 / max_rpm) * margin
        self._last = None
        self.slept = 0.0
        self.waits = 0

    def wait(self, label=""):
        if self.gap <= 0:
            self._last = time.monotonic()
            return
        now = time.monotonic()
        if self._last is not None and now < self._last + self.gap:
            delay = self._last + self.gap - now
            self.waits += 1
            self.slept += delay
            print(f"  [pace] holding {delay:.1f}s"
                  f"{(' before ' + label) if label else ''} "
                  f"(ceiling {self.max_rpm}/min x margin {self.margin} "
                  f"-> {self.gap:.1f}s between calls)")
            time.sleep(delay)
            now = time.monotonic()
        self._last = now


# Replaced in main() once the flags are parsed. A zero-rate pacer never sleeps,
# which is what keeps `--max-rpm 0` an honest way to say "do not pace me".
_PACER = _Pacer(0, 1.0)


def aws_error_evidence(exc):
    """Everything about a failed AWS call that an AWS support case needs.

    THE REQUEST ID IS THE POINT, AND `str(exc)` LOSES IT. A throttled Converse
    call arrives as a botocore ``ClientError`` whose string form names the
    operation and the message and NOT the request id; the id lives at
    ``exc.response["ResponseMetadata"]["RequestId"]``. That is the value an AWS
    support case asks for, so a probe that prints only the exception has spent
    a call and thrown away the evidence it was issued to collect.

    Returns a dict, possibly empty. Every value is copied VERBATIM out of the
    response and is never reformatted or paraphrased.
    """
    resp = getattr(exc, "response", None)
    if not isinstance(resp, dict):
        return {}
    meta = resp.get("ResponseMetadata") or {}
    err = resp.get("Error") or {}
    headers = meta.get("HTTPHeaders") or {}
    out = {
        "RequestId": meta.get("RequestId"),
        "HTTPStatusCode": meta.get("HTTPStatusCode"),
        "RetryAttempts": meta.get("RetryAttempts"),
        "ErrorCode": err.get("Code"),
        "ErrorMessage": err.get("Message"),
        "x-amzn-requestid": headers.get("x-amzn-requestid"),
        "x-amzn-errortype": headers.get("x-amzn-errortype"),
        "retry-after": headers.get("retry-after"),
        "date": headers.get("date"),
    }
    return {k: v for k, v in out.items() if v is not None}


def print_aws_error(exc, indent="          "):
    """Print the evidence block. Returns it so a caller can also collect it."""
    ev = aws_error_evidence(exc)
    if not ev:
        print(f"{indent}(no botocore response payload on this exception -- "
              f"nothing to quote for a support case)")
        return ev
    print(f"{indent}--- AWS error evidence, verbatim ---")
    for k, v in ev.items():
        print(f"{indent}  {k}: {v}")
    return ev


def guarded_build(build, what):
    """Build a client, or return a NAMED refusal instead of a traceback.

    FOUND BY RUNNING THIS FILE, NOT BY READING IT. The Responses branch already
    guards its credential resolution, on the argument written there that "a
    traceback out of main() is the shape this project has shipped nine times"
    -- and then constructs its client on the very next line UNGUARDED. The
    Converse branch did the same. On a machine with boto3 absent, or with no
    credential boto3 can see, `bedrock_probe.py --i-understand-this-bills
    --provider bedrock_anthropic` printed a ModuleNotFoundError chained into a
    RuntimeError and died from an uncaught exception -- reporting a traceback
    where it owed the message that names the one-line fix, to the one operator
    guaranteed to hit it: the one running this on day one.

    Returns (client, None) or (None, exc). Nothing has been called or billed at
    the point this can fail.
    """
    try:
        return build(), None
    except Exception as exc:                           # noqa: BLE001
        print(f"\n  REFUSED before any call -- {what} could not be built:")
        for line in str(exc).splitlines():
            print(f"    {line}")
        print("\n  Nothing was called. Nothing was billed.")
        return None, exc


# ===========================================================================
# A self-contained structural validator
# ===========================================================================
#
# NOT `jsonschema`. Adding a dependency so that a probe can check a schema
# would put a package in pyproject.toml that only this file uses, and the
# subset the Stage 5 schema exercises is small and closed: object, array,
# string, number, enum, required, additionalProperties: false. Walking it here
# keeps the check honest about exactly what it verified -- which is what gets
# reported -- rather than delegating to a library and reporting "valid".

def validate_against_schema(value, schema, path="$"):
    """Return a list of human-readable violations. Empty means it conformed."""
    problems = []
    kind = schema.get("type")

    if kind == "object":
        if not isinstance(value, dict):
            return [f"{path}: expected object, got {type(value).__name__}"]
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                problems.append(f"{path}.{name}: required key missing")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in props:
                    problems.append(f"{path}.{name}: additional property "
                                    f"(schema forbids them)")
        for name, sub in props.items():
            if name in value:
                problems += validate_against_schema(value[name], sub,
                                                    f"{path}.{name}")
        return problems

    if kind == "array":
        if not isinstance(value, list):
            return [f"{path}: expected array, got {type(value).__name__}"]
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(value):
                problems += validate_against_schema(item, item_schema,
                                                    f"{path}[{i}]")
        return problems

    if kind == "string" and not isinstance(value, str):
        problems.append(f"{path}: expected string, got {type(value).__name__}")
    elif kind in ("number", "integer") and (isinstance(value, bool)
                                            or not isinstance(value, (int, float))):
        problems.append(f"{path}: expected {kind}, got {type(value).__name__}")
    elif kind == "boolean" and not isinstance(value, bool):
        problems.append(f"{path}: expected boolean, got {type(value).__name__}")

    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: {value!r} is not in the enum "
                        f"{schema['enum']}")
    return problems


# ===========================================================================
# Reporting
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, actual, expected):
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected: {expected}\n"
                         f"          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def check_true(label, condition):
    check(label, bool(condition), True)


def section(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def dump(label, obj):
    """Print an SDK model or a dict in full. THE POINT OF THE PROBE.

    A renamed usage field reads as a zero everywhere downstream and as NULL in
    the database; the only thing that makes it visible is printing the whole
    block rather than the fields this project expects to find in it.
    """
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    print(f"  {label}:")
    for line in json.dumps(obj, indent=2, default=str).splitlines():
        print(f"    {line}")


# ===========================================================================
# The probe
# ===========================================================================

# Deliberately tiny AND deliberately shaped like the real thing: it names one
# real criterion so the model has something to be strict about, and it asks for
# the real Stage 5 schema. A "say hello" probe would prove the endpoint is up
# and nothing this file exists to answer.
PROBE_SYSTEM = (
    "You evaluate a patient against clinical trial eligibility criteria. "
    "Answer only with the JSON object the schema describes."
)
PROBE_USER = (
    "Patient: 61-year-old female, stage II invasive ductal carcinoma of the "
    "breast, ECOG 1.\n\n"
    "Trial NCT00000000 (Trial 1) — inclusion criterion: "
    "'Histologically confirmed breast cancer'. "
    "Exclusion criterion: 'ECOG performance status greater than 2'.\n\n"
    "Evaluate this one trial."
)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Live Bedrock probe for the Stage 5 adapter. BILLS.")
    parser.add_argument(
        CONFIRM_FLAG, dest="confirmed", action="store_true",
        help="Required. Without it nothing is called and the exit code is 2.")
    parser.add_argument(
        "--provider", default="bedrock",
        choices=["bedrock", "bedrock_anthropic"],
        help="Which Bedrock branch to probe. 'bedrock' is the OpenAI-compatible "
             "Responses API (GPT-5.6 Terra) and is the default because it is "
             "the branch this file was written for. 'bedrock_anthropic' is the "
             "Converse API (Claude Sonnet 4.6) and answers the A1..A10 list in "
             "oncotriage/agent/bedrock_anthropic_adapter.py. "
             "THE CHOICES ARE SPELT OUT RATHER THAN READ OFF "
             "config.MATCHING_PROVIDERS, because that tuple contains 'openai' "
             "and this file cannot probe a provider it has no adapter for -- "
             "argparse refusing an unprobeable name is better than main() "
             "discovering it three sections in.")
    parser.add_argument(
        "--probe-seed", action="store_true",
        help="Issue ONE EXTRA billed call with MATCHING_SEED smuggled through "
             "extra_body, to settle VERIFY-AT-GO-LIVE (4).")
    parser.add_argument(
        "--probe-truncation", action="store_true",
        help="bedrock_anthropic only: issue ONE EXTRA billed call at "
             "maxTokens=16 to settle A7 -- whether a real truncation reports "
             "stopReason 'max_tokens', which is the single string that arms "
             "Stage 5's reactive split.")
    parser.add_argument(
        "--probe-per-trial", action="store_true",
        help="bedrock_anthropic only: issue THREE EXTRA billed calls -- one "
             "warmup-shaped and two trial-shaped over the SAME system prefix "
             "-- to settle A11 (is `maxTokens = 1` with the structured-output "
             "block dropped accepted at all) and A12 (does the warmup's write "
             "get reported, and do the two calls behind it read it). THE "
             "MEASUREMENT COMES OUT OF THE USAGE BLOCK, never the wall clock. "
             "THIS IS THE MIGRATION WINDOW'S FIRST COMMAND for per-trial mode "
             "on this provider: without it, both premises the mode's "
             "affordability rests on are documentation.")
    parser.add_argument(
        "--per-trial-prefix-file",
        help="bedrock_anthropic only, with --probe-per-trial: read the system "
             "prefix from this file instead of using the probe's tiny built-in "
             "one. THE BUILT-IN PREFIX IS BELOW BEDROCK'S 1,024-TOKEN CACHE "
             "MINIMUM, so a zero cache write with it is the documented "
             "behaviour of a short prefix and says nothing about Stage 5. "
             "Render a real one and point this at it before drawing any "
             "conclusion about A12.")
    parser.add_argument(
        "--probe-output-tokens", action="store_true",
        help="bedrock_anthropic only, with --per-trial-prefix-file and one or "
             "more --per-trial-user-file: issue ONE REAL TRIAL-SHAPED CALL PER "
             "USER FILE over that shared prefix and report usage.outputTokens "
             "for each. THIS IS THE MEASUREMENT MATCHING_OUTPUT_TOKENS_PER_"
             "TRIAL NEEDS AND HAS NEVER HAD ON THIS MODEL: that constant was "
             "derived on gpt-5.6-terra and is the input to the pre-split "
             "guard, so on Claude Sonnet 4.6 it is a number nobody measured. "
             "IT ISSUES NO WARMUP OF ITS OWN -- run it directly after "
             "--probe-per-trial so the prefix is still inside "
             "BEDROCK_ANTHROPIC_CACHE_TTL, and read the per-call cacheRead "
             "figures below to see whether it was.")
    parser.add_argument(
        "--per-trial-user-file", action="append", default=None,
        metavar="PATH",
        help="bedrock_anthropic only, with --probe-output-tokens: a rendered "
             "Stage 5 trial USER message, repeatable, one call each. Render "
             "them with render_per_trial_probe_inputs.py, which writes them "
             "from a real patient's real kept trials through the real "
             "de-identified path -- a hand-written stand-in would measure a "
             "response to a prompt Stage 5 does not send.")
    parser.add_argument(
        "--dump-replies", metavar="DIR", default=None,
        help="bedrock_anthropic only, with --probe-output-tokens: write each "
             "call's RAW reply text and its usage block into DIR, one file "
             "per call. THE PROBE PERSISTS NEITHER TODAY -- it prints "
             "`text_chars` and an evaluation count, which is enough to see "
             "THAT a reply was a well-formed non-answer and not enough to see "
             "WHAT it said. A reply that cannot be re-read is a reply that "
             "has to be bought again. DIR is created if absent and REFUSED "
             "if it already holds replies: the per-call index makes filenames "
             "unique within one run and IDENTICAL across runs, so re-using a "
             "directory would overwrite paid-for evidence with no warning.")
    parser.add_argument(
        "--max-rpm", type=float, default=10.0,
        help="Requests-per-minute ceiling this probe holds itself under. "
             "DEFAULT 10, WHICH IS THIS ACCOUNT'S MEASURED APPLIED QUOTA for "
             "Claude Sonnet 4.6 cross-region (AWS console, 2026-08-30; the "
             "increase to 10,000 was DENIED). 0 disables pacing entirely. "
             "Ignored by --probe-throttle, which exceeds the ceiling on "
             "purpose.")
    parser.add_argument(
        "--rpm-margin", type=float, default=1.5,
        help="Multiplier on the inter-call gap (default 1.5, i.e. 6.7/min "
             "against a ceiling of 10). A FACTOR rather than a subtraction so "
             "it stays correct if the ceiling is raised. AWS does not document "
             "the enforcement window as a clean 60-second bucket, so pacing "
             "at exactly the ceiling is not a safe reading of it.")
    parser.add_argument(
        "--probe-throttle", action="store_true",
        help="bedrock_anthropic only, and it RUNS LAST: deliberately exceed "
             "the requests-per-minute ceiling to settle A14 -- whether the "
             "429s are BURSTY or SUSTAINED, what retry-after they carry, and "
             "WHAT THE REQUEST ID IS. Builds its own client with retries OFF, "
             "because botocore's standard mode absorbs a 429 and an absorbed "
             "429 is one that cannot be counted, timed or quoted to AWS "
             "support. Two waves separated by a recovery pause; cheap (tiny "
             "prompt, tiny ceiling) but it takes wall time.")
    parser.add_argument(
        "--throttle-burst", type=int, default=14,
        help="How many requests the first throttle wave issues at once "
             "(default 14, i.e. 1.4x a ceiling of 10).")
    parser.add_argument(
        "--calls", type=int, default=2,
        help="How many identical calls to issue (default 2; the second is what "
             "proves the prompt cache warms). Minimum 1.")
    args = parser.parse_args(argv)

    if not args.confirmed:
        print(__doc__)
        print("REFUSED: this probe issues live, billed Amazon Bedrock calls.")
        print(f"         Re-run with {CONFIRM_FLAG} if that is what you want.")
        print("         Nothing was called. Nothing was billed.")
        return 2

    if args.calls < 1:
        print("REFUSED: --calls must be at least 1.")
        return 2

    if args.max_rpm < 0 or args.rpm_margin <= 0:
        print("REFUSED: --max-rpm must be >= 0 and --rpm-margin must be > 0.")
        return 2

    if args.throttle_burst < 1:
        print("REFUSED: --throttle-burst must be at least 1.")
        return 2

    # A FLAG THAT SILENTLY DOES NOTHING IS WORSE THAN ONE THAT REFUSES, and
    # three of these had that shape: `--probe-truncation`, `--probe-per-trial`
    # and `--probe-throttle` are all read only inside the Converse branch, and
    # their help text saying "bedrock_anthropic only" is not enforcement. An
    # operator who typed `--provider bedrock --probe-per-trial` got a run that
    # answered none of A11/A12 and said so nowhere -- and on this branch the
    # whole point of the flag is that it is the one command that settles them
    # before a campaign's money rests on the answer.
    _CONVERSE_ONLY = (("--probe-truncation", args.probe_truncation),
                      ("--probe-per-trial", args.probe_per_trial),
                      ("--per-trial-prefix-file", args.per_trial_prefix_file),
                      ("--probe-output-tokens", args.probe_output_tokens),
                      ("--per-trial-user-file", args.per_trial_user_file),
                      ("--dump-replies", args.dump_replies),
                      ("--probe-throttle", args.probe_throttle))
    if args.provider != "bedrock_anthropic":
        _misused = [name for name, given in _CONVERSE_ONLY if given]
        if _misused:
            print(f"REFUSED: {', '.join(_misused)} "
                  f"{'is' if len(_misused) == 1 else 'are'} implemented on the "
                  f"Converse branch only, and --provider is "
                  f"{args.provider!r}. Re-run with "
                  f"--provider bedrock_anthropic, or drop the flag"
                  f"{'' if len(_misused) == 1 else 's'}.")
            print("         Nothing was called. Nothing was billed.")
            return 2
    # TWO PHASES READ IT NOW, so the refusal names both. Written as a
    # membership test over the phases rather than as `not A and not B`: a
    # third reader added to one and not the other is how a flag silently
    # starts being ignored again.
    _PREFIX_READERS = (("--probe-per-trial", args.probe_per_trial),
                       ("--probe-output-tokens", args.probe_output_tokens))
    if args.per_trial_prefix_file and not any(g for _, g in _PREFIX_READERS):
        print(f"REFUSED: --per-trial-prefix-file only has an effect with "
              f"{' or '.join(n for n, _ in _PREFIX_READERS)}, which are the "
              f"phases that read it.")
        print("         Nothing was called. Nothing was billed.")
        return 2
    if args.per_trial_user_file and not args.probe_output_tokens:
        print("REFUSED: --per-trial-user-file only has an effect with "
              "--probe-output-tokens, which is the phase that reads it.")
        print("         Nothing was called. Nothing was billed.")
        return 2
    if args.dump_replies and not args.probe_output_tokens:
        print("REFUSED: --dump-replies only has an effect with "
              "--probe-output-tokens, which is the phase that reads it.")
        print("         Nothing was called. Nothing was billed.")
        return 2
    # BELOW THE PROVIDER CHECK ON PURPOSE. A wrong --provider is the more
    # fundamental mistake and has to be the refusal an operator is told first;
    # reported the other way round, a reader fixes the missing input and meets
    # the provider refusal on the next run. THE PROVIDER RULE IS NOT RESTATED
    # HERE EITHER -- `_CONVERSE_ONLY` above owns it for every Converse-only
    # flag, and a second copy is a second thing to forget.
    if args.probe_output_tokens:
        # Both inputs are required and neither has a defensible default:
        # without a prefix there is no shared cache to read and the
        # measurement is of a request Stage 5 does not make, and without user
        # files there is nothing to measure.
        if not args.per_trial_prefix_file:
            print("REFUSED: --probe-output-tokens needs "
                  "--per-trial-prefix-file. Measuring output tokens against "
                  "this probe's tiny built-in prefix would measure a request "
                  "Stage 5 never sends.")
            print("         Nothing was called. Nothing was billed.")
            return 2
        if not args.per_trial_user_file:
            print("REFUSED: --probe-output-tokens needs at least one "
                  "--per-trial-user-file.")
            print("         Nothing was called. Nothing was billed.")
            return 2

    global _PACER
    _PACER = _Pacer(args.max_rpm, args.rpm_margin)
    if _PACER.gap > 0:
        print(f"[pace] every billed call is held {_PACER.gap:.1f}s after the "
              f"previous one: ceiling {args.max_rpm}/min x margin "
              f"{args.rpm_margin} -> an effective "
              f"{60.0 / _PACER.gap:.1f} requests/min.")
    else:
        print("[pace] PACING DISABLED (--max-rpm 0). A self-inflicted 429 "
              "will be absorbed by botocore's retry and every cache reading "
              "below becomes unattributable.")

    # THE DISPATCH SITS ABOVE EVERY LINE OF THE RESPONSES PROBE, so that branch
    # is byte-identical to the one that shipped: same imports, same order, same
    # checks. A probe that had been restructured to accommodate a second
    # provider would no longer be evidence about the first.
    if args.provider == "bedrock_anthropic":
        return _probe_bedrock_anthropic(args)

    from oncotriage import config
    from oncotriage.agent import bedrock_adapter
    from oncotriage.agent import deps
    from oncotriage.agent.response_schema import (
        EVALUATIONS_KEY, RESPONSE_SCHEMA_NAME, build_response_schema)
    from oncotriage.utils import get_model_cost

    section("PROVIDER — forced for this process only")
    print(f"  config.MATCHING_PROVIDER in the file: "
          f"{config.MATCHING_PROVIDER!r}")
    config.MATCHING_PROVIDER = config.MATCHING_PROVIDER_BEDROCK
    print(f"  forced to {config.MATCHING_PROVIDER!r} for this process. "
          f"Nothing on disk changed.")

    try:
        config.validate_matching_provider_config()
    except RuntimeError as exc:
        print(f"\n  REFUSED before any call: {exc}")
        return 1

    base_url = config.get_bedrock_base_url()
    # GUARDED. An unset credential is the FIRST thing a day-one operator hits,
    # and a traceback out of main() is the shape this project has shipped nine
    # times: it reports one exception where it owes a message somebody can act
    # on. Nothing has been called or billed at this point.
    try:
        _key, key_source = config.get_bedrock_api_key()
    except RuntimeError as exc:
        print(f"\n  REFUSED before any call:\n{exc}")
        print("\n  Nothing was called. Nothing was billed.")
        return 1
    del _key                       # never printed, never logged, never stored
    print(f"  endpoint  : {config.BEDROCK_ENDPOINT}")
    print(f"  base URL  : {base_url}                     [VERIFY (6)]")
    # THE SOURCE, NOT ONLY THE VALUE. This is day one's first command, and the
    # first thing an operator who has just exported ONCOTRIAGE_BEDROCK_REGION
    # wants to know is whether the export took. Printing the value alone
    # answers that only if they can remember what the default was.
    _region_src = (config.BEDROCK_REGION_SOURCE
                   or "BEDROCK_REGION_DEFAULT in oncotriage/config.py")
    print(f"  region    : {config.BEDROCK_REGION}  (from {_region_src})")
    print(f"  model     : {config.matching_wire_model()}")
    print(f"  key source: {key_source}")

    section("REQUEST — exactly what Stage 5 would send")
    kwargs = bedrock_adapter.build_bedrock_request(PROBE_SYSTEM, PROBE_USER)
    printable = {k: v for k, v in kwargs.items() if k != "timeout"}
    dump("responses.create kwargs (timeout omitted: client-side only)",
         printable)
    check("the request names the configured wire model",
          kwargs["model"], config.matching_wire_model())
    check("store is False (patient text is not retained for 30 days)",
          kwargs["store"], False)
    check("no seed is on the wire by default",
          "seed" in kwargs or "seed" in (kwargs.get("extra_body") or {}),
          config.BEDROCK_SEND_SEED_IN_EXTRA_BODY)

    client, _exc = guarded_build(deps.get_bedrock_client,
                                 "the Bedrock Responses client")
    if client is None:
        return 1
    schema = build_response_schema()

    raw_responses = []
    section(f"CALLS — {args.calls} identical, live and billed")
    for i in range(args.calls):
        print(f"\n  --- call {i + 1} of {args.calls} ---")
        try:
            _PACER.wait(f"call {i + 1}")
            raw = client.responses.create(**kwargs)
        except Exception as exc:                       # noqa: BLE001
            category = bedrock_adapter.classify_error(exc)
            print(f"  RAISED  {type(exc).__name__} [{category}]")
            print(f"          {exc}")
            print_aws_error(exc)
            print("\n  The adapter's VERIFY-AT-GO-LIVE list names what to edit "
                  "for each category.")
            _RESULTS["failed"] += 1
            _FAILURES.append(f"call {i + 1} raised {type(exc).__name__} "
                             f"[{category}]: {exc}")
            return _summary()
        raw_responses.append(raw)
        print(f"  id     : {getattr(raw, 'id', None)}")
        print(f"  status : {getattr(raw, 'status', None)}")
        print(f"  model  : {getattr(raw, 'model', None)}")
        dump("usage (VERIFY (5): read every field name here)",
             getattr(raw, "usage", None))

    first = raw_responses[0]

    section("(1)(2) REASONING — is 'none' accepted, in the {'effort': ...} shape")
    dump("response.reasoning", getattr(first, "reasoning", None))
    check_true("the call was accepted with "
               f"reasoning={{'effort': {config.MATCHING_REASONING_EFFORT!r}}}",
               True)   # reaching here at all means it was not rejected

    section("(3) STRUCTURED OUTPUT — accepted, ECHOED, and actually enforced")
    dump("response.text (the format the service says it applied)",
         getattr(first, "text", None))
    echoed = getattr(first, "text", None)
    echoed_d = echoed.model_dump() if hasattr(echoed, "model_dump") else (echoed or {})
    echoed_fmt = (echoed_d or {}).get("format") or {}
    check("the service echoes back a json_schema format",
          echoed_fmt.get("type"), "json_schema")
    check("...naming the Stage 5 schema", echoed_fmt.get("name"),
          RESPONSE_SCHEMA_NAME)
    check("...with strict mode on. IF THIS FAILS THE SCHEMA WAS A HINT, NOT A "
          "CONSTRAINT", echoed_fmt.get("strict"), True)

    translated = bedrock_adapter.translate_response(first)
    text = (translated.choices[0].message.content or "").strip()
    print(f"\n  translated finish_reason: "
          f"{translated.choices[0].finish_reason}")
    print(f"  translated refusal      : {translated.choices[0].message.refusal}")
    try:
        parsed = json.loads(text)
    except Exception as exc:                           # noqa: BLE001
        check(f"the output parses as JSON ({exc})", False, True)
        parsed = None

    if parsed is not None:
        problems = validate_against_schema(parsed, schema)
        check("the output CONFORMS to the real Stage 5 schema "
              f"(violations: {problems[:3]})", problems, [])
        check_true(f"...and carries the {EVALUATIONS_KEY!r} array",
                   isinstance(parsed.get(EVALUATIONS_KEY), list))

    section("(7) MODEL ECHO — Stage 5 RAISES on a mismatch")
    check("the echo equals the configured wire model",
          getattr(first, "model", None), config.matching_wire_model())

    section("(10) store=false was accepted")
    check_true("the call carrying store=false succeeded", True)

    section("(5) PROMPT CACHE — does an identical second call read from cache")
    reads = []
    for i, raw in enumerate(raw_responses):
        u = getattr(raw, "usage", None)
        d = u.model_dump() if hasattr(u, "model_dump") else (u or {})
        details = d.get("input_tokens_details") or {}
        reads.append(details.get("cached_tokens"))
        print(f"  call {i + 1}: input_tokens={d.get('input_tokens')} "
              f"cached_tokens={details.get('cached_tokens')} "
              f"cache_write_tokens={details.get('cache_write_tokens')}")
    check_true("call 1 reported a cached_tokens field at all "
               "(None means the field name moved -- VERIFY (5))",
               reads and reads[0] is not None)
    if len(reads) > 1:
        if reads[0] is not None and reads[1] is not None and reads[1] > reads[0]:
            print("  the cache WARMED between the two calls.")
        else:
            print("  the cache did NOT warm. Expected below the 1,024-token "
                  "minimum prefix -- this probe's prompt is deliberately tiny, "
                  "so a zero here is NOT evidence against caching in Stage 5, "
                  "whose prefix is ~20k tokens. Re-read with a real prompt "
                  "before drawing a conclusion.")

    if args.probe_seed:
        section("(4) SEED — one EXTRA billed call with seed in extra_body")
        seeded = dict(kwargs)
        seeded["extra_body"] = dict(seeded.get("extra_body") or {})
        seeded["extra_body"]["seed"] = config.MATCHING_SEED
        try:
            _PACER.wait("the seed call")
            client.responses.create(**seeded)
            print("  ACCEPTED. Set config.BEDROCK_SEND_SEED_IN_EXTRA_BODY = "
                  "True and re-read the adapter's `seed` row.")
            _RESULTS["passed"] += 1
        except Exception as exc:                       # noqa: BLE001
            print(f"  REJECTED ({type(exc).__name__}: {exc})")
            print("  Leave BEDROCK_SEND_SEED_IN_EXTRA_BODY False. The drop is "
                  "counted in BEDROCK_ADAPTER_DEGRADATIONS.")
            _RESULTS["passed"] += 1        # a definitive answer either way

    section("(8) COST — priced from PRICING_CONFIG's Bedrock rows")
    total_in = total_out = 0
    for raw in raw_responses:
        u = getattr(raw, "usage", None)
        d = u.model_dump() if hasattr(u, "model_dump") else (u or {})
        total_in += d.get("input_tokens") or 0
        total_out += d.get("output_tokens") or 0
    model_key = getattr(first, "model", None) or config.matching_wire_model()
    try:
        cost = get_model_cost(model_key, total_in, total_out)
        print(f"  {len(raw_responses)} call(s): {total_in} input + "
              f"{total_out} output tokens")
        print(f"  priced against {model_key!r}: ${cost:.6f}")
        print("  CACHED INPUT IS NOT DISCOUNTED IN THIS FIGURE -- "
              "PRICING_CONFIG has no cached term, so a cache hit makes this an "
              "OVER-estimate, which is the safe direction. Reconcile against "
              "the console before trusting it for a campaign.")
    except Exception as exc:                           # noqa: BLE001
        check(f"the answering model is priced in PRICING_CONFIG ({exc})",
              False, True)

    section("DEGRADATIONS RECORDED BY THE ADAPTER")
    recorded = dict(bedrock_adapter.BEDROCK_ADAPTER_DEGRADATIONS)
    print(f"  {recorded or 'none'}")
    print("  EMPTY IS THE EXPECTED READING HERE, and the reason is worth "
          "knowing: this probe builds the request with the adapter's own "
          "builder and issues it directly, so it never enters "
          "call_matching_model_bedrock -- which is where the once-per-process "
          "`seed_not_expressible` bump and its WARNING live. A real Stage 5 "
          "run records that on its first call. What the probe checks instead "
          "is stronger and is above: that no seed is on the wire at all.")
    print("  Anything else appearing here is a translation the adapter had to "
          "INTERPRET, and each key is a numbered VERIFY-AT-GO-LIVE item.")

    return _summary()


def _probe_bedrock_anthropic(args):
    """The Converse branch: A1..A10 in oncotriage/agent/bedrock_anthropic_adapter.py.

    A SEPARATE FUNCTION RATHER THAN BRANCHES THREADED THROUGH THE RESPONSES
    PROBE. The two share the refusal gate, the summary and the schema
    validator, and share NOTHING below that: different client, different
    request builder, different response shape, different usage field names,
    different questions. Interleaving them would produce a function in which
    every check had to be read twice to find out which API it was about.

    THE ORDER IS THE RANKING. A1 (structured output) first because it is the
    only failure that makes the branch useless rather than degraded; A2 (the
    cache) second because it is the failure that costs money silently.
    """
    from oncotriage import config
    from oncotriage.agent import bedrock_anthropic_adapter as adapter
    from oncotriage.agent import deps
    from oncotriage.agent.response_schema import (
        EVALUATIONS_KEY, RESPONSE_SCHEMA_NAME, build_response_schema)
    from oncotriage.utils import get_model_cost

    section("PROVIDER — forced for this process only")
    print(f"  config.MATCHING_PROVIDER in the file: "
          f"{config.MATCHING_PROVIDER!r}")
    config.MATCHING_PROVIDER = config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC
    print(f"  forced to {config.MATCHING_PROVIDER!r} for this process. "
          f"Nothing on disk changed.")

    try:
        config.validate_matching_provider_config()
    except RuntimeError as exc:
        print(f"\n  REFUSED before any call: {exc}")
        return 1

    _region_src = (config.BEDROCK_REGION_SOURCE
                   or "BEDROCK_REGION_DEFAULT in oncotriage/config.py")
    print(f"  API       : Converse (bedrock-runtime), through boto3")
    print(f"  region    : {config.BEDROCK_REGION}  (from {_region_src})")
    print(f"  model     : {config.matching_wire_model()}")
    print(f"  thinking  : {config.BEDROCK_ANTHROPIC_THINKING!r}   "
          f"effort: {config.BEDROCK_ANTHROPIC_EFFORT!r}        [A4] [A5]")
    print(f"  cache ttl : {config.BEDROCK_ANTHROPIC_CACHE_TTL!r}"
          f"                              [A2]")

    section("REQUEST — exactly what Stage 5 would send")
    try:
        kwargs = adapter.build_converse_request(PROBE_SYSTEM, PROBE_USER)
    except Exception as exc:                           # noqa: BLE001
        print(f"\n  REFUSED before any call: {type(exc).__name__}: {exc}")
        print("  Nothing was called. Nothing was billed.")
        return 1

    _schema_str = kwargs["outputConfig"]["textFormat"]["structure"]["jsonSchema"]["schema"]
    printable = json.loads(json.dumps(kwargs))
    printable["outputConfig"]["textFormat"]["structure"]["jsonSchema"]["schema"] = (
        f"<{len(_schema_str)} chars of serialized JSON; printed in full below>")
    dump("converse() kwargs", printable)
    check("the request names the configured wire model",
          kwargs["modelId"], config.matching_wire_model())
    check("the schema travels as a STRING, which is what Converse's "
          "jsonSchema.schema member is", isinstance(_schema_str, str), True)
    check("no seed reaches the wire — Converse has no such field",
          "seed" in json.dumps(kwargs), False)
    check_true("a cachePoint follows the system text",
               any("cachePoint" in b for b in kwargs["system"]))
    print(f"\n  serialized schema, in full:\n    {_schema_str}")

    client, _exc = guarded_build(deps.get_bedrock_anthropic_client,
                                 "the Bedrock Converse client")
    if client is None:
        return 1
    # FREE, AND ABOVE EVERY PAID CALL. See _preflight_sdk_shape for what this
    # cost to learn. It runs after the client is built because it needs that
    # client's own service model, and before the pacer's first wait because a
    # request the SDK cannot express cannot be sent by waiting longer.
    if not _preflight_sdk_shape(client, kwargs, config):
        return _summary()

    schema = build_response_schema()

    raw_responses = []
    elapsed = []
    section(f"CALLS — {args.calls} identical, live and billed")
    for i in range(args.calls):
        print(f"\n  --- call {i + 1} of {args.calls} ---")
        _PACER.wait(f"call {i + 1}")
        _t0 = time.monotonic()
        try:
            raw = client.converse(**kwargs)
        except Exception as exc:                       # noqa: BLE001
            category = adapter.classify_error(exc)
            print(f"  RAISED  {type(exc).__name__} [{category}]")
            print(f"          {exc}")
            print_aws_error(exc)
            print("\n  The adapter's A1..A10 list names what to edit for each "
                  "category. A ValidationException naming outputConfig is A1; "
                  "one naming additionalModelRequestFields is A4.")
            _RESULTS["failed"] += 1
            _FAILURES.append(f"call {i + 1} raised {type(exc).__name__} "
                             f"[{category}]: {exc}")
            return _summary()
        elapsed.append(time.monotonic() - _t0)
        raw_responses.append(raw)
        print(f"  request id : "
              f"{(raw.get('ResponseMetadata') or {}).get('RequestId')}")
        print(f"  stopReason : {raw.get('stopReason')}")
        print(f"  elapsed    : {elapsed[-1]:.1f}s")
        dump("usage (A2: read every field name here)", raw.get("usage"))

    first = raw_responses[0]

    # ---- A1 -------------------------------------------------------------
    section("(A1) STRUCTURED OUTPUT — accepted, and actually enforced")
    print(f"  first-call latency {elapsed[0]:.1f}s against a read budget of "
          f"{config.MATCHING_REQUEST_TIMEOUT_SECONDS}s.          [A1] [A10]")
    print("  A FIRST-EVER SCHEMA COMPILES THE GRAMMAR, which AWS documents as "
          "taking 'up to a few minutes'. A slow first call and a fast second "
          "is that, not a slow model.")
    if len(elapsed) > 1:
        print(f"  second call {elapsed[1]:.1f}s.")
    check_true("the call carrying outputConfig.textFormat was ACCEPTED", True)

    translated = adapter.translate_response(first)
    text = (translated.choices[0].message.content or "").strip()
    print(f"\n  translated finish_reason: "
          f"{translated.choices[0].finish_reason}")
    try:
        parsed = json.loads(text)
    except Exception as exc:                           # noqa: BLE001
        check(f"the output parses as JSON ({exc})", False, True)
        parsed = None

    if parsed is not None:
        problems = validate_against_schema(parsed, schema)
        check("the output CONFORMS to the real Stage 5 schema "
              f"(violations: {problems[:3]}). IF THIS FAILS, THE SCHEMA WAS "
              "ACCEPTED AND SILENTLY NOT ENFORCED, which is the dangerous "
              "outcome", problems, [])
        check_true(f"...and carries the {EVALUATIONS_KEY!r} array",
                   isinstance(parsed.get(EVALUATIONS_KEY), list))
    print(f"  (the schema was sent under the name {RESPONSE_SCHEMA_NAME!r}; "
          f"Converse echoes no format back, so conformance above is the only "
          f"evidence that it was enforced)")

    # ---- A2 -------------------------------------------------------------
    section("(A2) PROMPT CACHE — and the disjoint-usage arithmetic")
    reads = []
    for i, raw in enumerate(raw_responses):
        u = raw.get("usage") or {}
        reads.append(u.get("cacheReadInputTokens"))
        derived = adapter._usage_block(u)
        print(f"  call {i + 1}: inputTokens={u.get('inputTokens')} "
              f"cacheRead={u.get('cacheReadInputTokens')} "
              f"cacheWrite={u.get('cacheWriteInputTokens')} "
              f"-> prompt_tokens={derived['prompt_tokens']}")
    check_true("call 1 reported a cacheReadInputTokens field at all "
               "(None means the field name moved — the whole cached-token "
               "column would then store NULL and nothing would raise)",
               reads and reads[0] is not None)
    if len(reads) > 1:
        if reads[0] is not None and reads[1] is not None and reads[1] > reads[0]:
            print("  the cache WARMED between the two calls.")
        else:
            print("  the cache did NOT warm. EXPECTED HERE: the minimum "
                  "cacheable prefix for this model is 1,024 tokens and this "
                  "probe's system prompt is deliberately tiny, so a zero is "
                  "NOT evidence against caching in Stage 5, whose prefix is "
                  "~20k tokens. Re-read with a real prompt before concluding "
                  "anything — and note AWS's own wording: a checkpoint below "
                  "the minimum still SUCCEEDS, it just does not cache.")

    # ---- A3 -------------------------------------------------------------
    section("(A3) MODEL ECHO — does /model come back at all")
    dump("additionalModelResponseFields", first.get("additionalModelResponseFields"))
    _model, _echoed = adapter._model_echo(first)
    if _echoed:
        print(f"  AN ECHO ARRIVED: {_model!r}. MatchingModelMismatchError is "
              f"LIVE on this branch — record that.")
        check("the echo equals the configured wire model",
              _model, config.matching_wire_model())
    else:
        print(f"  NO ECHO. inferences.matching_model will record the REQUESTED "
              f"id ({_model!r}) and the adapter counts "
              f"'model_echo_unavailable' once per run. That is the state the "
              f"module ships in and it is not a failure of this probe.")

    # ---- A4 / A5 / A7 / A8 ----------------------------------------------
    section("(A4)(A5) THINKING AND EFFORT were accepted")
    check_true(f"the call carrying thinking="
               f"{config.BEDROCK_ANTHROPIC_THINKING!r} was accepted", True)
    if config.BEDROCK_ANTHROPIC_EFFORT is None:
        print("  outputConfig.effort was OMITTED (the shipped default). Set "
              "config.BEDROCK_ANTHROPIC_EFFORT and re-run to settle A5.")
    else:
        check_true(f"...and effort={config.BEDROCK_ANTHROPIC_EFFORT!r}", True)

    section("(A8) serviceTier was OMITTED, which is Standard")
    check("no serviceTier on the wire", "serviceTier" in kwargs,
          config.BEDROCK_ANTHROPIC_SERVICE_TIER is not None)

    section("(A7) STOP REASON — what a normal completion reports")
    print(f"  stopReason on call 1: {first.get('stopReason')!r} -> "
          f"finish_reason {translated.choices[0].finish_reason!r}")
    print("  THE TRUNCATION SPLIT IS ARMED BY 'max_tokens' AND NOTHING ELSE. "
          "Re-run with --probe-truncation to see what a real truncation "
          "reports; a value other than 'max_tokens' there means the split "
          "never fires and Stage 5 burns its parse-retry budget instead.")

    if args.probe_truncation:
        section("(A7) TRUNCATION — one EXTRA billed call at maxTokens=16")
        truncated = json.loads(json.dumps(kwargs))
        truncated["inferenceConfig"]["maxTokens"] = 16
        try:
            _PACER.wait("the truncation call")
            t_raw = client.converse(**truncated)
            print(f"  stopReason: {t_raw.get('stopReason')!r}")
            check("a deliberate truncation reports 'max_tokens', which is what "
                  "arms the split", t_raw.get("stopReason"), "max_tokens")
        except Exception as exc:                       # noqa: BLE001
            print(f"  RAISED ({type(exc).__name__}: {exc})")
            print_aws_error(exc, indent="  ")
            print("  A maxTokens below the model's minimum is itself a "
                  "possibility; this does not settle A7 either way.")

    if args.probe_per_trial:
        section("(A11)(A12) PER-TRIAL — the warmup, then two reads of it")
        print("  THREE EXTRA BILLED CALLS. The measurement is the USAGE BLOCK "
              "and never the wall clock: a fast second call proves nothing "
              "about caching, and a slow one disproves nothing.")
        print("  THE PREFIX HERE IS THIS PROBE'S, WHICH IS TINY. Bedrock's "
              "minimum cacheable prefix for this model is 1,024 tokens and "
              "PROBE_SYSTEM is far below it, so a zero WRITE below is the "
              "documented behaviour of a short prefix and NOT evidence about "
              "Stage 5, whose real system prompt measures 8,115-10,464 tokens "
              "on the twelve characterization fixtures. Re-run with "
              "--per-trial-prefix-file pointed at a real rendered prompt "
              "before concluding anything about the cache.")

        _pt_system = PROBE_SYSTEM
        if args.per_trial_prefix_file:
            try:
                _pt_system = open(args.per_trial_prefix_file,
                                  encoding="utf-8").read()
            except OSError as exc:
                print(f"\n  REFUSED: could not read "
                      f"{args.per_trial_prefix_file!r}: {exc}")
                print("  Nothing extra was called. Nothing extra was billed.")
                return _summary()
            print(f"  prefix from {args.per_trial_prefix_file!r}: "
                  f"{len(_pt_system)} chars "
                  f"(~{len(_pt_system) // config.CHARS_PER_TOKEN} tokens at "
                  f"CHARS_PER_TOKEN; the 1,024 floor needs "
                  f"~{1024 * config.CHARS_PER_TOKEN} chars)")

        warm_kwargs = adapter.build_converse_request(
            _pt_system, config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE,
            warmup=True)
        trial_kwargs = adapter.build_converse_request(_pt_system, PROBE_USER)

        # THE ONE THING THIS CAN CHECK FOR FREE, AND IT IS THE ONE THAT MATTERS
        # MOST: the two requests must carry a BYTE-IDENTICAL system block, or
        # the warmup warms a prefix the wave does not share and every read
        # below is zero for a reason no AWS page could explain.
        check("the warmup and the trial call carry a byte-identical system "
              "block, which is the prefix itself",
              json.dumps(warm_kwargs["system"], sort_keys=True),
              json.dumps(trial_kwargs["system"], sort_keys=True))
        check("the warmup asks for the configured minimal ceiling",
              warm_kwargs["inferenceConfig"]["maxTokens"],
              config.MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS)
        check("the warmup carries outputConfig only when configured to",
              "outputConfig" in warm_kwargs,
              bool(config.BEDROCK_ANTHROPIC_WARMUP_SEND_OUTPUT_CONFIG))

        pt_usages = []
        pt_latencies = []
        for label, kw in (("warmup", warm_kwargs),
                          ("trial 1", trial_kwargs),
                          ("trial 2", trial_kwargs)):
            print(f"\n  --- {label} ---")
            _PACER.wait(label)
            _t0 = time.monotonic()
            try:
                raw = client.converse(**kw)
            except Exception as exc:                   # noqa: BLE001
                category = adapter.classify_error(exc)
                print(f"  RAISED  {type(exc).__name__} [{category}]")
                print(f"          {exc}")
                print_aws_error(exc)
                if label == "warmup":
                    print("\n  THIS IS (A11). If the message names maxTokens, "
                          "the shipped code CLASSIFIES it -- "
                          "evaluation.classify_warmup_rejection carries "
                          "Converse's spelling -- and a real run degrades to "
                          "the one-then-rest schedule rather than failing. "
                          "Raise MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS "
                          "to whatever it names.")
                _RESULTS["failed"] += 1
                _FAILURES.append(f"{label} raised {type(exc).__name__} "
                                 f"[{category}]: {exc}")
                break
            u = raw.get("usage") or {}
            pt_usages.append((label, u))
            pt_latencies.append(time.monotonic() - _t0)
            print(f"  elapsed {pt_latencies[-1]:.1f}s   "
                  f"stopReason {raw.get('stopReason')!r}")
            dump("usage", u)
            _cd = u.get("cacheDetails")
            if _cd is not None:
                print(f"  cacheDetails: {_cd}   <- the TTL the write actually "
                      f"used; compare against "
                      f"BEDROCK_ANTHROPIC_CACHE_TTL="
                      f"{config.BEDROCK_ANTHROPIC_CACHE_TTL!r}")

        if pt_usages:
            check_true("(A11) the warmup's request shape was ACCEPTED",
                       pt_usages[0][0] == "warmup")

        if len(pt_usages) == 3:
            _w = pt_usages[0][1]
            _wrote = _w.get("cacheWriteInputTokens")
            _warm_read = _w.get("cacheReadInputTokens")
            print("\n  --- (A12) the reading the shipped code makes ---")
            print(f"  warmup: write={_wrote!r} read={_warm_read!r}")
            check_true(
                "(A12) the warmup reported a cacheWriteInputTokens FIELD at "
                "all. None means the field name moved, and the shipped code "
                "reads that as 'not_reported' and FAILS THE PATIENT -- which "
                "is cache-or-nothing working, and is a campaign that does not "
                "start",
                _wrote is not None)
            if _wrote or _warm_read:
                print("  the warmup left the prefix WARM "
                      f"({'wrote' if _wrote else 'already_warm'}). "
                      "classify_cache_write() would let the wave go out.")
            else:
                print("  THE WARMUP CACHED NOTHING. On this probe's tiny "
                      "prefix that is EXPECTED and documented; on a real "
                      "Stage 5 prefix it would stop the campaign. Re-run "
                      "with --per-trial-prefix-file before concluding.")
            for label, u in pt_usages[1:]:
                _r = u.get("cacheReadInputTokens")
                _in = u.get("inputTokens")
                print(f"  {label}: cacheRead={_r!r} nonCachedInput={_in!r} "
                      f"-> derived prompt_tokens="
                      f"{adapter._usage_block(u)['prompt_tokens']}")
                if _r:
                    check_true(f"(A12) {label} READ the shared prefix", True)
                else:
                    print(f"    {label} did not read the cache. If the warmup "
                          f"DID write, the two requests do not share a "
                          f"byte-identical prefix and that is a defect in "
                          f"build_converse_request, not in the account.")
            # THE DISJOINTNESS FORMULA, CHECKED AGAINST A REAL RESPONSE RATHER
            # THAN AGAINST A DOCSTRING. Converse's totalTokens is not
            # documented as including the cache terms, so this compares the
            # adapter's derived prompt_tokens against the vendor's own stated
            # sum instead.
            for label, u in pt_usages:
                _derived = adapter._usage_block(u)["prompt_tokens"]
                _stated = ((u.get("inputTokens") or 0)
                           + (u.get("cacheReadInputTokens") or 0)
                           + (u.get("cacheWriteInputTokens") or 0))
                check(f"({label}) the adapter's prompt_tokens equals AWS's own "
                      f"formula inputTokens + cacheRead + cacheWrite",
                      _derived, _stated)

            _report_cache_economics(
                pt_usages, len(_pt_system) // config.CHARS_PER_TOKEN,
                pt_latencies + elapsed, config, adapter)

    # ---- A6 -------------------------------------------------------------
    section("(A6) COST — priced from PRICING_CONFIG's Sonnet 4.6 rows")
    total_in = total_out = 0
    for raw in raw_responses:
        d = adapter._usage_block(raw.get("usage"))
        total_in += d["prompt_tokens"]
        total_out += d["completion_tokens"]
    model_key = config.matching_wire_model()
    try:
        cost = get_model_cost(model_key, total_in, total_out)
        print(f"  {len(raw_responses)} call(s): {total_in} input + "
              f"{total_out} output tokens (input INCLUDES cached, per the "
              f"disjointness formula)")
        print(f"  priced against {model_key!r}: ${cost:.6f}")
        if not model_key.startswith("global."):
            print("  *** THIS ROW IS INFERRED, NOT MEASURED. *** The AWS "
                  "Marketplace listing publishes GLOBAL dimensions only; the "
                  "+10% geo premium is carried over from the GPT-5.6 Terra "
                  "pattern. RECONCILE AGAINST THE CONSOLE BILL before any "
                  "campaign number rests on it.")
        print("  CACHED INPUT IS NOT DISCOUNTED IN THIS FIGURE — "
              "PRICING_CONFIG has no cached term, so a cache hit makes this an "
              "OVER-estimate. At $0.30 against $3.00 that gap is ~10x on the "
              "cached portion, which matters more here than on any other "
              "branch because the per-trial design depends on the cache.")
    except Exception as exc:                           # noqa: BLE001
        check(f"the model is priced in PRICING_CONFIG ({exc})", False, True)

    section("DEGRADATIONS RECORDED BY THE ADAPTER")
    recorded = dict(adapter.BEDROCK_ANTHROPIC_DEGRADATIONS)
    print(f"  {recorded or 'none'}")
    print("  'model_echo_unavailable' HERE IS EXPECTED unless A3 came back "
          "positive — translate_response() records it, and this probe calls "
          "that. The other keys are NOT expected: this probe builds the "
          "request with the adapter's own builder and issues it directly, so "
          "it never enters call_matching_model_bedrock_anthropic, which is "
          "where the once-per-process seed and effort bumps live. A real "
          "Stage 5 run records those on its first call; what the probe checks "
          "instead is stronger and is above — that no seed is on the wire.")

    # THE THROTTLE PHASE RUNS LAST AND THAT IS A CORRECTNESS PROPERTY, not an
    # ordering preference. It is the one phase that provokes 429s on purpose,
    # and botocore's standard retry mode charges a 500-token bucket 5 tokens
    # per throttling retry and stops retrying when it drains -- so any cache
    # reading taken after it could have been taken through a request that never
    # reached the service.
    # BEFORE THE THROTTLE PHASE AND AFTER EVERYTHING ELSE, for the reason
    # stated above it: the throttle phase provokes 429s on purpose and drains
    # botocore's retry quota, so a measurement taken after it could have been
    # taken through requests that never reached the service. It runs after A6
    # so its own cost is reported separately and cannot be confused with the
    # baseline calls' -- this phase's per-call output token count is the
    # finding, and folding it into a total would hide it.
    if args.probe_output_tokens:
        _probe_output_tokens(args, config, adapter, client)

    if args.probe_throttle:
        _probe_throttle_ceiling(args, config, adapter)

    return _summary()


# ===========================================================================
# THE SDK SHAPE PREFLIGHT — FREE, AND IT RUNS ABOVE EVERY PAID CALL
# ===========================================================================

# THE FLOOR, THE VERSION READER AND THE STATE VOCABULARY ALL LIVE IN
# `oncotriage/config.py` AND ARE NOT RESTATED HERE. They were declared in this
# file when only this file consulted them; the pipeline consults them now --
# `config.validate_matching_provider_config()` REFUSES a Converse run whose
# botocore predates the floor -- and a probe carrying its own copy of the
# number would be able to tell an operator something the pipeline will not act
# on. `config.MIN_BOTOCORE_FOR_CONVERSE_REQUEST` carries the measured bisect
# table and `config.botocore_sdk_state()` is the reader.
#
# WHAT THIS FILE STILL OWNS, and it is the half config cannot do: the request
# built by the adapter, validated by botocore's OWN ParamValidator against the
# service model of THIS client. A version number is a proxy for that; the
# validator is the fact. So the two are not redundant -- config refuses a
# version it knows is too old, and this refuses a request the SDK will not
# accept whatever the version says.
# Response members the adapter READS. These raise nothing when absent -- the
# field simply never arrives, `.get` returns None, and the shipped code records
# 'not_reported' and fails the patient. That is cache-or-nothing working, and
# from the outside it is indistinguishable from a provider that did not cache.
# So they are checked separately from the request members and reported as their
# own finding.
_RESPONSE_MEMBERS_READ = (("TokenUsage", "cacheReadInputTokens"),
                          ("TokenUsage", "cacheWriteInputTokens"),
                          ("TokenUsage", "cacheDetails"))


def _preflight_sdk_shape(client, kwargs, config):
    """Can the INSTALLED SDK express this request at all? Free. Returns bool.

    WHY THIS EXISTS, AND IT IS THE MOST EXPENSIVE LESSON THIS FILE HAS LEARNED.
    Run on 2026-09-03 without it, this probe built the adapter's real request,
    waited out its pacer, called `converse`, and got

        ParamValidationError: Unknown parameter in input: "outputConfig"
        Unknown parameter in system[1].cachePoint: "ttl"

    -- botocore's OWN validator, refusing locally. Nothing was signed, nothing
    was sent, nothing was billed. The probe then printed "A ValidationException
    naming outputConfig is A1", which points a reader at the PROVIDER and at an
    AWS support case for a request that never left the machine. THE TWO
    FAILURES ARE OPPOSITE FINDINGS WITH OPPOSITE REMEDIES: a
    ValidationException from the service means Bedrock refused the shape and
    the ADAPTER must change; a ParamValidationError from botocore means THIS
    MACHINE'S SDK PREDATES THE FEATURE and the DEPENDENCY must change. Only one
    of them is about this project's code.

    IT USES BOTOCORE'S OWN VALIDATOR RATHER THAN A WALK OF ITS SERVICE MODEL.
    `ParamValidator` is the exact object that raises inside `client.converse`,
    so running it here cannot disagree with what a real call would do -- which
    a hand-written "are these keys in the shape" check could, in either
    direction, and would then be a preflight that passes a request the SDK
    refuses.

    IT IS bedrock_anthropic ONLY. The Responses branch reaches Bedrock through
    the OpenAI SDK and has no botocore service model to validate against.
    """
    section("SDK PREFLIGHT — can the installed botocore express this request")
    state, reported, source = config.botocore_sdk_state()
    floor = config.botocore_floor_text()
    print(f"  botocore installed : {reported} ({source})")
    print(f"  minimum that can   : {floor}  (measured "
          f"{config.MIN_BOTOCORE_MEASURED_ON} by bisecting released wheels; "
          f"see config.MIN_BOTOCORE_FOR_CONVERSE_REQUEST)")
    print(f"  version verdict    : {state}")

    try:
        from botocore.validate import ParamValidator
        op = client.meta.service_model.operation_model("Converse")
        report = ParamValidator().validate(kwargs, op.input_shape)
        errors = report.generate_report() if report.has_errors() else ""
    except Exception as exc:                           # noqa: BLE001
        print(f"  the preflight itself could not run ({type(exc).__name__}: "
              f"{exc}); falling through to the live call, which is the "
              f"authority anyway.")
        return True

    # THE RESPONSE SIDE, WHICH RAISES NOTHING AND IS THEREFORE THE HALF THAT
    # WOULD OTHERWISE GO UNNOTICED.
    #
    # `_shape_resolver._shape_map` IS A BOTOCORE PRIVATE and this is the one
    # place here that reaches for one. So the failure is a THIRD state rather
    # than an empty list: `[]` means "checked, nothing missing" and None means
    # "could not check", and collapsing them would make a renamed botocore
    # internal read as a clean bill of health -- silence looking like success,
    # which is the shape this project removes.
    try:
        shapes = client.meta.service_model._shape_resolver._shape_map
        missing_response = [
            f"{shape}.{member}" for shape, member in _RESPONSE_MEMBERS_READ
            if member not in (shapes.get(shape, {}).get("members") or {})]
    except Exception as exc:                           # noqa: BLE001
        missing_response = None
        print(f"  the response-model check could not run "
              f"({type(exc).__name__}: {exc}) -- botocore's shape map is a "
              f"private and may have moved. NOT reported as clean.")

    if not errors:
        check_true("the installed botocore can express the adapter's request "
                   "(botocore's own ParamValidator, run offline)", True)
        if missing_response:
            print(f"  BUT the response model is missing {missing_response}. "
                  f"Those fields raise nothing when absent -- they simply "
                  f"never arrive -- so a 'not_reported' cache reading below "
                  f"would be about this SDK and not about the provider.")
        elif missing_response == []:
            check_true("...and every response member the adapter reads exists "
                       "in the model", True)
        return True

    check("the installed botocore can express the adapter's request",
          False, True)
    print("\n  botocore's own validator REFUSES it, offline:\n")
    for line in errors.strip().splitlines():
        print(f"    {line}")
    if missing_response:
        print(f"\n  ...and the response model is also missing "
              f"{missing_response}.")
    print("\n  THIS IS NOT A FINDING ABOUT AMAZON BEDROCK OR ABOUT THIS "
          "ACCOUNT.")
    print("  No request was signed and none was sent. The shape the adapter "
          "builds is")
    print("  newer than the service model this botocore ships, so EVERY "
          "Stage 5 call on")
    print(f"  provider {config.MATCHING_PROVIDER!r} fails here, locally, "
          f"before the wire.")
    # FOUR STATES, FOUR REMEDIES, AND THE THIRD IS THE ONE THAT MUST NOT BE
    # FOLDED INTO THE OTHERS. `config.classify_botocore_version` is the owner;
    # this branches on its closed vocabulary exhaustively, so a state added
    # there and not handled here reaches the `else` by name rather than being
    # absorbed into "at or above the floor" -- which would be a claim about a
    # number nobody read.
    if state == config.BOTOCORE_SDK_TOO_OLD:
        print(f"\n  REMEDY: botocore >= {floor}. The installed {reported} "
              f"predates it.")
        print(f"          {config.botocore_upgrade_command()}")
        print("          NOTE: reaching this line at all means the pipeline's "
              "own refusal was")
        print("          bypassed -- config.validate_matching_provider_config()"
              " refuses this")
        print("          state before any request is built.")
    elif state == config.BOTOCORE_SDK_VERSION_UNREADABLE:
        print(f"\n  REMEDY: botocore >= {floor}. The installed version string "
              f"({reported!r}) could not be")
        print("          parsed, so whether it is below that floor is NOT "
              "established here --")
        print("          the refusal above is, and it is the authority.")
    elif state == config.BOTOCORE_SDK_ABSENT:
        print(f"\n  REMEDY: botocore >= {floor}. No botocore version could be "
              f"read at all, so the")
        print("          floor is unverified; the refusal above is the "
              "authority.")
        print(f"          {config.botocore_upgrade_command()}")
    else:
        print("\n  REMEDY: the installed botocore is at or above the "
              "measured floor, so this is")
        print("          a DIFFERENT refusal from the one that floor "
              "describes. Read the lines")
        print("          above and fix the request the adapter builds.")
    print("\n  REFUSING. Nothing was called. Nothing was billed.")
    return False


# ===========================================================================
# THE PER-TRIAL OUTPUT-TOKEN MEASUREMENT
# ===========================================================================

def _probe_output_tokens(args, config, adapter, client):
    """One real trial-shaped call per rendered user message. THE MEASUREMENT.

    WHAT IT ANSWERS. `MATCHING_OUTPUT_TOKENS_PER_TRIAL` is the input to Stage
    5's pre-split guard. It was 1,100, derived on gpt-5.6-terra over 27 runs at
    reasoning_effort='none' -- a guard calibrated on a model the shipped
    provider does not call. THIS PHASE IS WHAT RE-DERIVED IT: run on
    2026-09-03 over eight real trial blocks of one real patient, it measured a
    maximum of 1,356 output tokens per verdict and the constant became 1,450.
    IT IS 2,500 NOW, and the correction is worth reading before trusting this
    phase's n: the empty-verdict investigation of the same day resent ONE of
    those eight trials twelve times and measured 2,234 -- 1.65x this phase's
    maximum, from the SAME judge on the SAME prefix. Eight calls over eight
    trials is a wide sample of INPUTS and a sample of one per input; twelve
    calls over one input is the reverse, and the tail this phase could not see
    is the one that moved the constant. Both tables, and the reason the value
    is marked INTERIM, are at the constant in `oncotriage/config.py`.

    IT IS NOT A ONE-OFF. The constant's own block says to re-derive it whenever
    the model, the provider or the thinking/effort configuration changes, and
    this is the command that does so: one call per supplied trial message over
    one shared prefix, reporting `usage.outputTokens` for each.

    WHY THE REQUESTS ARE THE ADAPTER'S AND NOT THIS FILE'S. Every one is built
    by `adapter.build_converse_request(prefix, user)` -- the same builder the
    node calls -- so maxTokens is `MATCHING_MAX_TOKENS`, `outputConfig` carries
    the real schema and the system block carries the real cachePoint. A
    hand-built request would measure the model's verbosity under a shape Stage
    5 does not send, which is the number the guard would then be set from.

    NO WARMUP OF ITS OWN, DELIBERATELY. The brief's step 3 is five calls, and a
    sixth would be a second cache write nobody asked for. It relies on a
    `--probe-per-trial` run inside `BEDROCK_ANTHROPIC_CACHE_TTL` and REPORTS
    each call's `cacheReadInputTokens` so a reader can see whether it got one.
    A zero read costs money and costs the measurement NOTHING: outputTokens is
    unaffected by whether the input was cached.
    """
    from oncotriage.agent.response_schema import (
        EVALUATIONS_KEY, build_response_schema)
    from oncotriage.utils import get_model_cost

    section("OUTPUT TOKENS — one real trial-shaped call per rendered message")

    try:
        prefix = open(args.per_trial_prefix_file, encoding="utf-8").read()
    except OSError as exc:
        print(f"  REFUSED: could not read {args.per_trial_prefix_file!r}: "
              f"{exc}")
        print("  Nothing extra was called. Nothing extra was billed.")
        return

    messages = []
    for path in args.per_trial_user_file:
        try:
            messages.append((path, open(path, encoding="utf-8").read()))
        except OSError as exc:
            print(f"  REFUSED: could not read {path!r}: {exc}")
            print("  Nothing extra was called. Nothing extra was billed.")
            return

    print(f"  prefix   : {args.per_trial_prefix_file!r} "
          f"({len(prefix):,} chars, "
          f"~{len(prefix) // config.CHARS_PER_TOKEN:,} tokens)")
    print(f"  messages : {len(messages)}")
    print(f"  ceiling  : maxTokens = MATCHING_MAX_TOKENS = "
          f"{config.MATCHING_MAX_TOKENS:,}")
    print(f"  the constant under measurement: "
          f"MATCHING_OUTPUT_TOKENS_PER_TRIAL = "
          f"{config.MATCHING_OUTPUT_TOKENS_PER_TRIAL:,}")

    dump_dir = args.dump_replies
    if dump_dir:
        # BEFORE THE FIRST CALL, DELIBERATELY. A directory that cannot be
        # created is a configuration fault, and discovering it after eight
        # billed calls means the replies those calls paid for are gone.
        try:
            os.makedirs(dump_dir, exist_ok=True)
        except OSError as exc:
            print(f"  REFUSED: could not create {dump_dir!r}: {exc}")
            print("  Nothing was called. Nothing was billed.")
            return
        # AND IT REFUSES A DIRECTORY THAT ALREADY HOLDS REPLIES. Filenames key
        # on the per-call index, which restarts at 1 every run -- so a second
        # run into the same directory overwrites the first run's evidence,
        # silently, and that evidence was PAID FOR and cannot be recovered.
        # Same class as the "a flag that silently does nothing" refusals above.
        try:
            _existing = sorted(n for n in os.listdir(dump_dir)
                               if n.startswith("reply_"))
        except OSError as exc:
            print(f"  REFUSED: could not read {dump_dir!r}: {exc}")
            print("  Nothing was called. Nothing was billed.")
            return
        if _existing:
            print(f"  REFUSED: {dump_dir!r} already holds "
                  f"{len(_existing)} reply file(s) (e.g. {_existing[0]!r}). "
                  f"Point --dump-replies at a new directory; those were paid "
                  f"for and this run would overwrite them.")
            print("  Nothing was called. Nothing was billed.")
            return
        print(f"  raw replies -> {dump_dir}")

    schema = build_response_schema()
    rows = []
    _first_system = None
    for i, (path, user) in enumerate(messages, start=1):
        label = f"message {i}/{len(messages)}"
        # BUILT INSIDE THE GUARD, not above it. `build_converse_request`
        # validates its own inputs and raises; outside a handler that raise
        # would leave this phase with no summary and the run with a traceback
        # where it owed a recorded failure -- the abort shape this project has
        # shipped more times than any other.
        try:
            kwargs = adapter.build_converse_request(prefix, user)
        except Exception as exc:                       # noqa: BLE001
            print(f"\n  --- {label}: {path} ---")
            print(f"  REFUSED before the call: {type(exc).__name__}: {exc}")
            _RESULTS["failed"] += 1
            _FAILURES.append(f"{label} could not be built: "
                             f"{type(exc).__name__}: {exc}")
            continue
        # FREE, AND IT IS THE ONE THING THAT WOULD INVALIDATE EVERY CACHE READ
        # BELOW: a system block that differs between two calls is a prefix
        # neither of them shares.
        if _first_system is None:
            _first_system = json.dumps(kwargs["system"], sort_keys=True)
        else:
            check(f"({label}) carries the SAME system block as the first one "
                  f"built -- a system block that differs between two calls is "
                  f"a prefix neither of them shares",
                  json.dumps(kwargs["system"], sort_keys=True), _first_system)

        print(f"\n  --- {label}: {path} ---")
        print(f"  user message: {len(user):,} chars")
        _PACER.wait(label)
        _t0 = time.monotonic()
        try:
            raw = client.converse(**kwargs)
        except Exception as exc:                       # noqa: BLE001
            category = adapter.classify_error(exc)
            print(f"  RAISED  {type(exc).__name__} [{category}]")
            print(f"          {exc}")
            print_aws_error(exc)
            _RESULTS["failed"] += 1
            _FAILURES.append(f"{label} raised {type(exc).__name__} "
                             f"[{category}]: {exc}")
            break
        latency = time.monotonic() - _t0
        u = raw.get("usage") or {}
        derived = adapter._usage_block(u)
        # THE TRANSLATION CAN RAISE -- the adapter has a whole error category
        # for it -- and it is the LAST thing that should be able to end this
        # phase: the call is already paid for and `usage` is already in hand,
        # so a translation fault must cost the TEXT and not the MEASUREMENT.
        # outputTokens is read off `u`, never off the translated object.
        try:
            translated = adapter.translate_response(raw)
            text = (translated.choices[0].message.content or "")
            translation_error = None
        except Exception as exc:                       # noqa: BLE001
            text, translation_error = "", f"{type(exc).__name__}: {exc}"
            print(f"  translate_response RAISED ({translation_error}); the "
                  f"usage block below is still this call's own.")
            _RESULTS["failed"] += 1
            _FAILURES.append(f"{label} could not be translated: "
                             f"{translation_error}")

        # PARSE VALIDITY, REPORTED IN THE TWO PIECES IT IS MADE OF. A fenced
        # response is not malformed -- the node strips fences before parsing --
        # so a fence is recorded and reported rather than counted as a failure,
        # and the parse is then attempted on the same text the node would have
        # parsed.
        fenced = text.strip().startswith("```")
        body = text.strip()
        if fenced:
            body = body.split("```")[1]
            if body.startswith("json"):
                body = body[4:]
        if translation_error is not None:
            problems, n_evals = [f"not translated: {translation_error}"], None
        else:
            try:
                parsed = json.loads(body)
                problems = validate_against_schema(parsed, schema)
                n_evals = (len(parsed.get(EVALUATIONS_KEY) or [])
                           if isinstance(parsed, dict) else None)
            except Exception as exc:                   # noqa: BLE001
                problems, n_evals = [f"json.loads: {exc}"], None

        # WRITTEN BEFORE ANYTHING ELSE CAN RAISE. `check()` below can fail
        # and the loop can `break`; the reply is already paid for by this
        # point and losing it to a later fault means buying it again.
        if dump_dir:
            base = os.path.splitext(os.path.basename(path))[0]
            stem = os.path.join(dump_dir, f"reply_{i:02d}_{base}")
            try:
                with open(stem + ".txt", "w", encoding="utf-8") as fh:
                    fh.write(text)
                with open(stem + ".json", "w", encoding="utf-8") as fh:
                    json.dump({
                        "message_index": i,
                        "user_file": path,
                        "user_chars": len(user),
                        "stop_reason": raw.get("stopReason"),
                        "usage": u,
                        "latency_s": round(latency, 3),
                        "text_chars": len(text),
                        "text_sha256": hashlib.sha256(
                            text.encode("utf-8")).hexdigest(),
                        "request_id": (raw.get("ResponseMetadata") or {}).get(
                            "RequestId"),
                        "translation_error": translation_error,
                    }, fh, indent=1, sort_keys=True)
            except OSError as exc:
                print(f"  could not write the raw reply: {exc}")
        print(f"  RAW REPLY ({len(text):,} chars): "
              f"{text[:400]!r}{' ...' if len(text) > 400 else ''}")
        print(f"  elapsed {latency:.1f}s   stopReason "
              f"{raw.get('stopReason')!r}")
        print(f"  outputTokens={u.get('outputTokens')}  "
              f"inputTokens={u.get('inputTokens')}  "
              f"cacheRead={u.get('cacheReadInputTokens')}  "
              f"cacheWrite={u.get('cacheWriteInputTokens')}")
        check(f"({label}) the response is well-formed under the shipped "
              f"schema{' (fenced; fence stripped first)' if fenced else ''}",
              problems, [])
        # THE NON-DEGENERACY TWIN, AND IT IS NOT DECORATION. `{"evaluations":
        # []}` is WELL-FORMED under the shipped schema -- the array carries no
        # `minItems` -- and it evaluates nothing. So the check above passes for
        # a response that answered no question, and on the first run of this
        # phase it did exactly that: message 1, the LARGEST trial block of the
        # five, came back at 30 output tokens and was reported PASS. A verdict
        # count is what separates "the model answered and the answer conforms"
        # from "the model declined and the decline conforms", and only the
        # first is a measurement of what a verdict costs.
        check_true(f"({label}) ...and carries at least ONE evaluation, so the "
                   f"conformance above is not a well-formed non-answer",
                   isinstance(n_evals, int) and n_evals >= 1)
        rows.append({
            "path": path,
            "user_chars": len(user),
            "output_tokens": u.get("outputTokens"),
            "input_tokens": u.get("inputTokens"),
            "cache_read": u.get("cacheReadInputTokens"),
            "cache_write": u.get("cacheWriteInputTokens"),
            "prompt_tokens_derived": derived["prompt_tokens"],
            "stop_reason": raw.get("stopReason"),
            "latency_s": round(latency, 2),
            "parsed_ok": problems == [],
            "fenced": fenced,
            "translation_error": translation_error,
            "evaluations": n_evals,
            "text_chars": len(text),
        })

    if not rows:
        print("\n  nothing was measured.")
        return

    section("OUTPUT TOKENS — the finding")
    # `evals` AND `chars` ARE PRINTED BECAUSE THEY WERE ALREADY CAPTURED AND
    # DROPPED. Without them a 30-output-token row is uninterpretable -- it
    # could be a terse verdict or an empty array -- and the reader's only route
    # to the answer is another billed call. They were in `rows` from the start;
    # only the table was short.
    print(f"  {'#':>2} {'user chars':>10} {'outputTokens':>13} "
          f"{'cacheRead':>10} {'stopReason':>12} {'parsed':>7} "
          f"{'evals':>6} {'chars':>7} {'USD':>9}")
    print(f"  {'-' * 2} {'-' * 10} {'-' * 13} {'-' * 10} {'-' * 12} "
          f"{'-' * 7} {'-' * 6} {'-' * 7} {'-' * 9}")
    model = config.matching_wire_model()
    phase_cost = 0.0
    for i, r in enumerate(rows, start=1):
        try:
            cost = get_model_cost(model, r["prompt_tokens_derived"] or 0,
                                  r["output_tokens"] or 0)
        except Exception:                              # noqa: BLE001
            cost = float("nan")
        phase_cost += 0.0 if cost != cost else cost
        print(f"  {i:>2} {r['user_chars']:>10,} {(r['output_tokens'] or 0):>13,} "
              f"{(r['cache_read'] or 0):>10,} {str(r['stop_reason']):>12} "
              f"{str(r['parsed_ok']):>7} {str(r['evaluations']):>6} "
              f"{r['text_chars']:>7,} {cost:>9.4f}")

    observed = [r["output_tokens"] for r in rows
                if isinstance(r["output_tokens"], int)]
    if observed:
        lo, hi = min(observed), max(observed)
        mean = sum(observed) / len(observed)
        print(f"\n  n={len(observed)}  min {lo:,}  mean {mean:,.0f}  "
              f"max {hi:,}  spread {hi - lo:,}")
        # THE ANSWERING SUBSET IS REPORTED SEPARATELY, AND WHICH STATISTIC IT
        # MOVES IS THE POINT. A well-formed non-answer (`evaluations: []`) can
        # only pull the MIN and the MEAN down; it can never raise the MAX. So a
        # ceiling adopted from the max is unaffected by one, and a mean quoted
        # as "what a verdict costs" is not -- which is why both are here rather
        # than only the one this file's own adoption rule reads.
        answered = [r["output_tokens"] for r in rows
                    if isinstance(r["output_tokens"], int)
                    and isinstance(r["evaluations"], int)
                    and r["evaluations"] >= 1]
        blank = len(observed) - len(answered)
        if blank:
            # `answered` CAN BE EMPTY AND THE FIRST VERSION OF THIS BLOCK
            # RAISED ON IT. A run in which EVERY response is a non-answer is
            # not hypothetical -- it is what a single-message confirmation of
            # the empty-array finding IS -- and `min([])` ended the phase with
            # a traceback where it owed a summary, one line below a check that
            # had correctly just failed. The abort shape this project has
            # shipped more often than any other, reproduced inside the guard
            # written to catch a degenerate value. Measured, not reasoned
            # about: it happened on the first run of this code.
            print(f"  {blank} of {len(observed)} response(s) carried ZERO "
                  f"evaluations -- well-formed, and not a verdict.")
            if answered:
                print(f"  over the {len(answered)} that ANSWERED: "
                      f"min {min(answered):,}  "
                      f"mean {sum(answered) / len(answered):,.0f}  "
                      f"max {max(answered):,}")
                print("  THE MAX IS UNAFFECTED BY THEM BY CONSTRUCTION (an "
                      "empty answer is short), so a ceiling adopted from the "
                      "max stands; the MEAN above is the one a reader must "
                      "not quote as the cost of a verdict.")
            else:
                print("  NOTHING ANSWERED, so there is no verdict-cost "
                      "figure in this run at all and the max above is the "
                      "size of a REFUSAL. Do not adopt a ceiling from it.")
        else:
            print(f"  every response carried at least one evaluation, so the "
                  f"figures above are all verdicts.")
        print(f"  MATCHING_OUTPUT_TOKENS_PER_TRIAL is "
              f"{config.MATCHING_OUTPUT_TOKENS_PER_TRIAL:,}; the measured max "
              f"is {hi:,} "
              f"({hi / config.MATCHING_OUTPUT_TOKENS_PER_TRIAL:.2f}x it).")
        print(f"  the largest measured response used "
              f"{hi / config.MATCHING_MAX_TOKENS:.4%} of MATCHING_MAX_TOKENS "
              f"({config.MATCHING_MAX_TOKENS:,}).")
        # THE CORRELATION THE gpt-5.6-terra CALIBRATION FOUND TO BE ABSENT,
        # re-asked on this model. Reported, never used to fit anything: five
        # points cannot support a regression and this file does not pretend
        # otherwise.
        print(f"  user-message chars vs outputTokens, in the order supplied: "
              f"{[(r['user_chars'], r['output_tokens']) for r in rows]}")
    print(f"\n  THIS PHASE COST ${phase_cost:.4f} at PRICING_CONFIG's "
          f"uncached rates (an OVER-estimate wherever cacheRead is non-zero).")
    print("  n IS SMALL AND STATED: five trials of ONE patient. It bounds "
          "what one verdict costs on this model; it is not a distribution.")
    return rows


# ===========================================================================
# (A14) THE THROTTLE CEILING, AND A REQUEST ID FOR THE SUPPORT CASE
# ===========================================================================

# THE DOCUMENTED CACHE RATES. Read 2026-08-30 from the AWS Marketplace listing
# the Claude Sonnet 4.6 model card names (prod-ffvjxvh4ltq64), in US dollars
# per MILLION tokens. THESE ARE GLOBAL DIMENSIONS; that listing publishes no
# geo rows, so a `us.`/`eu.`/`au.`/`jp.` profile is priced here by the SAME
# +10% premium PRICING_CONFIG already infers for those rows -- INFERRED, not
# measured, and labelled as such everywhere it is used.
#
# THEY ARE NOT IN PRICING_CONFIG AND THAT IS DELIBERATE (A13): that table is an
# {input, output} pair shared with every historical row, and introducing a
# cached term re-bases the whole series. So the cache arithmetic lives here,
# where it is a report rather than a stored figure.
_CACHE_RATES_GLOBAL_USD_PER_1M = {
    "input": 3.00,
    "output": 15.00,
    "cache_read": 0.30,
    "cache_write_5m": 3.75,
    "cache_write_1h": 6.00,
}
_GEO_PREMIUM = 1.10


def _cache_rates_for(wire_model):
    """The documented rates, with the geo premium applied when it applies."""
    inferred = not wire_model.startswith("global.")
    factor = _GEO_PREMIUM if inferred else 1.0
    return ({k: v * factor for k, v in _CACHE_RATES_GLOBAL_USD_PER_1M.items()},
            inferred)


def _probe_throttle_ceiling(args, config, adapter):
    """Exceed the ceiling on purpose, and come back with evidence.

    IT RUNS LAST, AND THAT IS A CORRECTNESS PROPERTY RATHER THAN TIDINESS.
    botocore's standard retry mode charges a 500-token bucket 5 tokens per
    throttling retry and, in AWS's own words, "when the available tokens are
    exhausted" it stops retrying altogether. A phase that provokes throttling
    before the cache measurements would leave those measurements taken through
    a drained quota, and a cache reading whose request never actually reached
    the service is worse than no reading.

    IT BUILDS ITS OWN CLIENT WITH RETRIES OFF, and that is a deliberate
    departure from `config.get_bedrock_anthropic_client()` rather than an
    oversight. That client sets `max_attempts = OPENAI_SDK_MAX_RETRIES + 1`
    precisely so a transient 429 is absorbed -- correct for a campaign, and
    useless here: an absorbed 429 is one this probe cannot count, cannot time,
    and cannot quote a request id for. `max_attempts=1` is one attempt and no
    retry, so every throttle surfaces with its own response metadata.

    TWO WAVES, AND THE SECOND IS WHAT ANSWERS THE QUESTION. A single burst
    tells you the ceiling binds; it cannot tell you whether it RECOVERS. Wave 2
    is issued after a recovery pause, and the two together separate BURSTY
    (wave 1 throttles, wave 2 is clean -- raise the retry budget and ride it
    out) from SUSTAINED (both throttle -- lower the parallel bound, because
    botocore's retry quota drains and a bigger budget does nothing).

    THE REQUESTS ARE THE CHEAPEST VALID ONES THIS ADAPTER CAN BUILD: the
    warmup shape over the probe's tiny system prompt, `maxTokens = 1` and no
    structured-output block. Throttling is a property of the request COUNT, not
    of its size, so paying for content here would buy nothing.
    """
    import concurrent.futures as _cf

    try:
        import boto3
        from botocore.config import Config as _BotoConfig
    except ImportError as exc:
        print(f"  REFUSED: --probe-throttle needs boto3 ({exc}).")
        return None

    section("(A14) THROTTLE CEILING — deliberately over the limit, UNPACED")
    print(f"  This account's APPLIED requests-per-minute quota for this model "
          f"was measured at {args.max_rpm:.0f} in the AWS console "
          f"(2026-08-30). Everything above this section held itself under it; "
          f"this section does not.")
    print(f"  Retries are OFF for this phase only (max_attempts=1), so every "
          f"429 surfaces instead of being absorbed.")

    throttle_client = boto3.client(
        "bedrock-runtime",
        region_name=config.BEDROCK_REGION,
        config=_BotoConfig(
            connect_timeout=config.BEDROCK_ANTHROPIC_CONNECT_TIMEOUT_SECONDS,
            read_timeout=config.MATCHING_REQUEST_TIMEOUT_SECONDS,
            retries={"max_attempts": 1, "mode": "standard"}),
    )
    kw = adapter.build_converse_request(
        PROBE_SYSTEM, config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE,
        warmup=True)

    def _one(idx):
        t0 = time.monotonic()
        try:
            raw = throttle_client.converse(**kw)
            return {"i": idx, "ok": True, "s": t0,
                    "e": time.monotonic(),
                    "rid": (raw.get("ResponseMetadata") or {}).get("RequestId"),
                    "usage": raw.get("usage") or {}}
        except Exception as exc:                       # noqa: BLE001
            return {"i": idx, "ok": False, "s": t0, "e": time.monotonic(),
                    "cls": type(exc).__name__,
                    "cat": adapter.classify_error(exc),
                    "msg": str(exc),
                    "ev": aws_error_evidence(exc)}

    waves = []
    plan = [("wave 1", args.throttle_burst),
            ("wave 2", max(4, args.throttle_burst // 2))]
    for wi, (label, n) in enumerate(plan):
        if wi:
            pause = 65.0
            print(f"\n  --- recovery pause {pause:.0f}s before {label} "
                  f"(one enforcement window plus margin) ---")
            time.sleep(pause)
        print(f"\n  --- {label}: {n} requests submitted at once, unpaced ---")
        w0 = time.monotonic()
        with _cf.ThreadPoolExecutor(max_workers=n) as pool:
            res = sorted(pool.map(_one, range(n)), key=lambda r: r["i"])
        span = time.monotonic() - w0
        ok = [r for r in res if r["ok"]]
        bad = [r for r in res if not r["ok"]]
        thr = [r for r in bad if r.get("cat") == "throttling"
               or (r.get("ev") or {}).get("ErrorCode") == "ThrottlingException"]
        print(f"  {len(ok)}/{n} accepted, {len(thr)} throttled, "
              f"{len(bad) - len(thr)} failed otherwise, over {span:.1f}s")
        # A RATE NEEDS A WINDOW. The burst is submitted at once, so its span
        # can be a fraction of a second -- and `accepted / span * 60` then
        # reports a five-figure requests-per-minute figure that LOOKS like a
        # measurement of the ceiling and is an artefact of dividing by nearly
        # zero. The primary evidence is the COUNT accepted out of a burst
        # issued inside one enforcement window; the rate is printed only when
        # the window is wide enough to carry one.
        if span >= 1.0:
            print(f"  observed ACCEPTED rate: "
                  f"{len(ok) / span * 60:.1f} requests/min over {span:.1f}s")
        else:
            print(f"  (the burst completed in {span:.2f}s -- too short to "
                  f"support a per-minute rate; the ACCEPTED COUNT above is "
                  f"the measurement)")
        waves.append({"label": label, "n": n, "span": span, "ok": ok,
                      "thr": thr, "other": [r for r in bad if r not in thr]})

        for r in res:
            tag = ("OK " if r["ok"] else
                   ("429" if r in thr else "ERR"))
            rid = r.get("rid") or (r.get("ev") or {}).get("RequestId")
            print(f"    [{tag}] #{r['i']:>2}  "
                  f"+{r['e'] - w0:5.1f}s  RequestId={rid}")

        for r in thr[:3]:
            print(f"\n  --- VERBATIM throttled response #{r['i']} ---")
            print(f"    exception: {r['cls']}: {r['msg']}")
            for k, v in (r.get("ev") or {}).items():
                print(f"    {k}: {v}")

    # ---- the reading -----------------------------------------------------
    w1, w2 = waves[0], waves[1]
    print()
    check_true("(A14) at least one 429 was produced, so the ceiling was "
               "actually reached and this section measured something",
               bool(w1["thr"] or w2["thr"]))
    ids = [(r.get("ev") or {}).get("RequestId")
           for r in (w1["thr"] + w2["thr"])]
    ids = [i for i in ids if i]
    check_true("(A14) a throttled response carried a RequestId, which is the "
               "value an AWS support case asks for", bool(ids))

    if w1["thr"] and not w2["thr"]:
        verdict = ("BURSTY — wave 1 throttled and wave 2, after a recovery "
                   "pause, did not. The limit refills. Raising "
                   "BEDROCK_ANTHROPIC_MAX_ATTEMPTS lets a campaign ride these "
                   "out.")
    elif w1["thr"] and w2["thr"]:
        verdict = ("SUSTAINED — both waves throttled. botocore's retry quota "
                   "drains under sustained throttling and stops retrying, so a "
                   "bigger BEDROCK_ANTHROPIC_MAX_ATTEMPTS does nothing: the "
                   "remedy is a smaller BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS.")
    elif not w1["thr"]:
        verdict = ("NOT REACHED — no 429 at this burst size. Either the "
                   "applied quota is higher than the console reported, or the "
                   "enforcement window is wider than one minute. Re-run with a "
                   "larger --throttle-burst before concluding the quota is "
                   "not binding.")
    else:
        verdict = "MIXED — read the per-request timeline above."
    print(f"\n  A14 VERDICT: {verdict}")

    # THE HEADLINE IS A COUNT, NOT A RATE, and that is the honest form of it:
    # wave 1 submits `n` requests inside one enforcement window, so "how many
    # were accepted" IS the ceiling as the service applied it. A rate is
    # derived only when the wave lasted long enough to divide by.
    accepted = len(w1["ok"])
    accepted_rate = ((accepted / w1["span"] * 60)
                     if w1["span"] >= 1.0 else None)
    print(f"  MEASURED CEILING: {accepted} of {w1['n']} requests accepted in "
          f"a single unpaced burst ({w1['span']:.2f}s), "
          f"{len(w1['thr'])} throttled.")
    if accepted_rate is not None:
        print(f"  ...i.e. {accepted_rate:.1f} accepted requests/min over that "
              f"window.")
    print(f"  The console-reported APPLIED quota was {args.max_rpm:.0f}/min. "
          f"An accepted count at or near that is the quota confirming itself; "
          f"a much larger one means the enforcement window is wider than this "
          f"burst and the ceiling was not really reached.")
    print(f"  Compare against config.per_trial_parallel_bound() = "
          f"{config.per_trial_parallel_bound()}: a per-trial wave issues that "
          f"many at once and repeats "
          f"ceil({config.MAX_TRIALS_FOR_EVALUATION}/"
          f"{config.per_trial_parallel_bound()}) = "
          f"{-(-config.MAX_TRIALS_FOR_EVALUATION // config.per_trial_parallel_bound())} "
          f"times per patient, plus one warmup.")
    return {"waves": waves, "request_ids": ids, "verdict": verdict,
            "accepted_rate_per_min": accepted_rate}


def _report_cache_economics(pt_usages, prefix_tokens, latencies, config,
                            adapter):
    """(A2)(A13) What the cache measured, and what it is worth. ARITHMETIC.

    EVERY TOKEN COUNT HERE IS MEASURED from a real usage block. Every PRICE is
    DOCUMENTED (the Marketplace listing) and, off `global.`, carries an
    INFERRED +10% geo premium. The two are kept apart in the printout, because
    a figure that mixes a measured quantity with an inferred rate is only as
    good as the rate and must say so.
    """
    section("(A2)(A13)(item 6) CACHE ECONOMICS — measured tokens, documented "
            "rates")
    wire = config.matching_wire_model()
    rates, inferred = _cache_rates_for(wire)
    print(f"  rates for {wire!r} (USD per 1M): "
          f"input {rates['input']:.2f}, output {rates['output']:.2f}, "
          f"cache read {rates['cache_read']:.2f}, "
          f"cache write 5m {rates['cache_write_5m']:.2f}, "
          f"cache write 1h {rates['cache_write_1h']:.2f}")
    if inferred:
        print("  *** THE +10% GEO PREMIUM ON THESE IS INFERRED, NOT MEASURED. "
              "The Marketplace listing publishes GLOBAL dimensions only. "
              "Reconcile against the console bill. ***")

    if len(pt_usages) < 3:
        print("  (no per-trial sequence was captured; nothing to price)")
        return None

    def _u(u, k):
        return u.get(k) or 0

    warm = pt_usages[0][1]
    trials = [u for _, u in pt_usages[1:]]
    wrote = _u(warm, "cacheWriteInputTokens")
    reads = [_u(u, "cacheReadInputTokens") for u in trials]
    fresh = [_u(u, "inputTokens") for u in trials]
    outs = [_u(u, "outputTokens") for u in trials]

    print(f"\n  MEASURED: warmup wrote {wrote} cached tokens; "
          f"trial reads {reads}; non-cached input per trial {fresh}; "
          f"output per trial {outs}")

    ttl_key = ("cache_write_1h" if config.BEDROCK_ANTHROPIC_CACHE_TTL == "1h"
               else "cache_write_5m")
    per = 1_000_000.0
    cached_cost = (wrote * rates[ttl_key] / per
                   + sum(reads) * rates["cache_read"] / per
                   + sum(fresh) * rates["input"] / per
                   + sum(outs) * rates["output"] / per)
    # The same three calls with the cache doing nothing: every token that was
    # read from cache would instead be fresh input at the full rate.
    uncached_cost = ((wrote + sum(fresh) + sum(reads)) * rates["input"] / per
                     + sum(outs) * rates["output"] / per)
    print(f"  these 3 calls WITH the cache   : ${cached_cost:.6f}")
    print(f"  the same 3 calls WITHOUT it    : ${uncached_cost:.6f}")
    if uncached_cost > 0:
        print(f"  saving on this sequence        : "
              f"{(1 - cached_cost / uncached_cost) * 100:.1f}%")

    # ---- extrapolated to a real patient ---------------------------------
    n = config.MAX_TRIALS_FOR_EVALUATION
    per_trial_fresh = (sum(fresh) / len(fresh)) if fresh else 0
    per_trial_out = (sum(outs) / len(outs)) if outs else 0
    read_each = (sum(reads) / len(reads)) if reads else 0
    real = (wrote * rates[ttl_key] / per
            + n * read_each * rates["cache_read"] / per
            + n * per_trial_fresh * rates["input"] / per
            + n * per_trial_out * rates["output"] / per)
    flat = ((wrote + n * (read_each + per_trial_fresh)) * rates["input"] / per
            + n * per_trial_out * rates["output"] / per)
    print(f"\n  EXTRAPOLATED to one {n}-trial patient at this prefix size "
          f"(~{prefix_tokens} prefix tokens), 1 warmup + {n} trial calls:")
    print(f"    with the cache    : ${real:.4f} / patient")
    print(f"    without the cache : ${flat:.4f} / patient")
    if real > 0:
        print(f"    per-trial mode is affordable at "
              f"{flat / real:.1f}x cheaper than the uncached same-shape run")
    print(f"    over 1,000 patients: ${real * 1000:.2f} vs ${flat * 1000:.2f}")

    # ---- 5m versus 1h ----------------------------------------------------
    d5 = wrote * rates["cache_write_5m"] / per
    d1 = wrote * rates["cache_write_1h"] / per
    print(f"\n  TTL: the write is billed once per patient. At the measured "
          f"{wrote} written tokens that is ${d5:.6f} at 5m and ${d1:.6f} at "
          f"1h — a difference of ${(d1 - d5) * 1000:.2f} over 1,000 patients.")
    print(f"  BEDROCK_ANTHROPIC_CACHE_TTL is "
          f"{config.BEDROCK_ANTHROPIC_CACHE_TTL!r}.")

    # ---- the TTL / read-budget collision, on measured latency ------------
    if latencies:
        worst = max(latencies)
        ttl_seconds = 3600 if config.BEDROCK_ANTHROPIC_CACHE_TTL == "1h" else 300
        bound = config.per_trial_parallel_bound()
        print(f"\n  THE 300s-READ-BUDGET vs {ttl_seconds}s-TTL COLLISION, on "
              f"MEASURED latency rather than on the timeout:")
        print(f"    slowest call observed here : {worst:.1f}s")
        print(f"    MATCHING_REQUEST_TIMEOUT_SECONDS : "
              f"{config.MATCHING_REQUEST_TIMEOUT_SECONDS}s")
        print(f"    the TTL resets on every hit, so what must stay under it is "
              f"the GAP between two consecutive prefix-sharing requests, not "
              f"the whole wave.")
        print(f"    at parallel bound {bound} the wave submits every request "
              f"up front, so the largest gap is ~one call's latency "
              f"({worst:.1f}s observed) — "
              f"{'INSIDE' if worst < ttl_seconds else 'OUTSIDE'} the "
              f"{ttl_seconds}s window with "
              f"{ttl_seconds - worst:.0f}s to spare.")
        _margin = ttl_seconds - config.MATCHING_REQUEST_TIMEOUT_SECONDS
        print(f"    THE COLLISION IS REAL ONLY IN THE WORST CASE: a call that "
              f"runs to the full {config.MATCHING_REQUEST_TIMEOUT_SECONDS}s "
              f"read budget leaves {_margin}s of margin at "
              f"{config.BEDROCK_ANTHROPIC_CACHE_TTL} -- "
              f"{'none' if _margin <= 0 else 'thin'}.")
        # GUARDED. A ratio against a latency of ~0 is not a headroom figure,
        # it is a division by nearly zero wearing one -- and it would print
        # with a straight face on a stub, on a cached instant reply, or on any
        # sample fast enough to round to zero. Below a tenth of a second the
        # honest statement is that the sample is too fast to derive a ratio
        # from.
        if worst >= 0.1:
            print(f"    Observed latency is "
                  f"{config.MATCHING_REQUEST_TIMEOUT_SECONDS / worst:.0f}x "
                  f"under that budget, so it does not bind in practice at "
                  f"this prefix size; '1h' is the one-edit remedy if it ever "
                  f"does.")
        else:
            print(f"    (the slowest observed call was {worst:.3f}s -- too "
                  f"fast to derive a headroom ratio from; re-read against a "
                  f"real run before concluding the budget does not bind)")
    return {"cached_usd_3calls": cached_cost, "per_patient_usd": real,
            "per_patient_uncached_usd": flat, "rates_inferred": inferred}


def _summary():
    print()
    print("=" * 74)
    print("SUMMARY")
    print("=" * 74)
    print(f"Passed: {_RESULTS['passed']}")
    print(f"Failed: {_RESULTS['failed']}")
    if _FAILURES:
        print("\nFailures:")
        for f in _FAILURES:
            print(f"  - {f}")
        print("\nEach failure maps to a numbered VERIFY-AT-GO-LIVE item in "
              "oncotriage/agent/bedrock_adapter.py, which names the edit.")
    return 1 if _RESULTS["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug 21 2026

@author: ramyalsaffar
"""
