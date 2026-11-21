from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import ProtocolError

if TYPE_CHECKING:
    from .client import Bot
    from .user import User


@dataclass(frozen=True)
class Attachment:
    bot: Bot
    chat_id: str
    raw: dict[str, Any]

    @property
    def file_id(self) -> str | None:
        return self.raw.get("fileId")

    @property
    def filename(self) -> str | None:
        return self.raw.get("filename")

    @property
    def mime(self) -> str | None:
        return self.raw.get("mime")

    @property
    def size(self) -> int | None:
        return self.raw.get("sizePlain")

    def download(self, destination: str | Path | None = None) -> bytes | Path:
        if not isinstance(self.file_id, str) or not self.file_id:
            raise ProtocolError("Attachment has no fileId")
        return self.bot.download_file(
            self.chat_id, self.file_id, destination=destination
        )


@dataclass(frozen=True)
class SentMessage:
    bot: Bot
    chat_id: str
    raw: dict[str, Any]

    @property
    def message_id(self) -> str:
        value = self.raw.get("message_id")
        if not isinstance(value, str) or not value:
            raise ProtocolError("Send response has no message_id")
        return value

    def edit(self, text: str, **kwargs: Any) -> Any:
        return self.bot.edit_message(self.chat_id, self.message_id, text, **kwargs)

    def delete(self, *, for_everyone: bool = False) -> Any:
        return self.bot.delete_messages(
            self.chat_id, [self.message_id], for_everyone=for_everyone
        )


@dataclass(frozen=True)
class MessageContext:
    bot: Bot
    event: dict[str, Any]
    message: dict[str, Any]

    @property
    def chat_id(self) -> str:
        return self.event["chat_id"]

    @property
    def message_id(self) -> str:
        return self.message["message_id"]

    @property
    def sender_id(self) -> str:
        return self.message["sender_id"]

    @property
    def text(self) -> str | None:
        return self.message.get("text")

    @property
    def attachments(self) -> list[Attachment]:
        items = self.message.get("attachments") or []
        if not isinstance(items, list) or any(
            not isinstance(item, dict) for item in items
        ):
            raise ProtocolError("Invalid message attachments")
        return [Attachment(self.bot, self.chat_id, item) for item in items]

    @property
    def photo(self) -> list[Attachment]:
        return [
            item
            for item in self.attachments
            if isinstance(item.mime, str) and item.mime.startswith("image/")
        ]

    def get_sender(self) -> User:
        return self.bot.get_user(self.sender_id)

    def is_subscribed(self) -> bool | None:
        value = self.get_sender().is_followed_by
        return value if isinstance(value, bool) else None

    def reply(self, text: str, **kwargs: Any) -> Any:
        return self.bot.send_message(
            self.chat_id, text, reply_to_id=self.message_id, **kwargs
        )

    def reply_message(self, text: str, **kwargs: Any) -> SentMessage:
        result = self.reply(text, **kwargs)
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("message_id"), str)
            or not result["message_id"]
        ):
            raise ProtocolError("Send response has no message_id")
        return SentMessage(self.bot, self.chat_id, result)

    def reply_photo(self, source: Any, **kwargs: Any) -> Any:
        return self.bot.send_photo(
            self.chat_id, source, reply_to_id=self.message_id, **kwargs
        )

    def reply_video(self, source: Any, **kwargs: Any) -> Any:
        return self.bot.send_video(
            self.chat_id, source, reply_to_id=self.message_id, **kwargs
        )

    def reply_music(self, source: Any, **kwargs: Any) -> Any:
        return self.bot.send_music(
            self.chat_id, source, reply_to_id=self.message_id, **kwargs
        )

    def reply_file(self, source: Any, **kwargs: Any) -> Any:
        return self.bot.send_file(
            self.chat_id, source, reply_to_id=self.message_id, **kwargs
        )

    def reply_formatted(self, content: Any, **kwargs: Any) -> Any:
        return self.bot.send_formatted(
            self.chat_id, content, reply_to_id=self.message_id, **kwargs
        )

    def react(self, emoji: str) -> Any:
        return self.bot.add_reaction(self.chat_id, self.message_id, emoji)

    def remove_reaction(self, emoji: str) -> Any:
        return self.bot.remove_reaction(self.chat_id, self.message_id, emoji)

    def delete_message(self, *, for_everyone: bool = False) -> Any:
        return self.bot.delete_messages(
            self.chat_id, [self.message_id], for_everyone=for_everyone
        )

    def forward_to(self, chat_id: str) -> Any:
        return self.bot.forward_messages(chat_id, self.chat_id, [self.message_id])


@dataclass(frozen=True)
class EventContext:
    bot: Bot
    event: dict[str, Any]

    @property
    def chat_id(self) -> str | None:
        return self.event.get("chat_id")

    @property
    def message_id(self) -> str | None:
        return self.event.get("message_id")

    @property
    def reactions(self) -> Any:
        return self.event.get("reactions")
