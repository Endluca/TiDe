BEGIN;

-- Forward-only business-identity migration. Restoring the old task codes would
-- route current assignments to the wrong teacher flows and detach their saved
-- progress from the shared business semantics.
DO $$
BEGIN
    RAISE NOTICE
        '0025 is forward-only; current G01-G09 teacher execution semantics remain active';
END
$$;

COMMIT;
