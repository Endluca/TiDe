# DTS direct 开发冻结实施规格

> 状态：开发冻结版（业务与工程输入）
> 版本：`dts-domain-v2`
> 冻结日期：2026-08-21
> 适用范围：DTS 事实持久化、源课程/教师参与、DOM 评价、缺席、关系、课程聚合、逐课积分及教师端读取切换
> 非完成证明：本文不表示迁移已执行、DTS 已消费、Worker 已启用、积分已重算或生产已发布
> 运行架构补充（2026-08-24）：业务规则继续以本文为准；V1 兼容、双通道追平、切流和回滚章节不再用于本次发布。当前只运行 `SINGLE_PIPELINE`，rev101 清空旧消费事实，DOM/OVS 从显式新时间开始，首条 INSERT 建立事实基线；缺失课程当前态的 appoint UPDATE 忽略并推进 checkpoint。

## 1. 权威性与范围

本文件是本轮 DTS 业务实现的唯一冻结输入。开发、迁移、测试和验收遇到以下旧文档冲突时，以本文件为准：

- `DTS事件直接投影规则.md` 中“子事件找不到主记录就永久 ignored”的 v1 行为；
- `TIT课程级数据与Mock字段契约.md`、`数据库表结构.md` 中“一节源课程只对应一位教师”的旧结构；
- `数据与积分规则.md` 中以旧 `lesson_source_wide` 单行直接归属教师的描述；
- `DTS_direct事件人工验证与纠错手册.md` 中标为“现行代码行为”的差异说明。

人工验证仍使用 `DTS_direct事件人工验证与纠错手册.md`；本文件回答“目标代码必须怎样实现”。两者不能互相替代。

本轮地域范围明确为：

1. 源课程、教师参与、缺席、处罚、关系、投诉和 QA 的通用模型覆盖 `dom/ovs`；
2. `dom_user_teacher_grading` 与 `dom_grading_label_log` 使用本文明确的 DOM 口径；
3. OVS 评价字段和值域尚未完成源表对账。本轮只持久化 OVS 评价源记录，不套用 DOM 好差评规则、不生成新的 OVS 评价积分；现行 OVS 读取在独立开关下保持不变，禁止新旧逻辑同时写同一结果。

## 2. 不可变业务规则

### 2.1 事实与教师参与

- `source_appoint_id` 标识一节源课程；一节源课程始终只计一节。
- 同一源课程可以有多条教师参与；每次实际指派形成一个参与阶段。
- 课程准入不判断 `appoint.use_point`、`appoint.status` 或入职 30 天。`buy/free/cancel/on/end/其他值/NULL` 均保留源事实。
- `appoint.t_id: A→B` 在首次完课前表示代课：A 立即转为 `t_absent`，B 新增参与；不等待缺席原因。
- 缺席原因只补证据和决定动作，按 `source_appoint_id + teacher_id` 归属参与，不决定教师是否缺席。
- 评价、标签、处罚、投诉、摄像头等子事件按 `appoint_id` 保存，不以 `end` 为入库前提。
- 首次进入 `end` 时冻结当时教师参与；课程积分全部归冻结参与。普通 DTS 事件不得静默转移完课归属。

### 2.2 三值语义

```text
NULL  = 来源未提供、集合不完整或无法证明
false = 完整权威来源明确证明不成立
0     = 完整权威来源明确证明数量为 0
```

缺少异常事件不能直接写 `false`。因此迟到、早退、摄像头、CPU、网络、TESOL 等字段必须保留来源完整性状态。

### 2.3 资格与分数

- 实际分 `raw_total_score` 不封顶，教师端显示 `min(raw_total_score, 200)`。
- 首次达到 100 且满足全部出营条件后获得出营资格，冻结 `graduation_score_locked=100`。
- 首次达到 200 且满足全部金牌条件后获得金牌资格。
- 出营、金牌及其首次获得时间不可回退；事实纠错只刷新当前条件和当前分数。
- `LEFT/BLOCKED` 不停止新事实和新积分。

## 3. 唯一处理链路

目标实现不再允许 direct 绕过持久事实直接改教师聚合。`direct` 与 `queued` 只允许在执行时机上不同，必须复用同一领域服务、选择器和重算函数。

```text
DTS 事件
→ 白名单、类型、隐私和主键校验
→ 同一事务写 dts_ingest_events
→ 同一事务追加 dts_source_row_versions（受保护 before/after）
→ 同一事务 UPSERT dts_source_rows（DELETE 写 tombstone）
→ 同一事务合并 dts_dirty_keys
→ 同一事务推进 dts_ingest_checkpoints
→ 提交后 ACK
→ 领域投影器按脏键读取完整当前集合
→ 重建源课程、参与、标签、关系等规范化领域事实
→ source_wide.changed.v2 Outbox
→ SourceWide Worker v2 重建教师聚合、任务命中、积分和当前资格
```

规则如下：

1. 事件幂等键固定为 `(source_region, source_partition_epoch_id, topic, partition_id, offset_value)`。
2. 来源事件版本身份固定为同一五元组；来源行当前态身份固定为
   `(source_region, source_table, source_key)`，`source_key` 由表主键规范化生成，不用业务内容拼接。
3. `dts_source_row_versions` 保存经白名单裁剪、DOM HMAC 后的 before/after，追加后禁止 UPDATE/DELETE；`dts_ingest_events` 仍只保存路由元数据。appoint 的教师/状态转换和关系时间线必须从版本历史重建，不能只比较最终镜像。
4. 同一来源行跨 CDC、snapshot fence 与源时钟回退的业务应用顺序只认 §4.1 的
   `source_row_revision`。BASELINE 只作快照证据且 revision 为空；真正推进 current 的 CDC 或
   SNAPSHOT_DIFF 在来源行锁内取 revision+1。`source_position` 只作 envelope 校验和审计，不能决定跨
   version kind 的先后。任何 fence 后 CDC 都不得因源时钟偏差被快照反向覆盖。
5. UPDATE 必须先用已有 `dts_source_rows.source_row` 合并稀疏 before/after，再校验主键未原地变化；合并后的完整 before/after 同时进入版本历史。
6. DELETE 不物理删除 `dts_source_rows`，保留 before 镜像并写 `is_deleted=true`。
7. 缺主课程、缺教师或缺字典的子事实仍持久化并将脏键保持 `PENDING/RETRY`；不得以“找不到目标”为由永久丢弃。
8. 未知表、退役表和明确非业务控制事件可以 `IGNORED`；退役表只写不含业务 payload 的 ingest ledger
   元数据并推进 checkpoint，不写 source versions/current、脏键或 Outbox。已列入本规格的有效业务表
   不能因依赖乱序被 `IGNORED`。
9. checkpoint 只证明事实已耐久接收，不证明领域投影、任务物化、积分结算或外部动作已完成。

### 3.1 写入所有权

| 组件 | 唯一可写范围 | 禁止写入 |
|---|---|---|
| DTS Ingestor | ingest ledger、`version_kind=CDC` 的 source row versions/current、CDC 触发的 dirty keys、checkpoint；通过受限 `scope_membership_apply_cdc()` 只写 active membership overlay；仅其失败产生的 `DTS_SOURCE_CONFLICT` 技术 Case | BASELINE/SNAPSHOT_DIFF epoch 发布；scope 指针/快照基线列；课程/参与事实、聚合、任务、积分、资格 |
| Scope Coordinator（接入组件内） | `dts_source_scope_states`、`dts_source_scope_snapshots`、`dts_source_snapshot_rows`、membership snapshot 基线/发布列；`version_kind=BASELINE/SNAPSHOT_DIFF` 的 source row versions/current、对应 dirty keys及 `SOURCE_SCOPE` Outbox；独占 candidate 装载、epoch 状态与 active 发布切换 | CDC checkpoint/ACK；课程/参与、teacher 聚合、任务、积分、资格；其他组件不得修改 active/candidate 指针、epoch 或 staging/snapshot 基线列 |
| Domain Projector | `source_courses`、参与、类型化 current、标签、关系；检测完课冲突；在 V2_PRIMARY 下与冲突事实同事务唯一创建/更新 `COURSE_COMPLETION_CORRECTION` Case并回填指针；其 dirty DEAD/来源冲突技术 Case | checkpoint、scope 完整性、teacher 聚合、一般任务、积分、资格；SHADOW_BUILD 禁止写生产业务 Case |
| SourceWide Worker v2 | 正常事件下写 `teacher_source_wide` 聚合、favorite observations/attributions、逐课结果、trigger matches、`TASK_PLAN` revision/Outbox、任务物化、课程/收藏/供给 score entries及账户/当前资格判断；纠错事件下幂等刷新聚合和全部 match/output；其 TASK_PLAN/投影 DEAD 技术 Case | DTS 状态表、来源版本、冻结字段；`FIXED_TASK_AWARD` 或 task assignment change消费；不得重复纠错事务已结算的同键流水 |
| Fixed Task Score Settler | 唯一消费 `task.assignment_changed.shared(.v1)`，唯一 INSERT `entry_type=FIXED_TASK_AWARD`，更新 G01–G09 子项后调用共用账户/资格重建函数 | 课程、收藏、供给流水/settlement、TASK_PLAN/assignment 创建、来源事实 |
| Favorite Observation Worker（SourceWide 子角色） | 只领取/更新 observation、归因与对应可逆流水；其 observation DEAD 技术 Case | 其他来源/课程/任务/资格；不得把 WAITING_HISTORY/WAITING_EVIDENCE 当失败 |
| Correction Service | 只在 `V2_PRIMARY` 的已批准决定事务内写完课字段、参与 role、观察失效/重建、归因与课程组件积分冲正/重结、账户与当前资格，并写 COURSE/PARTICIPATION v2 Outbox | 兼容/回滚 mode 下提交决定；普通 DTS；trigger matches、一般任务、投诉/摄像头 Case 或提醒；删除审计或回退历史资格 |

Fixed Task Score Settler 在 `V1_COMPAT_DUAL_CAPTURE/V2_PRIMARY/ROLLED_BACK` 三种 mode 都保持固定任务积分的
唯一 owner；切换只更换课程/关系来源聚合 owner，不更换 fixed-task ledger owner。SourceWide、Fixed Task
Settler 和 V2_PRIMARY Correction Service 三类 writer 都只能在各自
业务流水提交后调用唯一 `rebuild_teacher_score_and_qualification_v2(teacher_id,expected_projection_vector)`，
通过 PostgreSQL score projection advisory lock串行更新共享 account/qualification；SourceWide不得“全量重建”
时补造 FIXED_TASK_AWARD，Settler也不得写课程流水。数据库按受限函数/角色和 entry_type CHECK防绕过。

三 mode 的 Settler 都从共享 assignment Outbox及时结算；cutover/rollback 维护窗先停止新 assignment写入并
排空该类 active Outbox 的 `PENDING`，任何 `DEAD_LETTER` 或完成 assignment 缺 canonical 固定任务流水都阻断 final
shadow；`PUBLISHED` 表示已完成。active `outbox_events` 不存在 PROCESSING/RETRY/DEAD/PARKED 状态，旧
CANCELLED/PARKED 必须在启用三态约束前按本节 legacy archive 协议收口，不能进入运行时 readiness 查询。
取得 cutover exclusive 后 Settler不再运行；全量事务只核对/复用固定任务流水和重建账户，不新消费
task event。提交后恢复同一 Settler。统一锁序为 cutover shared → task event/assignment或 source event/course →
score projection lock → score entry/account/qualification；配置发布也在 cutover shared/catalog lock 后再取 score
lock，禁止反序。

三类积分 writer 及任何调用共用 rebuild 的命令，从读取规则到提交必须一直持有
`CONFIG:SCORE_GRADUATION` catalog shared lock；
`expected_projection_vector` 必须含当前 PUBLISHED config version_id、payload_hash和score rule version，取得 score
projection lock 后再次校验，不一致整事务重试。发布方持 catalog exclusive lock完成换版和全量重算后再提交，
因此旧规则 Worker 不得在新规则发布后覆盖账户或资格。统一顺序为 cutover shared→score catalog→业务行→score lock。

Domain Projector 每次规范化事实真实变化时写 `source_wide.changed.v2` Outbox：

- `aggregate_type` 只能为 `COURSE/PARTICIPATION/TEACHER/TEACHER_STUDENT/LABEL/COMPLAINT_CATEGORY/COMPLETION_CONFLICT/SOURCE_SCOPE`；
- aggregate key 必须包含 source_region 及对应业务键；唯一例外是 DOM/OVS 共用的投诉分类字典，其
  source_region 固定写 `dom`，不得按投诉所在地区复制一份 `ovs` 分类 aggregate。canonical key 使用字段名排序、无多余空白的 UTF-8 JSON。
  `aggregate_id=v2:{aggregate_type}:{SHA-256(canonical key)}`，完整类型化 key 放 payload；
- payload 只含 typed aggregate_key、changed_fields、aggregate_revision、正式 `source_row_revision`、审计
  source position、rule version 和 §9 的 cutover coverage identity，
  不含原始学生 ID；
- `event_id` 固定为 `source_wide.changed.v2:{aggregate_type}:{aggregate_id}:{aggregate_revision}`；
- Worker 使用 `FOR UPDATE SKIP LOCKED` 领取并在持有数据库行锁的同一事务内完成幂等投影，成功把
  Outbox 标 `PUBLISHED`。领取事务先锁 Outbox 行，再创建 handler SAVEPOINT；业务失败只
  `ROLLBACK TO SAVEPOINT`，仍持有 Outbox 行锁，并在同一外层事务更新 attempt_count/available_at/last_error，
  第 8 次同时写 `DEAD_LETTER` 与 §6.6 Case后提交。成功则业务写与 PUBLISHED同事务提交。进程崩溃时外层
  事务回滚、行锁释放、状态仍为 `PENDING`；禁止释放锁后另起失败事务，避免另一 Worker抢先领取/旧失败
  覆盖成功。Outbox handler不得在 savepoint 内做不可回滚外部副作用。Outbox 不新增 lease 或
  `COMPLETED` 状态；收藏 observation 才使用显式租约。

Outbox 状态集只有 PENDING/PUBLISHED/DEAD_LETTER。INSERT 后 event/aggregate identity、payload、created_at
不可改且禁止 DELETE；只允许状态技术列更新。失败 attempt 1–7 保持 PENDING，第8次转 DEAD_LETTER。
DEAD 只能经受限
`recover_outbox_event_v2(command_id,event_id,expected_payload_hash,expected_recovery_count,reason)` 回 PENDING；
canonical command request hash字段顺序为`protocol_version='outbox-recovery-v2',command_id,event_id,
expected_payload_hash,expected_recovery_count,reason`，幂等key=`recover-outbox:v2:{command_id}`且永不过期。
函数先锁command advisory/idempotency行：同request已有成功响应直接校验resource event/payload仍存在后返回REPLAYED，
不读取当前status/recovery_count；同command异request报`OUTBOX_RECOVERY_COMMAND_CONFLICT`。首次命令再锁event并要求
DEAD_LETTER、payload hash和recovery_count精确等于expected，随后attempt=0、recovery_count+1、
available_at=transaction_timestamp()并写audit/response；否则`OUTBOX_RECOVERY_STALE`。因此首次恢复响应丢失后
即使event已再次DEAD，同command重放也不会恢复下一代；下一次恢复必须新command_id并传当前recovery_count。
PUBLISHED 必须有 published_at且无出边，只有 cutover PUBLISHED 可有 settled_by_run_id。恢复后再次失败仍
使用同一 event/技术 Case，不新造 payload；任意非法边或 payload UPDATE 由 Trigger 拒绝。

迁移归档表 `outbox_events_legacy_archive` 以原 `event_id` 为 PK、原 `outbox_id` 唯一，保存原 aggregate/event
identity、已通过现行敏感字段检查的原 payload及 payload_hash、原 status/attempt/error/created/published时间、
`archive_source_row_hash`、
`archive_reason=PROVEN_NON_REAL_MOCK_CANCELLED|PROVEN_RETIRED_CONTROL_PARKED`、`proof_type/proof_hash`、
`migration_run_id`、`archive_audit_event_id` FK、archived_at/by。若旧 payload 未通过敏感检查则停止
`LEGACY_OUTBOX_PAYLOAD_UNSAFE`，不能把 raw payload搬入归档。表只允许 cutover migration角色 INSERT，普通
角色只读，Trigger 禁止 UPDATE/DELETE。

`archive_source_row_hash` 固定为数据库函数对canonical JSON v1计算SHA-256；字段顺序为outbox_id、event_id、
aggregate_type、aggregate_id、event_type、payload_hash、status、available_at、attempt_count、recovery_count、
last_error、settled_by_run_id、created_at、published_at。timestamp统一UTC微秒，NULL保留JSON null，整数十进制，
字符串不trim；payload本体不重复进入row hash，只使用同一canonical函数计算的payload_hash。
proof_type只允许两值，函数从数据库行重算proof_hash，调用方不能提交自由文本证据：

- `MOCK_SEED_CANCELLED`：active必须status=CANCELLED、aggregate_type=TASK_ASSIGNMENT、published/settled为空、
  last_error=MOCK_SEED_DELIVERY_DISABLED；payload的scenario/origin/source/source_mode/mock_only/
  delivery_disabled/execution_allowed必须分别为MOCK_SEED_SHARED_TASKS/MOCK_SEED/MOCK_SEED/MOCK/true/true/
  false；aggregate_id必须指向source_mode为MOCK*且created_by=MOCK_SEED的assignment，并且该assignment无
  FIXED_TASK_AWARD流水。proof hash覆盖这些typed列与被引用assignment的PK/source_mode/created_by。
- `MIGRATION_20260729_37_RETRY_PARKED`：active必须status=PARKED、
  event_type=outbound_output.retry_requested.v1、last_error=NO_OUTPUT_CONSUMER_CONFIGURED、published/settled为空，
  且payload._migration_20260729_37.previous_status=PENDING并存在previous_last_error键。proof hash覆盖event
  identity、上述marker和技术状态。

敏感检查只允许数据库不可变函数
`legacy_outbox_payload_safety_issues_v1(payload jsonb,proof_type text)`，返回按错误码 UTF-8 bytes 排序去重的
`text[]`；空数组才安全。payload 必须是 JSON object，canonical UTF-8 JSON 不超过 16384 bytes、嵌套深度不
超过 2；JSON pointer 使用 RFC 6901 转义。两种 proof 的递归允许字段和类型是封闭白名单，任何额外 key 都
返回 `KEY_NOT_ALLOWED:{pointer}`：

- `MOCK_SEED_CANCELLED` 顶层只允许
  `assignment_id,teacher_id,task_code,task_kind,from_status,to_status,status_reason_code,result_code,completed_at,
  row_version,scenario,origin,source,source_mode,mock_only,delivery_disabled,execution_allowed`，不允许 object/array；
  ID/code 字符串必须匹配 `[A-Za-z0-9._:-]{1,128}`，时间必须为 JSON null 或 RFC3339 UTC 微秒字符串，
  row_version 为正整数，枚举/七枚 mock 标志按上文 proof 谓词精确校验。
- `MIGRATION_20260729_37_RETRY_PARKED` 顶层只允许
  `output_id,attempt_count,display_type,actor_id,_migration_20260729_37`；前四项只允许安全 ID/code 字符串或
  非负整数，marker 必须是唯一允许的嵌套 object，且只允许 `previous_status,previous_last_error`，前者精确
  `PENDING`，后者为 JSON null 或 `[A-Za-z0-9._:-]{1,256}`。

函数还递归拒绝任何字符串中的 credential/连接串/私钥特征（大小写不敏感的 `Bearer `、`password=`、
`postgres://`、`postgresql://`、`mysql://`、`jdbc:`、`BEGIN ... PRIVATE KEY`）以及任何 `dom:v1:`/`ovs:v1:`
student token，分别返回 `CREDENTIAL_PATTERN:{pointer}`/`STUDENT_TOKEN_NOT_ALLOWED:{pointer}`。结构、类型和值
错误固定为 `PAYLOAD_NOT_OBJECT/PAYLOAD_TOO_LARGE/PAYLOAD_TOO_DEEP/TYPE_INVALID:{pointer}/
VALUE_INVALID:{pointer}`。checker 不读表、不接受自定义 allowlist，迁移角色也不能绕过。

`preview_legacy_outbox_archive_v2(event_id,proof_type)` 是唯一预览入口，返回
`archive_source_row_hash,payload_hash,proof_hash,safety_issue_codes,eligible`。proof hash 与 row hash 共用
canonical JSON v1：UTF-8、key 按 bytes 排序、timestamp UTC 微秒、JSON null 保留、整数十进制、字符串不
trim。`MOCK_SEED_CANCELLED` proof canonical 字段固定为
`proof_type,event_id,outbox_id,aggregate_type,aggregate_id,event_type,status,published_at,settled_by_run_id,
last_error,payload_hash,scenario,origin,source,source_mode,mock_only,delivery_disabled,execution_allowed,
payload_assignment_id,assignment_id,assignment_source_mode,assignment_created_by,fixed_task_award_count`；
payload_assignment_id、aggregate_id 和 linked assignment_id 必须相等。
`MIGRATION_20260729_37_RETRY_PARKED` 字段固定为
`proof_type,event_id,outbox_id,aggregate_type,aggregate_id,event_type,status,published_at,settled_by_run_id,
last_error,payload_hash,output_id,attempt_count,display_type,actor_id,marker_previous_status,
marker_previous_last_error`。未知proof、悬空assignment、任一谓词不符、checker 非空或proof hash变化固定报
`LEGACY_OUTBOX_PROOF_INVALID`（checker 非空优先报 `LEGACY_OUTBOX_PAYLOAD_UNSAFE`），不得归档。

唯一受限函数 `archive_legacy_outbox_event_v2(event_id,expected_row_hash,proof_type,expected_proof_hash,
migration_run_id)` 锁原
active row和归档键，在锁内重新调用同版本 checker/row/proof hash，校验 §9 的非 REAL/退役证明，先插 archive与 audit再以函数专属权限从 active表删除，全部
同一事务；这是 Outbox禁止 DELETE 的唯一历史迁移例外。重复调用时“active不存在、archive row/hash/证明完全
一致且archive_source_row_hash等于expected”返回 no-op；active和archive并存、hash不同或只删未归档均报
`LEGACY_OUTBOX_ARCHIVE_CONFLICT`。锁内active重算hash不等于expected报 `LEGACY_OUTBOX_ROW_CHANGED`。迁移在
提交三态 CHECK前独立重算 active/archived count+hash并 readback；事务失败原 active row仍在，禁止半归档。

Scope Coordinator 在 scope 状态真实变化时写 `SOURCE_SCOPE`：

- CURRENT→COMPLETE：重算 scope 内教师/课程，把可证明的空集合从 NULL 转 false/0；
- COMPLETE→STALE/FAILED：把依赖该证明的当前事实降为 SOURCE_MISSING，冲正可逆当前积分，但不撤销历史资格；
- HISTORY 覆盖扩展到 COMPLETE：把覆盖范围内 `WAITING_HISTORY` 收藏观察重新置 PENDING；
- HISTORY→STALE/FAILED：相关观察退回 WAITING_HISTORY；已经有奖励的归因进入
  `AWARDED_PENDING_EVIDENCE` 暂存态且分数不变，待历史恢复后再确认保留或冲正，不能把“证据暂缺”猜成 false。

上述唤醒和冲正都由 v2 Worker 消费同一 Outbox 完成，不允许只改 scope 状态而不刷新下游。

因此“领域投影完成”不等于“积分完成”；两层必须分别有 heartbeat、积压和失败证据。

## 4. 目标物理模型

### 4.1 复用表

| 表 | 目标用途 |
|---|---|
| `dts_ingest_events` | 事件级处理账本；保留路由、问题码和来源位点，不保存敏感原始 payload |
| `dts_ingest_issues` | v2 新增；保存无法形成合法 broker event identity 的脱敏接入失败工作项，供同一 delivery 修复、Case 和 ACK 闭环 |
| `dts_source_row_versions` | v2 新增；保存经过白名单与隐私处理的完整 before/after 版本，支持参与历史和关系历史重建 |
| `dts_source_partition_epochs` | v2 新增；登记每个 broker stream generation/topic/partition 的 active epoch，允许 topic 重建后 offset 复用而不撞旧账本 |
| `dts_broker_epoch_activation_requirements` | v2 新增；冻结每个待激活 broker epoch 必须完成的 source-table GLOBAL scope snapshot manifest，防止单个 scope 提前激活整分区 |
| `dts_source_scope_snapshots` | v2 新增；每个 scope 装载 epoch 的状态、fence、hash 和覆盖证据，区分 active 与 candidate |
| `dts_source_snapshot_rows` | v2 新增；按 snapshot/scope 暂存受保护基线行，校验完成前不进入 active current |
| `dts_source_scope_memberships` | v2 新增；记录 GLOBAL/TEACHER scope 每个 source key 的 active snapshot membership，供替换快照缺行语义 |
| `dts_source_table_publish_generations` | v2 新增；按 region/source_table 串行化 GLOBAL/TEACHER snapshot 发布并防止旧 candidate 覆盖较新 current |
| `dts_source_rows` | 所有白名单业务表的最新行镜像及 tombstone，不再只保存投诉字典 |
| `dts_dirty_keys` | 扩展为以 `source_region + key_type + key_part_1 + key_part_2` 唯一的合并重算队列；DOM/OVS 同 ID 不得互相吞并 |
| `dts_ingest_checkpoints` | 数据库已耐久接收的下一 offset |
| `outbox_events_legacy_archive` | v2 迁移新增；只归档可证明不属于真实待消费工作的旧 CANCELLED/PARKED，保留原审计语义但不冒充 PUBLISHED |
| `dts_projection_read_routes` | v2 新增；单例、版本化地选择稳定教师端视图的 v1 compatibility 或 v2 分支，与 pipeline mode同事务切换 |
| `domain_aggregate_revisions` | v2 新增；为每个规范化 aggregate 提供统一递增 revision 与 Outbox 幂等身份 |
| `lesson_score_component_settlements` | v2 新增；保存逐课非收藏计分组件的 AWARDED/REVERSED 当前生命周期、generation 和流水引用 |
| `score_entry_idempotency_aliases` | v2 新增；把唯一 legacy 不可变流水映射到 canonical 幂等键，不改写 `score_entries` |
| `source_course_complaints` | v2 新增；按源投诉行保存类型化有效性、分类映射与严重度集合，供 L0/指标和最新路由选择 |
| `personalized_trigger_matches` | 规则命中证据、抑制状态与输出关联 |
| `task_assignments` | 教师端与触发中心共用的唯一任务实例当前态 |
| `ops_cases / ops_decisions` | 完课归属纠错等人工决策与审计 |

`personalized_trigger_matches` 在 v2 是“一个稳定证据身份的当前生命周期”，不是每次复命中都 INSERT。
迁移新增 `source_region/source_appoint_id/participation_seq`、`match_revision>=1` 和
`last_transition_at`；稳定 dedupe_key 仍唯一。`MATCHED/MATERIALIZED→SUPPRESSED` 或
`SUPPRESSED→MATCHED/MATERIALIZED` 时更新同一行并把 revision+1，每次转换向 `audit_events` 追加
旧/新状态、证据摘要和来源位置，禁止覆盖审计历史。
同一状态下 approved `plan_evidence_hash`、typed seed列、target_task_code 或 execution variant 的语义变化也
必须 match_revision+1、更新 last_transition_at并追加 audit；MATERIALIZED 保持 MATERIALIZED 且 output link
不变，MATCHED 保持 MATCHED。只改变 evidence JSON 键序、审计时间、source_position/updated_at 时 revision
不变。
所有教师级/教师+标签/教师+分类 assignment 的 `why/evidence_snapshot/display_title` 只冻结首次物化证据，
后续课程、参与、地区或规则 match 只写各自 match/audit，不修改 assignment 不可变字段；读取当前触发
贡献必须聚合 active matches，不能从首次 assignment 快照倒推。

match 另用类型化列冻结投影来源：`materialization_origin=LEGACY_REUSED/CUTOVER_CREATED/V2_LIVE`、
`created_projection_generation>=0`、`serving_projection_generation>=1`（is_serving=true时必填）和 is_serving。
legacy迁移行为 origin=LEGACY_REUSED/created=0；cutover新建为 CUTOVER_CREATED/本次 generation；切换后新建为
V2_LIVE/当前 generation。origin/created不可改；rollback只把 CUTOVER_CREATED/V2_LIVE 的 is_serving=false并清
serving generation，不把 evidence改成 SUPPRESSED；再次 cutover重评仍成立时只写新 serving generation并恢复
true。旧 varchar `projection_epoch='ROLLED_BACK'` 不是目标契约，必须迁为上述类型化列。

“首次”由 Task Planner（SourceWide Worker v2 内部 Outbox 消费阶段，不是另一写方）的确定性 seed 选择器
定义，禁止使用最先到达 Worker、无 `ORDER BY` 的第一行或 INSERT 竞争胜者。为此
`personalized_trigger_matches` 必须新增类型化字段 `match_kind`、`target_task_code`、
`assignment_dedupe_key`、`seed_rule_rank`、`plan_evidence_hash`、`teacher_execution_variant`、
`assignment_dedupe_key_sort_bytes`、生成的 `source_region_rank(dom=0,ovs=1)`、
`source_appoint_id_type=NONE/NUMERIC/TEXT`、生成的
`source_appoint_id_type_rank(NUMERIC=0,TEXT=1,NONE=2)` 及互斥的 numeric/UTF-8 bytes排序列、
`evidence_discriminator_type`、相同 rank规则及互斥排序列、`dedupe_key_sort_bytes`，并对
`(assignment_dedupe_key_sort_bytes,is_serving,match_status,seed_rule_rank,source_region_rank,
source_appoint_id_type_rank,source_appoint_id_numeric,source_appoint_id_sort_bytes,participation_seq,
evidence_discriminator_type_rank,evidence_discriminator_numeric,evidence_discriminator_sort_bytes,
dedupe_key_sort_bytes)` 建领取索引；禁止依赖数据库默认 collation，或在运行时
解析 dedupe_key 或任意 JSON 来补排序字段。

match 集合发生真实语义变化的事务必须锁定
`domain_aggregate_revisions(TASK_PLAN, assignment_dedupe_key)`、revision+1，在同一事务按该键重算
`aggregate_state`，并写唯一事件
`task.materialization.requested.v2:{task_plan_aggregate_id}:{revision}`。TASK_PLAN 的
`aggregate_state` 固定包含：`materializable`、`eligibility_generation`、`eligible_since_at`、
`timezone_used/timezone_source/timezone_verified_at`、`teacher_row_version`、`template_version_id/
template_revision`、`teacher_copy_version_id/teacher_copy_version_number`、active match set hash、blocker 摘要和
plan state hash。状态规则只有以下一种：

- 尚无 assignment 且 `false→true` 时 `eligibility_generation+1`，`eligible_since_at` 取该事务唯一数据库
  `transaction_timestamp()`，同时冻结当时唯一 PUBLISHED 模板、teacher copy 与教师时区证据；教师资料不是
  合法 IANA 时区时固定用 `UTC + SYSTEM_DEFAULT`，不得使用 Worker 主机时区；
- `true→true` 且上述模板/配置/时区依据未变时保留 generation、eligible_since 和全部冻结依据；match 集合
  变化只更新 set/state hash。尚未物化时模板/teacher copy 发布版本发生变化，视为旧 generation 失效并
  开启下一 eligibility generation，重新冻结依据；教师资料普通时区更新不重开已经可物化的 generation；
- `true→false` 保留历史 generation 和已冻结 basis，只清除当前 materializable；没有 assignment 时以后再次
  `false→true` 开新 generation；
- assignment 已存在时永远不重选 seed、模板、截止时点或时区；blocker 恢复只复用原 generation/basis，
  不开新 generation。cutover 关联 LEGACY_COMPAT/FROZEN 时只从 assignment 已冻结字段形成稳定 basis，
  FROZEN 原本没有 copy 的继续为 NULL；aggregate 只记录后续贡献集合。
  assignment 是否存在以及冻结身份只从 `task_assignments.dedupe_key` 唯一行读取，不复制进 aggregate_state，
  避免首次 INSERT 后无 revision 的 plan state/hash 漂移。

`aggregate_state` 必须通过 additionalProperties=false 的 `task_plan_state_v1` schema。字段全集固定为
`protocol_version='task-plan-state-v1',assignment_dedupe_key,teacher_id,target_task_code,materializable,
eligibility_generation,eligible_since_at,timezone_used,timezone_source,timezone_verified_at,teacher_row_version,
template_version_id,template_revision,teacher_copy_version_id,teacher_copy_config_key,teacher_copy_version_number,
active_match_set_hash,blocker_code,blocker_set_hash,plan_state_hash`。这里的 copy 版本号就是
`config_versions.version_number`，不存在另一列“copy revision”。`plan_state_hash` 是对前述除自身外全部字段按该顺序构造的对象做canonical
JSON v1 SHA-256；对象key最终仍按UTF-8 bytes排序，timestamp转UTC微秒，NULL保留JSON null，整数十进制，字符串
不trim。active_match_set_hash必须为当前 serving active集合hash，空集合固定为`SHA-256(canonical [])`。
MATCH 的 materializable=true时generation>=1且时间/时区/teacher/template/copy全部非空、copy key固定
teacher_personalized_copy；已有关联 legacy assignment 时generation=0并按其冻结字段条件可空。true一律
`blocker_code=NONE`且blocker_set_hash固定为空数组hash；false保留最近 generation/basis，只有从未成立且无
legacy assignment 时basis为JSON null，但teacher_id/target_task_code/
active_match_set_hash/blocker_code/blocker_set_hash仍保留。assignment存在与否、assignment ID/
payload hash、plan_state_hash自身、source_position、updated/采集时间均禁止进入hash。数据库生成列/constraint
trigger用唯一 `task_plan_state_hash_v1(state_without_hash)` 重算，不接受调用方自报。

`blocker_set_hash=task_plan_blocker_set_hash_v1(...)` 的原子元素只允许：PENDING_DATA match 写
`{kind=NEGATIVE_LABEL_NAME_MISSING|NEGATIVE_LABEL_VARIANT_CONFLICT,dedupe_key,match_revision,
plan_evidence_hash}`；copy 缺失写 `{kind=TASK_COPY_CONFIG_MISSING,config_key=teacher_personalized_copy}`；copy
冲突写 `{kind=TASK_COPY_CONFIG_CONFLICT,config_key=teacher_personalized_copy,candidate_version_ids}`，候选 ID 按
UTF-8 bytes 排序。元素按 `kind + dedupe_key/config_key + match_revision + plan_evidence_hash/candidate IDs`
排序后做 canonical JSON v1 SHA-256；禁止包含原始学生 ID、文案、采集时间或 source_position。集合为空时固定
空数组 hash。`blocker_code` 是该集合的确定性摘要：空集合且 materializable=true 为 `NONE`；空集合且 false 为
`NO_ACTIVE_MATCH`；非空按 `TASK_COPY_CONFIG_CONFLICT > TASK_COPY_CONFIG_MISSING > NEGATIVE_LABEL_MULTIPLE >
NEGATIVE_LABEL_VARIANT_CONFLICT > NEGATIVE_LABEL_NAME_MISSING` 取值，其中两种负面标签 kind 同时存在时为
`NEGATIVE_LABEL_MULTIPLE`。同一 blocker kind 的 match revision/evidence 或 copy 候选集合变化即使 summary code
不变，也会改变 blocker_set_hash、TASK_PLAN revision/state hash；blocker 清除时必须回到空数组 hash。

`plan_evidence_hash=SHA-256(canonical JSON)` 只对下表每类批准的 plan 字段计算；禁止直接 hash
`evidence_snapshot`。JSON 键顺序、采集/审计时间、source_position、技术 updated_at 不得改变该 hash，真正
影响首次 why/title/variant 的规范化事实变化才改变。`teacher_execution_variant` 枚举为
`NONE/GENERAL/TEACHING_ENVIRONMENT_PHOTO`，不从 evidence JSON 临时解析。

active match set hash 的 canonical 元素固定为
`match.dedupe_key + match_revision + seed_rule_rank + source_region + 全部类型化 seed 排序列 +
plan_evidence_hash + teacher_execution_variant + normalized_active=true`，按与 seed 选择器相同的 typed 元组
排序后做 canonical JSON SHA-256。`MATCHED` 与 `MATERIALIZED` 在 hash 中一律规范化为同一个 active 值；
`output_id/materialized_at/match_status/updated_at` 不进入 hash。SUPPRESSED 会移出集合，恢复时以递增后的
match_revision 重新进入，所以 Planner 把 MATCHED 关联为 MATERIALIZED 不会让 aggregate hash 自行过期。

上述事务的 Outbox payload 必须包含 assignment key/hash、aggregate revision、eligibility generation、
`eligible_since_at`、完整时区/模板 revision 与 copy version number、plan state hash 和 changed match keys，不含教师文案或
敏感证据。Planner 只领取该 event_type 的 PENDING Outbox，并对
`task_assignments.dedupe_key` 取得事务 advisory lock，再锁 TASK_PLAN aggregate：事件 revision 小于当前
revision 时只以 `superseded_by_revision` 审计后发布，不用旧状态建任务；大于当前 revision 为
`TASK_PLAN_REVISION_CONFLICT`。revision 相等时必须先按 dedupe_key 锁并读取 assignment：若已存在，校验
其 dedupe/task_code/kind/creator、模板复合 FK 和 frozen seed payload hash 可由 assignment 自身冻结字段
确定性重建，随后只把本次新出现的 active match 关联到
同一 assignment 并从 MATCHED 改为 MATERIALIZED；不执行 seed 选择、不改 assignment 冻结字段，最后发布
event。同键 suppressed match 保留历史 output 关联但不作为当前贡献。只有 assignment 确认不存在时，
materializable=false 才确定性 no-op 发布；不存在且 materializable=true 时，才在同一一致性快照读取该键全部
`is_serving=true AND match_status IN ('MATCHED','MATERIALIZED')` 的 active match，校验 set hash 后按下列
元组逐项 ASC、NULLS LAST 取唯一 seed：

```text
(seed_rule_rank,
 source_region_rank,                 -- dom=0, ovs=1
 source_appoint_id_type_rank,        -- NUMERIC=0,TEXT=1,NONE=2；NONE 等价 NULLS LAST
 canonical source_appoint_id numeric/text sort value,
 participation_seq,
 evidence_discriminator_type_rank,
canonical evidence_discriminator numeric/text sort value, -- 无则 NONE/NULLS LAST
 match.dedupe_key UTF-8 bytes)
```

typed seed 列不由各 writer 自选，固定映射如下；NUMERIC/TEXT 取源表该 ID 的固有类型，不能把数字字符串
临时改成 TEXT：

| match kind | source_appoint_id / participation_seq | evidence_discriminator |
|---|---|---|
| `ABSENCE_P_REL_MEMO/ABSENCE_P_REL_ATTENDANCE` | 对应课程 typed ID / 唯一 t_absent seq，均必填 | absent canonical source ID；variant=`GENERAL` |
| `COMPLAINT_ATTENDANCE` | 对应课程 typed ID / 当前 completion seq，均必填 | 最新路由所选 source_complaint_id；variant=`GENERAL` |
| `NEGATIVE_LABEL_COURSE` | 对应课程 typed ID / 当前 completion seq，均必填 | label_id；variant=`GENERAL` 或 `TEACHING_ENVIRONMENT_PHOTO`，由精确 copy 映射决定 |
| `GENERAL_COMPLAINT` | 对应课程 typed ID / 当前 completion seq，均必填 | 最新路由所选 source_complaint_id；variant=`GENERAL` |
| `BLACKLIST_THRESHOLD` | `NONE` / NULL | `NONE`；variant=`GENERAL`；student-token 集合只进 plan_evidence_hash，不进 seed/Outbox 明文 |
| `NEGATIVE_LABEL_NAME_MISSING`（PENDING_DATA） | `NONE` / NULL | label_id；variant=`NONE`；不进入 active Planner 索引 |
| `NEGATIVE_LABEL_VARIANT_CONFLICT`（PENDING_DATA） | `NONE` / NULL | label_id；variant=`NONE`；不进入 active Planner 索引 |

`match_kind` 使用上表稳定枚举，不解析 trigger_code/dedupe key；`target_task_code` 映射固定为：两个 ABSENCE
分别 P-REL-MEMO/P-REL-ATTENDANCE，COMPLAINT_ATTENDANCE→P-REL-ATTENDANCE，NEGATIVE 两种正常/两种
PENDING→P-FB-NEGATIVE，GENERAL_COMPLAINT→P-FB-COMPLAINT，BLACKLIST_THRESHOLD→P-FB-BLACKLIST。
两个 NEGATIVE PENDING 仅来自 DOM，因此 source_region 固定 dom/rank=0；不存在跨区任选问题。

各 kind 的 canonical plan evidence v1 分别为：absence=`rule_code,region,typed appoint,seq,typed absent id,
reason_type`；complaint attendance/general=`rule_code,region,typed appoint,seq,typed complaint id,三级分类 ID,
complaint rule_id+SHA,severity/route,copy lookup key`；negative course=`region,typed appoint,seq,label_id,
当前 label_name,copy lookup key,variant`；blacklist=`region,threshold,current_count,sorted student-token-set hash`；
negative pending=`teacher_id,label_id,sorted contributing course-key hash,pending reason`。copy/template version另在
TASK_PLAN basis 中冻结，不重复塞进每个 match hash。数据库按 match_kind 对上述 type/value/participation/
variant 组合建 CHECK；课程型 match 不允许把缺失
appoint/evidence 降为 NONE，黑名单也不允许任选某个 student_token 当首次 seed。其他新增 match kind 必须先
扩展本表和版本化 CHECK，不能复用“最相近”的映射。

canonical ID 类型和比较规则沿用 §5。`seed_rule_rank` 固定为：P-REL-MEMO 的 absence=10；
P-REL-ATTENDANCE 的 absence=10、投诉出席=20；P-FB-NEGATIVE、P-FB-COMPLAINT、
P-FB-BLACKLIST 各自唯一 match 类型均为 10。match 与 TASK_PLAN Outbox 同事务提交后，Planner 才异步领取；
同一 Planner 快照内的多个首次 active match 必须由上式决胜。Planner 快照之后真正新增的 match 属于后续
贡献，不重选已存在 assignment。

新建个性化 assignment 必须冻结
`materialization_seed_kind=MATCH`、`materialization_seed_key=match.dedupe_key`、
`materialization_seed_revision=match_revision` 和
`materialization_seed_payload_hash=SHA-256(canonical JSON(why,display_title,evidence_snapshot,
template_version_id,template_revision,teacher_execution_variant,eligibility_generation,eligible_since_at,due_at,
timezone_used,timezone_source,timezone_verified_at,teacher_copy_version_id,teacher_copy_config_key))`；
assignment 必须用不可变类型化列同时保存 `materialization_template_revision`、
`materialization_plan_revision`、`teacher_execution_variant`、`eligibility_generation`、
`teacher_copy_version_id`、固定小写 `teacher_copy_config_key=teacher_personalized_copy` 和 seed match 的
`plan_evidence_hash`；MATCH 时 generation/template revision>=1、copy version和其余列全部非空，并可仅从
assignment 冻结列重建 payload hash，不读取当前 TASK_PLAN、active copy或 current template revision。
这些字段与 why/title/evidence 同属触发中心不可变字段。INSERT 与 seed match 关联、其他 active match 的
output 关联在同一事务完成；assignment 新增不可变 `eligible_since_at` 保存 plan eligibility 起点，
`assigned_at` 仍取任务实际 INSERT 并对教师端可读的数据库时间，`due_at` 按 eligible_since_at 与模板固定
持续小时数（P-REL 为 48 小时，P-FB 为 72 小时）作绝对时长相加，不按本地日历或实际消费时间重算。
MATCH assignment 只能通过受限 `materialize_task_plan_v2(event_id)` 创建，Planner 角色不得直接 INSERT。
函数在既定 catalog→TASK_PLAN→assignment→match 锁序内校验 event revision/hash 等于当前请求快照、
materialization_plan_revision 等于该 revision、template revision/current PUBLISHED、copy version、
generation/eligibility/timezone/due 等于 plan、seed key/revision/evidence hash/variant/target task 等于 canonical
seed match，并在 INSERT 前重算 payload hash。数据库防绕过 Trigger再次校验复合 FK和冻结字段；任一伪造
返回 `TASK_PLAN_MATERIALIZATION_MISMATCH` 且不写半行。固定 G 初始化、LEGACY_FROZEN迁移与compatibility
窗口新建使用各自独立受限函数，
不能借用 MATCH 路径。
`config_versions` 必须有 `UNIQUE(version_id,config_key)`；assignment以
`(teacher_copy_version_id,teacher_copy_config_key)`复合FK防止跨配置域引用。MATCH创建时所指版本必须是
当前唯一PUBLISHED且schema/hash合法；之后换版可继续引用不可变RETIRED。LEGACY_FROZEN/fixed两列均为空。
首次创建 INSERT 的唯一键冲突只表示两个“assignment 尚不存在”的创建者发生竞争；锁内必须读取既有
assignment 并校验它与本 event 计算出的同一 seed/hash 完全一致，随后按已存在分支关联其他 active match。
若该行在 Planner 加锁前已经存在，则不得拿当前 canonical 最小 match 与历史 seed 比较，也不得因后来出现
更小 seed 报冲突；只校验 assignment 自身冻结身份/hash，禁止覆盖。Planner INSERT、match 关联和 event
PUBLISHED 同事务完成，但不修改 same-revision aggregate_state/plan-state hash，也不自造下一 revision/event；
因此原 Outbox payload、最终 shadow 与重放始终引用同一个不可变请求快照。
Planner 持有 Outbox 行锁完成上述写入并把事件置 PUBLISHED 后同事务提交；进程崩溃整笔回滚，事件仍
PENDING。普通失败按统一 Outbox 退避，8 次后 DEAD_LETTER 并按 §6.6 建技术 Case；修复后重放原 event_id，
不得新造 plan event。Planner 自己把 match 从 MATCHED 关联为 MATERIALIZED 不再次递增 TASK_PLAN revision；
下一 plan 只由来源/规则导致的证据集合变化，或 assignment 尚未物化时模板/teacher-copy plan basis 换版触发。
v2 match 不使用无类型 `output_id` 作权威，改为互斥 typed FK
`task_assignment_id/ops_case_id/notification_id`；output_id 仅为生成兼容列。MATERIALIZED 必须恰有对应 FK
和 materialized_at，MATCHED/PENDING_DATA/FAILED 全空，SUPPRESSED 可保留曾物化 link但不得改 ID。
TEACHER_TASK deferred trigger 校验 linked assignment 的 dedupe_key/teacher_id/task_code 与 match 的
assignment_dedupe_key/teacher/target_task_code 一致；Planner 的 assignment INSERT、全部 link 和 event 发布
仍在同一事务，错表/错教师/孤儿 link 直接拒绝。
shadow/cutover 的 `TASK_PLAN` 必须包含同一 seed 四元组、eligibility generation、时间/时区/模板依据和
payload hash；cutover 首次补缺时的 `eligible_since_at` 固定为该最终 run 的 `evaluation_as_of`，时区证据取
run revision vector 中冻结的教师版本。同一输入集合重排、消费延迟、教师后来换时区、并发领取或重放必须
产生相同结果。迁移前已存在的 assignment 不重新解释历史 match，写
`materialization_seed_kind=LEGACY_FROZEN`、`materialization_seed_key=legacy-assignment:{assignment_id}`、
revision=0、`materialization_plan_revision=0`、`eligibility_generation=0`、`teacher_execution_variant=LEGACY_FROZEN`、
`teacher_copy_version_id=NULL,teacher_copy_config_key=NULL`、`plan_evidence_hash=SHA-256(canonical legacy evidence)`，并把当时引用模板的
revision 冻结到 materialization_template_revision；`eligible_since_at` 统一回填既有 `assigned_at`，evidence 标
`eligibility_time_source=LEGACY_ASSIGNED_AT`，原 due_at/时区证据原样保留且不按 48/72 小时重算，再对这组
既有冻结 payload 计算 hash；以后同样不得重选。
expand后、cutover前由v1语义compatibility writer首次新建的个性化任务不得冒充历史回填，固定写
`materialization_seed_kind=LEGACY_COMPAT`、seed_key=`legacy-compat:{assignment_id}`、seed_revision=0、
plan_revision=0、eligibility_generation=0；copy version/config key、template revision、英文why/title、
execution variant、plan evidence、当前教师时区与eligible/due必须完整冻结并进入payload hash。eligible_since_at
等于该受限INSERT的assigned_at，due仍按48/72小时；只能经 `create_legacy_compat_assignment_v2(...)` 创建。
LEGACY_COMPAT不进入v2 TASK_PLAN revision，但cutover把既有assignment原样保留并只关联当时active matches；
不得重选/改写其seed/copy/title。LEGACY_FROZEN仍仅指expand前存量且copy两列为空。

函数签名固定为
`create_legacy_compat_assignment_v2(assignment_dedupe_key text,teacher_id text,task_code text,
expected_legacy_compat_match_set_hash char(64)) RETURNS (assignment_id text,outcome text,payload_hash char(64))`。
仅 compatibility writer 数据库角色可调用。函数在既定锁序内先检查幂等记录：同request已有成功响应时，即使
mode已切换仍返回REPLAYED；没有成功幂等记录的新请求只允许
`dts_pipeline_control.mode IN (V1_COMPAT_DUAL_CAPTURE,ROLLED_BACK)`，V2_PRIMARY返回
`LEGACY_COMPAT_WINDOW_CLOSED`。ROLLED_BACK期间v1 Worker仍是生产owner，首次命中必须继续经本函数创建
LEGACY_COMPAT，禁止漏任务或直插；再次cutover原样保留。四参数request hash
字段顺序固定为 `protocol_version='legacy-compat-v2',assignment_dedupe_key,teacher_id,task_code,
expected_legacy_compat_match_set_hash`，幂等记录 key 固定为
`create-legacy-compat-assignment:v2:{SHA-256(UTF8 assignment_dedupe_key)}` 且永不过期。新建 assignment_id
固定为 `TAS-LC-` 加 `SHA-256(UTF8('legacy-compat-assignment:v2\u0000'||assignment_dedupe_key))` 的前 40 个
小写 hex；禁止数据库随机默认值参与身份或 seed。

事务锁序固定为 cutover shared mutex → 把
`CONFIG:teacher_personalized_copy` 与 `TEMPLATE:{task_code}` 组成完整 catalog lock-key 集合并按 UTF-8 bytes
一次性升序取得 shared advisory locks（因此当前两个 key 是 CONFIG 在前、TEMPLATE 在后）→
assignment_dedupe_key advisory lock →
`idempotency_records` 行 → teacher 行 → active match 行按 canonical seed 顺序。锁内校验 task_code 是五个 P
编码之一、dedupe_key 符合 §3.2 对应语法且内含同一 teacher/task、教师存在，并读取独立的
`legacy_compat_candidate`集合。谓词精确为`materialization_origin=LEGACY_REUSED AND
created_projection_generation=0 AND is_serving=false AND serving_projection_generation IS NULL AND
output_type=TEACHER_TASK AND match_status IN ('MATCHED','MATERIALIZED')`，并要求assignment_dedupe_key/teacher/
target_task_code与请求一致；PENDING_DATA/SUPPRESSED/FAILED及v2 serving match全部排除。MATCHED必须无assignment
link；MATERIALIZED只可链接同dedupe既有assignment，无既有assignment却出现MATERIALIZED固定报冲突。

`legacy_compat_match_set_hash_v1` 的canonical元素为
`protocol,match.dedupe_key,match_revision,seed_rule_rank,source_region,全部typed seed排序列,
plan_evidence_hash,teacher_execution_variant,materialization_origin,created_projection_generation,
normalized_candidate=true`，protocol固定`legacy-compat-match-set-v1`；按同typed seed元组排序后做canonical JSON
v1 SHA-256。match_status/link/time/is_serving不进入元素，因为已由谓词归一化；重算hash必须等于expected。
再读取锁保护下唯一 PUBLISHED template/copy，按与 v2 Planner 相同的 canonical seed、英文
why/title/variant/evidence 规则构造 payload，当前教师时区证据必须完整；缺依赖报
`LEGACY_COMPAT_DEPENDENCY_INCOMPLETE`，hash漂移报 `LEGACY_COMPAT_MATCH_SET_CHANGED`，均不写半行。

若幂等记录已存在，同 request hash 必须校验 resource assignment 仍存在且 frozen payload hash 等于保存响应，
然后原样返回 `REPLAYED`，不重读当前 catalog/match/time；同 key 异 request hash 报
`LEGACY_COMPAT_REQUEST_CONFLICT`。若幂等记录尚无但 dedupe assignment 已存在，只有 teacher_id/task_code 精确
一致时才把当时 active matches 关联到该既有行、写响应 `EXISTING_ASSIGNMENT`；不改任何冻结列，身份不符报
`LEGACY_COMPAT_ASSIGNMENT_CONFLICT`。若均不存在，使用上述确定 ID 原子写 LEGACY_COMPAT assignment、全部
active match links、audit 和幂等响应，outcome=`CREATED`；ID 已被另一 dedupe 占用同样报 conflict。首次
assigned_at/eligible_since_at 都取同一 `transaction_timestamp()`，due 按模板 48/72 小时，payload hash 包含
确定 ID、template revision/copy version/config key、英文文案、variant、evidence、时区和时间。事务提交后响应丢失时，
同四参数调用只走 REPLAYED；禁止生成新 ID/seed 或用当前更小 match 重解释已建任务。后续新增 match 由正常
link 路径关联，不再次调用创建函数。

cutover 不绕过 MATCH 创建约束。final shadow 的 TRIGGER_MATCH/TASK_PLAN/OUTBOX_COVERAGE 必须为每个需
首次物化的 assignment key 冻结 `plan_action=EXISTING_EVENT/CUTOVER_PLANNED`、当前 production plan
revision/state/hash、全部 match rows和预期 assignment payload。二选一规则固定为：若最终 match/plan state 与
production 当前 state/hash 完全相同，且当前 revision 已有 identity/payload 合法的 PENDING TASK_PLAN event，
必须选 `EXISTING_EVENT`，沿用该 event/revision，禁止无语义变化自增；只有 production 当前 state/hash 与最终
结果不同，或该键尚无可沿用的合法 current event/aggregate 时，才选 `CUTOVER_PLANNED`，冻结
`base_plan_revision`、`planned_revision=base+1` 和 deterministic event identity/payload，并把旧 PENDING event 的
coverage 标 `SUPERSEDED`。同态 current event 已是 PUBLISHED 时，只有 assignment/link 与最终结果完全一致才
视为已完成；否则是 `CUTOVER_TASK_PLAN_STATE_CONFLICT`，不得补造 revision 掩盖半物化。
production aggregate 不存在时 canonical base 固定为 0，函数先取得 assignment-key advisory lock并再次确认
无行，planned 固定为 1；`planned_event_id=task.materialization.requested.v2:{task_plan_aggregate_id}:{planned}`，
aggregate 表本身仍只允许持久化 revision>=1。禁止把不存在解释为 NULL、当前 1 或另造 migration revision。

第 6 步只调用受限 `materialize_cutover_task_plan_v2(run_id,result_key_hash)`。`EXISTING_EVENT` 分支锁定原
PENDING event，调用与 `materialize_task_plan_v2` 共用的内部 checker 原子物化/关联后按精确 coverage settle，
不改 aggregate revision。`CUTOVER_PLANNED` 的 APPLY 分支要求锁内 aggregate=current base 且 planned event
不存在，在同一事务写/更新 production matches、以 planned revision UPSERT TASK_PLAN aggregate、插入 PENDING
event，再调用同一 checker 物化并 settle。提交成功但调用方丢失响应后的同 run 重试走唯一
ALREADY_APPLIED 分支：只在 aggregate=current planned、event identity/payload 完全一致、event 已 PUBLISHED 且
`settled_by_run_id=同 run`、match/assignment/link/hash 全部一致时返回 no-op；半应用、异 run 或异 payload占用
一律 `CUTOVER_INPUT_CHANGED`。既有 assignment 只关联 match、不重写冻结字段；新任务 eligible/due 用
evaluation_as_of，assigned_at 用成功 INSERT 的 transaction_timestamp()。任何一步失败连同
match/aggregate/event/assignment/link 全回滚。planned event 必须纳入本次 OUTBOX_COVERAGE，不能事后另补。

`task_templates` 对 `(template_id,template_version)` 唯一，并对
`(template_id) WHERE status='PUBLISHED'` 建部分唯一索引。状态机固定为：DRAFT 可按 revision 乐观锁编辑；
发布/换版只能调用受限 `publish_task_template_v2(...)`。无旧 current 时函数在同一事务执行
`DRAFT→PUBLISHED`；有旧 current 时执行“旧 PUBLISHED→RETIRED + 新 DRAFT→PUBLISHED + audit + TASK_PLAN
fan-out”。函数在首次发布和换版时都必须校验精确分值映射：
`G01..G09=3,2,2,3,3,4,3,5,5`，五个 `P-*` 均为 `0`；不只校验合计分，异值拒绝整笔发布。
RETIRED 是终态，current PUBLISHED 不得单独退役或删除，普通角色没有 status 发布写权限；直接
UPDATE 发布、复活 RETIRED、绕过函数单独退役均由 Trigger 拒绝。发布函数必须取得 cutover shared mutex 和该
template catalog 的 exclusive advisory lock；禁止原地修改 PUBLISHED 的 identity、payload、积分、截止规则或执行路由。既有 assignment 继续按
其 `template_version_id` 读取 RETIRED 历史模板，只有新建 assignment 才要求引用当前唯一 PUBLISHED。
最终 shadow 的 active template vector 必须包含当前 14 个模板的 row_id/version/revision/payload_hash；缺失、
重复发布或锁内换版都以 `TASK_TEMPLATE_CATALOG_CONFLICT/CUTOVER_INPUT_CHANGED` 失败关闭。
模板或 `teacher_personalized_copy` 发布事务必须利用
`personalized_trigger_matches(assignment_dedupe_key,...)` 索引找出“有 active match 且尚无 assignment”的
受影响键。单一模板换版只选择 `target_task_code=本 template_id` 的键；teacher copy 换版选择全部此类键，
因为每个新 MATCH assignment 都冻结 copy version。禁止模板 A 换版重开模板 B 的 eligibility generation。
copy 换版必须先用新版本重评这些键下未物化 match 的英文标题、execution variant、copy evidence和
plan_evidence_hash；语义变化时原match revision+1并审计。P-FB-NEGATIVE 还要从该键完整当前标签集合重算
name-missing/variant-conflict blocker，不能保留旧copy算出的照片/通用流程。全部match重评完成后，每个
assignment key只递增一次TASK_PLAN revision。已有assignment及其冻结match内容不参与本次重写。
受影响键按 assignment_dedupe_key canonical 顺序逐键调用唯一 TASK_PLAN 重算命令，递增 revision、失效
旧 eligibility generation 并写新 plan Outbox；发布状态、所有 plan revision/event 和 publication audit 同一
事务提交，任一失败整笔回滚。旧 plan event 之后只会按 superseded 规则 no-op，不能用已 RETIRED 模板建
新任务。若受影响键数量超过已压测单事务上限，本次发布失败关闭并要求另立版本化 staging 方案，不能
分批暴露半套模板/copy。
所有 match→TASK_PLAN 重算和 Planner 在读取模板/copy 前必须先取得对应 catalog shared advisory lock；
teacher copy 使用全局 catalog key，模板使用 template_id key。任何模板/copy 发布在事务开始前先计算完整
目标/依赖 catalog lock 集合，再按 canonical key UTF-8 顺序一次性取得“本次发布目标=exclusive、其余
依赖=shared”的锁，禁止扫描过程中追加锁；模板与 copy 并发发布因此不会交叉反序。锁内换版并扫描
fan-out，所以扫描后不可能再提交基于旧版本的新 current plan。统一锁顺序为
cutover mutex → catalog lock → TASK_PLAN aggregate/assignment key → match/assignment 行锁，禁止反序。
新教师 G01–G09 固定任务初始化也在一个事务预取九个 template catalog shared locks（按 key bytes 排序），
再以同一 snapshot 校验恰好九个 current PUBLISHED row并插入；发布不能在查询与 INSERT 之间把模板退役。
模板表额外提供 `UNIQUE(row_id,template_id)`，assignment 以
`(template_version_id,task_code) REFERENCES task_templates(row_id,template_id)` 复合外键冻结语义；禁止
`task_code=G01` 却引用 G02/P 模板。新建 assignment 还必须在 catalog shared lock 内校验所引 row 是该
task_code 当前唯一 PUBLISHED；既有 assignment 的状态更新只校验冻结复合 FK 存在，允许继续读取 RETIRED
历史 row。

新建 `materialization_seed_kind=MATCH` 的个性化任务必须同时具有非空
`eligible_since_at/due_at/timezone_used/timezone_source/timezone_verified_at`，且 due_at 精确等于
eligible_since_at 加冻结模板的 P-REL 48 小时或 P-FB 72 小时；该等式由创建命令和 deferred constraint
trigger 共同校验。LEGACY_FROZEN 只要求 eligible_since_at 回填原 assigned_at，原 due/timezone 可全空或按
原状成组保留；固定任务 eligible_since_at 和四个 seed 字段为空。

`ops_cases` 为 v2 输出新增唯一、可空 `source_ref`；严重投诉写 canonical case key，完课纠错写
`course-completion-correction:{source_region}:{source_appoint_id}`。同时为完课纠错新增
`source_region/source_appoint_id`，并把 `teacher_id` 改为条件可空：
`case_type='COURSE_COMPLETION_CORRECTION'` 时允许因首次 end 缺教师而为空，§6.6 五类技术 Case 只在能
精确定位时填写；教师业务投诉/提醒类 Case 仍强制非空。
数据库对 `(case_type,source_region,source_appoint_id)` 建部分唯一约束；课程教师补齐后只更新同一 Case
payload/revision，不另建 Case。`ops_decisions` 继续以 decision_id 幂等关联该 Case，并新增
`downstream_projection_status=PENDING/PUBLISHED/DEAD_LETTER` 与 `projection_event_ids`；决定事实提交和下游
输出刷新必须分开表述。

`domain_aggregate_revisions` 的 PK 为 `(aggregate_type,aggregate_id)`，保存 canonical key、`revision>=1`、
last source revision、审计 source position、`aggregate_state jsonb NOT NULL DEFAULT '{}'` 和 updated_at。
`aggregate_state` 只允许各 aggregate 的固定 schema；TASK_PLAN 必须通过上文 plan-state CHECK，不能保存
原始学生 ID 或教师文案。key/revision 规则固定为：

| aggregate_type | canonical key |
|---|---|
| COURSE | `{source_region,source_appoint_id}` |
| PARTICIPATION | `{source_region,source_appoint_id,participation_seq}` |
| TEACHER | `{source_region,teacher_id}` |
| TEACHER_STUDENT | `{source_region,teacher_id,student_token}` |
| LABEL | `{source_region,label_id}` |
| COMPLAINT_CATEGORY | `{source_region:"dom",category_id}`；DOM/OVS 投诉共用 |
| COMPLETION_CONFLICT | `{source_region,source_appoint_id}` |
| SOURCE_SCOPE | `{source_region,source_table,scope_kind,scope_level,scope_key}` |
| TASK_PLAN | `{assignment_dedupe_key}`；只由 SourceWide Worker 的统一重算命令在 match 集合或未物化 plan basis（模板/copy）语义变化时递增 |

Domain Projector/Scope Coordinator/SourceWide Worker 分别只写上表授权的 aggregate；规范化语义真实变化才
revision+1，并用同一 revision 写 Outbox。数据库 aggregate_type CHECK 必须包含上表全部九类；其中
`source_wide.changed.v2` 只允许前八类，TASK_PLAN 只能产生 `task.materialization.requested.v2`。这样
label/source scope 等没有 row_version 的表也不需要自行发明事件版本。

`dts_source_row_versions` 至少包含：`source_region/source_partition_epoch_id/topic/partition_id/offset_value` 复合主键、
`version_kind=CDC/BASELINE/SNAPSHOT_DIFF`、`source_table/source_key`、operation、完整 `before_row/after_row`、
source_timestamp、record_id、source_position、`source_partition_epoch_id`、`source_row_revision`、
snapshot_id/as_of、covered-through fence offset 向量和 created_at。
CDC 使用真实 topic/partition/offset；BASELINE 使用保留 topic
`__baseline__:{snapshot_id}:{source_table}`、partition=0，并按 canonical source_key 排序生成从 1 开始的
确定 offset，operation=`BASELINE`。同一 snapshot/table/key 另有唯一约束，重跑快照不得重复版本。

所有目标 v2 字段名中的 `source_position` 都不是来源透传字符串，而是服务根据已校验 envelope 生成的
结构化 JSONB `dts_source_position_v1`：

```json
{
  "v": 1,
  "source_timestamp": "UTC RFC3339 microseconds or null",
  "record_id_type": "none|numeric|text",
  "record_id": "canonical string or null",
  "source_partition_epoch_id": "non-empty stable epoch id",
  "topic": "non-empty UTF-8",
  "partition_id": 0,
  "offset_value": 0
}
```

topic 配置固定 record_id_type；numeric 用 PostgreSQL 任意精度 numeric 比较，不按字符串比较，text 按
UTF-8 字节序比较。统一不可变数据库函数 `dts_source_position_cmp(a,b)` 逐项 ASC 比较：
`source_timestamp NULLS FIRST → record_id_type(none<numeric<text) → 对应 typed record_id NULLS FIRST →
topic UTF-8 bytes → partition_id integer → offset_value bigint`；比较前要求 epoch_id 相同，否则拒绝；返回
-1/0/1，“最新/更晚”固定为 cmp>0。该函数只用于真实 CDC envelope 的诊断/同 epoch 校验，禁止拿它比较 BASELINE/SNAPSHOT_DIFF 与 CDC：
source_timestamp 不是跨 fence 因果顺序。

跨 version_kind、snapshot fence 和源时钟回退的唯一业务应用顺序固定为每个
`(source_region,source_table,source_key)` 的 `source_row_revision`：接入/发布事务先锁
`dts_source_rows` 行，任何真正推进 source current 的 CDC 或 SNAPSHOT_DIFF 都取当前 revision+1，并与版本
INSERT/current UPDATE 同事务；BASELINE 只作证据，revision=NULL。数据库对
`(source_region,source_table,source_key,source_row_revision)` 建条件唯一约束。typed current/明细复制产生它的
source_row_revision；同 canonical 来源 ID 的修订、所有最新选择器末位、参与状态机推进、Case
“更晚/不晚于”和 expected 校验一律比较整数 revision，不比较 source_position。课程同时保留 position
JSONB作审计，但正式字段新增 `completion_source_revision/last_applied_source_revision/
conflict_resolved_against_revision/expected_source_revision`。

目标 `dts_source_rows` 物理 current 行必须显式保存 bigint `source_row_revision`，以及产生它的
`last_source_partition_epoch_id/topic/partition/offset` 复合 FK、`last_version_kind=CDC/SNAPSHOT_DIFF`、
JSONB source_position、typed record ID、可空 timestamptz source_timestamp 和 canonical payload hash。
现有 `row_version` 只作本地乐观锁，不能复用为 source_row_revision；真正 current 变化两者各自+1，
EPOCH_REPLAY/no-op/BASELINE 均不改 current。详细 ALTER 字段以数据库结构文档 §3.3.4 为准。

`dts_source_partition_epochs` 以 `(source_region,source_partition_epoch_id,topic,partition_id)` 为 PK，
`epoch_kind=BROKER/SNAPSHOT_BASELINE/SNAPSHOT_DIFF`。BROKER 行必须保存已验证
`stream_generation_id + epoch_opening_id`，epoch id 固定为
`epoch:v1:{SHA-256(canonical JSON(region,topic,partition,stream_generation_id,epoch_opening_id))}`；受控 reset
必须取得新的 control-plane epoch_opening_id，同一进程普通重启继续复用 ACTIVE epoch。BROKER 状态为
BARRIER_PENDING/ACTIVE/SUPERSEDED，保存 start offset、reset/recreate reason、`epoch_sequence>=1`、条件可空
`predecessor_epoch_id`、`v2_epoch_bootstrap_floor`、`activation_mode=H0_BOOTSTRAP/SNAPSHOT_MANIFEST`、条件可空
`activation_manifest_hash` 和生命周期；同一 `(region,topic,partition,epoch_sequence)` 唯一，
同一 `(region,topic,partition)` 最多一个 ACTIVE。全部 configured region/topic/partition 的首个 v2 epoch
只能由一次整向量函数 `bootstrap_initial_broker_epoch_v2(bootstrap_run_id,consumer_group,h0_vector,
expected_vector_hash)` 创建；不能逐 partition提交。函数按 canonical route顺序锁定完整配置集合，要求整个集合
无任何 BROKER epoch/current checkpoint，pipeline control尚不存在，h0_vector条目与route集合一一相等，且每项
`stream_generation_id + epoch_opening_id + H0 next_offset` 都已由 connector/control-plane回读验证。它在一个事务
内为每项写 `epoch_sequence=1,status=ACTIVE,predecessor=NULL,activation_mode=H0_BOOTSTRAP,
activation_manifest_hash=NULL,v2_epoch_bootstrap_floor=start_offset=H0 next_offset` 和唯一 current checkpoint，
最后写完整 typed H0 vector/hash、唯一 pipeline control与一条bootstrap audit；响应丢失时只按相同
bootstrap_run_id+consumer_group+完整vector/hash识别为 no-op。任一先出现的 epoch/checkpoint/control H0、
route漏/多项、未验证 stream identity、不相同 offset 或部分状态固定报 `INITIAL_EPOCH_BOOTSTRAP_CONFLICT`；
不得手工把 BARRIER_PENDING 改 ACTIVE。H0 的 dominance 仅按同 epoch next_offset 判断，这个特例不适用于任何
后继 epoch。任何 reset/repartition/recreate 后继都必须 sequence=前驱+1、精确回指前一个 ACTIVE、先进入
BARRIER_PENDING并把相关 scope置 STALE；无一类 reset可立即取得 dominance。同一 stream_generation 的 reset
继承该 generation 的 bootstrap floor，start offset低于 floor 固定报 `BROKER_RESET_BELOW_BOOTSTRAP_FLOOR`
且不消费低于 floor 的 delivery，必须把新订阅起点推进到 floor或更高，不能重放没有 v2 hash 的 legacy offset；已验证为新的
stream_generation/topic recreate 时不得继承旧数值 floor，而以新 generation 的受控 start offset建立新 floor，
旧 ACTIVE→SUPERSEDED、新 epoch先为 BARRIER_PENDING。BARRIER_PENDING Ingestor 仍可按该 epoch identity在同一
事务写 ingest ledger、CDC row version/current/dirty和该 epoch checkpoint，提交后 ACK；scope 始终 STALE，
不得由空集合推导 false/0，且该 checkpoint在 dominance/cutover readiness 中不可支配前驱。这样 snapshot
candidate 可从其 fence持续重放该 epoch CDC，不另建第二 staging ledger/checkpoint。epoch 创建时同时冻结
`dts_broker_epoch_activation_requirements` manifest。精确矩阵固定为：该 topic/partition 当前路由到的
每个有效业务白名单表都必须有 `GLOBAL/CURRENT`；`dom_appoint`、`ovs_appoint`、
`dom_teacher_class_schedule`、`dom_teacher_favorite`、`ovs_teacher_favorite`、
`dom_teacher_blacklist`、`ovs_teacher_blacklist` 若路由到该 partition，还必须各有
`GLOBAL/HISTORY`。其他白名单表不自动增加 HISTORY requirement；新增/变更表路由必须先修改这一
版本化矩阵再部署。TEACHER scopes保持 STALE并独立恢复，不作为 lineage激活条件。
若该 route 只包含退役控制表或确认无有效业务表，manifest 不能留空，必须写唯一哨兵
`source_table='__route_empty__',scope_kind='ROUTE_EMPTY',scope_level='GLOBAL',scope_key='*'` requirement，
记录冻结的 route_config_version/hash，由激活函数重算确认仍为空后才满足，
防止把“忘了生成 requirement”当成空路由。
SOURCE_SCOPE项保存 scope复合键、required epoch/next-offset barrier和非空 satisfying_snapshot_id；
ROUTE_EMPTY项的 snapshot_id/required barrier固定为空，只按哨兵身份与冻结route hash满足。只有全部
SOURCE_SCOPE项各自指向 COMPLETE active snapshot、snapshot fence包含该 epoch且checkpoint next_offset达到
required barrier，且全部ROUTE_EMPTY项回读仍为空，
count/hash独立重算等于 epoch冻结 manifest hash，`activate_broker_epoch_v2` 才可在同一事务把新 epoch→ACTIVE
并取得 lineage dominance；不能用一个表/一个 scope 的 COMPLETE提前激活整 partition。无需再应用一遍已耐久
CDC。激活前/后崩溃分别按
BARRIER_PENDING checkpoint或 ACTIVE 同一 checkpoint续读，event幂等键不变、不双 ACK。因此旧 H0=1000、
新 generation从0开始是合法的 0 floor，但未过 barrier前不能用于最终切换。

BASELINE/DIFF 行必须保存 snapshot_id、source_table，stream_generation/opening 均为空，epoch id 分别为
`snapshot-epoch:v1:{SHA-256(canonical JSON(region,kind,snapshot_id,source_table))}`，topic 分别固定为
`__baseline__:{snapshot_id}:{source_table}` / `__snapshot_diff__:{snapshot_id}:{source_table}`，partition=0，
状态固定 SEALED；`(region,kind,snapshot_id,source_table)` 唯一且 FK 指向对应 scope snapshot。synthetic epoch
不参与 BROKER ACTIVE 唯一约束、不建/推进 ingest checkpoint，也不能转为 broker epoch；BASELINE/
SNAPSHOT_DIFF version 与 position 必须复合 FK 引用各自 registry 行。checkpoint、ingest event 只对 BROKER
把 epoch_id 纳入身份，row version 三种 kind 都把 epoch_id 纳入 PK/FK。

epoch-aware next-offset vector 的 dominance 只按 registry lineage 判断：同 epoch 比较 next_offset；ACTIVE
后继 sequence 无论同/跨 generation 都只有在 SNAPSHOT_MANIFEST全部满足时，才可支配其可达前驱；
sequence=1 的 H0_BOOTSTRAP 只可支配它自身的较小/equal next-offset vector。
不比较两代 offset 数值；BARRIER_PENDING、无 predecessor 链、链分叉或未过 barrier 时不可比较并失败关闭。
H0、handoff、scope fence和启动校验都调用唯一数据库函数
`dts_checkpoint_vector_dominates(current,required)`；synthetic epoch 永不进入该函数。这样 reset 后新 epoch 的
较小合法 offset不会被误判“早于”旧 handoff，也不会把无血缘 epoch 当作更新。

`dts_ingest_checkpoints` 的 v2 PK 固定为
`(source_region,source_partition_epoch_id,topic,partition_id)`，字段至少含 consumer_group、`next_offset bigint`、
row_version、updated_at。next_offset 表示所有 `<next_offset` delivery 已与 ledger/source写同事务耐久并可 ACK；
CAS 只能递增。旧 SUPERSEDED epoch checkpoint永久保留只读；同一 `(region,topic,partition)` 以部分唯一保证
恰好一个 `is_current=true` checkpoint，其 epoch只能 BARRIER_PENDING/ACTIVE且必须为最大 epoch_sequence。
创建新 epoch与旧 current=false、新 checkpoint=start_offset/current=true同事务；普通重启只CAS同一行。
实时 current vector只选择这些 current行；BARRIER_PENDING 可供 Ingestor按授权 lineage续跑，但 final shadow/
cutover只接受全部 ACTIVE。启动校验先用 `dts_checkpoint_vector_lineage_status` 区分
DOMINATES/PENDING_BARRIER/INCOMPARABLE：Ingestor可在前两者恢复，其他服务和生产切换只接受 DOMINATES。

所有 fence 使用半开 next-offset 语义：snapshot 从 `start_next_offset` 重放 delivery
`offset >= start_next_offset AND offset < end_next_offset`；checkpoint `next_offset >= end_next_offset` 即覆盖完毕；
barrier发布后普通增量从 `offset >= end_next_offset` 继续。禁止写“严格越过 next offset”或要求
`offset > end_next_offset`，否则会漏掉边界 delivery。

无法通过 envelope/epoch 校验的 delivery 不能写入 `dts_ingest_events`，而写受保护的
`dts_ingest_issues`。`ingest_issue_id=SHA-256(canonical JSON(connector_delivery_identity_hash,payload_hmac,
hmac_key_version))`，即同一 delivery+payload 的全部逐层校验错误共用一个 issue；另存
`connector_delivery_identity_hash`、`payload_hmac` 与 `hmac_key_version`、可取得的 region/topic/partition/
offset/epoch、canonical 排序的 `current_error_codes`、`issue_revision>=1`、`status=OPEN/RESOLVED`、first_seen_at/
last_seen_at/attempt_count、`case_id` 和安全诊断摘要，禁止保存 raw payload、原始学生 ID或密钥。
`UNIQUE(connector_delivery_identity_hash,payload_hmac,hmac_key_version)` 防止同一 delivery 重复建工作项；同
identity 但 HMAC 或 key version不同是独立 issue，不覆盖旧证据。
接入器在同一事务 UPSERT issue、UPSERT `DTS_SOURCE_CONFLICT` Case并写 audit，事务提交后仍不 ACK；重复到达
只递增同一 issue attempt/last_seen 和 Case revision；A错误修复后暴露B错误时更新同一 row的 error set、
issue_revision+1并追加 A→B audit，不提前 RESOLVE。修复后必须重放原 delivery：只有该 delivery+payload 通过
全部校验，且合法 event/version/current/checkpoint 成功提交的同一事务，才把这一个 issue置 RESOLVED、恢复
同一 Case，然后 ACK。普通角色不能直接 RESOLVE、
UPDATE identity/HMAC 或 DELETE；无法形成 connector delivery identity 时连 issue 都不猜，报
`INGEST_ISSUE_ID_UNAVAILABLE` 并持续不 ACK。

同一 immutable stream generation 的订阅 reset 若重读旧 offset，payload hash 相同则记 EPOCH_REPLAY并推进
新 epoch checkpoint，但 source_row_revision=NULL且不写 current/dirty/Outbox；hash 不同报 SOURCE_CONFLICT。
topic 重建只有提供新的、已验证 stream_generation_id 时才允许同名 topic/partition/offset 承载新事件。

每个 source key 在一个 source partition epoch内只允许来自同一真实 topic+partition，并严格按 offset接入；
发现同 key 跨 partition/topic 时整批报 `SOURCE_KEY_PARTITION_DRIFT`，不推进 offending checkpoint。订阅
reset/重分区/topic重建必须新 BARRIER_PENDING epoch并令相关 scope STALE；该 epoch CDC 可正常取得
source_row_revision/current/checkpoint，但在新 snapshot manifest COMPLETE前不得恢复 scope完整性或用于
epoch dominance/cutover，不能用 timestamp猜跨 epoch顺序。

来源自带的 opaque position 只可作为 ingest ledger 路由元数据，不参与业务排序。source_timestamp 缺失可
规范化为 JSON null，record_id 缺失用 type=none；非法时间或与 topic 配置不符的 record_id、空 topic、
缺 epoch/partition/offset 必须整批报 `SOURCE_CONFLICT:SOURCE_POSITION_INVALID`，不写版本、不推进 checkpoint、
不 ACK，禁止退化成空串或到达顺序。

SNAPSHOT_DIFF 的 source_position 仍按同一 JSON schema 生成：timestamp=NULL、record_id_type=text、
record_id=canonical source_key、reserved topic/partition/确定 offset；它只供审计。发布事务在取得
`(source_region,source_table)` exclusive publish advisory lock 后，先把 candidate 从 fence 追平到锁内读取的
end offset vector，并确认普通 Ingestor 已将该 vector 内 CDC 各自仅应用一次；再把 desired set 与同一时刻
live source current 比较。已由 CDC 达成的 key 只更新 membership，不再写 diff；只有剩余差异写下一
source_row_revision 的 SNAPSHOT_DIFF。diff 记录 `covered_through_offsets=end_next_offset vector`；释放锁后
offset `>= end_next_offset` 的 CDC 可取后续 revision，源时间更早也不能排到 diff 前。candidate 重放 CDC 只更新
staging desired set，不追加第二份 CDC 版本/dirty/Outbox。

active epoch 发布后的普通 CDC 不能绕开 membership：Ingestor 在写 source current/revision 的同一事务内，
按 canonical scope key 顺序调用 SECURITY DEFINER `scope_membership_apply_cdc()`。该函数只针对已经存在的
GLOBAL/TEACHER scope 行锁定 membership，以完整 before/after 计算 overlay：INSERT/恢复令 after 所属 scope
present，DELETE 令 before 所属 scope absent，teacher A→B 同时令 A absent、B present；GLOBAL 同样按 after
是否未删除更新。overlay 记录同一 source_row_revision，和 source current/dirty/checkpoint 一起提交，失败则
全部回滚。函数无权改 active_snapshot_id、snapshot baseline、scope state 或 candidate staging；Trigger 拒绝
普通 Ingestor 直接写这些列。LOADING candidate 由 candidate replay cursor 从同一 append-only CDC versions
更新 staging，发布 barrier 必须证明 replay 已覆盖锁内 end vector，故不要求 Ingestor 双写 staging。scope 为
STALE/FAILED 时 overlay 仍追 current，但不能提供完整性；恢复发布以锁内 live current 合并 baseline+overlay。

`dts_source_scope_snapshots` 每个装载 epoch 一行，以全局唯一 `snapshot_id` 为 PK，并保存完整 scope 身份、
epoch 状态及 snapshot/fence/hash/覆盖区间证据。`dts_source_snapshot_rows` 的 PK 为
`(snapshot_id,source_region,source_table,scope_kind,scope_level,scope_key,source_key)`，保存 canonical row/hash、
受保护 row、snapshot_as_of 和 fence；只允许对应 candidate epoch 的 scope publisher 写。
`dts_source_scope_memberships` 的 PK 为
`(source_region,source_table,scope_kind,scope_level,scope_key,source_key)`，保存 active_snapshot_id、
`snapshot_is_present/snapshot_row_hash`、`cdc_overlay_is_present/cdc_overlay_row_hash/
last_cdc_source_revision`、由两者计算的 `effective_is_present` 和 membership_revision。scope state 分别保存
`active_snapshot_id` 与 `candidate_snapshot_id`；前者始终指向
最后一次原子发布的 epoch（即使当前已 STALE），后者只指向本轮装载尝试，二者不得混作一个指针。
DOM 行的 before/after 中禁止出现原始学生 ID；数据库 Trigger 拒绝改写、
物理删除和非法 token。索引至少覆盖 `(source_region,source_table,source_key,source_timestamp,record_id,offset_value)`
以及 appoint/teacher/student 依赖键。

`dts_dirty_keys` 现行主键不含地区，v2 Alembic 必须重建主键/唯一约束，把 `source_region` 纳入身份；
`last_source_region` 不能继续充当身份列。所有 claim、upsert、完成和重试 SQL 都必须带地区条件。
四列均 `NOT NULL`；单 ID key 的 `key_part_2` 唯一占位符固定为空字符串，不允许 NULL、`*` 或 `-`。精确矩阵为：

| key_type | source_region | key_part_1 | key_part_2 | 唯一领取 owner |
|---|---|---|---|---|
| `COURSE` | `dom/ovs` | canonical source_appoint_id | `''` | Domain Projector |
| `TEACHER` | `dom/ovs` | teacher_id | `''` | Domain Projector |
| `LABEL` | `dom/ovs` | canonical label_id | `''` | Domain Projector |
| `COMPLAINT_CATEGORY` | 只能 `dom` | canonical category_id | `''` | Domain Projector |
| `TEACHER_STUDENT` | `dom/ovs` | teacher_id | student_token；DOM必须是`dom:v1:` HMAC token | Domain Projector |
| `TEACHER_TIME_RECHECK` | 只能 `dom` | teacher_id | 北京业务日期`YYYY-MM-DD` | SourceWide Worker |

CHECK 按该矩阵拒绝空 part_1、非法 region、单ID非空 part_2、关系空 token和非法日期。canonical dirty key固定为
canonical JSON `{"source_region", "key_type", "key_part_1", "key_part_2"}`（实际hash按key UTF-8 bytes排序、
无多余空白），技术Case/source_ref、claim排序、input/dependency FK全部复用这四列，禁止各handler重新拼键。
目标状态为 `PENDING/PROCESSING/WAITING_DEPENDENCY/RETRY/DEAD/COMPLETED`。每行至少保存 dirty-local
`required_work_revision>=1,claimed_through_work_revision,completed_work_revision>=0,work_generation>=1,
dead_generation>=0,last_input_identity_hash,last_input_revision,row_version,attempt_count,next_attempt_at,
last_error_code,blocked_by,lease_owner_kind,lease_owner,lease_token,claimed_at,lease_expires_at,created_at,updated_at`。不同来源的
source_row_revision 永远不可直接比较。

另建 append-only `dts_dirty_key_inputs`：PK 为 dirty 复合键加
`input_kind,input_identity_hash,input_revision`，并对 `(dirty key,dirty_work_revision)` 唯一；保存 canonical
`input_identity jsonb`、`input_fingerprint`、分配到的 dirty-local `dirty_work_revision` 和 created_at。
input_kind 只允许 `SOURCE_REVISION/SCOPE_REVISION/CATALOG_REVISION/DEPENDENCY_WAKE/OPERATOR_RECOVERY/
TIME_RECHECK`；identity
分别固定包含 source region/table/key、scope identity、catalog type/key、dependency type/region/key、或
dead_generation+operator_request_id；TIME_RECHECK identity固定为
`{source_region:'dom',teacher_id,business_date_beijing}`、input_revision固定1、fingerprint为该canonical identity
加`protocol_version='teacher-time-recheck-v1'`的SHA-256。同教师同日由cutover catchup和00:05重复写是no-op，
跨日因dirty key和identity都不同而各自产生一次。`input_revision` 只在同一 input_identity 内比较。再建
`dts_dirty_key_dependencies`，PK 为 dirty 复合键加
`dependency_type,dependency_region,dependency_key`，保存等待时观察到的 dependency source revision/hash并建
反向索引；禁止运行时扫描 JSON 找依赖。

所有 fingerprint 共用 canonical JSON v1 SHA-256，字段不允许调用方增删。kind→唯一入口/引用校验固定为：

| kind | 唯一 wrapper / 可执行角色 | input identity 与 revision | fingerprint canonical 字段 |
|---|---|---|---|
| SOURCE_REVISION | `enqueue_dirty_from_source_revision_v2`；仅ingest/scope-publisher角色 | `{source_region,source_table,source_key}`；所指`source_row_revision` | `protocol='dirty-source-v1',identity,revision,version_kind,operation,is_deleted,protected_source_row_hash`；锁内复合FK回读version/current，hash不符拒绝 |
| SCOPE_REVISION | `enqueue_dirty_from_scope_revision_v2`；仅Scope Coordinator | `{region,table,scope_kind,scope_level,scope_key}`；scope `row_version` | `protocol='dirty-scope-v1',identity,row_version,state,active_snapshot_id,active_epoch_id,active_fence_hash`；必须回读同一scope行 |
| CATALOG_REVISION | `enqueue_dirty_from_catalog_revision_v2`；仅受限规则发布角色；当前catalog_type只允许COMPLAINT_RULE_SET | `{catalog_type='COMPLAINT_RULE_SET',catalog_key='ACTIVE_COMPLAINT_RULE_SET'}`；全局 activation_generation | `protocol='dirty-catalog-v1',identity,revision,version_id=source_sha256,payload_hash=content_hash`；必须回读PUBLISHED版本/audit |
| DEPENDENCY_WAKE | 只由无外部EXECUTE的`wake_dts_dirty_keys_for_dependency_v2`内部调用 | `{dependency_type,dependency_region,dependency_key}`；被引用事实自身revision | `protocol='dirty-dependency-v1',identity,revision,dependency_hash`；必须与dependency反向索引和引用事实一致 |
| OPERATOR_RECOVERY | 只由`recover_dts_dirty_key_v2`内部调用，其他角色无EXECUTE | `{canonical_dirty_key,dead_generation,operator_request_id}`；固定1 | `protocol='dirty-recovery-v1',identity,expected_row_version,reason_hash` |
| TIME_RECHECK | `enqueue_teacher_time_recheck_v2`；仅time-catchup/日更调度角色 | `{dom,teacher_id,business_date_beijing}`；固定1 | `protocol='teacher-time-recheck-v1',identity`；日期不得晚于函数内数据库当前北京业务日 |

底层 `_upsert_dts_dirty_key_input_v2(...)` REVOKE PUBLIC及全部runtime EXECUTE；wrapper不接收kind，按常量调用
内核并验证引用事实。伪kind、伪revision、无引用行或异hash固定报 `DIRTY_INPUT_AUTHORITY_DENIED/
DIRTY_INPUT_REFERENCE_INVALID`，不能用通用upsert复活DEAD。

所有状态写只允许下列 SECURITY DEFINER 函数，普通 Worker 对两表无直接 INSERT/UPDATE/DELETE：

1. 上表wrapper调用的内部 `_upsert_dts_dirty_key_input_v2(key,input_kind,input_identity,input_revision,
   input_fingerprint)` 在来源版本/current/
   checkpoint同一事务锁dirty行和同identity最新input。相同identity+revision+fingerprint为no-op；同identity+
   revision异fingerprint报`DIRTY_INPUT_CONFLICT`；同identity更旧revision为no-op；任何identity的合法新revision
   都先插input，并在dirty行内把required_work_revision原子+1，把该值回填input.dirty_work_revision。
   因而 appoint r10 后 grading r1 是两个identity、会得到连续dirty work revision，不会横向比较或吞掉。
   PENDING/RETRY/WAITING_DEPENDENCY/COMPLETED/DEAD 转 PENDING并清
   dependency/错误/租约，`next_attempt_at=transaction_timestamp()`；PROCESSING 不抢占当前 token，只提高
   required_work_revision并由完成函数在本轮后重新排队。
   从 DEAD 因新输入重开要写 audit，旧技术 Case 保持到新一代真正完成；单纯重放不能复活 DEAD。
2. 内部 `_claim_dts_dirty_keys_v2(worker_kind,worker_id,batch_size,lease_seconds)` 对PUBLIC和全部runtime角色
   REVOKE EXECUTE，只能由两个SECURITY DEFINER wrapper以常量kind调用：
   `claim_domain_dirty_keys_v2`只GRANT给NOINHERIT login `tit_dts_domain_projector_runtime`，常量
   DOMAIN_PROJECTOR并只领COURSE/TEACHER/LABEL/COMPLAINT_CATEGORY/TEACHER_STUDENT；
   `claim_teacher_time_rechecks_v2`只GRANT给NOINHERIT login `tit_source_wide_runtime`，常量
   SOURCEWIDE_TIME_RECHECK并只领TEACHER_TIME_RECHECK。不能在SECURITY DEFINER函数里用current_user判断invoker，
   也不能由worker_id内容决定权限。wrapper只领取
   `status IN (PENDING,RETRY) AND next_attempt_at<=transaction_timestamp()`，按
   `next_attempt_at,updated_at,canonical dirty key` 排序并 `FOR UPDATE SKIP LOCKED`，原子改 PROCESSING、生成随机
   lease_token、冻结 claimed_through_work_revision=required_work_revision、写lease_owner_kind、把next_attempt_at置NULL并写
   claimed/expires；lease_seconds 只允许 15–300。返回复合键、token、claimed work revision、row_version，
   Worker 后续每次写都必须带 token。
3. renew/complete/wait/fail/reaper同样各有domain/time-recheck两个仅分别GRANT的wrapper，调用无PUBLIC权限的
   `_renew/_complete/_wait/_fail/_reap_dts_dirty_key*_v2`并传常量owner kind；内部还校验行上lease_owner_kind与
   key_type矩阵。time-recheck不允许wait dependency wrapper。`renew_*` 只允许未过期 PROCESSING，
   最多从 transaction_timestamp 延长 300 秒；token/version/状态不符报 `DIRTY_LEASE_LOST`。
4. `complete_*` 必须与领域事实、aggregate revision和
   v2 Outbox 同一事务调用。token未过期且claimed相同才可提交；若 required_work>claimed_work，保存本轮 result
   后转PENDING、`next_attempt_at=transaction_timestamp()`，而非COMPLETED；否则写
   completed_work_revision=claimed_work、status=COMPLETED、attempt=0、next_attempt_at=NULL。两分支都清租约；
   真正完成时按 §6.6 原子恢复该 key 的技术 Case。
5. domain `wait_*` 只接受非空、canonical排序
   去重的类型化依赖数组，在同一事务替换 dependency rows并转 WAITING_DEPENDENCY；不增加attempt。若锁内
   required_work>claimed_work，直接转PENDING、next_attempt_at=transaction_timestamp()且不落过期依赖；否则
   WAITING_DEPENDENCY的next_attempt_at=NULL。`wake_dts_dirty_keys_for_dependency_v2(dependency_identity,
   dependency_source_revision,dependency_hash)` 由对应来源接入事务调用；只有与等待证据不同的新 revision/hash
   才按DEPENDENCY_WAKE input协议分配新dirty work revision并删除命中依赖；若当前PROCESSING仅提高required，
   否则转PENDING、attempt=0、next_attempt_at=transaction_timestamp()，普通重放no-op。
6. `fail_*` 只接受登记为 transient 的错误。锁内若
   required_work>claimed_work，清租约后直接PENDING、next_attempt_at=transaction_timestamp()且不计旧失败；
   否则 attempt+1，1–7 转RETRY，固定退避为
   `min(30 minutes,5 seconds * 2^(attempt-1))`、无随机抖动；第8次转DEAD、dead_generation+1并与技术 Case/audit
   同事务提交。租约到期由对应owner的 `reap_expired_domain_dirty_keys_v2` 或
   `reap_expired_teacher_time_rechecks_v2` 按同一规则记
   `DIRTY_LEASE_EXPIRED`，它是唯一可接管过期 PROCESSING 的入口；崩溃前未与 complete 同事务提交的领域写会
   回滚，因此接管后可安全重算。
7. `recover_dts_dirty_key_v2(key,expected_dead_generation,expected_row_version,operator_request_id,reason)` 是唯一
   人工 DEAD→PENDING入口；以 OPERATOR_RECOVERY identity/revision 写append-only input、原子分配下一dirty work
   revision，attempt=0、work_generation+1、next_attempt_at=transaction_timestamp()并写audit；同request重放
   no-op且不再+1，不提前关闭Case。WAITING_DEPENDENCY不能人工伪装成功，必须
   由依赖唤醒或新增来源事实触发。

CHECK 固定为：PROCESSING 当且仅当五个 owner/lease/claim 字段完整且owner kind符合key_type、
next_attempt_at=NULL；其他状态租约全空；
WAITING_DEPENDENCY 当且仅当 blocked_by非空、至少一条dependency且next_attempt_at=NULL；RETRY 必有
next_attempt_at/last_error且 attempt 1–7；DEAD 必为attempt=8、无next/lease；COMPLETED 必有
completed_work_revision>=required_work_revision、attempt=0且next=NULL；PENDING 必为attempt=0且
next_attempt_at非空。required_work_revision必须等于该dirty key inputs的最大dirty_work_revision，首次input分配1，
不得由调用方指定。
所有函数按 `row_version+1` 留审计。不得使用 `infinity` 静默停放。cutover readiness 只接受全部相关行
COMPLETED；PENDING/PROCESSING/WAITING_DEPENDENCY/RETRY/DEAD 任一存在都阻断 final shadow。

### 4.2 `source_courses`

一节源课程一行。

| 字段 | 类型/约束 | 含义 |
|---|---|---|
| `source_region` | varchar(8)，PK，`dom/ovs` | 来源地区 |
| `source_appoint_id` | varchar，PK | 源课程 ID |
| `student_token` | varchar，可空 | DOM 仅允许 `dom:v1:` token |
| `lesson_local_date` | date，可空 | 来源本地日期 |
| `lesson_local_time` | time，可空 | 来源本地时间 |
| `scheduled_start_at` | timestamptz，可空 | 只有时区可证明时才归一化 |
| `raw_end_time` | text，可空 | `appoint.end_time` 原值 |
| `end_time` | timestamptz，可空 | 权威完课时间的可计算形式；无法确认时区时为空 |
| `source_status` | text，可空 | appoint 当前 status 原值 |
| `current_teacher_id` | varchar，可空 | appoint 当前 t_id |
| `current_participation_seq` | integer，可空 | 当前指派参与序号 |
| `is_peak` | boolean，可空 | 由已确认地区/本地时间规则派生 |
| `completion_participation_seq` | integer，可空 | 首次 end 冻结参与 |
| `completion_teacher_id` | varchar，可空 | 首次 end 冻结教师 |
| `completion_frozen_at` | timestamptz，可空 | 冻结发生时间 |
| `completion_end_time` | timestamptz，可空 | 冻结时权威 end_time，也是收藏观察起点 |
| `completion_student_token` | varchar，可空 | 冻结时学员键 |
| `completion_is_peak` | boolean，可空 | 冻结时 Peak 事实 |
| `completion_lesson_local_date/time` | date/time，可空 | 冻结时课程日期和时间 |
| `completion_source_position` | jsonb，可空，合法结构化 source position | 产生首次冻结的 appoint 版本 |
| `completion_source_revision` | bigint，可空 | 产生首次冻结的 appoint source row revision；正式顺序边界 |
| `initial_completion_snapshot` | jsonb，可空 | 首次冻结的不可变完整快照；TRANSFER/VOID 后仍保留 |
| `completion_voided_at` | timestamptz，可空 | VOID 决定生效时间 |
| `completion_conflict_status` | varchar，非空 | `NONE/PENDING/RESOLVED_KEEP/RESOLVED_UPDATE/RESOLVED_TRANSFER/RESOLVED_VOID` |
| `completion_conflict_case_id` | varchar，可空 | 当前/最近完课纠错 Case |
| `conflict_resolved_against_position` | jsonb，可空，合法结构化 source position | 最近决定已处理到的 appoint 来源位置 |
| `conflict_resolved_against_revision` | bigint，可空 | 最近决定已处理到的 source row revision；正式收口边界 |
| `conflict_fingerprint` | varchar，可空 | 当前冻结快照与来源现状差异摘要 |
| `source_is_deleted` | boolean，非空 | 源 appoint 是否已删除 |
| `evidence_status` | varchar，非空 | `CONFIRMED/SOURCE_MISSING/SOURCE_CONFLICT` |
| `last_applied_event_position` | jsonb，可空，合法结构化 source position | 已应用到参与状态机的最后 appoint 来源版本位置 |
| `last_applied_source_revision` | bigint，可空 | 状态机最后应用的 source row revision；后续只能严格递增 |
| `row_version` | bigint，非空且递增 | 乐观锁版本 |
| `updated_at` | timestamptz，非空 | 当前投影更新时间 |

约束：

- PK：`(source_region, source_appoint_id)`；
- 当前完课教师、参与、冻结时间、来源位置和 source revision 在未 VOID 时必须同时为空或同时非空；end_time、学员、Peak、日期/时间按冻结时已知值保存，缺失时保持 NULL 并标 `SOURCE_MISSING`，不得阻止教师归属冻结；
- `current_teacher_id/current_participation_seq` 必须同时为空或同时非空；非空时必须指向本课程唯一
  `is_current=true` 参与且 teacher_id 相等，反向存在 current 参与时课程指针也必须指回；
- `completion_teacher_id/completion_participation_seq` 非空时必须指向本课程唯一
  `participation_role='COMPLETION'` 参与且 teacher_id 相等，反向存在 COMPLETION 时课程指针也必须指回；
- `initial_completion_snapshot` 首次写入后数据库 Trigger 禁止修改/清空；VOID 可以清空当前 completion 字段，但不能清除初始快照；
- 冻结字段只能由首次冻结或已批准的纠错命令修改，普通投影数据库角色无权修改已冻结值；
- `completion_end_time`、`completion_student_token` 或完成教师任一为空时不得启动收藏 24 小时观察，
  标记 `SOURCE_MISSING`；补齐来源后仍需新的完课纠错决定更新冻结快照，普通 DTS 不得直接补写。

### 4.3 `source_course_participations`

| 字段 | 类型/约束 | 含义 |
|---|---|---|
| `source_region` | varchar(8)，PK/FK | 来源地区 |
| `source_appoint_id` | varchar，PK/FK | 源课程 ID |
| `participation_seq` | integer，PK，`>=1` | 本课程内指派顺序 |
| `teacher_id` | varchar，非空 | 该阶段教师 |
| `participation_status` | text，可空 | `on/end/t_absent/...`；普通源值保留 |
| `participation_role` | varchar，非空 | `NORMAL/COMPLETION/PENDING_CORRECTION/REJECTED_CORRECTION/SUPERSEDED_COMPLETION/VOIDED_COMPLETION` |
| `is_current` | boolean，非空 | 是否为 appoint 当前指派 |
| `assigned_at` | timestamptz，可空 | 产生该阶段的业务时间；存量仅有快照时不得伪造 |
| `assigned_at_evidence_status` | varchar，非空 | `CONFIRMED/SOURCE_MISSING` |
| `ended_at` | timestamptz，可空 | 被下一指派替换或删除的来源时间 |
| `absence_reason_detail` | text，可空 | 最新 `reason_type` |
| `no_notice` | boolean，可空 | 最新原因是否精确为 `No Notification` |
| `source_deleted` | boolean，非空 | 删除前历史保留标记 |
| `assignment_source_partition_epoch_id` | varchar，非空 | 产生参与的 CDC 或 SNAPSHOT_DIFF epoch；复合 FK 身份组成 |
| `assignment_event_topic` | varchar，非空 | 产生本参与的 appoint 事件 topic |
| `assignment_event_partition` | integer，非空 | 产生本参与的 partition |
| `assignment_event_offset` | bigint，非空 | 产生本参与的 offset |
| `assignment_source_row_revision` | bigint，非空 | 产生本参与的 appoint source row revision；跨 CDC/SNAPSHOT_DIFF 的正式顺序 |
| `assignment_event_phase` | varchar，非空 | `SNAPSHOT_DIFF/BEFORE/AFTER`；允许首个 UPDATE 从真实 before 补建旧参与；原始 BASELINE 不进入参与状态机 |
| `row_version` | bigint，非空且递增 | 乐观锁版本 |

约束：

- PK：`(source_region, source_appoint_id, participation_seq)`；
- 每节课最多一行 `is_current=true` 的部分唯一约束；
- 每节课最多一行 `participation_role='COMPLETION'` 的部分唯一索引；
- 同一教师再次被指派必须新建序号，不能复活旧参与；
- `source_region + assignment_source_partition_epoch_id + assignment_event_topic + assignment_event_partition + assignment_event_offset + assignment_event_phase`
  唯一，保证同一来源版本的同一阶段只产生一次参与；
- `(source_region,source_appoint_id,assignment_source_row_revision,assignment_event_phase)` 唯一；
- `(source_region,assignment_source_partition_epoch_id,assignment_event_topic,assignment_event_partition,
  assignment_event_offset)` 复合 FK 指向产生该参与的 appoint row version；deferred trigger 另校验其
  source_appoint_id/source_row_revision。topic reset/recreate 后相同 offset 的新 epoch 不与旧参与碰撞；
- 投影器在课程行锁内按 source_row_revision 逐条应用尚未处理的 appoint 事件，再同时更新
  `last_applied_source_revision` 和审计 position；
- 参与序号由该锁内的 `max(participation_seq)+1` 生成；相同来源版本重算时复用已有序号，不得重复新增；
- 脏键可以合并，但 appoint 版本历史不能合并或丢弃；A→B→A 即使三个事件在同一批次到达也必须重放出 seq=1/2/3。

数据库新增 DEFERRABLE INITIALLY DEFERRED constraint trigger，覆盖 `source_courses`、
`source_course_participations` 和 `lesson_score_results`：事务结束时双向校验 current 指针↔is_current、
completion 指针/teacher↔唯一 COMPLETION；若已有 lesson_score_result，还必须校验其
completion_participation_seq/teacher_id 等于课程冻结指针并指向该 COMPLETION 行。任何普通角色直写出
双 current、双 completion、悬空/错教师指针或积分归属不一致都整事务失败。首次 end 与所有纠错命令先锁
课程行；TRANSFER 在同一事务按“旧 COMPLETION→SUPERSEDED_COMPLETION，再目标→COMPLETION，再改课程与
score 指针”的顺序执行，避免部分唯一索引瞬时冲突；并发决定仍由 Case revision/source_row_revision 校验拒绝旧写。

新 offset 的语义重复按课程行锁内的重建当前态判断：稀疏合并后的 before 与当前态一致才正常应用；
before/after 都等于当前态是 NOOP；before 与当前态不一致、after 等于当前态，且上一条已应用 appoint
版本具有相同 canonical before→after transition 时记 `SEMANTIC_REPLAY`，推进版本位置但不新增参与或
Outbox；before 不一致且不满足该条件时转 `SOURCE_CONFLICT`，不能强行套用。A→B→A→B 因中间版本
逐条改变当前态，后一次 before=A 与当时当前态一致，仍会合法创建新的 B 参与，不会被历史 fingerprint
全局去重。

首次发布且尚无 active epoch 时，desired set 中每个 appoint key 也必须先生成确定的
`SNAPSHOT_DIFF/SNAPSHOT_INSERT`，并由它创建 `phase=SNAPSHOT_DIFF` 的首个参与；原始 BASELINE 行只作
快照证据，永不直接创建参与。若 projector 首次看到的是 CDC UPDATE 且尚无 snapshot diff/INSERT，
但完整 before 明确给出旧 `t_id=A`，则先用该 before
确定性创建 `phase=BEFORE` 的 A 参与（`assigned_at=NULL/SOURCE_MISSING`），再按 after 创建
`phase=AFTER` 的新参与；因此首事件 A→B 仍得到 seq=1/2。若 before 也不能证明旧教师，则保持
`WAITING_DEPENDENCY`；对应 CURRENT scope 已 COMPLETE 后仍无法补齐时转 `SOURCE_CONFLICT` 并按 §6.6 创建技术 Case，
不得猜教师或吞掉变化。

确定性状态机：

| 事件 | 处理 |
|---|---|
| INSERT，`t_id=A` | 创建 seq=1，状态取 after.status |
| UPDATE，`A→A` | 不新增参与；只更新当前参与源状态 |
| UPDATE，`A→B`，首次 end 前 | A→`t_absent/is_current=false`；B 新建下一 seq |
| UPDATE，`A→B→A` | 第三次指派 A 新建 seq=3，不复用 seq=1 |
| UPDATE，`A→NULL`，首次 end 前 | A→`t_absent/is_current=false`；课程当前参与为空 |
| UPDATE，`NULL→B` | B 新建下一 seq；没有旧教师可标缺席 |
| 同一 UPDATE 同时 `A→B` 且 `status→end` | 先完成代课转移，再把 B 的新参与冻结为完课参与 |
| INSERT 即 `status=end` | 创建首个参与后立即冻结；缺 t_id 时不冻结并标 `SOURCE_MISSING` |
| 首次 end 后普通 t_id/status/end_time 变化或 DELETE | 更新来源当前态，但冻结归属不变；创建/更新完课纠错 Case |
| 首次 end 前 DELETE | 课程写 tombstone；当前参与 `is_current=false/source_deleted=true`，保留原状态且不推断缺席 |
| tombstone 后同 ID 恢复且仍未完课 | 按新来源版本创建下一 participation_seq，不复活已 source_deleted 的参与 |

首次 end 后的参与处理固定为：后续 `t_id` 变化不把冻结参与改为 `t_absent`；新教师仍建立下一 seq，
role=`PENDING_CORRECTION`，`is_current` 反映 appoint 当前指派，但该行不进入预约、完课、异常或积分聚合。
KEEP 后该行改为 `REJECTED_CORRECTION`；TRANSFER 后目标行改为 `COMPLETION`、旧完成行改为
`SUPERSEDED_COMPLETION`；VOID 后旧完成行改为 `VOIDED_COMPLETION`。这些角色变化只能由受限纠错事务执行。
`is_current` 始终只表示 appoint 来源当前 t_id，不表示积分归属；KEEP/UPDATE/TRANSFER/VOID 都不能为迎合
完课归属而篡改它。聚合仅统计 NORMAL/COMPLETION 中本节明确列出的状态，所有 correction 角色默认不计。

首次观察到 `status=end` 但 `t_id` 为空时，不存在可冻结教师：保存不可变的首次 end 证据快照，
`completion_conflict_status=PENDING`，创建同一课程的 `COURSE_COMPLETION_CORRECTION` Case，当前
completion/逐课积分/收藏观察均为空。后续 `NULL→B` 只创建 role=`PENDING_CORRECTION` 的参与并更新
Case，不自动冻结；只允许 `TRANSFER_COMPLETION` 明确选中该参与，或 `VOID_COMPLETION` 明确作废。
该类 Case 不允许 KEEP。

### 4.4 `source_course_labels`

| 字段 | 约束 |
|---|---|
| `source_region, source_log_id` | PK，来源 log 身份 |
| `source_appoint_id, label_id` | 非空 |
| `label_name_snapshot` | 可空，事件时名称快照 |
| `create_time, dt` | 可空；严格解析后的 log 业务排序时间 |
| `source_position` | jsonb，非空；§4.1 结构化来源位点，仅作审计/等值校验，不跨 version kind 排序 |
| `source_row_revision` | bigint，非空 | 同一 source_log_id 修订的正式最终决胜位点；禁止按 position 文本/JSON 比较 |
| `is_deleted, source_version, updated_at` | 非空 |

课程当前标签集合是未删除 log 按 `(source_appoint_id,label_id)` 去重后的集合。`label_id` 是身份，
当前展示名取该集合中按 `(create_time,dt,canonical source_log_id,source_row_revision)` 最新 log 的
`label_name`；同 ID 多条来源修订先按 `source_row_revision` 决定 typed 当前行，跨 log 再使用同一比较器。
`source_version` 仅保存已应用来源版本摘要，不参与名称选择。同 ID 多条 log
删除一条时，只要还有未删除 log，课程标签仍保留并恢复剩余最新名称。`grading_label` 字典只作受限
参考事实，不覆盖 log 提供的 label_name，也不改写课程标签或既有任务标题。

### 4.5 类型化课程与参与事实

`dts_source_rows` 是受限来源镜像，不直接给 API/积分读取。领域投影器固定写两张类型化当前表：

#### `source_course_complaints`

PK 为 `(source_region,source_complaint_id)`；至少保存 `source_appoint_id`、源 teacher/t_id（有则保存）、
`complaint_type/complaint_type_child/complaint_type_grandson`、approve、validity、`add_time`、`course_date`、
`is_valid`、匹配的 complaint_rule_id、severity_rank、
一/二/三级分类展示快照、`CONFIRMED/PENDING_DATA/SOURCE_MISSING`、is_deleted、source_version、
source_position、source_row_revision 和 updated_at。
每条源投诉独立留在该类型化集合；Worker/L0 不得回读受限 `dts_source_rows`，也不得只看课程 latest。
grandson 为空时 `is_valid` 仍可为 true，但规则/严重度为空且证据为 PENDING_DATA。

DOM/OVS 使用同一套 DOM 分类字典，精确关联链固定为：

```text
complaint_type          = dom_complaint_cate.id -> category_l1 = cate_cn_name
complaint_type_child    = dom_complaint_cate.id -> category_l2 = cate_cn_name
complaint_type_grandson = dom_complaint_cate.id -> category_l3 = cate_cn_name
category_l3_normalized  = NFKC(category_l3) -> strip -> 连续空白折叠为一个 ASCII 空格
category_l3_normalized  = complaint_category_rules.category_l3_normalized
```

分类 ID 使用 §4.8 的 canonical ID 等值关联，不做名称模糊匹配、大小写折叠或跨级回退。非空 ID
对应字典行缺失/已删除时保留投诉 typed 行和全部 ID，写
`SOURCE_MISSING:COMPLAINT_CATEGORY_NOT_FOUND` 并等待字典依赖；grandson ID 本身为空则仍按已冻结规则写
`PENDING_DATA:COMPLAINT_CATEGORY_MISSING`。一级/二级 ID 为空只使对应展示级别为空；只要三级 ID 可精确
匹配规则，仍可按 severity 路由，但不能把未知 son 猜成“出席问题”或“网络设备问题”。

#### `source_course_fact_current`

PK 为 `(source_region,source_appoint_id)`，至少包含：

- 当前 DOM 评价来源键、`POSITIVE/NEGATIVE/UNCLASSIFIED/SOURCE_MISSING`、negative_score、评价证据状态；
- 当前投诉来源键、是否存在投诉、是否存在有效投诉、最新有效一/二/三级分类、`complaint_evidence_status`、
  `complaint_error_code`；这些是
  课程展示/输出选择器投影，完整投诉集合仍以 `source_course_complaints` 为准；
- `is_camera_off`、`is_cpu_usage_high`、`is_network_delay_high` 及各自证据状态；
- `row_version`、最后参与计算的来源版本向量、updated_at。

CPU/网络新来源接入前固定 NULL/SOURCE_MISSING。标签不复制成逗号字符串，读取时关联
`source_course_labels`。字段只有实际变化时 row_version+1。

#### `source_participation_fact_current`

PK/FK 为 `(source_region,source_appoint_id,participation_seq)`，包含 `is_late`、`is_early`、
penalty evidence status、所用处罚来源键集合摘要、row_version、updated_at。处罚集合中任一有效记录
证明异常则为 true；没有 true 且集合完整、所有有效记录均证明未异常时为 false；存在缺时间或未知
appeal 且没有 true 时为 NULL/SOURCE_MISSING。

课程子事实“保存到源课程”专指：来源版本写入 `dts_source_row_versions/dts_source_rows`，类型化结果
写入上述 current 表；不得把评价、投诉、QA 直接覆盖到某个当前教师参与行。

### 4.6 师生关系与收藏归因

新增四张表：

#### `teacher_student_relationship_events`

- PK：`(source_region, source_partition_epoch_id, topic, partition_id, offset_value)`；
- 只保存类型化字段：来源表/记录 ID、旧/新 teacher_id、旧/新 student_token、`FAVORITE/BLOCK`、
  操作、旧/新有效起止时间、旧/新永久标志、effective_at、`effective_time_evidence_status`、来源时间；
  禁止保存整行 JSON 或原始学生 ID；
- 国内所有 old/new student 只允许 `dom:v1:` token；数据库 Trigger 与 ACL 同时校验；
- 事件只追加，不原地改写。

#### `teacher_student_relationship_current`

- PK：`(source_region, teacher_id, student_token)`；
- 字段：`is_favorited`、`is_blocked`、最后业务生效时间、`effective_time_evidence_status`、最后来源版本、`row_version`；
- 当前态由所有未删除来源记录重算，不由单条 DELETE 直接清零。

#### `course_favorite_observations`

- PK：`(source_region,source_appoint_id,observation_revision)`；同课程历史版本递增；
- 部分唯一：同课程 `status NOT IN ('INVALIDATED','VOIDED')` 时最多一个当前观察；
- 字段：teacher_id、student_token、completion participation、`observed_at=completion_end_time+24h`、
  `appoint_id_type=NUMERIC/TEXT` 及互斥的 `appoint_id_numeric/appoint_id_text_sort` canonical 排序列、
  relation_state、`relation_evidence_status`、`relation_error_code`、
  `PENDING/EVALUATING/CONFIRMED_TRUE/CONFIRMED_FALSE/WAITING_HISTORY/WAITING_EVIDENCE/RETRY/DEAD/INVALIDATED/VOIDED`、
  observation-local `required_evidence_revision>=1/claimed_evidence_revision/completed_evidence_revision>=0`、
  `required_evidence_fingerprint`、attempt_count、next_attempt_at、lock owner/time、rule version、observation revision，以及
  `materialization_origin`、`created_projection_generation`、`serving_projection_generation`、is_serving，值域/
  生命周期与 match相同；rollback所有 observation停止 serving，再切换时按当前证据恢复同一 revision或建新 revision；
- 到期领取索引：`status IN (PENDING,RETRY) + next_attempt_at + observed_at`；使用 `FOR UPDATE SKIP LOCKED`，
  expired EVALUATING 另有回收索引；最多 8 次后 DEAD 并按 §6.6 创建技术 Case；历史覆盖不足进入 WAITING_HISTORY，不把 false 当结论。

观察状态机只允许以下边，普通角色无 status/lease写权限：

- INSERT：正常 Domain 只建 `PENDING,attempt_count=0,next_attempt_at=observed_at`；cutover 专用 reducer 可按同一
  evaluation_as_of 直接建 PENDING/WAITING_HISTORY/WAITING_EVIDENCE/CONFIRMED_TRUE/CONFIRMED_FALSE；
- `claim_favorite_observations_v2(worker_id,limit)` 在数据库函数入口唯一读取一次 `clock_timestamp()` 为
  `claim_as_of`，不接受调用方/Worker主机时间；只领取 `observed_at<=claim_as_of` 且next到期的PENDING/RETRY，写
  EVALUATING、attempt+1、`claimed_evidence_revision=required_evidence_revision`、唯一
  lease_token/owner/acquired/expires；attempt不得超过8；
- `reap_expired_favorite_observations_v2(limit)` 同样在入口唯一读取一次数据库 `clock_timestamp()`，领取
  lease_expires_at<=该值的 EVALUATING：attempt<8 转 RETRY并
  写 backoff，attempt=8 转 DEAD、dead_generation+1并与技术 Case同事务；不能把过期 EVALUATING 直接当新
  PENDING或再次+attempt，所以崩溃不会永久卡死也不会一次超时计两次；
- EVALUATING 只有匹配 lease_token 的 owner 可 heartbeat。完成函数同时带claimed_evidence_revision：若锁内
  required>claimed，拒绝提交旧真假结论，转PENDING、attempt=0、next=transaction_timestamp()并清lease；相等
  才可写completed_evidence_revision=claimed并完成为CONFIRMED_TRUE/CONFIRMED_FALSE/WAITING_HISTORY/
  WAITING_EVIDENCE。技术失败时若required>claimed同样只回PENDING不计旧失败，否则attempt<8→RETRY、
  attempt=8→DEAD+Case。普通row_version不匹配仍拒绝；唯一例外是token未变且受限requeue只提高required
  revision，此时以上required>claimed分支负责收口；
- evidence/scope/历史变化只能经 `requeue_favorite_observation_v2(...)`。数据库helper
  `favorite_observation_evidence_fingerprint_v1` 对completion snapshot revision、关系current/history摘要、有效
  HISTORY scope epoch/vector和rule version做canonical JSON v1 SHA-256；同fingerprint重放no-op，变化时将
  required_evidence_revision+1并写新fingerprint/audit。WAITING_HISTORY、WAITING_EVIDENCE、CONFIRMED_TRUE、
  CONFIRMED_FALSE、PENDING或RETRY可转PENDING/两种WAITING并重置attempt；EVALUATING不抢占lease，只提高
  required revision，当前owner随后必走required>claimed分支，不能提交旧结论。CONFIRMED_TRUE 离开确定态时，若有当前奖励，则同事务把 attribution改为
  AWARDED_PENDING_EVIDENCE而不冲正；
- DEAD 只能经
  `recover_favorite_observation_v2(command_id,source_region,source_appoint_id,expected_observation_revision,
  expected_dead_generation,expected_row_version,reason)` 回 PENDING、attempt=0、
  next_attempt_at=transaction_timestamp()并保留dead_generation。幂等key=`recover-favorite-observation:v2:{command_id}`
  永不过期，请求hash按上述参数顺序加protocol version canonical计算；函数先处理同request成功响应并返回
  REPLAYED，再锁精确observation行校验DEAD/generation/row_version。异request同command拒绝，首次条件过期报
  `FAVORITE_OBSERVATION_RECOVERY_STALE`。响应丢失后即使该观察再次DEAD，同command也不触碰新generation；
  必须新command和新expected值。Case直到后续真正成功才恢复。任意非终态可由 Correction Service 的受限
  invalidate/void函数转 INVALIDATED/VOIDED；若有当前奖励须同事务冲正。INVALIDATED/VOIDED 无出边；
- 同状态 UPDATE 只有 EVALUATING heartbeat或上述 reducer的 evidence row_version推进合法；其他直接写、旧
  row_version/lease、非法边分别报 `FAVORITE_OBSERVATION_STATUS_TRANSITION_INVALID/
  FAVORITE_OBSERVATION_LEASE_MISMATCH/FAVORITE_OBSERVATION_RECOVERY_DENIED`。

字段组合由 CHECK/deferred Trigger 固定：只有 EVALUATING 可有 lease且 next_attempt_at为空；PENDING/RETRY
必须无 lease且 next_attempt_at非空（RETRY attempt=1..7且有 last_error）；DEAD 无 lease/next、attempt=8且
有 Case/dead_generation；WAITING_* 无 lease/next且 error_code分别指向缺 HISTORY/缺业务时间；CONFIRMED_*
无 lease/next/error且 relation_state与 true/false一致；INVALIDATED/VOIDED 有终止 reason/time且无可领取字段。
EVALUATING必须有claimed_evidence_revision；其他状态为空。非EVALUATING确定/等待状态必须
completed_evidence_revision=required_evidence_revision；PENDING/RETRY可大于completed。reaper发现required>claimed
时不计超时失败，直接清lease并回PENDING，避免证据并发变化被误记第8次。

#### `course_favorite_attributions`

- PK：`(source_region, teacher_id, student_token)`，保证同一师生终身最多一个当前获分归因；
- 部分唯一：`(source_region, source_appoint_id) WHERE status IN ('AWARDED','AWARDED_PENDING_EVIDENCE')`，一课最多一个当前获分归因；师生 PK下这两态也共同代表唯一当前奖励；
  已 `REVERSED` 的历史行不阻塞完课教师纠错后的新归因；
- 字段：课程 ID、完课参与序号、`AWARDED/AWARDED_PENDING_EVIDENCE/REVERSED`、hold_reason、分值 5、规则版本、`award_generation>=1`、当前积分流水 ID、上次冲正流水 ID、重算原因、版本和时间，以及当前 generation 的
  `materialization_origin=LEGACY_REUSED/CUTOVER_CREATED/V2_LIVE`、条件可空 `materialized_by_run_id` 和
  `award_projection_generation>=0`。origin/run/projection generation 与 award_generation 一起冻结，只在下一 generation 可重写。
- 每个归因同时记录 `observation_revision`；完课纠错不得把新观察错误关联到旧观察证据。
- deferred constraint trigger 在事务结束校验每条 AWARDED：teacher_id/completion_participation_seq 必须等于
  `source_courses` 当前冻结 completion 指针并指向同教师的 COMPLETION participation；其
  `(source_region,source_appoint_id,observation_revision)` 必须引用该课仍为 CONFIRMED_TRUE 的观察，且观察
  teacher/participation 与归因一致。REVERSED 历史不受当前指针约束。非法直写或 TRANSFER 中只改一侧均
  整笔拒绝。

`AWARDED_PENDING_EVIDENCE` 仍计入当前分数并与 AWARDED共同占用师生/课程当前奖励唯一约束；它必须引用同
教师/参与的 PENDING/EVALUATING/RETRY/DEAD/WAITING_HISTORY/WAITING_EVIDENCE 观察，hold_reason精确为
REVALIDATION_PENDING/WAITING_HISTORY/WAITING_EVIDENCE，不能另选课程或新加流水。
证据恢复为 CONFIRMED_TRUE 时原 generation/流水回 AWARDED；恢复为 CONFIRMED_FALSE、VOID/TRANSFER明确
失效时才写唯一冲正并转 REVERSED。RETRY/DEAD 期间继续 hold原5分，DEAD建技术 Case但不凭技术失败扣分；
恢复原观察后再作真假决定。这样“边界暂时未知不猜测扣分”和 attribution FK 可同时成立。

每次观察或关系历史纠错后，从所有 `CONFIRMED_TRUE` 观察中按 `observed_at ASC`，再按该 appoint topic
冻结 ID 类型的 canonical 值 ASC（NUMERIC 用 PostgreSQL 任意精度 numeric，TEXT 用 UTF-8 bytes）选最早；
observed_at 不得为空，两个 typed ID 列必须按 type 恰有一个非空，并建立对应领取/候选索引。禁止直接按
varchar `source_appoint_id`、DTS 到达或 INSERT 顺序决胜。
课程。若当前归因为 AWARDED_PENDING_EVIDENCE，选择器必须等待其引用观察重新确定，不得把“暂时没有
CONFIRMED_TRUE候选”当作 false、不得改选另一课。证据确定后，已有 AWARDED归因相同则无写入；候选明确
改变时先写原流水冲正，再给新候选加分；证据完整且明确没有候选时才冲正。
每次从无当前奖励或 REVERSED 再进入 AWARDED 时 `award_generation+1` 并新增 append-only 流水；不得复用
已冲正流水。奖励幂等键为
`favorite:{region}:{teacher}:{student}:{appoint}:obs{observation_revision}:gen{award_generation}:{rule_version}`，
冲正键为 `favorite-reversal:{original_score_entry_id}`，并写 `reversal_of_score_entry_id`；数据库对
非空 `reversal_of_score_entry_id` 建唯一约束，保证一条奖励最多冲正一次。

SCORE_GRADUATION 换版若改变收藏组件单价或规则身份，当前 AWARDED 与 AWARDED_PENDING_EVIDENCE 都先唯一
冲正旧流水、award_generation+1，再按新规则追加奖励；held 行保持 held、同一观察和hold_reason，只更新本代
分值/规则/流水。REVERSED 不重开。换版重放不得重复冲正或奖励，账户与资格在同一发布事务重建。

业务生效时间：

| 来源 | 生效规则 |
|---|---|
| favorite INSERT | `add_time`；缺失时 source_timestamp 只作为确定性排序/当前态更新时间，并标 `SOURCE_MISSING`，不得证明历史时点真值 |
| favorite UPDATE | 相同来源 ID 的历史修正：撤销 before 版本并按 after.add_time 重建，不把到达时间当新收藏周期；after.add_time 为空时边界为 SOURCE_MISSING |
| favorite DELETE | 失效时间为 DTS `source_timestamp`；该值为空时失效边界为 SOURCE_MISSING，不能用到达/处理时间替代；若后续提供权威删除业务时间，再以新规则版本重建 |
| blacklist INSERT/UPDATE | 起点取 `valid_start_time`，为空取 `add_time`，再为空取来源时间；终点取 `valid_end_time`；永久标志优先 |
| blacklist DELETE | 在 DTS `source_timestamp` 失效，同时保留删除前历史区间 |

“普通变化”和“历史纠错”的边界固定为：业务生效时间是否早于已经执行的 `observed_at`。早于或等于观察时刻时重算、补分/扣分并重选；晚于观察时刻时只更新当前关系，不改变既有获分归因。取消后重新收藏的业务生效时间晚于旧观察时刻，因此不会开启第二个获分周期。

favorite 的 `add_time` 缺失时，即使 source_timestamp 位于 observed_at 之前，也不能形成
`CONFIRMED_TRUE/CONFIRMED_FALSE` 或收藏积分；到期观察写
`WAITING_EVIDENCE + SOURCE_MISSING:FAVORITE_EFFECTIVE_TIME_MISSING`。后续同来源 ID 补齐/纠正权威
`add_time` 时，重新置 PENDING 并按该业务时间重评；这与 HISTORY scope 不完整导致的
`WAITING_HISTORY` 分开。只有关系边界时间均为 `CONFIRMED` 且 HISTORY 覆盖完整，才可确认第 24 小时真值。
UPDATE after.add_time 或 DELETE source_timestamp 缺失同样适用：当前来源记录可按 CRUD 更新，但所有可能被
该未知边界跨越的到期观察进入 WAITING_EVIDENCE，不新加分，也不因猜测时间冲正既有分；边界补齐后再按
真实业务时点补分或扣分。

### 4.7 `dts_source_scope_states`

用于证明集合是否完整，避免把未触达当作 false。

`dts_source_table_publish_generations` 是所有 scope candidate 的表级串行头：

| 字段 | 约束/含义 |
|---|---|
| `source_region,source_table` | PK；只允许白名单 region/table组合 |
| `current_generation` | bigint，>=0；初始0 |
| `current_snapshot_id` | generation=0 时 NULL；>0 时复合 FK 到同 region/table、COMPLETE且 published_generation=current_generation 的 snapshot |
| `active_candidate_snapshot_id` | 可空 FK；同表任意 GLOBAL/TEACHER scope 唯一正在装载的 candidate |
| `candidate_owner,candidate_lease_token,candidate_lease_expires_at` | 与 candidate ID 全空或全非空；lease token每次 begin/takeover随机唯一，防 ABA |
| `row_version,updated_at` | 乐观锁与审计时间 |

expand migration 为 DOM/OVS 各白名单表显式 Seed generation=0 的唯一行；缺/重复即
`SOURCE_TABLE_GENERATION_HEAD_MISSING/CONFLICT`。普通角色无写权限，只有以下 Scope Coordinator受限函数：

- `begin_source_snapshot_candidate_v2(...)` 锁 head，要求无 active candidate，创建 LOADING snapshot并冻结
  base_generation/current snapshot/source token，写 owner/token/lease和 row_version；
- `heartbeat_source_snapshot_candidate_v2(snapshot_id,owner,lease_token,expected_row_version)` 只允许仍为
  LOADING/VERIFYING 的同 owner续租至配置上限，不改 base/input；旧 token/过期后 heartbeat拒绝；
- `abort_source_snapshot_candidate_v2(...)` 在显式失败/取消时把 snapshot置 FAILED、写错误/audit并同事务清
  candidate四字段；`takeover_expired_source_snapshot_candidate_v2(...)` 只对 lease过期行执行同样失败关闭后，
  返回可重新 begin，旧 token永远失效；
- publish函数要求同 token/owner、candidate=VERIFYING、head generation=base，原子写 diff/membership/scope、
  snapshot COMPLETE+published_generation=base+1、head current_generation/current_snapshot更新并清 candidate。
  中途失败全部回滚仍保留 candidate；调用方随后 abort或续租重试。提交成功但响应丢失时，若 head已指向该
  COMPLETE snapshot且 generation/hash全同则 no-op；任何部分/异 hash为
  `SNAPSHOT_PUBLISH_STATE_CONFLICT`。

head/candidate正常成功、失败、取消、超时 takeover都必须有唯一释放路径；不能手工清指针。函数 ACL、Trigger
和 deferred FK保证 current_generation、snapshot manifest与 head 同一事务，不存在 generation推进但 snapshot
未 COMPLETE 的窗口。

| 字段 | 约束/含义 |
|---|---|
| `source_region, source_table, scope_kind, scope_level, scope_key` | PK；kind=`CURRENT/HISTORY`，level=`GLOBAL/TEACHER`；GLOBAL 的 key 固定 `*`，TEACHER 的 key 为 canonical teacher_id |
| `state` | `INCOMPLETE/LOADING/VERIFYING/COMPLETE/FAILED/STALE` |
| `active_snapshot_id` | 可空 FK；最后一次成功原子发布的 epoch。STALE/LOADING/VERIFYING/FAILED 时仍保留，供读取旧 current 和回退比较，但不代表 scope 完整 |
| `candidate_snapshot_id` | 可空 FK；当前装载尝试。FAILED 时保留失败 epoch 供审计；开始下一次尝试时原子替换为新 epoch，不覆盖旧 epoch 行 |
| `completed_at, invalidated_at, row_version` | scope 当前状态的可审计字段 |

`dts_source_scope_snapshots` 以全局唯一 `snapshot_id` 为 PK，同时保存完整 scope 复合键，并对
`snapshot_id + scope 复合键` 建 FK/唯一约束，防止跨 scope 借用。字段至少包括
`epoch_state=LOADING/VERIFYING/COMPLETE/FAILED/STALE/SUPERSEDED`、`snapshot_as_of`、
`snapshot_consistency_token/snapshot_fence_vector`（上游一致性快照事务/token及其经适配器验证的 broker
next-offset 映射）、`partition_offsets`（每项含 source_partition_epoch_id/topic/partition/start_next_offset/
end_next_offset）、`row_count/content_hash`、HISTORY 的
`history_from/history_through`、`base_publish_generation`、条件非空且按
`region+source_table` 唯一的 `published_generation`、`generation_diff_count/generation_diff_hash`、错误码、创建/
验证/完成/失败/失效时间。每个 staging key 另冻结开始装载时的
`base_source_row_revision/base_row_hash`。每条 SNAPSHOT_DIFF version 必须保存相同
`source_table_publish_generation`，并以 `(region,source_table,published_generation,snapshot_id)` 复合 FK 指向
该 COMPLETE snapshot；这就是 generation→diff 的可查询 manifest，禁止按 created_at 或 snapshot ID 猜代次。
epoch 证据不再存放在 scope state
单指针行里；`dts_source_snapshot_rows.snapshot_id` 必须 FK 到本表，发布后禁止改写该 epoch 的 staging 行。

本文所有 checkpoint/source fence 向量（scope partition_offsets、row version covered-through、H0、handoff H、
shadow run source fence）统一使用上述带 epoch_id 的 next-offset entry，并复合 FK 到 BROKER epoch registry；
同一 entry 不得跨 epoch，键为 `(region,epoch_id,topic,partition)`。reset/recreate 若跨 epoch，旧 scope 先
STALE，新 snapshot 以新 epoch 单独起段；需要审计两代时向量显式保存两个 segment，不能只保留 topic/
partition 后反查“当前 ACTIVE”。

物理状态转换冻结如下：

1. 首次装载在同一事务创建 LOADING epoch，并把 scope 从 `INCOMPLETE` 改为 `LOADING`、
   `candidate_snapshot_id=新 epoch`，此时 `active_snapshot_id=NULL`；
2. 已 COMPLETE scope 发起刷新或收到订阅重置/offset 回退时，先把 scope 与 active epoch 标为 STALE，
   保留 active 指针和旧 current；再创建新的 LOADING epoch，把 candidate 指向它并进入 LOADING；
3. 校验开始时 candidate epoch 与 scope 同时进入 VERIFYING；任一步失败时 candidate epoch 与 scope 同时
   进入 FAILED，active 指针不变，失败 staging/epoch 保留且绝不成为 active；
4. `FAILED/STALE→LOADING` 必须新建 snapshot_id。新事务只替换 candidate 指针，旧 FAILED epoch 继续
   独立留痕；禁止复用失败 ID 或把失败 staging 补写成下一轮；
5. 发布事务锁定 scope 行并校验 `row_version + candidate_snapshot_id + candidate epoch=VERIFYING`。在同一事务
   完成 replacement diff、current/membership 切换、candidate epoch→COMPLETE、旧 active epoch→SUPERSEDED、
   `active_snapshot_id=candidate_snapshot_id`、`candidate_snapshot_id=NULL`、scope→COMPLETE；任一步失败整笔回滚；
6. scope 只有 `state=COMPLETE` 且 active epoch 本身为 COMPLETE 时才提供完整性证明。其他状态可以读取旧
   current 作运营展示或 diff 基线，但不得从空集合推导 false/0。即使旧 active 是合法空集，也由其 epoch
   行和 active 指针证明，不能依赖 membership 是否有行反推。

每次转换都以旧 row_version 做乐观锁并递增 row_version，写审计和对应 SOURCE_SCOPE revision。

新 snapshot 不是新一轮 INSERT。publisher 在 staging 中把 snapshot 与 fence 后全部已提交
CDC/SNAPSHOT_DIFF 版本合成为 desired set，
校验通过后按 canonical source_key 与当前 active epoch 做 replacement diff，并在发布 COMPLETE 的同一事务：

1. key 存在、canonical row_hash 不变且 current 已有合法 v2 provenance：只把 membership snapshot baseline 指向本轮 candidate（在同一
   发布事务成为新的 active_snapshot_id），并清除已被 end vector 覆盖的旧 CDC overlay，不写语义 Outbox，
   不新增 participation/关系事件；
2. 新 key：写 `SNAPSHOT_DIFF/SNAPSHOT_INSERT`；appoint 首参与 phase=SNAPSHOT_DIFF，assigned_at 保持
   NULL/SOURCE_MISSING，不能把 snapshot_as_of 伪装成真实指派时间；
3. 同 key 行变化：写 `SNAPSHOT_DIFF/SNAPSHOT_UPDATE`，before=旧 active row、after=新 row；例如 B→A
   按状态机创建下一参与，但转换业务时间/关系边界为 SOURCE_MISSING；
4. GLOBAL desired set 缺少旧 key：写 `SNAPSHOT_DIFF/SNAPSHOT_DELETE` tombstone，before=旧 row；课程、排课、
   证书、关系等从 active 集合移除但历史保留，完课课程仍走纠错 Case；
5. TEACHER desired set 缺少旧 membership：只把该 teacher scope membership 的 snapshot_is_present=false，
   并清除已被 barrier 覆盖的旧 overlay，不能据此 tombstone 全局 source row；该教师集合重算必须按
   effective_is_present 排除它。若其他/全局 scope 后续证明
   行已删除或改属，再由对应 diff 更新 source current；
6. legacy current 若尚无非空 source_row_revision/版本复合 FK，即使 canonical row_hash 与 desired 相同也不得
   走“不变”分支；首个验证通过的 snapshot 先强制写
   `SNAPSHOT_DIFF/SNAPSHOT_BOOTSTRAP_PRESENT`（before=NULL、after=规范化 legacy current、revision=1）或
   `SNAPSHOT_BOOTSTRAP_TOMBSTONE`（revision=1）。若 desired 与该 legacy 状态不同，再在同一 publish generation
   顺序写正常 `SNAPSHOT_INSERT/UPDATE/DELETE` revision=2，以 bootstrap 状态作为 before；例如 legacy A→B
   固定为 r1 bootstrap A、r2 UPDATE A→B，legacy tombstone→present 固定为 r1 bootstrap tombstone、r2 INSERT。
   bootstrap 只建立 H0 current provenance，assigned/effective time 一律 SOURCE_MISSING，不伪装业务时间；
7. replacement diff 使用保留 topic `__snapshot_diff__:{snapshot_id}:{source_table}`（snapshot_id 已唯一关联
   scope level/key），partition=0。ordinal 的范围固定为每个 `(snapshot_id,source_table)` 本次最终实际需要写
   SNAPSHOT_DIFF 的 distinct canonical source_key 集合（不包含不变 key、BASELINE-only key或只改 membership 的
   TEACHER 缺行）；按 source_key 固有类型排序：NUMERIC 任意精度升序在前，TEXT 按 UTF-8 bytes 升序在后，
   同一 source_table 的 key 类型不得漂移，然后取 1-based `row_number()`，因此 ordinal>=1。单步 diff 固定 `diff_step=1,offset=2*ordinal-1`，
   bootstrap 后还需正常 diff 时固定 step1 为 bootstrap/奇数 offset、step2 为正常 diff/偶数 offset。唯一键为
   `(snapshot_id,source_table,source_key,diff_step)`；operation与 step组合由 CHECK 限定，重跑不得重复或交换步骤。

普通 Ingestor 已应用的 fence 后 CDC 只凭其唯一 source_row_revision 进入领域状态机一次；candidate replay
只改 staging。发布时仅 desired 与锁内 live current 的剩余差异生成后续 revision 的 SNAPSHOT_DIFF；首次
空 active 且尚未被 CDC 建立的 desired key 也走 `SNAPSHOT_DIFF/SNAPSHOT_INSERT`，原始 BASELINE 只作
快照证据。FAILED/STALE 恢复期间旧 active epoch
继续可读但其完整性结论失效；candidate 未原子发布前不能与旧 current 混用，也不能提前更新 active 指针。
为消除 GLOBAL/TEACHER 两个不同 source fence相互覆盖，同一 `(region,source_table)` 在任一时刻只允许一个
LOADING/VERIFYING candidate，不按 scope 放宽。唯一 `begin_source_snapshot_candidate_v2(...)` 锁
`dts_source_table_publish_generations`，要求 `active_candidate_snapshot_id IS NULL`，冻结 current generation并
CAS写 candidate id/owner/lease；另一 scope 必须等待。owner 崩溃后只有受限 takeover函数可在 lease过期时把旧
candidate置 FAILED、清指针并写 audit，staging仍不可改；禁止两个 candidate交错发布后再按时间猜先后。

快照切换固定为：上述 begin函数冻结 base generation与 staging 每键 base revision/hash → 由源库一致性导出取得不可变 `snapshot_consistency_token`，并由版本化适配器
验证/冻结 token 对应的 broker next-offset fence；目标 PostgreSQL 时间与源库时间不得拿来证明因果覆盖 →
加载该 token 的同一 snapshot → candidate 从其 fence 只读重放 CDC → 取得 source-table exclusive publish
lock并锁 generation 行 → 要求 current generation仍等于 base且 active candidate仍是自己，读取锁内 end vector
并继续重放 CDC至该向量 → 校验行数/hash与逐键 revision barrier → 同一事务按 live current 生成剩余 diff、
切 membership、把 table publish generation 恰好 +1，把该代写入 snapshot及全部 DIFF并独立重算
generation_diff_count/hash 后 COMPLETE。GLOBAL/TEACHER 发布都必须走这一串行点。
若任一 live key 的 revision/hash高于或不同于 candidate 已重放证据、generation 不再等于 base、candidate owner/
lease不一致、token/fence 验证失败，报
`SNAPSHOT_CANDIDATE_STALE/PUBLISH_GENERATION_GAP/SNAPSHOT_FENCE_UNVERIFIED`，整次不写 diff；禁止以旧 desired
覆盖新 current。TEACHER scope 的缺 membership 仍只影响该 scope，不生成全局 tombstone。任一步失败不得
发布，也不得继续沿用上一轮快照证据；Ingestor 的 CDC current 写必须先取同一锁的 shared 模式。

同一教师的有效 scope 选择固定为：存在 TEACHER 行时始终使用该行，哪怕它是非 COMPLETE；只有不存在
TEACHER 行时才回退 GLOBAL。TEACHER COMPLETE 必须包含自己的 snapshot/fence/校验证据，不能借 GLOBAL
补齐。这样 GLOBAL COMPLETE 与 TEACHER STALE 并存时结果仍为 STALE/SOURCE_MISSING，不允许选择更有利状态。

TESOL、处罚、评价等只有 CURRENT scope COMPLETE 时才允许从空集合推导 false/0。收藏历史还要求
HISTORY scope 覆盖 `[该师生最早 completion_end_time, observed_at]`；只有当前快照没有历史覆盖时，
观察固定为 WAITING_HISTORY/PENDING_DATA，不能猜测补分。

### 4.8 白名单表、来源键和依赖键

本轮有效业务来源仅允许以下表：

- DOM 专有：`dom_teacher`、`dom_complaint_cate`、`dom_teacher_absent_reason`、
  `dom_teacher_certification`、`dom_teacher_class_schedule`、`dom_teacher_penalty`；
- DOM/OVS 区域表：`*_appoint`、`*_complaint`、`*_grading_label`、`*_grading_label_log`、
  `*_teacher_blacklist`、`*_teacher_favorite`、`*_user_complaint`、`*_user_teacher_grading`、
  `*_qa_task_close_camera_record`；

`*_qa_ac_classroom_record`、`*_qa_task_fake_early_leave_record` 是退役控制路由：收到后只记
`IGNORED_RETIRED_SOURCE` 的表名、操作和位点并推进 checkpoint，不保存 before/after、来源行或业务事实。
这不表示它们仍是业务白名单或字段来源。

上述有效业务来源的主键固定为 `id`。NULL/缺失 id 整批失败；UPDATE 前后 id 改变整批失败。
数字 id 规范化为无指数十进制文本，文本 id 按源值 UTF-8 精确保留、不 trim；
`source_key_data={"id":<typed canonical value>}`，`source_key` 为该 canonical id 文本。

依赖键固定为：appoint→COURSE+TEACHER；课程子表→COURSE，含 teacher/t_id 时另加 TEACHER；
label log→COURSE+LABEL；label 字典→LABEL；favorite/blacklist→TEACHER_STUDENT；
schedule/certification→TEACHER。complaint 自身的 COURSE 键使用投诉地区，但
`complaint_type/complaint_type_child/complaint_type_grandson` 每个
非空分类 ID 都依赖 `(source_region=dom,key_type=COMPLAINT_CATEGORY,category_id)`；不得生成 ovs 分类键。
`dom_complaint_cate` INSERT/UPDATE/DELETE 先置脏该全局 DOM 分类键，再按 before/after ID 反查
`source_course_complaints` 中 DOM、OVS 两区所有未删除且上述任一级引用该 ID 的行，把各自行的
`(source_region,source_appoint_id)` COURSE 键置为 PENDING。typed complaint 必须为三个分类 ID 建反查索引，
因此 OVS 投诉先到、DOM 字典后到也能自动恢复，不允许全表无界扫描或只唤醒 DOM。
UPDATE 外键变化时 before/after 两侧都置脏，DELETE 使用 before 置脏。

### 4.9 v2 对账影子表

切换前 shadow 不消费、不修改生产 `outbox_events`。新增：

- `source_wide_v2_reconciliation_runs`：PK run_id，保存 rule_version、完整 source fence vector、domain/TASK_PLAN/
  task/output/score/qualification revision vector、active scope epoch/fence vector、14 项 active task template
  vector、teacher copy version、唯一 PUBLISHED complaint rule import SHA、
  `evaluation_as_of`、由其计算的 `evaluation_business_date_beijing`、
  `RUNNING/COMPLETE/FAILED`、各结果类型 count/hash、开始/完成时间；
- `source_wide_v2_shadow_cursors`：PK `(run_id,result_type)`，保存按 canonical key 全量扫描的 last_key、
  row_count/hash 和状态，作为独立断点，不复用 Outbox status；
- `source_wide_v2_shadow_results`：PK `(run_id,result_type,result_key_hash)`，保存 canonical result key、
  预期 payload、payload_hash 和计算时间。

run 创建即冻结全部 input fence/vector、evaluation 时间、规则/模板/copy版本和 mutex 标志，禁止 UPDATE。
状态只允许 `RUNNING→COMPLETE|FAILED`；COMPLETE/FAILED 无出边。RUNNING 期间 result 只能 INSERT：同
run/type/key 且相同 canonical key/payload hash 重放 no-op，不同则 `SHADOW_RESULT_CONFLICT` 并使 run
FAILED；结果禁止 UPDATE/DELETE。cursor 只可单调推进，COMPLETE 后不可回退；run 终态后所有 cursor、
result、count/hash/vector 均不可改删，重算必须新 run_id。cutover 使用 COMPLETE run 前锁 run 并从不可变
results 独立重算各 type count/hash，与 run 冻结摘要逐项一致，否则 `CUTOVER_SHADOW_EVIDENCE_CHANGED`。
cursor 只能由 `complete_shadow_cursor_v2(run_id,result_type)` 置 COMPLETE：函数锁 run/cursor，验证 canonical
terminal key并从该 type results 独立重算 count/hash后一次写入；此后该 type 禁止再 INSERT result或修改
last_key/count/hash。run 只能由 `complete_shadow_run_v2(run_id)` 置 COMPLETE：函数要求固定 result_type 全集
的 cursor 均 COMPLETE，重算所有摘要及 technical readiness 后一次终态提交；普通角色不能直接 UPDATE
run/cursor status。

result_type 固定为下列 14 类；这是 cutover 的最小结果注册表，未登记类型、键或 action 一律失败：

| result_type | canonical key | action |
|---|---|---|
| `TEACHER_WIDE` | teacher_id | UPSERT / RETAIN |
| `LESSON_SCORE` | region + appoint_id | UPSERT / RETAIN / DELETE_CURRENT |
| `LESSON_COMPONENT_SETTLEMENT` | region + appoint_id + completion_seq + component_code | MERGE_AWARDED / MERGE_REVERSED / RETAIN |
| `FAVORITE_OBSERVATION` | region + appoint_id + observation_revision | MERGE / RETAIN |
| `FAVORITE_ATTRIBUTION` | region + teacher_id + student_token | MERGE_AWARDED / MERGE_HELD / MERGE_REVERSED / RETAIN |
| `TRIGGER_MATCH` | dedupe_key | MERGE_ACTIVE / MERGE_SUPPRESSED / MERGE_PENDING / RETAIN |
| `TASK_PLAN` | assignment_dedupe_key | EXISTING_EVENT / CUTOVER_PLANNED |
| `CASE_PLAN` | source_ref | REUSE / CREATE / CANCEL_OPEN / RESTORE_AUTO_CANCELLED / RETAIN_HANDLED |
| `NOTIFICATION_PLAN` | reference_kind + reference_key | REUSE / CREATE / CANCEL_STORED / RESTORE_AUTO_CANCELLED / RETAIN_INTERACTED |
| `SCORE_ACCOUNT` | teacher_id + dimension | REBUILD / RETAIN |
| `SCORE_COMPONENT_ACCOUNT` | teacher_id + component_code | REBUILD / RETAIN |
| `SCORE_ENTRY_PLAN` | idempotency_key | REUSE / ALIAS / AWARD / REVERSE |
| `QUALIFICATION` | teacher_id | MERGE_IRREVERSIBLE / RETAIN |
| `OUTBOX_COVERAGE` | event_id | MATERIALIZED / EXPLICIT_EMPTY / SUPERSEDED |

每条结果保存 action、目标表版本化 typed state 和 `ABSENT` 或精确旧行 hash 前置条件；APPLY 必须先锁目标并
校验前置条件，禁止无条件 UPSERT。`SCORE_ENTRY_PLAN` 中 REUSE 只验证既有不可变流水，ALIAS 只用于唯一可证明
的 legacy 供给里程碑，AWARD/REVERSE 只追加奖励/冲正；固定任务分在 cutover 只允许 REUSE。`ALREADY_APPLIED`
是同 run 重放的执行结果，不是 shadow action。key/payload/count/hash 继续按本节 canonical JSON 规则由数据库
重算，具体 typed state 直接复用对应目标表 schema，不在本手册重复抄一份字段表。
`LESSON_SCORE.DELETE_CURRENT` 只允许在 v2 明确没有当前 completion（如已批准 VOID）且对应逐课可逆奖励已经
冲正时删除当前结果；历史参与、settlement 和 score_entries 仍保留，不允许写一行无 completion 的零分结果。
Case/提醒的 CANCEL 只允许作用于 OPEN/STORED；RESTORE 只允许恢复由来源纠错自动取消且尚未人工处理/阅读的
原行。已处理 Case、READ/CLICKED 提醒只能 RETAIN，不得因来源恢复回退状态或另建同 source_ref 输出。
shadow 只计算 canonical 输出键和预期结果，不生成生产 assignment/case/notification/score entry ID，
不写任务、Case、通知、积分或资格。只有所有
cursor COMPLETE 且 count/hash 校验通过，run 才可 COMPLETE；同一 fence 的 COMPLETE run 才能作为
cutover 输入。

run 创建时只读取一次数据库 `clock_timestamp()` 并冻结为 `evaluation_as_of`；断点恢复、所有 cursor、shadow
结果和最终生产 materialization 都把它作为显式函数参数，禁止内部再次调用 now/clock_timestamp 决定业务。
favorite 是否到期固定比较 `observed_at <= evaluation_as_of`；NEW/EXISTING、30 天窗口和日更状态固定使用
`evaluation_business_date_beijing`；最终 cutover 首次补缺 assignment 的 eligibility 与 due 计算基点用
evaluation_as_of，`assigned_at` 仍取成功 INSERT 的数据库 transaction_timestamp()，因为它表示实际可读时间；
正常异步 Planner 则只用 TASK_PLAN 冻结的 eligible_since_at，
技术 created_at 可用实际写入时间但不得进入计划 hash。最终 cutover 只能使用锁内该 run 的同一时点。
“实际提交时间”不能在提交前充当边界，补跑协议固定如下：全量切换主事务把
`dts_pipeline_control.time_catchup_status=PENDING,time_catchup_run_id=run_id,time_catchup_from=evaluation_as_of`
与 mode/read route同事务提交；API和所有生产物化 Worker在该状态下保持维护/fail closed。主事务提交后，协调器
仍持 intake+cutover session locks，另开事务把每个未来 `course_favorite_observations` 保持为可由
`observed_at` 索引自动领取的 PENDING（它本身就是耐久队列），并对 `(evaluation_business_date_beijing,
current Beijing date]` 中每个跨过的业务日、每位教师 UPSERT `dts_dirty_keys`：
`source_region=dom,key_type=TEACHER_TIME_RECHECK,key_part_1=teacher_id,key_part_2=YYYY-MM-DD`。该复合键唯一，
只由 SourceWide Worker领取，复用 dirty-key retry/DEAD/Case，不由 Domain Projector领取。

enqueue 事务以自己的 `clock_timestamp()` 写 `time_catchup_through`，独立 readback teacher/date expected count/hash
和全部 future/due observation count/hash后才把 status=COMPLETE；同键重放 no-op。主事务提交后协调器崩溃时，
启动门禁看到 PENDING，必须重新取得两把锁并从冻结 from 到当前时刻幂等补齐，不能先恢复服务。释放锁前
再次确认 catchup through 的北京业务日等于当前业务日；之后的新到期观察由 PENDING索引领取，下一日期转换
由正常 00:05 以同一 TEACHER_TIME_RECHECK键写入。rollback/再次 cutover 复用相同协议。

### 4.10 Pipeline control 与生产读路由

`dts_pipeline_control` 单例除 §9 的 mode/H0/H 外，还保存 time-catchup字段；`dts_projection_read_routes` 单例
`route_id=PRIMARY` 保存 `active_projection=V1_COMPAT/V2`、固定 `route_contract_version`、row_version、
switched_by_run_id、switched_at。deferred constraint固定映射：
`V1_COMPAT_DUAL_CAPTURE|ROLLED_BACK→V1_COMPAT`，`V2_PRIMARY→V2`，且 mode、route、全量物化 audit、
cutover/rollback run必须在同一事务；time_catchup=PENDING 时两种 route都只返回维护错误，不开放半完成数据。
control 另保存 `projection_generation>=0`：初始化0；每次进入 V2_PRIMARY（含再次 cutover）恰好+1；rollback
保持该值不回退。任何 serving v2行的 serving_projection_generation必须等于当前 V2_PRIMARY generation；
ROLLED_BACK/V1_COMPAT不允许新 v2 serving物化。

部署阶段预先创建不可变版本分支
`teacher_scorecard_v1_compat_v1/teacher_scorecard_v2_v1` 与
`teacher_lesson_score_v1_compat_v1/teacher_lesson_score_v2_v1`。稳定公开视图
`teacher_scorecard_current/teacher_lesson_score_current` 只按 route单例 `UNION ALL` 选择一个分支；运行切换
禁止 `CREATE OR REPLACE VIEW`。lesson两分支列/键完全一致，均以
`source_region+source_appoint_id+participation_seq` 一条参与一行；v1 compatibility分支用 v2参与事实承载
缺席/普通参与，只把旧生产课程分挂到冻结 completion参与，避免 rollback退回“一课一当前老师”丢行。

唯一 `switch_dts_projection_mode_v2(run_id,expected_control_version,expected_route_version,target_mode)` 仅供
cutover/rollback角色在 exclusive mutex内调用；函数校验 final run/全量结果、CAS control和route并写 audit。
普通应用只有稳定视图 SELECT，无 route表 UPDATE或底层分支直读权限。事务失败 mode/route/data全回滚；提交
响应丢失时同 run/target/version与 audit全同返回 no-op，异 run或部分状态报 `PROJECTION_ROUTE_CONFLICT`。

## 5. 子表集合选择器

每张业务子表都先进入 `dts_source_rows`，再从完整未删除集合重算。禁止“本事件直接覆盖宽表字段”。

投诉规则只读取唯一 `status='PUBLISHED'` 的 `complaint_rule_imports` 及其规则行。该表原始文件内容不可变，
但有受控 `DRAFT/PUBLISHED/RETIRED` 发布状态；数据库对 PUBLISHED 建全表唯一部分索引。发布新 SHA 必须取得
cutover shared mutex 与 complaint-rule-catalog exclusive advisory lock，在同一事务校验该文件内 normalized
三级分类唯一、RETIRE 旧 SHA、PUBLISH 新 SHA、
写 publication audit，并按旧/新规则差异唤醒两区所有受影响 complaint COURSE aggregate。禁止按 created_at
或无 ORDER BY 的任意行选规则。typed complaint、match/evidence 均冻结 `complaint_rule_id + source_sha256`；
最终 shadow 把 active SHA 纳入 revision vector，锁内换版使 run 失败重跑。

唯一发布入口固定为受限
`publish_complaint_rule_import_v2(source_sha256,expected_revision,idempotency_key)`（`source_sha256` 即
`complaint_rule_imports` PK）：无旧 current
时 DRAFT→PUBLISHED；有旧 current 时同事务旧 PUBLISHED→RETIRED、新 DRAFT→PUBLISHED、audit、两区差异
fan-out。RETIRED终态，current PUBLISHED不得单独退役/删除，普通角色不能直接写 status；PUBLISHED/RETIRED
import identity、文件/hash及全部 child rule rows不可 UPDATE/DELETE。expected revision/SHA不符报
`COMPLAINT_RULE_PUBLICATION_CONFLICT`。同 idempotency key+request hash在发布已完整提交后返回原结果；异参、
半状态或并发双初发/换版失败，不得复活旧 SHA。Trigger/ACL拒绝绕函数发布、单退役和 RETIRED→PUBLISHED。
每个曾激活版本另存唯一 `activation_generation`：首版为1，之后在同一catalog锁内按全局最大值+1；DRAFT为
NULL，PUBLISHED/RETIRED不可改。发布 fan-out 的 CATALOG_REVISION 固定使用全局 identity
`COMPLAINT_RULE_SET/ACTIVE_COMPLAINT_RULE_SET`，revision=activation_generation、version_id=source_sha256、
payload_hash=content_hash，禁止拿每行自己的 publication_revision 当跨版本顺序。
每条规则的物理身份固定为
`rule_id=complaint-rule:{lowercase full source_sha256}:{source_row_number}`；数据库同时约束
`UNIQUE(source_sha256,source_row_number)`、`UNIQUE(rule_id,source_sha256)`，并校验 rule_id 可由这两个字段
确定性重建。同一 SHA 重放得到同一 rule_id，新 SHA 的规则可与 RETIRED 旧 SHA 同时保留且不会主键冲突。
`source_course_complaints` 以及保存投诉规则证据的 match 必须以
`(complaint_rule_id,source_sha256)` 复合外键引用同一规则版本；禁止把新 active SHA 与旧 rule_id 拼成一条
合法 typed 事实。没有精确规则时两字段必须同时为空，不能只写其中一个。
同一 import 的 raw_rows、解析后的 `complaint_category_rules`、row count/content hash 在发布前必须一致；一旦
import 进入 PUBLISHED 或 RETIRED，其规则行由 Trigger 禁止 UPDATE/DELETE。修正规则只能导入新 SHA 并走
原子发布，不能在 active SHA 不变时原地改变 severity/route。
所有 complaint typed 投影/重算事务读取 active 规则前先取同 catalog shared lock，event/result 冻结 active
SHA；提交前以 active SHA CAS 复核。这样发布的 exclusive lock 会等旧读事务结束，发布后旧 SHA 结果不能
压回新结果。锁顺序同样为 cutover mutex → catalog lock → aggregate/course 行锁。

所有“最新/最大”选择器统一使用显式降序比较器：每个日期/时间先按来源类型严格解析，非法值视为
NULL；每一项均按“非 NULL 在前、值 DESC”比较（等价 `DESC NULLS LAST`），再以来源表固定类型的
canonical id DESC、`source_row_revision` DESC 决胜。数字 ID 按数值比较，文本 ID 按 UTF-8 字节序比较，
不得把数字字符串和文本规则混用。所有时间都为 NULL 时仍由 id/source_row_revision 唯一选出结果；
业务规则要求时间定位且时间全缺失时，不套该兜底，而是返回对应 `SOURCE_MISSING/PENDING_DATA`。

| 来源 | 当前结果选择器 | UPDATE/DELETE |
|---|---|---|
| `teacher_absent_reason` | 先按下述时间规则映射到唯一 `participation_seq`，再按 `(add_time,canonical id,source_row_revision)` 降序取该参与的第一条；即使 reason_type 为空也压住旧记录 | 重算 before/after 两个课程+教师键及受影响参与；删除最新后恢复次新 |
| `teacher_penalty` | 先按下述规则映射到唯一 `participation_seq`；所有已唯一映射且未删除记录都进入证据集合：`appeal_status=2` 为明确不生效、NULL 为 unknown、其他非空值才计算 late/early | UPDATE/DELETE 重算 before/after 映射；true 优先，删除一条后按该参与剩余完整集合重算 |
| DOM grading | 每课从未删除且 `is_del` 为 NULL/0 的记录按 `(update_time,create_time,start_time,dt,canonical id,source_row_revision)` 降序取第一条；不按 status 过滤，再按 use_point 分类 | 最新记录变未知时清除贡献；DELETE 后恢复次新记录 |
| grading label log | 按来源 log ID 保存，课程集合按 label_id 去重；展示名按 `(create_time,dt,canonical id,source_row_revision)` 统一比较器取最新 log | 删除一条不影响同标签其他 log；删除最新名称后恢复次新 |
| teacher certification | 教师未删除证书当前集合做 EXISTS | 删除一张证书后重算剩余集合 |
| complaint | 每课所有有效记录形成集合供 `EXISTS` 计数；展示与任务/Case/提醒路由按 `(add_time,course_date,id,source_row_revision)` 统一降序比较器只选最新有效记录 | 删除/失效最新记录后恢复次新，并抑制旧路由 match、激活/新建次新路由；字典缺失保持待重算 |
| user_complaint | 本轮仅保存 source version/current 供审计，不参与 complaint current，不补分类、不形成指标/任务/积分 | 三种操作都不产生业务脏键或 v2 Outbox；只推进 ingest checkpoint |
| close camera | 每课未删除记录 EXISTS 即 true | 删除一条后检查其他记录；完整 scope 为空才为 false |
| retired QA | 不进入 source versions/current 或领域事实；账本只记 `IGNORED_RETIRED_SOURCE` 路由元数据并推进 checkpoint | INSERT/UPDATE/DELETE 相同 |

处罚计算固定为：`appeal_status=2` 表示该记录明确不生效；NULL 表示未知且不得在 WHERE 中过滤；
其他非空值才计算。
`is_late = in_time - lesson_start_time > 30 秒`；`is_early = lesson_start_time + 30 分钟 - out_time > 30 秒`。
等于 30 秒不算异常。任一所需时间缺失/非法时该记录结果为 NULL/SOURCE_MISSING。late 与 early 分别
聚合：任一记录为 true 则结果 true；没有 true 但存在 `appeal_status=NULL` 或计算时间未知的记录则结果
NULL/SOURCE_MISSING；否则只有 scope COMPLETE 时结果 false，此时允许集合为空或全部记录均为
`appeal_status=2`，也允许所有生效记录均明确 false。scope 非 COMPLETE 且没有 true 时仍为 NULL，
不能把“尚未触达”当作空集合。

处罚到参与的定位规则固定为：

1. 只看同课程且 `teacher_id=t_id` 的参与；`t_id` 缺失时写
   `PENDING_DATA:PENALTY_PARTICIPATION_TEACHER_MISSING`，不得复制到当前教师；
2. 若课程已有冻结完课参与且其 teacher_id 与 t_id 相同，直接定位该 completion participation；这是
   A→B→A 时 seq=1/seq=3 的第一决胜依据；
3. 否则严格解析 penalty.lesson_start_time 作为定位业务时间，选择唯一满足
   `assigned_at <= lesson_start_time < ended_at` 的参与，ended_at 为空视为正无穷；边界采用左闭右开；
4. lesson_start_time 缺失/非法、参与区间证据不完整或命中数不等于 1 时，保留来源行并写
   `PENDING_DATA:PENALTY_PARTICIPATION_AMBIGUOUS`，不得落到同教师所有参与；
5. appoint 新版本、首次完课冻结、完课 TRANSFER/UPDATE/VOID、penalty UPDATE/DELETE 都重跑映射；
   before/after 指向的旧参与和新参与均置脏。只有唯一映射成功的集合才能写该参与 late/early。

DOM grading 分类：

| use_point | 好评 | 差评 | 其他 |
|---|---|---|---|
| `buy` | score 4/5 | score 1/2 | 不产生评价分类 |
| `free` | type=`satisfactory` | type=`unsatisfactory` | 不产生评价分类 |
| 其他/NULL | 无 | 无 | 清除该当前记录以前产生的分类 |

投诉有效条件固定为：

```sql
complaint_type = 13
AND (complaint_type_grandson IS NULL OR complaint_type_grandson <> 82)
AND approve = 'y'
AND validity = 1
```

## 6. 缺席、任务和纠错生命周期

### 6.1 缺席原因与计数

- `absent_cnt` 只统计 `participation_status='t_absent'` 的参与；缺席原因本身不额外增加 absent。
- `no_notice_cnt` 只统计已经是 `t_absent` 且最新 `reason_type='No Notification'` 的参与。
- `reason_type='Unfilled Lesson Memo'` 不增加 `no_notice_cnt`。
- 原因早到时先保留；只有对应参与成为 `t_absent` 后才产生任务命中和 no-notice 贡献。

同一教师可能在 A→B→A 中出现多次，来源原因却只有 `appoint_id+t_id`。映射到参与的确定规则为：

1. 原因业务时间取 `add_time`，缺失时取来源 `source_timestamp`；
2. 只看同课程、同教师、最终为 `t_absent` 的参与；
3. 优先选择 `ended_at <= 原因业务时间` 中 `ended_at` 最大的参与；
4. 若原因早到、尚无上述参与，则选择 `ended_at > 原因业务时间` 中 `ended_at` 最小的参与；
5. 时间相同按 `participation_seq` 小者决胜；仍无法唯一定位时不写原因/计数/任务，保留
   `PENDING_DATA:ABSENCE_PARTICIPATION_AMBIGUOUS`；
6. 新 appoint 版本形成参与后重新执行映射，因此原因早到与晚到结果一致。

### 6.2 两层幂等键

```text
命中证据 dedupe_key =
  absence:{rule_code}:{source_region}:{source_appoint_id}:{participation_seq}

教师任务 dedupe_key =
  personalized:{task_code}:{teacher_id}
```

映射：

- `Unfilled Lesson Memo` → `P-REL-MEMO`；
- 其他非空 reason_type → `P-REL-ATTENDANCE`；
- 空 reason_type → 不创建缺席任务。

原因 UPDATE/DELETE 后：

1. 旧 `personalized_trigger_matches` 改为 `SUPPRESSED`，保留审计；
2. 如果新原因命中另一规则，新增另一条证据 match；
3. 已创建的 `task_assignments` 不删除、不取消、不回退，未完成任务继续保留，已完成任务保持终态；
4. 同类原因以后再次命中时，把同 dedupe_key 的 match 重新激活并增加 match_revision，复用教师级 assignment；
5. 任务行存在只证明任务事实已创建，不证明提醒、通知或外部动作已送达。

### 6.3 重复差评标签任务

只使用 DOM 当前评价明确为 `NEGATIVE` 的源课程。课程当前标签从 `source_course_labels` 按
`label_id` 去重，`label_name` 只作展示。教师归属固定为课程当前冻结完课参与；未冻结完课的课程
先保留评价和标签事实，不计阈值。

同一冻结完课教师、同一 `label_id` 命中至少 2 个不同
`(source_region,source_appoint_id)` 时创建 `P-FB-NEGATIVE`。幂等键为：

```text
每课标签命中 = negative-label:{teacher_id}:{label_id}:{source_region}:{source_appoint_id}
教师标签任务 = personalized:P-FB-NEGATIVE:{teacher_id}:{label_id}
```

阈值计算只靠 label_id。达到阈值后，先把全部当前贡献课程按上述 seed 元组排序；任一贡献课程的当前
`label_name` 为空时，不能从 `grading_label` 字典补名，也不能跳过空名课程改选另一课程，暂不创建
assignment，只建 `dedupe_key=negative-label-name-missing:{teacher_id}:{label_id}`、
`trigger_code=PENDING_NEGATIVE_LABEL_NAME_MISSING`、`output_type=PENDING_DATA`、
`match_status=PENDING_DATA`、`output_id=NULL` 的 trigger match。缺失课程集合语义变化复用该行并
`match_revision+1`；完全补齐时置 SUPPRESSED。名称补齐后抑制该
pending match，再按正常每课 match/assignment 物化。若非空名称集合分别映射到不同
`teacher_execution_variant`（照片与通用流程冲突），写
`PENDING_DATA:TASK_SEED_VARIANT_CONFLICT` 并停止物化，不得任取一个。该冲突事实固定使用
`dedupe_key=negative-label-variant-conflict:{teacher_id}:{label_id}`、
`trigger_code=PENDING_NEGATIVE_LABEL_VARIANT_CONFLICT`、`output_type=PENDING_DATA`、
`match_status=PENDING_DATA`、`output_id=NULL`；evidence 保存按 canonical seed 顺序排列的最小
`{course key,label_name,teacher_execution_variant}` 集合和 teacher copy version。同一集合重放不增 revision；
冲突内容变化、冲突消失或再次出现均复用该行并 `match_revision+1`，消失时置 SUPPRESSED。空名优先：存在
空名时只激活 name-missing match，variant-conflict match 若已存在则 SUPPRESSED；全部非空后再判变体冲突。
全部名称落到同一执行变体后，才由
canonical 最小课程 match 提供冻结 why/title/evidence。名称后来再次为空时抑制当前课程 match、恢复
pending match，但已创建 assignment 仍保持不可变。非空中文名称仅保存在 evidence；教师端 title 使用
已批准英文映射，未命中映射时用固定英文 `Negative Feedback - Feedback Pattern`。两个照片变体仍只按
已确认的精确原始名称识别，不做模糊或字典补值。

PENDING_DATA 是该 assignment key 的原子 blocker，不能只写一条被 Planner 忽略的旁路记录。出现空名或
variant 冲突的同一聚合事务必须：激活唯一 pending match、把该 key 全部正常
`NEGATIVE_LABEL_COURSE` match 转为 SUPPRESSED、递增一次 TASK_PLAN revision并得到
`materializable=false`，再写新 plan Outbox；这样所有旧可物化 event 均被 superseded。恢复时反向原子
SUPPRESS pending match、按当前完整集合重新激活正常 match（各自 match_revision+1）、递增 TASK_PLAN
revision并恢复 materializable=true。assignment 已存在时仍不删除/改写。禁止让 pending blocker 与任何
正常 active course match 同时提交；数据库 deferred trigger 按 assignment_dedupe_key 校验这一互斥。

评价、标签 DELETE/UPDATE 或完课归属纠错后从完整当前集合重算：不再成立的 match 改为
`SUPPRESSED`；同一课程标签以后恢复时重新激活原 match 并增加 match_revision。assignment 不删除、
不取消、不回退，终态保持。标签改名不改变身份，也不改写已创建 assignment 的冻结标题。只有共享
任务契约列出的两个精确原始标签可以冻结授课环境照片执行变体。

### 6.4 投诉、拉黑与非任务输出

满足 §5 有效条件的投诉要进入任务/Case/提醒四路输出，还必须有冻结完课教师、非空
`complaint_type_grandson` 并精确命中 `complaint_category_rules`。grandson=NULL 仍计有效投诉指标，但只保留
PENDING_DATA、不进入输出。路由顺序和稳定 rule code 固定如下，先命中的分支结束本条分类路由：

同课多条有效投诉只对 §5 统一比较器选出的“当前最新有效投诉”产生输出；全部有效集合仍用于
`feedback_complaint_cnt/feedback_valid_complaint_cnt` 的课程 EXISTS。删除/失效最新记录后恢复次新记录，
旧分类 match 按 lifecycle 抑制，次新分类 match 重新激活或首次创建；不得同时为同课多条投诉各建输出。

教师端 title 只从已发布高影响配置
`teacher_personalized_copy/cfg-teacher-personalized-copy-v1` 读取；物理 key、`schema_version=1` 精确 JSON
Schema/Seed/canonical hash 以配置中心契约和共享任务契约 §3.2 为准。`P-FB-COMPLAINT` 命中映射写 `General Complaint - {mapped English}`，未命中固定写
`General Complaint - Complaint Category`；不得直接外显中文或任意来源英文。evidence 冻结 config version、
lookup key、mapping_hit、分类 ID/原名/rule ID，全部进入 canonical seed payload hash。配置换版只影响以后
首次创建的 assignment；shadow/cutover 必须使用同一 active config revision。该 key 缺失、重复或 schema/hash
非法时不得回退 Python 常量：TASK_PLAN 写
`materializable=false,blocker_code=TASK_COPY_CONFIG_MISSING|TASK_COPY_CONFIG_CONFLICT` 并保留 active match，
不建 assignment；健康检查/final shadow/cutover失败关闭。合法 copy 发布在同一 catalog事务 fan-out全部 active
且无 assignment 的键，产生新 plan revision并解除 blocker。

| 条件 | rule_code | output |
|---|---|---|
| `category_l2='出席问题'` | `TR-REL-ATTENDANCE` | `P-REL-ATTENDANCE` 教师任务 |
| `category_l2='网络设备问题'` | `TR-QUALITY-NETWORK-EQUIPMENT` | task-less 站内提醒 |
| 其他且 `severity_rank IN (0,1)` | `TR-FB-SEVERE-COMPLAINT` | `SEVERE_COMPLAINT` Ops Case |
| 其他且 `severity_rank IN (2,3,4)` | `TR-FB-GENERAL-COMPLAINT` | `P-FB-COMPLAINT` 教师任务 |

四个分支的 match 键统一为：

```text
complaint:{rule_code}:{source_region}:{source_appoint_id}:{completion_participation_seq}:{complaint_type_grandson}
```

输出键固定为：

```text
出席任务 assignment = personalized:P-REL-ATTENDANCE:{teacher_id}
一般投诉 assignment = personalized:P-FB-COMPLAINT:{teacher_id}:{complaint_type_grandson}
严重投诉 case key   = complaint-case:{rule_code}:{source_region}:{source_appoint_id}:{completion_participation_seq}:{complaint_type_grandson}
网络提醒 source_ref = complaint-notification:{rule_code}:{source_region}:{source_appoint_id}:{completion_participation_seq}:{complaint_type_grandson}
```

新建 v2 Case/notification 的物理 ID 分别固定为 `v2case:{SHA-256(case key)}`、
`v2notif:{SHA-256(source_ref)}`；从 v1 精确迁移的输出保留原物理 ID，以唯一 canonical `source_ref`
对账，不能为追求新 ID 复制一行。投诉失效或改路由时旧 match 转 SUPPRESSED：教师 assignment 仍保留；
严重投诉 Case 仅在 `status=OPEN` 时转 `CANCELLED` 并写
`external_action_status=SOURCE_EVIDENCE_SUPERSEDED`，已进入处理或其他终态不自动回退，只追加审计；
提醒仅在 `status=STORED` 时转 `CANCELLED` 并追加 notification event，已 READ/CLICKED 的历史不改写。
同键恢复时重新激活原 match；仅由来源纠错自动 CANCELLED 的 Case/提醒恢复原行，Case 增加
case_revision，提醒追加恢复 notification event；人工终态、已处理 Case 和已读提醒不重新打开、不重复创建。所有状态变更与 match 在同一 Worker
事务提交，任务行、Case 或提醒存在都不能被表述成外部动作已经执行。

完课 TRANSFER 时旧 completion_seq 的投诉 match 全部抑制：旧 OPEN Case、STORED 提醒按上述规则取消，
已处理/终态 Case 和已读/已点击提醒保留原教师及审计。新 completion_seq 产生新的 match 和 case/source_ref
键：任务类为新教师复用或创建其 assignment，Case/提醒类为新教师建立新输出；不得原地把已处理输出改 owner。

未开摄像头提醒单独使用 `TR-QUALITY-CAMERA-OFF`。QA 来源行一到即进入课程事实，不等待 end；但该表
没有教师字段，task-less 提醒在课程尚无冻结完课教师时保持 WAITING_DEPENDENCY，首次冻结后才物化给
该教师，不把“当前 t_id”猜成事件教师：

```text
match      = camera-off:TR-QUALITY-CAMERA-OFF:{source_region}:{source_appoint_id}:{completion_participation_seq}
source_ref = camera-notification:TR-QUALITY-CAMERA-OFF:{source_region}:{source_appoint_id}:{completion_participation_seq}
物理 ID    = v2notif:{SHA-256(source_ref)}
```

摄像头当前集合从 true→false 时抑制 match，STORED 提醒转 CANCELLED 并追加来源纠错事件；已读/已点击
历史不回退。false→true 时重新激活原 match；仅自动取消且尚未读的原提醒恢复 STORED 并追加恢复事件，
不新建提醒。完课归属 TRANSFER 时旧 completion_seq 的 match 被抑制；旧提醒仅在 STORED 时取消，已读/
已点击历史保留。新 completion_seq 使用新 match/source_ref 为新冻结教师幂等建立提醒，不能原地改旧提醒
的 teacher_id。

一般投诉 `P-FB-COMPLAINT` 只在课程已有冻结完课教师、三级分类 ID 非空且精确命中
`complaint_category_rules` 的 P2/P3/P4 时产生：

```text
每课分类 match = complaint:{rule_code}:{source_region}:{source_appoint_id}:{completion_participation_seq}:{complaint_type_grandson}
教师分类任务   = personalized:P-FB-COMPLAINT:{teacher_id}:{complaint_type_grandson}
```

`complaint_type_grandson IS NULL` 仍按已确版条件计入有效投诉指标，但因无法选择具体改善分类，只在
`source_course_complaints` 和 `source_course_fact_current` 写
`complaint_evidence_status=PENDING_DATA, complaint_error_code=COMPLAINT_CATEGORY_MISSING`；不创建
`personalized_trigger_matches`、assignment、Case 或提醒。分类补齐后清 error 并正常路由；再次变 NULL
时抑制旧分类 match/output 并恢复 typed PENDING_DATA。投诉失效、分类/严重度变化或完课归属纠错时，
旧 match 置 SUPPRESSED；同一键恢复时重新激活并增加 revision。assignment 一经创建不删除、不取消、
不回退；分类名称变化不改变 ID 身份和既有标题。

`P-FB-BLACKLIST` 按当前师生拉黑关系中不同 student_token 数量计算，不要求完课：

```text
阈值 match = blacklist-threshold:{source_region}:{teacher_id}
教师任务   = personalized:P-FB-BLACKLIST:{teacher_id}
```

首次达到 2 人时物化 assignment；降到 2 人以下时抑制 match，但 assignment 保留；再次达到阈值时重新
激活原 match 并增加 revision，不开启新的阈值周期。DOM/OVS 同一教师 ID 的关系不跨 source_region 合并；
教师任务按全局 teacher_id 复用同一 assignment。assignment.evidence_snapshot 只冻结首次物化时的地区
贡献，创建后不可修改；后续地区贡献只写各地区 match.evidence_snapshot 和 audit。需要当前贡献时按未
抑制 match 聚合，不能回写 assignment 证据。

### 6.5 完课归属纠错

首次 end 后出现以下任一变化，Domain Projector 在 V2_PRIMARY 的同一冲突事实事务创建或更新一个
`COURSE_COMPLETION_CORRECTION` Case，并把其 ID 回填课程指针：

- `t_id` 改变；
- status 离开 end 或再次进入 end；
- `end_time`、课程日期或学生键改变；
- appoint DELETE。

Case ID 固定为：

```text
course-completion-correction:{source_region}:{source_appoint_id}
```

普通 DTS 处理结果为“来源事实已处理 + 纠错 Case 待决”，不是 FAILED，也不是静默 IGNORED。冻结完课教师、已结课程分和资格暂不变化。

冲突判定只比较冻结快照中的教师、status=end、end_time、学生、课程日期/时间和 Peak 输入与来源当前态。
`conflict_fingerprint` 使用这些规范化字段及当前 appoint `source_row_revision` 计算。Case payload 保存
`case_revision`、冻结快照、来源现状、fingerprint、`latest_source_revision`、审计 source_position 和目标参与。
若 fingerprint 的 revision 已由 `conflict_resolved_against_revision` 覆盖，重算不得重复开 Case；只有更大
source_row_revision 产生新的未处理
差异时，才把同一 Case 重新置 OPEN 并将 case_revision+1。

允许的运营决定只有：

决定写入只在 `dts_pipeline_control.mode=V2_PRIMARY` 开放；`V1_COMPAT_DUAL_CAPTURE/ROLLED_BACK` 期间 Case
可以查看、补充人工备注，但提交决定统一返回 `CORRECTION_PROJECTION_MAINTENANCE`，不落 decision、不改冻结
参与或积分。这样兼容期和回滚期不会出现 v1/v2 两个积分 owner；进入 V2_PRIMARY 后仍按同一 Case revision提交。

| decision | 效果 |
|---|---|
| `KEEP_FROZEN_COMPLETION` | 保持当前 completion 与分数；把截至本次 `conflict_resolved_against_revision` 的全部 PENDING_CORRECTION 参与改 REJECTED_CORRECTION；写 `completion_conflict_status=RESOLVED_KEEP`，记录已处理 source revision 与审计 position，Case 关闭 |
| `UPDATE_COMPLETION_SNAPSHOT` | 仅允许保持原 completion_participation_seq 和教师不变，按决定 payload 更新当前 completion 的 end_time/student/课程日期时间/Peak 快照，role 继续为 COMPLETION；受影响逐课组件先冲正后重结，旧收藏观察 INVALIDATED，并只在教师、student、end_time 完整时插入 revision+1 PENDING 观察；`assignment_source_row_revision<=expected_source_revision` 的 PENDING_CORRECTION 全改 REJECTED；写 `completion_conflict_status=RESOLVED_UPDATE`。首次 initial snapshot 和历史资格不变 |
| `TRANSFER_COMPLETION` | 明确指定一个目标 participation_seq；同一事务把目标改 COMPLETION、旧完成（如有）改 SUPERSEDED_COMPLETION，其余 `assignment_source_row_revision<=expected_source_revision` 的 PENDING_CORRECTION 全改 REJECTED_CORRECTION；更新当前 completion 快照，冲正旧教师并给新教师重结；旧观察改 INVALIDATED；仅当新冻结教师、student_token、completion_end_time 都完整时插入 revision+1 的 PENDING 观察，否则不建观察并保留 SOURCE_MISSING；写 `completion_conflict_status=RESOLVED_TRANSFER`；初始快照和历史资格不变 |
| `VOID_COMPLETION` | 同一事务把旧完成（如有）改 VOIDED_COMPLETION、把 `assignment_source_row_revision<=expected_source_revision` 的全部 PENDING_CORRECTION 改 REJECTED_CORRECTION，清空当前 completion/逐课结果并冲正课程积分，当前观察改 VOIDED、当前归因改 REVERSED 并写 completion_voided_at；写 `completion_conflict_status=RESOLVED_VOID`；初始快照和历史资格保留 |

每个决定必须有唯一 `decision_id`、操作人、理由、时间、`expected_case_revision`、
`expected_conflict_fingerprint`、`expected_source_revision` 和只作审计的 `expected_source_position`。事务先
锁定 Case/课程并要求前三项及当前 source revision 精确一致；position 仅校验结构合法并留存，不作为跨
CDC/SNAPSHOT_DIFF 的并发边界。任一正式条件不一致返回稳定错误 `STALE_CASE_REVISION`，不插入 decision、
不改参与/快照/观察/积分。
重复提交同一 decision_id 且请求 hash 相同返回原结果，同 ID 不同参数报幂等冲突。
决定事务同时写 `conflict_resolved_against_revision`、审计 position 和最终 conflict status，避免同一差异
反复开 Case。决定只收口 `assignment_source_row_revision<=expected_source_revision` 的 PENDING 行；决定
事务之后更大 source_row_revision 产生的新教师参与仍按
PENDING_CORRECTION 进入同一 Case 的下一 revision，不能被旧决定提前拒绝。

决定事务还必须在完成所有同步冻结/观察/积分写入后，锁定并递增该课程的 COURSE aggregate revision，
以及每个 role/教师发生变化的 PARTICIPATION aggregate revision，用 §3.1 标准 event_id 写
`source_wide.changed.v2` Outbox；payload 的 changed_fields 至少包含 decision_id、decision、旧/新
completion_participation_seq、受影响 participation_seq 与 old/new teacher_id。任一 Outbox 写失败时整个决定
事务回滚；同 decision_id 幂等重放不得再次递增 revision 或新增事件。COURSE 事件是投诉、摄像头、差评、
课程展示和教师聚合的强制 fan-out 根，PARTICIPATION 事件刷新参与视图/相关教师聚合。Worker 复用正常
课程重算函数：抑制旧 completion 键的 match/output、为新 completion 键物化输出，并用既有稳定键防重；
不得由 Correction Service 越权直接写这些输出，也不得等待下一条 DTS 才刷新。

决定 API 提交后先返回 `APPLIED_PENDING_PROJECTION`；只有 `projection_event_ids` 全部 PUBLISHED 后，
`ops_decisions.downstream_projection_status` 才转 PUBLISHED。任一事件 DEAD_LETTER 则转 DEAD_LETTER 并建
运营告警，但不回滚已经提交的决定；修复后按原 event_id 重放，不能生成第二份任务、Case、提醒或流水。
普通应用数据库角色不能直接改冻结字段或积分流水。VOID 后普通 DTS 再次出现 end 也不能自动重新冻结，
必须由新的 TRANSFER 决定明确恢复当前 completion。

### 6.6 技术失败 Case

技术失败统一进入 `ops_cases`，但只表示“系统工作待恢复”，不等于教师违规、任务已发出或外部动作已执行。
类型、身份与来源引用冻结如下；`case_id=v2case:{SHA-256(source_ref)}`：

| case_type | 唯一 `source_ref` | 触发点 |
|---|---|---|
| `DTS_DIRTY_KEY_DEAD` | `tech-case:dts-dirty:{SHA-256(canonical dirty key)}:g{dead_generation}` | 同一 dirty key 第 8 次暂态失败进入 DEAD |
| `DTS_SOURCE_CONFLICT` | 有 key/revision 时 `tech-case:dts-source-conflict:{region}:{table}:{SHA-256(source_key)}:r{source_row_revision}:{error_code}`；无 revision但 epoch 合法时 `...:{region}:{epoch_id}:{topic}:{partition}:{offset}:{error_code}`；无法形成合法 epoch/event identity 时固定为 `...:ingest:{ingest_issue_id}`，当前错误集合只进 revisioned payload | 非法位置、主键变化、before 冲突或 CURRENT COMPLETE 后仍无法证明首参与 |
| `FAVORITE_OBSERVATION_DEAD` | `tech-case:favorite-observation:{region}:{appoint}:r{observation_revision}:g{dead_generation}` | 同一观察第 8 次暂态失败进入 DEAD |
| `TASK_MATERIALIZATION_DEAD` | `tech-case:task-plan:{task_plan_aggregate_id}:r{aggregate_revision}` | TASK_PLAN Outbox 第 8 次失败进入 DEAD_LETTER |
| `DOWNSTREAM_PROJECTION_DEAD` | `tech-case:projection:{event_id}` | 任意非 TASK_PLAN 的 `source_wide.changed.v2` Outbox 第 8 次失败；若关联完课纠错，另同步 decision projection status |

dirty key 与 favorite observation 新增 `dead_generation>=0`：每次从非 DEAD 首次进入 DEAD 加一；人工恢复后
同一 generation 的重复失败/重放只能更新同一 Case。source conflict 由 source revision 或唯一 broker event
天然分代；TASK_PLAN/投影由 aggregate revision/event_id 分代，不再另造 generation。
非法 epoch 的 `ingest_issue_id` 由接入器用受保护 connector delivery identity（cluster/subscription、
stream_generation/epoch_opening 若可用、topic/partition/offset）hash、payload HMAC与 HMAC key version做 canonical
SHA-256，不含错误码；A→B 错误变化只递增同一 issue/Case revision；
不得使用到达时间或原始 payload。connector delivery identity 缺失时整批保持未 ACK并报
`INGEST_ISSUE_ID_UNAVAILABLE`，不能跨 generation 猜同一 Case。

上述 Case 的 `teacher_id/source_region/source_appoint_id` 均条件可空：只有能从当前已保护事实精确推出时
才填，严禁猜教师；`priority=P1,status=OPEN,external_action_status=NOT_REQUESTED`，payload 只含错误码、
canonical key hash、attempt/generation/revision、首次/最近失败时间和安全诊断摘要，不放原始学生 ID、原始
payload、堆栈或密钥。相同 source_ref 重试幂等 no-op；诊断证据变化只使同一行 `case_revision+1` 并追加
audit，不重开第二行。

工作项首次转 DEAD/DEAD_LETTER/SOURCE_CONFLICT 与对应 Case UPSERT、case audit 必须由上表唯一 owner 在
同一数据库事务提交；非法 envelope 虽不写 source version/checkpoint，也必须把脱敏 ingest issue 与技术
Case 原子提交后保持“不 ACK”。Case 写失败则工作项/失败状态转换回滚，不能出现不可见的 DEAD。普通重试
次数未到 8、WAITING_DEPENDENCY/HISTORY/EVIDENCE 不创建技术 Case。

恢复动作必须重放原 dirty key/observation/event_id，而不是直接改派生结果或新造事件。DEAD→PENDING 只算
开始恢复，不算成功；技术 Case 在原 event 真正 PUBLISHED/原工作完成前不得转 RESOLVED。原工作成功后，Case
仍为 OPEN 时原子转 `RESOLVED` 并写 recovery evidence；若人工已置 `IN_REVIEW`，系统只追加恢复证据，由
运营显式关闭，不能越过人工处理。成功恢复后再次进入新的 dead generation/revision 才允许新 Case。
所有技术 Case 都不得自动创建 teacher assignment、notification 或任何“已送达/已执行”记录。

## 7. 教师聚合与课程积分

### 7.1 计数身份

课程 Peak 使用来源 `week + lesson_local_time`，不做 UTC、节假日或 DST 推断：week 1–5 为工作日，
0/6/7 为周末；DOM 每日 18:00–21:30，周末另含 09:00–11:30；OVS 每日
18:00–23:30 或 00:00–05:30，周末另含 09:00–11:30，边界均包含。week/time 缺失或非法时
`is_peak=NULL/SOURCE_MISSING`。

| 字段 | 冻结含义 |
|---|---|
| 系统 `source_course_cnt` | 去重 `(source_region,source_appoint_id)` 的非删除源课程数，不进入教师宽表 |
| `total_booked_cnt` | 该教师未 source_deleted 的普通参与行数；所有源 status 包括 on/cancel/其他/NULL 都计 1；PENDING_CORRECTION 不计 |
| `peak_booked_cnt` | 上述未删除参与中 `source_courses.is_peak=true` 的数量 |
| `total_completed_cnt` | 冻结完课参与数量 |
| `peak_completed_cnt` | 冻结完课且 Peak 的参与数量 |
| `absent_cnt` | `t_absent` 参与数量 |
| `late_cnt/early_cnt` | 冻结完课参与中对应处罚集合明确为 true 的数量 |
| `anomaly_cnt` | 按参与去重的 absent/late/early 并集 |
| `perfect_cnt` | 冻结完课、late=false、early=false 且两项来源集合均完整的数量 |
| `first_completed_student_cnt` | 冻结完课教师按 student_token 去重 |

A→B 时系统源课程数为 1；A、B 的 `total_booked_cnt` 各增加 1；A 的 absent 增加 1；只有首次 end 冻结教师增加 completed。

空值规则：late/early 任一为 NULL 时 `is_perfect=NULL`、本课完美分为 0、证据为 `SOURCE_MISSING`。不能用 `is not true` 判完美。

### 7.2 子事实与教师归属

- 子事件一到即保存到源课程，不等待 end。
- 有明确 teacher/t_id 时同时关联对应参与，找不到时等待重算，不复制给当前教师。
- 好评、收藏、完美、Peak、硬件等课程积分只在首次 end 后结算给冻结完课参与。
- 投诉、评价、QA 的课程级教师聚合也以冻结完课教师为准；显式 teacher_id 只作为证据指向，不改变“本课积分归完课教师”。
- 缺席和缺席任务归各自 `t_absent` 参与教师，不归完课教师。

### 7.3 比率

```text
reliability_absent_rate      = absent_cnt / total_booked_cnt
reliability_late_rate        = late_cnt / total_completed_cnt
reliability_early_leave_rate = early_cnt / total_completed_cnt
reliability_late_early_rate  = distinct(late OR early participation) / total_completed_cnt
feedback_praise_rate         = feedback_praise_cnt / feedback_total_eval_cnt
feedback_negative_rate       = feedback_negative_cnt / feedback_total_eval_cnt
feedback_eval_rate           = feedback_total_eval_cnt / total_completed_cnt
feedback_complaint_rate      = feedback_valid_complaint_cnt / total_completed_cnt
capacity_peak_slot_rate      = peak_slot_cnt / total_slot_cnt
capacity_key_slot_day_rate   = peak_slot_days / slot_days
```

分母为 0 或证据不完整时结果为 NULL。收藏、拉黑不再要求完课，原 `feedback_favorite_rate` 和 `feedback_block_rate` 没有同口径分母；本轮固定返回 NULL、不参与计分或资格，不允许继续除以完成学员数。

### 7.4 评价、收藏和硬件积分

- DOM 当前评价为好评且课程已冻结完课：完课教师获得 5 分；差评只保留事实，不扣分。
- 收藏归因 `AWARDED` 或 `AWARDED_PENDING_EVIDENCE`：冻结完课教师当前均保留 5 分；后者只表示历史证据待重验，不新增流水。
- `is_perfect=true`：冻结完课教师获得 4 分。
- Peak 完课：冻结完课教师获得 2 分。
- 摄像头、CPU、网络三项都明确 false：获得 2 分；任一 true 为 0 分；任一 NULL 为 0 分且 `SOURCE_MISSING`。
- CPU、网络新来源未接入前固定 NULL，因此当前不得新增硬件 2 分。

#### 7.4.1 逐课组件流水生命周期

除收藏使用 `course_favorite_attributions` 的专用 generation 外，四个逐课组件固定进入
`lesson_score_component_settlements`：

| component_code | 分值 | 成立条件 |
|---|---:|---|
| `FEEDBACK_PRAISE` | 5 | DOM 当前评价为好评 |
| `PERFECT_COMPLETED` | 4 | late=false 且 early=false，两个集合都完整 |
| `PEAK_COMPLETED` | 2 | 冻结 `completion_is_peak=true` |
| `CLASS_QUALITY_HARDWARE` | 2 | 摄像头、CPU、网络都明确 false |

该表 PK 为 `(source_region,source_appoint_id,completion_participation_seq,component_code)`，保存当前教师、
`AWARDED/REVERSED`、`award_generation>=1`、当前分值、规则版本、evidence fingerprint、当前奖励流水 ID、
最后冲正流水 ID、版本和时间，以及当前 generation 的
`materialization_origin=LEGACY_REUSED/CUTOVER_CREATED/V2_LIVE`、条件可空 `materialized_by_run_id` 和
`award_projection_generation>=0`；origin/run/projection generation 与 generation 一起冻结。状态转换固定为：

1. 首次 false/unknown→true：generation=1，写奖励流水并置 AWARDED；
2. AWARDED 且语义/分值未变：无写入；
3. AWARDED→false/unknown，或 scope STALE、VOID、TRANSFER 离开该 completion：按原流水只冲正一次并置 REVERSED；
4. REVERSED→true，或规则版本/分值变化需重结：`award_generation+1`，写新的奖励流水；规则变更时先冲正仍 AWARDED 的旧流水；
5. UPDATE_COMPLETION_SNAPSHOT 只对实际受影响组件执行上述状态机；KEEP 不产生流水。

数据库另对 `(source_region,source_appoint_id,component_code) WHERE status='AWARDED'` 建部分唯一索引，
并由 deferred constraint trigger 校验每条 AWARDED settlement 的 completion_participation_seq/teacher_id
等于 `source_courses` 当前冻结 completion 指针并指向同教师的 COMPLETION participation；REVERSED 历史
豁免。由此同课同组件在任一时点最多一笔当前奖励，TRANSFER 必须在同一事务先冲正旧参与、再写新参与，
不能让两个 seq 同时 AWARDED。

奖励与冲正幂等键固定为：

```text
lesson:{source_region}:{source_appoint_id}:p{completion_participation_seq}:{component_code}:gen{award_generation}:{score_rule_version}
lesson-reversal:{original_score_entry_id}
```

冲正流水必须写 `reversal_of_score_entry_id`；数据库对其非空值建唯一约束。一条原奖励最多冲正一次。
TRANSFER 给新 completion 重新计算：该目标组件从未出现则 generation=1，曾经 REVERSED 则继续递增；
不得复用旧教师/旧 participation 的奖励键。`lesson_score_results`、component settlement、score_entries、
账户和当前资格在同一 Worker/纠错事务提交，历史资格仍不可撤销。

`score_entries` 为 append-only，并新增/回填不可变 `projection_origin`、条件可空
`materialized_by_run_id/projection_generation`。writer→origin矩阵固定为：expand前存量回填
`LEGACY_EXISTING`；expand后旧v1 Worker在V1_COMPAT_DUAL_CAPTURE或ROLLED_BACK写
`V1_COMPAT_LIVE`；Fixed Task Score Settler在任一mode写 `FIXED_TASK_LIVE`；cutover全量首次新建写
`CUTOVER_CREATED`；V2_PRIMARY普通SourceWide写 `V2_LIVE`；rollback事务新写冲正写
`ROLLBACK_CREATED`。CUTOVER_CREATED/ROLLBACK_CREATED必须有对应cutover/rollback run_id，V2_LIVE及前两者
按动作写非空projection_generation；其余origin的run/generation为空。普通writer不能自选origin。

settlement/attribution 的 LEGACY_REUSED 奖励可指向 LEGACY_EXISTING或V1_COMPAT_LIVE流水/canonical alias；
CUTOVER_CREATED/V2_LIVE必须与奖励流水origin/run/generation一致，deferred trigger拒绝错配。
FIXED_TASK_LIVE只允许entry_type=FIXED_TASK_AWARD且task_assignment_id非空，不能被逐课组件/收藏归因引用。
ROLLBACK_CREATED只允许冲正并要求run非空；`reversal_of_score_entry_id`始终精确指向原奖励。rollback选择
可逆集合仍只看settlement/attribution自身origin，不得冲正LEGACY_REUSED或FIXED_TASK_LIVE。

逐课积分结果仍“一节源课程一行”，但必须增加 `completion_participation_seq` 和 `teacher_id`，FK 指向冻结参与；A 的缺席参与不生成第二份同课积分结果。教师端课程列表则按参与行展示，因此 A 可看到 `t_absent`，B 可看到 `end`，只有 B 的行显示本课积分。

#### 7.4.2 供给 40 槽位里程碑

供给分是教师粒度、首次达成后不可逆的 10 分里程碑，不是当前 `peak_slot_cnt × 单价`，也不是任务：

```text
component_code = CAPACITY_PEAK_SLOT_40
idempotency_key = CAPACITY_MILESTONE:CAPACITY_PEAK_SLOT_40:{teacher_id}
score_entry_id = CAPM-{SHA-256(idempotency_key) 前 32 位十六进制}
dimension = CAPACITY
entry_type = MILESTONE_ACHIEVEMENT
reason_code = CAPACITY_PEAK_SLOT_40_ACHIEVED
delta_score = 10
```

只有 `peak_slot_cnt` 证据为 CONFIRMED 且首次达到 `>=40` 时写一条奖励；以后降到 39、scope 失效、排课
off/DELETE 或规则重算都不冲正、不删除，恢复到 40 也不再奖励。当前组件账户以 canonical 流水是否存在
决定 0/10：先查 `score_entries.idempotency_key`，未命中再查
`score_entry_idempotency_aliases.alias_key`；两处命中不同流水为冲突。不能仅从当前 peak_slot_cnt 倒推
已获奖历史。
本轮 `SCORE_GRADUATION` schema v1 把该 component_code、阈值40和分值10设为常量；发布接口拒绝修改。
未来如要改变，必须先另行确版新的里程碑身份和旧奖励兼容规则，不能沿用本键补差或二次发奖。

### 7.5 `teacher_source_wide` 全字段口径

- 身份原值：`tchr_id/real_name/center_type_id/status` 来自教师当前源行；center desc 为
  1→CBT、5→TBT、其他含 NULL→HBT。
- `bu`：`is_full_time` 属于 `{5,6,7,11,19,20,21,22,503}`→HBT，属于
  `{8,10,201}`→OBT，其他→NULL/SOURCE_MISSING。
- `teach_area_type`：course 含 global_cn/global_pool→ovs，其他非空→dom，空→NULL；不再写 dmo。
- 日期：`status_on_date/onboard_date=date(status_on_time)`；off/last_on 同源日期；
  `onboard_30d_end_date=onboard_date+29`。
- `job_days=(status_off_date 或北京业务日期)-onboard_date`，负数/缺日期为 NULL；
  `job_month=floor(job_days/30)+1`。
- `first_booked_dt`：历史上首次普通指派对应课程日期；`first_completed_dt`：历史上首次被接受的完课
  快照日期；`first_open_slot_dt`：历史上首次有效开启 slot 日期。三者都是历史首次事实，普通 UPDATE/
  DELETE、source_deleted、完课 VOID/TRANSFER 或当前集合回退只能发现更早日期，不能清空或移到更晚；
  PENDING/REJECTED_CORRECTION 不产生首次日期。确需修正错误历史值只能走受限数据纠错并留审计。
- booked/completed/absent/late/early/anomaly/perfect/no_notice/first_completed_student 按 7.1；
  feedback_total_eval 为冻结完课且当前 DOM 评价明确好或差的课程数，praise/negative 分别计；
  `feedback_complaint_cnt` 为冻结完课中存在任意未删除 `dom/ovs complaint` 来源行的课程数，
  `feedback_valid_complaint_cnt` 为其中存在至少一条满足 §5 有效条件的 typed complaint 课程数；
  grandson=NULL 或非空 ID 字典缺失仍计后者，只使严重度/L0/输出证据待补；`user_complaint` 两者都不计。
  favorite/block 按当前关系学员去重。
- slot 五字段按 8.1；`capacity_avg_completed_per_day` 固定为入职 0–29 天内冻结完课数/30。
- 所有 rate 按 7.3；favorite/block rate 固定 NULL。
- `is_cpl_tesol` 按 8.2 三值；`is_self_introduce` 本轮无权威来源，保持 NULL。

上述字段由 SourceWide Worker v2 从规范化事实全量重算受影响教师，不允许事件差值把计数减为负数。
三项首次日期各增加 `*_evidence_status=CONFIRMED/CONFIRMED_EMPTY/LEGACY_FROZEN/SOURCE_MISSING`。v2
候选分别从完整参与版本历史、initial/已批准 completion 快照历史、schedule 曾经 status=on 的版本历史取
最小业务日期；不是只扫未删除 current。切换时与同 fence 的 v1 `teacher_source_wide` 旧值取所有非空
合法日期的 LEAST：旧值更早且 v2 无对应历史时保留旧值并标 LEGACY_FROZEN，不得为了“全量重算”覆盖；
两边均空且对应 HISTORY 从 onboard_date 覆盖到 fence 才标 CONFIRMED_EMPTY，否则为 SOURCE_MISSING。

### 7.6 教师端参与列表

`teacher_lesson_score_current` 的唯一行键为
`(source_region,source_appoint_id,participation_seq)`，不再提供含糊的单列 `lesson_id` 作为业务身份。
只展示 role=`NORMAL/COMPLETION` 且非“首次 end 前已删除”的参与；PENDING/REJECTED/SUPERSEDED/VOIDED
纠错行不展示。排序固定为：`scheduled_start_at NULLS LAST, lesson_local_date NULLS LAST,
lesson_local_time NULLS LAST, source_region, source_appoint_id, participation_seq`；
`lesson_sequence=row_number()`、`lesson_count=count(*)` 均在同一教师的可见参与集合内计算。

课程级评价/标签/投诉/QA 事实可在同课所有可见参与行展示；显式 t_id 的处罚和缺席证据只显示在
对应参与。只有行键等于当前冻结 completion 的参与返回 `valid_for_scoring=true` 和本课积分，其他参与
固定 0 分但不能伪装为“没有课程事实”。`is_perfect` 允许 true/false/NULL，NULL 必须连同
`evidence_status=SOURCE_MISSING` 返回。

## 8. 排课、TESOL、教师状态

### 8.1 排课

排课改为“当前有效槽集合”，不累计 on 转换次数：

- 来源行主键为 schedule.id；最新未删除且 `status='on'` 才是当前有效槽；
- UPDATE 到 off 或 DELETE 时从集合移除；重复/无差异 UPDATE 不重复计数；
- `total_slot_cnt/reg_slot_cnt/peak_slot_cnt` 按有效来源行 ID 去重；
- `slot_days/peak_slot_days` 按 date 去重，不等于 slot 次数；
- 这些字段明确属于入职第 0–29 天观察指标，只统计 `date` 在 `[onboard_date,onboard_date+29]` 的槽；
- `first_open_slot_dt` 保留全生命周期首次曾有效开启日期，删除当前槽不回退该历史事实；
- 教师晚到时 schedule 源行保留，教师到达后自动重算。

### 8.2 TESOL

```sql
EXISTS (
  当前未删除证书
  WHERE certification_code = '16'
    AND certification_status = 1
)
```

- EXISTS 为真时 `is_cpl_tesol=true`；
- 对应教师证书 scope 已 COMPLETE 且 EXISTS 为假时写 false；
- scope 未完整时写 NULL；
- 单张无效或删除事件不得在仍有另一张有效证书时覆盖成 false。

### 8.3 教师状态

- `onboard_date = date(status_on_time)`，是 NEW/EXISTING 和 30 天观察窗口的唯一日期依据；
- 北京业务日期 0–29 天且 status=on → NEW；满 30 天且 status=on → EXISTING；off → LEFT；hei → BLOCKED；其他/缺日期 → NULL+SOURCE_MISSING；
- 每日北京 00:05 按业务日期幂等重算，补跑可重复；并发由 PostgreSQL advisory lock 保证单次执行；
- center_type：1→CBT，5→TBT，其他含 NULL→HBT；
- course 包含 `global_cn/global_pool` → ovs，其他非空 → dom；无法判定时 NULL；目标数据不再写 dmo；
- 教师地区变化触发相关课程重算，但不删除课程事实；地区与来源不一致时标 `SOURCE_CONFLICT`，停止该课程新结算直到纠正；
- 教师 DELETE 只给源教师写 tombstone/来源缺失，不级联删除课程、参与、积分历史或已获得资格。

## 9. 迁移与切换顺序

不得清空现有业务事实后直接依赖 DTS 七天窗口重建。固定顺序如下：

cutover 对 PENDING v2 Outbox 的“已被全量覆盖”不按 source_position 猜测，而由最终 run 的
`OUTBOX_COVERAGE` 显式证明。每条 coverage 以 event_id 为 result key，保存 event payload hash、event_type、
aggregate type/id/revision、run input vector hash、coverage state（`MATERIALIZED/EXPLICIT_EMPTY/
SUPERSEDED`）以及完整 impact manifest。唯一纯函数 `expand_impact_closure_v1` 从 final run 冻结输入展开
root，不信任 event 自报 refs；registry 固定为：COURSE=父课程及全部参与/完成教师/师生关系/课程子事实；
PARTICIPATION=其父 COURSE closure；TEACHER=该教师聚合/积分/资格/当前任务计划及其全部参与课程 closure；
TEACHER_STUDENT=该关系、相关收藏课程/拉黑任务及父教师；LABEL=引用该标签的课程 closure 与教师+标签
TASK_PLAN；COMPLAINT_CATEGORY=两区在一/二/三级任一级引用该 category_id 的全部课程 closure；
COMPLETION_CONFLICT=父 COURSE closure 与
纠错 Case；SOURCE_SCOPE=scope 内全部实体及由 confirmed-empty 改变的实体，再按各 root 展开；TASK_PLAN=该
assignment key 的唯一计划/assignment 结果。每个 closure 节点再由同一 versioned reducer registry 映射到
§4.9 的具体 `result_type + canonical result key`。

coverage 保存 `manifest_version=1`、canonical 排序后的
`covered_result_refs(result_type,result_key_hash,outcome=PRESENT|EXPLICIT_EMPTY,payload_hash|null)`、manifest_count
和 manifest_hash；每个预期键都要单独 PRESENT 或 EXPLICIT_EMPTY，不能用一个 aggregate 级空标记代替。
cutover 校验器从 run 输入独立重算 closure，并要求 key 集合、outcome、payload hash、count/hash 完全相等；
refs 输入顺序不影响 canonical hash，少一键、多一键或错误 hash 都失败。所有白名单事件统一要求：event
revision 严格小于 run captured revision 时才可 `SUPERSEDED`；相等时只能逐键
`MATERIALIZED/EXPLICIT_EMPTY`；大于 captured revision 立即失败。白名单只有：

TASK_PLAN cutover 例外按 §3.1 的互斥 action 执行。`EXISTING_EVENT` 沿用 current PENDING event且不增 revision。
`CUTOVER_PLANNED` coverage 必须标
`coverage_origin=CUTOVER_PLANNED` 并同时冻结 base_revision、planned_revision、planned_event_id、
planned_payload_hash；aggregate 不存在时 base=0/planned=1，否则只允许 planned=base+1。APPLY 时锁内 production
aggregate 必须仍等于 base且 planned event尚不存在；ALREADY_APPLIED 只按 §3.1 的同 run全身份条件 no-op。
cutover 函数先原子写到 planned，再按其 manifest 校验/settle。该 planned event 不套“event<=captured”规则；
captured 边界就是 base，写后边界必须精确为 planned。planned 跳号、当前已推进、event 已被其他 payload
占用或缺计划 coverage 均整次回滚。若 base revision 本身有旧 PENDING event且 final state变化，唯一配套
`CUTOVER_PLANNED_BASE_SUPERSEDED` 允许该同 TASK_PLAN key、revision=base的旧 event与 planned event在同一
checker/manifest事务成对 settle为 SUPERSEDED/PUBLISHED；这是“相等 revision 不得 SUPERSEDED”的唯一例外，
缺 planned、异 key、跳号或 planned失败时旧 event也不得改变。所有 `coverage_origin=EXISTING_EVENT` 继续遵守
上段普通规则。

| event_type / aggregate | captured 边界 | 必须存在的 run 证据 |
|---|---|---|
| `source_wide.changed.v2` / COURSE、PARTICIPATION、TEACHER、TEACHER_STUDENT、LABEL、COMPLAINT_CATEGORY、COMPLETION_CONFLICT | event revision `<=` run 对应 domain aggregate revision | event payload hash 一致；OUTBOX_COVERAGE 的闭包 manifest 与最终生产物化逐键一致 |
| `source_wide.changed.v2` / SOURCE_SCOPE | 上述 revision 条件外，再要求 scope identity、row_version、active_snapshot_id 与 active epoch/fence hash 等于 run scope vector | coverage 引用 scope 重算的全部结果或明确空集合；只同 source fence 不足以覆盖 |
| `task.materialization.requested.v2` / TASK_PLAN | EXISTING_EVENT 为 current event revision=`captured`；CUTOVER_PLANNED 为 absent base 0→1 或 planned=base+1 | plan state hash、eligibility generation、模板/时区依据等于 run TASK_PLAN result；planned APPLY/ALREADY_APPLIED 与可选 base-event 成对 supersede 均满足上段校验 |

未知 event_type/aggregate、缺 coverage、payload/vector/hash 不一致均报
`CUTOVER_OUTBOX_COVERAGE_UNSUPPORTED/INCOMPLETE` 并停止。只有 `status=PENDING` 的白名单事件可在 cutover
事务中 settled；任何被最终 run 捕获的 `DEAD_LETTER` 都报 `CUTOVER_OUTBOX_DEAD_LETTER`，整次切换回滚，
必须先按原 event_id 审计重放回 PENDING，再重新运行锁内 final shadow。cutover 不把 DEAD_LETTER 直接
“治愈”为 PUBLISHED，也不 settle v1/未知事件；维护窗开始前旧 Worker 必须把旧事件全部排空为 PUBLISHED。

final run 还必须冻结 `technical_work_readiness` count/hash/vector，不能只看 Outbox：captured source fence 内的
`dts_dirty_keys` 必须全部 COMPLETED，任何 PROCESSING/RETRY/WAITING_DEPENDENCY/DEAD 均先阻断并要求原键
重放成功；任何尚未解决、尚未 ACK 的 DTS_SOURCE_CONFLICT 及其工作项同样阻断。到
`evaluation_as_of` 已到期的 favorite observation 若为 DEAD，只有 final shadow 已为该 observation 生成精确
FAVORITE_OBSERVATION/ATTRIBUTION结果时，才允许 cutover 事务用同一 reducer接管并把它落到确定的
PENDING/WAITING/CONFIRMED状态、重置重试字段；否则 `CUTOVER_TECHNICAL_WORK_NOT_READY`。TASK/投影
DEAD_LETTER 必须在 cutover 前按原 event恢复 PENDING，仍走 OUTBOX_COVERAGE。

每个在 cutover 中成功接管/settle 的曾 DEAD 工作项，其 §6.6 技术 Case与业务结果同事务恢复：OPEN→
RESOLVED，IN_REVIEW 只追加 recovery evidence；失败全部回滚。dirty/source conflict 不允许靠 full shadow
自称覆盖，必须在 final run 前恢复；技术 Case 状态不能代替底层工作项 readiness。

1. 先做 expand-contract，不得直接加 NOT NULL/换 PK。第一份 Alembic 可在旧 consumer 仍运行时只创建本文
   additive v2 表/索引、nullable 扩展列、ACL、SECURITY DEFINER 函数、immutable/deferred Trigger 与错误码
   映射，并把 `dts_source_rows` 扩展为 §4.1/数据库文档 §3.3.4 的双列迁移形态；此时不得先写一个缺 epoch 的
   H0/control。结构就绪后短暂停唯一 consumer、等待在途提交，读取旧 checkpoint 得到 raw next-offset L0，
   broker 继续积压但不得 ACK 新事件。对 L0 每个 region/topic/partition，必须从 broker/control-plane 取得并
   验证当前 `stream_generation_id + epoch_opening_id`；缺任一、与 L0 不属于同一代或无法证明时以
   `BROKER_GENERATION_UNVERIFIED` 保持暂停，严禁自造 epoch。验证通过后只调用一次整向量
   `bootstrap_initial_broker_epoch_v2(...)`，在同一事务为完整route集合创建对应 ACTIVE BROKER epoch，把全部
   checkpoint 以原 next offset迁为 epoch-aware identity，写 typed H0 vector/hash，创建唯一
   `dts_pipeline_control(control_id=PRIMARY,mode=V1_COMPAT_DUAL_CAPTURE)` 和初始化 audit；禁止逐partition提交。
   事务前崩溃
   没有 typed H0/control，可从仍未 ACK 的 L0 重做；事务后崩溃只接受 identity/vector/hash完全相同的幂等重放，
   不得生成第二 epoch或推进 offset。

   存量 ingest ledger 不猜历史 generation：只有 connector 证据能证明某行属于上述 generation 时才回填
   epoch；其余行永久标 `identity_version=V1_LEGACY`，保留旧唯一键、payload hash和审计，只读且不得参与 v2
   去重/排序/checkpoint。新事件必须为 `identity_version=V2_EPOCH` 且 epoch复合身份非空，部分唯一/Trigger
   分开保护两代。存量 `dts_source_rows` 标 `provenance_state=LEGACY_PENDING`；新 v2 current 必须完整 provenance，
   legacy 行在第 2 步首个成功 snapshot 走 `SNAPSHOT_BOOTSTRAP_PRESENT/TOMBSTONE` 收敛，禁止把 row_version
   充当 source revision。接入路径的 epoch/event/checkpoint/version 新写约束须在恢复前全部 VALIDATE；task/
   match 等仅存量需要回填的列可暂以 `LEGACY_PENDING` 条件 CHECK 保留 nullable，但新 INSERT Trigger已强制目标
   契约，并必须在第 4 步后移除例外、VALIDATE/NOT NULL，final shadow 前不得仍有 legacy pending。

   第一份 Alembic 同时给 v1 兼容 `lesson_source_wide` 增加可空 `source_region`、复合候选索引和临时写入
   Trigger。bootstrap 后仍保持暂停，发布 `V1_COMPAT_DUAL_CAPTURE` 版本；Trigger 只接受显式列或
   已校验的事务 GUC `tit.dts_source_region in (dom,ovs)`，缺失即失败，绝不默认 dom。consumer 与旧 Worker
   继续保持暂停。唯一现行 DTS consumer/projector 的 INSERT/UPDATE/DELETE/ON CONFLICT、
   所有 FK/查询和“v1语义 compatibility Worker”都显式携带地区，同时继续产出原 v1 结果。该新 binary还必须
   对此后新建的个性化 assignment写完整 LEGACY_COMPAT seed/revision/hash与copy复合键，对match写
   `materialization_origin=LEGACY_REUSED,created_projection_generation=0,is_serving=false`及全部typed seed/
   output link，对Case/notification写canonical source_ref，对积分写 `V1_COMPAT_LIVE` origin；不得再创建
   LEGACY_PENDING、旧单键或缺typed字段。部署或健康检查失败时
   保持暂停并回退兼容 DDL/binary，不让旧 binary 穿过新 Trigger 写入。仍在暂停窗口内回填存量地区并校验
   无 NULL、无同区重复、所有关联可唯一映射，随后第二份 Alembic 改为 `(source_region,课程id)` PK/复合 FK、
   `source_region NOT NULL`、`老师id` 条件可空。存量无法唯一回填、读写仍使用单键或临时 Trigger/GUC 不一致
   时以 `COMPAT_REGION_MISSING/COMPAT_WRITER_NOT_READY` 停线。旧 assignment/score 外键迁移必须同事务或
   先 expand 成复合外键，不允许出现悬空窗口。

   加三态 Outbox CHECK 前必须先停止旧 binary 继续写 `CANCELLED/PARKED`，并创建 append-only
   `outbox_events_legacy_archive`。只有能由 mock run/source_mode/seed audit 证明为非 REAL 的 CANCELLED，或能
   证明为旧退役控制事件且从未产生业务/外部副作用的 PARKED，才允许在同一受控迁移事务把完整原行、原
   status、证明依据与 archive audit 迁出 active Outbox；这不等于 PUBLISHED/已消费。REAL、证明不足的
   CANCELLED/PARKED 或任何未知旧状态一律 `LEGACY_OUTBOX_STATUS_CONFLICT` 停线，不改成 PUBLISHED/DEAD、
   不删除证据。迁完 active 表才启用 `PENDING/PUBLISHED/DEAD_LETTER` CHECK 与新 Trigger。

   恢复前必须用 catalog/ACL 自检逐项确认 dual-capture 会写的
   partition epochs、events/checkpoints、row versions/current、scope、dirty、domain/outbox、typed facts对象与
   函数均存在且权限正确；缺任一对象或仍是半迁移列类型即 `V2_SCHEMA_NOT_READY`，保持暂停。只有两次 DDL、
   backfill、复合 FK、全部 v2 schema 自检和新 binary smoke 全通过后，
   才从数据库 H0 恢复同一 consumer/group和旧 Worker；因此不存在“新 writer 已恢复但单列 PK 尚在”的
   同号冲突窗口。该暂停的锁时长、broker 积压和恢复吞吐未通过基准门禁时不得执行，应另立完整并行表迁移
   规格，不能临场改成半在线双写。
2. 同一 `V1_COMPAT_DUAL_CAPTURE` consumer 在每个原 DTS 事务内同时追加 v2 CDC source version/current/
   dirty key，再继续 v1 投影并推进同一个数据库 checkpoint，提交后才 ACK；没有第二 consumer group、双
   ACK 或两套 checkpoint。此时启用唯一 Domain Projector 的 `SHADOW_BUILD` 模式，允许它领取 v2 dirty key、
   写规范化 source_courses/participations/typed facts、domain revisions 和待处理 v2 Outbox，使预检 shadow
   能追到共同 fence；但 SourceWide Worker、Task Planner、favorite observation、task/Case/notification/
   score/qualification 生产物化仍关闭，v1 Worker 继续唯一拥有生产结果。
   SHADOW_BUILD 发现完课差异时只写 conflict status/fingerprint/latest source revision、
   COMPLETION_CONFLICT revision/Outbox；`completion_conflict_case_id` 保持 NULL，shadow 生成 canonical
   CASE_PLAN，绝不提前写可见 ops_case。第 6 步全量事务才按 source_ref 新建/复用唯一 Case并原子回填课程
   指针；切换后 V2_PRIMARY 的 Domain 再按 §6.5 同事务维护 Case/指针。
   为每个 topic/partition 记录 snapshot fence，Scope Coordinator 按 §4.1 导入同一 as-of BASELINE，使用已
   耐久的同一 CDC 流追平 candidate；发布前必须满足 checkpoint next_offset `>=` 锁内 end_next_offset vector、
   candidate hash/count 通过且 scope COMPLETE。首个 snapshot 对所有 `provenance_state=LEGACY_PENDING` key
   即使 row_hash 相同也强制写 bootstrap SNAPSHOT_DIFF/revision=1；legacy tombstone 同样建版本 provenance。
   所有白名单 current 全部收敛且 deferred FK通过后，才把 source_row_revision/last-version复合来源列改为
   NOT NULL并移除 LEGACY_PENDING；遗漏一行即 `LEGACY_SOURCE_PROVENANCE_INCOMPLETE`。旧课程没有可靠地区的
   行进入 PENDING_DATA。
3. 本阶段只复核第 1 步已经存在的 `dts_pipeline_control` 单例、checkpoint vector 与初始化 audit，并验证
   所有新服务在缺行、重复行、group/vector 不一致时 fail closed；不得到此时才补建 control 行。其字段保持
   `mode=V1_COMPAT_DUAL_CAPTURE/V2_PRIMARY/ROLLED_BACK`、row_version、consumer group、不可变 initial H0、
   最近 handoff H 与 audit；实时 current vector 只从 `dts_ingest_checkpoints` 同一快照读取，不复制到
   control。启动只校验当前 checkpoint 不早于 H0/该 mode 最近 H，不要求相等。实际 mode 切换只能发生在
   第 6 步 cutover 事务。接管协议固定为：协调器先取得独立 session-level
   `tit:dts-v2-intake` exclusive gate；正常 Ingestor 每批按“intake shared → cutover shared”顺序取锁，因此 gate
   只阻止新 broker claim/事务/ACK，不阻止已有 Domain/Worker 在 cutover shared mutex 下排空。等 Ingestor
   在途事务清零后冻结 next-offset H；继续在尚未取得 cutover exclusive 时等待 v2 source/current完整覆盖
   `<H`、scope barrier不落后于 H且 Domain/required Worker 队列排空。达到 drain 条件后才申请 cutover exclusive，
   取得后立即重读 checkpoint/队列/vector；若与 drain 证据不一致，释放 cutover exclusive 后回到排空步骤，
   禁止持 exclusive 等 Worker。最终锁内只运行 final shadow、校验和全量切换事务；然后
   与第 6 步生产物化/读切换同一主事务把 mode 改为 V2_PRIMARY、关闭 v1 direct projector，并写
   time_catchup=PENDING；此时仍不启用生产Worker或恢复consumer。按 §4.9持两把锁完成catchup并置COMPLETE后，
   才启用 Domain/SourceWide/Planner/favorite 与单向 v2→v1 compatibility projector，再从同一group/数据库
   next offset H恢复consumer。
   崩溃前未提交则仍是旧
   mode/checkpoint，提交后崩溃则按 V2_PRIMARY 从 H 续读；两种都不跳 offset、不双 ACK、不重复应用
   source_row_revision。禁止另起并行 v2 group、离线猜 offset 或两套 projector 竞争写同一事实。
4. 回填规范化事实；没有代课历史证据的存量只建 seq=1 并标 assigned_at SOURCE_MISSING，不伪造历史。
   受控迁移收敛全部旧个性化 assignment/match：P-REL→teacher+task，P-FB-NEGATIVE→teacher+label_id，
   P-FB-COMPLAINT→teacher+complaint_type_grandson，P-FB-BLACKLIST→teacher。label/category 仅在 evidence
   已有稳定 ID 或名称精确且唯一映射时更新新 dedupe_key；歧义/缺失进入对应 `PENDING_DATA:LEGACY_*_ID`。
   任一目标键映射到多条旧 assignment 时以 `TASK_DEDUPE_CONFLICT` 停止迁移，不自动合并状态；迁移采用
   临时键两阶段更新，且不修改 assignment_id、status、进度、模板引用或教师端拥有字段。同批完成
   materialization seed 回填：所有迁移前已存在个性化 assignment 均写 `LEGACY_FROZEN +
   legacy-assignment:{assignment_id} + revision=0` 并按既有冻结 payload 计算 hash，禁止用当前 active match
   反推、替换 why/title/evidence；切换时新补缺任务才使用 canonical MATCH seed。
   同批完成
   旧投诉/QA trigger match 的 `output_id + evidence` 到新 complaint/camera case key/source_ref 的精确映射：
   唯一匹配时保留原 case_id/notification_id、处理/READ 状态及全部 decision/notification event 历史，只补
   canonical source_ref；一个新键对应多条旧输出或 active 输出无法唯一映射时以
   `OUTPUT_DEDUPE_CONFLICT` 停止，不复制、不猜测。无法映射的终态历史仅保留审计且不得作为当前输出复用。
   同批完成 `dmo→dom`、旧 CPU/网络清 NULL 和假早退兼容清理。三项首次日期先把 v1 同 fence 值写入
   迁移 seed，再按 §7.5 与 v2 历史候选取 LEAST；不得只用当前未删除事实重算覆盖旧的更早日期。
   本步结束时同时 VALIDATE assignment/match/output typed link、模板/copy复合 FK与 legacy seed 条件，移除
   所有 `LEGACY_PENDING` 迁移例外；否则不得开始最终 shadow。之后仍运行的compatibility writer必须始终按
   第1步目标列契约写完整行；final cutover锁内在最终shadow前再扫描count/hash=0并VALIDATE一次，出现任何新
   LEGACY_PENDING/旧键/缺origin立即 `COMPAT_WRITER_NOT_READY` 全量回滚。
5. v1语义 compatibility SourceWide Worker 继续读取 v1 兼容投影并唯一拥有生产积分，所有新流水写
   V1_COMPAT_LIVE；Fixed Task Score Settler仍是固定任务分唯一writer并写FIXED_TASK_LIVE。按4.9运行独立v2预检shadow，
   它不领取/发布生产 Outbox，也不写 task/score/qualification；DOM、OVS 分别对账课程、参与、关系、
   收藏观察/归因、组件结算、聚合、积分计划、任务/Case/提醒计划和资格计划。维护窗前的 COMPLETE run
   只能证明预检，不得直接作为 cutover 输入。
6. 所有会改变 source/domain/计划/积分输入的写事务必须先取得同一
   `pg_advisory_xact_lock_shared(hashtextextended('tit:dts-v2-cutover',0))`，包括 DTS Ingestor、Domain/SourceWide
   Worker、Correction Service、favorite observation Worker、assignment/Case/notification 写入、教师日更和
   配置/积分规则发布。Cutover 进入维护窗口后先按上一步取得独立 intake exclusive gate、冻结 H并在普通
   shared 锁仍可运行时排空 Domain/required Worker；绝不先取得 cutover exclusive 再等待它们。排空后专用
   连接才取得 session-level cutover exclusive，阻断新业务写并等待当时仅剩的短事务退出，立即复核 H、零在途、
   dirty/Outbox/readiness 与 revision vector；变化则释放 cutover exclusive并重新排空。复核通过后不再调用任何
   需要 cutover shared lock 的 Worker，在不释放 exclusive 与 intake gate 的前提下重新运行一遍最终 full shadow，记录 source fence、全部 domain/TASK_PLAN
   aggregate revision、scope epoch/fence、task/output/score/qualification revision、14 项 active task template、
   active complaint rule SHA、teacher copy/其他 active config version、technical_work_readiness 向量和唯一
   `evaluation_as_of`。只有该 run COMPLETE 且事务开始前
   向量逐项未变才可继续；锁丢失或任一值变化以 `CUTOVER_INPUT_CHANGED` 失败关闭。然后在一个数据库事务内
   从 v2 规范化事实按该 run 的 `evaluation_as_of/evaluation_business_date_beijing` 执行全量生产
   materialization：投影/账户覆盖当前值；assignment 只补缺并保留既有
   状态/进度；Case/提醒按 canonical source_ref 复用精确迁移的原行并保留处理/READ 状态及事件历史，
   只补缺，不复制已处理输出。对全部冻结课程物化收藏观察：未来到期为 PENDING、已到期但所需 favorite
   业务时间证据缺失为 WAITING_EVIDENCE、时间证据完整但 HISTORY 不足为 WAITING_HISTORY、两类证据都完整
   才写确定观察并计算唯一归因；合并旧 observation/attribution 时保留
   revision/generation 和原流水引用。可唯一映射到切换前真实奖励流水的 attribution/component 固定
   `materialization_origin=LEGACY_REUSED`；本次依据 shadow 首次新增的奖励固定 CUTOVER_CREATED并写
   `materialized_by_run_id=本 run`。逐课组件 settlement 同样按现有 generation合并。切换后首次新增 generation
   固定 V2_LIVE；三类都写 award_projection_generation并由 deferred origin/流水检查约束。
   append-only score_entries 复用既有幂等键，对变化只写冲正+新流水；资格只允许不可逆前进。供给里程碑
   必须按 §7.4.2 迁移：已存在 canonical key 时原样复用；没有 canonical key 时，仅当同教师恰有一条
   `dimension=CAPACITY + reason_code=CAPACITY_PEAK_SLOT_40_ACHIEVED + delta_score=10` 的未冲正 legacy 流水，
   才保留原 score_entry_id，并向 `score_entry_idempotency_aliases` 插入 canonical alias；严禁 UPDATE
   append-only 流水的原 idempotency_key。alias 的 PK 为 canonical key，score_entry_id 也唯一，保存
   migration_run_id/reason/created_at；普通应用只读，只有 cutover 专用迁移角色可 INSERT，且同事务写
   migration audit。已获奖证据即使当前 peak_slot_cnt<40 仍保留 10 分。
   候选多条、金额/教师/营期不一致、里程碑被冲正、组件/总账户已有 10 分却找不到唯一流水时分别以
   `CAPACITY_MILESTONE_DEDUPE_CONFLICT/LEDGER_MISMATCH` 停止 cutover，不合并、不补造。无旧获奖证据且
   当前 peak_slot_cnt 已 CONFIRMED >=40 时，才按 canonical ID/key 新建一次奖励。
   同事务只对 §9 白名单中原状态为 PENDING、且有本 run 精确 OUTBOX_COVERAGE 的 v2 Outbox 校验
   event/revision/vector/result hash 后标 PUBLISHED并写 `settled_by_run_id`；发现任一已捕获 DEAD_LETTER、未知
   类型或 coverage 不完整立即回滚，不能先改状态再判断。随后锁定所有 `projection_event_ids` 与本次
   settled 集合相交或
   `downstream_projection_status<>PUBLISHED` 的完课纠错 `ops_decisions`，按关联 event_id 全量重算：全部
   Outbox 均存在且为 PUBLISHED（无论由 Worker 还是本次 cutover settled）才转 PUBLISHED；其余含缺失或
   未覆盖 PENDING 的保持 PENDING。DEAD_LETTER 已在 settle 前置检查阻断本次 cutover，不在切换事务内改成
   PUBLISHED 或 decision DEAD。状态转换追加 audit，不能因全量结果
   “看起来已覆盖”跳过 `projection_event_ids` 校验。最后切换 active projection 配置和读取视图，并写
   cutover audit。上述事件、decision 状态与读配置必须同一事务；事务失败则全部回滚，
   v1 继续生效。主事务成功只使 `time_catchup_status=PENDING`，协调器必须继续持有 intake gate 与
   cutover exclusive lock，按 §4.9 另起事务补齐并回读为 COMPLETE；此前 API 仍返回维护错误，不恢复
   v2 Worker、assignment 写入或 DTS 接入。只有 COMPLETE 且 through 日期二次校验通过后才释放两把锁并恢复服务。不得把维护窗
   前 shadow 的“已计算”或仅相同 source fence 当成生产物化依据。
7. 切换后继续运行单向 v2→v1 compatibility projector 两个完整业务日，旧 Worker 保持停止。窗口内回滚
   也必须用同一 exclusive mutex 进入维护窗口并选共同 fence，在一个事务内执行以下唯一生命周期：
   所有 `task_assignments` 无论终态/非终态都保留 ID、状态、进度和首次证据，禁止删除、取消、回退或隐藏；
   v2 trigger matches/audit 保留，`materialization_origin IN (CUTOVER_CREATED,V2_LIVE)` 的 match标
   `is_serving=false,serving_projection_generation=NULL`，created generation/origin不改，不得伪装
   evidence 失效；已创建 Case/提醒保留原 ID、OPEN/处理/READ 状态与完整历史并继续可见，v1 兼容路径必须
   通过 canonical source_ref 防止再建一份。favorite observations 保留 revision/证据但停止领取并标
   `is_serving=false`。rollback 不能以“行在 v2 表”猜 v2-only：锁内精确选择当前 favorite attribution
   `status IN (AWARDED,AWARDED_PENDING_EVIDENCE)` 及 component `status=AWARDED`，且当前 generation 的
   `materialization_origin IN (CUTOVER_CREATED,V2_LIVE)`、奖励流水 origin/run一致者，按原流水唯一冲正并置
   REVERSED；LEGACY_REUSED 即使物理行由 cutover补建也保留，不冲正。选择集合 count/hash、rollback_run_id
   与全部冲正/账户重建同事务审计；origin缺失、错配或一条原奖励已有冲正则 `ROLLBACK_ORIGIN_CONFLICT`
   整体停止。held attribution 同样属于当前奖励，不能漏掉。score account 从保留的 append-only 流水重建。
   固定任务奖励、供给里程碑、已获得
   出营/金牌资格及其时间均不可逆，回滚不冲正。随后从最新 v1 兼容事实全量重建旧生产投影并切回旧
   Worker/API；同一 rollback 事务把 `dts_pipeline_control.mode` 改为 `ROLLED_BACK`。该 mode 下同一 DTS
   consumer/group/checkpoint、v2 Ingestor、Domain Projector 和 v2→v1 compatibility projector 继续运行并
   唯一推进来源事实，v2 SourceWide/Task Planner/favorite observation/新 task-score-qualification 物化停止，
   v1 Worker 只消费 compatibility 输出并唯一拥有生产聚合/积分。ROLLED_BACK 下 Domain 对新完课冲突固定
   使用 SHADOW_BUILD 语义：只写 conflict status/fingerprint、COMPLETION_CONFLICT Outbox 与 CASE_PLAN，
   不新建生产 Case、不回填 `completion_conflict_case_id`；回滚前已存在的 Case 继续可见，但决定提交保持维护
   门禁，只允许查看和人工备注。再次
   cutover 在最终全量事务按 canonical source_ref 复用旧 Case或物化累计 CASE_PLAN，并与 conflict 指针同
   事务提交；v1 兼容路径不得为同一 source_ref 另建一份。不得反向交接 broker owner、恢复 v1 direct
   projector、回退 checkpoint、删除 v2 事实或让两套 Worker 同写。
   再次 cutover 从 ROLLED_BACK 进入 V2_PRIMARY 不做 ingest handoff，只在同一 exclusive mutex/最终 shadow/
   全量事务内切换生产物化 owner。先重评所有 retained 行：assignment/Case/提醒复用原物理 ID；match 恢复
   serving 但不重写首次 assignment 证据；观察按当前证据恢复/新修订；被冲正 attribution/component 只有
   再次成立才 generation+1 新增奖励。任何 retained 行无法唯一映射时停线，不复制或静默丢弃。
8. 两日对账通过并获得发布授权后才停兼容投影、退役旧 Worker/API。此后若需回滚，必须先从 v2
   重新生成 v1 兼容投影并走新迁移计划。cutover/rollback 全量事务的锁时长、行数和 WAL 基准未通过时，
   不得实施切换，应另行引入版本化 staging，而不是改成分批可见的半切换。

`lesson_source_wide` 在兼容窗口仍保持“一节源课程一行”和 `课程id=source_appoint_id`，但物理身份已迁为
`(source_region,课程id)`，所有 v2→v1 compatibility 写、旧 Worker 读取/结分和窗口内 rollback 重建都必须
带地区；严禁在同号冲突时覆盖、任选一行或拼接伪课程 ID。已冻结课程展示
当前 completion 教师，未冻结课程展示 appoint 当前教师；它不展示所有参与，也不再是任务/积分/API
权威。新 `teacher_lesson_score_current` 直接读取 `source_course_participations`，行键固定为
`source_region + source_appoint_id + participation_seq`，不拼接伪源课程 ID。所有新事实以 v2 表为权威。

## 10. 开发批次

1. 状态表与白名单：新增受保护的 `dts_source_row_versions`，扩展带地区主键的 dirty key；所有业务表进入版本历史与 `dts_source_rows`，补 `grading.use_point`、`certification_code`，移除 `cancel_reason/reason_desc` 业务依赖和退役 QA 的业务投影，只保留无 payload 的兼容控制路由。
2. 源课程/参与/完课冻结：实现状态机、乱序重算和纠错 Case。
3. 子表选择器：缺席、处罚、DOM 评价、标签、证书、投诉和摄像头集合重建。
4. 关系：当前态、时间线、24 小时领取/重试、唯一归因与补扣分。
5. 聚合/逐课积分/教师端视图：按本文口径一次切换；不在旧单行表继续补丁。
6. 教师状态/资格：在线状态日更、出营分快照、不可逆时间和显示分上限。
7. 回填、对账和读写切换。

每一批都必须同时覆盖 direct 与 queued 的同一领域函数；禁止复制两套业务判断。

## 11. 最低测试矩阵

除主手册 T00–T52 外，开发必须新增：

| 编号 | 场景 | 目标结果 |
|---|---|---|
| T53 | A→B→A | seq=1/2/3；前两阶段 absent，第三阶段当前 |
| T54 | A→NULL→B | A absent；B 新序号；无虚构 NULL 教师参与 |
| T55 | 同事件 A→B 且进入 end | 先建 B 参与，再冻结 B |
| T56 | INSERT 即 end | 有 t_id 时立即冻结；无 t_id 时 SOURCE_MISSING |
| T56A | end 时无教师，之后 NULL→B/离开 end/DELETE | 同一可空教师纠错 Case；B 只建 PENDING_CORRECTION，不自动冻结；仅 TRANSFER/VOID 可结案 |
| T57 | end→on→end、end 后换教师/改时间/DELETE | 冻结不自动变化；一个幂等纠错 Case |
| T57A | 首次 end 前 appoint DELETE 后恢复 | 旧参与 source_deleted 且不计 booked、不推断 absent；恢复创建下一 seq |
| T57B | A 首次 end 后连续 A→B→C，再 KEEP/TRANSFER/VOID | KEEP/VOID 收口全部既有 PENDING；TRANSFER 仅所选 B 或 C 成为 COMPLETION，其余 REJECTED；决定后更晚事件进入新 Case revision |
| T57C | 完课教师不变，仅 end_time/student/日期时间/Peak 修正 | UPDATE_COMPLETION_SNAPSHOT 保持原参与为 COMPLETION，更新当前快照并冲正/重结受影响组件；收藏观察按完整性失效重建 |
| T57D | 两个首次 end/TRANSFER 并发，以及直写错 current/completion/lesson_score/组件/favorite 指针 | 课程锁和 Case revision 只允许一个决定；部分唯一+deferred trigger 拒绝双角色、悬空、错 teacher/seq、双 AWARDED 组件、归因引用非当前 completion 或非 CONFIRMED_TRUE 观察，事务后全部一致 |
| T58 | 子事件先于主记录 | 源行保留；主记录到达后自动重建，最终与正序相同 |
| T59 | absent 最新删除 | 恢复次新原因；任务 match 抑制/重建正确，assignment 不删除 |
| T59A | 同一 absent canonical id 的相同业务时间修订乱序/重放 | typed 当前行只接受更大 source_row_revision；选择器末位也是 source_row_revision，旧 revision 不覆盖当前原因；position 仅留审计 |
| T59B | 同 key 在同 epoch/partition 的 offset 9/10、跨 partition/topic；reset新 epoch重读同 offset，及 topic重建同 offset/空非法 envelope | offset按bigint；跨 partition报SOURCE_KEY_PARTITION_DRIFT；同 generation同hash为EPOCH_REPLAY且无新row revision，不同hash冲突；新generation可落新版本；非法事件不推进对应epoch checkpoint |
| T60 | grading 最新删除/变未知 | 删除恢复次新；变未知清除当前贡献但不回退旧记录 |
| T60A | 好评/完美/Peak/硬件 true→false或未知→true | 原奖励只冲正一次；恢复后 generation+1 新增奖励，无重复流水 |
| T60B | 重复 label_id 达阈值但当前 label_name NULL→补齐→再 NULL | pending match→正常任务→pending 恢复；不读字典、不重复/改写 assignment，照片变体只按精确原名 |
| T60C | 同 label_id 多条 log 的 create_time/dt 与 DTS 到达顺序相反，删除当前最新 | typed 表保留完整排序输入；按 create_time→dt→canonical log id→source_row_revision 选名，删除后恢复次新 |
| T60D | 同一 DOM grading canonical id 的业务时间完全相同，来源修订乱序/重放 | typed 当前行只接受更大 source_row_revision；好/差评始终由统一比较器选出的当前修订决定 |
| T60E | 同 label_id 跨课当前名称分别为空/通用/照片标签，且课程处理顺序与 cutover 扫描顺序互换 | 空名/变体冲突各复用稳定 PENDING_DATA match；内容变化/消失/恢复 revision 正确且不重复；一致后按 canonical 最小 match 冻结同一 seed/title/variant/hash，重排不变 |
| T61 | penalty/complaint/QA 多行删一留一 | 按 OR/EXISTS/最新选择器保持正确结果 |
| T61A | 同课两条不同分类有效投诉，删除/失效当前最新 | 指标仍按课程 EXISTS；输出只路由最新，删除后抑制旧 match 并恢复次新分类路由 |
| T61B | 摄像头集合 true→false→true | 摄像头 match 抑制/重激活；仅 STORED 提醒取消后恢复，已读历史不回退，不重复同键提醒 |
| T61C | 投诉四路由后完课 TRANSFER A→B | 旧 participation match 抑制；未处理输出取消、已处理输出保留 A；按新 participation 为 B 建对应任务/Case/提醒且不重复 |
| T61D | 同课较旧 P0、较新 P3 同时有效 | 两条 typed complaint 都保留；课程输出只路由最新 P3，L0 仍统计较旧 P0 并使资格失败关闭 |
| T61E | 最新有效投诉 grandson NULL→具体 ID→NULL | typed error 写入→清除并路由→恢复；NULL 阶段无 PENDING_DATA trigger match/assignment/Case/提醒 |
| T61F | A→B→A，A 有处罚且处罚早到/晚到/UPDATE/DELETE | 完课 A 时只映射 completion seq=3，不复制到 seq1 或其他参与；未冻结且区间不能唯一证明时所有参与都不写并保持 PENDING_DATA；删除后从该参与剩余集合重算 |
| T61G | OVS complaint 先到且 DOM 分类字典缺失，随后 DOM 字典 INSERT/改名/DELETE/恢复 | OVS typed 行先 SOURCE_MISSING；DOM 分类键跨区唤醒 OVS COURSE；精确三级规范化后路由，删除退回等待，结果与字典先到一致 |
| T61H | 同课两条有效投诉的 add_time 与 DTS 到达顺序相反，且一条时间为空 | 始终按 add_time→course_date→canonical id→source_row_revision 降序选择输出；重放/换到达顺序不改变结果 |
| T61I | 同一参与处罚集合分别为 true+unknown、false+unknown、全 appeal_status=2、空集合，且 scope COMPLETE/STALE | true+unknown=true；false+unknown=NULL；COMPLETE 下全 2/空集合=false；STALE 且无 true=NULL，不误加完美分 |
| T61J | 一般投诉三级名分别命中/未命中 teacher copy，交换到达与 cutover 扫描顺序，并在最终 shadow 后换配置 | title 分别为批准英文/固定 fallback；原名只进 evidence；同配置 seed/hash 稳定，配置 revision 变化使 cutover 失败重跑，既有任务不改写 |
| T61K | 同 SHA 重放、连续三份不同 SHA 换版、旧 SHA Worker 与新发布并发，并尝试拼接新 SHA+旧 rule_id/改规则行 | 同 SHA 复用 rule_id；三版 activation_generation 单调1/2/3并逐版唤醒；复合 FK 拒绝错版；catalog锁/CAS保证唯一PUBLISHED和两区重算；发布后规则不可改，final shadow 后换版使 cutover 重跑 |
| T62 | TESOL 两张有效删一张 | 仍 true；全删且 scope COMPLETE 才 false |
| T63 | favorite/blacklist 全 CRUD 和重叠记录 | 当前态按完整集合重算，删除一条不误清其他记录 |
| T63A | DOM 先达到拉黑阈值、OVS 后达到同教师阈值 | 复用一个 assignment 且其首次 evidence 不变；两地区各自 match/audit 完整，当前贡献从 active match 聚合 |
| T64 | 收藏历史事件晚到 | 业务时间早于观察点则补/扣并重选；晚于观察点只改当前态 |
| T64A | favorite INSERT/UPDATE 缺 add_time，或 DELETE source_timestamp 为空；之后补齐权威边界时间 | 所有缺失场景都使受影响观察 WAITING_EVIDENCE，不按到达时间加分/扣分；补齐后按真实时点重评、补分、扣分或确认 false |
| T64B | 收藏单价/规则换版时归因分别为AWARDED、AWARDED_PENDING_EVIDENCE、REVERSED，并重放发布 | 前两者各唯一冲正并generation+1按新规则奖励，held仍held；REVERSED不重开；账户/流水一致且无重复 |
| T64C | 同一师生两课 observed_at 完全相同，appoint numeric ID 为 9/10，并交换到达/重算顺序；另测 text ID | numeric 固定选 9，text 按 UTF-8 bytes；typed CHECK/索引生效，重复重算不改变唯一归因或流水 |
| T65 | 课程完成纠错批准/拒绝/作废 | 冲正幂等，冻结审计保留，历史资格不撤销 |
| T65A | TRANSFER 目标缺 student_token 或 completion_end_time | 可完成教师归属转移，但不建收藏观察，相关证据为 SOURCE_MISSING；补齐后仍须新决定才建新修订观察 |
| T65B | TRANSFER/UPDATE/VOID 涉及四个非收藏课程组件，并并发尝试让旧/new seq 同组件均 AWARDED | 旧 completion 按原 entry 唯一冲正；新/更新 completion 按各组件 generation 重结；部分唯一与 deferred current-completion 校验拒绝双奖/错教师，账户与逐课结果同事务一致 |
| T65C | KEEP/UPDATE/TRANSFER/VOID 决定提交后，Worker 前后崩溃并重放纠错 Outbox | 决定事务原子写 COURSE/PARTICIPATION 事件；旧 match/output 收口、新键物化；projection 状态可追踪且任务/Case/提醒/流水不重复 |
| T66 | schedule on/off/delete/重复事件 | 当前集合和日期去重正确，不累计重复开启 |
| T66A | peak_slot_cnt 39→40→39→40，期间 scope STALE/恢复 | 只在首次确认达到 40 时按 canonical key 加 10；之后不冲正、不重复奖励 |
| T66B | SCORE_GRADUATION草稿尝试修改供给阈值40或分值10 | schema/服务端拒绝发布，不补差、不生成第二里程碑或新流水 |
| T67 | teacher DELETE | 历史课程/参与/积分/资格保留，无级联删除 |
| T68 | dmo 存量迁移 | 新数据仅 dom；关联、Peak、checkpoint 和 HMAC token 不丢不重 |
| T69 | OVS grading | 只持久化，不误套 DOM 分类；兼容路径与 v2 不双写 |
| T70 | 同课程并发脏键重算 | 参与序号、归因、任务和积分唯一约束均无重复 |
| T70A | 同批 A→B→A 后才运行投影 | 脏键虽合并，三个 appoint 版本均在，稳定重建 seq=1/2/3 |
| T70B | DOM/OVS 同 appoint ID 且同一教师两课均为 perfect，覆盖兼容运行/cutover/rollback | v2 与兼容表/旧 Worker 全程按地区复合键隔离；两行并存，perfect_cnt=2、完美分=8，回滚不覆盖或漏课 |
| T70C | 无基线时首个主事件为 A→B UPDATE | 完整 before 生成 seq1/BEFORE/SOURCE_MISSING，after 生成 seq2；before 不足则等待/冲突，不猜测 |
| T70D | 同一 A→B 以新 offset 紧邻重复；以及 before 不匹配且 after 也不同 | 前者 SEMANTIC_REPLAY 不新增参与/Outbox；后者 SOURCE_CONFLICT；A→B→A→B 的后一次 B 仍新建参与 |
| T71 | 主键原地变化 | 整批失败、checkpoint 不前进、无 ACK |
| T72 | 退役 QA/user_complaint CRUD | 账本和 checkpoint 正确，无业务写入 |
| T73 | scope 快照与 CDC fence 竞态 | 完整证据后发布 COMPLETE 并由 SOURCE_SCOPE 唤醒 false/0；STALE 后降为未知并冲正；TEACHER scope 优先级固定 |
| T73A | scope COMPLETE→STALE→新快照 COMPLETE 且组件重新成立 | 先冲正当前奖励，恢复后 generation+1 新增奖励；历史资格不撤销 |
| T73B | 第二个 snapshot 对同一 scope 分别包含不变 A、B→A、旧 key 缺行、后续再恢复 | 不变不增参与；变化写确定 SNAPSHOT_DIFF 且时间证据缺失；GLOBAL 缺行 tombstone、TEACHER 缺行只移 membership；恢复不复活旧 seq |
| T73C | candidate 装载/校验/发布分别失败，且旧 active 分别为非空集与已确认空集 | active 指针和旧 current 不变，candidate/失败 staging 独立留痕且不提供完整性；重试必须新 ID；成功发布才原子换 active 并清 candidate |
| T73D | snapshot fence 后同 key 的 CDC 先被普通 Ingestor 应用，candidate 又重放该 CDC；锁内 diff 与释放后源时间更早的 CDC 交错 | candidate replay 只改 staging；已达成状态不再发 diff；剩余 diff 取得下一 source_row_revision，释放后 CDC 再取更高 revision；版本/参与/dirty/Outbox 各一次且重建稳定 |
| T73E | TEACHER COMPLETE 空集合后 CDC INSERT/DELETE，以及来源行 teacher A→B；分别在 active、LOADING candidate 和事务崩溃时发生 | source current、membership overlay、dirty/checkpoint 同事务；A effective absent、B present，GLOBAL正确；candidate只由 replay追平；失败全回滚，COMPLETE不继续误判空集 |
| T73F | 多region/topic/partition首次H0整向量成功/响应丢失/漏一route/identity未验证/已有半行 | 单次受限函数原子创建全部sequence=1 ACTIVE epoch+current checkpoint+唯一control H0；同run/vector重放no-op；漏多项、未验证或部分状态全部失败关闭，不出现逐partition半提交 |
| T73G | 同 stream generation reset 起点分别等于/高于/低于 bootstrap floor，并在 BARRIER_PENDING 持续到达 CDC | 前两者可耐久 ledger/current/dirty/checkpoint+ACK但 scope 仍 STALE；低于 floor 拒绝消费；只有完整 manifest 激活后才取得 dominance，不重复应用已耐久 CDC |
| T73H | 旧 generation checkpoint=1000，新验证 generation 从 offset=0 开始 | 新 epoch floor=0 且先 BARRIER_PENDING；不比较两代 offset 数值，manifest 完整后按 predecessor lineage 支配旧 epoch |
| T73I | activation manifest 漏一个 routed CURRENT、漏 appoint/favorite/schedule/blacklist 的 HISTORY、或空 route 没有 ROUTE_EMPTY | count/hash 重算失败，epoch 保持 BARRIER_PENDING；补齐精确 requirement 与 COMPLETE snapshot 后才可原子 ACTIVE |
| T73J | snapshot fence start_next_offset=N,end_next_offset=M，边界 delivery 分别为 N、M-1、M | 只重放 [N,M)；checkpoint next>=M 即覆盖，M 由普通增量处理一次，无漏数或重复 |
| T73K | 同表 GLOBAL F1 与 TEACHER F2 并发 candidate，owner 超时/heartbeat/abort/takeover，发布响应丢失 | 表级 head 串行；有效 token 可续租，旧 token失效；takeover先使旧 candidate FAILED再重开；generation只+1且同结果重放 no-op |
| T73L | legacy current 与 snapshot 相同/不同，分别需要一段 bootstrap 或 bootstrap+normal diff | 相同只写 r1/step1/奇数 offset；不同再写 r2/step2/偶数 offset；四元唯一键与 generation hash 稳定，重跑不丢第二步 |
| T74 | KEEP 后相同与更新冲突 | 相同 fingerprint/source_row_revision 不重开 Case；更大 revision 的新差异只更新同一幂等 Case 并等待新决定 |
| T74A | 运营读取 Case rev1 后，更晚 DTS 使其变 rev2，再提交 rev1 决定 | 返回 STALE_CASE_REVISION；无 decision、角色、快照、观察或积分写入，rev2 保持 OPEN |
| T75 | VOID 后再次收到 end | 不自动重新冻结或重结；必须由新的受限 TRANSFER 决定恢复 |
| T76 | 收藏观察为 false、历史覆盖不足 | 前者留下 CONFIRMED_FALSE 且不加分；后者进入 WAITING_HISTORY，不把未知当 false |
| T76A | 已获收藏分后 HISTORY STALE→COMPLETE，候选仍是同课 | STALE 时归因转 AWARDED_PENDING_EVIDENCE，保留原 +5、award_generation 和流水；恢复为 true 时原地回 AWARDED，不新增/冲正流水 |
| T77 | 收藏 observation崩溃；Outbox在handler失败/savepoint/状态提交各点崩溃，另一Worker并发；并尝试改 payload/identity、PUBLISHED回退、非法settled、DEAD直接UPDATE恢复 | observation租约到期重领；Outbox锁内业务+状态原子；身份/payload不可变、三态组合CHECK和受限恢复函数拒绝非法写；崩溃后PENDING可重领，无抢占/半写/重复流水 |
| T77A | dirty/source conflict/favorite/TASK_PLAN、普通 COURSE/TEACHER/SOURCE_SCOPE 与纠错投影分别第8次失败；DEAD受控恢复后再失败、IN_REVIEW后恢复 | 失败状态与唯一技术 Case 同事务；所有非TASK plan v2投影都有 Case；DEAD→PENDING重置attempt但Case未提前关闭，再次失败仍同event/Case episode；原工作成功才OPEN→RESOLVED，IN_REVIEW只追加证据；新generation/revision才建新Case，teacher缺失也可落库 |
| T77B | dirty领取后分别在领域写前/后崩溃、租约过期与heartbeat竞态；PROCESSING中到达更大revision；WAITING依赖重放/新revision唤醒；DEAD普通重放/新输入/人工恢复 | 领域写+complete同事务无半提交；旧token/version均DIRTY_LEASE_LOST；reaper按一次失败退避/第8次DEAD+Case；required>claimed完成后回PENDING；旧依赖/普通重放no-op，新证据才work_generation+1；DEAD只被新输入或受限恢复重开，完成后才恢复Case |
| T78 | 新教师固定任务初始化，并与任一 G 模板换版并发 | catalog shared/exclusive lock 给出串行点；同一 snapshot/事务恰好 9 条 ASSIGNED且都引用当时 current PUBLISHED；重放不重复、目录不足失败关闭、不创建 notification/投递意图 |
| T78A | 五类任务多 active match 含 appoint/evidence numeric 9/10、TEXT、NONE、非C collation与PENDING_DATA；交换顺序重放/cutover并尝试缺typed字段直写 | type-rank NUMERIC<TEXT<NONE、numeric 9<10、text/dedupe按UTF-8 bytes；只查完整typed active索引并排除PENDING_DATA，缺字段被CHECK拒绝；seed/hash稳定且既有assignment不重选 |
| T78B | match+TASK_PLAN Outbox 提交后 Planner 在建任务前/提交前崩溃，多 revision 事件乱序领取；首次成功后立即重放同 event并直接跑 final shadow/cutover | 旧 revision 只记 superseded；当前 revision 原子建 assignment/关联 match/发布 event；重领仍一条 assignment、同一 seed/hash，无半写；物化不改 same-revision aggregate/plan hash，event、shadow、assignment 自身 hash一致 |
| T78C | TASK_PLAN 产生后延迟 3 天消费，期间教师换时区并重启 Worker；cutover 首次补缺重复运行 | 正常任务 due_at 始终用原 eligible_since_at+48/72h及冻结时区，hash 不漂移；cutover 用 run.evaluation_as_of；重复运行不改 assigned/due/seed |
| T78D | 同 template/copy 并发发布、PUBLISHED 原地改 payload、发布扫描时并发新增 active match/Planner/固定任务初始化 | 预计算全 catalog 锁集且无死锁；部分唯一阻止双发布/旧版 current plan，已发布行不可改；发布重算未物化 TASK_PLAN并使旧 event superseded；既有 assignment 仍读冻结 RETIRED row，新任务只用新 PUBLISHED row |
| T78E | 先以 canonical 较大 match 物化 assignment，后到/恢复 canonical 更小 match；再把 MATCHED→MATERIALIZED 后重算 | 新 revision event 只把后到 active match 关联原 assignment，seed/模板/due/hash不变且不报唯一冲突；active-set hash 对 MATCHED/MATERIALIZED 归一，排除 output/materialized 时间，物化前后不漂移 |
| T78F | assignment task_code 与模板 template_id 错配、MATCH due/timezone 缺失或等式错误、非法状态边/同状态/越权目标/旧 row_version | 复合 FK、current PUBLISHED 创建校验、due deferred trigger、状态矩阵/权限/版本错误码分别拒绝；既有 assignment 换版后仍读 RETIRED 冻结模板并可合法推进状态 |
| T78G | 发布单个任务模板、发布 teacher_personalized_copy、缺失/冲突 copy，并尝试直接改模板状态或复活 RETIRED | 模板发布只重算同 task_code 的未物化计划；copy 发布重算全部 active 未物化计划；既有 assignment不改；缺/冲突 copy 阻断 TASK_PLAN且无 fallback；非法直写/复活被拒绝 |
| T78H | V1_COMPAT_DUAL_CAPTURE首次建LEGACY_COMPAT，与template/copy发布并发、响应丢失同参重放、同幂等key异参、既有MATCH/FROZEN assignment和cutover并发 | catalog完整key集按UTF-8同序无死锁；确定assignment ID/seed与首次payload不漂移；同参REPLAYED，异参拒绝；既有同教师/task只关联不改冻结列；mode切换后新建失败且cutover原样保留已提交行 |
| T78I | TASK_PLAN state JSON字段重排、时间不同时区等值、NULL/空集合、额外字段、自身hash注入，并交换match顺序 | `task_plan_state_hash_v1`对等价输入同hash；UTC微秒/JSON null/空set固定；额外字段和自含hash拒绝；Outbox/Planner/shadow/cutover四处重算一致 |
| T78J | MATCH任务物化前后出现并恢复negative blocker，及LEGACY_COMPAT/FROZEN关联 | 未物化恢复开新generation；已有任务复用原generation/basis，不改seed、模板、文案或截止时间 |
| T78K | teacher copy把同一标签从GENERAL/title A改为PHOTO/title B，并使同label多课程产生或解除variant conflict | 未物化match先重评variant/title/evidence/revision，再每key只发一个plan revision；blocker正确切换；既有assignment不改 |
| T78L | 首次发布或换版把任一 G 分值改错、把任一 P 改为非0，并观察既有assignment | 发布整笔拒绝；必须逐 task_code 命中固定分值映射，既有assignment及积分不变 |
| T79 | 新表 ACL、隐私与不可变 Trigger | 原始 DOM 学员 ID、越权写、版本 UPDATE/DELETE、冻结字段普通修改均被数据库拒绝 |
| T79A | 绕过服务分别制造双 is_current、双 COMPLETION、课程指针不回指、score 指向 NORMAL | 部分唯一或事务末 deferred trigger 全部拒绝，合法 TRANSFER 的旧降级→新升级→三表指针同步可提交 |
| T80 | `source_wide.changed.v2` 端到端 | aggregate key/payload 无敏感值；失败可重试；仅 v2 Worker 写生产聚合、任务、积分和当前资格 |
| T80A | shadow 全量中断/同 key重放/重跑；COMPLETE 后尝试改 input/result/hash、删结果或重开 RUNNING，cutover 前伪造摘要 | 同 payload重放 no-op、异 payload使 run FAILED；终态证据不可改删，重跑新 run_id；只更新独立 run/cursor/results，不改变生产事实；cutover独立重算 count/hash，篡改一律失败 |
| T80B | cutover 全量事务成功/中途失败 | 成功时合并旧任务/Case/提醒/流水/资格且 ID、状态、历史不重不丢，发布 fence 前 Outbox 并原子切读；失败全部回滚仍由 v1 生效 |
| T80C | cutover 时收藏观察分别为未来到期、已到期业务时间缺失、已到期 HISTORY 不足、两类证据完整 | 分别物化 PENDING、WAITING_EVIDENCE、WAITING_HISTORY、确定观察/归因；revision/generation 与既有流水不重不丢 |
| T80D | cutover 时 v1 三项首次日期早于/晚于/缺失于 v2 候选，且 HISTORY 完整/不完整 | 逐字段 LEAST 合并；旧值更早保留为 LEGACY_FROZEN；双空仅在完整覆盖时 CONFIRMED_EMPTY，重放稳定 |
| T80E | cutover 时供给里程碑分别为 canonical 流水、唯一 legacy 流水、当前不足但曾获奖、账户 10 无流水、多候选，以及 alias 事务回滚 | 前三者保留同一 10 分且不重复；唯一 legacy 只建 alias、不改流水；冲突停线，回滚不留 alias；当前确认达 40 且无旧证据才新建一次 |
| T80F | v2 成功运行后产生非终态任务/match/Case/提醒/观察/归因/组件，再 rollback 并重新 cutover | assignment/已处理输出/不可逆分与资格保留；v2-only 可逆奖唯一冲正；retained 行按原 ID 和新 generation 复用，无孤儿或重复 |
| T80G | 预检 shadow COMPLETE 后发生纠错决定、任务进度、观察到期或规则发布，再尝试 cutover | writer 被共同 mutex 阻塞；若 revision vector 变化则 run 作废并报 CUTOVER_INPUT_CHANGED，必须在锁内重跑最终 shadow |
| T80H | cutover run 同时含 COURSE、TASK_PLAN、SOURCE_SCOPE 的 PENDING Outbox，分别为完整/部分/显式空 coverage，并混入 DEAD_LETTER | 只按 event/revision/vector/result hash settle 完整或显式空 PENDING；部分/未知 coverage 失败关闭；任一 DEAD_LETTER 使 cutover 全回滚且 decision/读配置不变，原 event 审计恢复后重跑才可切换 |
| T80I | 最终 shadow 跨 favorite observed_at、北京午夜/00:05，来源和 revision 均不变 | 全部计划/生产物化复用 run.evaluation_as_of 与同一北京业务日；hash 不漂移；锁前后时间区间写耐久补跑键，释放后正确追平 |
| T80J | expand-contract 各阶段持续收到 DOM/OVS 同号课程写入，故意缺 v2 表/函数/ACL或留半迁移 source_rows，control 行缺失/重复/group-vector错误，dual-capture 在事务/ACK 前后崩溃，并在 handoff H 前后重启 | 全部 additive v2 schema 自检和 control 初始行在恢复前完成，任一错误均 V2_SCHEMA_NOT_READY/fail closed；nullable→typed region/revision/position→复合 PK/FK 全程不写错行；单 group/单 checkpoint、提交后 ACK；mode/H 原子接管，崩溃恢复无 gap、双 ACK、重复 revision |
| T80K | COURSE closure 故意漏一个下游结果、refs 重排、相等 revision 伪造 SUPERSEDED、零输出、错误 payload hash；COMPLAINT_CATEGORY 分别只在 L1/L2/L3 被引用 | 独立 closure 重算识别三层所有引用；refs 重排 hash 相同；逐键 EXPLICIT_EMPTY 合法；漏/多 key、相等 revision SUPERSEDED、错误 hash 均 CUTOVER_OUTBOX_COVERAGE_INCOMPLETE 且不切读 |
| T80L | 切到 ROLLED_BACK 后新到 end 后教师/时间变更，再次 cutover | rollback 期间只新增 conflict/Outbox/CASE_PLAN，不建第二个生产 Case或回填 pointer；既有 Case仍可见；再次 cutover 按 canonical source_ref 复用/物化唯一 Case并与 pointer 同事务提交 |
| T80M | final run 捕获到期 favorite DEAD，分别有完整 observation+attribution/明确空结果、缺任一结果；另混入 dirty/source conflict/Outbox DEAD | 完整者标 TAKEOVER_ELIGIBLE 并允许 run COMPLETE，cutover 原子重建状态及恢复 Case；缺结果为 BLOCKING；dirty/source conflict/Outbox DEAD 始终阻断 COMPLETE，不能由 shadow 结果代替恢复 |
| T80N | cutover TASK_PLAN 覆盖 EXISTING_EVENT、CUTOVER_PLANNED、ALREADY_APPLIED；另测 absent aggregate 0→1、planned revision漂移、相同 event_id 异 payload/关联、base event 晚到 | 三合法分支分别复用既有事件、原子写 planned 事件、精确 no-op；首次 absent 基线为0并正常写1；异 payload/关联与 revision漂移全回滚；晚到 base event 以 CUTOVER_PLANNED_BASE_SUPERSEDED 留审计且不覆盖 current |
| T80O | expand-contract 遇到两类合法legacy Outbox、未知/冲突状态；逐一注入额外key/错误类型/超深超大payload、credential/连接串/私钥/student token、proof/row hash漂移，并模拟preview后并发变化与归档响应丢失 | 只有v1 checker空数组且typed proof成立才可由受限函数原子archive+audit+delete；精确issue/error阻断不安全payload；锁内重算识别TOCTOU；同run/row/proof hash重放no-op，active/archive不双存，未知状态停线 |
| T80P | 三种 pipeline mode 下固定任务完成与 SourceWide 全量/增量并发结分、随后 rollback/re-cutover | 只有 Fixed Task Score Settler 写 FIXED_TASK_AWARD；共享账户锁/重建函数确保一条 canonical 流水；SourceWide 不创建固定任务分，rollback不冲正且重切不重复 |
| T80Q | DUAL_CAPTURE/ROLLED_BACK期间提交四类完课决定，进入V2_PRIMARY后按最新Case revision重试 | 前两种mode维护拒绝且无参与/积分/decision写入；V2_PRIMARY只接受未过期revision并由唯一v2 owner投影 |
| T80R | cutover时v1仍有逐课结果，但v2已VOID或明确无current completion | 先冲正可逆奖励，再DELETE_CURRENT；历史参与/settlement/流水保留，不留陈旧分或伪零分completion行 |
| T80S | SCORE_GRADUATION换版与课程积分、固定任务积分、完课纠错重结并发 | 三类writer持shared catalog并在score lock后CAS版本；发布持exclusive并原子全量重算，旧规则事务不能覆盖新账户/资格 |
| T80T | cutover时v1 OPEN/STORED输出在v2已失效，或原自动取消输出在v2恢复；另含已处理/READ行 | 仅OPEN/STORED自动取消；仅系统自动取消原行恢复；已处理/已读只保留且不重建同source_ref |

## 12. 完成门禁

只有同时具备以下证据，才可把某批次写成“可启用”：

1. Alembic upgrade/downgrade/check 与数据库约束测试通过；
2. direct/queued 同输入得到同领域结果；
3. 乱序、重放、新 offset 的同业务重复、DELETE tombstone 和并发用例通过；
4. checkpoint、源当前态、脏键和领域写入的事务边界有失败回滚证据；
5. DOM 原始学生 ID 未进入海外 SQL、日志、账本或 payload；
6. 旧新结果按 DOM/OVS 分区对账，差异有逐项解释；
7. 任务只声明“已创建任务事实”，外部动作没有回执时不写“已送达/已执行”；
8. SourceWide Worker、积分和资格门禁在事实层与对账完成前保持关闭；
9. 人工手册 T00–T80 及全部后缀用例留下事件、checkpoint、事实表、Outbox、积分和重放证据；
10. 发布、DMS、真实 DTS 消费和业务验收分别由各自授权执行，不能由代码或文档完成替代。
