"""Supportive functions shared across the pipeline.

Moved out of ``02- Utility Functions.py`` by item 20c, which survives as a shim
re-exporting every name below. ``load_env_keys`` did NOT come here — it went to
``oncotriage.settings``, and that is the whole reason this module is allowed to
import ``oncotriage.config`` at all. See the package docstring.

WHY THREE FUNCTIONS TAKE AN OVERRIDE ARGUMENT THEY DID NOT USED TO HAVE
-----------------------------------------------------------------------
``get_model_cost``, ``resolve_qdrant_collection`` and ``get_age_reference_date``
read ``PRICING_CONFIG`` / ``qdrant_client`` + ``COLLECTION_NAME`` /
``DATA_SNAPSHOT_DATE`` out of the shared exec namespace at CALL time. That is
not an accident of the exec chain — it is a seam four files depend on:

  * ``38- Birth Date and Demographics Parser Test.py`` rebinds
    ``DATA_SNAPSHOT_DATE`` to "", "2026", "2026-03" and "not a date" and
    requires ``get_age_reference_date()`` to raise at each;
  * ``36- Logging Contract Test.py`` swaps ``qdrant_client`` for a stub;
  * ``37- Retrieval Observability Test.py`` swaps it via ``swap_globals``;
  * ``45- Fixture Capture.py`` / ``46- Fixture Replay.py`` rebind it to
    recording and replaying proxies.

A module function cannot see a caller's globals, so each of the three now takes
the value as an optional argument. ``02- Utility Functions.py``'s shim passes
``globals().get(...)`` — the shim's functions are defined inside the exec'd
text, so their ``__globals__`` IS the shared namespace and the lookup stays
dynamic. Callers inside the package pass nothing and get the config value.

``None`` means "not supplied" for the first two, because neither
``PRICING_CONFIG`` nor a client is ever legitimately ``None``.
``get_age_reference_date`` uses a private sentinel instead, because ``""`` is
one of the values File 38 requires to raise.
"""

import logging
import os
import re
import time
from collections import Counter
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import caffeine as _caffeine_mod
import httpx
from qdrant_client.http.exceptions import UnexpectedResponse
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from oncotriage import config


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


def get_model_cost(model_name: str, input_tokens: int, output_tokens: int,
                   pricing_config: Optional[dict] = None) -> float:
    """
    Calculate USD cost from token counts using current pricing.

    Args:
        model_name: Model identifier (e.g., 'gpt-4o-2024-08-06')
        input_tokens: Input token count from response.usage
        output_tokens: Output token count from response.usage
        pricing_config: Price table to use. None (the default) means
            ``oncotriage.config.PRICING_CONFIG``. The shim in
            '02- Utility Functions.py' passes the shared exec namespace's
            PRICING_CONFIG so that the exec chain keeps the late-binding it
            had; see this module's docstring.

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
    if pricing_config is None:
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


def exec_chain(files: List[str], caller_file: str, caller_globals: dict, chain_label: str = "") -> None:
    """Load and exec a list of project scripts into the caller's global scope.

    Args:
        files:          Ordered list of script names, e.g. ["01- Imports.py", "03- Config.py"].
        caller_file:    Pass __file__ — resolves the directory to search in.
        caller_globals: Pass globals() — scripts are exec'd into this namespace.
        chain_label:    Label for the completion message, e.g. "01 → 02 → 03".

    Raises:
        FileNotFoundError: If a script can't be found under its spaced or underscore variant.
    """
    base_dir = os.path.dirname(os.path.abspath(caller_file)) + os.sep
    saved_name = caller_globals.get("__name__")

    for name in files:
        for variant in (name, name.replace(" ", "_")):
            try:
                with open(base_dir + variant) as fh:
                    print(f"[Init] Loading {name}...")
                    caller_globals["__name__"] = "_exec_chain_"
                    exec(fh.read(), caller_globals)  # noqa: S102
                    break
            except FileNotFoundError:
                continue
        else:
            caller_globals["__name__"] = saved_name
            raise FileNotFoundError(f"Required script not found: '{name}' (searched in: {base_dir})")

    caller_globals["__name__"] = saved_name
    print(f"[Init] Chain complete ({chain_label}).\n")


#------------------------------------------------------------------------------


# Tenacity retry decorator for Qdrant operations (network hiccups, timeouts)
qdrant_retry = retry(
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException, UnexpectedResponse)),
)


#------------------------------------------------------------------------------


# Collecting the clinical trial batch name from the Qdrant
#---------------------------------------------------------
def resolve_qdrant_collection(client=None, collection_name: Optional[str] = None) -> str:
    """Resolve the COLLECTION_NAME alias to the actual backing collection.

    Qdrant aliases allow COLLECTION_NAME to remain constant ('trial_criteria')
    while the actual collection rotates weekly ('trial_criteria_20260226_140159').
    This function resolves the alias to the real collection name for logging.

    Retries up to 3 times with 1s delay if resolution fails or alias not found.

    Args:
        client: Qdrant client to ask. None means ``config.get_qdrant_client()``.
            The shim in '02- Utility Functions.py' passes the shared exec
            namespace's ``qdrant_client``, so a recording proxy installed by
            File 45 / 46 or a stub installed by File 36 is still the thing this
            function talks to. Building the real client here instead would have
            been a silent second connection.
        collection_name: Alias to resolve. None means ``config.COLLECTION_NAME``.
    """

    if client is None:
        client = config.get_qdrant_client()
    if collection_name is None:
        collection_name = config.COLLECTION_NAME

    MAX_RETRIES = 3

    for attempt in range(1, MAX_RETRIES + 1):

        try:
            all_aliases = client.get_aliases().aliases
            for a in all_aliases:
                if a.alias_name == collection_name:
                    return a.collection_name
            print(f"⚠ Alias '{collection_name}' not found in Qdrant (attempt {attempt}/{MAX_RETRIES})")
        except Exception as e:
            print(f"⚠ Qdrant alias resolution error (attempt {attempt}/{MAX_RETRIES}): {e}")

        if attempt < MAX_RETRIES:
            time.sleep(1)

    # Final fallback: check if collection_name is itself a real collection (no alias)
    try:
        client.get_collection(collection_name)
        print(f"⚠ '{collection_name}' is a real collection, not an alias. Using as-is.")
        return collection_name
    except Exception:
        pass

    print(f"⚠ FAILED to resolve collection after {MAX_RETRIES} attempts. Using '{collection_name}' as fallback.")
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


# Distinct from None and from "": File 38 requires get_age_reference_date() to
# raise on a snapshot date of "", so "" cannot double as "nothing was passed".
_SNAPSHOT_NOT_SUPPLIED = object()


def get_age_reference_date(snapshot_date=_SNAPSHOT_NOT_SUPPLIED) -> date:
    """The fixed date this run computes patient ages against.

    Resolves DATA_SNAPSHOT_DATE from oncotriage.config -- see the comment there
    for why the current clock cannot be used. Before item 20c this read
    ``globals().get("DATA_SNAPSHOT_DATE", "")``, which only worked because
    every project file shared one exec namespace.

    Args:
        snapshot_date: Override. Omitted means read ``config.DATA_SNAPSHOT_DATE``
            at call time (not bound at import, so patching the module attribute
            takes effect). The shim in '02- Utility Functions.py' passes the
            shared exec namespace's value so File 38's negative cases still
            reach this function.

    Raises ValueError when the constant is missing or is not a full date.
    Falling back to today() here would restore the exact defect the constant
    exists to remove, and would do it silently; an unset snapshot date is a
    configuration error, not a runtime condition to recover from.
    """

    if snapshot_date is _SNAPSHOT_NOT_SUPPLIED:
        raw = getattr(config, "DATA_SNAPSHOT_DATE", "")
    else:
        raw = snapshot_date

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
        try:
            _caffeine_mod.on(display=False)
            print(f"Caffeine ON (preventing sleep: {self.label})")
        except Exception:
            print(f"Caffeine unavailable (non-macOS?) (continuing: {self.label})")
        return self

    def __exit__(self, *args):
        try:
            _caffeine_mod.off()
            print(f"Caffeine OFF ({self.label})")
        except Exception:
            pass


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb  9 21:43:44 2026

@author: ramyalsaffar
"""
