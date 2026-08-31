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

    client = deps.get_bedrock_anthropic_client()
    schema = build_response_schema()

    raw_responses = []
    elapsed = []
    section(f"CALLS — {args.calls} identical, live and billed")
    for i in range(args.calls):
        print(f"\n  --- call {i + 1} of {args.calls} ---")
        _t0 = time.monotonic()
        try:
            raw = client.converse(**kwargs)
        except Exception as exc:                       # noqa: BLE001
            category = adapter.classify_error(exc)
            print(f"  RAISED  {type(exc).__name__} [{category}]")
            print(f"          {exc}")
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
            t_raw = client.converse(**truncated)
            print(f"  stopReason: {t_raw.get('stopReason')!r}")
            check("a deliberate truncation reports 'max_tokens', which is what "
                  "arms the split", t_raw.get("stopReason"), "max_tokens")
        except Exception as exc:                       # noqa: BLE001
            print(f"  RAISED ({type(exc).__name__}: {exc})")
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
        for label, kw in (("warmup", warm_kwargs),
                          ("trial 1", trial_kwargs),
                          ("trial 2", trial_kwargs)):
            print(f"\n  --- {label} ---")
            _t0 = time.monotonic()
            try:
                raw = client.converse(**kw)
            except Exception as exc:                   # noqa: BLE001
                category = adapter.classify_error(exc)
                print(f"  RAISED  {type(exc).__name__} [{category}]")
                print(f"          {exc}")
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
            print(f"  elapsed {time.monotonic() - _t0:.1f}s   "
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
