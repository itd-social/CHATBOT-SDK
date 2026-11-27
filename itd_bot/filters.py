"""Composable local filters; no commands are registered with the server."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import Any

from .context import EventContext, MessageContext
from .dispatcher import CommandContext

Context = MessageContext | EventContext
_Matcher = Callable[[Context, str | None], Context | None]


def _synchronous(function: Callable[..., Any]) -> None:
    if not callable(function):
        raise TypeError("A synchronous callable is required")
    call = inspect.getattr_static(function, "__call__", None)
    if (
        inspect.iscoroutinefunction(function)
        or inspect.isasyncgenfunction(function)
        or inspect.iscoroutinefunction(call)
        or inspect.isasyncgenfunction(call)
    ):
        raise TypeError("Router callbacks must be synchronous")


def _nonempty(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("A non-empty string is required")


class Filter:
    """A filter built by the module factories; supports &, | and ~.

    Evaluation short-circuits. A positive command branch supplies CommandContext
    to following AND conditions and the handler. OR keeps the first match;
    negation keeps the original context.
    """

    def __init__(self, matcher: _Matcher) -> None:
        self._matcher = matcher

    def _match(self, context: Context, username: str | None) -> Context | None:
        return self._matcher(context, username)

    def __call__(self, context: Context) -> bool:
        """Evaluate without a bound username; addressed commands do not match."""
        return self._match(context, None) is not None

    def __and__(self, other: Filter) -> Filter:
        if not isinstance(other, Filter):
            raise TypeError("Combine Filter instances with &, | and ~")

        def match(context: Context, username: str | None) -> Context | None:
            first = self._match(context, username)
            return None if first is None else other._match(first, username)

        return Filter(match)

    def __or__(self, other: Filter) -> Filter:
        if not isinstance(other, Filter):
            raise TypeError("Combine Filter instances with &, | and ~")

        def match(context: Context, username: str | None) -> Context | None:
            first = self._match(context, username)
            return other._match(context, username) if first is None else first

        return Filter(match)

    def __invert__(self) -> Filter:
        def match(context: Context, username: str | None) -> Context | None:
            return context if self._match(context, username) is None else None

        return Filter(match)


def predicate(function: Callable[[Any], bool]) -> Filter:
    """Wrap a synchronous context predicate; exceptions reach on_error."""
    _synchronous(function)
    return Filter(lambda context, username: context if function(context) else None)


def event_type(value: str) -> Filter:
    _nonempty(value)
    return predicate(lambda context: context.event.get("type") == value)


def chat(chat_id: str) -> Filter:
    _nonempty(chat_id)
    return predicate(lambda context: context.chat_id == chat_id)


def user(user_id: str) -> Filter:
    _nonempty(user_id)
    return predicate(
        lambda context: (
            (
                context.sender_id
                if isinstance(context, MessageContext)
                else context.event.get("user_id")
            )
            == user_id
        )
    )


def text(value: str) -> Filter:
    if not isinstance(value, str):
        raise ValueError("text requires a string")
    return predicate(
        lambda context: isinstance(context, MessageContext) and context.text == value
    )


def regex(pattern: str | re.Pattern[str], flags: int = 0) -> Filter:
    compiled = re.compile(pattern, flags)
    return predicate(
        lambda context: (
            isinstance(context, MessageContext)
            and isinstance(context.text, str)
            and compiled.search(context.text) is not None
        )
    )


def command(name: str, prefix: str = "/") -> Filter:
    """Match a local command and supply the existing CommandContext."""
    _nonempty(prefix)
    _nonempty(name)
    name = name.removeprefix(prefix).lower()
    if re.fullmatch(r"[a-z0-9_]{1,32}", name) is None:
        raise ValueError("Invalid command name")
    pattern = re.compile(
        "^"
        + re.escape(prefix)
        + r"([A-Za-z0-9_]{1,32})(?:@([A-Za-z0-9_]{1,64}))?(?=\s|$)"
    )

    def match(context: Context, username: str | None) -> Context | None:
        if not isinstance(context, MessageContext) or not isinstance(context.text, str):
            return None
        found = pattern.match(context.text)
        if found is None or found.group(1).lower() != name:
            return None
        target = found.group(2)
        if target and (not username or target.lower() != username):
            return None
        return CommandContext(
            context.bot,
            context.event,
            context.message,
            name,
            context.text[found.end() :].strip(),
        )

    return Filter(match)
