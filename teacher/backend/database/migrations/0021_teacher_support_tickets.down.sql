BEGIN;

DROP FUNCTION IF EXISTS public.mark_teacher_support_ticket_images_deleted(
    uuid, timestamptz
);
DROP FUNCTION IF EXISTS public.append_teacher_support_ticket_operator_message(
    uuid, bigint, jsonb
);
DROP FUNCTION IF EXISTS public.append_teacher_support_ticket_teacher_message(
    uuid, varchar, bigint, jsonb
);
DROP FUNCTION IF EXISTS public.create_teacher_support_ticket(
    uuid, varchar, varchar, varchar, jsonb, jsonb
);
DROP FUNCTION IF EXISTS public.validate_teacher_support_message(
    uuid, jsonb, varchar
);
DROP FUNCTION IF EXISTS public.teacher_support_primary_category(varchar);

DROP TABLE IF EXISTS public.teacher_support_tickets;

COMMIT;
