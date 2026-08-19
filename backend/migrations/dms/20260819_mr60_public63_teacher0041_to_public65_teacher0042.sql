-- MR #60 atomic DMS forward migration.
-- Approved input: public 20260819_63_dts_direct_privacy / teacher 0041_crm_sso_hybrid.
-- Output: public 20260819_65_g09_set_course / teacher 0042_g09_set_kuozhi_course.
-- Execute this file as one unit. Any failed guard rolls back every change.

BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '5min';

SELECT pg_advisory_xact_lock(
    hashtextextended('tit:mr60:public65:teacher0042', 0)
);

DO $preflight$
DECLARE
    public_head text;
    teacher_head text;
    teacher_count integer;
BEGIN
    IF current_setting('transaction_read_only')::boolean THEN
        RAISE EXCEPTION 'MR60 migration requires a writable DMS transaction';
    END IF;

    IF to_regclass('public.alembic_version') IS NULL
       OR to_regclass('public.task_templates') IS NULL
       OR to_regclass('public.task_assignments') IS NULL
       OR to_regclass('tide.schema_migrations') IS NULL
       OR to_regclass('tide.task_execution_versions') IS NULL
       OR to_regclass('tide.task_step_definitions') IS NULL
       OR to_regclass('tide.task_validation_rules') IS NULL THEN
        RAISE EXCEPTION 'MR60 migration requires the canonical public and tide schemas';
    END IF;

    SELECT CASE WHEN count(*) = 1 THEN min(version_num) END
    INTO public_head
    FROM public.alembic_version;

    IF public_head IS DISTINCT FROM '20260819_63_dts_direct_privacy' THEN
        RAISE EXCEPTION
            'MR60 migration requires public head 20260819_63_dts_direct_privacy; current=%',
            coalesce(public_head, '<invalid>');
    END IF;

    SELECT count(*), max(migration_id) FILTER (
        WHERE migration_order = (
            SELECT max(migration_order) FROM tide.schema_migrations
        )
    )
    INTO teacher_count, teacher_head
    FROM tide.schema_migrations;

    IF teacher_count <> 36
       OR teacher_head IS DISTINCT FROM '0041_crm_sso_hybrid'
       OR NOT EXISTS (
           SELECT 1
           FROM tide.schema_migrations
           WHERE migration_order = 36
             AND migration_id = '0041_crm_sso_hybrid'
       ) THEN
        RAISE EXCEPTION
            'MR60 migration requires the exact 36-row teacher ledger ending at 0041; count=%, head=%',
            teacher_count,
            coalesce(teacher_head, '<invalid>');
    END IF;

    IF to_regprocedure('public.enforce_task_assignment_write()') IS NULL
       OR NOT EXISTS (
           SELECT 1
           FROM pg_trigger
           WHERE tgrelid = 'public.task_assignments'::regclass
             AND tgname = 'trg_task_assignment_write'
             AND NOT tgisinternal
       ) THEN
        RAISE EXCEPTION 'MR60 migration requires the canonical task assignment trigger';
    END IF;

    IF (
        SELECT count(*)
        FROM public.task_templates
        WHERE row_id = 'G06:v1'
          AND template_id = 'G05'
          AND template_version = 1
          AND status = 'PUBLISHED'
          AND execution_owner = 'TEACHER_APP'
          AND payload->>'template_id' = 'G05'
          AND payload->>'category' = 'MANDATORY_GROWTH'
          AND payload->>'score_type' = 'FIXED'
          AND (payload->>'score_value')::integer = 3
          AND payload->>'content_status' = 'READY'
          AND payload->>'ops_name_zh' = 'TTP 入门'
          AND payload->>'title' = 'TTP Orientation'
          AND payload->>'why_template' =
              'Understand TTP and its key business scenarios.'
          AND payload->>'how_summary' =
              'Watch the in-platform TTP video and confirm every item in the learning checklist.'
          AND payload->>'completion_standard' =
              'The TTP video is watched in full and every published checklist item is confirmed.'
          AND payload->>'benefit' =
              'You understand the key TTP workflow and commitments.'
    ) <> 1 THEN
        RAISE EXCEPTION 'MR60 migration found unreviewed public63 G05 copy drift';
    END IF;

    IF (
        SELECT count(*)
        FROM public.task_templates
        WHERE row_id = 'G09:v1'
          AND template_id = 'G08'
          AND template_version = 1
          AND status = 'PUBLISHED'
          AND execution_owner = 'TEACHER_APP'
          AND payload->>'template_id' = 'G08'
          AND payload->>'category' = 'MANDATORY_GROWTH'
          AND payload->>'score_type' = 'FIXED'
          AND (payload->>'score_value')::integer = 5
          AND payload->>'content_status' = 'READY'
          AND payload->>'ops_name_zh' = 'Global Communicator 培训'
          AND payload->>'title' = 'Global Communicator Training'
          AND payload->>'why_template' =
              'Learn the core Global Communicator teaching flow.'
          AND payload->>'how_summary' =
              'Complete the configured in-platform videos and quiz.'
          AND payload->>'completion_standard' =
              'All configured videos and quiz requirements pass.'
          AND payload->>'benefit' =
              'You can now confidently prepare for a Global Communicator lesson.'
    ) <> 1 THEN
        RAISE EXCEPTION 'MR60 migration found unreviewed public63 G08 copy drift';
    END IF;

    IF (
        SELECT count(*)
        FROM public.task_templates
        WHERE row_id = 'G10:v1'
          AND template_id = 'G09'
          AND template_version = 1
          AND status = 'PUBLISHED'
          AND execution_owner = 'TEACHER_APP'
          AND payload->>'template_id' = 'G09'
          AND payload->>'category' = 'MANDATORY_GROWTH'
          AND payload->>'score_type' = 'FIXED'
          AND (payload->>'score_value')::integer = 5
          AND payload->>'content_status' = 'READY'
          AND payload->>'ops_name_zh' = 'SET 教学基础'
          AND payload->>'title' = 'SET Teaching Fundamentals'
          AND payload->>'why_template' =
              'Learn the fundamentals of SET teaching.'
          AND payload->>'how_summary' =
              'Watch the in-platform Mock video slot and complete the five-question Mock check.'
          AND payload->>'completion_standard' =
              'The Mock video is watched in full and the five-question check reaches 80%.'
          AND payload->>'benefit' =
              'You understand the SET teaching foundation.'
    ) <> 1 THEN
        RAISE EXCEPTION 'MR60 migration found unreviewed public63 G09 copy drift';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.task_assignments
        WHERE task_code = 'G08'
          AND task_kind = 'FIXED_GROWTH'
          AND (
              why NOT IN (
                  'Learn the core Cocos teaching flow.',
                  'Learn the core Global Communicator teaching flow.'
              )
              OR (
                  display_title IS NOT NULL
                  AND display_title NOT IN (
                      '',
                      'Cocos Course Training',
                      'Global Communicator Training'
                  )
              )
          )
    ) THEN
        RAISE EXCEPTION 'MR60 migration found unreviewed G08 assignment copy drift';
    END IF;
END
$preflight$;

LOCK TABLE public.task_templates IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE public.task_assignments IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE tide.schema_migrations IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE tide.task_execution_versions IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE tide.task_step_definitions IN SHARE MODE;
LOCK TABLE tide.task_validation_rules IN SHARE MODE;

DO $public64$
DECLARE
    changed integer;
BEGIN
    UPDATE public.task_templates
    SET payload = payload || jsonb_build_object(
            'ops_name_zh', 'TTP 入门',
            'title', 'TTP Orientation',
            'why_template', 'Understand TTP and its key business scenarios.',
            'how_summary', 'Complete the TTP video and Quiz in Kuozhi.',
            'completion_standard',
                'The TTP video reaches 100% progress and the Quiz is completed in Kuozhi.',
            'benefit', 'You understand the key TTP workflow and commitments.'
        ),
        revision = revision + 1,
        updated_by = 'SYSTEM_MIGRATION_20260819_64_G05_G08',
        updated_at = now()
    WHERE row_id = 'G06:v1'
      AND template_id = 'G05'
      AND template_version = 1
      AND status = 'PUBLISHED';
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed <> 1 THEN
        RAISE EXCEPTION 'MR60 public64 did not update exactly one G05 row';
    END IF;

    UPDATE public.task_templates
    SET payload = payload || jsonb_build_object(
            'ops_name_zh', 'Global Communicator 培训',
            'title', 'Global Communicator Training',
            'why_template', 'Learn the core Global Communicator teaching flow.',
            'how_summary',
                'Complete all six Global Communicator Sample Lessons videos in Kuozhi.',
            'completion_standard',
                'All six required videos reach 100% progress in Kuozhi.',
            'benefit',
                'You can now confidently prepare for a Global Communicator lesson.'
        ),
        revision = revision + 1,
        updated_by = 'SYSTEM_MIGRATION_20260819_64_G05_G08',
        updated_at = now()
    WHERE row_id = 'G09:v1'
      AND template_id = 'G08'
      AND template_version = 1
      AND status = 'PUBLISHED';
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed <> 1 THEN
        RAISE EXCEPTION 'MR60 public64 did not update exactly one G08 row';
    END IF;
END
$public64$;

DROP TRIGGER trg_task_assignment_write ON public.task_assignments;

UPDATE public.task_assignments
SET why = CASE
        WHEN why = 'Learn the core Cocos teaching flow.'
            THEN 'Learn the core Global Communicator teaching flow.'
        ELSE why
    END,
    display_title = CASE
        WHEN display_title = 'Cocos Course Training'
            THEN 'Global Communicator Training'
        ELSE display_title
    END,
    row_version = row_version + 1,
    updated_by = 'SYSTEM_MIGRATION_20260819_64_G05_G08',
    updated_at = now()
WHERE task_code = 'G08'
  AND task_kind = 'FIXED_GROWTH'
  AND (
      why = 'Learn the core Cocos teaching flow.'
      OR display_title = 'Cocos Course Training'
  );

CREATE TRIGGER trg_task_assignment_write
BEFORE INSERT OR UPDATE ON public.task_assignments
FOR EACH ROW EXECUTE FUNCTION public.enforce_task_assignment_write();

DO $public64_head$
DECLARE
    changed integer;
BEGIN
    UPDATE public.alembic_version
    SET version_num = '20260819_64_g05_g08_courses'
    WHERE version_num = '20260819_63_dts_direct_privacy';
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed <> 1 THEN
        RAISE EXCEPTION 'MR60 failed to advance the public ledger to revision 64';
    END IF;
END
$public64_head$;

DO $public65$
DECLARE
    changed integer;
BEGIN
    UPDATE public.task_templates
    SET payload = payload || jsonb_build_object(
            'ops_name_zh', 'SET 教学基础',
            'title', 'SET Teaching Fundamentals',
            'why_template', 'Learn the fundamentals of SET teaching.',
            'how_summary',
                'Complete the three SET videos and their three paired quizzes in Kuozhi.',
            'completion_standard',
                'All three required videos and all three paired quizzes reach 100% progress in Kuozhi.',
            'benefit', 'You understand the SET teaching foundation.'
        ),
        revision = revision + 1,
        updated_by = 'SYSTEM_MIGRATION_20260819_65_G09_SET_COURSE',
        updated_at = now()
    WHERE row_id = 'G10:v1'
      AND template_id = 'G09'
      AND template_version = 1
      AND status = 'PUBLISHED';
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed <> 1 THEN
        RAISE EXCEPTION 'MR60 public65 did not update exactly one G09 row';
    END IF;

    UPDATE public.alembic_version
    SET version_num = '20260819_65_g09_set_course'
    WHERE version_num = '20260819_64_g05_g08_courses';
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed <> 1 THEN
        RAISE EXCEPTION 'MR60 failed to advance the public ledger to revision 65';
    END IF;
END
$public65$;

DO $teacher42_preflight$
DECLARE
    execution_id uuid;
BEGIN
    IF (
        SELECT count(*)
        FROM public.task_templates
        WHERE row_id = 'G10:v1'
          AND template_id = 'G09'
          AND template_version = 1
          AND status = 'PUBLISHED'
          AND execution_owner = 'TEACHER_APP'
          AND payload->>'category' = 'MANDATORY_GROWTH'
          AND payload->>'ops_name_zh' = 'SET 教学基础'
          AND payload->>'title' = 'SET Teaching Fundamentals'
          AND payload->>'why_template' =
              'Learn the fundamentals of SET teaching.'
          AND payload->>'how_summary' =
              'Complete the three SET videos and their three paired quizzes in Kuozhi.'
          AND payload->>'completion_standard' =
              'All three required videos and all three paired quizzes reach 100% progress in Kuozhi.'
          AND payload->>'benefit' =
              'You understand the SET teaching foundation.'
          AND payload->>'content_status' = 'READY'
          AND (payload->>'score_value')::integer = 5
    ) <> 1 THEN
        RAISE EXCEPTION
            'teacher 0042 requires the complete code-canonical G09 copy from public65';
    END IF;

    SELECT id INTO execution_id
    FROM tide.task_execution_versions
    WHERE shared_template_row_id = 'G10:v1';

    IF execution_id IS NULL THEN
        IF EXISTS (
            SELECT 1
            FROM tide.task_execution_versions AS execution
            JOIN public.task_templates AS template
              ON template.row_id = execution.shared_template_row_id
            WHERE template.payload->>'category' = 'MANDATORY_GROWTH'
        ) THEN
            RAISE EXCEPTION
                'existing execution catalog is missing the stable G09 execution G10:v1';
        END IF;
        RAISE NOTICE
            'teacher 0042 left the empty G09 execution unchanged; run the explicit current catalog seed before rollout';
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE task_code = 'G09'
          AND shared_template_row_id <> 'G10:v1'
    ) OR EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE id = execution_id
          AND (
              task_code <> 'G09'
              OR status <> 'ACTIVE'
              OR execution_contract_version NOT IN ('v1', 'task-contract-v3')
              OR config NOT IN (
                  '{"estimatedMinutes":25,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-06","pendingReason":"KUOZHI_G09_COURSE_MAPPING_PENDING"}'::jsonb,
                  '{"estimatedMinutes":25,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-19-set-kuozhi-v1","pendingReason":null}'::jsonb
              )
          )
    ) THEN
        RAISE EXCEPTION
            'G09 execution identity or config is not an approved pre-0042/current shape';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_step_definitions
        WHERE execution_version_id = execution_id
    ) OR EXISTS (
        SELECT 1
        FROM tide.task_validation_rules
        WHERE execution_version_id = execution_id
    ) THEN
        RAISE EXCEPTION
            'G09 contains unreviewed local steps or rules; teacher 0042 will not replace them';
    END IF;
END
$teacher42_preflight$;

UPDATE tide.task_execution_versions
SET execution_contract_version = 'task-contract-v3',
    config = '{"estimatedMinutes":25,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-19-set-kuozhi-v1","pendingReason":null}'::jsonb,
    updated_at = now()
WHERE shared_template_row_id = 'G10:v1'
  AND config = '{"estimatedMinutes":25,"allowRetry":true,"contentStatus":"PENDING","contentVersion":"2026-08-06","pendingReason":"KUOZHI_G09_COURSE_MAPPING_PENDING"}'::jsonb;

DO $teacher42_postflight$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE shared_template_row_id = 'G10:v1'
          AND (
              task_code <> 'G09'
              OR status <> 'ACTIVE'
              OR execution_contract_version <> 'task-contract-v3'
              OR config <> '{"estimatedMinutes":25,"allowRetry":true,"contentStatus":"READY","contentVersion":"2026-08-19-set-kuozhi-v1","pendingReason":null}'::jsonb
          )
    ) THEN
        RAISE EXCEPTION 'G09 SET course execution publication verification failed';
    END IF;
END
$teacher42_postflight$;

INSERT INTO tide.schema_migrations (
    migration_id,
    migration_order,
    filename,
    sha256
)
VALUES (
    '0042_g09_set_kuozhi_course',
    37,
    '0042_g09_set_kuozhi_course.up.sql',
    '59c6f757ec6ca9ee60ad4e51f594bc62c678112139e8d1f70ab6905b1a8b663a'
);

DO $postflight$
BEGIN
    IF (
        SELECT count(*) = 1
           AND min(version_num) = '20260819_65_g09_set_course'
        FROM public.alembic_version
    ) IS NOT TRUE THEN
        RAISE EXCEPTION 'MR60 public migration ledger postflight failed';
    END IF;

    IF (
        SELECT count(*) = 37
           AND count(*) FILTER (
               WHERE migration_order = 37
                 AND migration_id = '0042_g09_set_kuozhi_course'
                 AND filename = '0042_g09_set_kuozhi_course.up.sql'
                 AND sha256 = '59c6f757ec6ca9ee60ad4e51f594bc62c678112139e8d1f70ab6905b1a8b663a'
           ) = 1
        FROM tide.schema_migrations
    ) IS NOT TRUE THEN
        RAISE EXCEPTION 'MR60 teacher migration ledger postflight failed';
    END IF;

    IF (
        SELECT count(*)
        FROM public.task_templates
        WHERE (
            row_id = 'G06:v1'
            AND template_id = 'G05'
            AND payload->>'ops_name_zh' = 'TTP 入门'
            AND payload->>'title' = 'TTP Orientation'
            AND payload->>'why_template' =
                'Understand TTP and its key business scenarios.'
            AND payload->>'how_summary' =
                'Complete the TTP video and Quiz in Kuozhi.'
            AND payload->>'completion_standard' =
                'The TTP video reaches 100% progress and the Quiz is completed in Kuozhi.'
            AND payload->>'benefit' =
                'You understand the key TTP workflow and commitments.'
        ) OR (
            row_id = 'G09:v1'
            AND template_id = 'G08'
            AND payload->>'ops_name_zh' = 'Global Communicator 培训'
            AND payload->>'title' = 'Global Communicator Training'
            AND payload->>'why_template' =
                'Learn the core Global Communicator teaching flow.'
            AND payload->>'how_summary' =
                'Complete all six Global Communicator Sample Lessons videos in Kuozhi.'
            AND payload->>'completion_standard' =
                'All six required videos reach 100% progress in Kuozhi.'
            AND payload->>'benefit' =
                'You can now confidently prepare for a Global Communicator lesson.'
        ) OR (
            row_id = 'G10:v1'
            AND template_id = 'G09'
            AND payload->>'ops_name_zh' = 'SET 教学基础'
            AND payload->>'title' = 'SET Teaching Fundamentals'
            AND payload->>'why_template' =
                'Learn the fundamentals of SET teaching.'
            AND payload->>'how_summary' =
                'Complete the three SET videos and their three paired quizzes in Kuozhi.'
            AND payload->>'completion_standard' =
                'All three required videos and all three paired quizzes reach 100% progress in Kuozhi.'
            AND payload->>'benefit' =
                'You understand the SET teaching foundation.'
        )
    ) <> 3 THEN
        RAISE EXCEPTION 'MR60 code-canonical task copy postflight failed';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.task_assignments
        WHERE task_code = 'G08'
          AND task_kind = 'FIXED_GROWTH'
          AND (
              why = 'Learn the core Cocos teaching flow.'
              OR display_title = 'Cocos Course Training'
          )
    ) THEN
        RAISE EXCEPTION 'MR60 left retired Cocos assignment copy behind';
    END IF;
END
$postflight$;

COMMIT;

SELECT version_num AS public_head
FROM public.alembic_version;

SELECT migration_order, migration_id, filename, sha256
FROM tide.schema_migrations
ORDER BY migration_order DESC
LIMIT 1;

SELECT
    row_id,
    template_id,
    revision,
    payload->>'ops_name_zh' AS ops_name_zh,
    payload->>'title' AS title,
    payload->>'why_template' AS why_template,
    payload->>'how_summary' AS how_summary,
    payload->>'completion_standard' AS completion_standard,
    payload->>'benefit' AS benefit
FROM public.task_templates
WHERE row_id IN ('G06:v1', 'G09:v1', 'G10:v1')
ORDER BY row_id;
