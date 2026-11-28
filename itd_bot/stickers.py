"""Read inactive sticker entities without validating or modifying raw JSON."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from .schemas import StickerEntity


def message_stickers(message: Mapping[str, Any]) -> list[StickerEntity]:
    """Select type=sticker dictionaries, retaining original objects and fields.

    Missing or malformed entities yield an empty list. Sticker IDs are not
    validated; incomplete/future data remains available to the handler.
    This does not enable sending, catalogues or fetching sticker assets.
    """
    entities = message.get("entities")
    if not isinstance(entities, list):
        return []
    return [
        cast(StickerEntity, entity)
        for entity in entities
        if isinstance(entity, dict) and entity.get("type") == "sticker"
    ]
