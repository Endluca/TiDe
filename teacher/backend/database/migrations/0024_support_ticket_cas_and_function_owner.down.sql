BEGIN;

-- Forward-only security migration. Deliberately keep NULL-safe CAS checks and
-- the non-login SECURITY DEFINER owner instead of restoring the vulnerability.
DO $$
BEGIN
    RAISE NOTICE
        '0024 is forward-only; secure CAS and function ownership remain active';
END
$$;

COMMIT;
