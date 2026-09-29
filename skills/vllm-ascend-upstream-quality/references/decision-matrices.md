# Decision matrices

## Bugfix matrix

Evaluate the current vllm-ascend effective implementation, not only filenames.

| Source reachable in Ascend | Upstream repair enters Ascend | Ascend equivalent handling | Conclusion |
|---|---|---|---|
| No | Any | Any | 不受影响 |
| Yes | Yes | Not applicable | 直接继承修复 |
| Yes | No | Yes | 不受影响 |
| Yes | No | No | 需要适配 |
| Unknown in any decisive field | Unknown | Unknown | 待确认 |

Rules:

- A patch, override, copied implementation, or independent backend requires producer/repair/equivalence tracing.
- `未发现映射` is not evidence of `不受影响`.
- Direct inheritance requires proving that the repaired shared callable, field, protocol, or model code remains effective.
- Equivalent handling requires current Ascend code that prevents the same failure, not a similar feature name.
- If upstream creates the Bug in shared code and repairs it in a location replaced by Ascend, check whether the replaced implementation reproduces or prevents the failure.

## Optimization matrix

| Ascend condition | Benefit classification |
|---|---|
| Shared path and upstream optimization executes unchanged | 理论直接继承 |
| Same causal bottleneck exists and the upstream code does not enter | 需要适配 |
| Similar NPU path exists but the bottleneck or expected gain is unproven | 待POC |
| Ascend has an effective independent equivalent implementation | 当前不适用 |
| Platform/backend/model/feature is unreachable | 当前不适用 |
| Ascend patch/override bypasses upstream but the mechanism may transfer | 待POC or 需要适配, depending on causal proof |
| PR is reverted in the target range | 当前不适用; net endpoint benefit is zero |

## Need adaptation versus POC

Use `需要适配` only when all are known:

1. the Ascend runtime path is reachable;
2. the same producer, redundant work, communication, allocation, or kernel bottleneck exists;
3. the upstream optimization does not already enter;
4. no equivalent Ascend handling exists;
5. an actionable change point is identified.

Use `待POC` when the optimization idea is relevant but profiling or a minimal benchmark is still needed to prove the NPU bottleneck or benefit.

## Not applicable reason taxonomy

Choose one primary reason and explain it:

1. platform/backend/model/feature unsupported;
2. Ascend uses a different effective solution;
3. Ascend patch/override bypasses the upstream path and the upstream mechanism is not applicable;
4. same-period revert removes the endpoint benefit.

Do not use reason 3 when the same bottleneck may still exist. Downgrade to POC until checked.

