"""`.github/scripts/trivyignore_staleness.py` -- the accepted table's hygiene gate.

WHAT THIS FILE IS ABOUT
=======================
That script is the only thing in the repository that can tell a human an entry
in ``.trivyignore`` has stopped describing anything. Trivy itself is perfectly
happy to be handed an ignore file full of ids that match nothing and says so
nowhere, so a dead exemption sits there being re-read by the next person as a
live constraint -- which is the exact failure `.github/scripts/audit_gate.py`
already refuses for pip-audit.

IT HAD NO TEST. Its behaviour was established by running it by hand during the
session that wrote it, and a control demonstrated at a terminal is a control
that runs once. This file is those demonstrations made standing.

HOW IT IS DRIVEN, AND WHY NOT exec()
------------------------------------
Every scenario runs the REAL script as a subprocess -- ``sys.executable`` plus
its path -- and asserts on the exit code and the printed sections. Nothing is
imported from it, nothing is exec'd, no source is patched, so this file needs
no ``_EXEC_ALLOWLIST`` entry in tests/test_package_invariants.py and section 1c
of that file has nothing to see here.

That also means the thing under test is the shipped script rather than a copy
of it, which matters more than usual here: the script's exit code IS its
contract (0 / 1 / 2, three different instructions to a human), and an exit code
is precisely what an in-process call does not produce.

EVERY SUBPROCESS RUNS WITH ``cwd`` SET TO THE TEMP DIRECTORY. That is not
tidiness. The script's own docstring argues that ``_REPO_ROOT`` is derived from
``__file__`` "rather than from the working directory: the day this exits 2
somebody runs it from wherever they happen to be standing". Running every
scenario from somewhere else is what turns that paragraph into a measurement,
and section 2 asserts the defaults it resolves from there.

THE CONTROLS ARE DIFFERENT INPUTS
---------------------------------
For a script that is a pure function of two files, the natural control is a
different pair of files -- the shape tests/test_agent_patient_hash_coverage.py
and tests/test_indexer_criteria_split_gate.py settled on. So every assertion
below is paired with a scenario built to make it FAIL, and the pairing is the
point rather than a decoration:

  * "a nonexistent id exits 2 and is named" is paired with the same table
    whose ids are all present, which must exit 0 and name nothing;
  * "every-entry-stale prints the systematic-fault paragraph" is paired with a
    table where SOME entries are stale, which must exit 2 and NOT print it --
    without that pair the first assertion is satisfied by any failing run;
  * "an expiry token parses" is paired with the same token minus its ``exp:``
    prefix, which must be reported UNREADABLE;
  * "the gate severity is derived from the workflow" is paired with a second
    workflow carrying a different value, so a printed constant fails.

THE MINIATURE REPORT IS A LITERAL IN THIS FILE, and its ADEQUACY IS DERIVED
--------------------------------------------------------------------------
``_MINIATURE_REPORT`` below is a Trivy-shaped image report -- an ``os-pkgs``
block, a ``python-pkg`` block, and a third Result carrying no
``Vulnerabilities`` key at all, which is a shape Trivy really emits. It is
serialized into the temp directory for each scenario.

A hand-written fixture is only worth what its shape is worth, so section 1
does not TRUST it: it parses the shipped script with ``ast``, collects every
key the script reads out of a report (``data.get(...)``, ``result.get(...)``,
``vuln.get(...)``, and the ``"Results" not in data`` guard), and requires the
miniature to carry all of them at the right nesting. A field the script starts
reading tomorrow fails HERE rather than being silently absent from every
scenario. The derivation carries its own non-degeneracy probe, because an AST
walk that found nothing would pass for free.

WHAT THIS FILE CANNOT PROVE, stated rather than glossed
-------------------------------------------------------
That a real Trivy 0.73.0 report has the shape the miniature claims. The AST
derivation narrows that to "the miniature carries every field the script
reads", which is the answerable half; the other half is a property of Trivy's
output format and would need a real scan of a real image -- network, a daemon,
and twenty minutes -- which is exactly what bucket A is defined to exclude.
The key names were taken from a real report produced by the command in the
script's own docstring.

Nor does it prove anything about ARCHITECTURE. The script's docstring warns
that an id present on linux/amd64 and absent on arm64 reads as stale; that is a
statement about two real images and no fixture can stand in for it.

BUCKET A. No network, no keys, no spend, no Docker, no daemon, no live Qdrant,
no corpus, no database, no git history. It writes only inside one
``tempfile.mkdtemp()``, which it removes and then asserts gone. NOT in the
collision matrix -- derived, not assumed: it writes no repository file at all,
and the three it READS (`.github/scripts/trivyignore_staleness.py`,
`.trivyignore`, `.github/workflows/ci.yml`) are written by neither of the
suite's two writers. All three are sha256-compared in the last section.
"""

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile


# ===========================================================================
# WHERE THINGS ARE
# ===========================================================================
# Derived from this file's own location and NOT from `oncotriage.__file__`,
# because this file deliberately imports nothing from the package: its subject
# is a standalone script under `.github/scripts/` that must run before anything
# is installed. A HARD guard rather than a check() -- a wrong root here is not
# one failure but every failure, each with a misleading message. That is the
# same reasoning tests/test_package_invariants.py,
# tests/test_config_snapshot_date_rot.py and the audit control give for the
# same derivation.
#
# REALPATH, NOT ABSPATH, AND THAT IS NOT A DETAIL. The script derives its own
# defaults with `Path(__file__).resolve()`, which RESOLVES SYMLINKS; abspath
# does not. The two agree on a checkout that has none and disagree the moment
# one appears -- on macOS `/var` is a symlink to `/private/var`, so a tree
# under `tempfile.gettempdir()` makes the script print `/private/var/...` while
# this file expected `/var/...`. Measured rather than anticipated: three checks
# in sections 2 and 12 failed exactly that way against a byte-identical copy of
# the repository, which is a defect in the comparison and not in either file.
_TESTS_DIR = os.path.dirname(os.path.realpath(__file__))
_CODE_DIR = os.path.dirname(_TESTS_DIR)

_SCRIPT = os.path.join(_CODE_DIR, ".github", "scripts", "trivyignore_staleness.py")
_REAL_IGNOREFILE = os.path.join(_CODE_DIR, ".trivyignore")
_REAL_WORKFLOW = os.path.join(_CODE_DIR, ".github", "workflows", "ci.yml")

for _required in (_SCRIPT, _REAL_IGNOREFILE, _REAL_WORKFLOW):
    if not os.path.isfile(_required):
        raise SystemExit(
            f"CANNOT RUN: {_required} not found. This file derives the "
            f"repository root from its own location ({_CODE_DIR}); if the "
            f"tests directory moved, that derivation moved with it.")


def _sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


_SHA_BEFORE = {p: _sha256(p)
               for p in (_SCRIPT, _REAL_IGNOREFILE, _REAL_WORKFLOW)}

_TMP = tempfile.mkdtemp(prefix="trivyignore-staleness-test-")


# ===========================================================================
# THE HARNESS
# ===========================================================================
_passed = 0
_failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def check_true(label, cond):
    check(label, bool(cond), True)


def section(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


class Run:
    """The outcome of one subprocess invocation. NEVER raises.

    A bare `subprocess.run(...)` inside a `check()` argument list would let an
    OSError escape while the argument was being evaluated and kill the file
    with no summary -- the defect tests/test_storage_query_layer.py,
    tests/test_dashboard_reproducibility_tab.py and
    tests/test_agent_trial_verdict_normalization.py each had to fix. A launch
    failure becomes rc=-1 and a message on `out`, which every assertion below
    then FAILS on rather than aborting.
    """

    def __init__(self, rc, out):
        self.rc = rc
        self.out = out

    def __repr__(self):                              # pragma: no cover - display
        return f"<Run rc={self.rc} {len(self.out)} chars>"


def run(ignorefile=None, report=None, workflow=None, gate_severity=None,
        extra=None, script=None):
    """Drive the shipped script. Absolute paths only, cwd deliberately elsewhere.

    `script` points the run at a DIFFERENT copy of the script, which is how the
    one control in this file that needs pre-fix behaviour gets it: a copy in
    the temp directory with one line reverted. A copy is driven as a
    subprocess exactly as the shipped one is -- nothing is exec'd, nothing is
    imported, and the shipped file is never written -- so the mutated-copy rule
    and the "no _EXEC_ALLOWLIST entry" property both hold.
    """
    argv = [sys.executable, script or _SCRIPT]
    if ignorefile is not None:
        argv += ["--ignorefile", ignorefile]
    if report is not None:
        argv += ["--report", report]
    if workflow is not None:
        argv += ["--workflow", workflow]
    if gate_severity is not None:
        argv += ["--gate-severity", gate_severity]
    argv += list(extra or ())

    env = dict(os.environ)
    # No bytecode anywhere near the repository. The script imports only the
    # standard library so none would be written today; pass 20f-1 lost two
    # hours to a stale .pyc and the guard costs nothing.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        completed = subprocess.run(argv, cwd=_TMP, env=env,
                                   capture_output=True, text=True, timeout=120)
    except OSError as exc:                           # pragma: no cover - launch
        return Run(-1, f"<launch failed: {type(exc).__name__}: {exc}>")
    except subprocess.TimeoutExpired:                # pragma: no cover - hang
        return Run(-1, "<timed out>")
    return Run(completed.returncode, completed.stdout + completed.stderr)


_RULE_D = "-" * 78
_RULE_E = "=" * 78


def sect(out, header_prefix):
    """The BODY of one printed section, or None when the header is absent.

    Substring matching on the whole output is not good enough here and the
    reason is concrete: every accepted id is printed under STILL PRESENT, so
    `"CVE-1111-1111" in out` is satisfied by a run that found it perfectly
    healthy. The script frames each section as

        ------ (78)
        HEADER: n
        ------ (78)
        body ...

    so the body is what lies between the second rule and the next rule of
    either kind. Returning None rather than "" distinguishes "the section was
    not printed" from "it was printed empty", which is the difference between
    two of the assertions below.
    """
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(header_prefix):
            start = i + 2 if i + 1 < len(lines) and lines[i + 1] == _RULE_D else i + 1
            body = []
            for line2 in lines[start:]:
                if line2 == _RULE_D or line2 == _RULE_E:
                    break
                body.append(line2)
            return "\n".join(body)
    return None


def header_line(out, prefix):
    """The full header line itself (it carries the count), or None."""
    for line in out.splitlines():
        if line.startswith(prefix):
            return line
    return None


def write(name, text):
    path = os.path.join(_TMP, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def write_json(name, payload):
    return write(name, json.dumps(payload, indent=1))


def ignore_file(name, lines):
    """Write an ignore file and return (path, {id_or_text: 1-based line no})."""
    path = write(name, "".join(l + "\n" for l in lines))
    return path, {text: n for n, text in enumerate(lines, start=1)}


# ===========================================================================
# THE MINIATURE REPORT
# ===========================================================================
# Trivy-shaped, deliberately small, and every row here exists to be reached by
# a named assertion below rather than to look plausible:
#
#   CVE-2026-53612          HIGH   fixed        -> present, NOT inert
#   GHSA-6v7p-g79w-8964     HIGH   fixed        -> present, NOT inert, and the
#                                                  hyphenated non-CVE id shape
#   CVE-2025-47273          HIGH   fixed, TWICE -> the two-row count, one row
#                                                  in each of the two blocks
#   CVE-2026-77777          HIGH   NO fix       -> INERT for the fix reason
#   CVE-2026-88888          MEDIUM fixed        -> INERT for the severity
#                                                  reason ONLY, which is what
#                                                  makes --gate-severity
#                                                  MEDIUM observable
#   CVE-2026-99999          MEDIUM NO fix       -> INERT for both reasons
#
# The ids are the real project's where the shape matters (the two in
# `.trivyignore` today that a reader would recognise) and obviously synthetic
# 7/8/9-repeated ones where it does not, so nobody mistakes a fixture value for
# a measurement.
#
# THE THIRD RESULT HAS NO `Vulnerabilities` KEY. Trivy emits those for a target
# it scanned and found clean, and `load_report` handles it with
# `result.get("Vulnerabilities") or []`. A fixture without one would leave that
# `or []` unexercised by every scenario in this file.
_MINIATURE_REPORT = {
    "SchemaVersion": 2,
    "ArtifactName": "clinical-trial-patient-match:latest",
    "ArtifactType": "container_image",
    "Results": [
        {
            "Target": "clinical-trial-patient-match:latest (debian 13.1)",
            "Class": "os-pkgs",
            "Type": "debian",
            "Vulnerabilities": [
                {"VulnerabilityID": "CVE-2026-53612", "PkgName": "git",
                 "InstalledVersion": "1:2.41.0-1", "FixedVersion": "1:2.41.5-0+deb13u1",
                 "Severity": "HIGH"},
                {"VulnerabilityID": "CVE-2026-77777", "PkgName": "openssl",
                 "InstalledVersion": "3.0.11-1", "FixedVersion": "",
                 "Severity": "HIGH"},
                {"VulnerabilityID": "CVE-2026-88888", "PkgName": "util-linux",
                 "InstalledVersion": "2.40.2-11", "FixedVersion": "2.40.2-12",
                 "Severity": "MEDIUM"},
                # NO `FixedVersion` KEY AT ALL, which is what Trivy emits
                # for a finding with no published fix -- as against
                # CVE-2026-77777 above, which carries the empty string. Both
                # shapes reach `vuln.get("FixedVersion") or ""`, and a fixture
                # carrying only one of them would leave half of that expression
                # unexercised by every scenario in this file.
                {"VulnerabilityID": "CVE-2026-99999", "PkgName": "libc6",
                 "InstalledVersion": "2.36-9",
                 "Severity": "MEDIUM"},
                {"VulnerabilityID": "CVE-2025-47273", "PkgName": "python3-setuptools",
                 "InstalledVersion": "66.1.1-1", "FixedVersion": "78.1.1",
                 "Severity": "HIGH"},
            ],
        },
        {
            "Target": "opt/venv/lib/python3.11/site-packages",
            "Class": "lang-pkgs",
            "Type": "python-pkg",
            "Vulnerabilities": [
                {"VulnerabilityID": "GHSA-6v7p-g79w-8964", "PkgName": "msgpack",
                 "InstalledVersion": "1.1.2", "FixedVersion": "1.2.1",
                 "Severity": "HIGH"},
                {"VulnerabilityID": "CVE-2025-47273", "PkgName": "setuptools",
                 "InstalledVersion": "70.3.0", "FixedVersion": "78.1.1",
                 "Severity": "HIGH"},
            ],
        },
        {
            "Target": "usr/local/bin/oncotriage-entrypoint",
            "Class": "secret",
            "Type": "",
        },
    ],
}

# Distinct ids the miniature carries, and one it deliberately does not.
_PRESENT_HIGH_FIXED = "CVE-2026-53612"
_PRESENT_GHSA = "GHSA-6v7p-g79w-8964"
_PRESENT_TWICE = "CVE-2025-47273"
_PRESENT_NO_FIX = "CVE-2026-77777"
_PRESENT_MEDIUM_FIXED = "CVE-2026-88888"
_PRESENT_MEDIUM_NO_FIX = "CVE-2026-99999"
_ABSENT_ID = "CVE-2026-11111"
_ABSENT_ID_2 = "CVE-2026-22222"

REPORT = write_json("trivy-full.json", _MINIATURE_REPORT)

# A workflow shaped like the real one's gating step. The `--severity` must sit
# ABOVE the `--ignorefile` marker, because derive_gate_severity() reads the
# 1200-character window that PRECEDES the marker -- measured, not assumed: the
# first version of this fixture put them the other way round and the script
# correctly reported `fallback, no --severity found above the gate's
# ignorefile`.
def workflow_text(severity):
    return (
        "      - name: Trivy image scan (gating)\n"
        "        run: |\n"
        "          docker run --rm \\\n"
        "            aquasec/trivy:0.73.0 image \\\n"
        f"              --scanners vuln --severity {severity} \\\n"
        "              --ignore-unfixed --exit-code 1 \\\n"
        "              --ignorefile /tmp/.trivyignore \\\n"
        "              clinical-trial-patient-match:latest\n"
    )


WORKFLOW = write("gate-workflow.yml", workflow_text("HIGH,CRITICAL"))


# ===========================================================================
# SECTION 1 -- THE FIXTURE'S ADEQUACY IS DERIVED FROM THE SCRIPT, NOT ASSERTED
# ===========================================================================
section("SECTION 1 -- the miniature carries every field the script reads")

# A hand-written fixture is worth exactly what its shape is worth, and the
# author of the fixture is the author of the assertions -- so "it has the right
# fields" cannot be a list retyped here. It is READ OFF the script: every
# `data.get("X")`, `result.get("X")` and `vuln.get("X")`, plus the `"Results"
# not in data` guard, at the names load_report() binds. A key the script starts
# reading tomorrow fails here instead of quietly being absent from every
# scenario below.
_SCRIPT_AST = ast.parse(open(_SCRIPT, encoding="utf-8").read(), _SCRIPT)

_READS = {"data": set(), "result": set(), "vuln": set()}
for _node in ast.walk(_SCRIPT_AST):
    # <name>.get("literal")
    if (isinstance(_node, ast.Call)
            and isinstance(_node.func, ast.Attribute)
            and _node.func.attr == "get"
            and isinstance(_node.func.value, ast.Name)
            and _node.func.value.id in _READS
            and _node.args
            and isinstance(_node.args[0], ast.Constant)
            and isinstance(_node.args[0].value, str)):
        _READS[_node.func.value.id].add(_node.args[0].value)
    # "literal" in <name>  /  "literal" not in <name>
    if (isinstance(_node, ast.Compare)
            and len(_node.ops) == 1
            and isinstance(_node.ops[0], (ast.In, ast.NotIn))
            and isinstance(_node.left, ast.Constant)
            and isinstance(_node.left.value, str)
            and isinstance(_node.comparators[0], ast.Name)
            and _node.comparators[0].id in _READS):
        _READS[_node.comparators[0].id].add(_node.left.value)

# NON-DEGENERACY FIRST. A walk that matched nothing would satisfy every
# "is a subset of the fixture" assertion below for free -- an empty set is a
# subset of everything -- so the counts are pinned before they are used. The
# numbers are what the shipped script reads today; a new one is a failure that
# names itself.
check("1a data-level keys read by the script",
      sorted(_READS["data"]), ["ArtifactName", "Results"])
check("1b result-level keys read by the script",
      sorted(_READS["result"]), ["Target", "Vulnerabilities"])
check("1c vuln-level keys read by the script",
      sorted(_READS["vuln"]),
      ["FixedVersion", "InstalledVersion", "PkgName", "Severity",
       "VulnerabilityID"])
check_true("1d ...and the walk is non-degenerate (it found keys at all three "
           "levels)",
           all(len(v) >= 2 for v in _READS.values()))

check("1e the miniature carries every data-level key",
      sorted(_READS["data"] - set(_MINIATURE_REPORT)), [])

_results = _MINIATURE_REPORT["Results"]
check_true("1f the miniature has more than one Result block", len(_results) > 1)
check("1g ...one of them is os-pkgs",
      sum(1 for r in _results if r.get("Class") == "os-pkgs"), 1)
check("1h ...one of them is a python-pkg",
      sum(1 for r in _results if r.get("Type") == "python-pkg"), 1)
check("1i every Result carries Target, which the script reads for its report",
      [r for r in _results if "Target" not in r], [])
check_true("1j one Result deliberately carries NO Vulnerabilities key, which "
           "is what exercises `result.get(...) or []`",
           any("Vulnerabilities" not in r for r in _results))

_vulns = [v for r in _results for v in r.get("Vulnerabilities") or []]
check_true("1k the miniature has vulnerability rows at all", len(_vulns) >= 5)
check("1l every row carries VulnerabilityID",
      [v for v in _vulns if "VulnerabilityID" not in v], [])
for _key in sorted(_READS["vuln"]):
    check_true(f"1m every vuln-level key the script reads appears on at least "
               f"one row: {_key}",
               any(_key in v for v in _vulns))

# Both shapes of "no fix", because the script writes `... or ""` and a fixture
# carrying one of them leaves the other half of that expression untested.
check_true("1n one row omits FixedVersion entirely (Trivy's shape for no fix)",
           any("FixedVersion" not in v for v in _vulns))
check_true("1o ...and one carries it as the empty string",
           any(v.get("FixedVersion") == "" for v in _vulns))

_ids = [v["VulnerabilityID"] for v in _vulns]
check("1p CVE-2025-47273 appears on exactly two rows, in two different blocks",
      _ids.count(_PRESENT_TWICE), 2)
for _absent in (_ABSENT_ID, _ABSENT_ID_2):
    check_true(f"1q {_absent} is deliberately NOT in the report, so a scenario "
               f"using it is testing staleness rather than a typo",
               _absent not in _ids)


# ===========================================================================
# SECTION 2 -- THE DEFAULTS RESOLVE OFF __file__, NOT off the working directory
# ===========================================================================
section("SECTION 2 -- the defaults are the real repository's, from any cwd")

# The script's docstring argues that `_REPO_ROOT` is derived from `__file__`
# "rather than from the working directory: the day this exits 2 somebody runs
# it from wherever they happen to be standing, and a default that depends on
# cwd would read a `.trivyignore` that is not the one CI mounts." Every run in
# this file has cwd=_TMP, so --help printed from there is the measurement of
# that paragraph.
#
# It also makes every OTHER scenario non-degenerate: they all pass an explicit
# --ignorefile into the temp directory, and that redirection only means
# something once the default is known to be the real file it is redirecting
# AWAY from. Same argument as the five database-isolation tests.
# ARGPARSE WRAPS ITS HELP TEXT, and it wraps long words MID-TOKEN rather than
# only at spaces -- `/private/var/folders/l6/6z3vc5_95_ddnwd6ndd16` + newline +
# `h000000gn/T/...` is a real observed break. So an absolute path is not a
# contiguous substring of `--help`, and collapsing runs of whitespace to a
# single space is not enough either: the space argparse inserted is INSIDE what
# was one path component. Both sides therefore have ALL whitespace removed.
#
# BOTH VERSIONS OF THIS WERE MEASURED RATHER THAN ANTICIPATED, and the second
# is the instructive one: the collapse-to-single-space version PASSED against
# the real repository, whose path happens to wrap at a space, and failed only
# against a byte-identical copy under a temp directory whose path does not. An
# assertion that holds because of where a line happened to break is an
# assertion that has not been tested.
def _flat(text):
    return "".join(text.split())


_help = run(extra=["--help"])
_help_flat = _flat(_help.out)
check("2a --help exits 0", _help.rc, 0)
check_true("2b the default --ignorefile is the repository's real .trivyignore, "
           "resolved from a cwd that is not the repository",
           _flat(_REAL_IGNOREFILE) in _help_flat)
check_true("2c the default --workflow is the repository's real ci.yml",
           _flat(_REAL_WORKFLOW) in _help_flat)
check_true("2d ...and the temp directory the scenarios use is NOT the "
           "repository (so the redirection below is a real redirection)",
           os.path.realpath(_TMP) != _CODE_DIR)


# ===========================================================================
# SECTION 3 -- THE PASSING PATH
# ===========================================================================
section("SECTION 3 -- every accepted entry still appears: exit 0")

_clean_lines = [
    "# a full-line comment, which the parser must skip entirely",
    "",
    f"{_PRESENT_HIGH_FIXED}   # HIGH   fixed 1:2.41.5-0+deb13u1",
    f"{_PRESENT_GHSA}    # HIGH  msgpack vendored in pip",
    f"{_PRESENT_TWICE}         # HIGH  setuptools vendored in pip",
]
CLEAN_IGN, _clean_at = ignore_file("clean.trivyignore", _clean_lines)

_clean = run(CLEAN_IGN, REPORT, WORKFLOW)
check("3a a table whose every id is in the report exits 0", _clean.rc, 0)
check_true("3b ...and says PASS", "PASS: all 3 accepted entr" in _clean.out)
check("3c ...with all three under STILL PRESENT",
      header_line(_clean.out, "STILL PRESENT"),
      "STILL PRESENT — the entry describes something that is in the image: 3")
check("3d ...and no STALE section at all",
      sect(_clean.out, "STALE ACCEPTED ENTRIES"), None)
check("3e ...and no UNREADABLE section",
      sect(_clean.out, "UNREADABLE LINES"), None)
check("3f ...and no DUPLICATED section",
      sect(_clean.out, "DUPLICATED IDS"), None)
check_true("3g the two-row id is reported as two rows, so the row count is "
           "real rather than one-per-entry",
           f"{_PRESENT_TWICE:22s}   2 row(s)" in _clean.out)
check_true("3h ...and names both packages it was found under",
           "python3-setuptools" in _clean.out and "setuptools" in _clean.out)
check_true("3i the comment on an id line is stripped rather than parsed",
           sect(_clean.out, "UNREADABLE LINES") is None)
check_true("3j the report's ArtifactName is echoed, so a reader can see WHICH "
           "image was scanned",
           "artifact    : clinical-trial-patient-match:latest" in _clean.out)

# CONTROL for 3a/3d: the identical table with ONE id swapped for one the
# report does not carry. Without this, 3a is satisfied by a script that exits 0
# unconditionally.
_ctl_lines = list(_clean_lines)
_ctl_lines[2] = f"{_ABSENT_ID}   # HIGH   fixed 1:2.41.5-0+deb13u1"
CTL_IGN, _ctl_at = ignore_file("control-one-stale.trivyignore", _ctl_lines)
_ctl = run(CTL_IGN, REPORT, WORKFLOW)
check("3k CONTROL: the same table with one id absent from the report exits 2",
      _ctl.rc, 2)
check_true("3l ...and the STALE section exists, so 3d was not passing for "
           "want of a section that is never printed",
           sect(_ctl.out, "STALE ACCEPTED ENTRIES") is not None)


# ===========================================================================
# SECTION 4 -- STALENESS NAMES THE ENTRY AND ITS LINE
# ===========================================================================
section("SECTION 4 -- a stale entry: exit 2, named, by line")

_stale_body = sect(_ctl.out, "STALE ACCEPTED ENTRIES")
check("4a the header carries the count",
      header_line(_ctl.out, "STALE ACCEPTED ENTRIES"),
      "STALE ACCEPTED ENTRIES: 1")
check_true("4b the stale id is named in the STALE section",
           _ABSENT_ID in (_stale_body or ""))
check_true("4c ...with its line number, so an editor can be pointed at it",
           f"line {_ctl_at[_ctl_lines[2]]:4d}  {_ABSENT_ID}" in (_stale_body or ""))
check_true("4d ...and the raw line, comment included, so the argument that was "
           "attached to it is in front of whoever deletes it",
           "# HIGH   fixed 1:2.41.5-0+deb13u1" in (_stale_body or ""))
check_true("4e the two ids that ARE present are not named as stale",
           _PRESENT_GHSA not in (_stale_body or "")
           and _PRESENT_TWICE not in (_stale_body or ""))
check_true("4f ...and they are still reported as present",
           _PRESENT_GHSA in (sect(_ctl.out, "STILL PRESENT") or ""))
check_true("4g the final line reports the three failure classes separately",
           "1 stale, 0 unreadable, 0 duplicated" in _ctl.out)

# CONTROL for 4b/4c: the same id, present. `sect()` exists precisely because a
# bare `_ABSENT_ID in out` would be satisfied by the STILL PRESENT listing, and
# this pair is what demonstrates the extractor is doing that work.
check_true("4h CONTROL: with every id present there is no STALE body to find "
           "the id in",
           sect(_clean.out, "STALE ACCEPTED ENTRIES") is None)
check_true("4i ...although the ids themselves ARE in the output, which is why "
           "the assertions above read a section rather than the whole text",
           _PRESENT_GHSA in _clean.out)


# ===========================================================================
# SECTION 5 -- EVERY ENTRY STALE IS A DIFFERENT DIAGNOSIS
# ===========================================================================
section("SECTION 5 -- all-stale prints the systematic-fault paragraph")

ALL_STALE_IGN, _as_at = ignore_file(
    "all-stale.trivyignore", [_ABSENT_ID, _ABSENT_ID_2])
_all_stale = run(ALL_STALE_IGN, REPORT, WORKFLOW)
check("5a a table whose every entry is stale exits 2", _all_stale.rc, 2)
check("5b ...and the count is both of them",
      header_line(_all_stale.out, "STALE ACCEPTED ENTRIES"),
      "STALE ACCEPTED ENTRIES: 2")
_as_body = sect(_all_stale.out, "STALE ACCEPTED ENTRIES") or ""
check_true("5c the systematic-fault paragraph is printed",
           "EVERY entry is stale" in _as_body)
check_true("5d ...and it says why that is more likely one fault than N "
           "dependency moves",
           "more likely one fault in" in _as_body)
check_true("5e ...and names the three things to check: the image, the "
           "platform, and the missing --ignorefile",
           "make build" in _as_body and "platform CI scans" in _as_body
           and "WITHOUT" in _as_body and "--ignorefile" in _as_body)
check_true("5f STILL PRESENT is printed as empty rather than omitted, so the "
           "zero is visible",
           (sect(_all_stale.out, "STILL PRESENT") or "").strip() == "(none)")

# CONTROL: SOME stale, not all. Without this pair, 5c is satisfied by a script
# that prints the paragraph on every failing run -- which would send a human to
# re-check the image every time one dependency legitimately moved.
check("5g CONTROL: one-of-three stale also exits 2", _ctl.rc, 2)
check_true("5h ...and does NOT print the systematic-fault paragraph",
           "EVERY entry is stale" not in (_stale_body or ""))
check_true("5i ...so 5c is about all-stale specifically and not about failure",
           "EVERY entry is stale" not in _ctl.out)


# ===========================================================================
# SECTION 6 -- UNREADABLE LINES
# ===========================================================================
section("SECTION 6 -- a line the parser cannot read is reported, never skipped")

_PROSE = "accepted because upstream has not shipped a fix"
_TRAILING = f"{_PRESENT_GHSA} probably"
_bad_lines = [
    f"{_PRESENT_HIGH_FIXED}   # fine",
    _PROSE,
    _TRAILING,
]
BAD_IGN, _bad_at = ignore_file("unreadable.trivyignore", _bad_lines)
_bad = run(BAD_IGN, REPORT, WORKFLOW)

check("6a a table with unreadable lines exits 2", _bad.rc, 2)
check("6b ...and both are counted",
      header_line(_bad.out, "UNREADABLE LINES"), "UNREADABLE LINES: 2")
_bad_body = sect(_bad.out, "UNREADABLE LINES") or ""
check_true("6c prose is reported by line number",
           f"line {_bad_at[_PROSE]}:" in _bad_body)
check_true("6d ...naming the token it tried to read as an id",
           "'accepted' is not a vulnerability id" in _bad_body)
check_true("6e ...and echoing the line, so it can be found",
           repr(_PROSE) in _bad_body)
check_true("6f a trailing token on an otherwise-good id line is reported",
           f"line {_bad_at[_TRAILING]}:" in _bad_body)
check_true("6g ...named, and explained as neither an expiry nor a comment",
           "trailing token 'probably' is neither an expiry" in _bad_body)
check_true("6h the section explains the two repairs (a '#' or a fix)",
           "needs a leading '#'" in _bad_body)
check_true("6i the good id on the first line is still reported present, so an "
           "unreadable line does not abandon the rest of the file",
           _PRESENT_HIGH_FIXED in (sect(_bad.out, "STILL PRESENT") or ""))
check_true("6j the id whose line was unreadable is NOT counted as an entry, "
           "and so is not reported present either",
           _PRESENT_GHSA not in (sect(_bad.out, "STILL PRESENT") or ""))
check_true("6k ...nor as stale, which would tell a human to delete a line "
           "that was never an entry",
           _PRESENT_GHSA not in (sect(_bad.out, "STALE ACCEPTED ENTRIES") or ""))

# CONTROL for 6a: the identical file with the prose commented out and the
# trailing token removed. Without it, 6a is satisfied by a script that exits 2
# for any of three reasons.
_ok_lines = [f"{_PRESENT_HIGH_FIXED}   # fine", f"# {_PROSE}", _PRESENT_GHSA]
OK_IGN, _ = ignore_file("readable.trivyignore", _ok_lines)
_ok = run(OK_IGN, REPORT, WORKFLOW)
check("6l CONTROL: the same lines, one commented and one detrailed, exit 0",
      _ok.rc, 0)
check("6m ...with no UNREADABLE section at all",
      sect(_ok.out, "UNREADABLE LINES"), None)


# ===========================================================================
# SECTION 6b -- UNREADABLE LINES SURVIVE AN UNUSABLE REPORT
# ===========================================================================
section("SECTION 6b -- the file's own defect is printed before the scan is read")

# THE BRIEF THIS FILE WAS WRITTEN FROM SAID "unreadable lines exit 2 and are
# printed even alongside a degenerate report". THE FIRST HALF IS NOT WHAT THE
# SCRIPT DOES, measured rather than argued: `main()` returns 1 from the
# degenerate-report guard, which sits ABOVE the final `if status == 2`. So the
# exit code is 1 and the UNREADABLE section is still printed.
#
# That is the RIGHT behaviour and the assertions are written to it rather than
# to the brief. The two exit codes are two different instructions: 2 says "the
# accepted table needs a human", 1 says "the comparison could not be made". A
# run that could not read the scan has not established that anything is stale,
# and returning 2 would tell a human to go and edit the table on the strength
# of a scan of nothing -- which is the exact failure the degenerate-report
# guard exists to prevent. What matters, and is asserted, is that the file's
# own defect is REPORTED anyway, because it needs no report to be true.
EMPTY_REPORT = write_json("empty-rows.json",
                          {"ArtifactName": "x", "Results": [{"Target": "t"}]})
_bad_empty = run(BAD_IGN, EMPTY_REPORT, WORKFLOW)
check("6n unreadable lines + a zero-row report exits 1, NOT 2: the comparison "
      "could not be made, so no claim about the table is available",
      _bad_empty.rc, 1)
check("6o ...and the UNREADABLE section is printed anyway",
      header_line(_bad_empty.out, "UNREADABLE LINES"), "UNREADABLE LINES: 2")
# `str.index` RAISES when the needle is absent, and the needle here is exactly
# what a defect would remove -- so a bare pair of index() calls inside check()'s
# argument list would kill the file with no summary at the moment it had
# something to report. `_at` is the same repair
# tests/test_dashboard_reproducibility_tab.py had to make.
def _at(text, needle):
    return text.find(needle)


_at_unreadable = _at(_bad_empty.out, "UNREADABLE LINES")
_at_fatal = _at(_bad_empty.out, "no vulnerability rows at all")
check_true("6p ...above the report's own FATAL, so it is not buried",
           0 <= _at_unreadable < _at_fatal)
check_true("6p(ii) ...and both markers were actually found, so 6p is not "
           "comparing two absences",
           _at_unreadable >= 0 and _at_fatal >= 0)
check_true("6q ...and nothing is reported as stale, because nothing could be",
           sect(_bad_empty.out, "STALE ACCEPTED ENTRIES") is None)

_bad_missing = run(BAD_IGN, os.path.join(_TMP, "does-not-exist.json"), WORKFLOW)
check("6r unreadable lines + a MISSING report also exits 1", _bad_missing.rc, 1)
check("6s ...and still prints the UNREADABLE section",
      header_line(_bad_missing.out, "UNREADABLE LINES"), "UNREADABLE LINES: 2")

# CONTROL for 6n/6r: the SAME unreadable file against the GOOD report. This is
# what makes the exit code above a statement about the report rather than about
# the script having stopped distinguishing 1 from 2.
check("6t CONTROL: the same unreadable file with a usable report exits 2",
      _bad.rc, 2)


# ===========================================================================
# SECTION 7 -- DUPLICATED IDS
# ===========================================================================
section("SECTION 7 -- the same id twice")

_dup_lines = [
    f"{_PRESENT_HIGH_FIXED}   # first, with the argument",
    _PRESENT_GHSA,
    f"{_PRESENT_HIGH_FIXED}   # second, added by someone who did not grep",
]
DUP_IGN, _dup_at = ignore_file("duplicate.trivyignore", _dup_lines)
_dup = run(DUP_IGN, REPORT, WORKFLOW)

check("7a a duplicated id exits 2", _dup.rc, 2)
check("7b ...and is counted",
      header_line(_dup.out, "DUPLICATED IDS"), "DUPLICATED IDS: 1")
_dup_body = sect(_dup.out, "DUPLICATED IDS") or ""
check_true("7c the duplicate names both line numbers, not just its own",
           f"{_PRESENT_HIGH_FIXED} on line {_dup_at[_dup_lines[2]]}, "
           f"already on line {_dup_at[_dup_lines[0]]}" in _dup_body)
check_true("7d ...and explains why deleting one is not enough",
           "ONE LINE PER CVE" in _dup_body)
check_true("7e the id is present, so a duplicate is not also reported stale",
           sect(_dup.out, "STALE ACCEPTED ENTRIES") is None)
check_true("7f both occurrences are counted as entries, so the STILL PRESENT "
           "total is 3 for two distinct ids",
           "in the image: 3" in _dup.out)
check_true("7g the final line separates duplication from staleness",
           "0 stale, 0 unreadable, 1 duplicated" in _dup.out)

# CONTROL: the same file with the third line changed to a DIFFERENT present id.
_nodup_lines = list(_dup_lines)
_nodup_lines[2] = f"{_PRESENT_TWICE}   # a different id entirely"
NODUP_IGN, _ = ignore_file("no-duplicate.trivyignore", _nodup_lines)
_nodup = run(NODUP_IGN, REPORT, WORKFLOW)
check("7h CONTROL: three distinct present ids exit 0", _nodup.rc, 0)
check("7i ...with no DUPLICATED section",
      sect(_nodup.out, "DUPLICATED IDS"), None)


# ===========================================================================
# SECTION 8 -- THE EXPIRY FORM PARSES
# ===========================================================================
section("SECTION 8 -- exp:YYYY-MM-DD is an entry, not an unreadable line")

# No entry in `.trivyignore` uses this today. It is parsed anyway so that the
# FIRST one written does not land in the unreadable list and get argued about
# instead of read -- the script says so at `_EXP_RE`, and this is the standing
# form of that claim.
#
# The dates are deliberately decades away from any plausible run date. A
# fixture that expires while the repository is still alive is a test that turns
# red for the calendar rather than for a defect.
_PAST, _FUTURE = "2020-01-01", "2099-12-31"
_exp_lines = [
    f"{_PRESENT_HIGH_FIXED} exp:{_PAST}      # long gone",
    f"{_PRESENT_GHSA} exp:{_FUTURE}   # still running",
    _PRESENT_TWICE,
]
EXP_IGN, _exp_at = ignore_file("expiry.trivyignore", _exp_lines)
_exp = run(EXP_IGN, REPORT, WORKFLOW)

check("8a an expiry token does not make the line unreadable",
      sect(_exp.out, "UNREADABLE LINES"), None)
check("8b ...and the entries are counted as entries",
      header_line(_exp.out, "STILL PRESENT"),
      "STILL PRESENT — the entry describes something that is in the image: 3")
check_true("8c the EXPIRING ENTRIES section is printed",
           sect(_exp.out, "EXPIRING ENTRIES") is not None)
check_true("8d ...with 2 of 3 carrying an expiry and 1 already expired",
           (header_line(_exp.out, "EXPIRING ENTRIES") or "").startswith(
               "EXPIRING ENTRIES: 2 (1 already expired, as of "))
_exp_body = sect(_exp.out, "EXPIRING ENTRIES") or ""
check_true("8e the past date is marked EXPIRED",
           f"{_PRESENT_HIGH_FIXED:22s} exp:{_PAST}  EXPIRED" in _exp_body)
check_true("8f the future date is marked active",
           f"{_PRESENT_GHSA:22s} exp:{_FUTURE}  active" in _exp_body)
check_true("8g the entry with no expiry is not listed there",
           _PRESENT_TWICE not in _exp_body)
check("8h AN EXPIRED ENTRY DOES NOT CHANGE THE EXIT CODE. It is reporting "
      "only: Trivy stops honouring the line silently, which is a fact a human "
      "needs, but the entry still DESCRIBES something in the image and this "
      "script gates on staleness alone",
      _exp.rc, 0)

# CONTROL for 8a: the same tokens with the `exp:` prefix removed are trailing
# garbage and must be reported. Without this, 8a is satisfied by a parser that
# ignores every trailing token.
_noexp_lines = [f"{_PRESENT_HIGH_FIXED} {_PAST}", _PRESENT_GHSA]
NOEXP_IGN, _noexp_at = ignore_file("bare-date.trivyignore", _noexp_lines)
_noexp = run(NOEXP_IGN, REPORT, WORKFLOW)
check("8i CONTROL: the same date without `exp:` is an unreadable line",
      header_line(_noexp.out, "UNREADABLE LINES"), "UNREADABLE LINES: 1")
check_true("8j ...named as a trailing token rather than silently dropped",
           f"trailing token '{_PAST}' is neither an expiry"
           in (sect(_noexp.out, "UNREADABLE LINES") or ""))
check("8k ...and it exits 2", _noexp.rc, 2)

# CONTROL for 8c: a table with no expiry anywhere prints no section at all,
# rather than an empty one. 8c would otherwise pass against a section that is
# always printed.
check("8l CONTROL: with no expiry anywhere the section is absent entirely",
      sect(_clean.out, "EXPIRING ENTRIES"), None)


# ===========================================================================
# SECTION 9 -- THE REPORT IS UNUSABLE: EVERY PATH EXITS 1
# ===========================================================================
section("SECTION 9 -- an unusable report exits 1 and accuses nobody")

# 1 IS NOT 2, AND THE DIFFERENCE IS THE WHOLE DESIGN. Exit 2 says "the accepted
# table needs a human". Exit 1 says "the comparison could not be made". Every
# scenario here would make EVERY accepted entry look stale if the guards were
# missing, and the failure message would tell a human to delete the file.
_MALFORMED = write("malformed.json", '{"Results": [ {"Target": "t", ')
_LIST_JSON = write_json("a-list.json", [{"Results": []}])
_NO_RESULTS = write_json("no-results.json", {"ArtifactName": "x", "hits": []})
_MISSING = os.path.join(_TMP, "absent-report.json")

_cases = [
    ("9a absent report", _MISSING, "no Trivy report at"),
    ("9b malformed JSON", _MALFORMED, "is not JSON"),
    ("9c JSON that is a list, not a Trivy report", _LIST_JSON,
     "is not a Trivy report: no 'Results' key"),
    ("9d a JSON object with no Results key", _NO_RESULTS,
     "is not a Trivy report: no 'Results' key"),
    ("9e Results present but no vulnerability rows at all", EMPTY_REPORT,
     "no vulnerability rows at all"),
]
for _label, _path, _needle in _cases:
    _r = run(CLEAN_IGN, _path, WORKFLOW)
    check(f"{_label} exits 1", _r.rc, 1)
    check_true(f"{_label} ...and says why: {_needle!r}", _needle in _r.out)
    check(f"{_label} ...and prints no STALE section, so a bad report never "
          f"accuses the table",
          sect(_r.out, "STALE ACCEPTED ENTRIES"), None)

check_true("9f the JSON-that-is-a-list message names the type it did parse as, "
           "so a reader knows the file was readable",
           "(list)" in run(CLEAN_IGN, _LIST_JSON, WORKFLOW).out)
check_true("9g the zero-row message lists the three usual causes",
           all(s in run(CLEAN_IGN, EMPTY_REPORT, WORKFLOW).out
               for s in ("wrong image was scanned", "WITH --ignorefile",
                         "pruned before the scan")))

_no_ign = run(os.path.join(_TMP, "absent-ignorefile"), REPORT, WORKFLOW)
check("9h an absent ignore file exits 1", _no_ign.rc, 1)
check_true("9i ...naming the path it looked at", "no ignore file at" in _no_ign.out)

# CONTROL for the whole section: the same ignore file with a usable report.
# Without it, every `rc == 1` above is satisfied by a script that cannot run.
check("9j CONTROL: the same table and a usable report exit 0", _clean.rc, 0)
check_true("9k ...so the exit 1s above are about the report, not about the "
           "table or the environment",
           "PASS: all 3 accepted entr" in _clean.out)


# ===========================================================================
# SECTION 10 -- INERT IS REPORTED AND NEVER GATED ON
# ===========================================================================
section("SECTION 10 -- present but suppressing nothing at the gate")

_inert_lines = [
    _PRESENT_HIGH_FIXED,       # HIGH + a fix          -> not inert
    _PRESENT_NO_FIX,           # HIGH, no fix          -> inert (fix)
    _PRESENT_MEDIUM_FIXED,     # MEDIUM + a fix        -> inert (severity)
    _PRESENT_MEDIUM_NO_FIX,    # MEDIUM, no fix        -> inert (both)
]
INERT_IGN, _ = ignore_file("inert.trivyignore", _inert_lines)
_inert = run(INERT_IGN, REPORT, WORKFLOW)
_inert_body = sect(_inert.out, "INERT") or ""

check("10a an inert entry does NOT change the exit code", _inert.rc, 0)
check("10b three of the four are inert",
      header_line(_inert.out, "INERT"),
      "INERT — present, but suppressing nothing at the gate today: 3")
check_true("10c the HIGH-with-a-fix entry is not inert",
           _PRESENT_HIGH_FIXED not in _inert_body)
check_true("10d a HIGH with no published fix is inert for that reason alone",
           f"{_PRESENT_NO_FIX:22s} no fixed version published" in _inert_body)
check_true("10e a MEDIUM with a fix is inert for the severity reason alone",
           f"{_PRESENT_MEDIUM_FIXED:22s} no row at CRITICAL/HIGH" in _inert_body)
check_true("10f a MEDIUM with no fix reports BOTH reasons, in that order",
           f"{_PRESENT_MEDIUM_NO_FIX:22s} no row at CRITICAL/HIGH; "
           f"no fixed version published" in _inert_body)
check_true("10g the section says in words that it is not gated on",
           "Reported, NOT gated on" in _inert.out)
check_true("10h ...and the PASS line repeats the inert count, so the number is "
           "on the last screen a reader sees",
           "3 of them suppress nothing at the gate today" in _inert.out)

# CONTROL: move the gate to MEDIUM. The MEDIUM-with-a-fix entry must leave the
# INERT list; the two with no fix must stay. Without this the whole section is
# satisfied by a script that computed INERT from a constant rather than from
# the severity it printed.
_inert_med = run(INERT_IGN, REPORT, WORKFLOW, gate_severity="MEDIUM")
_med_body = sect(_inert_med.out, "INERT") or ""
# STILL THREE, AND THE MEMBERSHIP IS WHAT MOVED -- which is a sharper control
# than a changed count would have been. The MEDIUM-with-a-fix entry leaves the
# list and the HIGH-with-a-fix entry joins it, so a script that had stopped
# consulting the severity would fail 10j and 10k even though 10i still held.
# The first version of this check expected 2 and was simply wrong arithmetic;
# it is recorded because the failure is what established the membership.
check("10i CONTROL: at --gate-severity MEDIUM three are inert again -- a "
      "different three",
      header_line(_inert_med.out, "INERT"),
      "INERT — present, but suppressing nothing at the gate today: 3")
check_true("10j ...the MEDIUM-with-a-fix entry is no longer inert",
           _PRESENT_MEDIUM_FIXED not in _med_body)
check_true("10k ...the HIGH-with-a-fix entry becomes inert, because HIGH is "
           "not in the gate any more -- so the value is genuinely consulted "
           "in both directions",
           f"{_PRESENT_HIGH_FIXED:22s} no row at MEDIUM" in _med_body)
check("10l ...and it still exits 0, because INERT gates on nothing",
      _inert_med.rc, 0)


# ===========================================================================
# SECTION 11 -- THE GATE SEVERITY IS DERIVED FROM THE WORKFLOW
# ===========================================================================
section("SECTION 11 -- where the printed gate severity comes from")

def gate_line(**kwargs):
    return header_line(run(**kwargs).out, "gate severity:")


check("11a a workflow carrying the marker is read",
      gate_line(ignorefile=CLEAN_IGN, report=REPORT, workflow=WORKFLOW),
      f"gate severity: HIGH,CRITICAL  (from derived from {WORKFLOW})")

# CONTROL for 11a: a second workflow with a DIFFERENT value. Without it, 11a is
# satisfied by a script printing the `_FALLBACK_GATE_SEVERITY` constant, which
# happens to be the same string.
_WF_LOW = write("low-workflow.yml", workflow_text("LOW,MEDIUM,HIGH,CRITICAL"))
check("11b CONTROL: a different --severity in the workflow is what gets "
      "printed, so 11a is not reading the fallback constant",
      gate_line(ignorefile=CLEAN_IGN, report=REPORT, workflow=_WF_LOW),
      f"gate severity: LOW,MEDIUM,HIGH,CRITICAL  (from derived from {_WF_LOW})")

_WF_NO_MARKER = write("no-marker.yml",
                      "      - name: Trivy image scan\n"
                      "        run: trivy image --severity HIGH,CRITICAL x\n")
check("11c a workflow with no step mounting the ignore file falls back, and "
      "SAYS it fell back",
      gate_line(ignorefile=CLEAN_IGN, report=REPORT, workflow=_WF_NO_MARKER),
      f"gate severity: HIGH,CRITICAL  (from fallback, no step in "
      f"{_WF_NO_MARKER} mounts the ignore file)")

_WF_ABSENT = os.path.join(_TMP, "no-such-workflow.yml")
_absent_line = gate_line(ignorefile=CLEAN_IGN, report=REPORT,
                         workflow=_WF_ABSENT) or ""
check_true("11d an absent workflow falls back and names the file",
           _absent_line.startswith(
               f"gate severity: HIGH,CRITICAL  (from fallback, {_WF_ABSENT} "
               f"unreadable:"))
check("11e a failure to derive the severity does not change the exit code -- "
      "it is reporting only",
      run(CLEAN_IGN, REPORT, _WF_ABSENT).rc, 0)

# The `--severity` BELOW the marker is the one shape that looks right and is
# not: derive_gate_severity reads the 1200 characters that PRECEDE the marker.
_WF_AFTER = write("severity-after.yml",
                  "        run: docker run trivy image \\\n"
                  "              --ignorefile /tmp/.trivyignore \\\n"
                  "              --severity LOW,MEDIUM \\\n"
                  "              image:latest\n")
check("11f a --severity BELOW the marker is not found, and the fallback says "
      "which half of the line it searched",
      gate_line(ignorefile=CLEAN_IGN, report=REPORT, workflow=_WF_AFTER),
      "gate severity: HIGH,CRITICAL  (from fallback, no --severity found "
      "above the gate's ignorefile)")

check("11g --gate-severity outranks the workflow and says so",
      gate_line(ignorefile=CLEAN_IGN, report=REPORT, workflow=_WF_LOW,
                gate_severity="CRITICAL"),
      "gate severity: CRITICAL  (from --gate-severity)")


# ===========================================================================
# SECTION 12 -- THE COMMITTED .trivyignore AND ci.yml, AS THEY STAND
# ===========================================================================
section("SECTION 12 -- the two real files this gate is aimed at")

# THE ONLY TWO CLAIMS ABOUT REAL FILES THIS FILE CAN MAKE OFFLINE, and both are
# worth making because CI's own staleness step needs an image and a 20-minute
# scan before it can say anything at all -- so a prose line committed into
# `.trivyignore` would sit undetected until the next scheduled scan.
#
# WHAT IS *NOT* CHECKABLE HERE, stated rather than glossed: staleness and
# duplication both need the report. `load_report` exits 1 before either is
# computed, so a run with no report reaches the unreadable-line check and
# nothing beyond it. Deriving the ids from the real file to fabricate a
# matching report would mean re-implementing `parse_ignorefile` here, and a
# second parser is a second parser however carefully it is written.
_real = run(report=os.path.join(_TMP, "no-report-at-all.json"))
check("12a the committed .trivyignore is read with the default resolution",
      header_line(_real.out, "ignore file : "),
      f"ignore file : {_REAL_IGNOREFILE}")
check("12b it exits 1 for want of a report, not 2 for want of a human",
      _real.rc, 1)
check("12c THE COMMITTED .trivyignore HAS NO UNREADABLE LINE. Every entry in "
      "it is visible to staleness; a line this parser skipped would be "
      "invisible there forever",
      sect(_real.out, "UNREADABLE LINES"), None)

# NON-DEGENERACY for 12c: the check above is `is None`, which is also what an
# empty file produces, and an empty accepted table would pass it while proving
# nothing. So the file is required to be non-empty -- measured through the
# script rather than by parsing it here.
_real_entries = run(ignorefile=_REAL_IGNOREFILE, report=REPORT,
                    workflow=WORKFLOW)
check_true("12d ...and it is non-degenerate: the real file holds entries, so "
           "12c is a statement about lines that exist",
           " accepted entr(y/ies), " in _real_entries.out
           and not _real_entries.out.startswith("0 accepted"))
check_true("12e (those entries are all stale against the MINIATURE report, "
           "which is expected and is exactly why the real check needs a real "
           "scan -- recorded here so the exit code below is not read as a "
           "finding about the accepted table)",
           _real_entries.rc == 2)

check("12f the REAL ci.yml still carries a step that mounts the ignore file, "
      "so the gate severity is derived rather than guessed",
      gate_line(ignorefile=CLEAN_IGN, report=REPORT, workflow=_REAL_WORKFLOW),
      f"gate severity: HIGH,CRITICAL  (from derived from {_REAL_WORKFLOW})")


# ===========================================================================
# SECTION 13 -- THE ID SHAPES THE PARSER ADMITS
# ===========================================================================
section("SECTION 13 -- what counts as a vulnerability id")

# The script's `_ID_RE` is deliberately a SHAPE rather than a list of known
# prefixes, and it argues why: this file holds CVE-* and GHSA-*, the pip-audit
# gate holds PYSEC-*, and Trivy emits DLA-*, DSA-*, RUSTSEC-*, TEMP-* and
# vendor ids besides. A prefix list would report a real id as UNREADABLE and
# fail CI for a defect that is not there.
#
# EVERY ID BELOW IS ABSENT FROM THE MINIATURE REPORT, which makes this section
# read the STALE list rather than the STILL PRESENT one -- and that is the
# stronger place to read it from. An id the parser silently DROPPED would
# appear in neither list, so "it is named as stale" is a positive statement
# that the line became an entry, where "it is not in UNREADABLE" would also be
# true of a line that vanished.
_SHAPES_OK = [
    "CVE-2026-11111",              # the ordinary case
    "GHSA-6v7p-g79w-9999",         # three hyphen groups, lower-case body
    "RUSTSEC-2021-0079",           # a third prefix entirely
    "DLA-3702-1",                  # Debian LTS
    "DSA-6442-1",                  # Debian security advisory
    "PYSEC-2024-48",               # the pip-audit gate's own form
    "TEMP-0841856-B18BAF",         # Debian's placeholder form
    "DS002",                       # the compact form, no hyphen at all
    "CVE-2026-1234.5",             # a dot is admitted inside a group
]
SHAPES_IGN, _shape_at = ignore_file(
    "shapes.trivyignore", _SHAPES_OK)
_shapes = run(SHAPES_IGN, REPORT, WORKFLOW)
check("13a every admitted shape parses, so none is reported unreadable",
      sect(_shapes.out, "UNREADABLE LINES"), None)
check("13b ...and all of them became entries",
      header_line(_shapes.out, "STALE ACCEPTED ENTRIES"),
      f"STALE ACCEPTED ENTRIES: {len(_SHAPES_OK)}")
_shape_body = sect(_shapes.out, "STALE ACCEPTED ENTRIES") or ""
for _vid in _SHAPES_OK:
    check_true(f"13c {_vid} is carried through as an entry", _vid in _shape_body)

# CONTROL: shapes that must NOT be read as ids. Each would otherwise be
# reported STALE, telling a human to delete a line that was never an entry --
# which the script's own comment names as the wrong way to err.
_SHAPES_BAD = [
    "glibc",                       # a package name, not an id
    "12345",                       # a bare number: the compact form needs a letter first
    "accepted",                    # prose
    "CVE-2026-11111 CVE-2026-22222",   # two ids on one line
]
BADSHAPES_IGN, _bad_shape_at = ignore_file("bad-shapes.trivyignore", _SHAPES_BAD)
_bad_shapes = run(BADSHAPES_IGN, REPORT, WORKFLOW)
check("13d CONTROL: four non-id shapes are all reported unreadable",
      header_line(_bad_shapes.out, "UNREADABLE LINES"),
      f"UNREADABLE LINES: {len(_SHAPES_BAD)}")
check("13e ...and NONE of them is reported stale, which is the direction the "
      "script chooses to err in",
      sect(_bad_shapes.out, "STALE ACCEPTED ENTRIES"), None)
check_true("13f two ids on one line is reported as a trailing token rather "
           "than half-read",
           "trailing token 'CVE-2026-22222'"
           in (sect(_bad_shapes.out, "UNREADABLE LINES") or ""))
check("13g ...and it exits 2", _bad_shapes.rc, 2)

# THIS CHECK USED TO PIN THE OPPOSITE, AND THAT IS THE POINT OF IT. `_ID_RE`
# began `[A-Za-z]`, so a hyphenated LOWER-CASE phrase -- which is what a
# half-written note looks like -- satisfied it, became an ENTRY, matched
# nothing in the scan and was reported STALE. 13h recorded that as a measured
# looseness rather than a desire. The regex now requires the first group to be
# `[A-Z][A-Z0-9]*`, so such a line is UNREADABLE: it names itself and is fixed
# in one edit, instead of sending a human to delete a line that was never an
# entry. That is the direction the script's own comment says it must err in,
# and 13e above is the general form of it.
_LOOKS_LIKE_ID = "not-an-id"
LOOSE_IGN, _ = ignore_file("loose.trivyignore", [_LOOKS_LIKE_ID])
_loose = run(LOOSE_IGN, REPORT, WORKFLOW)
check("13h a hyphenated lower-case phrase is UNREADABLE, not a phantom entry",
      header_line(_loose.out, "UNREADABLE LINES"), "UNREADABLE LINES: 1")
check_true("13h(ii) ...named as not-a-vulnerability-id rather than half-read",
           f"{_LOOKS_LIKE_ID!r} is not a vulnerability id"
           in (sect(_loose.out, "UNREADABLE LINES") or ""))
check("13h(iii) ...and it is NOT reported stale, so nobody is told to delete a "
      "line that was never an entry",
      sect(_loose.out, "STALE ACCEPTED ENTRIES"), None)
check("13h(iv) ...and the run exits 2, because an unreadable line is invisible "
      "to staleness forever", _loose.rc, 2)

# CONTROL: the same input against a COPY of the script with the first group
# widened back to `[A-Za-z]`. Without it, 13h is satisfied by any script that
# reports something unreadable, and the tightening it exists to hold could be
# reverted without a failure. The plant is a RECORDED failure rather than an
# exception, so a needle that stops matching fails here instead of killing the
# file -- the rule this project restates every time a control aborts a run.
_SCRIPT_SRC = open(_SCRIPT, encoding="utf-8").read()
_TIGHT = 'r"^(?:[A-Z][A-Z0-9]*(?:-[A-Za-z0-9.]+)+"'
_LOOSE_SRC = 'r"^(?:[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9.]+)+"'
check("13h(v) CONTROL PRECONDITION: the shipped script carries the tightened "
      "first group exactly once", _SCRIPT_SRC.count(_TIGHT), 1)
if _SCRIPT_SRC.count(_TIGHT) == 1:
    LOOSE_SCRIPT = write("reverted_id_re_staleness.py",
                         _SCRIPT_SRC.replace(_TIGHT, _LOOSE_SRC))
    _reverted = run(LOOSE_IGN, REPORT, WORKFLOW, script=LOOSE_SCRIPT)
    check_true("13h(vi) CONTROL: with the first group widened back to "
               "[A-Za-z], the same line becomes a phantom entry and is "
               "reported STALE",
               _LOOKS_LIKE_ID in (sect(_reverted.out,
                                       "STALE ACCEPTED ENTRIES") or ""))
    check("13h(vii) CONTROL: ...and is NOT reported unreadable there, so 13h "
          "is discriminating between the two regexes rather than restating "
          "something true of both",
          sect(_reverted.out, "UNREADABLE LINES"), None)
    check_true("13h(viii) CONTROL: the two runs genuinely disagree about this "
               "line, which is what makes 13h a test of the tightening",
               (sect(_loose.out, "UNREADABLE LINES") or "")
               != (sect(_reverted.out, "UNREADABLE LINES") or ""))
    check_true("13h(ix) ...and a REAL id parses identically under both, so the "
               "control changed only the case rule and not the id shape",
               run(CLEAN_IGN, REPORT, WORKFLOW, script=LOOSE_SCRIPT).rc
               == _clean.rc == 0)
else:
    check("13h(vi-ix) CONTROL COULD NOT BE BUILT: the tightened first group "
          "was not found in the shipped script, so four controls did not run",
          "unplantable", "planted")


# ===========================================================================
# SECTION 14 -- COMMENTS AND BLANK LINES
# ===========================================================================
section("SECTION 14 -- Trivy strips from the first '#', and so must this")

_comment_lines = [
    "# a heading",
    "",
    "   ",
    f"   {_PRESENT_HIGH_FIXED}   ",                       # leading/trailing space
    f"{_PRESENT_GHSA}# no space before the hash",
    f"{_PRESENT_TWICE} # HIGH  with an exp:2020-01-01 mentioned in PROSE",
]
COMMENT_IGN, _ = ignore_file("comments.trivyignore", _comment_lines)
_comments = run(COMMENT_IGN, REPORT, WORKFLOW)
check("14a comments, blanks and whitespace-only lines are skipped silently",
      sect(_comments.out, "UNREADABLE LINES"), None)
check("14b ...and the three ids are the three entries",
      header_line(_comments.out, "STILL PRESENT"),
      "STILL PRESENT — the entry describes something that is in the image: 3")
check("14c an expiry mentioned inside a COMMENT is not an expiry: the comment "
      "is stripped before the tokens are read",
      sect(_comments.out, "EXPIRING ENTRIES"), None)
check("14d ...and it exits 0", _comments.rc, 0)

# CONTROL for 14a: the same heading without its '#'.
_uncommented = list(_comment_lines)
_uncommented[0] = "a heading"
UNCOMMENT_IGN, _ = ignore_file("uncommented.trivyignore", _uncommented)
check("14e CONTROL: the same heading without a leading '#' is unreadable",
      header_line(run(UNCOMMENT_IGN, REPORT, WORKFLOW).out, "UNREADABLE LINES"),
      "UNREADABLE LINES: 1")


# ===========================================================================
# SECTION 15 -- HYGIENE: NOTHING IN THE REPOSITORY WAS TOUCHED
# ===========================================================================
section("SECTION 15 -- the three repository files this file reads are intact")

# Same shape as the newest suites: hash what is read, compare at the end, and
# assert the comparison is not None == None. This file writes no repository
# file at all -- which is the derivation that keeps it out of the collision
# matrix -- and these three hashes are what says so rather than claiming it.
_SHA_AFTER = {p: _sha256(p) for p in _SHA_BEFORE}
for _path in sorted(_SHA_BEFORE):
    _rel = os.path.relpath(_path, _CODE_DIR)
    check(f"15a {_rel} is byte-identical after the run",
          _SHA_AFTER[_path], _SHA_BEFORE[_path])
check_true("15b ...and those are real digests, not None on both sides",
           all(isinstance(v, str) and len(v) == 64
               for v in _SHA_BEFORE.values()))
check("15c three files were hashed, so the loop above is not empty",
      len(_SHA_BEFORE), 3)

check_true("15d every scenario wrote inside the temp directory only",
           all(os.path.dirname(p) == _TMP
               for p in (REPORT, CLEAN_IGN, WORKFLOW, BAD_IGN, DUP_IGN)))

_tmp_files = sorted(os.listdir(_TMP))
check_true("15e ...and there are files in it, so the previous check is not "
           "vacuous", len(_tmp_files) >= 10)

shutil.rmtree(_TMP, ignore_errors=True)
check("15f the temp directory is removed", os.path.isdir(_TMP), False)


# ===========================================================================
section("SUMMARY")
print(f"  passed: {_passed}")
print(f"  failed: {_failed}")

if __name__ == "__main__":
    sys.exit(1 if _failed else 0)


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug 20 2026

@author: ramyalsaffar
"""
