BEGIN;

REVOKE SELECT ON public.teacher_support_tickets FROM tit_growth_app;
REVOKE EXECUTE ON FUNCTION public.append_teacher_support_ticket_operator_message(
    uuid, bigint, jsonb
) FROM tit_growth_app;

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
BEGIN
    SELECT *
    INTO current_ticket
    FROM public.teacher_support_tickets
    WHERE ticket_id = p_ticket_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    normalized_message :=
        (p_message - 'created_at')
        || jsonb_build_object('created_at', now());

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
        row_version = row_version + 1,
        updated_at = now()
    WHERE ticket_id = p_ticket_id
    RETURNING * INTO current_ticket;

    RETURN current_ticket;
END
$$;

REVOKE ALL ON FUNCTION public.append_teacher_support_ticket_operator_message(
    uuid, bigint, jsonb
) FROM PUBLIC;

COMMENT ON FUNCTION public.append_teacher_support_ticket_operator_message(
    uuid, bigint, jsonb
) IS
    '世文运营侧原子追加 OPERATOR 消息；生产环境由 DBA 授权给世文运营应用角色。';

COMMIT;
