-- Local-only compatibility upgrade for the shared score-entry contract.
-- The real shared database owns public.score_entries; never run this fixture
-- against company or production databases.
BEGIN;

ALTER TABLE public.score_entries
    ADD COLUMN IF NOT EXISTS camp_enrollment_id varchar,
    ADD COLUMN IF NOT EXISTS lesson_id varchar,
    ADD COLUMN IF NOT EXISTS dimension varchar,
    ADD COLUMN IF NOT EXISTS reason_code varchar,
    ADD COLUMN IF NOT EXISTS evidence_status varchar,
    ADD COLUMN IF NOT EXISTS score_rule_version varchar,
    ADD COLUMN IF NOT EXISTS occurred_at timestamptz,
    ADD COLUMN IF NOT EXISTS reversal_of_score_entry_id varchar;

UPDATE public.score_entries entry
SET
    camp_enrollment_id = COALESCE(
        entry.camp_enrollment_id,
        teacher.camp_enrollment_id
    ),
    dimension = COALESCE(
        entry.dimension,
        NULLIF(entry.payload ->> 'dimension', ''),
        CASE
            WHEN entry.task_assignment_id IS NOT NULL
                THEN 'NEW_TEACHER_TASK'
            ELSE 'UNCLASSIFIED'
        END
    ),
    reason_code = COALESCE(
        entry.reason_code,
        NULLIF(entry.payload ->> 'reason_code', ''),
        CASE
            WHEN entry.task_assignment_id IS NOT NULL
                THEN 'FIXED_GROWTH_COMPLETED:' || COALESCE(
                    (
                        SELECT assignment.task_code
                        FROM public.task_assignments assignment
                        WHERE assignment.assignment_id =
                            entry.task_assignment_id
                    ),
                    'UNKNOWN'
                )
            ELSE entry.entry_type
        END
    ),
    evidence_status = COALESCE(
        entry.evidence_status,
        NULLIF(entry.payload ->> 'evidence_status', ''),
        'CONFIRMED'
    ),
    score_rule_version = COALESCE(
        entry.score_rule_version,
        NULLIF(entry.payload ->> 'score_rule_version', ''),
        'LOCAL_SHARED_CONTRACT_V1'
    ),
    occurred_at = COALESCE(entry.occurred_at, entry.recorded_at)
FROM public.teachers teacher
WHERE teacher.teacher_id = entry.teacher_id
  AND (
      entry.camp_enrollment_id IS NULL
      OR entry.dimension IS NULL
      OR entry.reason_code IS NULL
      OR entry.evidence_status IS NULL
      OR entry.score_rule_version IS NULL
      OR entry.occurred_at IS NULL
  );

ALTER TABLE public.score_entries
    ALTER COLUMN camp_enrollment_id SET NOT NULL,
    ALTER COLUMN dimension SET NOT NULL,
    ALTER COLUMN reason_code SET NOT NULL,
    ALTER COLUMN evidence_status SET NOT NULL,
    ALTER COLUMN score_rule_version SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public.score_entries'::regclass
          AND conname = 'score_entries_lesson_id_fkey'
    ) THEN
        ALTER TABLE public.score_entries
            ADD CONSTRAINT score_entries_lesson_id_fkey
            FOREIGN KEY (lesson_id)
            REFERENCES public.lesson_facts(lesson_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public.score_entries'::regclass
          AND conname = 'score_entries_reversal_of_score_entry_id_fkey'
    ) THEN
        ALTER TABLE public.score_entries
            ADD CONSTRAINT score_entries_reversal_of_score_entry_id_fkey
            FOREIGN KEY (reversal_of_score_entry_id)
            REFERENCES public.score_entries(score_entry_id);
    END IF;
END
$$;

COMMIT;
