from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import main2main_risk_analyzer as adapter
import pytest


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, encoding="utf-8"
    ).strip()


@pytest.fixture
def pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "engine"
    root.mkdir()
    git(root, "init")
    git(root, "config", "core.autocrlf", "false")
    git(root, "remote", "add", "origin", "https://github.com/example/engine.git")
    source = root / adapter.ENGINE_PACKAGE / "__main__.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(
        b"raise RuntimeError('must not execute during identity checks')\n"
    )
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "engine",
    )
    lock = {
        "repository": "https://github.com/example/engine.git",
        "commit": git(root, "rev-parse", "HEAD"),
        "files": [
            {
                "file": "__main__.py",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        ],
    }
    lock_path = tmp_path / "engine-lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    monkeypatch.setattr(adapter, "ENGINE_LOCK", lock_path)
    return root, source, lock


def test_exact_checkout_accepted_without_executing_it(pinned):
    root, _, _ = pinned
    assert adapter._engine_root(argparse.Namespace(engine_root=str(root))) == root


def test_invalid_explicit_root_never_falls_back_to_baseline(pinned, tmp_path):
    root, _, _ = pinned
    with pytest.raises(SystemExit, match="不回退"):
        adapter._engine_root(
            argparse.Namespace(
                engine_root=str(tmp_path / "missing"), ascend_root=str(root)
            )
        )


def test_wrong_commit_rejected(pinned):
    root, _, lock = pinned
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "--allow-empty",
        "-m",
        "later",
    )
    with pytest.raises(SystemExit, match="SHA 不匹配"):
        adapter._verify_engine(root, lock)


def test_wrong_origin_rejected(pinned):
    root, _, lock = pinned
    git(root, "remote", "set-url", "origin", "https://github.com/other/engine.git")
    with pytest.raises(SystemExit, match="来源或 SHA"):
        adapter._verify_engine(root, lock)


def test_modified_code_rejected_even_when_git_status_hides_it(pinned):
    root, source, lock = pinned
    git(root, "update-index", "--assume-unchanged", source.relative_to(root).as_posix())
    source.write_bytes(b"print('changed')\n")
    with pytest.raises(SystemExit, match="文件哈希不匹配"):
        adapter._verify_engine(root, lock)


def test_extra_untracked_module_rejected(pinned):
    root, source, lock = pinned
    source.with_name("extra.py").write_bytes(b"pass\n")
    with pytest.raises(SystemExit, match="文件集合"):
        adapter._verify_engine(root, lock)
