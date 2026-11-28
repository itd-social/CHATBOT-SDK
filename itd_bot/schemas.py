"""Type hints for the JSON that the SDK reads or returns.

Every class here is a ``TypedDict`` with ``total=False``: no key is promised.
They are used only in annotations; methods still return plain ``dict`` objects
and nothing is validated. The fields come from the ITD client bundle, from what
the SDK itself reads and from the tests. This is not a server specification.
"""

from __future__ import annotations

from typing import Any, TypedDict


class ChatPayload(TypedDict, total=False):
    """Chat fields read by the client bundle; not a specification."""

    chat_id: str
    chat_type: int
    other_user_id: str | None
    unread_count: int
    is_pinned: bool
    is_muted: bool


class ChatsResponse(TypedDict, total=False):
    """A chat page and its event cursor; not a specification."""

    chats: list[ChatPayload]
    event_seq: int


class AttachmentPayload(TypedDict, total=False):
    """A message attachment.

    Based on the client bundle and tests, this is not a specification.
    """

    fileId: str
    filename: str
    mime: str
    sizePlain: int


class StickerEntity(TypedDict, total=False):
    """Read-only sticker fields; not a specification.

    The client validator requires a string sticker_id, at most 64 UTF-16 units.
    Like other raw-JSON views, this hint promises no keys and validates nothing.
    """

    type: str
    sticker_id: str


class MessagePayload(TypedDict, total=False):
    """A message inside an event.

    Based on the client bundle and tests, this is not a specification.
    """

    message_id: str
    sender_id: str
    text: str | None
    attachments: list[AttachmentPayload]
    entities: list[Any]


class MessagesResponse(TypedDict, total=False):
    """Message history fields consumed by the client; not a specification."""

    messages: list[MessagePayload]


class UpdateEvent(TypedDict, total=False):
    """One event from ``/updates``.

    Based on the client bundle and tests, this is not a specification. Other
    event types carry more fields; the SDK does not read them. ``reactions``
    has no known shape and stays ``Any``.
    """

    type: str
    event_seq: int
    chat_id: str
    message: MessagePayload
    messages: list[MessagePayload]
    message_id: str
    reactions: Any


class UpdatesResponse(TypedDict, total=False):
    """The answer of ``Bot.get_updates``.

    Based on the client bundle and tests, this is not a specification.
    ``reset_required`` is always ``False`` here: ``get_updates`` raises
    ``ResetRequired`` instead of returning it as true.
    """

    events: list[UpdateEvent]
    reset_required: bool
    next_event_seq: int


class ReactionValue(TypedDict, total=False):
    """One reaction, as the SDK builds it for an emoji.

    Based on the client bundle and tests, this is not a specification.
    """

    type: str
    emoji: str


class ReactionEntry(TypedDict, total=False):
    """A reaction of a message and whether this account chose it.

    Based on the client bundle and tests, this is not a specification.
    """

    reaction: ReactionValue
    chosen: bool


class ReactionsResponse(TypedDict, total=False):
    """The answer of ``Bot.get_reactions``.

    Based on the client bundle and tests, this is not a specification.
    """

    reaction_version: str
    reactions: list[ReactionEntry]


class SendResponse(TypedDict, total=False):
    """The answer after sending a message.

    Based on the client bundle and tests, this is not a specification.
    ``replayed`` is kept when supplied by the server, never inferred locally.
    """

    message_id: str
    replayed: bool


class FileLink(TypedDict, total=False):
    """One entry of the file-links answer.

    Based on the client bundle and tests, this is not a specification.
    """

    fileId: str
    url: str


class FileLinksResponse(TypedDict, total=False):
    """The answer of ``Bot.get_file_links``.

    Based on the client bundle and tests, this is not a specification.
    """

    links: list[FileLink]


class UploadInitResponse(TypedDict, total=False):
    """The answer of the upload init request (internal step of ``upload_file``).

    Based on the client bundle and tests, this is not a specification.
    """

    uploadId: str


class UploadFinalizeResponse(TypedDict, total=False):
    """The answer of the upload finalize request, returned by ``upload_file``.

    Based on the client bundle and tests, this is not a specification.
    """

    fileId: str
