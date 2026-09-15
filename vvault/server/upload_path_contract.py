"""Pure upload-relative path preservation rules for VVAULT imports."""

from __future__ import annotations

import re
from pathlib import PurePosixPath


ASSET_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tiff",
    ".mp3", ".wav", ".m4a", ".ogg", ".webm", ".mp4", ".mov",
}

RESERVED_INSTANCE_FOLDERS = {
    "assets", "chatty", "config", "data", "documents", "frame", "identity",
    "logs", "memup", "simdrive", "transcript", "transcripts", "vvault",
    "vxrunner",
}


def safe_upload_relative_path(value: str) -> str:
    """Preserve authored casing/segments while rejecting absolute traversal."""
    normalized = str(value or "").replace("\\", "/").strip()
    if (
        not normalized
        or normalized.startswith("/")
        or "\x00" in normalized
        or re.match(r"^[A-Za-z]:/", normalized)
    ):
        raise ValueError("unsafe or empty upload-relative path")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("upload-relative path traversal is not allowed")
    return "/".join(parts)


def preserved_upload_path(
    *,
    callsign: str,
    relative_path: str,
    upload_kind: str,
    knowledge_destination: str | None = None,
) -> str:
    safe_relative_path = safe_upload_relative_path(relative_path)
    if upload_kind == "transcript":
        # Transcript providers/user-authored folders are first-class instance
        # directories.  The VSI contract has no transcript(s) wrapper.
        parts = safe_relative_path.split("/")
        if len(parts) < 2:
            raise ValueError(
                "transcript uploads require a provider folder beneath the instance root"
            )
        if parts[0].lower() in RESERVED_INSTANCE_FOLDERS:
            raise ValueError(
                "transcript uploads must use a provider folder directly beneath "
                "the instance root; transcript/transcripts/chatty wrappers are forbidden"
            )
        return f"instances/{callsign}/{safe_relative_path}"
    if knowledge_destination not in {"assets", "documents"}:
        raise ValueError("knowledge_destination must be assets or documents")
    extension = PurePosixPath(safe_relative_path).suffix.lower()
    inferred_folder = "assets" if extension in ASSET_EXTENSIONS else "documents"
    if inferred_folder != knowledge_destination:
        raise ValueError(
            f"knowledge_destination={knowledge_destination} conflicts with "
            f"the canonical {inferred_folder} classification"
        )
    canonical_folder = knowledge_destination
    return f"instances/{callsign}/{canonical_folder}/{safe_relative_path}"
