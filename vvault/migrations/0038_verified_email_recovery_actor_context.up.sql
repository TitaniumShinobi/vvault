-- OVVAULTS migration: 0038_verified_email_recovery_actor_context
-- Recovery links are unauthenticated, verified-email ceremonies.  They must
-- therefore have the same null initiating actor context as sign-in links.
-- Keep link ceremonies bound to an authenticated user/session pair.

DO $$
DECLARE
  constraint_name text;
BEGIN
  FOR constraint_name IN
    SELECT conname
    FROM pg_constraint
    WHERE conrelid = 'ovvaults.email_magic_link_challenges'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%initiating_user_id%'
      AND pg_get_constraintdef(oid) LIKE '%purpose%'
  LOOP
    EXECUTE format(
      'ALTER TABLE ovvaults.email_magic_link_challenges DROP CONSTRAINT %I',
      constraint_name
    );
  END LOOP;
END $$;

ALTER TABLE ovvaults.email_magic_link_challenges
  ADD CONSTRAINT email_magic_link_challenges_actor_context_check
  CHECK (
    (purpose IN ('signin', 'recovery')
      AND initiating_user_id IS NULL
      AND initiating_session_id IS NULL)
    OR
    (purpose = 'link'
      AND initiating_user_id IS NOT NULL
      AND initiating_session_id IS NOT NULL)
  );
