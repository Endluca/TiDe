BEGIN;

ALTER TABLE tide.teacher_photo_runs
    ADD COLUMN processing_owner text,
    ADD COLUMN lease_expires_at timestamptz,
    ADD COLUMN attempt_count integer NOT NULL DEFAULT 0,
    ADD COLUMN next_attempt_at timestamptz NOT NULL DEFAULT now(),
    ADD CONSTRAINT teacher_photo_runs_attempt_count_check
        CHECK (attempt_count >= 0),
    ADD CONSTRAINT teacher_photo_runs_lease_pair_check
        CHECK (
            (processing_owner IS NULL AND lease_expires_at IS NULL)
            OR (processing_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
        );

CREATE INDEX teacher_photo_runs_pending_claim_idx
    ON tide.teacher_photo_runs (
        next_attempt_at,
        submitted_at,
        id
    )
    WHERE status IN ('CHECKING', 'BEAUTIFYING');

CREATE TABLE tide.job_leases (
    job_key text PRIMARY KEY,
    owner_id text NOT NULL,
    lease_until timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT job_leases_key_check CHECK (length(job_key) BETWEEN 1 AND 128),
    CONSTRAINT job_leases_owner_check CHECK (length(owner_id) BETWEEN 1 AND 255)
);

CREATE INDEX job_leases_expiry_idx
    ON tide.job_leases (lease_until);

COMMIT;
