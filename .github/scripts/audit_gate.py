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
#   (THE `MODEL` CLASS IS GONE, AND ITS SIX ENTRIES WITH IT.) It read: "the fix
#             changes a model or numerical stack that this project's twelve
#             characterization fixtures exist to pin. They cannot currently be
#             replayed to prove otherwise ... so a bump here would be
#             unverifiable by the one mechanism built to verify it." That was a
#             statement about a MISSING MEASUREMENT, not about a constraint --
#             and the transformers-5.x pass took the measurement rather than
#             waiting for the fixtures. It re-scored every fixture's real
#             recorded (query, trial_texts) pair through the shipped
#             `models.score_pairs` seam -- 4,300 pairs, 43 rerank passes, 11
#             patients, with Stage 3's own RRF fusion -- behind a
#             reproducibility control (two baseline runs bit-identical) and a
#             positive control (max_length forced to 256 moves 11/11 patients'
#             top-15). transformers 4.57.1 -> 5.10.4 came back BIT-IDENTICAL,
#             torch 2.9.0 -> 2.10.0 moves no rank at all, and both pins moved.
#             See pyproject.toml for the full record.
#
#             THE LESSON IS WORTH KEEPING WHERE THE CLASS WAS: a blocking class
#             whose reason is "we cannot check this" expires the moment somebody
#             builds a check, and it will not expire on its own -- nothing in
#             this file goes stale when a measurement becomes possible. Anything
#             filed here for that reason is a task, not a constraint.
#
#   ARCH-COST the fix is takeable, changes no behaviour, and would multiply the
#             shipped image on the architecture this project builds. One entry,
#             measured per release on both arches inside python:3.11-slim.
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

    # ---- transformers: FIVE ENTRIES DELETED, AND THEY ARE FIXED, NOT DROPPED
    # PYSEC-2026-2288 (CVE-2026-1839), PYSEC-2026-2289 (CVE-2026-4372),
    # PYSEC-2026-2290 (CVE-2026-5241) and CVE-2026-9856 (GHSA-xrqw-3rrv-vx5w)
    # were accepted here under the MODEL class -- and the fifth id in that
    # family, the duplicate GHSA record of CVE-2026-4372, with them. The pin is
    # transformers==5.10.4 now, past every one of 5.0.0 / 5.3.0 / 5.5.0 /
    # 5.10.0, and OSV reports ZERO advisories against 5.10.4. Two further
    # advisories that affected 4.57.1 (PYSEC-2025-217, PYSEC-2025-218) were
    # never in this table because they carry no fix version; they were printed
    # in the UNFIXABLE column on every run and they clear with the same move.
    #
    # THE STALENESS CHECK BELOW IS WHAT MAKES THIS SAFE IN ONE COMMIT: an
    # accepted id that no longer appears exits 2. Leaving these behind after
    # the pin moved would have turned the gate red rather than letting a dead
    # exemption sit here being re-read as a live constraint. Same mechanism the
    # six starlette entries above went out through.
    #
    # WHAT THE BUMP WAS MEASURED TO DO: nothing. See the class note above and
    # pyproject.toml's transformers pin for the harness, both controls and the
    # numbers.

    # ---- torch: THREE OF FOUR DELETED; ONE REMAINS, RECLASSIFIED -----------
    # CVE-2025-2999 (fix 2.9.1), CVE-2025-3001 (fix 2.10.0) and PYSEC-2026-2286
    # / CVE-2026-24747 (fix 2.10.0) are cleared by the move to torch==2.10.0.
    #
    # PYSEC-2025-194 IS NOT CLEARED, AND ITS BLOCKER IS NOW A MEASUREMENT
    # RATHER THAN AN ABSENCE OF ONE. The fix is torch 2.13.0. Between 2.10.0
    # and 2.11.0 torch's Linux CUDA dependency markers lost their
    # `platform_machine == "x86_64"` guard, so from 2.11.0 on an aarch64 install
    # pulls fifteen nvidia wheels. Resolved inside python:3.11-slim on both
    # arches rather than read off the metadata:
    #
    #     linux/arm64   2.10.0  149 MB / 0 nvidia      <- the pin
    #                   2.11.0  2.7 GB / 15 nvidia
    #                   2.13.0  2.7 GB / 15 nvidia     <- the fix
    #     linux/amd64   2.9.0   3.8 GB   2.13.0  2.6 GB
    #
    # `make up` builds linux/arm64, so taking the fix as published adds ~2.6 GB
    # of CUDA to an image with no GPU. That is a cost, not an impossibility, and
    # it is filed as ARCH-COST rather than as a model risk: the ranking
    # measurement covers torch 2.13.0 too and reports zero rank changes.
    #
    # AND THE SINK IS NOT REACHABLE FROM THIS PROJECT -- MEASURED, not argued
    # from the summary. The advisory is memory corruption in
    # `torch.jit.script` (CVSS:3.1/AV:L/AC:L/PR:L/UI:N, local, low across
    # C/I/A). `git grep` over every tracked file for `torch.jit`, `jit.script`,
    # `torch.compile` and `TorchScript` returns hits in THIS COMMENT AND NOWHERE
    # ELSE -- phrased that way deliberately, because before this note the count
    # was zero and a claim reading "appears nowhere" would be falsified by the
    # sentence making it. The only torch API this project calls is
    # `torch.no_grad()` and a forward pass, in
    # oncotriage/agent/models.py:medcpt_score_pairs.
    #
    # REMOVAL CONDITION, mechanical rather than a judgement call. Delete this
    # entry when EITHER (a) a torch release >= 2.13.0 restores the
    # `platform_machine == "x86_64"` guard on its Linux CUDA markers -- check by
    # resolving it in python:3.11-slim on linux/arm64 and counting nvidia wheels
    # -- OR (b) the Dockerfile gains the CPU wheel index, which was measured at
    # 158 MB for 2.13.0+cpu on arm64 and is the recorded follow-up at
    # pyproject.toml's torch pin.
    "PYSEC-2025-194":  ("torch", "2.13.0",
                        "ARCH-COST +2.6 GB of CUDA on linux/arm64; "
                        "torch.jit sink unreachable"),
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
