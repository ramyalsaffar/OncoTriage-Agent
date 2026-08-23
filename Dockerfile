# ============================================================================
# Production-Grade Secure Docker Image
# ============================================================================
# Security-hardened multi-stage build with:
# - Non-root user for pip install (prevents wheel-jacking attacks)
# - Virtual environment isolation
# - Pinned base image digest for reproducibility
# - Vulnerability scanning integration
# - Supply chain attack mitigations
#
# Last updated: August 2026 (item 21 — Docker)
# Base image security scan: docker scout cves python:3.11-slim
#
# THE PINNED DIGEST DID NOT MATCH THE TAG BESIDE IT, and Docker uses the digest.
# `python:3.11-slim@sha256:2bac4376...` resolved to PYTHON 3.10.0 — checked by
# running it, after a container traceback showed /opt/venv/lib/python3.10/. So
# the line that the header calls "pinned base image digest for reproducibility"
# was reproducibly building on an interpreter two minor versions older than the
# one it advertised, and nothing anywhere would have said so. The digest below
# is `python:3.11-slim` as it actually resolves today, verified to report
# Python 3.11.15.
#
# The lesson generalises and is worth stating: a tag written next to a digest is
# a COMMENT. It is never checked. If you update one, run the image and confirm
# the other.
#
# ITEM 21 CHANGED FIVE THINGS. Each is argued where it happens; the summary:
#
#   1. The requirements COPY had NEVER MATCHED ANYTHING. It named
#      `05-*Requirements/requirements.txt`, and the build context is "03- Code",
#      which has no such directory — the dependency list lived one level up, in
#      "07- Requirements". So `docker build` failed at that line on every
#      checkout this repository has ever had. Item 21 pointed it at
#      `requirements/requirements.txt`, which was IN the context.
#      PASS 20f-2 SUPERSEDED THAT: there is one dependency list and it is
#      `pyproject.toml`'s `[project] dependencies`. This stage copies that file
#      alone and extracts the list from it; see STAGE 1.
#
#   PASS 20f-2's FOLLOW-UP HERE IS CLOSED. The LABEL `version="1.0.0"` in
#   STAGE 2 was a FOURTH version number beside `oncotriage.__version__` (2.0.0),
#   which the FastAPI app, GET /pipeline/info and `pip show oncotriage` all
#   derive from. That pass named the plumbing it would take — "an ARG here plus
#   a `build.args` entry in docker-compose.yml plus something to keep THAT in
#   step" — and all three now exist: `docker/app_version.py` is the something,
#   the Makefile and the compose file call it, and a `RUN --check` after the
#   source COPY fails the build if the ARG disagrees with the source. THE COUNT
#   OF HAND-MAINTAINED VERSION STRINGS IN THIS PROJECT IS ZERO. See the ARG at
#   the top of STAGE 2 for the trade that closing it cost.
#   2. The image never installed the package. It relied on `PYTHONPATH=/app`,
#      which is a different mechanism with different failure modes. It now does
#      an editable install; PYTHONPATH is gone. See STAGE 2.
#   3. `procps` and `bash` are installed, because the compose healthchecks need
#      them and the slim base has neither `pgrep` nor a shell with /dev/tcp.
#   4. setuptools is upgraded in the builder, because `pyproject.toml` declares
#      `requires = ["setuptools>=68"]` and the base image ships 57.5.0.
#   5. The base image digest, above.
# ============================================================================

# ===========================================================================
# STAGE 1: Builder - Install dependencies as non-root in venv
# ===========================================================================
#
# HOW DEBIAN SECURITY PATCHES ENTER THIS IMAGE: BY CHANGING THIS DIGEST, AND BY
# NOTHING ELSE THAT HAPPENS AT BUILD TIME.
#
# The digest fixes the content of all 111 OS packages at the moment it was
# published, and a pinned digest cannot receive Debian's later updates. There
# was an `apt-get upgrade` in STAGE 2 that reached the network at build time to
# close that gap; it is reverted, because it cleared zero findings (measured
# twice, 208 -> 208, argued at the line where it used to be) and it made two
# builds of one commit able to differ, which is exactly the property this pin
# exists to provide. Picking patches up therefore means editing the digest on
# BOTH FROM lines in the same commit, so the builder and the runtime stage stay
# the same Debian.
#
# The fixable-only Trivy gate in .github/workflows/ci.yml is what makes that
# non-optional rather than a chore somebody remembers: it fails on any HIGH or
# CRITICAL that Debian HAS released a fix for, so the day a fix lands, CI goes
# red and stays red until the digest moves.
#
# THAT HAS NOW HAPPENED ONCE, AND THIS PARAGRAPH USED TO SAY IT NEVER HAD. It
# read "Today it is green with 208 OS findings outstanding because Debian has
# fixed none of them" — true when written, and false in both halves now.
# RE-MEASURED 2026-08-20, Trivy 0.73.0, the FULL non-gating scan of the image
# `make build` produces:
#
#     all OS findings          248   (98 LOW, 88 MEDIUM, 58 HIGH, 4 CRITICAL)
#     OS findings WITH a fix    63   (9 LOW, 18 MEDIUM, 36 HIGH, 0 CRITICAL)
#     what the GATE sees        36   (fixable, HIGH or CRITICAL)
#     of those, unaccepted       0
#
# All 36 are the four util-linux ids of DSA-6442-1 spread across nine binary
# packages. Debian HAS fixed them, the gate turned RED exactly as this argument
# says it would, and it is green again only because those four ids are accepted
# TEMPORARILY in `.trivyignore` — no published base image carries the fix yet,
# measured there per digest. So "green" here now means "green with an expiry":
# `.github/scripts/trivyignore_staleness.py` fails CI the day an accepted id
# stops appearing in the scan, which is the day the acceptance is a lie.
#
# The four CRITICALs are still perl-base and Debian has still released no fix
# for any of them, which is what `--ignore-unfixed` is for.
#
# ANY DIGEST CHANGE MUST BE FOLLOWED BY RUNNING THE IMAGE AND READING THE
# INTERPRETER VERSION BACK:
#
#     docker run --rm <image> python --version        # expect Python 3.11.x
#
# This is the lesson recorded at the top of this file, and it is repeated here
# because this is the line it is about. A TAG WRITTEN NEXT TO A DIGEST IS A
# COMMENT — Docker resolves the digest and never checks the tag against it. The
# previous pin here read `python:3.11-slim@sha256:2bac4376...` and resolved to
# PYTHON 3.10.0, so a file whose own header advertised "pinned base image
# digest for reproducibility" was reproducibly building on an interpreter two
# minor versions older than it claimed. Nothing failed; it surfaced only from a
# container traceback naming /opt/venv/lib/python3.10/. The digest below is
# `python:3.11-slim` as it resolves today, verified by running it to report
# Python 3.11.15.
FROM python:3.11-slim@sha256:94c50be2dc994b873b55bc123e95e6dbade08095b3dfd790f51c34de3f08cbb7 AS builder

# Prevent Python from writing .pyc files and buffering
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install build dependencies
# Note: Combined in single RUN to reduce layers
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    git \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user BEFORE installing packages (security best practice)
# UID 1000 is standard for first non-system user
RUN groupadd -r appuser && \
    useradd -r -g appuser -u 1000 appuser

# Create virtual environment as root, then chown to appuser
# This prevents wheel-jacking attacks during pip install
RUN python -m venv /opt/venv && \
    chown -R appuser:appuser /opt/venv

# Switch to non-root user for pip install
# CRITICAL: Prevents malicious wheels from overwriting system files
USER appuser

# Activate virtual environment
ENV PATH="/opt/venv/bin:$PATH"

# Set working directory
WORKDIR /app

# Copy the dependency declaration
#
# ITEM 21 copied `requirements/requirements.txt` here. PASS 20f-2 DELETED THAT
# FILE: the dependency list lives in `pyproject.toml` now and there is exactly
# one of it. See the header of that file for why the merge happened and what
# happened to the `requirements/` directory.
#
# ONLY pyproject.toml LANDS IN THIS LAYER, and that is the whole reason the
# install below extracts the list instead of doing `pip install .`. The
# expensive layer here is torch; `pip install .` needs the source tree, so a
# one-character edit to any .py would invalidate the copy and re-download it.
# With only this file in the layer, the dependency install is re-run when — and
# only when — the dependency list changes. That is the property the separate
# requirements.txt copy existed for, kept without a second list.
COPY --chown=appuser:appuser pyproject.toml /app/pyproject.toml

# Install Python dependencies as non-root user
# This is the CRITICAL security step - running as appuser prevents:
# 1. Malicious wheels from overwriting /usr/local/lib Python modules
# 2. Setup.py scripts from modifying system files
# 3. Dependency confusion attacks from escalating privileges
#
# setuptools and wheel are upgraded alongside pip, and that is REQUIRED, not
# hygiene: `pyproject.toml` declares `requires = ["setuptools>=68"]`, and
# `python -m venv` on 3.11 seeds the interpreter's bundled setuptools, measured
# at 57.5.0 in this base image. STAGE 2 installs the package with
# `--no-build-isolation` (so the build reaches for no network), which means the
# backend it uses is THIS setuptools. Without the upgrade that install fails on
# the version floor.
#
# THE LIST IS READ OUT OF pyproject.toml WITH tomllib, which is standard
# library from Python 3.11 and this base image is python:3.11-slim — so nothing
# is installed in order to read the file that says what to install. It writes
# to /tmp rather than /app because /app is created by the WORKDIR above and is
# not owned by appuser, and a redirect into a root-owned directory fails at a
# point that reads like a dependency problem.
#
# IT MUST NOT SILENTLY INSTALL NOTHING. A pyproject.toml with a renamed or
# absent `[project] dependencies` key would make `d["project"]["dependencies"]`
# raise, which is the intended outcome — but a future edit reaching for
# `.get("dependencies", [])` would produce an empty file, a successful pip run,
# and an image whose imports all fail at runtime. The `assert` is the guard
# against that shape, and the floor is deliberately loose: it says "this is a
# real list", not a count that has to be maintained.
#
# setuptools AND wheel ARE UPGRADED AGAIN AFTER THE REQUIREMENTS INSTALL, and
# the repetition is deliberate. They are upgraded once BEFORE, so the build
# backend satisfies `requires = ["setuptools>=68"]`; the requirements install
# then resolves them back DOWN when a dependency's own metadata pins them, which
# is measured rather than theoretical -- the image shipped setuptools 70.3.0 and
# wheel 0.45.1 despite the earlier upgrade line. Trivy reported four HIGH
# findings against that pair and their vendored copies (CVE-2025-47273
# setuptools, CVE-2026-24049 wheel, CVE-2026-23949 jaraco.context and
# GHSA-6v7p-g79w-8964 msgpack, the last two vendored INSIDE them, which is why
# neither has its own dist-info in the venv). Floors rather than pins, because
# these are build tooling and nothing in this project imports them.
#
# THE `orchestration` EXTRA IS INSTALLED HERE TOO, AND IT HAS TO BE. Airflow
# moved out of the default dependencies, and docker-compose.yml runs
# `airflow-scheduler` and `airflow-apiserver` from THIS image — a
# default-only install leaves both services without the `airflow` binary, and
# they fail at start rather than at build, which is the expensive place to find
# it. Both keys are read with `[...]` and not `.get(...)`: a renamed extra must
# raise here, and the second `assert` catches the subtler case where the extra
# still exists but no longer carries Airflow.
RUN pip install --upgrade pip setuptools wheel && \
    python -c "import tomllib; \
p = tomllib.load(open('/app/pyproject.toml','rb'))['project']; \
deps = list(p['dependencies']); \
extra = p['optional-dependencies']['orchestration']; \
assert len(deps) > 10, f'pyproject.toml declared only {len(deps)} dependencies'; \
assert any(d.startswith('apache-airflow') for d in extra), 'the orchestration extra no longer carries apache-airflow'; \
open('/tmp/requirements.txt','w').write('\n'.join(deps + extra) + '\n')" && \
    cat /tmp/requirements.txt && \
    pip install -r /tmp/requirements.txt && \
    pip install --upgrade "setuptools>=78.1.1" "wheel>=0.46.2"


# ===========================================================================
# STAGE 2: Runtime - Minimal production image
# ===========================================================================
#
# THE SECOND HALF OF THE PIN. This digest and STAGE 1's must be the same value
# and must move together in one commit — the venv is built against STAGE 1's
# libc and copied into this stage, so two different Debians here is a runtime
# failure with no build-time symptom.
#
# Debian security patches are taken by editing these two lines, never by an
# `apt-get upgrade` below; the full argument, the measurement that removed that
# line, and the CI gate that forces the digest forward are at STAGE 1's FROM.
#
# AND THE SAME RULE APPLIES HERE: THE TAG BESIDE THE DIGEST IS NEVER CHECKED.
# `python:3.11-slim@sha256:2bac4376...` silently resolved to PYTHON 3.10.0 in
# this project, on this line as well as STAGE 1's. After any digest change, run
# the image and read the version back — `docker run --rm <image> python
# --version` — rather than trusting the tag you wrote beside it.
FROM python:3.11-slim@sha256:94c50be2dc994b873b55bc123e95e6dbade08095b3dfd790f51c34de3f08cbb7

# THE VERSION LABEL IS DERIVED, AND A WRONG ONE FAILS THE BUILD.
#
# Pass 20f-2 made oncotriage/__init__.py:__version__ the single declaration and
# left this label as a FOURTH number, "1.0.0", beside a package that said 2.0.0.
# It recorded the reason: a LABEL cannot read a Python attribute. That is still
# true, so the value arrives as a build ARG — and an ARG somebody has to supply
# is a fifth hand-maintained site unless two things are also true:
#
#   * something DERIVES it (docker/app_version.py, called by the Makefile and
#     interpolated into docker-compose.yml's build.args), and
#   * a wrong or absent value cannot ship (the RUN --check below).
#
# THE DEFAULT IS A SENTINEL THAT CANNOT PASS THE CHECK, deliberately. `unset` is
# not a version; a build that reaches the guard with it stops and names
# `make build`. The alternative — defaulting to the real number here — would put
# the literal back in this file, which is the entire defect.
#
# THE COST, STATED: a bare `docker compose build` now fails instead of silently
# labelling the image wrong. That is a real usability regression and it is the
# intended trade. `make build` and `make up` supply the arg; so does the
# one-liner the guard prints. A stale version label is the kind of thing nobody
# notices until it is being used to decide what is deployed.
ARG APP_VERSION=unset

# Set metadata for security scanning and compliance
LABEL maintainer="Ramy Alsaffar" \
      description="Clinical-Trial-Patient-Match - Clinical Trial Matching System" \
      version="${APP_VERSION}" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.source="https://github.com/ramyalsaffar/trialbridge-ai" \
      org.opencontainers.image.vendor="Ramy Alsaffar" \
      org.opencontainers.image.title="Clinical-Trial-Patient-Match" \
      org.opencontainers.image.description="AI-powered clinical trial patient matching" \
      security.scan="docker scout cves" \
      security.sbom="true"

# Runtime environment variables
#
# PYTHONPATH IS GONE, and its removal is the point of item 21's defect 6.
# `PYTHONPATH=/app` was the ONLY reason `import oncotriage` worked in the
# container: the image never installed the package. That is a second, invisible
# import mechanism — it puts the whole of /app on sys.path, so every numbered
# script and every stray .py beside them becomes importable as a top-level
# module, and `import config` or `import settings` in any dependency would
# resolve against this project's files before site-packages. The package is
# installed properly now (see below) and the variable is not needed.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=true \
    PATH="/opt/venv/bin:$PATH"

# Install only runtime dependencies (no build tools)
# curl: needed for health checks
# ca-certificates: needed for HTTPS connections
# bash: the entrypoint is bash, and the qdrant healthcheck in docker-compose.yml
#       needs a shell with /dev/tcp. Present in this base already; named so a
#       future slimmer base does not silently remove it.
# procps: `pgrep`, which the airflow-scheduler healthcheck used to call and
#       which THIS BASE IMAGE DOES NOT HAVE — measured, not assumed. The
#       healthcheck was therefore failing on every probe, forever. The compose
#       file now uses `airflow jobs check` instead, which is the authoritative
#       answer rather than a process-table guess, but procps stays because a
#       container you cannot run `ps` in is very hard to debug.
# THE DISTRIBUTION PACKAGES ARE INSTALLED AND NOT UPGRADED, AND THE `apt-get
# upgrade` THAT WAS HERE IS REVERTED.
#
# It was added on the argument that a digest pin cannot receive Debian's later
# security updates, which is true, and it was kept after its own measurement
# said it cleared nothing, which was the mistake. The measurement stands and is
# reproduced below; what changed is the conclusion drawn from it.
#
#     Trivy 0.73.0, debian 13.6, with the upgrade  ->  without it
#         all OS findings   208 (98 LOW, 79 MEDIUM, 27 HIGH, 4 CRITICAL)
#                        ->  208 (98 LOW, 79 MEDIUM, 27 HIGH, 4 CRITICAL)
#         OS findings WITH a fix   0  ->  0
#
# Identical, package for package. Every one of the 208 is a package Debian has
# not released a fix for, the four CRITICALs among them all perl-base
# (CVE-2026-13221, CVE-2026-42496, CVE-2026-57433, CVE-2026-8376), none with a
# fixed version in the Debian tracker. There was nothing for the line to take.
#
# ADDENDUM 2026-08-20, AND IT DOES NOT REOPEN THE ARGUMENT ABOVE. The
# measurement is left exactly as it was recorded — it is the evidence for the
# revert and rewording it would make it describe an experiment nobody ran — but
# read today one clause of it has stopped being true of the CURRENT scan, and a
# present tense left alone becomes a claim. Re-measured on the same image with
# the same Trivy 0.73.0: 248 OS findings, of which 63 DO have a fix (36 of them
# HIGH, all four util-linux ids of DSA-6442-1, published 2026-08-14). The four
# CRITICALs are unchanged: still perl-base, still unfixed.
#
# WHAT THAT DOES NOT MEAN. It is not "the upgrade would clear something today,
# so put it back" — the paragraph below is the answer to exactly that, and the
# route for these four is the digest, with a temporary acceptance in
# `.trivyignore` while no published base image carries the fix.
#
# WHAT IT COST, WHICH IS WHY "IT CLEARS NOTHING TODAY" IS NOT NEUTRAL. Both
# stages pin the base by sha256 digest. `apt-get upgrade` reaches the network
# at BUILD time and takes whatever Debian has published that day, so two builds
# of the same commit produce different package sets — the digest pin stops
# meaning what the header at the top of this file says it means, and the thing
# it stopped meaning is the only reproducibility guarantee the image has. A
# line that buys zero findings and sells that is a bad trade at any price.
#
# HOW DEBIAN SECURITY PATCHES ARE PICKED UP INSTEAD: BY RE-PINNING THE DIGEST
# at the two FROM lines. That is a reviewable, committed, single-valued change
# — one build of one commit gives one package set — and the fixable-only Trivy
# gate in .github/workflows/ci.yml is what makes it non-optional: the moment
# Debian publishes a fix for anything in this image, that gate turns red and
# stays red until the digest moves. See the note at the FROM lines for the
# check that must follow any digest change.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    bash \
    procps \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# THE BASE IMAGE'S OWN pip / setuptools / wheel ARE UPGRADED, and they are a
# different copy from the venv's.
#
# Nothing in this image runs them: `ENV PATH="/opt/venv/bin:$PATH"` puts the
# virtualenv first, and `which python` in the built image answers
# /opt/venv/bin/python. They sit at /usr/local/lib/python3.11/site-packages
# purely because the base image ships them — and Trivy scans the filesystem, not
# the PATH, so it reports them exactly like any other package.
#
# FOUR HIGHs CAME FROM HERE, and three of them from VENDORED copies nested
# inside those two packages, which is why upgrading the venv alone did not move
# them and why they have no dist-info of their own in site-packages:
#
#     CVE-2025-47273       setuptools                        -> 78.1.1
#     CVE-2026-24049       wheel                             -> 0.46.2
#     CVE-2026-23949       setuptools/_vendor/jaraco.context -> 6.1.0
#     GHSA-6v7p-g79w-8964  pip/_vendor/msgpack               -> 1.2.1
#
# The two vendored ones are fixed by taking a newer setuptools and pip rather
# than by touching them directly; a newer setuptools vendors a newer
# jaraco.context, and a newer pip a newer msgpack.
#
# `/usr/local/bin/python` IS SPELLED OUT rather than `python`, because at this
# point in the file PATH already prefers the venv and a bare `python` would
# upgrade the venv's copies a second time and leave these untouched — which is
# the mistake that made the first attempt at this look like it had worked.
#
# Floors, not pins: these are build tooling, nothing in this project imports
# them, and pinning them would create a fourth place versions have to be
# maintained.
RUN /usr/local/bin/python -m pip install --no-cache-dir --upgrade \
        pip "setuptools>=78.1.1" "wheel>=0.46.2"

# Create non-root user (must match builder stage UID)
RUN groupadd -r appuser && \
    useradd -r -g appuser -u 1000 appuser

# A HOME, AND A WRITABLE MODEL CACHE. Without these the pipeline gets to Stage 3
# and dies.
#
# `useradd -r` creates a SYSTEM user and does not create a home directory, so
# /home/appuser did not exist. huggingface_hub resolves its cache under $HOME,
# tries to create it, and cannot. A real POST /match in the container returned:
#
#     Pipeline error: PermissionError at /home/appuser when downloading
#     ncbi/MedCPT-Cross-Encoder. Check cache directory permissions.
#
# So the cross-encoder could never be downloaded and the reranking stage could
# never run — in any container, ever. Found by making the request, not by
# reading: nothing about the Dockerfile looks wrong, and every earlier stage
# works.
#
# The caches go in /opt/models rather than under $HOME because docker-compose.yml
# mounts a named volume there.
#
# HOW BIG, MEASURED rather than repeated. One cold load of both models in a
# fresh container leaves 876,997,657 bytes in /opt/models across 60 files:
# 876,969,919 of MedCPT (836.3 MiB) and 27,738 of FastEmbed BM25 (27 KB).
# Effectively all of it is the cross-encoder. That is NOT the ~110 MB this
# project carried from item 21 until the portability pass.
#
# WHY IT IS EIGHT TIMES THE OLD FIGURE, established by listing the blobs in a
# running container rather than inferred: the checkpoint publishes its weights
# in TWO formats under two snapshot revisions -- pytorch_model.bin
# (437,998,062 B) and model.safetensors (437,955,572 B) -- and one
# `from_pretrained` pulls both. Both blobs are present at those exact sizes in
# the host cache too, so the figure is a property of the checkpoint and not of
# either machine.
#
# QUOTE CONTENT BYTES, NEVER `du`. `du -sh` reports the FastEmbed cache as 84K
# on APFS and 104K on the container's overlay because it is 38 files of a few
# hundred bytes each and du counts allocated blocks. The first draft of this
# comment said "84 KB" for that reason and was wrong by 3x against the 27,738
# bytes both caches actually hold.
#
# WHAT THE VOLUME SAVES, MEASURED ON THIS MACHINE and stated as one machine's
# number because it is a function of the network: the cold load took 19.5 s
# (tokenizer 2.27 s, weights 15.78 s, BM25 0.78 s) and the same load after a
# container recreate took 2.71 s with the weights at 0.38 s -- a 41x difference
# on the weights, and it is paid INSIDE the first request. On a slower link the
# 837 MB is minutes rather than seconds; that end has not been measured here.
#
# The measurement and its argument live at oncotriage/paths.py, above
# `pin_model_cache`; this comment names the numbers because a Dockerfile cannot
# read a Python constant, and it names that owner so the two cannot drift
# silently.
#
# HOME is still created and set — libraries other than these two reach for it,
# and a user without one produces failures far from their cause.
ENV HOME=/home/appuser \
    HF_HOME=/opt/models/huggingface \
    FASTEMBED_CACHE_PATH=/opt/models/fastembed
RUN mkdir -p /home/appuser /opt/models/huggingface /opt/models/fastembed && \
    chown -R appuser:appuser /home/appuser /opt/models

# Set working directory
WORKDIR /app

# Copy virtual environment from builder stage
# This is already owned by appuser from builder stage
COPY --from=builder --chown=appuser:appuser /opt/venv /opt/venv

# Create application directories with correct ownership
#
# THESE ARE THE MOUNT POINTS, and creating them here is what gives a fresh named
# volume its ownership: Docker initialises an empty volume from the image
# content at the mount path, permissions and owner included. Without this, the
# volumes would be created root-owned and appuser could not write to them.
#
# THE MOUNT POINTS ARE EMPTY, AND THAT IS PASS 20g's FIX FOR AN INTERMITTENT
# CLEAN-BRING-UP FAILURE. This RUN used to create the nested tree as well —
# data/patients/fhir, data/trials, data/mesh, results/fhir_exploration,
# results/ablation, airflow_home/dags, airflow_home/logs — and Docker copies the
# image content at a mount path into a named volume THE FIRST TIME that volume
# is mounted. Five services mount /app/data and three mount /app/results, and
# `docker compose up` CREATES all of them at once, so on a genuinely empty
# volume several containers ran that copy concurrently. The copy is not
# serialized across containers, and it fails rather than tolerating the
# collision:
#
#     Error response from daemon: failed to mkdir
#     /var/lib/docker/volumes/Clinical-Trial-Patient-Match-app-results/_data/
#     fhir_exploration: file exists
#
# Measured on 2026-08-06, not inferred: one of four clean `down -v` + `up -d`
# cycles failed exactly this way and left three containers in Created and three
# never created. A second `up` always succeeded, because by then the volume was
# no longer empty and the copy was skipped — which is what made it look like a
# fluke rather than a defect.
#
# An EMPTY mount point has nothing to copy, so there is no concurrent mkdir and
# the failure mode does not exist. The volume root still gets its ownership from
# the directory here, which is the property this RUN is for. The nested tree is
# created by docker/prepare_paths.py, from the entrypoint, on every start —
# derived from oncotriage/paths.py:_DOCKER_PATHS, so it was already creating all
# seven of these directories and this list was a duplicate that could drift.
# `os.makedirs(..., exist_ok=True)` tolerates the concurrent case the volume
# copy did not.
#
# airflow_home/dags and airflow_home/logs are not in _DOCKER_PATHS and do not
# need to be: `write_dag_file()` does `dag_dir.mkdir(parents=True,
# exist_ok=True)` before writing, and Airflow creates its own log tree.
#
# /app/testing IS THE FIFTH, added by the portability pass. It is the container
# side of `testing_path` -- the Characterization Fixtures and Evaluation Runs
# trees, which used to be resolved by two private globs that INVENTED a
# directory when nothing matched. docker-compose.yml mounts the `app_testing`
# named volume here, for the reason every other volume exists: a containerised
# `python fixture_capture.py` is twelve real billed runs, and without a volume
# its output dies with the container.
RUN mkdir -p \
    /app/data \
    /app/results \
    /app/checkpoint \
    /app/airflow_home \
    /app/testing \
    && chown -R appuser:appuser /app

# Copy application code
# .dockerignore ensures only necessary files are copied
COPY --chown=appuser:appuser . /app/

# Container infrastructure, copied OUT of /app on purpose.
#
# These live in the repository under docker/ so they are reviewable and
# version-controlled, and they are copied to an image-only location so that a
# bind mount over /app — which docker-compose.yml no longer uses by default, but
# which anyone re-enabling development mode will add back — cannot replace the
# entrypoint with whatever happens to be in a working tree.
COPY --chown=appuser:appuser docker/entrypoint.sh /usr/local/bin/oncotriage-entrypoint
COPY --chown=appuser:appuser docker/prepare_paths.py docker/generate_dag.py \
     docker/app_version.py \
     /usr/local/lib/oncotriage-docker/
RUN chmod 0755 /usr/local/bin/oncotriage-entrypoint

# THE TWO MeSH LOOKUPS load_mesh_filter() REQUIRES, 105 KB, vendored.
#
# They go to an IMAGE-ONLY path and NOT to /app/data/mesh/, and that is not a
# preference. /app/data is a named volume, and Docker initialises a fresh volume
# by copying the image content at the mount path into it — concurrently, once
# per container, and the concurrent mkdir FAILS. That is the exact intermittent
# clean-bring-up failure pass 20g fixed by emptying these mount points; putting
# data back under one would put the race back with it.
#
# docker/prepare_paths.py:seed_mesh_core() copies them into the volume on every
# start instead: idempotent, never overwriting a file already there, and
# verified against docker/mesh-core/PROVENANCE.json before it writes. See
# docker/mesh-core/PROVENANCE.md for why these two and not the other three, and
# for the measurement that overturned DOCKER CLEAN BRING-UP.md §3's claim that
# this could not be done.
COPY --chown=appuser:appuser docker/mesh-core/ /usr/local/lib/oncotriage-docker/mesh-core/

# Switch to non-root user for runtime
# CRITICAL: All processes run as appuser, not root
USER appuser

# Install the project itself.
#
# WHY HERE AND NOT IN THE BUILDER, which is where a wheel would normally be
# built: the builder stage copies only pyproject.toml (requirements.txt before
# pass 20f-2). It has no source to install — deliberately, because that is what
# keeps a code edit from invalidating the torch layer. Copying the source into
# the builder as well and installing a wheel
# into /opt/venv is the other option the item named, and it was rejected for a
# specific reason rather than for effort — it produces TWO copies of the
# package: the wheel's, inside /opt/venv, and the tree's, at /app. Which one
# `import oncotriage` resolves would then depend on sys.path ordering, so the
# code that runs could differ from the code that is bind-mounted, edited and
# read. That is precisely the class of silent divergence this project exists to
# remove.
#
# An EDITABLE install has one copy by construction. setuptools writes a finder
# into site-packages pointing at /app/oncotriage, so:
#
#   * as shipped, /app is the tree COPY'd above and that is what runs;
#   * if someone re-adds a `.:/app` bind mount for development, /app becomes the
#     host tree and `import oncotriage` reaches the files they are editing —
#     still one copy, just a different one.
#
# One copy in both cases, and no PYTHONPATH. A non-editable wheel would have
# given two copies in the second case, resolved by sys.path order.
#
# --no-deps:            THE REASON CHANGED AT PASS 20f-2 AND THE FLAG DID NOT.
#                       It used to be "pyproject.toml declares no dependencies
#                       by design", which is no longer true — it declares all of
#                       them. It is now load-bearing in the opposite direction:
#                       STAGE 1 has already installed exactly that list into
#                       /opt/venv, and without this flag pip would re-resolve it
#                       here, over the network, in the runtime stage, at a point
#                       where the whole dependency layer was supposed to be
#                       settled. Same flag, same effect, a reason that is now
#                       the load-bearing one rather than a formality.
# --no-build-isolation: use the setuptools already in /opt/venv — upgraded in
#                       the builder for exactly this — instead of pip fetching a
#                       fresh build environment over the network at image-build
#                       time.
RUN pip install --no-deps --no-build-isolation --editable /app

# THE VERSION GUARD. See the ARG at the top of this stage for why it exists.
#
# It runs HERE, after `COPY . /app/`, because that is the first point at which
# /app/oncotriage/__init__.py — the single declaration — is in the image to be
# compared against. It reads the file as text rather than importing the package,
# so the check costs nothing and cannot be answered by a different copy on
# sys.path.
#
# A mismatch or the `unset` default fails the build and prints the command that
# supplies the value. The ARG is NOT re-declared here: it is declared once at the
# top of this stage and an ARG stays in scope for the rest of the stage it is
# declared in. A second bare `ARG APP_VERSION` would RESET it to empty when the
# build arg is not supplied, which would make the guard's message say `''`
# instead of `unset` — a worse diagnostic for the commonest failure.
RUN python /usr/local/lib/oncotriage-docker/app_version.py --check "${APP_VERSION}"

# Expose ports (documentation only)
EXPOSE 8000 8501 8080

# Every service goes through the entrypoint, which creates the container's data
# directories before the service that needs them starts, then `exec`s the
# compose `command`. See docker/entrypoint.sh.
ENTRYPOINT ["/usr/local/bin/oncotriage-entrypoint"]

# Default command (overridden by docker-compose)
CMD ["python", "--version"]
