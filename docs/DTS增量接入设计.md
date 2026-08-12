# DTS 增量接入设计

## 当前结论

- 国内与海外同步业务库是一个逻辑数据集的两部分，不把国内共享表当作海外缺失数据。
- 国内和海外使用同一套消费、持久化与投影代码，但各自运行一个独立消费者进程；两条链路通过目标库中的当前态和脏键队列汇合，不能共用消费组、位点或 SASL 密码。
- 海外 DTS 开始时间为 `2026-08-10 14:16:00+08:00`；国内 DTS 开始时间为 `2026-08-12 16:30:00+08:00`（北京时间）。它们是新消费状态首次解析 offset 的回放边界，不要求等于服务真正启动时间；已有数据库 checkpoint 时始终从 checkpoint 续跑。
- 业务已确认不做教师/课程全量基线。目标人群是国内 `dom_teacher.status_on_time` 在北京时间 `2026-08-13`（含）以后入职的新教师，结束边界开放；两条订阅起点都早于人群起点。只有国内教师主记录已到达、命中地区与入职 30 天窗口的课程才可物化。
- 课程目标契约固定为 23 列，教师目标契约固定为 55 列；字段逻辑以当前映射表和两份 OBS 脚本为准。

## 已实现的持久化切片

`app.dts_source_consumer`、`app.dts_ingest_store` 与
`app.dts_wide_projector` 已实现以下代码边界：

1. 按阿里云官方 Python 示例使用 `kafka-python`、`fastavro` 和官方 Avro schema 解码 DTS 消息。
2. 固定 DTS partition 0，关闭自动提交；新消费状态必须给出带时区的起始时间并按 DTS 要求转换为 epoch 秒，已有数据库 checkpoint 优先从 checkpoint 继续。
3. SASL 使用 `PLAIN` + `SASL_PLAINTEXT`，实际用户名按 `<账号>-<消费组ID>` 生成；密码只从运行时环境读取，不进入日志或仓库。
4. 按 `source_region + topic + partition + offset` 定义幂等键，并把已确认的 17 类国内共享/区域业务表事件路由为课程、教师、师生组合、标签或投诉分类脏键。
5. 已实现海外/国内教师筛选差异、Peak 时段差异、投诉两表各取最新一条、处罚时间差大于 30 秒的迟到/早退规则。
6. 目标固定为 `tide_system_test.public`，数据库身份固定为 `tit_dts_ingest_runtime`，SSL 固定为 `verify-full`；库名、Schema、角色或有效权限不一致时启动失败。
7. 每条消息在一个 PostgreSQL 事务内依次写接入账本、字段白名单当前态、脏键和数据库位点；事务成功后才提交 Kafka offset。数据库位点领先 Kafka 时从数据库续跑，Kafka 位点领先数据库时失败关闭。
8. 脏键投影器按课程、教师、师生组合、评价标签和投诉分类重算；课程必须等待国内共享教师主数据并通过开放式新师 cohort、地区及入职 30 天窗口校验。国内教师事件晚到时，会把当前镜像中该教师的国内/海外预约重新置脏；缺主记录时重试，不把“尚未到达”解释成删除。
9. `lesson_source_wide`、`teacher_source_wide` 采用有差异才更新的 UPSERT；源事实删除或退出范围时删除对应宽表行。宽表写入、派生教师脏键和当前脏键完成在同一事务内，失败则进入退避重试。
10. 课程实现国内/海外 Peak 差异（海外含 `00:00–05:30` 与 `18:00–23:30`）、最新评价、评价标签、投诉最新记录、收藏/拉黑最近课程归因、摄像头/CPU/网络/假早退和处罚时间差规则；教师实现入职 30 天窗口内课程、可靠性、反馈、档期、比例、TESOL 与 `is_self_introduce=NULL`。
11. 稀疏 UPDATE 先合并已持久化当前态、before 与 after，再重算反向依赖；归属键变化时旧键、新键都重新投影。
12. `TIT_DTS_PROJECTION_ENABLED` 默认关闭。国内、海外先只写账本/镜像/脏键并追平到同一激活时刻，之后只在一个项目开启全局投影，避免回放未完成时产生暂态 `0/false` 和双项目配置漂移。
13. `TIT_DTS_PROJECTION_MAX_ATTEMPTS` 默认 `8`（允许 `1–100`）。按 `10/20/40/80/160/300/300` 秒累计提供约 15 分钟跨 Topic 暂态依赖窗口；达到阈值后投影进程以 `DTS_WIDE_PROJECTION_RETRY_EXHAUSTED` 失败关闭且不刷新成功 heartbeat。重启不能清除该状态，只有对应新源事件重新置脏并清零尝试次数后才能恢复。
14. 每个持久化进程绑定唯一 `source_region`；入库事件区域与运行配置不一致时以 `DTS_SOURCE_REGION_MISMATCH` 失败关闭。两条 PostgreSQL 连接分别使用 `tit-dts-ingest-ovs`、`tit-dts-ingest-dom` 标识，健康状态也带安全的订阅摘要。
15. 启动时通过 PostgreSQL Catalog 精确校验教师 55 列、课程 23 列的顺序、类型、长度和可空性，以及四张 DTS 状态表的 57 列、15 个关键约束、4 个必要索引和 4 个 guard Trigger。两个 SourceWide Outbox Trigger 还会校验事件类型、绑定函数、参数、WHEN 和启用状态；任一漂移都在连接 broker 之前失败关闭。
16. 开启投影时，代码同时校验国内/海外两个 partition 0 的数据库 checkpoint 已达到统一激活时刻、投诉分类字典非空且引用完整，并通过全局 PostgreSQL session advisory lock 保证只有一个投影器。
17. 首次投影排空期间，application Profile 必须显式设置 `TIT_SOURCE_WIDE_ENABLED=false`，防止下游在宽表中间态上计分或固化不可逆资格。待脏键清零、两轮稳定且宽表抽样对账后，再恢复为 `true` 并验证 SourceWide 单 leader 与 Outbox 排空。
18. `TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED` 默认且在当前预发布保持 `false`。该门禁不停止积分和当前门槛刷新，只禁止尚未获得的出营/金牌资格首次变为 `true`；既有资格继续保留。非法布尔值失败关闭。业务终态与双流水位门禁完成前不得开启。

`scripts/run_dts_source_consumer.py` 保留为不连接目标库的影子验证；只有显式传入 `--commit-offsets` 才会推进消费组位点。`scripts/run_dts_ingest.py` 是持久化进程，数据库事务成功后始终提交 offset。

## 为什么目标宽表之外还需要接入状态

CDC 事件来自多张表。一个评价、投诉或质检事件只能给出局部事实，不能凭单条消息完整重建 23 列课程宽表；重启、乱序和重复投递也要求持久状态。目标库由 revision `20260812_57_dts_state` 创建四张受限表：

- 接入账本：唯一键为 `source_region + topic + partition + offset`，记录处理状态和安全的事件元数据；
- 当前态镜像：按来源表和业务主键保存订阅期内已见过的最新行，并保存不含敏感值的反向依赖键供 GIN 索引定位；
- 脏键队列：保存需要重算的课程、教师、师生组合和标签键及重试状态。
- 数据库位点：保存每个区域/topic/partition 的下一 offset，是 Kafka ACK 落后时的恢复下限。

这些表不能由消费者运行时账号建表。DDL 只由 Alembic/`tide_sys_admin` 创建；为简化运维，`tit_dts_ingest_runtime` 对四张状态表和两张宽表统一获得表级 CRUD。四张状态表的物理删除、事件账本改写和位点回退仍由数据库 Trigger 拒绝；其他运行角色不能读取状态镜像。

## Gaea 预发布配置

DTS 使用同一镜像，但国内、海外分别建立独立 Gaea 项目，避免两条订阅互相继承密码、共享
进程生命周期，也避免运营/教师进程继承 DTS 和数据库密码。每个项目只配置一组：

- `TIT_PROCESS_PROFILE=dts-ingest`；
- DTS 非敏感连接参数：`TIT_DTS_SOURCE_REGION/BROKER_URL/TOPIC/GROUP_ID/ACCOUNT/START_AT`；
- Gaea 密钥：`TIT_DTS_PASSWORD`，只用于 DTS SASL；
- PostgreSQL 非敏感参数：`TIT_DTS_INGEST_DB_HOST/PORT`；
- Gaea 密钥：`TIT_DTS_INGEST_DB_PASSWORD`，只用于 `tit_dts_ingest_runtime`。

两个密码不是同一个密码，不允许复用。`TIT_DTS_INGEST_DB_NAME=tide_system_test`、
`TIT_DTS_INGEST_DB_SCHEMA=public`、`TIT_DTS_INGEST_DB_USER=tit_dts_ingest_runtime`
即使显式配置也只能等于这三个固定值。

| 配置 | 海外 | 国内 |
|---|---|---|
| `TIT_DTS_SOURCE_REGION` | `ovs` | `dom` |
| `TIT_DTS_BROKER_URL` | `100.103.7.163:18003` | `dts-cn-beijing-vpc.aliyuncs.com:18003` |
| `TIT_DTS_TOPIC` | `ap_southeast_1_vpc_pc_gs5986x4885426aej_dba_tide_source_ovs_version2` | `cn_beijing_vpc_pc_2ze5w28lmdr8f626y_dba_tide_source_dom_version2` |
| `TIT_DTS_GROUP_ID` | `tit-ovs-group` | `tit-dom-group` |
| `TIT_DTS_ACCOUNT` | `titconsumeovs` | `titconsumedom` |
| `TIT_DTS_START_AT` | `2026-08-10T14:16:00+08:00` | `2026-08-12T16:30:00+08:00` |
| `TIT_DTS_COHORT_START` | `2026-08-13` | `2026-08-13` |
| `TIT_DTS_COHORT_END_EXCLUSIVE` | 空（开放式） | 空（开放式） |
| `TIT_DTS_PROJECTION_ENABLED` | 首次追平时 `false`；激活后两项目中仅一个为 `true` | 首次追平时 `false`；激活后两项目中仅一个为 `true` |
| `TIT_DTS_PROJECTION_MAX_ATTEMPTS` | `8` | `8` |
| `TIT_DTS_ACTIVATION_AT` | 开启投影时必填，显式带时区 | 与海外相同 |
| `TIT_DTS_REQUIRED_OVS_TOPIC` | 海外 topic | 海外 topic |
| `TIT_DTS_REQUIRED_DOM_TOPIC` | 国内 topic | 国内 topic |

两个可版本化的非敏感配置入口分别是
`backend/.env.dts-ingest.ovs.production.example` 和
`backend/.env.dts-ingest.dom.production.example`。两份文件都故意不含 `TIT_DTS_PASSWORD` 和
`TIT_DTS_INGEST_DB_PASSWORD` 的值。

## 当前未完成的是实联与上线

23/55 字段投影和国内/海外双运行配置已经进入持久化进程，不再停留在候选字段或影子输出。
`tide_system_test` 已迁移至 public 59 / teacher 0041；本次仍没有连接任一 broker、配置 DTS
Gaea 项目或执行真实业务对账，因此不能表述为“链路已跑通”。两项目的 DTS 密码和数据库密码
仍必须由盖娅密钥环境分别注入。投诉分类是早于新教师长期存在的静态共享字典，不受“新教师
入职前无个体数据”覆盖；开启投影前必须通过 DTS 变更事件或受控小型 Seed 将字典装入当前态，并验证
引用完整性。启动门禁可拒绝不满足这些条件的投影进程，但不能代替真实 DTS 认证、Avro 解码、位点恢复和下游业务对账。

## 影子验证命令

以下变量只应在预发布服务器的密钥环境中注入，不要写入 `.env`、命令历史或工单：

```bash
export TIT_DTS_SOURCE_REGION=ovs
export TIT_DTS_BROKER_URL='<broker>'
export TIT_DTS_TOPIC='<topic>'
export TIT_DTS_GROUP_ID='<group-id>'
export TIT_DTS_ACCOUNT='<account>'
export TIT_DTS_PASSWORD='<secret>'
export TIT_DTS_START_AT='2026-08-10T14:16:00+08:00'
python scripts/run_dts_source_consumer.py --max-messages 100
```

先不带 `--commit-offsets` 验证网络、认证、Avro 解码和表路由；确认输出计数正常后，再在明确接受推进测试消费组位点时加该参数。
