import importlib.util
import py_compile
import re
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
FOLDERS = sorted(
    p for p in EXAMPLES.iterdir() if p.is_dir() and p.name != "__pycache__"
)
BOT_FOLDERS = [p for p in FOLDERS if p.name != "rate_limit_demo"]
ENV = ("ITD_TOKEN", "ITD_CHAT", "ITD_USER", "ITD_REFRESH_TOKEN")


def load(folder: Path):
    spec = importlib.util.spec_from_file_location(
        "example_" + folder.name, folder / (folder.name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def test_every_example_folder_has_script_and_short_readme():
    assert len(FOLDERS) >= 8
    for folder in FOLDERS:
        script = folder / (folder.name + ".py")
        readme = folder / "README.md"
        assert script.is_file() and readme.is_file(), folder.name
        assert len(script.read_text(encoding="utf-8").splitlines()) <= 80
        assert 5 <= len(readme.read_text(encoding="utf-8").splitlines()) <= 10


@pytest.mark.parametrize("folder", FOLDERS, ids=lambda p: p.name)
def test_example_compiles_and_imports_without_network_or_token(folder, tmp_path):
    script = folder / (folder.name + ".py")
    py_compile.compile(str(script), cfile=str(tmp_path / "x.pyc"), doraise=True)
    module = load(folder)
    assert callable(module.main)
    assert 'if __name__ == "__main__":' in script.read_text(encoding="utf-8")


@pytest.mark.parametrize("folder", BOT_FOLDERS, ids=lambda p: p.name)
def test_bot_examples_stop_before_any_request_without_credentials(folder):
    with pytest.raises(SystemExit) as stop:
        load(folder).main()
    assert "ITD_" in str(stop.value)


@pytest.mark.parametrize("folder", FOLDERS, ids=lambda p: p.name)
def test_examples_have_no_default_secrets_or_real_ids(folder):
    source = (folder / (folder.name + ".py")).read_text(encoding="utf-8")
    assert not re.search(r"environ\.get\(\s*[\"']ITD_\w+[\"']\s*,", source)
    assert not re.search(r"eyJ[\w-]{10,}", source)


def test_rate_limit_demo_runs_offline_and_prints_expected_pauses(capsys):
    module = load(EXAMPLES / "rate_limit_demo")
    steps = dict(module.run())
    sends = [steps[f"отправка {number} в чат A"] for number in range(1, 6)]
    assert sends == [0, 0, 0, 0, 2.1]
    assert steps["правка в чате A"] == 0 and steps["правка в чате B"] == 0
    assert steps["429 в чате A, Retry-After"] == 5
    assert steps["отправка в чат B после 429 в A"] == 0
    assert steps["отправка в чат A после 429"] == 5
    module.main()
    out = capsys.readouterr().out
    assert "отправка 5 в чат A: 2.1 с" in out
    assert "отправка в чат A после 429: 5 с" in out
