-- Refuse rollback: identity bindings and one-time authentication evidence must
-- not be destructively removed.  Restore only from a verified database backup.
DO $$ BEGIN
  RAISE EXCEPTION 'Refusing destructive rollback of 0033_identity_directory';
END $$;
