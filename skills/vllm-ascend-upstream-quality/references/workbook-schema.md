# Workbook and record schema

Use one JSON object with:

```json
{
  "meta": {},
  "bugfix_rows": [],
  "optimization_rows": []
}
```

## Metadata

Required:

- `period_start`, `period_end`, `timezone`;
- `vllm_old_sha`, `vllm_new_sha`;
- `ascend_baseline`, `version_lane`, `version_anchor`.

`period_start` and `period_end` may define any user-selected duration. Interpret them as `[period_start, period_end)` in `timezone`; `period_end` is exclusive.

Optional: `total_merged_prs`, `generated_at`, `source_note`.

## Bugfix record

Required fields:

```text
record_id, upstream_pr, title, merged_at, merge_sha, pr_parent_sha, pr_head_sha,
summary, category, upstream_fix_point, impact_scenario,
ascend_relation, source_reachable, fix_enters_ascend,
equivalent_handling, lane_matches, conclusion, basis,
action, priority, validation_advice, evidence_level, status
```

Optional: `owner`, `notes`, `action_gate`, `cross_table_reason`.

Allowed values:

- reachability/coverage/lane: `是`, `否`, `未知`; equivalent may also be `不适用`;
- conclusion: `直接继承修复`, `需要适配`, `不受影响`, `待确认`;
- action: `modify`, `review`, `dismiss`;
- priority: `P1`, `P2`, `P3`;
- evidence: `E0`–`E4`.

Workbook order:

```text
记录ID, 上游PR链接, PR标题, 合入时间, Merge SHA, Bugfix简介, Bug分类,
上游修复点, 影响场景, Ascend有效实现/关系, Bug来源在Ascend可达,
上游修复进入Ascend, Ascend等价处理, 版本分支匹配, Ascend结论,
因果判断依据, 建议动作, 优先级, 验证建议, 证据等级, 状态, 负责人, 备注
```

## Optimization record

Required fields:

```text
record_id, upstream_pr, title, merged_at, merge_sha, pr_parent_sha, pr_head_sha,
description, mechanism, change_point, upstream_metric,
upstream_result, upstream_test_conditions, evidence_url,
affected_code, ascend_relation, npu_applicability,
benefit_acquisition, reason, evidence_level, expected_npu_kpi,
extra_work, estimated_effort, verification_priority,
adaptation_priority, verification_plan, npu_result,
action, status
```

Optional: `owner`, `notes`, `action_gate`, `cross_table_reason`.

`pr_parent_sha` and `pr_head_sha` are audit fields used to prove the exact PR delta. They are validated but are not separate workbook columns. When one PR appears in both tables, set `cross_table_reason` on both records to explain the distinct correctness and performance scope.

Recommended values:

- benefit acquisition: `理论直接继承`, `需要适配`, `待POC`, `当前不适用`;
- action: `modify`, `review`, `dismiss`;
- evidence: `E0`–`E4`;
- priority: `高`, `中`, `低`, `暂缓`.

Workbook order:

```text
收益点ID, 上游PR链接, PR标题, 合入时间, Merge SHA, 优化描述,
优化机制/类型, 优化机制/变更点, 上游评价指标, 上游实测结果,
上游测试条件, 收益证据URL, 影响代码路径, Ascend代码关系,
NPU适用性, NPU获取方式, 判定理由, 证据等级, 预期NPU指标,
额外工作类型, 预估工作量, 验证优先级, 适配优先级, 验证方案/验收标准,
NPU实测结果, 建议动作, 状态, 负责人, 备注
```

## Action gate object

For `action=modify`, include:

```json
{
  "relationship_verified": true,
  "contract_changed": true,
  "runtime_reachable": true,
  "version_lane_matches": true
}
```

## Sorting

- Bugfix: P1 -> P2 -> P3, then merge time.
- Optimization: verification priority high -> medium -> low -> paused, then adaptation priority and merge time.
