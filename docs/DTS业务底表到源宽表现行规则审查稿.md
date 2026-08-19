# DTS 业务底表到源宽表现行规则审查稿（历史 queued 路径）

> 本文只保留为旧 `queued` 方案的对照材料。2026-08-19 新增的逐事件 direct 规则以
> [`DTS事件直接投影规则.md`](DTS事件直接投影规则.md) 为准；不要把本文的事件账本、
> 源镜像、脏键和教师全量重算描述用于 direct 发布。

> 文档状态：业务审查稿
> 核对日期：2026-08-18
> 核对对象：当前分支代码、23/55 字段映射 v1.5、数据库模型与投影测试
> 目的：让业务方逐项确认“哪些教师、哪些课程进入宽表，以及每个字段如何计算”。本文描述的是**当前代码实际行为**；与映射约定不一致处单独列出，不把待修正逻辑写成已完成。

## 1. 先确认这条链路在做什么

这条链路不是把一张业务表原样复制成一张宽表，而是把国内、海外多个业务底表的最新事实，按教师或课程重新组合成两张目标表：

- `lesson_source_wide`：一节有效课程一行，共 23 个字段；
- `teacher_source_wide`：一位目标人群教师一行，共 55 个字段；
- 教师宽表的大部分计数、比例由课程宽表和排课、证书等当前态再次聚合得到。

当前实际处理顺序：

```text
国内/海外业务库变更
→ DTS 订阅消息
→ 解析 INSERT / UPDATE / DELETE
→ 表和字段白名单
→ 国内学生 ID 做不可逆 HMAC 伪名化
→ 事件账本 dts_ingest_events
→ 底表当前态镜像 dts_source_rows
→ 受影响对象队列 dts_dirty_keys
→ 按课程/教师完整重算
→ lesson_source_wide / teacher_source_wide
→ 宽表变化 Outbox（下游积分、任务等；不属于本文核对范围）
```

三个关键原则：

1. DTS 消息只负责告诉系统“什么事实变了”，不能靠一条局部消息直接拼出完整宽表。
2. 投影器每次读取已经落库的底表当前态，重新计算受影响的整节课或整位教师，避免局部字段覆盖成半成品。
3. 依赖尚未到达时应重试；只有明确删除、明确不在目标范围时才删除目标宽表行。

## 2. 人群、地区与时间范围

### 2.1 当前代码中的教师 cohort

- 教师权威主记录固定取国内共享表 `dom_teacher`；该表同时覆盖国内和海外教师。
- 目标教师按 `dom_teacher.status_on_time::date` 判断入职日。
- 当前代码默认 `TIT_DTS_COHORT_START=2026-08-13`，结束日期默认不设上限。
- 如果运行环境设置 `TIT_DTS_COHORT_END_EXCLUSIVE`，则只纳入：

```text
cohort_start <= 入职日 < cohort_end_exclusive
```

- 订阅起点 `TIT_DTS_START_AT` 必须不晚于 cohort 起始日 00:00（北京时间），否则启动失败。

注意：此前讨论的“改成 2026-08-19 起算”目前只是拟议方案，不是当前代码默认值。运行环境究竟配置为 8 月 13 日还是 8 月 19 日，必须以国内、海外两个 DTS 应用的实际变量读回为准。

### 2.2 教师地区归属

按 `dom_teacher.course` 判断：

- 包含 `global_cn` 或 `global_pool`：海外教师，`teach_area_type='ovs'`；
- 不包含上述两个 token：国内教师，`teach_area_type='dmo'`；
- `course` 缺失：地区未知，不允许课程通过地区匹配。

课程来源地区还必须与教师地区一致：

- `ovs_appoint` 只能匹配海外教师；
- `dom_appoint` 只能匹配国内教师。

### 2.3 30 天观察窗口

教师入职日记作 `D`：

```text
onboard_date = D
onboard_30d_end_date = D + 29 天
```

教师的课程、排课和聚合指标只统计闭区间 `[D, D+29]`，含首尾两天。

### 2.4 课程进入宽表的四道门

一节课只有同时满足以下条件才进入 `lesson_source_wide`：

1. `use_point = 'buy'`；
2. `status NOT IN ('cancel', 'on')`；
3. 学员标识非空；
4. 教师在 cohort 内、课程地区与教师地区一致、课程日期处于教师入职后 30 天内。

不满足范围的现有课程宽表行会被删除。若预约或教师记录只是尚未到达，当前逻辑进入重试，不能把“没到”当成删除。

## 3. 参与计算的业务底表

| 来源 | 表 | 主要用途 |
|---|---|---|
| 国内共享 | `dom_teacher` | 教师身份、状态、入职日、组织和地区归属 |
| 国内共享 | `dom_teacher_certification` | TESOL 状态 |
| 国内共享 | `dom_teacher_class_schedule` | 开放档期、Peak 档期、产能 |
| 国内共享 | `dom_teacher_absent_reason` | 缺席原因、No Notification、课中缺席 |
| 国内共享 | `dom_teacher_penalty` | 迟到、早退 |
| 国内共享 | `dom_complaint_cate` | 投诉一至三级分类名称字典 |
| 国内/海外 | `*_appoint` | 课程主事实、教师、学员、状态、起止时间 |
| 国内/海外 | `*_user_teacher_grading` | 评分、好评、差评 |
| 国内/海外 | `*_grading_label_log` | 评价标签明细 |
| 国内/海外 | `*_grading_label` | 标签字典及标签变化触发 |
| 国内/海外 | `*_user_complaint` | 用户侧投诉分类 |
| 国内/海外 | `*_complaint` | 投诉审批、有效性和分类 |
| 国内/海外 | `*_teacher_favorite` | 收藏及其课程归因 |
| 国内/海外 | `*_teacher_blacklist` | 拉黑及其课程归因 |
| 国内/海外 | `*_qa_task_close_camera_record` | 未开摄像头 |
| 国内/海外 | `*_qa_ac_classroom_record` | CPU、网络异常 |
| 国内/海外 | `*_qa_task_fake_early_leave_record` | 假早退 |

`*` 代表 `dom` 或 `ovs`。除白名单字段外，密码、手机号、证书 URL、无关自由文本等不会进入目标库当前态镜像。

## 4. 课程宽表 23 字段规则

| # | 目标字段 | 当前代码实际规则 | 空值与异常语义 |
|---:|---|---|---|
| 1 | `课程id` | `*_appoint.id` 转字符串；国内、海外同时出现同一 ID 时失败关闭 | 必填 |
| 2 | `上课日期` | 优先 `appoint.date`，缺失时退回 `appoint.start_time::date` | 两者都无法解析则 `NULL`；当前未校验二者日期冲突 |
| 3 | `上课时间` | `appoint.start_time::time`，统一按北京时间解析带时区值 | 无法解析为 `NULL` |
| 4 | `是否高峰` | 国内：工作日 18:00–21:30；周末再含 09:00–11:30。海外：工作日 18:00–23:30 或 00:00–05:30；周末再含 09:00–11:30 | 周次或时间缺失为 `NULL`；明确未命中为 `false` |
| 5 | `老师id` | `appoint.t_id`；必须能关联到 `dom_teacher` | 缺失报错并重试 |
| 6 | `学员id` | 海外保留源学员标识；国内在离开国内应用前转为 `dom:v1:<HMAC-SHA256>` | 国内原始 ID 禁止进入海外库、日志和公开输出 |
| 7 | `课程状态` | 保留 `appoint.status` 原值；整行先经过课程范围四道门 | 来源空为 `NULL` |
| 8 | `缺席原因明细` | 同课程、同教师的缺席记录按 `add_time`、`id` 取最新；依次取 `reason_desc > reason_type > appoint.cancel_reason` | 都无可信值为 `NULL`；国内自由文本会被白名单化或脱敏 |
| 9 | `迟到` | 处罚记录按课程和教师筛选，取最大 `in_time`；`in_time-start_time>30秒` 为 `true` | 当前代码无有效处罚时写 `false`；与“当前态不完整应为 NULL”的约定不一致 |
| 10 | `早退` | 取最大有效 `out_time`；`end_time-out_time>30秒` 为 `true`；1970 哨兵时间无效 | 当前代码无有效处罚时写 `false`；与不完整当前态语义存在差异 |
| 11 | `差评分` | 取最新有效评价，但当前代码只在 score 为 1 或 2 时保存，否则写 `NULL` | 与映射表“保留最新原始 score”文字不完全一致 |
| 12 | `差评标签` | 最新有效评价 score 为 1/2，或 `type='unsatisfactory'` 时为 `true` | 无评价为 `NULL`；有评价未命中为 `false` |
| 13 | `投诉一级分类` | 见下方投诉统一规则，最后用 `dom_complaint_cate` 映射中文名 | 无有效投诉为 `NULL`；字典缺项时整课重试 |
| 14 | `投诉二级分类` | 同上 | 同上 |
| 15 | `投诉三级分类` | 同上 | 同上 |
| 16 | `是否拉黑` | 同一地区、同教师、同学员的有效拉黑记录，归给 `abs(add_time-course.end_time)` 最小的有效课程 | 当前无关系记录或无候选课时直接写 `false`；未实现约定的“不完整则 NULL/异常” |
| 17 | `收藏` | 同一地区、同教师、同学员的收藏记录，按最近 `end_time` 归给一节有效课程 | 当前无关系记录或无候选课时直接写 `false`；未实现不完整语义 |
| 18 | `好评标签` | 最新有效评价 score 为 4/5，或 `type='satisfactory'` 时为 `true` | 无评价为 `NULL`；有评价未命中为 `false` |
| 19 | `评价详情` | `grading_label_log` 中 `type=1 AND status='normal'`；按 `label_id` 去重、排序，拼接 `label_name` | 无标签为 `NULL`；当前只读日志内名称，标签字典改名不会真正替换展示名 |
| 20 | `未开摄像头` | 存在对应 `qa_task_close_camera_record` 即 `true` | 当前查不到记录直接 `false`；未区分“完整无记录”和“历史未同步” |
| 21 | `cpu占用过高` | `qa_ac_classroom_record.type='CPU'` 且 `info.cpu[*].appoint_id` 命中课程 | JSON 非法/结构错误为 `NULL`；结构正常但未命中为 `false` |
| 22 | `网络延迟过高` | `type='NETWORK_DELAY'` 且 `info.network_delay[*].appoint_id` 命中课程 | JSON 非法/结构错误为 `NULL`；结构正常但未命中为 `false` |
| 23 | `假早退` | 存在对应 `qa_task_fake_early_leave_record` 即 `true` | 当前查不到记录直接 `false`；未区分源表是否完整 |

### 4.1 最新有效评价

评价候选必须满足：

```text
is_del ∈ {NULL, 0}
AND status ∈ {NULL, 0}
```

候选按 `update_time`、`create_time`、`id` 降序取最新一条。

### 4.2 投诉统一规则

1. `user_complaint` 按“学员 + appoint_id”取最大 `id`；
2. `complaint` 也按“学员 + appoint_id”取最大 `id`；
3. 两边做 FULL OUTER JOIN 语义合并；分类字段优先用户侧，`approve/validity` 取 `complaint`；
4. 只有同时满足以下条件才是宽表中的有效投诉：

```text
complaint_type = 13
AND complaint_type_grandson != 82
AND lower(approve) = 'y'
AND validity = 1
```

5. 如果一门课有多条有效合并结果，再按 `course_date`、`id` 取最新；
6. 分类 ID `-1`、`0` 视为该层级不存在；其他分类 ID 必须能在 `dom_complaint_cate` 中找到，否则不写半条课程记录，保留重试。

### 4.3 收藏/拉黑并列规则

最近课程距离相同时，依次：

1. 优先关系发生时间之前已经结束的课程；
2. 再取 `end_time` 更晚的课程；
3. 再取 `appoint_id` 更小的课程。

拉黑还会过滤当前认定无效的记录：永久有效、没有有效结束时间，或结束年份不早于 2999 才被视为有效。该规则本身也需要业务确认，因为它没有使用“当前时间是否落在有效期内”的一般区间判断。

## 5. 教师宽表 55 字段规则

### 5.1 身份、状态与窗口（1–17）

| # | 字段 | 当前代码实际规则 |
|---:|---|---|
| 1 | `tchr_id` | `dom_teacher.id` 转字符串，主键 |
| 2 | `real_name` | `dom_teacher.real_name` |
| 3 | `center_type_id` | `dom_teacher.center_type` 转字符串 |
| 4 | `center_type_desc` | 0/6→HBT，1→CBT，5→TBT，其他→`NULL` |
| 5 | `bu` | 5/6/7/11/19/20/21/22/503→HBT；8/10/201→OBT；其他→`NULL` |
| 6 | `status` | 保留 `dom_teacher.status` 原值 |
| 7 | `status_on_date` | `status_on_time::date` |
| 8 | `status_off_date` | `status_off_time::date` |
| 9 | `last_on_date` | `last_on_time::date` |
| 10 | `job_days` | `(status_off_date 或数据库 current_date) - status_on_date` |
| 11 | `job_month` | `job_days>=0` 时 `floor(job_days/30)+1`，否则 `NULL` |
| 12 | `teach_area_type` | `course` 包含 `global_cn/global_pool`→`ovs`，否则→`dmo` |
| 13 | `onboard_date` | `status_on_time::date` |
| 14 | `onboard_30d_end_date` | `onboard_date+29天` |
| 15 | `first_open_slot_dt` | 30 天内 `status='on'` 排课的最早日期 |
| 16 | `first_booked_dt` | 30 天内进入课程宽表课程的最早日期 |
| 17 | `first_completed_dt` | 30 天内 `课程状态='end'` 的最早日期 |

当前代码对未知 `center_type`、`is_full_time`、地区值只写 `NULL`，没有实现映射表约定的告警队列。

### 5.2 计数（18–40）

| # | 字段 | 当前代码实际规则 |
|---:|---|---|
| 18 | `total_booked_cnt` | 30 天内课程宽表行数；课程 ID 本身唯一 |
| 19 | `peak_booked_cnt` | 上述课程中 `是否高峰=true` 数 |
| 20 | `total_completed_cnt` | `课程状态='end'` 数 |
| 21 | `peak_completed_cnt` | 完课且 `是否高峰=true` 数 |
| 22 | `absent_cnt` | 当前只统计 `课程状态='t_absent'`；尚未把“存在缺席证据”并入口径 |
| 23 | `late_cnt` | 完课且 `迟到=true`，或缺席记录 `add_time>=课程开始时间` 的去重课程数 |
| 24 | `early_cnt` | 完课且 `早退=true` 的去重课程数 |
| 25 | `anomaly_cnt` | 缺席、迟到、早退三类课程 ID 的并集数 |
| 26 | `perfect_cnt` | 当前代码：完课且 `迟到 is not true`、`早退 is not true`、非课中缺席；这会把 `NULL` 当作“没有异常” |
| 27 | `no_notice_cnt` | 30 天课程中任一缺席记录 `reason_type='No Notification'` 的去重课程数；不是只看最新缺席记录 |
| 28 | `first_completed_student_cnt` | 30 天内完课课程的去重学员数 |
| 29 | `feedback_total_eval_cnt` | 30 天课程中存在最新有效评价的去重课程数 |
| 30 | `feedback_praise_cnt` | 30 天课程中 `好评标签=true` 数 |
| 31 | `feedback_negative_cnt` | 30 天课程中 `差评标签=true` 数 |
| 32 | `feedback_complaint_cnt` | `user_complaint` 或 `complaint` 任一表出现记录的去重课程数，不要求最终有效 |
| 33 | `feedback_valid_complaint_cnt` | 通过第 4.2 节完整有效投诉规则的去重课程数 |
| 34 | `feedback_favorite_cnt` | 课程宽表中 `收藏=true` 的去重学员数 |
| 35 | `feedback_block_cnt` | 课程宽表中 `是否拉黑=true` 的去重学员数 |
| 36 | `total_slot_cnt` | 30 天内 `status='on'` 排课，按 `date_slot`，缺失时按 `id` 去重 |
| 37 | `reg_slot_cnt` | 上述排课中 `project_code='1v1'` 的去重 slot 数 |
| 38 | `peak_slot_cnt` | 上述排课中命中教师地区 Peak 规则的去重 slot 数 |
| 39 | `slot_days` | 有开放排课的去重日期数 |
| 40 | `peak_slot_days` | 有 Peak 开放排课的去重日期数 |

### 5.3 比率与状态（41–55）

所有比率分母为 0 时统一写 `NULL`，不写 0。

| # | 字段 | 公式/规则 |
|---:|---|---|
| 41 | `reliability_absent_rate` | `absent_cnt / total_booked_cnt` |
| 42 | `reliability_late_rate` | `late_cnt / total_completed_cnt` |
| 43 | `reliability_early_leave_rate` | `early_cnt / total_completed_cnt` |
| 44 | `reliability_late_early_rate` | `(late_cnt+early_cnt) / total_completed_cnt` |
| 45 | `feedback_praise_rate` | `feedback_praise_cnt / feedback_total_eval_cnt` |
| 46 | `feedback_negative_rate` | `feedback_negative_cnt / feedback_total_eval_cnt` |
| 47 | `feedback_complaint_rate` | `feedback_complaint_cnt / total_completed_cnt` |
| 48 | `feedback_favorite_rate` | `feedback_favorite_cnt / first_completed_student_cnt` |
| 49 | `feedback_block_rate` | `feedback_block_cnt / first_completed_student_cnt` |
| 50 | `feedback_eval_rate` | `feedback_total_eval_cnt / total_completed_cnt` |
| 51 | `capacity_avg_completed_per_day` | `total_completed_cnt / 30` |
| 52 | `capacity_peak_slot_rate` | `peak_slot_cnt / total_slot_cnt` |
| 53 | `capacity_key_slot_day_rate` | `peak_slot_days / slot_days` |
| 54 | `is_cpl_tesol` | 存在 `certification_type='tesol' AND certification_status=1`→`true`；见过 TESOL 但无有效证书或已删除→`false`；增量期从未见过→`NULL` |
| 55 | `is_self_introduce` | 当前无可信来源，固定 `NULL` |

## 6. UPDATE、DELETE 与乱序到达

### 6.1 UPDATE

DTS 可能只发送部分列。当前态落库时会把：

```text
已存当前态 + before image + after image
```

合并成新当前态，再重新计算旧归属和新归属。教师、课程或学员归属改变时，两边都会重新置脏，避免旧教师或旧师生组合残留统计。

### 6.2 DELETE

- 明确收到预约 tombstone：删除课程宽表行并重新计算原教师；
- 明确收到教师 tombstone，或教师被改出 cohort：先删除该教师课程宽表，再删除教师宽表；
- 只有“当前镜像里查不到”但没有 tombstone：不推断为删除，等待依赖或保持不变。

### 6.3 重试与隔离

- 默认最多尝试 8 次；
- 退避约为 10、20、40、80、160、300、300 秒；
- 达到上限后保留 `RETRY` 和错误码，以 PostgreSQL `infinity` 停放，不阻塞其他键；
- 后续真实业务变更再次命中该键时，会恢复为 `PENDING` 并从 0 重新尝试。

## 7. “基线数据经过相同规则生成”具体是什么意思

如果决定从 2026-08-19 重新开始，基线不能简单理解为“把两张宽表从源库导出后直接导入”。真正的同规则基线应满足：

1. 基线取数覆盖本文第 3 节所有必要底表，不只是 `teacher` 和 `appoint`；
2. 使用与实时链路相同的表/字段白名单；
3. 国内学生 ID 使用同一个 HMAC 域和同一把受控密钥生成稳定 `dom:v1` token；
4. 先形成某一截止时点的完整底表当前态，再运行同一个课程/教师投影器；
5. 投诉字典、标签、关系、质检、处罚、证书和排课的“没有记录”只有在对应底表基线完整时才能解释成 `false/0`；
6. 基线截止点与 DTS 增量起点必须无缝衔接：不能漏一段，也不能用不可幂等方式重复一段；
7. 基线和实时重算相同样本时，两张宽表必须逐字段一致。

如果因为权限只能直接导入两张宽表，那么导出 SQL 必须完整复刻本文规则，并做逐字段对账；这种方案事实上维护了第二套计算实现，长期风险高于“底表基线进入同一投影器”。

## 8. 当前代码与已确认映射的差异

下面不是建议优化，而是会改变业务结果的差异，建议在重新清数和重启订阅前处理。

| 优先级 | 差异 | 可能影响 |
|---|---|---|
| P0 | 当前镜像只包含订阅后见过的变更；收藏、拉黑、摄像头、假早退等在“没查到”时直接写 `false` | 把历史未同步误判为明确没有，教师反馈和课堂质量被低估/高估 |
| P0 | `perfect_cnt` 使用 `迟到/早退 is not true`，所以 `NULL` 也会被当成无异常 | 数据缺失课程可能被算作完美课并影响可靠性积分 |
| P0 | `absent_cnt` 只统计 `status='t_absent'`，没有实现“缺席证据或状态”的合并口径 | 缺席数、异常数、缺席率和资格门槛可能偏低 |
| P1 | `差评分` 只保留 1/2；映射表文字写的是保留最新有效原始 score | 如果业务期待看到 3/4/5，当前数据会丢失 |
| P1 | `appoint.date` 与 `start_time::date` 不一致时没有报警，代码直接优先 date | 日期异常会静默进入 30 天窗口和 Peak 计算 |
| P1 | 处罚的 `appeal_status=NULL` 当前被排除，而“仅排除 2”的文字意味着 NULL 应保留 | 迟到、早退可能漏算 |
| P1 | 标签字典变更虽然会触发课程重算，但展示仍取日志内旧 `label_name` | 标签改名后宽表可能仍是旧名称 |
| P1 | 收藏/拉黑无候选课程时返回 `false`，没有实现 `NULL + 异常` | 关系事实可能无声丢失 |
| P1 | 拉黑有效性只识别永久、无结束时间或 2999 年哨兵，不按当前时间判断一般有效区间 | 有期限拉黑可能被忽略 |
| P2 | 未知 Center、BU、地区值只写 `NULL`，没有业务告警 | 新代码值漂移不易被及时发现 |
| P2 | `no_notice_cnt` 只要历史任一缺席记录是 No Notification 就计数，不限定最新记录 | 修正后的缺席原因仍可能保留旧计数 |

## 9. 请业务方逐项确认

请直接在本节勾选或批注；未确认项不建议作为正式清数重跑的业务口径。

- [ ] cohort 起始日最终是 2026-08-13，还是 2026-08-19？是否保持开放结束边界？
- [ ] 所有教师（国内和海外）的权威身份、入职日都以 `dom_teacher` 为准。
- [ ] `course` 含 `global_cn/global_pool` 即海外，否则国内；不存在第三类或冲突组合。
- [ ] 观察窗口固定为入职日起含首尾共 30 天。
- [ ] 有效预约固定为 `buy` 且状态不为 `cancel/on`、学员非空。
- [ ] 国内/海外 Peak 时间段与第 4 节一致，边界时刻包含在内。
- [ ] `差评分` 是只保留 1/2，还是保留最新评价的任意 score？
- [ ] 处罚记录只排除 `appeal_status=2`；`NULL` 是否应参与计算？
- [ ] `absent_cnt` 是否应为 `t_absent` 或存在有效缺席证据的课程并集？
- [ ] `perfect_cnt` 要求迟到、早退均明确为 `false`；任一 `NULL` 都不能算完美课。
- [ ] No Notification 按最新缺席原因，还是历史任一记录命中即计数？
- [ ] 投诉有效条件与第 4.2 节完全一致。
- [ ] 收藏/拉黑最近课程归因及并列规则完全一致。
- [ ] 一般有起止日期的拉黑记录如何判断有效？
- [ ] 当前态不完整时，未发现收藏/拉黑/质检/处罚记录必须为 `NULL`，不能为 `false/0`。
- [ ] `first_completed_student_cnt` 表示“窗口内有完课的去重学员数”，不要求判断该学员与该教师的全历史首次完课。
- [ ] `capacity_avg_completed_per_day` 固定除以 30，而不是除以截至当天已观察天数。
- [ ] TESOL 三值语义：明确完成 `true`、明确未完成 `false`、增量期从未触达 `NULL`。
- [ ] `is_self_introduce` 暂无可信来源，继续固定 `NULL`。
- [ ] 基线必须覆盖必要底表并走同一投影规则，不把“直接导入两张宽表”当作默认方案。

## 10. 本文边界

本文只覆盖：业务底表 → DTS 当前态 → 两张源宽表。

本文不确认：

- 宽表之后的逐课积分、教师总分、任务触发和资格逻辑；
- 当前国内/海外应用实际部署变量；
- 某个数据库当前是否已经装载完整基线；
- 实际生产字段对账是否通过。

业务规则确认后，应把第 8 节差异转成回归测试和代码修复，再用同一批源表快照同时跑“基线”和“实时投影”进行逐字段一致性验收。
