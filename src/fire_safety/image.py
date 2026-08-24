"""Image preparation and normalized bounding-box rendering."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from math import ceil, floor
from pathlib import Path
from typing import TypeAlias

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from fire_safety.settings import Settings, get_settings


class ImageStatus(StrEnum):
    """Stable status exposed when an uploaded image cannot be used."""

    IMAGE_UNUSABLE = "image_unusable"


class ImageProcessingError(ValueError):
    """Raised when an upload cannot become a :class:`PreparedImage`."""

    status = ImageStatus.IMAGE_UNUSABLE

    def __init__(self, message: str, *, reason: str, details: Mapping[str, object] | None = None):
        super().__init__(message)
        self.reason = reason
        self.details = dict(details or {})


@dataclass(frozen=True)
class PreparedImage:
    """The EXIF-corrected image used by both Qwen and bbox rendering.

    ``image`` and ``image_bytes`` represent the same corrected coordinate
    system. The bytes are encoded after EXIF is applied and contain no
    orientation transform that a downstream consumer still needs to perform.
    """

    image: Image.Image
    image_bytes: bytes
    media_type: str
    image_format: str
    width: int
    height: int
    pixel_count: int

    @property
    def qwen_bytes(self) -> bytes:
        """Return the canonical payload for a future Qwen request."""

        return self.image_bytes


BBox1000: TypeAlias = tuple[int, int, int, int]
PixelBBox: TypeAlias = tuple[int, int, int, int]


class InvalidBoundingBox(ValueError):
    """Raised when a normalized bbox violates the MVP contract."""


def prepare_image(
    source: bytes | bytearray | str | Path,
    settings: Settings | None = None,
) -> PreparedImage:
    """Decode and EXIF-correct one JPEG, PNG, or WEBP upload.

    The returned image is the sole coordinate basis for model input and UI
    drawing. All failures use ``status == "image_unusable"`` so the pipeline
    can map them to its public result state without inspecting Pillow errors.
    """

    app_settings = settings or get_settings()
    payload = _read_source(source)
    if not payload:
        raise _image_error("decode_failed", "图片为空")
    if len(payload) > app_settings.max_image_bytes:
        raise _image_error(
            "file_too_large",
            "图片文件超过大小限制",
            input_bytes=len(payload),
            max_image_bytes=app_settings.max_image_bytes,
        )

    try:
        with Image.open(BytesIO(payload)) as opened:
            image_format = (opened.format or "").upper()
            if image_format not in app_settings.allowed_image_formats:
                raise _image_error(
                    "unsupported_format",
                    "图片格式不受支持",
                    image_format=image_format or None,
                )
            opened.load()
            corrected = ImageOps.exif_transpose(opened).copy()
    except ImageProcessingError:
        raise
    except (Image.DecompressionBombError, OSError, ValueError, UnidentifiedImageError) as exc:
        raise _image_error("decode_failed", "图片无法解码") from exc

    width, height = corrected.size
    pixel_count = width * height
    if width > app_settings.max_image_width or height > app_settings.max_image_height:
        raise _image_error(
            "dimensions_exceeded",
            "图片尺寸超过限制",
            width=width,
            height=height,
            max_image_width=app_settings.max_image_width,
            max_image_height=app_settings.max_image_height,
        )
    if pixel_count > app_settings.max_image_pixels:
        raise _image_error(
            "pixels_exceeded",
            "图片像素总数超过限制",
            width=width,
            height=height,
            pixel_count=pixel_count,
            max_image_pixels=app_settings.max_image_pixels,
        )

    try:
        encoded = _encode_corrected(corrected, image_format)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _image_error("encode_failed", "图片无法重新编码") from exc
    if len(encoded) > app_settings.max_image_bytes:
        raise _image_error(
            "file_too_large",
            "修正后的图片超过大小限制",
            encoded_bytes=len(encoded),
            max_image_bytes=app_settings.max_image_bytes,
        )
    return PreparedImage(
        image=corrected,
        image_bytes=encoded,
        media_type=_media_type(image_format),
        image_format=image_format,
        width=width,
        height=height,
        pixel_count=pixel_count,
    )


def validate_bbox_1000(bbox: Sequence[object]) -> BBox1000:
    """Validate and return a normalized ``[x1, y1, x2, y2]`` bbox."""

    if len(bbox) != 4:
        raise InvalidBoundingBox("bbox must contain exactly four coordinates")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in bbox):
        raise InvalidBoundingBox("bbox coordinates must be integers")
    values = tuple(bbox)
    if any(value < 0 or value > 1000 for value in values):
        raise InvalidBoundingBox("bbox coordinates must be between 0 and 1000")
    x_min, y_min, x_max, y_max = values
    if x_min >= x_max or y_min >= y_max:
        raise InvalidBoundingBox("bbox must have positive area")
    return x_min, y_min, x_max, y_max


def bbox_to_pixels(
    bbox: Sequence[object], width: int, height: int
) -> PixelBBox:
    """Convert a valid normalized bbox to a half-open pixel rectangle."""

    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")
    x_min, y_min, x_max, y_max = validate_bbox_1000(bbox)
    pixel_bbox = (
        floor(x_min * width / 1000),
        floor(y_min * height / 1000),
        ceil(x_max * width / 1000),
        ceil(y_max * height / 1000),
    )
    return pixel_bbox


def draw_bboxes(
    prepared: PreparedImage | Image.Image,
    finding_bboxes: Mapping[
        str, Iterable[Sequence[object]]
    ]
    | Iterable[tuple[str, Sequence[object]]],
    *,
    color: str = "#D92D20",
    line_width: int = 4,
) -> Image.Image:
    """Draw valid finding bboxes, skipping invalid bboxes individually.

    ``finding_bboxes`` may be a mapping from finding ID to one or more bboxes,
    or an iterable of ``(finding_id, bbox)`` pairs. Labels are rendered as
    ``F1``, ``F2`` when IDs are numeric and otherwise use the supplied ID.
    """

    if line_width < 1:
        raise ValueError("line_width must be positive")
    image = prepared.image if isinstance(prepared, PreparedImage) else prepared
    canvas = image.convert("RGB").copy()
    draw = ImageDraw.Draw(canvas)
    for finding_id, bbox in _iter_finding_bboxes(finding_bboxes):
        try:
            left, top, right, bottom = bbox_to_pixels(bbox, canvas.width, canvas.height)
        except (InvalidBoundingBox, TypeError):
            continue
        xyxy = (left, top, right - 1, bottom - 1)
        draw.rectangle(xyxy, outline=color, width=line_width)
        label = _finding_label(finding_id)
        text_box = draw.textbbox((left, top), label)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        label_top = max(0, top - text_height - 2)
        draw.rectangle(
            (left, label_top, left + text_width + 4, label_top + text_height + 2),
            fill=color,
        )
        draw.text((left + 2, label_top + 1), label, fill="white")
    return canvas


def _read_source(source: bytes | bytearray | str | Path) -> bytes:
    if isinstance(source, bytes):
        return source
    if isinstance(source, bytearray):
        return bytes(source)
    if isinstance(source, (str, Path)):
        try:
            return Path(source).read_bytes()
        except OSError as exc:
            raise _image_error("decode_failed", "图片文件无法读取") from exc
    raise _image_error("decode_failed", "图片输入类型不受支持")


def _encode_corrected(image: Image.Image, image_format: str) -> bytes:
    with BytesIO() as output:
        options: dict[str, object] = {}
        if image_format == "JPEG":
            if image.mode not in {"RGB", "L", "CMYK"}:
                image = image.convert("RGB")
            options = {"quality": 95, "optimize": False}
        elif image_format == "WEBP":
            options = {"lossless": True}
        image.save(output, format=image_format, **options)
        return output.getvalue()


def _media_type(image_format: str) -> str:
    return {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[image_format]


def _image_error(reason: str, message: str, **details: object) -> ImageProcessingError:
    return ImageProcessingError(message, reason=reason, details=details)


def _iter_finding_bboxes(
    finding_bboxes: Mapping[
        str, Iterable[Sequence[object]]
    ]
    | Iterable[tuple[str, Sequence[object]]],
) -> Iterable[tuple[str, Sequence[object]]]:
    if isinstance(finding_bboxes, Mapping):
        for finding_id, bboxes in finding_bboxes.items():
            for bbox in bboxes:
                yield str(finding_id), bbox
        return
    yield from finding_bboxes


def _finding_label(finding_id: str) -> str:
    return f"F{finding_id}" if finding_id.isdigit() else finding_id


__all__ = [
    "BBox1000",
    "ImageProcessingError",
    "ImageStatus",
    "InvalidBoundingBox",
    "PixelBBox",
    "PreparedImage",
    "bbox_to_pixels",
    "draw_bboxes",
    "prepare_image",
    "validate_bbox_1000",
]
