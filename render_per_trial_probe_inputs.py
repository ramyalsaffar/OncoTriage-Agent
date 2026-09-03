# Per-trial probe input renderer
#######################################

"""Render ONE real patient's Stage 5 system prefix and per-trial user messages.

    python render_per_trial_probe_inputs.py                    # default out-dir
    python render_per_trial_probe_inputs.py --out-dir <dir> --trials 5
    python render_per_trial_probe_inputs.py --stem <bundle stem>

WHAT IT IS FOR. `bedrock_probe.py --probe-per-trial --per-trial-prefix-file X`
needs a REAL Stage 5 system prefix, because the probe's built-in one is far
below Bedrock's 1,024-token cache minimum and a zero cache write against it is
the documented behaviour of a short prefix rather than a finding about this
pipeline. This writes that file, and writes the per-trial USER messages beside
it so a per-trial output-token measurement is taken on the bytes Stage 5 would
really send.

IT IS FREE AND IT CANNOT REACH STAGE 5. Stages 1-4 only: Stage 2's dense
channel makes ONE `text-embedding-3-small` call for the patient (~$0.000002 at
the configured rate) and Stages 1, 3 and 4 call no priced endpoint at all.
That Stage 5 is unreachable is STRUCTURAL rather than promised -- this file
imports no evaluation NODE and compiles no graph -- and `boto3.client` is
patched to record and REFUSE for the whole run, through
`oncotriage/evaluation/mmr_redundancy.py:arm_boto3_guard`, with the count
printed at the end.

NOTHING IS REIMPLEMENTED THAT CAN BE IMPORTED. The four nodes are the shipped
ones, called in order (`mmr_redundancy.run_stages_1_to_4`'s pattern and its
reason: the compiled graph's conditional edges route on to the billed call).
The record is `agent/patient.build_patient_record`, the de-identification scan
is `deid.assert_no_identifiers`, the fence neutralizer and the trial renderer
are `agent/evaluation`'s own, and the system prompt is
`agent/prompts.render_system_prompt`.

THE ONE THING THAT COULD NOT BE IMPORTED IS PINNED INSTEAD. Stage 5's user
message wrapper is a THREE-LINE f-string nested inside
`node_llm_classifier_evaluation` (`_wrap_trials`), so no importable name
reaches it. This file carries a copy and REFUSES TO RUN if the shipped
template's constant parts have moved -- see `_assert_wrapper_is_current`. A
silent copy would be exactly the two-owners drift this project keeps removing;
a copy that refuses when it goes stale is a pin.

WHY THIS FILE IS NOT NUMBERED AND IS NOT IN `oncotriage/`. `bedrock_probe.py`'s
reasons, exactly: the numbered sequence says what you can run in pipeline
order, this is a hand-run support command for a probe, and putting it in the
package would put a file used by one manual command into every package-wide
import sweep for nothing.

Exit codes:
    0 -- the artefacts were written
    1 -- nothing could be rendered (no kept trials, or a refusal)
    2 -- an argument was refused, or the wrapper pin is stale
"""


# Run needed file
#----------------
# The six-line package bootstrap. `pip install -e .` makes it a no-op.
import os
import sys

try:
    import oncotriage  # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals()
         else None, "__file__"),
        (os.getcwd(), "cwd"),
    ):
        if _candidate and os.path.isdir(os.path.join(_candidate, "oncotriage")):
            if _candidate not in sys.path:
                sys.path.insert(0, _candidate)
            print(f"[Bootstrap] oncotriage package found at {_candidate} "
                  f"(via {_how}); added to sys.path")
            break
    else:
        raise
    del _candidate, _how


#------------------------------------------------------------------------------


import argparse
import ast
import glob
import hashlib
import json


# ===========================================================================
# THE WRAPPER PIN
# ===========================================================================

# Stage 5's user message around one already-rendered trial block, byte for
# byte. Copied from `_wrap_trials` in oncotriage/agent/evaluation.py, which is
# nested inside the node and therefore unimportable.
_USER_MESSAGE_TEMPLATE = "\nCLINICAL TRIALS:\n{trials_text}\n"

# What that function's f-string is made of: the constant pieces around its ONE
# interpolation, in order. If either moves, the copy above is stale.
_WRAPPER_CONSTANTS = ("\nCLINICAL TRIALS:\n", "\n")
_WRAPPER_FUNCTION = "_wrap_trials"


def wrap_trials(trials_text: str) -> str:
    """The pinned copy of Stage 5's user-message wrapper."""
    return _USER_MESSAGE_TEMPLATE.format(trials_text=trials_text)


def shipped_wrapper_constants(source_path: str):
    """The constant parts of the shipped `_wrap_trials` f-string, by AST.

    Returns the tuple of string constants surrounding its interpolations, or
    None when the function or its `return <f-string>` cannot be found -- which
    is itself a staleness signal and is reported as one rather than silently
    treated as agreement.
    """
    with open(source_path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if (isinstance(node, ast.FunctionDef)
                and node.name == _WRAPPER_FUNCTION):
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Return)
                        and isinstance(inner.value, ast.JoinedStr)):
                    return tuple(
                        part.value for part in inner.value.values
                        if isinstance(part, ast.Constant)
                        and isinstance(part.value, str))
    return None


def _assert_wrapper_is_current(out=print) -> bool:
    """REFUSE rather than render a stale wrapper. Prints what it compared."""
    import oncotriage.agent.evaluation as _evaluation
    source = os.path.abspath(_evaluation.__file__)
    found = shipped_wrapper_constants(source)
    out(f"  wrapper pin : {_WRAPPER_FUNCTION} in {os.path.basename(source)}")
    if found is None:
        out(f"  REFUSED: {_WRAPPER_FUNCTION}'s `return <f-string>` was not "
            f"found. The copy in this file cannot be shown to be current.")
        return False
    if tuple(found) != _WRAPPER_CONSTANTS:
        out("  REFUSED: the shipped wrapper's constant parts have moved.")
        out(f"           shipped: {list(found)!r}")
        out(f"           pinned : {list(_WRAPPER_CONSTANTS)!r}")
        out("           Update _USER_MESSAGE_TEMPLATE and "
            "_WRAPPER_CONSTANTS together, then re-run.")
        return False
    out(f"  wrapper pin : CURRENT {list(found)!r}")
    return True


#------------------------------------------------------------------------------


# ===========================================================================
# THE PATIENT, AND STAGES 1-4
# ===========================================================================

def choose_stem(fhir_files, stem=None):
    """Which bundle to render. The campaign's own draw, or an explicit stem.

    THE DEFAULT IS REPRODUCIBLE AND IS THE CAMPAIGN'S. `cohort.draw` at the
    configured campaign seed, size 1 -- the same sha256 rank the campaign
    cohort is drawn by, so the patient this renders is one a campaign could
    run rather than "whatever sorted first".
    """
    from oncotriage import config
    from oncotriage.evaluation import cohort as campaign_cohort

    stems = [campaign_cohort.stem_of(p) for p in fhir_files]
    if stem is not None:
        if stem not in stems:
            return None, f"stem {stem!r} is not in the corpus"
        chosen, how = stem, "explicit --stem"
    else:
        drawn = campaign_cohort.draw(stems, 1, config.CAMPAIGN_COHORT_SEED)
        if not drawn:
            return None, "the draw returned nothing"
        chosen, how = drawn[0], (
            f"cohort.draw(size=1, seed={config.CAMPAIGN_COHORT_SEED}) -- "
            f"{campaign_cohort.DRAW_ALGORITHM}")
    for path in fhir_files:
        if campaign_cohort.stem_of(path) == chosen:
            return (path, chosen, how), None
    return None, f"no path for stem {chosen!r}"


def stages_1_to_4(patient_data):
    """The four shipped nodes, in order, keeping the FULL kept entries.

    `mmr_redundancy.run_stages_1_to_4` is the same four calls and is not reused
    HERE because it returns `_trial_record` projections that deliberately drop
    `full_trial_json` -- and the trial payload is exactly what has to be
    rendered. The node call sequence, and the reason for calling nodes rather
    than the compiled graph, are that function's.
    """
    from oncotriage.agent.graph import build_initial_state
    from oncotriage.agent.retrieval import (
        node_cross_encoder_rerank, node_hybrid_retrieval, node_query_expansion)
    from oncotriage.agent.filtering import node_rule_based_filter

    state = build_initial_state(patient_data)
    state.update(node_query_expansion(state))
    state.update(node_hybrid_retrieval(state))
    state.update(node_cross_encoder_rerank(state))
    state.update(node_rule_based_filter(state))
    return state


#------------------------------------------------------------------------------


# ===========================================================================
# THE RENDER
# ===========================================================================

def render_prefix(patient_data, state, out=print):
    """The Stage 5 system message, through the node's own three steps.

    Identical in order and in arguments to
    `node_llm_classifier_evaluation`: build the de-identified record, SCAN it,
    neutralize the fence markers, then interpolate. The scan is not optional
    here for the same reason it is not optional there -- a record carrying an
    identifier must not reach a prompt string, and this one is written to disk.
    """
    from oncotriage.agent.evaluation import _neutralize_fence_markers
    from oncotriage.agent.patient import build_patient_record
    from oncotriage.agent.prompts import prompt_sha256, render_system_prompt
    from oncotriage.deid import assert_no_identifiers

    deid_record, patient_summary = build_patient_record(patient_data)
    skipped = assert_no_identifiers(patient_summary, deid_record)
    out(f"  de-identification: scan PASSED "
        f"({skipped} harvested value(s) below the scannable floor)")

    patient_record, runs = _neutralize_fence_markers(patient_summary)
    if runs:
        out(f"  neutralized {runs} fence marker run(s) in the record")

    system_prompt = render_system_prompt(
        mesh_filter_applied=bool(state.get("mesh_filter_applied", False)),
        mesh_filter_skip_reason=(state.get("mesh_filter_skip_reason")
                                 or "unrecorded"),
        patient_record=patient_record,
    )
    return system_prompt, prompt_sha256(system_prompt)


def pick_spread(blocks, count):
    """`count` block indices spanning the length range, longest first.

    THE BRIEF ASKS FOR VISIBLY DIFFERENT CRITERIA LENGTHS, and the criteria are
    all that varies between blocks. Taking the extremes plus evenly spaced
    interior ranks samples the range rather than the mode, which is what makes
    a MAXIMUM over five calls worth anything as a guard input.
    """
    order = sorted(range(len(blocks)), key=lambda i: (-len(blocks[i]), i))
    if count >= len(order):
        return order
    if count == 1:
        return order[:1]
    step = (len(order) - 1) / (count - 1)
    picked, seen = [], set()
    for k in range(count):
        idx = order[int(round(k * step))]
        if idx not in seen:
            seen.add(idx)
            picked.append(idx)
    for idx in order:                       # top up if rounding collided
        if len(picked) == count:
            break
        if idx not in seen:
            seen.add(idx)
            picked.append(idx)
    return picked


#------------------------------------------------------------------------------


# ===========================================================================
# COST PROJECTION
# ===========================================================================

def project_costs(prefix_tokens, user_tokens, out=print):
    """The full projected cost table for the probe's paid steps.

    PRICED FROM PRICING_CONFIG, which carries no cached term -- so every input
    figure here is the UNCACHED price and is therefore an OVER-estimate the
    moment the cache works. That is the safe direction for a budget and it is
    stated rather than corrected, because correcting it would mean pricing a
    discount this project does not model.
    """
    from oncotriage import config
    from oncotriage.utils import get_model_cost

    model = config.matching_wire_model()
    out(f"\n  priced against {model!r} from PRICING_CONFIG "
        f"(NO cached-input term: every input token below is charged at the "
        f"full rate, so these are OVER-estimates once the cache works)")

    # The probe's own two baseline calls carry PROBE_SYSTEM/PROBE_USER, which
    # are tiny; they are counted at a nominal 500 in / 800 out apiece so the
    # table is not silently short of them.
    # THE OUTPUT FIGURE IS READ OFF THE CONSTANT, never typed beside it. It is
    # MATCHING_OUTPUT_TOKENS_PER_TRIAL -- the number this whole measurement
    # exists to replace -- so a projection that hardcoded today's value would
    # go stale on exactly the commit that adopts a measured one, while still
    # printing a sentence naming the constant.
    per_trial_out = config.MATCHING_OUTPUT_TOKENS_PER_TRIAL
    rows = []
    rows.append(("step 2  baseline calls (--calls 2)", 2, 500, 800))
    rows.append(("step 2  truncation call (maxTokens=16)", 1, 500, 16))
    rows.append(("step 2  per-trial warmup (maxTokens=1)", 1, prefix_tokens, 1))
    rows.append(("step 2  per-trial trial 1 and 2",
                 2, prefix_tokens + max(user_tokens), per_trial_out))
    rows.append((f"step 3  {len(user_tokens)} real trial calls",
                 len(user_tokens),
                 prefix_tokens + int(sum(user_tokens) / len(user_tokens)),
                 per_trial_out))

    total = 0.0
    out(f"\n  {'step':<42} {'n':>2} {'in/call':>9} {'out/call':>9} {'USD':>10}")
    out(f"  {'-' * 42} {'-' * 2} {'-' * 9} {'-' * 9} {'-' * 10}")
    for label, n, tin, tout in rows:
        cost = get_model_cost(model, tin * n, tout * n)
        total += cost
        out(f"  {label:<42} {n:>2} {tin:>9,} {tout:>9,} {cost:>10.4f}")
    out(f"  {'-' * 42} {'-' * 2} {'-' * 9} {'-' * 9} {'-' * 10}")
    out(f"  {'PROJECTED TOTAL, steps 2 and 3':<42} {'':>2} {'':>9} {'':>9} "
        f"{total:>10.4f}")
    out(f"\n  The {per_trial_out:,} output figure is "
        f"MATCHING_OUTPUT_TOKENS_PER_TRIAL, the constant this measurement "
        f"exists to replace. It is the projection's INPUT, never its finding.")
    return total


#------------------------------------------------------------------------------


# ===========================================================================
# ENTRY POINT
# ===========================================================================

ARTEFACT_PREFIX = "system_prefix.txt"
ARTEFACT_INDEX = "index.json"
ARTEFACT_DIRNAME = "Per-Trial Probe Inputs"


def default_out_dir() -> str:
    """Where the artefacts land when no ``--out-dir`` is given: OUTSIDE THE REPO.

    A RENDERED SYSTEM PREFIX IS A PATIENT RECORD. It is de-identified and this
    corpus is Synthea's, so it carries no real person -- and the reason this
    project keeps `02- Data/` and `09- Testing/` as SIBLINGS of the repository
    rather than inside it does not depend on that. A default that wrote a
    clinical record into a git working tree would be one `git add .` away from
    committing one, and the first draft of this file did exactly that.

    `09- Testing/` is the tree the characterization fixtures and the evaluation
    runs already live in, reached through `paths.testing_path` -- an existing
    path variable, so this adds none and changes no path table.

    IT RESOLVES ONLY WHEN CALLED, never at import, which is what keeps
    `--help` free of a glob over the sibling tree.
    """
    from oncotriage import paths
    return os.path.join(paths.testing_path, ARTEFACT_DIRNAME)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Render one real patient's Stage 5 prefix and per-trial "
                    "user messages for bedrock_probe.py. FREE: stages 1-4 "
                    "only, and boto3.client is refused for the whole run.")
    parser.add_argument("--out-dir", default=None,
                        help="Where to write the artefacts. Default: "
                             f"{ARTEFACT_DIRNAME!r} under the sibling "
                             "09- Testing/ tree, which is OUTSIDE the "
                             "repository -- see default_out_dir().")
    parser.add_argument("--trials", type=int, default=5,
                        help="How many trial user messages to write "
                             "(default 5).")
    parser.add_argument("--stem", default=None,
                        help="Render this bundle stem instead of the "
                             "campaign draw's first patient.")
    args = parser.parse_args(argv)

    if args.trials < 1:
        print("REFUSED: --trials must be at least 1.")
        return 2

    from oncotriage import config, paths
    from oncotriage.evaluation.mmr_redundancy import arm_boto3_guard
    from oncotriage.fhir.parser import parse_fhir_bundle

    print("=" * 78)
    print("RENDER PER-TRIAL PROBE INPUTS -- stages 1-4 only, nothing billed "
          "by the judge")
    print("=" * 78)

    built, guard = arm_boto3_guard()
    print(f"  boto3 guard : {guard}")

    if not _assert_wrapper_is_current():
        return 2

    out_dir = args.out_dir or default_out_dir()
    os.makedirs(out_dir, exist_ok=True)
    print(f"  out dir     : {out_dir}")

    fhir_files = sorted(glob.glob(os.path.join(paths.data_fhir_path, "*.json")))
    print(f"  corpus      : {len(fhir_files)} bundles")
    chosen, why = choose_stem(fhir_files, args.stem)
    if chosen is None:
        print(f"  REFUSED: {why}")
        return 1
    path, stem, how = chosen
    print(f"  patient     : {stem}")
    print(f"                ({how})")

    patient_data = parse_fhir_bundle(path)

    print("\n--- STAGES 1-4 (live Qdrant, one embedding call) ---")
    state = stages_1_to_4(patient_data)
    kept = state.get("filtered_trials") or []
    print(f"  reranked {len(state.get('reranked_trials') or [])} -> "
          f"kept {len(kept)}  (cap {config.MAX_TRIALS_FOR_EVALUATION})")
    print(f"  mesh_filter_applied={state.get('mesh_filter_applied')!r} "
          f"skip_reason={state.get('mesh_filter_skip_reason')!r}")
    if not kept:
        print("  REFUSED: this patient kept no trials; nothing to render.")
        return 1

    print("\n--- THE SYSTEM PREFIX ---")
    system_prompt, prefix_sha = render_prefix(patient_data, state)
    prefix_chars = len(system_prompt)
    prefix_tokens = prefix_chars // config.CHARS_PER_TOKEN
    floor_chars = 1024 * config.CHARS_PER_TOKEN
    print(f"  chars {prefix_chars:,}  ~tokens {prefix_tokens:,} "
          f"(CHARS_PER_TOKEN={config.CHARS_PER_TOKEN})")
    print(f"  sha256[:16] {prefix_sha[:16]}")
    if prefix_chars < floor_chars:
        print(f"  REFUSED: the prefix is under Bedrock's 1,024-token cache "
              f"minimum (~{floor_chars:,} chars). A12 would be UNMEASURABLE "
              f"against it. Choose another patient with --stem.")
        return 1
    print(f"  ABOVE the 1,024-token cache floor "
          f"(~{floor_chars:,} chars) by ~{prefix_tokens - 1024:,} tokens. "
          f"A12 is measurable against this prefix.")

    prefix_path = os.path.join(out_dir, ARTEFACT_PREFIX)
    with open(prefix_path, "w", encoding="utf-8") as handle:
        handle.write(system_prompt)
    print(f"  wrote {prefix_path}")

    print("\n--- THE TRIAL USER MESSAGES ---")
    from oncotriage.agent.evaluation import _render_trial_blocks
    blocks = _render_trial_blocks(kept)
    picked = pick_spread(blocks, args.trials)
    print(f"  block chars across all {len(blocks)} kept trials: "
          f"min {min(len(b) for b in blocks):,}  "
          f"max {max(len(b) for b in blocks):,}")

    written, user_tokens = [], []
    for rank, idx in enumerate(picked, start=1):
        message = wrap_trials(blocks[idx])
        nct = kept[idx]["trial"].get("nct_id", f"index{idx}")
        name = f"trial_{rank}_{nct}.txt"
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as handle:
            handle.write(message)
        tokens = len(message) // config.CHARS_PER_TOKEN
        user_tokens.append(tokens)
        written.append({
            "rank": rank, "file": name, "nct_id": nct,
            "kept_index": idx, "block_chars": len(blocks[idx]),
            "message_chars": len(message), "approx_tokens": tokens,
            "sha256_16": hashlib.sha256(
                message.encode("utf-8")).hexdigest()[:16],
        })
        print(f"  {rank}. {name:<32} block {len(blocks[idx]):>7,} chars  "
              f"message {len(message):>7,} chars  ~{tokens:>5,} tokens")

    print("\n--- PROJECTED COST, STEPS 2 AND 3 ---")
    projected = project_costs(prefix_tokens, user_tokens)

    index = {
        "schema_version": 1,
        "patient_stem": stem,
        "patient_selection": how,
        "prefix_file": ARTEFACT_PREFIX,
        "prefix_chars": prefix_chars,
        "prefix_approx_tokens": prefix_tokens,
        "prefix_sha256": prefix_sha,
        "mesh_filter_applied": bool(state.get("mesh_filter_applied", False)),
        "kept_trials": len(kept),
        "prompt_version": __import__(
            "oncotriage.agent.prompts", fromlist=["PROMPT_VERSION"]
        ).PROMPT_VERSION,
        "wire_model": config.matching_wire_model(),
        "matching_output_tokens_per_trial_at_render":
            config.MATCHING_OUTPUT_TOKENS_PER_TRIAL,
        "matching_max_tokens_at_render": config.MATCHING_MAX_TOKENS,
        "projected_usd_steps_2_and_3": round(projected, 6),
        "trials": written,
        "boto3_clients_built": built,
    }
    index_path = os.path.join(out_dir, ARTEFACT_INDEX)
    with open(index_path, "w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"\n  wrote {index_path}")
    print(f"  boto3 clients built during this run: {built or 'none'}")
    print("\nNOTHING WAS SENT TO THE JUDGE. The next command is step 2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Sep  3 2026

@author: ramyalsaffar
"""
