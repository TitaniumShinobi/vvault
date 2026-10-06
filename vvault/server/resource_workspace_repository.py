"""Owner-scoped, read-only workspace resolution for AUTH resource grants."""

from __future__ import annotations

from typing import Any

from vvault.server import chatty_body_service
from vvault.server.relying_party_scope import configure_connection
from vvault.server.resource_authorization import (
    REQUIRED_MIGRATION,
    WIRE_CONTRACT,
    WORKSPACE_RESOLVE_CAPABILITY,
)


class ResourceWorkspaceRepository:
    def _connect(self):
        return chatty_body_service._connect()

    def readiness(self) -> dict[str, bool]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                configure_connection(cur)
                cur.execute(
                    """
                    SELECT EXISTS(
                      SELECT 1 FROM ovvaults.vvault_schema_migrations
                      WHERE migration_id=%s AND rolled_back_at IS NULL
                    ) AS migration_applied,
                    EXISTS(
                      SELECT 1 FROM ovvaults.resource_application_admissions
                      WHERE client_id='grid-windows' AND application_id='grid'
                        AND relying_party_id='grid'
                        AND contract_version=%s
                        AND capabilities=ARRAY[%s]::text[]
                    ) AS admission_consistent,
                    EXISTS(
                      SELECT 1 FROM ovvaults.resource_application_admissions
                      WHERE client_id='grid-windows' AND application_id='grid'
                        AND relying_party_id='grid' AND enabled
                    ) AS admission_enabled
                    """,
                    (REQUIRED_MIGRATION, WIRE_CONTRACT, WORKSPACE_RESOLVE_CAPABILITY),
                )
                row = cur.fetchone()
        values = dict(row) if isinstance(row, dict) else dict(zip(
            ("migration_applied", "admission_consistent", "admission_enabled"), row or (), strict=False
        ))
        return {
            "migrationApplied": bool(values.get("migration_applied")),
            "admissionConsistent": bool(values.get("admission_consistent")),
            "admissionEnabled": bool(values.get("admission_enabled")),
        }

    def resolve(self, *, owner_user_id: str, client_id: str, application_id: str) -> dict[str, Any] | None:
        """Resolve an existing workspace. This method never inserts or updates."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                configure_connection(cur)
                cur.execute(
                    """
                    SELECT w.workspace_id, w.lifecycle_status, w.capabilities,
                           a.application_id, a.relying_party_id
                    FROM ovvaults.owner_workspaces w
                    JOIN ovvaults.users u ON u.id=w.owner_user_id
                    JOIN ovvaults.resource_application_admissions a
                      ON a.client_id=%s AND a.application_id=%s
                     AND a.relying_party_id=%s AND a.enabled
                    WHERE w.owner_user_id=%s::uuid
                      AND COALESCE(to_jsonb(u)->>'account_state',
                                   to_jsonb(u)->>'enrollment_status')='ACTIVE'
                    LIMIT 1
                    """,
                    (client_id, application_id, application_id, owner_user_id),
                )
                row = cur.fetchone()
        if not row:
            return None
        value = dict(row) if isinstance(row, dict) else dict(zip(
            ("workspace_id", "lifecycle_status", "capabilities", "application_id", "relying_party_id"),
            row,
            strict=False,
        ))
        capabilities = sorted(set(value.get("capabilities") or []))
        return {
            "workspaceId": str(value["workspace_id"]),
            "lifecycleStatus": str(value["lifecycle_status"]),
            "applicationId": str(value["application_id"]),
            "relyingPartyId": str(value["relying_party_id"]),
            "capabilities": capabilities,
        }


RESOURCE_WORKSPACE_REPOSITORY = ResourceWorkspaceRepository()
