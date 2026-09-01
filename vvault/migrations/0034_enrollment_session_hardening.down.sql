-- Enrollment receipts, device bindings, and session evidence are forward-only.
DO $$ BEGIN
  RAISE EXCEPTION 'Refusing destructive rollback of 0034_enrollment_session_hardening';
END $$;
