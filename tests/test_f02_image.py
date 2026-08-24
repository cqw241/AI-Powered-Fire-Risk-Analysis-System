from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from fire_safety.image import (
    ImageProcessingError,
    ImageStatus,
    InvalidBoundingBox,
    bbox_to_pixels,
    draw_bboxes,
    prepare_image,
    validate_bbox_1000,
)
from fire_safety.settings import Settings


def image_bytes(
    image_format: str,
    *,
    size: tuple[int, int] = (20, 10),
    orientation: int | None = None,
) -> bytes:
    image = Image.new("RGB", size, color=(30, 60, 90))
    output = BytesIO()
    save_kwargs: dict[str, object] = {}
    if orientation is not None:
        exif = image.getexif()
        exif[274] = orientation
        save_kwargs["exif"] = exif.tobytes()
    image.save(output, format=image_format, **save_kwargs)
    return output.getvalue()


@pytest.mark.parametrize("image_format", ["JPEG", "PNG", "WEBP"])
def test_prepare_image_decodes_supported_formats(image_format: str) -> None:
    prepared = prepare_image(image_bytes(image_format))

    assert prepared.image_format == image_format
    expected_media_type = (
        "image/jpeg" if image_format == "JPEG" else f"image/{image_format.lower()}"
    )
    assert prepared.media_type == expected_media_type
    assert prepared.image.size == (20, 10)
    assert prepared.width == 20
    assert prepared.height == 10
    with Image.open(BytesIO(prepared.qwen_bytes)) as qwen_image:
        assert qwen_image.size == prepared.image.size


def test_exif_orientation_is_applied_to_both_image_and_qwen_payload() -> None:
    prepared = prepare_image(image_bytes("JPEG", size=(20, 10), orientation=6))

    assert prepared.image.size == (10, 20)
    assert prepared.width == 10
    assert prepared.height == 20
    with Image.open(BytesIO(prepared.qwen_bytes)) as qwen_image:
        assert qwen_image.size == (10, 20)


def test_invalid_image_maps_to_image_unusable() -> None:
    with pytest.raises(ImageProcessingError) as error:
        prepare_image(b"not an image")

    assert error.value.status is ImageStatus.IMAGE_UNUSABLE
    assert error.value.reason == "decode_failed"


def test_unsupported_format_maps_to_image_unusable() -> None:
    with pytest.raises(ImageProcessingError) as error:
        prepare_image(image_bytes("GIF"))

    assert error.value.status == "image_unusable"
    assert error.value.reason == "unsupported_format"


def test_image_limits_are_checked_after_exif_correction() -> None:
    payload = image_bytes("JPEG", size=(20, 10), orientation=6)

    with pytest.raises(ImageProcessingError) as error:
        prepare_image(payload, Settings(max_image_width=9))

    assert error.value.reason == "dimensions_exceeded"
    assert error.value.details["width"] == 10


def test_pixel_and_input_byte_limits_are_enforced() -> None:
    payload = image_bytes("PNG", size=(20, 10))

    with pytest.raises(ImageProcessingError) as pixel_error:
        prepare_image(payload, Settings(max_image_pixels=199))
    assert pixel_error.value.reason == "pixels_exceeded"

    with pytest.raises(ImageProcessingError) as byte_error:
        prepare_image(payload, Settings(max_image_bytes=len(payload) - 1))
    assert byte_error.value.reason == "file_too_large"


@pytest.mark.parametrize(
    "bbox",
    [(-1, 0, 10, 10), (0, 0, 1001, 10), (10, 10, 10, 20), (0, 20, 10, 10), (0, 0, 1.0, 2)],
)
def test_bbox_validation_rejects_invalid_coordinates(bbox: tuple[object, ...]) -> None:
    with pytest.raises(InvalidBoundingBox):
        validate_bbox_1000(bbox)


def test_bbox_conversion_uses_half_open_pixel_bounds() -> None:
    assert bbox_to_pixels((100, 200, 500, 800), 200, 100) == (20, 20, 100, 80)


def test_draw_bboxes_skips_only_invalid_bbox() -> None:
    prepared = prepare_image(image_bytes("PNG", size=(100, 100)))

    result = draw_bboxes(
        prepared,
        {
            "1": [(100, 100, 500, 500), (10, 10, 10, 20)],
            "2": [(500, 500, 900, 900)],
        },
    )

    assert result.size == prepared.image.size
    assert result.getpixel((10, 10)) != prepared.image.getpixel((10, 10))
    assert result.getpixel((50, 50)) != prepared.image.getpixel((50, 50))
