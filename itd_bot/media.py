from __future__ import annotations

import io
import os
from pathlib import Path


def prepare_photo(source: str | os.PathLike | bytes) -> tuple[bytes, str | None, dict]:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError(
            'Install image support: pip install "itd-chatbot-sdk[images]"'
        ) from exc
    original_name = None if isinstance(source, bytes) else Path(source).name
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    with Image.open(io.BytesIO(data)) as image:
        if image.format == "GIF":
            return data, original_name, {"width": image.width, "height": image.height}
        photo = ImageOps.exif_transpose(image)
        photo.thumbnail((1920, 1920))
        rgba = photo.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        output = io.BytesIO()
        background.save(output, format="JPEG", quality=70)
        filename = Path(original_name).stem + ".jpg" if original_name else "photo.jpg"
        return (
            output.getvalue(),
            filename,
            {"width": background.width, "height": background.height},
        )
