"""Офлайн-демонстрация корзин токенов: сеть и токен не нужны.

Время и ожидание подменены, поэтому «паузы» считаются мгновенно.
"""

import httpx

from itd_bot import RateLimiter, RateLimitError

ROOT = "/api/v1/messenger/chats"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def read(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def run() -> list[tuple[str, float]]:
    """Вернуть пары (шаг, ожидание в секундах) для типичных сценариев."""
    clock = FakeClock()
    limiter = RateLimiter(monotonic=clock.read, sleep=clock.sleep)
    steps: list[tuple[str, float]] = []

    def acquire(label: str, method: str, path: str) -> None:
        before = clock.now
        limiter.acquire(method, path)
        steps.append((label, round(clock.now - before, 3)))

    for number in range(1, 6):
        acquire(f"отправка {number} в чат A", "POST", f"{ROOT}/A/messages")
    acquire("правка в чате A", "PATCH", f"{ROOT}/A/messages/m1")
    acquire("правка в чате B", "PATCH", f"{ROOT}/B/messages/m1")

    answer = httpx.Response(429, headers={"Retry-After": "5"})
    try:
        limiter.observe(answer, "POST", f"{ROOT}/A/messages")
    except RateLimitError as error:
        steps.append(("429 в чате A, Retry-After", error.retry_after_seconds))
    acquire("отправка в чат B после 429 в A", "POST", f"{ROOT}/B/messages")
    acquire("отправка в чат A после 429", "POST", f"{ROOT}/A/messages")
    return steps


def main() -> None:
    for label, seconds in run():
        print(f"{label}: {seconds:g} с")


if __name__ == "__main__":
    main()
