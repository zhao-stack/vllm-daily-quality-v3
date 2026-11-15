---
name: vllm-ascend-main2main-pr
description: Implement or review a vllm-ascend main2main upgrade PR when its PR-wide diff changes `.github/vllm-main-verified.commit` or `.github/vllm-release-tag.commit`. Enforce upgrade-only scope, upstream-cause traceability, main/release lane separation, version-gated adaptation, and per-change PR evidence; do not use for ordinary PRs.
---

# vLLM Ascend Main2Main PR

Use this workflow for every implementation, continuation, review, and PR-description update on a main2main upgrade PR. It governs adaptation work; it does not replace the separate interface-contract analyzer.

## Identify the PR from its full diff

At the start of each turn, compare the PR branch with its intended base or merge base. Do not classify the PR from only the current working-tree edit.

A PR is a main2main upgrade PR when its PR-wide diff changes either of these files:

- `.github/vllm-main-verified.commit` - main-commit upgrade lane;
- `.github/vllm-release-tag.commit` - release-tag upgrade lane.

If both files change, treat both lanes explicitly. Continue using this skill for later edits in the same PR even when the current edit does not touch either marker.

## Scope gate

Modify only problems introduced by the exact main2main upgrade represented by the marker change. Do not include preexisting defects, unrelated cleanup, opportunistic refactors, forward backports, or behavior changes without a demonstrated causal link to that upgrade.

Before changing code, identify the exact upstream change that caused the need for adaptation. Trace it to the upstream symbol or behavior and the affected vllm-ascend use. If the cause is unknown or only suspected, do not modify code; record it as unresolved and investigate further.

## Interface scan rule

Before first changing `.github/vllm-main-verified.commit`, invoke `vllm-ascend-main2main` for the exact old-to-new range. Use the marker's PR-base value as `old`, the proposed value as `new`, and the vllm-ascend PR parent as the baseline.

Reuse a valid completed scan during subsequent work on the same PR. Do not invoke the analyzer again merely because later edits modify adaptation code, tests, commits, or the PR description. Rerun only when the verified-main target, old/new range, baseline, engine or external-source inputs change; when the recorded result is missing or invalid; or when the user explicitly requests it.

A release-tag-only marker change does not automatically invoke the interface analyzer. Use it there only for a genuinely unresolved interface-contract question covered by that skill's own invocation gate.

## Main and release adaptation

Before editing a release-tag upgrade, determine and document how the release lane's required adaptation differs from the main lane. Do not assume the same upstream behavior exists in both.

If main and release need different implementations, branch explicitly with `vllm_version_is`. Do not use `hasattr` or similar runtime capability probing as a substitute for version selection. If both lanes use the same implementation, avoid an unnecessary version branch.

## Upstream evidence

For every adaptation point:

1. identify the changed vllm-ascend file and symbol;
2. state why the upgrade requires the change;
3. identify the exact upstream change that caused it;
4. for a main-commit upgrade, provide a direct link to the corresponding change in the upstream PR, preferably the relevant file or diff anchor rather than only the PR landing page;
5. state whether the adaptation applies to main, release, or both;
6. state how the change was verified.

Do not implement an adaptation point that lacks this evidence.

## Coding constraints

- Keep each adaptation minimal and attributable to the exact upgrade range.
- Preserve existing vllm-ascend behavior outside the affected version lane.
- Do not delete or skip tests, weaken assertions, or add silent fallbacks to make the upgrade pass.
- Do not mix process-documentation or infrastructure cleanup into the upgrade PR.
- Add or update tests that exercise the introduced incompatibility when practical; otherwise explain the concrete validation used.

Follow the repository's general architecture, testing, formatting, commit sign-off, and PR-title rules in addition to this skill.

## PR description contract

The PR description must explain every adaptation point individually. Use a table or equivalent structure containing at least:

| vllm-ascend change | Why it is required | Upstream cause and direct link | Lane | Verification |
|---|---|---|---|---|

Also record the relevant main old/new SHAs or release-tag transition, the vllm-ascend baseline, the interface-scan result when required, and all unresolved items. For release-tag upgrades, include a separate summary of how release handling differs from main handling.

## Completion checks

Before declaring the PR ready:

- recompute the PR-wide diff and confirm it is still correctly classified;
- map every non-test code change to an upgrade-introduced cause and PR-description entry;
- confirm every main-commit adaptation entry has a direct upstream PR change link;
- confirm release/main differences use `vllm_version_is` rather than capability probing;
- confirm no unrelated changes or unresolved speculative fixes remain;
- confirm the required interface scan is present and still matches its input fingerprint;
- report any unmet rule as a blocker instead of silently relaxing it.
