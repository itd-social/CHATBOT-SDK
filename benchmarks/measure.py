"""Reproducible MockTransport measurements, with no server or real credentials."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import platform
import statistics
import sys
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

BASE = "https://example.test"


def event(number: int, chats: int = 4) -> dict[str, Any]:
    return {
        "type": "new_message",
        "event_seq": number,
        "chat_id": f"chat-{number % chats}",
        "message": {"message_id": str(number), "sender_id": "other", "text": "hello"},
    }


def page(events: list[dict[str, Any]], cursor: int) -> dict[str, Any]:
    return {"events": events, "next_event_seq": cursor, "reset_required": False}


def timing(action: Callable[[], Any], iterations: int, rounds: int) -> dict[str, Any]:
    for _ in range(min(100, iterations)):
        action()
    samples = []
    for _ in range(rounds):
        start = time.perf_counter_ns()
        for _ in range(iterations):
            action()
        samples.append((time.perf_counter_ns() - start) / iterations / 1000)
    return {
        "median_us": round(statistics.median(samples), 3),
        "min_us": round(min(samples), 3),
        "max_us": round(max(samples), 3),
        "iterations": iterations,
        "rounds": rounds,
    }


def microbench(iterations: int = 2000, rounds: int = 5) -> dict[str, Any]:
    from itd_bot import Bot, Dispatcher, RateLimitConfig, RateLimiter

    payload = page([event(number) for number in range(1, 101)], 100)
    encoded = json.dumps(payload, separators=(",", ":")).encode()

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/updates"):
            return httpx.Response(
                200, content=encoded, headers={"Content-Type": "application/json"}
            )
        return httpx.Response(200, json={"message_id": "reply"})

    with httpx.Client(transport=httpx.MockTransport(answer)) as client:
        with Bot(
            "fictional-token",
            client=client,
            base_url=BASE,
            limiter=RateLimiter(RateLimitConfig(enabled=False)),
        ) as bot:
            router = Dispatcher(
                bot, self_user_id="self", auto_typing=False, read_mode="off"
            )
            router.message()(lambda ctx: None)
            number = 0

            def dispatch() -> None:
                nonlocal number
                number += 1
                router.handle(event(number))

            result = {
                "send_message": timing(
                    lambda: bot.send_message("chat", "hello"), iterations, rounds
                ),
                "dispatcher_unique_message": timing(dispatch, iterations, rounds),
                "json_decode_100_events": timing(
                    lambda: json.loads(encoded), iterations, rounds
                ),
                "get_updates_100_events": timing(
                    lambda: bot.get_updates(0), iterations, rounds
                ),
            }
            router.close()
    return result


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def read(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def throughput(
    read_mode: str, messages: int = 120, batch_size: int = 10, chats: int = 4
) -> dict[str, Any]:
    import threading

    from itd_bot import Bot, Dispatcher, RateLimitConfig, RateLimiter

    if read_mode not in {"blocking", "off"}:
        raise ValueError(
            "This benchmark requires blocking or off for deterministic time"
        )
    clock, stop = Clock(), threading.Event()
    counts: Counter[str] = Counter()
    completed: list[float] = []
    handled = 0

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/updates"):
            counts["updates"] += 1
            since = int(request.url.params["since"])
            end = min(messages, since + batch_size)
            return httpx.Response(
                200,
                json=page([event(n, chats) for n in range(since + 1, end + 1)], end),
            )
        if request.url.path.endswith("/read"):
            counts["read"] += 1
            return httpx.Response(200, json={})
        if request.url.path.endswith("/messages"):
            counts["send"] += 1
            completed.append(clock.now)
            return httpx.Response(201, json={"message_id": "reply"})
        raise AssertionError("Unexpected benchmark route")

    limiter = RateLimiter(
        RateLimitConfig(), monotonic=clock.read, wall_time=clock.read, sleep=clock.sleep
    )
    with httpx.Client(transport=httpx.MockTransport(answer)) as client:
        with Bot(
            "fictional-token", client=client, base_url=BASE, limiter=limiter
        ) as bot:
            router = Dispatcher(
                bot, self_user_id="self", auto_typing=False, read_mode=read_mode
            )

            @router.message()
            def reply(ctx: Any) -> None:
                nonlocal handled
                ctx.reply("reply")
                handled += 1
                if handled == messages:
                    stop.set()

            router.run_polling(0, handle_signals=False, stop=stop)
    assert handled == messages and counts["updates"] == math.ceil(messages / batch_size)
    return {
        "read_mode": read_mode,
        "messages": messages,
        "batch_size": batch_size,
        "chats": chats,
        "workers": 1,
        "auto_typing": False,
        "virtual_seconds": round(clock.now, 6),
        "finite_run_replies_per_minute": round(messages * 60 / clock.now, 3)
        if clock.now
        else None,
        "replies_before_60s": sum(timestamp < 60 for timestamp in completed),
        "requests": dict(counts),
        "total_requests": sum(counts.values()),
    }


def metadata(label: str) -> dict[str, Any]:
    from itd_bot import Bot

    source = inspect.getsourcefile(Bot)
    assert source is not None
    return {
        "label": label,
        "python": platform.python_version(),
        "platform": sys.platform,
        "httpx": httpx.__version__,
        "client_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="working-tree")
    parser.add_argument(
        "--sdk-path",
        type=Path,
        help="Directory containing a historical itd_bot package",
    )
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument(
        "--read-modes",
        nargs="+",
        choices=["blocking", "off"],
        default=["blocking", "off"],
    )
    args = parser.parse_args()
    if args.iterations < 1 or args.rounds < 1:
        parser.error("iterations and rounds must be positive")
    if args.sdk_path is not None:
        if not (args.sdk_path / "itd_bot" / "__init__.py").is_file():
            parser.error("sdk-path must contain itd_bot/__init__.py")
        sys.path.insert(0, str(args.sdk_path.resolve()))
    print(
        json.dumps(
            {
                "environment": metadata(args.label),
                "microseconds": microbench(args.iterations, args.rounds),
                "throughput": [throughput(mode) for mode in args.read_modes],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
