"""Publisher-owned immutable marketplace packages and owner-scoped installs."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from vvault.server import chatty_body_service

_cache_lock = threading.Lock()
_listing_cache: tuple[float, list[dict[str, Any]]] | None = None
_package_cache: dict[tuple[str, bool, bool], tuple[float, dict[str, Any] | None]] = {}
_preflight_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
CACHE_TTL_SECONDS = 30.0
PREFLIGHT_CACHE_TTL_SECONDS = 10.0
CALLSIGN_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*-\d{3}$")


def _connect():
    return chatty_body_service._connect()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _invalidate_marketplace_caches(*, owner_user_id: str | None = None) -> None:
    global _listing_cache
    with _cache_lock:
        _listing_cache = None
        _package_cache.clear()
        if owner_user_id is None:
            _preflight_cache.clear()
        else:
            for key in [key for key in _preflight_cache if key[0] == owner_user_id]:
                _preflight_cache.pop(key, None)


def _validate_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    required = (
        "schemaId", "packageSlug", "packageVersion", "displayName",
        "description", "instructions", "capabilities", "gender",
        "conversationStarters", "defaultCallsign", "sourceHashes",
    )
    missing = [key for key in required if key not in manifest]
    if missing:
        raise ValueError(f"manifest missing fields: {', '.join(missing)}")
    if manifest["schemaId"] != "life.vvault.marketplace-package.v1":
        raise ValueError("unsupported marketplace package schema")
    if not CALLSIGN_RE.fullmatch(str(manifest["defaultCallsign"])):
        raise ValueError("defaultCallsign is invalid")
    if not isinstance(manifest["conversationStarters"], list):
        raise ValueError("conversationStarters must be an explicit public array")
    if not isinstance(manifest["capabilities"], list):
        raise ValueError("capabilities must be an array")
    hashes = manifest.get("sourceHashes")
    if not isinstance(hashes, dict) or any(
        not re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in hashes.values()
    ):
        raise ValueError("sourceHashes must contain SHA-256 values")
    return manifest


def verify_package_signature(manifest: dict[str, Any], signature_base64: str, public_key_base64: str) -> str:
    canonical = _canonical_json(_validate_manifest(manifest))
    public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_base64, validate=True))
    public_key.verify(base64.b64decode(signature_base64, validate=True), canonical.encode("utf-8"))
    return _sha(canonical)


def verify_curated_attestation(
    manifest: dict[str, Any], provenance: dict[str, Any], signature_base64: str, public_key_base64: str
) -> str:
    canonical = _canonical_json({"manifest": _validate_manifest(manifest), "provenance": provenance})
    public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_base64, validate=True))
    public_key.verify(base64.b64decode(signature_base64, validate=True), canonical.encode("utf-8"))
    return _sha(canonical)


def register_publisher(publisher_slug: str, display_name: str, origin_label: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", publisher_slug):
        raise ValueError("publisherSlug is invalid")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO ovvaults.marketplace_publishers
                (publisher_slug,display_name,origin_label) VALUES (%s,%s,%s)
                RETURNING id::text,publisher_slug,display_name,origin_label,publisher_type,created_at""",
                (publisher_slug, display_name, origin_label))
            row = dict(cur.fetchone())
        conn.commit()
    return row


def register_signing_key(publisher_id: str, key_id: str, public_key_base64: str) -> dict[str, Any]:
    raw = base64.b64decode(public_key_base64, validate=True)
    if len(raw) != 32 or not key_id or len(key_id) > 120:
        raise ValueError("ed25519 signing key is invalid")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO ovvaults.marketplace_publisher_signing_keys
                (publisher_id,key_id,algorithm,public_key_base64) VALUES (%s,%s,'ed25519',%s)
                RETURNING id::text,publisher_id::text,key_id,algorithm,created_at""",
                (str(UUID(publisher_id)), key_id, public_key_base64))
            row = dict(cur.fetchone())
        conn.commit()
    return row


def register_curator(curator_slug: str, display_name: str, public_key_base64: str) -> dict[str, Any]:
    raw = base64.b64decode(public_key_base64, validate=True)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", curator_slug) or len(raw) != 32:
        raise ValueError("curator identity or Ed25519 key is invalid")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO ovvaults.marketplace_curators
                (curator_slug,display_name,public_key_base64) VALUES (%s,%s,%s)
                RETURNING id::text,curator_slug,display_name,created_at""",
                (curator_slug, display_name, public_key_base64))
            row = dict(cur.fetchone())
        conn.commit()
    return row


def publish_package(publisher_id: str, signing_key_id: str, manifest: dict[str, Any], signature_base64: str, avatar_bytes: bytes | None, avatar_content_type: str | None) -> dict[str, Any]:
    global _listing_cache
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT public_key_base64 FROM ovvaults.marketplace_publisher_signing_keys
                WHERE id=%s AND publisher_id=%s AND revoked_at IS NULL""",
                (str(UUID(signing_key_id)), str(UUID(publisher_id))))
            key = cur.fetchone()
            if not key:
                raise PermissionError("publisher signing key is unavailable")
            manifest_sha = verify_package_signature(manifest, signature_base64, key["public_key_base64"])
            avatar_sha = _sha(avatar_bytes) if avatar_bytes is not None else None
            declared_avatar = str((manifest.get("sourceHashes") or {}).get("avatar") or "")
            if avatar_sha and declared_avatar != avatar_sha:
                raise ValueError("avatar bytes do not match signed source hash")
            provenance = {"basis": "publisher_signature", "publisherId": str(UUID(publisher_id))}
            cur.execute("""INSERT INTO ovvaults.marketplace_packages
                (publisher_id,signing_key_id,publication_mode,verification_status,attribution_basis,
                 provenance,package_slug,package_version,manifest,manifest_sha256,source_hashes,
                 signature_base64,avatar_body,avatar_sha256,avatar_content_type)
                VALUES (%s,%s,'publisher_signed','publisher_verified','publisher_signature',
                        %s::jsonb,%s,%s,%s::jsonb,%s,%s::jsonb,%s,%s,%s,%s)
                RETURNING id::text AS package_id,package_slug,package_version,manifest_sha256,published_at""",
                (publisher_id,signing_key_id,_canonical_json(provenance),manifest["packageSlug"],manifest["packageVersion"],_canonical_json(manifest),manifest_sha,_canonical_json(manifest["sourceHashes"]),signature_base64,avatar_bytes,avatar_sha,avatar_content_type))
            row = dict(cur.fetchone())
            cur.execute("INSERT INTO ovvaults.marketplace_listing_states (package_id,listing_status) VALUES (%s,'active')", (row["package_id"],))
        conn.commit()
    _invalidate_marketplace_caches()
    return row


def publish_curated_package(
    publisher_id: str,
    curator_id: str,
    manifest: dict[str, Any],
    provenance: dict[str, Any],
    attestation_signature_base64: str,
    avatar_bytes: bytes | None,
    avatar_content_type: str | None,
) -> dict[str, Any]:
    """Import supplied public provenance without pretending the origin signed it."""
    global _listing_cache
    manifest = _validate_manifest(manifest)
    required = {"sourceName", "capturedAt", "curatorIdentity"}
    if not isinstance(provenance, dict) or not required.issubset(provenance):
        raise ValueError("curated provenance requires sourceName, capturedAt, and curatorIdentity")
    if not provenance.get("sourceUrl") and not re.fullmatch(r"[0-9a-f]{64}", str(provenance.get("evidenceHash") or "")):
        raise ValueError("curated provenance requires sourceUrl or evidenceHash")
    attestation = {"manifest": manifest, "provenance": provenance}
    canonical_attestation = _canonical_json(attestation)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT public_key_base64,curator_slug FROM ovvaults.marketplace_curators WHERE id=%s", (str(UUID(curator_id)),))
            curator = cur.fetchone()
            if not curator or str(provenance["curatorIdentity"]) != str(curator["curator_slug"]):
                raise PermissionError("curator identity does not match attestation")
            verify_curated_attestation(manifest, provenance, attestation_signature_base64, curator["public_key_base64"])
            manifest_sha = _sha(_canonical_json(manifest))
            avatar_sha = _sha(avatar_bytes) if avatar_bytes is not None else None
            if avatar_sha and avatar_sha != str(manifest["sourceHashes"].get("avatar") or ""):
                raise ValueError("avatar bytes do not match attested source hash")
            cur.execute("""INSERT INTO ovvaults.marketplace_packages
                (publisher_id,curator_id,publication_mode,verification_status,attribution_basis,
                 provenance,attestation_signature_base64,package_slug,package_version,manifest,
                 manifest_sha256,source_hashes,signature_base64,avatar_body,avatar_sha256,avatar_content_type)
                VALUES (%s,%s,'curated_import','curator_attested','supplied_public_provenance',
                        %s::jsonb,%s,%s,%s,%s::jsonb,%s,%s::jsonb,'',%s,%s,%s)
                RETURNING id::text AS package_id,package_slug,package_version,manifest_sha256,published_at""",
                (str(UUID(publisher_id)),str(UUID(curator_id)),_canonical_json(provenance),attestation_signature_base64,
                 manifest["packageSlug"],manifest["packageVersion"],_canonical_json(manifest),manifest_sha,
                 _canonical_json(manifest["sourceHashes"]),avatar_bytes,avatar_sha,avatar_content_type))
            row = dict(cur.fetchone())
            cur.execute("INSERT INTO ovvaults.marketplace_listing_states (package_id,listing_status) VALUES (%s,'active')", (row["package_id"],))
        conn.commit()
    _invalidate_marketplace_caches()
    return row


def _dto(row: dict[str, Any], *, detail: bool) -> dict[str, Any]:
    manifest = row["manifest"] if isinstance(row.get("manifest"), dict) else json.loads(row["manifest"])
    provenance = row.get("provenance") or {}
    if not isinstance(provenance, dict):
        provenance = json.loads(provenance)
    listing_id = str(row["package_id"])
    avatar_state = "available" if row.get("avatar_sha256") else "missing"
    dto = {
        "listingId": listing_id,
        "packageId": listing_id,
        "packageSlug": str(row["package_slug"]),
        "packageVersion": str(row["package_version"]),
        "displayName": str(manifest["displayName"]),
        "description": str(manifest["description"]),
        "creator": {"label": str(row["display_name"]), "type": "external_publisher"},
        "origin": {
            "label": str(row["origin_label"]),
            "publisherSlug": str(row["publisher_slug"]),
            "provenance": str(row["publication_mode"]),
            "sourceName": provenance.get("sourceName"),
            "sourceUrl": provenance.get("sourceUrl"),
            "evidenceHash": provenance.get("evidenceHash"),
            "capturedAt": provenance.get("capturedAt"),
        },
        "verificationStatus": str(row["verification_status"]),
        "attributionBasis": str(row["attribution_basis"]),
        "attestation": {
            "type": str(row["publication_mode"]),
            "curatorLabel": row.get("curator_display_name"),
        },
        "capabilities": list(manifest["capabilities"]),
        "gender": str(manifest["gender"]),
        "conversationStarters": list(manifest["conversationStarters"]),
        "listingStatus": "active",
        "lifecycleStage": "gpt",
        "constructCategory": "user",
        "avatar": {
            "state": avatar_state,
            "descriptorUrl": f"/api/chatty/marketplace/listings/{listing_id}/avatar",
            "bytesUrl": f"/api/chatty/marketplace/listings/{listing_id}/avatar/bytes",
            "sha256": row.get("avatar_sha256"),
            "contentType": row.get("avatar_content_type"),
            "sizeBytes": int(row.get("avatar_size_bytes") or 0),
        },
        "installDefaults": {
            "requestedCallsign": str(manifest["defaultCallsign"]),
            "privacy": "private",
            "lifecycleStage": "gpt",
            "constructCategory": "user",
        },
        "packageManifestSha256": str(row["manifest_sha256"]),
        "publishedAt": row["published_at"].isoformat() if hasattr(row["published_at"], "isoformat") else str(row["published_at"]),
        "ownerIdentifiersProjected": False,
    }
    if detail:
        dto["instructions"] = str(manifest["instructions"])
        dto["sourceHashes"] = dict(manifest["sourceHashes"])
    return dto


def _package_rows() -> list[dict[str, Any]]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT package.id::text AS package_id, package.package_slug,
                       package.package_version, package.manifest, package.manifest_sha256,
                       package.avatar_sha256, package.avatar_content_type,
                       octet_length(package.avatar_body) AS avatar_size_bytes,
                       package.published_at, publisher.publisher_slug,
                       publisher.display_name, publisher.origin_label,
                       package.publication_mode,package.verification_status,package.attribution_basis,
                       package.provenance,curator.display_name AS curator_display_name
                FROM ovvaults.marketplace_packages package
                JOIN ovvaults.marketplace_publishers publisher ON publisher.id = package.publisher_id
                LEFT JOIN ovvaults.marketplace_curators curator ON curator.id = package.curator_id
                JOIN ovvaults.marketplace_listing_states listing_state
                  ON listing_state.package_id=package.id AND listing_state.listing_status='active'
                ORDER BY lower(package.manifest->>'displayName'), package.package_slug, package.package_version DESC
            """)
            return [dict(row) for row in cur.fetchall()]


def list_listings() -> list[dict[str, Any]]:
    global _listing_cache
    with _cache_lock:
        cached = _listing_cache
    if cached and time.monotonic() - cached[0] <= CACHE_TTL_SECONDS:
        return json.loads(json.dumps(cached[1]))
    listings = [_dto(row, detail=False) for row in _package_rows()]
    with _cache_lock:
        _listing_cache = (time.monotonic(), listings)
    return json.loads(json.dumps(listings))


def package_row(
    listing_id: str, *, include_avatar: bool = False, include_delisted: bool = False
) -> dict[str, Any] | None:
    listing_id = str(UUID(str(listing_id)))
    cache_key = (listing_id, include_avatar, include_delisted)
    with _cache_lock:
        cached = _package_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] <= CACHE_TTL_SECONDS:
        return dict(cached[1]) if cached[1] is not None else None
    avatar_column = ", package.avatar_body" if include_avatar else ""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT package.id::text AS package_id, package.package_slug,
                       package.package_version, package.manifest, package.manifest_sha256,
                       package.source_hashes, package.avatar_sha256,
                       package.avatar_content_type, octet_length(package.avatar_body) AS avatar_size_bytes,
                       package.published_at, publisher.publisher_slug,
                       publisher.display_name, publisher.origin_label,
                       package.publication_mode,package.verification_status,package.attribution_basis,
                       package.provenance,curator.display_name AS curator_display_name {avatar_column}
                FROM ovvaults.marketplace_packages package
                JOIN ovvaults.marketplace_publishers publisher ON publisher.id = package.publisher_id
                LEFT JOIN ovvaults.marketplace_curators curator ON curator.id = package.curator_id
                JOIN ovvaults.marketplace_listing_states listing_state ON listing_state.package_id=package.id
                WHERE package.id = %s
                  AND (%s OR listing_state.listing_status='active')
            """, (listing_id, include_delisted))
            row = cur.fetchone()
            result = dict(row) if row else None
    with _cache_lock:
        if len(_package_cache) >= 128:
            _package_cache.pop(next(iter(_package_cache)))
        _package_cache[cache_key] = (time.monotonic(), result)
    return dict(result) if result is not None else None


def detail(listing_id: str) -> dict[str, Any] | None:
    row = package_row(listing_id)
    return _dto(row, detail=True) if row else None


def install_preflight(owner_user_id: str, listing_id: str, requested_callsign: str) -> dict[str, Any]:
    if not CALLSIGN_RE.fullmatch(requested_callsign):
        raise ValueError("requestedCallsign is invalid")
    listing_id = str(UUID(str(listing_id)))
    cache_key = (owner_user_id, listing_id, requested_callsign)
    with _cache_lock:
        cached = _preflight_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] <= PREFLIGHT_CACHE_TTL_SECONDS:
        return {**cached[1], "cacheState": "fresh", "refreshing": False}
    package = package_row(listing_id, include_delisted=True)
    if not package:
        raise LookupError("listing not found")
    active_package = package_row(listing_id)
    if not active_package:
        return {"status": "listing_unavailable", "listingStatus": "delisted", "installedConstructId": None,
                "suggestedCallsign": None, "installable": False, "cacheState": "fresh", "refreshing": False}
    base, serial_text = requested_callsign.rsplit("-", 1)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id::text, installed_construct_id FROM ovvaults.marketplace_installations
                WHERE owner_user_id=%s AND package_id=%s AND uninstalled_at IS NULL LIMIT 1
            """, (owner_user_id, listing_id))
            installed = cur.fetchone()
            if installed:
                result = {"status": "already_installed", "installedConstructId": installed["installed_construct_id"], "suggestedCallsign": None, "installable": False}
                with _cache_lock:
                    _preflight_cache[cache_key] = (time.monotonic(), result)
                return {**result, "cacheState": "refreshed", "refreshing": False}
            cur.execute("SELECT DISTINCT construct_id FROM ovvaults.vault_files WHERE user_id=%s AND construct_id LIKE %s", (owner_user_id, f"{base}-%"))
            occupied = {str(row["construct_id"]) for row in cur.fetchall()}
    if requested_callsign not in occupied:
        result = {"status": "available", "installedConstructId": None, "suggestedCallsign": None, "installable": True}
    else:
        serial = int(serial_text)
        suggested = requested_callsign
        while suggested in occupied and serial < 999:
            serial += 1
            suggested = f"{base}-{serial:03d}"
        result = {"status": "owner_collision", "installedConstructId": None, "suggestedCallsign": suggested, "installable": False}
    with _cache_lock:
        if len(_preflight_cache) >= 512:
            _preflight_cache.pop(next(iter(_preflight_cache)))
        _preflight_cache[cache_key] = (time.monotonic(), result)
    return {**result, "cacheState": "refreshed", "refreshing": False}


def install(owner_user_id: str, listing_id: str, requested_callsign: str, idempotency_key: str, privacy: str) -> tuple[dict[str, Any], bool]:
    if not CALLSIGN_RE.fullmatch(requested_callsign):
        raise ValueError("requestedCallsign is invalid")
    if not idempotency_key or len(idempotency_key) > 160:
        raise ValueError("idempotencyKey must contain 1-160 characters")
    if privacy not in {"private", "link", "store"}:
        raise ValueError("privacy must be private, link, or store")
    package = package_row(listing_id, include_avatar=True)
    if not package:
        raise LookupError("listing not found")
    manifest = package["manifest"]
    now = datetime.now(timezone.utc)
    installation_id = str(uuid4())
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"marketplace-install:{owner_user_id}:{requested_callsign}",))
            cur.execute("""SELECT installation.id::text, installation.installed_construct_id,
                       installation.install_receipt, installation.receipt_sha256
                FROM ovvaults.marketplace_installation_events event
                JOIN ovvaults.marketplace_installations installation ON installation.id=event.installation_id
                WHERE event.owner_user_id=%s AND event.idempotency_key=%s LIMIT 1""", (owner_user_id, idempotency_key))
            replay = cur.fetchone()
            if replay:
                replay_receipt = replay["install_receipt"]
                if not isinstance(replay_receipt, dict):
                    replay_receipt = json.loads(replay_receipt)
                conn.commit()
                return {
                    **replay_receipt,
                    "receiptSha256": str(replay["receipt_sha256"]),
                }, True
            cur.execute("""SELECT install_receipt,receipt_sha256
                FROM ovvaults.marketplace_installations
                WHERE owner_user_id=%s AND package_id=%s AND uninstalled_at IS NULL
                FOR UPDATE""", (owner_user_id, listing_id))
            active_install = cur.fetchone()
            if active_install:
                active_receipt = active_install["install_receipt"]
                if not isinstance(active_receipt, dict):
                    active_receipt = json.loads(active_receipt)
                conn.commit()
                return {**active_receipt, "receiptSha256": str(active_install["receipt_sha256"])}, True
            cur.execute("SELECT 1 FROM ovvaults.vault_files WHERE user_id=%s AND construct_id=%s LIMIT 1", (owner_user_id, requested_callsign))
            if cur.fetchone():
                raise FileExistsError(requested_callsign)
            prompt = {
                "schemaId": "life.vvault.identity.prompt", "callsign": requested_callsign,
                "display_name": manifest["displayName"], "description": manifest["description"],
                "instructions": manifest["instructions"], "capabilities": manifest["capabilities"],
                "conversation_starters": manifest["conversationStarters"],
            }
            metadata = {
                "schemaId": "life.vvault.config.metadata", "callsign": requested_callsign,
                "display_name": manifest["displayName"], "description": manifest["description"],
                "construct_category": "user", "lifecycle_stage": "gpt", "privacy": privacy,
                "marketplaceProvenance": {
                    "listingId": listing_id, "packageId": listing_id,
                    "packageVersion": package["package_version"],
                    "publisherLabel": package["display_name"], "originLabel": package["origin_label"],
                    "packageManifestSha256": package["manifest_sha256"],
                    "sourceHashes": manifest["sourceHashes"],
                },
            }
            files: list[tuple[str, str, bytes | None, str, str]] = [
                ("identity/prompt.json", _canonical_json(prompt), None, "application/json", "text"),
                ("config/metadata.json", _canonical_json(metadata), None, "application/json", "text"),
                ("identity/definition.json", _canonical_json({"instructions": manifest["instructions"]}), None, "application/json", "text"),
                ("identity/gender.json", _canonical_json({"gender": manifest["gender"]}), None, "application/json", "text"),
            ]
            if package.get("avatar_body") is not None:
                avatar_bytes = bytes(package["avatar_body"])
                files.append(("identity/avatar.png", base64.b64encode(avatar_bytes).decode("ascii"), avatar_bytes, package["avatar_content_type"], "binary"))
            incarnation = chatty_body_service._ensure_construct_incarnation(
                cur, owner_user_id, requested_callsign, creation_source="marketplace_install"
            )
            incarnation_id = str(incarnation["incarnation_id"])
            for relative_path, content, raw_bytes, content_type, file_type in files:
                full_path = f"instances/{requested_callsign}/{relative_path}"
                digest = _sha(raw_bytes if raw_bytes is not None else content)
                cur.execute("""INSERT INTO ovvaults.vault_files
                    (user_id,bucket,object_key,filename,content_type,size_bytes,sha256,created_at,content,metadata,construct_id,storage_path,file_type,is_system,updated_at)
                    VALUES (%s,'vvault-local',%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,false,%s)""",
                    (owner_user_id,f"users/{owner_user_id}/{full_path}",full_path,content_type,len(raw_bytes if raw_bytes is not None else content.encode()),digest,now,content,_canonical_json({"folder":relative_path.split('/')[0],"marketplacePackageId":listing_id}),requested_callsign,full_path,file_type,now))
            receipt = {"schemaId":"life.vvault.marketplace-install.v1","installationId":installation_id,"listingId":listing_id,"packageId":listing_id,"packageVersion":package["package_version"],"installedConstructId":requested_callsign,"constructCategory":"user","lifecycleStage":"gpt","privacy":privacy,"incarnationId":incarnation_id,"provenance":{"publisherLabel":package["display_name"],"originLabel":package["origin_label"],"packageManifestSha256":package["manifest_sha256"],"sourceHashes":manifest["sourceHashes"]},"installedAt":now.isoformat()}
            receipt_sha = _sha(_canonical_json(receipt))
            cur.execute("""INSERT INTO ovvaults.marketplace_installations
                (id,owner_user_id,package_id,installed_construct_id,privacy,package_manifest_sha256,install_receipt,receipt_sha256,installed_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)""",(installation_id,owner_user_id,listing_id,requested_callsign,privacy,package["manifest_sha256"],_canonical_json(receipt),receipt_sha,now))
            cur.execute("""INSERT INTO ovvaults.marketplace_installation_events
                (installation_id,owner_user_id,event_type,idempotency_key,receipt,receipt_sha256)
                VALUES (%s,%s,'installed',%s,%s::jsonb,%s)""",(installation_id,owner_user_id,idempotency_key,_canonical_json(receipt),receipt_sha))
        conn.commit()
    _invalidate_marketplace_caches(owner_user_id=owner_user_id)
    return {**receipt, "receiptSha256": receipt_sha}, False


def uninstall(
    owner_user_id: str,
    installation_id: str,
    idempotency_key: str,
    community_store_disposition: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Delete only the installed GPT and atomically append its uninstall evidence."""
    installation_id = str(UUID(installation_id))
    if not idempotency_key or len(idempotency_key) > 160:
        raise ValueError("idempotencyKey must contain 1-160 characters")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT receipt FROM ovvaults.marketplace_installation_events
                WHERE owner_user_id=%s AND idempotency_key=%s AND event_type='uninstalled' LIMIT 1""",
                (owner_user_id, idempotency_key))
            replay = cur.fetchone()
            if replay:
                receipt = replay["receipt"] if isinstance(replay["receipt"], dict) else json.loads(replay["receipt"])
                return receipt, 200
            cur.execute("""SELECT installed_construct_id FROM ovvaults.marketplace_installations
                WHERE id=%s AND owner_user_id=%s AND uninstalled_at IS NULL""",
                (installation_id, owner_user_id))
            installation = cur.fetchone()
            if not installation:
                raise LookupError("active installation not found")
            construct_id = str(installation["installed_construct_id"])

    outcome: dict[str, Any] = {}

    def append_uninstall(cur, deletion: dict[str, Any]) -> None:
        cur.execute("""SELECT package_id::text,installed_construct_id,package_manifest_sha256
            FROM ovvaults.marketplace_installations
            WHERE id=%s AND owner_user_id=%s AND uninstalled_at IS NULL FOR UPDATE""",
            (installation_id, owner_user_id))
        active = cur.fetchone()
        if not active or str(active["installed_construct_id"]) != construct_id:
            raise RuntimeError("marketplace installation changed during uninstall")
        now = datetime.now(timezone.utc)
        receipt = {
            "schemaId": "life.vvault.marketplace-uninstall.v1",
            "installationId": installation_id,
            "listingId": str(active["package_id"]),
            "installedConstructId": construct_id,
            "ownerScoped": True,
            "publicListingPreserved": True,
            "vaultFilesDeleted": deletion["vaultFilesDeleted"],
            "transcriptsPreserved": True,
            "uninstalledAt": now.isoformat(),
        }
        receipt_sha = _sha(_canonical_json(receipt))
        receipt["receiptSha256"] = receipt_sha
        cur.execute("""UPDATE ovvaults.marketplace_installations SET uninstalled_at=%s
            WHERE id=%s AND owner_user_id=%s AND uninstalled_at IS NULL""",
            (now, installation_id, owner_user_id))
        if cur.rowcount != 1:
            raise RuntimeError("marketplace installation was not retired")
        cur.execute("""INSERT INTO ovvaults.marketplace_installation_events
            (installation_id,owner_user_id,event_type,idempotency_key,receipt,receipt_sha256)
            VALUES (%s,%s,'uninstalled',%s,%s::jsonb,%s)""",
            (installation_id, owner_user_id, idempotency_key, _canonical_json(receipt), receipt_sha))
        outcome.update(receipt)

    deletion = chatty_body_service.delete_construct(
        construct_id,
        user_id=owner_user_id,
        community_store_disposition=community_store_disposition,
        transaction_callback=append_uninstall,
    )
    if deletion.http_status != 200:
        error = deletion.to_response()[0]
        if deletion.http_status in {403, 409}:
            raise PermissionError(error.get("error_code") or "construct lifecycle prevents uninstall")
        raise RuntimeError(error.get("reason") or "canonical uninstall failed")
    chatty_body_service.invalidate_construct_projection_caches(owner_user_id, construct_id)
    _invalidate_marketplace_caches(owner_user_id=owner_user_id)
    return outcome, 201


def delist(
    listing_id: str,
    *,
    actor_type: str,
    actor_id: str,
    idempotency_key: str,
    reason: str,
) -> tuple[dict[str, Any], bool]:
    """Deactivate one listing while preserving its package and every installation."""
    listing_id = str(UUID(listing_id))
    actor_id = str(UUID(actor_id))
    if actor_type not in {"publisher", "curator", "marketplace_operator"}:
        raise ValueError("actorType is invalid")
    if not idempotency_key or len(idempotency_key) > 160:
        raise ValueError("idempotencyKey must contain 1-160 characters")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"marketplace-delist:{listing_id}",))
            cur.execute("""SELECT receipt FROM ovvaults.marketplace_listing_events
                WHERE package_id=%s AND idempotency_key=%s AND event_type='delisted'""",
                (listing_id, idempotency_key))
            replay = cur.fetchone()
            if replay:
                receipt = replay["receipt"] if isinstance(replay["receipt"], dict) else json.loads(replay["receipt"])
                return receipt, True
            cur.execute("""SELECT package.publisher_id::text,package.curator_id::text,
                       state.listing_status
                FROM ovvaults.marketplace_packages package
                JOIN ovvaults.marketplace_listing_states state ON state.package_id=package.id
                WHERE package.id=%s FOR UPDATE OF state""", (listing_id,))
            package = cur.fetchone()
            if not package:
                raise LookupError("listing not found")
            expected_actor = package["publisher_id"] if actor_type == "publisher" else package["curator_id"]
            if actor_type != "marketplace_operator" and str(expected_actor or "") != actor_id:
                raise PermissionError("actor does not control this listing")
            if package["listing_status"] != "active":
                raise FileExistsError("listing is already delisted")
            now = datetime.now(timezone.utc)
            receipt = {
                "schemaId": "life.vvault.marketplace-delist.v1",
                "listingId": listing_id,
                "listingStatus": "delisted",
                "packagePreserved": True,
                "existingInstallationsPreserved": True,
                "actorType": actor_type,
                "reason": reason[:500],
                "delistedAt": now.isoformat(),
            }
            receipt_sha = _sha(_canonical_json(receipt))
            receipt["receiptSha256"] = receipt_sha
            cur.execute("""UPDATE ovvaults.marketplace_listing_states
                SET listing_status='delisted',delisted_at=%s,delist_receipt_sha256=%s
                WHERE package_id=%s AND listing_status='active'""", (now, receipt_sha, listing_id))
            if cur.rowcount != 1:
                raise RuntimeError("listing state transition failed")
            cur.execute("""INSERT INTO ovvaults.marketplace_listing_events
                (package_id,event_type,actor_type,idempotency_key,receipt,receipt_sha256)
                VALUES (%s,'delisted',%s,%s,%s::jsonb,%s)""",
                (listing_id, actor_type, idempotency_key, _canonical_json(receipt), receipt_sha))
        conn.commit()
    _invalidate_marketplace_caches()
    return receipt, False
