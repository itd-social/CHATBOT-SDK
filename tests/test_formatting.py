import json

import httpx
import pytest

from itd_bot import Bot, Text


def test_formatting_utf16_offsets_and_send():
    content = (
        Text("Привет 👋 ").bold("мир").add(" ").link("сайт", "https://example.test")
    )
    text, entities = content.build()
    assert text == "Привет 👋 мир сайт"
    assert entities == [
        {"type": "bold", "offset": 10, "length": 3},
        {"type": "text_link", "offset": 14, "length": 4, "url": "https://example.test"},
    ]
    entities[0]["offset"] = 999

    def handler(req):
        body = json.loads(req.content)
        assert body["text"] == text
        assert body["entities"][0]["offset"] == 10
        return httpx.Response(200, json={"message_id": "sent"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert (
            Bot("token", base_url="https://example.test", client=client).send_formatted(
                "c", content
            )["message_id"]
            == "sent"
        )


def test_combined_styles_and_validation():
    text = Text().add("😀", styles=("bold", "italic")).code("test")
    assert text.build()[1] == [
        {"type": "bold", "offset": 0, "length": 2},
        {"type": "italic", "offset": 0, "length": 2},
        {"type": "code", "offset": 2, "length": 4},
    ]
    with pytest.raises(ValueError):
        Text().link("", "https://example.test")
    with pytest.raises(ValueError):
        Text().add("x", styles=("text_link",))
    with pytest.raises(ValueError):
        Text().add("x", styles=("invalid",))
