"""The project launcher must never substitute an unqualified global uv."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[2] / "scripts/uv"


def project(tmp_path: Path, version: str = "0.12.5") -> tuple[Path, Path]:
    root = tmp_path / "project with spaces"
    (root / "scripts").mkdir(parents=True)
    shutil.copyfile(LAUNCHER, root / "scripts/uv")
    (root / "pyproject.toml").write_text('[tool.uv]\nrequired-version = "==0.12.5"\n')
    backend = root / ".tools/uv/0.12.5/bin/uv"
    backend.parent.mkdir(parents=True)
    backend.write_text(
        f"#!{sys.executable}\n"
        "import json,os,shutil,sys,subprocess\n"
        f"version={version!r}\n"
        "if sys.argv[1:]==['--version']:\n"
        " print('uv '+version+' (test)');sys.exit(0)\n"
        "print(json.dumps({'args':sys.argv[1:],'cwd':os.getcwd(),"
        "'resolved_uv':shutil.which('uv'),'nested_version':"
        "subprocess.check_output(['uv','--version'],text=True).strip()}))\n"
    )
    backend.chmod(0o755)
    return root, backend


def launch(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - known interpreter and isolated fixture launcher
        [sys.executable, str(root / "scripts/uv"), *args],
        cwd=root.parent,
        capture_output=True,
        text=True,
        check=False,
    )


def test_qualified_local_tool_preserves_arguments_cwd_and_nested_resolution(tmp_path: Path) -> None:
    root, backend = project(tmp_path)
    arguments = ("run", "--frozen", "--all-groups", "file with spaces", "literal$(value)", "")
    outcome = launch(root, *arguments)
    assert outcome.returncode == 0, outcome.stderr
    result = json.loads(outcome.stdout)
    assert result["args"] == list(arguments)
    assert result["cwd"] == str(root)
    assert result["resolved_uv"] == str(backend)
    assert result["nested_version"] == "uv 0.12.5 (test)"


def test_missing_local_tool_does_not_fall_back_to_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, backend = project(tmp_path)
    backend.unlink()
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    marker = tmp_path / "global-was-used"
    global_uv = global_dir / "uv"
    global_uv.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    global_uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(global_dir) + os.pathsep + os.environ["PATH"])
    outcome = launch(root, "lock", "--check", "--offline")
    assert outcome.returncode == 2
    assert "project-local uv" in outcome.stderr
    assert not marker.exists()


@pytest.mark.parametrize("version", ["0.12.9", "0.12.50", "0.12.5rc1"])
def test_wrong_local_version_is_rejected(tmp_path: Path, version: str) -> None:
    root, _ = project(tmp_path, version)
    outcome = launch(root, "run", "pytest")
    assert outcome.returncode == 2
    assert "expected uv 0.12.5" in outcome.stderr
    assert not outcome.stdout


def test_nonexecutable_local_tool_is_rejected(tmp_path: Path) -> None:
    root, backend = project(tmp_path)
    backend.chmod(0o644)
    outcome = launch(root, "--version")
    assert outcome.returncode == 2
    assert "project-local uv" in outcome.stderr


def test_nonexact_repository_pin_is_rejected(tmp_path: Path) -> None:
    root, _ = project(tmp_path)
    (root / "pyproject.toml").write_text('[tool.uv]\nrequired-version = ">=0.12.5"\n')
    outcome = launch(root, "--version")
    assert outcome.returncode == 2
    assert "exact uv version" in outcome.stderr
