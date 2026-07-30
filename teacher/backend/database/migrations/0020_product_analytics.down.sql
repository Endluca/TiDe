BEGIN;

DROP VIEW IF EXISTS tide.analytics_task_business_change_v1;
DROP VIEW IF EXISTS tide.analytics_help_usage_v1;
DROP VIEW IF EXISTS tide.analytics_technical_quality_v1;
DROP VIEW IF EXISTS tide.analytics_content_quality_v1;
DROP VIEW IF EXISTS tide.analytics_task_step_funnel_v1;
DROP VIEW IF EXISTS tide.analytics_task_funnel_v1;
DROP VIEW IF EXISTS tide.analytics_task_assignment_funnel_v1;
DROP VIEW IF EXISTS tide.analytics_actor_task_journey_v1;

DROP INDEX IF EXISTS tide.app_events_task_code_version_time_idx;
DROP INDEX IF EXISTS tide.app_events_session_time_idx;
DROP INDEX IF EXISTS tide.app_events_task_name_time_idx;
DROP INDEX IF EXISTS tide.app_events_name_time_idx;
DROP INDEX IF EXISTS tide.app_events_anonymous_event_key;

DELETE FROM tide.app_events WHERE teacher_binding_id IS NULL;

ALTER TABLE tide.app_events
    DROP CONSTRAINT IF EXISTS app_events_session_check,
    DROP CONSTRAINT IF EXISTS app_events_anonymous_teacher_check,
    DROP CONSTRAINT IF EXISTS app_events_source_check,
    DROP CONSTRAINT IF EXISTS app_events_schema_version_check,
    DROP COLUMN IF EXISTS session_id,
    DROP COLUMN IF EXISTS event_source,
    DROP COLUMN IF EXISTS event_schema_version,
    DROP COLUMN IF EXISTS anonymous_teacher_id,
    ALTER COLUMN teacher_binding_id SET NOT NULL;

COMMIT;
