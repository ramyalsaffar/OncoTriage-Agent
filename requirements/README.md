# There is no requirements.txt here any more

**The dependency list is [`pyproject.toml`](../pyproject.toml)'s
`[project] dependencies`, and it is the only one.** Pass 20f-2 deleted
`requirements/requirements.txt` and merged its thirty pins, and every argument
those pins carried, into that file.

## Install

```bash
pip install -e .        # from "03- Code/" — deps come from pyproject.toml
```

`pip install -e .` used to give you an importable package whose imports all
failed, because `pyproject.toml` deliberately declared no dependencies. It does
not any more.

## What the Dockerfile does

It copies **`pyproject.toml` alone** into the builder stage and extracts the
list with `tomllib` (standard library on the image's Python 3.11), then
`pip install -r` that. Copying one small file rather than the source tree is
what keeps a one-character code edit from invalidating the layer that downloads
torch — the same property the separate `requirements.txt` copy existed for,
without a second list. The runtime stage then installs the package itself with
`--no-deps --editable`.

## Why this directory still exists

`oncotriage/paths.py` defines `requirements_path`, and `_DOCKER_PATHS` maps it
to `/app/requirements/`. Deleting the directory would leave a path variable
naming nothing and would change the container's fourteen-path bring-up report
in `docker/prepare_paths.py`, `DOCKER CLEAN BRING-UP.md` and `CLAUDE.md`.

**Recorded as a follow-up, with the whole edit:** drop `requirements_path` from
both tables in `oncotriage/paths.py`, drop this directory, and correct the
"fourteen" in the two documents. It is a paths decision rather than a dependency
one, which is why pass 20f-2 did not make it.

## The stale sibling

`{project root}/07- Requirements/requirements.txt` — outside the repository, not
version-controlled, and read by nothing. `requirements_path` resolves to that
directory on the development machine by glob prefix, and no code reads the
variable. It was never updated by pass 20f-2 and it should not be trusted.
