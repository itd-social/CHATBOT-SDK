import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("semantic_release")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("fix: correct session handling", "1.0.1"),
        ("feature: add command", "1.1.0"),
        ("refactor: simplify parser", "1.0.1"),
        ("feat!: change public api", "2.0.0"),
    ],
)
def test_semantic_release_updates_version_and_changelog(tmp_path, message, expected):
    if shutil.which("git") is None:
        pytest.skip("Git is required")
    root = Path(__file__).resolve().parents[1]
    repo = tmp_path / "repo"
    repo.mkdir()
    for filename in ("pyproject.toml", "CHANGELOG.md"):
        shutil.copy2(root / filename, repo / filename)
    env = os.environ.copy()
    env.update(
        {
            "GH_TOKEN": "test-token",
            "PYTHONUTF8": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
    )

    def run(*args):
        return subprocess.run(
            args,
            cwd=repo,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()

    run("git", "init", "-b", "main")
    run("git", "config", "user.name", "Release Test")
    run("git", "config", "user.email", "release-test@example.invalid")
    run("git", "config", "commit.gpgsign", "false")
    run("git", "config", "core.hooksPath", ".disabled-hooks")
    run("git", "add", "pyproject.toml", "CHANGELOG.md")
    run("git", "commit", "-m", "feat: initial release")
    run("git", "tag", "v1.0.0")
    (repo / "change.txt").write_text("test", encoding="utf-8")
    run("git", "add", "change.txt")
    run("git", "commit", "-m", message)
    run("git", "remote", "add", "origin", "https://github.com/example/itd-sdk.git")
    run("git", "update-ref", "refs/remotes/origin/main", "HEAD")
    run("git", "branch", "--set-upstream-to=origin/main", "main")
    assert (
        run(sys.executable, "-m", "semantic_release", "--noop", "version", "--print")
        == expected
    )
    run(
        sys.executable,
        "-m",
        "semantic_release",
        "version",
        "--no-push",
        "--no-vcs-release",
        "--skip-build",
    )
    assert run("git", "describe", "--tags", "--exact-match") == "v" + expected
    assert f'version = "{expected}"' in (repo / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert message.split(": ", 1)[1] in changelog.lower()
    assert "Все заметные изменения" in changelog


def test_first_release_starts_at_one(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("Git is required")
    root = Path(__file__).resolve().parents[1]
    repo = tmp_path / "repo"
    repo.mkdir()
    for filename in ("pyproject.toml", "CHANGELOG.md"):
        shutil.copy2(root / filename, repo / filename)
    env = os.environ.copy()
    env.update(
        {
            "GH_TOKEN": "test-token",
            "PYTHONUTF8": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
    )

    def run(*args):
        return subprocess.run(
            args,
            cwd=repo,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()

    run("git", "init", "-b", "main")
    run("git", "config", "user.name", "Release Test")
    run("git", "config", "user.email", "release-test@example.invalid")
    run("git", "config", "commit.gpgsign", "false")
    run("git", "config", "core.hooksPath", ".disabled-hooks")
    run("git", "add", "pyproject.toml", "CHANGELOG.md")
    run("git", "commit", "-m", "feat: initial release")
    run("git", "remote", "add", "origin", "https://github.com/example/itd-sdk.git")
    run("git", "update-ref", "refs/remotes/origin/main", "HEAD")
    run("git", "branch", "--set-upstream-to=origin/main", "main")
    assert (
        run(
            sys.executable,
            "-m",
            "semantic_release",
            "--noop",
            "version",
            "--print",
            "--major",
        )
        == "1.0.0"
    )
    run(
        sys.executable,
        "-m",
        "semantic_release",
        "version",
        "--major",
        "--no-push",
        "--no-vcs-release",
        "--skip-build",
    )
    assert run("git", "describe", "--tags", "--exact-match") == "v1.0.0"
    assert 'version = "1.0.0"' in (repo / "pyproject.toml").read_text(encoding="utf-8")
