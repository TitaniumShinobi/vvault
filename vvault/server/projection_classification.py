"""Authoritative visibility classification for live instance projections."""

from __future__ import annotations

from typing import Any

EXCLUDED_PROJECTION_CLASSES = frozenset({"evidence", "quarantine", "superseded"})
_BOOLEAN_MARKERS = (
    "replacementEvidence", "replacement_evidence", "evidence", "quarantine",
    "superseded", "projectionExcluded", "projection_excluded",
)
_CLASS_MARKERS = (
    "recordClass", "record_class", "projectionClass", "projection_class",
    "projectionNamespace", "projection_namespace", "namespace",
)


def row_is_projection_excluded(row_or_metadata: Any) -> bool:
    value = row_or_metadata if isinstance(row_or_metadata, dict) else {}
    metadata = value.get("metadata") if "metadata" in value else value
    if not isinstance(metadata, dict):
        return False
    for key in _BOOLEAN_MARKERS:
        marker = metadata.get(key)
        if marker is True or str(marker or "").strip().lower() == "true":
            return True
    for key in _CLASS_MARKERS:
        if str(metadata.get(key) or "").strip().lower() in EXCLUDED_PROJECTION_CLASSES:
            return True
    classification = metadata.get("classification")
    if isinstance(classification, dict):
        for key in ("class", "kind", "namespace", "status"):
            if str(classification.get(key) or "").strip().lower() in EXCLUDED_PROJECTION_CLASSES:
                return True
    return False


PROJECTABLE_METADATA_SQL = """
NOT (
    lower(coalesce(metadata ->> 'replacementEvidence', metadata ->> 'replacement_evidence', 'false')) = 'true'
 OR lower(coalesce(metadata ->> 'evidence', 'false')) = 'true'
 OR lower(coalesce(metadata ->> 'quarantine', 'false')) = 'true'
 OR lower(coalesce(metadata ->> 'superseded', 'false')) = 'true'
 OR lower(coalesce(metadata ->> 'projectionExcluded', metadata ->> 'projection_excluded', 'false')) = 'true'
 OR lower(coalesce(
        metadata ->> 'recordClass', metadata ->> 'record_class',
        metadata ->> 'projectionClass', metadata ->> 'projection_class',
        metadata ->> 'projectionNamespace', metadata ->> 'projection_namespace',
        metadata ->> 'namespace', ''
    )) IN ('evidence', 'quarantine', 'superseded')
 OR lower(coalesce(
        metadata #>> '{classification,class}', metadata #>> '{classification,kind}',
        metadata #>> '{classification,namespace}', metadata #>> '{classification,status}', ''
    )) IN ('evidence', 'quarantine', 'superseded')
)
""".strip()
