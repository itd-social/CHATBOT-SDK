import runpy
import shlex
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from itd_bot import __main__ as cli


def test_module_entry_point_shows_help_without_creating_files(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "argv", ["itd_bot", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(cli.__file__, run_name="__main__")
    assert exit_info.value.code == 0
    assert "init" in capsys.readouterr().out


def test_init_creates_project_and_installs_only_in_its_venv(
    tmp_path, monkeypatch, capsys
):
    project = tmp_path / "папка с пробелами" / "my-bot"
    run = Mock()
    monkeypatch.setattr(cli.subprocess, "run", run)

    assert cli.main(["init", str(project)]) == 0
    assert (project / ".env").read_text(encoding="utf-8") == "ITD_REFRESH_TOKEN=\n"
    assert not (project / "tests").exists()
    create, install = run.call_args_list
    assert create.args[0] == [cli.sys.executable, "-m", "venv", str(project / ".venv")]
    command = install.args[0]
    assert Path(command[0]).is_relative_to(project / ".venv")
    assert command[1:] == [
        "-m",
        "pip",
        "install",
        "-r",
        str(project / "requirements.txt"),
    ]
    for call in run.call_args_list:
        assert call.kwargs == {
            "check": True,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
    output = capsys.readouterr().out
    assert str(project / ".env") in output
    assert cli._command([command[0], str(project / "bot.py")]) in output


@pytest.mark.parametrize("kind", ["file", "empty", "populated"])
def test_init_refuses_existing_path_without_changing_files(
    tmp_path, monkeypatch, capsys, kind
):
    project = tmp_path / "existing"
    if kind == "file":
        project.write_bytes(b"keep")
    else:
        project.mkdir()
        if kind == "populated":
            (project / "bot.py").write_bytes(b"keep")
    run = Mock()
    monkeypatch.setattr(cli.subprocess, "run", run)

    assert cli.main(["init", str(project)]) == 1
    run.assert_not_called()
    assert "Путь уже существует" in capsys.readouterr().err
    if kind == "file":
        assert project.read_bytes() == b"keep"
    elif kind == "populated":
        assert (project / "bot.py").read_bytes() == b"keep"
        assert len(list(project.iterdir())) == 1
    else:
        assert not list(project.iterdir())


def test_init_reports_filesystem_error(tmp_path, monkeypatch, capsys):
    def denied(*args, **kwargs):
        raise PermissionError("PRIVATE_ENVIRONMENT_DATA")

    monkeypatch.setattr(Path, "mkdir", denied)
    assert cli.main(["init", str(tmp_path / "bot")]) == 1
    error = capsys.readouterr().err
    assert "Проверьте путь" in error and "PRIVATE_ENVIRONMENT_DATA" not in error


@pytest.mark.parametrize("step", [1, 2])
@pytest.mark.parametrize("failure", [OSError, subprocess.CalledProcessError])
def test_init_keeps_files_and_explains_failed_venv_or_install(
    tmp_path, monkeypatch, capsys, step, failure
):
    error = (
        failure(1, "PRIVATE_PROXY_PASSWORD")
        if failure is subprocess.CalledProcessError
        else failure("PRIVATE_PROXY_PASSWORD")
    )
    run = Mock(side_effect=([None] if step == 2 else []) + [error])
    monkeypatch.setattr(cli.subprocess, "run", run)
    project = tmp_path / "new bot"

    assert cli.main(["init", str(project)]) == 1
    assert (project / "bot.py").is_file()
    assert run.call_count == step
    output = capsys.readouterr().err
    assert "PRIVATE_PROXY_PASSWORD" not in output
    assert ("venv" if step == 1 else "PyPI") in output
    if step == 2:
        assert cli._command(run.call_args.args[0]) in output


def test_init_interruption_keeps_created_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.subprocess, "run", Mock(side_effect=KeyboardInterrupt))
    project = tmp_path / "new bot"
    assert cli.main(["init", str(project)]) == 130
    assert (project / "bot.py").exists()
    assert "Создание остановлено" in capsys.readouterr().err


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_printed_command_quotes_spaces_and_shell_characters(monkeypatch, platform):
    arguments = ["python's folder/$python.exe", "bot's folder/`bot.py"]
    with monkeypatch.context() as context:
        context.setattr(cli.os, "name", platform)
        command = cli._command(arguments)
    if platform == "nt":
        assert command == "& 'python''s folder/$python.exe' 'bot''s folder/`bot.py'"
    else:
        assert shlex.split(command) == arguments
