# Evidence, action, and priority

## Evidence levels

| Level | Minimum evidence | Allowed claims |
|---|---|---|
| E0 | Title, label, keyword, or unverified file hit | Candidate only |
| E1 | PR body/diff plus a relationship candidate or platform indication | Static review; no closed causal claim |
| E2 | Parent-to-merge delta and effective Ascend path form a complete static causal chain | Direct inherit, adaptation, equivalent handling, or unaffected static conclusion |
| E3 | NPU minimal functional test or microbenchmark with reproducible conditions | Verified function or local NPU benefit |
| E4 | Stable NPU end-to-end A/B with correctness guard and repeated measurement | Obtained NPU benefit |

Do not promote evidence because a PR reports an upstream number. Upstream benchmark evidence is not NPU evidence.

## Action gate

Set `action=modify` only when all values are true:

```text
relationship_verified
AND contract_changed
AND runtime_reachable
AND version_lane_matches
```

Otherwise use:

- `review` when evidence or a POC is still required;
- `dismiss` when the path is proven unreachable, equivalent, reverted, or out of scope.

## Priority

- P1: security, silent corruption, crash, race, memory corruption, or high-frequency high-value gap with a closed action gate.
- P2: compatibility, important feature regression, moderate benefit, or inheritance-only risk.
- P3: metrics-only, low-frequency, unsupported, reverted, or low-value review.

For optimization, keep two visible priorities:

- verification priority: how soon to prove the benefit;
- adaptation priority: how soon to implement after proof.

## Benefit language

Use these phrases consistently:

- `上游报告收益`: number comes from the upstream PR.
- `理论直接继承`: optimized code enters the Ascend path; NPU gain is not measured.
- `NPU局部验证收益`: E3 microbenchmark/minimal test.
- `已获得NPU收益`: E4 end-to-end result only.

