# vLLM Ascend Main2Main Skill Usage

This skill finds vllm-ascend review candidates for a concrete vLLM old-to-new range. It does not turn mapping hits directly into code changes.

## Files

- `SKILL.md`: mandatory analysis and action-gate policy.
- `main2main_mapping_manifest.json`: authoritative inventory of production mapping tables and their roles.
- `main2main_risk_analyzer.py`: validation, prediction and retrospective evaluation.
- `scripts/optimize_mappings.py`: deterministic normalization and staging-output generator.
- `tests/test_optimize_mappings.py`: optimizer regression tests.
- `main2main_mapping_guide.md`: table semantics, evidence requirements and maintenance workflow.
- canonical JSON tables: method, field, call protocol, registration, inheritance and broad mapping.
- `raw/`: immutable pre-optimization inputs for reproducible regeneration; these files are not production mappings.
- `curated/`: source-SHA-pinned relationships newly proven from current inheritance, patch or binding analysis.

The shared repository engine, not these tables, owns dynamic exact downstream-call binding and return-protocol analysis. The legacy call-protocol JSON remains an expanded review asset.

Do not load mapping files by wildcard. PR-specific fixtures, archived tables and optimizer staging outputs are not production inputs unless the manifest explicitly selects them.

## Required anchors

Collect the following before running analysis:

```text
vLLM old SHA
vLLM new SHA
vllm-ascend review baseline SHA
version lane and lane anchor
optional upstream PR parent and merge/head SHA
```

Do not infer an unknown lane from dependency versions. For a PR analysis, the relevant change is its `parent -> merge/head` contract delta.

## 1. Validate mappings first

From the skill directory:

```powershell
python .\main2main_risk_analyzer.py validate `
  --vllm-root D:\path\to\vllm `
  --ascend-root D:\path\to\vllm-ascend
```

Validation must run on the exact repositories used for prediction. Do not promote stale, missing or unresolved relationships into the actionable set.

Validation should confirm, as applicable:

- direct/indirect base and MRO;
- actual override symbol;
- patch target, assignment and installer;
- canonical field owner and access type;
- callsite-to-effective-callee binding and signature;
- registration lifecycle and process/version conditions;
- manifest/table provenance and integrity.

## 2. Predict candidates

```powershell
python .\main2main_risk_analyzer.py predict `
  --vllm-root D:\path\to\vllm `
  --ascend-root D:\path\to\vllm-ascend `
  --old <old_vllm_sha> `
  --new <new_vllm_sha> `
  --output .\risk_report.md
```

Review results in this order:

Default `exact-contracts`:

1. verified override or runtime-patch input-contract deltas;
2. executable direct-import target deltas;
3. effective-callee call argument and downstream return-consumption deltas;
4. patch/override replacement-return deltas.

Only with explicit `expanded` review:

1. canonical-owner field contract deltas;
2. legacy static call-protocol leads;
3. registration closure for already verified candidates;
4. inheritance-only P2 candidates;
5. broad mapping appendix.

`same_name_protocol` is only a P2 `same_name_candidate`. A shared name or parallel module path does not prove a dependency.

## 3. Apply the action gate

For each candidate, record four booleans:

```text
relationship_verified
contract_changed
runtime_reachable
version_lane_matches
```

The only legal condition for `action=modify` is:

```text
relationship_verified
AND contract_changed
AND runtime_reachable
AND version_lane_matches
```

Any false or unknown value produces `review` or `dismiss`.

Examples:

| Candidate | Result |
|---|---|
| Ascend directly overrides a changed required parameter; path is active in the selected lane | `modify` |
| Upstream and Ascend functions have the same name but no caller/patch/inheritance relation | P2 `dismiss` |
| Ascend reads a field name, but the mapped upstream file only re-exports the owner | `review`; resolve canonical owner first |
| Upstream constructor adds a keyword, but the mapped Ascend member is not the effective callee | `dismiss` |
| A patch file is affected and its registry entry still loads it | registration closure `review`; no registry edit by default |

## 4. Evaluate against a PR or commit

```powershell
python .\main2main_risk_analyzer.py evaluate `
  --vllm-root D:\path\to\vllm `
  --ascend-root D:\path\to\vllm-ascend `
  --old <old_vllm_sha> `
  --new <new_vllm_sha> `
  --ascend-commit <ascend_commit_or_range>
```

File intersection is only a search-efficiency metric. A real hit should match:

```text
upstream PR/range + changed symbol/contract + Ascend relation + fix cause
```

Classify misses and non-actions rather than calling all unmatched files false positives. Useful labels include:

- required range adaptation;
- historical debt;
- forward backport;
- already compatible;
- unrelated cleanup;
- test-only or defensive coverage;
- intentionally unsupported;
- infrastructure/flaky;
- unknown.

## Mapping-table optimization

Optimization is a staging workflow; it does not silently rewrite production tables.

```powershell
python .\scripts\optimize_mappings.py `
  --mapping-dir . `
  --ascend-root D:\path\to\vllm-ascend `
  --vllm-root D:\path\to\vllm

python -m pytest -q .\tests\test_optimize_mappings.py

python .\scripts\optimize_mappings.py `
  --mapping-dir . `
  --ascend-root D:\path\to\vllm-ascend `
  --vllm-root D:\path\to\vllm `
  --check
```

The manifest selects an immutable `source_file`, optional verified `additional_source_files`, the published `file`, and an optimized staging output. The optimizer should:

- aggregate repeated source locations into `occurrences`;
- remove generic external/logging field noise;
- remove records whose upstream or Ascend files/method symbols are absent at the pinned source SHAs;
- downgrade weak same-name and unresolved relationships;
- preserve source SHA and optimization statistics;
- mark records with action, confidence and validation status;
- produce deterministic output, verified by `--check`.

After review, publish validated staging content to the canonical filenames named by the manifest. Then run `validate` again before `predict`.

## Table-specific rules

### Method

Direct overrides and proven runtime patches are strong relations. Verify MRO or the complete patch installation chain. Same-name candidates never pass relationship validation by name alone.

### Field

Resolve the fully qualified owner and distinguish read/write/mutate/construct/forward. Only owner schema changes count. Exclude `tl.*`, `triton.*`, `logger.*` and similar external API references.

### Call protocol

For default exact analysis, resolve the effective callee and bind the concrete downstream call separately at old and new. Check proven return consumption and patch/override return substitution as separate contract kinds. Literal argument expansion may be exact; dynamic `*args`/`**kwargs` remains unresolved. Never use a same-name match or historical `missing_keywords` snapshot as proof. Use the static call-protocol table only in expanded/legacy review.

### Registration

Use only as a review-only closure after another verified risk or a lifecycle change. Follow entrypoint to initializer, installer and target, including process and version guards.

### Inheritance and broad

Pure inheritance is P2. Inherited fields require field evidence. Broad file/symbol mappings belong only in the appendix and cannot create repair tasks.

## Expected report

Every reported candidate should include:

- exact upstream range/PR, changed file, symbol and contract;
- Ascend file/symbol and relationship evidence;
- contract kind and direction, including concrete call shape or return use when applicable;
- selected version lane and runtime path;
- all four action-gate values;
- `modify`, `review` or `dismiss`, with reason;
- CI job/test/log evidence when available.

Keep actionable adaptations separate from raw recall candidates. This prevents mapping noise from enlarging a main2main PR.
