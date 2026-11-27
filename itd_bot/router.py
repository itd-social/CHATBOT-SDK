"""Opt-in local routing on the existing Dispatcher delivery machinery."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, TypeVar, overload

from .client import Bot
from .context import EventContext, MessageContext
from .dispatcher import Dispatcher
from .filters import Context, Filter, _synchronous, predicate
from .polling import ErrorAction
from .schemas import MessagePayload, UpdateEvent

_Handler = TypeVar("_Handler", bound=Callable[..., Any])
_ErrorHandler = TypeVar(
    "_ErrorHandler", bound=Callable[[Exception, UpdateEvent], ErrorAction | None]
)
_Middleware = Callable[[Callable[[Context], Any], Any], Any]
_Condition = Filter | Callable[[Any], bool]
_GRAPH_LOCK = threading.RLock()


class _Propagation(Enum):
    STOP = "stop"


@dataclass(frozen=True)
class _Route:
    event_type: str | None
    conditions: tuple[Filter, ...]
    handler: Callable[..., Any]
    order: int


@dataclass(frozen=True)
class _Compiled:
    route: _Route
    middleware: tuple[_Middleware, ...]


class _RouterDispatcher(Dispatcher):
    def __init__(self, router: Router, bot: Bot, **options: Any) -> None:
        super().__init__(bot, **options)
        self._router = router

    def _handle_message(
        self,
        event: UpdateEvent,
        message: MessagePayload,
        read: Callable[[str, str], None],
    ) -> None:
        read(event["chat_id"], message["message_id"])
        self._router._route(MessageContext(self.bot, event, message), self)

    def _dispatch(self, event: UpdateEvent, read: Callable[[str, str], None]) -> None:
        typ = event.get("type")
        if not isinstance(typ, str) or not typ:
            raise ValueError("Invalid event type")
        if typ in {"new_message", "new_messages"}:
            super()._dispatch(event, read)
        else:
            self._router._route(EventContext(self.bot, event), self)


class Router:
    """Register synchronous handlers, filters, middleware and child routers.

    Configure before the first handle/run_polling call. A bound root controls
    first/all matching and Dispatcher options; children must be unbound Router().
    Registrations are frozen into an immutable snapshot before delivery starts.
    """

    STOP: Final = _Propagation.STOP

    def __init__(
        self,
        bot: Bot | None = None,
        *,
        self_user_id: str | None = None,
        match: str = "first",
        **dispatcher_options: Any,
    ) -> None:
        if not isinstance(match, str) or match not in {"first", "all"}:
            raise ValueError("match must be 'first' or 'all'")
        if bot is None:
            if self_user_id is not None or dispatcher_options or match != "first":
                raise ValueError(
                    "Configure Dispatcher options and match on a bound root"
                )
        elif not isinstance(self_user_id, str) or not self_user_id:
            raise ValueError("self_user_id is required to prevent self-replies")
        self._mode = match
        self._parent: Router | None = None
        self._items: list[_Route | Router] = []
        self._middleware: list[_Middleware] = []
        self._sealed = False
        self._compiled: tuple[_Compiled, ...] = ()
        self._dispatcher = (
            _RouterDispatcher(
                self, bot, self_user_id=self_user_id, **dispatcher_options
            )
            if bot is not None
            else None
        )

    @classmethod
    def from_bot(cls, bot: Bot, **kwargs: Any) -> Router:
        profile = bot.get_me()
        kwargs.setdefault("username", profile.username)
        return cls(bot, self_user_id=profile.id, **kwargs)

    def __enter__(self) -> Router:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _bound(self) -> _RouterDispatcher:
        if self._dispatcher is None:
            raise ValueError("Use a bound root Router to handle events or poll")
        return self._dispatcher

    def _mutable(self) -> None:
        if self._sealed:
            raise RuntimeError("Configure routers before handling events or polling")

    def include_router(self, router: Router) -> None:
        if not isinstance(router, Router):
            raise TypeError("include_router requires a Router")
        with _GRAPH_LOCK:
            self._mutable()
            router._mutable()
            if router._dispatcher is not None:
                raise ValueError("Include an unbound Router()")
            if router._parent is not None:
                raise ValueError("Router already included")
            ancestor: Router | None = self
            while ancestor is not None:
                if ancestor is router:
                    raise ValueError("Router inclusion would create a cycle")
                ancestor = ancestor._parent
            router._parent = self
            self._items.append(router)

    def _register(
        self,
        event_type: str | None,
        conditions: tuple[_Condition, ...],
        order: int,
    ) -> Callable[[_Handler], _Handler]:
        if isinstance(order, bool) or not isinstance(order, int):
            raise ValueError("order must be an integer")
        prepared = tuple(
            condition if isinstance(condition, Filter) else predicate(condition)
            for condition in conditions
        )

        def register(handler: _Handler) -> _Handler:
            _synchronous(handler)
            with _GRAPH_LOCK:
                self._mutable()
                self._items.append(_Route(event_type, prepared, handler, order))
            return handler

        return register

    def message(
        self,
        *conditions: _Condition,
        order: int = 0,
    ) -> Callable[[_Handler], _Handler]:
        return self._register(None, conditions, order)

    def on_event(
        self,
        event_type: str,
        *conditions: _Condition,
        order: int = 0,
    ) -> Callable[[_Handler], _Handler]:
        if (
            not isinstance(event_type, str)
            or not event_type
            or event_type
            in {
                "new_message",
                "new_messages",
            }
        ):
            raise ValueError(
                "Use message() for incoming messages and a non-empty event type"
            )
        return self._register(event_type, conditions, order)

    def middleware(self, function: _Handler) -> _Handler:
        _synchronous(function)
        with _GRAPH_LOCK:
            self._mutable()
            self._middleware.append(function)
        return function

    @overload
    def on_error(
        self, handler: None = None
    ) -> Callable[[_ErrorHandler], _ErrorHandler]: ...

    @overload
    def on_error(self, handler: _ErrorHandler) -> _ErrorHandler: ...

    def on_error(
        self,
        handler: Callable[[Exception, UpdateEvent], ErrorAction | None] | None = None,
    ) -> Callable[..., Any]:
        def register(function: _ErrorHandler) -> _ErrorHandler:
            _synchronous(function)
            with _GRAPH_LOCK:
                self._mutable()
                self._bound().on_error(function)
            return function

        return register if handler is None else register(handler)

    def _seal(self) -> None:
        with _GRAPH_LOCK:
            self._bound()
            if self._sealed:
                return
            compiled: list[_Compiled] = []

            def visit(router: Router, inherited: tuple[_Middleware, ...]) -> None:
                router._sealed = True
                middleware = inherited + tuple(router._middleware)
                for item in router._items:
                    if isinstance(item, Router):
                        visit(item, middleware)
                    else:
                        compiled.append(_Compiled(item, middleware))

            visit(self, ())
            self._compiled = tuple(
                sorted(compiled, key=lambda entry: entry.route.order)
            )

    def _invoke(self, entry: _Compiled, context: Context) -> bool:
        stopped = False

        def call(index: int, current: Context) -> Any:
            nonlocal stopped
            if index == len(entry.middleware):
                result = entry.route.handler(current)
            else:
                called = False

                def next_handler(next_context: Context) -> Any:
                    nonlocal called
                    if called:
                        raise RuntimeError(
                            "Middleware must call next_handler at most once"
                        )
                    called = True
                    return call(index + 1, next_context)

                result = entry.middleware[index](next_handler, current)
            if result is self.STOP:
                stopped = True
            return result

        call(0, context)
        return stopped

    def _route(self, context: Context, dispatcher: _RouterDispatcher) -> None:
        message = isinstance(context, MessageContext)
        entered = False
        with ExitStack() as stack:
            for entry in self._compiled:
                route = entry.route
                if (route.event_type is None) != message:
                    continue
                if not message and route.event_type != context.event.get("type"):
                    continue
                selected: Context | None = context
                for condition in route.conditions:
                    if selected is None:
                        break
                    selected = condition._match(selected, dispatcher.username)
                if selected is None:
                    continue
                if not entered:
                    chat_id = context.chat_id
                    if isinstance(chat_id, str) and chat_id:
                        stack.enter_context(dispatcher._typing(chat_id))
                    entered = True
                stopped = self._invoke(entry, selected)
                if stopped or self._mode == "first":
                    return

    def handle(self, event: UpdateEvent) -> None:
        self._seal()
        self._bound().handle(event)

    def run_polling(
        self,
        since: int | None = None,
        *,
        state_path: str | os.PathLike[Any] | None = None,
        handle_signals: bool = True,
        **kwargs: Any,
    ) -> None:
        self._seal()
        self._bound().run_polling(
            since, state_path=state_path, handle_signals=handle_signals, **kwargs
        )

    def close(self) -> None:
        if self._dispatcher is not None:
            self._dispatcher.close()
