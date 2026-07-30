BEGIN;

DROP TABLE IF EXISTS tide.qa_message_commands;

ALTER TABLE tide.qa_messages
    DROP COLUMN IF EXISTS reason_code;

COMMIT;
