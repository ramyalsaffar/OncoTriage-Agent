# Characterization Fixture Replay
#################################

"""
Replays every fixture 45- Fixture Capture.py wrote and reports every difference.

WHAT IT DOES
------------
For each fixture:

  1. Re-parses the source FHIR bundle with the CURRENT parser, rather than
     feeding back the patient dict stored in the fixture. Item 20 restructures
     File 07 too; reusing the stored dict would feed the recorded answer in as
     an input and make that half of the diff vacuous.
  2. Runs the pipeline with all three model boundaries served from the
     recording — OpenAI embeddings, the MedCPT cross-encoder, and the Stage 5
     chat completion. No request reaches OpenAI, and no local model is loaded
     at all: ONCOTRIAGE_DEFER_LOCAL_MODELS is set before 13- is exec'd, so
     MedCPT and FastEmbed are never constructed.
  3. Rebuilds the deterministic prefix with the same function that wrote it
     (build_deterministic_prefix, in 45-) and diffs it field by field.
  4. Reports every difference by dotted field path with both values.

Qdrant is the one boundary that is NOT replayed. The trial corpus is the fixed
input the fixture is pinned against, so retrieval runs live and the recorded
per-channel NCT order is what proves the two runs asked the same questions. The
pinned collection is checked FIRST: if resolve_qdrant_collection() no longer
returns the name the fixtures were captured against, this file refuses to
replay anything rather than reporting a diff against a different index.

EXIT CODE
---------
0 only when every fixture replays clean. Any difference, any replay miss, any
collection mismatch, any load failure -> 1.

WHAT A DIFFERENCE MEANS
-----------------------
Not automatically a defect. It means the current code no longer does what it
did when the fixture was captured, and something has to explain why. The three
innocent explanations, in the order they should be checked:

  - a File 03 tunable changed. environment.tunables records the ones the prefix
    depends on; this file prints any that moved.
  - the pinned Qdrant collection's CONTENTS changed in place. Pinning the
    resolved name catches an alias swap, not an edit to the collection behind it.
  - the fixture is stale for a known, intended behaviour change, and should be
    re-captured with a note.

Everything else is item 20 having changed an answer.

USAGE
-----
    python "46- Fixture Replay.py"                     # replay all, exit 0 if clean
    python "46- Fixture Replay.py" --only normal_1
    python "46- Fixture Replay.py" --max-diffs 5       # truncate per-fixture output
    python "46- Fixture Replay.py" --fixture-dir <dir>
"""


#------------------------------------------------------------------------------


# Run needed files
#-----------------
# Item 20a: this file sits in the code directory, so __file__ locates it with
# no hardcoded path. __file__ is bound when the file is run as a script (every
# documented entry point for it) and when Spyder runfile()s it. In a bare
# interactive paste it is not bound, and the working directory is the only
# remaining candidate -- taken, but announced, never silently.
import os as _os_boot
if "__file__" in globals():
    _code_dir = _os_boot.path.dirname(_os_boot.path.abspath(__file__)) + _os_boot.sep
else:
    _code_dir = _os_boot.getcwd() + _os_boot.sep
    print(f"[Bootstrap] __file__ unbound; using the working directory as the code directory: {_code_dir}")
del _os_boot

# MUST be set before 13- is exec'd, which happens inside 45-'s own chain below.
# 13- reads it once at exec time and binds a raising placeholder in place of
# each local model; this file replaces both placeholders with stand-ins that
# serve recorded output. Setting it any later would load the models first and
# make "no model load" a claim rather than a fact.
import os as _os_bootstrap
_os_bootstrap.environ["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

# 45- carries the schema, the recording sink, the Qdrant recorder and
# build_deterministic_prefix, and it chains 07- and 13- (which chain 03, 08,
# 09, 10). Listing any of those here as well would exec them twice. 45-'s
# main() does not fire: exec_chain sets __name__ to "_exec_chain_".
exec_chain(
    ["45- Fixture Capture.py"],
    caller_file=_code_dir + "46- Fixture Replay.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 45 (→ 07 → 13 → 03 → 08 → 09 → 10)",
)

# Not in 01- Imports.py. Used to build the response objects the replay hands
# back in place of the OpenAI client's own.
from types import SimpleNamespace


#------------------------------------------------------------------------------


# ===========================================================================
# REPLAY MISS
# ===========================================================================

class FixtureReplayMiss(RuntimeError):
    """The pipeline asked for a model output the fixture does not contain.

    That is itself a finding, and a sharper one than a value difference: the
    code no longer embeds the same text, no longer scores the same trial texts,
    or no longer makes the same number of Stage 5 calls. It is raised rather
    than papered over with a zero vector, because a fabricated model output
    would propagate into the retrieval order and be reported as a dozen
    unrelated differences downstream.
    """


#------------------------------------------------------------------------------


# ===========================================================================
# REPLAY STANDS-INS
# ===========================================================================
#
# Each one mirrors the shape the real object returns, closely enough for the
# call sites in File 13 and no more. Anything the pipeline reads that is not
# reproduced here would raise an AttributeError naming the attribute, which is
# a better failure than a plausible stand-in that is subtly wrong.

def _embedding_response(vector):
    return SimpleNamespace(data=[SimpleNamespace(embedding=list(vector))])


def _chat_response(recorded):
    # completion_tokens_details mirrors the reasoning-model usage shape, and it
    # is set to None — not to a details object carrying 0 — when the recording
    # has no reasoning_tokens. Two recordings need that: one made before the
    # field existed (every GPT-4o-era fixture), and one made against a
    # non-reasoning model. File 13 reads the attribute defensively and logs
    # NULL for both, which is the honest answer; synthesising a 0 here would
    # make a replayed pre-migration fixture claim it spent no reasoning tokens
    # rather than that it never reported any.
    _reasoning = recorded["usage"].get("reasoning_tokens")
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=recorded["content"]),
            finish_reason=recorded.get("finish_reason"),
        )],
        model=recorded.get("model"),
        usage=SimpleNamespace(
            prompt_tokens=recorded["usage"]["prompt_tokens"],
            # Includes the reasoning tokens below, exactly as the API reports
            # it. Not a sum the replay computes.
            completion_tokens=recorded["usage"]["completion_tokens"],
            completion_tokens_details=(
                None if _reasoning is None
                else SimpleNamespace(reasoning_tokens=_reasoning)
            ),
        ),
    )


def _sparse_embedding(indices, values):
    # File 13 calls .indices.tolist() and .values.tolist(), so both have to be
    # numpy arrays and not lists. int32/float32 match what FastEmbed produces.
    return SimpleNamespace(
        indices=np.asarray(indices, dtype=np.int32),
        values=np.asarray(values, dtype=np.float32),
    )


class _ReplayState:
    """Indexes one fixture's recordings and tracks what has been consumed."""

    def __init__(self, fixture: Dict, sink):
        recordings = fixture["recordings"]
        self.fixture_id = fixture["fixture_id"]
        self.sink = sink
        self._lock = threading.Lock()

        # Embeddings and cross-encoder passes are served BY KEY: the pipeline
        # may issue them in any thread order, and what matters is that it asked
        # for the same thing, not that it asked in the same order.
        self.embeddings = {
            (r["model"], r["input"]): r["vector"]
            for r in recordings["openai_embeddings"]
        }
        self.sparse = {r["query"]: r for r in recordings["sparse_embeddings"]}
        self.cross_encoder = {
            (r["query"], r["trial_texts_sha256"]): r
            for r in recordings["cross_encoder"]
        }

        # Stage 5 is served BY CALL INDEX, not by key. The retry loop issues
        # the SAME request twice and must receive different answers; a
        # key-indexed lookup would hand back the malformed payload forever and
        # the run would exhaust its retries instead of recovering.
        self.chat_calls = list(recordings["chat_completions"])
        self.chat_cursor = 0


def _replay_embedding(state: _ReplayState):
    def handler(_inner, kwargs):
        key = (kwargs.get("model"), kwargs.get("input"))
        if key not in state.embeddings:
            detail = (f"model={key[0]!r} input[:120]={str(key[1])[:120]!r}")
            state.sink.note_miss("recordings.openai_embeddings", detail)
            raise FixtureReplayMiss(
                f"no recorded embedding for {detail} — the pipeline is "
                f"embedding text this fixture never saw"
            )
        return _embedding_response(state.embeddings[key])
    return handler


def _replay_chat(state: _ReplayState):
    def handler(_inner, kwargs):
        with state._lock:
            index = state.chat_cursor
            state.chat_cursor += 1

        if index >= len(state.chat_calls):
            detail = (f"call {index} requested, only {len(state.chat_calls)} "
                      f"recorded")
            state.sink.note_miss("recordings.chat_completions", detail)
            raise FixtureReplayMiss(
                f"Stage 5 made more calls than were recorded ({detail})"
            )

        recorded = state.chat_calls[index]
        # The request is diffed rather than enforced: serving the recorded
        # response for a request that changed is exactly what makes the change
        # visible downstream, in the verdicts and in stage5.request_sha256_by_call.
        # Same key set as the recorder in File 45, in the same order, because
        # the two are diffed against each other. A key present on one side only
        # would read as a request change on every fixture.
        state.sink.add("chat_completions", {
            "request": {
                "model": kwargs.get("model"),
                "messages": copy.deepcopy(kwargs.get("messages")),
                "temperature": kwargs.get("temperature"),
                "max_tokens": kwargs.get("max_tokens"),
                "max_completion_tokens": kwargs.get("max_completion_tokens"),
                "reasoning_effort": kwargs.get("reasoning_effort"),
                "seed": kwargs.get("seed"),
            },
            "response": copy.deepcopy(recorded["response"]),
            "served_from_recorded_index": recorded.get("call_index", index),
        })
        return _chat_response(recorded["response"])
    return handler


class ReplaySparseModel:
    """Serves recorded FastEmbed query vectors. Loads nothing."""

    def __init__(self, state: _ReplayState):
        self._state = state

    def query_embed(self, query_text, **_kwargs):
        recorded = self._state.sparse.get(query_text)
        if recorded is None:
            detail = f"query={str(query_text)[:160]!r}"
            self._state.sink.note_miss("recordings.sparse_embeddings", detail)
            raise FixtureReplayMiss(
                f"no recorded BM25 query vector for {detail}"
            )
        self._state.sink.add("sparse_embeddings", {
            "query": query_text,
            "indices": list(recorded["indices"]),
            "values": list(recorded["values"]),
        })
        yield _sparse_embedding(recorded["indices"], recorded["values"])


def _replay_medcpt(state: _ReplayState):
    def wrapper(query, trial_texts):
        digest = sha256_json(list(trial_texts))
        recorded = state.cross_encoder.get((query, digest))
        if recorded is None:
            detail = (f"query={str(query)[:80]!r} n_pairs={len(trial_texts)} "
                      f"trial_texts_sha256={digest[:16]}...")
            state.sink.note_miss("recordings.cross_encoder", detail)
            raise FixtureReplayMiss(
                f"no recorded cross-encoder pass for {detail} — either the "
                f"rerank query changed or the trial texts fed to it did"
            )
        # float32, the dtype the model produced. np.argsort ties break the same
        # way at either precision here, but the per-query min/max/mean logged
        # by Stage 3 would not round-trip identically as float64.
        scores = np.asarray(recorded["scores"], dtype=np.float32)
        state.sink.add("cross_encoder", {
            "query": query,
            "n_pairs": len(trial_texts),
            "trial_texts_sha256": digest,
            "trial_texts": list(trial_texts),
            "scores": [float(s) for s in scores],
            "dtype": str(scores.dtype),
        })
        return scores
    return wrapper


class _TripwireEndpoint:
    """A stand-in for one OpenAI sub-object. Raises the moment it is used.

    OpenAIProxy READS inner.embeddings and inner.chat.completions in its
    __init__, to build the two shims that serve them from the recording. Those
    two reads are structural and must succeed; what must never happen is a CALL
    reaching the network. So the tripwire hands back these placeholders for the
    two known paths, and they raise on any attribute access or call underneath.
    """

    def __init__(self, path):
        object.__setattr__(self, "_path", path)

    def _explode(self, what):
        raise FixtureReplayMiss(
            f"replay reached the real OpenAI client at {self._path}.{what}. "
            f"A replay must make no request to OpenAI; this would have been a "
            f"real, billed call. Either the pipeline now uses an OpenAI surface "
            f"the recording does not cover, or a hook is missing from "
            f"install_replay_hooks()."
        )

    def __getattr__(self, attr):
        if attr == "completions":
            return _TripwireEndpoint(f"{self._path}.completions")
        self._explode(attr)

    def __call__(self, *args, **kwargs):
        self._explode("()")


class _OpenAITripwire:
    """Stands where the real OpenAI client would be, and raises on any use.

    THIS IS THE MONEY GUARD, and it is the reason replay can claim "no request
    reaches OpenAI" rather than merely intending it.

    OpenAIProxy forwards UNKNOWN attributes to the object it wraps. In capture
    that object is the real client, which is correct -- capture is supposed to
    call OpenAI. In replay it must not be, because any surface the proxy does
    not intercept would go straight through to the endpoint and be billed while
    the replay reported clean. So replay wraps THIS instead: the two paths the
    proxy does intercept (embeddings, chat.completions) are handed inert
    placeholders that the proxy immediately shadows with its recording shims,
    and every other attribute raises by name.

    It is not a substitute for the identity assertion in
    install_replay_hooks() -- that one catches a hook that was never installed,
    this one catches a hook that was installed but incomplete. Different
    failures, both silent without a guard.
    """

    _STRUCTURAL = ("embeddings", "chat")

    def __init__(self):
        object.__setattr__(self, "touched", [])

    def __getattr__(self, attr):
        self.touched.append(attr)
        if attr in _OpenAITripwire._STRUCTURAL:
            # Read by OpenAIProxy.__init__ and then SHADOWED by its shim. The
            # placeholder is what makes an un-shadowed use raise instead of
            # reaching the network.
            return _TripwireEndpoint(f"openai_client.{attr}")
        raise FixtureReplayMiss(
            f"replay reached the OpenAI client for {attr!r}, which the fixture "
            f"does not serve. A replay must make no request to OpenAI; this "
            f"would have been a real, billed call. Either the pipeline now uses "
            f"an OpenAI surface the recording does not cover, or a hook is "
            f"missing from install_replay_hooks()."
        )


def install_replay_hooks(fixture: Dict, sink) -> tuple:
    """Point all four seams at the recording. Returns (saved, replay_state).

    INSTALLED THROUGH oncotriage.agent.deps SINCE PASS 20c-2c. This used to
    rebind four names in this module's globals(), which worked only because
    every project file was exec'd into one dict. File 13 is a shim over
    oncotriage/agent/ now, its functions resolve their globals in their own
    modules, and a rebinding here would have redirected NOTHING: every Stage 5
    prompt in every fixture would have gone to the real OpenAI endpoint, been
    billed, and the run would still have printed that all twelve replayed
    clean. Nothing would have raised. That is the defect the seam exists for.

    Qdrant alone still talks to the network: the corpus is the fixed input, not
    a recorded output. That is why this file cannot be run with all egress
    blocked, and why the OpenAI side gets a tripwire instead -- see
    _OpenAITripwire.
    """
    state = _ReplayState(fixture, sink)

    proxies = {
        deps.OPENAI_CLIENT: OpenAIProxy(
            # NOT the real client. See _OpenAITripwire.
            _OpenAITripwire(),
            _replay_embedding(state),
            _replay_chat(state),
        ),
        deps.QDRANT_CLIENT: QdrantProxy(deps.get_qdrant_client(), sink),
        deps.BM25_QUERY_MODEL: ReplaySparseModel(state),
        deps.MEDCPT_SCORER: _replay_medcpt(state),
    }
    saved = deps.set_overrides(proxies)

    # THE ASSERTION THAT REPLACES "IT ALWAYS WORKED". Identity, against what
    # deps hands the agent -- not against this namespace, which would pass with
    # the hooks installed nowhere.
    assert_hooks_reach_the_agent(
        {name: proxies[key] for name, key in _HOOK_KEYS.items()},
        "install_replay_hooks",
    )

    return saved, state


#------------------------------------------------------------------------------


# ===========================================================================
# DIFFING
# ===========================================================================

# How much of a long value to show. Whole GPT-4o prompts and 1536-float vectors
# do not belong in a diff report; the field path plus a recognisable head is
# what a reader acts on.
VALUE_PREVIEW_CHARS = 220


def _preview(value) -> str:
    text = repr(value)
    if len(text) <= VALUE_PREVIEW_CHARS:
        return text
    return f"{text[:VALUE_PREVIEW_CHARS]}... [{len(text)} chars]"


_ABSENT = object()


def diff_prefix(expected: Dict, actual: Dict) -> List[Dict]:
    """Field-by-field difference between two deterministic prefixes.

    Exact equality on flattened leaves. None never compares equal to 0: the
    degradation columns distinguish "the stage did not report" from "the stage
    reported nothing wrong" (see CLAUDE.md), and a diff that folded them
    together would hide the one regression those fields exist to catch.

    Fields present on one side only are reported as such, so a prefix that
    grew or lost a key is a difference rather than a silent pass.
    """
    flat_expected = flatten_prefix(expected)
    flat_actual = flatten_prefix(actual)

    differences = []
    for field in sorted(set(flat_expected) | set(flat_actual)):
        want = flat_expected.get(field, _ABSENT)
        got = flat_actual.get(field, _ABSENT)

        if want is _ABSENT:
            differences.append({"field": field, "kind": "added",
                                "expected": "<absent in fixture>",
                                "actual": _preview(got)})
        elif got is _ABSENT:
            differences.append({"field": field, "kind": "removed",
                                "expected": _preview(want),
                                "actual": "<absent in replay>"})
        elif type(want) is not type(got) or want != got:
            # type() before == so True does not compare equal to 1 and None
            # stays distinct from a falsy value of another type.
            differences.append({"field": field, "kind": "changed",
                                "expected": _preview(want),
                                "actual": _preview(got)})

    return differences


def diff_tunables(fixture: Dict) -> List[Dict]:
    """Config constants that moved since capture.

    Reported separately from the prefix diff because they are a different kind
    of finding: a tunable change EXPLAINS a prefix difference rather than being
    one, and a reader who does not see it will spend the afternoon looking for
    a refactor bug that is not there.
    """
    recorded = fixture["environment"].get("tunables") or {}
    moved = []
    for name, was in sorted(recorded.items()):
        now = globals().get(name, _ABSENT)
        if now is _ABSENT:
            moved.append({"name": name, "was": was, "now": "<no longer defined>"})
        elif now != was:
            moved.append({"name": name, "was": was, "now": now})
    return moved


#------------------------------------------------------------------------------


# ===========================================================================
# ONE FIXTURE
# ===========================================================================

def obtain_bundle(fixture: Dict) -> tuple:
    """Get the fixture's input bundle. Returns (path, is_temporary).

    A cohort fixture names a file in data_fhir_path. A derived fixture names a
    RECIPE (schema v2), which is rebuilt from the live corpus into a temporary
    file here — the derived bundle is 107 MB of Synthea record and storing it
    was what made the v1 fixture directory uncommittable.

    The fixture stores a filename, never a path: data_fhir_path is resolved by
    glob prefix in 01- Imports.py and moves between renumberings and between
    host and container.
    """
    identity = fixture["identity"]
    location = identity.get("source_bundle_location", BUNDLE_IN_COHORT)

    if location == BUNDLE_DERIVED:
        return rebuild_derived_bundle(fixture), True

    return os.path.join(data_fhir_path, identity["source_bundle"]), False


def replay_fixture(fixture: Dict, graph: object) -> Dict:
    """Replay one fixture and return its report."""
    fixture_id = fixture["fixture_id"]
    print(f"\n{'-' * 78}\n{fixture_id}  [{', '.join(fixture['case_labels'])}]"
          f"{'  (CONSTRUCTED)' if fixture['fixture_kind'] == FIXTURE_KIND_CONSTRUCTED else ''}")
    print(f"{'-' * 78}")

    report = {
        "fixture_id": fixture_id,
        "fixture_kind": fixture["fixture_kind"],
        "case_labels": fixture["case_labels"],
        "differences": [],
        "replay_misses": [],
        "tunables_moved": diff_tunables(fixture),
        "fatal": None,
        "parse_source": None,
        "recipe_hash_ok": None,
    }

    # --- Obtain and re-parse the source bundle -----------------------------
    temporary = False
    try:
        bundle_path, temporary = obtain_bundle(fixture)
    except Exception as exc:
        report["fatal"] = (f"could not obtain the source bundle: "
                           f"{type(exc).__name__}: {exc}")
        print(f"  FATAL: {report['fatal']}")
        return report

    try:
        if not os.path.exists(bundle_path):
            # Only reachable for a cohort fixture whose patient has left the
            # corpus. Falling back to the stored dict keeps the rest of the
            # pipeline diffable but silently removes File 07 from the
            # comparison, so it is stated in the report, not merely printed.
            patient_data = copy.deepcopy(
                fixture["deterministic_prefix"]["patient_data"]
            )
            report["parse_source"] = "stored_dict_bundle_missing"
            print(f"  WARNING: source bundle not found at {bundle_path}; "
                  f"replaying from the stored patient dict. The parser is NOT "
                  f"under test for this fixture.")
        else:
            try:
                patient_data = parse_fhir_bundle(bundle_path)
                report["parse_source"] = "rebuilt_from_recipe" if temporary \
                    else "reparsed"
            except Exception as exc:
                report["fatal"] = (f"parse_fhir_bundle failed on "
                                   f"{fixture['identity']['source_bundle']}: "
                                   f"{type(exc).__name__}: {exc}")
                print(f"  FATAL: {report['fatal']}")
                return report

            # A recipe that no longer reproduces its input is a broken fixture,
            # not a code regression, and it has to say so in its own words
            # before the diff blames Stage 1 for a different patient.
            if temporary:
                rebuilt_hash = compute_patient_hash(patient_data)
                expected_hash = fixture["identity"]["patient_data_hash"]
                report["recipe_hash_ok"] = rebuilt_hash == expected_hash
                if not report["recipe_hash_ok"]:
                    report["fatal"] = (
                        f"the derivation recipe "
                        f"{fixture['derivation']['recipe']!r} rebuilt a patient "
                        f"whose hash is {rebuilt_hash}, but the fixture records "
                        f"{expected_hash}. The recipe, the donor bundle or the "
                        f"parser changed — this is not a pipeline difference."
                    )
                    print(f"  FATAL: {report['fatal']}")
                    return report
                print(f"  rebuilt from recipe "
                      f"{fixture['derivation']['recipe']}: patient_data_hash "
                      f"{rebuilt_hash} matches")
    finally:
        if temporary and os.path.exists(bundle_path):
            os.remove(bundle_path)

    # --- Run with the model boundaries served from the recording ------------
    sink = RecordingSink()
    saved, _state = install_replay_hooks(fixture, sink)
    try:
        initial_state = build_initial_state(
            patient_data, fixture["inputs"]["ablation_flags"]
        )
        final_state = graph.invoke(initial_state)
        result = final_state["result"]
        result["qdrant_collection"] = resolve_qdrant_collection()
        result["patient_data_hash"] = compute_patient_hash(patient_data)
    except FixtureReplayMiss as exc:
        # A miss raised outside Stage 2's per-channel handler kills the run.
        # Inside it, the channel is recorded as failed and the run continues —
        # which is why sink.replay_misses is reported either way.
        report["fatal"] = f"replay miss: {exc}"
        report["replay_misses"] = list(sink.replay_misses)
        print(f"  FATAL: {report['fatal']}")
        return report
    except Exception as exc:
        report["fatal"] = (f"pipeline raised {type(exc).__name__}: {exc}\n"
                           f"{traceback.format_exc()}")
        report["replay_misses"] = list(sink.replay_misses)
        print(f"  FATAL: pipeline raised {type(exc).__name__}: {exc}")
        return report
    finally:
        restore_hooks(saved)

    report["replay_misses"] = list(sink.replay_misses)

    # --- Diff ---------------------------------------------------------------
    actual_prefix = build_deterministic_prefix(final_state, result, sink)
    # Through JSON first: the fixture's side has been through a round trip and
    # the live side has not, so comparing them raw would report a tuple that
    # became a list as a difference in the code.
    actual_prefix = json.loads(json.dumps(actual_prefix, ensure_ascii=False))

    report["differences"] = diff_prefix(
        fixture["deterministic_prefix"], actual_prefix
    )
    return report


#------------------------------------------------------------------------------


# ===========================================================================
# REPORTING
# ===========================================================================

def print_report(report: Dict, max_diffs: int) -> None:
    if report["tunables_moved"]:
        print("  CONFIG MOVED SINCE CAPTURE (explains prefix differences):")
        for moved in report["tunables_moved"]:
            print(f"    {moved['name']}: {moved['was']!r} -> {moved['now']!r}")

    for miss in report["replay_misses"]:
        print(f"  REPLAY MISS  {miss['field']}: {miss['detail']}")

    if report["fatal"]:
        return

    differences = report["differences"]
    if not differences:
        print(f"  CLEAN — deterministic prefix identical "
              f"({report['parse_source']}).")
        return

    print(f"  {len(differences)} DIFFERENCE(S):")
    for difference in differences[:max_diffs]:
        print(f"    [{difference['kind']}] {difference['field']}")
        print(f"        fixture: {difference['expected']}")
        print(f"        replay : {difference['actual']}")
    if len(differences) > max_diffs:
        print(f"    ... and {len(differences) - max_diffs} more "
              f"(raise --max-diffs to see them)")


#------------------------------------------------------------------------------


# ===========================================================================
# MAIN
# ===========================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay characterization fixtures and report every difference."
    )
    parser.add_argument("--only", nargs="*", default=None,
                        help="Replay only these fixture ids.")
    parser.add_argument("--fixture-dir", default=None,
                        help="Directory to read fixtures from.")
    parser.add_argument("--max-diffs", type=int, default=25,
                        help="Differences printed per fixture.")
    args = parser.parse_args()

    root = args.fixture_dir or FIXTURE_ROOT

    print(f"\n{'=' * 78}")
    print(f"{Project_Name}: Characterization Fixture Replay (schema v{SCHEMA_VERSION})")
    print(f"{'=' * 78}")
    print(f"  Fixture directory: {root}")

    paths = list_fixtures(root)
    if not paths:
        print(f"[FATAL] No fixtures in {root}. Run 45- Fixture Capture.py first.")
        return 1

    fixtures = []
    load_failures = 0
    for path in paths:
        try:
            fixtures.append(load_fixture(path))
        except (ValueError, json.JSONDecodeError) as exc:
            load_failures += 1
            print(f"  LOAD FAILED  {os.path.basename(path)}: {exc}")

    if args.only:
        fixtures = [f for f in fixtures if f["fixture_id"] in args.only]
        if not fixtures:
            print(f"[FATAL] --only matched no fixture in {root}")
            return 1

    if not fixtures:
        print("[FATAL] No fixture could be loaded.")
        return 1

    # --- THE SEAM, CHECKED BEFORE ANYTHING IS REPLAYED ----------------------
    #
    # This block exists because of the defect pass 20c-2c fixed, and it is
    # deliberately the first thing main() does.
    #
    # Until pass 2c, install_replay_hooks() redirected the pipeline by rebinding
    # four names in this module's globals(). That worked only because every
    # project file was exec'd into one dict. File 13 is now a shim over
    # oncotriage/agent/, whose functions resolve their globals in their own
    # modules -- so a rebinding here reaches nothing, every Stage 5 prompt in
    # every fixture goes to the REAL OpenAI endpoint and is BILLED, and this
    # file still prints that all twelve replayed clean. Nothing raises.
    #
    # assert_hooks_reach_the_agent() (File 45) is what makes that impossible: it
    # asks deps what the AGENT would reach and requires it to be the proxy, by
    # identity. Two things are checked here, in this order:
    #
    #   1. NEGATIVE CONTROL FIRST. With no overrides installed, the assertion
    #      must RAISE. An assertion that has only ever passed is not evidence it
    #      can catch anything -- and this one guards the only thing in this
    #      repository that costs money to get wrong.
    #   2. Then the real thing, installed and torn down, must pass.
    #
    # No fixture is loaded and no model is called by either step.
    print("\n  Dependency seam (oncotriage.agent.deps):")

    _probe_sink = RecordingSink()
    # An EMPTY fixture, of the shape _ReplayState indexes. Nothing is served
    # from it -- the probe never invokes the pipeline, it only checks which
    # objects deps hands out -- so every recording list is empty on purpose.
    _probe_state = _ReplayState(
        {
            "fixture_id": "_seam_probe",
            "recordings": {
                "openai_embeddings": [],
                "sparse_embeddings": [],
                "cross_encoder": [],
                "chat_completions": [],
            },
        },
        _probe_sink,
    )
    _probe_proxies = {
        deps.OPENAI_CLIENT: OpenAIProxy(_OpenAITripwire(),
                                        _replay_embedding(_probe_state),
                                        _replay_chat(_probe_state)),
        deps.QDRANT_CLIENT: QdrantProxy(deps.get_qdrant_client(), _probe_sink),
        deps.BM25_QUERY_MODEL: ReplaySparseModel(_probe_state),
        deps.MEDCPT_SCORER: _replay_medcpt(_probe_state),
    }
    _probe_expected = {name: _probe_proxies[key] for name, key in _HOOK_KEYS.items()}

    try:
        assert_hooks_reach_the_agent(_probe_expected, "negative control")
    except RuntimeError as _exc:
        print(f"    negative control: the assertion FAILS with no override "
              f"installed, as it must")
        print(f"      {str(_exc).splitlines()[0]}")
    else:
        print("\n[FATAL] THE SEAM CHECK PROVES NOTHING.")
        print("        assert_hooks_reach_the_agent() passed with NO override "
              "installed, so it would also pass if install_replay_hooks() "
              "redirected nothing -- and every Stage 5 call below would be a "
              "real, billed request to OpenAI.")
        return 1

    _probe_saved = deps.set_overrides(_probe_proxies)
    try:
        assert_hooks_reach_the_agent(_probe_expected, "positive control")
        print("    positive control: with the overrides installed, the agent "
              "reaches all four proxies")
    finally:
        deps.restore_overrides(_probe_saved)

    # And the OpenAI tripwire itself: the replay client wraps a stand-in that
    # raises, not the real client, so a model surface the recording does not
    # cover fails loudly instead of being forwarded and billed.
    # Both halves: an unknown surface must raise by name, and the two the proxy
    # shadows must raise as soon as anything actually CALLS them.
    _tripwire_armed = 0
    try:
        _OpenAITripwire().responses          # a surface the recording never covers
    except FixtureReplayMiss:
        _tripwire_armed += 1
    try:
        _OpenAITripwire().chat.completions.create(model="x")   # the shadowed path
    except FixtureReplayMiss:
        _tripwire_armed += 1
    if _tripwire_armed == 2:
        print("    OpenAI tripwire armed: an unrecorded surface AND an "
              "unshadowed chat.completions.create both raise instead of "
              "reaching the network")
    else:
        print(f"\n[FATAL] the OpenAI tripwire raised on {_tripwire_armed}/2 "
              f"probes; refusing to replay.")
        return 1

    # --- The pinned collection, checked before anything is replayed ---------
    #
    # A fixture diffed against a different index measures the corpus, not the
    # code, and would report dozens of retrieval-order differences that mean
    # nothing. This is a refusal, not a warning.
    live_collection = resolve_qdrant_collection()
    pinned = {f["environment"]["qdrant_collection"] for f in fixtures}
    print(f"  Live Qdrant collection:   {live_collection}")
    print(f"  Fixtures pinned to:       {', '.join(sorted(pinned))}")

    if pinned != {live_collection}:
        print(f"\n[FATAL] THE INDEX CHANGED, NOT THE CODE.")
        print(f"        The alias '{COLLECTION_NAME}' now resolves to "
              f"'{live_collection}', which is not what these fixtures were "
              f"captured against.")
        print(f"        Refusing to diff against a different index. Either "
              f"point Qdrant back at the pinned collection, or re-capture the "
              f"fixtures with 45- Fixture Capture.py.")
        for fixture in sorted(fixtures, key=lambda f: f["fixture_id"]):
            got = fixture["environment"]["qdrant_collection"]
            if got != live_collection:
                print(f"          {fixture['fixture_id']}: pinned {got}")
        return 1

    # --- ...and what is IN it -----------------------------------------------
    #
    # The name check above catches File 11 swapping the alias to a new
    # collection. It cannot catch the collection itself being re-indexed in
    # place, which changes every retrieval order while the name stays put. That
    # failure is indistinguishable from a code regression by the time it
    # reaches the diff, so it is caught here, before anything is replayed, and
    # named for what it is.
    live_digest, digest_seconds = compute_collection_digest(live_collection)
    print(f"  Collection contents:      {live_digest['point_count']} points, "
          f"{live_digest['distinct_nct_ids']} distinct NCT IDs, sha256 "
          f"{live_digest['nct_id_sha256'][:16]}... ({digest_seconds}s)")

    stale = []
    for fixture in sorted(fixtures, key=lambda f: f["fixture_id"]):
        recorded = fixture["environment"].get("collection_digest")
        if recorded is None:
            # Absent is not "matched". A fixture with no digest predates this
            # check and cannot be vouched for.
            stale.append((fixture["fixture_id"], "no digest recorded", None))
        elif recorded != live_digest:
            differing = [k for k in sorted(set(recorded) | set(live_digest))
                         if recorded.get(k) != live_digest.get(k)]
            stale.append((fixture["fixture_id"], "digest mismatch", differing))

    if stale:
        print(f"\n[FATAL] THE INDEX CHANGED, NOT THE CODE.")
        print(f"        '{live_collection}' still exists under the same name, "
              f"but its contents no longer match what these fixtures were "
              f"captured against.")
        print(f"        Every retrieval-order difference you would see below "
              f"would be the corpus, not item 20. Re-capture with "
              f"45- Fixture Capture.py.")
        for fixture_id, reason, fields in stale:
            print(f"          {fixture_id}: {reason}"
                  + (f" on {fields}" if fields else ""))
        for fixture in fixtures:
            recorded = fixture["environment"].get("collection_digest")
            if recorded and recorded != live_digest:
                print(f"        fixture: {recorded}")
                print(f"        live   : {live_digest}")
                break
        return 1

    print(f"  Local models:             not loaded "
          f"({DEFER_LOCAL_MODELS_ENV}=1)")
    print(f"  Replaying {len(fixtures)} fixture(s)\n")

    graph = build_matching_graph()

    reports = []
    with CaffeinateSession("fixture replay"):
        for fixture in sorted(fixtures, key=lambda f: f["fixture_id"]):
            report = replay_fixture(fixture, graph)
            print_report(report, args.max_diffs)
            reports.append(report)

    # --- Summary ------------------------------------------------------------
    print(f"\n{'=' * 78}\nSUMMARY\n{'=' * 78}")
    failed = 0
    for report in reports:
        n_diff = len(report["differences"])
        n_miss = len(report["replay_misses"])
        if report["fatal"]:
            status = "FATAL"
        elif n_diff or n_miss:
            status = "DIFFERS"
        else:
            status = "clean"
        if status != "clean":
            failed += 1
        detail = []
        if n_diff:
            detail.append(f"{n_diff} field(s)")
        if n_miss:
            detail.append(f"{n_miss} replay miss(es)")
        if report["parse_source"] not in ("reparsed", "rebuilt_from_recipe"):
            detail.append(report["parse_source"])
        print(f"  {status:<8} {report['fixture_id']:<28} "
              f"{'; '.join(detail)}")

    if load_failures:
        print(f"\n  {load_failures} fixture(s) could not be loaded.")

    print(f"\n  {len(reports) - failed}/{len(reports)} replayed clean.")
    print(f"{'=' * 78}\n")

    return 0 if (failed == 0 and load_failures == 0) else 1


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug  4 09:16:00 2026

@author: ramyalsaffar

"""
