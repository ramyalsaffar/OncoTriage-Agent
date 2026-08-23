# Staging Walk and Dry-Run Manifest
###################################

"""
Walk the project root, apply the rulings, and report what WOULD upload.

THE DRY RUN IS THE GATE, not a convenience. `s3_stage.py` produces this report
and stops; the upload happens only on a second, explicit `--execute`. The
report is what an operator reads before spending money and bandwidth on ~60 GB,
and it is the only place the exclusion rulings are visible as consequences
rather than as intentions.

THE WALK PRUNES AT THE DIRECTORY, which is a correctness property and not only
a speed one. `os.walk` is given the chance to skip an excluded directory before
descending, so `99- GitHub` costs one `classify()` call instead of 311,875
`stat` calls -- and, more to the point, the four live `.env` files inside it are
never even enumerated. A walk that enumerated everything and filtered afterwards
would hold those paths in memory and depend on a later filter to drop them.

THE REPORT IS NOT A CACHE. `--execute` re-walks and re-scans from scratch. A
manifest that was clean an hour ago says nothing about a file added since, and
trusting one would make the secrets refusal a function of when the report was
generated rather than of what is on disk.
"""

import os

from oncotriage import config
from oncotriage.observability import console, get_logger
from oncotriage.staging import exclusions as _exclusions

log = get_logger(__name__)


#------------------------------------------------------------------------------


# What one `du`-style unit means here. Decimal, because that is what AWS bills
# and prices in -- reporting GiB beside a price quoted per GB would understate
# the bill by 7%.
_BYTES_PER_GB = 1_000_000_000


class WalkResult:
    """Everything the walk learned. Plain class, no decorator (check 2i)."""

    __slots__ = ("files", "total_bytes", "excluded_hits", "stale_rules",
                 "unreadable_dirs", "unruled", "root")

    def __init__(self, root):
        self.root = root
        self.files = []            # [(abspath, relpath, size)]
        self.total_bytes = 0
        self.excluded_hits = {}    # rule -> [count, bytes, reason]
        self.stale_rules = []      # rules naming a path that is not there
        self.unreadable_dirs = []  # [(relpath, error)]
        # PATHS NO RULING COVERS, BY NAME. They are DROPPED -- the classifier
        # defaults to not-staged, which is the safe direction for a tool whose
        # worst outcome is uploading a credential. But a default-deny that says
        # nothing is a narrower upload than the operator asked for, silently,
        # so every one is named in the report. This is the one place the
        # implementation departs from "walk the root MINUS the exclusions", and
        # naming them is what keeps that departure visible on every run instead
        # of buried in a count.
        self.unruled = []          # [relpath]


def _human(num_bytes):
    """A size a person can read. Decimal units, matching AWS."""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1000.0 or unit == "TB":
            return f"{value:,.1f} {unit}" if unit != "B" else f"{int(value):,} B"
        value /= 1000.0
    return f"{value:,.1f} TB"


def walk(root, plan, measure_excluded=True):
    """Enumerate what would upload, pruning excluded directories at the branch.

    ``measure_excluded`` costs a full `du` of every excluded subtree -- ~187 GB
    of `stat` calls on this tree -- and buys the "what did the rulings actually
    remove" column of the report. It is an argument rather than always-on
    because a `--execute` run wants the walk and does not want to pay for a
    report it will not print.
    """
    result = WalkResult(root)
    seen_rules = set()
    unruled = result.unruled

    for dirpath, dirnames, filenames in os.walk(root, topdown=True,
                                                onerror=result.unreadable_dirs
                                                .append):
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""

        keep = []
        for name in sorted(dirnames):
            child_rel = os.path.join(rel_dir, name) if rel_dir else name
            staged, reason, rule = _exclusions.classify(child_rel, plan)
            if staged:
                keep.append(name)
                seen_rules.add(rule)
            else:
                seen_rules.add(rule)
                if rule == "unruled":
                    unruled.append(child_rel + os.sep)
                size, count = ((_subtree_size(os.path.join(dirpath, name)))
                               if measure_excluded else (0, 0))
                slot = result.excluded_hits.setdefault(rule, [0, 0, reason])
                slot[0] += count
                slot[1] += size
        dirnames[:] = keep

        for name in sorted(filenames):
            child_rel = os.path.join(rel_dir, name) if rel_dir else name
            staged, reason, rule = _exclusions.classify(child_rel, plan)
            abspath = os.path.join(dirpath, name)
            seen_rules.add(rule)
            if not staged:
                if rule == "unruled":
                    unruled.append(child_rel)
                slot = result.excluded_hits.setdefault(rule, [0, 0, reason])
                slot[0] += 1
                try:
                    slot[1] += os.path.getsize(abspath)
                except OSError:
                    pass
                continue
            try:
                size = os.path.getsize(abspath)
            except OSError as exc:
                # A file that vanished or cannot be stat'ed is NOT silently
                # dropped: it goes to the scan as unreadable, which refuses.
                result.files.append((abspath, child_rel, -1))
                result.unreadable_dirs.append(
                    OSError(f"{child_rel}: {type(exc).__name__}: {exc}"))
                continue
            result.files.append((abspath, child_rel, size))
            result.total_bytes += size

    # A ruling naming a path that is not on disk. REPORTED, NOT FATAL: the
    # secrets scan is the guarantee, so a stale path rule costs record-keeping
    # rather than safety. It is still worth naming -- a rename is exactly how a
    # ruling stops describing anything.
    for parts, (reason, kind) in sorted(plan.excluded.items()):
        display = "/".join(parts)
        if not os.path.exists(os.path.join(root, *parts)):
            result.stale_rules.append((display, kind, reason))

    return result


def _subtree_size(path):
    """(bytes, file count) under one directory. Used only for the report."""
    total = 0
    count = 0
    for dirpath, _dirs, filenames in os.walk(path, onerror=lambda _e: None):
        for name in filenames:
            count += 1
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total, count


#------------------------------------------------------------------------------
# COST
#------------------------------------------------------------------------------


def estimate_cost(total_bytes, object_count):
    """Storage per month and the one-time PUT charge, kept separate.

    THE TWO ARE NOT ADDED. A PUT charge is paid once on the first sync; folding
    it into a monthly figure reports a recurring cost that does not recur, and
    an operator comparing months would see the first one and budget from it.
    """
    gigabytes = total_bytes / _BYTES_PER_GB
    return {
        "gigabytes": gigabytes,
        "storage_usd_per_month": gigabytes * config.S3_STANDARD_USD_PER_GB_MONTH,
        "put_usd_one_time": (object_count / 1000.0) * config.S3_PUT_USD_PER_1000,
        "rate_gb_month": config.S3_STANDARD_USD_PER_GB_MONTH,
        "rate_put_per_1000": config.S3_PUT_USD_PER_1000,
    }


#------------------------------------------------------------------------------
# THE REPORT
#------------------------------------------------------------------------------


def build_report(result, plan, scan, top_n=10):
    """The dry-run manifest as a plain dict. Rendering is a separate step."""
    largest = sorted(result.files, key=lambda row: row[2], reverse=True)[:top_n]
    by_prefix = {}
    for _abspath, relpath, size in result.files:
        top = relpath.split(os.sep)[0]
        slot = by_prefix.setdefault(top, [0, 0])
        slot[0] += 1
        slot[1] += max(size, 0)

    return {
        "root": result.root,
        "staged_roots": plan.staged_roots(),
        "file_count": len(result.files),
        "total_bytes": result.total_bytes,
        "by_prefix": {k: {"files": v[0], "bytes": v[1]}
                      for k, v in sorted(by_prefix.items())},
        "largest": [{"path": relpath, "bytes": size}
                    for _a, relpath, size in largest],
        "excluded_hits": {
            rule: {"files": count, "bytes": size, "reason": reason}
            for rule, (count, size, reason) in sorted(result.excluded_hits.items())
        },
        "stale_rules": [{"path": p, "kind": k, "reason": r}
                        for p, k, r in result.stale_rules],
        # WALK ERRORS ARE REPORTED. They were collected and dropped in the
        # first draft of this module, which is the shape that turns "os.walk
        # could not descend into this directory" into a smaller upload nobody
        # notices. A directory the walk could not read may hold anything,
        # credentials included, so it is surfaced beside the scan's own
        # unreadable list rather than folded into it -- the remedies differ.
        "walk_errors": [str(e) for e in result.unreadable_dirs],
        "unruled": sorted(result.unruled),
        "scan": {
            "files_scanned": scan.files_scanned,
            "bytes_read": scan.bytes_read,
            "findings": len(scan.findings),
            "allowlisted": len(scan.allowlisted),
            "unreadable": len(scan.unreadable),
            "clean": scan.clean(),
        },
        "cost": estimate_cost(result.total_bytes, len(result.files)),
    }


def render_report(report, out=None):
    """Print the manifest. ``out`` is injectable so a test can capture it."""
    emit = out if out is not None else console.out
    cost = report["cost"]

    emit("")
    emit("=" * 78)
    emit("S3 STAGING DRY RUN -- NOTHING HAS BEEN UPLOADED")
    emit("=" * 78)
    emit(f"Project root : {report['root']}")
    emit(f"Staged roots : {', '.join(report['staged_roots'])}")
    emit("")
    emit(f"WOULD UPLOAD : {report['file_count']:,} objects, "
         f"{_human(report['total_bytes'])}")
    emit("")

    emit("Per top-level prefix")
    emit("-" * 78)
    emit(f"  {'prefix':<34} {'files':>10} {'size':>14}")
    for prefix, stats in report["by_prefix"].items():
        emit(f"  {prefix:<34} {stats['files']:>10,} "
             f"{_human(stats['bytes']):>14}")
    emit("")

    emit(f"Ten largest objects")
    emit("-" * 78)
    for row in report["largest"]:
        emit(f"  {_human(row['bytes']):>12}  {row['path']}")
    emit("")

    emit("Exclusion hits -- what the rulings removed")
    emit("-" * 78)
    emit(f"  {'rule':<52} {'files':>9} {'size':>12}")
    total_excluded_files = 0
    total_excluded_bytes = 0
    for rule, stats in sorted(report["excluded_hits"].items(),
                              key=lambda kv: -kv[1]["bytes"]):
        total_excluded_files += stats["files"]
        total_excluded_bytes += stats["bytes"]
        emit(f"  {rule[:52]:<52} {stats['files']:>9,} "
             f"{_human(stats['bytes']):>12}")
    emit(f"  {'TOTAL EXCLUDED':<52} {total_excluded_files:>9,} "
         f"{_human(total_excluded_bytes):>12}")
    emit("")

    if report["stale_rules"]:
        emit("Stale rulings -- named a path that is not on disk")
        emit("-" * 78)
        emit("  Reported, not fatal: the secrets scan is the guarantee, so a")
        emit("  stale path rule costs record-keeping and not safety.")
        for row in report["stale_rules"]:
            emit(f"    [{row['kind']}] {row['path']}")
        emit("")

    if report.get("unruled"):
        emit("UNRULED -- dropped, and no ruling says so")
        emit("-" * 78)
        emit("  These paths match no staged and no excluded entry, so the")
        emit("  classifier's default applied and they were NOT uploaded.")
        emit("  Default-deny is the safe direction for a tool that can upload a")
        emit("  credential, but it is NARROWER than 'the root minus the")
        emit("  exclusions' -- so each one is named here rather than counted.")
        emit("  Give each an entry in s3_staging_exclusions.json to make the")
        emit("  decision explicit.")
        for line in report["unruled"]:
            emit(f"    {line}")
        emit("")

    if report.get("walk_errors"):
        emit("Walk errors -- directories or files os.walk could not read")
        emit("-" * 78)
        emit("  A path the walk could not read may hold anything. Each one")
        emit("  below was NOT enumerated and is therefore NOT in the counts")
        emit("  above.")
        for line in report["walk_errors"]:
            emit(f"    {line}")
        emit("")

    scan = report["scan"]
    emit("Secrets scan -- filename AND content, over the staged set")
    emit("-" * 78)
    emit(f"  files scanned    : {scan['files_scanned']:,}")
    emit(f"  bytes read       : {_human(scan['bytes_read'])} "
         f"(bounded prefix per file)")
    emit(f"  findings         : {scan['findings']}")
    emit(f"  allowlisted      : {scan['allowlisted']}")
    emit(f"  unreadable       : {scan['unreadable']}")
    emit(f"  VERDICT          : {'CLEAN' if scan['clean'] else 'REFUSED'}")
    emit("")

    emit("Estimated cost -- S3 Standard, us-east-1")
    emit("-" * 78)
    emit(f"  storage          : ${cost['storage_usd_per_month']:,.2f} / month "
         f"({cost['gigabytes']:,.1f} GB @ ${cost['rate_gb_month']}/GB-month)")
    emit(f"  PUT requests     : ${cost['put_usd_one_time']:,.2f} ONE TIME "
         f"({report['file_count']:,} objects @ "
         f"${cost['rate_put_per_1000']}/1000)")
    emit("")
    emit("  NOT INCLUDED, and each is stated rather than folded in:")
    emit("    - VERSIONING IS ON, so every re-sync of a CHANGED object keeps")
    emit("      the previous version and storage grows with churn. This figure")
    emit("      is the first sync only.")
    emit("    - data transfer IN to S3 is free; GET/egress is not, and depends")
    emit("      on retrieval nobody has planned yet.")
    emit("    - no lifecycle rule is configured, so nothing ages into a")
    emit("      cheaper class on its own.")
    emit("=" * 78)
    emit("")
