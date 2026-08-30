"""The secret gate fires, on both layers, on values that look like real secrets.

WHAT THIS FILE IS ABOUT
=======================
A gate that has never fired has established nothing. Two layers ship:

  LAYER 1  .githooks/pre-commit -- convenience. Bypassable with --no-verify,
           and absent until somebody runs `make hooks`.
  LAYER 2  the `secret-scan` job in .github/workflows/ci.yml -- the guarantee.

Both run the same script over different ranges, so a hook that passes is a true
preview of the gate rather than a different opinion. This file drives BOTH
against real git repositories built in a temp directory, requires each to
refuse a planted secret, and then requires each to pass once the plant is gone.

THE PLANTS ARE SHAPE-FAITHFUL AND ARE ASSEMBLED AT RUN TIME
-------------------------------------------------------------
Not one secret-shaped literal is written into this file, and that is not
squeamishness -- a tracked file carrying one is a finding of its own, and this
project has shipped exactly that mistake: the compose file's 56-character
Airflow key was reproduced into two documentation files while being removed
from the code, and the project's own scanner caught only one of the two because
of a punctuation accident. So the values here are BUILT: a prefix, an alphabet,
and an index arithmetic, none of which matches any detector on its own.

LOW-ENTROPY PLACEHOLDERS DO NOT WORK AND SECTION 1 MEASURES THAT. `AKIAFAKE...`,
`sk-FAKE...` and `hf_FAKE...` sail past BOTH engines, which is why the shapes
are taken from the engines' real patterns rather than guessed:

    aws-access-token          \\b((?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16})\\b
                              -- BASE32. No 0, 1, 8 or 9 anywhere. Entropy >= 3.
    openai-api-key            requires the literal infix T3BlbkFJ and one of two
                              exact lengths.
    huggingface-access-token  hf_ + exactly 34 LETTERS. A digit breaks it.

Those three were read out of gitleaks v8.30.1's own config/gitleaks.toml, not
inferred, and section 1 asserts each generated value against the ENGINE rather
than against a copy of the regex.

WHAT IT NEEDS, AND WHAT IT DOES WITHOUT
-----------------------------------------
No network, no keys, NO SPEND, no live Qdrant, no model load, no corpus, no
database, no Docker daemon, no live server. It needs `git`, which every runner
has, and it builds every repository it touches inside one tempfile.mkdtemp()
that it removes and asserts gone.

IT WANTS gitleaks AND DOES NOT REQUIRE IT. The two-engine half is SKIPPED --
counted, printed, never a pass -- when the binary is absent, which is the state
of the `tests` job on a hosted runner (CI installs gitleaks in the `secret-scan`
job, not this one). Everything about the project scanner, the fingerprint
format, the accepted table, the ranges and the hook still runs. The skip count
is printed even at zero, on tests/test_package_invariants.py's precedent.

NOT IN THE COLLISION MATRIX, derived rather than declared: it writes nothing in
the repository -- every repository it builds is inside one temp directory -- and
the four files it READS (.github/scripts/secret_scan_gate.py,
.github/scan-accepted-fingerprints.txt, .githooks/pre-commit and
oncotriage/staging/secrets_scan.py) are written by neither of the suite's two
writers. All four are sha256-compared at the end.

IT EXECS NOTHING and loads no module by location, so it needs no
_EXEC_ALLOWLIST entry in tests/test_package_invariants.py. It drives the SHIPPED
script as a subprocess (sys.executable plus its path), which is also what makes
the EXIT CODE the thing asserted -- 0/1/2/3 are four different instructions to a
human and an in-process call produces none of them.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile


_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)

# A HARD GUARD, NOT A check(). A wrong root here is not one failure, it is every
# failure with a misleading message -- the case this suite already reserves
# SystemExit for.
_GATE = os.path.join(_REPO_ROOT, ".github", "scripts", "secret_scan_gate.py")
_ACCEPTED = os.path.join(_REPO_ROOT, ".github", "scan-accepted-fingerprints.txt")
_HOOK = os.path.join(_REPO_ROOT, ".githooks", "pre-commit")
_SCANNER = os.path.join(_REPO_ROOT, "oncotriage", "staging", "secrets_scan.py")
for _required in (_GATE, _ACCEPTED, _HOOK, _SCANNER):
    if not os.path.exists(_required):
        raise SystemExit(f"cannot find {_required}; the repository root was "
                         f"derived as {_REPO_ROOT} and is wrong")


# CAPTURED AT IMPORT, ABOVE EVERY DRIVER. Section 9 compares against these. A
# capture taken next to the comparison would be `x == x` microseconds apart --
# the defect tests/test_storage_write_durability.py's 9c had, where a planted
# mid-run write left the check GREEN.
_WATCHED = (_GATE, _ACCEPTED, _HOOK, _SCANNER)


def _sha256_at_import(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


_BEFORE = {p: _sha256_at_import(p) for p in _WATCHED}


# ===========================================================================
# THE HARNESS
# ===========================================================================
_passed = 0
_failed = 0
_skipped = 0
_SKIPS = []


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


def skip(label, reason):
    """Coverage that could NOT be exercised here. NEVER counted as a pass."""
    global _skipped
    _skipped += 1
    _SKIPS.append((label, reason))
    print(f"  SKIP  {label}\n          {reason}")


def section(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Absent:
    """A named absence. FALSY, so `if result:` reads as "there is no result".

    Returned wherever an operation could raise or produce nothing, so a defect
    that stops something being produced is a RECORDED failure rather than an
    IndexError or a KeyError evaluated inside check()'s argument list -- the
    abort shape this project has now shipped more than a dozen times.
    """

    __slots__ = ("why",)

    def __init__(self, why):
        self.why = why

    def __bool__(self):
        return False

    def __repr__(self):
        return f"<absent: {self.why}>"


# ===========================================================================
# THE PLANTS -- ASSEMBLED, NEVER WRITTEN OUT
# ===========================================================================
# Each generator is a prefix plus an index arithmetic over an alphabet. None of
# the three parts matches any detector on its own, which is checked in section
# 8: this file is scanned by the very scanner it tests and must come back clean.
_B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"      # RFC 4648 base32, no 0/1/8/9
_ALPHA = "abcdefghijklmnopqrstuvwxyz"
_ALNUM = _ALPHA + "0123456789" + _ALPHA.upper()

# The eight characters gitleaks' openai-api-key rule requires between the two
# random runs, split so the literal never appears whole in this file.
_OPENAI_INFIX = "T3Blbk" + "FJ"


def make_aws(seed=3):
    """An AWS access key id: AKIA + 16 base32 characters."""
    return "AKIA" + "".join(_B32[(i * 7 + seed) % 32] for i in range(16))


def make_hf(seed=5):
    """A Hugging Face token: hf_ + exactly 34 LETTERS. A digit breaks the rule."""
    return "hf_" + "".join(_ALPHA[(i * 11 + seed) % 26] for i in range(34))


def make_openai(seed=7):
    """An OpenAI key: sk- + 20 alnum + the required infix + 20 alnum."""
    return ("sk-" + "".join(_ALNUM[(i * 13 + seed) % 62] for i in range(20))
            + _OPENAI_INFIX
            + "".join(_ALNUM[(i * 17 + 11) % 62] for i in range(20)))


def make_placeholder_aws():
    """The NEGATIVE control: a placeholder spelled FAKE. Must fire NOTHING."""
    return "AKIA" + "FAKE" * 4


def make_placeholder_openai():
    return "sk-" + "FAKE" * 8


def make_placeholder_hf():
    return "hf_" + "FAKE" * 8


def plant_text(seed=0):
    """Three assignments, one per generator. Bytes, ready to write."""
    return ("aws_access_key_id = %s\n"
            "hf_hub_token = %s\n"
            "openai_api_key = %s\n" % (make_aws(3 + seed), make_hf(5 + seed),
                                       make_openai(7 + seed))).encode()


# ===========================================================================
# ENGINES
# ===========================================================================
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from oncotriage.staging.secrets_scan import scan_bytes, scan_filename   # noqa: E402

GITLEAKS = os.environ.get("GITLEAKS_BIN") or shutil.which("gitleaks")
if GITLEAKS and not os.path.exists(GITLEAKS):
    GITLEAKS = None


def gitleaks_rules(blob):
    """Rule ids gitleaks reports for one byte string, or Absent."""
    if not GITLEAKS:
        return Absent("gitleaks is not installed")
    proc = subprocess.run([GITLEAKS, "stdin", "--no-banner", "--redact",
                           "--report-format", "json", "--report-path", "-",
                           "--exit-code", "0"],
                          input=blob, capture_output=True)
    import json
    try:
        payload = json.loads(proc.stdout.decode("utf-8", "replace") or "[]")
    except ValueError:
        return Absent(f"gitleaks produced no JSON (exit {proc.returncode})")
    return sorted({entry.get("RuleID", "?") for entry in payload})


# ===========================================================================
# SCRATCH REPOSITORIES
# ===========================================================================
_TMP = tempfile.mkdtemp(prefix="secret-scan-gate-test-")


def git(repo, *args, check_rc=True):
    proc = subprocess.run(["git", "-C", repo] + list(args),
                          capture_output=True, text=True)
    if check_rc and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc


def new_repo(name):
    """A tiny git repository with one commit. Nothing is cloned."""
    repo = os.path.join(_TMP, name)
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "gate-test@example.invalid")
    git(repo, "config", "user.name", "Gate Test")
    # commit.gpgsign off, so a developer machine that signs everything does not
    # turn every commit here into a passphrase prompt or a failure.
    git(repo, "config", "commit.gpgsign", "false")
    with open(os.path.join(repo, "README"), "w", encoding="utf-8") as handle:
        handle.write("nothing interesting\n")
    git(repo, "add", "README")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def empty_accepted(name):
    path = os.path.join(_TMP, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("# no accepted findings\n")
    return path


def run_gate(repo, scan_range, accepted, extra=()):
    """Drive the SHIPPED script as a subprocess. Returns (exit, stdout+stderr).

    PYTHONPATH carries the real repository root so the gate can import this
    project's scanner while `--repo` points somewhere else entirely. That is the
    same seam the hook uses from a checkout, and it is what lets these scratch
    repositories hold no copy of the package.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = _REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    if GITLEAKS:
        env["GITLEAKS_BIN"] = GITLEAKS
    proc = subprocess.run(
        [sys.executable, _GATE, "--repo", repo, "--range", scan_range,
         "--accepted", accepted] + list(extra),
        capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout + proc.stderr


# ===========================================================================
section("1  THE PLANTS ARE SHAPE-FAITHFUL, AND PLACEHOLDERS ARE NOT")
# ===========================================================================
# The point of this section is that the controls below cannot pass vacuously. A
# gate refusing a value no engine would ever have matched proves nothing.
_aws, _hf, _oa = make_aws(), make_hf(), make_openai()

check("1a the AWS id is 20 characters", len(_aws), 20)
check("1b ...and every character after AKIA is base32 (no 0/1/8/9)",
      all(ch in _B32 for ch in _aws[4:]), True)
check("1c the HF token is hf_ plus exactly 34 characters", len(_hf), 37)
check("1d ...all of them letters, because a digit breaks gitleaks' rule",
      _hf[3:].isalpha(), True)
check("1e the OpenAI key carries the required infix",
      _OPENAI_INFIX in _oa, True)
check("1f ...at the position the rule requires (3 + 20)",
      _oa.index(_OPENAI_INFIX), 23)

_pd = {detector for detector, _o, _l in
       scan_bytes(("k = %s\nk = %s\nk = %s\n" % (_aws, _hf, _oa)).encode())}
check("1g the project scanner detects all three shapes",
      sorted(_pd & {"aws_access_key_id", "huggingface_token",
                    "openai_anthropic_key"}),
      ["aws_access_key_id", "huggingface_token", "openai_anthropic_key"])

# THE BRIEF THIS PASS WAS BUILT FROM SAID "low-entropy placeholders sail past
# BOTH engines". MEASURED, THAT IS TRUE OF ONE OF THEM AND FALSE OF THE OTHER,
# and the asymmetry is worth more than the claim it corrects:
#
#     value                 project scanner        gitleaks
#     AKIA + FAKE*4         aws_access_key_id      (nothing)
#     sk-  + FAKE*8         openai_anthropic_key   (nothing)
#     hf_  + FAKE*8         huggingface_token      (nothing)
#     AWS's own docs key    aws_access_key_id      (nothing)
#
# gitleaks carries an entropy floor on every one of those rules (3, 3 and 2) and
# an explicit `.+EXAMPLE$` allowlist on the AWS one. This project's nine
# detectors carry NEITHER, deliberately: they are a REFUSAL layer for a staging
# upload, they err toward stopping the run, and that is exactly why they caught
# the digitless 56-character Airflow signing key that no gitleaks rule fires on.
#
# THE CONSEQUENCE FOR THE CONTROLS BELOW IS THE SAME EITHER WAY. A placeholder
# control would exercise ONE engine while looking like it exercised two -- the
# "satisfied for the wrong reason" shape -- so the plants stay shape-faithful.
_placeholders = ("k = %s\nk = %s\nk = %s\n"
                 % (make_placeholder_aws(), make_placeholder_hf(),
                    make_placeholder_openai())).encode()
_pp = {detector for detector, _o, _l in scan_bytes(_placeholders)}
check("1h the project scanner DOES fire on FAKE placeholders -- it has no "
      "entropy floor, by design",
      sorted(_pp & {"aws_access_key_id", "huggingface_token",
                    "openai_anthropic_key"}),
      ["aws_access_key_id", "huggingface_token", "openai_anthropic_key"])

if GITLEAKS:
    _gr = gitleaks_rules(("k = %s\nk = %s\nk = %s\n"
                          % (_aws, _hf, _oa)).encode())
    check("1i gitleaks detects all three SHAPE-FAITHFUL values", sorted(_gr),
          ["aws-access-token", "huggingface-access-token", "openai-api-key"])
    # THE PAIR 1h/1j IS THE MEASUREMENT. Each alone is a fact about one engine;
    # together they are the reason a placeholder cannot be used as a control.
    _gp = gitleaks_rules(_placeholders)
    check("1j ...and reports NOTHING for the same FAKE placeholders", _gp, [])
    # ASSEMBLED, NOT WRITTEN OUT, AND SECTION 8 IS WHAT FORCED THAT. The first
    # version of this check carried AWS's documented example key as a literal --
    # and 8a failed, because this project's scanner has no `.+EXAMPLE$`
    # allowlist and reported this very file. The guard caught its own author,
    # which is the whole reason it is in here.
    _aws_docs_example = "AKIA" + "IOSFODNN7" + "EXAMPLE"
    check("1k ...including AWS's own documentation example, which its rule "
          "allowlists outright",
          gitleaks_rules(("k = %s\n" % _aws_docs_example).encode()), [])
else:
    skip("1i/1j/1k gitleaks' own view of the plants and the placeholders",
         "gitleaks is not on PATH; set GITLEAKS_BIN to run the two-engine half")


# ===========================================================================
section("2  THE FINGERPRINT FORMAT AND THE ACCEPTED TABLE")
# ===========================================================================
sys.path.insert(0, os.path.dirname(_GATE))
import secret_scan_gate as _gate_module                                # noqa: E402

check("2a a fingerprint splits into exactly four fields",
      _gate_module.parse_fingerprint("abc:oncotriage:detector:123"),
      ("abc", "oncotriage", "detector", "123"))
# THE LOCATOR MAY CONTAIN COLONS. It is a BASENAME for a filename finding, and a
# basename may legally contain one. Splitting on every colon would silently
# truncate such an entry into one that matches nothing -- a suppression that
# stops suppressing without saying so.
check("2b ...and a locator carrying colons survives whole",
      _gate_module.parse_fingerprint("abc:gitleaks:rule:odd:name:1")[3],
      "odd:name:1")
check("2c a line with too few fields is not a fingerprint",
      _gate_module.parse_fingerprint("abc:oncotriage:detector"), None)

_shipped = _gate_module.read_accepted(_ACCEPTED)
check_true("2d the shipped accepted table parses", isinstance(_shipped, dict))
check("2e ...and is non-empty, so 2f-2h are not vacuous",
      len(_shipped) > 0, True)
check("2f every accepted entry carries a recorded reason",
      [fp for fp, why in _shipped.items() if why == "(no reason recorded)"], [])
# A separator rule is not a reason. Without this the grouped summary prints
# eighty equals signs where the reason should be.
check("2g no reason is a bare separator",
      [fp for fp, why in _shipped.items() if set(why) <= set("=-_ ")], [])
with open(_ACCEPTED, encoding="utf-8") as _handle:
    _fingerprint_lines = [ln.strip() for ln in _handle
                          if ln.strip() and not ln.strip().startswith("#")]
check("2h no fingerprint is listed twice",
      len(_fingerprint_lines), len(set(_fingerprint_lines)))
check("2i the table on disk and the parsed table agree in size",
      len(_fingerprint_lines), len(_shipped))

_bad = os.path.join(_TMP, "malformed-accepted.txt")
with open(_bad, "w", encoding="utf-8") as _handle:
    _handle.write("# a reason\nnot-a-fingerprint\n")
try:
    _gate_module.read_accepted(_bad)
    _outcome = "accepted a malformed line"
except _gate_module.ScanUnavailable:
    _outcome = "refused"
check("2j a malformed accepted line is a REFUSAL, not a silent skip",
      _outcome, "refused")

# THE TABLE'S OWN NAME MUST NOT TRIP THE FILENAME LAYER. It did: the obvious
# name, secret-scan-accepted.txt, matches "a filename naming a secret", and the
# only fingerprint that could suppress that is keyed on the file's own blob oid
# -- which changes every time an entry is added. The suppression file would have
# had to suppress itself, and would have gone stale on every edit.
check("2k the accepted table's own filename fires no filename detector",
      scan_filename(os.path.basename(_ACCEPTED)), [])
check("2l ...and the name that forced the rename still does, so 2k is not "
      "vacuous", scan_filename("secret-scan-accepted.txt"), ["secret_file"])


# ===========================================================================
section("3  LAYER 2, THE GATE -- STAGED RANGE FIRES AND THEN CLEARS")
# ===========================================================================
_r3 = new_repo("staged")
_a3 = empty_accepted("accepted-3.txt")

_code, _out = run_gate(_r3, "staged", _a3)
check("3a a repository with nothing staged passes", _code, 0)

with open(os.path.join(_r3, "plant.conf"), "wb") as _handle:
    _handle.write(plant_text())
git(_r3, "add", "plant.conf")
_code, _out = run_gate(_r3, "staged", _a3)
check("3b a staged plant FAILS the gate", _code, 1)
check("3c ...and the project scanner named the AWS shape",
      "oncotriage:aws_access_key_id" in _out, True)
check("3d ...and the Hugging Face shape",
      "oncotriage:huggingface_token" in _out, True)
check("3e ...and the OpenAI shape",
      "oncotriage:openai_anthropic_key" in _out, True)
if GITLEAKS:
    check("3f gitleaks named it too, independently",
          "gitleaks:aws-access-token" in _out
          and "gitleaks:huggingface-access-token" in _out
          and "gitleaks:openai-api-key" in _out, True)
else:
    skip("3f gitleaks' independent findings on the staged plant",
         "gitleaks is not on PATH")
# NO MATCHED BYTES IN THE OUTPUT, ever. A scanner that prints secrets to prove
# it found secrets has moved them into a CI log.
check("3g the gate's output contains none of the three planted values",
      any(v in _out for v in (make_aws(3), make_hf(5), make_openai(7))), False)

git(_r3, "rm", "-q", "--cached", "plant.conf")
os.unlink(os.path.join(_r3, "plant.conf"))
_code, _out = run_gate(_r3, "staged", _a3)
check("3h unstaging the plant clears the gate", _code, 0)

# THE PLANT WORKS THROUGH THE FILENAME LAYER TOO, which gitleaks has no
# equivalent of at all.
with open(os.path.join(_r3, ".env"), "w", encoding="utf-8") as _handle:
    _handle.write("nothing credential shaped in here at all\n")
git(_r3, "add", "-f", ".env")
_code, _out = run_gate(_r3, "staged", _a3)
check("3i a file NAMED .env fails even with unremarkable content", _code, 1)
check("3j ...through the filename layer, named as such",
      "oncotriage:dotenv_file" in _out, True)
git(_r3, "rm", "-q", "--cached", ".env")
os.unlink(os.path.join(_r3, ".env"))
check("3k ...and clears once unstaged", run_gate(_r3, "staged", _a3)[0], 0)


# ===========================================================================
section("4  LAYER 2 -- THE OBJECT RANGE OUTLIVES THE WORKING TREE")
# ===========================================================================
# The reason the range is the object database and not the working tree, and the
# reason it is not a diff either.
_r4 = new_repo("objects")
_a4 = empty_accepted("accepted-4.txt")

check("4a a fresh repository passes the object scan",
      run_gate(_r4, "objects", _a4)[0], 0)

with open(os.path.join(_r4, "plant.conf"), "wb") as _handle:
    _handle.write(plant_text(seed=1))
git(_r4, "add", "plant.conf")
git(_r4, "commit", "-q", "-m", "plant")
_code, _out = run_gate(_r4, "objects", _a4)
check("4b a committed plant fails the object scan", _code, 1)

git(_r4, "rm", "-q", "plant.conf")
git(_r4, "commit", "-q", "-m", "remove the plant")
check("4c the file is gone from the working tree",
      os.path.exists(os.path.join(_r4, "plant.conf")), False)
_code, _out = run_gate(_r4, "objects", _a4)
check("4d ...and the object scan STILL fails, because the blob is still "
      "clonable", _code, 1)

# ACCEPTING IT MAKES IT PASS, AND THE ACCEPTANCE IS EXACT. This is the mechanism
# the shipped table uses for the twelve real historical compose blobs.
_fingerprints = [ln.strip() for ln in
                 run_gate(_r4, "objects", _a4, extra=("--emit-accepted",))[1]
                 .splitlines()
                 if _gate_module.parse_fingerprint(ln.strip())]
check("4e --emit-accepted lists the findings as fingerprints",
      len(_fingerprints) > 0, True)
_a4b = os.path.join(_TMP, "accepted-4b.txt")
with open(_a4b, "w", encoding="utf-8") as _handle:
    _handle.write("# the plant, accepted for this control only\n")
    _handle.write("\n".join(_fingerprints) + "\n")
check("4f accepting every fingerprint turns the gate green",
      run_gate(_r4, "objects", _a4b)[0], 0)

# AND A STALE ENTRY FAILS, DIFFERENTLY. Exit 2, not 1: a dead exemption is a
# different instruction to a human than a live finding.
_a4c = os.path.join(_TMP, "accepted-4c.txt")
with open(_a4c, "w", encoding="utf-8") as _handle:
    _handle.write("# a reason for something that is not there\n")
    _handle.write("\n".join(_fingerprints) + "\n")
    _handle.write("0" * 40 + ":oncotriage:aws_access_key_id:0\n")
_code, _out = run_gate(_r4, "objects", _a4c)
check("4g an accepted entry that matches nothing is exit 2", _code, 2)
check("4h ...and is named as stale rather than as a finding",
      "STALE" in _out, True)

# THE STALENESS CHECK IS SCOPED TO THE FULL RANGE. Running it on `staged` fired
# every entry as stale on the first hook invocation -- found by running it.
check("4i the same table on the staged range does NOT report staleness",
      run_gate(_r4, "staged", _a4c)[0], 0)


# ===========================================================================
section("5  THE EVIL MERGE -- WHAT A LOG SCAN STRUCTURALLY CANNOT SEE")
# ===========================================================================
_r5 = new_repo("evilmerge")
_a5 = empty_accepted("accepted-5.txt")
git(_r5, "checkout", "-q", "-b", "side")
with open(os.path.join(_r5, "side.txt"), "w", encoding="utf-8") as _handle:
    _handle.write("side\n")
git(_r5, "add", "side.txt")
git(_r5, "commit", "-q", "-m", "side")
git(_r5, "checkout", "-q", "main")
with open(os.path.join(_r5, "main.txt"), "w", encoding="utf-8") as _handle:
    _handle.write("mainline\n")
git(_r5, "add", "main.txt")
git(_r5, "commit", "-q", "-m", "mainline")
git(_r5, "merge", "--no-commit", "--no-ff", "-q", "side", check_rc=False)
with open(os.path.join(_r5, "evil.conf"), "wb") as _handle:
    _handle.write(plant_text(seed=2))
git(_r5, "add", "evil.conf")
git(_r5, "commit", "-q", "-m", "evil merge")

check("5a the merge really has two parents",
      len(git(_r5, "rev-list", "--parents", "-n", "1", "HEAD"
              ).stdout.split()), 3)
check("5b the planted file is in neither parent",
      [git(_r5, "cat-file", "-p", f"HEAD^{p}:evil.conf",
           check_rc=False).returncode for p in ("1", "2")], [128, 128])

_code, _out = run_gate(_r5, "objects", _a5)
check("5c the object gate FAILS on an evil merge", _code, 1)
check("5d ...naming the file", "evil.conf" in _out, True)

if GITLEAKS:
    # THE NON-DEGENERACY HALF. Without this, 5c is equally satisfied by a gate
    # that fails on everything -- and the whole argument for the object range is
    # that the log scan does NOT fail here.
    _log = subprocess.run(
        [GITLEAKS, "git", "--log-opts=--all --full-history", "--no-banner",
         "--redact", "--exit-code", "1", "--report-format", "json",
         "--report-path", os.path.join(_TMP, "evil-log.json"), _r5],
        capture_output=True, text=True)
    check("5e the full-history LOG scan reports no leak on the same repository",
          _log.returncode, 0)
    check("5f ...so 5c is a real difference and not a gate that fails on "
          "everything",
          run_gate(new_repo("control-clean"), "objects", _a5)[0], 0)
else:
    skip("5e/5f the log scan's blind spot, measured against gitleaks",
         "gitleaks is not on PATH")


# ===========================================================================
section("6  FORCE-PUSH RESIDUE -- UNREACHABLE IS NOT GONE")
# ===========================================================================
_r6 = new_repo("forcepush")
_a6 = empty_accepted("accepted-6.txt")
with open(os.path.join(_r6, "plant.conf"), "wb") as _handle:
    _handle.write(plant_text(seed=3))
git(_r6, "add", "plant.conf")
git(_r6, "commit", "-q", "-m", "plant")
_oid = git(_r6, "rev-parse", "HEAD:plant.conf").stdout.strip()
# `reset --hard` here is the local half of what a force-push does on a remote:
# the ref moves off the commit and the objects stay.
git(_r6, "reset", "-q", "--hard", "HEAD~1")

_reachable = git(_r6, "rev-list", "--objects", "--all").stdout
check("6a the blob is no longer reachable from any ref",
      _oid in _reachable, False)
_all_objects = git(_r6, "cat-file", "--batch-all-objects",
                   "--batch-check=%(objectname)").stdout
check("6b ...but it is still in the object database", _oid in _all_objects,
      True)
_code, _out = run_gate(_r6, "objects", _a6)
check("6c and the gate finds it, because it walks --batch-all-objects", _code, 1)
check("6d ...naming that exact blob", _oid in _out, True)

git(_r6, "reflog", "expire", "--expire=now", "--all")
git(_r6, "gc", "-q", "--prune=now")
check("6e once the object is genuinely expired it is gone",
      _oid in git(_r6, "cat-file", "--batch-all-objects",
                  "--batch-check=%(objectname)").stdout, False)
check("6f ...and the gate is clean again -- the control was removed",
      run_gate(_r6, "objects", _a6)[0], 0)


# ===========================================================================
section("7  LAYER 1, THE HOOK -- IT REFUSES, AND IT IS BYPASSABLE")
# ===========================================================================
_r7 = new_repo("hook")
os.makedirs(os.path.join(_r7, ".github", "scripts"))
os.makedirs(os.path.join(_r7, ".githooks"))
shutil.copy2(_GATE, os.path.join(_r7, ".github", "scripts",
                                 "secret_scan_gate.py"))
shutil.copy2(_ACCEPTED, os.path.join(_r7, ".github",
                                     "scan-accepted-fingerprints.txt"))
shutil.copy2(_HOOK, os.path.join(_r7, ".githooks", "pre-commit"))
os.chmod(os.path.join(_r7, ".githooks", "pre-commit"), 0o755)
git(_r7, "config", "core.hooksPath", ".githooks")
check("7a core.hooksPath points at the TRACKED directory, not .git/hooks",
      git(_r7, "config", "core.hooksPath").stdout.strip(), ".githooks")

_hook_env = dict(os.environ)
_hook_env["PYTHONPATH"] = _REPO_ROOT + os.pathsep + _hook_env.get("PYTHONPATH", "")
if GITLEAKS:
    _hook_env["GITLEAKS_BIN"] = GITLEAKS


def commit(repo, message, extra=()):
    return subprocess.run(["git", "-C", repo, "commit", "-q", "-m", message]
                          + list(extra),
                          capture_output=True, text=True, env=_hook_env)


_before = git(_r7, "rev-parse", "HEAD").stdout.strip()
git(_r7, "add", "-A")
_clean_commit = commit(_r7, "add the gate and the hook")
check("7b a clean commit is allowed through the hook",
      _clean_commit.returncode, 0)
check("7c ...and it really created a commit",
      git(_r7, "rev-parse", "HEAD").stdout.strip() != _before, True)

with open(os.path.join(_r7, "plant.conf"), "wb") as _handle:
    _handle.write(plant_text(seed=4))
git(_r7, "add", "plant.conf")
_before = git(_r7, "rev-parse", "HEAD").stdout.strip()
_blocked = commit(_r7, "plant")
check("7d the hook REFUSES a commit carrying a planted secret",
      _blocked.returncode != 0, True)
check("7e ...and NO commit was created",
      git(_r7, "rev-parse", "HEAD").stdout.strip(), _before)
check("7f ...and it said why, in a line an operator can act on",
      "SECRET SCAN REFUSED THIS COMMIT" in (_blocked.stdout + _blocked.stderr),
      True)
check("7g ...without printing any of the planted values",
      any(v in (_blocked.stdout + _blocked.stderr)
          for v in (make_aws(7), make_hf(9), make_openai(11))), False)

# THE HOOK IS CONVENIENCE, NOT PROTECTION, AND THIS IS THE PROOF RATHER THAN THE
# CLAIM. If --no-verify did not work the hook would be something else; that it
# does is exactly why the CI job is the guarantee.
_bypassed = commit(_r7, "plant, bypassed", extra=("--no-verify",))
check("7h --no-verify walks straight past it", _bypassed.returncode, 0)
check("7i ...and the commit exists",
      git(_r7, "rev-parse", "HEAD").stdout.strip() != _before, True)
check("7j but the object gate then catches what the hook could not",
      run_gate(_r7, "objects", empty_accepted("accepted-7.txt"))[0], 1)

# A HOOK THAT CANNOT RUN MUST REFUSE. "I could not look" and "I looked and it
# was clean" are different statements and only one licenses a commit.
_moved = os.path.join(_r7, ".github", "scripts", "secret_scan_gate.py")
os.rename(_moved, _moved + ".moved")
_no_gate = subprocess.run([os.path.join(_r7, ".githooks", "pre-commit")],
                          cwd=_r7, capture_output=True, text=True,
                          env=_hook_env)
check("7k the hook refuses when the gate script is missing",
      _no_gate.returncode != 0, True)
check("7l ...saying it refused rather than passing",
      "refusing" in (_no_gate.stdout + _no_gate.stderr).lower(), True)
os.rename(_moved + ".moved", _moved)


# ===========================================================================
section("8  THIS FILE, AND THE FILES IT READS, CARRY NO SECRET")
# ===========================================================================
# The rule this project paid for: the compose file's 56-character Airflow key
# was reproduced into two documentation files while being removed from the code,
# and the project's own scanner caught only one of the two -- because in one of
# them the character after the keyword was `_` rather than `:`, so the detector
# never fired. A grep is therefore part of the check and not only a scan.
_own_source = os.path.join(_TESTS_DIR, "test_secret_scan_gate.py")
for _label, _path in (("8a this test file", _own_source),
                      ("8b the gate script", _GATE),
                      ("8c the accepted table", _ACCEPTED),
                      ("8d the hook", _HOOK)):
    with open(_path, "rb") as _handle:
        _blob = _handle.read()
    check(f"{_label} carries no content match",
          [d for d, _o, _l in scan_bytes(_blob)], [])

check("8e ...and none of them is named like a secret store",
      [os.path.basename(p) for p in (_own_source, _GATE, _ACCEPTED, _HOOK)
       if scan_filename(os.path.basename(p))], [])

# THE GREP HALF. Six values this file can produce, none of which may appear in
# any tracked file. Assembled here, compared here, written nowhere.
_forbidden = [make_aws(s) for s in (3, 4, 5, 6, 7)] + \
             [make_hf(s) for s in (5, 6, 7, 8, 9)] + \
             [make_openai(s) for s in (7, 8, 9, 10, 11)]
_tracked = subprocess.run(["git", "-C", _REPO_ROOT, "ls-files", "-z"],
                          capture_output=True)
_hits = []
if _tracked.returncode == 0:
    for _rel in _tracked.stdout.decode("utf-8", "replace").split("\0"):
        if not _rel:
            continue
        _abs = os.path.join(_REPO_ROOT, _rel)
        try:
            with open(_abs, "rb") as _handle:
                _text = _handle.read()
        except OSError:
            continue
        for _value in _forbidden:
            if _value.encode() in _text:
                _hits.append((_rel, len(_value)))
    check("8f no tracked file carries a value this test can generate", _hits, [])
else:
    skip("8f the tracked-file grep", "not inside a git work tree")


# ===========================================================================
section("9  THE REPOSITORY WAS NOT WRITTEN TO")
# ===========================================================================
check("9a everything this file created is inside its own temp directory",
      _TMP.startswith(tempfile.gettempdir()), True)
_after = {p: _sha256(p) for p in _WATCHED}
check("9b the four repository files it reads are byte-identical afterwards",
      _after, _BEFORE)
# WITHOUT THIS, 9b IS SATISFIED BY TWO EMPTY DICTS OR BY ONE CAPTURE COMPARED
# WITH ITSELF. It is the probe that says the comparison had something to compare.
check("9c ...and both sides are four real digests, so 9b is not a tautology",
      sorted(len(v) for v in list(_BEFORE.values()) + list(_after.values())),
      [64] * 8)
shutil.rmtree(_TMP, ignore_errors=True)
check("9d the temp directory is gone", os.path.exists(_TMP), False)



# ===========================================================================
section("10  THE CI INSTALL LIST IS DERIVED AND CANNOT ROT SILENTLY")
# ===========================================================================
# The gate uses two PURE functions, and importing them costs whatever
# oncotriage/staging/secrets_scan.py pulls in at MODULE SCOPE. The `secret-scan`
# job installs exactly that and no more, from --print-requirements, so a new
# module-scope import anywhere in that chain would make the CI gate exit 3 --
# "the scan could not run" -- on the day it landed, with nothing in the
# repository having failed first. This section is that first failure.
def _module_scope_closure(entry):
    """(oncotriage modules, third-party import names) reachable at module scope.

    Deliberately a fresh AST walk rather than a reuse of anything in the gate:
    a derivation that shares an implementation with the thing it checks agrees
    with it by construction.
    """
    import ast
    stdlib = set(sys.stdlib_module_names)
    seen, third = set(), set()

    def resolve(mod):
        base = os.path.join(_REPO_ROOT, *mod.split("."))
        for candidate in (base + ".py", os.path.join(base, "__init__.py")):
            if os.path.exists(candidate):
                return candidate
        return None

    def walk(mod):
        if mod in seen:
            return
        path = resolve(mod)
        if path is None:
            return
        seen.add(mod)
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in tree.body:                  # MODULE SCOPE ONLY, by design
            targets = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 \
                    and node.module:
                targets = [node.module]
                # `from oncotriage import config` names a MODULE in .names, and
                # a walk that reads only node.module misses it entirely. That is
                # how the first version of this derivation reported zero
                # third-party imports for a chain that needs four.
                if node.module.split(".")[0] == "oncotriage":
                    targets += [f"{node.module}.{a.name}" for a in node.names]
            for target in targets:
                top = target.split(".")[0]
                if top == "oncotriage":
                    walk(target)
                elif top not in stdlib:
                    third.add(top)

    walk(entry)
    return seen, third


_modules, _third = _module_scope_closure("oncotriage.staging.secrets_scan")
check("10a the derivation reaches more than the entry module, so 10c is not "
      "vacuous", len(_modules) > 1, True)
check("10b ...and it really reached oncotriage.config, which is where three of "
      "the four come from", "oncotriage.config" in _modules, True)
check("10c every third-party import the scanner needs at module scope is in "
      "the gate's requirements table",
      sorted(_third), sorted(_gate_module.SCANNER_IMPORT_REQUIREMENTS))
check("10d ...and the table names nothing the closure does not reach",
      sorted(set(_gate_module.SCANNER_IMPORT_REQUIREMENTS) - _third), [])

_printed = subprocess.run([sys.executable, _GATE, "--print-requirements"],
                          capture_output=True, text=True)
check("10e --print-requirements exits 0", _printed.returncode, 0)
check("10f ...and prints the pip names the table maps to",
      sorted(_printed.stdout.split()),
      sorted(_gate_module.SCANNER_IMPORT_REQUIREMENTS.values()))
# IT MUST WORK BEFORE THE REQUIREMENTS ARE INSTALLED, or the CI step that
# installs from it cannot run. That means returning above load_project_scanner.
check("10g ...without importing the project's scanner, which is what makes it "
      "usable to bootstrap the install",
      "oncotriage" not in _printed.stderr, True)

# THE WORKFLOW MUST INSTALL FROM THE FLAG RATHER THAN REPEATING THE LIST.
_workflow = os.path.join(_REPO_ROOT, ".github", "workflows", "ci.yml")
with open(_workflow, encoding="utf-8") as _handle:
    _wf = _handle.read()
_wf_settings = "\n".join(ln for ln in _wf.splitlines()
                         if not ln.lstrip().startswith("#"))
check("10h the workflow installs from --print-requirements",
      "--print-requirements" in _wf_settings, True)
check("10i ...and does not name any of the four itself",
      [pip_name for pip_name in _gate_module.SCANNER_IMPORT_REQUIREMENTS.values()
       if pip_name in _wf_settings], [])
check("10j the workflow runs the gate with --require-gitleaks, so a missing "
      "binary is a red build and not a one-engine pass",
      "--require-gitleaks" in _wf_settings, True)

# ===========================================================================
section("SUMMARY")
print(f"  passed:  {_passed}")
print(f"  failed:  {_failed}")
# ALWAYS PRINTED, EVEN AT ZERO. A skip count that appears only when it is
# non-zero is indistinguishable from a file that has no skip mechanism at all.
print(f"  skipped: {_skipped}   (a skip is NOT a pass and is not counted as one)")
for _label, _reason in _SKIPS:
    print(f"    - {_label}")
if not GITLEAKS:
    print("\n  gitleaks was NOT available: the two-engine half of this gate is "
          "unverified here.")

if __name__ == "__main__":
    sys.exit(1 if _failed else 0)
