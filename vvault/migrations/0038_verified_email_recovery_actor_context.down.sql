-- OVVAULTS rollback: 0038_verified_email_recovery_actor_context
-- Safe only before any recovery challenge is issued.

ALTER TABLE ovvaults.email_magic_link_challenges
  DROP CONSTRAINT IF EXISTS email_magic_link_challenges_actor_context_check;

ALTER TABLE ovvaults.email_magic_link_challenges
  ADD CONSTRAINT email_magic_link_challenges_actor_context_check
  CHECK (
    (purpose = 'signin'
      AND initiating_user_id IS NULL
      AND initiating_session_id IS NULL)
    OR
    (purpose = 'link'
      AND initiating_user_id IS NOT NULL
      AND initiating_session_id IS NOT NULL)
  );
