# DTS direct 事件人工验证与纠错手册

> 状态：人工验收稿，不是发布完成证明
> 当前运行模式：`TIT_DTS_PIPELINE_MODE=SINGLE_PIPELINE`；文中的 direct 只表示逐事件业务规则，不表示仍有 legacy direct projector
> 原始代码审查基线：`flow/release` `436127d423966bd5077af5ad947956510bd00c30`
> 本次本地复审基线：`release` `f6bb8ef49da1fe7525a2fb63e5674c9bd5af95ea`
> 整理日期：2026-08-21
> 验证原则：先记录“代码实际行为”，再判断“业务是否认可”；不能把代码现状直接当成正确口径。
> 业务规则依据：`DTS_direct开发冻结实施规格.md`；清库与发布顺序以 `DTS_v2部署与切换清单.md` 为准
> 实现准备审查：见 `DTS_direct事件人工验证与纠错手册_审查与累计修改项清单.md`。当前行为与目标行为冲突时，开发必须采用冻结实施规格；不得用代码默认值替代业务规则。

## 1. 这份手册解决什么问题

本手册用于人工制造或观察一条 DTS 事件后，逐层回答以下问题：

1. 这条事件为什么会进入或不进入 direct 投影；
2. 代码读取 `before`、`after` 中的哪些字段；
3. 会新增、更新、删除哪一张宽表的哪一行；
4. 会怎样增减教师聚合；
5. 什么情况下事件会被 `ignored`，但 checkpoint 仍然前进；
6. 什么情况下整批失败，不推进 checkpoint，也不向 DTS ACK；
7. 数据库中应该观察什么，才能证明处理结果；
8. 哪些现行行为可能违反业务口径，需要人工确认或修正。

本手册负责“逐事件人工验收”；表结构、幂等、状态机、集合选择器、纠错命令、迁移顺序和目标测试以
`DTS_direct开发冻结实施规格.md` 为准。本手册中出现的 `ignored`、单行课程和增量差值，若明确标注为
“现行行为”，只用于定位旧代码差异，不能继续实现为目标行为。

本手册不证明以下事项已经完成：

- 国内、海外真实 DTS 都已稳定消费；
- SDK checkpoint 已经同步写入 Kafka broker；
- PRE 数据库已经迁移到代码 head；
- 两张宽表字段已经与真实源表完成业务对账；
- SourceWide Worker、积分、资格已经允许恢复。

## 2. 处理依据

### 2.1 代码依据

| 层级 | 代码入口 | 本手册据此判断什么 |
|---|---|---|
| 官方 DTS SDK 桥接 | `gaea/dts-ingest/java/.../TitDtsTransportBridge.java` | `UserRecord` 如何组成有界批、何时输出 `EVENT`、何时接受 durable ACK |
| Java/Python 协议 | `backend/app/dts_java_transport.py` 的 `OfficialJavaDtsTransport.run` | offset 连续性、REPLAY/ADVANCE、Python 落库成功后才 ACK |
| 事件标准化 | `backend/app/dts_source_consumer.py` 的 `build_change_event` | 表名、操作、字段、before/after 如何进入统一事件 |
| 国内隐私处理 | `protect_domestic_student_ids`、`assert_domestic_event_protected` | 国内学生 ID 何时转换成 `dom:v1:<HMAC-SHA256>` |
| 批处理入口 | `DtsEventProcessor.process_batch` | 事件怎样进入 direct sink；处理结果如何统计为 processed/ignored/duplicate |
| 数据库事务和 checkpoint | `backend/app/dts_ingest_store.py` 的 `PostgresDtsEventSink.apply_batch`、`_apply_direct_batch_transaction` | 宽表与 checkpoint 是否在同一事务、重复和缺口怎样处理 |
| 逐表业务处理 | `backend/app/dts_direct_projector.py` 的 `_apply_*` | 每张源表具体修改哪些宽表字段和教师聚合 |
| 宽表 Outbox | `20260806_39_source_wide` 迁移中的 Trigger | 宽表行变化后为什么会出现 `source_wide.changed.v1` |
| 下游消费 | `backend/app/source_wide_worker.py` | Outbox 被消费后如何进入教师、逐课结果和积分刷新 |

### 2.2 业务与数据语义依据

以下材料用于判断代码行为是否符合产品口径：

1. `README.md`；
2. `contracts/教师端共享任务表契约.md`；
3. `contracts/教师端积分与课程读取对照表.md`；
4. `docs/数据库表结构.md`；
5. `docs/DTS事件直接投影规则.md`。

必须保留的通用语义：

```text
NULL  = 来源未提供或尚不确定
false = 来源明确证明该事实不成立
0     = 来源明确证明数量为 0
```

人工验证时，如果发现“没有收到异常事件”被直接写成 `false`，必须单独记录，不能直接判为通过。

### 2.3 国内标识统一为 `dom`

已确版目标契约中，国内标识在所有命名空间统一为 `dom`。`dmo` 不再是可接受的目标值，只能用来描述当前代码和存量数据中待迁移的旧值。

| 使用位置 | 国内目标值 | 海外目标值 | 说明 |
|---|---|---|---|
| DTS `source_region` | `dom` | `ovs` | checkpoint、事件身份、隐私边界和运行区域判定 |
| 源表前缀 | `dom_*` | `ovs_*` | 例如 `dom_appoint`、`dom_user_teacher_grading` |
| DTS 应用/配置名 | `*-dom`、`TIT_DTS_SOURCE_REGION=dom` | `*-ovs`、`TIT_DTS_SOURCE_REGION=ovs` | 不允许把 `dmo` 写入 `source_region` |
| 国内学员伪名 token | `dom:v1:<HMAC-SHA256>` | 不适用 | token 前缀固定为 `dom:v1:` |
| `teacher_source_wide.teach_area_type` | `dom` | `ovs` | 当前代码仍会写 `dmo`，后续代码/数据迁移必须改为 `dom` |

目标契约为同值匹配：

```text
DTS source_region='dom'
→ 要求教师 teach_area_type='dom'

DTS source_region='ovs'
→ 要求教师 teach_area_type='ovs'
```

但当前代码实际是 `source_region='dom' → teach_area_type='dmo'`。在后续统一代码与数据迁移完成前，人工验证必须同时记录“现行实际值”和“目标契约值”，不得把两者混为已完成。

后续代码批次必须联动修改教师宽表存量值、direct/queued 投影、Peak 时段判定、下游契约和测试，不能只做字符串替换。

### 2.4 已确版的代课与课程子事件口径

本节是人工验收应当使用的业务标准，不代表当前代码已经实现。

#### 2.4.1 一节源课程可以有多条教师参与事实

- `dom_appoint.id` 是源课程 ID；
- `dom_appoint.t_id` 是当前被指派的教师，不是这节课全部教师历史；
- 同一 `appoint_id` 可以对应多条教师参与记录；
- 参与记录需要独立业务键，不能继续只用 `appoint_id` 做唯一键。

#### 2.4.2 `t_id` 变更就是代课事件

以下规则以 V2 已存在该课程为前提。fresh-start 上线后不回填旧课程；若首个收到的课程主表事件是
UPDATE，且 V2 当前态没有该 `appoint_id`，则按 7.3 的缺失课程规则忽略，不能只凭这次 UPDATE
补造旧课程及代课参与。

对一条 `dom_appoint UPDATE`，如果：

```text
before.t_id = A
after.t_id  = B
A != B
```

则必须立即：

1. 保留 A 的原课程参与记录；
2. 把 A 的课程状态标记为 `t_absent`；
3. 新增 B 的课程参与记录；
4. B 的初始状态取本次 `appoint.after.status`；
5. 不等待 `dom_teacher_absent_reason` 事件才判定 A 缺席。

如果再发生 `B → C`，则 B 同样变为 `t_absent`，并新增 C 的参与记录。

#### 2.4.3 缺席原因只补证据和决定动作

`dom_teacher_absent_reason` 不决定“教师是否缺席”。缺席事实已经由 `appoint.t_id` 变更建立。

该事件必须使用：

```text
appoint_id + t_id
```

定位对应教师的参与记录，然后：

1. 补充或更新缺席原因；
2. `reason_type='Unfilled Lesson Memo'` 时幂等触发 `P-REL-MEMO`；
3. 其他任意非空 `reason_type` 幂等触发 `P-REL-ATTENDANCE`；
4. `reason_type` 为空时只保留可追溯的源事实，不因缺席原因创建任务；
5. 保证原因事件早于或晚于 `appoint.t_id` 变更到达时，最终结果相同。

#### 2.4.4 完课教师在首次进入 `end` 时冻结

课程首次进入 `status='end'` 时，当时 `appoint.t_id` 对应的教师被冻结为实际完课教师。

- 这节课最终产生的积分归该教师；
- 原教师的 `t_absent` 记录、缺席原因和缺席动作继续保留；
- `end` 之后再改 `t_id` 不能静默改变完课教师，必须走显式纠错流程。

#### 2.4.5 课程子事件按 `appoint_id` 入账，不以 `end` 为前置条件

评价、投诉、处罚和 QA 等底表事件只要带有 `appoint_id`，就算在对应源课程上；处理事件时不要求课程已经 `end`。

- 有明确 `t_id` 的事件，同时可定位到对应教师参与记录；
- 只有 `appoint_id` 的事件，先作为课程级事实保留，不因当前老师变更而丢失或复制；
- 是否 `end` 只影响完课计数和最终积分结算，不影响子事件本身是否入账。

`teacher_favorite` 和 `teacher_blacklist` 是例外：它们是“教师—学生”关系事实，不以单一
`appoint_id` 作为业务身份，也不要求师生之间已经存在完成课程。关系当前态必须独立保存。
收藏加分是另一层事实：课程进入 `end` 后，在完课时间第 24 小时检查该师生是否处于收藏关系；
命中时只把这次收藏加分归因到一节课程，同一师生不能因持续收藏在后续课程重复加分。拉黑关系不做
这种完课归因。

#### 2.4.6 DOM 评价与评价标签的已确版口径

`dom_appoint.use_point` 不用于过滤课程。评价判定分支读取的是 `dom_user_teacher_grading.use_point`，并由它决定本条评价看 `score` 还是看 `type`：

| `use_point` | 权威字段 | 差评 | 好评 | 不参与本分支判定的字段 |
|---|---|---|---|---|
| `buy` | `score` | `score IN (1,2)` | `score IN (4,5)` | `type` |
| `free` | `type` | `type='unsatisfactory'` | `type='satisfactory'` | `score` |

因此：

- `buy` 课程不能因 `type='satisfactory'` 被判为好评，只看 `score`；
- `free` 课程不能因 `score=1/2/4/5` 被判定好差评，只看 `type`；
- `buy` 的 `score` 不在 1/2/4/5，或 `free` 的 `type` 不是上述两个值时，本条评价不标记为好评或差评；
- `use_point` 空或不是 `buy/free` 时，忽略该评价记录：不猜测应读 `score` 还是 `type`，不标记好评或差评，不报错，不进入待确认。

“忽略”指该记录不对课程评价事实产生贡献，不是永远保留它以前产生的贡献。因此 UPDATE 时：

| before.use_point | after.use_point | 目标处理 |
|---|---|---|
| `buy/free` | 空或其他 | 撤销 before 的好差评贡献，after 不产生新贡献 |
| 空或其他 | `buy/free` | 忽略 before，按 after 的权威分支写入新贡献 |
| 空或其他 | 空或其他 | 对评价事实无影响，checkpoint 正常前进 |

`dom_grading_label_log` 负责提供课程的评价标签。对每条有效标签事件，必须同时保留：

```text
appoint_id
label_id
label_name
```

`label_id` 是稳定身份，`label_name` 是业务展示和规则匹配值。不能只保留逗号拼接后的 `label_name`，也不能仅按名称删除或改名。

本次确认的表与规则是 DOM 口径。现行代码对 OVS 同名后缀表使用了同一处理逻辑；在源表字段和业务口径未确认一致前，不应自动将这份 DOM 规则宣称为 OVS 已确版口径。

## 3. 所有事件共用的处理过程

### 3.1 现行正常链路

```text
阿里云 DTS UserRecord
→ Java 识别操作、表名和字段
→ direct 模式过滤无关表，并裁剪白名单字段
→ Java 输出 EVENT × N + BATCH_COMPLETE
→ Python 校验 topic / partition / offset
→ 国内事件删除原始学生 ID，并生成 HMAC token
→ DtsEventProcessor.process_batch
→ PostgreSQL 开启一个批次事务
→ 锁定当前 stream，读取数据库 checkpoint
→ DtsDirectWideProjector 按 offset 顺序处理每条事件
→ 更新 lesson_source_wide / teacher_source_wide
→ 更新 dts_ingest_checkpoints
→ PostgreSQL 事务提交
→ Python 发送 DURABLE_ACK_BATCH
→ Java 对最后一条连续 ADVANCE 请求 SDK checkpoint
```

### 3.2 一个事件可能得到的四种结果

| 结果 | 宽表 | checkpoint | ACK | 含义 |
|---|---|---|---|---|
| `PROCESSED` | 进入了表级处理；不保证最终一定有差异写入 | 前进 | 有 | 表、操作和基础路由有效 |
| `IGNORED` | 不修改，或没有命中目标行 | 前进 | 有 | 现行 direct 放弃业务影响；目标 v2 只允许控制事件、未知表或退役来源走该分支 |
| `DUPLICATE` | 不重复修改 | 不重复写 | 有，动作是 REPLAY | 事件 offset 已低于数据库 checkpoint |
| `FAILED` | 整批回滚 | 不前进 | 无 | offset、字段、隐私、结构或 SQL 不变量失败 |

### 3.3 批次原子性

生产配置一次最多请求 2000 条，Java 硬上限为 2048 条，并受 8 MiB payload 上限保护。

同一批次中：

- 按 offset 顺序执行；
- 任意一条发生结构、隐私、业务不变量或数据库错误，整批回滚；
- 只有整批事务成功后才 ACK；
- 人工验证单条事件时，应尽量确保该批只有目标事件，或完整记录同批所有 offset。

### 3.4 现行 direct 模式的数据落点

正常情况下 direct 只写：

- `teacher_source_wide`；
- `lesson_source_wide`；
- `dts_ingest_checkpoints`；
- 宽表 Trigger 产生的 `outbox_events`；
- `dom_complaint_cate` 参考字典对应的少量 `dts_source_rows`；
- 国内 HMAC 指纹契约行。

direct 不写：

- `dts_ingest_events` 业务事件账本；
- 通用业务表 `dts_source_rows` 当前态镜像；
- `dts_dirty_keys` 投影队列。

因此现行 direct 的 `ignored` 事件一旦越过 checkpoint，后续补齐主记录、字典或关联关系时不会自动恢复。
这是必须修复的旧行为，不是业务取舍。目标 v2 必须在同一接收事务写
`dts_ingest_events + dts_source_rows + dts_dirty_keys + checkpoint`，缺依赖时保留源事实并待重算；
完整链路和事务边界见 `DTS_direct开发冻结实施规格.md` 第 3 节。

### 3.5 事件覆盖快速索引

| 来源表 | direct 处理函数 | 主要目标 | 主要作用 |
|---|---|---|---|
| `dom_teacher` | `_apply_teacher` | `teacher_source_wide`、相关课程 | 创建/更新/删除教师，退出 cohort 时删除课程 |
| `dom_appoint` / `ovs_appoint` | `_apply_appoint` | 两张宽表 | 创建/更新/删除课程，并增减教师聚合 |
| `dom_teacher_class_schedule` | `_apply_teacher_class_schedule` | 教师宽表 | 档期从非 on 变为 on 时单向累加 |
| `dom_teacher_certification` | `_apply_teacher_certification` | 教师宽表 | 更新 `is_cpl_tesol` |
| `dom_teacher_absent_reason` | `_apply_teacher_absent_reason` | 课程宽表、教师聚合 | 更新缺席原因和 no-notice 计数 |
| `dom_teacher_penalty` | `_apply_teacher_penalty` | 课程宽表、教师聚合 | 更新迟到、早退、异常和完美完课计数 |
| `*_user_teacher_grading` | `_apply_user_teacher_grading` | 课程宽表、教师聚合 | 现行同时检查 score/type；目标 DOM 按 grading.use_point 分支判定好差评 |
| `*_grading_label_log` | `_apply_grading_label_log` | 课程宽表 | 现行只增删标签名称；目标 DOM 同时保留 label_id/label_name |
| `*_grading_label` | `_apply_grading_label` | 所有匹配课程 | 标签字典改名或删除时扫描替换名称 |
| `*_teacher_favorite` | `_apply_teacher_favorite` | 课程宽表、教师聚合 | 归因最近完课并更新收藏事实 |
| `*_teacher_blacklist` | `_apply_teacher_blacklist` | 课程宽表、教师聚合 | 归因最近完课并更新拉黑事实 |
| `dom_complaint_cate` | `_apply_complaint_cate` | `dts_source_rows`、匹配课程 | 保存分类参考字典并同步名称变化 |
| `*_complaint` | `_apply_complaint` | 课程宽表、教师聚合 | 写权威有效投诉及分类名称 |
| `*_user_complaint` | `_apply_user_complaint` | 无 | 始终 ignored，等待权威 complaint |
| `*_qa_task_close_camera_record` | `_apply_qa_task_close_camera_record` | 课程宽表 | 更新未开摄像头 |
| `*_qa_ac_classroom_record` | `_apply_qa_ac_classroom_record` | 课程宽表 | 仅为现行代码遗留；已确版目标不再把该表作为 CPU、网络来源 |

其中 `*` 表示对应的 `dom_` 或 `ovs_` 地区前缀；教师共享表和投诉分类字典只来自 DOM。
`*_qa_task_fake_early_leave_record` 已由业务决策整体退役，不再属于目标事件覆盖范围；代码 head 已由
rev73 删除业务字段、投影、触发和 API 契约。兼容期若旧订阅仍送达该表，只允许推进位点并记录
`IGNORED_RETIRED_SOURCE`，不得形成业务事实。

## 4. 人工验证前的统一准备

### 4.1 安全门禁

业务字段未完成对账前保持：

```text
TIT_SOURCE_WIDE_ENABLED=false
TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED=false
```

这样可以验证 DTS 到宽表和 Outbox，但不让未确认的数据立即产生积分或不可逆资格。

### 4.2 每条事件必须记录的输入

```text
测试编号：
来源区域：dom / ovs
topic：仅记录安全别名
partition：
source_partition_epoch_id：
epoch_sequence / status：
stream_generation_id / epoch_opening_id：仅记录已脱敏安全别名或hash
offset：
operation：INSERT / UPDATE / DELETE
table_name：
source_timestamp：
record_id_type / canonical record_id：
before：脱敏后的完整白名单字段
after：脱敏后的完整白名单字段
所在批次的 start_next_offset / end_next_offset：
提交后 source_row_revision：真正current变化必填；replay/no-op为空
```

严禁把 DTS 密码、数据库密码、原始国内学生 ID 或完整连接串写入验证记录。

目标 v2 的 `source_position` 由上述 envelope 生成结构化 JSONB，人工不得手拼；它只用于envelope结构、
同真实partition/epoch位点诊断和审计。source current、typed selector、纠错expected/resolved的“更晚”一律
比较同source key锁内分配的整数 `source_row_revision`，不能调用position比较器替代。offset 9/10仅在同
epoch/topic/partition内按bigint验证接入顺序；同key跨partition/topic应报SOURCE_KEY_PARTITION_DRIFT，不能
构造跨partition全序。空topic、缺epoch/partition/offset、未验证generation/opening或record类型不符应整批
失败且checkpoint不前进。

### 4.3 通用验证 SQL

以下 SQL 使用 DMS 命名参数表示法；执行时替换为本次安全值。

#### checkpoint

```sql
SELECT c.source_region,
       c.source_partition_epoch_id,
       c.topic,
       c.partition_id,
       c.consumer_group,
       c.next_offset,
       c.is_current,
       e.epoch_sequence,
       e.status AS epoch_status,
       e.stream_generation_id,
       e.epoch_opening_id,
       e.v2_epoch_bootstrap_floor,
       c.row_version,
       c.updated_at
FROM public.dts_ingest_checkpoints AS c
JOIN public.dts_source_partition_epochs AS e
  ON e.source_region = c.source_region
 AND e.source_partition_epoch_id = c.source_partition_epoch_id
 AND e.topic = c.topic
 AND e.partition_id = c.partition_id
WHERE c.source_region = :source_region
  AND c.source_partition_epoch_id = :source_partition_epoch_id
  AND c.topic = :topic
  AND c.partition_id = :partition_id
  AND c.is_current IS TRUE;
```

正常新事件提交后：

```text
next_offset = 当前事件 offset + 1
```

#### 教师宽表

```sql
SELECT *
FROM public.teacher_source_wide
WHERE tchr_id = :teacher_id;
```

#### 课程宽表

```sql
SELECT *
FROM public.lesson_source_wide
WHERE "课程id" = :course_id;
```

#### 宽表 Outbox

```sql
SELECT outbox_id,
       aggregate_type,
       aggregate_id,
       event_type,
       status,
       payload,
       created_at
FROM public.outbox_events
WHERE event_type = 'source_wide.changed.v1'
  AND aggregate_id IN (:teacher_id, :course_id)
ORDER BY created_at DESC
LIMIT 20;
```

无差异 UPDATE 不一定产生新 Outbox；不能只凭“有事件”推断“宽表一定发生变化”。

#### direct 数据面约束

```sql
SELECT COUNT(*) AS ingest_event_rows
FROM public.dts_ingest_events;

SELECT COUNT(*) AS dirty_key_rows
FROM public.dts_dirty_keys;

SELECT source_region, source_table, COUNT(*) AS row_count
FROM public.dts_source_rows
GROUP BY source_region, source_table
ORDER BY source_region, source_table;
```

干净 direct 边界后，前两张表不应重新积累业务数据；`dts_source_rows` 只允许出现投诉分类参考数据和 HMAC 契约用途的数据。

#### 国内学生隐私

```sql
SELECT "课程id", "学员id"
FROM public.lesson_source_wide
WHERE "学员id" IS NOT NULL
  AND "学员id" !~ '^dom:v1:[0-9a-f]{64}$'
  AND "课程id" IN (:dom_course_ids);
```

国内课程查询结果必须为 0 行。

## 5. 完整示例：`dom_teacher` 新增一名教师

### 5.1 测试输入

```json
{
  "operation": "INSERT",
  "table_name": "dom_teacher",
  "after": {
    "id": "123",
    "real_name": "Test Teacher",
    "center_type": 0,
    "is_full_time": 5,
    "status": "on",
    "status_on_time": "2026-08-20 00:00:00",
    "status_off_time": null,
    "last_on_time": "2026-08-20 00:00:00",
    "course": "h5_tc"
  }
}
```

### 5.2 现行代码处理过程

| 步骤 | 代码依据 | 实际处理 | 人工证据 |
|---|---|---|---|
| 1. 表路由 | `source_table_suffix` | 识别为 `teacher` | heartbeat 中事件被接收，不是无关表预过滤 |
| 2. 字段裁剪 | `SOURCE_FIELD_WHITELIST['teacher']` | 只保留教师宽表需要的 9 个字段 | 事件中其他敏感或无关字段不得进入 SQL |
| 3. offset 检查 | `_apply_direct_batch_transaction` | 当前 offset 必须等于数据库 `next_offset`，或作为首事件建立 checkpoint | gap 时整批失败 |
| 4. 教师 ID | `_apply_teacher` | 使用 `after.id` 作为 `tchr_id` | `teacher_source_wide.tchr_id='123'` |
| 5. cohort | `_teacher_in_cohort` | `status_on_time::date` 必须位于运行配置 cohort | 不在 cohort 时目标缺失则 ignored |
| 6. 教师区域字段 | `_teacher_area` | 现行代码：`course` 含 `global_cn/global_pool` 为 `ovs`，否则为旧值 `dmo` | 现行会写 `dmo`；已确版目标应写 `dom` |
| 7. 入职窗口 | `_apply_teacher` | `onboard_date=D`，`onboard_30d_end_date=D+29` | 本例应为 2026-08-20 至 2026-09-18 |
| 8. 初始化 | `_empty_teacher_values` | 课程、评价、档期计数初始化为 0，TESOL/self-intro 为 NULL | 精确检查目标行 |
| 9. 宽表写入 | `_upsert` | 新增 `teacher_source_wide` | 目标教师一行 |
| 10. Outbox | 数据库 Trigger | 教师 INSERT 产生 `source_wide.changed.v1` | Outbox 中 aggregate_id=123 |
| 11. checkpoint | `_write_checkpoint` | 写 `next_offset=offset+1` | checkpoint 精确读回 |
| 12. ACK | `OfficialJavaDtsTransport.run` | PostgreSQL 提交后才发送 durable ACK | DB 失败时不应看到成功 heartbeat |

### 5.3 新教师字段预期

| 目标字段 | 现行计算 |
|---|---|
| `tchr_id` | `teacher.id` |
| `real_name` | `teacher.real_name` |
| `center_type_id` | `teacher.center_type` |
| `center_type_desc` | 现行：0/6→HBT、1→CBT、5→TBT、其他/空→`NULL`；已确版目标：1→CBT、5→TBT，其他所有值（含 `NULL`）→HBT |
| `bu` | 根据 `is_full_time` 映射 HBT/OBT |
| `status` | `teacher.status` |
| `status_on_date`、`onboard_date` | `status_on_time::date` |
| `status_off_date` | `status_off_time::date` |
| `last_on_date` | `last_on_time::date` |
| `job_days` | `status_off_date` 或北京时间当天，减去 onboard_date |
| `job_month` | `floor(job_days/30)+1` |
| `teach_area_type` | 现行 global 课程池为 `ovs`，其他写旧值 `dmo`；目标国内值统一为 `dom` |
| `onboard_30d_end_date` | onboard_date + 29 天 |
| 所有课程/评价/档期计数 | 0 |
| `is_cpl_tesol` | NULL，等待证书事件 |
| `is_self_introduce` | NULL |

`center_type_desc` 的已确版目标映射必须使用默认 HBT，不使用“未知则 NULL”：

| `dom_teacher.center_type` | 目标 `center_type_desc` |
|---|---|
| `1` | `CBT` |
| `5` | `TBT` |
| 其他任意值 | `HBT` |
| `NULL` | `HBT` |

当前 direct/queued 的 `_CENTER_DESCRIPTIONS` 只把 0/6 显式映射为 HBT，其他未列出值和 `NULL` 会写成 `NULL`，与已确版口径不符。

### 5.4 不应误判的下游结果

教师宽表行创建只证明 DTS 主数据已经落入源宽表，不等于：

- `teachers` 已经初始化；
- G01–G09 已经创建；
- 积分已经计算；
- 教师端已经可见。

上述动作要等 SourceWide Worker 消费 Outbox；人工验证 DTS 阶段时应保持 Worker 关闭。

### 5.5 需要人工确认的业务问题

1. `course` 到目标教师区域值 `ovs/dom` 的映射是否覆盖真实全部取值；
2. `is_full_time` 的固定映射是否仍是当前业务口径；`center_type_desc` 已确版为 1→CBT、5→TBT、其他/空→HBT；
3. 活跃教师 `job_days` 使用处理当天计算，是否允许同一教师在不同事件日得到不同值；
4. 教师 UPDATE 改变地区或入职窗口时，现行 direct 不重算已有课程，这一点是否必须修复。

## 6. 教师主表：`dom_teacher`

### 6.1 INSERT

前置条件：

- `after.id` 非空；
- `after.status_on_time` 在 cohort；
- `after` 至少包含建立教师宽表需要的完整字段。

现行结果：

- 创建 `teacher_source_wide`；
- 聚合字段从 0 开始；
- 不创建课程；
- checkpoint 前进；
- 宽表变化产生 Outbox。

### 6.2 UPDATE

如果教师已存在且 UPDATE 后仍在 cohort：

- 更新身份、组织、状态、地区和入职窗口；
- 保留课程、评价、档期、TESOL、自我介绍等已有聚合字段。

如果教师原来不存在，但本次 `after` 满足 cohort，UPDATE 可以创建教师宽表行。

如果 UPDATE 后退出 cohort，且目标教师存在：

1. 删除该教师全部 `lesson_source_wide`；
2. 删除 `teacher_source_wide`；
3. checkpoint 前进。

纠错重点：

- `course` 导致教师区域从国内改为 `ovs`，或 `status_on_time` 在 cohort 内发生变化时，现行代码保留已有课程和聚合；
- 这可能造成教师地区/窗口与课程不一致，应列为必测用例。

### 6.3 DELETE

- 教师存在：先删全部课程，再删教师；
- 教师不存在：ignored；
- 两种情况 checkpoint 都前进。

### 6.4 教师在线、在营和资格状态

目标数据库把三类状态分开，不能互相代替：

| 维度 | 目标值 | 权威事实 |
|---|---|---|
| 教师在线状态 | `NEW / EXISTING / LEFT / BLOCKED` | 新增 `teachers.online_status`；由 `dom_teacher.status + onboard_date` 确定 |
| 在营状态 | `IN_CAMP / GRADUATED` | 复用 `teachers.graduation_state`；业务已取消 `NOT_IN_CAMP` |
| 金牌状态 | `NOT_GOLD / GOLD` | 由 `teacher_qualifications.gold_qualified` 派生，获得后不可回退 |

在线状态映射已确版，`status` 先做去首尾空格和小写归一：

| 源条件 | `online_status` |
|---|---|
| `status='on'` 且业务日期位于入职日至入职日+29 天（含首尾） | `NEW` |
| `status='on'` 且业务日期已达入职日+30 天 | `EXISTING` |
| `status='off'` | `LEFT` |
| `status='hei'` | `BLOCKED` |
| 其他状态，或 `on` 缺少/无效/未来入职日 | `NULL` 并标记 `SOURCE_MISSING`，不猜测 |

“最近一个月”与本系统新师窗口统一按北京业务日期的连续 30 个自然日计算。
`NEW → EXISTING` 是时间驱动变化；即使 `dom_teacher` 没有新事件，系统也必须在入职日+30 天
通过日更或等价定时重算把教师转为 `EXISTING`。

状态流：

1. 教师进入培养范围后为 `IN_CAMP`；
2. 入职满 30 天仍未达到出营条件时，继续保持 `IN_CAMP`，第 30 天不自动转为未在营；
3. 30 天以后首次达到出营条件，仍可转为 `GRADUATED`；
4. 首次出营时写入不可变 `graduation_qualified_at`，首次达到金牌时写入不可变 `gold_qualified_at`；
5. `LEFT/BLOCKED` 只改变教师在线状态，不停止后续积分计算，也不删除已获得的出营或金牌事实；计分仍由课程、评价、任务等业务事实决定。

现有 `teacher_qualifications` 已有上述两个时间字段，不应在 `teachers` 再复制一份时间事实。读取视图
可以分别暴露为“出营时间”和“金牌时间”。

在营状态只有“在营”和“成功出营”两个值。满30天、离职、拉黑或暂未达到出营线都不会产生第三个在营状态。

这里的“分数固定在 100”理解为：教师的**出营分数**永久记录为 100，但系统另外继续计算
“金牌进度累计分”。数据库对应为：

- `graduation_score_locked`：首次出营时冻结为当时规则的出营分数，当前为 100；
- `raw_total_score`：实际累计分，出营后、达到金牌后都继续累计，不封顶；首次达到 200 时获得金牌；
- `public_total_score`：教师端显示分，`min(raw_total_score, 200)`，最高显示 200；
- 出营状态和 `graduation_score_locked` 不因后续重算变化。

例如：教师达到 100 分时出营，`graduation_score_locked=100`；之后又获得 100 分，
`raw_total_score=200` 并获得金牌；之后再获得 50 分时，实际累计分为 250，但教师端
仍显示 200，出营分数仍为 100。

## 7. 课程主表：`dom_appoint` / `ovs_appoint`

### 7.1 进入课程宽表的业务条件

已确版业务口径不使用 `appoint.use_point` 或 `appoint.status` 过滤课程。`buy`、`free` 以及
`status='cancel'/'on'` 的课程都必须进入课程事实；`status=NULL` 或出现未来新增状态值时，也不能仅因
状态值而拒绝进入课程宽表。评价时使用的分支字段是 `dom_user_teacher_grading.use_point`，不是用
`dom_appoint.use_point` 决定课程是否入库。

目标条件为：

1. 学员标识非空；
2. 教师宽表已存在；
3. 目标契约中，DOM 来源事件（`source_region='dom'`）匹配 `teach_area_type='dom'`，OVS 来源事件匹配 `teach_area_type='ovs'`；现行代码对 DOM 仍匹配旧值 `dmo`，应判为待修复。

入职 30 天不再是课程事实的准入上限。符合上述结构和地区条件的课程全量保留，
出营和金牌后的课程也可继续产生实际积分。30 天只用于 `NEW→EXISTING` 和明确指定为
“新师 30 天观察”的指标，不能被复用为通用课程、评价、收藏或计分过滤器。

`status` 只作为课程事实原值保存。完课、计分、预约量等派生口径如需判断状态，必须在各自规则中
独立判断，不能把派生指标条件重新变成课程宽表准入条件。当 `before.t_id != after.t_id` 时，无论
`after.status` 是什么值，都必须执行代课拆分；详见 7.3.2。

现行 direct 和 queued 代码仍额外要求 `use_point='buy'` 且
`status NOT IN ('cancel','on')`，因此 `free`、`cancel`、`on` 课程都会被判为范围外：

- 目标课程已存在时，事件可能删除该宽表行并回减聚合；
- 目标课程不存在时，事件 ignored 且 checkpoint 前进；
- 后续这些被过滤课程的评价、标签、投诉和 QA 等子事件也可能因缺少课程主记录而 ignored。

所以人工验收必须将现行 `use_point`、`status` 前置过滤都判为待修复缺口，不能当成正确业务条件。

### 7.2 INSERT

满足范围时创建 `lesson_source_wide`：

| 课程字段 | 来源或计算 |
|---|---|
| `课程id` | `appoint.id` |
| `上课日期` | `date`，缺失时尝试 `start_time::date` |
| `上课时间` | `time`，缺失时尝试 `start_time::time` |
| `是否高峰` | 地区、星期、上课时间共同判断 |
| `老师id` | `t_id` |
| `学员id` | 海外原 ID；国内必须是 HMAC token |
| `课程状态` | `status` |
| `缺席原因明细` | 初始为 `NULL`；只由同一 `appoint_id + t_id` 的 `dom_teacher_absent_reason.reason_type` 补充 |
| 迟到/早退/收藏/拉黑/未开摄像头等 | 现行代码初始化为 `false` 或 `NULL` |
| `cpu占用过高`、`网络延迟过高` | 来源待替换；新来源确版前保持 `NULL`，不得从 `qa_ac_classroom_record` 推断 |

现行 direct 还会按 7.5 的旧规则给教师增加本课程贡献。目标实现必须把“事实入库”
和“聚合计数”拆开。目标聚合已经冻结：`total_booked_cnt` 统计教师普通参与次数，所有源状态
（包括 `on/cancel/其他/NULL`）均计 1；`peak_booked_cnt` 统计其中明确为 Peak 的参与。

### 7.3 UPDATE

fresh-start 只处理联合 H0 之后的新事件，课程基线只由 H0 后的 INSERT 建立。对
`dom_appoint/ovs_appoint UPDATE`：

- V2 已存在该课程：按本节规则正常合并；`t_id` 变化时执行代课拆分；
- V2 不存在该课程：事件账本记 `IGNORED` 和
  `SOURCE_CHANGE_WITHOUT_CURRENT_IGNORED`，checkpoint 正常前进，但不创建 source current/version、
  课程、教师参与或 dirty key；
- 以后收到该课程 INSERT 时，从 INSERT 建立新基线；即使此后重放之前已忽略的 UPDATE，也必须仍然
  判为 duplicate/ignored，不能修改新基线。

这个例外只针对课程主表的缺失课程 UPDATE，不得扩大为“所有乱序子事件都丢弃”；评价、标签、投诉等
子事实仍按各自的持久化和依赖重算规则处理。

appoint UPDATE 保留课程已经接收的以下子事实：

- 缺席原因；
- 迟到、早退；
- 评价和评价详情；
- 投诉分类；
- 现行单行上的收藏、拉黑兼容字段；目标关系权威事实不保存在课程主行；
- 未开摄像头；
- CPU、网络的旧来源值在迁移时清为 `NULL`；替换来源接入前，appoint UPDATE 也必须继续保持 `NULL`。

处理聚合时：

```text
旧教师聚合 -= 旧课程贡献
更新课程主字段
新教师聚合 += 新课程贡献
```

所以教师、学员、课程日期、状态、Peak 发生变化时，都应验证旧归属和新归属。

如果 UPDATE 后不再满足范围：

- 已有课程被删除并回减教师聚合；
- 目标课程不存在时 ignored。

#### 7.3.1 `t_id` 变更的现行代码行为

当 `before.t_id=A` 、`after.t_id=B` 时，现行 direct 不会识别并新建一次代课参与，而是按同一 `课程id=appoint.id` 更新原行：

```text
更新前：appoint-9001 / A / old_status
更新后：appoint-9001 / B / after.status
```

同时，它会保留原行上的缺席原因、处罚、评价、投诉和 QA 字段，再把该行的教师聚合贡献从 A 转移到 B。

这与已确版业务口径不符：

- A 的课程参与历史消失；
- A 不会被标记为 `t_absent`；
- B 没有新的教师参与记录；
- A 的教师级子事实可能被带到 B；
- 多次代课和首次 `end` 教师冻结都无法表示。

#### 7.3.2 `status='on'` 且 `t_id` 变更的现行行为

例如：

```text
before.status = on
before.t_id   = A
after.status  = on
after.t_id    = B
```

当前 direct 的执行顺序是：

1. 使用 `appoint.id` 查找当前唯一课程行；
2. `_appoint_in_scope` 先检查 `after.status`；
3. 因 `status='on'` 判定为范围外；
4. 已有课程行则删除并回减旧教师聚合，没有课程行则 ignored；
5. 函数直接返回，不再读取 `after.t_id`，也不比较 `before.t_id` 和 `after.t_id`。

queued 模式的结果相同：它会保留 appoint 当前态，但宽表投影在 `_appoint_in_scope` 为 false 时直接删除原课程行并返回，同样不执行旧教师/新教师拆分。

因此对问题“会不会新增旧教师的课程记录”，当前答案是：**不会**。而且：

- 不会保留/创建 `A / t_absent`；
- 不会创建 `B / on`；
- 如果原来已有一条课程宽表记录，还会把该记录删除。

这与已确版的“课程准入不按 `status` 过滤”和“任何 `t_id: A→B` 都立即建立 A 缺席事实并新增 B 参与事实”同时冲突。目标处理顺序必须改为：

```text
先检测 before.t_id != after.t_id
→ 幂等保留/创建 A 参与并标记 t_absent
→ 新增 B 参与并保存 after.status 原值（本例为 on）
→ 再由每项派生规则独立决定该参与是否计入完课、计分、预约等指标
```

`status='on'` 必须进入课程宽表。它是否进入完课、计分或预约聚合是另一个问题，不能吞掉课程事实
或已经发生的教师代课历史。这也再次说明：源课程事实、教师参与事实和派生指标范围不能继续由
同一个“是否进单行课程宽表”条件同时决定。

### 7.4 DELETE

现行代码会物理删除课程、回减旧教师的课程/完课/Peak/异常/评价和去重学员计数，并在目标课程
不存在时 ignored。目标 v2 不物理删除历史：首次 end 前写 source tombstone、撤销当前派生贡献并保留
参与历史；首次 end 后 DELETE 创建完课纠错 Case，冻结归属和已结分保持不变，等待明确的
`KEEP_FROZEN_COMPLETION / UPDATE_COMPLETION_SNAPSHOT / TRANSFER_COMPLETION / VOID_COMPLETION` 决定。课程删除不得回减独立的
收藏/拉黑关系人数。

### 7.5 教师聚合变化依据

下表是目标 v2 冻结聚合口径。现行代码差异另按 K 项记录。

| 课程事实 | 教师字段变化 |
|---|---|
| 教师普通参与；源 status 任意 | `total_booked_cnt`；按参与计数，不按源课程去重 |
| 上述参与且源课程明确为 Peak | `peak_booked_cnt` |
| 首次 end 冻结参与 | `total_completed_cnt` |
| 首次 end 冻结参与且 Peak | `peak_completed_cnt` |
| 参与状态=`t_absent` | `absent_cnt` |
| 冻结完课参与且处罚集合明确迟到 | `late_cnt` |
| 冻结完课参与且处罚集合明确早退 | `early_cnt` |
| 同一参与缺席/迟到/早退任一成立 | `anomaly_cnt`，按参与去重 |
| 冻结完课且迟到、早退都明确为 false | `perfect_cnt`；任一 NULL 不计并标 SOURCE_MISSING |
| `t_absent` 参与的最新原因=`No Notification` | `no_notice_cnt` |
| 好评/差评标签非 NULL | `feedback_total_eval_cnt` |
| 好评标签=true | `feedback_praise_cnt` |
| 差评标签=true | `feedback_negative_cnt` |
| 冻结完课课程存在任意未删除 `dom/ovs complaint` 来源行 | `feedback_complaint_cnt`；不依赖分类字典或有效条件，`user_complaint` 不计 |
| 上述课程存在至少一条满足有效条件的 complaint typed 行 | `feedback_valid_complaint_cnt`；grandson=NULL 或字典缺失仍计，分类只影响 L0/输出证明 |
| 同师生至少一节完成课程 | `first_completed_student_cnt` 按学员去重 |
| 当前存在收藏关系 | `feedback_favorite_cnt` 按学员去重；不要求已有完课 |
| 当前存在有效拉黑关系 | `feedback_block_cnt` 按学员去重；不要求已有完课 |
| 完课第 24 小时仍为收藏关系 | 创建唯一课程收藏加分归因；同一教师+同一学生终身只允许一节课获收藏分 |

系统源课程数单独按 `(source_region,source_appoint_id)` 去重，不进入上述教师参与计数。收藏、拉黑
人数来自独立关系当前态；原收藏率、拉黑率本轮固定为 NULL，不再用完成学员数作分母。

### 7.6 已确版的代课目标状态流

以 `appoint_id=9001` 为例。

#### 步骤 A：原始排课

```text
dom_appoint INSERT: t_id=A, status=on
```

目标教师参与事实：

| source_appoint_id | participation_seq | teacher_id | status | 是否当前教师 |
|---|---:|---|---|---|
| 9001 | 1 | A | on | true |

#### 步骤 B：代课 A → B

```text
dom_appoint UPDATE:
before.t_id=A
after.t_id=B
after.status=on
```

目标教师参与事实：

| source_appoint_id | participation_seq | teacher_id | status | 是否当前教师 |
|---|---:|---|---|---|
| 9001 | 1 | A | t_absent | false |
| 9001 | 2 | B | on | true |

同一事务内已确认的聚合结果：

- A：`absent_cnt +1`、`anomaly_cnt +1`，不增加完课数；
- B：此时不增加完课数；
- 系统级“源课程数”仍为 1，必须按 `source_appoint_id` 去重。

聚合模型已确认拆分“源课程数”与“教师参与次数”：A→B 时前者仍为 1，后者为 2。
`total_booked_cnt/peak_booked_cnt` 固定统计教师参与，系统源课程数使用独立指标。

#### 步骤 C：A 的缺席原因到达

```text
dom_teacher_absent_reason INSERT:
appoint_id=9001
t_id=A
reason_type=<type>
```

目标结果：

- 只更新 `9001 / A` 的缺席证据；
- 不改变 B 的课程状态和缺席字段；
- `reason_type='Unfilled Lesson Memo'` 时幂等创建 `P-REL-MEMO`；
- 其他任意非空 `reason_type` 幂等创建 `P-REL-ATTENDANCE`；空值不因缺席原因创建任务；
- 如果该事件早于步骤 B 到达，步骤 B 完成后也必须得到相同结果。

#### 步骤 D：课程子事件到达

评价、投诉、处罚或 QA 事件带 `appoint_id=9001` 时：

- 事件直接计入源课程 9001；
- 不要求 `appoint.status='end'`；
- 有 `t_id` 时同时保留教师指向；
- 没有 `t_id` 时保留为课程级事实，不复制给每条教师参与记录。

#### 步骤 E：课程首次进入 end

```text
dom_appoint UPDATE:
after.t_id=B
after.status=end
```

目标结果：

| source_appoint_id | teacher_id | status | 完课计数 | 最终积分 |
|---|---|---|---:|---|
| 9001 | A | t_absent | 0 | 不获得该课完课积分 |
| 9001 | B | end | +1 | 这节课产生的积分归 B |

同时写入不可被普通 DTS UPDATE 静默改变的“首次 end 教师=B”事实。

#### 步骤 F：end 之后又改 t_id

不自动把完课老师从 B 改成 C，也不自动转移积分。该事件必须进入可审计的显式纠错流程。
运营 KEEP/UPDATE/TRANSFER/VOID 决定提交时，纠错事务同步完成冻结角色、观察和课程积分处理，并原子写
该 COURSE 及受影响 PARTICIPATION 的 `source_wide.changed.v2`。Correction Service 不直接改投诉、摄像头、
差评等 match/任务/Case/提醒；v2 Worker 必须由这些事件唤醒，幂等收口旧 completion 键并刷新新键。
决定提交只可写 `APPLIED_PENDING_PROJECTION`；相关 Outbox 全部 PUBLISHED 后才算下游刷新完成，不能等待
下一条 DTS 碰巧触发。

### 7.7 目标模型对现有表结构的最小要求

要支持上述状态流，至少需要区分：

1. **源课程事实**：以 `source_appoint_id` 定位，保存排课主字段、评价、投诉、QA 等课程级事实；
2. **教师参与事实**：以 `source_region + source_appoint_id + participation_seq` 定位，保存教师、参与状态、缺席原因和是否当前被指派；
3. **完课归属事实**：保存首次进入 `end` 时的参与记录或教师 ID；
4. **积分结果**：可追溯到源课程和完课教师，不以“当前 appoint.t_id”覆盖历史归属。

当前 `lesson_source_wide.课程id` 是单主键，`lesson_score_results.lesson_id` 又与它一对一，因此不能只修 `_apply_appoint`；表结构、Outbox 身份、SourceWide Worker、逐课积分结果和个性化触发的课程键都需要联动调整。

## 8. 排课：`dom_teacher_class_schedule`

以下是现行代码的单向累加行为：

```text
INSERT 且 after.status='on'
UPDATE 且 before.status!='on'、after.status='on'
```

一次有效开启执行：

```text
total_slot_cnt += 1
slot_days += 1
project_code='1v1' → reg_slot_cnt += 1
Peak → peak_slot_cnt += 1
Peak → peak_slot_days += 1
first_open_slot_dt = min(旧值, schedule.date)
重算 capacity 比率
```

前置条件：

- 教师宽表存在；
- 排课日期位于教师入职 30 天窗口。

以下 ignored 且 checkpoint 前进：

- `on→on`；
- `on→off`；
- DELETE；
- 教师不存在；
- 排课日期超出窗口。

现行 direct 不保存 slot 当前态，也不回减关闭/删除事件。目标已经冻结为“当前有效槽集合”：最新未删除
且 `status='on'` 的 schedule 行才计数，off/DELETE 回减，slot 按来源行 ID 去重，slot_days 按 date
去重；只统计入职第 0–29 天观察窗口。详情见冻结实施规格 8.1。

## 9. TESOL：`dom_teacher_certification`

已确版目标条件为：

```text
certification_code = '16'
AND certification_status = 1
```

同一教师只要存在至少一条满足条件的当前证书记录，`is_cpl_tesol=true`；删除或更新一条证书后必须
重算该教师剩余证书，不能因为还有另一条有效 16 号证书而错误写成 false。

| 事件 | 目标处理 |
|---|---|
| INSERT | 重新判断该教师是否存在 `code='16' AND status=1` |
| UPDATE | before/after 涉及的教师都重算当前证书集合 |
| DELETE | 重算 before 教师剩余证书；无其他有效 16 号证书才写 false |

教师不存在时，目标 v2 保留证书源行并将教师脏键保持待重算；教师到达后自动恢复，不得永久 ignored。

纠错重点：现行 direct 判断 `certification_type='tesol'`，字段白名单也没有 `certification_code`；
同时不保存多条证书当前集合，删除一条记录时不会检查是否还有另一条有效 16 号证书。

## 10. 缺席与处罚

### 10.1 `dom_teacher_absent_reason`

现行 direct 仅通过 `appoint_id` 定位已有课程，没有使用缺席事件中的 `t_id` 进一步定位教师参与记录，
并且仍会读取 `reason_desc` 和 `appoint.cancel_reason`。这些都不是已确版目标。

| 事件 | 现行处理 |
|---|---|
| INSERT | `缺席原因明细=reason_desc`，为空时取 `reason_type` |
| UPDATE | 清除 before 课程的原因，再写 after 课程 |
| DELETE | 对应课程 `缺席原因明细=NULL` |

现行 direct 在课程状态为 `t_absent` 且原因精确等于 `Unfilled Lesson Memo` 时改变
`no_notice_cnt`；已确版目标应改为最新 `reason_type='No Notification'`，不再用
`Unfilled Lesson Memo` 文案判断该计数。

已确版目标规则：

1. `appoint.t_id` 从 A 变为 B 时就立即把 A 标记为 `t_absent`；
2. 缺席原因事件只补证据和决定动作；
3. 必须用 `appoint_id + t_id` 定位 A，不能写到当前接课教师 B；
4. `缺席原因明细` 的唯一业务来源是 `dom_teacher_absent_reason.reason_type`；不读取 `reason_desc`，也不回退到 `appoint.cancel_reason`；
5. 同一课程、同一教师有多条缺席原因时，按 `add_time`、`id` 取最新记录的 `reason_type`；
6. 事件早到、晚到、重放应产生相同最终结果；缺席原因记录删除后重新计算剩余记录，没有剩余记录则写 `NULL`。

`No Notification` 和 `Unfilled Lesson Memo` 是两个不同的 `reason_type` 精确值：前者决定
`no_notice_cnt`，后者决定 `P-REL-MEMO` 任务。不得将两者等价，也不得用
`reason_desc` 文案补齐或猜测。

纠错重点：

- 当前只按 `appoint_id` 更新，代课后可能把 A 的缺席原因写到 B；
- 缺席原因先到时，后续 appoint 覆盖教师可能将原因一起转移给 B；
- 现行 appoint INSERT 会把 `cancel_reason` 写入缺席原因，且缺席事件优先使用 `reason_desc`，两者都必须移除；
- 删除缺席原因记录后，目标逻辑只重算剩余 `reason_type`，不得恢复 `appoint.cancel_reason`；
- 缺席原因任务映射已确版，但 direct 仍需先解决只按 `appoint_id` 定位和乱序事件丢失问题。

#### 10.1.1 缺席原因到个性化任务的已确版映射

当 SourceWide Worker 启用并消费到课程宽表变化后，现行规则是：

| `reason_type` / `缺席原因明细` | 规则 | 内部输出 | 教师动作 |
|---|---|---|---|
| `NULL` 或空 | 不因缺席原因命中 | 无缺席原因任务 | 无 |
| `Unfilled Lesson Memo` | `TR-REL-LESSON-MEMO` | `P-REL-MEMO` 教师任务 | 阅读内嵌 Lesson Memo 规则文档 |
| 其他任意非空值 | `TR-REL-ATTENDANCE` | `P-REL-ATTENDANCE` 教师任务 | 完成阔知课程 595 Reliability |

现行路径的人工验证必须分层：

1. 缺席原因是否写入了正确教师的课程或参与事实；
2. `personalized_trigger_matches` 是否以正确的教师和课程证据命中；
3. `task_assignments` 是否幂等创建了对应任务；
4. 任务行创建只证明共享任务事实已建立，不证明教师已读、已完成或外部消息已送达。

该映射只允许读取 `dom_teacher_absent_reason.reason_type`。任务使用现有稳定模板和教师级幂等键；
同一教师因重放、多节课或多条同类原因反复命中时，不重复创建同一稳定任务。任务行只证明任务事实已创建，不证明已阅读或已完成。

纠错生命周期已经冻结：原因未对应 `t_absent` 参与前只保留源事实；原因变更或删除时旧
`personalized_trigger_matches` 置 `SUPPRESSED`；改成另一规则时使用另一稳定键，同一规则和参与以后恢复时
重新激活原 match 并将 `match_revision+1`，不是新增同键证据行。已经创建的教师级
`task_assignments` 不删除、不取消、不回退，已完成终态保持不变。证据键固定为
`absence:{rule_code}:{source_region}:{source_appoint_id}:{participation_seq}`，assignment 键固定为
`personalized:{task_code}:{teacher_id}`。

### 10.2 `dom_teacher_penalty`

通过 `appoint_id` 定位已有课程。

当 `appeal_status` 为空或等于 2：

```text
迟到=false
早退=false
```

其他情况：

```text
迟到 = in_time - lesson_start_time > 30 秒
标准结束时间 = lesson_start_time + 30 分钟
早退 = 标准结束时间 - out_time > 30 秒
```

| 事件 | 现行处理 |
|---|---|
| INSERT | 按 after 计算迟到、早退 |
| UPDATE | 先清 before 课程为 false，再按 after 计算 |
| DELETE | 对应课程迟到、早退都写 false |

同步更新教师 `late_cnt`、`early_cnt`、`anomaly_cnt`、`perfect_cnt` 及相关比率。

以上是现行 v1 的直接覆盖行为，不是目标 v2 的落参与规则。v2 必须先把每条处罚映射到唯一
`participation_seq`：只看同课程、同 `t_id` 的参与；已有同教师冻结完课参与时优先定位该 completion；
否则以严格解析的 `lesson_start_time` 落入唯一的 `[assigned_at,ended_at)` 参与区间。`t_id` 缺失、时间
缺失/非法、区间证据不足或命中不唯一时，分别保留
`PENDING_DATA:PENALTY_PARTICIPATION_TEACHER_MISSING/AMBIGUOUS`，不得复制给当前教师或同教师全部参与。
appoint 新版本、首次冻结、完课纠错和 penalty UPDATE/DELETE 都要重跑 before/after 映射。每个参与只从
映射到自己的未删除处罚完整集合计算：`appeal_status=2` 明确不生效，NULL 是 unknown 且不能先过滤，
其他非空值才计算 late/early。每个维度均为 true 优先；无 true 但有 unknown 时为 NULL/SOURCE_MISSING；
只有 scope COMPLETE 且其余记录都明确 false、全部为 2 或集合为空时才为 false，不得按 v1 直接清零。

## 11. 评价与评价标签

### 11.1 `dom_user_teacher_grading`

现行代码的有效记录条件：

```text
is_del 为空或 0
status 为空或 0
```

目标 v2 只把 `is_del` 非空且非 0 的行视为删除；不按 grading.status 过滤。然后从剩余当前集合按冻结
比较器取最新一条，再根据该行 `use_point` 分类。这样 status 的未知枚举不会静默丢掉评价来源。

现行代码映射：

```text
score=1/2 → 差评分保存分数，差评标签=true
score=4/5 → 好评标签=true
type='unsatisfactory' → 差评标签=true
type='satisfactory' → 好评标签=true
```

现行逻辑没有使用 `use_point` 选择分支，而是同时检查 `score` 和 `type` 并做 OR 判定。更关键的是，`SOURCE_FIELD_WHITELIST['user_teacher_grading']` 当前根本不包含 `use_point`，即使源事件带了该字段，进入 projector 前也会被裁掉。

已确版的 DOM 目标映射：

| `use_point` | 目标判定 | `差评标签` | `好评标签` | `差评分` |
|---|---|---:|---:|---|
| `buy` | `score IN (1,2)` | true | false | 保存 1/2 |
| `buy` | `score IN (4,5)` | false | true | `NULL` |
| `buy` | 其他 score | false | false | `NULL` |
| `free` | `type='unsatisfactory'` | true | false | `NULL` |
| `free` | `type='satisfactory'` | false | true | `NULL` |
| `free` | 其他 type | false | false | `NULL` |
| 空或其他 | 忽略该评价记录 | 无贡献 | 无贡献 | 无贡献 |

比较时应先对 `use_point` 和 `type` 做去首尾空格及小写归一；`score` 只做数值 1/2/4/5 精确比较，不自行扩展区间。

| 事件 | 现行处理 |
|---|---|
| INSERT | 给 appoint_id 对应课程写评价 |
| UPDATE | 清 before 课程，再按 after 写新课程；无效 after 清三个评价字段 |
| DELETE | 清除差评分、差评标签、好评标签 |

同步调整教师评价总数、好评数、差评数和比率。

业务入账不以课程已经 `end` 为前置条件：底表事件只要有 `appoint_id`，就计入该源课程。课程首次进入 `end` 时，该课的积分才结算给冻结的完课教师。

纠错重点：

- 当前字段白名单丢弃 `use_point`，无法正确实现上述分支；
- 当前 OR 逻辑会让 `buy` 课受 `type` 影响，也会让 `free` 课受 `score` 影响；
- 如果 `score` 和 `type` 相互矛盾，当前可能同时写出好评和差评；
- `use_point` 无效的记录应从评价当前态中排除；有效 → 无效 UPDATE 必须撤销旧贡献，不能只把 after 当成 no-op；
- 删除最新评价时目标必须从剩余当前集合恢复次新评价；现行代码不会恢复。

### 11.2 `dom_grading_label_log`

已确版目标取消 `type=1` 和 `status='normal'` 两个过滤条件。每一条能够提供下列关联字段的
`grading_label_log` 都作为标签事实：

```text
appoint_id
label_id
label_name
```

已确版目标是同时保留 `label_id` 和 `label_name`，并使用 `appoint_id` 归属源课程。

| 事件 | 目标处理 |
|---|---|
| INSERT | 幂等保存 `appoint_id + label_id + label_name` 关联，不判断 type/status |
| UPDATE | 移除 before 精确关联，再保存 after 精确关联，不判断 type/status |
| DELETE | 只移除该 log 对应关联；其他相同标签 log 仍存在时课程标签继续保留 |

课程不存在时，目标先持久化 log 并等待课程脏键重算；不得永久 ignored。现行 direct/queued 仍有
`type/status` 过滤，属于待修复差异。

现行代码的缺口：

- `label_id` 虽然已进入 DTS 字段白名单，但 direct 路径没有把它写入课程事实；
- `评价详情` 只保存逗号拼接的名称，无法追溯每个名称的 `label_id`；
- 更新和删除按 `label_name` 处理，同名不同 ID 会相互影响；
- 删除一条记录时，不检查是否还有另一条同 ID 或同名有效记录；
- `dom_grading_label` 字典改名现在也按名称全表扫描，没有通过 `label_id` 稳定定位。

目标增删改应以标签身份为主：

| 事件 | 目标处理 |
|---|---|
| INSERT | 为 `appoint_id` 幂等保存 `label_id + label_name` |
| UPDATE | 用 before 的 `appoint_id + label_id` 移除旧关联，再按有效 after 写入新关联 |
| DELETE | 只删除 before 定位的标签关联，不按名称误删其他 ID |

如果同一 `appoint_id + label_id` 有多条尚未删除的 log，删除其中一条时还需检查其他记录，不能
直接删除课程标签事实；这里不使用 log 的 `type/status` 判断是否保留。

重复差评任务按冻结完课教师和 `label_id` 统计：同一标签至少命中 2 个不同
`(source_region,source_appoint_id)` 时创建 `P-FB-NEGATIVE`。每课 match 键为
`negative-label:{teacher_id}:{label_id}:{source_region}:{source_appoint_id}`，assignment 键为
`personalized:P-FB-NEGATIVE:{teacher_id}:{label_id}`。评价、标签或完课归属纠错使条件不成立时抑制
match；同一课程标签恢复时重新激活同一 match 并增加 revision。assignment 不删除、不取消、不回退。

### 11.3 OVS 评价口径边界

现行代码对 `ovs_user_teacher_grading` 和 `ovs_grading_label_log` 复用了上述同一函数。本轮范围已经
冻结：OVS 评价事件只持久化来源当前态，不套用 DOM 分类、不产生新的 OVS 评价积分；现行兼容读取
使用独立开关，禁止与 v2 双写。完成 OVS 源表对账后另行发布规则版本。

### 11.4 `dom_grading_label` / `ovs_grading_label`

这是标签字典名称变更。以下仅是现行 v1 行为：

| 事件 | 现行处理 |
|---|---|
| INSERT | 无 before 名称，ignored |
| UPDATE | 扫描所有课程，把评价详情中的旧名称替换为新名称 |
| DELETE | 从所有课程评价详情中删除旧名称 |

该处理按名称匹配，不按 label ID 建立持久关联。

目标 v2 仍持久化字典来源记录，但课程标签的 `label_id + label_name` 均以
`grading_label_log` 为准：字典 INSERT/UPDATE/DELETE 不覆盖 log 名称、不删除课程标签，也不改写既有
任务标题。log 同一 `appoint_id + label_id` 有多条时，展示名取剩余记录中按
`create_time,dt,canonical source_log_id,source_row_revision` 最新一条；`source_position` 与source_version仅审计，
不参与选名。删除最新 log 后恢复次新名称。

## 12. 收藏与拉黑

收藏和拉黑与其他课程子事件不同：它们的业务本质是“教师—学生”关系，而不是某一节课的独立
事实。关系成立不要求师生之间存在任何完成课程，必须先维护独立的关系当前态；课程宽表不能再充当
关系唯一事实源。

### 12.1 关系事实依据

关系事件的业务键和时间使用：

```text
teacher_id / tea_id
student token
favorite: source id + add_time
blacklist: source id + valid_start_time / valid_end_time / add_time
DELETE: DTS source_timestamp 作为失效时间
```

favorite 的 `add_time` 是判断历史收藏时点的唯一权威起点。缺失时可用 source_timestamp 稳定事件排序并
更新当前关系，但必须标 `effective_time_evidence_status=SOURCE_MISSING`；无论 source_timestamp 在第 24
小时观察点之前还是之后，都不能据此确认历史真值或加 5 分。到期观察进入
`WAITING_EVIDENCE/FAVORITE_EFFECTIVE_TIME_MISSING`；后续同来源记录补齐 add_time 后重新置 PENDING，按
权威业务时间重评。它与 HISTORY scope 不完整的 `WAITING_HISTORY` 是两个不同原因。

即使没有课程也要更新关系事实和教师去重人数，不能 ignored。关系 UPDATE/DELETE 持续更新当前收藏、
拉黑状态；同一教师、同一学生的多条关系记录需要按稳定 ID 维护，不能删除一条就假定整个关系消失。

### 12.2 `dom_teacher_favorite` / `ovs_teacher_favorite`

| 事件 | 目标关系处理 |
|---|---|
| INSERT | 写入或激活师生收藏关系 |
| UPDATE | 按 before/after 更新关系当前态和关系时间线 |
| DELETE | 删除对应关系记录；重算该师生是否仍有其他有效收藏关系 |

教师 `feedback_favorite_cnt` 按当前收藏关系中的学员去重调整，不要求存在完成课程。

收藏课程加分归因规则：

1. 课程首次进入 `end` 时冻结实际完课教师；源课程 `appoint.end_time` 已确认为权威完课时间，也是 24 小时观察起点；
2. 在权威完课时刻 `+24h` 判断该教师与学生在该时点是否为收藏关系；不得用 DTS 到达时间代替；
3. 为 true 时，把收藏事实和收藏分归因到该课程；
4. 同一教师、同一学生终身只允许一个课程归因获分；持续收藏、取消后重新收藏都不开启新获分周期；
5. 关系表继续更新当前关系，但已经完成的 24 小时课程归因不移动到其他课程。

该规则要求保存关系时间线并支持“截至某时点”的查询；只保存当前关系或只消费最新行不足以验证
第 24 小时的历史状态。多个课程同时候选时先按 `observed_at=end_time+24h` ASC；完全相同时按来源表固定
ID类型决胜：NUMERIC以任意精度numeric ASC（9早于10），TEXT按canonical UTF-8 bytes ASC，禁止varchar/
数据库默认collation或到达顺序。同一source table的ID类型固定，类型不符应在接入时拒绝。

延迟场景的处理已确版。例如课程 8 月 1 日 10:00 完课，系统在 8 月 2 日 10:00
判定当时未收藏，因此没加分；但 12:00 才收到一条“业务生效时间为当天 09:00”的
收藏事件。这说明学生在完课第 24 小时其实已经收藏。已确认按业务生效时间补加
这 5 分；反过来，如果后到的纠错证明当时并未收藏，则扣回该 5 分，并重新选择第一节
真正满足条件的课程。普通的第 24 小时以后才新增或取消收藏，不回溯改变已归因课程。

取消后再次收藏仍持续更新师生关系当前态和时间线，但不撤销、不移动已锁定的唯一课程归因，也不给第二节课重复加分。

### 12.3 `dom_teacher_blacklist` / `ovs_teacher_blacklist`

有效拉黑条件：

```text
is_valid_forever 为真
或 valid_end_time 年份 >= 2999
```

| 事件 | 目标关系处理 |
|---|---|
| INSERT | 有效时写入师生拉黑关系 |
| UPDATE | 按 before/after 更新关系当前态 |
| DELETE | 删除对应记录并重算该师生是否仍有其他有效拉黑关系 |

教师 `feedback_block_cnt` 按当前有效拉黑关系中的学员去重调整，不要求存在完成课程，也不强制归因
到某节课程。

`P-FB-BLACKLIST` 按一个地区内同一教师当前不同 `student_token` 统计，首次达到 2 人时触发：match 键为
`blacklist-threshold:{source_region}:{teacher_id}`，assignment 键为
`personalized:P-FB-BLACKLIST:{teacher_id}`。低于 2 人时抑制同一 match，恢复阈值时重新激活并增加
revision；assignment 始终保留。DOM/OVS 关系证据不跨地区合并，但同一全局 teacher_id 复用一个 assignment。

纠错重点：现行 direct/queued 都先找最近完成课程，找不到课程就 ignored；关系表没有持久集合，
删除一条关系记录也不会检查是否还有其他有效关系记录。这与目标口径不符。

## 13. 投诉

### 13.1 `dom_complaint_cate` 分类字典

这是 DOM/OVS 投诉共用的唯一分类字典；物理来源和分类 aggregate 的 `source_region` 都固定为 `dom`，
不得为 OVS 复制第二套分类键。

| 事件 | 字典处理 | 已有课程处理 |
|---|---|---|
| INSERT | 保存当前分类，`is_deleted=false` | 没有旧名称可替换，通常计为 ignored |
| UPDATE | 更新分类当前值 | 中文名称变化时，替换课程三个投诉字段中的旧名称 |
| DELETE | `is_deleted=true` | 清除课程中匹配的旧名称 |

人工验证不能要求字典一定先到：OVS 或 DOM 投诉可以先保存为待依赖，DOM 字典后到必须反向唤醒两区。

### 13.2 `dom_complaint` / `ovs_complaint`

有效投诉必须同时满足：

```text
complaint_type = 13
AND (complaint_type_grandson IS NULL OR complaint_type_grandson != 82)
AND approve = 'y'
AND validity = 1
```

有效时按 canonical ID 精确查询未删除的 `dom_complaint_cate`：

```text
complaint_type          -> dom_complaint_cate.id -> 投诉一级分类 = cate_cn_name
complaint_type_child    -> dom_complaint_cate.id -> 投诉二级分类 = cate_cn_name
complaint_type_grandson -> dom_complaint_cate.id -> 投诉三级分类 = cate_cn_name
```

三级名称再按 `Unicode NFKC -> 去首尾空白 -> 连续空白折叠为一个 ASCII 空格` 得到
`category_l3_normalized`，与 `complaint_category_rules.category_l3_normalized` 精确等值匹配；不做模糊、
大小写折叠或跨级回退。非空分类 ID 的字典行缺失/删除时写
`SOURCE_MISSING:COMPLAINT_CATEGORY_NOT_FOUND`；grandson ID 自身为空写
`PENDING_DATA:COMPLAINT_CATEGORY_MISSING`。一级/二级 ID 为空不妨碍可证明的三级严重度路由，但不能猜测二级
“出席问题”或“网络设备问题”。

| 事件 | 现行处理 |
|---|---|
| INSERT | 有效且字典完整时写三个分类 |
| UPDATE | 清 before 课程，再按 after 写分类；无效 after 清分类 |
| DELETE | 清除三个投诉分类字段 |

现行代码在字典缺失时：

- 当前投诉事件 ignored；
- 已有课程投诉值保持不变；
- checkpoint 前进；
- 后续字典到达不会自动重放投诉。

目标 v2 必须保留投诉源行和课程/分类脏键；投诉分类依赖统一使用
`(source_region=dom,key_type=COMPLAINT_CATEGORY,category_id)`。字典 INSERT/UPDATE/DELETE 按 before/after ID
反查 `source_course_complaints` 中 DOM/OVS 两区上述三个分类字段引用并唤醒各自 COURSE 键，最终与
“字典先到”结果一致。字典 DELETE 使对应展示分类进入 SOURCE_MISSING，不得保留无依据旧名称。

同步更新教师投诉计数和比率。

投诉事件只要带有 `appoint_id` 并满足权威有效条件，就计入对应源课程；不要求课程已经 `end`。

同课多条有效投诉的当前展示和四路输出只选一条“最新”：严格按
`add_time DESC NULLS LAST, course_date DESC NULLS LAST, canonical id DESC, source_row_revision DESC` 决胜。
不得用 DTS 到达顺序或source_position覆盖业务 add_time；时间都为空时仍由ID/revision稳定选择。完整typed集合继续用于
投诉计数和 L0，不因只路由最新而丢弃其他有效行。

投诉产生任务/Case/提醒还要求课程已有冻结完课教师、`complaint_type_grandson` 非空并精确命中分类规则。
路由固定为：二级“出席问题”→`TR-REL-ATTENDANCE/P-REL-ATTENDANCE`；二级“网络设备问题”→
`TR-QUALITY-NETWORK-EQUIPMENT` task-less 提醒；其他 P0/P1→`TR-FB-SEVERE-COMPLAINT` Ops Case；
其他 P2/P3/P4→`TR-FB-GENERAL-COMPLAINT/P-FB-COMPLAINT`。每课分类 match 键统一为
`complaint:{rule_code}:{source_region}:{source_appoint_id}:{completion_participation_seq}:{complaint_type_grandson}`，assignment 键为
`personalized:P-FB-COMPLAINT:{teacher_id}:{complaint_type_grandson}`。投诉失效、分类/严重度变化或完课
归属纠错时抑制 match；同键恢复时重新激活并增加 revision；assignment 不删除、不取消、不回退。
出席 assignment 复用 `personalized:P-REL-ATTENDANCE:{teacher_id}`；严重 Case key 和网络提醒 source_ref
分别以 `complaint-case:`、`complaint-notification:` 加同一 rule/地区/课程/分类身份生成。来源纠错只自动
取消仍 OPEN 的 Case 和仍 STORED 的提醒；已处理 Case、已读提醒保留审计，不回退也不重复创建。
`complaint_type_grandson IS NULL` 仍计入有效投诉指标，但只能形成
typed complaint/course current 的 `PENDING_DATA:COMPLAINT_CATEGORY_MISSING`，不得创建 trigger match、
assignment、Case 或提醒。

### 13.3 `dom_user_complaint` / `ovs_user_complaint`

现行 direct 永远 ignored，因为该表没有 `approve/validity`，不能作为权威投诉事实。
目标 v2 仅保存经过白名单和隐私处理的 source version/current 供接入审计；INSERT/UPDATE/DELETE 均不进入
`source_course_fact_current`，不补投诉分类，不形成业务脏键或 v2 Outbox，也不产生指标、任务或积分，
只推进 ingest ledger/checkpoint。这里的“保存来源”不等于“投诉补充事实”。

## 14. QA 事件

已确版目标只保留当前有权威来源的未开摄像头事件。CPU、网络字段继续保留，但旧
`qa_ac_classroom_record` 来源已经取消；假早退业务整体取消。

### 14.1 `dom_qa_task_close_camera_record` / `ovs_qa_task_close_camera_record`

| 事件 | 现行处理 |
|---|---|
| INSERT | `未开摄像头=true` |
| UPDATE | before 课程 false，after 课程 true |
| DELETE | before 课程 false |

现行课程不存在时 ignored；目标 v2 先保留来源行。摄像头当前事实按同课程未删除记录 EXISTS
重算，删除一条时若仍有其他记录，不得误写 false；集合完整且无记录时才可写 false。

未开摄像头为 true 的 task-less 提醒使用 rule `TR-QUALITY-CAMERA-OFF`。来源事实不等待 end，但该表
没有教师字段，提醒等待冻结完课教师；match/source_ref 分别为
`camera-off:TR-QUALITY-CAMERA-OFF:{source_region}:{source_appoint_id}:{completion_participation_seq}` 与
`camera-notification:TR-QUALITY-CAMERA-OFF:{source_region}:{source_appoint_id}:{completion_participation_seq}`。
当前集合 true→false 时抑制 match，并仅取消尚为 STORED 的提醒；恢复时复用原行。完课 TRANSFER 使用
新 participation 键，新教师新建幂等提醒，旧已读提醒只保留历史。

### 14.2 已取消或待替换的 QA 来源

- `*_qa_task_fake_early_leave_record`：业务已取消，新订阅移除该表；兼容期若仍收到旧订阅事件，只记
  不含业务 payload 的 `IGNORED_RETIRED_SOURCE` 路由元数据并推进 checkpoint，不解析或写来源版本/当前态；
- `*_qa_ac_classroom_record`：不再作为 `cpu占用过高`、`网络延迟过高` 的来源；兼容期事件同样只记
  无 payload 的退役路由元数据并推进 checkpoint；
- 新 CPU、网络来源确版前，两字段保持 `NULL`，不得用无事件、旧表无匹配或默认值推断为 `false`；
- 切换时必须清理旧来源写入的 CPU、网络存量值，否则旧值仍可能影响提醒和课堂质量积分。

## 15. 控制事件、无关表和未知关联

现行以下事件不修改宽表但会正常推进 checkpoint：

- DTS `HEARTBEAT`、`BEGIN`、`COMMIT` 等控制事件；
- 能明确识别为不在业务白名单内的数据表；
- 子事件找不到教师或课程；
- 范围外且目标不存在的教师/课程事件；
- `user_complaint`。

目标 v2 中，“子事件找不到教师或课程”不再属于 ignored：先写来源当前态和脏键，待依赖到达后
重算。`user_complaint` 只保存 source version/current 作为接入审计，不进入投诉领域事实，也不产生
业务脏键、Outbox、指标、任务或积分。真正允许业务忽略的只剩控制事件、非白名单表、明确退役来源，
以及这里已冻结为 source-only 的 `user_complaint`。

以下情况失败而不是 ignored：

- offset 大于数据库 `next_offset`，出现缺口；
- 同一批次不是同一 stream；
- 批次 offset 非严格递增；
- 主键发生原地变化；
- 必填业务依赖 ID 缺失；
- 国内原始学生 ID 可能进入海外 SQL；
- QA JSON 非法；
- 计数回减后出现负数；
- 数据库约束、Trigger 或 SQL 失败。

## 16. 推荐人工验证顺序

必须同时验证正常顺序、乱序持久化后恢复、重放以及集合记录删除后的回退选择。现行 direct 若把已列入
冻结规格的业务子事件永久 ignored，应判为目标失败。

| 编号 | 事件 | 主要验证点 |
|---|---|---|
| T00 | 控制事件 | 不改宽表，checkpoint 前进 |
| T01 | `dom_teacher INSERT` | 教师创建、零状态、Outbox、checkpoint |
| T02 | 重放 T01 offset | DUPLICATE，不重复创建或累加 |
| T03 | `dom/ovs_appoint INSERT` | 课程范围、基础字段、教师聚合 |
| T04 | penalty INSERT/UPDATE/DELETE | 迟到、早退、perfect/anomaly 增减 |
| T05 | grading INSERT/UPDATE/DELETE | 评价字段和教师评价聚合 |
| T06 | grading_label_log | 标签集合增删改 |
| T07 | complaint_cate → complaint | 字典映射、投诉聚合 |
| T08 | complaint 先于字典 | 投诉源行持久化并待重算；字典到达后自动恢复，最终与正序一致 |
| T09 | favorite / blacklist | 无课程也保存关系并按学员去重；收藏另验收 end+24h 唯一课程归因 |
| T10 | QA | 未开摄像头正常处理；已取消或待替换来源 ignored |
| T11 | schedule | 按当前有效 on 行集合重算；off/DELETE 回减、行 ID 去重、日期去重 |
| T12 | TESOL | 仅 `certification_code='16' AND certification_status=1` 为 true；多证书删除后重算 |
| T13 | appoint 改教师/日期/状态 | 旧贡献减、新贡献加 |
| T14 | teacher 改地区/入职窗口 | 课程事实保留；地区冲突标记并重算，30 天变化只影响明确观察指标 |
| T15 | appoint DELETE | 首次 end 前 tombstone 并撤销当前贡献；首次 end 后创建纠错 Case，不静默清分 |
| T16 | teacher DELETE | 教师源行 tombstone；课程、参与、积分、资格历史不得级联删除 |
| T17 | offset gap | 整批失败、checkpoint 不前进、无 ACK |
| T18 | 同批一条非法 QA JSON | 整批原子回滚 |
| T19 | 子事件先到、主事件后到 | 子事件先持久化；主事件到达后自动重建，最终与正序相同 |
| T20 | checkpoint 不存在时双消费者并发 | 检查首批事件是否被重复投影 |
| T21 | appoint `t_id: A→B` | 目标应保留 A=`t_absent` 并新增 B；现行会覆盖同一行，应判为不符业务口径 |
| T22 | 缺席原因在代课后到达 | 应按 `appoint_id+t_id` 只更新 A，不得写到 B |
| T23 | 缺席原因在代课前到达 | 代课后原因仍属于 A，最终状态必须与 T22 相同 |
| T24 | `A→B→C`、C 首次进入 `end` | A/B 都保留缺席记录，C 冻结为完课教师并获得课程积分 |
| T25 | 评价/投诉/QA 在非 `end` 状态到达 | 只要 `appoint_id` 有效就计入课程，不因未完课而 ignored |
| T26 | 首次 `end` 后又改 `t_id` | 冻结归属不变；幂等创建 COURSE_COMPLETION_CORRECTION Case |
| T27 | `appoint.use_point='free'` 的课程 | 课程应正常入库；现行被过滤应判为缺陷 |
| T28 | grading `use_point='buy'` 的 score 1/2/4/5 | 只用 score 判定，type 即使矛盾也不得改变结果 |
| T29 | grading `use_point='free'` 的 satisfactory/unsatisfactory | 只用 type 判定，score 即使矛盾也不得改变结果 |
| T30 | grading `use_point` 空/未知 | 忽略该记录，不猜测 score/type、不报错；有效值改为未知时撤销旧贡献 |
| T31 | grading_label_log INSERT，type/status 任意 | 按 appoint_id 保存 label_id+label_name，重放不重复，不按 type/status 过滤 |
| T32 | grading_label_log UPDATE 改课程/ID/名称/type/status | 移除 before 精确关联并写入 after；type/status 变化不得删除标签 |
| T33 | grading_label_log DELETE 且存在重复 log | 只删除该 log 影响；其他未删除关联仍保留课程标签 |
| T34 | 国内教师区域统一 | 新投影和存量迁移后 `teach_area_type='dom'`；业务表不再产生 `dmo`，DOM 课程、排课和 Peak 计数仍正确 |
| T35 | teacher.center_type 映射 | 1→CBT、5→TBT，0/6/其他数值/字符串/NULL均→HBT；direct/queued 结果一致 |
| T36 | appoint `status='on'` 且 `t_id: A→B` | 先保留/创建 A=`t_absent`，再新增 B=`on`；不得因 on 过滤删除或忽略代课历史 |
| T37 | appoint 依次使用 `cancel/on/end/其他值/NULL` | 每种状态均创建或保留课程行并原样保存状态；状态切换不得导致课程行仅因状态被删除 |
| T38 | appoint 有 `cancel_reason`，但无 absent_reason | `缺席原因明细=NULL`，不得从 appoint 补值 |
| T39 | absent_reason 的 `reason_desc` 与 `reason_type` 冲突 | 只保存 `reason_type`；按 `appoint_id+t_id` 归属，多记录取最新、删除后重算 |
| T40 | 旧假早退、qa_ac CPU/网络事件 | 不写课程事实、不触发任务/提醒；CPU、网络在新来源接入前保持 `NULL` |
| T41 | 师生无任何完成课程时收藏/拉黑 | 关系事实和教师去重人数照常更新，不得 ignored |
| T42 | 课程 end+24h 时收藏为 true，之后关系继续存在 | 只给该师生唯一一节课程归因并加分，后续课程不得重复加分 |
| T43 | 收藏在 end+24h 前后发生变化 | 按第 24 小时的历史关系状态判定，不用当前最新值倒推 |
| T44 | complaint 的 grandson 分别为 NULL、82、其他 | NULL 与其他非 82 值有效；82 无效，其余条件保持不变 |
| T45 | 入营超过 30 天后才出营，再达到金牌 | 30 天后仍 IN_CAMP；首次出营后出营分固定为 100，实际分不封顶继续累计；达到 200 获得金牌，教师端最高显示 200；出营/金牌状态与时间均不可回退 |
| T46 | 收藏关系事件延迟到达，但业务生效时间早于 end+24h | 按业务生效时间重算；应加未加时补 5 分，应扣未扣时扣 5 分，并始终只保留一节符合条件的获分课程 |
| T47 | `dom_teacher` 在线状态映射与时间跨界 | on+入职 0–29 天为 NEW，on+入职满 30 天为 EXISTING，off 为 LEFT，hei 为 BLOCKED；没有新 DTS 事件也能在第 30 天完成 NEW→EXISTING |
| T48 | 教师从 NEW/EXISTING 转为 LEFT/BLOCKED 后又到达可计分事实 | 在线状态照常更新，但不因 LEFT/BLOCKED 过滤该事实；正常入账并刷新实际分，教师端仍按 200 封顶显示 |
| T49 | 教师入职超过 30 天后到达课程、评价或可计分事实 | 事实照常入库和计分；30 天不得被复用为通用课程、评价、收藏或积分过滤器 |
| T50 | 同课同教师最新 `reason_type='Unfilled Lesson Memo'` | `缺席原因明细` 保存该值；幂等触发 `P-REL-MEMO`；不增加 `no_notice_cnt` |
| T51 | 最新 `reason_type='No Notification'`，以及另一个非空其他原因 | 两者都幂等触发 `P-REL-ATTENDANCE`；仅 `No Notification` 增加 `no_notice_cnt`；空原因不创建缺席任务 |
| T52 | 同一师生先收藏获分，后取消并重新收藏 | 关系当前态和时间线持续更新；不开启新获分周期，不给第二节课重复加分 |

| T53 | `A→B→A` | 创建 seq=1/2/3，不复活第一次 A |
| T54 | `A→NULL→B` | A 缺席、NULL 不建参与、B 使用下一序号 |
| T55 | 同一事件 `A→B` 且进入 end | 先建 B 参与，再冻结 B |
| T56 | appoint INSERT 即 end | 有 t_id 时立即冻结；缺 t_id 时 SOURCE_MISSING |
| T56A | end 时无教师，后续 NULL→B/离开 end/DELETE | 更新同一可空教师纠错 Case；B 仅为 PENDING_CORRECTION，不自动冻结 |
| T57 | end→on→end、end 后改教师/时间/DELETE | 一个幂等纠错 Case，冻结和积分不自动变化 |
| T57A | 首次 end 前 appoint DELETE 后恢复 | 旧参与 source_deleted、不计 booked、不算 absent；恢复创建下一 seq |
| T57B | A 首次 end 后连续 A→B→C，再 KEEP/TRANSFER/VOID | KEEP/VOID 将决定位置前的 B/C 都置 REJECTED_CORRECTION；TRANSFER 仅指定目标为 COMPLETION，其余拒绝；更晚事件进入新 Case revision |
| T57C | 教师不变，仅完课时间/学生/日期时间/Peak 修正 | UPDATE_COMPLETION_SNAPSHOT 保持原参与角色，更新当前快照并冲正/重结；收藏观察按完整性失效重建 |
| T57D | 首次 end/TRANSFER 并发，或直写错 current/completion/score 指针 | 只提交一个合法决定；部分唯一/deferred trigger 拒绝双角色、悬空和错 teacher/seq |
| T58 | 任意业务子事件先到 | `dts_source_rows` 保留，依赖到达后自动重算 |
| T59 | 最新缺席原因删除或改型 | 恢复次新；旧 match SUPPRESSED，assignment 不删除/不回退 |
| T59A | 同一 absent canonical id 的相同业务时间修订乱序/重放 | typed当前行和选择器只接受更大source_row_revision；position仅审计，旧revision不覆盖 |
| T59B | 同key同epoch/partition offset 9/10、跨partition/topic、reset新epoch重读、非法envelope | offset按bigint；跨partition报DRIFT不造全序；同generation同hash为EPOCH_REPLAY无新revision，异hash冲突；非法事件不推进对应epoch checkpoint |
| T60 | 最新 grading 删除或改未知 use_point | 删除恢复次新；改未知清除当前贡献但不回退旧记录 |
| T60A | 好评/完美/Peak/硬件 true→false或未知→true | 原奖励只冲正一次；恢复后 generation+1 新增奖励，无重复流水 |
| T60B | 重复 label_id 达阈值但当前 label_name NULL→补齐→再 NULL | pending match→正常任务→pending 恢复；不读字典、不重复/改写 assignment，照片变体只按精确原名 |
| T60C | 同 label_id 多条 log 的 create_time/dt 与到达顺序相反，删除最新 | typed行按时间/ID/source_row_revision稳定选名并恢复次新；position仅审计 |
| T60D | 同一 DOM grading canonical id 的业务时间完全相同，修订乱序/重放 | typed当前行和好/差评判定只接受更大source_row_revision |
| T60E | 同 label_id 跨课名称为空/通用/照片标签，交换事件及 cutover 扫描顺序 | 两类 pending match 键稳定、抑制/恢复 revision 正确且不重复；一致后 seed/title/variant/hash 不随顺序变化 |
| T61 | penalty/complaint/摄像头多行删一留一 | 按 OR/EXISTS/最新选择器保留剩余贡献 |
| T61A | 同课两条不同分类有效投诉，删除/失效当前最新 | 指标仍按课程 EXISTS；输出只路由最新，删除后抑制旧 match 并恢复次新分类路由 |
| T61B | 摄像头集合 true→false→true | match 抑制/重激活；仅 STORED 提醒取消后恢复，已读历史不回退且不重复同键提醒 |
| T61C | 投诉四路由后完课 TRANSFER A→B | 旧参与 match 抑制；未处理输出取消、已处理输出保留 A；按新参与给 B 幂等生成对应输出 |
| T61D | 同课较旧 P0、较新 P3 同时有效 | 两条 typed complaint 都保留；输出只路由最新 P3，但 L0 仍统计较旧 P0 并失败关闭资格 |
| T61E | 最新有效投诉 grandson NULL→具体 ID→NULL | typed error 写入→清除并路由→恢复；NULL 阶段无 trigger match/assignment/Case/提醒 |
| T61F | A→B→A，A 的处罚早到/晚到/更新/删除 | 只映射唯一 completion 或时间区间参与；歧义保持 PENDING_DATA，不复制到 seq1/seq3；删除后按剩余集合重算 |
| T61G | OVS 投诉先到、DOM 分类字典后到/改名/删除/恢复 | 全局 DOM 分类键跨区唤醒 OVS COURSE；精确三级规则恢复，结果与字典先到一致 |
| T61H | 同课两条有效投诉 add_time 与到达顺序相反，且一条时间为空 | 按add_time/course_date/id/source_row_revision稳定选最新；position/到达顺序不改变输出 |
| T61I | 同参与处罚为 true+unknown、false+unknown、全 appeal=2、空集合，并切换 scope COMPLETE/STALE | 依次为 true、NULL、COMPLETE false、COMPLETE false；STALE 且无 true 为 NULL |
| T61J | 一般投诉三级名命中/未命中 teacher copy，交换事件/cutover 顺序并在最终 shadow 后换配置 | 批准英文/fallback 固定；原名只留 evidence；同版本 hash 不变，版本变化使 cutover 重跑且不改既有任务 |
| T61K | 同SHA重放、连续三份不同SHA换版、旧Worker并发，并尝试新SHA+旧rule_id或改已发布规则 | 同SHA复用；activation_generation为1/2/3并逐版唤醒两区；错版FK和已发布修改被拒绝，最终shadow后换版要求重跑 |
| T62 | 多张 TESOL 有效证书删一张 | 仍为 true；集合完整且全无才 false |
| T63 | favorite/blacklist 全 CRUD 和重叠记录 | 当前态按完整集合重算，删除一条不误清其他记录 |
| T63A | DOM 先达到拉黑阈值、OVS 后达到同教师阈值 | 只有一个 assignment 且首次 evidence 不变；两地区 match/audit 分别完整，当前贡献按 active match 聚合 |
| T64 | 收藏历史事件晚到 | 早于观察点则补/扣并重选；晚于观察点只改关系当前态 |
| T64A | favorite 缺 add_time，source_timestamp 在观察点前/后，随后补齐 | 缺失时均 WAITING_EVIDENCE 且不加分；补齐权威时间后重评 |
| T64B | 收藏规则/单价换版时归因为AWARDED、held、REVERSED并重放 | 前两者唯一冲正并新generation奖励，held仍held；REVERSED不重开；无重复流水 |
| T64C | 同一师生两课observed_at相同，appoint numeric 9/10或text ID，并交换到达顺序 | numeric选9、text按UTF-8 bytes；唯一归因和流水不漂移 |
| T65 | 完课纠错批准/保留/作废 | 积分冲正幂等，冻结审计保留，历史资格不撤销 |
| T65A | TRANSFER 目标缺 student_token 或 completion_end_time | 教师归属可转移，但不建收藏观察并标 SOURCE_MISSING；补齐后仍需新决定才建观察 |
| T65B | TRANSFER/UPDATE/VOID 涉及四个非收藏课程组件 | 旧奖励唯一冲正；新/更新 completion 按组件 generation 重结，账户与逐课结果同事务一致 |
| T65C | 四类完课决定提交后 Worker 崩溃/重放 | 决定事务原子写 COURSE/PARTICIPATION Outbox；旧输出收口、新键物化，projection 状态可追踪且无重复输出/流水 |
| T66 | schedule on/off/DELETE/重复 | 当前集合和日期去重正确，不累计重复开启 |
| T66A | peak_slot_cnt 39→40→39→40，期间 scope 失效/恢复 | canonical 供给里程碑只加一次 10 分，之后不冲正、不重复 |
| T66B | SCORE_GRADUATION尝试修改供给阈值40或分值10 | 发布拒绝；已有/未有里程碑均不补差、不二次发奖 |
| T67 | teacher DELETE | 历史事实和资格保留，无级联删除 |
| T68 | `dmo→dom` 存量迁移 | 关联、Peak、checkpoint、HMAC token 不丢不重 |
| T69 | OVS grading | 只持久化，不误套 DOM 分类，不与兼容路径双写 |
| T70 | 同一课程并发重算 | 参与、收藏、任务、积分唯一约束无重复 |
| T70A | A→B→A 同批到达后才投影 | 版本历史保留三个转换，脏键合并不吞掉 B，稳定得到 seq=1/2/3 |
| T70B | DOM/OVS 同 appoint ID 且同一教师两课均 perfect，贯穿兼容/cutover/rollback | v2、兼容表和旧 Worker 均两行隔离；perfect_cnt=2、完美分=8，回滚不覆盖/漏课 |
| T70C | 无 V2 课程基线时首个 appoint 事件是 A→B UPDATE | 账本记 `SOURCE_CHANGE_WITHOUT_CURRENT_IGNORED` 并推进 checkpoint；不建 current/version、课程、参与或 dirty key；后续 INSERT 建基线后重放该 UPDATE 仍为 duplicate/ignored |
| T70D | A→B 以新 offset 紧邻重复；以及 before/after 均与当前不符 | 前者 SEMANTIC_REPLAY 不新增参与/Outbox；后者 SOURCE_CONFLICT；A→B→A→B 仍保留第二次 B 参与 |
| T71 | 主键原地变化 | 整批失败、checkpoint 不前进、无 ACK |
| T72 | 退役 QA 与 user_complaint 全 CRUD | 账本/checkpoint 正确，无独立业务输出 |
| T73 | scope 快照与 CDC fence 并发 | COMPLETE 写 SOURCE_SCOPE 并唤醒 false/0；STALE 降未知并冲正；教师 scope 优先级固定 |
| T73A | scope COMPLETE→STALE→新快照 COMPLETE 且课程组件恢复 | 先冲正，恢复后 generation+1 新奖励；历史资格不撤销 |
| T73B | 第二个 snapshot 中同一 key 不变/B→A/缺行/再恢复 | 不变不增参与；变化写 SNAPSHOT_DIFF 且时间未知；GLOBAL 缺行 tombstone、TEACHER 缺行只移 membership；恢复用新 seq |
| T73C | candidate 在装载/校验/发布失败，旧 active 为非空或已确认空集 | 旧 active/current 不变；失败 candidate/staging 独立留痕且不提供完整性；新 ID 重试，成功后才原子切换 |
| T73D | snapshot fence 后同 key CDC 已被普通 Ingestor 应用，candidate 又重放；锁内 diff 与释放后更早业务时间 CDC 交错 | candidate replay只改staging；已达成状态不再发diff；剩余diff和后续CDC各取递增source_row_revision，版本/参与/dirty/Outbox各一次 |
| T73E | TEACHER COMPLETE空集后CDC增删及teacher A→B，分别发生在active、LOADING candidate和事务崩溃 | source current、membership overlay、dirty/checkpoint同事务；A absent、B present、GLOBAL正确；candidate只由replay追平；失败全回滚 |
| T73F | 多region/topic/partition首次H0整向量成功、响应丢失、漏route、identity未验证或已有半行 | 单次受限函数原子创建全部seq1 ACTIVE epoch/current checkpoint/唯一control H0；同run/vector重放no-op；不完整状态失败关闭 |
| T73G | 同stream generation reset起点等于/高于/低于bootstrap floor，BARRIER_PENDING期间持续到达CDC | 前两者可耐久落账并ACK但scope仍STALE；低于floor拒绝；完整manifest激活后才支配旧代，不重复应用 |
| T73H | 旧generation checkpoint=1000，新验证generation从offset=0开始 | 新epoch floor=0且先BARRIER_PENDING；不跨代比较offset，manifest完整后按predecessor lineage支配旧epoch |
| T73I | activation manifest漏routed CURRENT、必需HISTORY或空route的ROUTE_EMPTY | count/hash失败且保持BARRIER_PENDING；补齐精确requirement和COMPLETE snapshot后才原子ACTIVE |
| T73J | snapshot fence start_next=N、end_next=M，边界delivery为N、M-1、M | 只重放[N,M)；checkpoint next>=M即覆盖，M由普通增量处理一次，无漏重 |
| T73K | 同表GLOBAL/TEACHER并发candidate，owner超时/heartbeat/abort/takeover及发布响应丢失 | table head串行；有效token续租、旧token失效；takeover先使旧candidate FAILED；generation只+1，同结果重放no-op |
| T73L | legacy current与snapshot相同/不同，需要bootstrap或bootstrap+normal diff | 相同只写r1/step1/奇数offset；不同再写r2/step2/偶数offset；四元唯一和generation hash稳定 |
| T74 | 完课 KEEP 后相同/新差异 | 相同 fingerprint 不重开；新差异只更新同一课程幂等 Case 并等待新决定 |
| T74A | 读取完课 Case rev1 后更晚 DTS 生成 rev2，再提交 rev1 决定 | 返回 STALE_CASE_REVISION，且不写 decision、角色、快照、观察或积分 |
| T75 | VOID 后再次收到 end | 普通 DTS 不自动重新冻结或重结，必须有新的受限 TRANSFER 决定 |
| T76 | 收藏观察 false 与历史覆盖不足 | 分别得到 CONFIRMED_FALSE/WAITING_HISTORY；两者都不加分但证据状态不可混同 |
| T76A | 已获收藏分后 HISTORY STALE 再恢复同一候选 | STALE转AWARDED_PENDING_EVIDENCE并保留原+5/generation/流水；恢复true原地回AWARDED，不新增或冲正流水 |
| T77 | 收藏 observation / Outbox Worker 分别中途崩溃 | 前者租约到期重领；后者事务回滚释放行锁后以 PENDING 重领；都无半写/重复 |
| T77A | dirty/source conflict/favorite/TASK_PLAN/普通v2投影分别第8次失败，DEAD受控恢复后再失败，IN_REVIEW后恢复 | 失败状态与唯一技术Case同事务；恢复不提前关Case；原工作成功才RESOLVED，IN_REVIEW只加证据；新generation/revision才新Case |
| T77B | dirty领取后领域写前/后崩溃、租约过期/heartbeat竞态；PROCESSING到达更大revision；WAITING依赖重放/新证据；DEAD普通重放/新输入/人工恢复 | 领域写+complete同事务；旧token/version失效；reaper精确计失败；required>claimed回PENDING；旧依赖/普通重放no-op，只有新证据或受限恢复重开，完成才恢复Case |
| T78 | 新教师固定任务初始化 | 恰好 9 条 ASSIGNED、重放幂等、基线不足失败关闭，且没有通知或投递意图 |
| T78A | 五类个性化任务多 match 同时首次命中，交换 Worker/地区/课程顺序并重放/cutover | 同一 assignment 锁定后选同一 canonical seed，TASK_PLAN hash 相同，既有任务不重选 |
| T78B | TASK_PLAN提交后Planner在建任务前/提交前崩溃，多revision乱序领取，首次成功后重放/cutover | 旧revision superseded；当前revision原子建assignment/link/发布；重领同一seed/hash，无半写 |
| T78C | TASK_PLAN延迟3天，期间教师换时区/Worker重启；cutover补缺重复运行 | due始终按冻结eligible_since+48/72h与原时区；cutover用evaluation_as_of；重放不改时间/seed |
| T78D | template/copy并发发布，PUBLISHED原地改payload，发布扫描时新增match/Planner/固定任务初始化 | 完整catalog锁集无死锁；发布唯一且不可原地改；待物化plan重算；旧assignment继续读RETIRED，新任务只用新PUBLISHED |
| T78E | 先由canonical较大match建任务，后到/恢复更小match，再将MATCHED转MATERIALIZED重算 | 只关联后到match，不改seed/template/due/hash；active-set hash不含输出状态时间而保持稳定 |
| T78F | task_code引用错模板、MATCH due/timezone缺失/等式错误、非法状态边/权限/旧row_version | 复合FK、deferred trigger、状态/权限/CAS分别拒绝；引用RETIRED历史模板的既有任务仍可合法推进 |
| T78G | 单模板/copy发布、copy缺失/冲突及直接改模板状态/复活RETIRED | 模板只fan-out同task，copy fan-out全部未物化plan；既有assignment不改；无fallback，非法发布写拒绝 |
| T78H | LEGACY_COMPAT首次创建与template/copy发布并发、响应丢失同参重放、同幂等key异参、既有assignment/cutover并发 | catalog锁同序无死锁；确定ID/seed/payload；同参REPLAYED、异参拒绝；既有行只关联；窗口关闭不再新建 |
| T78I | TASK_PLAN state字段重排、空集合、blocker和copy版本变化 | 等价输入hash一致；真实blocker/copy换版触发新plan revision；非法额外字段拒绝 |
| T78J | MATCH任务物化前后分别出现并恢复negative blocker，另测LEGACY_COMPAT/FROZEN关联 | 未物化恢复开新generation；已有任务复用原generation/basis且不改seed、模板、文案或截止时间 |
| T78K | copy把标签GENERAL/title A改为PHOTO/title B，并产生或解除同label多课程variant冲突 | 未物化match先重评再每key发一个plan revision；blocker正确；既有任务不改 |
| T78L | 首次发布/换版改错任一G分值或把任一P改为非0 | 发布整笔拒绝；逐task_code保持固定分值，既有assignment和积分不变 |
| T79 | 新事实表 ACL/隐私/不可变约束 | 原始 DOM 学员 ID、越权写、版本改删、普通角色改冻结字段均被数据库拒绝 |
| T79A | 直写双 current、双 COMPLETION、课程不回指或 score 指向 NORMAL | 非法事务全部失败；合法 TRANSFER 三表最终一致 |
| T80 | v2 Outbox 到生产 Worker | aggregate 身份完整、payload 无敏感值、失败可重试，生产写入所有权唯一 |
| T80A | shadow 中断和同 fence 重跑 | 独立 cursor/result 可恢复，生产 Outbox、任务、积分和资格不变，count/hash 稳定 |
| T80B | cutover 事务成功与中途失败 | 成功合并旧任务/Case/提醒/流水/资格，ID、状态、历史不重不丢并原子切读；失败全部回滚，旧 v1 仍完整生效 |
| T80C | cutover 时收藏观察为未来到期/业务时间缺失/HISTORY 不足/两类证据完整 | 分别物化 PENDING/WAITING_EVIDENCE/WAITING_HISTORY/确定观察归因；revision、generation、旧流水均保留 |
| T80D | cutover 时 v1 三项首次日期早于/晚于/缺失于 v2 候选，HISTORY 完整/不完整 | LEAST 合并；旧早值 LEGACY_FROZEN；双空按完整性区分 CONFIRMED_EMPTY/SOURCE_MISSING |
| T80E | cutover 供给里程碑为 canonical/唯一 legacy/当前不足但已获奖/账户无流水/多候选/alias 回滚 | 可唯一证明者保留同一 10 分；唯一 legacy 只建 alias、不改流水；歧义停线，回滚不留 alias |
| T80F | v2 运行后产生各类任务/输出/观察/可逆积分，再 rollback 并重新 cutover | 任务进度和不可逆事实保留；可逆奖唯一冲正；按原 ID/新 generation 复用，无孤儿或重复 |
| T80G | 预检 shadow 后发生纠错、任务进度、观察到期或规则发布 | 共同 mutex 阻塞写入；revision vector 变化则 CUTOVER_INPUT_CHANGED，锁内重跑最终 shadow |
| T80H | cutover 内有待投影完课决定，其 event 全覆盖/部分未覆盖/含 DEAD_LETTER | 仅全覆盖可同事务settle并推进decision；部分/未知失败关闭；任一DEAD使cutover全回滚且decision/read route不变 |
| T80I | 最终 shadow 跨收藏到期点、北京午夜/00:05且 revision 不变 | shadow/物化共用 evaluation_as_of/业务日，hash 不漂移；冻结时点后新增时间事件进入耐久补跑并追平 |
| T80J | expand-contract持续收到DOM/OVS同号课程，故意缺v2表/函数/ACL/控制行，dual-capture在事务/ACK前后崩溃并跨handoff重启 | schema/control自检失败关闭；复合身份全程正确；单group/checkpoint、提交后ACK；mode/H原子接管无gap/双ACK/重复revision |
| T80K | COURSE closure漏/多下游结果、refs重排、伪造相等revision SUPERSEDED、错误hash；投诉分类只在L1/L2/L3引用 | 独立closure重算识别全部引用；重排hash同；合法显式空可过，其余CUTOVER_OUTBOX_COVERAGE_INCOMPLETE且不切读 |
| T80L | ROLLED_BACK后新end教师/时间变化，再次cutover | 回滚期只写conflict/Outbox/CASE_PLAN，不建第二生产Case；再次cutover按canonical source_ref复用并原子回填pointer |
| T80Q | DUAL_CAPTURE/ROLLED_BACK期间提交四类完课决定，随后进入V2_PRIMARY按最新Case revision重试 | 前两种mode统一维护拒绝且不改参与/积分/decision；V2_PRIMARY只接受未过期revision并走唯一v2投影owner |
| T80R | cutover时v1有逐课结果但v2已VOID/无current completion | 冲正可逆奖励后删除当前逐课结果；保留历史参与、settlement和流水，不生成伪零分completion |
| T80S | SCORE_GRADUATION换版与课程/固定任务/完课纠错积分并发 | 发布与三类writer按同一catalog锁串行；锁后CAS版本，旧规则不能覆盖新账户或资格 |
| T80T | cutover时OPEN/STORED输出在v2失效、自动取消输出在v2恢复，并混入已处理/READ行 | 只自动取消未处理行、只恢复系统自动取消原行；已处理/已读保留且不重复source_ref |
| T80M | final run捕获到期favorite DEAD的完整结果/明确空/缺结果，并混入dirty/source conflict/Outbox DEAD | 完整favorite可TAKEOVER，缺结果阻断；其他DEAD始终阻断，不能由shadow替代恢复 |
| T80N | cutover TASK_PLAN覆盖EXISTING_EVENT/CUTOVER_PLANNED/ALREADY_APPLIED，另测base0、revision漂移、异payload/关联及晚到base event | 三合法分支复用/原子计划/精确no-op；首次base0→1；异输入全回滚，晚到base只留superseded审计 |
| T80O | 两类合法legacy Outbox及未知状态；注入额外key/错类型/超深超大、credential/连接串/私钥/student token、hash漂移、preview后并发变化和响应丢失 | checker空数组+typed proof才可原子archive+audit+delete；精确错误阻断；锁内重算防TOCTOU；同run/hash重放no-op且不双存 |
| T80P | 三pipeline mode固定任务完成与SourceWide全量/增量并发结分，随后rollback/re-cutover | 仅Fixed Task Score Settler写FIXED_TASK_AWARD；共享锁确保一条canonical流水；SourceWide不补造，回滚不冲正、重切不重复 |

T37 同时验收已冻结聚合：所有状态都形成普通参与并计 `total_booked_cnt`；只有首次 end 冻结参与计
完课和课程积分，Peak 预约按参与且 `is_peak=true` 计数。

## 17. 实现前纠错清单（历史审查基线）

本节保留 2026-08-21 实现前审查时发现的 K01–K18，便于追溯“为什么这样改”，不再表示代码 head
仍存在这些差距。代码 head 已通过 rev66–101 和对应运行时实现收敛这些规则；人工验收应按前文
T01–T80 契约和当前测试执行。本次发布还需在部署阶段读回数据库迁移、真实 source profile、
联合 H0、DOM/OVS checkpoint 和 V2 业务抽样；不要求 source-scope 快照、双写追平或 V1/V2 对账。

### K01：未知被写成 false

课程创建时，现行 direct 把迟到、早退、收藏、拉黑、摄像头、CPU、网络等初始化为 false。

风险：

- 没有收到异常事件可能被解释成明确无异常；
- 完课可能被错误计为完美完课；
- 三项硬件异常都为 false 时，下游可能错误发放硬件质量分。

目标已冻结：集合或来源 scope 未证明完整时写 NULL；只有完整权威集合能够证明不存在时才写 false。
late/early 任一为 NULL 时 `is_perfect=NULL`、不加完美分并标 `SOURCE_MISSING`。

### K02：教师地区或窗口变化不重算已有课程

教师 UPDATE 会更新 `teach_area_type` 和入职窗口，但保留已有课程和聚合。

必须验证：

- 国内目标值 `dom` → `ovs`；
- `ovs` → 国内目标值 `dom`；
- 迁移期旧值 `dmo` → 目标值 `dom`，并确认相关课程、Peak 和聚合不发生丢失或重复；
- `status_on_time` 前移；
- `status_on_time` 后移。

课程事实必须保留；地区变化触发重算。来源地区与教师地区不一致时标 `SOURCE_CONFLICT` 并暂停该课
新结算，不能删除课程。入职窗口变化只重算 NEW/EXISTING 和明确的 30 天观察指标。

### K03：首 checkpoint 并发

远端 `436127d` 把 advisory lock、地区标签和 checkpoint 查询合并为一条 PostgreSQL SQL。

在 checkpoint 尚不存在时，两个消费者可能先取得旧语句快照，再排队等待 advisory lock；后一个消费者获得锁后仍可能看不到前一个事务刚插入的 checkpoint。

人工或集成验证需要覆盖：

- 干净重置后的第一批；
- 两个 Pod 短暂重叠；
- 排课等非幂等增量是否重复 `+1`。

### K04：ingest processed 不等于实际写宽表

direct 的 `ingest.processed` 主要表示表级路由命中。事件可能因为缺教师、缺课程、缺字典而在 projector 内 ignored。

实际判断必须同时看：

- `heartbeat.ingest`；
- `heartbeat.projection.ignored`；
- `heartbeat.projection.batch_prefiltered`；
- checkpoint；
- 宽表前后值；
- Outbox。

### K05：现行缺主记录或字典不会恢复

direct 不保存通用当前态和脏键，所以：

- 课程子事件先到、appoint 后到，不会恢复；
- 证书/排课先到、教师后到，不会恢复；
- 投诉先到、字典后到，不会恢复；
- 收藏/拉黑先到、完课后到，不会恢复。

该行为已明确不被业务接受。目标 v2 必须写通用来源当前态/tombstone 和脏键；依赖补齐后自动重算，
最终结果与到达顺序无关。T08、T19、T58 是强制验收用例。

### K06：SDK 接受 checkpoint 不等于 broker 已提交

`SDK_CHECKPOINTS_ACCEPTED` 只能证明代码调用了 SDK commit。最终验证需要联合：

1. 数据库 checkpoint；
2. 新事件持续消费；
3. 重启后从数据库 checkpoint 恢复；
4. 可用时读取消费组或 DTS 侧真实位点。

### K07：代课业务粒度与现有单行课程模型冲突

现行 `lesson_source_wide` 只能用 `课程id=appoint.id` 保存一条课程、一个教师，不能同时保存“A 缺席+B 接课”。

在模型修复前，以下现象都应记录为已知业务缺口，不得判为验收通过：

- `t_id` 改变后原教师课程记录消失；
- 原教师没有 `t_absent`；
- 缺席原因丢失或转移到代课教师；
- 教师聚合只做了贡献转移，没有同时表示缺席与接课；
- 完课教师没有首次 `end` 冻结事实。

### K08：课程级子事件不应跟着当前教师行转移

评价、投诉、QA 等事件以 `appoint_id` 属于源课程，无需等待课程 `end`。现行单行模型把这些字段和当前 `老师id` 放在同一行，appoint 改教师时会保留全部子字段并改变行的教师归属。

目标实现必须保证：

- 课程级事实只存一份，不因代课复制或丢失；
- 带 `t_id` 的教师级事实进入对应教师参与记录；
- 最终积分使用首次 `end` 时冻结的完课教师；
- 收藏、拉黑按教师—学生关系处理，不与普通 `appoint_id` 子事件混为同一口径。

### K09：`free` 课程和 grading.use_point 分支尚未实现

现行课程范围判断要求 `appoint.use_point='buy'`，会丢失 `free` 课程及其后续评价。同时 `user_teacher_grading` 字段白名单不包含 `use_point`，现行 projector 无法区分应读 `score` 还是 `type`。

修复必须同时覆盖：

1. direct 课程范围；
2. queued 课程范围；
3. DTS Java/Python 字段白名单；
4. direct grading 分支；
5. queued 重投影 grading 分支；
6. buy/free 相互矛盾字段的回归测试。

### K10：评价标签只保留名称，缺少稳定 ID 事实

现行 direct 把课程标签降级成逗号拼接的名称集合，丢失 `label_id`。这会导致同名异 ID、改名、删除一条但仍有其他活跃 log 等情况无法正确处理。

目标模型需要可追溯的 `appoint_id + label_id + label_name` 关联事实；展示用的逗号名称可以从该事实派生，不能反过来作为唯一事实源。

### K11：国内教师区域旧值 `dmo` 待统一为 `dom`

已确版目标是国内标识全部使用 `dom`。现行 direct/queued 投影仍写入 `teach_area_type='dmo'`，课程地区匹配和部分 Peak 测试也依赖该旧值。

后续代码批次必须作为一个可回滚整体处理：

1. direct `_teacher_area` 国内返回值改为 `dom`；
2. queued `_teacher_area` 国内返回值改为 `dom`；
3. 课程来源地区与 `teach_area_type` 直接按 `dom/ovs` 匹配；
4. 修改 Peak 时段判定及所有 `dmo` 测试 fixture；
5. 使用显式数据迁移将存量 `teacher_source_wide.teach_area_type='dmo'` 更新为 `dom`；
6. 核对 `teachers.payload`、导入/导出、API 和下游读取方是否保留旧值；
7. 迁移前后分地区对账教师数、课程数、Peak 数和积分，发生差异即停止。

迁移完成标准不是“代码中搜不到 `dmo`”，而是数据库存量值、新事件写入、课程匹配、Peak 和下游对账全部通过。

### K12：`center_type_desc` 默认 HBT 尚未实现

已确版映射是 1→CBT、5→TBT、其他任意值（包括 `NULL`）→HBT。现行 direct/queued 使用有限字典 `{0:HBT,1:CBT,5:TBT,6:HBT}`，未列出值和 `NULL` 会写成 `NULL`。

后续修复应改为显式分支：

```text
center_type == 1 → CBT
center_type == 5 → TBT
else             → HBT
```

不能只继续扩充枚举字典，否则未来新值仍会回到 `NULL`。

### K13：课程准入仍错误过滤 `status='cancel'/'on'`

现行 direct/queued 的 `_appoint_in_scope` 都要求
`status NOT IN ('cancel','on')`。因此无论是否发生代课，只要当前状态是 `cancel` 或 `on`，已有课程行
就会被删除、缺失课程行就会 ignored；`status='on'` 且教师变化时，还会同时丢失旧教师缺席和新教师参与事实。

已确版目标是从课程宽表准入中**完整删除所有 `status` 条件**，不是只删除 `on`，也不是改成另一组
允许状态枚举。修复时必须把“源课程/教师参与事实”与“完课、计分、预约等派生指标范围”拆开，
再保证 `t_id` 差值始终建立代课历史。聚合口径现已冻结：所有未删除 NORMAL/COMPLETION 教师参与，
无论源 status 为 `cancel/on/end/其他/NULL`，均计入 `total_booked_cnt`；Peak booked 是其中
`is_peak=true` 的参与数；PENDING/REJECTED/SUPERSEDED/VOIDED 纠错角色不计。不得再在实现时另选状态集合。

### K14：缺席原因仍混用三个来源

现行代码在 appoint 创建时读取 `cancel_reason`，缺席事件到达后又优先读取 `reason_desc`、再回退
`reason_type`。已确版目标只允许 `dom_teacher_absent_reason.reason_type`，并使用
`appoint_id + t_id` 归属到缺席教师参与记录。代码、字段白名单、隐私规则和回归测试必须一起收敛，
不能只改 7.2 的展示文案。

### K15：审查时假早退退役及 CPU、网络来源撤销尚未实现（现已落地）

该审查项已由 rev73/rev77 及对应投影、契约、API 和回归测试完成：目标契约不再包含假早退业务
字段或触发；CPU、网络字段暂时保留，但 `*_qa_ac_classroom_record` 不再是来源，替换来源接入前固定
为 `NULL`。部署时仍须读回迁移版本并校验存量值已清空。

### K16：收藏/拉黑仍被错误绑定到最近完课

现行 direct/queued 只有找到事件时间以前最近完成课程才处理收藏或拉黑；没有完成课程时直接
ignored。目标需要独立师生关系当前态和关系时间线，教师关系人数不依赖课程。收藏加分还需要
`end_time+24h` 的定时判定、唯一课程归因和幂等锁；这些都不能由当前单行课程宽表承担。

### K17：TESOL、标签和投诉过滤条件仍是旧规则

现行代码仍使用 `certification_type='tesol'`，标签仍过滤 `type=1/status=normal`。目标分别是证书
code 16 + status 1、标签不按 type/status 过滤。投诉的 Python 判断目前因 `None != 82` 实际会接受
NULL，但文档和 SQL 口径必须显式写成 `IS NULL OR != 82`，并增加回归测试锁定。字段白名单、
queued/direct、存量重算和测试需要按各自差异一起修改。

### K18：教师状态维度和出营分数快照尚未建模

现有数据库已有不可回退的出营/金牌资格和两个获得时间，但没有独立的在线状态，也没有
`IN_CAMP/GRADUATED` 两值在营状态及 `graduation_score_locked`。业务已取消 `NOT_IN_CAMP`。后续迁移应复用
`teacher_qualifications.*_qualified_at` 作为唯一时间事实，不复制时间列。在线状态映射已确认为
`on+入职 30 天内→NEW`、`on+入职满 30 天→EXISTING`、`off→LEFT`、`hei→BLOCKED`；其中
`NEW→EXISTING` 必须有日更或定时重算，不能只等 DTS 事件。出营分数永久记录为 100，
`raw_total_score` 作为实际分不封顶继续累计，教师端 `public_total_score` 最高显示 200。
`LEFT/BLOCKED` 不是计分停止条件，现行代码和存量重算都不得用该状态过滤积分事实。

## 18. 单条事件验证记录模板

```markdown
### Txx - <表名> <操作>

#### 1. 输入

- source_region：
- topic 安全别名：
- partition：
- source_partition_epoch_id / epoch_sequence / status：
- stream_generation_id / epoch_opening_id 安全hash：
- offset：
- source_timestamp：
- record_id_type / canonical record_id：
- operation：
- table_name：
- before：
- after：
- start_next_offset / end_next_offset：
- 提交后 source_row_revision（replay/no-op为空）：

#### 2. 前置数据库状态

- current epoch-aware checkpoint / lineage status：
- dts_ingest_events / dts_ingest_issues / dts_source_row_versions / dts_source_rows / dts_dirty_keys：
- source_courses / source_course_participations：
- teacher_source_wide / 关系当前态与时间线：
- lesson_source_wide 兼容投影：
- 相关字典/主记录：
- Outbox 最新水位：

#### 3. 根据代码推导的现行预期

- 路由结果：processed / ignored / duplicate / failed
- 来源当前态/脏键变化：
- 源课程/参与/关系变化：
- 教师聚合/兼容投影变化：
- 教师聚合变化：
- checkpoint 预期：
- Outbox 预期：
- ACK 预期：

#### 4. 实际结果

- heartbeat：
- checkpoint：
- 来源当前态/脏键：
- 源课程/参与/关系：
- 教师宽表/兼容课程投影：
- Outbox：
- 重启/重放结果：

#### 5. 判定

- 代码实际行为是否与源码一致：是 / 否
- 业务是否认可该行为：是 / 否 / 待确认
- 是否需要修代码：
- 是否需要修文档或业务口径：
- 证据 SQL / 截图位置：
```

## 19. 人工验收完成条件

只有同时满足以下条件，才能把结论写成“DTS 事件到 direct 宽表消费已验证”：

1. 国内、海外各自至少覆盖一条真实主事件；
2. 每张实际启用的源表至少验证 INSERT/UPDATE/DELETE 中适用的操作；
3. processed、ignored、duplicate、failed 四条路径都有数据库证据；
4. checkpoint 与宽表修改的事务一致性有失败回滚证据；
5. 国内学生 ID 只以 HMAC token 出现在海外库；
6. 子事件乱序、缺主记录或缺字典时，现行丢弃行为已有证据，但不能将其当成目标行为接受；对缺席原因、收藏时间线等已要求与到达顺序无关的事实，必须验证持久化、重算或可恢复重试机制；
7. K01、K02、K03 已修复，或由明确责任人书面接受剩余风险；K07–K18 属于已确版业务冲突，不能仅以风险接受代替修复；
8. 两轮稳定观察中 checkpoint 连续前进，没有 gap、重复累加或异常回退；
9. 宽表抽样字段与源事件逐字段对账；
10. T00–T80 及全部后缀用例的代课、乱序恢复、集合选择器、完课冻结与显式纠错、free 课程、DOM buy/free 评价、标签 ID、`dom` 统一、center_type、全状态/全日期准入、缺席任务生命周期、QA 退役、关系时间线、24 小时收藏归因和冲正、投诉 NULL、排课当前态、教师状态、资格、scope fence、Worker 恢复和数据库权限均符合冻结规格；
11. 缺席任务映射按已确版精确值通过幂等验证：`Unfilled Lesson Memo`→`P-REL-MEMO`，其他非空 `reason_type`→`P-REL-ATTENDANCE`，空值不创建缺席任务；任务事实不得被表述为提醒、通知或外部动作已送达；
12. 在这些条件完成前，SourceWide Worker 和不可逆资格门禁保持关闭。
