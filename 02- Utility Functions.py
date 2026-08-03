# Supportive functions
#---------------------


#------------------------------------------------------------------------------


# To create the .env file:
    ## create .txt file first, and clean it if it has any text due to fresh creation!
    ## add the text you needed!
    ## rename it to .env
    ## use a terminal with this (get to the targeted folder first):
    ## mv .env.txt .env
    ## to view the .env in Finder on Mac, hit: command + shift + .


def load_env_keys():
    """Load API keys from .env file"""
    env_path = keys_path + '.env'
    
    # Validate file exists
    if not os.path.exists(env_path):
        raise FileNotFoundError(f".env file not found at: {env_path}")
    
    # Clear previous env vars to avoid stale values
    for key in ['OPENAI_API_KEY', 'QDRANT_URL', 'QDRANT_API_KEY']:
        os.environ.pop(key, None)
    
    # Load from file
    load_dotenv(dotenv_path=env_path, override=True)
    
    # Validate all keys loaded
    keys = {
        'openai': os.getenv('OPENAI_API_KEY'),
        'qdrant_url': os.getenv('QDRANT_URL'),
        'qdrant_key': os.getenv('QDRANT_API_KEY')
    }
    
    missing = [k for k, v in keys.items() if v is None]
    if missing:
        raise ValueError(f"Missing keys in .env file: {missing}")
    
    return keys


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


def get_model_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """
    Calculate USD cost from token counts using current pricing.
    
    Args:
        model_name: Model identifier (e.g., 'gpt-4o-2024-08-06')
        input_tokens: Input token count from response.usage
        output_tokens: Output token count from response.usage
    
    Returns:
        Total cost in USD
    
    Example:
        cost = get_model_cost('gpt-4o-2024-08-06', 1000, 500)
        # Returns: 0.0025 + 0.0050 = 0.0075 USD
    """
    pricing = PRICING_CONFIG["models"].get(model_name)
    if not pricing:
        logging.warning(f"get_model_cost: unknown model '{model_name}', cost not tracked.")
        return 0.0
    
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
def resolve_qdrant_collection() -> str:
    """Resolve the COLLECTION_NAME alias to the actual backing collection.
    
    Qdrant aliases allow COLLECTION_NAME to remain constant ('trial_criteria')
    while the actual collection rotates weekly ('trial_criteria_20260226_140159').
    This function resolves the alias to the real collection name for logging.
    
    Retries up to 3 times with 1s delay if resolution fails or alias not found.
    """
    
    MAX_RETRIES = 3
    
    for attempt in range(1, MAX_RETRIES + 1):
        
        try:
            all_aliases = qdrant_client.get_aliases().aliases
            for a in all_aliases:
                if a.alias_name == COLLECTION_NAME:
                    return a.collection_name
            print(f"⚠ Alias '{COLLECTION_NAME}' not found in Qdrant (attempt {attempt}/{MAX_RETRIES})")
        except Exception as e:
            print(f"⚠ Qdrant alias resolution error (attempt {attempt}/{MAX_RETRIES}): {e}")
        
        if attempt < MAX_RETRIES:
            time.sleep(1)
    
    # Final fallback: check if COLLECTION_NAME is itself a real collection (no alias)
    try:
        qdrant_client.get_collection(COLLECTION_NAME)
        print(f"⚠ '{COLLECTION_NAME}' is a real collection, not an alias. Using as-is.")
        return COLLECTION_NAME
    except Exception:
        pass
    
    print(f"⚠ FAILED to resolve collection after {MAX_RETRIES} attempts. Using '{COLLECTION_NAME}' as fallback.")
    return COLLECTION_NAME


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


def get_age_reference_date() -> date:
    """The fixed date this run computes patient ages against.

    Resolves DATA_SNAPSHOT_DATE from File 03 -- see the comment there for why
    the current clock cannot be used.

    Raises ValueError when the constant is missing or is not a full date.
    Falling back to today() here would restore the exact defect the constant
    exists to remove, and would do it silently; an unset snapshot date is a
    configuration error, not a runtime condition to recover from.
    """

    raw = globals().get("DATA_SNAPSHOT_DATE", "")
    reference, precision = parse_partial_date(raw)

    if reference is None or precision != "day":
        raise ValueError(
            f"DATA_SNAPSHOT_DATE must be a full YYYY-MM-DD date in "
            f"'03- Config.py'; got {raw!r} (parsed precision: {precision}). "
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