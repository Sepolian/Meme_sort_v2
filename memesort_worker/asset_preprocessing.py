"""Asset preprocessing: pure image computation without database or manifest access.

This module owns EXIF/alpha/color normalization, still image processing,
GIF frame extraction, and image dimension reading.  All functions accept
bytes and an explicit preprocessing spec; they never read the manifest
or touch the database.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps

from .recipe_provider import PreprocessSpec


@dataclass(frozen=True)
class ImageDimensions:
    width: int | None
    height: int | None


class ImportImageValidationError(ValueError):
    """Image validation failure with a stable Import Failure classification."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def image_dimensions_from_bytes(image_bytes: bytes) -> tuple[int, int]:
    """Read image dimensions from raw bytes."""
    with Image.open(io.BytesIO(image_bytes)) as image:
        width, height = image.size
    return int(width), int(height)


def safe_image_dimensions_from_bytes(image_bytes: bytes) -> tuple[int | None, int | None]:
    """Read image dimensions, returning (None, None) on failure."""
    try:
        return image_dimensions_from_bytes(image_bytes)
    except Exception:
        return None, None


def validate_import_image_bytes(
    image_bytes: bytes,
    *,
    max_frame_pixels: int,
    max_gif_frames: int,
) -> tuple[int, int]:
    """Strictly decode an imported image and enforce its resource limits.

    Every frame is loaded so corrupt later GIF frames cannot become a durable
    Library Copy.  The first frame supplies the Asset dimensions.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            frame_count = max(1, int(getattr(image, "n_frames", 1)))
            if image.format == "GIF" and frame_count > max_gif_frames:
                raise ImportImageValidationError("gif_frame_limit_exceeded")

            first_dimensions: tuple[int, int] | None = None
            for frame_index in range(frame_count):
                image.seek(frame_index)
                width, height = (int(value) for value in image.size)
                if width * height > max_frame_pixels:
                    raise ImportImageValidationError("image_frame_too_large")
                image.load()
                if first_dimensions is None:
                    first_dimensions = (width, height)

            assert first_dimensions is not None
            return first_dimensions
    except ImportImageValidationError:
        raise
    except Exception as error:
        raise ImportImageValidationError("image_decode_failed") from error


def composite_rgb(image: Image.Image, spec: PreprocessSpec) -> Image.Image:
    """Composite an image onto the alpha background and convert to color mode."""
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new(
            "RGB",
            rgba.size,
            spec.alpha_background,
        )
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert(spec.color_mode)


def preprocess_image_bytes(
    image_bytes: bytes,
    spec: PreprocessSpec,
) -> bytes:
    """Normalize a still image: EXIF orientation, alpha composite, resize.

    Returns PNG bytes ready for embedding.
    """
    max_side = spec.still_max_side

    with Image.open(io.BytesIO(image_bytes)) as image:
        oriented = ImageOps.exif_transpose(image)
        rgb = composite_rgb(oriented, spec)
        rgb.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        rgb.save(buffer, format="PNG")
        return buffer.getvalue()


def extract_gif_frame_bytes(
    image_bytes: bytes,
    spec: PreprocessSpec,
    frame_count: int,
) -> list[tuple[int, bytes]]:
    """Extract and preprocess GIF frames for embedding.

    Returns a list of (frame_index, png_bytes) tuples.
    """
    max_side = spec.gif_max_side
    if frame_count <= 0:
        raise ValueError("frame_count must be positive")

    frame_payloads: list[tuple[int, bytes]] = []
    with Image.open(io.BytesIO(image_bytes)) as image:
        total_frames = max(1, int(getattr(image, "n_frames", 1)))
        if total_frames <= frame_count:
            selected_indexes = list(range(total_frames))
        else:
            selected_indexes = sorted(
                {
                    round(index * (total_frames - 1) / (frame_count - 1))
                    for index in range(frame_count)
                }
            )

        for frame_index in selected_indexes:
            image.seek(frame_index)
            rgb = composite_rgb(image, spec)
            rgb.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            rgb.save(buffer, format="PNG")
            frame_payloads.append((frame_index, buffer.getvalue()))
    return frame_payloads
