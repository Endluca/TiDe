BEGIN;

-- Forward-only business-content migration. Reverting the step/rule definitions
-- would make already-recorded three-section progress ambiguous. Restore from a
-- reviewed backup with an explicit follow-up migration if rollback is required.
DO $$
BEGIN
    RAISE NOTICE
        '0031_g04_independent_sections is forward-only; no catalog or progress rows were changed by down';
END
$$;

COMMIT;
