BEGIN;

DROP TRIGGER IF EXISTS protect_system_notification_content
    ON tide.system_notifications;
DROP FUNCTION IF EXISTS tide.protect_system_notification_content();

DROP TRIGGER IF EXISTS protect_system_notification_publication
    ON tide.system_notification_publications;
DROP FUNCTION IF EXISTS tide.protect_system_notification_publication();

DROP INDEX IF EXISTS tide.system_notifications_teacher_issued_idx;

ALTER TABLE tide.system_notifications
    DROP CONSTRAINT IF EXISTS system_notifications_action_target_check,
    DROP CONSTRAINT IF EXISTS system_notifications_action_type_check,
    DROP CONSTRAINT IF EXISTS system_notifications_publication_id_fkey,
    DROP COLUMN IF EXISTS issued_at,
    DROP COLUMN IF EXISTS publication_id;

DROP TABLE IF EXISTS tide.system_notification_publications;

DROP INDEX IF EXISTS public.ix_notifications_teacher_requested_desc;
DROP INDEX IF EXISTS public.uq_notification_events_notification_request_hash;

COMMIT;
