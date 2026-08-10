BEGIN;

-- 该视图依赖已经退出当前读取链路的历史教师快照表。
-- 不使用 CASCADE：若出现未登记的下游依赖，迁移必须失败并先完成消费者审计。
DROP VIEW IF EXISTS tide.analytics_task_business_change_v1;

COMMIT;
