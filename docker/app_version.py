# The one version string, read without importing anything
#########################################################

"""Print ``oncotriage.__version__``, read out of the source file as TEXT.

Pass 20f-2 made ``oncotriage/__init__.py:__version__`` the single declaration of
this project's version: ``pyproject.toml`` takes it through
``[tool.setuptools.dynamic]``, which setuptools resolves from the AST at BUILD
time, and ``oncotriage/api/server.py`` reads the module attribute for both
``FastAPI(version=...)`` and ``GET /pipeline/info``. Three sites, one number.

IT LEFT A FOURTH AND SAID SO: ``LABEL version="1.0.0"`` in the Dockerfile's
STAGE 2, recorded in that file's header as a follow-up because "a Dockerfile
LABEL cannot read a Python attribute". That is true, and it is the whole problem
this module exists to work around. A LABEL takes a literal or an ``ARG``; an
``ARG`` takes whatever the builder is handed; and the only thing that can derive
a value and hand it to the builder is a step outside the Dockerfile. This is
that step, in one place, so the derivation is typed once rather than in the
Makefile AND in a compose file AND in whatever a human types.

WHY IT READS THE FILE INSTEAD OF IMPORTING THE PACKAGE. Two reasons, both hard:

  * ``make build`` runs on a HOST that may not have the package installed, and
    an ``import oncotriage`` there either fails or -- worse -- succeeds against
    some other copy on sys.path. Reading a known file by its path relative to
    this one cannot resolve to a different tree.
  * The same code runs INSIDE the image, where the guard compares the ARG it was
    handed against the source that was copied in. Importing there would work but
    would pull in the package's import chain to answer a question about one
    line of text.

WHY A REGEX AND NOT ``ast``. It is one assignment of a string literal at module
scope, and ``ast.parse`` on ``oncotriage/__init__.py`` reads a 200-line docstring
to find it. The pattern anchors on a line start, so a mention of ``__version__``
inside that docstring cannot match -- and the caller checks the result is a
plausible version rather than trusting the match, because a regex that finds the
wrong thing and a regex that finds nothing must not be the same outcome.
"""

import os
import re
import sys


_INIT_RELATIVE = os.path.join("oncotriage", "__init__.py")

_PATTERN = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.MULTILINE)

_PLAUSIBLE = re.compile(r"^\d+\.\d+")
"""What a version has to look like before this module will print it.

Deliberately loose -- it says "starts with two dotted numbers", not a full
semver grammar. The job is to reject a match that captured something that is not
a version at all (an empty string, a docstring fragment, a path), not to police
the numbering scheme. A tight grammar here would fail a legitimate ``2.0.0rc1``
and stop a build for a reason nobody could act on.
"""


def source_path(start=None):
    """Locate ``oncotriage/__init__.py``.

    Searched relative to this file's parent (the repository layout: this module
    is in ``docker/``, the package is its sibling) and then relative to the
    working directory, which is the order every entry point's six-line bootstrap
    already uses. Inside the image this module lives in
    ``/usr/local/lib/oncotriage-docker/`` with no sibling package, so the second
    candidate is the one that answers, from ``WORKDIR /app``.
    """
    candidates = []
    if start is not None:
        candidates.append(os.path.join(start, _INIT_RELATIVE))
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(os.path.dirname(here), _INIT_RELATIVE))
        candidates.append(os.path.join(os.getcwd(), _INIT_RELATIVE))
        candidates.append(os.path.join("/app", _INIT_RELATIVE))

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    raise RuntimeError(
        "cannot find oncotriage/__init__.py, which is where __version__ is "
        "declared. Looked in:\n  " + "\n  ".join(candidates)
    )


def read_version(path=None):
    """The declared version string.

    Raises:
        RuntimeError: the file has no module-scope ``__version__`` assignment,
            or the value does not look like a version. Both are raises rather
            than a default, because every consumer of this function stamps the
            result onto something -- an image label, a build guard -- and a
            plausible-looking wrong version is worse than a failed build.
    """
    if path is None:
        path = source_path()

    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()

    match = _PATTERN.search(text)
    if match is None:
        raise RuntimeError(
            f"no module-scope __version__ assignment in {path!r}. It is the "
            f"single declaration of this project's version; pyproject.toml's "
            f"[tool.setuptools.dynamic] reads the same attribute."
        )

    value = match.group(1).strip()
    if not _PLAUSIBLE.match(value):
        raise RuntimeError(
            f"__version__ in {path!r} is {value!r}, which does not look like a "
            f"version (expected it to start with N.N). Refusing to stamp it "
            f"onto an image label."
        )
    return value


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    try:
        version = read_version()
    except RuntimeError as exc:
        print(f"[app-version] {exc}", file=sys.stderr)
        return 1

    # `--check VALUE` is the build-time guard. It is a separate mode rather than
    # a second script because the comparison and the derivation must not be able
    # to disagree about where the version comes from.
    if argv and argv[0] == "--check":
        if len(argv) != 2:
            print("[app-version] usage: app_version.py [--check VERSION]",
                  file=sys.stderr)
            return 2
        supplied = argv[1]
        if supplied != version:
            print(
                f"[app-version] BUILD ARG APP_VERSION={supplied!r} does not "
                f"match oncotriage.__version__={version!r}.\n"
                f"  The image label would have advertised a version this build "
                f"is not. Build through the Makefile, which derives it:\n"
                f"      make build\n"
                f"  or supply it explicitly:\n"
                f"      ONCOTRIAGE_APP_VERSION=$(python docker/app_version.py) "
                f"docker compose build",
                file=sys.stderr)
            return 1
        print(f"[app-version] APP_VERSION={supplied} matches "
              f"oncotriage.__version__")
        return 0

    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
