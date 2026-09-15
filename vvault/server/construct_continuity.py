"""Account-private resolution of globally canonical system constructs.

This module deliberately has no fallback to user-owned ``vault_files``.  A
construct is canonical only after it has a registry entry, a current manifest,
and every required manifest asset has a verified hash.  This prevents a Drive
projection, a cache entry, or a legacy owner row from becoming identity
authority by accident.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping


CONTRACT_VERSION = "life.chatty.construct-continuity/v1"
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_INDETERMINATE = "INDETERMINATE"
DEFAULT_GRANTED_CONSTRUCTS = frozenset({"zen-001"})
IMMUTABLE_FIELDS = frozenset({
    "callsign", "aliases", "name", "displayName", "fullName", "description",
    "gender", "instructions", "systemPrompt", "coreInstructions",
    "constructClass", "canonicalAssetReferences", "identityManifest",
})
OVERLAY_FIELDS = frozenset({
    "definition", "conditioning", "avatarPresentation", "voiceProfile",
    "physicalFeatures", "interactionPreferences",
})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ConstructContinuityError(ValueError):
    """Raised for invalid account overlay or manifest input."""


def normalize_callsign(value: object) -> str:
    return str(value or "").strip().lower()


def validate_overlay_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Return an approved private overlay or reject reserved identity fields."""
    if not isinstance(fields, Mapping):
        raise ConstructContinuityError("overlay_fields_must_be_an_object")
    keys = {str(key) for key in fields}
    reserved = sorted(keys & IMMUTABLE_FIELDS)
    unsupported = sorted(keys - OVERLAY_FIELDS)
    if reserved:
        raise ConstructContinuityError("immutable_construct_fields:" + ",".join(reserved))
    if unsupported:
        raise ConstructContinuityError("unsupported_overlay_fields:" + ",".join(unsupported))
    return {str(key): value for key, value in fields.items()}


def scoped_cache_key(
    *, construct_id: str, manifest_version: int, account_user_id: str,
    relation_id: str, overlay_version: int, relying_party_id: str,
) -> str:
    """Build a non-shareable projection cache key for a construct relation."""
    canonical = {
        "constructId": normalize_callsign(construct_id),
        "manifestVersion": int(manifest_version),
        "accountUserId": str(account_user_id),
        "relationId": str(relation_id),
        "overlayVersion": int(overlay_version),
        "relyingPartyId": str(relying_party_id),
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"construct-continuity:v1:{digest}"


@dataclass(frozen=True)
class ContinuityResolution:
    status: str
    code: str
    construct_id: str | None = None
    payload: Mapping[str, Any] | None = None

    @property
    def ready(self) -> bool:
        return self.status == STATUS_PASS

    def evidence(self) -> dict[str, Any]:
        return {
            "contract": CONTRACT_VERSION,
            "status": self.status,
            "code": self.code,
            "constructId": self.construct_id,
            **dict(self.payload or {}),
        }


class PostgresConstructContinuityRepository:
    """Small read/write repository for the additive continuity tables.

    The caller must have already set both verified request context values on
    the connection.  The migration's RLS policies are the database backstop;
    no caller-provided account identifier is trusted as authentication.
    """

    def __init__(self, connect: Callable[..., Any]):
        self._connect = connect

    def resolve(
        self, *, requested_callsign: str, account_user_id: str,
        relying_party_id: str, auto_provision: bool = True,
    ) -> ContinuityResolution:
        alias = normalize_callsign(requested_callsign)
        if not alias or not str(account_user_id).strip():
            return ContinuityResolution(STATUS_FAIL, "INVALID_RESOLUTION_REQUEST")
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT principal.construct_id, principal.availability_state,
                               manifest.version, manifest.manifest_sha256,
                               manifest.source_release_sha256,
                               manifest.immutable_profile,
                               relation.id::text AS relation_id,
                               relation.grant_state, relation.overlay_version,
                               overlay.fields AS overlay_fields
                        FROM ovvaults.construct_aliases alias
                        JOIN ovvaults.construct_principals principal
                          ON principal.construct_id = alias.construct_id
                        JOIN ovvaults.construct_identity_manifests manifest
                          ON manifest.construct_id = principal.construct_id
                         AND manifest.state = 'ACTIVE'
                        LEFT JOIN ovvaults.account_construct_relations relation
                          ON relation.construct_id = principal.construct_id
                         AND relation.account_user_id = %s
                         AND relation.grant_state = 'ACTIVE'
                        LEFT JOIN LATERAL (
                          SELECT fields FROM ovvaults.account_construct_overlays
                          WHERE relation_id = relation.id AND state = 'ACTIVE'
                          ORDER BY version DESC LIMIT 1
                        ) overlay ON true
                        WHERE alias.alias = %s
                        """,
                        (account_user_id, alias),
                    )
                    row = cur.fetchone()
                    if not row:
                        return ContinuityResolution(STATUS_INDETERMINATE, "CANONICAL_CONSTRUCT_UNAVAILABLE")
                    row = dict(row)
                    construct_id = str(row["construct_id"])
                    if row["availability_state"] != "AVAILABLE":
                        return ContinuityResolution(STATUS_FAIL, "CONSTRUCT_NOT_AVAILABLE", construct_id)
                    if not _SHA256.fullmatch(str(row["manifest_sha256"] or "")):
                        return ContinuityResolution(STATUS_FAIL, "MANIFEST_HASH_INVALID", construct_id)
                    if not _SHA256.fullmatch(str(row["source_release_sha256"] or "")):
                        return ContinuityResolution(STATUS_INDETERMINATE, "SOURCE_PROVENANCE_AMBIGUOUS", construct_id)
                    cur.execute(
                        """
                        SELECT asset_key, expected_sha256, observed_sha256, required
                        FROM ovvaults.construct_identity_manifest_assets
                        WHERE construct_id = %s AND manifest_version = %s
                        ORDER BY asset_key
                        """,
                        (construct_id, row["version"]),
                    )
                    assets = [dict(asset) for asset in cur.fetchall()]
                    if not assets or any(
                        asset["required"] and (
                            not _SHA256.fullmatch(str(asset["expected_sha256"] or ""))
                            or asset["expected_sha256"] != asset["observed_sha256"]
                        )
                        for asset in assets
                    ):
                        return ContinuityResolution(STATUS_FAIL, "CANONICAL_ASSET_INTEGRITY_FAILED", construct_id)
                    if not row["relation_id"] and auto_provision and construct_id in DEFAULT_GRANTED_CONSTRUCTS:
                        cur.execute(
                            """
                            INSERT INTO ovvaults.account_construct_relations
                              (account_user_id, construct_id, grant_state, grant_source)
                            VALUES (%s, %s, 'ACTIVE', 'DEFAULT_SYSTEM_GRANT')
                            ON CONFLICT (account_user_id, construct_id) DO UPDATE
                              SET grant_state = 'ACTIVE', updated_at = now()
                            RETURNING id::text AS relation_id, overlay_version
                            """,
                            (account_user_id, construct_id),
                        )
                        relation = dict(cur.fetchone())
                        row.update(relation)
                        row["overlay_fields"] = {}
                    if not row["relation_id"]:
                        return ContinuityResolution(STATUS_FAIL, "ACCOUNT_CONSTRUCT_NOT_AUTHORIZED", construct_id)
                conn.commit()
        except Exception as exc:
            # A missing migration, unavailable schema, or failed RLS context is
            # evidence insufficiency, never a reason to fall back to legacy rows.
            return ContinuityResolution(
                STATUS_INDETERMINATE, "CONTINUITY_SCHEMA_OR_RLS_UNAVAILABLE",
                payload={"errorType": type(exc).__name__},
            )
        try:
            overlay = validate_overlay_fields(row.get("overlay_fields") or {})
        except ConstructContinuityError:
            return ContinuityResolution(
                STATUS_FAIL, "ACCOUNT_OVERLAY_POLICY_INVALID", construct_id
            )
        return ContinuityResolution(
            STATUS_PASS,
            "CANONICAL_RELATION_RESOLVED",
            construct_id,
            payload={
                "manifestVersion": int(row["version"]),
                "manifestSha256": row["manifest_sha256"],
                "sourceReleaseSha256": row["source_release_sha256"],
                "immutableProfile": row["immutable_profile"],
                "relationId": row["relation_id"],
                "overlayVersion": int(row.get("overlay_version") or 0),
                "overlay": overlay,
                "relyingPartyId": relying_party_id,
                "assets": [
                    {"assetKey": asset["asset_key"], "sha256": asset["expected_sha256"]}
                    for asset in assets
                ],
            },
        )
