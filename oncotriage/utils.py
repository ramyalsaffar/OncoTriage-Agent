"""Supportive functions shared across the pipeline.

Moved out of ``02- Utility Functions.py`` by item 20c. That file survived as a
shim re-exporting every name below and IS DELETED AS OF PASS 20e — nothing in
the repository raw-exec'd it any more once Files 05, 09, 13, 18 and 19 stopped.
``load_env_keys`` did NOT come here — it went to ``oncotriage.paths``, and that
is the whole reason this module is allowed to import ``oncotriage.config`` at
all. See the package docstring.

``exec_chain`` WENT WITH IT (pass 20e), and that is the point of the pass rather
than a side effect. It loaded a list of numbered scripts into a caller's
``globals()`` with ``__name__`` temporarily set to ``"_exec_chain_"``, which is
the mechanism that let a numbered file be both a script and a library. There is
no caller: the numbered files are thin entry points that import the package.
Shipping the function that rebuilds the arrangement this pass removed is how the
arrangement comes back, and this project treats an unreachable definition as a
defect everywhere else. ``tests/test_package_invariants.py`` section 1c now
scans the WHOLE repository for a call to it, or for a raw ``exec()`` of a
numbered file, and carries a planted control.

THE THREE EXEC-CHAIN OVERRIDE ARGUMENTS ARE DELETED (pass 20f-3)
-----------------------------------------------------------------
``get_model_cost``, ``resolve_qdrant_collection`` and ``get_age_reference_date``
used to read ``PRICING_CONFIG`` / ``qdrant_client`` + ``COLLECTION_NAME`` /
``DATA_SNAPSHOT_DATE`` out of the shared exec namespace at CALL time. A module
function cannot see a caller's globals, so item 20c gave each of them the value
as an OPTIONAL ARGUMENT and ``02- Utility Functions.py``'s shim passed
``globals().get(...)`` through it. Four parameters across three functions:
``pricing_config``, ``client``, ``collection_name``, ``snapshot_date``.

THEY WERE A BRIDGE TO A MECHANISM THAT NO LONGER EXISTS. ``36-`` and ``37-``
stopped rebinding ``qdrant_client`` at pass 20c-2c and install
``deps.set_override(deps.QDRANT_CLIENT, ...)``; ``38-`` stopped rebinding
``DATA_SNAPSHOT_DATE`` at pass 20d-1 and sets ``config.DATA_SNAPSHOT_DATE``, the
attribute ``get_age_reference_date()`` reads at call time; ``45-`` and ``46-``
became ``oncotriage/fixtures/{capture,replay}.py`` at pass 20c-3d and go through
``deps`` too; and pass 20e deleted the shim that passed the values, along with
``exec_chain`` itself.

MEASURED BEFORE REMOVAL, BY AST RATHER THAN BY GREP: 29 call sites across the
whole repository -- package, entry points and tests -- and not one of them
passes any of the four, positionally or by keyword. 4 to ``get_model_cost``
(all with exactly 3 positional arguments), 6 to ``resolve_qdrant_collection``
(all with none), 19 to ``get_age_reference_date`` (all with none).

THIS IS A BEHAVIOUR CHANGE AND IT IS STATED AS ONE: three public signatures
narrowed, so a caller outside this repository passing any of the four now gets a
``TypeError`` where it used to get an override. Pass 20e recorded the removal as
a follow-up and named the one thing that had to be settled first --
``get_age_reference_date``'s docstring called ``snapshot_date`` "the supported
patch point". IT IS NOT, AND HAD NOT BEEN SINCE PASS 20d-1: the file that
patches it, ``tests/test_fhir_birth_date_and_demographics.py`` section 3, sets
``config.DATA_SNAPSHOT_DATE`` and always did after the move, because the shim
that carried the argument was gone. The docstring named a seam its own test had
stopped using. The supported patch point is the config attribute, the function
reads it at CALL time so patching takes effect, and the docstring says that now.

The private "not supplied" sentinel went with ``snapshot_date``; it is named and
argued in the COMMENT that stands where it was defined, and DELIBERATELY NOT
HERE. ``tests/test_package_invariants.py`` check 2h counts a name inside any
string literal as a read, and this docstring is a string literal -- so naming the
deleted constant in it would mean that reinstating the constant, unread, is
NOT REPORTED. That is not hypothetical: it is what pass 20f-2 shipped and caught
only through a revert control, and it is what the revert control for THIS pass
caught here. A `#` comment is invisible to an AST walk, which is exactly why the
argument belongs in one.
"""

import logging
import os
import re
import shutil
import time
# `os` WAS IMPORTED HERE AND IS NOT ANY MORE (pass 20e). Its only reader was
# exec_chain(), which resolved the caller's directory with os.path -- so
# deleting the function left the import behind, and check 2h(i) of
# tests/test_package_invariants.py reported it on the first run after the
# deletion. Recorded rather than silently tidied, because it is the smallest
# possible instance of the thing that pass being about: remove a consumer and
# what it consumed becomes dead without anything failing.
from collections import Counter
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import httpx
from qdrant_client.http.exceptions import UnexpectedResponse
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from oncotriage import config
from oncotriage.observability import console, get_logger


log = get_logger(__name__)


# ---------------------------------------------------------------------------
# `caffeine`, which RUNS A macOS BINARY WHEN YOU IMPORT IT (item 21)
# ---------------------------------------------------------------------------
#
# THIS IMPORT WAS UNGUARDED AND IT MADE THE WHOLE PACKAGE UNIMPORTABLE ON LINUX.
# Measured inside the container, not reasoned about: the last two statements of
# the installed `caffeine.py` are
#
#     on()
#     atexit.register(off)
#
# and `on()` is `subprocess.Popen(['caffeinate', '-is', '-w', str(_pid)])`.
# `caffeinate` is a macOS binary. So on Linux `import caffeine` does not fail
# with an ImportError — it gets all the way through the module body and dies
# with
#
#     FileNotFoundError: [Errno 2] No such file or directory: 'caffeinate'
#
# Every module in this package reaches `oncotriage.utils` sooner or later, so
# that single line meant `import oncotriage.api.server` — and therefore every
# container service — could never start. The API container crash-looped on it.
#
# TWO THINGS ABOUT THE except CLAUSE ARE DELIBERATE:
#
#   * it catches `Exception`, NOT `ImportError`. The failure is an OS error
#     raised from a subprocess spawned during module execution. An
#     `except ImportError` — the obvious guard, and the one a reader will be
#     tempted to tighten this to — would not catch it, and the package would go
#     back to being unimportable on Linux.
#   * the reason is RECORDED, not swallowed. `CAFFEINE_IMPORT_ERROR` carries the
#     exception type and message and `CaffeinateSession` prints it, so a run
#     that lost sleep-prevention says why rather than being silently different
#     from a run that kept it.
#
# This changes nothing on macOS: the import succeeds and `_caffeine_mod` is the
# module, exactly as before. `CaffeinateSession` already wrapped its `.on()` and
# `.off()` calls in try/except and already documented itself as degrading on
# non-macOS platforms — the intent was always that this is optional. Only the
# import was not optional, which is why the degradation could never be reached.
try:
    import caffeine as _caffeine_mod
except Exception as _caffeine_exc:      # noqa: BLE001 - see above, not ImportError
    _caffeine_mod = None
    CAFFEINE_IMPORT_ERROR = f"{type(_caffeine_exc).__name__}: {_caffeine_exc}"
    del _caffeine_exc
else:
    CAFFEINE_IMPORT_ERROR = None


#------------------------------------------------------------------------------


# Preserving a state file that could not be read
#----------------------------------------------
# THE PATTERN IS oncotriage/batch/runner.py's, LIFTED TO ONE SITE BECAUSE THREE
# CALLERS NOW NEED IT. That module established it for the per-patient results
# file: an unreadable state file is renamed out of the way BEFORE anything can
# replace it, because every writer in this project does write-temp-then-replace
# and the first write of the next run therefore destroys whatever it could not
# parse -- irrecoverably, silently, and while the run reports success.
#
# The batch and ablation CHECKPOINTS need exactly the same treatment (a
# checkpoint that cannot be read is a checkpoint about to be overwritten by
# save_checkpoint's os.replace), and a second and third copy of a
# find-a-free-suffix-then-rename loop is two more chances for one of them to
# stop preserving. The suffix is the CALLER's argument rather than a constant
# here, so "the results file's sidecar" and "the checkpoint's sidecar" stay
# separable names at the call sites that mean them.


def preserve_corrupt_file(path, suffix: str, limit: int = 1000,
                          keep_original: bool = False) -> Tuple:
    """Put an unreadable state file aside. ``(preserved_path, error, key)``.

    Exactly one of the first two members is None. The third is a COUNTER KEY
    for the failure, decided here rather than sliced out of the message at the
    call site -- the batch runner's first version keyed on
    ``error.split(':')[0]``, which for the exhausted branch is an
    eighty-character sentence, and a counter key that is a sentence is a
    counter nobody can aggregate.

    THE SUFFIX IS NUMBERED WHEN IT COLLIDES. A fixed ``.corrupt`` would let the
    second corruption destroy the copy taken at the first, which is the same
    data loss one step removed. ``os.replace`` is deliberately NOT used to pick
    the name -- it overwrites -- so the first free suffix is searched for and
    ``os.rename`` onto it is guarded by that search.

    A RENAME FAILURE IS RETURNED, NEVER RAISED. The caller decides what losing
    the file costs; this function's job is to try and to say what happened.
    The search is bounded so a directory somebody has filled with sidecars
    produces a named refusal instead of an unbounded loop.

    MOVE OR COPY IS THE CALLER'S DECISION AND IT IS NOT COSMETIC. A moved file
    is GONE from its own path, so the next run finds nothing there. For the
    per-patient RESULTS file that is right: it is a report, the checkpoint is
    untouched, and the next run rebuilds the report.

    For a CHECKPOINT it would be a disaster wearing the costume of a fix. A
    checkpoint that is renamed aside and then refused makes the FIRST
    invocation loud and the SECOND one silent: there is no checkpoint any more,
    so the run starts fresh and re-bills the whole cohort with nothing to say
    it did. ``keep_original=True`` copies instead, which leaves the refusal
    STICKY -- every invocation refuses until an operator clears the checkpoint
    deliberately -- while still putting the evidence somewhere that operator's
    fix cannot destroy.

    Args:
        path:   the unreadable file, as anything ``os.rename`` accepts. Used as
                a string, so a ``str`` and a ``pathlib.Path`` behave alike.
        suffix: what to append before the numeric disambiguator.
        limit:  how many suffixes to try before giving up.
        keep_original: copy rather than move, leaving ``path`` where it is.
    """
    base = str(path)
    for index in range(0, limit):
        candidate = base + suffix + (f".{index}" if index else "")
        if os.path.exists(candidate):
            continue
        try:
            if keep_original:
                shutil.copy2(base, candidate)
            else:
                os.rename(base, candidate)
            return candidate, None, None
        except OSError as exc:
            return None, f"{type(exc).__name__}: {exc}", type(exc).__name__
    return (None,
            f"{limit} {suffix} sidecars already exist beside {base}; "
            f"refusing to guess a name",
            PRESERVE_EXHAUSTED)


PRESERVE_EXHAUSTED = "SidecarNamesExhausted"
"""Counter key for "the sidecar name search ran out of names".

A NAMED CONSTANT rather than a slice of the message, for the reason
``preserve_corrupt_file`` gives above.
"""


#------------------------------------------------------------------------------


def deduplicate_by_display(items: List[Dict], key: str = 'display') -> List[Dict]:
    """
    Deduplicate list of dicts by case-insensitive display field.

    Args:
        items: List of dicts (medications, conditions, etc.)
        key: Dict key to use for deduplication (default: 'display')

    Returns:
        List of dicts with duplicates removed (first occurrence kept)

    Example:
        medications = [
            {'display': 'Aspirin', 'code': '1234'},
            {'display': 'aspirin', 'code': '5678'},  # duplicate
            {'display': 'Ibuprofen', 'code': '9999'}
        ]
        unique = deduplicate_by_display(medications)
        # Returns [{'display': 'Aspirin', 'code': '1234'}, {'display': 'Ibuprofen', 'code': '9999'}]
    """
    seen = set()
    unique = []

    for item in items:

        display = item.get(key)

        # Preserve items with no display key, do not discard
        if display is None:
            unique.append(item)
            continue

        display_lower = display.lower()
        if display_lower not in seen:
            seen.add(display_lower)
            unique.append(item)

    return unique


#------------------------------------------------------------------------------


class UnknownModelPricingError(RuntimeError):
    """Raised when get_model_cost() is handed a model absent from PRICING_CONFIG.

    Deliberately not a KeyError: a stray `except KeyError` around a dict lookup
    would swallow it and put the pipeline back where it started. Callers that
    recover from their own failures must let this one through — a missing price
    is a configuration defect, not a runtime hiccup.
    """


def get_model_cost(model_name: str, input_tokens: int,
                   output_tokens: int) -> float:
    """
    Calculate USD cost from token counts using current pricing.

    Args:
        model_name: Model identifier (e.g., 'gpt-4o-2024-08-06')
        input_tokens: Input token count from response.usage
        output_tokens: Output token count from response.usage

        The price table is ``oncotriage.config.PRICING_CONFIG``, read HERE at
        call time rather than bound at import, so setting the module attribute
        takes effect. A ``pricing_config`` parameter stood here until pass
        20f-3; see this module's docstring for why it went.

    Returns:
        Total cost in USD

    Raises:
        UnknownModelPricingError: model_name is not in PRICING_CONFIG["models"].
            This used to warn and return 0.0, which put a row in the database
            claiming the run was free. A zero there is indistinguishable from a
            genuinely free run, and every aggregate built on the column — the
            dashboard's cost panel, the ablation study's cost-per-config, any
            projection to 1000 patients — silently understates by however much
            of the corpus ran on the unpriced model. Refusing to produce a
            number is the only honest answer: the caller must add the model to
            PRICING_CONFIG (oncotriage/config.py) or stop billing against it.

    Example:
        cost = get_model_cost('gpt-4o-2024-08-06', 1000, 500)
        # Returns: 0.0025 + 0.0050 = 0.0075 USD
    """
    pricing_config = config.PRICING_CONFIG

    pricing = pricing_config["models"].get(model_name)
    if not pricing:
        known = ", ".join(sorted(pricing_config["models"])) or "(none)"
        logging.error(
            f"get_model_cost: unknown model '{model_name}'; "
            f"priced models are: {known}"
        )
        raise UnknownModelPricingError(
            f"No pricing for model '{model_name}' in PRICING_CONFIG "
            f"(last_updated {pricing_config['last_updated']}). "
            f"Priced models: {known}. Add it to PRICING_CONFIG in "
            f"'oncotriage/config.py' — cost cannot be reported as 0.0 for a run "
            f"that consumed {input_tokens} input / {output_tokens} output tokens."
        )

    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]

    return input_cost + output_cost


#------------------------------------------------------------------------------


class UnknownCachePricingError(RuntimeError):
    """A model reported cached tokens and PRICING_CONFIG has no rate for them.

    A SECOND ERROR CLASS RATHER THAN A REUSE OF ``UnknownModelPricingError``,
    and the difference is what a reader does about it. That one means "this
    model is not priced at all" and stops a row being written; this one means
    "this model is priced, the flat figure is sound, and the SECOND figure
    beside it cannot be computed" -- so its caller records NULL in one column
    and carries on. Collapsing the two would make a missing cache rate abort a
    write that has every reason to succeed.

    Deliberately a ``RuntimeError`` and not a ``ValueError``, on
    ``UnknownModelPricingError``'s own footing: a stray ``except ValueError``
    around an arithmetic block must not swallow it.
    """


def get_model_cost_cached(model_name: str, input_tokens: int,
                          output_tokens: int, cached_read_tokens: int = 0,
                          cached_write_tokens: int = 0,
                          cache_write_ttl=None) -> float:
    """USD cost with the cache tiers priced separately. ``get_model_cost``'s twin.

    ADDITIVE, AND ``get_model_cost()`` IS UNTOUCHED. That function is read by 29
    call sites and by ``inferences.estimated_cost_usd``, whose whole value is
    that it means the same thing in every row ever written; introducing a cached
    term there would re-base the entire series. This is a SECOND figure, stored
    beside the first, so the A13 gap the Converse adapter records is a number a
    query can subtract rather than a caveat a reader has to remember.

    THE TWO CACHED COUNTS ARE SUBSETS OF ``input_tokens``, NEVER ADDITIONS TO
    IT. That is the provider's own arithmetic on both arms this function serves:
    Converse's usage documents ``total input = inputTokens + cacheRead +
    cacheWrite`` and ``oncotriage/agent/bedrock_anthropic_adapter.py`` sums the
    three back into ``prompt_tokens`` at the boundary, and OpenAI's
    ``prompt_tokens`` already includes its ``cached_tokens``. So the
    full-rate share is ``input_tokens - cached_read - cached_write`` and adding
    the tiers on top would bill the cached portion twice.

    THE RESULT IS NOT ALWAYS BELOW ``get_model_cost()``'s, and that is worth
    knowing before using this as a "discount". A cache READ bills at a tenth of
    input and a cache WRITE at 1.25x (5m) or 2.00x (1h), so a run whose writes
    are not repaid by later reads is priced ABOVE the flat figure. On a healthy
    per-trial patient the reads outnumber the write by roughly
    MAX_TRIALS_FOR_EVALUATION to one and the net is a large saving; on a warmup
    followed by an empty wave it is a small loss.

    Args:
        model_name: the wire model id, the same key ``get_model_cost`` takes.
        input_tokens: the TOTAL input tokens the provider reported.
        output_tokens: completion tokens.
        cached_read_tokens: the share of ``input_tokens`` served from a warm
            prefix. 0 means "measured none"; there is no "unknown" value here,
            because a caller that does not know must not call this at all.
        cached_write_tokens: the share of ``input_tokens`` written INTO the
            cache and billed at the write premium.
        cache_write_ttl: which write rate applies -- the value of
            ``config.BEDROCK_ANTHROPIC_CACHE_TTL`` for the run being priced.
            Required only when ``cached_write_tokens`` is non-zero, because a
            TTL is a fact about a charge that was made and there is nothing to
            select a rate for when nothing was written.

    Returns:
        Total cost in USD.

    Raises:
        UnknownModelPricingError: ``model_name`` is absent from PRICING_CONFIG.
            Identical to ``get_model_cost``'s, and raised by the same lookup.
        UnknownCachePricingError: cached tokens were reported and the row
            carries no rate for that tier, or ``cache_write_ttl`` names a TTL
            the row does not price. NEVER a silent fall back to the input rate:
            that is what the flat figure already is, and a second column that
            silently equals the first is worse than a NULL, which at least says
            the answer is not known.
        ValueError: a negative count, or a cached share larger than the input
            total it is supposed to be a subset of. Both are contract
            violations by the caller rather than missing configuration, so they
            are the builtin rather than one of the two above.
    """
    for _name, _value in (("input_tokens", input_tokens),
                          ("output_tokens", output_tokens),
                          ("cached_read_tokens", cached_read_tokens),
                          ("cached_write_tokens", cached_write_tokens)):
        # bool is excluded on this project's standing footing: isinstance(True,
        # int) is True, and a token count of 1 that was really a flag is a
        # number nobody measured.
        if not isinstance(_value, int) or isinstance(_value, bool):
            raise ValueError(
                f"get_model_cost_cached: {_name} must be an int, "
                f"got {_value!r} ({type(_value).__name__})")
        if _value < 0:
            raise ValueError(
                f"get_model_cost_cached: {_name} is negative ({_value})")

    cached_total = cached_read_tokens + cached_write_tokens
    if cached_total > input_tokens:
        # NOT CLAMPED. A cached share larger than the total it is drawn from
        # means the two numbers came from different populations -- the exact
        # asymmetry oncotriage/agent/evaluation.py's warmup fold creates and
        # argues about -- and silently clamping would price a run against a
        # non-cached share of zero while reporting a figure that looks whole.
        raise ValueError(
            f"get_model_cost_cached: cached tokens "
            f"({cached_read_tokens} read + {cached_write_tokens} write = "
            f"{cached_total}) exceed the {input_tokens} input tokens they are "
            f"a subset of. One of the two was measured over a different set of "
            f"requests.")

    pricing_config = config.PRICING_CONFIG
    pricing = pricing_config["models"].get(model_name)
    if not pricing:
        known = ", ".join(sorted(pricing_config["models"])) or "(none)"
        raise UnknownModelPricingError(
            f"No pricing for model '{model_name}' in PRICING_CONFIG "
            f"(last_updated {pricing_config['last_updated']}). "
            f"Priced models: {known}. Add it to PRICING_CONFIG in "
            f"'oncotriage/config.py'.")

    uncached = input_tokens - cached_total
    total = ((uncached / 1_000_000) * pricing["input"]
             + (output_tokens / 1_000_000) * pricing["output"])

    if cached_read_tokens:
        rate = pricing.get("cache_read")
        if rate is None:
            raise UnknownCachePricingError(
                f"'{model_name}' reported {cached_read_tokens} cached-read "
                f"tokens and its PRICING_CONFIG row carries no 'cache_read' "
                f"rate. Add one, or stop reading cached input on this model.")
        total += (cached_read_tokens / 1_000_000) * rate

    if cached_write_tokens:
        rates = pricing.get("cache_write")
        if not rates:
            raise UnknownCachePricingError(
                f"'{model_name}' reported {cached_write_tokens} cache-write "
                f"tokens and its PRICING_CONFIG row carries no 'cache_write' "
                f"rates. Add them, or stop writing the cache on this model.")
        if cache_write_ttl not in rates:
            raise UnknownCachePricingError(
                f"'{model_name}' reported {cached_write_tokens} cache-write "
                f"tokens at TTL {cache_write_ttl!r}, which its PRICING_CONFIG "
                f"row does not price. Priced TTLs: {sorted(rates)}. A write "
                f"rate is NOT interchangeable between TTLs -- they differ by "
                f"60% on the measured row -- so no fallback is taken.")
        total += (cached_write_tokens / 1_000_000) * rates[cache_write_ttl]

    return total


#------------------------------------------------------------------------------


# exec_chain() STOOD HERE AND IS DELETED (pass 20e)
#--------------------------------------------------
# It took a list of numbered script names, opened each one relative to the
# caller's __file__, set caller_globals["__name__"] to "_exec_chain_" so the
# file's `if __name__ == "__main__":` block would not fire, and exec'd it into
# that namespace. Falling back to the underscored filename variant, printing
# "[Init] Loading ..." per file and "[Init] Chain complete (label)." at the end.
#
# THAT WAS THE MECHANISM THE WHOLE PROJECT WAS BUILT ON, and pass 20e is where
# the last caller stopped. Files 05, 09 and 13 were the last three, and each of
# them was chaining for consumers that had themselves been converted one or two
# passes earlier -- File 05 for File 34 (converted in 20c-3d), File 13 for
# twelve files all converted by 20d-1, File 09 for five.
#
# NOT KEPT "in case", and the reason is specific rather than tidiness: the
# function's contract is "make a numbered file's names appear in your globals",
# which is exactly the arrangement that made oncotriage/agent/deps.py necessary,
# made File 14's log_inference wrapper necessary, and would have sent twelve
# fixture replays to the real OpenAI endpoint. A mechanism that is kept is a
# mechanism that gets used. tests/test_package_invariants.py section 1c scans
# the whole repository for a call to it or a raw exec() of a numbered file, and
# carries a planted control so the scan is shown to be able to fail.
#
# Its docstring's stated failure mode is preserved for the record: it raised
# FileNotFoundError naming the script and the directory it searched.


#------------------------------------------------------------------------------


QDRANT_RETRIES = Counter()
"""Qdrant calls that ``qdrant_retry`` slept and retried, keyed by function name.

Module-level, following ``INFERENCE_WRITE_RETRIES`` in
``oncotriage/storage/database_logger.py`` rather than becoming a column: this is
a property of the RUN and of the network under it, not of any one patient. A
retry can happen inside ``build_bm25_index_from_qdrant`` before the first
patient exists and inside ``compute_collection_digest`` after the last one, so
there is no row it could belong to.

WHAT IT REPLACES: nothing, which was the defect. ``qdrant_retry`` decorates
six call sites across the agent, the indexer, the trial lookup and the fixture
harness, and until this counter existed a run in which every Qdrant call
succeeded and a run in which a third of them succeeded only on the second
attempt were the same run in every record the project kept. Retrying IS the
right recovery; not saying it happened is not.

KEYED BY THE DECORATED FUNCTION'S NAME because that is what makes it
actionable: `_scroll_page` retrying says the collection scan is struggling,
`_search` retrying says query time is. `retry_state.fn` is None on tenacity's
statistics-only paths, so the key falls back to a named sentinel rather than
letting the hook raise -- a counter that can take down the call it is counting
is worse than no counter.

ATTEMPTS, NOT CALLS, and the distinction is `INFERENCE_WRITE_RETRIES`'s: three
retries of one call and one retry of three calls are the same total and
different findings, and the total is what a run-end summary can act on.
"""

_QDRANT_RETRY_UNNAMED = "<unnamed>"
"""Key used when tenacity's retry state carries no function to name.

A documented constant rather than a bare literal, because a run reporting
retries under this key means the hook fired somewhere the name was not
recoverable -- which is a finding about tenacity's call path, not about Qdrant.
"""


def _count_qdrant_retry(retry_state) -> None:
    """``before_sleep`` hook: record that a Qdrant call is about to be retried.

    Fires once per SLEEP, so a call that succeeds first time records nothing and
    a call that exhausts the three attempts records two. It does not decide
    anything -- tenacity's stop, wait and exception predicates are untouched by
    this pass -- and it must not raise, because it runs inside the retry
    machinery of a call that has already failed once.
    """
    fn = getattr(retry_state, "fn", None)
    name = getattr(fn, "__qualname__", None) or _QDRANT_RETRY_UNNAMED
    QDRANT_RETRIES[name] += 1

    outcome = getattr(retry_state, "outcome", None)
    exc = outcome.exception() if outcome is not None and outcome.failed else None
    # `attempts`, `delay_s`, `error_type` and `event` are already on
    # LOGGABLE_FIELDS. The function NAME is deliberately not a field: adding one
    # would widen the allowlist for a value the counter already carries and the
    # run-end summary already prints. It is a code identifier, so the reason is
    # surface area rather than confidentiality.
    log.warning("Qdrant call failed; retrying", event="qdrant_retry",
                attempts=retry_state.attempt_number,
                delay_s=round(getattr(retry_state.next_action, "sleep", 0.0), 3),
                error_type=type(exc).__name__ if exc is not None else None)


# Tenacity retry decorator for Qdrant operations (network hiccups, timeouts)
#
# THE ATTEMPTS, WAITS AND EXCEPTION CLASSES ARE UNCHANGED. The only addition is
# before_sleep, which counts and logs; tenacity calls it between the failure and
# the sleep, so it cannot alter what is retried or for how long.
qdrant_retry = retry(
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException, UnexpectedResponse)),
    before_sleep=_count_qdrant_retry,
)


#------------------------------------------------------------------------------


# Collecting the clinical trial batch name from the Qdrant
#---------------------------------------------------------
def resolve_qdrant_collection() -> str:
    """Resolve the COLLECTION_NAME alias to the actual backing collection.

    Qdrant aliases allow COLLECTION_NAME to remain constant ('trial_criteria')
    while the actual collection rotates weekly ('trial_criteria_20260226_140159').
    This function resolves the alias to the real collection name for logging.

    Retries up to 3 times with 1s delay if resolution fails or alias not found.

    Takes no arguments. It asks ``config.get_qdrant_client()`` for the client
    and resolves ``config.COLLECTION_NAME``, both read HERE rather than bound at
    import. ``client`` and ``collection_name`` parameters stood here until pass
    20f-3, for the exec chain's benefit; a fixture harness redirects this
    function's client the same way it redirects the agent's, by installing
    ``deps.set_override(deps.QDRANT_CLIENT, ...)`` -- except that this one goes
    through ``oncotriage.config``, which is deliberate and unchanged: a
    logging helper must not open a second connection, and it must not be
    silently pointed elsewhere by an agent test either.
    """

    client = config.get_qdrant_client()
    collection_name = config.COLLECTION_NAME

    MAX_RETRIES = 3

    for attempt in range(1, MAX_RETRIES + 1):

        try:
            all_aliases = client.get_aliases().aliases
            for a in all_aliases:
                if a.alias_name == collection_name:
                    return a.collection_name
            console.out(f"⚠ Alias '{collection_name}' not found in Qdrant (attempt {attempt}/{MAX_RETRIES})")
        except Exception as e:
            console.out(f"⚠ Qdrant alias resolution error (attempt {attempt}/{MAX_RETRIES}): {e}")

        if attempt < MAX_RETRIES:
            time.sleep(1)

    # Final fallback: check if collection_name is itself a real collection (no alias)
    try:
        client.get_collection(collection_name)
        console.out(f"⚠ '{collection_name}' is a real collection, not an alias. Using as-is.")
        return collection_name
    except Exception:
        pass

    console.out(f"⚠ FAILED to resolve collection after {MAX_RETRIES} attempts. Using '{collection_name}' as fallback.")
    return collection_name


#------------------------------------------------------------------------------


# Partial-date parsing and the run's age reference date
#------------------------------------------------------
# FHIR types Patient.birthDate as `date`, whose value is legally YYYY, YYYY-MM
# or YYYY-MM-DD, and real EHR exports also ship a full ISO dateTime in the
# field. HIPAA Safe Harbor de-identification produces the year-only form by
# design. A fixed datetime.strptime(value, '%Y-%m-%d') raises on three of those
# four shapes, and in this codebase that exception aborts the whole bundle.
#
# Missing components are filled with the midpoint of the range the record still
# allows, so the imputation error is centred instead of biased: an unknown
# month becomes July, an unknown day becomes the 15th. Worst case is ~6 months
# for a year-only date and ~15 days for a year-month date. The caller is told
# which shape it got (the returned precision) and is expected to record it --
# an imputed age must stay distinguishable from an exact one.
PARTIAL_DATE_ANCHOR_MONTH = 7    # mid-year,  used when the record has no month
PARTIAL_DATE_ANCHOR_DAY   = 15   # mid-month, used when the record has no day

# Out-of-range components ("1965-13-01", "1965-02-30") counted by the precision
# the parse was attempting when the component was rejected. A date that is
# well-formed but impossible is a data-quality signal in its own right, and the
# degradation that keeps the record usable must not be the only trace of it.
PARTIAL_DATE_DEGRADATIONS = Counter()

# Anchored at both ends. The day pattern also accepts the date portion of a
# full ISO datetime ("1965-04-12T00:00:00Z", "1965-04-12T00:00:00.000-07:00",
# "1965-04-12 00:00:00"), which is why its time part is an optional group.
_PARTIAL_DATE_PATTERNS = (
    ("day",   re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?$")),
    ("month", re.compile(r"^(\d{4})-(\d{2})$")),
    ("year",  re.compile(r"^(\d{4})$")),
)


def parse_partial_date(value) -> Tuple[Optional[date], str]:
    """Parse a FHIR partial date into a concrete date plus its precision.

    Args:
        value: Raw field value. A str in any of the shapes above; a date or
               datetime is passed through; anything else is unparseable.

    Returns:
        (date_or_None, precision) where precision is one of:
          "day"         -- full date, nothing imputed
          "month"       -- YYYY-MM, day imputed to PARTIAL_DATE_ANCHOR_DAY
          "year"        -- YYYY, month/day imputed to the anchors
          "missing"     -- empty / absent field
          "unparseable" -- present but not a date in any accepted shape

        Never raises. A returned date is always usable; a returned None always
        comes with a precision label saying why there is none, so no caller can
        mistake "no date" for "date at the epoch".
    """

    # datetime first: datetime is a subclass of date, so the order matters.
    if isinstance(value, datetime):
        return value.date(), "day"
    if isinstance(value, date):
        return value, "day"

    # An absent field is "missing"; a field carrying something that is not a
    # date string is "unparseable". Collapsing the two would report a corrupt
    # value as an empty one.
    if value is None:
        return None, "missing"
    if not isinstance(value, str):
        return None, "unparseable"

    raw = value.strip()
    if not raw:
        return None, "missing"

    for precision, pattern in _PARTIAL_DATE_PATTERNS:
        match = pattern.match(raw)
        if match is None:
            continue

        year  = int(match.group(1))
        month = int(match.group(2)) if precision in ("day", "month") else PARTIAL_DATE_ANCHOR_MONTH
        day   = int(match.group(3)) if precision == "day"             else PARTIAL_DATE_ANCHOR_DAY

        # Shape matched but a component may still be out of range ("1965-13-01",
        # "1965-02-30"). Degrade one step at a time rather than discarding the
        # record: the coarser components are still usable, and the precision
        # that comes back says exactly how much was kept.
        for fallback_precision, fallback_month, fallback_day in (
            (precision, month,                     day),
            ("month",   month,                     PARTIAL_DATE_ANCHOR_DAY),
            ("year",    PARTIAL_DATE_ANCHOR_MONTH, PARTIAL_DATE_ANCHOR_DAY),
        ):
            try:
                return date(year, fallback_month, fallback_day), fallback_precision
            except ValueError:
                PARTIAL_DATE_DEGRADATIONS[f"out_of_range:{fallback_precision}"] += 1
                continue

        return None, "unparseable"

    return None, "unparseable"


# _SNAPSHOT_NOT_SUPPLIED STOOD HERE AND IS DELETED (pass 20f-3). It was a
# sentinel distinct from None and from "", because File 38 requires
# get_age_reference_date() to raise on a snapshot date of "" and so "" could not
# double as "nothing was passed". With the `snapshot_date` parameter gone there
# is nothing left to be unsupplied.


def get_age_reference_date() -> date:
    """The fixed date this run computes patient ages against.

    Resolves DATA_SNAPSHOT_DATE from oncotriage.config -- see the comment there
    for why the current clock cannot be used. Before item 20c this read
    ``globals().get("DATA_SNAPSHOT_DATE", "")``, which only worked because
    every project file shared one exec namespace.

    Takes no arguments. THE SUPPORTED PATCH POINT IS
    ``oncotriage.config.DATA_SNAPSHOT_DATE``, which this function reads at CALL
    time rather than binding at import, so setting the module attribute takes
    effect -- that is what ``tests/test_fhir_birth_date_and_demographics.py``
    section 3 does to drive the four values below. A ``snapshot_date`` parameter
    stood here until pass 20f-3 and its docstring called ITSELF the supported
    patch point; no caller had passed it since pass 20d-1.

    Raises ValueError when the constant is missing or is not a full date.
    Falling back to today() here would restore the exact defect the constant
    exists to remove, and would do it silently; an unset snapshot date is a
    configuration error, not a runtime condition to recover from.
    """

    raw = getattr(config, "DATA_SNAPSHOT_DATE", "")

    reference, precision = parse_partial_date(raw)

    if reference is None or precision != "day":
        raise ValueError(
            f"DATA_SNAPSHOT_DATE must be a full YYYY-MM-DD date in "
            f"'oncotriage/config.py'; got {raw!r} (parsed precision: {precision}). "
            f"Patient ages are computed against it, so it cannot be defaulted "
            f"to the current date without reintroducing clock drift into the "
            f"Stage 5 prompt."
        )

    return reference


#------------------------------------------------------------------------------


class CaffeinateSession:
    """Context manager to prevent macOS sleep during long-running pipelines.

    Uses the 'caffeine' package (macOS only). Silently continues
    on non-macOS platforms or if the package is unavailable.

    Usage:
        with CaffeinateSession("Batch Runner"):
            # long-running work here
    """
    def __init__(self, label=""):
        self.label = label

    def __enter__(self):
        # The import itself may have failed — see the guarded import at the top
        # of this module. That is the ordinary case on Linux and in every
        # container. Reported by NAME rather than folded into the generic
        # "unavailable" message below, because the two are different facts: this
        # one means the package could not be loaded at all, and it carries the
        # reason.
        if _caffeine_mod is None:
            console.out(f"Caffeine unavailable ({CAFFEINE_IMPORT_ERROR}) "
                  f"(continuing: {self.label})")
            return self

        try:
            _caffeine_mod.on(display=False)
            console.out(f"Caffeine ON (preventing sleep: {self.label})")
        except Exception:
            console.out(f"Caffeine unavailable (non-macOS?) (continuing: {self.label})")
        return self

    def __exit__(self, *args):
        if _caffeine_mod is None:
            return
        try:
            _caffeine_mod.off()
            console.out(f"Caffeine OFF ({self.label})")
        except Exception:
            pass


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb  9 21:43:44 2026

@author: ramyalsaffar
"""
