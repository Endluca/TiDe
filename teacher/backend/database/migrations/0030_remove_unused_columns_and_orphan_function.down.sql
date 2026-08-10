BEGIN;

SET LOCAL lock_timeout = '10s';

LOCK TABLE tide.file_objects IN ACCESS EXCLUSIVE MODE;

ALTER TABLE tide.file_objects
    ADD COLUMN visibility text NOT NULL DEFAULT 'PRIVATE',
    ADD CONSTRAINT file_objects_visibility_check
        CHECK (visibility IN ('PRIVATE', 'PUBLIC_ASSET'));

CREATE FUNCTION tide.enforce_outbox_target()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    event_origin text;
BEGIN
    SELECT data_origin INTO event_origin
    FROM tide.integration_events
    WHERE id = NEW.integration_event_id;

    IF event_origin = 'MOCK' AND NEW.target_system <> 'SHIWEN_MOCK' THEN
        RAISE EXCEPTION 'mock integration events can only target SHIWEN_MOCK';
    END IF;

    RETURN NEW;
END;
$$;

COMMIT;
