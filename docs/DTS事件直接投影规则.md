# DTS 事件直接投影规则（现行 v1 差异说明）

> 状态：现行代码说明，不是目标开发契约。目标 v2 以 `DTS_direct开发冻结实施规格.md` 为唯一输入。
>
> 现行 v1 前提：国内、海外 DTS 在同一个干净边界重置；两张宽表先按发布方案清理或导入基线；边界之前的事实不要求由增量事件恢复。该前提已被目标 v2 废止，不能用于清空已有事实后依赖七天增量重建。
>
> 现行 v1 采用“DTS 当前有什么就消费什么”，会永久丢失乱序子事实；目标 v2 必须先持久化来源当前态/tombstone 和脏键，缺依赖时等待重算。DTS 七天窗口不能替代权威基线。

## 1. 现行 v1 链路

```text
DTS INSERT / UPDATE / DELETE
→ DTS 解析
→ 字段白名单与国内学生 ID HMAC
→ UPDATE 稀疏 before/after 合并为完整白名单镜像
→ 教师/课程主事件当前镜像满足范围时创建或更新，否则 ignored
→ 锁定消息对应的课程行和教师行
→ 计算课程旧行对教师指标的贡献
→ 根据 before 撤销旧课程或旧归属影响
→ 根据 after 写入新课程或新归属影响
→ 更新 lesson_source_wide
→ teacher_source_wide += 新贡献 - 旧贡献
→ 更新 dts_ingest_checkpoints
→ 事务提交后 ACK
```

现行直接模式不写业务事件账本、通用 `dts_source_rows` 业务镜像或 `dts_dirty_keys`。现有
`dts_source_rows` 只保留 `dom_complaint_cate` 小型参考字典和国内 HMAC 指纹契约；它们都不保存
教师、课程或学生业务事实。目标 v2 必须写入这三层耐久状态，并与 checkpoint 同事务；direct 与
queued 复用同一领域重算服务。

由于 direct 不再保存每节课的来源镜像，启动检查也不再用 `dts_source_rows` 反推课程
provenance；它只检查旧状态残留中的原始国内 ID，以及宽表中格式错误的 `dom:` token。基线
导入本身必须先完成国内学生 ID HMAC，不能指望启动检查从无地区字段的宽表里识别裸数字属于
国内还是海外。

`dts_ingest_checkpoints` 是 PostgreSQL 持久化位点表，按
`source_region + topic + partition_id` 保存下一条应处理的 `next_offset`。宽表写入和位点更新在
同一事务：真实写入失败时 checkpoint 不前进且记录不 ACK；目标不存在而被 ignored 时不写宽表，
但 checkpoint 正常前进并 ACK。重放先用 checkpoint 判重，因此不会重复累加 slot。

## 2. 总体语义

本节先记录现行 direct 实现；与业务新确版冲突的地方，在后续表格和段落中以
“目标”明确标出，不得把现行行为当作验收标准。

- 消费订阅在保留窗口内实际提供的所有白名单事件；事件是否“完整”不作为 ACK 前提。
- 教师、课程的 INSERT 和当前完整镜像满足 cohort/课程范围的 UPDATE 都可创建宽表主行；
  缺失主行的 DELETE、范围外 UPDATE 和子事件 ignored。
- 子记录采用“最后事件生效”：UPDATE 先撤销 `before` 的旧课程/旧归属，再应用
  `after`；DELETE 清空本条事件的影响，不恢复更早的历史记录。
- 教师课程类计数不扫描整位教师课程；每条消息只对旧课程贡献做减法、对新课程贡献做加法。
- 完课学员、收藏学员、拉黑学员三个去重数只对本消息涉及的“教师+学员”执行索引化
  `EXISTS` 前后比较；最早约课/完课日期只在日期、状态或归属变化时执行该教师的 `MIN`。
- 官方 Java 传输生产批次请求 2000 条（协议硬上限 2048 条，并继续受 8 MiB payload 上限约束）并放在一个 checkpoint 事务中；投影动作仍严格按
  offset 一条一条执行，不存在后置脏键队列或批末教师全量重算。
- 每批先用教师、课程两类分块查询读取该批涉及的目标 ID。缺失主行的 DELETE、范围外 UPDATE
  和子事件直接 ignored，避免逐事件空查；满足范围的主记录 UPDATE 与 INSERT 都进入投影，批内
  前序创建/删除会同步更新该索引，因此仍保持 offset 顺序语义。心跳中的 `batch_prefiltered` 和
  `batch_target_queries` 用于验证该优化是否命中。
- 排课使用本次确认的单向增量语义，不回看或回减历史；TESOL 按 before/after 最后事件生效。
- 关联事件先定位同一教师、同一学生、事件发生时间以前最近一节 `status='end'` 的课程。
- 子事件、排课、证书、收藏、拉黑或投诉无法命中已有教师/课程时 ignored，并推进 checkpoint。
- 字段缺失、非法主键变化、隐私违规、计数下溢和数据库错误仍然失败关闭，不能归入 ignored。

## 3. 教师主记录

来源：`dom_teacher`。

- INSERT，或 `after.status_on_time` 位于 cohort 的 UPDATE，可以创建教师宽表行；缺失教师的
  DELETE 及范围外 UPDATE ignored。
- `status_on_time` 必须处于 cohort；否则删除该教师及其课程宽表行。
- `onboard_date = status_on_time::date`。
- `onboard_30d_end_date = onboard_date + 29`，仅用于 `NEW→EXISTING` 和被明确定义为新师 30 天观察的指标；不作为通用课程事实准入门槛。
- `center_type_desc` 已确版目标：`center_type=1`→`CBT`、`center_type=5`→`TBT`，其他任意值（含 `NULL`）→`HBT`。当前 direct/queued 对未列出值和 `NULL` 仍写 `NULL`，属于待修复差异。
- 已确版目标：`course` 包含 `global_cn/global_pool` 时 `teach_area_type='ovs'`，否则 `teach_area_type='dom'`。
- 当前 direct/queued 代码仍在国内分支写入旧值 `dmo`；这是待代码与存量数据一起迁移的已知差异，不再是目标契约值。
- 身份、组织、状态字段由本次 `after` 覆盖；课程和 slot 聚合字段保留并由对应消息增量更新。
- 现行教师 DELETE 先删课程宽表，再删教师宽表；目标 v2 只写教师来源 tombstone，课程、参与、积分和资格历史不得级联删除。

## 4. 课程主记录

来源：`dom_appoint`、`ovs_appoint`。

INSERT，或当前 `after` 同时满足下列范围条件的 UPDATE，可以创建课程宽表行；缺失课程的
DELETE 及范围外 UPDATE ignored。

已确版的目标准入条件为：

1. 学员标识非空；
2. 教师宽表已存在；
3. 目标契约中来源地区与教师 `teach_area_type` 直接使用 `dom/ovs` 一致性匹配；当前代码对国内仍使用 `dom → dmo` 旧映射；

课程准入完全不判断 `appoint.use_point` 和 `appoint.status`。`buy/free`、`cancel/on`、其他状态及
`NULL` 状态都应保留课程事实并原样保存 `status`；入职 30 天后的课程事实也照常保留。完课、计分、预约等派生指标另按各自规则判断。
不满足上述结构和人群范围条件时才删除已有课程行。基础字段来自 appoint；已有评价、投诉、收藏、
拉黑和 QA 字段在 appoint 更新时保留。

当前 direct/queued 代码仍额外要求 `use_point='buy'`、
`status NOT IN ('cancel','on')` 且课程日期在入职 30 天内，与上述目标不符。这三项都必须从课程准入中完整删除，不能只放开
`on`。当 `before.t_id != after.t_id` 时，无论状态为何，都先保留/创建旧教师 `t_absent` 参与，再
新增新教师参与并保存 `after.status` 原值。

Peak：

- 国内工作日：18:00–21:30；周末另含 09:00–11:30。
- 海外工作日：18:00–23:30、00:00–05:30；周末另含 09:00–11:30。

## 5. 排课

来源：`dom_teacher_class_schedule`，一行代表一个 slot。

现行 v1 只在以下转换发生时累计一次：

```text
before.status != 'on' AND after.status = 'on'
```

INSERT 且 `after.status='on'` 同样计一次；`on→on`、`on→off` 和 DELETE 均不处理。目标 v2 已改为
“当前有效槽集合”：最新未删除且 status=on 才计数，off/DELETE 回减，slot 按来源行 ID 去重，
slot_days 按日期去重；只统计入职 0–29 天观察窗口。

一次有效开启执行：

- `total_slot_cnt += 1`；
- `slot_days += 1`；这里按已确认规则表示 slot 开启次数，不再表示自然日去重数；
- `project_code='1v1'` 时 `reg_slot_cnt += 1`；
- Peak 时 `peak_slot_cnt += 1`、`peak_slot_days += 1`；
- `first_open_slot_dt = min(旧值, date)`；
- 重算两个 capacity 比率。

国内 Peak slot：

- 工作日：37–44；
- 周末：19–24 或 37–44。

海外 Peak slot：

- 工作日：1–12 或 37–48；
- 周末另含 19–24。

源字段的真实开启值是 `on`，不能使用“status 非空”，因为 `off` 也非空。

## 6. 课程子事件

| 来源表 | 直接规则 |
|---|---|
| `dom_teacher_absent_reason` | 目标仅保存 `reason_type`，按 `appoint_id+t_id` 归属；多记录取最新，UPDATE/DELETE 后重算剩余记录 |
| `dom_teacher_penalty` | `appeal_status=2` 清零；迟到为 `in_time-start>30s`；早退按本版 30 分钟标准课长计算 |
| `dom_teacher_certification` | 目标以 `certification_code='16' AND certification_status=1` 判断 TESOL；按教师当前证书集合重算 |
| `dom_user_teacher_grading` | 当前集合取最新记录；buy 只按 score 1/2 差评、4/5 好评，free 只按 type；删除最新后恢复次新 |
| `ovs_user_teacher_grading` | 本轮只持久化，不套用 DOM 规则、不产生新 OVS 评价积分 |
| `*_grading_label_log` | 不判断 `type/status`；按 `appoint_id+label_id+label_name` 保存关联，UPDATE/DELETE 精确移除 before 关联 |
| `*_grading_label` | 名称变更时直接替换课程宽表中完全相同的旧标签名称；不保存标签字典状态 |
| `*_teacher_favorite` | 独立维护师生收藏关系和时间线；不要求已有完课，课程 end+24h 时做唯一课程加分归因 |
| `*_teacher_blacklist` | 独立维护师生当前有效拉黑关系；不要求已有完课，也不强制归因课程 |
| `*_complaint` | 只处理 `type=13 AND (grandson IS NULL OR grandson!=82) AND approve='y' AND validity=1`；其他事件清空 |
| `*_user_complaint` | v1 不单独投影，因为缺少 approve/validity；等待同一投诉的 authoritative complaint 事件 |
| `*_qa_task_close_camera_record` | INSERT/UPDATE=true，DELETE=false |

缺席原因触发教师任务的目标映射已确版：`reason_type='Unfilled Lesson Memo'`
幂等创建 `P-REL-MEMO`；其他任意非空 `reason_type`（包括 `No Notification`）幂等创建
`P-REL-ATTENDANCE`；空值不创建缺席任务。仅 `No Notification` 额外贡献 `no_notice_cnt`。

现行 v1 的评价、标签、处罚、投诉和摄像头只修改已有课程，找不到就永久 ignored。目标 v2 对所有
业务子表先写 `dts_source_rows` 和脏键；课程/教师/字典晚到后自动重建。收藏、拉黑还要独立维护
关系当前态和时间线；排课、证书同样不得因教师晚到而丢失。

同一师生的收藏课程积分只允许一节课获得：按课程首次 `end` 后第 24 小时的关系历史状态判断，
最早满足的课程获分，相同时间按课程 ID 决胜；持续收藏或取消后再收藏都不让后续课程重复获分。当前 direct/queued
仍使用“事件前最近完课”存储策略，不满足该目标。

课程主记录不得从 `appoint.cancel_reason` 初始化 `缺席原因明细`，缺席事件也不得读取
`reason_desc`；没有对应 `dom_teacher_absent_reason.reason_type` 时写 `NULL`。

业务已取消假早退，目标字段契约、白名单、投影、触发和输出都不再包含它。
`*_qa_ac_classroom_record` 也不再作为 CPU、网络来源；该表事件在目标消费者中 ignored 并推进
checkpoint。`cpu占用过高`、`网络延迟过高` 字段保留等待新来源，在新来源确版并接入前必须为
`NULL`，不能用旧表无匹配或事件未出现推断为 `false`。切换时还必须清空旧来源写入的存量值并重算
受影响课程，否则历史课堂质量分会继续生效。

投诉事件只有分类 ID，没有中文名称。`dom_complaint_cate` 事件直接维护系统库里现有
`dts_source_rows` 的小型参考字典；投诉事件按 ID 查询该字典后写入中文名。字典缺项时该投诉
现行事件 ignored 并推进 checkpoint，不写半条课程记录，也不会等待补齐。目标 v2 在字典缺失时保留
投诉源行并保持课程/分类脏键待重算；分类到达、改名或删除后按分类 ID 重建，不得永久保留旧名称或
丢失投诉。完整字典/权威基线是对应 scope 写 COMPLETE 的门禁。

## 7. 教师字段差值

课程写入前后分别生成一组 0/1 贡献，教师字段只应用差值：

以下是当前代码的增量聚合动作。目标模型将“源课程数”与“教师参与次数”分开：A→B 时前者是 1、
后者是 2；`total_booked_cnt` 固定为教师普通参与次数，所有源 status 都计；`peak_booked_cnt` 为其中
Peak 参与。下表其余内容只说明当前代码差距，完整目标见冻结实施规格第 7 节。

| 课程当前事实 | 教师字段动作 |
|---|---|
| 课程行新增/删除 | `total_booked_cnt ±1` |
| `是否高峰=true` | `peak_booked_cnt ±1` |
| `课程状态='end'` | `total_completed_cnt ±1`；同时按 Peak、迟到、早退更新对应字段 |
| `课程状态='t_absent'` | `absent_cnt ±1` |
| 缺席、迟到、早退任一成立 | `anomaly_cnt ±1` |
| 完课且迟到、早退均不为 true | `perfect_cnt ±1` |
| 有明确好评/差评 | 评价总数及好评/差评数应用差值 |
| 有有效投诉分类 | 投诉数与有效投诉数应用差值 |
| 完课/收藏/拉黑的师生关系首次出现或最后消失 | 现行三个去重学员数字段 `±1`；目标中收藏/拉黑人数改由独立关系当前态聚合，收藏分另读取 end+24h 锁定归因 |

应用计数后，仅使用教师当前标量重新计算比例字段。排课按第 5 节直接累加；TESOL 证书
`after` 有效时置 true，无效或 DELETE 时根据 `before` 置 false，不保存证书成员状态。

## 8. 开关与全新重跑边界

默认模式保持现行队列实现：

```env
TIT_DTS_PROJECTION_MODE=queued
```

### 8.1 发布前门禁

1. 系统库必须先从 public `20260818_62_dts_claim_idx` 迁移到
   `20260819_63_dts_direct_privacy`。rev63 让数据库隐私 Trigger 接受受限 DTS 角色提供的事务级
   DOM / OVS 标签；没有该迁移时，direct 的第一条有效课程写入会被数据库拒绝。
2. 在两个 Gaea DTS 应用的“环境变量”中配置 direct；不要修改普通 TIT 应用，也不要把国内 HMAC
   密钥放入 OVS 项目。
3. DOM、OVS 必须使用同一北京时间起点。消费组可以复用，但两个消费者必须先完全停止，并删除
   对应数据库 checkpoint；权威数据库没有 checkpoint 时，消费者会按 `TIT_DTS_START_AT` 重新
   定位，即使该消费组此前已经提交到更靠后的位点。新建消费组只用于额外隔离，不是重放前提。
4. 清理动作必须在两个旧 DTS 消费者和 SourceWide Worker 都停止后执行。由于 DTS 只保留七天，
   不要提前清库；应在镜像、变量和迁移都已准备好后进入维护窗口，清理后立即启动 DOM。

清理前先以数据库 owner / 管理员执行只读门禁：

```sql
SELECT pid, application_name, client_addr, state, xact_start
FROM pg_stat_activity
WHERE usename = 'tit_dts_ingest_runtime'
  AND pid <> pg_backend_pid();

SELECT
  (SELECT count(*) FROM public.teachers
   WHERE source_snapshot_label = 'SOURCE_WIDE_CURRENT') AS projected_teachers,
  (SELECT count(*) FROM public.lesson_score_results) AS lesson_results,
  (SELECT count(*) FROM public.personalized_trigger_matches) AS trigger_matches;
```

第一条查询必须为零行。第二条查询三个数字必须全为 `0`；只要有一个非零，就说明历史宽表已经产生
产品侧教师、分数、任务或资格事实，不能把它们混进普通 DTS 数据面清理，需要另开产品数据重置
维护窗口。尤其不可直接删除不可逆资格事实。

确认已有备份后，以 owner / 管理员在同一个事务中执行：

```sql
BEGIN;
SET LOCAL lock_timeout = '5s';

LOCK TABLE
  public.dts_ingest_checkpoints,
  public.dts_ingest_events,
  public.dts_source_rows,
  public.dts_dirty_keys,
  public.lesson_source_wide,
  public.teacher_source_wide,
  public.lesson_score_results,
  public.personalized_trigger_matches,
  public.outbox_events
IN ACCESS EXCLUSIVE MODE;

DO $reset_guard$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_stat_activity
    WHERE usename = 'tit_dts_ingest_runtime'
      AND pid <> pg_backend_pid()
  ) THEN
    RAISE EXCEPTION 'DTS runtime is still connected';
  END IF;
  IF EXISTS (SELECT 1 FROM public.teachers
             WHERE source_snapshot_label = 'SOURCE_WIDE_CURRENT')
     OR EXISTS (SELECT 1 FROM public.lesson_score_results)
     OR EXISTS (SELECT 1 FROM public.personalized_trigger_matches) THEN
    RAISE EXCEPTION 'downstream source-wide facts require a separate product reset';
  END IF;
END
$reset_guard$;

UPDATE public.outbox_events
SET status = 'CANCELLED',
    available_at = clock_timestamp(),
    published_at = clock_timestamp(),
    last_error = 'DTS_DIRECT_FULL_RESET_20260820'
WHERE event_type = 'source_wide.changed.v1'
  AND status <> 'PUBLISHED';

TRUNCATE TABLE
  public.lesson_score_results,
  public.personalized_trigger_matches,
  public.lesson_source_wide,
  public.teacher_source_wide,
  public.dts_ingest_events,
  public.dts_source_rows,
  public.dts_dirty_keys,
  public.dts_ingest_checkpoints;

COMMIT;
```

`outbox_events` 是不可删除审计事实，不能随宽表一起 `TRUNCATE`；旧的未发布
`source_wide.changed.v1` 只终止为 `CANCELLED`。清理不使用 `CASCADE`，防止误删未列明业务表。

### 8.2 两个 DTS 应用的变量

清理历史并重置两条 DTS 后，国内和海外都在各自 Gaea DTS 应用的“环境变量”中显式配置：

```env
TIT_DTS_PROJECTION_ENABLED=true
TIT_DTS_PROJECTION_MODE=direct
TIT_DTS_COHORT_START=2026-08-20
TIT_DTS_START_AT=2026-08-20T00:00:00+08:00
```

两个项目还必须分别保持 `TIT_DTS_SOURCE_REGION=dom/ovs`、
`TIT_DTS_EXECUTION_REGION=cn/sg`，并填入各自消费组的 `TIT_DTS_GROUP_ID`。DOM 项目必须继续注入
原有 `TIT_DTS_DOM_STUDENT_HMAC_PASSWORD`；OVS 项目不得配置这个密钥。若实际发布日晚于本例，应把
cohort 和两条 `START_AT` 一起前移到仍在七天保留窗口内的同一新边界，不能继续照抄
`2026-08-20`。

直接模式允许 DOM、OVS 各自投影本地区事件，不取得旧版全局脏键投影锁。为尽量避免海外课程先于
国内教师，发布顺序必须是：先启动 DOM，确认它已追到接近实时，再启动 OVS；仅看到 checkpoint
出现一行不等于 DOM 已追平。代码完成不等于已迁移、已清库、已重置 DTS 或已发布。

### 8.3 启动后验收

```sql
SELECT
  source_region,
  topic,
  partition_id,
  next_offset,
  to_timestamp(source_timestamp) AT TIME ZONE 'Asia/Shanghai'
    AS latest_source_event_time,
  clock_timestamp() - to_timestamp(source_timestamp) AS source_lag,
  updated_at
FROM public.dts_ingest_checkpoints
ORDER BY source_region, topic, partition_id;

SELECT
  (SELECT count(*) FROM public.teacher_source_wide) AS teachers,
  (SELECT count(*) FROM public.lesson_source_wide) AS lessons,
  (SELECT count(*) FROM public.dts_ingest_events) AS queued_event_receipts,
  (SELECT count(*) FROM public.dts_dirty_keys) AS queued_dirty_keys;

SELECT count(*) AS bad_dom_student_tokens
FROM public.lesson_source_wide
WHERE "学员id" LIKE 'dom:%'
  AND "学员id" !~ '^dom:v1:[0-9a-f]{64}$';
```

DOM 和 OVS 都必须出现且 `source_lag` 持续缩短。direct 下
`queued_event_receipts=0`、`queued_dirty_keys=0`、`bad_dom_student_tokens=0`；两张宽表数量允许小于
DTS 事件数量，因为本轮明确允许无法命中的事件 ignored。抽样对账通过后才恢复 SourceWide Worker；
不可逆资格开关在单独业务验收前继续关闭。
