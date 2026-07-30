BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS uq_notification_events_notification_request_hash
    ON public.notification_events (notification_id, request_hash);

CREATE INDEX IF NOT EXISTS ix_notifications_teacher_requested_desc
    ON public.notifications (teacher_id, requested_at DESC, notification_id DESC);

CREATE TABLE tide.system_notification_publications (
    publication_id uuid PRIMARY KEY,
    config_key varchar NOT NULL UNIQUE,
    type_code varchar NOT NULL,
    title varchar NOT NULL,
    body text NOT NULL,
    audience jsonb NOT NULL,
    action_type varchar,
    action_target text,
    scheduled_at timestamptz NOT NULL,
    expires_at timestamptz,
    status varchar NOT NULL DEFAULT 'SCHEDULED',
    config_hash varchar NOT NULL,
    recipient_count integer NOT NULL DEFAULT 0,
    attempt_count integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz,
    published_at timestamptz,
    cancelled_at timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT system_notification_publications_title_check
        CHECK (char_length(title) BETWEEN 1 AND 160),
    CONSTRAINT system_notification_publications_body_check
        CHECK (char_length(body) BETWEEN 1 AND 4000),
    CONSTRAINT system_notification_publications_audience_check
        CHECK (jsonb_typeof(audience) = 'object'),
    CONSTRAINT system_notification_publications_action_check
        CHECK ((action_type IS NULL) = (action_target IS NULL)),
    CONSTRAINT system_notification_publications_action_type_check
        CHECK (
            action_type IS NULL OR action_type IN (
                'TASK_DETAIL', 'MY_TIDE', 'TASKS', 'HELP', 'ACCOUNT'
            )
        ),
    CONSTRAINT system_notification_publications_status_check
        CHECK (status IN ('SCHEDULED', 'PUBLISHED', 'CANCELLED', 'FAILED')),
    CONSTRAINT system_notification_publications_hash_check
        CHECK (config_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT system_notification_publications_recipient_count_check
        CHECK (recipient_count >= 0),
    CONSTRAINT system_notification_publications_attempt_count_check
        CHECK (attempt_count >= 0),
    CONSTRAINT system_notification_publications_expiry_check
        CHECK (expires_at IS NULL OR expires_at > scheduled_at)
);

CREATE INDEX system_notification_publications_due_idx
    ON tide.system_notification_publications (
        (COALESCE(next_attempt_at, scheduled_at)), publication_id
    )
    WHERE status = 'SCHEDULED';

ALTER TABLE tide.system_notifications
    ADD COLUMN publication_id uuid,
    ADD COLUMN issued_at timestamptz;

UPDATE tide.system_notifications
SET issued_at = created_at
WHERE issued_at IS NULL;

UPDATE tide.system_notifications
SET action_type = CASE action_target
        WHEN '/' THEN 'MY_TIDE'
        WHEN '/my-tide' THEN 'MY_TIDE'
        WHEN '/path' THEN 'TASKS'
        WHEN '/help' THEN 'HELP'
        WHEN '/account' THEN 'ACCOUNT'
        ELSE action_type
    END,
    action_target = CASE action_target
        WHEN '/my-tide' THEN '/'
        ELSE action_target
    END
WHERE action_type = 'IN_APP_ROUTE';

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM tide.system_notifications
        WHERE action_type IS NOT NULL
          AND action_type NOT IN (
              'TASK_DETAIL', 'MY_TIDE', 'TASKS', 'HELP', 'ACCOUNT'
          )
    ) THEN
        RAISE EXCEPTION 'system_notifications contains unsupported legacy action types';
    END IF;
END
$$;

ALTER TABLE tide.system_notifications
    ALTER COLUMN issued_at SET NOT NULL,
    ALTER COLUMN issued_at SET DEFAULT now(),
    ADD CONSTRAINT system_notifications_publication_id_fkey
        FOREIGN KEY (publication_id)
        REFERENCES tide.system_notification_publications(publication_id)
        ON DELETE RESTRICT,
    ADD CONSTRAINT system_notifications_action_type_check
        CHECK (
            action_type IS NULL OR action_type IN (
                'TASK_DETAIL', 'MY_TIDE', 'TASKS', 'HELP', 'ACCOUNT'
            )
        ),
    ADD CONSTRAINT system_notifications_action_target_check
        CHECK (
            (action_type IS NULL AND action_target IS NULL)
            OR (action_type = 'TASK_DETAIL' AND action_target LIKE '/task/%')
            OR (action_type = 'MY_TIDE' AND action_target = '/')
            OR (action_type = 'TASKS' AND action_target = '/path')
            OR (action_type = 'HELP' AND action_target = '/help')
            OR (action_type = 'ACCOUNT' AND action_target = '/account')
        );

CREATE INDEX system_notifications_teacher_issued_idx
    ON tide.system_notifications (
        teacher_id, issued_at DESC, system_notification_id DESC
    )
    WHERE cancelled_at IS NULL;

CREATE OR REPLACE FUNCTION tide.protect_system_notification_content()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, tide
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'published system notifications cannot be deleted';
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

CREATE TRIGGER protect_system_notification_content
BEFORE UPDATE OR DELETE ON tide.system_notifications
FOR EACH ROW
EXECUTE FUNCTION tide.protect_system_notification_content();

CREATE OR REPLACE FUNCTION tide.protect_system_notification_publication()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, tide
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'system notification publications cannot be deleted';
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
           OR NEW.published_at IS DISTINCT FROM OLD.published_at
           OR NEW.created_at IS DISTINCT FROM OLD.created_at
       ) THEN
        RAISE EXCEPTION 'published system notification publication is immutable';
    END IF;

    RETURN NEW;
END
$$;

CREATE TRIGGER protect_system_notification_publication
BEFORE UPDATE OR DELETE ON tide.system_notification_publications
FOR EACH ROW
EXECUTE FUNCTION tide.protect_system_notification_publication();

COMMIT;
