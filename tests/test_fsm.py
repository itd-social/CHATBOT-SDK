"""Detached dialogue records, atomic file failures and Router integration."""

import copy
import json
import runpy
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from helpers import Transport, patch_os

from itd_bot import (
    FSM,
    ErrorAction,
    EventContext,
    FileFSMStore,
    MemoryFSMStore,
    MessageContext,
    RedisFSMStore,
    Router,
    filters,
)
from itd_bot import fsm as module

KEY = ("bot-1", "chat-1", "user-1")


class Redis:
    def __init__(self):
        self.values = {}
        self.lock = threading.Lock()
        self.closed = 0

    def get(self, key):
        with self.lock:
            return self.values.get(key)

    def set(self, key, value):
        with self.lock:
            self.values[key] = value.encode("utf-8")
        return True

    def delete(self, key):
        with self.lock:
            return int(self.values.pop(key, None) is not None)

    def close(self):
        self.closed += 1


@pytest.fixture(params=["memory", "file", "redis"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryFSMStore()
    if request.param == "file":
        return FileFSMStore(tmp_path / "fsm.json")
    return RedisFSMStore(Redis())


def context(bot, *, chat="chat-1", user="user-1", number=1, text="hello"):
    raw = {
        "type": "new_message",
        "chat_id": chat,
        "event_seq": number,
        "message": {"message_id": str(number), "sender_id": user, "text": text},
        "future": True,
    }
    return MessageContext(bot, raw, raw["message"])


def test_records_are_detached_and_set_replaces_both_fields(store):
    missing = store.get(KEY)
    assert missing == {"state": None, "data": {}}
    missing["data"]["not-saved"] = True
    assert store.get(KEY) == {"state": None, "data": {}}
    data = {"name": "fictional", "nested": {"items": [1]}}
    store.set(KEY, "age", data=data)
    data["nested"]["items"].append(2)
    result = store.get(KEY)
    result["data"]["nested"]["items"].append(3)
    assert store.get(KEY) == {
        "state": "age",
        "data": {"name": "fictional", "nested": {"items": [1]}},
    }
    store.set(KEY, "done")
    assert store.get(KEY) == {"state": "done", "data": {}}
    store.set(KEY, None, data={"kept": True})
    assert store.get(KEY) == {"state": None, "data": {"kept": True}}
    store.clear(KEY)
    store.clear(KEY)
    assert store.get(KEY) == {"state": None, "data": {}}


def test_namespace_chat_and_user_are_independent_and_encoded_without_collisions(store):
    keys = [
        KEY,
        ("bot-2", "chat-1", "user-1"),
        ("bot-1", "chat-2", "user-1"),
        ("bot-1", "chat-1", "user-2"),
        ("a:b", "c", "d"),
        ("a", "b:c", "d"),
        ("../бот", "[]", '"\\'),
    ]
    for number, key in enumerate(keys):
        store.set(key, str(number), data={"number": number})
    assert [store.get(key)["data"]["number"] for key in keys] == list(range(len(keys)))
    store.clear(KEY)
    assert store.get(keys[1])["state"] == "1"


def test_shared_store_is_thread_safe_for_distinct_dialogues(store):
    def work(number):
        key = ("bot-1", "chat-1", str(number))
        for step in range(3):
            store.set(key, str(step), data={"number": number, "step": step})
            assert store.get(key) == {
                "state": str(step),
                "data": {"number": number, "step": step},
            }

    with ThreadPoolExecutor(8) as executor:
        list(executor.map(work, range(24)))
    assert store.get(("bot-1", "chat-1", "23"))["state"] == "2"


@pytest.mark.parametrize(
    "bad",
    [
        ("", "chat", "user"),
        ("bot", None, "user"),
        ("bot", "chat"),
        ["bot", "chat", "user"],
        ("bot", "chat", 1),
    ],
)
def test_invalid_keys_fail_without_creating_records(store, bad):
    for method in (store.get, store.clear):
        with pytest.raises(ValueError, match="Ключ FSM"):
            method(bad)
    with pytest.raises(ValueError, match="Ключ FSM"):
        store.set(bad, "step")
    assert store.get(KEY)["state"] is None


@pytest.mark.parametrize(
    "state,data", [(1, {}), (False, {}), ("step", []), ("step", "private")]
)
def test_invalid_records_do_not_replace_previous_value(store, state, data):
    store.set(KEY, "old", data={"kept": True})
    with pytest.raises(ValueError):
        store.set(KEY, state, data=data)
    assert store.get(KEY) == {"state": "old", "data": {"kept": True}}


def test_file_survives_new_instance_and_empty_clear_creates_nothing(tmp_path):
    path = tmp_path / "fsm.json"
    first = FileFSMStore(path)
    first.clear(KEY)
    assert not path.exists()
    first.set(KEY, "step", data={"text": "Здравствуйте"})
    second = FileFSMStore(path)
    assert second.get(KEY) == {"state": "step", "data": {"text": "Здравствуйте"}}
    second.clear(KEY)
    assert first.get(KEY) == {"state": None, "data": {}}
    assert json.loads(path.read_text(encoding="utf-8"))["records"] == {}


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not-json",
        "[]",
        '{"version":true,"records":{}}',
        '{"version":2,"records":{}}',
        '{"version":1,"records":[]}',
        '{"version":1,"records":{"bad":{}}}',
        '{"version":1,"records":{"null":{}}}',
        '{"version":1,"records":{"[1,2,3]":{}}}',
    ],
)
def test_corrupt_file_is_never_silently_reset_or_overwritten(tmp_path, raw):
    path = tmp_path / "fsm.json"
    path.write_text(raw, encoding="utf-8")
    store = FileFSMStore(path)
    for operation in (
        lambda: store.get(KEY),
        lambda: store.set(KEY, "new"),
        lambda: store.clear(KEY),
    ):
        with pytest.raises(ValueError, match="файл FSM"):
            operation()
        assert path.read_text(encoding="utf-8") == raw
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(
    "value", [{}, {"data": {}}, {"state": "step", "data": []}, {"state": 1, "data": {}}]
)
def test_file_and_redis_reject_malformed_persistent_record(tmp_path, value):
    path = tmp_path / "fsm.json"
    path.write_text(
        json.dumps({"version": 1, "records": {json.dumps(KEY): value}}),
        encoding="utf-8",
    )
    client = Redis()
    redis_store = RedisFSMStore(client)
    client.values[redis_store._key(KEY)] = json.dumps(value).encode()
    for store in (FileFSMStore(path), redis_store):
        with pytest.raises(ValueError):
            store.get(KEY)


def test_equivalent_duplicate_file_keys_are_rejected(tmp_path):
    path = tmp_path / "fsm.json"
    value = {"state": None, "data": {}}
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "records": {
                    json.dumps(KEY): value,
                    json.dumps(KEY, separators=(",", ":")): value,
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        FileFSMStore(path).get(KEY)


@pytest.mark.parametrize("stage", ["write", "fsync", "replace"])
def test_failed_atomic_save_keeps_old_file_and_cleans_temporary(
    tmp_path, monkeypatch, stage
):
    path = tmp_path / "fsm.json"
    store = FileFSMStore(path)
    store.set(KEY, "old", data={"kept": True})
    before = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("fictional disk failure")

    if stage == "write":
        original = module.tempfile.NamedTemporaryFile

        class Broken:
            def __init__(self, *args, **kwargs):
                self.stream = original(*args, **kwargs)

            def __enter__(self):
                self.stream.__enter__()
                self.name = self.stream.name
                return self

            def write(self, value):
                fail()

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

        monkeypatch.setattr(module.tempfile, "NamedTemporaryFile", Broken)
    else:
        patch_os(monkeypatch, module, **{stage: fail})
    with pytest.raises(OSError, match="fictional"):
        store.set(KEY, "new")
    assert path.read_bytes() == before and store.get(KEY)["state"] == "old"
    assert list(tmp_path.iterdir()) == [path]


def test_file_parent_and_read_errors_are_not_hidden(tmp_path, monkeypatch):
    missing = tmp_path / "absent" / "fsm.json"
    with pytest.raises(FileNotFoundError):
        FileFSMStore(missing).set(KEY, "step")
    store = FileFSMStore(tmp_path / "fsm.json")
    monkeypatch.setattr(
        module.Path,
        "read_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("fictional")),
    )
    with pytest.raises(PermissionError):
        store.get(KEY)


@pytest.mark.parametrize("data", [{"value": object()}, {"value": float("nan")}])
def test_persistent_non_json_data_fails_without_replacing_record(tmp_path, data):
    for store in (FileFSMStore(tmp_path / "fsm.json"), RedisFSMStore(Redis())):
        store.set(KEY, "old")
        with pytest.raises((ValueError, TypeError)):
            store.set(KEY, "new", data=data)
        assert store.get(KEY) == {"state": "old", "data": {}}


def test_fsm_addresses_context_and_keeps_raw_event_intact(store):
    bot = Transport().bot()
    ctx = context(bot)
    original = copy.deepcopy(ctx.event)
    fsm = FSM(store, namespace="bot-1")
    fsm.set(ctx, "step", data={"x": 1})
    assert store.get(KEY) == fsm.get(ctx) == {"state": "step", "data": {"x": 1}}
    assert FSM(store, namespace="bot-2").get(ctx)["state"] is None
    fsm.clear(ctx)
    assert fsm.get(ctx) == {"state": None, "data": {}} and ctx.event == original


@pytest.mark.parametrize("namespace", [None, "", 1])
def test_fsm_requires_a_namespace(namespace):
    with pytest.raises(ValueError):
        FSM(MemoryFSMStore(), namespace=namespace)


def test_state_filter_combines_and_ignores_non_message_contexts(store):
    fsm = FSM(store, namespace="bot-1")
    bot = Transport().bot()
    ctx = context(bot)
    condition = filters.state(fsm, None) & filters.text("hello")
    assert condition(ctx)
    fsm.set(ctx, "step")
    assert not condition(ctx) and filters.state(fsm, "step")(ctx)
    event = EventContext(
        bot, {"type": "presence", "chat_id": "chat-1", "user_id": "user-1"}
    )
    assert not filters.state(fsm, None)(event)
    with pytest.raises(ValueError):
        fsm.get(event)
    with pytest.raises(ValueError):
        filters.state(fsm, 1)


def test_state_filter_storage_failure_reaches_error_policy_without_checkpoint():
    class Broken(MemoryFSMStore):
        def get(self, key):
            raise OSError("fictional storage failure")

    transport = Transport(
        httpx.Response(
            200,
            json={
                "events": [context(None).event],
                "next_event_seq": 1,
                "reset_required": False,
            },
        )
    )
    fsm = FSM(Broken(), namespace="bot-1")
    router = Router(
        transport.bot(), self_user_id="self", auto_typing=False, read_mode="off"
    )
    router.message(filters.state(fsm, None))(lambda ctx: pytest.fail("store failed"))
    errors, checkpoints = [], []
    router.on_error(lambda exc, event: errors.append(type(exc)) or ErrorAction.STOP)
    with pytest.raises(OSError):
        router.run_polling(0, handle_signals=False, on_checkpoint=checkpoints.append)
    assert errors == [OSError] and not checkpoints and len(transport.requests) == 1


def test_redis_keys_hide_raw_identifiers_and_clients_remain_caller_owned():
    client = Redis()
    with RedisFSMStore(client) as store:
        store.set(KEY, "step")
    store.close()
    assert client.closed == 0
    (key,) = client.values
    assert key.startswith("itd:fsm:") and all(part not in key for part in KEY)
    assert RedisFSMStore(client, prefix="other").get(KEY)["state"] is None


def test_redis_errors_and_unacknowledged_set_are_not_hidden_or_retried():
    class Broken(Redis):
        calls = 0

        def set(self, key, value):
            self.calls += 1
            return False

    client = Broken()
    with pytest.raises(RuntimeError, match="не подтвердил"):
        RedisFSMStore(client).set(KEY, "step")
    assert client.calls == 1
    client.get = lambda key: (_ for _ in ()).throw(ConnectionError("fictional"))
    with pytest.raises(ConnectionError):
        RedisFSMStore(client).get(KEY)


def test_optional_redis_import_and_client_configuration(monkeypatch):
    calls, client = [], Redis()

    class Retry:
        def __init__(self, backoff, count):
            self.count = count

    def create(url, **kwargs):
        calls.append((url, kwargs))
        return client

    modules = {
        "redis": types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=create)),
        "redis.retry": types.SimpleNamespace(Retry=Retry),
        "redis.backoff": types.SimpleNamespace(NoBackoff=object),
    }
    monkeypatch.setattr(
        module, "importlib", types.SimpleNamespace(import_module=modules.__getitem__)
    )
    with RedisFSMStore.from_url("redis://example.test/0", socket_timeout=5):
        pass
    assert client.closed == 1 and calls[0][1]["retry"].count == 0
    assert calls[0][1]["socket_timeout"] == 5
    explicit = object()
    RedisFSMStore.from_url("redis://example.test/0", retry=explicit)
    assert calls[1][1]["retry"] is explicit
    with pytest.raises(ValueError):
        RedisFSMStore.from_url("redis://example.test/0", prefix="")
    assert len(calls) == 2


@pytest.mark.parametrize("missing", ["redis", "dependency"])
def test_optional_redis_import_failure_is_clear_and_preserves_broken_dependency(
    monkeypatch, missing
):
    def fail(name):
        raise ModuleNotFoundError("fictional missing module", name=missing)

    monkeypatch.setattr(module, "importlib", types.SimpleNamespace(import_module=fail))
    expected = RuntimeError if missing == "redis" else ModuleNotFoundError
    with pytest.raises(expected):
        RedisFSMStore.from_url("redis://example.test/0")


def example_router(transport, state):
    example = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/fsm_bot/fsm_bot.py")
    )
    return example["configure"](
        transport.bot(), state, auto_typing=False, read_mode="off"
    )


def test_three_step_example_keeps_invalid_input_and_can_cancel(store):
    def answer(request):
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": "self", "username": "bot"})
        return httpx.Response(201, json={"message_id": "sent-1"})

    transport = Transport(default=answer)
    fsm = FSM(store, namespace="bot-1")
    router = example_router(transport, fsm)
    ctx = context(router._bound().bot)
    steps = [
        ("/start", "name", {}),
        ("fictional name", "age", {"name": "fictional name"}),
        ("invalid age", "age", {"name": "fictional name"}),
        ("21", "confirm", {"name": "fictional name", "age": "21"}),
        ("нет", "confirm", {"name": "fictional name", "age": "21"}),
        ("да", None, {}),
        ("/start", "name", {}),
        ("/cancel", None, {}),
    ]
    for number, (text, expected, data) in enumerate(steps, 1):
        router.handle(context(ctx.bot, number=number, text=text).event)
        assert fsm.get(ctx) == {"state": expected, "data": data}
    assert len(transport.requests) == 9
    assert all(
        "fictional name" not in request.content.decode()
        for request in transport.requests
    )


@pytest.mark.parametrize("queued", [False, True])
def test_dialogues_across_packets_keep_chat_order_and_users_separate(
    store, tmp_path, queued
):
    packet = [
        context(None, number=1, text="fictional name").event,
        context(None, number=2, text="21").event,
        context(None, number=3, text="да").event,
        context(None, chat="chat-2", number=4, user="user-2", text="other name").event,
    ]
    stop = threading.Event()

    def answer(request):
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": "self", "username": "bot"})
        if request.method == "GET":
            cursor = request.url.params["since"]
            return httpx.Response(
                200,
                json={
                    "events": packet[:2]
                    if cursor == "0"
                    else packet[2:]
                    if cursor == "2"
                    else [],
                    "next_event_seq": 2 if cursor == "0" else 4,
                    "reset_required": False,
                },
            )
        return httpx.Response(201, json={"message_id": "sent-1"})

    transport = Transport(default=answer)
    fsm = FSM(store, namespace="bot-1")
    router = example_router(transport, fsm)
    bot = router._bound().bot
    first, second = context(bot), context(bot, chat="chat-2", user="user-2")
    fsm.set(first, "name")
    fsm.set(second, "name")
    path = tmp_path / "cursor.json"
    router.run_polling(
        0,
        state_path=path,
        workers=2,
        chat_queues=queued,
        handle_signals=False,
        stop=stop,
        interval=0.001,
        on_checkpoint=lambda cursor: stop.set() if cursor == 4 else None,
    )
    assert fsm.get(first) == {"state": None, "data": {}}
    assert fsm.get(second) == {"state": "age", "data": {"name": "other name"}}
    assert json.loads(path.read_text(encoding="utf-8"))["cursor"] == 4
    replies = [
        json.loads(request.content)
        for request in transport.requests
        if request.method == "POST"
    ]
    assert len(replies) == 4


def test_failed_file_delete_keeps_previous_record(tmp_path, monkeypatch):
    path = tmp_path / "fsm.json"
    store = FileFSMStore(path)
    store.set(KEY, "old")
    before = path.read_bytes()

    def fail(*args):
        raise PermissionError("fictional")

    patch_os(monkeypatch, module, replace=fail)
    with pytest.raises(PermissionError):
        store.clear(KEY)
    assert path.read_bytes() == before
