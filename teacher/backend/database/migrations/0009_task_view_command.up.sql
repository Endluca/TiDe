BEGIN;

ALTER TABLE tide.task_command_receipts
    DROP CONSTRAINT task_command_receipts_type_check,
    ADD CONSTRAINT task_command_receipts_type_check
        CHECK (command_type IN ('VIEW', 'START', 'PROGRESS', 'SUBMIT', 'RETRY'));

COMMIT;
