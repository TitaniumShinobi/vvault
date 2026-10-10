"""Emit bounded, read-only evidence for the production authentication contract.

This helper intentionally uses the serving application's configured canonical
database connection.  It never changes database state and never prints
credentials, session tokens, or user records.
"""

import json

from vvault.server import chatty_body_service as body


MIGRATION_VERSION = "0039"
MIGRATION_ARTIFACT = "0039_returning_owner_session_without_device_gate.up.sql"
DEVON_EMAIL = "dwoodson92@gmail.com"


with body._connect() as connection:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH function_contract AS (
              SELECT pg_get_functiondef(
                'ovvaults.validate_enrollment_session()'::regprocedure
              ) AS definition
            )
            SELECT
              current_database() AS database,
              current_schema() AS schema,
              to_regclass('ovvaults.users') IS NOT NULL AS users_table,
              to_regclass('ovvaults.sessions') IS NOT NULL AS sessions_table,
              to_regclass('ovvaults.enrollment_schema_migrations') IS NOT NULL AS ledger_table,
              (SELECT checksum
                 FROM ovvaults.enrollment_schema_migrations
                WHERE version = %s) AS migration_checksum,
              position(
                'NEW.enrollment_session_kind = ''NORMAL'' AND NEW.enrollment_device_id IS NULL'
                in definition
              ) > 0 AS normal_unbound_semantics,
              position('account_state_value <> ''ACTIVE''' in definition) > 0 AS active_owner_required,
              EXISTS (
                SELECT 1
                  FROM pg_trigger
                 WHERE tgrelid = 'ovvaults.sessions'::regclass
                   AND NOT tgisinternal
                   AND pg_get_triggerdef(oid) LIKE '%%validate_enrollment_session%%'
              ) AS sessions_trigger,
              (SELECT count(*) FROM ovvaults.users
                WHERE lower(email) = %s) AS devon_user_count,
              (SELECT jsonb_object_agg(state_counts.account_state, state_counts.owner_count)
                 FROM (
                   SELECT account_state, count(*) AS owner_count
                     FROM ovvaults.users
                    WHERE lower(email) = %s
                    GROUP BY account_state
                 ) AS state_counts) AS devon_user_state_counts,
              (SELECT count(*)
                 FROM ovvaults.external_identities AS identity
                 JOIN ovvaults.users AS owner ON owner.id = identity.user_id
                WHERE lower(owner.email) = %s
                  AND identity.revoked_at IS NULL) AS devon_active_identity_count,
              (SELECT count(DISTINCT identity.user_id)
                 FROM ovvaults.external_identities AS identity
                 JOIN ovvaults.users AS owner ON owner.id = identity.user_id
                WHERE lower(owner.email) = %s
                  AND identity.revoked_at IS NULL) AS devon_active_identity_owner_count
              FROM function_contract
            """,
            (MIGRATION_VERSION, DEVON_EMAIL, DEVON_EMAIL, DEVON_EMAIL, DEVON_EMAIL),
        )
        row = cursor.fetchone()


if hasattr(row, "keys"):
    evidence = dict(row)
else:
    evidence = dict(zip((column.name for column in cursor.description), row))
evidence["migration_artifact"] = MIGRATION_ARTIFACT
evidence["migration_version"] = MIGRATION_VERSION
print(json.dumps(evidence, sort_keys=True))
