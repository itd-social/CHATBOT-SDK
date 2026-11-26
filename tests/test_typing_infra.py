"""Typing marker and the send/edit ``entities`` contrast. All values are fictional."""

from __future__ import annotations

import json
from importlib import resources

import httpx
from helpers import Transport

OK = httpx.Response(200, json={"message_id": "m"})


def test_package_ships_the_py_typed_marker():
    marker = resources.files("itd_bot").joinpath("py.typed")
    assert marker.is_file()


def _sent_bodies(call):
    transport = Transport(default=OK)
    call(transport.bot())
    return [json.loads(request.content) for request in transport.requests]


def test_send_skips_empty_entities_but_edit_sends_them():
    # Intentional difference; the effect of [] on the live ITD is not verified.
    (sent,) = _sent_bodies(lambda b: b.send_message("chat", "hi", entities=[]))
    (edited,) = _sent_bodies(lambda b: b.edit_message("chat", "m", "hi", entities=[]))
    assert "entities" not in sent
    assert edited["entities"] == []


def test_edit_message_documents_the_entities_difference():
    from itd_bot import Bot

    assert "entities" in (Bot.edit_message.__doc__ or "")
