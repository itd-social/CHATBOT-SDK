"""Optional dialogue state; storage operations replace whole records."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Protocol, TypedDict, cast

from .context import MessageContext

FSMKey = tuple[str, str, str]


class FSMRecord(TypedDict):
    state: str | None
    data: dict[str, Any]


def _record(state: str | None, data: dict[str, Any] | None) -> FSMRecord:
    if state is not None and not isinstance(state, str):
        raise ValueError("Состояние FSM должно быть строкой или None")
    if data is not None and not isinstance(data, dict):
        raise ValueError("Данные FSM должны быть словарём")
    return {"state": state, "data": copy.deepcopy(data) if data is not None else {}}


def _decode_record(value: Any) -> FSMRecord:
    if (
        not isinstance(value, dict)
        or "state" not in value
        or not isinstance(value.get("data"), dict)
    ):
        raise ValueError("Некорректная запись FSM")
    return _record(value["state"], value["data"])


def _key_text(key: FSMKey) -> str:
    if (
        not isinstance(key, tuple)
        or len(key) != 3
        or any(not isinstance(part, str) or not part for part in key)
    ):
        raise ValueError("Ключ FSM должен содержать namespace, чат и пользователя")
    return json.dumps(key, ensure_ascii=True, separators=(",", ":"))


class FSMStore(Protocol):
    """Thread-safe get/set/clear for (namespace, chat, user).

    get returns a detached record, or {state: None, data: {}} for a missing key.
    set replaces state and data together. No read-modify-write transaction is
    implied; serialize dialogue handlers with chat queues.
    """

    def get(self, key: FSMKey) -> FSMRecord: ...

    def set(
        self, key: FSMKey, state: str | None, *, data: dict[str, Any] | None = None
    ) -> None: ...

    def clear(self, key: FSMKey) -> None: ...


class MemoryFSMStore:
    """Keep detached records in one process; nothing survives a restart."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[FSMKey, FSMRecord] = {}

    def get(self, key: FSMKey) -> FSMRecord:
        _key_text(key)
        with self._lock:
            return copy.deepcopy(self._records.get(key, {"state": None, "data": {}}))

    def set(
        self, key: FSMKey, state: str | None, *, data: dict[str, Any] | None = None
    ) -> None:
        _key_text(key)
        value = _record(state, data)
        with self._lock:
            self._records[key] = value

    def clear(self, key: FSMKey) -> None:
        _key_text(key)
        with self._lock:
            self._records.pop(key, None)


class FileFSMStore:
    """Atomically replace a JSON file; share one instance per file/process.

    Parent directories must exist. Data must be JSON-serializable. Different
    instances/processes do not coordinate read-modify-write operations.
    """

    def __init__(self, path: str | os.PathLike[Any]) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()

    def _load(self) -> dict[str, FSMRecord]:
        try:
            content = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            payload = json.loads(content)
            if (
                not isinstance(payload, dict)
                or type(payload.get("version")) is not int
                or payload["version"] != 1
                or not isinstance(payload.get("records"), dict)
            ):
                raise ValueError("Некорректный формат файла FSM")
            records = {}
            for key, value in payload["records"].items():
                parts = json.loads(key)
                if not isinstance(parts, list):
                    raise ValueError("Некорректный ключ в файле FSM")
                normalized = _key_text(cast(FSMKey, tuple(parts)))
                if normalized in records:
                    raise ValueError("Повторный ключ в файле FSM")
                records[normalized] = _decode_record(value)
            return records
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "Не удалось разобрать файл FSM; исходный файл сохранён"
            ) from exc

    def _save(self, records: dict[str, FSMRecord]) -> None:
        content = json.dumps(
            {"version": 1, "records": records}, ensure_ascii=True, allow_nan=False
        )
        name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=self._path.name + ".",
                suffix=".tmp",
                delete=False,
            ) as stream:
                name = stream.name
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self._path)
        finally:
            if name is not None:
                try:
                    os.unlink(name)
                except FileNotFoundError:
                    pass

    def get(self, key: FSMKey) -> FSMRecord:
        serialized = _key_text(key)
        with self._lock:
            return self._load().get(serialized, {"state": None, "data": {}})

    def set(
        self, key: FSMKey, state: str | None, *, data: dict[str, Any] | None = None
    ) -> None:
        serialized = _key_text(key)
        value = _record(state, data)
        with self._lock:
            records = self._load()
            records[serialized] = value
            self._save(records)

    def clear(self, key: FSMKey) -> None:
        serialized = _key_text(key)
        with self._lock:
            records = self._load()
            if serialized in records:
                del records[serialized]
                self._save(records)


class RedisFSMStore:
    """Use atomic Redis GET/SET/DEL with a synchronous, thread-safe client.

    Data must be JSON-serializable. A passed client remains caller-owned;
    from_url creates an owned client with retries disabled by default.
    """

    def __init__(self, client: Any, *, prefix: str = "itd:fsm") -> None:
        if not isinstance(prefix, str) or not prefix:
            raise ValueError("Префикс Redis FSM должен быть непустой строкой")
        self._client = client
        self._prefix = prefix
        self._owns_client = False

    @classmethod
    def from_url(
        cls, url: str, *, prefix: str = "itd:fsm", **kwargs: Any
    ) -> RedisFSMStore:
        store = cls(None, prefix=prefix)
        try:
            redis = importlib.import_module("redis")
        except ModuleNotFoundError as exc:
            if exc.name != "redis":
                raise
            raise RuntimeError(
                'Установите Redis extra: pip install "itd-chatbot-sdk[redis]"'
            ) from exc
        retry = importlib.import_module("redis.retry")
        backoff = importlib.import_module("redis.backoff")
        kwargs.setdefault("retry", retry.Retry(backoff.NoBackoff(), 0))
        store._client = redis.Redis.from_url(url, **kwargs)
        store._owns_client = True
        return store

    def _key(self, key: FSMKey) -> str:
        digest = hashlib.sha256(_key_text(key).encode("utf-8")).hexdigest()
        return self._prefix + ":" + digest

    def get(self, key: FSMKey) -> FSMRecord:
        payload = self._client.get(self._key(key))
        return (
            _record(None, None)
            if payload is None
            else _decode_record(json.loads(payload))
        )

    def set(
        self, key: FSMKey, state: str | None, *, data: dict[str, Any] | None = None
    ) -> None:
        serialized = self._key(key)
        payload = json.dumps(_record(state, data), ensure_ascii=True, allow_nan=False)
        if not self._client.set(serialized, payload):
            raise RuntimeError("Redis не подтвердил запись состояния FSM")

    def clear(self, key: FSMKey) -> None:
        self._client.delete(self._key(key))

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> RedisFSMStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class FSM:
    """Address dialogue records by explicit bot namespace, chat and sender."""

    def __init__(self, store: FSMStore, *, namespace: str) -> None:
        if not isinstance(namespace, str) or not namespace:
            raise ValueError("Задайте непустой namespace FSM для вашего бота")
        self._store = store
        self._namespace = namespace

    def _key(self, context: MessageContext) -> FSMKey:
        if not isinstance(context, MessageContext):
            raise ValueError("FSM требует контекст входящего сообщения")
        return self._namespace, context.chat_id, context.sender_id

    def get(self, context: MessageContext) -> FSMRecord:
        return self._store.get(self._key(context))

    def set(
        self,
        context: MessageContext,
        state: str | None,
        *,
        data: dict[str, Any] | None = None,
    ) -> None:
        self._store.set(self._key(context), state, data=data)

    def clear(self, context: MessageContext) -> None:
        self._store.clear(self._key(context))
