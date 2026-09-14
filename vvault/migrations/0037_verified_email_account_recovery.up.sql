-- OVVAULTS migration: 0037_verified_email_account_recovery
-- Allow a consumed verified-email challenge to be explicitly classified as a
-- recovery ceremony. It creates no accounts and changes no existing data.

ALTER TABLE ovvaults.email_magic_link_challenges
  DROP CONSTRAINT IF EXISTS email_magic_link_challenges_purpose_check;
ALTER TABLE ovvaults.email_magic_link_challenges
  ADD CONSTRAINT email_magic_link_challenges_purpose_check
  CHECK (purpose IN ('signin', 'link', 'recovery'));

-- The original actor-context constraint allowed an unbound sign-in challenge
-- and a bound link challenge only. Recovery is likewise unbound: its owner is
-- resolved only after the one-time challenge is consumed.
DO $$
DECLARE constraint_name TEXT;
BEGIN
  FOR constraint_name IN
    SELECT conname
      FROM pg_constraint
     WHERE conrelid = 'ovvaults.email_magic_link_challenges'::regclass
       AND contype = 'c'
       AND pg_get_constraintdef(oid) ILIKE '%initiating_user_id%'
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
