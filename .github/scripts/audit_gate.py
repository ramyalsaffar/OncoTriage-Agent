# Dependency Audit Gate
######################

"""Run pip-audit, print EVERY finding, and fail only on the actionable ones.

WHAT "ACTIONABLE" MEANS HERE, AND WHY IT IS NOT "CRITICAL AND HIGH"
-------------------------------------------------------------------
The brief for this gate asked for "critical and high findings that have a fix
available". Half of that is not available from this data source:

    pip-audit reports NO SEVERITY AT ALL.

Its JSON carries `id`, `description` and `fix_versions` per finding and nothing
else -- measured, not assumed: the full output was dumped and inspected. PyPI's
advisory feed (PYSEC/OSV) does not carry a CVSS vector for most of these
entries, so pip-audit has nothing to report even in principle. Severity for the
same packages IS available, from Trivy, which scans the built image and prints
CRITICAL/HIGH per package -- that is the severity-aware gate, and it is a
separate job.

So this gate uses the axis that IS present and IS actionable:

    FAIL when a finding has a fix version and is not explicitly accepted.
    PRINT everything either way, including the unfixable ones.

A finding with no fix version cannot be acted on by upgrading, so gating on it
would be a permanently-red check that no commit can turn green -- the shape that
teaches people to ignore a pipeline.

THE ACCEPTED TABLE IS PER-ID AND ARGUED, NOT A BLANKET ALLOWLIST
-----------------------------------------------------------------
Every entry names the finding, the package, the version the fix landed in, and
the SPECIFIC constraint that blocks taking it. There is no rule-level, no
package-level wildcard and no "ignore everything in this file" switch -- a new
advisory against an already-accepted package still fails this gate, which is the
property a package-level ignore would destroy.

Nothing in here is a fix. An accepted finding is present, unpatched and shipped.

Run from terminal:
    python .github/scripts/audit_gate.py

Exit codes:
    0 -- no unaccepted fixable findings
    1 -- at least one, or pip-audit itself failed to run
    2 -- the accepted table has gone stale
"""

import json
import subprocess
import sys


# ===========================================================================
# ACCEPTED FINDINGS
# ===========================================================================
# id -> (package, fixed_in, why it cannot be taken here)
#
# Four blocking classes, and each entry says which one it is:
#
#   PIN-CAP   another pinned dependency's metadata forbids the fixed version.
#             pip refuses the combination outright; this is not a preference.
#   RENDER    the fix requires a streamlit whose rendering differs, which the
#             committed dashboard snapshot would have to absorb. Measured: 1.51.0
#             changes 96 snapshot values including 43 True->False and four figure
#             heights collapsing to 0. Regenerating a golden file to accommodate
#             that makes whatever the code does correct by definition.
#   MODEL     the fix changes a model or numerical stack that this project's
#             twelve characterization fixtures exist to pin. They cannot
#             currently be replayed to prove otherwise -- the Qdrant alias moved
#             to trial_criteria_20260807_111807 while the fixtures are pinned to
#             ...20260803_104642 -- so a bump here would be unverifiable by the
#             one mechanism built to verify it.
#   (THE `SCOPE` CLASS IS GONE.) It described apache-airflow, moved to the
#             `orchestration` extra and therefore invisible to this audit, with
#             two CRITICALs left unfixed. The upgrade to 3.3.0 is done, so there
#             is nothing to scope away. Note what that class always was: a
#             statement about what this gate can SEE, not about what ships. If
#             a package is ever moved out of the default install again, the
#             right home for its findings is `.trivyignore`, which scans the
#             image and therefore sees the extra.
_ACCEPTED = {
    # ---- pillow: PIN-CAP -------------------------------------------------
    # Every pillow fix is a 12.x release. streamlit 1.46.0 declares
    # `pillow<12` and fastembed 0.7.4 declares `pillow<12.0`, so pip cannot
    # resolve any of them. The earliest streamlit that lifts the cap is 1.51.0,
    # which is RENDER above. fastembed 0.8.0 lifts its own cap and was measured
    # to produce BYTE-IDENTICAL BM25 vectors, so fastembed is not the blocker --
    # streamlit is.
    "PYSEC-2026-165":  ("pillow", "12.2.0", "PIN-CAP streamlit<12 / fastembed<12"),
    "PYSEC-2026-2249": ("pillow", "12.1.1", "PIN-CAP streamlit<12 / fastembed<12"),
    "PYSEC-2026-2250": ("pillow", "12.2.0", "PIN-CAP streamlit<12 / fastembed<12"),
    "PYSEC-2026-2251": ("pillow", "12.2.0", "PIN-CAP streamlit<12 / fastembed<12"),
    "PYSEC-2026-2252": ("pillow", "12.2.0", "PIN-CAP streamlit<12 / fastembed<12"),
    "PYSEC-2026-2253": ("pillow", "12.3.0", "PIN-CAP streamlit<12 / fastembed<12"),
    "PYSEC-2026-2254": ("pillow", "12.3.0", "PIN-CAP streamlit<12 / fastembed<12"),
    "PYSEC-2026-2255": ("pillow", "12.3.0", "PIN-CAP streamlit<12 / fastembed<12"),
    "PYSEC-2026-2256": ("pillow", "12.3.0", "PIN-CAP streamlit<12 / fastembed<12"),
    "PYSEC-2026-2257": ("pillow", "12.3.0", "PIN-CAP streamlit<12 / fastembed<12"),
    "PYSEC-2026-2874": ("pillow", "12.2.0", "PIN-CAP streamlit<12 / fastembed<12"),
    "PYSEC-2026-3451": ("pillow", "12.3.0", "PIN-CAP streamlit<12 / fastembed<12"),
    "PYSEC-2026-3453": ("pillow", "12.3.0", "PIN-CAP streamlit<12 / fastembed<12"),
    "PYSEC-2026-3454": ("pillow", "12.3.0", "PIN-CAP streamlit<12 / fastembed<12"),
    "PYSEC-2026-3493": ("pillow", "12.3.0", "PIN-CAP streamlit<12 / fastembed<12"),
    "PYSEC-2026-3494": ("pillow", "12.3.0", "PIN-CAP streamlit<12 / fastembed<12"),
    "PYSEC-2026-3495": ("pillow", "12.3.0", "PIN-CAP streamlit<12 / fastembed<12"),
    "PYSEC-2026-3496": ("pillow", "12.3.0", "PIN-CAP streamlit<12 / fastembed<12"),

    # ---- starlette: SIX ENTRIES DELETED, AND THEY WERE FIXED, NOT DROPPED --
    # PYSEC-2026-1942 (CVE-2025-62727), -161, -2280, -2281, -248 and -249 were
    # accepted here on the argument that `fastapi==0.117.1` declares
    # `starlette<0.49.0` and every fix is >= 0.49.1. That cap is gone: the
    # Airflow upgrade forced fastapi to 0.136.3, because apache-airflow-core
    # 3.3.0 requires `fastapi>=0.129.0,<0.137.0` and no Airflow release clears
    # its two CRITICALs while coexisting with the old pin. starlette resolves to
    # 1.6.0, past all six fix versions.
    #
    # THE STALENESS CHECK BELOW IS WHAT MAKES THIS SAFE TO DO IN ONE COMMIT: an
    # accepted id that no longer appears in the audit exits 2. Leaving these six
    # behind after the pin moved would have turned the gate red rather than
    # letting a dead exemption sit here being re-read as a live constraint.

    # ---- streamlit: RENDER -----------------------------------------------
    "PYSEC-2026-212":  ("streamlit", "1.53.1", "RENDER dashboard snapshot"),
    "PYSEC-2026-2285": ("streamlit", "1.54.0", "RENDER dashboard snapshot"),

    # ---- torch / transformers: MODEL -------------------------------------
    # transformers is the MedCPT cross-encoder's loader and torch is what runs
    # it. Stage 3's ranking is the thing the fixtures pin.
    "CVE-2025-2999":   ("torch", "2.9.1",  "MODEL cross-encoder numerics"),
    "CVE-2025-3001":   ("torch", "2.10.0", "MODEL cross-encoder numerics"),
    "PYSEC-2025-194":  ("torch", "2.13.0", "MODEL cross-encoder numerics"),
    "PYSEC-2026-2286": ("torch", "2.10.0", "MODEL cross-encoder numerics"),
    "PYSEC-2026-2288": ("transformers", "5.0.0", "MODEL MedCPT cross-encoder major"),
    "PYSEC-2026-2289": ("transformers", "5.3.0", "MODEL MedCPT cross-encoder major"),
    #
    # CVE-2026-9856 (GHSA-xrqw-3rrv-vx5w) IS THE ENTRY THAT TURNED THIS GATE RED
    # WITH NO COMMIT, and the timing is the record: GitHub reviewed the advisory
    # at 2026-09-01T18:58:08Z, OSV modified it at 19:02:53Z, run 20 was green at
    # 16:45Z and run 21 red at 19:35Z. Nothing in run 21's commit touches a
    # dependency. That is this gate's advertised property working -- a NEW
    # advisory against an ALREADY-ACCEPTED package still fails -- and the cost of
    # that property is that it gates commits that did not cause it.
    #
    # IT IS THE SAME BLOCKER AS THE TWO ABOVE, ONE MAJOR FURTHER OUT: the fix
    # landed in 5.10.0 and this project pins transformers==4.57.1 as the MedCPT
    # cross-encoder's loader. Taking it is a 4.x -> 5.x bump of the library that
    # instantiates the Stage 3 tokenizer and weights, which is exactly what the
    # twelve characterization fixtures exist to pin and exactly what cannot be
    # replayed to prove today (they are stale: the de-identification and
    # pre-diagnosis-ECOG passes invalidated them and the recapture is the
    # standing item). Bumping it to clear a gate would be changing ranking
    # behaviour to make a check pass, unverified by the one mechanism built to
    # verify it.
    #
    # AND THE SINK IS NOT REACHABLE FROM THIS PROJECT -- MEASURED ON BOTH
    # PRECONDITIONS, rather than argued from the advisory summary. The advisory
    # is a path traversal in `save_pretrained()` on `PreTrainedTokenizerBase`
    # and `ProcessorMixin`: attacker-controlled keys of a `chat_template` dict,
    # arriving in a crafted `tokenizer_config.json` from a Hub repository, are
    # used as filenames when the victim SAVES the tokenizer or processor. Both
    # preconditions fail here, independently:
    #
    #   1. THE SINK IS NEVER CALLED. `git grep` for that method name over every
    #      tracked file in this repository returns hits in THIS COMMENT AND
    #      NOWHERE ELSE -- no code, no test, no entry point. Stated that way on
    #      purpose: before this note existed the count was zero, and a claim
    #      phrased as "appears nowhere" would have been falsified by the
    #      sentence making it. That is the trap this project has now met five
    #      times: a file that argues about a string cannot be grepped for it.
    #   2. The load is not attacker-directed. The only two `from_pretrained`
    #      calls in the package are AutoTokenizer and
    #      AutoModelForSequenceClassification in oncotriage/agent/deps.py, both
    #      handed `config.CROSS_ENCODER_MODEL` == "ncbi/MedCPT-Cross-Encoder" --
    #      and tests/test_package_invariants.py section 2f(ii) asserts BY AST
    #      that the checkpoint literal appears exactly once in the package, that
    #      both calls are handed that constant, that there are exactly two of
    #      them, and that no other package module calls `from_pretrained` at
    #      all. So pointing this project at an arbitrary Hub repository is not a
    #      configuration change; it is a source edit that fails a standing check.
    #      AutoModelForSequenceClassification is also not a ProcessorMixin, and
    #      none of the processors the advisory names (Idefics, Florence, Gemma,
    #      Phi, Qwen-VL) is loaded anywhere here.
    #
    # THAT IS AN ARGUMENT FOR WHY ACCEPTING IS SAFE, NOT A FOURTH BLOCKING
    # CLASS. The class field answers "what stops the fix being taken", and what
    # stops it is MODEL, the same as its two siblings. Unreachability is why
    # leaving it unpatched is defensible; it does not make the bump takeable.
    "CVE-2026-9856":   ("transformers", "5.10.0", "MODEL MedCPT cross-encoder major"),
    #
    # PYSEC-2026-2290 (CVE-2026-5241, GHSA-fgcw-684q-jj6r) WAS NOT RED. IT WAS
    # INVISIBLE, and it is here because the de-duplication fix in main() made it
    # visible. Before that fix it was filed UNFIXABLE -- printed on every green
    # run under a heading reading "no released fix", which is false of it: the
    # fix is transformers 5.5.0. See the merge comment in main() for how one
    # finding arrived twice with two different answers about its own fixability
    # and why first-wins was the wrong tie-break.
    #
    # THE BLOCKER IS THE SAME MODEL CONSTRAINT AS THE FOUR ENTRIES ABOVE, and
    # 5.5.0 is still a 4.x -> 5.x major on the MedCPT cross-encoder's loader.
    #
    # THE SINK IS NOT REACHABLE FROM THIS PROJECT, and this one is narrower than
    # CVE-2026-9856's. The advisory is arbitrary code execution in the LIGHTGLUE
    # model loading path: `LightGlueConfig` reads `trust_remote_code` out of an
    # untrusted `config.json` and propagates it into nested
    # `AutoConfig.from_pretrained()` calls, so an attacker-controlled repository
    # executes its own Python during `AutoModel.from_pretrained()` even when the
    # caller passed `trust_remote_code=False`. Three independent measurements,
    # not three readings of the summary:
    #
    #   1. `git grep trust_remote_code` over every tracked file returns ZERO
    #      hits. This project never passes the parameter in either direction.
    #   2. No LightGlue model is loaded, and none can be reached by
    #      configuration: the only two loads in the package are AutoTokenizer
    #      and AutoModelForSequenceClassification, both handed
    #      `config.CROSS_ENCODER_MODEL` == "ncbi/MedCPT-Cross-Encoder", and
    #      tests/test_package_invariants.py section 2f(ii) pins that by AST.
    #   3. The repository is not attacker-controlled. It is a first-party NCBI
    #      checkpoint named by a module constant, which is the same premise
    #      pass 20f-2 already made load-bearing when it gave that checkpoint one
    #      owner and one construction site.
    "PYSEC-2026-2290": ("transformers", "5.5.0", "MODEL MedCPT cross-encoder major"),
}

# Packages whose findings are about the BUILD TOOLING rather than this project's
# declared dependencies. pip and setuptools arrive with the base image and are
# upgraded by the workflow before the audit runs; if one still shows up here it
# means that upgrade did not happen, which is worth failing on rather than
# accepting. Listed so nobody "fixes" a pip advisory by pinning pip.
_BUILD_TOOLING = {"pip", "setuptools", "wheel"}


def run_pip_audit():
    """Return pip-audit's parsed JSON, or exit non-zero explaining why not."""
    proc = subprocess.run(
        [sys.executable, "-m", "pip_audit", "--progress-spinner", "off",
         "--format", "json"],
        capture_output=True, text=True,
    )
    # pip-audit exits 1 when it FINDS something, which is not an error here.
    if not proc.stdout.strip():
        print("FATAL: pip-audit produced no output.")
        print(proc.stderr[-4000:])
        sys.exit(1)
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        print(f"FATAL: pip-audit output was not JSON: {exc}")
        print(proc.stdout[:2000])
        sys.exit(1)


def main():
    print("=" * 78)
    print("DEPENDENCY AUDIT GATE (pip-audit)")
    print("=" * 78)

    data = run_pip_audit()
    deps = data.get("dependencies", data)

    findings = []
    for pkg in deps:
        for vuln in pkg.get("vulns", []):
            findings.append({
                "package": pkg["name"],
                "version": pkg.get("version"),
                "id": vuln["id"],
                "fix": tuple(vuln.get("fix_versions") or ()),
            })

    # De-duplicate, AND UNION THE FIX VERSIONS RATHER THAN KEEPING THE FIRST.
    # Counting one problem twice would make the printed totals disagree with the
    # number of distinct problems, which is what the de-duplication is for.
    #
    # THE UNION IS A CORRECTNESS FIX AND NOT TIDINESS -- MEASURED 2026-09-01,
    # against the resolved linux/py3.11 set. `setdefault` alone kept whichever
    # record came FIRST, and pip-audit emits ONE (package, id) here with two
    # records that DISAGREE about fixability:
    #
    #     transformers PYSEC-2026-2290   fix_versions []        <- kept
    #     transformers PYSEC-2026-2290   fix_versions ['5.5.0']  <- discarded
    #
    # Both describe CVE-2026-5241. The cause is upstream and is not a pip-audit
    # bug: OSV carries two records for that CVE and they model the range
    # differently -- GHSA-fgcw-684q-jj6r has `{"fixed": "5.5.0"}` while
    # PYSEC-2026-2290 has `{"last_affected": "5.2.0"}` and no `fixed` event at
    # all, so one yields a fix version and the other yields none. pip-audit
    # reports both under the PYSEC id, because that is the alias it prefers.
    #
    # WHAT FIRST-WINS COST: the finding was filed UNFIXABLE and therefore never
    # gated, while a fix exists and is not accepted anywhere. It was reported on
    # every green run, in the section headed "no released fix", which is false
    # of it. And which way it fell was decided by pip-audit's output ORDER,
    # which is not a contract -- so the gate would have turned red on a day when
    # nothing about this project or the advisory had changed. This project does
    # not leave a classification resting on an ordering nobody guarantees.
    #
    # THE UNION IS THE CONSERVATIVE DIRECTION, which is why it is the right
    # merge rather than a coin toss between two records: a finding that ANY
    # record says is fixable must be GATED, not silently filed as unfixable.
    # The reverse (intersection, or first-wins) can only ever hide work.
    unique = {}
    for f in findings:
        key = (f["package"], f["id"])
        if key in unique:
            merged = set(unique[key]["fix"]) | set(f["fix"])
            unique[key]["fix"] = tuple(sorted(merged))
        else:
            unique[key] = dict(f)
    findings = sorted(unique.values(), key=lambda f: (f["package"], f["id"]))

    fixable    = [f for f in findings if f["fix"]]
    unfixable  = [f for f in findings if not f["fix"]]
    accepted   = [f for f in fixable if f["id"] in _ACCEPTED]
    tooling    = [f for f in fixable
                  if f["id"] not in _ACCEPTED and f["package"] in _BUILD_TOOLING]
    blocking   = [f for f in fixable
                  if f["id"] not in _ACCEPTED and f["package"] not in _BUILD_TOOLING]

    # ---- EVERYTHING IS PRINTED, which is the point of the "regardless" -----
    print(f"\n{len(findings)} distinct finding(s) "
          f"({len(fixable)} with a fix, {len(unfixable)} without)\n")

    def dump(title, rows, extra=None):
        print("-" * 78)
        print(f"{title}: {len(rows)}")
        print("-" * 78)
        if not rows:
            print("  (none)")
        for f in rows:
            fix = ", ".join(f["fix"]) or "no fix available"
            print(f"  {f['package']:18s} {str(f['version']):10s} {f['id']:20s} -> {fix}")
            if extra:
                print(f"      {extra(f)}")
        print()

    dump("UNFIXABLE — no released fix, reported and not gated on", unfixable)
    dump("ACCEPTED — fix exists but is blocked; each argued in _ACCEPTED",
         accepted, lambda f: f"{_ACCEPTED[f['id']][2]}  (fixed in {_ACCEPTED[f['id']][1]})")
    dump("BUILD TOOLING — upgrade pip/setuptools in the job, do not pin", tooling)
    dump("BLOCKING — fixable, not accepted, and therefore actionable", blocking)

    # ---- staleness: an accepted id that no longer appears -----------------
    # An entry that has stopped matching means the dependency moved and the
    # justification is now describing nothing. Left alone it becomes a
    # permanent, unexamined exemption -- the shape this project removes
    # elsewhere by failing on stale exemption tables.
    # AND AN ACCEPTED ID THAT IS PRESENT BUT NOT FIXABLE IS THE SAME DEFECT ONE
    # STEP IN. "Accepted" means, in this table's own words, that a fix exists
    # and something specific blocks taking it -- every entry carries the version
    # the fix landed in. An entry whose finding reports NO fix version is
    # therefore describing something other than what it claims, and it is INERT:
    # `accepted` is computed from `fixable`, so such an id falls into UNFIXABLE
    # instead, is never gated, and the table goes on naming a blocked fix that
    # the data says does not exist.
    #
    # THIS CHECK IS HERE BECAUSE ITS ABSENCE IS WHY PYSEC-2026-2290 SURVIVED.
    # That finding was fixable (5.5.0), was filed unfixable by the first-wins
    # de-duplication above, and produced no complaint from anything -- the
    # staleness check below could not see it, because the id was still PRESENT.
    # The union fixes the classification; this fixes the blind spot that let a
    # wrong classification pass silently, which is the more durable half.
    #
    # It is exit 2 rather than exit 1 for the reason stale entries are: nothing
    # a commit can do turns it green, and the fix is a human reading the table.
    fix_by_id = {f["id"]: f["fix"] for f in findings}
    inert = sorted(vid for vid in _ACCEPTED
                   if vid in fix_by_id and not fix_by_id[vid])
    if inert:
        print("-" * 78)
        print(f"ACCEPTED ENTRIES THAT ARE NOT FIXABLE: {len(inert)}")
        print("-" * 78)
        for vid in inert:
            pkg, fixed, why = _ACCEPTED[vid]
            print(f"  {vid} ({pkg}) — this table says the fix is {fixed} and is")
            print(f"      blocked by: {why}. The audit reports NO fix version for")
            print("      it, so the entry gates nothing and one of the two is wrong.")
        print("\n  Either the advisory data lost its fix version, in which case")
        print("  the entry belongs in the unfixable column and should go, or the")
        print("  de-duplication above is discarding the record that carries it.")
        print()
        return 2

    seen_ids = {f["id"] for f in findings}
    stale = sorted(set(_ACCEPTED) - seen_ids)
    if stale:
        print("-" * 78)
        print(f"STALE ACCEPTED ENTRIES: {len(stale)}")
        print("-" * 78)
        for vid in stale:
            pkg, fixed, why = _ACCEPTED[vid]
            print(f"  {vid} ({pkg}, was blocked by: {why})")
        print("\n  These no longer appear in the audit. Either the dependency")
        print("  moved and the entry should go, or the audit stopped seeing the")
        print("  package. Both need a human; neither should sit here silently.")
        print()
        return 2

    if blocking or tooling:
        print("=" * 78)
        print(f"FAIL: {len(blocking) + len(tooling)} actionable finding(s).")
        print("=" * 78)
        return 1

    print("=" * 78)
    print(f"PASS: no actionable findings. "
          f"{len(accepted)} accepted, {len(unfixable)} unfixable, both printed above.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Aug  8 2026

@author: ramyalsaffar
"""
