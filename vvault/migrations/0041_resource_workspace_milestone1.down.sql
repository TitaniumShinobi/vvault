-- Rollback disables the additive resolver schema. It does not delete workspaces.
UPDATE ovvaults.resource_application_admissions
SET enabled=false, updated_at=now()
WHERE client_id='grid-windows' AND application_id='grid';
