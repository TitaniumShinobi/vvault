-- OVVAULTS rollback: 0036_enrollment_device_transfer_codes
-- Roll back only before any transfer code is issued. Once used, retain the
-- security evidence and disable the endpoint rather than deleting evidence.

DROP TABLE IF EXISTS ovvaults.enrollment_device_transfer_codes;
