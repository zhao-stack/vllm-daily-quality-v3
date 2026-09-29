# Upstream test-condition style

Write one concise Chinese cell per optimization PR. Preserve only reproduction-critical facts in this order:

```text
模型；硬件/平台/后端；TP/DP/EP/CP等并行配置；
输入/输出长度、并发、请求数或batch；
baseline与PR对照方式；预热/轮次；数据集或正确性检查。
```

## Rules

- Keep the cell normally between 40 and 180 Chinese characters.
- Use technical identifiers such as model names, FP8, TP, CUDA Graph, or kernel names when they are essential.
- Translate explanatory prose; do not paste the English PR summary.
- Separate test conditions from optimization mechanism and benchmark results.
- Do not infer undisclosed hardware, model, concurrency, or repetitions.
- End with `PR未说明……` when a reproduction-critical field is missing.
- For a pure unit test or infrastructure PR, state that no end-to-end benchmark was provided.
- For a screenshot-only result, say that the numeric conditions cannot be fully extracted.

## Good examples

```text
模型：DeepSeek-V4-Flash；硬件8×H200，EP=8；输入/输出均为1k tokens，
并发16/64/256。对比优化前后TPOT、TTFT和输出吞吐。
```

```text
对象：beam search每个decode step的beam展平。对比sum与itertools.chain的
算法复杂度；PR未提供模型、硬件、beam数量或实测基准。
```

## Bad examples

- the first 200 characters of the PR body;
- only `H100 benchmark`;
- optimization background presented as test conditions;
- a guessed hardware or model;
- upstream result repeated without the load that produced it.

