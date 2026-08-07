# The two MeSH core lookups, and why they are the only data in this repository

`DOCKER CLEAN BRING-UP.md` §3 recorded three options for provisioning the MeSH
lookups and rejected the first one — "bake them into the image" — like this:

> Impossible without moving data into the repository: the build context is
> `03- Code`, the lookups are in a sibling `02- Data/*MeSH/`, and
> `.dockerignore` excludes `02- Data/` anyway. They are data, generated from two
> multi-hundred-megabyte source files, and they are not version-controlled here.

**Half of that is true and the half that decided it is not.** Measured on
2026-08-06, against the files themselves rather than against the sentence:

| File | Bytes | Built from | Required? |
|---|---:|---|---|
| `mesh_c04_lookup.json` | 52,883 | `desc2026.xml` | **YES — `load_mesh_filter()` raises without it** |
| `mesh_tree_to_name.json` | 54,399 | `desc2026.xml` | **YES — same raise** |
| `snomed_to_mesh_trees.json` | 162,103 | `MRCONSO_2025AB.RRF` | no — prints a `NOTE:` and continues |
| `icd10_to_mesh_trees.json` | 29,732 | `MRCONSO_2025AB.RRF` | no — same |
| `umls_synonym_to_mesh_trees.json` | 1,596,502 | `MRCONSO_2025AB.RRF` | no — same |

The two files that actually stop `POST /match` total **107,282 bytes**. The
"multi-hundred-megabyte" figure is the size of the SOURCES (`desc2026.xml` at
313 MB, `MRCONSO_2025AB.RRF` at 2.2 GB), and the argument carried it over to the
outputs without measuring them. 105 KB of derived lookup table is not the
category of thing that argument was rejecting.

What remains true in it, and is why these files are HERE rather than referenced:
the build context is `03- Code`, and Docker cannot read a sibling directory. So
baking them in means putting them in the context. That is what this directory
is.

## Why only these two

**Licensing, and it is the deciding reason rather than a size one.**
`desc2026.xml` is the NLM MeSH descriptor file, which NLM places in the public
domain and explicitly permits redistribution of. `MRCONSO_2025AB.RRF` is the
UMLS Metathesaurus, which is distributed under a licence agreement that
restricts redistribution of its content — and the other three files are
derived from it, row by row, including source vocabularies (SNOMED CT) with
their own affiliate licensing. `Dockerfile` STAGE 2 carries
`org.opencontainers.image.source="https://github.com/ramyalsaffar/trialbridge-ai"`,
so anything committed here is a candidate for public distribution.

Vendoring 105 KB of public-domain NLM lookup is a judgement call about repository
hygiene. Vendoring 1.8 MB of UMLS-derived crosswalk would be a judgement call
about somebody else's licence, and this is not the pass to make it in.

The practical consequence is small and is stated rather than hidden: without the
three optional files the patient side of the MeSH filter falls back from the
SNOMED/ICD-10/UMLS crosswalks to fuzzy descriptor matching. The filter is
conservative by design — unmappable on either side means KEEP — so the effect is
weaker precision in Stage 4, announced by three `NOTE:` lines from
`load_mesh_filter()` and by `MESH_FILTER_DEGRADATIONS`. It is not a silent
change, and `DOCKER CLEAN BRING-UP.md` §2a still carries the `docker compose cp`
step for anyone who wants them.

## The project rule this bends, and the project rule it obeys

CLAUDE.md says **"Data and keys live outside this folder."** It also says
**"Facts about an external standard (MeSH tree numbers, LOINC codes, FHIR
resource names) stay inline as named constants."** These two files are the
second rule at a size the first rule's authors were picturing when they wrote
it: `mesh_tree_to_name.json` is literally a table of MeSH tree numbers and their
descriptor names, which is exactly the category the second rule names — it is
simply too large to write as a `dict` literal in `config.py`.

They are here, under `docker/`, and not under `oncotriage/`, so that the bend is
confined to the directory whose entire purpose is container provisioning and
does not become package data with a wheel and a `package-data` entry behind it.

## Keeping them from going stale

`PROVENANCE.json` beside this file records the source, the builder and the
sha256 of each file. `docker/prepare_paths.py` verifies both hashes when it seeds
`/app/data/mesh` and **refuses to start the container** on a mismatch, so a
truncated copy or a half-finished edit is a named failure at bring-up rather
than a filter that silently classifies fewer trials.

To refresh them after a new MeSH release:

```bash
python "09- MeSH Cancer Site Relevance Filter.py"       # rebuilds all five
cp "../02- Data/04- MeSH/mesh_c04_lookup.json"   docker/mesh-core/
cp "../02- Data/04- MeSH/mesh_tree_to_name.json" docker/mesh-core/
shasum -a 256 docker/mesh-core/*.json                   # -> PROVENANCE.json
```
