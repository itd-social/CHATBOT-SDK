import base64
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from itd_bot import APIError, Bot, ProtocolError, SessionStorageError


def jwt(subject, expiry):
    payload = (
        base64.urlsafe_b64encode(json.dumps({"sub": subject, "exp": expiry}).encode())
        .decode()
        .rstrip("=")
    )
    return f"header.{payload}.signature"


def test_refresh_rotation_and_restart(tmp_path):
    calls = []
    path = tmp_path / "session.json"

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/v1/auth/refresh":
            assert request.headers["origin"] == "https://example.test"
            assert request.headers["x-device-id"]
            assert request.content == b""
            expected = (
                "old"
                if len([x for x in calls if x.url.path.endswith("/refresh")]) == 1
                else "new"
            )
            assert request.headers["cookie"] == "refresh_token=" + expected
            return httpx.Response(
                200,
                headers=[
                    ("set-cookie", "refresh_token=; Path=/; Max-Age=0"),
                    ("set-cookie", "refresh_token=new; Path=/api/v1/auth; HttpOnly"),
                ],
                json={"accessToken": jwt("user", time.time() + 900)},
            )
        assert request.headers["authorization"].startswith("Bearer header.")
        return httpx.Response(200, json={"chats": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot.from_refresh_token(
            "old", session_path=path, base_url="https://example.test", client=client
        )
        bot.get_chats()
        assert json.loads(path.read_text())["refresh_token"] == "new"
        restarted = Bot.from_refresh_token(
            "old", session_path=path, base_url="https://example.test", client=client
        )
        restarted.get_chats()
        assert len([x for x in calls if x.url.path.endswith("/refresh")]) == 2


def test_refresh_on_401_and_no_unbounded_retry():
    sent = []

    def handler(req):
        sent.append((req.url.path, req.headers.get("authorization")))
        if req.url.path.endswith("/refresh"):
            return httpx.Response(200, json={"accessToken": "updated"})
        if req.headers["authorization"] == "Bearer old-access":
            return httpx.Response(401, json={"error": {"code": "UNAUTHORIZED"}})
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot(
            "old-access",
            refresh_token="refresh",
            session_path=None,
            base_url="https://example.test",
            client=client,
        )
        assert bot.send_message("chat", "hello") == {"ok": True}
    assert len(sent) == 3
    assert sent[1][0].endswith("/refresh")
    assert sent[-1][1] == "Bearer updated"


def test_parallel_requests_refresh_once():
    barrier = threading.Barrier(4)
    lock = threading.Lock()
    refreshes = []

    def handler(req):
        if req.url.path.endswith("/refresh"):
            with lock:
                refreshes.append(1)
            return httpx.Response(200, json={"accessToken": "new-access"})
        if req.headers["authorization"] == "Bearer old-access":
            barrier.wait(timeout=5)
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot(
            "old-access",
            refresh_token="seed",
            session_path=None,
            base_url="https://example.test",
            client=client,
        )
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: bot.get_chats(), range(4)))
    assert len(refreshes) == 1 and all(x["ok"] for x in results)


def test_concurrent_401_when_refresh_returns_same_access_token():
    barrier = threading.Barrier(3)
    counts = {}
    refreshes = []
    lock = threading.Lock()

    def handler(req):
        if req.url.path.endswith("/refresh"):
            with lock:
                refreshes.append(1)
            return httpx.Response(200, json={"accessToken": "same"})
        thread_id = threading.get_ident()
        with lock:
            counts[thread_id] = counts.get(thread_id, 0) + 1
            count = counts[thread_id]
        if count == 1:
            barrier.wait(timeout=5)
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot(
            "same",
            refresh_token="seed",
            session_path=None,
            base_url="https://example.test",
            client=client,
        )
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: bot.get_chats(), range(3)))
    assert len(refreshes) == 1 and all(x["ok"] for x in results)


def test_second_401_does_not_retry_and_multipart_not_replayed():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path.endswith("/refresh"):
            return httpx.Response(200, json={"accessToken": "new"})
        return httpx.Response(401, json={"error": {"code": "UNAUTHORIZED"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot(
            "old",
            refresh_token="seed",
            session_path=None,
            base_url="https://example.test",
            client=client,
        )
        with pytest.raises(APIError):
            bot.get_chats()
        assert len(calls) == 3
        calls.clear()
        with pytest.raises(APIError):
            bot.api_request(
                "POST", "/api/files/upload", files={"file": ("a.txt", b"x")}
            )
        assert calls == ["/api/files/upload"]


def test_server_removes_cookie_and_network_errors_do_not_erase_state(tmp_path):
    state = tmp_path / "session"

    def cleared(req):
        return httpx.Response(
            200,
            headers={"set-cookie": "refresh_token=; Max-Age=0"},
            json={"accessToken": "x"},
        )

    with httpx.Client(transport=httpx.MockTransport(cleared)) as client:
        bot = Bot.from_refresh_token(
            "seed", session_path=state, base_url="https://example.test", client=client
        )
        with pytest.raises(ProtocolError):
            bot.get_chats()
    assert json.loads(state.read_text())["refresh_token"] == "seed"

    def failure(req):
        raise httpx.ConnectError("offline", request=req)

    with httpx.Client(transport=httpx.MockTransport(failure)) as client:
        bot = Bot.from_refresh_token(
            "seed", session_path=state, base_url="https://example.test", client=client
        )
        with pytest.raises(httpx.ConnectError):
            bot.get_chats()
    assert json.loads(state.read_text())["refresh_token"] == "seed"


def test_proactive_expiry_and_session_errors(tmp_path):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path.endswith("/refresh"):
            return httpx.Response(
                200, json={"accessToken": jwt("same", time.time() + 900)}
            )
        return httpx.Response(200, json={"ok": True})

    path = tmp_path / "state.json"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot(
            jwt("same", time.time() - 1),
            refresh_token="seed",
            session_path=path,
            base_url="https://example.test",
            client=client,
        )
        bot.get_chat("c")
        assert calls[0].endswith("/refresh")
        with pytest.raises(SessionStorageError):
            Bot.from_refresh_token(
                "different",
                session_path=path,
                base_url="https://example.test",
                client=client,
            )
        with pytest.raises(SessionStorageError):
            Bot.from_refresh_token(
                "seed", session_path=path, base_url="https://other.test", client=client
            )


def test_refresh_failure_and_cookie_rotation_with_invalid_body(tmp_path):
    path = tmp_path / "state.json"

    def handler(req):
        return httpx.Response(
            200,
            headers={"set-cookie": "refresh_token=rotated; Path=/api/v1/auth"},
            json={"wrong": "value"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot.from_refresh_token(
            "seed", session_path=path, base_url="https://example.test", client=client
        )
        with pytest.raises(ProtocolError):
            bot.get_chats()
        assert json.loads(path.read_text())["refresh_token"] == "rotated"
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                401,
                json={"error": {"code": "SESSION_EXPIRED"}},
                headers={"set-cookie": "refresh_token=; Max-Age=0"},
            )
        )
    ) as client:
        bot = Bot.from_refresh_token(
            "seed", session_path=path, base_url="https://example.test", client=client
        )
        with pytest.raises(APIError) as exc:
            bot.get_chats()
        assert exc.value.code == "SESSION_EXPIRED"
        assert json.loads(path.read_text())["refresh_token"] == "rotated"


def test_different_subject_is_rejected():
    def handler(req):
        return httpx.Response(
            200, json={"accessToken": jwt("different", time.time() + 900)}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot(
            jwt("original", time.time() - 1),
            refresh_token="seed",
            session_path=None,
            base_url="https://example.test",
            client=client,
        )
        with pytest.raises(ProtocolError):
            bot.get_chats()


def test_account_binding_persists_across_restarts(tmp_path):
    state = tmp_path / "state"
    subjects = iter(["original", "different", "different"])

    def handler(req):
        if req.url.path.endswith("/refresh"):
            return httpx.Response(
                200, json={"accessToken": jwt(next(subjects), time.time() + 900)}
            )
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = Bot.from_refresh_token(
            "seed", session_path=state, base_url="https://example.test", client=client
        )
        assert first.get_chats() == {"ok": True}
        assert json.loads(state.read_text())["subject"] == "original"
        for _ in range(2):
            restarted = Bot.from_refresh_token(
                "seed",
                session_path=state,
                base_url="https://example.test",
                client=client,
            )
            with pytest.raises(ProtocolError):
                restarted.get_chats()
            with pytest.raises(ProtocolError):
                restarted.set_token(jwt("different", time.time() + 900))


def test_download_401_recreates_session_and_retries_once():
    file_id = "01234567-89ab-cdef-0123-456789abcdef"
    sessions = []
    requests = []

    def handler(req):
        requests.append(req.url.path)
        if req.url.path.endswith("/file-links"):
            return httpx.Response(
                200,
                json={
                    "links": [{"fileId": file_id, "url": f"/v1/chat-files/{file_id}"}]
                },
            )
        if req.url.path.endswith("/browser-file-session"):
            sessions.append(req.headers["authorization"])
            return httpx.Response(204)
        if req.url.path.endswith("/refresh"):
            return httpx.Response(200, json={"accessToken": "new"})
        if req.headers["authorization"] == "Bearer old":
            return httpx.Response(401)
        return httpx.Response(200, content=b"contents")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot(
            "old",
            refresh_token="seed",
            session_path=None,
            base_url="https://example.test",
            client=client,
        )
        assert bot.download_file("chat", file_id) == b"contents"
    assert sessions == ["Bearer old", "Bearer new"]
    assert len([x for x in requests if x.endswith("/refresh")]) == 1


def test_failed_rotation_save_blocks_further_requests(tmp_path, monkeypatch):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path.endswith("/refresh"):
            return httpx.Response(
                200,
                headers={"set-cookie": "refresh_token=rotated; Path=/api/v1/auth"},
                json={"accessToken": "new"},
            )
        return httpx.Response(401)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot(
            "old",
            refresh_token="seed",
            session_path=tmp_path / "state",
            base_url="https://example.test",
            client=client,
        )

        def fail_save():
            raise SessionStorageError("storage unavailable")

        monkeypatch.setattr(bot._refresh_session, "_save", fail_save)
        with pytest.raises(SessionStorageError):
            bot.get_chats()
        previous = len(calls)
        with pytest.raises(SessionStorageError):
            bot.get_chats()
        assert len(calls) == previous
