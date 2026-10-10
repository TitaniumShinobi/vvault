"""Lossless source capture with deterministic append-only provenance."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from vvault.server import chatty_body_service

CONTRACT_VERSION = "life.vvault.source-native-ingestion/v1"
_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_DESTINATIONS = frozenset({"vault_files", "transcripts"})


class SourceProjectionCollision(RuntimeError):
    """Raised when an immutable projection destination is already occupied."""


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _operation_id(document: dict[str, Any]) -> str:
    return _sha(_canonical(document).encode("utf-8"))


def _bounded_token(value: str, name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _TOKEN.fullmatch(normalized):
        raise ValueError(f"{name} is invalid")
    return normalized


@dataclass
class SourceNativeIngestionService:
    connect: Callable[..., Any] = chatty_body_service._connect

    def ingest(
        self, *, owner_user_id: str, relying_party_id: str, provider: str,
        source_kind: str, source_collection: str, raw_envelope: bytes,
        actor: str, stable_source_id: str | None = None,
        source_locator: str | None = None, observed_at: Any = None,
        payload: bytes | None = None, media_type: str = "application/octet-stream",
        source_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not owner_user_id or not actor or not source_collection:
            raise ValueError("owner, actor, and source_collection are required")
        if not isinstance(raw_envelope, bytes):
            raise TypeError("raw_envelope must be bytes")
        if payload is not None and not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        provider = _bounded_token(provider, "provider")
        source_kind = _bounded_token(source_kind, "source_kind")
        relying_party_id = _bounded_token(relying_party_id, "relying_party_id")
        metadata = source_metadata or {}
        if not isinstance(metadata, dict):
            raise TypeError("source_metadata must be an object")
        raw_hash = _sha(raw_envelope)
        payload_hash = _sha(payload) if payload is not None else None
        operation = {
            "contractVersion": CONTRACT_VERSION,
            "ownerUserId": owner_user_id,
            "relyingPartyId": relying_party_id,
            "provider": provider,
            "sourceKind": source_kind,
            "sourceCollection": source_collection,
            "stableSourceId": stable_source_id,
            "rawEnvelopeSha256": raw_hash,
            "rawEnvelopeBytes": len(raw_envelope),
            "payloadSha256": payload_hash,
            "payloadBytes": len(payload) if payload is not None else None,
        }
        operation_id = _operation_id(operation)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"source-ingest:{operation_id}",))
                cur.execute(
                    "SELECT receipt FROM ovvaults.source_ingest_receipts WHERE operation_id=%s",
                    (operation_id,),
                )
                previous = cur.fetchone()
                if previous:
                    receipt = dict(previous["receipt"])
                    receipt["result"] = "already_applied"
                    conn.commit()
                    return receipt
                cur.execute(
                    """INSERT INTO ovvaults.source_native_artifacts
                    (owner_user_id,relying_party_id,provider,source_kind,source_collection,
                     stable_source_id,source_locator,observed_at,raw_envelope,
                     raw_envelope_sha256,raw_envelope_bytes,payload,payload_sha256,
                     payload_bytes,media_type,source_metadata)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    RETURNING id::text, raw_envelope, raw_envelope_sha256,
                              raw_envelope_bytes, payload, payload_sha256, payload_bytes""",
                    (owner_user_id, relying_party_id, provider, source_kind, source_collection,
                     stable_source_id, source_locator, observed_at, raw_envelope, raw_hash,
                     len(raw_envelope), payload, payload_hash,
                     len(payload) if payload is not None else None, media_type,
                     _canonical(metadata)),
                )
                inserted = dict(cur.fetchone())
                if (bytes(inserted["raw_envelope"]) != raw_envelope
                    or inserted["raw_envelope_sha256"] != raw_hash
                    or int(inserted["raw_envelope_bytes"]) != len(raw_envelope)
                    or (bytes(inserted["payload"]) if inserted["payload"] is not None else None) != payload
                    or inserted["payload_sha256"] != payload_hash
                    or inserted["payload_bytes"] != (len(payload) if payload is not None else None)):
                    conn.rollback()
                    raise RuntimeError("source-native readback assertion failed")
                source_artifact_id = str(inserted["id"])
                receipt = {**operation, "operationId": operation_id,
                           "sourceArtifactId": source_artifact_id, "actor": actor,
                           "classification": "LEGACY_UNCLASSIFIED",
                           "classificationConstruct": "legacy-unassigned", "result": "applied"}
                receipt_sha = _sha(_canonical(receipt).encode("utf-8"))
                cur.execute(
                    """INSERT INTO ovvaults.source_ingest_receipts
                    (operation_id,source_artifact_id,owner_user_id,relying_party_id,
                     contract_version,actor,result,receipt,receipt_sha256)
                    VALUES (%s,%s,%s,%s,%s,%s,'applied',%s::jsonb,%s)""",
                    (operation_id, source_artifact_id, owner_user_id, relying_party_id,
                     CONTRACT_VERSION, actor, _canonical(receipt), receipt_sha),
                )
            conn.commit()
        return receipt

    def record_projection(
        self, *, source_artifact_id: str, owner_user_id: str,
        relying_party_id: str, destination_table: str,
        destination_record_id: str, projection_contract: str,
        projection_version: str, transform_sha256: str,
        source_sha256: str, destination_sha256: str, actor: str,
    ) -> dict[str, Any]:
        if destination_table not in _DESTINATIONS:
            raise ValueError("destination_table is invalid")
        for name, value in (("transform_sha256", transform_sha256),
                            ("source_sha256", source_sha256),
                            ("destination_sha256", destination_sha256)):
            if not re.fullmatch(r"[a-f0-9]{64}", str(value or "")):
                raise ValueError(f"{name} is invalid")
        document = {
            "contractVersion": CONTRACT_VERSION,
            "sourceArtifactId": source_artifact_id,
            "ownerUserId": owner_user_id,
            "relyingPartyId": relying_party_id,
            "destinationTable": destination_table,
            "destinationRecordId": destination_record_id,
            "projectionContract": projection_contract,
            "projectionVersion": projection_version,
            "transformSha256": transform_sha256,
            "sourceSha256": source_sha256,
            "destinationSha256": destination_sha256,
        }
        operation_id = _operation_id(document)
        receipt = {**document, "operationId": operation_id, "actor": actor, "result": "applied"}
        receipt_sha = _sha(_canonical(receipt).encode("utf-8"))
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"source-projection:{operation_id}",))
                cur.execute(
                    "SELECT receipt FROM ovvaults.canonical_source_projections WHERE operation_id=%s",
                    (operation_id,),
                )
                previous = cur.fetchone()
                if previous:
                    replay = dict(previous["receipt"])
                    replay["result"] = "already_applied"
                    conn.commit()
                    return replay
                cur.execute(
                    """INSERT INTO ovvaults.canonical_source_projections
                    (operation_id,source_artifact_id,owner_user_id,relying_party_id,
                     destination_table,destination_record_id,projection_contract,
                     projection_version,transform_sha256,source_sha256,destination_sha256,
                     receipt,receipt_sha256)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                    (operation_id, source_artifact_id, owner_user_id, relying_party_id,
                     destination_table, destination_record_id, projection_contract,
                     projection_version, transform_sha256, source_sha256,
                     destination_sha256, _canonical(receipt), receipt_sha),
                )
            conn.commit()
        return receipt

    def ingest_and_project_vault_file(
        self, *, owner_user_id: str, relying_party_id: str, provider: str,
        source_kind: str, source_collection: str, raw_envelope: bytes,
        projection_content: str, actor: str, stable_source_id: str | None = None,
        source_locator: str | None = None, observed_at: Any = None,
        source_metadata: dict[str, Any] | None = None,
        projection_contract: str, projection_version: str,
        explicit_construct_evidence: bool = False,
        construct_id: str | None = None,
        content_type: str = "text/plain; charset=utf-8",
        file_type: str = "document",
    ) -> dict[str, Any]:
        """Atomically capture source evidence and create one immutable file projection."""
        if not isinstance(raw_envelope, bytes):
            raise TypeError("raw_envelope must be bytes")
        if not isinstance(projection_content, str):
            raise TypeError("projection_content must be text")
        provider = _bounded_token(provider, "provider")
        source_kind = _bounded_token(source_kind, "source_kind")
        relying_party_id = _bounded_token(relying_party_id, "relying_party_id")
        if not owner_user_id or not actor or not source_collection:
            raise ValueError("owner, actor, and source_collection are required")
        if explicit_construct_evidence:
            if construct_id != "zen-001":
                raise ValueError("explicit construct evidence currently permits only zen-001")
            classification, classification_construct = "ACCOUNT_PRIVATE", "zen-001"
            destination_construct_id = "zen-001"
        else:
            if construct_id is not None:
                raise ValueError("construct_id requires explicit construct evidence")
            classification, classification_construct = "LEGACY_UNCLASSIFIED", "legacy-unassigned"
            destination_construct_id = None

        metadata = source_metadata or {}
        if not isinstance(metadata, dict):
            raise TypeError("source_metadata must be an object")
        raw_hash = _sha(raw_envelope)
        content_bytes = projection_content.encode("utf-8")
        destination_hash = _sha(content_bytes)
        transform_document = {
            "projectionContract": projection_contract,
            "projectionVersion": projection_version,
            "sourceSha256": raw_hash,
            "destinationSha256": destination_hash,
        }
        transform_sha = _operation_id(transform_document)
        batch_document = {
            "contractVersion": CONTRACT_VERSION,
            "ownerUserId": owner_user_id,
            "relyingPartyId": relying_party_id,
            "provider": provider,
            "sourceKind": source_kind,
            "sourceCollection": source_collection,
            "stableSourceId": stable_source_id,
            "rawEnvelopeSha256": raw_hash,
            "rawEnvelopeBytes": len(raw_envelope),
            "destinationSha256": destination_hash,
            "destinationBytes": len(content_bytes),
            "projectionContract": projection_contract,
            "projectionVersion": projection_version,
            "classification": classification,
            "classificationConstruct": classification_construct,
        }
        operation_id = _operation_id(batch_document)
        revision_path = f"imports/{provider}/{operation_id}/revision-{destination_hash}.txt"
        object_key = f"users/{owner_user_id}/{relying_party_id}/{revision_path}"
        bucket = "vvault-canonical-v1"

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"source-vault-projection:{operation_id}",))
                cur.execute(
                    "SELECT receipt FROM ovvaults.canonical_source_projections WHERE operation_id=%s",
                    (operation_id,),
                )
                previous = cur.fetchone()
                if previous:
                    replay = dict(previous["receipt"])
                    replay["result"] = "already_applied"
                    conn.commit()
                    return replay

                cur.execute(
                    """SELECT id::text FROM ovvaults.vault_files
                    WHERE user_id=%s AND bucket=%s AND object_key=%s FOR SHARE""",
                    (owner_user_id, bucket, object_key),
                )
                if cur.fetchone():
                    conn.rollback()
                    raise SourceProjectionCollision("immutable vault projection destination is occupied")

                source_operation = {
                    "contractVersion": CONTRACT_VERSION,
                    "ownerUserId": owner_user_id,
                    "relyingPartyId": relying_party_id,
                    "provider": provider,
                    "sourceKind": source_kind,
                    "sourceCollection": source_collection,
                    "stableSourceId": stable_source_id,
                    "rawEnvelopeSha256": raw_hash,
                    "rawEnvelopeBytes": len(raw_envelope),
                    "payloadSha256": None,
                    "payloadBytes": None,
                }
                source_operation_id = _operation_id(source_operation)
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"source-ingest:{source_operation_id}",),
                )
                cur.execute(
                    "SELECT receipt FROM ovvaults.source_ingest_receipts WHERE operation_id=%s",
                    (source_operation_id,),
                )
                source_previous = cur.fetchone()
                if source_previous:
                    source_receipt = dict(source_previous["receipt"])
                    source_artifact_id = str(source_receipt["sourceArtifactId"])
                else:
                    cur.execute(
                        """INSERT INTO ovvaults.source_native_artifacts
                        (owner_user_id,relying_party_id,provider,source_kind,source_collection,
                         stable_source_id,source_locator,observed_at,raw_envelope,
                         raw_envelope_sha256,raw_envelope_bytes,media_type,source_metadata)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                        RETURNING id::text, raw_envelope, raw_envelope_sha256,
                                  raw_envelope_bytes""",
                        (owner_user_id, relying_party_id, provider, source_kind,
                         source_collection, stable_source_id, source_locator, observed_at,
                         raw_envelope, raw_hash, len(raw_envelope),
                         "application/octet-stream", _canonical(metadata)),
                    )
                    inserted_source = dict(cur.fetchone())
                    if (bytes(inserted_source["raw_envelope"]) != raw_envelope
                        or inserted_source["raw_envelope_sha256"] != raw_hash
                        or int(inserted_source["raw_envelope_bytes"]) != len(raw_envelope)):
                        conn.rollback()
                        raise RuntimeError("source-native readback assertion failed")
                    source_artifact_id = str(inserted_source["id"])
                    source_receipt = {**source_operation, "operationId": source_operation_id,
                                      "sourceArtifactId": source_artifact_id, "actor": actor,
                                      "classification": "LEGACY_UNCLASSIFIED",
                                      "classificationConstruct": "legacy-unassigned",
                                      "result": "applied"}
                    source_receipt_sha = _sha(_canonical(source_receipt).encode("utf-8"))
                    cur.execute(
                        """INSERT INTO ovvaults.source_ingest_receipts
                        (operation_id,source_artifact_id,owner_user_id,relying_party_id,
                         contract_version,actor,result,receipt,receipt_sha256)
                        VALUES (%s,%s,%s,%s,%s,%s,'applied',%s::jsonb,%s)""",
                        (source_operation_id, source_artifact_id, owner_user_id,
                         relying_party_id, CONTRACT_VERSION, actor,
                         _canonical(source_receipt), source_receipt_sha),
                    )

                destination_metadata = {
                    "sourceNative": {
                        "sourceArtifactId": source_artifact_id,
                        "sourceOperationId": source_operation_id,
                        "sourceSha256": raw_hash,
                        "provider": provider,
                        "stableSourceId": stable_source_id,
                    },
                    "projection": {
                        "operationId": operation_id,
                        "contract": projection_contract,
                        "version": projection_version,
                        "transformSha256": transform_sha,
                    },
                }
                cur.execute(
                    """INSERT INTO ovvaults.vault_files
                    (user_id,relying_party_id,bucket,object_key,filename,content_type,
                     size_bytes,sha256,content,metadata,construct_id,storage_path,file_type,
                     source_table,source_row_id,source_filename,source_storage_path,
                     materialized_at,is_system,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,
                            'ovvaults.source_native_artifacts',%s,%s,%s,now(),false,now())
                    RETURNING id::text, user_id::text, construct_id, sha256, size_bytes,
                              content, object_key, storage_path""",
                    (owner_user_id, relying_party_id, bucket, object_key, revision_path,
                     content_type, len(content_bytes), destination_hash,
                     projection_content, _canonical(destination_metadata),
                     destination_construct_id, revision_path, file_type,
                     source_artifact_id, stable_source_id, source_locator),
                )
                destination = dict(cur.fetchone())
                if (str(destination.get("user_id")) != owner_user_id
                    or destination.get("construct_id") != destination_construct_id
                    or str(destination.get("sha256")) != destination_hash
                    or int(destination.get("size_bytes") or -1) != len(content_bytes)
                    or destination.get("content") != projection_content
                    or destination.get("object_key") != object_key
                    or destination.get("storage_path") != revision_path):
                    conn.rollback()
                    raise RuntimeError("vault projection readback assertion failed")
                destination_id = str(destination["id"])
                evidence_sha = _operation_id({
                    "sourceArtifactId": source_artifact_id,
                    "destinationRecordId": destination_id,
                    "classification": classification,
                    "classificationConstruct": classification_construct,
                    "operationId": operation_id,
                })
                cur.execute(
                    """INSERT INTO ovvaults.construct_legacy_classifications
                    (source_table,source_row_id,construct_id,classification,evidence_sha256,
                     classified_by_user_id)
                    VALUES ('vault_files',%s,%s,%s,%s,%s)""",
                    (destination_id, classification_construct, classification,
                     evidence_sha, owner_user_id),
                )
                receipt = {**batch_document, "operationId": operation_id,
                           "sourceOperationId": source_operation_id,
                           "sourceArtifactId": source_artifact_id,
                           "destinationTable": "vault_files",
                           "destinationRecordId": destination_id,
                           "destinationPath": revision_path,
                           "destinationObjectKey": object_key,
                           "transformSha256": transform_sha,
                           "actor": actor, "readbackVerified": True, "result": "applied"}
                receipt_sha = _sha(_canonical(receipt).encode("utf-8"))
                cur.execute(
                    """INSERT INTO ovvaults.canonical_source_projections
                    (operation_id,source_artifact_id,owner_user_id,relying_party_id,
                     destination_table,destination_record_id,projection_contract,
                     projection_version,transform_sha256,source_sha256,destination_sha256,
                     receipt,receipt_sha256)
                    VALUES (%s,%s,%s,%s,'vault_files',%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                    (operation_id, source_artifact_id, owner_user_id, relying_party_id,
                     destination_id, projection_contract, projection_version,
                     transform_sha, raw_hash, destination_hash,
                     _canonical(receipt), receipt_sha),
                )
            conn.commit()
        return receipt
