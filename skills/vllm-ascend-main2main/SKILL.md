---
name: vllm-ascend-main2main
description: Scan exact vLLM old-to-new interface or field contract breaks against a pinned vllm-ascend baseline. Use before changing `.github/vllm-main-verified.commit`, or when unresolved compatibility requires source-derived dependency tracing; reuse a valid scan for later PR edits when its inputs are unchanged. Do not use for release-tag-only upgrades or ordinary code/PR analysis.
---

# vLLM Ascend Main2Main

## Invocation gate

Loading this skill is not permission to run its analyzer. Run it only in one of these two modes.

### Verified-main marker update

Run the exact-contract scan before the first change to `.github/vllm-main-verified.commit` in a PR. Derive `old` from the PR base version of that file and `new` from the proposed value; use the pre-upgrade vllm-ascend PR parent as the review baseline. Complete the scan and record its actionable and unresolved findings before changing the marker file.

A completed scan is reusable only when its fingerprint still matches the exact vLLM old/new SHAs, vllm-ascend baseline SHA, engine SHA or version, and every external source SHA. Do not rerun it for later code or PR-description edits in the same PR when that fingerprint and `.github/vllm-main-verified.commit` remain unchanged. Rerun only when one of those inputs changes, the prior result is missing or invalid, or the user explicitly requests a fresh scan.

Changing only `.github/vllm-release-tag.commit` does not trigger this mode.

### Unresolved contract inquiry

Outside a verified-main marker update, run the analyzer only when all of these conditions are true:

- the task concerns an exact vLLM old-to-new range against a pinned pre-upgrade vllm-ascend baseline;
- the unresolved question is whether an upstream interface or field contract change breaks a downstream dependency;
- direct inspection has not established compatibility, so the answer requires source-derived tracing of patches, overrides, inheritance, direct imports, exact calls, signatures, fields, or return protocols;
- the requested output is an interface-break report, an unknown compatibility determination, or adaptation planning that depends on that determination.

Do not run this workflow for ordinary code logic, behavior, algorithm, performance, test, PR-diff, implementation-content, or bug-cause analysis. Do not run it merely to review or modify code. If the concrete difference and affected downstream use are already clear, analyze, explain, or implement the known change directly. Mentioning this skill or `main2main` does not itself pass the gate. When uncertain whether full tracing will materially change the answer, prefer direct code analysis.

## Analysis workflow

Use the vllm-ascend repository engine as the only source-analysis implementation. Keep this skill as a thin workflow, cache, and report layer. Never recreate AST, MRO, patch-flow, or signature logic inside the skill.

After the invocation gate passes, invoke the shared engine's fixed `main2main` scenario, which runs the full exact-contract plan. The separate `vllm-interface` upstream-PR scenario analyzes direct imports, overrides, and exact downstream calls while excluding monkey patches; it is an engine/CI entry point, not a reduced main2main skill mode.

## Required inputs

Resolve and record before analysis:

- vLLM repository and exact old/new SHAs;
- vllm-ascend repository and exact review baseline SHA, normally the upgrade PR parent;
- a vllm-ascend checkout that contains `tools/vllm_interface_contracts`, supplied through `--engine-root` when different from the review baseline checkout;
- exact SHAs for every external source root.

Require the vLLM working tree HEAD to equal `new` and the vllm-ascend working tree HEAD to equal `--expect-ascend-sha`. Do not guess a missing version or analyze a dirty, unintended checkout.

## Mandatory workflow after the gate passes

1. Read [references/analysis-contract.md](references/analysis-contract.md) before interpreting or promoting findings.
2. Run `validate --engine-mode new` to regenerate the live dependency graph. Treat unresolved bindings as review items, not guessed relationships.
3. Run `predict` for the exact old/new range. Use the pre-upgrade vllm-ascend baseline so later adaptations cannot hide the original break.
4. Read `main2main-introduced-breaks.csv` first. Keep `preexisting` and `analysis_unresolved` out of the upgrade repair list.
5. Verify the four action gates before proposing any code change.
6. Use `evaluate` only when an actual vllm-ascend adaptation commit is available.
7. Keep exact dynamic callsite and return-protocol checks in the default profile. Use `--profile expanded` only when the user explicitly asks for legacy static field/call-protocol, registration, or broad review tables.
8. Confirm `metadata.scenario=main2main` and inspect `metadata.analysis_plan.capabilities` and `metadata.timings_seconds` when execution scope or runtime cost matters.

Read [references/cli-and-output.md](references/cli-and-output.md) for commands, cache behavior, engine modes, exit behavior, and report fields.

## Typical commands

```powershell
python .\main2main_risk_analyzer.py validate `
  --engine-root <engine_vllm_ascend_repo> `
  --vllm-root <vllm_repo> `
  --ascend-root <baseline_vllm_ascend_repo> `
  --expect-ascend-sha <ascend_sha>

python .\main2main_risk_analyzer.py predict `
  --engine-root <engine_vllm_ascend_repo> `
  --vllm-root <vllm_repo_at_new> `
  --ascend-root <baseline_vllm_ascend_repo> `
  --expect-ascend-sha <ascend_sha> `
  --old <old_vllm_sha> `
  --new <new_vllm_sha> `
  --engine-mode new `
  --profile exact-contracts `
  --output <report.md>
```

## Migration modes

- `new`: use the shared repository engine; this is the default.
- `compare`: run the shared engine and the saved legacy analyzer, then emit `new-vs-legacy.json`. Use during migration audits.
- `legacy`: use saved mapping tables and the previous analyzer only for compatibility checks.

Do not publish run-scoped generated relations into the legacy production manifest. Do not update a mapping merely because upstream changed; a changed range must remain visible until downstream code becomes compatible.

## Scope discipline

Attribute every actionable item to the exact upstream range, upstream symbol, downstream dependency, and contract delta. Do not add unrelated cleanup, historical debt, defensive tests, or forward backports unless the user explicitly expands scope.
