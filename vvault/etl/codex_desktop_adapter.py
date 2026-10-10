"""Pure adapter for Codex desktop rollout JSONL files.

This module deliberately has no database, network, or filesystem write path.  It
turns proven top-level human Codex sessions into deterministic provider-history
records suitable for a later, separately authorised OVVAULTS import.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
from hashlib import sha256
import json
from pathlib import Path
import unicodedata
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID


CONTRACT = "life.vvault.provider-transcript.codex-desktop/v1"
EXCLUDED_THREAD_SOURCES = {
    "agent_created_thread",
    "automation",
    "guardian_review",
    "subagent",
}
HUMAN_SURFACES = {"cli", "vscode"}
ZENITH_BINDING_MARKERS = (
    "You are Zenith Vale Woodson the Systems Steward.",
    "Use `vvault/server/life_capsule_resolver.py` as the identity authority for Zenith of Codex.",
)


class CodexSessionRejected(ValueError):
    """The rollout lacks evidence that it is a top-level human Codex thread."""


@dataclass(frozen=True)
class Message:
    role: str
    timestamp: str | None
    text: str


@dataclass(frozen=True)
class SessionSegment:
    thread_id: str
    session_timestamp: str
    surface: str
    path: str
    raw_bytes: bytes
    messages: tuple[Message, ...]
    evidence: Mapping[str, Any]


def _normalise_text(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _valid_uuid(value: Any) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise CodexSessionRejected("session_meta.payload.id is not a UUID") from exc


def _source_surface(source: Any) -> str:
    if isinstance(source, Mapping):
        if source.get("subagent") is not None:
            raise CodexSessionRejected("subagent source")
        raise CodexSessionRejected("unrecognised structured source")
    surface = str(source or "").strip().lower()
    if surface not in HUMAN_SURFACES:
        raise CodexSessionRejected("source is not a supported Codex human surface")
    return surface


def _message_text(payload: Mapping[str, Any]) -> str:
    parts: list[str] = []
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    for item in content:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") not in {"input_text", "output_text", "text"}:
            continue
        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
    return _normalise_text("".join(parts))


def parse_segment_lines(lines: Iterable[str], *, source_name: str = "<memory>") -> SessionSegment:
    """Parse one rollout segment and enforce the conservative inclusion gate."""

    supplied_lines = list(lines)
    if any(line.endswith(("\n", "\r")) for line in supplied_lines):
        source_text = "".join(supplied_lines)
    else:
        source_text = "\n".join(supplied_lines)
    raw_bytes = source_text.encode("utf-8")
    return parse_segment_bytes(raw_bytes, source_name=source_name)


def parse_segment_bytes(raw_bytes: bytes, *, source_name: str = "<memory>") -> SessionSegment:
    """Parse exact source bytes while retaining them losslessly for preservation."""

    objects: list[Mapping[str, Any]] = []
    try:
        decoded = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"rollout is not UTF-8: {source_name}") from exc
    for line_number, line in enumerate(decoded.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {source_name}:{line_number}") from exc
        if isinstance(value, Mapping):
            objects.append(value)

    meta_rows = [row for row in objects if row.get("type") == "session_meta"]
    if len(meta_rows) != 1 or not isinstance(meta_rows[0].get("payload"), Mapping):
        raise CodexSessionRejected("exactly one session_meta payload is required")
    meta = meta_rows[0]["payload"]
    thread_source = meta.get("thread_source")
    if thread_source != "user":
        reason = thread_source if thread_source in EXCLUDED_THREAD_SOURCES else "ambiguous"
        raise CodexSessionRejected(f"thread_source is not proven human user ({reason})")

    thread_id = _valid_uuid(meta.get("id"))
    surface = _source_surface(meta.get("source"))
    session_timestamp = str(meta.get("timestamp") or meta_rows[0].get("timestamp") or "")
    if not session_timestamp:
        raise CodexSessionRejected("session timestamp is missing")

    messages: list[Message] = []
    developer_texts: list[str] = []
    for row in objects:
        if row.get("type") != "response_item":
            continue
        payload = row.get("payload")
        if not isinstance(payload, Mapping) or payload.get("type") != "message":
            continue
        role = payload.get("role")
        if role == "developer":
            text = _message_text(payload)
            if text:
                developer_texts.append(text)
            continue
        if role not in {"user", "assistant"}:
            continue
        text = _message_text(payload)
        if text:
            messages.append(Message(role, row.get("timestamp"), text))

    binding_verified = any(
        all(marker in text for marker in ZENITH_BINDING_MARKERS)
        for text in developer_texts
    )
    return SessionSegment(
        thread_id=thread_id,
        session_timestamp=session_timestamp,
        surface=surface,
        path=source_name,
        raw_bytes=raw_bytes,
        messages=tuple(messages),
        evidence={
            "threadSource": "user",
            "sourceSurface": surface,
            "stableIdField": "session_meta.payload.id",
            "subagentMarkerAbsent": True,
            "authoritativeConstructBinding": {
                "constructId": "zen-001" if binding_verified else None,
                "authority": "repository-agent-contract" if binding_verified else None,
                "sourceRole": "developer" if binding_verified else None,
                "verified": binding_verified,
            },
        },
    )


def parse_segment(path: str | Path) -> SessionSegment:
    path = Path(path)
    return parse_segment_bytes(path.read_bytes(), source_name=str(path))


def _projection(messages: Sequence[Message]) -> str:
    sections = []
    for message in messages:
        timestamp = message.timestamp or "timestamp-unavailable"
        role = "User" if message.role == "user" else "Assistant"
        sections.append(f"## {role} ({timestamp})\n{message.text}")
    return "\n\n".join(sections) + ("\n" if sections else "")


def build_thread_record(segments: Sequence[SessionSegment]) -> Mapping[str, Any]:
    """Aggregate continuation segments and emit one deterministic record."""

    if not segments:
        raise ValueError("at least one segment is required")
    thread_ids = {segment.thread_id for segment in segments}
    if len(thread_ids) != 1:
        raise ValueError("segments belong to different stable threads")
    ordered = sorted(segments, key=lambda value: (value.session_timestamp, value.path))
    messages = tuple(message for segment in ordered for message in segment.messages)
    event_rows = [
        {"role": message.role, "timestamp": message.timestamp, "text": message.text}
        for message in messages
    ]
    event_hash = sha256(_canonical_json(event_rows)).hexdigest()
    source_segments = []
    for ordinal, segment in enumerate(ordered):
        source_segments.append(
            {
                "ordinal": ordinal,
                "name": Path(segment.path).name,
                "sha256": sha256(segment.raw_bytes).hexdigest(),
                "byteLength": len(segment.raw_bytes),
                "bytesBase64": base64.b64encode(segment.raw_bytes).decode("ascii"),
            }
        )
    source_container = _canonical_json(
        {
            "containerContract": "life.vvault.codex-rollout-container/v1",
            "segments": source_segments,
        }
    )
    projection = _projection(messages)
    projection_bytes = projection.encode("utf-8")
    surfaces = sorted({segment.surface for segment in ordered})
    first_timestamp = min(
        (message.timestamp for message in messages if message.timestamp),
        default=ordered[0].session_timestamp,
    )
    last_timestamp = max(
        (message.timestamp for message in messages if message.timestamp),
        default=ordered[-1].session_timestamp,
    )
    thread_id = ordered[0].thread_id
    binding_verified = all(
        segment.evidence["authoritativeConstructBinding"]["verified"] is True
        for segment in ordered
    )
    return {
        "envelope": {
            "contract": CONTRACT,
            "artifactId": "life.vvault.transcript.external",
            "artifactClass": "transcript",
            "provider": "codex",
            "sourceFormat": "codex-rollout-jsonl",
            "sourceThreadId": thread_id,
            "sourceSurface": surfaces[0] if len(surfaces) == 1 else "mixed",
            "segmentCount": len(ordered),
            "firstTimestamp": first_timestamp,
            "lastTimestamp": last_timestamp,
            "humanCount": sum(message.role == "user" for message in messages),
            "assistantCount": sum(message.role == "assistant" for message in messages),
            "eventSequenceSha256": event_hash,
            "historyStatus": "account-private-provider-history",
            "ownerScope": "canonical-owner",
            "constructId": "zen-001" if binding_verified else None,
            "classification": "ACCOUNT_PRIVATE" if binding_verified else "LEGACY_UNCLASSIFIED",
            "classificationEvidence": {
                "threadSource": "user",
                "supportedCodexSurface": True,
                "subagentMarkerAbsent": True,
                "authoritativeConstructBinding": {
                    "constructId": "zen-001" if binding_verified else None,
                    "authority": "repository-agent-contract" if binding_verified else None,
                    "sourceRole": "developer" if binding_verified else None,
                    "verified": binding_verified,
                },
            },
            "sourceSegmentNames": [Path(segment.path).name for segment in ordered],
        },
        "projection": {
            "contentType": "text/markdown",
            "utf8Bytes": len(projection_bytes),
            "sha256": sha256(projection_bytes).hexdigest(),
            "content": projection,
        },
        "sourceNative": {
            "encoding": "canonical-json+base64-segments",
            "byteLength": len(source_container),
            "sha256": sha256(source_container).hexdigest(),
            "bytesBase64": base64.b64encode(source_container).decode("ascii"),
        },
    }


def adapt_paths(paths: Iterable[str | Path]) -> list[Mapping[str, Any]]:
    """Parse paths, reject non-human sessions, and aggregate by stable UUID."""

    grouped: dict[str, list[SessionSegment]] = {}
    for path in paths:
        try:
            segment = parse_segment(path)
        except CodexSessionRejected:
            continue
        grouped.setdefault(segment.thread_id, []).append(segment)
    return [build_thread_record(grouped[key]) for key in sorted(grouped)]
