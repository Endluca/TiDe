BEGIN;

CREATE TABLE public.teacher_support_tickets (
    ticket_id uuid PRIMARY KEY,
    teacher_id varchar NOT NULL
        REFERENCES public.teachers(teacher_id) ON DELETE RESTRICT,
    primary_category varchar NOT NULL,
    secondary_category varchar NOT NULL,
    problem_location varchar NOT NULL,
    problem_context jsonb NOT NULL DEFAULT '{}'::jsonb,
    messages jsonb NOT NULL DEFAULT '[]'::jsonb,
    status varchar NOT NULL DEFAULT 'WAITING_OPERATOR',
    last_operator_reply_at timestamptz,
    teacher_reply_deadline_at timestamptz,
    last_read_operator_message_id uuid,
    teacher_last_read_at timestamptz,
    close_reason varchar,
    closed_at timestamptz,
    image_cleanup_status varchar NOT NULL DEFAULT 'NOT_REQUIRED',
    images_deleted_at timestamptz,
    row_version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT teacher_support_tickets_primary_category_check
        CHECK (primary_category IN ('OPERATIONS', 'PRODUCT', 'OTHER')),
    CONSTRAINT teacher_support_tickets_secondary_category_check
        CHECK (
            secondary_category IN (
                'TASK_RULES',
                'LESSON_INFO',
                'SCORE_OR_REVIEW',
                'PRODUCT_FUNCTION',
                'ACCOUNT_LOGIN',
                'MEDIA_UPLOAD_CAMERA',
                'OTHER'
            )
        ),
    CONSTRAINT teacher_support_tickets_category_mapping_check
        CHECK (
            (
                secondary_category IN (
                    'TASK_RULES', 'LESSON_INFO', 'SCORE_OR_REVIEW'
                )
                AND primary_category = 'OPERATIONS'
            )
            OR (
                secondary_category IN (
                    'PRODUCT_FUNCTION',
                    'ACCOUNT_LOGIN',
                    'MEDIA_UPLOAD_CAMERA'
                )
                AND primary_category = 'PRODUCT'
            )
            OR (
                secondary_category = 'OTHER'
                AND primary_category = 'OTHER'
            )
        ),
    CONSTRAINT teacher_support_tickets_problem_location_check
        CHECK (
            problem_location IN (
                'MY_TIDE', 'TASK', 'LESSON', 'MESSAGES',
                'ACCOUNT', 'HELP', 'OTHER'
            )
        ),
    CONSTRAINT teacher_support_tickets_problem_context_check
        CHECK (jsonb_typeof(problem_context) = 'object'),
    CONSTRAINT teacher_support_tickets_messages_check
        CHECK (jsonb_typeof(messages) = 'array'),
    CONSTRAINT teacher_support_tickets_status_check
        CHECK (status IN ('WAITING_OPERATOR', 'WAITING_TEACHER', 'CLOSED')),
    CONSTRAINT teacher_support_tickets_close_reason_check
        CHECK (
            close_reason IS NULL
            OR close_reason IN ('RESOLVED', 'NO_RESPONSE_TIMEOUT')
        ),
    CONSTRAINT teacher_support_tickets_close_bundle_check
        CHECK (
            (
                status = 'CLOSED'
                AND close_reason IS NOT NULL
                AND closed_at IS NOT NULL
            )
            OR (
                status <> 'CLOSED'
                AND close_reason IS NULL
                AND closed_at IS NULL
            )
        ),
    CONSTRAINT teacher_support_tickets_cleanup_status_check
        CHECK (
            image_cleanup_status IN (
                'NOT_REQUIRED', 'PENDING', 'SUCCEEDED', 'FAILED'
            )
        ),
    CONSTRAINT teacher_support_tickets_cleanup_time_check
        CHECK (
            (
                image_cleanup_status = 'SUCCEEDED'
                AND images_deleted_at IS NOT NULL
            )
            OR (
                image_cleanup_status <> 'SUCCEEDED'
                AND images_deleted_at IS NULL
            )
        ),
    CONSTRAINT teacher_support_tickets_row_version_check
        CHECK (row_version > 0)
);

CREATE INDEX teacher_support_tickets_teacher_updated_idx
    ON public.teacher_support_tickets (
        teacher_id, updated_at DESC, ticket_id DESC
    );

CREATE INDEX teacher_support_tickets_status_deadline_idx
    ON public.teacher_support_tickets (
        status, teacher_reply_deadline_at, ticket_id
    )
    WHERE status <> 'CLOSED';

CREATE INDEX teacher_support_tickets_shared_updated_idx
    ON public.teacher_support_tickets (updated_at, ticket_id);

CREATE OR REPLACE FUNCTION public.teacher_support_primary_category(
    p_secondary_category varchar
)
RETURNS varchar
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN p_secondary_category IN (
            'TASK_RULES', 'LESSON_INFO', 'SCORE_OR_REVIEW'
        ) THEN 'OPERATIONS'
        WHEN p_secondary_category IN (
            'PRODUCT_FUNCTION', 'ACCOUNT_LOGIN', 'MEDIA_UPLOAD_CAMERA'
        ) THEN 'PRODUCT'
        WHEN p_secondary_category = 'OTHER' THEN 'OTHER'
        ELSE NULL
    END::varchar
$$;

CREATE OR REPLACE FUNCTION public.validate_teacher_support_message(
    p_ticket_id uuid,
    p_message jsonb,
    p_expected_sender varchar
)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
PARALLEL SAFE
SET search_path = pg_catalog, public
AS $$
DECLARE
    message_id_text text;
    image jsonb;
    expected_prefix text;
BEGIN
    IF jsonb_typeof(p_message) <> 'object' THEN
        RETURN false;
    END IF;

    message_id_text := p_message->>'message_id';
    IF message_id_text IS NULL
       OR message_id_text !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN
        RETURN false;
    END IF;

    IF p_message->>'sender' IS DISTINCT FROM p_expected_sender
       OR p_expected_sender NOT IN ('TEACHER', 'OPERATOR') THEN
        RETURN false;
    END IF;

    IF char_length(btrim(COALESCE(p_message->>'content', ''))) NOT BETWEEN 1 AND 5000 THEN
        RETURN false;
    END IF;

    IF p_message ? 'images'
       AND jsonb_typeof(p_message->'images') <> 'array' THEN
        RETURN false;
    END IF;

    IF jsonb_array_length(COALESCE(p_message->'images', '[]'::jsonb)) > 3 THEN
        RETURN false;
    END IF;

    expected_prefix :=
        'support-tickets/' || p_ticket_id::text || '/' || message_id_text || '/';

    FOR image IN
        SELECT value
        FROM jsonb_array_elements(
            COALESCE(p_message->'images', '[]'::jsonb)
        )
    LOOP
        IF jsonb_typeof(image) <> 'object'
           OR image->>'file_id' IS NULL
           OR image->>'file_id' !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
           OR image->>'object_key' IS NULL
           OR image->>'object_key' NOT LIKE expected_prefix || '%'
           OR image->>'object_key' LIKE '%..%'
           OR image->>'object_key' LIKE '%//%'
           OR char_length(btrim(COALESCE(image->>'filename', ''))) NOT BETWEEN 1 AND 160
           OR COALESCE((image->>'size')::bigint, -1) < 0
           OR image->>'mime_type' NOT IN (
               'image/jpeg', 'image/png', 'image/webp'
           )
           OR image->>'storage_provider' NOT IN ('LOCAL', 'OSS')
           OR (
               image ? 'deleted_at'
               AND image->>'deleted_at' IS NOT NULL
           ) THEN
            RETURN false;
        END IF;
    END LOOP;

    RETURN true;
EXCEPTION
    WHEN invalid_text_representation OR numeric_value_out_of_range THEN
        RETURN false;
END
$$;

CREATE OR REPLACE FUNCTION public.create_teacher_support_ticket(
    p_ticket_id uuid,
    p_teacher_id varchar,
    p_secondary_category varchar,
    p_problem_location varchar,
    p_problem_context jsonb,
    p_first_message jsonb
)
RETURNS public.teacher_support_tickets
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    created_ticket public.teacher_support_tickets;
    primary_category_value varchar;
    normalized_message jsonb;
BEGIN
    primary_category_value :=
        public.teacher_support_primary_category(p_secondary_category);

    IF primary_category_value IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '22023',
            MESSAGE = 'unsupported support-ticket secondary category';
    END IF;

    IF jsonb_typeof(p_problem_context) <> 'object' THEN
        RAISE EXCEPTION USING
            ERRCODE = '22023',
            MESSAGE = 'support-ticket problem context must be an object';
    END IF;

    normalized_message :=
        (p_first_message - 'created_at')
        || jsonb_build_object('created_at', now());

    IF NOT public.validate_teacher_support_message(
        p_ticket_id, normalized_message, 'TEACHER'
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '22023',
            MESSAGE = 'invalid teacher support-ticket message';
    END IF;

    INSERT INTO public.teacher_support_tickets (
        ticket_id,
        teacher_id,
        primary_category,
        secondary_category,
        problem_location,
        problem_context,
        messages
    )
    VALUES (
        p_ticket_id,
        p_teacher_id,
        primary_category_value,
        p_secondary_category,
        p_problem_location,
        p_problem_context,
        jsonb_build_array(normalized_message)
    )
    RETURNING * INTO created_ticket;

    RETURN created_ticket;
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

    IF NOT public.validate_teacher_support_message(
        p_ticket_id, normalized_message, 'TEACHER'
    ) THEN
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

    IF current_ticket.row_version <> p_expected_row_version THEN
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

CREATE OR REPLACE FUNCTION public.mark_teacher_support_ticket_images_deleted(
    p_ticket_id uuid,
    p_deleted_at timestamptz
)
RETURNS public.teacher_support_tickets
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    current_ticket public.teacher_support_tickets;
BEGIN
    UPDATE public.teacher_support_tickets ticket
    SET
        messages = (
            SELECT COALESCE(
                jsonb_agg(
                    message.value
                    || jsonb_build_object(
                        'images',
                        (
                            SELECT COALESCE(
                                jsonb_agg(
                                    image.value
                                    || jsonb_build_object(
                                        'deleted_at', p_deleted_at
                                    )
                                    ORDER BY image.ordinality
                                ),
                                '[]'::jsonb
                            )
                            FROM jsonb_array_elements(
                                COALESCE(
                                    message.value->'images',
                                    '[]'::jsonb
                                )
                            ) WITH ORDINALITY AS image(value, ordinality)
                        )
                    )
                    ORDER BY message.ordinality
                ),
                '[]'::jsonb
            )
            FROM jsonb_array_elements(ticket.messages)
                WITH ORDINALITY AS message(value, ordinality)
        ),
        image_cleanup_status = 'SUCCEEDED',
        images_deleted_at = p_deleted_at,
        row_version = row_version + 1,
        updated_at = now()
    WHERE ticket_id = p_ticket_id
      AND status = 'CLOSED'
      AND image_cleanup_status IN ('PENDING', 'FAILED')
    RETURNING * INTO current_ticket;

    RETURN current_ticket;
END
$$;

REVOKE ALL ON public.teacher_support_tickets FROM PUBLIC;
REVOKE ALL ON FUNCTION public.create_teacher_support_ticket(
    uuid, varchar, varchar, varchar, jsonb, jsonb
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.append_teacher_support_ticket_teacher_message(
    uuid, varchar, bigint, jsonb
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.append_teacher_support_ticket_operator_message(
    uuid, bigint, jsonb
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.mark_teacher_support_ticket_images_deleted(
    uuid, timestamptz
) FROM PUBLIC;

COMMENT ON TABLE public.teacher_support_tickets IS
    'TIDE 与世文共享的教师工单权威记录；TIDE 维护状态，双方只追加各自消息。';
COMMENT ON FUNCTION public.append_teacher_support_ticket_operator_message(
    uuid, bigint, jsonb
) IS
    '世文运营侧原子追加 OPERATOR 消息；生产环境由 DBA 授权给世文运营应用角色。';

COMMIT;
