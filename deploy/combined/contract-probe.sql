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
    support_reply_definition text;
    teacher_reply_definition text;
    session_uses_ssl boolean;
BEGIN
    IF current_user IS DISTINCT FROM 'tide_sys_admin'
       OR session_user IS DISTINCT FROM 'tide_sys_admin' THEN
        RAISE EXCEPTION
            'contract probe must connect directly as tide_sys_admin';
    END IF;

    IF current_database() IS DISTINCT FROM current_setting(
        'tide.contract_probe_expected_database'
    ) THEN
        RAISE EXCEPTION 'contract probe target database mismatch';
    END IF;

    IF current_setting('transaction_read_only') IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION 'contract probe transaction is not read-only';
    END IF;

    SELECT COALESCE(
        (
            SELECT ssl
            FROM pg_stat_ssl
            WHERE pid = pg_backend_pid()
        ),
        false
    ) INTO session_uses_ssl;

    IF current_setting('tide.contract_probe_require_ssl')::boolean THEN
        IF NOT session_uses_ssl
           OR current_setting('ssl') IS DISTINCT FROM 'on' THEN
            RAISE EXCEPTION
                'contract probe database session is not using required TLS';
        END IF;
    ELSE
        IF session_uses_ssl
           OR current_setting('ssl') IS DISTINCT FROM 'off' THEN
            RAISE EXCEPTION
                'private-line contract probe requires session TLS off '
                'and server ssl=off';
        END IF;
    END IF;

    IF to_regclass('public.task_templates') IS NULL
       OR to_regclass('public.task_assignments') IS NULL
       OR to_regclass('public.teachers') IS NULL
       OR to_regclass('public.complaint_category_rules') IS NULL
       OR to_regclass('public.operator_sessions') IS NULL
       OR to_regclass('public.teacher_scorecard_current') IS NULL
       OR to_regclass('public.teacher_lesson_score_current') IS NULL
       OR to_regclass('public.teacher_g01_status_current') IS NULL
       OR to_regclass('public.dts_ingest_checkpoints') IS NULL
       OR to_regclass('public.dts_ingest_events') IS NULL
       OR to_regclass('public.dts_source_rows') IS NULL
       OR to_regclass('public.dts_source_partition_epochs') IS NULL
       OR to_regclass('public.dts_source_row_versions') IS NULL
       OR to_regclass('public.dts_dirty_keys') IS NULL
       OR to_regclass('public.dts_dirty_key_inputs') IS NULL
       OR to_regclass('public.dts_dirty_key_dependencies') IS NULL
       OR to_regclass('public.dts_dirty_key_state_audits') IS NULL
       OR to_regclass('public.dts_source_snapshot_desired_rows') IS NULL
       OR to_regprocedure('public.bind_source_snapshot_profile_v3(text,text,text,text,jsonb,text)') IS NULL
       OR to_regprocedure('public.stage_source_snapshot_row_v3(text,text,text,jsonb,jsonb,jsonb,text)') IS NULL
       OR to_regprocedure('public.verify_source_snapshot_candidate_v3(text,text,text,text,bigint,bigint,bigint,text,text,text)') IS NULL
       OR to_regprocedure('public.publish_source_snapshot_candidate_v3(text,text,text,text,bigint,bigint,text,text)') IS NULL
       OR to_regprocedure('public.scope_membership_apply_cdc_v3(text,text,text,bigint,jsonb,jsonb,boolean)') IS NULL
       OR to_regclass('tide.task_execution_versions') IS NULL
       OR to_regclass('tide.file_objects') IS NULL
       OR to_regclass('tide.task_step_definitions') IS NULL
       OR to_regclass('tide.task_validation_rules') IS NULL
       OR to_regclass('tide.account_onboarding_states') IS NULL
       OR to_regclass('tide.user_accounts') IS NULL
       OR to_regclass('tide.auth_sessions') IS NULL
       OR to_regclass('tide.crm_sso_logins') IS NULL
       OR to_regclass('tide.schema_migrations') IS NULL THEN
        RAISE EXCEPTION 'required shared or teacher-side objects are missing';
    END IF;

    -- The combined deployment is pinned to the current public v2 migration
    -- head. Concrete shared rows, teacher executions and runtime guards are
    -- checked below rather than inferred from the Alembic ledger alone.
    IF (
        SELECT version_num
        FROM public.alembic_version
    ) IS DISTINCT FROM '20260823_100_scope_snapshot_diff' THEN
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
            '0037_g04_remove_device_check',
            '0038_personalized_environment_photo',
            '0039_g02_policy_document',
            '0040_g02_document_read_status',
            '0041_crm_sso_hybrid',
            '0042_g09_set_kuozhi_course',
            '0043_p_rel_execution_catalog'
        ]::text[] THEN
        RAISE EXCEPTION
            'teacher production migration ledger is not the exact reviewed chain ending at 0043';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_attribute
        WHERE attrelid = 'tide.user_accounts'::regclass
          AND attname = 'password_hash'
          AND attnum > 0
          AND NOT attisdropped
          AND NOT attnotnull
    ) OR NOT EXISTS (
        SELECT 1
        FROM pg_attribute
        WHERE attrelid = 'tide.user_accounts'::regclass
          AND attname = 'created_via'
          AND attnum > 0
          AND NOT attisdropped
    ) OR NOT EXISTS (
        SELECT 1
        FROM pg_attribute
        WHERE attrelid = 'tide.auth_sessions'::regclass
          AND attname = 'auth_method'
          AND attnum > 0
          AND NOT attisdropped
    ) THEN
        RAISE EXCEPTION 'CRM SSO account or session columns are incomplete';
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
           'Global Communicator Training',
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
          AND payload->>'benefit' =
              'Your profile and required TESOL learning evidence are now complete.'
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
        WHERE row_id = 'G09:v1'
          AND template_id = 'G08'
          AND template_version = 1
          AND status = 'PUBLISHED'
          AND payload->>'template_id' = 'G08'
          AND payload->>'ops_name_zh' = 'Global Communicator 培训'
          AND payload->>'title' = 'Global Communicator Training'
          AND payload->>'why_template' =
              'Learn the core Global Communicator teaching flow.'
          AND payload->>'benefit' =
              'You can now confidently prepare for a Global Communicator lesson.'
          AND payload->>'score_type' = 'FIXED'
          AND (payload->>'score_value')::numeric = 5
    ) THEN
        RAISE EXCEPTION
            'stable G09:v1 row for current G08 does not expose the reviewed Global Communicator copy';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM public.task_templates
        WHERE row_id = 'P-REL-MEMO:v1'
          AND template_id = 'P-REL-MEMO'
          AND template_version = 1
          AND status = 'PUBLISHED'
          AND output_type = 'TEACHER_TASK'
          AND execution_owner = 'TEACHER_APP'
          AND integration_mode = 'OUTBOUND_MANAGED'
          AND source_mode = 'REAL'
          AND payload->>'category' = 'PERSONALIZED_IMPROVEMENT'
          AND payload->>'content_status' = 'READY'
          AND payload->>'why_template' =
              'A completed lesson was recorded with a blank Lesson Memo.'
          AND payload->>'how_summary' =
              'Complete the Lesson Memo guidance and review how to submit an accurate memo after every lesson.'
          AND payload->>'completion_standard' =
              'The teacher app marks the assigned Lesson Memo learning activity as completed.'
          AND payload->>'benefit' =
              'This task carries no points. It helps strengthen your Lesson Memo reliability.'
          AND payload->>'score_type' = 'ZERO'
          AND (payload->>'score_value')::numeric = 0
    ) OR NOT EXISTS (
        SELECT 1
        FROM public.task_templates
        WHERE row_id = 'P-REL-ATTENDANCE:v1'
          AND template_id = 'P-REL-ATTENDANCE'
          AND template_version = 1
          AND status = 'PUBLISHED'
          AND output_type = 'TEACHER_TASK'
          AND execution_owner = 'TEACHER_APP'
          AND integration_mode = 'OUTBOUND_MANAGED'
          AND source_mode = 'REAL'
          AND payload->>'category' = 'PERSONALIZED_IMPROVEMENT'
          AND payload->>'content_status' = 'READY'
          AND payload->>'why_template' =
              'A lesson record shows a reliability issue, such as an absence, late arrival, or early leave.'
          AND payload->>'how_summary' =
              'Complete the assigned attendance training and pass its quiz.'
          AND payload->>'completion_standard' =
              'The teacher app marks the training and quiz as completed.'
          AND payload->>'score_type' = 'ZERO'
          AND (payload->>'score_value')::numeric = 0
    ) THEN
        RAISE EXCEPTION
            'stable reliability templates do not expose the reviewed teacher copy';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM public.task_templates
        WHERE row_id = 'P-FB-NEGATIVE:v1'
          AND template_id = 'P-FB-NEGATIVE'
          AND template_version = 1
          AND status = 'PUBLISHED'
          AND payload->>'template_id' = 'P-FB-NEGATIVE'
          AND payload->>'category' = 'PERSONALIZED_IMPROVEMENT'
          AND payload->>'title' = 'Feedback Improvement'
          AND payload->>'how_summary' =
              'Complete the configured improvement activity for the feedback issue shown in the task reason. Depending on the assigned activity, you may need to submit a teaching-environment photo for review or complete another guided action.'
          AND payload->>'completion_standard' =
              'The teacher app marks the task as completed after every requirement for the assigned improvement activity, including any required photo review, is satisfied.'
          AND payload->>'score_type' = 'ZERO'
          AND (payload->>'score_value')::numeric = 0
    ) THEN
        RAISE EXCEPTION
            'stable P-FB-NEGATIVE:v1 row is not the reviewed zero-point personalized improvement copy';
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

    IF NOT EXISTS (
        SELECT 1
        FROM public.task_templates
        WHERE row_id = 'G03:v1'
          AND template_id = 'G02'
          AND template_version = 1
          AND status = 'PUBLISHED'
          AND payload->>'how_summary' =
              'Read the current Overseas NT Policies document in TIDE. Your reading progress is saved automatically.'
          AND payload->>'completion_standard' =
              'G02 is completed automatically after you reach the end of the current published document.'
    ) THEN
        RAISE EXCEPTION
            'stable G03:v1 row for current G02 does not expose the reviewed document copy';
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

    IF NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions AS execution
        JOIN public.task_templates AS template
          ON template.row_id = execution.shared_template_row_id
        WHERE execution.shared_template_row_id = 'G10:v1'
          AND execution.task_code = 'G09'
          AND execution.status = 'ACTIVE'
          AND execution.execution_contract_version = 'task-contract-v3'
          AND execution.config =
              '{"estimatedMinutes":25,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-19-set-kuozhi-v1","pendingReason":null}'::jsonb
          AND template.template_id = 'G09'
          AND template.status = 'PUBLISHED'
          AND template.payload->>'how_summary' =
              'Complete the three SET videos and their three paired quizzes in Kuozhi.'
          AND template.payload->>'completion_standard' =
              'All three required videos and all three paired quizzes reach 100% progress in Kuozhi.'
          AND NOT EXISTS (
              SELECT 1 FROM tide.task_step_definitions AS definition
              WHERE definition.execution_version_id = execution.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM tide.task_validation_rules AS rule
              WHERE rule.execution_version_id = execution.id
          )
    ) THEN
        RAISE EXCEPTION
            'G09 SET course 658 execution is not the reviewed READY shape';
    END IF;

    IF (
        SELECT count(*)
        FROM tide.task_execution_versions
        WHERE task_code = 'P-REL-MEMO'
    ) <> 1 OR NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions AS execution
        WHERE execution.shared_template_row_id = 'P-REL-MEMO:v1'
          AND execution.task_code = 'P-REL-MEMO'
          AND execution.status = 'ACTIVE'
          AND execution.execution_contract_version = 'task-contract-v3'
          AND execution.config =
              '{"estimatedMinutes":6,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-07-24-lesson-memo-rules-v1","pendingReason":null}'::jsonb
          AND (
              SELECT count(*)
              FROM tide.task_step_definitions AS definition
              WHERE definition.execution_version_id = execution.id
          ) = 1
          AND EXISTS (
              SELECT 1
              FROM tide.task_step_definitions AS definition
              WHERE definition.execution_version_id = execution.id
                AND definition.step_key = 'p-rel-memo-document'
                AND definition.position = 1
                AND definition.step_type = 'DOCUMENT'
                AND definition.title = 'Read Lesson Memo Rules'
                AND definition.config =
                    '{"role":"LESSON_MEMO_RULES_DOCUMENT","documentCode":"lesson-memo-rules","sourceTitle":"Lesson Memo Rules","sourceNodeId":"OG9lyrgJPzkq5xD6fvzmqRonWzN67Mw4","sourceUpdatedAt":"2026-07-24T01:47:08Z","contentVersion":"2026-07-24-lesson-memo-rules-v1","contentHash":"43dde8551988fa167103510da304feac63b853aa03d6c75747086932bf111b51","readingCompletion":"SCROLL_TO_END"}'::jsonb
          )
          AND (
              SELECT count(*)
              FROM tide.task_validation_rules AS rule
              WHERE rule.execution_version_id = execution.id
          ) = 1
          AND EXISTS (
              SELECT 1
              FROM tide.task_validation_rules AS rule
              WHERE rule.execution_version_id = execution.id
                AND rule.rule_key = 'all-steps-complete'
                AND rule.rule_type = 'ALL_STEPS_COMPLETE'
                AND rule.rule_version =
                    '2026-07-24-lesson-memo-rules-v1'
                AND rule.position = 1
                AND rule.config =
                    '{"requiredStepKeys":["p-rel-memo-document"]}'::jsonb
                AND rule.teacher_failure_copy =
                    '请将当前版本的 Lesson Memo Rules 阅读到文档末尾。'
          )
    ) THEN
        RAISE EXCEPTION
            'P-REL-MEMO is not the exact reviewed READY document execution';
    END IF;

    IF (
        SELECT count(*)
        FROM tide.task_execution_versions
        WHERE task_code = 'P-REL-ATTENDANCE'
    ) <> 1 OR NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions AS execution
        WHERE execution.shared_template_row_id = 'P-REL-ATTENDANCE:v1'
          AND execution.task_code = 'P-REL-ATTENDANCE'
          AND execution.status = 'ACTIVE'
          AND execution.execution_contract_version = 'task-contract-v3'
          AND execution.config =
              '{"estimatedMinutes":12,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-22-reliability-course-595-v1","pendingReason":null}'::jsonb
          AND NOT EXISTS (
              SELECT 1
              FROM tide.task_step_definitions AS definition
              WHERE definition.execution_version_id = execution.id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM tide.task_validation_rules AS rule
              WHERE rule.execution_version_id = execution.id
          )
    ) THEN
        RAISE EXCEPTION
            'P-REL-ATTENDANCE is not the exact reviewed READY Kuozhi execution';
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
        WHERE execution.shared_template_row_id = 'G03:v1'
          AND execution.task_code = 'G02'
          AND execution.status = 'ACTIVE'
          AND execution.execution_contract_version = 'task-contract-v3'
          AND execution.config =
              '{"estimatedMinutes":35,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-07-24-overseas-nt-policies-v1","pendingReason":null}'::jsonb
          AND (
              SELECT count(*)
              FROM tide.task_step_definitions AS definition
              WHERE definition.execution_version_id = execution.id
          ) = 1
          AND EXISTS (
              SELECT 1
              FROM tide.task_step_definitions AS definition
              WHERE definition.execution_version_id = execution.id
                AND definition.step_key = 'g02-policy-document'
                AND definition.position = 1
                AND definition.step_type = 'DOCUMENT'
                AND definition.config->>'contentVersion' =
                    '2026-07-24-overseas-nt-policies-v1'
                AND definition.config->>'contentHash' =
                    '6875233667c6f3d90602a07c84849dbb88f41685929779a7b0dc79ccf859979c'
          )
          AND (
              SELECT count(*)
              FROM tide.task_validation_rules AS rule
              WHERE rule.execution_version_id = execution.id
          ) = 1
          AND EXISTS (
              SELECT 1
              FROM tide.task_validation_rules AS rule
              WHERE rule.execution_version_id = execution.id
                AND rule.rule_key = 'all-steps-complete'
                AND rule.rule_type = 'ALL_STEPS_COMPLETE'
                AND rule.rule_version =
                    '2026-08-11-g02-policy-document-v1'
                AND rule.position = 1
                AND rule.config =
                    '{"requiredStepKeys":["g02-policy-document"]}'::jsonb
          )
    ) THEN
        RAISE EXCEPTION
            'G02 teacher execution is not the reviewed policy document';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'tide'
          AND table_name = 'task_step_progress'
          AND column_name = 'reached_end'
          AND data_type = 'boolean'
          AND is_nullable = 'NO'
    ) OR NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'tide.task_step_progress'::regclass
          AND conname = 'task_step_progress_g02_read_status_check'
          AND contype = 'c'
          AND convalidated
    ) OR NOT EXISTS (
        SELECT 1
        FROM pg_trigger
        WHERE tgrelid = 'tide.task_step_progress'::regclass
          AND tgname = 'task_step_progress_g02_assignment_completion_check'
          AND NOT tgisinternal
          AND tgdeferrable
          AND tginitdeferred
    ) THEN
        RAISE EXCEPTION
            'G02 document progress constraints are not the reviewed 0040 shape';
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

    IF NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions AS execution
        JOIN public.task_templates AS template
          ON template.row_id = execution.shared_template_row_id
        WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
          AND execution.task_code = 'P-FB-NEGATIVE'
          AND execution.status = 'ACTIVE'
          AND execution.execution_contract_version = 'task-contract-v3'
          AND execution.config =
              '{"estimatedMinutes":8,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-11-personalized-environment-photo-v1","pendingReason":"JIAHE_PERSONALIZED_CONTENT_PENDING","independentModules":{"stepKeys":["p-fb-negative-environment-photo"],"allowOutOfOrderProgress":true,"keepAssignmentInProgressUntilPassed":true}}'::jsonb
          AND template.template_id = 'P-FB-NEGATIVE'
          AND template.status = 'PUBLISHED'
          AND template.execution_owner = 'TEACHER_APP'
          AND template.payload->>'category' = 'PERSONALIZED_IMPROVEMENT'
          AND (template.payload->>'score_value')::numeric = 0
    ) THEN
        RAISE EXCEPTION
            'P-FB-NEGATIVE is not the exact pending personalized photo execution';
    END IF;

    IF (
        SELECT array_agg(definition.step_key ORDER BY definition.position)
        FROM tide.task_execution_versions AS execution
        JOIN tide.task_step_definitions AS definition
          ON definition.execution_version_id = execution.id
        WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
    ) IS DISTINCT FROM ARRAY['p-fb-negative-environment-photo']::text[]
       OR NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions AS execution
        JOIN tide.task_step_definitions AS definition
          ON definition.execution_version_id = execution.id
        WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
          AND definition.step_key = 'p-fb-negative-environment-photo'
          AND definition.position = 1
          AND definition.step_type = 'UPLOAD'
          AND definition.title = 'Take a teaching-environment photo'
          AND definition.config =
              '{"version":"2026-08-11-personalized-environment-photo-v1","role":"ENVIRONMENT_PHOTO","reviewProfile":"TEACHING_ENVIRONMENT_V1","accept":["image/jpeg"],"captureOnly":true,"maxFiles":1}'::jsonb
    ) THEN
        RAISE EXCEPTION
            'P-FB-NEGATIVE does not have the exact reviewed photo step';
    END IF;

    IF (
        SELECT count(*)
        FROM tide.task_execution_versions AS execution
        JOIN tide.task_validation_rules AS rule
          ON rule.execution_version_id = execution.id
        WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
    ) <> 2 OR NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions AS execution
        JOIN tide.task_validation_rules AS rule
          ON rule.execution_version_id = execution.id
        WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
          AND rule.rule_key = 'all-steps-complete'
          AND rule.rule_type = 'ALL_STEPS_COMPLETE'
          AND rule.rule_version =
              '2026-08-11-personalized-environment-photo-v1'
          AND rule.position = 1
          AND rule.config =
              '{"requiredStepKeys":["p-fb-negative-environment-photo"]}'::jsonb
          AND rule.teacher_failure_copy =
              '请拍摄并提交一张当前授课环境照片。'
    ) OR NOT EXISTS (
        SELECT 1
        FROM tide.task_execution_versions AS execution
        JOIN tide.task_validation_rules AS rule
          ON rule.execution_version_id = execution.id
        WHERE execution.shared_template_row_id = 'P-FB-NEGATIVE:v1'
          AND rule.rule_key = 'p-fb-negative-environment-ai-review'
          AND rule.rule_type = 'AI_IMAGE_REVIEW'
          AND rule.rule_version = '2026-07-27-strict'
          AND rule.position = 2
          AND rule.config =
              '{"stepKey":"p-fb-negative-environment-photo","criteriaVersion":"personalized-teaching-environment-2026-08-v1","criteriaKeys":["camera_angle","lighting","background","dressing"],"allowedMimeTypes":["image/jpeg","image/png","image/webp"],"reviewProfile":"TEACHING_ENVIRONMENT_V1","systemPrompt":"You strictly review teacher-submitted evidence. Return JSON only with this exact shape: {\"decision\":\"PASS|RETRY|ERROR\",\"teacherReason\":\"teacher-safe concise message\",\"confidenceSummary\":{},\"criteria\":[{\"criterionKey\":\"one configured key\",\"result\":\"PASS|FAIL|UNKNOWN\",\"teacherMessage\":\"teacher-safe message or null\"}]}. Include every configured criterion exactly once. Never infer a pass from the mere presence of a person or object. Use UNKNOWN whenever the visual evidence is unclear. PASS only when every configured criterion is visibly and unambiguously PASS; any FAIL or UNKNOWN requires RETRY. Use ERROR only when the file cannot be assessed. Do not expose internal risk labels or private model reasoning.","userText":"Review this current teaching-environment photo strictly against camera angle, lighting, background and dressing only."}'::jsonb
          AND rule.teacher_failure_copy =
              '已保留你完成的内容，请根据提示更新这份材料。'
    ) THEN
        RAISE EXCEPTION
            'P-FB-NEGATIVE photo rules are not the exact four-criterion review';
    END IF;

    IF to_regrole('tit_teacher_crud') IS NULL
       OR to_regrole('tit_growth_app') IS NULL
       OR to_regrole('tit_dts_ingest_runtime') IS NULL
       OR to_regrole('tide_support_ticket_owner') IS NULL THEN
        RAISE EXCEPTION 'required runtime roles are missing';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname IN (
            'tit_growth_app',
            'tit_teacher_crud',
            'tit_dts_ingest_runtime'
        )
          AND (
              NOT rolcanlogin
              OR rolinherit
              OR rolsuper
              OR rolcreatedb
              OR rolcreaterole
              OR rolreplication
              OR rolbypassrls
          )
    ) OR EXISTS (
        SELECT 1
        FROM pg_auth_members AS membership
        JOIN pg_roles AS member_role
          ON member_role.oid = membership.member
        WHERE member_role.rolname IN (
            'tit_growth_app',
            'tit_teacher_crud',
            'tit_dts_ingest_runtime'
        )
    ) THEN
        RAISE EXCEPTION
            'runtime service roles are privileged or inherit another role';
    END IF;

    -- The probe itself deliberately runs as tide_sys_admin inside a read-only
    -- transaction. Validate DDL capability on the three runtime identities,
    -- not on that migration owner: runtime DML is checked table by table below.
    IF EXISTS (
        SELECT 1
        FROM unnest(ARRAY[
            'tit_growth_app',
            'tit_teacher_crud',
            'tit_dts_ingest_runtime'
        ]::text[]) AS runtime_role(name)
        WHERE has_database_privilege(
            runtime_role.name,
            current_database(),
            'CREATE'
        )
           OR has_schema_privilege(
               runtime_role.name,
               'public',
               'CREATE'
           )
           OR has_schema_privilege(
               runtime_role.name,
               'tide',
               'CREATE'
           )
    ) THEN
        RAISE EXCEPTION
            'contract probe role has write-capable privileges';
    END IF;

    -- This is the baseline for future ordinary Tide tables. A table migration
    -- may narrow it after CREATE; 0041 does that for crm_sso_logins.
    IF NOT EXISTS (
        SELECT 1
        FROM pg_default_acl AS defaults
        JOIN pg_roles AS owner_role
          ON owner_role.oid = defaults.defaclrole
        JOIN pg_namespace AS namespace
          ON namespace.oid = defaults.defaclnamespace
        CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS privilege
        JOIN pg_roles AS grantee_role
          ON grantee_role.oid = privilege.grantee
        WHERE owner_role.rolname = 'tide_sys_admin'
          AND namespace.nspname = 'tide'
          AND defaults.defaclobjtype = 'r'
          AND grantee_role.rolname = 'tit_teacher_crud'
        GROUP BY defaults.oid
        HAVING array_agg(DISTINCT privilege.privilege_type)
                   @> ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']::text[]
           AND NOT (
               array_agg(DISTINCT privilege.privilege_type)
                   && ARRAY['TRUNCATE', 'TRIGGER']::text[]
           )
    ) THEN
        RAISE EXCEPTION
            'future tide tables will not inherit teacher runtime CRUD';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        CROSS JOIN LATERAL unnest(
            ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']::text[]
        ) AS required_privilege(name)
        WHERE namespace.nspname = 'tide'
          AND relation.relkind IN ('r', 'p')
          AND relation.relname <> 'crm_sso_logins'
          AND NOT has_table_privilege(
              'tit_teacher_crud',
              relation.oid,
              required_privilege.name
          )
    ) OR EXISTS (
        SELECT 1
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        CROSS JOIN LATERAL unnest(
            ARRAY['TRUNCATE', 'TRIGGER']::text[]
        ) AS forbidden_privilege(name)
        WHERE namespace.nspname = 'tide'
          AND relation.relkind IN ('r', 'p')
          AND has_table_privilege(
              'tit_teacher_crud',
              relation.oid,
              forbidden_privilege.name
          )
    ) THEN
        RAISE EXCEPTION 'teacher runtime tide-table CRUD is incomplete';
    END IF;

    -- public 99 keeps raw DTS ingestion least-privileged: immutable source
    -- versions are append-only, current source rows are upsertable, checkpoint
    -- state is update-only, and dirty queues are reached through reviewed
    -- SECURITY DEFINER functions rather than direct table grants.
    IF EXISTS (
        SELECT 1
        FROM (
            VALUES
                ('public.teacher_source_wide'::text,
                    ARRAY['SELECT']::text[]),
                ('public.lesson_source_wide'::text,
                    ARRAY['SELECT']::text[]),
                ('public.dts_source_partition_epochs'::text,
                    ARRAY['SELECT']::text[]),
                ('public.dts_source_row_versions'::text,
                    ARRAY['SELECT', 'INSERT']::text[]),
                ('public.dts_source_rows'::text,
                    ARRAY['SELECT', 'INSERT', 'UPDATE']::text[]),
                ('public.dts_ingest_events'::text,
                    ARRAY['SELECT', 'INSERT']::text[]),
                ('public.dts_ingest_checkpoints'::text,
                    ARRAY['SELECT', 'UPDATE']::text[]),
                ('public.dts_dirty_keys'::text, ARRAY[]::text[]),
                ('public.dts_dirty_key_inputs'::text, ARRAY[]::text[]),
                ('public.dts_dirty_key_dependencies'::text, ARRAY[]::text[]),
                ('public.dts_dirty_key_state_audits'::text, ARRAY[]::text[])
        ) AS dts_table(name, expected_privileges)
        CROSS JOIN LATERAL unnest(ARRAY[
            'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'TRIGGER'
        ]::text[]) AS checked_privilege(name)
        WHERE has_table_privilege(
            'tit_dts_ingest_runtime',
            dts_table.name,
            checked_privilege.name
        ) IS DISTINCT FROM (
            checked_privilege.name = ANY(dts_table.expected_privileges)
        )
    ) THEN
        RAISE EXCEPTION
            'DTS runtime table ACL is not the exact public 99 ingest matrix';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        CROSS JOIN LATERAL unnest(
            ARRAY[
                'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'TRIGGER'
            ]::text[]
        ) AS forbidden_privilege(name)
        WHERE namespace.nspname IN ('public', 'tide')
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND relation.oid NOT IN (
              'public.dts_source_row_versions'::regclass,
              'public.dts_source_rows'::regclass,
              'public.dts_ingest_events'::regclass,
              'public.dts_ingest_checkpoints'::regclass
          )
          AND has_table_privilege(
              'tit_dts_ingest_runtime',
              relation.oid,
              forbidden_privilege.name
          )
    ) THEN
        RAISE EXCEPTION
            'DTS runtime can mutate a relation outside its public 99 ingest boundary';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM unnest(ARRAY[
            'public.teacher_source_wide',
            'public.lesson_source_wide'
        ]::text[]) AS source_table(name),
        unnest(ARRAY['SELECT']::text[]) AS required_privilege(name)
        WHERE NOT has_table_privilege(
            'tit_growth_app', source_table.name, required_privilege.name
        )
    ) OR EXISTS (
        SELECT 1
        FROM unnest(ARRAY[
            'public.teacher_source_wide',
            'public.lesson_source_wide'
        ]::text[]) AS source_table(name),
        unnest(ARRAY[
            'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'TRIGGER'
        ]::text[]) AS forbidden_privilege(name)
        WHERE has_table_privilege(
            'tit_growth_app', source_table.name, forbidden_privilege.name
        )
    ) THEN
        RAISE EXCEPTION 'TiDe runtime source-wide access is not read-only';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM unnest(ARRAY[
            'public.teachers',
            'public.complaint_category_rules',
            'public.personalized_trigger_matches',
            'public.lesson_score_results',
            'public.teacher_qualifications',
            'public.score_accounts',
            'public.score_component_accounts',
            'public.score_entries',
            'public.task_templates',
            'public.task_assignments',
            'public.notifications',
            'public.ops_cases',
            'public.ops_decisions',
            'public.outbox_events',
            'public.audit_events',
            'public.idempotency_records',
            'public.config_versions',
            'public.config_publication_audits',
            'public.operator_accounts',
            'public.operator_role_grants',
            'public.operator_sessions',
            'public.teacher_support_tickets'
        ]::text[]) AS business_table(name),
        unnest(ARRAY[
            'SELECT', 'INSERT', 'UPDATE', 'DELETE'
        ]::text[]) AS required_privilege(name)
        WHERE NOT has_table_privilege(
            'tit_growth_app', business_table.name, required_privilege.name
        )
    ) OR EXISTS (
        SELECT 1
        FROM unnest(ARRAY[
            'public.teachers',
            'public.complaint_category_rules',
            'public.personalized_trigger_matches',
            'public.lesson_score_results',
            'public.teacher_qualifications',
            'public.score_accounts',
            'public.score_component_accounts',
            'public.score_entries',
            'public.task_templates',
            'public.task_assignments',
            'public.notifications',
            'public.ops_cases',
            'public.ops_decisions',
            'public.outbox_events',
            'public.audit_events',
            'public.idempotency_records',
            'public.config_versions',
            'public.config_publication_audits',
            'public.operator_accounts',
            'public.operator_role_grants',
            'public.operator_sessions',
            'public.teacher_support_tickets'
        ]::text[]) AS business_table(name),
        unnest(ARRAY['TRUNCATE', 'TRIGGER']::text[]) AS forbidden_privilege(name)
        WHERE has_table_privilege(
            'tit_growth_app', business_table.name, forbidden_privilege.name
        )
    ) THEN
        RAISE EXCEPTION 'TiDe runtime business-table CRUD is incomplete';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM unnest(ARRAY[
            'public.alembic_version',
            'public.task_templates',
            'public.teachers',
            'public.teacher_scorecard_current',
            'public.teacher_lesson_score_current',
            'public.teacher_g01_status_current'
        ]::text[]) AS read_table(name)
        WHERE NOT has_table_privilege(
            'tit_teacher_crud', read_table.name, 'SELECT'
        )
    ) OR EXISTS (
        SELECT 1
        FROM unnest(ARRAY[
            'public.alembic_version',
            'public.task_templates',
            'public.teachers',
            'public.teacher_scorecard_current',
            'public.teacher_lesson_score_current',
            'public.teacher_g01_status_current'
        ]::text[]) AS read_table(name),
        unnest(ARRAY[
            'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'TRIGGER'
        ]::text[]) AS forbidden_privilege(name)
        WHERE has_table_privilege(
            'tit_teacher_crud', read_table.name, forbidden_privilege.name
        )
    ) THEN
        RAISE EXCEPTION 'teacher runtime public read-only ACL is invalid';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM unnest(ARRAY[
            'public.task_assignments',
            'public.notifications',
            'public.notification_events',
            'public.teacher_support_tickets'
        ]::text[]) AS business_table(name),
        unnest(ARRAY[
            'SELECT', 'INSERT', 'UPDATE', 'DELETE'
        ]::text[]) AS required_privilege(name)
        WHERE NOT has_table_privilege(
            'tit_teacher_crud', business_table.name, required_privilege.name
        )
    ) OR EXISTS (
        SELECT 1
        FROM unnest(ARRAY[
            'public.task_assignments',
            'public.notifications',
            'public.notification_events',
            'public.teacher_support_tickets'
        ]::text[]) AS business_table(name),
        unnest(ARRAY['TRUNCATE', 'TRIGGER']::text[]) AS forbidden_privilege(name)
        WHERE has_table_privilege(
            'tit_teacher_crud', business_table.name, forbidden_privilege.name
        )
    ) THEN
        RAISE EXCEPTION 'teacher runtime public business-table CRUD is incomplete';
    END IF;

    IF has_table_privilege(
        'tit_teacher_crud',
        'public.teacher_source_wide',
        'SELECT'
    ) OR (
        SELECT array_agg(column_name::text ORDER BY ordinal_position)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'teacher_g01_status_current'
    ) IS DISTINCT FROM ARRAY['tchr_id', 'is_cpl_tesol']::text[] THEN
        RAISE EXCEPTION
            'teacher runtime role can bypass the TESOL-only view boundary';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname = 'tide_support_ticket_owner'
          AND (
              rolcanlogin
              OR rolinherit
              OR rolsuper
              OR rolcreatedb
              OR rolcreaterole
              OR rolreplication
              OR rolbypassrls
          )
    ) THEN
        RAISE EXCEPTION 'support-ticket function owner is not a restricted NOLOGIN role';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_auth_members AS membership
        JOIN pg_roles AS granted_role
          ON granted_role.oid = membership.roleid
        JOIN pg_roles AS member_role
          ON member_role.oid = membership.member
        WHERE granted_role.rolname = 'tide_support_ticket_owner'
          AND member_role.rolname = 'tide_sys_admin'
          AND membership.set_option
    ) OR EXISTS (
        SELECT 1
        FROM pg_auth_members AS membership
        JOIN pg_roles AS granted_role
          ON granted_role.oid = membership.roleid
        JOIN pg_roles AS member_role
          ON member_role.oid = membership.member
        WHERE granted_role.rolname = 'tide_support_ticket_owner'
          AND member_role.rolname <> 'tide_sys_admin'
          AND (
              NOT membership.admin_option
              OR membership.inherit_option
              OR membership.set_option
          )
    ) THEN
        RAISE EXCEPTION
            'support-ticket owner membership grants runtime access outside tide_sys_admin';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM unnest(ARRAY[
            'SELECT', 'INSERT', 'UPDATE', 'DELETE'
        ]::text[]) AS required_privilege(name)
        WHERE NOT has_table_privilege(
            'tide_support_ticket_owner',
            'public.teacher_support_tickets',
            required_privilege.name
        )
    ) OR EXISTS (
        SELECT 1
        FROM unnest(ARRAY['TRUNCATE', 'TRIGGER']::text[]) AS forbidden_privilege(name)
        WHERE has_table_privilege(
            'tide_support_ticket_owner',
            'public.teacher_support_tickets',
            forbidden_privilege.name
        )
    ) THEN
        RAISE EXCEPTION 'support-ticket function owner table CRUD is incomplete';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'public.task_assignments'::regclass
          AND tgfoid =
              'public.enforce_task_assignment_write()'::regprocedure
          AND NOT tgisinternal
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'public.outbox_events'::regclass
          AND tgname = 'guard_outbox_event_update'
          AND NOT tgisinternal
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'public.lesson_score_results'::regclass
          AND tgname = 'guard_lesson_score_result_identity'
          AND NOT tgisinternal
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'public.operator_accounts'::regclass
          AND tgname = 'guard_operator_account_runtime_update'
          AND NOT tgisinternal
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'public.notifications'::regclass
          AND tgname = 'guard_teacher_notification_write'
          AND NOT tgisinternal
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'public.notification_events'::regclass
          AND tgname = 'guard_notification_event_history'
          AND NOT tgisinternal
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'public.audit_events'::regclass
          AND tgname = 'guard_audit_event_history'
          AND NOT tgisinternal
    ) OR (
        SELECT count(*)
        FROM pg_trigger
        WHERE tgrelid = ANY (ARRAY[
            'public.score_entries'::regclass,
            'public.idempotency_records'::regclass,
            'public.config_publication_audits'::regclass,
            'public.ops_decisions'::regclass
        ])
          AND tgname = 'guard_runtime_append_only_fact'
          AND NOT tgisinternal
    ) <> 4 OR NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'public.teacher_support_tickets'::regclass
          AND tgname = 'guard_simple_support_ticket_write'
          AND NOT tgisinternal
    ) OR (
        SELECT count(*)
        FROM pg_trigger
        WHERE tgrelid = ANY (ARRAY[
            'public.dts_ingest_checkpoints'::regclass,
            'public.dts_ingest_events'::regclass,
            'public.dts_source_rows'::regclass,
            'public.dts_dirty_keys'::regclass
        ])
          AND tgname = 'guard_dts_runtime_state_write'
          AND NOT tgisinternal
    ) <> 4 OR NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'public.lesson_source_wide'::regclass
          AND tgname = 'guard_dom_lesson_student_privacy_v1'
          AND tgfoid =
              'public.guard_dom_lesson_student_privacy_v1()'::regprocedure
          AND tgenabled IN ('O', 'A')
          AND tgtype = 23
          AND NOT tgisinternal
    ) OR NOT EXISTS (
        SELECT 1
        FROM pg_proc AS privacy_function
        JOIN pg_namespace AS privacy_namespace
          ON privacy_namespace.oid = privacy_function.pronamespace
        WHERE privacy_namespace.nspname = 'public'
          AND privacy_function.proname =
              'guard_dom_lesson_student_privacy_v1'
          AND pg_get_function_identity_arguments(privacy_function.oid) = ''
          AND position(
              'tit.dts_source_region' IN privacy_function.prosrc
          ) > 0
          AND position(
              'tit_dts_ingest_runtime' IN privacy_function.prosrc
          ) > 0
    ) OR to_regprocedure(
        'public.dom_student_json_is_safe_v1(jsonb)'
    ) IS NULL OR NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'tide.schema_migrations'::regclass
          AND tgname = 'guard_runtime_schema_migration_write'
          AND NOT tgisinternal
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'tide.crm_sso_logins'::regclass
          AND tgname = 'guard_crm_sso_login_write'
          AND NOT tgisinternal
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'tide.system_notifications'::regclass
          AND tgname = 'protect_system_notification_content'
          AND NOT tgisinternal
    ) THEN
        RAISE EXCEPTION 'runtime table-level business guards are incomplete';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_attribute AS attribute
        CROSS JOIN LATERAL aclexplode(attribute.attacl) AS privilege
        JOIN pg_roles AS grantee ON grantee.oid = privilege.grantee
        JOIN pg_class AS relation
          ON relation.oid = attribute.attrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname IN ('public', 'tide')
          AND attribute.attacl IS NOT NULL
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
          AND grantee.rolname IN (
              'tit_growth_app',
              'tit_teacher_crud',
              'tit_dts_ingest_runtime',
              'tide_support_ticket_owner'
          )
    ) THEN
        RAISE EXCEPTION 'runtime roles still have explicit column ACL';
    END IF;

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

    IF EXISTS (
        SELECT 1
        FROM (
            VALUES
                ('public.create_teacher_support_ticket(uuid,character varying,character varying,character varying,jsonb,jsonb)'::regprocedure, 'tit_growth_app'::text, false),
                ('public.create_teacher_support_ticket(uuid,character varying,character varying,character varying,jsonb,jsonb)'::regprocedure, 'tit_teacher_crud'::text, true),
                ('public.create_teacher_support_ticket(uuid,character varying,character varying,character varying,jsonb,jsonb)'::regprocedure, 'tit_dts_ingest_runtime'::text, false),
                ('public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)'::regprocedure, 'tit_growth_app'::text, false),
                ('public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)'::regprocedure, 'tit_teacher_crud'::text, true),
                ('public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)'::regprocedure, 'tit_dts_ingest_runtime'::text, false),
                ('public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'::regprocedure, 'tit_growth_app'::text, true),
                ('public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'::regprocedure, 'tit_teacher_crud'::text, false),
                ('public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'::regprocedure, 'tit_dts_ingest_runtime'::text, false),
                ('public.mark_teacher_support_ticket_images_deleted(uuid,timestamp with time zone)'::regprocedure, 'tit_growth_app'::text, false),
                ('public.mark_teacher_support_ticket_images_deleted(uuid,timestamp with time zone)'::regprocedure, 'tit_teacher_crud'::text, true),
                ('public.mark_teacher_support_ticket_images_deleted(uuid,timestamp with time zone)'::regprocedure, 'tit_dts_ingest_runtime'::text, false)
        ) AS expected(function_oid, role_name, can_execute)
        WHERE has_function_privilege(
            expected.role_name,
            expected.function_oid,
            'EXECUTE'
        ) IS DISTINCT FROM expected.can_execute
    ) OR EXISTS (
        SELECT 1
        FROM unnest(ARRAY[
            'public.create_teacher_support_ticket(uuid,character varying,character varying,character varying,jsonb,jsonb)'::regprocedure,
            'public.append_teacher_support_ticket_teacher_message(uuid,character varying,bigint,jsonb)'::regprocedure,
            'public.append_teacher_support_ticket_operator_message(uuid,bigint,jsonb)'::regprocedure,
            'public.mark_teacher_support_ticket_images_deleted(uuid,timestamp with time zone)'::regprocedure
        ]) AS secured(function_oid)
        JOIN pg_proc AS procedure ON procedure.oid = secured.function_oid
        CROSS JOIN LATERAL aclexplode(
            COALESCE(
                procedure.proacl,
                acldefault('f', procedure.proowner)
            )
        ) AS privilege
        WHERE privilege.grantee = 0
          AND privilege.privilege_type = 'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'support-ticket function execution ACL is not exact';
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

    IF EXISTS (
        SELECT 1
        FROM unnest(
            ARRAY['SELECT', 'INSERT', 'UPDATE']::text[]
        ) AS required_privilege(name)
        WHERE NOT has_table_privilege(
            'tit_teacher_crud',
            'tide.crm_sso_logins',
            required_privilege.name
        )
    ) OR has_table_privilege(
        'tit_teacher_crud',
        'tide.crm_sso_logins',
        'DELETE'
    ) THEN
        RAISE EXCEPTION 'teacher runtime CRM SSO ACL is invalid';
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
