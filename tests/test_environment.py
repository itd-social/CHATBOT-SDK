"""Environment configuration is explicit, typed and free of credential output."""

import builtins
import importlib.util
import os
import subprocess
import sys
import traceback
from dataclasses import fields
from pathlib import Path

import httpx
import pytest
from helpers import BASE, FAR_FUTURE, Transport, jwt

from itd_bot import Bot, Dispatcher, RateLimitConfig, RateLimiter


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.setattr(
        os,
        "environ",
        {key: value for key, value in os.environ.items() if not key.startswith("ITD_")},
    )


def test_token_is_read_lazily_and_never_appears_in_options_or_repr(monkeypatch, caplog):
    secret = "fictional-private-access-token"
    monkeypatch.setenv("ITD_TOKEN", secret)
    transport = Transport(httpx.Response(200, json={"chats": []}))
    bot = Bot.from_env(client=transport.client(), base_url=BASE)
    assert transport.requests == []
    assert bot.poll_options == {} and bot.dispatcher_options == {}
    assert secret not in repr(bot) + repr(bot.poll_options) + repr(
        bot.dispatcher_options
    )
    assert bot.get_chats() == {"chats": []}
    assert transport.requests[0].headers["Authorization"] == "Bearer " + secret
    assert secret not in caplog.text


def test_refresh_only_environment_uses_existing_refresh_flow(monkeypatch):
    monkeypatch.setenv("ITD_REFRESH_TOKEN", "fictional-refresh")
    monkeypatch.setenv("ITD_SESSION_PATH", "")
    transport = Transport(
        httpx.Response(200, json={"accessToken": jwt("self")}),
        httpx.Response(200, json={"chats": []}),
    )
    bot = Bot.from_env(client=transport.client(), base_url=BASE)
    assert transport.requests == []
    bot.get_chats()
    assert transport.paths == ["/api/v1/auth/refresh", "/api/v1/messenger/chats"]
    assert bot.refresh_count == 1


def test_both_tokens_keep_401_refresh_and_rotation(monkeypatch):
    monkeypatch.setenv("ITD_TOKEN", jwt("self"))
    monkeypatch.setenv("ITD_REFRESH_TOKEN", "fictional-refresh")
    monkeypatch.setenv("ITD_SESSION_PATH", "")
    fresh = jwt("self", FAR_FUTURE + 60)
    transport = Transport(
        httpx.Response(401, json={}),
        httpx.Response(200, json={"accessToken": fresh}),
        httpx.Response(200, json={"chats": []}),
    )
    bot = Bot.from_env(client=transport.client(), base_url=BASE)
    bot.get_chats()
    assert (
        bot.refresh_count == 1
        and transport.requests[-1].headers["Authorization"] == "Bearer " + fresh
    )


@pytest.mark.parametrize(
    "tokens",
    [
        {},
        {"ITD_TOKEN": ""},
        {"ITD_REFRESH_TOKEN": ""},
        {"ITD_TOKEN": "", "ITD_REFRESH_TOKEN": ""},
    ],
)
def test_missing_credentials_fail_before_http(tokens, monkeypatch):
    for name, value in tokens.items():
        monkeypatch.setenv(name, value)
    transport = Transport()
    with pytest.raises(ValueError, match="ITD_TOKEN or ITD_REFRESH_TOKEN"):
        Bot.from_env(client=transport.client())
    assert transport.requests == []


def test_explicit_credentials_and_session_override_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("ITD_TOKEN", "invalid\nfictional-env-token")
    monkeypatch.setenv("ITD_REFRESH_TOKEN", "invalid\nfictional-refresh")
    monkeypatch.setenv("ITD_SESSION_PATH", str(tmp_path / "unused.json"))
    transport = Transport(httpx.Response(200, json={}))
    bot = Bot.from_env(
        token="fictional-override",
        refresh_token=None,
        session_path=None,
        client=transport.client(),
        base_url=BASE,
    )
    bot.get_chats()
    assert transport.requests[0].headers["Authorization"] == "Bearer fictional-override"
    assert list(tmp_path.iterdir()) == []


def test_session_path_is_passed_to_existing_persistence(monkeypatch, tmp_path):
    session = tmp_path / "session.json"
    monkeypatch.setenv("ITD_REFRESH_TOKEN", "fictional-refresh")
    monkeypatch.setenv("ITD_SESSION_PATH", str(session))
    transport = Transport(
        httpx.Response(
            200,
            json={"accessToken": jwt("self")},
            headers={"Set-Cookie": "refresh_token=fictional-rotated; Path=/"},
        ),
        httpx.Response(200, json={}),
    )
    bot = Bot.from_env(client=transport.client(), base_url=BASE)
    bot.get_chats()
    assert session.is_file()
    assert "fictional-rotated" in session.read_text()


@pytest.mark.parametrize("mode", ["blocking", "background", "batch", "off"])
def test_settings_are_copied_and_explicitly_forwarded(mode, monkeypatch):
    for name, value in {
        "ITD_TOKEN": "fictional-token",
        "ITD_WORKERS": "3",
        "ITD_POLL_INTERVAL": "5.5",
        "ITD_READ_MODE": mode,
    }.items():
        monkeypatch.setenv(name, value)
    bot = Bot.from_env(client=Transport().client())
    assert bot.poll_options == {"workers": 3, "interval": 5.5}
    assert bot.dispatcher_options == {"read_mode": mode}
    options = bot.poll_options
    options["workers"] = 99
    bot.dispatcher_options.clear()
    assert bot.poll_options["workers"] == 3 and bot.dispatcher_options == {
        "read_mode": mode
    }
    calls = []
    monkeypatch.setattr(
        bot, "poll", lambda since, handler, **kwargs: calls.append(kwargs)
    )
    router = Dispatcher(bot, self_user_id="self", **bot.dispatcher_options)
    router.run_polling(0, handle_signals=False, **bot.poll_options)
    assert calls == [{"workers": 3, "interval": 5.5}]


def test_regular_bot_ignores_environment_and_from_env_takes_a_snapshot(monkeypatch):
    monkeypatch.setenv("ITD_WORKERS", "invalid-private-value")
    bot = Transport().bot()
    assert bot.poll_options == {} and bot.dispatcher_options == {}
    monkeypatch.setenv("ITD_TOKEN", "fictional-token")
    monkeypatch.setenv("ITD_WORKERS", "2")
    configured = Bot.from_env(client=Transport().client())
    monkeypatch.setenv("ITD_WORKERS", "8")
    assert configured.poll_options == {"workers": 2}


RATE_VALUES = {
    "enabled": ("false", False),
    "wait": (" False ", False),
    "requests_per_minute": ("17", 17),
    "min_interval": ("0.25", 0.25),
    "send_interval": ("1.5", 1.5),
    "reaction_interval": ("0", 0.0),
    "edit_interval": ("3", 3.0),
    "delete_for_me_interval": ("4", 4.0),
    "send_burst": ("5", 5),
    "edit_burst": ("2", 2),
    "reaction_burst": ("7", 7),
    "delete_for_me_burst": ("9", 9),
}


@pytest.mark.parametrize("field", RATE_VALUES)
def test_every_rate_field_uses_its_existing_type(field, monkeypatch):
    value, expected = RATE_VALUES[field]
    monkeypatch.setenv("ITD_TOKEN", "fictional-token")
    monkeypatch.setenv("ITD_RATE_" + field.upper(), value)
    bot = Bot.from_env(client=Transport().client())
    assert getattr(bot._rate_limits.config, field) == expected
    assert set(RATE_VALUES) == {item.name for item in fields(RateLimitConfig)}


def test_none_request_budget_and_true_boolean_are_explicit(monkeypatch):
    monkeypatch.setenv("ITD_TOKEN", "fictional-token")
    monkeypatch.setenv("ITD_RATE_REQUESTS_PER_MINUTE", " None ")
    monkeypatch.setenv("ITD_RATE_WAIT", "TRUE")
    bot = Bot.from_env(client=Transport().client())
    assert bot._rate_limits.config.requests_per_minute is None
    assert bot._rate_limits.config.wait is True


def test_default_shared_limiter_and_explicit_override_are_preserved(monkeypatch):
    monkeypatch.setenv("ITD_TOKEN", "fictional-token")
    shared = RateLimiter(RateLimitConfig(enabled=False))
    monkeypatch.setattr("itd_bot.client.default_limiter", lambda origin: shared)
    assert Bot.from_env(client=Transport().client())._rate_limits is shared
    monkeypatch.setenv("ITD_RATE_SEND_INTERVAL", "invalid-private-value")
    assert (
        Bot.from_env(client=Transport().client(), limiter=shared)._rate_limits is shared
    )
    assert (
        Bot.from_env(client=Transport().client(), limiter=None)._rate_limits is shared
    )


@pytest.mark.parametrize(
    "name,value",
    [
        ("ITD_WORKERS", "fictional-private-value"),
        ("ITD_WORKERS", "0"),
        ("ITD_WORKERS", "1.5"),
        ("ITD_POLL_INTERVAL", "fictional-private-value"),
        ("ITD_POLL_INTERVAL", "nan"),
        ("ITD_POLL_INTERVAL", "inf"),
        ("ITD_POLL_INTERVAL", "-1"),
        ("ITD_POLL_INTERVAL", "0"),
        ("ITD_READ_MODE", "fictional-private-value"),
        ("ITD_RATE_ENABLED", "fictional-private-value"),
        ("ITD_RATE_REQUESTS_PER_MINUTE", "0"),
        ("ITD_RATE_SEND_BURST", "-1"),
        ("ITD_RATE_MIN_INTERVAL", "-1"),
        ("ITD_RATE_SEND_INTERVAL", "nan"),
    ],
)
def test_bad_settings_fail_safely_before_client_creation(
    name, value, monkeypatch, caplog
):
    monkeypatch.setenv("ITD_TOKEN", "fictional-private-access")
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        "itd_bot.client.httpx.Client",
        lambda **kwargs: pytest.fail("client created too early"),
    )
    with pytest.raises(ValueError, match=name) as caught:
        Bot.from_env()
    rendered = (
        "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
        + caplog.text
    )
    assert "fictional-private-access" not in rendered
    if value == "fictional-private-value":
        assert value not in rendered


def test_sdk_does_not_load_dotenv_automatically(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ITD_TOKEN=fictional-file-token\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Set ITD_TOKEN"):
        Bot.from_env(client=Transport().client())
    original = builtins.__import__

    def no_dotenv(name, *args, **kwargs):
        if name == "dotenv" or name.startswith("dotenv."):
            pytest.fail("SDK imported dotenv")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_dotenv)
    monkeypatch.setenv("ITD_TOKEN", "fictional-env-token")
    assert Bot.from_env(client=Transport().client()).poll_options == {}


def load_example():
    path = Path(__file__).resolve().parents[1] / "examples/bot_env.py"
    spec = importlib.util.spec_from_file_location("bot_env_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_env_example_imports_without_dotenv_and_explains_missing_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "dotenv", None)
    module = load_example()
    with pytest.raises(SystemExit, match=r"itd-chatbot-sdk\[env\]"):
        module.main()


def test_real_dotenv_is_explicit_and_system_environment_wins(monkeypatch, tmp_path):
    pytest.importorskip("dotenv")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "ITD_TOKEN=fictional-file-token\nITD_WORKERS=3\nITD_READ_MODE=off\nITD_POLL_INTERVAL=5\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ITD_TOKEN", "fictional-system-token")
    transport = Transport(httpx.Response(200, json={"id": "self", "username": "bot"}))
    factory = Bot.from_env
    monkeypatch.setattr(
        Bot,
        "from_env",
        classmethod(
            lambda cls, **kwargs: factory(
                client=transport.client(), base_url=BASE, **kwargs
            )
        ),
    )
    calls = []
    monkeypatch.setattr(
        Dispatcher,
        "run_polling",
        lambda self, **kwargs: calls.append((self.read_mode, kwargs)),
    )
    load_example().main()
    assert calls == [
        ("off", {"state_path": ".bot-state.json", "workers": 3, "interval": 5.0})
    ]
    assert (
        transport.requests[0].headers["Authorization"]
        == "Bearer fictional-system-token"
    )


def test_import_and_factory_do_not_require_optional_dotenv_in_fresh_process(
    monkeypatch,
):
    monkeypatch.setenv("ITD_TOKEN", "fictional-token")
    code = "import sys; from itd_bot import Bot; bot=Bot.from_env(session_path=None); assert 'dotenv' not in sys.modules; bot.close()"
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stderr
