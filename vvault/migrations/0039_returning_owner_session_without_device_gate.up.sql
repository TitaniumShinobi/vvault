-- Returning owners authenticate with a verified provider identity. A trusted
-- browser remains an optional session binding, not a second sign-in gate.
CREATE OR REPLACE FUNCTION ovvaults.validate_enrollment_session()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  device_owner UUID;
  device_status TEXT;
  account_state_value TEXT;
BEGIN
  IF NEW.enrollment_session_kind = 'LEGACY' THEN
    RETURN NEW;
  END IF;

  SELECT account_state INTO account_state_value
  FROM ovvaults.users WHERE id = NEW.user_id;

  -- A NORMAL session created after verified identity sign-in may be unbound.
  -- If a device is supplied, it must still belong to the owner and be trusted.
  IF NEW.enrollment_session_kind = 'NORMAL' AND NEW.enrollment_device_id IS NULL THEN
    IF account_state_value <> 'ACTIVE' THEN
      RAISE EXCEPTION 'normal session requires active account';
    END IF;
    RETURN NEW;
  END IF;

  IF NEW.enrollment_device_id IS NULL THEN
    RAISE EXCEPTION 'enrollment session requires a bound device';
  END IF;
  SELECT user_id, status INTO device_owner, device_status
  FROM ovvaults.enrollment_devices WHERE id = NEW.enrollment_device_id;
  IF device_owner IS NULL OR device_owner <> NEW.user_id THEN
    RAISE EXCEPTION 'enrollment session device owner mismatch';
  END IF;
  IF NEW.enrollment_session_kind = 'PENDING_ENROLLMENT'
     AND (device_status <> 'PENDING' OR account_state_value <> 'PENDING_ENROLLMENT') THEN
    RAISE EXCEPTION 'pending enrollment session requires pending account and device';
  END IF;
  IF NEW.enrollment_session_kind = 'PENDING_DEVICE'
     AND (device_status <> 'PENDING' OR account_state_value <> 'ACTIVE') THEN
    RAISE EXCEPTION 'pending device session requires active account and pending device';
  END IF;
  IF NEW.enrollment_session_kind = 'NORMAL'
     AND (device_status <> 'TRUSTED' OR account_state_value <> 'ACTIVE') THEN
    RAISE EXCEPTION 'normal session requires active account and trusted device';
  END IF;
  RETURN NEW;
END;
$$;
