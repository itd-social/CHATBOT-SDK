"""Failure paths of RefreshSession: the bot's session must never be lost silently."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from http.cookies import CookieError

import httpx
import pytest
from helpers import (
    BASE,
    FAR_FUTURE,
    Transport,
    jwt,
    patch_os,
)

import itd_bot.auth as auth
from itd_bot import APIError, Bot, ProtocolError, RateLimitError, SessionStorageError
from itd_bot.auth import RefreshSession, _claims, _validate_refresh
from itd_bot.rate_limits import RateLimitState

SEED = "fictional-seed"
STORED = "fictional-stored-token"


def disk_full(*args, **kwargs):
    raise OSError("disk full")


def session(
    tmp_path, transport, *, seed=SEED, access_token=None, name="session.json", **kw
):
    path = tmp_path / name if name else None
    return RefreshSession(
        seed,
        client=transport.client(),
        base_url=BASE,
        session_path=path,
        access_token=access_token,
        **kw,
    )


def refreshed(cookie=None, *, access=None):
    headers = []
    if cookie is not None:
        headers.append(("set-cookie", f"refresh_token={cookie}; Path=/api/v1/auth"))
    return httpx.Response(
        200, headers=headers, json={"accessToken": access or jwt("alice")}
    )


def stored(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_state(path, *, seed=SEED, drop=(), **changes):
    state = {
        "version": 1,
        "seed_fingerprint": hashlib.sha256(seed.encode("ascii")).hexdigest(),
        "base_url": BASE,
        "refresh_token": STORED,
        "subject": "alice",
        **changes,
    }
    for key in drop:
        del state[key]
    path.write_text(json.dumps(state), encoding="utf-8")


def leftovers(directory):
    return sorted(p.name for p in directory.iterdir())


# --- reading a saved session -------------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"subject": 123},
        {"subject": ["alice"]},
        {"version": 2},
        {"base_url": "https://other.test"},
        {"seed_fingerprint": "0" * 64},
        {"refresh_token": "has space"},
        {"refresh_token": 5},
        {"refresh_token": "x" * 8193},
        {"drop": ("refresh_token",)},
    ],
    ids=[
        "subject-not-string",
        "subject-list",
        "unknown-version",
        "other-base-url",
        "other-initial-token",
        "token-with-space",
        "token-not-string",
        "token-too-long",
        "token-missing",
    ],
)
def test_unusable_saved_session_is_refused_and_left_untouched(tmp_path, changes):
    path = tmp_path / "session.json"
    write_state(path, **changes)
    before = path.read_bytes()
    transport = Transport()
    with pytest.raises(SessionStorageError) as info:
        session(tmp_path, transport, access_token=jwt("alice"))
    assert path.read_bytes() == before
    assert transport.requests == []
    assert STORED not in str(info.value) and SEED not in str(info.value)


@pytest.mark.parametrize(
    "raw",
    [b"{", b"[]", b"null", b"", b"\xff\xfe\x00"],
    ids=["json", "list", "null", "empty", "binary"],
)
def test_corrupt_session_file_is_refused_and_left_untouched(tmp_path, raw):
    path = tmp_path / "session.json"
    path.write_bytes(raw)
    with pytest.raises(SessionStorageError):
        session(tmp_path, Transport())
    assert path.read_bytes() == raw


def test_session_path_that_is_a_directory_is_a_storage_error(tmp_path):
    with pytest.raises(SessionStorageError) as info:
        RefreshSession(
            SEED,
            client=Transport().client(),
            base_url=BASE,
            session_path=tmp_path,
        )
    assert isinstance(info.value.__cause__, OSError)


def test_session_of_another_account_is_refused_without_naming_accounts(tmp_path):
    path = tmp_path / "session.json"
    write_state(path, subject="alice-account")
    before = path.read_bytes()
    with pytest.raises(SessionStorageError) as info:
        session(tmp_path, Transport(), access_token=jwt("bob-account"))
    message = str(info.value) + repr(info.value.__cause__)
    assert "alice-account" not in message and "bob-account" not in message
    assert path.read_bytes() == before


def test_saved_session_of_the_same_account_loads(tmp_path):
    path = tmp_path / "session.json"
    write_state(path, subject="alice")
    loaded = session(tmp_path, Transport(), access_token=jwt("alice"))
    assert loaded.subject == "alice"


def test_saved_account_fills_in_an_unknown_one(tmp_path):
    write_state(tmp_path / "session.json", subject="alice")
    assert session(tmp_path, Transport()).subject == "alice"


def test_session_in_missing_directory_fails_without_creating_anything(tmp_path):
    missing = tmp_path / "missing" / "session.json"
    with pytest.raises(SessionStorageError) as info:
        RefreshSession(
            SEED, client=Transport().client(), base_url=BASE, session_path=missing
        )
    assert isinstance(info.value.__cause__, OSError)
    assert leftovers(tmp_path) == []


# --- writing a session -------------------------------------------------------


def test_first_save_failure_leaves_no_files(tmp_path, monkeypatch):
    patch_os(monkeypatch, auth, replace=disk_full)
    with pytest.raises(SessionStorageError):
        session(tmp_path, Transport())
    assert leftovers(tmp_path) == []


@pytest.mark.parametrize("failing", ["replace", "fsync"])
def test_failed_save_keeps_the_previous_session_and_removes_the_temp_file(
    tmp_path, monkeypatch, failing
):
    transport = Transport(refreshed("rotated-cookie"))
    current = session(tmp_path, transport)
    path = tmp_path / "session.json"
    before = path.read_bytes()
    patch_os(monkeypatch, auth, **{failing: disk_full})
    with pytest.raises(SessionStorageError) as info:
        current.get_access_token()
    assert isinstance(info.value.__cause__, OSError)
    assert path.read_bytes() == before
    assert leftovers(tmp_path) == ["session.json"]


def test_cleanup_failure_does_not_hide_the_save_error(tmp_path, monkeypatch):
    patch_os(monkeypatch, auth, replace=disk_full, unlink=disk_full)
    with pytest.raises(SessionStorageError) as info:
        session(tmp_path, Transport())
    assert str(info.value.__cause__) == "disk full"


def test_rotated_cookie_survives_a_failed_save_and_the_old_one_is_not_reused(
    tmp_path, monkeypatch
):
    transport = Transport(refreshed("rotated-one"), refreshed("rotated-two"))
    current = session(tmp_path, transport)
    path = tmp_path / "session.json"
    real_replace, failures = os.replace, [1]

    def flaky_replace(source, target):
        if failures[0]:
            failures[0] -= 1
            raise OSError("disk full")
        real_replace(source, target)

    patch_os(monkeypatch, auth, replace=flaky_replace)
    with pytest.raises(SessionStorageError):
        current.get_access_token()
    assert stored(path)["refresh_token"] == SEED

    assert current.get_access_token()
    cookies = [request.headers["cookie"] for request in transport.requests]
    assert cookies == [f"refresh_token={SEED}", "refresh_token=rotated-one"]
    assert stored(path)["refresh_token"] == "rotated-two"
    assert current.refresh_count == 1


def test_while_storage_stays_broken_no_further_rotation_is_attempted(
    tmp_path, monkeypatch
):
    transport = Transport(refreshed("rotated-one"))
    current = session(tmp_path, transport)
    patch_os(monkeypatch, auth, replace=disk_full)
    for _ in range(3):
        with pytest.raises(SessionStorageError):
            current.get_access_token()
    assert len(transport.requests) == 1


def test_401_refresh_with_failed_save_recovers_without_the_old_cookie(
    tmp_path, monkeypatch
):
    stale = jwt("alice", FAR_FUTURE)
    fresh = jwt("alice", FAR_FUTURE + 1)
    transport = Transport(
        httpx.Response(401, json={}),
        refreshed("rotated-one", access=fresh),
        httpx.Response(401, json={}),
        refreshed("rotated-two", access=jwt("alice", FAR_FUTURE + 2)),
        httpx.Response(200, json={"chats": []}),
    )
    path = tmp_path / "session.json"
    bot = Bot(
        stale,
        refresh_token=SEED,
        session_path=path,
        base_url=BASE,
        client=transport.client(),
    )
    real_replace, failures = os.replace, [1]

    def flaky_replace(source, target):
        if failures[0]:
            failures[0] -= 1
            raise OSError("disk full")
        real_replace(source, target)

    patch_os(monkeypatch, auth, replace=flaky_replace)
    with pytest.raises(SessionStorageError):
        bot.get_chats()
    assert stored(path)["refresh_token"] == SEED

    assert bot.get_chats() == {"chats": []}
    cookies = [
        request.headers["cookie"]
        for request in transport.requests
        if request.url.path.endswith("/auth/refresh")
    ]
    assert cookies == [f"refresh_token={SEED}", "refresh_token=rotated-one"]
    assert stored(path)["refresh_token"] == "rotated-two"


# --- known and unknown accounts ----------------------------------------------


def test_set_access_token_learns_the_account_and_saves_it(tmp_path):
    transport = Transport()
    current = session(tmp_path, transport)
    assert current.subject is None
    token = jwt("alice")
    current.set_access_token(token)
    assert current.subject == "alice"
    assert stored(tmp_path / "session.json")["subject"] == "alice"
    assert current.get_access_token() == token
    assert transport.requests == []


def test_set_access_token_without_account_claim_keeps_the_account_unknown(tmp_path):
    current = session(tmp_path, Transport())
    before = (tmp_path / "session.json").read_bytes()
    current.set_access_token("opaque-fictional-token")
    assert current.subject is None
    assert current.get_access_token() == "opaque-fictional-token"
    assert (tmp_path / "session.json").read_bytes() == before


def test_set_access_token_that_cannot_be_saved_is_not_adopted(tmp_path, monkeypatch):
    new_access = jwt("alice", FAR_FUTURE + 1)
    transport = Transport(refreshed("rotated-one", access=new_access))
    current = session(tmp_path, transport)
    rejected = jwt("alice")
    with monkeypatch.context() as patch:
        patch_os(patch, auth, replace=disk_full)
        with pytest.raises(SessionStorageError):
            current.set_access_token(rejected)
    assert current.get_access_token() == new_access
    assert stored(tmp_path / "session.json")["subject"] == "alice"


@pytest.mark.parametrize(
    "other", [jwt("bob"), jwt(None), "opaque-token"], ids=["other", "no-sub", "opaque"]
)
def test_token_of_another_account_is_refused_and_changes_nothing(tmp_path, other):
    first = jwt("alice")
    current = session(tmp_path, Transport(), access_token=first)
    state = current.get_access_state()
    before = (tmp_path / "session.json").read_bytes()
    with pytest.raises(ProtocolError, match="different account"):
        current.set_access_token(other)
    assert current.get_access_state() == state
    assert (tmp_path / "session.json").read_bytes() == before


def test_bot_set_token_of_another_account_keeps_using_the_old_token(tmp_path):
    first = jwt("alice")
    transport = Transport(default=httpx.Response(200, json={"chats": []}))
    bot = Bot(
        first,
        refresh_token=SEED,
        session_path=tmp_path / "s.json",
        base_url=BASE,
        client=transport.client(),
    )
    with pytest.raises(ProtocolError):
        bot.set_token(jwt("bob"))
    bot.get_chats()
    assert transport.requests[0].headers["authorization"] == "Bearer " + first


def test_refresh_learns_the_account_and_saves_it(tmp_path):
    current = session(tmp_path, Transport(refreshed(access=jwt("alice"))))
    current.get_access_token()
    assert current.subject == "alice"
    assert stored(tmp_path / "session.json")["subject"] == "alice"


def test_refresh_for_another_account_is_refused(tmp_path):
    old = jwt("alice")
    transport = Transport(refreshed(access=jwt("bob")))
    current = session(tmp_path, transport, access_token=old)
    with pytest.raises(ProtocolError, match="different account"):
        current.refresh_after_401(old, 0)
    assert current.refresh_count == 0
    assert current.get_access_state() == (old, 0)


# --- refresh responses -------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "code", "errors", "retry_after"),
    [
        (httpx.Response(500, content=b"<html>oops</html>"), None, None, None),
        (
            httpx.Response(
                401, json={"error": {"code": "INVALID_SESSION", "errors": ["x"]}}
            ),
            "INVALID_SESSION",
            ["x"],
            None,
        ),
        (httpx.Response(403, json={"code": "FLAT_CODE"}), "FLAT_CODE", None, None),
        (
            httpx.Response(
                503, headers={"Retry-After": "7"}, json=["not", "an", "object"]
            ),
            None,
            None,
            "7",
        ),
        (httpx.Response(400, json={"error": "plain text"}), None, None, None),
    ],
    ids=["html", "wrapped", "flat", "list-with-retry-after", "error-not-object"],
)
def test_refresh_http_error_becomes_api_error_and_changes_nothing(
    tmp_path, response, code, errors, retry_after
):
    old = jwt("alice")
    transport = Transport(response)
    current = session(tmp_path, transport, access_token=old)
    before = (tmp_path / "session.json").read_bytes()
    with pytest.raises(APIError) as info:
        current.refresh_after_401(old, 0)
    error = info.value
    assert (error.status, error.code, error.errors, error.retry_after) == (
        response.status_code,
        code,
        errors,
        retry_after,
    )
    assert str(error) == "Session refresh failed"
    assert current.get_access_state() == (old, 0) and current.refresh_count == 0
    assert (tmp_path / "session.json").read_bytes() == before


def test_refresh_429_sets_a_cooldown_that_blocks_the_next_attempt(tmp_path):
    transport = Transport(httpx.Response(429, headers={"Retry-After": "9"}, json={}))
    current = session(
        tmp_path, transport, rate_limits=RateLimitState(monotonic=lambda: 100.0)
    )
    with pytest.raises(RateLimitError) as first:
        current.get_access_token()
    assert first.value.retry_after_seconds == 9 and not first.value.locally_blocked
    with pytest.raises(RateLimitError) as second:
        current.get_access_token()
    assert second.value.locally_blocked
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, json=["accessToken"]),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"accessToken": None}),
        httpx.Response(200, json={"accessToken": 12345}),
        httpx.Response(200, json={"accessToken": ""}),
        httpx.Response(200, json={"accessToken": "header.payload\nsignature"}),
        httpx.Response(200, json={"accessToken": "header.payload\rsignature"}),
    ],
    ids=[
        "not-json",
        "list",
        "missing",
        "null",
        "number",
        "empty",
        "newline",
        "carriage-return",
    ],
)
def test_unusable_refresh_body_is_a_protocol_error(tmp_path, response):
    old = jwt("alice")
    current = session(tmp_path, Transport(response), access_token=old)
    with pytest.raises(ProtocolError):
        current.refresh_after_401(old, 0)
    assert current.get_access_state() == (old, 0) and current.refresh_count == 0


def test_rotated_cookie_is_saved_even_when_the_body_is_unusable(tmp_path):
    response = httpx.Response(
        200,
        headers=[("set-cookie", "refresh_token=rotated-cookie; Path=/")],
        content=b"not json",
    )
    current = session(tmp_path, Transport(response))
    with pytest.raises(ProtocolError, match="Invalid refresh response"):
        current.get_access_token()
    assert stored(tmp_path / "session.json")["refresh_token"] == "rotated-cookie"


def test_conflicting_cookies_are_refused_and_nothing_is_saved(tmp_path):
    response = httpx.Response(
        200,
        headers=[
            ("set-cookie", "refresh_token=first-value; Path=/"),
            ("set-cookie", "refresh_token=second-value; Path=/"),
        ],
        json={"accessToken": jwt("alice")},
    )
    current = session(tmp_path, Transport(response))
    before = (tmp_path / "session.json").read_bytes()
    with pytest.raises(ProtocolError, match="Conflicting"):
        current.get_access_token()
    assert (tmp_path / "session.json").read_bytes() == before
    assert current.refresh_count == 0


def test_repeated_identical_cookie_is_accepted(tmp_path):
    response = httpx.Response(
        200,
        headers=[
            ("set-cookie", "refresh_token=same-value; Path=/"),
            ("set-cookie", "refresh_token=same-value; Path=/api/v1/auth"),
        ],
        json={"accessToken": jwt("alice")},
    )
    current = session(tmp_path, Transport(response))
    current.get_access_token()
    assert stored(tmp_path / "session.json")["refresh_token"] == "same-value"


@pytest.mark.parametrize("value", ["has,comma", "x" * 8193], ids=["comma", "too-long"])
def test_cookie_value_that_cannot_be_sent_back_is_refused(tmp_path, value):
    response = httpx.Response(
        200,
        headers=[("set-cookie", f"refresh_token={value}; Path=/")],
        json={"accessToken": jwt("alice")},
    )
    current = session(tmp_path, Transport(response))
    before = (tmp_path / "session.json").read_bytes()
    with pytest.raises(ProtocolError, match="Invalid refresh cookie"):
        current.get_access_token()
    assert (tmp_path / "session.json").read_bytes() == before


def test_cookie_parser_failure_is_a_protocol_error(tmp_path, monkeypatch):
    class BrokenCookie:
        def load(self, header):
            raise CookieError("broken")

    monkeypatch.setattr(auth, "SimpleCookie", BrokenCookie)
    response = httpx.Response(
        200,
        headers=[("set-cookie", "refresh_token=anything")],
        json={"accessToken": jwt("alice")},
    )
    current = session(tmp_path, Transport(response))
    before = (tmp_path / "session.json").read_bytes()
    with pytest.raises(ProtocolError, match="Invalid refresh cookie"):
        current.get_access_token()
    assert (tmp_path / "session.json").read_bytes() == before


@pytest.mark.parametrize(
    "header",
    [
        "refresh_token=; Path=/",
        "refresh_token=; Path=/; Max-Age=0",
        "refresh_token=gone-value; Path=/; Max-Age=0",
    ],
    ids=["empty", "empty-expired", "expired"],
)
def test_cookie_deleted_by_the_server_is_a_protocol_error(tmp_path, header):
    response = httpx.Response(
        200, headers=[("set-cookie", header)], json={"accessToken": jwt("alice")}
    )
    transport = Transport(response)
    current = session(tmp_path, transport)
    before = (tmp_path / "session.json").read_bytes()
    with pytest.raises(ProtocolError, match="removed"):
        current.get_access_token()
    assert (tmp_path / "session.json").read_bytes() == before
    assert current.refresh_count == 0


def test_unrelated_cookies_do_not_rotate_the_session(tmp_path):
    response = httpx.Response(
        200,
        headers=[("set-cookie", "other_cookie=1; Path=/")],
        json={"accessToken": jwt("alice")},
    )
    current = session(tmp_path, Transport(response))
    current.get_access_token()
    assert stored(tmp_path / "session.json")["refresh_token"] == SEED
    assert current.refresh_count == 1


# --- pure helpers ------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "a b",
        "a;b",
        'a"b',
        "a,b",
        "a\\b",
        "tab\tvalue",
        "ünicode",
        "x" * 8193,
        None,
        5,
    ],
)
def test_validate_refresh_rejects_values_that_are_not_a_cookie_value(value):
    with pytest.raises(ValueError, match="single cookie value"):
        _validate_refresh(value)


@pytest.mark.parametrize("value", ["x", "a=b==", "x" * 8192, "A-z_0.9~+/"])
def test_validate_refresh_accepts_cookie_safe_values(value):
    assert _validate_refresh(value) == value


@pytest.mark.parametrize(
    "value", ["", " bad ", "new\nline"], ids=["empty", "spaces", "newline"]
)
def test_constructor_rejects_an_invalid_initial_token(tmp_path, value):
    with pytest.raises(ValueError):
        session(tmp_path, Transport(), seed=value)
    assert leftovers(tmp_path) == []


def with_payload(raw: bytes) -> str:
    payload = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"a.{payload}.c"


@pytest.mark.parametrize(
    "token",
    [
        "",
        "no-dots",
        "a.!!!.c",
        "a.é.c",
        with_payload(b"{"),
        with_payload(b"[]"),
        with_payload(b"null"),
        with_payload(b"123"),
        with_payload(b"\xff\xfe"),
        "a." + "A" * 16385 + ".c",
    ],
    ids=[
        "empty",
        "one-part",
        "bad-base64",
        "non-ascii",
        "truncated-json",
        "json-list",
        "json-null",
        "json-number",
        "not-utf8",
        "oversized",
    ],
)
def test_claims_of_garbage_is_an_empty_dict(token):
    assert _claims(token) == {}


def test_claims_reads_a_normal_payload():
    assert _claims(jwt("alice", 7)) == {"sub": "alice", "exp": 7}
