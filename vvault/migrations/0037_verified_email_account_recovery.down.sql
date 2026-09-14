-- OVVAULTS rollback: 0037_verified_email_account_recovery
-- Safe only before any recovery challenge is issued.

ALTER TABLE ovvaults.email_magic_link_challenges
  DROP CONSTRAINT IF EXISTS email_magic_link_challenges_purpose_check;

ALTER TABLE ovvaults.email_magic_link_challenges
  ADD CONSTRAINT email_magic_link_challenges_purpose_check
  CHECK (purpose IN ('signin', 'link'));
