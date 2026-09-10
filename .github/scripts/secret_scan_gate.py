# Secret Scan Gate
##################

"""Two engines over one range: every object in the git object database.

WHAT THIS GATES, AND WHY THE RANGE IS NOT A COMMIT RANGE
--------------------------------------------------------
The requirement is that no credential can ENTER this repository. The obvious
implementation is a diff of what a push introduced, and it is WRONG here --
provably, by measurement rather than by argument. Two shapes defeat it, and one
of them ALSO defeats the full-history log scan this repository already ships:

  AN EVIL MERGE. A merge commit may carry content that is in NEITHER parent
      (`git merge --no-commit`, then add a file, then commit). `git log -p`
      shows no diff for a merge by default, so a scanner built on `git log`
      never sees that content. MEASURED, in a four-commit scratch repository
      built for this: with a shape-faithful AWS key id and a shape-faithful
      Hugging Face token in an evil merge,

          gitleaks git --log-opts="--all --full-history"
              -> "3 commits scanned", "no leaks found", exit 0
          gitleaks dir over the same working tree
              -> 2 findings

      Note the commit count as well as the verdict: the merge commit was not
      scanned at all. A push-range scan is strictly weaker than the full-history
      scan that already misses this, so no range narrower than the object
      database can be defended.

  A FORCE-PUSH. `github.event.before` can name a commit that is gone, and the
      range `before..after` can skip commits entirely. An object walk asks a
      different question -- "what is in this repository NOW" -- which has no
      before, so there is nothing for a force-push to move.

So the range is the OBJECT DATABASE: every object `git cat-file
--batch-all-objects` reports, reachable or not. On a fresh CI clone that is
exactly the set a member of the public can clone. On a developer machine it is a
SUPERSET of that -- it also holds objects left by an amended or rebased commit,
which is the right direction to be wrong in for a scanner.

WHAT THIS COSTS, MEASURED ON THIS REPOSITORY (200 commits, 2,862 objects)
--------------------------------------------------------------------------
    gitleaks git --log-opts="--all --full-history"    15.08 MB   1.2 s
    this gate, both engines, whole object database   128.89 MB  ~17 s

The object walk reads about 8.5x more bytes, and the reason is that a log scan
reads DIFFS -- added lines only -- while this reads every version of every file
in full. The brief this was built from had those two figures the other way
round; they are stated here as measured.

BLOBS ARE READ WHOLE, NOT TO `config.S3_STAGING_SCAN_PREFIX_BYTES`
-------------------------------------------------------------------
The staging scanner reads a bounded 64 KiB prefix of each file, because it walks
~60 GB of third-party data and the bound is what makes that take seconds. That
bound is a STATED LIMIT, not a guarantee, and applying it here would open a hole
this repository is measurably inside: 63,977,491 of the 128,894,276 bytes in
this object database -- 49.6% -- lie beyond 64 KiB of their blob. Half of the
history would be unscanned. So `scan_bytes` is called on the whole blob.

TWO ENGINES, AND NEITHER SUBSUMES THE OTHER
--------------------------------------------
  gitleaks       ~170 provider rules with entropy floors and issued prefixes.
                 Finds a Stripe key this project has never heard of.
  oncotriage     nine content detectors AND a FILENAME layer. Measured on this
                 repository's object database: it reports 15 findings that
                 gitleaks reports ZERO of -- twelve historical
                 `docker-compose.yml` blobs carrying the 56-character Airflow
                 signing key (letters, underscores and hyphens, no digit, so it
                 clears no entropy floor gitleaks has) and three historical
                 blobs of a test file carrying a synthetic `sk-` sentinel.
                 gitleaks over the same 128.89 MB finds 6, all of them the two
                 false positives `.gitleaksignore` already argues about.

Both engines' findings are normalised onto one fingerprint and gated by one
accepted table.

THE FINGERPRINT IS KEYED ON THE BLOB OID, WHICH IS STRICTLY BETTER THAN
`.gitleaksignore`'S `commit:path:rule:line`
------------------------------------------------------------------------
`.gitleaksignore`'s own header records the cost of its form: "a rebase or a
filter-branch changes the commit hash and the entry stops matching". A blob OID
is the sha1 of the CONTENT, so an entry here

    * survives a rebase, a filter-branch and a path rename, because none of
      those changes the blob;
    * cannot ever suppress DIFFERENT content, because different content is a
      different OID. That is the property the staging allowlist buys by keying
      on (path, sha256), arrived at independently and for the same reason.

A STALE ACCEPTED ENTRY FAILS THE GATE
--------------------------------------
Exit 2, on the precedent `.github/scripts/audit_gate.py` and
`.github/scripts/trivyignore_staleness.py` both set: a dead exemption is re-read
by the next person as a live constraint. Because a blob OID is permanent, this
can only fire after a history rewrite -- which is exactly when the accepted
table needs re-reviewing.

NO FINDING EVER CARRIES MATCHED BYTES
--------------------------------------
The engine, the detector, the blob OID, a basename, and a byte offset or line
number. gitleaks is run with `--redact` so its own report holds none either.
A scanner that prints secrets to prove it found secrets has moved them into a
log file, and this one's output is a public CI log.

Run from terminal:
    python .github/scripts/secret_scan_gate.py                 # the object database
    python .github/scripts/secret_scan_gate.py --range staged  # what is staged now
    python .github/scripts/secret_scan_gate.py --emit-accepted # fingerprints to review

Exit codes:
    0 -- no unaccepted findings
    1 -- at least one unaccepted finding
    2 -- the accepted table has gone stale
    3 -- the scan could not be run, so nothing was established
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
# STDLIB SINCE 3.11, WHICH IS THE INTERPRETER BOTH THE IMAGE AND THIS JOB PIN.
# It is what lets --print-requirements read the project's own pins BEFORE
# anything is installed, which is the whole point of that flag: the CI step
# that installs from it cannot depend on a package it is about to install.
import tomllib


_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_SCRIPTS_DIR))

# THE NAME IS NOT `secret-scan-accepted.txt`, AND THAT WAS FOUND BY RUNNING
# THIS GATE OVER A CLONE THAT CONTAINED IT. The project scanner's FILENAME layer
# matches `(^|[._\-])secrets?([._\-]|$)`, so a table called
# `secret-scan-accepted.txt` is itself reported as "a filename naming a secret"
# -- and the only fingerprint that could suppress it is keyed on that file's own
# blob oid, which changes every time an entry is added. The suppression file
# would have had to suppress itself, and would have gone stale on every edit.
#
# THE SAME SHAPE `.gitleaksignore`'S HEADER RECORDS, THROUGH THE NAME RATHER
# THAN THE CONTENT. Its first draft quoted the line it suppressed and produced a
# finding about itself; this one would have been named the thing it holds a
# table about. The resolution is the same in both cases and the name is now the
# more accurate one anyway: the file holds FINGERPRINTS, and it holds no secret.
ACCEPTED_PATH = os.path.join(os.path.dirname(_SCRIPTS_DIR),
                             "scan-accepted-fingerprints.txt")

# The gitleaks build this repository pins. Named here as well as in the
# workflow so a local run and a CI run cannot disagree about which ruleset
# produced a fingerprint -- a rule renamed upstream changes every fingerprint
# it emits, and an accepted table keyed on the old name would then go stale
# without anybody understanding why.
GITLEAKS_VERSION = "8.30.1"

# gitleaks reports a finding it cannot attribute to a line as line 0; nothing
# else uses this. Kept named so the fingerprint format has no bare literal.
_NO_LINE = 0

# A FINGERPRINT'S FIRST FIELD IS A BLOB OID AND NOTHING ELSE.
#
# Forty lowercase hex characters, which is git's SHA-1 object name. That length
# is not a preference here: `basenames_by_oid` parses the binary tree format
# with `body[nul + 1:nul + 21]`, twenty RAW bytes, so this whole file already
# only works against a SHA-1 repository. Accepting a 64-character SHA-256 name
# here and nowhere else would be a half-wiring -- the parser would admit an
# entry the census can never produce, and the table would report it stale
# forever. Refusing it is the loud failure, and both move together on the day
# this file learns SHA-256.
_OID_RE = re.compile(r"^[0-9a-f]{40}$")


# WHAT MUST BE INSTALLED FOR THIS GATE TO IMPORT THE PROJECT'S SCANNER
# ---------------------------------------------------------------------
# The gate uses two PURE functions -- `scan_bytes` and `scan_filename` -- which
# read no configuration and touch no client. But they live in
# oncotriage/staging/secrets_scan.py, which imports `oncotriage.config` and
# `oncotriage.observability` at MODULE SCOPE, and config imports three
# third-party packages there. So importing the pure functions costs those
# imports, and on a runner that has not run `make install` the gate exits 3.
#
# MEASURED BY AST, NOT LISTED FROM MEMORY: the module-scope closure of
# oncotriage.staging.secrets_scan reaches exactly six package modules
# (`oncotriage`, `.settings`, `.paths`, `.observability`, `.config`,
# `.staging.secrets_scan`) and exactly four third-party tops -- `dotenv` from
# paths, and `httpx`, `openai`, `qdrant_client` from config. No torch, no
# transformers, no streamlit, no langgraph.
#
# THE VERSIONS ARE IMMATERIAL AND THAT IS AN ARGUMENT RATHER THAN A SHRUG:
# nothing from any of the four is CALLED on this code path. They are satisfied
# so that an `import` statement succeeds; the detectors are `re` over `bytes`.
# Pinning them here would create a second dependency list beside
# pyproject.toml, which this project has already deleted once.
#
# THE LIST IS NOT RETYPED IN THE WORKFLOW. `--print-requirements` is what the
# CI step installs from, so there is one owner, and section 10 of
# tests/test_secret_scan_gate.py RE-DERIVES the closure by AST and fails when a
# new module-scope import appears in that chain. A list that rots silently is
# the failure mode; this one rots loudly.
SCANNER_IMPORT_REQUIREMENTS = {
    # import name    distribution name
    "dotenv":        "python-dotenv",
    "httpx":         "httpx",
    "openai":        "openai",
    "qdrant_client": "qdrant-client",
}

PYPROJECT_PATH = os.path.join(_REPO_ROOT, "pyproject.toml")


class RequirementsUnavailable(RuntimeError):
    """The pinned requirements could not be derived, so nothing was printed.

    A RuntimeError subclass on the same precedent as ScanUnavailable below: a
    broad ``except ValueError`` must not be able to turn "I could not read the
    dependency list" into "here is the dependency list".
    """


def _declared_pins(pyproject_path=None):
    """{distribution name: the exact requirement string pyproject.toml declares}.

    Keys are normalised the way PEP 503 normalises them, so `python_dotenv`,
    `python-dotenv` and `Python-Dotenv` are one key.
    """
    path = PYPROJECT_PATH if pyproject_path is None else pyproject_path
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except Exception as exc:
        raise RequirementsUnavailable(
            f"could not read the project's dependency list at {path}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    declared = data.get("project", {}).get("dependencies")
    if not declared:
        raise RequirementsUnavailable(
            f"{path} declares no project.dependencies, so there is nothing to "
            f"pin against. The gate will not fall back to unpinned names: an "
            f"unpinned install that looks pinned is the state this derivation "
            f"exists to remove."
        )

    pins = {}
    for requirement in declared:
        # The distribution name is everything before the first character that
        # can begin a version specifier, an extra or an environment marker.
        # No packaging.requirements here: this function must work BEFORE
        # anything is installed, so it is stdlib only.
        name = re.split(r"[<>=!~\[;\s]", requirement.strip(), maxsplit=1)[0]
        if name:
            pins[re.sub(r"[-_.]+", "-", name).lower()] = requirement.strip()
    return pins


def scanner_requirements(pyproject_path=None):
    """The pip arguments the CI step installs from, PINNED to this project's own.

    WHY THE PINS ARE HERE AT ALL, AND WHAT CHANGED. Until 2026-09-08 this
    returned the four bare distribution names, under an argument that is half
    right and was half wrong: nothing from any of the four is CALLED on this
    code path -- they are satisfied so that an ``import`` statement succeeds,
    and the detectors are ``re`` over ``bytes`` -- so the VERSIONS are
    immaterial to what the gate DECIDES. What that argument missed is what the
    versions decide about whether the gate RUNS. An unpinned install resolves
    whatever is newest on the day, so an upstream release that fails to import
    on the container's Python, or that moves a module-scope symbol
    ``oncotriage/config.py`` reads, turns this gate into exit 3 -- "the scan
    could not run" -- with NOTHING IN THIS REPOSITORY HAVING CHANGED, and the
    only remedy is an emergency edit to a security gate.

    IT IS STILL NOT A SECOND DEPENDENCY LIST, which is the thing the old
    argument was protecting and is right to protect. The pins are READ from
    ``pyproject.toml`` -- the one owner this project already declares, and the
    one it deleted ``requirements/requirements.txt`` to establish. Nothing is
    retyped, so nothing can drift; a pin bumped there moves this install with
    no edit here.

    ``httpx`` IS DELIBERATELY EMITTED BARE, and that is the correct answer
    rather than a gap. It is NOT a declared dependency of this project -- it
    arrives transitively, and ``oncotriage/config.py`` imports it at module
    scope on the strength of that. ``openai==1.99.9`` declares
    ``httpx<1,>=0.23.0``, so pinning it here would be inventing a constraint
    the project does not make, and would be WEAKER than what the resolver
    already does: openai's own declared range is the one openai was tested
    against. A name pyproject declares nothing for is emitted as the bare
    distribution name and the resolver honours whatever depends on it.

    Raises:
        RequirementsUnavailable: the dependency list could not be read, or
            declares nothing. It refuses rather than falling back to the four
            bare names, because an unpinned install that LOOKS pinned is
            invisible, and invisible is the whole failure mode being removed.
    """
    pins = _declared_pins(pyproject_path)
    out = []
    for distribution in sorted(SCANNER_IMPORT_REQUIREMENTS.values()):
        key = re.sub(r"[-_.]+", "-", distribution).lower()
        out.append(pins.get(key, distribution))
    return out


class ScanUnavailable(RuntimeError):
    """The scan could not be performed, so nothing has been established.

    A RuntimeError subclass on this project's standing precedent
    (UnknownModelPricingError, IndexVerificationError, SecretsRefusal): a broad
    ``except ValueError`` must not be able to turn "I could not look" into "I
    looked and it was clean".
    """


# ---------------------------------------------------------------------------
# THE PROJECT'S OWN SCANNER
#
# Imported lazily and from the repository root, so this script can still print
# --help, and still REFUSE with a named error, on a checkout where the package
# is not installed. The two functions used are pure and need no data tree --
# verified by importing them with ONCOTRIAGE_MAIN_PATH pointed at a directory
# that does not exist.
#
# THEY ARE REUSED, NOT RETYPED. Retyping nine detectors here would create the
# second copy this project keeps removing: nothing fails when two copies
# disagree, and the only symptom is a gate that stops catching what the staging
# refusal catches.
# ---------------------------------------------------------------------------

def load_project_scanner():
    """Return (scan_bytes, scan_filename) from oncotriage.staging.secrets_scan."""
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    try:
        from oncotriage.staging.secrets_scan import scan_bytes, scan_filename
    except Exception as exc:                       # pragma: no cover - refusal
        raise ScanUnavailable(
            f"could not import oncotriage.staging.secrets_scan from "
            f"{_REPO_ROOT}: {type(exc).__name__}: {exc}")
    return scan_bytes, scan_filename


# ---------------------------------------------------------------------------
# GIT PLUMBING
# ---------------------------------------------------------------------------

def _git(repo, args, binary=False):
    proc = subprocess.run(["git", "-C", repo] + args,
                          capture_output=True)
    if proc.returncode != 0:
        raise ScanUnavailable(
            f"git {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


class BatchReader:
    """One long-lived `git cat-file --batch`, so 1,634 blobs cost one process.

    A subprocess per object would be 1,634 forks; this is one. The protocol is
    `<oid> <type> <size>\\n<size bytes>\\n`, and the trailing newline is read and
    discarded rather than being left to desynchronise the next read -- which is
    the one way this loop can go wrong, and it goes wrong silently, returning
    the previous object's tail as the next object's head.
    """

    def __init__(self, repo):
        self._proc = subprocess.Popen(
            ["git", "-C", repo, "cat-file", "--batch"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def read(self, oid):
        self._proc.stdin.write((oid + "\n").encode())
        self._proc.stdin.flush()
        header = self._proc.stdout.readline().decode("utf-8", "replace").split()
        if len(header) != 3:
            raise ScanUnavailable(
                f"git cat-file --batch did not recognise {oid}: "
                f"{' '.join(header) or '<empty>'}")
        size = int(header[2])
        body = b""
        while len(body) < size:                    # read() may return short
            chunk = self._proc.stdout.read(size - len(body))
            if not chunk:
                raise ScanUnavailable(
                    f"git cat-file --batch truncated {oid} at {len(body)} of "
                    f"{size} bytes")
            body += chunk
        self._proc.stdout.read(1)                  # the trailing newline
        return body

    def close(self):
        try:
            self._proc.stdin.close()
        finally:
            self._proc.wait()


def object_census(repo):
    """(blobs, trees) from the WHOLE object database, reachable or not.

    `--batch-all-objects` rather than `rev-list --objects --all`, and the
    difference is the point: the second walks only what a ref reaches, so an
    object left behind by an amended or rebased commit is invisible to it. On a
    clone driven by the transport the two sets are usually identical, because
    the fetch is what refs reach; on a developer machine this one is a superset,
    which is where a hook wants to be looking. NOT on a LOCAL clone, measured:
    `git clone <path>` hardlinks the whole object directory, so unreferenced
    objects come with it and the two sets differ there too.
    """
    out = _git(repo, ["cat-file", "--batch-all-objects",
                      "--batch-check=%(objecttype) %(objectname) %(objectsize)"])
    blobs, trees = [], []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        kind, oid, size = parts
        if kind == "blob":
            blobs.append((oid, int(size)))
        elif kind == "tree":
            trees.append(oid)
    return blobs, trees


def basenames_by_oid(repo, trees):
    """oid -> {basename}, from EVERY tree object in the database.

    NOT from `git rev-list --objects --all`, and that is a measured decision
    rather than a preference. That command reports a blob under exactly ONE
    path, whichever it happens to reach first. Demonstrated in a scratch
    repository: one blob committed at `a.txt`, `dup1.txt` and `.env` is listed
    by `rev-list --objects` under a single name, so whether the FILENAME layer
    ever sees `.env` is decided by traversal order. Walking every tree finds all
    three, which is what a filename detector needs.

    The tree format is binary -- `<mode> <name>\\0<20 raw bytes of oid>` -- so it
    is parsed here rather than shelled out to `git ls-tree` once per tree.
    """
    reader = BatchReader(repo)
    names = {}
    try:
        for tree_oid in trees:
            body = reader.read(tree_oid)
            i = 0
            while i < len(body):
                space = body.index(b" ", i)
                nul = body.index(b"\0", space)
                name = body[space + 1:nul].decode("utf-8", "replace")
                child = body[nul + 1:nul + 21].hex()
                names.setdefault(child, set()).add(name)
                i = nul + 21
    finally:
        reader.close()
    return names


def staged_entries(repo):
    """[(oid, basename)] for the content this commit would introduce.

    Against HEAD when there is one, against the EMPTY TREE when there is not --
    so the hook works on the very first commit, which is otherwise the one
    commit `git diff --cached` refuses. The empty tree is obtained from git
    rather than written out as `4b825dc6...`, because that literal is the SHA-1
    empty tree and a repository initialised with `--object-format=sha256` has a
    different one.

    `--diff-filter=ACMR` -- added, copied, modified, renamed. A DELETION
    introduces no content, and scanning the pre-image of a file somebody is
    REMOVING would refuse the commit that takes a secret out of the tree.
    """
    try:
        _git(repo, ["rev-parse", "--verify", "HEAD"])
        base = "HEAD"
    except ScanUnavailable:
        base = _git(repo, ["hash-object", "-t", "tree", os.devnull]).strip()

    raw = _git(repo, ["diff-index", "--cached", "--raw", "-z",
                      "--diff-filter=ACMR", base], binary=True)
    # -z output alternates: ":<meta>\0<path>\0" (and two paths for R/C).
    fields = raw.split(b"\0")
    entries = []
    i = 0
    while i < len(fields):
        meta = fields[i]
        if not meta.startswith(b":"):
            i += 1
            continue
        parts = meta.decode("utf-8", "replace").split()
        # :<srcmode> <dstmode> <srcsha> <dstsha> <status>
        dst_oid, status = parts[3], parts[4]
        # A rename or a copy carries TWO paths; the destination is the second.
        take = 2 if status[0] in ("R", "C") else 1
        path = fields[i + take].decode("utf-8", "replace")
        i += take + 1
        # AN ALL-ZERO OID IS NOT A BLOB. git writes one for an UNMERGED entry
        # (a conflict), and `git cat-file --batch` answers "missing" for it --
        # which this module turns into ScanUnavailable and exit 3. That would
        # block a commit during a merge conflict with a message about the
        # scanner rather than about the conflict, so it is skipped: an unmerged
        # path has no staged content for this range to be about, and git itself
        # refuses the commit until it is resolved.
        if set(dst_oid) == {"0"}:
            continue
        entries.append((dst_oid, os.path.basename(path)))
    return entries


# ---------------------------------------------------------------------------
# REACHABILITY -- WHICH REMEDY A REFUSAL IS ALLOWED TO NAME
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS. `object_census` walks `--batch-all-objects`, so the gate sees
# objects no ref reaches -- which is the point of that choice and is what lets a
# force-push residue be refused. But the refusal MESSAGE offered one remedy for
# every finding: "add the fingerprint to the accepted table". For an object no
# ref reaches that remedy CARRIES A RISK THE MESSAGE DID NOT NAME, and the
# measurement behind it is recorded in BLOCK 5 of the accepted table itself: a
# fingerprint matches only where that object is present, so a checkout that
# lacks it reports the entry under ACCEPTED TABLE IS STALE -- exit 2.
#
# WHICH CHECKOUTS LACK IT IS NOT SOMETHING THIS GATE ESTABLISHES, and an earlier
# version of this comment and of the message both said it did. They asserted
# that a fresh clone would not have such an object. MEASURED, and false for
# three of the four clone forms tried: `git clone <local path>` hardlinks the
# whole object directory and the unreferenced blob came with it, as did
# `--no-hardlinks` and `--depth=1`; only a clone forced through the transport
# (`file://`, or a network remote) filtered by reachability. So the risk is
# real and CONDITIONAL, the message states it as one, and the deliberate
# non-entry in BLOCK 5 rests on the case that was actually inspected rather
# than on a universal.
#
# WHAT THIS PROBE CAN AND CANNOT ESTABLISH, MEASURED BOTH WAYS RATHER THAN
# ASSUMED, AND IT IS NARROWER THAN THIS COMMENT USED TO CLAIM. It said a
# reachable object "is in every clone" and an unreachable one "is in no clone".
# Both are false, and both were refuted in scratch repositories:
#
#   * an object reachable from a LOCAL ref may be absent from an existing clone
#     -- committed in `origin` after `worker` was cloned, it is listed by
#     `rev-list --objects --all` there and is not in `worker`'s database at all;
#
#   * an object UNREACHABLE here may be reachable somewhere else -- the same
#     content, the same oid, unreachable in one repository (`--all --reflog
#     --indexed-objects` does not list it) and reachable from a branch in
#     another;
#
#   * and an object unreachable here may be PRESENT and still unreachable in a
#     fresh clone of this repository -- `git clone <local path>` hardlinks the
#     object directory rather than fetching by reachability, so the
#     unreferenced blob arrives with it. Three of four clone forms carried it.
#
# So this probe answers exactly one question -- IS THIS OBJECT REACHED BY THE
# INSPECTED LOCAL REFS, REFLOGS OR INDEX -- and every sentence the refusal
# prints is held to that. What any other repository contains is not something a
# local probe can decide, and a message that claimed it would be inviting an
# irreversible cleanup on the strength of a fact nobody established.
#
# SO A REFUSAL MUST KNOW WHICH IT IS, AND MUST VERIFY IT RATHER THAN INFER IT.
# "No basename" is the symptom that first suggests an unreached blob (no tree
# names it), and it is NOT evidence: `materialise` gives a nameless blob a
# synthetic name, and a blob `git add`ed and never committed is reached by the
# INDEX and named by no TREE. Guessing from the symptom would name a deletion
# remedy for content that is about to be committed, which is the one direction
# this must never fail in.

REACHABILITY_REACHABLE = "reachable"
REACHABILITY_UNREACHABLE = "unreachable"
REACHABILITY_UNVERIFIED = "unverified"

# CLOSED, and a caller may branch on it exhaustively. `unverified` is a MEMBER
# rather than a fall-through: the natural way to write this check is
# `if oid not in reachable: unreachable`, and then a probe that could not run
# reports EVERY finding as unreached and recommends deleting content nobody
# established anything about.
#
# THREE STATES, AND THE REFUSAL MUST PRINT THREE HEADINGS. The first version of
# that message had two -- it partitioned on `== UNREACHABLE` and printed
# everything else under a heading claiming the repository REACHES it, so an
# `unverified` finding was reported as a verified reachable one. The
# classification was three-state and the operator's reading of it was not, which
# is worse than a missing state: it turns "I could not look" into a positive
# claim. `main` partitions on all three by name and folds anything outside the
# vocabulary into `unverified`, so the printed sections are TOTAL.
REACHABILITY_STATES = (REACHABILITY_REACHABLE,
                       REACHABILITY_UNREACHABLE,
                       REACHABILITY_UNVERIFIED)

# THE PROBE IS THE WIDEST ONE GIT OFFERS, AND EVERY FLAG IS LOAD-BEARING.
#
#   --all               every ref, including remote-tracking refs as this
#                       checkout last saw them. A clone driven by the transport
#                       fetches what refs reach, so an object a ref reaches is
#                       the one most likely to exist outside this checkout --
#                       which is why the accepted table is the right instrument
#                       for it. NOT a guarantee in EITHER direction, both
#                       measured: a commit made here and not pushed is
#                       ref-reachable and was absent from a clone taken before
#                       it, and a LOCAL clone carries unreferenced objects that
#                       no ref reaches at all.
#   --reflog            an object a local reflog entry still pins. `git prune`
#                       will not touch it, so a message naming plain expiry as
#                       the remedy would be false for it. Reported REACHABLE,
#                       which is the conservative answer: the ordinary message
#                       says nothing untrue, and no deletion is recommended for
#                       content a local reference still points at.
#   --indexed-objects   the index. MEASURED, in a scratch repository: a blob
#                       that is `git add`ed and not committed is listed by this
#                       flag and by no other, so the `staged` range -- whose
#                       whole subject is content about to enter history -- is
#                       covered and its findings are classified REACHABLE. That
#                       measurement is why this module needs no special case
#                       for that range: a hook must never tell somebody to
#                       prune what they have just staged, and here it cannot.
#
# The three together are what BLOCK 5's own inspection used, and the remedy that
# block names (`git prune --expire=now`) is true of an object reached by none of
# them and of no other object.
_REACHABILITY_PROBE = ("rev-list", "--objects", "--all", "--reflog",
                       "--indexed-objects")


def reachable_object_names(repo):
    """The set of object names reachable from any ref, reflog entry or index.

    `rev-list --objects` prints `<oid>` for a commit and `<oid> <path>` for a
    blob or a tree, and a path may contain a space -- so the split is on the
    FIRST space only. Filtered through `_OID_RE` for the reason recorded there:
    this whole module is SHA-1-only by construction (`basenames_by_oid` reads
    twenty raw bytes), so a name of any other length is not a name this gate can
    be about. On a SHA-256 repository that filter empties the set, which
    `classify_reachability` reads as UNVERIFIED -- the safe direction, and the
    same answer the rest of this file gives such a repository.
    """
    out = _git(repo, list(_REACHABILITY_PROBE))
    names = set()
    for line in out.splitlines():
        oid = line.split(" ", 1)[0]
        if _OID_RE.match(oid):
            names.add(oid)
    return names


def classify_reachability(repo, oids):
    """(oid -> one of REACHABILITY_STATES, note). NEVER RAISES.

    This runs on the refusal path, where the gate has already established that
    it must refuse; its only job is to decide which remedy the message names. A
    probe that fails must therefore not convert a refusal into exit 3, so
    `ScanUnavailable` is caught here and nowhere else in this module -- the
    finding is still refused, and the message says the classification could not
    be made.

    THE EMPTY-RESULT GUARD IS THE LOAD-BEARING HALF. A probe that answers
    nothing -- an empty repository, a git that did not understand a flag, a
    SHA-256 object format -- would otherwise report every finding as unreached
    and recommend deleting content nobody established anything about. An empty
    set is read as "not established" rather than as "nothing is reachable",
    which is the one reading that cannot be wrong in the dangerous direction.

    AND THE CALLER MUST PRINT THAT STATE UNDER ITS OWN HEADING. Returning
    `unverified` and then printing it beside the reachable findings is the same
    defect one layer up, and it shipped once: see the note at
    `REACHABILITY_STATES`.
    """
    oids = list(oids)
    try:
        reachable = reachable_object_names(repo)
    except ScanUnavailable as exc:
        # WHITESPACE-COLLAPSED, BECAUSE THE NOTE IS PRINTED INSIDE AN INDENTED
        # BLOCK. `_git` embeds git's own stderr, which is not guaranteed to be
        # one line -- and a newline in there breaks the layout of the two
        # places this string is printed, including the header of a refusal.
        return ({oid: REACHABILITY_UNVERIFIED for oid in oids},
                " ".join(f"the reachability probe could not run "
                         f"({exc})".split()))
    if not reachable:
        return ({oid: REACHABILITY_UNVERIFIED for oid in oids},
                "the reachability probe listed no objects at all, so its "
                "answer establishes nothing")
    states = {}
    for oid in oids:
        states[oid] = (REACHABILITY_REACHABLE if oid in reachable
                       else REACHABILITY_UNREACHABLE)
    return states, f"{len(reachable):,} object(s) reachable"


# ---------------------------------------------------------------------------
# FINDINGS AND FINGERPRINTS
# ---------------------------------------------------------------------------

class Finding:
    """One reason a commit or a repository may not be published.

    Carries no secret material -- see this module's docstring. Deliberately not
    a dataclass: check 2i of tests/test_package_invariants.py pins the exact
    decorator list of every definition in the package, and this file follows the
    same rule so the two cannot drift apart in style.
    """

    __slots__ = ("oid", "engine", "detector", "locator", "basename", "note")

    def __init__(self, oid, engine, detector, locator, basename, note):
        self.oid = oid
        self.engine = engine
        self.detector = detector
        self.locator = locator
        self.basename = basename
        self.note = note

    def fingerprint(self):
        return f"{self.oid}:{self.engine}:{self.detector}:{self.locator}"

    def describe(self):
        where = f"as {self.basename}" if self.basename else "(no basename)"
        return (f"{self.fingerprint()}\n"
                f"        {self.engine}/{self.detector}: {self.note} "
                f"{where}")


def parse_fingerprint(text):
    """Split on the FIRST three colons only, and require the OID to be one.

    The locator is a byte offset or a line number for a content finding and a
    BASENAME for a filename finding, and a basename may legally contain a colon
    on every filesystem this runs on. Splitting on every colon would silently
    truncate such an entry into one that matches nothing, which is a suppression
    that stops suppressing without saying so.

    THE OID IS VALIDATED AND THE COLON COUNT ALONE IS NOT ENOUGH, which was
    found by running rather than by reading. Without the `_OID_RE` test this
    function answers "yes, a fingerprint" for ANY line carrying three colons,
    and two consumers read it:

      * `read_accepted` raises on a line this rejects. That refusal is the only
        validation the accepted table has, so with the colon count alone a
        mistyped entry -- a pasted log line, a truncated oid, a note somebody
        wrote with a timestamp in it -- became a live table ENTRY that matches
        nothing, and the gate then reported it as STALE. A table whose
        malformed lines arrive as staleness sends an operator to delete a real
        exemption to quiet a typo.

      * tests/test_secret_scan_gate.py harvests `--emit-accepted` output
        through it, and that harvest reads stdout AND stderr together (on
        purpose: check 3g requires the planted values to appear in NEITHER
        stream). On a hosted x86_64 runner, importing this project's scanner
        pulls qdrant_client -> fastembed -> onnxruntime, which writes a
        device-discovery warning to stderr:

            2026-08-31 02:07:33.857 [W:onnxruntime:Default, dev.cc:146 ...] ...

        Three colons before the first space. The colon count alone harvested it
        as a fingerprint, wrote it into the accepted table under test, and the
        verify run reported it stale -- so check 4f failed on Linux while the
        gate it tests was working correctly. The machine emitting that warning
        is the only reason it was not seen on the development machine.

    A fingerprint is `<oid>:<engine>:<detector>:<locator>` and the first field
    is a git object name, so that is what is checked. The other three are
    deliberately NOT constrained: an engine or a detector renamed upstream must
    arrive as a STALE entry naming itself, which is the signal the staleness
    check exists to give, and not as a parse failure that names the table.
    """
    parts = text.split(":", 3)
    if len(parts) != 4:
        return None
    if not _OID_RE.match(parts[0]):
        return None
    return tuple(parts)


# ---------------------------------------------------------------------------
# ENGINE 1 -- THE PROJECT'S OWN SCANNER
# ---------------------------------------------------------------------------

def scan_with_project_scanner(reader, blobs, names):
    """Content findings on whole blobs, filename findings on every basename."""
    scan_bytes, scan_filename = load_project_scanner()
    findings, bytes_read = [], 0

    for oid, _size in blobs:
        body = reader.read(oid)
        bytes_read += len(body)
        seen = sorted(names.get(oid, ()))
        shown = seen[0] if seen else ""
        for detector, offset, length in scan_bytes(body):
            findings.append(Finding(
                oid, "oncotriage", detector, str(offset), shown,
                f"content match, {length} bytes at offset {offset}"))
        for basename in seen:
            for detector in scan_filename(basename):
                findings.append(Finding(
                    oid, "oncotriage", detector, basename, basename,
                    "filename match"))
    return findings, bytes_read


# ---------------------------------------------------------------------------
# ENGINE 2 -- GITLEAKS, OVER THE SAME OBJECT SET
#
# gitleaks has no mode that reads a blob from a stream, and one `gitleaks stdin`
# per blob would be 1,634 process launches. So the objects are MATERIALISED into
# a temporary directory, one directory per blob named by its OID, holding the
# blob under its own basename:
#
#     <tmp>/<oid>/<basename>
#
# The basename is the real one, unmodified, because gitleaks carries rules and
# allowlists that key on a path -- renaming `queries.py` to `<oid>__queries.py`
# would change which of them apply. The OID is recovered from the parent
# directory, so the fingerprint does not depend on the temp path.
#
# THE TEMPORARY DIRECTORY IS REMOVED IN A `finally`. It holds every historical
# version of every file, which on a public repository is public anyway -- but on
# a developer machine it is also every blob from an amended commit, and leaving
# that on disk is a second copy of something the operator may be in the middle
# of removing.
# ---------------------------------------------------------------------------

def materialise(reader, blobs, names, out_dir):
    """Write each blob to <out_dir>/<oid>/<basename>. Returns the count."""
    written = 0
    for oid, _size in blobs:
        body = reader.read(oid)
        seen = sorted(names.get(oid, ()))
        if not seen:
            # A blob in no tree is still a blob somebody can fetch by SHA on
            # GitHub. It gets the OID as its name; there is no basename for the
            # filename layer to judge, which is reported rather than guessed.
            seen = [oid]
        target_dir = os.path.join(out_dir, oid)
        os.makedirs(target_dir, exist_ok=True)
        for basename in seen:
            # A basename from a tree cannot contain "/" (git forbids it) but it
            # CAN be "." or ".."; both are rejected rather than written, because
            # os.path.join would then escape the temp directory.
            if basename in (".", "..") or "/" in basename or "\\" in basename:
                basename = "sanitised-" + hashlib.sha256(
                    basename.encode("utf-8", "replace")).hexdigest()[:16]
            with open(os.path.join(target_dir, basename), "wb") as handle:
                handle.write(body)
            written += 1
    return written


def gitleaks_binary():
    """The gitleaks executable, or None. ONE_GITLEAKS env var overrides PATH."""
    override = os.environ.get("GITLEAKS_BIN")
    if override:
        return override if os.path.exists(override) else None
    return shutil.which("gitleaks")


def scan_with_gitleaks(binary, scan_dir, report_path):
    """Findings from `gitleaks dir`, normalised onto the blob OID.

    `--exit-code 0`: this function reports and does not decide. The accepted
    table is the only thing that decides, and it decides for BOTH engines, so
    letting gitleaks set the exit status would give one engine a second,
    invisible gate with its own rules.
    """
    proc = subprocess.run(
        [binary, "dir", "--no-banner", "--redact",
         "--report-format", "json", "--report-path", report_path,
         "--exit-code", "0", scan_dir],
        capture_output=True)
    if proc.returncode != 0:
        raise ScanUnavailable(
            f"gitleaks exited {proc.returncode}: "
            f"{proc.stderr.decode('utf-8', 'replace').strip()[:400]}")
    if not os.path.exists(report_path):
        raise ScanUnavailable(f"gitleaks wrote no report at {report_path}")
    with open(report_path, "r", encoding="utf-8") as handle:
        raw = handle.read().strip()
    payload = json.loads(raw) if raw else []

    findings = []
    for entry in payload:
        path = entry.get("File", "")
        relative = os.path.relpath(path, scan_dir)
        parts = relative.split(os.sep)
        oid = parts[0] if parts else ""
        basename = parts[-1] if parts else ""
        rule = entry.get("RuleID", "unknown-rule")
        line = entry.get("StartLine", _NO_LINE)
        findings.append(Finding(
            oid, "gitleaks", rule, str(line), basename,
            f"{rule} at line {line}"))
    return findings


# ---------------------------------------------------------------------------
# THE ACCEPTED TABLE
# ---------------------------------------------------------------------------

def read_accepted(path):
    """fingerprint -> the comment block above it. Blank lines end a block."""
    if not os.path.exists(path):
        return {}
    accepted, comment = {}, []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                comment = []
                continue
            if stripped.startswith("#"):
                text = stripped.lstrip("#").strip()
                # A rule of "=" or "-" is a separator, not a reason. Keeping it
                # would make the grouped summary print eighty equals signs where
                # the reason should be, which is how a summary that exists to be
                # read stops being read.
                if text and set(text) <= set("=-_"):
                    continue
                comment.append(text)
                continue
            if parse_fingerprint(stripped) is None:
                raise ScanUnavailable(
                    f"{path}: not a fingerprint "
                    f"(<oid>:<engine>:<detector>:<locator>, where <oid> is the "
                    f"40 lowercase hex characters of a git blob name): "
                    f"{stripped}")
            accepted[stripped] = " ".join(comment) or "(no reason recorded)"
    return accepted


# ---------------------------------------------------------------------------
# THE SCAN
# ---------------------------------------------------------------------------

def run_scan(repo, scan_range, binary, keep_dir=None):
    """Both engines over one range. Returns (findings, stats)."""
    started = time.time()
    reader = BatchReader(repo)
    try:
        if scan_range == "objects":
            blobs, trees = object_census(repo)
            names = basenames_by_oid(repo, trees)
        elif scan_range == "staged":
            entries = staged_entries(repo)
            names = {}
            for oid, basename in entries:
                names.setdefault(oid, set()).add(basename)
            blobs = [(oid, -1) for oid in sorted(names)]
        else:                                       # pragma: no cover - argparse
            raise ScanUnavailable(f"unknown range {scan_range!r}")

        findings, bytes_read = scan_with_project_scanner(reader, blobs, names)

        gitleaks_ran = False
        if binary:
            scratch = keep_dir or tempfile.mkdtemp(prefix="secret-scan-objects-")
            try:
                # TWO DIRECTORIES, NOT ONE, AND THE SEPARATION IS THE POINT.
                # The report is written by the same gitleaks invocation that
                # walks `blobs/`, so a report inside the scanned tree is a file
                # that may or may not be scanned depending on when gitleaks
                # creates it -- and if it were, its `File` would parse as a blob
                # oid of "_gitleaks-report.json" and produce a finding about the
                # report rather than about the repository.
                blob_dir = os.path.join(scratch, "blobs")
                os.makedirs(blob_dir, exist_ok=True)
                materialise(reader, blobs, names, blob_dir)
                report = os.path.join(scratch, "gitleaks-report.json")
                findings += scan_with_gitleaks(binary, blob_dir, report)
                gitleaks_ran = True
            finally:
                if keep_dir is None:
                    shutil.rmtree(scratch, ignore_errors=True)
    finally:
        reader.close()

    stats = {
        "range": scan_range,
        "blobs": len(blobs),
        "bytes_read": bytes_read,
        "gitleaks_ran": gitleaks_ran,
        "seconds": time.time() - started,
    }
    return findings, stats


# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Two secret-scanning engines over the git object database.")
    parser.add_argument("--repo", default=_REPO_ROOT,
                        help="repository to scan (default: this checkout)")
    parser.add_argument("--range", dest="scan_range", default="objects",
                        choices=("objects", "staged"),
                        help="objects: the whole object database (the CI gate). "
                             "staged: what this commit would introduce (the hook).")
    parser.add_argument("--accepted", default=ACCEPTED_PATH,
                        help="accepted-fingerprint table")
    parser.add_argument("--emit-accepted", action="store_true",
                        help="print every finding as a fingerprint line and "
                             "exit 0, for review before pasting into the table. "
                             "It prints; it never writes the table.")
    parser.add_argument("--print-requirements", action="store_true",
                        help="print the pip requirements this gate needs in "
                             "order to import the project's scanner, space "
                             "separated, and exit 0. PINNED to pyproject.toml's "
                             "own declarations, read at run time -- a name it "
                             "declares nothing for (httpx, which arrives "
                             "transitively through openai) is printed bare. The "
                             "CI step installs from this rather than repeating "
                             "the list in YAML. Exits 3 if the dependency list "
                             "cannot be read, rather than falling back to "
                             "unpinned names.")
    parser.add_argument("--require-gitleaks", action="store_true",
                        help="exit 3 when gitleaks is not on PATH, instead of "
                             "running the project scanner alone and saying so.")
    args = parser.parse_args(argv)

    if args.print_requirements:
        # EXIT 3, the same code every other "the scan could not run" path uses.
        # The CI step is `pip install $(... --print-requirements)`: a refusal
        # here prints nothing on stdout, so pip is invoked with no arguments
        # and fails loudly on its own -- and the step carries `set -euo
        # pipefail` so the substitution's own non-zero exit is what fails
        # first. Either way the operator sees this message rather than a gate
        # that quietly installed nothing and then exited 3 about the scanner.
        try:
            print(" ".join(scanner_requirements()))
        except RequirementsUnavailable as exc:
            print(f"COULD NOT DERIVE THE INSTALL REQUIREMENTS: {exc}",
                  file=sys.stderr)
            return 3
        return 0

    binary = gitleaks_binary()
    if binary is None and args.require_gitleaks:
        print("SECRET SCAN COULD NOT RUN: gitleaks is not on PATH and "
              "--require-gitleaks was given.", file=sys.stderr)
        print(f"    expected gitleaks {GITLEAKS_VERSION}; set GITLEAKS_BIN to "
              f"point at it.", file=sys.stderr)
        return 3

    try:
        accepted = read_accepted(args.accepted)
        findings, stats = run_scan(args.repo, args.scan_range, binary)
    except ScanUnavailable as exc:
        # EXIT 3, NOT 1. "I could not look" and "I looked and it was clean" are
        # different statements and only one of them licenses a push.
        print(f"SECRET SCAN COULD NOT RUN: {exc}", file=sys.stderr)
        return 3

    print(f"range={stats['range']}  blobs={stats['blobs']}  "
          f"bytes={stats['bytes_read']:,}  "
          f"gitleaks={'yes' if stats['gitleaks_ran'] else 'NO'}  "
          f"{stats['seconds']:.1f}s")

    if not stats["gitleaks_ran"]:
        # NAMED, NEVER SILENT. A run with one engine is not the run this gate
        # promises, and a reader who is not told will read the pass as both.
        print("  WARNING: gitleaks was not run -- ONE ENGINE ONLY. The project "
              "scanner alone does not cover the ~170 provider rules gitleaks "
              "carries.")

    if args.emit_accepted:
        for finding in sorted(findings, key=lambda f: f.fingerprint()):
            print(finding.fingerprint())
        return 0

    # DEDUPED BY FINGERPRINT. A blob that lives at two basenames is
    # materialised under both, so gitleaks reports the same (oid, rule, line)
    # twice; reporting it twice would make one finding read as two and would
    # make the count in the summary a count of copies rather than of findings.
    by_fingerprint = {}
    for finding in findings:
        by_fingerprint.setdefault(finding.fingerprint(), finding)

    unaccepted = [f for fp, f in sorted(by_fingerprint.items())
                  if fp not in accepted]

    # THE STALENESS CHECK IS A PROPERTY OF THE FULL RANGE AND OF NOTHING ELSE.
    # The accepted table describes the whole object database; a `staged` scan
    # sees the handful of blobs one commit introduces and legitimately matches
    # almost none of it. Running the check there fired all 21 entries as stale
    # on the first hook invocation -- found by running it, not by reading -- and
    # a hook that reports the accepted table as broken on every commit is a hook
    # that gets uninstalled by the end of the day.
    stale = (sorted(fp for fp in accepted if fp not in by_fingerprint)
             if args.scan_range == "objects" else [])

    if unaccepted:
        # THE REMEDY IS CHOSEN BY A VERIFIED FACT, NOT BY THE SYMPTOM. See
        # `classify_reachability`: the "add a fingerprint" remedy is measured to
        # be WRONG for an object no ref reaches, and the probe runs here -- on
        # the path that has already decided to refuse -- so a clean run pays
        # nothing for it and a probe that cannot run cannot turn a refusal into
        # exit 3.
        #
        # THE PARTITION IS TOTAL AND THAT IS DELIBERATE. Three states, three
        # headings, and a fourth bucket for anything outside the vocabulary --
        # folded into `unverified`, which is the honest place for "no state this
        # code knows". Without that fold a finding whose state this branch does
        # not recognise would be counted in the total and printed under no
        # heading at all: the refusal would say N and list fewer than N, which
        # is silent under-reporting on the one output an operator acts on.
        states, probe_note = classify_reachability(
            args.repo, sorted({f.oid for f in unaccepted}))
        reaching = [f for f in unaccepted
                    if states.get(f.oid) == REACHABILITY_REACHABLE]
        unreached = [f for f in unaccepted
                     if states.get(f.oid) == REACHABILITY_UNREACHABLE]
        unverified = [f for f in unaccepted
                      if states.get(f.oid) not in (REACHABILITY_REACHABLE,
                                                   REACHABILITY_UNREACHABLE)]
        unclassified = [f for f in unverified
                        if states.get(f.oid) != REACHABILITY_UNVERIFIED]

        print()
        print(f"SECRET SCAN FAILED: {len(unaccepted)} unaccepted finding(s).")
        print(f"  reachability: {probe_note}")

        if reaching:
            print()
            print(f"  {len(reaching)} finding(s) in content this repository "
                  f"REACHES -- a ref, a reflog entry or the index:")
            print()
            for finding in reaching:
                print("    " + finding.describe())
            print()
            print("  Each line is <blob-oid>:<engine>:<detector>:<locator>.")
            print(f"  Locate one with:  git cat-file -p <blob-oid>")
            print("  If it is a false positive, add the fingerprint to")
            print(f"  {args.accepted} with a comment saying why -- DESCRIBE the")
            print("  value, never reproduce it: a suppression file that quotes "
                  "what")
            print("  it suppresses will suppress itself into existence.")

        if unreached:
            # A DIFFERENT FINDING WITH A DIFFERENT REMEDY, AND THE MESSAGE MUST
            # NOT OFFER THE OTHER ONE. A fingerprint keyed on one of these
            # matches only where the object is present, so in a checkout that
            # does not have it the gate reports the entry as STALE and exits 2.
            # WHICH checkouts those are is NOT established -- see the
            # measurement in the message: a local clone carries unreferenced
            # objects. BLOCK 5 of the accepted table records the case this gate
            # met, and it is a deliberate NON-entry for that reason.
            print()
            print(f"  {len(unreached)} finding(s) NOT REACHED by any inspected "
                  f"local ref, reflog")
            print("  entry or index entry:")
            print()
            for finding in unreached:
                print("    " + finding.describe())
            print()
            print("  VERIFIED, not guessed: each of these object names is "
                  "absent from")
            print("  `git rev-list --objects --all --reflog "
                  "--indexed-objects`, so no ref,")
            print("  no reflog entry and no index entry here reaches it.")
            print()
            print("  WHAT THAT DOES AND DOES NOT SAY. It is in THIS object "
                  "database and is")
            print("  not reached by the inspected local refs, reflogs or "
                  "index. It says")
            print("  NOTHING about what any other repository contains -- a "
                  "local probe")
            print("  cannot establish that, and the same content can be "
                  "unreachable here")
            print("  and reachable from a branch somewhere else.")
            print()
            print("  DO NOT ADD THESE TO THE ACCEPTED TABLE. A fingerprint "
                  "matches only")
            print("  where the object is present. If another checkout lacks "
                  "this object,")
            print("  its accepted fingerprint will be reported as stale.")
            print()
            print("  WHICH CHECKOUTS LACK IT IS NOT ESTABLISHED HERE, and this "
                  "gate cannot")
            print("  establish it. A local `git clone` hardlinks the whole "
                  "object")
            print("  directory, unreferenced objects included -- MEASURED: of "
                  "four clone")
            print("  forms, THREE carried such a blob (default, "
                  "--no-hardlinks, --depth=1)")
            print("  and only one forced through the transport (file://, or a "
                  "network")
            print("  remote) did not. So the line above is a consequence that "
                  "MAY follow,")
            print("  not a prediction that it will.")
            print()
            print(f"  BLOCK 5 of {args.accepted}")
            print("  records the case this gate met and what was established "
                  "about it.")
            print()
            print("  THE REMEDY IS OPERATOR-REVIEWED LOCAL CLEANUP, AND THIS "
                  "GATE PERFORMS")
            print("  NONE OF IT. Expiring unreachable objects removes EVERY "
                  "unreachable")
            print("  object in this checkout, not only these -- including "
                  "unreachable")
            print("  commits that are sometimes somebody's only handle on "
                  "abandoned")
            print("  work. Review what would go, then decide:")
            print()
            print("      git fsck --unreachable          # what is unreachable")
            print("      git prune --expire=now          # remove it")
            print()
            print("  Inspect one first with:  git cat-file -p <blob-oid>")

        if unverified:
            # NEITHER REMEDY IS OFFERED HERE, AND THAT IS THE POINT RATHER THAN
            # AN OMISSION. The two above are OPPOSITES -- one says record the
            # fingerprint, the other says recording it risks a stale entry in
            # any checkout that lacks the object -- and which is correct
            # follows from a fact this run failed to establish.
            # Naming either would be advising an action on the strength of a
            # coin toss, and one of the two is irreversible. The action is to
            # make the probe answer; the remedy follows from what it then says.
            print()
            print(f"  {len(unverified)} finding(s) whose reachability could "
                  f"NOT be established:")
            print()
            for finding in unverified:
                print("    " + finding.describe())
            print()
            print(f"  Reachability could not be established: {probe_note}.")
            print("  This run does not know whether any ref, reflog entry or "
                  "index entry")
            print("  here reaches these objects.")
            if unclassified:
                # LOUD, because it means this branch met a state it does not
                # know -- a vocabulary member added without a heading.
                print()
                print(f"  {len(unclassified)} of them carry no state this gate "
                      f"recognises at all, which")
                print("  is a defect in the gate rather than in the "
                      "repository. Report it.")
            print()
            print("  NO REMEDY IS RECOMMENDED FOR THESE. The two remedies this "
                  "gate knows")
            print("  are opposites -- recording a fingerprint is right for "
                  "content a ref")
            print("  reaches, and for content none reaches it risks a stale "
                  "entry in any")
            print("  checkout that lacks the object -- and which applies "
                  "follows from the")
            print("  fact this run could not get. Cleanup in particular is NOT "
                  "advised")
            print("  here: it is irreversible, and nothing has established "
                  "that these")
            print("  objects are unreached.")
            print()
            print("  RESOLVE THE PROBE FAILURE FIRST, then re-run this gate "
                  "and act on the")
            print("  heading it then prints. Check that `git` is on PATH and "
                  "that --repo")
            print("  names a repository, and that this command answers:")
            print()
            print("      git -C <repo> rev-list --objects --all --reflog "
                  "--indexed-objects")
            print()
            print("  Inspect one finding with:  git cat-file -p <blob-oid>")

        return 1

    if stale:
        print()
        print(f"ACCEPTED TABLE IS STALE: {len(stale)} entry(ies) no longer "
              f"match anything.")
        for fingerprint in stale:
            # Truncated: a reason block here runs to several hundred words and
            # 21 of them at full length buries the fingerprints they are about.
            print(f"    {fingerprint}\n        was: "
                  f"{accepted[fingerprint][:120]}")
        print()
        print("  A blob OID is permanent, so this normally means history was")
        print("  rewritten -- which is exactly when these need re-reviewing.")
        print("  Remove the entries, or re-derive them with --emit-accepted.")
        return 2

    # ACCEPTED IS NOT CLEAN, AND THE SUMMARY SAYS SO RATHER THAN PRINTING A
    # COUNT. This pipeline's own workflow header states the rule for the other
    # three gates -- "THE SECURITY GATES ARE GREEN, AND GREEN DOES NOT MEAN
    # CLEAN" -- and an accepted entry here can be a REAL credential that is
    # present in history and cannot be removed without a rewrite. A gate that
    # renders that as one green line is a gate that buries it.
    if by_fingerprint:
        print()
        print(f"ACCEPTED (present, not removed) -- {len(by_fingerprint)} "
              f"finding(s), grouped by the reason recorded beside them:")
        grouped = {}
        for fingerprint, finding in sorted(by_fingerprint.items()):
            grouped.setdefault(accepted[fingerprint], []).append(finding)
        for reason, group in sorted(grouped.items()):
            engines = sorted({f.engine for f in group})
            print(f"    {len(group):>3} x [{'+'.join(engines)}] "
                  f"{reason[:150]}")
        print()

    # THE CLOSING LINE SAYS WHAT WAS ESTABLISHED FOR THIS RANGE, and it is not
    # the same sentence for both. "every accepted entry still matched" is a
    # claim only the full-range scan can make; printing it after a `staged` scan
    # that matched none of them would be a report about a check that did not
    # run.
    if args.scan_range == "objects":
        print(f"NO UNACCEPTED FINDINGS. {len(by_fingerprint)} finding(s), all "
              f"{len(accepted)} accepted, and every one of the "
              f"{len(accepted)} accepted entries still matched.")
    else:
        print(f"NO UNACCEPTED FINDINGS in the staged content. "
              f"{len(by_fingerprint)} finding(s). The accepted table "
              f"({len(accepted)} entries) is not checked for staleness here -- "
              f"that is the full-range scan's job.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
