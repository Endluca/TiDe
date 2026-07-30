BEGIN;

UPDATE tide.teacher_photo_runs
SET filter_strength = CASE
        WHEN filter_strength = 1.000 THEN 0.750
        ELSE filter_strength
    END,
    filter_preset = CASE
        WHEN filter_preset IN (
            'cream_bright_04_adaptive_v2',
            'cream_bright_04_adaptive_v3'
        )
            THEN 'cream_bright_04_adaptive_v1'
        ELSE filter_preset
    END
WHERE filter_preset IN (
        'cream_bright_04_adaptive_v2',
        'cream_bright_04_adaptive_v3'
    )
   OR filter_strength = 1.000;

ALTER TABLE tide.teacher_photo_runs
    DROP CONSTRAINT teacher_photo_runs_strength_check;

ALTER TABLE tide.teacher_photo_runs
    ADD CONSTRAINT teacher_photo_runs_strength_check
    CHECK (filter_strength IS NULL OR filter_strength IN (0.600, 0.750));

COMMIT;
