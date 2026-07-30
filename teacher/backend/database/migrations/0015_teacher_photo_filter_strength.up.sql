BEGIN;

ALTER TABLE tide.teacher_photo_runs
    DROP CONSTRAINT teacher_photo_runs_strength_check;

ALTER TABLE tide.teacher_photo_runs
    ADD CONSTRAINT teacher_photo_runs_strength_check
    CHECK (filter_strength IS NULL OR filter_strength IN (0.600, 0.750, 1.000));

COMMIT;
