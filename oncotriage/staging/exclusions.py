# Staging Exclusion Rulings
###########################

"""
Load, validate and apply ``s3_staging_exclusions.json``.

WHY THE RULINGS ARE DATA. Which subtree is staged is an operator decision that
changes with the campaign, and encoding it as code means every change is a diff
against logic. It is a committed JSON manifest, so a ruling flips in one edit,
the REASON for every ruling is version-controlled beside it, and the loader can
refuse a manifest that has lost a reason.

WHY A DEFECT RAISES. Item 11a's line: a configuration fault that one edit fixes
raises, third-party data that no operator can fix is counted. Everything here is
the former. A manifest that will not parse, or that names a staged path with no
reason, must stop the run rather than degrade into "stage everything", which is
the one degradation that uploads a credentials directory.

WHAT THIS MODULE DOES NOT DO. It does not guarantee that no secret uploads.
Path rules are policy and performance; ``secrets_scan.py`` is the guarantee, and
it runs over whatever survives these rules on every invocation. The split is
what makes a stale path rule cheap: under a rename it costs performance and
record-keeping, never a hole.

MATCHING IS ON WHOLE PATH COMPONENTS, never on a string prefix. ``"02- Data/04-
MeSH"`` excludes that directory and everything under it and does NOT match
``"02- Data/04- MeSHX"``. A string ``startswith`` would, which is the shape that
turns a rename into a silent inclusion.
"""

import json
import os

from oncotriage import paths
from oncotriage.observability import get_logger

log = get_logger(__name__)


#------------------------------------------------------------------------------


DEFAULT_MANIFEST_FILENAME = "s3_staging_exclusions.json"

# The one schema this loader understands. A manifest declaring anything else is
# refused by version BEFORE any field is read, on load_fixture()'s precedent:
# comparing fields across two shapes reports a difference for every field the
# other version did not carry, which buries the one real finding.
SCHEMA_VERSION = 1

# The kinds an exclusion may declare. CLOSED, and an unknown kind raises --
# deps.OVERRIDE_KEYS' shape. The kind is not decoration: `secrets` entries are
# the ones the standing test asserts individually, so a typo that silently
# demoted `05- Keys` from `secrets` to nothing would take that assertion with
# it.
EXCLUSION_KINDS = ("secrets", "superseded", "regenerable", "operator-ruled")


class ManifestError(RuntimeError):
    """The staging manifest is missing, unreadable, or self-inconsistent.

    A RuntimeError subclass and deliberately not a ValueError, on the
    UnknownModelPricingError / IndexVerificationError precedent: a stray
    ``except ValueError`` around a json.load must not be able to eat it.
    """


#------------------------------------------------------------------------------
# LOADING
#------------------------------------------------------------------------------


def default_manifest_path():
    """``{code_path}/s3_staging_exclusions.json``.

    Resolved on CALL, never at import: ``paths.code_path`` is a lazy glob and
    importing this module must not fire it (section 2 of
    tests/test_package_invariants.py imports every package module with the
    project root pointed at a directory that does not exist).
    """
    return os.path.join(paths.code_path, DEFAULT_MANIFEST_FILENAME)


def load_manifest(manifest_path=None):
    """Read and validate the rulings. Raises ManifestError on any defect.

    ``manifest_path=None`` means the committed manifest. An explicit path is
    never cached -- the argument answers a question about one call, and a cache
    would make a test's scratch manifest the process-wide answer.
    """
    path = manifest_path if manifest_path is not None else default_manifest_path()

    if not os.path.isfile(path):
        raise ManifestError(
            f"Staging manifest not found: {path}\n"
            f"This file is the ONLY source of staging rulings and there is no "
            f"default set compiled in, deliberately: a loader that fell back to "
            f"'stage everything' would upload the credentials directory the "
            f"first time somebody moved this file."
        )

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ManifestError(
            f"Staging manifest unreadable: {path}\n"
            f"  {type(exc).__name__}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ManifestError(
            f"Staging manifest must be a JSON object, got "
            f"{type(data).__name__}: {path}"
        )

    found_version = data.get("schema_version")
    if found_version != SCHEMA_VERSION:
        raise ManifestError(
            f"Staging manifest schema_version is {found_version!r}, this loader "
            f"understands {SCHEMA_VERSION!r}: {path}"
        )

    _validate(data, path)
    return data


def _validate(data, path):
    """Every structural rule, applied before any caller sees the manifest."""
    problems = []

    for section in ("staged", "excluded", "noise", "path_names_non_paths",
                    "secret_scan_allowlist"):
        if section not in data:
            problems.append(f"missing required section {section!r}")
        elif not isinstance(data[section], list):
            problems.append(
                f"section {section!r} must be a list, got "
                f"{type(data[section]).__name__}")

    if problems:
        raise ManifestError(
            f"Staging manifest is malformed: {path}\n  - "
            + "\n  - ".join(problems))

    seen_staged = set()
    for entry in data["staged"]:
        problems += _check_entry(entry, "staged", ("path", "reason"))
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            rel = _normalise(entry["path"])
            if rel in seen_staged:
                problems.append(f"staged path declared twice: {entry['path']!r}")
            seen_staged.add(rel)

    seen_excluded = set()
    for entry in data["excluded"]:
        problems += _check_entry(entry, "excluded", ("path", "reason", "kind"))
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        if kind is not None and kind not in EXCLUSION_KINDS:
            problems.append(
                f"excluded entry {entry.get('path')!r} declares unknown kind "
                f"{kind!r}; the closed set is {list(EXCLUSION_KINDS)}")
        if isinstance(entry.get("path"), str):
            rel = _normalise(entry["path"])
            if rel in seen_excluded:
                problems.append(f"excluded path declared twice: {entry['path']!r}")
            seen_excluded.add(rel)

    for entry in data["noise"]:
        problems += _check_entry(entry, "noise", ("pattern", "reason"))

    for entry in data["path_names_non_paths"]:
        problems += _check_entry(entry, "path_names_non_paths", ("name", "reason"))

    for entry in data["secret_scan_allowlist"]:
        problems += _check_entry(
            entry, "secret_scan_allowlist", ("path", "sha256", "reason"))

    # A path cannot be both staged and excluded at the top level. Nested is
    # fine and is the normal case ("02- Data" staged, "02- Data/04- MeSH"
    # excluded); an EXACT collision is a contradiction with no defensible
    # reading, so it raises rather than one side quietly winning.
    for rel in sorted(seen_staged & seen_excluded):
        problems.append(
            f"path is both staged and excluded: {'/'.join(rel)!r}")

    if problems:
        raise ManifestError(
            f"Staging manifest is malformed: {path}\n  - "
            + "\n  - ".join(problems))


def _check_entry(entry, section, required):
    """Shape of one manifest entry. Returns a list of problem strings."""
    if not isinstance(entry, dict):
        return [f"{section}: entry is {type(entry).__name__}, expected an object"]
    found = []
    for field in required:
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            found.append(
                f"{section}: entry {entry!r} needs a non-empty string "
                f"{field!r}")
    return found


#------------------------------------------------------------------------------
# CLASSIFICATION
#------------------------------------------------------------------------------


def _normalise(relpath):
    """A root-relative path as a tuple of components.

    Tuples rather than strings because membership then compares COMPONENTS.
    ``("02- Data", "04- MeSH")`` is not a prefix of ``("02- Data", "04- MeSHX")``,
    while the string ``"02- Data/04- MeSH"`` is a prefix of
    ``"02- Data/04- MeSHX"``. That difference is the whole reason this function
    exists.
    """
    cleaned = str(relpath).replace("\\", "/").strip("/")
    return tuple(part for part in cleaned.split("/") if part and part != ".")


class StagingPlan:
    """The rulings, resolved into the three lookups the walk needs.

    Not a dataclass: check 2i of tests/test_package_invariants.py pins the exact
    decorator list of every definition in the package, so a decorator here is a
    second edit in a second file for no gain.
    """

    def __init__(self, staged, excluded, noise, non_path_names, allowlist):
        self.staged = staged                  # {components: reason}
        self.excluded = excluded              # {components: (reason, kind)}
        self.noise = noise                    # {basename: reason}
        self.non_path_names = non_path_names  # {name: reason}
        self.allowlist = allowlist            # {(relpath, sha256): reason}

    def staged_roots(self):
        """Top-level staged components, sorted, as display strings."""
        return sorted("/".join(parts) for parts in self.staged)


def build_plan(manifest):
    """Turn a validated manifest into a StagingPlan."""
    return StagingPlan(
        staged={_normalise(e["path"]): e["reason"] for e in manifest["staged"]},
        excluded={_normalise(e["path"]): (e["reason"], e["kind"])
                  for e in manifest["excluded"]},
        noise={e["pattern"]: e["reason"] for e in manifest["noise"]},
        non_path_names={e["name"]: e["reason"]
                        for e in manifest["path_names_non_paths"]},
        allowlist={(e["path"], e["sha256"]): e["reason"]
                   for e in manifest["secret_scan_allowlist"]},
    )


def classify(relpath, plan):
    """Why this root-relative path is or is not staged.

    Returns ``(staged: bool, reason: str, rule: str)``. ``rule`` is the manifest
    entry that decided it, so a report can say WHICH ruling applied rather than
    only that one did.

    THE MOST SPECIFIC RULE WINS, which is what lets "02- Data" be staged and
    "02- Data/04- MeSH" excluded underneath it. Specificity is depth, so the
    longest matching component prefix decides and a tie is impossible: an exact
    staged/excluded collision is refused at load.
    """
    parts = _normalise(relpath)

    if any(part in plan.noise for part in parts):
        hit = next(part for part in parts if part in plan.noise)
        return False, plan.noise[hit], f"noise:{hit}"

    best_depth = -1
    best = (False, "no staging ruling covers this path", "unruled")

    for candidate, reason in plan.staged.items():
        if len(candidate) > best_depth and parts[:len(candidate)] == candidate:
            best_depth = len(candidate)
            best = (True, reason, "staged:" + "/".join(candidate))

    for candidate, (reason, kind) in plan.excluded.items():
        if len(candidate) > best_depth and parts[:len(candidate)] == candidate:
            best_depth = len(candidate)
            best = (False, reason, f"excluded[{kind}]:" + "/".join(candidate))

    return best


def excluded_kinds(plan, kind):
    """Every excluded path of one kind, as display strings. Sorted."""
    return sorted("/".join(parts) for parts, (_r, k) in plan.excluded.items()
                  if k == kind)


#------------------------------------------------------------------------------
# THE PATH_NAMES CROSS-CHECK
#------------------------------------------------------------------------------


class PathNamesUnclassified(RuntimeError):
    """A path variable is neither staged nor excluded-with-a-reason.

    This is the guard the brief asks for in as many words: inclusion is a walk
    of the root minus the manifest, so a NEW path variable pointing somewhere
    the manifest has never heard of would otherwise be missed in silence. It
    raises, because the remedy is one manifest edit.
    """


def cross_check_path_names(plan, resolved=None):
    """Every oncotriage.paths.PATH_NAMES member is classified, or this raises.

    ``resolved`` is ``{name: value}``; ``None`` resolves them for real, which
    touches the filesystem, so a test passes its own mapping instead.

    THE NON-PATH MEMBERS ARE DECLARED, NOT DETECTED. ``PATH_NAMES`` has 20
    members and ``_main_path_source`` is not a path -- it is the string
    'fallback' or 'environment'. A heuristic ("does it look like a path?")
    would classify it correctly today and silently swallow the next non-path
    member; a declared set fails instead, which is the direction that keeps the
    cross-check honest. Both directions are checked: a declared non-path that
    HAS become a path is also a failure.
    """
    if resolved is None:
        resolved = {}
        for name in paths.PATH_NAMES:
            resolved[name] = getattr(paths, name)

    root = os.path.abspath(str(resolved.get("main_path", paths.main_path)))
    report = {}
    unclassified = []

    for name in sorted(resolved):
        value = resolved[name]

        if name in plan.non_path_names:
            if os.path.isabs(str(value)):
                unclassified.append(
                    f"{name} is declared a NON-PATH in the manifest "
                    f"(path_names_non_paths) but resolves to an absolute path "
                    f"{value!r}. Remove the declaration and give it a staging "
                    f"ruling.")
            else:
                report[name] = ("non-path", plan.non_path_names[name],
                                "declared", str(value))
            continue

        absolute = os.path.abspath(str(value))
        if absolute == root:
            report[name] = ("root", "the project root itself", "root", absolute)
            continue

        if os.path.commonpath([absolute, root]) != root:
            unclassified.append(
                f"{name} resolves OUTSIDE the project root and no ruling can "
                f"cover it: {value!r} (root {root!r})")
            continue

        rel = os.path.relpath(absolute, root)
        staged, reason, rule = classify(rel, plan)
        if rule == "unruled":
            unclassified.append(
                f"{name} -> {rel!r} is neither staged nor excluded. Add an "
                f"entry to s3_staging_exclusions.json naming it and saying "
                f"why.")
        else:
            report[name] = ("staged" if staged else "excluded", reason, rule,
                            absolute)

    if unclassified:
        raise PathNamesUnclassified(
            "oncotriage.paths.PATH_NAMES members are not covered by the "
            "staging manifest:\n  - " + "\n  - ".join(unclassified))

    # MESSAGE ONLY, NO FIELDS, and that is deliberate. `oncotriage/
    # observability.py` enforces LOGGABLE_FIELDS in the FORMATTER: a field not
    # on that allowlist is dropped and counted in FIELD_DROPS, so passing a
    # count here would either be silently discarded or force a widening of a
    # security-relevant allowlist for a line that carries no diagnosis the
    # console report does not already carry.
    log.info("staging_path_names_cross_check_ok")
    return report
