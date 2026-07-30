BEGIN;

DROP TABLE IF EXISTS tide.job_leases;

DROP INDEX IF EXISTS tide.teacher_photo_runs_pending_claim_idx;

ALTER TABLE tide.teacher_photo_runs
    DROP CONSTRAINT IF EXISTS teacher_photo_runs_lease_pair_check,
    DROP CONSTRAINT IF EXISTS teacher_photo_runs_attempt_count_check,
    DROP COLUMN IF EXISTS next_attempt_at,
    DROP COLUMN IF EXISTS attempt_count,
    DROP COLUMN IF EXISTS lease_expires_at,
    DROP COLUMN IF EXISTS processing_owner;

COMMIT;
