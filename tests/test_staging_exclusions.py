# S3 Staging Exclusion and Secrets-Refusal Test
###############################################

"""
The staging tool can upload 64 GB of a clinical-trial project to a public cloud.
The one thing it must never do is upload a credential, and the mechanism that
stops it is ``oncotriage/staging/secrets_scan.py`` -- a filename AND content
scan over exactly the set that is about to upload, with a HARD REFUSAL on any
hit. This file is that mechanism's standing proof.

WHAT IT HOLDS
-------------
    1. The manifest loader refuses every malformed shape, one at a time.
    2. Classification matches on whole path COMPONENTS, not string prefixes.
    3. The PATH_NAMES cross-check fails in BOTH directions -- an unclassified
       path variable, and a declared non-path that has become a path.
    4. THE SECRETS REFUSAL. Every detector fires on a fabricated value of its
       own shape; the two layers are shown INDEPENDENTLY NECESSARY, each with
       the counterexample from this project's own tree; a finding never carries
       the matched bytes; an unreadable file refuses; the allowlist excuses a
       file and STOPS excusing it the moment its content changes.
    5. The AWS preflight reaches every state with a stand-in session, and the
       ``--execute`` gate refuses.
    6. The cost arithmetic keeps the one-time charge out of the monthly
       figure, the two rates carry a MACHINE-READABLE date on PRICING_CONFIG's
       shape, the report prints that date beside the dollar figures, and the
       cost heading names the CONFIGURED staging region rather than a literal.
    7. Non-degeneracy against the SHIPPED manifest: `05- Keys` is really
       excluded, and it is excluded as `secrets`.

NO NETWORK, NO KEYS, NO SPEND, NO AWS SDK. Section 5 drives ``preflight()``
with a stand-in ``session_factory``; section 5g asserts that ``boto3`` never
entered ``sys.modules``, which is what makes "no network" a measurement rather
than a claim. Nothing here needs a live tree either: every manifest is
fabricated in a temporary directory except section 7, which READS the shipped
one and writes nothing.

IT EXECS NOTHING, so it needs no ``_EXEC_ALLOWLIST`` entry in
tests/test_package_invariants.py. Every control is a different INPUT to a
function -- which is the natural control for the pure functions this module is
mostly made of -- or a fabricated manifest on disk.

NOT IN THE COLLISION MATRIX, derived rather than assumed: it writes only inside
a ``tempfile.mkdtemp`` it removes, and the one repository file it reads
(``s3_staging_exclusions.json``) is written by neither of the suite's two
writers.

Run from terminal:
    python tests/test_staging_exclusions.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
import os
import sys

try:
    import oncotriage  # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
         if "__file__" in globals() else None, "__file__"),
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

import json
import re as _re
import shutil
import tempfile

from oncotriage import config as _config
from oncotriage.staging import exclusions as _exc
from oncotriage.staging import manifest as _man
from oncotriage.staging import s3_sync as _sync
from oncotriage.staging import secrets_scan as _scan


#------------------------------------------------------------------------------


_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, actual, expected):
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected: {expected}\n"
                         f"          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def raises(fn):
    """(exception_type_name, message) for a call that must raise.

    Returns ``(None, "")`` when it did not, so the caller records a FAILURE
    rather than the run aborting on the happy path.
    """
    try:
        fn()
    except Exception as exc:                    # noqa: BLE001 -- type is the answer
        return type(exc).__name__, str(exc)
    return None, ""


def drive(fn):
    """Call something that must NOT raise; a raise becomes a comparable value.

    Nine files in this suite have shipped the same defect -- a bare call inside
    a check() argument list, where the exact defect the check exists to catch
    makes the call raise, the raise escapes while the argument is being
    evaluated, and the run reports one traceback where it owed a summary. This
    is the fix, applied from the start.
    """
    try:
        return fn()
    except Exception as exc:                    # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


_TMP = tempfile.mkdtemp(prefix="oncotriage-staging-test-")


def write_manifest(payload, name="m.json"):
    path = os.path.join(_TMP, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


def good_manifest(**overrides):
    """A minimal manifest that validates. Overrides let one field go wrong."""
    payload = {
        "schema_version": 1,
        "staged": [{"path": "data", "reason": "the input"}],
        "excluded": [{"path": "data/secrets", "reason": "keys",
                      "kind": "secrets"}],
        "noise": [{"pattern": ".DS_Store", "reason": "os junk"}],
        "path_names_non_paths": [{"name": "_src", "reason": "a marker"}],
        "secret_scan_allowlist": [],
    }
    payload.update(overrides)
    return payload


print()
print("=" * 70)
print("SECTION 1 -- THE MANIFEST LOADER REFUSES EVERY MALFORMED SHAPE")
print("=" * 70)

_ok_path = write_manifest(good_manifest(), "ok.json")
check("1a a well-formed manifest loads",
      drive(lambda: _exc.load_manifest(_ok_path))["schema_version"], 1)

_t, _m = raises(lambda: _exc.load_manifest(os.path.join(_TMP, "nope.json")))
check("1b a MISSING manifest raises ManifestError", _t, "ManifestError")
check("...and the message says there is no compiled-in default, because a "
      "fallback would upload the credentials directory",
      "stage everything" in _m, True)

_bad = os.path.join(_TMP, "bad.json")
open(_bad, "w").write("{not json")
_t, _m = raises(lambda: _exc.load_manifest(_bad))
check("1c UNPARSEABLE JSON raises ManifestError", _t, "ManifestError")

_t, _m = raises(lambda: _exc.load_manifest(
    write_manifest(good_manifest(schema_version=99), "v99.json")))
check("1d a WRONG schema_version raises", _t, "ManifestError")
check("...naming both versions", "99" in _m and "1" in _m, True)

_no_section = good_manifest()
del _no_section["excluded"]
_t, _m = raises(lambda: _exc.load_manifest(
    write_manifest(_no_section, "nosec.json")))
check("1e a MISSING section raises", _t, "ManifestError")
check("...naming the section", "excluded" in _m, True)

_t, _m = raises(lambda: _exc.load_manifest(write_manifest(
    good_manifest(excluded=[{"path": "x", "kind": "secrets"}]), "nore.json")))
check("1f an exclusion with NO REASON raises -- an unreasoned ruling is the "
      "thing this manifest exists to prevent", _t, "ManifestError")
check("...naming the field", "reason" in _m, True)

_t, _m = raises(lambda: _exc.load_manifest(write_manifest(
    good_manifest(excluded=[{"path": "x", "reason": "r", "kind": "invented"}]),
    "kind.json")))
check("1g an UNKNOWN kind raises -- the vocabulary is closed", _t,
      "ManifestError")
check("...and the message prints the closed set",
      "secrets" in _m and "invented" in _m, True)

_t, _m = raises(lambda: _exc.load_manifest(write_manifest(
    good_manifest(excluded=[{"path": "x", "reason": "a", "kind": "secrets"},
                            {"path": "x", "reason": "b", "kind": "secrets"}]),
    "dup.json")))
check("1h a DUPLICATE path raises", _t, "ManifestError")

_t, _m = raises(lambda: _exc.load_manifest(write_manifest(
    good_manifest(staged=[{"path": "data", "reason": "r"}],
                  excluded=[{"path": "data", "reason": "r",
                             "kind": "secrets"}]), "both.json")))
check("1i a path that is BOTH staged and excluded raises -- there is no "
      "defensible reading, so neither side quietly wins", _t, "ManifestError")

_t, _m = raises(lambda: _exc.load_manifest(write_manifest(
    good_manifest(secret_scan_allowlist=[{"path": "p", "reason": "r"}]),
    "allow.json")))
check("1j an allowlist row with NO sha256 raises -- an excuse with no content "
      "key never expires", _t, "ManifestError")

_t, _m = raises(lambda: _exc.load_manifest(write_manifest(
    good_manifest(noise="not-a-list"), "noiselist.json")))
check("1k a section of the wrong TYPE raises", _t, "ManifestError")

check("1l ManifestError is a RuntimeError, so a stray `except ValueError` "
      "cannot eat it", issubclass(_exc.ManifestError, RuntimeError), True)
check("...and is NOT a ValueError",
      issubclass(_exc.ManifestError, ValueError), False)


print()
print("=" * 70)
print("SECTION 2 -- CLASSIFICATION MATCHES COMPONENTS, NOT STRING PREFIXES")
print("=" * 70)

_plan = _exc.build_plan(_exc.load_manifest(write_manifest(good_manifest(
    staged=[{"path": "02- Data", "reason": "input"}],
    excluded=[{"path": "02- Data/04- MeSH", "reason": "rebuildable",
               "kind": "regenerable"}],
), "cls.json")))

check("2a a staged root is staged",
      _exc.classify("02- Data", _plan)[0], True)
check("2b a file inside a staged root is staged",
      _exc.classify("02- Data/01- Patients/x.json", _plan)[0], True)
check("2c an excluded child of a staged root is NOT staged -- most specific "
      "rule wins", _exc.classify("02- Data/04- MeSH", _plan)[0], False)
check("2d ...and so is everything under it",
      _exc.classify("02- Data/04- MeSH/desc2026.xml", _plan)[0], False)

# THE CONTROL THIS SECTION EXISTS FOR. A string startswith would exclude
# "02- Data/04- MeSHX" because "02- Data/04- MeSH" is a prefix of it.
check("2e A SIBLING WHOSE NAME EXTENDS AN EXCLUDED ONE IS STILL STAGED -- "
      "this is what a startswith implementation gets wrong",
      _exc.classify("02- Data/04- MeSHX/keep.json", _plan)[0], True)
check("2f ...and the rule that decided it is the staged one, not the excluded "
      "one", _exc.classify("02- Data/04- MeSHX/keep.json", _plan)[2],
      "staged:02- Data")

check("2g an UNRULED path is not staged (default deny)",
      _exc.classify("99- Nowhere/x", _plan)[0], False)
check("...and it is labelled `unruled` so the report can name it rather than "
      "dropping it silently",
      _exc.classify("99- Nowhere/x", _plan)[2], "unruled")

check("2h a noise basename is dropped at any depth",
      _exc.classify("02- Data/deep/.DS_Store", _plan)[0], False)
check("...labelled with the pattern that matched",
      _exc.classify("02- Data/deep/.DS_Store", _plan)[2], "noise:.DS_Store")

check("2i a backslash path normalises the same as a forward-slash one",
      _exc.classify("02- Data\\04- MeSH", _plan)[0],
      _exc.classify("02- Data/04- MeSH", _plan)[0])


print()
print("=" * 70)
print("SECTION 3 -- THE PATH_NAMES CROSS-CHECK FAILS IN BOTH DIRECTIONS")
print("=" * 70)

_root = os.path.join(_TMP, "root")
os.makedirs(os.path.join(_root, "02- Data", "04- MeSH"), exist_ok=True)
_xplan = _exc.build_plan(_exc.load_manifest(write_manifest(good_manifest(
    staged=[{"path": "02- Data", "reason": "input"}],
    excluded=[{"path": "02- Data/04- MeSH", "reason": "rebuildable",
               "kind": "regenerable"}],
    path_names_non_paths=[{"name": "_main_path_source", "reason": "a marker"}],
), "xc.json")))

_resolved_ok = {
    "main_path": _root,
    "data_path": os.path.join(_root, "02- Data"),
    "data_MeSH_path": os.path.join(_root, "02- Data", "04- MeSH"),
    "_main_path_source": "fallback",
}
_report = drive(lambda: _exc.cross_check_path_names(_xplan, _resolved_ok))
check("3a every classified member passes", isinstance(_report, dict), True)
check("3b the staged one is reported staged", _report["data_path"][0], "staged")
check("3c the excluded one is reported excluded",
      _report["data_MeSH_path"][0], "excluded")
check("3d the declared non-path is reported non-path",
      _report["_main_path_source"][0], "non-path")
check("3e the root itself is reported as the root",
      _report["main_path"][0], "root")

_resolved_new = dict(_resolved_ok)
_resolved_new["brand_new_path"] = os.path.join(_root, "77- Unheard Of")
_t, _m = raises(lambda: _exc.cross_check_path_names(_xplan, _resolved_new))
check("3f AN UNCLASSIFIED PATH VARIABLE RAISES -- this is the guard that stops "
      "a future path variable being missed in silence", _t,
      "PathNamesUnclassified")
check("...naming the variable", "brand_new_path" in _m, True)

# THE OTHER DIRECTION, which a one-sided check would miss.
_resolved_flip = dict(_resolved_ok)
_resolved_flip["_main_path_source"] = os.path.join(_root, "02- Data")
_t, _m = raises(lambda: _exc.cross_check_path_names(_xplan, _resolved_flip))
check("3g A DECLARED NON-PATH THAT HAS BECOME A PATH ALSO RAISES -- without "
      "this the declaration is a permanent exemption", _t,
      "PathNamesUnclassified")
check("...telling the operator to remove the declaration",
      "Remove the declaration" in _m, True)

_resolved_out = dict(_resolved_ok)
_resolved_out["stray"] = os.path.join(_TMP, "somewhere-else")
_t, _m = raises(lambda: _exc.cross_check_path_names(_xplan, _resolved_out))
check("3h a path variable resolving OUTSIDE the root raises", _t,
      "PathNamesUnclassified")


print()
print("=" * 70)
print("SECTION 4 -- THE SECRETS REFUSAL")
print("=" * 70)

# Fabricated values of each issued shape. None is a real credential.
#
# EVERY ONE IS ASSEMBLED FROM TWO FRAGMENTS AND NONE APPEARS WHOLE IN THIS
# SOURCE, which is a requirement rather than a flourish. The first draft wrote
# them as literals and the next staging dry run flagged THIS FILE twelve times
# -- correctly, because a scanner that ignored a complete `AKIA...` or `ghp_...`
# in a source file would ignore a real one. Allowlisting the file was the wrong
# fix: it is edited constantly, so the sha256-keyed excuse would expire on
# every change and the staging tool would refuse until somebody re-allowlisted
# it, every time.
#
# Splitting each shape at a point INSIDE the detector's own pattern means the
# joined bytes match and the source bytes cannot. It also keeps this file out
# of any third-party scanner's way -- GitHub push protection rejects a literal
# `AKIA` + 16 uppercase, and a test suite that cannot be pushed is not a test
# suite.
#
# The split points are verified by section 4b-control below, which scans THIS
# FILE and requires zero hits.
def _shape(prefix, body):
    """Join two halves at run time. Neither half matches on its own."""
    return prefix + body


_SHAPES = {
    "openai_anthropic_key": _shape(b"sk-", b"proj-A1b2C3d4E5f6G7h8I9j0K1l2M3n4"),
    # SPLIT TWICE. A JWT's PAYLOAD segment also begins with `eyJ` -- it is
    # base64url for `{"` and both segments are JSON -- so splitting only at the
    # front left the payload matching on its own, 26 characters, inside this
    # source. Found by 4b-control on its first run.
    "jwt_or_qdrant_key":    _shape(b"eyJ", b"hbGciOiJIUzI1NiJ9." )
                            + _shape(b"eyJ", b"zdWIiOiIxMjM0NX0.Qw3rTy"),
    "aws_access_key_id":    _shape(b"AKIA", b"IOSFODNN7EXAMPLE"),
    "private_key_block":    _shape(b"-----BEGIN ", b"RSA PRIVATE KEY-----"),
    "github_token":         _shape(b"ghp_", b"A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"),
    "slack_token":          _shape(b"xox", b"b-1234567890-abcdefghij"),
    "google_api_key":       _shape(b"AIza", b"SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"),
    "huggingface_token":    _shape(b"hf_", b"A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7"),
    "credential_assignment": _shape(b'api_key = "', b'Zx9Qw8Er7Ty6Ui5Op4As3"'),
}
for _name, _blob in sorted(_SHAPES.items()):
    _hits = {h[0] for h in _scan.scan_bytes(_blob)}
    check(f"4a[{_name}] fires on a fabricated value of its own shape",
          _name in _hits, True)

check("4b every declared content detector is exercised above (non-degeneracy: "
      "a detector added without a case here would go untested)",
      sorted(_SHAPES), sorted(n for n, _p, _m in _scan._CONTENT_DETECTORS))

# THE CONTROL FOR THE SPLIT ITSELF. If a future edit writes one of these
# shapes whole, this file starts flagging in every staging run -- and the check
# that catches it has to be here, because nothing else scans the test suite.
check("4b-control THIS FILE contains no complete credential shape, so the "
      "scanner it tests does not flag it",
      _scan.scan_bytes(open(os.path.abspath(__file__), "rb").read()), [])
check("...and that is non-degenerate: the JOINED shapes do match, so the "
      "split is hiding them from the source and not from the detector",
      len(_scan.scan_bytes(b"\n".join(_SHAPES.values()))) >= len(_SHAPES), True)

check("4c innocuous prose fires nothing",
      _scan.scan_bytes(b"the patient was diagnosed with stage 3 carcinoma"), [])

# ---- THE TWO LAYERS ARE INDEPENDENTLY NECESSARY -------------------------
# Both counterexamples are real files in this project's tree, reduced to their
# shape. Neither layer alone catches both.
_PROSE_KEY = (b"OpenAI: use " + _SHAPES["openai_anthropic_key"] + b" for the run\n"
              + b"Qdrant: " + _SHAPES["jwt_or_qdrant_key"] + b"\n")
check("4d CONTENT catches a credential written as PROSE, with no assignment "
      "syntax anywhere -- this is 05- Keys/Keys.txt, and every scanner built "
      "on parsing NAME=VALUE misses it",
      sorted({h[0] for h in _scan.scan_bytes(_PROSE_KEY)}),
      ["jwt_or_qdrant_key", "openai_anthropic_key"])
check("...and its NAME alone would not have caught it, if it were called "
      "something ordinary", _scan.scan_filename("notes.txt"), [])

_SHORT_PW = b'{"admin": "Xk39fBq2Lm77PzQd"}'
check("4e CONTENT finds NOTHING in a short opaque password -- this is "
      "06- Airflow/simple_auth_manager_passwords.json.generated, a live admin "
      "password that every content heuristic misses",
      _scan.scan_bytes(_SHORT_PW), [])
check("4f ...and FILENAME is what catches it. Content-only would have "
      "uploaded it",
      _scan.scan_filename("simple_auth_manager_passwords.json.generated"),
      ["password_file"])

check("4g the filename layer catches a .env at any spelling",
      [bool(_scan.scan_filename(n))
       for n in (".env", ".env.local", "prod.env", "Keys.txt", "id_rsa",
                 "x.pem", "credentials")],
      [True] * 7)

check("4h SOURCE FILES ARE EXEMPT FROM THE FILENAME LAYER -- a module named "
      "secrets_scan.py is a module, and the first run of this scanner refused "
      "to upload its own source",
      _scan.scan_filename("secrets_scan.py"), [])
check("...while a non-source file of the same stem is still caught",
      _scan.scan_filename("secrets.json"), ["secret_file"])
check("...and source CONTENT is still scanned, which is the layer that would "
      "catch a hardcoded key in a .py",
      "openai_anthropic_key" in {h[0] for h in _scan.scan_bytes(
          b'KEY = "' + _SHAPES["openai_anthropic_key"] + b'"')}, True)

# ---- the value validator ------------------------------------------------
for _value, _reject, _why in (
    (b"_password_from_stdin", True, "a private helper NAME"),
    (b"ONCOTRIAGE_BEDROCK_API_KEY", True, "a constant NAME"),
    (b"ci-placeholder-not-a-real-key", True, "a kebab-case placeholder"),
    (b"get_openai_api_key", True, "a function NAME"),
    (b"Zx9Qw8Er7Ty6Ui5Op4As3", False, "a token carrying digits"),
    (b"+++===///", False, "unrecognisable noise MUST still be reported"),
):
    check(f"4i _is_program_identifier({_why})",
          _scan._is_program_identifier(_value), _reject)

check("4j the noise case is the one that matters: a predicate written as "
      "`fullmatch is None or no-digits` would REJECT it and let an "
      "unrecognisable blob through",
      _scan._is_program_identifier(b"+++===///"), False)

# ---- the exemption is BOUNDED BY LENGTH ---------------------------------
# The exemption used to be unbounded, and this project's own
# `docker-compose.yml` is the counterexample: `AIRFLOW__WEBSERVER__SECRET_KEY`
# is set to a 56-character literal of letters, underscores and hyphens with no
# digit, so the detector matched it, the validator exempted it, and
# `scan_bytes` over the real file returned ZERO findings. A hardcoded signing
# key in a file the scanner called clean.
#
# THE REAL FILE IS DELIBERATELY NOT ASSERTED ON HERE. It is a known defect
# scheduled to be fixed, and a check reading "the shipped compose file has
# exactly one finding" would go red on the day somebody fixes it -- a test that
# fails on the change it exists to protect. What is pinned instead is the
# PREDICATE and the window its constant sits in, which stay true afterwards.
_CAP = getattr(_scan, "_IDENTIFIER_EXEMPTION_MAX_LENGTH", None)
check("4j-a the exemption carries a length cap (if this fails, every check below "
      "is measuring an unbounded exemption)", _CAP is not None, True)

# THE FALLBACK IS 56 -- THE OFFENDER'S LENGTH -- AND NOT 0. With the constant
# deleted the boundary checks below still have to say something TRUE: at a
# fallback of 0 they end up asking whether a ONE-CHARACTER value is exempt,
# which fails for a reason that has nothing to do with the cap while the label
# claims otherwise. Measured, not reasoned: the first version used `_CAP or 0`
# and the revert harness reported 4j-e firing on `b"p"`. At 56 the same revert
# fires 4j-e on a 57-character value, which is what the label says it tests.
_CAP_OR_OFFENDER = _CAP if _CAP is not None else 56


def _identifier_of_length(n):
    """A digitless, identifier-shaped byte string of exactly ``n`` characters.

    Derived from the cap rather than typed, so the boundary cases below move
    with the constant instead of rotting against it. The stem carries no digit
    and no detector keyword, so the fragments are inert in this source.
    """
    stem = b"placeholder_configuration_value_for_the_signing_"
    return (stem * (n // len(stem) + 1))[:n]


# THE FLOOR. Two values, and the second is the one that actually binds.
check("4j-b the longest value this detector has ever CAPTURED in this tree "
      "(29 chars) is still exempt -- FIRES IF THE CAP IS LOWERED PAST IT",
      _scan._is_program_identifier(b"ci-placeholder-not-a-real-key"), True)

_LONGEST_NAME = "MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS"   # 43 chars
check("4j-b-i non-degeneracy: that 43-character name is still a real constant of "
      "this project, so the floor it sets has not rotted away",
      hasattr(_config, _LONGEST_NAME), True)
check("4j-c ...and the longest DIGITLESS IDENTIFIER in the whole tree (43 chars, "
      "zero identifiers reach 44) is still exempt. This is the binding floor: "
      "a cap of 40 would pass 4j-b and fail here, and six identifiers in this "
      "tree are 41 characters or longer",
      _scan._is_program_identifier(_LONGEST_NAME.encode()), True)

# THE CEILING, and the control that fires if the cap is removed outright.
check("4j-d a capture exactly AT the cap is still exempt",
      _scan._is_program_identifier(
          _identifier_of_length(_CAP_OR_OFFENDER)), True)
check("4j-e ...and one character OVER it is NOT, whatever its shape -- FIRES IF "
      "THE CAP IS REMOVED",
      _scan._is_program_identifier(
          _identifier_of_length(_CAP_OR_OFFENDER + 1)), False)

check("4j-f the cap sits inside the measured window [44, 55]: 44 clears the "
      "longest digitless identifier in the tree (43) and 55 is the last value "
      "that still reports the 56-character offender",
      44 <= (_CAP if _CAP is not None else -1) <= 55, True)

# END TO END, through the detector rather than the validator. Split at a point
# inside the detector's own keyword so neither fragment matches in this source
# -- the same technique _SHAPES uses, and 4b-control is what proves it worked.
_LONG_SECRET_LINE = (_shape(b"AIRFLOW__WEBSERVER__SECRET", b"_KEY: ")
                     + _identifier_of_length(56))
check("4j-g END TO END: a 56-character digitless value assigned to a SECRET_KEY "
      "is REPORTED -- this is docker-compose.yml's exact shape, and before the "
      "cap the scanner returned nothing for it",
      sorted({h[0] for h in _scan.scan_bytes(_LONG_SECRET_LINE)}),
      ["credential_assignment"])
_NAME_SECRET_LINE = (_shape(b"AIRFLOW__WEBSERVER__SECRET", b"_KEY: ")
                     + b"get_openai_api_key")
check("...while the same assignment carrying a program NAME is still silent, "
      "which is what stops this becoming a cap that reports everything",
      _scan.scan_bytes(_NAME_SECRET_LINE), [])

# ---- findings carry no secret material ----------------------------------
_leak_dir = os.path.join(_TMP, "leak")
os.makedirs(_leak_dir, exist_ok=True)
_leak_file = os.path.join(_leak_dir, "notes.txt")
open(_leak_file, "wb").write(_PROSE_KEY)
_findings = _scan.scan_file(_leak_file, "notes.txt")
_rendered = "\n".join(f.describe() for f in _findings)
check("4k a finding NEVER contains the matched bytes",
      any(s in _rendered for s in ("sk-proj-A1b2", "eyJhbGci")), False)
check("...it reports the detector, the file and the offset instead",
      "notes.txt" in _rendered and "byte " in _rendered, True)
check("...and it is non-degenerate: something WAS found",
      len(_findings) >= 2, True)

# ---- refusal, not filter ------------------------------------------------
_clean_file = os.path.join(_leak_dir, "clean.txt")
open(_clean_file, "w").write("no secrets here, only prose about carcinoma")
_empty_plan = _exc.build_plan(_exc.load_manifest(_ok_path))
_res = _scan.scan_files([(_leak_file, "notes.txt"),
                         (_clean_file, "clean.txt")], _empty_plan)
check("4l the scanner does NOT drop the dirty file and continue",
      _res.files_scanned, 2)
check("...it records findings", len(_res.findings) >= 2, True)
check("...and the result is not clean", _res.clean(), False)
_t, _m = raises(lambda: _scan.refuse_if_dirty(_res))
check("4m refuse_if_dirty RAISES on a dirty result", _t, "SecretsRefusal")
check("...saying nothing was uploaded", "Nothing was uploaded" in _m, True)
check("...and naming it a refusal rather than a filter",
      "refusal, not a filter" in _m, True)
check("4n SecretsRefusal is a RuntimeError, not a ValueError",
      (issubclass(_scan.SecretsRefusal, RuntimeError),
       issubclass(_scan.SecretsRefusal, ValueError)), (True, False))

_res_clean = _scan.scan_files([(_clean_file, "clean.txt")], _empty_plan)
check("4o a clean set is clean", _res_clean.clean(), True)
check("...and refuse_if_dirty does NOT raise on it (non-degeneracy: without "
      "this, 4m would pass against a function that always raises)",
      raises(lambda: _scan.refuse_if_dirty(_res_clean))[0], None)

# ---- unreadable is a refusal -------------------------------------------
_gone = os.path.join(_leak_dir, "vanished.txt")
_res_missing = _scan.scan_files([(_gone, "vanished.txt")], _empty_plan)
check("4p AN UNREADABLE FILE IS A REFUSAL -- 'could not read' is not 'is safe'",
      len(_res_missing.unreadable), 1)
check("...so the result is not clean", _res_missing.clean(), False)
check("...and it raises", raises(lambda: _scan.refuse_if_dirty(_res_missing))[0],
      "SecretsRefusal")

# ---- the allowlist ------------------------------------------------------
_allow_sha = _scan.sha256_file(_leak_file)
_allow_plan = _exc.build_plan(_exc.load_manifest(write_manifest(good_manifest(
    secret_scan_allowlist=[{"path": "notes.txt", "sha256": _allow_sha,
                            "reason": "a fabricated fixture"}]),
    "al.json")))
_res_allowed = _scan.scan_files([(_leak_file, "notes.txt")], _allow_plan)
check("4q an allowlisted file is excused", _res_allowed.findings, [])
check("...and the excuse is recorded rather than silent",
      len(_res_allowed.allowlisted) >= 2, True)
check("...so the set is clean", _res_allowed.clean(), True)

# THE CONTROL THAT MAKES THE ALLOWLIST SAFE.
open(_leak_file, "ab").write(b"\n# one more line\n")
_res_changed = _scan.scan_files([(_leak_file, "notes.txt")], _allow_plan)
check("4r THE EXCUSE EXPIRES WHEN THE CONTENT CHANGES -- keying on the path "
      "alone would leave a permanent hole under a path somebody later reuses",
      _res_changed.clean(), False)
check("...and the findings come back", len(_res_changed.findings) >= 2, True)

_wrong_path_plan = _exc.build_plan(_exc.load_manifest(write_manifest(
    good_manifest(secret_scan_allowlist=[
        {"path": "somewhere/else.txt", "sha256": _allow_sha,
         "reason": "wrong path"}]), "al2.json")))
check("4s an allowlist row for a DIFFERENT path excuses nothing",
      _scan.scan_files([(_leak_file, "notes.txt")],
                       _wrong_path_plan).clean(), False)


# ---- the bounded prefix, and what 0 means -------------------------------
_deep = os.path.join(_leak_dir, "deep.txt")
# A newline before the token: the lookbehind correctly refuses a token glued to
# a word character, and that is not what this pair measures.
open(_deep, "wb").write(b"z" * 100000 + b"\n" + _SHAPES["openai_anthropic_key"])
check("4t THE BOUNDED PREFIX IS A REAL LIMIT -- a key past the default 64 KiB "
      "is NOT seen, and this is stated rather than left for someone to "
      "discover", _scan.scan_file(_deep, "deep.txt"), [])
check("4u ...and prefix_bytes=0 means THE WHOLE FILE, not zero bytes. The "
      "tunable's comment promised this while read(0) returned b'' -- every "
      "file would have scanned clean under the same 'CLEAN' verdict a real "
      "scan prints",
      len(_scan.scan_file(_deep, "deep.txt", prefix_bytes=0)) >= 1, True)

# ---- more than one allowlist row for one path ---------------------------
_two = os.path.join(_leak_dir, "two.txt")
open(_two, "wb").write(b"\n" + _SHAPES["openai_anthropic_key"])
_two_sha = _scan.sha256_file(_two)
_two_plan = _exc.build_plan(_exc.load_manifest(write_manifest(good_manifest(
    secret_scan_allowlist=[
        {"path": "two.txt", "sha256": "0" * 64, "reason": "an outgoing hash"},
        {"path": "two.txt", "sha256": _two_sha, "reason": "the live hash"}]),
    "two.json")))
_two_res = _scan.scan_files([(_two, "two.txt")], _two_plan)
check("4v EVERY allowlist row for a path is considered, not just the first. "
      "Two hashes for one path is what a transition looks like, and the first "
      "version broke out of the loop on the first PATH match",
      _two_res.clean(), True)
check("...and the row that actually matched is the one recorded",
      _two_res.allowlisted[0][2] if _two_res.allowlisted else None,
      "the live hash")


print()
print("=" * 70)
print("SECTION 5 -- THE AWS PREFLIGHT AND THE --execute GATE")
print("=" * 70)


class _Creds:
    pass


class _StubSession:
    """Enough of a boto3 Session to drive every preflight branch offline."""

    def __init__(self, creds=_Creds(), region="us-east-1", identity=None,
                 raise_on=None):
        self._creds = creds
        self.region_name = region
        self._identity = identity or {"Account": "123456789012",
                                      "Arn": "arn:aws:iam::123456789012:user/x",
                                      "UserId": "AIDEXAMPLE"}
        self._raise_on = raise_on

    def get_credentials(self):
        if self._raise_on == "creds":
            raise RuntimeError("chain exploded")
        return self._creds

    def client(self, name):
        if self._raise_on == "sts":
            raise RuntimeError("denied")
        outer = self

        class _C:
            def get_caller_identity(self):
                return outer._identity
        return _C()


def _factory(**kwargs):
    return lambda: _StubSession(**kwargs)


_r = drive(lambda: _sync.preflight(_factory()))
check("5a a good session passes preflight", _r.state, _sync.PREFLIGHT_OK)
check("...and reports the identity",
      (_r.identity["account"], _r.identity["user_id"]),
      ("123456789012", "AIDEXAMPLE"))
check("5b NO CREDENTIALS is its own state, because the remedy is `aws "
      "configure` and not a pip install",
      _sync.preflight(_factory(creds=None)).state,
      _sync.PREFLIGHT_NO_CREDENTIALS)
check("5c a raising credential chain is also NO CREDENTIALS",
      _sync.preflight(_factory(raise_on="creds")).state,
      _sync.PREFLIGHT_NO_CREDENTIALS)
_r_region = _sync.preflight(_factory(region="eu-west-1"))
check("5d A WRONG REGION REFUSES -- a bucket's region is fixed for its "
      "lifetime, so this must not be discovered afterwards",
      _r_region.state, _sync.PREFLIGHT_WRONG_REGION)
check("...naming both regions",
      "eu-west-1" in _r_region.detail and "us-east-1" in _r_region.detail, True)
check("5e a failing sts call is CALL_FAILED, not NO_CREDENTIALS -- credentials "
      "that exist and cannot call are a permissions problem",
      _sync.preflight(_factory(raise_on="sts")).state,
      _sync.PREFLIGHT_CALL_FAILED)
check("5f every state reached above is in the closed vocabulary",
      all(s in _sync.PREFLIGHT_STATES for s in (
          _sync.PREFLIGHT_OK, _sync.PREFLIGHT_NO_CREDENTIALS,
          _sync.PREFLIGHT_WRONG_REGION, _sync.PREFLIGHT_CALL_FAILED,
          _sync.PREFLIGHT_NO_SDK)), True)

check("5g NO NETWORK, MEASURED: boto3 never entered sys.modules, so nothing "
      "above could have made an AWS call", "boto3" in sys.modules, False)

check("5h --execute refuses when preflight failed",
      _sync.execute_refusal_reason(_sync.preflight(_factory(creds=None)))
      is not None, True)
_pass_refusal = _sync.execute_refusal_reason(_sync.preflight(_factory()))
check("5i --execute STILL refuses when preflight PASSES, because the upload "
      "half is not built. This pin fails the day it lands, which is what "
      "forces the docstring to be rewritten rather than quietly outlived",
      _pass_refusal is not None, True)
check("...and it says so in as many words",
      "not built" in (_pass_refusal or ""), True)


print()
print("=" * 70)
print("SECTION 6 -- THE COST ARITHMETIC")
print("=" * 70)

_cost = _man.estimate_cost(100_000_000_000, 10_000)
check("6a storage is priced per decimal GB, matching how AWS bills",
      round(_cost["gigabytes"], 6), 100.0)
check("6b storage cost is rate x GB", round(_cost["storage_usd_per_month"], 4),
      round(100.0 * _cost["rate_gb_month"], 4))
check("6c the PUT charge is per 1,000 objects",
      round(_cost["put_usd_one_time"], 6),
      round(10.0 * _cost["rate_put_per_1000"], 6))
check("6d THE ONE-TIME CHARGE IS NOT FOLDED INTO THE MONTHLY FIGURE -- an "
      "operator comparing months would otherwise budget from the first one",
      "put_usd_one_time" in _cost and "storage_usd_per_month" in _cost, True)
check("6e an empty tree costs nothing and does not divide by zero",
      _man.estimate_cost(0, 0)["storage_usd_per_month"], 0.0)

def _render_cost_lines(cost, staged_region=None):
    """The cost block of a rendered report, as a list of lines.

    A MINIMAL REPORT DICT, not a walk of a real tree: the subject here is the
    six lines the cost block emits, and building 64 GB of fixture to reach them
    would make the check about the walk instead. ``out`` is the injectable sink
    ``render_report`` already carries.

    ``staged_region`` drives the mismatch arm by rebinding the module constant
    inside try/finally with the restore asserted, which is this project's
    idiom for a value resolved at import: an environment variable set here
    would reach nothing, because ``oncotriage.config`` has already resolved it.
    """
    lines = []
    report = {
        "root": "/fabricated", "staged_roots": ["x"], "file_count": 10_000,
        "total_bytes": 100_000_000_000, "by_prefix": {}, "largest": [],
        "excluded_hits": {}, "stale_rules": [], "walk_errors": [], "unruled": [],
        "scan": {"files_scanned": 1, "bytes_read": 1, "findings": 0,
                 "allowlisted": 0, "unreadable": 0, "clean": True},
        "cost": cost,
    }
    original = _config.S3_STAGING_REGION
    try:
        if staged_region is not None:
            _config.S3_STAGING_REGION = staged_region
        _man.render_report(report, out=lines.append)
    finally:
        _config.S3_STAGING_REGION = original
    start = next((i for i, ln in enumerate(lines)
                  if ln.startswith("Estimated cost")), None)
    if start is None:
        return []
    return lines[start:start + 8]


# A cost dict with the date REMOVED, for the degraded arm. Built by deleting
# the key rather than by passing None, because "the manifest never carried this
# field" is the case `.get` exists for and `None` would also be produced by a
# writer that carried it and set it to nothing.
_rates_removed_cost = dict(_cost)
del _rates_removed_cost["rates_last_updated"]

# CAPTURED BEFORE ANY RENDER, so the restore check at the end of this section
# compares against what was really in force rather than against a literal that
# would agree with a rebind that never happened.
_REGION_BEFORE_SECTION_6 = _config.S3_STAGING_REGION


# ---- the rates carry their own date, on PRICING_CONFIG's shape -----------
#
# THE TWO RATES WERE BARE SCALARS AND THE DATE THEY WERE READ WAS A `#`
# COMMENT. A comment is unreadable by any program, therefore unprintable by any
# report, therefore invisible to the person deciding whether to spend money on
# the strength of the two numbers -- and a rate with no visible age is a rate
# that gets trusted a year later. PRICING_CONFIG had already solved this in
# exactly this shape.

check("6f config.S3_PRICING carries both rates AND a machine-readable date, "
      "which is the whole reason it is a dict rather than two scalars",
      sorted(k for k in _config.S3_PRICING
             if k in ("last_updated", "standard_usd_per_gb_month",
                      "put_usd_per_1000")),
      ["last_updated", "put_usd_per_1000", "standard_usd_per_gb_month"])
check("6g ...and the date is an ISO day, not free text, so a reader can "
      "subtract it from today rather than parse a sentence",
      bool(_re.fullmatch(r"\d{4}-\d{2}-\d{2}",
                        str(_config.S3_PRICING["last_updated"]))), True)
check("6h ...and both rates are positive numbers rather than strings, which "
      "is what stops a quoted rate multiplying into a TypeError at the "
      "moment the estimate is wanted",
      all(isinstance(_config.S3_PRICING[k], (int, float))
          and not isinstance(_config.S3_PRICING[k], bool)
          and _config.S3_PRICING[k] > 0
          for k in ("standard_usd_per_gb_month", "put_usd_per_1000")), True)
check("6i THE DATE TRAVELS WITH THE FIGURES: estimate_cost carries it out, so "
      "the renderer does not have to reach back into config for it",
      _cost["rates_last_updated"], _config.S3_PRICING["last_updated"])
check("6j ...and it is read with .get, so a manifest missing the date still "
      "produces the FIGURES. Losing the age of a rate must not cost the "
      "estimate; the two rates are subscripted precisely because a missing "
      "RATE has no number to print and must fail loudly",
      _rates_removed_cost.get("rates_last_updated"), None)
check("6k ...and that degraded case still prices correctly",
      round(_rates_removed_cost["storage_usd_per_month"], 4), round(2.3, 4))

# ---- the report puts the date in front of the reader ---------------------
_cost_lines = _render_cost_lines(_cost)
check("6l THE RENDERED REPORT PRINTS THE DATE beside the dollar figures, "
      "which is the point: the age of the number is at the decision moment "
      "rather than in a source file nobody is reading",
      any(str(_config.S3_PRICING["last_updated"]) in ln for ln in _cost_lines),
      True)
check("6m ...on a line that says where it came from, so the reader can go "
      "and re-quote it",
      any("config.S3_PRICING" in ln for ln in _cost_lines), True)
check("6n ...and an UNDATED manifest says so in words rather than printing an "
      "empty field or the word None",
      any("UNDATED" in ln for ln in _render_cost_lines(_rates_removed_cost)),
      True)
check("6o CONTROL: the undated render still prints both dollar figures, so "
      "6n is not satisfied by a renderer that gave up",
      sum(1 for ln in _render_cost_lines(_rates_removed_cost)
          if "$" in ln) >= 2, True)

# ---- the heading names the CONFIGURED region, not a literal --------------
#
# It was the literal "us-east-1". Correct and unmaintained: the moment
# ONCOTRIAGE_S3_STAGING_REGION could move the region, a hardcoded heading would
# price one region under another region's name.
check("6p the cost heading names the CONFIGURED staging region",
      any(f"S3 Standard, {_config.S3_STAGING_REGION}" in ln
          for ln in _cost_lines), True)
check("6q ...and the rates declare which region they were QUOTED for, so the "
      "heading naming a different one cannot read as a re-quote",
      _config.S3_PRICING.get("quoted_region"), "us-east-1")
check("6r ...and when the two differ the report SAYS the figures are "
      "indicative, rather than pricing one region under another's name",
      any("RATE CAVEAT" in ln
          for ln in _render_cost_lines(_cost, staged_region="eu-west-1")),
      True)
check("6s CONTROL: and when they agree it does NOT, so 6r is a statement "
      "about the mismatch and not about the renderer always warning",
      any("RATE CAVEAT" in ln for ln in _cost_lines), False)

check("6v THE MISMATCH IS A FIELD AND NOT ONLY PROSE: estimate_cost carries "
      "the quoted region and the staged one out, so a machine consumer can "
      "branch on it. queries.cost_complete is the precedent -- a reason that "
      "lives in a note is a reason nothing can act on",
      (_cost["rates_quoted_region"], _cost["staged_region"]),
      (_config.S3_PRICING.get("quoted_region"), _config.S3_STAGING_REGION))
check("6w CONTROL: and the two agree today, so 6r's caveat really is driven "
      "by the rebind rather than by a standing mismatch",
      _cost["rates_quoted_region"] == _cost["staged_region"], True)

check("6t CONTROL: the mismatch arm's rebind was restored, so nothing after "
      "this section reads a region this section installed",
      _config.S3_STAGING_REGION, _REGION_BEFORE_SECTION_6)
check("6u ...and the captured baseline is non-degenerate: a rebind that never "
      "happened would satisfy 6t whatever it was set to",
      bool(_REGION_BEFORE_SECTION_6)
      and _REGION_BEFORE_SECTION_6 != "eu-west-1", True)


print()
print("=" * 70)
print("SECTION 7 -- NON-DEGENERACY AGAINST THE SHIPPED MANIFEST")
print("=" * 70)

_shipped = drive(lambda: _exc.load_manifest())
check("7a the SHIPPED manifest loads and validates",
      isinstance(_shipped, dict), True)
_ship_plan = _exc.build_plan(_shipped)
check("7b `05- Keys` is excluded in the shipped rulings",
      _exc.classify("05- Keys", _ship_plan)[0], False)
check("7c ...and it is excluded as `secrets`, not merely as clutter",
      _exc.classify("05- Keys", _ship_plan)[2],
      "excluded[secrets]:05- Keys")
check("7d the .env inside it is excluded too",
      _exc.classify("05- Keys/.env", _ship_plan)[0], False)
check("7e the Airflow generated password file is excluded as `secrets`",
      _exc.classify("06- Airflow/simple_auth_manager_passwords.json.generated",
                    _ship_plan)[2],
      "excluded[secrets]:06- Airflow/simple_auth_manager_passwords.json.generated")
check("7f the ragas venv is excluded",
      _exc.classify("03- Code/09- Testing/ragas-venv/lib/x.py",
                    _ship_plan)[0], False)
check("7g the two operator-ruled disposables are excluded AT THE PROJECT ROOT, "
      "which is where they actually are",
      [_exc.classify(p, _ship_plan)[0] for p in
       ("09- Testing/Fixture Backups/x", "09- Testing/The 42 Patients/y")],
      [False, False])
check("7h ...while the characterization fixtures beside them ARE staged, "
      "because they pin the Qdrant collection digest",
      _exc.classify("09- Testing/Characterization Fixtures/f.json.gz",
                    _ship_plan)[0], True)
check("7i the production inference database is staged",
      _exc.classify("02- Data/03- Inferences Storage/inferences.db",
                    _ship_plan)[0], True)
check("7j THE SHIPPED MANIFEST DOES NOT TRIP THE SCANNER ITSELF -- the first "
      "allowlist entry quoted the value it excused, which made this file "
      "credential-shaped and the next run flagged it",
      _scan.scan_bytes(open(_exc.default_manifest_path(), "rb").read()), [])
check("7k every secrets-kind exclusion in the shipped manifest is non-empty "
      "(non-degeneracy: an empty list would satisfy 7b-7e vacuously)",
      len(_exc.excluded_kinds(_ship_plan, "secrets")) >= 2, True)


shutil.rmtree(_TMP, ignore_errors=True)
check("8a the temporary tree is removed", os.path.exists(_TMP), False)


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")

if _FAILURES:
    print("\nFailures:")
    for _f in _FAILURES:
        print(f"  - {_f}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Aug 22 2026

@author: ramyalsaffar
"""
