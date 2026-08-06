#!/usr/bin/env bash
# ============================================================================
# Container entrypoint (item 21)
# ============================================================================
# Every service built from this repository's Dockerfile starts here. It makes
# the container's storage exist, then hands off to the compose `command`.
#
# WHY THIS EXISTS AT ALL
# ----------------------
# `oncotriage/paths.py` fixes fourteen absolute paths for the container
# (_DOCKER_PATHS) — /app/data/, /app/data/inferences.db, /app/results/,
# /app/checkpoint/ and the rest. Before item 21 the Dockerfile created three of
# them and docker-compose.yml then bind-mounted the host code directory over the
# whole of /app, which hid even those. Nothing declared a volume for data or
# results. So the API server resolved `inferences_path` to a file whose parent
# directory did not exist, and `log_inference` failed on the first request — as
# a caught, "non-critical" logging error, so the request still returned 200 and
# the row was simply gone.
#
# The named volumes in docker-compose.yml supply the storage; this script
# guarantees the directory structure inside it, on every start, before anything
# reads a path.
#
# WHY IT RUNS EVERY TIME rather than once at build: a named volume outlives the
# image. `docker compose down` keeps it, a new image version may add a path, and
# a volume that was created empty has no structure at all. Creating directories
# is idempotent and costs milliseconds.
#
# THIS SCRIPT IS COPIED TO /usr/local/bin/oncotriage-entrypoint AT BUILD TIME and
# is run from there, NOT from /app/docker/. docker-compose.yml bind-mounts the
# host code directory over /app, so a copy living under /app would be whatever
# is in the developer's working tree rather than what was built and tested.
# ============================================================================

set -euo pipefail

# Create every directory oncotriage/paths.py names for the container, derived
# from that table rather than repeated here. Failure is fatal ON PURPOSE: a
# service that starts without its storage produces exactly the silent data loss
# described above, and a container that refuses to start is the loud version of
# the same fact.
python /usr/local/lib/oncotriage-docker/prepare_paths.py

# Hand off to the compose `command`. `exec` so the service becomes PID 1 and
# receives SIGTERM directly — without it, `docker compose stop` would wait the
# full timeout and then kill the container, and Airflow and uvicorn would never
# run their shutdown paths.
exec "$@"
