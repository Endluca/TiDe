BEGIN;

CREATE OR REPLACE FUNCTION tide.protect_system_notification_content()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, tide
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;

    IF NEW.system_notification_id IS DISTINCT FROM OLD.system_notification_id
       OR NEW.publication_id IS DISTINCT FROM OLD.publication_id
       OR NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
       OR NEW.type_code IS DISTINCT FROM OLD.type_code
       OR NEW.title IS DISTINCT FROM OLD.title
       OR NEW.body IS DISTINCT FROM OLD.body
       OR NEW.action_type IS DISTINCT FROM OLD.action_type
       OR NEW.action_target IS DISTINCT FROM OLD.action_target
       OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
       OR NEW.dedupe_key IS DISTINCT FROM OLD.dedupe_key
       OR NEW.payload IS DISTINCT FROM OLD.payload
       OR NEW.issued_at IS DISTINCT FROM OLD.issued_at
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'published system notification content is immutable';
    END IF;

    RETURN NEW;
END
$$;

COMMIT;
