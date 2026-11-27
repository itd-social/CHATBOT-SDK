"""Run against a dedicated Redis service only when explicitly configured."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from itd_bot import RedisFSMStore

URL = os.environ.get("ITD_TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(not URL, reason="Dedicated Redis URL is not configured")


@pytest.mark.parametrize("decoded", [False, True])
def test_real_redis_records_namespaces_and_concurrent_keys(decoded):
    prefix = "itd-test-fsm:" + uuid.uuid4().hex
    keys = [("bot-1", "chat-1", "user-1"), ("bot-2", "chat-1", "user-1")]
    keys += [("bot-1", "chat-2", str(number)) for number in range(12)]
    with RedisFSMStore.from_url(
        URL,
        prefix=prefix,
        decode_responses=decoded,
        socket_timeout=5,
        socket_connect_timeout=5,
    ) as store:
        try:
            assert store.get(keys[0]) == {"state": None, "data": {}}

            def work(key):
                data = {"user": key[2], "nested": ["fictional", "Здравствуйте"]}
                store.set(key, "step", data=data)
                result = store.get(key)
                result["data"]["nested"].append("not-saved")
                assert store.get(key) == {"state": "step", "data": data}

            with ThreadPoolExecutor(4) as pool:
                list(pool.map(work, keys))
            store.clear(keys[0])
            assert store.get(keys[0])["state"] is None
            assert store.get(keys[1])["state"] == "step"
        finally:
            for key in keys:
                store.clear(key)
