BEGIN;

SET LOCAL lock_timeout = '10s';

-- These objects have no current business consumer.  Keep the guard explicit:
-- a partially removed schema, newly populated table, or unregistered dependency
-- must stop the migration instead of being hidden behind CASCADE.
DO $unused_tide_object_presence_guard$
DECLARE
    present_table_count integer;
    present_view_count integer;
BEGIN
    SELECT count(*)
    INTO present_table_count
    FROM unnest(ARRAY[
        'tide.outcome_projections',
        'tide.camp_enrollment_projections',
        'tide.audit_events',
        'tide.task_template_files',
        'tide.file_migrations',
        'tide.teacher_photo_runs'
    ]::text[]) AS expected(relation_name)
    WHERE to_regclass(expected.relation_name) IS NOT NULL;

    SELECT count(*)
    INTO present_view_count
    FROM unnest(ARRAY[
        'tide.analytics_actor_task_journey_v1',
        'tide.analytics_task_assignment_funnel_v1',
        'tide.analytics_task_funnel_v1',
        'tide.analytics_task_step_funnel_v1',
        'tide.analytics_content_quality_v1'
    ]::text[]) AS expected(relation_name)
    WHERE to_regclass(expected.relation_name) IS NOT NULL;

    IF present_table_count NOT IN (0, 6) THEN
        RAISE EXCEPTION
            'migration 0029 found a partially removed table set (% of 6 present)',
            present_table_count;
    END IF;
    IF present_view_count NOT IN (0, 5) THEN
        RAISE EXCEPTION
            'migration 0029 found a partially removed view set (% of 5 present)',
            present_view_count;
    END IF;
    IF (present_table_count = 0) <> (present_view_count = 0) THEN
        RAISE EXCEPTION
            'migration 0029 found inconsistent table/view cleanup state';
    END IF;

    IF present_table_count = 6 THEN
        EXECUTE 'LOCK TABLE '
            || 'tide.outcome_projections, '
            || 'tide.camp_enrollment_projections, '
            || 'tide.audit_events, '
            || 'tide.task_template_files, '
            || 'tide.file_migrations, '
            || 'tide.teacher_photo_runs '
            || 'IN ACCESS EXCLUSIVE MODE';
    END IF;
END
$unused_tide_object_presence_guard$;

DO $unused_tide_object_dependency_guard$
DECLARE
    target_tables oid[];
    target_views oid[];
    populated_tables text;
    dependent_views text;
    dependent_foreign_keys text;
    dependent_functions text;
    user_triggers text;
    inherited_relations text;
    publication_memberships text;
BEGIN
    IF to_regclass('tide.outcome_projections') IS NULL THEN
        RETURN;
    END IF;

    target_tables := ARRAY[
        'tide.outcome_projections'::regclass,
        'tide.camp_enrollment_projections'::regclass,
        'tide.audit_events'::regclass,
        'tide.task_template_files'::regclass,
        'tide.file_migrations'::regclass,
        'tide.teacher_photo_runs'::regclass
    ]::oid[];
    target_views := ARRAY[
        'tide.analytics_actor_task_journey_v1'::regclass,
        'tide.analytics_task_assignment_funnel_v1'::regclass,
        'tide.analytics_task_funnel_v1'::regclass,
        'tide.analytics_task_step_funnel_v1'::regclass,
        'tide.analytics_content_quality_v1'::regclass
    ]::oid[];

    SELECT string_agg(table_name, ', ' ORDER BY table_name)
    INTO populated_tables
    FROM (
        SELECT 'outcome_projections' AS table_name
        WHERE EXISTS (SELECT 1 FROM tide.outcome_projections LIMIT 1)
        UNION ALL
        SELECT 'camp_enrollment_projections'
        WHERE EXISTS (
            SELECT 1 FROM tide.camp_enrollment_projections LIMIT 1
        )
        UNION ALL
        SELECT 'audit_events'
        WHERE EXISTS (SELECT 1 FROM tide.audit_events LIMIT 1)
        UNION ALL
        SELECT 'task_template_files'
        WHERE EXISTS (SELECT 1 FROM tide.task_template_files LIMIT 1)
        UNION ALL
        SELECT 'file_migrations'
        WHERE EXISTS (SELECT 1 FROM tide.file_migrations LIMIT 1)
        UNION ALL
        SELECT 'teacher_photo_runs'
        WHERE EXISTS (SELECT 1 FROM tide.teacher_photo_runs LIMIT 1)
    ) AS populated;

    IF populated_tables IS NOT NULL THEN
        RAISE EXCEPTION
            'refusing to drop populated unused tide tables: %',
            populated_tables;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.task_templates
        WHERE template_id = 'G00'
          AND status <> 'RETIRED'
    ) OR EXISTS (
        SELECT 1
        FROM tide.task_execution_versions
        WHERE task_code = 'G00'
          AND status <> 'RETIRED'
    ) THEN
        RAISE EXCEPTION
            'refusing to retire teacher_photo_runs while a non-retired G00 template or execution route remains';
    END IF;

    SELECT string_agg(
        DISTINCT format('%I.%I', dependent_ns.nspname, dependent.relname),
        ', '
        ORDER BY format('%I.%I', dependent_ns.nspname, dependent.relname)
    )
    INTO dependent_views
    FROM pg_depend AS dependency
    JOIN pg_rewrite AS rewrite ON rewrite.oid = dependency.objid
    JOIN pg_class AS dependent ON dependent.oid = rewrite.ev_class
    JOIN pg_namespace AS dependent_ns
      ON dependent_ns.oid = dependent.relnamespace
    WHERE dependency.refobjid = ANY (target_tables || target_views)
      AND NOT dependent.oid = ANY (target_views);

    IF dependent_views IS NOT NULL THEN
        RAISE EXCEPTION
            'refusing to drop unused tide objects with dependent views: %',
            dependent_views;
    END IF;

    SELECT string_agg(
        format(
            '%I.%I (%I)',
            dependent_ns.nspname,
            dependent.relname,
            constraint_row.conname
        ),
        ', '
        ORDER BY dependent_ns.nspname,
                 dependent.relname,
                 constraint_row.conname
    )
    INTO dependent_foreign_keys
    FROM pg_constraint AS constraint_row
    JOIN pg_class AS dependent ON dependent.oid = constraint_row.conrelid
    JOIN pg_namespace AS dependent_ns
      ON dependent_ns.oid = dependent.relnamespace
    WHERE constraint_row.contype = 'f'
      AND constraint_row.confrelid = ANY (target_tables)
      AND NOT constraint_row.conrelid = ANY (target_tables);

    IF dependent_foreign_keys IS NOT NULL THEN
        RAISE EXCEPTION
            'refusing to drop unused tide tables with external foreign keys: %',
            dependent_foreign_keys;
    END IF;

    SELECT string_agg(
        format('%I.%I', function_ns.nspname, function_row.proname),
        ', '
        ORDER BY function_ns.nspname, function_row.proname
    )
    INTO dependent_functions
    FROM pg_depend AS dependency
    JOIN pg_proc AS function_row ON function_row.oid = dependency.objid
    JOIN pg_namespace AS function_ns
      ON function_ns.oid = function_row.pronamespace
    WHERE dependency.classid = 'pg_proc'::regclass
      AND dependency.refobjid = ANY (target_tables || target_views);

    IF dependent_functions IS NOT NULL THEN
        RAISE EXCEPTION
            'refusing to drop unused tide objects with dependent functions: %',
            dependent_functions;
    END IF;

    SELECT string_agg(
        format('%I.%I', trigger_ns.nspname, trigger_row.tgname),
        ', '
        ORDER BY trigger_ns.nspname, trigger_row.tgname
    )
    INTO user_triggers
    FROM pg_trigger AS trigger_row
    JOIN pg_class AS trigger_table ON trigger_table.oid = trigger_row.tgrelid
    JOIN pg_namespace AS trigger_ns
      ON trigger_ns.oid = trigger_table.relnamespace
    WHERE trigger_row.tgrelid = ANY (target_tables)
      AND NOT trigger_row.tgisinternal;

    IF user_triggers IS NOT NULL THEN
        RAISE EXCEPTION
            'refusing to drop unused tide tables with user triggers: %',
            user_triggers;
    END IF;

    SELECT string_agg(
        DISTINCT format('%I.%I', relation_ns.nspname, relation.relname),
        ', '
        ORDER BY format('%I.%I', relation_ns.nspname, relation.relname)
    )
    INTO inherited_relations
    FROM pg_inherits AS inheritance
    JOIN pg_class AS relation
      ON relation.oid = CASE
          WHEN inheritance.inhparent = ANY (target_tables)
          THEN inheritance.inhrelid
          ELSE inheritance.inhparent
      END
    JOIN pg_namespace AS relation_ns ON relation_ns.oid = relation.relnamespace
    WHERE inheritance.inhparent = ANY (target_tables)
       OR inheritance.inhrelid = ANY (target_tables);

    IF inherited_relations IS NOT NULL THEN
        RAISE EXCEPTION
            'refusing to drop inherited or partitioned unused tide tables: %',
            inherited_relations;
    END IF;

    SELECT string_agg(
        DISTINCT publication.pubname,
        ', '
        ORDER BY publication.pubname
    )
    INTO publication_memberships
    FROM pg_publication_rel AS membership
    JOIN pg_publication AS publication
      ON publication.oid = membership.prpubid
    WHERE membership.prrelid = ANY (target_tables);

    IF publication_memberships IS NOT NULL THEN
        RAISE EXCEPTION
            'refusing to drop unused tide tables still published by: %',
            publication_memberships;
    END IF;
END
$unused_tide_object_dependency_guard$;

-- The first view depends on analytics_task_assignment_funnel_v1.
DROP VIEW IF EXISTS tide.analytics_task_funnel_v1;
DROP VIEW IF EXISTS tide.analytics_task_assignment_funnel_v1;
DROP VIEW IF EXISTS tide.analytics_actor_task_journey_v1;
DROP VIEW IF EXISTS tide.analytics_task_step_funnel_v1;
DROP VIEW IF EXISTS tide.analytics_content_quality_v1;

DROP TABLE IF EXISTS tide.teacher_photo_runs;
DROP TABLE IF EXISTS tide.task_template_files;
DROP TABLE IF EXISTS tide.file_migrations;
DROP TABLE IF EXISTS tide.outcome_projections;
DROP TABLE IF EXISTS tide.camp_enrollment_projections;
DROP TABLE IF EXISTS tide.audit_events;

COMMIT;
