# BASELINE SNAPSHOT — continuation 5, THIS SESSION

**This file is the durable baseline the handoff's §2c said was MISSING.**

It is THIS session's baseline, captured before any edit and before any test was
run. It is **not** a reconstruction of the historical session-start values that
were never written to disk: those remain MISSING and are not recoverable from
here. Where a value below happens to equal one recorded in-conversation by an
earlier session, that agreement is stated as an observation, never as evidence
that the earlier value was file-backed.

- Captured: 2026-09-12, session `90d88517-cb01-4501-a68a-72f1ed15dda4`
- Repo root: `/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code`
- HEAD: `679761f39949e3f91f740a3187723be917631a44`
- `git status --porcelain` entries: **54**

## Production artefacts (outside the repository)

Paths are relative to the PROJECT root (the parent of `03- Code`).

| artefact | sha256 | bytes | agrees with handoff §2c |
|---|---|---|---|
| `02- Data/03- Inferences Storage/inferences.db` | `47bab774e152a620d1e20b8ca331e083fcb2f2307807b18544baa9b5ba6a2389` | 1212416 | yes |
| `04- Results/02- Ablation/ablation_results.db` | `f2bc23c6566d2bba245b5af4d4828bdb727ce8e069d054f35a1cc45369f30eb6` | 237568 | yes |
| `09- Testing/Evaluation Runs/spend_journal.jsonl` | `a9682eb99c6ec94b84cd0164a11e15c58ebe1ecb13be977f65732a36eb75fc89` | 14789 (19 lines) | yes |

## Characterization fixtures — per-file (handoff §2c: MISSING, now captured)

Directory: `09- Testing/Characterization Fixtures/`

| file | sha256 | bytes |
|---|---|---|
| `ablation_bm25_only.json.gz` | `92799a40da680897ceb2853d85d2296cfcf46c79d8b95bba61112c319c576b33` | 256763 |
| `ablation_no_cross_encoder.json.gz` | `8ff4bd2e6b2619fe6c96a117e36ac3cb96b60bc33d9412128bfb9d761b922407` | 136799 |
| `ablation_vector_only.json.gz` | `e119635ad06ae16736562c3b53df18870451ecbbf745db5b99bc6f3d43800db2` | 139277 |
| `index.json` | `b00f371e791334eee3cf136cfa5e0528542fa1cba91c7e2dc109de4768beb0b8` | 4834 |
| `llm_classifier_parse_retry_constructed.json.gz` | `d0e06819b455562a53f654556b54f1b6ee003f2665b1643b4e7318dea0a9558c` | 231530 |
| `mcode_genomic_variant.json.gz` | `03aa7183c0eec13f854b171f2fa18ee6137e15254006cc392bdfc0e8fb82fe47` | 106091 |
| `mesh_fallback_siteless_code.json.gz` | `133211eb1d12828d0a88c985b71c88a7b913829b4c5d9ce7bcc91764b2d82de2` | 175764 |
| `no_candidates_pediatric_age.json.gz` | `b8b599d5e274e5da6323e64c43554f4d2d77f90f1573b993d9f7434f64a414fd` | 147581 |
| `normal_1.json.gz` | `8645749c568929634e5355f82c27a23a19a590524f873a412416d299c83c617c` | 251949 |
| `normal_2.json.gz` | `58a632b132e1ce41eb0e1522946687057a70161589f46093cc0aa798da6755e7` | 142761 |
| `normal_3.json.gz` | `f104365b093d342af0ad7e6c4a4a0a5a0b4de628ec7240fb1e026e44d313f2bc` | 216565 |
| `truncation_split.json.gz` | `932e067c6af01f70407c9a2c586f8bfe211329951cc81c8f1cf7cca63ed5ad76` | 144138 |
| `unknown_stage.json.gz` | `2461a3eee3589dc32a8c3568c25293aeb57e072be8e7183105508f34bf73b49b` | 304072 |

**Thirteen files, not twelve.** Twelve are the recorded fixtures; `index.json`
is the manifest beside them. The handoff says "twelve characterization
fixtures" and that is right about the FIXTURES; the directory holds thirteen
files and all thirteen are hashed here, because a manifest that moved while
twelve payloads did not is exactly the change a twelve-row table would miss.

## What this snapshot does NOT establish

- It does not establish that the earlier session's in-conversation values were
  correct. It establishes what is on disk NOW.
- It is not evidence of zero egress at any earlier point.
- `git status` is 54 here against the handoff's 53; the difference is the
  untracked `scratchpad/` directory, which the handoff file itself created.
  Verified by listing: 45 tracked-modified + 8 untracked files + `scratchpad/`.

## Regeneration

```
cd "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match"
for f in "02- Data/03- Inferences Storage/inferences.db" \
         "04- Results/02- Ablation/ablation_results.db" \
         "09- Testing/Evaluation Runs/spend_journal.jsonl"; do
  shasum -a 256 "$f"
done
find "09- Testing/Characterization Fixtures/" -type f | sort | xargs shasum -a 256
```
