"""Canonical, non-mutating owner-admission repository."""

from __future__ import annotations

from typing import Any

from vvault.server import chatty_body_service
from vvault.server.relying_party_scope import configure_connection
from vvault.server.resource_owner_admission import CONTRACT, REQUIRED_MIGRATION


class ResourceOwnerAdmissionRepository:
    def _connect(self):
        return chatty_body_service._connect()

    def readiness(self) -> dict[str, bool]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                configure_connection(cur)
                cur.execute("""
                    SELECT EXISTS(SELECT 1 FROM ovvaults.vvault_schema_migrations
                                   WHERE migration_id=%s AND rolled_back_at IS NULL),
                           to_regclass('ovvaults.auth_resource_owner_bindings') IS NOT NULL
                """, (REQUIRED_MIGRATION,))
                row = cur.fetchone() or (False, False)
        values = tuple(row.values()) if isinstance(row, dict) else tuple(row)
        return {"migrationApplied": bool(values[0]), "bindingSourceAvailable": bool(values[1])}

    def lookup(self, **query: str) -> dict[str, Any]:
        """Read one explicit binding and current owner/application state; never write."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                configure_connection(cur)
                cur.execute("""
                    SELECT b.owner_user_id, b.binding_status, b.policy_version,
                           COALESCE(to_jsonb(u)->>'account_state', to_jsonb(u)->>'enrollment_status') owner_state
                      FROM ovvaults.auth_resource_owner_bindings b
                      JOIN ovvaults.users u ON u.id=b.owner_user_id
                      JOIN ovvaults.resource_application_admissions a
                        ON a.client_id=%s AND a.application_id=%s AND a.enabled
                       AND a.contract_version='life.vvault.resource-workspace/v1'
                       AND %s=ANY(a.capabilities)
                     WHERE b.auth_issuer=%s AND b.auth_subject=%s
                       AND b.audience=%s AND (b.valid_until IS NULL OR b.valid_until>now())
                     LIMIT 2
                """, (query["client_id"], query["application_id"], query["capability"],
                      query["issuer"], query["subject"], query["audience"]))
                rows = cur.fetchall()
        if not rows:
            return {"state": "UNKNOWN"}
        if len(rows) != 1:
            return {"state": "CONFLICT"}
        row = dict(rows[0]) if isinstance(rows[0], dict) else dict(zip(
            ("owner_user_id", "binding_status", "policy_version", "owner_state"), rows[0], strict=False
        ))
        binding = str(row.get("binding_status") or "")
        owner = str(row.get("owner_state") or "")
        if binding == "PENDING" or owner == "PENDING_ENROLLMENT":
            return {"state": "PENDING"}
        if binding == "REVOKED":
            return {"state": "REVOKED"}
        if binding == "DISABLED" or owner not in {"ACTIVE"}:
            return {"state": "DISABLED"}
        if binding != "ACTIVE":
            return {"state": "ERROR"}
        return {"state": "ACTIVE", "ownerId": str(row["owner_user_id"]),
                "policyVersion": str(row["policy_version"])}


RESOURCE_OWNER_ADMISSION_REPOSITORY = ResourceOwnerAdmissionRepository()
