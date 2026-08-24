# Run Configuration Fingerprint
###############################

"""What configuration produced a partial artifact, and whether this run may
continue it.

THE PROBLEM THIS EXISTS FOR. Three paid harnesses persist partial state and
resume from it: ``oncotriage/evaluation/run_harness.py`` (a manifest plus one
JSON per patient), ``oncotriage/batch/runner.py`` (a set of completed filename
stems) and ``oncotriage/ablation/study.py`` (a set of completed
``(config, patient)`` pairs). Every one of them recorded WHAT was done and none
of them recorded WHAT IT WAS DONE UNDER. So a resume after a prompt edit, a
model change or an index rebuild silently produced one artifact holding two
configurations' results, with nothing in it saying so -- and every mean,
every rate and every comparison computed over it is a number about nothing.

The fix is one stamp, compared before anything is skipped and before anything
is spent. It is deliberately ONE MODULE with three callers rather than three
comparisons, on this project's own repeated finding: two copies of a rule that
must agree are two copies that can disagree, and here disagreement means one
harness refuses a resume while another accepts the same one.

WHAT IS IN THE STAMP, AND WHY EACH FIELD IS THERE
-------------------------------------------------
    llm_classifier_prompt_version   a prompt edit moves every Stage 5 verdict.
                                    It is the one input that is neither a
                                    tunable, a model nor an index. HAND-
                                    MAINTAINED, and therefore capable of being
                                    wrong -- which is what the field below it
                                    exists for.
    llm_classifier_renderer_digest  the identity of the CODE that renders the
                                    Stage 5 prompt, derived from its source
                                    rather than declared by a person. The
                                    convention is that a renderer change bumps
                                    PROMPT_VERSION; nothing enforced the
                                    convention, so an edit to
                                    ``_create_patient_summary``, to a temporal
                                    helper or to the stage extractor that
                                    forgot the bump changed every rendered
                                    prompt while this gate saw nothing and the
                                    resumed run mixed two eras. See
                                    RENDERER_MODULES for what is hashed,
                                    RENDERER_DIGEST_ALGORITHM for how, and
                                    RENDERER_COVERAGE for what it does NOT
                                    cover.
    matching_model_configured       the model REQUESTED. What answered is
                                    recorded per call and can legitimately be a
                                    dated snapshot of the same alias; what was
                                    asked for is what a resume would ask for
                                    again.
    matching_call_mode              HOW Stage 5 is called: one request carrying
                                    several trials, or one request per trial.
                                    The same judge answering the same trials in
                                    two arms does not answer them the same way
                                    -- a per-trial call sees one trial's
                                    criteria in its whole context and cannot
                                    omit a trial from a batch it was never sent
                                    -- so the two arms are not commensurable
                                    and their patients must not be summed.
                                    Nothing else in this stamp moves with it:
                                    the wire model is the same id in both.
    qdrant_collection               the RESOLVED backing collection, never the
                                    alias. The alias is a constant -- that is
                                    what an alias is for -- so a gate on it is a
                                    gate that can never fire.
    collection_points               how many points were in that collection.
                                    See COLLECTION IDENTITY below: this is the
                                    weaker half of the pair, deliberately, and
                                    it is what catches a re-index in place.
    data_snapshot_date              patient age is computed against it, age
                                    drives Stage 4's age filter, and the filter
                                    decides which trials survive to be judged.
                                    Two eras of this constant are two different
                                    cohorts wearing one cohort's name.

WHAT IS DELIBERATELY NOT IN IT, each with its reason. ``collection_alias``: an
alias may be repointed at the SAME backing collection, in which case the two
runs are comparable and a gate on the alias name would refuse them; the
resolved name is the fact. ``age_reference_date``: a pure function of
``data_snapshot_date``, so gating both would be one fact counted twice, and the
one that is a configured constant is the one to gate. The tunables:
``fixture_replay.py:diff_tunables()`` already reports a tunable change on every
replay, and the three consumers here have no equivalent -- recorded as an open
limitation below rather than half-implemented here.

COLLECTION IDENTITY IS NAME PLUS POINT COUNT, AND THAT IS WEAKER THAN THE
FIXTURE HARNESS'S GATE
-------------------------------------------------------------------------
``oncotriage/fixtures/capture.py:compute_collection_digest()`` scrolls every
point's ``nct_id`` and hashes the sorted set, which catches a same-count
content swap. This does not. It compares the resolved NAME (which catches the
weekly alias swap, the overwhelmingly common case) and the POINT COUNT (which
catches a partial re-index and a deleted-trial run). What it misses is a
collection rebuilt in place to exactly the same size with different contents.

The gate is the weaker one on purpose and the reason is layering, not cost.
``compute_collection_digest`` lives in ``oncotriage/fixtures/capture.py``, and
two of this module's three consumers may not import it: ``oncotriage/batch``
and ``oncotriage/ablation`` are production and experiment code, and making
either depend on the characterization-fixture harness -- which imports the
agent, the parser and the storage layer -- is the wrong direction for a
dependency. Moving the digest to a neutral module is the right end state and it
is a refactor with its own equivalence proof; mixing a relocation into a gate
pass is what makes an equivalence proof stop meaning anything.

SO THE LIMITATION IS STATED WHEREVER THE GATE'S ANSWER IS: in
``COLLECTION_IDENTITY``, which every consumer writes into its own artifact, and
in the refusal text. A weaker gate that says it is weaker is a gate; a weaker
gate that does not is a claim.

``llm_classifier_renderer_digest`` HAS A COVERAGE BOUNDARY OF ITS OWN and it is
stated the same way, in ``RENDERER_COVERAGE`` and on the same refusals: it
hashes the executable source of five package modules and it does NOT see the
registries reached through ``agent.deps`` -- their code, their four MeSH JSON
lookups or the ``icd10-cm`` release -- nor the ``python-dateutil`` pin behind
every rendered interval.

RESOLUTION IS CACHED FOR THE PROCESS, AND THAT IS A CORRECTNESS ARGUMENT
------------------------------------------------------------------------
``current()`` resolves the collection and counts its points ONCE and caches the
answer. ``oncotriage/batch/runner.py`` writes its checkpoint after every
patient, and a stamp resolved per write would be tens of thousands of live
round trips -- but the reason it is cached is not the saving. A run is ONE
configuration. If the weekly alias swap lands mid-run, a per-write stamp would
put two collections into one checkpoint and the file would then refuse itself;
a per-run stamp records the collection the run STARTED against, which is what
every patient in it was actually matched against for as long as the process's
BM25 index and clients were built.

``clear_cache()`` is the seam, called by each consumer at the top of its run
so two runs in one process each resolve, and by tests.

NOTHING HERE RAISES ON A FAILURE TO RESOLVE. ``current()`` records ``UNKNOWN``
and counts it; ``compare()`` then answers ``FP_UNRESOLVED``, and the CALLER
decides. That is ``probe_index``'s arrangement and for its reason: this is a
diagnostic, and a diagnostic that raises replaces the finding with a traceback.

WHAT IMPORTING THIS MODULE DOES. Nothing: no client, no model, no path
resolution, no network. Every resolution is inside ``current()``.
"""

import ast
import hashlib
import os
import threading
from collections import Counter

from oncotriage import config
from oncotriage import utils
from oncotriage.agent import readiness
from oncotriage.agent.prompts import PROMPT_VERSION
from oncotriage.observability import get_logger


log = get_logger(__name__)


#------------------------------------------------------------------------------


# ===========================================================================
# CONSTANTS
# ===========================================================================

FINGERPRINT_VERSION = 3
"""Bumped when the FIELD SET changes, never when a field's value changes.

    2 -> 3  added ``matching_call_mode``. EVERY ARTIFACT STAMPED AT 2 THEREFORE
            ANSWERS FP_VERSION UNTIL AN OPERATOR CLEARS IT ONCE, for the reason
            spelled out under 1 -> 2 below; the remediation is identical and is
            printed on the refusal.

            WHAT IT CLOSES, AND THE HARM IS THE SAME SHAPE THE MODULE EXISTS
            FOR. ``config.matching_call_mode()`` decides whether Stage 5 sends
            ONE request carrying several trials or one request PER TRIAL. That
            is not a tuning knob with a marginal effect: it changes how many
            billed calls a patient costs, what the model sees in one context,
            which trials can be omitted from a response at all, and therefore
            the verdicts. Yet NOT ONE gated field moved with it -- the flag is
            a bool that no other field is a function of, and
            ``matching_model_configured`` is the same wire id in both arms
            because it is the same judge. So a grouped-mode checkpoint resumed
            under per-trial mode answered FP_MATCH, skipped every patient the
            grouped process had completed, ran the rest in the other arm, and
            put both into one ``inferences`` table with nothing in it saying
            so. Every mean, rate and per-patient cost computed over that
            artifact is a number about two arms presented as one.

    1 -> 2  added ``llm_classifier_renderer_digest``. EVERY ARTIFACT STAMPED AT
            1 THEREFORE ANSWERS FP_VERSION UNTIL AN OPERATOR CLEARS IT ONCE.
            That is this constant's designed semantics for a shape change, not
            a defect and not an accident: a version-1 stamp records five facts
            and this version gates on six, so the sixth would compare
            ``<not recorded>`` against a live digest and report a renderer
            change that may never have happened -- a true refusal for a false
            reason. The remediation is the consumer's own, printed on the
            refusal: clear the batch checkpoint, pass ``--fresh-start`` to the
            ablation study, or point ``--output-dir`` at a new directory (the
            evaluation harness additionally accepts
            ``--allow-environment-change``, which RECORDS the new era rather
            than discarding the old one).


It is what separates "this artifact was stamped by a writer that recorded
fewer facts than this one gates on" from "these two configurations differ".
Without it a stamp predating a new field would compare that field's absence
against a live value and report a configuration change that never happened --
a true refusal for a false reason, which is worse than no reason.
"""

UNKNOWN = "unknown"
"""What a field reads when it could not be established.

The same documented sentinel ``oncotriage/tracking.py`` uses, and never an
omitted key: an absent key and a key reading UNKNOWN are different findings,
and only the second is a statement that somebody tried.
"""

COLLECTION_IDENTITY = "name+points"
"""What the collection comparison actually compares, as a value a consumer
writes into its own artifact. See COLLECTION IDENTITY in the module docstring.
Read by every consumer and by the refusal text, so it cannot go stale silently.
"""

RENDERER_MODULES = (
    "agent/patient.py",
    "agent/prompts.py",
    "constants.py",
    "extraction/stage.py",
    "utils.py",
)
"""The modules whose EXECUTABLE SOURCE is hashed into
``llm_classifier_renderer_digest``, package-relative and sorted.

WHY A DIGEST AT ALL. ``llm_classifier_prompt_version`` is hand-maintained --
``oncotriage/agent/prompts.py`` says so in as many words, and says the
judgement is the point. A hand-maintained field is capable of being wrong, and
for the STORED-COLUMN consumers that is recoverable (``prompt_sha256`` records
the bytes per call, so a version that did not move beside a hash that did is
visible in the record). A RESUME GATE has no such second reading: it sees the
version and nothing else. So an edit to ``_create_patient_summary``, to a
temporal helper, or to the stage extractor that forgets the bump changes every
rendered prompt while this gate reports FP_MATCH, and the resumed run mixes two
eras into one artifact. This field is the mechanical half of that pair, at the
granularity a resume needs.

WHY EACH MODULE IS IN THE SET. Derived, not asserted: a static closure from
``patient._create_patient_summary`` and ``prompts.render_system_prompt`` over
every module-level name each reaches, transitively, reaches exactly these five
plus the two in ``RENDERER_MODULES_EXCLUDED`` and nothing else.
``tests/test_resume_configuration_fingerprint.py`` section 1b re-derives that
closure and fails if it reaches a module that is in neither tuple -- so a
helper moved to a new module cannot silently escape the digest, which is the
one rot a hand-written module list is prone to.

    agent/patient.py      ``_create_patient_summary`` and every helper that
                          shapes a character of it -- the three relevance
                          classifiers, the nine temporal helpers, the lab unit
                          normaliser, the stage-source phrase table.
    agent/prompts.py      ``render_system_prompt``. The Stage 5 prompt is the
                          template AND the record, and the template's version
                          is hand-maintained for exactly the same reason and
                          with exactly the same exposure.
    extraction/stage.py   the "Cancer Stage:" line. Not hypothetical: the CKD
                          guard pass changed this module and moved the rendered
                          stage of 244 of 1,000 patients without touching
                          patient.py at all.
    constants.py          ``LOINC_AJCC_CLINICAL_M``, which selects the M
                          category tier stage.py renders from.
    utils.py              ``deduplicate_by_display`` (which conditions render),
                          ``parse_partial_date`` (every interval) and
                          ``get_age_reference_date`` (what they are measured
                          against).

THE GRANULARITY IS THE MODULE, NOT THE FUNCTION, AND THAT IS DELIBERATE. A
per-definition closure would hash exactly the render path and nothing else, and
its failure mode is SILENT UNDER-COVERAGE: a bug in the closure walker drops a
helper from the digest and nothing ever says so. A module hash is a strict
SUPERSET of the render path, so its failure mode is over-refusal -- an edit to
``utils.get_model_cost`` refuses a resume it did not need to. That is the
direction this project accepts: a refusal costs one deliberate clear, and an
artifact holding two eras costs every number computed over it.
"""

RENDERER_MODULES_EXCLUDED = (
    "agent/deps.py",
    "config.py",
)
"""Reached by the render-path closure and deliberately NOT hashed.

Declared rather than merely absent, so the round trip in section 1b is CLOSED:
a module the closure reaches must be in one tuple or the other, and a new one
forces a decision instead of falling through.

    agent/deps.py   the dependency SEAM, not a renderer. Its whole contract is
                    that it returns an object which may be an override, so a
                    hash of the resolver describes neither the default nor what
                    was installed. What it hands back is the real uncovered
                    contributor -- see RENDERER_COVERAGE.
    config.py       its two render-relevant facts are ALREADY GATED as fields
                    of their own (``data_snapshot_date``, which
                    ``get_age_reference_date`` reads, and
                    ``matching_model_configured``). Hashing the module would
                    make this field a de-facto gate on every tunable, which the
                    module docstring explicitly declines to build, and it is
                    rewritten in place by ``tests/test_config_snapshot_date_
                    rot.py``. The one other config value the closure reaches,
                    ``STALE_LAB_AGE_DAYS``, was checked rather than assumed:
                    since 1.8.0 it keys ``TEMPORAL_KEY_LAB_STALE`` and decides
                    no character of output (``patient._lab_age_suffix``).

Neither is DESCENDED INTO by the closure either, which is what keeps
``tests/test_resume_configuration_fingerprint.py`` out of the collision matrix:
excluding a module excludes its subtree, so ``config.py`` -- a file one of the
suite's two writers rewrites -- is never opened by the derivation.
"""

RENDERER_DIGEST_ALGORITHM = "ast-normalized-sha256-v1"
"""How the source is reduced before it is hashed, hashed INTO the digest so a
change to the normalisation moves every digest rather than silently re-basing
comparability.

AST-NORMALIZED WITH DOCSTRINGS STRIPPED, NOT RAW BYTES, and the reason is that
raw bytes over-refuse in the one direction that would make this gate useless. A
comment cannot change rendered text, and this project writes its arguments AT
the code -- a raw-byte digest would refuse a resume for a documentation pass,
every time, and a gate that refuses for reasons the operator knows are spurious
is a gate the operator learns to clear without reading. Two modules with the
same normalised text are behaviourally identical, so what is excluded is
exactly what provably cannot move a character of output. (Docstrings are
stripped on the checked premise that nothing on the render path reads a
``__doc__``; section 1b asserts it rather than assuming it.)

THE ONE COST, STATED: ``ast.unparse`` is the interpreter's, so the digest is a
function of the source AND of the Python that read it, and a resume across two
Python versions refuses even with the source unchanged. That is over-refusal in
a case that is arguably real coverage -- a different interpreter IS a different
configuration -- and it is named here rather than discovered.
"""

RENDERER_COVERAGE = ("the executable source of " + str(len(RENDERER_MODULES))
                     + " package modules; NOT registry data")
"""What the renderer digest actually covers, as one clause a refusal can print.

``COLLECTION_IDENTITY``'s argument applied to this field: a weaker gate that
says it is weaker is a gate, and one that does not is a claim. WHAT IS NOT
COVERED, each measured rather than guessed:

    the registries reached through ``agent.deps``  -- the cancer code registry
        (which condition is Tier A / B / C, and therefore which conditions
        render in full and which collapse into one "Other conditions" line),
        the oncology lab registry (which observations, procedures and mCODE
        variants render at all) and the MeSH filter (the ``[neoplasm]`` versus
        ``[neoplasm-unverified]`` tag). Their CODE is excluded with
        ``agent/deps.py``'s argument; their DATA -- the four MeSH JSON lookups
        and the ``icd10-cm`` release -- is outside the repository entirely and
        could not be hashed from source at any granularity.
        PARTIALLY guarded elsewhere and only partially:
        ``tests/test_registries_cancer_code_claims_audit.py`` audits that every
        code in the registry still means what its comment claims, which catches
        a wrong entry and NOT a right one that was added or removed between two
        runs. Closing this properly is the same widening
        ``COLLECTION_IDENTITY`` names for the collection, and it is a follow-up
        rather than a half-measure taken here.
    ``python-dateutil``  -- ``relativedelta`` does the interval arithmetic every
        rendered date carries. A pin change moves rendered text and is not
        visible here.

It is NOT written into any consumer's artifact, and that asymmetry with
``COLLECTION_IDENTITY`` is deliberate: this string is a property of
FINGERPRINT_VERSION, which every stamp already carries, so a reader of an
artifact can look it up, whereas the collection identity qualifies a value the
artifact records and had nowhere else to live.
"""


# The gated fields, in the order a refusal lists them. CLOSED: `current()`
# produces exactly these keys and `compare()` walks exactly these keys, so a
# field added to one and not the other fails the round trip rather than being
# recorded and never gated -- the shape that lets a stamp look complete while
# gating on less than it records.
FINGERPRINT_FIELDS = (
    "llm_classifier_prompt_version",
    "llm_classifier_renderer_digest",
    "matching_model_configured",
    "matching_call_mode",
    "qdrant_collection",
    "collection_points",
    "data_snapshot_date",
)

# How compare() answered. A CLOSED set, for the reason
# fixtures.capture.RESUME_OUTCOMES and agent.state.TRIAL_VERDICTS are closed: a
# caller may branch on it exhaustively, and a new member has to be a change
# every caller sees rather than a string falling through an if/elif chain.
#
# EVERY MEMBER BUT THE FIRST IS A REFUSAL, and each names a DIFFERENT
# remediation -- which is the whole reason they are not one "mismatch" member.
# Clearing the artifact is right for FP_CHANGED and FP_ABSENT and wrong for
# FP_UNRESOLVED, where the artifact is fine and the endpoint is not.
FP_MATCH = "match"                      # every gated field agrees: resume
FP_ABSENT = "unknown_provenance"        # nothing recorded, or a stamp with no version
FP_VERSION = "fingerprint_version"      # a different stamp SHAPE
FP_CHANGED = "configuration_changed"    # a gated field genuinely differs
FP_UNRESOLVED = "current_unresolved"    # THIS run's configuration is not establishable
FP_OUTCOMES = (FP_MATCH, FP_ABSENT, FP_VERSION, FP_CHANGED, FP_UNRESOLVED)

NOT_RECORDED = "<not recorded>"
"""What a disagreement line prints for a field the stored stamp does not carry.

``oncotriage/evaluation/ragas_harness.py:identity_disagreement`` established
the rule this module follows: a missing key in the recorded identity is a
disagreement, never a pass. A partial artifact that does not state what it ran
under cannot be shown to have run under this.
"""

FINGERPRINT_DEGRADATIONS = Counter()
"""Why the CURRENT configuration could not be fully established, keyed by field
and exception type (``qdrant_collection:ConnectionError``,
``collection_points:unverifiable``).

Non-zero means some ``current()`` call in this process produced an UNKNOWN, and
therefore that every resume gate consulted afterwards refused with
FP_UNRESOLVED. Registered in ``oncotriage/degradation.py``'s spec table rather
than through ``register()``: this module does not import that one, so the
primary route applies.
"""


_RESOLVED = {}
"""The per-process cache. Keyed 'fingerprint'. See the module docstring."""

_RESOLVE_LOCK = threading.RLock()
"""Locked for the same reason ``oncotriage/agent/deps.py:_resolve`` is, and the
call site that forces it is real: ``oncotriage/batch/runner.py`` saves its
checkpoint from ``_on_done``, a done-CALLBACK, which runs on a WORKER thread --
so ``MAX_WORKERS`` threads can reach an unwarmed cache at once. ``if k not in d:
d[k] = build()`` is two atomic operations and one non-atomic sequence, and here
the cost of losing that race is not a wasted round trip but TWO resolutions that
can straddle an alias swap and disagree.

The lock is the second line of defence. The first is that every consumer warms
this cache on its main thread before its pool exists."""


#------------------------------------------------------------------------------


# ===========================================================================
# TAKING THE STAMP
# ===========================================================================

def _package_dir() -> str:
    """The directory holding this module -- i.e. the ``oncotriage`` package.

    Derived from ``__file__`` rather than from ``oncotriage.paths``, and that is
    a requirement rather than a shortcut: every path in ``paths`` resolves the
    SIBLING DATA TREE by glob, and the renderer digest is a fact about the code
    this process imported. It is also what makes the digest correct inside a
    copied tree -- a package copied to a scratch directory hashes its own
    modules, which is exactly what a revert harness needs.

    Computed on call rather than at import, so importing this module still
    resolves nothing.
    """
    return os.path.dirname(os.path.abspath(__file__))


def _strip_docstrings(node) -> None:
    """Remove the docstring from every module, function and class, in place.

    A body reduced to nothing gains ``pass``, because ``ast.unparse`` of an
    empty body is not valid Python and this function's output is compared as
    text.
    """
    for child in ast.walk(node):
        if not isinstance(child, (ast.Module, ast.FunctionDef,
                                  ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = child.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)
        if not body and not isinstance(child, ast.Module):
            body.append(ast.Pass())


def normalized_module_source(path: str) -> str:
    """One module's source with comments, docstrings and formatting removed.

    ``ast.unparse`` of the parsed module, so two files with the same output are
    behaviourally identical and two that differ differ executably. See
    ``RENDERER_DIGEST_ALGORITHM`` for why that is the right reduction and what
    it costs. Raises whatever the read or the parse raises; the caller counts.
    """
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    _strip_docstrings(tree)
    return ast.unparse(tree)


def renderer_module_digests() -> dict:
    """``{package-relative path: sha256 hex}`` for every RENDERER_MODULES entry.

    The primitive ``renderer_digest()`` is built from, and the reason it is
    public: a refusal says the digest moved, and this says WHICH MODULE moved,
    which is the difference between "somebody edited the renderer" and a
    diagnosis. Raises on an unreadable or unparseable module -- the caller is
    the one that decides what an unestablished digest means.
    """
    root = _package_dir()
    return {rel: hashlib.sha256(
                normalized_module_source(
                    os.path.join(root, *rel.split("/"))).encode("utf-8")
            ).hexdigest()
            for rel in RENDERER_MODULES}


def renderer_digest() -> str:
    """One hex digest over every RENDERER_MODULES entry, or ``UNKNOWN``.

    THE PATH IS HASHED BESIDE THE SOURCE, so moving a module moves the digest
    even if its text is untouched, and the ALGORITHM TAG is hashed first, so a
    change to the normalisation cannot silently re-base what two runs are
    comparing. The per-module digests go in as ``path:hex`` lines rather than as
    concatenated source, so no module's text can be arranged to look like the
    start of the next one's entry.

    NEVER RAISES. A module that cannot be read or parsed is counted in
    FINGERPRINT_DEGRADATIONS and answers UNKNOWN, which ``is_resolved`` then
    reads and ``compare()`` answers FP_UNRESOLVED for -- the module-wide rule
    that a diagnostic which raises replaces the finding with a traceback.
    """
    try:
        per_module = renderer_module_digests()
    except Exception as exc:                                   # noqa: BLE001
        FINGERPRINT_DEGRADATIONS[
            f"llm_classifier_renderer_digest:{type(exc).__name__}"] += 1
        log.warning("a renderer module could not be read; this run's rendering "
                    "code cannot be fingerprinted",
                    event="fingerprint_degraded", status="degraded",
                    error_type=type(exc).__name__)
        return UNKNOWN

    payload = "\n".join([RENDERER_DIGEST_ALGORITHM]
                        + [f"{rel}:{per_module[rel]}"
                           for rel in RENDERER_MODULES])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def clear_cache() -> None:
    """Drop the cached stamp so the next ``current()`` resolves again.

    Called at the top of each consumer's run -- ``clear_write_ledger()``'s
    precedent in the batch runner -- so two runs in one process do not share a
    collection resolution, and by tests.
    """
    with _RESOLVE_LOCK:
        _RESOLVED.pop("fingerprint", None)


def _resolve_collection():
    """``(name, points)`` for the collection this run will query.

    THE POINT COUNT IS TAKEN OF THE RESOLVED NAME, NOT OF THE ALIAS, and that
    is the one thing in this function that is not obvious. Resolving the alias
    and counting it are two round trips, and an alias swap between them would
    stamp collection A with collection B's point count -- a fingerprint that
    was never true of anything. ``probe_index(collection=resolved)`` closes the
    window: whatever name came back is the name that was counted.

    (That failure mode is not hypothetical in this project.
    ``oncotriage/tracking.py:configuration_params`` records the same defect
    found by running: its first version resolved the collection twice and the
    two calls could disagree.)

    Neither half raises. ``resolve_qdrant_collection`` already swallows its own
    failures and falls back to the alias string, so the only thing that can
    escape it is a client that cannot be BUILT; ``probe_index`` raises nothing
    by contract and reports ``unverifiable`` instead.
    """
    try:
        name = utils.resolve_qdrant_collection()
    except Exception as exc:                                   # noqa: BLE001
        FINGERPRINT_DEGRADATIONS[f"qdrant_collection:{type(exc).__name__}"] += 1
        log.warning("the backing collection could not be resolved; this run's "
                    "configuration cannot be fingerprinted",
                    event="fingerprint_degraded", status="degraded",
                    error_type=type(exc).__name__)
        return UNKNOWN, UNKNOWN

    try:
        probe = readiness.probe_index(collection=name)
    except Exception as exc:                                   # noqa: BLE001
        # probe_index's contract is that it raises nothing. This is the belt on
        # the braces: a client that cannot be built raises inside
        # deps.get_qdrant_client() BEFORE the probe's own try blocks are
        # entered, so the contract holds and this branch is still reachable.
        FINGERPRINT_DEGRADATIONS[f"collection_points:{type(exc).__name__}"] += 1
        return name, UNKNOWN

    if probe["points"] is None:
        # `absent` and `unverifiable` both land here. Neither is a count, and
        # neither may be defaulted to 0 -- an empty collection reports 0 and is
        # a MEASUREMENT, while these two are the absence of one.
        FINGERPRINT_DEGRADATIONS[f"collection_points:{probe['state']}"] += 1
        return name, UNKNOWN

    return name, probe["points"]


def _wire_model() -> str:
    """``config.matching_wire_model()``, degraded to UNKNOWN rather than raised.

    THIS MODULE'S CONTRACT IS THAT ``current()`` NEVER RAISES -- its own
    docstring says so, and both consumers call it from a ``main()`` that is
    about to spend money, where an exception out of the STAMP would abort a run
    the stamp exists to describe. ``matching_wire_model()`` raises on an
    unrecognised ``MATCHING_PROVIDER``, which is right at the call site that is
    about to build a request and wrong here.

    An UNKNOWN in a gated field makes the stamp UNRESOLVED, so ``compare()``
    answers FP_UNRESOLVED -- "this run's own configuration did not establish"
    -- which is the outcome that already exists for exactly this situation and
    the one that does NOT send an operator to clear a perfectly good
    checkpoint. The reason is counted, keyed by exception type, on the pattern
    the collection resolvers two functions up already use.
    """
    try:
        return config.matching_wire_model()
    except Exception as exc:                                   # noqa: BLE001
        FINGERPRINT_DEGRADATIONS[
            f"matching_model_configured:{type(exc).__name__}"] += 1
        log.warning("the configured matching provider did not resolve to a "
                    "model id; the run fingerprint records it as unknown",
                    event="fingerprint_wire_model_unresolved",
                    error_type=type(exc).__name__, error_message=str(exc),
                    degraded=True)
        return UNKNOWN


def _call_mode() -> str:
    """``config.matching_call_mode()``, degraded to UNKNOWN rather than raised.

    THROUGH THE ONE OWNER, NEVER THROUGH THE CONSTANT. ``matching_call_mode()``
    is the single place ``MATCHING_PER_TRIAL_CALLS_ENABLED`` is turned into the
    value this project records -- ``oncotriage/agent/evaluation.py`` decides
    Stage 5's partition through it and ``oncotriage/storage/database_logger.py``
    writes ``inferences.matching_call_mode`` from it, both by CALLING it rather
    than reading the flag. A ``from oncotriage.config import
    MATCHING_PER_TRIAL_CALLS_ENABLED`` here would BIND the value at import, so a
    process that moved the flag afterwards (``bedrock_probe.py`` does; a test
    does) would stamp the value this module was imported with rather than the
    one the run used -- and a stamp that disagrees with the run it describes is
    worse than no stamp. Reading ``config.MATCHING_PER_TRIAL_CALLS_ENABLED``
    live would be no better in kind: it is a SECOND derivation of one fact, the
    two-copies shape pass 20f-2 removed for the cross-encoder checkpoint, and
    the day the owner grows a third mode this one would silently keep answering
    with two.

    NEVER RAISES, on ``_wire_model``'s footing directly above: this module's
    contract is that ``current()`` never raises, and both consumers call it from
    a ``main()`` that is about to spend money. The owner cannot raise today --
    it is a conditional expression over a bool -- and that is exactly why the
    guard is here rather than argued away: a function that cannot fail is a
    function whose call site is free to stop checking, and the day it grows a
    lookup, a validation or a third mode this would otherwise abort the run the
    stamp exists to describe.

    An UNKNOWN in a gated field makes the stamp UNRESOLVED, so ``compare()``
    answers FP_UNRESOLVED -- "this run's own configuration did not establish"
    -- which does NOT send an operator to clear a good checkpoint.
    """
    try:
        return config.matching_call_mode()
    except Exception as exc:                                   # noqa: BLE001
        FINGERPRINT_DEGRADATIONS[
            f"matching_call_mode:{type(exc).__name__}"] += 1
        log.warning("the Stage 5 call mode did not resolve; the run "
                    "fingerprint records it as unknown",
                    event="fingerprint_call_mode_unresolved",
                    error_type=type(exc).__name__, error_message=str(exc),
                    degraded=True)
        return UNKNOWN


def current(refresh: bool = False) -> dict:
    """This run's configuration stamp. Resolved once per process, then cached.

    Args:
        refresh: resolve again even if a stamp is cached. For a caller that
            genuinely wants a second reading (a test asserting the cache, or a
            long-lived process that has reconfigured); NOT used by any consumer
            during a run, because a run is one configuration.

    Returns a dict carrying ``fingerprint_version`` plus exactly
    ``FINGERPRINT_FIELDS``. Any field may read ``UNKNOWN``; none is ever
    omitted, and none is ever defaulted to a plausible-looking value.
    """
    with _RESOLVE_LOCK:
        if refresh:
            _RESOLVED.pop("fingerprint", None)
        if "fingerprint" not in _RESOLVED:
            name, points = _resolve_collection()
            _RESOLVED["fingerprint"] = {
                "fingerprint_version": FINGERPRINT_VERSION,
                "llm_classifier_prompt_version": PROMPT_VERSION,
                "llm_classifier_renderer_digest": renderer_digest(),
                # THE WIRE MODEL, NOT `MATCHING_MODEL`, AND WITH THE PROVIDER
                # FLAG AT ITS DEFAULT THE TWO ARE THE SAME STRING -- so every
                # v2 stamp already on disk still matches and no
                # FINGERPRINT_VERSION bump is owed.
                #
                # WHAT IT CLOSES. This field is GATED, and `MATCHING_MODEL`
                # does NOT move when MATCHING_PROVIDER flips: it is the priced
                # identity of the judge, and "gpt-5.6-terra" is the same judge
                # on either provider. So a checkpoint written against OpenAI
                # would have been resumed against Bedrock with this gate
                # answering FP_MATCH, and one artifact would hold two
                # providers' rows -- different endpoint, different request
                # form, and no `seed` on the Responses side -- with nothing in
                # it saying so. The wire id differs by construction
                # ("gpt-5.6-terra" against "us.openai.gpt-5.6-terra"), so the
                # flip now reads as FP_CHANGED naming this field.
                #
                # WHAT IS STILL NOT GATED: BEDROCK_ENDPOINT and BEDROCK_REGION.
                # Same profile id in two Regions, or mantle against runtime
                # with the same id, are indistinguishable here. Closing that
                # needs a seventh gated field and a FINGERPRINT_VERSION bump,
                # whose cost is that every v2-stamped artifact refuses once --
                # recorded as a follow-up rather than taken silently.
                "matching_model_configured": _wire_model(),
                # HOW STAGE 5 IS CALLED, beside WHICH model it calls, because
                # the two together are what a request is. This is the field
                # that makes an arm of the per-trial campaign a configuration
                # rather than an undeclared variable -- see FINGERPRINT_VERSION
                # 2 -> 3 for what a resume across the two arms silently
                # produced before it.
                "matching_call_mode": _call_mode(),
                "qdrant_collection": name,
                "collection_points": points,
                "data_snapshot_date": config.DATA_SNAPSHOT_DATE,
            }
        # A COPY. The consumers put this straight into a JSON payload they then
        # mutate around, and a shared dict would let one consumer's edit reach
        # another's stamp.
        return dict(_RESOLVED["fingerprint"])


def summary(fingerprint: dict) -> str:
    """One line naming every gated field of a stamp. One text, three callers.

    THIS SENTENCE WAS WRITTEN THREE TIMES -- ``compare()``'s FP_MATCH detail,
    ``batch/runner.py:main``'s ``[Config]`` banner and
    ``ablation/study.py:main``'s ``Configuration:`` line -- and the renderer
    digest is what made that cost visible: adding a gated field left both
    banners naming five of six, so an operator who then met a refusal saying
    the renderer digest moved had never been shown the value it moved from.
    ``refusal_lines``' argument, applied one level down: three harnesses must
    not come to describe one configuration three ways.

    THE DIGEST IS ABBREVIATED TO 12 HEX CHARACTERS and the full value is in the
    artifact. A banner is read by a person deciding whether this is the run
    they meant; 64 characters of hex on a console line is not that, and the
    comparison is never made by eye.

    Every read is a ``.get``: this formats a diagnosis, and the whole point of
    a diagnosis is that it survives the state it is diagnosing.
    """
    get = fingerprint.get
    digest = str(get("llm_classifier_renderer_digest", NOT_RECORDED))
    return (f"prompt {get('llm_classifier_prompt_version', NOT_RECORDED)} "
            f"(renderer {digest[:12]}), "
            f"model {get('matching_model_configured', NOT_RECORDED)} "
            f"({get('matching_call_mode', NOT_RECORDED)}), "
            f"collection {get('qdrant_collection', NOT_RECORDED)} "
            f"({get('collection_points', NOT_RECORDED)} points), "
            f"snapshot {get('data_snapshot_date', NOT_RECORDED)}")


def is_resolved(fingerprint: dict) -> bool:
    """Whether every gated field of this stamp was established.

    A stamp carrying UNKNOWN is written down anyway -- the artifact should
    record what was known -- but it can never be shown to AGREE with anything,
    which is what ``compare()`` reads this for.

    A MISSING field counts as unestablished, not as absent-and-therefore-fine.
    ``current()`` never omits one, so the only way to get here with a field
    missing is a hand-built stamp -- a test fixture, or a caller that predates
    a field. The default used to be ``.get(f)``, which returns None, which is
    not UNKNOWN, so such a stamp reported RESOLVED; ``disagreements()`` then
    compared NOT_RECORDED with NOT_RECORDED, found them equal, and
    ``compare()`` answered FP_MATCH -- a hand-built stamp missing the very
    field a version bump added would have been reported as AGREEING with a run
    that has it. The version gate catches that particular case first, and a
    guard that depends on another guard running first is not a guard.
    """
    return all(fingerprint.get(f, UNKNOWN) != UNKNOWN
               for f in FINGERPRINT_FIELDS)


#------------------------------------------------------------------------------


# ===========================================================================
# COMPARING TWO STAMPS
# ===========================================================================

def disagreements(recorded, current_fp: dict) -> list:
    """Every gated field that differs, as ``"field: was -> now"`` lines.

    Empty means they agree. A field the recorded stamp does not carry is a
    DISAGREEMENT and prints as ``<not recorded>`` --
    ``ragas_harness.identity_disagreement``'s rule, and the reason is the same:
    an artifact that does not state what it ran under cannot be shown to have
    run under this. ``recorded`` may be None or any non-dict; both mean the
    same thing and neither raises, because this is only ever called to explain
    a refusal and a formatter that raises while formatting a refusal replaces
    the diagnosis with a traceback.
    """
    if not isinstance(recorded, dict):
        recorded = {}
    changed = []
    for field in FINGERPRINT_FIELDS:
        was = recorded.get(field, NOT_RECORDED)
        now = current_fp.get(field, NOT_RECORDED)
        if was != now:
            changed.append(f"{field}: {was!r} -> {now!r}")
    return changed


def compare(recorded, current_fp: dict) -> tuple:
    """``(outcome, detail)`` -- may this run continue ``recorded``'s artifact?

    ``outcome`` is one of ``FP_OUTCOMES`` and only ``FP_MATCH`` permits a
    resume. ``detail`` is one line for a console, naming every differing field
    rather than the first, because an operator reading a refusal wants the
    whole disagreement.

    THE ORDER OF THE CHECKS IS THE ORDER OF THE DIAGNOSES, and it is chosen so
    that the answer names the thing the operator can act on:

      1. THIS run unestablishable -> FP_UNRESOLVED. Asked first because
         comparing against an UNKNOWN would report every field as changed and
         send an operator to clear a perfectly good checkpoint when the actual
         fault is an unreachable endpoint. It is also the only outcome whose
         remediation is not about the artifact at all.
      2. nothing recorded, or a stamp with no version -> FP_ABSENT. Every
         artifact written before this module existed is here. It is NOT folded
         into FP_CHANGED: "produced by an unknown configuration" and "produced
         by a different one" are different findings, and only the first is
         silent about which fields moved.
      3. a different stamp SHAPE -> FP_VERSION, before any field is compared,
         so a field this version gates and that version never recorded is not
         reported as a configuration change.
      4. fields -> FP_CHANGED or FP_MATCH.
    """
    if not is_resolved(current_fp):
        unknown = [f for f in FINGERPRINT_FIELDS
                   if current_fp.get(f, UNKNOWN) == UNKNOWN]
        outcome, detail = FP_UNRESOLVED, (
            "this run's own configuration could not be established ("
            + ", ".join(f"{f}={UNKNOWN}" for f in unknown)
            + "), so it cannot be shown to match anything")
    elif not isinstance(recorded, dict) or not recorded:
        outcome, detail = FP_ABSENT, (
            "no configuration fingerprint was recorded, so what produced it is "
            "unknown")
    elif recorded.get("fingerprint_version") is None:
        outcome, detail = FP_ABSENT, (
            "the stored state carries no fingerprint_version: it was written "
            "before configuration fingerprinting existed, so what produced it "
            "is unknown")
    elif recorded.get("fingerprint_version") != FINGERPRINT_VERSION:
        outcome, detail = FP_VERSION, (
            f"fingerprint_version {recorded.get('fingerprint_version')!r} != "
            f"{FINGERPRINT_VERSION!r}: the stored state records a different set "
            f"of facts than this version gates on, so the two cannot be "
            f"compared field by field")
    else:
        changed = disagreements(recorded, current_fp)
        if changed:
            outcome, detail = FP_CHANGED, "; ".join(changed)
        else:
            outcome, detail = FP_MATCH, summary(current_fp)

    assert outcome in FP_OUTCOMES, f"unknown fingerprint outcome {outcome!r}"
    return outcome, detail


class ResumeRefusal(RuntimeError):
    """A stored artifact may not be continued by this run.

    A ``RuntimeError`` subclass and deliberately NOT a ``ValueError``, on
    ``oncotriage/utils.py:UnknownModelPricingError``'s precedent: a stray
    ``except ValueError`` around a json parse is exactly what would swallow the
    one thing standing between a misconfigured resume and a wrong artifact.

    ``outcome`` is the ``FP_OUTCOMES`` member, so a caller can branch on the
    finding without parsing the message; the message carries the whole
    diagnosis and the caller's own remediation, already formatted by
    ``refusal_lines``.
    """

    def __init__(self, message, outcome=FP_CHANGED):
        super().__init__(message)
        self.outcome = outcome


def refusal_lines(outcome: str, detail: str, artifact: str,
                  remediation) -> list:
    """The refusal, as the lines every consumer prints. One text, three callers.

    ``remediation`` is the caller's -- clearing a checkpoint, pointing
    ``--output-dir`` elsewhere and passing an override flag are three different
    commands and only the caller knows which. Everything ABOVE it is shared, so
    three harnesses cannot come to describe the same refusal three ways.

    THE TWO COVERAGE LIMITATIONS ARE PRINTED ON EVERY REFUSAL THAT COMPARED
    FIELDS, and only on those. FP_CHANGED is the only outcome that got as far
    as comparing a collection or a renderer digest: FP_ABSENT and FP_UNRESOLVED
    never reached the fields, FP_VERSION refuses BEFORE them, and FP_MATCH is
    not a refusal at all -- no caller passes it here, which is why it is not in
    the test below. Stating the limits of a comparison that did not run would
    be noise pretending to be rigour.

    FP_VERSION GETS A CLAUSE OF ITS OWN, for the opposite reason: it is the one
    refusal whose cause may be nothing at all. A version bump makes every
    existing artifact answer it exactly once, and an operator meeting that
    without being told reads a shape change as a configuration change and goes
    looking for an edit that did not happen.
    """
    lines = [f"REFUSED ({outcome}): {artifact}", f"    {detail}"]
    if outcome == FP_CHANGED:
        lines.append(f"    collection identity compared as "
                     f"{COLLECTION_IDENTITY}: a collection rebuilt in place to "
                     f"the same point count with different contents would not "
                     f"be detected here")
        lines.append(f"    renderer identity compared as "
                     f"{RENDERER_COVERAGE}: an edit to the cancer / lab / MeSH "
                     f"registries or to their data changes rendered text and "
                     f"would not be detected here either")
    if outcome == FP_VERSION:
        lines.append(f"    this is the stamp SHAPE changing, not necessarily "
                     f"the configuration: fingerprint_version "
                     f"{FINGERPRINT_VERSION} gates "
                     f"{len(FINGERPRINT_FIELDS)} facts and the stored state "
                     f"records a different set, so the two cannot be compared "
                     f"field by field. Clearing the artifact once is the whole "
                     f"remediation and it is expected on first contact after a "
                     f"version bump")
    lines.extend(f"    {line}" for line in
                 (remediation if isinstance(remediation, (list, tuple))
                  else [remediation]))
    return lines


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug 20 09:00:00 2026

@author: ramyalsaffar
"""
