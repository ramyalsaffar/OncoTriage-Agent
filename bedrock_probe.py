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
import json
import os
import sys


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
        "--probe-seed", action="store_true",
        help="Issue ONE EXTRA billed call with MATCHING_SEED smuggled through "
             "extra_body, to settle VERIFY-AT-GO-LIVE (4).")
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

    client = deps.get_bedrock_client()
    schema = build_response_schema()

    raw_responses = []
    section(f"CALLS — {args.calls} identical, live and billed")
    for i in range(args.calls):
        print(f"\n  --- call {i + 1} of {args.calls} ---")
        try:
            raw = client.responses.create(**kwargs)
        except Exception as exc:                       # noqa: BLE001
            category = bedrock_adapter.classify_error(exc)
            print(f"  RAISED  {type(exc).__name__} [{category}]")
            print(f"          {exc}")
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
