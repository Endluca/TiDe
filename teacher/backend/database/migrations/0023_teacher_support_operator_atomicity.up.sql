BEGIN;

DO $$
BEGIN
    IF to_regclass('public.teacher_support_tickets') IS NULL THEN
        RAISE EXCEPTION
            'teacher_support_tickets must exist before migration 0023';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname = 'tit_growth_app'
    ) THEN
        RAISE EXCEPTION
            'role tit_growth_app must be provisioned by DBA before migration 0023';
    END IF;
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

    -- 锁后取实际回复时间，避免等待行锁的早启动事务把窗口时间写回更早。
    reply_at := clock_timestamp();
    normalized_message :=
        (p_message - 'created_at')
        || jsonb_build_object('created_at', reply_at);

    IF NOT public.validate_teacher_support_message(
        p_ticket_id, normalized_message, 'OPERATOR'
    ) THEN
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

    IF current_ticket.row_version <> p_expected_row_version THEN
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

REVOKE ALL ON public.teacher_support_tickets FROM tit_growth_app;
REVOKE ALL ON FUNCTION public.create_teacher_support_ticket(
    uuid, varchar, varchar, varchar, jsonb, jsonb
) FROM tit_growth_app;
REVOKE ALL ON FUNCTION public.append_teacher_support_ticket_teacher_message(
    uuid, varchar, bigint, jsonb
) FROM tit_growth_app;
REVOKE ALL ON FUNCTION public.append_teacher_support_ticket_operator_message(
    uuid, bigint, jsonb
) FROM PUBLIC, tit_growth_app;
REVOKE ALL ON FUNCTION public.mark_teacher_support_ticket_images_deleted(
    uuid, timestamptz
) FROM tit_growth_app;

GRANT SELECT ON public.teacher_support_tickets TO tit_growth_app;
GRANT EXECUTE ON FUNCTION public.append_teacher_support_ticket_operator_message(
    uuid, bigint, jsonb
) TO tit_growth_app;

COMMENT ON FUNCTION public.append_teacher_support_ticket_operator_message(
    uuid, bigint, jsonb
) IS
    '世文运营侧原子追加 OPERATOR 消息，同时切换 WAITING_TEACHER 并从数据库回复时间起设置 48 小时窗口；tit_growth_app 仅获本函数 EXECUTE。';

COMMIT;
