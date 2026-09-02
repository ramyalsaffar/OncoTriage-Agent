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


WHY THE DRAW IS STRATIFIED BY CANCER GROUP, AND WHAT THAT COSTS
----------------------------------------------------------------
IT WAS SIMPLE RANDOM AND THE OPERATOR OVERRULED THAT, FOR COVERAGE. The whole
of the previous argument is kept below, because three of its four reasons are
still TRUE and are now COSTS this module pays rather than reasons it declines:

1.  **A STRATIFIED MEMBERSHIP IS NOT RECOMPUTABLE FROM THE SEED ALONE.** A
    group key needs ``CancerCodeRegistry``: SNOMED exact, then ICD-10-CM 2024
    exact through the ``icd10-cm`` package, then a display-term morphology
    fallback. ``oncotriage/run_fingerprint.py:RENDERER_COVERAGE`` states that
    the registry's DATA "is outside the repository entirely and could not be
    hashed from source at any granularity" -- so a reader on another machine
    with a different ``icd10-cm`` release computes different STRATA from the
    same seed and therefore a different cohort.

        THE RESIDUAL, STATED HONESTLY RATHER THAN ENGINEERED AROUND. A reader
        recomputing a membership needs the file list, the seed, the size, the
        algorithm below AND THE SAME REGISTRY DATA. What proves that a GIVEN
        RUN used a GIVEN MEMBERSHIP is not the recomputation -- it is
        ``cohort_digest``, which the run row and the checkpoint already carry
        and which the batch runner's resume gate already compares. That column
        is what turns "you can probably rebuild this" into "this run
        demonstrably ran that set", and it does not depend on the registry.

        WITHIN A STRATUM THE DRAW IS STILL sha256 RANK, not
        ``random.Random``. The registry decides the BUCKETS; nothing about the
        interpreter decides the ORDER inside one. So the part that can be made
        machine-independent is, and the part that cannot is named.

2.  **THE POPULATION IS ITSELF A SIMPLE RANDOM DRAW.**
    ``oncotriage/fhir/clean.py`` step 3 caps the corpus with
    ``rng.sample(remaining_files, COHORT_CAP)`` -- unstratified. So stratifying
    here fixes the cohort's marginals to the CORPUS's marginals, which are one
    random realisation, rather than to the population's. The cohort's group
    shares are therefore a statement about the corpus and not about Synthea's
    generator, and the pass record reports both side by side.

3.  **IT COSTS A FULL PARSE BEFORE THE FIRST PATIENT.** A group key needs the
    parsed bundle. ``oncotriage/evaluation/cohort_groups.py`` is that parse,
    measured at roughly three minutes for 1,000 bundles plus the ICD-10-CM
    build, and it is work the runner otherwise did lazily inside the pool.

4.  What the paper claims is now "a seeded sample of size N, stratified
    proportionally by primary cancer group and hash-ranked within each
    stratum", with the group key named and the digest published.

    THIS MODULE STAYS PURE AND THE GROUPING ARRIVES AS AN ARGUMENT.
    ``select(..., group_of=callable)``. With ``group_of=None`` the draw is
    simple random and the selection RECORDS that it was -- so nothing here
    imports the parser or the registry, every function is still drivable
    offline with a fabricated population, and a caller that cannot resolve
    groups gets a working cohort and a record saying it is unstratified rather
    than a crash or a silent stratification over one bucket.


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
# independent pair expects 50 * 100 / 500 = 10 (the shipped seeds realise 16 on
# the current corpus -- MEASURED, and chance). Every judged patient would
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


STRATIFIED_DRAW_ALGORITHM = (
    "proportional largest-remainder allocation by cancer group "
    "(minimum 1 per non-empty group), then "
    "sha256(seed|stem) ascending rank within each group")
"""The one-line statement of ``stratified_draw()``, recorded in every artefact.

``DRAW_ALGORITHM``'s argument, for the stratified arm: a value a consumer
writes into its own artefact cannot go stale silently, whereas a sentence in a
docstring can. ``CohortSelection.record()`` writes whichever of the two
actually ran, so an artefact never claims an algorithm the draw did not use.
"""

MINIMUM_PER_GROUP = 1
"""How many members a non-empty stratum is guaranteed, budget permitting.

THIS IS THE COVERAGE THE STRATIFICATION WAS RULED FOR. Pure proportional
allocation drops a group whose share rounds below 0.5 -- on this corpus that is
lung at 1.6%, which would vanish from a cohort of 30 and be represented by
noise at 500. A floor of 1 is also what
``oncotriage/ablation/study.py:stratified_sample`` has always applied, so the
two draws in this project now agree on the rule rather than on nothing.

IT IS NOT ALWAYS AFFORDABLE and the allocator says so rather than raising: when
the requested size is smaller than the number of non-empty groups, the floor is
granted to the LARGEST groups first (population descending, name ascending) and
the smallest groups get nothing. That is a real state for a smoke corpus and it
is reported in ``CohortSelection.describe()``, never silent.
"""


def allocate_proportional(counts, size, minimum=MINIMUM_PER_GROUP) -> dict:
    """How many members each stratum contributes. A pure function of its inputs.

    Args:
        counts:  ``{group: population size}``. Empty groups may be present and
                 are allocated 0; they cost nothing and letting a caller pass
                 the whole vocabulary keeps its own code branch-free.
        size:    the total to allocate. ``None`` or a value at or above the
                 population means "everyone", returned as ``counts`` itself.
        minimum: the per-group floor. See ``MINIMUM_PER_GROUP``.

    Returns:
        ``{group: allocation}`` for every key of ``counts``, summing to
        ``min(size, sum(counts.values()))``.

    LARGEST REMAINDER, NOT A ROUNDED SHARE, AND NOT AN RNG. Rounding each
    group's share independently -- which is what
    ``oncotriage/ablation/study.py:stratified_sample`` does -- can overshoot or
    undershoot the target by several patients, and that study then TRIMS the
    overshoot with a shuffle. A trim is a second random draw layered on a
    stratified one: it can empty a stratum the stratification just guaranteed,
    and it makes the realised allocation depend on the seed as well as on the
    populations. Largest remainder hits the target exactly, by construction,
    with no rng at all -- so the allocation is a function of ``(counts, size)``
    and the seed decides only WHICH members of each group are taken.

    EVERY TIE IS BROKEN ON THE GROUP NAME, ASCENDING. Two groups with an equal
    fractional remainder is the ordinary case at round numbers, and a tie
    resolved by dict order is a tie resolved by how the caller happened to build
    its input. The lesson ``oncotriage/extraction/stage.py``'s observation sort
    had to learn, one module over.
    """
    counts = {g: int(n) for g, n in counts.items()}
    if any(n < 0 for n in counts.values()):
        raise ValueError(f"group populations must not be negative; got {counts!r}")
    total = sum(counts.values())
    if size is None or size >= total:
        return dict(counts)
    if size < 0:
        raise ValueError(f"draw size must not be negative; got {size!r}")

    non_empty = [g for g in sorted(counts) if counts[g] > 0]
    alloc = {g: 0 for g in counts}
    if not non_empty or size == 0:
        return alloc

    # 1. The floor, largest population first so that a size smaller than the
    #    number of groups represents the biggest ones rather than whichever
    #    sorted first.
    remaining = size
    for g in sorted(non_empty, key=lambda k: (-counts[k], k)):
        if remaining <= 0:
            break
        take = min(minimum, counts[g], remaining)
        alloc[g] = take
        remaining -= take

    # 2. The proportional body, floored, capped by capacity.
    ideal = {g: size * counts[g] / total for g in non_empty}
    for g in sorted(non_empty):
        if remaining <= 0:
            break
        want = int(ideal[g]) - alloc[g]
        if want <= 0:
            continue
        take = min(want, counts[g] - alloc[g], remaining)
        alloc[g] += take
        remaining -= take

    # 3. The remainder, one at a time, by largest fractional part then name.
    #    Repeated rounds because a group at capacity is skipped and its slot
    #    must go somewhere; the loop terminates because every round either
    #    spends a slot or finds no capacity anywhere.
    while remaining > 0:
        order = sorted(
            (g for g in non_empty if alloc[g] < counts[g]),
            key=lambda k: (-(ideal[k] - int(ideal[k])), k))
        if not order:
            break
        for g in order:
            if remaining <= 0:
                break
            alloc[g] += 1
            remaining -= 1
    return alloc


def stratified_draw(stems, size, seed, group_of, minimum=MINIMUM_PER_GROUP):
    """``size`` stems, proportional by group, hash-ranked inside each group.

    Args:
        stems:    the population. Duplicates raise, through ``draw``.
        size:     how many to take. ``None`` or >= the population takes all.
        seed:     recorded verbatim and stringified into the rank key.
        group_of: ``stem -> group name``. Called once per stem. It MUST be
                  total: a grouper that raises takes the cohort selection with
                  it, above the first billed call, which is where that failure
                  is cheapest.
        minimum:  the per-group floor. See ``MINIMUM_PER_GROUP``.

    Returns:
        ``(stems, group_counts)`` -- the drawn stems SORTED BY STEM for
        ``draw``'s reason (processing order must not be an artefact of the
        seed), and ``{group: how many were drawn}`` for every group the
        POPULATION had, including the ones that got zero. A group present in
        the population and absent from the draw is a fact a reader needs and an
        omitted key is not one.

    THE DUPLICATE CHECK IS ``draw``'s AND IS REACHED THROUGH IT, once per
    stratum, so the two arms cannot come to disagree about what a duplicate
    stem means. A duplicate WITHIN one group raises there; a stem cannot be in
    two groups, because ``group_of`` is a function.
    """
    stems = list(stems)
    # The whole-population duplicate check first: two identical stems landing in
    # ONE group would be caught by draw() below, but the message would name a
    # stratum rather than the corpus, and a caller whose grouper is not a
    # function would be told nothing at all.
    draw(stems, 0, seed)

    buckets = {}
    for s in stems:
        buckets.setdefault(group_of(s), []).append(s)

    counts = {g: len(v) for g, v in buckets.items()}
    alloc = allocate_proportional(counts, size, minimum=minimum)

    selected = []
    for g in sorted(buckets):
        selected.extend(draw(buckets[g], alloc[g], seed))
    return sorted(selected), {g: alloc[g] for g in sorted(counts)}


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
                 "stratified", "group_counts", "corpus_group_counts",
                 "_group_of",
                 "stability_stems", "stability_seed", "stability_size",
                 "judge_stems", "judge_seed", "judge_size")

    def __init__(self, files, stems, corpus_size, seed, requested_size, digest_,
                 stability_stems, stability_seed,
                 judge_stems, judge_seed,
                 stratified=False, group_counts=None,
                 corpus_group_counts=None, group_of=None):
        self.files = files
        self.stems = stems
        self.corpus_size = corpus_size
        self.seed = seed
        self.requested_size = requested_size
        self.size = len(stems)
        self.digest = digest_
        # WHETHER THE DRAW WAS STRATIFIED IS A RECORDED FACT AND NOT AN
        # INFERENCE FROM `group_counts` BEING NON-EMPTY. A caller can supply a
        # grouper that answers one bucket for everybody, and that is a
        # stratified draw over a degenerate partition rather than a simple
        # random one; the two are different provenance and the record says
        # which happened.
        self.stratified = bool(stratified)
        self.group_counts = dict(group_counts or {})
        self.corpus_group_counts = dict(corpus_group_counts or {})
        # KEPT SO A SUBSAMPLE OF THIS COHORT USES THIS COHORT'S GROUPING. It is
        # the only mutable-ish thing on the object and it is a callable the
        # caller already owns; `subsample` is the one reader.
        self._group_of = group_of
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
            "algorithm":           (STRATIFIED_DRAW_ALGORITHM if self.stratified
                                    else DRAW_ALGORITHM),
            "stratified":          self.stratified,
            "group_counts":        dict(self.group_counts),
            "corpus_group_counts": dict(self.corpus_group_counts),
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
            f"[Cohort] draw: "
            f"{STRATIFIED_DRAW_ALGORITHM if self.stratified else DRAW_ALGORITHM}",
            f"[Cohort] stability sample (k=2 re-run): {self.stability_size} "
            f"patients, seed {self.stability_seed!r}, "
            f"digest {digest(self.stability_stems)}",
            f"[Cohort] judge sample: {self.judge_size} patients, "
            f"seed {self.judge_seed!r}, digest {digest(self.judge_stems)}",
            f"[Cohort] the two samples are independent draws from the cohort; "
            f"they overlap in {overlap} patient(s), which is chance and is "
            f"neither forced nor prevented",
        ]
        if self.stratified:
            # THE SHARES ARE THE POINT OF STRATIFYING, so they are printed
            # rather than only recorded: the cohort's share beside the
            # corpus's is what says the draw did what it claims.
            for g in sorted(self.corpus_group_counts):
                pop = self.corpus_group_counts[g]
                got = self.group_counts.get(g, 0)
                corpus_share = (pop / self.corpus_size) if self.corpus_size else 0.0
                cohort_share = (got / self.size) if self.size else 0.0
                lines.append(
                    f"[Cohort]   {g:<13s} {got:>4d} of {pop:>4d}  "
                    f"cohort {cohort_share:6.2%} vs corpus {corpus_share:6.2%}")
            # A GROUP THE FLOOR COULD NOT AFFORD IS NAMED. See
            # MINIMUM_PER_GROUP: at a size below the number of non-empty
            # groups the floor is granted largest-first and the rest get zero,
            # which is a real state for a smoke corpus and must not be silent.
            _dropped = sorted(g for g, pop in self.corpus_group_counts.items()
                              if pop > 0 and not self.group_counts.get(g, 0))
            if _dropped:
                lines.append(
                    f"[Cohort] NOTE: {len(_dropped)} non-empty group(s) "
                    f"contributed no patient -- {', '.join(_dropped)}. The "
                    f"cohort is smaller than the number of groups the corpus "
                    f"holds, so the per-group floor could not be granted to "
                    f"all of them.")
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


    def subsample(self, size, seed, group_of=None, minimum=None):
        """A further draw FROM THIS COHORT, stratified by this cohort's grouper.

        THE ABLATION STUDY'S SAMPLE IS THIS. The ruling is that the study's
        patients come out of the campaign cohort rather than out of the corpus,
        so a configuration's mean is measured over patients the campaign
        actually ran; drawing from the corpus would let a study report on
        patients no campaign has a verdict for.

        Args:
            size:     how many. ``None`` or >= the cohort takes all of it.
            seed:     recorded verbatim. It MUST differ from the two programme
                      sample seeds for the reason argued at the import guard
                      above -- one seed over one population makes the smaller
                      draw a prefix of the larger -- and this method does not
                      enforce that, because it does not know what else the
                      caller has drawn. The constant that names it does.
            group_of: ``stem -> group``. ``None`` reuses the grouper this
                      cohort was drawn with, which is what makes "the same
                      grouper" true by construction rather than by a caller
                      remembering to pass the same callable twice.
            minimum:  the per-group floor; ``None`` is ``MINIMUM_PER_GROUP``.

        Returns:
            ``(stems, group_counts)``, exactly ``stratified_draw``'s shape --
            or ``(stems, {})`` when there is no grouper at all, in which case
            the draw is simple random and the EMPTY DICT is what says so.
        """
        grouper = self._group_of if group_of is None else group_of
        if grouper is None:
            return draw(self.stems, size, seed), {}
        return stratified_draw(
            self.stems, size, seed, grouper,
            minimum=MINIMUM_PER_GROUP if minimum is None else minimum)


def select(fhir_files,
           size=None, seed=None,
           stability_size=None, stability_seed=None,
           judge_size=None, judge_seed=None,
           group_of=None) -> CohortSelection:
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

    # THE STRATIFIED ARM IS TAKEN ONLY WHEN A GROUPER IS SUPPLIED, and the
    # unstratified arm is not a fallback that hides a failure: a caller that
    # cannot resolve groups -- a test, an embedder, a smoke run with no
    # registry -- gets a working cohort whose RECORD says it was simple random.
    # See the module header for why the grouping arrives as an argument rather
    # than being resolved here.
    if group_of is None:
        stems = draw(population, size, seed)
        group_counts = {}
        corpus_group_counts = {}
    else:
        stems, group_counts = stratified_draw(population, size, seed, group_of)
        corpus_group_counts = {}
        for s in population:
            g = group_of(s)
            corpus_group_counts[g] = corpus_group_counts.get(g, 0) + 1
    files = sorted(by_stem[s][0] for s in stems)

    # THE TWO PROGRAMME SAMPLES ARE UNCHANGED AND ARE SIMPLE RANDOM DRAWS FROM
    # THE COHORT. That is the ruling and it is also the reading that costs
    # least: the cohort's own marginals are already fixed to the corpus's by
    # the stratification above, so a sample of it inherits those proportions in
    # expectation, and stratifying twice would fix the SAMPLE's marginals to
    # the COHORT's realisation -- the objection reason (2) in the module header
    # makes about stratifying a population that is itself a draw, one level in.
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
        stratified=group_of is not None,
        group_counts=group_counts,
        corpus_group_counts=corpus_group_counts,
        group_of=group_of,
    )


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep  1 2026

@author: ramyalsaffar
"""
