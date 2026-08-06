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

PYTHON ?= python

.PHONY: help serial-tests serial-tests-list install

help:
	@echo "Targets:"
	@echo "  make serial-tests       run the five colliding tests in order, one at a time"
	@echo "  make serial-tests-list  print that order and why, run nothing"
	@echo "  make install            pip install -e . (makes 'oncotriage' importable)"

serial-tests:
	$(PYTHON) tests/run_serial_tests.py

serial-tests-list:
	$(PYTHON) tests/run_serial_tests.py --list

install:
	$(PYTHON) -m pip install -e .
