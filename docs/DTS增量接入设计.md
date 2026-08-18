# DTS 增量接入设计

## 当前结论

- 国内与海外同步业务库是一个逻辑数据集的两部分，不把国内共享表当作海外缺失数据。
- 国内和海外使用同一套消费、持久化与投影代码，但各自运行一个独立消费者进程；海外消费者必须部署在新加坡，国内消费者必须部署在中国大陆。两条链路通过海外目标库中的当前态和脏键队列汇合，不能共用消费组、位点、SASL 密码或学生 HMAC 密钥。
- 海外 DTS 开始时间为 `2026-08-10 14:16:00+08:00`；国内 DTS 开始时间为 `2026-08-12 16:30:00+08:00`（北京时间）。它们是新消费状态首次解析 offset 的回放边界，不要求等于服务真正启动时间；已有数据库 checkpoint 时始终从 checkpoint 续跑。
- 业务已确认不做教师/课程全量基线。目标人群是国内 `dom_teacher.status_on_time` 在北京时间 `2026-08-13`（含）以后入职的新教师，结束边界开放；两条订阅起点都早于人群起点。只有国内教师主记录已到达、命中地区与入职 30 天窗口的课程才可物化。
- 课程目标契约固定为 23 列，教师目标契约固定为 55 列；字段逻辑以当前映射表和两份 OBS 脚本为准。

## 已实现的持久化切片

`app.dts_source_consumer`、`app.dts_ingest_store` 与
`app.dts_wide_projector` 已实现以下代码边界：

1. Gaea 正式接入使用已在国内 PRE 成功消费的官方 DTS SDK 1.4.0 主流程：`ConsumerContext(ASSIGN) → DefaultDTSConsumer → KafkaRecordFetcher → UserRecordGenerator → EtlRecordProcessor → RecordListener`；其内置 Kafka Java Client 1.0.0 负责 Kafka 协议。受控 listener 将官方 `DefaultUserRecord.getAvroRecord()` 重新编码为 Avro payload，Python 继续使用 `fastavro` 并拥有国内 HMAC、PostgreSQL 事务、数据库 checkpoint、脏键和宽表投影。`kafka-python` 仅保留为显式回退诊断。
   默认 Java transport 使用有界批协议：官方 listener 输出 `EVENT × N` 与 `BATCH_COMPLETE`，Python 在任何 SQL 前完成整批解码与国内 HMAC，再以一个 PostgreSQL 原子事务持久化连续事件；事务成功后只返回一个 `DURABLE_ACK_BATCH`，Java 完整校验后仅对整批最后一条 ADVANCE 调用 `DefaultUserRecord.commit()`（覆盖前面连续 ADVANCE），REPLAY 永不请求 SDK checkpoint，并返回 `SDK_CHECKPOINTS_ACCEPTED`。批次默认 100 条、硬上限 128 条，并受 Java 侧 8 MiB payload 上限保护；任一解码、隐私或数据库错误都不会产生 ACK。该回包只证明 SDK 接受 checkpoint 请求；SDK 后续异步提交 record offset，公开 API 没有 broker 同步成功回执。数据库 `next_offset + source_timestamp` 始终是恢复权威：SDK ASSIGN 按数据库 source timestamp 恢复，首条 offset 小于数据库 next_offset 时只允许账本已存在的幂等 replay，等于 next_offset 时正常前进，大于 next_offset 时按缺口失败关闭。DB 无 checkpoint 时才按 `TIT_DTS_START_AT` 定位。第 16 条中 TCP、Metadata v5 cap 和两个 `TIT_DTS_KAFKA_STARTUP_*` 参数仅描述 `TIT_DTS_TRANSPORT=kafka_python` 回退模式，不传给正式 Java transport。
2. 固定 DTS partition 0，关闭自动提交；新消费状态必须给出带时区的起始时间并按 DTS 要求转换为 epoch 秒，已有数据库 checkpoint 优先从 checkpoint 继续。
3. SASL 使用 `PLAIN` + `SASL_PLAINTEXT`，实际用户名按 `<账号>-<消费组ID>` 生成；密码只从运行时环境读取，不进入日志或仓库。
4. 按 `source_region + topic + partition + offset` 定义幂等键，并把已确认的 17 类国内共享/区域业务表事件路由为课程、教师、师生组合、标签或投诉分类脏键。
5. 已实现海外/国内教师筛选差异、Peak 时段差异、投诉两表各取最新一条、处罚时间差大于 30 秒的迟到/早退规则。
6. 目标固定为 `tide_system_test.public`，数据库身份固定为 `tit_dts_ingest_runtime`。SSL 默认且正式环境固定为 `verify-full`。2026-08-13 DMS 现场值为服务端 `ssl=off` 且当前会话非 TLS；专线只限制网络路径，不加密 PostgreSQL 流量。固定 `tide-system.rwlb.singapore.rds.aliyuncs.com:5432 / tide_system_test` 的国内、海外 DTS PRE 可复用既有两项例外：`TIT_DTS_INGEST_DB_SSLMODE=disable` 与 `TIT_DTS_ALLOW_INSECURE_DB=true`。两项必须同时配置；端点、库、角色、Schema 漂移或正式环境均在连接前失败关闭。每条新建的 PostgreSQL 物理连接都以 `pg_stat_ssl` 核验当前会话 TLS，并同时核验服务端 SSL 状态；明文例外还会在每次连接池 checkout 时复核，服务端一旦启用 TLS 便立即失败关闭并要求恢复 `verify-full/false`。长期持有的投影锁会话也在每批投影前执行同一核验。
7. 每个有界批次在一个 PostgreSQL 事务内按 offset 顺序写接入账本、字段白名单当前态和脏键，并只在批末推进数据库位点；整批事务成功后才请求 SDK checkpoint。数据库位点领先 Kafka 时从数据库续跑，Kafka 位点领先数据库时失败关闭。
8. 脏键投影器按课程、教师、师生组合、评价标签和投诉分类重算；课程必须等待国内共享教师主数据并通过开放式新师 cohort、地区及入职 30 天窗口校验。国内教师事件晚到时，会把当前镜像中该教师的国内/海外预约重新置脏；缺主记录时重试，不把“尚未到达”解释成删除。
9. `lesson_source_wide`、`teacher_source_wide` 采用有差异才更新的 UPSERT；源事实删除或退出范围时删除对应宽表行。宽表写入、派生教师脏键和当前脏键完成在同一事务内，失败则进入退避重试。
10. 课程实现国内/海外 Peak 差异（海外含 `00:00–05:30` 与 `18:00–23:30`）、最新评价、评价标签、投诉最新记录、收藏/拉黑最近课程归因、摄像头/CPU/网络/假早退和处罚时间差规则；教师实现入职 30 天窗口内课程、可靠性、反馈、档期、比例、TESOL 与 `is_self_introduce=NULL`。
11. 稀疏 UPDATE 先合并已持久化当前态、before 与 after，再重算反向依赖；归属键变化时旧键、新键都重新投影。
12. `TIT_DTS_PROJECTION_ENABLED` 默认关闭。国内、海外先只写账本/镜像/脏键并追平到同一激活时刻，之后只允许海外项目开启全局投影；国内项目固定为 `false`，避免回放未完成时产生暂态 `0/false`、双项目配置漂移或让国内跨境链路持有投影 owner 权限。
13. `TIT_DTS_PROJECTION_MAX_ATTEMPTS` 默认 `8`（允许 `1–100`）。按 `10/20/40/80/160/300/300` 秒累计提供约 15 分钟跨 Topic 暂态依赖窗口；达到阈值后该脏键保留 `RETRY`、错误码和尝试次数，并以 PostgreSQL `infinity` 停放，不再被当前投影循环选择，也不终止其他键和接入进程。heartbeat 的投影计数增加 `quarantined` 以暴露本轮新隔离数；对应真实源事件到达时，接入事务会把该键重新置为 `PENDING`、清零尝试次数并恢复处理。明确带有可信课程日期且早于 cohort 的历史关系事件直接完成为忽略，不进入隔离。投影热路径按单课程一次预取复用源当前态，教师评分/投诉按最多 100 个课程依赖一组批量查询，课程宽表无变化时不再重复置脏教师；每轮只 checkout 一条数据库连接并保持每键独立事务，避免为每个键重复连接池与传输门禁。每轮最多处理 1000 键且受 20 秒预算限制，避免追平批量放大导致 heartbeat 失鲜。heartbeat 同时记录 `source_queries/cache_hits/elapsed_ms/budget_exhausted`，用于判断瓶颈是否仍在投影 SQL。
14. 每个持久化进程绑定唯一 `source_region` 与运行区域：`ovs/sg`、`dom/cn`；订阅区域、运行区域或入库事件区域不一致时失败关闭。国内消息完成 Avro 解码后、构造任何海外 PostgreSQL SQL 参数前，必须删除 `s_id/student_id/stu_id/user_id` 原值，并使用只存在于国内容器的 `TIT_DTS_DOM_STUDENT_HMAC_PASSWORD` 生成 `dom:v1:<HMAC-SHA256>`。变量名中的 `PASSWORD` 用于触发 Gaea 敏感值掩码，不能改回会在配置页明文展示的旧名称。可能由人工录入的 `cancel_reason/reason_desc` 也不得原样出境：只保留精确业务值 `Unfilled Lesson Memo`，其他非空内容降为 `Domestic reason redacted`。海外项目与海外目标库不得持有该密钥或原始国内学生 ID。两条 PostgreSQL 连接分别使用 `tit-dts-ingest-ovs`、`tit-dts-ingest-dom` 标识，健康状态也带安全的订阅摘要。
15. 启动时通过 PostgreSQL Catalog 精确校验教师 55 列、课程 23 列的顺序、类型、长度和可空性，以及四张 DTS 状态表的 57 列、15 个关键约束、4 个必要索引和 4 个 guard Trigger。两个 SourceWide Outbox Trigger 还会校验事件类型、绑定函数、参数、WHEN 和启用状态；任一漂移都在连接 broker 之前失败关闭。
16. `kafka_python` 回退模式下，每次持久化进程启动都先清除上一进程留下的 heartbeat/readiness，再依次完成目标库连接、传输、身份、Schema/ACL 校验，完成 bootstrap DNS 解析后对解析结果执行 TCP 探针，最后执行 Kafka 端到端只读探针。TCP 探针只做三次握手，解析出的多个地址共享 5 秒连接预算；DNS 解析发生在该 socket 连接预算之前，不能把“5 秒”描述为覆盖 DNS 的整轮硬超时。探针不接收账号/密码且不收发应用数据；四层失败输出 `DTS_BROKER_TCP_*` 稳定错误码。TCP 成功后立即输出不含 endpoint/IP 的安全阶段日志；Kafka 侧依次拆为 bootstrap `ApiVersions` 自动协商与 SASL、已认证 bootstrap 连接复核、目标 Topic Metadata、partition 0 校验、advertised broker SASL、FindCoordinator、coordinator SASL、OffsetFetch，并按真实位点路径继续执行按时间、最早与末端 ListOffsets。kafka-python 2.2.20 依赖继续精确锁定，但不再把“支持 2.7 客户端”误写成固定 DTS Broker/API 2.7；`consumer_open` 的协商结果只表示客户端选择的协议兼容版本，不代表服务端精确版本。Metadata 阶段是真实的、与 `kcat -L -t <topic>` 同类语义的单 Topic Metadata API 请求；它保留服务端 `ApiVersions` 自动协商，仅将 Metadata API（key 3）客户端上限收敛为 v5，以复刻已成功消费的官方 Java 1.0 诊断客户端该阶段的协议边界，其他 Kafka API 仍按各自协商结果选择。`kafka_client_config` 记录该 cap 与策略，`topic_metadata` 记录服务端声明的 Metadata 版本范围及实际选用版本。回退探针继续复用锁定的 kafka-python 客户端，不安装第二套客户端、不生成带密码配置文件。每个阶段输出 `begin/ok/fail`、耗时、固定请求类型、安全连接状态与白名单错误分类；不输出 endpoint/IP、node ID、topic、group、账号、密码、异常正文、请求对象或堆栈。kafka-python 原生日志被进程强制隔离，因为其调试报文可能包含 PLAIN 认证字节。后续 Kafka 请求超时输出 `DTS_BROKER_KAFKA_REQUEST_TIMEOUT`，由此区分 Pod 网络与 SASL/metadata/消费组/位点层。Kafka 探针使用与正式消费相同的 SASL 配置，验证 topic、partition 0 以及真实初始位点：没有数据库 checkpoint 时按 `TIT_DTS_START_AT` 解析 offset；已有 checkpoint 时验证它没有落后于 Kafka 最早可用位点、没有超过当前末端，并继续执行 Kafka 位点领先数据库的保护。消费者手工绑定 partition 0，不执行 `JoinGroup`；“DTS 侧未见加入消费组”不能替代 SASL、FindCoordinator 或 OffsetFetch 的阶段证据。整轮 Kafka 位点探针默认从自动协商前开始按同一个 15 秒 deadline 收紧剩余请求超时；仅启动门禁可分别通过 `TIT_DTS_KAFKA_STARTUP_REQUEST_TIMEOUT_MS` 与 `TIT_DTS_KAFKA_STARTUP_API_VERSION_AUTO_TIMEOUT_MS` 在 `1–120000ms` 内调整。共享 deadline 取两者较大值；每个请求取自身配置上限与当时剩余整轮预算的较小值，较晚阶段可能短于配置值。`kafka_client_config` 只报告三个明确的 `configured_*` 上限；每条 phase 动态报告 `remaining_probe_budget_ms` 和两个 `effective_*` 值，超时失败时可安全收敛为 `0`，不会由日志计算覆盖原始错误。该 deadline 是 kafka-python 阻塞 SASL/DNS 调用协作遵守的预算，不是可强制终止进程的绝对 wall-clock 上限；回退消费、位点续跑、commit 与 heartbeat 仍固定为 15 秒，不读取这两个诊断覆盖。关闭连接另有 1 秒上限。任一步失败都不写 `ready` 或成功 heartbeat。`--watch` 容器仅对白名单内的暂态网络、Kafka/数据库连接以及投影激活依赖未就绪进行同进程有界退避，每轮关闭失败资源并用新连接重跑完整门禁；重试期间保持 NotReady，认证/授权、配置、Schema/ACL、隐私/HMAC 和 offset 不变量错误仍非零退出。非 `--watch` 命令保持单次执行。该整套门禁在容器进程启动及每次暂态重试时执行，不在镜像构建或周期 healthcheck 中重复执行。
注：第 16 条的“三个 `configured_*` 上限”仅指请求超时、ApiVersions 超时与整轮预算；Metadata 版本上限作为第四个独立配置读回字段。v5 cap 只对齐当前已定位的 Metadata 兼容路径，不预先声明整条 Kafka 协议链已兼容；完整结论以发布后后续阶段日志为准。
17. 回退 TCP/Kafka 探针只读取 metadata/offset，不写接入账本/镜像/脏键/宽表，也不提交消费组 offset；它的启动成功只证明具备开始消费的条件。正式 Java 模式则以官方 SDK 的首条 `UserRecord` 到达 listener 作为 transport 启动证据，但仍必须在 Python 整批 durable ACK 后才能请求 SDK checkpoint。成功 heartbeat 的 ingest 摘要同时记录 `batch_bytes`、`db_elapsed_ms`、`sdk_ack_elapsed_ms`、`batch_elapsed_ms` 与 `durable_next_offset`，用于区分 SDK 拉取、跨进程、数据库与 SDK checkpoint 阶段吞吐；全链路另看事件账本、数据库 checkpoint 和消费组位点，`SDK_CHECKPOINTS_ACCEPTED` 不能替代 Kafka 服务端位点读回。明确可重试的 Java 断线/超时会清除健康证据、关闭当轮 DB/Java 资源并在同一 PID 内按数据库 checkpoint 重跑完整启动门禁，永久错误仍失败关闭。
18. 海外项目开启投影时，代码同时校验国内/海外两个 partition 0 的数据库 checkpoint 已达到统一激活时刻、投诉分类字典非空且引用完整，并通过全局 PostgreSQL session advisory lock 保证只有一个投影器；国内项目请求开启投影直接失败关闭。
19. 首次投影排空期间，application Profile 必须显式设置 `TIT_SOURCE_WIDE_ENABLED=false`，防止下游在宽表中间态上计分或固化不可逆资格。待脏键清零、两轮稳定且宽表抽样对账后，再恢复为 `true` 并验证 SourceWide 单 leader 与 Outbox 排空。
20. `TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED` 默认且在当前预发布保持 `false`。该门禁不停止积分和当前门槛刷新，只禁止尚未获得的出营/金牌资格首次变为 `true`；既有资格继续保留。非法布尔值失败关闭。业务终态与双流水位门禁完成前不得开启。

`scripts/run_dts_source_consumer.py` 保留为不连接目标库的影子验证；它会真实读取并解码消息，只有显式传入 `--commit-offsets` 才会推进消费组位点。`scripts/run_dts_ingest.py` 的启动探针不读取消息、不提交 offset；进入正式消费循环后，只有对应批次的数据库事务成功才请求该批 SDK checkpoint。

## 为什么目标宽表之外还需要接入状态

CDC 事件来自多张表。一个评价、投诉或质检事件只能给出局部事实，不能凭单条消息完整重建 23 列课程宽表；重启、乱序和重复投递也要求持久状态。目标库由 revision `20260812_57_dts_state` 创建四张受限表：

- 接入账本：唯一键为 `source_region + topic + partition + offset`，记录处理状态和安全的事件元数据；
- 当前态镜像：按来源表和业务主键保存订阅期内已见过的最新行，并保存不含敏感值的反向依赖键供 GIN 索引定位；
- 脏键队列：保存需要重算的课程、教师、师生组合和标签键及重试状态。
- 数据库位点：保存每个区域/topic/partition 的下一 offset，是 Kafka ACK 落后时的恢复下限。

国内事件写入上述状态表、脏键或 `lesson_source_wide` 时只能出现 `dom:v1:` token，不能出现原始
学生 ID 或旧版 `student_ids` 依赖键；token 前缀同时提供来源识别，不能被解释为原始业务 ID。
稳定 token 可支持不同学员数、收藏和拉黑归因，但仍属于伪名数据而非匿名数据。若安全评审禁止
稳定个体 token 跨境，必须另建国内状态库和国内聚合服务，海外只接收按教师/课程聚合且无法回链
到个体的结果；不能把密钥搬到海外或改用可逆加密绕过该边界。

这些表不能由消费者运行时账号建表。DDL 只由 Alembic/`tide_sys_admin` 创建；为简化运维，`tit_dts_ingest_runtime` 对四张状态表和两张宽表统一获得表级 CRUD。四张状态表的物理删除、事件账本改写和位点回退仍由数据库 Trigger 拒绝；其他运行角色不能读取状态镜像。

## Gaea 预发布配置

DTS 在 `gaea.yml` 中使用同一个 `dts-ingest` 轻量构建模块，但国内、海外仍分别建立独立 Gaea
项目。海外项目必须选择新加坡数据中心，国内项目必须选择中国大陆数据中心；模块或环境变量不会
替平台完成地理放置。Gaea 会为两个项目分别构建和推送内容相同的镜像；模块选择不提供跨项目 digest 复用。
该结构避免两条订阅互相继承密码、共享进程生命周期，也避免运营/教师进程继承 DTS 和数据库
密码。两个项目的构建类型都必须是 `multi_module`、构建模块都必须是 `dts-ingest`，每个项目
只配置一组：

- `TIT_PROCESS_PROFILE=dts-ingest`；
- `TIT_DTS_TRANSPORT=official_java`（Gaea 镜像固定；`kafka_python` 仅用于显式回退诊断）；
- DTS 非敏感连接参数：`TIT_DTS_SOURCE_REGION/EXECUTION_REGION/BROKER_URL/TOPIC/GROUP_ID/ACCOUNT/START_AT`；
- Gaea 密钥：`TIT_DTS_PASSWORD`，只用于 DTS SASL；
- 国内项目额外 Gaea 密钥：`TIT_DTS_DOM_STUDENT_HMAC_PASSWORD`，由 CSPRNG 生成 32 bytes 并精确编码为 64 位小写 hex，以敏感变量掩码注入，只在国内消息仍位于国内容器时生成稳定 token；海外项目禁止配置；
- 国内 HMAC 密钥首次启动时只把单向 fingerprint 登记到受限 DTS 状态表；之后 fingerprint 不一致即失败关闭。禁止直接替换密钥，轮换必须新增 token 版本并迁移全部存量关联后另行发布；
- PostgreSQL 非敏感参数：`TIT_DTS_INGEST_DB_HOST/PORT/SSLMODE`；固定专线 PRE 的 `backend/dts-ingest.pre-ssl-off.env.example` 可由国内、海外项目复用两项 `disable/true` 覆盖，正式环境均保持 `verify-full/false`；
- Gaea 密钥：`TIT_DTS_INGEST_DB_PASSWORD`，只用于 `tit_dts_ingest_runtime`。

两个密码不是同一个密码，不允许复用。`TIT_DTS_INGEST_DB_NAME=tide_system_test`、
`TIT_DTS_INGEST_DB_SCHEMA=public`、`TIT_DTS_INGEST_DB_USER=tit_dts_ingest_runtime`
即使显式配置也只能等于这三个固定值。

| 配置 | 海外 | 国内 |
|---|---|---|
| `TIT_DTS_SOURCE_REGION` | `ovs` | `dom` |
| Gaea 数据中心 | 新加坡 | 中国大陆 |
| `TIT_DTS_EXECUTION_REGION` | `sg` | `cn` |
| `TIT_DTS_BROKER_URL` | `100.103.7.163:18003` | `dts-cn-beijing-vpc.aliyuncs.com:18003` |
| `TIT_DTS_TOPIC` | `ap_southeast_1_vpc_pc_gs5986x4885426aej_dba_tide_source_ovs_version2` | `cn_beijing_vpc_pc_2ze5w28lmdr8f626y_dba_tide_source_dom_version2` |
| `TIT_DTS_GROUP_ID` | 海外订阅“数据消费”页生成的消费组 ID（sid） | 国内订阅“数据消费”页生成的消费组 ID（sid） |
| `TIT_DTS_ACCOUNT` | `titconsumeovs` | `titconsumedom` |
| `TIT_DTS_START_AT` | `2026-08-10T14:16:00+08:00` | `2026-08-12T16:30:00+08:00` |
| `TIT_DTS_DOM_STUDENT_HMAC_PASSWORD` | 禁止配置 | CSPRNG 生成的 32-byte 密钥，精确编码为 64 位小写 hex，并由 Gaea 掩码保存 |
| `TIT_DTS_COHORT_START` | `2026-08-13` | `2026-08-13` |
| `TIT_DTS_COHORT_END_EXCLUSIVE` | 空（开放式） | 空（开放式） |
| `TIT_DTS_PROJECTION_ENABLED` | 首次追平时 `false`；激活后由本项目改为 `true` | 固定 `false`，禁止成为投影 owner |
| `TIT_DTS_PROJECTION_MAX_ATTEMPTS` | `8` | `8` |
| `TIT_DTS_ACTIVATION_AT` | 开启投影时必填，显式带时区 | 与海外相同 |
| `TIT_DTS_REQUIRED_OVS_TOPIC` | 海外 topic | 海外 topic |
| `TIT_DTS_REQUIRED_DOM_TOPIC` | 国内 topic | 国内 topic |

两个可版本化的生产安全配置入口分别是
`backend/.env.dts-ingest.ovs.production.example` 和
`backend/.env.dts-ingest.dom.production.example`；国内、海外固定专线 PRE 的临时非 TLS 覆盖位于
`backend/dts-ingest.pre-ssl-off.env.example`，两项必须一起加载。上述文件都故意不含
`TIT_DTS_PASSWORD`、`TIT_DTS_INGEST_DB_PASSWORD` 和国内 HMAC 密钥的值。正式环境若目标不再是当前固定 test 库，还必须同步修改
数据库身份契约、迁移和 ACL 并重新验收，不能只把 SSL 改回 `verify-full`。

## 当前未完成的是实联与上线

23/55 字段投影和国内/海外双运行配置已经进入持久化进程，不再停留在候选字段或影子输出。
截至 2026-08-14，`tide_system_test` 已实证为 public `20260814_61_teacher_copy`、teacher 精确
36 条且 head `0041_crm_sso_hybrid`，并已通过当前 release 的完整只读联合契约探针。rev61
只更新 4 条稳定任务模板文案，不改变 DTS 状态表、Trigger 或隐私函数；数据库发布门禁已经
关闭，但两个 DTS 仍须以 `projection=false` 完成真实 Kafka 连通性、readiness/heartbeat、
双流 checkpoint 和事件账本验收，不能把数据库契约通过写成全链路完成。
国内订阅已明确使用“AI 效率中心”团队的独立 Gaea 项目
`tida-camp-dts-dom` 并选择中国大陆集群；仍须从新 Pod 读回平台地域和运行配置。国内跨境写入还要求目标 PostgreSQL
默认和正式环境提供可由 `verify-full` 验证的 TLS；当前 PRE 仅在固定专线范围允许明文试跑，
不能称为 TLS 或生产传输安全。海外项目仍需
完成所在 Pod 到海外 DTS endpoint 的 Kafka 启动门禁。当前尚无合规国内 Pod 的成功
readiness/heartbeat、双流 checkpoint、目标写入或真实字段对账。
投诉分类是早于新教师长期存在的静态共享字典，不受“新教师
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

`<group-id>` 必须复制 DTS“数据消费”页的系统生成消费组 ID（sid），不是可编辑的消费组名称；
代码会按 `<账号>-<消费组 ID>` 生成 SASL 用户名，并在连接前拒绝仓库曾误发的名称占位值。

先不带 `--commit-offsets` 验证网络、认证、Avro 解码和表路由；确认输出计数正常后，再在明确接受推进测试消费组位点时加该参数。
