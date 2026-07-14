from __future__ import annotations

import base64
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_AVATAR_BYTES = 5 * 1024 * 1024
DATA_URL_RE = re.compile(r"^data:(image/[^;]+);base64,(.+)$", re.IGNORECASE | re.DOTALL)


class AvatarCanonicalizationError(ValueError):
    """Raised when avatar input cannot be promoted to canonical PNG."""


@dataclass(frozen=True)
class CanonicalAvatarPng:
    content_base64: str
    content_bytes: bytes
    sha256: str
    metadata: dict[str, Any]


def _decode_base64_payload(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except Exception as exc:
        raise AvatarCanonicalizationError("avatar payload is not valid base64") from exc


def split_avatar_payload(value: str, *, source_content_type: str | None = None) -> tuple[bytes, str | None]:
    if not isinstance(value, str) or not value.strip():
        raise AvatarCanonicalizationError("avatar payload is empty")

    raw_value = value.strip()
    match = DATA_URL_RE.match(raw_value)
    if match:
        return _decode_base64_payload(match.group(2).strip()), match.group(1).lower()

    return _decode_base64_payload(raw_value), source_content_type.lower() if source_content_type else None


def is_png_bytes(value: bytes | None) -> bool:
    return bool(value and value.startswith(PNG_SIGNATURE))


def is_png_base64_payload(value: str | None) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        raw, _mime = split_avatar_payload(value)
    except AvatarCanonicalizationError:
        return False
    return is_png_bytes(raw)


def normalize_avatar_payload_to_png(
    value: str,
    *,
    source_content_type: str | None = None,
    source_filename: str | None = None,
    max_bytes: int = MAX_AVATAR_BYTES,
) -> CanonicalAvatarPng:
    raw_bytes, declared_content_type = split_avatar_payload(value, source_content_type=source_content_type)
    if not raw_bytes:
        raise AvatarCanonicalizationError("avatar payload decoded to empty bytes")
    if len(raw_bytes) > max_bytes:
        raise AvatarCanonicalizationError(f"avatar payload exceeds {max_bytes} bytes")

    try:
        from PIL import Image
    except Exception as exc:
        raise AvatarCanonicalizationError("Pillow is required for avatar PNG canonicalization") from exc

    try:
        with Image.open(io.BytesIO(raw_bytes)) as image:
            original_format = (image.format or "").upper()
            image.load()
            if getattr(image, "is_animated", False):
                image.seek(0)
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            out = io.BytesIO()
            image.save(out, format="PNG")
            png_bytes = out.getvalue()
    except Exception as exc:
        raise AvatarCanonicalizationError("avatar payload is not a supported raster image") from exc

    if not is_png_bytes(png_bytes):
        raise AvatarCanonicalizationError("canonicalized avatar did not produce PNG bytes")

    now = datetime.now(timezone.utc).isoformat()
    sha256 = hashlib.sha256(png_bytes).hexdigest()
    metadata = {
        "contentType": "image/png",
        "mimeType": "image/png",
        "originalContentType": declared_content_type,
        "originalFormat": original_format or None,
        "originalFilename": source_filename,
        "canonicalizedFrom": source_filename or declared_content_type or "avatar_payload",
        "canonicalizedAt": now,
        "pngMagicOk": True,
        "sourceBytes": len(raw_bytes),
        "bytes": len(png_bytes),
    }
    return CanonicalAvatarPng(
        content_base64=base64.b64encode(png_bytes).decode("ascii"),
        content_bytes=png_bytes,
        sha256=sha256,
        metadata=metadata,
    )
