BEGIN;

-- Forward-only business-content migration. Recreating the device-check
-- definition would make retired progress active again and could regress an
-- already-completed assignment. Use a reviewed follow-up migration instead.
DO $$
BEGIN
    RAISE NOTICE
        '0037_g04_remove_device_check is forward-only; no catalog, assignment, progress or evidence rows were changed by down';
END
$$;

COMMIT;
