BEGIN;

CREATE TABLE tide.growth_stage_notification_states (
    teacher_id varchar PRIMARY KEY
        REFERENCES public.teachers(teacher_id) ON DELETE RESTRICT,
    highest_available_stage smallint NOT NULL,
    initialized_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT growth_stage_notification_states_stage_check
        CHECK (highest_available_stage BETWEEN 1 AND 3)
);

COMMENT ON TABLE tide.growth_stage_notification_states IS
'TIDE 对教师最高已开放成长阶段的观察状态；首次观察只建立基线，后续阶段提升才生成站内通知。';

COMMIT;
