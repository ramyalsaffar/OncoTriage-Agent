# OncoTriage Agent — make targets
#################################
#
# Only what needs a mechanism rather than a command line. Everything else is
# documented in CLAUDE.md and run directly.
#
# `serial-tests` exists because two tests mutate the source tree in place and
# three more read what those two write, so none of the five may overlap — see
# tests/run_serial_tests.py for the collision matrix, which pass 20d-2 DERIVED
# from the code rather than declaring. Before pass 20c-3b that fact was a
# warning paragraph in CLAUDE.md, which is followed by whoever read it and by
# nobody else.
#
# TWO OF THESE AT ONCE IS THE SAME DEFECT WITH NO HUMAN IN IT, which is what a
# Makefile target invites: `make serial-tests` on push, plus a developer running
# it locally, is two runs interleaving two backup/restore windows. Pass 20f-3
# gave the runner an flock, so the second one REFUSES with exit 3 and names the
# holder instead of silently reverting the first one's planted tree.

#
# `build` and `up` exist for ONE reason: the image's version label. A Dockerfile
# LABEL takes a literal or a build ARG, and nothing inside a Dockerfile or a
# compose file can read `oncotriage/__init__.py`. So the derivation happens
# HERE, once, and a `RUN --check` inside the build refuses an ARG that does not
# match the source. Without these targets a bare `docker compose build` fails
# rather than mislabelling the image; that is deliberate and is argued at the
# ARG in the Dockerfile.

PYTHON ?= python

# := not = : evaluate once when make starts, not once per use. With `=` this
# subshell would run again for every line that references it.
ONCOTRIAGE_APP_VERSION := $(shell $(PYTHON) docker/app_version.py)
export ONCOTRIAGE_APP_VERSION

.PHONY: help serial-tests serial-tests-list install version build up down

help:
	@echo "Targets:"
	@echo "  make serial-tests       run the five colliding tests in order, one at a time"
	@echo "  make serial-tests-list  print that order and why, run nothing"
	@echo "  make install            pip install -e . (makes 'oncotriage' importable)"
	@echo "  make version            print oncotriage.__version__ (the one declaration)"
	@echo "  make build              docker compose build, with the version label derived"
	@echo "  make up                 build, then docker compose up -d"
	@echo "  make down               docker compose down (volumes survive; add -v yourself)"

serial-tests:
	$(PYTHON) tests/run_serial_tests.py

serial-tests-list:
	$(PYTHON) tests/run_serial_tests.py --list

install:
	$(PYTHON) -m pip install -e .

version:
	@echo "$(ONCOTRIAGE_APP_VERSION)"

# The guard is inside the build; this only refuses to START a build with an
# empty value, which is what a broken docker/app_version.py would produce and
# which would otherwise reach the guard as a confusing empty-string mismatch.
build:
	@test -n "$(ONCOTRIAGE_APP_VERSION)" || \
		{ echo "make: docker/app_version.py produced no version; run it directly to see why"; exit 1; }
	docker compose build

up: build
	docker compose up -d

# NO `-v` HERE, EVER, and not for symmetry with `up`. `down -v` destroys the
# inference database, the Airflow metadata database with its generated password,
# and the Qdrant volume. A Makefile target one keystroke from `make down` is the
# wrong place to put it; type it yourself.
down:
	docker compose down
