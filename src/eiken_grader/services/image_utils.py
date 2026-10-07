"""画像前処理（すべてメモリ内で行い、ディスクには書かない）。

1. HEIC / HEIF（iPad の写真）を読み込めるようにする
2. EXIF の向き情報で回転補正
3. 長辺を上限サイズに縮小（トークン消費と送信時間の削減）
4. JPEG に再エンコード（EXIF・位置情報などのメタデータは付与しない）
"""

from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from eiken_grader.errors import ImageError

try:  # iPad の HEIC 写真に対応（未インストールでも他形式は動作する）
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:  # pragma: no cover
    HEIF_SUPPORTED = False

ACCEPTED_TYPES = ["jpg", "jpeg", "png", "webp", "heic", "heif"]

# 解凍爆弾対策（4096 x 4096 x 4 枚分程度を上限とする）
Image.MAX_IMAGE_PIXELS = 80_000_000


@dataclass(frozen=True)
class PreparedImage:
    data: bytes
    width: int
    height: int
    mime_type: str = "image/jpeg"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    def to_inline_part(self) -> dict:
        """Gemini の ``parts`` に渡す inline_data 形式。"""
        return {"inline_data": {"mime_type": self.mime_type, "data": base64.b64encode(self.data).decode()}}


def prepare_image(raw: bytes, max_long_edge: int = 2000, jpeg_quality: int = 85) -> PreparedImage:
    if not raw:
        raise ImageError("画像データが空です。")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "L"):
                # 透過 PNG などは白背景に合成
                rgba = img.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[-1])
                img = bg
            elif img.mode == "L":
                img = img.convert("RGB")
            img.thumbnail((max_long_edge, max_long_edge), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            # exif を渡さないため、メタデータは書き出されない
            img.save(out, format="JPEG", quality=jpeg_quality, optimize=True)
            return PreparedImage(data=out.getvalue(), width=img.width, height=img.height)
    except Image.DecompressionBombError as e:
        raise ImageError("画像のサイズが大きすぎます。") from e
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ImageError() from e


def images_digest(images: list[PreparedImage]) -> str:
    h = hashlib.sha256()
    for img in images:
        h.update(img.sha256.encode())
    return h.hexdigest()
