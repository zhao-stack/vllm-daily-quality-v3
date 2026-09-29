from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest import mock

import main2main_risk_analyzer as adapter
import pytest


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", ".")
    _git(
        root,
        "-c",
        "user.name=Skill Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        message,
    )
    return _git(root, "rev-parse", "HEAD")


def _source_pair(tmp_path: Path) -> tuple[Path, Path, str, str, str]:
    vllm = tmp_path / "vllm"
    ascend = tmp_path / "ascend"
    vllm.mkdir()
    ascend.mkdir()
    _git(vllm, "init")
    _git(ascend, "init")
    _write(vllm, "vllm/__init__.py", "")
    _write(
        vllm, "vllm/base.py", "class Base:\n    def run(self, value): return value\n"
    )
    old = _commit(vllm, "old")
    _write(
        vllm,
        "vllm/base.py",
        "class Base:\n    def run(self, value, required): return value\n",
    )
    new = _commit(vllm, "new")
    _write(ascend, "vllm_ascend/__init__.py", "")
    _write(
        ascend,
        "vllm_ascend/child.py",
        "from vllm.base import Base\n\nclass Child(Base):\n    def run(self, value): return value\n",
    )
    ascend_sha = _commit(ascend, "baseline")
    return vllm, ascend, old, new, ascend_sha


def _engine_root() -> Path:
    configured = os.environ.get("VLLM_INTERFACE_ENGINE_TEST_ROOT")
    if not configured:
        pytest.skip(
            "set VLLM_INTERFACE_ENGINE_TEST_ROOT to run engine integration tests"
        )
    root = Path(configured).resolve()
    if not (root / adapter.ENGINE_PACKAGE / "__main__.py").is_file():
        pytest.fail(f"invalid VLLM_INTERFACE_ENGINE_TEST_ROOT: {root}")
    return root


def _arguments(
    tmp_path: Path, sources: tuple[Path, Path, str, str, str], output: Path
) -> list[str]:
    vllm, ascend, old, new, ascend_sha = sources
    return [
        "predict",
        "--engine-root",
        str(_engine_root()),
        "--vllm-root",
        str(vllm),
        "--ascend-root",
        str(ascend),
        "--expect-ascend-sha",
        ascend_sha,
        "--old",
        old,
        "--new",
        new,
        "--cache-dir",
        str(tmp_path / "cache"),
        "--output",
        str(output),
    ]


def test_default_engine_mode_is_new() -> None:
    parser = adapter.build_parser()
    args = parser.parse_args(
        [
            "predict",
            "--vllm-root",
            "v",
            "--ascend-root",
            "a",
            "--old",
            "o",
            "--new",
            "n",
        ]
    )
    assert args.engine_mode == "new"


def test_engine_identity_contains_version_schema_and_hash() -> None:
    identity = adapter._engine_identity(_engine_root())
    assert identity["generator_version"] != "unknown"
    assert identity["range_analyzer_version"] != "unknown"
    assert identity["range_schema_version"] == 3
    assert identity["analysis_plan_version"] == 2
    assert len(str(identity["package_sha256"])) == 64


def test_new_engine_cache_is_pinned_to_main2main_scenario(
    tmp_path: Path,
) -> None:
    sources = _source_pair(tmp_path)
    vllm, ascend, old, new, ascend_sha = sources
    parser = adapter.build_parser()
    args = parser.parse_args(
        [
            "predict",
            "--engine-root",
            str(_engine_root()),
            "--vllm-root",
            str(vllm),
            "--ascend-root",
            str(ascend),
            "--expect-ascend-sha",
            ascend_sha,
            "--old",
            old,
            "--new",
            new,
        ]
    )
    inputs = adapter._cache_inputs(args, _engine_root())
    assert inputs["scenario"] == "main2main"


def test_predict_uses_exact_input_cache(tmp_path: Path) -> None:
    sources = _source_pair(tmp_path)
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    assert adapter.main(_arguments(tmp_path, sources, first)) == 0
    assert "本次升级引入" in first.read_text(encoding="utf-8")
    with mock.patch.object(
        adapter,
        "_run",
        side_effect=AssertionError("engine must not run on a cache hit"),
    ):
        assert adapter.main(_arguments(tmp_path, sources, second)) == 0
    assert first.read_bytes() == second.read_bytes()


def test_refresh_cache_runs_engine_again(tmp_path: Path) -> None:
    sources = _source_pair(tmp_path)
    output = tmp_path / "report.md"
    args = _arguments(tmp_path, sources, output)
    assert adapter.main(args) == 0
    with mock.patch.object(adapter, "_run", wraps=adapter._run) as runner:
        assert adapter.main([*args, "--refresh-cache"]) == 0
    assert runner.call_count == 1
