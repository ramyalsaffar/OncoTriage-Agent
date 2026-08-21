# Trivy Ignore-File Staleness Check
###################################

"""Fail when an accepted entry in `.trivyignore` no longer appears in the scan.

WHY THIS EXISTS, AND WHY IT MIRRORS `.github/scripts/audit_gate.py`
-------------------------------------------------------------------
The pip-audit gate already refuses to let a dead exemption sit in its accepted
table: an id that has stopped appearing exits 2, on the argument that the entry
is now describing nothing and will be re-read by the next person as a live
constraint. `.trivyignore` had no such check. It is the same table, for the
other scanner, with the same failure mode -- and it now holds entries whose
whole point is that they EXPIRE (the BASE-LAG block, four util-linux ids that
clear the moment a base image carrying Debian's fix is published).

That block's own removal condition says it plainly:

    "Nothing else in this file has a removal condition that can be checked by
     a script; this one can, and leaving it here after the digest moves is the
     failure mode."

This is that script. It is not a second gate on vulnerabilities -- it never
looks at severity to decide anything and it cannot make the Trivy gate greener.
It gates on the HYGIENE of the accepted table, which is the axis the gate itself
cannot see: Trivy is perfectly happy to be handed an ignore file full of ids
that match nothing, and says so nowhere.

WHICH SCAN IT READS, AND WHY IT MUST BE THAT ONE
-------------------------------------------------
The FULL, non-gating scan in `.github/workflows/ci.yml`:

    every severity (LOW,MEDIUM,HIGH,CRITICAL), fixed AND unfixed, NO ignorefile.

All three properties are load-bearing, because an accepted id is exactly what
the OTHER scan is told not to show:

  * NO `--ignorefile`   -- the gate suppresses these ids by construction. Read
                           the gate's output and every accepted entry looks
                           stale, always, and the check would demand the file
                           delete itself.
  * every severity      -- an id re-rated HIGH -> MEDIUM still exists and its
                           argument still holds; calling it stale would send a
                           human to delete an entry that is one re-rating away
                           from mattering again. `.trivyignore` makes this
                           argument itself, in the note on CVE-2026-13595.
  * fixed AND unfixed   -- the gate is `--ignore-unfixed`. An accepted id whose
                           fix was WITHDRAWN upstream is still present in the
                           image; it is not gone.

"NO LONGER APPEARS" IS DELIBERATELY THE WEAKEST TEST THAT IS STILL TRUE
------------------------------------------------------------------------
Three narrower tests were considered and are reported rather than gated on,
because each would fail on a state that is not a defect:

    suppresses nothing at the gate today
        -- true of an id that is currently MEDIUM, or currently unfixed. Both
           are one vendor edit away from HIGH+fixed, and the entry is what
           keeps the argument attached to the id. Printed under INERT below.

The remaining two failure classes ARE gated, because both make the staleness
question unanswerable rather than merely uninteresting:

    a line this parser cannot read
        -- a skipped entry is invisible to staleness forever. It is reported
           by line number and fails, never skipped.
    the same id twice
        -- `.trivyignore`'s own first rule is "ONE LINE PER CVE". Two entries
           for one id means deleting the one this check names removes no
           suppression, and the second sits on with an argument nobody read.

RUNNING IT BY HAND, WHICH IS THE POINT OF THE DAY IT EXITS 2
--------------------------------------------------------------
Produce the same JSON CI produces, then read it. Both commands are the ones in
the workflow, with the paths made local; nothing here needs a runner:

    cd "03- Code"
    mkdir -p /tmp/trivy-reports
    docker run --rm \
      -v /var/run/docker.sock:/var/run/docker.sock \
      -v /tmp/trivy-reports:/reports \
      aquasec/trivy:0.73.0 image \
        --scanners vuln --severity LOW,MEDIUM,HIGH,CRITICAL \
        --exit-code 0 --timeout 20m --no-progress \
        --format json -o /reports/trivy-full.json \
        clinical-trial-patient-match:latest

    python .github/scripts/trivyignore_staleness.py \
        --report /tmp/trivy-reports/trivy-full.json

The image must be the one `make build` produces. Scanning a DIFFERENT image and
reading this output is the one way to get a confidently wrong answer here: every
id that image happens not to carry reports as stale.

ARCHITECTURE IS PART OF "A DIFFERENT IMAGE". CI scans linux/amd64. A local
build on Apple silicon is arm64 and its OS package set is not identical. The
util-linux and pillow entries were measured to appear on both; an id that
appears on one arch only would read as stale on the other, and the fix for that
is to compare against a report from the same platform CI scans, not to relax
this check.

Run from terminal:
    python .github/scripts/trivyignore_staleness.py --report <trivy-full.json>

Exit codes:
    0 -- every accepted entry still appears in the scan
    1 -- the report is missing, unreadable, not a Trivy report, or degenerate
         (no vulnerability rows at all), so the comparison could not be made
    2 -- the accepted table needs a human: a stale entry, an unreadable line,
         or a duplicated id
"""

import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path


# The repository root is the parent of `.github/`, derived from THIS file rather
# than from the working directory: the day this exits 2 somebody runs it from
# wherever they happen to be standing, and a default that depends on cwd would
# read a `.trivyignore` that is not the one CI mounts.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_IGNOREFILE = _REPO_ROOT / ".trivyignore"
_DEFAULT_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

# The gating scan is the step that mounts the ignore file. Located by that,
# never by step name: the name is prose and the mount is the thing that makes
# it the gate.
_GATE_MARKER = "--ignorefile /tmp/.trivyignore"
_SEVERITY_RE = re.compile(r"--severity\s+([A-Z,]+)")
_FALLBACK_GATE_SEVERITY = "HIGH,CRITICAL"

# What a Trivy ignore id looks like. Deliberately a SHAPE and not a list of
# known prefixes: this file already holds CVE-* and GHSA-*, the pip-audit gate
# holds PYSEC-*, and Trivy emits DLA-*, DSA-*, RUSTSEC-*, TEMP-* and vendor ids
# besides. Two accepted forms, and the second one is why this is not simply
# "must contain a hyphen":
#
#   hyphenated   CVE-2026-53612, GHSA-6v7p-g79w-8964, RUSTSEC-2021-0079
#   compact      DS002 — Trivy's own non-vulnerability ids carry no hyphen.
#                Nothing in this file uses one (it is a `--scanners vuln`
#                ignore list), but flagging a real id as UNREADABLE fails CI
#                for a defect that is not there, and the compact form is
#                cheap to admit: capitals and digits, at least one digit.
#
# Anything else — prose, a stray word, two ids on one line — is REPORTED, never
# guessed at. Note which way that errs: a line reported as unreadable names
# itself and is fixed in one edit, whereas a line quietly read as an id would
# be reported as STALE and tell a human to delete something that was never an
# entry.
_ID_RE = re.compile(
    r"^(?:[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9.]+)+"    # hyphenated
    r"|[A-Z][A-Z0-9]*[0-9][A-Z0-9]*)$"                # compact, e.g. DS002
)

# Trivy's plain-text ignore format allows an optional expiry on the same line:
#     CVE-2019-14697 exp:2023-01-01
# No entry uses it today. It is parsed anyway, so that the first one written
# does not land in the UNPARSEABLE list and get argued about instead of read.
_EXP_RE = re.compile(r"^exp:(\d{4}-\d{2}-\d{2})$")


def derive_gate_severity(workflow_path):
    """Return (severity string, where it came from) for the GATING Trivy step.

    Reporting only -- nothing this returns can change an exit code, which is
    why a failure to derive it degrades to a printed fallback rather than
    stopping the check. It is derived at all so that changing the gate's
    `--severity` cannot leave this script printing a confident sentence about
    a filter the gate no longer uses.

    Parsed as TEXT, not YAML: the standard library has no YAML reader, and
    installing one to read one flag would put a dependency in front of a check
    whose whole value is that it runs anywhere.
    """
    try:
        text = Path(workflow_path).read_text(encoding="utf-8")
    except OSError as exc:
        return _FALLBACK_GATE_SEVERITY, f"fallback, {workflow_path} unreadable: {exc}"
    if _GATE_MARKER not in text:
        return (_FALLBACK_GATE_SEVERITY,
                f"fallback, no step in {workflow_path} mounts the ignore file")
    # The flags of one `docker run` are one continued shell line; take the
    # window around the marker rather than the whole file, which also holds
    # the FULL scan's own --severity.
    head = text[:text.index(_GATE_MARKER)]
    window = head[-1200:]
    matches = _SEVERITY_RE.findall(window)
    if not matches:
        return (_FALLBACK_GATE_SEVERITY,
                f"fallback, no --severity found above the gate's ignorefile")
    return matches[-1], f"derived from {workflow_path}"


# ===========================================================================
# PARSING THE ACCEPTED TABLE
# ===========================================================================
def parse_ignorefile(path):
    """Return (entries, problems).

    entries  -- list of dicts: id, line number, raw text, expiry (or None)
    problems -- list of (line number, raw text, why) for lines that are neither
                blank, nor a comment, nor an id. NEVER silently dropped.
    """
    entries, problems = [], []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh.read().splitlines(), start=1):
            # Trivy strips from the first '#', so a trailing comment on an id
            # line is legal and this file uses that heavily.
            text = raw.split("#", 1)[0].strip()
            if not text:
                continue
            parts = text.split()
            vid, rest = parts[0], parts[1:]
            if not _ID_RE.match(vid):
                problems.append((lineno, raw.rstrip(),
                                 f"{vid!r} is not a vulnerability id"))
                continue
            expiry = None
            bad_tail = None
            for token in rest:
                match = _EXP_RE.match(token)
                if match:
                    expiry = match.group(1)
                else:
                    bad_tail = token
                    break
            if bad_tail is not None:
                problems.append((lineno, raw.rstrip(),
                                 f"trailing token {bad_tail!r} is neither an "
                                 f"expiry (exp:YYYY-MM-DD) nor a comment"))
                continue
            entries.append({"id": vid, "line": lineno,
                            "raw": raw.rstrip(), "expiry": expiry})
    return entries, problems


# ===========================================================================
# READING THE SCAN
# ===========================================================================
def load_report(path):
    """Return the report's vulnerability rows, or exit 1 explaining why not."""
    if not os.path.exists(path):
        print(f"FATAL: no Trivy report at {path}")
        print("       The FULL non-gating scan step writes it; see this file's")
        print("       docstring for the exact command to produce one locally.")
        sys.exit(1)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError as exc:
        print(f"FATAL: {path} is not JSON: {exc}")
        sys.exit(1)
    except OSError as exc:
        print(f"FATAL: cannot read {path}: {exc}")
        sys.exit(1)

    if not isinstance(data, dict) or "Results" not in data:
        # A Trivy image report is an object carrying Results. A list, a string
        # or an object without it is some other tool's output, and comparing
        # against it would report every accepted id as stale.
        kind = type(data).__name__
        print(f"FATAL: {path} parses as JSON ({kind}) but is not a Trivy "
              f"report: no 'Results' key.")
        sys.exit(1)

    rows = []
    for result in data.get("Results") or []:
        for vuln in result.get("Vulnerabilities") or []:
            rows.append({
                "id": vuln.get("VulnerabilityID"),
                "pkg": vuln.get("PkgName"),
                "installed": vuln.get("InstalledVersion"),
                "fixed": vuln.get("FixedVersion") or "",
                "severity": vuln.get("Severity") or "UNKNOWN",
                "target": result.get("Target"),
            })
    return data, rows


# ===========================================================================
# THE CHECK
# ===========================================================================
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Fail when a .trivyignore entry no longer appears in the "
                    "FULL Trivy scan.")
    parser.add_argument("--report", default="trivy-full.json",
                        help="Trivy JSON report from the FULL, non-gating scan "
                             "(all severities, no --ignorefile). "
                             "Default: ./trivy-full.json")
    parser.add_argument("--ignorefile", default=str(_DEFAULT_IGNOREFILE),
                        help=f"Default: {_DEFAULT_IGNOREFILE}")
    # The gate's own severity filter, so the INERT section can say what the
    # GATE sees rather than what this script sees. It decides nothing, and it
    # is DERIVED from the workflow rather than retyped here -- a second copy of
    # `--severity HIGH,CRITICAL` is a second copy to drift, and this one would
    # drift into a sentence a reader takes as fact. Supply it explicitly to
    # ask "what would be inert if the gate were X".
    parser.add_argument("--gate-severity", default=None,
                        help="Severities the gating scan uses. Reporting only. "
                             "Default: read out of .github/workflows/ci.yml.")
    parser.add_argument("--workflow", default=str(_DEFAULT_WORKFLOW),
                        help=f"Default: {_DEFAULT_WORKFLOW}")
    args = parser.parse_args(argv)

    print("=" * 78)
    print("TRIVYIGNORE STALENESS CHECK")
    print("=" * 78)
    print(f"ignore file : {args.ignorefile}")
    print(f"report      : {args.report}")

    if not os.path.exists(args.ignorefile):
        print(f"\nFATAL: no ignore file at {args.ignorefile}")
        return 1
    entries, problems = parse_ignorefile(args.ignorefile)

    # ---- unreadable lines: never skipped, and reported before the scan is
    # even read. This is a defect of the FILE; it does not need the report, and
    # putting it below the degenerate-report guard would let a bad scan hide it.
    status = 0
    if problems:
        status = 2
        print()
        print("-" * 78)
        print(f"UNREADABLE LINES: {len(problems)}")
        print("-" * 78)
        for lineno, raw, why in problems:
            print(f"  line {lineno}: {why}")
            print(f"      {raw!r}")
        print("\n  A line this parser cannot read is a line staleness can never")
        print("  see. It is reported rather than skipped, because a skipped")
        print("  entry is invisible here forever. Either it is a comment and")
        print("  needs a leading '#', or it is an id and needs fixing.")

    data, rows = load_report(args.report)
    print(f"artifact    : {data.get('ArtifactName')}")
    if args.gate_severity is not None:
        gate_severity, gate_source = args.gate_severity, "--gate-severity"
    else:
        gate_severity, gate_source = derive_gate_severity(args.workflow)
    print(f"gate severity: {gate_severity}  (from {gate_source})")
    gate_sev = {s.strip().upper()
                for s in gate_severity.split(",") if s.strip()}

    # ---- non-degeneracy: a scan that found nothing cannot answer this -----
    # Without this, an empty or wrong-image report makes EVERY accepted entry
    # look stale and the failure message tells a human to delete the file. The
    # workflow has already lost an image to a `docker builder prune` once; a
    # check that reads a scan of nothing must say so, not draw conclusions.
    if not rows:
        print("\n" + "-" * 78)
        print("FATAL: the report contains no vulnerability rows at all.")
        print("-" * 78)
        print("  Every accepted entry would read as stale against it, which is")
        print("  a statement about the report and not about the entries.")
        print("  Usual causes: the wrong image was scanned, the scan was run")
        print("  WITH --ignorefile, or the image was pruned before the scan.")
        return 1

    seen = {}
    for row in rows:
        seen.setdefault(row["id"], []).append(row)

    print(f"\n{len(entries)} accepted entr(y/ies), "
          f"{len(rows)} vulnerability row(s) in the report, "
          f"{len(seen)} distinct id(s)\n")

    # ---- duplicates -------------------------------------------------------
    by_id = {}
    duplicates = []
    for entry in entries:
        if entry["id"] in by_id:
            duplicates.append((entry, by_id[entry["id"]]))
        else:
            by_id[entry["id"]] = entry

    present = [e for e in entries if e["id"] in seen]
    stale = [e for e in entries if e["id"] not in seen]

    # ---- EVERYTHING IS PRINTED, on the audit gate's precedent -------------
    print("-" * 78)
    print(f"STILL PRESENT — the entry describes something that is in the image:"
          f" {len(present)}")
    print("-" * 78)
    if not present:
        print("  (none)")
    for entry in present:
        hits = seen[entry["id"]]
        sevs = sorted({h["severity"] for h in hits})
        pkgs = sorted({h["pkg"] for h in hits})
        shown = ", ".join(pkgs[:3]) + ("" if len(pkgs) <= 3 else f" +{len(pkgs) - 3} more")
        print(f"  {entry['id']:22s} {len(hits):3d} row(s)  {'/'.join(sevs):18s} {shown}")
    print()

    # ---- INERT: reported, never gated on ---------------------------------
    # An entry that suppresses nothing at the gate TODAY. It is not stale --
    # the finding is in the image and the argument still attaches to it -- but
    # a reader should know the gate would be just as green without it.
    inert = []
    for entry in present:
        hits = seen[entry["id"]]
        if not any(h["severity"] in gate_sev and h["fixed"] for h in hits):
            why = []
            if not any(h["severity"] in gate_sev for h in hits):
                why.append(f"no row at {'/'.join(sorted(gate_sev))}")
            if not any(h["fixed"] for h in hits):
                why.append("no fixed version published")
            inert.append((entry, "; ".join(why)))
    print("-" * 78)
    print(f"INERT — present, but suppressing nothing at the gate today: "
          f"{len(inert)}")
    print("-" * 78)
    if not inert:
        print("  (none)")
    for entry, why in inert:
        print(f"  {entry['id']:22s} {why}")
    print("  Reported, NOT gated on: the gate is `--ignore-unfixed "
          f"--severity {gate_severity}`, so these")
    print("  suppress nothing right now and one vendor re-rating brings them")
    print("  back. Deleting the entry deletes the argument with it.")
    print()

    # ---- expiries: Trivy stops honouring them, silently ------------------
    today = _dt.date.today().isoformat()
    expired = [e for e in entries if e["expiry"] and e["expiry"] < today]
    if any(e["expiry"] for e in entries):
        print("-" * 78)
        print(f"EXPIRING ENTRIES: {sum(1 for e in entries if e['expiry'])} "
              f"({len(expired)} already expired, as of {today})")
        print("-" * 78)
        for entry in entries:
            if entry["expiry"]:
                mark = "EXPIRED" if entry["expiry"] < today else "active"
                print(f"  {entry['id']:22s} exp:{entry['expiry']}  {mark}")
        print()

    # ---- duplicated ids ---------------------------------------------------
    if duplicates:
        status = 2
        print("-" * 78)
        print(f"DUPLICATED IDS: {len(duplicates)}")
        print("-" * 78)
        for dup, first in duplicates:
            print(f"  {dup['id']} on line {dup['line']}, already on line "
                  f"{first['line']}")
        print("\n  This file's own first rule is ONE LINE PER CVE. Two entries")
        print("  for one id means deleting the one a staleness failure names")
        print("  removes no suppression, and the other sits on carrying an")
        print("  argument nobody re-read.")
        print()

    # ---- staleness: the thing this exists for ----------------------------
    if stale:
        status = 2
        print("-" * 78)
        print(f"STALE ACCEPTED ENTRIES: {len(stale)}")
        print("-" * 78)
        for entry in stale:
            print(f"  line {entry['line']:4d}  {entry['id']}")
            print(f"      {entry['raw']}")
        print("\n  These no longer appear in the scan. Either the dependency")
        print("  moved and the entry should go, or the scan stopped seeing the")
        print("  package. Both need a human; neither should sit here silently.")
        if len(stale) == len(entries):
            print("\n  EVERY entry is stale, which is more likely one fault in")
            print("  the report than N independent dependency moves: check the")
            print("  image scanned is the one `make build` produces, on the")
            print("  platform CI scans, and that the FULL scan was run WITHOUT")
            print("  --ignorefile.")
        print()

    print("=" * 78)
    if status == 2:
        print(f"FAIL: the accepted table needs a human "
              f"({len(stale)} stale, {len(problems)} unreadable, "
              f"{len(duplicates)} duplicated).")
        print("=" * 78)
        return 2

    print(f"PASS: all {len(present)} accepted entr(y/ies) still appear in the "
          f"scan.")
    print(f"      {len(inert)} of them suppress nothing at the gate today; "
          f"printed above, not gated on.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug 20 2026

@author: ramyalsaffar
"""
