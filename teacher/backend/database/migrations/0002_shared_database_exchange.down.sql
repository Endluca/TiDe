BEGIN;

DROP VIEW IF EXISTS tide.shiwen_fixed_task_completion_events_v1;
DROP VIEW IF EXISTS tide.shiwen_personalized_status_events_v1;
DROP TRIGGER IF EXISTS integration_events_immutable_guard ON tide.integration_events;
DROP FUNCTION IF EXISTS tide.reject_integration_event_mutation();
DROP TRIGGER IF EXISTS integration_events_task_reference_guard ON tide.integration_events;
DROP FUNCTION IF EXISTS tide.validate_integration_event_task_reference();
ALTER TABLE tide.integration_events
    DROP CONSTRAINT IF EXISTS integration_events_fixed_code_check,
    DROP CONSTRAINT IF EXISTS integration_events_result_code_check,
    DROP CONSTRAINT IF EXISTS integration_events_reason_code_check,
    DROP CONSTRAINT IF EXISTS integration_events_personalized_status_check;
COMMENT ON TABLE tide.integration_events IS NULL;

COMMIT;
