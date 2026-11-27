"""Opt-in measurements and the old/new slow-chat regression."""

import json
import threading

import httpx
import pytest

from benchmarks.measure import BASE, event, metadata, microbench, page, throughput
from itd_bot import Bot, RateLimitConfig, RateLimiter

pytestmark = pytest.mark.perf


def test_microbench():
    result = microbench(iterations=1000, rounds=5)
    assert all(item["median_us"] > 0 for item in result.values())
    print(json.dumps({"environment": metadata("pytest"), "microseconds": result}))


@pytest.mark.parametrize(
    "read_mode,reads", [("blocking", 120), ("batch", 48), ("off", 0)]
)
def test_fake_clock_throughput(read_mode, reads):
    result = throughput(read_mode)
    assert result["requests"] == {
        "updates": 12,
        **({"read": reads} if reads else {}),
        "send": 120,
    }
    print(json.dumps(result))


@pytest.mark.parametrize("queued", [False, True], ids=["batch-barrier", "chat-queues"])
def test_slow_chat_comparison(queued):
    entered, release, fast, stop = [threading.Event() for _ in range(4)]
    failures = []

    def answer(request):
        since = int(request.url.params["since"])
        if since == 0:
            return httpx.Response(200, json=page([event(1, chats=2)], 1))
        if since == 1:
            return httpx.Response(200, json=page([event(2, chats=2)], 2))
        return httpx.Response(200, json=page([], since))

    def handle(item):
        if item["event_seq"] == 1:
            entered.set()
            assert release.wait(3)
        else:
            fast.set()
            stop.set()

    with httpx.Client(transport=httpx.MockTransport(answer)) as client:
        bot = Bot(
            "fictional-token",
            base_url=BASE,
            client=client,
            limiter=RateLimiter(RateLimitConfig(enabled=False)),
        )

        def poll():
            try:
                options = {"chat_queues": True} if queued else {}
                bot.poll(0, handle, workers=2, interval=0.01, stop=stop, **options)
            except BaseException as exc:
                failures.append(exc)

        thread = threading.Thread(target=poll, daemon=True)
        thread.start()
        try:
            assert entered.wait(2)
            if queued:
                assert fast.wait(2)
            else:
                assert not fast.wait(0.1)
        finally:
            release.set()
            thread.join(2)
            stop.set()
            thread.join(2)
        assert not thread.is_alive() and not failures and fast.is_set()
