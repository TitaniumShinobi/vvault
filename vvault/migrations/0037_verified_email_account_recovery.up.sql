-- OVVAULTS migration: 0037_verified_email_account_recovery
-- Add the sole extra purpose needed by verified-email device-factor recovery.
-- No user, Vault, identity, or session rows are changed by this migration.

ALTER TABLE ovvaults.email_magic_link_challenges
  DROP CONSTRAINT IF EXISTS email_magic_link_challenges_purpose_check;

ALTER TABLE ovvaults.email_magic_link_challenges
  ADD CONSTRAINT email_magic_link_challenges_purpose_check
  CHECK (purpose IN ('signin', 'link', 'recovery'));
