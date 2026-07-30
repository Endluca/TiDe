BEGIN;

ALTER TABLE tide.integration_events
    ADD CONSTRAINT integration_events_personalized_status_check CHECK (
        event_type <> 'PERSONALIZED_STATUS'
        OR status IN ('VIEWED', 'IN_PROGRESS', 'SUBMITTED', 'UNDER_REVIEW', 'COMPLETED', 'FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED')
    ),
    ADD CONSTRAINT integration_events_reason_code_check CHECK (
        event_type <> 'PERSONALIZED_STATUS'
        OR (
            (status IN ('FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED') AND reason_code IS NOT NULL)
            OR
            (status NOT IN ('FAILED', 'EXPIRED', 'WAIVED', 'CANCELLED') AND reason_code IS NULL)
        )
    ),
    ADD CONSTRAINT integration_events_result_code_check CHECK (
        event_type <> 'PERSONALIZED_STATUS'
        OR (
            (task_code = 'P-REL-03' AND status = 'COMPLETED' AND result_code IN ('CAN_CONTINUE', 'CANNOT_CONTINUE'))
            OR
            ((task_code <> 'P-REL-03' OR status <> 'COMPLETED') AND result_code IS NULL)
        )
    ),
    ADD CONSTRAINT integration_events_fixed_code_check CHECK (
        event_type <> 'FIXED_TASK_COMPLETED'
        OR (task_code ~ '^G(0[1-9]|10)$' AND reason_code IS NULL AND result_code IS NULL)
    );

CREATE FUNCTION tide.validate_integration_event_task_reference()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    task_source_type text;
    task_code_value text;
BEGIN
    SELECT source_type, task_code
    INTO task_source_type, task_code_value
    FROM tide.teacher_tasks
    WHERE id = NEW.teacher_task_id;

    IF task_source_type IS NULL OR task_code_value IS DISTINCT FROM NEW.task_code THEN
        RAISE EXCEPTION 'integration event must match its teacher task';
    END IF;

    IF NEW.event_type = 'FIXED_TASK_COMPLETED' AND task_source_type <> 'FIXED' THEN
        RAISE EXCEPTION 'fixed completion event requires a fixed teacher task';
    END IF;

    IF NEW.event_type = 'PERSONALIZED_STATUS' THEN
        IF task_source_type <> 'PERSONALIZED' THEN
            RAISE EXCEPTION 'personalized status event requires a personalized teacher task';
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM tide.external_assignments
            WHERE teacher_task_id = NEW.teacher_task_id
              AND assignment_id = NEW.assignment_id
              AND external_task_id = NEW.external_task_id
        ) THEN
            RAISE EXCEPTION 'personalized status event must match its external assignment';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER integration_events_task_reference_guard
BEFORE INSERT
ON tide.integration_events
FOR EACH ROW
EXECUTE FUNCTION tide.validate_integration_event_task_reference();

-- 跨系统事件是追加日志。写入后不得原地修改或删除，纠错必须使用新事件版本。
CREATE FUNCTION tide.reject_integration_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'integration events are immutable';
END;
$$;

CREATE TRIGGER integration_events_immutable_guard
BEFORE UPDATE OR DELETE
ON tide.integration_events
FOR EACH ROW
EXECUTE FUNCTION tide.reject_integration_event_mutation();

-- 世文只读取以下版本化视图，不读取 integration_events.payload 或其他 tide 业务表。
CREATE VIEW tide.shiwen_personalized_status_events_v1
WITH (security_barrier = true)
AS
SELECT
    provider_event_id,
    assignment_id,
    external_task_id,
    task_code,
    status,
    sequence,
    reason_code,
    result_code,
    occurred_at,
    created_at AS published_at
FROM tide.integration_events
WHERE data_origin = 'REAL'
  AND event_type = 'PERSONALIZED_STATUS';

CREATE VIEW tide.shiwen_fixed_task_completion_events_v1
WITH (security_barrier = true)
AS
SELECT
    provider_event_id,
    external_task_id,
    teacher_id,
    task_code,
    occurred_at AS completed_at,
    created_at AS published_at
FROM tide.integration_events
WHERE data_origin = 'REAL'
  AND event_type = 'FIXED_TASK_COMPLETED';

COMMENT ON TABLE tide.integration_events IS
'TIDE 跨系统任务事件权威追加日志；共享数据库主链路通过版本化只读视图发布。';

COMMENT ON VIEW tide.shiwen_personalized_status_events_v1 IS
'供世文只读消费的真实个性化任务状态事件；不包含原始过程数据或 payload。';

COMMENT ON VIEW tide.shiwen_fixed_task_completion_events_v1 IS
'供世文只读消费的真实 G01-G10 可信完成事件；不包含分值或过程状态。';

COMMIT;
