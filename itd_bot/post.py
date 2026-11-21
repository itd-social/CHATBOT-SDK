from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import Bot


@dataclass(frozen=True)
class Post:
    """A post returned by the social API."""

    id: str
    raw: dict[str, Any]
    _bot: Bot = field(repr=False, compare=False)

    @property
    def content(self) -> str | None:
        return self.raw.get("content")

    @property
    def author(self) -> Any:
        return self.raw.get("author")

    @property
    def attachments(self) -> Any:
        return self.raw.get("attachments")

    def share(
        self, chat_id: str, *, text: str = "", reply_to_id: str | None = None
    ) -> dict:
        return self._bot.send_message(
            chat_id,
            text,
            reply_to_id=reply_to_id,
            entities=[{"type": "post", "post_id": self.id}],
        )
