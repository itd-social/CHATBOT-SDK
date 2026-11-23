import base64
import json
import math
import queue
import threading
import time

import httpx
import pytest

from itd_bot import APIError, Bot, ChatAction, RateLimitError, RateLimiter
from itd_bot.rate_limits import RateLimitConfig

UPLOADS = [a for a in ChatAction if a.value.startswith("upload_")]
OTHERS = [a for a in ChatAction if not a.value.startswith("upload_")]


def jwt(sub):
    payload = base64.urlsafe_b64encode(json.dumps({"sub": sub}).encode())
    return "header." + payload.decode().rstrip("=") + ".signature"


class Recorder:
    """MockTransport handler that records /action bodies; fail_after makes 429."""

    def __init__(self, fail_after=None, status=429, headers=None):
        self.bodies = queue.Queue()
        self.all = []
        self.fail_after = fail_after
        self.status = status
        self.headers = headers or {"Retry-After": "30"}

    def __call__(self, request):
        assert request.url.path.endswith("/action")
        body = json.loads(request.content)
        self.all.append(body)
        self.bodies.put(body)
        if self.fail_after is not None and len(self.all) > self.fail_after:
            return httpx.Response(self.status, headers=self.headers)
        return httpx.Response(204)

    def next(self):
        return self.bodies.get(timeout=2)


class Ticker:
    """Deterministic replacement for Event.wait: one release per background tick."""

    def __init__(self):
        self.timeouts = []
        self._ticks = threading.Semaphore(0)

    def wait(self, stop, timeout):
        self.timeouts.append(timeout)
        while not stop.is_set():
            if self._ticks.acquire(timeout=0.005):
                return False
        return True

    def tick(self):
        self._ticks.release()


@pytest.fixture
def make_bot():
    clients = []

    def make(handler, token="token", **kwargs):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        clients.append(client)
        return Bot(token, base_url="https://example.test", client=client, **kwargs)

    yield make
    for client in clients:
        client.close()


def chat_action_threads():
    return [t for t in threading.enumerate() if t.name == "itd-chat-action"]


def test_chat_action_values_and_python_310_compatible_enum():
    assert [a.value for a in ChatAction] == [
        "typing",
        "record_voice",
        "record_video",
        "upload_photo",
        "upload_video",
        "upload_voice",
        "upload_document",
    ]
    assert isinstance(ChatAction.TYPING, str)
    assert ChatAction.TYPING == "typing" and ChatAction("typing") is ChatAction.TYPING
    assert "StrEnum" not in [c.__name__ for c in ChatAction.__mro__]
    assert "RECORD_VIDEO" in (ChatAction.__doc__ or "")


@pytest.mark.parametrize("action", list(ChatAction))
def test_every_chat_action_and_its_string_send_the_same_body(make_bot, action):
    rec = Recorder()
    bot = make_bot(rec)
    bot.send_action("c", action)
    bot.send_action("c", action.value)
    assert rec.all == [{"type": action.value}] * 2
    assert type(rec.all[0]["type"]) is str


def test_default_action_is_typing_and_arbitrary_strings_pass(make_bot):
    rec = Recorder()
    bot = make_bot(rec)
    bot.send_action("c")
    bot.send_action("c", "something_new")
    bot.send_action("c", "upload_xxx", progress=0.25)
    assert rec.all == [
        {"type": "typing"},
        {"type": "something_new"},
        {"type": "upload_xxx", "progress": 0.25},
    ]


@pytest.mark.parametrize("action", ["", None, 5, b"typing", ["typing"]])
def test_empty_or_non_string_action_is_rejected_before_request(make_bot, action):
    rec = Recorder()
    bot = make_bot(rec)
    with pytest.raises(ValueError):
        bot.send_action("c", action)
    assert rec.all == []


def test_existing_chat_id_validation_is_kept(make_bot):
    rec = Recorder()
    bot = make_bot(rec)
    for chat in ("", "..", None):
        with pytest.raises(ValueError):
            bot.send_action(chat, ChatAction.TYPING)
    assert rec.all == []


@pytest.mark.parametrize("progress", [0, 1, 0.99, 0.5, 0.0, 1.0])
@pytest.mark.parametrize("action", [*UPLOADS, "upload_xxx"])
def test_valid_progress_is_sent_for_upload_actions(make_bot, action, progress):
    rec = Recorder()
    make_bot(rec).send_action("c", action, progress=progress)
    name = action.value if isinstance(action, ChatAction) else action
    assert rec.all == [{"type": name, "progress": progress}]


@pytest.mark.parametrize(
    "progress",
    [-0.1, 1.2, math.nan, math.inf, -math.inf, True, False, "0.5", [0.5], 2, -1],
)
def test_invalid_progress_is_rejected_before_request(make_bot, progress):
    rec = Recorder()
    bot = make_bot(rec)
    with pytest.raises(ValueError):
        bot.send_action("c", ChatAction.UPLOAD_VIDEO, progress=progress)
    assert rec.all == []


@pytest.mark.parametrize("action", [*OTHERS, "custom"])
def test_progress_for_non_upload_actions_is_rejected(make_bot, action):
    rec = Recorder()
    bot = make_bot(rec)
    for progress in (0, 0.5):
        with pytest.raises(ValueError):
            bot.send_action("c", action, progress=progress)
    assert rec.all == []


def test_chat_action_sends_first_request_immediately_and_repeats_on_interval(make_bot):
    rec, ticker = Recorder(), Ticker()
    bot = make_bot(rec)
    handle = bot.chat_action("c", ChatAction.UPLOAD_VIDEO, interval=2.5, progress=0.1)
    handle._wait = ticker.wait
    with handle as same:
        assert same is handle
        assert rec.next() == {"type": "upload_video", "progress": 0.1}
        ticker.tick()
        assert rec.next() == {"type": "upload_video", "progress": 0.1}
        ticker.tick()
        assert rec.next() == {"type": "upload_video", "progress": 0.1}
    assert ticker.timeouts[:2] == [2.5, 2.5]
    assert handle.error is None
    assert chat_action_threads() == []


def test_default_action_is_typing_with_three_second_interval(make_bot):
    rec, ticker = Recorder(), Ticker()
    handle = make_bot(rec).chat_action("c")
    handle._wait = ticker.wait
    with handle:
        assert rec.next() == {"type": "typing"}
        ticker.tick()
        assert rec.next() == {"type": "typing"}
    assert ticker.timeouts[0] == 3.0


def test_progress_is_read_on_every_tick_and_can_change_from_another_thread(make_bot):
    rec, ticker = Recorder(), Ticker()
    handle = make_bot(rec).chat_action("c", ChatAction.UPLOAD_VIDEO)
    handle._wait = ticker.wait
    with handle:
        assert rec.next() == {"type": "upload_video"}
        for value in (0.99, 0.98, 0.97):
            worker = threading.Thread(target=setattr, args=(handle, "progress", value))
            worker.start()
            worker.join()
            assert handle.progress == value
            ticker.tick()
            assert rec.next() == {"type": "upload_video", "progress": value}
        handle.progress = None
        ticker.tick()
        assert rec.next() == {"type": "upload_video"}


def test_progress_setter_uses_the_same_checks(make_bot):
    handle = make_bot(Recorder()).chat_action("c", ChatAction.UPLOAD_PHOTO)
    for bad in (-0.1, 1.2, math.nan, math.inf, True, "0.5"):
        with pytest.raises(ValueError):
            handle.progress = bad
    assert handle.progress is None
    handle.progress = 1
    assert handle.progress == 1
    typing = make_bot(Recorder()).chat_action("c", ChatAction.TYPING)
    with pytest.raises(ValueError):
        typing.progress = 0.5
    typing.progress = None
    with pytest.raises(ValueError):
        make_bot(Recorder()).chat_action("c", ChatAction.TYPING, progress=0.5)


def test_callable_progress_is_called_on_every_tick(make_bot):
    rec, ticker = Recorder(), Ticker()
    values = iter([0.0, 0.5, None, 1.0])
    calls = []

    def source():
        value = next(values)
        calls.append(value)
        return value

    handle = make_bot(rec).chat_action("c", "upload_voice", progress=source)
    handle._wait = ticker.wait
    with handle:
        assert rec.next() == {"type": "upload_voice", "progress": 0.0}
        assert handle.progress == 0.0
        ticker.tick()
        assert rec.next() == {"type": "upload_voice", "progress": 0.5}
        ticker.tick()
        assert rec.next() == {"type": "upload_voice"}
        ticker.tick()
        assert rec.next() == {"type": "upload_voice", "progress": 1.0}
    assert calls == [0.0, 0.5, None, 1.0]


@pytest.mark.parametrize("bad", [1.5, -1, math.nan, True, "x"])
def test_invalid_callable_progress_stops_loop_without_sending_garbage(make_bot, bad):
    rec, ticker = Recorder(), Ticker()
    values = iter([0.2, bad])
    handle = make_bot(rec).chat_action(
        "c", ChatAction.UPLOAD_PHOTO, progress=lambda: next(values)
    )
    handle._wait = ticker.wait
    with handle:
        assert rec.next() == {"type": "upload_photo", "progress": 0.2}
        ticker.tick()
        deadline = time.monotonic() + 2
        while handle.error is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert isinstance(handle.error, ValueError)
        ticker.tick()
        time.sleep(0.05)
    assert rec.all == [{"type": "upload_photo", "progress": 0.2}]
    assert chat_action_threads() == []


def test_invalid_callable_progress_on_first_send_raises_and_starts_no_thread(make_bot):
    rec = Recorder()
    handle = make_bot(rec).chat_action("c", ChatAction.UPLOAD_PHOTO, progress=lambda: 7)
    with pytest.raises(ValueError):
        handle.__enter__()
    assert rec.all == [] and handle._thread is None
    assert chat_action_threads() == []


def test_exit_stops_without_waiting_for_the_interval(make_bot):
    rec = Recorder()
    started = time.monotonic()
    with make_bot(rec).chat_action("c", interval=3600) as handle:
        assert rec.next() == {"type": "typing"}
        thread = handle._thread
        assert thread.is_alive() and thread.daemon
    assert time.monotonic() - started < 2
    assert not thread.is_alive()
    assert len(rec.all) == 1


def test_rate_limit_in_background_stops_loop_and_is_kept_in_error(make_bot):
    rec, ticker = Recorder(fail_after=1), Ticker()
    handle = make_bot(rec).chat_action("c")
    handle._wait = ticker.wait
    with handle:
        rec.next()
        ticker.tick()
        rec.next()
        deadline = time.monotonic() + 2
        while handle.error is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert isinstance(handle.error, RateLimitError)
        assert not handle._thread.is_alive()
        ticker.tick()
        time.sleep(0.05)
    assert len(rec.all) == 2
    assert isinstance(handle.error, RateLimitError)


@pytest.mark.parametrize(
    "failure",
    [
        lambda request: httpx.Response(401, json={"error": "expired"}),
        lambda request: (_ for _ in ()).throw(
            httpx.ConnectError("down", request=request)
        ),
    ],
)
def test_other_background_errors_stop_the_loop(make_bot, failure):
    ticker, calls = Ticker(), []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(204)
        return failure(request)

    handle = make_bot(handler).chat_action("c")
    handle._wait = ticker.wait
    with handle:
        ticker.tick()
        deadline = time.monotonic() + 2
        while handle.error is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert handle.error is not None
        ticker.tick()
        time.sleep(0.05)
    assert len(calls) == 2


def test_body_exception_is_not_replaced_even_after_background_error(make_bot):
    rec, ticker = Recorder(fail_after=1), Ticker()
    handle = make_bot(rec).chat_action("c")
    handle._wait = ticker.wait

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with handle:
            rec.next()
            ticker.tick()
            rec.next()
            deadline = time.monotonic() + 2
            while handle.error is None and time.monotonic() < deadline:
                time.sleep(0.005)
            raise Boom("from the body")
    assert isinstance(handle.error, RateLimitError)
    assert chat_action_threads() == []


def test_body_exception_stops_the_thread(make_bot):
    rec = Recorder()
    with pytest.raises(KeyError):
        with make_bot(rec).chat_action("c", interval=3600) as handle:
            raise KeyError("body")
    assert not handle._thread.is_alive()


def test_401_in_enter_raises_and_no_thread_is_started(make_bot):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"error": "bad token"})

    handle = make_bot(handler).chat_action("c")
    with pytest.raises(APIError):
        with handle:
            raise AssertionError("body must not run")
    assert len(calls) == 1 and handle._thread is None
    assert chat_action_threads() == []


@pytest.mark.parametrize("interval", [0.99, 0.5, 0, -1, math.nan, True])
def test_interval_below_one_second_is_rejected(make_bot, interval):
    rec = Recorder()
    with pytest.raises(ValueError):
        make_bot(rec).chat_action("c", interval=interval)
    assert rec.all == []


def test_interval_of_exactly_one_second_is_allowed(make_bot):
    handle = make_bot(Recorder()).chat_action("c", interval=1.0)
    assert handle is not None


def test_handle_cannot_be_reused(make_bot):
    handle = make_bot(Recorder()).chat_action("c", interval=3600)
    with handle:
        pass
    with pytest.raises(RuntimeError):
        handle.__enter__()


def test_chat_action_goes_through_the_shared_limiter_with_the_account(make_bot):
    seen = []

    class Recording(RateLimiter):
        def acquire(self, method, path, *, cost=1, account=None):
            seen.append((method, path, account))
            super().acquire(method, path, cost=cost, account=account)

    gate = Recording(RateLimitConfig(enabled=True, wait=False, min_interval=0))
    rec = Recorder()
    bot = make_bot(rec, token=jwt("user-1"), limiter=gate)
    with bot.chat_action("chat-1", interval=3600):
        rec.next()
    assert [(m, p) for m, p, _ in seen] == [
        ("POST", "/api/v1/messenger/chats/chat-1/action")
    ]
    assert seen[0][2] and seen[0][2].startswith("sub:")


def test_action_route_is_limited_only_by_the_shared_request_budget():
    gate = RateLimiter(RateLimitConfig(enabled=True, wait=False, min_interval=0))
    path = "/api/v1/messenger/chats/c/action"
    assert gate._group("POST", path) is None
    for _ in range(80):
        gate.acquire("POST", path, account="one")
    with pytest.raises(RateLimitError) as blocked:
        gate.acquire("POST", path, account="one")
    assert blocked.value.locally_blocked


def test_account_key_cache_decodes_each_token_once(monkeypatch):
    import itd_bot.client as client_module

    calls = []
    real = client_module._claims

    def counting(token):
        calls.append(token)
        return real(token)

    monkeypatch.setattr(client_module, "_claims", counting)
    first, second = jwt("user-1"), jwt("user-2")
    bot = Bot(first, client=httpx.Client(transport=httpx.MockTransport(lambda r: None)))
    key = bot._account_key(first)
    assert [bot._account_key(first) for _ in range(5)] == [key] * 5
    assert calls == [first]
    other = bot._account_key(second)
    assert other != key and calls == [first, second]
    assert bot._account_key(second) == other and len(calls) == 2
    assert bot._account_key(first) == key and len(calls) == 3


def test_account_key_cache_keeps_the_random_key_for_tokens_without_subject():
    bot = Bot(
        "opaque", client=httpx.Client(transport=httpx.MockTransport(lambda r: None))
    )
    key = bot._account_key("opaque")
    assert bot._account_key("opaque") == key
    assert bot._account_key("another-opaque") == key
    assert key.startswith("bot:") and "opaque" not in key
