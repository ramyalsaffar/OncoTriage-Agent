# Corpus Cancer-Group Map
########################

"""One parse of the corpus, producing the ``stem -> cancer group`` map the
stratified cohort draw needs.

WHY THIS IS A SEPARATE MODULE FROM ``oncotriage/evaluation/cohort.py``. That
module's stated property is that importing it does nothing and every function
in it is drivable offline with a fabricated population: it imports only
``oncotriage.config``, resolves no path, opens no file and builds no registry.
Stratification needs the OPPOSITE of all of that -- the FHIR parser, the
ICD-10-CM registry and a thousand files off disk -- so putting it there would
cost that module the purity that lets every test of the draw run in
milliseconds with no corpus. The grouping therefore arrives at ``select()`` as
a callable, and this is the module that builds one.

WHY NOT IN ``oncotriage/registries/primary_cancer.py``, WHICH OWNS THE
VOCABULARY. Because it would be a CYCLE: ``oncotriage/fhir/parser.py`` imports
that module (the pre-diagnosis ECOG pass needed the primary diagnosis date), so
a registry module importing the parser closes the loop. ``oncotriage.evaluation``
already imports both -- ``evaluation/cohort_diff.py`` imports the parser and the
registry today -- and imports neither of them back.

WHAT IT COSTS, MEASURED RATHER THAN ESTIMATED. Roughly three minutes for 1,000
bundles on the development machine, plus the ICD-10-CM release build on the
first ``load_registry()``. That is work the batch runner otherwise did lazily,
one patient at a time, inside its thread pool. It is the price of the operator's
coverage ruling and it is paid ONCE, above the first billed call, where a
failure costs nothing.

WHAT IMPORTING THIS MODULE DOES
--------------------------------
Nothing. It resolves no path and parses nothing until a function is called.
"""

import ast
import hashlib
import json
import os
import tempfile
import threading
from collections import Counter

from oncotriage import paths
from oncotriage.degradation import register
from oncotriage.fhir.parser import parse_fhir_bundle
from oncotriage.observability import console, get_logger
from oncotriage.registries import cancer_code_registry as _registry_module
from oncotriage.registries import primary_cancer as _grouper_module
from oncotriage.registries.cancer_code_registry import load_registry
from oncotriage.registries.primary_cancer import (
    CANCER_GROUP_UNRESOLVED,
    patient_cancer_group,
)
from oncotriage.evaluation.cohort import stem_of


log = get_logger(__name__)


#------------------------------------------------------------------------------


CORPUS_GROUPING_FAULTS = Counter()
"""Bundles this module could not group, keyed ``{phase}:{ExceptionType}``.

COUNTED AND NOT RAISED, which is item 11a's line applied here: a bundle that
will not parse is third-party DATA, there is no operator command that fixes it,
and raising would make one malformed file stop a whole campaign above its first
billed call. A bundle that cannot be grouped is admitted to the population
under ``CANCER_GROUP_UNRESOLVED`` -- so it can still be DRAWN, in its own
stratum -- and the fault is on the run-end report.

THE COUNT IS THE POINT. Without it, an unparseable half of the corpus and a
corpus with no cancer patients produce the same stratified draw over one
bucket, and neither says anything.
"""

register("CORPUS_GROUPING_FAULTS", CORPUS_GROUPING_FAULTS,
         "FHIR bundles that could not be parsed or grouped while building the "
         "campaign cohort's stratification map. Each is admitted to the "
         "population under the 'unknown' group rather than dropped, so the "
         "cohort size is unaffected and the draw is over a partition that "
         "names its own uncertainty.")


#------------------------------------------------------------------------------


# ===========================================================================
# THE PER-FILE GROUP CACHE
# ===========================================================================
#
# WHY IT EXISTS. Grouping the corpus costs a full parse of every bundle --
# MEASURED at 174.8 s for 1,000 bundles totalling 39.1 GB, plus the ICD-10-CM
# build on the first `load_registry()`. Before the stratified cohort draw that
# work happened lazily inside the runner's thread pool, one patient at a time;
# it is now on the critical path of every campaign and every ablation study,
# BEFORE the first patient. A repeat run on an unchanged corpus was paying it
# again for an answer that cannot have moved.
#
# THE KEY IS PER FILE AND THERE IS NO WHOLE-CORPUS KEY. Each row is keyed on
# `(stem, st_size, st_mtime_ns)`. Three consequences, and the second is why it
# is per file rather than a single digest over the corpus:
#
#   * a row whose stat no longer matches is DISCARDED and that bundle is
#     re-parsed -- invalidation is by construction rather than by an expiry;
#   * ONE changed bundle re-parses ONE bundle. A whole-corpus digest would
#     invalidate all 1,000 for a one-file edit, which on a corpus somebody is
#     actively regenerating is the same 175 s every time;
#   * a stem that has left the corpus is dropped on write, so the file cannot
#     grow without bound across regenerations.
#
# THERE IS NO TIME-BASED EXPIRY, deliberately. An expiry answers "how long
# might this have been true for", which is a guess; the stat signature answers
# "is this still true", which is a measurement. A cache that expires on a
# timer also re-parses a corpus nobody touched, which is the cost this exists
# to remove.
#
# MEASURED END TO END ON THE REAL 1,000-BUNDLE CORPUS, not estimated:
#
#     cold (cache deleted)            195.1 s
#     warm (nothing changed)            0.003 s
#     one bundle touched                0.073 s
#     answers identical across all three: yes
#     cache file: 187,757 bytes, 1,000 rows, 0 faults
#
# COST OF THE KEY ITSELF: 0.0108 s to stat all 1,000 files. CONTENT HASHING
# WAS CONSIDERED AND REJECTED on that measurement -- sha256 over 39.1 GB is
# ~40-60 s, a quarter to a third of the parse it saves, for a guarantee the
# stat signature already gives in every case a person or a generator produces.
#
#   THE LIMIT, STATED: a rewrite that preserves BOTH the byte size AND the
#   nanosecond mtime evades the key. That needs a deliberate `os.utime` with
#   the exact original `st_mtime_ns` alongside an equal-length edit; an
#   ordinary write, a Synthea regeneration, a `cp`, a `git checkout` and a
#   restore-from-backup all move mtime. It is not a security boundary and does
#   not defend against a chosen input.

CACHE_FILENAME = "cohort_group_cache.json"
"""The cache's filename, under ``paths.checkpoint_path``.

WHERE IT LIVES AND WHY. ``08- Checkpoint/`` is this project's one directory for
DERIVED STATE THAT SURVIVES BETWEEN RUNS -- the batch runner's resume
checkpoint, its results file and its STOP sentinel are already there -- and it
sits OUTSIDE the repository in the sibling tree, so nothing here is committed
and no `.gitignore` entry is needed.

IT IS NOT UNDER ``data_fhir_path``. That directory is an INPUT tree, and every
consumer of it globs ``*.json`` -- so a JSON cache written beside the bundles
would be picked up as a patient by the runner, the ablation study, the
evaluation harness and this module's own caller. A cache that enters its own
population is not a hazard worth arguing about.

IT DOES NOT FOLLOW ``--db``, unlike the ablation study's checkpoint (pass
20f-3). That checkpoint records what a DATABASE holds, so it is per database;
this records what the CORPUS is, which is one fact however many databases read
it. Two studies against two scratch databases share the corpus and should share
its grouping.

ONE PROCESS PER ENTRY IS NOT REQUIRED. A batch run and an ablation study can
write it concurrently; the write is atomic (temp + ``os.replace``), so a reader
sees one whole version or the other, and both computed the same values from the
same stat signatures. Last writer wins, benignly.
"""

CACHE_VERSION = 1
"""The cache FILE's own format version. Bumped when the row shape changes.

Separate from ``GROUPER_DIGEST`` below: this invalidates on a change to how the
cache is WRITTEN, that one on a change to what it MEANS.
"""

CORPUS_GROUP_CACHE_FAULTS = Counter()
"""Cache reads and writes that failed, keyed ``{phase}:{ExceptionType}``.

COUNTED AND NEVER RAISED. This is an OPTIMISATION: every failure path here
falls back to parsing, which is what the code did before the cache existed, so
a broken cache costs time and changes no answer. An optimisation that can kill
a campaign above its first billed call is worse than no optimisation.

IT IS A SEPARATE COUNTER FROM ``CORPUS_GROUPING_FAULTS`` and that is the point
rather than tidiness: a bundle that will not parse is a DATA problem an
operator cannot fix, and an unwritable checkpoint directory is a CONFIGURATION
problem they can. Reporting them under one name would put two findings with two
owners in one number -- which is the conflation ``oncotriage/degradation.py``
exists to remove.
"""

register("CORPUS_GROUP_CACHE_FAULTS", CORPUS_GROUP_CACHE_FAULTS,
         "the campaign cohort's group cache could not be read or written, so "
         "the corpus was re-parsed. NOTHING ABOUT THE RUN'S NUMBERS IS "
         "AFFECTED -- the grouping is identical either way; the run simply "
         "paid the parse it exists to avoid, and the next run will pay it "
         "again until the cause is fixed.")


_CACHE_STATE = {}
_CACHE_LOCK = threading.RLock()


def cache_path() -> str:
    """Where the cache file goes. Resolved on first CALL, never at import.

    ``oncotriage/paths.py`` globs the sibling tree on first read, so a module
    that resolved this at import would raise on any machine without one --
    which is the property this module's own header promises. CREATES NOTHING:
    the ``output_dir()``/``ensure_output_dir()`` lesson from pass 20c-3b.
    """
    with _CACHE_LOCK:
        if "path" not in _CACHE_STATE:
            _CACHE_STATE["path"] = os.path.join(paths.checkpoint_path,
                                                CACHE_FILENAME)
        return _CACHE_STATE["path"]


def _ast_digest(module) -> str:
    """A source digest of ``module``, docstrings and comments excluded.

    ``oncotriage/run_fingerprint.py``'s renderer-digest doctrine, applied one
    module over and for the same reason: a comment cannot change what this code
    computes, and a raw-byte digest would throw away 175 s of valid cache for
    every documentation pass. ``ast.unparse`` drops comments by construction and
    the docstring stripping is explicit.
    """
    tree = ast.parse(open(os.path.abspath(module.__file__),
                          encoding="utf-8").read())
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                    and isinstance(node, (ast.Module, ast.FunctionDef,
                                          ast.AsyncFunctionDef, ast.ClassDef))):
                node.body = body[1:] or [ast.Pass()]
    return hashlib.sha256(ast.unparse(tree).encode("utf-8")).hexdigest()[:16]


def grouper_digest() -> str:
    """What a cached group MEANS, as a digest. Resolved once per process.

    A STAT KEY DOES NOT INVALIDATE ON A CODE CHANGE, and that is the hole this
    closes. Every file's size and mtime are unchanged when the GROUPING itself
    is edited -- a group added to ``CANCER_GROUP_KEYWORDS``, a keyword widened,
    or the derivation of "which condition is the primary cancer" changed -- so
    a purely stat-keyed cache would serve rows computed under the old rule
    forever, silently, and the campaign would be stratified by a vocabulary
    nobody is running.

        THIS IS NOT HYPOTHETICAL. The pass immediately before this one changed
        ``patient_cancer_group`` to derive from
        ``_resolve_primary_cancer_condition``, with no file on disk touched. A
        cache without this digest would have survived that edit intact.

    WHAT IS COVERED: the AST-normalised source of the two modules the whole
    derivation lives in -- ``registries/primary_cancer.py`` (the vocabulary,
    ``cancer_group_key`` and ``patient_cancer_group``) and
    ``registries/cancer_code_registry.py`` (the SNOMED table and the three
    detection layers) -- plus the installed ``icd10-cm`` distribution version,
    because the ICD-10-CM code sets are built from that package at runtime and
    a release bump changes which conditions are cancers.

    WHAT IS NOT, STATED RATHER THAN IMPLIED: the ICD-10-CM release DATA itself
    beyond its version string. ``oncotriage/run_fingerprint.py``'s
    ``RENDERER_COVERAGE`` already records that the registry's data is outside
    this repository and cannot be hashed from source at any granularity; a
    reinstall of the same version with different content is not detected. It is
    over-broad in the safe direction elsewhere -- an unrelated edit to either
    module discards a valid cache, which costs one parse and no correctness.
    """
    with _CACHE_LOCK:
        if "grouper_digest" not in _CACHE_STATE:
            try:
                from importlib.metadata import version as _dist_version
                icd_version = _dist_version("icd10-cm")
            except Exception as exc:      # noqa: BLE001 -- counted, never fatal
                CORPUS_GROUP_CACHE_FAULTS[
                    f"icd10_version:{type(exc).__name__}"] += 1
                # UNKNOWN IS ITS OWN VALUE and not an empty string: a version
                # that could not be read must not hash the same as a version
                # that is genuinely absent from a future packaging.
                icd_version = "unknown"
            parts = [str(CACHE_VERSION), icd_version,
                     _ast_digest(_grouper_module),
                     _ast_digest(_registry_module)]
            _CACHE_STATE["grouper_digest"] = hashlib.sha256(
                "|".join(parts).encode("utf-8")).hexdigest()[:16]
        return _CACHE_STATE["grouper_digest"]


def file_signature(path) -> list:
    """``[size, mtime_ns]`` -- the half of a cache key that is about the FILE.

    A LIST rather than a tuple because it round-trips through JSON as one; a
    tuple would come back a list and compare unequal to itself, which is the
    kind of asymmetry that makes a cache silently never hit.
    """
    st = os.stat(path)
    return [st.st_size, st.st_mtime_ns]


def load_cache(path=None) -> dict:
    """``{stem: row}`` from disk, or ``{}`` when it cannot be used.

    RETURNS EMPTY RATHER THAN RAISING on every failure -- absent, unreadable,
    not JSON, not an object, a different ``CACHE_VERSION``, a different
    ``grouper_digest``. Each is counted. The caller then parses everything,
    which is what it did before this cache existed.
    """
    target = cache_path() if path is None else path
    try:
        with open(target, encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        # NOT COUNTED. The first run on a machine has no cache and that is not
        # a degradation; counting it would put a line on every clean bring-up's
        # run-end report.
        return {}
    except Exception as exc:              # noqa: BLE001
        CORPUS_GROUP_CACHE_FAULTS[f"read:{type(exc).__name__}"] += 1
        return {}
    if not isinstance(payload, dict):
        CORPUS_GROUP_CACHE_FAULTS["read:not_an_object"] += 1
        return {}
    if payload.get("cache_version") != CACHE_VERSION:
        # NOT COUNTED, and not a fault: a format bump is this code's own doing
        # and the remedy is automatic.
        return {}
    if payload.get("grouper_digest") != grouper_digest():
        return {}
    rows = payload.get("rows")
    if not isinstance(rows, dict):
        CORPUS_GROUP_CACHE_FAULTS["read:rows_not_an_object"] += 1
        return {}
    return rows


def save_cache(rows, path=None) -> bool:
    """Write ``rows`` atomically. Returns whether it landed.

    TEMP FILE IN THE DESTINATION'S OWN DIRECTORY, then ``os.replace``.
    ``shutil.copy``-style writing is not atomic and a torn cache is worse than
    none -- and ``os.replace`` is atomic only WITHIN one filesystem, which is
    why the temp file is not in ``/tmp``.

    THE TEMP FILE IS REMOVED ON A FAILED REPLACE, and that removal is itself
    counted: a directory accumulating ``.tmp`` files is the visible symptom of
    a cache that has been failing quietly, and this project has the same
    ``tmp_unlink:`` shape at ``oncotriage/ablation/study.py``'s checkpoint.
    """
    target = cache_path() if path is None else path
    payload = {"cache_version": CACHE_VERSION,
               "grouper_digest": grouper_digest(),
               "rows": rows}
    tmp = None
    try:
        directory = os.path.dirname(target) or "."
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".cohort_group_cache-",
                                   suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True)
        os.replace(tmp, target)
        return True
    except Exception as exc:              # noqa: BLE001
        CORPUS_GROUP_CACHE_FAULTS[f"write:{type(exc).__name__}"] += 1
        log.warning("cohort group cache could not be written",
                    extra={"reason": f"write:{type(exc).__name__}"})
        if tmp is not None and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError as unlink_exc:
                CORPUS_GROUP_CACHE_FAULTS[
                    f"tmp_unlink:{type(unlink_exc).__name__}"] += 1
        return False


def scan(fhir_files, out=None, use_cache=True) -> dict:
    """``{stem: {"group": ..., "patient_id": ...}}``, by parsing each bundle once.

    THE PATIENT ID RIDES ALONG BECAUSE THE SECOND PARSE IS THE EXPENSIVE PART.
    ``oncotriage/ablation/study.py`` draws over STEMS and works in PARSED
    PATIENTS, and ``oncotriage/fhir/parser.py:load_all_patients`` does not
    retain the path it parsed -- so joining a drawn stem back to a patient dict
    needs the id, and getting it by re-parsing the drawn bundles is a second
    pass over the same files for a field the first pass already read.

    CACHED PER FILE, keyed on ``(stem, st_size, st_mtime_ns)``. See
    ``CACHE_FILENAME`` for where the file lives and the block above it for why
    the key is per file and why there is no expiry. A row whose signature no
    longer matches is discarded and that ONE bundle is re-parsed; every other
    row is served from the cache.

    A FAILED BUNDLE IS CACHED TOO, and that is deliberate rather than an
    oversight. It carries ``group = CANCER_GROUP_UNRESOLVED`` like any other
    row, so a corpus holding a permanently malformed bundle does not re-parse
    it -- and fail -- on every run for the rest of its life. The COUNTER is
    what a reader consults for how many there are, and it is incremented only
    when the bundle is actually parsed, so a cached failure does not inflate
    it into a per-run tally of one event.

    Args:
        fhir_files: bundle paths. Any order; the answer is a dict.
        out:        line sink, ``console.out`` by default. See ``group_map``.
        use_cache:  ``False`` reads and writes nothing. It exists so a caller
                    that must measure the parse -- or that does not trust the
                    cache -- can say so, and so this function's own test can
                    drive the uncached arm without deleting a file somebody
                    else's run is using.

    Returns:
        A plain dict, one entry per stem in the input INCLUDING the ones that
        failed -- those carry ``group = CANCER_GROUP_UNRESOLVED`` and
        ``patient_id = None``. A missing key would make a failed bundle
        indistinguishable from one the caller never passed.
    """
    emit = console.out if out is None else out
    files = list(fhir_files)
    cached = load_cache() if use_cache else {}

    records = {}
    to_parse = []
    for path in files:
        stem = stem_of(path)
        row = cached.get(stem)
        try:
            signature = file_signature(path)
        except OSError as exc:
            # THE STAT ITSELF FAILED. Not a cache fault -- the FILE is
            # unreadable, which the parse below will report as a grouping
            # fault under its own counter. Falling through to the parse keeps
            # ONE owner for "this bundle could not be read".
            CORPUS_GROUP_CACHE_FAULTS[f"stat:{type(exc).__name__}"] += 1
            to_parse.append((stem, path))
            continue
        # THE DISCARD IS HERE AND IT IS AN EQUALITY, NOT AN AGE. A row whose
        # recorded signature differs from the file's -- in size, in mtime, or
        # because the row predates the field -- is simply not used.
        if (isinstance(row, dict) and row.get("signature") == signature
                and "group" in row):
            records[stem] = {"group": row["group"],
                             "patient_id": row.get("patient_id")}
        else:
            to_parse.append((stem, path))

    if use_cache:
        emit(f"  Grouping: {len(records)} of {len(files)} served from cache, "
             f"{len(to_parse)} to parse.")

    fresh = {}
    registry = load_registry() if to_parse else None
    for idx, (stem, path) in enumerate(to_parse, 1):
        if idx % 100 == 0 or idx == len(to_parse):
            emit(f"  Grouping {idx}/{len(to_parse)} patients...")
        try:
            patient = parse_fhir_bundle(str(path))
            records[stem] = {
                "group": patient_cancer_group(patient, registry),
                "patient_id": patient.get("patient_id"),
            }
        except Exception as exc:          # noqa: BLE001 -- counted, never silent
            CORPUS_GROUPING_FAULTS[f"parse:{type(exc).__name__}"] += 1
            log.warning("cohort grouping failed for a bundle",
                        extra={"reason": f"parse:{type(exc).__name__}"})
            emit(f"  [Cohort] could not group {stem}: "
                 f"{type(exc).__name__}: {exc}")
            records[stem] = {"group": CANCER_GROUP_UNRESOLVED,
                             "patient_id": None}
        try:
            fresh[stem] = file_signature(path)
        except OSError as exc:
            # NO SIGNATURE, NO ROW. Caching a group against a signature we
            # could not take would produce a row that can never be validated
            # and therefore never used -- a row that grows the file and serves
            # nobody.
            CORPUS_GROUP_CACHE_FAULTS[f"stat_after_parse:{type(exc).__name__}"] += 1

    # THE WRITE IS SKIPPED WHEN NOTHING WAS PARSED. A run that hit the cache
    # completely has nothing to add, and rewriting the file anyway would move
    # its own mtime for no reason and lose to a concurrent writer that DID
    # have something to say.
    if use_cache and to_parse:
        # BUILT FROM `records`, WHICH IS THE CORPUS AS IT IS NOW -- so a stem
        # that has left the corpus is dropped rather than carried forever.
        # This is the only pruning there is and it needs no separate pass.
        rows = {}
        for path in files:
            stem = stem_of(path)
            signature = fresh.get(stem)
            if signature is None:
                previous = cached.get(stem)
                if isinstance(previous, dict):
                    signature = previous.get("signature")
            if signature is None or stem not in records:
                continue
            rows[stem] = {"signature": signature,
                          "group": records[stem]["group"],
                          "patient_id": records[stem].get("patient_id")}
        save_cache(rows)

    return records


def group_map(fhir_files, out=None, use_cache=True) -> dict:
    """``{stem: group}`` for every file, by parsing each bundle once.

    Args:
        fhir_files: bundle paths. Any order; the answer is a dict.
        out:        line sink, ``console.out`` by default. Injectable on
                    ``degradation.print_report``'s footing -- the caller of
                    this function in production is a ``main()`` no test can
                    drive, so the progress it emits has to be exercisable on
                    its own.

    Returns:
        A plain dict. Every stem present in the input is a key, including the
        ones that failed -- see ``CORPUS_GROUPING_FAULTS``.

    THE REGISTRY IS RESOLVED ONCE AND PASSED IN, not resolved per patient.
    ``load_registry()`` takes a construction lock, and this loop runs a thousand
    times. It is ``load_registry()`` and deliberately NOT
    ``oncotriage.agent.deps.get_cancer_registry()``: a stub installed for an
    agent test must not change which patients a campaign selects. Same argument
    ``oncotriage/fhir/clean.py`` makes about the deletion path.

    A DUPLICATE STEM IS NOT DETECTED HERE and that is deliberate. This dict
    would silently collapse one, so the check stays where the population is
    built -- ``cohort.draw`` raises on it, over the ORIGINAL list, and that is
    the one owner of what a repeated stem means.
    """
    return {stem: rec["group"]
            for stem, rec in scan(fhir_files, out, use_cache).items()}


def patient_ids(records) -> dict:
    """``{stem: patient_id}`` from a ``scan()`` result, dropping the failures.

    A stem whose parse failed carries ``patient_id = None`` and is ABSENT here
    rather than mapped to ``None``: a caller building a set of wanted ids would
    otherwise put ``None`` in it, and ``None`` matches every patient dict whose
    own id is missing.
    """
    return {stem: rec["patient_id"] for stem, rec in records.items()
            if rec.get("patient_id") is not None}


def grouper(mapping):
    """A TOTAL ``stem -> group`` callable over ``mapping``.

    ``mapping.get`` would answer ``None`` for a stem the map does not carry,
    and ``None`` is not a member of ``CANCER_GROUPS`` -- it would become a
    stratum of its own named ``None`` in every printed report and every
    recorded ``group_counts``. ``stratified_draw`` requires its grouper to be
    TOTAL, and this is what makes it so: an unmapped stem is
    ``CANCER_GROUP_UNRESOLVED``, which is the group that already means "we
    could not establish this patient's cancer".
    """
    def group_of(stem):
        return mapping.get(stem, CANCER_GROUP_UNRESOLVED)
    return group_of


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Sep  2 2026

@author: ramyalsaffar
"""
