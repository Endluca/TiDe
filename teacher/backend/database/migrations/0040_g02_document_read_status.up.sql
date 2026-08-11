BEGIN;

SET LOCAL lock_timeout = '10s';

DO $$
BEGIN
    IF to_regclass('tide.task_step_progress') IS NULL THEN
        RAISE EXCEPTION
            'tide.task_step_progress is required before migration 0040';
    END IF;
END
$$;

ALTER TABLE tide.task_step_progress
    ADD COLUMN IF NOT EXISTS reached_end boolean NOT NULL DEFAULT false;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'tide'
          AND table_name = 'task_step_progress'
          AND column_name = 'reached_end'
          AND data_type = 'boolean'
    ) THEN
        RAISE EXCEPTION
            'tide.task_step_progress.reached_end must be boolean';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tide.task_step_progress
        WHERE step_key = 'g02-policy-document'
          AND (
              COALESCE(
                  progress_summary->>'contentVersion' =
                      '2026-07-24-overseas-nt-policies-v1',
                  false
              )
              AND COALESCE(
                  progress_summary->>'contentHash' =
                      '6875233667c6f3d90602a07c84849dbb88f41685929779a7b0dc79ccf859979c',
                  false
              )
              AND COALESCE(
                  progress_summary->'readPercent' = to_jsonb(percent::integer),
                  false
              )
              AND (
                  (
                      progress_summary->'reachedEnd' = 'true'::jsonb
                      AND status = 'COMPLETED'
                      AND percent = 100
                      AND completed_at IS NOT NULL
                      AND EXISTS (
                          SELECT 1
                          FROM public.task_assignments assignment
                          WHERE assignment.assignment_id =
                              task_step_progress.task_assignment_id
                            AND assignment.status = 'COMPLETED'
                      )
                  )
                  OR (
                      progress_summary->'reachedEnd' = 'false'::jsonb
                      AND status IN ('NOT_STARTED', 'IN_PROGRESS')
                      AND percent BETWEEN 0 AND 99
                      AND completed_at IS NULL
                  )
              )
          ) IS NOT TRUE
    ) THEN
        RAISE EXCEPTION
            'G02 document progress is inconsistent before migration 0040';
    END IF;
END
$$;

UPDATE tide.task_step_progress
SET reached_end = (progress_summary->'reachedEnd' = 'true'::jsonb)
WHERE step_key = 'g02-policy-document'
  AND reached_end IS DISTINCT FROM
      (progress_summary->'reachedEnd' = 'true'::jsonb);

UPDATE tide.task_step_progress
SET reached_end = false
WHERE step_key <> 'g02-policy-document'
  AND reached_end IS DISTINCT FROM false;

ALTER TABLE tide.task_step_progress
    ALTER COLUMN reached_end SET DEFAULT false,
    ALTER COLUMN reached_end SET NOT NULL;

ALTER TABLE tide.task_step_progress
    DROP CONSTRAINT IF EXISTS task_step_progress_g02_read_status_check;

ALTER TABLE tide.task_step_progress
    ADD CONSTRAINT task_step_progress_g02_read_status_check
    CHECK (
        (
            step_key = 'g02-policy-document'
            AND COALESCE(
                progress_summary->>'contentVersion' =
                    '2026-07-24-overseas-nt-policies-v1',
                false
            )
            AND COALESCE(
                progress_summary->>'contentHash' =
                    '6875233667c6f3d90602a07c84849dbb88f41685929779a7b0dc79ccf859979c',
                false
            )
            AND COALESCE(
                progress_summary->'readPercent' = to_jsonb(percent::integer),
                false
            )
            AND COALESCE(
                progress_summary->'reachedEnd' = to_jsonb(reached_end),
                false
            )
            AND (
                (
                    reached_end
                    AND status = 'COMPLETED'
                    AND percent = 100
                    AND completed_at IS NOT NULL
                )
                OR (
                    NOT reached_end
                    AND status IN ('NOT_STARTED', 'IN_PROGRESS')
                    AND percent BETWEEN 0 AND 99
                    AND completed_at IS NULL
                )
            )
        )
        OR (
            step_key <> 'g02-policy-document'
            AND NOT reached_end
        )
    );

-- A row-level CHECK cannot reference the shared assignment table. Keep the
-- cross-schema completion invariant as a deferred constraint trigger so the
-- progress row and assignment can still be completed in either order inside
-- the same transaction, while an inconsistent commit is rejected.
CREATE OR REPLACE FUNCTION tide.enforce_g02_document_assignment_completion()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, tide
AS $$
BEGIN
    IF NEW.step_key = 'g02-policy-document'
       AND NEW.reached_end
       AND NOT EXISTS (
           SELECT 1
           FROM public.task_assignments AS assignment
           WHERE assignment.assignment_id = NEW.task_assignment_id
             AND assignment.template_version_id = 'G03:v1'
             AND assignment.status = 'COMPLETED'
       ) THEN
        RAISE EXCEPTION
            'completed G02 document progress requires a completed shared assignment'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS task_step_progress_g02_assignment_completion_check
    ON tide.task_step_progress;

CREATE CONSTRAINT TRIGGER task_step_progress_g02_assignment_completion_check
AFTER INSERT OR UPDATE ON tide.task_step_progress
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION tide.enforce_g02_document_assignment_completion();

COMMIT;
