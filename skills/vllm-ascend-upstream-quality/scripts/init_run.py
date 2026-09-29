#!/usr/bin/env python3
"""Validate pinned repositories/ranges and write a reusable run manifest."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"ERROR: {message}")


def run_git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["git", "-C", str(root), *args]
    result = subprocess.run(
        command,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        fail(f"{' '.join(command)} failed: {detail}")
    return result


def validate_repo(raw_path: str, label: str) -> Path:
    root = Path(raw_path).expanduser().resolve()
    if not root.is_dir():
        fail(f"{label} repository does not exist: {root}")
    result = run_git(root, "rev-parse", "--is-inside-work-tree")
    if result.stdout.strip() != "true":
        fail(f"{label} is not a Git worktree: {root}")
    return root


def resolve_commit(root: Path, revision: str, label: str) -> str:
    result = run_git(root, "rev-parse", "--verify", f"{revision}^{{commit}}")
    commit = result.stdout.strip()
    if len(commit) != 40:
        fail(f"{label} did not resolve to a full commit SHA: {revision}")
    return commit


def require_ancestor(root: Path, old_sha: str, new_sha: str) -> None:
    result = run_git(
        root,
        "merge-base",
        "--is-ancestor",
        old_sha,
        new_sha,
        check=False,
    )
    if result.returncode == 1:
        fail(f"vLLM old SHA is not an ancestor of new SHA: {old_sha} -> {new_sha}")
    if result.returncode != 0:
        fail(result.stderr.strip() or "git merge-base failed")


def parse_instant(value: str, label: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        fail(f"{label} must be ISO-8601: {value} ({exc})")
    if parsed.tzinfo is None:
        fail(f"{label} must include an explicit UTC offset: {value}")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vllm-root", required=True)
    parser.add_argument("--vllm-old", required=True)
    parser.add_argument("--vllm-new", required=True)
    parser.add_argument("--ascend-root", required=True)
    parser.add_argument("--ascend-baseline", required=True)
    parser.add_argument("--lane", required=True)
    parser.add_argument("--anchor", required=True)
    parser.add_argument("--period-start", required=True)
    parser.add_argument("--period-end", required=True)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    vllm_root = validate_repo(args.vllm_root, "vLLM")
    ascend_root = validate_repo(args.ascend_root, "vllm-ascend")

    old_sha = resolve_commit(vllm_root, args.vllm_old, "vLLM old SHA")
    new_sha = resolve_commit(vllm_root, args.vllm_new, "vLLM new SHA")
    require_ancestor(vllm_root, old_sha, new_sha)

    ascend_baseline = resolve_commit(
        ascend_root, args.ascend_baseline, "Ascend baseline"
    )
    version_anchor = resolve_commit(ascend_root, args.anchor, "version anchor")

    start = parse_instant(args.period_start, "period start")
    end = parse_instant(args.period_end, "period end")
    if start >= end:
        fail("period start must be earlier than period end")
    try:
        zone = ZoneInfo(args.timezone)
    except Exception as exc:  # zoneinfo raises platform-specific subclasses
        fail(f"unknown timezone {args.timezone}: {exc}")

    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {
            "start": start.isoformat(),
            "end_exclusive": end.isoformat(),
            "timezone": args.timezone,
            "start_local": start.astimezone(zone).isoformat(),
            "end_exclusive_local": end.astimezone(zone).isoformat(),
        },
        "vllm": {
            "root": str(vllm_root),
            "old_sha": old_sha,
            "new_sha": new_sha,
            "range": f"{old_sha}..{new_sha}",
        },
        "vllm_ascend": {
            "root": str(ascend_root),
            "baseline_sha": ascend_baseline,
            "version_lane": args.lane.strip(),
            "version_anchor": version_anchor,
        },
        "requirements": {
            "pr_delta": "parent_to_merge_or_head",
            "main2main_validate_before_predict": True,
            "unknown_values_must_remain_pending": True,
        },
    }
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output),
                "vllm_range": manifest["vllm"]["range"],
                "ascend_baseline": ascend_baseline,
                "lane": args.lane.strip(),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

