# CI Test Classification and Runner
###################################

"""Which tests CI can run, which it cannot, and why -- with the reason recorded.

THE SUITE IS NOT PYTEST and never was: every check runs at module level and the
exit code is set in a `__main__` block, so there is nothing to collect and no
marker to attach. The classification therefore lives here, beside the runner,
rather than as decorators in 32 files.

THE FIVE BUCKETS
----------------
    A  parallel-safe in CI -- no network, no keys, no spend, no live server, no
       live Qdrant, no data outside the repository
    B  serial -- a member of the collision matrix in tests/run_serial_tests.py
    C  needs a live Qdrant endpoint
    D  costs money (a billed model call) or needs a live HTTP server
    E  needs data files that are not committed

CLASSIFICATION WAS DERIVED BY RUNNING, NOT BY READING. Every non-serial file was
executed with the project root pointed at a directory holding ONLY the
directory skeleton `.github/scripts/provision_ci_paths.py` creates -- which is
exactly what a CI runner has. The evidence string on each entry below is the
observed outcome, not an inference from its imports. A grep-based first pass
disagreed with the run in both directions: it marked files "needs Qdrant"
because they name `get_qdrant_client` inside a `deps.set_override` stand-in, and
it missed the four that die on a licence-gated file they never mention.

B AND E OVERLAP, AND THE BRIEF THIS WAS BUILT FROM ASSUMED THEY DO NOT.
Four of the five serial members ALSO need uncommitted data: the registry audit
needs the UMLS Metathesaurus (`MRCONSO*.RRF`, licence-gated, ~1.5 GB, not
redistributable), its control runs that audit as its own baseline subprocess,
the snapshot-date test runs `test_fhir_ecog_surfacing.py` as a subprocess and
that needs a generated Synthea corpus, and `test_degraded_dependencies.py`
needs the real patient corpus. Only `test_package_invariants.py` is runnable on
a hosted runner. Each such entry carries BOTH letters below: the bucket that
decides HOW it runs (B, through `make serial-tests`, never directly) and the
`needs` field that decides WHETHER it can. The workflow gates on `needs`.

WHY A DECLARED TABLE WITH AN ENFORCED COMPLETENESS CHECK. Deriving membership at
run time would mean running every test to find out which ones CI can run, which
is the thing being decided. So the table is declared -- and `check_complete()`
fails when any `tests/test_*.py` is absent from it, so a new test file turns
into a NAMED CI failure rather than silently never running. That is the same
trade `_EXEC_ALLOWLIST` and the never-read-name exemptions make elsewhere in
this project: a closed list is fine as long as something fails when it goes
stale.

Run from terminal:
    python .github/scripts/ci_test_buckets.py --list
    python .github/scripts/ci_test_buckets.py --check
    python .github/scripts/ci_test_buckets.py --run A
    python .github/scripts/ci_test_buckets.py --serial-preconditions

Exit codes:
    0 -- the requested action succeeded
    1 -- a test failed, or the table is incomplete
    2 -- a named test file is missing from tests/
"""

import argparse
import concurrent.futures
import os
import subprocess
import sys
import time


_CODE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TESTS_DIR = os.path.join(_CODE_DIR, "tests")


# ===========================================================================
# THE SERIAL MEMBERSHIP COMES FROM THE RUNNER, NOT FROM HERE
# ===========================================================================
# tests/run_serial_tests.py owns the collision matrix; it derived it from the
# code and it is what `make serial-tests` executes. Re-typing those five names
# here would be a second declaration that can disagree with the one that runs.
def _serial_members():
    """Return the basenames in tests/run_serial_tests.py's SERIAL_TESTS."""
    sys.path.insert(0, _TESTS_DIR)
    try:
        import run_serial_tests
    except ImportError as exc:                       # pragma: no cover
        raise RuntimeError(
            f"cannot import tests/run_serial_tests.py, which owns the "
            f"collision matrix: {exc}"
        ) from exc
    return tuple(os.path.basename(name) for name, _ in run_serial_tests.SERIAL_TESTS)


# ===========================================================================
# THE TABLE
# ===========================================================================
# (bucket, needs, evidence)
#   bucket   -- A/B/C/D/E, the single bucket the brief asks for
#   needs    -- None when CI can run it; otherwise what is absent on a runner
#   evidence -- the observed line that put it there
#
# `needs` is separate from `bucket` because a B member can also be unrunnable,
# and collapsing the two would either hide the collision constraint or hide the
# data constraint. Every non-None `needs` was read off a real run.
_A = "A"; _B = "B"; _C = "C"; _D = "D"; _E = "E"

BUCKETS = {
    # ---- A: verified green with only the directory skeleton ---------------
    "test_agent_ablation_flag_passthrough.py": (
        _A, None, "ran green in 8.3s; registries and clients replaced through deps"),
    "test_agent_bedrock_adapter.py": (
        _A, None,
        "ran green in 2.4s, 273 checks, against ONLY the directory skeleton: "
        "the Stage 5 Bedrock adapter behind config.MATCHING_PROVIDER, which "
        "ships OFF. Every client is a stand-in installed through "
        "oncotriage/agent/deps.py and every model response is a literal dict, "
        "so no network, no keys, no spend, no live Qdrant and no model load "
        "(ONCOTRIAGE_DEFER_LOCAL_MODELS is set above the imports). No corpus "
        "and no git history -- the nine controls are in-memory copies of "
        "oncotriage/agent/bedrock_adapter.py, argued at _EXEC_ALLOWLIST in "
        "tests/test_package_invariants.py, because the module has no prior "
        "revision for `git show` to serve. run_fingerprint.current() is "
        "deliberately NOT called: it probes the index over the network, and "
        "the gated field is asserted by AST plus the pure functions instead. "
        "It writes only inside a tempfile.mkdtemp it removes and asserts gone, "
        "and the three repository files it reads (bedrock_adapter.py, "
        "evaluation.py, database_logger.py) are written by neither of the "
        "suite's two writers, so it is NOT in the collision matrix"),
    "test_agent_age_units_and_sex_filter.py": (
        _A, None, "ran green in 1.8s; plants into in-memory copies, reads no git"),
    "test_agent_composed_assessment.py": (
        _A, None,
        "ran green in 1.4s, 103 checks, against ONLY the directory skeleton, "
        "and identical in a depth-1 clone: the stored assessment is a "
        "rendering of the criteria arrays rather than the model's prose. "
        "Every control is a different INPUT to a pure function, which is why "
        "there is no exec and no `git show` anywhere in it -- no network, no "
        "keys, no spend, no git history, no corpus, no database, no "
        "subprocess. It writes nothing anywhere, so it is not in the "
        "collision matrix"),
    "test_agent_cross_encoder_sequence_limit.py": (
        _A, None,
        "ran green in 1.1s, 42 checks, against ONLY the directory skeleton: "
        "config.CROSS_ENCODER_MAX_LENGTH and the load-time verifier in "
        "oncotriage/agent/deps.py that compares it with what the checkpoint "
        "declares. NO MODEL IS LOADED -- ONCOTRIAGE_DEFER_LOCAL_MODELS is set "
        "above the imports and section 4 asserts torch and transformers never "
        "entered sys.modules -- so no network, no keys, no spend, no model "
        "download, no live Qdrant, no corpus, no database, no git history. "
        "Every control is a different INPUT to a pure function, plus one "
        "override installed inside try/finally and asserted removed, so it "
        "execs nothing and needs no _EXEC_ALLOWLIST entry. It writes nothing "
        "anywhere and is NOT in the collision matrix -- the one repository "
        "file it reads, oncotriage/agent/deps.py, is written by neither of "
        "the suite's two writers"),
    "test_agent_degraded_run_and_reporting.py": (
        _A, None,
        "ran green in 2.6s, 118 checks, against ONLY the directory skeleton: "
        "the degraded_run derivation and its column, the four Stage 4 "
        "filter-applied markers, QDRANT_RETRIES through tenacity's own "
        "machinery, load_results' corrupt-file preservation and the run-end "
        "degradation report. Every patient and trial is a literal dict, the "
        "MeSH filter is replaced through oncotriage/agent/deps.py, the "
        "database is a temp file every log_inference call is pointed at "
        "explicitly and the results path is redirected through "
        "paths._RESOLVED -- no network, no keys, no spend, no live Qdrant, no "
        "git, no corpus. It execs nothing: the controls feed different inputs "
        "to a pure function, create genuinely corrupt files, or rebind a "
        "module attribute inside try/finally"),
    "test_agent_emission_provenance.py": (
        _A, None,
        "ran green in 1.7s, 184 checks, against ONLY the directory skeleton, "
        "identical in a depth-1 clone: the Stage 5 emission-provenance stamp "
        "and the two trial_matches columns. Every model response is a literal "
        "served by a stub installed through oncotriage/agent/deps.py, every "
        "plant goes into an in-memory copy of agent/evaluation.py or "
        "storage/database_logger.py -- both hashed before any plant and "
        "compared at the end -- and every database write goes to a scratch "
        "file in a temp directory asserted to differ from the production "
        "path and removed at the end. No network, no keys, no spend, no git "
        "history, no corpus. An _EXEC_ALLOWLIST member, argued there"),
    "test_agent_mesh_boost_and_quality_gate.py": (
        _A, None, "ran green in 1.8s once the two vendored MeSH lookups are seeded"),
    "test_agent_out_of_set_detector.py": (
        _A, None,
        "ran green in 2.4s, 167 checks; Stage 5's out-of-set detector (split "
        "into fabricated vs cross-chunk), the duplicate-answer collapse and "
        "the retrieval-rank trial_number. Every model response is a literal served "
        "through a deps stub, the database is a temp file every log_inference "
        "call is pointed at explicitly, and every plant goes into an in-memory "
        "copy -- no network, no keys, no spend, no git, no corpus"),
    "test_agent_patient_record_tokens.py": (
        _A, None,
        "ran green in 1.9s, 68 checks, against ONLY the directory skeleton, "
        "identical in a depth-1 clone: llm_classifier_patient_record_tokens "
        "measures the patient record's own token count. Every control is a "
        "different ARGUMENT to the shipped code -- a fence-carrying summary, "
        "a state that never rendered, a result dict without the key, a decoy "
        "database -- so it execs nothing and needs no _EXEC_ALLOWLIST entry. "
        "The OpenAI client is a stand-in installed through "
        "oncotriage/agent/deps.py and Qdrant and the cross-encoder are never "
        "reached; every write goes to a scratch database in a temp directory "
        "asserted to differ from the production one. No network, no keys, no "
        "spend, no corpus, no git history"),
    "test_agent_procedure_relevance.py": (
        _A, None,
        "ran green in 1.4s, 136 checks, against ONLY the directory skeleton, "
        "identical in a depth-1 clone: _classify_procedure_relevance decides "
        "which procedures reach the Stage 5 prompt. Every patient is a "
        "literal dict and the classifier is a pure function of it -- no "
        "network, no keys, no spend, no git history, no corpus, no database, "
        "no subprocess"),
    "test_agent_prompt_version.py": (
        _A, None,
        "ran green in 0.74s wall (0.00s of checks) in a `git ls-files` checkout "
        "with ONLY the skeleton: 41 passed, 0 failed, exit 0. It renders 16 "
        "strings and reads one committed JSON file -- no network, no keys, no "
        "spend, no database, no subprocess, no corpus, and NO GIT, which is the "
        "point of it (a commit recedes; the reference is the golden snapshot)"),
    "test_agent_remap_no_survivor.py": (
        _A, None,
        "ran green in 1.6s, 121 checks, against ONLY the directory skeleton, "
        "identical in a depth-1 clone: a rejection whose disqualifying labels "
        "were out of their arm's vocabulary, corrected under "
        "UNEVALUABLE_REMAP_NO_SURVIVOR and marked so the composition writes a "
        "sentence of its own. Every plant goes into an in-memory copy of "
        "agent/evaluation.py, hashed before any plant and compared at the end "
        "-- no network, no keys, no spend, no git history, no corpus. An "
        "_EXEC_ALLOWLIST member, argued there"),
    "test_agent_render_event_suppression.py": (
        _A, None,
        "ran green in 1.8s, 55 checks, against ONLY the directory skeleton, "
        "identical in a depth-1 clone: the packer's token-measurement path "
        "must emit no render event, and the decode logging aggregates to one "
        "event per render. Driven through the real observability logger with "
        "the records captured in memory -- no network, no keys, no spend, no "
        "git history, no corpus, no database"),
    "test_agent_stage5_render_slice_equality.py": (
        _A, None,
        "ran green in 1.1s, 47 checks, against ONLY the directory skeleton and "
        "in a tree with NO .git at all, with ONCOTRIAGE_MAIN_PATH pointed at a "
        "directory that does not exist: the Stage 5 packer prices each trial "
        "off a block the node's ONE whole-batch render already produced, and "
        "that block is byte-identical to the one a one-trial render makes. "
        "Every fixture is a literal dict and every control is a different "
        "INPUT to a pure function -- no network, no keys, no spend, no model "
        "call, no live Qdrant, no corpus, no database, no git history"),
    "test_agent_rrf_config_ownership.py": (
        _A, None,
        "ran green in 0.8s, 31 checks, against ONLY the directory skeleton and "
        "again in a tree with NO .git at all -- the five RRF fusion constants "
        "are config's and agent/retrieval.py READS them. Its fixture is a "
        "fabricated rank table, so no Qdrant, no model, no keys, no spend, no "
        "corpus, no database and no git history; the one plant goes into an "
        "in-memory ast copy, so it writes nothing anywhere and is NOT in the "
        "collision matrix -- the one repository file it reads, "
        "oncotriage/agent/retrieval.py, is written by neither of the suite's "
        "two writers. It execs nothing, so it needs no _EXEC_ALLOWLIST entry"),
    "test_agent_retrieval_observability.py": (
        _A, None, "ran green in 3.5s; needs .env to EXIST, makes no live call"),
    "test_agent_stage5_input_packing.py": (
        _A, None,
        "ran green in 1.8s, 109 checks, against ONLY the directory skeleton, "
        "identical in a depth-1 clone: Stage 5 bounds the size of its "
        "REQUESTS. Every response is a literal served by a stub installed "
        "through oncotriage.agent.deps. No network, no keys, no spend, no "
        "database, no subprocess, no fixture, no git history, no corpus, no "
        "model call; it writes nothing in the repository, so it is not in the "
        "collision matrix. An _EXEC_ALLOWLIST member -- section 6 execs a "
        "copy with the packing branch disabled as the OTHER ARM of an "
        "equivalence proof, not only as a control"),
    "test_agent_stage5_per_trial_calls.py": (
        _A, None,
        "ran green in 10.0s, 239 checks (this line read 139 and was stale "
        "by 100 across two passes): Stage 5's flag-gated PER-TRIAL call "
        "mode -- one billed call per patient-trial pair, the priming call "
        "awaited alone so the shared prefix can warm, the rest dispatched "
        "under an in-flight bound, and the merge in TRIAL order however the "
        "pool answers. Every response is a literal served by a stub installed "
        "through oncotriage.agent.deps, and the scheduling assertion is an "
        "integer comparison over tickets the stub issued rather than a "
        "measurement of elapsed time, so it does not depend on runner speed. "
        "No network, no keys, no spend, no subprocess, no fixture, no git "
        "history, no corpus, no model call, no live server. It DOES open "
        "SQLite, in section 9b only, to round-trip the additive "
        "inferences.matching_call_mode column through the real writer -- "
        "every database is a scratch file inside a tempfile.mkdtemp that is "
        "asserted to differ from the production path, removed at the end and "
        "asserted gone -- so it writes nothing in the repository and is NOT "
        "in the collision matrix. An _EXEC_ALLOWLIST member: fourteen "
        "controls each exec an in-memory copy of "
        "oncotriage/agent/evaluation.py with one part of the mechanism "
        "broken"),
    "test_agent_state_channel_coverage.py": (
        _A, None,
        "ran green in 2.1s, 73 checks, against ONLY the directory skeleton, "
        "identical in a depth-1 clone: a key a graph node returns that "
        "TrialMatchState does not declare is dropped, so the declaration is "
        "scanned against what the nodes actually return. Static analysis over "
        "the package's own AST -- no network, no keys, no spend, no git "
        "history, no corpus, no database, no subprocess"),
    "test_agent_summary_cancer_stage.py": (
        _A, None,
        "ran green in 2.0s, 53 checks; every patient is a literal dict, the "
        "plants go into an in-memory copy of oncotriage/agent/patient.py, and "
        "it reads no git, no corpus and no database"),
    "test_agent_summary_temporal_tagging.py": (
        _A, None,
        "ran green in 1.4s, 216 checks, against ONLY the directory skeleton, "
        "identical in a depth-1 clone: _create_patient_summary states elapsed "
        "time beside EVERY date it renders. render_bare() shuts the three "
        "doors every temporal phrase comes through, so the control needs "
        "neither an exec nor a `git show` and the file runs in a tree with no "
        ".git. The MeSH filter is overridden to None -- a documented "
        "reachable state -- so no data file is read, and the cancer and lab "
        "registries are the real ones, which read no files either. No "
        "network, no keys, no spend, no corpus, and it writes nothing "
        "anywhere"),
    "test_agent_structured_outputs.py": (
        _A, None,
        "ran green in 2.0s, 152 checks; the Stage 5 response schema, the reasoning-first field order and the "
        "refusal path. Every model response is a literal served through a "
        "deps stub, every plant doctors a COPY of a dict the shipped builder "
        "returns or an in-memory AST, and it execs nothing -- no network, no "
        "keys, no spend, no git, no corpus, no database, no subprocess"),
    "test_agent_temporal_conflict_flag.py": (
        _A, None,
        "ran green in 2.1s, 118 checks, against ONLY the directory skeleton, "
        "identical in a depth-1 clone: the RULE 4 temporal-conflict flag that "
        "is COUNTED and never applied. Every plant goes into an in-memory "
        "copy of agent/evaluation.py or observability.py, both hashed before "
        "any plant and compared at the end -- no network, no keys, no spend, "
        "no git history, no corpus. An _EXEC_ALLOWLIST member; its eighth "
        "copy is an equivalence proof rather than a control"),
    "test_agent_trial_data_fencing.py": (
        _A, None,
        "ran green in 0.02s of checks, 86 checks; the TRIAL_DATA fences in the "
        "USER message, the fence-marker neutralization of scraped text, and "
        "the system prompt's C6 data boundary. Every trial is a literal dict "
        "and the renderer is a pure function of it; the ten controls plant "
        "into in-memory copies of agent/evaluation.py and agent/prompts.py -- "
        "no network, no keys, no spend, no git, no corpus, no database, no "
        "subprocess, no model call"),
    "test_agent_trial_verdict_normalization.py": (
        _A, None, "ran green in 1.9s; every model response is a literal via a deps stub"),
    "test_agent_unsupported_rejection.py": (
        _A, None,
        "ran green in 1.3s, 133 checks, against ONLY the directory skeleton, "
        "identical in a depth-1 clone: a model-declared not_eligible carrying "
        "no disqualifying row in either criteria array is corrected rather "
        "than stored as a rejection nobody made. Every plant goes into an "
        "in-memory copy of agent/evaluation.py, and that copy carries its OWN "
        "anomaly counter so a control can fire without touching the live one "
        "this file asserts is at zero. No network, no keys, no spend, no git "
        "history, no corpus. An _EXEC_ALLOWLIST member, argued there"),
    "test_agent_user_message_snapshot.py": (
        _A, None,
        "ran green in 1.3s, 51 checks, against ONLY the directory skeleton, "
        "identical in a depth-1 clone: the Stage 5 USER message cannot change "
        "without somebody deciding it did. The reference is a committed "
        "golden file regenerated ONLY through --update-snapshot, deliberately "
        "NOT a `git show` -- a commit recedes, and three files in this suite "
        "already abort in a tree with no .git. NO NETWORK, no keys, no spend, "
        "no database, no subprocess, no fixture, no git history, no corpus, "
        "no model call: it renders three trials into a string, hashes it, "
        "parses two files and reads one JSON file"),
    "test_clinical_use_framing.py": (
        _A, None,
        "ran green in 2.3s, 71 checks, against ONLY the directory skeleton: "
        "the not-for-clinical-use framing on POST /match, POST /match/file, "
        "GET /pipeline/info and the dashboard page. The three responses are "
        "driven through FastAPI's TestClient WITHOUT its context manager, so "
        "no lifespan runs; the pipeline and the database writer are replaced "
        "on the server module and the Qdrant client through "
        "oncotriage/agent/deps.py, and the dashboard renders all nine tabs "
        "from a scratch SQLite file built by initialize_database(). Every "
        "plant goes into an in-memory copy or a temp-directory copy. No "
        "network (MEASURED -- all four socket entry points are replaced by a "
        "recorder that raises, with a control that makes a real call), no "
        "keys, no spend, no live server, no live Qdrant, no git, no corpus. "
        "It DOES need the placeholder .env to EXIST, which this skeleton "
        "writes, because GET /pipeline/info reports which source supplied the "
        "Qdrant endpoint -- the same precondition "
        "test_agent_retrieval_observability.py carries"),
    "test_dashboard_app_integration.py": (
        _A, None,
        "ran green in 2.0s, 110 checks -- 110 passed, 0 failed, 0 skipped -- "
        "against the developer tree, and its one gated probe records a SKIP "
        "on a checkout with no production inferences.db (the byte-identity "
        "check it guards stays LIVE either way). Renders "
        "oncotriage.dashboard.app:main() end to end plus five tab functions "
        "against ONE seeded scratch database inside a tempfile.mkdtemp it "
        "removes, with paths._RESOLVED repointed at it and restored. No "
        "network (measured -- every render runs with socket.connect/"
        "connect_ex/create_connection/getaddrinfo replaced by a recorder that "
        "RAISES, with a control that makes a real call and is named in the "
        "record), no keys, no spend, no live Qdrant, no model load, no corpus, "
        "no git history, no live server. NOT in the collision matrix: it "
        "writes only inside its temp directory, and the six repository files "
        "it READS -- dashboard/app.py, dashboard/nullsafe.py and four tab "
        "modules -- are written by neither of the suite's two writers and are "
        "sha256-compared at the end. It EXECS NOTHING: every plant is a COPY "
        "written to the temp directory and imported from there"),
    "test_dashboard_run_health.py": (
        _A, None,
        # RE-READ OFF A REAL RUN (this pass), and the string it replaces was
        # not: it claimed 155 checks green "against ONLY the directory
        # skeleton", and on a skeleton this file has NEVER been green -- 154
        # passed / 1 failed, from its first and only commit, measured by
        # running that commit. The recorded run was against the developer tree,
        # which has a production inferences.db; only the count was true of it.
        # The probe that failed is now GATED -- see section 7 of the file for
        # the ruling and its five controls.
        "ran green in 0.8s (2.4s wall), 167 checks -- 166 passed, 0 failed and "
        "1 SKIPPED -- against ONLY the directory skeleton, and 167/0/0 against "
        "the developer tree: "
        "the Run Health tab and the four run loaders. No network (measured -- "
        "every render runs with socket.connect/connect_ex/create_connection/"
        "getaddrinfo replaced by a recorder that RAISES, with a control that "
        "makes a real call), no keys, no spend, no live Qdrant, no model load, "
        "no corpus, no git history. Six scratch databases are built by the "
        "project's own initialize_database() inside a tempfile.mkdtemp it "
        "removes, and paths._RESOLVED is repointed at them and restored, so "
        "the production database is never opened -- asserted behaviourally by "
        "recording sqlite3.connect, with a DECOY control that shows the "
        "assertion failing. Its eight planted defects go into COPIES of the "
        "tab written to that temp directory and imported from there, so it "
        "execs nothing and needs no _EXEC_ALLOWLIST entry, and the two "
        "repository files it reads (dashboard/tabs/run_health.py, "
        "dashboard/data.py) are written by neither of the suite's two writers; "
        "the third file it reads is ITS OWN SOURCE, for the AST pins on the "
        "gate call site and on skip()'s accounting -- so it is NOT in the "
        "collision matrix. UNLIKE "
        "test_dashboard_reproducibility_tab.py it has no golden snapshot and "
        "so is not pinned to a streamlit version's element vocabulary; it "
        "asserts values derived from its own seed. THE ONE SKIP is the "
        "non-degeneracy probe on the 'the production database is byte-identical' "
        "hygiene check: it needs a READABLE production database and "
        "provision_ci_paths.py deliberately creates the parent directory and "
        "not the file. The hygiene check itself is NEVER gated, so a run that "
        "CREATED a production database still fails here ('absent' != <hash>); "
        "the gate is keyed on os.path.exists rather than on the digest the "
        "probe asserts about, so the fault the probe catches cannot satisfy "
        "the gate, and five controls plus an AST pin on the call site keep the "
        "skip path from becoming the only path"),
    "test_dashboard_reproducibility_tab.py": (
        _A, None,
        "parallel-safe and needs no external data (green in 7.2s on streamlit "
        "1.45.1, identical in a depth-1 clone) -- but RED on streamlit 1.46.0, "
        "which is what pyproject.toml pins. The committed golden snapshot "
        "records element type 'vertical'; 1.46.0 emits 'flex_container'. That "
        "is a repository defect, not a CI one, and it is deliberately NOT "
        "suppressed here -- see the CI report."),
    "test_evaluation_rater.py": (
        _A, None,
        "ran green in 1.1s, 311 checks, against ONLY the directory skeleton, "
        "identical in a depth-1 clone: the blind rater harness. IT DOES NOT "
        "NEED ragas INSTALLED -- measured, the interpreter that ran it green "
        "has no ragas distribution at all; `09- Testing/ragas-venv/` is a "
        "separate environment and nothing here imports out of it. One control "
        "cannot be input-based and says so: section 7j rebinds "
        "rater.render_system_prompt inside try/finally and asserts the "
        "restore BY IDENTITY, which is an attribute rebind rather than a "
        "patched source, so it execs nothing. The one thing it writes is a "
        "fresh tempfile.mkdtemp holding two state files, removed in a finally "
        "with the removal then ASSERTED -- nothing in the repository, so it "
        "is not in the collision matrix. No network, no keys, no spend, no "
        "corpus"),
    "test_evaluation_ragas_manifest.py": (
        _A, None,
        "ran green in 1.5s, 69 checks, against ONLY the directory skeleton "
        "this script provisions: it reads no evaluation run (the RunInput is "
        "built from literal strings), writes nothing anywhere (the JSON "
        "round-trip goes through json.dumps, never write_json), needs no git "
        "history, and asks importlib.metadata about four distributions "
        "whichever of them are installed -- the absent path is driven both "
        "naturally and through a planted lookup, so the outcome does not "
        "depend on what the runner happens to have. Section 7 spawns one "
        "subprocess that imports the harness and takes a stamp"),
    "test_evaluation_sample_naming.py": (
        _A, None,
        "ran green in 1.0s, 72 checks, against ONLY the directory skeleton "
        "this script provisions -- and in fact against no data tree at all: "
        "paths._RESOLVED is seeded with a scratch results root, so no glob "
        "fires and default_output_db() never reaches the sibling tree. No "
        "network, no keys, no spend, no live Qdrant, no model, no corpus, no "
        "database, no git history and no subprocess. It EXECS NOTHING -- the "
        "four plants are ast walks over in-memory copies -- and it writes "
        "nothing outside a tempfile.mkdtemp it removes, so it is not in the "
        "collision matrix either: the three files it READS are "
        "oncotriage/evaluation/sampling.py, 28- Select Evaluation Sample.py "
        "and oncotriage/evaluation/medcpt_calibration.py, written by none of "
        "the matrix's two writers. It IMPORTS medcpt_calibration (section 7), "
        "which reaches langgraph transitively -- inert at import, which "
        "tests/test_package_invariants.py section 2 is what proves"),
    "test_extraction_histology.py": (
        _A, None, "ran green in 0.1s, 133 checks, identical in a depth-1 clone"),
    "test_extraction_stage_m_category.py": (
        _A, None, "ran green in 2.8s; every fixture is a literal dict"),
    "test_extraction_stage_non_oncology_guard.py": (
        _A, None, "ran green in 2.6s; no corpus, no git"),
    "test_fhir_birth_date_and_demographics.py": (
        _A, None, "ran green in 2.3s"),
    "test_fixtures_harness_hardening.py": (
        _A, None,
        "ran green in 1.0s, 116 checks, against ONLY the directory skeleton, "
        "identical in a depth-1 clone: the characterization fixture harness's "
        "own refusals. Every file it writes is inside a fresh "
        "tempfile.mkdtemp(), it patches no repository file, and the two "
        "package modules it reads are written by neither of the suite's two "
        "writers, so it is NOT in the collision matrix -- derived, not "
        "assumed. It costs and needs nothing: no network, no keys, no spend, "
        "no live Qdrant, no corpus, no git history, no Docker. NOTE the "
        "separation from fixture_capture.py / fixture_replay.py themselves, "
        "which are bucket D and C in NON_TEST_ENTRY_POINTS below: this file "
        "exercises the harness's guards, never a capture or a replay"),
    "test_resume_capture_and_ragas.py": (
        _A, None,
        "ran green in 6.4s, 207 checks, against ONLY the directory skeleton: "
        "both paid harnesses' resume mechanisms. fixture_capture.py's "
        "--resume gate (all seven RESUME_OUTCOMES, and main()'s plan loop "
        "DRIVEN end to end with the paid and networked seams replaced by "
        "stand-ins, so the donor arithmetic, the temporary-bundle cleanup and "
        "the retry-base selection are the shipped code rather than a second "
        "implementation), and ragas_run.py's per-pair score journal (atomic "
        "write, torn-write recovery, per-pair reuse by identity and input "
        "fingerprint, the changed-environment refusal, and the merged set "
        "passing post_checks). It costs and needs nothing: no network, no "
        "keys, no spend, no live judge, no live Qdrant, no corpus, no git "
        "history, no Docker. Every fixture it reads is one it wrote into a "
        "fresh tempfile.mkdtemp(); paths.data_fhir_path is redirected through "
        "paths._RESOLVED and restored; fixture_root() is never called, so the "
        "twelve on-disk fixtures are never opened and a bug here cannot cost "
        "a re-capture. NOT in the collision matrix -- derived, not assumed: it "
        "patches no repository file, and the two package modules it reads "
        "(oncotriage/fixtures/capture.py, oncotriage/evaluation/"
        "ragas_harness.py) are written by neither of the suite's two writers; "
        "both are sha256-compared in section 8"),
    "test_resume_configuration_fingerprint.py": (
        _A, None,
        "ran green in 3.0s, 404 checks, against ONLY the directory skeleton: "
        "the configuration fingerprint and the three resume gates built on "
        "it. Section 1b covers llm_classifier_renderer_digest -- the hashed "
        "module set re-derived by a static closure over the render path, the "
        "AST normalisation shown to see an executable edit and not a comment, "
        "and a one-character renderer edit made in a COPY of the package shown "
        "to move the digest and answer FP_CHANGED with PROMPT_VERSION unmoved. run_harness.main() is DRIVEN end to end -- the fresh run, the "
        "--only, the --resume skip/re-run, four environment refusals and the "
        "override -- with run_one_patient replaced by a stand-in whose "
        "installation is asserted BY IDENTITY before each scenario, so a "
        "stand-in that failed to take fails here rather than reaching the "
        "OpenAI endpoint. The only live Qdrant work in the whole pass is "
        "run_fingerprint._resolve_collection, replaced by a two-line "
        "stand-in; the shipped current()/compare()/refusal path runs for "
        "real. It costs and needs nothing: no network, no keys, no spend, no "
        "live Qdrant, no live server, no corpus, no git history, no Docker, "
        "no database. It EXECS NOTHING -- every control is either a different "
        "INPUT to a pure function or an attribute rebind inside try/finally "
        "with the restore asserted by identity -- so it needs no "
        "_EXEC_ALLOWLIST entry. NOT in the collision matrix, derived: "
        "everything it writes is inside one tempfile.mkdtemp() that it "
        "removes and then asserts gone, and the source it DOES parse (section "
        "1b, exactly run_fingerprint.RENDERER_MODULES plus the two consumer "
        "banners) is written by neither of the suite's two writers -- "
        "oncotriage/config.py is recorded by the closure as an excluded "
        "module and never opened, and config.MATCHING_MODEL and "
        "config.DATA_SNAPSHOT_DATE are rebound as in-memory ATTRIBUTES and "
        "restored, which touches no file)"),
    "test_dockerignore_exclusions.py": (
        _A, None,
        "ran green in 0.04s, 36 checks / 0 skipped, against ONLY the directory "
        "skeleton -- and identically with ONCOTRIAGE_MAIN_PATH pointed at a "
        "directory that does not exist, because it imports nothing from the "
        "package: its subject is .dockerignore. It holds two things nothing "
        "else in the repository does -- that every directory carrying a "
        "pyvenv.cfg is named in .dockerignore (marker-based, so a renumber of "
        "`09- Testing/` fails here instead of silently returning 1.7 GB to "
        "the build context), and that the exclusion is not DEAD. IT CARRIES A "
        "SKIP MECHANISM AND THAT IS LOAD-BEARING FOR CI: the only venv this "
        "project has is untracked and self-ignored, so no hosted runner has "
        "one, and the tree-dependent half records 2 SKIPS there rather than "
        "failing -- a skip is not a pass and the count is printed even at "
        "zero. Everything that reads the committed .dockerignore, and every "
        "control, still runs on a runner, because the controls drive pure "
        "functions with fabricated inputs. No network, no keys, no spend, no "
        "Docker daemon, no live Qdrant, no corpus, no database, no git "
        "history, no subprocess, and it execs nothing. NOT in the collision "
        "matrix, derived: it writes no repository file, everything it writes "
        "is four marker files inside one tempfile.mkdtemp() it removes and "
        "then asserts gone, and .dockerignore -- the only repository file it "
        "reads -- is written by neither of the suite's two writers and is "
        "sha256-compared in its section 6"),
    "test_harness_endpoint_budget.py": (
        _A, None,
        "ran green in 0.9s, 38 checks, against ONLY the directory skeleton: "
        "the derived POST read budget (value AND shape), its inequality "
        "against MATCHING_REQUEST_TIMEOUT_SECONDS, File 19's "
        "ConnectTimeout-before-Timeout handler order, and the config-owned GET "
        "budget with the assertion that NO requests call in either harness "
        "lacks an explicit timeout=. IT STARTS NO SERVER, ISSUES NO REQUEST "
        "AND IMPORTS NO `requests`: both harnesses are read as TEXT and "
        "parsed, which is also why it cannot spend a cent against files that "
        "are bucket D when run. Every plant goes into an in-memory ast copy "
        "and both harness files are re-read and compared at the end, so it "
        "writes nothing anywhere. No network, no keys, no spend, no live "
        "server, no live Qdrant, no corpus, no database, no git history, no "
        "subprocess, and it execs nothing (section 1 evaluates ONE arithmetic "
        "expression node through eval, which is not exec and loads no "
        "module), so it needs no _EXEC_ALLOWLIST entry. NOT in the collision "
        "matrix: it reads oncotriage/config.py, which "
        "tests/test_config_snapshot_date_rot.py writes -- but that writer "
        "rewrites only the DATA_SNAPSHOT_DATE literal and restores it "
        "byte-identically, and touches no HARNESS_* line this file asserts on"),
    "test_indexer_criteria_split_gate.py": (
        _A, None,
        "ran green in 0.97s, 153 checks, against ONLY the directory skeleton: "
        "the criteria_split ingestion gate inside verify_collection, plus "
        "section 9's embedding-batch config ownership (declared in the file's "
        "own docstring as a second subject). The gate decision is a pure "
        "function of a counted distribution and every "
        "input here is a literal dict; the Qdrant scroll is driven against a "
        "paging stand-in and every structural check mutates an ast COPY "
        "in memory. No network, no keys, no spend, no live Qdrant, no git, no "
        "corpus, no database, no subprocess, and it execs nothing. NOTE the "
        "separation from test_indexer_admission_filters.py, which is bucket E "
        "for the UMLS-derived non-oncology lookup: nothing here touches the "
        "MeSH filter, which is why this is a new file rather than a section "
        "added to that one"),
    "test_observability_logging.py": (
        _A, None, "ran green in 9.2s; all six stages driven with deps stand-ins"),
    "test_paths_glob_determinism.py": (
        _A, None, "ran green in 0.1s against the skeleton; asserts 18 resolvers"),
    "test_paths_portability_roots.py": (
        _A, None,
        "ran green in 2.6s, 101 checks, against ONLY the directory skeleton: "
        "the two Testing roots and the model cache promoted into PATH_NAMES, "
        "and the three load sites that pin the model caches. No network, no "
        "keys, no spend, no live Qdrant, no corpus, no database and no git "
        "history; NO MODEL IS LOADED -- ONCOTRIAGE_DEFER_LOCAL_MODELS is set "
        "above the imports and section 8p asserts torch and transformers never "
        "entered sys.modules. Every root it resolves is FABRICATED under a "
        "tempfile.mkdtemp it removes and asserts gone, reached by seeding "
        "paths._RESOLVED and restoring it, so nothing outside that directory "
        "is written. It EXECS NOTHING -- every control is a different INPUT to "
        "a function, an ast walk over an in-memory copy, or an attribute "
        "rebound inside try/finally with the restore asserted -- and the five "
        "repository files it reads (paths.py, fixtures/capture.py, "
        "evaluation/run_harness.py, agent/deps.py, embedding.py) are written "
        "by neither of the suite's two writers, so it is NOT in the collision "
        "matrix"),
    "test_registries_cancer_codes_and_stage_extraction.py": (
        _A, None, "ran green in 0.2s"),
    "test_storage_inference_logging_contract.py": (
        _A, None, "ran green in 1.8s; temp SQLite only"),
    "test_staging_exclusions.py": (
        _A, None,
        "ran green in 1.1s, 117 checks, against ONLY the provisioned CI "
        "skeleton (verified by running it under ONCOTRIAGE_MAIN_PATH pointed "
        "at a fresh provision_ci_paths.py root, not assumed): the S3 staging "
        "exclusion rulings and the secrets refusal. NO NETWORK AND NO AWS SDK "
        "-- section 5 drives preflight() with a stand-in session_factory and "
        "section 5g asserts boto3 never entered sys.modules, which is what "
        "makes 'no network' a measurement. No keys, no spend, no live Qdrant, "
        "no model load, no corpus, no database, no git history and no "
        "subprocess. It EXECS NOTHING -- every control is a different INPUT to "
        "a function, or a manifest fabricated inside a tempfile.mkdtemp it "
        "removes and asserts gone -- so it needs no _EXEC_ALLOWLIST entry. NOT "
        "in the collision matrix: the one repository file it reads, "
        "s3_staging_exclusions.json, is written by neither of the suite's two "
        "writers, and section 4b-control additionally scans the test file "
        "itself and requires zero credential shapes in it"),
    "test_storage_provenance_persistence.py": (
        _A, None,
        "ran green in ~7s, 126 checks, against ONLY the directory skeleton: "
        "the seven Stage 5 normalizer-provenance columns through the real "
        "migration and the real log_inference, and the stamps through the real "
        "Stage 5 node and the real node_finalize on a StateGraph over the real "
        "TrialMatchState. Every database is a temp file every call is pointed "
        "at explicitly, every patient and trial is a literal dict, and the "
        "OpenAI client is a stand-in installed through oncotriage/agent/deps.py "
        "with the SCOPED deps.override -- no network, no keys, no spend, no "
        "live Qdrant, no git, no corpus. It DOES exec: five controls plant into "
        "in-memory copies of database_logger.py and evaluation.py, argued at "
        "_EXEC_ALLOWLIST in tests/test_package_invariants.py. It writes nothing "
        "in the repository and is not in the collision matrix -- the four "
        "package files it reads (storage/database_logger.py, "
        "agent/evaluation.py, agent/response_schema.py, fixtures/capture.py) "
        "are written by neither of the suite's two writers, and all four are "
        "sha256-compared at the end."),
    "test_storage_packing_and_cache_columns.py": (
        _A, None,
        "ran green in <1s, 124 checks, against ONLY the directory skeleton: "
        "the four Stage 5 packing/cache columns through the real migration and "
        "the real log_inference, and the billed-token failure returns through "
        "the real node. Every database is a temp file every call is pointed at "
        "explicitly, every patient and trial is a literal dict, and the OpenAI "
        "client is a stand-in installed through oncotriage/agent/deps.py with "
        "the SCOPED deps.override, so a raising stub cannot leak into a later "
        "section -- no network, no keys, no spend, no live Qdrant, no git, no "
        "corpus. It DOES exec: five controls plant into in-memory copies of "
        "database_logger.py and evaluation.py, argued at _EXEC_ALLOWLIST in "
        "tests/test_package_invariants.py. It writes nothing in the repository "
        "-- the two files it reads are sha256-compared at the end -- so it is "
        "not in the collision matrix"),
    "test_storage_query_layer.py": (
        _A, None, "ran green in 2.6s, 191 checks, identical in a depth-1 clone"),
    "test_trivyignore_staleness.py": (
        _A, None,
        "ran green in 0.9s, 181 checks, against ONLY the directory skeleton "
        "-- and identically with ONCOTRIAGE_MAIN_PATH pointed at a directory "
        "that does not exist, because it imports nothing from the package: "
        "its subject is .github/scripts/trivyignore_staleness.py, a "
        "standalone script that must run before anything is installed. Every "
        "scenario DRIVES THAT SCRIPT AS A SUBPROCESS (sys.executable + its "
        "path) with cwd set to the temp directory, which is also what "
        "measures the script's claim that its defaults resolve off __file__ "
        "rather than off the working directory. The Trivy report is a "
        "miniature literal in the test file whose ADEQUACY is derived by AST "
        "from the script (every data/result/vuln key the script reads must be "
        "present), so a field it starts reading fails there rather than being "
        "silently absent. It execs nothing and imports nothing from the "
        "script, so section 1c of tests/test_package_invariants.py has "
        "nothing to see and it needs no _EXEC_ALLOWLIST entry. No network, no "
        "keys, no spend, no Docker daemon, no live Qdrant, no corpus, no "
        "database, and no git history -- verified by running it green in a "
        "four-file copy of the repository carrying no .git at all. NOT in the "
        "collision matrix, derived: it writes NO repository file, everything "
        "it writes is inside one tempfile.mkdtemp() it removes and then "
        "asserts gone, and the three files it READS (the script, "
        ".trivyignore, .github/workflows/ci.yml) are written by neither of "
        "the suite's two writers -- all three sha256-compared in its section "
        "15"),
    "test_tracking_mlflow_index.py": (
        _A, None,
        "ran green in 1.4s, 99 checks, against ONLY the directory skeleton: the "
        "MLflow file-store round trip, the parameter enumeration, the two "
        "degrade-to-unknown paths and the missing-package refusal. The tracking "
        "store is a temp directory installed into paths._RESOLVED, "
        "resolve_qdrant_collection is replaced by a stand-in (it is the module's "
        "one live call) and the git probe is driven through a stubbed subprocess "
        "-- no network, no keys, no spend, no live Qdrant, no corpus, and no git "
        "history REQUIRED: section 8f accepts either outcome from the real probe "
        "so a `git archive` export reports rather than aborts. It execs nothing; "
        "the missing-package control masks sys.modules['mlflow'], which drives "
        "the SHIPPED function because the import is deferred into it"),

    # ---- B: the collision matrix ------------------------------------------
    # Bucket assignment is asserted against run_serial_tests.py in check_complete().
    "test_registries_cancer_code_claims_audit.py": (
        _B, "UMLS MRCONSO*.RRF (licence-gated, ~1.5 GB, not redistributable)",
        "'CANNOT RUN: UMLS MRCONSO not found' -- refuses rather than passing vacuously"),
    "test_registries_cancer_code_claims_audit_control.py": (
        _B, "UMLS MRCONSO*.RRF (it runs the audit above as its baseline)",
        "'File 42 exited 1 with NO defect planted, so a non-zero exit proves nothing'"),
    "test_degradation_counter_readers.py": (
        _A, None,
        "ran green in 3s, 138 checks, against ONLY the directory skeleton: "
        "every module-level Counter in the package and at the repository root "
        "is registered in oncotriage/degradation.py, census-registered there, "
        "or exempted with a named production reader that is then CHECKED by "
        "ast to contain a genuine read. No network, no keys, no spend, no "
        "live Qdrant, no model load, no corpus, no git history. The one "
        "database is a temp file built by the project's own "
        "initialize_database() inside a tempfile.mkdtemp it removes and then "
        "asserts gone, with paths._RESOLVED seeded so nothing can resolve to "
        "the production tree. It EXECS NOTHING -- every control is a "
        "different INPUT to a function of its argument (including a control "
        "MODULE written to that temp directory and parsed, never imported), "
        "an ast walk, or a registry entry removed inside try/finally with the "
        "restore asserted -- so it needs no _EXEC_ALLOWLIST entry. It writes "
        "nothing in the repository and the four package files it reads "
        "(degradation.py, retrieval/indexer.py, ablation/study.py, "
        "batch/runner.py) are sha256-compared at the end and are written by "
        "neither of the suite's two writers, so it is NOT in the collision "
        "matrix"),
    "test_degraded_dependencies.py": (
        _B, "the real Synthea patient corpus",
        "148 passed, 1 failed on the skeleton: 'there are real bundles to "
        "copy (non-degeneracy): 0'. THE EVIDENCE HERE USED TO READ 'IndexError "
        "on an empty corpus list, then NameError: _dry_counts' -- that cascade "
        "buried the real cause and killed the file with no summary; it is "
        "fixed, and the recorded outcome is now the diagnostic the file always "
        "measured and never got to print"),
    "test_config_snapshot_date_rot.py": (
        _B, "the generated scratch_ecog Synthea corpus (it runs ecog_surfacing)",
        "'baseline for test_fhir_ecog_surfacing.py is usable' failed"),
    "test_package_invariants.py": (
        _B, None,
        "ran green in 35.0s inside `make serial-tests` with only the skeleton"),

    # ---- C: needs a live Qdrant endpoint -----------------------------------
    "test_mcp_server_stdio_contract.py": (
        _C, "a live Qdrant collection and a corpus bundle path",
        "sections 4-6 make real Qdrant round trips; died resolving a bundle path"),

    # ---- E: needs uncommitted data ----------------------------------------
    "test_ablation_db_isolation.py": (
        _E, "the production ablation_results.db",
        "'the digest is a real one, not absent on both sides (non-degeneracy)' failed"),
    # The two escape-decode files need the TRIAL corpus, which no entry above
    # needed: `{data_trial_path}/trials_latest.json`, ~152 MB, written by
    # `11- RAG Trial Indexer.py` from a live ClinicalTrials.gov scrape and not
    # committed. Both census sections read it directly, and both files say in
    # their own first check that a missing corpus is a FAILURE and never a
    # silent skip -- which is what makes the bucket unambiguous.
    "test_agent_escaped_entity_decode.py": (
        _E, "the scraped trial corpus (trials_latest.json, ~152 MB, not committed)",
        "'1a the trial corpus was readable (a missing corpus is a FAILURE "
        "here, never a silent skip)' failed with FileNotFoundError on "
        "trials_latest.json -- and the file then ABORTED at `max(_depths)` "
        "over the empty census with 'ValueError: max() iterable argument is "
        "empty', so it recorded 10 checks and then printed a traceback where "
        "it owed a summary. (10 is what it reached before dying; 112 is what "
        "the same file reports WITH the corpus, and the number it would owe "
        "without one is neither of those -- which is the point, because a "
        "run that dies has no total.) THAT ABORT IS A DEFECT IN THAT FILE, "
        "not a "
        "reason to classify it anywhere else, and it is left to its owner: "
        "this table records what CI can run, and editing a test to fix its "
        "reporting is not that. Bucket E rather than a repository defect is "
        "MEASURED -- with the corpus present it is green, exit 0, 112 passed, "
        "0 failed"),
    "test_agent_markdown_escape_decode.py": (
        _E, "the scraped trial corpus (trials_latest.json, as above)",
        "'2a the trial corpus was readable (a missing corpus is a FAILURE "
        "here, never a silent skip)' failed with FileNotFoundError on "
        "trials_latest.json; 25 recorded failures and it ran to its own "
        "summary rather than aborting, which is the difference from its "
        "sibling above. With the corpus present it is green, exit 0, 176 "
        "passed, 0 failed"),
    "test_agent_patient_hash_coverage.py": (
        _E, "the real patient corpus (sections 4 and 7 parse bundles read-only)",
        "2 failures, both corpus non-degeneracy guards"),
    "test_docker_qdrant_override_and_readiness.py": (
        _E, "a real .env carrying an https Qdrant Cloud URL",
        "121 passed, 1 failed: 'the url is a real https endpoint (non-degenerate)'"),
    "test_fhir_ecog_surfacing.py": (
        _E, "the generated scratch_ecog Synthea corpus",
        "'scratch corpus not found at .../01- Patients/scratch_ecog/fhir/'"),
    "test_fhir_parser_dict_input.py": (
        _E, "the real patient corpus",
        "'a corpus bundle was found (non-degeneracy)' failed"),
    "test_indexer_admission_filters.py": (
        _E, "mesh_non_oncology_lookup.json (UMLS-derived, deliberately not vendored)",
        "KeyError: 'diabetes mellitus' -- the non-oncology lookup is absent"),
    "test_monitoring_ecog_availability_drift.py": (
        _E, "the production inferences.db",
        "'the production table was readable, so the count is a real number' failed"),
    "test_registries_mesh_pan_cancer_resolution.py": (
        _E, "the three UMLS-derived SNOMED->CUI->MeSH crosswalks (not vendored)",
        "resolution fell back to fuzzy_stem/unmapped where snomed was expected"),
    "test_storage_ecog_logging.py": (
        _E, "the generated scratch_ecog Synthea corpus",
        "'scratch corpus not found ... Generate it first: 04- FHIR Generate Data.py'"),
    "test_storage_wipe_all_tables.py": (
        _E, "the production inferences.db (it reads its real schema)",
        "'the production schema was read and is non-degenerate' failed"),
    "test_storage_run_metrics_flush.py": (
        _A, None,
        "ran green in ~10s, 109 checks, against ONLY the directory skeleton: "
        "the `run_metrics` table through the real initialize_database, the "
        "real flush_run_metrics and the real runner.flush_health, plus "
        "run_batch and run_resample driven with an erroring stand-in for "
        "process_patient. Every database is a temp file, paths._RESOLVED is "
        "seeded so nothing can resolve to the production tree, and "
        "ONCOTRIAGE_INFERENCES_DB is cleared so an exported one cannot make "
        "the isolation checks compare two scratch paths -- no network, no "
        "keys, no spend, no live Qdrant, no model load, no corpus, no git. It "
        "execs nothing: every control is a real failing condition created on "
        "disk, an alternative implementation written out for comparison, or an "
        "ast walk over a parsed source file. The ~10s is section 7's "
        "MAX_WORKERS threads flushing behind a barrier while another inserts "
        "counter keys under them"),
    "test_storage_schema_guards.py": (
        _A, None,
        # DERIVED BY RUNNING against a skeleton provisioned by
        # provision_ci_paths.py, not from its imports.
        "ran green in ~1.4s, 101 checks -- 101 passed, 0 failed, 0 skipped -- "
        "against ONLY the directory skeleton, and identically against the "
        "developer tree: the requires_columns derivation over the whole query "
        "registry, the rename record against a real fresh database, the "
        "trial_matches child-lookup index by EXPLAIN QUERY PLAN on a seeded "
        "database with an unindexed one as the control, PRAGMA user_version "
        "stamped/preserved/bumped/never-lowered, and report() driven end to "
        "end against a PRE-MIGRATION database. No network, no keys, no spend, "
        "no live Qdrant, no model load, no corpus, no git history and no live "
        "server. THE PRODUCTION DATABASE IS NEVER OPENED, not even read-only: "
        "the pre-migration shape is built from database_logger's own constants "
        "by renaming and dropping columns on a fresh database, which is why "
        "the skeleton and the developer tree give the same number. Every "
        "database is inside a tempfile.mkdtemp it removes and asserts gone, "
        "and the two package files it reads are sha256-compared at the end"),
    "test_runner_crash_record_and_db_unification.py": (
        _A, None,
        # DERIVED BY RUNNING against a skeleton provisioned by
        # provision_ci_paths.py, not from its imports.
        "ran green in ~11s, 65 checks -- 65 passed, 0 failed, 0 skipped -- "
        "against ONLY the directory skeleton: print_crash_record driven "
        "directly including its never-raises contract, an ast walk over main() "
        "placing the call in both BaseException handlers and on neither "
        "success path, and main() DRIVEN END TO END four times -- a planted "
        "mid-batch crash, a clean run, a mid-run ONCOTRIAGE_INFERENCES_DB "
        "hijack, and a fresh/resumed pair. The BM25 index, the graph, the "
        "tracking module and process_patient are stand-ins and THE GRAPH IS "
        "NEVER INVOKED, so no billed call is reachable; everything else -- "
        "run_batch, _on_done, flush_health, start_run_record, "
        "finalize_run_record, reconcile_writes, print_summary and both crash "
        "handlers -- is the real thing. No network, no keys, no spend, no live "
        "Qdrant, no model load, no corpus, no git history, no live server. The "
        "~11s is two real ThreadPoolExecutor passes per drive. Every database "
        "and FHIR file is inside a tempfile.mkdtemp it removes and asserts "
        "gone; the two package files it reads are sha256-compared at the end",
    ),
    "test_storage_run_identity.py": (
        _A, None,
        # RE-READ OFF A REAL RUN (this pass). The string it replaces was wrong
        # twice: "119 checks" was true of no run ever -- the file reported 121
        # at the commit that introduced it, measured by checking that commit
        # out and running it -- and "green against ONLY the directory skeleton"
        # was read off the developer tree, which has a production
        # inferences.db. On a skeleton it was 120 passed / 1 failed from day
        # one. The probe that failed is now GATED -- see section 10 of the file
        # for the ruling and its five controls.
        "ran green in ~1.0s, 133 checks -- 132 passed, 0 failed and 1 SKIPPED "
        "-- against ONLY the directory skeleton, and 133/0/0 against the "
        "developer tree: the "
        "`runs` table and the `inferences.run_id` reference through the real "
        "initialize_database and the real log_inference, plus run_batch and "
        "run_resample driven with a recording stand-in for process_patient. "
        "Every database is a temp file, paths._RESOLVED is seeded so nothing "
        "can resolve to the production tree, and ONCOTRIAGE_INFERENCES_DB is "
        "cleared so an exported one cannot make the isolation checks compare "
        "two scratch paths -- no network, no keys, no spend, no live Qdrant, no "
        "model load, no corpus, no git. It execs nothing: every control is a "
        "different INPUT to a pure function, a real failing condition created "
        "on disk, or an ast walk over an in-memory copy -- one of them over ITS "
        "OWN SOURCE, for the AST pins on the gate call site and on skip()'s "
        "accounting. THE ONE SKIP is the "
        "non-degeneracy probe on the 'the production database is "
        "byte-identical' hygiene check: it needs a READABLE production database "
        "and provision_ci_paths.py deliberately creates the parent directory "
        "and not the file. The hygiene check itself is NEVER gated, so a run "
        "that CREATED a production database still fails here ('absent' != "
        "<hash>); the gate is keyed on os.path.exists rather than on the sha "
        "the probe asserts about, so the fault the probe catches cannot satisfy "
        "the gate, and five controls plus an AST pin on the call site keep the "
        "skip path from becoming the only path"),
    "test_storage_write_durability.py": (
        _E, "the production inferences.db",
        "'9c ...and it was readable, so that comparison is not None == None' failed"),
}


# Not under tests/ -- the numbered entry points and the two fixture harnesses.
# Listed so the classification the brief asked for is complete, and so nobody
# wires one of them into CI later without meeting this table first.
NON_TEST_ENTRY_POINTS = {
    "18- FastAPI Server Test.py": (
        _D, "a live server on :8000 AND real spend",
        "every POST is a billed Stage 5 call, $0.13-$0.17 per patient"),
    "19- FastAPI Server Batch Test.py": (
        _D, "a live server on :8000 AND real spend",
        "same; slices fhir_files[410:412], so it also needs the corpus"),
    "fixture_capture.py": (
        _D, "real spend", "twelve real end-to-end runs; its own docstring says COSTS MONEY"),
    "fixture_replay.py": (
        _C, "a live Qdrant whose collection digest matches the fixtures",
        "refuses at the pinned-collection gate; free, but not a CI gate"),
    "13- LangGraph Agent.py": (
        _D, "real spend when RUN_TEST_ON_EXECUTE = True",
        "one billed Stage 5 call; edit-to-arm, so a bare run is a no-op"),
    "measure_medcpt_scores.py": (
        _C, "a live Qdrant index and the corpus",
        "runs Stages 1-3 over a seeded 30-patient sample"),
}


def in_bucket(letter):
    """Test basenames in `letter`, sorted."""
    return sorted(n for n, (b, _, _) in BUCKETS.items() if b == letter)


def runnable_in_ci(letter):
    """Test basenames in `letter` whose `needs` is None."""
    return sorted(n for n, (b, needs, _) in BUCKETS.items()
                  if b == letter and needs is None)


def check_complete():
    """Every tests/test_*.py is classified, and B agrees with the serial runner.

    Returns a list of problem strings; empty means consistent.
    """
    problems = []

    on_disk = {f for f in os.listdir(_TESTS_DIR)
               if f.startswith("test_") and f.endswith(".py")}
    declared = set(BUCKETS)

    for name in sorted(on_disk - declared):
        problems.append(
            f"UNCLASSIFIED: tests/{name} is not in BUCKETS. Add it with the "
            f"outcome of a real run against the CI skeleton -- do not guess.")
    for name in sorted(declared - on_disk):
        problems.append(
            f"STALE: BUCKETS names tests/{name}, which does not exist.")

    # The B column must be exactly the serial runner's list, in both
    # directions. A member added to run_serial_tests.py and not here would be
    # run by `make serial-tests` while this table called it parallel-safe.
    try:
        serial = set(_serial_members())
    except RuntimeError as exc:
        problems.append(str(exc))
        return problems

    declared_b = set(in_bucket(_B))
    for name in sorted(serial - declared_b):
        problems.append(
            f"MATRIX DRIFT: tests/{name} is in run_serial_tests.py's "
            f"SERIAL_TESTS but is not bucket B here.")
    for name in sorted(declared_b - serial):
        problems.append(
            f"MATRIX DRIFT: tests/{name} is bucket B here but is not in "
            f"run_serial_tests.py's SERIAL_TESTS.")

    return problems


def serial_preconditions():
    """Return (ready, rows). `ready` is True when every B member can run.

    Rows are (name, needs, present) so the caller can print what is missing
    rather than just refusing. Presence is decided by asking the filesystem for
    the specific input each member named when it refused -- not by a flag
    somebody sets, which would be a second declaration.
    """
    sys.path.insert(0, _CODE_DIR)
    from oncotriage import paths

    def _has_umls():
        import glob
        return bool(glob.glob(os.path.join(paths.data_MeSH_path, "MRCONSO*.RRF")))

    def _has_corpus():
        d = paths.data_fhir_path
        return os.path.isdir(d) and any(f.endswith(".json") for f in os.listdir(d))

    def _has_scratch_ecog():
        return os.path.isdir(os.path.join(paths.data_patient_path,
                                          "scratch_ecog", "fhir"))

    probes = {
        "test_registries_cancer_code_claims_audit.py": _has_umls,
        "test_registries_cancer_code_claims_audit_control.py": _has_umls,
        "test_degraded_dependencies.py": _has_corpus,
        "test_config_snapshot_date_rot.py": _has_scratch_ecog,
        "test_package_invariants.py": lambda: True,
    }

    rows = []
    for name in in_bucket(_B):
        needs = BUCKETS[name][1]
        try:
            present = probes[name]()
        except Exception as exc:                     # noqa: BLE001 - reported
            present = False
            needs = f"{needs} (probe raised {type(exc).__name__}: {exc})"
        rows.append((name, needs, present))

    return all(present for _, _, present in rows), rows


def _run_one(name, root):
    path = os.path.join("tests", name)
    env = dict(os.environ)
    if root:
        env["ONCOTRIAGE_MAIN_PATH"] = root
    start = time.time()
    completed = subprocess.run([sys.executable, path], cwd=_CODE_DIR, env=env,
                               capture_output=True, text=True)
    return name, completed.returncode, time.time() - start, completed.stdout, completed.stderr


def run_bucket(letter, root=None, workers=None):
    """Run every CI-runnable member of `letter`. Returns an exit code.

    Bucket A members write nothing in the repository -- that is the derivation
    in tests/run_serial_tests.py, and it is what makes concurrency safe here.
    Bucket B is NEVER run through this function: it goes through
    `make serial-tests`, which holds the lock and fixes the order.
    """
    if letter == _B:
        raise RuntimeError(
            "bucket B must not be run from here. `make serial-tests` is the "
            "only entry point: two of its members rewrite source in place and "
            "restore it, the order is load-bearing, and the runner holds an "
            "flock that stops two runs from interleaving their restores.")

    # A BUCKET THAT RUNS NOTHING MUST NOT EXIT 0. `--run Z` on a letter that is
    # not a bucket, or a bucket whose every member became unrunnable, would
    # otherwise print "ran 0, failed 0" and return success -- a CI step that
    # tested nothing and looked exactly like one that passed. That is the defect
    # pass 20g fixed in Files 18 and 19, and it is easier to reintroduce in a
    # runner than anywhere else.
    members = in_bucket(letter)
    if not members:
        raise RuntimeError(
            f"{letter!r} is not a bucket with any members. Known buckets: "
            f"{sorted({b for b, _, _ in BUCKETS.values()})}.")

    names = runnable_in_ci(letter)
    if not names:
        raise RuntimeError(
            f"bucket {letter} has {len(members)} member(s) and NONE of them is "
            f"runnable in CI, so this step would have tested nothing and "
            f"exited 0. Members and what each needs:\n" +
            "\n".join(f"    {n}: {BUCKETS[n][1]}" for n in members))

    skipped = [n for n in members if n not in names]

    if workers is None:
        workers = min(4, (os.cpu_count() or 2))

    print("=" * 74)
    print(f"BUCKET {letter} — {len(names)} test files, {workers} at a time")
    print("=" * 74)
    for name in skipped:
        print(f"  NOT RUN  {name}\n           needs: {BUCKETS[name][1]}")
    if skipped:
        print()

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run_one, n, root) for n in names]
        for future in concurrent.futures.as_completed(futures):
            name, rc, elapsed, out, err = future.result()
            results.append((name, rc, elapsed, out, err))
            print(f"  {'PASS' if rc == 0 else 'FAIL':<5} exit={rc:<3} "
                  f"{elapsed:6.1f}s  {name}")

    failed = [r for r in results if r[1] != 0]

    if failed:
        print()
        print("=" * 74)
        print(f"FAILURE OUTPUT — {len(failed)} of {len(results)}")
        print("=" * 74)
        for name, rc, _, out, err in sorted(failed):
            print()
            print("-" * 74)
            print(f"{name}  (exit {rc})")
            print("-" * 74)
            sys.stdout.write(out)
            if err:
                sys.stdout.write("--- stderr ---\n")
                sys.stdout.write(err)

    print()
    print("=" * 74)
    print(f"BUCKET {letter} SUMMARY")
    print("=" * 74)
    for name, rc, elapsed, _, _ in sorted(results):
        print(f"  {'PASS' if rc == 0 else 'FAIL':<5} exit={rc:<3} {elapsed:6.1f}s  {name}")
    print()
    print(f"  ran {len(results)}, failed {len(failed)}, not run {len(skipped)}")
    return 1 if failed else 0


def _print_table():
    letters = [(_A, "parallel-safe in CI"),
               (_B, "serial (collision matrix)"),
               (_C, "needs a live Qdrant endpoint"),
               (_D, "costs money or needs a live HTTP server"),
               (_E, "needs uncommitted data files")]
    for letter, label in letters:
        names = in_bucket(letter)
        if not names:
            continue
        print()
        print(f"BUCKET {letter} — {label}  ({len(names)})")
        print("-" * 74)
        for name in names:
            _, needs, evidence = BUCKETS[name]
            mark = "   " if needs is None else "!! "
            print(f"  {mark}{name}")
            print(f"       evidence: {evidence}")
            if needs is not None:
                print(f"       NOT RUNNABLE IN CI, needs: {needs}")
    print()
    print("Entry points outside tests/ (never wired into CI)")
    print("-" * 74)
    for name, (letter, needs, evidence) in sorted(NON_TEST_ENTRY_POINTS.items()):
        print(f"  [{letter}] {name}")
        print(f"       {evidence}")
        print(f"       needs: {needs}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true",
                       help="print the whole classification and exit")
    group.add_argument("--check", action="store_true",
                       help="fail if any tests/test_*.py is unclassified")
    group.add_argument("--run", metavar="LETTER",
                       help="run every CI-runnable member of a bucket")
    group.add_argument("--serial-preconditions", action="store_true",
                       help="report whether `make serial-tests` can run here")
    parser.add_argument("--root", default=os.environ.get("ONCOTRIAGE_MAIN_PATH"),
                        help="project root passed to each test as "
                             "ONCOTRIAGE_MAIN_PATH")
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args(argv)

    if args.list:
        _print_table()
        return 0

    if args.check:
        problems = check_complete()
        if problems:
            print("CLASSIFICATION IS INCONSISTENT:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print(f"Classification consistent: {len(BUCKETS)} test files, "
              f"{len(in_bucket(_A))} in bucket A "
              f"({len(runnable_in_ci(_A))} runnable in CI), "
              f"{len(in_bucket(_B))} in bucket B "
              f"({len(runnable_in_ci(_B))} runnable in CI).")
        return 0

    if args.serial_preconditions:
        ready, rows = serial_preconditions()
        print("SERIAL SUITE PRECONDITIONS")
        print("-" * 74)
        for name, needs, present in rows:
            print(f"  {'PRESENT' if present else 'ABSENT ':<8} {name}")
            if not present:
                print(f"           needs: {needs}")
        print()
        print(f"serial_ready={'true' if ready else 'false'}")
        return 0

    missing = [n for n in in_bucket(args.run)
               if not os.path.isfile(os.path.join(_TESTS_DIR, n))]
    if missing:
        print("MISSING test file(s):")
        for name in missing:
            print(f"  - tests/{name}")
        return 2

    return run_bucket(args.run, root=args.root, workers=args.workers)


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Aug  8 2026

@author: ramyalsaffar
"""
