# TIT 课程级数据与 Mock 字段契约

日期：2026-08-21
状态：v1 兼容字段契约；v2 结构与事件语义以 `docs/DTS_direct开发冻结实施规格.md` 为准

## 1. 当前用途

课程明细用于个性化任务触发、运营提醒、逐课计分归因和证据下钻。教师宽表仍提供明确的教师聚合字段；但 `perfect_cnt` 只用于对账，当前完美完课数由课程状态、迟到和早退派生。

历史测试库已导入 37,317 节课程基线；现行 v1 数据入口为：

- `lesson_source_wide`：现行一课一师兼容表；rev73 已物理删除假早退列，当前契约为 22 列，但它不再是 v2 权威写模型；
- `outbox_events`：只记录真实变化字段和受影响教师，不复制整行；
- `lesson_score_results`：保存一课一行的当前派生分数与证据状态；
- `complaint_category_rules`：保存投诉三级分类的精确匹配规则；
- `personalized_trigger_matches`：保存每次规则命中、证据和最终输出。

v2 权威写模型是 `source_courses + source_course_participations`，课程级积分按源课程一行、教师端课程按参与行展示。详细物理字段以 [开发冻结实施规格](../docs/DTS_direct开发冻结实施规格.md) 和 [数据库表结构](../docs/数据库表结构.md) 为准，触发口径以 [数据与积分规则](../docs/数据与积分规则.md) 为准。

## 2. 第一批课程字段

以下 22 列只定义现行 v1 兼容字段如何映射，不再定义 v2 主键或数据粒度。v2 中源课程使用
`(source_region,source_appoint_id)`，教师参与使用
`(source_region,source_appoint_id,participation_seq)`；不得把一课多师压回一行，也不得拼接伪造源课程 ID。

本节的国内学生隐私边界覆盖此前字段映射工作簿中“学员id 原值”的旧说明；旧工作簿不能作为把
国内原始学生 ID 写入海外库的授权依据。

| 源字段 | 类型化字段 / 用途 | 说明 |
|---|---|---|
| 课程id | `source_appoint_id` | 源课程稳定业务键；仅与 `source_region` 组成源课程唯一键，不能单独标识教师参与 |
| 上课日期 | `lesson_local_date` | 来源本地日期，不猜时区 |
| 上课时间 | `lesson_local_time` | 来源本地时间，不转成 UTC |
| 是否高峰 | `is_peak` | 0/1/空；保存课程级高峰事实，不直接替代教师维度 `peak_slot_cnt` |
| 老师id | `teacher_id` | 在教师参与行中必填；同一源课程可以出现多位教师，乱序时先持久化来源事实并待重算 |
| 学员id | `student_id` | 海外课程可保存受限海外源 ID；国内课程在国内容器内先转为 `dom:v1:<HMAC-SHA256>`，海外表只保存该稳定伪名 token，禁止保存原始国内学生 ID |
| 课程状态 | `lesson_lifecycle_status` | 原值保留，不自行改写枚举含义 |
| 迟到 | `is_late` | 0/1/空；触发可靠性任务 |
| 早退 | `is_early` | 0/1/空；触发可靠性任务 |
| 差评分 | `negative_score` | 数值/空；保留事实，本轮不单独触发 |
| 差评标签 | `has_negative_feedback_tag` | 1 表示差评，0 表示不是差评；只有值为 1 时才解析评价详情 |
| 缺席原因明细 | `absence_reason_detail` | 唯一来源为同一课程、同一教师最新 `dom_teacher_absent_reason.reason_type`；无记录为 `NULL`，不读取 `reason_desc` 或 `appoint.cancel_reason` |
| 投诉一级分类 | `complaint_category_l1` | 原样保存 |
| 投诉二级分类 | `complaint_category_l2` | 出席/网络设备投诉用于改路由 |
| 投诉三级分类 | `complaint_category_l3` | 与处罚规则三级分类精确匹配 |
| 是否拉黑 | `is_blocked` | 课程侧派生展示字段，不是拉黑关系权威；师生拉黑关系及教师去重人数不要求已有完成课程 |
| 收藏 | `is_favorited` | 表示该课程是否获得 end+24h 收藏归因；同一教师与学员终身只允许一节课为 1 并加分，取消后再收藏不开启新获分周期，关系当前态另行持续维护 |
| 好评标签 | `has_positive_feedback_tag` | 当前只保存是否存在 |
| 评价详情 | `feedback_detail` / 标签展示 | 标签身份必须来自规范化 `label_id` 关联，`label_name` 只用于展示；不得再用逗号字符串作为唯一标签事实 |
| 未开摄像头 | `is_camera_off` | 0/1/空；触发课中质量提醒 |
| cpu占用过高 | `is_cpu_usage_high` | 字段保留、来源待替换；新来源接入前必须为 `NULL`，不得由旧 `qa_ac_classroom_record` 赋值 |
| 网络延迟过高 | `is_network_delay_high` | 字段保留、来源待替换；新来源接入前必须为 `NULL`，不得由旧 `qa_ac_classroom_record` 赋值 |

`0` 表示来源明确给出 0；空值表示来源未提供。两者不能混为一谈。

## 3. 投诉分级字段

处罚规则表使用：一级分类、二级分类、三级分类、P级、学习标题、学习链接。

- 只用非空三级分类做精确匹配，不做模糊猜测；
- 来源 P0–P4 原样保存在 `source_level`；
- 同时保存 `severity_rank=0–4` 供路由；
- P0/P1 进入运营严重投诉 Case；P2/P3/P4 进入教师学习任务；
- 二级分类为出席问题或网络设备问题时，分别改走可靠性或课堂质量；
- 未匹配时进入 `PENDING_DATA`，不自动发布任务。

## 4. 隐私与证据

- 教师端不得读取原始学员 ID、投诉原文、IP、MAC 或内部处罚证据；
- 国内 DTS 原始学生 ID 只允许短暂存在于国内容器内存，必须在构造任何海外 PostgreSQL SQL
  参数前删除原始别名并使用国内项目独占密钥生成 `dom:v1:` token；海外项目、海外数据库、日志和
  错误 payload 均不得持有该密钥或原始国内学生 ID；
- `dom:v1:` 是用于不同学员数、收藏/拉黑归因和课程关联的稳定伪名 token，不是匿名数据，仍须
  受限访问。如果合规要求禁止稳定 token 跨境，则本契约需要改为国内聚合、海外只接收无法回链
  个体的指标，不能改用可逆加密或把密钥部署到海外；
- 任务外显只包含任务名称、触发原因和完成动作所需的最小信息；
- 运营端可以下钻到课程 ID 和结构化异常，但不返回 `source_records.raw_payload`；
- 源课程 ID、当前单课结果、规则命中和输出 ID 必须可互相追溯；投诉规则文件本身通过
  `data_import_batches + source_records` 追溯。

## 5. 每日外部接口目标

正式运行由受限接入服务先写来源当前态/tombstone，再幂等重建
`source_courses + source_course_participations`。`lesson_source_wide` 只允许在切换期作为只读兼容投影，
不得与 v2 双向写形成两个事实源。

每个接口接入前必须确认：

- 稳定业务键和字段字典；
- 鉴权方式与受限凭据；
- 分页、排序和单次完整性证明；
- 数据水位、发生时间、更新时间和时区；
- 迟到数据、修正、删除、补数和回看窗；
- 重试、断点续拉、重复/漏数校验和告警。

只有完整拉取、校验和标准化全部成功，监控服务才能提交对应源表事务。失败或部分变更不能
推进监控服务自己的水位。

本系统不再要求上游额外提供逐课 `perfect` 字段。教师端读取视图
`teacher_lesson_score_current.is_perfect`，其值由以下三项课程事实派生：

```text
lesson_lifecycle_status = 'end'
且 is_late = 0
且 is_early = 0
```

`is_perfect` 随依赖课程事实更新而变化，不写入任何源表。正式日更接口仍需保证
这三个来源字段的 0、1、空值和修正语义稳定。逐课 `is_perfect = true` 时可靠性加 4 分；
教师维度 `perfect_cnt` 按去重 `(source_region,source_appoint_id)` 复合课程键汇总。课堂质量硬件项直接读取
`is_camera_off / is_cpu_usage_high / is_network_delay_high`；三项均明确为 0 时
逐课加 2 分，任一字段为 1 时不加分，任一字段为空时不加分并标记
`SOURCE_MISSING`。由于 CPU、网络来源已取消且替换来源尚未接入，过渡期这两个字段必须为
`NULL`，因此不得产生新的硬件质量加分。

## 6. Mock 边界

当前真实课程字段可支持出席、投诉、拉黑、重复差评标签和未开摄像头触发。CPU、网络的新来源、
完整课堂环境、设备检测、连续在场和独立 Lesson Memo 提交事实仍缺来源；需要展示时只能使用明确
标记的 Mock，不能用于正式触发、扣分或资格结论。假早退业务已取消，不得再用真实或 Mock 数据恢复。
