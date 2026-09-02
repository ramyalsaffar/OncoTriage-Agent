"""What produced this run, as facts a reader can diff two runs on.

WHAT THIS ANSWERS AND WHAT ALREADY ANSWERED IT.
``oncotriage/run_fingerprint.py`` records the CONFIGURATION a run was taken
under -- the prompt version, the renderer digest, the judge, the collection, the
cohort -- and gates a resume on it. It says nothing about the MACHINE. Two runs
with an identical stamp can be produced by two different builds of this project
against two different resolved dependency sets with two different sets of
cross-encoder weights, and every artifact either one writes is silent about all
three. This module is the other half: the environment, the build, and the exact
identity of every model that gets loaded.

THE SPLIT BETWEEN THE TWO IS NOT ARBITRARY AND IT IS THE DESIGN DECISION HERE.
A fingerprint field GATES A RESUME, so a field that moves for a reason the
pipeline does not care about turns the gate into something an operator learns to
clear without reading -- which is precisely the over-refusal
``run_fingerprint.normalized_module_source`` strips docstrings to avoid. The
resolved package list moves for ``pip install ipython``. So:

    RECORDED HERE AND GATED       cross_encoder_revision -- it decides which
                                  weights rank every trial, and a resume across
                                  a checkpoint change mixes two rankers' output
                                  into one artifact. It is a FINGERPRINT field
                                  (see FINGERPRINT_FIELDS) and this module
                                  records it beside the rest of the model
                                  identity so one dict answers "what ran".

    RECORDED HERE AND NOT GATED   environment_hash, git_commit, git_dirty, the
                                  image identity. Each is a REAL provenance
                                  fact and none of them is a configuration the
                                  pipeline reads. Gating the environment hash
                                  would refuse a resume for a linter upgrade;
                                  gating the git commit would refuse one for a
                                  comment. Both are the shape that trains an
                                  operator to pass --fresh reflexively, at
                                  which cost the gate stops working for the
                                  fields that need it.

The consequence is stated rather than hidden: A RESUME ACROSS A DEPENDENCY
UPGRADE OR A CODE CHANGE OUTSIDE THE FIVE RENDERER MODULES IS PERMITTED, and the
artifact then holds rows from two environments. What makes that recoverable
rather than silent is that the two run rows carry different values in these
columns, so the mixing is a QUERY rather than an unanswerable question -- which
is exactly what "record, do not gate" buys and all it buys.

NOTHING HERE RAISES. Every resolution degrades to UNKNOWN and counts, on
``run_fingerprint``'s and ``probe_index``'s arrangement and for their reason:
this is a diagnostic, and a diagnostic that raises replaces the finding with a
traceback. It runs immediately before a campaign's first billed call, where an
exception would cost the whole run to record a fact nobody is gated on.

WHAT IMPORTING THIS MODULE DOES. Nothing: no subprocess, no file read, no
package scan, no network. ``git_commit()`` spawns a process and
``package_snapshot()`` reads dist-info off disk, and both are inside functions,
which is what keeps ``tests/test_package_invariants.py`` section 2's twelve
traps green.

WHY IT IMPORTS NO MODEL LOADER. The model identities recorded here are the
CONFIGURED ones -- which is what a run record can honestly carry, because the
run row is opened BEFORE the first patient and therefore before any model has
loaded. The pin is what makes the configured value the loaded one:
``oncotriage/agent/deps.py`` passes ``revision=`` and then verifies what came
back, raising at first load. So this record says what will load, and that
module's verifier is what makes the claim true.
"""

import hashlib
import os
import subprocess
import threading
from collections import Counter

from oncotriage import config
from oncotriage import paths
from oncotriage import settings
from oncotriage.embedding import BM25_SPARSE_MODEL_NAME
from oncotriage.observability import get_logger


log = get_logger(__name__)


#------------------------------------------------------------------------------


ENVIRONMENT_RECORD_VERSION = 1
"""Bumped when the FIELD SET of ``current()`` changes, never when a value does.

DELIBERATELY SEPARATE FROM ``run_fingerprint.FINGERPRINT_VERSION``, and the
reason is what that constant costs: a bump there makes every stamped artifact
answer FP_VERSION and refuse to resume until an operator clears it once. This
record gates nothing, so adding a field to it must not cost a campaign its
checkpoint. Two records, two versions, one of which is expensive to move and one
of which is not.
"""

UNKNOWN = "unknown"
"""What a field records when it could not be established.

The same sentinel string ``run_fingerprint.UNKNOWN`` uses, for its reason: a
documented value a reader can test for beats a missing key every consumer has to
guard, and it is never ``None``, which a numeric column would silently accept as
"not measured" -- a different fact.
"""

IMAGE_SOURCE_DIGEST = "digest"
IMAGE_SOURCE_BUILD_TAG = "build_tag"
IMAGE_SOURCE_NOT_CONTAINERISED = "not_containerised"
IMAGE_SOURCE_UNRECORDED = "containerised_unrecorded"

IMAGE_IDENTITY_SOURCES = (IMAGE_SOURCE_DIGEST, IMAGE_SOURCE_BUILD_TAG,
                          IMAGE_SOURCE_NOT_CONTAINERISED,
                          IMAGE_SOURCE_UNRECORDED)
"""WHICH channel answered "what image is this", as a closed vocabulary.

CLOSED so a reader may branch on it exhaustively, on
``readiness``'s four-state precedent -- and the members are not
interchangeable, which is the whole reason the source is a column beside the
identity rather than a prefix on it:

    digest                    an immutable content id. THE ONLY ONE THAT IS AN
                              IDENTITY: a digest names bytes.
    build_tag                 a NAME an operator gave a build, which can be
                              moved onto different bytes without changing. It
                              is accepted because it is what a locally-built
                              image has, and it is labelled because treating it
                              as a digest is the failure that makes two runs
                              look identical when they are not.
    not_containerised         this process is not in a container, so there is
                              no image and the absence is the answer. NOT a
                              degradation.
    containerised_unrecorded  this process IS in a container and nobody told it
                              which. THAT IS A DEGRADATION and is counted: a
                              containerised run whose image cannot be named is
                              a run whose code cannot be reproduced from the
                              record.

A CONTAINER CANNOT READ ITS OWN IMAGE DIGEST, and that is a fact about Docker
rather than a gap here -- the digest is held by the daemon, and everything
inside the namespace (`/etc/hostname`, `/proc/self/cgroup`) names the CONTAINER,
not the image its bytes came from. So the channel is an environment variable the
operator sets, and a run that was not told records that it was not told.
"""

ENVIRONMENT_DEGRADATIONS = Counter()
"""Why the environment record could not be fully established, by field and cause.

Keys are ``{field}:{cause}`` -- ``git_commit:not_a_repository``,
``package_snapshot:ImportError``, ``image_identity:containerised_unrecorded``.
Non-zero means some ``current()`` call in this process produced an UNKNOWN, and
therefore that the run row it filled cannot answer one of the questions this
module exists to answer.

Registered in ``oncotriage/degradation.py``'s spec table rather than through
``register()``: this module does not import that one, so the primary route
applies -- ``run_fingerprint.FINGERPRINT_DEGRADATIONS``' arrangement, for its
reason.
"""

_RESOLVED = {}
"""The per-process cache, keyed 'environment'. See ``current()``."""

_RESOLVE_LOCK = threading.RLock()
"""Locked for ``run_fingerprint._RESOLVE_LOCK``'s reason.

``if k not in d: d[k] = build()`` is two atomic operations and one non-atomic
sequence. Nothing drives this from a worker thread TODAY -- the batch runner
resolves it once on its main thread before the pool exists -- and the lock is
here because that is the pattern this project's other two lazy caches
(``agent/deps.py:_resolve``, ``run_fingerprint``) already hold, and because the
cost of losing the race is two subprocess spawns rather than one.
"""

GIT_TIMEOUT_SECONDS = 10
"""How long to wait for `git`. NOT in oncotriage/config.py, on
``control.STOP_MESSAGE_MAX_CHARS``' argument: that file's promise is that every
constant in it is a tunable an operator changes to change what a run DOES, and
this changes nothing about a run -- it bounds how long a diagnostic may block
before recording UNKNOWN. Generous because the cost of being wrong is a run
whose commit is unrecorded, and small because it is on the path before the first
patient."""


#------------------------------------------------------------------------------


def _git(args):
    """Run one git command in the package's own directory. ``None`` on anything.

    THE CWD IS DERIVED FROM ``__file__``, never from ``os.getcwd()``. `git`
    resolves its repository from the working directory, and this is called from
    a batch runner an operator may have launched from anywhere -- from ``/`` by
    a systemd unit, from the checkpoint directory by hand. Reading the working
    directory would record the commit of whatever repository happened to be
    above it, which is worse than recording nothing.

    NEVER RAISES. Every failure -- git absent, not a repository, a timeout, a
    permission error -- returns ``None`` and the caller counts it.
    """
    try:
        completed = subprocess.run(
            ["git"] + list(args),
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS,
            check=False)
    except Exception as exc:
        return None, type(exc).__name__
    if completed.returncode != 0:
        return None, f"exit{completed.returncode}"
    return completed.stdout, None


def git_commit():
    """``(commit, dirty)``: the HEAD commit and whether the tree was modified.

    ``commit`` is UNKNOWN when git could not answer; ``dirty`` is ``None`` in
    that case, which is "nobody measured this" and is a different fact from a
    measured ``False``. That distinction is ``runs.resumed``'s, one table over,
    and it is why this returns three states rather than two.

    DIRTY IS ASKED SEPARATELY AND IS NOT DERIVED FROM THE COMMIT. A commit
    identifies the code only if the tree matches it, which is the argument
    ``oncotriage/tracking.py`` already makes for its own ``git_dirty`` tag. It
    is ``git status --porcelain`` over TRACKED files only (``--untracked-files=
    no``): an untracked scratch file beside the package does not change what
    ran, and counting it would report every developer tree as dirty forever,
    which is a flag nobody then reads.
    """
    out, cause = _git(["rev-parse", "HEAD"])
    if out is None:
        ENVIRONMENT_DEGRADATIONS[f"git_commit:{cause}"] += 1
        return UNKNOWN, None
    commit = out.strip()
    if not commit:
        ENVIRONMENT_DEGRADATIONS["git_commit:empty"] += 1
        return UNKNOWN, None

    status, cause = _git(["status", "--porcelain", "--untracked-files=no"])
    if status is None:
        ENVIRONMENT_DEGRADATIONS[f"git_dirty:{cause}"] += 1
        return commit, None
    return commit, bool(status.strip())


def image_identity():
    """``(identity, source)``: which image this is, and which channel said so.

    ``source`` is always a member of IMAGE_IDENTITY_SOURCES. ``identity`` is
    ``None`` for the two sources that have nothing to name, which is a value:
    ``image_identity IS NULL AND image_identity_source = 'not_containerised'``
    is a complete answer and not a hole.

    THE DIGEST OUTRANKS THE TAG and the tag is never consulted when a digest was
    given -- a digest names bytes and a tag names a name, so preferring the
    weaker of two supplied answers would be a choice against the reader.
    """
    digest = settings.resolve_image_digest()
    if digest:
        return digest, IMAGE_SOURCE_DIGEST
    tag = settings.resolve_image_tag()
    if tag:
        return tag, IMAGE_SOURCE_BUILD_TAG
    if paths.IS_DOCKER:
        ENVIRONMENT_DEGRADATIONS[
            f"image_identity:{IMAGE_SOURCE_UNRECORDED}"] += 1
        log.warning("this process is containerised and no image identity was "
                    "supplied, so its run rows cannot name the build that "
                    "produced them",
                    event="image_identity_unrecorded",
                    image_identity_source=IMAGE_SOURCE_UNRECORDED,
                    reason=f"neither {settings.ENV_IMAGE_DIGEST} nor "
                           f"{settings.ENV_IMAGE_TAG} is set")
        return None, IMAGE_SOURCE_UNRECORDED
    return None, IMAGE_SOURCE_NOT_CONTAINERISED


def package_snapshot():
    """The resolved package list, ``pip freeze`` shape, one ``name==version``
    per line, sorted, newline-terminated. ``None`` when it could not be read.

    ``importlib.metadata`` AND NOT ``pip freeze``. Three reasons, and only the
    first is about speed: a subprocess spawn on the path before the first
    patient is a cost this project already counts (section 2 of
    tests/test_package_invariants.py exists because of it); ``pip`` is not
    guaranteed to be installed in the environment the code is running in, and a
    provenance record that needs a package manager present is a record that is
    absent exactly on a minimal production image; and ``pip freeze`` reports
    what pip THINKS is installed, including its own editable-install rendering
    (``-e git+...``), which differs by pip version and would make two identical
    environments hash differently across a pip upgrade.

    SORTED AND CASE-FOLDED ON THE NAME, so the hash is a function of the
    ENVIRONMENT and not of the order the filesystem happened to yield
    dist-info directories in -- which is ``paths._glob_one``'s lesson, and it is
    not hypothetical: ``importlib.metadata.distributions()`` walks ``sys.path``
    in order and yields whatever ``os.scandir`` gives.

    DUPLICATES ARE KEPT RATHER THAN COLLAPSED. Two dist-info directories for one
    package name -- a stale one left by a failed upgrade, or a shadowing copy on
    an earlier ``sys.path`` entry -- is a real and confusing environment state,
    and an environment hash that hides it is a hash that says two genuinely
    different machines are the same. The line count is therefore the DIST count,
    not the distinct-name count, and ``package_count`` says so.
    """
    try:
        from importlib import metadata
        entries = []
        for dist in metadata.distributions():
            name = dist.metadata["Name"]
            if not name:
                continue
            entries.append(f"{name}=={dist.version}")
    except Exception as exc:
        ENVIRONMENT_DEGRADATIONS[f"package_snapshot:{type(exc).__name__}"] += 1
        log.warning("the resolved package list could not be read, so this "
                    "run's environment cannot be diffed against another's",
                    event="package_snapshot_unavailable",
                    reason=type(exc).__name__)
        return None
    if not entries:
        ENVIRONMENT_DEGRADATIONS["package_snapshot:empty"] += 1
        return None
    entries.sort(key=lambda line: (line.lower(), line))
    return "\n".join(entries) + "\n"


def snapshot_hash(snapshot):
    """The 16-hex digest of a snapshot, or UNKNOWN for ``None``.

    TRUNCATED TO SIXTEEN CHARACTERS, matching
    ``run_fingerprint.renderer_digest()`` and
    ``fixtures.capture.compute_collection_digest`` -- this project's standing
    width for a digest a human reads and a query groups on. It is a
    DEDUPLICATION key and a diff trigger, not a security boundary: 64 bits is
    not a defence against a chosen collision, and nothing here is choosing.
    """
    if snapshot is None:
        return UNKNOWN
    return hashlib.sha256(snapshot.encode("utf-8")).hexdigest()[:16]


def model_identities():
    """Every model this run would load or call, by its exact configured id.

    CONFIGURED RATHER THAN OBSERVED, and the docstring at the top of this module
    says why that is honest: the run row is opened before the first patient and
    therefore before any model has loaded. What makes the configured value the
    loaded one is the PIN -- ``oncotriage/agent/deps.py`` passes
    ``revision=config.CROSS_ENCODER_REVISION`` and verifies what came back,
    raising at first load -- so this record states what will run and that
    module's verifier is what stops it being a claim.

    THE JUDGE MODEL IS DELIBERATELY ABSENT, and it is the one field the brief
    for this record asked for that is not here. ``evaluation/rater.py``'s
    ``DEFAULT_MODEL`` is a DEFAULT of a separate programme stage that writes no
    ``runs`` row of its own, runs later, against a different artifact, and takes
    an override on its own command line. Recording it on a CAMPAIGN row would
    be a claim about a pass that may never run and that, if it runs, may not use
    it -- which is ``tracking.CONFIGURATION_PARAM_NAMES``' "a false record is
    worse than no record, because it is indistinguishable from a true one",
    applied across a stage boundary. The judge's own artifact is where its model
    belongs.

    ``matching_wire_model`` IS RECORDED BESIDE ``matching_model`` AND IS NOT
    REDUNDANT WITH IT. They are the same string on the OpenAI arm and different
    on both Bedrock arms, where the wire id is a cross-Region inference profile;
    the first is what the project is configured to judge with and the second is
    what a request actually names, which is what a bill and a stored
    ``matching_model`` echo can be reconciled against.
    """
    return {
        "cross_encoder_model": config.CROSS_ENCODER_MODEL,
        "cross_encoder_revision": config.CROSS_ENCODER_REVISION,
        # THE PRECISION AND THE SEQUENCE BUDGET, which the pass that added them
        # left out of every record. Both are properties OF the checkpoint above
        # and both change every Stage 3 score -- the first the arithmetic the
        # scores are computed in, the second how much of each trial is read --
        # so a record naming the checkpoint and not these two names less than it
        # appears to.
        "cross_encoder_dtype": config.CROSS_ENCODER_DTYPE,
        "cross_encoder_max_length": config.CROSS_ENCODER_MAX_LENGTH,
        "embedding_model": config.EMBEDDING_MODEL,
        "sparse_model": BM25_SPARSE_MODEL_NAME,
        "matching_model": config.MATCHING_MODEL,
        "matching_wire_model": config.matching_wire_model(),
        "matching_provider": config.MATCHING_PROVIDER,
    }


def clear_cache():
    """Drop the per-process cache. Called by a consumer at the top of its run so
    two runs in one process each resolve, and by tests."""
    with _RESOLVE_LOCK:
        _RESOLVED.clear()


def current(refresh: bool = False) -> dict:
    """This run's environment record. Cached for the process.

    Returns a plain dict, which is what makes it storable: the caller hands it
    to ``start_run_record(environment=...)`` and to
    ``fixtures.capture.build_environment_block()`` without either of them
    importing this module's types -- the same shape ``run_fingerprint.current()``
    and ``cohort.CohortSelection.record()`` already take, for the layering
    reason argued at ``RUN_COHORT_COLUMNS``.

    ``package_snapshot`` IS IN THIS DICT AND IS THE ONE LARGE FIELD. It is
    several kilobytes and it is NOT a ``runs`` column: the storage layer writes
    it into ``run_environment`` keyed by ``environment_hash``, once per distinct
    hash rather than once per run. See ``RUN_ENVIRONMENT_COLUMNS`` there for the
    argument.
    """
    with _RESOLVE_LOCK:
        if refresh:
            _RESOLVED.clear()
        if "environment" in _RESOLVED:
            return _RESOLVED["environment"]

        snapshot = package_snapshot()
        commit, dirty = git_commit()
        identity, source = image_identity()

        record = {
            "environment_record_version": ENVIRONMENT_RECORD_VERSION,
            "environment_hash": snapshot_hash(snapshot),
            "package_snapshot": snapshot,
            "package_count": (None if snapshot is None
                              else len(snapshot.splitlines())),
            "git_commit": commit,
            "git_dirty": dirty,
            "image_identity": identity,
            "image_identity_source": source,
        }
        record.update(model_identities())

        log.info("environment record resolved",
                 event="environment_record_resolved",
                 environment_hash=record["environment_hash"],
                 package_count=record["package_count"],
                 git_commit=commit, git_dirty=dirty,
                 image_identity=identity, image_identity_source=source)

        _RESOLVED["environment"] = record
        return record


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Sep  2 2026
"""
