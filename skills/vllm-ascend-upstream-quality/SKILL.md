---
name: vllm-ascend-upstream-quality
description: Collect, classify, analyze, and report merged vLLM upstream PRs over an exact user-defined time/SHA range into vllm-ascend Bugfix and optimization-benefit management tables. Use when Codex is asked to produce quality summaries for a custom date range, week, month, release interval, or main2main upgrade; decide whether vllm-ascend directly inherits a fix or optimization, needs adaptation or POC, is unaffected; refine pending conclusions; extract concise Chinese upstream test conditions; or generate and validate the Excel workbook.
---

# vLLM Ascend Upstream Quality

## Daily V3 handover routing

For this package's daily quality report or sample replay, first read the sibling
`../vllm-daily-quality-v3/SKILL.md` and the package's `docs/分析与字段契约.md`.
That user-approved mode overrides this older two-sheet template, displayed
version/evidence columns, and exclusion of ordinary features: it requires the
eleven-sheet template and a separate feature/interface view. Retain the causal
decision matrices and strict record validation below. Read the current main2main
invocation gate before any analyzer execution; ordinary performance/source review
does not automatically trigger a full scan. Other custom two-view requests may
continue using the original mode below.

Produce auditable Bugfix and optimization-benefit reports for a pinned vLLM range and a pinned vllm-ascend baseline. Treat upstream PR text and mapping hits as evidence candidates, not conclusions.

## Required inputs

Resolve before analysis:

- vLLM repository and exact old/new SHAs;
- vllm-ascend repository and exact review baseline;
- main/release lane and its anchor;
- period start/end and timezone;
- for each PR, its parent and merge/head commit;
- output directory and optional existing workbook/template.

Never guess a SHA, lane, anchor, PR parent, or runtime support state. Stop or retain `待确认` when the missing input cannot be recovered.

Treat the requested period as `[period-start, period-end)` in the supplied timezone: include the start instant and exclude the end instant. Accept any user-defined duration. A week is only one possible range; never assume “last week.” If the user supplies dates without times, normalize both boundaries to local midnight in the supplied timezone and keep the end boundary exclusive.

When starting a reusable run, execute:

```powershell
python scripts/init_run.py `
  --vllm-root <vllm-root> --vllm-old <old-sha> --vllm-new <new-sha> `
  --ascend-root <ascend-root> --ascend-baseline <baseline-sha> `
  --lane <main-or-release> --anchor <anchor-sha> `
  --period-start <ISO-time> --period-end <ISO-time> --timezone <timezone> `
  --output <run-manifest.json>
```

## Required companion skills

- Use `vllm-ascend-main2main` for method, field, call, import relocation, copied-code, registration, inheritance, patch, and runtime-reachability analysis.
- Use `spreadsheets` when creating or editing the `.xlsx` report. Follow its artifact-tool, render, inspect, and formula-error requirements.

Do not duplicate main2main mapping logic in this skill. This skill orchestrates collection, evidence synthesis, decisions, reporting, and QA.

## Reference routing

- Read [references/decision-matrices.md](references/decision-matrices.md) before assigning any Bugfix or optimization conclusion.
- Read [references/evidence-levels.md](references/evidence-levels.md) before assigning evidence, priority, action, or claimed NPU benefit.
- Read [references/workbook-schema.md](references/workbook-schema.md) before creating records or the workbook.
- Read [references/test-condition-style.md](references/test-condition-style.md) when extracting or rewriting upstream test conditions.

## Workflow

### 1. Lock the analysis range

Validate repository paths, commits, ancestry, lane, anchor, period, and timezone. Record them in the report metadata. Ensure the SHA endpoints represent the intended user-defined period. Compare each PR parent to its merge/head; do not infer a PR delta from the whole requested range.

### 2. Collect merged PR evidence

Collect all merged PRs in the requested period and preserve:

- PR URL, title, labels, author, merge time and merge SHA;
- parent/head SHA and exact diff;
- changed files and affected symbols;
- PR body, test commands, benchmark tables, screenshots and linked dependencies;
- same-period reverts or superseding PRs.

Prefer the connected GitHub source. Fall back to local Git history only when it can recover exact PR identity and commits. Keep source URLs in row data.

### 3. Classify the PRs

Classify into:

- Bugfix: correctness, crash, race, security, compatibility, resource leak, invalid metrics, or build/runtime regression;
- optimization: latency, throughput, memory, communication, compilation, loading, graph execution, kernel, or preprocessing improvement;
- excluded: documentation-only, ordinary feature addition, maintenance with no quality effect, or duplicate/reverted change with no net endpoint effect.

Allow a PR to appear in both views only when it contains a real correctness fix and a separately managed performance benefit. Explain the overlap.

### 4. Structure upstream evidence

For Bugfix records, extract:

- trigger and affected scenario;
- Bug producer/source;
- upstream repair point;
- repair semantics and regression evidence.

For optimization records, extract:

- optimization mechanism;
- evaluation KPI;
- quantitative upstream result;
- concise Chinese test conditions.

Keep mechanisms, KPIs, results, and test conditions separate. Do not paste PR prose into the test-condition column.

### 5. Resolve the effective Ascend relationship

Use the main2main companion skill and current source to verify:

- direct inheritance or shared module use;
- override, wrapper, alias, monkey patch, or full replacement;
- copied/adapted code;
- independent NPU model, backend, scheduler, compiler, connector, or kernel;
- registration and runtime reachability;
- unsupported platform, backend, model, or feature;
- same-range revert or endpoint removal.

Run its mapping `validate` stage before `predict`; exclude stale or source-SHA-mismatched relations from actionable conclusions.

An override or patch never proves `不受影响`. Trace Bug producer -> upstream repair -> effective Ascend producer/consumer and check equivalent handling.

### 6. Apply the matrices

Apply the exact rules in `references/decision-matrices.md`.

- Bugfix outputs: `直接继承修复`, `需要适配`, `不受影响`, or `待确认`.
- Optimization outputs: `理论直接继承`, `需要适配`, `待POC`, or `当前不适用`.

Use `需要适配` only when the causal gap is statically closed. Use `待POC` when the mechanism is relevant but the NPU bottleneck or benefit is not proven.

### 7. Assign evidence, action, and priority

Apply `references/evidence-levels.md`.

- Static analysis may close at E2.
- Only NPU microbenchmark or equivalent minimal validation may reach E3.
- Only stable NPU end-to-end validation may reach E4.
- Never write `已获得收益` from E1/E2 evidence.
- Set `modify` only when all four main2main action-gate values are true.

For one-person operation, review in this order:

1. P1 correctness/security/crash adaptation;
2. high-value optimization POC or adaptation;
3. P2 compatibility;
4. direct-inherit minimal regression checks;
5. unsupported or low-value paths.

### 8. Validate the structured records

Create JSON using the schema in `references/workbook-schema.md`, then run:

```powershell
python scripts/validate_records.py --input <records.json> --strict
```

Fix errors before generating the workbook. Warnings may remain only when the record explicitly documents missing upstream information or a pending review.

### 9. Generate and verify the workbook

Use `assets/upstream-quality-template.xlsx`.

Because spreadsheet authoring must use the bundled artifact tool:

1. Load workspace dependencies.
2. Work in a writable run directory.
3. Copy `scripts/build_report.mjs` into that run directory.
4. Create the required `node_modules` junction there, pointing to the loader-provided Node packages.
5. Run:

```powershell
node build_report.mjs `
  --input <records.json> `
  --template <skill-root>/assets/upstream-quality-template.xlsx `
  --output <report.xlsx>
```

The builder writes both sheets, formulas, metadata, previews, compact inspections, and a formula-error scan. Open or render both sheets and fix clipping or unreadable wrapping before delivery.

## Completion gate

Finish only when:

- exact range and baselines are recorded;
- every included PR has a source URL and parent-to-merge evidence;
- every conclusion follows the matrix;
- `待确认` explains exactly what is unknown;
- test conditions are concise Chinese and explicitly mark missing parameters;
- upstream results and NPU results are separate;
- E1/E2 records do not claim obtained NPU benefit;
- summary counts reconcile to detail rows;
- the workbook has no obvious formula errors and both sheets pass visual review.
