#!/usr/bin/env python3
"""Thin orchestration adapter for the vllm-ascend interface engine.

The installed skill owns workflow, caching, and report presentation.  Static
source analysis lives only in ``tools.vllm_interface_contracts`` inside a
vllm-ascend checkout.  The previous analyzer remains available through
``--engine-mode legacy`` during the migration window.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent
LEGACY_ANALYZER = SKILL_ROOT / "legacy" / "main2main_risk_analyzer.py"
ENGINE_PACKAGE = Path("tools/vllm_interface_contracts")
DEFAULT_PROFILE = "exact-contracts"
DEFAULT_SCENARIO = "main2main"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD")


def _resolve(root: Path, revision: str) -> str:
    return _git(root, "rev-parse", f"{revision}^{{commit}}")


def _engine_root(args: argparse.Namespace) -> Path:
    candidates = [
        Path(args.engine_root).resolve() if args.engine_root else None,
        Path(args.ascend_root).resolve(),
    ]
    for candidate in candidates:
        if (
            candidate is not None
            and (candidate / ENGINE_PACKAGE / "__main__.py").is_file()
        ):
            return candidate
    checked = ", ".join(str(item) for item in candidates if item is not None)
    raise SystemExit(
        "找不到公共接口分析引擎。请通过 --engine-root 指向包含 "
        f"{ENGINE_PACKAGE.as_posix()} 的 vllm-ascend 仓库。已检查：{checked}"
    )


def _engine_identity(engine_root: Path) -> dict[str, str | int]:
    package_root = engine_root / ENGINE_PACKAGE
    digest = hashlib.sha256()
    for path in sorted(package_root.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    generator_source = (package_root / "generator.py").read_text(encoding="utf-8")
    range_source = (package_root / "range_analysis.py").read_text(encoding="utf-8")
    plan_source = (package_root / "analysis_plans.py").read_text(encoding="utf-8")

    def constant(source: str, name: str, default: str) -> str:
        match = re.search(rf"^{name}\s*=\s*[\"']([^\"']+)[\"']", source, re.MULTILINE)
        return match.group(1) if match else default

    schema_match = re.search(
        r"^RANGE_SCHEMA_VERSION\s*=\s*(\d+)", range_source, re.MULTILINE
    )
    plan_match = re.search(
        r"^ANALYSIS_PLAN_VERSION\s*=\s*(\d+)", plan_source, re.MULTILINE
    )
    return {
        "generator_version": constant(generator_source, "GENERATOR_VERSION", "unknown"),
        "range_analyzer_version": constant(
            range_source, "RANGE_ANALYZER_VERSION", "unknown"
        ),
        "range_schema_version": int(schema_match.group(1)) if schema_match else -1,
        "analysis_plan_version": int(plan_match.group(1)) if plan_match else -1,
        "package_sha256": digest.hexdigest(),
    }


def _named_values(values: list[str], option: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"{option} 需要 PACKAGE=VALUE：{value}")
        name, raw = value.split("=", 1)
        if not name or not raw or name in result:
            raise SystemExit(f"{option} 参数无效或重复：{value}")
        result[name] = raw
    return result


def _cache_root(args: argparse.Namespace) -> Path:
    if args.cache_dir:
        return Path(args.cache_dir).resolve()
    if args.output:
        return Path(args.output).resolve().parent / ".main2main-cache"
    return Path.cwd() / ".main2main-cache"


def _cache_inputs(args: argparse.Namespace, engine_root: Path) -> dict[str, Any]:
    vllm_root = Path(args.vllm_root).resolve()
    ascend_root = Path(args.ascend_root).resolve()
    ascend_sha = _head(ascend_root)
    expected_ascend = (
        _resolve(ascend_root, args.expect_ascend_sha)
        if args.expect_ascend_sha
        else ascend_sha
    )
    if ascend_sha != expected_ascend:
        raise SystemExit(
            f"vllm-ascend 版本不匹配：期望 {expected_ascend}，当前 {ascend_sha}"
        )
    external_roots = _named_values(args.external_root, "--external-root")
    external_shas = _named_values(args.expect_external_sha, "--expect-external-sha")
    if set(external_roots) != set(external_shas):
        raise SystemExit(
            "--external-root 与 --expect-external-sha 必须包含相同的 package"
        )
    resolved_external: dict[str, str] = {}
    for package, root in external_roots.items():
        actual = _head(Path(root).resolve())
        expected = _resolve(Path(root).resolve(), external_shas[package])
        if actual != expected:
            raise SystemExit(
                f"外部源码 {package} 版本不匹配：期望 {expected}，当前 {actual}"
            )
        resolved_external[package] = actual
    payload: dict[str, Any] = {
        "engine": _engine_identity(engine_root),
        "scenario": DEFAULT_SCENARIO,
        "profile": args.profile,
        "vllm_new_sha": _resolve(vllm_root, args.new)
        if hasattr(args, "new") and args.new
        else _head(vllm_root),
        "vllm_ascend_sha": ascend_sha,
        "external_sources": resolved_external,
    }
    if hasattr(args, "old") and args.old:
        payload["vllm_old_sha"] = _resolve(vllm_root, args.old)
    return payload


def _cache_entry(
    args: argparse.Namespace, engine_root: Path
) -> tuple[Path, dict[str, Any]]:
    inputs = _cache_inputs(args, engine_root)
    key = hashlib.sha256(
        json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return _cache_root(args) / key, inputs


def _engine_command(engine_root: Path, *values: str) -> list[str]:
    return [sys.executable, "-m", "tools.vllm_interface_contracts", *values]


def _source_options(args: argparse.Namespace) -> list[str]:
    options = [
        "--vllm-root",
        str(Path(args.vllm_root).resolve()),
        "--ascend-root",
        str(Path(args.ascend_root).resolve()),
        "--expect-ascend-sha",
        args.expect_ascend_sha or _head(Path(args.ascend_root).resolve()),
    ]
    for value in args.external_root:
        options.extend(("--external-root", value))
    for value in args.expect_external_sha:
        options.extend(("--expect-external-sha", value))
    return options


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _write_cache_metadata(path: Path, inputs: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(inputs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _load_or_analyze(
    args: argparse.Namespace, engine_root: Path
) -> tuple[dict[str, Any], Path, bool]:
    entry, inputs = _cache_entry(args, engine_root)
    report_path = entry / "main2main-range-report.json"
    metadata_path = entry / "cache-inputs.json"
    if not args.refresh_cache and report_path.is_file() and metadata_path.is_file():
        cached_inputs = json.loads(metadata_path.read_text(encoding="utf-8"))
        if cached_inputs == inputs:
            return json.loads(report_path.read_text(encoding="utf-8")), entry, True
    entry.mkdir(parents=True, exist_ok=True)
    command = _engine_command(
        engine_root,
        "analyze-range",
        *_source_options(args),
        "--old",
        args.old,
        "--new",
        args.new,
        "--profile",
        args.profile,
        "--scenario",
        DEFAULT_SCENARIO,
        "--output-dir",
        str(entry),
        "--fail-on",
        "never",
    )
    result = _run(command, cwd=engine_root)
    if result.returncode != 0:
        raise SystemExit(f"公共分析引擎执行失败：\n{result.stderr or result.stdout}")
    _write_cache_metadata(metadata_path, inputs)
    return json.loads(report_path.read_text(encoding="utf-8")), entry, False


def _copy_or_print(source: Path, output: str | None) -> None:
    if output:
        destination = Path(output).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    else:
        print(source.read_text(encoding="utf-8"))


def _legacy_values(args: argparse.Namespace) -> list[str]:
    values = [
        args.command,
        "--vllm-root",
        args.vllm_root,
        "--ascend-root",
        args.ascend_root,
    ]
    if args.command in {"predict", "evaluate"}:
        values.extend(("--old", args.old, "--new", args.new, "--profile", args.profile))
    if args.command == "evaluate":
        values.extend(("--ascend-commit", args.ascend_commit))
    if args.command == "validate":
        values.extend(
            ("--profile", args.profile, "--max-candidates", str(args.max_candidates))
        )
        if args.json_output:
            values.extend(("--json-output", args.json_output))
    if args.output:
        values.extend(("--output", args.output))
    return values


def _run_legacy(
    args: argparse.Namespace, *, output_override: Path | None = None
) -> subprocess.CompletedProcess[str]:
    values = _legacy_values(args)
    if output_override is not None:
        if "--output" in values:
            index = values.index("--output")
            values[index + 1] = str(output_override)
        else:
            values.extend(("--output", str(output_override)))
    result = _run([sys.executable, str(LEGACY_ANALYZER), *values], cwd=SKILL_ROOT)
    if result.returncode != 0:
        raise SystemExit(
            f"legacy analyzer 执行失败：\n{result.stderr or result.stdout}"
        )
    return result


def _new_predict(
    args: argparse.Namespace, engine_root: Path
) -> tuple[dict[str, Any], Path, bool]:
    report, entry, cache_hit = _load_or_analyze(args, engine_root)
    _copy_or_print(entry / "main2main-range-report.md", args.output)
    return report, entry, cache_hit


def _file_set(report: dict[str, Any]) -> set[str]:
    return {
        item["downstream"]["file"]
        for item in report.get("findings", [])
        if item.get("classification") == "introduced_break"
        and item.get("downstream", {}).get("file")
    }


def _legacy_file_set(text: str) -> set[str]:
    return set(re.findall(r"vllm_ascend/[A-Za-z0-9_./-]+\.py", text.replace("\\", "/")))


def _compare(args: argparse.Namespace, engine_root: Path) -> None:
    report, entry, cache_hit = _load_or_analyze(args, engine_root)
    legacy_path = entry / "legacy-output.md"
    _run_legacy(args, output_override=legacy_path)
    new_files = _file_set(report)
    legacy_files = _legacy_file_set(
        legacy_path.read_text(encoding="utf-8", errors="replace")
    )
    comparison = {
        "cache_hit": cache_hit,
        "new_introduced_files": sorted(new_files),
        "legacy_candidate_files": sorted(legacy_files),
        "common": sorted(new_files & legacy_files),
        "new_only": sorted(new_files - legacy_files),
        "legacy_only": sorted(legacy_files - new_files),
        "note": "legacy 输出是候选集合；new 输出只包含 old 兼容、new 不兼容且通过严格门槛的区间问题。",
    }
    comparison_path = entry / "new-vs-legacy.json"
    comparison_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _copy_or_print(entry / "main2main-range-report.md", args.output)
    print(
        json.dumps(
            {"comparison": str(comparison_path), **comparison},
            ensure_ascii=False,
            indent=2,
        )
    )


def _validate_new(args: argparse.Namespace, engine_root: Path) -> None:
    entry, inputs = _cache_entry(args, engine_root)
    entry.mkdir(parents=True, exist_ok=True)
    json_path = entry / "dependency-validation.json"
    command = _engine_command(
        engine_root,
        "validate",
        *_source_options(args),
        "--scenario",
        DEFAULT_SCENARIO,
        "--output",
        str(json_path),
    )
    result = _run(command, cwd=engine_root)
    if result.returncode != 0:
        raise SystemExit(f"公共分析引擎校验失败：\n{result.stderr or result.stdout}")
    _write_cache_metadata(entry / "cache-inputs.json", inputs)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    lines = [
        "# vLLM Ascend 动态依赖校验",
        "",
        f"- vLLM SHA：`{payload['inputs']['vllm_sha']}`",
        f"- vllm-ascend SHA：`{payload['inputs']['vllm_ascend_sha']}`",
        f"- 已确认关系：{payload['summary']['relations']}",
        f"- 精确下游调用：{payload['summary'].get('direct_call_dependencies', 0)}",
        f"- 当前接口风险：{payload['summary'].get('contract_risks', 0)}",
        f"- 当前接口待确认：{payload['summary'].get('contract_reviews', 0)}",
        f"- 待复核发现：{payload['summary']['findings']}",
        f"- 分析器能力缺口：{payload['summary']['generator_issues']}",
        "",
    ]
    rendered = "\n".join(lines)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    if args.json_output:
        shutil.copyfile(json_path, Path(args.json_output))


def _evaluate_new(args: argparse.Namespace, engine_root: Path) -> None:
    report, entry, _ = _load_or_analyze(args, engine_root)
    ascend_root = Path(args.ascend_root).resolve()
    commit = _resolve(ascend_root, args.ascend_commit)
    parent = _resolve(ascend_root, f"{commit}^")
    changed = set(_git(ascend_root, "diff", "--name-only", parent, commit).splitlines())
    predicted = _file_set(report)
    evaluation = {
        "ascend_commit": commit,
        "changed_files": sorted(changed),
        "predicted_files": sorted(predicted),
        "hits": sorted(changed & predicted),
        "misses": sorted(changed - predicted),
        "unmodified_predictions": sorted(predicted - changed),
    }
    path = entry / "main2main-evaluation.json"
    path.write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    text = (entry / "main2main-range-report.md").read_text(encoding="utf-8")
    text += "\n## PR 修改命中\n\n"
    text += (
        f"- 命中：{len(evaluation['hits'])}\n- 漏报候选：{len(evaluation['misses'])}\n"
    )
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)


def _add_engine_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--engine-root")
    parser.add_argument(
        "--engine-mode", choices=("new", "legacy", "compare"), default="new"
    )
    parser.add_argument("--cache-dir")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--expect-ascend-sha")
    parser.add_argument(
        "--external-root", action="append", default=[], metavar="PACKAGE=PATH"
    )
    parser.add_argument(
        "--expect-external-sha", action="append", default=[], metavar="PACKAGE=SHA"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vllm-root", required=True)
    common.add_argument("--ascend-root", required=True)
    common.add_argument("--old", required=True)
    common.add_argument("--new", required=True)
    common.add_argument("--output")
    common.add_argument(
        "--profile", choices=("exact-contracts", "expanded"), default=DEFAULT_PROFILE
    )
    _add_engine_options(common)

    subparsers.add_parser("predict", parents=[common])
    evaluate = subparsers.add_parser("evaluate", parents=[common])
    evaluate.add_argument("--ascend-commit", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--vllm-root", required=True)
    validate.add_argument("--ascend-root", required=True)
    validate.add_argument("--output")
    validate.add_argument("--json-output")
    validate.add_argument("--max-candidates", type=int, default=200)
    validate.add_argument(
        "--profile", choices=("exact-contracts", "expanded"), default=DEFAULT_PROFILE
    )
    _add_engine_options(validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.engine_mode == "legacy":
        _run_legacy(args)
        return 0
    engine_root = _engine_root(args)
    if args.command == "validate":
        if args.engine_mode == "compare":
            _validate_new(args, engine_root)
            _run_legacy(
                args, output_override=_cache_root(args) / "legacy-validation.md"
            )
        else:
            _validate_new(args, engine_root)
        return 0
    if args.engine_mode == "compare":
        _compare(args, engine_root)
    elif args.command == "evaluate":
        _evaluate_new(args, engine_root)
    else:
        _new_predict(args, engine_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
