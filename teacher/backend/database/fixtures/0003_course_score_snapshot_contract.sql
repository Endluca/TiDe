-- Local-only compatibility for the Shiwen fields used by course-score tests.
-- The company database already owns these columns; this file must not be
-- applied to company or production databases.
BEGIN;

-- Local source fixture only: there is no unconditional 40-point starting score.
ALTER TABLE public.teachers
    ALTER COLUMN total_score SET DEFAULT 0;

ALTER TABLE public.teacher_metric_snapshots
    ADD COLUMN IF NOT EXISTS lessons_completed integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS public_total_score double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS user_feedback_score double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS reliability_score double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS class_quality_score double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS metric_inputs jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS score_rule_version varchar NOT NULL DEFAULT 'LOCAL_FIXTURE_V1',
    ADD COLUMN IF NOT EXISTS score_policy_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE public.lesson_facts
    ADD COLUMN IF NOT EXISTS is_peak boolean;

COMMIT;
