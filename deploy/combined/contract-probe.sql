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
       OR to_regclass('public.complaint_category_rules') IS NULL
       OR to_regclass('public.operator_sessions') IS NULL
       OR to_regclass('public.teacher_scorecard_current') IS NULL
       OR to_regclass('public.teacher_lesson_score_current') IS NULL
       OR to_regclass('tide.task_execution_versions') IS NULL
       OR to_regclass('tide.file_objects') IS NULL
       OR to_regclass('tide.task_step_definitions') IS NULL
       OR to_regclass('tide.task_validation_rules') IS NULL
       OR to_regclass('tide.account_onboarding_states') IS NULL
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
    ) IS DISTINCT FROM '20260811_54_g04_remove_device_check' THEN
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
            '0025_fixed_task_semantic_alignment',
            '0026_kuozhi_course_syncs',
            '0027_remove_local_quiz_runtime',
            '0028_retire_task_business_change_view',
            '0029_remove_unused_tide_objects',
            '0030_remove_unused_columns_and_orphan_function',
            '0031_g04_independent_sections',
            '0032_first_login_onboarding',
            '0033_g01_tesol_only',
            '0037_g04_remove_device_check'
        ]::text[] THEN
        RAISE EXCEPTION
            'teacher production migration ledger is not the exact reviewed chain ending at 0037';
    END IF;

    IF to_regclass('tide.analytics_task_business_change_v1') IS NOT NULL
       OR to_regclass('public.teacher_metric_snapshots') IS NOT NULL
       OR to_regclass('public.lesson_facts') IS NOT NULL
       OR to_regclass('public.lesson_dimension_scores') IS NOT NULL
       OR to_regclass('tide.outcome_projections') IS NOT NULL
       OR to_regclass('tide.camp_enrollment_projections') IS NOT NULL
       OR to_regclass('tide.audit_events') IS NOT NULL
       OR to_regclass('tide.task_template_files') IS NOT NULL
       OR to_regclass('tide.file_migrations') IS NOT NULL
       OR to_regclass('tide.teacher_photo_runs') IS NOT NULL
       OR to_regclass('tide.analytics_actor_task_journey_v1') IS NOT NULL
       OR to_regclass('tide.analytics_task_assignment_funnel_v1') IS NOT NULL
       OR to_regclass('tide.analytics_task_funnel_v1') IS NOT NULL
       OR to_regclass('tide.analytics_task_step_funnel_v1') IS NOT NULL
       OR to_regclass('tide.analytics_content_quality_v1') IS NOT NULL
       OR EXISTS (
           SELECT 1
           FROM information_schema.columns
           WHERE (table_schema, table_name, column_name) IN (
               ('public', 'complaint_category_rules', 'learning_title'),
               ('public', 'complaint_category_rules', 'learning_url'),
               ('public', 'operator_sessions', 'last_seen_at'),
               ('tide', 'file_objects', 'visibility')
           )
       )
       OR to_regprocedure('tide.enforce_outbox_target()') IS NOT NULL THEN
        RAISE EXCEPTION
            'legacy objects, redundant columns or orphan function still exist';
    END IF;

    IF (
        SELECT count(*)
        FROM pg_attribute
        WHERE attrelid = 'tide.account_onboarding_states'::regclass
          AND attnum > 0
          AND NOT attisdropped
          AND attname IN (
              'account_id',
              'guide_code',
              'guide_version',
              'status',
              'idempotency_key',
              'request_hash',
              'acknowledged_at',
              'created_at',
              'updated_at'
          )
    ) <> 9 OR NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'tide.account_onboarding_states'::regclass
          AND conname = 'account_onboarding_states_pkey'
          AND contype = 'p'
    ) OR NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'tide.account_onboarding_states'::regclass
          AND conname = 'account_onboarding_states_account_idempotency_key'
          AND contype = 'u'
    ) OR (
        SELECT count(*)
        FROM pg_constraint
        WHERE conrelid = 'tide.account_onboarding_states'::regclass
          AND conname IN (
              'account_onboarding_states_guide_code_check',
              'account_onboarding_states_guide_version_check',
              'account_onboarding_states_status_check',
              'account_onboarding_states_idempotency_key_check',
              'account_onboarding_states_request_hash_check',
              'account_onboarding_states_time_check'
          )
          AND contype = 'c'
    ) <> 6 OR NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'tide.account_onboarding_states'::regclass
          AND contype = 'f'
          AND confrelid = 'tide.user_accounts'::regclass
          AND confdeltype = 'c'
    ) THEN
        RAISE EXCEPTION
            'teacher first-login onboarding state contract is incomplete';
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
           'Lesson Preparation',
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

    IF NOT EXISTS (
        SELECT 1
        FROM public.task_templates
        WHERE row_id = 'G01:v1'
          AND template_id = 'G01'
          AND status = 'PUBLISHED'
          AND payload->>'why_template' =
              'Complete the required TESOL status and learning evidence.'
          AND payload->>'how_summary' =
              'Confirm TESOL, pass all 61 questions, complete the Essay and submit the completion proof.'
          AND payload->>'completion_standard' =
              'TESOL is complete, the 61-question check reaches 80%, the Essay is complete and the completion proof is submitted.'
          AND position('Self-intro' IN payload->>'why_template') = 0
          AND position('Self-intro' IN payload->>'how_summary') = 0
          AND position('Self-intro' IN payload->>'completion_standard') = 0
    ) THEN
        RAISE EXCEPTION
            'stable G01:v1 row is not the reviewed TESOL-only catalog copy';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM public.task_templates
        WHERE row_id = 'G02:v1'
          AND template_id = 'G04'
          AND template_version = 1
          AND status = 'PUBLISHED'
          AND payload->>'template_id' = 'G04'
          AND payload->>'ops_name_zh' = '首课准备'
          AND payload->>'title' = 'Lesson Preparation'
          AND payload->>'why_template' =
              'Complete the teaching-environment photo review and prepare the courseware before your first lesson.'
          AND payload->>'how_summary' =
              'Complete two sections in any order: submit one teaching-environment photo for AI review and prepare the courseware for your first lesson. Each section keeps its own progress.'
          AND payload->>'completion_standard' =
              'G04 is completed only after both sections pass: all four teaching-environment photo criteria—camera angle, lighting, background and dressing—pass AI review, and the courseware preparation is confirmed. The sections may be completed in any order.'
          AND payload->>'benefit' =
              'Your teaching environment and courseware are ready for your first lesson.'
    ) THEN
        RAISE EXCEPTION
            'stable G02:v1 row for current G04 does not expose the reviewed two sections';
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

    IF (
        SELECT count(*)
        FROM tide.task_execution_versions AS execution
        JOIN tide.task_validation_rules AS rule
          ON rule.execution_version_id = execution.id
        WHERE execution.shared_template_row_id = 'G01:v1'
          AND execution.task_code = 'G01'
          AND execution.status = 'ACTIVE'
          AND (
              rule.rule_key = 'g01-external-status'
              OR rule.rule_type = 'G01_EXTERNAL_STATUS'
          )
    ) <> 1 OR NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions AS execution
        JOIN tide.task_validation_rules AS rule
          ON rule.execution_version_id = execution.id
        WHERE execution.shared_template_row_id = 'G01:v1'
          AND execution.task_code = 'G01'
          AND execution.status = 'ACTIVE'
          AND rule.rule_key = 'g01-external-status'
          AND rule.rule_type = 'G01_EXTERNAL_STATUS'
          AND rule.rule_version = '2026-08-11-tesol-only-v1'
          AND rule.position = 3
          AND rule.config = '{}'::jsonb
          AND rule.teacher_failure_copy = 'TESOL 真实状态尚未通过。'
    ) THEN
        RAISE EXCEPTION
            'G01 external-status rule set is not exactly the reviewed TESOL-only rule';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions AS execution
        WHERE execution.shared_template_row_id = 'G02:v1'
          AND execution.task_code = 'G04'
          AND execution.status = 'ACTIVE'
          AND execution.config->>'contentVersion' =
              '2026-08-11-g04-two-part'
          AND execution.config->'independentModules' = jsonb_build_object(
              'stepKeys', jsonb_build_array(
                  'g02-environment-photo',
                  'g02-courseware-confirmation'
              ),
              'allowOutOfOrderProgress', true,
              'keepAssignmentInProgressUntilPassed', true
          )
    ) THEN
        RAISE EXCEPTION
            'G04 teacher execution does not enable the reviewed independent modules';
    END IF;

    IF (
        SELECT array_agg(definition.step_key ORDER BY definition.position)
        FROM tide.task_execution_versions AS execution
        JOIN tide.task_step_definitions AS definition
          ON definition.execution_version_id = execution.id
        WHERE execution.shared_template_row_id = 'G02:v1'
          AND execution.task_code = 'G04'
          AND execution.status = 'ACTIVE'
    ) IS DISTINCT FROM ARRAY[
        'g02-environment-photo',
        'g02-courseware-confirmation'
    ]::text[] THEN
        RAISE EXCEPTION
            'G04 teacher execution does not have exactly the reviewed two steps';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_execution_versions AS execution
        JOIN tide.task_step_definitions AS definition
          ON definition.execution_version_id = execution.id
        WHERE execution.shared_template_row_id = 'G02:v1'
          AND execution.task_code = 'G04'
          AND execution.status = 'ACTIVE'
          AND definition.step_key = 'g02-device-check'
    ) THEN
        RAISE EXCEPTION
            'G04 device step is still active';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions AS execution
        JOIN tide.task_step_definitions AS definition
          ON definition.execution_version_id = execution.id
        WHERE execution.shared_template_row_id = 'G02:v1'
          AND execution.task_code = 'G04'
          AND execution.status = 'ACTIVE'
          AND definition.step_key = 'g02-courseware-confirmation'
          AND definition.step_type = 'CHECKLIST'
          AND definition.config->>'version' =
              'g02-courseware-2026-08-05-guidance-v1'
          AND definition.config->>'role' = 'COURSEWARE_CONFIRMATION'
    ) THEN
        RAISE EXCEPTION
            'G04 lesson-preparation step is not the reviewed guidance confirmation';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions AS execution
        JOIN tide.task_validation_rules AS rule
          ON rule.execution_version_id = execution.id
        WHERE execution.shared_template_row_id = 'G02:v1'
          AND execution.task_code = 'G04'
          AND execution.status = 'ACTIVE'
          AND rule.rule_key = 'all-steps-complete'
          AND rule.rule_type = 'ALL_STEPS_COMPLETE'
          AND rule.rule_version = '2026-08-11-g04-two-part-v1'
          AND rule.config->'requiredStepKeys' = jsonb_build_array(
              'g02-environment-photo',
              'g02-courseware-confirmation'
          )
    ) THEN
        RAISE EXCEPTION
            'G04 completion rule does not require the two current steps';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions AS execution
        JOIN tide.task_validation_rules AS rule
          ON rule.execution_version_id = execution.id
        WHERE execution.shared_template_row_id = 'G02:v1'
          AND execution.task_code = 'G04'
          AND execution.status = 'ACTIVE'
          AND rule.rule_key = 'g02-environment-ai-review'
          AND rule.rule_type = 'AI_IMAGE_REVIEW'
          AND rule.rule_version = '2026-07-27-strict'
          AND rule.config->>'criteriaVersion' =
              'lesson-preparation-camera-view-2026-08-v7-background-veto'
          AND rule.config->'criteriaKeys' =
              '["camera_angle", "lighting", "background", "dressing"]'::jsonb
    ) THEN
        RAISE EXCEPTION
            'G04 photo rule is not the reviewed four-criterion AI check';
    END IF;

    IF to_regrole('tit_teacher_crud') IS NULL
       OR to_regrole('tit_growth_app') IS NULL
       OR to_regrole('tide_support_ticket_owner') IS NULL THEN
        RAISE EXCEPTION 'required runtime roles are missing';
    END IF;

    IF has_table_privilege(
        'tit_teacher_crud',
        'public.teacher_source_wide',
        'SELECT'
    ) OR NOT has_column_privilege(
        'tit_teacher_crud',
        'public.teacher_source_wide',
        'tchr_id',
        'SELECT'
    ) OR NOT has_column_privilege(
        'tit_teacher_crud',
        'public.teacher_source_wide',
        'is_cpl_tesol',
        'SELECT'
    ) OR has_column_privilege(
        'tit_teacher_crud',
        'public.teacher_source_wide',
        'is_self_introduce',
        'SELECT'
    ) OR has_column_privilege(
        'tit_teacher_crud',
        'public.teacher_source_wide',
        'real_name',
        'SELECT'
    ) THEN
        RAISE EXCEPTION
            'teacher runtime role does not have the reviewed TESOL-only source ACL';
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
