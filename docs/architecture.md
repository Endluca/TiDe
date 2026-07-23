# TIT 外教成长系统当前架构

日期：2026-07-24

状态：共享 `task_assignments` 本地迁移已完成；教师端生产接入未完成

## 1. 核心结论

任务触发中心与教师端共用一个 PostgreSQL，`task_assignments` 是教师任务实例及当前状态的唯一事实表。

当前任务目录有 15 个已发布模板：G01–G10 固定成长任务与 5 个个性化改善任务。

- 任务触发中心在新教师首次写入时幂等初始化 G01–G10 assignment，默认 `ASSIGNED`；
- 任务触发中心读取固定任务完成状态并幂等结分，同时按确定性课程规则创建个性化 assignment；
- 教师端不创建 assignment，对已有 G/P 任务只更新执行状态；
- 当前 2.0 课程基线的有效投影为 829 个开放个性化任务、512 条提醒、0 个运营 Case 和 0 条 `PENDING_DATA`。历史已消费输出保留，失效的未消费输出取消，不删除共享任务事实。

共用数据库不等于共用写权限。教师端服务和触发中心必须使用不同的受限数据库角色；浏览器不得直连数据库。

## 2. 系统结构

```text
运营 Web App
├── 经营总览 / 待办处置
├── 任务进展 / 触达记录
├── 教师档案 / 课程证据
├── 任务规则 / 积分与门槛
└── 操作审计
        │ Cookie Session + REST
任务触发中心 FastAPI
├── Auth / RBAC
├── 教师宽表 / 课程基线 / 投诉规则导入
├── 课程确定性触发与幂等物化
├── 积分与资格计算
├── 任务读模型 / 固定任务结分
├── 通知 / Case / 外部动作请求
├── 输出投影 / 审计
└── 版本化配置
        │ SQLAlchemy + Alembic
PostgreSQL
├── data_import_batches / source_records
├── teachers / teacher_metric_snapshots
├── lesson_facts / complaint_category_rules / personalized_trigger_matches
├── lesson_dimension_scores
├── score_accounts / score_entries
├── task_templates / task_assignments
├── 通知 / Case / 动作请求 / Outbox
└── 运营账号 / 权限 / 统一审计
        │ 受限数据库角色
教师端后端
├── 读取教师可见的 G/P 任务
└── 按状态机只更新执行状态
```

## 3. 任务事实与写入责任

| 对象 | 当前写入方 | 必须保证的边界 |
|---|---|---|
| `task_templates` | 配置 / 迁移流程 | 当前只保留 10 个 G 模板和 5 个已确认 P 模板；不迁回其他历史目录 |
| `task_assignments` 固定任务创建字段 | 任务触发中心 | 新教师出现时一次初始化 G01–G10；`teacher_id + task_code` 终身唯一 |
| `task_assignments` 个性化创建字段 | 任务触发中心 | 只允许 5 个 P 模板；冻结 `why / display_title / evidence_snapshot / dedupe_key` |
| `task_assignments` 执行状态字段 | 教师端后端 | 对 G/P 任务都只更新状态五字段，携带预期 `row_version`，禁止终态回退 |
| 固定成长积分 | 积分服务 | 只读取合法 `FIXED_GROWTH + COMPLETED` assignment，每位教师每个 G 任务最多结分一次 |
| 审计事件 | 数据库触发器 / 受控服务 | 状态、前后值、操作身份与发生时间可回查；业务服务不得删除 assignment |

`task_assignments` 不保存积分、总分、出营或金牌结论。教师端不能写这些字段，触发中心也不能伪造教师完成状态。固定任务初始化只建立 10 条 `ASSIGNED` 事实，不创建通知；少于 10 条统一视为内部 `TASK_BASELINE_INCOMPLETE`，不是源数据缺失。

## 4. 数据与积分

### 数据底座

- 4 月教师 30 天宽表和 37,317 节课程明细 2.0 是当前手工基线，不是生产日更链路；
- 批次、无损原始行和类型化投影已落库；每日外部接口的鉴权、分页/水位、调度、修正和重放仍未实现；
- 只有完整拉取、校验和标准化均成功的批次，才能更新当前事实并触发重算；
- `teacher_metric_snapshots` 保存教师维度的计分输入，包括 `peak_slot_cnt`、`first_booked_date`、`is_cpl_tesol`、`is_self_introduce` 等已确认字段；
- 课程基线中的出席、投诉、拉黑、三个课中质量标志以及差评评价详情已用于个性化触发；`是否高峰` 已作为课程事实保存，但供给分仍只按教师统计表的 `peak_slot_cnt` 结算。

### 积分事实

- 课程分按教师维度统计快照计算；
- 供给分只认 `peak_slot_cnt >= 40`，首次达成 +10 且永久锁定，它不是教师任务；
- G01–G10 固定成长任务合计 30 分，只由共享 assignment 的可信完成事实产生；
- 5 个个性化改善任务固定为 0 分，任务完成和干预效果分开。

## 5. 触达记录

触达记录是系统输出的查询投影，不再保存一份重复的任务事实。

| 输出 | 权威来源 | 当前状态 |
|---|---|---|
| `TEACHER_TASK` | `task_assignments` | G01–G10 是新教师基线，个性化任务按规则创建；教师执行状态均以 assignment 为准 |
| `NOTIFICATION / REMINDER / RECOMMENDATION` | `notifications` | 当前规则命中 512 条课中质量提醒；没有真实回执不得表述为已送达 |
| `OPS_CASE` | `ops_cases` | 当前 2.0 基线没有开放的严重投诉处理事项 |
| `PENDING_DATA` | `personalized_trigger_matches` | 当前 0 条；评价详情和投诉分类均已满足当前触发所需字段 |
| `ACTION_REQUEST` | `outbound_outputs` 中对应输出记录 | 高风险动作只到待审批，不自动执行 |

任务行提交成功只代表任务事实存在。教师是否查看、开始或完成，只看 `task_assignments.status`；通知是否送达只看通知与 Outbox 回执，两者不得互相推断。

## 6. Agent 边界

当前触发是确定性 Workflow，不需要 Agent 猜测。Agent 只保留为未来多信号编排器：

- 确定性单一信号优先由确定规则处理；
- Agent 只能从服务端已发布的模板与策略候选集中选择；
- Agent 不能创作新任务、新阈值、新动作类型或教师可见自由文案；
- 没有可信数据、已发布模板和已发布规则时，正式创建必须 fail closed。

## 7. 初始化与生产边界

- Alembic 是唯一建表和 Schema 变更方式；API 启动不自动建表或灌入 Mock；
- `seed_database.py` 只幂等补齐 15 个当前任务模板，不创建教师或运行时业务事实，也不删除真实课程与触发结果；
- 运行库不迁入旧任务和旧触发策略的历史版本；需要追溯时使用 Git 历史和迁移前受控备份；
- 本地共享表、受限角色、列权限、状态机、终态保护、`row_version` 和统一审计已经迁移；每日教师接口必须复用新教师初始化入口，教师端生产服务仍需完成“只更新已有任务状态”的真实连接验收；
- 当前本地服务仍只允许单 API 进程。多 Worker 前必须完成事务型 Repository、并发回归、Outbox 租约和失败重试。

详细字段、权限与状态机以 [教师端共享任务表契约](../contracts/教师端共享任务表契约.md) 为准；当前已落库结构以 [数据库表结构](数据库表结构.md) 为准。
