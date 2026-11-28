"""Internal WebSocket connection, heartbeat and authorization recovery."""

from __future__ import annotations

import json as jsonlib
import logging
import threading
import time
from collections.abc import Callable, Generator, Iterator
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

import httpx

from .errors import ProtocolError
from .schemas import UpdateEvent

if TYPE_CHECKING:
    from .client import Bot
_log = logging.getLogger("itd_bot.client")
_WS_AUTH_RETRIES = 3


class _WebSocketClosed(ProtocolError):
    def __init__(self, code: int | None) -> None:
        super().__init__("WebSocket closed")
        self.code = code


def websocket_events(
    self: Bot,
    *,
    stop: threading.Event | None = None,
    pause_wait: Callable[[threading.Event | None, float], bool],
    on_connect: Callable[[], object] | None = None,
) -> Generator[UpdateEvent, None, None]:
    """Yield live frames until disconnected; use get_updates to recover gaps.

    Close codes 4001/4003 refresh an available session and reconnect, at most
    three times with delays of 1, 2 and 4 seconds. Other failures propagate.
    ``stop`` interrupts receiving and backoff; an active HTTP refresh waits
    for the configured HTTP timeout. Use polling to recover event gaps.
    """
    self._require_websocket()
    for attempt in range(_WS_AUTH_RETRIES + 1):
        access_token, generation = self._access_state()
        try:
            if on_connect is None:
                yield from self._websocket_events_once(access_token, stop=stop)
            else:
                yield from _websocket_events_once(
                    self,
                    access_token,
                    stop=stop,
                    monotonic=time.monotonic,
                    on_connect=on_connect,
                )
            return
        except _WebSocketClosed as exc:
            if (
                exc.code not in {4001, 4003}
                or self._refresh_session is None
                or attempt >= _WS_AUTH_RETRIES
            ):
                raise
            _log.warning(
                "WebSocket authorization closed (code %d); retry %d of %d",
                exc.code,
                attempt + 1,
                _WS_AUTH_RETRIES,
            )
            if (stop is not None and stop.is_set()) or pause_wait(stop, 2.0**attempt):
                return
            self._refresh_after_401(access_token, generation)


def _websocket_events_once(
    self: Bot,
    access_token: str,
    *,
    stop: threading.Event | None,
    monotonic: Callable[[], float],
    on_connect: Callable[[], object] | None = None,
) -> Iterator[UpdateEvent]:
    import websocket

    parts = urlsplit(self.base_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    url = f"{scheme}://{parts.netloc}{parts.path}/api/v1/messenger/ws?capabilities=chat_settings_v2"
    self._rate_limits.acquire("GET", "/api/v1/messenger/ws")
    try:
        conn = websocket.create_connection(
            url,
            timeout=5,
            redirect_limit=0,
            subprotocols=["itd.messenger.v1", "bearer." + access_token],
            origin=f"{parts.scheme}://{parts.netloc}",
        )
    except websocket.WebSocketBadStatusException as exc:
        if exc.status_code == 429:
            self._rate_limits.observe(
                httpx.Response(
                    429,
                    headers=exc.resp_headers or {},
                    content=exc.resp_body or b"",
                ),
                "GET",
                "/api/v1/messenger/ws",
            )
        raise
    try:
        handshake = conn.handshake_response
        if handshake is None or handshake.status != 101:
            raise ProtocolError("WebSocket handshake failed")
        if on_connect is not None:
            on_connect()
        last_ping = last_frame = monotonic()
        while stop is None or not stop.is_set():
            now = monotonic()
            if now - last_frame >= 15:
                raise ProtocolError("WebSocket heartbeat timed out")
            if now - last_ping >= 5:
                conn.send("ping")
                last_ping = now
            timeout = min(5, max(0.1, 5 - (now - last_ping)))
            conn.settimeout(timeout if stop is None else min(timeout, 1.0))
            try:
                receive = getattr(conn, "recv_data", None)
                if callable(receive):
                    opcode, frame = receive(control_frame=True)
                    if opcode == websocket.ABNF.OPCODE_CLOSE:
                        code = (
                            int.from_bytes(frame[:2], "big")
                            if isinstance(frame, (bytes, bytearray)) and len(frame) >= 2
                            else None
                        )
                        raise _WebSocketClosed(code)
                    if opcode in {
                        websocket.ABNF.OPCODE_PING,
                        websocket.ABNF.OPCODE_PONG,
                    }:
                        last_frame = monotonic()
                        continue
                else:
                    frame = conn.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if frame in (None, "", b""):
                raise ProtocolError("WebSocket closed")
            last_frame = monotonic()
            if frame in ("pong", b"pong"):
                continue
            try:
                event = jsonlib.loads(frame)
            except (ValueError, UnicodeDecodeError, TypeError) as exc:
                raise ProtocolError("Invalid WebSocket frame") from exc
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise ProtocolError("Invalid WebSocket event")
            yield cast(UpdateEvent, event)
    finally:
        conn.close()


def _require_websocket() -> None:
    try:
        import websocket  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            'Install the websocket extra: pip install "itd-chatbot-sdk[websocket]"'
        ) from exc


def _websocket_wakeups(self: Bot, wake: threading.Event, stop: threading.Event) -> None:
    backoff = 1.0
    while not stop.is_set():
        try:
            for _ in self.websocket_events(stop=stop):
                wake.set()
                backoff = 1.0
        except Exception as exc:
            _log.warning("WebSocket wake-up stream failed: %s", type(exc).__name__)
        # A reconnect can miss frames, so let /updates look for a gap.
        wake.set()
        if stop.wait(backoff):
            return
        backoff = min(backoff * 2, 30.0)
