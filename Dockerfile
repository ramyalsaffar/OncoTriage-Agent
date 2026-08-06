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
#      which has no such directory — the dependency list lives one level up, in
#      "07- Requirements". So `docker build` failed at that line on every
#      checkout this repository has ever had. It now copies
#      `requirements/requirements.txt`, which is IN the context, is
#      version-controlled, and lands at the path `oncotriage/paths.py` already
#      names for the container (`requirements_path` == "/app/requirements/").
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

# Copy requirements file
#
# ITEM 21: the source is `requirements/requirements.txt`, INSIDE the build
# context. It used to be `05-*Requirements/requirements.txt`, which matched
# nothing — see the header. The destination keeps the historical
# /app/requirements.txt for the pip invocation below; the copy that the
# container's `requirements_path` points at arrives with the code tree in
# STAGE 2, at /app/requirements/requirements.txt.
COPY --chown=appuser:appuser requirements/requirements.txt /app/requirements.txt

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
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r /app/requirements.txt


# ===========================================================================
# STAGE 2: Runtime - Minimal production image
# ===========================================================================
FROM python:3.11-slim@sha256:94c50be2dc994b873b55bc123e95e6dbade08095b3dfd790f51c34de3f08cbb7

# Set metadata for security scanning and compliance
LABEL maintainer="Ramy Alsaffar" \
      description="Clinical-Trial-Patient-Match - Clinical Trial Matching System" \
      version="1.0.0" \
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
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    bash \
    procps \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

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
# mounts a named volume there. MedCPT is ~110 MB and the FastEmbed BM25 model is
# fetched too; without a volume they would be re-downloaded on every
# `docker compose up` that recreates a container, on the first request, inside
# the request. HOME is still created and set — libraries other than these two
# reach for it, and a user without one produces failures far from their cause.
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
RUN mkdir -p \
    /app/data \
    /app/results \
    /app/checkpoint \
    /app/airflow_home \
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
     /usr/local/lib/oncotriage-docker/
RUN chmod 0755 /usr/local/bin/oncotriage-entrypoint

# Switch to non-root user for runtime
# CRITICAL: All processes run as appuser, not root
USER appuser

# Install the project itself.
#
# WHY HERE AND NOT IN THE BUILDER, which is where a wheel would normally be
# built: the builder stage copies only requirements.txt. It has no source to
# install. Copying the source into the builder as well and installing a wheel
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
# --no-deps:            pyproject.toml declares no dependencies by design (it
#                       says so and says why); requirements.txt above is the
#                       dependency list. This makes that explicit and keeps the
#                       install from reaching the network.
# --no-build-isolation: use the setuptools already in /opt/venv — upgraded in
#                       the builder for exactly this — instead of pip fetching a
#                       fresh build environment over the network at image-build
#                       time.
RUN pip install --no-deps --no-build-isolation --editable /app

# Expose ports (documentation only)
EXPOSE 8000 8501 8080

# Every service goes through the entrypoint, which creates the container's data
# directories before the service that needs them starts, then `exec`s the
# compose `command`. See docker/entrypoint.sh.
ENTRYPOINT ["/usr/local/bin/oncotriage-entrypoint"]

# Default command (overridden by docker-compose)
CMD ["python", "--version"]
