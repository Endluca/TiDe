BEGIN;

SET LOCAL lock_timeout = '10s';

-- All file objects are private and every current read path authorizes by the
-- owning account / task relation.  Refuse the cleanup if that invariant or
-- the exact legacy shape has drifted.
LOCK TABLE tide.file_objects IN ACCESS EXCLUSIVE MODE;

DO $unused_file_visibility_guard$
DECLARE
    file_objects_oid oid := to_regclass('tide.file_objects');
    visibility_attnum smallint;
    visibility_is_not_null boolean;
    visibility_constraint_oid oid;
    visibility_not_null_constraint_oid oid;
    visibility_default_oid oid;
    unexpected_dependencies text;
BEGIN
    IF file_objects_oid IS NULL THEN
        RAISE EXCEPTION
            'migration 0029 requires tide.file_objects';
    END IF;

    SELECT attribute.attnum, attribute.attnotnull
    INTO visibility_attnum, visibility_is_not_null
    FROM pg_attribute AS attribute
    WHERE attribute.attrelid = file_objects_oid
      AND attribute.attname = 'visibility'
      AND attribute.attnum > 0
      AND NOT attribute.attisdropped;

    IF visibility_attnum IS NULL THEN
        RAISE EXCEPTION
            'migration 0029 requires tide.file_objects.visibility';
    END IF;

    IF visibility_is_not_null IS DISTINCT FROM true THEN
        RAISE EXCEPTION
            'migration 0029 requires tide.file_objects.visibility to be NOT NULL';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.file_objects
        WHERE visibility IS DISTINCT FROM 'PRIVATE'
    ) THEN
        RAISE EXCEPTION
            'refusing to drop tide.file_objects.visibility with non-PRIVATE rows';
    END IF;

    SELECT constraint_row.oid
    INTO visibility_constraint_oid
    FROM pg_constraint AS constraint_row
    WHERE constraint_row.conrelid = file_objects_oid
      AND constraint_row.conname = 'file_objects_visibility_check'
      AND constraint_row.contype = 'c'
      AND constraint_row.conkey = ARRAY[visibility_attnum]::smallint[]
      AND constraint_row.convalidated;

    IF visibility_constraint_oid IS NULL THEN
        RAISE EXCEPTION
            'migration 0029 requires the validated file_objects_visibility_check constraint';
    END IF;

    -- PostgreSQL 18 represents NOT NULL as a pg_constraint dependency;
    -- earlier supported versions only expose pg_attribute.attnotnull.  Accept
    -- the exact generated constraint when present, while retaining the same
    -- fail-closed dependency check on every server version.
    SELECT constraint_row.oid
    INTO visibility_not_null_constraint_oid
    FROM pg_constraint AS constraint_row
    WHERE constraint_row.conrelid = file_objects_oid
      AND constraint_row.conname = 'file_objects_visibility_not_null'
      AND constraint_row.contype = 'n'
      AND constraint_row.conkey = ARRAY[visibility_attnum]::smallint[]
      AND constraint_row.convalidated;

    SELECT attribute_default.oid
    INTO visibility_default_oid
    FROM pg_attrdef AS attribute_default
    WHERE attribute_default.adrelid = file_objects_oid
      AND attribute_default.adnum = visibility_attnum;

    SELECT string_agg(
        pg_describe_object(
            dependency.classid,
            dependency.objid,
            dependency.objsubid
        ),
        ', '
        ORDER BY pg_describe_object(
            dependency.classid,
            dependency.objid,
            dependency.objsubid
        )
    )
    INTO unexpected_dependencies
    FROM pg_depend AS dependency
    WHERE dependency.refclassid = 'pg_class'::regclass
      AND dependency.refobjid = file_objects_oid
      AND dependency.refobjsubid = visibility_attnum
      AND NOT (
          dependency.classid = 'pg_constraint'::regclass
          AND dependency.objid = visibility_constraint_oid
      )
      AND NOT (
          visibility_not_null_constraint_oid IS NOT NULL
          AND dependency.classid = 'pg_constraint'::regclass
          AND dependency.objid = visibility_not_null_constraint_oid
      )
      AND NOT (
          visibility_default_oid IS NOT NULL
          AND dependency.classid = 'pg_attrdef'::regclass
          AND dependency.objid = visibility_default_oid
      );

    IF unexpected_dependencies IS NOT NULL THEN
        RAISE EXCEPTION
            'refusing to drop tide.file_objects.visibility with external dependencies: %',
            unexpected_dependencies;
    END IF;
END
$unused_file_visibility_guard$;

DO $orphan_function_guard$
DECLARE
    function_oid oid := to_regprocedure('tide.enforce_outbox_target()');
    dependent_objects text;
BEGIN
    IF function_oid IS NULL THEN
        RAISE EXCEPTION
            'migration 0029 requires tide.enforce_outbox_target()';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_proc AS function_row
        WHERE function_row.oid = function_oid
          AND function_row.prorettype = 'trigger'::regtype
          AND function_row.pronargs = 0
    ) THEN
        RAISE EXCEPTION
            'migration 0029 found an unexpected enforce_outbox_target signature';
    END IF;

    SELECT string_agg(
        pg_describe_object(
            dependency.classid,
            dependency.objid,
            dependency.objsubid
        ),
        ', '
        ORDER BY pg_describe_object(
            dependency.classid,
            dependency.objid,
            dependency.objsubid
        )
    )
    INTO dependent_objects
    FROM pg_depend AS dependency
    WHERE dependency.refclassid = 'pg_proc'::regclass
      AND dependency.refobjid = function_oid;

    IF dependent_objects IS NOT NULL THEN
        RAISE EXCEPTION
            'refusing to drop tide.enforce_outbox_target() with dependent objects: %',
            dependent_objects;
    END IF;
END
$orphan_function_guard$;

ALTER TABLE tide.file_objects
    DROP CONSTRAINT file_objects_visibility_check,
    DROP COLUMN visibility;

DROP FUNCTION tide.enforce_outbox_target();

COMMIT;
