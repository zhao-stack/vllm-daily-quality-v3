---
name: vllm-ascend-main2main
description: Run exact upstream commit-range interface analysis only for a confirmed vllm-ascend main2main upgrade PR changing .github/vllm-main-verified.commit, including combined tag/commit and stacked upgrades. Verify candidates against both source trees and reuse matching scans. Do not use for ordinary PRs, tag-only upgrades, or generic unresolved compatibility inquiries.
---

# vLLM Ascend Main2Main

## Invocation gate: main2main upgrade PRs only

This analyzer supports the type-specific workflow in the companion vllm-ascend-main2main-pr skill. First classify the entire PR diff against its intended base/merge base and read the marker values.

- Run only for a confirmed main2main upgrade PR whose .github/vllm-main-verified.commit value advances: commit-only direct, commit-only continuation, or combined commit/tag upgrade.
- Before a PR exists or its marker has been edited, preparation is allowed only for an explicitly requested main2main upgrade PR with a concrete marker transition and exact old/new targets. This permits scanning before the first marker edit; it does not admit generic interface work.
- A label/title, ordinary adaptation, failing test, whitespace-only marker edit, or mention of this skill is insufficient. Ordinary PRs, tag-only upgrades, rollbacks, and generic unresolved contract inquiries do not pass this gate. Return to the requested ordinary analysis without running this workflow.
- Loading or reviewing this skill does not itself authorize a scan. Respect read-only review scope and do not initiate an implementation/CI workflow just because a PR qualifies.
- For later work on the same upgrade PR, classify the whole diff, not just the latest commit. If the upgrade was removed from the whole PR, leave this workflow.

### Select the interval and pre-adaptation baseline

1. Direct upgrade: old is the PR baseline's main marker; new is the proposed target. Use the clean vllm-ascend snapshot before this upgrade's adaptations.
2. Continuation A → B → C: the predecessor owns A → B. Scan only old=B to new=C against a clean snapshot containing the pinned predecessor adaptations but none of the new B → C adaptations. Record predecessor PR/head SHA and the inherited squash commit; do not accidentally scan A → C using the final target branch marker.
3. Combined tag/commit: pin this same pre-adaptation snapshot before editing version guards. Scan the commit interval against that independent snapshot even if tag adjustments have already begun in the implementation checkout.
4. Existing PR missing a scan: reconstruct the appropriate pre-adaptation snapshot. Do not treat already-fixed code as the original baseline.

Run validate/predict and review findings before first changing the main marker. Reuse a completed scan only if exact old/new SHAs, downstream baseline SHA, engine version/source fingerprint, and all external source SHAs still match. Do not rescan merely for later adaptation commits or description edits. Rerun if inputs change, results are absent/invalid, or the user requests a fresh scan within this eligible upgrade. Rebase or predecessor synchronization requires rechecking that fingerprint and responsibility interval.

## Analysis workflow

Use only the gray engine pinned by [engine-lock.json](engine-lock.json): zhao-stack/vllm-ascend at 0b2d7f1adb822977ac8e8466e3a101e928bd86ba (v2.34.0). Keep this skill as a thin workflow, cache, and report layer. Never recreate AST, MRO, patch-flow, or signature logic inside the skill.

The default independent checkout is $CODEX_HOME/cache/main2main-engines/<locked-sha>, or ~/.codex/cache/main2main-engines/<locked-sha> when CODEX_HOME is unset. Pass --engine-root to use another independent checkout of exactly the same repository and SHA. Before execution the adapter verifies origin, HEAD, clean tracked files, and all 23 Python files against canonical Git-byte hashes. Missing/wrong engines fail explicitly; never fall back to --ascend-root. Read the engine preparation instructions in references/cli-and-output.md before the first run. Updating the pin requires an intentional lock update and validation, not moving with the gray branch tip.

After the invocation gate passes, invoke the shared engine's fixed `main2main` scenario, which runs the full exact-contract plan. The separate `vllm-interface` upstream-PR scenario analyzes direct imports, overrides, and exact downstream calls while excluding monkey patches; it is an engine/CI entry point, not a reduced main2main skill mode.

## Required inputs

Resolve and record before analysis:

- vLLM repository and exact old/new SHAs;
- vllm-ascend repository and exact pre-adaptation baseline SHA selected by the direct/continuation/combined rules above; predecessor PR/head and inherited commit when applicable;
- an independent checkout of the locked gray engine, supplied through --engine-root or prepared at the documented default cache path; keep it separate from the review baseline checkout;
- exact SHAs for every external source root.

Require the vLLM working tree HEAD to equal `new` and the vllm-ascend working tree HEAD to equal `--expect-ascend-sha`. Do not guess a missing version or analyze a dirty, unintended checkout.

## Mandatory workflow after the gate passes

1. Read [references/analysis-contract.md](references/analysis-contract.md) before interpreting or promoting findings.
2. Run `validate --engine-mode new` to regenerate the live dependency graph. Treat unresolved bindings as review items, not guessed relationships.
3. Run `predict` for the exact old/new range. Use the pre-upgrade vllm-ascend baseline so later adaptations cannot hide the original break.
4. Read `main2main-introduced-breaks.csv` first. Keep `preexisting` and `analysis_unresolved` out of the upgrade repair list.
5. Verify the four action gates and independently inspect both source trees before proposing any code change. A tool classification is a candidate, not authorization to patch.
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

Attribute every candidate to the exact upstream PR/commit and diff location, assigned interval, downstream symbol/dependency, and contract delta. Check existing adaptation and version guards in both lanes. Four true tool gates are necessary but do not replace source review of actual necessity.

Record each finding as confirmed adaptation needed, already compatible/no change, false positive, or unresolved, with evidence and rationale. Record false negatives discovered by CI/source review as well. A statically proven break does not need a manufactured failing CI run before adaptation; do not invent runtime proof.

Only confirmed new-interval problems enter the upgrade repair list. Keep predecessor-range issues with the predecessor PR; report historical issues separately. Do not repair the engine or add unrelated cleanup, defensive fallbacks, speculative fixes, historical debt, or forward backports in the upgrade PR.

Pass confirmed findings to vllm-ascend-main2main-pr for implementation: exact upstream cause before edits, vllm_version_is for lane selection, minimal explicit fixes without silent fallbacks or weakened tests, and one Signed-off-by commit per independent adaptation. This analysis skill does not independently push changes, comment on PRs, or trigger CI.
