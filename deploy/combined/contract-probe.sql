\set ON_ERROR_STOP on

BEGIN TRANSACTION READ ONLY;

SELECT set_config(
    'tide.contract_probe_expected_database',
    :'expected_database',
    true
);
SELECT set_config(
    'tide.contract_probe_require_ssl',
    :'require_ssl',
    true
);

DO $probe$
DECLARE
    actual_codes text[];
    actual_titles text[];
    actual_scores integer[];
    total_score integer;
    allowed_status_column text;
    forbidden_assignment_column text;
    support_reply_definition text;
    teacher_reply_definition text;
BEGIN
    IF current_user IS DISTINCT FROM 'tit_contract_probe'
       OR session_user IS DISTINCT FROM 'tit_contract_probe' THEN
        RAISE EXCEPTION
            'contract probe must connect directly as tit_contract_probe';
    END IF;

    IF current_database() IS DISTINCT FROM current_setting(
        'tide.contract_probe_expected_database'
    ) THEN
        RAISE EXCEPTION 'contract probe target database mismatch';
    END IF;

    IF current_setting('transaction_read_only') IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION 'contract probe transaction is not read-only';
    END IF;

    IF current_setting('tide.contract_probe_require_ssl')::boolean
       AND NOT COALESCE(
           (
               SELECT ssl
               FROM pg_stat_ssl
               WHERE pid = pg_backend_pid()
           ),
           false
       ) THEN
        RAISE EXCEPTION 'contract probe database session is not using TLS';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname = 'tit_contract_probe'
          AND rolcanlogin
          AND NOT rolsuper
          AND NOT rolcreatedb
          AND NOT rolcreaterole
          AND NOT rolreplication
          AND NOT rolbypassrls
    ) OR EXISTS (
        SELECT 1
        FROM pg_auth_members AS membership
        JOIN pg_roles AS probe_role
          ON probe_role.oid = membership.member
        WHERE probe_role.rolname = 'tit_contract_probe'
    ) THEN
        RAISE EXCEPTION
            'contract probe role is privileged or inherits another role';
    END IF;

    IF to_regclass('public.task_templates') IS NULL
       OR to_regclass('public.task_assignments') IS NULL
       OR to_regclass('public.teachers') IS NULL
       OR to_regclass('public.teacher_scorecard_current') IS NULL
       OR to_regclass('public.teacher_lesson_score_current') IS NULL
       OR to_regclass('tide.task_execution_versions') IS NULL
       OR to_regclass('tide.schema_migrations') IS NULL THEN
        RAISE EXCEPTION 'required shared or teacher-side objects are missing';
    END IF;

    IF has_database_privilege(
        'tit_contract_probe',
        current_database(),
        'CREATE'
    ) OR has_schema_privilege(
        'tit_contract_probe',
        'public',
        'CREATE'
    ) OR has_schema_privilege(
        'tit_contract_probe',
        'tide',
        'CREATE'
    ) OR EXISTS (
        SELECT 1
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname IN ('public', 'tide')
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND (
              has_table_privilege(
                  'tit_contract_probe',
                  relation.oid,
                  'INSERT'
              )
              OR has_table_privilege(
                  'tit_contract_probe',
                  relation.oid,
                  'UPDATE'
              )
              OR has_table_privilege(
                  'tit_contract_probe',
                  relation.oid,
                  'DELETE'
              )
              OR has_table_privilege(
                  'tit_contract_probe',
                  relation.oid,
                  'TRUNCATE'
              )
          )
    ) OR EXISTS (
        SELECT 1
        FROM pg_class AS sequence
        JOIN pg_namespace AS namespace
          ON namespace.oid = sequence.relnamespace
        WHERE namespace.nspname IN ('public', 'tide')
          AND sequence.relkind = 'S'
          AND (
              has_sequence_privilege(
                  'tit_contract_probe',
                  sequence.oid,
                  'USAGE'
              )
              OR has_sequence_privilege(
                  'tit_contract_probe',
                  sequence.oid,
                  'UPDATE'
              )
          )
    ) THEN
        RAISE EXCEPTION 'contract probe role has write-capable privileges';
    END IF;

    IF (
        SELECT version_num
        FROM public.alembic_version
    ) IS DISTINCT FROM '20260729_38_catalog_scores' THEN
        RAISE EXCEPTION 'ops Alembic head is not the reviewed combined-deployment head';
    END IF;

    IF (
        SELECT array_agg(migration_id ORDER BY migration_order)
        FROM tide.schema_migrations
    ) IS DISTINCT FROM ARRAY[
            '0001_initial',
            '0002_shared_database_exchange',
            '0003_file_upload_intents',
            '0004_task_command_receipts',
            '0005_faq_message_commands',
            '0006_teacher_profile_g01_support',
            '0007_shared_task_assignment_links',
            '0008_remove_legacy_task_exchange',
            '0009_task_view_command',
            '0010_current_task_execution',
            '0011_system_notification_delivery',
            '0012_system_notification_publication_guards',
            '0013_system_notification_owner_maintenance',
            '0014_teacher_photo_processing',
            '0015_teacher_photo_filter_strength',
            '0016_database_quiz_banks',
            '0019_growth_stage_notification_state',
            '0020_product_analytics',
            '0021_teacher_support_tickets',
            '0022_performance_job_leases',
            '0023_teacher_support_operator_atomicity',
            '0024_support_ticket_cas_and_function_owner',
            '0025_fixed_task_semantic_alignment'
        ]::text[] THEN
        RAISE EXCEPTION
            'teacher production migration ledger is not the exact reviewed chain ending at 0025';
    END IF;

    SELECT
        array_agg(template_id ORDER BY template_id),
        array_agg(payload->>'title' ORDER BY template_id),
        array_agg((payload->>'score_value')::integer ORDER BY template_id),
        sum((payload->>'score_value')::integer)
    INTO actual_codes, actual_titles, actual_scores, total_score
    FROM public.task_templates
    WHERE status = 'PUBLISHED'
      AND source_mode = 'REAL'
      AND payload->>'category' = 'MANDATORY_GROWTH';

    IF actual_codes IS DISTINCT FROM
       ARRAY['G01','G02','G03','G04','G05','G06','G07','G08','G09']::text[]
       OR actual_titles IS DISTINCT FROM ARRAY[
           'Profile & Credentials Completion',
           'Platform Policies',
           'How to handle different types of students',
           'Lesson Preparation&Device Network Check',
           'TTP Orientation',
           'ME Culture & PARSNIP',
           'Reliability Training',
           'Cocos Course Training',
           'SET Teaching Fundamentals'
       ]::text[]
       OR actual_scores IS DISTINCT FROM ARRAY[3,2,2,3,3,4,3,5,5]::integer[]
       OR total_score IS DISTINCT FROM 30 THEN
        RAISE EXCEPTION
            'mandatory catalog mismatch: codes=%, titles=%, scores=%, total=%',
            actual_codes, actual_titles, actual_scores, total_score;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.teachers AS teacher
        LEFT JOIN public.task_assignments AS assignment
          ON assignment.teacher_id = teacher.teacher_id
         AND assignment.task_kind = 'FIXED_GROWTH'
         AND assignment.creator_system = 'TRIGGER_CENTER'
         AND assignment.source_mode = 'REAL'
         AND assignment.task_code BETWEEN 'G01' AND 'G09'
        GROUP BY teacher.teacher_id
        HAVING count(assignment.assignment_id) <> 9
    ) THEN
        RAISE EXCEPTION 'at least one teacher does not have exactly nine fixed assignments';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_execution_versions AS execution
        JOIN public.task_templates AS template
          ON template.row_id = execution.shared_template_row_id
        WHERE execution.status = 'ACTIVE'
          AND (
              execution.task_code IS DISTINCT FROM template.template_id
              OR template.status IS DISTINCT FROM 'PUBLISHED'
          )
    ) THEN
        RAISE EXCEPTION 'active teacher execution does not match its shared template';
    END IF;

    IF (
        SELECT array_agg(task_code::text ORDER BY task_code)
        FROM tide.task_execution_versions
        WHERE status = 'ACTIVE'
          AND task_code LIKE 'G%'
    ) IS DISTINCT FROM
       ARRAY['G01','G02','G03','G04','G05','G06','G07','G08','G09']::text[] THEN
        RAISE EXCEPTION 'teacher execution catalog is not the current G01-G09 catalog';
    END IF;

    IF to_regrole('tit_teacher_crud') IS NULL
       OR to_regrole('tit_growth_app') IS NULL
       OR to_regrole('tide_support_ticket_owner') IS NULL THEN
        RAISE EXCEPTION 'required runtime roles are missing';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname = 'tide_support_ticket_owner'
          AND (
              rolcanlogin
              OR rolsuper
              OR rolcreatedb
              OR rolcreaterole
              OR rolreplication
              OR rolbypassrls
          )
    ) THEN
        RAISE EXCEPTION 'support-ticket function owner is not a restricted NOLOGIN role';
    END IF;

    IF has_table_privilege(
        'tit_teacher_crud',
        'public.task_assignments',
        'INSERT'
    ) OR has_any_column_privilege(
        'tit_teacher_crud',
        'public.task_assignments',
        'INSERT'
    ) OR has_table_privilege(
        'tit_teacher_crud',
        'public.task_assignments',
        'DELETE'
    ) THEN
        RAISE EXCEPTION 'teacher runtime role can create or delete assignments';
    END IF;

    FOREACH allowed_status_column IN ARRAY ARRAY[
        'status',
        'status_reason_code',
        'status_changed_at',
        'completed_at',
        'updated_by'
    ] LOOP
        IF NOT has_column_privilege(
            'tit_teacher_crud',
            'public.task_assignments',
            allowed_status_column,
            'UPDATE'
        ) THEN
            RAISE EXCEPTION
                'teacher runtime role cannot update required column %',
                allowed_status_column;
        END IF;
    END LOOP;

    FOREACH forbidden_assignment_column IN ARRAY ARRAY[
        'teacher_id',
        'task_code',
        'template_version_id',
        'priority',
        'why',
        'display_title',
        'evidence_snapshot',
        'due_at',
        'row_version'
    ] LOOP
        IF has_column_privilege(
            'tit_teacher_crud',
            'public.task_assignments',
            forbidden_assignment_column,
            'UPDATE'
        ) THEN
            RAISE EXCEPTION
                'teacher runtime role can update forbidden column %',
                forbidden_assignment_column;
        END IF;
    END LOOP;

    IF has_table_privilege(
        'tit_teacher_crud',
        'public.score_entries',
        'INSERT,UPDATE,DELETE'
    ) OR has_any_column_privilege(
        'tit_teacher_crud',
        'public.score_entries',
        'INSERT,UPDATE'
    ) THEN
        RAISE EXCEPTION 'teacher runtime role can write score entries';
    END IF;

    IF NOT has_function_privilege(
        'tit_growth_app',
        'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'ops runtime role cannot execute the support-ticket reply function';
    END IF;

    IF has_function_privilege(
        'tit_growth_app',
        'public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)',
        'EXECUTE'
    ) OR NOT has_function_privilege(
        'tit_teacher_crud',
        'public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)',
        'EXECUTE'
    ) OR has_function_privilege(
        'tit_teacher_crud',
        'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'support-ticket function execution roles are not separated';
    END IF;

    IF has_function_privilege(
        'tit_contract_probe',
        'public.create_teacher_support_ticket(uuid,character varying,character varying,character varying,jsonb,jsonb)',
        'EXECUTE'
    ) OR has_function_privilege(
        'tit_contract_probe',
        'public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)',
        'EXECUTE'
    ) OR has_function_privilege(
        'tit_contract_probe',
        'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)',
        'EXECUTE'
    ) OR has_function_privilege(
        'tit_contract_probe',
        'public.mark_teacher_support_ticket_images_deleted(uuid,timestamp with time zone)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION
            'contract probe role can execute a mutation function';
    END IF;

    SELECT pg_get_functiondef(
        'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'::regprocedure
    )
    INTO support_reply_definition;
    SELECT pg_get_functiondef(
        'public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)'::regprocedure
    )
    INTO teacher_reply_definition;
    IF position('status = ''WAITING_TEACHER''' IN support_reply_definition) = 0
       OR position('last_operator_reply_at' IN support_reply_definition) = 0
       OR position('teacher_reply_deadline_at' IN support_reply_definition) = 0
       OR position('interval ''48 hours''' IN support_reply_definition) = 0
       OR position('p_message IS NULL' IN support_reply_definition) = 0
       OR position(
           'row_version IS DISTINCT FROM p_expected_row_version'
           IN support_reply_definition
       ) = 0
       OR position('p_message IS NULL' IN teacher_reply_definition) = 0
       OR position(
           'row_version IS DISTINCT FROM p_expected_row_version'
           IN teacher_reply_definition
       ) = 0 THEN
        RAISE EXCEPTION 'support-ticket reply function is not an atomic 48-hour transition';
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
        ) AS secured(function_oid)
        JOIN pg_proc AS procedure
          ON procedure.oid = secured.function_oid
        WHERE pg_get_userbyid(procedure.proowner)
                  <> 'tide_support_ticket_owner'
           OR NOT procedure.prosecdef
    ) THEN
        RAISE EXCEPTION 'support-ticket SECURITY DEFINER owner contract is incomplete';
    END IF;

    IF NOT has_table_privilege(
        'tit_teacher_crud',
        'public.teacher_scorecard_current',
        'SELECT'
    ) OR NOT has_table_privilege(
        'tit_teacher_crud',
        'public.teacher_lesson_score_current',
       'SELECT'
    ) OR NOT has_table_privilege(
        'tit_teacher_crud',
        'public.teachers',
        'SELECT'
    ) OR NOT has_table_privilege(
        'tit_teacher_crud',
        'public.task_templates',
        'SELECT'
    ) OR NOT has_table_privilege(
        'tit_teacher_crud',
        'public.task_assignments',
        'SELECT'
    ) THEN
        RAISE EXCEPTION 'teacher runtime role cannot read required shared objects';
    END IF;
END
$probe$;

SELECT version_num AS ops_alembic_head
FROM public.alembic_version;

SELECT migration_id, sha256, applied_at
FROM tide.schema_migrations
ORDER BY applied_at DESC, migration_id DESC
LIMIT 5;

SELECT 1 FROM public.teacher_scorecard_current LIMIT 0;
SELECT 1 FROM public.teacher_lesson_score_current LIMIT 0;

COMMIT;
