"""Image preprocessing helpers for ollama vision models."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from ...config import settings

Image.MAX_IMAGE_PIXELS = settings.max_image_pixels


_IMAGE_FORMATS: dict[str, str] = {
    "png": "PNG",
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "gif": "GIF",
    "bmp": "BMP",
    "webp": "WEBP",
    "tif": "TIFF",
    "tiff": "TIFF",
}


def _format_from_name(name: str) -> str:
    ext = Path(name).suffix.lstrip(".").lower()
    return _IMAGE_FORMATS.get(ext, "JPEG")


def resize_image(
    data: bytes,
    name: str = "image.jpg",
    max_dimension: int = 1024,
    quality: int = 85,
) -> bytes:
    """Resize and re-encode an image to reduce payload size for ollama."""
    if not data:
        return data

    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.width * img.height > settings.max_image_pixels:
                return data
            processed = img.convert("RGB")

            width, height = processed.size
            if width > max_dimension or height > max_dimension:
                ratio = min(max_dimension / width, max_dimension / height)
                new_size = (int(width * ratio), int(height * ratio))
                processed = processed.resize(new_size, Image.Resampling.LANCZOS)

            fmt = _format_from_name(name)
            output = io.BytesIO()
            save_kwargs: dict[str, object] = {}
            if fmt == "JPEG":
                save_kwargs["quality"] = quality
                save_kwargs["optimize"] = True
            elif fmt == "WEBP":
                save_kwargs["quality"] = quality
            processed.save(output, format=fmt, **save_kwargs)
            return output.getvalue()
    except Exception:  # noqa: BLE001
        return data
