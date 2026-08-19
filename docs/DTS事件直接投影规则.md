# DTS 事件直接投影规则（v1）

> 状态：代码已提供显式 `direct` 模式，默认仍为 `queued`，尚未发布或切换运行环境。
>
> 适用前提：国内、海外 DTS 在同一个干净边界重置；两张宽表先按发布方案清理或导入基线；边界之前的事实不要求由增量事件恢复。

## 1. 链路

```text
DTS INSERT / UPDATE / DELETE
→ DTS 解析
→ 字段白名单与国内学生 ID HMAC
→ UPDATE 稀疏 before/after 合并为完整白名单镜像
→ 锁定消息对应的课程行和教师行
→ 计算课程旧行对教师指标的贡献
→ 根据 before 撤销旧课程或旧归属影响
→ 根据 after 写入新课程或新归属影响
→ 更新 lesson_source_wide
→ teacher_source_wide += 新贡献 - 旧贡献
→ 更新 dts_ingest_checkpoints
→ 事务提交后 ACK
```

直接模式不写业务事件账本、`dts_source_rows` 业务镜像或 `dts_dirty_keys`。国内 HMAC 指纹仍使用原有技术契约行，它不包含学生标识，也不参与业务投影。

由于 direct 不再保存每节课的来源镜像，启动检查也不再用 `dts_source_rows` 反推课程
provenance；它只检查旧状态残留中的原始国内 ID，以及宽表中格式错误的 `dom:` token。基线
导入本身必须先完成国内学生 ID HMAC，不能指望启动检查从无地区字段的宽表里识别裸数字属于
国内还是海外。

任一宽表写入失败时 checkpoint 不前进，Kafka/DTS 记录不会 ACK；重放先用 checkpoint 判重，因此不会重复累加 slot。

## 2. 总体语义

- 主记录（教师、课程）采用当前事件覆盖当前宽表。
- 子记录采用“最后事件生效”：UPDATE 先撤销 `before` 的旧课程/旧归属，再应用
  `after`；DELETE 清空本条事件的影响，不恢复更早的历史记录。
- 教师课程类计数不扫描整位教师课程；每条消息只对旧课程贡献做减法、对新课程贡献做加法。
- 完课学员、收藏学员、拉黑学员三个去重数只对本消息涉及的“教师+学员”执行索引化
  `EXISTS` 前后比较；最早约课/完课日期只在日期、状态或归属变化时执行该教师的 `MIN`。
- 官方 Java 传输可以把多条消息放在一个 checkpoint 事务中，但投影动作仍严格按 offset
  一条一条执行，不存在后置脏键队列或批末教师全量重算。
- 排课使用本次确认的单向增量语义，不回看或回减历史；TESOL 按 before/after 最后事件生效。
- 关联事件先定位同一教师、同一学生、事件发生时间以前最近一节 `status='end'` 的课程。
- 子事件早于课程主事件时失败关闭并保留 checkpoint，不能把“课程尚未到达”静默当作空值。

## 3. 教师主记录

来源：`dom_teacher`。

- `status_on_time` 必须处于 cohort；否则删除该教师及其课程宽表行。
- `onboard_date = status_on_time::date`。
- `onboard_30d_end_date = onboard_date + 29`，课程和排课只接受闭区间 `[D,D+29]`。
- `course` 包含 `global_cn/global_pool` 时 `teach_area_type='ovs'`，否则为现行契约值 `dmo`。
- 身份、组织、状态字段由本次 `after` 覆盖；课程和 slot 聚合字段保留并由对应消息增量更新。
- 教师 DELETE 先删课程宽表，再删教师宽表。

## 4. 课程主记录

来源：`dom_appoint`、`ovs_appoint`。

进入课程宽表必须同时满足：

1. `use_point='buy'`；
2. `status NOT IN ('cancel','on')`；
3. 学员标识非空；
4. 教师宽表已存在；
5. 来源地区与教师 `teach_area_type` 一致；
6. 上课日期在教师 `[D,D+29]` 内。

不满足时删除已有课程行。基础字段来自 appoint；已有评价、投诉、收藏、拉黑和 QA 字段在 appoint 更新时保留。

Peak：

- 国内工作日：18:00–21:30；周末另含 09:00–11:30。
- 海外工作日：18:00–23:30、00:00–05:30；周末另含 09:00–11:30。

## 5. 排课

来源：`dom_teacher_class_schedule`，一行代表一个 slot。

只在以下转换发生时计数一次：

```text
before.status != 'on' AND after.status = 'on'
```

INSERT 且 `after.status='on'` 同样计一次；`on→on`、`on→off` 和 DELETE 均不处理。因此这里明确采用“重置边界后的首次开启次数”，不表示任意时点的源表当前开启量。

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
| `dom_teacher_absent_reason` | after 写入缺席原因；DELETE 清空 |
| `dom_teacher_penalty` | `appeal_status=2` 清零；迟到为 `in_time-start>30s`；早退按本版 30 分钟标准课长计算 |
| `*_user_teacher_grading` | 有效 after 写分数和好/差评标记；删除或失效清空；1/2 为差评、4/5 为好评 |
| `*_grading_label_log` | `type=1,status='normal'` 把标签加入课程当前名称集合；UPDATE/DELETE 用 before 移除旧名称，再用 after 加入新名称 |
| `*_grading_label` | 名称变更时直接替换课程宽表中完全相同的旧标签名称；不保存标签字典状态 |
| `*_teacher_favorite` | 归因到事件时间以前同师生最近一节已完成课程；DELETE 将该课程收藏置 false |
| `*_teacher_blacklist` | 永久有效记录按同样规则归因；DELETE/失效将该课程拉黑置 false |
| `*_complaint` | 只处理 `type=13,grandson!=82,approve='y',validity=1`；其他事件清空 |
| `*_user_complaint` | v1 不单独投影，因为缺少 approve/validity；等待同一投诉的 authoritative complaint 事件 |
| `*_qa_task_close_camera_record` | INSERT/UPDATE=true，DELETE=false |
| `*_qa_task_fake_early_leave_record` | INSERT/UPDATE=true，DELETE=false |
| `*_qa_ac_classroom_record` | 从 `info.cpu/network_delay[].appoint_id` 定位课程并按操作置 true/false |

投诉事件只有分类 ID，没有中文名称。直接模式不建字典状态表，因此启动前必须把经确认的
ID→中文名完整映射以非敏感配置 `TIT_DTS_COMPLAINT_CATEGORY_MAP_JSON` 注入；配置为空时
直接模式拒绝启动，遇到缺失映射或 `complaint_cate` 名称与配置不一致时停在当前 checkpoint，
不写错误分类。更新配置并重启后，同一事件会重放，并把宽表中的旧分类名称替换为新名称。

## 7. 教师字段差值

课程写入前后分别生成一组 0/1 贡献，教师字段只应用差值：

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
| 完课/收藏/拉黑的师生关系首次出现或最后消失 | 三个去重学员数字段 `±1` |

应用计数后，仅使用教师当前标量重新计算比例字段。排课按第 5 节直接累加；TESOL 证书
`after` 有效时置 true，无效或 DELETE 时根据 `before` 置 false，不保存证书成员状态。

## 8. 开关与切换边界

默认模式保持现行队列实现：

```env
TIT_DTS_PROJECTION_MODE=queued
```

清理历史并重置两条 DTS 后，国内和海外分别显式配置：

```env
TIT_DTS_PROJECTION_ENABLED=true
TIT_DTS_PROJECTION_MODE=direct
TIT_DTS_COHORT_START=2026-08-19
TIT_DTS_START_AT=2026-08-19T00:00:00+08:00
TIT_DTS_COMPLAINT_CATEGORY_MAP_JSON={...}
```

直接模式允许 DOM、OVS 各自投影本地区事件，不取得旧版全局脏键投影锁。为避免海外课程先于国内教师，发布顺序必须是：先启动 DOM 并确认 checkpoint 前进，再启动 OVS。代码完成不等于已迁移、已清库、已重置 DTS 或已发布。
