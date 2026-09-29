# CLI and output reference

## Contents

- Engine selection
- Cache
- Commands
- Outputs
- Failure behavior

## Engine selection

Pass `--engine-root` when the analyzed vllm-ascend baseline is older than the shared engine. Without it, the adapter searches `--ascend-root`. The selected root must contain `tools/vllm_interface_contracts`.

Use `--engine-mode new|compare|legacy`. Default to `new`; use `compare` only for migration parity checks and `legacy` only to reproduce historical results.

New-engine output currently uses range report `schema_version=3` with `metadata.range_analyzer_version=1.2.1` and fixed analysis-plan version 2. The underlying relation generator remains `0.36.0` with JSONL schema 6. Range schema 3 adds the selected scenario, capability execution states, and phase timings on top of schema 2 contract fields. Analyzer/schema/plan versions are part of the cache identity. Legacy output in `legacy` or the legacy side of `compare` does not acquire this schema merely by being wrapped by the skill.

The adapter always passes `--scenario main2main`. That fixed plan runs patch, override, inheritance, direct-import, direct-call, return-protocol, and generator-finding analysis. The repository CLI separately exposes `--scenario vllm-interface` for upstream PR awareness; that plan runs direct-import, override, and direct-call analysis while excluding monkey patches. It is not a skill engine mode and does not replace `new|compare|legacy`.

## Cache

Use `--cache-dir` to choose the cache root. Otherwise place `.main2main-cache` beside `--output`, or in the current directory when no output is supplied.

The cache key includes:

- engine package SHA-256, generator version, range analyzer version, range schema version, and analysis-plan version;
- exact vLLM old/new SHAs;
- exact vllm-ascend SHA;
- fixed `main2main` scenario, profile, and external-source SHAs.

Use `--refresh-cache` to force regeneration. A cache hit must not rerun repository AST analysis.

## Commands

- `validate`: regenerate the current dependency graph, validate exact current call/return contracts, and report generator plus contract finding counts.
- `predict`: analyze old-to-new and render the range report.
- `evaluate`: compare introduced-break downstream files with an actual vllm-ascend adaptation commit.

All commands accept `--engine-root`, `--engine-mode`, `--cache-dir`, `--refresh-cache`, `--expect-ascend-sha`, repeated `--external-root PACKAGE=PATH`, and matching `--expect-external-sha PACKAGE=SHA` where applicable.

## Outputs

Each new-engine cache entry contains:

- `main2main-range-report.json`: complete machine-readable metadata, gates, endpoints, compatibility states, evidence, and suggestions;
- `main2main-range-report.md`: concise Chinese review report;
- `main2main-introduced-breaks.csv`: only upgrade-introduced breaks;
- `main2main-all-findings.csv`: warnings, historical issues, fixes, and unresolved items;
- `cache-inputs.json`: exact inputs used to validate cache reuse;
- `new-vs-legacy.json` and `legacy-output.md` in compare mode.

Every finding records old/new upstream endpoints, downstream endpoint or callsite, source lines, relationship type, `contract_kind`, direction, compatibility transition, four action gates, confidence, action, and modification suggestion. Contract kinds include `call_arguments`, `call_target_presence`, `return_usage`, `replacement_return`, and other structural presence checks. The JSON also carries exact call shapes, return uses, and old/new/downstream return contracts where applicable.

`metadata.analysis_plan.capabilities` shows which phases were analyzed, used only as prerequisites, or skipped. `metadata.timings_seconds` records repository indexing, each relation collector, range comparison, import analysis, direct-call discovery/comparison, generator-finding conversion, and total elapsed time; skipped phases are `null`.

## Failure behavior

The skill is awareness-only by default. Finding an introduced break does not fail the command. Invalid SHAs, checkout mismatch, parse failure, missing engine, or incomplete output fail because the analysis itself is invalid.

The repository engine supports `--fail-on introduced` and `--fail-on unresolved` for an explicitly blocking CI. Do not enable either mode in upstream awareness CI without a separate policy decision.
