"""Deterministic VVAULT capacity state; independent from authentication."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GIB = 1024 ** 3
STRUCTURED_CODES = {"ENOSPC", "EDQUOT", "EROFS"}


def _recent_event(path: str | None, *, now: float) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        observed = datetime.fromisoformat(str(payload["observed_at"]).replace("Z", "+00:00")).timestamp()
        if payload.get("code") in STRUCTURED_CODES and 0 <= now - observed <= 300:
            return {"code": payload["code"], "mount": str(payload.get("mount") or "")}
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return None


def capacity_status(mount: str = "/", *, event_path: str | None = None, now: float | None = None) -> dict[str, Any]:
    stats = os.statvfs(mount)
    available = stats.f_bavail * stats.f_frsize
    total = stats.f_blocks * stats.f_frsize
    available_inodes, total_inodes = stats.f_favail, stats.f_files
    free_fraction = available / total if total else 0.0
    inode_fraction = available_inodes / total_inodes if total_inodes else 0.0
    readonly = bool(stats.f_flag & getattr(os, "ST_RDONLY", 1))
    event = _recent_event(event_path, now=now if now is not None else datetime.now(timezone.utc).timestamp())
    warning = available < max(8 * GIB, int(total * 0.20)) or inode_fraction < 0.10
    deployment_blocked = available < max(5 * GIB, int(total * 0.15)) or inode_fraction < 0.05
    critical = (available < max(GIB, int(total * 0.05)) or
                available_inodes < max(10_000, int(total_inodes * 0.01)) or readonly or event is not None)
    level = "critical" if critical else "deployment_blocked" if deployment_blocked else "warning" if warning else "ready"
    return {
        "status": level,
        "available_bytes": available,
        "total_bytes": total,
        "free_fraction": free_fraction,
        "available_inodes": available_inodes,
        "total_inodes": total_inodes,
        "inode_free_fraction": inode_fraction,
        "read_only": readonly,
        "recent_storage_event": event,
        "warning": warning,
        "deployment_blocked": deployment_blocked,
        "critical": critical,
        "authentication_regression": False,
    }
