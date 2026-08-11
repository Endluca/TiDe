BEGIN;

-- Forward-only business-content migration. Reverting the execution definition
-- could make already-recorded document progress ambiguous. Restore through a
-- reviewed follow-up migration if a rollback is required.
DO $$
BEGIN
    RAISE NOTICE
        '0039_g02_policy_document is forward-only; no catalog, assignment or progress rows were changed by down';
END
$$;

COMMIT;
