BEGIN;

DO $$
DECLARE
    owner_role oid;
BEGIN
    IF to_regclass('public.teacher_support_tickets') IS NULL THEN
        RAISE EXCEPTION
            'teacher_support_tickets must exist before migration 0024';
    END IF;

    SELECT oid
    INTO owner_role
    FROM pg_roles
    WHERE rolname = 'tide_support_ticket_owner'
      AND NOT rolcanlogin
      AND NOT rolsuper
      AND NOT rolcreatedb
      AND NOT rolcreaterole
      AND NOT rolreplication
      AND NOT rolbypassrls;

    IF owner_role IS NULL THEN
        RAISE EXCEPTION
            'DBA must provision a non-login unprivileged tide_support_ticket_owner role';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_auth_members
        WHERE member = owner_role
    ) THEN
        RAISE EXCEPTION
            'tide_support_ticket_owner must not inherit from any other role';
    END IF;

    IF NOT pg_has_role(
        current_user,
        'tide_support_ticket_owner',
        'MEMBER'
    ) THEN
        RAISE EXCEPTION
            'migration role must be a member of tide_support_ticket_owner';
    END IF;

    IF has_schema_privilege(
        'tide_support_ticket_owner',
        'public',
        'CREATE'
    ) THEN
        RAISE EXCEPTION
            'tide_support_ticket_owner must not retain CREATE on schema public';
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION public.append_teacher_support_ticket_teacher_message(
    p_ticket_id uuid,
    p_teacher_id varchar,
    p_expected_row_version bigint,
    p_message jsonb
)
RETURNS public.teacher_support_tickets
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    current_ticket public.teacher_support_tickets;
    existing_message jsonb;
    normalized_message jsonb;
BEGIN
    SELECT *
    INTO current_ticket
    FROM public.teacher_support_tickets
    WHERE ticket_id = p_ticket_id
      AND teacher_id = p_teacher_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    normalized_message :=
        (p_message - 'created_at')
        || jsonb_build_object('created_at', now());

    IF p_message IS NULL
       OR public.validate_teacher_support_message(
           p_ticket_id, normalized_message, 'TEACHER'
       ) IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION USING
            ERRCODE = '22023',
            MESSAGE = 'invalid teacher support-ticket message';
    END IF;

    SELECT value
    INTO existing_message
    FROM jsonb_array_elements(current_ticket.messages)
    WHERE value->>'message_id' = normalized_message->>'message_id'
    LIMIT 1;

    IF existing_message IS NOT NULL THEN
        IF existing_message->>'sender' = 'TEACHER'
           AND existing_message->>'content' = normalized_message->>'content'
           AND COALESCE(existing_message->'images', '[]'::jsonb)
               = COALESCE(normalized_message->'images', '[]'::jsonb) THEN
            RETURN current_ticket;
        END IF;

        RAISE EXCEPTION USING
            ERRCODE = '23505',
            MESSAGE = 'support-ticket message id already exists';
    END IF;

    IF current_ticket.status = 'CLOSED' THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'closed support ticket cannot receive messages';
    END IF;

    IF current_ticket.row_version IS DISTINCT FROM p_expected_row_version THEN
        RAISE EXCEPTION USING
            ERRCODE = '40001',
            MESSAGE = 'support-ticket row version is stale';
    END IF;

    UPDATE public.teacher_support_tickets
    SET
        messages = messages || jsonb_build_array(normalized_message),
        status = 'WAITING_OPERATOR',
        teacher_reply_deadline_at = NULL,
        row_version = row_version + 1,
        updated_at = now()
    WHERE ticket_id = p_ticket_id
    RETURNING * INTO current_ticket;

    RETURN current_ticket;
END
$$;

CREATE OR REPLACE FUNCTION public.append_teacher_support_ticket_operator_message(
    p_ticket_id uuid,
    p_expected_row_version bigint,
    p_message jsonb
)
RETURNS public.teacher_support_tickets
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    current_ticket public.teacher_support_tickets;
    existing_message jsonb;
    normalized_message jsonb;
    reply_at timestamptz;
BEGIN
    SELECT *
    INTO current_ticket
    FROM public.teacher_support_tickets
    WHERE ticket_id = p_ticket_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    reply_at := clock_timestamp();
    normalized_message :=
        (p_message - 'created_at')
        || jsonb_build_object('created_at', reply_at);

    IF p_message IS NULL
       OR public.validate_teacher_support_message(
           p_ticket_id, normalized_message, 'OPERATOR'
       ) IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION USING
            ERRCODE = '22023',
            MESSAGE = 'invalid operator support-ticket message';
    END IF;

    SELECT value
    INTO existing_message
    FROM jsonb_array_elements(current_ticket.messages)
    WHERE value->>'message_id' = normalized_message->>'message_id'
    LIMIT 1;

    IF existing_message IS NOT NULL THEN
        IF existing_message->>'sender' = 'OPERATOR'
           AND existing_message->>'content' = normalized_message->>'content'
           AND COALESCE(existing_message->'images', '[]'::jsonb)
               = COALESCE(normalized_message->'images', '[]'::jsonb) THEN
            RETURN current_ticket;
        END IF;

        RAISE EXCEPTION USING
            ERRCODE = '23505',
            MESSAGE = 'support-ticket message id already exists';
    END IF;

    IF current_ticket.status = 'CLOSED' THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'closed support ticket cannot receive messages';
    END IF;

    IF current_ticket.row_version IS DISTINCT FROM p_expected_row_version THEN
        RAISE EXCEPTION USING
            ERRCODE = '40001',
            MESSAGE = 'support-ticket row version is stale';
    END IF;

    UPDATE public.teacher_support_tickets
    SET
        messages = messages || jsonb_build_array(normalized_message),
        status = 'WAITING_TEACHER',
        last_operator_reply_at = reply_at,
        teacher_reply_deadline_at = reply_at + interval '48 hours',
        row_version = row_version + 1,
        updated_at = reply_at
    WHERE ticket_id = p_ticket_id
    RETURNING * INTO current_ticket;

    RETURN current_ticket;
END
$$;

COMMENT ON FUNCTION public.append_teacher_support_ticket_teacher_message(
    uuid, varchar, bigint, jsonb
) IS
    'TIDE 原子追加 TEACHER 消息；NULL 或不匹配的 expected row version 均以 40001 拒绝。';
COMMENT ON FUNCTION public.append_teacher_support_ticket_operator_message(
    uuid, bigint, jsonb
) IS
    '世文原子追加 OPERATOR 消息并开启 48 小时窗口；NULL 或不匹配的 expected row version 均以 40001 拒绝。';

REVOKE ALL ON public.teacher_support_tickets
    FROM tide_support_ticket_owner;
GRANT SELECT ON public.teacher_support_tickets
    TO tide_support_ticket_owner;
GRANT INSERT (
    ticket_id,
    teacher_id,
    primary_category,
    secondary_category,
    problem_location,
    problem_context,
    messages
) ON public.teacher_support_tickets
    TO tide_support_ticket_owner;
GRANT UPDATE (
    messages,
    status,
    last_operator_reply_at,
    teacher_reply_deadline_at,
    image_cleanup_status,
    images_deleted_at,
    row_version,
    updated_at
) ON public.teacher_support_tickets
    TO tide_support_ticket_owner;

REVOKE ALL ON FUNCTION public.teacher_support_primary_category(varchar)
    FROM PUBLIC, tide_support_ticket_owner;
REVOKE ALL ON FUNCTION public.validate_teacher_support_message(
    uuid, jsonb, varchar
) FROM PUBLIC, tide_support_ticket_owner;
GRANT EXECUTE ON FUNCTION public.teacher_support_primary_category(varchar)
    TO tide_support_ticket_owner;
GRANT EXECUTE ON FUNCTION public.validate_teacher_support_message(
    uuid, jsonb, varchar
) TO tide_support_ticket_owner;

GRANT USAGE, CREATE ON SCHEMA public TO tide_support_ticket_owner;
ALTER FUNCTION public.create_teacher_support_ticket(
    uuid, varchar, varchar, varchar, jsonb, jsonb
) OWNER TO tide_support_ticket_owner;
ALTER FUNCTION public.append_teacher_support_ticket_teacher_message(
    uuid, varchar, bigint, jsonb
) OWNER TO tide_support_ticket_owner;
ALTER FUNCTION public.append_teacher_support_ticket_operator_message(
    uuid, bigint, jsonb
) OWNER TO tide_support_ticket_owner;
ALTER FUNCTION public.mark_teacher_support_ticket_images_deleted(
    uuid, timestamptz
) OWNER TO tide_support_ticket_owner;
REVOKE CREATE ON SCHEMA public FROM tide_support_ticket_owner;

DO $$
BEGIN
    IF has_schema_privilege(
        'tide_support_ticket_owner',
        'public',
        'CREATE'
    ) THEN
        RAISE EXCEPTION
            'tide_support_ticket_owner retains CREATE on schema public';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM unnest(
            ARRAY[
                'public.create_teacher_support_ticket(uuid,character varying,character varying,character varying,jsonb,jsonb)'::regprocedure,
                'public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)'::regprocedure,
                'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'::regprocedure,
                'public.mark_teacher_support_ticket_images_deleted(uuid,timestamp with time zone)'::regprocedure
            ]
        ) AS secured_function(function_oid)
        JOIN pg_proc procedure
          ON procedure.oid = secured_function.function_oid
        JOIN pg_roles owner_role
          ON owner_role.oid = procedure.proowner
        WHERE owner_role.rolname <> 'tide_support_ticket_owner'
           OR NOT procedure.prosecdef
    ) THEN
        RAISE EXCEPTION
            'support-ticket SECURITY DEFINER owner contract is incomplete';
    END IF;
END
$$;

COMMIT;
