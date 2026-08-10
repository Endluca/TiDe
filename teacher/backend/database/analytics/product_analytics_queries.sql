-- TIDE 产品分析一期只读查询
-- 参数采用 psql 变量写法，例如：\set from_time '2026-07-01'
-- 所有查询只读；业务效果视图只表示相关性，不表示因果关系。
-- 任务语义查询使用 v2：task_code 只经 assignment 的稳定模板行解析，
-- raw_task_code / raw_task_codes 只用于核对事件发生时的原始证据。

-- 1. 按任务、版本、入口、位置、设备和语言查看完整漏斗。
SELECT *
FROM tide.analytics_task_funnel_v2
WHERE cohort_day >= :'from_time'::timestamptz
  AND cohort_day < :'to_time'::timestamptz
  AND (NULLIF(:'task_code', '') IS NULL OR task_code = :'task_code')
ORDER BY cohort_day DESC, task_code, template_version, entry_source;

-- 2. 还原一个 assignment 的完整事件旅程。
SELECT
    event_name,
    event_source,
    session_id,
    task_code,
    raw_task_code,
    stable_template_row_id,
    task_code_resolution,
    template_version,
    execution_contract_version,
    entry_source,
    display_position,
    step_key,
    attempt_no,
    result,
    error_code,
    occurred_at
FROM tide.analytics_actor_task_journey_v2
WHERE task_assignment_id = :'task_assignment_id'
ORDER BY occurred_at, received_at;

-- 3. 查看该 assignment 的首次通过、最终通过、退出、恢复和耗时。
SELECT *
FROM tide.analytics_task_assignment_funnel_v2
WHERE task_assignment_id = :'task_assignment_id';

-- 4. 各步骤流失。
SELECT
    task_code,
    raw_task_codes,
    stable_template_row_id,
    task_code_resolution,
    template_version,
    step_key,
    step_type,
    started_teachers,
    completed_teachers,
    failed_count,
    round(
        completed_teachers::numeric / NULLIF(started_teachers, 0),
        4
    ) AS step_completion_rate
FROM tide.analytics_task_step_funnel_v2
WHERE NULLIF(:'task_code', '') IS NULL OR task_code = :'task_code'
ORDER BY task_code, template_version, step_key;

-- 5. 视频、上传和摄像头质量。
SELECT *
FROM tide.analytics_content_quality_v2
WHERE NULLIF(:'task_code', '') IS NULL OR task_code = :'task_code'
ORDER BY task_code, template_version, step_key;

-- 6. 技术异常：affected_session_rate 是受影响会话占比，
--    不是“单次接口请求失败率”。
SELECT *
FROM tide.analytics_technical_quality_v1
WHERE event_day >= :'from_time'::timestamptz
  AND event_day < :'to_time'::timestamptz
ORDER BY event_day DESC, failure_count DESC;

-- 7. FAQ / AI 帮助使用率与 FAQ 命中率。
SELECT *
FROM tide.analytics_help_usage_v1
WHERE event_day >= :'from_time'::timestamptz
  AND event_day < :'to_time'::timestamptz
ORDER BY event_day DESC, entry_source;

-- 8. 查重复 event_id。正常结果应为空。
SELECT anonymous_teacher_id, event_id, count(*) AS duplicate_count
FROM tide.app_events
GROUP BY anonymous_teacher_id, event_id
HAVING count(*) > 1;

-- 9. 抽样核对分析漏斗与共享任务最终状态。
SELECT
    funnel.task_assignment_id,
    funnel.task_code,
    funnel.raw_task_codes,
    funnel.stable_template_row_id,
    funnel.task_code_resolution,
    assignment.status AS business_status,
    funnel.completed_at,
    funnel.finally_passed,
    funnel.first_validation_result,
    funnel.max_attempt_no
FROM tide.analytics_task_assignment_funnel_v2 funnel
JOIN public.task_assignments assignment
  ON assignment.assignment_id = funnel.task_assignment_id
WHERE assignment.updated_at >= :'from_time'::timestamptz
ORDER BY assignment.updated_at DESC
LIMIT 100;

-- 10. 上线前检查核心任务事件查询计划。
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT event_name, occurred_at, properties
FROM tide.app_events
WHERE task_assignment_id = :'task_assignment_id'
  AND occurred_at >= :'from_time'::timestamptz
ORDER BY occurred_at;
