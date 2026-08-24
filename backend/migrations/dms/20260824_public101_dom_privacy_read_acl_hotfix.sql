-- Hotfix for an already-applied public rev101 database.
-- DOM ingest validates domestic-student privacy state at startup and therefore
-- needs read-only access to current lesson and dirty-key facts.
DO $$
DECLARE
    public_head text;
BEGIN
    IF current_setting('transaction_read_only')::boolean THEN
        RAISE EXCEPTION 'DOM privacy ACL hotfix requires a writable transaction';
    END IF;

    IF to_regclass('public.alembic_version') IS NULL
       OR to_regclass('public.dts_dirty_keys') IS NULL
       OR to_regclass('public.lesson_source_wide') IS NULL
       OR to_regrole('tit_dts_ingest_runtime') IS NULL THEN
        RAISE EXCEPTION 'DOM privacy ACL hotfix prerequisites are missing';
    END IF;

    SELECT CASE WHEN count(*) = 1 THEN min(version_num) END
    INTO public_head
    FROM public.alembic_version;

    IF public_head IS DISTINCT FROM
         '20260824_101_dts_single_pipeline_reset' THEN
        RAISE EXCEPTION
            'DOM privacy ACL hotfix requires public head 101; current=%',
            coalesce(public_head, '<invalid>');
    END IF;

    EXECUTE 'GRANT SELECT ON TABLE '
        'public.dts_dirty_keys,public.lesson_source_wide '
        'TO tit_dts_ingest_runtime';

    IF NOT has_table_privilege(
             'tit_dts_ingest_runtime','public.dts_dirty_keys','SELECT'
           )
       OR NOT has_table_privilege(
             'tit_dts_ingest_runtime','public.lesson_source_wide','SELECT'
           )
       OR EXISTS (
            SELECT 1
            FROM unnest(ARRAY[
              'public.dts_dirty_keys',
              'public.dts_dirty_key_inputs',
              'public.dts_dirty_key_dependencies',
              'public.dts_dirty_key_state_audits'
            ]::text[]) AS relation(name)
            CROSS JOIN unnest(ARRAY[
              'INSERT','UPDATE','DELETE','TRUNCATE','TRIGGER'
            ]::text[]) AS privilege(name)
            WHERE has_table_privilege(
              'tit_dts_ingest_runtime',relation.name,privilege.name
            )
          ) THEN
        RAISE EXCEPTION 'DOM privacy ACL hotfix verification failed';
    END IF;
END
$$;
