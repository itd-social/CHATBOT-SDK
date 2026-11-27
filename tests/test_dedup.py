"""Deduplicate only completed messages; failed or in-flight work stays safe."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from helpers import BASE, Transport

from itd_bot import Bot, Dispatcher, MemoryDedupStore


def event(message_id="m-1", chat_id="chat-1", **changes):
    message = {"message_id": message_id, "sender_id": "other", "text": "hello"}
    message.update(changes)
    return {"type": "new_message", "chat_id": chat_id, "message": message}


def router(bot, **kwargs):
    kwargs.setdefault("read_mode", "off")
    kwargs.setdefault("auto_typing", False)
    kwargs.setdefault("self_user_id", "self")
    return Dispatcher(bot, **kwargs)


@pytest.fixture
def bot():
    transport = Transport(default=httpx.Response(204))
    with transport.client() as client:
        yield Bot("fictional-token", base_url=BASE, client=client)


@pytest.mark.parametrize("deduplicate", [True, False])
def test_duplicate_success_is_skipped_by_default_and_can_be_disabled(bot, deduplicate):
    current = router(bot, deduplicate=deduplicate)
    seen = []
    current.message()(lambda ctx: seen.append(ctx.message_id))
    current.handle(event())
    current.handle(event())
    assert seen == (["m-1"] if deduplicate else ["m-1", "m-1"])


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_failed_handler_releases_claim_and_can_be_retried(bot, failure):
    current = router(bot)
    seen = []

    @current.message()
    def handle(ctx):
        seen.append(ctx.message_id)
        if len(seen) == 1:
            raise failure("fictional failure")

    with pytest.raises(failure):
        current.handle(event())
    current.handle(event())
    current.handle(event())
    assert seen == ["m-1", "m-1"]


@pytest.mark.parametrize("deduplicate", [True, False])
def test_batch_redelivery_after_read_failure_keeps_completed_messages_once(
    monkeypatch, deduplicate
):
    stop = threading.Event()
    cursors, marks, seen, checkpoints = [], [], [], []
    failed = False
    messages = [event("m-1")["message"], event("m-2")["message"]]

    def respond(request):
        nonlocal failed
        if request.url.path.endswith("/updates"):
            cursors.append(int(request.url.params["since"]))
            return httpx.Response(
                200,
                json={
                    "events": [
                        {
                            "type": "new_messages",
                            "chat_id": "chat-1",
                            "messages": messages,
                        }
                    ],
                    "next_event_seq": 2,
                    "reset_required": False,
                },
            )
        message_id = json.loads(request.content)["last_read_message"]
        marks.append(message_id)
        if message_id == "m-2" and not failed:
            failed = True
            raise httpx.ReadTimeout("fictional read failure")
        return httpx.Response(204)

    monkeypatch.setattr("itd_bot.client.pause", lambda stop, delay: False)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        bot = Bot("fictional-token", base_url=BASE, client=client, get_retries=0)
        current = router(bot, read_mode="blocking", deduplicate=deduplicate)

        @current.message()
        def handle(ctx):
            seen.append(ctx.message_id)
            if ctx.message_id == "m-2":
                stop.set()

        bot.poll(0, current.handle, stop=stop, on_checkpoint=checkpoints.append)
    assert cursors == [0, 0] and checkpoints == [2]
    assert seen == (["m-1", "m-2"] if deduplicate else ["m-1", "m-1", "m-2"])
    assert marks == (
        ["m-1", "m-2", "m-2"] if deduplicate else ["m-1", "m-2", "m-1", "m-2"]
    )


def test_lru_capacity_evicts_the_least_recently_seen_success(bot):
    current = router(bot, dedup_capacity=2)
    seen = []
    current.message()(lambda ctx: seen.append(ctx.message_id))
    for message_id in ("m-1", "m-2", "m-1", "m-3", "m-2"):
        current.handle(event(message_id))
    assert seen == ["m-1", "m-2", "m-3", "m-2"]


def test_shared_store_is_scoped_by_server_account_and_chat(bot):
    store = MemoryDedupStore()
    seen = []
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(204))
    ) as client:
        other_server = Bot(
            "fictional-token", base_url="https://other.test", client=client
        )
        same = router(bot, dedup_store=store)
        duplicate = router(bot, dedup_store=store)
        account = router(bot, self_user_id="another-account", dedup_store=store)
        server = router(other_server, dedup_store=store)
        for name, current in (
            ("same", same),
            ("duplicate", duplicate),
            ("account", account),
            ("server", server),
        ):
            current.message()(lambda ctx, label=name: seen.append(label))
        same.handle(event())
        duplicate.handle(event())
        account.handle(event())
        server.handle(event())
        same.handle(event(chat_id="chat-2"))
    assert seen == ["same", "account", "server", "same"]


def test_same_inflight_message_waits_while_another_chat_can_run(bot):
    current = router(bot)
    started, release, duplicate_started, other_done = (
        threading.Event() for _ in range(4)
    )
    seen = []
    lock = threading.Lock()

    @current.message()
    def handle(ctx):
        if ctx.chat_id == "chat-1":
            started.set()
            assert release.wait(5)
        with lock:
            seen.append(ctx.chat_id)
        if ctx.chat_id == "chat-2":
            other_done.set()

    def duplicate():
        duplicate_started.set()
        current.handle(event())

    with ThreadPoolExecutor(3) as pool:
        first = pool.submit(current.handle, event())
        try:
            assert started.wait(2)
            second = pool.submit(duplicate)
            assert duplicate_started.wait(2)
            other = pool.submit(current.handle, event(chat_id="chat-2"))
            assert other_done.wait(2)
            assert not second.done()
        finally:
            release.set()
        first.result(timeout=2)
        second.result(timeout=2)
        other.result(timeout=2)
    assert seen == ["chat-2", "chat-1"]


def test_waiting_duplicate_can_claim_after_first_handler_fails(bot):
    current = router(bot)
    started, release, duplicate_started = (threading.Event() for _ in range(3))
    seen = []

    @current.message()
    def handle(ctx):
        seen.append(ctx.message_id)
        if len(seen) == 1:
            started.set()
            assert release.wait(5)
            raise RuntimeError("fictional failure")

    def duplicate():
        duplicate_started.set()
        current.handle(event())

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(current.handle, event())
        try:
            assert started.wait(2)
            second = pool.submit(duplicate)
            assert duplicate_started.wait(2)
        finally:
            release.set()
        with pytest.raises(RuntimeError):
            first.result(timeout=2)
        second.result(timeout=2)
    assert seen == ["m-1", "m-1"]


def test_reentrant_duplicate_does_not_deadlock(bot):
    current = router(bot)
    seen = []

    @current.message()
    def handle(ctx):
        seen.append(ctx.message_id)
        current.handle(ctx.event)

    current.handle(event())
    assert seen == ["m-1"]


def test_non_message_events_are_not_deduplicated(bot):
    current = router(bot)
    seen = []
    current.on_event("message_edited")(lambda raw: seen.append(raw["message_id"]))
    raw = {"type": "message_edited", "chat_id": "chat-1", "message_id": "m-1"}
    current.handle(raw)
    current.handle(raw)
    assert seen == ["m-1", "m-1"]


def test_external_store_failure_is_propagated_and_claim_is_released(bot):
    class Store:
        def __init__(self):
            self.inner = MemoryDedupStore()
            self.failed = False
            self.releases = []

        def __bool__(self):
            return False

        def claim(self, key):
            return self.inner.claim(key)

        def complete(self, key):
            if not self.failed:
                self.failed = True
                raise OSError("fictional storage failure")
            self.inner.complete(key)

        def release(self, key):
            self.releases.append(key)
            self.inner.release(key)

    store = Store()
    current = router(bot, dedup_store=store)
    seen = []
    current.message()(lambda ctx: seen.append(ctx.message_id))
    with pytest.raises(OSError):
        current.handle(event())
    current.handle(event())
    current.handle(event())
    assert seen == ["m-1", "m-1"]
    assert store.releases == [(BASE, "self", "chat-1", "m-1")]


@pytest.mark.parametrize("capacity", [0, -1, True, 1.5, "4"])
def test_invalid_capacity_is_rejected(bot, capacity):
    with pytest.raises(ValueError, match="positive integer"):
        MemoryDedupStore(capacity)
    with pytest.raises(ValueError, match="positive integer"):
        router(bot, dedup_capacity=capacity)


@pytest.mark.parametrize("deduplicate", [None, "false", 0])
def test_invalid_switch_is_rejected(bot, deduplicate):
    with pytest.raises(ValueError, match="boolean"):
        router(bot, deduplicate=deduplicate)


def test_invalid_external_store_is_rejected(bot):
    with pytest.raises(TypeError, match="claim, complete and release"):
        router(bot, dedup_store=object())
