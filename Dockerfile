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
# Last updated: February 2026
# Base image security scan: docker scout cves python:3.11-slim
# ============================================================================

# ===========================================================================
# STAGE 1: Builder - Install dependencies as non-root in venv
# ===========================================================================
FROM python:3.11-slim@sha256:2bac43769ace90ebd3ad83e5392295e25dfc58e58543d3ab326c3330b505283d AS builder

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
COPY --chown=appuser:appuser 05-*Requirements/requirements.txt /app/requirements.txt

# Install Python dependencies as non-root user
# This is the CRITICAL security step - running as appuser prevents:
# 1. Malicious wheels from overwriting /usr/local/lib Python modules
# 2. Setup.py scripts from modifying system files
# 3. Dependency confusion attacks from escalating privileges
RUN pip install --upgrade pip && \
    pip install -r /app/requirements.txt


# ===========================================================================
# STAGE 2: Runtime - Minimal production image
# ===========================================================================
FROM python:3.11-slim@sha256:2bac43769ace90ebd3ad83e5392295e25dfc58e58543d3ab326c3330b505283d

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
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DOCKER_CONTAINER=true \
    PATH="/opt/venv/bin:$PATH"

# Install only runtime dependencies (no build tools)
# curl: needed for health checks
# ca-certificates: needed for HTTPS connections
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user (must match builder stage UID)
RUN groupadd -r appuser && \
    useradd -r -g appuser -u 1000 appuser

# Set working directory
WORKDIR /app

# Copy virtual environment from builder stage
# This is already owned by appuser from builder stage
COPY --from=builder --chown=appuser:appuser /opt/venv /opt/venv

# Create application directories with correct ownership
RUN mkdir -p \
    /app/data/patients/fhir \
    /app/data/trials \
    /app/results \
    /app/airflow_home/dags \
    /app/airflow_home/logs \
    && chown -R appuser:appuser /app

# Copy application code
# .dockerignore ensures only necessary files are copied
COPY --chown=appuser:appuser . /app/

# Switch to non-root user for runtime
# CRITICAL: All processes run as appuser, not root
USER appuser

# Expose ports (documentation only)
EXPOSE 8000 8501 8080

# Default command (overridden by docker-compose)
CMD ["python", "--version"]