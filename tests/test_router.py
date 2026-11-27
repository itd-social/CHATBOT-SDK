"""Router behavior through raw events and MockTransport; no real accounts."""

import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from helpers import Transport, jwt

from itd_bot import (
    APIError,
    CommandContext,
    ErrorAction,
    EventContext,
    MessageContext,
    RateLimitError,
    ResetRequired,
    Router,
    filters,
)


def event(number=1, *, chat="chat-1", sender="user-1", text="hello"):
    return {
        "type": "new_message",
        "event_seq": number,
        "chat_id": chat,
        "message": {"message_id": str(number), "sender_id": sender, "text": text},
    }


def make(**options):
    transport = Transport(default=httpx.Response(200, json={"message_id": "sent-1"}))
    settings = {"auto_typing": False, "read_mode": "off", **options}
    return Router(transport.bot(), self_user_id="self", **settings), transport


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("first", ["priority"]),
        ("all", ["priority", "one", "child", "last"]),
    ],
)
def test_first_and_all_are_explicit_and_order_is_stable(mode, expected):
    root, _ = make(match=mode)
    child = Router()
    seen = []
    root.message()(lambda ctx: seen.append("one"))
    child.message()(lambda ctx: seen.append("child"))
    root.include_router(child)
    root.message()(lambda ctx: seen.append("last"))
    root.message(order=-1)(lambda ctx: seen.append("priority"))
    root.handle(event())
    assert seen == expected


def test_filters_combine_short_circuit_and_preserve_command_context():
    root, _ = make()
    seen, calls = [], []
    condition = (
        filters.command("/START") & filters.chat("chat-1") & ~filters.user("blocked")
    )
    root.message(condition)(lambda ctx: seen.append((type(ctx), ctx.command, ctx.args)))
    root.message(filters.text("fallback") | filters.regex(re.compile("^he.+o$")))(
        lambda ctx: seen.append(type(ctx))
    )
    root.message(
        filters.text("no"), lambda ctx: calls.append(ctx) or True, filters.text("never")
    )(lambda ctx: pytest.fail("unmatched"))
    root.handle(event(text="/start  value "))
    root.handle(event(2, text="hello"))
    root.handle(event(3, text="fallback"))
    root.handle(event(4, text="unmatched"))
    assert seen == [(CommandContext, "start", "value"), MessageContext, MessageContext]
    assert calls == []


def test_or_not_and_direct_filter_calls_do_not_leak_command_metadata():
    root, _ = make(match="all")
    seen, evaluated = [], []
    root.message(
        filters.text("hello")
        | filters.predicate(lambda ctx: evaluated.append(1) or True)
    )(lambda ctx: seen.append(type(ctx)))
    root.message(~filters.command("start"))(lambda ctx: seen.append(type(ctx)))
    root.message(
        filters.command("no")
        & filters.predicate(lambda ctx: evaluated.append(2) or True)
    )(lambda ctx: pytest.fail("and"))
    root.handle(event())
    assert seen == [MessageContext, MessageContext] and evaluated == []
    context = MessageContext(root._bound().bot, event(), event()["message"])
    assert filters.text("hello")(context)
    assert not filters.command("start")(context)
    assert not (~filters.text("hello"))(context)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("!go value", "value"),
        ("!GO@SiteBot  words  ", "words"),
        ("!go@other value", None),
        ("!gone", None),
        ("before !go", None),
        (None, None),
        (17, None),
    ],
)
def test_command_prefix_target_and_non_text_values(text, expected):
    root, _ = make(username="@SiteBot")
    seen = []
    root.message(filters.command("go", prefix="!"))(lambda ctx: seen.append(ctx.args))
    root.handle(event(text=text))
    assert seen == ([] if expected is None else [expected])


def test_addressed_command_requires_known_username():
    root, _ = make()
    root.message(filters.command("go"))(lambda ctx: pytest.fail("unknown username"))
    root.handle(event(text="/go@someone"))


def test_non_message_context_filters_and_unknown_raw_fields():
    root, _ = make(match="all")
    seen = []
    raw = {
        "type": "presence",
        "chat_id": "chat-1",
        "user_id": "user-1",
        "future": {"x": 1},
    }
    root.on_event(
        "presence",
        filters.event_type("presence"),
        filters.user("user-1"),
        filters.chat("chat-1"),
    )(lambda ctx: seen.append(ctx))
    root.on_event(
        "presence",
        filters.text("hello") | filters.regex("hello") | filters.command("go"),
    )(lambda ctx: pytest.fail("not a message"))
    root.on_event("other")(lambda ctx: pytest.fail("wrong type"))
    root.message()(lambda ctx: pytest.fail("message route"))
    root.handle(raw)
    assert len(seen) == 1 and isinstance(seen[0], EventContext)
    assert seen[0].event is raw and seen[0].event["future"] == {"x": 1}


def test_event_without_a_chat_uses_no_typing():
    root, transport = make(auto_typing=True, typing_delay=0)
    seen = []
    root.on_event("future_event")(seen.append)
    root.handle({"type": "future_event", "future": True})
    assert len(seen) == 1 and not transport.requests


def test_middleware_nests_and_handler_stop_survives_a_missing_return():
    root, _ = make(match="all")
    child, grandchild = Router(), Router()
    trace = []

    def wrap(name):
        def middleware(next_handler, ctx):
            trace.append(name + " before")
            try:
                next_handler(ctx)
            finally:
                trace.append(name + " after")

        return middleware

    root.middleware(wrap("root"))
    child.middleware(wrap("child"))
    grandchild.middleware(wrap("grandchild"))
    child.include_router(grandchild)
    root.include_router(child)

    @grandchild.message()
    def first(ctx):
        trace.append("handler")
        return Router.STOP

    root.message()(lambda ctx: pytest.fail("STOP was swallowed"))
    root.handle(event())
    assert trace == [
        "root before",
        "child before",
        "grandchild before",
        "handler",
        "grandchild after",
        "child after",
        "root after",
    ]


def test_middleware_can_skip_a_handler_or_stop_the_message():
    root, _ = make(match="all")
    child = Router()
    child.middleware(lambda next_handler, ctx: None)
    child.message()(lambda ctx: pytest.fail("skipped"))
    root.include_router(child)
    seen = []
    root.message()(lambda ctx: seen.append(ctx.message_id))
    root.handle(event())
    assert seen == ["1"]
    stopped, _ = make(match="all")
    stopped.middleware(lambda next_handler, ctx: Router.STOP)
    stopped.message()(lambda ctx: pytest.fail("middleware stop"))
    stopped.handle(event())


def test_next_handler_cannot_accidentally_execute_a_write_twice():
    root, transport = make()

    def middleware(next_handler, ctx):
        next_handler(ctx)
        next_handler(ctx)

    root.middleware(middleware)
    root.message()(lambda ctx: ctx.reply("fictional reply"))
    with pytest.raises(RuntimeError, match="at most once"):
        root.handle(event())
    assert len(transport.requests) == 1


def test_packet_matching_stop_and_dedup_are_per_message():
    root, _ = make(match="all")
    seen = []
    root.message()(lambda ctx: seen.append(ctx.message_id) or Router.STOP)
    root.message()(lambda ctx: pytest.fail("second handler"))
    raw = {
        "type": "new_messages",
        "chat_id": "chat-1",
        "messages": [event()["message"], event(2)["message"]],
        "future": {"data": 1},
    }
    root.handle(raw)
    root.handle(raw)
    root.handle(event(3, sender="self"))
    assert seen == ["1", "2"]


def test_read_and_typing_are_once_for_all_handlers_and_duplicates():
    root, transport = make(
        match="all", read_mode="blocking", auto_typing=True, typing_delay=0
    )
    seen = []
    root.message()(lambda ctx: seen.append("first"))
    root.message()(lambda ctx: seen.append("second"))
    root.handle(event())
    root.handle(event())
    assert seen == ["first", "second"]
    assert [r.url.path.rsplit("/", 1)[-1] for r in transport.requests] == [
        "read",
        "action",
    ]


def test_failure_releases_claim_and_middleware_finally_runs():
    root, _ = make()
    trace = []

    def middleware(next_handler, ctx):
        try:
            return next_handler(ctx)
        finally:
            trace.append("finally")

    root.middleware(middleware)

    @root.message()
    def fail_once(ctx):
        if not trace:
            raise RuntimeError("fictional failure")
        trace.append("handled")

    with pytest.raises(RuntimeError):
        root.handle(event())
    root.handle(event())
    assert trace == ["finally", "handled", "finally"]


def test_concurrent_delivery_claims_one_message_once():
    root, _ = make()
    entered, release = threading.Event(), threading.Event()
    seen = []

    @root.message()
    def slow(ctx):
        seen.append(ctx.message_id)
        entered.set()
        assert release.wait(2)

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(root.handle, event())
        try:
            assert entered.wait(2)
            second = pool.submit(root.handle, event())
        finally:
            release.set()
        first.result(2)
        second.result(2)
    assert seen == ["1"]


@pytest.mark.parametrize(
    "operation", ["handler", "middleware", "include", "child", "error"]
)
def test_configuration_freezes_for_the_whole_tree(operation):
    root, _ = make()
    child = Router()
    root.include_router(child)
    root.handle(event())
    with pytest.raises(RuntimeError, match="before"):
        if operation == "handler":
            root.message()(lambda ctx: None)
        elif operation == "middleware":
            root.middleware(lambda next_handler, ctx: None)
        elif operation == "include":
            root.include_router(Router())
        elif operation == "child":
            child.message()(lambda ctx: None)
        else:
            root.on_error(lambda exc, raw: None)


def test_invalid_graphs_and_bound_children_are_rejected():
    first, second = Router(), Router()
    first.include_router(second)
    with pytest.raises(ValueError, match="cycle"):
        second.include_router(first)
    with pytest.raises(ValueError, match="cycle"):
        first.include_router(first)
    with pytest.raises(ValueError, match="already"):
        first.include_router(second)
    root, _ = make()
    with pytest.raises(ValueError, match="unbound"):
        first.include_router(root)
    with pytest.raises(TypeError):
        first.include_router("wrong")
    with pytest.raises(ValueError, match="bound"):
        second.handle(event())
    with Router() as child:
        child.close()


@pytest.mark.parametrize(
    "options",
    [
        {"match": "wrong"},
        {"match": None},
        {"match": []},
        {"match": "all"},
        {"self_user_id": "self"},
        {"read_mode": "off"},
    ],
)
def test_unbound_and_matching_options_are_checked(options):
    with pytest.raises(ValueError):
        Router(**options)


@pytest.mark.parametrize("identity", [None, "", 0, True])
def test_bound_roots_require_a_string_self_identity(identity):
    with pytest.raises(ValueError):
        Router(Transport().bot(), self_user_id=identity)


@pytest.mark.parametrize("kind", ["", "new_message", "new_messages", None])
def test_incoming_events_use_message_registration(kind):
    with pytest.raises(ValueError):
        Router().on_event(kind)


@pytest.mark.parametrize("order", [True, None, "1", 0.5])
def test_order_requires_an_integer(order):
    with pytest.raises(ValueError):
        Router().message(order=order)


@pytest.mark.parametrize(
    "factory,value",
    [
        (filters.chat, ""),
        (filters.user, None),
        (filters.event_type, ""),
        (filters.text, 17),
        (filters.command, ""),
        (filters.command, "bad name"),
    ],
)
def test_filter_arguments_are_checked(factory, value):
    with pytest.raises(ValueError):
        factory(value)


def test_invalid_prefix_pattern_and_combinations():
    with pytest.raises(ValueError):
        filters.command("start", prefix="")
    with pytest.raises(re.error):
        filters.regex("[")
    with pytest.raises(TypeError):
        filters.text("x") & True
    with pytest.raises(TypeError):
        filters.text("x") | False
    with pytest.raises(TypeError):
        filters.predicate(17)
    with pytest.raises(TypeError):
        Router().message()(17)
    with pytest.raises(TypeError):
        Router().middleware(17)


def test_async_callbacks_are_rejected_before_they_can_be_called():
    async def function(ctx):
        return True

    async def generator(ctx):
        yield True

    class AsyncCall:
        async def __call__(self, ctx):
            return True

    class AsyncGenerator:
        async def __call__(self, ctx):
            yield True

    for callback in [function, generator, AsyncCall(), AsyncGenerator()]:
        with pytest.raises(TypeError, match="synchronous"):
            filters.predicate(callback)
        with pytest.raises(TypeError, match="synchronous"):
            Router().message()(callback)


@pytest.mark.parametrize(
    "override,target", [({}, "SiteBot"), ({"username": "custom"}, "custom")]
)
def test_from_bot_uses_profile_and_explicit_username_override(override, target):
    transport = Transport(
        httpx.Response(200, json={"id": "self", "username": "SiteBot"})
    )
    with Router.from_bot(
        transport.bot(), read_mode="off", auto_typing=False, **override
    ) as root:
        seen = []
        root.message(filters.command("start"))(lambda ctx: seen.append(ctx.command))
        root.handle(event(text="/start@" + target))
        assert seen == ["start"]
    assert transport.paths == ["/api/users/me"]


@pytest.mark.parametrize("bad", [{}, {"type": []}, {"type": ""}])
def test_bad_manual_event_types_raise(bad):
    root, _ = make()
    with pytest.raises(ValueError):
        root.handle(bad)


@pytest.mark.parametrize("queued", [False, True])
def test_polling_batch_reads_checkpoint_and_error_hook_keep_existing_contract(queued):
    stop, checkpoints, handled, failures = threading.Event(), [], [], []
    items = [event(1), event(2)]

    def answer(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "events": items if request.url.params["since"] == "0" else [],
                    "next_event_seq": 2,
                    "reset_required": False,
                },
            )
        return httpx.Response(200, json={})

    transport = Transport(default=answer)
    root = Router(
        transport.bot(), self_user_id="self", auto_typing=False, read_mode="batch"
    )

    @root.on_error()
    def error(exc, raw):
        failures.append(raw["event_seq"])
        return ErrorAction.SKIP

    @root.message()
    def handle(ctx):
        handled.append(ctx.message_id)
        if ctx.message_id == "1":
            raise RuntimeError("fictional failure")
        stop.set()

    root.run_polling(
        0,
        handle_signals=False,
        stop=stop,
        chat_queues=queued,
        interval=0.001,
        on_checkpoint=checkpoints.append,
    )
    assert handled == ["1", "2"] and failures == [1] and checkpoints[-1] == 2
    reads = [
        json.loads(r.content)
        for r in transport.requests
        if r.url.path.endswith("/read")
    ]
    assert reads == [{"last_read_message": "2"}]


def test_reset_required_is_never_hidden_by_router():
    transport = Transport(httpx.Response(200, json={"reset_required": True}))
    root = Router(transport.bot(), self_user_id="self", auto_typing=False)
    with pytest.raises(ResetRequired):
        root.run_polling(0, handle_signals=False)


@pytest.mark.parametrize(
    "failure,expected",
    [
        (httpx.ReadError("fictional failure"), httpx.ReadError),
        (httpx.Response(429, json={}), RateLimitError),
        (httpx.Response(403, json={}), APIError),
        (httpx.Response(401, json={}), APIError),
    ],
)
def test_read_failure_prevents_handlers(failure, expected):
    transport = Transport(failure)
    root = Router(transport.bot(), self_user_id="self", auto_typing=False)
    root.message()(lambda ctx: pytest.fail("read failed"))
    with pytest.raises(expected):
        root.handle(event())
    assert len(transport.requests) == 1


def test_context_actions_and_packet_raw_fields_keep_existing_requests():
    def answer(request):
        if request.method == "GET":
            return httpx.Response(200, json={"reaction_version": "v1", "reactions": []})
        return httpx.Response(200, json={"message_id": "sent-1"})

    transport = Transport(default=answer)
    root = Router(
        transport.bot(), self_user_id="self", read_mode="off", auto_typing=False
    )
    raw = {
        "type": "new_messages",
        "chat_id": "chat-1",
        "messages": [event()["message"]],
        "future": True,
    }
    raw["messages"][0]["future"] = {"field": 1}

    @root.message()
    def actions(ctx):
        assert ctx.event is raw and ctx.message is raw["messages"][0]
        assert ctx.message["future"] == {"field": 1}
        assert ctx.reply("fictional reply")["message_id"] == "sent-1"
        ctx.react("👍")
        ctx.forward_to("chat-2")

    root.handle(raw)
    requests = transport.requests
    assert [r.method for r in requests] == ["POST", "GET", "PUT", "POST"]
    reply, _, reaction, forward = requests
    assert json.loads(reply.content)["reply_to_id"] == "1"
    reaction_body = json.loads(reaction.content)
    assert reaction_body["expected_version"] == "v1"
    assert reaction_body["reactions"] == [{"type": "emoji", "emoji": "👍"}]
    assert isinstance(reaction_body["operation_id"], str)
    assert forward.url.path.endswith("/chats/chat-2/messages/forward")
    assert json.loads(forward.content) == {
        "source_chat_id": "chat-1",
        "message_ids": ["1"],
    }


@pytest.mark.parametrize(
    "failure,expected",
    [
        (httpx.ReadError("fictional failure"), httpx.ReadError),
        (httpx.Response(401, json={}), APIError),
        (httpx.Response(403, json={}), APIError),
        (httpx.Response(429, json={}), RateLimitError),
    ],
)
def test_failed_context_write_is_not_retried(failure, expected):
    transport = Transport(failure)
    root = Router(
        transport.bot(), self_user_id="self", read_mode="off", auto_typing=False
    )
    root.message()(lambda ctx: ctx.reply("fictional reply"))
    with pytest.raises(expected):
        root.handle(event())
    assert len(transport.requests) == 1 and transport.requests[0].method == "POST"


@pytest.mark.parametrize("stage", ["filter", "middleware", "handler"])
def test_route_errors_reach_poll_policy_without_checkpoint_or_payload_log(
    stage, caplog
):
    raw = event(text="fictional private text")
    transport = Transport(
        httpx.Response(
            200, json={"events": [raw], "next_event_seq": 1, "reset_required": False}
        )
    )
    root = Router(
        transport.bot(), self_user_id="self", read_mode="off", auto_typing=False
    )
    failures, checkpoints = [], []

    def fail(*args):
        raise RuntimeError("fictional private error")

    if stage == "filter":
        root.message(fail)(lambda ctx: pytest.fail("filter failed"))
    elif stage == "middleware":
        root.middleware(fail)
        root.message()(lambda ctx: pytest.fail("middleware failed"))
    else:
        root.message()(fail)

    @root.on_error
    def error(exc, data):
        failures.append((type(exc), data))
        return ErrorAction.STOP

    with pytest.raises(RuntimeError, match="fictional private error"):
        root.run_polling(0, handle_signals=False, on_checkpoint=checkpoints.append)
    assert failures == [(RuntimeError, raw)] and not checkpoints
    assert "fictional private" not in caplog.text
    assert len(transport.requests) == 1


def test_retry_repeats_partial_chain_only_when_explicitly_requested():
    raw = event()
    transport = Transport(
        default=httpx.Response(
            200, json={"events": [raw], "next_event_seq": 1, "reset_required": False}
        )
    )
    root = Router(
        transport.bot(),
        self_user_id="self",
        match="all",
        read_mode="off",
        auto_typing=False,
    )
    trace, checkpoints = [], []
    stop = threading.Event()
    root.message()(lambda ctx: trace.append("first"))

    @root.message()
    def second(ctx):
        trace.append("second")
        if trace.count("second") == 1:
            raise RuntimeError("fictional failure")
        stop.set()

    root.on_error(lambda exc, data: ErrorAction.RETRY)
    root.run_polling(
        0,
        stop=stop,
        interval=0.001,
        handle_signals=False,
        on_checkpoint=checkpoints.append,
    )
    assert trace == ["first", "second", "first", "second"]
    assert checkpoints == [1]
    assert all(request.method == "GET" for request in transport.requests)


def test_async_error_hook_is_rejected():
    root, _ = make()

    async def error(exc, raw):
        return ErrorAction.SKIP

    with pytest.raises(TypeError, match="synchronous"):
        root.on_error(error)


def test_queued_polling_preserves_chat_order_and_persists_cursor(tmp_path):
    released, stop = threading.Event(), threading.Event()
    trace = []
    lock = threading.Lock()
    packet = [event(1), event(2), event(3, chat="chat-2")]

    def answer(request):
        return httpx.Response(
            200,
            json={
                "events": packet if request.url.params["since"] == "0" else [],
                "next_event_seq": 3,
                "reset_required": False,
            },
        )

    transport = Transport(default=answer)
    root = Router(
        transport.bot(), self_user_id="self", read_mode="off", auto_typing=False
    )

    @root.message()
    def handle(ctx):
        if ctx.message_id == "1":
            assert released.wait(2), "another chat should run independently"
        with lock:
            trace.append(ctx.message_id)
        if ctx.message_id == "3":
            released.set()
        if ctx.message_id == "2":
            stop.set()

    path = tmp_path / "state.json"
    root.run_polling(
        0,
        state_path=path,
        stop=stop,
        workers=2,
        chat_queues=True,
        handle_signals=False,
        interval=0.001,
    )
    assert trace == ["3", "1", "2"]
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["cursor"] == 3 and state["user_id"] == "self"


def test_checked_router_callbacks_keep_context_and_return_types(tmp_path):
    pytest.importorskip("mypy")
    code = """from collections.abc import Callable
from itd_bot import Bot, CommandContext, EventContext, MessageContext, Router, filters

def configure(bot: Bot) -> None:
    root = Router.from_bot(bot, match="all")
    child = Router()
    root.include_router(child)

    @child.message(filters.command("start") & ~filters.user("blocked"))
    def start(ctx: CommandContext) -> str:
        return ctx.args

    @root.message(lambda ctx: ctx.text is not None)
    def message(ctx: MessageContext) -> str | None:
        return ctx.text

    @root.on_event("presence", filters.predicate(lambda ctx: ctx.chat_id == "chat-1"))
    def presence(ctx: EventContext) -> str | None:
        return ctx.chat_id

    @root.middleware
    def wrap(next_handler: Callable[[MessageContext | EventContext], object], ctx: MessageContext | EventContext) -> object:
        return next_handler(ctx)

    command_callback: Callable[[CommandContext], str] = start
    message_callback: Callable[[MessageContext], str | None] = message
    event_callback: Callable[[EventContext], str | None] = presence
"""
    caller = tmp_path / "router_caller.py"
    caller.write_text(code, encoding="utf-8")
    arguments = [
        sys.executable,
        "-m",
        "mypy",
        "--strict",
        "--python-version",
        "3.10",
        "--cache-dir",
        str(tmp_path / "cache"),
        str(caller),
    ]
    options = dict(
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    positive = subprocess.run(arguments, **options)
    assert positive.returncode == 0, positive.stdout + positive.stderr
    caller.write_text(
        code + "\n    wrong: Callable[[CommandContext], int] = start\n",
        encoding="utf-8",
    )
    negative = subprocess.run(arguments, **options)
    assert negative.returncode == 1 and "assignment" in negative.stdout


def test_read_refreshes_401_before_filter_and_handler_and_saves_rotation(tmp_path):
    transport = Transport(
        httpx.Response(401, json={}),
        httpx.Response(
            200,
            json={"accessToken": jwt("self")},
            headers={
                "set-cookie": "refresh_token=fictional-rotated; Path=/api/v1/auth; HttpOnly"
            },
        ),
        httpx.Response(200, json={}),
        httpx.Response(201, json={"message_id": "sent-1"}),
    )
    session = tmp_path / "session.json"
    bot = transport.bot(refresh_token="fictional-seed", session_path=session)
    root = Router(bot, self_user_id="self", auto_typing=False)

    def after_read(ctx):
        assert transport.paths[-1].endswith("/read") and len(transport.requests) == 3
        assert (
            json.loads(session.read_text(encoding="utf-8"))["refresh_token"]
            == "fictional-rotated"
        )
        return True

    root.message(after_read)(lambda ctx: ctx.reply("fictional reply"))
    root.handle(event())
    assert [r.url.path.rsplit("/", 1)[-1] for r in transport.requests] == [
        "read",
        "refresh",
        "read",
        "messages",
    ]
    assert transport.requests[-1].headers["authorization"] == "Bearer " + jwt("self")
