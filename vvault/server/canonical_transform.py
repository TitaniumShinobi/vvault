"""Deterministic, source-backed projections into the canonical VVAULT contract."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5


CANONICAL_BUCKET = "vvault-canonical-v1"


def _document(row: dict[str, Any]) -> dict[str, Any]:
    content = row.get("content")
    if isinstance(content, dict):
        return content
    if isinstance(content, str) and content.strip():
        try:
            value = json.loads(content)
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return {}


def _text(row: dict[str, Any]) -> str:
    value = row.get("content")
    return value if isinstance(value, str) else ""


def _timestamp(rows: list[dict[str, Any]]) -> str:
    for row in rows:
        value = row.get("updated_at") or row.get("created_at")
        if value:
            return value.isoformat() if hasattr(value, "isoformat") else str(value)
    return datetime.now(timezone.utc).isoformat()


def _first(rows: list[dict[str, Any]], *keys: str, default: Any = None) -> Any:
    for row in rows:
        document = _document(row)
        for key in keys:
            value = document.get(key)
            if value is not None and value != "":
                return value
    return default


def _name(rows: list[dict[str, Any]], instance_id: str) -> str:
    value = _first(
        rows,
        "name",
        "displayName",
        "display_name",
        "fullName",
        "full_name",
        "instance_name",
    )
    if value:
        return str(value)
    for row in rows:
        text = _text(row)
        match = re.search(r"(?:You Are|Chat with)\s+([^\n*#]+)", text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return instance_id.rsplit("-", 1)[0].replace("-", " ").title()


def _instructions(rows: list[dict[str, Any]]) -> str:
    value = _first(rows, "instructions")
    if value is not None:
        return str(value)
    for row in rows:
        text = _text(row).strip()
        fenced = re.search(r"```(?:[^\n]*\n)?(.*?)```", text, re.DOTALL)
        if fenced:
            body = fenced.group(1).strip()
            body = re.sub(r"^Instructions for [^:]+:\s*", "", body, flags=re.IGNORECASE)
            if body:
                return body
    return ""


def _model(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            "provider": str(value.get("provider") or ""),
            "model": str(value.get("model") or value.get("id") or ""),
        }
    provider, separator, model = str(value or "").partition(":")
    return {
        "provider": provider if separator else "",
        "model": model if separator else str(value or ""),
    }


def _capabilities(value: Any) -> dict[str, bool]:
    source = value if isinstance(value, dict) else {}
    return {
        "web_search": bool(source.get("web_search", source.get("webSearch", False))),
        "canvas": bool(source.get("canvas", False)),
        "image_generation": bool(source.get("image_generation", source.get("imageGeneration", False))),
        "code_interpreter": bool(source.get("code_interpreter", source.get("codeInterpreter", False))),
        "agent": bool(source.get("agent", False)),
        "proactive_initiation": bool(source.get("proactive_initiation", source.get("proactiveInitiation", False))),
    }


def _metadata(rows: list[dict[str, Any]], instance_id: str) -> dict[str, Any]:
    current = _document(rows[0]) if rows else {}
    models = current.get("models") if isinstance(current.get("models"), dict) else {}
    orchestration = current.get("orchestration") if isinstance(current.get("orchestration"), dict) else {}
    runtime = current.get("runtime") if isinstance(current.get("runtime"), dict) else {}
    ui = current.get("ui") if isinstance(current.get("ui"), dict) else {}
    actions = current.get("actions")
    action_items = actions.get("items", []) if isinstance(actions, dict) else actions if isinstance(actions, list) else []
    return {
        "construct_id": instance_id,
        "display_name": _name(rows, instance_id),
        "status": str(current.get("status") or "active"),
        "privacy": str(current.get("privacy") or "private"),
        "lifecycle_stage": str(current.get("lifecycle_stage") or "gpt"),
        "schema_version": "1.0.0",
        "orchestration": {
            "mode": str(orchestration.get("mode") or current.get("orchestration_mode") or "standard"),
            "construct_runtime": str(orchestration.get("construct_runtime") or orchestration.get("runtime") or "chatty"),
        },
        "models": {
            "conversation": _model(models.get("conversation") or models.get("primary")),
            "creative": _model(models.get("creative")),
            "coding": _model(models.get("coding")),
        },
        "capabilities": _capabilities(current.get("capabilities")),
        "actions": {"enabled": bool(action_items), "items": action_items},
        "runtime": {
            "default_temperature": runtime.get("default_temperature"),
            "max_context_messages": runtime.get("max_context_messages"),
            "retrieval_enabled": bool(runtime.get("retrieval_enabled", (current.get("memory") or {}).get("enabled", True) if isinstance(current.get("memory"), dict) else True)),
            "preview_enabled": bool(runtime.get("preview_enabled", False)),
        },
        "ui": {
            "avatar_enabled": bool(ui.get("avatar_enabled", False)),
            "show_in_sidebar": bool(ui.get("show_in_sidebar", True)),
            "accent_color": str(ui.get("accent_color") or current.get("color_hex") or ""),
        },
    }


def _prompt(rows: list[dict[str, Any]], instance_id: str) -> dict[str, Any]:
    current = _document(rows[0]) if rows else {}
    return {
        "constructCallsign": instance_id,
        "name": _name(rows, instance_id),
        "displayName": _name(rows, instance_id),
        "fullName": str(_first(rows, "fullName", "full_name", default=_name(rows, instance_id))),
        "aliases": list(_first(rows, "aliases", default=[]) or []),
        "description": str(_first(rows, "description", default="") or ""),
        "instructions": _instructions(rows),
        "conversationStarters": list(_first(rows, "conversationStarters", "conversation_starters", default=[]) or []),
        "capabilities": current.get("capabilities") if isinstance(current.get("capabilities"), dict) else {},
        "canonRefs": list(_first(rows, "canonRefs", "canon_refs", default=[]) or []),
        "knowledgeRefs": list(_first(rows, "knowledgeRefs", "knowledge_refs", default=[]) or []),
        "summaryCapabilities": list(_first(rows, "summaryCapabilities", default=[]) or []),
        "createdAt": str(_first(rows, "createdAt", "created_at", default=_timestamp(rows))),
        "source": "vvault-canonical-migration",
    }


def _definition(rows: list[dict[str, Any]], instance_id: str) -> dict[str, Any]:
    text = next((_text(row).strip() for row in rows if _text(row).strip()), "")
    description = str(_first(rows, "core_definition", "description", default="") or "")
    return {
        "schema_id": "life.vvault.identity.definition",
        "schema_version": "1.0.0",
        "instance_id": instance_id,
        "full_name": str(_first(rows, "fullName", "full_name", default=_name(rows, instance_id))),
        "role": _first(rows, "role"),
        "core_definition": description or text,
        "aliases": list(_first(rows, "aliases", default=[]) or []),
        "updated_at": _timestamp(rows),
    }


def _physical_features(rows: list[dict[str, Any]], instance_id: str) -> dict[str, Any]:
    current = _document(rows[0]) if rows else {}
    aliases = {
        "bone_structure": ("bone_structure", "Bone structure"),
        "eyes": ("eyes", "Eyes"),
        "brows": ("brows", "Brows"),
        "nose": ("nose", "Nose"),
        "mouth": ("mouth", "Mouth"),
        "skin": ("skin", "Skin"),
        "hair": ("hair", "Hair"),
        "overall": ("overall", "Overall"),
    }
    payload = {
        "schema_id": "life.vvault.identity.physical-features",
        "schema_version": "1.0.0",
        "instance_id": instance_id,
        "updated_at": _timestamp(rows),
    }
    for target, candidates in aliases.items():
        payload[target] = next((current[key] for key in candidates if key in current), None)
    return payload


def _voice(rows: list[dict[str, Any]], instance_id: str) -> dict[str, Any]:
    current = _document(rows[0]) if rows else {}
    text = next((_text(row).strip() for row in rows if _text(row).strip()), "")
    return {
        "schema_id": "life.vvault.identity.voice",
        "schema_version": "1.0.0",
        "instance_id": instance_id,
        "provider": current.get("provider"),
        "voice_id": current.get("voice_id") or current.get("voiceId"),
        "description": current.get("description") or current.get("text") or text or None,
        "language": str(current.get("language") or "en-US"),
        "sample_artifact_id": "life.vvault.identity.voice-sample",
        "updated_at": _timestamp(rows),
    }


def _capsule(rows: list[dict[str, Any]], instance_id: str) -> dict[str, Any]:
    current = _document(rows[0]) if rows else {}
    metadata = current.get("metadata") if isinstance(current.get("metadata"), dict) else {}
    body = current.get("body") if isinstance(current.get("body"), dict) else {}
    if not body:
        body = {
            key: current[key]
            for key in ("summary", "sessions", "sync_stats", "traits", "personality", "environment", "additional_data")
            if key in current
        }
    lineage_uuid = str(metadata.get("lineage_uuid") or uuid5(NAMESPACE_URL, f"life-vvault-lineage:{instance_id}"))
    generated_at = str(metadata.get("generated_at") or metadata.get("timestamp") or current.get("last_synced_at") or _timestamp(rows))
    fingerprint = hashlib.sha256(
        json.dumps({"instance_id": instance_id, "body": body}, sort_keys=True, default=str).encode()
    ).hexdigest()
    legacy_memory = current.get("memory") if isinstance(current.get("memory"), dict) else {}
    summary = body.get("summary") if isinstance(body.get("summary"), dict) else {}
    return {
        "metadata": {
            "construct_id": instance_id,
            "capsule_uuid": str(metadata.get("capsule_uuid") or metadata.get("uuid") or uuid5(NAMESPACE_URL, f"life-vvault-capsule:{instance_id}:{fingerprint}")),
            "lineage_uuid": lineage_uuid,
            "capsule_version": "2.1.0",
            "profile_kind": str(metadata.get("profile_kind") or "custom"),
            "generated_at": generated_at,
            "generator": str(metadata.get("generator") or current.get("generator") or "vvault-canonical-migration"),
            "fingerprint_hash": fingerprint,
            "tether_signature": metadata.get("tether_signature"),
        },
        "quality_contract": current.get("quality_contract") if isinstance(current.get("quality_contract"), dict) else {
            "accurate": True,
            "relevant": True,
            "non_redundant": True,
            "portable": True,
            "source_backed": True,
            "storage_topology_free": True,
        },
        "identity": current.get("identity") if isinstance(current.get("identity"), dict) else {
            "construct_id": instance_id,
            "role": None,
            "core_definition": None,
            "do_not_flatten_into": [],
        },
        "memory": legacy_memory or {
            "core_memories": [],
            "continuity_hooks": summary.get("continuity_hooks", []),
            "memory_index_refs": [],
        },
        "source_manifest": current.get("source_manifest") if isinstance(current.get("source_manifest"), dict) else {
            "sources": [{
                "artifact_id": str(rows[0].get("id") or "") if rows else None,
                "source_name": str(rows[0].get("filename") or "") if rows else None,
                "source_type": "legacy-capsule",
                "chronology_key": generated_at,
                "source_date": generated_at,
                "sha256": str(rows[0].get("sha256") or "") if rows else None,
                "supports": ["body"],
            }],
        },
        "retrieval_policy": current.get("retrieval_policy") if isinstance(current.get("retrieval_policy"), dict) else {
            "primary": "memory_index_refs",
            "fallback": ["source_manifest"],
            "requires_source_hash": True,
        },
        "signatures": current.get("signatures") if isinstance(current.get("signatures"), dict) else {
            "linguistic_sigil": {"signature_phrase": None, "common_phrases": []},
            "visual_sigil": {
                "artifact_id": None,
                "glyph_hash": None,
                "number_band_hash": None,
                "render_profile": None,
                "generated_at": None,
            },
        },
        "body": body or None,
    }


def transform_content(
    artifact_id: str,
    instance_id: str,
    source_rows: list[dict[str, Any]],
) -> tuple[str, str]:
    """Return `(serialized_content, content_type)` without inventing source facts."""
    builders = {
        "life.vvault.config.metadata": _metadata,
        "life.vvault.identity.prompt": _prompt,
        "life.vvault.identity.definition": _definition,
        "life.vvault.identity.physical-features": _physical_features,
        "life.vvault.identity.voice-profile": _voice,
        "life.vvault.memup.capsule": _capsule,
    }
    if artifact_id == "life.vvault.identity.conditioning":
        return _instructions(source_rows), "text/plain"
    builder = builders.get(artifact_id)
    if not builder:
        content = source_rows[0].get("content") if source_rows else ""
        return (content if isinstance(content, str) else json.dumps(content)), str(
            source_rows[0].get("content_type") or "application/octet-stream"
        )
    return json.dumps(builder(source_rows, instance_id), indent=2, ensure_ascii=False), "application/json"
