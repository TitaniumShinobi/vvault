"""Canonical LIFE construct identity and Memup capsule resolver.

This is the named authority boundary used by authenticated VVAULT routes. It
never reads local instance folders or legacy Supabase projections: resolution
delegates only to the OVVAULTS-backed body repository and rejects responses
that are not explicitly body-native.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vvault.server import chatty_body_service
from vvault.server.construct_continuity import (
    ContinuityResolution,
    PostgresConstructContinuityRepository,
)
from vvault.server.artifact_contract import (
    CAPSULE_ARTIFACT_ID,
    CONTRACT_ID,
    CONTRACT_VERSION,
    PROMPT_ARTIFACT_ID,
    storage_path,
)


@dataclass(frozen=True)
class LifeCapsuleResolution:
    callsign: str
    identity: chatty_body_service.BodyResult
    capsule: chatty_body_service.BodyResult

    @property
    def ready(self) -> bool:
        return (
            self.identity.status == "body_native"
            and self.capsule.status == "body_native"
            and self.identity.payload.get("body_source") == "ovvaults.vault_files"
            and self.capsule.payload.get("body_source") == "ovvaults.vault_files"
            and self.capsule.payload.get("storage_path")
            == storage_path(CAPSULE_ARTIFACT_ID, self.callsign)
        )

    def evidence(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "callsign": self.callsign,
            "authority": "vvault_body",
            "schema": "ovvaults",
            "storage_owner": "ovvaults.vault_files",
            "contract_id": CONTRACT_ID,
            "contract_version": CONTRACT_VERSION,
            "identity_artifact_id": PROMPT_ARTIFACT_ID,
            "capsule_artifact_id": CAPSULE_ARTIFACT_ID,
            "identity_status": self.identity.status,
            "capsule_status": self.capsule.status,
            "identity_source": self.identity.payload.get("body_source"),
            "capsule_source": self.capsule.payload.get("body_source"),
            "capsule_storage_path": self.capsule.payload.get("storage_path"),
            "capsule_sha256": self.capsule.payload.get("sha256"),
        }


def resolve_identity(construct_id: str) -> chatty_body_service.BodyResult:
    """Resolve canonical construct identity exclusively from OVVAULTS."""
    return chatty_body_service.identity(construct_id)


def resolve_capsule(construct_id: str) -> chatty_body_service.BodyResult:
    """Resolve the exact canonical Memup capsule exclusively from OVVAULTS."""
    return chatty_body_service.canonical_capsule(construct_id)


def resolve_life_capsule(construct_id: str) -> LifeCapsuleResolution:
    callsign = chatty_body_service.normalize_callsign(construct_id)
    return LifeCapsuleResolution(
        callsign=callsign,
        identity=resolve_identity(callsign),
        capsule=resolve_capsule(callsign),
    )


def resolve_account_construct(
    construct_id: str,
    *,
    account_user_id: str,
    relying_party_id: str,
    auto_provision: bool = True,
) -> ContinuityResolution:
    """Resolve a global principal plus exactly one private account relation.

    This v1 boundary never falls back to account-owned legacy files.  Callers
    must handle INDETERMINATE until the additive schema and signed release are
    available.
    """
    repository = PostgresConstructContinuityRepository(chatty_body_service._connect)
    return repository.resolve(
        requested_callsign=construct_id,
        account_user_id=account_user_id,
        relying_party_id=relying_party_id,
        auto_provision=auto_provision,
    )
