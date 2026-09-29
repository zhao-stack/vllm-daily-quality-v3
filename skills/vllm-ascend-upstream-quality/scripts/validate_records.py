#!/usr/bin/env python3
"""Validate upstream-quality structured records and decision consistency."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


META_REQUIRED = {
    "period_start",
    "period_end",
    "timezone",
    "vllm_old_sha",
    "vllm_new_sha",
    "ascend_baseline",
    "version_lane",
    "version_anchor",
}

BUG_REQUIRED = {
    "record_id",
    "upstream_pr",
    "title",
    "merged_at",
    "merge_sha",
    "pr_parent_sha",
    "pr_head_sha",
    "summary",
    "category",
    "upstream_fix_point",
    "impact_scenario",
    "ascend_relation",
    "source_reachable",
    "fix_enters_ascend",
    "equivalent_handling",
    "lane_matches",
    "conclusion",
    "basis",
    "action",
    "priority",
    "validation_advice",
    "evidence_level",
    "status",
}

OPT_REQUIRED = {
    "record_id",
    "upstream_pr",
    "title",
    "merged_at",
    "merge_sha",
    "pr_parent_sha",
    "pr_head_sha",
    "description",
    "mechanism",
    "change_point",
    "upstream_metric",
    "upstream_result",
    "upstream_test_conditions",
    "evidence_url",
    "affected_code",
    "ascend_relation",
    "npu_applicability",
    "benefit_acquisition",
    "reason",
    "evidence_level",
    "expected_npu_kpi",
    "extra_work",
    "estimated_effort",
    "verification_priority",
    "adaptation_priority",
    "verification_plan",
    "npu_result",
    "action",
    "status",
}

YES_NO_UNKNOWN = {"是", "否", "未知"}
EQUIVALENCE = {"是", "否", "未知", "不适用"}
BUG_CONCLUSIONS = {"直接继承修复", "需要适配", "不受影响", "待确认"}
OPT_ACQUISITION = {"理论直接继承", "需要适配", "待POC", "当前不适用"}
ACTIONS = {"modify", "review", "dismiss"}
BUG_PRIORITIES = {"P1", "P2", "P3"}
OPT_PRIORITIES = {"高", "中", "低", "暂缓"}
EVIDENCE = {"E0", "E1", "E2", "E3", "E4"}
PLACEHOLDER_NPU_RESULTS = {
    "",
    "未进行NPU实测",
    "未验证",
    "待验证",
    "无",
    "不适用",
    "当前NPU路径不适用",
}
RAW_ENGLISH_START = re.compile(
    r"^(This|Part|With|Summary|Thanks|Use|Adds|Fixes|In|The)\b", re.I
)
PR_URL = re.compile(r"^https://github\.com/vllm-project/vllm/pull/\d+/?$")
SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")


class Findings:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    def warn(self, path: str, message: str) -> None:
        self.warnings.append(f"{path}: {message}")


def nonempty(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def parse_iso(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def require_fields(
    findings: Findings, path: str, row: dict[str, Any], required: set[str]
) -> None:
    for key in sorted(required):
        if key not in row or not nonempty(row[key]):
            findings.error(path, f"missing required field {key}")


def check_common(findings: Findings, path: str, row: dict[str, Any]) -> None:
    url = str(row.get("upstream_pr", ""))
    if url and not PR_URL.match(url):
        findings.error(path, "upstream_pr must be a vllm-project/vllm PR URL")
    sha = str(row.get("merge_sha", ""))
    if sha and not SHA.match(sha):
        findings.error(path, "merge_sha must contain 7-40 hexadecimal characters")
    for key in ("pr_parent_sha", "pr_head_sha"):
        value = str(row.get(key, ""))
        if value and not SHA.match(value):
            findings.error(path, f"{key} must contain 7-40 hexadecimal characters")
    if row.get("merged_at") and not parse_iso(row["merged_at"]):
        findings.error(path, "merged_at must be ISO-8601")
    if row.get("action") not in ACTIONS:
        findings.error(path, f"invalid action {row.get('action')!r}")
    if row.get("evidence_level") not in EVIDENCE:
        findings.error(path, f"invalid evidence_level {row.get('evidence_level')!r}")
    if row.get("action") == "modify":
        gate = row.get("action_gate")
        keys = (
            "relationship_verified",
            "contract_changed",
            "runtime_reachable",
            "version_lane_matches",
        )
        if not isinstance(gate, dict) or not all(gate.get(key) is True for key in keys):
            findings.error(path, "action=modify requires all four action_gate values true")


def check_bug(findings: Findings, index: int, row: Any) -> None:
    path = f"bugfix_rows[{index}]"
    if not isinstance(row, dict):
        findings.error(path, "row must be an object")
        return
    require_fields(findings, path, row, BUG_REQUIRED)
    check_common(findings, path, row)

    for key in ("source_reachable", "fix_enters_ascend", "lane_matches"):
        if row.get(key) not in YES_NO_UNKNOWN:
            findings.error(path, f"invalid {key} {row.get(key)!r}")
    if row.get("equivalent_handling") not in EQUIVALENCE:
        findings.error(
            path, f"invalid equivalent_handling {row.get('equivalent_handling')!r}"
        )
    conclusion = row.get("conclusion")
    if conclusion not in BUG_CONCLUSIONS:
        findings.error(path, f"invalid conclusion {conclusion!r}")
    if row.get("priority") not in BUG_PRIORITIES:
        findings.error(path, f"invalid priority {row.get('priority')!r}")

    source = row.get("source_reachable")
    repair = row.get("fix_enters_ascend")
    equivalent = row.get("equivalent_handling")
    lane = row.get("lane_matches")
    decisive = (source, repair, equivalent, lane)

    if conclusion == "直接继承修复" and not (
        source == "是" and repair == "是" and lane == "是"
    ):
        findings.error(path, "direct inherit requires source=yes, repair=yes, lane=yes")
    if conclusion == "需要适配" and not (
        source == "是"
        and repair == "否"
        and equivalent == "否"
        and lane == "是"
    ):
        findings.error(
            path, "adaptation requires source=yes, repair=no, equivalent=no, lane=yes"
        )
    if conclusion == "不受影响":
        valid = source == "否" or (
            source == "是" and repair == "否" and equivalent == "是"
        )
        if not valid:
            findings.error(path, "unaffected conclusion does not match the Bugfix matrix")
    if conclusion == "待确认" and "未知" not in decisive:
        findings.error(path, "pending conclusion requires at least one unknown matrix field")
    if conclusion != "待确认" and row.get("evidence_level") in {"E0", "E1"}:
        findings.warn(path, "closed static conclusion normally requires E2 or higher")


def check_optimization(findings: Findings, index: int, row: Any) -> None:
    path = f"optimization_rows[{index}]"
    if not isinstance(row, dict):
        findings.error(path, "row must be an object")
        return
    require_fields(findings, path, row, OPT_REQUIRED)
    check_common(findings, path, row)

    acquisition = row.get("benefit_acquisition")
    if acquisition not in OPT_ACQUISITION:
        findings.error(path, f"invalid benefit_acquisition {acquisition!r}")
    for key in ("verification_priority", "adaptation_priority"):
        if row.get(key) not in OPT_PRIORITIES:
            findings.error(path, f"invalid {key} {row.get(key)!r}")

    conditions = str(row.get("upstream_test_conditions", "")).strip()
    if len(conditions) < 20:
        findings.error(path, "upstream_test_conditions is too short to be reproducible")
    if len(conditions) > 220:
        findings.warn(path, "upstream_test_conditions exceeds 220 characters")
    if RAW_ENGLISH_START.match(conditions):
        findings.error(path, "upstream_test_conditions appears to paste raw English PR prose")

    evidence = row.get("evidence_level")
    npu_result = str(row.get("npu_result", "")).strip()
    if evidence in {"E0", "E1", "E2"} and npu_result not in PLACEHOLDER_NPU_RESULTS:
        if "未进行NPU实测" not in npu_result and "未验证" not in npu_result:
            findings.error(path, "E0-E2 record must not claim a concrete NPU result")
    if evidence in {"E3", "E4"} and npu_result in PLACEHOLDER_NPU_RESULTS:
        findings.error(path, "E3/E4 record requires a concrete NPU result")
    if row.get("status") in {"已获得收益", "已获得NPU收益"} and evidence != "E4":
        findings.error(path, "obtained-NPU-benefit status requires E4")
    if acquisition == "当前不适用" and row.get("action") != "dismiss":
        findings.warn(path, "current-not-applicable records should normally be dismissed")
    if acquisition == "需要适配" and evidence in {"E0", "E1"}:
        findings.warn(path, "optimization adaptation normally requires E2 causal closure")


def duplicate_values(rows: list[Any], key: str) -> list[str]:
    values = [str(row.get(key)) for row in rows if isinstance(row, dict) and row.get(key)]
    return sorted(value for value, count in Counter(values).items() if count > 1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return a non-zero exit code when warnings are present.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = Path(args.input).expanduser().resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(data, dict):
        print("ERROR: top-level JSON must be an object", file=sys.stderr)
        return 2

    findings = Findings()
    meta = data.get("meta")
    if not isinstance(meta, dict):
        findings.error("meta", "must be an object")
        meta = {}
    for key in sorted(META_REQUIRED):
        if not nonempty(meta.get(key)):
            findings.error("meta", f"missing required field {key}")
    for key in ("period_start", "period_end"):
        if meta.get(key) and not parse_iso(meta[key]):
            findings.error("meta", f"{key} must be ISO-8601")

    bug_rows = data.get("bugfix_rows", [])
    optimization_rows = data.get("optimization_rows", [])
    if not isinstance(bug_rows, list):
        findings.error("bugfix_rows", "must be an array")
        bug_rows = []
    if not isinstance(optimization_rows, list):
        findings.error("optimization_rows", "must be an array")
        optimization_rows = []

    for index, row in enumerate(bug_rows):
        check_bug(findings, index, row)
    for index, row in enumerate(optimization_rows):
        check_optimization(findings, index, row)

    for label, rows in (
        ("bugfix_rows", bug_rows),
        ("optimization_rows", optimization_rows),
    ):
        for key in ("record_id", "upstream_pr"):
            duplicates = duplicate_values(rows, key)
            if duplicates:
                findings.error(label, f"duplicate {key}: {', '.join(duplicates)}")

    bug_prs = {
        row.get("upstream_pr") for row in bug_rows if isinstance(row, dict)
    }
    opt_prs = {
        row.get("upstream_pr") for row in optimization_rows if isinstance(row, dict)
    }
    overlap = sorted(value for value in bug_prs & opt_prs if value)
    undocumented_overlap = []
    for pr in overlap:
        related = [
            row
            for row in [*bug_rows, *optimization_rows]
            if isinstance(row, dict) and row.get("upstream_pr") == pr
        ]
        if not all(nonempty(row.get("cross_table_reason")) for row in related):
            undocumented_overlap.append(pr)
    if undocumented_overlap:
        findings.warn(
            "records",
            "PRs appear in both tables without cross_table_reason: "
            + ", ".join(undocumented_overlap),
        )

    summary = {
        "input": str(path),
        "bugfix_rows": len(bug_rows),
        "optimization_rows": len(optimization_rows),
        "errors": len(findings.errors),
        "warnings": len(findings.warnings),
    }
    print(json.dumps(summary, ensure_ascii=False))
    for message in findings.errors:
        print(f"ERROR {message}")
    for message in findings.warnings:
        print(f"WARN {message}")
    if findings.errors or (args.strict and findings.warnings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
