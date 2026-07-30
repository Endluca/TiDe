BEGIN;

CREATE OR REPLACE FUNCTION tide.protect_system_notification_publication()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, tide
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'system notification publications cannot be deleted';
    END IF;

    IF OLD.status = 'PUBLISHED'
       AND NEW.status NOT IN ('PUBLISHED', 'CANCELLED') THEN
        RAISE EXCEPTION 'published system notification publication cannot be reopened';
    END IF;

    IF OLD.status = 'CANCELLED'
       AND NEW.status <> 'CANCELLED' THEN
        RAISE EXCEPTION 'cancelled system notification publication cannot be reopened';
    END IF;

    IF OLD.status IN ('PUBLISHED', 'CANCELLED')
       AND (
           NEW.publication_id IS DISTINCT FROM OLD.publication_id
           OR NEW.config_key IS DISTINCT FROM OLD.config_key
           OR NEW.type_code IS DISTINCT FROM OLD.type_code
           OR NEW.title IS DISTINCT FROM OLD.title
           OR NEW.body IS DISTINCT FROM OLD.body
           OR NEW.audience IS DISTINCT FROM OLD.audience
           OR NEW.action_type IS DISTINCT FROM OLD.action_type
           OR NEW.action_target IS DISTINCT FROM OLD.action_target
           OR NEW.scheduled_at IS DISTINCT FROM OLD.scheduled_at
           OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
           OR NEW.config_hash IS DISTINCT FROM OLD.config_hash
           OR NEW.recipient_count IS DISTINCT FROM OLD.recipient_count
           OR NEW.published_at IS DISTINCT FROM OLD.published_at
           OR NEW.created_at IS DISTINCT FROM OLD.created_at
       ) THEN
        RAISE EXCEPTION 'published system notification publication is immutable';
    END IF;

    RETURN NEW;
END
$$;

COMMIT;
