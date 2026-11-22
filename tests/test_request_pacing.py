from concurrent.futures import ThreadPoolExecutor
import threading

import httpx
import pytest

from itd_bot import Bot, RateLimitConfig, RateLimiter, RateLimitError, UploadQuotaError
from itd_bot.rate_limits import default_limiter


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
        self.lock = threading.Lock()

    def read(self):
        with self.lock:
            return self.now

    def sleep(self, seconds):
        with self.lock:
            self.sleeps.append(seconds)
            self.now += seconds


FIXED_INTERVALS = {
    "send_burst": 1,
    "edit_burst": 1,
    "reaction_burst": 1,
    "delete_for_me_burst": 1,
}


def limiter(clock, **options):
    return RateLimiter(
        RateLimitConfig(**options), monotonic=clock.read, sleep=clock.sleep
    )


def test_default_settings_and_origin_sharing():
    config = RateLimitConfig()
    assert config.requests_per_minute == 80
    assert (
        config.send_burst,
        config.edit_burst,
        config.reaction_burst,
        config.delete_for_me_burst,
    ) == (4, 4, 25, 8)
    assert default_limiter("https://one.test") is default_limiter("https://one.test")
    assert default_limiter("https://one.test") is not default_limiter(
        "https://two.test"
    )


def test_rolling_window_counts_errors_and_all_routes():
    clock = Clock()
    gate = limiter(clock, requests_per_minute=2, min_interval=0)
    gate.acquire("GET", "/api/users/me")
    gate.acquire("POST", "/api/v1/auth/refresh")
    gate.acquire("GET", "/v1/chat-files/file")
    assert clock.now == 60
    gate.acquire("GET", "/api/unknown")
    assert clock.now == 60
    gate.acquire("GET", "/api/unknown")
    assert clock.now == 120


@pytest.mark.parametrize(
    "paths,method,interval",
    [
        (
            ["chats/a/messages", "users/b/messages", "chats/c/messages/forward"],
            "POST",
            2.1,
        ),
        (["chats/a/messages/x", "chats/b/messages/y"], "PATCH", 6.2),
        (["chats/a/messages/x/reactions", "chats/b/messages/y/reactions"], "PUT", 1.1),
    ],
)
def test_groups_share_capacity_across_paths(paths, method, interval):
    clock = Clock()
    gate = limiter(clock, min_interval=0, **FIXED_INTERVALS)
    for path in paths:
        gate.acquire(method, "/api/v1/messenger/" + path)
    assert clock.now == pytest.approx((len(paths) - 1) * interval)


def test_groups_are_independent_and_reads_do_not_consume_send_capacity():
    clock = Clock()
    gate = limiter(clock, min_interval=0)
    for method, path in [
        ("POST", "chats/a/messages"),
        ("PATCH", "chats/a/messages/x"),
        ("PUT", "chats/a/messages/x/reactions"),
        ("GET", "chats/a/messages"),
    ]:
        gate.acquire(method, "/api/v1/messenger/" + path)
    assert clock.now == 0


def test_cooldown_crosses_methods_and_ids_but_not_groups():
    clock = Clock()
    gate = limiter(clock, min_interval=0)
    with pytest.raises(RateLimitError):
        gate.observe(
            httpx.Response(429, headers={"Retry-After": "8"}),
            "POST",
            "/api/v1/messenger/chats/a/messages",
        )
    gate.acquire("GET", "/api/v1/messenger/chats/a/messages")
    assert clock.now == 0
    gate.acquire("POST", "/api/v1/messenger/users/b/messages")
    assert clock.now == 8


def test_global_cooldown_and_milliseconds_take_longer_delay():
    clock = Clock()
    gate = limiter(clock)
    with pytest.raises(RateLimitError) as error:
        gate.observe(
            httpx.Response(
                429,
                headers={
                    "Retry-After": "1",
                    "X-RateLimit-Retry-After-Ms": "3200",
                    "X-RateLimit-Scope": "ip_flood",
                },
            ),
            "GET",
            "/api/a",
        )
    assert error.value.retry_after_seconds == 4
    gate.acquire("GET", "/api/b")
    assert clock.now == 4


def test_later_cooldown_does_not_shorten_existing_one():
    clock = Clock()
    gate = limiter(clock)
    for delay in (10, 2):
        with pytest.raises(RateLimitError):
            gate.observe(
                httpx.Response(429, headers={"Retry-After": str(delay)}),
                "GET",
                "/api/a",
            )
    gate.acquire("GET", "/api/a")
    assert clock.now == 10


def test_upload_budget_is_not_a_timed_rate_limit():
    clock = Clock()
    gate = limiter(clock)
    with pytest.raises(UploadQuotaError) as error:
        gate.observe(
            httpx.Response(
                429,
                headers={"Retry-After": "60"},
                json={"error": {"code": "UPLOAD_QUOTA_EXCEEDED"}},
            ),
            "POST",
            "/api/v1/messenger/media/upload/init",
        )
    assert error.value.code == "UPLOAD_QUOTA_EXCEEDED"
    assert not isinstance(error.value, RateLimitError)
    assert not gate._deadlines


def test_shared_limiter_coordinates_clients_and_does_not_retry_writes():
    clock = Clock()
    gate = limiter(clock)
    seen = []

    def transport(request):
        seen.append((clock.now, request.url.path))
        if len(seen) == 1:
            return httpx.Response(429, headers={"Retry-After": "5"})
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        a = Bot("fake", base_url="https://example.test", client=http, limiter=gate)
        b = Bot("other", base_url="https://example.test", client=http, limiter=gate)
        with pytest.raises(RateLimitError):
            a.send_message("a", "hello")
        assert len(seen) == 1
        b.send_message_to_user("b", "hello")
    assert [t for t, _ in seen] == [0, 5]


def test_transport_failures_still_consume_capacity():
    clock = Clock()
    gate = limiter(clock, **FIXED_INTERVALS)
    calls = []

    def transport(request):
        calls.append(clock.now)
        raise httpx.ReadTimeout("unknown write outcome")

    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        bot = Bot("fake", client=http, limiter=gate)
        for _ in range(2):
            with pytest.raises(httpx.ReadTimeout):
                bot.send_message("a", "hello")
    assert calls == pytest.approx([0, 2.1])


def test_concurrent_callers_do_not_overspend_group():
    clock = Clock()
    gate = limiter(clock, min_interval=0, **FIXED_INTERVALS)
    barrier = threading.Barrier(12)

    def run(_):
        barrier.wait(timeout=5)
        gate.acquire("POST", "/api/v1/messenger/chats/a/messages")

    with ThreadPoolExecutor(12) as pool:
        list(pool.map(run, range(12)))
    assert clock.now >= 11 * 2.1 - 1e-8


def test_nonblocking_and_disabled_options():
    clock = Clock()
    gate = limiter(clock, wait=False, min_interval=1.2)
    gate.acquire("GET", "/api/a")
    with pytest.raises(RateLimitError) as error:
        gate.acquire("GET", "/api/b")
    assert error.value.locally_blocked
    assert clock.now == 0
    off = limiter(clock, enabled=False)
    for _ in range(100):
        off.acquire("POST", "/api/v1/messenger/chats/a/messages")
    assert clock.now == 0


@pytest.mark.parametrize(
    "options",
    [
        {"requests_per_minute": 0},
        {"requests_per_minute": True},
        {"min_interval": float("nan")},
        {"send_interval": -1},
        {"reaction_interval": float("inf")},
        {"edit_interval": True},
        {"send_burst": 0},
        {"send_burst": -1},
        {"edit_burst": True},
        {"reaction_burst": 1.5},
        {"delete_for_me_burst": "4"},
    ],
)
def test_invalid_configuration(options):
    with pytest.raises(ValueError):
        RateLimitConfig(**options)


@pytest.mark.parametrize(
    "headers,body,seconds",
    [
        ({"X-RateLimit-Retry-After-Ms": "1500"}, {}, 2),
        ({}, {"retryAfter": 9}, 9),
        ({"Retry-After": "2"}, {"retry_after": 7}, 7),
        ({"Retry-After": "invalid", "X-RateLimit-Retry-After-Ms": "nan"}, {}, 30),
    ],
)
def test_wait_sources(headers, body, seconds):
    clock = Clock()
    gate = limiter(clock)
    with pytest.raises(RateLimitError) as error:
        gate.observe(
            httpx.Response(429, headers=headers, json={"error": body}), "GET", "/api/a"
        )
    assert error.value.retry_after_seconds == seconds


def test_wait_rechecks_new_server_cooldown():
    clock = Clock()
    gate = limiter(clock, min_interval=1.2)
    gate.acquire("GET", "/api/a")
    original_sleep = clock.sleep
    updated = False

    def sleep(seconds):
        nonlocal updated
        original_sleep(seconds)
        if not updated:
            updated = True
            with pytest.raises(RateLimitError):
                gate.observe(
                    httpx.Response(
                        429,
                        headers={
                            "Retry-After": "10",
                            "X-RateLimit-Scope": "global_flood",
                        },
                    ),
                    "GET",
                    "/api/a",
                )

    gate._sleep = sleep
    gate.acquire("GET", "/api/b")
    assert clock.now == pytest.approx(11.2)


def test_upload_stages_use_common_budget_and_do_not_repeat_failed_part():
    clock = Clock()
    gate = limiter(clock, requests_per_minute=1)
    seen = []

    def transport(request):
        seen.append((request.url.path, clock.now))
        if request.url.path.endswith("/init"):
            return httpx.Response(200, json={"uploadId": "upload"})
        return httpx.Response(429, headers={"Retry-After": "10"})

    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        bot = Bot("fake", client=http, limiter=gate)
        with pytest.raises(RateLimitError):
            bot.upload_file(b"test", filename="test.txt")
    assert len(seen) == 2
    assert seen[0][1] == 0 and seen[1][1] == pytest.approx(60)
    assert seen[1][0].endswith("/part/0")


def test_refresh_and_api_request_use_same_budget():
    import base64
    import json
    import time

    payload = (
        base64.urlsafe_b64encode(
            json.dumps({"sub": "user", "exp": time.time() + 3600}).encode()
        )
        .decode()
        .rstrip("=")
    )
    token = "header." + payload + ".signature"
    clock = Clock()
    gate = limiter(clock, requests_per_minute=1)
    seen = []

    def transport(request):
        seen.append((request.url.path, clock.now))
        if request.url.path.endswith("/auth/refresh"):
            return httpx.Response(200, json={"accessToken": token})
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        bot = Bot.from_refresh_token(
            "fake", session_path=None, client=http, limiter=gate
        )
        bot.get_chats()
    assert [p for p, _ in seen] == ["/api/v1/auth/refresh", "/api/v1/messenger/chats"]
    assert [t for _, t in seen] == pytest.approx([0, 60])


def test_websocket_429_obeys_shared_cooldown(monkeypatch):
    websocket = pytest.importorskip("websocket")
    clock = Clock()
    gate = limiter(clock)
    error = websocket.WebSocketBadStatusException("limited", 429)
    error.status_code = 429
    error.resp_headers = {"Retry-After": "12", "X-RateLimit-Scope": "global_flood"}
    error.resp_body = "{}"

    def connect(*args, **kwargs):
        raise error

    monkeypatch.setattr(websocket, "create_connection", connect)
    with Bot("fake", limiter=gate) as bot:
        with pytest.raises(RateLimitError):
            next(bot.websocket_events())
    gate.acquire("GET", "/api/other")
    assert clock.now == 12


def test_forward_batch_reserves_message_weight_not_extra_http_slots():
    clock = Clock()
    gate = limiter(clock, min_interval=0, **FIXED_INTERVALS)
    seen = []

    def transport(request):
        seen.append(clock.now)
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        bot = Bot("fake", client=http, limiter=gate)
        bot.forward_messages("a", "a", ["one", "two", "three"])
        bot.send_message("a", "after batch")
    assert seen == pytest.approx([0, 6.3])
    assert len(gate._starts) == 2


@pytest.mark.parametrize("cost", [0, -1, True, 1.5])
def test_invalid_weight(cost):
    with pytest.raises(ValueError):
        RateLimiter().acquire("GET", "/api/a", cost=cost)


def test_default_has_no_delay_between_independent_requests():
    clock = Clock()
    gate = limiter(clock)
    for path in (
        "/api/users/me",
        "/api/v1/messenger/chats",
        "/api/v1/messenger/updates",
    ):
        gate.acquire("GET", path)
    assert clock.now == 0 and clock.sleeps == []


def test_global_budget_can_be_disabled_without_disabling_message_rules():
    clock = Clock()
    gate = limiter(clock, requests_per_minute=None, **FIXED_INTERVALS)
    for _ in range(100):
        gate.acquire("GET", "/api/v1/messenger/chats")
    assert clock.now == 0
    for _ in range(2):
        gate.acquire("POST", "/api/v1/messenger/chats/a/messages")
    assert clock.now == pytest.approx(2.1)
    assert not gate._starts


def test_delete_for_me_has_own_rule_and_batch_counts_as_one_request():
    clock = Clock()
    gate = limiter(clock, **FIXED_INTERVALS)
    seen = []

    def transport(request):
        seen.append(clock.now)
        return httpx.Response(200, json={"ok": [], "failed": []})

    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        bot = Bot("fake", client=http, limiter=gate)
        bot.delete_messages("a", ["one", "two", "three"])
        bot.delete_messages("b", ["four"])
        bot.delete_messages("a", ["five"], for_everyone=True)
    assert seen == pytest.approx([0, 3.2, 3.2])


def test_unmeasured_routes_have_separate_template_cooldowns():
    clock = Clock()
    gate = limiter(clock)
    with pytest.raises(RateLimitError):
        gate.observe(
            httpx.Response(429, headers={"Retry-After": "9"}),
            "GET",
            "/api/v1/messenger/chats/a/messages/x/reactions",
        )
    gate.acquire("GET", "/api/v1/messenger/chats/unread")
    gate.acquire("GET", "/api/v1/messenger/chats/b")
    assert clock.now == 0
    gate.acquire("GET", "/api/v1/messenger/chats/b/messages/y/reactions")
    assert clock.now == 9


def test_upload_parts_share_cooldown_but_not_finalize():
    clock = Clock()
    gate = limiter(clock)
    with pytest.raises(RateLimitError):
        gate.observe(
            httpx.Response(429, headers={"Retry-After": "7"}),
            "PUT",
            "/api/v1/messenger/media/upload/first/part/0",
        )
    gate.acquire("POST", "/api/v1/messenger/media/upload/other/finalize")
    assert clock.now == 0
    gate.acquire("PUT", "/api/v1/messenger/media/upload/other/part/3")
    assert clock.now == 7


def test_literal_unread_route_is_not_mistaken_for_chat_id():
    clock = Clock()
    gate = limiter(clock)
    with pytest.raises(RateLimitError):
        gate.observe(
            httpx.Response(429, headers={"Retry-After": "8"}),
            "GET",
            "/api/v1/messenger/chats/unread",
        )
    gate.acquire("GET", "/api/v1/messenger/chats/a")
    assert clock.now == 0
    gate.acquire("GET", "/api/v1/messenger/chats/unread")
    assert clock.now == 8


@pytest.mark.parametrize("scope", ["new_recipients_hour", "new_recipients_day"])
@pytest.mark.parametrize("target", ["chats/new/messages", "users/new/messages"])
def test_new_recipient_cooldown_does_not_block_existing_correspondence(scope, target):
    clock = Clock()
    gate = limiter(clock)
    path = "/api/v1/messenger/" + target
    with pytest.raises(RateLimitError):
        gate.observe(
            httpx.Response(
                429, headers={"Retry-After": "3600", "X-RateLimit-Scope": scope}
            ),
            "POST",
            path,
        )
    gate.acquire("POST", "/api/v1/messenger/chats/existing/messages")
    assert clock.now == 0
    gate.acquire("GET", path)
    assert clock.now == 0
    with pytest.raises(RateLimitError) as error:
        gate.check("POST", path + "?ignored=1")
    assert error.value.scope == scope and error.value.locally_blocked
    gate.acquire("POST", path)
    assert clock.now == 3600


SEND = "/api/v1/messenger/chats/a/messages"
BUCKETS = [
    ("POST", SEND, 4, 2.1),
    ("PATCH", "/api/v1/messenger/chats/a/messages/x", 4, 6.2),
    ("PUT", "/api/v1/messenger/chats/a/messages/x/reactions", 25, 1.1),
    ("POST", "/api/v1/messenger/chats/a/messages/delete-for-me", 8, 3.2),
]


def test_bucket_scope_is_only_the_group_for_now():
    from itd_bot.rate_limits import _bucket_scope

    assert _bucket_scope("send", "POST", SEND) == "send"
    assert _bucket_scope("send", "POST", SEND.replace("/a/", "/b/")) == "send"


@pytest.mark.parametrize("method,path,burst,interval", BUCKETS)
def test_burst_is_spent_without_waiting_then_one_token_per_interval(
    method, path, burst, interval
):
    clock = Clock()
    gate = limiter(clock, min_interval=0)
    for _ in range(burst):
        gate.acquire(method, path)
    assert clock.now == 0 and clock.sleeps == []
    for turn in range(1, 4):
        gate.acquire(method, path)
        assert clock.now == pytest.approx(turn * interval)
    assert clock.sleeps == pytest.approx([interval] * 3)


def test_idle_time_refills_the_bucket_up_to_burst():
    clock = Clock()
    gate = limiter(clock)
    for _ in range(4):
        gate.acquire("POST", SEND)
    clock.now += 4 * 2.1
    for _ in range(4):
        gate.acquire("POST", SEND)
    assert clock.sleeps == []
    gate.acquire("POST", SEND)
    assert clock.sleeps == pytest.approx([2.1])

    clock = Clock()
    gate = limiter(clock)
    for _ in range(4):
        gate.acquire("POST", SEND)
    clock.now += 2.1
    gate.acquire("POST", SEND)
    assert clock.sleeps == []
    gate.acquire("POST", SEND)
    assert clock.sleeps == pytest.approx([2.1])


def test_burst_one_keeps_fixed_interval():
    clock = Clock()
    gate = limiter(clock, send_burst=1)
    for _ in range(3):
        gate.acquire("POST", SEND)
    assert clock.now == pytest.approx(2 * 2.1)
    clock.now += 100
    start = clock.now
    gate.acquire("POST", SEND)
    assert clock.now == start


def test_forward_cost_spends_that_many_tokens_when_it_fits_the_burst():
    clock = Clock()
    gate = limiter(clock, min_interval=0)
    seen = []

    def transport(request):
        seen.append(clock.now)
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        bot = Bot("fake", client=http, limiter=gate)
        bot.forward_messages("a", "a", ["one", "two", "three"])
        bot.send_message("a", "fits the last token")
        bot.send_message("a", "waits")
    assert seen == pytest.approx([0, 0, 2.1])
    assert len(gate._starts) == 3


def test_cost_above_burst_waits_for_full_bucket_then_goes_into_debt():
    clock = Clock()
    gate = limiter(clock, min_interval=0)
    gate.acquire("POST", SEND)
    gate.acquire("POST", SEND)
    gate.acquire("POST", SEND, cost=6)
    assert clock.now == pytest.approx(2 * 2.1)
    gate.acquire("POST", SEND)
    assert clock.now == pytest.approx(2 * 2.1 + 3 * 2.1)


def test_cost_above_burst_on_fresh_bucket_does_not_wait_or_hang():
    clock = Clock()
    gate = limiter(clock, min_interval=0)
    gate.acquire("POST", SEND, cost=9)
    assert clock.now == 0
    gate.acquire("POST", SEND)
    assert clock.now == pytest.approx((9 - 4 + 1) * 2.1)


def test_cost_above_burst_nonblocking_reports_local_block():
    clock = Clock()
    gate = limiter(clock, wait=False)
    gate.acquire("POST", SEND)
    with pytest.raises(RateLimitError) as error:
        gate.acquire("POST", SEND, cost=6)
    assert error.value.locally_blocked and error.value.retry_after_seconds == 3
    assert clock.now == 0


def test_milliseconds_retry_after_is_not_rounded_up_and_gives_one_token():
    clock = Clock()
    gate = limiter(clock, min_interval=0)
    with pytest.raises(RateLimitError) as error:
        gate.observe(
            httpx.Response(
                429,
                headers={
                    "X-RateLimit-Retry-After-Ms": "54",
                    "X-RateLimit-Scope": "chat",
                },
            ),
            "POST",
            SEND,
        )
    assert error.value.retry_after_seconds == 1
    times = []
    for _ in range(3):
        gate.acquire("POST", SEND)
        times.append(clock.now)
    assert times == pytest.approx([0.054, 0.054 + 2.1, 0.054 + 4.2])


def test_rounded_retry_after_header_does_not_hide_precise_milliseconds():
    clock = Clock()
    gate = limiter(clock)
    with pytest.raises(RateLimitError) as error:
        gate.observe(
            httpx.Response(
                429,
                headers={"Retry-After": "1", "X-RateLimit-Retry-After-Ms": "54"},
            ),
            "POST",
            SEND,
        )
    assert error.value.retry_after_seconds == 1
    gate.acquire("POST", SEND)
    assert clock.now == pytest.approx(0.054)


def test_longer_integer_retry_after_wins_over_shorter_milliseconds():
    clock = Clock()
    gate = limiter(clock)
    with pytest.raises(RateLimitError):
        gate.observe(
            httpx.Response(
                429,
                headers={"Retry-After": "5", "X-RateLimit-Retry-After-Ms": "54"},
            ),
            "POST",
            SEND,
        )
    gate.acquire("POST", SEND)
    assert clock.now == 5


def test_429_empties_bucket_that_still_looked_full():
    clock = Clock()
    gate = limiter(clock, min_interval=0)
    gate.acquire("POST", SEND)
    with pytest.raises(RateLimitError):
        gate.observe(httpx.Response(429, headers={"Retry-After": "2"}), "POST", SEND)
    clock.now += 2
    gate.acquire("POST", SEND)
    assert clock.sleeps == []
    gate.acquire("POST", SEND)
    assert clock.sleeps == pytest.approx([2.1])


def test_429_on_one_group_does_not_empty_other_buckets():
    clock = Clock()
    gate = limiter(clock, min_interval=0)
    with pytest.raises(RateLimitError):
        gate.observe(httpx.Response(429, headers={"Retry-After": "8"}), "POST", SEND)
    for _ in range(4):
        gate.acquire("PATCH", "/api/v1/messenger/chats/a/messages/x")
    assert clock.now == 0


def test_concurrent_callers_never_exceed_burst_per_interval():
    clock = Clock()
    gate = limiter(clock, min_interval=0)
    grants = []
    spend = gate._bucket_spend

    def record(scope, group, now, cost):
        grants.append(now)
        spend(scope, group, now, cost)

    gate._bucket_spend = record
    barrier = threading.Barrier(12)

    def run(_):
        barrier.wait(timeout=5)
        gate.acquire("POST", SEND)

    with ThreadPoolExecutor(12) as pool:
        list(pool.map(run, range(12)))
    grants.sort()
    assert len(grants) == 12
    assert grants[3] == 0 and grants[4] > 0
    for first, later in zip(grants, grants[4:]):
        assert later - first >= 2.1 - 1e-6
    assert clock.now >= 8 * 2.1 - 1e-6
