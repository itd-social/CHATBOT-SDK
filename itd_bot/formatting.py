from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_STYLES = {"bold", "italic", "underline", "strike", "code", "spoiler", "text_link"}


def utf16_length(text: str) -> int:
    """Count UTF-16 code units, as JavaScript String.length does."""
    return len(text.encode("utf-16-le")) // 2


class Text:
    """Build ITD message text and entities without parsing or escaping markup."""

    def __init__(self, text: str = ""):
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        self._parts = [text]
        self._length = utf16_length(text)
        self._entities: list[dict[str, Any]] = []

    def add(
        self, text: str, *, styles: Iterable[str] = (), url: str | None = None
    ) -> Text:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if isinstance(styles, str):
            raise TypeError("styles must be an iterable of style names, not a string")
        names = tuple(styles)
        if any(name not in _STYLES for name in names) or len(set(names)) != len(names):
            raise ValueError("invalid or duplicate formatting style")
        if ("text_link" in names) != (url is not None):
            raise ValueError("text_link requires a URL and URL requires text_link")
        if url is not None and (not isinstance(url, str) or not url):
            raise ValueError("link URL must be a non-empty string")
        length = utf16_length(text)
        if not length and names:
            raise ValueError("formatted text cannot be empty")
        if len(self._entities) + len(names) > 50:
            raise ValueError("at most 50 entities are supported by the web client")
        for name in names:
            entity = {"type": name, "offset": self._length, "length": length}
            if name == "text_link":
                entity["url"] = url
            self._entities.append(entity)
        self._parts.append(text)
        self._length += length
        return self

    def bold(self, text: str) -> Text:
        return self.add(text, styles=("bold",))

    def italic(self, text: str) -> Text:
        return self.add(text, styles=("italic",))

    def underline(self, text: str) -> Text:
        return self.add(text, styles=("underline",))

    def strike(self, text: str) -> Text:
        return self.add(text, styles=("strike",))

    def code(self, text: str) -> Text:
        return self.add(text, styles=("code",))

    def spoiler(self, text: str) -> Text:
        return self.add(text, styles=("spoiler",))

    def link(self, text: str, url: str) -> Text:
        return self.add(text, styles=("text_link",), url=url)

    def build(self) -> tuple[str, list[dict[str, Any]]]:
        return "".join(self._parts), [entity.copy() for entity in self._entities]
