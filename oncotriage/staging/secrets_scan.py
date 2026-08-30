# Staging Secrets Scan
######################

"""
A hard refusal, not a filter: if any file about to upload looks credential
shaped, the whole run stops.

WHY A REFUSAL AND NOT A FILTER. A filter answers "which of these files are
safe" and drops the rest. That is the wrong question, because the two answers
diverge exactly when the scanner is wrong about one file -- and a filter that
silently drops a false positive uploads everything else while an operator
believes the set is complete, whereas a filter that silently keeps a false
negative uploads a key. A refusal answers "is this whole set safe", which is the
question the operator actually has, and it is the answer that cannot be wrong
in the expensive direction without somebody noticing.

TWO INDEPENDENT LAYERS, EITHER OF WHICH REFUSES. Neither subsumes the other and
this tree contains a counterexample to each:

  CONTENT -- the value's own shape, anywhere in the bytes, with no regard for
      the syntax around it. `05- Keys/Keys.txt` is four lines of PROSE ("OpenAI
      ... sk-..."), not assignments, so every scanner built on parsing
      NAME=VALUE misses it entirely. Value-shape matching catches it.

  FILENAME -- what the file is called. `06- Airflow/
      simple_auth_manager_passwords.json.generated` is `{"admin": "<16 opaque
      chars>"}`. Sixteen characters under a key spelled `admin` is below every
      content heuristic that would not also fire on ordinary prose, so content
      matching alone would have uploaded a live Airflow admin password. The
      filename says what it is.

WHAT A FINDING NEVER CARRIES. The detector name, the file, the byte offset and
the matched LENGTH. Never the matched text, never a prefix of it, never a
redacted rendering with the shape preserved. A scanner that reports secrets to
prove it found secrets has moved them into a log file, and this project's logs
go to stderr and into `docker logs`.

AN UNREADABLE FILE IS A REFUSAL, not a skip. "I could not read this file" and
"this file is safe" are different statements and only one of them licenses an
upload. The remedy is one chmod or one exclusion entry, so it raises.

THE ALLOWLIST IS KEYED BY (path, sha256). A false positive is real -- these are
heuristics over 60 GB of third-party data -- so there has to be a way past one.
Keying on the CONTENT HASH as well as the path means an allowlisted file that
CHANGES stops being allowlisted, which is what stops the escape hatch becoming
a permanent hole under a path somebody later reuses.
"""

import hashlib
import os
import re

from oncotriage import config
from oncotriage.observability import console, get_logger

log = get_logger(__name__)


#------------------------------------------------------------------------------


class SecretsRefusal(RuntimeError):
    """Credential-shaped content survived the exclusion rules. Nothing uploads.

    A RuntimeError subclass rather than a ValueError, on this project's
    standing precedent (UnknownModelPricingError, IndexVerificationError,
    CrossEncoderLimitMismatchError): a broad ``except ValueError`` anywhere up
    the stack must not be able to turn this into a warning.
    """


# ---------------------------------------------------------------------------
# CONTENT DETECTORS
#
# Byte patterns, applied to a bounded prefix of every candidate file. Bytes
# rather than str because the prefix of a 21 MB FHIR bundle is not guaranteed
# to be decodable and a UnicodeDecodeError must not become a skipped file.
#
# Each entry is (name, compiled pattern, what it means). The name is what a
# finding reports; the pattern never appears in output.
# ---------------------------------------------------------------------------

_CONTENT_DETECTORS = (
    # `sk-` is short enough to occur inside ordinary words ("task-force"), so
    # the lookbehind requires the token to START. The length floor is what
    # separates a key from the literal string "sk-".
    ("openai_anthropic_key",
     re.compile(rb"(?<![A-Za-z0-9_\-])sk-[A-Za-z0-9_\-]{20,}"),
     "an 'sk-' prefixed API key (OpenAI / Anthropic shape)"),

    # `eyJ` is base64url for `{"`, i.e. the start of a JWT header. The Qdrant
    # Cloud API key in this project's own .env is exactly this shape. Both the
    # dotted three-segment form and a bare long token are matched, because a
    # detector that required the dots would miss any provider that ships one
    # segment.
    # THE DOT IS INSIDE THE CLASS, and the standing test is what found that it
    # had to be. Without it the match stops at the first segment boundary, so
    # the length floor was being applied to the JWT HEADER alone -- and a
    # header for a minimal {"alg":"HS256"} is 17 characters after `eyJ`, which
    # is under any floor worth having. The real Qdrant Cloud key in this
    # project's .env has a long enough header to match either way, so a fixture
    # copied from it would never have exposed this; the test's fabricated
    # minimal JWT did.
    ("jwt_or_qdrant_key",
     re.compile(rb"(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-\.]{20,}"),
     "a JWT / Qdrant Cloud API key shape"),

    ("aws_access_key_id",
     re.compile(rb"(?<![A-Za-z0-9])(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA)"
                rb"[A-Z0-9]{16}(?![A-Za-z0-9])"),
     "an AWS access key id"),

    ("private_key_block",
     re.compile(rb"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
     "a PEM private key block"),

    ("github_token",
     re.compile(rb"(?<![A-Za-z0-9_\-])(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}"
                rb"|github_pat_[A-Za-z0-9_]{30,}"),
     "a GitHub personal access token"),

    ("slack_token",
     re.compile(rb"(?<![A-Za-z0-9_\-])xox[baprs]-[A-Za-z0-9\-]{10,}"),
     "a Slack token"),

    ("google_api_key",
     re.compile(rb"(?<![A-Za-z0-9_\-])AIza[A-Za-z0-9_\-]{35}(?![A-Za-z0-9_\-])"),
     "a Google API key"),

    ("huggingface_token",
     re.compile(rb"(?<![A-Za-z0-9_\-])hf_[A-Za-z0-9]{30,}"),
     "a Hugging Face access token"),

    # THE ASSIGNMENT FORM IS THE LOWEST-PRECISION DETECTOR AND IS LAST. It is
    # what catches a provider this list has never heard of. The floor of 12 and
    # the restricted value charset are what keep it off ordinary prose; it is
    # still the detector most likely to produce a false positive, which is what
    # the (path, sha256) allowlist is for.
    ("credential_assignment",
     re.compile(rb"(?i)(?:api[_\-]?key|secret[_\-]?(?:key|token)?|"
                rb"access[_\-]?token|auth[_\-]?token|bearer[_\-]?token|"
                rb"password|passwd|credential)"
                rb"[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9+/=_\-]{12,})"),
     "a credential-shaped assignment"),
)


# The length above which the identifier exemption below STOPS APPLYING. See
# ``_is_program_identifier``; the constant is here rather than inside it so the
# bound is visible beside the detector table it moderates.
#
# MEASURED ACROSS THIS REPOSITORY, NOT CHOSEN. Three numbers set it, and the
# window between the first two is where it sits:
#
#   43  the longest DIGITLESS identifier anywhere in the tree
#       (`MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS`, and
#       `MATCHING_PER_TRIAL_PROMPT_CACHE_KEY_ENABLED` beside it). Zero
#       identifiers reach 44. The exemption must clear this or it starts
#       reporting the project's own constant names as credentials.
#   29  the longest value the detector has ever actually CAPTURED and this
#       function has exempted, over all 285 files
#       (`ci-placeholder-not-a-real-key`), with `ONCOTRIAGE_AIRFLOW_PASSWORD`
#       at 27 and `ONCOTRIAGE_BEDROCK_API_KEY` at 26 behind it. Twelve
#       distinct captures, none between 29 and the offender.
#   56  the length of the value that FORCED this cap: `docker-compose.yml`'s
#       hardcoded `AIRFLOW__WEBSERVER__SECRET_KEY`, letters, underscores and
#       hyphens, NO DIGIT, so the pre-cap exemption matched it exactly and the
#       scanner called the file clean. That was measured, not predicted --
#       `scan_bytes` over the real file returned zero findings.
#
#       THAT LITERAL IS GONE. The compose file sources both Airflow secrets
#       from the environment now (`${...:?}`), so `scan_bytes` over it returns
#       zero findings again -- for the opposite reason, and this window is
#       still the one that was measured. The 56 is kept as the provenance of
#       the ceiling, not as a claim about the tree today: the number that set
#       this bound has to survive the defect being fixed, or the next reader
#       has an unexplained constant.
#
# So the safe window is [44, 55] and this constant sits inside it with five
# characters of headroom over the longest name the project writes and eight
# below the offender. 40 -- the obvious round number -- would have been WRONG:
# six digitless identifiers in this tree are 41 characters or longer.
#
# WHICH WAY TO ERR, argued rather than assumed. A cap set too LOW reports a
# real identifier: the staging run refuses, an operator adds one allowlist row,
# and nothing leaves the machine. A cap set too HIGH exempts a real credential
# and it uploads. The two costs are not comparable, so the constant sits as
# close to its measured floor as the headroom argument allows rather than being
# centred in the window.
_IDENTIFIER_EXEMPTION_MAX_LENGTH = 48


def _is_program_identifier(value):
    """True when a captured value is a NAME rather than a secret.

    MEASURED, NOT CHOSEN. The first run of this scanner over the staged tree
    produced 17 findings and every one was a false positive; fifteen came from
    this detector matching source code that MENTIONS a credential rather than
    carrying one -- ``ONCOTRIAGE_BEDROCK_API_KEY``, ``_password_from_stdin``,
    ``get_openai_api_key``, ``ci-placeholder-not-a-real-key``. The captured
    values separate cleanly from the three real keys in this project's own
    .env on one axis:

        false positives   entropy 3.09 - 4.09 bits/char, NO DIGITS, all of them
        real keys         entropy 4.36 - 5.71 bits/char, digits + upper + lower

    So the rule is character CLASS, not entropy. A 0.27-bit gap between 4.09
    and 4.36 is not a threshold anyone should stake a credential on, while
    "letters, underscores and hyphens only, no digit anywhere" describes every
    identifier a programmer writes and no token any provider issues.

    THE RULE IS BOUNDED BY LENGTH, and it did not used to be. A capture longer
    than ``_IDENTIFIER_EXEMPTION_MAX_LENGTH`` is never exempt, whatever its
    shape. Everything at or below it is judged on character class exactly as
    before.

    WHY THE UNBOUNDED FORM WAS WRONG, measured in this repository rather than
    argued. This function's first version accepted a false negative it
    described as "about a 3% chance" for a 20-character base62 token -- true of
    a 20-character token and false as a general claim, because that probability
    is (52/62)^L and it is a statement about LENGTH. The tree contained the
    counterexample: `docker-compose.yml` SET `AIRFLOW__WEBSERVER__SECRET_KEY`
    to a 56-character literal of letters, underscores and hyphens with no
    digit. The detector matched it, this function exempted it, and
    ``scan_bytes`` over the real file returned zero findings -- a hardcoded
    signing key in a file the scanner called clean.

    PAST TENSE ON PURPOSE. That literal was removed once this cap made it
    visible; the compose file interpolates both Airflow secrets from the
    environment now. The counterexample is what the bound was measured
    against, and it stays written down here for that reason -- a bound whose
    reason has been deleted is a bound the next pass widens back.

    WHAT THE BOUND COSTS AND BUYS. The exemption still admits a digitless token
    at the cap, where (52/62)^L has fallen from 3% at twenty characters to
    about 0.02%; above the cap it admits none at all. The bound is not what
    makes this detector safe -- it is the LAST and lowest-precision of nine,
    and the eight above it match on issued prefixes (sk-, eyJ, AKIA, ghp_, xox,
    AIza, hf_, PEM) and none of them consults this function. What the bound
    does is stop the exemption growing without limit into exactly the region
    where a name stops being plausible and a secret starts.
    """
    # THE LENGTH TEST COMES FIRST, and it is a separate statement rather than a
    # clause of the return so that a reader sees TWO independent reasons a
    # value can fail to be exempt. It is also the cheap one -- it rejects
    # without running a regex over a long capture -- and it is the one that is
    # true regardless of shape, which is the whole point: above the cap there
    # is no shape that earns an exemption.
    if len(value) > _IDENTIFIER_EXEMPTION_MAX_LENGTH:
        return False
    # THE LEADING UNDERSCORE IS LOAD-BEARING and the first draft of this line
    # omitted it, so `_password_from_stdin` -- a private helper in
    # "24- Airflow Manager.py", and about as clearly a name as anything gets --
    # was still reported. Python spells private names with a leading
    # underscore; a rule about identifiers that cannot see one is a rule about
    # a language nobody writes.
    # NO DIGIT IN THE CHARACTER CLASS AT ALL, which is what makes this one
    # expression say both halves of the rule: identifier-shaped AND digitless.
    # The first attempt was `fullmatch(...) is None or not any(digit)`, which
    # is not the same predicate -- it returns True for a value that matches
    # NOTHING (binary noise, `+++===`), i.e. it would have REJECTED the hit and
    # let an unrecognisable high-entropy blob through. Inverting the safe
    # direction is the one mistake a secrets scanner may not make, so the
    # predicate is written as the single positive statement it is.
    return re.fullmatch(rb"[A-Za-z_][A-Za-z_\-]*", value) is not None


# Which detectors validate their capture group before reporting. A dict rather
# than a fourth tuple member so the table above stays readable and so the
# absence of an entry means "no validator", which is the common case.
_VALUE_VALIDATORS = {
    "credential_assignment": _is_program_identifier,
}


# Extensions whose FILENAME is not evidence. A module called `secrets_scan.py`
# is a module; a file called `secrets.json` is a store. Source code that holds
# a hardcoded key is caught by the CONTENT layer, which is the right layer for
# it -- so exempting source from the filename layer costs no coverage.
#
# MEASURED: the first run of this scanner flagged its own source file,
# oncotriage/staging/secrets_scan.py, under `secret_file`. A scanner that
# refuses to upload itself is a scanner nobody can use.
_SOURCE_EXTENSIONS = frozenset({
    ".py", ".pyi", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".c", ".h",
    ".cc", ".cpp", ".hpp", ".rs", ".rb", ".sh", ".bash", ".zsh", ".ipynb",
})


# ---------------------------------------------------------------------------
# FILENAME DETECTORS
#
# Applied to the BASENAME. This layer exists for the file whose content is
# unremarkable and whose name is the only thing that says what it holds.
# ---------------------------------------------------------------------------

_FILENAME_DETECTORS = (
    # ONE (?i) AND IT IS AT THE START. Python rejects an inline global flag
    # anywhere else ("global flags not at the start of the expression"), and
    # the first draft of this line carried two -- caught at import, which is
    # the cheapest place a regex defect can be caught.
    # Catches `.env`, `.env.local`, `.env.production` and `prod.env`.
    ("dotenv_file", re.compile(r"(?i)^\.env(\..*)?$|\.env$"),
     "a .env file"),
    ("password_file", re.compile(r"(?i)password"),
     "a filename naming a password"),
    ("secret_file", re.compile(r"(?i)(^|[._\-])secrets?([._\-]|$)"),
     "a filename naming a secret"),
    ("credential_file", re.compile(r"(?i)^credentials?(\..+)?$"),
     "a credentials file"),
    ("key_material_file", re.compile(r"(?i)\.(pem|p12|pfx|jks|keystore)$"),
     "a key-material file extension"),
    ("private_key_file", re.compile(r"(?i)^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$"),
     "an SSH private key filename"),
    ("keys_document", re.compile(r"(?i)^keys?\.(txt|json|ya?ml|md|csv)$"),
     "a document named 'keys'"),
)


#------------------------------------------------------------------------------


class Finding:
    """One reason one file may not upload. Carries no secret material.

    Deliberately not a dataclass -- check 2i of
    tests/test_package_invariants.py pins the exact decorator list of every
    definition in the package.
    """

    __slots__ = ("relpath", "detector", "layer", "meaning", "offset", "length")

    def __init__(self, relpath, detector, layer, meaning, offset, length):
        self.relpath = relpath
        self.detector = detector
        self.layer = layer          # "content" | "filename" | "unreadable"
        self.meaning = meaning
        self.offset = offset        # byte offset, -1 for filename findings
        self.length = length        # matched length, -1 for filename findings

    def describe(self):
        """One line for an operator. NEVER includes the matched bytes."""
        where = (f"byte {self.offset}, {self.length} chars"
                 if self.offset >= 0 else "filename")
        return (f"{self.relpath}\n"
                f"        {self.layer}/{self.detector}: {self.meaning} "
                f"({where})")


def scan_bytes(blob):
    """Content findings in one byte string, as ``(detector, offset, length)``.

    Pure, and the natural control for a pure function is a different input --
    which is why the standing test drives this directly with fabricated blobs
    rather than planting into a copy of the module.
    """
    hits = []
    for name, pattern, _meaning in _CONTENT_DETECTORS:
        validator = _VALUE_VALIDATORS.get(name)
        for match in pattern.finditer(blob):
            if validator is not None:
                # The validator answers "is this capture a NAME rather than a
                # secret", so a True REJECTS the hit.
                captured = match.group(1) if match.groups() else match.group(0)
                if validator(captured):
                    continue
            hits.append((name, match.start(), len(match.group(0))))
    return hits


def scan_filename(basename):
    """Filename findings for one basename, as detector names.

    Source files are exempt from THIS layer only -- see _SOURCE_EXTENSIONS.
    Their content is still scanned by every content detector.
    """
    if os.path.splitext(basename)[1].lower() in _SOURCE_EXTENSIONS:
        return []
    return [name for name, pattern, _meaning in _FILENAME_DETECTORS
            if pattern.search(basename)]


def _meaning(table, name):
    for entry_name, _pattern, meaning in table:
        if entry_name == name:
            return meaning
    return name


def sha256_file(path, chunk=1 << 20):
    """Full-content sha256. Used by the allowlist and by the spot verification.

    ``chunk`` (1 MiB) IS A READ BUFFER AND NOT A SCAN BOUND, and the two are
    named apart here because they look alike and one of them is a security
    property. ``config.S3_STAGING_SCAN_PREFIX_BYTES`` decides HOW MUCH OF A
    FILE IS EXAMINED -- raise it and the scanner sees more, lower it and a
    credential can hide past the bound -- which is why it is a tunable in
    config.py with a stated limit beside it. This number decides only how many
    bytes cross the read boundary at a time on the way to the SAME digest.
    Every byte of the file is hashed at any value of it; changing it moves
    memory against syscalls and nothing else, so it is a local default rather
    than a knob an operator is invited to tune. 1 MiB is the ordinary value for
    this idiom, chosen for that reason and calibrated against nothing.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def scan_file(abspath, relpath, prefix_bytes=None):
    """Every finding for one file. Raises OSError to the caller on read failure.

    Only a bounded PREFIX is read. That is a stated limit rather than a
    guarantee: a key pasted 100 KB into a 21 MB FHIR bundle is not seen. The
    bound is what makes scanning ~60 GB take seconds instead of an hour, and it
    is a tunable (``config.S3_STAGING_SCAN_PREFIX_BYTES``) so the trade can be
    changed without editing this file.
    """
    limit = (config.S3_STAGING_SCAN_PREFIX_BYTES if prefix_bytes is None
             else prefix_bytes)
    # 0 MEANS THE WHOLE FILE, and this line is the fix for a documentation bug
    # that would have silently disabled the scanner. The tunable's comment says
    # "raising it to 0 would mean read the whole file", which was simply false:
    # `handle.read(0)` returns b"" and every file would have scanned clean
    # while the run reported the same "VERDICT: CLEAN" it reports when the scan
    # actually ran. A knob whose extreme value turns the guard off without
    # saying so is worse than no knob.
    if limit == 0:
        limit = -1                      # read() semantics: -1 is "everything"
    findings = []

    for detector in scan_filename(os.path.basename(relpath)):
        findings.append(Finding(relpath, detector, "filename",
                                _meaning(_FILENAME_DETECTORS, detector), -1, -1))

    with open(abspath, "rb") as handle:
        blob = handle.read(limit)
    for detector, offset, length in scan_bytes(blob):
        findings.append(Finding(relpath, detector, "content",
                                _meaning(_CONTENT_DETECTORS, detector),
                                offset, length))

    return findings


class ScanResult:
    """What the scan found, and whether the upload may proceed."""

    __slots__ = ("findings", "allowlisted", "unreadable", "files_scanned",
                 "bytes_read")

    def __init__(self):
        self.findings = []       # [Finding] -- these REFUSE
        self.allowlisted = []    # [(relpath, detector, reason)] -- these do not
        self.unreadable = []     # [(relpath, error)] -- these REFUSE
        self.files_scanned = 0
        self.bytes_read = 0

    def clean(self):
        return not self.findings and not self.unreadable


def scan_files(entries, plan, prefix_bytes=None, progress_every=25000):
    """Scan every candidate. Returns a ScanResult; never raises on a finding.

    ``entries`` is an iterable of ``(abspath, relpath)``. The caller decides
    what to do with a dirty result -- ``refuse_if_dirty`` is that decision, kept
    separate so a report can show the findings before anything raises.

    ``progress_every`` (25,000) IS A CONSOLE CADENCE AND DECIDES NOTHING ABOUT
    THE SCAN, which is the same distinction ``sha256_file``'s ``chunk`` draws
    and worth drawing again because this file now carries three numbers that a
    reader could mistake for one kind. Every entry handed in is scanned at any
    value of it, ``None`` or ``0`` prints no progress at all, and the
    ScanResult is identical either way. It exists so a run over ~200,000 files
    is not a silent several-minute pause. Chosen so a full staged tree emits a
    handful of lines rather than one or hundreds; calibrated against nothing,
    and deliberately NOT a config tunable, because config.py's own rule is that
    a tunable there has an effect and this one has no effect on any output the
    project keeps.

    THE ONE ARGUED BOUND IN THIS FILE IS ``config.S3_STAGING_SCAN_PREFIX_BYTES``
    -- see ``scan_file``. That one is a security property with a stated limit;
    these two are not, and conflating them is how a security bound gets tuned
    by somebody who thought they were adjusting a buffer.
    """
    result = ScanResult()

    for abspath, relpath in entries:
        result.files_scanned += 1
        if progress_every and result.files_scanned % progress_every == 0:
            console.out(f"    ...scanned {result.files_scanned:,} files")

        try:
            findings = scan_file(abspath, relpath, prefix_bytes=prefix_bytes)
            result.bytes_read += min(
                os.path.getsize(abspath),
                config.S3_STAGING_SCAN_PREFIX_BYTES if prefix_bytes is None
                else prefix_bytes)
        except OSError as exc:
            result.unreadable.append((relpath, f"{type(exc).__name__}: {exc}"))
            continue

        if not findings:
            continue

        # An allowlist entry excuses a file only while its CONTENT is unchanged.
        # The hash is computed only for files that already have a finding, so
        # the cost is paid on the handful of hits rather than on every file.
        # EVERY ALLOWLIST ROW FOR THIS PATH IS CONSIDERED, not just the first.
        # The first version broke out of the loop as soon as a row's PATH
        # matched, so a manifest carrying two hashes for one path -- which is
        # exactly what a transition looks like, the outgoing content and the
        # incoming one -- could only ever consult whichever `dict` iteration
        # order put first, and the other row was dead. Nothing validates
        # against duplicate allowlist paths, so that was reachable.
        #
        # The hash is computed ONCE and only for a file that already has a
        # finding, so the cost is paid on the handful of hits rather than on
        # every file in a 64 GB walk.
        candidates = [(sha, reason)
                      for (allow_path, sha), reason in plan.allowlist.items()
                      if allow_path == relpath]
        excuse = None
        if candidates:
            try:
                actual = sha256_file(abspath)
            except OSError as exc:
                result.unreadable.append((relpath, f"{type(exc).__name__}: {exc}"))
                excuse = "unreadable"
            else:
                for sha, reason in candidates:
                    if actual == sha:
                        excuse = reason
                        break

        if excuse and excuse != "unreadable":
            for finding in findings:
                result.allowlisted.append((relpath, finding.detector, excuse))
        elif excuse != "unreadable":
            result.findings.extend(findings)

    return result


def refuse_if_dirty(result):
    """Raise SecretsRefusal unless the scan came back clean.

    Separate from ``scan_files`` so a caller can print the whole finding list
    before the raise. A refusal that names one file when six are dirty sends an
    operator round the loop six times.
    """
    if result.clean():
        log.info("staging_secrets_scan_clean")
        return

    lines = [
        "REFUSING TO UPLOAD: credential-shaped files survived the exclusion "
        "rules.",
        "",
        "Nothing was uploaded. This is a refusal, not a filter -- the run stops "
        "rather than",
        "dropping the offending files and continuing, because a set that is "
        "missing files an",
        "operator believes are present is its own failure.",
        "",
    ]
    if result.findings:
        lines.append(f"  {len(result.findings)} finding(s):")
        for finding in result.findings:
            lines.append("      " + finding.describe())
        lines.append("")
    if result.unreadable:
        lines.append(f"  {len(result.unreadable)} unreadable file(s) -- "
                     f"'could not read' is not 'is safe':")
        for relpath, error in result.unreadable:
            lines.append(f"      {relpath}\n        {error}")
        lines.append("")
    lines += [
        "  To resolve, one of:",
        "    - add an exclusion entry to s3_staging_exclusions.json (with a "
        "reason), or",
        "    - add a (path, sha256, reason) row to its secret_scan_allowlist "
        "if this is a",
        "      false positive. The hash is part of the key, so the excuse "
        "expires when the",
        "      file changes.",
    ]
    log.error("staging_secrets_scan_refused")
    raise SecretsRefusal("\n".join(lines))
