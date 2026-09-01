# Campaign Cohort and Programme Samples
######################################

"""WHICH patients a campaign runs, and which of them the two follow-on
measurements are taken over.

THE RULING THIS IMPLEMENTS. The evaluation programme is a **300-patient
campaign** drawn from the corpus on disk, a **50-patient stability sample**
re-run once so every member has two observations (k=2), and a **100-patient
judge sample** rated by the independent rater. The 50 and the 100 are
INDEPENDENT draws from the 300 -- chance overlap is expected and is reported,
and forcing them together or apart would bias one or both.

WHAT WAS THERE BEFORE. Nothing. ``oncotriage/batch/runner.py`` loaded every
``*.json`` under ``paths.data_fhir_path`` and ran all of it, so the ruled cohort
size had no mechanism at all and the two samples had no existence outside the
resample pass's own unrelated draw. The 1,000 files on disk are themselves the
output of ``oncotriage/fhir/clean.py``'s cap, which is a DELETION -- so "run
fewer patients" could previously only be expressed by deleting bundles.

    THE CORPUS IS NOT NARROWED HERE AND NOTHING IS DELETED. This module reads a
    list of paths and returns a subset of it. The 1,000 bundles stay on disk,
    which is what lets the cohort be redrawn, widened, or reproduced from a
    seed without regenerating anything.


WHY HASH-RANKING RATHER THAN ``random.Random(seed).sample``
-----------------------------------------------------------
This project holds both precedents and they are not equivalent:

    ``oncotriage/ablation/study.py:stratified_sample`` and
    ``oncotriage/evaluation/sampling.py:select_samples`` use a local
    ``random.Random(seed)``. Deterministic within one interpreter.

    ``oncotriage/evaluation/rater.py:select_retest_decisions`` ranks by
    ``sha256(seed | key)`` and takes the lowest k, and says why in as many
    words: "``random.Random(seed).sample`` is deterministic within one
    interpreter but its stream is an implementation detail of CPython's
    Mersenne seeding, and this selection has to reproduce from a recorded seed
    on another machine and another Python."

THAT ARGUMENT DECIDES THIS ONE, and more strongly, because the artefact is
larger. The retest subsample decides which decisions are asked twice; this
decides WHICH PATIENTS EVERY PUBLISHED NUMBER IS COMPUTED OVER. A reader given
the seed, the size and the file list must be able to recompute the membership
with ten lines of Python and no assumption about the interpreter that drew it.
``rank_key`` and ``draw`` below are that algorithm, and ``DRAW_ALGORITHM``
states it in one line that every artefact records.

    THE COST IS STATED: hash-ranking is not a uniformly random sample in the
    ``random.sample`` sense -- it is a uniformly random PERMUTATION of the
    population truncated at k, which is the same distribution over subsets (the
    hash acts as a fixed random labelling and every k-subset is equally likely
    under it) and is NOT the same STREAM. So a cohort drawn here will not equal
    one ``random.Random(seed).sample`` would draw from the same seed. Nothing
    reproduces an earlier draw, because there was no earlier draw.


WHY THE DRAW IS SIMPLE RANDOM AND **NOT** STRATIFIED BY CANCER GROUP
---------------------------------------------------------------------
The two existing samplers in this project both stratify -- the ablation study
proportionally by primary cancer group, the 30-patient extract by three
hand-named groups -- so the default expectation is that this one would too. It
does not, for four reasons in the order that decided it:

1.  **STRATIFICATION DESTROYS THE RECOMPUTABILITY THIS MODULE EXISTS FOR.** A
    group key needs ``CancerCodeRegistry``: SNOMED exact, then ICD-10-CM 2024
    exact through the ``icd10-cm`` package, then a display-term morphology
    fallback. ``oncotriage/run_fingerprint.py:RENDERER_COVERAGE`` states that
    the registry's DATA "is outside the repository entirely and could not be
    hashed from source at any granularity". So a stratified membership is a
    function of an artefact this project cannot pin, and a reader on another
    machine with a different ``icd10-cm`` release would compute DIFFERENT
    STRATA from the same seed and therefore a different cohort. Hash-ranking
    would buy nothing: the ranking would be reproducible and the buckets it
    ranked within would not.

2.  **THE POPULATION IS ALREADY A SIMPLE RANDOM DRAW.**
    ``oncotriage/fhir/clean.py`` step 3 caps the corpus with
    ``rng.sample(remaining_files, COHORT_CAP)`` -- unstratified. So the 1,000
    on disk are a simple random sample of the alive cancer patients Synthea
    produced, and a simple random sample of THAT is a simple random sample of
    the same population at the same proportions in expectation. Stratifying the
    second stage while the first is unstratified fixes the cohort's marginals
    to the CORPUS's marginals, which are themselves one random realisation --
    it makes the 300 match the 1,000 exactly rather than matching the
    population, which is not the quantity anyone wants fixed.

3.  **IT WOULD COST A FULL PARSE BEFORE THE FIRST PATIENT.** A group key needs
    the parsed bundle, so stratifying means parsing every file on disk and
    building the ICD-10-CM registry at cohort-selection time -- which this
    runner otherwise does lazily, one patient at a time, inside the pool.

4.  **WHAT THE PAPER CLAIMS.** "A seeded simple random sample of size N from
    the corpus, drawn by sha256 rank over the filename stem" is a claim a
    reader verifies with the file list and ten lines of code. "Proportionally
    stratified by primary cancer group" is a claim a reader can only verify by
    reproducing the registry, and (1) says they cannot.

The group composition the cohort inherits is a MEASUREMENT rather than a
guarantee, and it is reported in the pass record beside the corpus's own.


WHY THE STEM IS THE IDENTITY
-----------------------------
``oncotriage/batch/runner.py``'s checkpoint keys on ``Path(f).stem`` and its
own docstring says so ("This is consistent with how pending_files and
completed_files are filtered, avoiding UUID vs. stem mismatch bugs"). Using the
same identity here means the cohort, the checkpoint and the resample pass all
speak about patients the same way, with no mapping to get wrong -- and it costs
no parse, which (3) above is about.

    NOT THE FULL PATH, which carries the machine's directory layout and would
    make the draw machine-dependent. Not the ``patient_id``, which needs a
    parse; the corpus's stems do END in it, but that is a Synthea filename
    convention rather than a contract and nothing here relies on it.


WHAT IMPORTING THIS MODULE DOES
--------------------------------
Nothing. It resolves no path, opens no file, builds no registry and reads no
corpus: every function takes the file list from its caller. That is what makes
the whole of it drivable offline, and it is why ``oncotriage.paths`` is
deliberately NOT imported here.
"""

import hashlib
import os

from oncotriage.config import (
    CAMPAIGN_COHORT_SEED,
    CAMPAIGN_COHORT_SIZE,
    CAMPAIGN_JUDGE_SAMPLE_SIZE,
    CAMPAIGN_JUDGE_SEED,
    CAMPAIGN_STABILITY_SAMPLE_SIZE,
    CAMPAIGN_STABILITY_SEED,
)


#------------------------------------------------------------------------------


# ===========================================================================
# THE ALGORITHM, AS A PUBLISHED STRING
# ===========================================================================

DRAW_ALGORITHM = "sha256(seed|stem) ascending rank, lowest k, tie-break on stem"
"""The one-line statement of ``draw()``, recorded in every artefact.

READ BY THE RECORD RATHER THAN ONLY BY A HUMAN. ``COLLECTION_IDENTITY``'s
argument in ``oncotriage/run_fingerprint.py``: a value a consumer writes into
its own artefact cannot go stale silently, whereas a sentence in a docstring
can. A reader who has the seed, the size, this string and the file list has
everything needed to recompute the membership.
"""

DIGEST_ALGORITHM = "sha256 over the newline-joined sorted stems"
"""The one-line statement of ``digest()``. Recorded for ``DRAW_ALGORITHM``'s
reason: a digest whose construction is not stated is a number nobody can check.
"""

DIGEST_CHARS = 16
"""How much of the hex digest the recorded value keeps.

TRUNCATED, AND THE TRUNCATION IS ARGUED. This value is compared for EQUALITY by
a resume gate and printed on a refusal line an operator reads; 64 characters of
hex on a console is not read, it is skipped. 16 hex characters is 64 bits, and
the thing it must not do is collide between two cohorts an operator might
plausibly draw in one programme -- a birthday bound of about 2**32 distinct
cohorts, against a programme that draws three. It is NOT a security property
and nothing here defends against a chosen-prefix attack: the input is a list of
filenames this project generated.

``run_fingerprint.summary`` already truncates the renderer digest to 12 for the
same reason; this keeps the FULL recorded value at 16 rather than truncating at
print time, because the checkpoint's guard compares what it stored.
"""


# ===========================================================================
# THE THREE DRAWS ARE INDEPENDENT, WHICH REQUIRES THREE DISTINCT SEEDS
# ===========================================================================
#
# NOT DECORATION AND NOT SUPERSTITION. ``rank_key`` is a pure function of
# ``(seed, stem)``, so two draws from ONE population under ONE seed produce the
# SAME ranking -- and the smaller of the two is then a strict PREFIX of the
# larger. With all three seeds equal the 50-patient stability sample would be a
# SUBSET of the 100-patient judge sample, every time, with 100% overlap where an
# independent pair expects 50 * 100 / 300 = 16.7. Every judged patient would
# also be a re-run patient, and the judge's agreement rate would be measured
# entirely on the sub-population selected for stability.
#
# THE GUARD IS AT IMPORT AND IS A RuntimeError, NOT AN ``assert`` -- `python -O`
# deletes asserts, and this is a correctness guard rather than a debugging aid.
# It follows `RUN_STOP_REASONS`' duplicate check in
# oncotriage/storage/database_logger.py, which is the same shape one module over.
#
# THE COHORT SEED IS ALLOWED TO EQUAL EITHER SAMPLE SEED and only the two SAMPLE
# seeds are required to differ, because the two samples are drawn from one
# population (the cohort) while the cohort is drawn from a different one (the
# corpus). Sharing a seed across two different populations produces two
# rankings over two different key spaces, which is not the nesting above.
# MEASURED rather than assumed: the shipped seeds are checked in the pass
# record, and the realised overlap is reported there.
if CAMPAIGN_STABILITY_SEED == CAMPAIGN_JUDGE_SEED:
    raise RuntimeError(
        f"CAMPAIGN_STABILITY_SEED and CAMPAIGN_JUDGE_SEED are both "
        f"{CAMPAIGN_STABILITY_SEED!r}. Both samples are drawn from the SAME "
        f"population (the campaign cohort) by the same rank function, so one "
        f"seed makes the smaller sample a strict subset of the larger -- the "
        f"two are then not independent and the overlap the programme reports "
        f"is a property of the seeds rather than of chance. Give them "
        f"different values in oncotriage/config.py.")


#------------------------------------------------------------------------------


# ===========================================================================
# THE PURE DRAW
# ===========================================================================

def stem_of(path) -> str:
    """The filename stem of a FHIR bundle path -- the runner's patient identity.

    ``os.path.splitext(os.path.basename(p))[0]`` rather than ``Path(p).stem``,
    which is the same value; the difference is that this module does not import
    ``pathlib`` for one call and the caller may hand a ``Path`` or a ``str``
    either way, because ``os.path.basename`` accepts both.
    """
    return os.path.splitext(os.path.basename(str(path)))[0]


def rank_key(seed, stem: str) -> tuple:
    """``(sha256(seed|stem), stem)`` -- the sort key ``draw`` orders on.

    THE SECOND MEMBER MAKES THE ORDERING TOTAL BY CONSTRUCTION rather than by
    sort stability, which is the lesson
    ``oncotriage/extraction/stage.py``'s observation sort had to learn: an
    ordering that ties and then relies on the input order is an ordering that
    depends on how the caller happened to build its list. Two distinct stems
    cannot tie here at all -- a full sha256 collision would be needed -- so the
    tie-break is unreachable and is present so that the ordering does not
    DEPEND on that being true.

    THE SEED IS STRINGIFIED, NOT COERCED TO int. An operator who sets a string
    seed gets a working, recomputable draw rather than a TypeError, and the
    recorded value is what was used. ``rater.select_retest_decisions`` builds
    its key the same way, with ``"%s|..." % seed``.
    """
    return (hashlib.sha256(
        ("%s|%s" % (seed, stem)).encode("utf-8")).hexdigest(), stem)


def draw(stems, size, seed) -> list:
    """The lowest ``size`` stems by ``rank_key``, returned SORTED BY STEM.

    Args:
        stems: the population. Duplicates are a caller defect and raise --
            see below.
        size:  how many to take. ``None`` means "all of them", which is what a
            caller with no configured size gets; a size at or above the
            population size takes the whole population, which is the ONLY
            reason a corpus smaller than the ruled cohort still runs.
        seed:  recorded verbatim and stringified into the rank key.

    Returns:
        A new list. The ORDER IS BY STEM and not by rank, because the order a
        draw is returned in becomes the order patients are processed in, and
        rank order is an artefact of the seed -- two seeds drawing the same set
        would process it in two different orders and produce two different
        interleavings in the ``inferences`` table for no reason. Sorting also
        makes ``draw(...) == draw(...)`` a comparison of SETS spelled as lists.

    A DUPLICATE STEM RAISES rather than being de-duplicated silently. Two
    bundles whose paths differ and whose stems do not are two patients this
    pipeline cannot tell apart: the checkpoint would record one completion for
    both, the second would be skipped on every resume, and the cohort would be
    one patient short of the count it reports. De-duplicating would hide that;
    raising names it at the one point in the run where it is cheap to fix.
    """
    stems = list(stems)
    if len(set(stems)) != len(stems):
        seen, repeats = set(), []
        for s in stems:
            if s in seen and s not in repeats:
                repeats.append(s)
            seen.add(s)
        raise ValueError(
            f"the population carries {len(stems) - len(set(stems))} duplicate "
            f"stem(s) -- {repeats[:5]!r}{' ...' if len(repeats) > 5 else ''}. "
            f"Two bundles that share a filename stem are one patient to the "
            f"checkpoint, which keys on the stem, so the cohort would be short "
            f"of the count it reports and every resume would skip one of them.")
    if size is None or size >= len(stems):
        return sorted(stems)
    if size < 0:
        raise ValueError(f"draw size must not be negative; got {size!r}")
    ranked = sorted(stems, key=lambda s: rank_key(seed, s))
    return sorted(ranked[:size])


def digest(stems) -> str:
    """A ``DIGEST_CHARS``-character fingerprint of a MEMBERSHIP.

    Sorted before hashing, so the digest is a property of the SET and not of
    the order a caller happened to build it in -- which is what lets the same
    cohort computed by two different code paths compare equal.

    THE SEPARATOR IS A NEWLINE AND THE STEMS CANNOT CONTAIN ONE, because they
    are filename components. A separator a member could contain would let two
    different sets hash the same.
    """
    joined = "\n".join(sorted(stems))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:DIGEST_CHARS]


#------------------------------------------------------------------------------


# ===========================================================================
# THE SELECTION
# ===========================================================================

class CohortSelection(object):
    """One campaign's cohort and its two programme samples, with provenance.

    IMMUTABLE BY CONVENTION rather than by machinery: every attribute is set in
    ``__init__`` and nothing here mutates one. ``files`` is handed straight to
    the runner, which does not write to it.
    """

    __slots__ = ("files", "stems", "corpus_size",
                 "seed", "requested_size", "size", "digest",
                 "stability_stems", "stability_seed", "stability_size",
                 "judge_stems", "judge_seed", "judge_size")

    def __init__(self, files, stems, corpus_size, seed, requested_size, digest_,
                 stability_stems, stability_seed,
                 judge_stems, judge_seed):
        self.files = files
        self.stems = stems
        self.corpus_size = corpus_size
        self.seed = seed
        self.requested_size = requested_size
        self.size = len(stems)
        self.digest = digest_
        self.stability_stems = stability_stems
        self.stability_seed = stability_seed
        self.stability_size = len(stability_stems)
        self.judge_stems = judge_stems
        self.judge_seed = judge_seed
        self.judge_size = len(judge_stems)

    # ── WHAT THE CAMPAIGN RECORDS ──────────────────────────────────────────

    def record(self) -> dict:
        """The provenance a consumer persists. Plain JSON-able scalars only.

        THE MEMBERSHIP ITSELF IS NOT IN IT, deliberately. Three hundred stems
        is 15 kB of Synthea patient names in a ``runs`` row and in every
        checkpoint write -- once per completed patient -- and the membership is
        RECOMPUTABLE from the four scalars that are here plus the corpus. What
        the record carries is what makes that recomputation checkable: the
        algorithm, the seed, the size and the digest of the answer.

        ``requested_size`` AND ``size`` ARE BOTH HERE AND THEY ARE DIFFERENT
        FACTS. The first is what the configuration asked for; the second is
        what the corpus could supply. A campaign whose corpus is smaller than
        the ruled cohort reports ``requested 300, selected 240`` rather than
        silently reporting 240 as though that had been the plan.
        """
        return {
            "algorithm":           DRAW_ALGORITHM,
            "digest_algorithm":    DIGEST_ALGORITHM,
            "corpus_size":         self.corpus_size,
            "cohort_seed":         self.seed,
            "cohort_requested":    self.requested_size,
            "cohort_size":         self.size,
            "cohort_digest":       self.digest,
            "stability_seed":      self.stability_seed,
            "stability_size":      self.stability_size,
            "stability_digest":    digest(self.stability_stems),
            "judge_seed":          self.judge_seed,
            "judge_size":          self.judge_size,
            "judge_digest":        digest(self.judge_stems),
            "sample_overlap":      len(set(self.stability_stems)
                                       & set(self.judge_stems)),
        }

    def describe(self) -> list:
        """The console block, as lines. One text, however many callers.

        ``spend.report_lines`` and ``run_fingerprint.refusal_lines``' shape: a
        consumer prints these rather than composing its own, so two callers
        cannot come to describe one cohort two ways.
        """
        overlap = len(set(self.stability_stems) & set(self.judge_stems))
        lines = [
            f"[Cohort] {self.size} of {self.corpus_size} patients "
            f"(requested {self.requested_size}, seed {self.seed!r}, "
            f"digest {self.digest})",
            f"[Cohort] draw: {DRAW_ALGORITHM}",
            f"[Cohort] stability sample (k=2 re-run): {self.stability_size} "
            f"patients, seed {self.stability_seed!r}, "
            f"digest {digest(self.stability_stems)}",
            f"[Cohort] judge sample: {self.judge_size} patients, "
            f"seed {self.judge_seed!r}, digest {digest(self.judge_stems)}",
            f"[Cohort] the two samples are independent draws from the cohort; "
            f"they overlap in {overlap} patient(s), which is chance and is "
            f"neither forced nor prevented",
        ]
        if self.size < self.requested_size:
            lines.append(
                f"[Cohort] SHORT: the corpus offers {self.corpus_size} "
                f"patient(s) and the configured cohort is "
                f"{self.requested_size}. Every available patient was selected; "
                f"no number computed over this run is over the ruled cohort.")
        # A SATURATED SAMPLE IS A COST STATEMENT AND IT IS EASY TO MISS. A
        # stability sample at or above the cohort's size re-runs EVERY patient,
        # which doubles the campaign's Stage 5 spend -- and it happens silently
        # whenever the cohort is smaller than the configured sample, which is
        # the ordinary state of a smoke corpus. `draw()` taking all of a short
        # population is correct; not saying so is not.
        if self.stability_size >= self.size and self.size:
            lines.append(
                f"[Cohort] NOTE: the stability sample is the WHOLE cohort "
                f"({self.stability_size} of {self.size}), so every patient is "
                f"run twice and this campaign costs about double.")
        return lines


def select(fhir_files,
           size=None, seed=None,
           stability_size=None, stability_seed=None,
           judge_size=None, judge_seed=None) -> CohortSelection:
    """Draw the campaign cohort and both programme samples from a file list.

    Args:
        fhir_files: every bundle path the corpus offers, in any order.
        size / seed: the cohort draw. ``None`` -- what every shipped caller
            passes -- reads ``config.CAMPAIGN_COHORT_SIZE`` and
            ``config.CAMPAIGN_COHORT_SEED``.
        stability_size / stability_seed, judge_size / judge_seed: the two
            samples, defaulting to their own config constants the same way.

    THE DEFAULTS ARE RESOLVED HERE AND NOWHERE ELSE, which is what keeps the
    runner cohort-blind: ``oncotriage/batch/runner.py`` calls this with the file
    list and nothing else, so no number in the ruled programme -- 300, 50, 100 --
    appears anywhere outside ``oncotriage/config.py`` and this module.

    THE ARGUMENTS EXIST FOR TESTS AND FOR A FUTURE FLAG, not for the campaign.
    They are keyword-only in effect (every caller passes by name) and each
    ``None`` means "read the constant", which is the same convention
    ``sampling.default_output_db(total=None)`` uses.

    Returns:
        A ``CohortSelection``. ``files`` is the subset of ``fhir_files`` whose
        stems were drawn, SORTED BY PATH -- which is byte-identical to what the
        runner's own ``sorted(glob.glob(...))`` produced for the whole corpus,
        so the processing order of a full-corpus run is unchanged.

    THE SAMPLES ARE DRAWN FROM THE COHORT, NOT FROM THE CORPUS. That is the
    ruling and it is also the only reading that is meaningful: a stability
    sample containing a patient the campaign never ran has nothing to be
    stable about, and a judge sample containing one has no verdicts to rate.
    """
    size = CAMPAIGN_COHORT_SIZE if size is None else size
    seed = CAMPAIGN_COHORT_SEED if seed is None else seed
    stability_size = (CAMPAIGN_STABILITY_SAMPLE_SIZE if stability_size is None
                      else stability_size)
    stability_seed = (CAMPAIGN_STABILITY_SEED if stability_seed is None
                      else stability_seed)
    judge_size = (CAMPAIGN_JUDGE_SAMPLE_SIZE if judge_size is None
                  else judge_size)
    judge_seed = CAMPAIGN_JUDGE_SEED if judge_seed is None else judge_seed

    by_stem = {}
    for path in fhir_files:
        by_stem.setdefault(stem_of(path), []).append(path)
    # THE DUPLICATE CHECK IS `draw`'s AND IS REACHED THROUGH IT rather than
    # duplicated here: this dict would silently collapse a repeat, so the
    # population handed to `draw` is built from the ORIGINAL list.
    population = [stem_of(p) for p in fhir_files]

    stems = draw(population, size, seed)
    files = sorted(by_stem[s][0] for s in stems)

    return CohortSelection(
        files=files,
        stems=stems,
        corpus_size=len(population),
        seed=seed,
        requested_size=size,
        digest_=digest(stems),
        stability_stems=draw(stems, stability_size, stability_seed),
        stability_seed=stability_seed,
        judge_stems=draw(stems, judge_size, judge_seed),
        judge_seed=judge_seed,
    )


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep  1 2026

@author: ramyalsaffar
"""
