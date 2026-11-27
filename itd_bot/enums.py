"""Optional views of raw values; responses are never converted to enums."""

from __future__ import annotations

from enum import Enum, IntEnum


class ChatType(IntEnum):
    """Known chat types. Unknown integers keep their value and name UNKNOWN."""

    UNKNOWN = -1
    DIRECT = 0

    @classmethod
    def _missing_(cls, value: object) -> ChatType | None:
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        member = int.__new__(cls, value)
        member._name_ = "UNKNOWN"
        member._value_ = value
        return member


class EntityType(str, Enum):
    """Formatting entity types; unknown strings keep their original value."""

    UNKNOWN = "unknown"
    BOLD = "bold"
    ITALIC = "italic"
    UNDERLINE = "underline"
    STRIKE = "strike"
    CODE = "code"
    SPOILER = "spoiler"
    TEXT_LINK = "text_link"
    STICKER = "sticker"

    def __str__(self) -> str:
        return self.value

    @classmethod
    def _missing_(cls, value: object) -> EntityType | None:
        if not isinstance(value, str):
            return None
        member = str.__new__(cls, value)
        member._name_ = "UNKNOWN"
        member._value_ = value
        return member
