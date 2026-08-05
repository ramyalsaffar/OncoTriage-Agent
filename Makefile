# OncoTriage Agent — make targets
#################################
#
# Only what needs a mechanism rather than a command line. Everything else is
# documented in CLAUDE.md and run directly.
#
# `serial-tests` exists because Files 42, 43, 44 and 47 mutate the source tree
# in place and CANNOT run concurrently — see run_serial_tests.py for the
# collision matrix, pair by pair. Before pass 20c-3b that fact was a warning
# paragraph in CLAUDE.md, which is followed by whoever read it and by nobody
# else.

PYTHON ?= python

.PHONY: help serial-tests serial-tests-list install

help:
	@echo "Targets:"
	@echo "  make serial-tests       run Files 42, 43, 44, 47 in order, one at a time"
	@echo "  make serial-tests-list  print that order and why, run nothing"
	@echo "  make install            pip install -e . (makes 'oncotriage' importable)"

serial-tests:
	$(PYTHON) run_serial_tests.py

serial-tests-list:
	$(PYTHON) run_serial_tests.py --list

install:
	$(PYTHON) -m pip install -e .
