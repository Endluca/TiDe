# TIT 外教成长系统当前架构

日期：2026-08-07

状态：共享测试库和运营 Web App 可试跑；教师端生产接入、外部日更和生产部署未完成

## 1. 核心结论

任务触发中心与教师端共用一个 PostgreSQL，`task_assignments` 是教师任务实例及当前状态的唯一事实表。

当前任务目录有 14 个已发布模板：连续编号的 G01–G09 固定成长任务与 5 个个性化改善任务。重排前的退役固定任务以隐藏编码 G00 只读保留历史 assignment。

- 任务触发中心在新教师首次写入时幂等初始化当前 9 项固定 assignment，默认 `ASSIGNED`；
- 任务触发中心读取固定任务完成状态并幂等结分，同时按确定性课程规则创建个性化 assignment；
- 教师端不创建 assignment，对已有 G/P 任务只更新执行状态；
- 当前隔离测试库仅保留显式 Seed 的 2 位虚构教师和 4 节虚构课程；源数据更新由字段差异
  Outbox 驱动 SourceWide Worker 增量投影。旧库的大批量历史试跑数据不迁入当前库。

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
├── 当前教师 / 课程宽表读取与投诉规则
├── 课程确定性触发与幂等物化
├── 积分与资格计算
├── 任务读模型 / 固定任务结分
├── 通知 / Case / 外部动作请求
├── 输出投影 / 审计
└── 版本化配置
        │ SQLAlchemy + Alembic
PostgreSQL
├── teacher_source_wide / lesson_source_wide
├── teachers / complaint_category_rules / personalized_trigger_matches
├── lesson_score_results / teacher_qualifications
├── score_accounts / score_entries
├── task_templates / task_assignments
├── 通知 / Case / 动作请求 / Outbox
└── 运营账号 / 权限 / 统一审计
        │ 受限数据库角色
教师端后端
├── 读取教师可见的 G/P 任务
└── 按状态机只更新执行状态
```

Gaea 测试交付使用一个项目和一个镜像，整套 Pod 可以水平复制。每个 Pod 保留五个逻辑
进程：FastAPI 在 `8010` 同源提供运营 React 与 API，教师 Nginx 在 `8080` 提供教师 React 并代理到
Pod 内 `3000` 的 NestJS，积分 Worker 与 SourceWide Worker 都不暴露端口。Gaea 分别把运营、教师域名绑定到
`8010/8080`。s6 负责进程生命周期，聚合健康检查同时覆盖两端 HTTP 与 Worker heartbeat。
积分候选进程通过 PostgreSQL session advisory lock 保持逻辑单活，standby 仍刷新各自
Pod heartbeat；教师全局调度使用 `tide.job_leases`，照片处理按数据库行租约认领。因此应用
可以从 2 个副本开始并使用 RollingUpdate，旧、新 Pod 短暂并存不等于后台逻辑并行执行。
Alembic 和教师端 migration 仍是发布前独立作业，数据库角色与业务写权限不会因为共享镜像
而合并。私有文件优先使用 OSS；LOCAL 模式和视频预热本地账本必须使用所有 Pod 可见的
ReadWriteMany 共享卷。这个受控 TEST 形态不提供进程级秘密隔离：同 UID 进程可接触容器级
环境变量，生产信任边界成立前仍应拆容器或 Pod。

## 3. 任务事实与写入责任

| 对象 | 当前写入方 | 必须保证的边界 |
|---|---|---|
| `task_templates` | 配置 / 迁移流程 | 当前发布 G01–G09 和 5 个已确认 P 模板；G00 仅退役保留历史 |
| `task_assignments` 固定任务创建字段 | 任务触发中心 | 新教师出现时一次初始化当前 9 项；`teacher_id + task_code` 终身唯一 |
| `task_assignments` 个性化创建字段 | 任务触发中心 | 只允许 5 个 P 模板；冻结 `why / display_title / evidence_snapshot / dedupe_key` |
| `task_assignments` 执行状态字段 | 教师端后端 | 对 G/P 任务都只更新状态五字段，携带预期 `row_version`，禁止终态回退 |
| 固定成长积分 | 积分服务 | 只读取合法 `FIXED_GROWTH + COMPLETED` assignment，每位教师每个 G 任务最多结分一次 |
| 审计事件 | 数据库触发器 / 受控服务 | 状态、前后值、操作身份与发生时间可回查；业务服务不得删除 assignment |

`task_assignments` 不保存积分、总分、出营或金牌结论。教师端不能写这些字段，触发中心也不能伪造教师完成状态。固定任务初始化只建立 9 条 `ASSIGNED` 事实，不创建通知；少于 9 条统一视为内部 `TASK_BASELINE_INCOMPLETE`，不是源数据缺失。

## 4. 数据与积分

### 数据底座

- 当前测试库只保存显式生成的 2 位虚构教师和 4 节虚构课程；历史 4 月宽表与
  37,317 节课程不迁入新库，也不是生产日更链路；
- 教师和课程当前源事实分别只保存在两张宽表。每日外部接口的鉴权、分页/水位、调度、
  修正和重放由待接入的监控服务负责；投诉分类规则文件另有独立批次与原始行追溯；
- 监控服务只有在一组来源变更完整获取、校验和标准化成功后，才能提交源表事务；
- `teacher_source_wide` 保存教师维度当前源事实，包括 CSV 的 61 个指标以及
  `is_cpl_tesol`、`is_self_introduce` 两个可空教师资料状态字段；G01 只消费 TESOL，
  Self-intro 状态继续保留但不参与 G01；旧教师快照已删除；
- 课程基线中的出席、投诉、拉黑、三个课中质量标志以及差评评价详情已用于个性化触发；`是否高峰` 已作为课程事实保存，但供给分仍只按教师统计表的 `peak_slot_cnt` 结算。

### 积分事实

- 课程分由 `lesson_source_wide` 的逐课事实计算到一课一行的 `lesson_score_results`，
  再按受影响教师聚合到积分组件与账户；
- 供给分只认 `peak_slot_cnt >= 40`，首次达成 +10 且永久锁定，它不是教师任务；
- 当前 9 项固定成长任务合计 30 分，只由共享 assignment 的可信完成事实产生；
- 5 个个性化改善任务固定为 0 分，任务完成和干预效果分开。

## 5. 触达记录

触达记录是系统输出的查询投影，不再保存一份重复的任务事实。

| 输出 | 权威来源 | 当前状态 |
|---|---|---|
| `TEACHER_TASK` | `task_assignments` | 当前 9 项固定任务是新教师基线，个性化任务按规则创建；教师执行状态均以 assignment 为准 |
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
- `seed_database.py` 只幂等补齐 14 个当前已发布任务模板，不创建教师或运行时业务事实，也不删除真实课程与触发结果；
- 运行库不迁入旧任务和旧触发策略的历史版本；需要追溯时使用 Git 历史和迁移前受控备份；
- 本地共享表、受限角色、列权限、状态机、终态保护、`row_version` 和统一审计已经迁移；每日教师接口必须复用新教师初始化入口，教师端生产服务仍需完成“只更新已有任务状态”的真实连接验收；
- 当前运营 API 的公开读写路径已直接使用 PostgreSQL；输出重试、关联通知、审计和 Outbox
  在同一事务完成，共享测试库双 Worker 冲突验证通过。每个 Pod 都运行固定任务积分候选
  进程，但同一时刻只有持 PostgreSQL advisory lock 的 leader 执行结算；教师全局任务和
  照片处理分别由全局租约、行租约协调。真实触达 Worker、外部日更、生产监控和恢复演练
  尚未完成。
- Gaea 单模块骨架已把两套 Web、两套 API 和 heartbeat 结算 Worker 收入同一镜像，并用
  `8010/8080` 两个端口承载不同域名；该配置只证明源码具备统一构建入口，不代表内部镜像
  已在 Gaea 构建成功，也不代表双端口 Ingress、迁移作业或生产切流已经完成。
- 经营总览使用数据库聚合，不读取全部教师 JSON；教师列表、任务明细、输出和审计均为
  服务端分页，首次进入页面不自动读取业务数据。

详细字段、权限与状态机以 [教师端共享任务表契约](../contracts/教师端共享任务表契约.md) 为准；当前已落库结构以 [数据库表结构](数据库表结构.md) 为准。
