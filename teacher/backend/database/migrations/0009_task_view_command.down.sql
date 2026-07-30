BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM tide.task_command_receipts WHERE command_type = 'VIEW') THEN
        RAISE EXCEPTION 'VIEW command receipts must be retained or migrated before rolling back 0009';
    END IF;
END
$$;

ALTER TABLE tide.task_command_receipts
    DROP CONSTRAINT task_command_receipts_type_check,
    ADD CONSTRAINT task_command_receipts_type_check
        CHECK (command_type IN ('START', 'PROGRESS', 'SUBMIT', 'RETRY'));

COMMIT;
