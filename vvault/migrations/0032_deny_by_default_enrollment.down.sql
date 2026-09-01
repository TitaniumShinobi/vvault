-- Enrollment receipts, credentials, devices, and revocations are security
-- evidence.  Rollback would discard that evidence and is intentionally barred.
DO $$
BEGIN
  RAISE EXCEPTION 'Refusing destructive rollback: preserve enrollment security evidence with a reviewed forward migration';
END;
$$;
