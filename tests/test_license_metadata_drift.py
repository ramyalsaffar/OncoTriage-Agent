"""The licence and the repository identity are declared in six places at once.

WHAT THIS FILE IS ABOUT
=======================
One licence swap touched six files that each declare the same two facts -- WHAT
LICENCE THIS IS and WHERE THIS REPOSITORY LIVES -- in six different notations:

    CITATION.cff                 license:  <SPDX identifier>   (YAML, CFF 1.2.0)
    LICENSE                      the licence TEXT itself
    pyproject.toml               project.license.text          (TOML)
    Dockerfile                   org.opencontainers.image.*    (LABEL)
    docker/mesh-core/PROVENANCE.md  the repository URL, in prose
    README.md                    a badge, a link and a section

NOTHING FAILS WHEN THEY DISAGREE. Every one of the six is read by a different
consumer -- GitHub's "Cite this repository" control, `pip show oncotriage`, a
container registry, a person -- and no consumer reads two of them, so a swap
that reached five of six ships a package whose metadata says one thing and
whose image label says another, with no error anywhere. That is the same class
`tests/test_dockerignore_exclusions.py` and
`.github/scripts/trivyignore_staleness.py` exist for: a declaration that has
stopped describing anything is re-read by the next person as a live constraint.

The failure is not hypothetical for this project. Before the swap, CITATION.cff
carried no `license` key at all (the former terms had no SPDX identifier, so
the schema's non-standard-licence field carried a link instead), the Dockerfile
title named a project called `Clinical-Trial-Patient-Match`, and the image
description named a system nobody had called that in a year.

THE SINGLE SOURCE OF TRUTH IS CITATION.cff
-------------------------------------------
Every identifier comparison below is against IDENT and REPO_URL, both READ OUT
OF CITATION.cff at the top of this file rather than typed here. That direction
is deliberate and it is the same one `oncotriage/config.py` takes for the
cross-encoder checkpoint: a literal retyped in the test agrees with a literal
retyped in the code exactly until one of them moves, and then the check is
satisfied by the wrong evidence. The ONE value pinned as a literal here is the
sha256 of the published PolyForm text, which is the one fact that has no other
declaration in the tree to be compared against -- see SECTION 2.

CITATION.cff IS PARSED WITH A REGEX AND NOT WITH PyYAML, AND THAT IS MEASURED
-----------------------------------------------------------------------------
`pyyaml` is not a declared dependency of this project (`pyproject.toml` names
it nowhere, measured 2026-09-02), and a drift test is the wrong place to add
one -- it would put a parser in the install graph of every consumer of a
package that does not otherwise read YAML. What this file needs from that file
is three TOP-LEVEL SCALARS, which is a strictly smaller language than YAML, so
`cff_top_level_scalars` reads exactly that: an unindented `key: value` line,
comments skipped, indented lines (list items, block-scalar bodies) skipped.
THE LIMIT IS STATED RATHER THAN DISCOVERED -- it does not understand anchors,
flow mappings or multi-line scalars, and a `license` written in any of those
forms would read as absent. It also does not strip a YAML inline comment
from a value, so `license: X  # note` would read as `X  # note` and fail check
1b -- LOUDLY, and with an obvious repair, which is the right direction to be
wrong in. Both limits are non-degeneracy-checked (SECTION 1) so a parser that
has stopped parsing fails here instead of reporting a clean file. The
Dockerfile reader has the mirror limit: it reads only the `key="value"` form,
not the legacy space-separated one, and SECTION 4's own non-degeneracy check
is what fails if that ever stops matching.

THE REPOSITORY SWEEP IS NOT THE SUBSTRING SWEEP IT LOOKS LIKE
--------------------------------------------------------------
SECTION 7 forbids two things. The first, an all-rights-reserved notice, is a
raw substring over the whole tree and measures ZERO today, so it stands as
written. The second is NOT: the key CITATION.cff used to carry appears in that
file's own header comment, in the paragraph explaining that the key is gone --
so a raw substring test for it is red on a correct tree, and it would be red
naming the documentation of the fix as the defect. THIS PROJECT HAS SHIPPED
THAT EXACT SHAPE FOUR TIMES (`tests/test_docker_qdrant_override_and_readiness.py`
sections 5e and 7e, `tests/test_fixture_call_mode_pin.py` check 2g, and the
compose-secrets pass, whose own comment reproduced the secret it removed). So
the check is on the DECLARATION -- a line that begins, after optional
whitespace, with that key and a colon, which is what a CFF key is and what a
comment is not -- and the prose count is PRINTED AND NOT GATED beside it, so
the divergence between the two readings is visible rather than silent.

Both needles are ASSEMBLED AT RUN TIME from fragments, on
`tests/test_secret_scan_gate.py`'s precedent: this file is inside the tree it
sweeps, and a scanner that reports itself is a scanner nobody can run.

WHAT IS DELIBERATELY NOT CHECKED
---------------------------------
That the licence TEXT is the one PolyForm publishes. That was established once,
by fetching it, and cannot be re-established without a network call -- so what
is pinned is the sha256 that measurement produced. A pinned digest is a
one-time fact frozen; it catches an edit to the licence body and it cannot
catch the original fetch having been wrong. Stated here rather than implied.

That the image actually CARRIES the labels. That needs a Docker daemon, which
is what bucket A is defined to exclude; what is checked is the Dockerfile's
instruction text, the same reading
`tests/test_docker_qdrant_override_and_readiness.py` section 7e takes.

BUCKET A. No network, no keys, no spend, no Docker daemon, no live Qdrant, no
model, no corpus, no database, no git history, no subprocess, no live server.
It IMPORTS NOTHING from the package -- its subjects are six repository files
and a filesystem walk -- and it EXECS NOTHING and loads no module by location:
every control is a different ARGUMENT to a pure function, over a string mutated
in memory. It WRITES NOTHING ANYWHERE, not even a temp directory. NOT in the
collision matrix, derived: it writes no repository file, and the six it reads
are written by neither of the suite's two writers
(`tests/test_registries_cancer_code_claims_audit_control.py` writes
`oncotriage/registries/cancer_code_registry.py`, and
`tests/test_config_snapshot_date_rot.py` writes `oncotriage/config.py`); all
six are sha256-compared in SECTION 9 anyway.
"""

import hashlib
import os
import re
import sys
import tomllib


# ===========================================================================
# WHERE THINGS ARE
# ===========================================================================
# realpath, not abspath: the sweep in SECTION 7 compares paths derived from a
# walk of this same root, and a symlinked checkout would otherwise make the two
# disagree about a prefix. tests/test_trivyignore_staleness.py records the
# measurement that established this -- three checks failed against a
# byte-identical copy under a temp directory only because of macOS's
# /var -> /private/var link.
_TESTS_DIR = os.path.dirname(os.path.realpath(__file__))
_CODE_DIR = os.path.dirname(_TESTS_DIR)

_CITATION = os.path.join(_CODE_DIR, "CITATION.cff")
_LICENSE = os.path.join(_CODE_DIR, "LICENSE")
_PYPROJECT = os.path.join(_CODE_DIR, "pyproject.toml")
_DOCKERFILE = os.path.join(_CODE_DIR, "Dockerfile")
_PROVENANCE = os.path.join(_CODE_DIR, "docker", "mesh-core", "PROVENANCE.md")
_README = os.path.join(_CODE_DIR, "README.md")

_READ_FILES = (_CITATION, _LICENSE, _PYPROJECT, _DOCKERFILE, _PROVENANCE,
               _README)

# A HARD GUARD AND NOT A check(). A wrong root here is not one failure but
# every failure, each with a misleading message -- the argument
# tests/test_package_invariants.py, tests/test_config_snapshot_date_rot.py and
# tests/test_dockerignore_exclusions.py all make for the same guard.
for _p in _READ_FILES:
    if not os.path.isfile(_p):
        raise SystemExit(
            f"CANNOT RUN: {_p} not found. This file derives the repository "
            f"root from its own location ({_CODE_DIR}); if the tests directory "
            f"moved, that derivation moved with it.")


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path):
    with open(path, "rb") as fh:
        return _sha256_bytes(fh.read())


_SHA_BEFORE = {p: _sha256_file(p) for p in _READ_FILES}


# ===========================================================================
# THE HARNESS
# ===========================================================================
_passed = 0
_failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def check_true(label, cond):
    check(label, bool(cond), True)


def section(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def guarded(fn, *args, **kwargs):
    """Call `fn`, converting a raise into a value `check` fails on.

    THE ABORT SHAPE, WHICH THIS PROJECT HAS SHIPPED SEVENTEEN TIMES: a bare
    call inside a `check(...)` argument list raises while the argument is being
    evaluated, so the run prints one traceback where it owed a summary and
    every result below it. Every control here plants a defect that is designed
    to break a parser, which is precisely when the raise happens.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:                      # noqa: BLE001 -- deliberate
        return f"<RAISED {type(exc).__name__}: {exc}>"


# ===========================================================================
# THE PURE FUNCTIONS EVERYTHING IS DRIVEN THROUGH
# ===========================================================================
# Pure, so every control below is a different ARGUMENT rather than a file
# mutated on disk -- the shape tests/test_agent_patient_hash_coverage.py and
# tests/test_dockerignore_exclusions.py settled on. It is also what makes the
# claim "this file writes nothing" a property of its construction rather than
# of its discipline.

_CFF_SCALAR_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):[ \t]*(.*)$")


def cff_top_level_scalars(text):
    """Top-level ``key: value`` scalars of a CFF file, last occurrence winning.

    Unindented lines only, so list items and block-scalar bodies are skipped;
    full-line comments skipped. A quoted value is unquoted. LAST WINS, which is
    YAML's own rule for a duplicated key -- and `cff_duplicate_keys` reports the
    duplication separately, because a file with two `license:` lines is a file
    whose licence depends on which parser reads it.
    """
    out = {}
    for raw in text.splitlines():
        if not raw or raw[:1].isspace() or raw.lstrip().startswith("#"):
            continue
        m = _CFF_SCALAR_RE.match(raw)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        out[key] = val
    return out


def cff_duplicate_keys(text):
    """Top-level keys declared more than once, sorted."""
    seen = {}
    for raw in text.splitlines():
        if not raw or raw[:1].isspace() or raw.lstrip().startswith("#"):
            continue
        m = _CFF_SCALAR_RE.match(raw)
        if m:
            seen[m.group(1)] = seen.get(m.group(1), 0) + 1
    return sorted(k for k, n in seen.items() if n > 1)


def _dockerfile_instructions(text):
    """The Dockerfile's instruction lines, comments dropped, continuations joined.

    A COPY of the parser in tests/test_docker_qdrant_override_and_readiness.py,
    not an import: that module runs 127 checks at import time (it is a test, not
    a library), so importing it to reach one eight-line function would run a
    whole other suite as a side effect of this one. The two are compared in
    SECTION 8's control rather than merely duplicated.
    """
    out, pending = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1].rstrip() + " "
            continue
        out.append((pending + line).strip())
        pending = ""
    if pending:
        out.append(pending.strip())
    return out


def pyproject_license_identifier(project):
    """The SPDX identifier `project.license` declares, in EITHER legal form.

    PEP 621 writes a TABLE (``license = { text = "..." }``); PEP 639 writes the
    bare STRING (``license = "..."``). `pyproject.toml`'s own comment records
    that this project is on the table form only because the development
    interpreter's setuptools is below 77, and names the two-line migration to
    the string form as correct the day it clears -- so a reader that assumed
    the table would abort with an `AttributeError` out of a `check(...)`
    argument list on a LEGITIMATE edit that this repository has already written
    down as planned. That is the abort shape, and here it would fire on the one
    change the file it reads says is coming. Both forms carry the same SPDX
    identifier, so reading both is the correct behaviour rather than a
    tolerance: a `dict` without `text`, or anything else, returns None and the
    equality below fails loudly.
    """
    value = project.get("license")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("text")
    return None


_LABEL_PAIR_RE = re.compile(r'([A-Za-z0-9_.-]+)="([^"]*)"')


def dockerfile_labels(text):
    """Every ``key="value"`` pair of every LABEL instruction, last winning.

    READ OFF THE INSTRUCTIONS, NOT OFF THE FILE TEXT, for the reason
    tests/test_docker_qdrant_override_and_readiness.py records having learned
    three separate times: this Dockerfile's header comments QUOTE the label
    values they argue about, so a substring search over the whole file is
    satisfied -- or defeated -- by the prose explaining it.
    """
    out = {}
    for ins in _dockerfile_instructions(text):
        if not ins.upper().startswith("LABEL "):
            continue
        for key, val in _LABEL_PAIR_RE.findall(ins[len("LABEL "):]):
            out[key] = val
    return out


# The needles, assembled. See the docstring: this file is inside the tree
# SECTION 7 sweeps, and a scanner that reports itself is a scanner nobody can
# run. tests/test_secret_scan_gate.py caught its own author this way.
_RESERVED_NEEDLE = "All rights" + " reserved"
_URL_KEY = "license" + "-url"
_URL_KEY_DECLARATION_RE = re.compile(r"^[ \t]*" + re.escape(_URL_KEY) + r"[ \t]*:",
                                     re.MULTILINE)


def declares_url_key(text):
    """True when `text` DECLARES the non-standard-licence key, not merely names it.

    A declaration is a line beginning, after optional whitespace, with the key
    and a colon. That is what a CFF/YAML key is; a `#` comment, a markdown
    bullet and a Python string are none of them. See the docstring for why the
    raw-substring form of this question is red on a correct tree.
    """
    return bool(_URL_KEY_DECLARATION_RE.search(text))


def mentions_reserved_notice(text):
    return _RESERVED_NEEDLE.lower() in text.lower()


# Directories a licence sweep must not descend into, each argued:
#   .git            -- object storage, not source
#   __pycache__     -- compiled artefacts
#   build           -- build artefacts
#   *.egg-info      -- generated metadata; PKG-INFO inlines package metadata
#   node_modules, .pytest_cache, .mypy_cache, .ruff_cache -- third-party/derived
# and, by MARKER rather than by name, any directory carrying a `pyvenv.cfg`.
# THAT LAST ONE IS LOAD-BEARING AND IS NOT DEFENSIVE PROGRAMMING: this tree
# holds `09- Testing/ragas-venv/` -- 1.7 GB and 92,649 files -- and a licence
# sweep that descends into it reads several thousand third-party licences,
# every one of which carries a reservation notice. That is the measurement
# tests/test_dockerignore_exclusions.py records for its own nested-__pycache__
# count: a walk that goes somewhere its subject does not is not a bigger
# number, it is a different question. The marker is what a virtualenv IS; the
# name is what somebody called it.
_PRUNE_DIRS = frozenset({".git", "__pycache__", "build", ".pytest_cache",
                         ".mypy_cache", ".ruff_cache", "node_modules"})
_SKIP_EXT = frozenset({".rtf"})


def sweep_text_files(root):
    """Every scannable text file under `root`, sorted, with the pruning above.

    A FILESYSTEM WALK AND NOT `git ls-files`, so that this file needs no git --
    and the limit that buys is stated: an untracked text file left in the tree
    is scanned. That errs toward REPORTING, whose repair is to prune it or
    remove it; the alternative errs toward missing a licence notice, which is
    the direction that matters. A file that does not decode as UTF-8 is
    counted as binary and skipped.
    """
    paths, skipped_binary, pruned = [], 0, []
    for dirpath, dirnames, filenames in os.walk(root):
        if os.path.isfile(os.path.join(dirpath, "pyvenv.cfg")):
            dirnames[:] = []
            pruned.append(dirpath)
            continue
        dirnames[:] = sorted(d for d in dirnames
                             if d not in _PRUNE_DIRS and not d.endswith(".egg-info"))
        for name in sorted(filenames):
            if os.path.splitext(name)[1].lower() in _SKIP_EXT:
                continue
            paths.append(os.path.join(dirpath, name))
    out = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                out.append((path, fh.read()))
        except (UnicodeDecodeError, OSError):
            skipped_binary += 1
    return out, skipped_binary, sorted(pruned)


# ===========================================================================
# SECTION 1 -- CITATION.cff IS THE SOURCE OF TRUTH
# ===========================================================================
section("SECTION 1 -- CITATION.cff: the identifier and the repository URL")

_citation_text = open(_CITATION, encoding="utf-8").read()
_cff = cff_top_level_scalars(_citation_text)

# NON-DEGENERACY FIRST. Every check below is an equality or an absence over
# this dict, and both are satisfied for free by an empty one -- a parser that
# has stopped parsing would report a clean file with no key in it.
check("1a  the CFF parser found a real top-level mapping (non-degeneracy)",
      len(_cff) >= 8, True)
check("1a  ...and no top-level key is declared twice",
      guarded(cff_duplicate_keys, _citation_text), [])

IDENT = _cff.get("license")
REPO_URL = _cff.get("repository-code")

check("1b  CITATION.cff declares the SPDX identifier",
      IDENT, "PolyForm-Noncommercial-1.0.0")
check("1c  ...and carries no non-standard-licence URL key beside it, which "
      "would assert the opposite of what a standard identifier says",
      _URL_KEY in _cff, False)
check("1d  cff-version is the one GitHub's parser reads",
      _cff.get("cff-version"), "1.2.0")
check("1e  repository-code is present and is an https URL",
      bool(REPO_URL) and REPO_URL.startswith("https://"), True)
print(f"  (IDENT    = {IDENT!r})")
print(f"  (REPO_URL = {REPO_URL!r})")


# ===========================================================================
# SECTION 2 -- LICENSE: the published text, byte for byte, plus the notice
# ===========================================================================
section("SECTION 2 -- LICENSE is the published text plus the required notice")

# THE ONE PINNED LITERAL IN THIS FILE, AND IT IS PINNED BECAUSE IT HAS NO OTHER
# DECLARATION TO BE COMPARED AGAINST. Everything else here is read out of
# CITATION.cff; the licence BODY is a fact about a document published at
# <https://polyformproject.org/licenses/noncommercial/1.0.0>, which no file in
# this repository restates. N is the length of that published plain text and
# the digest is over exactly those bytes.
#
# WHAT IT CATCHES AND WHAT IT CANNOT: it catches any edit to the licence body,
# which is the drift that matters (a licence with a word changed is a different
# licence and is no longer the one the SPDX identifier names). It cannot catch
# the original fetch having been wrong, because a pinned digest freezes a
# one-time measurement. Re-establishing that needs a network call, which bucket
# A excludes.
_PUBLISHED_BYTES = 4563
_PUBLISHED_SHA256 = \
    "ffcca38841adb694b6f380647e15f17c446a4d1656fed51a1e2041d064c94cc8"

with open(_LICENSE, "rb") as _fh:
    _license_bytes = _fh.read()

_license_first_line = _license_bytes.split(b"\n", 1)[0].decode("utf-8")

check("2a  line 1 is the licence's own title", _license_first_line,
      "# PolyForm Noncommercial License 1.0.0")
check("2b  the file is longer than the published text (there is a notice)",
      len(_license_bytes) > _PUBLISHED_BYTES, True)
check("2c  the first %d bytes are the published text, byte for byte"
      % _PUBLISHED_BYTES,
      _sha256_bytes(_license_bytes[:_PUBLISHED_BYTES]), _PUBLISHED_SHA256)

# THE REMAINDER. The brief this was written from described it as "exactly one
# line beginning 'Required Notice:' plus at most one trailing newline"; MEASURED,
# it is a BLANK LINE, then that line, then a newline -- the blank line is the
# markdown paragraph break separating the notice from the licence body, and a
# regex written to the description rather than to the file would have failed on
# a correct LICENSE. The optional leading newline is that blank line.
_REQUIRED_NOTICE_RE = re.compile(rb"\A\n?Required Notice: [^\n]+\n?\Z")
_tail = _license_bytes[_PUBLISHED_BYTES:]

check("2d  everything after the published text is one Required Notice line",
      bool(_REQUIRED_NOTICE_RE.match(_tail)), True)
check("2e  ...and it is exactly one non-empty line",
      len([ln for ln in _tail.split(b"\n") if ln.strip()]), 1)
check("2f  ...and the notice names the repository CITATION.cff declares",
      REPO_URL.encode("utf-8") in _tail if REPO_URL else False, True)
check("2g  the pin is a real digest over a proper prefix (non-degeneracy)",
      len(_PUBLISHED_SHA256) == 64
      and _PUBLISHED_SHA256 != _sha256_bytes(_license_bytes), True)


# ===========================================================================
# SECTION 3 -- pyproject.toml
# ===========================================================================
section("SECTION 3 -- pyproject.toml declares the same identifier")

with open(_PYPROJECT, "rb") as _fh:
    _pyproject = tomllib.load(_fh)

_project = _pyproject.get("project", {})
_authors = _project.get("authors", [])

check("3a  project.license declares the SPDX identifier CITATION.cff does",
      guarded(pyproject_license_identifier, _project), IDENT)
check("3b  project.authors has exactly one entry", len(_authors), 1)
check("3c  ...and it is the copyright holder named in the required notice",
      _authors[0].get("name") if _authors else None, "Ramy Alsaffar")


# ===========================================================================
# SECTION 4 -- the image labels
# ===========================================================================
section("SECTION 4 -- the OCI labels name this project and this licence")

_dockerfile_text = open(_DOCKERFILE, encoding="utf-8").read()
_labels = dockerfile_labels(_dockerfile_text)

check("4a  LABEL instructions parsed into a real mapping (non-degeneracy)",
      len(_labels) >= 8, True)
check("4b  org.opencontainers.image.licenses is the SPDX identifier",
      _labels.get("org.opencontainers.image.licenses"), IDENT)
check("4c  org.opencontainers.image.source is the repository CITATION.cff "
      "declares", _labels.get("org.opencontainers.image.source"), REPO_URL)
check("4d  org.opencontainers.image.title is the project's name",
      _labels.get("org.opencontainers.image.title"), "OncoTriage Agent")

# THE THREE RETIRED LABELS. Each named a project this repository has not been
# called since it was renamed -- `Clinical-Trial-Patient-Match` is the SIBLING
# DIRECTORY's name, and `trialbridge` was an earlier working name. A stale
# title on a published image is not cosmetic: it is what a registry lists and
# what a scanner attributes a finding to.
_RETIRED_LABELS = ("description", "org.opencontainers.image.title",
                   "org.opencontainers.image.description")
_retired_text = " ".join(_labels.get(k, "") for k in _RETIRED_LABELS)

check("4e  all three retired labels are present (non-degeneracy: an absent "
      "label is not a corrected one)",
      sorted(k for k in _RETIRED_LABELS if k in _labels),
      sorted(_RETIRED_LABELS))
check("4f  ...and none of them names the sibling directory",
      "Clinical-Trial-Patient-Match" in _retired_text, False)
check("4g  ...and none of them names the earlier working name",
      "trialbridge" in _retired_text.lower(), False)


# ===========================================================================
# SECTION 5 -- the vendored MeSH provenance
# ===========================================================================
section("SECTION 5 -- docker/mesh-core/PROVENANCE.md names this repository")

_provenance_text = open(_PROVENANCE, encoding="utf-8").read()

check("5a  it names the repository CITATION.cff declares, verbatim",
      REPO_URL in _provenance_text if REPO_URL else False, True)
check("5b  ...and does not name the earlier working name",
      "trialbridge-ai" in _provenance_text.lower(), False)


# ===========================================================================
# SECTION 6 -- README
# ===========================================================================
section("SECTION 6 -- README carries the identifier, the link and the badge")

_readme_text = open(_README, encoding="utf-8").read()
_readme_lines = _readme_text.splitlines()

# WHY THE IDENTIFIER IS IN THE BADGE'S ALT TEXT. README's prose writes the
# licence's NAME -- "PolyForm Noncommercial 1.0.0", with spaces, which is how
# LICENSE line 1 and the Licence section both write it -- and a shields.io
# static badge cannot carry the SPDX IDENTIFIER in its message verbatim, because
# a literal '-' is that URL's field separator and has to be doubled. So the
# machine-readable form lives in the alt text, which is also what a screen
# reader is handed. It is the one place in README that a rename must move.
_badge_lines = [ln for ln in _readme_lines
                if "img.shields.io/badge/" in ln and "license" in ln.lower()]

check("6a  README names the SPDX identifier somewhere",
      IDENT in _readme_text if IDENT else False, True)
check("6b  ...and links to the licence file itself",
      "](LICENSE)" in _readme_text, True)
check("6c  ...and carries exactly one static shields.io licence badge",
      len(_badge_lines), 1)
check("6d  ...whose link target is LICENSE",
      "](LICENSE)" in _badge_lines[0] if _badge_lines else False, True)


# ===========================================================================
# SECTION 7 -- THE SWEEP
# ===========================================================================
section("SECTION 7 -- no file reserves rights, and no file declares the "
        "non-standard-licence key")

_files, _binary_skipped, _pruned_venvs = sweep_text_files(_CODE_DIR)

check("7a  the sweep reached a real corpus (non-degeneracy)",
      len(_files) >= 100, True)
check("7b  no file carries a reservation notice",
      sorted(os.path.relpath(p, _CODE_DIR) for p, t in _files
             if mentions_reserved_notice(t)), [])
check("7c  no file DECLARES the non-standard-licence key",
      sorted(os.path.relpath(p, _CODE_DIR) for p, t in _files
             if declares_url_key(t)), [])

# PRINTED AND NOT GATED. The two readings differ by exactly the files that
# NAME the key in prose while declaring nothing -- today, CITATION.cff's own
# header, which explains why the key is gone. Reporting the difference is what
# keeps the narrower check honest: a reader can see what it chose not to gate.
_prose = sorted(os.path.relpath(p, _CODE_DIR) for p, t in _files
                if _URL_KEY in t and not declares_url_key(t))
print(f"  ({len(_files)} text files scanned, {_binary_skipped} skipped as "
      f"binary or undecodable)")
print(f"  (files NAMING the key in prose without declaring it, reported and "
      f"not gated: {_prose})")


# ===========================================================================
# SECTION 8 -- CONTROLS
# ===========================================================================
# Every control plants its defect into an IN-MEMORY copy and asserts the check
# goes the other way. Each is paired with the real file's own answer above, so
# a control that disagreed with everything would not read as one that works.
section("SECTION 8 -- every check above is shown to FAIL on a planted defect")

# --- a: CITATION.cff --------------------------------------------------------
_c = _citation_text.replace("license: PolyForm-Noncommercial-1.0.0",
                            "license: MIT")
check("8a  a swapped identifier is read as swapped",
      guarded(cff_top_level_scalars, _c).get("license"), "MIT")
check("8a  ...and the plant really changed the file (non-degeneracy)",
      _c != _citation_text, True)

_c = _citation_text.replace("cff-version: 1.2.0", "cff-version: 1.1.0")
check("8b  a downgraded cff-version is read as downgraded",
      guarded(cff_top_level_scalars, _c).get("cff-version"), "1.1.0")

_c = _citation_text + "\n" + _URL_KEY + ": https://example.invalid/terms\n"
check("8c  a reintroduced non-standard-licence KEY is seen as a key",
      _URL_KEY in guarded(cff_top_level_scalars, _c), True)
check("8c  ...and the key-anchored sweep predicate fires on it",
      guarded(declares_url_key, _c), True)
check("8c  ...while the header comment ALONE does not, which is the whole "
      "reason the predicate is anchored",
      guarded(declares_url_key, _citation_text), False)

_c = _citation_text + "\nlicense: MIT\n"
check("8d  a duplicated top-level key is reported",
      guarded(cff_duplicate_keys, _c), ["license"])

_c = "\n".join("  " + ln for ln in _citation_text.splitlines())
check("8e  a parser handed a file with no top-level key reports none, which "
      "is what check 1a exists to fail on",
      len(guarded(cff_top_level_scalars, _c)), 0)

# --- b: LICENSE -------------------------------------------------------------
_edited = bytearray(_license_bytes)
_edited[100:103] = b"XXX"
check("8f  a three-byte edit inside the licence body moves the digest",
      _sha256_bytes(bytes(_edited[:_PUBLISHED_BYTES])) == _PUBLISHED_SHA256,
      False)

_no_notice = _license_bytes[:_PUBLISHED_BYTES]
check("8g  a LICENSE with the required notice stripped fails the tail check",
      bool(_REQUIRED_NOTICE_RE.match(_no_notice[_PUBLISHED_BYTES:])), False)

_two_notices = _license_bytes + b"Required Notice: Copyright Someone Else\n"
check("8h  a SECOND notice line fails the one-line check",
      len([ln for ln in _two_notices[_PUBLISHED_BYTES:].split(b"\n")
           if ln.strip()]), 2)

_wrong_repo = _license_bytes.replace(
    b"https://github.com/ramyalsaffar/OncoTriage-Agent",
    b"https://github.com/ramyalsaffar/trialbridge-ai")
check("8i  a notice naming a stale repository fails the cross-check",
      (REPO_URL or "").encode("utf-8") in _wrong_repo[_PUBLISHED_BYTES:], False)

# --- c: pyproject.toml ------------------------------------------------------
_p = tomllib.loads(open(_PYPROJECT, encoding="utf-8").read()
                   .replace('license = { text = "PolyForm-Noncommercial-1.0.0" }',
                            'license = { text = "MIT" }'))
check("8j  a swapped pyproject licence no longer equals IDENT",
      _p["project"]["license"]["text"] == IDENT, False)

_p = tomllib.loads(open(_PYPROJECT, encoding="utf-8").read().replace(
    'authors = [{ name = "Ramy Alsaffar", email = "ramyalsaffar@gmail.com" }]',
    'authors = [{ name = "Ramy Alsaffar" }, { name = "Somebody Else" }]'))
check("8k  a second author is counted", len(_p["project"]["authors"]), 2)

# BOTH LEGAL FORMS, AND THE ONE THAT WOULD HAVE ABORTED. The middle case is
# the migration `pyproject.toml`'s own comment says is correct once setuptools
# clears 77; the reader must go on working through it rather than raising.
check("8l  the PEP 621 table form is read",
      guarded(pyproject_license_identifier,
              {"license": {"text": "PolyForm-Noncommercial-1.0.0"}}),
      "PolyForm-Noncommercial-1.0.0")
check("8l  ...and so is the PEP 639 string form this project plans to move to",
      guarded(pyproject_license_identifier,
              {"license": "PolyForm-Noncommercial-1.0.0"}),
      "PolyForm-Noncommercial-1.0.0")
check("8l  ...and neither raises; an unreadable shape is a named absence, "
      "which check 3a FAILS on rather than aborting under",
      [guarded(pyproject_license_identifier, v)
       for v in ({}, {"license": {}}, {"license": 7})],
      [None, None, None])

# --- d: Dockerfile ----------------------------------------------------------
_d = _dockerfile_text.replace(
    'org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0"',
    'org.opencontainers.image.licenses="MIT"')
check("8m  a swapped image licence label is read as swapped",
      guarded(dockerfile_labels, _d).get("org.opencontainers.image.licenses"),
      "MIT")

_d = _dockerfile_text.replace('org.opencontainers.image.title="OncoTriage Agent"',
                              'org.opencontainers.image.title="Clinical-Trial-Patient-Match"')
_lab = guarded(dockerfile_labels, _d)
check("8n  a REVERTED title puts the sibling directory's name back",
      "Clinical-Trial-Patient-Match" in " ".join(
          _lab.get(k, "") for k in _RETIRED_LABELS), True)

_d = _dockerfile_text.replace(
    'org.opencontainers.image.description="Oncology patient',
    'org.opencontainers.image.description="trialbridge -- oncology patient')
check("8o  a description naming the earlier working name is caught",
      "trialbridge" in " ".join(
          guarded(dockerfile_labels, _d).get(k, "") for k in _RETIRED_LABELS
      ).lower(), True)

# THE PARSER MUST READ INSTRUCTIONS AND NOT THE FILE. This Dockerfile's own
# header comments quote the label values they argue about; a whole-text search
# is satisfied by the prose. Planted here as a comment carrying a value the
# labels do not.
_d = ('# LABEL org.opencontainers.image.title="Clinical-Trial-Patient-Match"\n'
      + _dockerfile_text)
check("8p  a COMMENT quoting a retired value does not become a label",
      "Clinical-Trial-Patient-Match" in " ".join(
          guarded(dockerfile_labels, _d).get(k, "") for k in _RETIRED_LABELS),
      False)
check("8p  ...though the raw text a naive check would search does carry it",
      "Clinical-Trial-Patient-Match" in _d, True)

# --- e: PROVENANCE.md -------------------------------------------------------
_pr = _provenance_text.replace(
    "https://github.com/ramyalsaffar/OncoTriage-Agent",
    "https://github.com/ramyalsaffar/trialbridge-ai")
check("8q  a stale repository URL fails the verbatim check",
      (REPO_URL or "") in _pr, False)
check("8q  ...and is caught by name too", "trialbridge-ai" in _pr.lower(), True)

# --- f: README --------------------------------------------------------------
_r = "\n".join(ln for ln in _readme_lines
               if not ("img.shields.io/badge/" in ln and "license" in ln.lower()))
check("8r  a README with the badge removed has no badge line",
      len([ln for ln in _r.splitlines()
           if "img.shields.io/badge/" in ln and "license" in ln.lower()]), 0)

# 6a's CONTROL IS ABOUT 6a AND NOT ABOUT THE BADGE, deliberately. The obvious
# form -- "removing the badge line also removes IDENT" -- is TRUE today and is
# a landmine: it is a statement about the identifier appearing in exactly one
# place, so it goes red the day somebody legitimately writes the identifier
# into the Licence prose as well, with a message asserting something that is
# then false. A test that fails on an improvement to the thing it protects is
# the shape this project records having shipped twice (the compose grace pin's
# check 3c, and the call-mode pin's check 1c). The count is PRINTED instead.
_r_no_ident = _readme_text.replace(IDENT, "MIT") if IDENT else _readme_text
check("8r  a README that has lost the SPDX identifier fails check 6a",
      IDENT in _r_no_ident if IDENT else False, False)
print(f"  (README writes the identifier "
      f"{_readme_text.count(IDENT) if IDENT else 0} time(s); reported, not "
      f"gated -- check 6a asks only that it is written at all)")

_r = _readme_text.replace("](LICENSE)", "](https://example.invalid/LICENSE)")
check("8s  a badge pointed away from the licence file fails the link check",
      "](LICENSE)" in _r, False)

# --- g: the sweep -----------------------------------------------------------
check("8t  a reservation notice is caught wherever it is written",
      guarded(mentions_reserved_notice,
              "Copyright 2026 Somebody. " + _RESERVED_NEEDLE + "."), True)
check("8t  ...case-insensitively",
      guarded(mentions_reserved_notice, _RESERVED_NEEDLE.upper()), True)
check("8t  ...and an ordinary file is not reported (non-degeneracy)",
      guarded(mentions_reserved_notice, "Copyright 2026 Somebody."), False)
check("8u  the key predicate fires on an indented declaration too",
      guarded(declares_url_key, "authors:\n  " + _URL_KEY + ": https://x/"), True)
check("8u  ...and not on a prose mention mid-sentence",
      guarded(declares_url_key,
              "the " + _URL_KEY + " key is gone; see the header"), False)
# THE PRUNE, AND THE ONE PLACE THIS FILE CANNOT MAKE A CONTROL PURE. Every
# other control here is a different ARGUMENT to a pure function; this one is
# about a filesystem walk, and fabricating a virtualenv to prune would mean
# writing files, which this file promises it does not do. So the gate is
# stated AND its vacuity is measured: it is a real check exactly where the tree
# holds an environment, and it is vacuous where it does not -- which is every
# hosted runner, because `09- Testing/` is untracked and self-ignored. That is
# the landmine tests/test_dockerignore_exclusions.py records and answers with a
# SKIP; here the honest form is to print the count rather than to claim the
# gate fired. It is not a skip, because the check itself does run everywhere.
check("8v  no swept file lies inside a pruned virtualenv, so a third-party "
      "licence is never read as this repository's",
      sorted(p for p, _ in _files
             if any(p.startswith(v + os.sep) for v in _pruned_venvs)), [])
print(f"  ({len(_pruned_venvs)} virtualenv(s) pruned by marker: "
      f"{[os.path.relpath(v, _CODE_DIR) for v in _pruned_venvs]}; where this "
      f"list is empty -- every hosted runner -- check 8v is VACUOUS and says "
      f"nothing about the prune)")


# ===========================================================================
# SECTION 9 -- HYGIENE
# ===========================================================================
section("SECTION 9 -- nothing was written")

for _p in _READ_FILES:
    check(f"9a  {os.path.relpath(_p, _CODE_DIR)} is byte-identical after the run",
          _sha256_file(_p), _SHA_BEFORE[_p])

check("9b  ...and those are real digests, six of them, all different "
      "(non-degeneracy: a comparison of one file with itself would pass too)",
      len({v for v in _SHA_BEFORE.values()}) == len(_READ_FILES)
      and all(isinstance(v, str) and len(v) == 64 for v in _SHA_BEFORE.values()),
      True)


# ===========================================================================
section("SUMMARY")
print(f"  passed:  {_passed}")
print(f"  failed:  {_failed}")

if __name__ == "__main__":
    sys.exit(1 if _failed else 0)


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Sep 02 2026

@author: ramyalsaffar
"""
