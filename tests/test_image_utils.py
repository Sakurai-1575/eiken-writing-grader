from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from eiken_grader.errors import ImageError
from eiken_grader.services.image_utils import images_digest, prepare_image
from tests.conftest import make_image_bytes


def open_img(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_resizes_long_edge():
    raw = make_image_bytes(size=(4000, 3000))
    img = prepare_image(raw, max_long_edge=2000)
    assert max(img.width, img.height) == 2000
    assert (img.width, img.height) == (2000, 1500)
    assert img.mime_type == "image/jpeg"
    assert open_img(img.data).format == "JPEG"


def test_small_image_not_upscaled():
    img = prepare_image(make_image_bytes(size=(600, 400)), max_long_edge=2000)
    assert (img.width, img.height) == (600, 400)


def test_exif_orientation_applied_and_metadata_removed():
    # Orientation=6（90度回転）: 横長で保存された画像は縦長に補正される
    raw = make_image_bytes(size=(400, 300), exif_orientation=6)
    assert open_img(raw).getexif().get(0x010F) == "TestCamera"
    img = prepare_image(raw)
    assert (img.width, img.height) == (300, 400)
    out = open_img(img.data)
    assert len(out.getexif()) == 0  # 撮影機器・位置情報などのメタデータは残さない


def test_png_with_alpha_converted_to_rgb():
    img = prepare_image(make_image_bytes(fmt="PNG", color=(0, 0, 0, 0)))
    assert open_img(img.data).mode == "RGB"


def test_heic_supported():
    pillow_heif = pytest.importorskip("pillow_heif")
    buf = io.BytesIO()
    heif = pillow_heif.from_pillow(Image.new("RGB", (320, 240), (200, 200, 200)))
    heif.save(buf, quality=80)
    img = prepare_image(buf.getvalue())
    assert (img.width, img.height) == (320, 240)


@pytest.mark.parametrize("raw", [b"", b"not an image", b"\x89PNG\r\n\x1a\n broken"])
def test_broken_input_raises_image_error(raw):
    with pytest.raises(ImageError):
        prepare_image(raw)


def test_inline_part_and_digest():
    a = prepare_image(make_image_bytes(color=(255, 255, 255)))
    b = prepare_image(make_image_bytes(color=(0, 0, 0)))
    part = a.to_inline_part()
    assert part["inline_data"]["mime_type"] == "image/jpeg"
    assert base64.b64decode(part["inline_data"]["data"]) == a.data
    assert images_digest([a, b]) != images_digest([b, a])
    assert images_digest([a]) == images_digest([a])
