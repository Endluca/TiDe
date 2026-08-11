BEGIN;

-- Local/test-only representation of the operations-owned public rev56 row.
-- Production must receive this row from the public Alembic chain; the teacher
-- migration only creates the deterministic Tide execution that references it.
INSERT INTO public.task_templates (
    row_id,
    template_id,
    template_version,
    status,
    revision,
    output_type,
    execution_owner,
    external_task_template_code,
    source_mode,
    payload,
    created_by,
    updated_by,
    integration_mode
) VALUES (
    'P-FB-NEGATIVE:v1',
    'P-FB-NEGATIVE',
    1,
    'PUBLISHED',
    56,
    'TEACHER_TASK',
    'TEACHER_APP',
    'TIT.P.FB.NEGATIVE',
    'REAL',
    '{"template_id":"P-FB-NEGATIVE","output_type":"TEACHER_TASK","audience":"TEACHER","owner":"TIT_GROWTH_OPS","execution_owner":"TEACHER_APP","integration_mode":"OUTBOUND_MANAGED","category":"PERSONALIZED_IMPROVEMENT","dimension":"USER_FEEDBACK","stage":"TRIGGERED","ops_name_zh":"差评改善","content_locale":"en","content_status":"READY","title":"Feedback Improvement","why_template":"The same negative-feedback signal has appeared more than once for this teacher.","how_summary":"Complete the configured improvement activity for the feedback issue shown in the task reason. Depending on the assigned activity, you may need to submit a teaching-environment photo for review or complete another guided action.","completion_standard":"The teacher app marks the task as completed after every requirement for the assigned improvement activity, including any required photo review, is satisfied.","benefit":"This task carries no points. It targets a repeated learner-feedback issue.","priority":"P1","score_type":"ZERO","score_value":0,"source_mode":"REAL"}'::jsonb,
    'local_contract_fixture',
    'local_contract_fixture',
    'OUTBOUND_MANAGED'
)
ON CONFLICT (row_id) DO UPDATE SET
    template_id = EXCLUDED.template_id,
    template_version = EXCLUDED.template_version,
    status = EXCLUDED.status,
    revision = EXCLUDED.revision,
    output_type = EXCLUDED.output_type,
    execution_owner = EXCLUDED.execution_owner,
    external_task_template_code = EXCLUDED.external_task_template_code,
    source_mode = EXCLUDED.source_mode,
    payload = EXCLUDED.payload,
    updated_by = EXCLUDED.updated_by,
    integration_mode = EXCLUDED.integration_mode,
    updated_at = now();

COMMIT;
